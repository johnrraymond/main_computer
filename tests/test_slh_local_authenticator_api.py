"""Patch A: persistent P-256 identities through localhost services only."""
from __future__ import annotations

from http.client import HTTPConnection
import json
import os
from pathlib import Path
from threading import Thread
import time

import pytest

from main_computer.slh.api import SLHAPI
from main_computer.slh.authority import SLHAuthority
from main_computer.slh.authenticator import verify_object
from main_computer.slh.authenticator_store import LocalIdentityStore, IdentityStoreError
from main_computer.slh.local_authenticator_api import LocalAuthenticatorAPI
from main_computer.slh.local_service import create_server
from main_computer.slh.organization_state import organization_status
from tools.slh_authenticator_cli import save_identity, load_identity


def _http(port, method, path, body=None):
    conn = HTTPConnection('127.0.0.1', port, timeout=3)
    try:
        data = json.dumps(body).encode() if body is not None else None
        headers = {'Content-Type': 'application/json'} if data else {}
        conn.request(method, path, body=data, headers=headers)
        response = conn.getresponse()
        return response.status, json.loads(response.read())
    finally:
        conn.close()


def _challenge(device_id, *, expires=None):
    now = int(time.time())
    return {'version': 1, 'purpose': 'slh.known_device.login',
            'audience': 'slh://' + '0'*64,
            'device_id': device_id, 'challenge_id': 'a'*32,
            'nonce': 'n'*43, 'issued_at': now, 'expires_at': expires or now+60}


def test_real_local_service_identity_api_and_restart(tmp_path):
    state_dir, identities = tmp_path/'slh', tmp_path/'identity'
    with create_server(state_dir=state_dir, port=0, identity_dir=identities) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_port
        try:
            assert _http(port, 'GET', '/v1/local/authenticator/status')[1]['protection'] == 'none'
            code, created = _http(port, 'POST', '/v1/local/authenticator/identities',
                                  {'device_id':'admin-device','label':'My administration device'})
            assert code == 201
            assert created['label'] == 'My administration device'
            assert created['fingerprint'] and 'BEGIN PUBLIC KEY' in created['public_key']
            assert 'PRIVATE' not in json.dumps(created)
            assert _http(port, 'POST', '/v1/local/authenticator/identities',
                         {'device_id':'admin-device'})[0] == 409
            code, entries = _http(port, 'GET', '/v1/local/authenticator/identities')
            assert code == 200 and len(entries['identities']) == 1
            assert 'public_key' not in entries['identities'][0]
            statement = _challenge('admin-device')
            code, signed = _http(port, 'POST', '/v1/local/authenticator/identities/admin-device/sign',
                                 {'challenge': statement})
            assert code == 200
            assert verify_object(created['public_key'], statement, signed['signature'])
            assert _http(port, 'GET', '/v1/bootstrap/status')[1]['initialized'] is False
            assert organization_status(state_dir)['enabled'] is False
        finally:
            server.shutdown()
            thread.join(timeout=3)
    with create_server(state_dir=state_dir, port=0, identity_dir=identities) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            code, detail = _http(server.server_port, 'GET',
                                 '/v1/local/authenticator/identities/admin-device')
            assert code == 200 and detail['public_key'] == created['public_key']
            restart_challenge = _challenge('admin-device')
            code, signed = _http(server.server_port,'POST',
                '/v1/local/authenticator/identities/admin-device/sign',
                {'challenge': restart_challenge})
            assert code == 200
            assert verify_object(detail['public_key'], restart_challenge, signed['signature'])
            assert detail['fingerprint'] == created['fingerprint']
        finally:
            server.shutdown()
            thread.join(timeout=3)


