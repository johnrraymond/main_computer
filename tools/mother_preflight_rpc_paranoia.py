#!/usr/bin/env python3
"""Read-only RPC continuity preflight for Mother remove-node operations.

The check answers one narrow question before a node is removed:

    Will removing this node remove the final Mother node from its controller?

If not, the controller still has a local node available for its RPC route and
this preflight passes.  If yes, but other Mother nodes survive on other
controllers, the operator must explicitly acknowledge that public DNS/upstream
RPC routing has already been adjusted so canonical RPC will still work after
this controller disappears from service.

This script never mutates DNS, Coolify, Traefik, validators, services, or
Mother state.  ``--rpc-will-work-post-remove`` is an operator assertion, not a
DNS verification.  A post-remove canonical RPC probe remains the proof.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.mother_helper_cleanup2_yagni import (
    MotherHelperCleanup2YagniError,
    _load_topology,
)


KIND = "main_computer.mother.preflight_rpc_paranoia.v1"


class MotherPreflightRpcParanoiaError(RuntimeError):
    """The read-only RPC preflight could not produce a trustworthy result."""


def _service_rows(topology: Mapping[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    raw = topology.get("services")
    if not isinstance(raw, list):
        raise MotherPreflightRpcParanoiaError("topology does not contain normalized service records")

    for item in raw:
        if not isinstance(item, Mapping):
            continue
        node = str(item.get("node") or "").strip()
        controller_id = str(item.get("controller_id") or "").strip()
        service_uuid = str(item.get("service_uuid") or "").strip()
        if not node or not controller_id or not service_uuid:
            raise MotherPreflightRpcParanoiaError(
                "topology contains an incomplete service record"
            )
        rows.append(
            {
                "node": node,
                "controller_id": controller_id,
                "service_uuid": service_uuid,
            }
        )

    if not rows:
        raise MotherPreflightRpcParanoiaError("topology contains no node services")
    return rows


def evaluate_rpc_remove_boundary(
    *,
    services: list[Mapping[str, str]],
    node: str,
    rpc_will_work_post_remove: bool,
) -> dict[str, Any]:
    """Evaluate the controller-removal boundary without performing I/O."""

    node_name = str(node or "").strip()
    if not node_name:
        raise MotherPreflightRpcParanoiaError("node is required")

    matches = [item for item in services if str(item.get("node") or "").strip() == node_name]
    if len(matches) != 1:
        if not matches:
            raise MotherPreflightRpcParanoiaError(
                f"target node {node_name!r} is not present in the current topology"
            )
        raise MotherPreflightRpcParanoiaError(
            f"target node {node_name!r} appears more than once in the current topology"
        )

    target = dict(matches[0])
    target_controller = str(target["controller_id"])
    survivors = [dict(item) for item in services if str(item.get("node") or "").strip() != node_name]
    same_controller_survivors = [
        item for item in survivors if str(item.get("controller_id") or "") == target_controller
    ]
    other_controller_survivors = [
        item for item in survivors if str(item.get("controller_id") or "") != target_controller
    ]

    controller_removed_from_topology = not same_controller_survivors
    no_rpc_survivors = not survivors
    acknowledgement_required = controller_removed_from_topology and not no_rpc_survivors
    acknowledged = bool(rpc_will_work_post_remove)

    if no_rpc_survivors:
        status = "no-rpc-survivors"
        clean = False
        code = "MOTHER_PREFLIGHT_RPC_PARANOIA_NO_RPC_SURVIVORS"
        message = (
            f"refusing RPC continuity approval for removal of {node_name!r}: "
            "no Mother node would remain after removal"
        )
    elif acknowledgement_required and not acknowledged:
        status = "rpc-post-remove-ack-required"
        clean = False
        code = "MOTHER_PREFLIGHT_RPC_PARANOIA_RPC_CONTINUITY_ACK_REQUIRED"
        message = (
            f"removing {node_name!r} removes the last Mother node from "
            f"{target_controller!r}; update DNS/upstream RPC routing, then acknowledge "
            "that canonical RPC will work post-remove"
        )
    else:
        status = "pass"
        clean = True
        code = None
        if controller_removed_from_topology:
            message = (
                f"removing {node_name!r} removes the last Mother node from "
                f"{target_controller!r}, but post-remove RPC continuity was explicitly acknowledged"
            )
        else:
            message = (
                f"removing {node_name!r} leaves {len(same_controller_survivors)} Mother node(s) "
                f"on {target_controller!r}"
            )

    return {
        "status": status,
        "clean": clean,
        "code": code,
        "message": message,
        "target": target,
        "survivors": survivors,
        "same_controller_survivors": same_controller_survivors,
        "other_controller_survivors": other_controller_survivors,
        "controller_removed_from_topology": controller_removed_from_topology,
        "rpc_will_work_post_remove_acknowledged": acknowledged,
        "acknowledgement_required": acknowledgement_required,
        "post_remove_rpc_probe_required": controller_removed_from_topology,
    }


def run_preflight_rpc_paranoia(
    *,
    runtime_state_root: str | Path,
    network: str,
    node: str,
    topology_evidence: str | Path | None = None,
    acknowledged_topology_evidence_sha256: str | None = None,
    rpc_will_work_post_remove: bool = False,
) -> dict[str, Any]:
    network_name = str(network or "").strip()
    if not network_name:
        raise MotherPreflightRpcParanoiaError("network is required")

    try:
        topology = _load_topology(
            runtime_state_root,
            network=network_name,
            topology_evidence=topology_evidence,
            acknowledged_sha256=acknowledged_topology_evidence_sha256,
        )
    except MotherHelperCleanup2YagniError as exc:
        raise MotherPreflightRpcParanoiaError(f"{exc.code}: {exc}") from exc

    services = _service_rows(topology)
    decision = evaluate_rpc_remove_boundary(
        services=services,
        node=node,
        rpc_will_work_post_remove=bool(rpc_will_work_post_remove),
    )

    return {
        "kind": KIND,
        "schema_version": 1,
        "status": decision["status"],
        "network": network_name,
        "operation": "remove-node",
        "read_only": True,
        "failure": (
            {
                "code": decision["code"],
                "message": decision["message"],
            }
            if decision["code"] is not None
            else None
        ),
        "message": decision["message"],
        "target": decision["target"],
        "survivors": decision["survivors"],
        "same_controller_survivors": decision["same_controller_survivors"],
        "other_controller_survivors": decision["other_controller_survivors"],
        "topology_evidence": {
            "path": str(topology["path"]),
            "sha256": str(topology["sha256"]),
            "discovered_from_disk": bool(topology.get("discovered")),
        },
        "rpc_continuity": {
            "controller_removed_from_topology": decision["controller_removed_from_topology"],
            "acknowledgement_required": decision["acknowledgement_required"],
            "rpc_will_work_post_remove_acknowledged": decision[
                "rpc_will_work_post_remove_acknowledged"
            ],
            "acknowledgement_source": (
                "explicit-operator-assertion"
                if decision["rpc_will_work_post_remove_acknowledged"]
                else None
            ),
            "dns_or_upstream_routing_verified_by_this_script": False,
            "post_remove_rpc_probe_required": decision["post_remove_rpc_probe_required"],
        },
        "policy": {
            "network_mutation_performed": False,
            "dns_mutation_performed": False,
            "coolify_mutation_performed": False,
            "traefik_mutation_performed": False,
            "service_deletion_performed": False,
        },
        "summary": {
            "clean": decision["clean"],
            "target_node": str(decision["target"]["node"]),
            "target_controller_id": str(decision["target"]["controller_id"]),
            "survivor_count": len(decision["survivors"]),
            "same_controller_survivor_count": len(decision["same_controller_survivors"]),
            "other_controller_survivor_count": len(decision["other_controller_survivors"]),
            "controller_removed_from_topology": decision["controller_removed_from_topology"],
            "rpc_will_work_post_remove_acknowledged": decision[
                "rpc_will_work_post_remove_acknowledged"
            ],
            "acknowledgement_required": decision["acknowledgement_required"],
            "post_remove_rpc_probe_required": decision["post_remove_rpc_probe_required"],
            "network_mutation_performed": False,
        },
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only Mother RPC preflight for remove-node. Blocks removal of the final node "
            "from a controller unless post-remove canonical RPC continuity is explicitly acknowledged."
        )
    )
    parser.add_argument("--runtime-state-root", required=True)
    parser.add_argument("--network", default="mainnet")
    parser.add_argument("--node", required=True)
    parser.add_argument("--topology-evidence")
    parser.add_argument("--acknowledge-topology-evidence-sha256")
    parser.add_argument(
        "--rpc-will-work-post-remove",
        action="store_true",
        help=(
            "Operator assertion that DNS/upstream routing has already been adjusted so canonical RPC "
            "will remain reachable after the target controller loses its final Mother node."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = run_preflight_rpc_paranoia(
            runtime_state_root=args.runtime_state_root,
            network=args.network,
            node=args.node,
            topology_evidence=args.topology_evidence,
            acknowledged_topology_evidence_sha256=args.acknowledge_topology_evidence_sha256,
            rpc_will_work_post_remove=args.rpc_will_work_post_remove,
        )
    except MotherPreflightRpcParanoiaError as exc:
        print(f"MOTHER_PREFLIGHT_RPC_PARANOIA_FAILED: {exc}", file=sys.stderr)
        return 1

    if result["status"] == "pass":
        if result["summary"]["controller_removed_from_topology"]:
            print(
                "MOTHER_PREFLIGHT_RPC_PARANOIA_CLEAN_ACKNOWLEDGED: "
                f"{result['summary']['target_node']} is the last Mother node on "
                f"{result['summary']['target_controller_id']}, and post-remove RPC continuity "
                "was explicitly acknowledged."
            )
        else:
            print(
                "MOTHER_PREFLIGHT_RPC_PARANOIA_CLEAN: "
                f"{result['summary']['same_controller_survivor_count']} same-controller survivor(s) "
                "remain after removal."
            )
    else:
        failure = result.get("failure") or {}
        print(f"{failure.get('code')}: {failure.get('message')}")

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "pass" else 3


if __name__ == "__main__":
    raise SystemExit(main())
