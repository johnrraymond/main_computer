"""Real SLH TLS and authority carried through the SEH's opaque admin stream.

No UI/oracle, no Coolify remote mutation, and no SEH-owned identity database.
"""
from __future__ import annotations

import http.client
import json
import ssl
import threading

import pytest

from main_computer.mcel_seh_remote_client import RemoteSEHClient, SEHAdminHTTPSConnection
from main_computer.mcel_slh_network_client import NetworkConnectionError
from main_computer.seh.relay import SEHRelay
from main_computer.slh.authenticator import generate_identity, sign_object
from main_computer.slh.local_service import create_server
from main_computer.slh.seh_transport import SLHSEHConnector
from tests.test_slh_operational_service import port, provision, request, wait_until, disposable_slh_state


@pytest.fixture
def live_admin_tunnel(tmp_path, disposable_slh_state):
    state = tmp_path / 'slh'
    with create_server(state_dir=state, port=0) as local:
        thread = threading.Thread(target=local.serve_forever, daemon=True)
        thread.start()
        relay = None
        tunnel = None
        restricted_server = None
        restricted_thread = None
        try:
            key = provision(local.server_port, state)
            api_port, worker_port = port(), port()
            while worker_port == api_port:
                worker_port = port()
            # No Worker service is needed to validate the administrative TLS
            # boundary. The real ORG-SLH service still owns its admin sessions.
            token = _login_local(local.server_port, key)
            status, _ = request(local.server_port, 'PATCH', '/v1/local/organization/config',
                                {'host':'127.0.0.1','api_port':api_port,'worker_port':worker_port,
                                 'workers_enabled':False,'seh_enabled':False}, token=token)
            assert status == 200
            assert request(local.server_port, 'POST', '/v1/local/organization/enable', {}, token=token)[0] == 200
            wait_until(lambda: local.organization_controller.status()['listening'])
            # A dedicated, loopback-only listener with the same persisted
            # organizational authority, never the unrestricted API listener.
            from main_computer.slh.api import SLHAPI
            from main_computer.slh.authority import SLHAuthority
            from main_computer.slh.managed_gateway import transport_identity
            from main_computer.slh.organization_service import make_seh_identity_listener
            tls_context, fingerprint = transport_identity(state)
            restricted_authority = SLHAuthority(state)
            restricted_server = make_seh_identity_listener(
                authority=restricted_authority, api=SLHAPI(restricted_authority),
                tls_context=tls_context, fingerprint=fingerprint)
            assert restricted_server.server_address[0] == '127.0.0.1'
            assert restricted_server.server_port != api_port
            restricted_thread = threading.Thread(target=restricted_server.serve_forever, daemon=True)
            restricted_thread.start()
            slh_private, slh_public = generate_identity()
            relay = SEHRelay( slh_public_key=slh_public, port=0).start()
            url = f'ws://127.0.0.1:{relay.port}'
            tunnel = SLHSEHConnector(seh_url=url, 
                    identity_private_key=slh_private, gateway_port=worker_port,
                    admin_target=('127.0.0.1', restricted_server.server_port), allow_lab=True).connect()
            yield url, key, relay, api_port, state
        finally:
            if tunnel is not None:
                tunnel.stop()
            if relay is not None:
                relay.stop()
            if restricted_server is not None:
                restricted_server.shutdown()
                restricted_server.server_close()
            if restricted_thread is not None:
                restricted_thread.join(timeout=3)
            local.shutdown()
            thread.join(timeout=3)


def _login_local(port_number, key):
    code, challenge = request(port_number, 'POST', '/v1/auth/challenge',
                              {'device_id':'owner-device'})
    assert code == 200
    code, login = request(port_number, 'POST', '/v1/auth/verify',
                          {'device_id':'owner-device', 'challenge_id':challenge['challenge_id'],
                           'signature':sign_object(key, challenge)})
    assert code == 200
    return login['access_token']


