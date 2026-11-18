"""Stage-1 contract: deployed MCEL apps resolve registry policy but do not style."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import shutil

import pytest

from main_computer.mcel_applications_registry import (
    REGISTRY_PATH, SELECTION_SCHEMA,
    inert_presentation_mount, read_registry, resolve_presentation,
)
from main_computer.mcel_calculator_expanded_host import render_expanded_page
from main_computer.mcel_chat_console_host import render_chat_console_page
from main_computer.mcel_semantic_other_apps_host import render_semantic_page
from main_computer.mcel_phase2c_candidate_host import CandidateHostError, ROOT


PROFILE = {
    "theme": "theme-machine",
    "form": "form-current",
    "chrome": "chrome-tng-side-rails",
}


def test_system_defaults_in_existing_registry_and_no_rollout():
    registry = read_registry(ROOT)
    assert registry["schema"] == "mcel.applications-shell.registry.v1"
    assert len(registry["apps"]) == 22
    assert registry["presentation"]["system"] == PROFILE
    assert registry["presentation"]["rollout"] == {"defaultEnabled": False, "apps": {}}
    for app_id in ("calculator", "file-explorer", "task-manager", "code-editor", "chat-console"):
        policy = resolve_presentation(registry, app_id)
        assert policy == {"schema": SELECTION_SCHEMA, "appId": app_id,
                          "profiles": PROFILE, "eligible": True, "rolloutEnabled": False}
    assert resolve_presentation(registry, "desktop")["eligible"] is False
    assert resolve_presentation(registry, "mcel-lab")["rolloutEnabled"] is False


def test_application_overrides_are_sparse_and_inherit_other_properties():
    registry = deepcopy(read_registry(ROOT))
    app = next(a for a in registry["apps"] if a["id"] == "code-editor")
    app["presentation"] = {"chrome": "chrome-other"}
    assert resolve_presentation(registry, "code-editor")["profiles"] == {
        "theme": "theme-machine", "form": "form-current", "chrome": "chrome-other"
    }
    registry["presentation"]["system"]["theme"] = "theme-new"
    assert resolve_presentation(registry, "code-editor")["profiles"]["theme"] == "theme-new"
    assert resolve_presentation(registry, "calculator")["profiles"]["chrome"] == "chrome-tng-side-rails"
    assert resolve_presentation(registry, "code-editor", promoted=False)["eligible"] is False


def test_rollout_is_independent_of_profiles_and_bounded_to_semantic_apps():
    registry = deepcopy(read_registry(ROOT))
    registry["presentation"]["rollout"]["apps"] = {"calculator": True}
    assert resolve_presentation(registry, "calculator")["rolloutEnabled"] is True
    assert resolve_presentation(registry, "calculator", promoted=False)["rolloutEnabled"] is False
    assert resolve_presentation(registry, "task-manager")["rolloutEnabled"] is False
    registry["presentation"]["rollout"]["defaultEnabled"] = True
    registry["presentation"]["rollout"]["apps"]["task-manager"] = False
    assert resolve_presentation(registry, "task-manager")["rolloutEnabled"] is False
    assert resolve_presentation(registry, "code-editor")["rolloutEnabled"] is True
    assert resolve_presentation(registry, "desktop")["rolloutEnabled"] is False


@pytest.mark.parametrize("change", [
    lambda r: r["presentation"]["system"].pop("form"),
    lambda r: r["presentation"]["system"].update({"theme": 123}),
    lambda r: r["presentation"]["rollout"].update({"defaultEnabled": "true"}),
    lambda r: r["presentation"]["rollout"]["apps"].update({"desktop": True}),
    lambda r: next(a for a in r["apps"] if a["id"] == "calculator").update({"presentation": {"chrome": "bad"}}),
    lambda r: next(a for a in r["apps"] if a["id"] == "desktop").update({"presentation": {"chrome": "chrome-other"}}),
])
def test_registry_rejects_bad_policy_values(tmp_path, change):
    registry = deepcopy(read_registry(ROOT))
    change(registry)
    registry_file = tmp_path / REGISTRY_PATH
    registry_file.parent.mkdir(parents=True)
    registry_file.write_text(json.dumps(registry), encoding="utf8")
    # The canonical route allowlist is still read from the *actual* source.
    route_file = tmp_path / "main_computer/viewport_state.py"
    route_file.parent.mkdir(parents=True)
    route_file.write_bytes((ROOT / "main_computer/viewport_state.py").read_bytes())
    with pytest.raises(CandidateHostError):
        read_registry(tmp_path)


@pytest.mark.parametrize("app_id, root_id, render", [
    ("calculator", "mcel-expanded-root", lambda promoted: render_expanded_page(promoted=promoted)),
    ("file-explorer", "mcel-workbench", lambda promoted: render_semantic_page("file-manager", promoted=promoted)),
    ("task-manager", "mcel-workbench", lambda promoted: render_semantic_page("task-manager", promoted=promoted)),
    ("code-editor", "mcel-workbench", lambda promoted: render_semantic_page("code-editor", promoted=promoted)),
    ("chat-console", "chat-console-app", lambda promoted: render_chat_console_page(promoted=promoted)),
])
def test_promoted_mounts_connect_after_existing_mount_and_candidates_unchanged(app_id, root_id, render):
    promoted = render(True)
    assert promoted.count('id="mcel-application-presentation-selection"') == 1
    assert promoted.count("McelApplicationPresentationRegistry.connect(") == 1
    expected = inert_presentation_mount(ROOT, app_id, root_id,
        controller_preloaded=(app_id != "chat-console"))
    assert promoted.endswith(expected + "</body></html>")
    assert '"rolloutEnabled":false' in expected
    assert 'window.__mcelSemanticColorRollout=false' in promoted
    assert '"status":"resolved-no-op"' not in expected  # status is produced only on the browser side
    candidate = render(False)
    assert 'id="mcel-application-presentation-selection"' not in candidate
    assert "McelApplicationPresentationRegistry.connect(" not in candidate



def test_bad_presentation_cannot_break_shell_inventory(tmp_path):
    registry = deepcopy(read_registry(ROOT))
    registry["presentation"]["system"]["theme"] = None
    registry_file = tmp_path / REGISTRY_PATH
    registry_file.parent.mkdir(parents=True)
    registry_file.write_text(json.dumps(registry), encoding="utf8")
    route_file = tmp_path / "main_computer/viewport_state.py"
    route_file.parent.mkdir(parents=True)
    route_file.write_bytes((ROOT / "main_computer/viewport_state.py").read_bytes())
    assert len(read_registry(tmp_path, validate_presentation=False)["apps"]) == 22
    with pytest.raises(CandidateHostError):
        read_registry(tmp_path)

def test_registry_failure_is_nonblocking_and_reported(tmp_path):
    # The application and JS assets remain present, but the registry is bad.
    import main_computer.mcel_applications_registry as mod
    real = mod.read_registry
    def fail(_repo):
        raise CandidateHostError("deliberate registry outage")
    try:
        mod.read_registry = fail
        html = inert_presentation_mount(ROOT, "calculator", "mcel-expanded-root")
    finally:
        mod.read_registry = real
    assert '"registry-unavailable"' in html
    assert '"rolloutEnabled":false' in html
    assert 'McelApplicationPresentationRegistry.connect(' in html




def test_missing_presentation_hook_does_not_change_operational_page(monkeypatch, caplog):
    import main_computer.mcel_applications_registry as mod
    def fail(*args, **kwargs):
        raise OSError("optional presentation asset missing")
    monkeypatch.setattr(mod, "inert_presentation_mount", fail)
    original = '<html><body><main id="app">still running</main></body></html>'
    assert mod.attach_inert_presentation(original, ROOT, "calculator", "app", promoted=True) == original
    assert "presentation registry hook unavailable" in caplog.text

def test_browser_hook_is_inert_even_when_rollout_enabled():
    from playwright.sync_api import sync_playwright
    import main_computer.mcel_applications_registry as mod
    original = mod.resolve_presentation(read_registry(ROOT), "calculator")
    modified = {**original, "rolloutEnabled": True}
    js = (ROOT / mod.HOOK_JS).read_text(encoding="utf8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=shutil.which("chromium") or None)
        page = browser.new_page()
        try:
            page.set_content('<div id="root"><button id="action">Action</button></div>'
                             '<script id="mcel-application-presentation-selection" type="application/json">'
                             + json.dumps(modified) + '</script>')
            before = page.evaluate('''() => ({markup:document.getElementById("root").outerHTML,
                style: getComputedStyle(document.getElementById("action")).cssText,
                body: document.body.getAttributeNames()})''')
            page.add_script_tag(content=js)
            result = page.evaluate('''() => McelApplicationPresentationRegistry.connect({
                appId: "calculator", rootId: "root"})''')
            after = page.evaluate('''() => ({markup:document.getElementById("root").outerHTML,
                style: getComputedStyle(document.getElementById("action")).cssText,
                body: document.body.getAttributeNames()})''')
            assert result["status"] == "resolved-no-op"
            assert result["rolloutEnabled"] is True
            assert result["applied"] is False
            assert before == after
            assert page.evaluate('''() => McelApplicationPresentationRegistry.connect({
                appId: "calculator", rootId: "root"}).status''') == "resolved-no-op"
        finally:
            browser.close()
