#!/usr/bin/env python3
"""Inspect the deterministic repository catalog of MCEL application packages."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from main_computer.mcel_application_packages import (  # noqa: E402
    CATALOG_FORMAT,
    build_application_package_catalog,
)


INVALID_CATALOG_EXIT = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Discover and validate repository-local MCEL application packages."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="Repository root containing mcel_apps/. Defaults to the current checkout.",
    )
    parser.add_argument("--json", action="store_true", help="Emit only the canonical JSON catalog.")
    return parser


def _human_report(payload: dict[str, object]) -> str:
    lines = [
        CATALOG_FORMAT,
        f"repository: {payload['repositoryRoot']}",
        f"packages_root: {payload['packagesRoot']}",
        f"packages: {payload['packageCount']}",
        f"valid: {payload['validCount']}",
        f"invalid: {payload['invalidCount']}",
        f"catalog_fingerprint: {payload['fingerprint']}",
    ]

    for package in payload["packages"]:  # type: ignore[index]
        app_id = package.get("appId") or package.get("directoryName")
        lines.extend(
            [
                "",
                str(app_id),
                f"  package: {'valid' if package['valid'] else 'invalid'}",
                f"  root: {package['packageRoot']}",
                f"  current conformance: {package.get('conformance', {}).get('currentMode', '')}",
                f"  target conformance: {package.get('conformance', {}).get('targetMode', '')}",
                f"  fingerprint: {package.get('fingerprint') or 'unavailable'}",
            ]
        )
        for issue in package.get("errors", []):
            suffix = f" [{issue.get('path')}]" if issue.get("path") else ""
            lines.append(f"  error: {issue.get('code')}: {issue.get('message')}{suffix}")

    for issue in payload["errors"]:  # type: ignore[index]
        suffix = f" [{issue.get('path')}]" if issue.get("path") else ""
        lines.append(f"catalog error: {issue.get('code')}: {issue.get('message')}{suffix}")
    for issue in payload["warnings"]:  # type: ignore[index]
        suffix = f" [{issue.get('path')}]" if issue.get("path") else ""
        lines.append(f"catalog warning: {issue.get('code')}: {issue.get('message')}{suffix}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    catalog = build_application_package_catalog(args.repo_root)
    payload = catalog.to_dict()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        print(_human_report(payload))
    return 0 if catalog.ok else INVALID_CATALOG_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
