"""Generic profiled-package promotion execution for MCEL fixture apps.

Profiled-package promotion execution consumes a successful promotion rehearsal,
applies the exact rehearsed payload to the live repository under a durable
transaction directory, reruns promoted authorities, and preserves rollback
material.  App-specific wrappers provide only profile facts and stable entry
points; this module owns the transaction/apply/rollback mechanics.

Already DSL-authoritative packages are treated as an idempotent success.  That
lets reference fixtures remain promoted while still sharing the same execution
path used for legacy-to-DSL authority transitions.
"""

from __future__ import annotations

import json
import os
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence
from uuid import uuid4

from main_computer.mcel_application_ir import canonical_json_bytes
from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_profiled_package_promotion_rehearsal import (
    DEFAULT_REPORT_ROOT as DEFAULT_REHEARSAL_REPORT_ROOT,
    ProfiledPackagePromotionRehearsalProfile,
    _display,
    _protected_source_snapshot,
    _restore_live_package,
    _run_promoted_authorities,
    _sha,
    _tree_snapshot,
    _verify_generated_ownership,
    _workspace_fingerprints,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REPORT_SCHEMA = "mcel.profiled-package-promotion-execution-report.v1"
REPORT_VERSION = "mcel-profiled-package-promotion-v1"
TRANSACTION_SCHEMA = "mcel.profiled-package-promotion-transaction.v1"
ROLLBACK_SCHEMA = "mcel.profiled-package-promotion-rollback-result.v1"
DEFAULT_TRANSACTION_ROOT = Path("runtime/state/mcel/profiled-package-promotions")
DEFAULT_REPORT_ROOT = Path("runtime/reports/mcel-profiled-package-promotions")
DEFAULT_LOCK_PATH = Path("runtime/state/mcel/profiled-package-promotion.lock")


class ProfiledPackagePromotionError(RuntimeError):
    """Raised when profiled-package promotion or rollback cannot complete truthfully."""


@dataclass(frozen=True)
class ProfiledPackagePromotionResult:
    valid: bool
    status: str
    report: Mapping[str, Any]
    diagnostics: tuple[Mapping[str, Any], ...]
    output_directory: Path | None = None

    @property
    def diagnostic_count(self) -> int:
        return len(self.diagnostics)

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.report)
        payload["diagnosticCount"] = self.diagnostic_count
        payload["diagnostics"] = [dict(item) for item in self.diagnostics]
        if self.output_directory is not None:
            payload.setdefault("artifacts", {})["outputDirectory"] = _display(
                self.output_directory, REPOSITORY_ROOT
            )
        return payload


