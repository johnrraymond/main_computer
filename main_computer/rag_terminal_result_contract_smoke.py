#!/usr/bin/env python3
"""
Smoke test: terminal result contracts are result-mode-specific.

This smoke is intentionally above the patch-artifact contract smoke.  It locks
down the broader rule that "done" means the declared result mode's contract
passed.  Patch artifacts are one terminal result mode, not the definition of
terminal completion.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any

from rag_terminal_artifact_contract import SNAPSHOT_ZIP, VERIFIED_BUNDLE
from rag_terminal_result_contract import (
    ACCEPTED_TERMINAL_RESULT,
    ALREADY_APPLIED_NOOP,
    DIAGNOSIS_ONLY,
    EDIT_PROPOSAL,
    FULL_FILE_REPLACEMENT,
    NONTERMINAL_RESULT,
    PATCH_ARTIFACT,
    RUNTIME_VERIFICATION_REPORT,
    evaluate_terminal_result_contract,
)


@dataclass(frozen=True)
class Case:
    name: str
    candidate: dict[str, Any]
    expected_terminal_state: str
    expected_promotable: bool
    expected_failed_gate: str | None = None
    expected_verification_level: str | None = None


def snapshot_artifact(**overrides: Any) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "artifact_mode": SNAPSHOT_ZIP,
        "replacement_files": [{"path": "main_computer/web/applications/scripts/chat-console.js", "exists": True}],
        "root_contract_valid": True,
        "new_patch_usable": True,
        "dry_run_command": "python new_patch.py terminal_snapshot.zip --dry-run",
    }
    candidate.update(overrides)
    return candidate


def bundle_artifact(**overrides: Any) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "artifact_mode": VERIFIED_BUNDLE,
        "manifest_json_exists": True,
        "reference_patch_exists": True,
        "replacement_files": [{"path": "main_computer/web/applications/scripts/chat-console.js", "exists": True}],
        "reference_matches_replacement_files": True,
        "new_patch_usable": True,
        "dry_run_command": "python new_patch.py terminal_bundle.zip --dry-run",
    }
    candidate.update(overrides)
    return candidate


def diagnosis_candidate(**overrides: Any) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "result_mode": DIAGNOSIS_ONLY,
        "explanation": "The failure is caused by a stale target-state assumption.",
        "evidence_backed": True,
        "anchored_claims": ["current file still contains old literal"],
    }
    candidate.update(overrides)
    return candidate


def edit_proposal_candidate(**overrides: Any) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "result_mode": EDIT_PROPOSAL,
        "proposal": "Replace the visible running-state button label with Cancel.",
        "proposal_verified": True,
        "proposal_scope": "verified_excerpt",
    }
    candidate.update(overrides)
    return candidate


def full_file_replacement_candidate(**overrides: Any) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "result_mode": FULL_FILE_REPLACEMENT,
        "replacement_files": [{"path": "main_computer/web/applications/scripts/chat-console.js", "exists": True}],
        "replacement_materialized": True,
    }
    candidate.update(overrides)
    return candidate


def noop_candidate(**overrides: Any) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "result_mode": ALREADY_APPLIED_NOOP,
        "target_state_proven": True,
        "no_changes_needed": True,
        "evidence_backed": True,
        "real_repo_modified": False,
    }
    candidate.update(overrides)
    return candidate


def runtime_candidate(**overrides: Any) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "result_mode": RUNTIME_VERIFICATION_REPORT,
        "runtime_exercised": True,
        "observed_expected_behavior": True,
        "runtime_evidence_captured": True,
    }
    candidate.update(overrides)
    return candidate


def patch_artifact_candidate(artifact: dict[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "result_mode": PATCH_ARTIFACT,
        "artifact": artifact if artifact is not None else snapshot_artifact(),
    }
    candidate.update(overrides)
    return candidate


def cases() -> list[Case]:
    return [
        Case(
            name="missing_result_mode_is_nonterminal",
            candidate={"proposal_verified": True},
            expected_terminal_state=NONTERMINAL_RESULT,
            expected_promotable=False,
            expected_failed_gate="result_mode",
        ),
        Case(
            name="unknown_result_mode_is_nonterminal",
            candidate={"result_mode": "make_everything_good", "contract_passed": True},
            expected_terminal_state=NONTERMINAL_RESULT,
            expected_promotable=False,
            expected_failed_gate="result_mode",
        ),
        Case(
            name="verified_proposal_without_declared_result_mode_is_nonterminal",
            candidate={
                "proposal": "Replace the visible label.",
                "proposal_verified": True,
                "proposal_scope": "verified_excerpt",
            },
            expected_terminal_state=NONTERMINAL_RESULT,
            expected_promotable=False,
            expected_failed_gate="result_mode",
        ),
        Case(
            name="edit_proposal_mode_accepts_verified_proposal_but_not_promotable",
            candidate=edit_proposal_candidate(),
            expected_terminal_state=ACCEPTED_TERMINAL_RESULT,
            expected_promotable=False,
            expected_verification_level="proposal_verified",
        ),
        Case(
            name="edit_proposal_mode_rejects_unverified_proposal",
            candidate=edit_proposal_candidate(proposal_verified=False),
            expected_terminal_state=NONTERMINAL_RESULT,
            expected_promotable=False,
            expected_failed_gate="proposal_verified",
        ),
        Case(
            name="full_file_replacement_mode_accepts_materialized_safe_replacement_but_not_promotable",
            candidate=full_file_replacement_candidate(),
            expected_terminal_state=ACCEPTED_TERMINAL_RESULT,
            expected_promotable=False,
            expected_verification_level="replacement_materialized",
        ),
        Case(
            name="full_file_replacement_mode_rejects_unsafe_path",
            candidate=full_file_replacement_candidate(
                replacement_files=[{"path": "../main_computer/web/applications/scripts/chat-console.js", "exists": True}]
            ),
            expected_terminal_state=NONTERMINAL_RESULT,
            expected_promotable=False,
            expected_failed_gate="repo_relative_paths_safe",
        ),
        Case(
            name="patch_artifact_mode_accepts_snapshot_artifact_and_is_promotable",
            candidate=patch_artifact_candidate(snapshot_artifact()),
            expected_terminal_state=ACCEPTED_TERMINAL_RESULT,
            expected_promotable=True,
            expected_verification_level="no_reference",
        ),
        Case(
            name="patch_artifact_mode_accepts_verified_bundle_and_is_promotable",
            candidate=patch_artifact_candidate(bundle_artifact()),
            expected_terminal_state=ACCEPTED_TERMINAL_RESULT,
            expected_promotable=True,
            expected_verification_level="exact",
        ),
        Case(
            name="patch_artifact_mode_rejects_only_replacement_without_artifact_contract",
            candidate=patch_artifact_candidate(
                {
                    "replacement_files": [{"path": "main_computer/web/applications/scripts/chat-console.js", "exists": True}]
                }
            ),
            expected_terminal_state=NONTERMINAL_RESULT,
            expected_promotable=False,
            expected_failed_gate="artifact.artifact_mode",
        ),
        Case(
            name="patch_artifact_mode_rejects_bad_snapshot_artifact",
            candidate=patch_artifact_candidate(snapshot_artifact(replacement_files=[])),
            expected_terminal_state=NONTERMINAL_RESULT,
            expected_promotable=False,
            expected_failed_gate="artifact.replacement_files_exist",
        ),
        Case(
            name="diagnosis_only_mode_accepts_evidence_backed_explanation_but_not_promotable",
            candidate=diagnosis_candidate(),
            expected_terminal_state=ACCEPTED_TERMINAL_RESULT,
            expected_promotable=False,
            expected_verification_level="evidence_backed",
        ),
        Case(
            name="diagnosis_only_mode_rejects_unanchored_explanation",
            candidate=diagnosis_candidate(anchored_claims=[]),
            expected_terminal_state=NONTERMINAL_RESULT,
            expected_promotable=False,
            expected_failed_gate="anchored_claims_exist",
        ),
        Case(
            name="already_applied_noop_accepts_target_state_proof_but_not_promotable",
            candidate=noop_candidate(),
            expected_terminal_state=ACCEPTED_TERMINAL_RESULT,
            expected_promotable=False,
            expected_verification_level="target_state_proven",
        ),
        Case(
            name="already_applied_noop_rejects_missing_target_state_proof",
            candidate=noop_candidate(target_state_proven=False),
            expected_terminal_state=NONTERMINAL_RESULT,
            expected_promotable=False,
            expected_failed_gate="target_state_proven",
        ),
        Case(
            name="runtime_verification_report_accepts_runtime_evidence_but_not_promotable",
            candidate=runtime_candidate(),
            expected_terminal_state=ACCEPTED_TERMINAL_RESULT,
            expected_promotable=False,
            expected_verification_level="runtime_observed",
        ),
        Case(
            name="runtime_verification_report_rejects_missing_runtime_evidence",
            candidate=runtime_candidate(runtime_evidence_captured=False),
            expected_terminal_state=NONTERMINAL_RESULT,
            expected_promotable=False,
            expected_failed_gate="runtime_evidence_captured",
        ),
    ]


def main() -> int:
    failures: list[dict[str, Any]] = []
    case_reports: list[dict[str, Any]] = []

    for case in cases():
        report = evaluate_terminal_result_contract(case.candidate)
        accepted = report["terminal_state"] == ACCEPTED_TERMINAL_RESULT
        patch_artifact_promotable = (
            accepted
            and report["result_mode"] == PATCH_ARTIFACT
            and report["result_contract_passed"] is True
            and report.get("artifact_report", {}).get("promotable") is True
        )
        non_artifact_terminal_not_promotable = (
            not accepted
            or report["result_mode"] == PATCH_ARTIFACT
            or report["promotable"] is False
        )
        checks = {
            "terminal_state": report["terminal_state"] == case.expected_terminal_state,
            "promotable": report["promotable"] is case.expected_promotable,
            "failed_gate": report.get("failed_gate") == case.expected_failed_gate,
            "verification_level": report.get("verification", {}).get("level") == case.expected_verification_level,
            "patch_promotable_is_derived": report["promotable"] is patch_artifact_promotable,
            "non_artifact_terminal_results_are_not_promotable": non_artifact_terminal_not_promotable,
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
        "mode": "rag_terminal_result_contract_smoke",
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