def test_sign_restrictions_and_bootstrap_compatibility(tmp_path):
    api = LocalAuthenticatorAPI(tmp_path)
    assert api.call('POST','/v1/local/authenticator/identities',{'device_id':'dev'},loopback=False)[0] == 403
    code, data = api.call('POST','/v1/local/authenticator/identities',{'device_id':'dev'},loopback=True)
    assert code == 201
    statement = {'version':1,'action':'slh.admin.bootstrap','admin_id':'owner','device_id':'dev','public_key':data['public_key']}
    code, signed = api.call('POST', '/v1/local/authenticator/identities/dev/sign',
                            {'challenge':statement},loopback=True)
    assert code == 200 and verify_object(data['public_key'],statement,signed['signature'])
    for bad in ({'anything':'danger'}, {**statement,'public_key':'evil'},
                {**statement,'device_id':'other'},
                {**_challenge('dev'), 'expires_at':int(time.time())-1},
                {**_challenge('dev'), 'purpose':'other'}):
        code, response = api.call('POST', '/v1/local/authenticator/identities/dev/sign',
                                  {'challenge':bad},loopback=True)
        assert code == 400, response
    code, result = api.call('POST','/v1/local/authenticator/identities',
                            {'device_id':'../traversal'},loopback=True)
    assert code == 400
    assert api.call('GET','/v1/local/authenticator/identities/nonexistent',loopback=True)[0] == 404


def test_legacy_key_is_preserved_and_cli_shares_store(tmp_path):
    store_dir = tmp_path/'keys'
    old = save_identity(store_dir, 'legacy')
    raw = (store_dir/'legacy.key.pem').read_bytes()
    api = LocalAuthenticatorAPI(store_dir)
    status, identities = api.call('GET','/v1/local/authenticator/identities',loopback=True)
    assert status == 200 and identities['identities'][0]['fingerprint'] == old['fingerprint']
    assert api.call('POST','/v1/local/authenticator/identities',
                    {'device_id':'legacy'},loopback=True)[0] == 409
    assert (store_dir/'legacy.key.pem').read_bytes() == raw
    key, public = load_identity(store_dir, 'legacy')
    assert key and 'BEGIN PUBLIC KEY' in public
    (store_dir/'corrupt.key.pem').write_text('bad key',encoding='utf-8')
    status, values = api.call('GET','/v1/local/authenticator/identities',loopback=True)
    assert status == 200 and values['invalid_identities'][0]['device_id'] == 'corrupt'
    assert api.call('GET','/v1/local/authenticator/identities/corrupt',loopback=True)[0] == 409
    assert (store_dir/'corrupt.key.pem').read_text() == 'bad key'


def test_no_organization_api_or_admin_session_needed(tmp_path):
    from tools.slh_seh_connector import service_identity
    service_identity(tmp_path/'slh', create=True)
    authority = SLHAuthority(tmp_path/'slh')
    api = SLHAPI(authority,local_authenticator=LocalAuthenticatorAPI(tmp_path/'identities'))
    code, result = api.call('POST','/v1/local/authenticator/identities',
                            {'device_id':'before-bootstrap'},loopback=True)
    assert code == 201 and result['device_id'] == 'before-bootstrap'
    assert not authority.initialized()
    assert api.call('GET','/v1/local/authenticator/identities',loopback=False)[0] == 403
    assert 'post' in api.openapi()['paths']['/v1/local/authenticator/identities']


def test_viewport_localhost_api_uses_same_identity_backend(tmp_path, monkeypatch):
    """Actual viewport route dispatch, with a minimal test HTTP wrapper.

    Exported snapshots can omit game_projects/; in that case this separate
    viewport import is blocked by the unrelated game bundle fixture.
    """
    import importlib
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from main_computer.slh import authenticator_store
    try:
        from main_computer.viewport_http import ViewportHttpMixin
        routes = importlib.import_module('main_computer.viewport_route_dispatch')
    except Exception as exc:
        if type(exc).__name__ == 'GameWebLoaderError':
            pytest.skip('snapshot omits the game_projects/webgl-demo fixture needed by viewport imports')
        raise
    monkeypatch.setattr(authenticator_store, 'default_identity_directory', lambda: tmp_path/'user')
    class Handler(ViewportHttpMixin, BaseHTTPRequestHandler):
        def do_GET(self):
            routes.dispatch_get(self)
        def do_POST(self):
            routes.dispatch_post(self)
        def log_message(self, *a):
            pass
    class Server(HTTPServer):
        def signal(self, *a, **kw):
            pass
    with Server(('127.0.0.1',0), Handler) as server:
        t = Thread(target=server.serve_forever,daemon=True)
        t.start()
        try:
            code, result = _http(server.server_port,'POST',
                '/api/local/authenticator/identities',{'device_id':'on-viewport'})
            assert code == 201 and result['device_id'] == 'on-viewport'
            code, result = _http(server.server_port,'GET',
                '/api/local/authenticator/identities')
            assert code == 200 and len(result['identities']) == 1
            code, result = _http(server.server_port,'POST',
                '/api/local/authenticator/identities/on-viewport/sign',
                {'challenge':_challenge('on-viewport')})
            assert code == 200 and 'signature' in result
        finally:
            server.shutdown()
            t.join(timeout=3)


