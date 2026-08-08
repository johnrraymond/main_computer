"""Contract Workbench MCEL reference-fixture profile.

Workbench is intentionally retained as the richer profiled-package conformance
fixture.  It exercises portable IR projection, constrained expressions,
capability/effect accounting, multi-scenario browser proof, and promotion
authority.  This module centralizes Workbench-specific facts consumed by the
generic profiled-package MCEL tooling.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from main_computer.mcel_application_definition_ir import (
    definition_to_application_ir,
    import_application_definition,
)
from main_computer.mcel_application_ir import validate_application_ir
from main_computer.mcel_dsl_compiler import DEFAULT_CANDIDATE_ROOT
from main_computer.mcel_workbench_expression_profile import count_native_calls, count_opaque_callbacks
from main_computer.mcel_profiled_package_candidate_evidence import ProfiledPackageCandidateEvidenceProfile
from main_computer.mcel_profiled_package_candidate_projection import ProfiledPackageProjectionProfile
from main_computer.mcel_profiled_package_ir_native_proof import ProfiledPackageIrNativeProofProfile
from main_computer.mcel_profiled_package_promotion_rehearsal import ProfiledPackagePromotionRehearsalProfile
from main_computer.mcel_profiled_package_promotion import ProfiledPackagePromotionProfile
from main_computer.mcel_projection_profiles.contract_workbench_v1 import (
    GENERATED_PATHS,
    PROFILE_ID,
    project_workbench_ir,
)


APP_ID = "contract-workbench"
FIXTURE_ROLE = "mcel.reference-fixture.profiled-package.workbench.v1"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DSL_SOURCE = Path("mcel_apps/contract-workbench/application.js")
DEFAULT_FIXTURE_IR = Path("tests/fixtures/mcel_application_ir/contract-workbench.ir.json")
DEFAULT_PACKAGE_ROOT = Path("mcel_apps/contract-workbench")
PROFILE_MODULE_PATH = Path("main_computer/mcel_projection_profiles/contract_workbench_v1.py")
PROJECTION_PROFILE = PROFILE_ID

PROJECTION_REPORT_SCHEMA = "mcel.workbench-candidate-projection-report.v1"
PROJECTION_VERSION = "mcel-workbench-candidate-projection-wave11"
EVIDENCE_REPORT_SCHEMA = "mcel.workbench-candidate-evidence-report.v1"
EVIDENCE_VERSION = "mcel-workbench-candidate-evidence-wave11"
DEFAULT_REPORT_ROOT = Path("runtime/reports/mcel-compiler-candidates")

EXPECTED_INTENT_COUNT = 7
EXPECTED_SCENARIO_COUNT = 14
EXPECTED_EFFECT_COUNT = 18
EXPECTED_OBSERVATION_COVERAGE_COUNT = 7
EXPECTED_CAPABILITY_COUNT = 1
EXPECTED_CAPABILITY_INTENT_COUNT = 2


def build_workbench_projection_profile() -> ProfiledPackageProjectionProfile:
    """Build the Workbench profile for generic profiled-package projection."""

    return ProfiledPackageProjectionProfile(
        app_id=APP_ID,
        projection_profile=PROJECTION_PROFILE,
        project_ir=_project_ir,
        import_live_ir=import_application_definition,
        import_candidate_ir=_import_shadow_definition,
        generated_paths=tuple(GENERATED_PATHS),
        default_dsl_source=DEFAULT_DSL_SOURCE,
        default_fixture_ir=DEFAULT_FIXTURE_IR,
        default_live_package_root=DEFAULT_PACKAGE_ROOT,
        default_candidate_root=DEFAULT_CANDIDATE_ROOT,
        profile_module_path=PROFILE_MODULE_PATH,
        report_schema=PROJECTION_REPORT_SCHEMA,
        version=PROJECTION_VERSION,
        source_conflict_code="MCEL_WORKBENCH_PROJECTION_SOURCE_CONFLICT",
        projection_profile_invalid_code="MCEL_WORKBENCH_PROJECTION_PROFILE_INVALID",
        invalid_live_package_code="MCEL_WORKBENCH_LIVE_PACKAGE_INVALID",
        file_conflict_code="MCEL_WORKBENCH_PROJECTION_FILE_CONFLICT",
        drift_code="MCEL_WORKBENCH_CANDIDATE_GENERATED_DRIFT",
        roundtrip_conflict_code="MCEL_WORKBENCH_CANDIDATE_ROUNDTRIP_CONFLICT",
        source_conflict_summary="Live Workbench definition and DSL candidate are not semantically exact.",
        drift_summary="Existing Workbench candidate projections contain manual drift.",
        roundtrip_conflict_summary=(
            "Generated Workbench package does not import back to the candidate semantics."
        ),
        top_level_flags={"counterSpecificExecutionPathRequired": False},
        limitations={
            "portableIrProjectionComplete": True,
            "normalizedDefinitionProjectionRequired": False,
            "opaqueCallbacksRemain": False,
        },
        source_metrics=_source_metrics,
    )



def build_workbench_candidate_evidence_profile() -> ProfiledPackageCandidateEvidenceProfile:
    """Build the Workbench profile for generic profiled-package evidence."""

    from main_computer.mcel_workbench_candidate_projection import project_workbench_candidate

    return ProfiledPackageCandidateEvidenceProfile(
        app_id=APP_ID,
        project_candidate=project_workbench_candidate,
        default_dsl_source=DEFAULT_DSL_SOURCE,
        default_fixture_ir=DEFAULT_FIXTURE_IR,
        default_package_root=DEFAULT_PACKAGE_ROOT,
        default_candidate_root=DEFAULT_CANDIDATE_ROOT,
        default_report_root=DEFAULT_REPORT_ROOT,
        report_schema=EVIDENCE_REPORT_SCHEMA,
        report_version=EVIDENCE_VERSION,
        report_title="Contract Workbench Candidate Evidence",
        expected_intent_count=EXPECTED_INTENT_COUNT,
        expected_scenario_count=EXPECTED_SCENARIO_COUNT,
        evidence_failed_code="MCEL_WORKBENCH_CANDIDATE_EVIDENCE_FAILED",
        source_invalid_code="MCEL_WORKBENCH_CANDIDATE_EVIDENCE_SOURCE_INVALID",
        stage_failed_code="MCEL_WORKBENCH_CANDIDATE_STAGE_FAILED",
        live_changed_code="MCEL_WORKBENCH_LIVE_PACKAGE_CHANGED",
        live_changed_summary=(
            "The live Workbench package changed during isolated candidate proof."
        ),
        migration_debt=_workbench_migration_debt,
    )


def build_workbench_ir_native_proof_profile() -> ProfiledPackageIrNativeProofProfile:
    """Build the Workbench profile for generic profiled-package IR-native proof."""

    return ProfiledPackageIrNativeProofProfile(
        app_id=APP_ID,
        default_dsl_source=DEFAULT_DSL_SOURCE,
        default_fixture_ir=DEFAULT_FIXTURE_IR,
        generated_paths=tuple(GENERATED_PATHS),
        projection_profile=PROJECTION_PROFILE,
        report_schema="mcel.workbench-ir-native-intent-complete-proof.v1",
        effect_accounting_schema="mcel.workbench-ir-native-effect-accounting.v1",
        capability_accounting_schema="mcel.workbench-ir-native-capability-accounting.v1",
        expected_intent_count=EXPECTED_INTENT_COUNT,
        expected_scenario_count=EXPECTED_SCENARIO_COUNT,
        expected_effect_count=EXPECTED_EFFECT_COUNT,
        expected_observation_coverage_count=EXPECTED_OBSERVATION_COVERAGE_COUNT,
        expected_capability_count=EXPECTED_CAPABILITY_COUNT,
        expected_capability_intent_count=EXPECTED_CAPABILITY_INTENT_COUNT,
        not_dsl_authoritative_message="Workbench package is not DSL-authoritative.",
        compile_conflict_message=(
            "Authoritative Workbench DSL does not compile to the canonical IR exactly."
        ),
        acceptance_failed_message="Workbench acceptance evidence did not pass.",
        observation_failed_message="Workbench browser observation did not pass.",
        observation_coverage_failed_message=(
            "Workbench browser observation coverage is not 7/7 passing."
        ),
        proof_failed_message=(
            "Workbench IR-native intent/effect/capability proof did not converge."
        ),
    )


def build_workbench_promotion_rehearsal_profile() -> ProfiledPackagePromotionRehearsalProfile:
    """Build the Workbench profile for generic profiled-package promotion rehearsal."""

    from main_computer.mcel_workbench_candidate_evidence import run_workbench_candidate_evidence
    from main_computer.mcel_workbench_candidate_projection import project_workbench_candidate

    return ProfiledPackagePromotionRehearsalProfile(
        app_id=APP_ID,
        project_candidate=project_workbench_candidate,
        run_candidate_evidence=run_workbench_candidate_evidence,
        generated_paths=tuple(GENERATED_PATHS),
        projection_profile=PROJECTION_PROFILE,
        default_dsl_source=DEFAULT_DSL_SOURCE,
        default_fixture_ir=DEFAULT_FIXTURE_IR,
        default_package_root=DEFAULT_PACKAGE_ROOT,
        default_candidate_root=DEFAULT_CANDIDATE_ROOT,
        default_evidence_report_root=DEFAULT_REPORT_ROOT,
        default_report_root=DEFAULT_REPORT_ROOT,
        report_schema="mcel.app-promotion-rehearsal-report.v1",
        report_version="mcel-app-promotion-rehearsal-wave12",
        report_filename="mcel-app-promotion-rehearsal-report.json",
        report_markdown_filename="mcel-app-promotion-rehearsal-report.md",
        report_title="Contract Workbench Promotion Rehearsal",
        live_authority="legacy-explicit-package",
        rehearsed_authority="mcel.dsl.v1",
        generated_authority=PROJECTION_PROFILE,
        expected_intent_count=EXPECTED_INTENT_COUNT,
        expected_scenario_count=EXPECTED_SCENARIO_COUNT,
        expected_effect_count=EXPECTED_EFFECT_COUNT,
        expected_capability_count=EXPECTED_CAPABILITY_COUNT,
        source_invalid_code="MCEL_WORKBENCH_PROMOTION_REHEARSAL_SOURCE_INVALID",
        evidence_invalid_code="MCEL_WORKBENCH_PROMOTION_EVIDENCE_INVALID",
        evidence_binding_conflict_code="MCEL_WORKBENCH_PROMOTION_EVIDENCE_BINDING_CONFLICT",
        rehearsal_failed_code="MCEL_WORKBENCH_PROMOTION_REHEARSAL_FAILED",
        mutated_live_repo_code="MCEL_WORKBENCH_PROMOTION_REHEARSAL_MUTATED_LIVE_REPOSITORY",
    )



def build_workbench_promotion_profile() -> ProfiledPackagePromotionProfile:
    """Build the Workbench profile for generic profiled-package promotion execution."""

    from main_computer.mcel_workbench_promotion_rehearsal import rehearse_workbench_promotion

    return ProfiledPackagePromotionProfile(
        app_id=APP_ID,
        rehearsal_profile=build_workbench_promotion_rehearsal_profile(),
        run_rehearsal=rehearse_workbench_promotion,
        default_package_root=DEFAULT_PACKAGE_ROOT,
        default_fixture_ir=DEFAULT_FIXTURE_IR,
        projection_profile=PROJECTION_PROFILE,
        default_transaction_root=Path("runtime/state/mcel/application-promotions/contract-workbench"),
        default_report_root=Path("runtime/reports/mcel-application-promotions/contract-workbench"),
        default_rehearsal_report_root=DEFAULT_REPORT_ROOT,
        default_lock_path=Path("runtime/state/mcel/application-promotion.contract-workbench.lock"),
        report_schema="mcel.application-promotion-execution-report.v1",
        report_version="mcel-workbench-promotion-wave13",
        transaction_schema="mcel.application-promotion-transaction.v1",
        rollback_schema="mcel.application-promotion-rollback-result.v1",
        report_filename="mcel-workbench-promotion-report",
        rollback_report_filename="mcel-workbench-promotion-rollback-report",
        report_title="Contract Workbench Authority Promotion",
        rollback_report_title="Contract Workbench Authority Rollback",
        live_authority_before="legacy-explicit-package",
        target_source_authority="mcel.dsl.v1",
        derived_artifact_authority=PROJECTION_PROFILE,
        legacy_package_authority="retired",
        truth_status="semantic-runtime-proven",
        promoted_idempotent_status="already-promoted",
        promotion_failed_code="MCEL_WORKBENCH_PROMOTION_FAILED",
        rollback_failed_code="MCEL_WORKBENCH_PROMOTION_ROLLBACK_FAILED",
        lock_schema="mcel.application-promotion-lock.v1",
        lock_held_message="Another Workbench promotion operation holds the lock",
        already_promoted_summary=(
            "Contract Workbench already declares mcel.dsl.v1 authority; no second promotion was executed."
        ),
        unsupported_authority_message="Unsupported Workbench source authority before promotion",
        invalid_plan_schema_message="Fresh rehearsal returned an unknown promotion-plan schema.",
        invalid_plan_app_message="Fresh rehearsal returned a plan for another application.",
        invalid_plan_start_message=(
            "Workbench promotion plan does not begin at legacy explicit-package authority."
        ),
        invalid_plan_end_message="Workbench promotion plan does not establish mcel.dsl.v1 authority.",
        invalid_plan_generated_authority_message=(
            "Workbench promotion plan does not establish the portable IR projection authority."
        ),
        invalid_plan_evidence_message=(
            "Workbench promotion plan is not bound to fresh semantic-runtime-proven candidate evidence."
        ),
        empty_plan_message="Workbench promotion plan contains no file transitions.",
        promoted_dsl_failed_message="Promoted authoritative Workbench DSL failed exact compilation.",
        authority_failed_message="Post-promotion Workbench authority checks failed",
        automatic_rollback_failed_message=(
            "Automatic rollback did not restore the protected Workbench source boundary exactly."
        ),
        committed_only_rollback_message=(
            "Only a committed Workbench promotion transaction can be rolled back."
        ),
        rollback_drift_message="Protected MCEL source drift blocks Workbench rollback",
        rollback_not_exact_message="Workbench rollback restoration is not exact",
        rollback_package_message="Workbench package rollback restoration is not exact",
        rollback_fingerprint_message="Workbench rollback fingerprint restoration is not exact",
        no_transaction_message="No committed Workbench promotion transaction exists.",
        invalid_transaction_message="Invalid Workbench promotion transaction identifier.",
        transaction_not_found_message="Workbench promotion transaction not found",
    )


def _project_ir(application_ir: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, bytes]]:
    projection = project_workbench_ir(application_ir)
    return projection.profile, dict(projection.files)


def _source_metrics(application_ir: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        "nativeDomainCallCount": count_native_calls(application_ir),
        "opaqueCallbackDebt": count_opaque_callbacks(application_ir),
    }


def _workbench_migration_debt(projection_report: Mapping[str, Any]) -> Mapping[str, Any]:
    source = projection_report.get("source") or {}
    return {
        "opaqueCallbacks": int(source.get("opaqueCallbackDebt") or 0),
        "nativeDomainCalls": int(source.get("nativeDomainCallCount") or 0),
        "portableIrProjectionComplete": True,
        "normalizedDefinitionProjectionRequired": False,
    }


def _import_shadow_definition(
    package_root: Path,
    repo: Path,
    live_ir: Mapping[str, Any],
) -> tuple[Mapping[str, Any] | None, list[Mapping[str, Any]]]:
    diagnostics: list[Mapping[str, Any]] = []
    normalized_path = package_root / "generated/mcel.application.normalized.json"
    try:
        document = json.loads(normalized_path.read_text(encoding="utf-8"))
        definition = document["definition"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        diagnostics.append(
            _diagnostic(
                "MCEL_WORKBENCH_SHADOW_DEFINITION_UNREADABLE",
                f"Could not read shadow normalized definition: {exc}",
                "$roundtrip",
            )
        )
        return None, diagnostics

    source = {
        "kind": "application-definition-source-binding",
        "frontend": "mcel.application-definition.v1",
        "file": "mcel_apps/contract-workbench/application.js",
        "start": {"line": 1, "column": 1},
        "end": {"line": 1, "column": 1},
    }
    definition_fingerprint = str((live_ir.get("migration") or {}).get("definitionFingerprint") or "")
    candidate = definition_to_application_ir(
        definition,
        app_id=APP_ID,
        source=source,
        source_files=(),
        definition_fingerprint=definition_fingerprint,
        normalized_reference="mcel_apps/contract-workbench/generated/mcel.application.normalized.json",
    )
    validation = validate_application_ir(candidate)
    diagnostics.extend(item.to_dict() for item in validation.diagnostics)
    return validation.normalized if validation.valid else None, diagnostics



def _diagnostic(
    code: str,
    summary: str,
    semantic_path: str,
    *,
    observed: Any = None,
) -> dict[str, Any]:
    return {
        "schema": "mcel.compiler-diagnostic.v1",
        "code": code,
        "severity": "error",
        "blocking": True,
        "summary": summary,
        "problem": summary,
        "semanticPath": semantic_path,
        "observed": observed,
    }
