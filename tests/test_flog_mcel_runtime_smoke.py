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


def passing_app_surface_conformance(app_id: str = "calculator", layer_ids: tuple[str, ...] | None = None) -> dict:
    layers = layer_ids or (
        "runtime-ownership",
        "runtime-visual-fit",
        "diagnostic-no-throw",
    )
    return {
        "contractVersion": "mcel.app-surface-conformance.v1",
        "appId": app_id,
        "status": "pass",
        "valid": True,
        "conformanceRequired": True,
        "requiredLayerIds": list(layers),
        "layers": [
            {
                "id": layer_id,
                "status": "pass",
                "valid": True,
                "finding": f"{layer_id} passed.",
                "detail": {},
            }
            for layer_id in layers
        ],
        "failedLayerIds": [],
        "unavailableLayerIds": [],
        "policyFailedLayerIds": [],
        "policyUnavailableLayerIds": [],
        "diagnosticCodes": [],
        "counts": {"errors": 0, "warnings": 0, "info": 0},
        "diagnostics": [],
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
        "appSurfaceConformance": passing_app_surface_conformance(app_id),
        "measurements": {
            "visualIntegrityViolations": [],
            "layoutCollisions": [],
            "contentFitViolations": [],
            "appSurfaceConformance": {
                "status": "pass",
                "valid": True,
                "failedLayerIds": [],
                "unavailableLayerIds": [],
            },
        },
        "issues": [],
    }



def passing_app_truth(app_id: str = "calculator") -> dict:
    return {
        "schema": "mcel-app-truth-snapshot-v1",
        "contractVersion": "mcel.app-truth-gate.v1",
        "appId": app_id,
        "generatedAt": "2026-07-27T11:00:00.000Z",
        "overallStatus": "runtime-proven",
        "requirements": {
            "present": True,
            "schemaValid": True,
            "contractComplete": True,
            "acceptanceContractCount": 1,
        },
        "adapter": {
            "registered": True,
            "runtimeCoreReady": True,
            "fullApplicationSemanticReady": False,
        },
        "surface": {
            "registered": True,
            "conformanceRequired": True,
        },
        "evidence": {
            "runtime": {
                "present": True,
                "fresh": True,
                "diagnosisCompleted": True,
                "policyPassed": True,
            },
            "acceptance": {
                "present": False,
                "passed": False,
            },
        },
        "claims": {
            "specified": True,
            "implementationPresent": True,
            "partiallyImplemented": True,
            "runtimeSurfaceProven": True,
            "acceptanceProven": False,
            "semanticRuntimeProven": False,
            "verificationComplete": False,
        },
        "findings": [
            {
                "code": "acceptance-test-missing",
                "severity": "warning",
                "blocking": False,
                "message": "Acceptance evidence is missing.",
                "detail": {"appId": app_id},
            }
        ],
        "findingCodes": ["acceptance-test-missing"],
    }


