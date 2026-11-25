"""v11 source lowering tests and differential Python AST models.

These tests are NOT Bend compiler or runtime acceptance; actual Bend must be
run using `python -m tools.python3_to_bend2.runtime_semantics smoke`.
"""
from __future__ import annotations

import contextlib
import io
import random
import textwrap
import unittest

from tools.python3_to_bend2.syntax import TranslationError
from tools.python3_to_bend2.runtime_semantics import (
    RuntimeLowerer, RuntimeSemanticPass, V11_CASES, run_smoke,
)


class ErrorValue:
    def __init__(self, kind, message):
        self.kind = kind
        self.message = message
    def __str__(self):
        return self.message


def python_result(source: str):
    namespace = {}
    with io.StringIO() as output, contextlib.redirect_stdout(output):
        exec(compile(source, '<original>', 'exec'), namespace, namespace)
        result = namespace['main']()
        return result, output.getvalue()


def lowered_python_result(source: str):
    pass_ = RuntimeSemanticPass('<model>')
    transformed = pass_.apply(source)
    def static_isinstance(value, cls):
        if isinstance(value, cls):
            return True
        current = type(value).__name__
        while current in pass_.parents:
            current = pass_.parents[current]
            if current == cls.__name__:
                return True
        return False
    namespace = {'isinstance': static_isinstance,
        '_py10_raise': lambda kind, message: ErrorValue(kind, str(message)),
        '_py10_rethrow': lambda error: error,
        '_py10_is_error': lambda error: isinstance(error, ErrorValue),
        '_py10_is_exception': lambda error, kind: isinstance(error, ErrorValue) and (
            error.kind == kind or kind == 'Exception'),
    }
    with io.StringIO() as output, contextlib.redirect_stdout(output):
        exec(compile(transformed, '<lowered>', 'exec'), namespace, namespace)
        result = namespace['main']()
        return result, output.getvalue()


class SourceTests(unittest.TestCase):
    def test_smoke_emit_all_cases(self):
        for name, source in V11_CASES.items():
            with self.subTest(name=name):
                text = RuntimeLowerer(source, name, show_return=True).compile()
                self.assertIn('def main() -> IO(Unit):', text)
                self.assertIn('type PyHeap is Data:', text)
                self.assertNotIn('Process.run(', text)
                self.assertNotIn('python_compat', text)

    def test_local_module_linker_remains_available(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from tools.python3_to_bend2.runtime_semantics import translate_runtime_file
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'helper.py').write_text('def calc(x: int) -> int:\n    return x + 3\n')
            main = root / 'entry.py'
            main.write_text('from helper import calc\ndef main() -> int:\n    return calc(8)\n')
            self.assertIn('py6mod_helper__calc', translate_runtime_file(main))

    def test_missing_compiler_is_a_failure(self):
        self.assertEqual(run_smoke(bend='__missing_bend_11__'), 2)

    def test_try_else_finally_semantics(self):
        for name in ('nested_exception_caught', 'finally_success', 'multi_statement_try', 'method_exception', 'exception_message', 'reraise'):
            with self.subTest(name=name):
                self.assertEqual(python_result(V11_CASES[name]), lowered_python_result(V11_CASES[name]))

    def test_math_and_generator_semantics(self):
        for name in ('dynamic_gcd', 'dynamic_factorial', 'dynamic_isqrt', 'function_local_math_import',
                     'single_inheritance', 'inherited_isinstance', 'property_getter_setter', 'generator_pure_range',
                     'generator_alias_next', 'generator_exhaustion_caught', 'dynamic_combinations'):
            with self.subTest(name=name):
                self.assertEqual(python_result(V11_CASES[name]), lowered_python_result(V11_CASES[name]))

    def test_150_randomized_finally(self):
        rand = random.Random(11220)
        for i in range(150):
            bad = rand.randrange(-50, 50)
            fallback = rand.randrange(-500, 500)
            src = f'''def risky(n: int) -> int:
    if n < 0:
        raise ValueError('bad')
    return n + 7

def main() -> int:
    try:
        value = risky({bad})
    except ValueError as exc:
        value = {fallback}
    finally:
        print({i})
    return value
'''
            self.assertEqual(python_result(src), lowered_python_result(src), (i,bad))
            if i < 5:
                RuntimeLowerer(src, f'random_{i}').compile()

    def test_subclass_runtime_predicate_present(self):
        code = RuntimeLowerer(V11_CASES['single_inheritance'], 'inheritance').compile()
        self.assertIn('def PyType.is_subclass(', code)
        self.assertIn('PyType.is_subclass(tag, expected)', code)

    def test_mixed_exception_nested_calls(self):
        src = '''def fail(n: int) -> int:
    if n == 0:
        raise ValueError('bad')
    return n

def wrapper(n: int) -> int:
    return (fail(n) * 4) + 7

def main() -> int:
    try:
        ans = wrapper(0)
    except ValueError:
        ans = 200
    return ans
'''
        self.assertEqual(python_result(src), lowered_python_result(src))
        text = RuntimeLowerer(src, 'nested').compile()
        self.assertIn('PyExc.is_error(', text)


class NegativeTests(unittest.TestCase):
    def rejects(self, src: str, fragment: str):
        with self.assertRaises(TranslationError) as err:
            RuntimeLowerer(textwrap.dedent(src), 'rejected').compile()
        self.assertIn(fragment, str(err.exception))

    def test_no_unverified_multiple_inheritance(self):
        self.rejects('''
        class A:
            pass
        class B:
            pass
        class C(A, B):
            pass
        def main() -> int:
            return 0
        ''', 'multiple inheritance')

    def test_generator_with_multiple_yields_rejected(self):
        self.rejects('''
        def g(n: int):
            for i in range(n):
                yield i
                yield i + 1
        def main() -> int:
            out = g(3)
            return 0
        ''', 'exactly one yielded expression')

    def test_impure_generator_rejected(self):
        self.rejects('''
        def g(n: int):
            for i in range(n):
                yield print(i)
        def main() -> int:
            for v in g(3):
                pass
            return 0
        ''', 'pure integer arithmetic')

    def test_generator_target_unbound_after_empty_rejected(self):
        self.rejects('''
        def g(n: int):
            for i in range(n):
                yield i
        def main() -> int:
            for result in g(0):
                pass
            return result
        ''', 'target may be unbound')

    def test_shadowed_math_import_rejected(self):
        self.rejects('''
        import math
        def main() -> int:
            math = 7
            return math.factorial(3)
        ''', 'import name is shadowed')

    def test_raise_from_rejected(self):
        self.rejects('''
        def main() -> int:
            raise ValueError('x') from RuntimeError('y')
        ''', 'exception chaining')

    def test_finally_override_rejected(self):
        self.rejects('''
        def fail() -> int:
            raise ValueError('x')
        def main() -> int:
            try:
                fail()
            finally:
                return 3
        ''', 'completion record')


if __name__ == '__main__':
    unittest.main()
