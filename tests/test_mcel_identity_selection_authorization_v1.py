"""No user-facing login: select/clear, lazy cryptographic proof and role provenance."""
from __future__ import annotations
import shutil
import pytest
from playwright.sync_api import sync_playwright
from main_computer.mcel_slh_application import compile_slh_application,render_slh_page

MOCK = r'''() => {
  window.calls=[];window.bearers=[];
  window.fetch=async(url,options={})=>{
    const method=options.method||'GET',body=options.body?JSON.parse(options.body):{};
    window.calls.push({url,method,body});
    const reply=(data,ok=true)=>({ok,json:async()=>data});
    if(url==='/api/applications/slh/identities')return reply({schema:'mcel.slh.identities.v4b',
       state:'AVAILABLE',defaultDeviceId:'one',identities:[
         {deviceId:'one',label:'One',fingerprint:'a'.repeat(64),defaultStatus:'DEFAULT'},
         {deviceId:'two',label:'Two',fingerprint:'b'.repeat(64),defaultStatus:'ADDITIONAL'}], explanation:''});
    if(url==='/api/applications/slh/observe?view=overview')return reply({schema:'mcel.slh.first-slice-observation.v1',
       view:'overview',overview:{organizationState:'running',organizationEndpoint:'127.0.0.1:8899'}});
    if(url==='/api/applications/slh/observe?view=workers')return reply({schema:'mcel.slh.first-slice-observation.v1',
       view:'workers',workerSummary:{observationState:'AUTHENTICATED'}});
    if(url==='/api/applications/slh/setup/action'){
       if(body.action==='authenticate')return reply({outcome:'AUTHENTICATED',access_token:'x'.repeat(40),
          deviceId:body.arguments.adminDeviceId,message:'Verified'});
       if(body.action==='logout')return reply({outcome:'SIGNED_OUT',deviceId:'',message:'Revoked'});
    }
    if(url==='/api/applications/slh/identity-notebook'){
       if(body.operation==='read-pending'||body.operation==='read-trash')return reply({ok:true,result:[]});
       if(body.operation==='read-enrolled')return reply({ok:true,result:[{id:'one',identity:'One',role:'admin',createdAt:'Nov 13, 2026'},
          {id:'two',identity:'Two',role:'worker',createdAt:'Nov 13, 2026'}]});
       if(body.operation==='read-retention')return reply({ok:true,result:{enabled:false}});
       return reply({ok:false,error:{code:'REQUEST_REFUSED'}},false);
    }
    if(url==='/api/applications/slh/seh/deployment' && body.operation==='inspect')return reply({ok:true,result:{
       generation:33,selectedController:'coolify-a',applicationUuid:'old-uuid',
       deploymentStatus:'UNDEPLOYED',controllers:[{identity:'coolify-a'}]}});
    return reply({ok:false,error:{code:'REFUSED'}},false);
  };
}'''


def test_semantic_identity_contract_has_clearable_selection_verified_role_and_no_login_intent():
    ir,_=compile_slh_application()
    nodes={n['id']:n for n in ir['surfaces'][0]['nodes']}
    identity=nodes['surface-node:slh.available-local-identities']
    assert identity['clearable'] is True
    assert identity['roleFrom']=={'state':{'ref':'state:enrolled-devices'},'key':'id','roleField':'role'}
    assert all(n.get('authentication') is None for n in nodes.values() if n['nodeKind']=='operation-notebook')
    assert not any(x['id'] in ('intent:login-administrator','intent:logout-administrator') for x in ir['intents'])


