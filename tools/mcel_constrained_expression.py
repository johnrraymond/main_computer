#!/usr/bin/env python3
"""Analyze constrained expression graphs embedded in an MCEL Application IR document."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main_computer.mcel_constrained_expression import (  # noqa: E402
    EXPRESSION_REPORT_SCHEMA,
    analyze_application_expressions,
)
from main_computer.mcel_application_ir import validate_application_ir  # noqa: E402

INVALID_EXPRESSION_EXIT = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Candidate mcel.application-ir.v1 JSON file.")
    parser.add_argument("--json", action="store_true", help="Emit the full machine-readable expression report.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result = {
            "schema": EXPRESSION_REPORT_SCHEMA,
            "valid": False,
            "appId": "unknown-app",
            "expressionCount": 0,
            "diagnosticCount": 1,
            "expressions": [],
            "error": str(exc),
        }
        print(json.dumps(result, indent=2, sort_keys=True) if args.json else f"status: fail\nerror: {exc}")
        return INVALID_EXPRESSION_EXIT

    ir_report = validate_application_ir(payload)
    report = analyze_application_expressions(payload, emit_reference_diagnostics=True)
    valid = ir_report.valid and report.valid
    result = report.to_dict()
    result["valid"] = valid
    result["irValid"] = ir_report.valid
    result["irDiagnosticCount"] = len(ir_report.diagnostics)

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        print("mcel-constrained-expression-wave2a")
        print(f"input: {args.input.as_posix()}")
        print(f"app: {report.app_id}")
        print(f"status: {'pass' if valid else 'fail'}")
        print(f"expressions: {report.expression_count}")
        print(f"expression_diagnostics: {len(report.diagnostics)}")
        print(f"ir_diagnostics: {len(ir_report.diagnostics)}")
        for path, diagnostic in report.diagnostics:
            print(f"{diagnostic.code}: {path}: {diagnostic.summary}")
        for diagnostic in ir_report.diagnostics:
            print(f"{diagnostic.code}: {diagnostic.semantic_path}: {diagnostic.summary}")
    return 0 if valid else INVALID_EXPRESSION_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
