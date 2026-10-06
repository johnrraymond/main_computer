#!/usr/bin/env python3
"""Read-only Mother remove-node preflight for target-node service-row contamination.

This second paranoia pass answers one narrow question before remove-node:

    Does live Coolify inventory contain any *other* live service row that
    advertises the target node identity?

The remove-node executor deletes the target's exact primary service UUID.  A
second row that still advertises the target node can survive that deletion and
later make topology detection report the removed node as unexpectedly live.
That is exactly the failure this preflight blocks.

The check intentionally mirrors the live-topology detector's service-hint and
"live" classification.  It performs GET-only Coolify inspection and never
patches, restarts, deploys, votes, deletes, or mutates Mother state.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping
import urllib.request


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.mother_helper_cleanup2_yagni import (
    MotherHelperCleanup2YagniError,
    _load_private_state,
)
from tools.mother_preflight_paranoia import (
    MotherPreflightParanoiaError,
    _discover_current_topology_evidence,
    _load_acknowledged_current_topology_for_preflight,
)
from tools.mother.common.coolify_state import (
    CoolifyObservationError,
    get_coolify_json,
    list_coolify_controllers,
)
from tools.mother.common.deployment_topology_rectification import (
    _service_hint_is_live,
    _service_hints_from_payload,
)


KIND = "main_computer.mother.preflight_paranoia2.v1"
BLOCK_CODE = "MOTHER_PREFLIGHT_PARANOIA2_TARGET_NODE_SERVICE_ROW_CONFLICT"


class MotherPreflightParanoia2Error(RuntimeError):
    """The read-only second paranoia pass could not produce a trustworthy result."""


def _target_service(services: list[Mapping[str, Any]], node: str) -> dict[str, str]:
    node_name = str(node or "").strip()
    if not node_name:
        raise MotherPreflightParanoia2Error("node is required")

    matches = [
        item
        for item in services
        if str(item.get("node") or "").strip() == node_name
    ]
    if len(matches) != 1:
        if not matches:
            raise MotherPreflightParanoia2Error(
                f"target node {node_name!r} is not present in the current topology"
            )
        raise MotherPreflightParanoia2Error(
            f"target node {node_name!r} appears more than once in the current topology"
        )

    record = matches[0]
    controller_id = str(record.get("controller_id") or "").strip()
    service_uuid = str(record.get("service_uuid") or "").strip()
    if not controller_id or not service_uuid:
        raise MotherPreflightParanoia2Error(
            f"target node {node_name!r} has an incomplete primary service record"
        )
    return {
        "node": node_name,
        "controller_id": controller_id,
        "service_uuid": service_uuid,
    }


def evaluate_target_node_service_rows(
    *,
    target: Mapping[str, str],
    expected_nodes: list[str],
    inventory_hints: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Evaluate inventory hints without performing I/O."""

    target_node = str(target.get("node") or "").strip()
    target_uuid = str(target.get("service_uuid") or "").strip()
    if not target_node or not target_uuid:
        raise MotherPreflightParanoia2Error("target service record is incomplete")

    expected = {str(item).strip() for item in expected_nodes if str(item).strip()}
    if target_node not in expected:
        raise MotherPreflightParanoia2Error("target node is not part of the expected current topology")

    target_rows: list[dict[str, Any]] = []
    unexpected_node_rows: list[dict[str, Any]] = []
    conflicting_rows: list[dict[str, Any]] = []

    for raw_hint in inventory_hints:
        hint = dict(raw_hint)
        node_hints = [str(item).strip() for item in (hint.get("node_hints") or []) if str(item).strip()]
        live = _service_hint_is_live(hint)
        is_primary_target = str(hint.get("uuid") or "") == target_uuid
        unexpected_hints = sorted({item for item in node_hints if item not in expected})
        advertises_target = target_node in node_hints

        # Topology authority is intentionally narrower than broad node hints.
        # Helper/service metadata may mention a Mother node without being that
        # node's primary service row.  Only an exact live service-name match can
        # survive as a primary node after the target row is deleted.
        service_name = str(hint.get("name") or "").strip()
        exact_primary_node = service_name if service_name in node_hints else None
        topology_authoritative_primary = bool(live and exact_primary_node)
        post_remove_target_poison = bool(
            topology_authoritative_primary
            and exact_primary_node == target_node
            and not is_primary_target
        )
        current_topology_poison = bool(
            topology_authoritative_primary
            and exact_primary_node not in expected
        )

        if not advertises_target and not unexpected_hints and not current_topology_poison:
            continue

        hint["live_by_topology_detector"] = live
        hint["is_primary_target_service"] = is_primary_target
        hint["exact_primary_node"] = exact_primary_node
        hint["topology_authoritative_primary"] = topology_authoritative_primary
        hint["unexpected_node_hints"] = unexpected_hints
        hint["would_survive_primary_target_deletion"] = not is_primary_target
        hint["would_poison_current_topology"] = current_topology_poison
        hint["would_poison_post_remove_topology"] = post_remove_target_poison
        reasons: list[str] = []
        if current_topology_poison:
            reasons.append("already-advertises-node-outside-current-topology")
        if post_remove_target_poison:
            reasons.append("would-advertise-target-after-primary-service-deletion")
        hint["conflict_reasons"] = reasons

        if advertises_target:
            target_rows.append(hint)
        if current_topology_poison:
            unexpected_node_rows.append(hint)
        if reasons:
            conflicting_rows.append(hint)

    clean = not conflicting_rows
    return {
        "clean": clean,
        "status": "pass" if clean else "target-node-service-row-conflict",
        "failure": (
            None
            if clean
            else {
                "code": BLOCK_CODE,
                "message": (
                    f"{len(conflicting_rows)} live Coolify service row(s) can contaminate topology "
                    f"before or after removal of {target_node!r}; clean those rows before mutation"
                ),
            }
        ),
        "target_rows": target_rows,
        "unexpected_node_rows": unexpected_node_rows,
        "conflicting_rows": conflicting_rows,
    }