@dataclass(frozen=True)
class ProfiledPackagePromotionProfile:
    app_id: str
    rehearsal_profile: ProfiledPackagePromotionRehearsalProfile
    run_rehearsal: Callable[..., Any]
    default_package_root: Path
    default_fixture_ir: Path | None
    projection_profile: str
    default_transaction_root: Path = DEFAULT_TRANSACTION_ROOT
    default_report_root: Path = DEFAULT_REPORT_ROOT
    default_rehearsal_report_root: Path = DEFAULT_REHEARSAL_REPORT_ROOT
    default_lock_path: Path = DEFAULT_LOCK_PATH
    report_schema: str = REPORT_SCHEMA
    report_version: str = REPORT_VERSION
    transaction_schema: str = TRANSACTION_SCHEMA
    rollback_schema: str = ROLLBACK_SCHEMA
    report_filename: str = "mcel-profiled-package-promotion-report"
    rollback_report_filename: str = "mcel-profiled-package-promotion-rollback-report"
    report_title: str = "Profiled Package Authority Promotion"
    rollback_report_title: str = "Profiled Package Authority Rollback"
    live_authority_before: str = "legacy-explicit-package"
    allowed_authorities_before: tuple[str, ...] = (
        "legacy-explicit",
        "legacy-explicit-package",
        "semantic-runtime-proven",
    )
    target_authoring_status: str = "dsl-authoritative"
    target_source_authority: str = "mcel.dsl.v1"
    derived_artifact_authority: str | None = None
    legacy_package_authority: str = "retired"
    truth_status: str = "semantic-runtime-proven"
    promoted_idempotent_status: str = "already-promoted"
    promotion_failed_code: str = "MCEL_PROFILED_PACKAGE_PROMOTION_FAILED"
    rollback_failed_code: str = "MCEL_PROFILED_PACKAGE_PROMOTION_ROLLBACK_FAILED"
    lock_schema: str = "mcel.profiled-package-promotion-lock.v1"
    lock_held_message: str = "Another profiled-package promotion operation holds the lock"
    already_promoted_summary: str = (
        "Application already declares DSL authority; no second promotion was executed."
    )
    unsupported_authority_message: str = "Unsupported source authority before promotion"
    rehearsal_failed_message: str = "Fresh promotion rehearsal did not pass."
    rehearsal_ineligible_message: str = "Fresh promotion rehearsal did not authorize promotion eligibility."
    rehearsal_rollback_message: str = "Fresh promotion rehearsal did not prove exact rollback restoration."
    invalid_plan_schema_message: str = "Fresh rehearsal returned an unknown promotion-plan schema."
    invalid_plan_app_message: str = "Fresh rehearsal returned a plan for another application."
    invalid_plan_start_message: str = "Promotion plan does not begin at legacy explicit-package authority."
    invalid_plan_end_message: str = "Promotion plan does not establish mcel.dsl.v1 authority."
    invalid_plan_generated_authority_message: str = "Promotion plan does not establish the expected generated authority."
    invalid_plan_evidence_message: str = (
        "Promotion plan is not bound to fresh semantic-runtime-proven candidate evidence."
    )
    empty_plan_message: str = "Promotion plan contains no file transition records."
    promoted_dsl_failed_message: str = "Promoted authoritative DSL failed exact compilation."
    authority_failed_message: str = "Post-promotion authority checks failed"
    automatic_rollback_failed_message: str = (
        "Automatic rollback did not restore the protected source boundary exactly."
    )
    committed_only_rollback_message: str = "Only a committed promotion transaction can be rolled back."
    rollback_drift_message: str = "Protected MCEL source drift blocks rollback"
    rollback_not_exact_message: str = "Rollback restoration is not exact"
    rollback_package_message: str = "Package rollback restoration is not exact"
    rollback_fingerprint_message: str = "Rollback fingerprint restoration is not exact"
    no_transaction_message: str = "No committed promotion transaction exists."
    invalid_transaction_message: str = "Invalid promotion transaction identifier."
    transaction_not_found_message: str = "Promotion transaction not found"
    stage_name: str = "promotion-execution"
    diagnostic_schema: str = "mcel.compiler-diagnostic.v1"

    @property
    def generated_authority_label(self) -> str:
        return self.derived_artifact_authority or self.projection_profile

    @property
    def expected_intent_count(self) -> int:
        return self.rehearsal_profile.expected_intent_count

    @property
    def expected_scenario_count(self) -> int:
        return self.rehearsal_profile.expected_scenario_count

    @property
    def expected_effect_count(self) -> int:
        return self.rehearsal_profile.expected_effect_count

    @property
    def expected_capability_count(self) -> int:
        return self.rehearsal_profile.expected_capability_count


