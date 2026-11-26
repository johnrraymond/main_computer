"""Native decoder boundary, original-source codec and rejection contracts."""
import ast
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.python3_to_bend2 import translate, TranslationError
from tools.python3_to_bend2.coolify import audit_exports, compare_export, export_oracle, _ERROR_MARKER
from tools.python3_to_bend2.json_decode import PACKAGE, REJECTION
from tools.python3_to_bend2.library_exports import EXPORTS, programs
from tools.python3_to_bend2.native_execution import Preflight

ROOT = Path(__file__).resolve().parents[1]


class JsonDecodeTests(unittest.TestCase):
    def test_dynamic_decode_runs_in_bend(self):
        code = translate('''import json as j

def main()->str:
    raw = '{"v":' + '123}'
    return j.dumps(j.loads(raw), sort_keys=True)
''', show_return=True)
        self.assertIn('import '+PACKAGE+'/src/parse.bend as JSONParse', code)
        self.assertIn('JSONParse.parse(text)', code)
        self.assertRegex(code, r'py_pair_cont_\d+\(PyJSON\.decode\(')
        self.assertIn('PyValue.add(', code)
        self.assertIn('PyInt.negative(PyJSON.mag(rest, Nil{}))', code)
        self.assertIn('PyDict.set(acc, key, value)', code)
        self.assertIn('PyJSON.valid(value, 32n)', code)
        self.assertIn('Nat.is_le(String.length(text), 8192n)', code)
        self.assertIn('Nat.is_le(String.length(text), 1024n)', code)
        self.assertIn('case VError{message}:\n      VError{message}', code)
        self.assertNotIn('Process.run(', code)

    def test_decoder_not_injected_into_serializer_only(self):
        code = translate('import json\ndef main()->str: return json.dumps({"a":1})')
        self.assertNotIn(PACKAGE, code)
        self.assertNotIn('PyJSON.decode(', code)

    def test_all_reviewed_codec_fixtures_emit(self):
        export = EXPORTS['seh_frame_json_decode']
        self.assertEqual(len(export.fixtures), 28)
        self.assertEqual(sum(f.error is not None for f in export.fixtures), 9)
        outcomes = list(audit_exports(ROOT, selected='seh_frame_json_decode'))
        self.assertEqual(len(outcomes), 28)
        self.assertTrue(all(r[2] == 'EMITTED' for r in outcomes), outcomes)
        for fixture in export.fixtures:
            original, lowered = programs(ROOT, export, fixture)
            self.assertIn('def _frame(obj: dict)', original)
            self.assertIn('def _frame(obj: object)', lowered)
            self.assertIn('json.loads(raw)', original)
            self.assertIn('json.loads(raw)', lowered)
            a, b = {}, {}
            exec(compile(original, '<original pinned export>', 'exec'), a)
            exec(compile(lowered, '<lowered pinned export>', 'exec'), b)
            if fixture.error:
                for fn in (a['main'], b['main']):
                    with self.assertRaises(json.JSONDecodeError):
                        fn()
            else:
                self.assertEqual(a['main'](), b['main']())
                self.assertEqual(a['main'](), json.dumps(json.loads(fixture.args[0]),
                                                       sort_keys=True, separators=(',', ':')))

    def test_malformed_oracle_and_exact_native_rejection(self):
        export = EXPORTS['seh_frame_json_decode']
        fixture = next(f for f in export.fixtures if f.error)
        original, _ = programs(ROOT, export, fixture)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'oracle.py'
            path.write_text(original, encoding='utf-8')
            oracle = export_oracle(path, fixture.error)
        self.assertEqual(oracle.returncode, 1)
        self.assertTrue(oracle.stderr.startswith(_ERROR_MARKER+'json.decoder.JSONDecodeError: '))
        good = subprocess.CompletedProcess([], 1, '', 'Error: '+REJECTION+'\n')
        self.assertEqual(compare_export(oracle, good, fixture.error, json_decode=True), 'VERIFIED_REJECTION')
        self.assertEqual(compare_export(oracle, good, fixture.error), 'DIFFERENTIAL_FAILED')
        for rc, out, err in ((0, '', REJECTION), (1, 'unexpected\n', REJECTION),
                             (1, '', 'ValueError: unrelated'), (1, '', 'Error: compiler failed')):
            self.assertEqual(compare_export(oracle, subprocess.CompletedProcess([], rc, out, err),
                                            fixture.error, json_decode=True), 'DIFFERENTIAL_FAILED')
        wrong = subprocess.CompletedProcess([], 1, '', _ERROR_MARKER+'ValueError: unrelated\n')
        self.assertEqual(compare_export(wrong, good, fixture.error, json_decode=True), 'ORACLE_FAILED')

    def test_parse_failure_cannot_be_discarded(self):
        cases = (
            'raw="bad"\n    x=json.loads(raw)\n    return "ok"',
            'raw="bad"\n    return json.dumps([json.loads(raw)])',
            'raw="bad"\n    return json.dumps(json.loads(raw, parse_int=str))',
            'raw=1\n    return json.dumps(json.loads(raw))',
            'raw="bad"\n    return json.dumps(json.loads(raw), default=str)',
            'raw="{}"\n    return json.dumps(json.loads(raw)) + "suffix"',
        )
        for body in cases:
            with self.subTest(body=body), self.assertRaises(TranslationError):
                translate('import json\ndef main()->str:\n    '+body+'\n')
        with self.assertRaises(TranslationError):
            translate('''import json
def discard(x:object)->str: return "ok"
def main()->str:
    raw="bad"
    return discard(json.loads(raw))
''')

    def test_preflight_is_not_execution(self):
        with patch('tools.python3_to_bend2.coolify.preflight', return_value=Preflight(False,'missing')):
            cases = list(audit_exports(ROOT, selected='seh_frame_json_decode', native=True))
        self.assertEqual(len(cases), 28)
        self.assertTrue(all(c[2] == 'PREFLIGHT_BLOCKED' for c in cases))


if __name__ == '__main__':
    unittest.main()
