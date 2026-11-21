"""Identity credentials stay within their row and rotate automatically."""
import shutil
import pytest
from main_computer.mcel_slh_application import render_slh_page


@pytest.mark.parametrize('width,height',[(740,460),(1120,730)])
def test_credential_rollover_is_in_place_and_only_copy_button(width,height):
    playwright=pytest.importorskip('playwright.sync_api')
    executable=shutil.which('chromium') or shutil.which('chromium-browser')
    html=render_slh_page()
    stub=r'''<script>
window.__otpReads=[];window.__copied=[];
Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async value=>window.__copied.push(value)}});
window.fetch=async(url,opts={})=>{
 const accept=(result,status=200)=>({ok:status>=200&&status<300,status,json:async()=>result});
 if(url.startsWith('/api/applications/slh/identities'))return accept({schema:'mcel.slh.identities.v4b',state:'AVAILABLE',defaultDeviceId:'admin-laptop',explanation:'Verified identity',identities:[{deviceId:'admin-laptop',fingerprint:'b'.repeat(64),label:'Administrator',defaultStatus:'DEFAULT'}]});
 if(url.startsWith('/api/applications/slh/observe'))return accept({ok:true,schema:'mcel.slh.first-slice-observation.v1',view:'overview',overview:{organizationState:'running',organizationEndpoint:'127.0.0.1:8899',serviceState:'RUNNING',observationState:'OBSERVED'}});
 if(url.endsWith('/setup/action'))return accept({access_token:'a'.repeat(32),device_id:'admin-laptop',expires_at:Math.floor(Date.now()/1000)+900});
 if(url.endsWith('/software-authenticator')){
   const request=JSON.parse(opts.body);window.__otpReads.push(request);
   if(request.operation!=='current')return accept({ok:false,error:{code:'NOT_ADMITTED'}},403);
   const num=window.__otpReads.length;
   const code=num===1?'11112222':num===2?'33334444':'55556666';
   return accept({ok:true,result:{code,expiresAt:Math.floor(Date.now()/1000)+(num<3?2:29),periodSeconds:30,deviceId:'admin-laptop',usable:true}});
 }
 return accept({ok:false,error:{code:'NOT_CONFIGURED'}},503);
};
</script>'''
    html=html.replace('<script type="application/json" id="mcel-generated-data">',stub+'<script type="application/json" id="mcel-generated-data">')
    with playwright.sync_playwright() as p:
        browser=p.chromium.launch(headless=True,**({'executable_path':executable} if executable else {}),args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':width,'height':height})
            errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.set_content(html,wait_until='domcontentloaded')
            row=page.locator('[data-record-key="admin-laptop"]')
            row.locator('summary').click(timeout=12000)
            code=row.locator('[data-mcel-passkey-code]')
            code.get_by_text('11112222',exact=True).wait_for(timeout=12000)
            assert row.locator('[data-mcel-credential-detail] button').count()==1
            assert row.get_by_role('button',name='Copy').count()==1
            assert page.locator('[data-mcel-passkey-toggle]').count()==0
            bounds=row.evaluate('''row=>{
              const nav=row.closest('[data-mcel-purpose="navigation"]').getBoundingClientRect();
              const selectors=['[data-mcel-inline-details]','[data-mcel-credential-detail]',
                '[data-mcel-passkey-status]','[data-mcel-passkey-code]',
                '[data-mcel-passkey-remaining]','[data-mcel-passkey-copy]'];
              return selectors.map(selector=>{
                const r=row.querySelector(selector).getBoundingClientRect();
                return {selector,left:r.left,right:r.right,navLeft:nav.left,navRight:nav.right};
              });
            }''')
            assert all(x['left']>=x['navLeft']-1 and x['right']<=x['navRight']+1 for x in bounds),bounds
            first=int(row.locator('[data-mcel-passkey-remaining]').inner_text().split('s')[0])
            assert 0<first<=2
            code.get_by_text('33334444',exact=True).wait_for(timeout=6500)
            code.get_by_text('55556666',exact=True).wait_for(timeout=6500)
            remaining=int(row.locator('[data-mcel-passkey-remaining]').inner_text().split('s')[0])
            assert remaining>=25,(remaining,first)
            assert row.locator('[data-mcel-passkey-status]').inner_text()=='Usable'
            row.get_by_role('button',name='Copy').click()
            assert page.evaluate('window.__copied')==['55556666']
            assert page.evaluate('window.__otpReads')==[
                {'operation':'current','deviceId':'admin-laptop'} for _ in range(3)]
            assert not errors,errors
        finally:
            browser.close()
