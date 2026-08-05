"""Non-mutating generic-pipeline promotion rehearsal for Contract Workbench."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from main_computer.mcel_application_ir import canonical_json_bytes, compare_application_ir
from main_computer.mcel_application_packages import build_application_package_catalog
from main_computer.mcel_application_runtime_projection import build_application_runtime_projection
from main_computer.mcel_dsl_compiler import DEFAULT_CANDIDATE_ROOT, compile_dsl_application
from main_computer.mcel_workbench_candidate_evidence import (
    DEFAULT_REPORT_ROOT as DEFAULT_EVIDENCE_REPORT_ROOT,
    _prepare_workspace,
    run_workbench_candidate_evidence,
)
from main_computer.mcel_workbench_candidate_projection import (
    APP_ID,
    DEFAULT_DSL_SOURCE,
    DEFAULT_FIXTURE_IR,
    DEFAULT_PACKAGE_ROOT,
    GENERATED_PATHS,
    PROJECTION_PROFILE,
    project_workbench_candidate,
)

REPORT_SCHEMA = "mcel.app-promotion-rehearsal-report.v1"
REPORT_VERSION = "mcel-app-promotion-rehearsal-wave12"
PLAN_SCHEMA = "mcel.application-promotion-plan.v1"
OWNERSHIP_SCHEMA = "mcel.generated-file-ownership.v1"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_ROOT = Path("runtime/reports/mcel-compiler-candidates")
PROMOTION_SUPPORT_ROOT = Path("main_computer/mcel_projection_profiles/contract-workbench-v1/promotion")
PROMOTION_SUPPORT_FILES = (
    "tests/test_forward_spec.py",
    "tests/test_operations.py",
    "tests/test_package.py",
)


class WorkbenchPromotionRehearsalError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkbenchPromotionRehearsalResult:
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
        return value


def rehearse_workbench_promotion(
    *,
    repo_root: Path = REPOSITORY_ROOT,
    dsl_source_path: Path = DEFAULT_DSL_SOURCE,
    fixture_ir_path: Path = DEFAULT_FIXTURE_IR,
    candidate_root: Path = DEFAULT_CANDIDATE_ROOT,
    evidence_report_root: Path = DEFAULT_EVIDENCE_REPORT_ROOT,
    report_root: Path = DEFAULT_REPORT_ROOT,
    headed: bool = False,
    write_report: bool = True,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    evidence_runner: Callable[..., Any] | None = None,
) -> WorkbenchPromotionRehearsalResult:
    repo = repo_root.resolve()
    diagnostics: list[Mapping[str, Any]] = []
    live_package = repo / DEFAULT_PACKAGE_ROOT
    package_before = _tree_snapshot(live_package)
    protected_before = _protected_source_snapshot(repo)

    source = _resolve(repo, dsl_source_path)
    fixture = _resolve(repo, fixture_ir_path)
    candidates = _resolve(repo, candidate_root)
    evidence_root = _resolve(repo, evidence_report_root)
    reports = _resolve(repo, report_root)

    dsl = compile_dsl_application(source, compare_ir_path=fixture)
    diagnostics.extend(dsl.diagnostics)
    if not dsl.valid or dsl.normalized_ir is None or dsl.comparison_status != "exact" or not dsl.source_binding_fingerprint:
        return _failure("invalid-dsl", diagnostics, "Native Workbench DSL is not exact.")

    projection = project_workbench_candidate(
        dsl_source_path=source,
        fixture_ir_path=fixture,
        live_package_root=live_package,
        candidate_root=candidates,
        write_candidate=True,
    )
    diagnostics.extend(projection.diagnostics)
    if not projection.valid or projection.candidate_directory is None:
        return _failure("invalid-candidate", diagnostics, "Portable Workbench candidate projection is not exact.")

    evidence_call = evidence_runner or run_workbench_candidate_evidence
    evidence = evidence_call(
        repo_root=repo,
        dsl_source_path=source,
        fixture_ir_path=fixture,
        candidate_root=candidates,
        report_root=evidence_root,
        headed=headed,
        write_report=True,
        command_runner=command_runner,
    )
    evidence_payload = evidence.to_dict() if hasattr(evidence, "to_dict") else dict(evidence)
    evidence_valid = bool(getattr(evidence, "valid", evidence_payload.get("valid")))
    candidate_evidence = evidence_payload.get("candidate") or {}
    if not evidence_valid or evidence_payload.get("truthStatus") != "semantic-runtime-proven":
        diagnostics.append(_diagnostic("MCEL_WORKBENCH_PROMOTION_EVIDENCE_INVALID", "The isolated Workbench candidate has not earned semantic-runtime-proven evidence.", "$candidateEvidence"))
        return _failure("invalid-evidence", diagnostics, "Passing isolated candidate evidence is required.")
    if (
        candidate_evidence.get("semanticFingerprint") != dsl.semantic_fingerprint
        or candidate_evidence.get("sourceBindingFingerprint") != dsl.source_binding_fingerprint
    ):
        diagnostics.append(_diagnostic("MCEL_WORKBENCH_PROMOTION_EVIDENCE_BINDING_CONFLICT", "Candidate evidence is not bound to the exact Workbench DSL candidate.", "$candidateEvidence"))
        return _failure("stale-evidence", diagnostics, "Exact candidate evidence is required.")

    candidate_dir = projection.candidate_directory.resolve()
    candidate_package = candidate_dir / "package" / "mcel_apps" / APP_ID
    rehearsal_state = candidate_dir / "promotion-rehearsal"
    output = reports / APP_ID / str(dsl.source_binding_fingerprint).removeprefix("sha256:") / "promotion-rehearsal"
    workspace = rehearsal_state / "workspace"

    try:
        plan, promoted = _build_promotion_plan(
            live_package=live_package,
            candidate_package=candidate_package,
            dsl_source=source,
            semantic_fingerprint=str(dsl.semantic_fingerprint),
            source_binding_fingerprint=str(dsl.source_binding_fingerprint),
            evidence=evidence_payload,
        )
        _stage_material(rehearsal_state, plan, promoted, live_package)
        _prepare_workspace(repo, workspace, candidate_package)
        pre = _workspace_fingerprints(workspace)
        _apply_plan(workspace, plan, promoted)
        _verify_plan_after_hashes(workspace, plan)
        _verify_generated_ownership(workspace / DEFAULT_PACKAGE_ROOT)

        promoted_compile = compile_dsl_application(
            workspace / DEFAULT_PACKAGE_ROOT / "application.js",
            compare_ir_path=fixture,
        )
        if not promoted_compile.valid or promoted_compile.normalized_ir is None or promoted_compile.comparison_status != "exact":
            raise WorkbenchPromotionRehearsalError("Promoted Workbench DSL does not compile exactly in the isolated repository.")
        semantic_comparison = compare_application_ir(dsl.normalized_ir, promoted_compile.normalized_ir)
        if semantic_comparison.get("status") != "exact":
            raise WorkbenchPromotionRehearsalError("Promoted Workbench semantic identity changed.")

        _run_promoted_authorities(repo, workspace, headed, command_runner or subprocess.run)
        proof = _load_json(workspace / "runtime/reports/mcel-app-proof/apps/contract-workbench/mcel-app-proof-report.json", "post-promotion Workbench proof")
        intent = proof.get("intentCoverage") or {}
        effects = intent.get("effectAccounting") or {}
        capabilities = intent.get("capabilityAccounting") or {}
        post = _workspace_fingerprints(workspace)
        checks = {
            "dslCompilation": promoted_compile.valid and promoted_compile.comparison_status == "exact",
            "semanticCompatibility": semantic_comparison.get("status") == "exact",
            "generatedOwnership": _verify_generated_ownership(workspace / DEFAULT_PACKAGE_ROOT),
            "packageValidation": bool(post.get("packageValid")),
            "acceptance": (proof.get("stages") or {}).get("acceptanceEvidence", {}).get("status") == "pass",
            "browserObservation": (proof.get("stages") or {}).get("browserObservation", {}).get("status") == "pass",
            "applicationProof": proof.get("status") == "pass" and proof.get("truthStatus") == "semantic-runtime-proven",
            "repositoryBinding": (proof.get("stages") or {}).get("repositoryBinding", {}).get("status") == "exact",
            "intentCompleteness": intent.get("status") == "ir-native" and intent.get("coveredIntentCount") == 7,
            "scenarioCompleteness": intent.get("observedScenarioCount") == 14,
            "effectAccounting": effects.get("status") == "closed" and effects.get("declaredEffectCount") == 18,
            "capabilityAccounting": capabilities.get("status") == "closed" and capabilities.get("declaredCapabilityCount") == 1,
        }
        if not all(checks.values()):
            raise WorkbenchPromotionRehearsalError("Post-promotion checks failed: " + ", ".join(key for key, value in checks.items() if not value))

        _restore_live_package(workspace, live_package)
        rollback = _workspace_fingerprints(workspace)
        rollback_exact = (
            _tree_snapshot(workspace / DEFAULT_PACKAGE_ROOT) == package_before
            and rollback.get("package") == pre.get("package")
            and rollback.get("catalog") == pre.get("catalog")
            and rollback.get("runtimeProjection") == pre.get("runtimeProjection")
        )
        if not rollback_exact:
            raise WorkbenchPromotionRehearsalError("Rollback did not restore the original Workbench package exactly.")
    except (OSError, ValueError, WorkbenchPromotionRehearsalError) as exc:
        diagnostics.append(_diagnostic("MCEL_WORKBENCH_PROMOTION_REHEARSAL_FAILED", str(exc), "$promotionRehearsal"))
        report = _base_report(dsl, projection, evidence_payload)
        report.update({"status": "fail", "valid": False, "promotionRehearsal": "fail", "rollbackRehearsal": "not-completed", "rollbackRestoration": "not-completed", "promotionEligible": False, "authority": _authority(False), "error": str(exc)})
        if write_report:
            _write_reports(output, report)
        return WorkbenchPromotionRehearsalResult(False, "fail", report, tuple(diagnostics), output if write_report else None)

    package_after = _tree_snapshot(live_package)
    protected_after = _protected_source_snapshot(repo)
    live_changed = package_after != package_before or protected_after != protected_before
    if live_changed:
        diagnostics.append(_diagnostic("MCEL_WORKBENCH_PROMOTION_REHEARSAL_MUTATED_LIVE_REPOSITORY", "The non-mutating rehearsal changed protected live sources.", "$authority.liveApplicationChanged"))

    valid = not live_changed and not diagnostics
    report = _base_report(dsl, projection, evidence_payload)
    report.update({
        "status": "pass" if valid else "fail",
        "valid": valid,
        "promotionRehearsal": "pass" if valid else "fail",
        "postPromotionTruthStatus": "semantic-runtime-proven",
        "rollbackRehearsal": "pass",
        "rollbackRestoration": "exact",
        "promotionEligible": valid,
        "promotionExecuted": False,
        "liveRepositoryChanged": live_changed,
        "plan": plan,
        "postPromotion": {"checks": {key: {"status": "pass" if value else "fail"} for key, value in checks.items()}, "fingerprints": post, "intentCoverage": {"declaredIntents": 7, "coveredIntents": 7, "declaredScenarios": 14, "observedScenarios": 14}, "effectAccounting": effects, "capabilityAccounting": capabilities},
        "rollback": {"status": "pass", "restoration": "exact", "fingerprints": rollback},
        "authority": _authority(valid),
        "artifacts": {"promotionMaterial": _display(rehearsal_state / "promotion", repo), "rollbackMaterial": _display(rehearsal_state / "rollback", repo), "workspace": _display(workspace, repo)},
    })
    if write_report:
        _write_reports(output, report)
    return WorkbenchPromotionRehearsalResult(valid, report["status"], report, tuple(diagnostics), output if write_report else None)


def _build_promotion_plan(*, live_package: Path, candidate_package: Path, dsl_source: Path, semantic_fingerprint: str, source_binding_fingerprint: str, evidence: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, bytes]]:
    promoted: dict[str, bytes] = {}
    root = f"mcel_apps/{APP_ID}"
    promoted[f"{root}/application.js"] = dsl_source.read_bytes()
    profile_root = Path(__file__).resolve().parents[1] / PROMOTION_SUPPORT_ROOT
    for relative in PROMOTION_SUPPORT_FILES:
        promoted[f"{root}/{relative}"] = (profile_root / relative).read_bytes()
    generated_paths = sorted(path for path in GENERATED_PATHS if path != "mcel.app.json")
    for relative in generated_paths:
        promoted[f"{root}/{relative}"] = (candidate_package / relative).read_bytes()

    manifest = _load_json(live_package / "mcel.app.json", "live Workbench package manifest")
    manifest["authoring"] = {
        "schema": "mcel.application-authoring.v1",
        "status": "dsl-authoritative",
        "source": "application.js",
        "ownership": "mcel.generated.json",
        "normalizedDefinition": "generated/mcel.application.normalized.json",
    }
    manifest["projection"] = {"profile": PROJECTION_PROFILE, "generatedArtifactsAreDerived": True}
    promoted[f"{root}/mcel.app.json"] = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    ownership = {
        "schema": OWNERSHIP_SCHEMA,
        "version": REPORT_VERSION,
        "appId": APP_ID,
        "manualEditsProhibited": True,
        "generatedArtifactsAreDerived": True,
        "sourceAuthority": {"kind": "mcel.dsl.v1", "path": "application.js", "semanticFingerprint": semantic_fingerprint, "sourceBindingFingerprint": source_binding_fingerprint},
        "generatedFiles": [
            {"path": relative, "sha256": _sha(promoted[f"{root}/{relative}"]), "generator": PROJECTION_PROFILE}
            for relative in generated_paths
        ],
    }
    promoted[f"{root}/mcel.generated.json"] = canonical_json_bytes(ownership) + b"\n"

    files = []
    for relative, content in sorted(promoted.items()):
        current = live_package.parent.parent / relative
        before = current.read_bytes() if current.is_file() else None
        package_relative = relative.removeprefix(root + "/")
        files.append({"path": relative, "action": "replace" if before is not None else "add", "beforeSha256": _sha(before), "afterSha256": _sha(content), "byteChange": before != content, "generated": package_relative in generated_paths, "transitionSupport": package_relative in PROMOTION_SUPPORT_FILES})
    plan = {
        "schema": PLAN_SCHEMA,
        "version": REPORT_VERSION,
        "appId": APP_ID,
        "promotionExecuted": False,
        "sourceAuthorityBefore": "legacy-explicit-package",
        "sourceAuthorityAfter": "mcel.dsl.v1",
        "derivedArtifactAuthorityAfter": PROJECTION_PROFILE,
        "semanticFingerprint": semantic_fingerprint,
        "sourceBindingFingerprint": source_binding_fingerprint,
        "candidateEvidenceBinding": {"truthStatus": evidence.get("truthStatus"), "semanticFingerprint": (evidence.get("candidate") or {}).get("semanticFingerprint"), "sourceBindingFingerprint": (evidence.get("candidate") or {}).get("sourceBindingFingerprint"), "evidenceReused": False},
        "files": files,
    }
    return plan, promoted


def _stage_material(root: Path, plan: Mapping[str, Any], promoted: Mapping[str, bytes], live_package: Path) -> None:
    if root.exists(): shutil.rmtree(root)
    promotion = root / "promotion"
    rollback = root / "rollback"
    promotion.mkdir(parents=True); rollback.mkdir(parents=True)
    (promotion / "promotion-plan.json").write_bytes(canonical_json_bytes(plan) + b"\n")
    for relative, content in promoted.items():
        target = promotion / relative; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(content)
    shutil.copytree(live_package, rollback / DEFAULT_PACKAGE_ROOT)
    rollback_manifest = {"schema": "mcel.application-promotion-rollback.v1", "appId": APP_ID, "restores": DEFAULT_PACKAGE_ROOT.as_posix(), "tree": _tree_snapshot(live_package)}
    (rollback / "rollback-manifest.json").write_bytes(canonical_json_bytes(rollback_manifest) + b"\n")


def _apply_plan(workspace: Path, plan: Mapping[str, Any], promoted: Mapping[str, bytes]) -> None:
    for entry in plan.get("files") or []:
        relative = str(entry.get("path") or "")
        target = workspace / relative
        current = target.read_bytes() if target.is_file() else None
        if _sha(current) != entry.get("beforeSha256"):
            raise WorkbenchPromotionRehearsalError(f"Repository drift blocks promotion at {relative}.")
        content = promoted[relative]
        if _sha(content) != entry.get("afterSha256"):
            raise WorkbenchPromotionRehearsalError(f"Promotion payload hash mismatch at {relative}.")
        target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(content)


def _verify_plan_after_hashes(workspace: Path, plan: Mapping[str, Any]) -> None:
    for entry in plan.get("files") or []:
        relative = str(entry.get("path") or "")
        if _sha((workspace / relative).read_bytes()) != entry.get("afterSha256"):
            raise WorkbenchPromotionRehearsalError(f"Promoted file hash mismatch at {relative}.")


def _verify_generated_ownership(package: Path) -> bool:
    ownership = _load_json(package / "mcel.generated.json", "generated ownership")
    if ownership.get("schema") != OWNERSHIP_SCHEMA or ownership.get("appId") != APP_ID:
        raise WorkbenchPromotionRehearsalError("Generated ownership identity is invalid.")
    expected = sorted(path for path in GENERATED_PATHS if path != "mcel.app.json")
    entries = {str(item.get("path")): item for item in ownership.get("generatedFiles") or [] if isinstance(item, Mapping)}
    if sorted(entries) != expected:
        raise WorkbenchPromotionRehearsalError("Generated ownership set is incomplete or unexpected.")
    for relative in expected:
        path = package / relative
        if not path.is_file() or _sha(path.read_bytes()) != entries[relative].get("sha256"):
            raise WorkbenchPromotionRehearsalError(f"Generated ownership drift at {relative}.")
    return True


def _run_promoted_authorities(repo: Path, workspace: Path, headed: bool, runner: Callable[..., subprocess.CompletedProcess[str]]) -> None:
    commands = [
        [sys.executable, str(repo / "tools/mcel_application_runtime_projection.py"), "--repo-root", str(workspace)],
        [sys.executable, str(repo / "tools/mcel_application_package_browser_catalog.py"), "--repo-root", str(workspace)],
        [sys.executable, str(repo / "main_computer/mcel_acceptance_runner.py"), "--repo-root", str(workspace), "--app", APP_ID, "--check"],
        [sys.executable, str(repo / "main_computer/mcel_application_observation_runner.py"), "--repo-root", str(workspace), "--app", APP_ID, "--check", *(["--headed"] if headed else [])],
        [sys.executable, str(repo / "main_computer/mcel_app_prove.py"), "--repo-root", str(workspace), "--app", APP_ID, "--reuse-evidence", "--check"],
    ]
    for command in commands:
        completed = runner(command, cwd=workspace, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False, timeout=900)
        if completed.returncode != 0:
            raise WorkbenchPromotionRehearsalError("Promoted authority failed: " + " ".join(command) + (f"\n{completed.stdout.strip()}" if completed.stdout else ""))


def _restore_live_package(workspace: Path, live_package: Path) -> None:
    target = workspace / DEFAULT_PACKAGE_ROOT
    if target.exists(): shutil.rmtree(target)
    shutil.copytree(live_package, target)


def _workspace_fingerprints(repo: Path) -> dict[str, Any]:
    catalog = build_application_package_catalog(repo)
    record = next((item for item in catalog.packages if item.app_id == APP_ID), None)
    if record is None:
        return {"packageValid": False, "package": None, "catalog": catalog.fingerprint, "runtimeProjection": None}
    projection = build_application_runtime_projection(repo, catalog, record)
    return {"packageValid": record.valid, "package": record.fingerprint, "catalog": catalog.fingerprint, "runtimeProjection": projection.fingerprint}


def _tree_snapshot(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink() and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"}:
            result[path.relative_to(root).as_posix()] = _sha(path.read_bytes()) or ""
    return result


def _protected_source_snapshot(repo: Path) -> dict[str, str]:
    result = {}
    roots = [repo / DEFAULT_PACKAGE_ROOT, repo / "tests/fixtures/mcel_dsl/contract-workbench.application.js", repo / "tests/fixtures/mcel_application_ir/contract-workbench.ir.json"]
    roots.extend(sorted((repo / "main_computer").glob("mcel_*")))
    roots.extend(sorted((repo / "tools").glob("mcel_*")))
    for root in roots:
        paths = root.rglob("*") if root.is_dir() else [root]
        for path in paths:
            if path.is_file() and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"}:
                result[path.relative_to(repo).as_posix()] = _sha(path.read_bytes()) or ""
    return result


def _base_report(dsl: Any, projection: Any, evidence: Mapping[str, Any]) -> dict[str, Any]:
    return {"schema": REPORT_SCHEMA, "version": REPORT_VERSION, "appId": APP_ID, "genericPipeline": True, "counterSpecificExecutionPathRequired": False, "candidate": {"semanticFingerprint": dsl.semantic_fingerprint, "sourceBindingFingerprint": dsl.source_binding_fingerprint, "projectionProfile": projection.report.get("projectionProfile"), "evidenceStatus": evidence.get("status"), "truthStatus": evidence.get("truthStatus")}}


def _authority(eligible: bool) -> dict[str, Any]:
    return {"liveAuthority": "legacy-explicit-package", "rehearsedAuthority": "mcel.dsl.v1", "candidatePromoted": False, "promotionExecuted": False, "promotionEligible": eligible, "externalEvidenceReused": False, "liveApplicationChanged": False}


def _write_reports(output: Path, report: Mapping[str, Any]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "mcel-app-promotion-rehearsal-report.json").write_bytes(canonical_json_bytes(report) + b"\n")
    lines = ["# Contract Workbench Promotion Rehearsal", "", f"- Status: `{report.get('status')}`", f"- Promotion eligible: `{str(bool(report.get('promotionEligible'))).lower()}`", f"- Post-promotion truth: `{report.get('postPromotionTruthStatus')}`", f"- Rollback restoration: `{report.get('rollbackRestoration')}`", ""]
    (output / "mcel-app-promotion-rehearsal-report.md").write_text("\n".join(lines), encoding="utf-8")


def _failure(status: str, diagnostics: list[Mapping[str, Any]], message: str) -> WorkbenchPromotionRehearsalResult:
    diagnostics.append(_diagnostic("MCEL_WORKBENCH_PROMOTION_REHEARSAL_SOURCE_INVALID", message, "$promotionRehearsal"))
    report = {"schema": REPORT_SCHEMA, "version": REPORT_VERSION, "appId": APP_ID, "status": status, "valid": False, "promotionRehearsal": "fail", "promotionEligible": False, "authority": _authority(False)}
    return WorkbenchPromotionRehearsalResult(False, status, report, tuple(diagnostics))


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try: value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc: raise WorkbenchPromotionRehearsalError(f"Could not load {label}: {exc}") from exc
    if not isinstance(value, dict): raise WorkbenchPromotionRehearsalError(f"{label} must be a JSON object.")
    return value


def _diagnostic(code: str, summary: str, semantic_path: str) -> dict[str, Any]:
    return {"schema": "mcel.compiler-diagnostic.v1", "code": code, "severity": "error", "blocking": True, "summary": summary, "problem": summary, "semanticPath": semantic_path}


def _sha(content: bytes | None) -> str | None:
    return "sha256:" + hashlib.sha256(content).hexdigest() if content is not None else None


def _resolve(repo: Path, path: Path) -> Path:
    return path if path.is_absolute() else repo / path


def _display(path: Path, repo: Path) -> str:
    try: return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError: return path.resolve().as_posix()
