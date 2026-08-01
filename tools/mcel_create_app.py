#!/usr/bin/env python3
"""Create a deterministic MCEL application scaffold."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from main_computer.mcel_scaffolding import (  # noqa: E402
    DEFAULT_TEMPLATE_VERSION,
    McelScaffoldingError,
    generate_application,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create the canonical deterministic MCEL application package."
    )
    parser.add_argument("app_id", help="Lowercase application id, for example contract-counter.")
    parser.add_argument("--title", help="Human-readable title. Defaults from the application id.")
    parser.add_argument(
        "--output-root",
        type=Path,
        help="Destination root. Defaults to the repository mcel_apps directory.",
    )
    parser.add_argument(
        "--template-version",
        default=DEFAULT_TEMPLATE_VERSION,
        help=f"Exact template version. Current value: {DEFAULT_TEMPLATE_VERSION}.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate and report without writing.")
    parser.add_argument("--json", action="store_true", help="Emit a machine-readable result on stdout.")
    return parser


def _human_success(result: dict[str, Any]) -> str:
    validation = result["validation"]
    heading = "Validated" if result["dry_run"] else "Created"
    lines = [
        f"{heading} MCEL application: {result['app_id']}",
        f"Title: {result['title']}",
        f"Template: {result['template']['id']} {result['template']['version']}",
        f"Destination: {result['destination']}",
        f"Files: {len(result['created_files'])}",
        f"Structural validation: {'pass' if validation['ok'] else 'fail'}",
        "Target integrations still missing:",
    ]
    lines.extend(f"  - {gap}" for gap in result["target_gaps"])
    return "\n".join(lines)


def _error_payload(error: McelScaffoldingError) -> dict[str, Any]:
    return {
        "schema": "mcel.create-app-result.v1",
        "ok": False,
        "result_code": error.result_code,
        "message": str(error),
        "details": error.details,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = generate_application(
            args.app_id,
            title=args.title,
            output_root=args.output_root,
            template_version=args.template_version,
            dry_run=args.dry_run,
        )
    except McelScaffoldingError as exc:
        payload = _error_payload(exc)
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"MCEL application generation failed: {exc}", file=sys.stderr)
        return exc.exit_code

    payload = result.to_dict()
    if args.json:
        print(_human_success(payload), file=sys.stderr)
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(_human_success(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
