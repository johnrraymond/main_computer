"""Patch B staged localhost bootstrap; no organization activation or TOTP."""
from __future__ import annotations

from http.client import HTTPConnection
import json
from threading import Thread
from pathlib import Path

import pytest

from main_computer.slh.api import SLHAPI
from main_computer.slh.authority import SLHAuthority
from main_computer.slh.local_authenticator_api import LocalAuthenticatorAPI
from main_computer.slh.local_service import create_server
from main_computer.slh.organization_state import organization_status


def _client(tmp_path):
    auth = SLHAuthority(tmp_path / 'slh')
    signing = LocalAuthenticatorAPI(tmp_path / 'identity')
    api = SLHAPI(auth, local_authenticator=signing)
    assert signing.call('POST','/v1/local/authenticator/identities',
                        {'device_id':'first-admin'},loopback=True)[0] == 201
    secret = (auth.directory / 'bootstrap.secret').read_text().strip()
    return auth, signing, api, secret


def _begin(api, secret, org_id='organization-one'):
    return api.call('POST','/v1/bootstrap/begin',
                    {'org_id':org_id,'bootstrap_secret':secret},loopback=True)


def _stage(signing, api, secret, begun, *, admin_id='first', key_id='first-admin'):
    code, identity = signing.call('GET', f'/v1/local/authenticator/identities/{key_id}',loopback=True)
    assert code == 200
    statement = {**begun['proof_context'], 'admin_id':admin_id,
                 'device_id':key_id,'public_key':identity['public_key']}
    code, signature = signing.call('POST',f'/v1/local/authenticator/identities/{key_id}/sign',
                                   {'challenge':statement},loopback=True)
    assert code == 200, signature
    body = {'admin_id':admin_id,'admin_name':'First Admin', 'device_id':key_id,
            'public_key':identity['public_key'], 'proof':signature['signature'],
            'bootstrap_secret':secret}
    return api.call('PUT',f"/v1/bootstrap/{begun['transaction_id']}/administrator",body,loopback=True)


def test_staged_bootstrap_is_inert_and_durable(tmp_path):
    auth, signing, api, secret = _client(tmp_path)
    assert api.call('GET','/v1/bootstrap/status',loopback=True)[1]['setup_phase'] == 'UNCONFIGURED'
    code, begun = _begin(api,secret)
    assert code == 201
    assert begun['state'] == 'PENDING'
    assert api.call('GET','/v1/bootstrap/status',loopback=True)[1]['setup_phase'] == 'PENDING'
    assert api.call('POST','/v1/bootstrap/begin',
                    {'org_id':'another','bootstrap_secret':secret},loopback=True)[1]['error']['code'] == 'SETUP_ALREADY_PENDING'
    assert api.call('POST','/v1/bootstrap/admin',{},loopback=False)[0] == 403
    code, staged = _stage(signing, api,secret,begun)
    assert code == 200, staged
    assert staged['state'] == 'AWAITING_AUTHENTICATOR'
    assert staged['fingerprint']
    assert not auth.initialized()
    assert auth.admin_directory() == []
    assert auth.devices() == []
    assert (auth.directory/'bootstrap.secret').exists()
    # Legacy direct bootstrap cannot create a conflicting active administrator.
    assert api.call('POST','/v1/bootstrap/admin',{'bootstrap_secret':secret},loopback=True)[1]['error']['code'] == 'SETUP_IN_PROGRESS'
    auth2 = SLHAuthority(auth.directory)
    api2 = SLHAPI(auth2,local_authenticator=signing)
    code, saved = api2.call('GET',f"/v1/bootstrap/{begun['transaction_id']}/status",token=secret,loopback=True)
    assert code == 200
    assert saved['state'] == 'AWAITING_AUTHENTICATOR'
    assert saved['admin_id'] == 'first'
    assert 'challenge_nonce' not in saved
    assert api2.call('GET','/v1/bootstrap/status',loopback=True)[1]['initialized'] is False
    assert organization_status(auth.directory)['enabled'] is False
    assert organization_status(auth.directory)['listening'] is False
    assert api2.call('PUT',f"/v1/bootstrap/{begun['transaction_id']}/administrator",{},loopback=True)[0] != 200
    code, cancelled = api2.call('POST',f"/v1/bootstrap/{begun['transaction_id']}/cancel",
                                 {'bootstrap_secret':secret},loopback=True)
    assert code == 200 and cancelled['state'] == 'CANCELED'
    assert api2.call('GET',f"/v1/bootstrap/{begun['transaction_id']}/status",token=secret,loopback=True)[1]['state'] == 'CANCELED'
    assert signing.call('GET','/v1/local/authenticator/identities/first-admin',loopback=True)[0] == 200
    assert _begin(api2,secret)[0] == 201


