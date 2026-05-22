#!/usr/bin/env python3
"""
Smoke test: mode-aware terminal artifact contract.

This locks down the next generated-editor boundary:

  verified proposal != terminal artifact
  full-file replacement != terminal artifact
  terminal artifact acceptance is mode-aware
  promotable is derived only from accepted_terminal_artifact
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any

from rag_terminal_artifact_contract import (
    ACCEPTED_TERMINAL_ARTIFACT,
    NONTERMINAL_RESULT,
    SNAPSHOT_ZIP,
    VERIFIED_BUNDLE,
    evaluate_terminal_artifact_contract,
)


@dataclass(frozen=True)
class Case:
    name: str
    candidate: dict[str, Any]
    expected_promotable: bool
    expected_terminal_state: str
    expected_failed_gate: str | None = None
    expected_verification_level: str | None = None


def snapshot_candidate(**overrides: Any) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "artifact_mode": SNAPSHOT_ZIP,
        "replacement_files": [{"path": "main_computer/web/applications/scripts/chat-console.js", "exists": True}],
        "root_contract_valid": True,
        "new_patch_usable": True,
        "dry_run_command": "python new_patch.py terminal_snapshot.zip --dry-run",
    }
    candidate.update(overrides)
    return candidate


def bundle_candidate(**overrides: Any) -> dict[str, Any]:
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


def cases() -> list[Case]:
    return [
        Case(
            name="verified_proposal_alone_is_nonterminal",
            candidate={"proposal_verified": True},
            expected_promotable=False,
            expected_terminal_state=NONTERMINAL_RESULT,
            expected_failed_gate="artifact_mode",
        ),
        Case(
            name="full_file_replacement_without_artifact_contract_is_nonterminal",
            candidate={
                "artifact_mode": SNAPSHOT_ZIP,
                "replacement_files": [{"path": "main_computer/web/applications/scripts/chat-console.js", "exists": True}],
                "root_contract_valid": True,
            },
            expected_promotable=False,
            expected_terminal_state=NONTERMINAL_RESULT,
            expected_failed_gate="new_patch_usable",
        ),
        Case(
            name="missing_artifact_mode_is_nonterminal",
            candidate={
                "replacement_files": [{"path": "main_computer/web/applications/scripts/chat-console.js", "exists": True}],
                "root_contract_valid": True,
                "new_patch_usable": True,
                "dry_run_command": "python new_patch.py terminal_snapshot.zip --dry-run",
            },
            expected_promotable=False,
            expected_terminal_state=NONTERMINAL_RESULT,
            expected_failed_gate="artifact_mode",
        ),
        Case(
            name="unknown_artifact_mode_is_nonterminal",
            candidate=snapshot_candidate(artifact_mode="raw_patch"),
            expected_promotable=False,
            expected_terminal_state=NONTERMINAL_RESULT,
            expected_failed_gate="artifact_mode",
        ),
        Case(
            name="snapshot_zip_contract_accepts_terminal_artifact",
            candidate=snapshot_candidate(),
            expected_promotable=True,
            expected_terminal_state=ACCEPTED_TERMINAL_ARTIFACT,
            expected_verification_level="no_reference",
        ),
        Case(
            name="snapshot_zip_without_replacement_files_is_nonterminal",
            candidate=snapshot_candidate(replacement_files=[]),
            expected_promotable=False,
            expected_terminal_state=NONTERMINAL_RESULT,
            expected_failed_gate="replacement_files_exist",
        ),
        Case(
            name="snapshot_zip_with_unsafe_path_is_nonterminal",
            candidate=snapshot_candidate(replacement_files=[{"path": "../main_computer/web/applications/scripts/chat-console.js", "exists": True}]),
            expected_promotable=False,
            expected_terminal_state=NONTERMINAL_RESULT,
            expected_failed_gate="repo_relative_paths_safe",
        ),
        Case(
            name="snapshot_zip_with_wrong_root_contract_is_nonterminal",
            candidate=snapshot_candidate(root_contract_valid=False),
            expected_promotable=False,
            expected_terminal_state=NONTERMINAL_RESULT,
            expected_failed_gate="root_contract_valid",
        ),
        Case(
            name="verified_bundle_contract_accepts_terminal_artifact",
            candidate=bundle_candidate(),
            expected_promotable=True,
            expected_terminal_state=ACCEPTED_TERMINAL_ARTIFACT,
            expected_verification_level="exact",
        ),
        Case(
            name="verified_bundle_missing_manifest_is_nonterminal",
            candidate=bundle_candidate(manifest_json_exists=False),
            expected_promotable=False,
            expected_terminal_state=NONTERMINAL_RESULT,
            expected_failed_gate="manifest_json_exists",
        ),
        Case(
            name="verified_bundle_missing_reference_patch_is_nonterminal",
            candidate=bundle_candidate(reference_patch_exists=False),
            expected_promotable=False,
            expected_terminal_state=NONTERMINAL_RESULT,
            expected_failed_gate="reference_patch_exists",
        ),
        Case(
            name="verified_bundle_reference_mismatch_is_nonterminal",
            candidate=bundle_candidate(reference_matches_replacement_files=False),
            expected_promotable=False,
            expected_terminal_state=NONTERMINAL_RESULT,
            expected_failed_gate="reference_matches_replacement_files",
        ),
        Case(
            name="verified_bundle_with_unsafe_path_is_nonterminal",
            candidate=bundle_candidate(replacement_files=[{"path": "/absolute/main_computer/web/applications/scripts/chat-console.js", "exists": True}]),
            expected_promotable=False,
            expected_terminal_state=NONTERMINAL_RESULT,
            expected_failed_gate="repo_relative_paths_safe",
        ),
    ]


def main() -> int:
    failures: list[dict[str, Any]] = []
    case_reports: list[dict[str, Any]] = []

    for case in cases():
        report = evaluate_terminal_artifact_contract(case.candidate)
        derived_promotable = (
            report["terminal_state"] == ACCEPTED_TERMINAL_ARTIFACT
            and report["artifact_contract_passed"] is True
        )
        checks = {
            "promotable": report["promotable"] is case.expected_promotable,
            "terminal_state": report["terminal_state"] == case.expected_terminal_state,
            "failed_gate": report.get("failed_gate") == case.expected_failed_gate,
            "verification_level": report.get("verification", {}).get("level") == case.expected_verification_level,
            "promotable_is_derived": report["promotable"] is derived_promotable,
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
        "mode": "rag_terminal_artifact_contract_smoke",
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
