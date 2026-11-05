"""Permanent local security, admission, recovery and readiness regressions."""
from __future__ import annotations

import json
import socket
import threading
import time
from types import SimpleNamespace

import pytest
from websockets.sync.client import connect

from main_computer.seh import relay as relay_module
from main_computer.seh.relay import SEHRelay
from main_computer.slh.authenticator import generate_identity
from main_computer.slh.authority import SLHError
from main_computer.slh.marketplace_auto import _check_url, read_settings, CONFIG_NAME, DEFAULT
from main_computer.slh.managed_gateway import SLHMarketplaceProxy
from main_computer.slh.seh_transport import SLHSEHConnector
from main_computer.slh.seh_autoconnect import SEHAutoConnection, SEHConnectionSettings
from main_computer.slh.organization_controller import OrganizationController
from main_computer.viewport_routes_energy import _WorkerHubLiveSessionClient
from tools.seh_control.git_deployment import SEHRemoteUnreachable, SEHGitError, _api, _registered_application


def wait(predicate, seconds=3):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(.01)
    return False


@pytest.mark.parametrize('url', ['http://127.0.0.1:8872', 'http://[::1]:8872',
                                 'https://external.example:8872'])
def test_loopback_http_and_remote_https_allowed(url):
    assert _check_url(url) is None


@pytest.mark.parametrize('url', ['http://192.168.1.10:8872', 'http://external.example',
                                 'http://localhost:8872', 'http://127.0.0.2.evil.test',
                                 'http://user:password@127.0.0.1:8872',
                                 'http://[bad:ip]', 'ftp://127.0.0.1:8872'])
def test_unsafe_marketplace_urls_denied(url):
    with pytest.raises(SLHError, match='INVALID_MARKETPLACE_HUB_URL'):
        _check_url(url)


def test_previously_saved_unsafe_url_fails_closed_without_mutation(tmp_path):
    path = tmp_path / CONFIG_NAME
    raw = json.dumps({**DEFAULT, 'hub_url':'http://192.168.1.10:8872'})
    path.write_text(raw)
    with pytest.raises(SLHError, match='INVALID_MARKETPLACE_HUB_URL'):
        read_settings(tmp_path)
    assert path.read_text() == raw


def test_proxy_and_raw_msk_socket_refuse_before_network(monkeypatch):
    calls = []
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **kw: calls.append((a,kw)))
    auth = {'kind': 'multisession_key', 'multisession_key_id': 'private'}
    proxy = SLHMarketplaceProxy(SimpleNamespace(scheduler=object()))
    with pytest.raises(SLHError):
        proxy.connect_device('worker', hub_url='http://192.168.1.10:8872', auth=auth)
    client = object.__new__(_WorkerHubLiveSessionClient)
    client.hub_url = 'http://192.168.1.10:8872'
    client.auth_message = {'multisession_authorization': auth}
    client.timeout_s = 1
    with pytest.raises(SLHError):
        client._open_socket()
    assert calls == []


def test_relay_pending_deadline_and_gateway_proof(monkeypatch):
    # Real local WebSocket relay, P-256 SLH tunnel, and TCP gateway connection.
    # Shorten just this instance's admission timeout to avoid a 30s test.
    monkeypatch.setattr(relay_module, 'PREAUTH_DEADLINE_SECONDS', 0.35)
    monkeypatch.setattr(relay_module, 'MAX_PENDING_CLIENTS', 1)
    signer, public = generate_identity()
    with socket.socket() as listening:
        listening.bind(('127.0.0.1', 0)); listening.listen(10)
        gateway_port = listening.getsockname()[1]
        relay = SEHRelay(slh_id='lab', slh_public_key=public, host='127.0.0.1', port=0).start()
        conn = SLHSEHConnector(seh_url=f'ws://127.0.0.1:{relay.port}', slh_id='lab',
                               identity_private_key=signer, gateway_port=gateway_port,
                               allow_lab=True).connect()
        uri = f'ws://127.0.0.1:{relay.port}/v1/seh/client/lab'
        try:
            with connect(uri) as unauth:
                assert wait(lambda: len(relay._pending_clients) == 1)
                with connect(uri) as rejected:
                    with pytest.raises(Exception):
                        rejected.recv(timeout=1)
                # Even sending data cannot restart the absolute deadline.
                for _ in range(2):
                    unauth.send(b'not-a-proof')
                    time.sleep(.06)
                assert wait(lambda: len(relay._pending_clients) == 0, seconds=2)
                with pytest.raises(Exception):
                    unauth.recv(timeout=2)
            assert wait(lambda: len(relay._clients) == 0)
            with connect(uri) as authenticated:
                assert wait(lambda: len(conn._streams) == 1)
                local = next(iter(conn._streams.values()))
                source_port = local.getsockname()[1]
                assert conn.mark_worker_authenticated(-1) is False
                assert len(relay._pending_clients) == 1
                # Only the matching local stream can be authorized by SLH.
                assert conn.mark_worker_authenticated(source_port) is True
                assert wait(lambda: not relay._pending_clients)
                time.sleep(.45)
                authenticated.ping().wait(timeout=2)
        finally:
            conn.stop()
            relay.stop()
        assert not relay._pending_clients


