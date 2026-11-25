"""Native v6 module/dictionary front-end tests.

A source-only test is NOT equivalent to Bend check/run. That is deliberate.
"""
from __future__ import annotations

import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

from tools.python3_to_bend2.syntax import TranslationError
from tools.python3_to_bend2.modules import (
    ModuleLowerer, StaticModuleLinker, run_module_smoke, translate_linked_file,
)


def project(files):
    context = tempfile.TemporaryDirectory()
    directory = Path(context.name)
    for name, source in files.items():
        f = directory / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(textwrap.dedent(source).strip() + '\n', encoding='utf-8')
    return context, directory


class StaticLinkerTests(unittest.TestCase):
    def compare(self, files, expected=None):
        context, d = project(files)
        with context:
            cp = subprocess.run([sys.executable, '-B', '-c', 'from main import main; print(main())'],
                                cwd=d, capture_output=True, text=True, timeout=15)
            self.assertEqual(cp.returncode, 0, cp.stderr)
            flat = StaticModuleLinker(d / 'main.py').link()
            scope = {}
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                exec(compile(flat, str(d / 'linked.py'), 'exec'), scope)
                result = scope['main']()
                print(result)
            self.assertEqual(out.getvalue(), cp.stdout)
            if expected is not None:
                self.assertEqual(out.getvalue(), expected)
            bend = translate_linked_file(d / 'main.py', show_return=True)
            self.assertIn('def main() -> IO(Unit):', bend)
            self.assertNotIn('Process.run(', bend)
            return flat

    def test_module_qualified(self):
        self.compare({'main.py': '''
            import mathops as m
            def main() -> int:
                return m.mul(5, 8)
        ''', 'mathops.py': '''
            def mul(a: int, b: int) -> int:
                return a * b
        '''}, '40\n')

    def test_from_import_alias(self):
        self.compare({'main.py': '''
            from square import squared as sq
            def main() -> int:
                return sq(13)
        ''', 'square.py': '''
            def squared(x: int) -> int:
                return x * x
        '''}, '169\n')

    def test_transitive(self):
        self.compare({'main.py': '''
            from a import run
            def main() -> int:
                return run(6)
        ''', 'a.py': '''
            from b import increment
            def run(n: int) -> int:
                return increment(n) + 2
        ''', 'b.py': '''
            def increment(n: int) -> int:
                return n + 1
        '''}, '9\n')

    def test_same_symbol_in_multiple_modules(self):
        flat = self.compare({'main.py': '''
            import a
            import b
            def main() -> int:
                return a.f(2) + b.f(2)
        ''', 'a.py': '''
            def f(n: int) -> int:
                return n + 3
        ''', 'b.py': '''
            def f(n: int) -> int:
                return n * 3
        '''}, '11\n')
        self.assertIn('py6mod_a__f', flat)
        self.assertIn('py6mod_b__f', flat)

    def test_module_local_forward_reference(self):
        self.compare({'main.py': '''
            from ops import g
            def main() -> int:
                return g(3)
        ''', 'ops.py': '''
            def g(x: int) -> int:
                return h(x) + 1
            def h(x: int) -> int:
                return x * 2
        '''}, '7\n')

    def test_mutation_cross_import(self):
        self.compare({'main.py': '''
            from mutate import push
            def main() -> int:
                xs: list[int] = [1]
                same = xs
                push(xs, 3)
                return len(same)
        ''', 'mutate.py': '''
            def push(xs: list[int], v: int) -> None:
                xs.append(v)
        '''}, '2\n')

    def test_function_import_in_class_method(self):
        self.compare({'main.py': '''
            from ops import calc
            class Counter:
                x: int
                def __init__(self, x: int) -> None:
                    self.x = x
                def value(self) -> int:
                    return calc(self.x)
            def main() -> int:
                c = Counter(12)
                return c.value()
        ''', 'ops.py': '''
            def calc(x: int) -> int:
                return x + 1
        '''}, '13\n')

    def refuses(self, files, fragment):
        context, d = project(files)
        with context:
            with self.assertRaises(TranslationError) as cm:
                translate_linked_file(d / 'main.py')
            self.assertIn(fragment, str(cm.exception))

    def test_missing_or_stdlib_import(self):
        self.refuses({'main.py': 'import os\ndef main() -> int:\n    return 1\n'}, 'missing')

    def test_top_level_side_effect(self):
        self.refuses({'main.py': 'from helper import f\ndef main() -> int:\n    return f()\n',
                      'helper.py': 'x = 3\ndef f() -> int:\n    return x\n'}, 'import-time side effects')

    def test_circular_imports(self):
        self.refuses({'main.py': 'import a\ndef main() -> int:\n    return a.f()\n',
                      'a.py': 'import b\ndef f() -> int:\n    return 1\n',
                      'b.py': 'import a\ndef f() -> int:\n    return 2\n'}, 'circular imports')

    def test_first_class_function_import_rejected(self):
        self.refuses({'main.py': 'from ops import f\ndef main() -> int:\n    return f\n',
                      'ops.py': 'def f() -> int:\n    return 4\n'}, 'first-class imported')

    def test_wildcard_import_rejected(self):
        self.refuses({'main.py': 'from ops import *\ndef main() -> int:\n    return f()\n',
                      'ops.py': 'def f() -> int:\n    return 4\n'}, 'wildcard')

    def test_missing_export_rejected(self):
        self.refuses({'main.py': 'from ops import nonexistent\ndef main() -> int:\n    return nonexistent()\n',
                      'ops.py': 'def f() -> int:\n    return 4\n'}, 'unknown function')

    def test_local_shadow_of_import_rejected(self):
        self.refuses({'main.py': '''
            from ops import f
            def main() -> int:
                f = 3
                return f()
        ''', 'ops.py': 'def f() -> int:\n    return 4\n'}, 'shadowed function')

    def test_builtin_shadow_rejected(self):
        self.refuses({'main.py': 'def print() -> int:\n    return 3\ndef main() -> int:\n    return print()\n'}, 'shadows builtin')

    def test_require_project_boundary(self):
        context, d = project({'main.py': 'def main() -> int:\n    return 1\n'})
        with context:
            with self.assertRaises(TranslationError):
                StaticModuleLinker(d / 'main.py', d / 'wrong-subfolder')

    def test_rejected_imported_class(self):
        self.refuses({'main.py': 'import objects\ndef main() -> int:\n    return objects.f()\n',
                      'objects.py': 'class A:\n    pass\ndef f() -> int:\n    return 4\n'}, 'imported class')


