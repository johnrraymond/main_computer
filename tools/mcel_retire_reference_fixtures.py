#!/usr/bin/env python3
"""Retire legacy MCEL reference fixtures after generic harness migration.

This tool exists because the normal Chat overlay artifact only creates or
replaces files.  It cannot express deletion by omission.  Run with --dry-run
first, then --apply from the repository root when the final fixture-retirement
prep patch has landed.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

FIXTURE_DELETION_TARGETS = (
    "mcel_apps/contract-counter",
    "mcel_apps/contract-workbench",
    "main_computer/mcel_counter_candidate_evidence.py",
    "main_computer/mcel_counter_candidate_projection.py",
    "main_computer/mcel_counter_compatibility.py",
    "main_computer/mcel_counter_effect_probe.py",
    "main_computer/mcel_counter_generated_contracts.py",
    "main_computer/mcel_counter_ir_native_proof.py",
    "main_computer/mcel_counter_legacy_fixture.py",
    "main_computer/mcel_counter_legacy_importer.py",
    "main_computer/mcel_counter_legacy_runtime.js",
    "main_computer/mcel_counter_promotion.py",
    "main_computer/mcel_counter_promotion_rehearsal.py",
    "main_computer/mcel_counter_reference_fixture_profile.py",
    "main_computer/mcel_workbench_candidate_evidence.py",
    "main_computer/mcel_workbench_candidate_projection.py",
    "main_computer/mcel_workbench_expression_profile.py",
    "main_computer/mcel_workbench_ir_native_proof.py",
    "main_computer/mcel_workbench_promotion.py",
    "main_computer/mcel_workbench_promotion_rehearsal.py",
    "main_computer/mcel_workbench_reference_fixture_profile.py",
    "main_computer/mcel_projection_profiles/contract_workbench_v1.py",
    "tools/mcel_counter_candidate_evidence.py",
    "tools/mcel_counter_candidate_projection.py",
    "tools/mcel_counter_compatibility.py",
    "tools/mcel_counter_ir_native_proof.py",
    "tools/mcel_counter_legacy_import.py",
    "tools/mcel_counter_promotion.py",
    "tools/mcel_counter_promotion_rehearsal.py",
    "tests/fixtures/mcel_application_ir/contract-counter.ir.json",
    "tests/fixtures/mcel_application_ir/contract-workbench.ir.json",
    "tests/fixtures/mcel_application_template_v1/contract-counter",
    "tests/test_mcel_application_browser_scenario_runner.py",
    "tests/test_mcel_application_runtime_collection.py",
    "tests/test_mcel_counter_compatibility.py",
    "tests/test_mcel_forward_specification_app.py",
    "tests/test_mcel_reference_app_wrapper_guardrails.py",
    "tests/test_mcel_workbench_portability.py",
)


def _safe_target(relative_path: str) -> Path:
    if not relative_path or relative_path.startswith("/") or ".." in relative_path.split("/"):
        raise ValueError(f"Unsafe deletion target: {relative_path!r}")
    target = (REPO_ROOT / relative_path).resolve()
    repo = REPO_ROOT.resolve()
    if target != repo and repo not in target.parents:
        raise ValueError(f"Deletion target escapes repository root: {relative_path!r}")
    return target


def _target_status(relative_path: str) -> dict[str, str | bool]:
    target = _safe_target(relative_path)
    kind = "missing"
    if target.is_dir():
        kind = "directory"
    elif target.is_file():
        kind = "file"
    return {"path": relative_path, "exists": target.exists(), "kind": kind}


def build_plan() -> dict[str, object]:
    targets = [_target_status(path) for path in FIXTURE_DELETION_TARGETS]
    return {
        "schema": "mcel.reference-fixture-retirement-plan.v1",
        "targetCount": len(targets),
        "existingTargetCount": sum(1 for item in targets if item["exists"]),
        "targets": targets,
    }


def apply_plan() -> dict[str, object]:
    removed: list[str] = []
    already_missing: list[str] = []
    for relative_path in FIXTURE_DELETION_TARGETS:
        target = _safe_target(relative_path)
        if target.is_dir():
            shutil.rmtree(target)
            removed.append(relative_path)
        elif target.is_file():
            target.unlink()
            removed.append(relative_path)
        else:
            already_missing.append(relative_path)
    return {
        "schema": "mcel.reference-fixture-retirement-result.v1",
        "removedCount": len(removed),
        "alreadyMissingCount": len(already_missing),
        "removed": removed,
        "alreadyMissing": already_missing,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Delete the retired fixture targets.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    payload = apply_plan() if args.apply else build_plan()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        mode = "apply" if args.apply else "dry-run"
        print(f"MCEL reference fixture retirement {mode}:")
        for item in payload.get("targets", []):
            print(f"  {item['kind']:9} {item['path']}")
        for path in payload.get("removed", []):
            print(f"  removed   {path}")
        for path in payload.get("alreadyMissing", []):
            print(f"  missing   {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
