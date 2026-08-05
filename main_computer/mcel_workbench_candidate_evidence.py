"""Fresh isolated evidence for the Contract Workbench Wave 10 candidate."""

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
from main_computer.mcel_dsl_compiler import DEFAULT_CANDIDATE_ROOT
from main_computer.mcel_workbench_candidate_projection import (
    APP_ID,
    DEFAULT_DSL_SOURCE,
    DEFAULT_FIXTURE_IR,
    DEFAULT_PACKAGE_ROOT,
    project_workbench_candidate,
)

REPORT_SCHEMA = "mcel.workbench-candidate-evidence-report.v1"
VERSION = "mcel-workbench-candidate-evidence-wave11"
DEFAULT_REPORT_ROOT = Path("runtime/reports/mcel-compiler-candidates")
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class WorkbenchCandidateEvidenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkbenchCandidateEvidenceResult:
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
            value["artifacts"] = {"reports": _display(self.output_directory, REPOSITORY_ROOT)}
        return value


def run_workbench_candidate_evidence(
    *,
    repo_root: Path = REPOSITORY_ROOT,
    dsl_source_path: Path = DEFAULT_DSL_SOURCE,
    fixture_ir_path: Path = DEFAULT_FIXTURE_IR,
    candidate_root: Path = DEFAULT_CANDIDATE_ROOT,
    report_root: Path = DEFAULT_REPORT_ROOT,
    headed: bool = False,
    write_report: bool = False,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> WorkbenchCandidateEvidenceResult:
    repo = repo_root.resolve()
    diagnostics: list[Mapping[str, Any]] = []
    live_package = repo / DEFAULT_PACKAGE_ROOT
    live_before = _tree_fingerprint(live_package)
    projection = project_workbench_candidate(
        dsl_source_path=dsl_source_path,
        fixture_ir_path=fixture_ir_path,
        live_package_root=live_package,
        candidate_root=(repo / candidate_root if not candidate_root.is_absolute() else candidate_root),
        write_candidate=True,
    )
    diagnostics.extend(projection.diagnostics)
    if not projection.valid or projection.candidate_directory is None:
        return _failure("invalid-projection", diagnostics, "Workbench candidate projection is not exact.")

    candidate_directory = projection.candidate_directory.resolve()
    candidate_package = candidate_directory / "package/mcel_apps/contract-workbench"
    workspace = candidate_directory / "evidence-workspace"
    source_binding = str((projection.report.get("source") or {}).get("sourceBindingFingerprint") or "").removeprefix("sha256:")
    output_root = report_root if report_root.is_absolute() else repo / report_root
    output_directory = output_root / APP_ID / source_binding
    if write_report and output_directory.exists():
        shutil.rmtree(output_directory)

    try:
        _prepare_workspace(repo, workspace, candidate_package)
        _run_authorities(repo, workspace, headed, command_runner or subprocess.run)
        acceptance = _load_json(workspace / "runtime/reports/mcel-acceptance/apps/contract-workbench/mcel-acceptance-report.json", "candidate acceptance")
        observation = _load_json(workspace / "runtime/reports/mcel-observation/apps/contract-workbench/mcel-operation-observation-report.json", "candidate observation")
        proof = _load_json(workspace / "runtime/reports/mcel-app-proof/apps/contract-workbench/mcel-app-proof-report.json", "candidate application proof")
    except WorkbenchCandidateEvidenceError as exc:
        diagnostics.append(_diagnostic("MCEL_WORKBENCH_CANDIDATE_EVIDENCE_FAILED", str(exc), "$candidateEvidence"))
        report = _base_failure_report(projection, candidate_directory, workspace, str(exc))
        if write_report:
            _write_reports(output_directory, report)
        return WorkbenchCandidateEvidenceResult(False, "fail", report, tuple(diagnostics), output_directory if write_report else None)

    intent = proof.get("intentCoverage") or {}
    stages = proof.get("stages") or {}
    stage_checks = {
        "candidateProjection": projection.status == "exact",
        "acceptance": acceptance.get("status") == "pass" and acceptance.get("passed") is True,
        "browserObservation": observation.get("status") == "pass" and observation.get("ok") is True,
        "applicationProof": proof.get("status") == "pass" and proof.get("truthStatus") == "semantic-runtime-proven",
        "repositoryBinding": (stages.get("repositoryBinding") or {}).get("status") == "exact",
        "intentCompleteness": intent.get("passed") is True and int(intent.get("declaredIntentCount") or 0) == 7 and int(intent.get("coveredIntentCount") or 0) == 7,
        "scenarioCompleteness": int(intent.get("declaredScenarioCount") or 0) == 14 and int(intent.get("observedScenarioCount") or 0) == 14,
    }
    for stage, passed in stage_checks.items():
        if not passed:
            diagnostics.append(_diagnostic("MCEL_WORKBENCH_CANDIDATE_STAGE_FAILED", f"Candidate stage {stage} did not pass.", f"$stages.{stage}"))

    live_after = _tree_fingerprint(live_package)
    live_unchanged = live_before == live_after
    if not live_unchanged:
        diagnostics.append(_diagnostic("MCEL_WORKBENCH_LIVE_PACKAGE_CHANGED", "The live Workbench package changed during isolated candidate proof.", "$authority.liveApplicationChanged"))

    valid = all(stage_checks.values()) and live_unchanged and not any(item.get("blocking", True) for item in diagnostics)
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "version": VERSION,
        "appId": APP_ID,
        "status": "pass" if valid else "fail",
        "valid": valid,
        "truthStatus": proof.get("truthStatus"),
        "genericPipeline": True,
        "counterSpecificExecutionPathRequired": False,
        "candidate": {
            "directory": _display(candidate_directory, repo),
            "workspace": _display(workspace, repo),
            "semanticFingerprint": (projection.report.get("source") or {}).get("semanticFingerprint"),
            "sourceBindingFingerprint": (projection.report.get("source") or {}).get("sourceBindingFingerprint"),
            "projectionProfile": projection.report.get("projectionProfile"),
        },
        "stages": {name: {"status": "pass" if passed else "fail"} for name, passed in stage_checks.items()},
        "intentCoverage": {
            "mode": intent.get("coverageMode"),
            "declaredIntents": intent.get("declaredIntentCount"),
            "coveredIntents": intent.get("coveredIntentCount"),
            "declaredScenarios": intent.get("declaredScenarioCount"),
            "observedScenarios": intent.get("observedScenarioCount"),
        },
        "authority": {
            "liveAuthority": "legacy-explicit-package",
            "candidateAuthority": "none",
            "liveApplicationChanged": not live_unchanged,
            "candidatePromoted": False,
            "promotionExecuted": False,
            "promotionEligible": False,
            "evidenceReused": False,
        },
        "migrationDebt": {
            "opaqueCallbacks": int((projection.report.get("source") or {}).get("opaqueCallbackDebt") or 0),
            "nativeDomainCalls": int((projection.report.get("source") or {}).get("nativeDomainCallCount") or 0),
            "portableIrProjectionComplete": True,
            "normalizedDefinitionProjectionRequired": False,
        },
    }
    if write_report:
        _publish(workspace, output_directory, report)
    return WorkbenchCandidateEvidenceResult(valid, report["status"], report, tuple(diagnostics), output_directory if write_report else None)