class FakeConnector:
    instances = []
    def __init__(self, **kwargs):
        self._running = threading.Event()
        self._lock = threading.RLock()
        self._stream_events = []
        self.last_error = None
        self._running.set()
        FakeConnector.instances.append(self)
    def connect(self):
        return self
    def stop(self):
        self._running.clear()


def test_coolify_fallback_is_bounded_and_local_binding_checked(tmp_path, monkeypatch):
    settings = SEHConnectionSettings(True, 'wss://external.example', 'existing-route', False, .1)
    state = {'offline':False, 'binding': ('uuid', 'identity', 'deployment', b'key'), 'denied': False}
    calls = []
    def resolver(_):
        calls.append(1)
        if state['denied']:
            raise SEHGitError('HTTP 403')
        if state['offline']:
            raise SEHRemoteUnreachable('transport unavailable')
        return settings
    FakeConnector.instances.clear()
    monkeypatch.setattr('main_computer.slh.seh_autoconnect.SLHSEHConnector', FakeConnector)
    monkeypatch.setattr(SEHAutoConnection, '_local_canonical_binding', lambda self: state['binding'])
    # Canonical resolver normally reads its durable private signing key.
    from main_computer.slh.authenticator import generate_identity
    signer, _ = generate_identity()
    # Tests use the actual key object, and bypass only disk availability here.
    monkeypatch.setattr('main_computer.slh.seh_autoconnect._load_service_signer', lambda _: signer)
    supervisor = SEHAutoConnection(tmp_path, gateway_port=1,
                                   settings_resolver=resolver).start()
    try:
        assert wait(lambda: supervisor.connections == 1)
        state['offline'] = True
        FakeConnector.instances[-1].stop()
        assert wait(lambda: supervisor.connections == 2)
        assert supervisor.connection_diagnostics()['phase'] == 'LAST_VERIFIED_BINDING_FALLBACK'
        assert supervisor._fallback_started_at is not None
        # Success under fallback cannot renew its fixed recovery budget.
        supervisor._fallback_started_at -= 301
        FakeConnector.instances[-1].stop()
        assert wait(lambda: supervisor.failures > 0)
        time.sleep(.2)
        assert supervisor.connections == 2
        # A recovered strict read can re-establish normal operation.
        state['offline'] = False
        assert wait(lambda: supervisor.connections == 3)
        assert supervisor.connection_diagnostics()['phase'] == 'AUTHENTICATED'
        state['offline'] = True
        state['binding'] = ('changed-uuid', 'identity', 'deployment', b'key')
        FakeConnector.instances[-1].stop()
        assert wait(lambda: supervisor.failures > 1)
        assert supervisor.connections == 3
        state['offline'] = False
        state['denied'] = True
        time.sleep(.2)
        assert supervisor.connections == 3
    finally:
        supervisor.stop()
    assert supervisor._last_strict_settings is None


def test_coolify_status_zero_classified_but_403_and_404_not_fallback():
    class Response:
        def __init__(self, status):
            self.status=status
            self.ok=False
    for status in (0,403,404):
        class Client:
            def request(self, *args):return Response(status)
        expected = SEHRemoteUnreachable if status == 0 else SEHGitError
        with pytest.raises(expected) as e:
            _api(Client(), 'GET', '/api/v1/applications/uuid')
        if status != 0:
            assert type(e.value) is SEHGitError
        if status != 404:
            with pytest.raises(expected):
                _registered_application(Client(), 'uuid')
        else:
            assert _registered_application(Client(), 'uuid') is None


