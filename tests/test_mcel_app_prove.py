from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from main_computer import mcel_app_prove as prove
from main_computer.mcel_application_packages import build_application_package_catalog
from main_computer.mcel_application_runtime_projection import build_runtime_projection_set
from main_computer.mcel_evidence_provenance import build_repository_provenance
from mcel_dsl_authoring_harness import require_profile_backed_surface_bundle_case


ROOT = Path(__file__).resolve().parents[1]


def _proof_case():
    case = require_profile_backed_surface_bundle_case(ROOT)
    catalog = build_application_package_catalog(ROOT)
    record = next(item for item in catalog.packages if item.app_id == case.app_id)
    projection = next(item for item in build_runtime_projection_set(ROOT).projections if item.app_id == case.app_id)
    provenance = build_repository_provenance(ROOT)
    return case.app_id, catalog, record, projection, provenance


def _evidence():
    app_id, catalog, record, projection, provenance = _proof_case()
    acceptance = {
        "schema": "mcel-acceptance-evidence-report-v1",
        "generatedAt": "2026-08-01T02:00:00Z",
        "status": "pass",
        "passed": True,
        "evidenceScope": {"kind": "app-scoped", "selectedApps": [app_id]},
        "repositoryProvenance": provenance,
        "applicationPackages": [{"appId": app_id, "packageFingerprint": record.fingerprint}],
        "results": [{
            "appId": app_id,
            "status": "pass",
            "passed": True,
            "testCount": 1,
            "enforceableContractCount": 1,
            "notDueContractCount": 0,
            "contracts": [{"contractId": f"{app_id}.acceptance.generic-proof", "status": "pass"}],
        }],
    }
    layers = {layer: "pass" for layer in prove.REQUIRED_SURFACE_LAYERS}
    surface_id = (
        ((case_bundle := require_profile_backed_surface_bundle_case(ROOT).surface_bundle) or {}).get("surfaceId")
        or f"{app_id}.surface.primary"
    )
    observation = {
        "schema": "mcel.application-operation-observation-report.v1",
        "generatedAt": "2026-08-01T02:01:00Z",
        "status": "pass",
        "ok": True,
        "evidenceScope": "app-scoped",
        "appId": app_id,
        "url": f"http://localhost/mcel-package-host.html?app={app_id}",
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
            "surfaceId": surface_id,
            "requiredLayerStatuses": layers,
        },
    }
    return app_id, catalog, record, projection, provenance, acceptance, observation


