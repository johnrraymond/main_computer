"""Application-specific mechanics used by the generic MCEL authoring pipeline.

Generic authorities own discovery, source binding, validation, reporting,
promotion policy, and proof decisions.  A profile contributes only mechanics
that portable Application IR cannot yet project or execute on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class AppAuthoringProfile:
    app_id: str
    profile_id: str
    projection_profile: str
    fixture_ir: Path | None
    candidate_source: Path | None
    authoring_frontend: str
    project_candidate: Callable[..., Any]
    rehearse_promotion: Callable[..., Any]
    execute_promotion: Callable[..., Any]
    rollback_promotion: Callable[..., Any]
    run_node_probe: Callable[[Path, str], Mapping[str, Any]] | None
    run_browser_probe: Callable[[Path, bool, str], Mapping[str, Any]] | None
    build_effect_accounting: Callable[..., Mapping[str, Any]] | None
    run_ir_native_proof: Callable[..., Mapping[str, Any]] | None
    run_candidate_evidence: Callable[..., Any] | None
    resolve_scenario_operation: Callable[[Mapping[str, Any]], str]
    receipt_code_aliases: Mapping[str, str]
    promotion_supported: bool = True
    promotion_rehearsal_supported: bool = True
    portable_ir_projection_complete: bool = True


class AppAuthoringProfileError(RuntimeError):
    """Raised when no generic-pipeline mechanics profile exists for an app."""


def _counter_profile() -> AppAuthoringProfile:
    from main_computer.mcel_counter_candidate_evidence import (
        _build_effect_accounting,
        _run_browser_effect_probe,
        _run_counter_effect_probe,
        run_counter_candidate_evidence,
    )
    from main_computer.mcel_counter_candidate_projection import project_counter_candidate
    from main_computer.mcel_counter_compatibility import DEFAULT_DSL_SOURCE, DEFAULT_FIXTURE_IR
    from main_computer.mcel_counter_ir_native_proof import run_counter_ir_native_intent_proof
    from main_computer.mcel_counter_promotion import execute_counter_promotion, rollback_counter_promotion
    from main_computer.mcel_counter_promotion_rehearsal import rehearse_counter_promotion

    def resolve_scenario_operation(scenario: Mapping[str, Any]) -> str:
        intent_id = str(((scenario.get("intent") or {}).get("ref") or ""))
        suffix = intent_id.removeprefix("intent:")
        claims = list(scenario.get("steps") or [])
        refused = any(isinstance(claim, Mapping) and claim.get("kind") == "claim.receipt-disposition" for claim in claims)
        return "stale" if suffix == "increment" and refused else suffix

    return AppAuthoringProfile(
        app_id="contract-counter",
        profile_id="mcel.contract-counter.authoring-profile.v1",
        projection_profile="mcel.counter.explicit-projection.v1",
        fixture_ir=DEFAULT_FIXTURE_IR,
        candidate_source=DEFAULT_DSL_SOURCE,
        authoring_frontend="mcel.dsl.v1",
        project_candidate=project_counter_candidate,
        rehearse_promotion=rehearse_counter_promotion,
        execute_promotion=execute_counter_promotion,
        rollback_promotion=rollback_counter_promotion,
        run_node_probe=_run_counter_effect_probe,
        run_browser_probe=_run_browser_effect_probe,
        build_effect_accounting=_build_effect_accounting,
        run_ir_native_proof=run_counter_ir_native_intent_proof,
        run_candidate_evidence=run_counter_candidate_evidence,
        resolve_scenario_operation=resolve_scenario_operation,
        receipt_code_aliases={"REVISION_STALE": "SCM_STALE_REVISION"},
    )


def _workbench_profile() -> AppAuthoringProfile:
    from main_computer.mcel_workbench_candidate_evidence import run_workbench_candidate_evidence
    from main_computer.mcel_workbench_candidate_projection import (
        DEFAULT_DSL_SOURCE,
        DEFAULT_FIXTURE_IR,
        PROJECTION_PROFILE,
        project_workbench_candidate,
    )
    from main_computer.mcel_workbench_ir_native_proof import run_workbench_ir_native_intent_proof
    from main_computer.mcel_workbench_promotion import (
        execute_workbench_promotion,
        rollback_workbench_promotion,
    )
    from main_computer.mcel_workbench_promotion_rehearsal import rehearse_workbench_promotion

    def resolve_scenario_operation(scenario: Mapping[str, Any]) -> str:
        intent_id = str(((scenario.get("intent") or {}).get("ref") or ""))
        return intent_id.removeprefix("intent:")

    return AppAuthoringProfile(
        app_id="contract-workbench",
        profile_id="mcel.contract-workbench.authoring-profile.v2",
        projection_profile=PROJECTION_PROFILE,
        fixture_ir=DEFAULT_FIXTURE_IR,
        candidate_source=DEFAULT_DSL_SOURCE,
        authoring_frontend="mcel.dsl.v1",
        project_candidate=project_workbench_candidate,
        rehearse_promotion=rehearse_workbench_promotion,
        execute_promotion=execute_workbench_promotion,
        rollback_promotion=rollback_workbench_promotion,
        run_node_probe=None,
        run_browser_probe=None,
        build_effect_accounting=None,
        run_ir_native_proof=run_workbench_ir_native_intent_proof,
        run_candidate_evidence=run_workbench_candidate_evidence,
        resolve_scenario_operation=resolve_scenario_operation,
        receipt_code_aliases={"REVISION_STALE": "SCM_STALE_REVISION"},
        promotion_supported=True,
        promotion_rehearsal_supported=True,
        portable_ir_projection_complete=True,
    )


def get_app_authoring_profile(app_id: str) -> AppAuthoringProfile:
    normalized = str(app_id or "").strip()
    if normalized == "contract-counter":
        return _counter_profile()
    if normalized == "contract-workbench":
        return _workbench_profile()
    raise AppAuthoringProfileError(
        f"No generic MCEL authoring profile is registered for application {normalized!r}."
    )


def registered_app_authoring_profiles() -> tuple[str, ...]:
    return ("contract-counter", "contract-workbench")