def test_bad_signature_and_transaction_binding(tmp_path):
    auth, signing, api, secret = _client(tmp_path)
    code, begun = _begin(api,secret)
    assert code == 201
    _, identity = signing.call('GET','/v1/local/authenticator/identities/first-admin',loopback=True)
    statement = {**begun['proof_context'], 'admin_id':'first',
                 'device_id':'first-admin', 'public_key':identity['public_key']}
    code, signed = signing.call('POST','/v1/local/authenticator/identities/first-admin/sign',
                                {'challenge':statement},loopback=True)
    assert code == 200
    base = {'admin_id':'second','admin_name':'First Admin','device_id':'first-admin',
            'public_key':identity['public_key'],'proof':signed['signature'],
            'bootstrap_secret':secret}
    # Signature for admin "first" cannot be reused to stage admin "second".
    assert api.call('PUT',f"/v1/bootstrap/{begun['transaction_id']}/administrator",base,loopback=True)[1]['error']['code'] == 'INVALID_KEY_PROOF'
    assert api.call('GET',f"/v1/bootstrap/{begun['transaction_id']}/status",token=secret,loopback=True)[1]['state'] == 'PENDING'
    assert api.call('GET',f"/v1/bootstrap/{begun['transaction_id']}/status",token='bad',loopback=True)[0] == 403
    assert api.call('GET',f"/v1/bootstrap/{begun['transaction_id']}/status",token=secret,loopback=False)[0] == 403
    base['admin_id'] = 'first'
    assert api.call('PUT',f"/v1/bootstrap/{begun['transaction_id']}/administrator",base,loopback=True)[0] == 200
    assert api.call('PUT',f"/v1/bootstrap/{begun['transaction_id']}/administrator",base,loopback=True)[0] == 409
    assert api.call('POST',f"/v1/bootstrap/{begun['transaction_id']}/cancel",{'bootstrap_secret':'bad'},loopback=True)[0] == 403


def test_expiry_and_cross_transaction_proof_rejection(tmp_path,monkeypatch):
    import main_computer.slh.authority as authority_module
    clock = [1750000000]
    monkeypatch.setattr(authority_module.time,'time',lambda:clock[0])
    auth, signing, api, secret = _client(tmp_path)
    _, initial = _begin(api,secret)
    assert _stage(signing,api,secret,initial)[0] == 200
    clock[0] += auth.SETUP_TTL_SECONDS
    assert api.call('GET',f"/v1/bootstrap/{initial['transaction_id']}/status",token=secret,loopback=True)[1]['state'] == 'EXPIRED'
    assert api.call('GET','/v1/bootstrap/status',loopback=True)[1]['setup_phase'] == 'UNCONFIGURED'
    assert api.call('POST',f"/v1/bootstrap/{initial['transaction_id']}/cancel",{'bootstrap_secret':secret},loopback=True)[0] == 409
    # The old proof is correctly signed for the first transaction, but cannot
    # be repurposed for this fresh transaction after expiry.
    clock[0] -= auth.SETUP_TTL_SECONDS - 1
    _, identity = signing.call('GET','/v1/local/authenticator/identities/first-admin',loopback=True)
    old_statement = {**initial['proof_context'], 'admin_id':'first',
                     'device_id':'first-admin','public_key':identity['public_key']}
    _, old_signature = signing.call('POST','/v1/local/authenticator/identities/first-admin/sign',
                                    {'challenge':old_statement},loopback=True)
    clock[0] += auth.SETUP_TTL_SECONDS - 1
    code, renewed = _begin(api,secret)
    assert code == 201
    code, invalid = api.call('PUT',f"/v1/bootstrap/{renewed['transaction_id']}/administrator",
                             {'admin_id':'first','admin_name':'First Admin','device_id':'first-admin',
                              'public_key':identity['public_key'], 'proof':old_signature['signature'],
                              'bootstrap_secret':secret},loopback=True)
    assert code == 403 and invalid['error']['code'] == 'INVALID_KEY_PROOF'
    assert not auth.initialized()


