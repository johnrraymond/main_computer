#!/usr/bin/env python3
"""Regenerate the Website Builder MCEL page runtime bundle."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from main_computer.mcel_runtime_package import DEFAULT_MCEL_RUNTIME_OUTPUT, package_mcel_runtime


def main() -> int:
    parser = argparse.ArgumentParser(description="Build deploy/local-platform/site-runtimes/mcel-runtime.js")
    parser.add_argument(
        "--repo-root",
        default=REPO_ROOT,
        type=Path,
        help="Repository root. Defaults to the parent of tools/.",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_MCEL_RUNTIME_OUTPUT,
        type=Path,
        help="Repo-relative output path for the generated runtime bundle.",
    )
    args = parser.parse_args()

    result = package_mcel_runtime(args.repo_root, args.output)
    print(f"wrote {result.output_path.relative_to(args.repo_root)} ({result.size_bytes} bytes)")
    print(f"version {result.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
