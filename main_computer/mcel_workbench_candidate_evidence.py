"""Workbench profiled-package candidate evidence wrapper.

Workbench supplies fixture-specific profile data.  Shared isolated evidence
mechanics live in ``main_computer.mcel_profiled_package_candidate_evidence``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from main_computer.mcel_dsl_compiler import DEFAULT_CANDIDATE_ROOT
from main_computer.mcel_profiled_package_candidate_evidence import (
    DEFAULT_REPORT_ROOT,
    ProfiledPackageCandidateEvidenceError,
    ProfiledPackageCandidateEvidenceProfile,
    ProfiledPackageCandidateEvidenceResult,
    _prepare_workspace as _generic_prepare_workspace,
    run_profiled_package_candidate_evidence,
)
from main_computer.mcel_workbench_reference_fixture_profile import (
    APP_ID,
    DEFAULT_DSL_SOURCE,
    DEFAULT_FIXTURE_IR,
    DEFAULT_PACKAGE_ROOT,
    EVIDENCE_REPORT_SCHEMA as REPORT_SCHEMA,
    EVIDENCE_VERSION as VERSION,
    REPOSITORY_ROOT,
    build_workbench_candidate_evidence_profile,
)


WorkbenchCandidateEvidenceError = ProfiledPackageCandidateEvidenceError
WorkbenchCandidateEvidenceResult = ProfiledPackageCandidateEvidenceResult


def workbench_profiled_package_candidate_evidence_profile() -> ProfiledPackageCandidateEvidenceProfile:
    return build_workbench_candidate_evidence_profile()


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
    return run_profiled_package_candidate_evidence(
        workbench_profiled_package_candidate_evidence_profile(),
        repo_root=repo_root,
        dsl_source_path=dsl_source_path,
        fixture_ir_path=fixture_ir_path,
        candidate_root=candidate_root,
        report_root=report_root,
        headed=headed,
        write_report=write_report,
        command_runner=command_runner,
    )


def _prepare_workspace(repo: Path, workspace: Path, candidate_package: Path) -> None:
    """Compatibility shim for existing Workbench promotion rehearsal code."""

    _generic_prepare_workspace(repo, workspace, candidate_package, DEFAULT_PACKAGE_ROOT)
