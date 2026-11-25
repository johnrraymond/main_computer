"""Declaration ordering for Bend 2 text emitted by experimental Python frontend.

Bend does not grant forward definitions the same behavior as Python. Build a
source graph of local types, constructors and definitions and topologically
order declarations, while preserving type-before-value dependencies. This
checks only source structure, not compiler validity or affine type checking.
"""
from __future__ import annotations
import re

_HEADER = re.compile(r'(?m)^(?:(@unsafe )?def ([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\(|type ([A-Za-z_]\w*) is Data:)')
_CONSTRUCTOR = re.compile(r'(?m)^  ([A-Za-z_]\w*)\{')


def reorder_bend_declarations(source: str) -> str:
    matches = list(_HEADER.finditer(source))
    if not matches:
        raise ValueError('no Bend declarations found')
    prefix = source[:matches[0].start()]
    decls = {}
    types = {}
    initial_unsafe = set()
    for i, match in enumerate(matches):
        name = match.group(2) or match.group(3)
        if name in decls:
            raise ValueError(f'duplicate declaration {name}')
        end = matches[i+1].start() if i+1<len(matches) else len(source)
        block = source[match.start():end].strip()
        decls[name] = block
        if match.group(3):
            for ctor in _CONSTRUCTOR.findall(block):
                if ctor in types:
                    raise ValueError(f'duplicate constructor {ctor}')
                types[ctor] = name
        if match.group(1):
            initial_unsafe.add(name)
    word_names = sorted(decls, key=len, reverse=True)
    # Scan only tokens in each declaration; no guessed builtins. Constructor
    # tags create additional dependencies on the owning local datatype.
    deps = {}
    for name, block in decls.items():
        tokens = set(re.findall(r'(?<![A-Za-z_0-9.])([A-Za-z_][A-Za-z_0-9.]*)', block))
        edge = {n for n in word_names if n in tokens and n != name}
        edge.update(types[token] for token in tokens if token in types and types[token] != name)
        deps[name] = edge
    ordered = []
    visiting = set()
    visited = set()
    in_cycle = set()
    trail = []
    def visit(name):
        if name in visited:
            return
        if name in visiting:
            if name in trail:
                in_cycle.update(trail[trail.index(name):])
            return
        visiting.add(name)
        trail.append(name)
        for neighbor in sorted(deps[name]):
            visit(neighbor)
        trail.pop()
        visiting.remove(name)
        visited.add(name)
        ordered.append(name)
    for name in decls:
        visit(name)
    # Cyclic datatypes cannot simply be marked unsafe; force an explicit
    # diagnostic instead of generating order-dependent nonsense.
    for name in in_cycle:
        if decls[name].startswith('type '):
            raise ValueError('recursive native datatype dependency: ' + name)
    for name in ordered:
        if name in in_cycle | initial_unsafe and not decls[name].startswith('@unsafe def '):
            decls[name] = '@unsafe ' + decls[name]
    return prefix.rstrip() + '\n\n' + '\n\n'.join(decls[name] for name in ordered) + '\n'
