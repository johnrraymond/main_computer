"""Calculator promotion rehearsal wrapper."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from main_computer.mcel_calculator_candidate_evidence import (
    DEFAULT_REPORT_ROOT as DEFAULT_EVIDENCE_REPORT_ROOT,
    run_calculator_candidate_evidence,
)
from main_computer.mcel_calculator_candidate_projection import (
    DEFAULT_CANDIDATE_ROOT,
    DEFAULT_DSL_SOURCE,
    DEFAULT_PACKAGE_ROOT,
    PROJECTION_PROFILE,
    project_calculator_candidate,
)
from main_computer.mcel_calculator_host_bound_profile import (
    APP_ID,
    DEFAULT_PROMOTION_REPORT_ROOT as DEFAULT_REPORT_ROOT,
    PROMOTED_TRUTH_STATUS,
    PROMOTION_BOUNDARY,
    build_calculator_promotion_profile,
)
from main_computer.mcel_host_bound_promotion_rehearsal import (
    REPOSITORY_ROOT,
    HostBoundPromotionRehearsalError,
    HostBoundPromotionRehearsalResult,
    execute_host_bound_promotion,
    rollback_host_bound_promotion,
    run_host_bound_promotion_rehearsal,
)


CalculatorPromotionRehearsalError = HostBoundPromotionRehearsalError
CalculatorPromotionRehearsalResult = HostBoundPromotionRehearsalResult
CALCULATOR_PROMOTION_PROFILE = build_calculator_promotion_profile(
    project_candidate=project_calculator_candidate,
    run_candidate_evidence=run_calculator_candidate_evidence,
)


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
    **unused: Any,
) -> CalculatorPromotionRehearsalResult:
    return run_host_bound_promotion_rehearsal(
        CALCULATOR_PROMOTION_PROFILE,
        repo_root=repo_root,
        dsl_source_path=dsl_source_path,
        candidate_root=candidate_root,
        evidence_report_root=evidence_report_root,
        report_root=report_root,
        headed=headed,
        write_report=write_report,
        evidence_runner=evidence_runner,
        command_runner=command_runner,
        **unused,
    )


def execute_calculator_promotion(
    *,
    repo_root: Path = REPOSITORY_ROOT,
    headed: bool = False,
    write_report: bool = True,
    **unused: Any,
) -> CalculatorPromotionRehearsalResult:
    return execute_host_bound_promotion(
        CALCULATOR_PROMOTION_PROFILE,
        repo_root=repo_root,
        headed=headed,
        write_report=write_report,
        **unused,
    )


def rollback_calculator_promotion(
    transaction: str | None = None,
    *,
    repo_root: Path = REPOSITORY_ROOT,
    **unused: Any,
) -> CalculatorPromotionRehearsalResult:
    return rollback_host_bound_promotion(
        CALCULATOR_PROMOTION_PROFILE,
        transaction=transaction,
        repo_root=repo_root,
        **unused,
    )
