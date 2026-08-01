from __future__ import annotations

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


def test_app_proof_composes_independent_authorities(monkeypatch) -> None:
    _catalog, _record, _projection, _provenance, acceptance, observation = _evidence()

    monkeypatch.setattr(prove, "_run_dependency", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        prove,
        "_load_json",
        lambda path, _label: acceptance if "mcel-acceptance" in path.as_posix() else observation,
    )
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
