"""Native Bend 2 object-heap mark/sweep collector for Python3-to-Bend.

Based on MicroPython's non-moving mark/sweep design, *not* a port of its C
memory manager. This collects only at explicit, compiler-established safepoints
inside main(); no caller-frame liveness is inferred, and this is not a Bend
runtime certification. Do not perform collection inside arbitrary expressions.
"""
from __future__ import annotations

import argparse
import ast
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Sequence

from tools.python3_to_bend2.syntax import TranslationError, bend_command
from tools.python3_to_bend2.modules import ModuleLowerer, StaticModuleLinker

# A representation change made only in v7: class fields use the same KV list
# as dictionaries. This lets GC enumerate every possible object reference
# without undocumented Bend Map traversal primitives.
RECORD_REWRITE = (
    ('ORecord{tag: String, fields: Map<&2, PyValue>}',
     'ORecord{tag: String, fields: List<&2, PyKV>}'),
    ('ORecord{name, Map.new(&2, PyValue)}', 'ORecord{name, Nil{}}'),
    ('Pair.snd(Map<&2, PyValue>, PyValue, Map.get(PyValue, VError{"AttributeError"}, fields, field))',
     'PyDict.get(fields, field, VError{"AttributeError"})'),
    ('ORecord{tag, Map.set(&2, PyValue, fields, field, value)}',
     'ORecord{tag, PyDict.set(fields, field, value)}'),
)

# All references live inside the existing PyValue/PyObject/PyCell types.
# The visited set is the mark bitmap analogue; a list avoids relying on
# Map iteration or external pointer manipulation. No compaction: Nat IDs are
# monotonically assigned and never recycled in this version.
GC_BEND = '''\
def PyGC.contains(seen: List<&2, Nat>, address: Nat) -> Bool:
  match seen:
    case Nil{}:
      False{}
    case Con{head, tail}:
      Bool.or(Nat.is_eq(head, address), PyGC.contains(tail, address))

@unsafe def PyGC.mark_value(+heap: PyHeap, value: PyValue, seen: List<&2, Nat>) -> List<&2, Nat>:
  match value:
    case VRef{address}:
      PyGC.mark_address(heap, address, seen)
    case _:
      seen

@unsafe def PyGC.mark_address(+heap: PyHeap, address: Nat, seen: List<&2, Nat>) -> List<&2, Nat>:
  match PyGC.contains(seen, address):
    case True{}:
      seen
    case False{}:
      PyGC.mark_object(heap, PyHeap.read(heap, address), address <> seen)

@unsafe def PyGC.mark_object(+heap: PyHeap, object: PyObject, seen: List<&2, Nat>) -> List<&2, Nat>:
  match object:
    case OMissing{}:
      seen
    case OList{items}:
      PyGC.mark_values(heap, items, seen)
    case ODict{items}:
      PyGC.mark_fields(heap, items, seen)
    case ORecord{tag, fields}:
      PyGC.mark_fields(heap, fields, seen)

@unsafe def PyGC.mark_values(+heap: PyHeap, values: List<&2, PyValue>, seen: List<&2, Nat>) -> List<&2, Nat>:
  match values:
    case Nil{}:
      seen
    case Con{value, rest}:
      PyGC.mark_values(heap, rest, PyGC.mark_value(heap, value, seen))

@unsafe def PyGC.mark_fields(+heap: PyHeap, fields: List<&2, PyKV>, seen: List<&2, Nat>) -> List<&2, Nat>:
  match fields:
    case Nil{}:
      seen
    case Con{KV{key, value}, rest}:
      PyGC.mark_fields(heap, rest, PyGC.mark_value(heap, value, seen))

def PyGC.sweep(cells: List<&2, PyCell>, seen: List<&2, Nat>) -> List<&2, PyCell>:
  match cells:
    case Nil{}:
      Nil{}
    case Con{Cell{+address, object}, rest}:
      PyGC.sweep_step(PyGC.contains(seen, address), Cell{address, object}, rest, seen)

def PyGC.sweep_step(live: Bool, cell: PyCell, rest: List<&2, PyCell>, seen: List<&2, Nat>) -> List<&2, PyCell>:
  match live:
    case True{}:
      cell <> PyGC.sweep(rest, seen)
    case False{}:
      PyGC.sweep(rest, seen)

def PyHeap.collect(heap: PyHeap, roots: List<&2, PyValue>) -> PyHeap:
  match heap:
    case Heap{next, +cells, stdout}:
      +marks: List<&2, Nat> = PyGC.mark_values(Heap{next, cells, stdout}, roots, Nil{})
      Heap{next, PyGC.sweep(cells, marks), stdout}
'''


def _is_gc_collect(node: ast.AST) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == 'gc' and node.func.attr == 'collect')


