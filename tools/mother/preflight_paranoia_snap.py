#!/usr/bin/env python3
"""Read-only SNAP add-node foundation preflight.

This tool never mutates Coolify or Mother state.  It answers whether the
*current admitted network* is safe to use as the foundation for adding the
next node in SNAP mode.

Policy checked here:

* SNAP requires at least two admitted incumbent validators.
* Every admitted incumbent service must exist and be ``running:healthy``.
* Every incumbent Besu service must use a valid paired sync profile:
    FULL -> --sync-min-peers=0
    SNAP -> --sync-min-peers=2
* At the two-validator boundary, both incumbents must be FULL/0 before the
  third node is added as SNAP.  This preserves the two-FULL-node foundation.
* With three or more admitted validators, mixed FULL/0 and SNAP/2 profiles are
  accepted, provided all incumbent profiles and health checks are valid.

When a profile repair is obvious, the report includes the exact manual
``node_sync_mode_switch_smoke.py`` command to run.  This script does not run
that command itself.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping
import urllib.request

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.mother_preflight_paranoia import (
    MotherPreflightParanoiaError,
    _discover_current_topology_evidence,
    _load_acknowledged_current_topology_for_preflight,
    _service_status_from_payload,
)
from tools.mother_helper_cleanup2_yagni import (
    MotherHelperCleanup2YagniError,
    _compose_text_from_service_payload,
    _controller,
    _load_private_state,
    _load_topology,
    _service_detail,
)


KIND = "main_computer.mother.preflight_paranoia_snap.v1"
_SYNC_MODE_RE = re.compile(r"^--sync-mode=(FULL|SNAP)$", re.IGNORECASE)
_SYNC_MIN_PEERS_RE = re.compile(r"^--sync-min-peers=(\d+)$", re.IGNORECASE)
_VALID_PROFILE = {"FULL": 0, "SNAP": 2}


class SnapPreflightError(RuntimeError):
    """The read-only SNAP preflight could not establish trustworthy state."""


def _validator_set(topology: Mapping[str, Any], document: Mapping[str, Any]) -> list[str]:
    candidates = (
        topology.get("validator_set"),
        document.get("validator_set"),
        document.get("expected_validator_set"),
    )
    for value in candidates:
        if not isinstance(value, list):
            continue
        result: list[str] = []
        for item in value:
            text = str(item or "").strip().lower()
            if not re.fullmatch(r"0x[0-9a-f]{40}", text):
                raise SnapPreflightError("topology validator_set contains an invalid validator address")
            result.append(text)
        if len(result) != len(set(result)):
            raise SnapPreflightError("topology validator_set contains duplicate validator addresses")
        return result
    raise SnapPreflightError("topology evidence does not contain an explicit validator_set")


def _validator_count(topology: Mapping[str, Any], document: Mapping[str, Any]) -> int | None:
    for value in (
        topology.get("validator_count"),
        document.get("validator_count"),
    ):
        if type(value) is int and value >= 0:
            return value
    return None


def _sync_profile(compose_text: str, node: str) -> tuple[str, int]:
    try:
        compose = yaml.safe_load(compose_text)
    except yaml.YAMLError as exc:
        raise SnapPreflightError(f"{node}: Coolify Compose is not valid YAML: {exc}") from exc
    if not isinstance(compose, Mapping):
        raise SnapPreflightError(f"{node}: Coolify Compose is not an object")
    services = compose.get("services")
    if not isinstance(services, Mapping):
        raise SnapPreflightError(f"{node}: Coolify Compose has no services mapping")
    service = services.get(node)
    if not isinstance(service, Mapping):
        raise SnapPreflightError(f"{node}: Coolify Compose has no exact node service")
    command = service.get("command")
    if not isinstance(command, list):
        raise SnapPreflightError(f"{node}: Besu service does not use a list command")

    modes: list[str] = []
    min_peers: list[int] = []
    for item in command:
        if not isinstance(item, str):
            continue
        text = item.strip()
        mode_match = _SYNC_MODE_RE.fullmatch(text)
        if mode_match:
            modes.append(mode_match.group(1).upper())
        peers_match = _SYNC_MIN_PEERS_RE.fullmatch(text)
        if peers_match:
            min_peers.append(int(peers_match.group(1)))

    if len(modes) != 1:
        raise SnapPreflightError(f"{node}: expected exactly one --sync-mode flag; found {len(modes)}")
    if len(min_peers) != 1:
        raise SnapPreflightError(
            f"{node}: expected exactly one --sync-min-peers flag; found {len(min_peers)}"
        )
    return modes[0], min_peers[0]


def _is_running_healthy(status: str) -> bool:
    return str(status or "").strip().lower() == "running:healthy"


def _manual_fix_command(*, node: str, network: str, service_uuid: str, mode: str) -> str:
    return (
        f"python .\\tools\\mother\\node_sync_mode_switch_smoke.py {node} `\n"
        f"  --network {network} `\n"
        f"  --service-uuid {service_uuid} `\n"
        f"  --sync-mode {mode} `\n"
        "  --execute-mutations"
    )


def run_snap_preflight(
    *,
    runtime_state_root: str | Path,
    network: str,
    topology_evidence: str | Path | None = None,
    acknowledged_topology_evidence_sha256: str | None = None,
    timeout: float = 30.0,
    max_response_bytes: int = 12 * 1024 * 1024,
    opener: Any = urllib.request.urlopen,
) -> dict[str, Any]:
    """Inspect the current admitted network and decide whether a SNAP add is safe."""

    network_name = str(network or "").strip()
    if not network_name:
        raise SnapPreflightError("network is required")
    if timeout <= 0 or max_response_bytes <= 0:
        raise SnapPreflightError("timeout and max-response-bytes must be positive")

    discovered = topology_evidence is None
    selected_evidence = (
        _discover_current_topology_evidence(runtime_state_root, network=network_name)
        if discovered
        else topology_evidence
    )
    # Auto-discovery builds a path beneath runtime_state_root.  When that root is
    # relative (the normal CLI default: ``runtime/state``), the discovered Path is
    # also relative.  The acknowledged-topology loader interprets relative inputs
    # as relative to the Mother root, so passing the discovered path through as-is
    # would prepend ``runtime/state/mother`` a second time.  Canonicalize only
    # auto-discovered paths here; explicit user-supplied relative paths retain the
    # loader's established semantics.
    if discovered:
        selected_evidence = Path(selected_evidence).resolve(strict=False)
    try:
        private_state = _load_private_state(runtime_state_root, network=network_name, mode="inspect")
        topology_record = _load_acknowledged_current_topology_for_preflight(
            runtime_state_root,
            network=network_name,
            topology_evidence=selected_evidence,
            acknowledged_sha256=acknowledged_topology_evidence_sha256,
        )
        if topology_record is None:
            topology_record = _load_topology(
                runtime_state_root,
                network=network_name,
                topology_evidence=selected_evidence,
                acknowledged_sha256=acknowledged_topology_evidence_sha256,
            )
    except (MotherPreflightParanoiaError, MotherHelperCleanup2YagniError) as exc:
        raise SnapPreflightError(str(exc)) from exc

    nodes = [str(item) for item in topology_record.get("nodes", [])]
    services = topology_record.get("services")
    document = topology_record.get("document")
    topology = topology_record.get("topology")
    if not isinstance(services, list) or not isinstance(document, Mapping) or not isinstance(topology, Mapping):
        raise SnapPreflightError("current topology evidence is missing required node/service data")

    validators = _validator_set(topology, document)
    declared_validator_count = _validator_count(topology, document)
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    manual_commands: list[str] = []
    remediation_by_node: dict[str, str] = {}

    if len(nodes) != len(validators):
        blockers.append(
            {
                "code": "topology-node-validator-count-mismatch",
                "message": (
                    f"topology has {len(nodes)} node(s) but {len(validators)} admitted validator(s); "
                    "SNAP preflight refuses to guess which nodes are authoritative"
                ),
            }
        )
    if declared_validator_count is not None and declared_validator_count != len(validators):
        blockers.append(
            {
                "code": "declared-validator-count-mismatch",
                "message": (
                    f"validator_count={declared_validator_count} but validator_set contains "
                    f"{len(validators)} validator(s)"
                ),
            }
        )
    if len(validators) < 2:
        blockers.append(
            {
                "code": "insufficient-snap-foundation",
                "message": (
                    f"only {len(validators)} admitted validator(s) exist; add the next node as FULL, not SNAP"
                ),
            }
        )

    service_by_node = {
        str(item.get("node")): item
        for item in services
        if isinstance(item, Mapping) and str(item.get("node") or "").strip()
    }
    missing_records = [node for node in nodes if node not in service_by_node]
    if missing_records:
        blockers.append(
            {
                "code": "topology-service-record-missing",
                "message": "missing exact service bindings for: " + ", ".join(missing_records),
            }
        )

    for node in nodes:
        record = service_by_node.get(node)
        if not isinstance(record, Mapping):
            continue
        controller_id = str(record.get("controller_id") or "").strip()
        service_uuid = str(record.get("service_uuid") or "").strip()
        observation: dict[str, Any] = {
            "node": node,
            "controller_id": controller_id,
            "service_uuid": service_uuid,
        }
        try:
            controller = _controller(
                private_state,
                network=network_name,
                controller_id=controller_id,
            )
            detail = _service_detail(
                controller,
                service_uuid,
                timeout=float(timeout),
                max_response_bytes=int(max_response_bytes),
                opener=opener,
            )
            if detail.get("missing") is True:
                observation.update(
                    {
                        "service_missing": True,
                        "service_status": "missing",
                        "profile_valid": False,
                    }
                )
                blockers.append(
                    {
                        "code": "incumbent-service-missing",
                        "node": node,
                        "service_uuid": service_uuid,
                        "message": f"{node}: admitted incumbent Coolify service is missing",
                    }
                )
                observations.append(observation)
                continue

            payload = detail.get("payload")
            status = _service_status_from_payload(payload)
            compose_text, compose_source, compose_encoding = _compose_text_from_service_payload(payload)
            mode, min_peers = _sync_profile(compose_text, node)
            expected_min_peers = _VALID_PROFILE.get(mode)
            profile_valid = expected_min_peers == min_peers
            healthy = _is_running_healthy(status)
            observation.update(
                {
                    "service_missing": False,
                    "service_status": status,
                    "running_healthy": healthy,
                    "sync_mode": mode,
                    "sync_min_peers": min_peers,
                    "expected_sync_min_peers": expected_min_peers,
                    "profile_valid": profile_valid,
                    "compose_source": compose_source,
                    "compose_encoding": compose_encoding,
                }
            )

            if not healthy:
                blockers.append(
                    {
                        "code": "incumbent-not-running-healthy",
                        "node": node,
                        "service_uuid": service_uuid,
                        "message": f"{node}: service status is {status!r}, not 'running:healthy'",
                    }
                )

            if not profile_valid:
                desired_mode = mode if mode in _VALID_PROFILE else "FULL"
                blockers.append(
                    {
                        "code": "invalid-sync-profile-pair",
                        "node": node,
                        "service_uuid": service_uuid,
                        "message": (
                            f"{node}: {mode}/{min_peers} is invalid; {mode} requires "
                            f"--sync-min-peers={expected_min_peers}"
                        ),
                    }
                )
                if mode in _VALID_PROFILE:
                    # Above the two-validator boundary, preserve the chosen sync mode and
                    # repair only its paired --sync-min-peers value.  At exactly two
                    # validators, the stronger FULL/0 foundation rule below takes
                    # precedence, so do not emit a conflicting SNAP repair here.
                    if len(validators) != 2:
                        remediation_by_node[node] = desired_mode

            if len(validators) == 2 and (mode != "FULL" or min_peers != 0):
                blockers.append(
                    {
                        "code": "two-validator-foundation-not-full",
                        "node": node,
                        "service_uuid": service_uuid,
                        "message": (
                            f"{node}: a two-validator SNAP foundation requires both incumbents FULL/0; "
                            f"found {mode}/{min_peers}"
                        ),
                    }
                )
                remediation_by_node[node] = "FULL"
        except (MotherHelperCleanup2YagniError, SnapPreflightError) as exc:
            observation.update({"inspection_error": str(exc), "profile_valid": False})
            blockers.append(
                {
                    "code": "incumbent-inspection-failed",
                    "node": node,
                    "service_uuid": service_uuid,
                    "message": f"{node}: {exc}",
                }
            )
        observations.append(observation)

    # Build the remediation plan only after all blocker rules have run so stronger
    # topology rules can override weaker profile-pair repairs.  This guarantees at
    # most one exact manual mutation command per incumbent.
    observation_by_node = {
        str(item.get("node")): item
        for item in observations
        if isinstance(item, Mapping) and str(item.get("node") or "").strip()
    }
    for node in nodes:
        desired_mode = remediation_by_node.get(node)
        if desired_mode is None:
            continue
        observation = observation_by_node.get(node)
        if not isinstance(observation, Mapping):
            continue
        service_uuid = str(observation.get("service_uuid") or "").strip()
        if not service_uuid:
            continue
        manual_commands.append(
            _manual_fix_command(
                node=node,
                network=network_name,
                service_uuid=service_uuid,
                mode=desired_mode,
            )
        )

    if len(validators) >= 3:
        full_count = sum(
            1
            for item in observations
            if item.get("running_healthy") is True
            and item.get("sync_mode") == "FULL"
            and item.get("sync_min_peers") == 0
        )
        snap_count = sum(
            1
            for item in observations
            if item.get("running_healthy") is True
            and item.get("sync_mode") == "SNAP"
            and item.get("sync_min_peers") == 2
        )
        warnings.append(
            {
                "code": "mixed-profile-foundation-observed",
                "message": (
                    f"network has {len(validators)} admitted validators: {full_count} healthy FULL/0, "
                    f"{snap_count} healthy SNAP/2; mixed profiles are allowed above the two-validator boundary"
                ),
            }
        )

    safe = not blockers
    return {
        "kind": KIND,
        "schema_version": 1,
        "status": "pass" if safe else "blocked",
        "safe_to_add_snap": safe,
        "read_only": True,
        "network_mutation_performed": False,
        "network": network_name,
        "topology_evidence": {
            "path": str(topology_record.get("path")),
            "sha256": str(topology_record.get("sha256")),
            "discovered_from_disk": discovered,
        },
        "topology": {
            "nodes": nodes,
            "node_count": len(nodes),
            "validator_set": validators,
            "validator_count": len(validators),
            "declared_validator_count": declared_validator_count,
        },
        "service_observations": observations,
        "blockers": blockers,
        "warnings": warnings,
        "manual_fix_commands": manual_commands,
        "summary": {
            "safe_to_add_snap": safe,
            "blocker_count": len(blockers),
            "warning_count": len(warnings),
            "incumbent_count": len(observations),
            "two_validator_full_foundation_required": len(validators) == 2,
            "network_mutation_performed": False,
            "manual_remediation_command_count": len(manual_commands),
        },
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only SNAP paranoia preflight. Inspect admitted Mother validators, exact Coolify "
            "service health, and Besu FULL/0 vs SNAP/2 profiles before adding a SNAP node."
        )
    )
    parser.add_argument("--network", required=True)
    parser.add_argument("--runtime-state-root", default="runtime/state")
    parser.add_argument("--topology-evidence")
    parser.add_argument("--acknowledge-topology-evidence-sha256")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-response-bytes", type=int, default=12 * 1024 * 1024)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = run_snap_preflight(
            runtime_state_root=args.runtime_state_root,
            network=args.network,
            topology_evidence=args.topology_evidence,
            acknowledged_topology_evidence_sha256=args.acknowledge_topology_evidence_sha256,
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
        )
    except SnapPreflightError as exc:
        print(f"MOTHER_PREFLIGHT_PARANOIA_SNAP_FAILED: {exc}", file=sys.stderr)
        return 2

    if result["safe_to_add_snap"]:
        print("MOTHER_PREFLIGHT_PARANOIA_SNAP_PASS: current network foundation is safe for a SNAP add.")
    else:
        print(
            "MOTHER_PREFLIGHT_PARANOIA_SNAP_BLOCKED: do not add the next node as SNAP until the "
            "reported blockers are resolved."
        )
    print(json.dumps(result, indent=2, sort_keys=True))

    commands = result.get("manual_fix_commands") or []
    if commands:
        print()
        print(
            "MOTHER_PREFLIGHT_PARANOIA_SNAP_REMEDIATION_REQUIRED: "
            f"{len(commands)} manual command(s) available; this preflight remains blocked and exits non-zero."
        )
        print("Manual profile-fix command(s); review and run these yourself, then rerun this preflight:")
        for index, command in enumerate(commands, start=1):
            print()
            print(f"[{index}]")
            print(command)

    return 0 if result["safe_to_add_snap"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
