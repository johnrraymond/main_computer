"""Minimal real consumer: one source-bound Evaluate button, native rest → hover → rest."""
from pathlib import Path
import shutil
import json
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'main_computer/web/applications'


def _legacy_first_consumer_page():
    from main_computer.mcel_calculator_expanded_host import render_expanded_page
    page=render_expanded_page(ROOT,promoted=True)
    match=re.search(r'(<script id="mcel-expanded-ir" type="application/json">)(.*?)(</script>)',page,re.S)
    assert match
    ir=json.loads(match.group(2))
    # Preserve the original one-control migration as an isolated legacy
    # compatibility acceptance case. The real Calculator is now fully
    # source-declared and colored by its canonical renderer.
    for node in ir['surfaces'][0]['nodes']:node.pop('color',None)
    for region in ir['surfaces'][0]['interfaceIntent']['regions']:region.pop('color',None)
    return page[:match.start(2)]+json.dumps(ir,separators=(',',':')).replace('<','\\u003c')+page[match.end(2):]


def test_evaluate_is_only_migrated_control_and_click_still_calculates():
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import sync_playwright
    from main_computer.mcel_calculator_expanded_host import render_expanded_page

    with sync_playwright() as playwright:
        # The checked-in acceptance suite must work on a developer's Windows
        # Playwright installation and in CI with system Chromium.
        system = shutil.which('chromium') or shutil.which('chromium-browser')
        kwargs = {'headless': True, 'args': ['--no-sandbox']}
        if system:
            kwargs['executable_path'] = system
        browser = playwright.chromium.launch(**kwargs)
        page = browser.new_page(viewport={'width':950,'height':850}, device_scale_factor=.75)
        page.evaluate('''() => {for(const name of ['localStorage','sessionStorage']){
          const entries=new Map();Object.defineProperty(window,name,{configurable:true,value:{
            getItem:k=>entries.get(k)||null,setItem:(k,v)=>entries.set(k,String(v)),
            removeItem:k=>entries.delete(k),clear:()=>entries.clear()}});}}''')
        page.set_content(_legacy_first_consumer_page())
        assert page.locator('#mcel-expanded-diagnostic').inner_text() == 'Semantic Calculator ready'
        page.add_style_tag(path=str(WEB / 'styles/mcel-lab.css'))
        for script in ('mcel-contract.js','mcel-law-registry.js','mcel-style-law.js',
                       'mcel-semantic-presentation-bridge.js','mcel-semantic-theme-preview.js'):
            page.add_script_tag(path=str(WEB / 'scripts' / script))
        initial = page.evaluate('''() => {
          const b=document.querySelector('[data-mcel-node$="arithmetic-expression"] form button[type="submit"]');
          const rect=b.getBoundingClientRect();
          return {geometry:[rect.x,rect.y,rect.width,rect.height],
            marked:!!b.getAttribute('data-mcel-color-state'),
            ewg:[...document.querySelectorAll('.mcel-diagnostics-counter [data-mcel-color-role]')]
              .map(e=>e.getAttribute('data-mcel-color-role'))};
        }''')
        assert initial['marked'] is False
        assert initial['ewg'] == ['diagnostic.error','diagnostic.warning','diagnostic.success']
        installed = page.evaluate('''() => {
          const root=document.getElementById('mcel-expanded-root');
          const ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
          const law=McelLabStyleLaw,swatch=document.createElement('div');
          swatch.className='mcel-runtime-preview theme-machine';document.body.append(swatch);
          const palette=law.semanticPaletteFor(swatch,getComputedStyle);
          const report=McelSemanticThemePreview.apply({root,ir,bridge:McelSemanticPresentationBridge,
            styleLaw:law,bindingModel:'operational-runtime',theme:'theme-machine',tokens:palette});
          window.__evalPalette=palette;
          const b=root.querySelector('[data-mcel-color-state="evaluate-form-action"]');
          const convert=color=>{const el=document.createElement('span');document.body.append(el);
            el.style.backgroundColor=color;const result=getComputedStyle(el).backgroundColor;el.remove();return result;};
          const options={theme:'theme-machine',role:'action.generic',surface:'control',context:'form',palette};
          const rest=law.resolveColorTreatment(options),hover=law.resolveColorTreatment({...options,interaction:{hovered:true}});
          return {report:report.evaluateColor, exact:b?.closest('form')?.getAttribute('data-intent'),
            marked:root.querySelectorAll('[data-mcel-color-state]').length,
            padMarked:root.querySelector('[data-pad-action="submit"]').hasAttribute('data-mcel-color-state'),
            rest:convert(rest.channels.background),hover:convert(hover.channels.background),
            css:document.getElementById('mcel-lab-semantic-theme-color-only').textContent,
            geometry:b && (()=>{const r=b.getBoundingClientRect();return [r.x,r.y,r.width,r.height];})()};
        }''')
        assert installed['report']['status']=='resolved', installed
        assert installed['report']['mechanism']=='native-css', installed
        assert installed['report']['intent']=='intent:evaluate', installed
        assert installed['exact']=='intent:evaluate' and installed['marked']==1 and not installed['padMarked'],installed
        assert installed['geometry']==initial['geometry'], installed
        assert installed['rest'] != installed['hover'], installed
        assert 'data-mcel-color-state="evaluate-form-action"]:hover' in installed['css'],installed

        button = page.locator('[data-mcel-color-state="evaluate-form-action"]')
        def measure():
            return button.evaluate('''el=>{const r=el.getBoundingClientRect();return {
              background:getComputedStyle(el).backgroundColor,geometry:[r.x,r.y,r.width,r.height],
              same:el===document.querySelector('[data-mcel-node$="arithmetic-expression"] form button[type="submit"]')};}''')
        normal=measure()
        button.hover()
        hover=measure()
        page.mouse.move(0,0)
        restored=measure()
        assert normal['background']==installed['rest'],(normal,installed)
        assert hover['background']==installed['hover'],(hover,installed)
        assert restored['background']==normal['background'],(restored,normal)
        assert normal['geometry']==hover['geometry']==restored['geometry'], (normal,hover,restored)
        assert normal['same'] and hover['same'] and restored['same']

        page.locator('[data-mcel-node$="arithmetic-expression"] input').fill('2+3')
        button.click()
        page.wait_for_function('''() => document.querySelector('[data-mcel-node$="calculated-value"]')?.textContent?.trim()==='5' ''')
        assert page.locator('[data-mcel-node$="calculated-value"]').inner_text().strip() == '5'
        assert page.locator('#mcel-expanded-diagnostic').inner_text() == 'Semantic Calculator ready'

        # The same canonical control changes treatment under a second Theme,
        # without replacing the node, recoloring another control, or remounting.
        second = page.evaluate('''() => {
          const root=document.getElementById('mcel-expanded-root');
          const button=root.querySelector('[data-mcel-color-state="evaluate-form-action"]');
          const other=root.querySelector('[data-pad-action="submit"]');
          const before=button;
          const law=McelLabStyleLaw,swatch=document.querySelector('.mcel-runtime-preview.theme-machine');
          swatch.className='mcel-runtime-preview theme-accessible';
          const tokens=law.semanticPaletteFor(swatch,getComputedStyle);
          const report=McelSemanticThemePreview.apply({root,
            ir:JSON.parse(document.getElementById('mcel-expanded-ir').textContent),
            bridge:McelSemanticPresentationBridge,styleLaw:law,
            bindingModel:'operational-runtime',theme:'theme-accessible',tokens});
          return {status:report.evaluateColor.status,
            same:before===root.querySelector('[data-mcel-color-state="evaluate-form-action"]'),
            one:root.querySelectorAll('[data-mcel-color-state]').length===1,
            padUnchanged:!other.hasAttribute('data-mcel-color-state'),
            ewgs:[...root.querySelectorAll('.mcel-diagnostics-counter [data-mcel-color-role]')]
              .map(el=>el.getAttribute('data-mcel-color-role'))};
        }''')
        assert second == {'status':'resolved','same':True,'one':True,'padUnchanged':True,
                          'ewgs':['diagnostic.error','diagnostic.warning','diagnostic.success']},second
        page.mouse.move(0,0)
        alternate_normal=measure()
        button.hover()
        alternate_hover=measure()
        page.mouse.move(0,0)
        assert alternate_normal['background'] != normal['background'],(alternate_normal,normal)
        assert alternate_hover['background'] != alternate_normal['background'],(alternate_hover,alternate_normal)
        assert measure()['background'] == alternate_normal['background']
        assert alternate_normal['geometry'] == normal['geometry']
        browser.close()


