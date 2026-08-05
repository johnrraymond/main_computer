"""Transactional Counter DSL authority promotion and guarded rollback.

Wave 7 executes the exact Wave 6 promotion plan against the live repository.
It prepares a durable protected-source backup, applies the rehearsed payload
with sibling temporary files, reruns the complete Counter authority chain, and
automatically restores the pre-promotion protected boundary on any failure.
A committed transaction remains rollback-capable while the protected MCEL
source tree still matches the recorded post-promotion snapshot.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence
from uuid import uuid4

from main_computer.mcel_application_ir import canonical_json_bytes, compare_application_ir
from main_computer.mcel_counter_candidate_evidence import (
    _build_effect_accounting,
    _load_json,
    _run_browser_effect_probe,
    _run_candidate_authorities,
    _run_command,
    _run_counter_effect_probe,
)
from main_computer.mcel_counter_compatibility import (
    DEFAULT_FIXTURE_IR,
    compare_counter_representations,
)
from main_computer.mcel_counter_legacy_importer import import_counter_legacy_package
from main_computer.mcel_counter_promotion_rehearsal import (
    DEFAULT_REPORT_ROOT as DEFAULT_REHEARSAL_REPORT_ROOT,
    REPOSITORY_ROOT,
    _display_path,
    _promotion_authority_source_snapshot,
    _sha,
    _snapshot_changes,
    _tree_snapshot,
    _verify_promoted_ownership,
    _workspace_fingerprints,
    rehearse_counter_promotion,
)
from main_computer.mcel_dsl_compiler import compile_dsl_application

REPORT_SCHEMA = "mcel.counter-promotion-execution-report.v1"
REPORT_VERSION = "mcel-counter-promotion-wave7"
TRANSACTION_SCHEMA = "mcel.counter-promotion-transaction.v1"
ROLLBACK_SCHEMA = "mcel.counter-promotion-rollback-result.v1"
DEFAULT_TRANSACTION_ROOT = Path("runtime/state/mcel/counter-promotions")
DEFAULT_REPORT_ROOT = Path("runtime/reports/mcel-counter-promotions")
DEFAULT_LOCK_PATH = Path("runtime/state/mcel/counter-promotion.lock")


class CounterPromotionError(RuntimeError):
    """Raised when promotion or rollback cannot complete truthfully."""


@dataclass(frozen=True)
class CounterPromotionResult:
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


def execute_counter_promotion(
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
    node_probe_runner: Callable[[Path], Mapping[str, Any]] | None = None,
    browser_probe_runner: Callable[[Path, bool], Mapping[str, Any]] | None = None,
    failure_injector: Callable[[str], None] | None = None,
) -> CounterPromotionResult:
    repo = repo_root.resolve()
    diagnostics: list[Mapping[str, Any]] = []
    live_package = repo / "mcel_apps" / "contract-counter"
    lock_path = _resolve_under(repo, DEFAULT_LOCK_PATH)
    tx_base = _resolve_under(repo, transaction_root)
    report_base = _resolve_under(repo, report_root)
    runner = command_runner or _run_command

    try:
        with _promotion_lock(lock_path):
            existing = _authoring_status(live_package)
            if existing == "dsl-authoritative":
                raise CounterPromotionError(
                    "Counter already declares mcel.dsl.v1 authority; no second promotion was executed."
                )
            if existing not in {None, "legacy-explicit", "legacy-explicit-package"}:
                raise CounterPromotionError(f"Unsupported Counter source authority before promotion: {existing}")

            rehearse = rehearsal_runner or rehearse_counter_promotion
            rehearsal = rehearse(
                repo_root=repo,
                fixture_ir_path=fixture_ir_path,
                report_root=rehearsal_report_root,
                headed=headed,
                write_report=True,
                command_runner=command_runner,
                node_probe_runner=node_probe_runner,
                browser_probe_runner=browser_probe_runner,
            )
            rehearsal_payload = rehearsal.to_dict() if hasattr(rehearsal, "to_dict") else dict(rehearsal)
            if not bool(getattr(rehearsal, "valid", rehearsal_payload.get("valid"))):
                raise CounterPromotionError("Fresh Wave 6 rehearsal did not pass.")
            if not rehearsal_payload.get("promotionEligible"):
                raise CounterPromotionError("Fresh Wave 6 rehearsal did not authorize promotion eligibility.")
            if rehearsal_payload.get("rollbackRestoration") != "exact":
                raise CounterPromotionError("Fresh Wave 6 rehearsal did not prove exact rollback restoration.")

            plan = dict(rehearsal_payload.get("plan") or {})
            _validate_plan(plan)
            promotion_root = _artifact_path(repo, rehearsal_payload, "promotionMaterial")
            promoted = _load_promoted_payload(plan, promotion_root)
            _verify_plan_preconditions(repo, plan, promoted)

            transaction_id = _transaction_id(str(plan["sourceBindingFingerprint"]))
            transaction_directory = tx_base / transaction_id
            output_directory = report_base / transaction_id
            if transaction_directory.exists():
                raise CounterPromotionError(f"Promotion transaction already exists: {transaction_id}")

            protected_before = _promotion_authority_source_snapshot(repo)
            live_before = _tree_snapshot(live_package)
            before_fingerprints = _workspace_fingerprints(repo)
            _prepare_transaction(
                repo=repo,
                transaction_directory=transaction_directory,
                plan=plan,
                promoted=promoted,
                protected_before=protected_before,
                before_fingerprints=before_fingerprints,
            )
            _write_transaction_state(transaction_directory, "prepared", plan, committed=False)
            if failure_injector:
                failure_injector("prepared")

            try:
                _apply_transaction_payload(repo, plan, promoted, transaction_id)
                _write_transaction_state(transaction_directory, "applied", plan, committed=False)
                if failure_injector:
                    failure_injector("applied")

                _verify_promoted_ownership(repo)
                live_dsl = repo / "mcel_apps" / "contract-counter" / "application.js"
                compatibility = compare_counter_representations(
                    package_root=live_package,
                    fixture_ir_path=fixture_ir_path,
                    dsl_source_path=live_dsl,
                    write_report=True,
                    report_root=repo / "runtime/reports/mcel-application-compatibility/apps/contract-counter",
                )
                if not compatibility.valid:
                    raise CounterPromotionError("Post-promotion three-way compatibility is not exact.")

                _run_candidate_authorities(
                    repo=repo,
                    workspace=repo,
                    headed=headed,
                    command_runner=runner,
                )
                acceptance = _load_json(
                    repo / "runtime/reports/mcel-acceptance/apps/contract-counter/mcel-acceptance-report.json",
                    "post-promotion acceptance report",
                )
                observation = _load_json(
                    repo / "runtime/reports/mcel-observation/apps/contract-counter/mcel-operation-observation-report.json",
                    "post-promotion observation report",
                )
                proof = _load_json(
                    repo / "runtime/reports/mcel-app-proof/apps/contract-counter/mcel-app-proof-report.json",
                    "post-promotion application proof",
                )
                node_probe = dict((node_probe_runner or _run_counter_effect_probe)(repo))
                browser_probe = dict((browser_probe_runner or _run_browser_effect_probe)(repo, headed))
                compiled = compile_dsl_application(live_dsl, compare_ir_path=fixture_ir_path)
                if not compiled.valid or compiled.normalized_ir is None:
                    raise CounterPromotionError("Promoted authoritative DSL failed compilation.")
                effects = _build_effect_accounting(
                    ir=compiled.normalized_ir,
                    acceptance=acceptance,
                    observation=observation,
                    node_probe=node_probe,
                    browser_probe=browser_probe,
                )
                imported = import_counter_legacy_package(live_package)
                semantic_comparison = (
                    compare_application_ir(compiled.normalized_ir, imported.normalized_ir)
                    if imported.valid and imported.normalized_ir is not None
                    else {"status": "invalid"}
                )
                after_fingerprints = _workspace_fingerprints(repo)
                checks = {
                    "sourceAuthority": _authoring_status(live_package) == "dsl-authoritative",
                    "generatedOwnership": True,
                    "compatibility": compatibility.status == "exact",
                    "packageValidation": bool(after_fingerprints.get("packageValid")),
                    "acceptance": acceptance.get("status") == "pass" and acceptance.get("passed") is True,
                    "browserObservation": observation.get("status") == "pass" and observation.get("ok") is True,
                    "effectAccounting": effects.get("status") == "closed" and effects.get("valid") is True,
                    "applicationProof": proof.get("status") == "pass" and proof.get("truthStatus") == "semantic-runtime-proven",
                    "repositoryBinding": (proof.get("stages") or {}).get("repositoryBinding", {}).get("status") == "exact",
                    "semanticRoundtrip": semantic_comparison.get("status") == "exact",
                    "semanticIdentity": compiled.semantic_fingerprint == plan.get("semanticFingerprint"),
                }
                failed = [name for name, passed in checks.items() if not passed]
                if failed:
                    raise CounterPromotionError("Post-promotion authority checks failed: " + ", ".join(failed))
                if failure_injector:
                    failure_injector("verified")

                protected_after = _promotion_authority_source_snapshot(repo)
                _write_json(transaction_directory / "protected-after.json", protected_after)
                transaction_report = {
                    "schema": REPORT_SCHEMA,
                    "version": REPORT_VERSION,
                    "appId": "contract-counter",
                    "valid": True,
                    "status": "pass",
                    "transactionId": transaction_id,
                    "promotionExecuted": True,
                    "candidatePromoted": True,
                    "sourceAuthorityBefore": "legacy-explicit-package",
                    "sourceAuthority": "mcel.dsl.v1",
                    "derivedArtifactAuthority": "mcel.counter.explicit-projection.v1",
                    "legacyPackageAuthority": "retired",
                    "truthStatus": "semantic-runtime-proven",
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
                        "sourceAuthority": "mcel.dsl.v1",
                        "derivedArtifactAuthority": "mcel.counter.explicit-projection.v1",
                        "legacyPackageAuthority": "retired",
                        "rollbackAvailable": True,
                    },
                    "artifacts": {
                        "transaction": _display_path(transaction_directory, repo),
                        "rollback": _display_path(transaction_directory / "protected-before", repo),
                    },
                }
                _write_transaction_state(transaction_directory, "committed", plan, committed=True)
                _write_json(transaction_directory / "promotion-result.json", transaction_report)
                if write_report:
                    _write_execution_report(output_directory, transaction_report, diagnostics)
                return CounterPromotionResult(
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
                        _promotion_authority_source_snapshot(repo) == protected_before
                        and _tree_snapshot(live_package) == live_before
                    )
                    if not restored:
                        rollback_error = "Automatic rollback did not restore the protected source boundary exactly."
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
                raise CounterPromotionError(message) from exc
    except (CounterPromotionError, OSError, ValueError, json.JSONDecodeError) as exc:
        diagnostics.append(_diagnostic("MCEL_COUNTER_PROMOTION_FAILED", str(exc), "$promotion"))
        report = {
            "schema": REPORT_SCHEMA,
            "version": REPORT_VERSION,
            "appId": "contract-counter",
            "valid": False,
            "status": "fail",
            "promotionExecuted": False,
            "candidatePromoted": False,
            "sourceAuthority": _authoring_status(live_package) or "legacy-explicit-package",
            "rollbackAvailable": False,
            "automaticRollbackPerformed": "Automatic rollback" in str(exc) or "rollback" in str(exc).lower(),
            "authority": {
                "promotionExecuted": False,
                "candidatePromoted": False,
                "sourceAuthority": _authoring_status(live_package) or "legacy-explicit-package",
            },
        }
        return CounterPromotionResult(False, "fail", report, tuple(diagnostics))


def rollback_counter_promotion(
    transaction: str,
    *,
    repo_root: Path = REPOSITORY_ROOT,
    transaction_root: Path = DEFAULT_TRANSACTION_ROOT,
    report_root: Path = DEFAULT_REPORT_ROOT,
    write_report: bool = True,
) -> CounterPromotionResult:
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
                raise CounterPromotionError("Only a committed Counter promotion transaction can be rolled back.")
            expected_after = _read_json(tx / "protected-after.json")
            current = _promotion_authority_source_snapshot(repo)
            drift = _snapshot_changes(expected_after, current)
            if drift:
                raise CounterPromotionError(
                    "Protected MCEL source drift blocks rollback: " + ", ".join(drift)
                )
            _restore_protected_snapshot(repo, tx)
            expected_before = _read_json(tx / "protected-before.json")
            restored = _promotion_authority_source_snapshot(repo)
            drift_after = _snapshot_changes(expected_before, restored)
            if drift_after:
                raise CounterPromotionError(
                    "Rollback restoration is not exact: " + ", ".join(drift_after)
                )
            expected_fingerprints = _read_json(tx / "fingerprints-before.json")
            restored_fingerprints = _workspace_fingerprints(repo)
            fingerprint_keys = ("package", "catalog", "runtimeProjection", "semantic")
            fingerprint_drift = [
                key for key in fingerprint_keys
                if expected_fingerprints.get(key) != restored_fingerprints.get(key)
            ]
            if fingerprint_drift:
                raise CounterPromotionError(
                    "Rollback fingerprint restoration is not exact: " + ", ".join(fingerprint_drift)
                )
            state.update({
                "phase": "rolled-back",
                "committed": False,
                "rolledBackUtc": _utc_now(),
            })
            _write_json(tx / "transaction-state.json", state)
            report = {
                "schema": ROLLBACK_SCHEMA,
                "version": REPORT_VERSION,
                "appId": "contract-counter",
                "valid": True,
                "status": "pass",
                "transactionId": tx.name,
                "rollbackExecuted": True,
                "restoration": "exact",
                "sourceAuthority": _authoring_status(repo / "mcel_apps/contract-counter") or "legacy-explicit-package",
                "promotionActive": False,
                "fingerprints": restored_fingerprints,
            }
            _write_json(tx / "rollback-result.json", report)
            output = report_base / tx.name / "rollback"
            if write_report:
                _write_execution_report(output, report, diagnostics, filename="mcel-counter-promotion-rollback-report")
            return CounterPromotionResult(True, "pass", report, tuple(diagnostics), output if write_report else None)
    except (CounterPromotionError, OSError, ValueError, json.JSONDecodeError) as exc:
        diagnostics.append(_diagnostic("MCEL_COUNTER_PROMOTION_ROLLBACK_FAILED", str(exc), "$rollback"))
        report = {
            "schema": ROLLBACK_SCHEMA,
            "version": REPORT_VERSION,
            "appId": "contract-counter",
            "valid": False,
            "status": "fail",
            "rollbackExecuted": False,
            "restoration": "blocked",
        }
        return CounterPromotionResult(False, "fail", report, tuple(diagnostics))


def _validate_plan(plan: Mapping[str, Any]) -> None:
    if plan.get("schema") != "mcel.counter-promotion-plan.v1":
        raise CounterPromotionError("Fresh rehearsal returned an unknown promotion-plan schema.")
    if plan.get("sourceAuthorityBefore") != "legacy-explicit-package":
        raise CounterPromotionError("Promotion plan does not begin at legacy explicit-package authority.")
    if plan.get("sourceAuthorityAfter") != "mcel.dsl.v1":
        raise CounterPromotionError("Promotion plan does not establish mcel.dsl.v1 authority.")
    evidence = plan.get("candidateEvidenceBinding") or {}
    if evidence.get("truthStatus") != "semantic-runtime-proven" or evidence.get("evidenceReused") is not False:
        raise CounterPromotionError("Promotion plan is not bound to fresh semantic-runtime-proven candidate evidence.")
    if not isinstance(plan.get("files"), list) or not plan["files"]:
        raise CounterPromotionError("Promotion plan contains no file transition records.")


def _load_promoted_payload(plan: Mapping[str, Any], promotion_root: Path) -> dict[str, bytes]:
    promoted: dict[str, bytes] = {}
    for item in plan["files"]:
        relative = str(item["path"])
        path = promotion_root / relative
        if not path.is_file():
            raise CounterPromotionError(f"Promotion payload is missing: {relative}")
        content = path.read_bytes()
        if _sha(content) != item.get("afterSha256"):
            raise CounterPromotionError(f"Promotion payload hash mismatch: {relative}")
        promoted[relative] = content
    return promoted


def _verify_plan_preconditions(repo: Path, plan: Mapping[str, Any], promoted: Mapping[str, bytes]) -> None:
    for item in plan["files"]:
        relative = str(item["path"])
        current = repo / relative
        before = current.read_bytes() if current.is_file() else None
        if _sha(before) != item.get("beforeSha256"):
            raise CounterPromotionError(f"Live before-hash drift blocks promotion: {relative}")
        if _sha(promoted[relative]) != item.get("afterSha256"):
            raise CounterPromotionError(f"Staged after-hash mismatch blocks promotion: {relative}")


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
                raise CounterPromotionError(f"Live repository drift appeared during promotion: {relative}")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.mcel-promote-{transaction_id}.tmp")
            temporary.write_bytes(promoted[relative])
            if _sha(temporary.read_bytes()) != item.get("afterSha256"):
                raise CounterPromotionError(f"Temporary promotion file failed hash verification: {relative}")
            staged.append((temporary, target, relative))
        for temporary, target, relative in staged:
            os.replace(temporary, target)
            if _sha(target.read_bytes()) != next(
                item.get("afterSha256") for item in plan["files"] if item["path"] == relative
            ):
                raise CounterPromotionError(f"Applied promotion file failed hash verification: {relative}")
    finally:
        for temporary, _target, _relative in staged:
            if temporary.exists():
                temporary.unlink()


def _restore_protected_snapshot(repo: Path, transaction_directory: Path) -> None:
    before = _read_json(transaction_directory / "protected-before.json")
    current = _promotion_authority_source_snapshot(repo)
    for relative in sorted(set(current) - set(before), reverse=True):
        path = repo / relative
        if path.is_file():
            path.unlink()
    backup = transaction_directory / "protected-before"
    for relative, digest in before.items():
        source = backup / relative
        if not source.is_file() or _sha(source.read_bytes()) != digest:
            raise CounterPromotionError(f"Rollback backup integrity failed: {relative}")
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
def _promotion_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise CounterPromotionError(f"Another Counter promotion operation holds the lock: {path}") from exc
    try:
        payload = {"schema": "mcel.counter-promotion-lock.v1", "pid": os.getpid(), "createdUtc": _utc_now()}
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
                state = _read_json(state_path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if state.get("phase") == "committed" and state.get("committed") is True:
                return candidate
        raise CounterPromotionError("No committed Counter promotion transaction exists.")
    if Path(transaction).name != transaction or transaction in {"", ".", ".."}:
        raise CounterPromotionError("Invalid Counter promotion transaction identifier.")
    path = root / transaction
    if not path.is_dir():
        raise CounterPromotionError(f"Counter promotion transaction not found: {transaction}")
    return path


def _artifact_path(repo: Path, report: Mapping[str, Any], key: str) -> Path:
    raw = str((report.get("artifacts") or {}).get(key) or "")
    if not raw:
        raise CounterPromotionError(f"Fresh rehearsal did not publish {key}.")
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
        "appId": "contract-counter",
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
    filename: str = "mcel-counter-promotion-report",
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    payload = dict(report)
    payload["diagnosticCount"] = len(diagnostics)
    payload["diagnostics"] = [dict(item) for item in diagnostics]
    _write_json(output / f"{filename}.json", payload)
    lines = [
        "# Counter Authority Promotion" if "rollback" not in filename else "# Counter Authority Rollback",
        "",
        f"- Status: `{payload.get('status')}`",
        f"- Transaction: `{payload.get('transactionId')}`",
        f"- Source authority: `{payload.get('sourceAuthority')}`",
        f"- Promotion executed: `{str(payload.get('promotionExecuted')).lower()}`",
        f"- Rollback available: `{str(payload.get('rollbackAvailable')).lower()}`",
        "",
    ]
    (output / f"{filename}.md").write_text("\n".join(lines), encoding="utf-8")


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value) + b"\n")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CounterPromotionError(f"Expected JSON object: {path}")
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
