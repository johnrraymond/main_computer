"""The first modern semantic Chrome profile: horizontal TNG rails only."""
from pathlib import Path

from playwright.sync_api import sync_playwright

from main_computer.mcel_calculator_expanded_host import render_expanded_page
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

ROOT=Path(__file__).resolve().parents[1]
WEB=ROOT/'main_computer/web/applications'


def _open(browser,width,height=760):
    page=browser.new_page(viewport={'width':width,'height':height},device_scale_factor=.75)
    page.evaluate('''() => {for(const name of ['localStorage','sessionStorage']){
        const m=new Map();Object.defineProperty(window,name,{configurable:true,value:{
          getItem:k=>m.get(k)||null,setItem:(k,v)=>m.set(k,String(v)),
          removeItem:k=>m.delete(k),clear:()=>m.clear()}})}}''')
    page.set_content(render_expanded_page(ROOT,promoted=True))
    assert page.locator('#mcel-expanded-diagnostic').inner_text()=='Semantic Calculator ready'
    for js in ('mcel-semantic-presentation-bridge.js','mcel-semantic-chrome-presentation-law.js','mcel-unified-presentation-controller.js'):
        page.add_script_tag(path=str(WEB/'scripts'/js))
    return page


SETUP='''() => {
  const root=document.getElementById('mcel-expanded-root');
  const ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
  const unit=root.querySelector('[data-mcel-working-unit][data-mcel-sizing="intrinsic"]');
  const workspace=unit.parentElement;
  const nav=root.querySelector('[data-mcel-natural-scroller="navigation"]');
  const activity=root.querySelector('[data-mcel-natural-scroller="inspection"]');
  const submit=unit.querySelector('button[type="submit"]');
  const box=el=>{let r=el.getBoundingClientRect();return [r.x,r.y,r.width,r.height]};
  const state=()=>({unit:box(unit),workspace:box(workspace),nav:box(nav),activity:box(activity),
    input:box(submit),scroll:[workspace.scrollHeight,workspace.clientHeight,workspace.scrollWidth,workspace.clientWidth]});
  return {root,ir,unit,workspace,nav,activity,submit,box,state};
}'''


def test_chrome_is_catalogued_and_lab_selectable_without_global_activation():
    source=(WEB/'scripts/mcel-chrome-law.js').read_text()
    lab=(WEB/'scripts/mcel-lab.js').read_text()
    page=(WEB/'apps/mcel-lab.html').read_text()
    app=(WEB.parent/'applications.html').read_text()
    assert '"chrome-tng-side-rails"' in source
    assert 'value="chrome-tng-side-rails">TNG Side Rails' in page
    assert 'mcel-semantic-chrome-presentation-law.js' in app
    assert 'applyMcelSemanticChromePreview(observation, reason)' in lab
    assert 'applyMcelSemanticChromePreview(null, reason)' in lab
    assert 'data-mcel-semantic-chrome' in (WEB/'scripts/mcel-semantic-chrome-presentation-law.js').read_text()
    assert 'mcel-semantic-chrome-presentation-law.js' not in (ROOT/'main_computer/mcel_calculator_expanded_host.py').read_text()