def test_legacy_directory_selection_and_conflict(tmp_path,monkeypatch):
    from main_computer.slh import authenticator_store as mod
    monkeypatch.setattr(mod,'user_identity_dir',lambda: tmp_path/'canonical')
    monkeypatch.setattr(mod,'legacy_authenticator_dir',lambda: tmp_path/'legacy')
    save_identity(tmp_path/'legacy','old')
    assert mod.default_identity_directory() == tmp_path/'legacy'
    save_identity(tmp_path/'canonical','new')
    with pytest.raises(IdentityStoreError,match='IDENTITY_DIRECTORY_CONFLICT'):
        mod.default_identity_directory()


def test_multi_identity_and_secret_not_in_public_results(tmp_path):
    store = LocalIdentityStore(tmp_path)
    a = store.create('admin',label='Admin')
    b = store.create('worker',label='Worker')
    assert a['fingerprint'] != b['fingerprint']
    records = store.list()['identities']
    assert {x['device_id'] for x in records} == {'admin','worker'}
    for data in (*records, store.get('admin'), store.get('worker')):
        assert 'PRIVATE KEY' not in json.dumps(data)
        assert 'identity_file' not in data
    assert set(store.directory.glob('*.key.pem')) == {tmp_path/'admin.key.pem',tmp_path/'worker.key.pem'}


def test_first_administrator_existing_bootstrap_and_login_using_local_api(tmp_path):
    """Current SLH bootstrap/login contract can consume local API signatures.

    This is NOT the future staged/TOTP bootstrap, which belongs to later patches.
    """
    from tools.slh_seh_connector import service_identity
    service_identity(tmp_path/'slh', create=True)
    auth = SLHAuthority(tmp_path/'slh')
    client = LocalAuthenticatorAPI(tmp_path/'identity')
    api = SLHAPI(auth,local_authenticator=client)
    code, identity = client.call('POST','/v1/local/authenticator/identities',
                                 {'device_id':'first-admin'},loopback=True)
    assert code == 201
    statement = {'version':1,'action':'slh.admin.bootstrap','admin_id':'owner','device_id':'first-admin','public_key':identity['public_key']}
    code, signed = client.call('POST',
        '/v1/local/authenticator/identities/first-admin/sign',
        {'challenge':statement},loopback=True)
    assert code == 200
    bootstrap = {'admin_id':'owner','admin_name':'Owner',
                 'device_id':'first-admin','public_key':identity['public_key'],
                 'bootstrap_secret':(tmp_path/'slh'/'bootstrap.secret').read_text().strip(),
                 'proof':signed['signature']}
    assert api.call('POST','/v1/bootstrap/admin',bootstrap,loopback=True)[0] == 201
    code, challenge = api.call('POST','/v1/auth/challenge',
                               {'device_id':'first-admin'},loopback=True)
    assert code == 200
    code, signed = client.call('POST',
        '/v1/local/authenticator/identities/first-admin/sign',
        {'challenge':challenge},loopback=True)
    assert code == 200
    code, result = api.call('POST','/v1/auth/verify',
        {'device_id':'first-admin','challenge_id':challenge['challenge_id'],
         'signature':signed['signature']},loopback=True)
    assert code == 200 and result['role'] == 'admin'
    assert api.call('GET','/v1/auth/session',token=result['access_token'],loopback=True)[0] == 200