class TracingCollectorLowerer(ModuleLowerer):
    """Add explicit cycle collection at safe statement boundaries in main()."""

    def __init__(self, source: str, source_name: str = '<input>', *, show_return: bool = False):
        # Verify import syntax before stripping the no-op gc module import.
        tree = ast.parse(source, filename=source_name)
        imports = [n for n in tree.body if isinstance(n, ast.Import)
                   for a in n.names if a.name == 'gc']
        if any(len(n.names) != 1 or n.names[0].asname for n in imports):
            raise TranslationError('v7 supports only plain import gc', source=source_name)
        if any(isinstance(n, ast.ImportFrom) and n.module == 'gc' for n in tree.body):
            raise TranslationError('from gc import is not supported; use import gc', source=source_name)
        self._has_gc_import = bool(imports)
        if any(_is_gc_collect(n) for n in ast.walk(tree)) and not self._has_gc_import:
            raise TranslationError('gc.collect() requires import gc', source=source_name)
        if any(_is_gc_collect(n) for n in ast.walk(tree)):
            # A call to main() from another translated function could enter
            # main recursively with an active caller frame that is not in the
            # current root set. Fail closed until cross-frame roots exist.
            if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'main'
                   for n in ast.walk(tree)):
                raise TranslationError('reentrant main() is unsafe with collection; refused', source=source_name)
        for fn in [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))]:
            calls = [n for n in ast.walk(fn) if _is_gc_collect(n)]
            if not calls:
                continue
            if not isinstance(fn, ast.FunctionDef) or fn.name != 'main':
                raise TranslationError('gc.collect() outside main() needs caller roots; refused', source=source_name)
            permitted = {id(n.value) for n in ast.walk(fn)
                         if isinstance(n, ast.Expr) and _is_gc_collect(n.value)}
            if any(id(call) not in permitted or call.args or call.keywords for call in calls):
                raise TranslationError('gc.collect() must be a zero-argument standalone statement', source=source_name)
            if any(isinstance(n, ast.Name) and n.id == 'gc' and isinstance(n.ctx, (ast.Store, ast.Del))
                   for n in ast.walk(fn)):
                raise TranslationError('gc namespace may not be rebound', source=source_name)
        # Other gc.* APIs and accesses are intentionally unsupported.
        for n in ast.walk(tree):
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == 'gc' and n.attr != 'collect':
                raise TranslationError('unsupported gc API ' + n.attr, source=source_name)
        tree.body = [node for node in tree.body if node not in imports]
        super().__init__(ast.unparse(tree), source_name, show_return=show_return)

    def expr(self, node, env, heap, lines):
        if _is_gc_collect(node):
            # All live locals of main and compiler-generated control-flow
            # helpers are passed explicitly; nothing from active callee frames
            # is omitted because gc.collect() in those functions is rejected.
            roots = '[' + ', '.join(v.bend for v in env.values()) + ']'
            if not env:
                roots = 'Nil{}'
            heap = self.modify(f'PyHeap.collect({heap}, {roots})', lines, 'collected_heap')
            return heap, self.bind('VNone{}', lines), 'None'
        return super().expr(node, env, heap, lines)

    def compile(self) -> str:
        code = super().compile()
        for before, after in RECORD_REWRITE:
            if code.count(before) != 1:
                raise AssertionError('v7 expected exactly one record-runtime occurrence: ' + before)
            code = code.replace(before, after, 1)
        marker = 'def main() -> IO(Unit):\n'
        if code.count(marker) != 1:
            raise AssertionError('v7 missing unique main()')
        return code.replace(marker, GC_BEND + '\n' + marker, 1)


def translate_tracing_file(path: Path, *, show_return: bool = False) -> str:
    """Accept v6's statically linked local modules, plus import gc in entry."""
    # The static linker intentionally erases the compiler-builtin import.
    # Prove it really existed in the source, rather than fabricating it when
    # an undefined name happens to look like a call to gc.collect().
    root_tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    root_has_gc = any(isinstance(stmt, ast.Import) and len(stmt.names) == 1
                      and stmt.names[0].name == 'gc' and stmt.names[0].asname is None
                      for stmt in root_tree.body)
    linker = StaticModuleLinker(path)
    flattened = linker.link()
    if any(_is_gc_collect(n) for n in ast.walk(ast.parse(flattened))):
        if not root_has_gc:
            raise TranslationError('gc.collect() requires explicit import gc in entry file', source=str(path))
        flattened = 'import gc\n' + flattened
    return TracingCollectorLowerer(flattened, str(path), show_return=show_return).compile()


