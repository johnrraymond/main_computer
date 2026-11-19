"""Guardrail and actual semantic-page tests for tools.dangerous_mutation.

These tests mutate only an in-process fake LMS; no production Coolify contact.
"""
from __future__ import annotations

import json

import pytest

from tools import dangerous_mutation as dm


UUID_A='abc12345orig'
UUID_B='abc12345replacement'


def _record(uuid=UUID_A, undeployed=True, generation=33):
    return {'status':'READ_ONLY','registered':True,'network':'mainnet','generation':generation,
            'deployment':{'application_uuid':uuid,'undeployed':undeployed}}


def _lms(uuid=UUID_A,phase='UNDEPLOYED',generation=33):
    return {'generation':generation,'applicationUuid':uuid,'deploymentStatus':phase,
            'selectedController':'coolify-a','controllers':[{'identity':'coolify-a'}]}


def test_preflight_discovers_registered_uuid_and_performs_no_mutation(tmp_path):
    result=dm.preflight(state_root=tmp_path,local_port=8897,inspect=lambda:_record(),lms=lambda:_lms())
    assert result['status']=='READ_ONLY'
    assert result['application_uuid']==UUID_A
    assert result['state']=='tombstone'
    assert dm.cycle_plan(result,cycle_active=False)==['deploy','undeploy']
    with pytest.raises(dm.MutationRefused,match='PINNED_SEH_UUID_CHANGED'):
        dm.preflight(state_root=tmp_path,local_port=8897,expect_uuid=UUID_B,
                     inspect=lambda:_record(),lms=lambda:_lms())
    with pytest.raises(dm.MutationRefused,match='LMS_CANONICAL_UUID_MISMATCH'):
        dm.preflight(state_root=tmp_path,local_port=8897,inspect=lambda:_record(),lms=lambda:_lms(uuid=UUID_B))


def test_active_requires_specific_outage_approval_and_unknown_refuses(tmp_path):
    active=dm.preflight(state_root=tmp_path,local_port=8897,inspect=lambda:_record(undeployed=False),lms=lambda:_lms(phase='CONNECTED_VERIFIED'))
    with pytest.raises(dm.MutationRefused,match='REQUIRES_CYCLE_ACTIVE'):
        dm.cycle_plan(active,cycle_active=False)
    assert dm.cycle_plan(active,cycle_active=True)==['undeploy','deploy']
    legacy=dm.preflight(state_root=tmp_path,local_port=8897,inspect=lambda:_record(undeployed=None),lms=lambda:_lms(phase='CONNECTED_VERIFIED'))
    assert legacy['state']=='active' and legacy['state_basis']=='lms-verified-runtime'
    unknown=dm.preflight(state_root=tmp_path,local_port=8897,inspect=lambda:_record(undeployed=None),lms=lambda:_lms(phase='TARGET_SELECTED'))
    with pytest.raises(dm.MutationRefused,match='AMBIGUOUS_SEH_STATE'):
        dm.cycle_plan(unknown,cycle_active=True)
    unverified_active=dm.preflight(state_root=tmp_path,local_port=8897,
                                  inspect=lambda:_record(undeployed=False),lms=lambda:_lms(phase='TARGET_SELECTED'))
    with pytest.raises(dm.MutationRefused,match='AMBIGUOUS_SEH_STATE'):
        dm.cycle_plan(unverified_active,cycle_active=True)


def test_preflight_refuses_conflicting_deployment_authorities(tmp_path):
    with pytest.raises(dm.MutationRefused,match='TOMBSTONE_CONTRADICTS_LIVE_RUNTIME'):
        dm.preflight(state_root=tmp_path,local_port=8897,
                     inspect=lambda:_record(undeployed=True),
                     lms=lambda:_lms(phase='CONNECTED_VERIFIED'))
    with pytest.raises(dm.MutationRefused,match='ACTIVE_CONTRADICTS_LMS_TOMBSTONE'):
        dm.preflight(state_root=tmp_path,local_port=8897,
                     inspect=lambda:_record(undeployed=False),
                     lms=lambda:_lms(phase='UNDEPLOYED'))


