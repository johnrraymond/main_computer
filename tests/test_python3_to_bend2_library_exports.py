"""Closed-library linking and real-source differential contracts."""
import ast
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.python3_to_bend2 import translate, TranslationError
from tools.python3_to_bend2.coolify import (
    audit_exports, compare_export, export_oracle, _ERROR_MARKER,
)
from tools.python3_to_bend2.library_exports import (
    EXPORTS, programs, _lower_globals, _constant, Fixture,
)
from tools.python3_to_bend2.native_execution import Preflight

ROOT = Path(__file__).resolve().parents[1]


def invoke(source):
    # Only called on the pinned/reviewed bundle programs.
    ns = {}
    exec(compile(source, '<reviewed export>', 'exec'), ns)
    try:
        return ('value', ns['main']())
    except Exception as exc:
        return ('error', type(exc).__name__, str(exc))


class LibraryExportTests(unittest.TestCase):
    def test_original_and_lowered_programs_agree(self):
        for name, export in EXPORTS.items():
            for i, fixture in enumerate(export.fixtures):
                with self.subTest(name=name, fixture=i):
                    original, lowered = programs(ROOT, export, fixture)
                    result = invoke(original)
                    self.assertEqual(result, invoke(lowered))
                    if fixture.error:
                        self.assertEqual(result[:2], ('error', fixture.error))
                    else:
                        self.assertEqual(result[0], 'value')

    def test_export_bundles_emit(self):
        results = list(audit_exports(ROOT))
        self.assertEqual(len(results), sum(len(e.fixtures) for e in EXPORTS.values()))
        self.assertTrue(all(r[2] == 'EMITTED' for r in results), results)

    def test_cross_module_link_preserves_actual_functions(self):
        export = EXPORTS['bridge_normalize_evm_address']
        original, lowered = programs(ROOT, export, export.fixtures[0])
        self.assertIn('def normalize_address(value: str)', original)
        self.assertIn('def normalize_evm_address(value: Any', original)
        self.assertIn('def normalize_evm_address(value: str', lowered)
        self.assertIn('_HEX_CHARS = set(', original)
        self.assertNotIn('_HEX_CHARS', lowered)
        native = translate(lowered, show_return=True)
        self.assertIn('def PyStr.all_chars(', native)
        self.assertIn('def PyStr.all_chars_step(', native)
        self.assertIn('py3_normalize_address(', native)
        self.assertIn('py3_normalize_evm_address(', native)

    def test_changed_cross_module_import_rejected(self):
        export = EXPORTS['bridge_normalize_evm_address']
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for path in {f.path for f in export.fragments}:
                dest = root / path
                dest.parent.mkdir(parents=True, exist_ok=True)
                text = (ROOT / path).read_text(encoding='utf-8')
                text = text.replace('from main_computer.hub_credit_models import normalize_address, positive_int',
                                    'from other_module import normalize_address, positive_int')
                dest.write_text(text, encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'cross-module import changed'):
                programs(root, export, export.fixtures[0])

    def test_changed_export_rejected(self):
        export = EXPORTS['canonical_contract_key']
        path = export.fragments[0].path
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dest = root / path
            dest.parent.mkdir(parents=True)
            dest.write_text((ROOT / path).read_text(encoding='utf-8').replace(
                '"alpha_beta_lockout": "alpha-beta-lockout"', '"alpha_beta_lockout": "different"'), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'source changed'):
                programs(root, export, export.fixtures[0])

    def test_non_ascii_fixture_rejected(self):
        export = EXPORTS['canonical_contract_key']
        with self.assertRaisesRegex(ValueError, 'ASCII'):
            programs(ROOT, export, Fixture(('é',)))

    def test_escaping_or_mutating_globals_rejected(self):
        constant = _constant(ast.parse('{"a": "b"}', mode='eval').body)
        for body in ('return TABLE', 'TABLE["a"] = "c"', 'TABLE = {}', 'return TABLE.pop("a")'):
            with self.subTest(body=body):
                node = ast.parse('def f():\n    '+body).body[0]
                with self.assertRaises(ValueError):
                    _lower_globals(node, {'TABLE': constant})

    def test_quantifier_requires_string_iterable(self):
        with self.assertRaises(TranslationError):
            translate('def main() -> bool:\n    seq = [1, 2]\n    return all(ch in "ab" for ch in seq)\n')

    def test_any_not_in_uses_short_circuit_native_helper(self):
        code = translate('def main() -> bool:\n    seq = "abc"\n    return any(ch not in {"a", "b"} for ch in seq)\n')
        self.assertIn('Bool.not(PyStr.all_chars(', code)
        self.assertIn('PyStr.all_chars_step(', code)

    def test_literal_string_set_checks_whole_string(self):
        code = translate('def main() -> bool:\n    val = "ab"\n    return val in {"a", "abc"}\n')
        self.assertIn('PyStr.equal(PyExc.message(', code)
        self.assertNotIn('def PyStr.all_chars(', code)

    def test_error_comparison_checks_exact_error_and_status(self):
        oracle = subprocess.CompletedProcess([], 1, '', _ERROR_MARKER+'ValueError: bad\n')
        def run(rc, err, out=''):
            return subprocess.CompletedProcess([], rc, out, err)
        self.assertEqual(compare_export(oracle, run(1, 'Error: ValueError: bad\n'), 'ValueError'), 'VERIFIED')
        for obs in (run(0, 'ValueError: bad'), run(1, 'ValueError: wrong'),
                    run(1, 'TypeError: bad'), run(1, 'ValueError: bad', 'unexpected')):
            self.assertEqual(compare_export(oracle, obs, 'ValueError'), 'DIFFERENTIAL_FAILED')
        self.assertEqual(compare_export(run(0, '', 'value\n'), run(0, '', 'value\n'), 'ValueError'), 'ORACLE_FAILED')

    def test_native_preflight_blocked(self):
        with patch('tools.python3_to_bend2.coolify.preflight', return_value=Preflight(False, 'missing')):
            results = list(audit_exports(ROOT, native=True, selected='bridge_hex_validation'))
        self.assertEqual(len(results), 10)
        self.assertTrue(all(r[2] == 'PREFLIGHT_BLOCKED' for r in results))

    def test_expected_exception_oracle(self):
        export = EXPORTS['bridge_normalize_bytes32']
        fixture = next(f for f in export.fixtures if f.error)
        original, _ = programs(ROOT, export, fixture)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'case.py'
            path.write_text(original, encoding='utf-8')
            result = export_oracle(path, fixture.error)
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stderr.startswith(_ERROR_MARKER+'ValueError: '))


if __name__ == '__main__':
    unittest.main()