def test_tng_rails_center_tool_without_modifying_mode_activity_or_functionality():
    with sync_playwright() as p:
        browser=_chrome(p,None)
        for width in (960,1440):
            page=_open(browser,width)
            data=page.evaluate('''() => {
              const {root,ir,unit,workspace,nav,activity,submit,box,state} = ('''+SETUP+''')();
              const first=state(),children=[...unit.children],controls=[...root.querySelectorAll('button,input')];
              const response=McelSemanticChromePresentationLaw.apply({root,ir,
                bridge:McelSemanticPresentationBridge,profile:'chrome-tng-side-rails',
                theme:'theme-machine',styleLaw:McelLabStyleLaw});
              const second=state();
              const rails=[...unit.querySelectorAll('[data-mcel-chrome-generated="true"]')];
              const positions=rails.map(rail=>({part:rail.getAttribute('data-mcel-chrome-part'),
                rect:box(rail),pointer:getComputedStyle(rail).pointerEvents,
                color:getComputedStyle(rail).backgroundImage,
                aria:rail.getAttribute('aria-hidden'),inert:rail.hasAttribute('inert')}));
              const bounded=rails.every(rail=>rail.closest('[data-mcel-working-unit]')===unit);
              const renderedColor=getComputedStyle(rails[0]).backgroundImage;
              const duplicate=McelSemanticChromePresentationLaw.apply({root,ir,
                bridge:McelSemanticPresentationBridge,profile:'chrome-tng-side-rails',
                theme:'theme-machine',styleLaw:McelLabStyleLaw});
              return {first,second,response,positions,duplicate,
                originalChildrenPreserved:children.every(child=>child.parentElement===unit),
                nativeControlsPreserved:controls.every(child=>root.contains(child)),
                count:unit.querySelectorAll('[data-mcel-chrome-generated="true"]').length,
                bounded,
                colorToken:root.style.getPropertyValue('--mcel-tng-rail-inset'),
                renderedColor};
            }''')
            assert data['response']['status']=='applied', (width,data)
            assert data['response']['horizontalOnly'] and data['response']['rails']==2,(width,data)
            assert data['duplicate']['status']=='applied' and data['count']==2,(width,data)
            assert data['originalChildrenPreserved'] and data['nativeControlsPreserved'] and data['bounded'],(width,data)
            assert data['first']['nav']==data['second']['nav'],(width,data)
            assert data['first']['activity']==data['second']['activity'],(width,data)
            assert data['first']['input'][2:]==data['second']['input'][2:],(width,data)
            assert data['first']['unit'][2:]==data['second']['unit'][2:],(width,data)
            assert data['first']['unit'][0]<data['second']['unit'][0],(width,data)
            assert abs(data['second']['unit'][0]+data['second']['unit'][2]/2 -
                       (data['second']['workspace'][0]+(data['second']['workspace'][2]-15)/2))<2,(width,data)
            assert data['first']['scroll']==data['second']['scroll'],(width,data)
            for item in data['positions']:
                assert item['rect'][1]==data['second']['unit'][1]
                assert item['rect'][3]==data['second']['unit'][3]
            assert data['positions'][0]['rect'][0]+data['positions'][0]['rect'][2] < data['second']['unit'][0],(width,data)
            assert data['positions'][1]['rect'][0] > data['second']['unit'][0]+data['second']['unit'][2],(width,data)
            assert all(x['pointer']=='none' and x['inert'] and x['aria']=='true' for x in data['positions']),data
            assert '#b29acb'==data['colorToken'] and '178, 154, 203' in data['renderedColor'],data
            # The calculation uses original operational controls.
            page.locator('[data-mcel-node$="arithmetic-expression"] input').fill('2+3')
            page.locator('[data-mcel-node$="arithmetic-expression"] button[type="submit"]').click()
            page.wait_for_function("() => document.querySelector('[data-mcel-node$=calculated-value]')?.textContent?.trim()==='5'")
            page.close()
        browser.close()


