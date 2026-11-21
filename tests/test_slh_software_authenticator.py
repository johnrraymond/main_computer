"""Software OTP paired to a P-256 admin; live approval, replay, lockout and legacy compatibility."""
import base64
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from main_computer.slh.software_otp import code_at, new_secret, valid_counter
from main_computer.slh.api import SLHAPI
from main_computer.slh.authority import SLHAuthority
from main_computer.slh.authenticator import generate_identity, sign_object
from main_computer.slh.authenticator_store import LocalIdentityStore, IdentityStoreError


def test_rfc6238_sha256_vector():
    secret=base64.b32encode(b"12345678901234567890123456789012").decode('ascii').rstrip('=')
    assert code_at(secret,59)[0]=='46119246'
    assert valid_counter(secret,'46119246',59)==1
    assert valid_counter(secret,'46119247',59) is None

def test_local_software_authenticator_is_separate_from_identity(tmp_path):
    store=LocalIdentityStore(tmp_path/'identities')
    store.create('admin-box')
    key_before=store.get('admin-box')['public_key']
    activated=store.activate_software_otp('admin-box','a'*64)
    assert len(activated['pairing_secret'])==52
    code=store.current_software_otp('admin-box','a'*64)
    assert len(code['code'])==8 and code['code'].isdigit()
    assert code_at(activated['pairing_secret'])[0]==code['code']
    assert store.get('admin-box')['public_key']==key_before
    with pytest.raises(IdentityStoreError) as e:
        store.activate_software_otp('admin-box','a'*64)
    assert e.value.code=='SOFTWARE_OTP_ALREADY_ACTIVATED'
    assert not ('pairing_secret' in store.current_software_otp('admin-box','a'*64))

@pytest.fixture
def setup(tmp_path):
    from tools.slh_seh_connector import service_identity
    service_identity(tmp_path,create=True)
    authority=SLHAuthority(tmp_path)
    api=SLHAPI(authority)
    key,pub=generate_identity()
    body={'admin_id':'administrator','admin_name':'Administrator',
          'device_id':'admin-device','public_key':pub,
          'bootstrap_secret':(tmp_path/'bootstrap.secret').read_text().strip()}
    statement={k:body[k] for k in ('admin_id','device_id','public_key')}
    statement.update({'version':1,'action':'slh.admin.bootstrap'})
    body['proof']=sign_object(key,statement)
    status,result=api.call('POST','/v1/bootstrap/admin',body,loopback=True)
    assert status==201,result
    status,challenge=api.call('POST','/v1/auth/challenge',{'device_id':'admin-device'})
    assert status==200
    status,login=api.call('POST','/v1/auth/verify',{'device_id':'admin-device',
        'challenge_id':challenge['challenge_id'],'signature':sign_object(key,challenge)})
    assert status==200
    return authority,api,key,login['access_token'],tmp_path

def request(api,authority,name='new-worker',admin='administrator'):
    key,pub=generate_identity()
    data={'selected_admin':admin,'device_id':name,'public_key':pub,
        'kind':'worker','device_name':name,'claimed_user':'Operator',
        'requested_role':'worker','client_nonce':'x'*32}
    statement={**data,'version':1,'action':'slh.device.enrollment.request',
        'audience':'slh://'+authority.service_fingerprint()}
    data['proof']=sign_object(key,statement)
    status,result=api.call('POST','/v1/enrollment/requests',data)
    assert status==201,result
    return result['request_id']

