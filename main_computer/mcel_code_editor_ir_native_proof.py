"""Fresh Code Editor IR-native intent proof backed by an isolated real workspace.

All eight compiled intentions run through the projected adapter, generic mount,
unchanged Code Editor facade/capabilities and a real ViewportServer. Source
writes are independently verified against on-disk bytes. No real repository
file is offered to the browser for mutation.
"""
from __future__ import annotations

import hashlib
import contextlib
import io
import json
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from main_computer.mcel_application_package_browser_catalog import build_repository_browser_catalog_payload
from main_computer.mcel_application_runtime_projection import build_runtime_projection_set
from main_computer.mcel_application_virtual_assets import build_virtual_mcel_browser_assets
from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_evidence_provenance import build_repository_provenance
from main_computer.config import MainComputerConfig

APP_ID = "code-editor"
SCHEMA = "mcel.code-editor-real-effects-ir-proof.v1"
_EXPECTED = frozenset({"inspectWorkspace", "openFile", "editDraft", "saveFile",
                       "discardDraft", "closeFile", "previewAiderPlan", "applyReviewedPatch"})
_SCRIPTS = ("code-editor-core.js", "code-editor-view-model.js",
            "code-editor-capabilities.js", "code-editor.js",
            "mcel-self-diagnosis.js", "mcel-semantic-layout-inference.js",
            "mcel-host-bound-application-runtime.js")


class CodeEditorIrNativeProofError(RuntimeError):
    pass


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _require(condition: Any, explanation: str) -> None:
    if not condition:
        raise CodeEditorIrNativeProofError(explanation)


# The test sets up a real server; only localhost HTTP transport is bridged
# because about:blank / local Chromium CI cannot necessarily fetch loopback.
# Responses and transaction receipts come exclusively from ViewportServer.
_BROWSER_RUN = r'''async ({record,manifest,catalogFingerprint,sources}) => {
  const catalog = {catalogFingerprint, getPackage:id=>id===record.appId?record:null,
    listPackages:()=>[record]};
  window.__mcelProofEvents=[];
  const root=document.querySelector('#code-editor-app');
  root.addEventListener('mcel:host-bound-intent', e=>window.__mcelProofEvents.push(e.detail.intentName));
  const mount=await window.McelHostBoundApplicationRuntime.mountApplication({
    appId:'code-editor',manifest,root,packageCatalog:catalog,
    moduleLoader:async (_url,entry)=>{
      const source=sources[entry.path];
      if(typeof source!=='string') throw new Error('Missing generated module '+entry.path);
      const bytes=new TextEncoder().encode(source);
      let binary='';for(const byte of bytes) binary+=String.fromCharCode(byte);
      return import(`data:text/javascript;base64,${btoa(binary)}`);
    }
  });
  const runtime=window.MainComputerCodeEditorRuntime;
  const snapshots={};
  async function act(name,payload={}){
    const response=await mount.invoke(name,payload);
    const state=runtime.state();
    const receipt=state.receipts.find(r=>r.intent===name);
    snapshots[name]={ok:response?.ok===true,receipt:receipt?JSON.parse(JSON.stringify(receipt)):null,
      dirty:state.draft?.dirty,activePath:state.activeFile?.path||null,
      draftText:state.draft?.text||null,pending:state.pendingPatch?.state||null,
      handle:state.pendingPatch?.handle||null,lastError:state.lastError?.message||String(state.lastError||'')};
    return state;
  }
  await act('inspectWorkspace',{repoDir:'.',query:'proof'});
  await act('openFile',{path:'src/proof.js'});
  await act('editDraft',{path:'src/proof.js',text:'after();\n'});
  await act('saveFile',{path:'src/proof.js',text:'after();\n'});
  // Return here for Python to check real disk bytes, before the stale write.
  return {snapshots,events:window.__mcelProofEvents,active:mount.active,
    binding:mount.bindingReport,adapterBindings:mount.modules.adapter.bindings,
    rootStatus:root.dataset.mcelHostBoundStatus};
}'''

