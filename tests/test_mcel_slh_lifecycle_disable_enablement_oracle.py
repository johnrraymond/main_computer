"""MCEL conditional action enablement: keep the region and action visible.

The Service Lifecycle region must not pop in and out when a derived status
changes. Disabled actions cannot dispatch, even through programmatic execute().
"""
from __future__ import annotations

import shutil

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_slh_application import compile_slh_application, render_slh_page


MOCK = r'''() => {
  const storage={};
  Object.defineProperty(window,'sessionStorage',{configurable:true,value:{
    getItem:k=>storage[k]??null,setItem:(k,v)=>storage[k]=String(v),removeItem:k=>delete storage[k]
  }});
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
      if(body.action==='disable')window.lifecycle='disabled';
      return {ok:true,json:async()=>({outcome:'DISABLED',message:'Disabled',deviceId:body.arguments.adminDeviceId})};
    }
    throw Error('UNEXPECTED_API:'+url);
  };
}'''


def _status(page):
    return page.evaluate('''() => {
      const region=document.querySelector('[data-mcel-purpose="commands"]');
      const button=region?.querySelector('button[data-intent="intent:disable-organization"]');
      return {regionPresent:!!region,regionVisible:!!region?.getClientRects().length && !region.closest('[hidden]'),
        buttonPresent:!!button,buttonVisible:!!button?.getClientRects().length && !button.closest('[hidden]'),
        disabled:button?.disabled,ariaDisabled:button?.getAttribute('aria-disabled')};
    }''')


def test_disable_condition_is_semantic_enablement_not_visibility():
    ir,_=compile_slh_application()
    node=next(n for n in ir['surfaces'][0]['nodes'] if n['id']=='surface-node:slh.disable-org-slh')
    assert node['visibleWhen']=={'state':{'ref':'state:selected-target'},'equals':'local'}
    assert node['enabledWhen']=={'any':[
        {'state':{'ref':'state:organization-lifecycle'},'path':'state','equals':status}
        for status in ('running','starting','failed','stopped_unexpectedly')
    ]}


@pytest.mark.parametrize('width',[390,1040,1440])
def test_service_lifecycle_persists_and_disable_is_state_gated(width):
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        try:
            page=browser.new_page(viewport={'width':width,'height':800})
            page.on('dialog',lambda dialog:dialog.accept())
            page.evaluate(MOCK)
            page.set_content(render_slh_page())
            page.wait_for_function("() => window.__mcelSlh?.app.readState('state:organization-lifecycle')?.state==='not_configured'")
            initial=_status(page)
            assert initial=={'regionPresent':True,'regionVisible':True,'buttonPresent':True,
                'buttonVisible':True,'disabled':True,'ariaDisabled':'true'},initial
            assert all(c['method']=='GET' for c in page.evaluate('window.slhCalls'))
            for state,should_enable in [('running',True),('starting',True),('failed',True),
                                        ('stopped_unexpectedly',True),('disabled',False),
                                        ('not_configured',False),('unknown',False)]:
                page.evaluate('(s)=>{window.lifecycle=s}',state)
                page.evaluate("() => window.__mcelSlh.app.execute('intent:refresh-lifecycle')")
                page.wait_for_function('(s)=>window.__mcelSlh.app.readState("state:organization-lifecycle")?.state===s',arg=state)
                observed=_status(page)
                assert observed['regionVisible'] and observed['buttonVisible'],(state,observed)
                assert observed['disabled'] is (not should_enable),(state,observed)
                assert observed['ariaDisabled']==str(not should_enable).lower(),(state,observed)
                if not should_enable:
                    posted_before=page.evaluate("() => window.slhCalls.filter(c=>c.method==='POST').length")
                    # Both ordinary and synthetic clicks must not invoke the port.
                    page.locator('button[data-intent="intent:disable-organization"]').evaluate('(button)=>button.click()')
                    result=page.evaluate("async()=>await window.__mcelSlh.app.execute('intent:disable-organization')")
                    assert result['disposition']=='refused-before-attempt',result
                    assert page.evaluate("() => window.slhCalls.filter(c=>c.method==='POST').length")==posted_before
            # A legal enabled action still goes through the existing setup port.
            page.evaluate("() => {window.lifecycle='running'}")
            page.evaluate("() => window.__mcelSlh.app.execute('intent:refresh-lifecycle')")
            page.wait_for_function("() => window.__mcelSlh.app.readState('state:organization-lifecycle')?.state==='running'")
            page.locator('button[data-intent="intent:disable-organization"]').click()
            page.wait_for_function("() => window.slhCalls.some(c=>c.body?.action==='disable')")
            page.wait_for_function("() => window.__mcelSlh.app.readState('state:organization-lifecycle')?.state==='disabled'")
            assert _status(page)['disabled'] is True
            assert _status(page)['regionVisible'] is True
        finally:
            browser.close()


def test_service_lifecycle_stays_local_and_existing_empty_region_rule_remains():
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        try:
            page=browser.new_page(viewport={'width':1040,'height':800})
            page.evaluate(MOCK)
            page.set_content(render_slh_page('network'))
            page.wait_for_function("() => window.__mcelSlh?.app.readState('state:selected-target')==='network'")
            status=_status(page)
            assert status['regionPresent'] and status['buttonPresent'],status
            assert not status['regionVisible'] and not status['buttonVisible'],status
            assert all(call['method']=='GET' for call in page.evaluate('window.slhCalls'))
        finally:
            browser.close()
