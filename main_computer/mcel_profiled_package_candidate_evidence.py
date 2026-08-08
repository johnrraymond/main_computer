"""Generic isolated evidence pipeline for profiled-package MCEL candidates.

Profiled-package apps materialize generated package files by applying a
deterministic projection profile to canonical MCEL IR.  This module owns the
app-agnostic evidence mechanics: create an isolated workspace, overlay the
candidate package, run the standard MCEL authorities, aggregate stage proof,
publish reports, and verify that the live package stayed untouched.

App-specific wrappers should provide only a profile: app identity, source
defaults, projection hook, report labels, expected proof counts, and optional
migration-debt/report shaping callbacks.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from main_computer.mcel_application_ir import canonical_json_bytes
from main_computer.mcel_dsl_compiler import DEFAULT_CANDIDATE_ROOT


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_ROOT = Path("runtime/reports/mcel-compiler-candidates")
REPORT_SCHEMA = "mcel.profiled-package-candidate-evidence-report.v1"
REPORT_VERSION = "mcel-profiled-package-candidate-evidence-v1"


class ProfiledPackageCandidateEvidenceError(RuntimeError):
    """Raised when isolated profiled-package evidence cannot be produced."""


@dataclass(frozen=True)
class ProfiledPackageCandidateEvidenceResult:
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
        if self.output_directory:
            value["artifacts"] = {
                "reports": _display(self.output_directory, REPOSITORY_ROOT)
            }
        return value


@dataclass(frozen=True)
class ProfiledPackageCandidateEvidenceProfile:
    app_id: str
    project_candidate: Callable[..., Any]
    default_dsl_source: Path
    default_fixture_ir: Path | None
    default_package_root: Path
    default_candidate_root: Path = DEFAULT_CANDIDATE_ROOT
    default_report_root: Path = DEFAULT_REPORT_ROOT
    report_schema: str = REPORT_SCHEMA
    report_version: str = REPORT_VERSION
    report_title: str = "Profiled Package Candidate Evidence"
    truth_status: str = "semantic-runtime-proven"
    expected_intent_count: int | None = None
    expected_scenario_count: int | None = None
    live_authority: str = "legacy-explicit-package"
    candidate_authority: str = "none"
    projection_invalid_status: str = "invalid-projection"
    invalid_projection_message: str = "Candidate projection is not exact."
    evidence_failed_code: str = "MCEL_PROFILED_PACKAGE_CANDIDATE_EVIDENCE_FAILED"
    source_invalid_code: str = "MCEL_PROFILED_PACKAGE_CANDIDATE_EVIDENCE_SOURCE_INVALID"
    stage_failed_code: str = "MCEL_PROFILED_PACKAGE_CANDIDATE_STAGE_FAILED"
    live_changed_code: str = "MCEL_PROFILED_PACKAGE_LIVE_PACKAGE_CHANGED"
    live_changed_summary: str = (
        "The live application package changed during isolated candidate proof."
    )
    report_filename: str = "mcel-candidate-evidence-report.json"
    report_markdown_filename: str = "mcel-candidate-evidence-report.md"
    authority_commands: tuple[Callable[[Path, Path, bool], list[str]], ...] = field(
        default_factory=tuple
    )
    migration_debt: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None
    candidate_extra: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None
    report_extra: Callable[[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]] | None = None


def run_profiled_package_candidate_evidence(
    profile: ProfiledPackageCandidateEvidenceProfile,
    *,
    repo_root: Path = REPOSITORY_ROOT,
    dsl_source_path: Path | None = None,
    fixture_ir_path: Path | None = None,
    candidate_root: Path | None = None,
    report_root: Path | None = None,
    headed: bool = False,
    write_report: bool = False,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> ProfiledPackageCandidateEvidenceResult:
    repo = repo_root.resolve()
    dsl_source_path = dsl_source_path or profile.default_dsl_source
    fixture_ir_path = profile.default_fixture_ir if fixture_ir_path is None else fixture_ir_path
    candidate_root = candidate_root or profile.default_candidate_root
    report_root = report_root or profile.default_report_root

    diagnostics: list[Mapping[str, Any]] = []
    live_package = repo / profile.default_package_root
    live_before = _tree_fingerprint(live_package)

    projection_kwargs: dict[str, Any] = {
        "dsl_source_path": dsl_source_path,
        "live_package_root": live_package,
        "candidate_root": repo / candidate_root if not candidate_root.is_absolute() else candidate_root,
        "write_candidate": True,
    }
    if fixture_ir_path is not None:
        projection_kwargs["fixture_ir_path"] = fixture_ir_path

    projection = profile.project_candidate(**projection_kwargs)
    diagnostics.extend(projection.diagnostics)
    if not projection.valid or projection.candidate_directory is None:
        return _failure(
            profile,
            profile.projection_invalid_status,
            diagnostics,
            profile.invalid_projection_message,
        )

    candidate_directory = projection.candidate_directory.resolve()
    candidate_package = candidate_directory / "package" / profile.default_package_root
    workspace = candidate_directory / "evidence-workspace"
    source_binding = str(
        (projection.report.get("source") or {}).get("sourceBindingFingerprint") or ""
    ).removeprefix("sha256:")
    output_root = report_root if report_root.is_absolute() else repo / report_root
    output_directory = output_root / profile.app_id / source_binding
    if write_report and output_directory.exists():
        shutil.rmtree(output_directory)

    try:
        _prepare_workspace(repo, workspace, candidate_package, profile.default_package_root)
        _run_authorities(profile, repo, workspace, headed, command_runner or subprocess.run)
        acceptance = _load_json(
            workspace / f"runtime/reports/mcel-acceptance/apps/{profile.app_id}/mcel-acceptance-report.json",
            "candidate acceptance",
        )
        observation = _load_json(
            workspace / f"runtime/reports/mcel-observation/apps/{profile.app_id}/mcel-operation-observation-report.json",
            "candidate observation",
        )
        proof = _load_json(
            workspace / f"runtime/reports/mcel-app-proof/apps/{profile.app_id}/mcel-app-proof-report.json",
            "candidate application proof",
        )
    except ProfiledPackageCandidateEvidenceError as exc:
        diagnostics.append(
            _diagnostic(
                profile.evidence_failed_code,
                str(exc),
                "$candidateEvidence",
            )
        )
        report = _base_failure_report(profile, projection, candidate_directory, workspace, str(exc))
        if write_report:
            _write_reports(profile, output_directory, report)
        return ProfiledPackageCandidateEvidenceResult(
            False,
            "fail",
            report,
            tuple(diagnostics),
            output_directory if write_report else None,
        )

    intent = proof.get("intentCoverage") or {}
    stages = proof.get("stages") or {}
    stage_checks = _stage_checks(profile, projection, acceptance, observation, proof, intent, stages)
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

    valid = (
        all(stage_checks.values())
        and live_unchanged
        and not any(item.get("blocking", True) for item in diagnostics)
    )

    report = _evidence_report(
        profile,
        projection,
        candidate_directory,
        workspace,
        proof,
        intent,
        stage_checks,
        live_unchanged,
        valid,
    )
    if write_report:
        _publish(profile, workspace, output_directory, report)
    return ProfiledPackageCandidateEvidenceResult(
        valid,
        str(report["status"]),
        report,
        tuple(diagnostics),
        output_directory if write_report else None,
    )


def _default_authority_commands(
    profile: ProfiledPackageCandidateEvidenceProfile,
) -> tuple[Callable[[Path, Path, bool], list[str]], ...]:
    def application_definition(repo: Path, workspace: Path, _headed: bool) -> list[str]:
        return [
            sys.executable,
            str(repo / "tools/mcel_application_definition.py"),
            "--repo-root",
            str(workspace),
            "--app",
            profile.app_id,
            "--check",
        ]

    def runtime_projection(repo: Path, workspace: Path, _headed: bool) -> list[str]:
        return [
            sys.executable,
            str(repo / "tools/mcel_application_runtime_projection.py"),
            "--repo-root",
            str(workspace),
        ]

    def browser_catalog(repo: Path, workspace: Path, _headed: bool) -> list[str]:
        return [
            sys.executable,
            str(repo / "tools/mcel_application_package_browser_catalog.py"),
            "--repo-root",
            str(workspace),
        ]

    def acceptance(repo: Path, workspace: Path, _headed: bool) -> list[str]:
        return [
            sys.executable,
            str(repo / "main_computer/mcel_acceptance_runner.py"),
            "--repo-root",
            str(workspace),
            "--app",
            profile.app_id,
            "--check",
        ]

    def observation(repo: Path, workspace: Path, headed: bool) -> list[str]:
        command = [
            sys.executable,
            str(repo / "main_computer/mcel_application_observation_runner.py"),
            "--repo-root",
            str(workspace),
            "--app",
            profile.app_id,
            "--check",
        ]
        if headed:
            command.append("--headed")
        return command

    def app_proof(repo: Path, workspace: Path, _headed: bool) -> list[str]:
        return [
            sys.executable,
            str(repo / "main_computer/mcel_app_prove.py"),
            "--repo-root",
            str(workspace),
            "--app",
            profile.app_id,
            "--reuse-evidence",
            "--check",
        ]

    return (
        application_definition,
        runtime_projection,
        browser_catalog,
        acceptance,
        observation,
        app_proof,
    )


def _stage_checks(
    profile: ProfiledPackageCandidateEvidenceProfile,
    projection: Any,
    acceptance: Mapping[str, Any],
    observation: Mapping[str, Any],
    proof: Mapping[str, Any],
    intent: Mapping[str, Any],
    stages: Mapping[str, Any],
) -> dict[str, bool]:
    intent_count_ok = intent.get("passed") is True
    if profile.expected_intent_count is not None:
        intent_count_ok = (
            intent_count_ok
            and int(intent.get("declaredIntentCount") or 0) == profile.expected_intent_count
            and int(intent.get("coveredIntentCount") or 0) == profile.expected_intent_count
        )
    scenario_count_ok = True
    if profile.expected_scenario_count is not None:
        scenario_count_ok = (
            int(intent.get("declaredScenarioCount") or 0) == profile.expected_scenario_count
            and int(intent.get("observedScenarioCount") or 0) == profile.expected_scenario_count
        )
    return {
        "candidateProjection": projection.status == "exact",
        "acceptance": acceptance.get("status") == "pass" and acceptance.get("passed") is True,
        "browserObservation": observation.get("status") == "pass" and observation.get("ok") is True,
        "applicationProof": proof.get("status") == "pass"
        and proof.get("truthStatus") == profile.truth_status,
        "repositoryBinding": (stages.get("repositoryBinding") or {}).get("status") == "exact",
        "intentCompleteness": intent_count_ok,
        "scenarioCompleteness": scenario_count_ok,
    }


def _evidence_report(
    profile: ProfiledPackageCandidateEvidenceProfile,
    projection: Any,
    candidate_directory: Path,
    workspace: Path,
    proof: Mapping[str, Any],
    intent: Mapping[str, Any],
    stage_checks: Mapping[str, bool],
    live_unchanged: bool,
    valid: bool,
) -> dict[str, Any]:
    source = projection.report.get("source") or {}
    candidate: dict[str, Any] = {
        "directory": _display(candidate_directory, REPOSITORY_ROOT),
        "workspace": _display(workspace, REPOSITORY_ROOT),
        "semanticFingerprint": source.get("semanticFingerprint"),
        "sourceBindingFingerprint": source.get("sourceBindingFingerprint"),
        "projectionProfile": projection.report.get("projectionProfile"),
    }
    if profile.candidate_extra is not None:
        candidate.update(dict(profile.candidate_extra(projection.report)))

    report: dict[str, Any] = {
        "schema": profile.report_schema,
        "version": profile.report_version,
        "appId": profile.app_id,
        "status": "pass" if valid else "fail",
        "valid": valid,
        "truthStatus": proof.get("truthStatus"),
        "genericPipeline": True,
        "counterSpecificExecutionPathRequired": False,
        "candidate": candidate,
        "stages": {name: {"status": "pass" if passed else "fail"} for name, passed in stage_checks.items()},
        "intentCoverage": {
            "mode": intent.get("coverageMode"),
            "declaredIntents": intent.get("declaredIntentCount"),
            "coveredIntents": intent.get("coveredIntentCount"),
            "declaredScenarios": intent.get("declaredScenarioCount"),
            "observedScenarios": intent.get("observedScenarioCount"),
        },
        "authority": {
            "liveAuthority": profile.live_authority,
            "candidateAuthority": profile.candidate_authority,
            "liveApplicationChanged": not live_unchanged,
            "candidatePromoted": False,
            "promotionExecuted": False,
            "promotionEligible": False,
            "evidenceReused": False,
        },
    }
    if profile.migration_debt is not None:
        report["migrationDebt"] = dict(profile.migration_debt(projection.report))
    if profile.report_extra is not None:
        report.update(dict(profile.report_extra(projection.report, proof, intent)))
    return report


def _prepare_workspace(
    repo: Path,
    workspace: Path,
    candidate_package: Path,
    package_root: Path,
) -> None:
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    for name in (
        "main_computer",
        "mcel_apps",
        "tests",
        "pretty_docs",
        "contracts",
        "deploy",
        "docker",
        "scripts",
        "game_projects",
        "tools",
    ):
        source = repo / name
        if source.is_dir():
            shutil.copytree(source, workspace / name, ignore=_copy_ignore)
    for path in repo.iterdir():
        if path.is_file() and path.suffix.lower() not in {".zip", ".pyc", ".pyo"}:
            shutil.copy2(path, workspace / path.name)
    target = workspace / package_root
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(candidate_package, target, ignore=_copy_ignore)


def _run_authorities(
    profile: ProfiledPackageCandidateEvidenceProfile,
    repo: Path,
    workspace: Path,
    headed: bool,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    commands = profile.authority_commands or _default_authority_commands(profile)
    for build_command in commands:
        command = build_command(repo, workspace, headed)
        completed = runner(
            command,
            cwd=workspace,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=900,
        )
        if completed.returncode != 0:
            raise ProfiledPackageCandidateEvidenceError(
                "Candidate authority failed: "
                + " ".join(command)
                + (f"\n{completed.stdout.strip()}" if completed.stdout else "")
            )


def _publish(
    profile: ProfiledPackageCandidateEvidenceProfile,
    workspace: Path,
    output: Path,
    report: Mapping[str, Any],
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for source, target in (
        (
            workspace / f"runtime/reports/mcel-acceptance/apps/{profile.app_id}",
            output / "acceptance",
        ),
        (
            workspace / f"runtime/reports/mcel-observation/apps/{profile.app_id}",
            output / "observation",
        ),
        (
            workspace / f"runtime/reports/mcel-app-proof/apps/{profile.app_id}",
            output / "proof",
        ),
    ):
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
    _write_reports(profile, output, report)


def _write_reports(
    profile: ProfiledPackageCandidateEvidenceProfile,
    output: Path,
    report: Mapping[str, Any],
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / profile.report_filename).write_bytes(canonical_json_bytes(report) + b"\n")
    lines = [
        f"# {profile.report_title}",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Truth status: `{report.get('truthStatus')}`",
        f"- Semantic fingerprint: `{(report.get('candidate') or {}).get('semanticFingerprint')}`",
        "",
        "## Stages",
        "",
    ]
    for name, value in (report.get("stages") or {}).items():
        lines.append(f"- {name}: `{value.get('status')}`")
    lines.append("")
    (output / profile.report_markdown_filename).write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


def _base_failure_report(
    profile: ProfiledPackageCandidateEvidenceProfile,
    projection: Any,
    candidate: Path,
    workspace: Path,
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
            "directory": _display(candidate, REPOSITORY_ROOT),
            "workspace": _display(workspace, REPOSITORY_ROOT),
            "semanticFingerprint": (projection.report.get("source") or {}).get("semanticFingerprint"),
            "sourceBindingFingerprint": (projection.report.get("source") or {}).get("sourceBindingFingerprint"),
        },
        "authority": {
            "liveAuthority": profile.live_authority,
            "candidateAuthority": profile.candidate_authority,
            "candidatePromoted": False,
            "promotionExecuted": False,
            "promotionEligible": False,
            "evidenceReused": False,
            "liveApplicationChanged": False,
        },
        "error": error,
    }


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfiledPackageCandidateEvidenceError(
            f"Could not load {label}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise ProfiledPackageCandidateEvidenceError(f"{label} must be a JSON object.")
    return value


def _copy_ignore(_directory: str, names: Sequence[str]) -> set[str]:
    return {
        name
        for name in names
        if name
        in {
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            "node_modules",
            ".git",
        }
        or name.endswith((".pyc", ".pyo", ".zip"))
    }


def _tree_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if (
            not path.is_file()
            or path.is_symlink()
            or "__pycache__" in path.parts
            or path.suffix in {".pyc", ".pyo"}
        ):
            continue
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return "sha256:" + digest.hexdigest()


def _display(path: Path, repo: Path) -> str:
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
        "problem": summary,
        "semanticPath": semantic_path,
    }


def _failure(
    profile: ProfiledPackageCandidateEvidenceProfile,
    status: str,
    diagnostics: list[Mapping[str, Any]],
    message: str,
) -> ProfiledPackageCandidateEvidenceResult:
    diagnostics.append(_diagnostic(profile.source_invalid_code, message, "$candidate"))
    return ProfiledPackageCandidateEvidenceResult(False, status, {}, tuple(diagnostics))
