"""E aperture-grille CRT: 25% quieter than reference E; display > ambient >> controls.

Exercise the actual shared chrome layer in Chromium, including theme cleanup,
pressed/hovered controls, dynamic application state and glyph ownership.
"""
from pathlib import Path
from playwright.sync_api import sync_playwright
from main_computer.mcel_calculator_expanded_host import render_expanded_page
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

ROOT=Path(__file__).resolve().parents[1]
SCRIPTS=ROOT/'main_computer/web/applications/scripts'


def test_aperture_grille_strength_period_and_native_controls():
    with sync_playwright() as pw:
        browser=_chrome(pw,None)
        try:
            page=browser.new_page(viewport={'width':1440,'height':900})
            page.set_content(render_expanded_page(ROOT,promoted=True))
            for name in ('mcel-semantic-presentation-bridge.js',
                         'mcel-semantic-chrome-presentation-law.js'):
                page.add_script_tag(path=str(SCRIPTS/name))
            result=page.evaluate('''() => {
              const root=document.getElementById('mcel-expanded-root');
              const ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
              const button=root.querySelector('button');
              const originalText=button.firstChild;
              const rect=button.getBoundingClientRect().toJSON();
              const opts={root,ir,bridge:McelSemanticPresentationBridge,
                styleLaw:McelLabStyleLaw,theme:'theme-machine',profile:'chrome-tng-side-rails'};
              const applied=McelSemanticChromePresentationLaw.apply(opts);
              const getTile=(role)=>{
                const raw=root.style.getPropertyValue('--mcel-tng-aperture-'+role);
                const m=/data:image\\/svg\\+xml,([^"\\)]+)/.exec(raw);
                if(!m)throw Error('MISSING_TILE:'+role);
                const svg=new DOMParser().parseFromString(decodeURIComponent(m[1]),'image/svg+xml');
                const x1=[...svg.querySelectorAll('rect')].find(el=>
                    el.getAttribute('x')==='1' && el.getAttribute('height')==='6');
                return {w:svg.documentElement.getAttribute('width'),
                  h:svg.documentElement.getAttribute('height'),
                  alpha:Number(x1.getAttribute('fill-opacity')),
                  source:raw,hasRaster:svg.querySelectorAll('path').length===1};
              };
              const tiles=Object.fromEntries(['shell','background','display','button'].map(k=>[k,getTile(k)]));
              const housing=root.querySelector('[data-mcel-chrome-material="housing"]');
              const glass=root.querySelector('[data-mcel-chrome-material="instrument-glass"]');
              const css=el=>{const v=getComputedStyle(el);return {image:v.backgroundImage,
                  size:v.backgroundSize,shadow:v.textShadow};};
              const paint={root:css(root),housing:css(housing),glass:css(glass),button:css(button)};
              button.setAttribute('aria-pressed','true');
              const pressed=css(button);
              button.removeAttribute('aria-pressed');
              const sameRect=JSON.stringify(rect)===JSON.stringify(button.getBoundingClientRect().toJSON());
              const sameText=originalText===button.firstChild;
              const cleaned=McelSemanticChromePresentationLaw.apply({...opts,profile:'chrome-strict-hierarchy'});
              const restored=['shell','background','display','button'].every(role=>
                root.style.getPropertyValue('--mcel-tng-aperture-'+role)==='');
              return {applied,tiles,paint,pressed,sameRect,sameText,
                cleaned,restored,originalControlLabel:button.textContent};
            }''')
            assert result['applied']['status']=='applied', result
            assert result['applied']['displayMaterial']=='black-glass-pixel',result
            for role,t in result['tiles'].items():
                assert (t['w'],t['h'])==('21','6') and t['hasRaster'],(role,t)
            # This is exactly 75% of candidate E's 0.78 / 0.98 / 0.03
            # per-surface modulation (plus shell at .52 of background).
            for role,baseline in [('background',.78),('display',.98),('button',.03),('shell',.78*.52)]:
                alpha=result['tiles'][role]['alpha']
                assert abs(alpha-.082*baseline*.75)<.000011,(role,alpha)
            assert result['tiles']['display']['alpha']>result['tiles']['background']['alpha']>result['tiles']['button']['alpha']*15,result
            for role in ('root','housing','glass','button'):
                p=result['paint'][role]
                assert '21px 6px' in p['size'] and p['image'].count('data:image/svg+xml,')==1,(role,p)
            assert result['paint']['button']['shadow']=='none',result
            assert result['pressed']['size']==result['paint']['button']['size'],result
            assert result['pressed']['image'].count('data:image/svg+xml,')==1,result
            assert result['sameRect'] and result['sameText'],result
            assert result['cleaned']['status']=='unchanged' and result['restored'],result
        finally:
            browser.close()
