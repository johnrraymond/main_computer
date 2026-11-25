"""Regression tests for compiler diagnostics from the native feature smoke."""
from __future__ import annotations

import re
import unittest

from tools.python3_to_bend2 import translate
from tools.python3_to_bend2.feature_cases import PROBES
from tools.python3_to_bend2.pair_lowering import lower_computed_pairs


class PairLoweringTests(unittest.TestCase):
    def test_single_evaluation_and_live_capture(self):
        source = '''import Base

def py3_main(+heap: PyHeap, +live: PyValue, +dead: PyValue) -> PyHeap & PyValue:
  (py5_from_pair_heap_1, py5_from_pair_2) = PyHeap.new_list(heap, [live])
  +py5_heap_3 : PyHeap = py5_from_pair_heap_1
  +py5_val_4 : PyValue = py5_from_pair_2
  (py5_heap_3, PyValue.add(live, py5_val_4))
'''
        output = lower_computed_pairs(source)
        self.assertEqual(output.count('PyHeap.new_list(heap, [live])'), 1)
        self.assertIn('def py_pair_cont_1(py_cont_pair: PyHeap & PyValue, +live: PyValue)', output)
        self.assertIn('py_pair_cont_1(PyHeap.new_list(heap, [live]), live)', output)
        self.assertIn('(py5_from_pair_heap_1, py5_from_pair_2) = py_cont_pair', output)
        self.assertEqual(lower_computed_pairs(output), output)

    def test_branch_capture_and_multiple_pairs(self):
        source = '''import Base

def py3_main(flag: Bool, +heap: PyHeap, +live: PyValue) -> PyHeap & PyValue:
  match flag:
    case True{}:
      +local : PyValue = live
      (py5_from_pair_heap_1, py5_from_pair_2) = PyHeap.new_list(heap, [local])
      +first : PyHeap = py5_from_pair_heap_1
      +value : PyValue = py5_from_pair_2
      (py5_from_pair_heap_3, py5_from_pair_4) = PyHeap.new_list(first, [value])
      (py5_from_pair_heap_3, PyValue.add(local, py5_from_pair_4))
    case False{}:
      (py5_from_pair_heap_5, py5_from_pair_6) = PyHeap.new_list(heap, [live])
      (py5_from_pair_heap_5, PyValue.add(live, py5_from_pair_6))
'''
        output = lower_computed_pairs(source)
        self.assertEqual(output.count('PyHeap.new_list('), 3)
        helpers = re.findall(r'^def py_pair_cont_\d+\((.*)\) ->', output, re.M)
        self.assertEqual(len(helpers), 3)
        self.assertEqual(sum('+local: PyValue' in h for h in helpers), 2)
        self.assertEqual(sum('+live: PyValue' in h for h in helpers), 1)
        self.assertNotRegex(output, r'(?m)^\s+\([^\n]*\) = PyHeap.new_list')

    def test_observed_diagnostics_are_removed(self):
        for key in ('1_integer_arithmetic', '3_float_arithmetic', '4_complex_arithmetic',
                    '5_boolean_short_circuit', '8_for_list', '26_str_methods',
                    '35_class_attributes', '51_pathlib_import', '65_slicing_text',
                    '69_recursive_fib'):
            with self.subTest(key=key):
                output = translate(PROBES[key], show_return=True)
                self.assertNotRegex(output, r'(?m)^\s+match [^:\n]*\(')
                self.assertNotRegex(output, r'(?m)^\s+\([^\n]*\) = [\w.]+\(')
                self.assertNotRegex(output, r'(?m)^\s+chr \+ 32$')
                # Strip string contents: parentheses in literal messages are
                # not Bend syntax. Check the emitted string-length expression.
                for line in output.splitlines():
                    if 'VInt{PyInt.positive(PyMag.from_nat(String.length(PyExc.message(' in line:
                        self.assertEqual(line.count('('), line.count(')'))
                if key in ('3_float_arithmetic', '4_complex_arithmetic'):
                    self.assertIn('SoftNum.NumError', output)
                    self.assertNotRegex(output, r'(?m)^\s+\+(?:is_finished|is_integral|pure_imag|negative|converted)\s*=')


if __name__ == '__main__':
    unittest.main()