def execute_profiled_package_promotion(
    profile: ProfiledPackagePromotionProfile,
    *,
    repo_root: Path = REPOSITORY_ROOT,
    fixture_ir_path: Path | None = None,
    transaction_root: Path | None = None,
    report_root: Path | None = None,
    rehearsal_report_root: Path | None = None,
    headed: bool = False,
    write_report: bool = True,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    rehearsal_runner: Callable[..., Any] | None = None,
    failure_injector: Callable[[str], None] | None = None,
    force_repromotion: bool = False,
) -> ProfiledPackagePromotionResult:
    """Execute a fresh, rehearsed profiled-package authority transition."""

    repo = repo_root.resolve()
    diagnostics: list[Mapping[str, Any]] = []
    live_package = repo / profile.default_package_root
    lock_path = _resolve_under(repo, profile.default_lock_path)
    tx_base = _resolve_under(repo, transaction_root or profile.default_transaction_root)
    report_base = _resolve_under(repo, report_root or profile.default_report_root)
    rehearsal_reports = _resolve_under(repo, rehearsal_report_root or profile.default_rehearsal_report_root)
    runner = command_runner or subprocess.run

    try:
        with _promotion_lock(profile, lock_path):
            existing = _authoring_status(live_package)
            if existing == profile.target_authoring_status and not force_repromotion:
                report = _already_promoted_report(profile, repo, live_package)
                output = report_base / "already-promoted"
                if write_report:
                    write_profiled_package_promotion_execution_report(
                        profile,
                        output,
                        report,
                        diagnostics,
                    )
                return ProfiledPackagePromotionResult(
                    True,
                    profile.promoted_idempotent_status,
                    report,
                    tuple(diagnostics),
                    output if write_report else None,
                )

            if existing not in (None, *profile.allowed_authorities_before, profile.target_authoring_status):
                raise ProfiledPackagePromotionError(
                    f"{profile.unsupported_authority_message}: {existing}"
                )

            rehearse = rehearsal_runner or profile.run_rehearsal
            rehearsal = rehearse(
                repo_root=repo,
                fixture_ir_path=fixture_ir_path or profile.default_fixture_ir,
                report_root=rehearsal_reports,
                headed=headed,
                write_report=True,
                command_runner=command_runner,
            )
            rehearsal_payload = (
                rehearsal.to_dict() if hasattr(rehearsal, "to_dict") else dict(rehearsal)
            )
            if not bool(getattr(rehearsal, "valid", rehearsal_payload.get("valid"))):
                raise ProfiledPackagePromotionError(profile.rehearsal_failed_message)
            if not rehearsal_payload.get("promotionEligible"):
                raise ProfiledPackagePromotionError(profile.rehearsal_ineligible_message)
            if rehearsal_payload.get("rollbackRestoration") != "exact":
                raise ProfiledPackagePromotionError(profile.rehearsal_rollback_message)

            plan = dict(rehearsal_payload.get("plan") or {})
            _validate_plan(profile, plan)
            promotion_root = _artifact_path(profile, repo, rehearsal_payload, "promotionMaterial")
            promoted = _load_promoted_payload(profile, plan, promotion_root)
            _verify_plan_preconditions(profile, repo, plan, promoted)

            transaction_id = _transaction_id(str(plan["sourceBindingFingerprint"]))
            transaction_directory = tx_base / transaction_id
            output_directory = report_base / transaction_id
            if transaction_directory.exists():
                raise ProfiledPackagePromotionError(
                    f"Promotion transaction already exists: {transaction_id}"
                )

            protected_before = _protected_source_snapshot(repo, profile.rehearsal_profile)
            package_before = _tree_snapshot(live_package)
            before_fingerprints = _workspace_fingerprints(repo, profile.rehearsal_profile)
            _prepare_transaction(
                repo=repo,
                transaction_directory=transaction_directory,
                plan=plan,
                promoted=promoted,
                protected_before=protected_before,
                before_fingerprints=before_fingerprints,
                package_before=package_before,
            )
            _write_transaction_state(profile, transaction_directory, "prepared", plan, committed=False)
            if failure_injector:
                failure_injector("prepared")

            try:
                _apply_transaction_payload(profile, repo, plan, promoted, transaction_id)
                _write_transaction_state(profile, transaction_directory, "applied", plan, committed=False)
                if failure_injector:
                    failure_injector("applied")

                ownership_exact = _verify_generated_ownership(profile.rehearsal_profile, live_package)
                live_dsl = live_package / "application.js"
                fixture = _resolve_under(repo, fixture_ir_path or profile.default_fixture_ir)
                compiled = compile_dsl_application(live_dsl, compare_ir_path=fixture)
                if (
                    not compiled.valid
                    or compiled.normalized_ir is None
                    or compiled.comparison_status != "exact"
                ):
                    raise ProfiledPackagePromotionError(profile.promoted_dsl_failed_message)

                _run_promoted_authorities(profile.rehearsal_profile, repo, repo, headed, runner)
                acceptance = _load_json(
                    repo
                    / f"runtime/reports/mcel-acceptance/apps/{profile.app_id}/mcel-acceptance-report.json",
                    "post-promotion acceptance report",
                    profile,
                )
                observation = _load_json(
                    repo
                    / f"runtime/reports/mcel-observation/apps/{profile.app_id}/mcel-operation-observation-report.json",
                    "post-promotion observation report",
                    profile,
                )
                proof = _load_json(
                    repo
                    / f"runtime/reports/mcel-app-proof/apps/{profile.app_id}/mcel-app-proof-report.json",
                    "post-promotion application proof",
                    profile,
                )
                intent = proof.get("intentCoverage") or {}
                effects = intent.get("effectAccounting") or {}
                capabilities = intent.get("capabilityAccounting") or {}
                after_fingerprints = _workspace_fingerprints(repo, profile.rehearsal_profile)

                checks = {
                    "sourceAuthority": _authoring_status(live_package) == profile.target_authoring_status,
                    "generatedOwnership": ownership_exact is True,
                    "dslCompilation": compiled.valid and compiled.comparison_status == "exact",
                    "semanticIdentity": compiled.semantic_fingerprint == plan.get("semanticFingerprint"),
                    "packageValidation": bool(after_fingerprints.get("packageValid")),
                    "acceptance": acceptance.get("status") == "pass" and acceptance.get("passed") is True,
                    "browserObservation": observation.get("status") == "pass" and observation.get("ok") is True,
                    "applicationProof": proof.get("status") == "pass"
                    and proof.get("truthStatus") == profile.truth_status,
                    "repositoryBinding": (proof.get("stages") or {})
                    .get("repositoryBinding", {})
                    .get("status")
                    == "exact",
                    "intentCompleteness": intent.get("status") == "ir-native"
                    and intent.get("coveredIntentCount") == profile.expected_intent_count
                    and intent.get("declaredIntentCount") == profile.expected_intent_count,
                    "scenarioCompleteness": intent.get("observedScenarioCount") == profile.expected_scenario_count
                    and intent.get("declaredScenarioCount") == profile.expected_scenario_count,
                    "effectAccounting": effects.get("status") == "closed"
                    and effects.get("declaredEffectCount") == profile.expected_effect_count
                    and effects.get("closedEffectCount") == profile.expected_effect_count,
                    "capabilityAccounting": capabilities.get("status") == "closed"
                    and capabilities.get("declaredCapabilityCount") == profile.expected_capability_count,
                }
                failed = [name for name, passed in checks.items() if not passed]
                if failed:
                    raise ProfiledPackagePromotionError(
                        profile.authority_failed_message + ": " + ", ".join(failed)
                    )
                if failure_injector:
                    failure_injector("verified")

                protected_after = _protected_source_snapshot(repo, profile.rehearsal_profile)
                _write_json(transaction_directory / "protected-after.json", protected_after)
                transaction_report = {
                    "schema": profile.report_schema,
                    "version": profile.report_version,
                    "appId": profile.app_id,
                    "valid": True,
                    "status": "pass",
                    "transactionId": transaction_id,
                    "promotionExecuted": True,
                    "candidatePromoted": True,
                    "sourceAuthorityBefore": profile.live_authority_before,
                    "sourceAuthority": profile.target_source_authority,
                    "derivedArtifactAuthority": profile.generated_authority_label,
                    "legacyPackageAuthority": profile.legacy_package_authority,
                    "truthStatus": profile.truth_status,
                    "semanticFingerprint": compiled.semantic_fingerprint,
                    "candidateSourceBindingFingerprint": plan.get("sourceBindingFingerprint"),
                    "liveSourceBindingFingerprint": compiled.source_binding_fingerprint,
                    "rollbackAvailable": True,
                    "rollbackTransaction": transaction_id,
                    "automaticRollbackPerformed": False,
                    "checks": {name: {"status": "pass"} for name in checks},
                    "fingerprints": {"before": before_fingerprints, "after": after_fingerprints},
                    "authority": {
                        "liveApplicationChanged": True,
                        "promotionExecuted": True,
                        "candidatePromoted": True,
                        "sourceAuthority": profile.target_source_authority,
                        "derivedArtifactAuthority": profile.generated_authority_label,
                        "legacyPackageAuthority": profile.legacy_package_authority,
                        "rollbackAvailable": True,
                    },
                    "artifacts": {
                        "transaction": _display(transaction_directory, repo),
                        "rollback": _display(transaction_directory / "protected-before", repo),
                    },
                }
                _write_transaction_state(profile, transaction_directory, "committed", plan, committed=True)
                _write_json(transaction_directory / "promotion-result.json", transaction_report)
                if write_report:
                    write_profiled_package_promotion_execution_report(
                        profile,
                        output_directory,
                        transaction_report,
                        diagnostics,
                    )
                return ProfiledPackagePromotionResult(
                    True,
                    "pass",
                    transaction_report,
                    tuple(diagnostics),
                    output_directory if write_report else None,
                )
            except Exception as exc:
                rollback_error: str | None = None
                try:
                    _restore_protected_snapshot(profile, repo, transaction_directory)
                    restored = (
                        _protected_source_snapshot(repo, profile.rehearsal_profile) == protected_before
                        and _tree_snapshot(live_package) == package_before
                    )
                    if not restored:
                        rollback_error = profile.automatic_rollback_failed_message
                except Exception as rollback_exc:  # pragma: no cover - catastrophic path
                    rollback_error = str(rollback_exc)
                _write_transaction_state(
                    profile,
                    transaction_directory,
                    "rolled-back" if rollback_error is None else "rollback-failed",
                    plan,
                    committed=False,
                    error=str(exc),
                    rollback_error=rollback_error,
                )
                message = str(exc)
                if rollback_error:
                    message += f" Automatic rollback failure: {rollback_error}"
                raise ProfiledPackagePromotionError(message) from exc
    except (ProfiledPackagePromotionError, OSError, ValueError, json.JSONDecodeError) as exc:
        diagnostics.append(_diagnostic(profile, profile.promotion_failed_code, str(exc), "$promotion"))
        report = {
            "schema": profile.report_schema,
            "version": profile.report_version,
            "appId": profile.app_id,
            "valid": False,
            "status": "fail",
            "promotionExecuted": False,
            "candidatePromoted": False,
            "sourceAuthority": _reported_source_authority(profile, live_package),
            "rollbackAvailable": False,
            "automaticRollbackPerformed": "Automatic rollback" in str(exc) or "rollback" in str(exc).lower(),
            "authority": {
                "promotionExecuted": False,
                "candidatePromoted": False,
                "sourceAuthority": _reported_source_authority(profile, live_package),
            },
        }
        return ProfiledPackagePromotionResult(False, "fail", report, tuple(diagnostics))


