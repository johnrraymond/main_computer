"""Patch 2: generic canonical source→Theme binding inside operational renderer."""
from pathlib import Path
import json
import re

from playwright.sync_api import sync_playwright
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome
from main_computer.mcel_calculator_expanded_host import render_expanded_page

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT/'main_computer/web/applications'


def _page(with_color=False, invalid=False):
    page=render_expanded_page(ROOT,promoted=True).replace('</head>',
        '<script>window.__mcelSemanticColorLabPreview=true;</script></head>')
    if True:
        m=re.search(r'(<script id="mcel-expanded-ir" type="application/json">)(.*?)(</script>)',page,flags=re.S)
        assert m
        ir=json.loads(m.group(2))
        # Legacy v1 acceptance fixture: simulate an authored application with
        # either zero color declarations or precisely one. The actual Calculator
        # is now intentionally fully source-migrated.
        for node in ir['surfaces'][0]['nodes']:node.pop('color',None)
        for region in ir['surfaces'][0]['interfaceIntent']['regions']:region.pop('color',None)
        if with_color:
            form=next(n for n in ir['surfaces'][0]['nodes'] if n['id'].endswith('arithmetic-expression'))
            form['color']={'role':'action.generic' if not invalid else 'action.invalid',
                           'surface':'control','context':'form','consequence':'routine'}
        page=page[:m.start(2)]+json.dumps(ir,separators=(',',':')).replace('<','\\u003c')+page[m.end(2):]
        # This test supplies
        # one real computed Theme palette explicitly; no application color literals.
        css=(WEB/'styles/mcel-lab.css').read_text()
        page=page.replace('</head>','<style>'+css+'</style></head>',1)
        if not with_color:return page
        old="const app=McelSemanticOperationalRuntime.mount(root,ir,{capabilities:port,presentation:'expert'});"
        assert old in page
        new="""const swatch=document.createElement('div');
        swatch.className='mcel-runtime-preview theme-machine';
        swatch.style.display='none'; document.body.append(swatch);
        const palette=McelLabStyleLaw.semanticPaletteFor(swatch,getComputedStyle);
        const app=McelSemanticOperationalRuntime.mount(root,ir,{capabilities:port,presentation:'expert',
          colorTheme:'theme-machine',colorPalette:palette});
        window.__mcelColorSwatch=swatch;"""
        page=page.replace(old,new,1)
    return page


def _storage(page):
    page.evaluate('''() => {for (const name of ['localStorage','sessionStorage']) {
      const values=new Map();Object.defineProperty(window,name,{configurable:true,value:{
        getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,String(v)),
        removeItem:k=>values.delete(k),clear:()=>values.clear()}});
    }}''')


def test_canonical_renderer_infers_all_color_targets_without_authored_color_and_keeps_operations():
    with sync_playwright() as p:
        browser=_chrome(p,None)
        page=browser.new_page(viewport={'width':980,'height':760},device_scale_factor=.75)
        _storage(page)
        page.set_content(_page())
        assert page.locator('#mcel-expanded-diagnostic').inner_text()=='Semantic Calculator ready'
        result=page.evaluate('''() => {
          const app=window.__mcelExpanded.app, root=document.getElementById('mcel-expanded-root');
          const submit=root.querySelector('[data-mcel-node$="arithmetic-expression"] button[type="submit"]');
          const buttons=[...root.querySelectorAll('button')];
          const before=submit.getBoundingClientRect();
          return {report:app.colorPresentation(),native:submit.tagName,
            role:submit.getAttribute('data-mcel-color-role'),colored:root.querySelectorAll('[data-mcel-color-state]').length,
            buttonCoverage:buttons.filter(x=>x.hasAttribute('data-mcel-color-state')).length,
            buttons:buttons.length,geom:[before.x,before.y,before.width,before.height]};
        }''')
        assert result['report']['status']=='applied' and result['report']['authored']==0,result
        assert result['report']['inferred']==27 and result['colored']==111,result
        assert result['native']=='BUTTON' and result['role']=='action.evaluate',result
        assert result['buttonCoverage']==53 and result['buttons']==75,result  # Embedded chat owns its controls
        button=page.locator('[data-mcel-node$="arithmetic-expression"] button[type="submit"]')
        rest=button.evaluate('el=>getComputedStyle(el).backgroundColor')
        button.hover()
        assert button.evaluate('el=>getComputedStyle(el).backgroundColor')!=rest
        page.mouse.move(0,0)
        page.locator('[data-mcel-node$="arithmetic-expression"] input').fill('2+3')
        button.click()
        page.wait_for_function("() => document.querySelector('[data-mcel-node$=calculated-value]')?.textContent?.trim()==='5'")
        alternate=page.evaluate('''() => {
          const app=window.__mcelExpanded.app;
          const e=document.querySelector('[data-mcel-node$="arithmetic-expression"] button[type="submit"]');
          const before=e.getBoundingClientRect();
          const report=app.setColorTheme('theme-civic');
          const after=e.getBoundingClientRect();
          return {status:report.status,bg:getComputedStyle(e).backgroundColor,
            stable:before.x===after.x&&before.y===after.y&&before.width===after.width&&before.height===after.height};
        }''')
        assert alternate['status']=='applied' and alternate['stable'],alternate
        assert alternate['bg']!=rest,alternate
        browser.close()


def test_invalid_authored_color_refuses_before_any_dom_render():
    with sync_playwright() as p:
        browser=_chrome(p,None)
        page=browser.new_page()
        _storage(page)
        page.set_content(_page(with_color=True,invalid=True))
        result=page.evaluate('''() => ({diagnostic:document.getElementById('mcel-expanded-diagnostic').textContent,
          rootCount:document.querySelector('#mcel-expanded-root').childElementCount,
          colorCss:document.querySelectorAll('style[data-mcel-semantic-color-law]').length})''')
        browser.close()
    assert 'semantic-color-authored-color-contract-forbidden' in result['diagnostic'],result
    assert result['rootCount']==0 and result['colorCss']==0,result


def test_non_authored_app_infers_theme_without_any_explicit_color_opt_in():
    with sync_playwright() as p:
        browser=_chrome(p,None)
        page=browser.new_page()
        _storage(page)
        page.set_content(_page())
        result=page.evaluate('''() => ({ready:document.getElementById('mcel-expanded-diagnostic').textContent,
          report:window.__mcelExpanded.app.colorPresentation(),
          bindings:document.querySelectorAll('#mcel-expanded-root [data-mcel-color-state]').length,
          styles:document.querySelectorAll('style[data-mcel-semantic-color-law]').length,
          generatedRoles:document.querySelectorAll('[data-mcel-generated-color-role]').length})''')
        browser.close()
    assert result['ready']=='Semantic Calculator ready',result
    assert result['report']['status']=='applied' and result['report']['source']=='semantic-inference',result
    assert result['report']['authored']==0 and result['bindings']==111 and result['styles']==1,result
    assert result['generatedRoles']==0,result
