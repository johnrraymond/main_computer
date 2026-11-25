"""Bend 2 Python-object heap: automatic mark/sweep with precise caller roots.

This is a restricted *native Bend source generator*, not a general Python VM.
The collector runs at compiler-created statement safepoints. Live roots held
by suspended callers are preserved in explicitly pushed frames. A frame is
popped upon normal function return. Cycles can be collected without moving
objects. Generated Bend still REQUIRES the real compiler/runtime gate.

No host CPython calls are emitted. CPython is used only in differential tests.
"""
from __future__ import annotations

import argparse
import ast
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

from tools.python3_to_bend2.syntax import TranslationError, bend_command
from tools.python3_to_bend2.integers import run_bend_gate
from tools.python3_to_bend2.modules import ModuleLowerer, StaticModuleLinker
from tools.python3_to_bend2.tracing_gc import RECORD_REWRITE, GC_BEND

_GC_APIS = {'collect', 'disable', 'enable', 'isenabled'}
_WEAKREF_APIS = {'ref'}
_SAFETY_FN = '__pybend_gc_safepoint__'
_ROOT_PATTERN = re.compile(r'^\+(\w+)\s*:\s*PyValue\s*=')
_CALL_PATTERN = re.compile(r'^(py3_\w+)\((\w+)(,.*)?\)$', flags=re.S)


def _gc_call(node: ast.AST) -> str | None:
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == 'gc'):
        return node.func.attr
    return None


def _safepoint() -> ast.Expr:
    return ast.Expr(value=ast.Call(func=ast.Name(id=_SAFETY_FN, ctx=ast.Load()), args=[], keywords=[]))


class SafePoints:
    """Insert checkpoints at statement boundaries, never mid-expression."""

    @classmethod
    def statements(cls, stmts: list[ast.stmt]) -> list[ast.stmt]:
        out: list[ast.stmt] = []
        for stmt in stmts:
            if isinstance(stmt, (ast.If, ast.While, ast.For)):
                stmt.body = cls.statements(stmt.body)
                stmt.orelse = cls.statements(stmt.orelse)
                if isinstance(stmt, (ast.For, ast.While)):
                    # Also cover loops with only pass/control-flow statements.
                    stmt.body.append(ast.copy_location(_safepoint(), stmt))
            out.append(stmt)
            if isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr)):
                out.append(ast.copy_location(_safepoint(), stmt))
        return out

    @classmethod
    def apply(cls, tree: ast.Module) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                node.body = cls.statements(node.body)
        ast.fix_missing_locations(tree)


def _split_heap_ctor(code: str) -> str:
    """Extend every PyHeap constructor/pattern preserving all existing fields.

    The old runtime is purposely matched by *arity*. An unexpected future
    representation is an error, never a silent destructive rewrite.
    """
    out: list[str] = []
    pos = 0
    count = 0
    while True:
        start = code.find('Heap{', pos)
        if start < 0:
            out.append(code[pos:])
            break
        out.append(code[pos:start])
        cursor = start + len('Heap{')
        openers = {'{': '}', '(': ')', '[': ']', '<': '>'}
        closers = {v: k for k, v in openers.items()}
        stack = ['{']
        current = cursor
        quoted = False
        escaped = False
        while stack:
            if current >= len(code):
                raise AssertionError('unterminated Heap constructor')
            ch = code[current]
            if quoted:
                if escaped:
                    escaped = False
                elif ch == '\\':
                    escaped = True
                elif ch == '"':
                    quoted = False
            elif ch == '"':
                quoted = True
            elif ch in openers:
                stack.append(ch)
            elif ch in closers:
                if not stack or stack[-1] != closers[ch]:
                    raise AssertionError('unbalanced Heap constructor')
                stack.pop()
            current += 1
        old = code[cursor:current-1]
        # Split on top-level commas only.
        parts, last, depths, quoted, escaped = [], 0, [], False, False
        for idx, ch in enumerate(old):
            if quoted:
                if escaped:
                    escaped = False
                elif ch == '\\':
                    escaped = True
                elif ch == '"':
                    quoted = False
            elif ch == '"':
                quoted = True
            elif ch in openers:
                depths.append(ch)
            elif ch in closers:
                depths.pop()
            elif ch == ',' and not depths:
                parts.append(old[last:idx].strip())
                last = idx + 1
        parts.append(old[last:].strip())
        if len(parts) != 3:
            raise AssertionError(f'expected 3-field v7 Heap, got {len(parts)}: {old[:200]!r}')
        a, b, c = parts
        if a.startswith('next: Nat'):
            additions = ['frames: List<&2, PyFrame>', 'since_gc: Nat',
                         'gc_enabled: Bool', 'threshold: Nat']
        elif a == '0n' and b == 'Nil{}' and c == '""':
            additions = ['Nil{}', '0n', 'True{}', '__INITIAL_THRESHOLD__']
        elif a.startswith(('+next', 'next')) and b in ('cells', '+cells') and c == 'stdout':
            additions = ['frames', 'since_gc', 'gc_enabled', 'threshold']
        elif a == '(1n+next)' or a.startswith(('+next', 'next')):
            # Allocation is the sole place that increments the budget;
            # mutations, print, and sweep preserve it.
            delta = '1n+since_gc' if a == '(1n+next)' else 'since_gc'
            additions = ['frames', delta, 'gc_enabled', 'threshold']
        else:
            raise AssertionError('unrecognized Heap shape: ' + old[:100])
        out.append('Heap{' + ', '.join(parts + additions) + '}')
        count += 1
        pos = current
    if count < 9:
        raise AssertionError(f'expected heap constructors, found {count}')
    return ''.join(out)


