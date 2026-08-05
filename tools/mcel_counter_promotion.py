#!/usr/bin/env python3
"""Execute or roll back the transactional Counter DSL authority transition."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main_computer.mcel_counter_promotion import (  # noqa: E402
    DEFAULT_REPORT_ROOT,
    DEFAULT_TRANSACTION_ROOT,
    REPOSITORY_ROOT,
    execute_counter_promotion,
    rollback_counter_promotion,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--execute", action="store_true", help="Execute the rehearsed live authority transition.")
    action.add_argument("--rollback", metavar="TRANSACTION", help="Roll back a committed transaction id or 'latest'.")
    parser.add_argument("--repo-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--transaction-root", type=Path, default=DEFAULT_TRANSACTION_ROOT)
    parser.add_argument("--report-root", type=Path, default=DEFAULT_REPORT_ROOT)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--no-write-report", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.execute:
        result = execute_counter_promotion(
            repo_root=args.repo_root,
            transaction_root=args.transaction_root,
            report_root=args.report_root,
            headed=args.headed,
            write_report=not args.no_write_report,
        )
    else:
        result = rollback_counter_promotion(
            args.rollback,
            repo_root=args.repo_root,
            transaction_root=args.transaction_root,
            report_root=args.report_root,
            write_report=not args.no_write_report,
        )

    payload = result.to_dict()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("mcel-counter-promotion-wave7")
        print("app: contract-counter")
        print(f"status: {payload.get('status')}")
        print(f"diagnostics: {result.diagnostic_count}")
        print(f"transaction: {payload.get('transactionId')}")
        if args.execute:
            print(f"promotion_executed: {str(payload.get('promotionExecuted')).lower()}")
            print(f"source_authority: {payload.get('sourceAuthority')}")
            print(f"derived_artifact_authority: {payload.get('derivedArtifactAuthority')}")
            print(f"legacy_package_authority: {payload.get('legacyPackageAuthority')}")
            print(f"truth_status: {payload.get('truthStatus')}")
            print(f"semantic_fingerprint: {payload.get('semanticFingerprint')}")
            print(f"rollback_available: {str(payload.get('rollbackAvailable')).lower()}")
        else:
            print(f"rollback_executed: {str(payload.get('rollbackExecuted')).lower()}")
            print(f"restoration: {payload.get('restoration')}")
            print(f"source_authority: {payload.get('sourceAuthority')}")
        if result.output_directory:
            try:
                display = result.output_directory.resolve().relative_to(args.repo_root.resolve()).as_posix()
            except ValueError:
                display = str(result.output_directory.resolve())
            print(f"reports: {display}")
        for item in result.diagnostics:
            print(f"  {item.get('code')}: {item.get('summary')}")
    return 0 if result.valid else 2


if __name__ == "__main__":
    raise SystemExit(main())
