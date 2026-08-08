"""Counter evidence wrapper for the generic explicit-package candidate pipeline."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping

from main_computer.mcel_counter_candidate_projection import project_counter_candidate
from main_computer.mcel_counter_compatibility import DEFAULT_DSL_SOURCE, DEFAULT_FIXTURE_IR
from main_computer.mcel_counter_effect_probe import (
    CounterCandidateEvidenceError,
    _build_effect_accounting,
    _evaluate_browser_effect_probe,
    _run_browser_effect_probe,
    _run_counter_effect_probe,
)
from main_computer.mcel_counter_reference_fixture_profile import (
    APP_ID,
    build_counter_candidate_evidence_profile,
)
from main_computer.mcel_dsl_compiler import DEFAULT_CANDIDATE_ROOT
from main_computer.mcel_explicit_package_candidate_evidence import (
    DEFAULT_REPORT_ROOT,
    ExplicitPackageCandidateEvidenceProfile,
    ExplicitPackageCandidateEvidenceResult,
    _load_json as _generic_load_json,
    _prepare_workspace as _generic_prepare_workspace,
    _run_candidate_authorities as _generic_run_candidate_authorities,
    run_explicit_package_candidate_evidence,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


CandidateEvidenceResult = ExplicitPackageCandidateEvidenceResult


def counter_explicit_package_candidate_evidence_profile() -> ExplicitPackageCandidateEvidenceProfile:
    return build_counter_candidate_evidence_profile(
        project_candidate=project_counter_candidate,
        build_effect_accounting=_build_effect_accounting,
    )


def run_counter_candidate_evidence(
    *,
    repo_root: Path = REPOSITORY_ROOT,
    dsl_source_path: Path = DEFAULT_DSL_SOURCE,
    fixture_ir_path: Path = DEFAULT_FIXTURE_IR,
    candidate_root: Path = DEFAULT_CANDIDATE_ROOT,
    report_root: Path = DEFAULT_REPORT_ROOT,
    headed: bool = False,
    write_report: bool = True,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    node_probe_runner: Callable[[Path], Mapping[str, Any]] | None = None,
    browser_probe_runner: Callable[[Path, bool], Mapping[str, Any]] | None = None,
) -> CandidateEvidenceResult:
    return run_explicit_package_candidate_evidence(
        counter_explicit_package_candidate_evidence_profile(),
        repo_root=repo_root,
        dsl_source_path=dsl_source_path,
        fixture_ir_path=fixture_ir_path,
        candidate_root=candidate_root,
        report_root=report_root,
        headed=headed,
        write_report=write_report,
        command_runner=command_runner,
        node_probe_runner=node_probe_runner or (lambda workspace: _run_counter_effect_probe(workspace)),
        browser_probe_runner=browser_probe_runner
        or (lambda workspace, is_headed: _run_browser_effect_probe(workspace, is_headed)),
    )


def _prepare_workspace(repo: Path, workspace: Path, candidate_package: Path) -> None:
    _generic_prepare_workspace(repo, workspace, candidate_package, app_id=APP_ID)


def _run_candidate_authorities(
    *,
    repo: Path,
    workspace: Path,
    headed: bool,
    command_runner: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    _generic_run_candidate_authorities(
        repo=repo,
        workspace=workspace,
        app_id=APP_ID,
        headed=headed,
        command_runner=command_runner,
    )


def _load_json(path: Path, label: str) -> dict[str, Any]:
    return _generic_load_json(path, label)
