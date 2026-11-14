"""E/W/G is the first semantic Color Law consumer; diagnostics stay operational."""
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'main_computer/web/applications'
CSS = WEB / 'styles/mcel-diagnostics-counter-widget.css'
THEME_CSS = WEB / 'styles/mcel-semantic-color-diagnostics.css'
WIDGET = WEB / 'scripts/mcel-diagnostics-counter-widget.js'


def test_semantic_markup_and_theme_ownership_not_hardcoded_widget_colors():
    widget = WIDGET.read_text()
    style = CSS.read_text()
    theme_style = THEME_CSS.read_text()
    projector = (WEB / 'scripts/mcel-semantic-theme-preview.js').read_text()
    shell = (ROOT / 'main_computer/web/applications.html').read_text()
    host = (ROOT / 'main_computer/mcel_calculator_expanded_host.py').read_text()
    for role in ('diagnostic.error', 'diagnostic.warning', 'diagnostic.success'):
        assert f'data-mcel-color-role="{role}"' in widget
        assert f'[data-mcel-color-role="{role}"]' in theme_style
        assert role in projector
    assert 'data-mcel-color-role' in (WEB / 'apps/code-editor.html').read_text()
    assert 'styleLaw.resolveColor?.' in projector
    assert '--mcel-theme-error' in projector and '--mcel-theme-warning' in projector and '--mcel-theme-success' in projector
    assert 'semanticColorCss+' in projector
    assert shell.index('applications/styles/mcel-semantic-color-diagnostics.css') < shell.index('applications/styles/mcel-diagnostics-counter-widget.css')
    assert host.index('styles/mcel-semantic-color-diagnostics.css') < host.index('styles/mcel-diagnostics-counter-widget.css')
    # No literal foreground / background / status border palettes in the widget.
    assert not re.search(r'#[0-9a-fA-F]{3,8}\b|rgba?\s*\(', style)
    assert not re.search(r'#[0-9a-fA-F]{3,8}\b|rgba?\s*\(', widget)
    assert 'color-mix(' in theme_style and '--mcel-theme-error' in theme_style
    assert 'data-mcel-color-role' in widget and 'data-mcel-color-role' in projector


def test_real_standalone_counter_keeps_palette_geometry_and_semantic_counts():
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch14_7_capability_negotiation_frob import _chrome
    from main_computer.mcel_calculator_expanded_host import render_expanded_page

    with sync_playwright() as p:
        browser = _chrome(p, None)
        for width in (390, 950):
            page = browser.new_page(viewport={'width':width,'height':850}, device_scale_factor=.75)
            page.evaluate('''() => {for(const name of ['localStorage','sessionStorage']) {
              const entries=new Map();Object.defineProperty(window,name,{configurable:true,value:{
                getItem:k=>entries.get(k)||null,setItem:(k,v)=>entries.set(k,String(v)),
                removeItem:k=>entries.delete(k),clear:()=>entries.clear()}});}}''')
            page.set_content(render_expanded_page(ROOT, promoted=True))
            assert page.locator('#mcel-expanded-diagnostic').inner_text() == 'Semantic Calculator ready'
            recorded = page.evaluate('''() => {
              const w=document.querySelector('.mcel-diagnostics-counter');
              if(!w) throw Error('Real Calculator diagnostic widget did not mount');
              const roles=['diagnostic.error','diagnostic.warning','diagnostic.success'];
              const spans=roles.map(role=>w.querySelector(`[data-mcel-color-role="${role}"]`));
              const css=spans.map(el=>el&&getComputedStyle(el).color);
              const rect=w.getBoundingClientRect();
              const root=document.getElementById('mcel-expanded-root');
              const theme=getComputedStyle(root);const probe=document.createElement('span');document.body.appendChild(probe);
              const expected=['--mcel-theme-error','--mcel-theme-warning','--mcel-theme-success'].map(token=>{
                probe.style.color=theme.getPropertyValue(token).trim();return getComputedStyle(probe).color;
              }); probe.remove();
              return {roles:spans.map(el=>el?.dataset.mcelColorRole),colors:css,expected,
                states:w.dataset.state,role:w.getAttribute('data-mcel-color-role'),
                text:w.textContent,values:[w.dataset.errors,w.dataset.warnings,w.dataset.ok],
                bounds:[rect.width,rect.height],aria:w.getAttribute('aria-label'),
                children:[...w.children].length};
            }''')
            assert recorded['roles'] == ['diagnostic.error','diagnostic.warning','diagnostic.success'], (width,recorded)
            assert len(set(recorded['colors'])) == 3, (width,recorded)
            assert recorded['colors'] == recorded['expected'], (width,recorded)
            assert recorded['children'] == 7 and recorded['bounds'][1] == 24, (width,recorded)
            assert recorded['aria'] and 'Click to copy current and historical issues' in recorded['aria'], (width,recorded)
            assert recorded['role'] == {'error':'diagnostic.error','warning':'diagnostic.warning','ok':'diagnostic.success'}[recorded['states']], (width,recorded)
            assert recorded['values'] and all(x.isdigit() for x in recorded['values']), (width,recorded)
            page.close()
        browser.close()


