#!/usr/bin/env python3
"""
Hostile smoke: generated-editor terminal result candidates must reject false done/promotable states.

This smoke intentionally does not call a model.  It exercises generated-editor-like
terminal result candidates against the terminal result contract and proves that
negative evidence cannot be promoted into a terminal patch artifact.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any

from rag_terminal_result_contract import (
    ALREADY_APPLIED_NOOP,
    FULL_FILE_REPLACEMENT,
    NONTERMINAL_RESULT,
    PATCH_ARTIFACT,
    RUNTIME_VERIFICATION_REPORT,
    evaluate_terminal_result_contract,
)


@dataclass(frozen=True)
class HostileCase:
    name: str
    candidate: dict[str, Any]
    expected_failed_gate: str


SAFE_TARGET = "main_computer/web/applications/scripts/chat-console.js"


def full_file_replacement_candidate(**overrides: Any) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "result_mode": FULL_FILE_REPLACEMENT,
        "replacement_files": [{"path": SAFE_TARGET, "exists": True}],
        "replacement_materialized": True,
    }
    candidate.update(overrides)
    return candidate


def patch_artifact_candidate(**overrides: Any) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "result_mode": PATCH_ARTIFACT,
        "artifact": {
            # Hostile on purpose: replacement evidence without an artifact mode,
            # root contract, or patch adapter must not satisfy patch_artifact.
            "replacement_files": [{"path": SAFE_TARGET, "exists": True}],
            "replacement_materialized": True,
        },
    }
    candidate.update(overrides)
    return candidate


def already_applied_noop_candidate(**overrides: Any) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "result_mode": ALREADY_APPLIED_NOOP,
        "target_state_proven": False,
        "no_changes_needed": True,
        "evidence_backed": True,
        "real_repo_modified": False,
        # Diagnostic field for generated-editor callers.  The contract should
        # reject this through target_state_proven rather than trusting a noop claim.
        "old_literal_still_present": True,
    }
    candidate.update(overrides)
    return candidate


def runtime_verification_candidate(**overrides: Any) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "result_mode": RUNTIME_VERIFICATION_REPORT,
        "runtime_exercised": True,
        "observed_expected_behavior": True,
        "runtime_evidence_captured": False,
    }
    candidate.update(overrides)
    return candidate


def cases() -> list[HostileCase]:
    return [
        HostileCase(
            name="full_file_replacement_with_unsafe_path_is_nonterminal",
            candidate=full_file_replacement_candidate(
                replacement_files=[{"path": "../main_computer/web/applications/scripts/chat-console.js", "exists": True}]
            ),
            expected_failed_gate="repo_relative_paths_safe",
        ),
        HostileCase(
            name="full_file_replacement_without_replacement_file_is_nonterminal",
            candidate=full_file_replacement_candidate(replacement_files=[]),
            expected_failed_gate="replacement_files_exist",
        ),
        HostileCase(
            name="patch_artifact_with_only_replacement_materialized_evidence_is_nonterminal",
            candidate=patch_artifact_candidate(),
            expected_failed_gate="artifact.artifact_mode",
        ),
        HostileCase(
            name="already_applied_noop_with_old_literal_still_present_is_nonterminal",
            candidate=already_applied_noop_candidate(),
            expected_failed_gate="target_state_proven",
        ),
        HostileCase(
            name="runtime_verification_without_runtime_evidence_is_nonterminal",
            candidate=runtime_verification_candidate(),
            expected_failed_gate="runtime_evidence_captured",
        ),
    ]


def main() -> int:
    failures: list[dict[str, Any]] = []
    case_reports: list[dict[str, Any]] = []

    for case in cases():
        report = evaluate_terminal_result_contract(case.candidate)
        checks = {
            "terminal_state_is_nonterminal": report["terminal_state"] == NONTERMINAL_RESULT,
            "result_contract_did_not_pass": report["result_contract_passed"] is False,
            "failed_gate": report.get("failed_gate") == case.expected_failed_gate,
            "promotable_is_false": report["promotable"] is False,
        }
        case_report = {
            "name": case.name,
            "ok": all(checks.values()),
            "checks": checks,
            "report": report,
        }
        case_reports.append(case_report)
        if not case_report["ok"]:
            failures.append(case_report)

    summary = {
        "mode": "rag_generated_editor_terminal_result_hostile_smoke",
        "ok": not failures,
        "case_count": len(case_reports),
        "passed_case_count": len(case_reports) - len(failures),
        "failed_case_count": len(failures),
        "cases": case_reports,
    }

    print(json.dumps(summary, indent=2, sort_keys=True))
    if failures:
        print(json.dumps({"ok": False, "failures": failures}, indent=2, sort_keys=True), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
