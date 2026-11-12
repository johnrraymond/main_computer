"""File Manager response-order and selected-root consistency regressions.

These run with an isolated Chromium transport. They do not assert live Windows
parity or grant any additional filesystem/navigation capability.
"""
from __future__ import annotations

from pathlib import Path
from playwright.sync_api import sync_playwright
from main_computer.mcel_semantic_other_apps_host import render_semantic_page
from tools.mcel_phase2b_browser import launch_semantic_browser


def test_root_change_discards_in_flight_old_root_listing_and_inspection():
    with sync_playwright() as p:
        browser, _ = launch_semantic_browser(p)
        try:
            page = browser.new_page(viewport={"width": 390, "height": 850})
            page.evaluate('''() => {
              const reply=data=>({ok:true,status:200,json:async()=>data});
              window.fetch=async (url,opts) => {
                const body=JSON.parse(opts.body||'{}'), path=String(url);
                if(path.endsWith('/file-explorer/roots'))return reply({ok:true,roots:[
                  {id:'workspace',label:'Workspace'}, {id:'secondary',label:'Secondary'}]});
                if(path.endsWith('/file-explorer/list') && body.root_id==='workspace'){
                  return new Promise(resolve=>{
                    window.__releaseWorkspace=()=>resolve(reply({ok:true,root_id:'workspace',
                      relative_path:'',entries:[{relative_path:'secret.txt',name:'secret.txt',kind:'file'}]}));
                  });
                }
                if(path.endsWith('/file-explorer/list') && body.root_id==='secondary')
                  return reply({ok:true,root_id:'secondary',relative_path:'',entries:[
                    {relative_path:'public.txt',name:'public.txt',kind:'file'}]});
                throw Error('unexpected-file-endpoint:'+path);
              };
            }''')
            page.set_content(render_semantic_page('file-manager'))
            page.get_by_role('button', name='Load Roots').click()
            page.locator('[data-record-key="workspace"] button').click()
            page.evaluate('''() => {
              window.__inFlight=window.__mcelWorkbench.app.execute('intent:browse');
            }''')
            page.wait_for_function('typeof window.__releaseWorkspace === "function"')
            # Switching semantic root revokes the previous root's visible data.
            page.locator('[data-record-key="secondary"] button').click()
            page.evaluate('window.__releaseWorkspace()')
            assert page.evaluate('async () => (await window.__inFlight).disposition') == 'superseded'
            entries = page.evaluate('window.__mcelWorkbench.app.readState("state:entries")')
            # The new root may have completed its authorized read already.
            # Neither an empty transition nor fresh secondary evidence can
            # ever expose stale workspace entries.
            assert entries is None or [entry['id'] for entry in entries] == ['public.txt']
            assert page.evaluate('window.__mcelWorkbench.app.readState("state:selected-entry")') == ''
            assert page.locator('[data-record-key="secret.txt"]').count() == 0
            assert page.locator('[data-mcel-current-directory]').inner_text() == '/'
            page.get_by_role('button', name='Browse', exact=True).click()
            page.locator('[data-record-key="public.txt"]').wait_for()
            assert page.locator('[data-record-key="secret.txt"]').count() == 0
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        finally:
            browser.close()


def test_late_nested_read_does_not_replace_latest_file_capability_evidence():
    with sync_playwright() as p:
        browser, _ = launch_semantic_browser(p)
        try:
            page = browser.new_page()
            page.add_script_tag(content=(
                Path(__file__).resolve().parents[1] /
                'main_computer/web/applications/scripts/mcel-semantic-read-ports.js'
            ).read_text(encoding='utf-8'))
            result = page.evaluate('''async () => {
              const reply=data=>({ok:true,status:200,json:async()=>data});
              const calls=[], navigations=[];
              let releaseNested;
              const fetcher=async (url,opts) => {
                const body=JSON.parse(opts.body||'{}');calls.push({url,body});
                if(url.endsWith('/roots'))return reply({ok:true,roots:[{id:'workspace',label:'Workspace'}]});
                if(!url.endsWith('/list'))throw Error('unexpected endpoint');
                const parent=body.relative_path;
                if(parent==='')return reply({ok:true,root_id:'workspace',relative_path:'',entries:[
                  {relative_path:'src',name:'src',kind:'directory'}]});
                if(parent==='src/nested')return new Promise(resolve=>{
                  releaseNested=()=>resolve(reply({ok:true,root_id:'workspace',relative_path:'src/nested',entries:[
                    {relative_path:'src/nested/old.js',name:'old.js',kind:'file',suggested_app:'code-editor'}]}));
                });
                if(parent==='src')return reply({ok:true,root_id:'workspace',relative_path:'src',entries:[
                  {relative_path:'src/nested',name:'nested',kind:'directory'},
                  {relative_path:'src/current.js',name:'current.js',kind:'file',suggested_app:'code-editor'}]});
                throw Error('unexpected path');
              };
              const port=McelSemanticReadPorts.create({appId:'semantic-file-manager',fetcher,
                navigate:route=>{navigations.push(route);return true;}});
              const spec={
                'load-roots':['filesystem-read','roots','listRoots'],
                'browse':['filesystem-read','list','listDirectory'],
                'enter-directory':['filesystem-read','enter','openDirectory'],
                'open-with':['application-navigation','open','openSuggestedApp']};
              const req=(name,args)=>{
                const [cap,op,method]=spec[name];
                return {appId:'semantic-file-manager',intentId:'intent:'+name,
                  effectId:'effect:'+name+'.request',capabilityId:'capability:'+cap,
                  operation:op,runtimeMethod:method,risk:'external-read',arguments:args};
              };
              await port.invoke(req('load-roots',{}));
              await port.invoke(req('browse',{rootId:'workspace'}));
              await port.invoke(req('enter-directory',{rootId:'workspace',relativePath:'src'}));
              const delayed=port.invoke(req('enter-directory',{rootId:'workspace',relativePath:'src/nested'}));
              await Promise.resolve();
              await port.invoke(req('enter-directory',{rootId:'workspace',relativePath:'src'}));
              releaseNested(); await delayed;
              const current=req('open-with',{rootId:'workspace',relativePath:'src/current.js'});
              const old=req('open-with',{rootId:'workspace',relativePath:'src/nested/old.js'});
              const currentAuthorized=await port.authorize(current), oldAuthorized=await port.authorize(old);
              if(currentAuthorized)await port.invoke(current);
              return {currentAuthorized,oldAuthorized,navigations,requestCount:calls.length};
            }''')
            assert result == {
                'currentAuthorized': True,
                'oldAuthorized': False,
                'navigations': ['/applications/code-editor'],
                'requestCount': 5,
            }
        finally:
            browser.close()