_BROWSER_PREPARE = r'''async () => {
  const mount=window.McelHostBoundApplicationRuntime.getMount('code-editor');
  const runtime=window.MainComputerCodeEditorRuntime;
  const snapshots={};
  async function act(name,payload={}){
    const response=await mount.invoke(name,payload);
    const state=runtime.state();
    const receipt=state.receipts.find(r=>r.intent===name);
    snapshots[name]={ok:response?.ok===true,receipt:receipt?JSON.parse(JSON.stringify(receipt)):null,
      dirty:state.draft?.dirty,activePath:state.activeFile?.path||null,
      draftText:state.draft?.text||null,pending:state.pendingPatch?.state||null,
      handle:state.pendingPatch?.handle||null,lastError:state.lastError?.message||String(state.lastError||'')};
    return state;
  }
  // External on-disk modification has occurred, invalidating the base hash.
  await act('editDraft',{path:'src/proof.js',text:'third();\n'});
  const stale=await act('saveFile',{path:'src/proof.js',text:'third();\n'});
  snapshots.staleSave={dirty:stale.draft?.dirty,active:stale.activeFile?.content,
    lastError:stale.lastError?.message||String(stale.lastError||''),
    receipt:stale.receipts.find(r=>r.intent==='saveFile')};
  await act('discardDraft');
  await act('closeFile');
  await act('openFile',{path:'src/proof.js'});
  await act('editDraft',{path:'src/proof.js',text:'reviewed();\n'});
  await act('previewAiderPlan',{projectRoot:'.'});
  // A review is not an authorization: first attempt must be refused locally.
  const blocked=await act('applyReviewedPatch',{reviewed:true,approved:false});
  snapshots.unapproved={error:blocked.lastError?.message||String(blocked.lastError||''),
    receipt:blocked.receipts.find(r=>r.intent==='applyReviewedPatch')};
  return {snapshots,events:window.__mcelProofEvents,receipts:runtime.state().receipts,
    bound:mount.active,rootStatus:document.querySelector('#code-editor-app').dataset.mcelHostBoundStatus};
}'''

_BROWSER_APPLY = r'''async () => {
  const mount=McelHostBoundApplicationRuntime.getMount('code-editor');
  const result=await mount.invoke('applyReviewedPatch',{reviewed:true,approved:true});
  const state=MainComputerCodeEditorRuntime.state();
  return {ok:result?.ok===true, receipt:state.receipts.find(r=>r.intent==='applyReviewedPatch'),
    pending:state.pendingPatch?.state,events:window.__mcelProofEvents, bound:mount.active,
    rootStatus:document.querySelector('#code-editor-app').dataset.mcelHostBoundStatus};
}'''


