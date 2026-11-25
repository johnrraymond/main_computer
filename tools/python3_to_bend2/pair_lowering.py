"""Lower heap/value pair bindings to explicit Bend continuation functions.

Bend 2.0 does not allow matching on an application or let-bound value.
Destructuring a computed pair is also a match. The heap frontend emits
straight-line, typed SSA bindings and Boolean case branches; lift the suffix
of each such binding to a top-level function with a pair parameter, passing
only the free, already-bound values. The computed pair is evaluated once.
This pass operates on that internal emitter format, not arbitrary Bend.
"""
from __future__ import annotations

import re

from .declaration_order import reorder_bend_declarations

_HEADER = re.compile(r'^(?:@unsafe )?def (py[35]_\w+|py_pair_cont_\d+)\((.*)\) -> (PyHeap & PyValue):$')
_PARAM = re.compile(r'([+]?)([A-Za-z_]\w*):\s*(PyHeap & PyValue|PyHeap|PyValue|Bool)\Z')
_BIND = re.compile(r'([+]?)([A-Za-z_]\w*)\s*:\s*(PyHeap|PyValue|Bool)\s*=')
_PAIR = re.compile(r'^( +)\((py5_from_pair_heap_\d+), (py5_from_pair_\d+)\) = (.+)$')
_STRING = re.compile(r'"(?:\\.|[^"\\])*"')
_IDENT = re.compile(r'(?<![\w.])[A-Za-z_]\w*(?![\w.])')
_DECL = re.compile(r'(?m)(?=^(?:@unsafe )?def |^type )')


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(' '))


def _capture_scope(header: re.Match[str], lines: list[str], index: int) -> dict[str, tuple[str, str]]:
    """Collect the typed parameters and preceding SSA definitions in scope."""
    scope: dict[str, tuple[str, str, int]] = {}
    for param in header[2].split(', ') if header[2] else []:
        found = _PARAM.fullmatch(param)
        if not found:
            raise ValueError('unsupported continuation parameter: ' + param)
        mark, name, typ = found.groups()
        scope[name] = (mark, typ, 0)
    for line in lines[1:index]:
        if not line.strip():
            continue
        level = _indent(line)
        scope = {name: data for name, data in scope.items() if data[2] <= level}
        bind = _BIND.match(line.strip())
        if bind:
            mark, name, typ = bind.groups()
            scope[name] = (mark, typ, level)
        # A continuation starts by destructuring its pair parameter. Its two
        # fields are affine until the emitter's subsequent +aliases bind them.
        match = re.fullmatch(r'\((py5_from_pair_heap_\d+), (py5_from_pair_\d+)\) = py_cont_pair', line.strip())
        if match:
            scope[match[1]] = ('', 'PyHeap', level)
            scope[match[2]] = ('', 'PyValue', level)
    return {name: (mark, typ) for name, (mark, typ, _) in scope.items()
            if scope[name][2] <= _indent(lines[index])}


def lower_computed_pairs(source: str) -> str:
    """Lift all emitted computed-pair destructuring without duplicating calls."""
    declarations = _DECL.split(source)
    output: list[str] = []
    next_id = 0
    for declaration in declarations:
        pending = [declaration]
        while pending:
            block = pending.pop(0)
            lines = block.rstrip().splitlines()
            if not lines:
                continue
            header = _HEADER.fullmatch(lines[0])
            sites = [(i, m) for i, line in enumerate(lines) if (m := _PAIR.fullmatch(line))]
            sites = [(i, m) for i, m in sites if m[4] != 'py_cont_pair']
            if not sites:
                output.append(block.rstrip())
                continue
            if header is None:
                raise ValueError('computed pair outside generated heap function: ' + lines[0])
            index, match = sites[0]
            level = len(match[1])
            end = index + 1
            while end < len(lines) and (not lines[end].strip() or _indent(lines[end]) >= level):
                end += 1
            suffix = lines[index+1:end]
            # An SSA binding must have a following expression in this branch.
            if not any(line.strip() for line in suffix):
                raise ValueError('computed pair has no continuation')
            scope = _capture_scope(header, lines, index)
            tokens = set(_IDENT.findall(_STRING.sub('""', '\n'.join(suffix))))
            captures = [name for name in scope if name in tokens]
            # Generated field names are unique; captures are just typed SSA
            # locals/parameters. No inference or arbitrary source parsing.
            next_id += 1
            helper = f'py_pair_cont_{next_id}'
            params = ['py_cont_pair: PyHeap & PyValue'] + [
                f'{scope[name][0]}{name}: {scope[name][1]}' for name in captures]
            args = ', '.join([match[4], *captures])
            # A function may have computed pairs in multiple sibling arms;
            # reprocess its remaining branches as well as the lifted suffix.
            rewritten = '\n'.join(lines[:index] + [match[1] + f'{helper}({args})'] + lines[end:])
            lifted = '\n'.join([f'def {helper}({", ".join(params)}) -> {header[3]}:',
                                f'  ({match[2]}, {match[3]}) = py_cont_pair',
                                *[('  ' + line[level:]) if line.strip() else '' for line in suffix]])
            pending.insert(0, rewritten)
            pending.insert(0, lifted)
    return reorder_bend_declarations('\n\n'.join(output) + '\n')
