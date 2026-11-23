"""Long-period CRT patina is sparse, ambient-weighted and non-interfering.

The rejected 128x128 SVG had 27 diagonal scratches and 440 dots per repeat.
A 1024x768 wear field is now pre-rendered from multi-scale glass noise and
isolated marks; TNG's original 75%-strength aperture grille stays intact.
"""
from pathlib import Path
from playwright.sync_api import sync_playwright
from main_computer.mcel_calculator_expanded_host import render_expanded_page
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'main_computer/web/applications/scripts'


def test_crt_patina_has_long_period_wear_and_quiet_buttons():
    with sync_playwright() as pw:
        browser = _chrome(pw, None)
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 900})
            page.set_content(render_expanded_page(ROOT, promoted=True))
            for name in ('mcel-semantic-presentation-bridge.js',
                         'mcel-semantic-chrome-presentation-law.js'):
                page.add_script_tag(path=str(SCRIPTS / name))
            data = page.evaluate(r'''async () => {
              const root=document.getElementById('mcel-expanded-root');
              const ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
              const opts={root,ir,bridge:McelSemanticPresentationBridge,
                styleLaw:McelLabStyleLaw,theme:'theme-machine',profile:'chrome-tng-side-rails'};
              const applied=McelSemanticChromePresentationLaw.apply(opts);
              const find = kind => root.querySelector(`[data-mcel-tng-wear-surface="${kind}"]`);
              const get = kind => {
                const e=find(kind);
                const glint=e?.querySelector('[data-mcel-tng-wear-glint]');
                const a=e&&getComputedStyle(e), b=glint&&getComputedStyle(glint);
                return {has:!!e,opacity:Number(a?.opacity||0),glint:Number(b?.opacity||0),
                  size:a?.backgroundSize,texture:a?.backgroundImage,glintTexture:b?.backgroundImage,
                  pointer:a?.pointerEvents};
              };
              const roles={panel:get('panel'),display:get('display'),button:get('button')};
              const bitmap=async background=> {
                const m=/data:image\/webp;base64,([A-Za-z0-9+/=]+)/.exec(background);
                if (!m) return null;
                const blob=await (await fetch('data:image/webp;base64,'+m[1])).blob();
                const image=await createImageBitmap(blob);
                const dims=[image.width,image.height];image.close();return dims;
              };
              const dims=await Promise.all(['panel','display','button'].map(k=>bitmap(roles[k].texture)));
              const ambient=getComputedStyle(root).backgroundImage;
              const button=root.querySelector('[data-mcel-chrome-material="raised-control"]');
              const state={label:button?.textContent,shadow:button&&getComputedStyle(button).textShadow,
                wrappers:button?.querySelectorAll('[data-mcel-tng-gold-text]').length||0,
                childText:[...button.childNodes].filter(n=>n.nodeType===3).length};
              const cleanup=McelSemanticChromePresentationLaw.apply({...opts,profile:'chrome-strict-hierarchy'});
              return {applied,roles,dims,ambient,buttonState:state,cleanup,
                remaining:root.querySelectorAll('[data-mcel-tng-wear-surface]').length};
            }''')
            assert data['applied']['status'] == 'applied', data
            for kind, dims in zip(('panel','display','button'),data['dims']):
                role=data['roles'][kind]
                assert role['has'] and role['pointer']=='none', data
                assert role['size']=='1024px 768px', (kind,role)
                assert role['texture'].count('data:image/webp;base64,')==1, (kind,role)
                assert role['glintTexture'].count('data:image/webp;base64,')==1,(kind,role)
                assert dims==[1024,768],(kind,dims)
            assert data['roles']['panel']['opacity']>data['roles']['button']['opacity'],data
            assert data['roles']['display']['glint']>data['roles']['button']['glint'],data
            assert 'data:image/webp;base64,' in data['ambient'],data
            assert 'data:image/svg+xml,' in data['ambient'],data  # unchanged E grille
            assert data['buttonState']['shadow']=='none',data
            assert data['buttonState']['wrappers']==0,data
            assert data['buttonState']['childText']>=1,data
            assert data['cleanup']['status']=='unchanged' and data['remaining']==0,data
        finally:
            browser.close()
