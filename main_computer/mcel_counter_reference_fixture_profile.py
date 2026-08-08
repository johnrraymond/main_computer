"""Contract Counter MCEL reference-fixture profile.

Counter is intentionally retained as the small explicit-package conformance
fixture.  This module centralizes the Counter-specific facts consumed by the
generic explicit-package MCEL projection and evidence tools.  Wrapper modules
keep their historical entry-point names; this profile is the single source for
Counter identifiers, paths, generated-contract inventory, report labels, and
fixture diagnostics.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from main_computer.mcel_counter_legacy_importer import DEFAULT_COUNTER_ROOT, import_counter_legacy_package
from main_computer.mcel_dsl_compiler import DEFAULT_CANDIDATE_ROOT
from main_computer.mcel_explicit_package_compatibility import ExplicitPackageCompatibilityProfile
from main_computer.mcel_explicit_package_candidate_evidence import (
    DEFAULT_REPORT_ROOT as DEFAULT_EVIDENCE_REPORT_ROOT,
    ExplicitPackageCandidateEvidenceProfile,
)
from main_computer.mcel_explicit_package_candidate_projection import ExplicitPackageProjectionProfile
from main_computer.mcel_explicit_package_ir_native_proof import ExplicitPackageIrNativeProofProfile
from main_computer.mcel_explicit_package_promotion import (
    DEFAULT_LOCK_PATH as DEFAULT_EXECUTION_LOCK_PATH,
    DEFAULT_REPORT_ROOT as DEFAULT_EXECUTION_REPORT_ROOT,
    DEFAULT_TRANSACTION_ROOT as DEFAULT_EXECUTION_TRANSACTION_ROOT,
    ExplicitPackagePromotionProfile,
)
from main_computer.mcel_explicit_package_promotion_rehearsal import (
    DEFAULT_REPORT_ROOT as DEFAULT_PROMOTION_REPORT_ROOT,
    ExplicitPackagePromotionRehearsalProfile,
)


APP_ID = "contract-counter"
FIXTURE_ROLE = "mcel.reference-fixture.explicit-package.counter.v1"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_IR = REPOSITORY_ROOT / "tests" / "fixtures" / "mcel_application_ir" / "contract-counter.ir.json"
DEFAULT_DSL_SOURCE = REPOSITORY_ROOT / "mcel_apps" / "contract-counter" / "application.js"

COMPATIBILITY_REPORT_SCHEMA = "mcel.application-compatibility-report.v1"
COMPATIBILITY_VERSION = "mcel-counter-compatibility-wave3"
COMPATIBILITY_REPORT_ROOT = REPOSITORY_ROOT / "runtime" / "reports" / "mcel-application-compatibility" / "apps" / APP_ID


def build_counter_compatibility_profile() -> ExplicitPackageCompatibilityProfile:
    """Build the Counter profile for generic explicit-package compatibility."""

    return ExplicitPackageCompatibilityProfile(
        app_id=APP_ID,
        import_package=import_counter_legacy_package,
        default_package_root=DEFAULT_COUNTER_ROOT,
        default_fixture_ir=DEFAULT_FIXTURE_IR,
        default_dsl_source=DEFAULT_DSL_SOURCE,
        default_report_root=COMPATIBILITY_REPORT_ROOT,
        report_schema=COMPATIBILITY_REPORT_SCHEMA,
        report_version=COMPATIBILITY_VERSION,
        report_title="Contract Counter Application Compatibility Report",
        live_importer_id="mcel.counter.legacy-importer",
        fixture_unreadable_code="MCEL_COUNTER_FIXTURE_UNREADABLE",
        source_binding_stale_code="MCEL_COUNTER_FIXTURE_SOURCE_BINDING_STALE",
        feature_compatibility_failed_code="MCEL_COUNTER_FEATURE_COMPATIBILITY_FAILED",
        feature_compatibility_failed_summary=(
            "At least one Counter semantic feature is not exact across live, fixture, and DSL representations."
        ),
        source_binding_stale_summary=(
            "The Counter IR fixture source hashes do not exactly match the live explicit package."
        ),
    )


PROJECTION_REPORT_SCHEMA = "mcel.counter-candidate-projection-report.v1"
PROJECTION_VERSION = "mcel-counter-candidate-projection-wave4"
PROJECTION_PROFILE = "mcel.counter.explicit-projection.v1"

EVIDENCE_REPORT_SCHEMA = "mcel.counter-candidate-evidence-report.v1"
EVIDENCE_REPORT_VERSION = "mcel-counter-candidate-evidence-wave5"
EFFECT_REPORT_SCHEMA = "mcel.counter-effect-accounting-report.v1"
NODE_PROBE_SCHEMA = "mcel.counter-effect-probe.v1"
BROWSER_PROBE_SCHEMA = "mcel.counter-browser-effect-probe.v1"

GENERATED_CONTRACTS = (
    "contracts/domain.js",
    "contracts/intents.js",
    "contracts/adapter.js",
    "contracts/surface.js",
    "contracts/layout.js",
    "contracts/acceptance.js",
    "contracts/observation.js",
)


def build_counter_projection_profile(
    *,
    generate_contracts: Callable[[Mapping[str, Any]], Mapping[str, bytes]],
) -> ExplicitPackageProjectionProfile:
    """Build the Counter profile for generic explicit-package projection."""

    return ExplicitPackageProjectionProfile(
        app_id=APP_ID,
        projection_profile=PROJECTION_PROFILE,
        generate_contracts=generate_contracts,
        import_package=import_counter_legacy_package,
        generated_contracts=GENERATED_CONTRACTS,
        default_dsl_source=DEFAULT_DSL_SOURCE,
        default_fixture_ir=DEFAULT_FIXTURE_IR,
        default_live_package_root=DEFAULT_COUNTER_ROOT,
        default_candidate_root=DEFAULT_CANDIDATE_ROOT,
        report_schema=PROJECTION_REPORT_SCHEMA,
        version=PROJECTION_VERSION,
        source_conflict_code="MCEL_COUNTER_PROJECTION_SOURCE_CONFLICT",
        unsupported_ir_code="MCEL_COUNTER_PROJECTION_UNSUPPORTED_IR",
        invalid_live_package_code="MCEL_COUNTER_LIVE_PACKAGE_RECORD_INVALID",
        file_conflict_code="MCEL_COUNTER_PROJECTION_FILE_CONFLICT",
        package_fingerprint_conflict_code="MCEL_COUNTER_CANDIDATE_PACKAGE_FINGERPRINT_CONFLICT",
        runtime_fingerprint_conflict_code="MCEL_COUNTER_CANDIDATE_RUNTIME_FINGERPRINT_CONFLICT",
        drift_code="MCEL_COUNTER_CANDIDATE_GENERATED_DRIFT",
        roundtrip_conflict_code="MCEL_COUNTER_CANDIDATE_ROUNDTRIP_CONFLICT",
        source_conflict_summary="DSL and live Counter fixture semantics must be exact before projection.",
        roundtrip_conflict_summary=(
            "Generated candidate package does not import back to the canonical Counter fixture semantics."
        ),
    )


def build_counter_candidate_evidence_profile(
    *,
    project_candidate: Callable[..., Any],
    build_effect_accounting: Callable[..., Mapping[str, Any]],
) -> ExplicitPackageCandidateEvidenceProfile:
    """Build the Counter profile for generic explicit-package evidence."""

    return ExplicitPackageCandidateEvidenceProfile(
        app_id=APP_ID,
        project_candidate=project_candidate,
        build_effect_accounting=build_effect_accounting,
        default_dsl_source=DEFAULT_DSL_SOURCE,
        default_fixture_ir=DEFAULT_FIXTURE_IR,
        default_candidate_root=DEFAULT_CANDIDATE_ROOT,
        default_report_root=DEFAULT_EVIDENCE_REPORT_ROOT,
        report_schema=EVIDENCE_REPORT_SCHEMA,
        report_version=EVIDENCE_REPORT_VERSION,
        report_title="MCEL Counter Reference Fixture Candidate Evidence",
        effect_accounting_filename="mcel-counter-effect-accounting-report.json",
        node_probe_filename="mcel-counter-node-effect-probe.json",
        browser_probe_filename="mcel-counter-browser-effect-probe.json",
        invalid_dsl_message="DSL compilation did not produce valid Counter fixture IR.",
        invalid_projection_message="Counter fixture candidate projection is not exact.",
        evidence_failed_code="MCEL_COUNTER_CANDIDATE_EVIDENCE_FAILED",
        source_invalid_code="MCEL_COUNTER_CANDIDATE_EVIDENCE_SOURCE_INVALID",
        stage_failed_code="MCEL_COUNTER_CANDIDATE_STAGE_FAILED",
        live_changed_code="MCEL_COUNTER_LIVE_PACKAGE_CHANGED",
        live_changed_summary="The live Counter fixture package changed during isolated candidate evidence execution.",
    )


IR_NATIVE_REPORT_SCHEMA = "mcel.ir-native-intent-complete-proof.v1"
IR_NATIVE_REPORT_VERSION = "mcel-counter-ir-native-proof-wave8"


def build_counter_ir_native_proof_profile(
    *,
    run_node_probe: Callable[[Path, str], Mapping[str, Any]],
    run_browser_probe: Callable[[Path, bool, str], Mapping[str, Any]],
    build_effect_accounting: Callable[..., Mapping[str, Any]],
) -> ExplicitPackageIrNativeProofProfile:
    """Build the Counter profile for generic explicit-package IR-native proof."""

    return ExplicitPackageIrNativeProofProfile(
        app_id=APP_ID,
        run_node_probe=run_node_probe,
        run_browser_probe=run_browser_probe,
        build_effect_accounting=build_effect_accounting,
        report_schema=IR_NATIVE_REPORT_SCHEMA,
        report_version=IR_NATIVE_REPORT_VERSION,
        report_title="Contract Counter IR-Native Intent-Complete Proof",
        operation_prefix="ir-native",
        generated_file_generator=PROJECTION_PROFILE,
        abstract_to_runtime_code={"REVISION_STALE": "SCM_STALE_REVISION"},
        wrong_app_message="Wave 8 currently supports only contract-counter.",
        non_authoritative_message="The application is not declared dsl-authoritative.",
        authoring_binding_message=(
            "Promoted Counter authoring must bind application.js and mcel.generated.json exactly."
        ),
        missing_source_or_ownership_message=(
            "Authoritative DSL source or virtual generated ownership manifest is missing."
        ),
        invalid_dsl_message="Authoritative DSL did not compile to valid canonical IR",
        app_identity_message="Authoritative DSL app identity does not match the package manifest.",
        catalog_binding_message="Counter package catalog binding is stale.",
        runtime_projection_message="Counter runtime projection was not discovered exactly once.",
        acceptance_binding_message=(
            "Acceptance evidence is not exactly bound to the promoted Counter package and repository."
        ),
        browser_binding_message=(
            "Browser observation is not exactly bound to the promoted Counter package, projection, and repository."
        ),
        effect_accounting_message="IR-native effect accounting did not close",
        convergence_failure_message="IR-native intent-complete proof did not converge",
    )


PROMOTION_REPORT_SCHEMA = "mcel.counter-promotion-rehearsal-report.v1"
PROMOTION_REPORT_VERSION = "mcel-counter-promotion-rehearsal-wave6"
PROMOTION_PLAN_SCHEMA = "mcel.counter-promotion-plan.v1"
PROMOTION_OWNERSHIP_SCHEMA = "mcel.generated-file-ownership.v1"


def build_counter_promotion_rehearsal_profile(
    *,
    project_candidate: Callable[..., Any],
    run_candidate_evidence: Callable[..., Any],
    compare_representations: Callable[..., Any],
    run_node_probe: Callable[[Path], Mapping[str, Any]],
    run_browser_probe: Callable[[Path, bool], Mapping[str, Any]],
    build_effect_accounting: Callable[..., Mapping[str, Any]],
) -> ExplicitPackagePromotionRehearsalProfile:
    """Build the Counter profile for generic explicit-package promotion rehearsal."""

    return ExplicitPackagePromotionRehearsalProfile(
        app_id=APP_ID,
        project_candidate=project_candidate,
        run_candidate_evidence=run_candidate_evidence,
        compare_representations=compare_representations,
        import_package=import_counter_legacy_package,
        build_effect_accounting=build_effect_accounting,
        run_node_probe=run_node_probe,
        run_browser_probe=run_browser_probe,
        generated_contracts=GENERATED_CONTRACTS,
        default_dsl_source=DEFAULT_DSL_SOURCE,
        default_fixture_ir=DEFAULT_FIXTURE_IR,
        default_candidate_root=DEFAULT_CANDIDATE_ROOT,
        default_evidence_report_root=DEFAULT_EVIDENCE_REPORT_ROOT,
        default_report_root=DEFAULT_PROMOTION_REPORT_ROOT,
        report_schema=PROMOTION_REPORT_SCHEMA,
        report_version=PROMOTION_REPORT_VERSION,
        plan_schema=PROMOTION_PLAN_SCHEMA,
        ownership_schema=PROMOTION_OWNERSHIP_SCHEMA,
        generator_profile=PROJECTION_PROFILE,
        report_filename="mcel-counter-promotion-rehearsal-report.json",
        report_markdown_filename="mcel-counter-promotion-rehearsal-report.md",
        report_title="Counter Promotion Rehearsal",
        live_authority="legacy-explicit-package",
        rehearsed_authority="mcel.dsl.v1",
        source_authority_after="mcel.dsl.v1",
        evidence_invalid_code="MCEL_COUNTER_PROMOTION_EVIDENCE_INVALID",
        evidence_binding_conflict_code="MCEL_COUNTER_PROMOTION_EVIDENCE_BINDING_CONFLICT",
        rehearsal_failed_code="MCEL_COUNTER_PROMOTION_REHEARSAL_FAILED",
        unavailable_code="MCEL_COUNTER_PROMOTION_REHEARSAL_UNAVAILABLE",
        mutated_live_code="MCEL_COUNTER_PROMOTION_REHEARSAL_MUTATED_LIVE_REPOSITORY",
        invalid_dsl_message="Authoritative Counter DSL is not valid.",
        invalid_candidate_message="Wave 4 candidate projection is not exact.",
        invalid_evidence_message="Candidate evidence is not promotion-rehearsal eligible.",
        stale_evidence_message="Candidate evidence binding is stale or conflicting.",
        evidence_invalid_summary=(
            "The exact candidate has not independently earned semantic-runtime-proven evidence."
        ),
        evidence_binding_summary=(
            "Candidate evidence is not bound to the exact DSL semantic and source-binding fingerprints."
        ),
        post_compatibility_failure_message="Post-promotion compatibility is not exact.",
        rollback_failure_message=(
            "Rollback did not restore the original Counter package fingerprints exactly."
        ),
        protected_source_scope="counter-and-shared-mcel-authority-sources",
        protected_exact_paths=(
            "mcel_apps/contract-counter/application.js",
            "tests/fixtures/mcel_application_ir/contract-counter.ir.json",
        ),
        protected_prefixes=(
            "mcel_apps/contract-counter/",
            "main_computer/mcel_",
            "tools/mcel_",
            "main_computer/web/applications/mcel-packages/contract-counter/",
            "main_computer/web/applications/scripts/mcel-",
        ),
        compatibility_report_tool_command="python tools/mcel_counter_compatibility.py --write-report",
    )


PROMOTION_EXECUTION_REPORT_SCHEMA = "mcel.counter-promotion-execution-report.v1"
PROMOTION_EXECUTION_REPORT_VERSION = "mcel-counter-promotion-wave7"
PROMOTION_TRANSACTION_SCHEMA = "mcel.counter-promotion-transaction.v1"
PROMOTION_ROLLBACK_SCHEMA = "mcel.counter-promotion-rollback-result.v1"
PROMOTION_TRANSACTION_ROOT = Path("runtime/state/mcel/counter-promotions")
PROMOTION_EXECUTION_REPORT_ROOT = Path("runtime/reports/mcel-counter-promotions")
PROMOTION_LOCK_PATH = Path("runtime/state/mcel/counter-promotion.lock")


def build_counter_promotion_execution_profile(
    *,
    rehearsal_profile: ExplicitPackagePromotionRehearsalProfile,
    run_rehearsal: Callable[..., Any],
    compare_representations: Callable[..., Any],
    run_node_probe: Callable[[Path], Mapping[str, Any]],
    run_browser_probe: Callable[[Path, bool], Mapping[str, Any]],
    build_effect_accounting: Callable[..., Mapping[str, Any]],
) -> ExplicitPackagePromotionProfile:
    """Build the Counter profile for generic explicit-package promotion execution."""

    return ExplicitPackagePromotionProfile(
        app_id=APP_ID,
        rehearsal_profile=rehearsal_profile,
        run_rehearsal=run_rehearsal,
        compare_representations=compare_representations,
        import_package=import_counter_legacy_package,
        build_effect_accounting=build_effect_accounting,
        run_node_probe=run_node_probe,
        run_browser_probe=run_browser_probe,
        package_relative_path="mcel_apps/contract-counter",
        default_fixture_ir=DEFAULT_FIXTURE_IR,
        default_transaction_root=PROMOTION_TRANSACTION_ROOT,
        default_report_root=PROMOTION_EXECUTION_REPORT_ROOT,
        default_rehearsal_report_root=DEFAULT_PROMOTION_REPORT_ROOT,
        default_lock_path=PROMOTION_LOCK_PATH,
        report_schema=PROMOTION_EXECUTION_REPORT_SCHEMA,
        report_version=PROMOTION_EXECUTION_REPORT_VERSION,
        transaction_schema=PROMOTION_TRANSACTION_SCHEMA,
        rollback_schema=PROMOTION_ROLLBACK_SCHEMA,
        report_filename="mcel-counter-promotion-report",
        rollback_report_filename="mcel-counter-promotion-rollback-report",
        report_title="Counter Authority Promotion",
        rollback_report_title="Counter Authority Rollback",
        live_authority_before="legacy-explicit-package",
        allowed_authorities_before=("legacy-explicit", "legacy-explicit-package"),
        target_authoring_status="dsl-authoritative",
        target_source_authority="mcel.dsl.v1",
        derived_artifact_authority=PROJECTION_PROFILE,
        legacy_package_authority="retired",
        truth_status="semantic-runtime-proven",
        compatibility_report_root=Path("runtime/reports/mcel-application-compatibility/apps/contract-counter"),
        promotion_failed_code="MCEL_COUNTER_PROMOTION_FAILED",
        rollback_failed_code="MCEL_COUNTER_PROMOTION_ROLLBACK_FAILED",
        promotion_lock_schema="mcel.counter-promotion-lock.v1",
        lock_held_message="Another Counter promotion operation holds the lock",
        already_promoted_message=(
            "Counter already declares mcel.dsl.v1 authority; no second promotion was executed."
        ),
        unsupported_authority_message="Unsupported Counter source authority before promotion",
        rehearsal_failed_message="Fresh Wave 6 rehearsal did not pass.",
        rehearsal_ineligible_message="Fresh Wave 6 rehearsal did not authorize promotion eligibility.",
        rehearsal_rollback_message="Fresh Wave 6 rehearsal did not prove exact rollback restoration.",
        invalid_plan_schema_message="Fresh rehearsal returned an unknown promotion-plan schema.",
        invalid_plan_start_message="Promotion plan does not begin at legacy explicit-package authority.",
        invalid_plan_end_message="Promotion plan does not establish mcel.dsl.v1 authority.",
        invalid_plan_evidence_message=(
            "Promotion plan is not bound to fresh semantic-runtime-proven candidate evidence."
        ),
        empty_plan_message="Promotion plan contains no file transition records.",
        compatibility_failed_message="Post-promotion three-way compatibility is not exact.",
        promoted_dsl_failed_message="Promoted authoritative DSL failed compilation.",
        authority_failed_message="Post-promotion authority checks failed",
        automatic_rollback_failed_message=(
            "Automatic rollback did not restore the protected source boundary exactly."
        ),
        committed_only_rollback_message="Only a committed Counter promotion transaction can be rolled back.",
        rollback_drift_message="Protected MCEL source drift blocks rollback",
        rollback_not_exact_message="Rollback restoration is not exact",
        rollback_fingerprint_message="Rollback fingerprint restoration is not exact",
        no_transaction_message="No committed Counter promotion transaction exists.",
        invalid_transaction_message="Invalid Counter promotion transaction identifier.",
        transaction_not_found_message="Counter promotion transaction not found",
    )