def test_gate_allows_only_one_real_browser_operation():
    gate=dm.BrowserGate('http://127.0.0.1:8765')
    url='http://127.0.0.1:8765/api/applications/slh/seh/deployment'
    body=lambda op,id=None:{'target':'local','operation':op,'arguments':{},'sessionTicket':'',**({'operationId':id} if id else {})}
    assert gate.classify(url,'POST',json.dumps(body('inspect')))=='ALLOW_READ'
    assert gate.classify(url,'POST',json.dumps(body('deploy','mcel-seh-'+'a'*32)))=='DENY_MUTATION'
    gate.arm('deploy')
    assert gate.classify(url,'POST',json.dumps(body('undeploy','mcel-seh-'+'a'*32)))=='DENY_MUTATION'
    assert gate.classify(url,'POST',json.dumps(body('deploy','wrong-id')))=='DENY_MUTATION'
    assert gate.classify(url,'POST',json.dumps(body('deploy','mcel-seh-'+'a'*32)))=='ALLOW_MUTATION'
    assert gate.classify(url,'POST',json.dumps(body('deploy','mcel-seh-'+'b'*32)))=='DENY_MUTATION'
    assert gate.classify(url.replace('127.0.0.1','evil.example'),'POST',json.dumps(body('inspect')))=='DENY_ORIGIN'
    assert gate.classify(url,'POST',json.dumps(body('register')))=='DENY_OPERATION'
    assert gate.classify(url,'POST',json.dumps(body('cancel-operation')))=='DENY_OPERATION'
    assert len(gate.mutations)==1


def test_live_cli_requires_two_approvals(monkeypatch,tmp_path):
    # argparse performs the refusal before any call to inspect or browser.
    with pytest.raises(SystemExit) as e:
        dm.main(['--mode','live','--execute-mutations','--report',str(tmp_path/'out.json')])
    assert e.value.code == 2


class FakePageAndLms:
    def __init__(self):
        self.uuid=UUID_A
        self.undeployed=True
        self.phase='UNDEPLOYED'
        self.issued=[]
        self.receipts={}
        self.generation=33

    def canonical(self,*args):
        return _record(self.uuid,self.undeployed,self.generation)

    def lms(self):
        return _lms(self.uuid,self.phase,self.generation)

    def execute(self,operation,args,*,target):
        assert target=='local'
        if operation == 'operation-state':
            return {'state':'IDLE','busy':False}
        assert operation=='operation-status'
        return self.receipts[args['operationId']]



