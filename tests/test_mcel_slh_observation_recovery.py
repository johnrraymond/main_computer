"""Isolated SLH startup/return recovery; no live service requests or mutations."""
from __future__ import annotations

import shutil
from playwright.sync_api import sync_playwright
from main_computer.mcel_slh_application import render_slh_page


MOCK = r'''() => {
  const stored={};
  window.slhStore=stored;
  Object.defineProperty(window,'sessionStorage',{configurable:true,value:{
    getItem:k=>stored[k]??null,setItem:(k,v)=>{stored[k]=String(v)},removeItem:k=>{delete stored[k]}
  }});
  window.directoryMode='AVAILABLE';
  window.serviceMode='UNAVAILABLE';
  window.bindHost='192.168.1.42';window.bindPort=9021;
  window.readLog=[];window.mutationLog=[];
  window.fetch=async(url,options={})=>{
    const method=options.method||'GET';
    window.readLog.push({url:String(url),method});
    const response=(payload,ok=true)=>({ok,json:async()=>payload});
    if(url==='/api/applications/slh/identities')return response({
      schema:'mcel.slh.identities.v4b',state:window.directoryMode,
      identities:window.directoryMode==='AVAILABLE'?[{
        deviceId:'local-default',label:'Default',fingerprint:'a'.repeat(64),defaultStatus:'DEFAULT'
      }]:[],defaultDeviceId:window.directoryMode==='AVAILABLE'?'local-default':'',
      explanation:'Bounded read-only mocked directory'
    });
    if(url==='/api/applications/slh/observe?view=overview')return response({
      ok:true,schema:'mcel.slh.first-slice-observation.v1',view:'overview',
      overview:window.serviceMode==='AVAILABLE'?{
        serviceState:'RUNNING',observationState:'OBSERVED',organizationState:'running',
        organizationEndpoint:window.bindHost+':'+window.bindPort
      }:{serviceState:'UNREACHABLE',observationState:'UNAVAILABLE',
         organizationState:'NOT_OBSERVED',organizationEndpoint:'NOT_OBSERVED'}
    });
    if(url==='/api/applications/slh/setup/action'){
      const body=JSON.parse(options.body||'{}');window.mutationLog.push(body);
      if(body.action==='configure-and-enable'){
        window.serviceMode='AVAILABLE';window.bindHost=body.arguments.bindAddress;
        window.bindPort=body.arguments.port;
        return response({outcome:'RUNNING',message:'Configured',deviceId:body.arguments.adminDeviceId});
      }
      return response({error:'NOT_AUTHORIZED'},false);
    }
    if(url==='/api/applications/slh/seh/deployment')return response({error:{code:'UNAVAILABLE'}},false);
    return response({error:'UNEXPECTED'},false);
  };
}'''


def _browser():
    return sync_playwright()


def _mount(page):
    page.set_content(render_slh_page())
    page.wait_for_function('''()=>window.__mcelSlh?.diagnostics()?.startup?.lifecycleRefresh !== 'pending' &&
      window.__mcelSlh?.diagnostics()?.startup?.lifecycleRefresh !== 'not-attempted' ''')
    return page.locator('form[data-intent="intent:configure-and-enable-organization"]')


def test_unobserved_config_is_blank_blocked_and_reported():
    with _browser() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        try:
            page=browser.new_page();page.evaluate(MOCK)
            form=_mount(page)
            assert form.locator('[name="bindAddress"]').input_value()==''
            assert form.locator('[name="port"]').input_value()==''
            record=page.evaluate('window.__mcelSlh.diagnostics()')
            assert record['lifecycle']['configurationVerified'] is False
            assert record['lifecycle']['rawObservation']['classification']=='endpoint-unverified'
            assert record['lifecycle']['rawObservation']['serviceState']=='UNREACHABLE'
            assert record['lifecycle']['observedBindAddress'] is None
            assert any(x['code']=='slh-lifecycle-config-unverified' for x in record['findings'])
            form.locator('button[type="submit"]').click()
            assert page.evaluate('window.mutationLog')==[]
            assert 'configuration unverified' in page.locator('#mcel-workbench-status').inner_text()
        finally:
            browser.close()


def test_transient_failure_recovers_without_manual_refresh_or_synthetic_bind():
    with _browser() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        try:
            page=browser.new_page();page.evaluate(MOCK)
            # The first overview is unobserved, subsequent one becomes reachable.
            page.evaluate('''()=>{
              const previous=window.fetch;let count=0;
              window.fetch=async(u,o)=>{
                if(u==='/api/applications/slh/observe?view=overview' && ++count===2)
                  window.serviceMode='AVAILABLE';
                return previous(u,o);
              };
            }''')
            form=_mount(page)
            page.wait_for_function("window.__mcelSlh.diagnostics().lifecycle.configurationVerified === true",timeout=15000)
            assert form.locator('[name="bindAddress"]').input_value()=='192.168.1.42'
            assert form.locator('[name="port"]').input_value()=='9021'
            diag=page.evaluate('window.__mcelSlh.diagnostics()')
            assert diag['startup']['lifecycleAttempts']>=2
            assert diag['lifecycle']['rawObservation']['classification']=='observed'
            assert diag['lifecycle']['renderedDefaultWhileUnobserved'] is False
            assert page.evaluate('window.mutationLog')==[]
        finally:
            browser.close()