def test_admin_login_and_identity_read_through_opaque_relay(tmp_path, live_admin_tunnel):
    url, key, relay, _, _ = live_admin_tunnel
    client = RemoteSEHClient(trust_path=tmp_path/'seh-pins.json',
                             signer=lambda device, challenge: sign_object(key, challenge))
    pairing = client.connect(url, 'owner-device', allow_lab=True)
    assert pairing['pairingRequired'] is True
    assert len(pairing['tlsSha256']) == 64
    connected = client.connect(url, 'owner-device',
                               confirm_pin=pairing['tlsSha256'], allow_lab=True)
    assert connected['role'] == 'admin'
    ticket = connected['sessionTicket']
    result = client.admin_request(ticket, 'GET', '/v1/admin/devices')
    assert any(row['id'] == 'owner-device' for row in result['devices'])
    # A separate enrollment request is adjudicated by the actual ORG-SLH
    # authority, reached via opaque relay. The SEH neither stores it nor votes.
    from main_computer.slh.authority import SLHAuthority
    applicant_key, applicant_public = generate_identity()
    statement = {'selected_admin':'owner','device_id':'applicant-one',
                 'public_key':applicant_public, 'kind':'worker','device_name':'Applicant',
                 'claimed_user':'Applicant','requested_role':'worker',
                 'client_nonce':'a'*32}
    statement.update({'version':1,'action':'slh.device.enrollment.request',
                      'audience':'slh://'+SLHAuthority(live_admin_tunnel[4]).service_fingerprint()})
    status, pending = request(live_admin_tunnel[3], 'POST', '/v1/enrollment/requests',
                              {**{k:statement[k] for k in ('selected_admin','device_id','public_key',
                               'kind','device_name','claimed_user','requested_role','client_nonce')},
                               'proof':sign_object(applicant_key, statement)},tls=True)
    assert status == 201, pending
    request_id = pending['request_id']
    assert any(x['id'] == request_id for x in client.admin_request(ticket,'GET','/v1/admin/enrollments')['enrollments'])
    rejected = client.admin_request(ticket, 'POST', f'/v1/admin/enrollments/{request_id}/deny', {})
    assert rejected['status'] == 'DENIED'
    assert all(x['id'] != request_id or x['status'] != 'pending'
               for x in client.admin_request(ticket,'GET','/v1/admin/enrollments')['enrollments'])
    with pytest.raises(NetworkConnectionError) as lifecycle:
        client.seh_connection_request(ticket)
    assert lifecycle.value.code == 'SEH_OPERATION_NOT_ALLOWED'
    with pytest.raises(NetworkConnectionError) as lifecycle:
        client.seh_connection_request(ticket, action='stop')
    assert lifecycle.value.code == 'SEH_OPERATION_NOT_ALLOWED'
    with pytest.raises(NetworkConnectionError) as prohibited:
        client.admin_request(ticket, 'POST', '/v1/local/organization/disable', {})
    assert prohibited.value.code == 'NETWORK_OPERATION_NOT_ADMITTED'
    assert client.disconnect(ticket)['authenticationState'] == 'DISCONNECTED'
    with pytest.raises(NetworkConnectionError) as invalid:
        client.admin_request(ticket, 'GET', '/v1/admin/devices')
    assert invalid.value.status == 401
    assert relay.tunnel_ready



def test_remote_seh_has_no_discovery_dependency(live_admin_tunnel, tmp_path, monkeypatch):
    url, key, relay, _, _ = live_admin_tunnel
    import main_computer.mcel_seh_remote_client as module
    assert not hasattr(module, '_discover_slh_id')
    client = RemoteSEHClient(trust_path=tmp_path/'seh-pin.json',
                             signer=lambda device, challenge: sign_object(key, challenge))
    pairing = client.connect(url, 'owner-device', allow_lab=True)
    assert pairing['pairingRequired'] is True
    assert relay.tunnel_ready


def test_anonymous_admin_transport_gets_no_org_records(live_admin_tunnel):
    url, _, relay, _, _ = live_admin_tunnel
    conn = SEHAdminHTTPSConnection('127.0.0.1', 443, seh_url=url, 
                      allow_lab=True, timeout=5, context=ssl._create_unverified_context())
    try:
        conn.request('GET', '/v1/admin/devices')
        response = conn.getresponse()
        assert response.status == 401
        assert json.loads(response.read())['error']['code'] in ('AUTH_REQUIRED','SESSION_REQUIRED')
    finally:
        conn.close()
    assert relay.tunnel_ready


def test_old_routed_admin_path_rejected_by_relay(live_admin_tunnel):
    url, _, _, _, _ = live_admin_tunnel
    from websockets.sync.client import connect
    with connect(url+'/v1/seh/admin/forged') as ws:
        with pytest.raises(Exception):
            ws.recv(timeout=3)


