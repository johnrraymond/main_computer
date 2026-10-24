"""Official DSL -> shared MCEL generated notebook; no Task Manager runtime patch."""
from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_notebook_projection import (
    NotebookProjectionRefused, compile_notebook_preview, render_notebook_html,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "mcel_experiments/task-manager-notebook/application.js"
SAMPLE = ROOT / "mcel_experiments/task-manager-notebook/read-only-sample.json"
HOST = ROOT / "main_computer/web/applications/apps/task-manager.html"
HOST_SCRIPT = ROOT / "main_computer/web/applications/scripts/task-manager.js"


def canonical_ir():
    report = compile_dsl_application(SOURCE)
    assert report.valid, report.diagnostics
    return report.normalized_ir


def test_projected_html_is_standalone_source_derived_and_does_not_change_host():
    before = {name: name.read_bytes() for name in (HOST, HOST_SCRIPT)}
    html = compile_notebook_preview(SOURCE, fixture_path=SAMPLE)
    assert "<main id=\"preview\"></main>" in html
    assert "McelViewTransition" in html
    assert "McelTabWorkspace" in html
    assert 'Content-Security-Policy' in html
    assert "connect-src 'none'" in html
    assert '"appId":"task-manager-notebook"' in html
    assert 'setTaskNotebookTab' not in html
    assert {name: name.read_bytes() for name in before} == before
    assert html == compile_notebook_preview(SOURCE, fixture_path=SAMPLE)


def test_rendering_is_not_whole_application_certification():
    html = compile_notebook_preview(SOURCE)
    assert "not release-certified" in html
    assert '"status":"not-requested"' in html
    assert "Preview · No machine controls" in html


@pytest.mark.parametrize("mutation", [
    lambda ir: ir["effects"].append({"id": "effect:unauthorized"}),
    lambda ir: ir["capabilities"].append({"id": "capability:unauthorized"}),
    lambda ir: ir["intents"][0]["writes"].append({"ref": "state:unknown"}),
    lambda ir: ir["surfaces"][0]["semanticSurface"]["tabGroup"]["tabs"][1].update(
        panel=ir["surfaces"][0]["semanticSurface"]["tabGroup"]["tabs"][0]["panel"]),
    lambda ir: ir["surfaces"][0]["semanticSurface"]["tabGroup"].update(inactivePanelPolicy="destroy"),
])
def test_projection_refuses_untrusted_or_invalid_authority(mutation):
    ir = copy.deepcopy(canonical_ir())
    mutation(ir)
    with pytest.raises(NotebookProjectionRefused):
        render_notebook_html(ir)


def test_fixture_cannot_be_markup_injection():
    html = render_notebook_html(canonical_ir(), fixture={"processes": [
        {"danger": "</script><script>window.pwned=true</script>"}]})
    assert "</script><script>window.pwned=true</script>" not in html
    assert "\\u003c/script>" in html


def test_generator_rejects_non_record_fixture(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("[1,2,3]")
    with pytest.raises(NotebookProjectionRefused):
        compile_notebook_preview(SOURCE, fixture_path=p)


def test_live_generated_notebook_in_chromium(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    executable = shutil.which("chromium") or shutil.which("chromium-browser")
    with playwright.sync_playwright() as pw:
        kwargs = {"headless": True}
        if executable:
            kwargs.update(executable_path=executable, args=["--no-sandbox"])
        try:
            browser = pw.chromium.launch(**kwargs)
        except Exception as exc:
            pytest.skip(f"Chromium not available: {exc}")
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 850})
            errors = []
            requests = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("request", lambda r: requests.append(r.url))
            # file:// is unavailable in some sandboxed Chromium installations.
            # set_content exercises exactly the same emitted HTML and inline runtime.
            page.set_content(compile_notebook_preview(SOURCE, fixture_path=SAMPLE), wait_until="load")
            tabs = page.locator('[role="tab"]')
            panels = page.locator('[role="tabpanel"]')
            assert tabs.count() == panels.count() == 4
            assert tabs.all_text_contents() == ["Server Processes", "All Processes", "Connections", "CPU + GPU"]
            assert page.locator('[role="tab"][aria-selected="true"]').count() == 1
            assert page.locator('[role="tabpanel"]:visible').count() == 1
            assert page.locator('[role=tabpanel]:visible table tbody tr').count() == 3
            assert page.locator('[role=tabpanel]:visible table th').count() >= 3
            # The real click updates view state only; no capability/network requests.
            page.locator('[data-mcel-tab="connections"]').click()
            assert page.locator('[data-mcel-panel="connections"]').is_visible()
            assert page.locator('[data-mcel-panel="server-processes"]').is_hidden()
            assert page.locator('[role=tabpanel]:visible table tbody tr').count() == 2
            assert page.evaluate("location.hash") == "#tab=connections"
            initial = page.evaluate("McelNotebookPreview.getState()")
            refusal = page.evaluate("McelNotebookPreview.select('invalid-tab')")
            assert refusal == {"ok": False, "code": "invalid-selection", "effects": []}
            assert page.evaluate("McelNotebookPreview.getState()") == initial
            # View-only URL changes restore typed selection, including invalid refusal.
            page.evaluate("location.hash = '#tab=all-processes'")
            page.wait_for_function("McelNotebookPreview.getState().selected === 'all-processes'")
            assert page.locator('[data-mcel-panel="all-processes"]').is_visible()
            page.evaluate("location.hash = '#tab=invalid-tab'")
            page.wait_for_timeout(50)
            assert page.evaluate("McelNotebookPreview.getState().selected") == "all-processes"
            # Arrow/Home/End keyboard navigation, roving focus, ARIA selection.
            page.locator('[data-mcel-tab="connections"]').focus()
            page.keyboard.press("ArrowRight")
            assert page.locator('[data-mcel-tab="hardware"]').get_attribute("aria-selected") == "true"
            assert page.locator('[data-mcel-tab="hardware"]').evaluate("e => e === document.activeElement")
            page.keyboard.press("Home")
            assert page.locator('[data-mcel-tab="server-processes"]').get_attribute("aria-selected") == "true"
            page.keyboard.press("End")
            assert page.locator('[data-mcel-tab="hardware"]').get_attribute("aria-selected") == "true"
            # The generated stylesheet contains its own layout and table overflow.
            for width in (360, 768, 1280):
                page.set_viewport_size({"width": width, "height": 760})
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                assert page.locator('[role="tab"]').count() == 4
                assert page.locator('[role="tabpanel"]:visible').count() == 1
            # Empty/loading/error labels are generated rather than hard-coded into app markup.
            for status, label in (("loading", "Loading data"), ("empty", "No data"),
                                  ("failed", "Data unavailable")):
                outcome = page.evaluate("""([data,status]) => McelNotebookPreview.updateSnapshot(data,status)""",
                                        [{}, status])
                assert outcome == {"ok": True, "effects": []}
                assert label in page.locator('[role="tabpanel"]:visible').inner_text()
            # No external operational requests even when some data is displayed.
            assert not errors, errors
            assert not requests, requests
        finally:
            browser.close()


def test_generic_renderer_works_for_another_valid_tab_identity():
    ir = canonical_ir()
    ir = copy.deepcopy(ir)
    ir["application"]["title"] = "Different Notebook"
    tabs = ir["surfaces"][0]["semanticSurface"]["tabGroup"]["tabs"]
    tabs[0]["label"] = "Overview"
    html = render_notebook_html(ir)
    assert "Different Notebook" in html
    assert '"label":"Overview"' in html