def test_unavailable_identity_page_exit_does_not_deselect_and_legacy_marker_heals():
    with _browser() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        try:
            page=browser.new_page();page.evaluate(MOCK)
            page.evaluate("window.directoryMode='UNAVAILABLE';window.slhStore['mcel.slh.selected-identity.v2.local']='__NONE__'")
            _mount(page)
            assert page.evaluate("window.__mcelSlh.app.readState('state:selected-local-identity')")==''
            page.evaluate("window.dispatchEvent(new Event('pagehide'))")
            assert page.evaluate("window.slhStore['mcel.slh.selected-identity.v2.local']")=='__NONE__'
            page.evaluate("window.directoryMode='AVAILABLE';window.serviceMode='AVAILABLE'")
            _mount(page)
            page.wait_for_function("window.__mcelSlh.app.readState('state:selected-local-identity')==='local-default'")
            assert page.evaluate("window.slhStore['mcel.slh.selected-identity.v2.local']")=='local-default'
            assert page.evaluate("window.__mcelSlh.diagnostics().startup.restoration")=='legacy-none-migrated'
        finally:
            browser.close()


def test_only_operator_deselect_persists_none_and_manual_config_is_possible():
    with _browser() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        try:
            page=browser.new_page();page.on("dialog",lambda dialog:dialog.accept());page.evaluate(MOCK)
            form=_mount(page)
            page.wait_for_function("window.__mcelSlh.app.readState('state:selected-local-identity')==='local-default'")
            page.get_by_role('button',name='Deselect identity').click()
            page.wait_for_function("window.__mcelSlh.app.readState('state:selected-local-identity')==='' ")
            assert page.evaluate("window.slhStore['mcel.slh.selected-identity.v2.local']")=='__EXPLICIT_NONE_V3__'
            page.evaluate("window.directoryMode='AVAILABLE';window.serviceMode='UNAVAILABLE'")
            _mount(page)
            assert page.evaluate("window.__mcelSlh.diagnostics().startup.restoration")=='explicit-none-marker'
            assert page.evaluate("window.__mcelSlh.app.readState('state:selected-local-identity')")==''
            page.locator('[data-select-key="local-default"]').click()
            page.wait_for_function("window.__mcelSlh.app.readState('state:selected-local-identity')==='local-default'")
            form=page.locator('form[data-intent="intent:configure-and-enable-organization"]')
            form.locator('[name="bindAddress"]').fill('0.0.0.0')
            form.locator('[name="port"]').fill('9018')
            form.locator('button[type="submit"]').click()
            page.wait_for_function("window.mutationLog.some(x=>x.action==='configure-and-enable')")
            result=page.evaluate("window.mutationLog.find(x=>x.action==='configure-and-enable')")
            assert result['arguments']['bindAddress']=='0.0.0.0'
            assert result['arguments']['port']==9018
        finally:
            browser.close()


def test_verified_wildcard_bind_is_preserved_as_actual_configuration():
    with _browser() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        try:
            page=browser.new_page();page.evaluate(MOCK)
            page.evaluate("window.serviceMode='AVAILABLE';window.bindHost='0.0.0.0';window.bindPort=8899")
            form=_mount(page)
            page.wait_for_function("window.__mcelSlh.diagnostics().lifecycle.configurationVerified===true")
            assert form.locator('[name="bindAddress"]').input_value()=='0.0.0.0'
            assert form.locator('[name="port"]').input_value()=='8899'
            diag=page.evaluate('window.__mcelSlh.diagnostics()')
            assert diag['lifecycle']['rawObservation']['endpoint']=='0.0.0.0:8899'
            assert not any(x['code']=='slh-wildcard-bind-not-observed' for x in diag['findings'])
        finally:
            browser.close()


def test_return_refresh_preserves_an_operator_edited_bind_draft():
    with _browser() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        try:
            page=browser.new_page();page.evaluate(MOCK)
            page.evaluate("window.serviceMode='AVAILABLE'")
            form=_mount(page)
            page.wait_for_function("window.__mcelSlh.diagnostics().lifecycle.configurationVerified===true")
            form.locator('[name="bindAddress"]').fill('0.0.0.0')
            page.evaluate("window.bindHost='192.168.1.50';window.dispatchEvent(new Event('focus'))")
            page.wait_for_function("window.__mcelSlh.app.readState('state:organization-lifecycle')?.bindAddress==='192.168.1.50'")
            assert form.locator('[name="bindAddress"]').input_value()=='0.0.0.0'
            assert page.evaluate("window.__mcelSlh.diagnostics().lifecycle.observedBindAddress")=='192.168.1.50'
            assert page.evaluate('window.mutationLog')==[]
        finally:
            browser.close()
