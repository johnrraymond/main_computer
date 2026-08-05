from __future__ import annotations

import json
from pathlib import Path

from main_computer.mcel_app_compile import compile_application
from main_computer.mcel_app_project import project_application
from main_computer.mcel_app_promote import inspect_application_authority
from main_computer.mcel_app_ir_native_proof import run_app_ir_native_intent_proof
from main_computer.mcel_application_packages import build_application_package_catalog
from main_computer.mcel_application_runtime_projection import build_runtime_projection_set
from main_computer.mcel_evidence_provenance import build_repository_provenance

ROOT = Path(__file__).resolve().parents[1]
SEMANTIC = "sha256:a9dbe6b7ec49978d313f18836b30c3394539c18f29430c3a7553837bc46eb0ef"


def _record():
    return next(x for x in build_application_package_catalog(ROOT).packages if x.app_id == "contract-counter")


def _acceptance() -> dict:
    record = _record()
    provenance = build_repository_provenance(ROOT)
    return {
        "status": "pass", "passed": True, "generatedAt": "2026-08-04T19:20:00Z",
        "evidenceScope": {"kind": "app-scoped", "selectedApps": ["contract-counter"]},
        "repositoryProvenance": provenance,
        "applicationPackages": [{"appId": "contract-counter", "packageFingerprint": record.fingerprint}],
        "results": [{"appId": "contract-counter", "status": "pass", "testCount": 1}],
    }


def _observation() -> dict:
    catalog = build_application_package_catalog(ROOT)
    record = _record()
    projection = next(x for x in build_runtime_projection_set(ROOT).projections if x.app_id == "contract-counter")
    provenance = build_repository_provenance(ROOT)
    return {
        "status": "pass", "ok": True, "generatedAt": "2026-08-04T19:21:00Z",
        "evidenceScope": "app-scoped", "appId": "contract-counter",
        "package": {"fingerprint": record.fingerprint}, "catalogFingerprint": catalog.fingerprint,
        "repositoryProvenance": provenance,
        "observation": {
            "runtimeProjectionFingerprint": projection.fingerprint,
            "repositoryFingerprint": provenance["fingerprint"],
            "comparison": {"stateMatches": True, "receiptMatches": True, "surfaceMatches": True},
        },
        "surfaceConformance": {"status": "pass", "valid": True},
    }


def _receipt(ok: bool, status: str, code: str) -> dict:
    return {"ok": ok, "status": status, "code": code}


def _node(_repo: Path, prefix: str) -> dict:
    return {"schema": "mcel.counter-effect-probe.v1", "operations": [
        {"operationId": f"{prefix}-increment", "before": {"count": 0, "revision": 0}, "result": _receipt(True,"committed","APPLICATION_OPERATION_COMMITTED"), "after": {"count": 1, "revision": 1}},
        {"operationId": f"{prefix}-stale", "before": {"count": 1, "revision": 1}, "result": _receipt(False,"refused","SCM_STALE_REVISION"), "after": {"count": 1, "revision": 1}},
        {"operationId": f"{prefix}-direct-set", "before": {"count": 1, "revision": 1}, "result": _receipt(False,"refused","INTENT_PROHIBITED"), "after": {"count": 1, "revision": 1}},
        {"operationId": f"{prefix}-reset", "before": {"count": 1, "revision": 1}, "result": _receipt(True,"committed","APPLICATION_OPERATION_COMMITTED"), "after": {"count": 0, "revision": 2}},
    ]}


def _browser(_repo: Path, _headed: bool, prefix: str) -> dict:
    observed={"status":"pass","comparison":{"surfaceMatches":True}}
    return {"schema":"mcel.counter-browser-effect-probe.v1","operations":[
        {"operationId":f"{prefix}-browser-increment","before":{"count":0,"revision":0},"result":_receipt(True,"committed","APPLICATION_OPERATION_COMMITTED"),"after":{"count":1,"revision":1},"visible":"1","observation":observed},
        {"operationId":f"{prefix}-browser-stale","before":{"count":1,"revision":1},"result":_receipt(False,"refused","SCM_STALE_REVISION"),"after":{"count":1,"revision":1},"visible":"1"},
        {"operationId":f"{prefix}-browser-direct-set","before":{"count":1,"revision":1},"result":_receipt(False,"refused","INTENT_PROHIBITED"),"after":{"count":1,"revision":1},"visible":"1"},
        {"operationId":f"{prefix}-browser-reset","before":{"count":1,"revision":1},"result":_receipt(True,"committed","APPLICATION_OPERATION_COMMITTED"),"after":{"count":0,"revision":2},"visible":"0","observation":observed},
    ]}


def test_generic_compile_uses_live_authoritative_source() -> None:
    result = compile_application(app_id="contract-counter", repo_root=ROOT)
    payload = result.to_dict()
    assert result.valid
    assert payload["genericPipeline"] is True
    assert payload["counterSpecificExecutionPathRequired"] is False
    assert payload["semanticFingerprint"] == SEMANTIC
    assert payload["source"] == "mcel_apps/contract-counter/application.js"


def test_generic_projection_uses_registered_application_profile() -> None:
    result = project_application(app_id="contract-counter", repo_root=ROOT)
    payload = result.to_dict()
    assert result.valid
    assert payload["projectionProfile"] == "mcel.counter.explicit-projection.v1"
    assert payload["counterSpecificExecutionPathRequired"] is False
    assert payload["semanticFingerprint"] == SEMANTIC


def test_generic_promotion_inspection_reports_promoted_authority() -> None:
    result = inspect_application_authority(app_id="contract-counter", repo_root=ROOT)
    payload = result.to_dict()
    assert result.valid
    assert payload["status"] == "promoted"
    assert payload["sourceAuthority"] == "mcel.dsl.v1"
    assert payload["counterSpecificExecutionPathRequired"] is False


def test_generic_ir_native_authority_owns_counter_proof_result() -> None:
    report = run_app_ir_native_intent_proof(
        app_id="contract-counter", repo=ROOT, record=_record(),
        acceptance=_acceptance(), observation=_observation(),
        node_probe_runner=_node, browser_probe_runner=_browser,
    )
    assert report["schema"] == "mcel.app-ir-native-intent-complete-proof.v1"
    assert report["authority"] == "mcel.app-ir-native-proof.v1"
    assert report["genericPipeline"] is True
    assert report["counterSpecificExecutionPathRequired"] is False
    assert report["status"] == "ir-native"
    assert report["legacyEvidenceRequired"] is False
    assert report["semanticFingerprint"] == SEMANTIC
