"""Disposable signed enrollment, administration, and managed Worker transport.

The same applicant key traverses enrollment, approval, session, gateway and
revocation. No installed identity, wallet, deployed SEH or credits are used.
"""
from functools import partial
import secrets

import pytest

from main_computer.mcel_slh_network_client import (
    NetworkSlhClient, NetworkConnectionError, _connection, _json_request,
)
from main_computer.mcel_seh_remote_client import RemoteSEHClient, SEHAdminHTTPSConnection
from main_computer.slh.authenticator import sign_object, public_key_fingerprint
from main_computer.slh.authority import SLHAuthority
from main_computer.slh.managed_client import ManagedSLHWorkerClient
from main_computer.slh.managed_gateway import SLHManagedGateway, transport_identity
from main_computer.slh.organization_state import read_config
from main_computer.slh.seh_transport import SEHLocalClientAdapter
from tools.slh_authenticator_cli import load_identity
from main_computer.slh.authenticator_store import LocalIdentityStore
from main_computer.mcel_worker_enrollment import WorkerEnrollment
from main_computer.mcel_slh_identity_notebook import execute_notebook
from tests.test_seh_remote_admin_backend import live_admin_tunnel  # pytest fixture
from tests.test_slh_operational_service import disposable_slh_state, wait_until


