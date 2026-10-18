"""Patch 15b: viewport-bound, independent, fail-closed layout certification."""
from __future__ import annotations

import copy
import os
import shutil
from pathlib import Path

import pytest

from main_computer import mcel_layout_certification as cert

ROOT = Path(__file__).resolve().parents[1]


def _browser_available():
    pytest.importorskip("playwright.sync_api")
    if not (os.getenv("MCEL_CHROMIUM_EXECUTABLE") or os.getenv("CHROMIUM_EXECUTABLE")
            or shutil.which("chromium") or shutil.which("chromium-browser")):
        # Playwright's default bundled binary can still be available on Windows.
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
                browser.close()
            except Exception as exc:
                pytest.skip(f"Chromium unavailable: {exc}")


def test_compiler_ir_projector_source_fingerprints_bind_layout_tree():
    for app in cert.SUPPORTED_APPS:
        pkg = cert.prepare_application(ROOT, app)
        assert pkg["semanticFingerprint"].startswith("sha256:")
        assert pkg["projectionFingerprint"] == pkg["manifest"]["projection"]["fingerprint"]
        assert pkg["packageFingerprint"] == pkg["manifest"]["source"]["packageFingerprint"]
        assert pkg["layoutTree"]["schema"] == "mcel.layout-tree.v1"
        assert pkg["layoutTree"]["root"]["role"] == "group"
        assert "layoutTree" in pkg["modules"]["layout"]
        assert all(value.startswith("sha256:") for value in pkg["inputs"].values())


def test_python_certificate_refuses_status_pass_when_evidence_is_incomplete():
    for app in cert.SUPPORTED_APPS:
        tree = cert.prepare_application(ROOT, app)["layoutTree"]
        forged = {"status": "pass"}
        missing = cert.validate_browser_observation(forged, tree, {"width": 1140})
        assert "missing-or-invalid-evidence:independent" in missing
        assert "layout-tree-coverage-incomplete" in missing
        assert "computed-css-observation-incomplete" in missing


def test_refuses_invalid_scenario_set_before_browser(monkeypatch):
    called = []
    def prepared(*args):
        called.append(args)
        return cert.prepare_application_original(*args)
    monkeypatch.setattr(cert, "prepare_application_original", cert.prepare_application, raising=False)
    monkeypatch.setattr(cert, "prepare_application", prepared)
    report = cert.run_certification(ROOT, app_ids=("calculator",), scenarios={"calculator": ({"id":"graph-wide","width":1140,"mode":"graphing"},)})
    assert report["passed"] is False and "scenarios missing" in report["error"]
    assert len(called) == 1


def test_fresh_browser_observation_proves_requested_viewport_or_refuses():
    _browser_available()
    report = cert.run_certification(ROOT)
    assert report["passed"] is True, [(a["appId"], [(s["id"], s["observed"].get("issues"),
        s["observed"].get("certificationRefusals")) for s in a["scenarios"]])
        for a in report["applications"]]
    assert report["status"] == "pass"
    assert report["scope"] == cert.SCOPE
    assert report["fullSemanticRuntimeProven"] is False
    assert report["evidenceFingerprint"].startswith("sha256:")
    assert len(report["applications"]) == 2
    for app in report["applications"]:
        pkg = cert.prepare_application(ROOT, app["appId"])
        assert len(app["scenarios"]) == len(cert.SCENARIOS[app["appId"]])
        for scenario in app["scenarios"]:
            observed = scenario["observed"]
            assert observed["viewport"]["innerWidth"] == scenario["width"]
            assert abs(observed["hostBox"]["width"] - scenario["width"]) <= 1.5
            assert observed["hostBox"]["height"] > 0
            if observed["status"] == "pass":
                assert not cert.validate_browser_observation(observed, pkg["layoutTree"], scenario)
                assert observed["treeMatch"] and observed["independent"] == "pass"
                assert observed["selectedCount"] == 1 and observed["feasibleCandidates"] > 0
                assert observed["atomicSplitCount"] == observed["layoutFailureCount"] == 0
                assert observed["restored"] and observed["authoredCSSUnchanged"]
            else:
                assert (observed.get("issues") or observed.get("certificationRefusals")
                        or observed.get("detail") or observed.get("reason")), scenario
    editor = next(a for a in report["applications"] if a["appId"] == "code-editor")
    wide, compact = editor["scenarios"]
    assert wide["observed"]["status"] == "pass", wide
    assert abs(wide["observed"]["canonicalBox"]["width"] - 1210) <= 5, wide
    assert compact["observed"]["viewport"]["innerWidth"] == 1050
    assert compact["observed"]["status"] == "pass"
    assert compact["observed"]["canonicalBox"]["width"] < wide["observed"]["canonicalBox"]["width"]
    for item,expected_areas,inspector_visible in ((wide, 'rail sidebar editor inspector', True),
                                                  (compact, 'rail sidebar editor', False)):
        observed=item["observed"]
        area=next(n for n in observed["generatedStructuralCSS"] if n["id"]=="workbench")
        inspector=next(n for n in observed["nodes"] if n["id"]=="inspector")
        assert area["areas"].replace('"', '').strip()==expected_areas
        assert inspector["visible"] is inspector_visible


