from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "main_computer" / "flog_mcel_runtime_smoke.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "flog_mcel_runtime_smoke",
        MODULE_PATH,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def flog():
    return load_module()


def passing_diagnosis(app_id: str = "calculator") -> dict:
    return {
        "schema": "mcel-self-diagnosis-report-v2",
        "appId": app_id,
        "contractId": f"{app_id}.contract.default.app-health",
        "mode": "default",
        "verdict": "pass",
        "summary": {"critical": 0, "warning": 0, "info": 18},
        "primarySurface": {
            "expected": f"{app_id}.surface.workspace",
            "usable": True,
            "exactlyOneAuthoritativeSurface": True,
            "host": {"exists": True, "visible": True},
        },
        "findings": [],
        "measurements": {
            "visualIntegrityViolations": [],
            "layoutCollisions": [],
            "contentFitViolations": [],
        },
    }


def passing_widget_payload(app_id: str = "calculator") -> dict:
    diagnosis = passing_diagnosis(app_id)
    primary = diagnosis["primarySurface"]
    return {
        "schema": "mcel-diagnostics-counter-copy-v4",
        "widgetVersion": "mcel-diagnostics-counter-widget-v4",
        "appId": app_id,
        "contractId": diagnosis["contractId"],
        "route": f"http://127.0.0.1:8765/applications/{app_id}",
        "timestamp": "2026-07-20T00:00:00+00:00",
        "verdict": "pass",
        "rawVerdict": "pass",
        "counts": {"errors": 0, "warnings": 0, "ok": 18},
        "current": {"counts": {"errors": 0, "warnings": 0, "ok": 18}, "issues": []},
        "primarySurface": primary,
        "measurements": {
            "visualIntegrityViolations": [],
            "layoutCollisions": [],
            "contentFitViolations": [],
        },
        "issues": [],
    }



def test_parse_viewport_defaults_to_desktop_baseline(flog):
    assert flog.parse_viewport("") == {"width": 1920, "height": 1200}
    assert flog.parse_viewport("desktop") == {"width": 1920, "height": 1200}
    assert flog.parse_viewport("1600x900") == {"width": 1600, "height": 900}


def test_parse_viewport_rejects_invalid_values(flog):
    with pytest.raises(ValueError):
        flog.parse_viewport("wide")
    with pytest.raises(ValueError):
        flog.parse_viewport("200x100")


def test_build_scenarios_uses_registry_app_contracts(flog):
    scenarios = flog.build_scenarios(REPO_ROOT)
    by_id = {scenario.id: scenario for scenario in scenarios}

    assert set(by_id) == {
        "calculator.default-load",
        "code-editor.default-load",
        "file-explorer.default-load",
        "git-tools.default-load",
        "mcel-lab.default-load",
        "website-builder.default-load",
    }
    assert by_id["website-builder.default-load"].route == "/applications/website-builder/hub-site"
    assert by_id["mcel-lab.default-load"].route == "/applications/mcel-lab"


def test_build_scenarios_can_filter_by_app_and_scenario(flog):
    scenarios = flog.build_scenarios(
        REPO_ROOT,
        apps=["code-editor", "calculator"],
        scenario_ids=["code-editor.default-load"],
    )

    assert [scenario.id for scenario in scenarios] == ["code-editor.default-load"]


def test_build_scenarios_rejects_unknown_scenario(flog):
    with pytest.raises(ValueError):
        flog.build_scenarios(REPO_ROOT, scenario_ids=["missing.default-load"])


def test_classify_diagnosis_passes_clean_report(flog):
    result = flog.classify_diagnosis(passing_diagnosis())

    assert result["status"] == "pass"
    assert result["counts"] == {"errors": 0, "warnings": 0, "infos": 18}
    assert result["primarySurface"]["usable"] is True
    assert result["failures"] == []


def test_classify_diagnosis_uses_summary_primary_surface_like_raw_mcel_report(flog):
    diagnosis = passing_diagnosis()
    diagnosis["summary"]["primarySurface"] = diagnosis.pop("primarySurface")

    result = flog.classify_diagnosis(diagnosis)

    assert result["status"] == "pass"
    assert result["primarySurface"]["usable"] is True
    assert result["primarySurface"]["exactlyOneAuthoritativeSurface"] is True


def test_classify_diagnosis_uses_widget_payload_counts_and_primary_surface(flog):
    payload = passing_widget_payload("website-builder")

    result = flog.classify_diagnosis(payload)

    assert result["status"] == "pass"
    assert result["verdict"] == "pass"
    assert result["counts"] == {"errors": 0, "warnings": 0, "infos": 18}
    assert result["primarySurface"]["expected"] == "website-builder.surface.workspace"


