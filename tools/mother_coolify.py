#!/usr/bin/env python3
"""Observe Coolify through committed Mother private state using GET requests only."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.mother.common.coolify_state import (
    CoolifyObservationError,
    list_coolify_controllers,
    observe_health,
    observe_inventory,
    resolve_coolify_controller,
    safe_controller_summary,
    write_coolify_evidence,
)
from tools.mother.common.errors import MotherError, exit_code_for
from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import read_private_state


DEFAULT_RUNTIME_STATE_ROOT = Path("runtime/state")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _operation(command: str, network: str, operation_id: str | None = None) -> OperationIdentity:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return OperationIdentity(
        operation_id=operation_id or f"mother-coolify-{command}-{stamp}",
        request_id=f"mother-coolify-cli-{command}",
        network=network,
        operation_kind="MOTHER-OP-DIAGNOSE",
    )


def _load(args: argparse.Namespace, command: str, network: str):
    operation = _operation(command, network, getattr(args, "operation_id", None))
    paths = MotherPaths(runtime_state_root=Path(args.runtime_state_root)).resolve_private_state_paths()
    private_state = read_private_state(paths, operation=operation)
    return operation, paths, private_state


def _print(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _result_summary(evidence: dict[str, Any], evidence_path: Path) -> dict[str, Any]:
    return {
        "command": evidence["command"],
        "complete": evidence["summary"]["complete"],
        "counts": evidence["summary"]["counts"],
        "evidence_path": str(evidence_path),
        "failed_endpoints": evidence["summary"]["failed_endpoints"],
        "mother_generation": evidence["mother_binding"]["generation"],
        "policy": evidence["policy"],
        "target": evidence["target"],
    }


def _cmd_controllers(args: argparse.Namespace) -> int:
    _, _, private_state = _load(args, "controllers", "local")
    controllers = list_coolify_controllers(private_state)
    _print({
        "controllers": [safe_controller_summary(item) for item in controllers],
        "mother_generation": private_state.binding.generation,
    })
    return 0


def _selected(args: argparse.Namespace, command: str, *, require_token: bool):
    operation, paths, private_state = _load(args, command, args.network)
    controller = resolve_coolify_controller(
        private_state,
        args.network,
        args.controller,
        require_enabled=True,
        require_token=require_token,
    )
    return operation, paths, private_state, controller


def _cmd_health(args: argparse.Namespace) -> int:
    operation, paths, private_state, controller = _selected(args, "health", require_token=False)
    evidence = observe_health(
        controller,
        private_state,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        created_at=_utc_now(),
    )
    destination = write_coolify_evidence(paths, evidence, operation=operation)
    _print(_result_summary(evidence, destination))
    return 0 if evidence["summary"]["complete"] else 1


def _cmd_inventory(args: argparse.Namespace) -> int:
    operation, paths, private_state, controller = _selected(args, "inventory", require_token=True)
    evidence = observe_inventory(
        controller,
        private_state,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        max_items=args.max_items,
        created_at=_utc_now(),
    )
    destination = write_coolify_evidence(paths, evidence, operation=operation)
    _print(_result_summary(evidence, destination))
    return 0 if evidence["summary"]["complete"] else 1


def _cmd_observe_all(args: argparse.Namespace) -> int:
    operation, paths, private_state = _load(args, "observe-all", "local")
    results: list[dict[str, Any]] = []
    all_complete = True
    for controller in list_coolify_controllers(private_state):
        if not controller.enabled:
            results.append({
                "controller_id": controller.controller_id,
                "network": controller.network,
                "skipped": "disabled",
            })
            continue
        if not controller.api_token.strip():
            results.append({
                "controller_id": controller.controller_id,
                "network": controller.network,
                "skipped": "missing-api-token",
            })
            all_complete = False
            continue
        evidence = observe_inventory(
            controller,
            private_state,
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
            max_items=args.max_items,
            created_at=_utc_now(),
        )
        destination = write_coolify_evidence(paths, evidence, operation=operation)
        summary = _result_summary(evidence, destination)
        results.append(summary)
        all_complete = bool(summary["complete"]) and all_complete
    _print({
        "complete": all_complete,
        "mother_generation": private_state.binding.generation,
        "results": results,
    })
    return 0 if all_complete else 1


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    parser.add_argument("--operation-id")


def _network_common(parser: argparse.ArgumentParser) -> None:
    _common(parser)
    parser.add_argument("--network", required=True)
    parser.add_argument("--controller", required=True)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    controllers = subparsers.add_parser("controllers", help="list Mother-bound Coolify controllers without secrets")
    _common(controllers)
    controllers.set_defaults(handler=_cmd_controllers)

    health = subparsers.add_parser("health", help="perform the unauthenticated Coolify health GET")
    _network_common(health)
    health.set_defaults(handler=_cmd_health)

    inventory = subparsers.add_parser("inventory", help="collect the predefined authenticated GET-only inventory")
    _network_common(inventory)
    inventory.add_argument("--max-items", type=int, default=1000)
    inventory.set_defaults(handler=_cmd_inventory)

    observe_all = subparsers.add_parser("observe-all", help="inventory every enabled Mother-bound controller")
    _common(observe_all)
    observe_all.add_argument("--timeout", type=float, default=30.0)
    observe_all.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    observe_all.add_argument("--max-items", type=int, default=1000)
    observe_all.set_defaults(handler=_cmd_observe_all)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except CoolifyObservationError as exc:
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
