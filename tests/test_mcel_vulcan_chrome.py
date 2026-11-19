"""Vulcan: source-bound, isolated light chrome with real MCEL Lab selection."""
from pathlib import Path
from playwright.sync_api import sync_playwright
from tests.test_mcel_semantic_chrome_universal_material import _page
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'main_computer/web/applications'


def test_vulcan_catalog_and_lab_selection_are_authoritative():
    html = (WEB / 'apps/mcel-lab.html').read_text()
    lab = (WEB / 'scripts/mcel-lab.js').read_text()
    assert '<option value="chrome-vulcan">Vulcan</option>' in html
    assert 'McelLabChromeLaw.chromeCatalog' in lab
    assert 'applyMcelSemanticChromePreview(null, reason)' in lab
    with sync_playwright() as pw:
        browser = _chrome(pw, None)
        page = browser.new_page()
        page.set_content('<select id="mcel-chrome-select"></select>')
        page.add_script_tag(path=str(WEB / 'scripts/mcel-chrome-law.js'))
        response = page.evaluate('''() => {
          const law=McelLabChromeLaw;
          const menu=document.getElementById('mcel-chrome-select');
          for (const profile of law.chromeCatalog) {
            const option=document.createElement('option');
            option.value=profile.id;option.textContent=profile.label;menu.appendChild(option);
          }
          menu.value='chrome-vulcan';
          return {name:law.chromeLabel('vulcan'), normalized:law.normalizeChrome(menu.value),
            label:menu.selectedOptions[0]?.textContent,
            kind:law.chromeDefinition('vulcan').kind,
            retainsTng:law.normalizeChrome('tng')};
        }''')
        assert response == {'name':'Vulcan','normalized':'chrome-vulcan',
            'label':'Vulcan','kind':'semantic-chrome','retainsTng':'chrome-tng-side-rails'}
        browser.close()


def test_vulcan_material_state_dynamic_controls_and_cleanup():
    with sync_playwright() as pw:
        browser = _chrome(pw, None)
        page = _page(browser)
        applied = page.evaluate('''() => {
          const root=document.getElementById('mcel-expanded-root');
          const ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
          window.__vulcanParams={root,ir,bridge:McelSemanticPresentationBridge,
            styleLaw:McelLabStyleLaw,theme:'theme-machine'};
          const region=root.querySelector('[data-mcel-region][data-mcel-purpose="input"]');
          region.insertAdjacentHTML('beforeend',`<button id="vulcan-action" type="button">Run</button>
             <button id="vulcan-toggle" type="button" aria-pressed="false">Toggle</button>`);
          window.__vulcanClicks=0;
          root.querySelector('#vulcan-action').addEventListener('click',()=>window.__vulcanClicks++);
          const before=root.querySelectorAll('[data-mcel-chrome-generated="true"]').length;
          const result=McelSemanticChromePresentationLaw.apply({...window.__vulcanParams,
            profile:'chrome-vulcan'});
          const button=root.querySelector('#vulcan-action');
          return {status:result.status,profile:result.profile,material:
            button.getAttribute('data-mcel-chrome-material'),
            source:result.source,rails:result.rails,before,
            background:getComputedStyle(root).backgroundColor,
            radius:getComputedStyle(button).borderRadius,
            generated:root.querySelectorAll('[data-mcel-chrome-generated="true"]').length,
            wear:root.querySelectorAll('[data-mcel-tng-wear-surface]').length};
        }''')
        assert applied['status'] == 'applied' and applied['profile'] == 'chrome-vulcan', applied
        assert applied['rails'] == 0 and applied['generated'] == 0 and applied['wear'] == 0, applied
        assert applied['background'] == 'rgb(255, 255, 255)', applied
        assert applied['material'] == 'raised-control' and applied['radius'] == '12px', applied
        action = page.locator('#vulcan-action')
        action.hover()
        page.wait_for_timeout(160)
        hover = action.evaluate('node => getComputedStyle(node).backgroundColor')
        page.mouse.down()
        pressed = action.evaluate('node => getComputedStyle(node).backgroundColor')
        page.mouse.up()
        assert hover == 'rgb(245, 245, 246)', hover
        assert pressed == 'rgb(236, 236, 238)', pressed
        assert page.evaluate('window.__vulcanClicks') == 1
        toggle=page.locator('#vulcan-toggle')
        toggle.hover()
        toggle.evaluate('node => node.setAttribute("aria-pressed","true")')
        assert toggle.evaluate('node => getComputedStyle(node).backgroundColor') == pressed
        # An added operational control is rebound by child-list reconciliation.
        page.evaluate('''() => document.querySelector('[data-mcel-region][data-mcel-purpose="input"]')
          .insertAdjacentHTML('beforeend','<button id="vulcan-late">Later</button>')''')
        page.wait_for_function('''() => document.getElementById('vulcan-late')?.
          getAttribute('data-mcel-chrome-material')==='raised-control' ''')
        # Existing core calculation must remain functional.
        page.locator('[data-mcel-node$="arithmetic-expression"] input').fill('2+3')
        page.locator('[data-mcel-node$="arithmetic-expression"] button[type="submit"]').click()
        page.wait_for_function("() => document.querySelector('[data-mcel-node$=calculated-value]')?.textContent?.trim()==='5'")
        switched=page.evaluate('''() => {
          const params=window.__vulcanParams;
          const tng=McelSemanticChromePresentationLaw.apply({...params,profile:'chrome-tng-side-rails'});
          const root=params.root;
          const tngState={status:tng.status,rails:root.querySelectorAll('[data-mcel-chrome-generated="true"]').length,
            profile:root.getAttribute('data-mcel-semantic-chrome')};
          const strict=McelSemanticChromePresentationLaw.apply({...params,profile:'chrome-strict-hierarchy'});
          return {tngState,strict:strict.status,
            rootChrome:root.getAttribute('data-mcel-semantic-chrome'),
            marks:root.querySelectorAll('[data-mcel-chrome-material]').length,
            rails:root.querySelectorAll('[data-mcel-chrome-generated="true"]').length};
        }''')
        assert switched['tngState']['status']=='applied',switched
        assert switched['tngState']['profile']=='chrome-tng-side-rails' and switched['tngState']['rails']==2,switched
        assert switched['strict']=='unchanged' and switched['rootChrome'] is None,switched
        assert switched['marks']==switched['rails']==0,switched
        browser.close()


