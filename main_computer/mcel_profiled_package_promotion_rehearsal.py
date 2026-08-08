"""Generic promotion rehearsal for profiled-package MCEL candidates.

Profiled-package apps materialize generated package files by applying a
deterministic projection profile to canonical MCEL IR.  This module owns the
app-agnostic rehearsal mechanics: build the exact promotion plan, stage
promotion/rollback material, apply it only inside a disposable workspace, rerun
standard MCEL authorities there, prove rollback is exact, and verify that the
live repository was not mutated.

App-specific wrappers should provide only a profile: app identity, source
defaults, projection/evidence hooks, report labels, expected proof counts, and
generated artifact inventory.
"""

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
from main_computer.mcel_profiled_package_candidate_evidence import (
    DEFAULT_REPORT_ROOT as DEFAULT_EVIDENCE_REPORT_ROOT,
    _prepare_workspace,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_ROOT = Path("runtime/reports/mcel-compiler-candidates")
REPORT_SCHEMA = "mcel.profiled-package-promotion-rehearsal-report.v1"
REPORT_VERSION = "mcel-profiled-package-promotion-rehearsal-v1"
PLAN_SCHEMA = "mcel.application-promotion-plan.v1"
OWNERSHIP_SCHEMA = "mcel.generated-file-ownership.v1"


class ProfiledPackagePromotionRehearsalError(RuntimeError):
    """Raised when profiled-package promotion rehearsal cannot complete."""


@dataclass(frozen=True)
class ProfiledPackagePromotionRehearsalResult:
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


@dataclass(frozen=True)
class ProfiledPackagePromotionRehearsalProfile:
    app_id: str
    project_candidate: Callable[..., Any]
    run_candidate_evidence: Callable[..., Any]
    generated_paths: tuple[str, ...]
    projection_profile: str
    default_dsl_source: Path
    default_fixture_ir: Path
    default_package_root: Path
    default_candidate_root: Path = DEFAULT_CANDIDATE_ROOT
    default_evidence_report_root: Path = DEFAULT_EVIDENCE_REPORT_ROOT
    default_report_root: Path = DEFAULT_REPORT_ROOT
    report_schema: str = REPORT_SCHEMA
    report_version: str = REPORT_VERSION
    plan_schema: str = PLAN_SCHEMA
    ownership_schema: str = OWNERSHIP_SCHEMA
    report_filename: str = "mcel-profiled-package-promotion-rehearsal-report.json"
    report_markdown_filename: str = "mcel-profiled-package-promotion-rehearsal-report.md"
    report_title: str = "Profiled Package Promotion Rehearsal"
    live_authority: str = "legacy-explicit-package"
    rehearsed_authority: str = "mcel.dsl.v1"
    generated_authority: str | None = None
    expected_intent_count: int = 0
    expected_scenario_count: int = 0
    expected_effect_count: int = 0
    expected_capability_count: int = 0
    promotion_support_files: tuple[str, ...] = ()
    protected_source_roots: tuple[Path, ...] = ()
    source_invalid_code: str = "MCEL_PROFILED_PACKAGE_PROMOTION_REHEARSAL_SOURCE_INVALID"
    evidence_invalid_code: str = "MCEL_PROFILED_PACKAGE_PROMOTION_EVIDENCE_INVALID"
    evidence_binding_conflict_code: str = "MCEL_PROFILED_PACKAGE_PROMOTION_EVIDENCE_BINDING_CONFLICT"
    rehearsal_failed_code: str = "MCEL_PROFILED_PACKAGE_PROMOTION_REHEARSAL_FAILED"
    mutated_live_repo_code: str = "MCEL_PROFILED_PACKAGE_PROMOTION_REHEARSAL_MUTATED_LIVE_REPOSITORY"
    diagnostic_schema: str = "mcel.compiler-diagnostic.v1"

    @property
    def generated_authority_label(self) -> str:
        return self.generated_authority or self.projection_profile


def rehearse_profiled_package_promotion(
    profile: ProfiledPackagePromotionRehearsalProfile,
    *,
    repo_root: Path = REPOSITORY_ROOT,
    dsl_source_path: Path | None = None,
    fixture_ir_path: Path | None = None,
    candidate_root: Path | None = None,
    evidence_report_root: Path | None = None,
    report_root: Path | None = None,
    headed: bool = False,
    write_report: bool = True,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    evidence_runner: Callable[..., Any] | None = None,
) -> ProfiledPackagePromotionRehearsalResult:
    repo = repo_root.resolve()
    diagnostics: list[Mapping[str, Any]] = []
    live_package = repo / profile.default_package_root
    package_before = _tree_snapshot(live_package)
    protected_before = _protected_source_snapshot(repo, profile)

    source = _resolve(repo, dsl_source_path or profile.default_dsl_source)
    fixture = _resolve(repo, fixture_ir_path or profile.default_fixture_ir)
    candidates = _resolve(repo, candidate_root or profile.default_candidate_root)
    evidence_root = _resolve(repo, evidence_report_root or profile.default_evidence_report_root)
    reports = _resolve(repo, report_root or profile.default_report_root)

    dsl = compile_dsl_application(source, compare_ir_path=fixture)
    diagnostics.extend(dsl.diagnostics)
    if not dsl.valid or dsl.normalized_ir is None or dsl.comparison_status != "exact" or not dsl.source_binding_fingerprint:
        return _failure(profile, "invalid-dsl", diagnostics, f"Native {profile.app_id} DSL is not exact.")

    projection = profile.project_candidate(
        dsl_source_path=source,
        fixture_ir_path=fixture,
        live_package_root=live_package,
        candidate_root=candidates,
        write_candidate=True,
    )
    diagnostics.extend(projection.diagnostics)
    if not projection.valid or projection.candidate_directory is None:
        return _failure(profile, "invalid-candidate", diagnostics, f"{profile.app_id} candidate projection is not exact.")

    evidence_call = evidence_runner or profile.run_candidate_evidence
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
        diagnostics.append(_diagnostic(profile, profile.evidence_invalid_code, f"The isolated {profile.app_id} candidate has not earned semantic-runtime-proven evidence.", "$candidateEvidence"))
        return _failure(profile, "invalid-evidence", diagnostics, "Passing isolated candidate evidence is required.")
    if (
        candidate_evidence.get("semanticFingerprint") != dsl.semantic_fingerprint
        or candidate_evidence.get("sourceBindingFingerprint") != dsl.source_binding_fingerprint
    ):
        diagnostics.append(_diagnostic(profile, profile.evidence_binding_conflict_code, f"Candidate evidence is not bound to the exact {profile.app_id} DSL candidate.", "$candidateEvidence"))
        return _failure(profile, "stale-evidence", diagnostics, "Exact candidate evidence is required.")

    candidate_dir = projection.candidate_directory.resolve()
    candidate_package = candidate_dir / "package" / profile.default_package_root
    rehearsal_state = candidate_dir / "promotion-rehearsal"
    output = reports / profile.app_id / str(dsl.source_binding_fingerprint).removeprefix("sha256:") / "promotion-rehearsal"
    workspace = rehearsal_state / "workspace"

    try:
        plan, promoted = _build_promotion_plan(
            profile,
            live_package=live_package,
            candidate_package=candidate_package,
            dsl_source=source,
            semantic_fingerprint=str(dsl.semantic_fingerprint),
            source_binding_fingerprint=str(dsl.source_binding_fingerprint),
            evidence=evidence_payload,
        )
        _stage_material(profile, rehearsal_state, plan, promoted, live_package)
        _prepare_workspace(repo, workspace, live_package, profile.default_package_root)
        pre = _workspace_fingerprints(workspace, profile)
        _apply_plan(workspace, plan, promoted)
        _verify_plan_after_hashes(workspace, plan)
        _verify_generated_ownership(profile, workspace / profile.default_package_root)

        promoted_compile = compile_dsl_application(
            workspace / profile.default_package_root / "application.js",
            compare_ir_path=fixture,
        )
        if not promoted_compile.valid or promoted_compile.normalized_ir is None or promoted_compile.comparison_status != "exact":
            raise ProfiledPackagePromotionRehearsalError(f"Promoted {profile.app_id} DSL does not compile exactly in the isolated repository.")
        semantic_comparison = compare_application_ir(dsl.normalized_ir, promoted_compile.normalized_ir)
        if semantic_comparison.get("status") != "exact":
            raise ProfiledPackagePromotionRehearsalError(f"Promoted {profile.app_id} semantic identity changed.")

        _run_promoted_authorities(profile, repo, workspace, headed, command_runner or subprocess.run)
        proof = _load_json(workspace / f"runtime/reports/mcel-app-proof/apps/{profile.app_id}/mcel-app-proof-report.json", f"post-promotion {profile.app_id} proof")
        intent = proof.get("intentCoverage") or {}
        effects = intent.get("effectAccounting") or {}
        capabilities = intent.get("capabilityAccounting") or {}
        post = _workspace_fingerprints(workspace, profile)
        checks = {
            "dslCompilation": promoted_compile.valid and promoted_compile.comparison_status == "exact",
            "semanticCompatibility": semantic_comparison.get("status") == "exact",
            "generatedOwnership": _verify_generated_ownership(profile, workspace / profile.default_package_root),
            "packageValidation": bool(post.get("packageValid")),
            "acceptance": (proof.get("stages") or {}).get("acceptanceEvidence", {}).get("status") == "pass",
            "browserObservation": (proof.get("stages") or {}).get("browserObservation", {}).get("status") == "pass",
            "applicationProof": proof.get("status") == "pass" and proof.get("truthStatus") == "semantic-runtime-proven",
            "repositoryBinding": (proof.get("stages") or {}).get("repositoryBinding", {}).get("status") == "exact",
            "intentCompleteness": intent.get("status") == "ir-native" and intent.get("coveredIntentCount") == profile.expected_intent_count,
            "scenarioCompleteness": intent.get("observedScenarioCount") == profile.expected_scenario_count,
            "effectAccounting": effects.get("status") == "closed" and effects.get("declaredEffectCount") == profile.expected_effect_count,
            "capabilityAccounting": capabilities.get("status") == "closed" and capabilities.get("declaredCapabilityCount") == profile.expected_capability_count,
        }
        if not all(checks.values()):
            raise ProfiledPackagePromotionRehearsalError("Post-promotion checks failed: " + ", ".join(key for key, value in checks.items() if not value))

        _restore_live_package(workspace, live_package, profile.default_package_root)
        rollback = _workspace_fingerprints(workspace, profile)
        rollback_exact = (
            _tree_snapshot(workspace / profile.default_package_root) == package_before
            and rollback.get("package") == pre.get("package")
            and rollback.get("catalog") == pre.get("catalog")
            and rollback.get("runtimeProjection") == pre.get("runtimeProjection")
        )
        if not rollback_exact:
            raise ProfiledPackagePromotionRehearsalError(f"Rollback did not restore the original {profile.app_id} package exactly.")
    except (OSError, ValueError, ProfiledPackagePromotionRehearsalError) as exc:
        diagnostics.append(_diagnostic(profile, profile.rehearsal_failed_code, str(exc), "$promotionRehearsal"))
        report = _base_report(profile, dsl, projection, evidence_payload)
        report.update({"status": "fail", "valid": False, "promotionRehearsal": "fail", "rollbackRehearsal": "not-completed", "rollbackRestoration": "not-completed", "promotionEligible": False, "authority": _authority(profile, False), "error": str(exc)})
        if write_report:
            _write_reports(profile, output, report)
        return ProfiledPackagePromotionRehearsalResult(False, "fail", report, tuple(diagnostics), output if write_report else None)

    package_after = _tree_snapshot(live_package)
    protected_after = _protected_source_snapshot(repo, profile)
    live_changed = package_after != package_before or protected_after != protected_before
    if live_changed:
        diagnostics.append(_diagnostic(profile, profile.mutated_live_repo_code, "The non-mutating rehearsal changed protected live sources.", "$authority.liveApplicationChanged"))

    valid = not live_changed and not diagnostics
    report = _base_report(profile, dsl, projection, evidence_payload)
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
        "postPromotion": {
            "checks": {key: {"status": "pass" if value else "fail"} for key, value in checks.items()},
            "fingerprints": post,
            "intentCoverage": {
                "declaredIntents": profile.expected_intent_count,
                "coveredIntents": profile.expected_intent_count,
                "declaredScenarios": profile.expected_scenario_count,
                "observedScenarios": profile.expected_scenario_count,
            },
            "effectAccounting": effects,
            "capabilityAccounting": capabilities,
        },
        "rollback": {"status": "pass", "restoration": "exact", "fingerprints": rollback},
        "authority": _authority(profile, valid),
        "artifacts": {
            "promotionMaterial": _display(rehearsal_state / "promotion", repo),
            "rollbackMaterial": _display(rehearsal_state / "rollback", repo),
            "workspace": _display(workspace, repo),
        },
    })
    if write_report:
        _write_reports(profile, output, report)
    return ProfiledPackagePromotionRehearsalResult(valid, report["status"], report, tuple(diagnostics), output if write_report else None)


