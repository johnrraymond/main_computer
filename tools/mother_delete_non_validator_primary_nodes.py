#!/usr/bin/env python3
"""Explicit manual cleanup for live Coolify primary nodes that are not QBFT validators.

Safety boundary:

* resolve requested node names to exact live Coolify service rows;
* independently read the live QBFT validator set immediately before mutation;
* require every live validator address to map to a known Mother node identity;
* refuse to delete any requested node that is a live QBFT validator;
* preflight every requested deletion before performing the first mutation;
* reuse Mother's existing single-service node-removal primitive;
* never vote, reseal topology, or write Mother evidence.

This is an operator-triggered recovery tool.  Mutation requires both --execute and
--yes-i-know-this-deletes-live-node-services.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.mother.common.coolify_state import (  # noqa: E402
    get_coolify_json,
    list_coolify_controllers,
)
from tools.mother.common.deployment_node_remove import (  # noqa: E402
    MotherDeploymentNodeRemoveError,
    acknowledgement_for,
    execute_node_removal,
)
from tools.mother.common.models import OperationIdentity  # noqa: E402
from tools.mother.common.paths import MotherPaths  # noqa: E402
from tools.mother.common.private_state import read_private_state  # noqa: E402
from tools.mother_build_reseal_input import network_document  # noqa: E402
from tools.mother_detect_topology_v2 import (  # noqa: E402
    is_terminal_status,
    primary_items,
    record_exact_service_nodes,
    summarize_record,
)
from tools.mother_preflight_paranoia_validator_set import (  # noqa: E402
    _normalize_validator_set,
    _rpc,
    _shared_rpc_route_url,
    _validator_node_map,
)


KIND = "main_computer.mother.delete_non_validator_primary_nodes.v1"
_NODE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class DeleteNonValidatorPrimaryNodesError(RuntimeError):
    pass


def _operation(network: str) -> OperationIdentity:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return OperationIdentity(
        operation_id=f"mother-delete-non-validator-primary-nodes-{stamp}",
        request_id=f"mother-delete-non-validator-primary-nodes-{stamp}-request",
        network=network,
        operation_kind="MOTHER-OP-REMOVE-NODE",
    )


def _normalize_node(value: Any) -> str:
    node = str(value or "").strip().lower().replace("_", "-")
    if _NODE_RE.fullmatch(node) is None:
        raise DeleteNonValidatorPrimaryNodesError(f"invalid --node value: {value!r}")
    return node


def _service_inventory(
    private_state: Any,
    *,
    network: str,
    timeout: float,
    max_response_bytes: int,
) -> dict[str, list[dict[str, str]]]:
    controllers = [
        controller
        for controller in list_coolify_controllers(private_state)
        if controller.network == network and controller.enabled
    ]
    if not controllers:
        raise DeleteNonValidatorPrimaryNodesError(
            f"no enabled Coolify controllers for network {network!r}"
        )

    rows: dict[str, list[dict[str, str]]] = {}
    for controller in controllers:
        observation = get_coolify_json(
            controller,
            "/api/v1/services",
            authenticated=True,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
        )
        if not observation.ok:
            raise DeleteNonValidatorPrimaryNodesError(
                f"Coolify service inventory failed for {controller.controller_id}: HTTP {observation.status}"
            )
        for item in primary_items(observation.payload):
            if not isinstance(item, Mapping) or is_terminal_status(item):
                continue
            exact_nodes = record_exact_service_nodes(item, network)
            if not exact_nodes:
                continue
            summary = summarize_record(
                item,
                controller_id=controller.controller_id,
                endpoint_label="services",
                network=network,
            )
            raw_name = summary.get("name")
            raw_uuid = summary.get("uuid")
            if not isinstance(raw_name, str) or not isinstance(raw_uuid, str):
                continue
            exact_name = raw_name.strip().lower().replace("_", "-")
            if exact_name not in exact_nodes:
                continue
            rows.setdefault(exact_name, []).append(
                {
                    "node": exact_name,
                    "controller_id": controller.controller_id,
                    "service_uuid": raw_uuid.strip(),
                    "status": str(summary.get("status") or ""),
                }
            )
    return rows


def _resolve_targets(
    requested_nodes: list[str],
    inventory: Mapping[str, list[dict[str, str]]],
) -> list[dict[str, str]]:
    targets: list[dict[str, str]] = []
    for node in requested_nodes:
        rows = list(inventory.get(node) or [])
        unique = {
            (row.get("controller_id"), row.get("service_uuid"), row.get("node")): row
            for row in rows
        }
        rows = list(unique.values())
        if not rows:
            raise DeleteNonValidatorPrimaryNodesError(
                f"requested node {node!r} has no exact live Coolify primary service row"
            )
        if len(rows) != 1:
            detail = ", ".join(
                f"{row.get('controller_id')}:{row.get('service_uuid')}" for row in rows
            )
            raise DeleteNonValidatorPrimaryNodesError(
                f"requested node {node!r} is ambiguous across live Coolify service rows: {detail}"
            )
        targets.append(rows[0])
    return targets


def run(args: argparse.Namespace) -> dict[str, Any]:
    if not args.node:
        raise DeleteNonValidatorPrimaryNodesError("at least one --node is required")
    requested_nodes = [_normalize_node(node) for node in args.node]
    if len(set(requested_nodes)) != len(requested_nodes):
        raise DeleteNonValidatorPrimaryNodesError("duplicate --node values are not allowed")
    if args.execute != args.yes_i_know_this_deletes_live_node_services:
        raise DeleteNonValidatorPrimaryNodesError(
            "live deletion requires both --execute and --yes-i-know-this-deletes-live-node-services"
        )

    runtime_state_root = Path(args.runtime_state_root).resolve(strict=False)
    private_paths = MotherPaths(runtime_state_root=runtime_state_root).resolve_private_state_paths()
    private_state = read_private_state(private_paths, operation=_operation(args.network))
    network_doc = network_document(private_state, args.network)
    expected_chain_id = network_doc.get("chain_id")
    if not isinstance(expected_chain_id, int) or expected_chain_id <= 0:
        raise DeleteNonValidatorPrimaryNodesError("Mother private state does not contain a valid chain_id")

    rpc_url = args.rpc_url or _shared_rpc_route_url(network_doc)
    chain_raw = _rpc(
        rpc_url,
        "eth_chainId",
        [],
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
    )
    try:
        live_chain_id = int(str(chain_raw), 16)
    except (TypeError, ValueError) as exc:
        raise DeleteNonValidatorPrimaryNodesError(
            f"live eth_chainId result is invalid: {chain_raw!r}"
        ) from exc
    if live_chain_id != expected_chain_id:
        raise DeleteNonValidatorPrimaryNodesError(
            f"live chain_id {live_chain_id} does not match Mother chain_id {expected_chain_id}"
        )

    live_validators = _normalize_validator_set(
        _rpc(
            rpc_url,
            "qbft_getValidatorsByBlockNumber",
            ["latest"],
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
        ),
        "live QBFT validator set",
    )
    validator_to_node = _validator_node_map(network_doc, network=args.network)
    unmapped = [address for address in live_validators if address not in validator_to_node]
    if unmapped:
        raise DeleteNonValidatorPrimaryNodesError(
            "live QBFT contains validator identities that do not map to known Mother nodes; "
            "refusing service deletion: " + ", ".join(unmapped)
        )
    live_validator_nodes = [validator_to_node[address] for address in live_validators]
    forbidden = sorted(set(requested_nodes) & set(live_validator_nodes))
    if forbidden:
        raise DeleteNonValidatorPrimaryNodesError(
            "refusing to delete live QBFT validator node service(s): " + ", ".join(forbidden)
        )

    inventory = _service_inventory(
        private_state,
        network=args.network,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
    )
    targets = _resolve_targets(requested_nodes, inventory)

    plan = {
        "kind": KIND,
        "schema_version": 1,
        "status": "planned" if not args.execute else "executing",
        "network": args.network,
        "rpc_url": rpc_url,
        "chain_id": live_chain_id,
        "live_validator_set": live_validators,
        "live_validator_nodes": live_validator_nodes,
        "requested_nodes": requested_nodes,
        "targets": targets,
        "validator_vote_performed": False,
        "evidence_mutation_performed": False,
        "planned_mutation_count": len(targets),
    }
    if not args.execute:
        return {**plan, "status": "inspection", "live_mutation_performed": False}

    operation = _operation(args.network)
    results: list[dict[str, Any]] = []
    for target in targets:
        node = target["node"]
        service_uuid = target["service_uuid"]
        try:
            result = execute_node_removal(
                private_state,
                network=args.network,
                controller_id=target["controller_id"],
                node=node,
                service_uuid=service_uuid,
                acknowledged_node_removal=acknowledgement_for(node, service_uuid),
                allow_missing=False,
                timeout=args.timeout,
                max_wait_seconds=args.max_wait_seconds,
                poll_interval_seconds=args.poll_interval_seconds,
                max_response_bytes=args.max_response_bytes,
                operation=operation,
            )
        except MotherDeploymentNodeRemoveError as exc:
            raise DeleteNonValidatorPrimaryNodesError(
                f"{exc.code}: deletion failed for {node}: {exc}"
            ) from exc
        results.append(result)

    return {
        **plan,
        "status": "pass",
        "clean": True,
        "live_mutation_performed": bool(results),
        "mutation_count": sum(int(item.get("mutation_count") or 0) for item in results),
        "results": results,
        "next_step": "rerun mother_preflight_paranoia_validator_set.py",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manually delete exact live Coolify primary node services only after proving they are not live QBFT validators.",
        allow_abbrev=False,
    )
    parser.add_argument("--runtime-state-root", default=str(Path("runtime") / "state"))
    parser.add_argument("--network", default="mainnet", choices=["mainnet"])
    parser.add_argument("--node", action="append", help="exact primary node service name; repeat for multiple nodes")
    parser.add_argument("--rpc-url", help="override the Mother shared RPC URL; primarily for diagnostics/tests")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-wait-seconds", type=float, default=60.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=2.0)
    parser.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    parser.add_argument("--execute", action="store_true", help="perform the verified service deletion(s)")
    parser.add_argument(
        "--yes-i-know-this-deletes-live-node-services",
        action="store_true",
        help="required together with --execute",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "kind": KIND,
                    "status": "failed",
                    "error": str(exc),
                    "live_mutation_performed": False,
                    "evidence_mutation_performed": False,
                },
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") in {"pass", "inspection"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
