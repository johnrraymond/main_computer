"""Causal diagnostic contract: resident -> harness -> durable LMS receipt.

Tests are isolated, do not connect to Coolify, and never mutate production state.
"""
from __future__ import annotations

import json
from pathlib import Path
import threading
import time

import pytest

from main_computer.slh.authenticator import generate_identity
from main_computer.slh.seh_autoconnect import SEHAutoConnection, SEHConnectionSettings
from main_computer.slh.seh_management_journal import ManagementJournal
from seh_mutate_harness import _resident_failure_diagnostic


def wait_for(test, seconds=3):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if test():
            return True
        time.sleep(.02)
    return False


class ConnectedStub:
    def __init__(self, **kwargs):
        self._lock = threading.RLock()
        self._stream_events = []
        self._running = threading.Event()
        self._running.set()
        self.last_error = None

    def connect(self):
        return self

    def stop(self):
        self._running.clear()


@pytest.fixture
def supervisor(monkeypatch, tmp_path):
    key, _ = generate_identity()
    monkeypatch.setattr('main_computer.slh.seh_autoconnect._load_service_signer',
                        lambda _: key)
    config = SEHConnectionSettings(enabled=True, seh_url='wss://external.example',
                                   retry_seconds=.1)
    auto = SEHAutoConnection(tmp_path, gateway_port=9001,
                             settings_resolver=lambda _: config)
    yield auto
    auto.stop()


def test_canonical_binding_failure_reports_exact_gate_without_leaking_exception(supervisor, monkeypatch):
    monkeypatch.setattr('main_computer.slh.seh_autoconnect.SLHSEHConnector', ConnectedStub)
    monkeypatch.setattr(supervisor, '_local_canonical_binding',
                        lambda: (_ for _ in ()).throw(
                            ValueError('Canonical SEH deployment was disabled')))
    supervisor.start()
    assert wait_for(lambda: supervisor.failures >= 1)
    first = supervisor.connection_diagnostics()
    assert first['attempts'] >= 1
    assert first['failure_stage'] == 'VERIFYING_LOCAL_BINDING'
    assert first['failure_code'] == 'SEH_CANONICAL_DEPLOYMENT_DISABLED'
    assert first['last_error_class'] == 'ValueError'
    assert first['failure_attempt'] >= 1
    assert first['failure_unix_ns'] > 0
    assert wait_for(lambda: supervisor.failures >= 2)
    again = supervisor.connection_diagnostics()
    assert again['failure_stage'] == 'VERIFYING_LOCAL_BINDING'
    assert again['failure_code'] == 'SEH_CANONICAL_DEPLOYMENT_DISABLED'
    assert 'Canonical SEH deployment was disabled' not in json.dumps(again)


def test_outbound_connection_failure_is_not_misattributed_to_config(supervisor, monkeypatch):
    class BrokenConnector:
        def __init__(self, **kwargs):
            pass

        def connect(self):
            raise ValueError('Bearer EXAMPLE-PRIVATE-TOKEN')

    monkeypatch.setattr('main_computer.slh.seh_autoconnect.SLHSEHConnector', BrokenConnector)
    supervisor.start()
    assert wait_for(lambda: supervisor.failures >= 1)
    diagnostic = supervisor.connection_diagnostics()
    assert diagnostic['failure_stage'] == 'CONNECTING_OUTBOUND_WSS'
    assert diagnostic['failure_code'] == 'SEH_OUTBOUND_CONNECT_FAILED'
    assert diagnostic['last_error_class'] == 'ValueError'
    assert 'EXAMPLE-PRIVATE-TOKEN' not in json.dumps(diagnostic)