def rollback_profiled_package_promotion(
    profile: ProfiledPackagePromotionProfile,
    transaction: str,
    *,
    repo_root: Path = REPOSITORY_ROOT,
    transaction_root: Path | None = None,
    report_root: Path | None = None,
    write_report: bool = True,
) -> ProfiledPackagePromotionResult:
    """Roll back a committed profiled-package authority transition."""

    repo = repo_root.resolve()
    diagnostics: list[Mapping[str, Any]] = []
    tx_base = _resolve_under(repo, transaction_root or profile.default_transaction_root)
    report_base = _resolve_under(repo, report_root or profile.default_report_root)
    lock_path = _resolve_under(repo, profile.default_lock_path)
    live_package = repo / profile.default_package_root
    try:
        with _promotion_lock(profile, lock_path):
            tx = _resolve_transaction(profile, tx_base, transaction)
            state = _read_json(tx / "transaction-state.json", profile)
            if state.get("phase") != "committed" or not state.get("committed"):
                raise ProfiledPackagePromotionError(profile.committed_only_rollback_message)
            expected_after = _read_json(tx / "protected-after.json", profile)
            current = _protected_source_snapshot(repo, profile.rehearsal_profile)
            drift = _snapshot_changes(expected_after, current)
            if drift:
                raise ProfiledPackagePromotionError(
                    profile.rollback_drift_message + ": " + ", ".join(drift)
                )

            _restore_protected_snapshot(profile, repo, tx)
            expected_before = _read_json(tx / "protected-before.json", profile)
            restored = _protected_source_snapshot(repo, profile.rehearsal_profile)
            drift_after = _snapshot_changes(expected_before, restored)
            if drift_after:
                raise ProfiledPackagePromotionError(
                    profile.rollback_not_exact_message + ": " + ", ".join(drift_after)
                )
            expected_package = _read_json(tx / "package-before.json", profile)
            actual_package = _tree_snapshot(live_package)
            package_drift = _snapshot_changes(expected_package, actual_package)
            if package_drift:
                raise ProfiledPackagePromotionError(
                    profile.rollback_package_message + ": " + ", ".join(package_drift)
                )

            expected_fingerprints = _read_json(tx / "fingerprints-before.json", profile)
            restored_fingerprints = _workspace_fingerprints(repo, profile.rehearsal_profile)
            fingerprint_keys = ("package", "catalog", "runtimeProjection")
            fingerprint_drift = [
                key
                for key in fingerprint_keys
                if expected_fingerprints.get(key) != restored_fingerprints.get(key)
            ]
            if fingerprint_drift:
                raise ProfiledPackagePromotionError(
                    profile.rollback_fingerprint_message + ": " + ", ".join(fingerprint_drift)
                )

            state.update(
                {
                    "phase": "rolled-back",
                    "committed": False,
                    "rolledBackUtc": _utc_now(),
                }
            )
            _write_json(tx / "transaction-state.json", state)
            report = {
                "schema": profile.rollback_schema,
                "version": profile.report_version,
                "appId": profile.app_id,
                "valid": True,
                "status": "pass",
                "transactionId": tx.name,
                "rollbackExecuted": True,
                "restoration": "exact",
                "sourceAuthority": profile.live_authority_before,
                "promotionActive": False,
                "fingerprints": restored_fingerprints,
            }
            _write_json(tx / "rollback-result.json", report)
            output = report_base / tx.name / "rollback"
            if write_report:
                write_profiled_package_promotion_execution_report(
                    profile,
                    output,
                    report,
                    diagnostics,
                    filename=profile.rollback_report_filename,
                )
            return ProfiledPackagePromotionResult(
                True,
                "pass",
                report,
                tuple(diagnostics),
                output if write_report else None,
            )
    except (ProfiledPackagePromotionError, OSError, ValueError, json.JSONDecodeError) as exc:
        diagnostics.append(_diagnostic(profile, profile.rollback_failed_code, str(exc), "$rollback"))
        report = {
            "schema": profile.rollback_schema,
            "version": profile.report_version,
            "appId": profile.app_id,
            "valid": False,
            "status": "fail",
            "rollbackExecuted": False,
            "restoration": "blocked",
        }
        return ProfiledPackagePromotionResult(False, "fail", report, tuple(diagnostics))


