"""Regressions for observed affine reuse failures and adjacent runtime sites.

These tests inspect emitted binder declarations, not Bend compiler acceptance.
"""
from contextlib import redirect_stdout
from io import StringIO
import re
import unittest
from unittest.mock import patch

from tools.python3_to_bend2 import translate
from tools.python3_to_bend2.__main__ import main
from tools.python3_to_bend2.coverage import run_probes
from tools.python3_to_bend2.feature_cases import PROBES


class OwnershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = translate(PROBES['1_integer_arithmetic'], show_return=True)

    def definition(self, name):
        match = re.search(r'(?m)^(?:@unsafe )?def ' + re.escape(name) + r'\(', self.source)
        self.assertIsNotNone(match, name)
        rest = self.source[match.start():]
        return re.split(r'\n(?=(?:@unsafe )?def |type )', rest, maxsplit=1)[0]

    def test_integer_reused_digits(self):
        self.assertIn('(+d: U32,', self.definition('PyMag.finish_norm'))
        self.assertIn('q: Nat, +r: U32', self.definition('PyMag.from_nat_case'))

    def test_heap_and_dictionary_reused_parameters_and_fields(self):
        for name in ('PyCell.lookup', 'PyCell.replace', 'PyHeap.list_append_at',
                     'PyHeap.list_set_at', 'PyHeap.set_field_at', 'PyHeap.dict_set_at',
                     'PyHeap.del_field_at'):
            with self.subTest(name=name):
                self.assertIn('+address: Nat', self.definition(name))
        self.assertIn('Cell{+key, old}', self.definition('PyCell.replace'))
        self.assertIn('KV{+oldkey, oldval}', self.definition('PyDict.set'))

    def test_gc_reused_parameters_and_fields(self):
        checks = {'PyGC.mark_address_checked': '+address: Nat',
                  'PyGC.sweep': 'Cell{+address, object}',
                  'PyGC.weak_cells': '+marks: List<&2, Nat>',
                  'PyGC.retain_finalized': 'Con{+address, rest}',
                  'PyHeap.sweep_after_finalizers': '+marks: List<&2, Nat>',
                  'PyGC.clear_weaks': 'WeakEntry{+wid, +target, handler, alive}',
                  'PyGC.find_finalizers': 'Cell{+address, object}'}
        for name, expected in checks.items():
            with self.subTest(name=name):
                self.assertIn(expected, self.definition(name))

    def test_collections_reused_data(self):
        self.assertIn('OList{+values}', self.definition('PyObject.list_item_index'))
        self.assertIn('+h: PyHeap', self.definition('PyHeap.list_slice'))
        self.assertIn('Chr{+chr}', self.definition('PyStr.trim_lead'))

    def test_single_probe_does_not_claim_full_coverage(self):
        with patch('tools.python3_to_bend2.__main__.run_probes', return_value=[
                dict(feature='1_integer_arithmetic', source='EMITTED', native='VERIFIED', detail='')]) as runner:
            with redirect_stdout(StringIO()) as out:
                self.assertEqual(main(['probe', '1_integer_arithmetic', '--wsl']), 0)
            runner.assert_called_once_with(check_bend=True, bend='bend', wsl=True,
                                           feature='1_integer_arithmetic')
            self.assertIn('SINGLE_PROBE_ONLY', out.getvalue())
            self.assertNotIn('NATIVE_READINESS PASS', out.getvalue())

    def test_single_probe_missing_compiler_fails(self):
        with patch('tools.python3_to_bend2.native_execution.shutil.which', return_value=None):
            with redirect_stdout(StringIO()) as out:
                self.assertEqual(main(['probe', '1_integer_arithmetic']), 2)
        self.assertIn('PREFLIGHT_BLOCKED', out.getvalue())

    def test_feature_selection_keeps_full_inventory_unchanged(self):
        results = run_probes(feature='1_integer_arithmetic')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['source'], 'EMITTED')
        self.assertEqual(len(PROBES), 70)


if __name__ == '__main__':
    unittest.main()
