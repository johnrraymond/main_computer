"""Patch 5: one admin-authorized SLH-owned outbound SEH activation."""
from __future__ import annotations

from copy import deepcopy
import json
from unittest.mock import Mock, patch

import pytest

from main_computer.slh.api import SLHAPI
from main_computer.slh.authority import SLHError
from main_computer.slh.organization_controller import OrganizationController
from main_computer.slh.organization_state import save_config, read_config
from main_computer.slh.seh_autoconnect import SEHConnectionSettings
from seh_mutate_harness import _parser, run, HarnessError
from tools.seh_control import identity as mother
from tools.seh_control.commands import SEHControl
from tests.test_seh_single_deployment_migration import populated, APP


class Authority:
    def __init__(self, directory):
        self.directory = directory
    def initialized(self):
        return True
    def authenticate(self, token, *, admin=False):
        if token != 'verified-admin':
            raise SLHError('AUTH_REQUIRED', 401)
        return {'user_id': 'owner', 'role': 'admin'}


def ready(tmp_path, monkeypatch, *, running=True):
    root, _ = populated(tmp_path)
    SEHControl(root).migrate_single('mainnet', 1, 'migrate')
    directory = tmp_path / 'slh'
    directory.mkdir()
    save_config(directory, {'schema': 'main-computer.slh-organization-config.v1',
        'configured': True, 'enabled': running, 'activation_confirmed': running,
        'host': '127.0.0.1', 'api_port': 19041, 'worker_port': 19042,
        'workers_enabled': True, 'seh_enabled': False})
    controller = OrganizationController(Authority(directory), automatic_start=False)
    if running:
        controller._process = Mock(pid=99999)
        controller._process.poll.return_value = None
    monkeypatch.setattr('main_computer.slh.storage.machine_root', lambda: root)
    settings = SEHConnectionSettings(True, 'wss://external.greatlibrary.io',
                                      False, 2.0)
    resolver = Mock(return_value=settings)
    monkeypatch.setattr('main_computer.slh.seh_canonical_connection.resolve_canonical_connection', resolver)
    return root, directory, controller, resolver


def record(root):
    return deepcopy(mother.read(root, 'mainnet')[2]['networks']['mainnet'])


def test_plan_is_read_only_and_does_not_choose_an_identity(tmp_path, monkeypatch):
    root, directory, controller, resolver = ready(tmp_path, monkeypatch)
    before = mother.read(root, 'mainnet')[1].binding
    plan = controller.activate_seh(execute=False)
    assert plan['status'] == 'PLAN_ONLY' and plan['application_uuid'] == APP
    assert not plan['remote_mutation']
    assert plan['mother_generation'] == before.generation
    assert mother.read(root, 'mainnet')[1].binding == before
    assert read_config(directory)['seh_enabled'] is False
    assert resolver.call_count == 1
    controller.close()


def test_one_transaction_enables_existing_uuid_and_running_child_without_restarting(tmp_path, monkeypatch):
    root, directory, controller, resolver = ready(tmp_path, monkeypatch)
    observed = []
    monkeypatch.setattr(controller, '_seh_child_request', lambda method, path, *, token=None:
                        observed.append((method, path, token)) or {'status': 'ACTIVATION_STARTED'})
    monkeypatch.setattr(controller, 'restart', lambda: pytest.fail('Must not restart child'))
    monkeypatch.setattr(controller, '_terminate', lambda: None)
    original = record(root)
    before = mother.read(root, 'mainnet')[1].binding.generation
    done = controller.activate_seh(execute=True, expected_generation=before, token='verified-admin')
    assert done['status'] == 'ACTIVATION_STARTED'
    assert done['mother_generation'] == before
    assert done['remote_mutation'] is False
    assert observed == []  # process-local local token is never sent to child
    updated = record(root)
    assert updated == original  # Mother remains unchanged
    assert read_config(directory)['seh_enabled'] is True
    again = controller.activate_seh(execute=True, token='verified-admin')
    assert again['mother_mutation'] == 'UNCHANGED'
    assert mother.read(root, 'mainnet')[1].binding.generation == before
    assert observed == []
    controller.close()


