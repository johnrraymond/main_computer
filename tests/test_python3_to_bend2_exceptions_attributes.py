"""v10 tests: compilation claims and native Bend execution are separate gates.

Source checks cannot certify Bend executable equivalence. The independent
Python model here checks semantics of the *exception rewrite only*; the
strict Bend gate remains required for native runtime certification.
"""
import ast
import subprocess
import random
import sys
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.python3_to_bend2.syntax import TranslationError
from tools.python3_to_bend2.exceptions_attributes import (
    ExceptionAttributeLowerer, NativeRuntimePass, V10_CASES, run_smoke,
)
from tools.python3_to_bend2.declaration_order import reorder_bend_declarations


class ModelError:
    def __init__(self, kind, message):
        self.kind = kind
        self.message = message


def transformed_python_result(source):
    transformed = NativeRuntimePass('model.py').apply(source)
    ns = {
        '__py10_raise': lambda kind, msg: ModelError(kind, str(msg)),
        '__py10_rethrow': lambda error: error,
        '__py10_is_error': lambda value: isinstance(value, ModelError),
        '__py10_is_exception': lambda value, kind: isinstance(value, ModelError) and value.kind == kind,
    }
    exec(compile(transformed, '<lowered-python>', 'exec'), ns, ns)
    return ns['main']()


class ExceptionAttributeSourceTests(unittest.TestCase):
    def test_all_v10_smokes_generate(self):
        for name, source in V10_CASES.items():
            with self.subTest(name=name):
                bend = ExceptionAttributeLowerer(source, name, show_return=True).compile()
                self.assertIn('def main() -> IO(Unit):', bend)
                self.assertNotIn('Process.run(', bend)
                self.assertNotIn('python_compat', bend)
                self.assertIn('type PyHeap is Data:', bend)
                self.assertEqual(bend.count('def main() -> IO(Unit):'), 1)

    def test_compiler_gate_refuses_missing_bend(self):
        self.assertEqual(run_smoke(bend='__missing_bend_binary__'), 2)

    def test_protocol_specific_helpers(self):
        src = V10_CASES['attr_alias']
        code = ExceptionAttributeLowerer(src, 'attrs').compile()
        self.assertIn('def PyHeap.has_field(', code)
        self.assertIn('PyDict.contains(fields, field)', code)
        self.assertIn('ORecord{tag, PyDict.delete(fields, field)}', code)
        self.assertIn('PyHeap.set_field(', code)
        self.assertIn('PyHeap.get_field(', code)

    def test_shared_runtime_record_storage_is_consistent(self):
        # The integer-only probe still links the shared object runtime, so
        # this catches the actual reported compiler failure even without attrs.
        from tools.python3_to_bend2 import translate
        from tools.python3_to_bend2.feature_cases import PROBES
        for name, source in (
            ('integer', PROBES['1_integer_arithmetic']),
            ('attribute_alias', V10_CASES['attr_alias']),
            ('attribute_delete', V10_CASES['attr_delete']),
        ):
            with self.subTest(name=name):
                code = translate(source, show_return=True)
                self.assertIn('ORecord{tag: String, fields: List<&2, PyKV>}', code)
                self.assertIn('PyDict.get(fields, field, VError{"AttributeError"})', code)
                self.assertIn('ORecord{tag, PyDict.set(fields, field, value)}', code)
                self.assertIn('PyDict.contains(fields, field)', code)
                self.assertIn('ORecord{tag, PyDict.delete(fields, field)}', code)
                self.assertNotIn('Map<&2, PyValue>', code)
                self.assertNotRegex(code, r'\bMap\.(?:get|set|has|del|new)\(')
                self.assertIn('def PyDict.delete(items: List<&2, PyKV>, +key: String)', code)
                self.assertIn('case Con{KV{+oldkey, value}, rest}:\n'
                              '      PyDict.delete_step(PyStr.equal(oldkey, key), oldkey, value, rest, key)', code)
                self.assertIn('case True{}:\n      rest\n    case False{}:\n'
                              '      KV{oldkey, value} <> PyDict.delete(rest, key)', code)

    def test_exception_kind_and_unwind(self):
        for name in ('raise_catch', 'raise_propagates', 'raise_type_mismatch'):
            with self.subTest(name=name):
                original = V10_CASES[name]
                ns = {}
                exec(compile(original, '<original>', 'exec'), ns, ns)
                self.assertEqual(transformed_python_result(original), ns['main']())
                bend = ExceptionAttributeLowerer(original, name, show_return=True).compile()
                self.assertIn('PyExc.is_error(', bend)
                self.assertIn('PyExc.is_kind(', bend)
                self.assertIn('VError{', bend)

    def test_100_randomized_exception_boundaries(self):
        rnd = random.Random(4192)
        for i in range(100):
            incoming = rnd.randint(-1000, 1000)
            fallback = rnd.randint(-10_000, 10_000)
            increment = rnd.randint(-200, 200)
            src = f"""def risky(n: int) -> int:
    if n < 0:
        raise ValueError('negative')
    return n + ({increment})
def main() -> int:
    try:
        result = risky({incoming})
    except ValueError:
        result = {fallback}
    return result
"""
            with self.subTest(index=i, n=incoming):
                ns = {}
                exec(compile(src, '<original>', 'exec'), ns, ns)
                self.assertEqual(transformed_python_result(src), ns['main']())
                if i < 8:
                    self.assertIn('PyExc.is_kind(', ExceptionAttributeLowerer(src, f'random_{i}').compile())

    def test_math_source_is_folded_and_operator_lowered(self):
        src = V10_CASES['stdlib_math']
        rewritten = NativeRuntimePass('math.py').apply(src)
        self.assertNotIn('import math', rewritten)
        self.assertNotIn('import operator', rewritten)
        self.assertIn('720', rewritten)
        self.assertIn('9', rewritten)
        self.assertIn('6', rewritten)
        bend = ExceptionAttributeLowerer(src, 'math').compile()
        self.assertIn('PyValue.add(', bend)

    def test_exact_bigint_abs(self):
        code = ExceptionAttributeLowerer(V10_CASES['abs_int'], 'abs').compile()
        self.assertIn('def PyValue.abs(', code)
        self.assertIn('PyNeg{digits}', code)

    def test_declarations_ordered(self):
        code = ExceptionAttributeLowerer(V10_CASES['raise_catch'], 'order').compile()
        self.assertLess(code.index('type PyValue is Data:'), code.index('def PyExc.is_error('))
        self.assertLess(code.index('def PyExc.is_error('), code.index('def py3_main('))
        self.assertLess(code.index('def py3_main('), code.index('def main() -> IO(Unit):'))

    def test_new_code_does_not_modify_old_v9_suite(self):
        code = ExceptionAttributeLowerer(V10_CASES['raise_catch'], 'preserve').compile()
        self.assertIn('PyGC.run_finalizers(', code)
        self.assertIn('def PyHeap.collect(', code)
        self.assertIn('def PyHeap.new_weak(', code)

    def test_declaration_linker_rejects_duplicates(self):
        with self.assertRaisesRegex(ValueError, 'duplicate declaration'):
            reorder_bend_declarations('def a() -> Nat:\n  1n\n\ndef a() -> Nat:\n  2n\n')


