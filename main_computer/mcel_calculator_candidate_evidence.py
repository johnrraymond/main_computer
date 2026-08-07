"""Candidate evidence wrapper for Calculator's host-bound DSL authority."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from main_computer.mcel_calculator_candidate_projection import (
    DEFAULT_CANDIDATE_ROOT,
    DEFAULT_DSL_SOURCE,
    DEFAULT_PACKAGE_ROOT,
    project_calculator_candidate,
)
from main_computer.mcel_calculator_host_bound_profile import (
    APP_ID,
    DEFAULT_CANDIDATE_EVIDENCE_REPORT_ROOT as DEFAULT_REPORT_ROOT,
    build_calculator_candidate_evidence_profile,
)
from main_computer.mcel_calculator_ir_native_proof import run_calculator_ir_native_intent_proof
from main_computer.mcel_calculator_parity import (
    run_calculator_browser_parity_probe,
    run_calculator_generated_adapter_parity,
)
from main_computer.mcel_host_bound_candidate_evidence import (
    REPOSITORY_ROOT,
    HostBoundCandidateEvidenceError,
    HostBoundCandidateEvidenceResult,
    run_host_bound_candidate_evidence,
)


CalculatorCandidateEvidenceError = HostBoundCandidateEvidenceError
CalculatorCandidateEvidenceResult = HostBoundCandidateEvidenceResult
CALCULATOR_CANDIDATE_EVIDENCE_PROFILE = build_calculator_candidate_evidence_profile(
    project_candidate=project_calculator_candidate,
    run_generated_adapter_parity=run_calculator_generated_adapter_parity,
    run_ir_native_proof=run_calculator_ir_native_intent_proof,
    run_browser_parity_probe=run_calculator_browser_parity_probe,
)


def run_calculator_candidate_evidence(
    *,
    repo_root: Path = REPOSITORY_ROOT,
    dsl_source_path: Path = DEFAULT_DSL_SOURCE,
    fixture_ir_path: Path | None = None,
    candidate_root: Path = DEFAULT_CANDIDATE_ROOT,
    report_root: Path = DEFAULT_REPORT_ROOT,
    headed: bool = False,
    write_report: bool = True,
    command_runner: Any = None,
    node_probe_runner: Any = None,
    browser_probe_runner: Any = None,
) -> CalculatorCandidateEvidenceResult:
    return run_host_bound_candidate_evidence(
        CALCULATOR_CANDIDATE_EVIDENCE_PROFILE,
        repo_root=repo_root,
        dsl_source_path=dsl_source_path,
        fixture_ir_path=fixture_ir_path,
        candidate_root=candidate_root,
        report_root=report_root,
        headed=headed,
        write_report=write_report,
        command_runner=command_runner,
        node_probe_runner=node_probe_runner,
        browser_probe_runner=browser_probe_runner,
    )