def test_stale_generation_or_missing_admin_never_changes_state(tmp_path, monkeypatch):
    root, directory, controller, resolver = ready(tmp_path, monkeypatch)
    original = mother.read(root, 'mainnet')[1].binding
    with pytest.raises(SLHError) as stale:
        controller.activate_seh(execute=True, expected_generation=100, token='verified-admin')
    assert stale.value.code == 'IDENTITY_SNAPSHOT_CHANGED'
    with pytest.raises(SLHError) as missing:
        controller.activate_seh(execute=True)
    assert missing.value.code == 'ADMIN_SESSION_REQUIRED'
    assert mother.read(root, 'mainnet')[1].binding == original
    assert read_config(directory)['seh_enabled'] is False
    assert not resolver.called
    controller.close()


def test_remote_trust_failure_refuses_before_mother_or_service_write(tmp_path, monkeypatch):
    root, directory, controller, resolver = ready(tmp_path, monkeypatch)
    resolver.side_effect = ValueError('mismatched signing key')
    before = mother.read(root, 'mainnet')[1].binding
    with pytest.raises(SLHError) as refusal:
        controller.activate_seh(execute=True, token='verified-admin')
    assert refusal.value.code == 'SEH_REMOTE_TRUST_PREFLIGHT_REFUSED'
    assert mother.read(root, 'mainnet')[1].binding == before
    assert read_config(directory)['seh_enabled'] is False
    controller.close()


def test_partial_activation_returns_incomplete_and_idempotent_retry(tmp_path, monkeypatch):
    root, directory, controller, resolver = ready(tmp_path, monkeypatch)
    import main_computer.slh.organization_controller as module
    actual_save = module.save_config
    calls = []
    def fail_once(path, value):
        calls.append(True)
        if len(calls) == 1:
            raise OSError('simulated local write fault')
        return actual_save(path, value)
    monkeypatch.setattr(module, 'save_config', fail_once)
    monkeypatch.setattr(controller, '_terminate', lambda: None)
    before = mother.read(root, 'mainnet')[1].binding.generation
    partial = controller.activate_seh(execute=True, token='verified-admin')
    assert partial['status'] == 'INCOMPLETE'
    assert partial['failed_stage'] == 'resident_service_activation'
    assert partial['mother_generation'] == before
    assert read_config(directory)['seh_enabled'] is False
    restored = controller.activate_seh(execute=True, token='verified-admin')
    assert restored['status'] == 'ACTIVATION_STARTED'
    assert restored['mother_mutation'] == 'UNCHANGED'
    assert read_config(directory)['seh_enabled'] is True
    assert mother.read(root, 'mainnet')[1].binding.generation == before
    controller.close()


def test_local_admin_api_refuses_unauthorized_activation(tmp_path, monkeypatch):
    root, directory, controller, resolver = ready(tmp_path, monkeypatch)
    api = SLHAPI(Authority(directory), organization_controller=controller)
    code, body = api.call('POST','/v1/local/seh/activate',
                          {'execute': True}, loopback=True)
    assert code == 401
    code, body = api.call('POST','/v1/local/seh/activate',
                          {'execute': True}, token='verified-admin', loopback=False)
    assert code == 403
    code, body = api.call('POST','/v1/local/seh/activate',
                          {'execute': False}, token='verified-admin', loopback=True)
    assert code == 200 and body['status'] == 'PLAN_ONLY'
    assert mother.read(root, 'mainnet')[1].binding.generation == 2
    controller.close()


def test_resident_status_requires_authenticated_tunnel_and_private_services(tmp_path, monkeypatch):
    root, directory, controller, resolver = ready(tmp_path, monkeypatch)
    save_config(directory, {**read_config(directory), 'seh_enabled': True})
    monkeypatch.setattr(controller, '_seh_child_status', lambda: {
        'seh_enabled': True, 'seh_connection_verified': True,
        'private_requests_available': True, 'pinky_sweeper_available': True,
        'worker_serving_enabled': True})
    assert controller.seh_status()['status'] == 'CONNECTED_VERIFIED'
    monkeypatch.setattr(controller, '_seh_child_status', lambda: {
        'seh_enabled': True, 'seh_connection_verified': True,
        'private_requests_available': False, 'pinky_sweeper_available': True,
        'worker_serving_enabled': True})
    assert controller.seh_status()['status'] == 'NOT_CONNECTED'
    monkeypatch.setattr(controller, '_seh_child_status', lambda: None)
    assert controller.seh_status()['resident_child_observed'] is False
    controller.close()


