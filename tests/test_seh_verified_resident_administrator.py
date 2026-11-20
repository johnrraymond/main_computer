"""Administrator discovery is authority-based and cannot mutate the SEH."""
from __future__ import annotations

from pathlib import Path
import json
import time
from http import client as http_client
from types import SimpleNamespace

import pytest
import seh_mutate_harness as harness
from main_computer.slh.authenticator_store import LocalIdentityStore


def _store(tmp_path, *, default=True, others=()):
    store = LocalIdentityStore(tmp_path / 'slh' / 'identities')
    if default:
        primary = store.ensure_default_identity()['device_id']
    else:
        primary = None
    for device in others:
        store.create(device)
    return store, primary


class Resident:
    def __init__(self, roles, *, revoked=(), fail_challenge=()):
        self.roles = roles
        self.revoked = set(revoked)
        self.fail_challenge = set(fail_challenge)
        self.calls = []
        self.sessions = set()

    def __call__(self, method, path, body=None, token=None):
        self.calls.append((method, path, body, token))
        if path == '/v1/auth/challenge':
            device = body['device_id']
            if device in self.fail_challenge:
                raise harness.HarnessError('Resident SLH localhost service is unavailable: ConnectionRefusedError')
            if device in self.revoked:
                raise harness.HarnessError('Resident SLH refused disconnect: UNKNOWN_OR_REVOKED_DEVICE')
            now = int(time.time())
            return {'version': 1, 'purpose': 'slh.known_device.login',
                    'audience': 'slh://' + 'a' * 64, 'device_id': device,
                    'challenge_id': 'c' * 32, 'nonce': 'n' * 32,
                    'issued_at': now, 'expires_at': now + 60}
        if path == '/v1/auth/verify':
            device = body['device_id']
            self.sessions.add('tok-' + device)
            return {'access_token': 'tok-' + device}
        if path == '/v1/auth/session':
            device = token.removeprefix('tok-')
            return {'device_id': device, 'user_id': 'user-' + device,
                    'role': self.roles[device], 'active': True}
        if path == '/v1/auth/logout':
            self.sessions.remove(token)
            return {'status': 'LOGGED_OUT'}
        raise AssertionError('unexpected call ' + path)


def test_selects_only_verified_admin_not_ordinary_default(tmp_path):
    store, default = _store(tmp_path, others=('new-admin', 'inactive'))
    backend = Resident({default:'worker','new-admin':'admin'}, revoked={'inactive'})
    device, bearer = harness._verified_resident_administrator(store, None, backend)
    assert device == 'new-admin' and bearer == 'tok-new-admin'
    assert backend.sessions == {'tok-new-admin'}
    assert all(path.startswith('/v1/auth/') for _, path, _, _ in backend.calls)
    backend('POST', '/v1/auth/logout', {}, token=bearer)
    assert not backend.sessions


def test_verified_default_wins_without_probing_unnecessary_keys(tmp_path):
    store, default = _store(tmp_path, others=('other-admin',))
    backend = Resident({default:'admin','other-admin':'admin'})
    assert harness._verified_resident_administrator(store, None, backend) == (
        default, 'tok-' + default)
    assert not any(call[2] == {'device_id':'other-admin'} for call in backend.calls)


def test_multiple_verified_admins_without_admin_default_refuse_and_logout(tmp_path):
    store, default = _store(tmp_path, others=('admin-1','admin-2'))
    backend = Resident({default:'worker','admin-1':'admin','admin-2':'admin'})
    with pytest.raises(harness.HarnessError, match='Multiple verified administrators'):
        harness._verified_resident_administrator(store, None, backend)
    assert backend.sessions == set()
    assert all(path.startswith('/v1/auth/') for _, path, _, _ in backend.calls)


def test_explicit_admin_override_must_still_be_authorized(tmp_path):
    store, default = _store(tmp_path, others=('admin-1',))
    backend = Resident({default:'worker','admin-1':'admin'})
    with pytest.raises(harness.HarnessError, match='No verified resident administrator'):
        harness._verified_resident_administrator(store, default, backend)
    assert not backend.sessions
    assert harness._verified_resident_administrator(store, 'admin-1', backend) == (
        'admin-1','tok-admin-1')
    with pytest.raises(harness.HarnessError, match='absent'):
        harness._verified_resident_administrator(store, 'nonexistent', backend)


def test_transport_failure_fails_closed_without_mutation(tmp_path):
    store, default = _store(tmp_path, others=('admin-1',))
    backend = Resident({default:'worker','admin-1':'admin'}, fail_challenge={default})
    with pytest.raises(harness.HarnessError, match='unavailable'):
        harness._verified_resident_administrator(store, None, backend)
    assert not backend.sessions
    assert not any('/seh/' in path for _, path, _, _ in backend.calls)


