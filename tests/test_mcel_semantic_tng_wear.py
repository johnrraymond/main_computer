"""Accepted TNG wear twiddle promoted to Chrome, without taking Color/Form authority."""
from pathlib import Path

from playwright.sync_api import sync_playwright

from main_computer.mcel_calculator_expanded_host import render_expanded_page
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'main_computer/web/applications'
THEMES = ('theme-machine', 'theme-local', 'theme-saas', 'theme-editorial',
          'theme-luxury', 'theme-civic', 'theme-accessible', 'theme-debug')
SURFACE = '[data-mcel-tng-wear-surface]'


def _page(browser):
    page = browser.new_page(viewport={'width': 1440, 'height': 850}, device_scale_factor=.75)
    page.evaluate('''() => {for (const name of ['localStorage','sessionStorage']) {
      const values=new Map();Object.defineProperty(window,name,{configurable:true,value:{
        getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,String(v)),
        removeItem:k=>values.delete(k),clear:()=>values.clear()}});}}''')
    page.set_content(render_expanded_page(ROOT, promoted=True))
    assert page.locator('#mcel-expanded-diagnostic').inner_text() == 'Semantic Calculator ready'
    for name in ('mcel-lab.css',):
        page.add_style_tag(path=str(WEB / 'styles' / name))
    for name in ('mcel-semantic-presentation-bridge.js', 'mcel-semantic-theme-preview.js',
                 'mcel-semantic-chrome-presentation-law.js'):
        page.add_script_tag(path=str(WEB / 'scripts' / name))
    return page


SETUP = '''theme => {
  const root=document.getElementById('mcel-expanded-root'),
    ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent),
    bridge=McelSemanticPresentationBridge,styleLaw=McelLabStyleLaw;
  const unit=root.querySelector('[data-mcel-working-unit][data-mcel-sizing="intrinsic"]');
  const buttons=[...root.querySelectorAll('[data-mcel-working-unit] button, [data-mcel-mode-tabs] button')];
  const rect=el=>{const r=el.getBoundingClientRect();return [r.x,r.y,r.width,r.height]};
  const original=buttons.map(el=>({rect:rect(el),color:getComputedStyle(el).color,
    bg:getComputedStyle(el).backgroundColor,border:getComputedStyle(el).borderColor,
    nativeChildren:el.children.length,position:el.style.getPropertyValue('position')}));
  const projected=McelSemanticThemePreview.apply({root,ir,bridge,styleLaw,
    bindingModel:'operational-runtime',theme,tokens:styleLaw.themePaletteFor(theme)});
  const before=buttons.map(el=>({rect:rect(el),color:getComputedStyle(el).color,
    bg:getComputedStyle(el).backgroundColor,border:getComputedStyle(el).borderColor}));
  const response=McelSemanticChromePresentationLaw.apply({root,ir,bridge,styleLaw,
    theme,profile:'chrome-tng-side-rails'});
  window.__wearFixture={root,ir,bridge,styleLaw,unit,buttons};
  const after=buttons.map(el=>({rect:rect(el),color:getComputedStyle(el).color,
    bg:getComputedStyle(el).backgroundColor,border:getComputedStyle(el).borderColor,
    nativeChildren:original[buttons.indexOf(el)].nativeChildren,
    surface:el.querySelector(':scope > [data-mcel-tng-wear-surface="button"]')!==null}));
  const rails=[...unit.querySelectorAll('[data-mcel-chrome-generated="true"]')];
  const railWear=rails.map(rail=>{
    const surface=rail.querySelector(':scope > [data-mcel-tng-wear-surface="rail"]');
    const glint=surface?.querySelector(':scope > [data-mcel-tng-wear-glint]');
    return {surface:!!surface, glint:!!glint, pointer:surface&&getComputedStyle(surface).pointerEvents,
      mask:surface&&getComputedStyle(surface).maskImage,
      texture:surface&&getComputedStyle(surface).backgroundImage,
      reflection:glint&&getComputedStyle(glint).backgroundImage,
      lightAngle:glint&&getComputedStyle(glint).maskImage,
      aria:surface?.getAttribute('aria-hidden'), inert:surface?.hasAttribute('inert')};
  });
  return {response,projected:projected.status,before,after,railWear,
    count:root.querySelectorAll('[data-mcel-tng-wear-surface]').length,
    buttons:buttons.length,rails:rails.length};
}'''


