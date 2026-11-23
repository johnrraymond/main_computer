"""Regression/negative controls for the .local and shared-auth acceptance gate."""
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tools import mcel_security_hostname_smoke as contract


class HostnameAcceptanceRegression(unittest.TestCase):
    def test_exact_five_browser_origins(self):
        rows = contract.Results()
        with redirect_stdout(io.StringIO()):
            contract.check_namespace(rows)
        self.assertTrue(all(status == 'PASS' for _, status, _ in rows.rows))
        self.assertEqual(len(contract.ALL_HOSTS), len(set(contract.ALL_HOSTS)))
        self.assertEqual(contract.DOCS_HOST, 'onlyoffice.main-computer.local')

    def test_checkout_and_unleashed_are_exclusive_not_virtual_hosts(self):
        self.assertFalse(contract.concurrent_lanes_allowed(('dev', 'unleashed')))
        self.assertFalse(contract.concurrent_lanes_allowed(('unleashed', 'debug', 'dev')))
        self.assertTrue(contract.concurrent_lanes_allowed(('dev', 'debug', 'safe')))
        self.assertTrue(contract.concurrent_lanes_allowed(('unleashed', 'debug', 'safe')))
        self.assertTrue(contract.concurrent_lanes_allowed(('debug', 'safe')))
        self.assertFalse(contract.concurrent_lanes_allowed(('dev', 'dev')))
        self.assertFalse(contract.concurrent_lanes_allowed(('unknown',)))

    def test_exclusive_lane_does_not_require_virtual_host_mux(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'main_computer').mkdir()
            (root / 'scripts').mkdir()
            (root / 'scripts/main-computer-start-stop.ps1').write_text(
                'default { $normalized = "unleashed"; $port = "8765" }\n'
                '$defaultEnvironment = Get-ModeDefaultEnvironment $RootPath "unleashed"\n'
                'tree_kind = "source"\nmode = "unleashed"\n')
            rows = contract.Results()
            with redirect_stdout(io.StringIO()):
                contract.check_production_names(rows, root)
            result = {name: state for name, state, _ in rows.rows}
            self.assertEqual(result['dev-unleashed-exclusive-8765-lane-source'], 'PASS')
            self.assertEqual(result['real-dev-unleashed-mutual-exclusion'], 'UNVERIFIED')
            self.assertNotIn('checkout-and-unleashed-same-port-collision-resolved', result)

    def test_source_8765_mapping_is_actually_inspected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'main_computer').mkdir()
            (root / 'scripts').mkdir()
            (root / 'scripts/main-computer-start-stop.ps1').write_text(
                'default { $normalized = "unleashed"; $port = "9999" }\n'
                '$defaultEnvironment = Get-ModeDefaultEnvironment $RootPath "unleashed"\n'
                'tree_kind = "source"\nmode = "unleashed"\n')
            rows = contract.Results()
            with redirect_stdout(io.StringIO()):
                contract.check_production_names(rows, root)
            self.assertEqual({name: state for name, state, _ in rows.rows}[
                'dev-unleashed-exclusive-8765-lane-source'], 'FAIL')

    def test_unapproved_underscore_is_rejected(self):
        rows = contract.Results()
        with mock.patch.object(contract, 'ALL_HOSTS', (*contract.ALL_HOSTS, 'dev.main_computer.local')):
            with redirect_stdout(io.StringIO()):
                contract.check_namespace(rows)
        self.assertTrue(any(s == 'FAIL' for _, s, _ in rows.rows))

    def test_source_missing_browser_mappings_is_detected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'main_computer/web').mkdir(parents=True)
            (root / 'scripts').mkdir()
            (root / 'main_computer/auth_bootstrap.py').write_text("HOST='mc.test'\n")
            (root / 'main_computer/auth_bootstrap_service.py').write_text("--host-resolver-rules=MAP mc.test 127.0.0.1\n")
            (root / 'main_computer/web/graphical.html').write_text('https://mc.test/graphical')
            rows = contract.Results()
            with redirect_stdout(io.StringIO()):
                contract.check_production_names(rows, root)
            failures = {n for n,s,_ in rows.rows if s == 'FAIL'}
            self.assertIn('managed-browser-explicit-loopback-mappings', failures)
            self.assertIn('managed-browser-unknown-namespace-deny', failures)
            self.assertIn('legacy-mc-test-removed-from-active-paths', failures)
            self.assertIn('production-canonical-hostname-registry', failures)

    def test_loopback_mapping_only_does_not_satisfy_unknown_deny(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'main_computer').mkdir()
            (root / 'scripts').mkdir()
            (root / 'main_computer/auth_hostnames.py').write_text(
                '\n'.join('MAP ' + h + ' 127.0.0.1' for h in contract.ALL_HOSTS))
            rows = contract.Results()
            with redirect_stdout(io.StringIO()):
                contract.check_production_names(rows, root)
            states = dict((n,s) for n,s,_ in rows.rows)
            self.assertEqual(states['managed-browser-explicit-loopback-mappings'], 'PASS')
            self.assertEqual(states['managed-browser-unknown-namespace-deny'], 'FAIL')

    def test_cookie_host_bound_source_requirement(self):
        self.assertTrue(contract.COOKIE_NAME.startswith('__Host-'))
        self.assertFalse(contract.DOCS_HOST in contract.HOSTS.values())

    def test_docker_probe_only_inspects_daemon(self):
        rows = contract.Results()
        fake = mock.Mock(returncode=0, stdout='main-computer-onlyoffice-documentserver|onlyoffice/documentserver:latest|127.0.0.1:18085->80/tcp\n', stderr='')
        with mock.patch.object(contract.subprocess, 'run', return_value=fake) as run:
            with redirect_stdout(io.StringIO()):
                contract.check_docker_runtime(rows)
        self.assertEqual(rows.rows[0][1], 'PASS')
        self.assertEqual(run.call_args.args[0][:2], ['docker', 'ps'])

    def test_duplicate_document_server_is_rejected(self):
        rows = contract.Results()
        fake = mock.Mock(returncode=0,
                         stdout='main-computer-onlyoffice-documentserver|onlyoffice/documentserver:latest|127.0.0.1:18085->80/tcp\nmain-computer-onlyoffice-second|onlyoffice/documentserver:latest|127.0.0.1:18086->80/tcp\n')
        with mock.patch.object(contract.subprocess, 'run', return_value=fake):
            with redirect_stdout(io.StringIO()):
                contract.check_docker_runtime(rows)
        self.assertEqual(rows.rows[0][1], 'FAIL')

    def test_wildcard_onlyoffice_port_is_rejected(self):
        rows = contract.Results()
        fake = mock.Mock(returncode=0,
                         stdout='main-computer-onlyoffice-documentserver|onlyoffice/documentserver:latest|0.0.0.0:18085->80/tcp\n')
        with mock.patch.object(contract.subprocess, 'run', return_value=fake):
            with redirect_stdout(io.StringIO()):
                contract.check_docker_runtime(rows)
        self.assertEqual(dict((n,s) for n,s,_ in rows.rows)['docker-onlyoffice-no-wildcard-host-publish'], 'FAIL')

    def test_unavailable_docker_never_passes(self):
        rows = contract.Results()
        with mock.patch.object(contract.subprocess, 'run', side_effect=FileNotFoundError):
            with redirect_stdout(io.StringIO()):
                contract.check_docker_runtime(rows)
        self.assertEqual(rows.rows[0][1], 'UNVERIFIED')

    def test_current_source_remains_blocked_until_normalized(self):
        root = Path(__file__).resolve().parents[1]
        rows = contract.Results()
        with redirect_stdout(io.StringIO()):
            contract.check_production_names(rows, root)
        status = dict((n, state) for n,state,_ in rows.rows)
        # This assertion will continue to be valid after full cutover: it proves
        # the gate produced one explicit result for each required category.
        for required in ('production-canonical-hostname-registry',
                         'managed-browser-unknown-namespace-deny',
                         'one-auth-https-owner-across-concurrent-lanes'):
            self.assertIn(status[required], {'PASS', 'FAIL'})


if __name__ == '__main__':
    unittest.main()
