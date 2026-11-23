"""The /graphical LCD's 8x6 raster is fused with TNG's existing 8x8 pixels."""
from pathlib import Path
from playwright.sync_api import sync_playwright
from main_computer.mcel_calculator_expanded_host import render_expanded_page
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

ROOT=Path(__file__).resolve().parents[1]
SCRIPTS=ROOT/'main_computer/web/applications/scripts'


def test_graphical_lcd_source_uses_the_measured_grid_periods():
    source=(ROOT/'main_computer/web/graphical.html').read_text()
    assert 'repeating-linear-gradient(90deg, rgba(255,255,255,.028)' in source
    assert 'transparent 1px, transparent 8px' in source
    assert 'repeating-linear-gradient(180deg, rgba(0,0,0,.16)' in source
    assert 'transparent 1px, transparent 6px' in source


def test_tng_aperture_grille_replaces_legacy_lcd_tile_without_changing_graphical_page():
    # /graphical is still a separate display. TNG's chosen CRT material is
    # candidate E, not a direct copy of /graphical's 8x6 grid.
    with sync_playwright() as pw:
        browser=_chrome(pw,None)
        try:
            page=browser.new_page(viewport={'width':1440,'height':900})
            page.set_content(render_expanded_page(ROOT,promoted=True))
            for script in ('mcel-semantic-presentation-bridge.js',
                           'mcel-semantic-chrome-presentation-law.js'):
                page.add_script_tag(path=str(SCRIPTS/script))
            result=page.evaluate(r'''() => {
              const root=document.getElementById('mcel-expanded-root');
              const ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
              const opts={root,ir,bridge:McelSemanticPresentationBridge,
                styleLaw:McelLabStyleLaw,theme:'theme-machine',profile:'chrome-tng-side-rails'};
              const applied=McelSemanticChromePresentationLaw.apply(opts);
              const raw=root.style.getPropertyValue('--mcel-tng-aperture-background');
              const m=/data:image\/svg\+xml,([^"\)]+)/.exec(raw);
              const svg=new DOMParser().parseFromString(decodeURIComponent(m[1]),'image/svg+xml');
              const button=root.querySelector('[data-mcel-chrome-material="raised-control"]');
              const glass=root.querySelector('[data-mcel-chrome-material="instrument-glass"]');
              const housing=root.querySelector('[data-mcel-chrome-material="housing"]');
              const sizes=[root,housing,glass,button].map(e=>getComputedStyle(e).backgroundSize);
              const colors=[root,housing,glass,button].map(e=>getComputedStyle(e).backgroundImage);
              const cleared=McelSemanticChromePresentationLaw.apply({...opts,profile:'chrome-strict-hierarchy'});
              return {applied,dimensions:[svg.documentElement.getAttribute('width'),
                  svg.documentElement.getAttribute('height')],
                sizes,colors,cleared,removed:root.style.getPropertyValue('--mcel-tng-aperture-background')===''};
            }''')
            assert result['applied']['status']=='applied',result
            assert result['dimensions']==['21','6'],result
            assert all('21px 6px' in size for size in result['sizes']),result
            assert all(img.count('data:image/svg+xml,')==1 for img in result['colors']),result
            assert result['cleared']['status']=='unchanged' and result['removed'],result
        finally:
            browser.close()
