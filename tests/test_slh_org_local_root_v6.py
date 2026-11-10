"""ORG-SLH-only persistent bearer root and proof-bound administrator grants.

All state is disposable. No production identity, root or organization is touched.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import sqlite3
import ssl
import subprocess
import sys
import threading
import time
from http.client import HTTPSConnection

import pytest

from main_computer.slh.api import SLHAPI
from main_computer.slh.authenticator import generate_identity, sign_object
from main_computer.slh.authority import SLHAuthority, SLHError
from main_computer.slh.managed_gateway import transport_identity
from main_computer.slh.organization_service import OrganizationHTTPHandler, OrganizationHTTPServer
from tools.slh_seh_connector import service_identity


PATH = '/v1/admin/root/identities'


def prepared(tmp_path):
    state = tmp_path / 'org'
    service_identity(state, create=True)
    auth = SLHAuthority(state)
    return auth


def initialize_org(auth):
    # The existing first-bootstrap path establishes the org. These tests also
    # model a migrated organization whose original admin key was lost.
    with auth._db() as c:
        c.execute('INSERT INTO slh_setup(singleton,initialized_at) VALUES (1,?)', (int(time.time()),))


def proof(auth, device_id='734-CXR-SD-104-EU-26-11-09', *, key=None, pub=None, nonce=None, issued_at=None):
    if key is None:
        key, pub = generate_identity()
    now = int(time.time()) if issued_at is None else issued_at
    req = {'device_id': device_id, 'public_key': pub,
           'client_nonce': nonce or secrets.token_urlsafe(32), 'issued_at': now}
    statement = {'version': 1, 'action': 'slh.admin.root.register',
                 'audience': 'slh://' + auth.service_fingerprint(), **req}
    req['proof'] = sign_object(key, statement)
    return key, req


def root(auth):
    return auth.local_auth_key_path.read_text(encoding='ascii').strip()


def call(auth, req, *, token=None, **options):
    return SLHAPI(auth, root_api_enabled=True).call(
        'POST', PATH, req, token=root(auth) if token is None else token,
        loopback=False, **options)


def test_root_only_created_by_org_service_startup_initialization(tmp_path):
    auth = prepared(tmp_path)
    assert not auth.local_auth_key_path.exists()
    assert auth.ensure_local_auth_key() == auth.local_auth_key_path
    initial = auth.local_auth_key_path.read_bytes()
    assert len(initial.strip()) == 64
    assert auth.ensure_local_auth_key() == auth.local_auth_key_path
    assert SLHAuthority(auth.directory).ensure_local_auth_key() == auth.local_auth_key_path
    assert auth.local_auth_key_path.read_bytes() == initial
    assert not auth.initialized()
    if os.name == 'posix':
        assert auth.local_auth_key_path.stat().st_mode & 0o077 == 0
    assert 'LOCAL-AUTH-KEY' not in json.dumps(auth.status())
    assert initial.decode('ascii').strip() not in json.dumps(auth.status())


def test_corrupt_root_never_replaced(tmp_path):
    auth = prepared(tmp_path)
    auth.local_auth_key_path.write_text('broken\n', encoding='ascii')
    with pytest.raises(SLHError) as e:
        auth.ensure_local_auth_key()
    assert e.value.code == 'LOCAL_AUTH_KEY_INVALID'
    assert auth.local_auth_key_path.read_text() == 'broken\n'
    assert auth.local_auth_key_path.exists()


def test_symlink_root_refused(tmp_path):
    auth = prepared(tmp_path)
    other = tmp_path / 'other-secret'
    other.write_text('keep', encoding='ascii')
    try:
        auth.local_auth_key_path.symlink_to(other)
    except (OSError, NotImplementedError):
        pytest.skip('Symlinks unavailable for this Windows account')
    with pytest.raises(SLHError) as e:
        auth.ensure_local_auth_key()
    assert e.value.code == 'LOCAL_AUTH_KEY_INVALID'
    assert other.read_text() == 'keep'


def test_parallel_processes_agree_on_one_root(tmp_path):
    auth = prepared(tmp_path)
    code = ("import sys; from pathlib import Path; from main_computer.slh.authority import SLHAuthority; "
            "a=SLHAuthority(Path(sys.argv[1])); a.ensure_local_auth_key(); "
            "print(a.local_auth_key_path.read_text().strip())")
    procs = [subprocess.Popen([sys.executable, '-c', code, str(auth.directory)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True) for _ in range(4)]
    outputs = []
    for process in procs:
        out, err = process.communicate(timeout=20)
        assert process.returncode == 0, err
        outputs.append(out.strip())
    assert len(set(outputs)) == 1
    assert outputs[0] == root(auth)


def test_root_grant_recovery_and_ordinary_login(tmp_path):
    auth = prepared(tmp_path)
    auth.ensure_local_auth_key()
    initialize_org(auth)
    key, req = proof(auth)
    code, response = call(auth, req)
    assert code == 201, response
    assert response['status'] == 'REGISTERED'
    assert response['role'] == 'admin'
    assert 'access_token' not in response and 'LOCAL-AUTH-KEY' not in json.dumps(response)
    # Normal login needs a separate fresh challenge; never passes root again.
    challenge = auth.challenge(req['device_id'])
    logged_in = auth.verify_login({'device_id': req['device_id'],
                                   'challenge_id': challenge['challenge_id'],
                                   'signature': sign_object(key, challenge)})
    assert logged_in['role'] == 'admin'
    assert auth.authenticate(logged_in['access_token'], admin=True)['device_id'] == req['device_id']
    assert auth.initialized()
    assert any(d['id'] == req['device_id'] for d in auth.devices())
    assert auth.local_auth_key_path.exists()  # Root is not one-time bootstrap secret.
    audit = json.dumps(auth.audit())
    assert 'administrator.root_grant' in audit
    assert root(auth) not in audit


def test_root_grant_without_preexisting_administrator_and_preserves_other_roles(tmp_path):
    auth = prepared(tmp_path)
    auth.ensure_local_auth_key()
    initialize_org(auth)
    with auth._db() as c:
        c.execute("INSERT INTO users VALUES ('worker-account','worker','worker','active')")
        c.execute("INSERT INTO devices VALUES ('worker-device','worker-account','old-public','old-fingerprint','p256_device','worker','', 'active',1)")
    _, req = proof(auth)
    code, body = call(auth, req)
    assert code == 201, body
    with auth._db() as c:
        row = c.execute("SELECT role FROM users WHERE id='worker-account'").fetchone()
        assert row['role'] == 'worker'
        row = c.execute("SELECT status FROM devices WHERE id='worker-device'").fetchone()
        assert row['status'] == 'active'
        assert c.execute('SELECT COUNT(*) FROM slh_setup').fetchone()[0] == 1


def test_root_grant_denials_and_idempotency(tmp_path):
    auth = prepared(tmp_path)
    auth.ensure_local_auth_key()
    initialize_org(auth)
    key, req = proof(auth)
    api = SLHAPI(auth, root_api_enabled=True)
    assert api.call('POST', PATH, req, token='invalid', loopback=False)[0] == 403
    assert api.call('POST', PATH, req, token='', loopback=False)[0] == 403
    assert api.call('POST', PATH, req, token=root(auth), loopback=False)[0] == 201
    assert api.call('POST', PATH, req, token=root(auth), loopback=False)[1]['status'] == 'ALREADY_REGISTERED'
    # Same ID, different key, even with valid root+proof, cannot silently rekey.
    _, changed = proof(auth, device_id=req['device_id'])
    assert call(auth, changed)[1]['error']['code'] == 'DEVICE_ALREADY_REGISTERED'
    _, changed = proof(auth, device_id='different-identity', key=key, pub=req['public_key'])
    assert call(auth, changed)[0] == 409
    # Normal sessions are neither accepted nor minted by this root endpoint.
    assert SLHAPI(auth).call('POST', PATH, req, token=root(auth), loopback=True)[0] == 404
    assert SLHAPI(auth).call('POST', PATH, req, token=root(auth), loopback=False)[0] == 404


def test_wrong_proof_target_expiration_and_nonce_replay(tmp_path):
    auth = prepared(tmp_path)
    auth.ensure_local_auth_key()
    initialize_org(auth)
    _, req = proof(auth)
    altered = {**req, 'device_id': 'changed-target'}
    assert call(auth, altered)[1]['error']['code'] == 'INVALID_KEY_PROOF'
    altered = {**req, 'public_key': generate_identity()[1]}
    assert call(auth, altered)[1]['error']['code'] == 'INVALID_KEY_PROOF'
    _, expired = proof(auth, issued_at=int(time.time()) - 150)
    assert call(auth, expired)[1]['error']['code'] == 'ROOT_PROOF_EXPIRED'
    _, first = proof(auth, device_id='first-identity', nonce=req['client_nonce'])
    assert call(auth, first)[0] == 201
    _, second = proof(auth, device_id='second-identity', nonce=req['client_nonce'])
    assert call(auth, second)[1]['error']['code'] == 'ROOT_PROOF_REPLAY'
    # Proof remains bound to the original service signing fingerprint.
    _, different = proof(auth, device_id='third-identity')
    different['proof'] = req['proof']
    assert call(auth, different)[1]['error']['code'] == 'INVALID_KEY_PROOF'


def test_uninitialized_organization_not_granted_via_root(tmp_path):
    auth = prepared(tmp_path)
    auth.ensure_local_auth_key()
    _, req = proof(auth)
    assert call(auth, req)[1]['error']['code'] == 'NOT_INITIALIZED'
    assert auth.devices() == []


def test_tls_network_organization_root_endpoint(tmp_path):
    auth = prepared(tmp_path)
    auth.ensure_local_auth_key()
    initialize_org(auth)
    tls_context, _ = transport_identity(auth.directory)
    server = OrganizationHTTPServer(('127.0.0.1', 0), OrganizationHTTPHandler)
    server.socket = tls_context.wrap_socket(server.socket, server_side=True)
    server.authority = auth
    server.api = SLHAPI(auth, root_api_enabled=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _, req = proof(auth)
        def request(token):
            conn = HTTPSConnection('127.0.0.1', server.server_port,
                                  context=ssl._create_unverified_context(), timeout=5)
            try:
                conn.request('POST', PATH, body=json.dumps(req),
                             headers={'Content-Type': 'application/json',
                                      'Authorization': 'Bearer ' + token})
                result = conn.getresponse()
                return result.status, json.loads(result.read())
            finally:
                conn.close()
        assert request('incorrect')[0] == 403
        code, response = request(root(auth))
        assert code == 201, response
        assert response['device_id'] == req['device_id']
        assert request(root(auth))[1]['status'] == 'ALREADY_REGISTERED'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