def test_resident_disconnection_reports_safe_supervisor_evidence(tmp_path, monkeypatch):
    root, directory, controller, resolver = ready(tmp_path, monkeypatch)
    save_config(directory, {**read_config(directory), 'seh_enabled': True})
    monkeypatch.setattr(controller, '_seh_child_status', lambda: {
        'seh_enabled': True, 'seh_connection_verified': False,
        'private_requests_available': True, 'pinky_sweeper_available': True,
        'worker_serving_enabled': True,
        'seh_outbound_phase': 'RETRYING_AFTER_FAILURE',
        'seh_outbound_attempts': 4, 'seh_outbound_connections': 0,
        'seh_outbound_failures': 4,
        'seh_outbound_last_error_class': 'InvalidStatus',
        'seh_outbound_last_http_status': 404})
    status = controller.seh_status()
    assert status['status'] == 'NOT_CONNECTED'
    assert status['outbound_tunnel_authenticated'] is False
    assert status['resident_outbound_attempts'] == 4
    assert status['resident_outbound_last_error_class'] == 'InvalidStatus'
    assert status['resident_outbound_last_http_status'] == 404
    assert 'token' not in json.dumps(status)
    controller.close()


def test_cli_requires_two_approvals_and_refuses_seh_alias(monkeypatch):
    for flags in (['--execute-mutations'], ['--yes-i-know-this-mutates-seh'],
                  ['--seh','legacy-alias','--execute-mutations','--yes-i-know-this-mutates-seh']):
        with pytest.raises(HarnessError):
            run(_parser().parse_args(['activate-seh',*flags]))


def test_cli_one_signed_device_login_reports_connection_without_leaking_token(monkeypatch):
    from main_computer.slh.authenticator_store import LocalIdentityStore
    class Response:
        status=200
        def __init__(self,payload):self.data=json.dumps(payload).encode()
        def read(self,n):return self.data
    calls=[]
    class Connection:
        def __init__(self,host,port,timeout):
            assert host=='127.0.0.1' and port==8897
        def request(self,method,path,body=None,headers=None):
            calls.append((method,path,json.loads(body) if body else {},headers or {}))
        def getresponse(self):
            path=calls[-1][1]
            if path.endswith('/challenge'):
                data={'challenge_id':'a'*32, 'device_id':'admin-pc'}
            elif path.endswith('/verify'):
                data={'access_token':'ONLY-IN-MEMORY'}
            elif path.endswith('/activate'):
                data={'status':'ACTIVATION_STARTED', 'application_uuid':APP}
            elif path.endswith('/status'):
                data={'status':'CONNECTED_VERIFIED', 'outbound_tunnel_authenticated':True,
                      'private_requests_available':True, 'pinky_sweeper_available':True}
            else:data={'ok':True}
            return Response(data)
        def close(self):pass
    monkeypatch.setattr('http.client.HTTPConnection',Connection)
    monkeypatch.setattr(LocalIdentityStore,'list',lambda self: {'identities':[{'device_id':'admin-pc'}],
                                                              'invalid_identities':[]})
    monkeypatch.setattr(LocalIdentityStore,'sign',lambda self,dev,challenge: {'signature':'signed'})
    args=_parser().parse_args(['activate-seh','--execute-mutations','--yes-i-know-this-mutates-seh'])
    outcome=run(args)
    assert outcome['status']=='CONNECTED_VERIFIED'
    assert outcome['application_uuid']==APP
    assert 'ONLY-IN-MEMORY' not in json.dumps(outcome)
    assert calls[-1][1]=='/v1/auth/logout'
    assert [path for _,path,_,_ in calls] == [
        '/v1/auth/challenge','/v1/auth/verify','/v1/local/seh/activate',
        '/v1/local/seh/status','/v1/auth/logout']
    assert calls[2][3]['Authorization']=='Bearer ONLY-IN-MEMORY'


