from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from main_computer import mcel_app_prove as prove
from main_computer.mcel_application_packages import build_application_package_catalog
from main_computer.mcel_application_runtime_projection import build_runtime_projection_set
from main_computer.mcel_evidence_provenance import build_repository_provenance


ROOT = Path(__file__).resolve().parents[1]


def _evidence():
    catalog = build_application_package_catalog(ROOT)
    record = next(item for item in catalog.packages if item.app_id == "contract-counter")
    projection = next(item for item in build_runtime_projection_set(ROOT).projections if item.app_id == "contract-counter")
    provenance = build_repository_provenance(ROOT)
    acceptance = {
        "schema": "mcel-acceptance-evidence-report-v1",
        "generatedAt": "2026-08-01T02:00:00Z",
        "status": "pass",
        "passed": True,
        "evidenceScope": {"kind": "app-scoped", "selectedApps": ["contract-counter"]},
        "repositoryProvenance": provenance,
        "applicationPackages": [{"appId": "contract-counter", "packageFingerprint": record.fingerprint}],
        "results": [{
            "appId": "contract-counter",
            "status": "pass",
            "passed": True,
            "testCount": 1,
            "contracts": [{"contractId": "contract-counter.acceptance.operation-control", "status": "pass"}],
        }],
    }
    layers = {layer: "pass" for layer in prove.REQUIRED_SURFACE_LAYERS}
    observation = {
        "schema": "mcel.application-operation-observation-report.v1",
        "generatedAt": "2026-08-01T02:01:00Z",
        "status": "pass",
        "ok": True,
        "evidenceScope": "app-scoped",
        "appId": "contract-counter",
        "url": "http://localhost/mcel-package-host.html?app=contract-counter",
        "package": {"fingerprint": record.fingerprint},
        "catalogFingerprint": catalog.fingerprint,
        "repositoryProvenance": provenance,
        "observation": {
            "runtimeProjectionFingerprint": projection.fingerprint,
            "repositoryFingerprint": provenance["fingerprint"],
            "comparison": {"stateMatches": True, "receiptMatches": True, "surfaceMatches": True},
        },
        "surfaceConformance": {
            "status": "pass",
            "valid": True,
            "surfaceId": "contract-counter.surface.primary",
            "requiredLayerStatuses": layers,
        },
    }
    return catalog, record, projection, provenance, acceptance, observation


def _ir_native_coverage() -> dict:
    return {
        "schema": "mcel.ir-native-intent-complete-proof.v1",
        "appId": "contract-counter",
        "status": "ir-native",
        "passed": True,
        "applicable": True,
        "coverageMode": "authoritative-dsl-ir-runtime-convergence",
        "legacyEvidenceRequired": False,
        "definitionFingerprint": "sha256:semantic",
        "declaredIntentCount": 3,
        "coveredIntentCount": 3,
        "declaredScenarioCount": 4,
        "observedScenarioCount": 4,
        "failedIntentIds": [],
        "missingScenarioIds": [],
        "unexpectedScenarioIds": [],
        "failedScenarioIds": [],
        "crossCuttingChecks": {"legacyEvidenceRequired": False},
        "intents": {},
    }


