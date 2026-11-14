"""Patch 3: named Theme authority shared between Lab and standalone canonical MCEL."""
from pathlib import Path
import json
import re

from playwright.sync_api import sync_playwright
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome
from main_computer.mcel_calculator_expanded_host import render_expanded_page

ROOT=Path(__file__).resolve().parents[1]
WEB=ROOT/'main_computer/web/applications'
SCRIPTS=WEB/'scripts'
THEMES=['theme-machine','theme-local','theme-saas','theme-editorial',
        'theme-luxury','theme-civic','theme-accessible','theme-debug']
TOKENS=['--mcel-theme-'+name for name in
        ('card','ink','muted','accent','accent-2','line','error','warning','success')]


def source_with_one_semantic_evaluate():
    page=render_expanded_page(ROOT,promoted=True)
    match=re.search(r'(<script id="mcel-expanded-ir" type="application/json">)(.*?)(</script>)',page,re.S)
    assert match
    ir=json.loads(match.group(2))
    form=next(n for n in ir['surfaces'][0]['nodes'] if n['id'].endswith('arithmetic-expression'))
    form['color']={'role':'action.generic','surface':'control','context':'form','consequence':'routine'}
    return page[:match.start(2)]+json.dumps(ir,separators=(',',':')).replace('<','\\u003c')+page[match.end(2):]


def no_storage(page):
    page.evaluate('''() => {for(const key of ['localStorage','sessionStorage']){
      const m=new Map();Object.defineProperty(window,key,{configurable:true,value:{
        getItem:k=>m.get(k)||null,setItem:(k,v)=>m.set(k,String(v)),
        removeItem:k=>m.delete(k),clear:()=>m.clear()}});}}''')


def create_page(browser,html,lab):
    page=browser.new_page(viewport={'width':960,'height':850},device_scale_factor=.75)
    no_storage(page)
    page.set_content(html)
    assert page.locator('#mcel-expanded-diagnostic').inner_text()=='Semantic Calculator ready'
    if lab:
        page.add_style_tag(path=str(WEB/'styles/mcel-lab.css'))
        for file in ('mcel-semantic-presentation-bridge.js','mcel-semantic-theme-preview.js'):
            page.add_script_tag(path=str(SCRIPTS/file))
    return page


def capture(page):
    return page.evaluate('''() => {
      const el=document.querySelector('#mcel-expanded-root [data-mcel-color-role="action.generic"]');
      if(!el)return null;
      const s=getComputedStyle(el),r=el.getBoundingClientRect();
      return {background:s.backgroundColor,color:s.color,border:s.borderColor,
        dimensions:[r.width,r.height],identity:el.getAttribute('data-mcel-color-state')};
    }''')


def test_canonical_theme_palette_is_complete_matches_existing_lab_preset_css():
    # The existing legacy site preview keeps its styling in mcel-lab.css;
    # before migration the nine core semantic tokens must agree exactly.
    css=(WEB/'styles/mcel-lab.css').read_text()
    js=(SCRIPTS/'mcel-style-law.js').read_text()
    for theme in THEMES:
        match=re.search(r'\.mcel-runtime-preview\.'+theme+r'\s*\{(.*?)\n\}',css,re.S)
        assert match,theme
        for token in TOKENS:
            value=re.search(re.escape(token)+r':\s*([^;]+);',match.group(1))
            assert value and json.dumps(value.group(1).strip()) in js,(theme,token)


def test_existing_lab_theme_css_values_match_canonical_palette_registry_in_browser():
    with sync_playwright() as p:
        browser=_chrome(p,None)
        page=browser.new_page()
        page.set_content('<div id="swatch" class="mcel-runtime-preview theme-machine"></div>')
        page.add_style_tag(path=str(WEB/'styles/mcel-lab.css'))
        for file in ('mcel-contract.js','mcel-law-registry.js','mcel-style-law.js'):
            page.add_script_tag(path=str(SCRIPTS/file))
        observed=page.evaluate('''themes => {
          const el=document.getElementById('swatch'),law=McelLabStyleLaw;
          return themes.map(theme=>{
            el.className='mcel-runtime-preview '+theme;
            const css=law.semanticPaletteFor(el,getComputedStyle);
            return {theme,css,authority:law.themePalette(theme)};
          });
        }''',THEMES)
        for row in observed:
            assert row['css']==row['authority'],row
        browser.close()


