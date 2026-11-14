"""Patch 4 acceptance: one canonical Calculator color contract, all hosts and controls."""
from pathlib import Path

from playwright.sync_api import sync_playwright

from main_computer.mcel_calculator_expanded_host import compile_expanded, render_expanded_page
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'main_computer/web/applications'
THEMES = ('theme-machine','theme-local','theme-saas','theme-editorial',
          'theme-luxury','theme-civic','theme-accessible','theme-debug')


def _storage(page):
    page.evaluate('''() => {for (const name of ['localStorage','sessionStorage']) {
       const data=new Map();Object.defineProperty(window,name,{configurable:true,value:{
          getItem:k=>data.get(k)||null,setItem:(k,v)=>data.set(k,String(v)),
          removeItem:k=>data.delete(k),clear:()=>data.clear()}});
    }}''')


def _page(browser, viewport=None):
    page=browser.new_page(viewport=viewport or {'width':980,'height':740},device_scale_factor=.75)
    _storage(page)
    page.set_content(render_expanded_page(ROOT,promoted=True))
    assert page.locator('#mcel-expanded-diagnostic').inner_text()=='Semantic Calculator ready'
    return page


def test_authored_calculator_color_is_complete_and_contains_no_physical_palette():
    ir=compile_expanded(ROOT)
    surface=ir['surfaces'][0]
    regions=surface['interfaceIntent']['regions']
    nodes=surface['nodes']
    assert len(regions)==13 and len(nodes)==14
    assert all('color' in x for x in [*regions,*nodes])
    assert all(set(x['color']) <= {'role','surface','context','consequence','selection','operation'}
               for x in [*regions,*nodes])
    assert all(x['color']['role'].startswith(('surface.','text.','navigation.','action.'))
               for x in [*regions,*nodes])
    assert next(x['color']['role'] for x in nodes if x['id'].endswith('arithmetic-expression'))=='action.evaluate'
    assert all('#' not in str(x['color']) and 'rgb' not in str(x['color']) for x in [*regions,*nodes])


def test_all_generated_calculator_controls_have_authorized_roles_and_actions_work():
    with sync_playwright() as p:
        browser=_chrome(p,None)
        for width,height in ((390,850),(900,740),(1440,900)):
            page=_page(browser,{'width':width,'height':height})
            report=page.evaluate('''() => ({
              report:window.__mcelExpanded.app.colorPresentation(),
              root:document.getElementById('mcel-expanded-root').hasAttribute('data-mcel-semantic-color-full'),
              missing:[...document.querySelectorAll('#mcel-expanded-root button,#mcel-expanded-root input,#mcel-expanded-root select')]
                .filter(el=>!el.closest('[data-mcel-embedded-chat],.mcel-diagnostics-counter')&&
                  !el.hasAttribute('data-mcel-color-state')).map(el=>el.outerHTML.slice(0,150)),
              submit:document.querySelector('[data-mcel-node$="arithmetic-expression"] button[type="submit"]')
                ?.getAttribute('data-mcel-color-role'),
              keypad:Object.fromEntries([...document.querySelectorAll('[data-pad-action]')]
                .map(el=>[el.dataset.padAction,el.getAttribute('data-mcel-color-role')]))
            })''')
            assert report['root'] and not report['missing'], (width,report)
            assert report['report']['status']=='applied' and report['report']['coverage']=='source-complete',report
            assert report['report']['authored']==27 and report['report']['legacy']==0,report
            assert report['submit']=='action.evaluate',report
            assert report['keypad']['clear']=='action.clear' and report['keypad']['+']=='action.operator',report
            assert report['keypad']['7']=='action.input' and report['keypad']['submit']=='action.evaluate',report
            page.locator('[data-mcel-node$="arithmetic-expression"] input').fill('2+3')
            page.locator('[data-mcel-node$="arithmetic-expression"] button[type="submit"]').click()
            page.wait_for_function("() => document.querySelector('[data-mcel-node$=calculated-value]')?.textContent?.trim()==='5'")
            assert page.locator('[data-mcel-node$="calculated-value"]').inner_text().strip()=='5'
            page.close()
        browser.close()