def test_wear_preserves_semantic_colors_and_layout_across_themes():
    with sync_playwright() as pw:
        browser=_chrome(pw,None)
        try:
            page=_page(browser)
            for theme in THEMES:
                r=page.evaluate('('+SETUP+')',theme)
                assert r['response']['status']=='applied',(theme,r)
                assert r['projected']=='applied-in-isolated-lab-frame',(theme,r)
                assert r['rails']==2,(theme,r)
                for before,after in zip(r['before'],r['after']):
                    assert before['rect'][2:]==after['rect'][2:],(theme,before,after)
                    assert (before['bg'],before['color'],before['border'])==(after['bg'],after['color'],after['border']),(theme,before,after)
                for wear in r['railWear']:
                    assert wear['surface'] and wear['glint'],(theme,wear)
                    assert wear['pointer']=='none' and wear['aria']=='true' and wear['inert'],wear
                    assert 'data:image/webp' in wear['texture'] and 'data:image/webp' in wear['reflection'],wear
                assert not any(x['surface'] for x in r['after']), (theme,r)
        finally:
            browser.close()


def test_static_wear_allows_calculator_and_forced_colors():
    with sync_playwright() as pw:
        browser=_chrome(pw,None)
        try:
            page=_page(browser)
            page.evaluate('('+SETUP+')','theme-machine')
            key=page.locator('[data-mcel-expression-pad] button').first
            check=key.evaluate('''el=>({position:getComputedStyle(el).position,
              shadow:getComputedStyle(el).textShadow,
              image:getComputedStyle(el).backgroundImage,
              overlays:el.querySelectorAll(':scope > [data-mcel-tng-wear-surface]').length})''')
            if check['position']=='static': assert check['overlays']==0,check
            assert check['shadow']=='none' and 'data:image/svg+xml' in check['image'],check
            page.locator('[data-mcel-node$="arithmetic-expression"] input').fill('2+3')
            page.locator('[data-mcel-node$="arithmetic-expression"] button[type="submit"]').click()
            page.wait_for_function("() => document.querySelector('[data-mcel-node$=calculated-value]')?.textContent?.trim()==='5'")
            page.emulate_media(forced_colors='active')
            assert page.locator(SURFACE).count()>=2
            assert page.locator(SURFACE).first.evaluate('el=>getComputedStyle(el).display')=='none'
        finally:
            browser.close()


def test_wear_reconciles_runtime_replacement_and_chrome_switch():
    with sync_playwright() as pw:
        browser=_chrome(pw,None)
        try:
            page=_page(browser)
            page.evaluate('('+SETUP+')','theme-machine')
            r=page.evaluate('''async () => {
              const {root,ir,bridge,styleLaw}=window.__wearFixture;
              const button=root.querySelector('[data-mcel-expression-pad] button');
              const oldPosition=button.style.getPropertyValue('position');
              const copy=button.cloneNode(true);
              copy.querySelectorAll('[data-mcel-tng-wear-surface]').forEach(n=>n.remove());
              button.replaceWith(copy);
              await new Promise(done=>setTimeout(done,100));
              const current=copy.getAttribute('data-mcel-chrome-material');
              const painted=getComputedStyle(copy).backgroundImage;
              const switched=McelSemanticChromePresentationLaw.apply({root,ir,bridge,styleLaw,
                theme:'theme-machine',profile:'chrome-strict-hierarchy'});
              const clean=root.querySelectorAll('[data-mcel-tng-wear-surface]').length;
              const again=McelSemanticChromePresentationLaw.apply({root,ir,bridge,styleLaw,
                theme:'theme-machine',profile:'chrome-tng-side-rails'});
              return {oldPosition,position:copy.style.getPropertyValue('position'),
                current,painted,clean,off:switched.status,on:again.status,
                rails:root.querySelectorAll('[data-mcel-chrome-generated="true"]').length};
            }''')
            assert r['oldPosition']==r['position'],r
            assert r['current']=='raised-control' and 'data:image/svg+xml' in r['painted'],r
            assert r['clean']==0 and r['off']=='unchanged' and r['on']=='applied',r
            assert r['rails']==2,r
        finally:
            browser.close()