def test_compact_widget_payload_keeps_user_visible_diagnostic_shape(flog):
    payload = passing_widget_payload("code-editor")
    payload["measurements"]["hugeDomDump"] = ["not wanted"]

    compact = flog.compact_widget_payload(payload)

    assert compact["schema"] == "mcel-diagnostics-counter-copy-v4"
    assert compact["appId"] == "code-editor"
    assert compact["counts"] == {"errors": 0, "warnings": 0, "ok": 18}
    assert "hugeDomDump" not in compact["measurements"]


def test_classify_diagnosis_fails_on_warning_by_default(flog):
    diagnosis = passing_diagnosis()
    diagnosis["summary"]["warning"] = 1
    diagnosis["findings"] = [
        {
            "severity": "warning",
            "code": "visible-overlay-detected",
            "finding": "Overlay or diagnostic surfaces are visible.",
        }
    ]

    result = flog.classify_diagnosis(diagnosis)

    assert result["status"] == "fail"
    assert any("warning MCEL finding" in item for item in result["failures"])


def test_classify_diagnosis_can_allow_warnings(flog):
    diagnosis = passing_diagnosis()
    diagnosis["summary"]["warning"] = 1

    result = flog.classify_diagnosis(diagnosis, require_zero_warnings=False)

    assert result["status"] == "pass"
    assert result["warnings"]


def test_classify_diagnosis_fails_on_unusable_primary_surface(flog):
    diagnosis = passing_diagnosis()
    diagnosis["primarySurface"]["usable"] = False

    result = flog.classify_diagnosis(diagnosis)

    assert result["status"] == "fail"
    assert any("primary surface" in item for item in result["failures"])


def test_classify_diagnosis_fails_on_visual_integrity_violations(flog):
    diagnosis = passing_diagnosis()
    diagnosis["measurements"]["visualIntegrityViolations"] = [{"code": "overlap"}]

    result = flog.classify_diagnosis(diagnosis)

    assert result["status"] == "fail"
    assert result["visualIntegrityViolationCount"] == 1


def test_compact_diagnosis_keeps_contract_evidence_without_huge_dump(flog):
    diagnosis = passing_diagnosis("website-builder")
    diagnosis["measurements"]["hugeDomDump"] = ["not wanted"]

    compact = flog.compact_diagnosis(diagnosis)

    assert compact["appId"] == "website-builder"
    assert compact["contractId"] == "website-builder.contract.default.app-health"
    assert "hugeDomDump" not in compact["measurements"]


def test_diagnostic_event_from_trial_uses_shared_event_schema(flog):
    trial = {
        "scenarioId": "calculator.default-load",
        "app": "calculator",
        "route": "/applications/calculator",
        "finishedAt": "2026-07-20T00:00:00+00:00",
        "diagnosis": {
            "contractId": "calculator.contract.default.app-health",
            "verdict": "pass",
            "findings": [],
            "measurements": {"visualIntegrityViolations": []},
        },
        "widgetPayload": passing_widget_payload("calculator"),
        "classification": {
            "status": "pass",
            "counts": {"errors": 0, "warnings": 0, "infos": 18},
            "primarySurface": {"usable": True},
        },
    }

    event = flog.diagnostic_event_from_trial(trial)

    assert event["schema"] == "mcel-diagnostic-event-v1"
    assert event["source"] == "mcel-runtime-flog"
    assert event["appId"] == "calculator"
    assert event["counts"]["errors"] == 0
    assert event["counts"]["ok"] == 18
    assert event["rawVerdict"] == "pass"
    assert event["primarySurface"]["usable"] is True


def test_report_summary_and_markdown(flog):
    scenario = flog.scenario_for_app("calculator")
    trial = {
        "scenarioId": scenario.id,
        "app": scenario.app,
        "route": scenario.route,
        "classification": flog.classify_diagnosis(passing_diagnosis("calculator")),
    }
    report = flog.build_report(
        repo=REPO_ROOT,
        base_url="http://127.0.0.1:8765",
        scenarios=[scenario],
        trials=[trial],
        viewport={"width": 1920, "height": 1200},
    )

    assert report["kind"] == "mcel.flog.runtime-contracts.report"
    assert report["viewport"] == {"width": 1920, "height": 1200}
    assert report["summary"]["status"] == "pass"

    markdown = flog.render_markdown(report)

    assert "# MCEL Runtime FLOG Report" in markdown
    assert "calculator.default-load" in markdown
    assert "1920x1200" in markdown
    assert "window.MCEL.diagnose" in markdown