def run_preflight_paranoia2(
    *,
    runtime_state_root: str | Path,
    network: str,
    node: str,
    topology_evidence: str | Path | None = None,
    acknowledged_topology_evidence_sha256: str | None = None,
    timeout: float = 30.0,
    max_response_bytes: int = 12 * 1024 * 1024,
    opener: Any = urllib.request.urlopen,
) -> dict[str, Any]:
    network_name = str(network or "").strip()
    node_name = str(node or "").strip()
    if not network_name or not node_name:
        raise MotherPreflightParanoia2Error("network and node are required")
    if timeout <= 0 or max_response_bytes <= 0:
        raise MotherPreflightParanoia2Error("timeout/response arguments are invalid")

    discovered_topology = topology_evidence is None
    try:
        selected_topology = (
            _discover_current_topology_evidence(runtime_state_root, network=network_name)
            if discovered_topology
            else topology_evidence
        )
        topology = _load_acknowledged_current_topology_for_preflight(
            runtime_state_root,
            network=network_name,
            topology_evidence=selected_topology,
            acknowledged_sha256=acknowledged_topology_evidence_sha256,
        )
    except MotherPreflightParanoiaError as exc:
        raise MotherPreflightParanoia2Error(str(exc)) from exc

    if topology is None:
        raise MotherPreflightParanoia2Error(
            "topology evidence is not passed, clean, complete current-topology evidence"
        )

    target = _target_service(topology["services"], node_name)

    try:
        private_state = _load_private_state(
            runtime_state_root,
            network=network_name,
            mode="inspect",
        )
    except MotherHelperCleanup2YagniError as exc:
        raise MotherPreflightParanoia2Error(f"{exc.code}: {exc}") from exc

    try:
        controllers = [
            item
            for item in list_coolify_controllers(private_state)
            if item.network == network_name and item.enabled
        ]
    except CoolifyObservationError as exc:
        raise MotherPreflightParanoia2Error(f"{exc.code}: {exc}") from exc
    if not controllers:
        raise MotherPreflightParanoia2Error(
            f"no enabled Coolify controllers are configured for {network_name!r}"
        )

    inventory_hints: list[dict[str, Any]] = []
    inventory_observations: list[dict[str, Any]] = []
    for controller in controllers:
        try:
            observation = get_coolify_json(
                controller,
                "/api/v1/services",
                authenticated=True,
                timeout=float(timeout),
                max_response_bytes=int(max_response_bytes),
                opener=opener,
            )
        except CoolifyObservationError as exc:
            raise MotherPreflightParanoia2Error(
                f"{exc.code}: failed reading Coolify service inventory from "
                f"{controller.controller_id}: {exc}"
            ) from exc

        hints = _service_hints_from_payload(observation.payload)
        inventory_observations.append(
            {
                "controller_id": controller.controller_id,
                "http_status": observation.status,
                "response_sha256": observation.response_sha256,
                "byte_length": observation.byte_length,
                "service_hint_count": len(hints),
            }
        )
        for hint in hints:
            inventory_hints.append(
                {
                    "controller_id": controller.controller_id,
                    **dict(hint),
                }
            )

    decision = evaluate_target_node_service_rows(
        target=target,
        expected_nodes=[str(item) for item in topology["nodes"]],
        inventory_hints=inventory_hints,
    )

    return {
        "kind": KIND,
        "schema_version": 1,
        "status": decision["status"],
        "operation": "remove-node",
        "network": network_name,
        "target": target,
        "read_only": True,
        "failure": decision["failure"],
        "topology_evidence": {
            "path": str(topology["path"]),
            "sha256": str(topology["sha256"]),
            "discovered_from_disk": discovered_topology,
        },
        "inventory_observations": inventory_observations,
        "target_node_service_rows": decision["target_rows"],
        "already_unexpected_node_service_rows": decision["unexpected_node_rows"],
        "conflicting_service_rows": decision["conflicting_rows"],
        "policy": {
            "allowed_http_methods": ["GET"],
            "coolify_control_plane_only": True,
            "network_mutation_performed": False,
            "service_deletion_performed": False,
            "validator_mutation_performed": False,
            "routing_or_topology_mutation_performed": False,
        },
        "summary": {
            "clean": decision["clean"],
            "target_node": target["node"],
            "target_controller_id": target["controller_id"],
            "target_service_uuid": target["service_uuid"],
            "controller_inventory_count": len(inventory_observations),
            "target_node_service_row_count": len(decision["target_rows"]),
            "already_unexpected_node_service_row_count": len(decision["unexpected_node_rows"]),
            "conflicting_service_row_count": len(decision["conflicting_rows"]),
            "current_topology_poison_present": bool(decision["unexpected_node_rows"]),
            "post_remove_topology_poison_risk": any(
                item.get("would_poison_post_remove_topology") is True
                for item in decision["conflicting_rows"]
            ),
            "network_mutation_performed": False,
        },
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only Mother preflight paranoia2 for remove-node. Blocks when a live "
            "non-primary Coolify service row still advertises the target node identity "
            "and could poison post-remove topology detection."
        )
    )
    parser.add_argument("--runtime-state-root", required=True)
    parser.add_argument("--network", default="mainnet")
    parser.add_argument("--node", required=True)
    parser.add_argument("--topology-evidence")
    parser.add_argument("--acknowledge-topology-evidence-sha256")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-response-bytes", type=int, default=12 * 1024 * 1024)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = run_preflight_paranoia2(
            runtime_state_root=args.runtime_state_root,
            network=args.network,
            node=args.node,
            topology_evidence=args.topology_evidence,
            acknowledged_topology_evidence_sha256=args.acknowledge_topology_evidence_sha256,
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
        )
    except MotherPreflightParanoia2Error as exc:
        print(f"MOTHER_PREFLIGHT_PARANOIA2_FAILED: {exc}", file=sys.stderr)
        return 1

    if result["status"] == "pass":
        print(
            "MOTHER_PREFLIGHT_PARANOIA2_CLEAN: "
            f"no live non-primary Coolify service rows advertise {result['target']['node']!r}."
        )
    else:
        failure = result.get("failure") or {}
        print(f"{failure.get('code')}: {failure.get('message')}")

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "pass" else 3


if __name__ == "__main__":
    raise SystemExit(main())
