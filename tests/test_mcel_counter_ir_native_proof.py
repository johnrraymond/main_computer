from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest

from main_computer.mcel_application_packages import build_application_package_catalog
from main_computer.mcel_application_runtime_projection import build_runtime_projection_set
from main_computer.mcel_evidence_provenance import build_repository_provenance
from main_computer.mcel_counter_ir_native_proof import (
    CounterIrNativeProofError,
    _verify_generated_ownership,
    run_counter_ir_native_intent_proof,
)
from main_computer.mcel_counter_reference_fixture_profile import build_counter_ir_native_proof_profile
from main_computer.mcel_explicit_package_ir_native_proof import ExplicitPackageIrNativeProofProfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "mcel_apps/contract-counter"


def _record():
    catalog = build_application_package_catalog(ROOT)
    return next(item for item in catalog.packages if item.app_id == "contract-counter")


def _acceptance() -> dict:
    catalog = build_application_package_catalog(ROOT)
    record = next(item for item in catalog.packages if item.app_id == "contract-counter")
    provenance = build_repository_provenance(ROOT)
    return {
        "status": "pass",
        "passed": True,
        "generatedAt": "2026-08-04T19:20:00Z",
        "evidenceScope": {"kind": "app-scoped", "selectedApps": ["contract-counter"]},
        "repositoryProvenance": provenance,
        "applicationPackages": [{"appId": "contract-counter", "packageFingerprint": record.fingerprint}],
        "results": [{"appId": "contract-counter", "status": "pass", "testCount": 1}],
    }


def _observation() -> dict:
    catalog = build_application_package_catalog(ROOT)
    record = next(item for item in catalog.packages if item.app_id == "contract-counter")
    projection = next(item for item in build_runtime_projection_set(ROOT).projections if item.app_id == "contract-counter")
    provenance = build_repository_provenance(ROOT)
    return {
        "status": "pass",
        "ok": True,
        "generatedAt": "2026-08-04T19:21:00Z",
        "evidenceScope": "app-scoped",
        "appId": "contract-counter",
        "package": {"fingerprint": record.fingerprint},
        "catalogFingerprint": catalog.fingerprint,
        "repositoryProvenance": provenance,
        "observation": {
            "runtimeProjectionFingerprint": projection.fingerprint,
            "repositoryFingerprint": provenance["fingerprint"],
            "comparison": {"stateMatches": True, "receiptMatches": True, "surfaceMatches": True},
        },
        "surfaceConformance": {"status": "pass", "valid": True},
    }


def _receipt(*, ok: bool, status: str, code: str) -> dict:
    return {"ok": ok, "status": status, "code": code}


def _node_probe(_repo: Path, prefix: str) -> dict:
    return {
        "schema": "mcel.counter-effect-probe.v1",
        "operations": [
            {"operationId": f"{prefix}-increment", "before": {"count": 0, "revision": 0}, "result": _receipt(ok=True, status="committed", code="APPLICATION_OPERATION_COMMITTED"), "after": {"count": 1, "revision": 1}},
            {"operationId": f"{prefix}-stale", "before": {"count": 1, "revision": 1}, "result": _receipt(ok=False, status="refused", code="SCM_STALE_REVISION"), "after": {"count": 1, "revision": 1}},
            {"operationId": f"{prefix}-direct-set", "before": {"count": 1, "revision": 1}, "result": _receipt(ok=False, status="refused", code="INTENT_PROHIBITED"), "after": {"count": 1, "revision": 1}},
            {"operationId": f"{prefix}-reset", "before": {"count": 1, "revision": 1}, "result": _receipt(ok=True, status="committed", code="APPLICATION_OPERATION_COMMITTED"), "after": {"count": 0, "revision": 2}},
        ],
    }


def _browser_probe(_repo: Path, _headed: bool, prefix: str) -> dict:
    observed = {"status": "pass", "comparison": {"surfaceMatches": True}}
    return {
        "schema": "mcel.counter-browser-effect-probe.v1",
        "operations": [
            {"operationId": f"{prefix}-browser-increment", "before": {"count": 0, "revision": 0}, "result": _receipt(ok=True, status="committed", code="APPLICATION_OPERATION_COMMITTED"), "after": {"count": 1, "revision": 1}, "visible": "1", "observation": observed},
            {"operationId": f"{prefix}-browser-stale", "before": {"count": 1, "revision": 1}, "result": _receipt(ok=False, status="refused", code="SCM_STALE_REVISION"), "after": {"count": 1, "revision": 1}, "visible": "1"},
            {"operationId": f"{prefix}-browser-direct-set", "before": {"count": 1, "revision": 1}, "result": _receipt(ok=False, status="refused", code="INTENT_PROHIBITED"), "after": {"count": 1, "revision": 1}, "visible": "1"},
            {"operationId": f"{prefix}-browser-reset", "before": {"count": 1, "revision": 1}, "result": _receipt(ok=True, status="committed", code="APPLICATION_OPERATION_COMMITTED"), "after": {"count": 0, "revision": 2}, "visible": "0", "observation": observed},
        ],
    }