def test_profile_change_restores_original_layout_and_theme_controls_physical_color():
    with sync_playwright() as p:
        browser=_chrome(p,None)
        page=_open(browser,1440)
        result=page.evaluate('''() => {
          const {root,ir,unit,workspace,nav,activity,submit,box,state}= ('''+SETUP+''')();
          const render=(profile,theme)=>McelSemanticChromePresentationLaw.apply({root,ir,
            bridge:McelSemanticPresentationBridge,styleLaw:McelLabStyleLaw,profile,theme});
          const original=state();
          const gold=render('chrome-tng-side-rails','theme-machine');
          const goldBox=state();
          const goldColor=root.style.getPropertyValue('--mcel-tng-rail-primary');
          const purpleColor=root.style.getPropertyValue('--mcel-tng-rail-inset');
          const civic=render('chrome-tng-side-rails','theme-civic');
          const civicBox=state();
          const civicColor=root.style.getPropertyValue('--mcel-tng-rail-primary');
          const oldSubmit=submit;
          const cleared=render('chrome-strict-hierarchy','theme-machine');
          const restored=state();
          return {original,gold,goldBox,goldColor,purpleColor,civic,civicBox,civicColor,cleared,restored,
            rails:root.querySelectorAll('[data-mcel-chrome-generated="true"]').length,
            marker:root.hasAttribute('data-mcel-semantic-chrome'),
            buttonIdentity:oldSubmit===unit.querySelector('button[type="submit"]'),
            styleProperties:root.style.getPropertyValue('--mcel-tng-rail-primary')};
        }''')
        browser.close()
    assert result['gold']['status']==result['civic']['status']=='applied',result
    assert result['cleared']['status']=='unchanged' and result['rails']==0 and not result['marker'],result
    assert result['goldColor'] != result['civicColor'] and result['purpleColor']=='#b29acb',result
    assert result['goldBox']['unit']==result['civicBox']['unit'],result
    assert result['original']==result['restored'],result
    assert result['styleProperties']=='' and result['buttonIdentity'],result


def test_small_workspace_does_not_overlap_or_overflow():
    with sync_playwright() as p:
        browser=_chrome(p,None)
        for width in (390,846):
            page=_open(browser,width,850 if width==390 else 385)
            result=page.evaluate('''() => {
              const {root,ir,unit,workspace,nav,activity,submit,box,state}= ('''+SETUP+''')();
              const before=state();
              const res=McelSemanticChromePresentationLaw.apply({root,ir,
                bridge:McelSemanticPresentationBridge,profile:'chrome-tng-side-rails',
                theme:'theme-machine',styleLaw:McelLabStyleLaw});
              const after=state();
              const rails=[...unit.querySelectorAll('[data-mcel-chrome-generated="true"]')];
              return {before,after,status:res.status,rails:rails.map(x=>({visible:getComputedStyle(x).display,
                width:box(x)[2]}))};
            }''')
            assert result['status']=='applied',(width,result)
            assert result['after']['scroll']==result['before']['scroll'],(width,result)
            assert all(rail['visible']=='none' for rail in result['rails']),(width,result)
            page.close()
        browser.close()


