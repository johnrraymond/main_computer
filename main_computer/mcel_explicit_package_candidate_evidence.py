"""Generic isolated evidence pipeline for explicit-package MCEL candidates.

Explicit-package apps materialize deterministic generated contracts inside an
application package.  This module owns the app-agnostic evidence mechanics:
build an isolated workspace, overlay the candidate package, run the existing
MCEL authorities there, aggregate stage proof, publish reports, and verify that
the live package stayed untouched.

App-specific wrappers provide only a profile plus domain-specific effect probes
and effect-accounting logic.
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

from main_computer.mcel_application_ir import canonical_json_bytes
from main_computer.mcel_application_packages import build_application_package_catalog
from main_computer.mcel_dsl_compiler import DEFAULT_CANDIDATE_ROOT, compile_dsl_application
from main_computer.mcel_evidence_provenance import build_repository_provenance


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_ROOT = Path("runtime/reports/mcel-compiler-candidates")
REPORT_SCHEMA = "mcel.explicit-package-candidate-evidence-report.v1"
REPORT_VERSION = "mcel-explicit-package-candidate-evidence-v1"


class ExplicitPackageCandidateEvidenceError(RuntimeError):
    """Raised when isolated candidate evidence cannot be produced truthfully."""


@dataclass(frozen=True)
class ExplicitPackageCandidateEvidenceResult:
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
            value.setdefault("artifacts", {})["outputDirectory"] = _display_path(
                self.output_directory, REPOSITORY_ROOT
            )
        return value


@dataclass(frozen=True)
class ExplicitPackageCandidateEvidenceProfile:
    app_id: str
    project_candidate: Callable[..., Any]
    build_effect_accounting: Callable[..., Mapping[str, Any]]
    default_dsl_source: Path
    default_fixture_ir: Path | None
    default_candidate_root: Path = DEFAULT_CANDIDATE_ROOT
    default_report_root: Path = DEFAULT_REPORT_ROOT
    report_schema: str = REPORT_SCHEMA
    report_version: str = REPORT_VERSION
    report_title: str = "MCEL Explicit Package Candidate Evidence"
    live_authority: str = "legacy-explicit-package"
    candidate_authority: str = "none"
    generated_artifacts_key: str = "contractsGeneratedInCandidate"
    effect_accounting_filename: str = "mcel-effect-accounting-report.json"
    node_probe_filename: str = "mcel-node-effect-probe.json"
    browser_probe_filename: str = "mcel-browser-effect-probe.json"
    invalid_dsl_message: str = "DSL compilation did not produce valid canonical IR."
    invalid_projection_message: str = "Candidate projection is not exact."
    evidence_failed_code: str = "MCEL_EXPLICIT_PACKAGE_CANDIDATE_EVIDENCE_FAILED"
    source_invalid_code: str = "MCEL_EXPLICIT_PACKAGE_CANDIDATE_EVIDENCE_SOURCE_INVALID"
    stage_failed_code: str = "MCEL_EXPLICIT_PACKAGE_CANDIDATE_STAGE_FAILED"
    live_changed_code: str = "MCEL_EXPLICIT_PACKAGE_LIVE_PACKAGE_CHANGED"
    live_changed_summary: str = "The live application package changed during isolated candidate evidence execution."
    report_filename: str = "mcel-candidate-evidence-report.json"
    report_markdown_filename: str = "mcel-candidate-evidence-report.md"


def run_explicit_package_candidate_evidence(
    profile: ExplicitPackageCandidateEvidenceProfile,
    *,
    repo_root: Path = REPOSITORY_ROOT,
    dsl_source_path: Path | None = None,
    fixture_ir_path: Path | None = None,
    candidate_root: Path | None = None,
    report_root: Path | None = None,
    headed: bool = False,
    write_report: bool = True,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    node_probe_runner: Callable[[Path], Mapping[str, Any]] | None = None,
    browser_probe_runner: Callable[[Path, bool], Mapping[str, Any]] | None = None,
) -> ExplicitPackageCandidateEvidenceResult:
    repo = repo_root.resolve()
    dsl_source_path = dsl_source_path or profile.default_dsl_source
    fixture_ir_path = profile.default_fixture_ir if fixture_ir_path is None else fixture_ir_path
    candidate_root = candidate_root or profile.default_candidate_root
    report_root = report_root or profile.default_report_root

    diagnostics: list[Mapping[str, Any]] = []
    live_package = repo / "mcel_apps" / profile.app_id
    live_before = _tree_fingerprint(live_package)

    dsl = compile_dsl_application(
        dsl_source_path,
        compare_ir_path=fixture_ir_path,
        candidate_root=candidate_root,
        write_candidate=True,
    )
    diagnostics.extend(dsl.diagnostics)
    if not dsl.valid or dsl.normalized_ir is None or not dsl.source_binding_fingerprint:
        return _failure(profile, "invalid-dsl", diagnostics, profile.invalid_dsl_message)

    projection = profile.project_candidate(
        dsl_source_path=dsl_source_path,
        fixture_ir_path=fixture_ir_path,
        live_package_root=live_package,
        candidate_root=candidate_root,
        write_candidate=True,
    )
    diagnostics.extend(projection.diagnostics)
    if not projection.valid or projection.candidate_directory is None:
        return _failure(profile, "invalid-projection", diagnostics, profile.invalid_projection_message)

    source_binding = dsl.source_binding_fingerprint.removeprefix("sha256:")
    candidate_directory = projection.candidate_directory.resolve()
    workspace = candidate_directory / "evidence-workspace"
    candidate_package = candidate_directory / "package" / "mcel_apps" / profile.app_id
    candidate_ir_path = candidate_directory / "mcel.application.ir.json"
    if not candidate_ir_path.is_file():
        candidate_ir_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_ir_path.write_bytes(canonical_json_bytes(dsl.normalized_ir) + b"\n")
    if not candidate_package.is_dir():
        return _failure(profile, "missing-candidate-package", diagnostics, "Candidate package is missing.")

    output_root = report_root if report_root.is_absolute() else repo / report_root
    output_directory = output_root / profile.app_id / source_binding
    if write_report and output_directory.exists():
        shutil.rmtree(output_directory)

    try:
        _prepare_workspace(repo, workspace, candidate_package, app_id=profile.app_id)
        _run_candidate_authorities(
            repo=repo,
            workspace=workspace,
            app_id=profile.app_id,
            headed=headed,
            command_runner=command_runner or _run_command,
        )
        acceptance = _load_json(
            workspace / f"runtime/reports/mcel-acceptance/apps/{profile.app_id}/mcel-acceptance-report.json",
            "candidate acceptance report",
        )
        observation = _load_json(
            workspace / f"runtime/reports/mcel-observation/apps/{profile.app_id}/mcel-operation-observation-report.json",
            "candidate observation report",
        )
        base_proof = _load_json(
            workspace / f"runtime/reports/mcel-app-proof/apps/{profile.app_id}/mcel-app-proof-report.json",
            "candidate application proof",
        )
        if node_probe_runner is None or browser_probe_runner is None:
            raise ExplicitPackageCandidateEvidenceError(
                "Explicit-package evidence requires app-specific node and browser effect probes."
            )
        node_probe = dict(node_probe_runner(workspace))
        browser_probe = dict(browser_probe_runner(workspace, headed))
        effect_accounting = dict(
            profile.build_effect_accounting(
                ir=dsl.normalized_ir,
                acceptance=acceptance,
                observation=observation,
                node_probe=node_probe,
                browser_probe=browser_probe,
            )
        )
    except ExplicitPackageCandidateEvidenceError as exc:
        diagnostics.append(_diagnostic(profile.evidence_failed_code, str(exc), "$candidateEvidence"))
        failure_report = _failure_report(
            profile=profile,
            repo=repo,
            candidate_directory=candidate_directory,
            workspace=workspace,
            dsl=dsl,
            candidate_ir_path=candidate_ir_path,
            error=str(exc),
        )
        if write_report:
            output_directory.mkdir(parents=True, exist_ok=True)
            _write_json(output_directory / profile.report_filename, failure_report)
            (output_directory / profile.report_markdown_filename).write_text(
                _render_markdown(profile, failure_report), encoding="utf-8"
            )
        return _result(False, "fail", diagnostics, failure_report, output_directory if write_report else None)

    workspace_catalog = build_application_package_catalog(workspace)
    candidate_record = next(
        (record for record in workspace_catalog.packages if record.app_id == profile.app_id),
        None,
    )
    workspace_provenance = build_repository_provenance(workspace)
    projection_report = _load_json(candidate_directory / "projection-report.json", "candidate projection report")

    stage_checks = {
        "candidateProjection": projection_report.get("status") == "exact",
        "packageValidation": bool(candidate_record and candidate_record.valid and candidate_record.fingerprint),
        "runtimeProjection": (base_proof.get("stages") or {}).get("generatedArtifacts", {}).get("status") == "pass",
        "acceptance": acceptance.get("status") == "pass" and acceptance.get("passed") is True,
        "browserObservation": observation.get("status") == "pass" and observation.get("ok") is True,
        "effectAccounting": effect_accounting.get("status") == "closed" and effect_accounting.get("valid") is True,
        "applicationProof": base_proof.get("status") == "pass" and base_proof.get("truthStatus") == "semantic-runtime-proven",
        "repositoryBinding": (base_proof.get("stages") or {}).get("repositoryBinding", {}).get("status") == "exact",
    }
    for stage, passed in stage_checks.items():
        if not passed:
            diagnostics.append(
                _diagnostic(
                    profile.stage_failed_code,
                    f"Candidate stage {stage} did not pass.",
                    f"$stages.{stage}",
                )
            )

    live_after = _tree_fingerprint(live_package)
    live_unchanged = live_before == live_after
    if not live_unchanged:
        diagnostics.append(
            _diagnostic(
                profile.live_changed_code,
                profile.live_changed_summary,
                "$authority.liveApplicationChanged",
            )
        )

    valid = all(stage_checks.values()) and live_unchanged and not any(
        item.get("blocking", True) for item in diagnostics
    )
    status = "pass" if valid else "fail"
    report: dict[str, Any] = {
        "schema": profile.report_schema,
        "version": profile.report_version,
        "appId": profile.app_id,
        "status": status,
        "valid": valid,
        "truthStatus": base_proof.get("truthStatus"),
        "candidate": {
            "directory": _display_path(candidate_directory, repo),
            "workspace": _display_path(workspace, repo),
            "semanticFingerprint": dsl.semantic_fingerprint,
            "sourceBindingFingerprint": dsl.source_binding_fingerprint,
            "irSha256": _sha256_path(candidate_ir_path),
            "packageFingerprint": candidate_record.fingerprint if candidate_record else None,
            "catalogFingerprint": workspace_catalog.fingerprint,
            "repositoryProvenance": workspace_provenance,
        },
        "stages": {
            stage: {"status": "pass" if passed else "fail"}
            for stage, passed in stage_checks.items()
        },
        "authority": _authority(
            profile,
            live_application_changed=not live_unchanged,
        ),
        "effectAccounting": effect_accounting,
        "baseApplicationProof": base_proof,
        "evidence": {},
    }

    if write_report:
        _publish_evidence(
            profile=profile,
            workspace=workspace,
            output_directory=output_directory,
            report=report,
            effect_accounting=effect_accounting,
            node_probe=node_probe,
            browser_probe=browser_probe,
        )
        report["evidence"] = _published_artifacts(profile, output_directory, repo)
        _write_json(output_directory / profile.report_filename, report)
        (output_directory / profile.report_markdown_filename).write_text(
            _render_markdown(profile, report), encoding="utf-8"
        )

    return _result(valid, status, diagnostics, report, output_directory if write_report else None)


def _prepare_workspace(repo: Path, workspace: Path, candidate_package: Path, *, app_id: str) -> None:
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    source_dirs = (
        "main_computer",
        "tools",
        "mcel_apps",
        "tests",
        "pretty_docs",
        "contracts",
        "deploy",
        "docker",
        "scripts",
        "game_projects",
    )
    for name in source_dirs:
        source = repo / name
        if source.is_dir():
            shutil.copytree(source, workspace / name, ignore=_copy_ignore)
    for path in repo.iterdir():
        if not path.is_file() or path.suffix.lower() in {".zip", ".pyc", ".pyo"}:
            continue
        shutil.copy2(path, workspace / path.name)
    target = workspace / "mcel_apps" / app_id
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(candidate_package, target, ignore=_copy_ignore)


def _copy_ignore(_directory: str, names: Sequence[str]) -> set[str]:
    ignored = {
        name
        for name in names
        if name in {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules", ".git"}
        or name.endswith((".pyc", ".pyo", ".zip"))
    }
    return ignored


def _run_candidate_authorities(
    *,
    repo: Path,
    workspace: Path,
    app_id: str,
    headed: bool,
    command_runner: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    commands = [
        [sys.executable, str(repo / "tools/mcel_application_runtime_projection.py"), "--repo-root", str(workspace)],
        [sys.executable, str(repo / "tools/mcel_application_package_browser_catalog.py"), "--repo-root", str(workspace)],
        [
            sys.executable,
            str(repo / "main_computer/mcel_acceptance_runner.py"),
            "--repo-root",
            str(workspace),
            "--app",
            app_id,
            "--check",
        ],
        [
            sys.executable,
            str(repo / "main_computer/mcel_application_observation_runner.py"),
            "--repo-root",
            str(workspace),
            "--app",
            app_id,
            "--check",
            *( ["--headed"] if headed else [] ),
        ],
        [
            sys.executable,
            str(repo / "main_computer/mcel_app_prove.py"),
            "--repo-root",
            str(workspace),
            "--app",
            app_id,
            "--reuse-evidence",
            "--check",
        ],
    ]
    for command in commands:
        completed = command_runner(
            command,
            cwd=workspace,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=900,
        )
        if completed.returncode != 0:
            raise ExplicitPackageCandidateEvidenceError(
                "Candidate authority failed: "
                + " ".join(command)
                + (f"\n{completed.stdout.strip()}" if completed.stdout else "")
            )


def _run_command(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(*args, **kwargs)


def _publish_evidence(
    *,
    profile: ExplicitPackageCandidateEvidenceProfile,
    workspace: Path,
    output_directory: Path,
    report: Mapping[str, Any],
    effect_accounting: Mapping[str, Any],
    node_probe: Mapping[str, Any],
    browser_probe: Mapping[str, Any],
) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    mappings = (
        (
            workspace / f"runtime/reports/mcel-acceptance/apps/{profile.app_id}",
            output_directory / "acceptance",
        ),
        (
            workspace / f"runtime/reports/mcel-observation/apps/{profile.app_id}",
            output_directory / "observation",
        ),
        (
            workspace / f"runtime/reports/mcel-app-proof/apps/{profile.app_id}",
            output_directory / "proof",
        ),
    )
    for source, target in mappings:
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
    effects = output_directory / "effects"
    effects.mkdir(parents=True, exist_ok=True)
    _write_json(effects / profile.effect_accounting_filename, effect_accounting)
    _write_json(effects / profile.node_probe_filename, node_probe)
    _write_json(effects / profile.browser_probe_filename, browser_probe)


def _published_artifacts(
    profile: ExplicitPackageCandidateEvidenceProfile,
    output_directory: Path,
    repo: Path,
) -> dict[str, Any]:
    paths = {
        "acceptance": output_directory / "acceptance/mcel-acceptance-report.json",
        "browserObservation": output_directory / "observation/mcel-operation-observation-report.json",
        "effectAccounting": output_directory / f"effects/{profile.effect_accounting_filename}",
        "applicationProof": output_directory / "proof/mcel-app-proof-report.json",
    }
    return {
        name: {
            "path": _display_path(path, repo),
            "sha256": _sha256_path(path),
        }
        for name, path in paths.items()
    }


def _failure_report(
    *,
    profile: ExplicitPackageCandidateEvidenceProfile,
    repo: Path,
    candidate_directory: Path,
    workspace: Path,
    dsl: Any,
    candidate_ir_path: Path,
    error: str,
) -> dict[str, Any]:
    return {
        "schema": profile.report_schema,
        "version": profile.report_version,
        "appId": profile.app_id,
        "status": "fail",
        "valid": False,
        "truthStatus": None,
        "candidate": {
            "directory": _display_path(candidate_directory, repo),
            "workspace": _display_path(workspace, repo),
            "semanticFingerprint": dsl.semantic_fingerprint,
            "sourceBindingFingerprint": dsl.source_binding_fingerprint,
            "irSha256": _sha256_path(candidate_ir_path),
        },
        "stages": {
            "candidateProjection": {"status": "pass"},
            "packageValidation": {"status": "not-completed"},
            "runtimeProjection": {"status": "not-completed"},
            "acceptance": {"status": "not-completed"},
            "browserObservation": {"status": "fail" if "observation" in error.lower() or "browser" in error.lower() else "not-completed"},
            "effectAccounting": {"status": "not-completed"},
            "applicationProof": {"status": "not-completed"},
            "repositoryBinding": {"status": "not-completed"},
        },
        "authority": _authority(profile, live_application_changed=False),
        "error": error,
    }


def _authority(
    profile: ExplicitPackageCandidateEvidenceProfile,
    *,
    live_application_changed: bool,
) -> dict[str, Any]:
    return {
        "liveAuthority": profile.live_authority,
        "candidateAuthority": profile.candidate_authority,
        "liveApplicationChanged": live_application_changed,
        profile.generated_artifacts_key: True,
        "evidenceReused": False,
        "candidatePromoted": False,
        "promotionEligible": False,
    }


def _tree_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink() or "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return "sha256:" + digest.hexdigest()


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExplicitPackageCandidateEvidenceError(f"Could not load {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ExplicitPackageCandidateEvidenceError(f"{label} must be a JSON object: {path}")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256_path(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _display_path(path: Path, repo: Path = REPOSITORY_ROOT) -> str:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _diagnostic(code: str, summary: str, semantic_path: str, **extra: Any) -> dict[str, Any]:
    value = {
        "schema": "mcel.compiler-diagnostic.v1",
        "code": code,
        "severity": "error",
        "blocking": True,
        "summary": summary,
        "semanticPath": semantic_path,
    }
    value.update(extra)
    return value


def _failure(
    profile: ExplicitPackageCandidateEvidenceProfile,
    status: str,
    diagnostics: list[Mapping[str, Any]],
    message: str,
) -> ExplicitPackageCandidateEvidenceResult:
    diagnostics.append(_diagnostic(profile.source_invalid_code, message, "$candidate"))
    return _result(False, status, diagnostics, {})


def _result(
    valid: bool,
    status: str,
    diagnostics: list[Mapping[str, Any]],
    report: Mapping[str, Any],
    output_directory: Path | None = None,
) -> ExplicitPackageCandidateEvidenceResult:
    return ExplicitPackageCandidateEvidenceResult(valid, status, report, tuple(diagnostics), output_directory)


def _render_markdown(profile: ExplicitPackageCandidateEvidenceProfile, report: Mapping[str, Any]) -> str:
    lines = [
        f"# {profile.report_title}",
        "",
        f"- Status: **{report.get('status')}**",
        f"- Truth status: `{report.get('truthStatus')}`",
        f"- Semantic fingerprint: `{(report.get('candidate') or {}).get('semanticFingerprint', '')}`",
        f"- Source-binding fingerprint: `{(report.get('candidate') or {}).get('sourceBindingFingerprint', '')}`",
        "",
        "## Stages",
        "",
    ]
    for stage, value in (report.get("stages") or {}).items():
        lines.append(f"- {stage}: `{value.get('status')}`")
    lines.extend(
        [
            "",
            "## Authority",
            "",
            f"- Live authority: `{(report.get('authority') or {}).get('liveAuthority')}`",
            f"- Candidate promoted: `{str((report.get('authority') or {}).get('candidatePromoted')).lower()}`",
            f"- Evidence reused: `{str((report.get('authority') or {}).get('evidenceReused')).lower()}`",
            f"- Promotion eligible: `{str((report.get('authority') or {}).get('promotionEligible')).lower()}`",
            "",
        ]
    )
    return "\n".join(lines)