def test_pinned_resident_tls_live_start_without_worker_restart(tmp_path, monkeypatch):
    """Actual TLS request from local owner to child, with genuine certificate pin."""
    import threading
    from main_computer.slh.managed_gateway import transport_identity
    from main_computer.slh.organization_service import OrganizationHTTPHandler, OrganizationHTTPServer
    from tests.test_slh_operational_service import port

    directory = tmp_path / 'slh'
    directory.mkdir()
    api_port = port()
    save_config(directory, {'schema': 'main-computer.slh-organization-config.v1',
        'configured': True, 'enabled': True, 'activation_confirmed': True,
        'host': '127.0.0.1', 'api_port': api_port, 'worker_port': port(),
        'workers_enabled': True, 'seh_enabled': True})
    tls, _ = transport_identity(directory)
    child = OrganizationHTTPServer(('127.0.0.1',api_port), OrganizationHTTPHandler)
    child.socket = tls.wrap_socket(child.socket, server_side=True)
    child.state_dir = directory
    child.authority = Authority(directory)
    child.workers_enabled = True
    child.seh_enabled = False
    child.private_requests = object()
    child.pinky_sweeper = object()
    class Gateway:
        allow_seh = False
        seh_connection = None
        def start_canonical_seh(self):
            self.seh_connection = type('Connection', (), {'connected': True})()
    child.gateway = Gateway()
    thread = threading.Thread(target=child.serve_forever, daemon=True)
    thread.start()
    controller = OrganizationController(Authority(directory), automatic_start=False)
    controller._process = Mock(pid=99999)
    controller._process.poll.return_value = None
    try:
        assert controller._seh_child_status()['seh_connection_verified'] is False
        assert controller._seh_child_request('POST', '/v1/org/admin/seh/activate', token='wrong') is None
        assert controller._seh_child_status()['seh_connection_verified'] is False
        assert controller._seh_child_request('POST', '/v1/org/admin/seh/activate', token='verified-admin') == {
            'status': 'ACTIVATION_STARTED', 'outbound_connection_owner': 'resident-slh'}
        assert child.gateway.allow_seh is True
        assert controller.seh_status()['status'] == 'CONNECTED_VERIFIED'
        # The same PID and listener are retained: no child restart.
        assert controller._process.pid == 99999
        (directory/'managed-tls-cert.pem').write_text('not the child certificate')
        assert controller._seh_child_status() is None
    finally:
        child.shutdown()
        child.server_close()
        thread.join(timeout=4)
        controller._process = None
        controller.close()


def test_cli_real_signed_admin_session_over_loopback(tmp_path, monkeypatch):
    """Exercise actual P-256 challenge/verify and localhost-only activation API."""
    from threading import Thread
    from main_computer.slh.local_service import create_server
    from main_computer.slh.authenticator import sign_object
    from main_computer.slh.authenticator_store import LocalIdentityStore
    from main_computer.slh.storage import secure_machine_state_dir
    from tests.test_slh_operational_service import disposable_slh_state as _fixture

    slh = tmp_path / 'slh'
    user_keys = tmp_path / 'user-keys'
    store = LocalIdentityStore(user_keys)
    identity = store.create('admin-pc')
    key = store.load_private_key('admin-pc')
    monkeypatch.setattr('main_computer.slh.local_service.secure_machine_state_dir',
                        lambda directory: (directory.mkdir(parents=True, exist_ok=True), directory)[1])
    original_init = LocalIdentityStore.__init__
    def local_store_init(self, directory=None):
        original_init(self, user_keys if directory is None else directory)
    monkeypatch.setattr(LocalIdentityStore, '__init__', local_store_init)
    with create_server(state_dir=slh, port=0) as local:
        statement = {'version': 1, 'action': 'slh.admin.bootstrap',
                     'admin_id': 'owner', 'device_id': 'admin-pc',
                     'public_key': identity['public_key']}
        local.organization_controller.authority.bootstrap({
            **statement, 'proof': sign_object(key, statement),
            'bootstrap_secret': (slh / 'bootstrap.secret').read_text(),
            'admin_name': 'Owner'}, loopback=True)
        local.organization_controller.activate_seh = lambda **kwargs: {
            'status': 'ACTIVATION_STARTED' if kwargs['execute'] else 'PLAN_ONLY',
            'application_uuid': APP}
        local.organization_controller.seh_status = lambda: {
            'status': 'CONNECTED_VERIFIED', 'outbound_tunnel_authenticated':True,
            'private_requests_available':True,'pinky_sweeper_available':True}
        runner = Thread(target=local.serve_forever, daemon=True)
        runner.start()
        try:
            opts = _parser().parse_args(['activate-seh', '--local-port', str(local.server_port),
                '--execute-mutations', '--yes-i-know-this-mutates-seh'])
            result = run(opts)
            assert result['status'] == 'CONNECTED_VERIFIED'
            assert 'admin-pc' not in result and 'access_token' not in result
            assert result['application_uuid'] == APP
        finally:
            local.shutdown()
            runner.join(timeout=3)