def test_otp_approval_identity_selection_replay_encrypted_seed_and_legacy(setup):
    authority,api,_,token,path=setup
    secret=new_secret()
    with patch('time.time',return_value=1700000100):
        current,step=code_at(secret)
        status,result=api.call('POST','/v1/admin/authenticator/software-otp',
            {'secret':secret,'code':current,'service_fingerprint':authority.service_fingerprint()},token=token)
        assert status==200,result
        assert result['status']=='REGISTERED'
        assert api.call('GET','/v1/admin/authenticator/software-otp',token=token)[1]['active'] is True
        assert api.call('POST','/v1/admin/authenticator/software-otp',
            {'secret':secret,'code':current,'service_fingerprint':authority.service_fingerprint()},token=token)[0]==409
        assert secret not in (path/'organization.sqlite3').read_bytes().decode('latin1')
        assert api.call('POST','/v1/admin/authenticator/software-otp',
            {'secret':new_secret(),'code':current,'service_fingerprint':authority.service_fingerprint()},token='')[0]==401
        rid=request(api,authority)
        # Code used during pairing cannot be reused for enrollment.
        st,out=api.call('POST','/v1/enrollment/approvals',
            {'request_id':rid,'admin_identity':'administrator','one_time_code':current})
        assert st==403,out
        # Existing administrator bearer-session approval is still supported.
        legacy=request(api,authority,'legacy-worker')
        assert api.call('POST','/v1/enrollment/approvals',
            {'request_id':legacy},token=token)[0]==201
        # Existing enrollment-token approval is also unchanged.
        legacy2=request(api,authority,'legacy-worker-2')
        st,issued=api.call('POST',f'/v1/admin/enrollments/{legacy2}/keys',{},token=token)
        assert st==201
        assert api.call('POST','/v1/enrollment/approvals',
            {'request_id':legacy2,'enrollment_key':issued['enrollment_key']})[0]==201
    with patch('time.time',return_value=1700000135):
        otp=code_at(secret)[0]
        wrong=request(api,authority,'another-worker')
        assert api.call('POST','/v1/enrollment/approvals',
            {'request_id':wrong,'admin_identity':'impostor','one_time_code':otp})[0]==403
        st,resp=api.call('POST','/v1/enrollment/approvals',
            {'request_id':rid,'admin_identity':'administrator','one_time_code':otp})
        assert st==201,resp
        assert resp['authorized_by']=='administrator'
        # Same time-window cannot be consumed twice for another worker.
        st,resp=api.call('POST','/v1/enrollment/approvals',
            {'request_id':wrong,'admin_identity':'administrator','one_time_code':otp})
        assert st==403,resp
        assert resp['error']['code']=='INVALID_ONE_TIME_CODE'
        # Restart retains verified OTP state and last consumed counter.
        fresh=SLHAPI(SLHAuthority(path))
        assert fresh.call('POST','/v1/enrollment/approvals',
            {'request_id':wrong,'admin_identity':'administrator','one_time_code':otp})[0]==403

def test_invalid_codes_rate_limit_and_physical_inspection(setup):
    authority,api,_,token,_=setup
    secret=new_secret()
    with patch('time.time',return_value=1700000100):
        assert api.call('POST','/v1/admin/authenticator/software-otp',
            {'secret':secret,'code':code_at(secret)[0],'service_fingerprint':authority.service_fingerprint()},token=token)[0]==200
        rid=request(api,authority)
        assert api.call('PATCH','/v1/admin/policies',
            {'physical_inspection_required':True},token=token)[0]==200
        # Physical inspection remains a gate even with valid OTP.
        assert api.call('POST','/v1/enrollment/approvals',
            {'request_id':rid,'admin_identity':'administrator','one_time_code':'00000000'})[1]['error']['code']=='INSPECTION_REQUIRED'
        assert api.call('POST',f'/v1/admin/enrollments/{rid}/inspect',{},token=token)[0]==200
        for i in range(5):
            st,resp=api.call('POST','/v1/enrollment/approvals',
                {'request_id':rid,'admin_identity':'administrator','one_time_code':'not-a-code' if i==0 else '00000000'})
            assert st==403
        # Four syntactically correct misses count; malformed code was rejected before rate limiting.
        st,resp=api.call('POST','/v1/enrollment/approvals',
            {'request_id':rid,'admin_identity':'administrator','one_time_code':'00000000'})
        assert st==429,resp
        assert resp['error']['code']=='SOFTWARE_OTP_RATE_LIMITED'


def test_local_authenticator_http_never_exposes_pairing_secret(tmp_path):
    from main_computer.slh.local_authenticator_api import LocalAuthenticatorAPI
    api=LocalAuthenticatorAPI(tmp_path/'identities')
    assert api.call('POST','/v1/local/authenticator/identities',{'device_id':'admin-device'},loopback=True)[0]==201
    route='/v1/local/authenticator/identities/admin-device/software-otp'
    status,activated=api.call('POST',route,
        {'action':'activate','service_fingerprint':'b'*64},loopback=True)
    assert status==201
    assert 'pairing_secret' not in activated
    assert api.call('POST',route,
        {'action':'current','service_fingerprint':'b'*64},loopback=False)[0]==403
    status,current=api.call('POST',route,
        {'action':'current','service_fingerprint':'b'*64},loopback=True)
    assert status==200 and len(current['code'])==8
    assert api.call('POST',route,
        {'action':'current','service_fingerprint':'c'*64},loopback=True)[0]==404
    assert api.call('POST',route,
        {'action':'activate','service_fingerprint':'b'*64},loopback=True)[0]==409


