#!/usr/bin/env python3
"""Generate or check the browser-safe MCEL application-package catalog."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from main_computer.mcel_application_package_browser_catalog import (  # noqa: E402
    BROWSER_CATALOG_RESULT_SCHEMA,
    BrowserCatalogError,
    InvalidRepositoryPackageCatalog,
    StaleBrowserPackageCatalog,
    build_repository_browser_catalog_payload,
    check_browser_catalog,
    default_browser_catalog_path,
    write_browser_catalog,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate the data-only browser catalog from validated MCEL application packages."
    )
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT, help="Repository root.")
    parser.add_argument("--output", type=Path, default=None, help="Override generated JavaScript path.")
    parser.add_argument("--check", action="store_true", help="Refuse a missing or stale generated catalog without writing.")
    parser.add_argument("--json", action="store_true", help="Emit a machine-readable result.")
    return parser


def _relative_or_string(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return str(path)


def _result(*, ok: bool, mode: str, root: Path, output: Path, payload: dict[str, object], changed: bool, code: str) -> dict[str, object]:
    return {
        "schema": BROWSER_CATALOG_RESULT_SCHEMA,
        "ok": ok,
        "resultCode": code,
        "mode": mode,
        "repositoryRoot": "." if root.resolve() == REPO_ROOT.resolve() else str(root),
        "output": _relative_or_string(output, root),
        "changed": changed,
        "packageCount": payload.get("packageCount", 0),
        "catalogFingerprint": payload.get("catalogFingerprint"),
        "catalogFingerprintAlgorithm": payload.get("catalogFingerprintAlgorithm"),
    }


def _human(data: dict[str, object]) -> str:
    return "\n".join(
        [
            str(data["schema"]),
            f"mode: {data['mode']}",
            f"output: {data['output']}",
            f"packages: {data['packageCount']}",
            f"catalog_fingerprint: {data['catalogFingerprint']}",
            f"changed: {str(data['changed']).lower()}",
            f"status: {'pass' if data['ok'] else 'fail'}",
        ]
    ) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.repo_root
    output = args.output or default_browser_catalog_path(root)
    try:
        if args.check:
            fresh, output, _expected = check_browser_catalog(root, output_path=output)
            payload = build_repository_browser_catalog_payload(root)
            if not fresh:
                raise StaleBrowserPackageCatalog(
                    "Generated browser application-package catalog is missing or stale."
                )
            data = _result(
                ok=True,
                mode="check",
                root=root,
                output=output,
                payload=payload,
                changed=False,
                code="browser_catalog_fresh",
            )
        else:
            output, payload, changed = write_browser_catalog(root, output_path=output)
            data = _result(
                ok=True,
                mode="write",
                root=root,
                output=output,
                payload=payload,
                changed=changed,
                code="browser_catalog_written" if changed else "browser_catalog_unchanged",
            )
    except BrowserCatalogError as exc:
        data = {
            "schema": BROWSER_CATALOG_RESULT_SCHEMA,
            "ok": False,
            "resultCode": exc.result_code,
            "mode": "check" if args.check else "write",
            "repositoryRoot": str(root),
            "output": _relative_or_string(output, root),
            "changed": False,
            "packageCount": 0,
            "catalogFingerprint": None,
            "catalogFingerprintAlgorithm": None,
            "error": str(exc),
        }
        if args.json:
            print(json.dumps(data, indent=2, sort_keys=True))
        else:
            print(_human(data), end="")
            print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code

    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print(_human(data), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
