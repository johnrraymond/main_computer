"""Contract Workbench promotion execution wrapper.

The transaction/apply/rollback mechanics are shared by
``main_computer.mcel_profiled_package_promotion``.  This module preserves the
historical Workbench entry points while the Workbench-specific facts live in
``mcel_workbench_reference_fixture_profile``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable

from main_computer.mcel_profiled_package_promotion import (
    DEFAULT_REPORT_ROOT,
    DEFAULT_TRANSACTION_ROOT,
    ProfiledPackagePromotionError,
    ProfiledPackagePromotionProfile,
    ProfiledPackagePromotionResult,
    execute_profiled_package_promotion,
    rollback_profiled_package_promotion,
)
from main_computer.mcel_workbench_reference_fixture_profile import (
    APP_ID,
    DEFAULT_FIXTURE_IR,
    DEFAULT_PACKAGE_ROOT,
    build_workbench_promotion_profile,
)


REPORT_SCHEMA = "mcel.application-promotion-execution-report.v1"
REPORT_VERSION = "mcel-workbench-promotion-wave13"
TRANSACTION_SCHEMA = "mcel.application-promotion-transaction.v1"
ROLLBACK_SCHEMA = "mcel.application-promotion-rollback-result.v1"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRANSACTION_ROOT = Path("runtime/state/mcel/application-promotions/contract-workbench")
DEFAULT_REPORT_ROOT = Path("runtime/reports/mcel-application-promotions/contract-workbench")
DEFAULT_LOCK_PATH = Path("runtime/state/mcel/application-promotion.contract-workbench.lock")

WorkbenchPromotionError = ProfiledPackagePromotionError
WorkbenchPromotionResult = ProfiledPackagePromotionResult


def workbench_profiled_package_promotion_profile() -> ProfiledPackagePromotionProfile:
    """Build the Workbench profile for generic profiled-package promotion execution."""

    return build_workbench_promotion_profile()


def execute_workbench_promotion(
    *,
    repo_root: Path = REPOSITORY_ROOT,
    fixture_ir_path: Path = DEFAULT_FIXTURE_IR,
    transaction_root: Path = DEFAULT_TRANSACTION_ROOT,
    report_root: Path = DEFAULT_REPORT_ROOT,
    rehearsal_report_root: Path | None = None,
    headed: bool = False,
    write_report: bool = True,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    rehearsal_runner: Callable[..., Any] | None = None,
    failure_injector: Callable[[str], None] | None = None,
    force_repromotion: bool = False,
) -> WorkbenchPromotionResult:
    return execute_profiled_package_promotion(
        workbench_profiled_package_promotion_profile(),
        repo_root=repo_root,
        fixture_ir_path=fixture_ir_path,
        transaction_root=transaction_root,
        report_root=report_root,
        rehearsal_report_root=rehearsal_report_root,
        headed=headed,
        write_report=write_report,
        command_runner=command_runner,
        rehearsal_runner=rehearsal_runner,
        failure_injector=failure_injector,
        force_repromotion=force_repromotion,
    )


def rollback_workbench_promotion(
    transaction: str,
    *,
    repo_root: Path = REPOSITORY_ROOT,
    transaction_root: Path = DEFAULT_TRANSACTION_ROOT,
    report_root: Path = DEFAULT_REPORT_ROOT,
    write_report: bool = True,
) -> WorkbenchPromotionResult:
    return rollback_profiled_package_promotion(
        workbench_profiled_package_promotion_profile(),
        transaction,
        repo_root=repo_root,
        transaction_root=transaction_root,
        report_root=report_root,
        write_report=write_report,
    )
