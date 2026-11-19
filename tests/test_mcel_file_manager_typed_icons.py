"""File Manager icon contract: real source, typed semantics, real rendered controls.

Expected identities are independent test data; the production resolver never
loads this oracle. Runtime capability bindings and endpoint behavior must remain
unchanged when the icon presentation is active.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from main_computer.mcel_semantic_other_apps_host import compile_semantic_app, render_semantic_page

ROOT = Path(__file__).resolve().parents[1]
ORACLE = json.loads((ROOT / "tests/fixtures/mcel-semantic-icon-oracle.v1.json").read_text())

# These expectations are taken from application functionality, not the icon
# resolver. A same-binding/wrong-meaning classification must fail this oracle.
OPERATIONS = {
    "load-roots": ("roots", "refresh", "folder", "listRoots"),
    "browse": ("list", "open", "folder", "listDirectory"),
    "enter-directory": ("enter", "open", "folder", "openDirectory"),
    "search": ("search", "search", "file", "searchEntries"),
    "preview-entry": ("preview", "inspect", "file", "readPreview"),
    "open-with": ("open", "open", "application", "openSuggestedApp"),
}


def test_file_manager_uses_validated_typed_operations_with_original_execution_bindings():
    ir = compile_semantic_app("file-manager")
    intents = {i["id"].removeprefix("intent:"): i for i in ir["intents"]}
    source = (ROOT / "tools/fixtures/mcel_semantic_other_apps/file-manager/application.js").read_text()
    assert "action." not in source and "navigation." not in source and "resource." not in source
    for name, (binding, kind, target, method) in OPERATIONS.items():
        i = intents[name]
        assert i["operation"] == binding
        assert i["runtimeMethod"] == method
        assert i["typedOperation"] == {
            "schema": "mcel.typed-operation.v1", "kind": kind, "target": target
        }
        assert i["risk"] == "external-read" and len(i["effectRefs"]) == 1
    for name in ("choose-root", "choose-entry"):
        assert "typedOperation" not in intents[name]


def test_file_manager_icons_match_oracle_in_real_chromium_and_do_not_change_execution():
    from playwright.sync_api import sync_playwright

    expectations = ORACLE["applications"]["file-manager"]
    assert expectations["source"] == "tools/fixtures/mcel_semantic_other_apps/file-manager/application.js"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=shutil.which("chromium") or None)
        page = browser.new_page(viewport={"width": 1200, "height": 850})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.evaluate("""() => {
              window.fmCalls=[];
              window.fetch=async (url,options={}) => {
                window.fmCalls.push({url:String(url),body:JSON.parse(options.body||'{}')});
                let payload;
                if(String(url).endsWith('/file-explorer/roots'))
                  payload={ok:true,roots:[{id:'workspace',label:'Workspace'},{id:'scratch',label:'Scratch'}]};
                else if(String(url).endsWith('/file-explorer/list'))
                  payload={ok:true,entries:[{relative_path:'docs',name:'docs',kind:'directory'},
                    {relative_path:'README.md',name:'README.md',kind:'file'}]};
                else if(String(url).endsWith('/file-explorer/search'))
                  payload={ok:true,results:[]};
                else if(String(url).endsWith('/file-explorer/read'))
                  payload={ok:true,readable:true,content:'Preview content'};
                else payload={ok:false,error:'unexpected'};
                return {ok:payload.ok,status:payload.ok?200:404,json:async()=>payload};
              };
            }""")
            page.set_content(render_semantic_page("file-manager", ROOT, promoted=True),
                             wait_until="domcontentloaded", timeout=30000)
            page.locator('[data-mcel-generated-action="refresh-location"]').wait_for()
            page.wait_for_function("""() => {
              const root=document.getElementById('mcel-workbench');
              return [...root.querySelectorAll('button[data-mcel-generated-action]')]
                .filter(b=>b.querySelector('[data-mcel-icon-identity]')).length>=4;
            }""")
            actual = page.evaluate("""() => {
              const root=document.getElementById('mcel-workbench');
              const nodes={};
              for(const holder of root.querySelectorAll('[data-mcel-node][data-node-kind="control"], [data-mcel-node][data-node-kind="form"]')){
                const key=holder.getAttribute('data-mcel-node').split('.').pop();
                const button=holder.getAttribute('data-node-kind')==='form'
                  ?holder.querySelector('form button[type="submit"]')
                  :holder.querySelector('button[data-intent]');
                nodes[key]=button?.querySelector(':scope > [data-mcel-icon-identity]')?.getAttribute('data-mcel-icon-identity')||null;
              }
              const generated={};
              for(const button of root.querySelectorAll('button[data-mcel-generated-action]')){
                const kind=button.getAttribute('data-mcel-generated-action');
                const icon=button.querySelector(':scope > [data-mcel-icon-identity]');
                (generated[kind]??=[]).push({icon:icon?.getAttribute('data-mcel-icon-identity')||null,
                  slots:button.querySelectorAll('[data-mcel-icon-core="slot"]').length,
                  decorative:icon?.getAttribute('aria-hidden')||null});
              }
              return {nodes,generated,errors:[...root.querySelectorAll('button')]
                .filter(b=>b.querySelectorAll('[data-mcel-icon-core="slot"]').length>1).map(b=>b.outerHTML),
                report:McelSemanticIconCore.inspect(root),
                identity:JSON.parse(document.getElementById('mcel-workbench-ir').textContent).application.source.file};
            }""")
            assert actual["identity"] == expectations["source"]
            assert actual["nodes"] == expectations["nodes"], actual
            assert not actual["errors"], actual
            assert actual["report"]["resolved"] == 6, actual
            assert actual["report"]["generatedResolved"] >= 4, actual
            for kind, icon_id in expectations["generatedControls"].items():
                assert kind in actual["generated"], (kind, actual)
                assert all(entry == {"icon": icon_id, "slots": 1, "decorative": "true"}
                           for entry in actual["generated"][kind]), (kind, actual["generated"][kind])

            # Verify a real capability request still takes its original endpoint.
            before=page.evaluate('window.fmCalls.length')
            page.get_by_role('button',name='Browse',exact=True).click()
            page.wait_for_function('(n) => window.fmCalls.length > n',arg=before)
            assert page.evaluate('window.fmCalls.at(-1).url.endsWith("/file-explorer/list")')

            # The selected location toggles its generated icon by effective
            # state, not a heuristic over the button's text.
            page.locator('button[data-select-key="scratch"]').click()
            page.wait_for_function("""() => {
              const b=document.querySelector('button[data-select-key="scratch"]');
              return b?.getAttribute('data-mcel-generated-action')==='refresh-location' &&
                b.querySelector('[data-mcel-icon-identity="action.refresh"]')!==null;
            }""")
            assert page.evaluate("""() => {
              const b=document.querySelector('button[data-select-key="workspace"]');
              return b?.getAttribute('data-mcel-generated-action')==='open-location' &&
                b.querySelector('[data-mcel-icon-identity="resource.folder-open"]')!==null;
            }""")
            assert not errors, errors
        finally:
            browser.close()
