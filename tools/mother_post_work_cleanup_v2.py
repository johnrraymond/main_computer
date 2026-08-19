#!/usr/bin/env python3
"""Deterministic Mother post-work cleanup v2 orchestrator.

This script is intentionally only an orchestrator.  It loads already-finalized
topology evidence and then delegates to the narrow v2 cleanup units in a fixed
order:

1. chain RPC preflight,
2. chain-only satisfied pending-vote cleanup,
3. retired admission-voter shims for the transient voter nodes proven by the
   source validator-admission evidence,
4. retired activation-guardian shims for every current topology service,
5. retired genesis-proof-guardian shims for every current topology service.

It does not contain broad helper discovery, helper deletion, Besu/FDB/Hub
cleanup logic, bespoke Coolify health interpretation, or parent-stack
redeploys.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable, Mapping
import urllib.request

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import PrivateStateReadResult, read_private_state
from tools.mother.common.deployment_validator_routes import (
    MotherDeploymentValidatorRouteError,
    controller_validator_host,
    validator_route_from_record,
)
from tools.mother_activation_guardian_cleanup import (
    execute_activation_guardian_cleanup,
    inspect_activation_guardian_cleanup,
)
from tools.mother_admission_voter_cleanup import (
    execute_admission_voter_cleanup,
    inspect_admission_voter_cleanup,
)
from tools.mother_chain_cleanup import run_chain_cleanup
from tools.mother_genesis_proof_guardian_cleanup import (
    execute_genesis_proof_guardian_cleanup,
    inspect_genesis_proof_guardian_cleanup,
)


KIND = "main_computer.mother.post_work_cleanup_v2.v1"
EVIDENCE_SUBDIR = "post-work-cleanup-v2"

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class MotherPostWorkCleanupV2Error(RuntimeError):
    """Post-work cleanup v2 failed before a trustworthy rollup result."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


