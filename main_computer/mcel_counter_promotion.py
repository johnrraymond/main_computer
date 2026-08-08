"""Counter promotion wrapper for generic explicit-package MCEL tooling."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping

from main_computer.mcel_counter_effect_probe import (
    _build_effect_accounting,
    _run_browser_effect_probe,
    _run_counter_effect_probe,
)
from main_computer.mcel_counter_compatibility import DEFAULT_FIXTURE_IR, compare_counter_representations
from main_computer.mcel_counter_promotion_rehearsal import (
    DEFAULT_REPORT_ROOT as DEFAULT_REHEARSAL_REPORT_ROOT,
    REPOSITORY_ROOT,
    rehearse_counter_promotion,
    counter_explicit_package_promotion_rehearsal_profile,
)
from main_computer.mcel_counter_reference_fixture_profile import (
    PROMOTION_EXECUTION_REPORT_ROOT as DEFAULT_REPORT_ROOT,
    PROMOTION_LOCK_PATH as DEFAULT_LOCK_PATH,
    PROMOTION_ROLLBACK_SCHEMA as ROLLBACK_SCHEMA,
    PROMOTION_TRANSACTION_ROOT as DEFAULT_TRANSACTION_ROOT,
    PROMOTION_TRANSACTION_SCHEMA as TRANSACTION_SCHEMA,
    PROMOTION_EXECUTION_REPORT_SCHEMA as REPORT_SCHEMA,
    PROMOTION_EXECUTION_REPORT_VERSION as REPORT_VERSION,
    build_counter_promotion_execution_profile,
)
from main_computer.mcel_explicit_package_promotion import (
    ExplicitPackagePromotionError,
    ExplicitPackagePromotionProfile,
    ExplicitPackagePromotionResult,
    execute_explicit_package_promotion,
    rollback_explicit_package_promotion,
)


class CounterPromotionError(ExplicitPackagePromotionError):
    """Raised when Counter promotion or rollback cannot complete truthfully."""


CounterPromotionResult = ExplicitPackagePromotionResult


def counter_explicit_package_promotion_profile() -> ExplicitPackagePromotionProfile:
    return build_counter_promotion_execution_profile(
        rehearsal_profile=counter_explicit_package_promotion_rehearsal_profile(),
        run_rehearsal=rehearse_counter_promotion,
        compare_representations=compare_counter_representations,
        run_node_probe=_run_counter_effect_probe,
        run_browser_probe=_run_browser_effect_probe,
        build_effect_accounting=_build_effect_accounting,
    )


def execute_counter_promotion(
    *,
    repo_root: Path = REPOSITORY_ROOT,
    fixture_ir_path: Path = DEFAULT_FIXTURE_IR,
    transaction_root: Path = DEFAULT_TRANSACTION_ROOT,
    report_root: Path = DEFAULT_REPORT_ROOT,
    rehearsal_report_root: Path = DEFAULT_REHEARSAL_REPORT_ROOT,
    headed: bool = False,
    write_report: bool = True,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    rehearsal_runner: Callable[..., Any] | None = None,
    node_probe_runner: Callable[[Path], Mapping[str, Any]] | None = None,
    browser_probe_runner: Callable[[Path, bool], Mapping[str, Any]] | None = None,
    failure_injector: Callable[[str], None] | None = None,
) -> CounterPromotionResult:
    return execute_explicit_package_promotion(
        counter_explicit_package_promotion_profile(),
        repo_root=repo_root,
        fixture_ir_path=fixture_ir_path,
        transaction_root=transaction_root,
        report_root=report_root,
        rehearsal_report_root=rehearsal_report_root,
        headed=headed,
        write_report=write_report,
        command_runner=command_runner,
        rehearsal_runner=rehearsal_runner,
        node_probe_runner=node_probe_runner,
        browser_probe_runner=browser_probe_runner,
        failure_injector=failure_injector,
    )


def rollback_counter_promotion(
    transaction: str,
    *,
    repo_root: Path = REPOSITORY_ROOT,
    transaction_root: Path = DEFAULT_TRANSACTION_ROOT,
    report_root: Path = DEFAULT_REPORT_ROOT,
    write_report: bool = True,
) -> CounterPromotionResult:
    return rollback_explicit_package_promotion(
        counter_explicit_package_promotion_profile(),
        transaction,
        repo_root=repo_root,
        transaction_root=transaction_root,
        report_root=report_root,
        write_report=write_report,
    )
