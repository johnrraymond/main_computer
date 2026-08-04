#!/usr/bin/env python3
"""Import the live explicit Contract Counter package into canonical MCEL IR."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main_computer.mcel_application_ir import canonical_json_bytes  # noqa: E402
from main_computer.mcel_counter_legacy_importer import (  # noqa: E402
    DEFAULT_COUNTER_ROOT,
    import_counter_legacy_package,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-root", type=Path, default=DEFAULT_COUNTER_ROOT)
    parser.add_argument("--write-ir", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = import_counter_legacy_package(args.package_root)

    if args.write_ir and report.normalized_ir is not None:
        args.write_ir.parent.mkdir(parents=True, exist_ok=True)
        args.write_ir.write_bytes(canonical_json_bytes(report.normalized_ir) + b"\n")

    if args.json:
        print(json.dumps(report.to_dict(include_ir=True), indent=2, sort_keys=True, ensure_ascii=False))
    else:
        print("mcel-counter-legacy-importer-wave3")
        print(f"package: {report.package_root}")
        print(f"app: {report.app_id}")
        print(f"status: {report.status}")
        print(f"diagnostics: {report.diagnostic_count}")
        if report.semantic_fingerprint:
            print(f"semantic_fingerprint: {report.semantic_fingerprint}")
        if report.source_binding_fingerprint:
            print(f"source_binding_fingerprint: {report.source_binding_fingerprint}")
        if args.write_ir and report.normalized_ir is not None:
            print(f"ir: {args.write_ir.as_posix()}")
        for item in report.diagnostics:
            print(f"{item.get('code')}: {item.get('semanticPath')}: {item.get('summary')}")
    return 0 if report.valid else 3


if __name__ == "__main__":
    raise SystemExit(main())