def test_running_gateway_starts_one_canonical_supervisor_without_rebinding_listener(tmp_path):
    """Real gateway object: hot start is idempotent and does not recreate Worker sockets."""
    from main_computer.slh.authenticator import generate_identity, sign_object
    from main_computer.slh.authority import SLHAuthority
    from main_computer.slh.managed_gateway import SLHManagedGateway
    from tools.slh_seh_connector import service_identity

    slh = tmp_path / 'slh'
    service_identity(slh, create=True)
    authority = SLHAuthority(slh)
    key, public = generate_identity()
    statement = {'version': 1, 'action': 'slh.admin.bootstrap',
                 'admin_id': 'owner', 'device_id': 'owner-device', 'public_key': public}
    authority.bootstrap({**statement, 'proof': sign_object(key, statement),
                         'bootstrap_secret': (slh / 'bootstrap.secret').read_text(),
                         'admin_name': 'Owner'}, loopback=True)
    gateway = SLHManagedGateway(authority, host='127.0.0.1', port=0, allow_seh=True)
    gateway._server = object()  # already-bound Worker server; do not replace
    existing_server = gateway._server
    fake_settings = SEHConnectionSettings(True, 'wss://external.greatlibrary.io',
                                           False, 2.0)
    class FakeSupervisor:
        made = []
        def __init__(self, *args, **kwargs):
            self.made.append((args, kwargs))
        def start(self):
            return self
    with patch('main_computer.slh.seh_canonical_connection.resolve_canonical_connection',
               return_value=fake_settings) as resolver, patch(
               'main_computer.slh.seh_autoconnect.SEHAutoConnection', FakeSupervisor):
        connection = gateway.start_canonical_seh()
        assert connection is gateway.start_canonical_seh()
        assert gateway._server is existing_server
        assert resolver.call_count == 1
        assert len(FakeSupervisor.made) == 1
        assert not (slh / 'seh-connection.json').exists()


def test_unconfigured_slh_is_prepared_by_single_authorized_activation(tmp_path, monkeypatch):
    """A fresh admin bootstrap needs no separate Worker setup command."""
    root, directory, controller, resolver = ready(tmp_path, monkeypatch, running=False)
    save_config(directory, {**read_config(directory), 'configured': False,
                            'enabled': False, 'activation_confirmed': False,
                            'workers_enabled': False, 'seh_enabled': False,
                            'host': '0.0.0.0'})
    before = mother.read(root, 'mainnet')[1].binding.generation
    original = record(root)
    planned = controller.activate_seh(execute=False)
    assert planned['status'] == 'PLAN_ONLY'
    assert planned['worker_service_setup'] == 'REQUIRED'
    assert planned['worker_bind_policy'] == 'LOOPBACK_DEFAULT'
    assert read_config(directory)['configured'] is False
    assert mother.read(root, 'mainnet')[1].binding.generation == before

    starts = []
    def fake_launch():
        starts.append(True)
        controller._process = Mock(pid=1001)
        controller._process.poll.return_value = None
    monkeypatch.setattr(controller, '_launch', fake_launch)
    monkeypatch.setattr(controller, '_listening', lambda cfg: True)
    monkeypatch.setattr(controller, '_terminate', lambda: None)
    done = controller.activate_seh(execute=True, token='verified-admin', expected_generation=before)
    assert done['status'] == 'ACTIVATION_STARTED'
    assert starts == [True]
    cfg = read_config(directory)
    assert cfg['configured'] and cfg['enabled'] and cfg['activation_confirmed']
    assert cfg['workers_enabled'] and cfg['seh_enabled']
    assert cfg['host'] == '127.0.0.1'  # never open a new LAN listener implicitly
    assert (cfg['worker_port'], cfg['api_port']) == (19042, 19041)
    assert mother.read(root, 'mainnet')[1].binding.generation == before
    assert record(root) == original
    controller._process = None
    controller.close()