def test_registration_rejects_wrong_slhs_and_no_secret_in_responses(setup):
    authority,api,_,token,_=setup
    secret=new_secret()
    body={'service_fingerprint':'f'*64,'secret':secret,'code':code_at(secret)[0]}
    st,err=api.call('POST','/v1/admin/authenticator/software-otp',body,token=token)
    assert st==403 and err['error']['code']=='SOFTWARE_OTP_AUDIENCE_MISMATCH'
    body['service_fingerprint']=authority.service_fingerprint()
    st,result=api.call('POST','/v1/admin/authenticator/software-otp',body,token=token)
    assert st==200 and 'secret' not in str(result)
    assert 'secret' not in str(api.call('GET','/v1/admin/authenticator/software-otp',token=token))


def test_paired_authenticator_cli_does_not_print_secret(tmp_path,monkeypatch,capsys):
    from tools import slh_software_otp
    from main_computer.slh.authenticator_store import LocalIdentityStore
    store=LocalIdentityStore(tmp_path/'identities')
    store.create('admin-device')
    slh='a'*64
    monkeypatch.setattr(slh_software_otp,'LocalIdentityStore',lambda:store)
    class FakeClient:
        def connect(self,*args,**kwargs):
            return {'pairingRequired':False,'sessionTicket':'t'*40,'role':'admin'}
        def admin_request(self,ticket,method,path,body=None):
            if path=='/v1/org/identity':return {'service_fingerprint':slh}
            if path=='/v1/admin/authenticator/software-otp' and method=='GET':return {'active':False}
            assert path=='/v1/admin/authenticator/software-otp'
            assert body['service_fingerprint']==slh
            assert body['secret']==store.software_otp_pairing_secret('admin-device',slh)
            assert body['code']==store.current_software_otp('admin-device',slh)['code']
            return {'status':'REGISTERED'}
        def disconnect(self,ticket): pass
    monkeypatch.setattr(slh_software_otp,'NetworkSlhClient',FakeClient)
    assert slh_software_otp.main(['pair','--identity','admin-device',
        '--address','192.168.1.10','--port','8899'])==0
    text=capsys.readouterr().out
    assert store.software_otp_pairing_secret('admin-device',slh) not in text
    assert 'Software authenticator paired' in text
    assert slh_software_otp.main(['show','--identity','admin-device',
        '--service-fingerprint',slh])==0
    assert 'Current code:' in capsys.readouterr().out


def test_otp_cannot_enroll_administrator_and_signed_revoke_disables_it(setup):
    authority,api,_,token,_=setup
    secret=new_secret()
    with patch('time.time',return_value=1700000100):
        pair={'secret':secret,'code':code_at(secret)[0],
            'service_fingerprint':authority.service_fingerprint()}
        assert api.call('POST','/v1/admin/authenticator/software-otp',pair,token=token)[0]==200
        admin_req=request(api,authority,'unauthorized-admin')
        # Directly change a separate pending request's role through the test
        # DB to exercise the elevated-role prohibition at the authority gate.
        with authority._db() as c:
            c.execute("UPDATE enrollments SET requested_role='admin' WHERE id=?",(admin_req,))
    with patch('time.time',return_value=1700000135):
        otp=code_at(secret)[0]
        st,out=api.call('POST','/v1/enrollment/approvals',{
            'request_id':admin_req,'admin_identity':'administrator','one_time_code':otp})
        assert st==403 and out['error']['code']=='ADMIN_ROLE_REQUIRES_SIGNED_ADMIN_SESSION'
        assert api.call('POST','/v1/admin/authenticator/software-otp/revoke',{},token='')[0]==401
        st,deleted=api.call('POST','/v1/admin/authenticator/software-otp/revoke',{},token=token)
        assert st==200 and deleted['status']=='REVOKED'
        assert api.call('GET','/v1/admin/authenticator/software-otp',token=token)[1]['active'] is False
        rid=request(api,authority,'worker-after-revoke')
        st,out=api.call('POST','/v1/enrollment/approvals',{
            'request_id':rid,'admin_identity':'administrator','one_time_code':otp})
        assert st==403 and out['error']['code']=='SOFTWARE_OTP_NOT_REGISTERED'
