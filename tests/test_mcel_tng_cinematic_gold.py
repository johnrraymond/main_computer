"""Cinematic gold is source-safe: no text wrappers, no parent paint damage."""
from pathlib import Path
from playwright.sync_api import sync_playwright
from main_computer.mcel_calculator_expanded_host import render_expanded_page
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

ROOT=Path(__file__).resolve().parents[1]
SCRIPTS=ROOT/'main_computer/web/applications/scripts'

def _page(browser):
    page=browser.new_page(viewport={'width':1440,'height':900})
    page.set_content(render_expanded_page(ROOT,promoted=True))
    for name in ('mcel-semantic-presentation-bridge.js','mcel-semantic-chrome-presentation-law.js'):
        page.add_script_tag(path=str(SCRIPTS/name))
    return page

def test_gold_is_source_safe_and_reversible():
    with sync_playwright() as pw:
        browser=_chrome(pw,None)
        try:
            page=_page(browser)
            result=page.evaluate(r'''() => {
              const root=document.getElementById('mcel-expanded-root');
              const ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
              const heading=root.querySelector('h1');
              const owned=heading.firstChild;
              const button=root.querySelector('button');
              const buttonOwned=button.firstChild;
              const config=document.createElement('h2');
              config.setAttribute('data-mcel-color-role','text.primary');
              config.textContent='Configuration';
              config.style.cssText='background-color:rgb(9,38,30);padding:3px';
              root.append(config);
              const oldConfig=getComputedStyle(config).backgroundColor;
              const oldRect=config.getBoundingClientRect().toJSON();
              const opts={root,ir,bridge:McelSemanticPresentationBridge,
                styleLaw:McelLabStyleLaw,profile:'chrome-tng-side-rails',theme:'theme-machine'};
              const on=McelSemanticChromePresentationLaw.apply(opts);
              const gold=getComputedStyle(heading);
              const buttonPaint=getComputedStyle(button);
              const under={config:getComputedStyle(config).backgroundColor,
                configImage:getComputedStyle(config).backgroundImage,
                rect:config.getBoundingClientRect().toJSON(),
                bg:gold.backgroundImage,clip:gold.backgroundClip,fill:gold.webkitTextFillColor,
                shadow:gold.textShadow,buttonFill:buttonPaint.webkitTextFillColor,
                buttonShadow:buttonPaint.textShadow,
                sameHeading:heading.firstChild===owned,
                sameButton:button.firstChild===buttonOwned,
                wraps:root.querySelectorAll('[data-mcel-tng-gold-text]').length};
              let count=0;
              for(const value of ['Running','Stopped','Running']){
                owned.nodeValue=value;
                count+=Number(heading.firstChild===owned&&heading.textContent===value);
              }
              const off=McelSemanticChromePresentationLaw.apply({...opts,profile:'chrome-strict-hierarchy'});
              return{on:on.status,off:off.status,under,oldConfig,oldRect,count,
                restoredText:heading.firstChild===owned,
                restoredWraps:root.querySelectorAll('[data-mcel-tng-gold-text]').length};
            }''')
            assert result['on']=='applied' and result['off']=='unchanged',result
            u=result['under']
            assert u['config']==result['oldConfig'] and u['configImage']=='none',u
            assert u['rect']==result['oldRect'],u
            assert 'gradient' in u['bg'] and 'text' in u['clip'] and u['fill']=='rgba(0, 0, 0, 0)',u
            assert u['shadow']==u['buttonShadow']=='none',u
            assert u['buttonFill']=='rgb(243, 200, 120)',u
            assert u['sameHeading'] and u['sameButton'] and u['wraps']==0,u
            assert result['count']==3 and result['restoredText'] and result['restoredWraps']==0,result
        finally:
            browser.close()

def test_gold_follows_forced_colors_without_decorative_spans():
    with sync_playwright() as pw:
        browser=_chrome(pw,None)
        try:
            page=_page(browser)
            page.evaluate(r'''() => {
              const root=document.getElementById('mcel-expanded-root');
              const ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
              McelSemanticChromePresentationLaw.apply({root,ir,bridge:McelSemanticPresentationBridge,
                styleLaw:McelLabStyleLaw,profile:'chrome-tng-side-rails',theme:'theme-machine'});
            }''')
            page.emulate_media(forced_colors='active')
            data=page.evaluate(r'''() => [...document.querySelectorAll('#mcel-expanded-root h1,#mcel-expanded-root h2,#mcel-expanded-root h3')]
              .filter(el=>!el.hasAttribute('style'))
              .slice(0,10).map(el=>{const s=getComputedStyle(el);return {
                bg:s.backgroundImage,shadow:s.textShadow,
                wraps:el.querySelectorAll('[data-mcel-tng-gold-text]').length};})''')
            assert data,data
            assert all(all(p.strip()=='none' for p in x['bg'].split(',')) and x['shadow']=='none' and x['wraps']==0 for x in data),data
        finally:
            browser.close()
