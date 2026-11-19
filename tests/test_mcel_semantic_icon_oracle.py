"""Independent icon oracle evaluated against the *real* rendered MCEL apps.

Oracle fixtures never enter the runtime. This test checks every canonical
control/form node rather than comparing resolver internals with themselves.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from main_computer.mcel_semantic_other_apps_host import compile_semantic_app, render_semantic_page
from main_computer.mcel_semantic_generated_host import compile_p3_generated_candidate, render_p3_generated_page
from main_computer.mcel_slh_application import compile_slh_application, render_slh_page
from main_computer.mcel_applications_registry import attach_inert_presentation

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "main_computer/web/applications"
ORACLE = json.loads((ROOT / "tests/fixtures/mcel-semantic-icon-oracle.v1.json").read_text(encoding="utf-8"))

# The canonical route dispatch uses the promoted workbench hosts. P3 is a
# *different*, source-compiled candidate, so do not relabel it as the current
# canonical Task Manager. Activate the same shared MCEL policy only in its
# test-rendered page; the P3 candidate HTTP route remains unchanged.
HOSTS = {
    "file-manager": lambda: render_semantic_page("file-manager", ROOT, promoted=True),
    "code-editor": lambda: render_semantic_page("code-editor", ROOT, promoted=True),
    "task-manager": lambda: render_semantic_page("task-manager", ROOT, promoted=True),
    "task-manager-p3": lambda: attach_inert_presentation(
        render_p3_generated_page(ROOT), ROOT, "task-manager", "mcel-workbench", promoted=True),
    "slh": lambda: render_slh_page("overview", ROOT),
}
COMPILERS = {
    "file-manager": lambda: compile_semantic_app("file-manager", ROOT),
    "code-editor": lambda: compile_semantic_app("code-editor", ROOT),
    "task-manager": lambda: compile_semantic_app("task-manager", ROOT),
    "task-manager-p3": lambda: compile_p3_generated_candidate(ROOT)[0],
    "slh": lambda: compile_slh_application(ROOT)[0],
}


def test_oracle_sources_are_the_actual_applications_not_stand_ins():
    assert set(ORACLE["applications"]) == set(HOSTS) == set(COMPILERS)
    for app, spec in ORACLE["applications"].items():
        source = ROOT / spec["source"]
        assert source.is_file(), (app, source)
        ir = COMPILERS[app]()
        assert ir["application"]["appId"] == spec["appId"], app
        assert ir["application"]["source"]["file"] == spec["source"], app
        actual_nodes = {node["id"].split(".")[-1] for node in ir["surfaces"][0]["nodes"]
                        if node["nodeKind"] in {"control", "form"}}
        assert actual_nodes == set(spec["nodes"]), (app, actual_nodes ^ set(spec["nodes"]))
    # The canonical Task Manager and the new P3 candidate are not substitutes.
    assert ORACLE["applications"]["task-manager"]["source"] != ORACLE["applications"]["task-manager-p3"]["source"]
    assert ORACLE["applications"]["task-manager"]["appId"] != ORACLE["applications"]["task-manager-p3"]["appId"]


def test_oracle_is_independent_of_production_and_icons_are_core():
    assert ORACLE["schema"] == "mcel.semantic-icon-render-oracle.v1"
    code = (WEB / "scripts/mcel-semantic-icon-core.js").read_text(encoding="utf-8")
    host = (ROOT / "main_computer/mcel_applications_registry.py").read_text(encoding="utf-8")
    assert "mcel-semantic-icon-oracle" not in code and "mcel-semantic-icon-oracle" not in host
    for forbidden in ("file-manager", "file-explorer", "task-manager", "slh", "calculator", "code-editor"):
        assert forbidden not in code, f"Application-dependent icon inference: {forbidden}"
    assert '"theme", "form", "chrome"' in (WEB / "scripts/mcel-unified-presentation-controller.js").read_text()
    assert '"icon"' not in (ROOT / "main_computer/mcel_applications_registry.py").read_text().split('PROPERTIES = ')[1].split('\n', 1)[0]


@pytest.mark.parametrize("app", list(ORACLE["applications"]))
def test_real_rendered_app_matches_independent_semantic_icon_oracle(app):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    expected = ORACLE["applications"][app]["nodes"]
    expected_app_id = ORACLE["applications"][app]["appId"]
    expected_source = ORACLE["applications"][app]["source"]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=shutil.which("chromium") or None)
        page = browser.new_page(viewport={"width": 1200, "height": 850})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        try:
            page.route("**/api/**", lambda route: route.fulfill(
                status=503, content_type="application/json", body='{"ok":false}'))
            page.set_content(HOSTS[app](), wait_until="domcontentloaded", timeout=30000)
            actual = page.evaluate("""() => {
              const root=document.getElementById('mcel-workbench');
              const nodes={};
              for(const holder of root.querySelectorAll('[data-mcel-node][data-node-kind="control"], [data-mcel-node][data-node-kind="form"]')){
                const id=holder.getAttribute('data-mcel-node').split('.').pop();
                const canonical=holder.getAttribute('data-node-kind')==='form'
                  ?holder.querySelector('form button[type="submit"]')
                  :holder.querySelector('button[data-intent]');
                const slots=[...(canonical?.querySelectorAll(':scope > [data-mcel-icon-core="slot"]')||[])];
                nodes[id]={id:slots[0]?.getAttribute('data-mcel-icon-identity')||null,
                  slots:slots.length, svg:slots[0]?.querySelector('[data-mcel-icon-id]')?.getAttribute('data-mcel-icon-id')||null,
                  decorative:slots[0]?.getAttribute('aria-hidden')||null};
              }
              const source=document.getElementById('mcel-workbench-ir')||document.getElementById('mcel-generated-data');
              const parsed=JSON.parse(source.textContent);
              return {nodes,steps:__mcelPresentationReport.steps,icons:__mcelPresentationReport.iconCore,
                status:__mcelPresentationReport.status,script:!!window.McelSemanticIconCore,
                json:!!document.getElementById('mcel-icon-registry'),
                appId:(parsed.ir||parsed).application.appId,
                sourceFile:(parsed.ir||parsed).application.source?.file};
            }""")
            assert errors == [], errors
            assert actual["appId"] == expected_app_id, (app, actual["appId"])
            assert actual["sourceFile"] == expected_source, (app, actual["sourceFile"])
            assert actual["status"] == "applied" and actual["icons"]["status"] == "applied", actual
            assert actual["script"] and actual["json"]
            assert set(actual["nodes"]) == set(expected), actual
            for name, want in expected.items():
                node = actual["nodes"][name]
                assert node["id"] == want, (app, name, want, node)
                assert node["slots"] == (1 if want else 0), (app, name, node)
                if want:
                    assert node["svg"] == want and node["decorative"] == "true", (app, name, node)
            assert actual["icons"]["resolved"] == sum(v is not None for v in expected.values())
            assert actual["icons"]["ambiguous"] == 0
            # Dynamic reconciliation is idempotent, and no author content is
            # replaced when a profile is changed or reapplied.
            after = page.evaluate("""() => {
              const root=document.getElementById('mcel-workbench');
              const source=document.getElementById('mcel-workbench-ir')||document.getElementById('mcel-generated-data');
              const parsed=JSON.parse(source.textContent), ir=parsed.ir||parsed;
              const bridge=parsed.composition ? {
                observeRuntimePresentationTargets:input=>McelSemanticPresentationBridge.observeRuntimePresentationTargets({
                  ...input,composition:parsed.composition})
              } : McelSemanticPresentationBridge;
              const core=McelSemanticIconCore;
              const before=[...root.querySelectorAll('button')].map(b=>b.textContent);
              const first=core.apply({root,ir,bridge,
                profiles:{theme:'theme-machine',form:'form-current',chrome:'chrome-tng-side-rails'}});
              const second=core.apply({root,ir,bridge,
                profiles:{theme:'theme-accessible',form:'form-pill',chrome:'chrome-strict-hierarchy'}});
              const button=root.querySelector('[data-mcel-icon-core="slot"]')?.parentElement || root.querySelector('button');
              const slot=button?.querySelector('[data-mcel-icon-core="slot"]');
              let events=0;button?.addEventListener('click',e=>{e.stopImmediatePropagation();events++},true);
              const third=core.apply({root,ir,bridge,
                profiles:{theme:'theme-accessible',form:'form-pill',chrome:'chrome-strict-hierarchy'}});
              button?.dispatchEvent(new MouseEvent('click',{bubbles:true}));
              return {first,second,third,events,labelsUnchanged:JSON.stringify(before)===
                JSON.stringify([...root.querySelectorAll('button')].map(b=>b.textContent)),
                size:slot?getComputedStyle(slot).width:null,
                stroke:slot?getComputedStyle(slot.querySelector('svg')).strokeWidth:null,
                theme:root.getAttribute('data-mcel-icon-theme'),
                form:root.getAttribute('data-mcel-icon-form'),
                chrome:root.getAttribute('data-mcel-icon-chrome'),
                duplicates:[...root.querySelectorAll('button')].some(b=>b.querySelectorAll('[data-mcel-icon-core="slot"]').length>1)};
            }""")
            assert after["first"]["updated"] == 0, after
            assert after["second"]["updated"] == 0 and after["third"]["updated"] == 0, after
            assert after["events"] == 1 and after["labelsUnchanged"] and not after["duplicates"], after
            assert (after["theme"], after["form"], after["chrome"]) == (
                "theme-accessible", "form-pill", "chrome-strict-hierarchy"), after
            if any(expected.values()):
                assert after["size"] == "18px" and after["stroke"] == "2.8px", after
            else:
                assert after["size"] is None and after["stroke"] is None, after
        finally:
            browser.close()


def test_unavailable_icon_registry_cannot_block_theme_form_chrome_or_app(monkeypatch):
    """Missing optional icon assets degrade icons only, never app execution."""
    from playwright.sync_api import sync_playwright
    original = Path.read_text

    def missing_registry(self, *args, **kwargs):
        if self.name == "mcel-icons.v1.json":
            raise OSError("fault-injected unavailable optional icon registry")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", missing_registry)
    html = render_semantic_page("file-manager", ROOT, promoted=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=shutil.which("chromium") or None)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.set_content(html, wait_until="domcontentloaded", timeout=18000)
            report = page.evaluate("""() => ({
              mounted:document.querySelectorAll('#mcel-workbench [data-mcel-node]').length,
              report:window.__mcelPresentationReport,
              script:!!window.McelSemanticIconCore
            })""")
            assert errors == []
            assert report["mounted"] > 0
            assert report["report"]["status"] == "applied"
            assert report["report"]["steps"] == {"theme": "applied", "form": "applied", "chrome": "applied"}
            assert report["report"]["iconCore"]["status"] == "unavailable"
        finally:
            browser.close()


def test_icon_slots_reconcile_replaced_semantic_nodes_and_release_cleanly():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=shutil.which("chromium") or None)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        try:
            page.set_content(render_semantic_page("file-manager", ROOT, promoted=True),
                             wait_until="domcontentloaded", timeout=18000)
            before = page.evaluate("""() => {
              const holder=document.querySelector('[data-mcel-node$=".preview-file"]');
              const copy=holder.cloneNode(true);
              copy.querySelector('[data-mcel-icon-core="slot"]').remove();
              holder.replaceWith(copy);
              return copy.querySelectorAll('[data-mcel-icon-core="slot"]').length;
            }""")
            assert before == 0
            page.wait_for_function("""() => document.querySelector('[data-mcel-node$=".preview-file"]')
              ?.querySelector('[data-mcel-icon-identity="action.inspect"]') !== null""", timeout=3000)
            after = page.evaluate("""() => {
              const root=document.getElementById('mcel-workbench');
              const before=root.querySelectorAll('[data-mcel-icon-core="slot"]').length;
              const released=McelSemanticIconCore.release(root);
              return {before,released,after:root.querySelectorAll('[data-mcel-icon-core="slot"]').length,
                active:root.hasAttribute('data-mcel-icon-core'),
                buttons:root.querySelectorAll('button').length};
            }""")
            assert errors == [], errors
            assert after["before"] >= 6 and after["released"]
            assert after["after"] == 0 and not after["active"] and after["buttons"] > 0
        finally:
            browser.close()


def test_generated_p3_bridge_needs_exact_composition_and_refuses_missing_authority():
    """A P3 semantic mount has generated regions; no authored hierarchy is invented."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=shutil.which("chromium") or None)
        page = browser.new_page()
        try:
            page.set_content(HOSTS["task-manager-p3"](), wait_until="domcontentloaded", timeout=30000)
            result = page.evaluate("""() => {
              const root=document.getElementById('mcel-workbench');
              const payload=JSON.parse(document.getElementById('mcel-generated-data').textContent);
              return {
                without:McelSemanticPresentationBridge.observeRuntimePresentationTargets({root,ir:payload.ir}),
                withComposition:McelSemanticPresentationBridge.observeRuntimePresentationTargets({root,ir:payload.ir,composition:payload.composition}),
                operational:!!window.__mcelP3
              };
            }""")
            assert result["without"]["status"] == "unresolved", result
            assert result["withComposition"]["status"] == "source-bound", result
            assert result["withComposition"]["expectedControls"] == 16
            assert result["withComposition"]["expectedRegions"] == 8
            assert result["operational"]
        finally:
            browser.close()