def _fresh_browser_effects(repo: Path, record: Any, compiled: Any, headed: bool) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise CodeEditorIrNativeProofError("Playwright required for fresh Code Editor proof") from exc
    virtual = build_virtual_mcel_browser_assets(repo)
    catalog = build_repository_browser_catalog_payload(repo)
    matched = [entry for entry in catalog.get("packages", []) if entry.get("appId") == APP_ID]
    _require(len(matched) == 1, "Browser catalog Code Editor identity is not unique")
    _require(matched[0].get("fingerprint") == record.fingerprint, "Browser package fingerprint differs")
    manifest = json.loads(virtual.read("applications/mcel-packages/code-editor/mcel.runtime.json"))
    projection = [item for item in build_runtime_projection_set(repo).projections if item.app_id == APP_ID]
    _require(len(projection) == 1 and projection[0].mount_mode == "host-bound", "Host-bound projection missing")
    _require(manifest["source"]["packageFingerprint"] == record.fingerprint, "Generated manifest package drift")
    _require(manifest["projection"]["fingerprint"] == projection[0].fingerprint, "Generated manifest projection drift")
    sources = {entry["path"]:virtual.read("applications/mcel-packages/code-editor/" + entry["path"]).decode("utf-8")
               for entry in manifest["modules"].values()}
    from main_computer.viewport import ViewportServer

    with tempfile.TemporaryDirectory(prefix="mcel-code-editor-proof-") as temporary:
        workspace = Path(temporary) / "main_computer_test"
        source = workspace / "src/proof.js"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"before();\n")
        shutil.copy2(repo / "new_patch.py", workspace / "new_patch.py")
        server = ViewportServer(("127.0.0.1", 0), MainComputerConfig(workspace=workspace))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        request_log: list[str] = []
        def backend_request(_source: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
            target = str(payload.get("url", ""))
            if not target.startswith("/api/applications/editor/"):
                raise CodeEditorIrNativeProofError("Unexpected Code Editor HTTP endpoint: " + target)
            request_log.append(target)
            request = Request(base + target, data=str(payload.get("body") or "{}").encode("utf-8"),
                              headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urlopen(request, timeout=25) as response:
                    return {"status": response.status, "text": response.read().decode("utf-8")}
            except HTTPError as error:
                return {"status": error.code, "text": error.read().decode("utf-8")}
        try:
            with sync_playwright() as playwright:
                executable = (os.environ.get("MCEL_CHROMIUM_EXECUTABLE") or os.environ.get("CHROMIUM_EXECUTABLE")
                              or shutil.which("chromium") or shutil.which("chromium-browser"))
                launch: dict[str, Any] = {"headless": not headed}
                if executable:
                    launch.update({"executable_path": executable, "args": ["--no-sandbox", "--disable-dev-shm-usage"]})
                browser = playwright.chromium.launch(**launch)
                try:
                    page = browser.new_page(viewport={'width':1220, 'height':920})
                    page.set_default_timeout(30000)
                    page.expose_binding("__mcelBackend", backend_request)
                    page.expose_binding("__mcelSha256", lambda _source, payload: list(hashlib.sha256(bytes(payload)).digest()))
                    page.set_content('<!doctype html><html><head><style>' +
                        (repo / 'main_computer/web/applications/styles/code-editor.css').read_text(encoding='utf-8') +
                        '</style></head><body data-active-app="code-editor">' +
                        (repo / 'main_computer/web/applications/apps/code-editor.html').read_text(encoding='utf-8') +
                        '</body></html>')
                    page.evaluate('''() => {
                      Object.defineProperty(window.crypto,'subtle',{configurable:true,
                        value:{digest:async (_algorithm,bytes)=>new Uint8Array(
                          await window.__mcelSha256(Array.from(new Uint8Array(bytes)))).buffer}});
                      window.fetch=async (url,init={})=>{
                        const response=await window.__mcelBackend({url:String(url),body:init.body||'{}'});
                        return new Response(response.text,{status:response.status,
                          headers:{'Content-Type':'application/json'}});
                      };
                    }''')
                    # The canonical HTML includes a demonstration authored-source snapshot.
                    # Empty it for this proof so inspection/read must go to the real isolated server.
                    page.evaluate("""() => {const el=document.querySelector('#code-studio-source-editor');
                      if(el){el.value='';el.textContent='';}}""")
                    for script in _SCRIPTS:
                        page.add_script_tag(path=str(repo / "main_computer/web/applications/scripts" / script))
                    page.evaluate('''() => {const root=document.querySelector('#code-editor-app');
                      root.style.setProperty('display','block','important');
                      root.style.height='800px';root.dataset.mcelLayoutLive='true';
                      root.querySelector('.code-studio-activitybar').style.setProperty('display','grid','important');
                    }''')
                    initial = page.evaluate(_BROWSER_RUN, {"record": matched[0], "manifest": manifest,
                        "catalogFingerprint": catalog["catalogFingerprint"], "sources": sources})
                    after_save = source.read_bytes()
                    _require(after_save == b"after();\n", "Real save did not write expected source bytes")
                    source.write_bytes(b"concurrent();\n")
                    prepared = page.evaluate(_BROWSER_PREPARE)
                    after_refusal_and_preview = source.read_bytes()
                    applied = page.evaluate(_BROWSER_APPLY)
                    after_patch = source.read_bytes()
                    mount_data = {"initial": initial, "prepared": prepared, "applied": applied,
                                  "afterSaveSha256": _sha(after_save),
                                  "afterPreviewSha256": _sha(after_refusal_and_preview),
                                  "afterApplySha256": _sha(after_patch), "requestPaths": request_log}
                    return mount_data
                finally:
                    browser.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()


def run_code_editor_ir_native_intent_proof(*, repo: Path, record: Any,
    acceptance: Mapping[str, Any] | None = None, observation: Mapping[str, Any] | None = None,
    headed: bool = False, node_probe_runner: Any = None, browser_probe_runner: Any = None) -> dict[str, Any]:
    del node_probe_runner
    repo = Path(repo).resolve()
    _require(getattr(record, "app_id", None) == APP_ID, "Wrong package for Code Editor proof")
    source = repo / "mcel_apps/code-editor/application.js"
    compiled = compile_dsl_application(source, write_candidate=False)
    _require(compiled.valid and compiled.normalized_ir is not None, "Canonical Code Editor DSL did not compile")
    ir = compiled.normalized_ir
    intents = {i["sourceName"]: i for i in ir.get("intents", [])}
    _require(set(intents) == _EXPECTED, "Declared Code Editor intent set changed")
    _require(all(i.get("runtimeMethod") == name for name, i in intents.items()), "Generated intent method drift")
    effects = {e["id"]: e for e in ir.get("effects", [])}
    for name, item in intents.items():
        refs = [r.get("ref") for r in item.get("effectRefs", [])]
        if item["risk"] == "local-state":
            _require(not refs, f"Local intent {name} incorrectly requires capability")
        else:
            _require(len(refs) == 1 and refs[0] in effects, f"Capability effect not closed: {name}")
            _require((effects[refs[0]].get("owner") or {}).get("ref") == item["id"],
                     f"Effect ownership drift for {name}")
    scenarios = ir.get("scenarios", [])
    _require(len(scenarios) >= len(_EXPECTED), "Compiled DSL lost Code Editor scenarios")
    _require(all((s.get("intent") or {}).get("ref") in {i["id"] for i in intents.values()}
                 for s in scenarios), "Scenario references unbound intent")
    _require(browser_probe_runner is None, "Production Code Editor proof does not accept substituted browser truth")
    fingerprint_before = build_repository_provenance(repo)["fingerprint"]
    # ViewportServer logs its isolated test requests to stdout; keep --json
    # CLI output machine-readable while preserving fail-closed exceptions.
    with contextlib.redirect_stdout(io.StringIO()):
        evidence = _fresh_browser_effects(repo, record, compiled, headed)
    _require(build_repository_provenance(repo)["fingerprint"] == fingerprint_before,
             "Authoritative repository changed during Code Editor effect proof")
    first = evidence["initial"]
    prepared = evidence["prepared"]
    applied_result = evidence["applied"]
    s = first["snapshots"]
    r = prepared["snapshots"]
    _require(first["active"] is True and prepared["bound"] is True and applied_result["bound"] is True and
             first["rootStatus"] == prepared["rootStatus"] == applied_result["rootStatus"] == "mounted", "Generic mount lost authority")
    _require(first["binding"]["status"] == "bound" and first["binding"]["intentCount"] == len(_EXPECTED),
             "Generated adapter binding is incomplete")
    _require(set(first["adapterBindings"]) == _EXPECTED, "Generated runtime adapter intent coverage is not exact")
    _require(s["inspectWorkspace"]["ok"] and s["openFile"]["activePath"] == "src/proof.js",
             "File inspection/open not observed")
    _require(s["editDraft"]["dirty"] is True and s["saveFile"]["dirty"] is False,
             "Draft/save state transition not observed")
    save = s["saveFile"]["receipt"] or {}
    files = (((save.get("raw") or {}).get("receipt") or {}).get("files") or [])
    _require(save.get("ok") is True and save.get("mutationAllowed") is True
             and len(files) == 1 and files[0]["before_sha256"] == _sha(b"before();\n")
             and files[0]["after_sha256"] == evidence["afterSaveSha256"]
             and files[0]["written_sha256"] == evidence["afterSaveSha256"],
             "Save lacks verified transaction receipt or byte-level evidence")
    stale = r["staleSave"]
    _require(stale["dirty"] is True and stale["active"] == "after();\n"
             and stale["receipt"]["ok"] is False and "source" in stale["lastError"].lower(),
             "Stale source was not rejected")
    _require(r["discardDraft"]["dirty"] is False and r["closeFile"]["activePath"] is None,
             "Discard/close local effects not observed")
    preview = r["previewAiderPlan"]["receipt"] or {}
    _require(preview.get("ok") is True and preview.get("mutationAllowed") is False
             and r["previewAiderPlan"]["pending"] == "prepared", "Plan preview was not nonmutating")
    blocked = r["unapproved"]
    _require(blocked["receipt"]["ok"] is False and "approved=true" in blocked["error"].lower(),
             "Unapproved reviewed patch was not blocked")
    _require(evidence["afterPreviewSha256"] == _sha(b"concurrent();\n"),
             "Stale refusal or read-only preview modified source bytes")
    applied = applied_result["receipt"] or {}
    _require(applied_result["ok"] is True and applied.get("ok") is True and applied.get("mutationAllowed") is True
             and applied_result["pending"] == "applied"
             and evidence["afterApplySha256"] == _sha(b"reviewed();\n"),
             "Reviewed patch did not produce independent applied filesystem effect")
    receipts = ((applied.get("raw") or {}).get("receipt") or {})
    _require(receipts.get("ok") is True and receipts.get("rollback_required") is False
             and receipts.get("files") and receipts["files"][0]["written_sha256"] == evidence["afterApplySha256"],
             "Reviewed patch receipt is not byte-bound")
    required_requests = ("/api/applications/editor/files", "/api/applications/editor/read",
                         "/api/applications/editor/project/file/save",
                         "/api/applications/editor/project/transaction/prepare",
                         "/api/applications/editor/project/transaction/apply")
    _require(all(path in evidence["requestPaths"] for path in required_requests),
             "Real source service requests were not exercised")
    _require(len(set(first["events"] + applied_result["events"])) == len(_EXPECTED),
             "Not all eight intents were routed through generated adapter")
    intent_results = {name: {"passed": True, "runtimeMethod": name,
                "effectRefs": [r["ref"] for r in item.get("effectRefs", [])],
                "evidence": "fresh-chromium-adapter+real-viewport-server+independent-filesystem"}
                for name,item in sorted(intents.items())}
    scenarios_result = {s["id"]: {"passed": True, "intentId": s["intent"]["ref"],
                    "evidence": "observed intent, explicit receipt and effect preconditions"}
                    for s in scenarios}
    checks = {key:True for key in ("dslAuthorityCompiled", "hostBoundProjectionActive",
             "browserCatalogHostBound", "generatedRuntimeBindingsExact", "allLocalIntentsProviderFree",
             "capabilityAccountingClosed", "realGeneratedAdapterMounted", "freshBrowserParity",
             "independentBytesAfterSave", "staleSourceRefused", "previewNonMutating",
             "unapprovedPatchRefused", "reviewedApplyWritesVerified")}
    return {"schema": SCHEMA, "appId": APP_ID, "status": "ir-native-authoritative",
         "passed": True, "applicable": True, "semanticFingerprint": compiled.semantic_fingerprint,
         "sourceBindingFingerprint": compiled.source_binding_fingerprint,
         "declaredIntentCount":len(intents), "coveredIntentCount":len(intents),
         "declaredScenarioCount":len(scenarios), "observedScenarioCount":len(scenarios),
         "failedIntentIds":[],"failedScenarioIds":[], "crossCuttingChecks":checks,
         "intents":intent_results, "scenarios":scenarios_result,
         "effectAccounting":{"status":"closed", "declaredEffectCount":len(effects)},
         "parityEvidence":{"freshChromiumObservation":True,
             "browserObservation":{"status":"pass","checks":{"sourceTreeUnchanged":True,
                  "realFilesystemEffects":True,"unapprovedEffectRefused":True}}},
         "browserEffectEvidence": {"saveAfterSha256":evidence["afterSaveSha256"],
             "reviewedApplyAfterSha256":evidence["afterApplySha256"],
             "afterReadOnlyPreviewSha256":evidence["afterPreviewSha256"],
             "serverRequestPaths":evidence["requestPaths"]},
         "promotionEligible":False, "legacyEvidenceRequired":False,
         "proofScope":"host-bound-generated-adapter+real-disposable-workspace; not standalone package-host truth"}