def test_seh_remote_input_validation_and_no_unencrypted_public_transport():
    from main_computer.mcel_seh_remote_client import _seh_endpoint
    for endpoint in ('http://seh.example.org', 'ws://seh.example.org',
                     'wss://user:secret@seh.example.org', 'wss://seh.example.org/private',
                     'wss://seh.example.org/#fragment', 'wss://seh.example.org/?foo=bar'):
        with pytest.raises(NetworkConnectionError):
            _seh_endpoint(endpoint)
    assert _seh_endpoint('wss://seh.example.org') == 'wss://seh.example.org'


def test_org_slh_admin_outbound_lifecycle_persists_and_does_not_stop_org(tmp_path):
    """An authenticated admin can stop/restart ONLY the owning SLH outbound tunnel."""
    from types import SimpleNamespace
    from main_computer.slh.managed_gateway import transport_identity
    from main_computer.slh.organization_service import OrganizationHTTPHandler, OrganizationHTTPServer
    from main_computer.slh.organization_state import save_config, read_config
    import time

    state = tmp_path / 'owner-slh'
    state.mkdir()
    save_config(state, {'schema':'main-computer.slh-organization-config.v1',
                        'configured':True,'enabled':True,'activation_confirmed':True,
                        'host':'127.0.0.1','api_port':port(),'worker_port':port(),
                        'workers_enabled':True,'seh_enabled':True})
    ctx, fingerprint = transport_identity(state)
    server = OrganizationHTTPServer(('127.0.0.1', 0), OrganizationHTTPHandler)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    server.state_dir = state
    server.fingerprint = fingerprint
    class Authority:
        def authenticate(self, token, *, admin=False):
            if token != 'admin':
                from main_computer.slh.authority import SLHError
                raise SLHError('AUTH_REQUIRED', 401)
            return {'role':'admin', 'user_id':'owner'}
    class Connection:
        stopped = False
        def stop(self):
            self.stopped = True
        def connection_diagnostics(self):
            return {'connected':not self.stopped, 'phase':'AUTHENTICATED'}
    class Gateway:
        def __init__(self):
            self.seh_connection = Connection()
            self.allow_seh = True
            self.started = 0
        def start_canonical_seh(self):
            if self.seh_connection is None:
                self.started += 1
                self.seh_connection = Connection()
            return self.seh_connection
    server.authority = Authority()
    server.gateway = Gateway()
    server.workers_enabled = True
    server.seh_enabled = True
    server.pinky_sweeper = None
    server.private_requests = None
    server.seh_activation_error = None
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    bind_port = server.server_address[1]
    try:
        assert request(bind_port,'GET','/v1/org/admin/seh/connection',tls=True)[0] == 401
        before = read_config(state)
        assert request(bind_port,'POST','/v1/org/admin/seh/connection',
                       {'action':'stop'},tls=True)[0] == 401
        assert read_config(state) == before
        assert request(bind_port,'POST','/v1/org/admin/seh/connection',
                       {'action':'stop','target':'another-slh'},token='admin',tls=True)[0] == 400
        assert read_config(state) == before
        code, stopped = request(bind_port,'POST','/v1/org/admin/seh/connection',
                                {'action':'stop'},token='admin',tls=True)
        assert code == 200 and stopped['status'] == 'DISCONNECTION_SCHEDULED'
        assert read_config(state)['seh_enabled'] is False
        wait_until(lambda: server.gateway.seh_connection is None)
        assert request(bind_port,'GET','/v1/org/status',tls=True)[0] == 200
        code, started = request(bind_port,'POST','/v1/org/admin/seh/connection',
                                {'action':'start'},token='admin',tls=True)
        assert code == 200 and started['status'] == 'CONNECTION_STARTED'
        assert read_config(state)['seh_enabled'] is True
        assert server.gateway.seh_connection is not None
        code, restarted = request(bind_port,'POST','/v1/org/admin/seh/connection',
                                  {'action':'restart'},token='admin',tls=True)
        assert code == 200 and restarted['status'] == 'RESTART_SCHEDULED'
        wait_until(lambda: server.gateway.started == 2)
        assert read_config(state)['seh_enabled'] is True
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_org_slh_failed_start_rolls_back_policy(tmp_path):
    from main_computer.slh.managed_gateway import transport_identity
    from main_computer.slh.organization_service import OrganizationHTTPHandler, OrganizationHTTPServer
    from main_computer.slh.organization_state import save_config, read_config

    state = tmp_path / 'slh'
    state.mkdir()
    save_config(state, {'schema':'main-computer.slh-organization-config.v1',
                        'configured':True,'enabled':True,'activation_confirmed':True,
                        'host':'127.0.0.1','api_port':port(),'worker_port':port(),
                        'workers_enabled':True,'seh_enabled':False})
    ctx, fingerprint = transport_identity(state)
    server = OrganizationHTTPServer(('127.0.0.1',0),OrganizationHTTPHandler)
    server.socket = ctx.wrap_socket(server.socket,server_side=True)
    server.state_dir = state
    server.fingerprint = fingerprint
    class Authority:
        def authenticate(self, token, *, admin=False):
            return {'role':'admin','user_id':'owner'}
    class BrokenGateway:
        seh_connection = None
        allow_seh = False
        def start_canonical_seh(self):
            raise RuntimeError('Refused unverified canonical deployment')
    server.authority = Authority()
    server.gateway = BrokenGateway()
    server.workers_enabled = True
    server.seh_enabled = False
    server.pinky_sweeper = None
    server.private_requests = None
    server.seh_activation_error = None
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        code, result = request(server.server_address[1], 'POST','/v1/org/admin/seh/connection',
                               {'action':'start'},token='admin',tls=True)
        assert code == 409 and result['error']['code'] == 'SEH_CONNECTION_OPERATION_REFUSED'
        assert read_config(state)['seh_enabled'] is False
        assert server.gateway.allow_seh is False
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_local_seh_disconnect_requires_admin_and_preserves_org_service(tmp_path):
    from main_computer.slh.api import SLHAPI
    from main_computer.slh.authority import SLHError
    from main_computer.slh.organization_controller import OrganizationController
    from main_computer.slh.organization_state import save_config, read_config

    state = tmp_path / 'owned'
    state.mkdir()
    save_config(state, {'schema':'main-computer.slh-organization-config.v1',
                        'configured':True,'enabled':True,'activation_confirmed':True,
                        'host':'127.0.0.1','api_port':19003,'worker_port':19004,
                        'workers_enabled':True,'seh_enabled':True})
    class Auth:
        directory = state
        def authenticate(self, token, *, admin=False):
            if token != 'admin':
                raise SLHError('AUTH_REQUIRED', 401)
            return {'role':'admin','user_id':'owner'}
        def initialized(self):
            return True
    controller = OrganizationController(Auth(), automatic_start=False)
    api = SLHAPI(Auth(), organization_controller=controller)
    try:
        assert api.call('POST','/v1/local/seh/disconnect',{},token='',loopback=True)[0] == 401
        assert api.call('POST','/v1/local/seh/disconnect',{'host':'another'},
                        token='admin',loopback=True)[0] == 400
        assert read_config(state)['seh_enabled'] is True
        code, response = api.call('POST','/v1/local/seh/disconnect',{},
                                   token='admin',loopback=True)
        assert code == 200 and response['status'] == 'DISCONNECTION_REQUESTED'
        persisted = read_config(state)
        assert persisted['seh_enabled'] is False
        assert persisted['enabled'] is True
        assert persisted['workers_enabled'] is True
        assert api.call('POST','/v1/local/seh/disconnect',{},token='admin',loopback=False)[0] == 403
    finally:
        controller.close()


