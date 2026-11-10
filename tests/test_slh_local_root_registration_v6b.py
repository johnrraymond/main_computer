"""V6-B: LMS-SLH explicitly grants an existing local identity through ORG's TLS API."""
from __future__ import annotations

from http.client import HTTPConnection
import json
from threading import Thread
import time

import pytest

from main_computer.slh.api import SLHAPI
from main_computer.slh.authenticator import verify_object
from main_computer.slh.authority import SLHAuthority, SLHError
from main_computer.slh.local_authenticator_api import LocalAuthenticatorAPI
from main_computer.slh.local_root_registration import LocalRootAdministratorRegistration
from main_computer.slh.local_service import create_server
from main_computer.slh.managed_gateway import transport_identity
from main_computer.slh.organization_service import OrganizationHTTPHandler, OrganizationHTTPServer
from main_computer.slh.organization_state import DEFAULTS, save_config
from tools.slh_seh_connector import service_identity


PATH = '/v1/local/organization/admin/identities'


def request(port, method, path, body=None, headers=None):
    conn = HTTPConnection('127.0.0.1', port, timeout=5)
    try:
        raw = json.dumps(body).encode('utf-8') if body is not None else None
        conn.request(method, path, body=raw, headers={'Content-Type': 'application/json', **(headers or {})})
        reply = conn.getresponse()
        return reply.status, json.loads(reply.read())
    finally:
        conn.close()


@pytest.fixture
def services(tmp_path):
    state, identities = tmp_path / 'org', tmp_path / 'identities'
    service_identity(state, create=True)
    org = SLHAuthority(state)
    org.ensure_local_auth_key()
    with org._db() as c:
        c.execute('INSERT INTO slh_setup(singleton,initialized_at) VALUES (1,?)', (int(time.time()),))
        # An earlier admin is preserved when granting a new one.
        c.execute('INSERT INTO users VALUES (?,?,?,?)', ('olduser','old admin','admin','active'))
        c.execute('INSERT INTO devices VALUES (?,?,?,?,?,?,?,?,?)',
                  ('old-admin','olduser','old-key','old-fingerprint',
                   'p256_device','old admin','', 'active',int(time.time())))

    tls_context, fingerprint = transport_identity(state)
    org_server = OrganizationHTTPServer(('127.0.0.1', 0), OrganizationHTTPHandler)
    org_server.socket = tls_context.wrap_socket(org_server.socket, server_side=True)
    org_server.authority = org
    org_server.api = SLHAPI(org, root_api_enabled=True)
    org_server.fingerprint = fingerprint
    org_server.state_dir = state
    save_config(state, {**DEFAULTS, 'configured': True, 'enabled': True,
                        'activation_confirmed': True, 'host': '127.0.0.1',
                        'api_port': org_server.server_port})
    org_thread = Thread(target=org_server.serve_forever, daemon=True)
    org_thread.start()
    try:
        with create_server(state_dir=state, identity_dir=identities, port=0) as local:
            local_thread = Thread(target=local.serve_forever, daemon=True)
            local_thread.start()
            try:
                yield org, local, identities, org_server
            finally:
                local.shutdown()
                local_thread.join(timeout=3)
    finally:
        org_server.shutdown()
        org_thread.join(timeout=3)
        org_server.server_close()


def test_real_local_to_tls_registration_and_normal_login(services):
    org, local, identity_dir, _ = services
    code, created = request(local.server_port, 'POST', '/v1/local/authenticator/identities', {})
    assert code == 201
    assert created['device_id'].count('-') == 7
    original_default = request(local.server_port, 'GET', '/v1/local/authenticator/identities')[1]['default_device_id']
    code, granted = request(local.server_port, 'POST', PATH, {'device_id': created['device_id']})
    assert code == 200 and granted == {
        'status': 'REGISTERED', 'device_id': created['device_id'],
        'fingerprint': created['fingerprint'], 'role': 'admin',
    }
    assert 'PRIVATE' not in json.dumps(granted) and 'LOCAL-AUTH-KEY' not in json.dumps(granted)
    assert created['device_id'] != original_default
    assert request(local.server_port, 'GET', '/v1/local/authenticator/identities')[1]['default_device_id'] == original_default
    assert request(local.server_port, 'POST', PATH, {'device_id': created['device_id']})[1]['status'] == 'ALREADY_REGISTERED'
    with org._db() as c:
        assert c.execute("SELECT status FROM devices WHERE id='old-admin'").fetchone()[0] == 'active'
    assert org.initialized()
    challenge = org.challenge(created['device_id'])
    code, signed = request(local.server_port, 'POST',
        '/v1/local/authenticator/identities/' + created['device_id'] + '/sign',
        {'challenge': challenge})
    assert code == 200
    session = org.verify_login({'device_id': created['device_id'],
                                'challenge_id': challenge['challenge_id'],
                                'signature': signed['signature']})
    assert session['role'] == 'admin'
    assert org.authenticate(session['access_token'], admin=True)['device_id'] == created['device_id']
    assert 'administrator.root_grant' in json.dumps(org.audit())
    assert org.local_auth_key_path.read_text().strip() not in json.dumps(granted)


