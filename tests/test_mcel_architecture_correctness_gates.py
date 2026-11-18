"""Adversarial MCEL architecture gates (RED until the shared runtime is fixed).

Tests-only specification. Chromium uses mocked HTTP boundaries; no live SLH,
Coolify, credentials, file writes, or consequential backend mutations occur.
Run with: python -m pytest -q tests/test_mcel_architecture_correctness_gates.py

Gates correspond to the architectural diagnostic findings 1-7. Assertions
are intentionally NOT xfailed: these are regression requirements, not a
certificate that the current implementation already meets them.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from playwright.sync_api import sync_playwright

from main_computer.mcel_slh_application import compile_slh_application, render_slh_page
from tests.test_mcel_record_notebook_async_reconciliation import (
    MOCK, _calls, _launch, _pending_loaded,
)
from tests.test_mcel_seh_two_action_lifecycle_ui import _setup

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js"
SLH_PORT = ROOT / "main_computer/web/applications/scripts/mcel-semantic-slh-ports.js"
NOTEBOOK = '[data-mcel-operation-notebook="surface-node:slh.local-seh-lifecycle"]'


def _browser(pw):
    chromium = shutil.which("chromium")
    return pw.chromium.launch(headless=True, executable_path=chromium or None,
                              args=["--no-sandbox"])


def test_p0_refused_duplicate_cannot_release_another_operation_lock():
    """Second dispatch is refused; its finally must not unlock first dispatch."""
    with sync_playwright() as pw:
        browser = _browser(pw)
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.evaluate(MOCK)
            # Install the intercept BEFORE app mounting: SLH ports capture fetch
            # during creation, so swapping window.fetch afterward is too late.
            page.evaluate('''() => {
              window.lockGate={inFlight:[], backendCalls:0};
              const old=window.fetch;
              const ok=result=>({ok:true,json:async()=>({ok:true,result})});
              window.fetch=async(url,options={})=>{
                const body=options.body?JSON.parse(options.body):{};
                if(String(url)==='/api/applications/slh/seh/deployment'){
                  if(body.operation==='inspect')return ok({generation:33,
                    selectedController:'coolify-c',applicationUuid:'',
                    controllers:[{identity:'coolify-c'}],deploymentStatus:'UNDEPLOYED'});
                  if(body.operation==='operation-state')return ok({
                    schema:'main-computer.seh-management-operation-snapshot.v1',
                    state:'IDLE',busy:false,operation:null,last_operation:null});
                  if(body.operation==='deploy'){
                    window.lockGate.backendCalls++;
                    return new Promise(resolve=>window.lockGate.inFlight.push(()=>resolve(ok({
                      outcome:'accepted-deferred',operationId:body.operationId||'test',
                      applicationUuid:'mock-uuid',message:'accepted'}))));
                  }
                }
                return old(url,options);
              };
            }''')
            page.set_content(render_slh_page())
            page.wait_for_function("() => window.__mcelSlh?.app.readState('state:seh-deployment-status')?.deploymentStatus==='UNDEPLOYED'")
            page.evaluate("() => {window.lockGate.first=window.__mcelSlh.app.execute('intent:seh-deployment-deploy')}")
            page.wait_for_function("() => window.lockGate.backendCalls===1")
            second = page.evaluate("async () => await window.__mcelSlh.app.execute('intent:seh-deployment-deploy')")
            assert second["disposition"] == "refused-before-attempt", second
            page.evaluate('''() => {
              window.lockGate.third=window.__mcelSlh.app.execute('intent:seh-deployment-deploy');
            }''')
            # Observe the third dispatch while the first authorization/invoke is pending.
            page.wait_for_timeout(80)
            premature = page.evaluate("() => window.lockGate.backendCalls")
            page.evaluate('''() => {
              for(const release of window.lockGate.inFlight)release();
            }''')
            page.evaluate("async () => await Promise.all([window.lockGate.first,window.lockGate.third])")
            assert premature == 1, (
                "A refused duplicate released the first operation's lock; "
                f"{premature} concurrent backend deploy calls were admitted"
            )
            # Releasing the legitimate first request must permit a NEW action;
            # a repaired guard must not become a permanent lock.
            page.evaluate("() => {window.lockGate.fourth=window.__mcelSlh.app.execute('intent:seh-deployment-deploy')}")
            page.wait_for_function("() => window.lockGate.backendCalls===2")
            page.evaluate("() => {for(const release of window.lockGate.inFlight)release()}")
            fourth = page.evaluate("async () => await window.lockGate.fourth")
            assert fourth["disposition"] in ("accepted-deferred", "completed"), fourth
        finally:
            browser.close()


def test_p0_revocation_invalidates_outstanding_protected_results():
    """An old authenticated read may not resurrect records after revocation."""
    with sync_playwright() as pw:
        browser = _browser(pw)
        try:
            page = _launch(browser)
            page.locator('[data-mcel-notebook-tab="pending"]').click()
            _pending_loaded(page)
            page.evaluate('''() => {
              window.delayPending=true;
              window.revocationRead=window.__mcelSlh.app.execute('intent:read-organization-pending');
            }''')
            page.wait_for_function("() => window.pendingResponses.length===1")
            page.evaluate('''() => window.__mcelSlh.app.setNotebookAuthorization(false,{
              scope:'records'
            })''')
            assert page.evaluate("() => window.__mcelSlh.app.readState('state:pending-enrollments')") is None
            page.evaluate("() => window.replyPending(0,'Forbidden Late Record')")
            disposition = page.evaluate("async () => (await window.revocationRead).disposition")
            resurrected = page.evaluate("() => window.__mcelSlh.app.readState('state:pending-enrollments')")
            assert disposition == "superseded", f"Revoked read was incorrectly {disposition}"
            assert resurrected is None, "Late response repopulated a revoked protected state"
        finally:
            browser.close()


def test_p1_undeploy_eligibility_must_hold_at_dispatch_not_only_on_button():
    """Programmatic execute() must honor the application's enabledWhen rule."""
    with sync_playwright() as pw:
        browser = _browser(pw)
        try:
            page, notebook = _setup(browser, "UNDEPLOYED")
            button = notebook.locator('[data-mcel-operation-action="Undeploy"]')
            assert button.is_disabled(), "Test precondition: Undeploy UI should be disabled"
            before = page.evaluate("() => window.sehUiCalls.filter(c=>c.operation==='undeploy').length")
            receipt = page.evaluate("async () => await window.__mcelSlh.app.execute('intent:seh-deployment-undeploy')")
            after = page.evaluate("() => window.sehUiCalls.filter(c=>c.operation==='undeploy').length")
            assert receipt["disposition"] == "refused-before-attempt", receipt
            assert after == before, "UI-disabled Undeploy reached the capability backend"
        finally:
            browser.close()