class TestFailClosed(unittest.TestCase):
    def rejected(self, program, message):
        with self.assertRaises(TranslationError) as got:
            ExceptionAttributeLowerer(textwrap.dedent(program), 'rejection').compile()
        self.assertIn(message, str(got.exception))

    def test_try_finally_rejected(self):
        self.rejected('''
            def fail(n: int) -> int:
                raise ValueError('bad')
            def main() -> int:
                try:
                    return fail(1)
                finally:
                    print(1)
        ''', 'try/finally')

    def test_try_multi_statement_rejected(self):
        self.rejected('''
            def fail(n: int) -> int:
                raise ValueError('bad')
            def main() -> int:
                try:
                    a = fail(1)
                    b = 9
                except ValueError:
                    a = 2
                return a
        ''', 'one direct')

    def test_throwing_function_not_swallowed(self):
        self.rejected('''
            def fail(n: int) -> int:
                raise ValueError('bad')
            def main() -> int:
                x = fail(1)
                return x + 1
        ''', 'potentially throwing')

    def test_throw_nested_expression_rejected(self):
        self.rejected('''
            def fail(n: int) -> int:
                raise ValueError('bad')
            def main() -> int:
                return 5 + fail(3)
        ''', 'potentially throwing')

    def test_raise_unbound_name_rejected(self):
        self.rejected('''
            def main() -> int:
                raise FileNotFoundError('bad')
        ''', 'exception class')

    def test_dynamic_protocol_override_rejected(self):
        self.rejected('''
            class X:
                def __getattr__(self, key: str) -> int:
                    return 1
            def main() -> int:
                x = X()
                return 0
        ''', 'protocol')

    def test_class_method_raise_rejected(self):
        self.rejected('''
            class X:
                def bad(self) -> int:
                    raise ValueError('bad')
            def main() -> int:
                return 0
        ''', 'instance methods')

    def test_field_retyping_rejected(self):
        self.rejected('''
            class Box:
                value: int
                def __init__(self, value: int) -> None:
                    self.value = value
            def main() -> int:
                box = Box(5)
                setattr(box, 'value', 'broken')
                return 0
        ''', 'setattr changes')

    def test_method_getattr_rejected(self):
        self.rejected('''
            class Box:
                value: int
                def __init__(self, value: int) -> None:
                    self.value = value
                def f(self) -> int:
                    return self.value
            def main() -> int:
                box = Box(1)
                print(getattr(box, 'f'))
                return 0
        ''', 'attribute requires literal')

    def test_special_getattr_rejected(self):
        self.rejected('''
            class Box:
                value: int
                def __init__(self, value: int) -> None:
                    self.value = value
            def main() -> int:
                box = Box(1)
                print(hasattr(box, '__class__'))
                return 0
        ''', 'attribute requires literal')

    def test_nonliteral_getattr_rejected(self):
        self.rejected('''
            class Box:
                value: int
                def __init__(self, value: int) -> None:
                    self.value = value
            def main() -> int:
                box = Box(1)
                key = 'value'
                return getattr(box, key)
        ''', 'attribute requires literal')

    def test_math_dynamic_args_rejected(self):
        self.rejected('''
            import math
            def main() -> int:
                n = 5
                return math.factorial(n)
        ''', 'compile-time integer')

    def test_unsupported_stdlib_function_rejected(self):
        self.rejected('''
            import math
            def main() -> int:
                return math.sin(4)
        ''', 'unsupported math.sin')

    def test_module_shadow_rejected(self):
        self.rejected('''
            import math
            def main() -> int:
                math = 3
                return math.factorial(4)
        ''', 'shadowed')

    def test_raise_unknown_exception_class_rejected(self):
        self.rejected('''
            class ValueError:
                pass
            def main() -> int:
                raise ValueError('x')
        ''', 'shadowed')

    def test_shadowed_exception_name_rejected(self):
        self.rejected("\n            def fail(ValueError: int) -> int:\n                raise ValueError('bad')\n            def main() -> int:\n                return fail(1)\n        ", 'exception name shadowed')

    def test_import_inside_function_rejected(self):
        self.rejected('''
            def main() -> int:
                import math
                return math.factorial(6)
        ''', 'import')


if __name__ == '__main__':
    unittest.main()