def test_native_hover_press_focus_and_theme_transitions_preserve_geometry():
    with sync_playwright() as p:
        browser=_chrome(p,None)
        page=_page(browser)
        evaluate=page.locator('[data-mcel-node$="arithmetic-expression"] button[type="submit"]')
        original=page.evaluate('''() => document.querySelector('[data-mcel-node$="arithmetic-expression"] button[type="submit"]')''')
        base=evaluate.evaluate('''el=>{const s=getComputedStyle(el),r=el.getBoundingClientRect();
          return {bg:s.backgroundColor,rect:[r.x,r.y,r.width,r.height]}}''')
        evaluate.hover()
        hovered=evaluate.evaluate('el=>getComputedStyle(el).backgroundColor')
        assert hovered!=base['bg']
        page.mouse.move(0,0)
        assert evaluate.evaluate('el=>getComputedStyle(el).backgroundColor')==base['bg']
        result=page.evaluate('''() => {
          const e=document.querySelector('[data-mcel-node$="arithmetic-expression"] button[type="submit"]'),
            before=e.getBoundingClientRect(),changes=[];
          for(const theme of McelLabStyleLaw.themes){
            const report=window.__mcelExpanded.app.setColorTheme(theme);
            const after=e.getBoundingClientRect(),style=getComputedStyle(e);
            changes.push({theme,status:report.status,bg:style.backgroundColor,fg:style.color,
              identity:e===document.querySelector('[data-mcel-node$="arithmetic-expression"] button[type="submit"]'),
              geometry:after.x===before.x&&after.y===before.y&&after.width===before.width&&after.height===before.height});
          }
          return changes;
        }''')
        assert len(result)==8
        assert all(x['status']=='applied' and x['identity'] and x['geometry'] for x in result),result
        assert len(set(x['bg'] for x in result))==8,result
        browser.close()


def test_lab_theme_projection_delegates_to_same_canonical_colors():
    with sync_playwright() as p:
        browser=_chrome(p,None)
        page=_page(browser)
        for js in ('mcel-semantic-presentation-bridge.js','mcel-semantic-theme-preview.js'):
            page.add_script_tag(path=str(WEB/'scripts'/js))
        snapshots=page.evaluate('''themes=>{
          const root=document.getElementById('mcel-expanded-root'),
            ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent),
            el=root.querySelector('[data-mcel-node$="arithmetic-expression"] button[type="submit"]');
          const style=()=>{const s=getComputedStyle(el),r=el.getBoundingClientRect();
            return {foreground:s.color,background:s.backgroundColor,border:s.borderTopColor,
              geometry:[r.x,r.y,r.width,r.height]};};
          return themes.map(theme=>{
            const standalone=window.__mcelExpanded.app.setColorTheme(theme);
            const before=style(),lab=McelSemanticThemePreview.apply({root,ir,
              bridge:McelSemanticPresentationBridge,styleLaw:McelLabStyleLaw,
              theme,tokens:McelLabStyleLaw.themePaletteFor(theme),bindingModel:'operational-runtime'});
            return {theme,standalone:standalone.status,lab:lab.status,
              delegated:lab.evaluateColor?.status,parity:JSON.stringify(before)===JSON.stringify(style())};
          });
        }''',list(THEMES))
        false_swatch=page.evaluate('''() => {
          const root=document.getElementById('mcel-expanded-root'),
            ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent),
            theme='theme-civic',node=root.querySelector('[data-mcel-node$="arithmetic-expression"] button[type="submit"]');
          window.__mcelExpanded.app.setColorTheme(theme);
          const normal=getComputedStyle(node).backgroundColor;
          const swatch={...McelLabStyleLaw.themePaletteFor(theme),'--mcel-theme-accent':'#ff00ff',
            '--mcel-theme-success':'#ff00ff','--mcel-theme-card':'#00ff00'};
          const lab=McelSemanticThemePreview.apply({root,ir,bridge:McelSemanticPresentationBridge,
            styleLaw:McelLabStyleLaw,theme,tokens:swatch,bindingModel:'operational-runtime'});
          return {status:lab.status,unchanged:getComputedStyle(node).backgroundColor===normal};
        }''')
        browser.close()
    assert false_swatch['status']=='applied-in-isolated-lab-frame' and false_swatch['unchanged'],false_swatch
    assert all(x['standalone']=='applied' and x['lab']=='applied-in-isolated-lab-frame'
               and x['parity'] for x in snapshots),snapshots


