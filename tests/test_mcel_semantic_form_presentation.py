"""Form Presentation Law: independent physical shape, semantic source binding."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "main_computer/web/applications"
SCRIPT = WEB / "scripts/mcel-semantic-form-presentation-law.js"


def test_ownership_and_no_collision_with_validation_form_law():
    law = SCRIPT.read_text(encoding="utf-8")
    theme = (WEB / "scripts/mcel-semantic-theme-preview.js").read_text(encoding="utf-8")
    lab = (WEB / "scripts/mcel-lab.js").read_text(encoding="utf-8")
    app = (ROOT / "main_computer/web/applications.html").read_text(encoding="utf-8")
    html = (WEB / "apps/mcel-lab.html").read_text(encoding="utf-8")
    assert "McelSemanticFormPresentationLaw" in law
    assert "McelLabFormLaw" not in law
    assert "mcel-form-law.js" in app
    assert "mcel-semantic-form-presentation-law.js" in app
    assert app.index("mcel-semantic-presentation-bridge.js") < app.index("mcel-semantic-form-presentation-law.js") < app.index("mcel-semantic-theme-preview.js")
    assert 'id="mcel-form-presentation-select"' in html
    assert "data-mcel-form-presentation-corner" in law
    assert "cornerAwareGroups" in law and "cornerAwareGroups" not in theme
    assert "border-radius:" not in theme
    assert "box-shadow:none!important" not in theme
    assert "legacyCss?.()" in lab
    assert "McelSemanticFormPresentationLaw" in lab
    # The Chrome transformations and existing structural remediation code stay intact.
    assert "MCEL.applyChrome" in lab
    assert "runCompositionRemediationPasses" in lab
    assert "border-radius: var(--mcel-form-control-radius);" in lab
    assert "--site-radius:" not in lab[lab.index("    function isolatedSiteCss("):lab.index("    function isolatedSiteDocument(")]
    assert "body[data-mcel-form" in law


def test_legacy_shape_presets_retain_theme_baselines_and_decouple_form():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

    with sync_playwright() as p:
        browser = _chrome(p, None)
        page = browser.new_page()
        page.set_content('<body class="theme-machine" data-mcel-form="form-current"><button id="test">Run</button></body>')
        page.add_script_tag(path=str(SCRIPT))
        result = page.evaluate("""() => {
          const law=McelSemanticFormPresentationLaw;
          const button=document.getElementById('test');
          const style=document.createElement('style');
          style.textContent=law.legacyCss()+
            'button{border-radius:var(--mcel-form-control-radius)}';
          document.head.appendChild(style);
          const measured=[];
          for (const theme of ['theme-machine','theme-editorial','theme-luxury','theme-civic']) {
            document.body.className=theme;
            for (const form of law.profiles) {
              document.body.dataset.mcelForm=form.id;
              measured.push({theme,form:form.id,
                radius:getComputedStyle(button).borderTopLeftRadius,
                text:button.textContent});
            }
          }
          return {measured,invalid:law.normalize('bogus'),profiles:law.profiles.length};
        }""")
        browser.close()
    assert result['profiles'] == 5
    assert result['invalid'] == 'form-current'
    current = {item['theme']: item['radius'] for item in result['measured'] if item['form'] == 'form-current'}
    assert current == {'theme-machine':'999px','theme-editorial':'2px','theme-luxury':'2px','theme-civic':'999px'}
    for form in ('form-angular','form-soft','form-pill','form-wireframe'):
        values = {item['radius'] for item in result['measured'] if item['form'] == form}
        assert len(values) == 1, (form,values)
    assert all(item['text'] == 'Run' for item in result['measured'])


def test_real_operational_calculator_switches_form_without_remount_or_scroll_change():
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch14_7_capability_negotiation_frob import _chrome
    from main_computer.mcel_calculator_expanded_host import render_expanded_page

    with sync_playwright() as p:
        browser = _chrome(p, None)
        for width, height in ((390, 850), (846, 385), (1440, 850)):
            page = browser.new_page(viewport={'width': width, 'height': height}, device_scale_factor=0.75)
            page.evaluate("""() => { for (const name of ['localStorage','sessionStorage']) {
                const data=new Map();Object.defineProperty(window,name,{configurable:true,value:{
                    getItem:key=>data.get(key)||null,setItem:(key,value)=>data.set(key,String(value)),
                    removeItem:key=>data.delete(key),clear:()=>data.clear()}}); }}""")
            page.set_content(render_expanded_page(ROOT, promoted=True))
            assert page.locator('#mcel-expanded-diagnostic').inner_text() == 'Semantic Calculator ready'
            page.add_style_tag(path=str(WEB / 'styles/mcel-lab.css'))
            for name in ('mcel-contract.js','mcel-law-registry.js','mcel-style-law.js',
                         'mcel-semantic-presentation-bridge.js','mcel-semantic-theme-preview.js',
                         'mcel-semantic-form-presentation-law.js'):
                page.add_script_tag(path=str(WEB / 'scripts' / name))
            report = page.evaluate("""() => {
              const root=document.getElementById('mcel-expanded-root');
              const ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
              const bridge=McelSemanticPresentationBridge;
              const themeLaw=McelSemanticThemePreview;
              const formLaw=McelSemanticFormPresentationLaw;
              const palette=document.createElement('div');
              palette.className='mcel-runtime-preview theme-machine';
              document.body.appendChild(palette);
              const targets=bridge.observeRuntimePresentationTargets({root,ir});
              const controls=targets.targets.filter(t=>t.kind==='control');
              const button=controls.flatMap(t=>[...t.element.querySelectorAll('button')])[0];
              if (!button) throw Error('No source-bound native buttons');
              const identity=controls.map(t=>t.element);
              const scrolls=[...root.querySelectorAll('*')].filter(el=>['auto','scroll'].includes(getComputedStyle(el).overflowY));
              const scroll=()=>scrolls.map(el=>[el.clientHeight,el.scrollHeight,el.scrollTop,getComputedStyle(el).overflowY]);
              const positions=()=>[...root.querySelectorAll('button,input,select,textarea')].map(el=>{
                const r=el.getBoundingClientRect();return [r.x,r.y,r.width,r.height,el.disabled];
              });
              const initialPosition=positions();const initialScroll=scroll();
              const scrollWithoutNewOverflow=()=>scrolls.every((el,i)=>
                initialScroll[i][1] > initialScroll[i][0] || el.scrollHeight <= el.clientHeight + 1);
              const theme=(name)=>{
                palette.className='mcel-runtime-preview '+name;
                return themeLaw.apply({root,ir,bridge,styleLaw:McelLabStyleLaw,
                  bindingModel:'operational-runtime',theme:name,
                  tokens:themeLaw.themeTokens(palette,getComputedStyle)});
              };
              const machine=theme('theme-machine');
              const base=formLaw.apply({root,ir,bridge,profile:'form-current'});
              const colorBefore=getComputedStyle(button).backgroundColor;
              const corners=[];
              for (const name of formLaw.profiles.map(p=>p.id)) {
                const result=formLaw.apply({root,ir,bridge,profile:name});
                const style=getComputedStyle(button);
                corners.push({name,status:result.status,radius:style.borderTopLeftRadius,
                  background:style.backgroundColor,painted:result.roundedGroupBackgrounds,
                  shapeCount:result.shapedElements,sourceStable:controls.every((t,i)=>t.element===identity[i]),
                  geometryNegotiated:result.shapeLayout?.status==='feasible',
                  negotiationCount:result.shapeLayout?.adjusted,
                  noNewOverflow:scrollWithoutNewOverflow(),
                  scrollOwnerStable:scrolls.every((el,i)=>getComputedStyle(el).overflowY===initialScroll[i][3])});
              }
              const civic=theme('theme-civic');
              const colorCivic=getComputedStyle(button).backgroundColor;
              const afterTheme=formLaw.apply({root,ir,bridge,profile:'form-soft'});
              const finalRadius=getComputedStyle(button).borderTopLeftRadius;
              return {targetStatus:targets.status,machine:machine.status,civic:civic.status,
                base:base.status,corners,colorBefore,colorCivic,afterTheme:afterTheme.status,finalRadius,
                irPresent:!!document.getElementById('mcel-expanded-ir'),
                formOwnsAttribute:!!root.querySelector('[data-mcel-form-presentation-kind]')};
            }""")
            assert report['targetStatus'] == 'source-bound', (width,report)
            assert report['base'] == 'applied-in-isolated-lab-frame', (width,report)
            assert report['machine'] == report['civic'] == 'applied-in-isolated-lab-frame', (width,report)
            assert len(report['corners']) == 5, (width,report)
            assert len({t['radius'] for t in report['corners']}) >= 3, (width,report)
            assert all(x['sourceStable'] and x['geometryNegotiated'] and x['noNewOverflow']
                       and x['scrollOwnerStable'] for x in report['corners']), (width,report)
            assert any(x['negotiationCount'] > 0 for x in report['corners']), (width,report)
            assert all(x['status'] == 'applied-in-isolated-lab-frame' and x['shapeCount'] > 0 for x in report['corners']), (width,report)
            assert report['colorBefore'] != report['colorCivic'], (width,report)
            assert report['afterTheme'] == 'applied-in-isolated-lab-frame'
            assert report['finalRadius'] == '12px', (width,report)
            assert report['irPresent'] and report['formOwnsAttribute']
            page.close()
        browser.close()


def test_lab_form_selector_switches_source_bound_preview_without_frame_reload():
    """Use the real Lab binding functions against the canonical legacy-host fixture."""
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch14_7_capability_negotiation_frob import _chrome
    from main_computer.mcel_dsl_compiler import compile_dsl_application
    from main_computer.mcel_calculator_host_projection import project as calculator_markup

    lab = (WEB / 'scripts/mcel-lab.js').read_text()
    operations = lab[lab.index('    // The Calculator experiment never writes to the production Calculator DOM.'):
                     lab.index('    // Presentation compatibility inspection is observational only.')]
    change = lab[lab.index('    function changeMcelFormPresentation('):
                 lab.index('    function changeMcelChrome(')]
    html = (WEB / 'apps/mcel-lab.html').read_text()
    preview = html[html.index('  <section id="mcel-lab-presentation-workspace"'):
                   html.index('  <section id="mcel-semantic-compatibility"')]
    ir = compile_dsl_application(ROOT / 'mcel_apps/calculator/application.js').normalized_ir
    frame_html = '<!doctype html><html><head><style>body{margin:0}' + \
        (WEB / 'styles/calculator.css').read_text() + \
        '</style></head><body>' + calculator_markup(ROOT) + '</body></html>'
    with sync_playwright() as p:
        browser = _chrome(p, None)
        page = browser.new_page(viewport={'width':950,'height':850})
        page.set_content('<!doctype html><html><head><style>.mcel-runtime-preview{display:none}</style></head><body>'
                         '<div id="fake-mcel-runtime-preview" class="mcel-runtime-preview theme-machine"></div>'
                         '<section id="mcel-lab-app" data-mcel-workspace-view="presentation">' + preview + '</section></body></html>')
        page.add_style_tag(path=str(WEB / 'styles/mcel-lab.css'))
        for script in ('mcel-contract.js','mcel-law-registry.js','mcel-style-law.js',
                       'mcel-semantic-layout-inference.js','mcel-semantic-presentation-bridge.js',
                       'mcel-semantic-form-presentation-law.js','mcel-semantic-theme-preview.js'):
            page.add_script_tag(path=str(WEB / 'scripts' / script))
        setup = """var mcelLabState={semanticThemePreviewEnabled:true,formPresentation:'form-current',
          compileEvents:[]};
          var mcelThemeSelect=document.getElementById('mcel-theme-select');
          var mcelFormPresentationSelect=document.getElementById('mcel-form-presentation-select');
          var mcelRuntimePreview=document.getElementById('fake-mcel-runtime-preview');
          function syncMcelRenderedSiteFrame(){window.__formLegacySync=(window.__formLegacySync||0)+1;}
          function renderMcelCompilerLog(){};
        """
        page.add_script_tag(content=setup + operations + change)
        page.evaluate('''({ir,frameHtml})=>{
          window.McelLabCanonicalPresentationSources={calculator:ir};
          const f=document.getElementById('mcel-semantic-theme-frame');
          f.hidden=false;
          f.srcdoc=frameHtml;
        }''', {'ir':ir,'frameHtml':frame_html})
        page.wait_for_function('''() => document.getElementById('mcel-semantic-theme-frame')
          .contentDocument?.getElementById('calculator-mode-basic')''')
        result=page.evaluate('''() => {
          const f=document.getElementById('mcel-semantic-theme-frame');
          const button=f.contentDocument.getElementById('calculator-mode-basic');
          const bounds=()=>{const r=button.getBoundingClientRect();return [r.x,r.y,r.width,r.height];};
          const snapshot=()=>({radius:f.contentWindow.getComputedStyle(button).borderTopLeftRadius,
            background:f.contentWindow.getComputedStyle(button).backgroundColor});
          mcelThemeSelect.value='theme-machine';
          const projected=applyMcelSemanticThemePreview('lab-test');
          const before=snapshot(),initialBounds=bounds();
          mcelFormPresentationSelect.value='form-angular';
          const angular=changeMcelFormPresentation('lab-test');
          const afterAngular=snapshot();
          mcelFormPresentationSelect.value='form-pill';
          const pill=changeMcelFormPresentation('lab-test');
          const afterPill=snapshot();
          return {projected:projected.status,angular:angular.status,pill:pill.status,
            before,afterAngular,afterPill,boundsSame:JSON.stringify(bounds())===JSON.stringify(initialBounds),
            nodeSame:button===f.contentDocument.getElementById('calculator-mode-basic'),
            sourceSame:!!button.getAttribute('data-mcel-control'),
            iframeSame:f===document.getElementById('mcel-semantic-theme-frame'),
            legacySync:window.__formLegacySync,form:mcelLabState.formPresentation};
        }''')
        browser.close()
    assert result['projected'] == 'applied-in-isolated-lab-frame', result
    assert result['angular'] == result['pill'] == 'applied-in-isolated-lab-frame',result
    assert result['afterAngular']['radius'] == '2px',result
    assert result['afterPill']['radius'] == '999px',result
    assert result['before']['background'] == result['afterAngular']['background'] == result['afterPill']['background'],result
    assert result['nodeSame'] and result['iframeSame'] and result['sourceSame'] and result['boundsSame'],result
    assert result['legacySync'] == 2 and result['form'] == 'form-pill',result