def test_counter_ir_native_proof_uses_generic_explicit_package_profile() -> None:
    profile = build_counter_ir_native_proof_profile(
        run_node_probe=_node_probe,
        run_browser_probe=_browser_probe,
        build_effect_accounting=lambda **_kwargs: {"status": "closed", "valid": True},
    )

    assert isinstance(profile, ExplicitPackageIrNativeProofProfile)
    assert profile.app_id == "contract-counter"
    assert profile.generated_file_generator == "mcel.counter.explicit-projection.v1"
    assert profile.report_schema == "mcel.ir-native-intent-complete-proof.v1"
    assert profile.runtime_code_for("REVISION_STALE") == "SCM_STALE_REVISION"


def test_promoted_counter_earns_ir_native_intent_complete_proof() -> None:
    report = run_counter_ir_native_intent_proof(
        repo=ROOT,
        record=_record(),
        acceptance=_acceptance(),
        observation=_observation(),
        node_probe_runner=_node_probe,
        browser_probe_runner=_browser_probe,
    )

    assert report["status"] == "ir-native"
    assert report["passed"] is True
    assert report["legacyEvidenceRequired"] is False
    assert report["declaredIntentCount"] == 3
    assert report["coveredIntentCount"] == 3
    assert report["declaredScenarioCount"] == 4
    assert report["observedScenarioCount"] == 4
    assert report["generatedOwnership"]["exact"] is True
    assert report["generatedOwnership"]["generatedFileCount"] == 7
    assert report["effectAccounting"]["status"] == "closed"
    assert all(item["passed"] is True for item in report["intents"].values())
    assert all(item["passed"] is True for item in report["scenarios"].values())


def test_generated_ownership_rejects_derived_contract_drift(tmp_path: Path) -> None:
    record = _record()
    files = dict(record.files)
    files["contracts/domain.js"] = b"// drift\n"
    drifted_record = replace(record, files=files)
    ownership = json.loads(record.files["mcel.generated.json"].decode("utf-8"))

    with pytest.raises(CounterIrNativeProofError, match="ownership drift"):
        _verify_generated_ownership(
            package_root=tmp_path / "contract-counter",
            record=drifted_record,
            ownership=ownership,
            semantic_fingerprint=ownership["sourceAuthority"]["semanticFingerprint"],
        )


def test_generated_ownership_rejects_wrong_semantic_binding() -> None:
    record = _record()
    ownership = json.loads(record.files["mcel.generated.json"].decode("utf-8"))
    ownership["sourceAuthority"]["semanticFingerprint"] = "sha256:wrong"

    with pytest.raises(CounterIrNativeProofError, match="authoritative DSL semantics"):
        _verify_generated_ownership(
            package_root=PACKAGE,
            record=record,
            ownership=ownership,
            semantic_fingerprint="sha256:expected",
        )


def test_ir_native_proof_fails_when_prohibited_intent_mutates_browser_state() -> None:
    def bad_browser(repo: Path, headed: bool, prefix: str) -> dict:
        report = copy.deepcopy(_browser_probe(repo, headed, prefix))
        direct = next(item for item in report["operations"] if item["operationId"] == f"{prefix}-browser-direct-set")
        direct["after"] = {"count": 99, "revision": 2}
        direct["visible"] = "99"
        return report

    with pytest.raises(CounterIrNativeProofError, match="effect accounting did not close"):
        run_counter_ir_native_intent_proof(
            repo=ROOT,
            record=_record(),
            acceptance=_acceptance(),
            observation=_observation(),
            node_probe_runner=_node_probe,
            browser_probe_runner=bad_browser,
        )


def test_ir_native_proof_requires_passing_browser_observation() -> None:
    observation = _observation()
    observation["status"] = "fail"
    observation["ok"] = False

    with pytest.raises(CounterIrNativeProofError, match="Browser observation is not exactly bound"):
        run_counter_ir_native_intent_proof(
            repo=ROOT,
            record=_record(),
            acceptance=_acceptance(),
            observation=observation,
            node_probe_runner=_node_probe,
            browser_probe_runner=_browser_probe,
        )