def test_python_adjudicator_rejects_spoofed_width_and_host_overflow():
    tree = cert.prepare_application(ROOT, "code-editor")["layoutTree"]
    scenario = {"width": 1050, "mode": "dock", "id": "dock-compact"}
    forged = {"status": "pass", "issues": [],
        "viewport": {"innerWidth": 1480, "documentClientWidth": 1480},
        "hostBox": {"left": 0, "right": 1480, "width": 1480, "height": 920,
                    "clientWidth": 1400, "scrollWidth": 1480},
        "canonicalBox": {"left": 8, "right": 1472, "width": 1464, "height": 904}}
    errors = cert.validate_browser_observation(forged, tree, scenario)
    assert "browser-viewport-width-mismatch" in errors
    assert "host-viewport-containment-unproven" in errors
    assert "canonical-root-viewport-overflow" in errors
    assert "host-horizontal-overflow" in errors


def test_distinct_requested_widths_cannot_reuse_one_effective_width():
    items = [
        {"id": "dock-wide", "width": 1220,
         "observed": {"status": "pass", "viewport": {"innerWidth": 1220},
                      "hostBox": {"width": 1218}}},
        {"id": "dock-compact", "width": 1050,
         "observed": {"status": "pass", "viewport": {"innerWidth": 1050},
                      "hostBox": {"width": 1218}}},
    ]
    cert.enforce_distinct_widths(items)
    assert items[0]["observed"]["status"] == "pass"
    assert items[1]["observed"]["status"] == "fail"
    assert "distinct-scenarios-have-identical-effective-width" in items[1]["observed"]["certificationRefusals"]
    items[1]["observed"] = {"status": "pass", "viewport": {"innerWidth": 1050},
                            "hostBox": {"width": 1048}}
    cert.enforce_distinct_widths(items)
    assert items[1]["observed"]["status"] == "pass"


def test_broken_projection_fingerprint_is_not_certifiable(monkeypatch):
    _browser_available()
    original = cert.prepare_application
    def tampered(repo, app):
        source = original(repo, app)
        if app == "code-editor":
            source = {**source, "manifest": copy.deepcopy(source["manifest"])}
            source["manifest"]["source"]["packageFingerprint"] = "sha256:" + "0" * 64
        return source
    monkeypatch.setattr(cert, "prepare_application", tampered)
    report = cert.run_certification(ROOT, app_ids=("code-editor",))
    assert report["passed"] is False and report["status"] == "fail"
    assert report["applications"][0]["status"] == "fail"
    assert all(s["observed"]["status"] == "fail" for s in report["applications"][0]["scenarios"])
    assert "package fingerprint mismatch" in str(report["applications"][0]["scenarios"][0]["observed"]).lower()


def test_missing_layout_engine_is_never_silent_fallback(monkeypatch):
    _browser_available()
    monkeypatch.setattr(cert, "SCRIPTS", tuple(x for x in cert.SCRIPTS if "layout-inference" not in x))
    result = cert.run_certification(ROOT, app_ids=("calculator",))
    assert result["passed"] is False
    assert all(s["observed"]["status"] == "fail" for s in result["applications"][0]["scenarios"])
    assert all("layout runtime is unavailable" in str(s["observed"]) for s in result["applications"][0]["scenarios"])


def test_python_adjudicator_refuses_compact_scenario_with_visible_inspector():
    tree = cert.prepare_application(ROOT, "code-editor")["layoutTree"]
    observed = {"status": "pass", "issues": [],
        "nodes": [{"id": "inspector", "visible": True}],
        "generatedStructuralCSS": [{"id": "workbench", "areas": '"rail sidebar editor inspector"'}]}
    errors = cert.validate_browser_observation(observed, tree,
        {"id": "dock-compact", "width": 1050, "mode": "dock"})
    assert "code-editor-workbench-responsive-areas-unproven" in errors
    assert "code-editor-inspector-responsive-visibility-unproven" in errors
