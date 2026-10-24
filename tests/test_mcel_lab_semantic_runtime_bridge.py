"""Exercise the Lab's real UI semantic bindings in Chromium, not local bookkeeping."""

from __future__ import annotations

import importlib
import shutil
from unittest.mock import patch

import pytest


def test_mcel_lab_five_ui_intents_have_real_browser_witnesses() -> None:
    playwright = pytest.importorskip("playwright.sync_api")
    # The exported repository omits optional WebGL game bundles. Those are
    # unrelated to the Lab and are not proof inputs for these five operations.
    from main_computer import game_web_loader

    with patch.object(game_web_loader, "read_game_bundle", return_value=""):
        pages = importlib.import_module("main_computer.viewport_pages")
        html = pages.APPLICATIONS_INDEX_HTML

    executable = shutil.which("chromium") or shutil.which("chromium-browser")
    try:
        with playwright.sync_playwright() as pw:
            kwargs = {"headless": True}
            if executable:
                kwargs["executable_path"] = executable
                kwargs["args"] = ["--no-sandbox"]
            try:
                browser = pw.chromium.launch(**kwargs)
            except Exception as exc:
                pytest.skip(f"Chromium unavailable for live MCEL Lab UI proof: {exc}")
            try:
                page = browser.new_page(viewport={"width": 1800, "height": 1200})
                # set_content has an opaque origin. Mock only browser storage
                # and navigation, never MCEL operations or DOM evidence.
                page.evaluate("""() => {
                    const store = {getItem:()=>null,setItem:()=>{},removeItem:()=>{},clear:()=>{}};
                    Object.defineProperty(window, 'localStorage', {configurable:true,value:store});
                    Object.defineProperty(window, 'sessionStorage', {configurable:true,value:store});
                    history.replaceState = () => {};
                }""")
                page.set_content(html, wait_until="domcontentloaded", timeout=90000)
                assert page.evaluate("typeof window.McelLabSemanticRuntime") == "object"
                assert page.evaluate("typeof window.McelLabSemanticAdapter") == "object"
                initial = page.evaluate("""() => ({
                  badBlueprint: window.McelLabSemanticRuntime.selectAppBlueprint({appId:'not-real'}),
                  badAspect: window.McelLabSemanticRuntime.inspectAspect({appId:'mcel-lab',aspectId:'no-such-aspect'}),
                  earlyInspect: window.McelLabSemanticRuntime.inspectRenderedElement({appId:'mcel-lab'})
                })""")
                assert all(item["ok"] is False for item in initial.values())

                selections = page.evaluate("""async () => ({
                  selected: await window.McelLabSemanticAdapter.executeIntent('selectAppBlueprint', {appId:'mcel-lab'}),
                  aspect: await window.McelLabSemanticAdapter.executeIntent('inspectAspect', {appId:'mcel-lab',aspectId:'actions'}),
                  mounted: await window.McelLabSemanticAdapter.executeIntent('mountAppPreview', {appId:'mcel-lab'}),
                  validated: await window.McelLabSemanticAdapter.executeIntent('validateBlueprintContract', {appId:'mcel-lab',aspectId:'actions'})
                })""")
                assert all(item["ok"] is True for item in selections.values())
                assert selections["mounted"]["state"]["mountedPreview"]["contained"] is True
                assert selections["validated"]["state"]["validation"]["status"] == "validated"

                # Make the application's normal route visible in this isolated
                # document. Point selection still goes through its real handler.
                page.evaluate("document.getElementById('mcel-lab-app').style.display='block'")
                assert page.evaluate("window.McelLabPointInspect.activate()") is True
                refused = page.evaluate("""async () => window.McelLabSemanticAdapter.executeIntent(
                    'inspectRenderedElement',{appId:'mcel-lab',inspectMode:true})""")
                assert refused["ok"] is False
                preview = page.locator(".mcel-lab-mounted-preview-frame [data-mcel-preview-clone]").first
                preview.dispatch_event("pointerdown", {"clientX": 40, "clientY": 40, "button": 0, "buttons": 1, "pointerId": 1})
                observed = page.evaluate("""async () => window.McelLabSemanticAdapter.executeIntent(
                    'inspectRenderedElement',{appId:'mcel-lab',inspectMode:true})""")
                assert observed["ok"] is True
                record = observed["state"]["selectedElement"]
                assert record["selectedAppId"] == "mcel-lab"
                assert record["selector"]
                assert page.evaluate("""() => document.getElementById('mcel-blueprint-selected-element').dataset.mcelSelectionRender""") == "complete"

                audit = page.evaluate("window.McelLabSemanticAdapter.getRuntimeBindingCoverage()")
                assert audit["runtimeBindingReady"] is True
                by_intent = {item["intentId"]: item for item in audit["entries"]}
                for intent in ("selectAppBlueprint", "inspectAspect", "mountAppPreview", "inspectRenderedElement", "validateBlueprintContract"):
                    assert by_intent[intent]["status"] == "runtime-bound"
                for intent in ("annotateRefactorCandidate", "exportRepairContext"):
                    assert by_intent[intent]["status"] in ("adapter-local", "runtime-bound")
                forbidden = page.evaluate("""async () => window.McelLabSemanticAdapter.executeIntent('applySelfMutation', {command:'git push'})""")
                assert forbidden["status"] == "blocked"
                assert forbidden["receipt"]["mutationAllowed"] is False
            finally:
                browser.close()
    except Exception:
        raise
