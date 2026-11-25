"""Regress extended value dispatch and intentional-exception acceptance."""
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.python3_to_bend2 import native_execution as native
from tools.python3_to_bend2.coverage import _python_oracle, _CHAIN_MARKER, run_probes
from tools.python3_to_bend2.feature_cases import PROBES
from tools.python3_to_bend2.numeric import Compiler


FEATURE = '61_raise_from'
CHAIN = ('ValueError: bad\n'
         'The above exception was the direct cause of the following exception:\n'
         'RuntimeError: x\n')
POLICY = ('SOME PROOFS FAIL\nError: 2 defs rely on unsafe or foreign code:\n'
          'defs that depend on @unsafe: PyMag.add_step, py3_main\n')


def result(code, stdout='', stderr=''):
    return subprocess.CompletedProcess([], code, stdout, stderr)


class ValueVariantTests(unittest.TestCase):
    def test_exception_message_matches_every_value_constructor(self):
        for feature in ('1_integer_arithmetic', '3_float_arithmetic',
                        '4_complex_arithmetic', FEATURE):
            with self.subTest(feature=feature):
                source = Compiler(PROBES[feature], feature, show_return=True).compile()
                constructors = source.split('type PyValue is Data:\n', 1)[1].split('\n\n', 1)[0]
                names = set(re.findall(r'^  (V\w+)\{', constructors, re.M))
                body = source.split('def PyExc.message_only(', 1)[1].split('\n\n', 1)[0]
                cases = set(re.findall(r'^    case (V\w+)\{', body, re.M))
                self.assertTrue(names)
                self.assertEqual(names, cases)
                if feature == FEATURE:
                    self.assertIn('case VChainedError{message, cause}:\n      PyExc.after_colon(message)', body)
                elif feature in ('3_float_arithmetic', '4_complex_arithmetic'):
                    self.assertIn('case VFloat{x}:\n      PyNum.show_signed(x, ".0")', body)
                    self.assertIn('case VComplex{r, i}:\n      PyNum.show_complex(r, i)', body)

    def probe(self, observed):
        with patch.object(native, 'preflight', return_value=native.Preflight(True, 'bend 2.0.40')), \
             patch.object(native, 'run_standalone', side_effect=[result(1, stderr=POLICY), observed]) as runner:
            row, = run_probes(check_bend=True, feature=FEATURE)
        self.assertEqual(runner.call_count, 2)
        return row

    def test_real_python_oracle_emits_expected_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'probe.py')
            path.write_text(PROBES[FEATURE], encoding='utf-8')
            oracle = _python_oracle(path, expect_chain=True)
        self.assertEqual(oracle.returncode, 1)
        self.assertEqual(oracle.stdout, '')
        self.assertEqual(oracle.stderr, _CHAIN_MARKER + CHAIN)

    def test_exact_expected_exception_passes(self):
        for prefix in ('', 'Error: '):
            with self.subTest(prefix=prefix):
                self.assertEqual(self.probe(result(1, stderr=prefix + CHAIN))['native'], 'VERIFIED')

    def test_wrong_cause_message_exit_or_output_fails(self):
        for observed in (
            result(1, stderr=CHAIN.replace('ValueError', 'TypeError')),
            result(1, stderr=CHAIN.replace('RuntimeError: x', 'RuntimeError: wrong')),
            result(1, stderr='RuntimeError: x\n'),
            result(0, stderr=CHAIN),
            result(2, stderr=CHAIN),
            result(1, stdout='unexpected\n', stderr=CHAIN),
            result(1, stderr='Compiler failure\n' + CHAIN),
            result(1, stderr=CHAIN + 'unexpected\n'),
        ):
            with self.subTest(observed=observed):
                self.assertEqual(self.probe(observed)['native'], 'DIFFERENTIAL_FAILED')

    def test_unexpected_python_failure_is_not_a_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'probe.py')
            for source in ('def main(): raise ValueError("x")',
                           'raise ValueError("import failed")',
                           'def main(): return 3'):
                with self.subTest(source=source):
                    path.write_text(source, encoding='utf-8')
                    oracle = _python_oracle(path, expect_chain=True)
                    self.assertFalse(oracle.stderr.startswith(_CHAIN_MARKER))


if __name__ == '__main__':
    unittest.main()