def _prepare_workspace(repo: Path, workspace: Path, candidate_package: Path) -> None:
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    for name in ("main_computer", "mcel_apps", "tests", "pretty_docs", "contracts", "deploy", "docker", "scripts", "game_projects", "tools"):
        source = repo / name
        if source.is_dir():
            shutil.copytree(source, workspace / name, ignore=_copy_ignore)
    for path in repo.iterdir():
        if path.is_file() and path.suffix.lower() not in {".zip", ".pyc", ".pyo"}:
            shutil.copy2(path, workspace / path.name)
    target = workspace / DEFAULT_PACKAGE_ROOT
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(candidate_package, target, ignore=_copy_ignore)


def _run_authorities(repo: Path, workspace: Path, headed: bool, runner: Callable[..., subprocess.CompletedProcess[str]]) -> None:
    commands = [
        [sys.executable, str(repo / "tools/mcel_application_definition.py"), "--repo-root", str(workspace), "--app", APP_ID, "--check"],
        [sys.executable, str(repo / "tools/mcel_application_runtime_projection.py"), "--repo-root", str(workspace)],
        [sys.executable, str(repo / "tools/mcel_application_package_browser_catalog.py"), "--repo-root", str(workspace)],
        [sys.executable, str(repo / "main_computer/mcel_acceptance_runner.py"), "--repo-root", str(workspace), "--app", APP_ID, "--check"],
        [sys.executable, str(repo / "main_computer/mcel_application_observation_runner.py"), "--repo-root", str(workspace), "--app", APP_ID, "--check", *(["--headed"] if headed else [])],
        [sys.executable, str(repo / "main_computer/mcel_app_prove.py"), "--repo-root", str(workspace), "--app", APP_ID, "--reuse-evidence", "--check"],
    ]
    for command in commands:
        completed = runner(command, cwd=workspace, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False, timeout=900)
        if completed.returncode != 0:
            raise WorkbenchCandidateEvidenceError("Candidate authority failed: " + " ".join(command) + (f"\n{completed.stdout.strip()}" if completed.stdout else ""))