ProgressCallback = Callable[[str, str, Mapping[str, Any]], None]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _identifier(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text or not _IDENTIFIER_RE.fullmatch(text):
        raise MotherPostWorkCleanupV2Error(
            "MOTHER_POST_WORK_CLEANUP_V2_INVALID_ARGUMENT",
            f"{name} must be a simple identifier",
        )
    return text


def _sha256(value: Any, name: str) -> str:
    text = str(value or "").strip().lower()
    if not _SHA256_RE.fullmatch(text):
        raise MotherPostWorkCleanupV2Error(
            "MOTHER_POST_WORK_CLEANUP_V2_INVALID_ARGUMENT",
            f"{name} must be a SHA-256 hex digest",
        )
    return text


def _positive(value: float, name: str) -> float:
    number = float(value)
    if number <= 0:
        raise MotherPostWorkCleanupV2Error(
            "MOTHER_POST_WORK_CLEANUP_V2_INVALID_ARGUMENT",
            f"{name} must be positive",
        )
    return number


def _nonnegative(value: float, name: str) -> float:
    number = float(value)
    if number < 0:
        raise MotherPostWorkCleanupV2Error(
            "MOTHER_POST_WORK_CLEANUP_V2_INVALID_ARGUMENT",
            f"{name} must be non-negative",
        )
    return number


def _service_summary(
    node: str,
    controller_id: str,
    service_uuid: str,
    record: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "node": _identifier(node, "topology node"),
        "controller_id": _identifier(controller_id, f"{node} controller_id"),
        "service_uuid": _identifier(service_uuid, f"{node} service_uuid"),
    }
    if isinstance(record, Mapping):
        for key in ("validator_route", "p2p_route"):
            value = record.get(key)
            if isinstance(value, Mapping):
                result[key] = dict(value)
        for key in ("vpn_ip", "p2p_port", "p2p_endpoint", "enode", "rpc_url", "chain_rpc_url"):
            value = record.get(key)
            if value is not None:
                result[key] = value
    return result


def _latest_sort_key(path: Path, document: Mapping[str, Any]) -> tuple[str, float, str]:
    stamp = document.get("completed_at") or document.get("observed_at") or ""
    return (str(stamp), path.stat().st_mtime if path.exists() else 0.0, path.name)


def _emit_progress(progress: ProgressCallback | None, phase: str, message: str, **fields: Any) -> None:
    if progress is not None:
        progress(phase, message, fields)


def _stderr_progress(quiet: bool) -> ProgressCallback | None:
    if quiet:
        return None

    def emit(phase: str, message: str, fields: Mapping[str, Any]) -> None:
        suffix = " ".join(f"{key}={value}" for key, value in sorted(fields.items()))
        print(
            f"{_utc_now()} mother-post-work-cleanup-v2.{phase} {message}{(' ' + suffix) if suffix else ''}",
            file=sys.stderr,
            flush=True,
        )

    return emit


def _operation(network: str, mode: str) -> OperationIdentity:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    network_id = _identifier(network, "network")
    mode_id = _identifier(mode, "mode")
    operation_id = f"mother-post-work-cleanup-v2-{mode_id}-{network_id}-{stamp}"
    return OperationIdentity(
        operation_id=operation_id,
        request_id=f"{operation_id}-request",
        network=network,
        operation_kind="MOTHER-OP-ADD-NODE",
    )


def _load_private_state(runtime_state_root: str | Path, *, network: str, mode: str) -> PrivateStateReadResult:
    paths = MotherPaths(runtime_state_root=Path(runtime_state_root)).resolve_private_state_paths()
    return read_private_state(paths, operation=_operation(network, mode))


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path, *, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MotherPostWorkCleanupV2Error(
            "MOTHER_POST_WORK_CLEANUP_V2_EVIDENCE_MISSING",
            f"{label} does not exist: {path}",
        ) from exc
    except json.JSONDecodeError as exc:
        raise MotherPostWorkCleanupV2Error(
            "MOTHER_POST_WORK_CLEANUP_V2_EVIDENCE_INVALID",
            f"{label} is not valid JSON: {path}",
        ) from exc
    if not isinstance(value, Mapping):
        raise MotherPostWorkCleanupV2Error(
            "MOTHER_POST_WORK_CLEANUP_V2_EVIDENCE_INVALID",
            f"{label} must be a JSON object: {path}",
        )
    return value


def _resolve_mother_path(paths: MotherPaths, value: str | Path) -> Path:
    try:
        return paths.validate_contained(value)
    except ValueError as exc:
        raise MotherPostWorkCleanupV2Error(
            "MOTHER_POST_WORK_CLEANUP_V2_PATH_INVALID",
            str(exc),
        ) from exc


def _topology(document: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("final_topology", "current_topology", "post_add_topology", "post_removal_topology"):
        value = document.get(key)
        if isinstance(value, Mapping):
            return value
    return document


def _topology_nodes(document: Mapping[str, Any], topology: Mapping[str, Any]) -> list[str]:
    candidates: list[Any] = []
    summary = document.get("summary")
    if isinstance(summary, Mapping):
        candidates.append(summary.get("final_nodes"))
    candidates.append(topology.get("nodes"))
    candidates.append(document.get("nodes"))

    for raw in candidates:
        if isinstance(raw, list):
            nodes = [_identifier(item, "topology node") for item in raw]
            if nodes:
                return nodes

    observations = document.get("service_observations")
    if isinstance(observations, list):
        nodes = []
        for item in observations:
            if isinstance(item, Mapping) and isinstance(item.get("node"), str):
                nodes.append(_identifier(item["node"], "service observation node"))
        if nodes:
            return list(dict.fromkeys(nodes))

    raise MotherPostWorkCleanupV2Error(
        "MOTHER_POST_WORK_CLEANUP_V2_TOPOLOGY_INVALID",
        "topology evidence does not list current nodes",
    )


def _validator_set(document: Mapping[str, Any], topology: Mapping[str, Any]) -> list[str]:
    summary = document.get("summary")
    candidates = [
        topology.get("validator_set"),
        document.get("final_validator_set"),
        summary.get("final_validator_set") if isinstance(summary, Mapping) else None,
    ]
    for raw in candidates:
        if isinstance(raw, list) and raw:
            return [str(item) for item in raw]
    raise MotherPostWorkCleanupV2Error(
        "MOTHER_POST_WORK_CLEANUP_V2_TOPOLOGY_INVALID",
        "topology evidence does not list the final validator set",
    )


def _chain_id(document: Mapping[str, Any], topology: Mapping[str, Any]) -> int | None:
    for raw in (topology.get("chain_id"), document.get("chain_id")):
        if isinstance(raw, int) and raw > 0:
            return raw
    return None


def _service_records(document: Mapping[str, Any], topology: Mapping[str, Any], nodes: list[str]) -> list[dict[str, Any]]:
    by_node: dict[str, dict[str, Any]] = {}

    raw_services = topology.get("services")
    if isinstance(raw_services, Mapping):
        for node in nodes:
            record = raw_services.get(node)
            if isinstance(record, Mapping):
                controller_id = record.get("controller_id")
                service_uuid = record.get("service_uuid") or record.get("created_service_uuid")
                if isinstance(controller_id, str) and isinstance(service_uuid, str) and service_uuid:
                    by_node[node] = _service_summary(node, controller_id, service_uuid, record)

    observations = document.get("service_observations")
    if isinstance(observations, list):
        for item in observations:
            if not isinstance(item, Mapping):
                continue
            node = item.get("node")
            controller_id = item.get("controller_id")
            service_uuid = item.get("service_uuid")
            if (
                isinstance(node, str)
                and node in nodes
                and isinstance(controller_id, str)
                and isinstance(service_uuid, str)
                and service_uuid
            ):
                # Service observations are refreshed by Coolify and override stale
                # UUID/controller data, but preserve route/RPC metadata already
                # found in final_topology.services when the observation omits it.
                merged = dict(by_node.get(node) or {})
                merged.update(_service_summary(node, controller_id, service_uuid, item))
                by_node[node] = merged

    target = document.get("target")
    if isinstance(target, Mapping):
        node = target.get("node")
        controller_id = target.get("controller_id")
        service_uuid = target.get("service_uuid") or target.get("previous_service_uuid")
        if (
            isinstance(node, str)
            and node in nodes
            and node not in by_node
            and isinstance(controller_id, str)
            and isinstance(service_uuid, str)
            and service_uuid
        ):
            by_node[node] = _service_summary(node, controller_id, service_uuid, target)

    missing = [node for node in nodes if node not in by_node]
    if missing:
        raise MotherPostWorkCleanupV2Error(
            "MOTHER_POST_WORK_CLEANUP_V2_TOPOLOGY_INVALID",
            "topology evidence lacks service records for: " + ", ".join(missing),
        )
    return [by_node[node] for node in nodes]


def _source_admission_evidence(
    paths: MotherPaths,
    topology_evidence: Mapping[str, Any],
) -> dict[str, Any] | None:
    source = topology_evidence.get("source_validator_admission_evidence")
    if not isinstance(source, Mapping):
        return None
    sha = source.get("sha256")
    if not isinstance(sha, str):
        return None
    locator = source.get("locator")
    raw_path = source.get("path")
    if isinstance(raw_path, str) and raw_path:
        evidence_path = _resolve_mother_path(paths, raw_path)
    elif isinstance(locator, str) and locator:
        evidence_path = _resolve_mother_path(paths, locator)
    else:
        return None
    if not evidence_path.is_file():
        return None
    expected_sha = _sha256(sha, "source validator-admission evidence SHA-256")
    actual_sha = _file_sha256(evidence_path)
    if actual_sha != expected_sha:
        raise MotherPostWorkCleanupV2Error(
            "MOTHER_POST_WORK_CLEANUP_V2_SOURCE_ADMISSION_ACK_MISMATCH",
            "topology evidence points at validator-admission evidence whose SHA-256 no longer matches disk",
        )
    return {
        "path": evidence_path,
        "sha256": actual_sha,
        "locator": locator,
    }


def _voter_nodes_from_admission_evidence(source: dict[str, Any] | None) -> list[str]:
    if source is None:
        return []
    document = _load_json(Path(source["path"]), label="source validator-admission evidence")
    raw = document.get("transient_voter_guardian_nodes")
    if not isinstance(raw, list):
        raw = document.get("voter_nodes")
    if not isinstance(raw, list):
        return []
    return [_identifier(item, "admission voter node") for item in raw]


def _topology_evidence_dir(paths: MotherPaths) -> Path:
    return paths.evidence_root / "deployment-node-add-post-admission-observe"


def _candidate_topology_evidence(paths: MotherPaths, *, network: str) -> list[dict[str, Any]]:
    root = _topology_evidence_dir(paths)
    if not root.is_dir():
        return []
    candidates: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        try:
            document = _load_json(path, label="topology evidence candidate")
            summary = document.get("summary")
            document_network = document.get("network")
            if isinstance(document_network, str) and document_network != network:
                continue
            if not (
                document.get("status") == "pass"
                and isinstance(summary, Mapping)
                and summary.get("complete") is True
                and summary.get("clean") is True
                and summary.get("topology_current") is True
            ):
                continue
            candidates.append({"path": path, "document": document, "sort_key": _latest_sort_key(path, document)})
        except MotherPostWorkCleanupV2Error:
            continue
    return sorted(candidates, key=lambda item: item["sort_key"])


def _discover_latest_topology_evidence(paths: MotherPaths, *, network: str) -> Path:
    candidates = _candidate_topology_evidence(paths, network=network)
    if not candidates:
        raise MotherPostWorkCleanupV2Error(
            "MOTHER_POST_WORK_CLEANUP_V2_TOPOLOGY_NOT_FOUND",
            "no passed, clean, complete, current add-node post-admission topology evidence was found on disk",
        )
    return Path(candidates[-1]["path"])


def _validate_topology_evidence(
    paths: MotherPaths,
    topology_evidence: str | Path | None,
    *,
    acknowledged_sha256: str | None,
    network: str,
) -> dict[str, Any]:
    discovered = topology_evidence is None
    path = _discover_latest_topology_evidence(paths, network=network) if discovered else _resolve_mother_path(paths, topology_evidence)
    document = _load_json(path, label="topology evidence")
    actual_sha = _file_sha256(path)
    if acknowledged_sha256 is not None and actual_sha != _sha256(acknowledged_sha256, "topology evidence SHA-256"):
        raise MotherPostWorkCleanupV2Error(
            "MOTHER_POST_WORK_CLEANUP_V2_TOPOLOGY_ACK_MISMATCH",
            "acknowledged topology evidence SHA-256 does not match the evidence file",
        )

    summary = document.get("summary")
    if not (
        document.get("status") == "pass"
        and isinstance(summary, Mapping)
        and summary.get("complete") is True
        and summary.get("clean") is True
        and summary.get("topology_current") is True
    ):
        raise MotherPostWorkCleanupV2Error(
            "MOTHER_POST_WORK_CLEANUP_V2_TOPOLOGY_NOT_ACCEPTED",
            "cleanup v2 requires passed, clean, complete, current topology evidence",
        )

    document_network = document.get("network")
    if isinstance(document_network, str) and document_network != network:
        raise MotherPostWorkCleanupV2Error(
            "MOTHER_POST_WORK_CLEANUP_V2_TOPOLOGY_NETWORK_MISMATCH",
            "topology evidence network does not match --network",
        )

    topology = _topology(document)
    nodes = _topology_nodes(document, topology)
    services = _service_records(document, topology, nodes)
    final_validator_set = _validator_set(document, topology)
    return {
        "path": path,
        "sha256": actual_sha,
        "document": document,
        "discovered": discovered,
        "topology": topology,
        "nodes": nodes,
        "services": services,
        "final_validator_set": final_validator_set,
        "chain_id": _chain_id(document, topology),
    }


def _step_success(result: Mapping[str, Any]) -> bool:
    return result.get("status") in {"pass", "clean", "cleanup-required", "skipped"}


def _step_error(name: str, node: str | None, exc: BaseException) -> dict[str, Any]:
    return {
        "step": name,
        "node": node,
        "status": "failed",
        "error_code": getattr(exc, "code", type(exc).__name__),
        "error": str(exc),
    }


def _call_service_cleanup(
    *,
    mode: str,
    cleanup_name: str,
    private_state: PrivateStateReadResult,
    network: str,
    service: Mapping[str, str],
    instant_deploy: bool,
    max_wait_seconds: float,
    poll_interval_seconds: float,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
    progress: ProgressCallback | None,
    admission_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    node = service["node"]
    controller_id = service["controller_id"]
    service_uuid = service["service_uuid"]
    _emit_progress(
        progress,
        cleanup_name,
        "starting service cleanup",
        mode=mode,
        node=node,
        controller_id=controller_id,
        service_uuid=service_uuid,
    )

    if cleanup_name == "admission-voter":
        if mode == "inspect":
            result = inspect_admission_voter_cleanup(
                private_state,
                network=network,
                controller_id=controller_id,
                service_uuid=service_uuid,
                node=node,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
                progress=progress,
            )
        else:
            assert admission_evidence is not None
            result = execute_admission_voter_cleanup(
                private_state,
                network=network,
                controller_id=controller_id,
                service_uuid=service_uuid,
                node=node,
                admission_evidence=Path(admission_evidence["path"]),
                acknowledged_admission_evidence_sha256=str(admission_evidence["sha256"]),
                acknowledged_service_uuid=service_uuid,
                allow_retired_admission_voter_shim=True,
                instant_deploy=instant_deploy,
                max_wait_seconds=max_wait_seconds,
                poll_interval_seconds=poll_interval_seconds,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
                progress=progress,
            )
    elif cleanup_name == "activation-guardian":
        if mode == "inspect":
            result = inspect_activation_guardian_cleanup(
                private_state,
                network=network,
                controller_id=controller_id,
                service_uuid=service_uuid,
                node=node,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
                progress=progress,
            )
        else:
            result = execute_activation_guardian_cleanup(
                private_state,
                network=network,
                controller_id=controller_id,
                service_uuid=service_uuid,
                node=node,
                acknowledged_service_uuid=service_uuid,
                allow_retired_activation_guardian_shim=True,
                instant_deploy=instant_deploy,
                max_wait_seconds=max_wait_seconds,
                poll_interval_seconds=poll_interval_seconds,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
                progress=progress,
            )
    elif cleanup_name == "genesis-proof-guardian":
        if mode == "inspect":
            result = inspect_genesis_proof_guardian_cleanup(
                private_state,
                network=network,
                controller_id=controller_id,
                service_uuid=service_uuid,
                node=node,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
                progress=progress,
            )
        else:
            result = execute_genesis_proof_guardian_cleanup(
                private_state,
                network=network,
                controller_id=controller_id,
                service_uuid=service_uuid,
                node=node,
                acknowledged_service_uuid=service_uuid,
                allow_retired_genesis_proof_guardian_shim=True,
                instant_deploy=instant_deploy,
                max_wait_seconds=max_wait_seconds,
                poll_interval_seconds=poll_interval_seconds,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
                progress=progress,
            )
    else:
        raise AssertionError(f"unknown cleanup step: {cleanup_name}")

    return {
        "step": cleanup_name,
        "node": node,
        "controller_id": controller_id,
        "service_uuid": service_uuid,
        "status": result.get("status"),
        "result": result,
    }


def _rpc_host_from_service(
    private_state: PrivateStateReadResult,
    *,
    network: str,
    service: Mapping[str, Any],
) -> tuple[str, str]:
    for key in ("rpc_url", "chain_rpc_url"):
        value = service.get(key)
        if isinstance(value, str) and value.strip():
            # The caller needs only the URL, so report the whole URL as host source.
            return value.strip(), f"topology.services.{service.get('node')}.{key}"
    route = validator_route_from_record(service)
    if isinstance(route, Mapping):
        host = route.get("vpn_ip") or route.get("advertised_host")
        if isinstance(host, str) and host.strip():
            return "http://" + host.strip() + ":8545", f"topology.services.{service.get('node')}.validator_route.vpn_ip"
    host = service.get("vpn_ip")
    if isinstance(host, str) and host.strip():
        return "http://" + host.strip() + ":8545", f"topology.services.{service.get('node')}.vpn_ip"
    try:
        host, source = controller_validator_host(
            private_state,
            network=network,
            controller_id=str(service.get("controller_id") or ""),
        )
    except MotherDeploymentValidatorRouteError as exc:
        raise MotherPostWorkCleanupV2Error(
            "MOTHER_POST_WORK_CLEANUP_V2_CHAIN_RPC_DISCOVERY_FAILED",
            str(exc),
        ) from exc
    return "http://" + host + ":8545", source + ".rpc-port-8545"


def _derive_chain_rpc_urls(
    private_state: PrivateStateReadResult,
    *,
    network: str,
    services: list[Mapping[str, Any]],
) -> tuple[list[str], list[dict[str, Any]]]:
    urls: list[str] = []
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for service in services:
        node = _identifier(service.get("node"), "topology node")
        url, source = _rpc_host_from_service(private_state, network=network, service=service)
        item = f"{node}={url}"
        if item in seen:
            continue
        seen.add(item)
        urls.append(item)
        sources.append({"node": node, "rpc_url": url, "source": source})
    return urls, sources



def _chain_cleanup_statuses(result: Mapping[str, Any]) -> list[str]:
    statuses: list[str] = []
    for item in result.get("nodes") or []:
        if not isinstance(item, Mapping):
            continue
        cleanup = item.get("cleanup")
        if isinstance(cleanup, Mapping):
            statuses.append(str(cleanup.get("status") or ""))
    return statuses


def _chain_cleanup_step_status(result: Mapping[str, Any], *, mode: str, preflight: bool = False) -> str:
    explicit_status = result.get("status")
    if explicit_status in {"pass", "clean"}:
        return "pass"
    if explicit_status in {"failed", "manual-review-required"}:
        return str(explicit_status)
    statuses = _chain_cleanup_statuses(result)
    if not statuses:
        return "failed"
    allowed = {"not_needed", "cleared"}
    if preflight or mode == "inspect":
        allowed = {"not_needed", "would_clear", "cleared"}
    return "pass" if all(item in allowed for item in statuses) else "manual-review-required"


def _chain_cleared_count(result: Mapping[str, Any]) -> int:
    summary = result.get("summary")
    if isinstance(summary, Mapping):
        try:
            return int(summary.get("cleared_count") or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def _host_chain_probe_commands(services: list[Mapping[str, Any]]) -> list[dict[str, str]]:
    commands: list[dict[str, str]] = []
    for service in services:
        node = str(service.get("node") or "").strip()
        service_uuid = str(service.get("service_uuid") or "").strip()
        controller_id = str(service.get("controller_id") or "").strip()
        if not node or not service_uuid:
            continue
        command = f"""NODE='{node}'
UUID='{service_uuid}'

test -n "$NODE" || {{ echo "NODE blank"; exit 1; }}
test -n "$UUID" || {{ echo "UUID blank"; exit 1; }}

C="$(docker ps -q --filter "name=^/${{NODE}}-${{UUID}}$" | head -n1)"
test -n "$C" || {{
  echo "Besu container not found for ${{NODE}}-${{UUID}}"
  docker ps --format 'table {{{{.Names}}}}\t{{{{.Status}}}}\t{{{{.Image}}}}' | grep -Ei "$NODE|$UUID|besu" || true
  exit 1
}}

docker run --rm --pull=never --network "container:$C" python:3.12-alpine python -c 'import json,urllib.request; RPC="http://127.0.0.1:8545";
def rpc(m,p=[]):
 b=json.dumps({{"jsonrpc":"2.0","id":1,"method":m,"params":p}}).encode()
 r=urllib.request.urlopen(urllib.request.Request(RPC,data=b,headers={{"Content-Type":"application/json"}},method="POST"),timeout=5)
 print(m, r.read().decode(), flush=True)
rpc("eth_chainId")
rpc("eth_blockNumber")
rpc("eth_syncing")
rpc("net_peerCount")
rpc("qbft_getValidatorsByBlockNumber",["latest"])
rpc("qbft_getPendingVotes")'"""
        commands.append(
            {
                "node": node,
                "controller_id": controller_id,
                "service_uuid": service_uuid,
                "command": command,
            }
        )
    return commands




def _cleanup1_python_source() -> str:
    return r"""
import json
import re
import sys
import time
import urllib.request

_MASK64 = 0xFFFFFFFFFFFFFFFF
_ROUND_CONSTANTS = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A,
    0x8000000080008000, 0x000000000000808B, 0x0000000080000001,
    0x8000000080008081, 0x8000000000008009, 0x000000000000008A,
    0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
    0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
    0x000000000000800A, 0x800000008000000A, 0x8000000080008081,
    0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)
_ROTATION_OFFSETS = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)

def _rotate_left_64(value, shift):
    shift %= 64
    if shift == 0:
        return value & _MASK64
    return ((value << shift) | (value >> (64 - shift))) & _MASK64

def _keccakf1600(state):
    for round_constant in _ROUND_CONSTANTS:
        column = [state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20] for x in range(5)]
        for x in range(5):
            delta = column[(x - 1) % 5] ^ _rotate_left_64(column[(x + 1) % 5], 1)
            for y in range(5):
                state[x + 5 * y] ^= delta
        moved = [0] * 25
        for x in range(5):
            for y in range(5):
                moved[y + 5 * ((2 * x + 3 * y) % 5)] = _rotate_left_64(state[x + 5 * y], _ROTATION_OFFSETS[x][y])
        for x in range(5):
            for y in range(5):
                state[x + 5 * y] = moved[x + 5 * y] ^ ((~moved[((x + 1) % 5) + 5 * y]) & moved[((x + 2) % 5) + 5 * y])
        state[0] ^= round_constant

def keccak256(payload):
    rate = 136
    state = [0] * 25
    offset = 0
    while offset + rate <= len(payload):
        block = payload[offset:offset + rate]
        for index in range(rate // 8):
            state[index] ^= int.from_bytes(block[index * 8:index * 8 + 8], "little")
        _keccakf1600(state)
        offset += rate
    block = bytearray(payload[offset:])
    block.append(0x01)
    while len(block) < rate:
        block.append(0)
    block[-1] |= 0x80
    for index in range(rate // 8):
        state[index] ^= int.from_bytes(block[index * 8:index * 8 + 8], "little")
    _keccakf1600(state)
    output = bytearray()
    while len(output) < 32:
        for index in range(rate // 8):
            output.extend(state[index].to_bytes(8, "little"))
            if len(output) >= 32:
                break
        if len(output) < 32:
            _keccakf1600(state)
    return bytes(output[:32])

def checksum_address(value):
    raw = str(value).lower().removeprefix("0x")
    if re.fullmatch(r"[0-9a-f]{40}", raw) is None:
        raise ValueError("address must contain exactly 20 hexadecimal bytes")
    digest = keccak256(raw.encode("ascii")).hex()
    return "0x" + "".join(ch.upper() if int(digest[i], 16) >= 8 else ch for i, ch in enumerate(raw))

def emit(payload, code=0):
    print(json.dumps(payload, sort_keys=True), flush=True)
    raise SystemExit(code)

def address(value, label):
    text = str(value or "").lower()
    if not text.startswith("0x"):
        text = "0x" + text
    if re.fullmatch(r"0x[0-9a-f]{40}", text) is None:
        raise ValueError(label + " is not an Ethereum address: " + repr(value))
    return text

mode = sys.argv[1]
node = sys.argv[2]
expected_chain_id = None if sys.argv[3] == "none" else int(sys.argv[3])
expected_validators = [address(item, "expected validator") for item in json.loads(sys.argv[4])]
quiet_seconds = float(sys.argv[5])
poll_seconds = float(sys.argv[6])
rpc_timeout = float(sys.argv[7])
RPC = "http://127.0.0.1:8545"

def rpc(method, params=None):
    body = json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":[] if params is None else params}, separators=(",", ":")).encode()
    req = urllib.request.Request(RPC, data=body, headers={"Content-Type":"application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=rpc_timeout) as response:
        payload = json.loads(response.read(1048576).decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("error") is not None or "result" not in payload:
        raise RuntimeError(method + " failed: " + repr(payload.get("error") if isinstance(payload, dict) else payload))
    return payload["result"]

def validators():
    result = rpc("qbft_getValidatorsByBlockNumber", ["latest"])
    if not isinstance(result, list):
        raise RuntimeError("validator response is not a list")
    return [address(item, "live validator") for item in result]

def pending_votes():
    result = rpc("qbft_getPendingVotes", [])
    if not isinstance(result, dict):
        raise RuntimeError("pending votes response is not an object")
    out = {}
    for key, value in result.items():
        if isinstance(value, bool):
            out[address(key, "pending vote target")] = value
    return out

def block_number():
    return int(str(rpc("eth_blockNumber", [])), 16)

def syncing():
    return rpc("eth_syncing", []) is not False

def satisfied_targets(pending, live):
    live_set = set(live)
    targets = []
    unsafe = []
    for addr, vote in sorted(pending.items()):
        if vote is True and addr in live_set:
            targets.append({"address": addr, "vote": vote, "reason": "add_vote_already_in_validator_set"})
        elif vote is False and addr not in live_set:
            targets.append({"address": addr, "vote": vote, "reason": "remove_vote_already_absent_from_validator_set"})
        else:
            unsafe.append({"address": addr, "vote": vote, "reason": "pending_vote_not_satisfied"})
    return targets, unsafe

def quiet_window(reference):
    deadline = time.monotonic() + quiet_seconds
    first_block = block_number()
    observations = [{"elapsed_seconds": 0, "validator_set": reference, "block_number": first_block, "syncing": syncing()}]
    while time.monotonic() < deadline:
        time.sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))
        current = validators()
        current_block = block_number()
        node_syncing = syncing()
        observations.append({
            "elapsed_seconds": round(max(0.0, quiet_seconds - max(0.0, deadline - time.monotonic())), 3),
            "validator_set": current,
            "block_number": current_block,
            "syncing": node_syncing,
        })
        if sorted(current) != sorted(reference):
            return {"quiet": False, "reason": "validator_set_changed", "observations": observations}
    last = observations[-1]
    return {
        "quiet": last["block_number"] > first_block and last["syncing"] is False,
        "reason": "quiet_window_satisfied" if last["block_number"] > first_block and last["syncing"] is False else "quiet_window_without_block_progress",
        "observations": observations,
    }

try:
    chain_id = int(str(rpc("eth_chainId", [])), 16)
    live = validators()
    pending = pending_votes()
    peers = rpc("net_peerCount", [])
    current_block = rpc("eth_blockNumber", [])
    is_syncing = rpc("eth_syncing", [])
    if expected_chain_id is not None and chain_id != expected_chain_id:
        emit({"kind":"cleanup1.qbft_pending_vote_cleanup.v1","node":node,"status":"failed","error":"chain id mismatch","expected_chain_id":expected_chain_id,"chain_id":chain_id}, 2)
    if sorted(live) != sorted(expected_validators):
        emit({"kind":"cleanup1.qbft_pending_vote_cleanup.v1","node":node,"status":"failed","error":"live validator set does not match expected final validator set","expected_validator_set":expected_validators,"live_validator_set":live}, 2)
    targets, unsafe = satisfied_targets(pending, live)
    if unsafe:
        emit({"kind":"cleanup1.qbft_pending_vote_cleanup.v1","node":node,"status":"failed","error":"pending vote is not satisfied/stale","unsafe_pending_votes":unsafe,"pending_votes_before":pending}, 2)
    cleanup = {"pending_votes_before": pending, "cleanup_targets": targets, "cleared": [], "skipped": []}
    if mode == "execute" and targets:
        quiet = quiet_window(live)
        cleanup["quiet_observation"] = quiet
        if quiet.get("quiet") is not True:
            cleanup["status"] = "deferred_not_quiet"
            cleanup["pending_votes_after"] = pending_votes()
            emit({"kind":"cleanup1.qbft_pending_vote_cleanup.v1","node":node,"status":"failed","error":"quiet validator-set window was not satisfied","cleanup":cleanup}, 2)
        refreshed_live = validators()
        refreshed_pending = pending_votes()
        refreshed_targets, refreshed_unsafe = satisfied_targets(refreshed_pending, refreshed_live)
        if sorted(refreshed_live) != sorted(expected_validators) or refreshed_unsafe:
            emit({"kind":"cleanup1.qbft_pending_vote_cleanup.v1","node":node,"status":"failed","error":"pending vote state changed before discard","live_validator_set":refreshed_live,"pending_votes":refreshed_pending,"unsafe_pending_votes":refreshed_unsafe}, 2)
        for target in refreshed_targets:
            vote_address = checksum_address(target["address"])
            discard_result = rpc("qbft_discardValidatorVote", [vote_address])
            item = dict(target)
            item["discard_vote_address"] = vote_address
            item["discard_result"] = discard_result
            cleanup["cleared"].append(item)
        cleanup["pending_votes_after"] = pending_votes()
        cleanup["status"] = "cleared" if cleanup["cleared"] else "not_needed"
    else:
        cleanup["pending_votes_after"] = pending
        cleanup["status"] = "would_clear" if targets and mode != "execute" else "not_needed"
    emit({
        "kind":"cleanup1.qbft_pending_vote_cleanup.v1",
        "node":node,
        "mode":mode,
        "status":"pass",
        "chain_id":chain_id,
        "block_number":current_block,
        "syncing":is_syncing,
        "peer_count":peers,
        "live_validator_set":live,
        "cleanup":cleanup,
        "summary":{
            "cleanup_target_count":len(cleanup.get("cleanup_targets") or []),
            "cleared_count":len(cleanup.get("cleared") or []),
            "pending_vote_count_before":len(pending),
            "pending_vote_count_after":len(cleanup.get("pending_votes_after") or {}),
        },
    }, 0)
except Exception as exc:
    emit({"kind":"cleanup1.qbft_pending_vote_cleanup.v1","node":node,"status":"failed","error_code":type(exc).__name__,"error":str(exc)}, 2)
"""


def _cleanup1_json_from_stdout(stdout: str) -> dict[str, Any]:
    for line in reversed(str(stdout or "").splitlines()):
        text = line.strip()
        if not text:
            continue
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {
        "kind": "cleanup1.qbft_pending_vote_cleanup.v1",
        "status": "failed",
        "error_code": "MOTHER_POST_WORK_CLEANUP_V2_CLEANUP1_NO_JSON",
        "error": "cleanup1 did not emit a JSON result",
        "stdout": str(stdout or "")[-4000:],
    }


def _docker_besu_container_id(node: str, service_uuid: str, *, timeout: float) -> tuple[str | None, dict[str, Any]]:
    filter_value = f"name=^/{node}-{service_uuid}$"
    command = ["docker", "ps", "-q", "--filter", filter_value]
    started = _utc_now()
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=max(1.0, float(timeout)),
            check=False,
        )
    except FileNotFoundError as exc:
        raise MotherPostWorkCleanupV2Error(
            "MOTHER_POST_WORK_CLEANUP_V2_CLEANUP1_DOCKER_UNAVAILABLE",
            "docker CLI is not available for local cleanup1 execution",
        ) from exc
    detail = {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "started_at": started,
        "completed_at": _utc_now(),
    }
    if completed.returncode != 0:
        raise MotherPostWorkCleanupV2Error(
            "MOTHER_POST_WORK_CLEANUP_V2_CLEANUP1_DOCKER_PS_FAILED",
            "docker ps failed while locating local Besu container",
        )
    values = [line.strip() for line in str(completed.stdout or "").splitlines() if line.strip()]
    return (values[0] if values else None), detail


def _run_cleanup1_container(
    *,
    mode: str,
    node: str,
    container_id: str,
    final_validator_set: list[str],
    chain_id: int | None,
    quiet_seconds: float,
    poll_seconds: float,
    timeout: float,
) -> dict[str, Any]:
    command = [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "--network",
        f"container:{container_id}",
        "python:3.12-alpine",
        "python",
        "-",
        mode,
        node,
        "none" if chain_id is None else str(chain_id),
        json.dumps(final_validator_set, sort_keys=True),
        str(float(quiet_seconds)),
        str(float(poll_seconds)),
        str(float(timeout)),
    ]
    started = _utc_now()
    completed = subprocess.run(
        command,
        input=_cleanup1_python_source(),
        text=True,
        capture_output=True,
        timeout=max(10.0, float(timeout) + float(quiet_seconds) + float(poll_seconds) + 30.0),
        check=False,
    )
    payload = _cleanup1_json_from_stdout(completed.stdout)
    return {
        "container_id": container_id,
        "command": command[:8] + ["<cleanup1-python>"],
        "returncode": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
        "started_at": started,
        "completed_at": _utc_now(),
        "cleanup1": payload,
        "status": "pass" if completed.returncode == 0 and payload.get("status") == "pass" else "failed",
    }


def _run_local_cleanup1_step(
    *,
    mode: str,
    services: list[Mapping[str, Any]],
    final_validator_set: list[str],
    chain_id: int | None,
    quiet_seconds: float,
    poll_seconds: float,
    timeout: float,
) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    found_count = 0
    for service in services:
        node = _identifier(service.get("node"), "topology node")
        service_uuid = _identifier(service.get("service_uuid"), f"{node} service UUID")
        controller_id = str(service.get("controller_id") or "")
        container_id, lookup = _docker_besu_container_id(node, service_uuid, timeout=timeout)
        item: dict[str, Any] = {
            "node": node,
            "controller_id": controller_id,
            "service_uuid": service_uuid,
            "container_lookup": lookup,
        }
        if not container_id:
            item["status"] = "skipped"
            item["reason"] = "Besu container for this topology service is not present on the local Docker host"
            nodes.append(item)
            continue
        found_count += 1
        item["container_id"] = container_id
        try:
            item["result"] = _run_cleanup1_container(
                mode=mode,
                node=node,
                container_id=container_id,
                final_validator_set=final_validator_set,
                chain_id=chain_id,
                quiet_seconds=quiet_seconds,
                poll_seconds=poll_seconds,
                timeout=timeout,
            )
            item["status"] = item["result"]["status"]
        except Exception as exc:  # noqa: BLE001 - local cleanup1 must fail fast before service cleanup
            item["status"] = "failed"
            item["error_code"] = getattr(exc, "code", type(exc).__name__)
            item["error"] = str(exc)
        nodes.append(item)
    status = "pass" if found_count > 0 and all(item.get("status") in {"pass", "skipped"} for item in nodes) else "failed"
    failed = [item for item in nodes if item.get("status") == "failed"]
    return {
        "step": "cleanup1",
        "status": status,
        "mode": mode,
        "chain_cleanup_local_authority": True,
        "cleanup1_execution": "ephemeral-docker-helper",
        "docker_touched": found_count > 0,
        "besu_restarted": False,
        "coolify_touched": False,
        "compose_touched": False,
        "parent_redeploy_performed": False,
        "found_count": found_count,
        "skipped_count": sum(1 for item in nodes if item.get("status") == "skipped"),
        "failed_count": len(failed),
        "nodes": nodes,
        "failed_before_service_cleanup": bool(failed or found_count == 0),
    }


def _cleanup1_cleared_count(step: Mapping[str, Any]) -> int:
    total = 0
    for item in step.get("nodes") or []:
        if not isinstance(item, Mapping):
            continue
        result = item.get("result")
        if not isinstance(result, Mapping):
            continue
        payload = result.get("cleanup1")
        if not isinstance(payload, Mapping):
            continue
        summary = payload.get("summary")
        if isinstance(summary, Mapping):
            try:
                total += int(summary.get("cleared_count") or 0)
            except (TypeError, ValueError):
                pass
    return total

def _chain_step_error(
    name: str,
    exc: BaseException,
    *,
    resolved_rpc_urls: list[str] | None,
    rpc_sources: list[dict[str, Any]] | None,
    services: list[Mapping[str, Any]],
) -> dict[str, Any]:
    step = _step_error(name, None, exc)
    step["rpc_urls"] = list(resolved_rpc_urls or [])
    step["rpc_sources"] = list(rpc_sources or [])
    step["host_probe_commands"] = _host_chain_probe_commands(services)
    step["failed_before_service_cleanup"] = True
    return step


def _make_chain_args(
    *,
    mode: str,
    resolved_rpc_urls: list[str],
    final_validator_set: list[str],
    chain_id: int | None,
    quiet_seconds: float,
    poll_seconds: float,
    timeout: float,
    runtime_state_root: str | Path,
) -> argparse.Namespace:
    return argparse.Namespace(
        mode=mode,
        rpc_url=list(resolved_rpc_urls),
        committed_validator_address=final_validator_set,
        expected_chain_id=chain_id,
        phase="post-work-cleanup-v2",
        quiet_seconds=quiet_seconds,
        poll_seconds=poll_seconds,
        timeout=timeout,
        runtime_state_root=str(runtime_state_root),
        write_evidence=False,
        allow_discard_satisfied_pending_votes=(mode == "execute"),
        acknowledge_chain_cleanup_only=(mode == "execute"),
    )

def _run_chain_step(
    *,
    step_name: str,
    mode: str,
    runtime_state_root: str | Path,
    resolved_rpc_urls: list[str],
    rpc_sources: list[dict[str, Any]],
    final_validator_set: list[str],
    chain_id: int | None,
    quiet_seconds: float,
    poll_seconds: float,
    timeout: float,
    preflight: bool = False,
) -> dict[str, Any]:
    chain_mode = "inspect" if preflight else mode
    args = _make_chain_args(
        mode=chain_mode,
        resolved_rpc_urls=resolved_rpc_urls,
        final_validator_set=final_validator_set,
        chain_id=chain_id,
        quiet_seconds=quiet_seconds,
        poll_seconds=poll_seconds,
        timeout=timeout,
        runtime_state_root=runtime_state_root,
    )
    result = run_chain_cleanup(args)
    return {
        "step": step_name,
        "node": None,
        "status": _chain_cleanup_step_status(result, mode=chain_mode, preflight=preflight),
        "rpc_urls": list(resolved_rpc_urls),
        "rpc_sources": list(rpc_sources),
        "result": result,
    }





def _targeted_helper_recreate_plan(steps: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for step in steps:
        if step.get("step") not in {"admission-voter", "activation-guardian", "genesis-proof-guardian"}:
            continue
        result = step.get("result")
        if not isinstance(result, Mapping):
            continue
        if result.get("compose_touched") is not True and result.get("mutation_performed") is not True:
            continue
        node = str(step.get("node") or result.get("node") or "").strip()
        service_uuid = str(step.get("service_uuid") or result.get("service_uuid") or "").strip()
        target_name = str(step.get("target_name") or result.get("target_name") or "").strip()
        controller_id = str(step.get("controller_id") or result.get("controller_id") or "").strip()
        if not node or not service_uuid or not target_name:
            continue
        plan.append(
            {
                "node": node,
                "controller_id": controller_id,
                "service_uuid": service_uuid,
                "helper_service": target_name,
                "reason": "compose was patched without parent-stack redeploy; recreate this helper/shim service only on the target host",
            }
        )
    return plan




def run_post_work_cleanup_v2(
    private_state: PrivateStateReadResult,
    *,
    runtime_state_root: str | Path,
    network: str,
    topology_evidence: str | Path | None = None,
    acknowledged_topology_evidence_sha256: str | None = None,
    mode: str = "inspect",
    rpc_urls: list[str] | None = None,
    cleanup_all: bool = False,
    instant_deploy: bool = False,
    max_wait_seconds: float = 60.0,
    poll_interval_seconds: float = 5.0,
    timeout: float = 30.0,
    max_response_bytes: int = 12 * 1024 * 1024,
    chain_quiet_seconds: float = 35.0,
    chain_poll_seconds: float = 5.0,
    chain_timeout: float = 10.0,
    opener: Any = urllib.request.urlopen,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    mode_name = _identifier(mode, "mode")
    if mode_name not in {"inspect", "execute"}:
        raise MotherPostWorkCleanupV2Error(
            "MOTHER_POST_WORK_CLEANUP_V2_INVALID_ARGUMENT",
            "mode must be inspect or execute",
        )
    if instant_deploy:
        raise MotherPostWorkCleanupV2Error(
            "MOTHER_POST_WORK_CLEANUP_V2_PARENT_REDEPLOY_FORBIDDEN",
            "--instant-deploy is disabled for post-work cleanup v2 because Coolify applies it as a full parent-stack redeploy; patch Compose first, then recreate only the helper/shim services on the target host",
        )
    network_id = _identifier(network, "network")
    request_timeout = _positive(timeout, "timeout")
    response_limit = int(max_response_bytes)
    if response_limit <= 0:
        raise MotherPostWorkCleanupV2Error(
            "MOTHER_POST_WORK_CLEANUP_V2_INVALID_ARGUMENT",
            "max_response_bytes must be positive",
        )
    wait_limit = _nonnegative(max_wait_seconds, "max_wait_seconds")
    poll_interval = _nonnegative(poll_interval_seconds, "poll_interval_seconds")

    paths = MotherPaths(runtime_state_root=Path(runtime_state_root))
    accepted_topology = _validate_topology_evidence(
        paths,
        topology_evidence,
        acknowledged_sha256=acknowledged_topology_evidence_sha256,
        network=network_id,
    )
    services = accepted_topology["services"]
    admission_evidence = _source_admission_evidence(paths, accepted_topology["document"])
    voter_nodes = set(_voter_nodes_from_admission_evidence(admission_evidence))
    service_by_node = {item["node"]: item for item in services}

    resolved_rpc_urls: list[str] = []
    rpc_sources: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []

    def finish(status: str) -> dict[str, Any]:
        targeted_plan = _targeted_helper_recreate_plan(steps)
        chain_cleanup_steps = [step for step in steps if step.get("step") in {"chain-cleanup", "cleanup1"}]
        chain_touched = any(
            (
                isinstance(step.get("result"), Mapping) and _chain_cleared_count(step["result"]) > 0
            )
            or (step.get("step") == "cleanup1" and _cleanup1_cleared_count(step) > 0)
            for step in chain_cleanup_steps
        )
        service_steps = [
            step
            for step in steps
            if step.get("step") in {"admission-voter", "activation-guardian", "genesis-proof-guardian"}
        ]
        return {
            "kind": KIND,
            "observed_at": _utc_now(),
            "mode": mode_name,
            "status": status,
            "network": network_id,
            "topology_evidence": {
                "path": str(accepted_topology["path"]),
                "sha256": accepted_topology["sha256"],
                "discovered_from_disk": bool(accepted_topology.get("discovered")),
            },
            "source_validator_admission_evidence": (
                {
                    "path": str(admission_evidence["path"]),
                    "sha256": admission_evidence["sha256"],
                    "voter_nodes": sorted(voter_nodes),
                }
                if admission_evidence is not None
                else None
            ),
            "service_order": [
                {
                    "node": item["node"],
                    "controller_id": item["controller_id"],
                    "service_uuid": item["service_uuid"],
                }
                for item in services
            ],
            "step_order": [step.get("step") for step in steps],
            "steps": steps,
            "targeted_helper_recreate_plan": targeted_plan,
            "summary": {
                "clean": status == "pass",
                "complete": True,
                "chain_rpc_preflight_order": "first" if not cleanup_all else "not_used_with_cleanup1",
                "chain_cleanup_order": "before-service-cleanup",
                "cleanup_all": bool(cleanup_all),
                "cleanup1_execution": "ephemeral-docker-helper" if cleanup_all else "not_used",
                "service_cleanup_order": [
                    "admission-voter",
                    "activation-guardian",
                    "genesis-proof-guardian",
                ],
                "chain_rpc_preflight_passed": any(step.get("step") == "chain-rpc-preflight" and _step_success(step) for step in steps),
                "cleanup1_performed": any(step.get("step") == "cleanup1" for step in steps),
                "cleanup1_found_count": sum(int(step.get("found_count") or 0) for step in steps if step.get("step") == "cleanup1"),
                "cleanup1_skipped_count": sum(int(step.get("skipped_count") or 0) for step in steps if step.get("step") == "cleanup1"),
                "chain_cleanup_performed": any(step.get("step") in {"chain-cleanup", "cleanup1"} for step in steps),
                "chain_touched": chain_touched,
                "service_cleanup_started": bool(service_steps),
                "service_cleanup_performed": bool(service_steps),
                "coolify_parent_redeploy_allowed": False,
                "coolify_parent_redeploy_performed": False,
                "coolify_touched": any(
                    isinstance(step.get("result"), Mapping) and step["result"].get("coolify_touched") is True
                    for step in steps
                ),
                "docker_touched": any(step.get("docker_touched") is True for step in steps),
                "besu_restarted": False,
                "targeted_services": len(services),
                "admission_voter_target_nodes": sorted(voter_nodes),
                "targeted_helper_recreate_required": bool(targeted_plan),
                "targeted_helper_recreate_count": len(targeted_plan),
            },
        }

    if cleanup_all:
        steps.append(
            _run_local_cleanup1_step(
                mode=mode_name,
                services=services,
                final_validator_set=accepted_topology["final_validator_set"],
                chain_id=accepted_topology["chain_id"],
                quiet_seconds=chain_quiet_seconds,
                poll_seconds=chain_poll_seconds,
                timeout=chain_timeout,
            )
        )
        if not _step_success(steps[-1]):
            return finish("failed")
    else:
        try:
            if rpc_urls:
                resolved_rpc_urls = list(rpc_urls)
                rpc_sources = [
                    {"node": item.split("=", 1)[0], "rpc_url": item.split("=", 1)[1], "source": "cli-override"}
                    for item in resolved_rpc_urls
                    if "=" in item
                ]
            else:
                resolved_rpc_urls, rpc_sources = _derive_chain_rpc_urls(private_state, network=network_id, services=services)

            steps.append(
                _run_chain_step(
                    step_name="chain-rpc-preflight",
                    mode=mode_name,
                    runtime_state_root=runtime_state_root,
                    resolved_rpc_urls=resolved_rpc_urls,
                    rpc_sources=rpc_sources,
                    final_validator_set=accepted_topology["final_validator_set"],
                    chain_id=accepted_topology["chain_id"],
                    quiet_seconds=chain_quiet_seconds,
                    poll_seconds=chain_poll_seconds,
                    timeout=chain_timeout,
                    preflight=True,
                )
            )
            if not _step_success(steps[-1]):
                steps[-1]["failed_before_service_cleanup"] = True
                steps[-1]["host_probe_commands"] = _host_chain_probe_commands(services)
                return finish("failed")
        except Exception as exc:  # noqa: BLE001 - fail before touching service cleanup
            steps.append(
                _chain_step_error(
                    "chain-rpc-preflight",
                    exc,
                    resolved_rpc_urls=resolved_rpc_urls,
                    rpc_sources=rpc_sources,
                    services=services,
                )
            )
            return finish("failed")

        try:
            steps.append(
                _run_chain_step(
                    step_name="chain-cleanup",
                    mode=mode_name,
                    runtime_state_root=runtime_state_root,
                    resolved_rpc_urls=resolved_rpc_urls,
                    rpc_sources=rpc_sources,
                    final_validator_set=accepted_topology["final_validator_set"],
                    chain_id=accepted_topology["chain_id"],
                    quiet_seconds=chain_quiet_seconds,
                    poll_seconds=chain_poll_seconds,
                    timeout=chain_timeout,
                    preflight=False,
                )
            )
            if not _step_success(steps[-1]):
                steps[-1]["failed_before_service_cleanup"] = True
                steps[-1]["host_probe_commands"] = _host_chain_probe_commands(services)
                return finish("failed")
        except Exception as exc:  # noqa: BLE001 - fail before touching service cleanup
            steps.append(
                _chain_step_error(
                    "chain-cleanup",
                    exc,
                    resolved_rpc_urls=resolved_rpc_urls,
                    rpc_sources=rpc_sources,
                    services=services,
                )
            )
            return finish("failed")

    # Newest service cleanup first: retired admission-voter shim(s).
    if admission_evidence is None or not voter_nodes:
        steps.append(
            {
                "step": "admission-voter",
                "node": None,
                "status": "skipped",
                "reason": "topology evidence does not reference source validator-admission voter nodes",
            }
        )
    else:
        for node in [item["node"] for item in services if item["node"] in voter_nodes]:
            try:
                steps.append(
                    _call_service_cleanup(
                        mode=mode_name,
                        cleanup_name="admission-voter",
                        private_state=private_state,
                        network=network_id,
                        service=service_by_node[node],
                        admission_evidence=admission_evidence,
                        instant_deploy=False,
                        max_wait_seconds=wait_limit,
                        poll_interval_seconds=poll_interval,
                        timeout=request_timeout,
                        max_response_bytes=response_limit,
                        opener=opener,
                        progress=progress,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                steps.append(_step_error("admission-voter", node, exc))

    # Then the newer general service shim.
    for service in services:
        try:
            steps.append(
                _call_service_cleanup(
                    mode=mode_name,
                    cleanup_name="activation-guardian",
                    private_state=private_state,
                    network=network_id,
                    service=service,
                    instant_deploy=False,
                    max_wait_seconds=wait_limit,
                    poll_interval_seconds=poll_interval,
                    timeout=request_timeout,
                    max_response_bytes=response_limit,
                    opener=opener,
                    progress=progress,
                )
            )
        except Exception as exc:  # noqa: BLE001
            steps.append(_step_error("activation-guardian", service.get("node"), exc))

    # Then the already-tested genesis proof guardian shim.
    for service in services:
        try:
            steps.append(
                _call_service_cleanup(
                    mode=mode_name,
                    cleanup_name="genesis-proof-guardian",
                    private_state=private_state,
                    network=network_id,
                    service=service,
                    instant_deploy=False,
                    max_wait_seconds=wait_limit,
                    poll_interval_seconds=poll_interval,
                    timeout=request_timeout,
                    max_response_bytes=response_limit,
                    opener=opener,
                    progress=progress,
                )
            )
        except Exception as exc:  # noqa: BLE001
            steps.append(_step_error("genesis-proof-guardian", service.get("node"), exc))

    clean = all(_step_success(step) for step in steps)
    return finish("pass" if clean else "manual-review-required")





def _write_evidence(runtime_state_root: str | Path, result: Mapping[str, Any]) -> Path:
    root = Path(runtime_state_root) / "mother" / "evidence" / EVIDENCE_SUBDIR
    root.mkdir(parents=True, exist_ok=True)
    stamp = _utc_now().replace("-", "").replace(":", "")
    network = str(result.get("network") or "unknown-network")
    path = root / f"{stamp}-{network}.json"
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Post-work cleanup v2 orchestrator: chain preflight/cleanup first, then admission voter, activation guardian, genesis guardian."
    )
    parser.add_argument("mode", choices=("inspect", "execute"))
    parser.add_argument("--runtime-state-root", required=True)
    parser.add_argument("--network", required=True)
    parser.add_argument("--topology-evidence", help="optional; defaults to latest passed clean current post-admission topology evidence on disk")
    parser.add_argument("--acknowledge-topology-evidence-sha256")
    parser.add_argument("--rpc-url", action="append", default=[], help="optional override; defaults to RPC URLs derived from topology/private state")
    parser.add_argument("--cleanup-all", action="store_true", help="run chain cleanup through local ephemeral cleanup1 Docker helper instead of cross-host RPC")
    parser.add_argument("--instant-deploy", action="store_true", help="forbidden in v2; retained only to fail safely before touching anything")
    parser.add_argument("--max-wait-seconds", type=float, default=60.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-response-bytes", type=int, default=12 * 1024 * 1024)
    parser.add_argument("--chain-quiet-seconds", type=float, default=35.0)
    parser.add_argument("--chain-poll-seconds", type=float, default=5.0)
    parser.add_argument("--chain-timeout", type=float, default=10.0)
    parser.add_argument("--write-evidence", action="store_true")
    parser.add_argument("--quiet-progress", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    progress = _stderr_progress(args.quiet_progress)
    try:
        private_state = _load_private_state(args.runtime_state_root, network=args.network, mode=args.mode)
        result = run_post_work_cleanup_v2(
            private_state,
            runtime_state_root=args.runtime_state_root,
            network=args.network,
            topology_evidence=args.topology_evidence,
            acknowledged_topology_evidence_sha256=args.acknowledge_topology_evidence_sha256,
            mode=args.mode,
            rpc_urls=list(args.rpc_url or []),
            cleanup_all=args.cleanup_all,
            instant_deploy=args.instant_deploy,
            max_wait_seconds=args.max_wait_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
            chain_quiet_seconds=args.chain_quiet_seconds,
            chain_poll_seconds=args.chain_poll_seconds,
            chain_timeout=args.chain_timeout,
            progress=progress,
        )
        if args.write_evidence:
            result = dict(result)
            evidence_path = _write_evidence(args.runtime_state_root, result)
            result["evidence_path"] = str(evidence_path)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("status") == "pass" else 2
    except MotherPostWorkCleanupV2Error as exc:
        print(
            json.dumps(
                {
                    "kind": KIND,
                    "observed_at": _utc_now(),
                    "status": "failed",
                    "error_code": exc.code,
                    "error": str(exc),
                },
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
