"""CRT grunge is present on large material surfaces, quiet on buttons."""
from pathlib import Path
from playwright.sync_api import sync_playwright
from main_computer.mcel_calculator_expanded_host import render_expanded_page
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

ROOT=Path(__file__).resolve().parents[1]
SCRIPTS=ROOT/'main_computer/web/applications/scripts'

def test_grunge_is_composited_on_panels_not_reintroduced_as_button_scratch_overlay():
    with sync_playwright() as pw:
        browser=_chrome(pw,None)
        try:
            page=browser.new_page();page.set_content(render_expanded_page(ROOT,promoted=True))
            for name in ('mcel-semantic-presentation-bridge.js','mcel-semantic-chrome-presentation-law.js'):
                page.add_script_tag(path=str(SCRIPTS/name))
            report=page.evaluate(r'''() => {
              const root=document.getElementById('mcel-expanded-root');
              const ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
              const result=McelSemanticChromePresentationLaw.apply({root,ir,bridge:McelSemanticPresentationBridge,
                styleLaw:McelLabStyleLaw,theme:'theme-machine',profile:'chrome-tng-side-rails'});
              const paint=kind=>{const e=root.querySelector(`[data-mcel-chrome-material="${kind}"]`);
                const s=getComputedStyle(e);return{image:s.backgroundImage,size:s.backgroundSize,
                  position:s.position,wear:e.querySelectorAll(':scope > [data-mcel-tng-wear-surface]').length};};
              const glass=document.querySelector('[data-mcel-tng-stationary-glass]');
              const button=root.querySelector('[data-mcel-chrome-material="raised-control"]');
              return {status:result.status,glass:{image:getComputedStyle(glass).backgroundImage,position:getComputedStyle(glass).position},panel:paint('housing'),display:paint('instrument-glass'),
                button:paint('raised-control'),label:button.textContent,
                wraps:button.querySelectorAll('[data-mcel-tng-gold-text]').length};
            }''')
            assert report['status']=='applied',report
            for role in ('panel','display'):
                p=report[role]
                assert 'data:image/webp;base64,' not in p['image'],p
                assert p['wear']==0,p
            assert 'data:image/webp;base64,' in report['glass']['image'] and report['glass']['position']=='fixed',report
            assert '21px 6px' in report['button']['size'],report
            assert report['label'] and report['wraps']==0,report
        finally:
            browser.close()