def test_existing_worker_binding_preserved_during_first_activation(tmp_path, monkeypatch):
    root, directory, controller, _ = ready(tmp_path, monkeypatch, running=False)
    save_config(directory, {**read_config(directory), 'workers_enabled': False,
                            'host': '0.0.0.0'})
    monkeypatch.setattr(controller, '_launch', lambda: None)
    answer = controller.activate_seh(execute=True, token='verified-admin')
    assert answer['status'] == 'ACTIVATION_STARTED'
    cfg = read_config(directory)
    assert cfg['workers_enabled'] and cfg['enabled'] and cfg['seh_enabled']
    assert cfg['host'] == '0.0.0.0'  # explicit existing choice, never overwritten
    assert (cfg['worker_port'], cfg['api_port']) == (19042, 19041)
    controller.close()


def test_enabled_admin_only_child_is_restarted_only_when_workers_missing(tmp_path, monkeypatch):
    root, directory, controller, _ = ready(tmp_path, monkeypatch, running=True)
    save_config(directory, {**read_config(directory), 'workers_enabled': False})
    events = []
    def fake_terminate():
        events.append('terminate')
        controller._process = None
    def fake_launch():
        events.append('launch')
        controller._process = Mock(pid=1001)
        controller._process.poll.return_value = None
    monkeypatch.setattr(controller, '_terminate', fake_terminate)
    monkeypatch.setattr(controller, '_launch', fake_launch)
    monkeypatch.setattr(controller, '_listening', lambda cfg: True)
    answer = controller.activate_seh(execute=True, token='verified-admin')
    assert answer['status'] == 'ACTIVATION_STARTED'
    assert events == ['terminate', 'launch']
    cfg = read_config(directory)
    assert cfg['workers_enabled'] and cfg['seh_enabled'] and cfg['enabled']
    controller._process = None
    controller.close()


def test_unconfigured_preflight_refusal_never_provisions_worker_or_mother(tmp_path, monkeypatch):
    root, directory, controller, resolver = ready(tmp_path, monkeypatch, running=False)
    save_config(directory, {**read_config(directory), 'configured': False,
                            'workers_enabled': False})
    resolver.side_effect = ValueError('wrong public key')
    before_cfg = read_config(directory)
    before_gen = mother.read(root, 'mainnet')[1].binding.generation
    with pytest.raises(SLHError) as exc:
        controller.activate_seh(execute=True, token='verified-admin')
    assert exc.value.code == 'SEH_REMOTE_TRUST_PREFLIGHT_REFUSED'
    assert read_config(directory) == before_cfg
    assert mother.read(root, 'mainnet')[1].binding.generation == before_gen
    controller.close()


def test_local_bearer_token_is_not_a_child_process_session(tmp_path):
    """One database does not mean shared in-memory bearer sessions."""
    from main_computer.slh.authenticator import generate_identity, sign_object
    from main_computer.slh.authority import SLHAuthority
    from tools.slh_seh_connector import service_identity

    slh = tmp_path / 'slh'
    service_identity(slh, create=True)
    local = SLHAuthority(slh)
    key, public = generate_identity()
    statement = {'version': 1, 'action': 'slh.admin.bootstrap',
                 'admin_id': 'owner', 'device_id': 'admin-pc', 'public_key': public}
    local.bootstrap({**statement, 'proof': sign_object(key, statement),
                     'bootstrap_secret': (slh / 'bootstrap.secret').read_text(),
                     'admin_name': 'Owner'}, loopback=True)
    challenge = local.challenge('admin-pc')
    session = local.verify_login({'device_id': 'admin-pc',
                                  'challenge_id': challenge['challenge_id'],
                                  'signature': sign_object(key, challenge)})
    assert local.authenticate(session['access_token'], admin=True)['role'] == 'admin'
    child = SLHAuthority(slh)
    with pytest.raises(SLHError) as exc:
        child.authenticate(session['access_token'], admin=True)
    assert exc.value.code == 'SESSION_INVALID'