def _raw_seh_request(url, method, path, body=None, *, token=''):
    """Deliberately bypass MCEL's client allowlist: test server-side fencing."""
    conn = SEHAdminHTTPSConnection('127.0.0.1', 443, seh_url=url,
                   allow_lab=True, timeout=5,
                  context=ssl._create_unverified_context())
    try:
        encoded = None if body is None else json.dumps(body).encode('utf-8')
        headers = {'Accept':'application/json'}
        if encoded is not None:
            headers['Content-Type'] = 'application/json'
        if token:
            headers['Authorization'] = f'Bearer {token}'
        conn.request(method, path, body=encoded, headers=headers)
        response = conn.getresponse()
        return response.status, json.loads(response.read())
    finally:
        conn.close()


def test_admin_with_real_signature_cannot_escape_seh_identity_boundary(live_admin_tunnel):
    url, key, _, api_port, state = live_admin_tunnel
    status, challenge = _raw_seh_request(url, 'POST', '/v1/auth/challenge',
                                          {'device_id':'owner-device'})
    assert status == 200
    status, result = _raw_seh_request(url, 'POST', '/v1/auth/verify', {
        'device_id':'owner-device', 'challenge_id':challenge['challenge_id'],
        'signature':sign_object(key, challenge)})
    assert status == 200
    token = result['access_token']
    assert _raw_seh_request(url,'GET','/v1/auth/session',token=token)[0] == 200
    assert _raw_seh_request(url,'GET','/v1/admin/devices',token=token)[0] == 200
    assert _raw_seh_request(url,'GET','/v1/org/status')[1] == {
        'schema':'main-computer.slh-org-status.v1','operational':True}

    from main_computer.slh.organization_state import read_config
    from main_computer.slh.authority import SLHAuthority
    before_config = read_config(state)
    before_policy = SLHAuthority(state).policy()
    # These are valid routes on the full/direct service, not SEH privileges.
    prohibited = (
        ('GET','/v1/org/admin/services',None),
        ('GET','/v1/org/admin/seh/connection',None),
        ('POST','/v1/org/admin/seh/connection',{'action':'stop'}),
        ('POST','/v1/org/admin/seh/activate',{}),
        ('PATCH','/v1/org/admin/marketplace/config',{'changes':{},'expected_revision':0}),
        ('GET','/v1/org/admin/marketplace/status',None),
        ('PATCH','/v1/admin/scheduler/policy',{'mode':'private_first'}),
        ('GET','/v1/admin/scheduler/status',None),
        ('PATCH','/v1/admin/pinky-promise/policy',{'anything':True}),
        ('POST','/v1/admin/root/identities',{}),
        ('POST','/v1/accounting/sweep',{}),
        ('POST','/v1/work',{}),
        ('GET','/v1/wallets',None),
        ('POST','/v1/local/organization/disable',{}),
        ('POST','/v1/local/organization/stop',{}),
        ('GET','/v1/admin/audit',None),
        ('POST','/v1/admin/enrollments/'+'a'*32+'/inspect',{}),
        ('GET','/v1/admin/devices?ignore=1',None),
    )
    for method,path,body in prohibited:
        status, response = _raw_seh_request(url,method,path,body,token=token)
        assert (status, response['error']['code']) == (403,'SEH_OPERATION_NOT_ALLOWED'), (method,path,status,response)
    for body in ({'physical_inspection_required':False},
                 {'physical_inspection_required':False,'remove_trash_after_30_days':True},
                 {'remove_trash_after_30_days':True,'extra':False},
                 {'remove_trash_after_30_days':'true'}, {}):
        status, response = _raw_seh_request(url,'PATCH','/v1/admin/policies',body,token=token)
        assert (status,response['error']['code']) == (403,'SEH_OPERATION_NOT_ALLOWED')
    assert read_config(state) == before_config
    assert SLHAuthority(state).policy() == before_policy
    status, policy = _raw_seh_request(url,'PATCH','/v1/admin/policies',
                        {'remove_trash_after_30_days':True},token=token)
    assert status == 200 and policy['remove_trash_after_30_days'] is True
    assert SLHAuthority(state).policy()['physical_inspection_required'] == before_policy['physical_inspection_required']
    assert SLHAuthority(state).policy()['remove_trash_after_30_days'] is True
    assert _raw_seh_request(url,'POST','/v1/auth/logout',{},token=token)[0] == 200
    assert _raw_seh_request(url,'GET','/v1/admin/devices',token=token)[0] == 401


