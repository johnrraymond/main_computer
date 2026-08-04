#!/usr/bin/env python3
"""Compile one official mcel.dsl.v1 source into a validated candidate Application IR."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main_computer.mcel_dsl_compiler import (  # noqa: E402
    DEFAULT_CANDIDATE_ROOT,
    compile_dsl_application,
)

INVALID_DSL_EXIT = 3
SEMANTIC_CONFLICT_EXIT = 4


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Official vanilla-JavaScript application source.")
    parser.add_argument("--compare-ir", type=Path, help="Existing IR authority to compare by exact semantic meaning.")
    parser.add_argument("--write-candidate", action="store_true", help="Stage normalized IR under compiler-candidates.")
    parser.add_argument("--candidate-root", type=Path, default=DEFAULT_CANDIDATE_ROOT)
    parser.add_argument("--timeout-ms", type=int, default=1000)
    parser.add_argument("--json", action="store_true", help="Emit the full machine-readable compiler report.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = compile_dsl_application(
        args.input,
        compare_ir_path=args.compare_ir,
        write_candidate=args.write_candidate,
        candidate_root=args.candidate_root,
        timeout_ms=args.timeout_ms,
    )
    if args.json:
        print(json.dumps(report.to_dict(include_ir=True), indent=2, sort_keys=True, ensure_ascii=False))
    else:
        print("mcel-dsl-compiler-wave2b")
        print(f"source: {report.source}")
        print(f"app: {report.app_id}")
        print(f"status: {report.status}")
        print(f"diagnostics: {report.diagnostic_count}")
        if report.semantic_fingerprint:
            print(f"semantic_fingerprint: {report.semantic_fingerprint}")
        if report.source_binding_fingerprint:
            print(f"source_binding_fingerprint: {report.source_binding_fingerprint}")
        if report.comparison_status:
            print(f"comparison: {report.comparison_status}")
        if report.candidate_ir_path:
            print(f"candidate_ir: {report.to_dict()['candidate']['ir']}")
        for diagnostic in report.diagnostics:
            print(f"{diagnostic.get('code')}: {diagnostic.get('semanticPath')}: {diagnostic.get('summary')}")
    if report.valid:
        return 0
    if report.status == "semantic-conflict":
        return SEMANTIC_CONFLICT_EXIT
    return INVALID_DSL_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