def passing_app_truth_snapshot(app_id: str = "calculator") -> dict:
    app_truth = passing_app_truth(app_id)
    return {
        "schema": "mcel-app-truth-snapshot-v1",
        "contractVersion": "mcel.app-truth-gate.v1",
        "generatedAt": "2026-07-27T11:00:00.000Z",
        "appCount": 1,
        "appIds": [app_id],
        "statusCounts": {"runtime-proven": 1},
        "findingCounts": {"acceptance-test-missing": 1},
        "apps": [app_truth],
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


def test_build_scenarios_uses_app_surface_registry_required_policies(flog):
    scenarios = flog.build_scenarios(REPO_ROOT)
    by_id = {scenario.id: scenario for scenario in scenarios}

    assert set(by_id) == {
        "calculator.default-load",
        "code-editor.default-load",
        "document.default-load",
        "file-explorer.default-load",
        "website-builder.default-load",
    }
    assert by_id["website-builder.default-load"].route == "/applications/website-builder/hub-site"
    assert by_id["document.default-load"].route == "/applications/document"
    assert by_id["document.default-load"].maturity == "semantic-runtime"
    assert by_id["document.default-load"].required_layer_ids == (
        "semantic-surface",
        "layout-grammar",
        "runtime-ownership",
        "runtime-visual-fit",
        "diagnostic-no-throw",
    )
    assert all(scenario.conformance_required for scenario in scenarios)


def test_build_scenarios_can_filter_by_app_and_scenario(flog):
    scenarios = flog.build_scenarios(
        REPO_ROOT,
        apps=["code-editor", "calculator"],
        scenario_ids=["code-editor.default-load"],
    )

    assert [scenario.id for scenario in scenarios] == ["code-editor.default-load"]


def test_build_scenarios_can_run_legacy_apps_explicitly_without_requiring_conformance(flog):
    scenarios = flog.build_scenarios(REPO_ROOT, apps=["mcel-lab"])

    assert [scenario.id for scenario in scenarios] == ["mcel-lab.default-load"]
    assert scenarios[0].route == "/applications/mcel-lab"
    assert scenarios[0].conformance_required is False
    assert scenarios[0].registry_state == "legacy"


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
    assert compact["appSurfaceConformance"]["status"] == "pass"
    assert compact["measurements"]["appSurfaceConformance"]["status"] == "pass"
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


def test_classify_diagnosis_fails_missing_required_app_surface_conformance(flog):
    diagnosis = passing_diagnosis("document")

    result = flog.classify_diagnosis(
        diagnosis,
        conformance_required=True,
        required_layer_ids=(
            "semantic-surface",
            "layout-grammar",
            "runtime-ownership",
            "runtime-visual-fit",
            "diagnostic-no-throw",
        ),
    )

    assert result["status"] == "fail"
    assert any("app-surface conformance summary is missing" in item for item in result["failures"])


def test_classify_diagnosis_fails_policy_failed_app_surface_layers(flog):
    diagnosis = passing_diagnosis("document")
    conformance = passing_app_surface_conformance(
        "document",
        (
            "semantic-surface",
            "layout-grammar",
            "runtime-ownership",
            "runtime-visual-fit",
            "diagnostic-no-throw",
        ),
    )
    conformance["status"] = "fail"
    conformance["valid"] = False
    conformance["layers"][1]["status"] = "fail"
    conformance["layers"][1]["valid"] = False
    conformance["failedLayerIds"] = ["layout-grammar"]
    conformance["policyFailedLayerIds"] = ["layout-grammar"]
    diagnosis["appSurfaceConformance"] = conformance
    diagnosis["measurements"]["appSurfaceConformance"] = {
        "status": "fail",
        "valid": False,
        "failedLayerIds": ["layout-grammar"],
        "unavailableLayerIds": [],
    }

    result = flog.classify_diagnosis(
        diagnosis,
        conformance_required=True,
        required_layer_ids=tuple(conformance["requiredLayerIds"]),
    )

    assert result["status"] == "fail"
    assert "app-surface layer failed: layout-grammar" in result["failures"]
    assert result["appSurfaceConformance"]["status"] == "fail"


def test_classify_diagnosis_respects_runtime_baseline_policy_scope(flog):
    diagnosis = passing_diagnosis("code-editor")
    conformance = passing_app_surface_conformance("code-editor")
    conformance["layers"] = [
        {
            "id": "semantic-surface",
            "status": "unavailable",
            "valid": False,
            "finding": "Static semantic extraction is not required for this host-workbench smoke.",
            "detail": {},
        },
        {
            "id": "layout-grammar",
            "status": "unavailable",
            "valid": False,
            "finding": "Static layout extraction is not required for this host-workbench smoke.",
            "detail": {},
        },
        *conformance["layers"],
    ]
    conformance["unavailableLayerIds"] = ["semantic-surface", "layout-grammar"]
    conformance["policyUnavailableLayerIds"] = []
    diagnosis["appSurfaceConformance"] = conformance

    result = flog.classify_diagnosis(
        diagnosis,
        conformance_required=True,
        required_layer_ids=(
            "runtime-ownership",
            "runtime-visual-fit",
            "diagnostic-no-throw",
        ),
    )

    assert result["status"] == "pass"
    assert result["failures"] == []
    assert result["appSurfacePolicyScope"]["status"] == "pass"
    assert result["appSurfacePolicyScope"]["nonRequiredUnavailableLayerIds"] == [
        "semantic-surface",
        "layout-grammar",
    ]


def test_classify_diagnosis_does_not_promote_non_policy_static_failures(flog):
    diagnosis = passing_diagnosis("website-builder")
    conformance = passing_app_surface_conformance("website-builder")
    conformance["layers"] = [
        {
            "id": "semantic-surface",
            "status": "fail",
            "valid": False,
            "finding": "Static semantic extraction is not required for this runtime-baseline smoke.",
            "detail": {},
        },
        {
            "id": "layout-grammar",
            "status": "fail",
            "valid": False,
            "finding": "Static layout extraction is not required for this runtime-baseline smoke.",
            "detail": {},
        },
        *conformance["layers"],
    ]
    conformance["failedLayerIds"] = ["semantic-surface", "layout-grammar"]
    conformance["policyFailedLayerIds"] = []
    diagnosis["appSurfaceConformance"] = conformance

    result = flog.classify_diagnosis(
        diagnosis,
        conformance_required=True,
        required_layer_ids=(
            "runtime-ownership",
            "runtime-visual-fit",
            "diagnostic-no-throw",
        ),
    )

    assert result["status"] == "pass"
    assert result["failures"] == []
    assert result["appSurfacePolicyScope"]["status"] == "pass"
    assert result["appSurfacePolicyScope"]["failedLayerIds"] == []
    assert result["appSurfacePolicyScope"]["nonRequiredFailedLayerIds"] == [
        "semantic-surface",
        "layout-grammar",
    ]


def test_classify_diagnosis_fails_on_content_fit_violations(flog):
    diagnosis = passing_diagnosis("document")
    diagnosis["measurements"]["contentFitViolations"] = [{"code": "header-clipped"}]

    result = flog.classify_diagnosis(diagnosis)

    assert result["status"] == "fail"
    assert result["contentFitViolationCount"] == 1


def test_compact_diagnosis_keeps_contract_evidence_without_huge_dump(flog):
    diagnosis = passing_diagnosis("website-builder")
    diagnosis["appSurfaceConformance"] = passing_app_surface_conformance("website-builder")
    diagnosis["measurements"]["hugeDomDump"] = ["not wanted"]

    compact = flog.compact_diagnosis(diagnosis)

    assert compact["appId"] == "website-builder"
    assert compact["contractId"] == "website-builder.contract.default.app-health"
    assert compact["appSurfaceConformance"]["status"] == "pass"
    assert compact["measurements"]["appSurfaceConformance"]["status"] == "pass"
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
            "appSurfaceConformance": passing_app_surface_conformance("calculator"),
        },
        "appTruth": passing_app_truth("calculator"),
    }

    event = flog.diagnostic_event_from_trial(trial)

    assert event["schema"] == "mcel-diagnostic-event-v1"
    assert event["source"] == "mcel-runtime-flog"
    assert event["appId"] == "calculator"
    assert event["counts"]["errors"] == 0
    assert event["counts"]["ok"] == 18
    assert event["rawVerdict"] == "pass"
    assert event["primarySurface"]["usable"] is True
    assert event["appSurfaceConformance"]["status"] == "pass"
    assert event["appSurfacePolicyScope"]["status"] == "pass"
    assert event["appTruth"]["overallStatus"] == "runtime-proven"
    assert event["appTruth"]["claims"]["semanticRuntimeProven"] is False


