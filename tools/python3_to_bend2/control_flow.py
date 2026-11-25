"""Native Bend 2 compiler increment over v4: corrected loop/lazy-expression lowering.

This module does NOT claim Python 3 completeness. The compiler and runtime gate
must be executed with the actual Bend 2.0.40 executable to certify results.
No generated program delegates to CPython; CPython is only the test oracle.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Sequence

from tools.python3_to_bend2.syntax import TranslationError, bend_command
from tools.python3_to_bend2.integers import run_bend_gate
from tools.python3_to_bend2.heap import HeapTranslator, Local, SMOKE_CASES


class ControlFlowLowerer(HeapTranslator):
    """Compiler pass with Python-correct range evaluation and lazy branching."""

    def __init__(self, source: str, source_name: str = '<input>', *, show_return: bool = False):
        super().__init__(source, source_name, show_return=show_return)
        self.reserved = {n.id for n in ast.walk(self.tree) if isinstance(n, ast.Name)}
        self.reserved.update(n.arg for n in ast.walk(self.tree) if isinstance(n, ast.arg))

    def gensym(self, prefix: str = 'tmp') -> str:
        while True:
            self.counter += 1
            symbol = f'py5_{prefix}_{self.counter}'
            if not hasattr(self, 'reserved') or symbol not in self.reserved:
                if hasattr(self, 'reserved'):
                    self.reserved.add(symbol)
                return symbol

    def _while(self, node, rest, env, heap, lines, return_cont, *, break_cont=None, continue_cont=None):
        # A while-else suite executes only after normal test exhaustion.
        # A break goes directly to 'rest', skipping that else suite.
        assigned = {target.id for stmt in ast.walk(node) for target in
                    (stmt.targets if isinstance(stmt, ast.Assign) else
                     [stmt.target] if isinstance(stmt, (ast.AnnAssign, ast.AugAssign)) else [])
                    if isinstance(target, ast.Name)}
        missing = assigned - set(env)
        if missing:
            self.fail('loop locals must be initialized before the loop: ' + ', '.join(sorted(missing)), node)
        testname = self.gensym('while_test')
        stepname = self.gensym('while_step')
        startenv = {k: Local(self.gensym('loop_' + k), v.typ) for k, v in env.items()}
        testh = self.gensym('heap')
        testlines: list[str] = []
        checked_h, tested, _ = self.expr(node.test, startenv, testh, testlines)
        testlines.append(stepname + '(' + ', '.join([f'PyHeap.truth({checked_h}, {tested})', checked_h] +
                                             [x.bend for x in startenv.values()]) + ')')
        self.helpers.append(f'@unsafe def {testname}(+{testh}: PyHeap' +
                            (', ' + self._params(startenv) if startenv else '') +
                            ') -> PyHeap & PyValue:\n' + '\n'.join('  ' + s for s in testlines))
        steph = self.gensym('heap')
        flag = self.gensym('flag')

        def go_next(nextenv, nexth):
            if set(nextenv) != set(startenv):
                self.fail('loop body changed live variable set', node)
            return [self._join(testname, nexth, nextenv)]

        def go_break(nextenv, nexth):
            return self.block(rest, nextenv, nexth, return_cont,
                              break_cont=break_cont, continue_cont=continue_cont)

        yes = self.block(node.body, dict(startenv), steph, go_next,
                         break_cont=go_break, continue_cont=go_next)
        no = self.block(node.orelse + rest, dict(startenv), steph, return_cont,
                        break_cont=break_cont, continue_cont=continue_cont)
        self.helpers.append(f'@unsafe def {stepname}({flag}: Bool, +{steph}: PyHeap' +
                            (', ' + self._params(startenv) if startenv else '') +
                            ') -> PyHeap & PyValue:\n  match ' + flag + ':\n    case True{}:\n' +
                            '\n'.join('      ' + s for s in yes) + '\n    case False{}:\n' +
                            '\n'.join('      ' + s for s in no))
        lines.append(self._join(testname, heap, env))
        return lines

    def _for(self, node: ast.For, env: dict[str, Local] | None = None) -> list[ast.stmt]:
        if not isinstance(node.target, ast.Name) or not isinstance(node.iter, ast.Call) or \
           not isinstance(node.iter.func, ast.Name) or node.iter.func.id != 'range' or node.iter.keywords:
            self.fail('native for requires range(...) and a simple target', node)
        args = node.iter.args
        if not 1 <= len(args) <= 3:
            self.fail('range expects 1 to 3 arguments', node)
        first, end = (ast.Constant(0), args[0]) if len(args) == 1 else (args[0], args[1])
        step = 1
        if len(args) == 3:
            try:
                step = ast.literal_eval(args[2])
            except (TypeError, ValueError, SyntaxError, MemoryError, RecursionError):
                step = None
            if type(step) is not int or step == 0:
                self.fail('range step must be a known nonzero integer', node)
        # The hidden cursor is distinct from the visible Python 'for' target.
        # This ensures reassigning the target never changes the iteration.
        start_name = self.gensym('range_start')
        stop_name = self.gensym('range_stop')
        cursor_name = self.gensym('range_cursor')

        def get(name):
            return ast.Name(id=name, ctx=ast.Load())

        def put(name, expr):
            return ast.Assign(targets=[ast.Name(id=name, ctx=ast.Store())], value=expr)

        # Python evaluates range(start, stop, step) from left to right, once.
        initial = [put(start_name, first), put(stop_name, end),
                   put(cursor_name, get(start_name))]
        # A pre-existing target survives an empty loop, exactly as in Python.
        # If no pre-existing target exists, initialize only for the Bend loop
        # carrier; post-loop reads of possibly unbound targets are rejected.
        if env is None or node.target.id not in env:
            initial.append(put(node.target.id, get(start_name)))
        elif env[node.target.id].typ != 'int':
            self.fail('iteration target rebinds a differently typed variable', node)
        predicate = ast.Compare(left=get(cursor_name),
                                ops=[ast.Lt() if step > 0 else ast.Gt()],
                                comparators=[get(stop_name)])
        body = [put(node.target.id, get(cursor_name)),
                ast.AugAssign(target=ast.Name(id=cursor_name, ctx=ast.Store()),
                              op=ast.Add(), value=ast.Constant(step)),
                *node.body]
        loop = ast.While(test=predicate, body=body, orelse=list(node.orelse))
        return [ast.fix_missing_locations(ast.copy_location(item, node)) for item in [*initial, loop]]

    def block(self, statements, env, heap, return_cont, *, break_cont=None, continue_cont=None):
        # Mirrors v4's control flow, but a for loop is lowered with the actual
        # incoming environment so empty ranges preserve existing bindings.
        lines = []
        pending = [s for s in statements if not (isinstance(s, ast.Expr) and
                   isinstance(s.value, ast.Constant) and isinstance(s.value.value, str))]
        while pending:
            stmt, pending = pending[0], pending[1:]
            if isinstance(stmt, ast.Return):
                value_node = stmt.value if stmt.value is not None else ast.Constant(None)
                heap, value, returned_type = self.expr(value_node, env, heap, lines)
                if self.current_return not in ('unknown', returned_type):
                    self.fail(f'return value type {returned_type} does not match {self.current_return}', stmt)
                lines.append(f'({heap}, {value})')
                return lines
            if isinstance(stmt, ast.If):
                return self._bool_dispatch(stmt, pending, env, heap, lines, return_cont,
                                           break_cont=break_cont, continue_cont=continue_cont)
            if isinstance(stmt, ast.For):
                # A Python loop variable does not exist when an empty range
                # finishes. The value-carrier implementation cannot yet raise
                # UnboundLocalError here; refuse instead of silently forging it.
                if isinstance(stmt.target, ast.Name) and stmt.target.id not in env:
                    target = stmt.target.id
                    remainder = [*stmt.orelse, *pending]
                    if any(isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id == target
                           for part in remainder for n in ast.walk(part)):
                        self.fail(f'loop target {target!r} may be unbound after empty range', stmt)
                pending = self._for(stmt, env) + pending
                continue
            if isinstance(stmt, ast.While):
                return self._while(stmt, pending, env, heap, lines, return_cont,
                                   break_cont=break_cont, continue_cont=continue_cont)
            if isinstance(stmt, ast.Break):
                if break_cont is None:
                    self.fail('break outside loop', stmt)
                return lines + break_cont(env, heap)
            if isinstance(stmt, ast.Continue):
                if continue_cont is None:
                    self.fail('continue outside loop', stmt)
                return lines + continue_cont(env, heap)
            if isinstance(stmt, ast.Assign):
                if len(stmt.targets) != 1:
                    self.fail('chained unpacking assignment not yet lowered', stmt)
                env, heap = self.assign(stmt.targets[0], stmt.value, env, heap, lines)
                continue
            if isinstance(stmt, ast.AnnAssign):
                if stmt.value is None:
                    self.fail('uninitialized annotated local', stmt)
                env, heap = self.assign(stmt.target, stmt.value, env, heap, lines,
                                        self.anno(stmt.annotation))
                continue
            if isinstance(stmt, ast.AugAssign):
                expr = ast.BinOp(
                    left=ast.copy_location(ast.fix_missing_locations(
                        ast.parse(ast.unparse(stmt.target), mode='eval').body), stmt),
                    op=stmt.op, right=stmt.value)
                env, heap = self.assign(stmt.target, expr, env, heap, lines)
                continue
            if isinstance(stmt, ast.Expr):
                heap, _, _ = self.expr(stmt.value, env, heap, lines)
                continue
            if isinstance(stmt, ast.Pass):
                continue
            self.fail('unsupported statement ' + type(stmt).__name__, stmt)
        return lines + return_cont(env, heap)

    def _lazy_helper(self, *, cond: str, heap: str, env: dict[str, Local],
                     make_yes, make_no, held: str | None = None) -> tuple[str, str, str]:
        name = self.gensym('lazy')
        flag = self.gensym('flag')
        inner_heap = self.gensym('heap')
        arg = self.gensym('held') if held is not None else None
        case_env = {k: Local(self.gensym('capture_' + k), local.typ)
                    for k, local in env.items()}
        captured_args = [local.bend for local in env.values()]

        def compile_arm(factory):
            statements: list[str] = []
            h, result, typ = factory(inner_heap, case_env, arg, statements)
            statements.append(f'({h}, {result})')
            return statements, typ

        yes, yt = compile_arm(make_yes)
        no, nt = compile_arm(make_no)
        if yt != nt:
            raise TranslationError(f'lazy branches have inconsistent types {yt!r} and {nt!r}',
                                   source=self.source_name)
        params = f'{flag}: Bool, +{inner_heap}: PyHeap'
        if arg is not None:
            params += f', +{arg}: PyValue'
        if case_env:
            params += ', ' + self._params(case_env)
        self.helpers.append(
            f'def {name}({params}) -> PyHeap & PyValue:\n  match {flag}:\n    case True{{}}:\n' +
            '\n'.join('      ' + s for s in yes) + '\n    case False{}:\n' +
            '\n'.join('      ' + s for s in no))
        actual_args = [cond, heap] + ([held] if held is not None else []) + captured_args
        # Return a call expression; the caller consumes the state-value pair.
        return name + '(' + ', '.join(actual_args) + ')', yt, name

    def _chain_compare_step(self, node, index, previous, previous_type, env, heap, lines):
        op = node.ops[index]
        right_node = node.comparators[index]
        heap, right, right_type = self.expr(right_node, env, heap, lines)
        mapping = {
            ast.Eq: 'eq', ast.NotEq: 'ne', ast.Lt: 'lt', ast.Gt: 'gt',
            ast.LtE: 'le', ast.GtE: 'ge', ast.Is: 'reference_eq',
        }
        operation = mapping.get(type(op))
        if operation is None:
            self.fail('unsupported chained comparison operation', node)
        if operation == 'reference_eq' and not (
            previous_type in self.class_fields or previous_type.startswith('list[')
        ):
            self.fail('identity comparison requires heap objects', node)
        if operation in ('eq', 'ne') and (
            previous_type.startswith('list[') or right_type.startswith('list[')
        ):
            self.fail('list == requires recursive value equality', node)
        if operation in ('lt', 'gt', 'le', 'ge') and (previous_type != right_type or right_type != 'int'):
            self.fail('ordered comparison requires integers', node)
        if previous_type != right_type and operation != 'reference_eq':
            self.fail('mixed-type comparison requires Python coercion', node)
        checked = self.bind(f'PyValue.{operation}({previous}, {right})', lines)
        if index + 1 == len(node.ops):
            return heap, checked, 'bool'

        def pass_next(h, captured, held, sink):
            return self._chain_compare_step(node, index+1, held, right_type, captured, h, sink)
        def fail_early(h, captured, held, sink):
            return h, self.bind('VBool{False{}}', sink), 'bool'
        call, typ, _ = self._lazy_helper(
            cond=f'PyHeap.truth({heap}, {checked})', heap=heap,
            env=env, make_yes=pass_next, make_no=fail_early, held=right)
        heap, value = self.pair(call, lines)
        return heap, value, typ

    def expr(self, node, env, heap, lines):
        if isinstance(node, ast.Compare) and len(node.ops) > 1:
            heap, value, typ = self.expr(node.left, env, heap, lines)
            return self._chain_compare_step(node, 0, value, typ, env, heap, lines)
        if isinstance(node, ast.IfExp):
            heap, test, _ = self.expr(node.test, env, heap, lines)

            def yes(h, captured, held, sink):
                return self.expr(node.body, captured, h, sink)
            def no(h, captured, held, sink):
                return self.expr(node.orelse, captured, h, sink)

            call, typ, _ = self._lazy_helper(
                cond=f'PyHeap.truth({heap}, {test})', heap=heap, env=env,
                make_yes=yes, make_no=no)
            heap, value = self.pair(call, lines)
            return heap, value, typ

        if isinstance(node, ast.BoolOp):
            if len(node.values) < 2:
                self.fail('boolean expression requires two operands', node)
            heap, head, head_type = self.expr(node.values[0], env, heap, lines)
            remaining = node.values[1:]
            tail = remaining[0] if len(remaining) == 1 else ast.BoolOp(op=node.op, values=remaining)

            def progress(h, captured, held, sink):
                return self.expr(tail, captured, h, sink)
            def keep(h, captured, held, sink):
                return h, held, head_type
            if isinstance(node.op, ast.And):
                yes, no = progress, keep
            elif isinstance(node.op, ast.Or):
                yes, no = keep, progress
            else:
                self.fail('unsupported bool operator', node)
            call, typ, _ = self._lazy_helper(
                cond=f'PyHeap.truth({heap}, {head})', heap=heap, env=env,
                make_yes=yes, make_no=no, held=head)
            heap, value = self.pair(call, lines)
            return heap, value, typ
        return super().expr(node, env, heap, lines)

    def compile(self) -> str:
        source = super().compile()
        # A tagged VError at the final boundary must fail the process; a
        # successful-looking printed error was the v4 fail-open behavior.
        boundary = '''\
def PyHeap.finish_io(pair: PyHeap & PyValue, show_return: Bool) -> IO(Unit):
  (h, value) = pair
  PyHeap.finish_io_value(h, value, show_return)

def PyHeap.finish_io_value(h: PyHeap, value: PyValue, show_return: Bool) -> IO(Unit):
  match value:
    case VError{message}:
      do IO<Unit>:
        IO.write(PyHeap.out(h))
        IO.die(Unit, 1, message)
    case other:
      IO.write(PyHeap.finish_show(show_return, PyHeap.out(h), other))
'''
        marker = 'def main() -> IO(Unit):\n'
        if source.count(marker) != 1:
            raise AssertionError('generated Bend entry point is not unique')
        source = source[:source.rfind(marker)] + boundary + '\n' + marker + \
                 '  PyHeap.finish_io(py3_main(PyHeap.empty()), ' + \
                 ('True{}' if self.show_return else 'False{}') + ')\n'
        return source


def translate_control_flow(source: str, name: str = '<input>', *, show_return: bool = False) -> str:
    return ControlFlowLowerer(source, name, show_return=show_return).compile()


V5_CASES = dict(SMOKE_CASES)
V5_CASES.update({
    'range_continue': '''def main() -> int:
    total = 0
    for k in range(2, 13, 2):
        if k == 6:
            continue
        total += k
    return total
''',
    'range_break_else': '''def main() -> int:
    total = 0
    for k in range(8):
        if k == 3:
            break
        total += k
    else:
        total += 1000
    return total
''',
    'range_target_rebind': '''def main() -> int:
    count = 0
    for k in range(6):
        k = 100
        count += 1
    return count
''',
    'while_else': '''def main() -> int:
    x = 0
    while x < 3:
        x += 1
    else:
        x += 10
    return x
''',
    'while_break_skips_else': '''def main() -> int:
    x = 0
    while x < 3:
        x += 1
        if x == 2:
            break
    else:
        x = 999
    return x
''',
    'ifexp_lazy': '''def tick(xs: list[int], n: int) -> int:
    xs.append(n)
    return n

def main() -> int:
    xs: list[int] = []
    v = tick(xs, 4) if True else tick(xs, 9)
    return len(xs) + v
''',
    'short_circuit_and': '''def push(xs: list[int]) -> bool:
    xs.append(2)
    return True

def main() -> int:
    xs: list[int] = []
    false = False
    check = false and push(xs)
    return len(xs)
''',
    'chained_compare_short_circuit': '''def note(xs: list[int], v: int) -> int:
    xs.append(v)
    return v

def main() -> int:
    xs: list[int] = []
    result = 5 < note(xs, 2) < note(xs, 3)
    return len(xs)
''',
    'short_circuit_or': '''def push(xs: list[int]) -> bool:
    xs.append(2)
    return True

def main() -> int:
    xs: list[int] = []
    true = True
    check = true or push(xs)
    return len(xs)
''',
})


def run_control_flow_smoke(*, bend: str = 'bend', wsl: bool = False, require_bend: bool = True,
                 only: str | None = None) -> int:
    """Actual runtime gate (required by default); no fake compiler success."""
    cases = {only: V5_CASES[only]} if only else V5_CASES
    if require_bend and not wsl and shutil.which(bend) is None:
        print('PY3_BEND_V5 BLOCKED: Bend executable not available; no execution-certified cases')
        return 2
    failures = 0
    verified = 0
    with tempfile.TemporaryDirectory(prefix='py3-bend-v5-') as td:
        for label, source in cases.items():
            try:
                result = translate_control_flow(source, label, show_return=True)
                if not require_bend:
                    print(f'PY3_BEND_V5 {label} AST_EMIT_PASS bend=NOT_EXECUTED')
                    continue
                # Compare actual process result, stdout and stderr for expected
                # successful runs. Any negative/nonzero process exit is failure.
                expected_buf = io.StringIO()
                namespace: dict[str, object] = {'__name__': f'_py3_bend_v5_{label}'}
                exec(compile(source, f'{label}.py', 'exec'), namespace)
                with contextlib.redirect_stdout(expected_buf):
                    expected_return = namespace['main']()
                expected = expected_buf.getvalue() + str(expected_return) + '\n'
                path = Path(td) / (label + '.bend')
                path.write_text(result, encoding='utf-8')
                checked, verdict = run_bend_gate(path, bend, wsl=wsl)
                if not checked:
                    raise RuntimeError('BEND_COMPILER_REJECTED: ' + verdict)
                proc = subprocess.run(bend_command(path, bend=bend, wsl=wsl),
                                      capture_output=True, text=True, timeout=90)
                if proc.returncode or proc.stdout != expected:
                    raise RuntimeError(f'DIFFERENTIAL_MISMATCH: returncode={proc.returncode} '
                                       f'expected={expected!r} actual={proc.stdout!r} '
                                       f'stderr={proc.stderr[-1600:]!r}')
                verified += 1
                print(f'PY3_BEND_V5 {label} COMPILED_AND_EXECUTED_PASS')
            except (TranslationError, AssertionError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                failures += 1
                print(f'PY3_BEND_V5 {label} FAIL {exc}')
    print(f'PY3_BEND_V5 SUMMARY source_cases={len(cases)} executed={verified} '
          f'failed={failures} compiler_verified={require_bend and not failures}')
    return 2 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    translate = actions.add_parser('translate')
    translate.add_argument('input', type=Path)
    translate.add_argument('-o', '--output', type=Path)
    translate.add_argument('--show-return', action='store_true')
    translate.add_argument('--require-bend', action='store_true')
    translate.add_argument('--bend', default='bend')
    translate.add_argument('--wsl', action='store_true')
    smoke = actions.add_parser('smoke')
    smoke.add_argument('--bend', default='bend')
    smoke.add_argument('--wsl', action='store_true')
    smoke.add_argument('--source-only', action='store_true')
    smoke.add_argument('--only', choices=tuple(sorted(V5_CASES)))
    args = parser.parse_args(argv)
    if args.action == 'smoke':
        return run_control_flow_smoke(bend=args.bend, wsl=args.wsl,
                            require_bend=not args.source_only, only=args.only)
    try:
        src = args.input.read_text(encoding='utf-8')
        text = translate_control_flow(src, str(args.input), show_return=args.show_return)
        path = args.output or args.input.with_suffix('.bend')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        print(f'PY3_BEND_V5 GENERATED {path} runtime_verified=false')
        if args.require_bend:
            ok, verdict = run_bend_gate(path, args.bend, wsl=args.wsl)
            print(f'PY3_BEND_V5 {verdict}')
            return 0 if ok else 2
        return 0
    except (TranslationError, OSError) as exc:
        print(f'PY3_BEND_V5 ERROR {exc}', file=sys.stderr)
        return 2