def test_disconnection_is_signed_by_verified_admin_only(tmp_path, monkeypatch):
    store, default = _store(tmp_path, others=('approved-admin',))
    backend = Resident({default:'worker','approved-admin':'admin'})
    conn_calls = []
    issued_at = int(time.time())
    challenge = {'version':1,'purpose':'slh.known_device.login',
                 'audience':'slh://'+'a'*64,'device_id':None,
                 'challenge_id':'c'*32,'nonce':'n'*32,
                 'issued_at':issued_at,'expires_at':issued_at+60}

    class Response:
        status=200
        def __init__(self, value): self.raw = json.dumps(value).encode()
        def read(self, length): return self.raw[:length]

    class Connection:
        def __init__(self, host, port, timeout):
            assert host=='127.0.0.1' and port==8897
            self.path=None
            self.body=None
            self.headers=None
        def request(self, method, path, body=None, headers=None):
            self.path=path
            self.body=json.loads(body) if body else None
            self.headers=headers
            conn_calls.append((method,path,self.body,headers))
        def getresponse(self):
            path=self.path
            if path == '/v1/auth/challenge':
                device=self.body['device_id']
                return Response({**challenge, 'device_id':device})
            if path == '/v1/auth/verify':
                device=self.body['device_id']
                return Response(backend('POST', path, self.body))
            if path == '/v1/auth/session':
                return Response(backend('GET',path,token=self.headers['Authorization'][7:]))
            if path == '/v1/auth/logout':
                return Response(backend('POST',path,self.body,token=self.headers['Authorization'][7:]))
            if path == '/v1/local/seh/disconnect':
                assert self.headers['Authorization']=='Bearer tok-approved-admin'
                return Response({'status':'DISCONNECTION_REQUESTED','connection_enabled':False})
            raise AssertionError(path)
        def close(self): pass

    monkeypatch.setattr(http_client,'HTTPConnection',Connection)
    result=harness._disconnect_via_resident_slh(
        SimpleNamespace(admin_device=None, local_port=8897),state_dir=store.directory.parent)
    assert result['status']=='DISCONNECTION_REQUESTED'
    assert [path for _,path,_,_ in conn_calls].count('/v1/local/seh/disconnect')==1
    assert not backend.sessions


def test_read_only_cli_authenticates_and_logs_out_without_disconnection(tmp_path, monkeypatch):
    store, default = _store(tmp_path, others=('approved-admin',))
    issued_at = int(time.time())
    calls = []
    class Response:
        status = 200
        def __init__(self, body): self.raw = json.dumps(body).encode('utf-8')
        def read(self, size): return self.raw[:size]
    class Connection:
        def __init__(self, host, port, timeout):
            assert host == '127.0.0.1' and port == 8897
        def request(self, method, path, body=None, headers=None):
            self.path, self.body = path, json.loads(body) if body else None
            calls.append((method, path))
            self.headers = headers
        def getresponse(self):
            if self.path == '/v1/auth/challenge':
                device = self.body['device_id']
                return Response({'version':1, 'purpose':'slh.known_device.login',
                    'audience':'slh://'+'a'*64, 'device_id':device,
                    'challenge_id':'b'*32, 'nonce':'n'*32,
                    'issued_at':issued_at, 'expires_at':issued_at+60})
            if self.path == '/v1/auth/verify':
                return Response({'access_token':'tok-' + self.body['device_id']})
            if self.path == '/v1/auth/session':
                device = self.headers['Authorization'].removeprefix('Bearer tok-')
                return Response({'device_id':device,'user_id':'u-'+device,
                    'role':'admin' if device=='approved-admin' else 'worker','active':True})
            if self.path == '/v1/auth/logout':
                return Response({'status':'LOGGED_OUT'})
            raise AssertionError('Unapproved read-only preflight operation: '+self.path)
        def close(self): pass
    monkeypatch.setattr(http_client, 'HTTPConnection', Connection)
    args = harness._parser().parse_args(['verify-resident-admin',
                  '--slh-state-dir',str(store.directory.parent)])
    assert harness.run(args) == {'status':'VERIFIED_ADMIN_SESSION',
        'device_id':'approved-admin','remote_mutation':False,'session_closed':True}
    assert not any('/seh/disconnect' in path or '/seh/activate' in path
                   for _,path in calls)
    assert [path for _,path in calls].count('/v1/auth/logout')==2
    args.execute_mutations=True
    with pytest.raises(harness.HarnessError, match='read-only'):
        harness.run(args)


def test_read_only_preflight_refuses_unverified_logout(tmp_path,monkeypatch):
    store, default = _store(tmp_path)
    def request(method,path,body=None,token=None):
        if path == '/v1/auth/challenge':
            now=int(time.time())
            return {'version':1,'purpose':'slh.known_device.login',
                    'audience':'slh://'+'a'*64,'device_id':default,
                    'challenge_id':'b'*32,'nonce':'n'*32,
                    'issued_at':now,'expires_at':now+60}
        if path == '/v1/auth/verify': return {'access_token':'token'}
        if path == '/v1/auth/session':
            return {'device_id':default,'user_id':'admin','role':'admin','active':True}
        if path == '/v1/auth/logout': return {'status':'FAILED'}
        raise AssertionError(path)
    # Simulate the same read-only gate implementation with a failing logout.
    # The authority picker itself has a valid admin; downstream preflight must
    # refuse if that session cannot be closed.
    from unittest.mock import patch
    monkeypatch.setattr(harness,'_verified_resident_administrator',
                        lambda store,explicit,request: (default,'token'))
    # Route requests through a captured HTTP-like response stub.
    class Response:
        status=200
        def read(self,n):return b'{"status":"FAILED"}'
    class Connection:
        def __init__(self,*a,**kw): pass
        def request(self,*a,**kw): pass
        def getresponse(self):return Response()
        def close(self):pass
    monkeypatch.setattr(http_client,'HTTPConnection',Connection)
    with pytest.raises(harness.HarnessError, match='logout unverified'):
        harness._disconnect_via_resident_slh(
            SimpleNamespace(admin_device=None,local_port=8897),
            state_dir=store.directory.parent,verify_only=True)
