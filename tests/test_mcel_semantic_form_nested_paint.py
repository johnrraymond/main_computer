"""A coextensive semantic paint layer must not square off its rounded peer."""
from pathlib import Path

from playwright.sync_api import sync_playwright

from main_computer.mcel_calculator_expanded_host import render_expanded_page
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'main_computer/web/applications'
SCRIPT = WEB / 'scripts'
THEMES = ('theme-machine', 'theme-local', 'theme-saas', 'theme-editorial',
          'theme-luxury', 'theme-civic', 'theme-accessible', 'theme-debug')


def _open(browser, width=960):
    page = browser.new_page(viewport={'width':width, 'height':850},device_scale_factor=.75)
    page.evaluate('''() => { for (const name of ['localStorage','sessionStorage']) {
      const data=new Map(); Object.defineProperty(window,name,{configurable:true,value:{
        getItem:k=>data.get(k)||null,setItem:(k,v)=>data.set(k,String(v)),
        removeItem:k=>data.delete(k),clear:()=>data.clear()}});}}''')
    page.set_content(render_expanded_page(ROOT,promoted=True))
    assert page.locator('#mcel-expanded-diagnostic').inner_text()=='Semantic Calculator ready'
    for stylesheet in ('mcel-lab.css',):
        page.add_style_tag(path=str(WEB / 'styles' / stylesheet))
    for script in ('mcel-semantic-layout-inference.js','mcel-semantic-presentation-bridge.js',
                   'mcel-semantic-theme-preview.js','mcel-semantic-form-presentation-law.js',
                   'mcel-semantic-chrome-presentation-law.js'):
        page.add_script_tag(path=str(SCRIPT / script))
    return page


PROBE = r'''({theme='theme-machine',profile='form-current',chrome=true,remove=false}) => {
 const root=document.getElementById('mcel-expanded-root');
 const ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
 const bridge=McelSemanticPresentationBridge,styleLaw=McelLabStyleLaw;
 const lookup={};
 const nav=root.querySelector('[data-mcel-natural-scroller="navigation"]');
 const activity=root.querySelector('[data-mcel-activity]');
 const holder=activity.closest('[data-mcel-node]');
 const status=activity.closest('[data-mcel-region]');
 const outer=status.parentElement.closest('[data-mcel-region]');
 const evaluate=root.querySelector('button[type="submit"]');
 const box=el=>{const r=el.getBoundingClientRect();return [r.x,r.y,r.width,r.height];};
 const layout=()=>({outer:box(outer),status:box(status),holder:box(holder),
   activity:box(activity),nav:box(nav),evaluate:box(evaluate)});
 const before=layout();
 const themed=McelSemanticThemePreview.apply({root,ir,bridge,styleLaw,theme,
   bindingModel:'operational-runtime',tokens:styleLaw.themePaletteFor(theme)});
 const shaped=McelSemanticFormPresentationLaw.apply({root,ir,bridge,profile});
 const mounted=chrome?McelSemanticChromePresentationLaw.apply({root,ir,bridge,styleLaw,
   profile:'chrome-tng-side-rails',theme}):{status:'not-requested'};
 const s=el=>getComputedStyle(el);
 const after=layout();
 const evidence={theme:themed.status,form:shaped.status,chrome:mounted.status,
   shape:shaped.shapeLayout?.status,nested:shaped.roundedNestedPaint,
   before,after,
   outerRadius:s(outer).borderTopLeftRadius,statusRadius:s(status).borderTopLeftRadius,
   outerBg:s(outer).backgroundColor,statusBg:s(status).backgroundColor,
   outerRect:box(outer),statusRect:box(status),
   identity:activity===root.querySelector('[data-mcel-activity]'),
   outerMarked:outer.hasAttribute('data-mcel-form-presentation-nested-paint'),
   insideScrollbar:s(nav).overflowY};
 if(remove){
   const original=s(outer).borderTopLeftRadius;
   McelSemanticFormPresentationLaw.apply({root,ir,bridge,profile:'form-wireframe'});
   evidence.wireframe={outer:s(outer).borderTopLeftRadius,
     status:s(status).borderTopLeftRadius,
     marker:outer.hasAttribute('data-mcel-form-presentation-nested-paint')};
   McelSemanticFormPresentationLaw.apply({root,ir,bridge,profile});
   evidence.restored={outer:s(outer).borderTopLeftRadius,status:s(status).borderTopLeftRadius,
     marker:outer.hasAttribute('data-mcel-form-presentation-nested-paint'),
     rects:layout()};
 }
 return evidence;
}'''


