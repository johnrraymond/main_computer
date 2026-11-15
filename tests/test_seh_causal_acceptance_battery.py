"""Patch 4: mutation-free SEH causal evidence acceptance battery.

No real admin operation, Coolify request, Mother write, or production SLH/SEH
connection is permitted here. The only network listener is a disposable TLS
loopback child used to exercise the actual SLH parent status projection.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import threading
import time
from types import SimpleNamespace

import pytest
from playwright.sync_api import sync_playwright

from main_computer.slh.authenticator import generate_identity
from main_computer.slh.seh_autoconnect import SEHAutoConnection, SEHConnectionSettings
from main_computer.slh.seh_management_journal import ManagementJournal
from main_computer.mcel_slh_application import render_slh_page
from seh_mutate_harness import _parser, run
from tests.test_mcel_slh_startup_ewg import MOCK


SECRET = 'TOKEN-NEVER-PERSIST-IN-EVIDENCE-1234'
ID_PREFIX = 'mcel-seh-acceptance-battery-'
SCENARIOS = [
    ('deployment_disabled', 'SEH_CANONICAL_DEPLOYMENT_DISABLED', 'VERIFYING_LOCAL_BINDING', 'ValueError', None),
    ('signing_identity_changed', 'SEH_LOCAL_SIGNING_IDENTITY_CHANGED', 'VERIFYING_LOCAL_BINDING', 'ValueError', None),
    ('wss_connect_failed', 'SEH_OUTBOUND_CONNECT_FAILED', 'CONNECTING_OUTBOUND_WSS', 'ConnectionError', None),
    ('wss_auth_rejected', 'SEH_OUTBOUND_AUTH_REJECTED', 'CONNECTING_OUTBOUND_WSS', 'InvalidStatus', 403),
    ('wss_service_unavailable', 'SEH_OUTBOUND_SERVICE_UNAVAILABLE', 'CONNECTING_OUTBOUND_WSS', 'InvalidStatus', 503),
    ('resolver_unavailable', 'SEH_CONFIGURATION_RESOLUTION_FAILED', 'RESOLVING_CONFIGURATION', 'ValueError', None),
]


def _until(predicate, seconds=3):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.015)
    return False


class _ConnectedStub:
    def __init__(self, **kwargs):
        self._running = threading.Event()
        self._running.set()
        self._stream_events = []
        self._lock = threading.RLock()
        self.last_error = None

    def connect(self):
        return self

    def stop(self):
        self._running.clear()


def _failure_snapshot(tmp_path: Path, monkeypatch, scenario: str) -> dict:
    """Inject at the real resident retry boundary and preserve its measured state."""
    key, _ = generate_identity()
    monkeypatch.setattr('main_computer.slh.seh_autoconnect._load_service_signer', lambda _: key)
    settings = SEHConnectionSettings(enabled=True, seh_url='wss://disposable.invalid',
                                     retry_seconds=.4)
    if scenario == 'resolver_unavailable':
        resolver = lambda _: (_ for _ in ()).throw(ValueError('private config ' + SECRET))
    else:
        resolver = lambda _: settings
    supervisor = SEHAutoConnection(tmp_path / 'supervisor', gateway_port=9001,
                                    settings_resolver=resolver)
    if scenario in ('deployment_disabled', 'signing_identity_changed'):
        monkeypatch.setattr('main_computer.slh.seh_autoconnect.SLHSEHConnector', _ConnectedStub)
        error = ('Canonical SEH deployment was disabled' if scenario == 'deployment_disabled'
                 else 'Local SEH signing identity changed')
        monkeypatch.setattr(supervisor, '_local_canonical_binding',
                            lambda: (_ for _ in ()).throw(ValueError(error)))
    elif scenario == 'wss_connect_failed':
        class FailedConnect:
            def __init__(self, **kwargs):
                pass
            def connect(self):
                raise ConnectionError('Authorization Bearer ' + SECRET)
        monkeypatch.setattr('main_computer.slh.seh_autoconnect.SLHSEHConnector', FailedConnect)
    elif scenario in ('wss_auth_rejected', 'wss_service_unavailable'):
        status = 403 if scenario == 'wss_auth_rejected' else 503
        class InvalidStatus(Exception):
            def __init__(self):
                super().__init__('Secret: ' + SECRET)
                self.response = SimpleNamespace(status_code=status)
        class FailedConnect:
            def __init__(self, **kwargs):
                pass
            def connect(self):
                raise InvalidStatus()
        monkeypatch.setattr('main_computer.slh.seh_autoconnect.SLHSEHConnector', FailedConnect)
    try:
        supervisor.start()
        assert _until(lambda: supervisor.failures >= 1), 'Failure injection did not reach supervisor'
        snapshot = supervisor.connection_diagnostics()
    finally:
        supervisor.stop()
    assert SECRET not in json.dumps(snapshot)
    return snapshot


def _parent_observation(tmp_path: Path, diagnostic: dict) -> dict:
    """Real child TLS /v1/org/status and real parent seh_status(), loopback only."""
    from main_computer.slh.managed_gateway import transport_identity
    from main_computer.slh.organization_controller import OrganizationController
    from main_computer.slh.organization_service import OrganizationHTTPHandler, OrganizationHTTPServer
    from main_computer.slh.organization_state import save_config
    from tests.test_slh_operational_service import port

    directory = tmp_path / 'org'
    directory.mkdir()
    listen_port = port()
    save_config(directory, {'schema': 'main-computer.slh-organization-config.v1',
        'configured': True, 'enabled': True, 'activation_confirmed': True,
        'host': '127.0.0.1', 'api_port': listen_port, 'worker_port': port(),
        'workers_enabled': True, 'seh_enabled': True})
    context, _ = transport_identity(directory)
    server = OrganizationHTTPServer(('127.0.0.1', listen_port), OrganizationHTTPHandler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.state_dir = directory
    server.fingerprint = 'disposable-acceptance-battery'
    server.workers_enabled = True
    server.seh_enabled = True
    server.private_requests = None
    server.pinky_sweeper = None
    server.seh_activation_error = None

    class StubSupervisor:
        connected = False
        def connection_diagnostics(self):
            return dict(diagnostic)

    server.gateway = SimpleNamespace(seh_connection=StubSupervisor())
    server.authority = object()
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    controller = OrganizationController(SimpleNamespace(directory=directory), automatic_start=False)
    controller._process = SimpleNamespace(poll=lambda: None, pid=12345)
    try:
        child = controller._seh_child_status()
        assert child is not None, 'Disposable TLS child status unavailable'
        assert child['seh_outbound_failure_code'] == diagnostic['failure_code']
        assert child['seh_outbound_failure_stage'] == diagnostic['failure_stage']
        observed = controller.seh_status()
        assert observed['resident_child_observed'] is True
        assert observed['resident_outbound_failure_code'] == diagnostic['failure_code']
        assert observed['resident_outbound_failure_stage'] == diagnostic['failure_stage']
        return observed
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
        controller._process = None
        controller.close()


def _harness_from_disposable_status(monkeypatch, observed: dict) -> dict:
    """Run the actual activation harness using fake resident HTTP only.

    Although the CLI's mutation flags are set to exercise its outcome path,
    every HTTP action is intercepted in-memory and there is no live service.
    """
    import http.client
    from main_computer.slh.authenticator_store import LocalIdentityStore
    import tools.seh_control.git_deployment as git_deployment

    requests = []

    class Response:
        status = 200
        def __init__(self, payload):
            self.payload = json.dumps(payload).encode('utf-8')
        def read(self, n):
            return self.payload
        def getheader(self, name, default=''):
            return 'application/json'

    class ResidentHTTP:
        def __init__(self, host, port, timeout):
            assert host == '127.0.0.1'
        def request(self, method, path, body=None, headers=None):
            requests.append((method, path))
        def getresponse(self):
            path = requests[-1][1]
            if path.endswith('/challenge'):
                return Response({'challenge_id': 'c' * 32})
            if path.endswith('/verify'):
                return Response({'access_token': SECRET})
            if path.endswith('/activate'):
                return Response({'status': 'ACTIVATION_STARTED',
                                 'remote_recovery': 'REPLACEMENT_QUEUED_NOT_VERIFIED',
                                 'remote_mutation': False})
            if path.endswith('/status'):
                return Response(observed)
            return Response({'status': 'LOGGED_OUT'})
        def close(self):
            pass

    monkeypatch.setattr(http.client, 'HTTPConnection', ResidentHTTP)
    monkeypatch.setattr(LocalIdentityStore, 'list', lambda self: {
        'identities': [{'device_id': 'acceptance-only'}], 'invalid_identities': []})
    monkeypatch.setattr(LocalIdentityStore, 'sign',
                        lambda self, dev, challenge: {'signature': 'disposable-signed'})
    monkeypatch.setattr(git_deployment, 'recover_canonical_runtime',
                        lambda *a, **kw: pytest.fail('A remote recovery was attempted'))
    args = _parser().parse_args(['activate-seh', '--network', 'mainnet',
        '--execute-mutations', '--yes-i-know-this-mutates-seh',
        '--activation-timeout', '0.1'])
    outcome = run(args)
    assert outcome['status'] == 'INCOMPLETE'
    assert outcome['remote_mutation'] is False
    assert outcome['outbound_tunnel_authenticated'] is False
    assert requests[-1] == ('POST', '/v1/auth/logout')
    assert not any('coolify' in path for _, path in requests)
    assert SECRET not in json.dumps(outcome)
    return outcome


def _durable_receipt(tmp_path: Path, scenario: str, outcome: dict) -> dict:
    operation_id = ID_PREFIX + scenario
    manager = ManagementJournal(tmp_path / 'lms-state', 'mainnet')
    submission = manager.submit(operation_id,
             {'action': 'activate-seh', 'principal': 'acceptance-local'}, lambda: outcome)
    assert submission['status'] == 'ACCEPTED'
    assert _until(lambda: manager.status(operation_id)['phase'] == 'RECORDED')
    first = manager.status(operation_id)
    # Fresh LMS instance, same disposable receipt directory: no replayed worker.
    restarted = ManagementJournal(tmp_path / 'lms-state', 'mainnet')
    recovered = restarted.status(operation_id)
    assert recovered['phase'] == 'RECORDED'
    assert recovered['causal_diagnostic'] == first['causal_diagnostic']
    assert restarted.snapshot()['last_operation']['causal_diagnostic'] == recovered['causal_diagnostic']
    assert SECRET not in (manager.path / (operation_id + '.json')).read_text()
    return recovered


def _chain(tmp_path: Path, monkeypatch, scenario: str):
    with monkeypatch.context() as scoped:
        resident = _failure_snapshot(tmp_path, scoped, scenario)
        observed = _parent_observation(tmp_path, resident)
        result = _harness_from_disposable_status(scoped, observed)
    receipt = _durable_receipt(tmp_path, scenario, result)
    return resident, observed, result, receipt


@pytest.mark.parametrize('scenario,code,stage,error_class,http_status', SCENARIOS)
def test_injected_failure_survives_resident_tls_harness_and_lms_restart(
        tmp_path, monkeypatch, scenario, code, stage, error_class, http_status):
    resident, parent, harness, receipt = _chain(tmp_path, monkeypatch, scenario)
    assert resident['failure_code'] == code
    assert resident['failure_stage'] == stage
    assert resident['last_error_class'] == error_class
    assert (resident['attempts'] == 0) is (scenario == 'resolver_unavailable')
    assert parent['resident_outbound_last_http_status'] == http_status
    assert harness['causal_diagnostic']['error_code'] == code
    assert harness['causal_diagnostic']['failed_stage'] == stage
    assert receipt['result']['status'] == 'INCOMPLETE'
    assert receipt['causal_diagnostic']['error_code'] == code
    assert receipt['causal_diagnostic']['failed_stage'] == stage
    assert receipt['causal_diagnostic']['exception_class'] == error_class
    assert receipt['causal_diagnostic'].get('http_status') == http_status
    assert receipt['result']['causal_diagnostic'] == receipt['causal_diagnostic']
    assert SECRET not in json.dumps(receipt)


def _browser_page(browser, receipt: dict):
    page = browser.new_page(viewport={'width': 1280, 'height': 800})
    page.evaluate(MOCK)
    page.evaluate('''receipt=>{
      window.lmsReceipt=receipt;
      window.managementReads=[];
      window.lmsAvailable=true;
      window.lmsState={state:'IDLE',busy:false,operation:null,last_operation:receipt};
      const original=window.fetch;
      window.fetch=async(url,options={})=>{
        if(url==='/api/applications/slh/seh/deployment'){
          if(!window.lmsAvailable)throw new Error('injected-LMS-disconnect');
          const body=JSON.parse(options.body||'{}');
          window.managementReads.push(body.operation);
          const value=body.operation==='operation-state'?window.lmsState:
            body.operation==='operation-status'?window.lmsReceipt:{status:'REFUSED'};
          return {ok:true,json:async()=>({ok:true,result:value})};
        }
        return original(url,options);
      };
    }''', receipt)
    page.set_content(render_slh_page())
    page.wait_for_function('''()=>window.__mcelSlh?.diagnostics()?.startup?.lifecycleRefresh==='completed' ''')
    return page


def test_restart_receipts_reach_mcel_modal_and_ewg_without_mutation(tmp_path, monkeypatch):
    """Actual persisted failure receipts, not hard-coded UI examples."""
    receipts = []
    for scenario, code, stage, *_ in SCENARIOS:
        scenario_root = tmp_path / scenario
        scenario_root.mkdir()
        _, _, _, receipt = _chain(scenario_root, monkeypatch, scenario)
        receipts.append((receipt, code, stage))
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=shutil.which('chromium') or None)
        try:
            for receipt, code, stage in receipts:
                page = _browser_page(browser, receipt)
                try:
                    errors = []
                    page.on('pageerror', lambda err: errors.append(str(err)))
                    page.wait_for_function('''()=>window.__mcelSlh.diagnostics().sehManagement.causalDiagnostic!==null''')
                    assert page.locator('[data-mcel-lms-operation-modal]').is_hidden()
                    page.locator('[data-mcel-lms-operation-indicator] button').click()
                    modal = page.locator('[data-mcel-lms-operation-modal]')
                    assert modal.is_visible()
                    text = modal.inner_text()
                    assert code in text and stage in text
                    assert receipt['operation_id'] in text
                    assert SECRET not in text
                    before = page.evaluate('window.managementReads.length')
                    page.locator('[data-mcel-diagnostic-title-control]').click()
                    page.wait_for_function('window.copies.length===1')
                    copied = page.evaluate('window.copies[0]')
                    diagnostic = copied['diagnosticEvidence']['operational']['sehManagement']['causalDiagnostic']
                    assert diagnostic['error_code'] == code
                    assert diagnostic['failed_stage'] == stage
                    assert copied['diagnosticEvidence']['operational']['sehManagement']['operationId'] == receipt['operation_id']
                    assert page.evaluate('window.managementReads.length') == before
                    assert SECRET not in json.dumps(copied)
                    assert 'activate-seh' not in page.evaluate('window.managementReads')
                    assert not errors, errors
                finally:
                    page.close()
        finally:
            browser.close()


def test_interrupted_lms_execution_is_durable_unverified_and_never_replayed(tmp_path):
    """Simulate process death after RUNNING, not an in-process worker crash."""
    operation_id = ID_PREFIX + 'lost-executor'
    details = {'action': 'activate-seh', 'principal': 'acceptance-local'}
    original = ManagementJournal(tmp_path / 'lms-state', 'mainnet')
    # Explicitly construct a disposable on-disk RUNNING receipt as if LMS died
    # after recording admission. No runner is created, and no action executes.
    original._write({'schema': 'main-computer.seh-management-operation.v1',
        'operation_id': operation_id, 'action': details['action'],
        'principal': details['principal'], 'digest': original._digest(details),
        'phase': 'RUNNING', 'stage': 'ACTIVATION_EXECUTING',
        'accepted_at': '2026-11-14T00:00:00+00:00', 'output': [], 'output_sequence': 0})
    recovered = ManagementJournal(tmp_path / 'lms-state', 'mainnet')
    snapshot = recovered.snapshot()
    assert snapshot['state'] == 'IDLE'
    receipt = snapshot['last_operation']
    assert receipt['phase'] == 'RECORDED'
    assert receipt['stage'] == 'EXECUTION_RESULT_UNCONFIRMED'
    assert receipt['result']['status'] == 'INCOMPLETE'
    assert receipt['result']['remote_mutation'] == 'UNKNOWN'
    assert receipt['causal_diagnostic']['error_code'] == 'LMS_RESULT_UNCONFIRMED'
    assert receipt['causal_diagnostic']['failed_stage'] == 'EXECUTION_RESULT_UNCONFIRMED'
    # A repeated operation ID must replay the known receipt, never call execute.
    replay = recovered.submit(operation_id, details,
                              lambda: pytest.fail('Interrupted action was replayed'))
    assert replay['replayed_receipt'] is True
    assert replay['status'] == 'INCOMPLETE'
    assert recovered.status(operation_id)['causal_diagnostic'] == receipt['causal_diagnostic']


def test_lms_disconnect_does_not_dismiss_modal_or_lose_receipt(tmp_path):
    """Browser connection pause, recovered receipt, E/W/G and deliberate close."""
    operation_id = ID_PREFIX + 'disconnect'
    journal = ManagementJournal(tmp_path / 'lms', 'mainnet')
    journal.submit(operation_id, {'action': 'activate-seh', 'principal': 'acceptance-local'},
        lambda: {'status': 'INCOMPLETE', 'remote_mutation': False,
                 'error_code': 'SEH_CANONICAL_DEPLOYMENT_DISABLED'})
    assert _until(lambda: journal.status(operation_id)['phase'] == 'RECORDED')
    recovered = ManagementJournal(tmp_path / 'lms', 'mainnet').status(operation_id)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=shutil.which('chromium') or None)
        try:
            page = _browser_page(browser, recovered)
            try:
                page.evaluate('''async id=>{
                  const m=window.__mcelSlh.managementOverlay;
                  m.begin('intent:seh-deployment-activate','Activate SEH');
                  m.track(id);
                  await m.refresh();
                }''', operation_id)
                modal = page.locator('[data-mcel-lms-operation-modal]')
                assert modal.is_visible()
                page.evaluate('window.lmsAvailable=false')
                page.evaluate('''async()=>await window.__mcelSlh.managementOverlay.refresh()''')
                assert modal.is_visible(), 'Service interruption dismissed operator dialog'
                page.evaluate('window.__mcelSlh.managementOverlay.pause()')
                assert modal.is_visible(), 'Network disconnect dismissed operator dialog'
                page.evaluate('''async()=>{
                  window.lmsAvailable=true;
                  await window.__mcelSlh.managementOverlay.start();
                }''')
                assert modal.is_visible()
                page.wait_for_function('''()=>window.__mcelSlh.diagnostics().sehManagement.causalDiagnostic?.error_code==='SEH_CANONICAL_DEPLOYMENT_DISABLED' ''')
                modal.locator('button', has_text='Dismiss').click()
                assert modal.is_hidden()
                page.evaluate('''async()=>await window.__mcelSlh.managementOverlay.refresh()''')
                assert modal.is_hidden(), 'An already-dismissed dialog reopened on polling'
                assert page.evaluate('window.__mcelSlh.diagnostics().sehManagement.causalDiagnostic.error_code') == 'SEH_CANONICAL_DEPLOYMENT_DISABLED'
            finally:
                page.close()
        finally:
            browser.close()


@pytest.mark.parametrize('condition', ('deployment_disabled', 'remote_trust_mismatch'))
def test_actual_activation_preflight_refuses_without_service_or_mother_mutation(
        tmp_path, monkeypatch, condition):
    """Admission decisions use the real controller and disposable canonical state."""
    from main_computer.slh.authority import SLHError
    from main_computer.slh.organization_state import save_config, read_config
    from tools.seh_control import identity as mother
    from tools.seh_control import git_deployment as git
    from tests.test_slh_seh_governed_activation import ready

    disabled = condition == 'deployment_disabled'
    root, directory, controller, resolver = ready(
        tmp_path, monkeypatch, deployment_enabled=not disabled)
    try:
        if disabled:
            save_config(directory, {**read_config(directory), 'seh_enabled': True})
            monkeypatch.setattr(git, 'reconcile_pending_recovery',
                lambda *a, **kw: pytest.fail('Disabled gate entered remote recovery'))
        else:
            monkeypatch.setattr(git, 'reconcile_pending_recovery', lambda *a, **kw: None)
            resolver.side_effect = ValueError('untrusted remote signing key ' + SECRET)
        before_binding = mother.read(root, 'mainnet')[1].binding
        before_config = read_config(directory)
        if disabled:
            outcome = controller.activate_seh(execute=True, token='verified-admin')
            assert outcome['status'] == 'REFUSED'
            assert outcome['error_code'] == 'SEH_CANONICAL_DEPLOYMENT_DISABLED'
            assert outcome['failed_stage'] == 'canonical_deployment_activation_gate'
            assert outcome['remote_mutation'] is False
            assert not resolver.called
        else:
            with pytest.raises(SLHError) as error:
                controller.activate_seh(execute=True, token='verified-admin')
            assert error.value.code == 'SEH_REMOTE_TRUST_PREFLIGHT_REFUSED'
            assert resolver.call_count == 1
        assert mother.read(root, 'mainnet')[1].binding == before_binding
        assert read_config(directory) == before_config
    finally:
        controller.close()


def test_lms_restart_during_operation_keeps_open_modal_and_reports_uncertainty(tmp_path):
    """Crash after RUNNING, reconnect to new LMS; do not fabricate success."""
    operation_id = ID_PREFIX + 'crash-while-open'
    details = {'action': 'activate-seh', 'principal': 'acceptance-local'}
    original = ManagementJournal(tmp_path / 'lms-state', 'mainnet')
    original._write({'schema': 'main-computer.seh-management-operation.v1',
        'operation_id': operation_id, 'action': details['action'],
        'principal': details['principal'], 'digest': original._digest(details),
        'phase': 'RUNNING', 'stage': 'ACTIVATION_EXECUTING',
        'accepted_at': '2026-11-14T00:00:00+00:00', 'output': [], 'output_sequence': 0})
    initial = {'operation_id': operation_id, 'action': 'activate-seh',
               'phase': 'RUNNING', 'stage': 'ACTIVATION_EXECUTING'}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=shutil.which('chromium') or None)
        try:
            page = _browser_page(browser, initial)
            try:
                page.evaluate('''async op=>{
                  window.lmsState={state:'BUSY',busy:true,operation:op};
                  const m=window.__mcelSlh.managementOverlay;
                  m.begin('intent:seh-deployment-activate','Activate SEH');
                  m.track(op.operation_id);
                  await m.refresh();
                }''', initial)
                modal = page.locator('[data-mcel-lms-operation-modal]')
                assert modal.is_visible()
                page.evaluate('window.lmsAvailable=false')
                page.evaluate('window.__mcelSlh.managementOverlay.pause()')
                assert modal.is_visible()
                # New journal instance sees a RUNNING receipt with no living
                # worker. This marks uncertainty; it must NOT repeat execution.
                new_lms = ManagementJournal(tmp_path / 'lms-state', 'mainnet')
                state = new_lms.snapshot()
                assert state['state'] == 'IDLE'
                assert state['last_operation']['result']['status'] == 'INCOMPLETE'
                assert state['last_operation']['causal_diagnostic']['error_code'] == 'LMS_RESULT_UNCONFIRMED'
                page.evaluate('''async state=>{
                  window.lmsAvailable=true;
                  window.lmsState=state;
                  window.lmsReceipt=state.last_operation;
                  await window.__mcelSlh.managementOverlay.start();
                }''', state)
                assert modal.is_visible(), 'LMS restart closed the operator modal'
                page.wait_for_function('''()=>window.__mcelSlh.diagnostics().sehManagement.causalDiagnostic?.error_code==='LMS_RESULT_UNCONFIRMED' ''')
                assert 'LMS_RESULT_UNCONFIRMED' in modal.inner_text()
                before = page.evaluate('window.managementReads.length')
                page.locator('[data-mcel-diagnostic-title-control]').click()
                page.wait_for_function('window.copies.length===1')
                copy = page.evaluate('window.copies[0]')
                evidence = copy['diagnosticEvidence']['operational']['sehManagement']
                assert evidence['operationId'] == operation_id
                assert evidence['causalDiagnostic']['failed_stage'] == 'EXECUTION_RESULT_UNCONFIRMED'
                assert page.evaluate('window.managementReads.length') == before
                assert 'activate-seh' not in page.evaluate('window.managementReads')
            finally:
                page.close()
        finally:
            browser.close()