def test_lab_iframe_chrome_selector_binds_real_calculator_semantics():
    """Exercise the same Lab methods used by the Chrome select, not just the law."""
    source=(WEB/'apps/mcel-lab.html').read_text()
    start=source.index('  <section id="mcel-lab-presentation-workspace"')
    end=source.index('  <section id="mcel-semantic-compatibility"',start)
    html=('<!doctype html><html><head><style>body{background:#080812;color:white}'
          '.mcel-runtime-preview{display:none}#mcel-lab-app{display:block!important;min-height:100vh}'
          '</style></head><body><div id="fake-mcel-runtime-preview" class="mcel-runtime-preview theme-machine"></div>'
          '<div id="mcel-lab-app" data-mcel-workspace-view="presentation">'+source[start:end]+'</div></body></html>')
    js=(WEB/'scripts/mcel-lab.js').read_text()
    start=js.index('    // The Calculator experiment never writes to the production Calculator DOM.')
    end=js.index('    // Presentation compatibility inspection is observational only.',start)
    operations=js[start:end]
    child=render_expanded_page(ROOT,promoted=True).replace('<script id="mcel-expanded-ir"',
      '''<script>for(const name of ['localStorage','sessionStorage']){const m=new Map();
      Object.defineProperty(window,name,{configurable:true,value:{getItem:k=>m.get(k)||null,
      setItem:(k,v)=>m.set(k,String(v)),removeItem:k=>m.delete(k),clear:()=>m.clear()}});}</script>
      <script id="mcel-expanded-ir"''',1)
    with sync_playwright() as p:
        browser=_chrome(p,None)
        page=browser.new_page(viewport={'width':1440,'height':900})
        page.set_content(html)
        page.add_style_tag(path=str(WEB/'styles/mcel-lab.css'))
        for name in ('mcel-contract.js','mcel-law-registry.js','mcel-style-law.js','mcel-chrome-law.js',
                     'mcel-semantic-presentation-bridge.js','mcel-semantic-theme-preview.js',
                     'mcel-semantic-chrome-presentation-law.js','mcel-unified-presentation-controller.js'):
            page.add_script_tag(path=str(WEB/'scripts'/name))
        page.add_script_tag(content='''var mcelLabState={semanticThemePreviewEnabled:false,chrome:'chrome-tng-side-rails'};
            var mcelThemeSelect=document.getElementById('mcel-theme-select');
            var mcelChromeSelect=document.getElementById('mcel-chrome-select');
            var mcelFormPresentationSelect=document.getElementById('mcel-form-presentation-select');
            var mcelRuntimePreview=document.getElementById('fake-mcel-runtime-preview');
            mcelChromeSelect.value='chrome-tng-side-rails';
            '''+operations+'''
            document.getElementById('mcel-semantic-theme-enable').addEventListener('click',enableMcelSemanticThemePreview);
            document.getElementById('mcel-semantic-theme-frame').addEventListener('load',onMcelSemanticThemeFrameLoad);
            ''')
        page.locator('#mcel-semantic-theme-enable').click()
        page.evaluate('''child => {document.getElementById('mcel-semantic-theme-frame').srcdoc=child}''',child)
        page.wait_for_function('''() => document.getElementById('mcel-semantic-theme-frame').contentDocument?.getElementById('mcel-expanded-root')?.querySelector('[data-mcel-natural-scroller="workspace"]')''', timeout=10000)
        state=page.evaluate('''() => ({source:mcelSemanticThemeFrameObservation(mcelSemanticThemeFrame()).status, theme:applyMcelSemanticThemePreview('fixture-mounted'), chrome:document.getElementById('mcel-semantic-chrome-presentation-evidence').textContent})''')
        assert '2 inert rails' in state['chrome'],state
        result=page.evaluate('''() => {
          const frame=document.getElementById('mcel-semantic-theme-frame');
          const root=frame.contentDocument.getElementById('mcel-expanded-root');
          const unit=root.querySelector('[data-mcel-working-unit][data-mcel-sizing="intrinsic"]');
          const before=unit.querySelectorAll('[data-mcel-chrome-generated="true"]').length;
          mcelThemeSelect.value='theme-civic';applyMcelSemanticThemePreview('theme-switch');
          const themeColor=root.style.getPropertyValue('--mcel-tng-rail-primary');
          mcelChromeSelect.value='chrome-strict-hierarchy';
          const clear=applyMcelSemanticChromePreview(null,'strict-switch');
          const strict=unit.querySelectorAll('[data-mcel-chrome-generated="true"]').length;
          mcelChromeSelect.value='chrome-tng-side-rails';
          const restore=applyMcelSemanticChromePreview(null,'reselect-tng');
          return {before,themeColor,clear:clear.status,strict,restore:restore.status,
            after:unit.querySelectorAll('[data-mcel-chrome-generated="true"]').length,
            message:document.getElementById('mcel-semantic-chrome-presentation-evidence').textContent,
            app:root.getAttribute('aria-label')};
        }''')
        browser.close()
    assert result['before']==result['after']==2 and result['strict']==0,result
    assert result['clear']=='unchanged' and result['restore']=='applied',result
    assert result['themeColor']=='#337bad',result
    assert 'TNG Side Rails' in result['message'],result
