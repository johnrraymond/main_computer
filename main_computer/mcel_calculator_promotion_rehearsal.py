"""Non-mutating promotion rehearsal for the host-bound Calculator DSL authority.

Calculator remains a shadow DSL package in the live repository.  This rehearsal
takes the fresh browser parity and candidate-evidence authorities that already
proved the generated adapter against the existing HTML runtime, builds the exact
manifest transition that would make the DSL semantically authoritative, applies
that transition only inside a temporary workspace, and proves rollback restores
the Calculator package byte-for-byte.

No generated contracts are written into ``mcel_apps/calculator`` and the legacy
semantic adapter is not deleted here.
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

from main_computer.mcel_application_ir import canonical_json_bytes
from main_computer.mcel_application_package_browser_catalog import build_repository_browser_catalog_payload
from main_computer.mcel_application_packages import build_application_package_catalog
from main_computer.mcel_application_runtime_projection import build_runtime_projection_set
from main_computer.mcel_calculator_candidate_evidence import (
    DEFAULT_REPORT_ROOT as DEFAULT_EVIDENCE_REPORT_ROOT,
    run_calculator_candidate_evidence,
)
from main_computer.mcel_calculator_candidate_projection import (
    DEFAULT_CANDIDATE_ROOT,
    DEFAULT_DSL_SOURCE,
    DEFAULT_PACKAGE_ROOT,
    project_calculator_candidate,
)
from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_evidence_provenance import build_repository_provenance


APP_ID = "calculator"
REPORT_SCHEMA = "mcel.calculator-promotion-rehearsal-report.v1"
REPORT_VERSION = "mcel-calculator-promotion-rehearsal-v1"
PLAN_SCHEMA = "mcel.application-promotion-plan.v1"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_ROOT = Path("runtime/reports/mcel-application-promotions/calculator/rehearsals")
PROMOTED_TRUTH_STATUS = "semantic-runtime-proven"
PROMOTION_BOUNDARY = (
    "mcel_apps/calculator/mcel.app.json",
)


class CalculatorPromotionRehearsalError(RuntimeError):
    """Raised when Calculator promotion rehearsal cannot complete truthfully."""


@dataclass(frozen=True)
class CalculatorPromotionRehearsalResult:
    valid: bool
    status: str
    report: Mapping[str, Any]
    diagnostics: tuple[Mapping[str, Any], ...]
    output_directory: Path | None = None

    @property
    def diagnostic_count(self) -> int:
        return len(self.diagnostics)

    def to_dict(self) -> dict[str, Any]:
        value = dict(self.report)
        value["diagnosticCount"] = self.diagnostic_count
        value["diagnostics"] = [dict(item) for item in self.diagnostics]
        if self.output_directory is not None:
            value.setdefault("artifacts", {})["outputDirectory"] = _display_path(self.output_directory, REPOSITORY_ROOT)
        return value


def rehearse_calculator_promotion(
    *,
    repo_root: Path = REPOSITORY_ROOT,
    dsl_source_path: Path = DEFAULT_DSL_SOURCE,
    candidate_root: Path = DEFAULT_CANDIDATE_ROOT,
    evidence_report_root: Path = DEFAULT_EVIDENCE_REPORT_ROOT,
    report_root: Path = DEFAULT_REPORT_ROOT,
    headed: bool = False,
    write_report: bool = True,
    evidence_runner: Callable[..., Any] | None = None,
    command_runner: Any = None,
    **_unused: Any,
) -> CalculatorPromotionRehearsalResult:
    """Build and validate the non-mutating Calculator promotion plan.

    ``command_runner`` is accepted for generic promotion-dispatch compatibility.
    Calculator proof is in-process through the candidate evidence authority.
    """

    del command_runner
    repo = Path(repo_root).resolve()
    diagnostics: list[Mapping[str, Any]] = []
    live_package = repo / DEFAULT_PACKAGE_ROOT
    manifest_path = live_package / "mcel.app.json"
    live_before = _tree_snapshot(live_package)
    protected_before = _protected_source_snapshot(repo)

    dsl_source = _resolve(repo, dsl_source_path)
    compiled = compile_dsl_application(dsl_source, write_candidate=False)
    diagnostics.extend(compiled.diagnostics)
    if not compiled.valid or compiled.normalized_ir is None or not compiled.source_binding_fingerprint:
        diagnostics.append(_diagnostic("MCEL_CALCULATOR_PROMOTION_DSL_INVALID", "Calculator DSL did not compile for promotion rehearsal.", "$source"))
        return _failure("invalid-dsl", diagnostics)

    projection = project_calculator_candidate(
        dsl_source_path=dsl_source,
        live_package_root=live_package,
        candidate_root=_resolve(repo, candidate_root),
        write_candidate=True,
    )
    diagnostics.extend(projection.diagnostics)
    if not projection.valid or projection.candidate_directory is None:
        diagnostics.append(_diagnostic("MCEL_CALCULATOR_PROMOTION_PROJECTION_INVALID", "Calculator deterministic candidate projection failed.", "$candidate"))
        return _failure("invalid-candidate", diagnostics)

    evidence_call = evidence_runner or run_calculator_candidate_evidence
    evidence = evidence_call(
        repo_root=repo,
        dsl_source_path=dsl_source,
        candidate_root=_resolve(repo, candidate_root),
        report_root=_resolve(repo, evidence_report_root),
        headed=headed,
        write_report=True,
    )
    evidence_payload = evidence.to_dict() if hasattr(evidence, "to_dict") else dict(evidence)
    evidence_valid = bool(getattr(evidence, "valid", evidence_payload.get("valid")))
    if not _candidate_evidence_is_promotion_rehearsal_ready(evidence_payload, compiled):
        evidence_valid = False
        diagnostics.append(_diagnostic(
            "MCEL_CALCULATOR_PROMOTION_EVIDENCE_INVALID",
            "Calculator candidate evidence is not promotion-rehearsal ready.",
            "$candidateEvidence",
        ))
    if not evidence_valid:
        return _failure("invalid-evidence", diagnostics)

    live_manifest = _load_json(manifest_path)
    promoted_manifest = _promoted_manifest(
        live_manifest,
        semantic_fingerprint=str(compiled.semantic_fingerprint),
        source_binding_fingerprint=str(compiled.source_binding_fingerprint),
        evidence=evidence_payload,
    )
    promoted_files = {
        "mcel_apps/calculator/mcel.app.json": _json_bytes(promoted_manifest),
    }
    plan = _build_promotion_plan(
        repo=repo,
        promoted_files=promoted_files,
        semantic_fingerprint=str(compiled.semantic_fingerprint),
        source_binding_fingerprint=str(compiled.source_binding_fingerprint),
        evidence=evidence_payload,
    )

    rehearsal = _rehearse_apply_and_rollback(repo, plan, promoted_files)
    diagnostics.extend(rehearsal["diagnostics"])
    runtime_check = _validate_promoted_workspace(rehearsal["workspace"], promoted_manifest)
    diagnostics.extend(runtime_check["diagnostics"])

    live_after = _tree_snapshot(live_package)
    protected_after = _protected_source_snapshot(repo)
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
            diagnostics.append(_diagnostic("MCEL_CALCULATOR_PROMOTION_REHEARSAL_STAGE_FAILED", f"Calculator promotion rehearsal stage failed: {stage}.", f"$stages.{stage}"))

    valid = all(stage_checks.values()) and not any(item.get("blocking", True) for item in diagnostics)
    output_directory = _output_directory(repo, report_root, compiled.source_binding_fingerprint)
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "version": REPORT_VERSION,
        "appId": APP_ID,
        "valid": valid,
        "status": "pass" if valid else "fail",
        "promotionRehearsal": "pass" if valid else "fail",
        "postPromotionTruthStatus": PROMOTED_TRUTH_STATUS if valid else "unproven",
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
        "authority": {
            "sourceAuthorityBefore": "mcel.dsl.shadow.v1",
            "sourceAuthorityAfter": "mcel.dsl.v1",
            "derivedArtifactAuthorityAfter": "mcel.calculator.host-bound-virtual-projection.v1",
            "presentationAuthority": "existing-host-html",
            "legacySemanticAdapterRemainsLive": True,
            "generatedArtifactsAreDerived": True,
            "contractsWrittenToSourceTree": False,
            "candidatePromoted": False,
            "promotionEligible": valid,
            "promotionExecuted": False,
        },
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
        _write_json(output_directory / "mcel-calculator-promotion-rehearsal-report.json", report)
        (output_directory / "mcel-calculator-promotion-rehearsal-report.md").write_text(_render_markdown(report), encoding="utf-8")
        _write_json(output_directory / "promotion-plan.json", plan)
        promoted_root = output_directory / "promoted-files"
        for relative, content in promoted_files.items():
            target = promoted_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)

    return CalculatorPromotionRehearsalResult(valid, "pass" if valid else "fail", report, tuple(diagnostics), output_directory if write_report else None)


def _candidate_evidence_is_promotion_rehearsal_ready(
    evidence: Mapping[str, Any],
    compiled: Any,
) -> bool:
    authority = evidence.get("authority") if isinstance(evidence.get("authority"), Mapping) else {}
    candidate = evidence.get("candidate") if isinstance(evidence.get("candidate"), Mapping) else {}
    stages = evidence.get("stages") if isinstance(evidence.get("stages"), Mapping) else {}
    return (
        evidence.get("valid") is True
        and evidence.get("status") == "pass"
        and evidence.get("truthStatus") == "fresh-browser-shadow-ir-native-parity"
        and candidate.get("semanticFingerprint") == compiled.semantic_fingerprint
        and candidate.get("sourceBindingFingerprint") == compiled.source_binding_fingerprint
        and authority.get("freshChromiumObservation") is True
        and authority.get("legacySemanticAdapterRemainsLive") is True
        and authority.get("candidatePromoted") is False
        and all((stage or {}).get("status") == "pass" for stage in stages.values())
    )


def _promoted_manifest(
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
    conformance["currentMode"] = PROMOTED_TRUTH_STATUS
    conformance["targetMode"] = PROMOTED_TRUTH_STATUS
    conformance["promotionRehearsalSupported"] = True
    promoted["conformance"] = conformance

    evidence_block = dict(promoted.get("evidence") or {})
    evidence_block["freshBrowserParity"] = "mcel.calculator-browser-parity-observation.v1"
    evidence_block["promotionRehearsal"] = REPORT_SCHEMA
    evidence_block["candidateTruthStatus"] = evidence.get("truthStatus")
    promoted["evidence"] = evidence_block

    projection = dict(promoted.get("projection") or {})
    projection["generatedArtifactsAreDerived"] = True
    projection["hostBoundRuntimeActive"] = True
    projection["liveRuntimeChanged"] = True
    projection["mountMode"] = "host-bound"
    projection["presentationAuthority"] = "existing-host-html"
    projection["profile"] = "mcel.calculator.host-bound-virtual-projection.v1"
    promoted["projection"] = projection

    promotion = dict(promoted.get("promotion") or {})
    promotion.update(
        {
            "schema": "mcel.application-promotion-binding.v1",
            "rehearsal": REPORT_SCHEMA,
            "semanticFingerprint": semantic_fingerprint,
            "sourceBindingFingerprint": source_binding_fingerprint,
            "promotionEligible": True,
            "promotionExecuted": False,
            "legacySemanticAdapterRetirementPending": True,
        }
    )
    promoted["promotion"] = promotion
    return promoted


def _build_promotion_plan(
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
        "schema": PLAN_SCHEMA,
        "appId": APP_ID,
        "planId": "mcel-calculator-promotion-rehearsal-v1",
        "promotionBoundary": list(PROMOTION_BOUNDARY),
        "sourceAuthorityBefore": "mcel.dsl.shadow.v1",
        "sourceAuthorityAfter": "mcel.dsl.v1",
        "derivedArtifactAuthorityAfter": "mcel.calculator.host-bound-virtual-projection.v1",
        "presentationAuthority": "existing-host-html",
        "semanticFingerprint": semantic_fingerprint,
        "sourceBindingFingerprint": source_binding_fingerprint,
        "candidateEvidenceTruthStatus": evidence.get("truthStatus"),
        "freshChromiumObservation": (evidence.get("authority") or {}).get("freshChromiumObservation") is True,
        "promotionExecuted": False,
        "files": transitions,
    }


def _rehearse_apply_and_rollback(
    repo: Path,
    plan: Mapping[str, Any],
    promoted_files: Mapping[str, bytes],
) -> dict[str, Any]:
    diagnostics: list[Mapping[str, Any]] = []
    workspace_root = Path(tempfile.mkdtemp(prefix="mcel_calculator_promotion_rehearsal_"))
    workspace = workspace_root / "repo"
    _copy_repository_for_rehearsal(repo, workspace)
    package = workspace / DEFAULT_PACKAGE_ROOT
    before = _tree_snapshot(package)
    try:
        _apply_plan(workspace, plan, promoted_files)
        after_apply = _tree_snapshot(package)
        if before == after_apply:
            diagnostics.append(_diagnostic("MCEL_CALCULATOR_PROMOTION_NOOP", "Calculator promotion plan did not change the rehearsal package.", "$promotionPlan"))
        _restore_live_package(package, before)
        restored = _tree_snapshot(package)
        return {
            "workspace": workspace,
            "rollbackRehearsal": "pass" if restored == before and before != after_apply else "fail",
            "rollbackRestoration": "exact" if restored == before else "drift",
            "diagnostics": diagnostics,
        }
    except Exception as exc:
        diagnostics.append(_diagnostic("MCEL_CALCULATOR_PROMOTION_ROLLBACK_FAILED", f"Calculator promotion rehearsal failed: {exc}", "$rollback"))
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
            raise CalculatorPromotionRehearsalError(f"Promotion plan references missing promoted file: {relative}")
        target = _safe_target(workspace, relative)
        before_sha = change.get("beforeSha256")
        if before_sha is not None and target.exists():
            actual = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
            if actual != before_sha:
                raise CalculatorPromotionRehearsalError(f"Before-hash drift blocks rehearsal at {relative}.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(promoted_files[relative])
        actual_after = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
        if actual_after != change.get("afterSha256"):
            raise CalculatorPromotionRehearsalError(f"After-hash verification failed at {relative}.")


def _restore_live_package(package_root: Path, snapshot: Mapping[str, bytes]) -> None:
    if package_root.exists():
        shutil.rmtree(package_root)
    package_root.mkdir(parents=True)
    for relative, content in snapshot.items():
        target = package_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def _validate_promoted_workspace(workspace: Path, promoted_manifest: Mapping[str, Any]) -> dict[str, Any]:
    diagnostics: list[Mapping[str, Any]] = []
    try:
        manifest_path = workspace / "mcel_apps/calculator/mcel.app.json"
        manifest_path.write_bytes(_json_bytes(promoted_manifest))
        catalog = build_application_package_catalog(workspace)
        records = [record for record in catalog.packages if record.app_id == APP_ID]
        runtime = build_runtime_projection_set(workspace)
        runtime_records = [record for record in runtime.projections if record.app_id == APP_ID]
        browser = build_repository_browser_catalog_payload(workspace)
        browser_records = [record for record in browser.get("packages") or [] if record.get("appId") == APP_ID]
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
        diagnostics.append(_diagnostic("MCEL_CALCULATOR_PROMOTED_WORKSPACE_INVALID", f"Promoted Calculator workspace validation failed: {exc}", "$workspace"))
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


def _protected_source_snapshot(repo: Path) -> dict[str, str]:
    digest: dict[str, str] = {}
    for relative in PROMOTION_BOUNDARY:
        path = repo / relative
        digest[relative] = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "@missing"
    return digest


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
        raise CalculatorPromotionRehearsalError(f"Unsafe promotion path: {relative}")
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


def _failure(status: str, diagnostics: list[Mapping[str, Any]]) -> CalculatorPromotionRehearsalResult:
    return CalculatorPromotionRehearsalResult(
        False,
        status,
        {
            "schema": REPORT_SCHEMA,
            "version": REPORT_VERSION,
            "appId": APP_ID,
            "valid": False,
            "status": status,
            "promotionRehearsal": "fail",
            "promotionEligible": False,
            "promotionExecuted": False,
        },
        tuple(diagnostics),
        None,
    )


def _render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Calculator Promotion Rehearsal",
        "",
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