# Marking and sweeping is retained from v7. Only the root enumeration and
# collection policy are extended; no reference-ID reuse or relocation.
_MARK_SWEEP = GC_BEND[:GC_BEND.index('def PyHeap.collect(')].rstrip()
_MARK_SWEEP = _MARK_SWEEP.replace('def PyGC.contains(seen: List<&2, Nat>, address: Nat)',
                                  'def PyGC.contains(seen: List<&2, Nat>, +address: Nat)')
_MARK_SWEEP = _MARK_SWEEP.replace('def PyGC.mark_address(+heap: PyHeap, address: Nat, seen:',
                                  'def PyGC.mark_address(+heap: PyHeap, +address: Nat, seen:')
_MARK_SWEEP = _MARK_SWEEP.replace('def PyGC.sweep(cells: List<&2, PyCell>, seen:',
                                  'def PyGC.sweep(cells: List<&2, PyCell>, +seen:')

_RUNTIME_V8 = r'''\
type PyFrame is Data:
  Frame{roots: List<&2, PyValue>}

def PyGC.mark_frames(+heap: PyHeap, frames: List<&2, PyFrame>, seen: List<&2, Nat>) -> List<&2, Nat>:
  match frames:
    case Nil{}:
      seen
    case Con{Frame{roots}, rest}:
      PyGC.mark_frames(heap, rest, PyGC.mark_values(heap, roots, seen))

def PyHeap.push_roots(heap: PyHeap, roots: List<&2, PyValue>) -> PyHeap:
  match heap:
    case Heap{next, cells, stdout, frames, since_gc, gc_enabled, threshold}:
      Heap{next, cells, stdout, Frame{roots} <> frames, since_gc, gc_enabled, threshold}

def PyHeap.pop_roots(heap: PyHeap) -> PyHeap:
  match heap:
    case Heap{next, cells, stdout, frames, since_gc, gc_enabled, threshold}:
      PyHeap.pop_roots_frame(next, cells, stdout, frames, since_gc, gc_enabled, threshold)

def PyHeap.pop_roots_frame(next: Nat, cells: List<&2, PyCell>, stdout: String,
                           frames: List<&2, PyFrame>, since_gc: Nat,
                           gc_enabled: Bool, threshold: Nat) -> PyHeap:
  match frames:
    case Nil{}:
      Heap{next, cells, stdout, Nil{}, since_gc, gc_enabled, threshold}
    case Con{Frame{roots}, rest}:
      Heap{next, cells, stdout, rest, since_gc, gc_enabled, threshold}

def PyHeap.collect(heap: PyHeap, roots: List<&2, PyValue>) -> PyHeap:
  match heap:
    case Heap{+next, +cells, +stdout, +frames, since_gc, +gc_enabled, +threshold}:
      +scan: PyHeap = Heap{next, cells, stdout, frames, since_gc, gc_enabled, threshold}
      +marks: List<&2, Nat> = PyGC.mark_values(scan, roots, Nil{})
      +marked: List<&2, Nat> = PyGC.mark_frames(scan, frames, marks)
      Heap{next, PyGC.sweep(cells, marked), stdout, frames, 0n, gc_enabled, threshold}

def PyHeap.maybe_collect(heap: PyHeap, roots: List<&2, PyValue>) -> PyHeap:
  match heap:
    case Heap{next, cells, stdout, frames, +since_gc, +gc_enabled, +threshold}:
      PyHeap.maybe_collect_enabled(gc_enabled, Heap{next, cells, stdout, frames, since_gc, gc_enabled, threshold}, roots, since_gc, threshold)

def PyHeap.maybe_collect_enabled(enabled: Bool, heap: PyHeap,
                                 roots: List<&2, PyValue>, since_gc: Nat,
                                 threshold: Nat) -> PyHeap:
  match enabled:
    case False{}:
      heap
    case True{}:
      PyHeap.maybe_collect_due(Nat.is_lt(since_gc, threshold), heap, roots)

def PyHeap.maybe_collect_due(under_limit: Bool, heap: PyHeap,
                             roots: List<&2, PyValue>) -> PyHeap:
  match under_limit:
    case True{}:
      heap
    case False{}:
      PyHeap.collect(heap, roots)

def PyHeap.set_gc_enabled(heap: PyHeap, enabled: Bool) -> PyHeap:
  match heap:
    case Heap{next, cells, stdout, frames, since_gc, gc_enabled, threshold}:
      Heap{next, cells, stdout, frames, since_gc, enabled, threshold}

def PyHeap.is_gc_enabled(heap: PyHeap) -> Bool:
  match heap:
    case Heap{next, cells, stdout, frames, since_gc, gc_enabled, threshold}:
      gc_enabled
'''.lstrip('\\\n')


