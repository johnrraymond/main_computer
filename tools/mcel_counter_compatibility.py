#!/usr/bin/env python3
"""Compare live, fixture, and official-DSL Contract Counter semantics."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main_computer.mcel_counter_compatibility import (  # noqa: E402
    DEFAULT_COUNTER_ROOT,
    DEFAULT_DSL_SOURCE,
    DEFAULT_FIXTURE_IR,
    DEFAULT_REPORT_ROOT,
    compare_counter_representations,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-root", type=Path, default=DEFAULT_COUNTER_ROOT)
    parser.add_argument("--fixture-ir", type=Path, default=DEFAULT_FIXTURE_IR)
    parser.add_argument("--dsl-source", type=Path, default=DEFAULT_DSL_SOURCE)
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--report-root", type=Path, default=DEFAULT_REPORT_ROOT)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = compare_counter_representations(
        package_root=args.package_root,
        fixture_ir_path=args.fixture_ir,
        dsl_source_path=args.dsl_source,
        write_report=args.write_report,
        report_root=args.report_root,
    )
    data = report.to_dict()

    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        print("mcel-counter-compatibility-wave3")
        print("app: contract-counter")
        print(f"status: {report.status}")
        print(f"diagnostics: {report.diagnostic_count}")
        print(f"migration_state: {data['migrationState']}")
        print(f"live_authority: {data['liveAuthority']}")
        print(f"promotion_eligible: {str(data['promotionEligible']).lower()}")
        for name, value in data["semanticFingerprints"].items():
            print(f"{name}_semantic_fingerprint: {value}")
        print(f"source_hash_compatibility: {data['sourceHashCompatibility']['status']}")
        print(f"features: {len(data['features'])}")
        if report.json_path:
            print(f"json: {data['artifacts']['json']}")
        if report.markdown_path:
            print(f"markdown: {data['artifacts']['markdown']}")
        for item in report.diagnostics:
            print(f"{item.get('code')}: {item.get('semanticPath')}: {item.get('summary')}")
    return 0 if report.valid else 4


if __name__ == "__main__":
    raise SystemExit(main())
