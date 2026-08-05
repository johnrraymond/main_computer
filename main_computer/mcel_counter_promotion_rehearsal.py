"""Non-mutating Counter DSL authority-transition and rollback rehearsal.

Wave 6 binds the independently proven Wave 5 candidate to an exact promotion
plan, applies that plan only inside a disposable repository copy, reruns the
existing semantic/runtime authorities there, and restores the original package
byte-for-byte.  It never changes the live repository or promotes the candidate.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from main_computer.mcel_application_ir import canonical_json_bytes, compare_application_ir
from main_computer.mcel_application_packages import build_application_package_catalog
from main_computer.mcel_application_runtime_projection import build_application_runtime_projection
from main_computer.mcel_counter_candidate_evidence import (
    DEFAULT_REPORT_ROOT as DEFAULT_EVIDENCE_REPORT_ROOT,
    _build_effect_accounting,
    _load_json,
    _prepare_workspace,
    _run_browser_effect_probe,
    _run_candidate_authorities,
    _run_command,
    _run_counter_effect_probe,
    run_counter_candidate_evidence,
)
from main_computer.mcel_counter_candidate_projection import (
    GENERATED_CONTRACTS,
    project_counter_candidate,
)
from main_computer.mcel_counter_compatibility import (
    DEFAULT_DSL_SOURCE,
    DEFAULT_FIXTURE_IR,
    compare_counter_representations,
)
from main_computer.mcel_counter_legacy_importer import import_counter_legacy_package
from main_computer.mcel_dsl_compiler import DEFAULT_CANDIDATE_ROOT, compile_dsl_application

REPORT_SCHEMA = "mcel.counter-promotion-rehearsal-report.v1"
REPORT_VERSION = "mcel-counter-promotion-rehearsal-wave6"
PLAN_SCHEMA = "mcel.counter-promotion-plan.v1"
OWNERSHIP_SCHEMA = "mcel.generated-file-ownership.v1"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_ROOT = Path("runtime/reports/mcel-compiler-candidates")


class CounterPromotionRehearsalError(RuntimeError):
    """Raised when the promotion rehearsal cannot truthfully complete."""


@dataclass(frozen=True)
class CounterPromotionRehearsalResult:
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


def rehearse_counter_promotion(
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
    node_probe_runner: Callable[[Path], Mapping[str, Any]] | None = None,
    browser_probe_runner: Callable[[Path, bool], Mapping[str, Any]] | None = None,
) -> CounterPromotionRehearsalResult:
    repo = repo_root.resolve()
    diagnostics: list[Mapping[str, Any]] = []
    live_package = repo / "mcel_apps" / "contract-counter"
    live_before = _tree_snapshot(live_package)
    source_before = _source_tree_snapshot(repo)
    authority_source_before = _promotion_authority_source_snapshot(repo)

    dsl = compile_dsl_application(dsl_source_path, compare_ir_path=fixture_ir_path)
    diagnostics.extend(dsl.diagnostics)
    if not dsl.valid or dsl.normalized_ir is None or not dsl.source_binding_fingerprint:
        return _failure("invalid-dsl", diagnostics, "Authoritative Counter DSL is not valid.")

    projection = project_counter_candidate(
        dsl_source_path=dsl_source_path,
        fixture_ir_path=fixture_ir_path,
        live_package_root=live_package,
        candidate_root=candidate_root,
        write_candidate=True,
    )
    diagnostics.extend(projection.diagnostics)
    if not projection.valid or projection.candidate_directory is None:
        return _failure("invalid-candidate", diagnostics, "Wave 4 candidate projection is not exact.")

    run_evidence = evidence_runner or run_counter_candidate_evidence
    evidence = run_evidence(
        repo_root=repo,
        dsl_source_path=dsl_source_path,
        fixture_ir_path=fixture_ir_path,
        candidate_root=candidate_root,
        report_root=evidence_report_root,
        headed=headed,
        write_report=True,
        command_runner=command_runner,
        node_probe_runner=node_probe_runner,
        browser_probe_runner=browser_probe_runner,
    )
    evidence_payload = evidence.to_dict() if hasattr(evidence, "to_dict") else dict(evidence)
    evidence_valid = bool(getattr(evidence, "valid", evidence_payload.get("valid")))
    if not evidence_valid or evidence_payload.get("truthStatus") != "semantic-runtime-proven":
        diagnostics.append(_diagnostic(
            "MCEL_COUNTER_PROMOTION_EVIDENCE_INVALID",
            "The exact candidate has not independently earned semantic-runtime-proven evidence.",
            "$candidateEvidence",
        ))
        return _failure("invalid-evidence", diagnostics, "Candidate evidence is not promotion-rehearsal eligible.")

    evidence_candidate = evidence_payload.get("candidate") or {}
    if (
        evidence_candidate.get("semanticFingerprint") != dsl.semantic_fingerprint
        or evidence_candidate.get("sourceBindingFingerprint") != dsl.source_binding_fingerprint
    ):
        diagnostics.append(_diagnostic(
            "MCEL_COUNTER_PROMOTION_EVIDENCE_BINDING_CONFLICT",
            "Candidate evidence is not bound to the exact DSL semantic and source-binding fingerprints.",
            "$candidateEvidence.candidate",
        ))
        return _failure("stale-evidence", diagnostics, "Candidate evidence binding is stale or conflicting.")

    candidate_directory = projection.candidate_directory.resolve()
    candidate_package = candidate_directory / "package" / "mcel_apps" / "contract-counter"
    source_binding = dsl.source_binding_fingerprint.removeprefix("sha256:")
    rehearsal_root = candidate_directory / "promotion-rehearsal"
    workspace = rehearsal_root / "workspace"
    promotion_root = rehearsal_root / "promotion"
    rollback_root = rehearsal_root / "rollback"
    output_base = report_root if report_root.is_absolute() else repo / report_root
    output_directory = output_base / "contract-counter" / source_binding / "promotion-rehearsal"

    try:
        plan, promoted_files = _build_promotion_plan(
            repo=repo,
            live_package=live_package,
            candidate_package=candidate_package,
            dsl_source_path=dsl_source_path,
            semantic_fingerprint=str(dsl.semantic_fingerprint),
            source_binding_fingerprint=str(dsl.source_binding_fingerprint),
            evidence_payload=evidence_payload,
        )
        _stage_material(plan, promoted_files, live_package, promotion_root, rollback_root)
        _prepare_workspace(repo, workspace, live_package)
        pre = _workspace_fingerprints(workspace)
        _apply_plan(workspace, plan, promoted_files)
        _verify_promoted_ownership(workspace)

        compatibility = compare_counter_representations(
            package_root=workspace / "mcel_apps" / "contract-counter",
            fixture_ir_path=fixture_ir_path,
            dsl_source_path=workspace / "mcel_apps" / "contract-counter" / "application.js",
            write_report=False,
        )
        if not compatibility.valid:
            raise CounterPromotionRehearsalError("Post-promotion compatibility is not exact.")

        runner = command_runner or _run_command
        _run_candidate_authorities(repo=repo, workspace=workspace, headed=headed, command_runner=runner)
        acceptance = _load_json(
            workspace / "runtime/reports/mcel-acceptance/apps/contract-counter/mcel-acceptance-report.json",
            "promotion rehearsal acceptance report",
        )
        observation = _load_json(
            workspace / "runtime/reports/mcel-observation/apps/contract-counter/mcel-operation-observation-report.json",
            "promotion rehearsal observation report",
        )
        proof = _load_json(
            workspace / "runtime/reports/mcel-app-proof/apps/contract-counter/mcel-app-proof-report.json",
            "promotion rehearsal application proof",
        )
        node_probe = dict((node_probe_runner or _run_counter_effect_probe)(workspace))
        browser_probe = dict((browser_probe_runner or _run_browser_effect_probe)(workspace, headed))
        effects = _build_effect_accounting(
            ir=dsl.normalized_ir,
            acceptance=acceptance,
            observation=observation,
            node_probe=node_probe,
            browser_probe=browser_probe,
        )
        imported = import_counter_legacy_package(workspace / "mcel_apps" / "contract-counter")
        semantic_comparison = (
            compare_application_ir(dsl.normalized_ir, imported.normalized_ir)
            if imported.valid and imported.normalized_ir is not None
            else {"status": "invalid"}
        )
        post = _workspace_fingerprints(workspace)

        post_checks = {
            "compatibility": compatibility.status == "exact",
            "packageValidation": bool(post.get("packageValid")),
            "acceptance": acceptance.get("status") == "pass" and acceptance.get("passed") is True,
            "browserObservation": observation.get("status") == "pass" and observation.get("ok") is True,
            "effectAccounting": effects.get("status") == "closed" and effects.get("valid") is True,
            "applicationProof": proof.get("status") == "pass" and proof.get("truthStatus") == "semantic-runtime-proven",
            "repositoryBinding": (proof.get("stages") or {}).get("repositoryBinding", {}).get("status") == "exact",
            "semanticRoundtrip": semantic_comparison.get("status") == "exact",
            "ownership": True,
        }
        if not all(post_checks.values()):
            failed = [name for name, value in post_checks.items() if not value]
            raise CounterPromotionRehearsalError("Post-promotion authority checks failed: " + ", ".join(failed))

        _rollback_plan(workspace, plan, live_package)
        rollback = _workspace_fingerprints(workspace)
        rollback_exact = (
            _tree_snapshot(workspace / "mcel_apps" / "contract-counter") == live_before
            and rollback.get("package") == pre.get("package")
            and rollback.get("catalog") == pre.get("catalog")
            and rollback.get("runtimeProjection") == pre.get("runtimeProjection")
        )
        if not rollback_exact:
            raise CounterPromotionRehearsalError("Rollback did not restore the original Counter package fingerprints exactly.")
    except (CounterPromotionRehearsalError, OSError, ValueError) as exc:
        diagnostics.append(_diagnostic(
            "MCEL_COUNTER_PROMOTION_REHEARSAL_FAILED", str(exc), "$promotionRehearsal"
        ))
        report = _base_report(dsl, projection, evidence_payload)
        report.update({
            "status": "fail",
            "valid": False,
            "promotionRehearsal": "fail",
            "rollbackRehearsal": "not-completed",
            "promotionEligible": False,
            "error": str(exc),
            "authority": _authority(False),
        })
        if write_report:
            output_directory.mkdir(parents=True, exist_ok=True)
            _write_report(output_directory, report, diagnostics)
        return CounterPromotionRehearsalResult(False, "fail", report, tuple(diagnostics), output_directory if write_report else None)

    live_after = _tree_snapshot(live_package)
    source_after = _source_tree_snapshot(repo)
    authority_source_after = _promotion_authority_source_snapshot(repo)
    source_changes = _snapshot_changes(source_before, source_after)
    authority_source_changes = _snapshot_changes(authority_source_before, authority_source_after)
    unrelated_source_changes = sorted(set(source_changes) - set(authority_source_changes))
    live_package_unchanged = live_after == live_before
    live_unchanged = live_package_unchanged and not authority_source_changes
    if not live_unchanged:
        changed = authority_source_changes or ["mcel_apps/contract-counter"]
        diagnostics.append(_diagnostic(
            "MCEL_COUNTER_PROMOTION_REHEARSAL_MUTATED_LIVE_REPOSITORY",
            "The non-mutating rehearsal changed protected Counter/MCEL authority source paths: "
            + ", ".join(changed),
            "$authority.liveApplicationChanged",
        ))

    valid = live_unchanged and not any(item.get("blocking", True) for item in diagnostics)
    report = _base_report(dsl, projection, evidence_payload)
    report.update({
        "status": "pass" if valid else "fail",
        "valid": valid,
        "promotionRehearsal": "pass",
        "rollbackRehearsal": "pass" if rollback_exact else "fail",
        "rollbackRestoration": "exact" if rollback_exact else "conflicting",
        "postPromotionTruthStatus": proof.get("truthStatus"),
        "promotionEligible": valid,
        "plan": plan,
        "postPromotion": {
            "checks": {name: {"status": "pass" if value else "fail"} for name, value in post_checks.items()},
            "fingerprints": post,
            "semanticComparison": semantic_comparison,
            "effectAccounting": effects,
        },
        "rollback": {
            "status": "pass" if rollback_exact else "fail",
            "restoration": "exact" if rollback_exact else "conflicting",
            "fingerprints": rollback,
        },
        "authority": _authority(valid, live_application_changed=not live_unchanged),
        "repositoryObservation": {
            "scope": "counter-and-shared-mcel-authority-sources",
            "livePackageUnchanged": live_package_unchanged,
            "protectedSourceChanges": authority_source_changes,
            "unrelatedSourceChangesObserved": unrelated_source_changes,
        },
        "artifacts": {
            "workspace": _display_path(workspace, repo),
            "promotionMaterial": _display_path(promotion_root, repo),
            "rollbackMaterial": _display_path(rollback_root, repo),
        },
    })
    if write_report:
        output_directory.mkdir(parents=True, exist_ok=True)
        _write_report(output_directory, report, diagnostics)
    return CounterPromotionRehearsalResult(valid, report["status"], report, tuple(diagnostics), output_directory if write_report else None)


def _build_promotion_plan(
    *,
    repo: Path,
    live_package: Path,
    candidate_package: Path,
    dsl_source_path: Path,
    semantic_fingerprint: str,
    source_binding_fingerprint: str,
    evidence_payload: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, bytes]]:
    dsl_bytes = dsl_source_path.read_bytes()
    generated: dict[str, bytes] = {
        relative: (candidate_package / relative).read_bytes() for relative in GENERATED_CONTRACTS
    }
    ownership = {
        "schema": OWNERSHIP_SCHEMA,
        "version": REPORT_VERSION,
        "appId": "contract-counter",
        "sourceAuthority": {
            "kind": "mcel.dsl.v1",
            "path": "application.js",
            "semanticFingerprint": semantic_fingerprint,
            "sourceBindingFingerprint": source_binding_fingerprint,
        },
        "generatedArtifactsAreDerived": True,
        "manualEditsProhibited": True,
        "generatedFiles": [
            {
                "path": relative,
                "sha256": _sha(generated[relative]),
                "generator": "mcel.counter.explicit-projection.v1",
            }
            for relative in sorted(generated)
        ],
    }
    ownership_bytes = canonical_json_bytes(ownership) + b"\n"
    manifest_path = live_package / "mcel.app.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["authoring"] = {
        "schema": "mcel.application-authoring.v1",
        "status": "dsl-authoritative",
        "source": "application.js",
        "ownership": "mcel.generated.json",
    }
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=False, ensure_ascii=False) + "\n").encode("utf-8")
    promoted = {
        "mcel_apps/contract-counter/application.js": dsl_bytes,
        "mcel_apps/contract-counter/mcel.generated.json": ownership_bytes,
        "mcel_apps/contract-counter/mcel.app.json": manifest_bytes,
        **{f"mcel_apps/contract-counter/{relative}": content for relative, content in generated.items()},
    }
    files: list[dict[str, Any]] = []
    for relative, content in sorted(promoted.items()):
        current = repo / relative
        before = current.read_bytes() if current.is_file() else None
        files.append({
            "path": relative,
            "action": "add" if before is None else "replace",
            "beforeSha256": _sha(before),
            "afterSha256": _sha(content),
            "byteChange": before != content,
            "generated": relative.endswith(".js") and "/contracts/" in relative,
        })
    evidence_candidate = evidence_payload.get("candidate") or {}
    plan = {
        "schema": PLAN_SCHEMA,
        "version": REPORT_VERSION,
        "appId": "contract-counter",
        "sourceAuthorityBefore": "legacy-explicit-package",
        "sourceAuthorityAfter": "mcel.dsl.v1",
        "derivedArtifactAuthorityAfter": "mcel.counter.explicit-projection.v1",
        "semanticFingerprint": semantic_fingerprint,
        "sourceBindingFingerprint": source_binding_fingerprint,
        "candidateEvidenceBinding": {
            "truthStatus": evidence_payload.get("truthStatus"),
            "semanticFingerprint": evidence_candidate.get("semanticFingerprint"),
            "sourceBindingFingerprint": evidence_candidate.get("sourceBindingFingerprint"),
            "evidenceReused": (evidence_payload.get("authority") or {}).get("evidenceReused"),
        },
        "preconditions": {
            "repositoryPackageFingerprint": _package_fingerprint(repo),
            "candidateProjectionStatus": "exact",
            "candidateEvidenceStatus": "pass",
            "candidateTruthStatus": "semantic-runtime-proven",
        },
        "files": files,
        "postPromotionCommands": [
            "python tools/mcel_counter_compatibility.py --write-report",
            "python tools/mcel_application_runtime_projection.py --check",
            "python main_computer/mcel_acceptance_runner.py --app contract-counter --check",
            "python main_computer/mcel_application_observation_runner.py --app contract-counter --check",
            "python main_computer/mcel_app_prove.py --app contract-counter --reuse-evidence --check",
        ],
        "promotionExecuted": False,
    }
    return plan, promoted


def _stage_material(
    plan: Mapping[str, Any], promoted: Mapping[str, bytes], live_package: Path,
    promotion_root: Path, rollback_root: Path,
) -> None:
    if promotion_root.exists():
        shutil.rmtree(promotion_root)
    if rollback_root.exists():
        shutil.rmtree(rollback_root)
    for relative, content in promoted.items():
        target = promotion_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    rollback_manifest: list[dict[str, Any]] = []
    repo = live_package.parents[1]
    for item in plan["files"]:
        relative = str(item["path"])
        source = repo / relative
        rollback_manifest.append({
            "path": relative,
            "action": "delete" if not source.exists() else "restore",
            "sha256": item.get("beforeSha256"),
        })
        if source.is_file():
            target = rollback_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
    (rollback_root / "rollback-manifest.json").parent.mkdir(parents=True, exist_ok=True)
    (rollback_root / "rollback-manifest.json").write_bytes(canonical_json_bytes({
        "schema": "mcel.counter-promotion-rollback.v1",
        "files": rollback_manifest,
    }) + b"\n")
    (promotion_root / "promotion-plan.json").write_bytes(canonical_json_bytes(plan) + b"\n")


def _apply_plan(workspace: Path, plan: Mapping[str, Any], promoted: Mapping[str, bytes]) -> None:
    for item in plan["files"]:
        relative = str(item["path"])
        path = workspace / relative
        current = path.read_bytes() if path.is_file() else None
        if _sha(current) != item.get("beforeSha256"):
            raise CounterPromotionRehearsalError(f"Repository drift blocks promotion rehearsal at {relative}.")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(promoted[relative])
        if _sha(path.read_bytes()) != item.get("afterSha256"):
            raise CounterPromotionRehearsalError(f"Promotion payload integrity failed at {relative}.")


def _rollback_plan(workspace: Path, plan: Mapping[str, Any], live_package: Path) -> None:
    repo = live_package.parents[1]
    for item in reversed(list(plan["files"])):
        relative = str(item["path"])
        target = workspace / relative
        original = repo / relative
        if original.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(original.read_bytes())
        elif target.exists():
            target.unlink()


def _verify_promoted_ownership(workspace: Path) -> None:
    package = workspace / "mcel_apps" / "contract-counter"
    manifest = json.loads((package / "mcel.app.json").read_text(encoding="utf-8"))
    authoring = manifest.get("authoring") or {}
    if authoring.get("status") != "dsl-authoritative" or authoring.get("source") != "application.js":
        raise CounterPromotionRehearsalError("Promoted manifest does not declare DSL source authority.")
    ownership = json.loads((package / "mcel.generated.json").read_text(encoding="utf-8"))
    if ownership.get("schema") != OWNERSHIP_SCHEMA:
        raise CounterPromotionRehearsalError("Generated ownership manifest schema is invalid.")
    for item in ownership.get("generatedFiles") or []:
        path = package / str(item.get("path"))
        if not path.is_file() or _sha(path.read_bytes()) != item.get("sha256"):
            raise CounterPromotionRehearsalError(f"Generated ownership hash mismatch: {item.get('path')}")


def _workspace_fingerprints(workspace: Path) -> dict[str, Any]:
    catalog = build_application_package_catalog(workspace)
    record = next((item for item in catalog.packages if item.app_id == "contract-counter"), None)
    runtime = build_application_runtime_projection(workspace, catalog, record) if record and record.valid else None
    imported = import_counter_legacy_package(workspace / "mcel_apps" / "contract-counter")
    return {
        "package": record.fingerprint if record else None,
        "packageValid": bool(record and record.valid),
        "catalog": catalog.fingerprint,
        "runtimeProjection": runtime.fingerprint if runtime else None,
        "semantic": imported.semantic_fingerprint,
    }


def _package_fingerprint(repo: Path) -> str | None:
    catalog = build_application_package_catalog(repo)
    record = next((item for item in catalog.packages if item.app_id == "contract-counter"), None)
    return record.fingerprint if record else None


def _tree_snapshot(root: Path) -> dict[str, str]:
    if not root.is_dir():
        return {}
    return {
        path.relative_to(root).as_posix(): _sha(path.read_bytes()) or ""
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"}
    }


def _source_tree_snapshot(repo: Path) -> dict[str, str]:
    ignored_roots = {"runtime", ".git", ".pytest_cache", "__pycache__", "node_modules"}
    snapshot: dict[str, str] = {}
    for path in sorted(repo.rglob("*")):
        if not path.is_file():
            continue
        relative_path = path.relative_to(repo)
        if any(part in ignored_roots for part in relative_path.parts):
            continue
        if path.suffix in {".pyc", ".pyo", ".zip"}:
            continue
        snapshot[relative_path.as_posix()] = _sha(path.read_bytes()) or ""
    return snapshot


def _promotion_authority_source_snapshot(repo: Path) -> dict[str, str]:
    full = _source_tree_snapshot(repo)
    protected: dict[str, str] = {}
    exact_paths = {
        "tests/fixtures/mcel_dsl/contract-counter.application.js",
        "tests/fixtures/mcel_application_ir/contract-counter.ir.json",
    }
    prefixes = (
        "mcel_apps/contract-counter/",
        "main_computer/mcel_",
        "tools/mcel_",
        "main_computer/web/applications/mcel-packages/contract-counter/",
        "main_computer/web/applications/scripts/mcel-",
    )
    for relative, digest in full.items():
        if relative in exact_paths or relative.startswith(prefixes):
            protected[relative] = digest
    return protected


def _snapshot_changes(before: Mapping[str, str], after: Mapping[str, str]) -> list[str]:
    return sorted(
        relative
        for relative in set(before) | set(after)
        if before.get(relative) != after.get(relative)
    )


def _base_report(dsl: Any, projection: Any, evidence: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "version": REPORT_VERSION,
        "appId": "contract-counter",
        "candidate": {
            "semanticFingerprint": dsl.semantic_fingerprint,
            "sourceBindingFingerprint": dsl.source_binding_fingerprint,
            "projectionStatus": projection.status,
            "evidenceStatus": evidence.get("status"),
            "truthStatus": evidence.get("truthStatus"),
        },
    }


def _authority(eligible: bool, *, live_application_changed: bool = False) -> dict[str, Any]:
    return {
        "liveAuthority": "legacy-explicit-package",
        "rehearsedAuthority": "mcel.dsl.v1",
        "liveApplicationChanged": live_application_changed,
        "promotionExecuted": False,
        "candidatePromoted": False,
        "externalEvidenceReused": False,
        "promotionEligible": eligible,
    }


def _write_report(output: Path, report: Mapping[str, Any], diagnostics: Sequence[Mapping[str, Any]]) -> None:
    payload = dict(report)
    payload["diagnosticCount"] = len(diagnostics)
    payload["diagnostics"] = [dict(item) for item in diagnostics]
    (output / "mcel-counter-promotion-rehearsal-report.json").write_bytes(canonical_json_bytes(payload) + b"\n")
    lines = [
        "# Counter Promotion Rehearsal",
        "",
        f"- Status: `{payload.get('status')}`",
        f"- Promotion rehearsal: `{payload.get('promotionRehearsal')}`",
        f"- Post-promotion truth: `{payload.get('postPromotionTruthStatus')}`",
        f"- Rollback rehearsal: `{payload.get('rollbackRehearsal')}`",
        f"- Rollback restoration: `{payload.get('rollbackRestoration')}`",
        f"- Promotion eligible: `{str(payload.get('promotionEligible')).lower()}`",
        "",
        (
            "Protected Counter/MCEL authority sources changed; no promotion was executed."
            if (payload.get("authority") or {}).get("liveApplicationChanged")
            else "Protected Counter/MCEL authority sources were unchanged and no promotion was executed."
        ),
        "",
    ]
    unrelated = (payload.get("repositoryObservation") or {}).get("unrelatedSourceChangesObserved") or []
    if unrelated:
        lines.extend([
            "Unrelated repository source changes were observed but were not attributed to the Counter promotion boundary:",
            "",
            *[f"- `{path}`" for path in unrelated],
            "",
        ])
    (output / "mcel-counter-promotion-rehearsal-report.md").write_text("\n".join(lines), encoding="utf-8")


def _failure(status: str, diagnostics: list[Mapping[str, Any]], message: str) -> CounterPromotionRehearsalResult:
    if not diagnostics:
        diagnostics.append(_diagnostic("MCEL_COUNTER_PROMOTION_REHEARSAL_UNAVAILABLE", message, "$promotionRehearsal"))
    report = {
        "schema": REPORT_SCHEMA, "version": REPORT_VERSION, "appId": "contract-counter",
        "valid": False, "status": status, "promotionEligible": False, "authority": _authority(False),
    }
    return CounterPromotionRehearsalResult(False, status, report, tuple(diagnostics))


def _diagnostic(code: str, summary: str, semantic_path: str) -> dict[str, Any]:
    return {
        "schema": "mcel.compiler-diagnostic.v1", "code": code, "severity": "error",
        "blocking": True, "stage": "promotion-rehearsal", "semanticPath": semantic_path,
        "summary": summary, "problem": summary,
    }


def _sha(content: bytes | None) -> str | None:
    return "sha256:" + hashlib.sha256(content).hexdigest() if content is not None else None


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()