def _build_promotion_plan(
    profile: ProfiledPackagePromotionRehearsalProfile,
    *,
    live_package: Path,
    candidate_package: Path,
    dsl_source: Path,
    semantic_fingerprint: str,
    source_binding_fingerprint: str,
    evidence: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, bytes]]:
    promoted: dict[str, bytes] = {}
    root = profile.default_package_root.as_posix()
    promoted[f"{root}/application.js"] = dsl_source.read_bytes()
    generated_paths = sorted(path for path in profile.generated_paths if path != "mcel.app.json")
    for relative in generated_paths:
        promoted[f"{root}/{relative}"] = (candidate_package / relative).read_bytes()

    manifest = _load_json(live_package / "mcel.app.json", f"live {profile.app_id} package manifest")
    manifest["authoring"] = {
        "schema": "mcel.application-authoring.v1",
        "status": "dsl-authoritative",
        "source": "application.js",
        "ownership": "mcel.generated.json",
        "normalizedDefinition": "generated/mcel.application.normalized.json",
    }
    manifest["projection"] = {"profile": profile.projection_profile, "generatedArtifactsAreDerived": True}
    promoted[f"{root}/mcel.app.json"] = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    ownership = {
        "schema": profile.ownership_schema,
        "version": profile.report_version,
        "appId": profile.app_id,
        "manualEditsProhibited": True,
        "generatedArtifactsAreDerived": True,
        "sourceAuthority": {
            "kind": "mcel.dsl.v1",
            "path": "application.js",
            "semanticFingerprint": semantic_fingerprint,
            "sourceBindingFingerprint": source_binding_fingerprint,
        },
        "generatedFiles": [
            {"path": relative, "sha256": _sha(promoted[f"{root}/{relative}"]), "generator": profile.projection_profile}
            for relative in generated_paths
        ],
    }
    promoted[f"{root}/mcel.generated.json"] = canonical_json_bytes(ownership) + b"\n"

    files = []
    for relative, content in sorted(promoted.items()):
        current = live_package.parent.parent / relative
        before = current.read_bytes() if current.is_file() else None
        package_relative = relative.removeprefix(root + "/")
        files.append({
            "path": relative,
            "action": "replace" if before is not None else "add",
            "beforeSha256": _sha(before),
            "afterSha256": _sha(content),
            "byteChange": before != content,
            "generated": package_relative in generated_paths,
            "transitionSupport": package_relative in profile.promotion_support_files,
        })
    plan = {
        "schema": profile.plan_schema,
        "version": profile.report_version,
        "appId": profile.app_id,
        "promotionExecuted": False,
        "sourceAuthorityBefore": profile.live_authority,
        "sourceAuthorityAfter": "mcel.dsl.v1",
        "derivedArtifactAuthorityAfter": profile.generated_authority_label,
        "semanticFingerprint": semantic_fingerprint,
        "sourceBindingFingerprint": source_binding_fingerprint,
        "candidateEvidenceBinding": {
            "truthStatus": evidence.get("truthStatus"),
            "semanticFingerprint": (evidence.get("candidate") or {}).get("semanticFingerprint"),
            "sourceBindingFingerprint": (evidence.get("candidate") or {}).get("sourceBindingFingerprint"),
            "evidenceReused": False,
        },
        "files": files,
    }
    return plan, promoted


