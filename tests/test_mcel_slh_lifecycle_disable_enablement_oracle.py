"""Configuration task lifecycle oracle: no Service Lifecycle panel or form reset.

Stop is a temporary pause of the running child, whereas Disable permanently
turns off the ORG-SLH auto-start policy. These are local signed actions only.
"""
from __future__ import annotations

import shutil
import pytest
from playwright.sync_api import sync_playwright
from main_computer.mcel_slh_application import compile_slh_application, render_slh_page


MOCK = r'''() => {
  window.lifecycle='not_configured';window.slhCalls=[];
  window.fetch=async(url,options={})=>{
    const method=options.method||'GET',body=options.body?JSON.parse(options.body):null;
    window.slhCalls.push({url,method,body});
    if(url==='/api/applications/slh/identities')return {ok:true,json:async()=>({
      schema:'mcel.slh.identities.v4b',state:'AVAILABLE',defaultDeviceId:'device-a',
      identities:[{deviceId:'device-a',label:'Default identity',fingerprint:'a'.repeat(64),defaultStatus:'DEFAULT'}],
      explanation:'available'})};
    if(url==='/api/applications/slh/observe?view=overview')return {ok:true,json:async()=>({
      ok:true,schema:'mcel.slh.first-slice-observation.v1',view:'overview',
      overview:{organizationState:window.lifecycle,organizationEndpoint:'127.0.0.1:8899'}})};
    if(url==='/api/applications/slh/setup/action'){
      if(body.action==='toggle-enablement')window.lifecycle=window.lifecycle==='disabled'?'running':'disabled';
      else if(body.action==='toggle')window.lifecycle=window.lifecycle==='running'?'stopped':'running';
      return {ok:true,json:async()=>({outcome:body.action==='toggle-enablement'?(window.lifecycle==='disabled'?'DISABLED':'ENABLED'):window.lifecycle==='stopped'?'STOPPED':'RUNNING',
        message:'Operation confirmed',deviceId:body.arguments.adminDeviceId})};
    }
    throw Error('UNEXPECTED_API:'+url);
  };
}'''


def test_lifecycle_controls_are_part_of_configuration_task_and_semantically_gated():
    ir, composition=compile_slh_application()
    nodes={n['id']:n for n in ir['surfaces'][0]['nodes']}
    form=nodes['surface-node:slh.configure-org-slh']
    assert form['resetControl'] is False
    disable=nodes['surface-node:slh.organization-enablement']
    toggle=nodes['surface-node:slh.start-stop-org-slh']
    assert disable['labelByState']['labels']=={'running':'Disable','stopped':'Disable','disabled':'Enable'}
    assert toggle['labelByState']['labels']=={'running':'Stop','stopped':'Start'}
    assert {m['ref'] for r in composition['regions'] if r['recipe']=='task' for m in r['members']} >= {
        form['id'],disable['id'],toggle['id']}
    assert not any(r['id'].endswith('service-lifecycle') for r in composition['regions'])


@pytest.mark.parametrize('width',[390,1040,1440])
def test_configure_footer_actions_follow_authoritative_lifecycle(width):
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        try:
            page=browser.new_page(viewport={'width':width,'height':800})
            page.on('dialog',lambda dialog:dialog.accept())
            page.evaluate(MOCK)
            page.set_content(render_slh_page())
            page.wait_for_function("() => window.__mcelSlh?.app.readState('state:organization-lifecycle')?.state==='not_configured'")
            form=page.locator('form[data-intent="intent:configure-and-enable-organization"]')
            assert form.count()==1
            assert form.locator('button[type="reset"]').count()==0
            disable=form.locator('button[data-intent="intent:toggle-organization-enablement"]')
            toggle=form.locator('button[data-intent="intent:toggle-organization"]')
            assert disable.count()==toggle.count()==1
            assert disable.is_disabled() and toggle.is_disabled()
            assert all(c['method']=='GET' for c in page.evaluate('window.slhCalls'))
            for state, can_disable, can_toggle, label, enable_label in [
                ('running',True,True,'Stop','Disable'),('stopped',True,True,'Start','Disable'),
                ('disabled',True,False,'Start','Enable'),('starting',False,False,'Start','Disable'),
                ('failed',False,False,'Start','Disable'),('not_configured',False,False,'Start','Disable')]:
                page.evaluate('(s)=>window.lifecycle=s',state)
                page.evaluate("() => window.__mcelSlh.app.execute('intent:refresh-lifecycle')")
                page.wait_for_function('(s)=>window.__mcelSlh.app.readState("state:organization-lifecycle")?.state===s',arg=state)
                assert disable.is_enabled() is can_disable,(state,'disable')
                assert toggle.is_enabled() is can_toggle,(state,'toggle')
                assert toggle.inner_text()==label
                assert disable.inner_text()==enable_label
                if not can_disable:
                    before=page.evaluate("() => window.slhCalls.filter(c=>c.method==='POST').length")
                    refused=page.evaluate("async()=>window.__mcelSlh.app.execute('intent:toggle-organization-enablement')")
                    assert refused['disposition']=='refused-before-attempt',refused
                    assert page.evaluate("() => window.slhCalls.filter(c=>c.method==='POST').length")==before
            page.evaluate("() => {window.lifecycle='running'}")
            page.evaluate("() => window.__mcelSlh.app.execute('intent:refresh-lifecycle')")
            page.wait_for_function("() => window.__mcelSlh.app.readState('state:organization-lifecycle')?.state==='running'")
            toggle.click()
            page.wait_for_function("() => window.__mcelSlh.app.readState('state:organization-lifecycle')?.state==='stopped'")
            assert toggle.inner_text()=='Start'
            assert disable.is_enabled() and disable.inner_text()=='Disable'
            toggle.click()
            page.wait_for_function("() => window.__mcelSlh.app.readState('state:organization-lifecycle')?.state==='running'")
            disable.click()
            page.wait_for_function("() => window.__mcelSlh.app.readState('state:organization-lifecycle')?.state==='disabled'")
            assert disable.inner_text()=='Enable' and toggle.is_disabled()
            disable.click()
            page.wait_for_function("() => window.__mcelSlh.app.readState('state:organization-lifecycle')?.state==='running'")
            assert disable.inner_text()=='Disable' and toggle.inner_text()=='Stop'
            assert [c['body']['action'] for c in page.evaluate('window.slhCalls') if c['body']] == ['toggle','toggle','toggle-enablement','toggle-enablement']
        finally:
            browser.close()


@pytest.mark.parametrize('view',['network','remote-seh'])
def test_no_local_management_controls_on_remote_targets(view):
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        try:
            page=browser.new_page(viewport={'width':1040,'height':800})
            page.evaluate(MOCK)
            page.set_content(render_slh_page(view))
            page.wait_for_function('() => !!window.__mcelSlh?.app')
            for intent in ('toggle-organization-enablement','toggle-organization'):
                button=page.locator(f'button[data-intent="intent:{intent}"]')
                assert button.count()==1 and not button.is_visible()
                refused=page.evaluate('(id)=>window.__mcelSlh.app.execute("intent:"+id)',intent)
                assert refused['disposition']=='refused-before-attempt'
            assert not any(c['method']=='POST' for c in page.evaluate('window.slhCalls'))
        finally:
            browser.close()