def _already_promoted_report(
    profile: ProfiledPackagePromotionProfile,
    repo: Path,
    package: Path,
) -> dict[str, Any]:
    return {
        "schema": profile.report_schema,
        "version": profile.report_version,
        "appId": profile.app_id,
        "valid": True,
        "status": profile.promoted_idempotent_status,
        "promotionExecuted": False,
        "candidatePromoted": False,
        "sourceAuthority": profile.target_source_authority,
        "derivedArtifactAuthority": profile.generated_authority_label,
        "legacyPackageAuthority": profile.legacy_package_authority,
        "truthStatus": profile.truth_status,
        "rollbackAvailable": False,
        "alreadyPromoted": True,
        "summary": profile.already_promoted_summary,
        "fingerprints": _workspace_fingerprints(repo, profile.rehearsal_profile),
        "authority": {
            "liveApplicationChanged": False,
            "promotionExecuted": False,
            "candidatePromoted": False,
            "sourceAuthority": profile.target_source_authority,
            "derivedArtifactAuthority": profile.generated_authority_label,
            "legacyPackageAuthority": profile.legacy_package_authority,
            "rollbackAvailable": False,
        },
    }


def _validate_plan(profile: ProfiledPackagePromotionProfile, plan: Mapping[str, Any]) -> None:
    if plan.get("schema") != profile.rehearsal_profile.plan_schema:
        raise ProfiledPackagePromotionError(profile.invalid_plan_schema_message)
    if plan.get("appId") != profile.app_id:
        raise ProfiledPackagePromotionError(profile.invalid_plan_app_message)
    if plan.get("sourceAuthorityBefore") != profile.live_authority_before:
        raise ProfiledPackagePromotionError(profile.invalid_plan_start_message)
    if plan.get("sourceAuthorityAfter") != profile.target_source_authority:
        raise ProfiledPackagePromotionError(profile.invalid_plan_end_message)
    if plan.get("derivedArtifactAuthorityAfter") != profile.generated_authority_label:
        raise ProfiledPackagePromotionError(profile.invalid_plan_generated_authority_message)
    evidence = plan.get("candidateEvidenceBinding") or {}
    if evidence.get("truthStatus") != profile.truth_status or evidence.get("evidenceReused") is not False:
        raise ProfiledPackagePromotionError(profile.invalid_plan_evidence_message)
    files = plan.get("files")
    if not isinstance(files, list) or not files:
        raise ProfiledPackagePromotionError(profile.empty_plan_message)


