"""Stage 1 ORG-SLH configuration workspace: read-only identity and inert draft.

The tests mount the actual compiled SLH semantic page with a fake identities
endpoint. They neither create organization state nor contact a real service.
"""
from __future__ import annotations

import shutil
from playwright.sync_api import sync_playwright

from main_computer.mcel_slh_application import compile_slh_application, render_slh_page


MOCK = r'''() => {
  const saved = {};
  Object.defineProperty(window,'sessionStorage',{configurable:true,value:{
    getItem:key=>saved[key] ?? null,
    setItem:(key,value)=>{saved[key]=String(value)},
    removeItem:key=>{delete saved[key]}
  }});
  window.slhCalls=[];
  window.fetch=async (url,options={})=>{
    window.slhCalls.push({url,method:options.method||'GET'});
    if(url==='/api/applications/slh/observe?view=overview')return {ok:true,json:async()=>({
      ok:true,schema:'mcel.slh.first-slice-observation.v1',view:'overview',
      overview:{organizationState:'not_configured',organizationEndpoint:'NOT_CONFIGURED'}})};
    if(url==='/api/applications/slh/identities')return {ok:true,json:async()=>({
      schema:'mcel.slh.identities.v4b',state:'AVAILABLE',defaultDeviceId:'dev-default',
      identities:[
        {deviceId:'dev-default',label:'Default identity',fingerprint:'a'.repeat(64),defaultStatus:'DEFAULT'},
        {deviceId:'dev-second',label:'Second identity',fingerprint:'b'.repeat(64),defaultStatus:'ADDITIONAL'}
      ],explanation:'Local identities available; not authenticated'
    })};
    throw Error('UNEXPECTED_SLH_API:'+url);
  };
}'''


def _browser(playwright):
    return playwright.chromium.launch(headless=True, executable_path=shutil.which('chromium') or None)


def _open(page,view):
    page.set_content(render_slh_page(view))
    page.wait_for_function("() => window.__mcelSlh?.app.readState('state:local-identities')?.length===2")


def test_patch1_compiler_declares_configuration_draft_and_no_legacy_workspace_controls():
    ir,composition=compile_slh_application()
    nodes={node['id']:node for node in ir['surfaces'][0]['nodes']}
    form=nodes['surface-node:slh.configure-org-slh']
    assert form['nodeKind']=='form' and form['intent']['ref']=='intent:configure-and-enable-organization' 
    assert form['fields']=={'bindAddress':{'kind':'string'},'port':{'kind':'integer'}}
    assert form['defaults']=={'bindAddress':'0.0.0.0','port':8899}
    assert form['submitLabel']=='Configure ORG-SLH'
    assert {i['id'] for i in ir['intents']} == {
        'intent:refresh-identities','intent:refresh-identity-status',
        'intent:choose-identity','intent:select-target','intent:connect-network-slh',
        'intent:disconnect-network-slh',
        'intent:refresh-lifecycle','intent:configure-and-enable-organization','intent:disable-organization'
    }
    assert {effect['risk'] for effect in ir['effects']}=={'external-read','consequential'}
    assert nodes['surface-node:slh.first-administrator-identity']['label']=='Proposed first administrator'
    assert nodes['surface-node:slh.first-administrator-identity']['value']['state']['ref']=='state:selected-local-identity'
    assert not any('refresh-' in node['id'] or any(x in node['id'] for x in [
        'worker-bank','login-administrator','activate-organization','start-local',
        'rename-worker','service-status','health-checks','observation-evidence'
    ]) for node in nodes.values())
    assert len([r for r in composition['regions'] if r['recipe']=='task'])==2
    assert len([r for r in composition['regions'] if r['recipe']=='commands'])==2
    assert ir['surfaces'][0]['purpose']=='manage' 
    assert not any(r['recipe']=='selection-echo' for r in composition['regions'])


def test_local_configuration_ui_has_no_effects_and_identity_selection_drives_first_admin():
    with sync_playwright() as playwright:
        browser=_browser(playwright)
        try:
            page=browser.new_page(viewport={'width':1280,'height':800})
            errors=[]
            page.on('pageerror',lambda exc:errors.append(str(exc)))
            page.evaluate(MOCK)
            _open(page,'overview')
            assert page.get_by_role('heading',name='Configuration').count()==1
            assert page.locator('form[data-intent="intent:configure-and-enable-organization"] [data-mcel-reference-caption]').inner_text()=='Proposed first administrator'
            form=page.locator('form[data-intent="intent:configure-and-enable-organization"]')
            assert form.count()==1
            assert form.locator('[name="bindAddress"]').input_value()=='0.0.0.0'
            assert form.locator('[name="port"]').input_value()=='8899'
            button=form.get_by_role('button',name='Configure ORG-SLH')
            assert button.is_enabled()
            assert page.get_by_text('Default identity',exact=True).count()>=1
            assert form.locator('[data-mcel-reference-label]').inner_text()=='Default identity'
            page.locator('[data-select-key="dev-second"]').click()
            page.wait_for_function("() => window.__mcelSlh.app.readState('state:selected-local-identity')==='dev-second'")
            assert form.locator('[data-mcel-reference-label]').inner_text()=='Second identity'
            form.locator('[name="bindAddress"]').fill('192.168.1.24')
            form.locator('[name="port"]').fill('8999')
            assert form.locator('[name="bindAddress"]').input_value()=='192.168.1.24'
            assert form.locator('[name="port"]').input_value()=='8999'
            assert all(call['method']=='GET' for call in page.evaluate('window.slhCalls'))
            assert page.locator('button[data-intent="intent:login-administrator"]').count()==0
            assert page.evaluate("""async() => {
              try {await window.__mcelSlh.app.execute('intent:login-administrator');return false}
              catch(e) {return String(e.message).includes('unknown-intent')}
            }""")
            assert page.locator('button[data-intent="intent:start-local"]').count()==0
            assert page.locator('[data-mcel-node="surface-node:slh.worker-bank"]').count()==0
            assert errors==[], errors
        finally:
            browser.close()


def test_remote_target_keeps_selected_identity_and_connection_task_without_local_draft():
    with sync_playwright() as playwright:
        browser=_browser(playwright)
        try:
            page=browser.new_page(viewport={'width':1200,'height':760})
            page.evaluate(MOCK)
            _open(page,'overview')
            page.locator('[data-select-key="dev-second"]').click()
            page.evaluate('window.dispatchEvent(new Event("pagehide"))')
            page.evaluate('window.slhCalls=[]')
            _open(page,'network')
            assert page.evaluate('window.__mcelSlh.app.readState("state:selected-local-identity")')=='dev-second'
            assert page.locator('form[data-intent="intent:configure-and-enable-organization"]:visible').count()==0
            assert page.locator('form[data-intent="intent:connect-network-slh"]:visible').count()==1
            assert page.locator('input[name="tlsSha256"]').count()==0
            assert page.locator('[data-mcel-selected-details]:visible').count()==0
            assert page.locator('[data-select-key="dev-second"]:visible').count()==1
            assert all(call['url']=='/api/applications/slh/identities' for call in page.evaluate('window.slhCalls'))
        finally:
            browser.close()
