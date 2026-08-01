#!/usr/bin/env python3
"""Generate or check browser-safe MCEL application runtime projections."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main_computer.mcel_application_runtime_projection import (  # noqa: E402
    RUNTIME_PROJECTION_RESULT_SCHEMA,
    RuntimeProjectionError,
    StaleRuntimeProjection,
    check_runtime_projections,
    write_runtime_projections,
)
from main_computer.mcel_application_packages import repository_root  # noqa: E402


def _payload(*, root: Path, output: Path, projection_set, mode: str, changed: bool, ok: bool, code: str) -> dict:
    return {
        "schema": RUNTIME_PROJECTION_RESULT_SCHEMA,
        "ok": ok,
        "resultCode": code,
        "mode": mode,
        "repositoryRoot": root.as_posix(),
        "output": output.as_posix(),
        "packageCount": projection_set.package_count,
        "catalogFingerprint": projection_set.catalog_fingerprint,
        "changed": changed,
    }


def _render(payload: dict) -> str:
    return "\n".join([
        payload["schema"],
        f"mode: {payload['mode']}",
        f"output: {payload['output']}",
        f"packages: {payload['packageCount']}",
        f"catalog_fingerprint: {payload['catalogFingerprint']}",
        f"changed: {str(payload['changed']).lower()}",
        f"status: {'pass' if payload['ok'] else 'fail'}",
    ]) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=repository_root())
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    root = args.repo_root.resolve()

    try:
        if args.check:
            fresh, output, projection_set = check_runtime_projections(root, output_root=args.output)
            if not fresh:
                raise StaleRuntimeProjection("Browser-safe MCEL application runtime projections are stale.")
            payload = _payload(
                root=root,
                output=output.relative_to(root) if output.is_relative_to(root) else output,
                projection_set=projection_set,
                mode="check",
                changed=False,
                ok=True,
                code="runtime_projection_fresh",
            )
        else:
            output, projection_set, changed = write_runtime_projections(root, output_root=args.output)
            payload = _payload(
                root=root,
                output=output.relative_to(root) if output.is_relative_to(root) else output,
                projection_set=projection_set,
                mode="write",
                changed=changed,
                ok=True,
                code="runtime_projection_written" if changed else "runtime_projection_fresh",
            )
    except RuntimeProjectionError as exc:
        payload = {
            "schema": RUNTIME_PROJECTION_RESULT_SCHEMA,
            "ok": False,
            "resultCode": exc.result_code,
            "mode": "check" if args.check else "write",
            "repositoryRoot": root.as_posix(),
            "output": str(args.output or "main_computer/web/applications/mcel-packages"),
            "packageCount": 0,
            "catalogFingerprint": None,
            "changed": bool(args.check),
            "error": str(exc),
        }
        print(json.dumps(payload, indent=2, sort_keys=True) if args.json else _render(payload), end="")
        return exc.exit_code

    print(json.dumps(payload, indent=2, sort_keys=True) if args.json else _render(payload), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