def test_cached_branch_file_selection_reopens_authorized_parent():
    from tests.test_mcel_semantic_parity_recovery import page_for
    with sync_playwright() as p:
        browser, _ = launch_semantic_browser(p)
        try:
            page = page_for(browser, 'file-manager', width=560)
            page.get_by_role('button', name='Load Roots').click()
            page.locator('[data-record-key="workspace"] button').click()
            page.get_by_role('button', name='Browse', exact=True).click()
            page.locator('[data-record-key="src"] button').click()
            page.locator('[data-record-key="src/nested"] button').click()
            page.locator('[data-record-key="src/nested/hi.js"]').wait_for()
            root_file=page.locator('[data-mcel-tree-branch=""] [data-record-key="README.md"] button')
            reads_before=page.evaluate("() => window.__calls.filter(c=>c.path.endsWith('/file-explorer/list') && c.body.relative_path==='').length")
            assert root_file.is_enabled()
            root_file.click()
            page.wait_for_function('window.__mcelWorkbench.app.readState("state:selected-entry")==="README.md"')
            assert page.locator('[data-mcel-current-directory]').inner_text() == '/'
            # The click must re-list the parent and may not select from cache.
            assert page.evaluate("() => window.__calls.filter(c=>c.path.endsWith('/file-explorer/list') && c.body.relative_path==='').length") == reads_before + 1
            page.get_by_role('button', name='Preview Entry').click()
            page.locator('[data-state="state:preview"]').get_by_text('const a=1;').wait_for()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        finally:
            browser.close()


def test_explorer_tree_has_independent_cached_collapse_and_compact_directory_list():
    from tests.test_mcel_semantic_parity_recovery import page_for
    with sync_playwright() as p:
        browser, _ = launch_semantic_browser(p)
        try:
            page = page_for(browser, 'file-manager', width=1100)
            page.locator('[data-record-key="src"] button').click()
            page.locator('[data-record-key="src/nested"] button').click()
            page.locator('[data-record-key="src/nested/hi.js"]').wait_for()
            assert page.locator('[data-mcel-explorer-view]').count() == 1
            assert page.locator('[data-mcel-directory-columns]').inner_text().split() == ['Name', 'Type', 'Size']
            assert page.locator('[data-mcel-directory-path]').inner_text() == '/src/nested'
            assert page.locator('[data-mcel-directory-entry="src/nested/hi.js"]').count() == 1
            assert page.locator('[data-mcel-tree-branch="src/nested"]').count() == 1
            before = len(page.evaluate('window.__calls'))
            page.locator('[data-mcel-tree-toggle="src"]').click()
            assert page.locator('[data-mcel-tree-branch="src"]').count() == 0
            assert page.locator('[data-mcel-directory-path]').inner_text() == '/src/nested'
            assert len(page.evaluate('window.__calls')) == before
            page.locator('[data-mcel-tree-toggle="src"]').click()
            assert page.locator('[data-mcel-tree-branch="src/nested"]').count() == 1
            assert len(page.evaluate('window.__calls')) == before
            # Native tree arrows traverse visible entries without navigating.
            page.locator('[data-select-key="src"]').focus()
            page.keyboard.press('ArrowDown')
            assert page.evaluate('document.activeElement.getAttribute("data-select-key")') == 'src/nested'
            assert len(page.evaluate('window.__calls')) == before
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        finally:
            browser.close()
