
"""Restored grunge balance for the subtle aperture-grille CRT treatment.

Large housing and active display surfaces must carry the aged-console patina,
while buttons remain quieter and text-node ownership stays intact.
"""
from pathlib import Path
from playwright.sync_api import sync_playwright
from main_computer.mcel_calculator_expanded_host import render_expanded_page
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'main_computer/web/applications/scripts'


def test_restored_grunge_is_stronger_on_panels_and_displays_than_buttons():
    with sync_playwright() as pw:
        browser = _chrome(pw, None)
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 900})
            page.set_content(render_expanded_page(ROOT, promoted=True))
            for name in ('mcel-semantic-presentation-bridge.js',
                         'mcel-semantic-chrome-presentation-law.js'):
                page.add_script_tag(path=str(SCRIPTS / name))
            result = page.evaluate(r'''() => {
              const root=document.getElementById('mcel-expanded-root');
              const ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
              const opts={root,ir,bridge:McelSemanticPresentationBridge,
                styleLaw:McelLabStyleLaw,theme:'theme-machine',profile:'chrome-tng-side-rails'};
              const applied=McelSemanticChromePresentationLaw.apply(opts);
              const pick = kind => root.querySelector(`[data-mcel-tng-wear-surface="${kind}"]`);
              const panel = pick('panel');
              const display = pick('display');
              const button = pick('button');
              const read = el => {
                const glint = el?.querySelector('[data-mcel-tng-wear-glint]');
                return {
                  exists: !!el,
                  opacity: el ? Number(getComputedStyle(el).opacity) : null,
                  mask: el ? getComputedStyle(el).maskImage : null,
                  glintOpacity: glint ? Number(getComputedStyle(glint).opacity) : null,
                  glintMask: glint ? getComputedStyle(glint).maskImage : null,
                };
              };
              const textProbe = root.querySelector('button[type="submit"]') || root.querySelector('button');
              return {applied, panel: read(panel), display: read(display), button: read(button),
                buttonText: textProbe?.textContent || null,
                buttonGoldWraps: textProbe?.querySelectorAll('[data-mcel-tng-gold-text]').length || 0};
            }''')
            assert result['applied']['status'] == 'applied', result
            assert result['panel']['exists'] and result['display']['exists'] and result['button']['exists'], result
            assert result['panel']['opacity'] > result['button']['opacity'], result
            assert result['display']['opacity'] > result['button']['opacity'], result
            assert result['panel']['glintOpacity'] > result['button']['glintOpacity'], result
            assert result['display']['glintOpacity'] > result['button']['glintOpacity'], result
            assert result['buttonText'], result
            assert result['buttonGoldWraps'] == 0, result
        finally:
            browser.close()
