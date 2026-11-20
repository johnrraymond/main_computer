"""End-to-end notebook recovery: one expired local ORG session during Reject."""
from pathlib import Path
import shutil
from playwright.sync_api import sync_playwright
from main_computer.mcel_slh_application import render_slh_page


def test_visible_reject_recovers_expired_local_organization_session():
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None,
                                           args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':1280,'height':900})
            errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.evaluate('''() => {
              window.notebookCalls=[];window.loginCount=0;window.rejectedCount=0;window.pendingRow=true;
              window.fetch=async(url,opts={})=>{
                const body=opts.body?JSON.parse(opts.body):{};
                const status=(data,code=200)=>({ok:code<400,status:code,json:async()=>data});
                if(url==='/api/applications/slh/identities')return status({
                  schema:'mcel.slh.identities.v4b',state:'AVAILABLE',defaultDeviceId:'owner',
                  identities:[{deviceId:'owner',label:'Owner',fingerprint:'a'.repeat(64),defaultStatus:'DEFAULT'}],explanation:''});
                if(url==='/api/applications/slh/observe?view=overview')return status({ok:true,
                  schema:'mcel.slh.first-slice-observation.v1',view:'overview',
                  overview:{organizationState:'running',organizationEndpoint:'127.0.0.1:8899'}});
                if(url==='/api/applications/slh/setup/action'){
                  if(body.action==='logout')return status({outcome:'SIGNED_OUT'});
                  if(body.action==='authenticate')return status({
                    access_token:String(++window.loginCount).repeat(40),device_id:body.arguments.adminDeviceId,
                    expires_at:Math.floor(Date.now()/1000)+900});
                }
                if(url==='/api/applications/slh/identity-notebook'){
                  window.notebookCalls.push({operation:body.operation,token:opts.headers.Authorization});
                  if(body.operation==='read-pending')return status({ok:true,result:window.pendingRow?[{
                    id:'a'.repeat(32),identity:'Identity Test 001',role:'worker',requestedAt:'Nov 17, 2026'}]:[]});
                  if(body.operation==='read-enrolled'||body.operation==='read-trash')return status({ok:true,result:[]});
                  if(body.operation==='read-retention')return status({ok:true,result:{enabled:true}});
                  if(body.operation==='reject'){
                    if(++window.rejectedCount===1)return status({error:{code:'SESSION_INVALID'}},401);
                    window.pendingRow=false;
                    return status({ok:true,result:{outcome:'DENIED',message:'DENIED'}});
                  }
                }
                throw Error('unexpected fetch '+url);
              };
            }''')
            page.set_content(render_slh_page('overview'))
            page.wait_for_function("() => window.__mcelSlh?.app.readState('state:selected-local-identity')==='owner'")
            button=page.locator('[data-record-key="'+'a'*32+'"] [data-mcel-notebook-action="Reject"]')
            button.wait_for(timeout=4500)
            button.click(timeout=4500)
            page.wait_for_function("() => window.rejectedCount===2",timeout=4500)
            page.wait_for_function("() => !document.querySelector('[data-mcel-notebook-scroll] [data-record-key]')",timeout=4500)
            assert page.locator('[data-mcel-notebook-status]').inner_text().find('SESSION_INVALID')==-1
            assert page.evaluate('window.loginCount')==2
            assert not errors,errors
        finally:
            browser.close()
