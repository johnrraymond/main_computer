#!/usr/bin/env python3
"""Ensure a Mother's public JSON-RPC Traefik route without RPC paranoia or canary funding.

This intentionally uses the established, proven Mother route-writer executor (which
checks eth_chainId through the backend and through Traefik) but has its own
operator-facing command, deployment lifecycle entry point, and failure boundary.
The helper is ephemeral; the Traefik dynamic configuration is persistent.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import re
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.mother.common.models import OperationIdentity
from tools.mother.common.errors import MotherError
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import read_private_state
from tools.mother.common.deployment_validator_rpc_canary_funding import (
    MotherDeploymentValidatorRpcCanaryFundingError,
    _network_state,
    _shared_rpc_route_host,
    _target_controller_id,
    execute_shared_rpc_route_rewire,
)


class RpcRouteEnsureError(RuntimeError):
    pass


def route_plan(private_state: Any, *, network: str, node: str, controller_id: str) -> dict[str, Any]:
    """Resolve a single, explicitly specified controller-local backend; no network I/O."""
    if network != "mainnet":
        raise RpcRouteEnsureError(f"unsupported network {network!r}; only mainnet owns this shared RPC route")
    if controller_id not in {"coolify-a", "coolify-c"}:
        raise RpcRouteEnsureError(f"unsupported controller {controller_id!r}")
    if not node or not isinstance(node, str):
        raise RpcRouteEnsureError("node is required")
    state = _network_state(private_state)
    if state.get("chain_id") != 42424240:
        raise RpcRouteEnsureError("mainnet chain ID does not match 42424240")
    if re.fullmatch(r"mainnet[ac]-super[1-9][0-9]*", node) is None:
        raise RpcRouteEnsureError(f"unsupported validator-RPC backend {node!r}")
    actual_controller = _target_controller_id(state, node)
    if actual_controller != controller_id:
        raise RpcRouteEnsureError(
            f"node {node!r} belongs to {actual_controller!r}, not {controller_id!r}"
        )
    # Only network validator names may serve as controller-local backends.
    route_host = _shared_rpc_route_host(state)
    return {
        "network": network,
        "controller_id": controller_id,
        "node": node,
        "host": route_host,
        "url": f"https://{route_host}",
        "backend_url": f"http://{node}:8545",
        "expected_chain_id": 42424240,
    }


def ensure_route(
    private_state: Any,
    *,
    network: str,
    node: str,
    controller_id: str,
    execute: bool,
    timeout: float = 30.0,
    max_response_bytes: int = 4 * 1024 * 1024,
    max_wait_seconds: float = 300.0,
    poll_interval_seconds: float = 5.0,
    **kwargs: Any,
) -> dict[str, Any]:
    plan = route_plan(private_state, network=network, node=node, controller_id=controller_id)
    if not execute:
        return {"status": "pass", "clean": True, "ensured": False, "mode": "plan-only", "route": plan}
    result = execute_shared_rpc_route_rewire(
        private_state,
        controller_id=controller_id,
        target_node=node,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        max_wait_seconds=max_wait_seconds,
        poll_interval_seconds=poll_interval_seconds,
        **kwargs,
    )
    if not (
        result.get("status") == "pass"
        and (result.get("proof") or {}).get("healthy") is True
        and (result.get("cleanup") or {}).get("deleted") is True
    ):
        raise RpcRouteEnsureError("route writer did not verify healthy routing and helper cleanup")
    return {
        "status": "pass",
        "clean": True,
        "ensured": True,
        "mode": "traefik-route-ensure",
        "route": plan,
        "proof": dict(result["proof"]),
        "writer_cleanup": dict(result["cleanup"]),
        "service_uuid": result.get("service_uuid"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", default="mainnet")
    parser.add_argument("--runtime-state-root", default=str(ROOT / "runtime" / "state"))
    parser.add_argument("--node", required=True)
    parser.add_argument("--controller-id", required=True, choices=["coolify-a", "coolify-c"])
    parser.add_argument("--execute", action="store_true", help="install/repair the persistent Traefik route")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    parser.add_argument("--max-wait-seconds", type=float, default=300.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=5.0)
    args = parser.parse_args(argv)
    try:
        if args.timeout <= 0 or args.max_wait_seconds <= 0 or args.poll_interval_seconds < 0 or args.max_response_bytes <= 0:
            raise RpcRouteEnsureError("timeouts and response limits must be positive")
        operation = OperationIdentity(
            operation_id=f"mother-rpc-route-ensure-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            request_id="mother-rpc-route-ensure",
            network=args.network,
            operation_kind="MOTHER-OP-RPC-PROPAGATE",
        )
        paths = MotherPaths(runtime_state_root=Path(args.runtime_state_root)).resolve_private_state_paths()
        private_state = read_private_state(paths, operation=operation)
        result = ensure_route(
            private_state,
            network=args.network,
            node=args.node,
            controller_id=args.controller_id,
            execute=args.execute,
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
            max_wait_seconds=args.max_wait_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
        )
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0
    except (RpcRouteEnsureError, MotherDeploymentValidatorRpcCanaryFundingError, MotherError, RuntimeError, ValueError) as exc:
        # The operator needs the actual failure; do not make the harness mistake
        # an unhealthy/unreachable route for a successful no-op.
        print(json.dumps({"status": "failed", "clean": False, "ensured": False, "error": str(exc)}, sort_keys=True, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