def _load_promoted_payload(
    profile: ProfiledPackagePromotionProfile,
    plan: Mapping[str, Any],
    promotion_root: Path,
) -> dict[str, bytes]:
    promoted: dict[str, bytes] = {}
    for item in plan["files"]:
        relative = str(item["path"])
        path = promotion_root / relative
        if not path.is_file():
            raise ProfiledPackagePromotionError(f"Promotion payload is missing: {relative}")
        content = path.read_bytes()
        if _sha(content) != item.get("afterSha256"):
            raise ProfiledPackagePromotionError(f"Promotion payload hash mismatch: {relative}")
        promoted[relative] = content
    return promoted


def _verify_plan_preconditions(
    profile: ProfiledPackagePromotionProfile,
    repo: Path,
    plan: Mapping[str, Any],
    promoted: Mapping[str, bytes],
) -> None:
    for item in plan["files"]:
        relative = str(item["path"])
        current = repo / relative
        before = current.read_bytes() if current.is_file() else None
        if _sha(before) != item.get("beforeSha256"):
            raise ProfiledPackagePromotionError(f"Live before-hash drift blocks promotion: {relative}")
        if _sha(promoted[relative]) != item.get("afterSha256"):
            raise ProfiledPackagePromotionError(f"Staged after-hash mismatch blocks promotion: {relative}")


