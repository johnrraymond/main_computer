#!/usr/bin/env python3
"""
Terminal result contract helpers for generated-editor style workflows.

A run is not "done" merely because an intermediate object exists.  It is done
only when the declared result mode satisfies that mode's terminal contract.

Patch artifacts are one supported terminal result mode, but they are not the
only possible terminal result.
"""

from __future__ import annotations

from typing import Any

from rag_terminal_artifact_contract import (
    ACCEPTED_TERMINAL_ARTIFACT,
    evaluate_terminal_artifact_contract,
    repo_relative_paths_are_safe,
)


ACCEPTED_TERMINAL_RESULT = "accepted_terminal_result"
NONTERMINAL_RESULT = "nonterminal_result"

DIAGNOSIS_ONLY = "diagnosis_only"
EDIT_PROPOSAL = "edit_proposal"
FULL_FILE_REPLACEMENT = "full_file_replacement"
PATCH_ARTIFACT = "patch_artifact"
ALREADY_APPLIED_NOOP = "already_applied_noop"
RUNTIME_VERIFICATION_REPORT = "runtime_verification_report"

SUPPORTED_RESULT_MODES = {
    DIAGNOSIS_ONLY,
    EDIT_PROPOSAL,
    FULL_FILE_REPLACEMENT,
    PATCH_ARTIFACT,
    ALREADY_APPLIED_NOOP,
    RUNTIME_VERIFICATION_REPORT,
}


def _bool(candidate: dict[str, Any], key: str) -> bool:
    return candidate.get(key) is True


def _non_empty_string(candidate: dict[str, Any], key: str) -> bool:
    value = candidate.get(key)
    return isinstance(value, str) and bool(value.strip())


def _non_empty_list(candidate: dict[str, Any], key: str) -> bool:
    value = candidate.get(key)
    return isinstance(value, list) and bool(value)


def _first_failed_gate(contract: dict[str, bool], ordered_gates: list[str]) -> str | None:
    for gate in ordered_gates:
        if contract.get(gate) is not True:
            return gate
    return None


def _replacement_paths(candidate: dict[str, Any]) -> list[Any]:
    replacement_files = candidate.get("replacement_files")
    if not isinstance(replacement_files, list):
        return []
    paths: list[Any] = []
    for entry in replacement_files:
        if isinstance(entry, dict):
            paths.append(entry.get("path", entry.get("target_file")))
        else:
            paths.append(entry)
    return paths


def _replacement_files_exist(candidate: dict[str, Any]) -> bool:
    replacement_files = candidate.get("replacement_files")
    if not isinstance(replacement_files, list) or not replacement_files:
        return False
    for entry in replacement_files:
        if isinstance(entry, dict):
            if entry.get("exists") is not True:
                return False
        elif not isinstance(entry, str) or not entry.strip():
            return False
    return True


def _diagnosis_only_contract(candidate: dict[str, Any]) -> tuple[dict[str, bool], list[str]]:
    contract = {
        "explanation_exists": _non_empty_string(candidate, "explanation"),
        "evidence_backed": _bool(candidate, "evidence_backed"),
        "anchored_claims_exist": _non_empty_list(candidate, "anchored_claims"),
    }
    return contract, ["explanation_exists", "evidence_backed", "anchored_claims_exist"]


def _edit_proposal_contract(candidate: dict[str, Any]) -> tuple[dict[str, bool], list[str]]:
    contract = {
        "proposal_exists": _non_empty_string(candidate, "proposal"),
        "proposal_verified": _bool(candidate, "proposal_verified"),
        "proposal_scope_declared": _non_empty_string(candidate, "proposal_scope"),
    }
    return contract, ["proposal_exists", "proposal_verified", "proposal_scope_declared"]


def _full_file_replacement_contract(candidate: dict[str, Any]) -> tuple[dict[str, bool], list[str]]:
    replacement_paths = _replacement_paths(candidate)
    contract = {
        "replacement_files_exist": _replacement_files_exist(candidate),
        "repo_relative_paths_safe": repo_relative_paths_are_safe(replacement_paths),
        "replacement_materialized": _bool(candidate, "replacement_materialized"),
    }
    return contract, ["replacement_files_exist", "repo_relative_paths_safe", "replacement_materialized"]


def _already_applied_noop_contract(candidate: dict[str, Any]) -> tuple[dict[str, bool], list[str]]:
    contract = {
        "target_state_proven": _bool(candidate, "target_state_proven"),
        "no_changes_needed": _bool(candidate, "no_changes_needed"),
        "evidence_backed": _bool(candidate, "evidence_backed"),
        "real_repo_modified_false": candidate.get("real_repo_modified") is False,
    }
    return contract, ["target_state_proven", "no_changes_needed", "evidence_backed", "real_repo_modified_false"]