def _stage_material(
    profile: ProfiledPackagePromotionRehearsalProfile,
    root: Path,
    plan: Mapping[str, Any],
    promoted: Mapping[str, bytes],
    live_package: Path,
) -> None:
    if root.exists():
        shutil.rmtree(root)
    promotion = root / "promotion"
    rollback = root / "rollback"
    promotion.mkdir(parents=True)
    rollback.mkdir(parents=True)
    (promotion / "promotion-plan.json").write_bytes(canonical_json_bytes(plan) + b"\n")
    for relative, content in promoted.items():
        target = promotion / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    shutil.copytree(live_package, rollback / profile.default_package_root)
    rollback_manifest = {
        "schema": "mcel.application-promotion-rollback.v1",
        "appId": profile.app_id,
        "restores": profile.default_package_root.as_posix(),
        "tree": _tree_snapshot(live_package),
    }
    (rollback / "rollback-manifest.json").write_bytes(canonical_json_bytes(rollback_manifest) + b"\n")


def _apply_plan(workspace: Path, plan: Mapping[str, Any], promoted: Mapping[str, bytes]) -> None:
    for entry in plan.get("files") or []:
        relative = str(entry.get("path") or "")
        target = workspace / relative
        current = target.read_bytes() if target.is_file() else None
        if _sha(current) != entry.get("beforeSha256"):
            raise ProfiledPackagePromotionRehearsalError(f"Repository drift blocks promotion at {relative}.")
        content = promoted[relative]
        if _sha(content) != entry.get("afterSha256"):
            raise ProfiledPackagePromotionRehearsalError(f"Promotion payload hash mismatch at {relative}.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def _verify_plan_after_hashes(workspace: Path, plan: Mapping[str, Any]) -> None:
    for entry in plan.get("files") or []:
        relative = str(entry.get("path") or "")
        if _sha((workspace / relative).read_bytes()) != entry.get("afterSha256"):
            raise ProfiledPackagePromotionRehearsalError(f"Promoted file hash mismatch at {relative}.")


def _verify_generated_ownership(profile: ProfiledPackagePromotionRehearsalProfile, package: Path) -> bool:
    ownership = _load_json(package / "mcel.generated.json", "generated ownership")
    if ownership.get("schema") != profile.ownership_schema or ownership.get("appId") != profile.app_id:
        raise ProfiledPackagePromotionRehearsalError("Generated ownership identity is invalid.")
    expected = sorted(path for path in profile.generated_paths if path != "mcel.app.json")
    entries = {str(item.get("path")): item for item in ownership.get("generatedFiles") or [] if isinstance(item, Mapping)}
    if sorted(entries) != expected:
        raise ProfiledPackagePromotionRehearsalError("Generated ownership set is incomplete or unexpected.")
    for relative in expected:
        path = package / relative
        if not path.is_file() or _sha(path.read_bytes()) != entries[relative].get("sha256"):
            raise ProfiledPackagePromotionRehearsalError(f"Generated ownership drift at {relative}.")
    return True


def _run_promoted_authorities(
    profile: ProfiledPackagePromotionRehearsalProfile,
    repo: Path,
    workspace: Path,
    headed: bool,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    commands = [
        [sys.executable, str(repo / "tools/mcel_application_runtime_projection.py"), "--repo-root", str(workspace)],
        [sys.executable, str(repo / "tools/mcel_application_package_browser_catalog.py"), "--repo-root", str(workspace)],
        [sys.executable, str(repo / "main_computer/mcel_acceptance_runner.py"), "--repo-root", str(workspace), "--app", profile.app_id, "--check"],
        [sys.executable, str(repo / "main_computer/mcel_application_observation_runner.py"), "--repo-root", str(workspace), "--app", profile.app_id, "--check", *(["--headed"] if headed else [])],
        [sys.executable, str(repo / "main_computer/mcel_app_prove.py"), "--repo-root", str(workspace), "--app", profile.app_id, "--reuse-evidence", "--check"],
    ]
    for command in commands:
        completed = runner(command, cwd=workspace, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False, timeout=900)
        if completed.returncode != 0:
            raise ProfiledPackagePromotionRehearsalError("Promoted authority failed: " + " ".join(command) + (f"\n{completed.stdout.strip()}" if completed.stdout else ""))


def _restore_live_package(workspace: Path, live_package: Path, package_root: Path) -> None:
    target = workspace / package_root
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(live_package, target)


def _workspace_fingerprints(repo: Path, profile: ProfiledPackagePromotionRehearsalProfile) -> dict[str, Any]:
    catalog = build_application_package_catalog(repo)
    record = next((item for item in catalog.packages if item.app_id == profile.app_id), None)
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


def _protected_source_snapshot(repo: Path, profile: ProfiledPackagePromotionRehearsalProfile) -> dict[str, str]:
    result: dict[str, str] = {}
    roots = [repo / profile.default_package_root, repo / profile.default_dsl_source, repo / profile.default_fixture_ir]
    roots.extend(repo / path for path in profile.protected_source_roots)
    roots.extend(sorted((repo / "main_computer").glob("mcel_*")))
    roots.extend(sorted((repo / "tools").glob("mcel_*")))
    for root in roots:
        paths = root.rglob("*") if root.is_dir() else [root]
        for path in paths:
            if path.is_file() and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"}:
                result[path.relative_to(repo).as_posix()] = _sha(path.read_bytes()) or ""
    return result


def _base_report(profile: ProfiledPackagePromotionRehearsalProfile, dsl: Any, projection: Any, evidence: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": profile.report_schema,
        "version": profile.report_version,
        "appId": profile.app_id,
        "genericPipeline": True,
        "counterSpecificExecutionPathRequired": False,
        "candidate": {
            "semanticFingerprint": dsl.semantic_fingerprint,
            "sourceBindingFingerprint": dsl.source_binding_fingerprint,
            "projectionProfile": projection.report.get("projectionProfile"),
            "evidenceStatus": evidence.get("status"),
            "truthStatus": evidence.get("truthStatus"),
        },
    }


def _authority(profile: ProfiledPackagePromotionRehearsalProfile, eligible: bool) -> dict[str, Any]:
    return {
        "liveAuthority": profile.live_authority,
        "rehearsedAuthority": profile.rehearsed_authority,
        "candidatePromoted": False,
        "promotionExecuted": False,
        "promotionEligible": eligible,
        "externalEvidenceReused": False,
        "liveApplicationChanged": False,
    }


def _write_reports(profile: ProfiledPackagePromotionRehearsalProfile, output: Path, report: Mapping[str, Any]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / profile.report_filename).write_bytes(canonical_json_bytes(report) + b"\n")
    lines = [
        f"# {profile.report_title}",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Promotion eligible: `{str(bool(report.get('promotionEligible'))).lower()}`",
        f"- Post-promotion truth: `{report.get('postPromotionTruthStatus')}`",
        f"- Rollback restoration: `{report.get('rollbackRestoration')}`",
        "",
    ]
    (output / profile.report_markdown_filename).write_text("\n".join(lines), encoding="utf-8")


def _failure(
    profile: ProfiledPackagePromotionRehearsalProfile,
    status: str,
    diagnostics: list[Mapping[str, Any]],
    message: str,
) -> ProfiledPackagePromotionRehearsalResult:
    diagnostics.append(_diagnostic(profile, profile.source_invalid_code, message, "$promotionRehearsal"))
    report = {
        "schema": profile.report_schema,
        "version": profile.report_version,
        "appId": profile.app_id,
        "status": status,
        "valid": False,
        "promotionRehearsal": "fail",
        "promotionEligible": False,
        "authority": _authority(profile, False),
    }
    return ProfiledPackagePromotionRehearsalResult(False, status, report, tuple(diagnostics))


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfiledPackagePromotionRehearsalError(f"Could not load {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProfiledPackagePromotionRehearsalError(f"{label} must be a JSON object.")
    return value


def _diagnostic(
    profile: ProfiledPackagePromotionRehearsalProfile,
    code: str,
    summary: str,
    semantic_path: str,
) -> dict[str, Any]:
    return {
        "schema": profile.diagnostic_schema,
        "code": code,
        "severity": "error",
        "blocking": True,
        "summary": summary,
        "problem": summary,
        "semanticPath": semantic_path,
    }


def _sha(content: bytes | None) -> str | None:
    return "sha256:" + hashlib.sha256(content).hexdigest() if content is not None else None


def _resolve(repo: Path, path: Path) -> Path:
    return path if path.is_absolute() else repo / path


def _display(path: Path, repo: Path) -> str:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()
