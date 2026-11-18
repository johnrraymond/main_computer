"""SEH Lifecycle UI: bounded Coolify controls, canonical deploy and no relay authority."""
from __future__ import annotations

import json
import shutil

import pytest

from main_computer.mcel_slh_application import compile_slh_application, render_slh_page
from main_computer.mcel_slh_seh_deployment import execute_seh_deployment, SEHDeploymentUIError
from main_computer.mcel_slh_network_client import NetworkConnectionError
from tests.test_mcel_seh_management_oracle_v1 import _browser_mock


def test_semantic_deployment_contract_is_typed_and_not_remote_authority():
    ir, _ = compile_slh_application()
    nodes = {n['id']: n for n in ir['surfaces'][0]['nodes']}
    local = nodes['surface-node:slh.local-seh-lifecycle']
    tabs = {t['id']: t for t in local['tabs']}
    assert [t['id'] for t in local['tabs']] == [
        'configuration','deployment','connection','status','diagnostics']
    assert len(tabs['configuration']['forms']) == 2
    assert tabs['configuration']['forms'][0]['secretFields'] == ['apiToken']
    assert tabs['configuration']['forms'][1]['choicesFrom']['name']['path'] == 'controllers'
    assert {x['intent']['ref'] for x in tabs['deployment']['actions'].values()} == {
        'intent:seh-deployment-deploy','intent:seh-deployment-undeploy'}
    assert nodes['surface-node:slh.network-seh-lifecycle']['tabs'] == local['tabs']
    assert nodes['surface-node:slh.remote-seh-connection-details']['nodeKind'] == 'state-value'
    assert 'apiToken' not in json.dumps(ir['states'])


class FakeDirect:
    def __init__(self): self.calls=[]
    def request(self,method,path,body=None):
        self.calls.append((method,path,body))
        if method=='GET':
            return {'schema':'main-computer.slh-seh-management.v1', 'status':'READ_ONLY',
                    'network':'mainnet','generation':33,'controllers':[
                        {'identity':'coolify-a'},{'identity':'coolify-c'}],
                    'deployment':{'coolify':{'source':'networks.mainnet.coolify.controllers.coolify-a'},
                                  'deployment':{'application_uuid':'previous-uuid'}},
                    'deployment_phase':'UNDEPLOYED'}
        return {'status':'PLAN_ONLY','application_uuid':'previous-uuid','remote_mutation':False}


class FakeNetwork:
    def __init__(self):self.calls=[]
    def seh_deployment_request(self,ticket,method,path,body=None):
        self.calls.append((ticket,method,path,body))
        assert ticket=='n'*40
        return FakeDirect().request(method,path,body)


def test_local_and_network_deploy_plans_use_selected_slh_without_relay():
    direct=FakeDirect()
    data=execute_seh_deployment('inspect',{},target='local',local_client=direct)
    assert data['deploymentStatus']=='UNDEPLOYED'
    assert [x['identity'] for x in data['controllers']]==['coolify-a','coolify-c']
    assert direct.calls==[('GET','/v1/local/seh/deployment',None)]
    plan=execute_seh_deployment('preview-deploy',{},target='local',local_client=direct)
    assert plan['outcome']=='PLAN_ONLY'
    assert direct.calls[-1][-1]=={'action':'deploy-seh','execute':False,
                                 'expected_generation':None,'confirm':None}
    direct.calls.clear()
    deferred=execute_seh_deployment('deploy',{},target='local',local_client=direct)
    assert deferred['outcome']=='PLAN_ONLY'  # fake execute returns a plan, never fabricated success
    assert [x[:2] for x in direct.calls]==[
        ('GET','/v1/local/seh/deployment'),
        ('POST','/v1/local/seh/deployment/harness')]
    assert {k:v for k,v in direct.calls[-1][-1].items() if k!='operation_id'}=={
        'action':'deploy-seh','execute':True,
        'expected_generation':33,'confirm':'DEPLOY_SEH'}
    assert direct.calls[-1][-1]['operation_id'].startswith('mcel-seh-')
    remote=FakeNetwork()
    execute_seh_deployment('preview-undeploy',{},target='network',
                           remote_ticket='n'*40,network_client=remote)
    assert remote.calls[-1][-1]['action']=='undeploy-seh'
    for target in ('remote-seh','seh'):
        with pytest.raises(SEHDeploymentUIError,match='SEH_DEPLOYMENT_TARGET_REFUSED'):
            execute_seh_deployment('deploy',{},target=target,remote_ticket='n'*40,network_client=remote)
    with pytest.raises(SEHDeploymentUIError,match='SEH_DEPLOYMENT_TARGET_MISMATCH'):
        execute_seh_deployment('inspect',{},target='local',local_token='t'*32,remote_ticket='n'*40)
    with pytest.raises(SEHDeploymentUIError,match='SEH_DEPLOYMENT_TARGET_MISMATCH'):
        execute_seh_deployment('inspect',{},target='network',local_token='t'*32,remote_ticket='n'*40)