def _publish(workspace: Path, output: Path, report: Mapping[str, Any]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for source, target in (
        (workspace / "runtime/reports/mcel-acceptance/apps/contract-workbench", output / "acceptance"),
        (workspace / "runtime/reports/mcel-observation/apps/contract-workbench", output / "observation"),
        (workspace / "runtime/reports/mcel-app-proof/apps/contract-workbench", output / "proof"),
    ):
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
    _write_reports(output, report)


def _write_reports(output: Path, report: Mapping[str, Any]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "mcel-candidate-evidence-report.json").write_bytes(canonical_json_bytes(report) + b"\n")
    lines = [
        "# Contract Workbench Candidate Evidence",
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
    (output / "mcel-candidate-evidence-report.md").write_text("\n".join(lines), encoding="utf-8")


def _base_failure_report(projection: Any, candidate: Path, workspace: Path, error: str) -> dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "version": VERSION,
        "appId": APP_ID,
        "status": "fail",
        "valid": False,
        "truthStatus": None,
        "candidate": {
            "directory": _display(candidate, REPOSITORY_ROOT),
            "workspace": _display(workspace, REPOSITORY_ROOT),
            "semanticFingerprint": (projection.report.get("source") or {}).get("semanticFingerprint"),
            "sourceBindingFingerprint": (projection.report.get("source") or {}).get("sourceBindingFingerprint"),
        },
        "authority": {"liveAuthority": "legacy-explicit-package", "candidatePromoted": False, "promotionExecuted": False, "promotionEligible": False, "evidenceReused": False, "liveApplicationChanged": False},
        "error": error,
    }


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkbenchCandidateEvidenceError(f"Could not load {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkbenchCandidateEvidenceError(f"{label} must be a JSON object.")
    return value


def _copy_ignore(_directory: str, names: Sequence[str]) -> set[str]:
    return {name for name in names if name in {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules", ".git"} or name.endswith((".pyc", ".pyo", ".zip"))}


def _tree_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink() or "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big")); digest.update(relative)
        digest.update(len(content).to_bytes(8, "big")); digest.update(content)
    return "sha256:" + digest.hexdigest()


def _display(path: Path, repo: Path) -> str:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _diagnostic(code: str, summary: str, semantic_path: str) -> dict[str, Any]:
    return {"schema": "mcel.compiler-diagnostic.v1", "code": code, "severity": "error", "blocking": True, "summary": summary, "problem": summary, "semanticPath": semantic_path}


def _failure(status: str, diagnostics: list[Mapping[str, Any]], message: str) -> WorkbenchCandidateEvidenceResult:
    diagnostics.append(_diagnostic("MCEL_WORKBENCH_CANDIDATE_EVIDENCE_SOURCE_INVALID", message, "$candidate"))
    return WorkbenchCandidateEvidenceResult(False, status, {}, tuple(diagnostics))