_WEAKREF_BEND = r'''
def PyHeap.make_weak(value: PyValue) -> PyValue:
  match value:
    case VRef{address}:
      VWeak{address}
    case _:
      VError{"TypeError: cannot create weak reference"}

def PyHeap.deref_weak(heap: PyHeap, weak: PyValue) -> PyValue:
  match weak:
    case VWeak{address}:
      PyHeap.deref_weak_object(PyHeap.read(heap, address), address)
    case _:
      VError{"TypeError: not a weak reference"}

def PyHeap.deref_weak_object(object: PyObject, address: Nat) -> PyValue:
  match object:
    case OMissing{}:
      VNone{}
    case _:
      VRef{address}
'''


class RuntimeModuleLinker(StaticModuleLinker):
    virtual_builtin_modules = frozenset({'gc', 'weakref'})


class AutomaticCollectorLowerer(ModuleLowerer):
    """Precise nonmoving GC for a static subset, across nested ordinary calls."""

    def __init__(self, source: str, source_name: str = '<input>', *,
                 show_return: bool = False, threshold: int = 8):
        if not 1 <= threshold <= 2**32:
            raise ValueError('automatic collection threshold must be between 1 and 2**32')
        self.threshold = threshold
        tree = ast.parse(source, filename=source_name)
        gc_imports = [node for node in tree.body if isinstance(node, ast.Import)
                      for alias in node.names if alias.name == 'gc']
        weakref_imports = [node for node in tree.body if isinstance(node, ast.Import)
                           for alias in node.names if alias.name == 'weakref']
        if any(len(node.names) != 1 or node.names[0].asname for node in weakref_imports):
            raise TranslationError('use plain import weakref, without alias', source=source_name)
        if any(len(node.names) != 1 or node.names[0].asname for node in gc_imports):
            raise TranslationError('use plain import gc, without alias', source=source_name)
        has_import = bool(gc_imports)
        has_weakref = bool(weakref_imports)
        if any(isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == _SAFETY_FN
               for node in tree.body):
            raise TranslationError('reserved internal GC helper cannot be redefined', source=source_name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == _SAFETY_FN:
                raise TranslationError('reserved GC safepoint is compiler-only', source=source_name)
            if isinstance(node, ast.ImportFrom) and node.module in ('gc', 'weakref'):
                raise TranslationError('from gc/weakref import is unsupported', source=source_name)
            if isinstance(node, ast.Name) and node.id in ('gc','weakref') and isinstance(node.ctx, (ast.Store, ast.Del)):
                raise TranslationError('gc/weakref namespace shadowing is unsafe', source=source_name)
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == 'weakref':
                if node.attr not in _WEAKREF_APIS or not has_weakref:
                    raise TranslationError('unsupported or unimported weakref API', source=source_name)
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == 'gc':
                if node.attr not in _GC_APIS:
                    raise TranslationError(f'unsupported gc.{node.attr}', source=source_name)
                if not has_import:
                    raise TranslationError('gc API requires import gc', source=source_name)
            operation = _gc_call(node)
            if operation:
                if operation not in _GC_APIS:
                    raise TranslationError(f'unsupported gc.{operation}', source=source_name)
                if node.args or node.keywords:
                    raise TranslationError('gc API here accepts no arguments', source=source_name)
                if operation in ('collect', 'disable', 'enable'):
                    # These modify heap state. Only statement-level calls
                    # can be emitted safely without breaking expression purity.
                    permitted = any(isinstance(parent, ast.Expr) and parent.value is node
                                    for parent in ast.walk(tree))
                    if not permitted:
                        raise TranslationError(f'gc.{operation}() must be a standalone statement', source=source_name)
        tree.body = [n for n in tree.body if n not in gc_imports and n not in weakref_imports]
        SafePoints.apply(tree)
        self._active_env = None
        self._extra_roots = ()
        self._offsets = {}
        super().__init__(ast.unparse(tree), source_name, show_return=show_return)

    def roots(self, env, lines, *, include_temps=True):
        names = [entry.bend for entry in env.values()]
        names.extend(self._extra_roots)
        region = lines[self._offsets.get(id(lines), 0):] if include_temps else ()
        for line in region:
            matched = _ROOT_PATTERN.match(line)
            if matched:
                names.append(matched.group(1))
        names = list(dict.fromkeys(names))
        return '[' + ', '.join(names) + ']' if names else 'Nil{}'

    def expr(self, node, env, heap, lines):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == _SAFETY_FN:
            if node.args or node.keywords:
                self.fail('internal gc safepoint takes no arguments', node)
            heap = self.modify(f'PyHeap.maybe_collect({heap}, {self.roots(env, lines, include_temps=False)})', lines, 'gc_auto')
            value = self.bind('VNone{}', lines)
            self._offsets[id(lines)] = len(lines)
            return heap, value, 'None'
        operation = _gc_call(node)
        if operation:
            if operation == 'collect':
                heap = self.modify(f'PyHeap.collect({heap}, {self.roots(env, lines, include_temps=False)})', lines, 'gc_manual')
                return heap, self.bind('VNone{}', lines), 'None'
            if operation in ('enable', 'disable'):
                enabled = 'True{}' if operation == 'enable' else 'False{}'
                heap = self.modify(f'PyHeap.set_gc_enabled({heap}, {enabled})', lines, 'gc_mode')
                return heap, self.bind('VNone{}', lines), 'None'
            if operation == 'isenabled':
                return heap, self.bind(f'VBool{{PyHeap.is_gc_enabled({heap})}}', lines), 'bool'
            self.fail(f'unsupported gc API {operation}', node)
        # Weak pointers are deliberately non-tracing. Only statically known
        # user-class instances can be weakly referenced; lists and dicts
        # cannot be weak-referenced by CPython either.
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and \
           isinstance(node.func.value, ast.Name) and node.func.value.id == 'weakref' and node.func.attr == 'ref':
            if len(node.args) != 1 or node.keywords:
                self.fail('weakref.ref accepts one instance, no callback yet', node)
            heap, strong, typ = self.expr(node.args[0], env, heap, lines)
            if typ not in self.class_fields:
                self.fail('weakref.ref requires a user class instance', node)
            return heap, self.bind(f'PyHeap.make_weak({strong})', lines), f'weakref[{typ}]'
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in env and \
           env[node.func.id].typ.startswith('weakref['):
            if node.args or node.keywords:
                self.fail('weakref getter accepts no arguments', node)
            heap, wref, typ = self.expr(node.func, env, heap, lines)
            return heap, self.bind(f'PyHeap.deref_weak({heap}, {wref})', lines), typ[len('weakref['):-1]
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and \
           isinstance(node.ops[0], (ast.Is, ast.IsNot)) and \
           len(node.comparators) == 1 and \
           (isinstance(node.left, ast.Constant) and node.left.value is None or
            isinstance(node.comparators[0], ast.Constant) and node.comparators[0].value is None):
            heap, left, lt = self.expr(node.left, env, heap, lines)
            heap, right, rt = self.expr(node.comparators[0], env, heap, lines)
            comparison = f'PyValue.reference_eq({left}, {right})'
            if isinstance(node.ops[0], ast.IsNot):
                comparison = f'PyValue.not({comparison})'
            return heap, self.bind(comparison, lines), 'bool'
        previous = self._active_env
        self._active_env = env
        try:
            return super().expr(node, env, heap, lines)
        finally:
            self._active_env = previous

    def _lazy_helper(self, *, cond, heap, env, make_yes, make_no, held=None):
        # The v5 generated helper carries a held PyValue argument outside its
        # ordinary locals map. It must be rooted while recursive calls occur.
        def with_roots(factory):
            def compile_arm(h, captured, held_parameter, sink):
                previous = self._extra_roots
                self._extra_roots = previous + ((held_parameter,) if held_parameter else ())
                try:
                    return factory(h, captured, held_parameter, sink)
                finally:
                    self._extra_roots = previous
            return compile_arm
        return super()._lazy_helper(cond=cond, heap=heap, env=env,
                                    make_yes=with_roots(make_yes),
                                    make_no=with_roots(make_no), held=held)

    def pair(self, expr, lines):
        match = _CALL_PATTERN.fullmatch(expr)
        if not match:
            return super().pair(expr, lines)
        callee, original_heap, suffix = match.groups()
        env = self._active_env or {}
        roots = self.roots(env, lines)
        # Preserve all live caller values, including intermediate expression
        # temporaries, while the callee runs its own safepoints.
        pushed = self.modify(f'PyHeap.push_roots({original_heap}, {roots})', lines, 'gc_enter')
        modified = f'{callee}({pushed}{suffix or ""})'
        result_heap, result_val = super().pair(modified, lines)
        popped = self.modify(f'PyHeap.pop_roots({result_heap})', lines, 'gc_exit')
        return popped, result_val

    def compile(self):
        code = super().compile()
        for before, after in RECORD_REWRITE:
            if code.count(before) != 1:
                raise AssertionError('unexpected record representation: ' + before)
            code = code.replace(before, after, 1)
        # Weak pointers live in PyValue but are not visited by PyGC.mark_value.
        marker = '  VRef{address: Nat}\n'
        if code.count(marker) != 1:
            raise AssertionError('unknown PyValue layout')
        code = code.replace(marker, marker + '  VWeak{address: Nat}\n', 1)
        show_case = '    case VRef{address}:\n      "<object>"\n'
        if code.count(show_case) != 1:
            raise AssertionError('weak pointer display location changed')
        code = code.replace(show_case, show_case + '    case VWeak{address}:\n      "<weakref>"\n', 1)
        truth_case = '    case VRef{address}:\n      True{}\n'
        if code.count(truth_case) != 1:
            raise AssertionError('weak pointer truth location changed')
        code = code.replace(truth_case, truth_case + '    case VWeak{address}:\n      True{}\n', 1)
        ref_func = 'def PyValue.reference_eq(a: PyValue, b: PyValue) -> PyValue:\n  match a b:\n    case VRef{x} VRef{y}:\n      VBool{Nat.is_eq(x, y)}\n'
        if code.count(ref_func) != 1:
            raise AssertionError('identity reference operation layout changed')
        code = code.replace(ref_func, ref_func + '    case VNone{} VNone{}:\n      VBool{True{}}\n', 1)
        code = _split_heap_ctor(code)
        code = code.replace('__INITIAL_THRESHOLD__', f'{self.threshold}n')
        # All v7 PyGC functions traverse only references, never weak edges.
        # New frame-stack types must be declared before the runtime functions.
        marker = 'type PyHeap is Data:\n'
        if code.count(marker) != 1:
            raise AssertionError('missing heap datatype')
        code = code.replace(marker, 'type PyFrame is Data:\n  Frame{roots: List<&2, PyValue>}\n\n' + marker, 1)
        marker = 'def main() -> IO(Unit):\n'
        if code.count(marker) != 1:
            raise AssertionError('no unique entry point')
        code = code.replace(marker, _MARK_SWEEP + '\n\n' + _RUNTIME_V8.replace('type PyFrame is Data:\n  Frame{roots: List<&2, PyValue>}\n\n', '') + '\n' + _WEAKREF_BEND + '\n' + marker, 1)
        # Verify that runtime state fields are threaded through every heap
        # reconstruction and call-site preservation is generated.
        if '__INITIAL_THRESHOLD__' in code:
            raise AssertionError('threshold not instantiated')
        if 'Process.run(' in code or 'python_compat' in code:
            raise AssertionError('forbidden CPython delegation')
        return code


def translate_automatic_gc_file(path: Path, *, show_return: bool = False, threshold: int = 8) -> str:
    root_tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    imports = [node for node in root_tree.body if isinstance(node, ast.Import) and
               len(node.names) == 1 and node.names[0].name == 'gc' and node.names[0].asname is None]
    source = RuntimeModuleLinker(path).link()
    # Static linker discards import gc and emits the flattened functions.
    weak_imports = [n for n in root_tree.body if isinstance(n, ast.Import) and
                    len(n.names) == 1 and n.names[0].name == 'weakref' and n.names[0].asname is None]
    if imports:
        source = 'import gc\n' + source
    if weak_imports:
        source = 'import weakref\n' + source
    return AutomaticCollectorLowerer(source, str(path), show_return=show_return, threshold=threshold).compile()


V8_CASES = {
    'nested_caller_live': '''import gc

def inner(z: int) -> int:
    dead = []
    dead.append(dead)
    dead = [5]
    gc.collect()
    return z

def outer(held: list[int]) -> int:
    alias = held
    result = inner(2)
    return alias[0] + result

def main() -> int:
    xs = [40]
    return outer(xs)
''',
    'auto_reclaims_cycle': '''def main() -> int:
    live = [33]
    k = 0
    gone = []
    while k < 12:
        gone = []
        gone.append(gone)
        k += 1
    return live[0] + k
''',
    'recursive_frame': '''import gc

def visit(n: int, xs: list[int]) -> int:
    garbage = [n]
    if n == 0:
        gc.collect()
        return xs[0]
    return visit(n - 1, xs)

def main() -> int:
    xs = [51]
    return visit(4, xs)
''',
    'intermediate_argument': '''import gc

def trigger() -> int:
    tmp = [1]
    gc.collect()
    return 7

def examine(x: list[int], y: int) -> int:
    return x[0] + y

def main() -> int:
    return examine([9], trigger())
''',
    'explicit_disable_enable': '''import gc

def main() -> int:
    gc.disable()
    xs = [1]
    gc.enable()
    gc.collect()
    return xs[0]
''',
    'nested_method_gc': '''import gc

class Box:
    child: list[int]
    def __init__(self, child: list[int]) -> None:
        self.child = child
    def tidy(self) -> None:
        dead = []
        dead.append(dead)
        gc.collect()

def main() -> int:
    xs = [31]
    obj = Box(xs)
    obj.tidy()
    return obj.child[0]
''',
    'weakref_live_and_dead': '''import gc
import weakref

class Box:
    value: int
    def __init__(self, value: int) -> None:
        self.value = value

def main() -> int:
    box = Box(7)
    w = weakref.ref(box)
    gc.collect()
    if w() is None:
        return 99
    box = None
    gc.collect()
    if w() is None:
        return 1
    return 0
'''

}


def run_smoke(*, bend: str = 'bend', wsl: bool = False, source_only: bool = False,
              threshold: int = 1) -> int:
    if not source_only and not wsl and shutil.which(bend) is None:
        print('PY3_BEND_GC_V8 BLOCKED bend-executable-missing native-executed=0')
        return 2
    emitted = executed = failures = 0
    with tempfile.TemporaryDirectory(prefix='pybend-gc-v8-') as td:
        for name, source in V8_CASES.items():
            try:
                root = Path(td)
                py = root / (name + '.py')
                py.write_text(source, encoding='utf-8')
                bend_src = AutomaticCollectorLowerer(source, source_name=name,
                                             show_return=True, threshold=threshold).compile()
                b = root / (name + '.bend')
                b.write_text(bend_src, encoding='utf-8')
                emitted += 1
                if source_only:
                    print(f'PY3_BEND_GC_V8 {name} SOURCE_ONLY compiler_verified=false')
                    continue
                ok, verdict = run_bend_gate(b, bend, wsl=wsl)
                if not ok:
                    raise RuntimeError('BEND_CHECK_FAILED ' + verdict)
                oracle = subprocess.run(
                    [sys.executable, '-B', '-c',
                     'import runpy; module=runpy.run_path(' + repr(str(py)) + '); print(module["main"]())'],
                    capture_output=True, text=True, timeout=30)
                if oracle.returncode:
                    raise RuntimeError('PY_ORACLE_FAILED ' + oracle.stderr)
                actual = subprocess.run(bend_command(b, bend=bend, wsl=wsl),
                                        capture_output=True, text=True, timeout=90)
                if actual.returncode or actual.stdout != oracle.stdout:
                    raise RuntimeError(f'DIFFERENTIAL_FAIL returncode={actual.returncode} '
                                       f'expected={oracle.stdout!r} actual={actual.stdout!r} '
                                       f'stderr={actual.stderr[-900:]!r}')
                executed += 1
                print(f'PY3_BEND_GC_V8 {name} BEND_EXECUTION_PASS')
            except (TranslationError, AssertionError, OSError, RuntimeError,
                    subprocess.TimeoutExpired) as exc:
                failures += 1
                print(f'PY3_BEND_GC_V8 {name} FAIL {exc}')
    print(f'PY3_BEND_GC_V8 SUMMARY source_emitted={emitted} native_executed={executed} '
          f'failures={failures} certified={not source_only and executed == len(V8_CASES) and failures == 0}')
    return 0 if failures == 0 and (source_only or executed == len(V8_CASES)) else 2


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action', required=True)
    tr = sub.add_parser('translate')
    tr.add_argument('input', type=Path)
    tr.add_argument('-o', '--output', type=Path)
    tr.add_argument('--threshold', type=int, default=8)
    tr.add_argument('--show-return', action='store_true')
    tr.add_argument('--bend', default='bend')
    tr.add_argument('--wsl', action='store_true')
    tr.add_argument('--require-bend', action='store_true')
    smoke = sub.add_parser('smoke')
    smoke.add_argument('--threshold', type=int, default=1)
    smoke.add_argument('--bend', default='bend')
    smoke.add_argument('--wsl', action='store_true')
    smoke.add_argument('--source-only', action='store_true')
    a = p.parse_args(argv)
    if a.action == 'smoke':
        return run_smoke(bend=a.bend, wsl=a.wsl,
                         source_only=a.source_only, threshold=a.threshold)
    try:
        out = a.output or a.input.with_suffix('.bend')
        code = translate_automatic_gc_file(a.input, threshold=a.threshold,
                                 show_return=a.show_return)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(code, encoding='utf-8')
        print(f'PY3_BEND_GC_V8 SOURCE_EMITTED {out} bend_verified=false')
        if a.require_bend:
            checked, verdict = run_bend_gate(out, a.bend, wsl=a.wsl)
            print('PY3_BEND_GC_V8 ' + verdict)
            return 0 if checked else 2
        return 0
    except (TranslationError, OSError, SyntaxError, AssertionError, ValueError) as exc:
        print('PY3_BEND_GC_V8 ERROR ' + str(exc), file=sys.stderr)
        return 2