def test_refuse_untrusted_or_missing_inputs(services):
    org, local, identities, _ = services
    port = local.server_port
    assert request(port, 'POST', PATH, {'device_id': 'not-present'}) == (
        404, {'error': {'code': 'IDENTITY_NOT_FOUND'}})
    assert request(port, 'POST', PATH, {'device_id': '../escape'})[0] == 400
    assert request(port, 'POST', PATH, {'device_id': 'abc', 'root_key': 'anything'})[0] == 400
    assert request(port, 'POST', PATH, {})[0] == 400
    assert request(port, 'POST', PATH, {'device_id': 'abc'}, {'Origin': 'https://evil.example'}) == (
        403, {'error': {'code': 'CROSS_ORIGIN_DENIED'}})
    # The network ORG root endpoint is never mounted on the LMS-SLH plaintext server.
    assert request(port, 'POST', '/v1/admin/root/identities', {})[0] == 404
    assert local.organization_controller is not None
    assert SLHAPI(org, local_root_registration=LocalRootAdministratorRegistration(
        SLHAuthority(org.directory), LocalAuthenticatorAPI(identities))).call(
            'POST', PATH, {'device_id': 'abc'}, loopback=False)[0] == 403


def test_missing_root_and_tls_pin_prevent_registration(services, monkeypatch):
    org, local, identities, _ = services
    code, created = request(local.server_port, 'POST', '/v1/local/authenticator/identities', {})
    assert code == 201
    # The LMS-SLH authority is distinct from the live ORG authority instance.
    lms_authority = local.organization_controller.authority
    def refuse_root():
        raise SLHError('LOCAL_AUTH_KEY_UNAVAILABLE', 503)
    monkeypatch.setattr(lms_authority, '_read_local_auth_key', refuse_root)
    assert request(local.server_port, 'POST', PATH, {'device_id': created['device_id']})[1] == {
        'error': {'code': 'LOCAL_AUTH_KEY_UNAVAILABLE'}}
    monkeypatch.undo()
    cert = org.directory / 'managed-tls-cert.pem'
    saved = cert.read_bytes()
    try:
        cert.write_text('not a certificate', encoding='ascii')
        assert request(local.server_port, 'POST', PATH,
                       {'device_id': created['device_id']})[1] == {
                           'error': {'code': 'ORG_TLS_PIN_UNAVAILABLE'}}
    finally:
        cert.write_bytes(saved)
    # A *valid but different* TLS certificate must fail before transmitting
    # the root bearer to an imposter endpoint.
    wrong_state = org.directory.parent / 'unrelated-tls'
    transport_identity(wrong_state)
    try:
        cert.write_bytes((wrong_state / 'managed-tls-cert.pem').read_bytes())
        assert request(local.server_port, 'POST', PATH,
                       {'device_id': created['device_id']})[1] == {
                           'error': {'code': 'ORG_TLS_PIN_MISMATCH'}}
    finally:
        cert.write_bytes(saved)
    monkeypatch.setattr(lms_authority, '_read_local_auth_key', lambda: 'A'*64)
    assert request(local.server_port, 'POST', PATH,
                   {'device_id': created['device_id']}) == (
                       403, {'error': {'code': 'LOCAL_AUTH_KEY_DENIED'}})
    monkeypatch.undo()
    with org._db() as c:
        assert c.execute('SELECT 1 FROM devices WHERE id=?', (created['device_id'],)).fetchone() is None


def test_missing_root_is_not_generated_by_lms(services):
    org, local, identities, _ = services
    code, created = request(local.server_port, 'POST', '/v1/local/authenticator/identities', {})
    assert code == 201
    org.local_auth_key_path.unlink()
    assert request(local.server_port, 'POST', PATH,
                   {'device_id': created['device_id']}) == (
                       503, {'error': {'code': 'LOCAL_AUTH_KEY_UNAVAILABLE'}})
    assert not org.local_auth_key_path.exists()


def test_root_proof_signer_only_accepts_matching_identity_and_fresh_statement(services):
    org, local, identities, _ = services
    code, created = request(local.server_port, 'POST', '/v1/local/authenticator/identities', {})
    assert code == 201
    statement = {'version': 1, 'action': 'slh.admin.root.register',
                 'audience': 'slh://' + org.service_fingerprint(),
                 'device_id': created['device_id'], 'public_key': created['public_key'],
                 'client_nonce': 'n'*40, 'issued_at': int(time.time())}
    path = '/v1/local/authenticator/identities/' + created['device_id'] + '/sign'
    code, signed = request(local.server_port, 'POST', path, {'challenge': statement})
    assert code == 200 and verify_object(created['public_key'], statement, signed['signature'])
    for changed in (
        {**statement, 'public_key': 'not its public key'},
        {**statement, 'device_id': 'some-other-id'},
        {**statement, 'issued_at': int(time.time()) - 500},
        {**statement, 'client_nonce': 'too-short'},
        {**statement, 'unexpected_field': 'foo'},
    ):
        assert request(local.server_port, 'POST', path, {'challenge': changed})[0] == 400
    assert org.local_auth_key_path.is_file()