@pytest.mark.parametrize('transport', ['slh', 'seh'])
def test_signed_enrollment_to_connected_worker_and_revocation(tmp_path, live_admin_tunnel, transport):
    relay_url, admin_key, relay, api_port, state = live_admin_tunnel
    authority = SLHAuthority(state)
    _, pin = transport_identity(state)
    identity_dir = tmp_path / 'applicant'
    did = 'enrollment-e2e-' + secrets.token_hex(6)
    store = LocalIdentityStore(identity_dir)
    store.create(did, label="Disposable E2E Worker")
    key, public = load_identity(identity_dir, did)
    factory = (partial(SEHAdminHTTPSConnection, seh_url=relay_url, allow_lab=True)
               if transport == 'seh' else None)
    def call(method, path, body=None, token=''):
        conn, actual_pin = _connection('127.0.0.1', api_port, pin, connection_factory=factory)
        try:
            assert actual_pin == pin
            return _json_request(conn, method, path, body, token)
        finally:
            conn.close()
    class LMS:
        _identity = staticmethod(store.get)
        _sign = staticmethod(lambda device, statement: store.sign(device, statement)['signature'])
    bridge = WorkerEnrollment(lms=LMS(), connection=lambda kind, endpoint: call,
                              journal=tmp_path/'enrollments.sqlite3')
    args = {'deviceId':did,'transport':transport,'endpoint':'fixture'}
    def session():
        challenge = call('POST', '/v1/auth/challenge', {'device_id': did})
        return call('POST', '/v1/auth/verify', {'device_id': did,
                    'challenge_id': challenge['challenge_id'], 'signature': sign_object(key, challenge)})
    signer = lambda device, challenge: sign_object(admin_key, challenge)
    if transport == 'seh':
        admin = RemoteSEHClient(trust_path=tmp_path/'pins.json', signer=signer)
        connect = lambda **kw: admin.connect(relay_url, 'owner-device', allow_lab=True, **kw)
    else:
        admin = NetworkSlhClient(trust_path=tmp_path/'pins.json', signer=signer)
        # Production Network SLH rejects loopback by design; use the
        # already validated disposable fixture endpoint only in this test.
        with pytest.raises(NetworkConnectionError) as refused:
            admin.connect('127.0.0.1', api_port, 'owner-device')
        assert refused.value.code == 'PRIVATE_NETWORK_ADDRESS_REQUIRED'
        connect = lambda **kw: admin._connect_validated('127.0.0.1', api_port, 'owner-device',
                                                        label=f'127.0.0.1:{api_port}', **kw)
    assert connect()['pairingRequired'] is True
    connected = connect(confirm_pin=pin)
    assert connected['role'] == 'admin'
    ticket = connected['sessionTicket']
    gateway = None
    worker = None
    adapter = None
    worker_token = None
    try:
        assert any(a['id'] == 'owner' for a in call('GET', '/v1/enrollment/admins')['admins'])
        policy = admin.admin_request(ticket, 'GET', '/v1/admin/policies')
        assert policy['physical_inspection_required'] is False
        assert bridge.execute('status',args)['status']=='ACCESS_NOT_VERIFIED'
        pending = bridge.execute('request',args)
        rid = pending['requestId']
        assert pending['status']=='PENDING_ADMIN'
        assert bridge.execute('request',args)==pending  # no second submission
        assert call('GET', f'/v1/enrollment/requests/{rid}/status')['status'] == 'pending'
        notebook = {'target':'remote-seh' if transport=='seh' else 'network',
                    'remote_ticket':ticket, 'remote_client':admin, 'seh_client':admin}
        assert any(r['id']==rid and r['role']=='worker' for r in execute_notebook('read-pending',{},**notebook))
        with pytest.raises(NetworkConnectionError) as refused:
            session()
        assert refused.value.code == 'UNKNOWN_OR_REVOKED_DEVICE'
        approved = execute_notebook('accept',{'recordId':rid},**notebook)
        assert approved['outcome']=='APPROVED'
        observed_bridge = bridge.execute('status',args)
        assert observed_bridge['status']=='AUTHENTICATED' and observed_bridge['role']=='worker'
        assert any(r['id']==did for r in execute_notebook('read-enrolled',{},**notebook))
        worker_token = session()['access_token']
        observed = call('GET', '/v1/auth/session', token=worker_token)
        assert observed['active'] and observed['role'] == 'worker' and observed['device_id'] == did
        with pytest.raises(NetworkConnectionError) as refused:
            call('GET', '/v1/admin/devices', token=worker_token)
        assert refused.value.code == 'ADMIN_REQUIRED'
        row = next(r for r in admin.admin_request(ticket, 'GET', '/v1/admin/devices')['devices'] if r['id'] == did)
        assert row['fingerprint'] == public_key_fingerprint(public) and row['status'] == 'active'
        # The fixture's authenticated relay targets this same disposable Worker port.
        gateway = SLHManagedGateway(authority, host='127.0.0.1',
                                    port=read_config(state)['worker_port'], allow_seh=False)
        gateway.start_background()
        wait_until(lambda: gateway._server is not None)
        assert gateway.fingerprint == pin
        port = gateway.port
        relay_before = relay.bytes_forwarded
        if transport == 'seh':
            adapter = SEHLocalClientAdapter(relay_url, allow_lab=True)
            port = adapter.server_address[1]
        worker = ManagedSLHWorkerClient(device_id=did, identity_dir=identity_dir,
            tls_fingerprint=pin, hub_url=f'https://127.0.0.1:{port}', worker_id=did,
            auth_message={'type': 'worker.auth', 'device_id': did, 'market': {
                'rings': ['ring-2'], 'capabilities': ['chat.completions'], 'models': ['mock-model'],
                'price': {'amount': '1', 'unit': 'compute_credit'}, 'max_concurrency': 1}},
            work_executor=lambda _: pytest.fail('Enrollment test must not execute paid work'))
        worker.start()
        wait_until(lambda: any(r['device_id'] == did for r in gateway.scheduler.snapshot()))
        if transport == 'seh':
            assert relay.bytes_forwarded > relay_before and adapter.bytes_forwarded > 0
        worker.close()
        worker = None
        wait_until(lambda: not gateway.scheduler.snapshot())
        assert execute_notebook('delete',{'recordId':did},**notebook)['outcome']=='REVOKED'
        assert bridge.execute('status',args)['status']=='ACCESS_DENIED'
        assert next(r for r in admin.admin_request(ticket, 'GET', '/v1/admin/devices')['devices']
                    if r['id'] == did)['status'] != 'active'
        with pytest.raises(NetworkConnectionError) as refused:
            session()
        assert refused.value.code == 'UNKNOWN_OR_REVOKED_DEVICE'
    finally:
        if worker is not None:
            worker.close()
        if adapter is not None:
            adapter.stop()
        if gateway is not None:
            gateway.stop()
        admin.disconnect(ticket)


def test_worker_identity_live_probe_is_read_only_and_loopback_only():
    from tools.mcel_worker_identity_live_acceptance import allow_read, PARENT, CHILD, ORIGIN
    assert allow_read(PARENT, 'GET') and allow_read(CHILD, 'GET')
    assert allow_read(ORIGIN+'/api/applications/slh/identities', 'GET')
    for method in ('POST', 'PATCH', 'PUT', 'DELETE'):
        assert not allow_read(ORIGIN+'/api/applications/slh/identities', method)
        assert not allow_read(ORIGIN+'/api/applications/worker/settings', method)
    assert not allow_read('https://example.com/applications/worker', 'GET')
    assert not allow_read(ORIGIN+'/api/applications/slh/setup/action', 'GET')
