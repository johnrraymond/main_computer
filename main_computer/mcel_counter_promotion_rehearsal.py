"""Counter promotion rehearsal wrapper for generic explicit-package MCEL tooling."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping

from main_computer.mcel_counter_candidate_evidence import (
    DEFAULT_REPORT_ROOT as DEFAULT_EVIDENCE_REPORT_ROOT,
    _build_effect_accounting,
    _run_browser_effect_probe,
    _run_counter_effect_probe,
    run_counter_candidate_evidence,
)
from main_computer.mcel_counter_candidate_projection import project_counter_candidate
from main_computer.mcel_counter_compatibility import (
    DEFAULT_DSL_SOURCE,
    DEFAULT_FIXTURE_IR,
    compare_counter_representations,
)
from main_computer.mcel_counter_reference_fixture_profile import (
    APP_ID,
    GENERATED_CONTRACTS,
    PROMOTION_OWNERSHIP_SCHEMA as OWNERSHIP_SCHEMA,
    PROMOTION_PLAN_SCHEMA as PLAN_SCHEMA,
    PROMOTION_REPORT_SCHEMA as REPORT_SCHEMA,
    PROMOTION_REPORT_VERSION as REPORT_VERSION,
    build_counter_promotion_rehearsal_profile,
)
from main_computer.mcel_dsl_compiler import DEFAULT_CANDIDATE_ROOT
from main_computer.mcel_explicit_package_promotion_rehearsal import (
    DEFAULT_REPORT_ROOT,
    ExplicitPackagePromotionRehearsalError,
    ExplicitPackagePromotionRehearsalProfile,
    ExplicitPackagePromotionRehearsalResult,
    _diagnostic,
    _display_path,
    _promotion_authority_source_snapshot as _generic_promotion_authority_source_snapshot,
    _sha,
    _snapshot_changes,
    _source_tree_snapshot,
    _tree_snapshot,
    apply_explicit_package_promotion_plan,
    build_explicit_package_promotion_plan,
    promotion_authority as _generic_authority,
    rollback_explicit_package_promotion_plan,
    run_explicit_package_promotion_rehearsal,
    stage_explicit_package_promotion_material,
    verify_explicit_package_promoted_ownership,
    workspace_fingerprints as _generic_workspace_fingerprints,
    write_explicit_package_promotion_report,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class CounterPromotionRehearsalError(ExplicitPackagePromotionRehearsalError):
    """Raised when the Counter promotion rehearsal cannot truthfully complete."""


CounterPromotionRehearsalResult = ExplicitPackagePromotionRehearsalResult


def counter_explicit_package_promotion_rehearsal_profile() -> ExplicitPackagePromotionRehearsalProfile:
    return build_counter_promotion_rehearsal_profile(
        project_candidate=project_counter_candidate,
        run_candidate_evidence=run_counter_candidate_evidence,
        compare_representations=compare_counter_representations,
        run_node_probe=_run_counter_effect_probe,
        run_browser_probe=_run_browser_effect_probe,
        build_effect_accounting=_build_effect_accounting,
    )


def rehearse_counter_promotion(
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
    node_probe_runner: Callable[[Path], Mapping[str, Any]] | None = None,
    browser_probe_runner: Callable[[Path, bool], Mapping[str, Any]] | None = None,
) -> CounterPromotionRehearsalResult:
    return run_explicit_package_promotion_rehearsal(
        counter_explicit_package_promotion_rehearsal_profile(),
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
        node_probe_runner=node_probe_runner,
        browser_probe_runner=browser_probe_runner,
    )


def _build_promotion_plan(
    *,
    repo: Path,
    live_package: Path,
    candidate_package: Path,
    dsl_source_path: Path,
    semantic_fingerprint: str,
    source_binding_fingerprint: str,
    evidence_payload: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, bytes]]:
    return build_explicit_package_promotion_plan(
        counter_explicit_package_promotion_rehearsal_profile(),
        repo=repo,
        live_package=live_package,
        candidate_package=candidate_package,
        dsl_source_path=dsl_source_path,
        semantic_fingerprint=semantic_fingerprint,
        source_binding_fingerprint=source_binding_fingerprint,
        evidence_payload=evidence_payload,
    )


def _stage_material(
    plan: Mapping[str, Any], promoted: Mapping[str, bytes], live_package: Path,
    promotion_root: Path, rollback_root: Path,
) -> None:
    stage_explicit_package_promotion_material(plan, promoted, live_package, promotion_root, rollback_root)


def _apply_plan(workspace: Path, plan: Mapping[str, Any], promoted: Mapping[str, bytes]) -> None:
    apply_explicit_package_promotion_plan(workspace, plan, promoted)


def _rollback_plan(workspace: Path, plan: Mapping[str, Any], live_package: Path) -> None:
    rollback_explicit_package_promotion_plan(workspace, plan, live_package)


def _verify_promoted_ownership(workspace: Path) -> None:
    verify_explicit_package_promoted_ownership(
        workspace,
        counter_explicit_package_promotion_rehearsal_profile(),
    )


def _workspace_fingerprints(workspace: Path) -> dict[str, Any]:
    return _generic_workspace_fingerprints(
        workspace,
        counter_explicit_package_promotion_rehearsal_profile(),
    )


def _promotion_authority_source_snapshot(repo: Path) -> dict[str, str]:
    return _generic_promotion_authority_source_snapshot(
        repo,
        counter_explicit_package_promotion_rehearsal_profile(),
    )


def _authority(eligible: bool, *, live_application_changed: bool = False) -> dict[str, Any]:
    return _generic_authority(
        counter_explicit_package_promotion_rehearsal_profile(),
        eligible,
        live_application_changed=live_application_changed,
    )