def test_select_deselect_role_provenance_and_lazy_authorization():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':1100,'height':800})
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.evaluate("() => Object.defineProperty(window,'sessionStorage',{configurable:true,value:{_store:{},getItem(k){return this._store[k]??null},setItem(k,v){this._store[k]=v}}})")
            page.evaluate(MOCK)
            page.set_content(render_slh_page())
            page.wait_for_function("() => window.__mcelSlh?.app.readState('state:selected-local-identity')==='one'")
            assert page.locator('[data-mcel-operation-auth]').count()==0
            assert page.get_by_role('button',name='Deselect identity').count()==1
            assert page.locator('[data-mcel-verified-role]').all_text_contents()==['Role not verified','Role not verified']
            assert not any(c['body'].get('action')=='authenticate' for c in page.evaluate('window.calls'))
            # Request a privileged read: transparent, bound authentication occurs only here.
            result=page.evaluate("async()=>await window.__mcelSlh.app.execute('intent:read-organization-enrolled')")
            assert result['disposition']=='completed',result
            page.wait_for_function("() => document.querySelector('[data-mcel-verified-role]')?.textContent==='Administrator'")
            assert page.locator('[data-mcel-verified-role]').all_text_contents()==['Administrator','Role not verified']
            assert len([c for c in page.evaluate('window.calls') if c['body'].get('action')=='authenticate'])==1
            # Identity switch clears the privileged projection and the bearer.
            page.evaluate("async()=>await window.__mcelSlh.app.execute('intent:choose-identity',{key:'two'})")
            assert page.locator('[data-mcel-verified-role]').all_text_contents()==['Role not verified','Role not verified']
            assert page.evaluate("window.__mcelSlh.app.readState('state:enrolled-devices')") is None
            page.get_by_role('button',name='Deselect identity').click()
            page.wait_for_function("() => window.__mcelSlh.app.readState('state:selected-local-identity')==='' ")
            assert page.get_by_text('No identity selected').count()>=1
            assert page.evaluate('sessionStorage.getItem("mcel.slh.selected-identity.v2.local")')=='__NONE__'
            count=len([c for c in page.evaluate('window.calls') if c['body'].get('action')=='authenticate'])
            denied=page.evaluate("async()=>await window.__mcelSlh.app.execute('intent:read-organization-enrolled')")
            assert denied['disposition']!='completed'
            assert len([c for c in page.evaluate('window.calls') if c['body'].get('action')=='authenticate'])==count
            page.set_content(render_slh_page())
            page.wait_for_function("() => !!window.__mcelSlh?.app.readState('state:local-identities')")
            assert page.evaluate("window.__mcelSlh.app.readState('state:selected-local-identity')")==''
            assert not errors,errors
        finally:browser.close()


def test_late_privileged_response_is_superseded_when_identity_changes():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None,args=['--no-sandbox'])
        try:
            page=browser.new_page()
            page.evaluate(MOCK)
            page.evaluate('''() => {
              const original=window.fetch;window.pauseRead=false;window.deferred=0;
              window.fetch=async(url,opts)=>{
                const body=opts?.body?JSON.parse(opts.body):{};
                if(window.pauseRead && url==='/api/applications/slh/identity-notebook' &&
                   body.operation==='read-enrolled')return new Promise(resolve=>{
                    window.deferred++;window.releasePrivileged=()=>resolve({ok:true,json:async()=>({ok:true,
                      result:[{id:'one',identity:'Old',role:'admin',createdAt:'Now'}]})});});
                return original(url,opts);
              };
            }''')
            page.set_content(render_slh_page())
            page.wait_for_function("() => window.__mcelSlh?.app.readState('state:selected-local-identity')==='one'")
            # Authenticate through an explicitly requested protected read.
            assert page.evaluate("async()=>await window.__mcelSlh.app.execute('intent:read-organization-enrolled')")['disposition']=='completed'
            page.evaluate('''() => { window.pauseRead=true;
               window.pendingProtectedRead=window.__mcelSlh.app.execute('intent:read-organization-enrolled'); }''')
            page.wait_for_function('() => window.deferred>=1',timeout=4500)
            page.get_by_role('button',name='Deselect identity').click()
            page.wait_for_function("() => window.__mcelSlh.app.readState('state:selected-local-identity')==='' ")
            page.evaluate('window.releasePrivileged()')
            result=page.evaluate('async()=>await window.pendingProtectedRead')
            assert result['disposition']=='superseded',result
            assert page.evaluate("window.__mcelSlh.app.readState('state:enrolled-devices')") is None
        finally:browser.close()