def test_seh_allowlist_rejects_non_identity_routes_and_fuzzy_paths():
    from main_computer.slh.organization_service import seh_identity_operation_allowed
    assert seh_identity_operation_allowed('POST','/v1/admin/enrollments/'+'a'*32+'/deny')
    assert seh_identity_operation_allowed('POST','/v1/admin/devices/worker-one/reactivate')
    for method, path in (
        ('GET','/v1/org/admin/services'),('POST','/v1/org/admin/seh/activate'),
        ('GET','/v1/admin/devices?bypass=1'),('GET','/v1/admin/devices/../audit'),
        ('POST','/v1/admin/enrollments/'+'a'*33+'/deny'),
        ('GET','/v1/admin/scheduler/policy'),('POST','/v1/auth/people/challenge'),
        ('GET','/v1/capabilities'),('GET','/openapi.json')):
        assert not seh_identity_operation_allowed(method,path)


def test_admin_stream_target_requires_literal_loopback():
    from main_computer.slh.seh_transport import SLHSEHConnector
    signing_key, _ = generate_identity()
    for unsafe in ('0.0.0.0', 'localhost', '192.168.1.25', '::1'):
        with pytest.raises(ValueError, match='loopback only'):
            SLHSEHConnector(seh_url='ws://127.0.0.1:9999',
                 identity_private_key=signing_key,
                gateway_port=8898, admin_target=(unsafe, 8899), allow_lab=True)


