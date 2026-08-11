from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from main_computer.mcel_app_ir_native_proof import (
    AppIrNativeProofError,
    run_app_ir_native_intent_proof,
)

from mcel_dsl_authoring_harness import (
    authoring_profile_for_case,
    package_record_for_case,
    require_profile_backed_surface_bundle_case,
    synthetic_host_bound_browser_parity_probe_for_case,
)


ROOT = Path(__file__).resolve().parents[1]


def test_generic_ir_native_proof_uses_profile_backed_authority_without_fixture_names() -> None:
    case = require_profile_backed_surface_bundle_case(ROOT)
    profile = authoring_profile_for_case(case)

    report = run_app_ir_native_intent_proof(
        app_id=case.app_id,
        repo=ROOT,
        record=package_record_for_case(case),
        acceptance={},
        observation={},
        browser_probe_runner=synthetic_host_bound_browser_parity_probe_for_case(case),
    )

    assert report["schema"] == "mcel.app-ir-native-intent-complete-proof.v1"
    assert report["applicationProfile"] == profile.profile_id
    assert report["projectionProfile"] == profile.projection_profile
    assert report["status"]
    assert report["passed"] is True
    assert report["genericPipeline"] is True
    assert report["legacyEvidenceRequired"] is False
    assert report["declaredIntentCount"] > 0
    assert report["declaredScenarioCount"] == report["declaredIntentCount"]


def test_generic_ir_native_proof_rejects_package_identity_mismatch() -> None:
    case = require_profile_backed_surface_bundle_case(ROOT)
    mismatched_record = replace(package_record_for_case(case), app_id="wrong-app")

    with pytest.raises(AppIrNativeProofError, match="identity|different application"):
        run_app_ir_native_intent_proof(
            app_id=case.app_id,
            repo=ROOT,
            record=mismatched_record,
            acceptance={},
            observation={},
            browser_probe_runner=synthetic_host_bound_browser_parity_probe_for_case(case),
        )


def test_generic_ir_native_proof_rejects_incomplete_runtime_binding_checks() -> None:
    case = require_profile_backed_surface_bundle_case(ROOT)
    base_probe = synthetic_host_bound_browser_parity_probe_for_case(case)

    def incomplete_probe(repo: Path, headed: bool, operation_prefix: str) -> dict:
        report = base_probe(repo, headed, operation_prefix)
        first = next(iter(report["runtimeBindingChecks"]))
        report["runtimeBindingChecks"][first] = False
        return report

    with pytest.raises(AppIrNativeProofError, match="IR proof did not converge|runtime"):
        run_app_ir_native_intent_proof(
            app_id=case.app_id,
            repo=ROOT,
            record=package_record_for_case(case),
            acceptance={},
            observation={},
            browser_probe_runner=incomplete_probe,
        )