def test_unverified_coolify_failure_never_uses_fallback(tmp_path, monkeypatch):
    from main_computer.slh.authenticator import generate_identity
    signer,_ = generate_identity()
    monkeypatch.setattr('main_computer.slh.seh_autoconnect._load_service_signer', lambda _: signer)
    state = {'calls':0}
    def resolver(_):
        state['calls'] += 1
        raise SEHRemoteUnreachable('offline')
    monkeypatch.setattr('main_computer.slh.seh_autoconnect.SLHSEHConnector',
                        lambda **kw: pytest.fail('No unverified SEH connection'))
    conn = SEHAutoConnection(tmp_path, gateway_port=1, settings_resolver=resolver).start()
    try:
        assert wait(lambda: conn.failures >= 1)
        assert not conn.connected
    finally:
        conn.stop()


def test_authenticated_tunnel_is_not_a_claim_of_settlement():
    controller = object.__new__(OrganizationController)
    controller._lock = threading.RLock()
    controller.config = lambda: {'enabled': True, 'seh_enabled': True, 'workers_enabled': True}
    controller._seh_child_status = lambda: {
        'seh_enabled': True, 'seh_connection_verified': True,
        'worker_serving_enabled': True, 'private_requests_available': False,
        'pinky_sweeper_available': True, 'pinky_sweeper_running': False,
        'pinky_sweep_last_attempt_ns': 123, 'pinky_sweep_last_error': 'NO_SIGNER',
        'pinky_sweep_last_confirmed': False}
    status = controller.seh_status()
    assert status['status'] == 'NOT_CONNECTED'
    assert status['outbound_tunnel_authenticated'] is True
    assert status['pinky_sweeper_available'] is True
    assert status['pinky_sweeper_running'] is False
    assert status['pinky_sweep_last_confirmed'] is False
    assert status['pinky_sweep_last_error'] == 'NO_SIGNER'
    assert 'wallet' not in json.dumps(status).lower()


def test_canonical_fallback_binding_reads_fresh_mother_state_and_signer(tmp_path, monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from tools.slh_seh_connector import service_identity
    from tools.seh_control import git_deployment as git
    from main_computer.slh.organization_state import save_config, DEFAULTS
    state_dir = tmp_path / 'slh'
    service_identity(state_dir, create=True)
    save_config(state_dir, {**DEFAULTS, 'configured': True, 'enabled': True,
                            'activation_confirmed': True, 'workers_enabled': True,
                            'seh_enabled': True})
    record = {'coolify': {'controller': {'enabled': True}},
              'deployment': {'enabled': True, 'application_uuid': 'verified-uuid',
                             'git': {'server_uuid': 'fixed-server'}}}
    control = {'enabled': True}
    monkeypatch.setattr('main_computer.slh.storage.machine_root', lambda: tmp_path)
    monkeypatch.setattr(git, '_canonical_selection',
                        lambda root, network: (record, control, 44, 'verified-mother-binding'))
    auto = SEHAutoConnection(state_dir, gateway_port=1,
                             settings_resolver=lambda _: SEHConnectionSettings(
                                 True, 'wss://external.example', 'existing-route'))
    initial = auto._local_canonical_binding()
    assert initial[0] == 'verified-uuid'
    record['deployment']['application_uuid'] = 'different-uuid'
    assert auto._local_canonical_binding() != initial
    record['deployment']['application_uuid'] = 'verified-uuid'
    record['deployment']['enabled'] = False
    with pytest.raises(ValueError, match='disabled'):
        auto._local_canonical_binding()
    record['deployment']['enabled'] = True
    control['enabled'] = False
    with pytest.raises(ValueError, match='disabled'):
        auto._local_canonical_binding()
    control['enabled'] = True
    other_key, _ = generate_identity()
    key_path = state_dir / 'seh-service-signing-key.pem'
    key_path.write_bytes(other_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    with pytest.raises(ValueError, match='identity changed'):
        auto._local_canonical_binding()
