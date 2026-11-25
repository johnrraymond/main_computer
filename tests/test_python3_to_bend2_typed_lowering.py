"""Additional AST compiler/fail-closed contract cases; Bend runtime checks are separate."""
from __future__ import annotations
import ast
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import contextlib
import io

from tools.python3_to_bend2.typed_lowering import NativeTranslator, TranslationError, emit_compat, EXTENDED_SMOKES, auto_semantic_risk, main


class TypedLoweringContract(unittest.TestCase):
    def output(self, src: str, *, unsafe=False) -> str:
        return NativeTranslator(src, 'fixture.py', unsafe_recursion=unsafe).compile()

    def test_counted_loop_has_structural_nat_fuel(self):
        text = self.output(EXTENDED_SMOKES[0][1])
        self.assertIn('match pyfuel_0001:', text)
        self.assertIn('case 1n+pyrem_0001:', text)
        self.assertIn('pyloop_0001(pyrem_0001,', text)
        self.assertNotIn('@unsafe', text)

    def test_dynamic_counted_loop_uses_u32_to_nat(self):
        text = self.output(EXTENDED_SMOKES[1][1])
        self.assertIn('U32.to_nat(n)', text)
        self.assertNotIn('@unsafe', text)

    def test_while_is_unverified_and_uses_rebound_state(self):
        text = self.output(EXTENDED_SMOKES[2][1])
        self.assertIn('@unsafe def pyloop', text)
        self.assertIn('@unsafe def pystep', text)
        self.assertIn('pyloop_0001(pyssa_i_1, pyssa_s_1)', text)

    def test_nonterminal_if(self):
        text = self.output('def main() -> int:\n    n = 2\n    if n > 3:\n        n = 7\n    else:\n        n = 4\n    return n\n')
        self.assertIn('match pycond_0001:', text)
        self.assertIn('pyssa_n_1', text)

    def test_list_append_is_rebound(self):
        text = self.output(EXTENDED_SMOKES[3][1])
        self.assertIn('List.append(&2, U32, a, [3])', text)
        self.assertIn('pyssa_a_1', text)

    def test_index_update_static_bounds(self):
        text = self.output(EXTENDED_SMOKES[4][1])
        self.assertIn('List.set(&2, U32, a, 1n, 9)', text)
        with self.assertRaisesRegex(TranslationError, 'statically in range'):
            self.output('def main() -> list[int]:\n    xs = [1]\n    xs[3] = 7\n    return xs\n')

    def test_refuse_mutation_of_list_parameter_aliases(self):
        with self.assertRaisesRegex(TranslationError, 'caller aliases'):
            self.output('def f(xs: list[int]) -> list[int]:\n    xs.append(4)\n    return xs\n')

    def test_refuse_list_aliasing(self):
        with self.assertRaisesRegex(TranslationError, 'aliasing'):
            self.output('def f() -> list[int]:\n    a = [1]\n    b = a\n    b.append(3)\n    return a\n')

    def test_refuse_negative_big_int(self):
        with self.assertRaises(TranslationError):
            self.output('def main() -> int:\n    return -1\n')
        with self.assertRaises(TranslationError):
            self.output('def main() -> int:\n    return 2 ** 130\n')

    def test_record_class_construction_and_method(self):
        text = self.output(EXTENDED_SMOKES[5][1])
        self.assertIn('type PyPoint is Data:', text)
        self.assertIn('case PyPoint{x, y}:', text)
        self.assertIn('pycls_Point_total(p)', text)

    def test_class_mutation_requires_runtime_fallback(self):
        with self.assertRaises(TranslationError):
            self.output(EXTENDED_SMOKES[9][1])

    def test_recursive_user_function_requires_unsafe_optin(self):
        with self.assertRaisesRegex(TranslationError, 'recursion'):
            self.output(EXTENDED_SMOKES[6][1])
        self.assertIn('@unsafe def fact', self.output(EXTENDED_SMOKES[6][1], unsafe=True))

    def test_native_main_io_print(self):
        text = self.output('def main() -> None:\n    print("Hello!")\n    print(3)\n    return None\n')
        self.assertIn('def main() -> IO(Unit):', text)
        self.assertIn('IO.print("Hello!")', text)
        self.assertIn('IO.print(U32.show(3))', text)

    def test_unknown_effect_fails(self):
        with self.assertRaises(TranslationError):
            self.output('import os\ndef main() -> int:\n    return 1\n')

    def test_native_rejects_break_and_continue(self):
        code = ('def f() -> int:\n    total = 0\n    for i in range(5):\n'
                '        if i > 3:\n            break\n        total += i\n    return total\n')
        with self.assertRaises(TranslationError):
            self.output(code)


class CompatBackendContract(unittest.TestCase):
    def test_arbitrary_python_program_is_preserved_not_executed_on_translate(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            sentinel = folder / 'should_not_exist'
            code = f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('bad')\n"
            bend, source = emit_compat(code, 'test.py', folder / 'test.bend')
            self.assertFalse(sentinel.exists())
            self.assertEqual(source.read_text(), code)
            text = bend.read_text()
            self.assertIn('Process.run(', text)
            self.assertIn('IO.exit(status)', text)
            self.assertIn('PyCompat.write_err(stderr)', text)
            self.assertIn('CPython executes source', text)
            self.assertTrue((folder / 'py3bend2_write_err.c').exists())
            self.assertTrue((folder / 'py3bend2_write_err.js').exists())
            self.assertIn('io_out(stderr, bytes, n)', (folder / 'py3bend2_write_err.c').read_text())

    def test_python_sidecar_matches_original_process_stdout_stderr_and_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            original = folder / 'original.py'
            original.write_text('import sys\nprint(2 ** 100)\nsys.stderr.write("an error")\nsys.exit(7)\n')
            _, source = emit_compat(original.read_text(), str(original), folder / 'test.bend',
                                    interpreter=sys.executable)
            a = subprocess.run([sys.executable, str(original)], capture_output=True, text=True)
            b = subprocess.run([sys.executable, str(source)], capture_output=True, text=True)
            self.assertEqual((a.stdout, a.stderr, a.returncode), (b.stdout, b.stderr, b.returncode))

    def test_auto_detects_integer_precision_mismatch(self):
        self.assertIn('different precision', auto_semantic_risk(
            'def main() -> int:\n    return 2 ** 80\n', 'case.py'))
        self.assertIn('different precision', auto_semantic_risk(
            'def main():\n    return 1 - 2\n', 'case.py'))

    def test_auto_routes_big_integer_to_compatibility_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'large.py'
            target = Path(directory) / 'large.bend'
            source.write_text('def main() -> int:\n    return 2 ** 80\n')
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = main(['translate', str(source), '-o', str(target)])
            self.assertEqual(code, 0)
            self.assertIn('CPython compatibility', buf.getvalue())
            self.assertIn('Process.run(', target.read_text())
            self.assertTrue(target.with_name('large.python_compat.py').exists())

    def test_invalid_python_rejected_during_build(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SyntaxError):
                emit_compat('def ???', 'bad.py', Path(directory) / 'out.bend')


if __name__ == '__main__':
    unittest.main()