def test_vulcan_on_all_other_lab_mounts_preserves_navigation_and_clear():
    from main_computer.mcel_semantic_other_apps_host import render_semantic_page
    with sync_playwright() as pw:
        browser = _chrome(pw, None)
        for app in ('file-manager', 'task-manager', 'code-editor'):
            page = browser.new_page(viewport={'width':1200,'height':800})
            page.set_content(render_semantic_page(app, ROOT, promoted=True))
            for script in ('mcel-semantic-presentation-bridge.js',
                           'mcel-semantic-chrome-presentation-law.js'):
                page.add_script_tag(path=str(WEB / 'scripts' / script))
            result = page.evaluate('''() => {
              const root=document.getElementById('mcel-workbench');
              const ir=JSON.parse(document.getElementById('mcel-workbench-ir').textContent);
              const params={root,ir,bridge:McelSemanticPresentationBridge,
                styleLaw:McelLabStyleLaw,theme:'theme-machine'};
              const app=McelSemanticChromePresentationLaw.apply({...params,profile:'chrome-vulcan'});
              const nav=root.querySelector('[data-mcel-purpose="navigation"]');
              const measured={status:app.status,rails:app.rails,
                root:getComputedStyle(root).backgroundColor,
                nav:getComputedStyle(nav).backgroundColor,
                navMaterial:nav.getAttribute('data-mcel-chrome-material'),
                materials:root.querySelectorAll('[data-mcel-chrome-material]').length};
              const cleared=McelSemanticChromePresentationLaw.apply({...params,
                profile:'chrome-strict-hierarchy'});
              return {...measured,cleared:cleared.status,remaining:
                root.querySelectorAll('[data-mcel-chrome-material]').length};
            }''')
            assert result['status']=='applied' and result['rails']==0,(app,result)
            assert result['root']=='rgb(255, 255, 255)',(app,result)
            assert result['nav']=='rgb(255, 255, 255)',(app,result)
            assert result['materials']>0 and result['navMaterial']=='housing',(app,result)
            assert result['cleared']=='unchanged' and result['remaining']==0,(app,result)
            page.close()
        browser.close()