def test_resolver_failure_has_no_connection_attempt(monkeypatch, tmp_path):
    key, _ = generate_identity()
    monkeypatch.setattr('main_computer.slh.seh_autoconnect._load_service_signer',
                        lambda _: key)
    auto = SEHAutoConnection(tmp_path, gateway_port=9001,
                             settings_resolver=lambda _: (_ for _ in ()).throw(ValueError('a private URL')))
    try:
        auto.start()
        assert wait_for(lambda: auto.failures >= 1)
        diag = auto.connection_diagnostics()
        assert diag['attempts'] == 0
        assert diag['failure_stage'] == 'RESOLVING_CONFIGURATION'
        assert diag['failure_code'] == 'SEH_CONFIGURATION_RESOLUTION_FAILED'
        assert 'private URL' not in json.dumps(diag)
    finally:
        auto.stop()


def test_resident_harness_projection_accepts_only_fixed_fields():
    evidence = _resident_failure_diagnostic({
        'resident_child_observed': True,
        'resident_outbound_failure_code': 'SEH_CANONICAL_DEPLOYMENT_DISABLED',
        'resident_outbound_failure_stage': 'VERIFYING_LOCAL_BINDING',
        'resident_outbound_failure_unix_ns': 1790000000000000000,
        'resident_outbound_failure_attempt': 25,
        'resident_outbound_attempts': 25,
        'resident_outbound_failures': 25,
        'resident_outbound_last_error_class': 'ValueError',
        'resident_outbound_last_http_status': None,
        'token': 'BEARER NEVER KEEP THIS',
    })
    assert evidence['error_code'] == 'SEH_CANONICAL_DEPLOYMENT_DISABLED'
    assert evidence['failed_stage'] == 'VERIFYING_LOCAL_BINDING'
    assert evidence['failure_attempt'] == 25
    assert evidence['attempts'] == 25
    assert evidence['exception_class'] == 'ValueError'
    assert 'NEVER KEEP THIS' not in json.dumps(evidence)
    injected = _resident_failure_diagnostic({
        'resident_outbound_failure_code': ['SECRET'],
        'resident_outbound_failure_stage': ['SECRET'],
        'resident_outbound_last_error_class': ['SECRET'],
        'resident_outbound_attempts': -1,
    })
    assert injected['error_code'] == 'SEH_OUTBOUND_OUTCOME_UNVERIFIED'
    assert injected['failed_stage'] == 'RESIDENT_OUTBOUND_VERIFICATION'
    assert 'attempts' not in injected


def test_receipt_is_durable_and_readable_by_new_lms_manager(tmp_path):
    operation_id = 'mcel-seh-causal-durable-123456789'
    journal = ManagementJournal(tmp_path, 'mainnet')
    diagnostic = _resident_failure_diagnostic({
        'resident_child_observed': True,
        'resident_outbound_failure_code': 'SEH_CANONICAL_DEPLOYMENT_DISABLED',
        'resident_outbound_failure_stage': 'VERIFYING_LOCAL_BINDING',
        'resident_outbound_last_error_class': 'ValueError',
        'resident_outbound_attempts': 25, 'resident_outbound_failures': 25,
    })
    outcome = {'status': 'INCOMPLETE',
               'failed_stage': 'resident_outbound_wss_verification',
               'remote_mutation': False, 'causal_diagnostic': diagnostic}
    assert journal.submit(operation_id, {'action': 'activate-seh',
                'principal': 'local-machine'}, lambda: outcome)['status'] == 'ACCEPTED'
    assert wait_for(lambda: journal.status(operation_id)['phase'] == 'RECORDED')
    row = journal.status(operation_id)
    assert row['result']['status'] == 'INCOMPLETE'
    assert row['result']['failed_stage'] == 'resident_outbound_wss_verification'
    assert row['causal_diagnostic']['failed_stage'] == 'VERIFYING_LOCAL_BINDING'
    assert row['causal_diagnostic']['error_code'] == 'SEH_CANONICAL_DEPLOYMENT_DISABLED'
    assert row['causal_diagnostic']['attempts'] == 25
    other = ManagementJournal(tmp_path, 'mainnet')
    assert other.status(operation_id)['causal_diagnostic'] == row['causal_diagnostic']
    assert other.snapshot()['last_operation']['causal_diagnostic'] == row['causal_diagnostic']
    assert (journal.path / (operation_id + '.json')).exists()