def test_app_proof_composes_independent_authorities(monkeypatch) -> None:
    _catalog, _record, _projection, _provenance, acceptance, observation = _evidence()

    monkeypatch.setattr(prove, "_run_dependency", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(prove, "run_counter_ir_native_intent_proof", lambda **_kwargs: _ir_native_coverage())
    def fake_load(path, _label):
        if path.name == "mcel.app.json":
            return {"authoring": {"status": "dsl-authoritative"}}
        return acceptance if "mcel-acceptance" in path.as_posix() else observation

    monkeypatch.setattr(prove, "_load_json", fake_load)
    monkeypatch.setattr(
        prove,
        "_artifact_reference",
        lambda path, _repo, payload: {"path": path.as_posix(), "sha256": "test", "schema": payload.get("schema"), "status": payload.get("status")},
    )

    report = prove.run_app_proof(repo=ROOT, app_id="contract-counter")

    assert report["status"] == "pass"
    assert report["truthStatus"] == "semantic-runtime-proven"
    assert report["truthSnapshot"]["claims"]["semanticRuntimeProven"] is True
    assert report["stages"]["repositoryBinding"]["status"] == "exact"
    assert report["stages"]["surfaceConformance"]["status"] == "pass"
    assert report["stages"]["intentCompleteProof"]["status"] == "ir-native"
    assert report["stages"]["intentCompleteProof"]["applicable"] is True
    assert report["stages"]["intentCompleteProof"]["legacyEvidenceRequired"] is False
    assert report["intentCoverage"]["declaredIntentCount"] == 3
    assert report["intentCoverage"]["coveredIntentCount"] == 3
    assert "intentCompleteProof" in report["evidence"]


def test_app_proof_rejects_stale_observation_package_fingerprint() -> None:
    catalog, record, projection, provenance, acceptance, observation = _evidence()
    observation["package"]["fingerprint"] = "sha256:stale"

    with pytest.raises(prove.AppProofError, match="package fingerprint is stale"):
        prove._assert_evidence_alignment(
            app_id="contract-counter",
            record=record,
            catalog=catalog,
            projection=projection,
            provenance=provenance,
            acceptance=acceptance,
            observation=observation,
        )


def test_app_proof_rejects_missing_required_surface_layer() -> None:
    catalog, record, projection, provenance, acceptance, observation = _evidence()
    observation["surfaceConformance"]["requiredLayerStatuses"]["runtime-visual-fit"] = "unavailable"

    with pytest.raises(prove.AppProofError, match="runtime-visual-fit"):
        prove._assert_evidence_alignment(
            app_id="contract-counter",
            record=record,
            catalog=catalog,
            projection=projection,
            provenance=provenance,
            acceptance=acceptance,
            observation=observation,
        )



def test_legacy_intent_coverage_is_non_vacuous_evidence_status() -> None:
    _catalog, record, _projection, _provenance, acceptance, observation = _evidence()
    record = replace(record, authoring={})
    observation["operations"] = 1
    coverage = prove._intent_complete_coverage(
        repo=ROOT,
        app_id="contract-counter",
        record=record,
        acceptance=acceptance,
        observation=observation,
    )
    assert coverage["status"] == "legacy-evidence"
    assert coverage["passed"] is True
    assert coverage["applicable"] is False
    assert coverage["coverageMode"] == "legacy-package-acceptance-and-browser-observation"
    assert coverage["declaredIntentCount"] is None
    assert coverage["coveredIntentCount"] is None
    assert coverage["declaredScenarioCount"] is None
    assert coverage["observedScenarioCount"] == 1


def test_legacy_intent_coverage_still_fails_closed() -> None:
    _catalog, record, _projection, _provenance, acceptance, observation = _evidence()
    record = replace(record, authoring={})
    observation["status"] = "fail"
    observation["ok"] = False
    with pytest.raises(prove.AppProofError, match="Legacy package acceptance and browser evidence did not converge"):
        prove._intent_complete_coverage(
            repo=ROOT,
            app_id="contract-counter",
            record=record,
            acceptance=acceptance,
            observation=observation,
        )

def _workbench_coverage_inputs():
    catalog = build_application_package_catalog(ROOT)
    record = next(item for item in catalog.packages if item.app_id == "contract-workbench")
    normalized = prove._load_json(ROOT / record.authoring["normalizedDefinition"], "normalized definition")
    scenario_ids = [entry["id"] for entry in normalized["definition"]["acceptance"]]
    acceptance = {
        "status": "pass",
        "passed": True,
        "results": [{
            "appId": "contract-workbench",
            "status": "pass",
            "testCount": 9,
            "enforceableContractCount": 1,
            "notDueContractCount": 0,
        }],
    }
    observation = {
        "status": "pass",
        "ok": True,
        "observation": {
            "scenarioResults": [{"id": scenario_id, "passed": True} for scenario_id in scenario_ids],
        },
    }
    return record, acceptance, observation


def test_intent_complete_coverage_converges_for_workbench() -> None:
    record, acceptance, observation = _workbench_coverage_inputs()
    coverage = prove._intent_complete_coverage(
        repo=ROOT,
        app_id="contract-workbench",
        record=record,
        acceptance=acceptance,
        observation=observation,
    )
    assert coverage["status"] == "pass"
    assert coverage["applicable"] is True
    assert coverage["coverageMode"] == "normalized-definition-intent-convergence"
    assert coverage["declaredIntentCount"] == 7
    assert coverage["coveredIntentCount"] == 7
    assert coverage["declaredScenarioCount"] == 14
    assert coverage["observedScenarioCount"] == 14
    assert coverage["crossCuttingChecks"]["clearAllObserved"] is True


def test_intent_complete_coverage_rejects_missing_clear_all_browser_proof() -> None:
    record, acceptance, observation = _workbench_coverage_inputs()
    observation["observation"]["scenarioResults"] = [
        entry for entry in observation["observation"]["scenarioResults"]
        if entry["id"] != "contract-workbench.acceptance.clear-all"
    ]
    with pytest.raises(prove.AppProofError, match="Intent-complete proof did not converge"):
        prove._intent_complete_coverage(
            repo=ROOT,
            app_id="contract-workbench",
            record=record,
            acceptance=acceptance,
            observation=observation,
        )
