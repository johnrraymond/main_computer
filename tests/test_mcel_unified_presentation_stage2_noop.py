"""Stage 2: shared presentation ownership is prepared, never activated."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from main_computer.mcel_applications_registry import (
    CONTROLLER_JS, HOOK_JS, inert_presentation_mount, read_registry,
    resolve_presentation,
)
from main_computer.mcel_calculator_expanded_host import render_expanded_page
from main_computer.mcel_semantic_other_apps_host import render_semantic_page
from main_computer.mcel_chat_console_host import render_chat_console_page
from main_computer.mcel_phase2c_candidate_host import ROOT


@pytest.mark.parametrize("app_id,root_id,render", [
    ("calculator", "mcel-expanded-root", lambda promoted: render_expanded_page(promoted=promoted)),
    ("file-explorer", "mcel-workbench", lambda promoted: render_semantic_page("file-manager", promoted=promoted)),
    ("task-manager", "mcel-workbench", lambda promoted: render_semantic_page("task-manager", promoted=promoted)),
    ("code-editor", "mcel-workbench", lambda promoted: render_semantic_page("code-editor", promoted=promoted)),
    ("chat-console", "chat-console-app", lambda promoted: render_chat_console_page(promoted=promoted)),
])
def test_existing_promoted_mounts_use_one_inert_shared_controller(app_id, root_id, render):
    promoted = render(True)
    assert promoted.count("McelUnifiedPresentationController?.prepare(") == 1
    assert promoted.count('Object.defineProperty(global, "McelUnifiedPresentationController"') == 1
    assert promoted.count("McelApplicationPresentationRegistry.connect(") == 1
    assert promoted.index('Object.defineProperty(global, "McelUnifiedPresentationController"') < promoted.index(
        "McelApplicationPresentationRegistry.connect(")
    assert promoted.endswith(inert_presentation_mount(ROOT, app_id, root_id,
        controller_preloaded=(app_id != "chat-console")) + "</body></html>")
    candidate = render(False)
    assert 'Object.defineProperty(global, "McelUnifiedPresentationController"' not in candidate
    assert 'id="mcel-application-presentation-selection"' not in candidate


def test_stage25_controller_normalizes_existing_law_execution_only():
    source = (ROOT / CONTROLLER_JS).read_text(encoding="utf8")
    assert "McelSemanticColorPresentation" in source
    assert "McelSemanticFormPresentationLaw" in source
    assert "McelSemanticChromePresentationLaw" in source
    assert "WeakMap" in source
    assert "mountOperationalColor" in source
    assert "applyLabTheme" in source and "applyLabForm" in source and "applyLabChrome" in source
    assert "document.createElement(" not in source
    assert "MutationObserver(" not in source


def test_missing_optional_controller_does_not_block_registry_resolution(monkeypatch, caplog):
    import main_computer.mcel_applications_registry as mod
    monkeypatch.setattr(mod, "CONTROLLER_JS", Path("missing-unified-presentation-controller.js"))
    markup = inert_presentation_mount(ROOT, "calculator", "mcel-expanded-root")
    assert "McelApplicationPresentationRegistry.connect(" in markup
    assert "McelUnifiedPresentationController?.prepare" in markup  # no-op hook alone
    assert 'Object.defineProperty(global, "McelUnifiedPresentationController"' not in markup
    assert "presentation controller unavailable" in caplog.text


def test_read_only_controller_does_not_mutate_dom_or_call_laws_even_when_rollout_enabled():
    from playwright.sync_api import sync_playwright
    selection = {**resolve_presentation(read_registry(ROOT), "calculator"), "rolloutEnabled": True}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=shutil.which("chromium") or None)
        page = browser.new_page()
        try:
            page.set_content('''<!doctype html><html><head><style>button{color:blue}</style></head><body>
              <main id="root" data-mcel-semantic-color-full="true">
                <button id="control">Existing operation</button></main>
              <div id="other"><button>Unrelated app</button></div></body></html>''')
            page.add_script_tag(content='''(() => {
              window.lawCalls=[];
              window.McelSemanticColorPresentation={mount(){lawCalls.push("color");throw Error("called")}};
              window.McelSemanticFormPresentationLaw={apply(){lawCalls.push("form");throw Error("called")}};
              window.McelSemanticChromePresentationLaw={apply(){lawCalls.push("chrome");throw Error("called")}};
              window.MutationObserver = class {constructor(){lawCalls.push("observer")}};
            })();''')
            # set_content is not used again: append the data script without modifying
            # the app root and take the DOM baseline after adding it.
            page.evaluate('''selection => {
              const node=document.createElement("script");
              node.id="mcel-application-presentation-selection";
              node.type="application/json";node.textContent=JSON.stringify(selection);
              document.body.appendChild(node);
            }''', selection)
            snapshot = '''() => ({root:document.getElementById("root").outerHTML,
              other:document.getElementById("other").outerHTML,
              styles:[...document.querySelectorAll("style")].map(n=>n.textContent),
              computed:getComputedStyle(document.getElementById("control")).color,
              classes:document.body.className,attrs:document.body.getAttributeNames()})'''
            before = page.evaluate(snapshot)
            page.add_script_tag(content=(ROOT / CONTROLLER_JS).read_text(encoding="utf8"))
            page.add_script_tag(content=(ROOT / HOOK_JS).read_text(encoding="utf8"))
            # Browser automation may itself create observers while injecting scripts.
            # Count only any observers created by the actual registry call.
            page.evaluate("() => { window.lawCalls.length = 0; }")
            result = page.evaluate('''() => {
              const sel=McelApplicationPresentationRegistry.connect({appId:"calculator",rootId:"root"});
              const root=document.getElementById("root");
              const controller=McelUnifiedPresentationController;
              const first=controller.inspect(root);
              const repeat=controller.prepare({root,appId:"calculator",profiles:sel.profiles,rolloutEnabled:true});
              const conflict=controller.prepare({root,appId:"code-editor",profiles:sel.profiles});
              return {sel,plan:first,identical: first===repeat,conflict,
                hasApply:typeof controller.apply, lawCalls:window.lawCalls};
            }''')
            after = page.evaluate(snapshot)
            assert before == after
            assert result["sel"]["status"] == "resolved-no-op"
            assert result["sel"]["controllerStatus"] == "prepared-no-op"
            assert result["sel"]["applied"] is False
            assert result["plan"]["status"] == "prepared-no-op"
            assert result["plan"]["executionAvailable"] is False
            assert result["plan"]["rolloutEnabled"] is True
            assert [s["property"] for s in result["plan"]["steps"]] == ["theme", "form", "chrome"]
            assert [s["phase"] for s in result["plan"]["steps"]] == [
                "operational-mount", "post-mount", "post-mount"]
            assert result["identical"] is True
            assert result["conflict"]["reason"] == "root-already-owned-by-other-application"
            assert result["hasApply"] == "undefined"
            assert result["lawCalls"] == []
            assert page.evaluate('''() => {
              const root=document.getElementById("root");
              const controller=McelUnifiedPresentationController;
              const removed=controller.release(root);
              return {removed,inspect:controller.inspect(root)};
            }''') == {"removed": True, "inspect": None}
            # The same contract is available to an isolated Lab mount later;
            # it still cannot apply any styling in this stage.
            lab = page.evaluate('''() => {
              const root=document.getElementById("root");
              const profiles={theme:"theme-machine",form:"form-current",chrome:"chrome-other"};
              return McelUnifiedPresentationController.prepare({
                root,appId:"calculator",profiles,surface:"lab",rolloutEnabled:true});
            }''')
            assert lab["surface"] == "lab"
            assert lab["status"] == "prepared-no-op"
            assert lab["profiles"]["chrome"] == "chrome-other"
            assert page.evaluate(snapshot) == before
        finally:
            browser.close()
