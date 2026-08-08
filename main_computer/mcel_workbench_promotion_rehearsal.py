"""Contract Workbench promotion rehearsal wrapper.

The rehearsal mechanics are shared by
:mod:`main_computer.mcel_profiled_package_promotion_rehearsal`.  This module
keeps the historical Workbench entry points and test helper names while the
Workbench-specific facts live in ``mcel_workbench_reference_fixture_profile``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping

from main_computer.mcel_dsl_compiler import DEFAULT_CANDIDATE_ROOT
from main_computer.mcel_profiled_package_candidate_evidence import (
    DEFAULT_REPORT_ROOT as DEFAULT_EVIDENCE_REPORT_ROOT,
)
from main_computer.mcel_profiled_package_promotion_rehearsal import (
    DEFAULT_REPORT_ROOT,
    PLAN_SCHEMA,
    ProfiledPackagePromotionRehearsalError,
    ProfiledPackagePromotionRehearsalResult,
    _apply_plan,
    _build_promotion_plan as _generic_build_promotion_plan,
    _display,
    _protected_source_snapshot as _generic_protected_source_snapshot,
    _restore_live_package as _generic_restore_live_package,
    _run_promoted_authorities as _generic_run_promoted_authorities,
    _sha,
    _stage_material as _generic_stage_material,
    _tree_snapshot,
    _verify_generated_ownership as _generic_verify_generated_ownership,
    _workspace_fingerprints as _generic_workspace_fingerprints,
    rehearse_profiled_package_promotion,
)
from main_computer.mcel_workbench_reference_fixture_profile import (
    APP_ID,
    DEFAULT_DSL_SOURCE,
    DEFAULT_FIXTURE_IR,
    DEFAULT_PACKAGE_ROOT,
    build_workbench_promotion_rehearsal_profile,
)


REPORT_SCHEMA = "mcel.app-promotion-rehearsal-report.v1"
REPORT_VERSION = "mcel-app-promotion-rehearsal-wave12"
OWNERSHIP_SCHEMA = "mcel.generated-file-ownership.v1"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROMOTION_SUPPORT_FILES: tuple[str, ...] = ()

WorkbenchPromotionRehearsalError = ProfiledPackagePromotionRehearsalError
WorkbenchPromotionRehearsalResult = ProfiledPackagePromotionRehearsalResult


def rehearse_workbench_promotion(
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
) -> WorkbenchPromotionRehearsalResult:
    return rehearse_profiled_package_promotion(
        build_workbench_promotion_rehearsal_profile(),
        repo_root=repo_root,
        dsl_source_path=dsl_source_path,
        fixture_ir_path=fixture_ir_path,
        candidate_root=candidate_root,
        evidence_report_root=evidence_report_root,
        report_root=report_root,
        headed=headed,
        write_report=write_report,
        command_runner=command_runner,
        evidence_runner=evidence_runner,
    )


def _build_promotion_plan(
    *,
    live_package: Path,
    candidate_package: Path,
    dsl_source: Path,
    semantic_fingerprint: str,
    source_binding_fingerprint: str,
    evidence: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, bytes]]:
    return _generic_build_promotion_plan(
        build_workbench_promotion_rehearsal_profile(),
        live_package=live_package,
        candidate_package=candidate_package,
        dsl_source=dsl_source,
        semantic_fingerprint=semantic_fingerprint,
        source_binding_fingerprint=source_binding_fingerprint,
        evidence=evidence,
    )



def _stage_material(
    root: Path,
    plan: Mapping[str, Any],
    promoted: Mapping[str, bytes],
    live_package: Path,
) -> None:
    _generic_stage_material(
        build_workbench_promotion_rehearsal_profile(),
        root,
        plan,
        promoted,
        live_package,
    )


def _verify_generated_ownership(package: Path) -> bool:
    return _generic_verify_generated_ownership(
        build_workbench_promotion_rehearsal_profile(),
        package,
    )


def _restore_live_package(workspace: Path, live_package: Path) -> None:
    _generic_restore_live_package(workspace, live_package, DEFAULT_PACKAGE_ROOT)


def _run_promoted_authorities(
    repo: Path,
    workspace: Path,
    headed: bool,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    _generic_run_promoted_authorities(
        build_workbench_promotion_rehearsal_profile(),
        repo,
        workspace,
        headed,
        runner,
    )


def _protected_source_snapshot(repo: Path) -> dict[str, str]:
    return _generic_protected_source_snapshot(
        repo,
        build_workbench_promotion_rehearsal_profile(),
    )


def _workspace_fingerprints(repo: Path) -> dict[str, Any]:
    return _generic_workspace_fingerprints(
        repo,
        build_workbench_promotion_rehearsal_profile(),
    )