def test_vulcan_white_rail_selected_only_no_black_bar():
    from tests.test_mcel_semantic_chrome_universal_material import _page
    with sync_playwright() as pw:
        browser = _chrome(pw, None)
        page = _page(browser)
        measurements = page.evaluate('''() => {
          const root=document.getElementById('mcel-expanded-root');
          const ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
          McelSemanticChromePresentationLaw.apply({root,ir,
            bridge:McelSemanticPresentationBridge,styleLaw:McelLabStyleLaw,
            theme:'theme-machine',profile:'chrome-vulcan'});
          const css=element=>getComputedStyle(element);
          const tabs=[...root.querySelectorAll('[data-mcel-mode-tabs] button')];
          return {
            body:css(document.body).backgroundColor,
            workspace:css(root.querySelector('[data-mcel-purpose="workspace"]')).backgroundColor,
            rail:css(root.querySelector('[data-mcel-natural-scroller="navigation"]')).backgroundColor,
            headingBorder:css(root.querySelector('[data-mcel-navigation-chrome]')).borderBottomWidth,
            tabStyles:tabs.map(el=>({label:el.textContent.trim(),
              selected:el.getAttribute('aria-pressed'),
              background:css(el).backgroundColor,
              border:css(el).borderTopWidth,
              shadow:css(el).boxShadow})),
            diagnostic: (()=>{const el=document.getElementById('mcel-expanded-diagnostic');
              return el&&{width:css(el).width,height:css(el).height,
                clip:css(el).clipPath,liveText:el.textContent.trim()};})()
          };
        }''')
        assert measurements['body']==measurements['workspace']==measurements['rail']=='rgb(255, 255, 255)',measurements
        assert measurements['headingBorder']=='0px',measurements
        assert len(measurements['tabStyles']) >= 4,measurements
        for tab in measurements['tabStyles']:
            assert tab['border']=='0px' and tab['shadow']=='none',tab
            if tab['selected']=='true':
                assert tab['background']=='rgb(236, 236, 238)',tab
            else:
                assert tab['background']=='rgba(0, 0, 0, 0)',tab
        assert measurements['diagnostic']['width']=='1px',measurements
        assert measurements['diagnostic']['height']=='1px',measurements
        assert measurements['diagnostic']['liveText'],measurements
        browser.close()


def test_vulcan_nested_surfaces_do_not_retain_dark_paint_or_status_strip():
    from main_computer.mcel_semantic_other_apps_host import render_semantic_page
    with sync_playwright() as pw:
        browser = _chrome(pw, None)
        for app in ('file-manager', 'task-manager', 'code-editor'):
            page = browser.new_page(viewport={'width':1200,'height':800})
            page.set_content(render_semantic_page(app, ROOT, promoted=True))
            for script in ('mcel-semantic-presentation-bridge.js',
                           'mcel-semantic-chrome-presentation-law.js'):
                page.add_script_tag(path=str(WEB / 'scripts' / script))
            result=page.evaluate('''() => {
              const root=document.getElementById('mcel-workbench');
              const ir=JSON.parse(document.getElementById('mcel-workbench-ir').textContent);
              McelSemanticChromePresentationLaw.apply({root,ir,
                bridge:McelSemanticPresentationBridge,styleLaw:McelLabStyleLaw,
                theme:'theme-machine',profile:'chrome-vulcan'});
              return {
                nested:[...root.querySelectorAll('[data-mcel-color-role="surface.panel"]')]
                  .map(node=>getComputedStyle(node).backgroundColor),
                editor:[...root.querySelectorAll('[data-mcel-document-surface]')]
                  .map(node=>getComputedStyle(node).backgroundColor),
                status: (() => {
                  const node=document.getElementById('mcel-workbench-status');
                  const css=getComputedStyle(node);
                  return {width:css.width,height:css.height,text:node.textContent.trim()};
                })()
              }
            }''')
            assert result['nested'],(app,result)
            assert set(result['nested']) == {'rgb(255, 255, 255)'},(app,result)
            assert set(result['editor']) <= {'rgb(255, 255, 255)'},(app,result)
            assert result['status']['width']=='1px' and result['status']['height']=='1px',(app,result)
            assert result['status']['text'],(app,result)
            page.close()
        browser.close()