def test_receipt_diagnostic_rejects_arbitrary_code_stage_and_messages(tmp_path):
    operation_id = 'mcel-seh-causal-untrusted-1234567'
    journal = ManagementJournal(tmp_path, 'mainnet')
    diagnostic = {'source': 'resident-outbound-supervisor',
                  'error_code': ['PRIVATE-TOKEN'],
                  'failed_stage': ['PRIVATE-TOKEN'],
                  'exception_class': 'TokenIsPrivate',
                  'exception_message': 'Bearer PRIVATE-TOKEN',
                  'attempts': True, 'http_status': 'Bearer PRIVATE-TOKEN'}
    result = {'status': 'INCOMPLETE', 'remote_mutation': 'UNKNOWN',
              'causal_diagnostic': diagnostic}
    journal.submit(operation_id, {'action': 'activate-seh',
                   'principal': 'local-machine'}, lambda: result)
    assert wait_for(lambda: journal.status(operation_id)['phase'] == 'RECORDED')
    raw = (journal.path / (operation_id + '.json')).read_text()
    assert 'PRIVATE-TOKEN' not in raw
    row = journal.status(operation_id)
    assert row['causal_diagnostic']['error_code'] == 'SEH_OUTBOUND_OUTCOME_UNVERIFIED'
    assert 'attempts' not in row['causal_diagnostic']
    assert row['result']['causal_diagnostic'] == row['causal_diagnostic']


def test_preflight_refusal_has_causal_receipt(tmp_path):
    operation_id = 'mcel-seh-causal-preflight-123456'
    journal = ManagementJournal(tmp_path, 'mainnet')
    def execute():
        journal.update_stage(operation_id, 'ACTIVATION_PREFLIGHT')
        return {'status': 'REFUSED',
                'error_code': 'SEH_CANONICAL_DEPLOYMENT_DISABLED',
                'remote_mutation': False}
    journal.submit(operation_id, {'action': 'activate-seh',
                   'principal': 'local-machine'}, execute)
    assert wait_for(lambda: journal.status(operation_id)['phase'] == 'RECORDED')
    row = journal.status(operation_id)
    assert row['causal_diagnostic']['error_code'] == 'SEH_CANONICAL_DEPLOYMENT_DISABLED'
    assert row['causal_diagnostic']['failed_stage'] == 'ACTIVATION_PREFLIGHT'
    assert row['result']['remote_mutation'] is False


