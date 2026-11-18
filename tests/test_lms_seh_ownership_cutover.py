"""LMS is the sole executor; ORG-SLH only authorizes and forwards."""
from __future__ import annotations
import hmac
import http.client
import json
from pathlib import Path
import secrets
import threading
import time
from wsgiref.simple_server import make_server

import pytest
from main_computer.slh.api import SLHAPI
from main_computer.slh.organization_service import OrganizationHTTPServer, OrganizationHTTPHandler
from main_computer.slh.seh_lms_ipc import (LMSManagementClient, LMSForwardingError,
    signature, local_path)
from main_computer.slh.seh_deployment_management import SLHSEHDeploymentManager
from main_computer.mcel_slh_seh_deployment import execute_seh_deployment
from tests.test_slh_seh_coolify_management_backend import _Authority, root_state, TOKEN


class Services:
    def __init__(self, tmp_path):
        self.root=root_state(tmp_path)
        self.manager=SLHSEHDeploymentManager(root=self.root,state_dir=tmp_path/'slh')
        self.key=secrets.token_bytes(32)
        self.api=SLHAPI(_Authority(),seh_deployment_manager=self.manager,seh_ipc_key=self.key)
        self.lms=make_server('127.0.0.1',0,self.api.wsgi_app)
        self.lms_thread=threading.Thread(target=self.lms.serve_forever,daemon=True)
        self.lms_thread.start()
        self.org=OrganizationHTTPServer(('127.0.0.1',0),OrganizationHTTPHandler)
        self.org.authority=_Authority()
        self.org.seh_identity_only=False
        self.org.seh_lms_forwarder=LMSManagementClient(port=self.lms.server_port,key=self.key)
        self.org_thread=threading.Thread(target=self.org.serve_forever,daemon=True)
        self.org_thread.start()

    def stop(self):
        self.org.shutdown();self.org_thread.join(3);self.org.server_close()
        self.lms.shutdown();self.lms_thread.join(3);self.lms.server_close()


def send(port,method,path,body=None,*,headers=None):
    conn=http.client.HTTPConnection('127.0.0.1',port,timeout=3)
    raw=json.dumps(body).encode() if body is not None else None
    all_headers={'Host':f'127.0.0.1:{port}', 'Accept':'application/json'}
    if raw is not None:all_headers['Content-Type']='application/json'
    if headers:all_headers.update(headers)
    try:
        conn.request(method,path,body=raw,headers=all_headers)
        response=conn.getresponse()
        return response.status,json.loads(response.read())
    finally:conn.close()


@pytest.fixture
def services(tmp_path):
    svc=Services(tmp_path)
    yield svc
    svc.stop()


def test_local_inspect_direct_when_org_stopped_and_no_signer(services):
    services.org.shutdown();services.org_thread.join(3)
    result=execute_seh_deployment('inspect',{},target='local',
          local_client=LMSManagementClient(port=services.lms.server_port))
    assert result['generation']==1
    assert result['controllers']==[{'identity':'coolify-a'}]
    assert TOKEN not in json.dumps(result)
    code,data=send(services.lms.server_port,'GET','/v1/local/seh/deployment')
    assert code==200 and data['generation']==1
    assert send(services.lms.server_port,'GET','/v1/local/seh/deployment',
                headers={'Origin':'https://evil.example'})[0]==403
    assert send(services.lms.server_port,'GET','/v1/local/seh/deployment',
                headers={'Host':'attacker.example'})[0]==403


def test_authenticated_remote_forwards_to_same_lms_and_untrusted_refused(services):
    address='/v1/org/admin/seh/deployment'
    code,data=send(services.org.server_port,'GET',address)
    assert code==401
    assert send(services.org.server_port,'POST',address+'/harness',
         {'action':'inspect','execute':False,'expected_generation':None,'confirm':None})[0]==401
    code,data=send(services.org.server_port,'GET',address,
         headers={'Authorization':'Bearer good'})
    assert code==200 and data['generation']==1
    # Remove the forwarding capability: never create a fallback manager.
    services.org.seh_lms_forwarder=None
    code,data=send(services.org.server_port,'GET',address,
         headers={'Authorization':'Bearer good'})
    assert code==503 and data['error']['code']=='LMS_MANAGEMENT_UNAVAILABLE'
    services.org.seh_identity_only=True
    assert send(services.org.server_port,'GET',address,
         headers={'Authorization':'Bearer good'})[0]==403
    assert not hasattr(services.org,'seh_deployment_manager')


