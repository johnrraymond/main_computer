"""Form shape requirements are solved by Layout in one deterministic pass."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / 'main_computer/web/applications/scripts/mcel-semantic-layout-inference.js'


def test_closed_form_shape_clearance_and_declared_unsupported_geometry():
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch14_7_capability_negotiation_frob import _chrome
    with sync_playwright() as p:
        browser = _chrome(p, None)
        page = browser.new_page()
        page.set_content('''<!doctype html><main id="root">
          <article id="rounded" style="width:160px; border:2px solid #000;
            border-radius:70px; padding:0; background:#ddd">
            <div id="paint" style="height:35px; background:#222"></div>
          </article>
          <article id="bad" style="width:160px; border-radius:60px; padding:0">
            <div style="height:35px; transform:translateX(-12px); background:#444"></div>
          </article>
        </main>''')
        page.add_script_tag(path=str(ENGINE))
        result = page.evaluate('''() => {
          const E=McelSemanticLayoutInference;
          const root=document.getElementById('root');
          const rounded=document.getElementById('rounded');
          const bad=document.getElementById('bad');
          const child=document.getElementById('paint');
          const radii={tl:{x:80,y:40},tr:{x:80,y:40},bl:{x:80,y:40},br:{x:80,y:40}};
          const insets=E.minimumFormInsets({width:200,height:100,radii,borders:[0,0,0,0]});
          const baseline={padding:getComputedStyle(rounded).paddingTop,
            childX:child.getBoundingClientRect().left,originalHTML:rounded.outerHTML};
          const prior=E.evaluateFormShapeLayout({root,containers:[rounded,bad]});
          const first=E.applyFormShapeLayout({root,containers:[rounded,bad]});
          const shape=()=>{
            const r=rounded.getBoundingClientRect(),c=child.getBoundingClientRect(),s=getComputedStyle(rounded);
            const radius=Math.min(parseFloat(s.borderTopLeftRadius),r.width/2,r.height/2);
            const all=[[c.left,c.top],[c.right,c.top],[c.right,c.bottom],[c.left,c.bottom]];
            const inside=all.every(([x,y])=>{
              const px=x-r.left,py=y-r.top;
              const cx=px<radius?radius:px>r.width-radius?r.width-radius:null;
              const cy=py<radius?radius:py>r.height-radius?r.height-radius:null;
              return cx===null || cy===null || ((px-cx)/radius)**2+((py-cy)/radius)**2<=1.0001;
            });
            return {inset:parseFloat(s.paddingLeft),childX:c.left,inside,
              scrollX:rounded.scrollWidth-rounded.clientWidth};
          };
          const after=shape();
          const again=E.applyFormShapeLayout({root,containers:[rounded,bad]});
          const repeat=shape();
          const cleared=E.clearFormShapeLayout(root);
          return {baseline,priorStatus:prior.status,first,after,again,repeat,cleared,
            reset:getComputedStyle(rounded).paddingLeft,
            childIdentity:child===document.getElementById('paint')};
        }''')
        browser.close()
    assert result['priorStatus'] == 'partial', result
    assert result['first']['status'] == result['again']['status'] == 'partial', result
    assert result['first']['adjusted'] == result['again']['adjusted'] == 1, result
    assert any(x['code'] == 'unsupported-form-content-geometry' for x in result['first']['violations']), result
    assert result['after']['inside'] and result['after']['scrollX'] <= 1, result
    assert result['after'] == result['repeat'], result
    assert result['after']['inset'] > 10, result
    assert result['baseline']['padding'] == result['reset'] == '0px', result
    assert result['cleared'] == 1 and result['childIdentity'], result


def test_zero_radius_has_no_layout_mutation_and_elliptical_radius_is_deterministic():
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch14_7_capability_negotiation_frob import _chrome
    with sync_playwright() as p:
        browser = _chrome(p, None)
        page = browser.new_page()
        page.set_content('''<main id="root"><section id="x" style="width:200px; height:100px;
          box-sizing:border-box; border-radius:60px / 30px; padding:0">
          <div style="height:24px; background:#000"></div></section></main>''')
        page.add_script_tag(path=str(ENGINE))
        result = page.evaluate('''() => {
          const E=McelSemanticLayoutInference;
          const root=document.getElementById('root'),x=document.getElementById('x');
          const first=E.applyFormShapeLayout({root,containers:[x]});
          const a=[...['Top','Right','Bottom','Left']].map(s=>parseFloat(getComputedStyle(x)['padding'+s]));
          E.clearFormShapeLayout(root);
          x.style.borderRadius='0';
          const second=E.applyFormShapeLayout({root,containers:[x]});
          return {first,second,a,reset:getComputedStyle(x).paddingLeft};
        }''')
        browser.close()
    assert result['first']['status'] == 'feasible' and result['first']['adjusted'] == 1, result
    assert result['a'][0] > 0 and result['a'][1] > result['a'][0], result
    assert result['second']['adjusted'] == 0 and result['reset'] == '0px', result


def test_real_calculator_semantic_form_constraints_survive_view_transitions():
    """No Calculator-specific offset; the same solver handles Basic and Graphing."""
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch14_7_capability_negotiation_frob import _chrome
    from main_computer.mcel_calculator_expanded_host import render_expanded_page
    web = ROOT / 'main_computer/web/applications'
    with sync_playwright() as p:
        browser = _chrome(p, None)
        for width, height in ((390, 850), (846, 385), (1440, 850)):
            page = browser.new_page(viewport={'width': width, 'height': height}, device_scale_factor=.75)
            page.evaluate('''() => { for(const name of ['localStorage','sessionStorage']){
              const data=new Map();Object.defineProperty(window,name,{configurable:true,value:{
              getItem:key=>data.get(key)||null,setItem:(key,value)=>data.set(key,String(value)),
              removeItem:key=>data.delete(key),clear:()=>data.clear()}});}}''')
            page.set_content(render_expanded_page(ROOT, promoted=True))
            assert page.locator('#mcel-expanded-diagnostic').inner_text() == 'Semantic Calculator ready'
            page.add_style_tag(path=str(web / 'styles/mcel-lab.css'))
            for name in ('mcel-contract.js','mcel-law-registry.js','mcel-style-law.js',
                         'mcel-semantic-presentation-bridge.js','mcel-semantic-theme-preview.js',
                         'mcel-semantic-form-presentation-law.js'):
                page.add_script_tag(path=str(web / 'scripts' / name))
            initialized = page.evaluate('''() => {
              const root=document.getElementById('mcel-expanded-root');
              const ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
              const bridge=McelSemanticPresentationBridge;
              const palette=document.createElement('div');palette.className='mcel-runtime-preview theme-machine';
              document.body.appendChild(palette);
              McelSemanticThemePreview.apply({root,ir,bridge,styleLaw:McelLabStyleLaw,
                bindingModel:'operational-runtime',theme:'theme-machine',
                tokens:McelSemanticThemePreview.themeTokens(palette,getComputedStyle)});
              window.__mcelTestRoot=root;
              const report=McelSemanticFormPresentationLaw.apply({root,ir,bridge,profile:'form-soft'});
              return {status:report.shapeLayout.status,adjusted:report.shapeLayout.adjusted};
            }''')
            assert initialized['status'] == 'feasible' and initialized['adjusted'] >= 2, (width, initialized)
            page.get_by_role('button', name='Graphing', exact=True).click()
            checked = page.evaluate('''() => {
              const root=window.__mcelTestRoot;
              const expression=root.querySelector('[data-mcel-node$="graph-expression"]');
              const surface=root.querySelector('[data-mcel-node$="arithmetic-expression"]');
              const s=getComputedStyle(expression);
              const r=expression.getBoundingClientRect();
              const child=expression.firstElementChild.getBoundingClientRect();
              const radius=Math.min(parseFloat(s.borderTopLeftRadius),r.width/2,r.height/2);
              // Independent quarter-circle membership check at the painted
              // child's top-left corner; no reuse of the solver's formula.
              const x=child.left-r.left,y=child.top-r.top;
              const inside=(x>=radius||y>=radius)||
                (x-radius)**2+(y-radius)**2<=radius**2+.2;
              const scrollers=[...root.querySelectorAll('[data-mcel-natural-scroller]')];
              const outer=root.querySelector('[data-mcel-natural-layout]');
              return {inside,padding:parseFloat(s.paddingTop),radius,
                outerFrameRadius:parseFloat(getComputedStyle(outer).borderTopLeftRadius),
                semanticIdentity:root.querySelector('[data-mcel-node$="graph-expression"]')===expression,
                scrollOwners:scrollers.map(el=>getComputedStyle(el).overflowY),
                graphVisible:r.width>0 && r.height>0,arithmeticHidden:!surface.getClientRects().length};
            }''')
            assert checked['inside'] and checked['padding'] > 0, (width, checked)
            assert checked['outerFrameRadius'] == 0, (width, checked)
            assert checked['graphVisible'] and checked['arithmeticHidden'] and checked['semanticIdentity'], (width,checked)
            assert all(kind in ('auto', 'scroll') for kind in checked['scrollOwners']), (width,checked)
            page.get_by_role('button', name='Basic', exact=True).click()
            restored = page.evaluate('''() => {
              const root=window.__mcelTestRoot;
              const e=root.querySelector('[data-mcel-node$="arithmetic-expression"]');
              return {visible:e.getBoundingClientRect().height>0,
                padded:parseFloat(getComputedStyle(e).paddingTop)>0,
                profile:root.getAttribute('data-mcel-form-profile')};
            }''')
            assert restored['visible'] and restored['padded'] and restored['profile'] == 'form-soft', (width,restored)
            page.close()
        browser.close()


def test_unsupported_form_geometry_uses_explicit_safe_shape_fallback():
    """An unsupported transform cannot be silently certified as contained."""
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch14_7_capability_negotiation_frob import _chrome
    web = ROOT / 'main_computer/web/applications'
    with sync_playwright() as p:
        browser = _chrome(p, None)
        page = browser.new_page()
        page.set_content('''<div id="root"><section id="frame" style="width:200px;
           border-radius:16px; padding:0; background:#222"><div id="child"
           style="height:30px; background:#333; transform:translateX(-5px)">Result</div>
           </section><button id="action">Run</button></div>''')
        page.add_script_tag(path=str(ENGINE))
        page.add_script_tag(path=str(web / 'scripts/mcel-semantic-form-presentation-law.js'))
        result = page.evaluate('''() => {
          const root=document.getElementById('root'),frame=document.getElementById('frame');
          const child=document.getElementById('child');
          const bridge={observeRuntimePresentationTargets:()=>({status:'source-bound',targets:[
            {kind:'region',element:frame},{kind:'control',element:document.getElementById('action')}]})};
          const run=()=>McelSemanticFormPresentationLaw.apply({root,ir:{},bridge,profile:'form-soft'});
          const refused=run();
          const fallback={status:refused.shapeLayout.status,code:refused.shapeLayout.violations[0]?.code,
            radius:getComputedStyle(frame).borderTopLeftRadius,
            marked:frame.hasAttribute('data-mcel-form-shape-refused')};
          child.style.transform='none';
          const accepted=run();
          return {fallback,accepted:{status:accepted.shapeLayout.status,
            radius:getComputedStyle(frame).borderTopLeftRadius,
            marked:frame.hasAttribute('data-mcel-form-shape-refused'),
            padding:parseFloat(getComputedStyle(frame).paddingTop)}};
        }''')
        browser.close()
    assert result['fallback'] == {'status':'partial',
        'code':'unsupported-form-content-geometry','radius':'0px','marked':True}, result
    assert result['accepted']['status'] == 'feasible', result
    assert result['accepted']['radius'] == '14px' and not result['accepted']['marked'], result
    assert result['accepted']['padding'] > 0, result
