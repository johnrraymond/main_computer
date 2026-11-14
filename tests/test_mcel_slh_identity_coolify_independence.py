"""Identity and local Coolify observation remain independent at first mount."""
from __future__ import annotations

import shutil
from playwright.sync_api import sync_playwright

from main_computer.mcel_slh_application import render_slh_page


MOCK = r'''({identityState="AVAILABLE"}={}) => {
  window.mountCalls=[];
  window.fetch=async(url,opts={})=>{
    const body=opts.body?JSON.parse(opts.body):{};
    window.mountCalls.push({url,operation:body.operation||body.action||""});
    const reply=(result,ok=true)=>({ok,json:async()=>result});
    if(url==="/api/applications/slh/identities"){
      // The real LMS uses one loopback listener. The original regression
      // let Coolify inspect contend with the identity directory read.
      await new Promise(resolve=>setTimeout(resolve,30));
      window.mountCalls.push({url:"identity-read-completed",operation:""});
      if(identityState!=="AVAILABLE")return reply({
        schema:"mcel.slh.identities.v4b",state:identityState,
        identities:[],defaultDeviceId:"",explanation:"LMS identity directory cannot be verified"
      });
      return reply({schema:"mcel.slh.identities.v4b",state:"AVAILABLE",
        identities:[
          {deviceId:"one",label:"One",fingerprint:"a".repeat(64),defaultStatus:"DEFAULT"},
          {deviceId:"two",label:"Two",fingerprint:"b".repeat(64),defaultStatus:"ADDITIONAL"}],
        defaultDeviceId:"one",explanation:"Local identities verified"});
    }
    if(url==="/api/applications/slh/observe?view=overview")return reply({
      schema:"mcel.slh.first-slice-observation.v1",view:"overview",
      overview:{organizationState:"running",organizationEndpoint:"127.0.0.1:8899"}
    });
    if(url==="/api/applications/slh/seh/deployment" && body.operation==="operation-state")return reply({
      ok:true,result:{schema:"main-computer.seh-management-operation-snapshot.v1",
        state:"IDLE",busy:false,operation:null}
    });
    if(url==="/api/applications/slh/seh/deployment" && body.operation==="inspect")return reply({
      ok:true,result:{generation:33,selectedController:"coolify-c",applicationUuid:"",
        deploymentStatus:"UNDEPLOYED",controllers:[{identity:"coolify-c"}]}
    });
    if(url==="/api/applications/slh/setup/action" && body.action==="authenticate")return reply({
      outcome:"AUTHENTICATED",access_token:"x".repeat(40),device_id:body.arguments.adminDeviceId
    });
    if(url==="/api/applications/slh/identity-notebook"){
      if(body.operation==="read-enrolled")return reply({ok:true,result:[
        {id:"one",identity:"One",role:"admin",createdAt:"today"},
        {id:"two",identity:"Two",role:"worker",createdAt:"today"}]});
      if(body.operation==="read-retention")return reply({ok:true,result:{enabled:false}});
      return reply({ok:true,result:[]});
    }
    return reply({ok:false,error:{code:"UNAVAILABLE"}},false);
  };
}'''


def _page(browser, *, identity_state="AVAILABLE"):
    page=browser.new_page(viewport={"width":1250,"height":800})
    page.evaluate(MOCK, {"identityState":identity_state})
    page.set_content(render_slh_page())
    page.wait_for_function("() => !!window.__mcelSlh?.app.readState('state:seh-deployment-status')",timeout=10000)
    return page


def test_initial_identity_directory_completes_before_local_coolify_without_login():
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None,args=['--no-sandbox'])
        try:
            page=_page(browser)
            page.wait_for_function("()=>window.__mcelSlh.app.readState('state:local-identities')?.length===2")
            calls=page.evaluate("window.mountCalls")
            identities=[i for i,call in enumerate(calls) if call['url'].endswith('/identities')]
            deployments=[i for i,call in enumerate(calls) if call['operation']=='inspect']
            assert len(identities)==2 and len(deployments)==1
            completed=[i for i,call in enumerate(calls) if call['url']=='identity-read-completed']
            assert len(completed)==2 and max(completed)<min(deployments), calls
            assert page.locator('[data-select-key="one"]').count()==1
            assert page.locator('[data-select-key="two"]').count()==1
            assert page.evaluate('window.__mcelSlh.app.readState("state:selected-local-identity")')=='one'
            assert not any(call['operation']=='authenticate' for call in calls)
            assert page.locator('select').filter(has=page.locator('option')).count()>=1
            # An explicit protected read is still allowed to verify roles;
            # it must not be a prerequisite for either local LMS observation.
            result=page.evaluate("async()=>await window.__mcelSlh.app.execute('intent:read-organization-enrolled')")
            assert result['disposition']=='completed',result
            page.wait_for_function("()=>document.querySelector('[data-mcel-verified-role]')?.textContent==='Administrator'")
            page.locator('[data-select-key="two"]').click()
            page.wait_for_function("()=>window.__mcelSlh.app.readState('state:selected-local-identity')==='two'")
            assert page.locator('[data-select-key="one"]').count()==1
            assert page.evaluate('window.__mcelSlh.app.readState("state:seh-deployment-status").selectedController')=='coolify-c'
            page.get_by_role('button',name='Deselect identity').click()
            page.wait_for_function("()=>window.__mcelSlh.app.readState('state:selected-local-identity')==='' ")
            assert page.locator('[data-select-key="one"]').count()==1
            assert page.evaluate('window.__mcelSlh.app.readState("state:seh-deployment-status").selectedController')=='coolify-c'
        finally:
            browser.close()


def test_unavailable_identity_directory_is_not_reported_as_verified_empty_or_gates_coolify():
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None,args=['--no-sandbox'])
        try:
            page=_page(browser,identity_state='UNAVAILABLE')
            page.wait_for_function("()=>window.__mcelSlh.app.readState('state:local-identity-status')?.state==='UNAVAILABLE'")
            assert page.evaluate('window.__mcelSlh.app.readState("state:local-identities")') is None
            assert page.evaluate('window.__mcelSlh.app.readState("state:selected-local-identity")')==''
            assert 'Local identity directory unavailable' in page.locator('#mcel-workbench-status').inner_text()
            assert page.evaluate('window.__mcelSlh.app.readState("state:seh-deployment-status").selectedController')=='coolify-c'
            assert not any(call['operation']=='authenticate' for call in page.evaluate('window.mountCalls'))
        finally:
            browser.close()