def test_hmac_forgery_tamper_and_replay_fail_closed(services):
    port=services.lms.server_port
    path='/v1/local/seh/deployment'
    stamp=str(int(time.time()));nonce=secrets.token_hex(16)
    headers={'X-LMS-Forwarded':'v1','X-LMS-Timestamp':stamp,
             'X-LMS-Nonce':nonce, 'X-LMS-Principal':'owner-device',
             'X-LMS-Signature':signature(services.key,'GET',path,stamp,nonce,b'','owner-device')}
    assert send(port,'GET',path,headers=headers)[0]==200
    code,data=send(port,'GET',path,headers=headers)
    assert code==403 and data['error']['code']=='LMS_FORWARD_REPLAY_REFUSED'
    mutated={**headers,'X-LMS-Nonce':secrets.token_hex(16)}
    assert send(port,'GET',path,headers=mutated)[0]==403
    forged={**headers,'X-LMS-Nonce':secrets.token_hex(16),
            'X-LMS-Signature':'0'*64}
    assert send(port,'GET',path,headers=forged)[0]==403
    wrong_path={**headers,'X-LMS-Nonce':secrets.token_hex(16)}
    assert send(port,'GET','/v1/local/services',headers=wrong_path)[0] in (401,403)
    assert send(port,'GET',path+'?query=1',headers=headers)[0]==400


def test_lms_unavailable_and_unknown_paths_fail_closed(tmp_path):
    assert local_path('/v1/org/admin/seh/deployment')=='/v1/local/seh/deployment'
    with pytest.raises(LMSForwardingError):
        local_path('/v1/org/admin/other')
    server=make_server('127.0.0.1',0,lambda e,s: [b'no'])
    port=server.server_port;server.server_close()
    with pytest.raises(LMSForwardingError,match='LMS_MANAGEMENT_UNAVAILABLE'):
        LMSManagementClient(port=port).request('GET','/v1/local/seh/deployment')
    with pytest.raises(LMSForwardingError,match='LMS_OPERATION_NOT_ADMITTED'):
        LMSManagementClient(port=port).request('POST','/v1/local/organization/enable',{})


def _await_receipt(manager, operation_id, *, timeout=3):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        receipt=manager.operation_status(operation_id)
        if receipt['phase'] in ('RECORDED','CANCELED'):
            return receipt
        time.sleep(.01)
    raise AssertionError('Timed out waiting for LMS operation receipt')


def test_durable_harness_receipt_prevents_reexecution_after_response_loss(tmp_path):
    root=root_state(tmp_path)
    invokes=[]
    def harness(args,*,control):
        invokes.append(args)
        if not args.execute_mutations:
            return {'status':'PLAN_ONLY','remote_mutation':False}
        return {'status':'REPLACEMENT_QUEUED_NOT_VERIFIED','application_uuid':'owned-resource'}
    manager=SLHSEHDeploymentManager(root=root,harness_runner=harness)
    body={'action':'deploy-seh','execute':True,'expected_generation':1,
          'confirm':'DEPLOY_SEH','operation_id':'mcel-seh-persistent-0000000001'}
    first=manager.harness(body)
    assert first['status']=='ACCEPTED'
    assert first['operation_id']==body['operation_id']
    assert _await_receipt(manager,body['operation_id'])['phase']=='RECORDED'
    new_process=SLHSEHDeploymentManager(root=root,harness_runner=harness)
    second=new_process.harness(body)
    assert second['replayed_receipt'] is True
    assert len(invokes)==1  # one complete harness lifecycle invocation
    state=new_process.operation_status(body['operation_id'])
    assert state['phase']=='RECORDED' and state['principal']=='local-machine'
    with pytest.raises(Exception,match='SEH_OPERATION_ID_CONFLICT'):
        new_process.harness({**body,'expected_generation':2})


