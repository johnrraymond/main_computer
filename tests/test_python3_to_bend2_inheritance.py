"""C3 and super static-lowering regressions (not Bend runtime certification)."""
from __future__ import annotations

import contextlib
import io
import textwrap
import unittest
from unittest.mock import patch
import subprocess

from tools.python3_to_bend2.syntax import TranslationError
from tools.python3_to_bend2.inheritance import InheritanceLowerer, InheritancePass, bootstrap_bend_wsl, run_smoke


SIMPLE = '''
class A:
    def value(self) -> int:
        return 10
class B:
    def value(self) -> int:
        return 20
class C(A, B):
    def value(self) -> int:
        return super().value() + 5

def main() -> int:
    obj = C()
    return obj.value()
'''

DIAMOND = '''
class Root:
    def value(self) -> int:
        return 1
class Left(Root):
    def value(self) -> int:
        return 10 + super().value()
class Right(Root):
    def value(self) -> int:
        return 100 + super().value()
class Child(Left, Right):
    def value(self) -> int:
        return 1000 + super().value()

def main() -> int:
    obj = Child()
    return obj.value()
'''

MIXIN = '''
class Base:
    x: int
    def __init__(self, n: int) -> None:
        self.x = n
    def value(self) -> int:
        return self.x
class Mixin:
    def extra(self) -> int:
        return 20
class Derived(Mixin, Base):
    def extra(self) -> int:
        return super().extra() + self.value() + 5

def main() -> int:
    v = Derived(8)
    return v.extra()
'''

SUPER_OBJECT_INIT = """
class Base:
    pass
class Child(Base):
    def __init__(self) -> None:
        super().__init__()
    def value(self) -> int:
        return 44

def main() -> int:
    x = Child()
    return x.value()
"""

SUPER_INIT = """
class Root:
    value: int
    def __init__(self, n: int) -> None:
        self.value = n
    def get(self) -> int:
        return self.value
class Child(Root):
    def __init__(self, n: int) -> None:
        super().__init__(n + 2)
def main() -> int:
    x = Child(7)
    return x.get()
"""

OVERRIDE = '''
class Base:
    def value(self) -> int:
        return 17
class Other:
    def value(self) -> int:
        return 91
class Child(Other, Base):
    pass

def main() -> int:
    x = Child()
    return x.value()
'''

MULTI_FIELD = '''
class A:
    x: int
    def __init__(self, n: int) -> None:
        self.x = n
class B:
    y: int
class C(A, B):
    def get(self) -> int:
        return self.x + 3

def main() -> int:
    c = C(7)
    return c.get()
'''


def result(source: str) -> tuple[object, str]:
    env = {'__name__': 'test_generated'}
    with io.StringIO() as output, contextlib.redirect_stdout(output):
        exec(compile(source, '<source>', 'exec'), env, env)
        return env['main'](), output.getvalue()