def test_vulcan_keypad_has_outlined_keys_without_outlining_navigation():
    with sync_playwright() as pw:
        browser = _chrome(pw, None)
        page = _page(browser)
        result=page.evaluate('''() => {
          const root=document.getElementById('mcel-expanded-root');
          const ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
          McelSemanticChromePresentationLaw.apply({root,ir,
            bridge:McelSemanticPresentationBridge,styleLaw:McelLabStyleLaw,
            theme:'theme-machine',profile:'chrome-vulcan'});
          const summarize=node=>({
            border:getComputedStyle(node).borderTopWidth,
            style:getComputedStyle(node).borderTopStyle,
            color:getComputedStyle(node).borderTopColor,
            background:getComputedStyle(node).backgroundColor,
            radius:getComputedStyle(node).borderRadius});
          return {
            keys:[...root.querySelectorAll('[data-mcel-expression-pad] button[data-pad-action]')]
              .map(node=>({action:node.dataset.padAction,...summarize(node)})),
            nav:[...root.querySelectorAll('[data-mcel-mode-tabs] button')]
              .map(node=>({selected:node.getAttribute('aria-pressed'),...summarize(node)}))
          };
        }''')
        assert len(result['keys'])==21,result
        for key in result['keys']:
            assert key['border']=='1px' and key['style']=='solid',key
            assert key['color']=='rgb(200, 201, 205)',key
            assert key['background']=='rgb(255, 255, 255)',key
            assert key['radius']=='12px',key
        assert result['keys'][-1]['action']=='submit',result
        assert len(result['nav'])==4,result
        assert all(item['border']=='0px' for item in result['nav']),result
        # Probe the full interaction cycle, not only a CSS text match.
        key=page.locator('[data-mcel-expression-pad] button[data-pad-action="7"]')
        key.hover()
        assert key.evaluate('e=>getComputedStyle(e).borderTopWidth')=='1px'
        assert key.evaluate('e=>getComputedStyle(e).backgroundColor')=='rgb(245, 245, 246)'
        page.mouse.down()
        assert key.evaluate('e=>getComputedStyle(e).backgroundColor')=='rgb(236, 236, 238)'
        page.mouse.up()
        assert key.evaluate('e=>getComputedStyle(e).borderTopWidth')=='1px'
        assert page.locator('[data-mcel-node$="arithmetic-expression"] input').input_value().endswith('7')
        browser.close()