def _ir_native_coverage(app_id: str) -> dict[str, Any]:
    return {
        "schema": "mcel.ir-native-intent-complete-proof.v1",
        "appId": app_id,
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
    app_id, _catalog, _record, _projection, _provenance, acceptance, observation = _evidence()

    monkeypatch.setattr(prove, "_run_dependency", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        prove,
        "run_counter_ir_native_intent_proof",
        lambda **kwargs: _ir_native_coverage(str(kwargs.get("app_id") or app_id)),
    )

    def fake_load(path, _label):
        if path.name == "mcel.app.json":
            return {"authoring": {"status": "dsl-authoritative"}}
        return acceptance if "mcel-acceptance" in path.as_posix() else observation

    monkeypatch.setattr(prove, "_load_json", fake_load)
    monkeypatch.setattr(
        prove,
        "_artifact_reference",
        lambda path, _repo, payload: {
            "path": path.as_posix(),
            "sha256": "test",
            "schema": payload.get("schema"),
            "status": payload.get("status"),
        },
    )
    monkeypatch.setattr(
        prove,
        "_package_requirements_contract",
        lambda _repo, _record: (
            {
                "app": app_id,
                "contract_complete": True,
                "block_type_counts": {"mcel-intent": 3},
                "intent_count": 3,
                "mutation_intent_count": 1,
                "prohibited_intent_count": 0,
                "runtime_check_count": 1,
            },
            {"mcel-intent": 3, "mcel-runtime-check": 1},
        ),
    )
    monkeypatch.setattr(
        prove,
        "_truth_snapshot",
        lambda **_kwargs: {
            "overallStatus": "semantic-runtime-proven",
            "claims": {"semanticRuntimeProven": True},
        },
    )

    report = prove.run_app_proof(repo=ROOT, app_id=app_id)

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
    app_id, catalog, record, projection, provenance, acceptance, observation = _evidence()
    observation["package"]["fingerprint"] = "sha256:stale"

    with pytest.raises(prove.AppProofError, match="package fingerprint is stale"):
        prove._assert_evidence_alignment(
            app_id=app_id,
            record=record,
            catalog=catalog,
            projection=projection,
            provenance=provenance,
            acceptance=acceptance,
            observation=observation,
        )


def test_app_proof_rejects_missing_required_surface_layer() -> None:
    app_id, catalog, record, projection, provenance, acceptance, observation = _evidence()
    observation["surfaceConformance"]["requiredLayerStatuses"]["runtime-visual-fit"] = "unavailable"

    with pytest.raises(prove.AppProofError, match="runtime-visual-fit"):
        prove._assert_evidence_alignment(
            app_id=app_id,
            record=record,
            catalog=catalog,
            projection=projection,
            provenance=provenance,
            acceptance=acceptance,
            observation=observation,
        )


def test_legacy_intent_coverage_is_non_vacuous_evidence_status() -> None:
    app_id, _catalog, record, _projection, _provenance, acceptance, observation = _evidence()
    record = replace(record, authoring={})
    observation["operations"] = 1
    coverage = prove._intent_complete_coverage(
        repo=ROOT,
        app_id=app_id,
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
    app_id, _catalog, record, _projection, _provenance, acceptance, observation = _evidence()
    record = replace(record, authoring={})
    observation["status"] = "fail"
    observation["ok"] = False
    with pytest.raises(prove.AppProofError, match="Legacy package acceptance and browser evidence did not converge"):
        prove._intent_complete_coverage(
            repo=ROOT,
            app_id=app_id,
            record=record,
            acceptance=acceptance,
            observation=observation,
        )


def _normalized_coverage_inputs(tmp_path: Path):
    app_id, _catalog, record, _projection, _provenance = _proof_case()
    scenario_ids = [
        "sample.acceptance.create",
        "sample.acceptance.quote",
        "sample.acceptance.cancel",
        "sample.acceptance.prohibited",
        "sample.acceptance.filter-sort",
        "sample.acceptance.multi-instance",
        "sample.acceptance.clear-all",
    ]
    normalized = {
        "definitionFingerprint": "sha256:sample-definition",
        "definition": {
            "operations": {
                "create-item": {"operationKind": "mutation"},
                "quote-item": {"operationKind": "async"},
                "cancel-quote": {"operationKind": "cancel"},
                "delete-item": {"operationKind": "prohibited"},
            },
            "acceptance": [
                {"id": scenario_ids[0], "when": {"intentId": "create-item"}, "expect": {"operationStatus": "committed"}},
                {
                    "id": scenario_ids[1],
                    "when": {"intentId": "quote-item"},
                    "expect": {
                        "operationStatus": "committed",
                        "provisionalEventsVisibleBeforeCommit": True,
                        "olderOperationStatus": "superseded",
                        "independentItemKeys": True,
                    },
                },
                {
                    "id": scenario_ids[2],
                    "when": {"intentId": "cancel-quote"},
                    "expect": {
                        "operationStatus": "cancelled",
                        "canonicalStateUnchanged": True,
                        "provisionalStateClosed": True,
                    },
                },
                {
                    "id": scenario_ids[3],
                    "when": {"intentId": "delete-item"},
                    "expect": {"code": "INTENT_PROHIBITED", "canonicalStateUnchanged": True},
                },
                {"id": scenario_ids[4], "when": {"intentId": "create-item"}, "expect": {"operationStatus": "committed"}},
                {"id": scenario_ids[5], "when": {"intentId": "create-item"}, "expect": {"operationStatus": "committed"}},
                {"id": scenario_ids[6], "when": {"intentId": "create-item"}, "expect": {"operationStatus": "committed"}},
            ],
        },
    }
    normalized_path = tmp_path / "mcel.application.normalized.json"
    normalized_path.write_text(json.dumps(normalized), encoding="utf-8")
    manifest_path = tmp_path / "mcel.app.json"
    manifest_path.write_text('{"authoring":{"status":"legacy-explicit"}}\n', encoding="utf-8")
    record = replace(
        record,
        manifest=str(manifest_path),
        authoring={"normalizedDefinition": str(normalized_path)},
    )
    acceptance = {
        "status": "pass",
        "passed": True,
        "results": [{
            "appId": app_id,
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
    return app_id, record, acceptance, observation


def test_intent_complete_coverage_converges_for_normalized_definition(tmp_path: Path) -> None:
    app_id, record, acceptance, observation = _normalized_coverage_inputs(tmp_path)
    coverage = prove._intent_complete_coverage(
        repo=ROOT,
        app_id=app_id,
        record=record,
        acceptance=acceptance,
        observation=observation,
    )
    assert coverage["status"] == "pass"
    assert coverage["applicable"] is True
    assert coverage["coverageMode"] == "normalized-definition-intent-convergence"
    assert coverage["declaredIntentCount"] == 4
    assert coverage["coveredIntentCount"] == 4
    assert coverage["declaredScenarioCount"] == 7
    assert coverage["observedScenarioCount"] == 7
    assert coverage["crossCuttingChecks"]["clearAllObserved"] is True


def test_intent_complete_coverage_rejects_missing_clear_all_browser_proof(tmp_path: Path) -> None:
    app_id, record, acceptance, observation = _normalized_coverage_inputs(tmp_path)
    observation["observation"]["scenarioResults"] = [
        entry for entry in observation["observation"]["scenarioResults"]
        if not entry["id"].endswith(".clear-all")
    ]
    with pytest.raises(prove.AppProofError, match="Intent-complete proof did not converge"):
        prove._intent_complete_coverage(
            repo=ROOT,
            app_id=app_id,
            record=record,
            acceptance=acceptance,
            observation=observation,
        )