def test_lost_harness_response_records_incomplete_without_global_busy_or_retry(tmp_path):
    root=root_state(tmp_path)
    calls=[]
    def error_harness(args,**kwargs):
        calls.append('called')
        if not args.execute_mutations:
            return {'status':'PLAN_ONLY','ready_for_undeploy':True}
        raise RuntimeError('transport response lost after Coolify mutation')
    manager=SLHSEHDeploymentManager(root=root,harness_runner=error_harness)
    body={'action':'undeploy-seh','execute':True,'expected_generation':1,
          'confirm':'UNDEPLOY_SEH','operation_id':'mcel-seh-uncertain-00000000001'}
    assert manager.harness(body)['status']=='ACCEPTED'
    receipt=_await_receipt(manager,body['operation_id'])
    assert receipt['phase']=='RECORDED' and receipt['result']['status']=='INCOMPLETE'
    assert receipt['result']['remote_mutation']=='UNKNOWN'
    other=SLHSEHDeploymentManager(root=root,harness_runner=error_harness)
    assert other.harness(body)['status']=='INCOMPLETE'
    assert len(calls)==1  # one harness lifecycle invocation failed once
    assert other.operation_status(body['operation_id'])['phase']=='RECORDED'
    state=other.operation_snapshot()
    assert state['state']=='IDLE' and state['busy'] is False
    assert state['last_operation']['operation_id']==body['operation_id']


def test_canonical_network_lms_receipt_route_is_allowlisted_only_with_network_session(monkeypatch):
    from main_computer import mcel_slh_network_client as module
    from main_computer.mcel_slh_network_client import NetworkSlhClient
    token='n'*42
    api=NetworkSlhClient()
    seen=[]
    monkeypatch.setattr(api,'_authenticated_request',lambda tk,m,p,b=None:seen.append((m,p)) or {'phase':'VERIFIED'})
    with module._SESSION_LOCK:
        module._SESSIONS[token]={'transport':None,'expires':int(time.time())+120}
    try:
        path='/v1/org/admin/seh/deployment/operations/mcel-seh-1234567890abcdef'
        assert api.seh_deployment_request(token,'GET',path)=={'phase':'VERIFIED'}
        assert seen==[('GET',path)]
        with pytest.raises(Exception):api.seh_deployment_request(token,'GET',path,{'forged':True})
        with pytest.raises(Exception):api.seh_deployment_request(token,'GET',path+'/../../other')
    finally:
        with module._SESSION_LOCK:module._SESSIONS.pop(token,None)


def test_remote_admin_principal_is_bound_into_lms_receipt(services):
    invocations=[]
    def fake_harness(args,*,control):
        invocations.append(args)
        if not args.execute_mutations:
            return {'status':'PLAN_ONLY','remote_mutation':False}
        return {'status':'REPLACEMENT_QUEUED_NOT_VERIFIED','application_uuid':'owned-app'}
    services.manager.harness_runner=fake_harness
    body={'action':'deploy-seh','execute':True,'expected_generation':1,
          'confirm':'DEPLOY_SEH','operation_id':'mcel-seh-remote-owner-12345678'}
    path='/v1/org/admin/seh/deployment/harness'
    assert send(services.org.server_port,'POST',path,body)[0]==401
    code,response=send(services.org.server_port,'POST',path,body,
        headers={'Authorization':'Bearer good'})
    assert code==200 and response['status']=='ACCEPTED'
    receipt=_await_receipt(services.manager,body['operation_id'])
    assert receipt['principal']=='owner-device'
    assert len(invocations)==1  # one complete harness lifecycle invocation
    # Losing the response and replaying the same ID reuses the durable receipt.
    code,response=send(services.org.server_port,'POST',path,body,
        headers={'Authorization':'Bearer good'})
    assert code==200 and response['replayed_receipt'] is True
    assert len(invocations)==1
    # A local OS caller cannot claim the remote administrator's receipt.
    local=LMSManagementClient(port=services.lms.server_port)
    with pytest.raises(LMSForwardingError,match='SEH_OPERATION_ID_CONFLICT'):
        local.request('POST','/v1/local/seh/deployment/harness',body)
