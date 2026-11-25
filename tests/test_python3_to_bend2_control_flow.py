"""v5 compiler-lowering regression tests.

Structural and Python-transformation tests are not native Bend runtime tests.
The standalone v5 smoke --require-Bend lane is mandatory for certification.
"""
from __future__ import annotations

import ast
import contextlib
import io
import random
import tempfile
from pathlib import Path
import unittest

from tools.python3_to_bend2.syntax import TranslationError
from tools.python3_to_bend2.control_flow import (
    ControlFlowLowerer, V5_CASES, run_control_flow_smoke, translate_control_flow,
)


def python_range_lower(source: str):
    """Execute the same range→while AST rewrite in CPython as a semantic model."""
    compiler = ControlFlowLowerer('def main() -> int:\n    return 0\n')

    class Rewrite(ast.NodeTransformer):
        def visit_For(self, node):
            # Inner loops get their own cursors; walk them before rewriting.
            node = self.generic_visit(node)
            return compiler._for(node)

    original = ast.parse(source)
    lowered = ast.fix_missing_locations(Rewrite().visit(original))

    def run(tree):
        scope = {'__name__': '__test__'}
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exec(compile(tree, '<range-model>', 'exec'), scope)
            value = scope['main']()
        return value, output.getvalue(), scope.get('calls')

    return run(original), run(lowered)


class RangeSemanticsTests(unittest.TestCase):
    def assert_equivalent(self, source):
        original, lowered = python_range_lower(source)
        self.assertEqual(original, lowered)

    def test_target_reassignment_does_not_control_iterator(self):
        self.assert_equivalent('''def main():
    total = 0
    for n in range(1, 10, 2):
        total += n
        n = 500
    return total
''')

    def test_continue_consumes_iteration(self):
        self.assert_equivalent('''def main():
    x = 0
    for n in range(1, 8):
        if n % 2:
            continue
        x += n
    return x
''')

    def test_break_skips_for_else(self):
        self.assert_equivalent('''def main():
    count = 0
    for n in range(10):
        count += n
        if n >= 4:
            break
    else:
        count = 999
    return count
''')

    def test_exhaustion_runs_else(self):
        self.assert_equivalent('''def main():
    count = 0
    for n in range(0):
        count += n
    else:
        count += 4
    return count
''')

    def test_range_arguments_once_in_order(self):
        self.assert_equivalent('''calls = []
def mark(n):
    calls.append(n)
    return n

def main():
    count = 0
    for n in range(mark(2), mark(7), 2):
        count += n
    return count
''')

    def test_nested_break_continue_scopes(self):
        self.assert_equivalent('''def main():
    tally = 0
    for i in range(1, 5):
        for j in range(1, 5):
            if j == 2:
                continue
            if i == 3 and j == 3:
                break
            tally += i * j
    return tally
''')

    def test_fuzz_literal_ranges(self):
        rng = random.Random(1941)
        for i in range(75):
            start, stop = rng.randint(-15, 15), rng.randint(-15, 15)
            step = rng.choice([-5, -3, -1, 1, 2, 4])
            source = f'''def main():
    total = 0
    for n in range({start}, {stop}, {step}):
        if n == 0:
            continue
        total += n
        if total > 100:
            break
    else:
        total += 7
    return total
'''
            with self.subTest(i=i, start=start, stop=stop, step=step):
                self.assert_equivalent(source)


class ControlFlowEmitterTests(unittest.TestCase):
    def test_all_cases_generate_without_cpython_bridge(self):
        for label, source in V5_CASES.items():
            with self.subTest(label=label):
                bend = translate_control_flow(source, label, show_return=True)
                self.assertIn('def PyHeap.finish_io(', bend)
                self.assertIn('IO.die(Unit, 1, message)', bend)
                self.assertIn('def main() -> IO(Unit):', bend)
                self.assertNotIn('Process.run(', bend)
                self.assertNotIn('python_compat.py', bend)

    def test_break_continue_and_while_else_are_lowered(self):
        src = V5_CASES['range_break_else']
        bend = translate_control_flow(src)
        self.assertRegex(bend, r'py5_range_cursor_\d+')
        self.assertRegex(bend, r'@unsafe def py5_while_test_\d+')
        self.assertIn('PyHeap.truth(', bend)
        self.assertIn('PyValue.add(', bend)

    def test_lazy_and_or_return_values_not_coerced_bool(self):
        for op in ('and', 'or'):
            source = f'''def main() -> int:
    a = 0
    b = 3
    return a {op} b
'''
            bend = translate_control_flow(source)
            self.assertRegex(bend, r'def py5_lazy_\d+')
            self.assertIn('PyHeap.truth(', bend)
            self.assertIn('VInt{PyPos{', bend)

    def test_ifexp_branches_lazy_with_effect(self):
        bend = translate_control_flow(V5_CASES['ifexp_lazy'])
        self.assertRegex(bend, r'def py5_lazy_\d+')
        self.assertIn('case True{}:', bend)
        self.assertIn('case False{}:', bend)
        self.assertEqual(bend.count('py3_tick('), 3)  # definition and two branches

    def test_mismatched_lazy_branch_types_refused(self):
        for expression in ('3 if True else "a"', '1 or "b"'):
            with self.subTest(expression=expression):
                with self.assertRaisesRegex(TranslationError, 'inconsistent types'):
                    translate_control_flow(f'def main():\n    return {expression}\n')

    def test_unique_symbol_names_even_if_user_chooses_prefix(self):
        source = 'def main() -> int:\n    py5_val_1 = 1\n    py5_range_cursor_2 = 2\n    return py5_val_1 + py5_range_cursor_2\n'
        bend = translate_control_flow(source)
        self.assertIn('PyValue.add(', bend)

    def test_existing_target_survives_empty_range(self):
        src = '''def main() -> int:
    k = 99
    for k in range(0):
        k = 1
    return k
'''
        self.assertIn('py3_main(', translate_control_flow(src))
        # Check that the Python AST rewrite has no synthetic target write
        # before the loop when the original target was already bound.
        fn = ast.parse(src).body[0]
        compiler = ControlFlowLowerer('def main() -> int:\n    return 0\n')
        stmts = compiler._for(fn.body[1], {'k': __import__('tools.python3_to_bend2.heap', fromlist=['Local']).Local('k', 'int')})
        self.assertFalse(any(isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == 'k' for t in stmt.targets)
            for stmt in stmts[:-1]))
        fn.body[1:2] = stmts
        scope = {}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])),
                     '<zero-range>', 'exec'), scope)
        self.assertEqual(scope['main'](), 99)

    def test_unbound_loop_target_after_empty_range_rejected(self):
        source = '''def main() -> int:
    for i in range(0):
        pass
    return i
'''
        with self.assertRaisesRegex(TranslationError, 'may be unbound'):
            translate_control_flow(source)

    def test_while_else_break_and_normal(self):
        for label in ('while_break_skips_else', 'while_else'):
            with self.subTest(label=label):
                bend = translate_control_flow(V5_CASES[label])
                self.assertIn('def py5_while_step', bend)

    def test_strict_gate_unavailable_compiler_cannot_pass(self):
        self.assertEqual(2, run_control_flow_smoke(bend='definitely-unavailable-bend-executable', only='range_continue'))


if __name__ == '__main__':
    unittest.main()
