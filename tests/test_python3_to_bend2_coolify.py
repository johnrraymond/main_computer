"""Deployment-scope discovery and real-source translator regressions."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.python3_to_bend2 import translate, TranslationError
from tools.python3_to_bend2.coolify import (
    discover, function_program, audit_functions, FUNCTION_CASES,
)

ROOT = Path(__file__).resolve().parents[1]


class CoolifyScopeTests(unittest.TestCase):
    def test_deployed_scope_and_generated_initializers(self):
        hub, seh, site = discover(ROOT)
        self.assertIn('main_computer/stable_hub.py', hub.modules)
        self.assertIn('main_computer/stable_hub_worker_sessions.py', hub.modules)
        self.assertIn('fdb', hub.external)
        self.assertNotIn('main_computer/exp_fdb_hub.py', hub.modules)
        self.assertNotIn('main_computer/router.py', hub.modules)
        self.assertEqual(set(seh.modules), {
            'main_computer/seh/__init__.py', 'main_computer/seh/relay.py',
            'main_computer/seh/stream_writer.py', 'main_computer/slh/authenticator.py',
        })
        self.assertNotIn('<invalid-relative-import>', seh.external)
        self.assertIn('websockets', seh.external)
        self.assertIn('deploy/local-platform/site-server/app.py', site.modules)

    def test_missing_deployment_inputs_are_reported(self):
        hub, _, _ = discover(ROOT)
        for rel in ('Dockerfile.hub.stable', 'run-stable-hub.py'):
            self.assertEqual(rel in hub.missing, not (ROOT / rel).is_file())

    def test_real_source_functions_emit(self):
        results = list(audit_functions(ROOT))
        self.assertEqual(len(results), sum(len(c[-1]) for c in FUNCTION_CASES.values()))
        self.assertTrue(all(r[2] == 'EMITTED' for r in results), results)

    def test_changed_body_does_not_execute_oracle(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path = root / 'main_computer/hub_networks.py'
            path.parent.mkdir(parents=True)
            path.write_text('def _default_hub_url(host: str, port: int) -> str:\n    return "changed"\n')
            with self.assertRaisesRegex(ValueError, 'body changed'):
                function_program(root, 'hub_url', ('localhost', 1))

    def test_native_preflight_block_is_not_verification(self):
        from tools.python3_to_bend2.native_execution import Preflight
        with patch('tools.python3_to_bend2.coolify.preflight', return_value=Preflight(False, 'missing')):
            results = list(audit_functions(ROOT, native=True))
        self.assertTrue(all(r[2] == 'PREFLIGHT_BLOCKED' for r in results))

    def test_prefix_and_literal_encoding(self):
        code = translate('def main() -> bool:\n    s = "é\\n\\\""\n    return s.startswith("é")\n', show_return=True)
        self.assertIn('PyStr.starts_with(PyExc.message(', code)
        self.assertIn('SCon{Chr{233}', code)
        self.assertIn('Chr{10}', code)
        self.assertIn('Chr{34}', code)
        with self.assertRaises(TranslationError):
            translate('def main() -> bool:\n    s = "x"\n    return s.startswith(3)\n')
        with self.assertRaises(TranslationError):
            translate('def main() -> str:\n    return "\\ud800"\n')


if __name__ == '__main__':
    unittest.main()
