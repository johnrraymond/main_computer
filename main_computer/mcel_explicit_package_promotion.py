"""Generic transactional promotion execution for explicit-package MCEL apps.

Explicit-package promotion execution consumes a successful promotion rehearsal,
applies the exact rehearsed payload to the live repository under a durable
transaction directory, reruns app authorities, and preserves an exact rollback
boundary.  App-specific wrappers supply profile facts and hooks; this module owns
the transaction/apply/rollback mechanics.
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

from main_computer.mcel_application_ir import canonical_json_bytes, compare_application_ir
from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_explicit_package_candidate_evidence import (
    _load_json,
    _run_candidate_authorities,
    _run_command,
)
from main_computer.mcel_explicit_package_promotion_rehearsal import (
    ExplicitPackagePromotionRehearsalProfile,
    _display_path,
    _promotion_authority_source_snapshot,
    _sha,
    _snapshot_changes,
    _tree_snapshot,
    verify_explicit_package_promoted_ownership,
    workspace_fingerprints,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRANSACTION_ROOT = Path("runtime/state/mcel/explicit-package-promotions")
DEFAULT_REPORT_ROOT = Path("runtime/reports/mcel-explicit-package-promotions")
DEFAULT_LOCK_PATH = Path("runtime/state/mcel/explicit-package-promotion.lock")
REPORT_SCHEMA = "mcel.explicit-package-promotion-execution-report.v1"
REPORT_VERSION = "mcel-explicit-package-promotion-v1"
TRANSACTION_SCHEMA = "mcel.explicit-package-promotion-transaction.v1"
ROLLBACK_SCHEMA = "mcel.explicit-package-promotion-rollback-result.v1"


class ExplicitPackagePromotionError(RuntimeError):
    """Raised when promotion or rollback cannot complete truthfully."""


@dataclass(frozen=True)
class ExplicitPackagePromotionResult:
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
            payload.setdefault("artifacts", {})["outputDirectory"] = _display_path(
                self.output_directory, REPOSITORY_ROOT
            )
        return payload


@dataclass(frozen=True)
class ExplicitPackagePromotionProfile:
    app_id: str
    rehearsal_profile: ExplicitPackagePromotionRehearsalProfile
    run_rehearsal: Callable[..., Any]
    compare_representations: Callable[..., Any]
    import_package: Callable[[Path], Any]
    build_effect_accounting: Callable[..., Mapping[str, Any]]
    run_node_probe: Callable[[Path], Mapping[str, Any]]
    run_browser_probe: Callable[[Path, bool], Mapping[str, Any]]
    package_relative_path: str
    default_fixture_ir: Path | None
    default_transaction_root: Path = DEFAULT_TRANSACTION_ROOT
    default_report_root: Path = DEFAULT_REPORT_ROOT
    default_rehearsal_report_root: Path = Path("runtime/reports/mcel-compiler-candidates")
    default_lock_path: Path = DEFAULT_LOCK_PATH
    report_schema: str = REPORT_SCHEMA
    report_version: str = REPORT_VERSION
    transaction_schema: str = TRANSACTION_SCHEMA
    rollback_schema: str = ROLLBACK_SCHEMA
    report_filename: str = "mcel-promotion-report"
    rollback_report_filename: str = "mcel-promotion-rollback-report"
    report_title: str = "MCEL Authority Promotion"
    rollback_report_title: str = "MCEL Authority Rollback"
    live_authority_before: str = "legacy-explicit-package"
    allowed_authorities_before: tuple[str, ...] = ("legacy-explicit", "legacy-explicit-package")
    target_authoring_status: str = "dsl-authoritative"
    target_source_authority: str = "mcel.dsl.v1"
    derived_artifact_authority: str = "mcel.explicit-package-projection.v1"
    legacy_package_authority: str = "retired"
    truth_status: str = "semantic-runtime-proven"
    compatibility_report_root: Path | None = None
    promotion_failed_code: str = "MCEL_EXPLICIT_PACKAGE_PROMOTION_FAILED"
    rollback_failed_code: str = "MCEL_EXPLICIT_PACKAGE_PROMOTION_ROLLBACK_FAILED"
    promotion_lock_schema: str = "mcel.explicit-package-promotion-lock.v1"
    lock_held_message: str = "Another explicit-package promotion operation holds the lock"
    already_promoted_message: str = (
        "Application already declares DSL authority; no second promotion was executed."
    )
    unsupported_authority_message: str = "Unsupported source authority before promotion"
    rehearsal_failed_message: str = "Fresh promotion rehearsal did not pass."
    rehearsal_ineligible_message: str = "Fresh promotion rehearsal did not authorize promotion eligibility."
    rehearsal_rollback_message: str = "Fresh promotion rehearsal did not prove exact rollback restoration."
    invalid_plan_schema_message: str = "Fresh rehearsal returned an unknown promotion-plan schema."
    invalid_plan_start_message: str = "Promotion plan does not begin at legacy explicit-package authority."
    invalid_plan_end_message: str = "Promotion plan does not establish mcel.dsl.v1 authority."
    invalid_plan_evidence_message: str = (
        "Promotion plan is not bound to fresh semantic-runtime-proven candidate evidence."
    )
    empty_plan_message: str = "Promotion plan contains no file transition records."
    compatibility_failed_message: str = "Post-promotion compatibility is not exact."
    promoted_dsl_failed_message: str = "Promoted authoritative DSL failed compilation."
    authority_failed_message: str = "Post-promotion authority checks failed"
    automatic_rollback_failed_message: str = (
        "Automatic rollback did not restore the protected source boundary exactly."
    )
    committed_only_rollback_message: str = "Only a committed promotion transaction can be rolled back."
    rollback_drift_message: str = "Protected MCEL source drift blocks rollback"
    rollback_not_exact_message: str = "Rollback restoration is not exact"
    rollback_fingerprint_message: str = "Rollback fingerprint restoration is not exact"
    no_transaction_message: str = "No committed promotion transaction exists."
    invalid_transaction_message: str = "Invalid promotion transaction identifier."
    transaction_not_found_message: str = "Promotion transaction not found"
    stage_name: str = "promotion-execution"


def execute_explicit_package_promotion(
    profile: ExplicitPackagePromotionProfile,
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
    node_probe_runner: Callable[[Path], Mapping[str, Any]] | None = None,
    browser_probe_runner: Callable[[Path, bool], Mapping[str, Any]] | None = None,
    failure_injector: Callable[[str], None] | None = None,
) -> ExplicitPackagePromotionResult:
    repo = repo_root.resolve()
    diagnostics: list[Mapping[str, Any]] = []
    fixture = fixture_ir_path if fixture_ir_path is not None else profile.default_fixture_ir
    live_package = repo / profile.package_relative_path
    lock_path = _resolve_under(repo, profile.default_lock_path)
    tx_base = _resolve_under(repo, transaction_root or profile.default_transaction_root)
    report_base = _resolve_under(repo, report_root or profile.default_report_root)
    rehearsal_reports = rehearsal_report_root or profile.default_rehearsal_report_root
    runner = command_runner or _run_command

    try:
        with _promotion_lock(profile, lock_path):
            existing = _authoring_status(live_package)
            if existing == profile.target_authoring_status:
                raise ExplicitPackagePromotionError(profile.already_promoted_message)
            if existing is not None and existing not in profile.allowed_authorities_before:
                raise ExplicitPackagePromotionError(f"{profile.unsupported_authority_message}: {existing}")

            rehearse = rehearsal_runner or profile.run_rehearsal
            rehearsal = rehearse(
                repo_root=repo,
                fixture_ir_path=fixture,
                report_root=rehearsal_reports,
                headed=headed,
                write_report=True,
                command_runner=command_runner,
                node_probe_runner=node_probe_runner,
                browser_probe_runner=browser_probe_runner,
            )
            rehearsal_payload = rehearsal.to_dict() if hasattr(rehearsal, "to_dict") else dict(rehearsal)
            if not bool(getattr(rehearsal, "valid", rehearsal_payload.get("valid"))):
                raise ExplicitPackagePromotionError(profile.rehearsal_failed_message)
            if not rehearsal_payload.get("promotionEligible"):
                raise ExplicitPackagePromotionError(profile.rehearsal_ineligible_message)
            if rehearsal_payload.get("rollbackRestoration") != "exact":
                raise ExplicitPackagePromotionError(profile.rehearsal_rollback_message)

            plan = dict(rehearsal_payload.get("plan") or {})
            _validate_plan(profile, plan)
            promotion_root = _artifact_path(profile, repo, rehearsal_payload, "promotionMaterial")
            promoted = _load_promoted_payload(profile, plan, promotion_root)
            _verify_plan_preconditions(profile, repo, plan, promoted)

            transaction_id = _transaction_id(str(plan["sourceBindingFingerprint"]))
            transaction_directory = tx_base / transaction_id
            output_directory = report_base / transaction_id
            if transaction_directory.exists():
                raise ExplicitPackagePromotionError(f"Promotion transaction already exists: {transaction_id}")

            protected_before = _promotion_authority_source_snapshot(repo, profile.rehearsal_profile)
            live_before = _tree_snapshot(live_package)
            before_fingerprints = workspace_fingerprints(repo, profile.rehearsal_profile)
            _prepare_transaction(
                repo=repo,
                transaction_directory=transaction_directory,
                plan=plan,
                promoted=promoted,
                protected_before=protected_before,
                before_fingerprints=before_fingerprints,
            )
            _write_transaction_state(profile, transaction_directory, "prepared", plan, committed=False)
            if failure_injector:
                failure_injector("prepared")

            try:
                _apply_transaction_payload(profile, repo, plan, promoted, transaction_id)
                _write_transaction_state(profile, transaction_directory, "applied", plan, committed=False)
                if failure_injector:
                    failure_injector("applied")

                verify_explicit_package_promoted_ownership(repo, profile.rehearsal_profile)
                live_dsl = live_package / "application.js"
                compatibility = profile.compare_representations(
                    package_root=live_package,
                    fixture_ir_path=fixture,
                    dsl_source_path=live_dsl,
                    write_report=True,
                    report_root=repo / (
                        profile.compatibility_report_root
                        or Path(f"runtime/reports/mcel-application-compatibility/apps/{profile.app_id}")
                    ),
                )
                if not compatibility.valid:
                    raise ExplicitPackagePromotionError(profile.compatibility_failed_message)

                _run_candidate_authorities(
                    repo=repo,
                    workspace=repo,
                    app_id=profile.app_id,
                    headed=headed,
                    command_runner=runner,
                )
                acceptance = _load_json(
                    repo / f"runtime/reports/mcel-acceptance/apps/{profile.app_id}/mcel-acceptance-report.json",
                    "post-promotion acceptance report",
                )
                observation = _load_json(
                    repo / f"runtime/reports/mcel-observation/apps/{profile.app_id}/mcel-operation-observation-report.json",
                    "post-promotion observation report",
                )
                proof = _load_json(
                    repo / f"runtime/reports/mcel-app-proof/apps/{profile.app_id}/mcel-app-proof-report.json",
                    "post-promotion application proof",
                )
                node_probe = dict((node_probe_runner or profile.run_node_probe)(repo))
                browser_probe = dict((browser_probe_runner or profile.run_browser_probe)(repo, headed))
                compiled = compile_dsl_application(live_dsl, compare_ir_path=fixture)
                if not compiled.valid or compiled.normalized_ir is None:
                    raise ExplicitPackagePromotionError(profile.promoted_dsl_failed_message)
                effects = profile.build_effect_accounting(
                    ir=compiled.normalized_ir,
                    acceptance=acceptance,
                    observation=observation,
                    node_probe=node_probe,
                    browser_probe=browser_probe,
                )
                imported = profile.import_package(live_package)
                semantic_comparison = (
                    compare_application_ir(compiled.normalized_ir, imported.normalized_ir)
                    if imported.valid and imported.normalized_ir is not None
                    else {"status": "invalid"}
                )
                after_fingerprints = workspace_fingerprints(repo, profile.rehearsal_profile)
                checks = {
                    "sourceAuthority": _authoring_status(live_package) == profile.target_authoring_status,
                    "generatedOwnership": True,
                    "compatibility": compatibility.status == "exact",
                    "packageValidation": bool(after_fingerprints.get("packageValid")),
                    "acceptance": acceptance.get("status") == "pass" and acceptance.get("passed") is True,
                    "browserObservation": observation.get("status") == "pass" and observation.get("ok") is True,
                    "effectAccounting": effects.get("status") == "closed" and effects.get("valid") is True,
                    "applicationProof": proof.get("status") == "pass" and proof.get("truthStatus") == profile.truth_status,
                    "repositoryBinding": (proof.get("stages") or {}).get("repositoryBinding", {}).get("status") == "exact",
                    "semanticRoundtrip": semantic_comparison.get("status") == "exact",
                    "semanticIdentity": compiled.semantic_fingerprint == plan.get("semanticFingerprint"),
                }
                failed = [name for name, passed in checks.items() if not passed]
                if failed:
                    raise ExplicitPackagePromotionError(profile.authority_failed_message + ": " + ", ".join(failed))
                if failure_injector:
                    failure_injector("verified")

                protected_after = _promotion_authority_source_snapshot(repo, profile.rehearsal_profile)
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
                    "derivedArtifactAuthority": profile.derived_artifact_authority,
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
                    "semanticComparison": semantic_comparison,
                    "effectAccounting": effects,
                    "authority": {
                        "liveApplicationChanged": True,
                        "promotionExecuted": True,
                        "candidatePromoted": True,
                        "sourceAuthority": profile.target_source_authority,
                        "derivedArtifactAuthority": profile.derived_artifact_authority,
                        "legacyPackageAuthority": profile.legacy_package_authority,
                        "rollbackAvailable": True,
                    },
                    "artifacts": {
                        "transaction": _display_path(transaction_directory, repo),
                        "rollback": _display_path(transaction_directory / "protected-before", repo),
                    },
                }
                _write_transaction_state(profile, transaction_directory, "committed", plan, committed=True)
                _write_json(transaction_directory / "promotion-result.json", transaction_report)
                if write_report:
                    write_explicit_package_promotion_execution_report(
                        profile,
                        output_directory,
                        transaction_report,
                        diagnostics,
                    )
                return ExplicitPackagePromotionResult(
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
                        _promotion_authority_source_snapshot(repo, profile.rehearsal_profile) == protected_before
                        and _tree_snapshot(live_package) == live_before
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
                raise ExplicitPackagePromotionError(message) from exc
    except (ExplicitPackagePromotionError, OSError, ValueError, json.JSONDecodeError) as exc:
        diagnostics.append(_diagnostic(profile, profile.promotion_failed_code, str(exc), "$promotion"))
        report = {
            "schema": profile.report_schema,
            "version": profile.report_version,
            "appId": profile.app_id,
            "valid": False,
            "status": "fail",
            "promotionExecuted": False,
            "candidatePromoted": False,
            "sourceAuthority": _authoring_status(live_package) or profile.live_authority_before,
            "rollbackAvailable": False,
            "automaticRollbackPerformed": "Automatic rollback" in str(exc) or "rollback" in str(exc).lower(),
            "authority": {
                "promotionExecuted": False,
                "candidatePromoted": False,
                "sourceAuthority": _authoring_status(live_package) or profile.live_authority_before,
            },
        }
        return ExplicitPackagePromotionResult(False, "fail", report, tuple(diagnostics))


def rollback_explicit_package_promotion(
    profile: ExplicitPackagePromotionProfile,
    transaction: str,
    *,
    repo_root: Path = REPOSITORY_ROOT,
    transaction_root: Path | None = None,
    report_root: Path | None = None,
    write_report: bool = True,
) -> ExplicitPackagePromotionResult:
    repo = repo_root.resolve()
    diagnostics: list[Mapping[str, Any]] = []
    tx_base = _resolve_under(repo, transaction_root or profile.default_transaction_root)
    report_base = _resolve_under(repo, report_root or profile.default_report_root)
    lock_path = _resolve_under(repo, profile.default_lock_path)
    live_package = repo / profile.package_relative_path
    try:
        with _promotion_lock(profile, lock_path):
            tx = _resolve_transaction(profile, tx_base, transaction)
            state = _read_json(tx / "transaction-state.json", profile)
            if state.get("phase") != "committed" or not state.get("committed"):
                raise ExplicitPackagePromotionError(profile.committed_only_rollback_message)
            expected_after = _read_json(tx / "protected-after.json", profile)
            current = _promotion_authority_source_snapshot(repo, profile.rehearsal_profile)
            drift = _snapshot_changes(expected_after, current)
            if drift:
                raise ExplicitPackagePromotionError(profile.rollback_drift_message + ": " + ", ".join(drift))
            _restore_protected_snapshot(profile, repo, tx)
            expected_before = _read_json(tx / "protected-before.json", profile)
            restored = _promotion_authority_source_snapshot(repo, profile.rehearsal_profile)
            drift_after = _snapshot_changes(expected_before, restored)
            if drift_after:
                raise ExplicitPackagePromotionError(profile.rollback_not_exact_message + ": " + ", ".join(drift_after))
            expected_fingerprints = _read_json(tx / "fingerprints-before.json", profile)
            restored_fingerprints = workspace_fingerprints(repo, profile.rehearsal_profile)
            fingerprint_keys = ("package", "catalog", "runtimeProjection", "semantic")
            fingerprint_drift = [
                key for key in fingerprint_keys
                if expected_fingerprints.get(key) != restored_fingerprints.get(key)
            ]
            if fingerprint_drift:
                raise ExplicitPackagePromotionError(profile.rollback_fingerprint_message + ": " + ", ".join(fingerprint_drift))
            state.update({
                "phase": "rolled-back",
                "committed": False,
                "rolledBackUtc": _utc_now(),
            })
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
                "sourceAuthority": _authoring_status(live_package) or profile.live_authority_before,
                "promotionActive": False,
                "fingerprints": restored_fingerprints,
            }
            _write_json(tx / "rollback-result.json", report)
            output = report_base / tx.name / "rollback"
            if write_report:
                write_explicit_package_promotion_execution_report(
                    profile,
                    output,
                    report,
                    diagnostics,
                    filename=profile.rollback_report_filename,
                )
            return ExplicitPackagePromotionResult(True, "pass", report, tuple(diagnostics), output if write_report else None)
    except (ExplicitPackagePromotionError, OSError, ValueError, json.JSONDecodeError) as exc:
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
        return ExplicitPackagePromotionResult(False, "fail", report, tuple(diagnostics))


def _validate_plan(profile: ExplicitPackagePromotionProfile, plan: Mapping[str, Any]) -> None:
    if plan.get("schema") != profile.rehearsal_profile.plan_schema:
        raise ExplicitPackagePromotionError(profile.invalid_plan_schema_message)
    if plan.get("sourceAuthorityBefore") != profile.live_authority_before:
        raise ExplicitPackagePromotionError(profile.invalid_plan_start_message)
    if plan.get("sourceAuthorityAfter") != profile.target_source_authority:
        raise ExplicitPackagePromotionError(profile.invalid_plan_end_message)
    evidence = plan.get("candidateEvidenceBinding") or {}
    if evidence.get("truthStatus") != profile.truth_status or evidence.get("evidenceReused") is not False:
        raise ExplicitPackagePromotionError(profile.invalid_plan_evidence_message)
    if not isinstance(plan.get("files"), list) or not plan["files"]:
        raise ExplicitPackagePromotionError(profile.empty_plan_message)


def _load_promoted_payload(
    profile: ExplicitPackagePromotionProfile,
    plan: Mapping[str, Any],
    promotion_root: Path,
) -> dict[str, bytes]:
    promoted: dict[str, bytes] = {}
    for item in plan["files"]:
        relative = str(item["path"])
        path = promotion_root / relative
        if not path.is_file():
            raise ExplicitPackagePromotionError(f"Promotion payload is missing: {relative}")
        content = path.read_bytes()
        if _sha(content) != item.get("afterSha256"):
            raise ExplicitPackagePromotionError(f"Promotion payload hash mismatch: {relative}")
        promoted[relative] = content
    return promoted


def _verify_plan_preconditions(
    profile: ExplicitPackagePromotionProfile,
    repo: Path,
    plan: Mapping[str, Any],
    promoted: Mapping[str, bytes],
) -> None:
    for item in plan["files"]:
        relative = str(item["path"])
        current = repo / relative
        before = current.read_bytes() if current.is_file() else None
        if _sha(before) != item.get("beforeSha256"):
            raise ExplicitPackagePromotionError(f"Live before-hash drift blocks promotion: {relative}")
        if _sha(promoted[relative]) != item.get("afterSha256"):
            raise ExplicitPackagePromotionError(f"Staged after-hash mismatch blocks promotion: {relative}")


def _prepare_transaction(
    *,
    repo: Path,
    transaction_directory: Path,
    plan: Mapping[str, Any],
    promoted: Mapping[str, bytes],
    protected_before: Mapping[str, str],
    before_fingerprints: Mapping[str, Any],
) -> None:
    transaction_directory.mkdir(parents=True, exist_ok=False)
    _write_json(transaction_directory / "promotion-plan.json", plan)
    _write_json(transaction_directory / "protected-before.json", protected_before)
    _write_json(transaction_directory / "fingerprints-before.json", before_fingerprints)
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
    profile: ExplicitPackagePromotionProfile,
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
                raise ExplicitPackagePromotionError(f"Live repository drift appeared during promotion: {relative}")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.mcel-promote-{transaction_id}.tmp")
            temporary.write_bytes(promoted[relative])
            if _sha(temporary.read_bytes()) != item.get("afterSha256"):
                raise ExplicitPackagePromotionError(f"Temporary promotion file failed hash verification: {relative}")
            staged.append((temporary, target, relative))
        for temporary, target, relative in staged:
            os.replace(temporary, target)
            if _sha(target.read_bytes()) != next(
                item.get("afterSha256") for item in plan["files"] if item["path"] == relative
            ):
                raise ExplicitPackagePromotionError(f"Applied promotion file failed hash verification: {relative}")
    finally:
        for temporary, _target, _relative in staged:
            if temporary.exists():
                temporary.unlink()


def _restore_protected_snapshot(
    profile: ExplicitPackagePromotionProfile,
    repo: Path,
    transaction_directory: Path,
) -> None:
    before = _read_json(transaction_directory / "protected-before.json", profile)
    current = _promotion_authority_source_snapshot(repo, profile.rehearsal_profile)
    for relative in sorted(set(current) - set(before), reverse=True):
        path = repo / relative
        if path.is_file():
            path.unlink()
    backup = transaction_directory / "protected-before"
    for relative, digest in before.items():
        source = backup / relative
        if not source.is_file() or _sha(source.read_bytes()) != digest:
            raise ExplicitPackagePromotionError(f"Rollback backup integrity failed: {relative}")
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


@contextmanager
def _promotion_lock(profile: ExplicitPackagePromotionProfile, path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ExplicitPackagePromotionError(f"{profile.lock_held_message}: {path}") from exc
    try:
        payload = {"schema": profile.promotion_lock_schema, "pid": os.getpid(), "createdUtc": _utc_now()}
        os.write(descriptor, canonical_json_bytes(payload) + b"\n")
        os.close(descriptor)
        yield
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _resolve_transaction(profile: ExplicitPackagePromotionProfile, root: Path, transaction: str) -> Path:
    if transaction == "latest":
        candidates = sorted(
            (path for path in root.iterdir() if path.is_dir()),
            key=lambda path: path.name,
            reverse=True,
        ) if root.is_dir() else []
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
        raise ExplicitPackagePromotionError(profile.no_transaction_message)
    if Path(transaction).name != transaction or transaction in {"", ".", ".."}:
        raise ExplicitPackagePromotionError(profile.invalid_transaction_message)
    path = root / transaction
    if not path.is_dir():
        raise ExplicitPackagePromotionError(f"{profile.transaction_not_found_message}: {transaction}")
    return path


def _artifact_path(
    profile: ExplicitPackagePromotionProfile,
    repo: Path,
    report: Mapping[str, Any],
    key: str,
) -> Path:
    raw = str((report.get("artifacts") or {}).get(key) or "")
    if not raw:
        raise ExplicitPackagePromotionError(f"Fresh rehearsal did not publish {key}.")
    path = Path(raw)
    return path.resolve() if path.is_absolute() else (repo / path).resolve()


def _resolve_under(repo: Path, path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (repo / path).resolve()


def _transaction_id(source_binding: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    digest = source_binding.removeprefix("sha256:")[:12]
    return f"{stamp}-{digest}-{uuid4().hex[:8]}"


def _write_transaction_state(
    profile: ExplicitPackagePromotionProfile,
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


def write_explicit_package_promotion_execution_report(
    profile: ExplicitPackagePromotionProfile,
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
    title = profile.rollback_report_title if "rollback" in report_filename else profile.report_title
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


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value) + b"\n")


def _read_json(path: Path, profile: ExplicitPackagePromotionProfile) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ExplicitPackagePromotionError(f"Expected JSON object: {path}")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _diagnostic(
    profile: ExplicitPackagePromotionProfile,
    code: str,
    summary: str,
    semantic_path: str,
) -> dict[str, Any]:
    return {
        "schema": "mcel.compiler-diagnostic.v1",
        "code": code,
        "severity": "error",
        "blocking": True,
        "stage": profile.stage_name,
        "semanticPath": semantic_path,
        "summary": summary,
        "problem": summary,
    }