def test_p1_transient_notebook_read_failure_recovers_on_explicit_app_lifecycle_signal():
    """Application may retry a transient read when focus/online says retry; no click."""
    with sync_playwright() as pw:
        browser = _browser(pw)
        try:
            page = browser.new_page(viewport={"width": 1150, "height": 850})
            page.evaluate(MOCK)
            page.evaluate("window.pendingReject=true")
            page.set_content(render_slh_page())
            page.wait_for_function("() => window.__mcelSlh?.app.readState('state:selected-local-identity')==='one'")
            page.locator('[data-mcel-notebook-tab="pending"]').scroll_into_view_if_needed()
            page.wait_for_function("() => document.querySelector('[data-mcel-notebook-status]')?.textContent.includes('INSPECTION_REQUIRED')")
            assert _calls(page, "read-pending") == 1
            page.evaluate('''() => {
              window.pendingReject=false;
              window.dispatchEvent(new Event('online'));
              window.dispatchEvent(new Event('focus'));
            }''')
            # The application, not MCEL, chooses which readiness events retry.
            # A genuine ADMIN_REFUSED would need a distinct explicit recovery.
            page.wait_for_function("() => window.__mcelSlh.app.readState('state:pending-enrollments')?.length===1", timeout=7000)
            assert _calls(page, "read-pending") >= 2
            assert "unavailable" not in page.locator('[data-mcel-notebook-status]').inner_text().lower()
        finally:
            browser.close()


def test_p1_transient_notebook_read_failure_can_retry_on_reactivation_without_tab_click():
    """Application visibility reactivation can retry after a recoverable failure."""
    with sync_playwright() as pw:
        browser = _browser(pw)
        try:
            page = browser.new_page(viewport={"width": 1150, "height": 700})
            page.evaluate(MOCK)
            page.evaluate('''() => {
              window.pendingReject=true;
              window.IntersectionObserver=class {
                constructor(callback){this.callback=callback;window.testNotebookObserver=this;}
                observe(node){this.node=node;}
                disconnect(){}
                emit(visible){this.callback([{target:this.node,isIntersecting:visible}]);}
              };
            }''')
            page.set_content(render_slh_page())
            page.wait_for_function("() => window.__mcelSlh?.app.readState('state:selected-local-identity')==='one'")
            page.wait_for_function("() => window.__mcelSlh?.app.readState('state:organization-lifecycle')?.state==='running'")
            page.evaluate("() => window.testNotebookObserver.emit(true)")
            page.wait_for_function("() => document.querySelector('[data-mcel-notebook-status]')?.textContent.includes('INSPECTION_REQUIRED')")
            assert _calls(page, "read-pending") == 1
            page.evaluate('''() => {
              window.pendingReject=false;
              window.testNotebookObserver.emit(false);
              window.testNotebookObserver.emit(true);
            }''')
            page.wait_for_function("() => window.notebookCalls.filter(x=>x==='read-pending').length>=2", timeout=3500)
            _pending_loaded(page)
        finally:
            browser.close()