def test_resident_policy_watch_hot_starts_seh_without_cross_process_token(tmp_path):
    """Existing Worker and private services stay alive through SEH activation."""
    from types import SimpleNamespace
    from main_computer.slh.organization_service import _refresh_outbound_seh
    directory = tmp_path / 'slh'
    directory.mkdir()
    save_config(directory, {'schema': 'main-computer.slh-organization-config.v1',
        'configured': True, 'enabled': True, 'activation_confirmed': True,
        'host': '127.0.0.1', 'api_port': 19041, 'worker_port': 19042,
        'workers_enabled': True, 'seh_enabled': False})
    existing_listener, private, sweeper = object(), object(), object()
    gateway = SimpleNamespace(seh_connection=None, allow_seh=False,
                              _server=existing_listener)
    starts = []
    def start_canonical():
        starts.append(True)
        assert gateway.allow_seh
        gateway.seh_connection = object()
    gateway.start_canonical_seh = start_canonical
    child = SimpleNamespace(state_dir=directory, gateway=gateway,
                            workers_enabled=True, seh_enabled=False,
                            private_requests=private, pinky_sweeper=sweeper)
    _refresh_outbound_seh(child)
    assert starts == []  # no implicit activation
    save_config(directory, {**read_config(directory), 'seh_enabled': True})
    _refresh_outbound_seh(child)
    _refresh_outbound_seh(child)
    assert starts == [True] and child.seh_enabled is True
    assert child.seh_activation_error is None
    assert gateway._server is existing_listener
    assert child.private_requests is private and child.pinky_sweeper is sweeper


def test_resident_policy_watch_retries_without_failing_worker_child(tmp_path):
    from types import SimpleNamespace
    from main_computer.slh.organization_service import _refresh_outbound_seh
    directory = tmp_path / 'slh'
    directory.mkdir()
    save_config(directory, {'schema': 'main-computer.slh-organization-config.v1',
        'configured': True, 'enabled': True, 'activation_confirmed': True,
        'host': '127.0.0.1', 'api_port': 19041, 'worker_port': 19042,
        'workers_enabled': True, 'seh_enabled': True})
    gateway = SimpleNamespace(seh_connection=None, allow_seh=False)
    calls = []
    def start_canonical():
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError('temporary relay unavailable')
        gateway.seh_connection = object()
    gateway.start_canonical_seh = start_canonical
    child = SimpleNamespace(state_dir=directory, gateway=gateway,
                            workers_enabled=True, seh_enabled=False)
    _refresh_outbound_seh(child)
    assert child.seh_activation_error == 'RuntimeError'
    assert not child.seh_enabled and gateway.seh_connection is None
    _refresh_outbound_seh(child)
    assert child.seh_activation_error is None
    assert child.seh_enabled and gateway.seh_connection is not None
    assert len(calls) == 2


def test_unhandled_recovery_exception_is_structured_incomplete_without_secrets(tmp_path):
    """A runtime exception after Coolify mutation is not a preflight refusal."""
    from io import BytesIO

    class BrokenController:
        def activate_seh(self, **kwargs):
            raise AttributeError('bearer-secret-NEVER-PRINT')

    api = SLHAPI(Authority(tmp_path), organization_controller=BrokenController())
    statuses = []
    headers = []
    body = json.dumps({'execute': True}).encode()
    env = {'REMOTE_ADDR': '127.0.0.1', 'HTTP_HOST': '127.0.0.1',
           'REQUEST_METHOD': 'POST', 'PATH_INFO': '/v1/local/seh/activate',
           'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(body)),
           'HTTP_AUTHORIZATION': 'Bearer verified-admin', 'wsgi.input': BytesIO(body)}
    response_bytes = b''.join(api.wsgi_app(env, lambda status, h: (statuses.append(status), headers.extend(h))))
    result = json.loads(response_bytes)
    assert statuses[0] == '500 Internal Server Error'
    assert result == {'status': 'INCOMPLETE', 'failed_stage': 'resident_activation_unhandled',
                      'error_type': 'AttributeError', 'remote_mutation': 'UNKNOWN',
                      'outbound_tunnel_authenticated': False}
    assert b'bearer-secret' not in response_bytes


