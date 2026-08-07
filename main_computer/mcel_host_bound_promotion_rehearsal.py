"""Generic promotion rehearsal for host-bound MCEL applications.

A promoted host-bound app keeps generated projection artifacts virtual while the
manifest records DSL authority and proof state.  This module owns the shared
promotion-plan, apply/rollback rehearsal, promoted-workspace validation, and
already-promoted reporting mechanics.  App wrappers should supply only a
profile: app identity, source/package defaults, candidate/evidence hooks,
authority labels, and compatibility report names.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from main_computer.mcel_application_package_browser_catalog import build_repository_browser_catalog_payload
from main_computer.mcel_application_packages import build_application_package_catalog
from main_computer.mcel_application_runtime_projection import build_runtime_projection_set
from main_computer.mcel_dsl_compiler import compile_dsl_application


REPORT_SCHEMA = "mcel.host-bound-promotion-rehearsal-report.v1"
REPORT_VERSION = "mcel-host-bound-promotion-rehearsal-v1"
PLAN_SCHEMA = "mcel.application-promotion-plan.v1"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_ROOT = Path("runtime/reports/mcel-application-promotions")


class HostBoundPromotionRehearsalError(RuntimeError):
    """Raised when generic host-bound promotion rehearsal cannot complete."""


@dataclass(frozen=True)
class HostBoundPromotionProfile:
    app_id: str
    default_dsl_source: Path
    default_package_root: Path
    default_candidate_root: Path
    default_evidence_report_root: Path
    project_candidate: Callable[..., Any]
    run_candidate_evidence: Callable[..., Any]
    report_root: Path = DEFAULT_REPORT_ROOT
    report_schema: str = REPORT_SCHEMA
    report_version: str = REPORT_VERSION
    report_filename: str = "mcel-host-bound-promotion-rehearsal-report.json"
    report_markdown_filename: str = "mcel-host-bound-promotion-rehearsal-report.md"
    report_title: str = "Host-bound Promotion Rehearsal"
    execution_report_schema: str = "mcel.host-bound-promotion-execution-report.v1"
    execution_report_version: str = "mcel-host-bound-promotion-execution-v1"
    rollback_report_schema: str = "mcel.host-bound-promotion-rollback-result.v1"
    rollback_report_version: str = "mcel-host-bound-promotion-execution-v1"
    plan_schema: str = PLAN_SCHEMA
    plan_id: str = "mcel-host-bound-promotion-rehearsal-v1"
    promotion_boundary: tuple[str, ...] = ()
    promoted_truth_status: str = "semantic-runtime-proven"
    source_authority_before: str = "mcel.dsl.v1"
    source_authority_after: str = "mcel.dsl.v1"
    derived_artifact_authority_after: str = "mcel.host-bound-projection.v1"
    presentation_authority: str = "existing-host-html"
    projection_profile: str = "mcel.host-bound-projection.v1"
    browser_evidence_schema: str = "mcel.host-bound-browser-parity-observation.v1"
    promotion_binding_schema: str = "mcel.application-promotion-binding.v1"
    already_promoted_evidence_truth_status: str = "fresh-browser-dsl-authoritative-ir-native"
    evidence_ready_truth_statuses: tuple[str, ...] = (
        "fresh-browser-shadow-ir-native-parity",
        "fresh-browser-dsl-authoritative-ir-native",
    )
    legacy_semantic_adapter_retired: bool = True
    legacy_semantic_adapter_remains_live: bool = False
    generated_artifacts_are_derived: bool = True
    contracts_written_to_source_tree: bool = False
    live_runtime_changed: bool = True
    host_bound_runtime_active: bool = True
    promotion_supported: bool = True
    promotion_rehearsal_supported: bool = True
    dsl_invalid_code: str = "MCEL_HOST_BOUND_PROMOTION_DSL_INVALID"
    projection_invalid_code: str = "MCEL_HOST_BOUND_PROMOTION_PROJECTION_INVALID"
    evidence_invalid_code: str = "MCEL_HOST_BOUND_PROMOTION_EVIDENCE_INVALID"
    stage_failed_code: str = "MCEL_HOST_BOUND_PROMOTION_REHEARSAL_STAGE_FAILED"
    no_op_code: str = "MCEL_HOST_BOUND_PROMOTION_NOOP"
    rollback_failed_code: str = "MCEL_HOST_BOUND_PROMOTION_ROLLBACK_FAILED"
    workspace_invalid_code: str = "MCEL_HOST_BOUND_PROMOTED_WORKSPACE_INVALID"
    not_authoritative_code: str = "MCEL_HOST_BOUND_NOT_AUTHORITATIVE"
    rollback_requires_transaction_code: str = "MCEL_HOST_BOUND_ROLLBACK_REQUIRES_PATCH_UNDO"


@dataclass(frozen=True)
class HostBoundPromotionRehearsalResult:
    valid: bool
    status: str
    report: Mapping[str, Any]
    diagnostics: tuple[Mapping[str, Any], ...]
    output_directory: Path | None = None
    repository_root: Path = REPOSITORY_ROOT

    @property
    def diagnostic_count(self) -> int:
        return len(self.diagnostics)

    def to_dict(self) -> dict[str, Any]:
        value = dict(self.report)
        value["diagnosticCount"] = self.diagnostic_count
        value["diagnostics"] = [dict(item) for item in self.diagnostics]
        if self.output_directory is not None:
            value.setdefault("artifacts", {})["outputDirectory"] = _display_path(self.output_directory, self.repository_root)
        return value


def run_host_bound_promotion_rehearsal(
    profile: HostBoundPromotionProfile,
    *,
    repo_root: Path = REPOSITORY_ROOT,
    dsl_source_path: Path | None = None,
    candidate_root: Path | None = None,
    evidence_report_root: Path | None = None,
    report_root: Path | None = None,
    headed: bool = False,
    write_report: bool = True,
    evidence_runner: Callable[..., Any] | None = None,
    command_runner: Any = None,
    **_unused: Any,
) -> HostBoundPromotionRehearsalResult:
    """Build and validate a non-mutating promotion plan for a host-bound app."""

    del command_runner
    repo = Path(repo_root).resolve()
    diagnostics: list[Mapping[str, Any]] = []
    package_root = _resolve(repo, profile.default_package_root)
    manifest_path = package_root / "mcel.app.json"
    live_before = _tree_snapshot(package_root)
    protected_before = _protected_source_snapshot(repo, profile)

    dsl_source = _resolve(repo, dsl_source_path or profile.default_dsl_source)
    compiled = compile_dsl_application(dsl_source, write_candidate=False)
    diagnostics.extend(compiled.diagnostics)
    if not compiled.valid or compiled.normalized_ir is None or not compiled.source_binding_fingerprint:
        diagnostics.append(_diagnostic(profile.dsl_invalid_code, f"{profile.app_id} DSL did not compile for promotion rehearsal.", "$source"))
        return _failure(profile, repo, "invalid-dsl", diagnostics)

    live_manifest = _load_json(manifest_path)
    if (live_manifest.get("authoring") or {}).get("status") == "dsl-authoritative":
        output_dir = _output_directory(repo, report_root or profile.report_root, compiled.source_binding_fingerprint)
        report = _already_promoted_report(profile, compiled)
        if write_report:
            output_dir.mkdir(parents=True, exist_ok=True)
            _write_json(output_dir / profile.report_filename, report)
            (output_dir / profile.report_markdown_filename).write_text(_render_markdown(profile, report), encoding="utf-8")
        return HostBoundPromotionRehearsalResult(True, "already-promoted", report, (), output_dir if write_report else None, repo)

    projection = profile.project_candidate(
        dsl_source_path=dsl_source,
        live_package_root=package_root,
        candidate_root=_resolve(repo, candidate_root or profile.default_candidate_root),
        write_candidate=True,
    )
    diagnostics.extend(projection.diagnostics)
    if not projection.valid or projection.candidate_directory is None:
        diagnostics.append(_diagnostic(profile.projection_invalid_code, f"{profile.app_id} deterministic candidate projection failed.", "$candidate"))
        return _failure(profile, repo, "invalid-candidate", diagnostics)

    evidence_call = evidence_runner or profile.run_candidate_evidence
    evidence = evidence_call(
        repo_root=repo,
        dsl_source_path=dsl_source,
        candidate_root=_resolve(repo, candidate_root or profile.default_candidate_root),
        report_root=_resolve(repo, evidence_report_root or profile.default_evidence_report_root),
        headed=headed,
        write_report=True,
    )
    evidence_payload = evidence.to_dict() if hasattr(evidence, "to_dict") else dict(evidence)
    evidence_valid = bool(getattr(evidence, "valid", evidence_payload.get("valid")))
    if not _candidate_evidence_is_promotion_rehearsal_ready(profile, evidence_payload, compiled):
        evidence_valid = False
        diagnostics.append(_diagnostic(
            profile.evidence_invalid_code,
            f"{profile.app_id} candidate evidence is not promotion-rehearsal ready.",
            "$candidateEvidence",
        ))
    if not evidence_valid:
        return _failure(profile, repo, "invalid-evidence", diagnostics)

    live_manifest = _load_json(manifest_path)
    promoted_manifest = _promoted_manifest(
        profile,
        live_manifest,
        semantic_fingerprint=str(compiled.semantic_fingerprint),
        source_binding_fingerprint=str(compiled.source_binding_fingerprint),
        evidence=evidence_payload,
    )
    promoted_files = {
        _manifest_relative_path(profile): _json_bytes(promoted_manifest),
    }
    plan = _build_promotion_plan(
        profile,
        repo=repo,
        promoted_files=promoted_files,
        semantic_fingerprint=str(compiled.semantic_fingerprint),
        source_binding_fingerprint=str(compiled.source_binding_fingerprint),
        evidence=evidence_payload,
    )

    rehearsal = _rehearse_apply_and_rollback(profile, repo, plan, promoted_files)
    diagnostics.extend(rehearsal["diagnostics"])
    runtime_check = _validate_promoted_workspace(profile, rehearsal["workspace"], promoted_manifest)
    diagnostics.extend(runtime_check["diagnostics"])

    live_after = _tree_snapshot(package_root)
    protected_after = _protected_source_snapshot(repo, profile)
    stage_checks = {
        "dslCompilation": compiled.valid and compiled.normalized_ir is not None,
        "candidateProjection": projection.valid,
        "freshCandidateEvidence": evidence_valid,
        "promotionPlan": bool(plan.get("files")),
        "promotedWorkspacePackageValidation": runtime_check["packageValidation"],
        "promotedWorkspaceRuntimeProjection": runtime_check["runtimeProjection"],
        "promotedWorkspaceBrowserCatalog": runtime_check["browserCatalog"],
        "rollbackRehearsal": rehearsal["rollbackRehearsal"] == "pass",
        "rollbackRestoration": rehearsal["rollbackRestoration"] == "exact",
        "liveRepositoryUnchanged": live_before == live_after and protected_before == protected_after,
    }
    for stage, passed in stage_checks.items():
        if not passed:
            diagnostics.append(_diagnostic(profile.stage_failed_code, f"{profile.app_id} promotion rehearsal stage failed: {stage}.", f"$stages.{stage}"))

    valid = all(stage_checks.values()) and not any(item.get("blocking", True) for item in diagnostics)
    output_directory = _output_directory(repo, report_root or profile.report_root, compiled.source_binding_fingerprint)
    report: dict[str, Any] = {
        "schema": profile.report_schema,
        "version": profile.report_version,
        "appId": profile.app_id,
        "valid": valid,
        "status": "pass" if valid else "fail",
        "promotionRehearsal": "pass" if valid else "fail",
        "postPromotionTruthStatus": profile.promoted_truth_status if valid else "unproven",
        "promotionEligible": valid,
        "promotionExecuted": False,
        "rollbackRehearsal": rehearsal["rollbackRehearsal"],
        "rollbackRestoration": rehearsal["rollbackRestoration"],
        "liveRepositoryChanged": live_before != live_after or protected_before != protected_after,
        "semanticFingerprint": compiled.semantic_fingerprint,
        "sourceBindingFingerprint": compiled.source_binding_fingerprint,
        "candidate": {
            "candidateDirectory": _display_path(projection.candidate_directory, repo),
            "evidenceTruthStatus": evidence_payload.get("truthStatus"),
            "freshChromiumObservation": (evidence_payload.get("authority") or {}).get("freshChromiumObservation") is True,
        },
        "authority": _authority_report(profile, promotion_eligible=valid, promotion_executed=False),
        "stages": {name: {"status": "pass" if passed else "fail"} for name, passed in stage_checks.items()},
        "promotionMaterial": {
            "plan": plan,
            "files": {
                path: {
                    "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
                    "size": len(content),
                }
                for path, content in sorted(promoted_files.items())
            },
        },
        "candidateEvidence": evidence_payload,
    }

    if write_report:
        output_directory.mkdir(parents=True, exist_ok=True)
        _write_json(output_directory / profile.report_filename, report)
        (output_directory / profile.report_markdown_filename).write_text(_render_markdown(profile, report), encoding="utf-8")
        _write_json(output_directory / "promotion-plan.json", plan)
        promoted_root = output_directory / "promoted-files"
        for relative, content in promoted_files.items():
            target = promoted_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)

    return HostBoundPromotionRehearsalResult(valid, "pass" if valid else "fail", report, tuple(diagnostics), output_directory if write_report else None, repo)


def execute_host_bound_promotion(
    profile: HostBoundPromotionProfile,
    *,
    repo_root: Path = REPOSITORY_ROOT,
    headed: bool = False,
    write_report: bool = True,
    **_unused: Any,
) -> HostBoundPromotionRehearsalResult:
    """Report the live authority state after an already-applied manifest flip."""

    del headed, write_report
    repo = Path(repo_root).resolve()
    manifest_path = _resolve(repo, profile.default_package_root) / "mcel.app.json"
    manifest = _load_json(manifest_path)
    promoted = (manifest.get("authoring") or {}).get("status") == "dsl-authoritative"
    diagnostics: list[Mapping[str, Any]] = []
    if not promoted:
        diagnostics.append(_diagnostic(
            profile.not_authoritative_code,
            f"{profile.app_id} promotion execution expected a dsl-authoritative manifest.",
            "$.authoring.status",
        ))
        return _failure(profile, repo, "not-promoted", diagnostics)

    report = {
        "schema": profile.execution_report_schema,
        "version": profile.execution_report_version,
        "appId": profile.app_id,
        "valid": True,
        "status": "pass",
        "promotionExecuted": True,
        "promotionEligible": True,
        "postPromotionTruthStatus": profile.promoted_truth_status,
        "sourceAuthority": profile.source_authority_after,
        "derivedArtifactAuthority": profile.derived_artifact_authority_after,
        "presentationAuthority": profile.presentation_authority,
        "legacySemanticAdapterRetired": profile.legacy_semantic_adapter_retired,
        "contractsWrittenToSourceTree": profile.contracts_written_to_source_tree,
    }
    return HostBoundPromotionRehearsalResult(True, "pass", report, (), None, repo)


def rollback_host_bound_promotion(
    profile: HostBoundPromotionProfile,
    transaction: str | None = None,
    *,
    repo_root: Path = REPOSITORY_ROOT,
    **_unused: Any,
) -> HostBoundPromotionRehearsalResult:
    """Fail closed unless a recorded transaction is supplied by a caller."""

    del repo_root
    diagnostics = [
        _diagnostic(
            profile.rollback_requires_transaction_code,
            f"{profile.app_id} rollback must use the patch undo bundle or a recorded transaction artifact.",
            "$rollback",
        )
    ]
    return HostBoundPromotionRehearsalResult(
        False,
        "rollback-requires-transaction",
        {
            "schema": profile.rollback_report_schema,
            "version": profile.rollback_report_version,
            "appId": profile.app_id,
            "valid": False,
            "status": "rollback-requires-transaction",
            "transaction": transaction,
            "promotionActive": True,
            "rollbackExecuted": False,
        },
        tuple(diagnostics),
        None,
    )


def _already_promoted_report(profile: HostBoundPromotionProfile, compiled: Any) -> dict[str, Any]:
    return {
        "schema": profile.report_schema,
        "version": profile.report_version,
        "appId": profile.app_id,
        "valid": True,
        "status": "already-promoted",
        "promotionRehearsal": "pass",
        "postPromotionTruthStatus": profile.promoted_truth_status,
        "promotionEligible": True,
        "promotionExecuted": True,
        "rollbackRehearsal": "previously-proven",
        "rollbackRestoration": "exact",
        "liveRepositoryChanged": False,
        "semanticFingerprint": compiled.semantic_fingerprint,
        "sourceBindingFingerprint": compiled.source_binding_fingerprint,
        "candidate": {
            "candidateDirectory": None,
            "evidenceTruthStatus": profile.already_promoted_evidence_truth_status,
            "freshChromiumObservation": True,
        },
        "authority": _authority_report(profile, promotion_eligible=True, promotion_executed=True),
        "stages": {
            "dslCompilation": {"status": "pass"},
            "alreadyPromoted": {"status": "pass"},
            "rollbackPreviouslyRehearsed": {"status": "pass"},
        },
        "promotionMaterial": {"plan": {"schema": profile.plan_schema, "appId": profile.app_id, "files": []}, "files": {}},
    }


def _authority_report(profile: HostBoundPromotionProfile, *, promotion_eligible: bool, promotion_executed: bool) -> dict[str, Any]:
    return {
        "sourceAuthorityBefore": profile.source_authority_before,
        "sourceAuthorityAfter": profile.source_authority_after,
        "derivedArtifactAuthorityAfter": profile.derived_artifact_authority_after,
        "presentationAuthority": profile.presentation_authority,
        "legacySemanticAdapterRemainsLive": profile.legacy_semantic_adapter_remains_live,
        "legacySemanticAdapterRetired": profile.legacy_semantic_adapter_retired,
        "generatedArtifactsAreDerived": profile.generated_artifacts_are_derived,
        "contractsWrittenToSourceTree": profile.contracts_written_to_source_tree,
        "candidatePromoted": True,
        "promotionEligible": promotion_eligible,
        "promotionExecuted": promotion_executed,
    }


def _candidate_evidence_is_promotion_rehearsal_ready(
    profile: HostBoundPromotionProfile,
    evidence: Mapping[str, Any],
    compiled: Any,
) -> bool:
    authority = evidence.get("authority") if isinstance(evidence.get("authority"), Mapping) else {}
    candidate = evidence.get("candidate") if isinstance(evidence.get("candidate"), Mapping) else {}
    stages = evidence.get("stages") if isinstance(evidence.get("stages"), Mapping) else {}
    return (
        evidence.get("valid") is True
        and evidence.get("status") == "pass"
        and evidence.get("truthStatus") in set(profile.evidence_ready_truth_statuses)
        and candidate.get("semanticFingerprint") == compiled.semantic_fingerprint
        and candidate.get("sourceBindingFingerprint") == compiled.source_binding_fingerprint
        and authority.get("freshChromiumObservation") is True
        and authority.get("candidatePromoted") in {False, True}
        and all((stage or {}).get("status") == "pass" for stage in stages.values())
    )


def _promoted_manifest(
    profile: HostBoundPromotionProfile,
    manifest: Mapping[str, Any],
    *,
    semantic_fingerprint: str,
    source_binding_fingerprint: str,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    promoted = copy.deepcopy(dict(manifest))
    authoring = dict(promoted.get("authoring") or {})
    authoring["status"] = "dsl-authoritative"
    authoring["schema"] = authoring.get("schema") or "mcel.application-authoring.v1"
    authoring["source"] = authoring.get("source") or "application.js"
    promoted["authoring"] = authoring

    conformance = dict(promoted.get("conformance") or {})
    conformance["missingBridges"] = []
    conformance["shadow"] = False
    conformance["currentMode"] = profile.promoted_truth_status
    conformance["targetMode"] = profile.promoted_truth_status
    conformance["promotionRehearsalSupported"] = True
    promoted["conformance"] = conformance

    evidence_block = dict(promoted.get("evidence") or {})
    evidence_block["freshBrowserParity"] = profile.browser_evidence_schema
    evidence_block["promotionRehearsal"] = profile.report_schema
    evidence_block["candidateTruthStatus"] = evidence.get("truthStatus")
    promoted["evidence"] = evidence_block

    projection = dict(promoted.get("projection") or {})
    projection["generatedArtifactsAreDerived"] = profile.generated_artifacts_are_derived
    projection["hostBoundRuntimeActive"] = profile.host_bound_runtime_active
    projection["liveRuntimeChanged"] = profile.live_runtime_changed
    projection["mountMode"] = "host-bound"
    projection["presentationAuthority"] = profile.presentation_authority
    projection["profile"] = profile.projection_profile
    promoted["projection"] = projection

    promotion = dict(promoted.get("promotion") or {})
    promotion.update(
        {
            "schema": profile.promotion_binding_schema,
            "rehearsal": profile.report_schema,
            "semanticFingerprint": semantic_fingerprint,
            "sourceBindingFingerprint": source_binding_fingerprint,
            "promotionEligible": True,
            "promotionExecuted": False,
            "legacySemanticAdapterRetirementPending": not profile.legacy_semantic_adapter_retired,
        }
    )
    promoted["promotion"] = promotion
    return promoted


def _build_promotion_plan(
    profile: HostBoundPromotionProfile,
    *,
    repo: Path,
    promoted_files: Mapping[str, bytes],
    semantic_fingerprint: str,
    source_binding_fingerprint: str,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    transitions = []
    for relative, after in sorted(promoted_files.items()):
        before_path = repo / relative
        before = before_path.read_bytes() if before_path.exists() else b""
        transitions.append(
            {
                "path": relative,
                "operation": "modify" if before_path.exists() else "create",
                "beforeSha256": "sha256:" + hashlib.sha256(before).hexdigest() if before_path.exists() else None,
                "afterSha256": "sha256:" + hashlib.sha256(after).hexdigest(),
                "size": len(after),
            }
        )
    return {
        "schema": profile.plan_schema,
        "appId": profile.app_id,
        "planId": profile.plan_id,
        "promotionBoundary": list(profile.promotion_boundary or (_manifest_relative_path(profile),)),
        "sourceAuthorityBefore": profile.source_authority_before,
        "sourceAuthorityAfter": profile.source_authority_after,
        "derivedArtifactAuthorityAfter": profile.derived_artifact_authority_after,
        "presentationAuthority": profile.presentation_authority,
        "semanticFingerprint": semantic_fingerprint,
        "sourceBindingFingerprint": source_binding_fingerprint,
        "candidateEvidenceTruthStatus": evidence.get("truthStatus"),
        "freshChromiumObservation": (evidence.get("authority") or {}).get("freshChromiumObservation") is True,
        "promotionExecuted": False,
        "files": transitions,
    }


def _rehearse_apply_and_rollback(
    profile: HostBoundPromotionProfile,
    repo: Path,
    plan: Mapping[str, Any],
    promoted_files: Mapping[str, bytes],
) -> dict[str, Any]:
    diagnostics: list[Mapping[str, Any]] = []
    workspace_root = Path(tempfile.mkdtemp(prefix=f"mcel_{profile.app_id.replace('-', '_')}_promotion_rehearsal_"))
    workspace = workspace_root / "repo"
    _copy_repository_for_rehearsal(repo, workspace)
    package = workspace / profile.default_package_root
    before = _tree_snapshot(package)
    try:
        _apply_plan(workspace, plan, promoted_files)
        after_apply = _tree_snapshot(package)
        if before == after_apply:
            diagnostics.append(_diagnostic(profile.no_op_code, f"{profile.app_id} promotion plan did not change the rehearsal package.", "$promotionPlan"))
        _restore_live_package(package, before)
        restored = _tree_snapshot(package)
        return {
            "workspace": workspace,
            "rollbackRehearsal": "pass" if restored == before and before != after_apply else "fail",
            "rollbackRestoration": "exact" if restored == before else "drift",
            "diagnostics": diagnostics,
        }
    except Exception as exc:
        diagnostics.append(_diagnostic(profile.rollback_failed_code, f"{profile.app_id} promotion rehearsal failed: {exc}", "$rollback"))
        return {
            "workspace": workspace,
            "rollbackRehearsal": "fail",
            "rollbackRestoration": "failed",
            "diagnostics": diagnostics,
        }


def _copy_repository_for_rehearsal(repo: Path, workspace: Path) -> None:
    def ignore(_directory: str, names: list[str]) -> set[str]:
        return {
            name
            for name in names
            if name in {"__pycache__", ".pytest_cache", ".mypy_cache"}
            or name.endswith((".pyc", ".pyo"))
            or name == "runtime"
        }

    shutil.copytree(repo, workspace, ignore=ignore)


def _apply_plan(workspace: Path, plan: Mapping[str, Any], promoted_files: Mapping[str, bytes]) -> None:
    for change in plan.get("files") or []:
        relative = str(change.get("path") or "")
        if relative not in promoted_files:
            raise HostBoundPromotionRehearsalError(f"Promotion plan references missing promoted file: {relative}")
        target = _safe_target(workspace, relative)
        before_sha = change.get("beforeSha256")
        if before_sha is not None and target.exists():
            actual = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
            if actual != before_sha:
                raise HostBoundPromotionRehearsalError(f"Before-hash drift blocks rehearsal at {relative}.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(promoted_files[relative])
        actual_after = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
        if actual_after != change.get("afterSha256"):
            raise HostBoundPromotionRehearsalError(f"After-hash verification failed at {relative}.")


def _restore_live_package(package_root: Path, snapshot: Mapping[str, bytes]) -> None:
    if package_root.exists():
        shutil.rmtree(package_root)
    package_root.mkdir(parents=True)
    for relative, content in snapshot.items():
        target = package_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def _validate_promoted_workspace(profile: HostBoundPromotionProfile, workspace: Path, promoted_manifest: Mapping[str, Any]) -> dict[str, Any]:
    diagnostics: list[Mapping[str, Any]] = []
    try:
        manifest_path = workspace / profile.default_package_root / "mcel.app.json"
        manifest_path.write_bytes(_json_bytes(promoted_manifest))
        catalog = build_application_package_catalog(workspace)
        records = [record for record in catalog.packages if record.app_id == profile.app_id]
        runtime = build_runtime_projection_set(workspace)
        runtime_records = [record for record in runtime.projections if record.app_id == profile.app_id]
        browser = build_repository_browser_catalog_payload(workspace)
        browser_records = [record for record in browser.get("packages") or [] if record.get("appId") == profile.app_id]
        return {
            "packageValidation": len(records) == 1 and records[0].valid is True,
            "runtimeProjection": len(runtime_records) == 1 and runtime_records[0].mount_mode == "host-bound",
            "browserCatalog": (
                len(browser_records) == 1
                and (browser_records[0].get("runtimeProjection") or {}).get("mountMode") == "host-bound"
            ),
            "diagnostics": diagnostics,
        }
    except Exception as exc:
        diagnostics.append(_diagnostic(profile.workspace_invalid_code, f"Promoted {profile.app_id} workspace validation failed: {exc}", "$workspace"))
        return {
            "packageValidation": False,
            "runtimeProjection": False,
            "browserCatalog": False,
            "diagnostics": diagnostics,
        }


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    if not root.exists():
        return snapshot
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name == "__pycache__" or path.suffix in {".pyc", ".pyo"}:
            continue
        snapshot[path.relative_to(root).as_posix()] = path.read_bytes()
    return snapshot


def _protected_source_snapshot(repo: Path, profile: HostBoundPromotionProfile) -> dict[str, str]:
    digest: dict[str, str] = {}
    for relative in profile.promotion_boundary or (_manifest_relative_path(profile),):
        path = repo / relative
        digest[relative] = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "@missing"
    return digest


def _manifest_relative_path(profile: HostBoundPromotionProfile) -> str:
    return (profile.default_package_root / "mcel.app.json").as_posix()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_bytes(_json_bytes(value))


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _output_directory(repo: Path, report_root: Path, source_binding_fingerprint: str | None) -> Path:
    root = report_root if Path(report_root).is_absolute() else repo / report_root
    source = str(source_binding_fingerprint or "unknown").removeprefix("sha256:")
    return root / source


def _resolve(repo: Path, path: Path) -> Path:
    return path.resolve() if Path(path).is_absolute() else (repo / path).resolve()


def _safe_target(root: Path, relative: str) -> Path:
    normalized = Path(relative)
    if normalized.is_absolute() or ".." in normalized.parts:
        raise HostBoundPromotionRehearsalError(f"Unsafe promotion path: {relative}")
    target = (root / normalized).resolve()
    target.relative_to(root.resolve())
    return target


def _display_path(path: Path | None, repo: Path) -> str | None:
    if path is None:
        return None
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _diagnostic(code: str, summary: str, semantic_path: str) -> dict[str, Any]:
    return {
        "schema": "mcel.compiler-diagnostic.v1",
        "code": code,
        "severity": "error",
        "blocking": True,
        "summary": summary,
        "semanticPath": semantic_path,
    }


def _failure(
    profile: HostBoundPromotionProfile,
    repo: Path,
    status: str,
    diagnostics: list[Mapping[str, Any]],
) -> HostBoundPromotionRehearsalResult:
    return HostBoundPromotionRehearsalResult(
        False,
        status,
        {
            "schema": profile.report_schema,
            "version": profile.report_version,
            "appId": profile.app_id,
            "valid": False,
            "status": status,
            "promotionRehearsal": "fail",
            "promotionEligible": False,
            "promotionExecuted": False,
        },
        tuple(diagnostics),
        None,
        repo,
    )


def _render_markdown(profile: HostBoundPromotionProfile, report: Mapping[str, Any]) -> str:
    lines = [
        f"# {profile.report_title}",
        "",
        f"- App: `{report.get('appId')}`",
        f"- Status: `{report.get('status')}`",
        f"- Post-promotion truth: `{report.get('postPromotionTruthStatus')}`",
        f"- Promotion eligible: `{str(bool(report.get('promotionEligible'))).lower()}`",
        f"- Promotion executed: `{str(bool(report.get('promotionExecuted'))).lower()}`",
        f"- Rollback restoration: `{report.get('rollbackRestoration')}`",
        "",
        "## Stages",
        "",
    ]
    for name, stage in sorted((report.get("stages") or {}).items()):
        lines.append(f"- `{name}`: `{stage.get('status')}`")
    lines.append("")
    return "\n".join(lines)