class MultipleInheritanceTests(unittest.TestCase):
    def test_python_semantics_and_emit(self):
        for name, source in [('simple', SIMPLE), ('diamond', DIAMOND),
                             ('mixin', MIXIN), ('override', OVERRIDE),
                             ('multi_field', MULTI_FIELD), ('super_init', SUPER_INIT), ('object_init', SUPER_OBJECT_INIT)]:
            with self.subTest(name=name):
                p = InheritancePass(name)
                lowered = p.apply(source)
                self.assertEqual(result(source), result(lowered))
                generated = InheritanceLowerer(source, name, show_return=True).compile()
                self.assertIn('def main() -> IO(Unit):', generated)
                self.assertIn('type PyHeap is Data:', generated)
                self.assertNotIn('Process.run(', generated)
                self.assertNotIn('python_compat', generated)

    def test_bootstrap_requires_explicit_windows_wsl(self):
        with self.assertRaisesRegex(RuntimeError, 'Windows Python'):
            bootstrap_bend_wsl()
        fake = subprocess.CompletedProcess(args=[], returncode=0,
            stdout='bend 2.0.40\nPYBEND_PATH=/home/test/.bend/bin/bend\n', stderr='')
        with patch('tools.python3_to_bend2.inheritance.sys.platform', 'win32'):
            with patch('tools.python3_to_bend2.inheritance.subprocess.run', return_value=fake) as invoke:
                self.assertEqual(bootstrap_bend_wsl(), '/home/test/.bend/bin/bend')
                self.assertEqual(invoke.call_args[0][0][:3], ['wsl.exe', 'sh', '-lc'])

    def test_bend_execution_gate_is_not_source_gate(self):
        self.assertEqual(run_smoke(bend='__nonexistent_bend__'), 2)
        self.assertEqual(run_smoke(source_only=True), 0)

    def test_diamond_c3_order(self):
        p = InheritancePass('diamond')
        p.apply(DIAMOND)
        self.assertEqual(p.mros['Child'], ('Child', 'Left', 'Right', 'Root'))

    def test_full_subtype_predicate(self):
        generated = InheritanceLowerer(DIAMOND, 'diamond').compile()
        self.assertIn('def PyType.is_subclass(', generated)
        self.assertIn('PyStr.equal(tag, "Child"), PyStr.equal(expected, "Right")', generated)
        self.assertIn('PyStr.equal(tag, "Child"), PyStr.equal(expected, "Root")', generated)

    def reject(self, src, expected):
        with self.assertRaises(TranslationError) as exc:
            InheritanceLowerer(textwrap.dedent(src), 'invalid').compile()
        self.assertIn(expected, str(exc.exception))

    def test_inconsistent_mro_rejected(self):
        self.reject('''
class A: pass
class B: pass
class X(A, B): pass
class Y(B, A): pass
class Z(X, Y): pass
def main() -> int:
    return 0
''', 'inconsistent C3')

    def test_cycle_rejected(self):
        self.reject('''
class A(B): pass
class B(A): pass
def main() -> int:
    return 0
''', 'cyclic')

    def test_super_without_target_rejected(self):
        self.reject('''
class A:
    def value(self) -> int:
        return super().value()
def main() -> int:
    return A().value()
''', 'no super() target')

    def test_conflicting_field_types_rejected(self):
        self.reject('''
class A:
    x: int
class B:
    x: str
class C(A, B): pass
def main() -> int:
    return 1
''', 'conflicting inherited field type')

    def test_finally_return_overrides_error_and_return(self):
        sources = [
            'def main() -> int:\n    try:\n        return 3\n    finally:\n        return 7\n',
            "def fail(n: int) -> int:\n    if n < 0:\n        raise ValueError('bad')\n    return n + 2\n\ndef main() -> int:\n    try:\n        out = fail(-1)\n    except KeyError:\n        out = 15\n    finally:\n        return 19\n",
            'def side() -> int:\n    print(4)\n    return 8\n\ndef main() -> int:\n    try:\n        return side()\n    finally:\n        return 12\n',
        ]
        class ErrorValue:
            def __init__(self, kind, message):
                self.kind = kind
                self.message = message
        runtime = {
            '_py10_raise': lambda kind, msg: ErrorValue(kind, msg),
            '_py10_rethrow': lambda error: error,
            '_py10_is_error': lambda error: isinstance(error, ErrorValue),
            '_py10_is_exception': lambda error, kind: isinstance(error, ErrorValue)
                and (error.kind == kind or kind in ('Exception', 'BaseException')),
        }
        for source in sources:
            with self.subTest(source=source[:50]):
                original = result(source)
                lower = InheritancePass('final').apply(source)
                namespace = dict(runtime)
                with io.StringIO() as stdout, contextlib.redirect_stdout(stdout):
                    exec(compile(lower, '<lowered>', 'exec'), namespace, namespace)
                    actual = namespace['main'](), stdout.getvalue()
                self.assertEqual(original, actual)
                self.assertIn('def main() -> IO(Unit):', InheritanceLowerer(source).compile())

    def test_unbound_dynamic_super_rejected(self):
        self.reject('''
class A:
    def value(self) -> int:
        item = super()
        return 1
def main() -> int:
    return A().value()
''', 'super() is supported only')


if __name__ == '__main__':
    unittest.main()
