#!/usr/bin/env python3
"""Rehearse Counter DSL promotion and rollback without changing the live repository."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from main_computer.mcel_counter_promotion_rehearsal import (  # noqa: E402
    DEFAULT_REPORT_ROOT, REPOSITORY_ROOT, rehearse_counter_promotion,
)

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--report-root", type=Path, default=DEFAULT_REPORT_ROOT)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--no-write-report", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    result = rehearse_counter_promotion(
        repo_root=args.repo_root, report_root=args.report_root, headed=args.headed,
        write_report=not args.no_write_report,
    )
    payload = result.to_dict()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        authority = payload.get("authority") or {}
        print("mcel-counter-promotion-rehearsal-wave6")
        print("app: contract-counter")
        print(f"status: {payload.get('status')}")
        print(f"diagnostics: {result.diagnostic_count}")
        print(f"promotion_rehearsal: {payload.get('promotionRehearsal')}")
        print(f"post_promotion_truth_status: {payload.get('postPromotionTruthStatus')}")
        print(f"rollback_rehearsal: {payload.get('rollbackRehearsal')}")
        print(f"rollback_restoration: {payload.get('rollbackRestoration')}")
        print(f"live_repository_changed: {str(authority.get('liveApplicationChanged')).lower()}")
        print(f"promotion_executed: {str(authority.get('promotionExecuted')).lower()}")
        print(f"promotion_eligible: {str(payload.get('promotionEligible')).lower()}")
        if result.output_directory:
            try: display = result.output_directory.resolve().relative_to(args.repo_root.resolve()).as_posix()
            except ValueError: display = str(result.output_directory.resolve())
            print(f"reports: {display}")
        for item in result.diagnostics:
            print(f"  {item.get('code')}: {item.get('summary')}")
    if args.check and not result.valid:
        return 1
    return 0 if result.valid else 2

if __name__ == "__main__":
    raise SystemExit(main())