def test_important_semantic_compiler_is_opt_in_and_invalid_priority_is_refused():
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        system=shutil.which('chromium') or shutil.which('chromium-browser')
        kwargs={'headless':True,'args':['--no-sandbox']}
        if system: kwargs['executable_path']=system
        b=p.chromium.launch(**kwargs)
        page=b.new_page()
        page.set_content('<div id="palette" class="mcel-runtime-preview theme-machine"></div>')
        page.add_style_tag(path=str(WEB/'styles/mcel-lab.css'))
        for script in ('mcel-contract.js','mcel-law-registry.js','mcel-style-law.js'):
            page.add_script_tag(path=str(WEB/'scripts'/script))
        cases=page.evaluate('''() => {
          const law=McelLabStyleLaw,palette=law.semanticPaletteFor(document.getElementById('palette'),getComputedStyle);
          const input={id:'evaluate-form-action',theme:'theme-machine',role:'action.generic',surface:'control',palette};
          return {original:law.compileColorStateCss(input),
            elevated:law.compileColorStateCss({...input,important:true}),
            invalid:law.compileColorStateCss({...input,important:'yes'})};
        }''')
        b.close()
    assert cases['original']['status']=='resolved' and '!important' not in cases['original']['css']
    assert cases['elevated']['status']=='resolved' and 'color:' in cases['elevated']['css']
    assert '!important' in cases['elevated']['css']
    assert cases['invalid']['reason']=='invalid-cascade-priority'
