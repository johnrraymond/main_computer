#!/usr/bin/env python3
"""
Hostile smoke: generated patch artifacts must not be trusted by metadata alone.

This deterministic, no-model smoke exercises artifact candidates at the adapter
boundary and proves unsafe or unverifiable handoffs are rejected before they can
become patch-promotable terminal results.

It intentionally does not use wrong top-level snapshot names as a raw snapshot
rejection signal: new_patch.py raw snapshot mode strips one top-level snapshot
directory by design, so root-name-only rejection would be brittle.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from rag_terminal_artifact_contract import (
    NONTERMINAL_RESULT as NONTERMINAL_ARTIFACT_RESULT,
    VERIFIED_BUNDLE,
    evaluate_terminal_artifact_contract,
)
from rag_terminal_result_contract import (
    NONTERMINAL_RESULT,
    PATCH_ARTIFACT,
    evaluate_terminal_result_contract,
)


MODE = "rag_generated_editor_patch_artifact_adapter_hostile_smoke"
SAFE_TARGET = "main_computer/web/applications/scripts/chat-console.js"


@dataclass(frozen=True)
class HostileCase:
    name: str
    replacement_paths: tuple[str, ...]
    replacement_files_materialized: bool
    reference_matches_replacement_files: bool
    expected_failed_gate: str


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def output_root(root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = root / "debug_assets" / MODE / stamp
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def materialize_bundle(
    *,
    case: HostileCase,
    case_dir: Path,
) -> dict[str, Any]:
    """Create minimal verified-bundle-shaped evidence for one hostile case.

    The terminal artifact contract consumes normalized evidence booleans rather
    than reading this directory directly.  The files are still materialized here
    so every hostile candidate has inspectable adapter-boundary evidence under
    debug_assets.
    """

    bundle_dir = case_dir / "bundle"
    files_dir = bundle_dir / "files"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    files_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "artifact_mode": VERIFIED_BUNDLE,
        "replacement_files": list(case.replacement_paths),
        "reference_patch": "reference.patch",
        "case": case.name,
    }
    write_text(bundle_dir / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    write_text(
        bundle_dir / "reference.patch",
        "\n".join(
            [
                "diff --git a/main_computer/web/applications/scripts/chat-console.js b/main_computer/web/applications/scripts/chat-console.js",
                "--- a/main_computer/web/applications/scripts/chat-console.js",
                "+++ b/main_computer/web/applications/scripts/chat-console.js",
                "@@ -1 +1 @@",
                "-old hostile smoke fixture",
                "+new hostile smoke fixture",
                "",
            ]
        ),
    )

    materialized_replacements: list[str] = []
    if case.replacement_files_materialized:
        for rel_path in case.replacement_paths:
            # Unsafe declared paths are not written through.  This smoke must
            # prove they are rejected at the repo-relative-path gate without
            # creating traversal side effects on disk.
            if ".." in Path(rel_path.replace("\\", "/")).parts or rel_path.startswith(("/", "\\")):
                continue
            replacement_path = files_dir / rel_path
            write_text(replacement_path, "new hostile smoke fixture\n")
            materialized_replacements.append(rel_path)

    return {
        "bundle_dir": str(bundle_dir),
        "manifest_path": str(bundle_dir / "manifest.json"),
        "reference_patch_path": str(bundle_dir / "reference.patch"),
        "materialized_replacements": materialized_replacements,
    }


def replacement_entry_for_case(case: HostileCase, path: str) -> dict[str, Any]:
    if case.expected_failed_gate == "replacement_files_exist":
        exists = False
    else:
        # For unsafe-path cases, deliberately make the rest of the evidence look
        # usable so the first failing gate must be repo_relative_paths_safe.
        exists = True
    return {"path": path, "exists": exists}


def artifact_candidate_for_case(case: HostileCase) -> dict[str, Any]:
    return {
        "artifact_mode": VERIFIED_BUNDLE,
        "manifest_json_exists": True,
        "reference_patch_exists": True,
        "replacement_files": [replacement_entry_for_case(case, path) for path in case.replacement_paths],
        "reference_matches_replacement_files": case.reference_matches_replacement_files,
        "new_patch_usable": True,
        "dry_run_command": "python new_patch.py hostile_verified_bundle.zip --dry-run",
    }


def hostile_cases() -> list[HostileCase]:
    return [
        HostileCase(
            name="unsafe_verified_bundle_path_is_rejected",
            replacement_paths=("../outside.py", "files/../outside.py", "main_computer/../../outside.py"),
            replacement_files_materialized=True,
            reference_matches_replacement_files=True,
            expected_failed_gate="repo_relative_paths_safe",
        ),
        HostileCase(
            name="missing_replacement_file_is_rejected",
            replacement_paths=(SAFE_TARGET,),
            replacement_files_materialized=False,
            reference_matches_replacement_files=True,
            expected_failed_gate="replacement_files_exist",
        ),
        HostileCase(
            name="verified_bundle_reference_mismatch_is_rejected_without_fuzz",
            replacement_paths=(SAFE_TARGET,),
            replacement_files_materialized=True,
            reference_matches_replacement_files=False,
            expected_failed_gate="reference_matches_replacement_files",
        ),
    ]


def evaluate_case(case: HostileCase, report_dir: Path) -> dict[str, Any]:
    case_dir = report_dir / case.name
    evidence = materialize_bundle(case=case, case_dir=case_dir)

    artifact_candidate = artifact_candidate_for_case(case)
    artifact_report = evaluate_terminal_artifact_contract(artifact_candidate)

    result_candidate = {
        "result_mode": PATCH_ARTIFACT,
        "artifact": artifact_candidate,
    }
    result_report = evaluate_terminal_result_contract(result_candidate)

    expected_result_failed_gate = f"artifact.{case.expected_failed_gate}"
    checks = {
        "artifact_contract_failed": artifact_report.get("artifact_contract_passed") is False,
        "artifact_terminal_state": artifact_report.get("terminal_state") == NONTERMINAL_ARTIFACT_RESULT,
        "artifact_promotable_false": artifact_report.get("promotable") is False,
        "artifact_failed_gate": artifact_report.get("failed_gate") == case.expected_failed_gate,
        "artifact_verification_level_null": artifact_report.get("verification", {}).get("level") is None,
        "result_contract_failed": result_report.get("result_contract_passed") is False,
        "result_terminal_state": result_report.get("terminal_state") == NONTERMINAL_RESULT,
        "result_promotable_false": result_report.get("promotable") is False,
        "result_failed_gate": result_report.get("failed_gate") == expected_result_failed_gate,
        "result_verification_level_null": result_report.get("verification", {}).get("level") is None,
    }

    case_report = {
        "name": case.name,
        "ok": all(checks.values()),
        "expected_failed_gate": case.expected_failed_gate,
        "expected_result_failed_gate": expected_result_failed_gate,
        "checks": checks,
        "evidence": evidence,
        "artifact_candidate": artifact_candidate,
        "artifact_report": artifact_report,
        "result_report": result_report,
    }
    write_text(case_dir / "case_report.json", json.dumps(case_report, indent=2, sort_keys=True) + "\n")
    return case_report


def main() -> int:
    root = repo_root()
    report_dir = output_root(root)

    cases = [evaluate_case(case, report_dir) for case in hostile_cases()]
    failures = [case for case in cases if not case["ok"]]

    summary = {
        "mode": MODE,
        "ok": not failures,
        "repo_root": str(root),
        "report_dir": str(report_dir),
        "case_count": len(cases),
        "passed_case_count": len(cases) - len(failures),
        "failed_case_count": len(failures),
        "cases": cases,
    }

    report_path = report_dir / "final_report.json"
    summary["report_path"] = str(report_path)
    write_text(report_path, json.dumps(summary, indent=2, sort_keys=True) + "\n")

    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"\nWrote report: {report_path}")
    if failures:
        print(json.dumps({"ok": False, "failures": failures}, indent=2, sort_keys=True), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
