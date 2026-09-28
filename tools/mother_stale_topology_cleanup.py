#!/usr/bin/env python3
"""Delete one exact stale Mother topology helper service after proving it is safe.

This recovery tool is intentionally narrow.  It deletes only a preserved
bootnode-precleanup static-nodes writer whose Coolify service row advertises a
node that is outside the acknowledged current Mother topology.  Such a row can
make live-topology detection falsely resurrect a removed node.

The caller must acknowledge the exact controller, service UUID, and unexpected
node hint.  No primary Mother node service is eligible for this cleanup.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any, Mapping
import urllib.parse
import urllib.request


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.mother_bootnode_precleanup import _delete_writer_service
from tools.mother_helper_cleanup2_yagni import MotherHelperCleanup2YagniError, _load_private_state
from tools.mother_preflight_paranoia import (
    MotherPreflightParanoiaError,
    _load_acknowledged_current_topology_for_preflight,
)
from tools.mother.common.coolify_state import (
    CoolifyObservationError,
    get_coolify_json,
    resolve_coolify_controller,
)
from tools.mother.common.deployment_topology_rectification import (
    _service_hint_is_live,
    _service_hints_from_payload,
)


KIND = "main_computer.mother.stale_topology_cleanup.v1"
WRITER_PREFIX = "mother-static-node-writer-"
WRITER_DESCRIPTION = "Ephemeral Mother bootnode precleanup static-nodes writer"


class MotherStaleTopologyCleanupError(RuntimeError):
    """The exact stale helper could not be proved safe for deletion."""


def _single_hint(payload: Any, *, service_uuid: str) -> dict[str, Any]:
    matches = [
        dict(item)
        for item in _service_hints_from_payload(payload)
        if str(item.get("uuid") or "") == service_uuid
    ]
    if len(matches) != 1:
        raise MotherStaleTopologyCleanupError(
            "Coolify did not return exactly one service row for the acknowledged UUID"
        )
    return matches[0]


def _validate_stale_writer(
    *,
    hint: Mapping[str, Any],
    expected_nodes: set[str],
    acknowledged_unexpected_nodes: set[str],
) -> dict[str, Any]:
    name = str(hint.get("name") or "")
    description = str(hint.get("description") or "")
    node_hints = {
        str(item).strip()
        for item in (hint.get("node_hints") or [])
        if str(item).strip()
    }

    if not name.startswith(WRITER_PREFIX) or description != WRITER_DESCRIPTION:
        raise MotherStaleTopologyCleanupError(
            "acknowledged service is not a bootnode-precleanup static-nodes writer"
        )
    if not _service_hint_is_live(hint):
        raise MotherStaleTopologyCleanupError(
            "acknowledged stale helper is already terminal; no live-row cleanup is required"
        )
    if not acknowledged_unexpected_nodes:
        raise MotherStaleTopologyCleanupError("at least one --unexpected-node acknowledgement is required")
    if not acknowledged_unexpected_nodes.issubset(node_hints):
        missing = sorted(acknowledged_unexpected_nodes - node_hints)
        raise MotherStaleTopologyCleanupError(
            "acknowledged unexpected node hint(s) are not advertised by this service: "
            + ", ".join(missing)
        )
    overlap = sorted(node_hints & expected_nodes)
    if overlap:
        raise MotherStaleTopologyCleanupError(
            "refusing cleanup because the helper advertises current-topology node(s): "
            + ", ".join(overlap)
        )
    unacknowledged = sorted(node_hints - acknowledged_unexpected_nodes)
    if unacknowledged:
        raise MotherStaleTopologyCleanupError(
            "refusing cleanup because the helper advertises unacknowledged node hint(s): "
            + ", ".join(unacknowledged)
        )

    return {
        "name": name,
        "description": description,
        "status": hint.get("status"),
        "node_hints": sorted(node_hints),
    }


def run_cleanup(
    *,
    runtime_state_root: str | Path,
    network: str,
    topology_evidence: str | Path,
    acknowledged_topology_evidence_sha256: str,
    controller_id: str,
    service_uuid: str,
    unexpected_nodes: list[str],
    execute: bool,
    allow_mutation: bool,
    timeout: float = 30.0,
    max_response_bytes: int = 12 * 1024 * 1024,
    max_wait_seconds: float = 60.0,
    poll_interval_seconds: float = 2.0,
    opener: Any = urllib.request.urlopen,
    sleeper: Any = time.sleep,
) -> dict[str, Any]:
    network_name = str(network or "").strip()
    controller_name = str(controller_id or "").strip()
    service = str(service_uuid or "").strip()
    unexpected = {str(item).strip() for item in unexpected_nodes if str(item).strip()}
    if not network_name or not controller_name or not service:
        raise MotherStaleTopologyCleanupError("network, controller-id, and service-uuid are required")
    if execute != allow_mutation:
        raise MotherStaleTopologyCleanupError(
            "live cleanup requires both --execute and --allow-mutation; omit both for inspect-only"
        )
    if timeout <= 0 or max_response_bytes <= 0 or max_wait_seconds < 0 or poll_interval_seconds < 0:
        raise MotherStaleTopologyCleanupError("timeout/response/wait arguments are invalid")

    try:
        topology = _load_acknowledged_current_topology_for_preflight(
            runtime_state_root,
            network=network_name,
            topology_evidence=topology_evidence,
            acknowledged_sha256=acknowledged_topology_evidence_sha256,
        )
    except MotherPreflightParanoiaError as exc:
        raise MotherStaleTopologyCleanupError(str(exc)) from exc
    if topology is None:
        raise MotherStaleTopologyCleanupError(
            "topology evidence is not passed, clean, complete current-topology evidence"
        )

    expected_nodes = {str(item).strip() for item in topology["nodes"] if str(item).strip()}
    if unexpected & expected_nodes:
        raise MotherStaleTopologyCleanupError(
            "--unexpected-node cannot name a node in the acknowledged current topology"
        )

    try:
        private_state = _load_private_state(runtime_state_root, network=network_name, mode="inspect")
        controller = resolve_coolify_controller(
            private_state,
            network_name,
            controller_name,
            require_enabled=True,
            require_token=True,
        )
    except (MotherHelperCleanup2YagniError, CoolifyObservationError) as exc:
        code = getattr(exc, "code", type(exc).__name__)
        raise MotherStaleTopologyCleanupError(f"{code}: {exc}") from exc

    endpoint = f"/api/v1/services/{urllib.parse.quote(service, safe='')}"
    try:
        before = get_coolify_json(
            controller,
            endpoint,
            authenticated=True,
            timeout=float(timeout),
            max_response_bytes=int(max_response_bytes),
            opener=opener,
        )
    except CoolifyObservationError as exc:
        raise MotherStaleTopologyCleanupError(f"{exc.code}: {exc}") from exc

    if before.status == 404:
        return {
            "kind": KIND,
            "schema_version": 1,
            "status": "pass",
            "network": network_name,
            "controller_id": controller_name,
            "service_uuid": service,
            "already_absent": True,
            "live_mutation_performed": False,
            "topology_evidence": {"path": str(topology["path"]), "sha256": str(topology["sha256"])},
            "summary": {"clean": True, "service_absent": True, "live_mutation_performed": False},
        }
    if not before.ok:
        raise MotherStaleTopologyCleanupError(
            f"Coolify service lookup failed with HTTP {before.status}"
        )

    hint = _single_hint(before.payload, service_uuid=service)
    verified = _validate_stale_writer(
        hint=hint,
        expected_nodes=expected_nodes,
        acknowledged_unexpected_nodes=unexpected,
    )

    if not execute:
        return {
            "kind": KIND,
            "schema_version": 1,
            "status": "ready",
            "network": network_name,
            "controller_id": controller_name,
            "service_uuid": service,
            "stale_service": verified,
            "live_mutation_performed": False,
            "topology_evidence": {"path": str(topology["path"]), "sha256": str(topology["sha256"])},
            "summary": {"clean": True, "service_absent": False, "live_mutation_performed": False},
        }

    deletion = _delete_writer_service(
        controller=controller,
        controller_id=controller_name,
        service_uuid=service,
        service_name=verified["name"],
        timeout=float(timeout),
        max_response_bytes=int(max_response_bytes),
        opener=opener,
    )
    if deletion.get("status") not in {200, 202, 204, 404}:
        raise MotherStaleTopologyCleanupError(
            f"Coolify rejected stale helper deletion with HTTP {deletion.get('status')}"
        )

    observations: list[dict[str, Any]] = []
    deadline = time.monotonic() + float(max_wait_seconds)
    while True:
        try:
            after = get_coolify_json(
                controller,
                endpoint,
                authenticated=True,
                timeout=float(timeout),
                max_response_bytes=int(max_response_bytes),
                opener=opener,
            )
        except CoolifyObservationError as exc:
            raise MotherStaleTopologyCleanupError(f"{exc.code}: {exc}") from exc
        observations.append(
            {
                "status": after.status,
                "response_sha256": after.response_sha256,
                "byte_length": after.byte_length,
                "elapsed_ms": after.elapsed_ms,
            }
        )
        if after.status == 404:
            break
        if not after.ok:
            raise MotherStaleTopologyCleanupError(
                f"Coolify absence verification failed with HTTP {after.status}"
            )
        if time.monotonic() >= deadline:
            raise MotherStaleTopologyCleanupError(
                "stale helper remained visible after the cleanup wait window"
            )
        if poll_interval_seconds:
            sleeper(float(poll_interval_seconds))

    return {
        "kind": KIND,
        "schema_version": 1,
        "status": "pass",
        "network": network_name,
        "controller_id": controller_name,
        "service_uuid": service,
        "stale_service": verified,
        "deletion": deletion,
        "absence_observations": observations,
        "already_absent": False,
        "live_mutation_performed": True,
        "topology_evidence": {"path": str(topology["path"]), "sha256": str(topology["sha256"])},
        "policy": {
            "allowed_http_methods": ["GET", "DELETE"],
            "service_deletion_performed": True,
            "validator_mutation_performed": False,
            "routing_or_topology_mutation_performed": False,
        },
        "summary": {"clean": True, "service_absent": True, "live_mutation_performed": True},
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect or delete one exact stale bootnode-precleanup writer that advertises "
            "a node outside acknowledged current Mother topology."
        )
    )
    parser.add_argument("--runtime-state-root", required=True)
    parser.add_argument("--network", default="mainnet")
    parser.add_argument("--topology-evidence", required=True)
    parser.add_argument("--acknowledge-topology-evidence-sha256", required=True)
    parser.add_argument("--controller-id", required=True)
    parser.add_argument("--service-uuid", required=True)
    parser.add_argument("--unexpected-node", action="append", default=[], required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--allow-mutation", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-response-bytes", type=int, default=12 * 1024 * 1024)
    parser.add_argument("--max-wait-seconds", type=float, default=60.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=2.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = run_cleanup(
            runtime_state_root=args.runtime_state_root,
            network=args.network,
            topology_evidence=args.topology_evidence,
            acknowledged_topology_evidence_sha256=args.acknowledge_topology_evidence_sha256,
            controller_id=args.controller_id,
            service_uuid=args.service_uuid,
            unexpected_nodes=args.unexpected_node,
            execute=args.execute,
            allow_mutation=args.allow_mutation,
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
            max_wait_seconds=args.max_wait_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
        )
    except MotherStaleTopologyCleanupError as exc:
        print(f"MOTHER_STALE_TOPOLOGY_CLEANUP_FAILED: {exc}", file=sys.stderr)
        return 1

    if result["status"] == "pass":
        if result.get("already_absent"):
            print("MOTHER_STALE_TOPOLOGY_CLEANUP_CLEAN: acknowledged stale helper is already absent.")
        else:
            print("MOTHER_STALE_TOPOLOGY_CLEANUP_CLEAN: stale helper deleted and absence verified.")
    else:
        print("MOTHER_STALE_TOPOLOGY_CLEANUP_READY: exact stale helper is safe for cleanup.")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