def test_eight_theme_projections_resolve_ewg_roles_via_color_law_not_html_tags():
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch14_7_capability_negotiation_frob import _chrome
    from main_computer.mcel_calculator_expanded_host import render_expanded_page

    with sync_playwright() as p:
        browser = _chrome(p, None)
        page = browser.new_page(viewport={'width':1000,'height':700},device_scale_factor=.75)
        page.evaluate('''() => {for(const name of ['localStorage','sessionStorage']) {
            const entries=new Map();Object.defineProperty(window,name,{configurable:true,value:{
              getItem:k=>entries.get(k)||null,setItem:(k,v)=>entries.set(k,String(v)),
              removeItem:k=>entries.delete(k),clear:()=>entries.clear()}});}}''')
        page.set_content(render_expanded_page(ROOT,promoted=True))
        assert page.locator('#mcel-expanded-diagnostic').inner_text() == 'Semantic Calculator ready'
        page.add_style_tag(path=str(WEB / 'styles/mcel-lab.css'))
        for name in ('mcel-contract.js','mcel-law-registry.js','mcel-style-law.js',
                     'mcel-semantic-presentation-bridge.js','mcel-semantic-theme-preview.js'):
            page.add_script_tag(path=str(WEB / 'scripts' / name))
        results = page.evaluate('''() => {
          const root=document.getElementById('mcel-expanded-root'),
            ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent),
            counter=root.querySelector('.mcel-diagnostics-counter');
          const spans=[...counter.querySelectorAll('[data-mcel-color-role]')];
          const identity=spans.slice(), baseline=counter.textContent;
          const probe=document.createElement('span');document.body.appendChild(probe);
          const swatch=document.createElement('div');swatch.className='mcel-runtime-preview';document.body.appendChild(swatch);
          const rgb=(value)=>{probe.style.color=value;return getComputedStyle(probe).color;};
          const canvas=document.createElement('canvas');canvas.width=canvas.height=1;
          const ctx=canvas.getContext('2d');
          const toRGB=color=>{ctx.clearRect(0,0,1,1);ctx.fillStyle=color;
            ctx.fillRect(0,0,1,1);return [...ctx.getImageData(0,0,1,1).data].slice(0,3);};
          const luminance=color=>toRGB(color).map(c=>{c/=255;
            return c<=0.04045?c/12.92:((c+0.055)/1.055)**2.4;
          }).reduce((sum,v,i)=>sum+v*[0.2126,0.7152,0.0722][i],0);
          const contrast=(a,b)=>{const x=luminance(a),y=luminance(b);
            return (Math.max(x,y)+0.05)/(Math.min(x,y)+0.05);};
          const themeLaw=McelLabStyleLaw,preview=McelSemanticThemePreview;
          const modes=[];
          for (const theme of themeLaw.themes) {
            swatch.className='mcel-runtime-preview '+theme;
            const tokens=preview.themeTokens(swatch,getComputedStyle);
            const outcome=preview.apply({root,ir,bridge:McelSemanticPresentationBridge,
              styleLaw:themeLaw,bindingModel:'operational-runtime',theme,tokens});
            const actual=['diagnostic.error','diagnostic.warning','diagnostic.success'].map(role=>{
              const element=counter.querySelector(`[data-mcel-color-role="${role}"]`);
              const expected=themeLaw.resolveColor({theme,role,state:'enabled',surface:'text',palette:tokens});
              const indicator=themeLaw.resolveColor({theme,role,state:'enabled',surface:'indicator',palette:tokens});
              return {role,actual:getComputedStyle(element).color,expected:rgb(expected.foreground),
                resolved:expected.status==='resolved'&&indicator.status==='resolved'};
            });
            // All three diagnostic meanings must remain readable regardless
            // of which of them is currently the indicator's dominant severity.
            const tested=[];
            for(const state of ['diagnostic.error','diagnostic.warning','diagnostic.success']){
              counter.dataset.mcelColorRole=state;
              const bg=getComputedStyle(counter).backgroundColor;
              tested.push(...actual.map(signal=>({state,role:signal.role,
                ratio:contrast(signal.actual,bg)})));
            }
            const rect=counter.getBoundingClientRect();
            modes.push({theme,status:outcome.status,signals:actual,unique:new Set(actual.map(x=>x.actual)).size,
              textStable:counter.textContent===baseline,nodesStable:identity.every(x=>x.isConnected),
              contrastRatios:tested,minimumContrast:Math.min(...tested.map(x=>x.ratio)),
              computedBackground:getComputedStyle(counter).backgroundColor,
              rect:[rect.x,rect.y,rect.width,rect.height],
              role:counter.getAttribute('data-mcel-color-role')});
          }
          return modes;
        }''')
        browser.close()
    assert len(results) == 8
    for rec in results:
        assert rec['status'] == 'applied-in-isolated-lab-frame',rec
        assert rec['unique'] == 3 and rec['nodesStable'] and rec['textStable'],rec
        assert len(rec['contrastRatios']) == 9 and rec['minimumContrast'] >= 4.5,rec
        assert all(x['resolved'] and x['actual']==x['expected'] for x in rec['signals']),rec
        assert rec['computedBackground'] and rec['rect'][2] >= 74 and rec['rect'][3] == 24,rec
        assert rec['role'] in {'diagnostic.error','diagnostic.warning','diagnostic.success'},rec
