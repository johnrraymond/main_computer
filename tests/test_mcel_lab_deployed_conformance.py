from __future__ import annotations

import inspect
from pathlib import Path

from main_computer import flog_mcel_runtime_smoke as flog
from main_computer import mcel_lab_deployed_conformance as deployed


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "pretty_docs" / "mcel-lab-blueprint-studio.md"


def _passing_trial(
    *,
    width: float = 700,
    height: float = 500,
    stacked: bool = False,
) -> dict:
    return {
        "classification": {
            "status": "pass",
            "failures": [],
            "appSurfaceConformance": {
                "status": "pass",
                "valid": True,
                "layers": [
                    {
                        "id": "runtime-ownership",
                        "status": "pass",
                        "valid": True,
                    },
                    {
                        "id": "runtime-visual-fit",
                        "status": "pass",
                        "valid": True,
                    },
                    {
                        "id": "diagnostic-no-throw",
                        "status": "pass",
                        "valid": True,
                    },
                ],
            },
        },
        "supplementalEvidence": {
            "schema": "mcel-lab-deployed-geometry-v2",
            "route": "/applications/mcel-lab",
            "hostMatchCount": 1,
            "editorMatchCount": 1,
            "editorRect": {
                "x": 10,
                "y": 20,
                "width": width,
                "height": height,
                "right": 10 + width,
                "bottom": 20 + height,
            },
            "workbench": {
                "gridColumnCount": 1 if stacked else 2,
                "overflowY": "visible" if stacked else "hidden",
                "contentSized": True,
            },
            "rail": {
                "overflowRequired": True,
                "scrollableWhenRequired": True,
            },
            "internallyClippedRailChildren": [],
            "siblingOverlaps": [],
            "stackedOrder": True,
            "semanticForm": {
                "selectedApp": "mcel-lab",
                "selectedAspect": "form",
                "viewerCount": 1,
                "cardCount": 9,
                "groupCount": 8,
                "primitiveKindCounts": dict(deployed.EXPECTED_FORM_KIND_COUNTS),
                "sourceBoundCardCount": 9,
                "contractStatusCardCount": 9,
                "sourceFactCardCount": 9,
                "internallyClippedCardIds": [],
                "cardOverlaps": [],
                "workSurfaceOverflowRequired": True,
                "workSurfaceScrollableWhenRequired": True,
            },
        },
    }


def test_authorized_viewports_keep_canonical_desktop_probe_last() -> None:
    assert [
        (profile.name, profile.width, profile.height, profile.layout)
        for profile in deployed.VIEWPORT_PROFILES
    ] == [
        ("desktop-1280x720", 1280, 720, "desktop"),
        ("stacked-900x900", 900, 900, "stacked"),
        ("desktop-1440x900", 1440, 900, "desktop"),
    ]
    assert deployed.VIEWPORT_PROFILES[-1].requires_desktop_minimum is True


def test_desktop_geometry_requires_authoritative_surface_and_minimum_size() -> None:
    profile = deployed.VIEWPORT_PROFILES[0]

    passing = deployed.classify_geometry(profile, _passing_trial())
    too_small = deployed.classify_geometry(
        profile,
        _passing_trial(width=639, height=419),
    )

    assert passing["status"] == "pass"
    assert too_small["status"] == "fail"
    assert any("below 640px" in item for item in too_small["failures"])
    assert any("below 420px" in item for item in too_small["failures"])


def test_deployed_geometry_rejects_clipping_overlap_and_unscrollable_rail() -> None:
    trial = _passing_trial()
    geometry = trial["supplementalEvidence"]
    geometry["internallyClippedRailChildren"] = [".selected-card"]
    geometry["siblingOverlaps"] = [{"left": ".nav", "right": ".primary"}]
    geometry["rail"]["scrollableWhenRequired"] = False

    result = deployed.classify_geometry(deployed.VIEWPORT_PROFILES[0], trial)

    assert result["status"] == "fail"
    assert any("do not contain accessible internal content" in item for item in result["failures"])
    assert any("not scrollable" in item for item in result["failures"])
    assert any("sibling overlap" in item for item in result["failures"])


def test_semantic_form_geometry_requires_registry_counts_provenance_and_fit() -> None:
    passing = deployed.classify_geometry(
        deployed.VIEWPORT_PROFILES[0],
        _passing_trial(),
    )
    assert passing["status"] == "pass"

    failing_trial = _passing_trial()
    semantic_form = failing_trial["supplementalEvidence"]["semanticForm"]
    semantic_form["cardCount"] = 8
    semantic_form["groupCount"] = 7
    semantic_form["primitiveKindCounts"]["context"] = 1
    semantic_form["sourceBoundCardCount"] = 8
    semantic_form["contractStatusCardCount"] = 8
    semantic_form["sourceFactCardCount"] = 8
    semantic_form["internallyClippedCardIds"] = ["mcel-lab.form.context.implementation-evidence"]
    semantic_form["cardOverlaps"] = [{"left": "subject", "right": "action"}]
    semantic_form["workSurfaceScrollableWhenRequired"] = False

    failing = deployed.classify_geometry(
        deployed.VIEWPORT_PROFILES[0],
        failing_trial,
    )

    assert failing["status"] == "fail"
    assert any("expected 9" in item for item in failing["failures"])
    assert any("expected 8" in item for item in failing["failures"])
    assert any("kind counts differ" in item for item in failing["failures"])
    assert any("exact source provenance" in item for item in failing["failures"])
    assert any("Contract status" in item for item in failing["failures"])
    assert any("exposes its Source" in item for item in failing["failures"])
    assert any("clip readable content" in item for item in failing["failures"])
    assert any("primitive card overlap" in item for item in failing["failures"])
    assert any("work surface is not scrollable" in item for item in failing["failures"])