def _http(port, method, path, payload=None, *, secret=None):
    conn = HTTPConnection('127.0.0.1',port,timeout=3)
    try:
        raw = json.dumps(payload).encode() if payload is not None else None
        headers = {'Content-Type':'application/json'} if raw else {}
        if secret:
            headers['Authorization'] = 'Bearer ' + secret
        conn.request(method,path,raw,headers)
        response=conn.getresponse()
        return response.status,json.loads(response.read())
    finally:
        conn.close()


def test_real_loopback_api_and_disabled_org_service(tmp_path):
    store = tmp_path/'identity'
    state = tmp_path/'slh'
    with create_server(state_dir=state, identity_dir=store, port=0) as server:
        thread = Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            port=server.server_port
            secret=(state/'bootstrap.secret').read_text().strip()
            assert _http(port,'GET','/v1/bootstrap/status')[1]['setup_phase'] == 'UNCONFIGURED'
            assert _http(port,'POST','/v1/local/authenticator/identities',{'device_id':'host-admin'})[0] == 201
            code, begun = _http(port,'POST','/v1/bootstrap/begin',{'org_id':'home','bootstrap_secret':secret})
            assert code == 201
            code, identity = _http(port,'GET','/v1/local/authenticator/identities/host-admin')
            assert code == 200
            proof = {**begun['proof_context'],'admin_id':'owner',
                     'device_id':'host-admin','public_key':identity['public_key']}
            code, signature = _http(port,'POST','/v1/local/authenticator/identities/host-admin/sign',{'challenge':proof})
            assert code == 200
            code, saved = _http(port,'PUT',f"/v1/bootstrap/{begun['transaction_id']}/administrator",
                                {'admin_id':'owner','admin_name':'Owner','device_id':'host-admin',
                                 'public_key':identity['public_key'],'proof':signature['signature'],
                                 'bootstrap_secret':secret})
            assert code == 200, saved
            assert _http(port,'GET',f"/v1/bootstrap/{begun['transaction_id']}/status",secret=secret)[1]['state'] == 'AWAITING_AUTHENTICATOR'
            assert _http(port,'GET',f"/v1/bootstrap/{begun['transaction_id']}/status")[0] == 403
            assert _http(port,'GET','/v1/local/status')[1]['organization_service']['state'] in ('disabled','not_configured','stopped')
            assert SLHAuthority(state).initialized() is False
        finally:
            server.shutdown()
            thread.join(timeout=3)


def test_existing_initialized_slhs_remain_valid(tmp_path):
    auth, signing, api, secret = _client(tmp_path)
    _, identity = signing.call('GET','/v1/local/authenticator/identities/first-admin',loopback=True)
    statement = {'version':1,'action':'slh.admin.bootstrap','org_id':'existing-org',
                 'admin_id':'owner','device_id':'first-admin','public_key':identity['public_key']}
    _, signed = signing.call('POST','/v1/local/authenticator/identities/first-admin/sign',
                            {'challenge':statement},loopback=True)
    body = {'org_id':'existing-org','admin_id':'owner','admin_name':'Owner',
            'device_id':'first-admin','public_key':identity['public_key'],
            'proof':signed['signature'],'bootstrap_secret':secret}
    assert api.call('POST','/v1/bootstrap/admin',body,loopback=True)[0] == 201
    assert SLHAuthority(auth.directory).initialized() is True
    assert api.call('GET','/v1/bootstrap/status',loopback=True)[1]['setup_phase'] == 'INITIALIZED'
    assert _begin(api,secret)[0] in (403,409)
    assert 'post' in api.openapi()['paths']['/v1/bootstrap/begin']
    assert 'put' in api.openapi()['paths']['/v1/bootstrap/{id}/administrator']


def test_competing_local_bootstrap_writers_only_one_wins(tmp_path):
    """Two service processes cannot create competing initial setup proposals."""
    from concurrent.futures import ThreadPoolExecutor
    directory = tmp_path/'slh'
    first = SLHAuthority(directory)
    second = SLHAuthority(directory)
    secret = (directory/'bootstrap.secret').read_text().strip()
    def begin(authority):
        return SLHAPI(authority).call('POST','/v1/bootstrap/begin',
              {'org_id':'org-one','bootstrap_secret':secret},loopback=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(begin, (first,second)))
    assert sorted(status for status, _ in results) == [201,409]
    with first._db() as connection:
        assert connection.execute("SELECT COUNT(*) FROM bootstrap_transactions WHERE status='PENDING'").fetchone()[0] == 1
    assert not first.initialized()
