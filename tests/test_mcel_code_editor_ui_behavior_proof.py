"""Independent browser proof of Code Editor UI -> generated MCEL adapter -> state.

External persistence is deliberately refused by the fixture. This establishes
UI routing, read/draft behavior, and effect refusal, not successful real writes.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from main_computer.mcel_application_package_browser_catalog import build_repository_browser_catalog_payload
from main_computer.mcel_application_virtual_assets import build_virtual_mcel_browser_assets
from main_computer.mcel_dsl_compiler import compile_dsl_application

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer/web/applications/scripts"
SOURCE = ROOT / "mcel_apps/code-editor/application.js"


def test_code_editor_ui_actions_go_through_generated_adapter_and_effect_denial_is_observed():
    playwright = pytest.importorskip("playwright.sync_api")
    compiled = compile_dsl_application(SOURCE, write_candidate=False)
    assert compiled.valid and compiled.semantic_fingerprint
    virtual = build_virtual_mcel_browser_assets(ROOT)
    catalog = build_repository_browser_catalog_payload(ROOT)
    record = next(entry for entry in catalog["packages"] if entry["appId"] == "code-editor")
    manifest = json.loads(virtual.read("applications/mcel-packages/code-editor/mcel.runtime.json"))
    assert manifest["source"]["packageFingerprint"] == record["fingerprint"]
    assert manifest["projection"]["fingerprint"] == record["runtimeProjection"]["fingerprint"]
    sources = {
        entry["path"]: virtual.read(f"applications/mcel-packages/code-editor/{entry['path']}").decode("utf-8")
        for entry in manifest["modules"].values()
    }

    with playwright.sync_playwright() as api:
        opts = {"headless": True}
        executable = (os.environ.get("MCEL_CHROMIUM_EXECUTABLE") or os.environ.get("CHROMIUM_EXECUTABLE")
                      or shutil.which("chromium") or shutil.which("chromium-browser"))
        if executable:
            opts.update({"executable_path": executable, "args": ["--no-sandbox", "--disable-dev-shm-usage"]})
        browser = api.chromium.launch(**opts)
        try:
            page = browser.new_page()
            page.set_content('<!doctype html><html><body><div id="code-editor-app"></div></body></html>')
            # Intercept only external I/O; application core, facade, generated
            # adapter, and host-bound binding authority all run unmodified.
            page.evaluate('''() => {
              // about:blank is not a secure context. Supply a deterministic
              // digest exclusively to exercise the editor's open/read path;
              // cryptographic correctness is outside this behavioral test.
              Object.defineProperty(window.crypto, 'subtle', {configurable:true,
                value:{digest:async () => new Uint8Array(32).buffer}});
              window.__proofCalls = [];
              window.__intentEvents = [];
              window.fetch = async (url, init = {}) => {
                const target = String(url);
                window.__proofCalls.push({url: target, body: init.body || ''});
                if (target.endsWith('/api/applications/editor/files')) {
                  return new Response(JSON.stringify({ok:true,repo_dir:'.',files:[{
                    path:'src/proof.js',name:'proof.js',kind:'file',bytes:8
                  }]}), {status:200,headers:{'Content-Type':'application/json'}});
                }
                if (target.endsWith('/api/applications/editor/read')) {
                  return new Response(JSON.stringify({ok:true,repo_dir:'.',files:[{
                    path:'src/proof.js',content:'before();'
                  }]}), {status:200,headers:{'Content-Type':'application/json'}});
                }
                // No external mutation is permitted by this fixture.
                return new Response(JSON.stringify({ok:false,error:'proof-denied-write'}), {
                  status:403,headers:{'Content-Type':'application/json'}
                });
              };
              document.querySelector('#code-editor-app').addEventListener('mcel:host-bound-intent',
                e => window.__intentEvents.push(e.detail.intentName));
            }''')
            for name in ("code-editor-core.js", "code-editor-view-model.js", "code-editor-capabilities.js", "code-editor.js"):
                page.add_script_tag(path=str(SCRIPTS / name))
            page.evaluate("""() => {window.McelApplicationPackages = {catalogFingerprint:'fixture',getPackage:()=>null,listPackages:()=>[]}}""")
            page.add_script_tag(path=str(SCRIPTS / "mcel-host-bound-application-runtime.js"))

            # Before binding, the real UI must refuse to execute a direct
            # operation through the unverified facade.
            page.locator("#file-map-refresh").click()
            before = page.evaluate('''() => ({calls: window.__proofCalls.length,
                route: document.querySelector('#code-editor-app').dataset.codeEditorIntentRoute})''')
            assert before == {"calls": 0, "route": "blocked-no-mcel-mount"}
            page.evaluate('''async ({record,manifest,catalogFingerprint,sources}) => {
              const catalog={catalogFingerprint,getPackage:id=>id===record.appId?record:null,
                listPackages:()=>[record]};
              await window.McelHostBoundApplicationRuntime.mountApplication({
                appId:'code-editor',manifest,root:document.querySelector('#code-editor-app'),
                packageCatalog:catalog,
                moduleLoader:async (_url,entry)=>{
                  const bytes=new TextEncoder().encode(sources[entry.path]);
                  let binary=''; for (const byte of bytes) binary+=String.fromCharCode(byte);
                  return import(`data:text/javascript;base64,${btoa(binary)}`);
                }
              });
            }''', {"record": record,"manifest": manifest,
                   "catalogFingerprint": catalog["catalogFingerprint"],"sources":sources})
            page.locator("#file-map-refresh").click()
            page.wait_for_function("window.MainComputerCodeEditorRuntime.state().receipts.some(r=>r.intent==='inspectWorkspace' && r.ok)")
            page.locator('[data-code-editor-runtime-file]').first.dblclick()
            page.wait_for_function("window.MainComputerCodeEditorRuntime.state().activeFile?.path==='src/proof.js'", timeout=8000)
            page.locator("#code-studio-runtime-draft").fill("after();")
            page.wait_for_function("window.MainComputerCodeEditorRuntime.state().draft?.text==='after();'")
            # Save is invoked via the adapter, but the external capability
            # refuses it. No source should be reported successfully saved.
            page.locator("#code-studio-commit-runtime").click()
            page.wait_for_function("window.MainComputerCodeEditorRuntime.state().receipts.some(r=>r.intent==='saveFile')")
            result = page.evaluate('''() => {
              const root=document.querySelector('#code-editor-app');
              const state=window.MainComputerCodeEditorRuntime.state();
              const mount=window.McelHostBoundApplicationRuntime.getMount('code-editor');
              return {route:root.dataset.codeEditorIntentRoute,
                mounted:mount?.active===true,bindings:mount?.bindingReport.intentCount,
                events:window.__intentEvents,
                calls:window.__proofCalls.map(c=>c.url),
                activePath:state.activeFile?.path, activeContent:state.activeFile?.content,
                draftText:state.draft?.text,dirty:state.draft?.dirty,
                receipts:state.receipts.slice(0,5).map(r=>({intent:r.intent,ok:r.ok})),
                lastError:state.lastError?.message||state.lastError||null};
            }''')
            assert result["mounted"] is True
            assert result["bindings"] == 8
            assert result["route"] == "generated-adapter"
            for action in ("inspectWorkspace", "openFile", "editDraft", "saveFile"):
                assert action in result["events"], result
                assert any(receipt["intent"] == action for receipt in result["receipts"]), result
            assert result["activePath"] == "src/proof.js"
            assert result["activeContent"] == "before();"
            assert result["draftText"] == "after();"
            assert result["dirty"] is True
            assert result["receipts"][0] == {"intent": "saveFile", "ok": False}
            assert result["calls"][:2] == [
                "/api/applications/editor/files", "/api/applications/editor/read"
            ]
            assert result["calls"][2:] == ["/api/applications/editor/project/file/save"]
            assert "proof-denied-write" in str(result["lastError"])
            # Once explicitly unmounted, all future UI actions fail closed.
            page.evaluate("window.McelHostBoundApplicationRuntime.getMount('code-editor').unmount()")
            page.locator("#file-map-refresh").click()
            after = page.evaluate('''() => ({calls:window.__proofCalls.length,
              route:document.querySelector('#code-editor-app').dataset.codeEditorIntentRoute})''')
            assert after == {"calls": 3, "route": "blocked-no-mcel-mount"}
        finally:
            browser.close()
