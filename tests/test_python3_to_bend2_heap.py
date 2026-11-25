"""v4 source contracts. These are NOT Bend execution tests.

The CLI's --require-bend path is the only runtime/differential certificate.
"""
from __future__ import annotations

import ast
from pathlib import Path
import re
import shutil
import tempfile
import unittest

from tools.python3_to_bend2.syntax import TranslationError
from tools.python3_to_bend2.heap import HEAP_BEND, SMOKE_CASES, HeapTranslator, translate_heap, main


class NativeHeapSourceTests(unittest.TestCase):
    def test_all_source_smoke(self):
        for name, source in SMOKE_CASES.items():
            with self.subTest(name=name):
                out = translate_heap(source, name, show_return=True)
                self.assertIn('import Base\n', out)
                self.assertIn('type PyHeap is Data:', out)
                self.assertIn('type PyValue is Data:', out)
                self.assertIn('PyHeap.finish(py3_main(PyHeap.empty()), True{})', out)
                self.assertNotIn('Process.run(', out)
                self.assertNotIn('python_compat.py', out)

    def test_heap_alias_address_is_shared(self):
        out = translate_heap(SMOKE_CASES['shared_list'])
        self.assertIn('PyHeap.new_list(', out)
        self.assertIn('PyHeap.list_append(', out)
        self.assertIn('py3_mutate(', out)
        self.assertNotIn('List.append(&2, PyValue, py4_', out)
        self.assertIn('PyHeap.list_item(', out)

    def test_method_mutates_by_stable_heap_reference(self):
        out = translate_heap(SMOKE_CASES['class_alias'])
        self.assertIn('def py3_Box_inc(', out)
        self.assertIn('PyHeap.set_field(', out)
        self.assertIn('PyHeap.get_field(', out)
        self.assertIn('PyHeap.new_record(', out)
        self.assertNotIn('copy-as-reference', out)

    def test_mutations_cross_function_boundary(self):
        out = translate_heap(SMOKE_CASES['cross_function'])
        self.assertRegex(out, r'def py3_add\(\+heap: PyHeap, \+xs: PyValue, \+n: PyValue\)')
        self.assertIn('py3_add(', out)
        self.assertIn('PyHeap.list_length(', out)

    def test_branch_mutations_share_heap(self):
        out = translate_heap(SMOKE_CASES['branch_alias'])
        self.assertIn('match py4_flag_', out)
        self.assertGreaterEqual(out.count('PyHeap.list_append('), 3)
        self.assertIn('case False{}:', out)

    def test_while_has_safe_ownership_but_explicit_unsafe_recursion(self):
        out = translate_heap(SMOKE_CASES['while_mutation'])
        self.assertRegex(out, r'@unsafe def py4_while_test_\d+\(\+py4_heap_')
        self.assertIn('PyValue.truth(', out)
        self.assertIn('PyHeap.list_append(', out)

    def test_range_negative_step(self):
        out = translate_heap('def main() -> int:\n    n = 0\n    for i in range(5, 0, -2):\n        n += i\n    return n\n')
        self.assertIn('PyValue.gt(', out)
        self.assertIn('PyValue.add(', out)

    def test_while_break_and_continue(self):
        source = '''def main() -> int:
    x = 0
    while x < 9:
        x += 1
        if x == 2:
            continue
        if x == 4:
            break
    return x
'''
        out = translate_heap(source)
        self.assertIn('PyValue.eq(', out)
        self.assertIn('PyValue.lt(', out)
        self.assertIn('@unsafe def py4_while_', out)

    def test_big_integer_arithmetic(self):
        source = 'def main() -> int:\n    return (-2 ** 144) * (7 ** 5) // 9 + 123456789987654321\n'
        out = translate_heap(source)
        for operation in ('pow', 'mul', 'floordiv', 'add', 'neg'):
            self.assertIn(f'PyValue.{operation}(', out)
        self.assertIn('type PyInt is Data:', out)

    def test_annotated_empty_list(self):
        source = '''def main() -> int:
    x: list[int] = []
    x.append(10)
    return x[0]
'''
        self.assertIn('PyHeap.list_item(', translate_heap(source))

    def test_list_element_type_mismatch_refused(self):
        source = '''def main() -> int:
    x: list[int] = []
    x.append("not an integer")
    return 0
'''
        with self.assertRaisesRegex(TranslationError, 'homogeneous list type'):
            translate_heap(source)

    def test_general_dynamic_python_remains_rejected(self):
        unsupported = {
            'dynamic_import': 'import math\ndef main() -> int:\n    return math.factorial(5)\n',
            'inheritance': 'class Base: pass\nclass Child(Base): pass\ndef main() -> int:\n    return 1\n',
            'exceptions': 'def main() -> int:\n    try:\n        return 1\n    except ValueError:\n        return 2\n',
            'generator': 'def main() -> int:\n    x = (i for i in [1,2])\n    return 0\n',
            'dictionary': 'def main() -> int:\n    x = {"a": 1}\n    return 0\n',
            'set': 'def main() -> int:\n    x = {1, 2}\n    return 0\n',
            'division_by_zero': 'def main() -> int:\n    return 4 // 0\n',
            'unknown_denominator': 'def f(n: int) -> int:\n    return 5 // n\ndef main() -> int:\n    return f(2)\n',
            'floating': 'def main() -> float:\n    return 1.5\n',
            'async': 'async def main() -> int:\n    return 1\n',
            'with': 'def main() -> int:\n    with open("test") as fh:\n        return 1\n',
        }
        for label, source in unsupported.items():
            with self.subTest(label=label):
                with self.assertRaises(TranslationError):
                    translate_heap(source, label)

    def test_empty_list_truth_dispatches_through_heap(self):
        code = translate_heap(SMOKE_CASES['empty_list_truth'])
        self.assertIn('PyObject.truth(', code)
        self.assertIn('PyHeap.truth(', code)
        self.assertIn('List.is_empty(&2, PyValue, values)', code)

    def test_different_list_equality_is_not_identity(self):
        source = 'def main() -> bool:\n    return [1] == [1]\n'
        with self.assertRaisesRegex(TranslationError, 'list == requires recursive value equality'):
            translate_heap(source)

    def test_unsupported_object_print_fails_closed(self):
        source = 'def main() -> None:\n    xs = [1]\n    print(xs)\n'
        with self.assertRaisesRegex(TranslationError, 'object repr/print'):
            translate_heap(source)

    def test_non_dataclass_has_no_implicit_constructor(self):
        source = 'class Point:\n    x: int\ndef main() -> int:\n    p = Point(3)\n    return p.x\n'
        with self.assertRaisesRegex(TranslationError, 'constructor missing'):
            translate_heap(source)

    def test_return_annotation_mismatch_refused(self):
        source = 'def main() -> int:\n    return "wrong"\n'
        with self.assertRaisesRegex(TranslationError, 'return value type'):
            translate_heap(source)

    def test_import_process_run_absent(self):
        out = translate_heap('def main() -> int:\n    return 1\n')
        self.assertNotIn('import "./effs', out)
        self.assertNotIn('CPython', out)
        self.assertNotIn('Process.run(', out)

    def test_compiler_gate_blocks_no_bend(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'sample.py'
            path.write_text('def main() -> int:\n    return 12\n')
            out = Path(td) / 'sample.bend'
            self.assertEqual(2, main(['translate', str(path), '-o', str(out),
                                      '--require-bend', '--bend', 'missing-bend2-bin']))
            self.assertTrue(out.is_file())

    def test_no_bend_smoke_does_not_certify(self):
        self.assertEqual(2, main(['smoke', '--require-bend', '--bend', 'missing-bend2-bin']))

    def test_code_deterministic(self):
        src = SMOKE_CASES['for_range']
        self.assertEqual(translate_heap(src), translate_heap(src))


if __name__ == '__main__':
    unittest.main()
