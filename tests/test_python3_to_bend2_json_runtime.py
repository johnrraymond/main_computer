"""Acceptance and guard tests for native canonical JSON serialization."""
import ast
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from tools.python3_to_bend2 import Compiler, translate, TranslationError
from tools.python3_to_bend2.json_runtime import JsonCompiler, uses_runtime_json
from tools.python3_to_bend2.library_exports import EXPORTS, programs, Fixture
from tools.python3_to_bend2.coolify import audit_exports, export_oracle, compare_export
from tools.python3_to_bend2.feature_cases import PROBES

ROOT = Path(__file__).resolve().parents[1]


class JsonRuntimeTests(unittest.TestCase):
    def test_reviewed_seh_frame_programs(self):
        export = EXPORTS['seh_frame_json']
        for fixture in export.fixtures:
            with self.subTest(fixture=fixture):
                original, lowered = programs(ROOT, export, fixture)
                scope = {}
                exec(compile(original, '<reviewed SEH frame>', 'exec'), scope)
                self.assertEqual(scope['main'](), json.dumps(fixture.args[0], separators=(',', ':'), sort_keys=True))
                scope = {}
                exec(compile(lowered, '<reviewed linked frame>', 'exec'), scope)
                self.assertEqual(scope['main'](), json.dumps(fixture.args[0], separators=(',', ':'), sort_keys=True))
        outcomes = list(audit_exports(ROOT, selected='seh_frame_json'))
        self.assertEqual(len(outcomes), len(export.fixtures))
        self.assertTrue(all(x[2] == 'EMITTED' for x in outcomes), outcomes)

    def test_serialization_occurs_in_bend_not_python(self):
        source = '''import json as j

def encode(obj: dict) -> str:
    return j.dumps(obj, sort_keys=True, separators=(',', ':'))

def main() -> str:
    n = 5 * 9
    nested = {'k': [n, None, False, 'word']}
    return encode({'z': nested, 'a': n})
'''
        compiler = Compiler(source, show_return=True)
        self.assertIsInstance(compiler.backend, JsonCompiler)
        bend = compiler.compile()
        self.assertIn('PyJSON.render(heap, obj, True{}, True{}, ",", ":")', bend)
        self.assertIn('PyValue.mul(', bend)
        self.assertIn('PyHeap.new_dict(', bend)
        self.assertIn('PyHeap.new_list(', bend)
        self.assertIn('PyJSON.order(sorted, items)', bend)
        self.assertNotIn('Process.run(', bend)
        self.assertNotIn('{"a":45,', bend)

    def test_encoder_options(self):
        for expression, expected in (
            ('json.dumps(obj)', 'False{}, True{}, ", ", ": "'),
            ('json.dumps(obj, ensure_ascii=False, sort_keys=True)', 'True{}, False{}, ", ", ": "'),
            ('json.dumps(obj, separators=(",", ":"))', 'False{}, True{}, ",", ":"'),
        ):
            with self.subTest(expression=expression):
                code = translate(f'import json\ndef main()->str:\n    obj={{"b": 1, "a": 2}}\n    return {expression}\n')
                self.assertIn(expected, code)

    def test_unsupported_domains_fail_closed(self):
        for body in (
            'return json.dumps({1: "bad"})',
            'return json.dumps({"a": 1.5})',
            'return json.dumps({"a": b"x"})',
            'return json.dumps({"a": {1, 2}})',
            'return json.dumps((1, 2))',
            'return json.dumps({}, indent=2)',
            'return json.dumps({}, default=str)',
            'return json.dumps({}, separators=(";", "="))',
            'return json.dumps({}, ensure_ascii=1)',
            'obj = {}\n    obj["cycle"] = obj\n    return json.dumps(obj)',
            'obj = []\n    obj.append(obj)\n    return json.dumps(obj)',
            'json = 1\n    return json.dumps({})',
        ):
            with self.subTest(body=body):
                with self.assertRaises(TranslationError):
                    translate('import json\ndef main()->str:\n    '+body+'\n')

    def test_import_not_shadowed_and_fixture_values_validated(self):
        export = EXPORTS['seh_frame_json']
        with self.assertRaisesRegex(ValueError, 'unsupported JSON fixture'):
            programs(ROOT, export, Fixture(({'x': 1.5},)))
        with self.assertRaisesRegex(ValueError, 'key must be str'):
            programs(ROOT, export, Fixture(({1: 'x'},)))
        value = []
        value.append(value)
        with self.assertRaisesRegex(ValueError, 'depth limit or cycle'):
            programs(ROOT, export, Fixture((value,)))

    def test_original_feature_inventory_does_not_select_new_path(self):
        for key, source in PROBES.items():
            with self.subTest(key=key):
                self.assertFalse(uses_runtime_json(ast.parse(source)))

    def test_exact_wire_bytes_are_compared(self):
        original, _ = programs(ROOT, EXPORTS['seh_frame_json'], EXPORTS['seh_frame_json'].fixtures[8])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'case.py'
            path.write_text(original, encoding='utf-8')
            oracle = export_oracle(path, None)
        self.assertEqual(oracle.returncode, 0)
        self.assertEqual(compare_export(oracle, oracle, None), 'VERIFIED')
        changed = subprocess.CompletedProcess([], 0, oracle.stdout.replace('\\u00e9', 'é'), '')
        self.assertNotEqual(changed.stdout, oracle.stdout)
        self.assertEqual(compare_export(oracle, changed, None), 'DIFFERENTIAL_FAILED')


if __name__ == '__main__':
    unittest.main()
