"""D+C+E+H cinematic chrome gold never paints the parent surface or blurs small labels."""
from pathlib import Path
from playwright.sync_api import sync_playwright
from main_computer.mcel_calculator_expanded_host import render_expanded_page
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'main_computer/web/applications/scripts'


def test_cinematic_gold_is_glyph_only_reversible_and_dynamically_reconciled():
    with sync_playwright() as pw:
        browser = _chrome(pw, None)
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        page.set_content(render_expanded_page(ROOT, promoted=True))
        for script in ('mcel-semantic-presentation-bridge.js',
                       'mcel-semantic-chrome-presentation-law.js'):
            page.add_script_tag(path=str(SCRIPTS / script))
        result = page.evaluate('''async () => {
          const root=document.getElementById('mcel-expanded-root');
          const ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
          const originalHeading=root.querySelector('h1');
          const originalButton=root.querySelector('button');
          const originalText=originalButton.firstChild;
          // Configuration is a genuine canonical region heading, NOT a keycap.
          const config=root.ownerDocument.createElement('h2');
          config.textContent='Configuration';
          config.style.cssText='background-color:rgb(9,38,30);padding:3px;';
          root.append(config);
          const initialConfigBackground=getComputedStyle(config).backgroundColor;
          const initialConfigImage=getComputedStyle(config).backgroundImage;
          const initialConfigGeometry=config.getBoundingClientRect().toJSON();
          const initialHeadingGeometry=originalHeading.getBoundingClientRect().toJSON();
          const initialButtonGeometry=originalButton.getBoundingClientRect().toJSON();
          const opts={root,ir,bridge:McelSemanticPresentationBridge,
            styleLaw:McelLabStyleLaw,profile:'chrome-tng-side-rails',theme:'theme-machine'};
          const applied=McelSemanticChromePresentationLaw.apply(opts);
          const paint=el=>{let s=getComputedStyle(el);return {
            background:s.backgroundImage,clip:s.backgroundClip,fill:s.webkitTextFillColor,
            shadow:s.textShadow,stroke:s.webkitTextStrokeWidth,filter:s.filter};};
          const goldHeading=originalHeading.querySelector('[data-mcel-tng-gold-text]');
          const goldButton=originalButton.querySelector('[data-mcel-tng-gold-text]');
          const goldConfig=config.querySelector('[data-mcel-tng-gold-text]');
          const underChrome={
            configBackground:getComputedStyle(config).backgroundColor,
            configImage:getComputedStyle(config).backgroundImage,
            configGeometry:config.getBoundingClientRect().toJSON(),
            headingGeometry:originalHeading.getBoundingClientRect().toJSON(),
            buttonGeometry:originalButton.getBoundingClientRect().toJSON(),
            headingParent:paint(originalHeading),
            headingGlyph:paint(goldHeading),
            buttonParent:paint(originalButton),
            buttonGlyph:goldButton?paint(goldButton):null,
            configGlyph:paint(goldConfig),
            sameTextNode:originalButton.firstChild===originalText,
            unchangedContent:originalButton.textContent===originalText.textContent};
          const dynamic=document.createElement('button');
          dynamic.textContent='Deploy new';
          let clicked=0;dynamic.addEventListener('click',()=>clicked++);
          root.append(dynamic);
          await new Promise(resolve=>setTimeout(resolve,30));
          dynamic.click();
          const dynamicIsGold=getComputedStyle(dynamic).webkitTextFillColor==='rgb(243, 200, 120)' &&
            dynamic.firstChild?.nodeType===3 && !dynamic.querySelector('[data-mcel-tng-gold-text]');
          const reset=McelSemanticChromePresentationLaw.apply({...opts,
            profile:'chrome-strict-hierarchy'});
          return {applied,underChrome,reset,initialConfigBackground,
            initialConfigImage,initialConfigGeometry,initialHeadingGeometry,
            initialButtonGeometry,dynamicIsGold,clicked,
            originalTextRestored:originalButton.firstChild===originalText,
            wrappersAfterReset:root.querySelectorAll('[data-mcel-tng-gold-text]').length,
            svgDefinitionsAfterReset:!!document.getElementById('mcel-tng-cinematic-bevel-filter')};
        }''')
        assert result['applied']['status']=='applied', result
        paint=result['underChrome']
        assert paint['configBackground'] == result['initialConfigBackground'],result
        assert paint['configImage'] == result['initialConfigImage'],result
        assert paint['configGeometry'] == result['initialConfigGeometry'],result
        assert paint['headingGeometry'] == result['initialHeadingGeometry'],result
        assert paint['buttonGeometry'] == result['initialButtonGeometry'],result
        for name in ('headingGlyph','configGlyph'):
            assert all(part.strip()=='text' for part in paint[name]['clip'].split(',')) and 'gradient' in paint[name]['background'],(name,paint[name])
            assert paint[name]['fill']=='rgba(0, 0, 0, 0)',(name,paint[name])
            assert paint[name]['shadow']=='none',(name,paint[name])
        assert 'mcel-tng-cinematic-bevel-filter' in paint['configGlyph']['filter'],paint
        assert paint['buttonGlyph'] is None,paint
        assert paint['buttonParent']['fill']=='rgb(243, 200, 120)',paint
        assert paint['buttonParent']['shadow']=='none',paint
        assert paint['sameTextNode'] and paint['unchangedContent'],paint
        assert result['dynamicIsGold'] and result['clicked']==1,result
        assert result['wrappersAfterReset']==0 and result['originalTextRestored'],result
        assert not result['svgDefinitionsAfterReset'],result
        assert result['reset']['status']=='unchanged',result
        browser.close()


def test_cinematic_text_follows_system_forced_colors():
    with sync_playwright() as pw:
        browser = _chrome(pw,None)
        page=browser.new_page()
        page.set_content(render_expanded_page(ROOT, promoted=True))
        for script in ('mcel-semantic-presentation-bridge.js','mcel-semantic-chrome-presentation-law.js'):
            page.add_script_tag(path=str(SCRIPTS/script))
        page.evaluate('''() => {
          const root=document.getElementById('mcel-expanded-root');
          const ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
          McelSemanticChromePresentationLaw.apply({root,ir,bridge:McelSemanticPresentationBridge,
            styleLaw:McelLabStyleLaw,profile:'chrome-tng-side-rails',theme:'theme-machine'});
        }''')
        page.emulate_media(forced_colors='active')
        styles=page.evaluate('''() => [...document.querySelectorAll('[data-mcel-tng-gold-text]')]
          .slice(0,10).map(el=>{const s=getComputedStyle(el);return {
          bg:s.backgroundImage,shadow:s.textShadow,filter:s.filter,fill:s.webkitTextFillColor,color:s.color};})''')
        assert styles,styles
        assert all(x['bg']=='none' and x['filter']=='none' and x['shadow']=='none' and x['fill']==x['color'] for x in styles),styles
        browser.close()
