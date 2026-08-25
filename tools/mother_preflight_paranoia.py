#!/usr/bin/env python3
"""Read-only Mother mutation preflight for stale helper cleanup.

It inspects the current topology's live Coolify service details and Compose
documents before add-node/remove-node.  It reports stale helper cleanup needs
and blocks mutation when required current validators are not live-healthy.

It performs GET-only inspection and never patches, restarts, deploys, votes, or
deletes anything.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping
import urllib.request

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.mother_helper_cleanup2_yagni import (
    HELPER_EXACT_NAMES,
    HELPER_PREFIXES,
    MIMIC_LABEL,
    SHIM_LABEL,
    MotherHelperCleanup2YagniError,
    _compose_text_from_service_payload,
    _controller,
    _load_private_state,
    _load_topology,
    _parse_compose,
    _service_detail,
)
from tools.mother.common.paths import MotherPaths
from tools.mother.common.deployment_node_add_validator_admission import (
    _find_conflicting_node_remove_voters,
)
from tools.mother.common.deployment_node_remove_do import (
    _find_conflicting_add_node_voters,
)


KIND = "main_computer.mother.preflight_paranoia.v1"
CURRENT_TOPOLOGY_EVIDENCE_DIRS = (
    "deployment-node-add-post-admission-observe",
    "deployment-node-remove-finalize",
    "deployment-node-add-single-node-chain-and-hub-proof",
    "deployment-live-current-topology",
    "deployment-live-topology-empty-rectification",
)
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


class MotherPreflightParanoiaError(RuntimeError):
    """The read-only preflight could not produce a trustworthy result."""


def _summary_marks_current_topology(summary: Mapping[str, Any]) -> bool:
    return (
        summary.get("topology_current") is True
        or summary.get("current_topology_marked_by_evidence") is True
    )


def _candidate_is_current(document: Mapping[str, Any], *, network: str) -> bool:
    summary = document.get("summary")
    document_network = document.get("network")
    return bool(
        (not isinstance(document_network, str) or document_network == network)
        and document.get("status") == "pass"
        and isinstance(summary, Mapping)
        and summary.get("complete") is True
        and summary.get("clean") is True
        and _summary_marks_current_topology(summary)
    )


def _discover_current_topology_evidence(runtime_state_root: str | Path, *, network: str) -> Path:
    evidence_root = Path(runtime_state_root) / "mother" / "evidence"
    candidates: list[tuple[str, float, str, Path]] = []
    for directory in CURRENT_TOPOLOGY_EVIDENCE_DIRS:
        root = evidence_root / directory
        if not root.is_dir():
            continue
        for path in root.glob("*.json"):
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(document, Mapping) or not _candidate_is_current(document, network=network):
                continue
            stamp = str(document.get("completed_at") or document.get("observed_at") or "")
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0.0
            candidates.append((stamp, mtime, path.name, path))
    if not candidates:
        raise MotherPreflightParanoiaError(
            "no passed, clean, complete, current topology evidence was found on disk"
        )
    return sorted(candidates)[-1][3]


def _sha256(value: str, label: str) -> str:
    normalized = str(value or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise MotherPreflightParanoiaError(f"{label} must be a lowercase SHA-256 hex digest")
    return normalized


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _topology_document(document: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("final_topology", "current_topology", "post_add_topology", "post_removal_topology"):
        value = document.get(key)
        if isinstance(value, Mapping):
            return value
    return document


def _string_list(value: Any, label: str) -> list[str] | None:
    if not isinstance(value, list):
        return None
    items: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if not text:
            raise MotherPreflightParanoiaError(f"{label} contains an empty value")
        items.append(text)
    return list(dict.fromkeys(items))


def _preflight_topology_nodes(document: Mapping[str, Any], topology: Mapping[str, Any]) -> list[str]:
    summary = document.get("summary")
    candidates: list[Any] = []
    if isinstance(summary, Mapping):
        candidates.append(summary.get("final_nodes"))
    candidates.extend([topology.get("nodes"), document.get("nodes")])

    for raw in candidates:
        nodes = _string_list(raw, "topology nodes")
        if nodes is None:
            continue
        if nodes:
            return nodes
        if isinstance(summary, Mapping) and summary.get("empty_topology_marked_by_evidence") is True:
            return []

    observations = document.get("service_observations")
    if isinstance(observations, list):
        nodes = [
            str(item.get("node") or "").strip()
            for item in observations
            if isinstance(item, Mapping) and str(item.get("node") or "").strip()
        ]
        if nodes:
            return list(dict.fromkeys(nodes))

    raise MotherPreflightParanoiaError("topology evidence does not list current nodes")


def _preflight_service_summary(node: str, record: Mapping[str, Any]) -> dict[str, Any] | None:
    controller_id = record.get("controller_id")
    service_uuid = record.get("service_uuid") or record.get("created_service_uuid")
    if not isinstance(controller_id, str) or not controller_id.strip():
        return None
    if not isinstance(service_uuid, str) or not service_uuid.strip():
        return None
    return {
        "node": node,
        "controller_id": controller_id.strip(),
        "service_uuid": service_uuid.strip(),
    }


def _preflight_service_records(
    document: Mapping[str, Any],
    topology: Mapping[str, Any],
    nodes: list[str],
) -> list[dict[str, Any]]:
    by_node: dict[str, dict[str, Any]] = {}

    raw_services = topology.get("services")
    if isinstance(raw_services, Mapping):
        for node in nodes:
            record = raw_services.get(node)
            if isinstance(record, Mapping):
                summary = _preflight_service_summary(node, record)
                if summary is not None:
                    by_node[node] = summary

    observations = document.get("service_observations")
    if isinstance(observations, list):
        for item in observations:
            if not isinstance(item, Mapping):
                continue
            node = str(item.get("node") or "").strip()
            if node not in nodes:
                continue
            summary = _preflight_service_summary(node, item)
            if summary is not None:
                by_node[node] = summary

    missing = [node for node in nodes if node not in by_node]
    if missing:
        raise MotherPreflightParanoiaError(
            "topology evidence lacks service records for: " + ", ".join(missing)
        )
    return [by_node[node] for node in nodes]


def _load_acknowledged_current_topology_for_preflight(
    runtime_state_root: str | Path,
    *,
    network: str,
    topology_evidence: str | Path | None,
    acknowledged_sha256: str | None,
) -> dict[str, Any] | None:
    if topology_evidence is None:
        return None

    paths = MotherPaths(runtime_state_root=Path(runtime_state_root))
    try:
        path = paths.validate_contained(topology_evidence)
    except (TypeError, ValueError) as exc:
        raise MotherPreflightParanoiaError(str(exc)) from exc

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise MotherPreflightParanoiaError(f"topology evidence does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise MotherPreflightParanoiaError(f"topology evidence is not valid JSON: {path}") from exc
    if not isinstance(document, Mapping):
        raise MotherPreflightParanoiaError(f"topology evidence must be a JSON object: {path}")

    actual_sha = _file_sha256(path)
    if acknowledged_sha256 is not None and actual_sha != _sha256(acknowledged_sha256, "topology evidence SHA-256"):
        raise MotherPreflightParanoiaError("acknowledged topology evidence SHA-256 does not match the evidence file")

    document_network = document.get("network")
    if isinstance(document_network, str) and document_network != network:
        raise MotherPreflightParanoiaError("topology evidence network does not match --network")

    summary = document.get("summary")
    if not (
        document.get("status") == "pass"
        and isinstance(summary, Mapping)
        and summary.get("complete") is True
        and summary.get("clean") is True
        and _summary_marks_current_topology(summary)
    ):
        return None

    topology = _topology_document(document)
    nodes = _preflight_topology_nodes(document, topology)
    services = _preflight_service_records(document, topology, nodes)
    return {
        "path": path,
        "sha256": actual_sha,
        "discovered": False,
        "document": document,
        "topology": topology,
        "nodes": nodes,
        "services": services,
        "current_topology_marker_accepted": True,
        "empty_topology_accepted_for_add_node": (
            isinstance(summary, Mapping)
            and summary.get("empty_topology_marked_by_evidence") is True
            and nodes == []
        ),
    }


def _explicit_empty_topology_nodes(document: Mapping[str, Any]) -> list[str] | None:
    summary = document.get("summary")
    topology = document.get("final_topology")
    if not isinstance(topology, Mapping):
        topology = document.get("current_topology")
    if not isinstance(summary, Mapping) or not isinstance(topology, Mapping):
        return None
    if summary.get("empty_topology_marked_by_evidence") is not True:
        return None
    if summary.get("current_topology_marked_by_evidence") is not True and summary.get("topology_current") is not True:
        return None

    final_nodes = summary.get("final_nodes")
    topology_nodes = topology.get("nodes")
    if not isinstance(final_nodes, list) or not isinstance(topology_nodes, list):
        return None
    if final_nodes or topology_nodes:
        return None

    services = topology.get("services")
    if isinstance(services, Mapping) and services:
        return None
    validator_set = topology.get("validator_set")
    if isinstance(validator_set, list) and validator_set:
        return None
    return []


def _load_acknowledged_empty_topology_for_add_node(
    runtime_state_root: str | Path,
    *,
    network: str,
    topology_evidence: str | Path | None,
    acknowledged_sha256: str | None,
) -> dict[str, Any] | None:
    if topology_evidence is None:
        return None
    if acknowledged_sha256 is None:
        return None

    paths = MotherPaths(runtime_state_root=Path(runtime_state_root))
    try:
        path = paths.validate_contained(topology_evidence)
    except (TypeError, ValueError) as exc:
        raise MotherPreflightParanoiaError(str(exc)) from exc

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise MotherPreflightParanoiaError(f"topology evidence does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise MotherPreflightParanoiaError(f"topology evidence is not valid JSON: {path}") from exc
    if not isinstance(document, Mapping):
        raise MotherPreflightParanoiaError(f"topology evidence must be a JSON object: {path}")

    actual_sha = _file_sha256(path)
    if actual_sha != _sha256(acknowledged_sha256, "topology evidence SHA-256"):
        raise MotherPreflightParanoiaError("acknowledged topology evidence SHA-256 does not match the evidence file")

    document_network = document.get("network")
    if isinstance(document_network, str) and document_network != network:
        raise MotherPreflightParanoiaError("topology evidence network does not match --network")

    summary = document.get("summary")
    if not (
        document.get("status") == "pass"
        and isinstance(summary, Mapping)
        and summary.get("complete") is True
        and summary.get("clean") is True
        and str(summary.get("next_phase") or document.get("next_phase") or "") == f"add-node-prep-{network}"
    ):
        return None

    nodes = _explicit_empty_topology_nodes(document)
    if nodes is None:
        return None

    topology = document.get("final_topology")
    if not isinstance(topology, Mapping):
        topology = document.get("current_topology")
    if not isinstance(topology, Mapping):
        topology = {"nodes": [], "services": {}, "validator_set": []}

    return {
        "path": path,
        "sha256": actual_sha,
        "discovered": False,
        "document": document,
        "topology": topology,
        "nodes": nodes,
        "services": [],
        "empty_topology_accepted_for_add_node": True,
    }


def _private_validator_address(private_state: Any, *, network: str, node: str) -> str:
    try:
        document = yaml.safe_load(private_state.document_bytes)
        value = document["networks"][network]["validators"][node]["address"]
    except (KeyError, TypeError, yaml.YAMLError) as exc:
        raise MotherPreflightParanoiaError(
            f"validator address for {node!r} is missing from Mother private state"
        ) from exc
    address = str(value or "").strip().lower()
    if ADDRESS_RE.fullmatch(address) is None:
        raise MotherPreflightParanoiaError(
            f"validator address for {node!r} is invalid in Mother private state"
        )
    return address


def _labels(definition: Mapping[str, Any]) -> dict[str, str]:
    raw = definition.get("labels")
    if isinstance(raw, Mapping):
        return {str(key): str(value) for key, value in raw.items()}
    result: dict[str, str] = {}
    if isinstance(raw, list):
        for item in raw:
            text = str(item)
            if "=" in text:
                key, value = text.split("=", 1)
                result[key] = value
    return result


def _cleanup2_supported(name: str) -> bool:
    return name in HELPER_EXACT_NAMES or any(name.startswith(prefix) for prefix in HELPER_PREFIXES)


def _cleanup2_retired_mimic(definition: Mapping[str, Any]) -> bool:
    labels = _labels(definition)
    return (
        labels.get(MIMIC_LABEL, "").lower() == "true"
        and labels.get(SHIM_LABEL, "").lower() == "true"
        and labels.get("main_computer.mother.cleanup_scope") == "helper-cleanup2-yagni"
    )


def _active_cleanup_helpers(compose_text: str) -> tuple[list[str], list[str]]:
    compose = _parse_compose(compose_text)
    services = compose.get("services")
    if not isinstance(services, Mapping):
        return [], []
    active: list[str] = []
    retired: list[str] = []
    for raw_name, raw_definition in services.items():
        name = str(raw_name)
        if not _cleanup2_supported(name):
            continue
        definition = raw_definition if isinstance(raw_definition, Mapping) else {}
        if _cleanup2_retired_mimic(definition):
            retired.append(name)
        else:
            active.append(name)
    return sorted(active), sorted(retired)


def _service_status_from_payload(payload: Any) -> str:
    candidates: list[Mapping[str, Any]] = []
    if isinstance(payload, Mapping):
        candidates.append(payload)
        for key in ("service", "data", "application", "resource"):
            value = payload.get(key)
            if isinstance(value, Mapping):
                candidates.append(value)
    for record in candidates:
        for key in ("status", "human_status", "state", "health"):
            value = record.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return "unknown"


def _required_validator_health_failed(
    *,
    operation: str,
    service_node: str,
    target_node: str,
    missing: bool,
    service_status: str,
) -> bool:
    # Adding a validator depends on every current validator being able to vote
    # and serve as a healthy bootnode.  Removing a validator should not be
    # blocked merely because the target being removed is unhealthy, but the
    # remaining survivor validators must be live-healthy before removal voting.
    required = operation == "add-node" or (operation == "remove-node" and service_node != target_node)
    if not required:
        return False
    return missing or service_status.strip().lower() != "running:healthy"


def _cleanup_command(
    *,
    python_executable: str,
    runtime_state_root: str | Path,
    network: str,
    topology_path: str | Path,
    topology_sha256: str,
    timeout: float,
    max_response_bytes: int,
    max_wait_seconds: float,
    poll_interval_seconds: float,
) -> str:
    cleanup_script = REPO_ROOT / "tools" / "mother_helper_cleanup2_yagni.py"
    argv = [
        str(python_executable),
        str(cleanup_script),
        "execute",
        "--runtime-state-root",
        str(runtime_state_root),
        "--network",
        str(network),
        "--topology-evidence",
        str(topology_path),
        "--acknowledge-topology-evidence-sha256",
        str(topology_sha256),
        "--timeout",
        str(float(timeout)),
        "--max-response-bytes",
        str(int(max_response_bytes)),
        "--max-wait-seconds",
        str(float(max_wait_seconds)),
        "--poll-interval-seconds",
        str(float(poll_interval_seconds)),
        "--write-evidence",
    ]
    return subprocess.list2cmdline(argv)


def run_preflight_paranoia(
    *,
    operation: str,
    runtime_state_root: str | Path,
    network: str,
    node: str,
    topology_evidence: str | Path | None = None,
    acknowledged_topology_evidence_sha256: str | None = None,
    timeout: float = 30.0,
    max_response_bytes: int = 12 * 1024 * 1024,
    max_wait_seconds: float = 60.0,
    poll_interval_seconds: float = 5.0,
    opener: Any = urllib.request.urlopen,
    python_executable: str | None = None,
) -> dict[str, Any]:
    operation_name = str(operation).strip()
    if operation_name not in {"add-node", "remove-node"}:
        raise MotherPreflightParanoiaError("operation must be add-node or remove-node")
    network_name = str(network).strip()
    node_name = str(node).strip()
    if not network_name or not node_name:
        raise MotherPreflightParanoiaError("network and node are required")
    if timeout <= 0 or max_response_bytes <= 0 or max_wait_seconds < 0 or poll_interval_seconds < 0:
        raise MotherPreflightParanoiaError("timeout/response/wait arguments are invalid")

    discovered_topology = topology_evidence is None
    selected_topology_evidence = (
        _discover_current_topology_evidence(runtime_state_root, network=network_name)
        if discovered_topology
        else topology_evidence
    )
    try:
        private_state = _load_private_state(runtime_state_root, network=network_name, mode="inspect")
        topology = None
        if topology_evidence is not None or acknowledged_topology_evidence_sha256 is not None:
            topology = _load_acknowledged_current_topology_for_preflight(
                runtime_state_root,
                network=network_name,
                topology_evidence=selected_topology_evidence,
                acknowledged_sha256=acknowledged_topology_evidence_sha256,
            )
        if topology is None and operation_name == "add-node":
            topology = _load_acknowledged_empty_topology_for_add_node(
                runtime_state_root,
                network=network_name,
                topology_evidence=selected_topology_evidence,
                acknowledged_sha256=acknowledged_topology_evidence_sha256,
            )
        if topology is None:
            topology = _load_topology(
                runtime_state_root,
                network=network_name,
                topology_evidence=selected_topology_evidence,
                acknowledged_sha256=acknowledged_topology_evidence_sha256,
            )
    except MotherHelperCleanup2YagniError as exc:
        raise MotherPreflightParanoiaError(f"{exc.code}: {exc}") from exc

    target_validator = _private_validator_address(
        private_state,
        network=network_name,
        node=node_name,
    )

    observations: list[dict[str, Any]] = []
    active_helpers: list[dict[str, Any]] = []
    retired_helpers: list[dict[str, Any]] = []
    blocking_conflicts: list[dict[str, Any]] = []
    required_validator_health_failures: list[dict[str, Any]] = []

    for service_record in topology["services"]:
        current_node = str(service_record["node"])
        controller_id = str(service_record["controller_id"])
        service_uuid = str(service_record["service_uuid"])
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
        except MotherHelperCleanup2YagniError as exc:
            raise MotherPreflightParanoiaError(
                f"{exc.code}: failed inspecting {current_node}: {exc}"
            ) from exc

        receipt = detail.get("receipt")
        service_missing = detail.get("missing") is True
        service_status = "missing" if service_missing else _service_status_from_payload(detail.get("payload"))
        observations.append(
            {
                "node": current_node,
                "controller_id": controller_id,
                "service_uuid": service_uuid,
                "http_status": receipt.get("status") if isinstance(receipt, Mapping) else None,
                "service_missing": service_missing,
                "service_status": service_status,
            }
        )
        if _required_validator_health_failed(
            operation=operation_name,
            service_node=current_node,
            target_node=node_name,
            missing=service_missing,
            service_status=service_status,
        ):
            required_validator_health_failures.append(
                {
                    "node": current_node,
                    "controller_id": controller_id,
                    "service_uuid": service_uuid,
                    "service_missing": service_missing,
                    "service_status": service_status,
                    "reason": "required-current-validator-not-running-healthy",
                }
            )
        if service_missing:
            continue

        try:
            compose_text, _source_field, _source_encoding = _compose_text_from_service_payload(detail["payload"])
        except MotherHelperCleanup2YagniError as exc:
            raise MotherPreflightParanoiaError(
                f"{exc.code}: failed reading Compose for {current_node}: {exc}"
            ) from exc

        active, retired = _active_cleanup_helpers(compose_text)
        for helper in active:
            active_helpers.append(
                {
                    "node": current_node,
                    "controller_id": controller_id,
                    "service_uuid": service_uuid,
                    "helper_service": helper,
                }
            )
        for helper in retired:
            retired_helpers.append(
                {
                    "node": current_node,
                    "controller_id": controller_id,
                    "service_uuid": service_uuid,
                    "helper_service": helper,
                }
            )

        if operation_name == "add-node":
            conflicts = _find_conflicting_node_remove_voters(
                compose_text,
                candidate_validator=target_validator,
            )
        else:
            conflicts = _find_conflicting_add_node_voters(
                compose_text,
                target_validator=target_validator,
            )
        for conflict in conflicts:
            blocking_conflicts.append(
                {
                    "node": current_node,
                    "controller_id": controller_id,
                    "service_uuid": service_uuid,
                    **dict(conflict),
                }
            )

    cleanup_required = bool(active_helpers)
    required_validator_unhealthy = bool(required_validator_health_failures)
    topology_path = str(topology["path"])
    topology_sha256 = str(topology["sha256"])
    command = None
    if cleanup_required:
        command = _cleanup_command(
            python_executable=python_executable or sys.executable,
            runtime_state_root=runtime_state_root,
            network=network_name,
            topology_path=topology_path,
            topology_sha256=topology_sha256,
            timeout=float(timeout),
            max_response_bytes=int(max_response_bytes),
            max_wait_seconds=float(max_wait_seconds),
            poll_interval_seconds=float(poll_interval_seconds),
        )

    return {
        "kind": KIND,
        "schema_version": 1,
        "status": (
            "cleanup-required"
            if cleanup_required
            else "required-validator-unhealthy"
            if required_validator_unhealthy
            else "pass"
        ),
        "operation": operation_name,
        "network": network_name,
        "target_node": node_name,
        "target_validator": target_validator,
        "read_only": True,
        "cleanup_required": cleanup_required,
        "required_validator_unhealthy": required_validator_unhealthy,
        "cleanup_command": command,
        "topology_evidence": {
            "path": topology_path,
            "sha256": topology_sha256,
            "discovered_from_disk": discovered_topology,
        },
        "active_cleanup_helpers": active_helpers,
        "retired_cleanup_helpers": retired_helpers,
        "blocking_conflicts": blocking_conflicts,
        "required_validator_health_failures": required_validator_health_failures,
        "service_observations": observations,
        "summary": {
            "active_cleanup_helper_count": len(active_helpers),
            "retired_cleanup_helper_count": len(retired_helpers),
            "blocking_conflict_count": len(blocking_conflicts),
            "required_validator_unhealthy_count": len(required_validator_health_failures),
            "cleanup_command_emitted": command is not None,
            "current_topology_marker_accepted": bool(topology.get("current_topology_marker_accepted")),
            "empty_topology_accepted_for_add_node": bool(topology.get("empty_topology_accepted_for_add_node")),
            "network_mutation_performed": False,
            "clean": (not cleanup_required and not required_validator_unhealthy),
        },
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only Mother preflight paranoia: inspect current Compose for stale helpers "
            "before add-node/remove-node and emit cleanup2 when cleanup is needed."
        )
    )
    parser.add_argument("operation", choices=("add-node", "remove-node"))
    parser.add_argument("--runtime-state-root", required=True)
    parser.add_argument("--network", required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--topology-evidence")
    parser.add_argument("--acknowledge-topology-evidence-sha256")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-response-bytes", type=int, default=12 * 1024 * 1024)
    parser.add_argument("--max-wait-seconds", type=float, default=60.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=5.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = run_preflight_paranoia(
            operation=args.operation,
            runtime_state_root=args.runtime_state_root,
            network=args.network,
            node=args.node,
            topology_evidence=args.topology_evidence,
            acknowledged_topology_evidence_sha256=args.acknowledge_topology_evidence_sha256,
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
            max_wait_seconds=args.max_wait_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
        )
    except MotherPreflightParanoiaError as exc:
        print(f"MOTHER_PREFLIGHT_PARANOIA_FAILED: {exc}", file=sys.stderr)
        return 1

    if result["cleanup_required"]:
        print(
            "MOTHER_PREFLIGHT_PARANOIA_CLEANUP_REQUIRED: "
            f"{result['summary']['active_cleanup_helper_count']} active cleanup2-supported helper(s) "
            f"found before {result['operation']}."
        )
        if result["blocking_conflicts"]:
            print(
                "MOTHER_PREFLIGHT_PARANOIA_BLOCKING_CONFLICTS: "
                f"{len(result['blocking_conflicts'])} opposite-operation voter conflict(s) detected."
            )
    if result.get("required_validator_unhealthy"):
        print(
            "MOTHER_PREFLIGHT_PARANOIA_REQUIRED_VALIDATOR_UNHEALTHY: "
            f"{result['summary']['required_validator_unhealthy_count']} required current validator service(s) "
            "are not running:healthy."
        )
    if not result["cleanup_required"] and not result.get("required_validator_unhealthy"):
        print(
            "MOTHER_PREFLIGHT_PARANOIA_CLEAN: "
            f"no active cleanup2-supported helpers found before {result['operation']}."
        )

    print(json.dumps(result, indent=2, sort_keys=True))
    if result["cleanup_required"]:
        print()
        print("Run this cleanup command before the mutation:")
        print(result["cleanup_command"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