def test_stacked_geometry_requires_content_sized_single_column_source_order() -> None:
    profile = deployed.VIEWPORT_PROFILES[1]
    passing = deployed.classify_geometry(
        profile,
        _passing_trial(width=850, height=460, stacked=True),
    )

    failing_trial = _passing_trial(width=850, height=460, stacked=True)
    workbench = failing_trial["supplementalEvidence"]["workbench"]
    workbench["gridColumnCount"] = 2
    workbench["overflowY"] = "auto"
    workbench["contentSized"] = False
    failing_trial["supplementalEvidence"]["stackedOrder"] = False
    failing = deployed.classify_geometry(profile, failing_trial)

    assert passing["status"] == "pass"
    assert failing["status"] == "fail"
    assert any("one grid column" in item for item in failing["failures"])
    assert any("internal vertical scroll owner" in item for item in failing["failures"])
    assert any("content-sized block flow" in item for item in failing["failures"])
    assert any("source order" in item for item in failing["failures"])


def test_runtime_flog_supports_read_only_trial_probe_and_explicit_browser() -> None:
    parameters = inspect.signature(flog.run_browser_scenarios).parameters

    assert "trial_probe" in parameters
    assert "browser_executable" in parameters
    assert "supplementalEvidence" in inspect.getsource(flog.run_browser_scenarios)


def test_deployed_report_preserves_flog_schema_and_marks_profile_failure(monkeypatch) -> None:
    scenario = flog.RuntimeScenario(
        id="mcel-lab.default-load",
        app="mcel-lab",
        route="/applications/mcel-lab",
        intent="test",
    )

    monkeypatch.setattr(
        deployed.flog,
        "build_report",
        lambda **kwargs: {
            "schema": flog.REPORT_SCHEMA,
            "version": flog.REPORT_VERSION,
            "repositoryProvenance": {"fingerprint": "abc"},
            "source": {},
            "summary": {"status": "pass"},
            "results": [],
            "trials": kwargs["trials"],
        },
    )
    profiles = [
        {
            "profile": profile.to_dict(),
            "status": "pass",
            "failures": [],
            "warnings": [],
            "geometry": {},
        }
        for profile in deployed.VIEWPORT_PROFILES
    ]
    profiles[1]["status"] = "fail"
    profiles[1]["failures"] = ["stacked proof failed"]

    report = deployed.build_report(
        repo=ROOT,
        base_url="http://127.0.0.1:8765",
        scenario=scenario,
        trials=[{"app": "mcel-lab"}],
        profile_results=profiles,
    )

    assert report["schema"] == flog.REPORT_SCHEMA
    assert report["mcelLabDeployedConformance"]["status"] == "fail"
    assert report["summary"]["status"] == "fail"
    assert report["summary"]["deployedConformanceStatus"] == "fail"
    assert report["summary"]["semanticFormConformanceStatus"] == "fail"


def test_documentation_defines_exact_deployed_evidence_sequence() -> None:
    source = DOC.read_text(encoding="utf-8")

    assert "mcel_lab_deployed_conformance.py" in source
    assert "--base-url http://127.0.0.1:8765" in source
    assert "mcel_acceptance_runner.py --app mcel-lab --check" in source
    assert "mcel_truth_audit.py --release-gate" in source
    assert "1280 × 720" in source
    assert "1440 × 900" in source
    assert "900 × 900" in source



def test_deployed_fixture_defaults_to_noncanonical_report_directory() -> None:
    assert deployed.DEFAULT_OUTPUT_DIR == Path(
        "runtime/reports/flog/mcel-lab-deployed-conformance"
    )


def test_deployed_report_declares_partial_evidence_scope(monkeypatch) -> None:
    scenario = flog.RuntimeScenario(
        id="mcel-lab.default-load",
        app="mcel-lab",
        route="/applications/mcel-lab",
        intent="test",
    )
    captured: dict = {}

    def fake_build_report(**kwargs):
        captured.update(kwargs)
        return {
            "schema": flog.REPORT_SCHEMA,
            "version": flog.REPORT_VERSION,
            "repositoryProvenance": {"fingerprint": "abc"},
            "evidenceScope": kwargs["evidence_scope"],
            "source": {},
            "summary": {"status": "pass"},
            "results": [],
            "trials": kwargs["trials"],
        }

    monkeypatch.setattr(deployed.flog, "build_report", fake_build_report)
    profiles = [
        {
            "profile": profile.to_dict(),
            "status": "pass",
            "failures": [],
            "warnings": [],
            "geometry": {},
        }
        for profile in deployed.VIEWPORT_PROFILES
    ]
    report = deployed.build_report(
        repo=ROOT,
        base_url="http://127.0.0.1:8765",
        scenario=scenario,
        trials=[
            {
                "scenarioId": f"mcel-lab.deployed-{profile.name}",
                "app": "mcel-lab",
            }
            for profile in deployed.VIEWPORT_PROFILES
        ],
        profile_results=profiles,
    )

    assert captured["evidence_scope"]["kind"] == "deployed-app-scoped"
    assert report["evidenceScope"]["canonical"] is False
    assert report["evidenceScope"]["selectedApps"] == ["mcel-lab"]

