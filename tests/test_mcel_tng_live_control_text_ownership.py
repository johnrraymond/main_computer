"""TNG Chrome must not steal text nodes owned by incremental action-label updates.

SLH action labels are changed by operational-runtime.js via a direct Text child.
The cinematic wrapper once moved that child into a span; every refresh then
appended and wrapped a new Text child (DisableDisableDisable...). Run against
the actual compiled SLH page, not an isolated mocked DOM or static heading.
"""
from __future__ import annotations

import shutil

from playwright.sync_api import sync_playwright
from main_computer.mcel_slh_application import render_slh_page


MOCK = r'''() => {
  window.lifecycle='running';
  window.fetch=async(url,options={})=>{
    const body=options.body?JSON.parse(options.body):null;
    if(url==='/api/applications/slh/identities')return {ok:true,json:async()=>({
      schema:'mcel.slh.identities.v4b',state:'AVAILABLE',defaultDeviceId:'device-a',
      identities:[{deviceId:'device-a',label:'Default identity',fingerprint:'a'.repeat(64),defaultStatus:'DEFAULT'}],
      explanation:'available'
    })};
    if(url==='/api/applications/slh/observe?view=overview')return {ok:true,json:async()=>({
      ok:true,schema:'mcel.slh.first-slice-observation.v1',view:'overview',
      overview:{organizationState:window.lifecycle,organizationEndpoint:'127.0.0.1:8899'}
    })};
    if(url==='/api/applications/slh/seh/deployment')return {ok:true,json:async()=>({
      ok:true,result:body?.operation==='operation-state'?
      {schema:'main-computer.seh-management-operation-snapshot.v1',state:'IDLE',busy:false,
        operation:null,last_operation:null}:
      {generation:33,selectedController:'coolify-a',applicationUuid:'',
        deploymentStatus:'UNDEPLOYED',controllers:[]}
    })};
    throw Error('UNEXPECTED_API:'+url);
  };
}'''


def test_live_slh_dynamic_action_labels_never_accumulate_under_tng():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        try:
            page=browser.new_page(viewport={'width':1350,'height':850})
            errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.evaluate(MOCK)
            page.set_content(render_slh_page())
            page.wait_for_function("() => window.__mcelSlh?.app.readState('state:organization-lifecycle')?.state==='running'")
            selectors={
                'enable':'button[data-intent="intent:toggle-organization-enablement"]',
                'start':'button[data-intent="intent:toggle-organization"]',
                'apply':'form[data-intent="intent:configure-and-enable-organization"] button[type="submit"]',
            }
            def verify(state):
                expected={'enable':'Disable' if state!='disabled' else 'Enable',
                          'start':'Stop' if state=='running' else 'Start','apply':'Apply'}
                for name,sel in selectors.items():
                    button=page.locator(sel)
                    assert button.count()==1,sel
                    assert button.text_content()==expected[name],(name,state,button.text_content())
                    assert button.locator('[data-mcel-tng-gold-text]').count()==0,(name,state)
                    assert button.evaluate('el=>[...el.childNodes].filter(n=>n.nodeType===3).length')==1,(name,state)
                    assert button.evaluate('el=>getComputedStyle(el).textShadow')=='none',(name,state)
            verify('running')
            # Every explicit refresh fires the real operational runtime update.
            # This used to add one new gold span for each changed or ticked state.
            for state in ('stopped','running','stopped','running','stopped','running'):
                page.evaluate('s=>window.lifecycle=s',state)
                receipt=page.evaluate("async()=>await window.__mcelSlh.app.execute('intent:refresh-lifecycle')")
                assert receipt['disposition']=='completed',receipt
                page.wait_for_function('(s)=>window.__mcelSlh.app.readState("state:organization-lifecycle")?.state===s',arg=state)
                page.wait_for_timeout(35)
                verify(state)
            assert not errors,errors
        finally:
            browser.close()