def test_cli_preserves_uncertain_non_json_activation_as_incomplete(monkeypatch):
    from main_computer.slh.authenticator_store import LocalIdentityStore

    class Response:
        def __init__(self, status, payload, content_type='application/json'):
            self.status = status
            self.data = payload
            self.content_type = content_type
        def read(self, limit):
            return self.data[:limit]
        def getheader(self, name, default=''):
            return self.content_type if name.lower() == 'content-type' else default

    calls = []

    class Connection:
        def __init__(self, host, port, timeout):
            assert host == '127.0.0.1' and port == 8897
        def request(self, method, path, body=None, headers=None):
            calls.append((method, path, headers))
        def getresponse(self):
            path = calls[-1][1]
            if path.endswith('/challenge'):
                return Response(200, json.dumps({'challenge_id': 'a'*32}).encode())
            if path.endswith('/verify'):
                return Response(200, b'{"access_token":"INTERNAL-ONLY"}')
            if path.endswith('/activate'):
                return Response(500, b'A server error occurred. Please contact administrator.', 'text/plain')
            return Response(200, b'{"ok":true}')
        def close(self):
            pass

    monkeypatch.setattr('http.client.HTTPConnection', Connection)
    monkeypatch.setattr(LocalIdentityStore, 'list', lambda self: {
        'identities': [{'device_id': 'admin-pc'}], 'invalid_identities': []})
    monkeypatch.setattr(LocalIdentityStore, 'sign', lambda self, dev, challenge: {'signature': 'signed'})
    args = _parser().parse_args(['activate-seh', '--execute-mutations',
                                 '--yes-i-know-this-mutates-seh'])
    result = run(args)
    assert result['status'] == 'INCOMPLETE'
    assert result['failed_stage'] == 'resident_non_json_response'
    assert result['resident_http_status'] == 500
    assert result['remote_mutation'] == 'UNKNOWN'
    assert 'INTERNAL-ONLY' not in json.dumps(result)
    assert calls[-1][1] == '/v1/auth/logout'


def test_cli_reads_complete_long_json_activation_receipt(monkeypatch):
    from main_computer.slh.authenticator_store import LocalIdentityStore

    class Response:
        status = 200
        def __init__(self, data):
            self.data = json.dumps(data).encode()
        def read(self, limit):
            return self.data[:limit]
        def getheader(self, name, default=''):
            return 'application/json' if name.lower() == 'content-type' else default

    calls = []

    class Connection:
        def __init__(self, host, port, timeout):
            pass
        def request(self, method, path, body=None, headers=None):
            calls.append(path)
        def getresponse(self):
            path = calls[-1]
            if path.endswith('/challenge'):
                return Response({'challenge_id': 'a'*32})
            if path.endswith('/verify'):
                return Response({'access_token': 'INTERNAL-ONLY'})
            if path.endswith('/activate'):
                return Response({'status': 'INCOMPLETE', 'failed_stage': 'git_source_publication',
                                 'git_repair_command': 'a'*20000})
            return Response({'ok': True})
        def close(self):
            pass

    monkeypatch.setattr('http.client.HTTPConnection', Connection)
    monkeypatch.setattr(LocalIdentityStore, 'list', lambda self: {
        'identities': [{'device_id': 'admin-pc'}], 'invalid_identities': []})
    monkeypatch.setattr(LocalIdentityStore, 'sign', lambda self, dev, challenge: {'signature': 'signed'})
    args = _parser().parse_args(['activate-seh', '--execute-mutations',
                                 '--yes-i-know-this-mutates-seh'])
    receipt = run(args)
    assert receipt['status'] == 'INCOMPLETE'
    assert len(receipt['git_repair_command']) == 20000


def test_unexpected_deleted_app_recovery_returns_incomplete_before_any_retry(tmp_path, monkeypatch):
    from main_computer.slh.seh_canonical_connection import SEHApplicationDeleted
    root, directory, controller, resolver = ready(tmp_path, monkeypatch)
    resolver.side_effect = SEHApplicationDeleted('Deleted Coolify UUID')

    def unexpected_recovery(*args, **kwargs):
        raise AttributeError('Coolify unexpected response SECRET-BEARER')

    monkeypatch.setattr('tools.seh_control.git_deployment.restore_deleted_canonical_application',
                        unexpected_recovery)
    before = mother.read(root, 'mainnet')[1].binding.generation
    result = controller.activate_seh(execute=True, token='verified-admin')
    assert result['status'] == 'INCOMPLETE'
    assert result['failed_stage'] == 'coolify_deleted_application_recovery_unhandled'
    assert result['error_type'] == 'AttributeError'
    assert result['remote_mutation'] == 'UNKNOWN'
    assert 'SECRET-BEARER' not in json.dumps(result)
    assert mother.read(root, 'mainnet')[1].binding.generation == before
    controller.close()