def test_p1_auth_failure_and_transport_unavailability_are_distinguishable():
    """Two different causes must not collapse into the same authority-denied."""
    observed = {}
    with sync_playwright() as pw:
        browser = _browser(pw)
        try:
            for scenario in ("credential_refused", "transport_unavailable"):
                page = browser.new_page(viewport={"width": 1150, "height": 850})
                page.evaluate(MOCK)
                page.evaluate('''scenario => {
                  const previous=window.fetch;
                  window.fetch=async(url,options={})=>{
                    const body=options.body?JSON.parse(options.body):{};
                    if(String(url)==='/api/applications/slh/setup/action' && body.action==='authenticate'){
                      if(scenario==='transport_unavailable')throw Error('MOCK_NETWORK_UNAVAILABLE');
                      return {ok:false,json:async()=>({error:{code:'ADMIN_REFUSED'}})};
                    }
                    return previous(url,options);
                  };
                }''', scenario)
                page.set_content(render_slh_page())
                page.wait_for_function("() => window.__mcelSlh?.app.readState('state:selected-local-identity')==='one'")
                result = page.evaluate("async () => await window.__mcelSlh.app.execute('intent:read-organization-pending')")
                observed[scenario] = (result.get("disposition"), result.get("reason"))
                page.close()
        finally:
            browser.close()
    refused, unavailable = observed["credential_refused"], observed["transport_unavailable"]
    assert refused != unavailable, (
        "Credential refusal and transport outage collapsed into one result: "
        f"{refused!r}; typed cause was lost"
    )
    assert any(word in str(refused).lower() for word in ("refus", "denied", "unauthoriz")), refused
    assert any(word in str(unavailable).lower() for word in ("transport", "network", "unavailable")), unavailable


def test_p2_record_notebook_contract_accepts_optional_retention_and_empty_trash():
    """A generic record notebook can exist without SLH identity policy controls."""
    ir, composition = compile_slh_application()
    import copy
    minimal = copy.deepcopy(ir)
    notebooks = [node for node in minimal["surfaces"][0]["nodes"] if node["nodeKind"] == "record-notebook"]
    assert notebooks, "Missing fixture notebook"
    notebook = notebooks[0]
    notebook.pop("retention", None)
    notebook.pop("empty", None)
    # This synthetic app retains its declared record tabs; no business policy.
    if "updates" in notebook:
        notebook["updates"] = {"rows": True, "status": True}
    with sync_playwright() as pw:
        browser = _browser(pw)
        try:
            page = browser.new_page()
            # The complete SLH page loads the actual version of shared runtime.
            page.set_content(render_slh_page())
            result = page.evaluate('''ir => {
              const engine=window.McelSemanticOperationalRuntime;
              const host=document.createElement('div');host.style.display='none';
              document.body.append(host);
              try {
                engine.contract(ir.ir,ir.composition);
                const app=engine.mount(host,ir.ir,{composition:ir.composition,
                  presentation:'expert',capabilities:{authorize:async()=>true,invoke:async()=>[]}});
                const rendered=!!host.querySelector('[data-mcel-notebook]');
                app.unmount();
                return {ok:rendered,reason:rendered?'':'generic-notebook-not-rendered'};
              } catch(ex){return {ok:false,reason:String(ex.message||ex)};}
              finally {host.remove();}
            }''', {'ir': minimal, 'composition': composition})
            assert result["ok"], f"Generic notebook rejected without identity trash policy: {result}"
        finally:
            browser.close()


def test_p2_generic_runtime_does_not_embed_specific_product_policy():
    """Static architecture gate: domain vocabulary and Aider policy live in apps."""
    shared = SHARED.read_text(encoding="utf-8")
    # This does not prohibit generic source-write preflights; it prohibits
    # recognizing one product by literal intent name in the framework.
    assert 'id==="intent:run-aider"' not in shared, (
        "Generic MCEL runtime still branches on the Aider application intent"
    )
    assert 'intent.input[0].sourceName==="recordId"' not in shared, (
        "Generic record notebook hardcodes the identity app's recordId argument"
    )
    assert 'execute(ref(action.intent),{recordId:' not in shared, (
        "Generic notebook renderer hardcodes an application's action argument"
    )
    # The collection renderer may display authored messages, but should not
    # author fixed identity/trash policy wording itself.
    for marker in ("Permanently remove ALL trashed identities?",
                   "organizational identities", "Organization records unavailable:"):
        assert marker not in shared, f"Generic MCEL runtime owns domain wording: {marker}"
    # The SLH application must retain authority over its business controls.
    assert SLH_PORT.exists()


def test_p2_activity_receipts_are_bounded_during_long_running_sessions():
    """MCEL in-memory presentation history is finite; backend evidence is separate."""
    with sync_playwright() as pw:
        browser = _browser(pw)
        try:
            page = _launch(browser)
            count = page.evaluate('''async () => {
              const app=window.__mcelSlh.app;
              // Local interaction already supported by the SLH semantic IR;
              // no HTTP mutation and no deployment action.
              for(let i=0;i<300;i++){
                const receipt=await app.execute('intent:choose-identity',{key:'one'});
                if(receipt.disposition!=='selected')throw Error(JSON.stringify(receipt));
              }
              return app.receipts().length;
            }''')
            assert count <= 256, (
                f"Unbounded in-memory operation receipts: {count} after 300 selections; "
                "requires bounded UI history and separate durable evidence"
            )
        finally:
            browser.close()