def test_report_summary_and_markdown(flog):
    scenario = flog.scenario_for_app("calculator")
    diagnosis = passing_diagnosis("calculator")
    diagnosis["appSurfaceConformance"] = passing_app_surface_conformance("calculator")
    trial = {
        "scenarioId": scenario.id,
        "app": scenario.app,
        "route": scenario.route,
        "appSurfacePolicy": scenario.to_dict()["appSurfacePolicy"],
        "classification": flog.classify_diagnosis(
            diagnosis,
            conformance_required=scenario.conformance_required,
            required_layer_ids=scenario.required_layer_ids,
        ),
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
    assert report["source"]["scenarioSource"] == "mcel-app-surface-registry-conformance-required-apps-with-route-overrides"
    assert report["results"][0]["scenarioId"] == "calculator.default-load"
    assert report["results"][0]["status"] == "pass"
    assert report["results"][0]["appSurfaceConformance"]["status"] == "pass"
    assert report["results"][0]["appSurfacePolicyScope"]["status"] == "pass"

    markdown = flog.render_markdown(report)

    assert "# MCEL Runtime FLOG Report" in markdown
    assert "calculator.default-load" in markdown
    assert "1920x1200" in markdown
    assert "window.MCEL.diagnose" in markdown


def test_report_marks_non_required_layer_noise_without_failing_policy(flog):
    scenario = flog.scenario_for_app("website-builder")
    diagnosis = passing_diagnosis("website-builder")
    conformance = passing_app_surface_conformance("website-builder")
    conformance["layers"] = [
        {
            "id": "semantic-surface",
            "status": "fail",
            "valid": False,
            "finding": "Website Builder static extraction is not required for runtime-baseline.",
            "detail": {},
        },
        {
            "id": "layout-grammar",
            "status": "fail",
            "valid": False,
            "finding": "Website Builder static layout is not required for runtime-baseline.",
            "detail": {},
        },
        *conformance["layers"],
    ]
    conformance["failedLayerIds"] = ["semantic-surface", "layout-grammar"]
    conformance["policyFailedLayerIds"] = []
    diagnosis["appSurfaceConformance"] = conformance
    classification = flog.classify_diagnosis(
        diagnosis,
        conformance_required=scenario.conformance_required,
        required_layer_ids=scenario.required_layer_ids,
    )
    trial = {
        "scenarioId": scenario.id,
        "app": scenario.app,
        "route": scenario.route,
        "appSurfacePolicy": scenario.to_dict()["appSurfacePolicy"],
        "diagnosis": flog.compact_diagnosis(diagnosis),
        "classification": classification,
    }
    report = flog.build_report(
        repo=REPO_ROOT,
        base_url="http://127.0.0.1:8765",
        scenarios=[scenario],
        trials=[trial],
        viewport={"width": 1920, "height": 1200},
    )

    result = report["results"][0]
    assert result["status"] == "pass"
    assert result["appSurfacePolicyScope"]["status"] == "pass"
    assert result["appSurfacePolicyScope"]["nonRequiredFailedLayerIds"] == [
        "semantic-surface",
        "layout-grammar",
    ]

    markdown = flog.render_markdown(report)
    assert "| website-builder.default-load | website-builder | pass" in markdown
    assert "non-required failed layers: semantic-surface, layout-grammar" in markdown


def test_report_results_surface_failed_visual_evidence(flog):
    scenario = flog.scenario_for_app("mcel-lab")
    diagnosis = passing_diagnosis("mcel-lab")
    diagnosis["verdict"] = "fail"
    diagnosis["summary"]["critical"] = 1
    diagnosis["findings"] = [
        {
            "severity": "critical",
            "code": "visual-integrity-violation",
            "finding": "Rendered semantic surfaces collide.",
        }
    ]
    diagnosis["measurements"]["visualIntegrityViolations"] = [
        {
            "type": "readable-text-outside-owner",
            "owner": {"selector": "details.mcel-lab-work-context"},
        }
    ]

    trial = {
        "scenarioId": scenario.id,
        "app": scenario.app,
        "route": scenario.route,
        "diagnosis": flog.compact_diagnosis(diagnosis),
        "classification": flog.classify_diagnosis(diagnosis),
    }
    report = flog.build_report(
        repo=REPO_ROOT,
        base_url="http://127.0.0.1:8765",
        scenarios=[scenario],
        trials=[trial],
        viewport={"width": 1920, "height": 1200},
    )

    assert report["summary"]["status"] == "fail"
    assert report["results"][0]["status"] == "fail"
    assert report["results"][0]["issueEvidence"][0]["code"] == "visual-integrity-violation"
    assert report["results"][0]["visualIntegrityViolations"][0]["type"] == "readable-text-outside-owner"

    markdown = flog.render_markdown(report)
    assert "Failed scenario evidence" in markdown
    assert "visual-integrity-violation" in markdown
    assert "details.mcel-lab-work-context" in markdown


def test_app_truth_runtime_evidence_from_trial_preserves_timestamp_and_policy(flog):
    scenario = flog.scenario_for_app("calculator")
    trial = {
        "scenarioId": scenario.id,
        "app": scenario.app,
        "route": scenario.route,
        "finishedAt": "2026-07-27T11:00:00+00:00",
        "classification": {
            "status": "pass",
            "counts": {"errors": 0, "warnings": 0, "infos": 18},
            "primarySurface": {"usable": True},
            "appSurfaceConformance": passing_app_surface_conformance("calculator"),
            "appSurfacePolicyScope": {
                "status": "pass",
                "failedLayerIds": [],
                "unavailableLayerIds": [],
            },
            "requiredLayerIds": [
                "runtime-ownership",
                "runtime-visual-fit",
                "diagnostic-no-throw",
            ],
            "failures": [],
            "warnings": [],
        },
    }

    evidence = flog.app_truth_runtime_evidence_from_trial(trial)

    assert evidence["appId"] == "calculator"
    assert evidence["app"] == "calculator"
    assert evidence["finishedAt"] == "2026-07-27T11:00:00+00:00"
    assert evidence["generatedAt"] == "2026-07-27T11:00:00+00:00"
    assert evidence["appSurfacePolicyScope"]["status"] == "pass"


def test_report_attaches_gate_truth_without_changing_surface_verdict(flog):
    scenario = flog.scenario_for_app("calculator")
    app_truth = passing_app_truth("calculator")
    truth_snapshot = passing_app_truth_snapshot("calculator")
    trial = {
        "scenarioId": scenario.id,
        "app": scenario.app,
        "route": scenario.route,
        "finishedAt": "2026-07-27T11:00:00+00:00",
        "appSurfacePolicy": scenario.to_dict()["appSurfacePolicy"],
        "classification": {
            "status": "pass",
            "counts": {"errors": 0, "warnings": 0, "infos": 18},
            "primarySurface": {"usable": True},
            "appSurfaceConformance": passing_app_surface_conformance("calculator"),
            "appSurfacePolicyScope": {"status": "pass"},
            "requiredLayerIds": list(scenario.required_layer_ids),
            "failures": [],
            "warnings": [],
        },
        "appTruthAvailable": True,
        "appTruth": app_truth,
        "appTruthSnapshot": truth_snapshot,
    }

    report = flog.build_report(
        repo=REPO_ROOT,
        base_url="http://127.0.0.1:8765",
        scenarios=[scenario],
        trials=[trial],
        viewport={"width": 1920, "height": 1200},
    )

    assert report["summary"]["status"] == "pass"
    assert report["summary"]["truthStatusCounts"] == {"runtime-proven": 1}
    assert report["summary"]["runtimeSurfaceProvenCount"] == 1
    assert report["summary"]["semanticRuntimeProvenCount"] == 0
    assert report["summary"]["truthFindingCounts"] == {"acceptance-test-missing": 1}
    assert report["results"][0]["appTruth"]["overallStatus"] == "runtime-proven"
    assert report["appTruthSnapshot"] == truth_snapshot
    assert report["source"]["truthSource"] == (
        "window.McelAppTruthGate evaluateAppTruth/buildTruthSnapshot"
    )

    markdown = flog.render_markdown(report)
    assert "## App truth" in markdown
    assert "| calculator | runtime-proven | yes | no | no | acceptance-test-missing |" in markdown
    assert "Truth findings do not rewrite the FLOG surface verdict" in markdown
    assert "truth gate keeps requirements, adapter, acceptance, and semantic readiness" in markdown


def test_latest_app_truth_snapshot_uses_last_gate_built_snapshot(flog):
    first = passing_app_truth_snapshot("calculator")
    second = passing_app_truth_snapshot("code-editor")
    trials = [
        {"appTruthSnapshot": first},
        {"appTruthSnapshot": {}},
        {"appTruthSnapshot": second},
    ]

    assert flog.latest_app_truth_snapshot(trials) == second
    assert flog.latest_app_truth_snapshot([{"appTruthSnapshot": {}}]) == {}

