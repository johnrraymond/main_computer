"""Proof verdicts cannot substitute for actual differential execution."""
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from tools.python3_to_bend2 import native_execution as native
from tools.python3_to_bend2.coverage import run_probes
from tools.python3_to_bend2.feature_cases import PROBES


FEATURE = '1_integer_arithmetic'
POLICY = ('SOME PROOFS FAIL\nError: 2 defs rely on unsafe or foreign code:\n'
          'defs that depend on @unsafe: PyMag.add_step, py3_main\n')


def result(code, stdout='', stderr=''):
    return subprocess.CompletedProcess([], code, stdout, stderr)


class ProofPolicyTests(unittest.TestCase):
    def test_check_only_pass_is_not_kernel_proof(self):
        proof = native.classify_proof_check(result(0, 'ALL PROOFS CHECK\nUse --verdict for mathematical validity.\n'))
        self.assertEqual(proof.status, 'CHECKED')
        self.assertTrue(proof.permits_execution)

    def test_exact_unsafe_verdict_can_proceed_to_execution(self):
        proof = native.classify_proof_check(result(1, stderr=POLICY))
        self.assertEqual(proof.status, 'UNAVAILABLE_UNSAFE_OR_FOREIGN')
        self.assertTrue(proof.permits_execution)
        self.assertIn('2 definitions', proof.detail)

    def test_singular_and_foreign_verdicts(self):
        diagnostic = ('SOME PROOFS FAIL\nError: 1 def relies on unsafe or foreign code:\n'
                      'defs that depend on foreign effects: py3_main\n')
        self.assertTrue(native.classify_proof_check(result(1, stderr=diagnostic)).permits_execution)

    def test_other_errors_never_permit_execution(self):
        for verdict in (
            result(1, stderr='SOME PROOFS FAIL\nError: expected Nat observed String'),
            result(1, stderr=POLICY + 'Error: affine variable consumed twice'),
            result(1, stdout='ALL PROOFS CHECK'),
            result(0, stderr=POLICY),
            result(2, stderr=POLICY),
            result(0, stdout='unknown verdict'),
            result(1, stderr='SOME PROOFS FAIL'),
        ):
            with self.subTest(verdict=verdict):
                self.assertFalse(native.classify_proof_check(verdict).permits_execution)

    def probe(self, check, run=None):
        with patch.object(native, 'preflight', return_value=native.Preflight(True, 'bend 2.0.40')), patch.object(native, 'run_standalone', side_effect=[check] if run is None else [check, run]) as runner:
            observed, = run_probes(check_bend=True, feature=FEATURE)
        return observed, runner

    def test_policy_rejection_requires_real_run_and_oracle(self):
        # Independent expected output from the fixed Python probe. Bend runner
        # is mocked here: this tests acceptance control flow, not Bend itself.
        env = {}
        exec(compile(PROBES[FEATURE], FEATURE, 'exec'), env)
        expected = str(env['main']()) + '\n'
        observed, runner = self.probe(result(1, stderr=POLICY), result(0, expected))
        self.assertEqual(observed['native'], 'VERIFIED')
        self.assertEqual(observed['proof'], 'UNAVAILABLE_UNSAFE_OR_FOREIGN')
        self.assertEqual(runner.call_count, 2)
        self.assertTrue(runner.call_args_list[0].kwargs['check_only'])
        self.assertFalse(runner.call_args_list[1].kwargs.get('check_only', False))

    def test_policy_rejection_does_not_hide_wrong_output(self):
        observed, _ = self.probe(result(1, stderr=POLICY), result(0, 'wrong output\n'))
        self.assertEqual(observed['native'], 'DIFFERENTIAL_FAILED')

    def test_run_failure_preserves_compiler_diagnostics(self):
        diagnostic = 'Error: ' + 'x' * 900 + '\nLocation: PyHeap.truth'
        observed, _ = self.probe(result(1, stderr=POLICY), result(1, stderr=diagnostic))
        self.assertEqual(observed['native'], 'RUN_FAILED')
        self.assertIn('Location: PyHeap.truth', observed['detail'])

    def test_type_failure_does_not_start_run(self):
        observed, runner = self.probe(result(1, stderr='SOME PROOFS FAIL\nError: type mismatch'))
        self.assertEqual(observed['native'], 'CHECK_FAILED')
        runner.assert_called_once()


if __name__ == '__main__':
    unittest.main()