def test_org_child_status_and_parent_projection_preserve_failure(tmp_path, monkeypatch):
    """Exercise the actual local TLS child status, then parent status projection."""
    from unittest.mock import Mock
    from main_computer.slh.managed_gateway import transport_identity
    from main_computer.slh.organization_controller import OrganizationController
    from main_computer.slh.organization_service import OrganizationHTTPHandler, OrganizationHTTPServer
    from main_computer.slh.organization_state import save_config
    from tests.test_slh_operational_service import port

    directory = tmp_path / 'slh'
    directory.mkdir()
    api_port = port()
    save_config(directory, {'schema': 'main-computer.slh-organization-config.v1',
        'configured': True, 'enabled': True, 'activation_confirmed': True,
        'host': '127.0.0.1', 'api_port': api_port, 'worker_port': port(),
        'workers_enabled': True, 'seh_enabled': True})
    context, _ = transport_identity(directory)
    server = OrganizationHTTPServer(('127.0.0.1', api_port), OrganizationHTTPHandler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.state_dir = directory
    server.fingerprint = 'test'
    server.workers_enabled = True
    server.seh_enabled = True
    server.private_requests = None
    server.pinky_sweeper = None
    server.seh_activation_error = None

    class StubSupervisor:
        connected = False
        def connection_diagnostics(self):
            return {'phase': 'RESOLVING_CONFIGURATION',
                    'attempts': 25, 'connections': 0, 'failures': 25,
                    'last_error_class': 'ValueError', 'last_http_status': None,
                    'failure_stage': 'VERIFYING_LOCAL_BINDING',
                    'failure_code': 'SEH_CANONICAL_DEPLOYMENT_DISABLED',
                    'failure_unix_ns': 1790000000000000000,
                    'failure_attempt': 25}

    server.gateway = type('G', (), {'seh_connection': StubSupervisor()})()
    server.authority = object()
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    controller = OrganizationController(type('Authority', (), {'directory': directory})(), automatic_start=False)
    controller._process = type('P', (), {'poll': lambda self: None, 'pid': 12345})()
    try:
        child = controller._seh_child_status()
        assert child['seh_outbound_failure_stage'] == 'VERIFYING_LOCAL_BINDING'
        assert child['seh_outbound_failure_code'] == 'SEH_CANONICAL_DEPLOYMENT_DISABLED'
        parent = controller.seh_status()
        assert parent['resident_outbound_failure_stage'] == 'VERIFYING_LOCAL_BINDING'
        assert parent['resident_outbound_failure_code'] == 'SEH_CANONICAL_DEPLOYMENT_DISABLED'
        assert parent['resident_outbound_failure_attempt'] == 25
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
        controller._process = None
        controller.close()


def test_full_activation_harness_timeout_preserves_resident_failure(monkeypatch):
    """No remote operations: loopback HTTP is entirely faked in this test."""
    from main_computer.slh.authenticator_store import LocalIdentityStore
    from seh_mutate_harness import _parser, run
    import http.client

    secret = 'ONLY-LOCAL-TOKEN-DO-NOT-LOG'
    calls = []

    class FakeResponse:
        status = 200
        def __init__(self, payload):
            self.payload = json.dumps(payload).encode()
        def read(self, n):
            return self.payload
        def getheader(self, name, default=''):
            return 'application/json'

    class FakeConnection:
        def __init__(self, host, port, timeout):
            assert host == '127.0.0.1'
        def request(self, method, path, body=None, headers=None):
            calls.append(path)
        def getresponse(self):
            path = calls[-1]
            if path.endswith('/challenge'):
                return FakeResponse({'challenge_id': 'c' * 32})
            if path.endswith('/verify'):
                return FakeResponse({'access_token': secret})
            if path.endswith('/activate'):
                return FakeResponse({'status': 'ACTIVATION_STARTED',
                                     'remote_recovery': 'REPLACEMENT_QUEUED_NOT_VERIFIED'})
            if path.endswith('/status'):
                return FakeResponse({'status': 'NOT_CONNECTED',
                  'resident_child_observed': True,
                  'resident_outbound_attempts': 25,
                  'resident_outbound_failures': 25,
                  'resident_outbound_last_error_class': 'ValueError',
                  'resident_outbound_failure_code': 'SEH_CANONICAL_DEPLOYMENT_DISABLED',
                  'resident_outbound_failure_stage': 'VERIFYING_LOCAL_BINDING',
                  'resident_outbound_failure_attempt': 25})
            return FakeResponse({'status': 'LOGGED_OUT'})
        def close(self):
            pass

    monkeypatch.setattr(http.client, 'HTTPConnection', FakeConnection)
    monkeypatch.setattr(LocalIdentityStore, 'list', lambda self: {
        'identities': [{'device_id': 'device-test'}], 'invalid_identities': []})
    monkeypatch.setattr(LocalIdentityStore, 'sign',
                        lambda self, dev, challenge: {'signature': 'signed'})
    args = _parser().parse_args(['activate-seh', '--network', 'mainnet',
        '--execute-mutations', '--yes-i-know-this-mutates-seh',
        '--activation-timeout', '0.1'])
    outcome = run(args)
    assert outcome['status'] == 'INCOMPLETE'
    assert outcome['failed_stage'] == 'resident_outbound_wss_verification'
    assert outcome['causal_diagnostic']['error_code'] == 'SEH_CANONICAL_DEPLOYMENT_DISABLED'
    assert outcome['causal_diagnostic']['failed_stage'] == 'VERIFYING_LOCAL_BINDING'
    assert outcome['causal_diagnostic']['attempts'] == 25
    assert secret not in json.dumps(outcome)
    assert calls[-1] == '/v1/auth/logout'
