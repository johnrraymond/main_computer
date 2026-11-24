"""Long-period CRT patina is composited without creating containing blocks."""
from pathlib import Path
from playwright.sync_api import sync_playwright
from main_computer.mcel_calculator_expanded_host import render_expanded_page
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

ROOT=Path(__file__).resolve().parents[1]
SCRIPTS=ROOT/'main_computer/web/applications/scripts'

def test_patina_has_long_period_without_absolute_overlays_on_static_elements():
    with sync_playwright() as pw:
        browser=_chrome(pw,None)
        try:
            page=browser.new_page(viewport={'width':1440,'height':900})
            page.set_content(render_expanded_page(ROOT,promoted=True))
            for name in ('mcel-semantic-presentation-bridge.js','mcel-semantic-chrome-presentation-law.js'):
                page.add_script_tag(path=str(SCRIPTS/name))
            report=page.evaluate(r'''() => {
              const root=document.getElementById('mcel-expanded-root');
              const ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
              const opts={root,ir,bridge:McelSemanticPresentationBridge,
                styleLaw:McelLabStyleLaw,theme:'theme-machine',profile:'chrome-tng-side-rails'};
              const on=McelSemanticChromePresentationLaw.apply(opts);
              const css=kind=>{const e=root.querySelector(`[data-mcel-chrome-material="${kind}"]`);
                const v=getComputedStyle(e);return{image:v.backgroundImage,size:v.backgroundSize,
                  position:v.position,wear:e.querySelectorAll(':scope > [data-mcel-tng-wear-surface]').length};};
              const panel=css('housing'),display=css('instrument-glass'),button=css('raised-control');
              const el=root.querySelector('[data-mcel-chrome-material="raised-control"]');
              const label={text:el.textContent,shadow:getComputedStyle(el).textShadow,
                wraps:el.querySelectorAll('[data-mcel-tng-gold-text]').length};
              const ambient=getComputedStyle(root).backgroundImage;
              const off=McelSemanticChromePresentationLaw.apply({...opts,profile:'chrome-strict-hierarchy'});
              return {on:on.status,off:off.status,panel,display,button,label,ambient,
                remaining:root.querySelectorAll('[data-mcel-tng-wear-surface]').length};
            }''')
            assert report['on']=='applied' and report['off']=='unchanged',report
            for role in ('panel','display'):
                x=report[role]
                assert 'data:image/webp;base64,' in x['image'] and '1024px 768px' in x['size'],x
                if x['position']=='static': assert x['wear']==0,x
            assert '21px 6px' in report['button']['size'],report
            assert 'data:image/webp;base64,' in report['ambient'],report
            assert 'data:image/svg+xml,' in report['ambient'],report
            assert report['label']['shadow']=='none' and report['label']['wraps']==0,report
            assert report['label']['text'] and report['remaining']==0,report
        finally:
            browser.close()
