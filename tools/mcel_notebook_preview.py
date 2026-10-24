#!/usr/bin/env python3
"""Build a standalone, read-only MCEL-generated tab notebook from official DSL.

No Main Computer application routes, system endpoints or installed packages change.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from main_computer.mcel_notebook_projection import NotebookProjectionRefused, compile_notebook_preview

DEFAULT_SOURCE = ROOT / "mcel_experiments/task-manager-notebook/application.js"
DEFAULT_OUTPUT = ROOT / "runtime/reports/mcel-experiments/task-manager-notebook/generated-notebook.html"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--fixture", type=Path, help="Optional explicit sample data; never live machine telemetry")
    parser.add_argument("--check", action="store_true", help="Check generation but do not write a preview")
    args = parser.parse_args(argv)
    try:
        html = compile_notebook_preview(args.input, fixture_path=args.fixture)
    except (NotebookProjectionRefused, OSError, ValueError) as exc:
        print(f"MCEL_NOTEBOOK_PREVIEW_REFUSED: {exc}", file=sys.stderr)
        return 4
    if not args.check:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(html, encoding="utf-8")
    print(f"MCEL_NOTEBOOK_PREVIEW_PASS: {'checked' if args.check else args.output} bytes={len(html.encode('utf-8'))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
