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
from main_computer.mcel_profiled_package_candidate_evidence import ProfiledPackageCandidateEvidenceProfile
from main_computer.mcel_profiled_package_candidate_projection import ProfiledPackageProjectionProfile
from main_computer.mcel_profiled_package_ir_native_proof import ProfiledPackageIrNativeProofProfile
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
        expected_intent_count=7,
        expected_scenario_count=14,
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
        expected_intent_count=7,
        expected_scenario_count=14,
        expected_effect_count=18,
        expected_observation_coverage_count=7,
        expected_capability_count=1,
        expected_capability_intent_count=2,
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


def _project_ir(application_ir: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, bytes]]:
    projection = project_workbench_ir(application_ir)
    return projection.profile, dict(projection.files)


def _source_metrics(application_ir: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        "nativeDomainCallCount": _count_kind(application_ir, "domain.call"),
        "opaqueCallbackDebt": _count_active_opaque(application_ir),
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


def _count_active_opaque(value: Any) -> int:
    if isinstance(value, Mapping):
        return (1 if value.get("kind") == "legacy.opaque-function" else 0) + sum(
            _count_active_opaque(child)
            for key, child in value.items()
            if str(key) != "compatibility"
        )
    if isinstance(value, list):
        return sum(_count_active_opaque(child) for child in value)
    return 0


def _count_kind(value: Any, kind: str) -> int:
    if isinstance(value, Mapping):
        return (1 if value.get("kind") == kind else 0) + sum(_count_kind(child, kind) for child in value.values())
    if isinstance(value, list):
        return sum(_count_kind(child, kind) for child in value)
    return 0


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