def test_resident_service_wires_only_the_restricted_identity_listener(tmp_path, monkeypatch):
    """Exercise production _run_ready_service wiring, not just a test connector."""
    import os
    import psutil
    from types import SimpleNamespace
    from main_computer.slh import organization_service as org
    from main_computer.slh.organization_state import DEFAULTS

    servers = []
    gateways = []
    actual_server_cls = org.OrganizationHTTPServer

    def recording_server(*args, **kwargs):
        server = actual_server_cls(*args, **kwargs)
        servers.append(server)
        return server

    class FakeGateway:
        def __init__(self, authority, **options):
            self.seh_admin_target = options['seh_admin_target']
            self.seh_connection = None
            self._server = object()  # already bound, for gateway readiness
            self.scheduler = SimpleNamespace(credit_provider=None)
            gateways.append(self)

        def start_background(self):
            return threading.current_thread()

        def stop(self):
            pass

    class FakeRequests:
        def __init__(self, *args, **kwargs):
            pass
        def net_credit_status(self):
            return {}

    class FakeService:
        def __init__(self, *args, **kwargs):
            pass
        def start(self):
            pass
        def stop(self):
            pass
        def close(self):
            pass
        def wake(self):
            pass

    class FakeMarketplace(FakeService):
        def __init__(self,*args,**kwargs):
            super().__init__()

    monkeypatch.setattr(org, 'OrganizationHTTPServer', recording_server)
    monkeypatch.setattr(org, 'SLHManagedGateway', FakeGateway)
    monkeypatch.setattr(org, 'PrivateRequests', FakeRequests)
    monkeypatch.setattr(org, 'PinkySweep', FakeService)
    monkeypatch.setattr(org, 'SLHMarketplaceProxy', FakeMarketplace)
    monkeypatch.setattr(org, 'AutomaticMarketplace', FakeService)
    monkeypatch.setattr(org, '_refresh_outbound_seh', lambda server: None)

    state = tmp_path / 'resident'
    state.mkdir()
    class Authority:
        def ensure_local_auth_key(self):
            pass
    authority = Authority()
    config = {**DEFAULTS, 'host':'127.0.0.1', 'api_port':port(),
              'worker_port':port(), 'workers_enabled':True, 'seh_enabled':False}
    parent = psutil.Process(os.getpid())
    failure = []
    def run_service():
        try:
            org._run_ready_service(state, parent.pid, parent.create_time(), authority, config)
        except BaseException as exc:
            failure.append(exc)

    thread = threading.Thread(target=run_service, daemon=True)
    thread.start()
    try:
        wait_until(lambda: len(servers) == 2 and len(gateways) == 1)
        main, restricted = servers
        gateway = gateways[0]
        assert main.seh_identity_only is False
        assert restricted.seh_identity_only is True
        assert restricted.authority is main.authority is authority
        assert restricted.api is main.api
        assert restricted.fingerprint == main.fingerprint
        assert restricted.server_address[0] == '127.0.0.1'
        assert gateway.seh_admin_target == ('127.0.0.1',restricted.server_port)
        assert gateway.seh_admin_target[1] != main.server_port
        assert request(restricted.server_port, 'GET','/v1/org/status',tls=True)[0] == 200
        status, result = request(restricted.server_port, 'POST',
                                 '/v1/org/admin/seh/connection',
                                 {'action':'stop'},token='admin',tls=True)
        assert status == 403 and result['error']['code'] == 'SEH_OPERATION_NOT_ALLOWED'
    finally:
        if servers:
            servers[0].shutdown()
        thread.join(timeout=8)
    assert not thread.is_alive()
    assert not failure, failure