@pytest.mark.parametrize('initial_active',[False,True])
def test_real_generated_page_clicks_deploy_then_undeploy_with_verified_receipts(monkeypatch,tmp_path,initial_active):
    fake=FakePageAndLms()
    if initial_active:
        fake.undeployed=False
        fake.phase='CONNECTED_VERIFIED'
    from main_computer.mcel_slh_application import render_slh_page
    gate_holder={}
    original_gate=dm.BrowserGate
    class TestGate(original_gate):
        def __init__(self,base):
            super().__init__(base)
            gate_holder['gate']=self
    monkeypatch.setattr(dm,'BrowserGate',TestGate)
    def lms(body):
        gate=gate_holder['gate']
        op=body['operation']
        decision=gate.classify('http://127.0.0.1:8765/api/applications/slh/seh/deployment',
                              'POST',json.dumps(body))
        assert decision.startswith('ALLOW_'),decision
        if op=='inspect':
            value=fake.lms()
        elif op=='operation-state':
            value={'schema':'main-computer.seh-management-operation-snapshot.v1',
                   'state':'IDLE','busy':False,'operation':None,'last_operation':None}
        elif op=='operation-status':
            value=fake.receipts[body['arguments']['operationId']]
        elif op in ('deploy','undeploy'):
            fake.issued.append(op)
            if op=='deploy':
                fake.uuid=UUID_B;fake.undeployed=False;fake.phase='CONNECTED_VERIFIED'
                status='CONNECTED_VERIFIED'
            else:
                fake.undeployed=True;fake.phase='UNDEPLOYED';status='SEH_UNDEPLOYED'
            oid=body['operationId']
            fake.receipts[oid]={'operation_id':oid,'phase':'RECORDED',
                'stage':'COMPLETED','action':op+'-seh',
                'result':{'status':status},'output':[]}
            value={'outcome':'accepted-deferred','message':'LMS accepted',
                   'operationId':oid,'applicationUuid':fake.uuid}
        return {'ok':True,'result':value}
    def fake_browser_open(page, baseline):
        page.expose_function('testLms',lms)
        page.evaluate('''() => {
          window.fetch=async(url,opts={})=>{
            if(url==='/api/applications/slh/seh/deployment'){
              const body=JSON.parse(opts.body||'{}');
              const value=await window.testLms(body);
              if(["deploy","undeploy"].includes(body.operation))window.__testRequested=body.operation;
              return {ok:true,status:200,json:async()=>value};
            }
            if(url==='/api/applications/slh/identities')return {ok:true,json:async()=>({
              schema:'mcel.slh.identities.v4b',state:'AVAILABLE',defaultDeviceId:'',
              identities:[],explanation:''})};
            if(url==='/api/applications/slh/observe?view=overview')return {ok:true,json:async()=>({
              ok:true,schema:'mcel.slh.first-slice-observation.v1',view:'overview',
              overview:{organizationState:'stopped',organizationEndpoint:'0.0.0.0:8897'}})};
            return {ok:false,status:403,json:async()=>({ok:false,error:{code:'FORBIDDEN'}})};
          };
        }''')
        page.set_content(render_slh_page())
        page.wait_for_function('() => !!window.__mcelSlh?.app',timeout=15000)
        page.evaluate("async()=>await window.__mcelSlh.app.execute('intent:read-seh-deployment')")
        page.locator(dm.NOTEBOOK+' [data-mcel-operation-tab="deployment"]').click()
    monkeypatch.setattr(dm,'browser_open',fake_browser_open)
    def fake_action(page,gate,operation,*,timeout):
        label='Deploy / Redeploy' if operation=='deploy' else 'Undeploy'
        gate.arm(operation)
        before=len(gate.mutations)
        button=page.locator(dm.NOTEBOOK+' [data-mcel-operation-action='+json.dumps(label)+']')
        assert button.is_enabled()
        button.click()
        assert page.locator('[data-mcel-lms-operation-modal]').is_visible()
        page.wait_for_function("""op=>window.__testRequested===op""",arg=operation,timeout=10000)
        assert len(gate.mutations)==before+1
        identifier=gate.mutations[-1]['operation_id']
        receipt=fake.receipts[identifier]
        expected=receipt['result']['status']
        page.wait_for_function("""expected=>document.querySelector('[data-mcel-lms-operation-modal]')?.textContent?.includes(expected)""",arg=expected,timeout=10000)
        return {'operation':operation,'operation_id':identifier,'phase':'RECORDED','status':expected,
                'ui_modal_confirmed':True,'page_http_confirmed':True}
    monkeypatch.setattr(dm,'browser_action',fake_action)
    monkeypatch.setattr(dm,'execute_seh_deployment',fake.execute)
    monkeypatch.setattr(dm,'read_lms_deployment',fake.lms)
    pre=dm.preflight(state_root=tmp_path,local_port=8897,inspect=fake.canonical,lms=fake.lms)
    result=dm.execute_browser_cycle(
        base='http://127.0.0.1:8765',pre=pre,
        state_root=tmp_path,local_port=8897,cycle_active=initial_active,timeout=25,
        read_canonical=fake.canonical)
    assert result['status']=='PASS_LIVE_UI_MUTATIONS'
    assert fake.issued==(['undeploy','deploy'] if initial_active else ['deploy','undeploy'])
    assert all(step['ui_modal_confirmed'] and step['page_http_confirmed'] for step in result['steps'])
    assert result['final_undeployed'] is (not initial_active)


def test_identity_gate_rejects_unapproved_records_and_bulk_actions():
    gate=dm.IdentityBrowserGate('http://127.0.0.1:8765','verified-admin')
    url='http://127.0.0.1:8765/api/applications/slh/identity-notebook'
    request=lambda operation,args:json.dumps({'target':'local','sessionTicket':'',
                                            'operation':operation,'arguments':args})
    assert gate.classify(url,'POST',request('read-pending',{}))=='ALLOW_IDENTITY_READ'
    assert gate.classify(url,'POST',request('accept',{'recordId':'a'*32}))=='DENY_IDENTITY_MUTATION'
    gate.arm_row('accept','a'*32)
    assert gate.classify(url,'POST',request('accept',{'recordId':'b'*32}))=='DENY_IDENTITY_MUTATION'
    assert gate.classify(url,'POST',request('accept',{'recordId':'a'*32}))=='ALLOW_IDENTITY_MUTATION'
    assert gate.classify(url,'POST',request('accept',{'recordId':'a'*32}))=='DENY_IDENTITY_MUTATION'
    assert gate.classify(url,'POST',request('empty-trash',{}))=='DENY_IDENTITY_MUTATION'
    assert gate.classify(url,'POST',request('set-retention',{'enabled':False}))=='DENY_IDENTITY_MUTATION'
    assert gate.classify(url,'POST',request('remove',{'recordId':'old-admin'}))=='DENY_IDENTITY_MUTATION'
    setup='http://127.0.0.1:8765/api/applications/slh/setup/action'
    assert gate.classify(setup,'POST',json.dumps({'action':'authenticate','arguments':{'adminDeviceId':'wrong'}}))=='DENY_IDENTITY_AUTH'
    assert gate.classify(setup,'POST',json.dumps({'action':'authenticate','arguments':{'adminDeviceId':'verified-admin'}}))=='ALLOW_ADMIN_AUTH'
    assert gate.classify(setup,'POST',json.dumps({'action':'toggle','arguments':{}}))=='DENY_IDENTITY_AUTH'