def test_activity_paint_continuity_at_all_themes_and_widths():
    with sync_playwright() as p:
        browser=_chrome(p,None)
        for width in (390,846,960,1440):
            page=_open(browser,width)
            for theme in THEMES:
                outcome=page.evaluate(PROBE,{'theme':theme,'profile':'form-current','chrome':True})
                assert outcome['theme']=='applied-in-isolated-lab-frame',(width,theme,outcome)
                assert outcome['form']=='applied-in-isolated-lab-frame' and outcome['shape']=='feasible',(width,theme,outcome)
                assert outcome['chrome']=='applied',(width,theme,outcome)
                assert outcome['nested']>=1 and outcome['outerMarked'],(width,theme,outcome)
                assert outcome['outerRadius']==outcome['statusRadius']=='14px',(width,theme,outcome)
                assert outcome['outerBg']==outcome['statusBg'],(width,theme,outcome)
                assert outcome['outerRect']==outcome['statusRect'],(width,theme,outcome)
                assert outcome['identity'],(width,theme,outcome)
                assert all(outcome['before'][key]==outcome['after'][key] for key in
                           ('activity','holder','outer','status','nav')),(width,theme,outcome)
                assert outcome['before']['evaluate'][2:]==outcome['after']['evaluate'][2:],(width,theme,outcome)
            page.close()
        browser.close()


def test_form_profile_switches_clear_owned_nested_paint_without_layout_drift():
    with sync_playwright() as p:
        browser=_chrome(p,None);page=_open(browser)
        for profile in ('form-current','form-angular','form-soft','form-pill','form-wireframe'):
            report=page.evaluate(PROBE,{'profile':profile,'remove':True})
            assert report['form']=='applied-in-isolated-lab-frame',(profile,report)
            assert report['shape']=='feasible',(profile,report)
            if profile == 'form-current':
                assert report['outerRect']==report['statusRect'],(profile,report)
            assert report['before']['nav']==report['after']['nav'],(profile,report)
            assert report['outerRadius']==report['statusRadius'],(profile,report)
            assert report['wireframe']=={'outer':'0px','status':'0px','marker':False},(profile,report)
            assert all(report['restored']['rects'][key]==report['after'][key] for key in
                       ('outer','status','nav')),(profile,report)
            assert report['restored']['outer']==report['restored']['status'],(profile,report)
        page.close();browser.close()


def test_activity_history_works_after_rounding_and_tng_switch():
    with sync_playwright() as p:
        browser=_chrome(p,None);page=_open(browser)
        report=page.evaluate(PROBE,{'theme':'theme-machine','profile':'form-current'})
        assert report['outerRadius']=='14px' and report['outerMarked']
        page.locator('form[data-intent="intent:evaluate"] input[name="expression"]').fill('2+3')
        page.locator('form[data-intent="intent:evaluate"] button[type="submit"]').click()
        page.locator('[data-state="state:arithmetic-result"]').get_by_text('5').wait_for()
        assert page.locator('[data-mcel-activity] summary').inner_text()=='Activity · 1 recent operation'
        page.locator('[data-mcel-activity] summary').click()
        assert page.locator('[data-mcel-activity] li').count()==1
        checks=page.evaluate('''() => {
          const root=document.getElementById('mcel-expanded-root'),
            activity=root.querySelector('[data-mcel-activity]'),
            outer=activity.closest('[data-mcel-region]').parentElement.closest('[data-mcel-region]');
          return {radius:getComputedStyle(outer).borderTopLeftRadius,
            marker:outer.hasAttribute('data-mcel-form-presentation-nested-paint'),
            open:activity.open,items:activity.querySelectorAll('li').length};
        }''')
        assert checks=={'radius':'14px','marker':True,'open':True,'items':1},checks
        page.close();browser.close()
