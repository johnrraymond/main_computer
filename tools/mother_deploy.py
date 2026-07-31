#!/usr/bin/env python3
"""Build secret-safe, read-only Mother deployment plans from committed identity."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.mother.common.deployment_plan import (
    MotherDeploymentPlanError,
    build_starter_deployment_plan,
)
from tools.mother.common.errors import MotherError, exit_code_for
from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import read_private_state


DEFAULT_RUNTIME_STATE_ROOT = Path("runtime/state")


def _operation(network: str, operation_id: str | None) -> OperationIdentity:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return OperationIdentity(
        operation_id=operation_id or f"mother-deploy-plan-{network}-{stamp}",
        request_id="mother-deploy-cli-plan",
        network=network,
        operation_kind="MOTHER-OP-PLAN",
    )


def _selected_nodes(raw_values: list[str]) -> tuple[str, ...]:
    selected: list[str] = []
    for raw in raw_values:
        selected.extend(item.strip() for item in raw.split(",") if item.strip())
    return tuple(selected)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser(
        "plan",
        help="build the starter add-node sequence without network access or mutation",
        allow_abbrev=False,
    )
    plan.add_argument("--network", default="mainnet")
    plan.add_argument(
        "--node",
        action="append",
        default=[],
        help="optional target name; repeat or provide comma-separated names",
    )
    plan.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    plan.add_argument("--operation-id")
    plan.add_argument(
        "--require-ready",
        action="store_true",
        help="return status 1 when the plan reports any execution blocker",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        operation = _operation(args.network, args.operation_id)
        paths = MotherPaths(
            runtime_state_root=Path(args.runtime_state_root)
        ).resolve_private_state_paths()
        private_state = read_private_state(paths, operation=operation)
        plan = build_starter_deployment_plan(
            private_state,
            network=args.network,
            selected_nodes=_selected_nodes(args.node),
        )
        print(json.dumps(plan, indent=2, sort_keys=True))
        if args.require_ready and not plan["summary"]["ready_for_execution"]:
            return 1
        return 0
    except MotherDeploymentPlanError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2
    except MotherError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return exit_code_for(exc)
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
