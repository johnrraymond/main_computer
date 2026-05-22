#!/usr/bin/env python3
"""
Debug-site-only golden website path smoke.

This is the safety smoke for the next Website Builder integration step.  It is
end-to-end, but deliberately narrow: AI website editing is allowed only for one
builder-managed debug-golden-path-* site at a time.

The smoke delegates the generated-editor/RAG/patch/new_patch/Git mechanics to
rag_golden_website_path_smoke.py, then adds a contract that must stay true before
this pathway can be exposed from the Website Builder app:

    selected site id is debug-golden-path-*
    selected path is runtime/websites/<site>
    selected path is not a hub/platform/install path
    new_patch.py target-root is the selected site
    Git top-level is the selected site, not the builder repo
    commit is created only after dry-run/apply validation
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_REPO_ROOT))
if str(SCRIPT_REPO_ROOT / "main_computer") not in sys.path:
    sys.path.insert(0, str(SCRIPT_REPO_ROOT / "main_computer"))

from main_computer.rag_debug_website_golden_path_smoke import ProgressReporter, norm_wsl
from main_computer.rag_golden_website_path_smoke import (
    DEBUG_GOLDEN_PREFIX,
    build_parser as build_golden_parser,
    evaluate as evaluate_golden_website_path,
    is_debug_golden_site_id,
)


MODE = "rag_debug_site_only_golden_path_smoke"
NAME = "debug_site_only_golden_path_e2e_rag_edit_zip_apply_git_commit"


def debug_site_only_contract(report: dict[str, Any]) -> dict[str, bool]:
    site_id = str(report.get("site_id") or "")
    target = report.get("target") if isinstance(report.get("target"), dict) else {}
    site_wsl_path = norm_wsl(str(report.get("site_wsl_path") or target.get("site_wsl_path") or ""))
    checks = report.get("checks") if isinstance(report.get("checks"), dict) else {}

    return {
        "debug_site_id_only": is_debug_golden_site_id(site_id),
        "site_path_is_runtime_website": f"/runtime/websites/{site_id}" in site_wsl_path,
        "target_not_hub_or_install": all(
            forbidden not in site_wsl_path
            for forbidden in ("/install/hub", "/hub-site", "/hub-local", "/directus")
        ),
        "site_git_top_level_is_selected_site": bool(checks.get("site_git_top_level_is_selected_site")),
        "new_patch_target_root_is_selected_site": bool(checks.get("new_patch_target_roots_are_selected_site")),
        "commit_is_debug_site_commit": bool(checks.get("committed_debug_site_true"))
        and bool(checks.get("committed_install_or_hub_repo_false")),
    }


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    explicit_site = str(getattr(args, "site", "") or "").strip().lower()
    if explicit_site and not is_debug_golden_site_id(explicit_site):
        raise ValueError(
            f"{MODE} only targets {DEBUG_GOLDEN_PREFIX}* sites; got {explicit_site!r}"
        )

    report = evaluate_golden_website_path(args)
    original_mode = report.get("mode")
    original_name = report.get("name")
    contract = debug_site_only_contract(report)

    checks = report.setdefault("checks", {})
    for key, value in contract.items():
        checks[f"debug_site_only_{key}"] = value

    failed_checks = [name for name, passed in checks.items() if not passed]
    report.update(
        {
            "name": NAME,
            "mode": MODE,
            "base_name": original_name,
            "base_mode": original_mode,
            "debug_site_only_contract": contract,
            "debug_site_only": True,
            "website_builder_app_ready_gate": "debug_sites_only",
            "failed_checks": failed_checks,
            "ok": bool(report.get("ok")) and all(contract.values()),
        }
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = build_golden_parser()
    parser.description = "Run the debug-site-only golden website RAG path smoke."
    parser.epilog = (
        "Safety gate: this smoke rejects hub/platform targets and only edits "
        "runtime/websites/debug-golden-path-* sites."
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.progress = ProgressReporter(
        enabled=not bool(args.quiet),
        interval_seconds=args.progress_interval_seconds,
    )
    try:
        report = evaluate(args)
    except (ValueError, OSError) as exc:
        report = {
            "name": NAME,
            "ok": False,
            "mode": MODE,
            "debug_site_only": True,
            "error": str(exc),
        }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