def _runtime_verification_report_contract(candidate: dict[str, Any]) -> tuple[dict[str, bool], list[str]]:
    contract = {
        "runtime_exercised": _bool(candidate, "runtime_exercised"),
        "observed_expected_behavior": _bool(candidate, "observed_expected_behavior"),
        "runtime_evidence_captured": _bool(candidate, "runtime_evidence_captured"),
    }
    return contract, ["runtime_exercised", "observed_expected_behavior", "runtime_evidence_captured"]


def _verification_level_for_result_mode(result_mode: str) -> str:
    if result_mode == DIAGNOSIS_ONLY:
        return "evidence_backed"
    if result_mode == EDIT_PROPOSAL:
        return "proposal_verified"
    if result_mode == FULL_FILE_REPLACEMENT:
        return "replacement_materialized"
    if result_mode == ALREADY_APPLIED_NOOP:
        return "target_state_proven"
    if result_mode == RUNTIME_VERIFICATION_REPORT:
        return "runtime_observed"
    return "contract_passed"


def evaluate_terminal_result_contract(candidate: dict[str, Any] | None) -> dict[str, Any]:
    """Evaluate whether a generated-editor result reached its declared terminal mode.

    Terminal completion is result-mode-specific.  A verified proposal can be a
    terminal result only for an edit-proposal task; it is nonterminal when the
    declared result mode is patch_artifact.  A patch artifact delegates to the
    mode-aware terminal artifact contract helper.
    """

    if not isinstance(candidate, dict):
        candidate = {}

    result_mode = candidate.get("result_mode")
    report: dict[str, Any] = {
        "terminal_state": NONTERMINAL_RESULT,
        "result_mode": result_mode,
        "result_contract": {},
        "result_contract_passed": False,
        "artifact_report": None,
        "verification": {
            "level": None,
        },
        "failed_gate": None,
        "promotable": False,
    }

    if result_mode not in SUPPORTED_RESULT_MODES:
        report["failed_gate"] = "result_mode"
        return report

    if result_mode == PATCH_ARTIFACT:
        artifact_candidate = candidate.get("artifact")
        artifact_report = evaluate_terminal_artifact_contract(
            artifact_candidate if isinstance(artifact_candidate, dict) else {}
        )
        artifact_passed = artifact_report.get("terminal_state") == ACCEPTED_TERMINAL_ARTIFACT
        artifact_failed_gate = artifact_report.get("failed_gate")
        contract = {
            "artifact_contract_passed": artifact_passed,
        }
        report.update(
            {
                "result_contract": contract,
                "result_contract_passed": artifact_passed,
                "artifact_report": artifact_report,
                "verification": artifact_report.get("verification", {"level": None}),
                "failed_gate": None if artifact_passed else f"artifact.{artifact_failed_gate or 'contract'}",
            }
        )
        if artifact_passed:
            report["terminal_state"] = ACCEPTED_TERMINAL_RESULT

        report["promotable"] = (
            report["terminal_state"] == ACCEPTED_TERMINAL_RESULT
            and report["result_contract_passed"] is True
            and artifact_report.get("promotable") is True
        )
        return report

    if result_mode == DIAGNOSIS_ONLY:
        contract, ordered_gates = _diagnosis_only_contract(candidate)
    elif result_mode == EDIT_PROPOSAL:
        contract, ordered_gates = _edit_proposal_contract(candidate)
    elif result_mode == FULL_FILE_REPLACEMENT:
        contract, ordered_gates = _full_file_replacement_contract(candidate)
    elif result_mode == ALREADY_APPLIED_NOOP:
        contract, ordered_gates = _already_applied_noop_contract(candidate)
    elif result_mode == RUNTIME_VERIFICATION_REPORT:
        contract, ordered_gates = _runtime_verification_report_contract(candidate)
    else:
        # Defensive fallback for future edit drift.
        report["failed_gate"] = "result_mode"
        return report

    contract_passed = all(contract.get(gate) is True for gate in ordered_gates)
    report.update(
        {
            "result_contract": contract,
            "result_contract_passed": contract_passed,
            "failed_gate": _first_failed_gate(contract, ordered_gates),
            "verification": {
                "level": _verification_level_for_result_mode(result_mode) if contract_passed else None,
            },
        }
    )

    if contract_passed:
        report["terminal_state"] = ACCEPTED_TERMINAL_RESULT
        report["failed_gate"] = None

    # Single source of truth: non-artifact terminal results can be accepted, but
    # they are not patch-promotable handoff artifacts.
    report["promotable"] = False
    return report
