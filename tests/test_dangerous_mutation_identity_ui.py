"""Disposable, real HTTP identity authority with generated browser notebook clicks.

Actual network navigation to loopback is disabled in this test container, so
only the browser transport is in-memory; the ORG-SLH authority remains real.
"""
import json

from main_computer.mcel_slh_application import render_slh_page
from main_computer.mcel_slh_identity_notebook import execute_notebook
from tests.test_mcel_slh_dangerous_admin_discovery import disposable_org
from tools import dangerous_mutation as dm
from tools.mcel_slh_dangerous_functional_acceptance import _open_lms_authenticated_session
from tools.slh_authenticator_cli import _request


def test_live_identity_browser_mutations_against_disposable_real_org(tmp_path,monkeypatch):
    with disposable_org(tmp_path,lms_signer=True) as (port,credentials,root,authority):
        gatebox={}
        base='http://127.0.0.1:8765'
        original=dm.IdentityBrowserGate
        class Gate(original):
            def __init__(self,base,device):
                super().__init__(base,device)
                gatebox['gate']=self
        monkeypatch.setattr(dm,'IdentityBrowserGate',Gate)
        tokens=[]
        class Client:
            def __init__(self,tok):self.tok=tok
            def request(self,method,path,token,body):
                assert token==self.tok
                return _request(port,method,path,body,token=token)
        def api(url,options=None):
            options=options or {}
            body=json.loads(options.get('body') or '{}') if options.get('body') else None
            method=options.get('method') or 'GET'
            gate=gatebox['gate']
            decision=gate.classify(base+url,method,options.get('body'))
            assert decision.startswith('ALLOW_'),(url,body,decision)
            if url=='/api/applications/slh/identities':
                value={'schema':'mcel.slh.identities.v4b','state':'AVAILABLE',
                       'defaultDeviceId':root,'identities':[{'deviceId':root,
                         'label':'Owner','fingerprint':'a'*64,'defaultStatus':'DEFAULT'}],
                       'explanation':''}
            elif url=='/api/applications/slh/observe?view=overview':
                value={'ok':True,'schema':'mcel.slh.first-slice-observation.v1',
                       'view':'overview','overview':{'organizationState':'running',
                       'organizationEndpoint':'0.0.0.0:8897'}}
            elif url=='/api/applications/slh/observe?view=workers':
                value={'schema':'mcel.slh.first-slice-observation.v1','view':'workers',
                       'workerSummary':{'observationState':'AUTHENTICATED'}}
            elif url=='/api/applications/slh/seh/lifecycle':
                assert body=={'target':'local','sessionTicket':'','operation':'status'}
                value={'ok':True,'result':{'phase':'DISCONNECTED','connectionEnabled':False,
                    'connected':False,'gatewayReady':False,'configurationOwner':'ORG-SLH',
                    'relayRole':'Opaque passthrough','lastErrorClass':''}}
            elif url=='/api/applications/slh/setup/action':
                if body['action']=='authenticate':
                    token,who=_open_lms_authenticated_session(port,root)
                    tokens.append(token)
                    value={'outcome':'AUTHENTICATED','message':'Authenticated','access_token':token,'deviceId':root}
                else:
                    if tokens:
                        _request(port,'POST','/v1/auth/logout',{},token=tokens.pop())
                    value={'outcome':'SIGNED_OUT','message':'Signed out','deviceId':''}
            elif url=='/api/applications/slh/identity-notebook':
                assert tokens,'notebook requires administrator authentication'
                result=execute_notebook(body['operation'],body['arguments'],target='local',
                                        local_token=tokens[-1],local_client=Client(tokens[-1]))
                value={'ok':True,'result':result}
            elif url=='/api/applications/slh/seh/deployment':
                if body['operation']=='inspect':
                    value={'ok':True,'result':{'generation':33,'selectedController':'coolify-a',
                       'applicationUuid':'test-uuid','deploymentStatus':'UNDEPLOYED',
                       'controllers':[{'identity':'coolify-a'}]}}
                elif body['operation']=='operation-state':
                    value={'ok':True,'result':{'schema':'main-computer.seh-management-operation-snapshot.v1',
                        'state':'IDLE','busy':False,'operation':None,'last_operation':None}}
                else:raise AssertionError('No SEH mutation allowed')
            else:raise AssertionError('Unexpected browser request '+url)
            return value
        def load(page):
            page.expose_function('testIdentityApi',api)
            page.evaluate('''() => {
              window.fetch=async(url,options={})=>{
                const value=await window.testIdentityApi(url,options);
                return {ok:true,status:200,json:async()=>value};
              };
            }''')
            page.set_content(render_slh_page())
        result=dm.identity_ui_cycle(base=base,port=port,admin_device=root,page_loader=load)
        assert result['status']=='PASS_LIVE_IDENTITY_UI_MUTATIONS',result
        assert result['cleanup']=='VERIFIED'
        assert {x['ui_operation'] for x in result['steps']}=={'accept','reject','delete','reactivate','remove'}
        assert [d['id'] for d in authority.devices()]==[root]
        assert not tokens


def test_ui_unavailable_refuses_before_any_enrollment_is_created(tmp_path,monkeypatch):
    with disposable_org(tmp_path,lms_signer=True) as (port,credentials,root,authority):
        def unavailable(page, admin_device, *, timeout_ms=18000):
            raise dm.MutationRefused('UI_IDENTITY_DIRECTORY_UNAVAILABLE')
        monkeypatch.setattr(dm,'_identity_ui_readiness_gate',unavailable)
        result=dm.identity_ui_cycle(
            base='http://127.0.0.1:8765',port=port,admin_device=root,
            page_loader=lambda page:page.set_content(render_slh_page()))
        assert result['status']=='INCOMPLETE'
        assert result['failed_gate']=='UI_IDENTITY_DIRECTORY_UNAVAILABLE'
        assert result['stage']=='waiting-for-verified-ui-prerequisites'
        assert result['created']==[]
        assert result['steps']==[]
        assert result['cleanup']=='VERIFIED'
        assert [d['id'] for d in authority.devices()]==[root]
