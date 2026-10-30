"""The original MCEL row, not an independent body-portal mirror, owns hover."""
from playwright.sync_api import sync_playwright
from tools.mcel_patch14_7_capability_negotiation_frob import SCRIPTS, _chrome
from tools.mcel_task_manager_notebook_lab_acceptance import html_fixture
from tools.mcel_patch14_6_promise_verification_frob import scenario


def test_real_mouse_continuous_hover_original_and_extension():
    with sync_playwright() as p:
        browser=_chrome(p,None)
        try:
            for width in (390,1440):
                page=browser.new_page(viewport={'width':width,'height':850},reduced_motion='reduce')
                errors=[]
                page.on('pageerror',lambda e: errors.append(str(e)))
                page.set_content(html_fixture(),wait_until='load')
                for script in ('mcel-presentation-negotiation.js','mcel-presentation-hosts.js',
                               'mcel-layout-continuity.js','mcel-universal-semantic-layout-lab.js'):
                    page.add_script_tag(path=str(SCRIPTS/script))
                page.evaluate('''({plan})=>{
                    const host=document.createElement('div');document.querySelector('#task-manager-app').append(host);
                    window.__lab=McelUniversalSemanticLayoutLab.mount(host,plan,
                      {lab:true,presentationNegotiation:true});
                    window.__row=host.querySelector('mcel-row');
                }''',{'plan':scenario('normal')['plan']})
                page.locator('mcel-row').first.hover()
                page.wait_for_timeout(1250)
                assert page.locator('.mcel-unclip-card').count()==1
                state=page.evaluate('''()=>{
                    const row=__row,card=__lab.collection.floatingCard,rect=card.getBoundingClientRect();
                    return {owns:card.parentElement===row,hovered:row.matches(':hover'),
                      noClone:!card.querySelector('.mcel-unclip-mirror'),
                      x:rect.left+Math.min(rect.width/2,30),y:rect.top+Math.min(rect.height/2,30)};
                }''')
                assert state['owns'] and state['hovered'] and state['noClone'],state
                page.mouse.move(state['x'],state['y'])
                page.wait_for_timeout(1500)
                assert page.locator('.mcel-unclip-card').count()==1,'extension dismissed under active pointer'
                assert page.evaluate('window.__row.matches(":hover")'),'source no longer owns hover'
                page.mouse.move(1,1)
                page.wait_for_timeout(400)
                assert page.locator('.mcel-unclip-card').count()==1
                page.wait_for_timeout(800)
                assert page.locator('.mcel-unclip-card').count()==0
                assert not errors,errors
                page.evaluate('window.__lab.destroy()')
                page.close()
        finally:browser.close()