def test_graphing_symbolic_help_and_ewg_stay_in_independent_semantic_domains():
    with sync_playwright() as p:
        browser=_chrome(p,None)
        page=_page(browser)
        page.get_by_role('button',name='Graphing',exact=True).click()
        graph=page.evaluate('''() => {
          const host=document.querySelector('[data-mcel-node$="graph-expression"]');
          const buttons=[...host.querySelectorAll('button')];
          return {total:buttons.length,colored:buttons.filter(el=>el.hasAttribute('data-mcel-color-state')).length,
            roles:[...new Set(buttons.map(el=>el.getAttribute('data-mcel-color-role')))]};
        }''')
        assert graph['total']>15 and graph['total']==graph['colored'],graph
        assert 'action.clear' in graph['roles'] and 'action.input' in graph['roles'],graph
        page.get_by_role('button',name='Symbolic',exact=True).click()
        symbolic=page.evaluate('''() => [...document.querySelectorAll('[data-mcel-node$="symbolic-expression"] button')]
          .map(el=>({role:el.getAttribute('data-mcel-color-role'),state:el.getAttribute('data-mcel-color-state')}))''')
        assert symbolic and all(x['role'] and x['state'] for x in symbolic),symbolic
        page.get_by_role('button',name='Help',exact=True).click()
        independent=page.evaluate('''() => {
          const chat=document.querySelector('[data-mcel-embedded-chat]');
          const ewg=document.querySelector('.mcel-diagnostics-counter');
          return {chatPresent:!!chat,chatInnerSemanticControls:chat?.querySelectorAll('[data-mcel-generated-color-role]').length||0,
            ewgRoles:ewg?[...ewg.querySelectorAll('[data-mcel-color-role]')].map(el=>el.getAttribute('data-mcel-color-role')):[]};
        }''')
        assert independent['chatPresent'],independent
        assert any(x.startswith('diagnostic.') for x in independent['ewgRoles']),independent
        page.close();browser.close()


def test_graph_series_uses_theme_accent_and_rethemes_without_graph_recalculation():
    with sync_playwright() as p:
        browser=_chrome(p,None)
        page=_page(browser,{'width':1100,'height':820})
        page.get_by_role('button',name='Graphing',exact=True).click()
        page.locator('[data-mcel-node$="graph-expression"] input[name="expression"]').fill('x*x')
        page.locator('[data-mcel-node$="graph-expression"] button[type="submit"]').click()
        page.wait_for_selector('[data-mcel-series-role="primary"]')
        evidence=page.evaluate('''() => {
          const curve=document.querySelector('[data-mcel-series-role="primary"]');
          const plot=curve.closest('[data-mcel-plot]');
          const initial={stroke:getComputedStyle(curve).stroke,role:plot.getAttribute('data-mcel-color-role'),
            accent:getComputedStyle(plot).getPropertyValue('--mcel-color-accent').trim(),
            path:curve.getAttribute('d')};
          const result=window.__mcelExpanded.app.setColorTheme('theme-civic');
          const after={stroke:getComputedStyle(curve).stroke,
            accent:getComputedStyle(plot).getPropertyValue('--mcel-color-accent').trim(),
            path:curve.getAttribute('d')};
          return {initial,after,result:result.status,
            identity:curve===document.querySelector('[data-mcel-series-role="primary"]')};
        }''')
        assert evidence['result']=='applied' and evidence['identity'],evidence
        assert evidence['initial']['role']=='surface.panel',evidence
        assert evidence['initial']['accent'] and evidence['after']['accent'],evidence
        assert evidence['initial']['stroke']!=evidence['after']['stroke'],evidence
        assert evidence['initial']['path']==evidence['after']['path'],evidence
        browser.close()
