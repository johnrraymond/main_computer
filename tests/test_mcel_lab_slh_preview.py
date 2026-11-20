"""SLH is a real, source-bound MCEL Lab preview, not a fabricated lab specimen.

All browser transport is synthetic and unavailable. The test never contacts or
mutates an operator's actual SLH or SEH.
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

from main_computer.mcel_slh_application import render_slh_page
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'main_computer/web/applications'


def test_slh_lab_catalog_and_real_route_share_one_canonical_identity():
    markup = (WEB / 'apps/mcel-lab.html').read_text(encoding='utf-8')
    lab = (WEB / 'scripts/mcel-lab.js').read_text(encoding='utf-8')
    assert '<option value="slh">SLH</option>' in markup
    assert 'route: "/applications/slh"' in lab
    assert 'irId: "mcel-generated-data", appId: "slh", wrappedIr: true' in lab
    html = render_slh_page()
    assert 'id="mcel-generated-data"' in html
    assert 'id="mcel-workbench"' in html
    assert 'appId:data.ir.application.appId' in html


def test_actual_slh_mount_is_source_bound_and_theme_compatible():
    with sync_playwright() as p:
        browser = _chrome(p, None)
        page = browser.new_page(viewport={'width': 1280, 'height': 820})
        try:
            # Deny all external calls; only the canonical HTML/IR and mounted
            # operational DOM are under test, with no service authority.
            page.evaluate('''() => {
              window.fetch=async()=>({ok:false,status:503,
                json:async()=>({error:{code:'UNAVAILABLE'}})});
            }''')
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.set_content(render_slh_page(), wait_until='domcontentloaded')
            page.wait_for_function('() => !!window.__mcelSlh?.app')
            page.add_script_tag(path=str(WEB / 'scripts/mcel-semantic-presentation-bridge.js'))
            page.add_script_tag(path=str(WEB / 'scripts/mcel-semantic-theme-preview.js'))
            report = page.evaluate('''() => {
              const envelope=JSON.parse(document.getElementById('mcel-generated-data').textContent);
              const {ir,composition}=envelope;
              const root=document.getElementById('mcel-workbench');
              const binding=McelSemanticPresentationBridge.observeRuntimePresentationTargets({
                ir,composition,root});
              const themed=McelSemanticThemePreview.apply({root,ir,
                bridge:McelSemanticPresentationBridge,styleLaw:McelLabStyleLaw,
                theme:'theme-machine',bindingModel:'operational-runtime'});
              return {appId:__mcelSlh.appId, irId:ir.application.appId,
                compositionSchema:composition.schema,binding:binding.status,
                controls:binding.targets.filter(t=>t.kind==='control').length,
                regions:binding.targets.filter(t=>t.kind==='region').length,
                expectedControls:binding.expectedControls,expectedRegions:binding.expectedRegions,
                theme:themed.status,themeSource:themed.source,children:root.children.length};
            }''')
            assert report['appId'] == report['irId'] == report['themeSource'] == 'slh'
            assert report['compositionSchema'] == 'mcel.generated-composition.v1'
            assert report['binding'] == 'source-bound'
            assert report['controls'] == report['expectedControls'] > 0
            assert report['regions'] == report['expectedRegions'] > 0
            assert report['children'] > 0
            assert report['theme'] == 'applied-in-isolated-lab-frame'
            assert not errors, errors
        finally:
            browser.close()