def test_undeploy_dispatches_to_lms_without_blocking_on_preflight():
    # The worker performs preflight and records its refusal under an operation
    # ID; MCEL must not block the UI on an unobservable preflight call.
    direct=FakeDirect()
    execute_seh_deployment('undeploy',{},target='local',local_client=direct)
    assert [x[:2] for x in direct.calls]==[
        ('GET','/v1/local/seh/deployment'),
        ('POST','/v1/local/seh/deployment/harness')]
    assert direct.calls[-1][-1]['execute'] is True


def test_secret_schema_and_request_rejection():
    client=FakeDirect()
    for args in ({'name':'coolify-a','url':'http://no-tls','apiToken':'abc123'},
                 {'name':'coolify-a','apiToken':'token'},
                 {'name':'../../bad','url':'https://good','apiToken':'abc123'}):
        if 'url' in args and args['url'].startswith('http://'):
            # Transport refuses origin at the backend, not this screen layer.
            continue
        with pytest.raises(SEHDeploymentUIError):
            execute_seh_deployment('register',args,target='local',local_client=client)
    assert not client.calls


@pytest.mark.parametrize('target',['overview','network','remote-seh'])
def test_browser_deployment_controls_are_selected_target_scoped(target):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True,
          executable_path=shutil.which('chromium') or None,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':1200,'height':840})
            errors=[]
            page.on('pageerror', lambda error:errors.append(str(error)))
            page.on('dialog', lambda dialog:dialog.accept())
            page.evaluate(_browser_mock())
            page.evaluate('''() => {
                window.sehAdmin={selected:'coolify-a',names:['coolify-a','coolify-c'],calls:[]};
                const previous=window.fetch;
                window.fetch=async(url,options={})=>{
                  if(String(url).endsWith('/seh/deployment')){
                    const body=JSON.parse(options.body||'{}');window.sehAdmin.calls.push(body);
                    if(body.operation==='operation-state')return {ok:true,json:async()=>({ok:true,result:{
                      schema:'main-computer.seh-management-operation-snapshot.v1',state:'IDLE',busy:false,operation:null}})};
                    const value=window.sehAdmin;
                    if(body.operation==='inspect')return {ok:true,json:async()=>({ok:true,
                      result:{generation:33,selectedController:value.selected,applicationUuid:'old-app',
                        deploymentStatus:'UNDEPLOYED',controllers:value.names.map(identity=>({identity}))}})};
                    if(body.operation==='register')value.names.push(body.arguments.name);
                    if(body.operation==='select')value.selected=body.arguments.name;
                    return {ok:true,json:async()=>({ok:true,result:{outcome:'PLAN_ONLY',
                      message:'Preflight only',applicationUuid:'old-app'}})};
                  }
                  return previous(url,options);
                };
            }''')
            page.set_content(render_slh_page(target))
            page.wait_for_function('() => !!window.__mcelSlh?.app')
            notebook=page.locator('[data-mcel-operation-notebook]:visible')
            if target=='remote-seh':
                assert notebook.count()==0
                assert not page.evaluate('window.sehAdmin.calls')
                assert not errors,errors
                return
            if target=='overview':
                page.evaluate("async()=>window.__mcelSlh.app.execute('intent:read-seh-deployment')")
                transport='local'
            else:
                form=page.locator('form[data-intent="intent:connect-network-slh"]')
                form.locator('[name="address"]').fill('192.168.1.8')
                form.locator('[name="port"]').fill('8899')
                form.locator('[type="submit"]').click()
                transport='network'
            page.wait_for_function("() => window.sehAdmin.calls.some(x=>x.operation==='inspect')")
            assert notebook.locator('[data-mcel-operation-tab]').all_text_contents()==[
                'Configuration','Deployment','Connection','Status','Diagnostics']
            assert notebook.locator('[data-mcel-operation-form]').count()==2
            # Configuration forms remain available, but this browser test is
            # about exactly two lifecycle actions, not a mocked registry write.
            assert notebook.locator('[data-mcel-operation-form="intent:seh-deployment-register"]').count()==1
            assert notebook.locator('[data-mcel-operation-form="intent:seh-deployment-select"]').count()==1
            notebook.locator('[data-mcel-operation-tab="deployment"]').click()
            assert notebook.locator('[data-mcel-operation-action]').all_text_contents()==[
                'Deploy / Redeploy','Undeploy']
            notebook.locator('[data-mcel-operation-action="Deploy / Redeploy"]').click()
            page.wait_for_function("() => window.sehAdmin.calls.some(x=>x.operation==='deploy')")
            assert all(x['target']==transport for x in page.evaluate('window.sehAdmin.calls'))
            assert all(x['target']!='remote-seh' for x in page.evaluate('window.sehAdmin.calls'))
            assert not errors,errors
        finally:
            browser.close()


