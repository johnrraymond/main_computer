"""TNG-default MCEL Lab preview mounts unrelated canonical applications without synthesized IR."""
from pathlib import Path

from playwright.sync_api import sync_playwright
from main_computer.mcel_semantic_other_apps_host import render_semantic_page
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "main_computer/web/applications"


def test_lab_defaults_to_tng_and_exposes_four_canonical_mounts():
    bindings = (WEB / "scripts/dom-bindings/mcel-lab.js").read_text()
    lab = (WEB / "scripts/mcel-lab.js").read_text()
    markup = (WEB / "apps/mcel-lab.html").read_text()
    assert 'chrome: "chrome-tng-side-rails"' in bindings
    assert 'mcelLabState.chrome = "chrome-tng-side-rails"' in lab
    assert 'mcel-semantic-preview-app' in markup
    for app in ('calculator', 'file-manager', 'task-manager', 'code-editor'):
        assert f'<option value="{app}">' in markup
        assert f'{app}:' in lab if app == 'calculator' else f'"{app}":' in lab
    assert 'mcel-semantic-preview-app' in lab
    assert "selected.route" in lab
    assert 'mcel-shell-child=1' in lab
    # The selector is a fixed catalog of local semantic routes; it never takes an arbitrary URL.
    assert 'frame.src = `${selected.route}?' in lab


def test_lab_app_selector_reuses_one_live_presentation_pipeline():
    source = (WEB / 'scripts/mcel-lab.js').read_text()
    prefix = source.split('    // Presentation compatibility inspection is observational only.', 1)[0]
    with sync_playwright() as p:
        browser = _chrome(p, None)
        page = browser.new_page()
        page.set_content("""
          <div class="mcel-lab-presentation-stage"></div>
          <section id="mcel-legacy-site-preview"></section>
          <section id="mcel-semantic-theme-evidence" hidden>
            <div id="mcel-semantic-theme-card-swatch"></div>
            <div id="mcel-semantic-theme-control-swatch"></div>
            <span id="mcel-semantic-theme-evidence-text"></span>
            <span id="mcel-semantic-form-presentation-evidence"></span>
            <span id="mcel-semantic-chrome-presentation-evidence"></span>
          </section>
          <select id="mcel-semantic-preview-app">
            <option value="calculator">Calculator</option>
            <option value="file-manager">File Manager</option>
            <option value="task-manager">Task Manager</option>
            <option value="code-editor">Code Editor</option>
          </select>
          <button id="mcel-semantic-theme-enable"></button>
          <button id="mcel-semantic-theme-disable"></button>
          <output id="mcel-semantic-theme-status"></output>
          <iframe id="mcel-semantic-theme-frame" hidden></iframe>
        """)
        page.evaluate("""() => {
          window.mcelLabState = {chrome:'chrome-tng-side-rails', theme:'theme-machine',
            formPresentation:'form-current', semanticPreviewApp:'calculator'};
          window.mcelChromeSelect = {value:'chrome-tng-side-rails'};
          window.mcelThemeSelect = {value:'theme-machine'};
          window.mcelFormPresentationSelect = {value:'form-current'};
          window.McelLabChromeLaw = {normalizeChrome:x=>x, chromeLabel:()=> 'TNG Side Rails'};
          window.McelLabStyleLaw = {normalizeTheme:x=>x, themePaletteFor:()=>({}),
            themeLabel:()=> 'Original MCEL'};
          window.McelSemanticPresentationBridge = {observeRuntimePresentationTargets:()=>({
            status:'source-bound', expectedControls:2, expectedRegions:2,
            targets:[{kind:'control'},{kind:'control'},{kind:'region'},{kind:'region'}]
          })};
          window.McelSemanticThemePreview = {apply:()=>({
            status:'applied-in-isolated-lab-frame', controls:2, regions:2,
            visualEvidence:{uncoloredRegions:0,uncoloredControls:0,observed:4}
          })};
          window.McelSemanticFormPresentationLaw = {normalize:x=>x,
            apply:()=>({status:'applied-in-isolated-lab-frame',shapedElements:3,
              roundedGroupBackgrounds:1,shapeLayout:{adjusted:0,violations:[]}})};
          window.McelSemanticChromePresentationLaw = {apply:args=>{
            window.__lastChrome = args;
            return {status:'applied',rails:0,materialCoverage:{materials:{'raised-control':2}}};
          }};
          const frame=document.getElementById('mcel-semantic-theme-frame');
          Object.defineProperty(frame,'src',{configurable:true,get(){return this.__src||''},
            set(value){this.__src=value}});
          Object.defineProperty(frame,'contentDocument',{configurable:true,
            get(){return this.__fixture||null}});
          window.__setFixture = (rootId,irId,appId) => {
            const doc=document.implementation.createHTMLDocument('host');
            doc.body.innerHTML = `<div id="${rootId}"></div>`;
            const ir=doc.createElement('script');ir.id=irId;
            ir.type='application/json';ir.textContent=JSON.stringify({schema:'mcel.application-ir.v1',
              application:{appId},surfaces:[{}]});doc.body.appendChild(ir);
            frame.__fixture=doc;
          };
        }""")
        page.add_script_tag(content=prefix)
        result=page.evaluate("""() => {
          enableMcelSemanticThemePreview();
          const frame=document.getElementById('mcel-semantic-theme-frame');
          const menu=document.getElementById('mcel-semantic-preview-app');
          const cases=[
            ['calculator','mcel-expanded-root','mcel-expanded-ir','semantic-calculator-expanded','/applications/calculator'],
            ['file-manager','mcel-workbench','mcel-workbench-ir','semantic-file-manager','/applications/file-explorer'],
            ['task-manager','mcel-workbench','mcel-workbench-ir','semantic-task-manager','/applications/task-manager'],
            ['code-editor','mcel-workbench','mcel-workbench-ir','semantic-code-editor','/applications/code-editor']
          ];
          const observed=[];
          for (const [selection,rootId,irId,id,route] of cases) {
            menu.value=selection;changeMcelSemanticPreviewApp();
            window.__setFixture(rootId,irId,id);
            const apply=applyMcelSemanticThemePreview('test');
            observed.push({selection,route:frame.src, expected:route,
              status:apply.status,chrome:window.__lastChrome?.profile,
              appId:window.__lastChrome?.ir?.application?.appId});
          }
          disableMcelSemanticThemePreview();
          return {observed,finalSrc:frame.src,hidden:frame.hidden,
            enabled:window.mcelLabState.semanticThemePreviewEnabled};
        }""")
        for entry in result['observed']:
            assert entry['route'].startswith(entry['expected']+'?'),entry
            assert entry['status']=='applied-in-isolated-lab-frame',entry
            assert entry['chrome']=='chrome-tng-side-rails',entry
            assert entry['appId'].startswith('semantic-'),entry
        assert result['finalSrc']=='about:blank' and result['hidden'] and not result['enabled'],result
        browser.close()


