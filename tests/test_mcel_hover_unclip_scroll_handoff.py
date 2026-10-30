"""Real-browser geometry, 15% chain threshold and hover timing acceptance."""
from pathlib import Path
from tools.mcel_hover_unclip_scroll_handoff_frob import verify


def test_geometry_and_scroll_handoff(tmp_path:Path):
    report=verify(tmp_path/'unclip')
    assert report['status']=='PASS',report['errors']
    assert len(report['cases'])==4
    assert all(case['geometry']['sourceExact'] and case['geometry']['trackOutside']
               and case['pixels']['cardExpanded'] and case['pixels']['meanAbsoluteDifference']<3.0 for case in report['cases'])
    assert all(case['handoff']['held'] and case['handoff']['transferred'] and
               case['handoff']['released'] for case in report['cases'])


def test_real_pointer_dwell_and_leave_timers():
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch14_7_capability_negotiation_frob import SCRIPTS,_chrome
    from tools.mcel_task_manager_notebook_lab_acceptance import html_fixture
    from tools.mcel_patch14_6_promise_verification_frob import scenario
    with sync_playwright() as p:
        browser=_chrome(p,None)
        try:
            page=browser.new_page(viewport={'width':390,'height':850},reduced_motion='reduce')
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.set_content(html_fixture(),wait_until='load')
            for f in ('mcel-presentation-negotiation.js','mcel-presentation-hosts.js',
                      'mcel-layout-continuity.js','mcel-universal-semantic-layout-lab.js'):
                page.add_script_tag(path=str(SCRIPTS/f))
            page.evaluate('''({plan})=>{
              const host=document.createElement('div');document.querySelector('#task-manager-app').append(host);
              window.__testMount=McelUniversalSemanticLayoutLab.mount(host,plan,
                {lab:true,presentationNegotiation:true});}''',{'plan':scenario('normal')['plan']})
            row=page.locator('mcel-row').first;row.hover()
            page.wait_for_timeout(250)
            assert page.locator('.mcel-unclip-card').count()==0
            page.wait_for_timeout(950)
            assert page.locator('.mcel-unclip-card').count()==1
            page.mouse.move(0,0)
            page.wait_for_timeout(450)
            assert page.locator('.mcel-unclip-card').count()==1
            page.wait_for_timeout(700)
            assert page.locator('.mcel-unclip-card').count()==0
            assert not errors,errors
            page.evaluate('window.__testMount.destroy()')
            page.close()
        finally:browser.close()