class NativeDictTests(unittest.TestCase):
    def generate(self, code):
        result = ModuleLowerer(textwrap.dedent(code).strip(), 'dict-case', show_return=True).compile()
        self.assertIn('type PyKV is Data:', result)
        self.assertIn('ODict{items: List<&2, PyKV>}', result)
        self.assertEqual(result.count('def main() -> IO(Unit):'), 1)
        return result

    def test_literal_replace_len_get(self):
        code = '''
            def main() -> int:
                data: dict[str,int] = {"a": 2, "b": 4}
                data["a"] = 8
                return data.get("a", 0) + len(data)
        '''
        self.assertIn('PyHeap.dict_set(', self.generate(code))
        self.assertEqual(eval_main(code), 10)

    def test_alias_across_call(self):
        code = '''
            def add(d: dict[str,int]) -> None:
                d["x"] = 8
            def main() -> int:
                original: dict[str,int] = {}
                another = original
                add(original)
                return another.get("x", 0)
        '''
        self.assertIn('PyHeap.dict_get(', self.generate(code))
        self.assertEqual(eval_main(code), 8)

    def test_membership_and_empty_truth(self):
        code = '''
            def main() -> int:
                d: dict[str,int] = {}
                was_empty = not d
                d["here"] = 2
                if "here" in d and "absent" not in d and was_empty:
                    return 1
                return 0
        '''
        self.assertIn('PyHeap.dict_contains(', self.generate(code))
        self.assertEqual(eval_main(code), 1)

    def test_get_default_is_eager(self):
        code = '''
            def marker(xs: list[int]) -> int:
                xs.append(2)
                return 900
            def main() -> int:
                d: dict[str,int] = {"k": 1}
                xs: list[int] = []
                result = d.get("k", marker(xs))
                return result + len(xs)
        '''
        self.assertIn('py3_marker(', self.generate(code))
        self.assertEqual(eval_main(code), 2)

    def test_non_str_key_rejected(self):
        with self.assertRaisesRegex(TranslationError, 'keys must be strings'):
            self.generate('def main() -> int:\n    d = {3: 1}\n    return 1')

    def test_get_non_dict_is_not_evaluated_twice(self):
        # The call remains rejected by the v5 backend rather than
        # duplicating evaluation of the receiver.
        with self.assertRaises(TranslationError):
            self.generate('def main() -> int:\n    return "abc".get("x", 1)')

    def test_strict_runtime_gate_missing(self):
        self.assertEqual(run_module_smoke(bend='definitely-no-bend', source_only=False), 2)

    def test_source_smoke_only(self):
        self.assertEqual(run_module_smoke(source_only=True), 0)


def eval_main(src):
    scope = {}
    exec(textwrap.dedent(src).strip(), scope)
    return scope['main']()


if __name__ == '__main__':
    unittest.main()