def test_three_other_operational_applications_accept_tng_and_theme_without_rails():
    with sync_playwright() as p:
        browser = _chrome(p, None)
        for name in ('file-manager', 'task-manager', 'code-editor'):
            page = browser.new_page(viewport={'width':1100,'height':740})
            page.set_content(render_semantic_page(name, ROOT, promoted=True))
            assert page.locator('#mcel-workbench-status').inner_text()=='Semantic application ready'
            for script in ('mcel-semantic-presentation-bridge.js',
                           'mcel-semantic-theme-preview.js', 'mcel-semantic-chrome-presentation-law.js'):
                page.add_script_tag(path=str(WEB / 'scripts' / script))
            report = page.evaluate("""() => {
              const root=document.getElementById('mcel-workbench');
              const ir=JSON.parse(document.getElementById('mcel-workbench-ir').textContent);
              const bridge=McelSemanticPresentationBridge;
              const before=bridge.observeRuntimePresentationTargets({root,ir});
              const theme=McelSemanticThemePreview.apply({root,ir,bridge,
                bindingModel:'operational-runtime',styleLaw:McelLabStyleLaw,
                theme:'theme-machine',tokens:McelLabStyleLaw.themePaletteFor('theme-machine')});
              const chrome=McelSemanticChromePresentationLaw.apply({root,ir,bridge,
                profile:'chrome-tng-side-rails',theme:'theme-machine',styleLaw:McelLabStyleLaw});
              const count=root.querySelectorAll('[data-mcel-chrome-material]').length;
              const rails=root.querySelectorAll('[data-mcel-chrome-part^="tng-side-"]').length;
              const unchanged=McelSemanticChromePresentationLaw.apply({root,ir,bridge,
                profile:'chrome-strict-hierarchy',theme:'theme-machine',styleLaw:McelLabStyleLaw});
              return {before:before.status,theme:theme.status,chrome:chrome.status,
                count,rails,unchanged:unchanged.status,
                cleaned:root.querySelectorAll('[data-mcel-chrome-material]').length===0};
            }""")
            assert report['before']=='source-bound', (name,report)
            assert report['theme']=='applied-in-isolated-lab-frame', (name,report)
            assert report['chrome']=='applied' and report['count']>0,(name,report)
            assert report['rails']==0,(name,report)
            assert report['unchanged']=='unchanged' and report['cleaned'],(name,report)
            page.close()
        browser.close()
