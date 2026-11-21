"""Native identity-rail OTP acceptance: no fingerprint nor pairing UI."""
from pathlib import Path
import shutil
import pytest
from main_computer.mcel_slh_application import compile_slh_application, render_slh_page


def test_semantic_identity_disclosure_and_no_authenticator_notebook():
    ir,_ = compile_slh_application()
    nodes={node['id']:node for node in ir['surfaces'][0]['nodes']}
    identities=nodes['surface-node:slh.available-local-identities']
    assert identities['credentialDisclosure']=={'intent':{'ref':'intent:software-authenticator-reveal'},'identityField':'deviceId'}
    assert 'fingerprint' not in identities['fields'] and 'deviceId' not in identities['fields']
    assert 'surface-node:slh.software-authenticator-notebook' not in nodes
    assert not {'intent:software-authenticator-pair','intent:software-authenticator-revoke'} & {i['id'] for i in ir['intents']}
    assert 'code' not in {i['id'] for i in ir['states']}


@pytest.mark.parametrize('width,height',[(1120,730),(740,460)])
def test_current_identity_passkey_is_inline_counted_and_not_in_activity(width,height):
    pw=pytest.importorskip('playwright.sync_api')
    executable=shutil.which('chromium') or shutil.which('chromium-browser')
    source=render_slh_page()
    stub=r'''<script>
window.__otpWrites=[];window.__clipboard=[];
Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async value=>window.__clipboard.push(value)}});
window.fetch=async (url,opts={})=>{
 const accept=(result,status=200)=>({ok:status>=200&&status<300,status,json:async()=>result});
 if(url.startsWith('/api/applications/slh/identities'))return accept({schema:'mcel.slh.identities.v4b',state:'AVAILABLE',defaultDeviceId:'admin-laptop',explanation:'Verified identity',identities:[{deviceId:'admin-laptop',fingerprint:'a'.repeat(64),label:'Administrator laptop',defaultStatus:'DEFAULT'}]});
 if(url.startsWith('/api/applications/slh/observe'))return accept({ok:true,schema:'mcel.slh.first-slice-observation.v1',view:'overview',overview:{organizationState:'running',organizationEndpoint:'127.0.0.1:8899',serviceState:'RUNNING',observationState:'OBSERVED'}});
 if(url.endsWith('/setup/action'))return accept({access_token:'a'.repeat(32),device_id:'admin-laptop',expires_at:Math.floor(Date.now()/1000)+900});
 if(url.endsWith('/software-authenticator')){
   const request=JSON.parse(opts.body);window.__otpWrites.push(request);
   if(request.operation==='current')return accept({ok:true,result:{code:'12345678',expiresAt:Math.floor(Date.now()/1000)+25,periodSeconds:30,deviceId:'admin-laptop',usable:true}});
   return accept({ok:false,error:{code:'UNEXPECTED_OPERATION'}},403);
 }
 if(url.startsWith('/api/applications/slh/seh/'))return accept({ok:false,error:{code:'NOT_CONFIGURED'}},503);
 return accept({ok:false,error:{code:'NOT_CONFIGURED'}},503);
};
</script>'''
    source=source.replace('<script type="application/json" id="mcel-generated-data">',stub+'<script type="application/json" id="mcel-generated-data">')
    with pw.sync_playwright() as p:
        browser=p.chromium.launch(headless=True,**({'executable_path':executable} if executable else {}),args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':width,'height':height})
            errors=[]
            page.on('pageerror',lambda exc:errors.append(str(exc)))
            page.set_content(source,wait_until='domcontentloaded')
            row=page.locator('[data-record-key="admin-laptop"]').first
            row.wait_for(timeout=10000)
            assert row.get_by_text('Administrator laptop').count()==1
            assert row.get_by_text('a'*64).count()==0
            assert page.get_by_text('Pair',exact=True).count()==0
            assert page.get_by_text('Revoke',exact=True).count()==0
            row.locator('summary').click()
            status=row.locator('[data-mcel-passkey-status]')
            status.get_by_text('Usable',exact=True).wait_for(timeout=10000)
            code=row.locator('[data-mcel-passkey-code]')
            assert code.inner_text()=='12345678'
            assert 's remaining' in row.locator('[data-mcel-passkey-remaining]').inner_text()
            assert row.locator('[data-mcel-credential-detail] button').count()==1
            assert row.locator('[data-mcel-passkey-toggle]').count()==0
            geometry=row.evaluate("""row => {
              const detail=row.querySelector('[data-mcel-inline-details]');
              const credential=detail.querySelector('[data-mcel-credential-detail]');
              const rail=row.closest('[data-mcel-purpose="navigation"]');
              return [row,detail,credential,...credential.children].map(el=>{
                const r=el.getBoundingClientRect(),p=rail.getBoundingClientRect();
                return {left:r.left,right:r.right,railLeft:p.left,railRight:p.right};
              });
            }""")
            assert all(item['left']>=item['railLeft']-1 and item['right']<=item['railRight']+1 for item in geometry),geometry
            row.get_by_role('button',name='Copy').click()
            assert page.evaluate('window.__clipboard')==['12345678']
            row.locator('summary').click()
            page.wait_for_function("document.querySelector('[data-mcel-passkey-code]').textContent==='•••• ••••'")
            assert code.text_content()=='•••• ••••'
            assert page.evaluate('window.__otpWrites')==[{'operation':'current','deviceId':'admin-laptop'}]
            assert '12345678' not in page.locator('[data-mcel-activity-history]').all_inner_texts().__repr__()
            assert not errors, errors
        finally:
            browser.close()


def test_disclosure_is_bound_to_its_own_identity_and_clears_previous_code():
    pw=pytest.importorskip('playwright.sync_api')
    source=render_slh_page()
    stub=r'''<script>
window.__otpWrites=[];
window.fetch=async(url,opts={})=>{
 const accept=(result,status=200)=>({ok:status>=200&&status<300,status,json:async()=>result});
 if(url.startsWith('/api/applications/slh/identities'))return accept({schema:'mcel.slh.identities.v4b',state:'AVAILABLE',defaultDeviceId:'admin-laptop',explanation:'Verified identity',identities:[{deviceId:'admin-laptop',fingerprint:'a'.repeat(64),label:'Administrator laptop',defaultStatus:'DEFAULT'},{deviceId:'another-laptop',fingerprint:'b'.repeat(64),label:'Another administrator',defaultStatus:'ADDITIONAL'}]});
 if(url.startsWith('/api/applications/slh/observe'))return accept({ok:true,schema:'mcel.slh.first-slice-observation.v1',view:'overview',overview:{organizationState:'running',organizationEndpoint:'127.0.0.1:8899',serviceState:'RUNNING',observationState:'OBSERVED'}});
 if(url.endsWith('/setup/action')){
    const request=JSON.parse(opts.body);const identity=request.arguments.adminDeviceId;
    return accept({access_token:'a'.repeat(32),device_id:identity,expires_at:Math.floor(Date.now()/1000)+900});
 }
 if(url.endsWith('/software-authenticator')){
   const request=JSON.parse(opts.body);window.__otpWrites.push(request);
   return accept({ok:true,result:{code:request.deviceId==='admin-laptop'?'11112222':'33334444',expiresAt:Math.floor(Date.now()/1000)+25,periodSeconds:30,deviceId:request.deviceId,usable:true}});
 }
 return accept({ok:false,error:{code:'NOT_CONFIGURED'}},503)
};</script>'''
    source=source.replace('<script type="application/json" id="mcel-generated-data">',stub+'<script type="application/json" id="mcel-generated-data">')
    with pw.sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path=shutil.which('chromium'),args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':1150,'height':720})
            page.set_content(source,wait_until='domcontentloaded')
            first=page.locator('[data-record-key="admin-laptop"]')
            second=page.locator('[data-record-key="another-laptop"]')
            first.locator('summary').click()
            first.locator('[data-mcel-passkey-code]').get_by_text('11112222',exact=True).wait_for(timeout=10000)
            second.locator('summary').click()
            second.locator('[data-mcel-passkey-code]').get_by_text('33334444',exact=True).wait_for(timeout=10000)
            assert first.locator('[data-mcel-passkey-code]').text_content()=='•••• ••••'
            assert page.evaluate('window.__otpWrites')==[
                {'operation':'current','deviceId':'admin-laptop'},
                {'operation':'current','deviceId':'another-laptop'}]
        finally:
            browser.close()
