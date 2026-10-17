"""Real Code Editor UI -> generated MCEL adapter -> transaction-backed file effect.

The browser talks to an actual ViewportServer whose workspace is a disposable
pytest directory. Filesystem bytes and server-issued receipts are checked
independently, including an intervening external modification.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from pathlib import Path

import pytest

from main_computer.config import MainComputerConfig
from main_computer.viewport import ViewportServer
from main_computer.mcel_application_package_browser_catalog import build_repository_browser_catalog_payload
from main_computer.mcel_application_virtual_assets import build_virtual_mcel_browser_assets
from main_computer.mcel_dsl_compiler import compile_dsl_application

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer/web/applications/scripts"
SOURCE = ROOT / "mcel_apps/code-editor/application.js"


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@pytest.fixture
def real_editor_workspace(tmp_path: Path):
    workspace = tmp_path / "main_computer_test"
    source = workspace / "src/proof.js"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"before();\n")
    shutil.copy2(ROOT / "new_patch.py", workspace / "new_patch.py")
    server = ViewportServer(("127.0.0.1", 0), MainComputerConfig(workspace=workspace))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", source
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_code_editor_real_write_receipt_and_stale_save_refusal(real_editor_workspace):
    playwright = pytest.importorskip("playwright.sync_api")
    base, source = real_editor_workspace
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
        launch = {"headless": True}
        executable = (os.environ.get("MCEL_CHROMIUM_EXECUTABLE") or os.environ.get("CHROMIUM_EXECUTABLE")
                      or shutil.which("chromium") or shutil.which("chromium-browser"))
        if executable:
            launch.update({"executable_path": executable, "args": ["--no-sandbox", "--disable-dev-shm-usage"]})
        browser = api.chromium.launch(**launch)
        try:
            page = browser.new_page()
            page.set_default_timeout(10000)
            # Browser localhost navigation is blocked in some CI sandboxes.
            # Bridge only HTTP transport; the application capability and the
            # real ViewportServer handler remain unmodified, and the test reads
            # actual workspace bytes after the request completes.
            def backend_request(_source, payload):
                target = str(payload["url"])
                if not target.startswith("/api/applications/editor/"):
                    raise AssertionError(f"unexpected Code Editor endpoint: {target}")
                request = Request(base + target, data=str(payload["body"]).encode("utf-8"),
                                  headers={"Content-Type": "application/json"}, method="POST")
                try:
                    with urlopen(request, timeout=25) as response:
                        return {"status": response.status, "text": response.read().decode("utf-8")}
                except HTTPError as error:
                    return {"status": error.code, "text": error.read().decode("utf-8")}

            page.expose_binding("__mcelBackend", backend_request)
            page.expose_binding("__mcelSha256", lambda _source, payload:
                                list(hashlib.sha256(bytes(payload)).digest()))
            page.set_content('<!doctype html><html><body><div id="code-editor-app"></div></body></html>')
            page.evaluate('''() => {
              // about:blank has no subtle crypto; delegate the real SHA-256
              // computation to Python, not a fabricated fixed digest.
              Object.defineProperty(window.crypto, 'subtle', {configurable:true,
                value:{digest: async (_algorithm, buffer) =>
                  new Uint8Array(await window.__mcelSha256(Array.from(new Uint8Array(buffer)))).buffer}});
              window.fetch = async (url, init = {}) => {
                const reply = await window.__mcelBackend({url: String(url), body: init.body || "{}"});
                return new Response(reply.text, {status: reply.status,
                  headers:{'Content-Type':'application/json'}});
              };
            }''')
            page.evaluate('''() => {
              window.__intentEvents = [];
              document.querySelector('#code-editor-app').addEventListener('mcel:host-bound-intent',
                e => window.__intentEvents.push(e.detail.intentName));
            }''')
            for name in ("code-editor-core.js", "code-editor-view-model.js", "code-editor-capabilities.js", "code-editor.js"):
                page.add_script_tag(path=str(SCRIPTS / name))
            page.evaluate("() => {window.McelApplicationPackages = {catalogFingerprint:'fixture',getPackage:()=>null,listPackages:()=>[]}}")
            page.add_script_tag(path=str(SCRIPTS / "mcel-host-bound-application-runtime.js"))
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
            }''', {"record": record, "manifest": manifest,
                   "catalogFingerprint": catalog["catalogFingerprint"], "sources": sources})
            page.locator("#file-map-search").fill("proof")
            page.locator("#file-map-refresh").click()
            page.wait_for_function("window.MainComputerCodeEditorRuntime.state().workspace.files.length > 0")
            page.locator('[data-code-editor-runtime-file]').first.dblclick()
            page.wait_for_function("window.MainComputerCodeEditorRuntime.state().activeFile?.path==='src/proof.js'")
            page.locator("#code-studio-runtime-draft").fill("after();\n")
            page.wait_for_function("window.MainComputerCodeEditorRuntime.state().draft?.text==='after();\\n'")
            page.locator("#code-studio-commit-runtime").click()
            page.wait_for_function('''() => window.MainComputerCodeEditorRuntime.state().receipts.some(
                r => r.intent==='saveFile')''', timeout=30000)
            result = page.evaluate('''() => {
              const s=window.MainComputerCodeEditorRuntime.state();
              const r=s.receipts.find(r=>r.intent==='saveFile');
              return {dirty:s.draft?.dirty, active:s.activeFile?.content, receipt:r,
                events:window.__intentEvents,
                mounted:window.McelHostBoundApplicationRuntime.getMount('code-editor')?.active};
            }''')
            assert source.read_bytes() == b"after();\n"
            assert result["mounted"] is True
            assert result["dirty"] is False, result
            assert result["active"] == "after();\n"
            for action in ("inspectWorkspace", "openFile", "editDraft", "saveFile"):
                assert action in result["events"]
            receipt = result["receipt"]
            assert receipt["ok"] is True and receipt["mutationAllowed"] is True
            assert receipt["path"] == "src/proof.js"
            effect = receipt["raw"]["receipt"]
            assert effect["ok"] is True and effect["rollback_required"] is False
            assert len(effect["files"]) == 1
            assert effect["files"][0]["before_sha256"] == sha("before();\n")
            assert effect["files"][0]["after_sha256"] == sha("after();\n")
            assert effect["files"][0]["written_sha256"] == sha(source.read_text(encoding="utf-8"))

            # Concurrent modification invalidates the original base hash.
            page.locator("#code-studio-runtime-draft").fill("third();\n")
            page.wait_for_function("window.MainComputerCodeEditorRuntime.state().draft?.text==='third();\\n'")
            source.write_bytes(b"concurrent();\n")
            page.locator("#code-studio-commit-runtime").click()
            page.wait_for_function('''() => window.MainComputerCodeEditorRuntime.state().receipts.filter(
                r=>r.intent==='saveFile').length===2''', timeout=30000)
            denied = page.evaluate('''() => {
              const s=window.MainComputerCodeEditorRuntime.state();
              return {draft:s.draft?.text, dirty:s.draft?.dirty,
                active:s.activeFile?.content, receipt:s.receipts.find(r=>r.intent==='saveFile'),
                error:s.lastError?.message};
            }''')
            assert source.read_bytes() == b"concurrent();\n"
            assert denied["draft"] == "third();\n" and denied["dirty"] is True
            assert denied["active"] == "after();\n"
            assert denied["receipt"]["ok"] is False
            assert "source" in (denied["error"] or "").lower()
            # Nonmutating draft controls must also use the mounted adapter.
            page.locator("#code-studio-restore-live-workspace").click()
            page.wait_for_function("window.MainComputerCodeEditorRuntime.state().draft?.dirty===false")
            page.locator("#code-studio-clear-live-workspace").click()
            page.wait_for_function("window.MainComputerCodeEditorRuntime.state().activeFile===null")
            later = page.evaluate('''() => ({events:window.__intentEvents,
              receipts:window.MainComputerCodeEditorRuntime.state().receipts.slice(0,2)})''')
            assert later["events"][-2:] == ["discardDraft", "closeFile"]
            assert [item["intent"] for item in later["receipts"]] == ["closeFile", "discardDraft"]
            assert source.read_bytes() == b"concurrent();\n"
        finally:
            browser.close()