def test_vulcan_file_manager_loaded_explorer_is_white_legible_and_reversible():
    """Exercise post-load explorer projection: empty-state tests cannot catch dark paint."""
    from main_computer.mcel_semantic_other_apps_host import render_semantic_page
    with sync_playwright() as pw:
        browser = _chrome(pw, None)
        page = browser.new_page(viewport={'width': 1280, 'height': 850})
        try:
            # A real semantic File Manager read produces these nested DOM nodes
            # only after the authorized root and directory responses arrive.
            page.evaluate('''() => {
              window.vulcanFileCalls=[];
              window.fetch=async (url, opts={}) => {
                const path=String(url), body=JSON.parse(opts.body||'{}');
                window.vulcanFileCalls.push({path,body});
                let data={ok:true};
                if(path.endsWith('/file-explorer/roots'))
                  data.roots=[{id:'workspace',label:'Workspace'},{id:'home',label:'Home'}];
                else if(path.endsWith('/file-explorer/list')){
                  data.root_id=body.root_id;data.relative_path=body.relative_path;
                  data.entries=body.relative_path==='src' ? [
                    {relative_path:'src/nested',name:'nested',kind:'directory'},
                    {relative_path:'src/index.js',name:'index.js',kind:'file',bytes:152}]
                  : [
                    {relative_path:'src',name:'src',kind:'directory'},
                    {relative_path:'README.md',name:'README.md',kind:'file',bytes:2048}];
                }else throw Error('Unexpected File Manager request: '+path);
                return {ok:true,status:200,json:async()=>data};
              };
            }''')
            page.set_content(render_semantic_page('file-manager', ROOT, promoted=True))
            for script in ('mcel-semantic-presentation-bridge.js',
                           'mcel-semantic-chrome-presentation-law.js'):
                page.add_script_tag(path=str(WEB / 'scripts' / script))
            result = page.evaluate('''() => {
              const root=document.getElementById('mcel-workbench');
              window.vulcanFileParams={root,
                ir:JSON.parse(document.getElementById('mcel-workbench-ir').textContent),
                bridge:McelSemanticPresentationBridge,styleLaw:McelLabStyleLaw,
                theme:'theme-machine'};
              return McelSemanticChromePresentationLaw.apply({
                ...window.vulcanFileParams,profile:'chrome-vulcan'});
            }''')
            assert result['status'] == 'applied', result
            pane = page.locator('[data-mcel-explorer-content-pane]')
            pane.wait_for()
            assert page.locator('[data-mcel-directory-entry]').count() == 2
            measurement = page.evaluate('''() => {
              const root=document.getElementById('mcel-workbench');
              const css=sel=>{const el=root.querySelector(sel),style=getComputedStyle(el);
                return {background:style.backgroundColor,text:style.color,
                  border:style.borderTopWidth,boxShadow:style.boxShadow};};
              return {
                nav:css('[data-mcel-purpose="navigation"]'),
                workspace:css('[data-mcel-purpose="workspace"]'),
                selectedRoot:css('[data-record-key="workspace"][aria-selected="true"]'),
                unselectedRoot:css('[data-record-key="home"]'),
                tree:css('[data-mcel-explorer-navigation]'),
                treeHeading:css('[data-mcel-explorer-navigation] h3'),
                content:css('[data-mcel-explorer-content-pane]'),
                directoryHeading:css('[data-mcel-directory-head]'),
                columns:css('[data-mcel-directory-columns]'),
                folder:css('[data-mcel-directory-entry="src"]'),
                file:css('[data-mcel-directory-entry="README.md"]'),
                fileName:css('[data-mcel-directory-entry="README.md"] [data-mcel-directory-open]'),
                mutedType:css('[data-mcel-directory-type]'),
                divider:css('[data-mcel-explorer-navigation]')
              };
            }''')
            white = 'rgb(255, 255, 255)'
            assert measurement['workspace']['background'] == white, measurement
            assert measurement['nav']['background'] == white, measurement
            for key in ('tree', 'treeHeading', 'content', 'directoryHeading',
                        'columns', 'folder', 'file'):
                assert measurement[key]['background'] == white, (key, measurement)
                assert measurement[key]['text'] == 'rgb(32, 33, 35)', (key, measurement)
            assert measurement['selectedRoot']['background'] == 'rgb(236, 236, 238)', measurement
            assert measurement['selectedRoot']['text'] == 'rgb(32, 33, 35)', measurement
            assert measurement['unselectedRoot']['background'] in ('rgba(0, 0, 0, 0)', white), measurement
            assert measurement['fileName']['text'] == 'rgb(32, 33, 35)', measurement
            assert measurement['mutedType']['text'] == 'rgb(96, 99, 106)', measurement
            assert measurement['divider']['border'] == '0px', measurement
            assert page.locator('[data-mcel-directory-entry="src"] [data-mcel-directory-open]').is_enabled()
            # The styling does not hide or replace the actual source-bound controls.
            page.locator('[data-mcel-directory-entry="src"] [data-mcel-directory-open]').click()
            page.wait_for_function('''() =>
              document.querySelector('[data-mcel-directory-path]')?.textContent==='/src' ''')
            assert any(x['path'].endswith('/file-explorer/list') and
                       x['body'].get('relative_path') == 'src'
                       for x in page.evaluate('window.vulcanFileCalls'))
            # Clear Vulcan and verify the application's original style is restored.
            cleared = page.evaluate('''() => {
              McelSemanticChromePresentationLaw.apply({
                ...window.vulcanFileParams,profile:'chrome-strict-hierarchy'});
              const el=document.querySelector('[data-mcel-explorer-content-pane]');
              return {active:window.vulcanFileParams.root.getAttribute('data-mcel-semantic-chrome'),
                background:getComputedStyle(el).backgroundColor};
            }''')
            assert cleared == {'active': None, 'background': 'rgb(16, 33, 45)'}, cleared
        finally:
            browser.close()
