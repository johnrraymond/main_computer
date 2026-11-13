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
        'intent:seh-deployment-preview-deploy','intent:seh-deployment-deploy',
        'intent:seh-deployment-preview-undeploy','intent:seh-deployment-undeploy'}
    assert nodes['surface-node:slh.network-seh-lifecycle']['tabs'] == local['tabs']
    assert nodes['surface-node:slh.remote-seh-connection-details']['nodeKind'] == 'state-value'
    assert 'apiToken' not in json.dumps(ir['states'])


class FakeDirect:
    def __init__(self): self.calls=[]
    def request(self,method,path,token,body=None):
        self.calls.append((method,path,token,body))
        assert token == 't'*32
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
        return FakeDirect().request(method,path,'t'*32,body)


def test_local_and_network_deploy_plans_use_selected_slh_without_relay():
    direct=FakeDirect()
    data=execute_seh_deployment('inspect',{},target='local',local_token='t'*32,local_client=direct)
    assert data['deploymentStatus']=='UNDEPLOYED'
    assert [x['identity'] for x in data['controllers']]==['coolify-a','coolify-c']
    assert direct.calls==[('GET','/v1/org/admin/seh/deployment','t'*32,None)]
    plan=execute_seh_deployment('preview-deploy',{},target='local',local_token='t'*32,local_client=direct)
    assert plan['outcome']=='PLAN_ONLY'
    assert direct.calls[-1][-1]=={'action':'deploy-seh','execute':False,
                                 'expected_generation':None,'confirm':None}
    direct.calls.clear()
    deferred=execute_seh_deployment('deploy',{},target='local',local_token='t'*32,local_client=direct)
    assert deferred['outcome']=='PLAN_ONLY'  # fake execute returns a plan, never fabricated success
    assert [x[:2] for x in direct.calls]==[
        ('GET','/v1/org/admin/seh/deployment'),
        ('POST','/v1/org/admin/seh/deployment/harness'),
        ('POST','/v1/org/admin/seh/deployment/harness')]
    assert direct.calls[-1][-1]=={'action':'deploy-seh','execute':True,
                                 'expected_generation':33,'confirm':'DEPLOY_SEH'}
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


def test_preflight_refuses_unready_undeploy_before_execute():
    class NotReady(FakeDirect):
        def request(self,method,path,token,body=None):
            if method=='POST':return {'status':'PLAN_ONLY','ready_for_undeploy':False}
            return super().request(method,path,token,body)
    with pytest.raises(SEHDeploymentUIError,match='SEH_DEPLOYMENT_PREFLIGHT_REFUSED'):
        execute_seh_deployment('undeploy',{},target='local',local_token='t'*32,local_client=NotReady())


def test_secret_schema_and_request_rejection():
    client=FakeDirect()
    for args in ({'name':'coolify-a','url':'http://no-tls','apiToken':'abc123'},
                 {'name':'coolify-a','apiToken':'token'},
                 {'name':'../../bad','url':'https://good','apiToken':'abc123'}):
        if 'url' in args and args['url'].startswith('http://'):
            # Transport refuses origin at the backend, not this screen layer.
            continue
        with pytest.raises(SEHDeploymentUIError):
            execute_seh_deployment('register',args,target='local',local_token='t'*32,local_client=client)
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
                page.evaluate("async()=>window.__mcelSlh.app.execute('intent:login-administrator')")
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
            registration=notebook.locator('[data-mcel-operation-form="intent:seh-deployment-register"]')
            assert registration.locator('[name="apiToken"]').get_attribute('type')=='password'
            registration.locator('[name="name"]').fill('coolify-new')
            registration.locator('[name="url"]').fill('https://coolify-new.example.com')
            registration.locator('[name="apiToken"]').fill('test-token-1234')
            registration.locator('[type="submit"]').click()
            page.wait_for_function("() => window.sehAdmin.calls.some(x=>x.operation==='register')")
            page.wait_for_function("() => document.querySelector('[data-mcel-operation-form=\\\"intent:seh-deployment-select\\\"] select')?.querySelectorAll('option').length===4")
            selection=notebook.locator('[data-mcel-operation-form="intent:seh-deployment-select"]')
            selection.locator('select[name="name"]').select_option('coolify-new')
            selection.locator('[type="submit"]').click()
            page.wait_for_function("() => window.sehAdmin.calls.some(x=>x.operation==='select')")
            notebook.locator('[data-mcel-operation-tab="deployment"]').click()
            notebook.locator('[data-mcel-operation-action="Preflight Deploy"]').click()
            page.wait_for_function("() => window.sehAdmin.calls.some(x=>x.operation==='preview-deploy')")
            notebook.locator('[data-mcel-operation-action="Deploy"]').click()
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
