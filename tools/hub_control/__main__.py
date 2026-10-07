#!/usr/bin/env python3
"""Internal Hub Control CLI.

The normal operator surface is ``hub_mutate_harness.py``. This module exposes
stage-oriented JSON commands only for that harness and recovery/testing.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .add_hub import do as add_do
from .add_hub import finalize as add_finalize
from .add_hub import inspect_operation as add_inspect_operation
from .add_hub import prep as add_prep
from .common.errors import HubControlError
from .common.models import HubContext
from .inspect import inspect_network
from .remove_hub import do as remove_do
from .remove_hub import finalize as remove_finalize
from .remove_hub import inspect_operation as remove_inspect_operation
from .remove_hub import prep as remove_prep
from .common.state import require_operation


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Internal Main Computer Hub Control")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    inspect = sub.add_parser("inspect")
    inspect.add_argument("network")
    inspect.add_argument("--operation-id", default="")

    for kind in ("add-hub", "remove-hub"):
        op = sub.add_parser(kind)
        stages = op.add_subparsers(dest="stage", required=True)
        prep = stages.add_parser("prep")
        prep.add_argument("network")
        prep.add_argument("--hub", required=True)
        if kind == "remove-hub":
            prep.add_argument("--allow-full-deletion", action="store_true")
        for stage in ("do", "finalize"):
            cmd = stages.add_parser(stage)
            cmd.add_argument("network")
            cmd.add_argument("--operation-id", required=True)
            if kind == "add-hub" and stage == "do":
                cmd.add_argument(
                    "--force-git",
                    action=argparse.BooleanOptionalAction,
                    default=True,
                    help="block deployment when relevant Hub runtime files have uncommitted local changes",
                )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    ctx = HubContext.from_repo(Path(args.repo_root).resolve())
    try:
        if args.command == "inspect":
            operation_id = str(args.operation_id or "")
            if operation_id:
                operation = require_operation(ctx, args.network, operation_id)
                if operation.get("kind") == "add-hub":
                    result = add_inspect_operation(ctx, args.network, operation_id)
                elif operation.get("kind") == "remove-hub":
                    result = remove_inspect_operation(ctx, args.network, operation_id)
                else:
                    raise HubControlError("HUB_OPERATION_KIND_MISMATCH", f"unsupported Hub operation kind: {operation.get('kind')!r}")
            else:
                result = inspect_network(ctx, args.network)
        elif args.command == "add-hub":
            if args.stage == "prep":
                result = add_prep(ctx, args.network, args.hub)
            elif args.stage == "do":
                result = add_do(
                    ctx,
                    args.network,
                    args.operation_id,
                    force_git=bool(args.force_git),
                )
            elif args.stage == "finalize":
                result = add_finalize(ctx, args.network, args.operation_id)
            else:
                raise ValueError(f"unsupported add-hub stage: {args.stage!r}")
        elif args.command == "remove-hub":
            if args.stage == "prep":
                result = remove_prep(
                    ctx,
                    args.network,
                    args.hub,
                    allow_full_deletion=bool(args.allow_full_deletion),
                )
            elif args.stage == "do":
                result = remove_do(ctx, args.network, args.operation_id)
            elif args.stage == "finalize":
                result = remove_finalize(ctx, args.network, args.operation_id)
            else:
                raise ValueError(f"unsupported remove-hub stage: {args.stage!r}")
        else:
            raise ValueError(f"unsupported Hub command: {args.command!r}")
        payload = {"ok": True, "result": result}
        print(json.dumps(payload, sort_keys=True) if args.json else json.dumps(payload, indent=2, sort_keys=True))
        return 0
    except HubControlError as exc:
        payload = {"ok": False, "error": {"code": exc.code, "message": exc.message}}
        print(json.dumps(payload, sort_keys=True) if args.json else json.dumps(payload, indent=2, sort_keys=True))
        return 2
    except Exception as exc:  # noqa: BLE001
        payload = {"ok": False, "error": {"code": "HUB_CONTROL_ERROR", "message": str(exc)}}
        print(json.dumps(payload, sort_keys=True) if args.json else json.dumps(payload, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