def test_real_network_client_deployment_transport_requires_direct_authenticated_session(monkeypatch):
    import time
    from main_computer import mcel_slh_network_client as module
    from main_computer.mcel_slh_network_client import NetworkSlhClient
    client = NetworkSlhClient()
    ticket = 'q' * 42
    sent = []
    monkeypatch.setattr(client, '_authenticated_request',
                        lambda tk, method, path, body=None:
                            sent.append((tk,method,path,body)) or {'status':'READ_ONLY'})
    try:
        with module._SESSION_LOCK:
            module._SESSIONS[ticket] = {'transport': None, 'expires': int(time.time())+120}
        assert client.seh_deployment_request(ticket, 'GET',
               '/v1/org/admin/seh/deployment') == {'status':'READ_ONLY'}
        assert sent[-1][1:3] == ('GET','/v1/org/admin/seh/deployment')
        with pytest.raises(NetworkConnectionError, match='NETWORK_OPERATION_NOT_ADMITTED'):
            client.seh_deployment_request(ticket,'POST','/v1/org/admin/seh/deployment/harness',
                                          {'action':'deploy-seh'})
        with pytest.raises(NetworkConnectionError, match='NETWORK_OPERATION_NOT_ADMITTED'):
            client.seh_deployment_request(ticket,'POST','/v1/admin/devices',{})
        with module._SESSION_LOCK:
            module._SESSIONS[ticket]['transport'] = {'kind':'seh', 'seh_url':'wss://relay.invalid'}
        with pytest.raises(NetworkConnectionError,match='NETWORK_SESSION_TARGET_MISMATCH'):
            client.seh_deployment_request(ticket,'GET','/v1/org/admin/seh/deployment')
        assert len(sent) == 1
    finally:
        with module._SESSION_LOCK:
            module._SESSIONS.pop(ticket, None)


def test_local_seh_notebook_reads_coolify_directly_without_authentication():
    """The removed Service Lifecycle panel must not strand the saved controller.

    Local LMS does not depend on a selected identity or ORG-SLH sign-in.
    """
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True,
            executable_path=shutil.which('chromium') or None, args=['--no-sandbox'])
        try:
            page = browser.new_page(viewport={'width':1050,'height':800})
            errors=[]
            page.on('pageerror', lambda e:errors.append(str(e)))
            page.on('dialog', lambda d:d.accept())
            page.evaluate(_browser_mock())
            # Explicitly deselected identity must not block local LMS reads.
            page.evaluate("""() => Object.defineProperty(window,'sessionStorage',{
              configurable:true,value:{_store:{'mcel.slh.selected-identity.v2.local':'__NONE__'},
              getItem(k){return this._store[k]??null},setItem(k,v){this._store[k]=v}}})""")
            page.evaluate('''() => {
                window.coolifyInspections=[];
                const original=window.fetch;
                window.fetch=async(url,options={})=>{
                    if(String(url)==='/api/applications/slh/seh/deployment'){
                        const body=JSON.parse(options.body);
                        if(body.operation==='operation-state')return {ok:true,json:async()=>({ok:true,result:{
                          schema:'main-computer.seh-management-operation-snapshot.v1',state:'IDLE',busy:false,operation:null}})};
                        window.coolifyInspections.push(body);
                        if(body.operation!=='inspect')throw Error('UNEXPECTED_OPERATION');
                        if(options.headers?.Authorization)
                            throw Error('UNEXPECTED_ORG_BEARER');
                        return {ok:true,json:async()=>({ok:true,result:{
                          generation:33,controllers:[{identity:'coolify-a'},{identity:'coolify-c'}],
                          selectedController:'coolify-c',applicationUuid:'former-app-uuid',
                          deploymentStatus:'UNDEPLOYED'}})};
                    }
                    return original(url,options);
                };
            }''')
            page.set_content(render_slh_page('overview'))
            page.wait_for_function('() => !!window.__mcelSlh?.app')
            notebook=page.locator('[data-mcel-operation-notebook="surface-node:slh.local-seh-lifecycle"]')
            select=notebook.locator('[data-mcel-operation-form="intent:seh-deployment-select"] select')
            authenticate=notebook.locator('[data-mcel-operation-auth]')
            assert authenticate.count()==0
            # Opening Configuration already triggers the first LMS inspection.
            # There is no need to click its already-selected tab.
            page.wait_for_function('() => window.coolifyInspections.length > 0')
            page.wait_for_function('''() => document.querySelector(
              '[data-mcel-operation-form="intent:seh-deployment-select"] select')?.value==='coolify-c' ''')
            assert select.locator('option').all_text_contents()==[
                'Select a saved controller','coolify-a','coolify-c']
            assert authenticate.count()==0
            assert notebook.locator('[data-mcel-operation-form="intent:seh-deployment-select"] button').is_enabled()
            page.wait_for_function("() => !!window.__mcelSlh.app.readState('state:local-identities')")
            # Identity restoration is independent from local LMS inspection.
            assert not [call for call in page.evaluate('window.calls')
                        if call['url']=='/api/applications/slh/setup/action']
            # Local management remains populated across identity selection and deselection.
            page.evaluate("async()=>await window.__mcelSlh.app.execute('intent:choose-identity',{key:'one'})")
            assert select.input_value()=='coolify-c'
            page.get_by_role('button',name='Deselect identity').click()
            page.wait_for_function("() => window.__mcelSlh.app.readState('state:selected-local-identity')==='' ")
            assert select.input_value()=='coolify-c'
            # Mounted pages now refresh read-only SEH deployment observations.
            inspections=page.evaluate('window.coolifyInspections')
            assert inspections and all(call['operation']=='inspect' for call in inspections)
            assert not errors,errors
        finally:
            browser.close()