def _prepare_transaction(
    *,
    repo: Path,
    transaction_directory: Path,
    plan: Mapping[str, Any],
    promoted: Mapping[str, bytes],
    protected_before: Mapping[str, str],
    before_fingerprints: Mapping[str, Any],
    package_before: Mapping[str, str],
) -> None:
    transaction_directory.mkdir(parents=True, exist_ok=False)
    _write_json(transaction_directory / "promotion-plan.json", plan)
    _write_json(transaction_directory / "protected-before.json", protected_before)
    _write_json(transaction_directory / "fingerprints-before.json", before_fingerprints)
    _write_json(transaction_directory / "package-before.json", package_before)
    before_root = transaction_directory / "protected-before"
    for relative in protected_before:
        source = repo / relative
        target = before_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    promotion_root = transaction_directory / "promotion"
    for relative, content in promoted.items():
        target = promotion_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def _apply_transaction_payload(
    profile: ProfiledPackagePromotionProfile,
    repo: Path,
    plan: Mapping[str, Any],
    promoted: Mapping[str, bytes],
    transaction_id: str,
) -> None:
    staged: list[tuple[Path, Path, str]] = []
    try:
        for item in plan["files"]:
            relative = str(item["path"])
            target = repo / relative
            current = target.read_bytes() if target.is_file() else None
            if _sha(current) != item.get("beforeSha256"):
                raise ProfiledPackagePromotionError(
                    f"Live repository drift appeared during promotion: {relative}"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.mcel-promote-{transaction_id}.tmp")
            temporary.write_bytes(promoted[relative])
            if _sha(temporary.read_bytes()) != item.get("afterSha256"):
                raise ProfiledPackagePromotionError(
                    f"Temporary promotion file failed hash verification: {relative}"
                )
            staged.append((temporary, target, relative))
        for temporary, target, relative in staged:
            os.replace(temporary, target)
            expected = next(item.get("afterSha256") for item in plan["files"] if item["path"] == relative)
            if _sha(target.read_bytes()) != expected:
                raise ProfiledPackagePromotionError(
                    f"Applied promotion file failed hash verification: {relative}"
                )
    finally:
        for temporary, _target, _relative in staged:
            if temporary.exists():
                temporary.unlink()


def _restore_protected_snapshot(
    profile: ProfiledPackagePromotionProfile,
    repo: Path,
    transaction_directory: Path,
) -> None:
    before = _read_json(transaction_directory / "protected-before.json", profile)
    current = _protected_source_snapshot(repo, profile.rehearsal_profile)
    for relative in sorted(set(current) - set(before), reverse=True):
        path = repo / relative
        if path.is_file():
            path.unlink()
    backup = transaction_directory / "protected-before"
    for relative, digest in before.items():
        source = backup / relative
        if not source.is_file() or _sha(source.read_bytes()) != digest:
            raise ProfiledPackagePromotionError(f"Rollback backup integrity failed: {relative}")
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.mcel-rollback.tmp")
        temporary.write_bytes(source.read_bytes())
        os.replace(temporary, target)


def _authoring_status(package: Path) -> str | None:
    manifest = package / "mcel.app.json"
    if not manifest.is_file():
        return None
    try:
        value = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "invalid"
    authoring = value.get("authoring")
    return str(authoring.get("status")) if isinstance(authoring, Mapping) and authoring.get("status") else None


def _reported_source_authority(
    profile: ProfiledPackagePromotionProfile,
    package: Path,
) -> str:
    return (
        profile.target_source_authority
        if _authoring_status(package) == profile.target_authoring_status
        else profile.live_authority_before
    )


@contextmanager
def _promotion_lock(
    profile: ProfiledPackagePromotionProfile,
    path: Path,
) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ProfiledPackagePromotionError(f"{profile.lock_held_message}: {path}") from exc
    try:
        payload = {
            "schema": profile.lock_schema,
            "appId": profile.app_id,
            "pid": os.getpid(),
            "createdUtc": _utc_now(),
        }
        os.write(descriptor, canonical_json_bytes(payload) + b"\n")
        os.close(descriptor)
        yield
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _resolve_transaction(
    profile: ProfiledPackagePromotionProfile,
    root: Path,
    transaction: str,
) -> Path:
    if transaction == "latest":
        candidates = (
            sorted(
                (path for path in root.iterdir() if path.is_dir()),
                key=lambda path: path.name,
                reverse=True,
            )
            if root.is_dir()
            else []
        )
        for candidate in candidates:
            state_path = candidate / "transaction-state.json"
            if not state_path.is_file():
                continue
            try:
                state = _read_json(state_path, profile)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if state.get("phase") == "committed" and state.get("committed") is True:
                return candidate
        raise ProfiledPackagePromotionError(profile.no_transaction_message)
    if Path(transaction).name != transaction or transaction in {"", ".", ".."}:
        raise ProfiledPackagePromotionError(profile.invalid_transaction_message)
    path = root / transaction
    if not path.is_dir():
        raise ProfiledPackagePromotionError(f"{profile.transaction_not_found_message}: {transaction}")
    return path


def _artifact_path(
    profile: ProfiledPackagePromotionProfile,
    repo: Path,
    report: Mapping[str, Any],
    key: str,
) -> Path:
    raw = str((report.get("artifacts") or {}).get(key) or "")
    if not raw:
        raise ProfiledPackagePromotionError(f"Fresh rehearsal did not publish {key}.")
    path = Path(raw)
    return path.resolve() if path.is_absolute() else (repo / path).resolve()


def _resolve_under(repo: Path, path: Path | None) -> Path:
    if path is None:
        raise ProfiledPackagePromotionError("A required path was not provided.")
    return path.resolve() if path.is_absolute() else (repo / path).resolve()


def _transaction_id(source_binding: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    digest = source_binding.removeprefix("sha256:")[:12]
    return f"{stamp}-{digest}-{uuid4().hex[:8]}"


def _write_transaction_state(
    profile: ProfiledPackagePromotionProfile,
    directory: Path,
    phase: str,
    plan: Mapping[str, Any],
    *,
    committed: bool,
    error: str | None = None,
    rollback_error: str | None = None,
) -> None:
    payload = {
        "schema": profile.transaction_schema,
        "version": profile.report_version,
        "appId": profile.app_id,
        "transactionId": directory.name,
        "phase": phase,
        "committed": committed,
        "semanticFingerprint": plan.get("semanticFingerprint"),
        "candidateSourceBindingFingerprint": plan.get("sourceBindingFingerprint"),
        "updatedUtc": _utc_now(),
    }
    if error:
        payload["error"] = error
    if rollback_error:
        payload["rollbackError"] = rollback_error
    _write_json(directory / "transaction-state.json", payload)


def write_profiled_package_promotion_execution_report(
    profile: ProfiledPackagePromotionProfile,
    output: Path,
    report: Mapping[str, Any],
    diagnostics: Sequence[Mapping[str, Any]],
    *,
    filename: str | None = None,
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    payload = dict(report)
    payload["diagnosticCount"] = len(diagnostics)
    payload["diagnostics"] = [dict(item) for item in diagnostics]
    report_filename = filename or profile.report_filename
    _write_json(output / f"{report_filename}.json", payload)
    rollback = "rollback" in report_filename
    title = profile.rollback_report_title if rollback else profile.report_title
    lines = [
        f"# {title}",
        "",
        f"- Status: `{payload.get('status')}`",
        f"- Transaction: `{payload.get('transactionId')}`",
        f"- Source authority: `{payload.get('sourceAuthority')}`",
        f"- Promotion executed: `{str(payload.get('promotionExecuted')).lower()}`",
        f"- Rollback available: `{str(payload.get('rollbackAvailable')).lower()}`",
        "",
    ]
    (output / f"{report_filename}.md").write_text("\n".join(lines), encoding="utf-8")



def _snapshot_changes(before: Mapping[str, str], after: Mapping[str, str]) -> list[str]:
    return [
        path
        for path in sorted(set(before) | set(after))
        if before.get(path) != after.get(path)
    ]


def _load_json(
    path: Path,
    label: str,
    profile: ProfiledPackagePromotionProfile,
) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfiledPackagePromotionError(f"Could not load {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProfiledPackagePromotionError(f"{label} must be a JSON object.")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value) + b"\n")


def _read_json(path: Path, profile: ProfiledPackagePromotionProfile) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ProfiledPackagePromotionError(f"Expected JSON object: {path}")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _diagnostic(
    profile: ProfiledPackagePromotionProfile,
    code: str,
    summary: str,
    semantic_path: str,
) -> dict[str, Any]:
    return {
        "schema": profile.diagnostic_schema,
        "code": code,
        "severity": "error",
        "blocking": True,
        "stage": profile.stage_name,
        "semanticPath": semantic_path,
        "summary": summary,
        "problem": summary,
    }
