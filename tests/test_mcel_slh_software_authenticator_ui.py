"""Software authenticator MCEL integration and authority boundaries."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from main_computer.mcel_slh_application import compile_slh_application, render_slh_page
from main_computer.mcel_slh_software_authenticator import execute, SoftwareAuthenticatorError
from main_computer.slh.authenticator_store import LocalIdentityStore


class PinnedOrganization:
    def __init__(self, device_id='admin-laptop', fingerprint='b'*64):
        self.device_id=device_id
        self.fingerprint=fingerprint
        self.active=False
        self.code_available=False
        self.secret=None
        self.calls=[]

    def request(self, method,path,token,body=None):
        self.calls.append((method,path,body))
        assert token=='t'*30
        if (method,path)==('GET','/v1/auth/session'):
            return {'active':True,'role':'admin','device_id':self.device_id}
        if (method,path)==('GET','/v1/org/identity'):
            return {'service_fingerprint':self.fingerprint}
        if (method,path)==('GET','/v1/admin/authenticator/software-otp'):
            return {'active':self.active,'device_id':self.device_id if self.active else None,
                    'code_available':self.code_available}
        if (method,path)==('POST','/v1/admin/authenticator/software-otp'):
            assert body['service_fingerprint']==self.fingerprint
            assert body['code'].isdigit() and len(body['code'])==8
            self.secret=body['secret']
            self.active=True
            self.code_available=False
            return {'status':'REGISTERED'}
        if (method,path)==('POST','/v1/admin/authenticator/software-otp/revoke'):
            assert body=={}
            self.active=False
            return {'status':'REVOKED'}
        assert False,(method,path)


def test_pair_current_revoke_no_secret_or_code_in_mcel_receipts(tmp_path):
    store=LocalIdentityStore(tmp_path/'identities')
    store.create('admin-laptop')
    peer=PinnedOrganization()
    token='t'*30
    before=execute('status','admin-laptop',token,client=peer,store=store)
    assert before['status']=='NOT_PAIRED'
    # First expansion automatically initializes the administrator's factor;
    # the registration proof is consumed and MUST NOT be shown as usable.
    initial=execute('current','admin-laptop',token,client=peer,store=store)
    assert initial['usable'] is False and initial['code'] is None
    assert 'secret' not in str(initial)
    assert peer.secret==store.software_otp_pairing_secret('admin-laptop','b'*64)
    used=execute('current','admin-laptop',token,client=peer,store=store)
    assert used['usable'] is False and used['code'] is None
    peer.code_available=True  # server accepted time window has elapsed
    current=execute('current','admin-laptop',token,client=peer,store=store)
    assert current['code']==store.current_software_otp('admin-laptop','b'*64)['code']
    assert current['usable'] is True and len(current['code'])==8
    revocation=execute('revoke','admin-laptop',token,client=peer,store=store)
    assert revocation['outcome']=='REVOKED'
    assert execute('status','admin-laptop',token,client=peer,store=store)['status']=='NOT_PAIRED'


def test_identity_and_role_checks_precede_secrets(tmp_path):
    store=LocalIdentityStore(tmp_path/'ids')
    store.create('admin-laptop')
    peer=PinnedOrganization('another-user')
    with pytest.raises(SoftwareAuthenticatorError,match='ADMIN_IDENTITY_MISMATCH'):
        execute('pair','admin-laptop','t'*30,client=peer,store=store)
    assert not list((tmp_path/'ids').glob('*.software-otp.*'))
    with pytest.raises(SoftwareAuthenticatorError,match='INVALID_AUTHENTICATOR_REQUEST'):
        execute('pair','../admin-laptop','t'*30,client=peer,store=store)
    with pytest.raises(Exception):
        execute('pair','admin-laptop','invalid',client=peer,store=store)
    peer.device_id='admin-laptop'
    original=peer.request
    def non_admin(method,path,token,body=None):
        if path=='/v1/auth/session':return {'active':True,'role':'worker','device_id':'admin-laptop'}
        return original(method,path,token,body)
    peer.request=non_admin
    with pytest.raises(SoftwareAuthenticatorError,match='ADMIN_IDENTITY_MISMATCH'):
        execute('pair','admin-laptop','t'*30,client=peer,store=store)


def test_semantic_app_identity_detail_not_notebook():
    ir,composition=compile_slh_application()
    nodes={n['id']:n for n in ir['surfaces'][0]['nodes']}
    assert 'surface-node:slh.software-authenticator-notebook' not in nodes
    node=nodes['surface-node:slh.available-local-identities']
    assert node['credentialDisclosure']['identityField']=='deviceId'
    assert node['credentialDisclosure']['intent']['ref']=='intent:software-authenticator-reveal'
    assert 'fingerprint' not in node['fields']
    assert 'deviceId' not in node['fields']
    assert 'code' not in {state['id'] for state in ir['states']}
    assert 'software-authenticator-receipt' in str(ir)
    assert 'revealSlhSoftwareAuthenticator' in str(ir)
    assert 'credentialDisclosure' in render_slh_page()
    assert 'Pair software authenticator' not in str(ir)
    assert 'Revoke authenticator' not in str(ir)


def test_separate_server_operation_is_wired_only_as_local_post():
    dispatch=(Path(__file__).resolve().parents[1]/'main_computer/viewport_route_dispatch.py').read_text()
    assert 'if urlsplit(self.path).path == "/api/applications/slh/software-authenticator":' in dispatch
    assert 'handle_software_authenticator(self)' in dispatch
    js=(Path(__file__).resolve().parents[1]/'main_computer/web/applications/scripts/mcel-semantic-slh-ports.js').read_text()
    assert 'softwareAuthenticatorOperation(req)' in js
    assert '"X-MCEL-SLH-Operation":"software-authenticator"' in js
    assert 'localStorage' not in js[js.index('function renderCurrentCode'):js.index('let accessToken')]
    assert 'result.code' in js[js.index('function renderCurrentCode'):js.index('let accessToken')]


def test_otp_route_enforces_local_same_origin_and_no_store(monkeypatch):
    import io
    import json
    from main_computer import mcel_slh_software_authenticator_route as route

    class Handler:
        def __init__(self, remote='127.0.0.1',origin='http://127.0.0.1:8888'):
            body=json.dumps({'operation':'current','deviceId':'admin-laptop'}).encode()
            self.client_address=(remote,12345)
            self.server=SimpleNamespace(server_address=('127.0.0.1',8888))
            self.headers={'Host':'127.0.0.1:8888','Origin':origin,
                'Sec-Fetch-Site':'same-origin','Content-Type':'application/json',
                'X-MCEL-SLH-Operation':'software-authenticator','Content-Length':str(len(body)),
                'Authorization':'Bearer '+'t'*30}
            self.rfile=io.BytesIO(body);self.wfile=io.BytesIO()
            self.status=0;self.sent={}
        def _send_json(self,obj,status):
            self.status=int(status);self.sent={'json':obj}
        def send_response(self,status):self.status=int(status)
        def send_header(self,k,v):self.sent[k]=v
        def end_headers(self):pass
    monkeypatch.setattr(route,'execute',lambda *a: {'code':'12345678','expiresAt':12345678})
    good=Handler()
    route.handle_software_authenticator(good)
    assert good.status==200
    assert good.sent['Cache-Control'].startswith('no-store')
    assert b'12345678' in good.wfile.getvalue()
    for case in (Handler(remote='192.168.1.20'),Handler(origin='https://evil.example')):
        route.handle_software_authenticator(case)
        assert case.status==403
        assert case.sent['json']['error']['code']=='LOCAL_ORIGIN_REQUIRED'
        assert not case.wfile.getvalue()
    unknown=Handler()
    unknown.rfile=io.BytesIO(b'{"operation":"current","deviceId":"admin-laptop","secret":"x"}')
    unknown.headers['Content-Length']=str(len(unknown.rfile.getvalue()))
    route.handle_software_authenticator(unknown)
    assert unknown.status==400


def test_original_otp_authority_marks_consumed_window_unavailable(setup=None):
    # The org-side replay counter, not the local browser's clock, is the
    # authoritative source of whether an already displayed code can be used.
    from main_computer.slh.software_otp_authority import status
    assert 'last_accepted_step' in __import__('inspect').getsource(status)


def test_default_authenticator_store_uses_lms_canonical_identity_directory(tmp_path,monkeypatch):
    from main_computer import mcel_slh_software_authenticator as module
    identity_root=tmp_path/'slh'/'identities'
    store=LocalIdentityStore(identity_root)
    store.create('admin-laptop')
    monkeypatch.setattr(module,'slh_state_dir',lambda:tmp_path/'slh')
    peer=PinnedOrganization()
    initial=module.execute('current','admin-laptop','t'*30,client=peer)
    assert initial['usable'] is False
    assert peer.active is True
    peer.code_available=True
    result=module.execute('current','admin-laptop','t'*30,client=peer)
    assert result['usable'] is True and len(result['code'])==8
    # The interactive user's alternate store must never silently receive a
    # replacement identity while the machine-owned LMS identity exists.
    assert not (tmp_path/'user').exists()


def test_locked_enrollment_identity_cannot_display_usable_code(tmp_path):
    store=LocalIdentityStore(tmp_path/'ids')
    store.create('admin-laptop')
    peer=PinnedOrganization()
    module_token='t'*30
    execute('current','admin-laptop',module_token,client=peer,store=store)
    peer.code_available=True
    original=peer.request
    def locked(method,path,token,body=None):
        result=original(method,path,token,body)
        if (method,path)==('GET','/v1/admin/authenticator/software-otp'):
            return {**result,'locked':True}
        return result
    peer.request=locked
    with pytest.raises(SoftwareAuthenticatorError,match='SOFTWARE_OTP_RATE_LIMITED'):
        execute('current','admin-laptop',module_token,client=peer,store=store)