def test_local_seh_inspect_does_not_depend_on_failed_org_auth():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True,
          executable_path=shutil.which('chromium') or None, args=['--no-sandbox'])
        try:
            page=browser.new_page()
            page.evaluate(_browser_mock())
            page.evaluate('''() => {
                window.coolifyInspections=0;
                window.authRequests=0;
                const original=window.fetch;
                window.fetch=async(url,opts={})=>{
                    if(String(url).includes('/seh/deployment')){
                        if(JSON.parse(opts.body||'{}').operation==='operation-state')return {ok:true,json:async()=>({ok:true,result:{
                          schema:'main-computer.seh-management-operation-snapshot.v1',state:'IDLE',busy:false,operation:null}})};
                        window.coolifyInspections++;
                        if(opts.headers?.Authorization)throw Error('UNEXPECTED_BEARER');
                        return {ok:true,json:async()=>({ok:true,result:{
                          generation:33,controllers:[{identity:'coolify-a'}],
                          selectedController:'coolify-a',applicationUuid:'',
                          deploymentStatus:'TARGET_SELECTED'}})};
                    }
                    if(String(url)==='/api/applications/slh/setup/action' &&
                        JSON.parse(opts.body||'{}').action==='authenticate'){
                        window.authRequests++;
                        return {ok:false,json:async()=>({error:{code:'UNAUTHORIZED'}})};
                    }
                    return original(url,opts);
                };
            }''')
            page.set_content(render_slh_page('overview'))
            page.wait_for_function('() => !!window.__mcelSlh?.app')
            notebook=page.locator('[data-mcel-operation-notebook="surface-node:slh.local-seh-lifecycle"]')
            notebook.locator('[data-mcel-operation-tab="configuration"]').click()
            page.wait_for_function("() => window.coolifyInspections>0")
            select=notebook.locator('[data-mcel-operation-form="intent:seh-deployment-select"] select')
            page.wait_for_function("() => document.querySelector('[data-mcel-operation-form=\"intent:seh-deployment-select\"] select')?.value==='coolify-a'")
            assert notebook.locator('[data-mcel-operation-auth]').count()==0
            assert page.evaluate('window.authRequests')==0
            assert select.input_value()=='coolify-a'
        finally:
            browser.close()


def test_saved_mother_coolify_identities_reach_the_ui_projection(tmp_path):
    """Use verified identity-state files, not a fabricated controllers response."""
    from tests.test_slh_seh_coolify_management_backend import root_state
    from main_computer.slh.seh_deployment_management import SLHSEHDeploymentManager
    manager=SLHSEHDeploymentManager(root=root_state(tmp_path),state_dir=tmp_path/'slh')
    manager.select({'name':'coolify-a','expected_generation':1,
                    'operation_id':'select-controller-test'})
    class Direct:
        def request(self,method,path,body=None):
            assert method=='GET' and path=='/v1/local/seh/deployment'
            return manager.inspect({})
    projection=execute_seh_deployment('inspect',{},target='local',local_client=Direct())
    assert projection['controllers']==[{'identity':'coolify-a'}]
    assert projection['selectedController']=='coolify-a'
    assert projection['generation']==2
    assert projection['deploymentStatus']=='TARGET_SELECTED'
    assert 'api_token' not in json.dumps(projection)
