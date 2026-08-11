from __future__ import annotations

from pathlib import Path

from main_computer.mcel_app_ir_native_proof import run_app_ir_native_intent_proof

from mcel_dsl_authoring_harness import (
    authoring_profile_for_case,
    package_record_for_case,
    require_profile_backed_surface_bundle_case,
    synthetic_host_bound_browser_parity_probe_for_case,
)


ROOT = Path(__file__).resolve().parents[1]


def test_generic_candidate_evidence_hooks_are_discovered_by_profile_capability() -> None:
    case = require_profile_backed_surface_bundle_case(ROOT)
    profile = authoring_profile_for_case(case)

    assert profile.app_id == case.app_id
    assert profile.authoring_frontend == "mcel.dsl.v1"
    assert profile.project_candidate is not None
    assert profile.run_candidate_evidence is not None
    assert profile.run_ir_native_proof is not None
    assert profile.run_browser_probe is not None
    assert profile.promotion_supported is True
    assert profile.promotion_rehearsal_supported is True


def test_generic_candidate_evidence_can_bind_ir_native_authority_without_reference_fixture() -> None:
    case = require_profile_backed_surface_bundle_case(ROOT)
    report = run_app_ir_native_intent_proof(
        app_id=case.app_id,
        repo=ROOT,
        record=package_record_for_case(case),
        acceptance={},
        observation={},
        browser_probe_runner=synthetic_host_bound_browser_parity_probe_for_case(case),
    )

    assert report["schema"] == "mcel.app-ir-native-intent-complete-proof.v1"
    assert report["authority"] == "mcel.app-ir-native-proof.v1"
    assert report["appId"] == case.app_id
    assert report["genericPipeline"] is True
    assert report["counterSpecificExecutionPathRequired"] is False
    assert report["legacyEvidenceRequired"] is False
    assert report["passed"] is True
    assert report["coveredIntentCount"] == report["declaredIntentCount"]
    assert report["coveredIntentCount"] > 0


def test_generic_candidate_evidence_rejects_failed_browser_parity() -> None:
    case = require_profile_backed_surface_bundle_case(ROOT)

    def failing_probe(_repo: Path, _headed: bool, _operation_prefix: str) -> dict:
        return {"schema": "mcel.synthetic-host-bound-browser-parity-probe.v1", "status": "fail", "valid": False}

    try:
        run_app_ir_native_intent_proof(
            app_id=case.app_id,
            repo=ROOT,
            record=package_record_for_case(case),
            acceptance={},
            observation={},
            browser_probe_runner=failing_probe,
        )
    except Exception as exc:
        assert "evidence did not pass" in str(exc) or "authority" in str(exc)
    else:
        raise AssertionError("failed browser parity was accepted")
