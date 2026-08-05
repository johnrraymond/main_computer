"""Transactional Contract Workbench DSL authority promotion and guarded rollback.

Wave 13 executes the exact generic Wave 12 promotion plan against the live
repository.  It creates a durable protected-source backup, applies the
rehearsed payload atomically, reruns the full promoted Workbench authority
chain, and automatically restores the pre-promotion boundary on any failure.
A committed transaction remains rollback-capable while protected MCEL sources
still match the recorded post-promotion snapshot.
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
from main_computer.mcel_workbench_candidate_projection import (
    APP_ID,
    DEFAULT_FIXTURE_IR,
    DEFAULT_PACKAGE_ROOT,
    PROJECTION_PROFILE,
)
from main_computer.mcel_workbench_promotion_rehearsal import (
    DEFAULT_REPORT_ROOT as DEFAULT_REHEARSAL_REPORT_ROOT,
    REPOSITORY_ROOT,
    _display,
    _protected_source_snapshot,
    _run_promoted_authorities,
    _sha,
    _tree_snapshot,
    _verify_generated_ownership,
    _workspace_fingerprints,
    rehearse_workbench_promotion,
)

REPORT_SCHEMA = "mcel.application-promotion-execution-report.v1"
REPORT_VERSION = "mcel-workbench-promotion-wave13"
TRANSACTION_SCHEMA = "mcel.application-promotion-transaction.v1"
ROLLBACK_SCHEMA = "mcel.application-promotion-rollback-result.v1"
DEFAULT_TRANSACTION_ROOT = Path("runtime/state/mcel/application-promotions/contract-workbench")
DEFAULT_REPORT_ROOT = Path("runtime/reports/mcel-application-promotions/contract-workbench")
DEFAULT_LOCK_PATH = Path("runtime/state/mcel/application-promotion.contract-workbench.lock")


class WorkbenchPromotionError(RuntimeError):
    """Raised when Workbench promotion or rollback cannot complete truthfully."""


@dataclass(frozen=True)
class WorkbenchPromotionResult:
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


def execute_workbench_promotion(
    *,
    repo_root: Path = REPOSITORY_ROOT,
    fixture_ir_path: Path = DEFAULT_FIXTURE_IR,
    transaction_root: Path = DEFAULT_TRANSACTION_ROOT,
    report_root: Path = DEFAULT_REPORT_ROOT,
    rehearsal_report_root: Path = DEFAULT_REHEARSAL_REPORT_ROOT,
    headed: bool = False,
    write_report: bool = True,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    rehearsal_runner: Callable[..., Any] | None = None,
    failure_injector: Callable[[str], None] | None = None,
) -> WorkbenchPromotionResult:
    """Execute a fresh, rehearsed Workbench authority transition."""

    repo = repo_root.resolve()
    diagnostics: list[Mapping[str, Any]] = []
    live_package = repo / DEFAULT_PACKAGE_ROOT
    lock_path = _resolve_under(repo, DEFAULT_LOCK_PATH)
    tx_base = _resolve_under(repo, transaction_root)
    report_base = _resolve_under(repo, report_root)
    runner = command_runner or subprocess.run

    try:
        with _promotion_lock(lock_path):
            existing = _authoring_status(live_package)
            if existing == "dsl-authoritative":
                raise WorkbenchPromotionError(
                    "Contract Workbench already declares mcel.dsl.v1 authority; no second promotion was executed."
                )
            if existing not in {None, "legacy-explicit", "legacy-explicit-package", "semantic-runtime-proven"}:
                raise WorkbenchPromotionError(
                    f"Unsupported Workbench source authority before promotion: {existing}"
                )

            rehearse = rehearsal_runner or rehearse_workbench_promotion
            rehearsal = rehearse(
                repo_root=repo,
                fixture_ir_path=fixture_ir_path,
                report_root=rehearsal_report_root,
                headed=headed,
                write_report=True,
                command_runner=command_runner,
            )
            rehearsal_payload = (
                rehearsal.to_dict() if hasattr(rehearsal, "to_dict") else dict(rehearsal)
            )
            if not bool(getattr(rehearsal, "valid", rehearsal_payload.get("valid"))):
                raise WorkbenchPromotionError("Fresh Wave 12 Workbench rehearsal did not pass.")
            if not rehearsal_payload.get("promotionEligible"):
                raise WorkbenchPromotionError(
                    "Fresh Wave 12 Workbench rehearsal did not authorize promotion eligibility."
                )
            if rehearsal_payload.get("rollbackRestoration") != "exact":
                raise WorkbenchPromotionError(
                    "Fresh Wave 12 Workbench rehearsal did not prove exact rollback restoration."
                )

            plan = dict(rehearsal_payload.get("plan") or {})
            _validate_plan(plan)
            promotion_root = _artifact_path(repo, rehearsal_payload, "promotionMaterial")
            promoted = _load_promoted_payload(plan, promotion_root)
            _verify_plan_preconditions(repo, plan, promoted)

            transaction_id = _transaction_id(str(plan["sourceBindingFingerprint"]))
            transaction_directory = tx_base / transaction_id
            output_directory = report_base / transaction_id
            if transaction_directory.exists():
                raise WorkbenchPromotionError(
                    f"Workbench promotion transaction already exists: {transaction_id}"
                )

            protected_before = _protected_source_snapshot(repo)
            package_before = _tree_snapshot(live_package)
            before_fingerprints = _workspace_fingerprints(repo)
            _prepare_transaction(
                repo=repo,
                transaction_directory=transaction_directory,
                plan=plan,
                promoted=promoted,
                protected_before=protected_before,
                before_fingerprints=before_fingerprints,
                package_before=package_before,
            )
            _write_transaction_state(transaction_directory, "prepared", plan, committed=False)
            if failure_injector:
                failure_injector("prepared")

            try:
                _apply_transaction_payload(repo, plan, promoted, transaction_id)
                _write_transaction_state(transaction_directory, "applied", plan, committed=False)
                if failure_injector:
                    failure_injector("applied")

                ownership_exact = _verify_generated_ownership(live_package)
                live_dsl = live_package / "application.js"
                fixture = _resolve_under(repo, fixture_ir_path)
                compiled = compile_dsl_application(live_dsl, compare_ir_path=fixture)
                if (
                    not compiled.valid
                    or compiled.normalized_ir is None
                    or compiled.comparison_status != "exact"
                ):
                    raise WorkbenchPromotionError(
                        "Promoted authoritative Workbench DSL failed exact compilation."
                    )

                _run_promoted_authorities(repo, repo, headed, runner)
                acceptance = _load_json(
                    repo
                    / "runtime/reports/mcel-acceptance/apps/contract-workbench/mcel-acceptance-report.json",
                    "post-promotion Workbench acceptance report",
                )
                observation = _load_json(
                    repo
                    / "runtime/reports/mcel-observation/apps/contract-workbench/mcel-operation-observation-report.json",
                    "post-promotion Workbench observation report",
                )
                proof = _load_json(
                    repo
                    / "runtime/reports/mcel-app-proof/apps/contract-workbench/mcel-app-proof-report.json",
                    "post-promotion Workbench application proof",
                )
                intent = proof.get("intentCoverage") or {}
                effects = intent.get("effectAccounting") or {}
                capabilities = intent.get("capabilityAccounting") or {}
                after_fingerprints = _workspace_fingerprints(repo)

                checks = {
                    "sourceAuthority": _authoring_status(live_package) == "dsl-authoritative",
                    "generatedOwnership": ownership_exact is True,
                    "dslCompilation": compiled.valid and compiled.comparison_status == "exact",
                    "semanticIdentity": compiled.semantic_fingerprint == plan.get("semanticFingerprint"),
                    "packageValidation": bool(after_fingerprints.get("packageValid")),
                    "acceptance": acceptance.get("status") == "pass" and acceptance.get("passed") is True,
                    "browserObservation": observation.get("status") == "pass" and observation.get("ok") is True,
                    "applicationProof": proof.get("status") == "pass"
                    and proof.get("truthStatus") == "semantic-runtime-proven",
                    "repositoryBinding": (proof.get("stages") or {})
                    .get("repositoryBinding", {})
                    .get("status")
                    == "exact",
                    "intentCompleteness": intent.get("status") == "ir-native"
                    and intent.get("coveredIntentCount") == 7
                    and intent.get("declaredIntentCount") == 7,
                    "scenarioCompleteness": intent.get("observedScenarioCount") == 14
                    and intent.get("declaredScenarioCount") == 14,
                    "effectAccounting": effects.get("status") == "closed"
                    and effects.get("declaredEffectCount") == 18
                    and effects.get("closedEffectCount") == 18,
                    "capabilityAccounting": capabilities.get("status") == "closed"
                    and capabilities.get("declaredCapabilityCount") == 1
                    and capabilities.get("streamedOperationCount") == 1
                    and capabilities.get("cancellableOperationCount") == 1,
                }
                failed = [name for name, passed in checks.items() if not passed]
                if failed:
                    raise WorkbenchPromotionError(
                        "Post-promotion Workbench authority checks failed: " + ", ".join(failed)
                    )
                if failure_injector:
                    failure_injector("verified")

                protected_after = _protected_source_snapshot(repo)
                _write_json(transaction_directory / "protected-after.json", protected_after)
                transaction_report = {
                    "schema": REPORT_SCHEMA,
                    "version": REPORT_VERSION,
                    "appId": APP_ID,
                    "valid": True,
                    "status": "pass",
                    "transactionId": transaction_id,
                    "promotionExecuted": True,
                    "candidatePromoted": True,
                    "sourceAuthorityBefore": "legacy-explicit-package",
                    "sourceAuthority": "mcel.dsl.v1",
                    "derivedArtifactAuthority": PROJECTION_PROFILE,
                    "legacyPackageAuthority": "retired",
                    "truthStatus": "semantic-runtime-proven",
                    "semanticFingerprint": compiled.semantic_fingerprint,
                    "candidateSourceBindingFingerprint": plan.get("sourceBindingFingerprint"),
                    "liveSourceBindingFingerprint": compiled.source_binding_fingerprint,
                    "rollbackAvailable": True,
                    "rollbackTransaction": transaction_id,
                    "automaticRollbackPerformed": False,
                    "checks": {name: {"status": "pass"} for name in checks},
                    "fingerprints": {
                        "before": before_fingerprints,
                        "after": after_fingerprints,
                    },
                    "intentCoverage": {
                        "declaredIntents": 7,
                        "coveredIntents": 7,
                        "declaredScenarios": 14,
                        "observedScenarios": 14,
                    },
                    "effectAccounting": effects,
                    "capabilityAccounting": capabilities,
                    "authority": {
                        "liveApplicationChanged": True,
                        "promotionExecuted": True,
                        "candidatePromoted": True,
                        "sourceAuthority": "mcel.dsl.v1",
                        "derivedArtifactAuthority": PROJECTION_PROFILE,
                        "legacyPackageAuthority": "retired",
                        "rollbackAvailable": True,
                    },
                    "artifacts": {
                        "transaction": _display(transaction_directory, repo),
                        "rollback": _display(transaction_directory / "protected-before", repo),
                    },
                }
                _write_transaction_state(transaction_directory, "committed", plan, committed=True)
                _write_json(transaction_directory / "promotion-result.json", transaction_report)
                if write_report:
                    _write_execution_report(output_directory, transaction_report, diagnostics)
                return WorkbenchPromotionResult(
                    True,
                    "pass",
                    transaction_report,
                    tuple(diagnostics),
                    output_directory if write_report else None,
                )
            except Exception as exc:
                rollback_error: str | None = None
                try:
                    _restore_protected_snapshot(repo, transaction_directory)
                    restored = (
                        _protected_source_snapshot(repo) == protected_before
                        and _tree_snapshot(live_package) == package_before
                    )
                    if not restored:
                        rollback_error = (
                            "Automatic rollback did not restore the protected Workbench source boundary exactly."
                        )
                except Exception as rollback_exc:  # pragma: no cover - catastrophic path
                    rollback_error = str(rollback_exc)
                _write_transaction_state(
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
                raise WorkbenchPromotionError(message) from exc
    except (WorkbenchPromotionError, OSError, ValueError, json.JSONDecodeError) as exc:
        diagnostics.append(
            _diagnostic("MCEL_WORKBENCH_PROMOTION_FAILED", str(exc), "$promotion")
        )
        report = {
            "schema": REPORT_SCHEMA,
            "version": REPORT_VERSION,
            "appId": APP_ID,
            "valid": False,
            "status": "fail",
            "promotionExecuted": False,
            "candidatePromoted": False,
            "sourceAuthority": _reported_source_authority(live_package),
            "rollbackAvailable": False,
            "automaticRollbackPerformed": "rollback" in str(exc).lower(),
            "authority": {
                "promotionExecuted": False,
                "candidatePromoted": False,
                "sourceAuthority": _reported_source_authority(live_package),
            },
        }
        return WorkbenchPromotionResult(False, "fail", report, tuple(diagnostics))


def rollback_workbench_promotion(
    transaction: str,
    *,
    repo_root: Path = REPOSITORY_ROOT,
    transaction_root: Path = DEFAULT_TRANSACTION_ROOT,
    report_root: Path = DEFAULT_REPORT_ROOT,
    write_report: bool = True,
) -> WorkbenchPromotionResult:
    """Roll back a committed Workbench authority transition if no protected drift exists."""

    repo = repo_root.resolve()
    diagnostics: list[Mapping[str, Any]] = []
    tx_base = _resolve_under(repo, transaction_root)
    report_base = _resolve_under(repo, report_root)
    lock_path = _resolve_under(repo, DEFAULT_LOCK_PATH)
    try:
        with _promotion_lock(lock_path):
            tx = _resolve_transaction(tx_base, transaction)
            state = _read_json(tx / "transaction-state.json")
            if state.get("phase") != "committed" or not state.get("committed"):
                raise WorkbenchPromotionError(
                    "Only a committed Workbench promotion transaction can be rolled back."
                )
            expected_after = _read_json(tx / "protected-after.json")
            current = _protected_source_snapshot(repo)
            drift = _snapshot_changes(expected_after, current)
            if drift:
                raise WorkbenchPromotionError(
                    "Protected MCEL source drift blocks Workbench rollback: " + ", ".join(drift)
                )

            _restore_protected_snapshot(repo, tx)
            expected_before = _read_json(tx / "protected-before.json")
            restored = _protected_source_snapshot(repo)
            drift_after = _snapshot_changes(expected_before, restored)
            if drift_after:
                raise WorkbenchPromotionError(
                    "Workbench rollback restoration is not exact: " + ", ".join(drift_after)
                )
            expected_package = _read_json(tx / "package-before.json")
            actual_package = _tree_snapshot(repo / DEFAULT_PACKAGE_ROOT)
            package_drift = _snapshot_changes(expected_package, actual_package)
            if package_drift:
                raise WorkbenchPromotionError(
                    "Workbench package rollback restoration is not exact: "
                    + ", ".join(package_drift)
                )

            expected_fingerprints = _read_json(tx / "fingerprints-before.json")
            restored_fingerprints = _workspace_fingerprints(repo)
            fingerprint_keys = ("package", "catalog", "runtimeProjection")
            fingerprint_drift = [
                key
                for key in fingerprint_keys
                if expected_fingerprints.get(key) != restored_fingerprints.get(key)
            ]
            if fingerprint_drift:
                raise WorkbenchPromotionError(
                    "Workbench rollback fingerprint restoration is not exact: "
                    + ", ".join(fingerprint_drift)
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
                "schema": ROLLBACK_SCHEMA,
                "version": REPORT_VERSION,
                "appId": APP_ID,
                "valid": True,
                "status": "pass",
                "transactionId": tx.name,
                "rollbackExecuted": True,
                "restoration": "exact",
                "sourceAuthority": "legacy-explicit-package",
                "promotionActive": False,
                "fingerprints": restored_fingerprints,
            }
            _write_json(tx / "rollback-result.json", report)
            output = report_base / tx.name / "rollback"
            if write_report:
                _write_execution_report(
                    output,
                    report,
                    diagnostics,
                    filename="mcel-workbench-promotion-rollback-report",
                )
            return WorkbenchPromotionResult(
                True,
                "pass",
                report,
                tuple(diagnostics),
                output if write_report else None,
            )
    except (WorkbenchPromotionError, OSError, ValueError, json.JSONDecodeError) as exc:
        diagnostics.append(
            _diagnostic(
                "MCEL_WORKBENCH_PROMOTION_ROLLBACK_FAILED", str(exc), "$rollback"
            )
        )
        report = {
            "schema": ROLLBACK_SCHEMA,
            "version": REPORT_VERSION,
            "appId": APP_ID,
            "valid": False,
            "status": "fail",
            "rollbackExecuted": False,
            "restoration": "blocked",
        }
        return WorkbenchPromotionResult(False, "fail", report, tuple(diagnostics))


def _validate_plan(plan: Mapping[str, Any]) -> None:
    if plan.get("schema") != "mcel.application-promotion-plan.v1":
        raise WorkbenchPromotionError("Fresh rehearsal returned an unknown promotion-plan schema.")
    if plan.get("appId") != APP_ID:
        raise WorkbenchPromotionError("Fresh rehearsal returned a plan for another application.")
    if plan.get("sourceAuthorityBefore") != "legacy-explicit-package":
        raise WorkbenchPromotionError(
            "Workbench promotion plan does not begin at legacy explicit-package authority."
        )
    if plan.get("sourceAuthorityAfter") != "mcel.dsl.v1":
        raise WorkbenchPromotionError(
            "Workbench promotion plan does not establish mcel.dsl.v1 authority."
        )
    if plan.get("derivedArtifactAuthorityAfter") != PROJECTION_PROFILE:
        raise WorkbenchPromotionError(
            "Workbench promotion plan does not establish the portable IR projection authority."
        )
    evidence = plan.get("candidateEvidenceBinding") or {}
    if (
        evidence.get("truthStatus") != "semantic-runtime-proven"
        or evidence.get("evidenceReused") is not False
    ):
        raise WorkbenchPromotionError(
            "Workbench promotion plan is not bound to fresh semantic-runtime-proven candidate evidence."
        )
    files = plan.get("files")
    if not isinstance(files, list) or not files:
        raise WorkbenchPromotionError("Workbench promotion plan contains no file transitions.")


def _load_promoted_payload(
    plan: Mapping[str, Any], promotion_root: Path
) -> dict[str, bytes]:
    promoted: dict[str, bytes] = {}
    for item in plan["files"]:
        relative = str(item["path"])
        path = promotion_root / relative
        if not path.is_file():
            raise WorkbenchPromotionError(f"Workbench promotion payload is missing: {relative}")
        content = path.read_bytes()
        if _sha(content) != item.get("afterSha256"):
            raise WorkbenchPromotionError(
                f"Workbench promotion payload hash mismatch: {relative}"
            )
        promoted[relative] = content
    return promoted


def _verify_plan_preconditions(
    repo: Path, plan: Mapping[str, Any], promoted: Mapping[str, bytes]
) -> None:
    for item in plan["files"]:
        relative = str(item["path"])
        current = repo / relative
        before = current.read_bytes() if current.is_file() else None
        if _sha(before) != item.get("beforeSha256"):
            raise WorkbenchPromotionError(
                f"Live before-hash drift blocks Workbench promotion: {relative}"
            )
        if _sha(promoted[relative]) != item.get("afterSha256"):
            raise WorkbenchPromotionError(
                f"Staged after-hash mismatch blocks Workbench promotion: {relative}"
            )


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
                raise WorkbenchPromotionError(
                    f"Live repository drift appeared during Workbench promotion: {relative}"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(
                f".{target.name}.mcel-promote-{transaction_id}.tmp"
            )
            temporary.write_bytes(promoted[relative])
            if _sha(temporary.read_bytes()) != item.get("afterSha256"):
                raise WorkbenchPromotionError(
                    f"Temporary Workbench promotion file failed hash verification: {relative}"
                )
            staged.append((temporary, target, relative))
        for temporary, target, relative in staged:
            os.replace(temporary, target)
            expected = next(
                item.get("afterSha256")
                for item in plan["files"]
                if item["path"] == relative
            )
            if _sha(target.read_bytes()) != expected:
                raise WorkbenchPromotionError(
                    f"Applied Workbench promotion file failed hash verification: {relative}"
                )
    finally:
        for temporary, _target, _relative in staged:
            if temporary.exists():
                temporary.unlink()


def _restore_protected_snapshot(repo: Path, transaction_directory: Path) -> None:
    before = _read_json(transaction_directory / "protected-before.json")
    current = _protected_source_snapshot(repo)
    for relative in sorted(set(current) - set(before), reverse=True):
        path = repo / relative
        if path.is_file():
            path.unlink()
    backup = transaction_directory / "protected-before"
    for relative, digest in before.items():
        source = backup / relative
        if not source.is_file() or _sha(source.read_bytes()) != digest:
            raise WorkbenchPromotionError(
                f"Workbench rollback backup integrity failed: {relative}"
            )
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
    return (
        str(authoring.get("status"))
        if isinstance(authoring, Mapping) and authoring.get("status")
        else None
    )


def _reported_source_authority(package: Path) -> str:
    return "mcel.dsl.v1" if _authoring_status(package) == "dsl-authoritative" else "legacy-explicit-package"


@contextmanager
def _promotion_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise WorkbenchPromotionError(
            f"Another Workbench promotion operation holds the lock: {path}"
        ) from exc
    try:
        payload = {
            "schema": "mcel.application-promotion-lock.v1",
            "appId": APP_ID,
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


def _resolve_transaction(root: Path, transaction: str) -> Path:
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
                state = _read_json(state_path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if state.get("phase") == "committed" and state.get("committed") is True:
                return candidate
        raise WorkbenchPromotionError(
            "No committed Workbench promotion transaction exists."
        )
    if Path(transaction).name != transaction or transaction in {"", ".", ".."}:
        raise WorkbenchPromotionError(
            "Invalid Workbench promotion transaction identifier."
        )
    path = root / transaction
    if not path.is_dir():
        raise WorkbenchPromotionError(
            f"Workbench promotion transaction not found: {transaction}"
        )
    return path


def _artifact_path(repo: Path, report: Mapping[str, Any], key: str) -> Path:
    raw = str((report.get("artifacts") or {}).get(key) or "")
    if not raw:
        raise WorkbenchPromotionError(f"Fresh Workbench rehearsal did not publish {key}.")
    path = Path(raw)
    return path.resolve() if path.is_absolute() else (repo / path).resolve()


def _resolve_under(repo: Path, path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (repo / path).resolve()


def _transaction_id(source_binding: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    digest = source_binding.removeprefix("sha256:")[:12]
    return f"{stamp}-{digest}-{uuid4().hex[:8]}"


def _write_transaction_state(
    directory: Path,
    phase: str,
    plan: Mapping[str, Any],
    *,
    committed: bool,
    error: str | None = None,
    rollback_error: str | None = None,
) -> None:
    payload = {
        "schema": TRANSACTION_SCHEMA,
        "version": REPORT_VERSION,
        "appId": APP_ID,
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


def _write_execution_report(
    output: Path,
    report: Mapping[str, Any],
    diagnostics: Sequence[Mapping[str, Any]],
    *,
    filename: str = "mcel-workbench-promotion-report",
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    payload = dict(report)
    payload["diagnosticCount"] = len(diagnostics)
    payload["diagnostics"] = [dict(item) for item in diagnostics]
    _write_json(output / f"{filename}.json", payload)
    rollback = "rollback" in filename
    lines = [
        "# Contract Workbench Authority Rollback" if rollback else "# Contract Workbench Authority Promotion",
        "",
        f"- Status: `{payload.get('status')}`",
        f"- Transaction: `{payload.get('transactionId')}`",
        f"- Source authority: `{payload.get('sourceAuthority')}`",
        f"- Promotion executed: `{str(payload.get('promotionExecuted')).lower()}`",
        f"- Rollback available: `{str(payload.get('rollbackAvailable')).lower()}`",
        "",
    ]
    (output / f"{filename}.md").write_text("\n".join(lines), encoding="utf-8")


def _snapshot_changes(before: Mapping[str, str], after: Mapping[str, str]) -> list[str]:
    return [
        path
        for path in sorted(set(before) | set(after))
        if before.get(path) != after.get(path)
    ]


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkbenchPromotionError(f"Could not load {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkbenchPromotionError(f"{label} must be a JSON object.")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value) + b"\n")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise WorkbenchPromotionError(f"Expected JSON object: {path}")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _diagnostic(code: str, summary: str, semantic_path: str) -> dict[str, Any]:
    return {
        "schema": "mcel.compiler-diagnostic.v1",
        "code": code,
        "severity": "error",
        "blocking": True,
        "stage": "promotion-execution",
        "semanticPath": semantic_path,
        "summary": summary,
        "problem": summary,
    }