def test_preflight_cli_runs_inspect_internally_without_browser_or_mutation(monkeypatch,tmp_path):
    observed=[]
    def fake_preflight(**kwargs):
        observed.append(kwargs)
        return {'status':'READ_ONLY','application_uuid':UUID_A,'state':'tombstone',
                'lms_idle':True,'deployment_phase':'UNDEPLOYED'}
    monkeypatch.setattr(dm,'preflight',fake_preflight)
    monkeypatch.setattr(dm,'execute_browser_cycle',lambda **kwargs: (_ for _ in ()).throw(AssertionError('browser cannot run')))
    destination=tmp_path/'report.json'
    assert dm.main(['--mode','preflight','--scope','seh','--report',str(destination)])==0
    assert len(observed)==1
    data=json.loads(destination.read_text())
    assert data['status']=='READ_ONLY' and data['preflight']['application_uuid']==UUID_A


def test_all_scope_checks_seh_before_identity_mutations(monkeypatch,tmp_path):
    def unknown(**kwargs):
        return {'status':'READ_ONLY','application_uuid':UUID_A,
                'state':'unknown','lms_idle':True}
    monkeypatch.setattr(dm,'preflight',unknown)
    from tools import mcel_slh_dangerous_functional_acceptance as old
    monkeypatch.setattr(old,'discover_admin_device',lambda **kw:'verified-admin')
    monkeypatch.setattr(dm,'identity_ui_cycle',lambda **kw:(_ for _ in ()).throw(AssertionError('must refuse before identity mutations')))
    destination=tmp_path/'report.json'
    assert dm.main(['--mode','live','--scope','all','--execute-mutations',
                    '--acknowledge-real-mutations','--cycle-active-seh',
                    '--report',str(destination)])==1
    assert json.loads(destination.read_text())['failed_gate']=='AMBIGUOUS_SEH_STATE_NO_DESTRUCTIVE_CYCLE'


def test_all_scope_combines_both_browser_lanes_only_when_verified(monkeypatch,tmp_path):
    calls=[]
    monkeypatch.setattr(dm,'preflight',lambda **kw:{'status':'READ_ONLY','application_uuid':UUID_A,
                   'state':'tombstone','lms_idle':True})
    from tools import mcel_slh_dangerous_functional_acceptance as old
    monkeypatch.setattr(old,'discover_admin_device',lambda **kw:'verified-admin')
    def identity_ui(**kwargs):
        calls.append('identities')
        return {'status':'PASS_LIVE_IDENTITY_UI_MUTATIONS'}
    def seh_ui(**kwargs):
        calls.append('seh')
        return {'status':'PASS_LIVE_UI_MUTATIONS'}
    monkeypatch.setattr(dm,'identity_ui_cycle',identity_ui)
    monkeypatch.setattr(dm,'execute_browser_cycle',seh_ui)
    destination=tmp_path/'report.json'
    assert dm.main(['--mode','live','--execute-mutations','--acknowledge-real-mutations',
                    '--report',str(destination)])==0
    assert calls==['identities','seh']
    data=json.loads(destination.read_text())
    assert data['status']=='PASS_LIVE_UI_MUTATIONS'
    assert set(data['groups'])=={'identities','seh'}


def test_identity_gate_allows_exact_connection_status_read_but_no_connection_mutations():
    gate=dm.IdentityBrowserGate('http://127.0.0.1:8765','verified-admin')
    url='http://127.0.0.1:8765/api/applications/slh/seh/lifecycle'
    body=lambda operation:json.dumps({'target':'local','sessionTicket':'','operation':operation})
    assert gate.classify(url,'POST',body('status'))=='ALLOW_CONNECTION_STATUS_READ'
    for operation in ('start','stop','restart'):
        assert gate.classify(url,'POST',body(operation))=='DENY_CONNECTION_MUTATION'
    assert gate.classify(url,'POST',json.dumps({'target':'network',
        'sessionTicket':'token','operation':'status'}))=='DENY_CONNECTION_MUTATION'
