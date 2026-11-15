"""The SLH E/W/G copies the same MCEL evidence as Calculator, with an
independent, bounded record of startup identity and lifecycle observations.

No live services or changes to service configuration are exercised.
"""
from __future__ import annotations

import shutil

from playwright.sync_api import sync_playwright

from main_computer.mcel_slh_application import render_slh_page


MOCK = r'''() => {
  const values={};
  window.__diagnosticSessionStorage=values;
  Object.defineProperty(window,'sessionStorage',{configurable:true,value:{
    getItem:k=>values[k]??null,setItem:(k,v)=>{values[k]=String(v)},
    removeItem:k=>{delete values[k]}
  }});
  window.identityState='AVAILABLE';
  window.lifecycleState='AVAILABLE';
  window.reads=[];
  window.copies=[];
  Object.defineProperty(navigator,'clipboard',{configurable:true,value:{
    writeText:async s=>{window.copies.push(JSON.parse(s));}
  }});
  window.fetch=async(url,options={})=>{
    window.reads.push({url,method:options.method||'GET'});
    const reply=(data,ok=true)=>({ok,json:async()=>data});
    if(url==='/api/applications/slh/identities')return reply({
      schema:'mcel.slh.identities.v4b',state:window.identityState,
      defaultDeviceId:window.identityState==='AVAILABLE'?'one':'',
      identities:window.identityState==='AVAILABLE'?[{
        deviceId:'one',label:'One',fingerprint:'a'.repeat(64),defaultStatus:'DEFAULT'}]:[],
      explanation:'Read-only test identity directory'});
    if(url==='/api/applications/slh/observe?view=overview'){
      if(window.lifecycleState!=='AVAILABLE')return reply({ok:false},false);
      return reply({ok:true,schema:'mcel.slh.first-slice-observation.v1',view:'overview',
        overview:{organizationState:'running',organizationEndpoint:'127.0.0.1:8899'}});
    }
    if(url==='/api/applications/slh/seh/deployment')return reply({ok:false,error:{code:'UNAVAILABLE'}},false);
    return reply({ok:false},false);
  };
}'''


def _load(page):
    page.set_content(render_slh_page())
    page.wait_for_function('''()=>window.__mcelSlh?.diagnostics()?.startup?.lifecycleRefresh!=='pending' &&
      window.__mcelSlh?.diagnostics()?.startup?.lifecycleRefresh!=='not-attempted' ''', timeout=15000)
    widget=page.locator('[data-mcel-diagnostic-title-control]')
    assert widget.count()==1
    assert widget.get_attribute('data-mcel-diagnostics-counter')=='slh'
    assert widget.evaluate('el=>el.previousElementSibling?.hasAttribute("data-mcel-application-title")')
    return widget


def test_ewg_captures_interrupted_identity_observation_without_blocking_lifecycle():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        try:
            page=browser.new_page(viewport={'width':1200,'height':800})
            errors=[]
            page.on('pageerror',lambda error: errors.append(str(error)))
            page.evaluate(MOCK)
            page.evaluate("window.identityState='UNAVAILABLE'")
            widget=_load(page)
            record=page.evaluate('window.__mcelSlh.diagnostics()')
            assert record['identity']['directoryVerified'] is False
            assert record['identity']['count'] is None
            assert record['identity']['selected'] is False
            assert record['startup']['identityRefresh']=='failed'
            assert record['startup']['lifecycleRefresh']=='completed'
            assert record['lifecycle']['observedBindAddress']=='127.0.0.1'
            assert record['lifecycle']['renderedBindAddress']=='127.0.0.1'
            assert any(f['code']=='slh-startup-identity-observation-unavailable' for f in record['findings'])
            widget.click()
            page.wait_for_function('window.copies.length===1')
            copied=page.evaluate('window.copies[0]')
            assert copied['schema']=='mcel-diagnostics-counter-copy-v4'
            assert copied['appId']=='slh'
            assert copied['diagnosticEvidence']['operational']['identity']['directoryVerified'] is False
            assert copied['diagnosticEvidence']['operational']['lifecycle']['observedBindAddress']=='127.0.0.1'
            assert any(x['stage']=='observation-settled' and x['intent']=='intent:refresh-identities'
                       and x['outcome']=='failed' for x in copied['diagnosticEvidence']['operational']['timeline'])
            assert not any(item['url'].endswith('/setup/action') for item in page.evaluate('window.reads'))
            assert not errors, errors
        finally:
            browser.close()


def test_ewg_retains_previous_failure_and_does_not_invent_deselection_marker():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        try:
            page=browser.new_page(viewport={'width':1200,'height':800})
            errors=[]
            page.on('pageerror',lambda error: errors.append(str(error)))
            page.evaluate(MOCK)
            page.evaluate("window.identityState='UNAVAILABLE';window.lifecycleState='UNAVAILABLE'")
            _load(page)
            first=page.evaluate('window.__mcelSlh.diagnostics()')
            assert first['lifecycle']['renderedBindAddress'] is None
            assert first['lifecycle']['renderedDefaultWhileUnobserved'] is False
            assert first['startup']['lifecycleRefresh']=='failed'
            assert {f['code'] for f in first['findings']} >= {
                'slh-startup-identity-observation-unavailable',
                'slh-startup-lifecycle-observation-unavailable'}
            page.evaluate("window.dispatchEvent(new Event('pagehide'))")
            assert page.evaluate("window.__diagnosticSessionStorage['mcel.slh.selected-identity.v2.local'] || null") is None
            page.evaluate("window.identityState='AVAILABLE';window.lifecycleState='AVAILABLE'")
            widget=_load(page)
            second=page.evaluate('window.__mcelSlh.diagnostics()')
            assert second['identity']['directoryVerified'] is True
            assert second['identity']['remembered']=='saved-identity'
            assert second['identity']['selected'] is True
            assert second['startup']['restoration']=='selection-requested'
            assert any(x['stage']=='pagehide-selection-persistence' and x['outcome']=='preserved-existing'
                       and x['directoryVerified'] is False for x in second['timeline'])
            assert any(x['stage']=='lifecycle-hydration' and x['outcome']=='failed' for x in second['timeline'])
            widget.click()
            page.wait_for_function('window.copies.length===1')
            copied=page.evaluate('window.copies[0]')
            assert copied['diagnosticEvidence']['operational']['identity']['remembered']=='saved-identity'
            assert any(x['stage']=='pagehide-selection-persistence' for x in copied['diagnosticEvidence']['operational']['timeline'])
            assert not errors,errors
        finally:
            browser.close()
