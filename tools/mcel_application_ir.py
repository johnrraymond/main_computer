#!/usr/bin/env python3
"""Validate and deterministically normalize a candidate MCEL Application IR document."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main_computer.mcel_application_ir import (  # noqa: E402
    VALIDATION_REPORT_SCHEMA,
    canonical_json_bytes,
    validate_application_ir,
)

INVALID_IR_EXIT = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Candidate mcel.application-ir.v1 JSON file.")
    parser.add_argument("--write-normalized", type=Path, help="Write canonical normalized IR after validation.")
    parser.add_argument("--json", action="store_true", help="Emit the full machine-readable validation report.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result = {
            "schema": VALIDATION_REPORT_SCHEMA,
            "valid": False,
            "appId": "unknown-app",
            "diagnosticCount": 1,
            "diagnostics": [],
            "error": str(exc),
        }
        print(json.dumps(result, indent=2, sort_keys=True) if args.json else f"status: fail\nerror: {exc}")
        return INVALID_IR_EXIT

    report = validate_application_ir(payload)
    if report.valid and report.normalized is not None and args.write_normalized:
        args.write_normalized.parent.mkdir(parents=True, exist_ok=True)
        args.write_normalized.write_bytes(canonical_json_bytes(report.normalized) + b"\n")

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True, ensure_ascii=False))
    else:
        print("mcel-application-ir-v1")
        print(f"input: {args.input.as_posix()}")
        print(f"app: {report.app_id}")
        print(f"status: {'pass' if report.valid else 'fail'}")
        print(f"diagnostics: {len(report.diagnostics)}")
        if report.semantic_fingerprint:
            print(f"semantic_fingerprint: {report.semantic_fingerprint}")
        if report.source_binding_fingerprint:
            print(f"source_binding_fingerprint: {report.source_binding_fingerprint}")
        if args.write_normalized and report.valid:
            print(f"normalized: {args.write_normalized.as_posix()}")
        for diagnostic in report.diagnostics:
            print(f"{diagnostic.code}: {diagnostic.semantic_path}: {diagnostic.summary}")
    return 0 if report.valid else INVALID_IR_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