def test_default_and_all_eight_named_themes_have_identical_semantic_colors_in_both_hosts():
    html=source_with_one_semantic_evaluate()
    with sync_playwright() as p:
        browser=_chrome(p,None)
        standalone=create_page(browser,html,False)
        lab=create_page(browser,html,True)
        assert standalone.evaluate("McelLabStyleLaw.canonicalColorTheme('default')")=='theme-machine'
        assert standalone.evaluate("McelLabStyleLaw.themePalette('default')") == standalone.evaluate("McelLabStyleLaw.themePalette('theme-machine')")
        assert standalone.evaluate("McelLabStyleLaw.themePalette('undefined-theme')") is None
        assert standalone.evaluate('''() => ({presentation:window.__mcelExpanded.app.colorPresentation(),
          styleCount:document.querySelectorAll('style[data-mcel-semantic-color-law]').length})''')['styleCount']==1
        original_standalone=standalone.locator('[data-mcel-color-state]')
        original_lab=lab.locator('[data-mcel-color-state]')
        original_standalone_id=original_standalone.get_attribute('data-mcel-color-state')
        original_lab_id=original_lab.get_attribute('data-mcel-color-state')
        assert original_standalone_id and original_lab_id
        for theme in THEMES:
            report=standalone.evaluate('''theme=>window.__mcelExpanded.app.setColorTheme(theme)''',theme)
            assert report['status']=='applied',(theme,report)
            preview=lab.evaluate('''theme => {
              const root=document.getElementById('mcel-expanded-root');
              const ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
              return McelSemanticThemePreview.apply({root,ir,bindingModel:'operational-runtime',
                bridge:McelSemanticPresentationBridge,styleLaw:McelLabStyleLaw,
                theme,tokens:McelLabStyleLaw.themePalette(theme)});
            }''',theme)
            assert preview['status']=='applied-in-isolated-lab-frame',(theme,preview)
            assert preview['canonicalConsumer']=='applied',(theme,preview)
            a,b=capture(standalone),capture(lab)
            assert a and b and (a['background'],a['color'],a['border'])==(b['background'],b['color'],b['border']),(theme,a,b)
            assert a['identity']==original_standalone_id and b['identity']==original_lab_id
            assert a['dimensions']==b['dimensions'],(theme,a,b)
            original_standalone.hover(); a_hover=capture(standalone)
            original_lab.hover(); b_hover=capture(lab)
            assert (a_hover['background'],a_hover['color'],a_hover['border'])==(
                b_hover['background'],b_hover['color'],b_hover['border']),(theme,a_hover,b_hover)
            standalone.mouse.move(1,1);lab.mouse.move(1,1)
            assert capture(standalone)['background']==a['background']
            assert capture(lab)['background']==b['background']
        assert standalone.locator('[data-mcel-color-state]').count()==1
        assert lab.locator('[data-mcel-color-state]').count()==1
        browser.close()


def test_named_theme_refusal_does_not_change_active_calculator_presentation():
    with sync_playwright() as p:
        browser=_chrome(p,None)
        page=create_page(browser,source_with_one_semantic_evaluate(),False)
        before=capture(page)
        invalid=page.evaluate('''() => ({unknown:window.__mcelExpanded.app.setColorTheme('theme-not-real'),
          palette:window.__mcelExpanded.app.setColorTheme('theme-civic',{'--mcel-theme-ink':'#ff0000'})})''')
        assert invalid['unknown']['status']=='unresolved' and invalid['unknown']['reason']=='unknown-theme',invalid
        assert invalid['palette']['status']=='unresolved' and invalid['palette']['reason']=='noncanonical-theme-palette',invalid
        assert capture(page)==before,(before,capture(page))
        browser.close()


def test_unmigrated_calculator_has_no_semantic_color_binding_or_theme_styles():
    with sync_playwright() as p:
        browser=_chrome(p,None)
        page=create_page(browser,render_expanded_page(ROOT,promoted=True),False)
        report=page.evaluate('''() => ({original:window.__mcelExpanded.app.colorPresentation(),
          changed:window.__mcelExpanded.app.setColorTheme('theme-civic'),
          binds:document.querySelectorAll('#mcel-expanded-root [data-mcel-color-state]').length,
          styles:document.querySelectorAll('style[data-mcel-semantic-color-law]').length})''')
        assert report['original']['status']==report['changed']['status']=='legacy-unmigrated',report
        assert report['binds']==report['styles']==0,report
        browser.close()
