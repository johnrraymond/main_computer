"""Navigation containers remain noninteractive under MCEL Semantic Color Law.

Regression for MODE region and mode-tabs group highlighting when a user
hovers/clicks empty space or selects a child navigation item.
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

from main_computer.mcel_calculator_expanded_host import compile_expanded, render_expanded_page
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

ROOT = Path(__file__).resolve().parents[1]


def test_color_law_refuses_interactive_region_and_compiles_static_group():
    with sync_playwright() as p:
        browser = _chrome(p, None)
        page = browser.new_page()
        page.set_content('<html><body></body></html>')
        page.add_script_tag(path=str(ROOT/'main_computer/web/applications/scripts/mcel-contract.js'))
        page.add_script_tag(path=str(ROOT/'main_computer/web/applications/scripts/mcel-law-registry.js'))
        page.add_script_tag(path=str(ROOT/'main_computer/web/applications/scripts/mcel-style-law.js'))
        result = page.evaluate('''() => {
          const law=McelLabStyleLaw, palette=law.themePaletteFor('default');
          const group={role:'navigation.group',surface:'navigation',context:'navigation'};
          const item={role:'navigation.item',surface:'navigation',context:'navigation'};
          const panel={role:'surface.panel',surface:'navigation',context:'navigation'};
          const compiled={
            group:law.compileDeclaredColor({declaration:group,kind:'region',palette,id:'group-test'}),
            panel:law.compileDeclaredColor({declaration:panel,kind:'region',palette,id:'panel-test'}),
            item:law.compileDeclaredColor({declaration:item,kind:'control',palette,id:'item-test'})
          };
          return {
            regionMisuse:law.validateColorDeclaration(item,'region').reason,
            groupSelection:law.validateColorDeclaration({...group,selection:'on'},'region').reason,
            statuses:Object.fromEntries(Object.entries(compiled).map(([k,v])=>[k,v.status])),
            phases:Object.fromEntries(Object.entries(compiled).map(([k,v])=>[k,v.phases])),
            css:Object.fromEntries(Object.entries(compiled).map(([k,v])=>[k,v.css])),
            hoverNoChange:law.resolveColorTreatment({...group,interaction:{hovered:true}}).channels.background===
              law.resolveColorTreatment(group).channels.background
          };
        }''')
        browser.close()
    assert result['regionMisuse'] == 'interactive-role-on-region',result
    assert result['groupSelection'] == 'selection-on-navigation-group',result
    assert all(s == 'resolved' for s in result['statuses'].values()),result
    assert result['phases']['group'] == ['rest'] and result['phases']['panel'] == ['rest'],result
    assert ':hover' not in result['css']['group'] and ':active' not in result['css']['group'],result
    assert ':hover' not in result['css']['panel'] and ':active' not in result['css']['panel'],result
    assert ':hover' in result['css']['item'] and ':active' in result['css']['item'],result
    assert result['hoverNoChange'],result


def test_calculator_mode_region_and_group_stay_static_while_items_interact():
    ir=compile_expanded(ROOT)
    mode_region=next(x for x in ir['surfaces'][0]['interfaceIntent']['regions'] if x['id'].endswith('.mode'))
    assert mode_region['purpose']=='mode-control' and 'color' not in mode_region
    with sync_playwright() as p:
        browser=_chrome(p,None)
        page=browser.new_page(viewport={'width':1000,'height':800},device_scale_factor=.75)
        page.evaluate('''() => {for(const name of ['localStorage','sessionStorage']){
          const data=new Map();Object.defineProperty(window,name,{configurable:true,value:{
            getItem:k=>data.get(k)||null,setItem:(k,v)=>data.set(k,String(v)),
            removeItem:k=>data.delete(k),clear:()=>data.clear()}});}}''')
        page.set_content(render_expanded_page(ROOT,promoted=True))
        assert page.locator('#mcel-expanded-diagnostic').inner_text()=='Semantic Calculator ready'
        mode=page.locator('[data-mcel-region$=".mode"]')
        group=page.locator('[data-mcel-mode-tabs]')
        basic=group.get_by_role('button',name='Basic',exact=True)
        graph=group.get_by_role('button',name='Graphing',exact=True)
        role=page.evaluate('''() => {
          const root=document.querySelector('#mcel-expanded-root');
          const mode=root.querySelector('[data-mcel-region$=".mode"]');
          const group=root.querySelector('[data-mcel-mode-tabs]');
          const holder=group.closest('[data-mcel-node]');
          const select=holder.querySelector('[data-intent]');
          return {mode:mode.getAttribute('data-mcel-color-role'),
            group:group.getAttribute('data-mcel-color-role'),
            select:select.getAttribute('data-mcel-color-role'),
            graph:group.querySelector('button[data-value="graphing"]').getAttribute('data-mcel-color-role'),
            mounted:window.__mcelExpanded.app.colorPresentation()};
        }''')
        assert [role[k] for k in ('mode','group','select','graph')] == [
          'surface.panel','navigation.group','navigation.item','navigation.item'],role
        assert role['mounted']['status']=='applied' and role['mounted']['coverage']=='source-complete',role

        def colors():
            return page.evaluate('''() => {
              const root=document.querySelector('#mcel-expanded-root');
              const mode=root.querySelector('[data-mcel-region$=".mode"]');
              const group=root.querySelector('[data-mcel-mode-tabs]');
              const graph=group.querySelector('button[data-value="graphing"]');
              const get=el=>{const style=getComputedStyle(el);
                return [style.backgroundColor,style.borderColor]};
              return {mode:get(mode),group:get(group),graph:get(graph),
                groupHovered:group.matches(':hover'),modeHovered:mode.matches(':hover'),
                graphSelected:graph.getAttribute('aria-pressed')};
            }''')
        page.mouse.move(0,0)
        resting=colors()
        graph.hover()
        hovering=colors()
        assert hovering['modeHovered'] and hovering['groupHovered'],hovering
        assert resting['mode']==hovering['mode'],(resting,hovering)
        assert resting['group']==hovering['group'],(resting,hovering)
        assert hovering['graph']!=resting['graph'],(resting,hovering)

        # Dispatch a real click on the non-action group rather than child tabs.
        group.evaluate('el=>el.click()')
        page.wait_for_timeout(50)
        empty_click=colors()
        assert empty_click['mode']==resting['mode'] and empty_click['group']==resting['group'],empty_click
        assert empty_click['graphSelected']=='false',empty_click

        # Actual pointer interaction with empty MODE-region space must be
        # equally inert, including while :hover matches the region.
        box=mode.bounding_box()
        x,y=box['x']+box['width']-5,box['y']+6
        target=page.evaluate('''({x,y}) => document.elementFromPoint(x,y).closest('button,[data-mcel-mode-tabs]')?.tagName || null''',{'x':x,'y':y})
        assert target is None, (box,target)
        page.mouse.click(x,y)
        assert colors()['mode']==resting['mode'] and colors()['group']==resting['group']
        assert colors()['graphSelected']=='false'

        graph.click()
        page.wait_for_function('''() => document.querySelector('[data-mcel-mode-tabs] button[data-value="graphing"]').getAttribute('aria-pressed')==='true' ''')
        selected=colors()
        assert selected['mode']==resting['mode'] and selected['group']==resting['group'],(resting,selected)
        assert selected['graphSelected']=='true',selected
        # True navigation still switches views and remains theme-bound.
        assert page.locator('[data-mcel-node$="graph-expression"]').is_visible()
        assert selected['graph']!=resting['graph'],(resting,selected)
        basic.click()
        assert page.locator('[data-mcel-node$="arithmetic-expression"]').is_visible()
        assert colors()['group']==resting['group']

        # MCEL Lab's isolated preview delegates fully semantic source to the
        # SAME canonical resolver, never a second interactive color projector.
        web=ROOT/'main_computer/web/applications'
        page.add_script_tag(path=str(web/'scripts/mcel-semantic-presentation-bridge.js'))
        page.add_script_tag(path=str(web/'scripts/mcel-semantic-theme-preview.js'))
        lab=page.evaluate('''ir => McelSemanticThemePreview.apply({
          root:document.getElementById('mcel-expanded-root'),ir,
          bridge:McelSemanticPresentationBridge,styleLaw:McelLabStyleLaw,
          theme:'theme-civic',tokens:McelLabStyleLaw.themePaletteFor('theme-civic'),
          bindingModel:'operational-runtime'
        })''',ir)
        assert lab['status']=='applied-in-isolated-lab-frame',lab
        page.mouse.move(0,0)
        themed_rest=colors()
        graph.hover()
        themed_hover=colors()
        assert themed_rest['mode']==themed_hover['mode']
        assert themed_rest['group']==themed_hover['group']
        assert themed_rest['graph']!=themed_hover['graph']
        graph.click()
        assert colors()['mode']==themed_rest['mode']
        assert colors()['group']==themed_rest['group']
        browser.close()