GC_CASES = {
    'unreachable_cycle': """import gc

def main() -> int:
    forgotten = []
    forgotten.append(forgotten)
    forgotten = [8]
    gc.collect()
    return forgotten[0]
""",
    'alias_live': """import gc

def main() -> int:
    first = [11]
    shared = first
    first = [99]
    gc.collect()
    return shared[0]
""",
    'reachable_cycle': """import gc

def main() -> int:
    xs = []
    xs.append(xs)
    gc.collect()
    return len(xs)
""",
    'dict_to_list': """import gc

def main() -> int:
    xs = [5]
    table: dict[str,list[int]] = {"k": xs}
    xs = [100]
    gc.collect()
    return len(table)
""",
    'record_to_list': """import gc

class Box:
    child: list[int]
    def __init__(self, child: list[int]) -> None:
        self.child = child

def main() -> int:
    xs = [2]
    box = Box(xs)
    xs = [4]
    gc.collect()
    return len(box.child)
""",
    'loop_collections': """import gc

def main() -> int:
    xs = [1]
    i = 0
    discarded = []
    while i < 3:
        discarded = [i]
        gc.collect()
        i += 1
    return xs[0] + i
""",
}


def run_tracing_smoke(*, bend: str = 'bend', wsl: bool = False, source_only: bool = False) -> int:
    """Compile and differentially execute actual GC cases, or fail closed."""
    from tools.python3_to_bend2.integers import run_bend_gate
    if not source_only and not wsl and shutil.which(bend) is None:
        print('PY3_BEND_GC_V7 BLOCKED compiler missing native_cases=0')
        return 2
    failures = 0
    executed = 0
    with tempfile.TemporaryDirectory(prefix='py3-bend-gc-v7-') as d:
        for label, source in GC_CASES.items():
            try:
                source_path = Path(d) / (label + '.py')
                source_path.write_text(source, encoding='utf-8')
                generated = TracingCollectorLowerer(source, label, show_return=True).compile()
                bend_path = Path(d) / (label + '.bend')
                bend_path.write_text(generated, encoding='utf-8')
                if source_only:
                    print(f'PY3_BEND_GC_V7 {label} SOURCE_ONLY native_executed=false')
                    continue
                checked, verdict = run_bend_gate(bend_path, bend, wsl=wsl)
                if not checked:
                    raise RuntimeError('BEND_COMPILE_FAILED ' + verdict)
                expected = subprocess.run(
                    [sys.executable, '-B', '-c',
                     f'import runpy; ns = runpy.run_path({str(source_path)!r}); print(ns["main"]())'],
                    capture_output=True, text=True, timeout=30)
                if expected.returncode:
                    raise RuntimeError('PYTHON_ORACLE_FAILED ' + expected.stderr)
                actual = subprocess.run(bend_command(bend_path, bend=bend, wsl=wsl),
                                        capture_output=True, text=True, timeout=90)
                if actual.returncode or actual.stdout != expected.stdout:
                    raise RuntimeError(f'DIFFERENTIAL_FAILED rc={actual.returncode} '
                                       f'expected={expected.stdout!r} got={actual.stdout!r} '
                                       f'stderr={actual.stderr[-1000:]!r}')
                executed += 1
                print(f'PY3_BEND_GC_V7 {label} COMPILER_AND_DIFFERENTIAL_PASS')
            except (TranslationError, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
                failures += 1
                print(f'PY3_BEND_GC_V7 {label} FAIL {exc}')
    print(f'PY3_BEND_GC_V7 SUMMARY source_cases={len(GC_CASES)} '
          f'native_executed={executed} failures={failures} '
          f'certified={not source_only and executed == len(GC_CASES) and failures == 0}')
    return 0 if failures == 0 and (source_only or executed == len(GC_CASES)) else 2


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='cmd', required=True)
    t = sub.add_parser('translate')
    t.add_argument('input', type=Path)
    t.add_argument('-o', '--output', type=Path)
    t.add_argument('--show-return', action='store_true')
    t.add_argument('--require-bend', action='store_true')
    t.add_argument('--bend', default='bend')
    t.add_argument('--wsl', action='store_true')
    s = sub.add_parser('smoke')
    s.add_argument('--bend', default='bend')
    s.add_argument('--wsl', action='store_true')
    s.add_argument('--source-only', action='store_true')
    a = p.parse_args(argv)
    from tools.python3_to_bend2.integers import run_bend_gate
    if a.cmd == 'translate':
        try:
            target = a.output or a.input.with_suffix('.bend')
            code = translate_tracing_file(a.input, show_return=a.show_return)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(code, encoding='utf-8')
            print(f'PY3_BEND_GC_V7 EMITTED {target} compiler_verified=false')
            if a.require_bend:
                verified, verdict = run_bend_gate(target, a.bend, wsl=a.wsl)
                print('PY3_BEND_GC_V7 ' + verdict)
                return 0 if verified else 2
            return 0
        except (TranslationError, OSError) as exc:
            print('PY3_BEND_GC_V7 FAILED ' + str(exc), file=sys.stderr)
            return 2
    return run_tracing_smoke(bend=a.bend, wsl=a.wsl, source_only=a.source_only)
