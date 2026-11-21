"""Real Chromium: consumed registration slot -> next usable identity passkey."""
from __future__ import annotations
import shutil
import pytest
from main_computer.mcel_slh_application import render_slh_page


@pytest.mark.parametrize('width,height',[(1150,720),(740,460)])
def test_identity_details_marks_consumed_code_and_rotates(width,height):
    playwright=pytest.importorskip('playwright.sync_api')
    executable=shutil.which('chromium') or shutil.which('chromium-browser')
    source=render_slh_page()
    stub=r'''<script>
window.__otpWrites=[];
window.fetch=async(url,opts={})=>{
 const accept=(result,status=200)=>({ok:status>=200&&status<300,status,json:async()=>result});
 if(url.startsWith('/api/applications/slh/identities'))return accept({schema:'mcel.slh.identities.v4b',state:'AVAILABLE',defaultDeviceId:'admin-laptop',explanation:'Verified identity',identities:[{deviceId:'admin-laptop',fingerprint:'a'.repeat(64),label:'Administrator laptop',defaultStatus:'DEFAULT'}]});
 if(url.startsWith('/api/applications/slh/observe'))return accept({ok:true,schema:'mcel.slh.first-slice-observation.v1',view:'overview',overview:{organizationState:'running',organizationEndpoint:'127.0.0.1:8899',serviceState:'RUNNING',observationState:'OBSERVED'}});
 if(url.endsWith('/setup/action'))return accept({access_token:'a'.repeat(32),device_id:'admin-laptop',expires_at:Math.floor(Date.now()/1000)+900});
 if(url.endsWith('/software-authenticator')){
   const request=JSON.parse(opts.body);window.__otpWrites.push(request);
   if(request.operation!=='current')return accept({ok:false,error:{code:'NOT_ADMITTED'}},403);
   const first=window.__otpWrites.length===1;
   return accept({ok:true,result:{code:first?null:'87654321',expiresAt:Math.floor(Date.now()/1000)+(first?2:22),periodSeconds:30,deviceId:'admin-laptop',usable:!first}});
 }
 return accept({ok:false,error:{code:'NOT_CONFIGURED'}},503);
};
</script>'''
    source=source.replace('<script type="application/json" id="mcel-generated-data">',stub+'<script type="application/json" id="mcel-generated-data">')
    with playwright.sync_playwright() as p:
        browser=p.chromium.launch(headless=True,**({'executable_path':executable} if executable else {}),args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':width,'height':height})
            errors=[]
            page.on('pageerror',lambda exc:errors.append(str(exc)))
            page.set_content(source,wait_until='domcontentloaded')
            row=page.locator('[data-record-key="admin-laptop"]')
            row.locator('summary').click(timeout=10000)
            row.locator('[data-mcel-passkey-status]').get_by_text('Old / expired',exact=True).wait_for(timeout=10000)
            assert row.locator('[data-mcel-passkey-copy]').is_disabled()
            assert row.locator('[data-mcel-passkey-code]').text_content()=='•••• ••••'
            row.locator('[data-mcel-passkey-status]').get_by_text('Usable',exact=True).wait_for(timeout=12000)
            assert row.locator('[data-mcel-passkey-code]').text_content()=='87654321'
            assert row.locator('[data-mcel-passkey-copy]').is_enabled()
            assert page.evaluate('window.__otpWrites')==[
                {'operation':'current','deviceId':'admin-laptop'},
                {'operation':'current','deviceId':'admin-laptop'}]
            assert not errors, errors
        finally:
            browser.close()
