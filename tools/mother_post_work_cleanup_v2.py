#!/usr/bin/env python3
"""Deterministic Mother post-work cleanup v2 cleanup1 orchestrator.

This script is cleanup1-only now.  It loads already-finalized topology evidence
and then runs one of these narrow paths:

1. cleanup1 local-authority QBFT vote cleanup, when ``--cleanup-all`` is used,
   by installing a temporary Coolify cleanup service on each target controller;
2. otherwise, chain RPC preflight and chain-only satisfied pending-vote cleanup
   through explicit/reachable RPC URLs.

Helper-service cleanup was removed from this orchestrator.  Helper mimic rewrite
and exact host-side helper reload belong to ``tools/mother_helper_cleanup2_yagni.py``.
This script must not run admission-voter, activation-guardian, or
genesis-proof-guardian helper cleanup, must not create helper-apply services, and
must not restart or redeploy parent stacks.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import base64
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Callable, Mapping
import urllib.parse
import urllib.request

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.mother.common.coolify_state import resolve_coolify_controller
from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import PrivateStateReadResult, read_private_state
from tools.mother.common.deployment_completed_helper_cleanup import (
    MotherDeploymentCompletedHelperCleanupError,
    _application_uuid,
    _controller_config,
    _http,
    _resolve_environment_uuid,
    _temporary_service_body,
    _wait_for_temporary_service_health,
)
from tools.mother.common.deployment_validator_routes import (
    MotherDeploymentValidatorRouteError,
    controller_validator_host,
    validator_route_from_record,
)
from tools.mother_chain_cleanup import run_chain_cleanup


KIND = "main_computer.mother.post_work_cleanup_v2.v1"
EVIDENCE_SUBDIR = "post-work-cleanup-v2"

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CLEANUP1_PREFIX = "mother-qbft-cleanup1"


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


def _marks_current_topology(summary: Mapping[str, Any]) -> bool:
    return (
        summary.get("topology_current") is True
        or summary.get("current_topology_marked_by_evidence") is True
    )


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
                and _marks_current_topology(summary)
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
        and _marks_current_topology(summary)
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


def _single_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _cleanup1_python_program() -> str:
    return r"""
import json
import os
import sys
import urllib.request

RPC = "http://127.0.0.1:8545"
expected_validators = {str(item).lower() for item in json.loads(os.environ["EXPECTED_VALIDATORS"])}
expected_chain_id = os.environ.get("EXPECTED_CHAIN_ID", "").strip()
mode = os.environ.get("CLEANUP1_MODE", "inspect")
node = os.environ.get("NODE", "")

def rpc(method, params=None):
    body = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": [] if params is None else params,
    }, separators=(",", ":")).encode()
    req = urllib.request.Request(
        RPC,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as response:
        payload = json.loads(response.read().decode())
    if "error" in payload:
        raise RuntimeError(f"{method} failed: {payload['error']}")
    return payload.get("result")

def fail(message, **extra):
    print(json.dumps({
        "status": "failed",
        "node": node,
        "error": message,
        **extra,
    }, sort_keys=True))
    raise SystemExit(2)

chain_id = rpc("eth_chainId")
if expected_chain_id:
    try:
        observed_chain_id = int(str(chain_id), 16)
    except ValueError:
        fail("invalid eth_chainId response", chain_id=chain_id)
    if observed_chain_id != int(expected_chain_id):
        fail("chain id mismatch", expected_chain_id=int(expected_chain_id), observed_chain_id=observed_chain_id)

block_number = rpc("eth_blockNumber")
syncing = rpc("eth_syncing")
peer_count = rpc("net_peerCount")
validators = [str(item).lower() for item in rpc("qbft_getValidatorsByBlockNumber", ["latest"])]
live_set = set(validators)
if live_set != expected_validators:
    fail(
        "live validator set does not match finalized topology",
        expected_validator_set=sorted(expected_validators),
        live_validator_set=validators,
    )

pending = rpc("qbft_getPendingVotes") or {}
if not isinstance(pending, dict):
    fail("pending votes response is not an object", pending_votes=pending)

unsafe = []
satisfied = []
for address, add_vote in pending.items():
    normalized = str(address).lower()
    vote_value = bool(add_vote)
    is_satisfied = (vote_value and normalized in live_set) or ((not vote_value) and normalized not in live_set)
    if is_satisfied:
        satisfied.append({"address": str(address), "add_vote": vote_value})
    else:
        unsafe.append({"address": str(address), "add_vote": vote_value})

if unsafe:
    fail("pending vote is not satisfied/stale", unsafe_pending_votes=unsafe, pending_votes=pending)

cleared = []
if mode == "execute":
    for item in satisfied:
        rpc("qbft_discardValidatorVote", [item["address"]])
        cleared.append(item)

final_pending = rpc("qbft_getPendingVotes") or {}
print(json.dumps({
    "status": "pass",
    "node": node,
    "mode": mode,
    "chain_id": chain_id,
    "block_number": block_number,
    "eth_syncing": syncing,
    "peer_count": peer_count,
    "validator_set": validators,
    "initial_pending_votes": pending,
    "satisfied_pending_votes": satisfied,
    "cleared_pending_votes": cleared,
    "cleared_count": len(cleared),
    "final_pending_votes": final_pending,
}, sort_keys=True))
"""


def _cleanup1_shell_script(
    *,
    mode: str,
    node: str,
    service_uuid: str,
    final_validator_set: list[str],
    chain_id: int | None,
) -> str:
    node_name = _identifier(node, "cleanup1 node")
    service = _identifier(service_uuid, "cleanup1 service UUID")
    expected_validators = json.dumps([str(item).lower() for item in final_validator_set], separators=(",", ":"))
    expected_chain_id = "" if chain_id is None else str(int(chain_id))
    python_program = _cleanup1_python_program()
    return "\n".join(
        [
            "set -eu",
            "proof_dir=/proof",
            "mkdir -p \"$proof_dir\"",
            "healthy=\"$proof_dir/healthy\"",
            "proof=\"$proof_dir/cleanup1-qbft-vote-cleanup.json\"",
            "failure=\"$proof_dir/cleanup1-qbft-vote-cleanup-failed.json\"",
            "rm -f \"$healthy\" \"$proof\" \"$failure\"",
            f"NODE={_single_quote(node_name)}",
            f"SERVICE_UUID={_single_quote(service)}",
            f"EXPECTED_VALIDATORS={_single_quote(expected_validators)}",
            f"EXPECTED_CHAIN_ID={_single_quote(expected_chain_id)}",
            f"CLEANUP1_MODE={_single_quote(_identifier(mode, 'cleanup1 mode'))}",
            "C=\"$(docker ps -q --filter \"name=^/${NODE}-${SERVICE_UUID}$\" | head -n1)\"",
            "if [ -z \"$C\" ]; then",
            "  printf '{\"status\":\"failed\",\"error\":\"Besu container not found\",\"node\":\"%s\",\"service_uuid\":\"%s\"}\\n' \"$NODE\" \"$SERVICE_UUID\" > \"$failure\"",
            "  sleep 300",
            "  exit 20",
            "fi",
            "cat > /tmp/cleanup1.py <<'PY'",
            python_program,
            "PY",
            "if docker run --rm --pull=never --network \"container:$C\" \\",
            "  -e NODE=\"$NODE\" \\",
            "  -e EXPECTED_VALIDATORS=\"$EXPECTED_VALIDATORS\" \\",
            "  -e EXPECTED_CHAIN_ID=\"$EXPECTED_CHAIN_ID\" \\",
            "  -e CLEANUP1_MODE=\"$CLEANUP1_MODE\" \\",
            "  python:3.12-alpine python /tmp/cleanup1.py > \"$proof.tmp\"; then",
            "  mv \"$proof.tmp\" \"$proof\"",
            "  touch \"$healthy\"",
            "else",
            "  rc=$?",
            "  if [ -s \"$proof.tmp\" ]; then mv \"$proof.tmp\" \"$failure\"; else printf '{\"status\":\"failed\",\"error\":\"cleanup1 docker runner failed\",\"exit_code\":%s}\\n' \"$rc\" > \"$failure\"; fi",
            "  sleep 300",
            "  exit \"$rc\"",
            "fi",
            "sleep 300",
        ]
    )


def _cleanup1_compose(
    *,
    service_name: str,
    mode: str,
    node: str,
    parent_service_uuid: str,
    final_validator_set: list[str],
    chain_id: int | None,
) -> str:
    name = _identifier(service_name, "cleanup1 service name")
    script = _cleanup1_shell_script(
        mode=mode,
        node=node,
        service_uuid=parent_service_uuid,
        final_validator_set=final_validator_set,
        chain_id=chain_id,
    )
    escaped_script = script.replace("$", "$$")
    indented = "\n".join("        " + line for line in escaped_script.splitlines())
    compose = "\n".join(
        [
            "services:",
            f"  {name}:",
            "    image: docker:27-cli",
            "    restart: \"no\"",
            "    read_only: true",
            "    network_mode: none",
            "    environment:",
            "      DOCKER_CONFIG: /proof/.docker",
            "      HOME: /proof",
            "      TMPDIR: /tmp",
            "    tmpfs:",
            "      - /tmp",
            "    command:",
            "      - sh",
            "      - -ec",
            "      - |",
            indented,
            "    healthcheck:",
            "      test:",
            "        - CMD",
            "        - sh",
            "        - -ec",
            "        - test -f /proof/healthy && test -f /proof/cleanup1-qbft-vote-cleanup.json",
            "      interval: 5s",
            "      timeout: 5s",
            "      retries: 24",
            "      start_period: 10s",
            "    volumes:",
            "      - /var/run/docker.sock:/var/run/docker.sock",
            "      - cleanup1-proof:/proof",
            "    labels:",
            f"      main_computer.mother.node: {node}",
            "      main_computer.mother.component: cleanup1-qbft-vote-cleanup",
            f"      main_computer.mother.target-service-uuid: {parent_service_uuid}",
            "volumes:",
            "  cleanup1-proof:",
            "",
        ]
    )
    parsed = yaml.safe_load(compose)
    if not isinstance(parsed, Mapping) or "services" not in parsed or name not in parsed["services"]:
        raise MotherPostWorkCleanupV2Error(
            "MOTHER_POST_WORK_CLEANUP_V2_CLEANUP1_COMPOSE_INVALID",
            "cleanup1 Compose is invalid",
        )
    if "$" in compose.replace("$$", ""):
        raise MotherPostWorkCleanupV2Error(
            "MOTHER_POST_WORK_CLEANUP_V2_CLEANUP1_COMPOSE_INVALID",
            "cleanup1 Compose contains an unescaped dollar interpolation",
        )
    if "/var/run/docker.sock:/var/run/docker.sock" not in compose:
        raise MotherPostWorkCleanupV2Error(
            "MOTHER_POST_WORK_CLEANUP_V2_CLEANUP1_COMPOSE_INVALID",
            "cleanup1 Compose does not mount the Docker socket",
        )
    return compose


def _run_cleanup1_coolify(
    private_state: PrivateStateReadResult,
    *,
    network: str,
    service: Mapping[str, str],
    final_validator_set: list[str],
    chain_id: int | None,
    mode: str,
    timeout: float,
    max_response_bytes: int,
    max_wait_seconds: float,
    poll_interval_seconds: float,
    opener: Any,
    progress: ProgressCallback | None,
) -> dict[str, Any]:
    node = _identifier(service["node"], "cleanup1 node")
    controller_id = _identifier(service["controller_id"], "cleanup1 controller_id")
    parent_service_uuid = _identifier(service["service_uuid"], "cleanup1 parent service UUID")
    _emit_progress(
        progress,
        "cleanup1",
        "installing temporary Coolify cleanup service",
        mode=mode,
        node=node,
        controller_id=controller_id,
        service_uuid=parent_service_uuid,
    )
    observations: list[dict[str, Any]] = []
    controller = resolve_coolify_controller(
        private_state,
        network,
        controller_id,
        require_enabled=True,
        require_token=True,
    )
    controller_config = _controller_config(private_state, network=network, controller_id=controller_id)
    service_name = f"{_CLEANUP1_PREFIX}-{parent_service_uuid[:8]}"
    cleanup_service_uuid: str | None = None
    create_receipt: dict[str, Any] | None = None
    start_receipt: dict[str, Any] | None = None
    health_result: dict[str, Any] | None = None
    delete_receipt: dict[str, Any] | None = None
    try:
        environment_uuid = _resolve_environment_uuid(
            controller=controller,
            controller_id=controller_id,
            endpoint=f"/api/v1/projects/{urllib.parse.quote(str(controller_config['project_uuid']), safe='')}/environments",
            expected_name="mainnet",
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            observations=observations,
        )
        compose = _cleanup1_compose(
            service_name=service_name,
            mode=mode,
            node=node,
            parent_service_uuid=parent_service_uuid,
            final_validator_set=final_validator_set,
            chain_id=chain_id,
        )
        body = _temporary_service_body(controller_config, service_name, compose)
        body["environment_uuid"] = environment_uuid
        body["description"] = "Ephemeral Mother cleanup1 QBFT satisfied pending-vote cleanup"
        body["instant_deploy"] = False
        create_response = _http(
            controller,
            "POST",
            "/api/v1/services",
            body=body,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        create_receipt = {
            "method": "POST",
            "endpoint": "/api/v1/services",
            "status": create_response["status"],
            "ok": create_response["ok"],
            "response_sha256": create_response["response_sha256"],
            "byte_length": create_response["byte_length"],
            "elapsed_ms": create_response["elapsed_ms"],
            "service_name": service_name,
            "request_body_sha256": hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "cleanup_scope": "cleanup1-qbft-vote-cleanup",
        }
        observations.append({key: create_receipt[key] for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")})
        if not create_response["ok"]:
            return {
                "step": "cleanup1",
                "node": node,
                "controller_id": controller_id,
                "service_uuid": parent_service_uuid,
                "status": "failed",
                "reason": "create-failed",
                "create": create_receipt,
                "start": None,
                "health": None,
                "delete": None,
                "observations": observations,
                "compose_touched": False,
                "coolify_touched": True,
                "docker_touched": start_receipt is not None and start_receipt.get("ok") is True,
                "parent_redeploy_performed": False,
                "besu_restarted": False,
            }
        cleanup_service_uuid = _application_uuid(create_response.get("payload"))
        create_receipt["service_uuid"] = cleanup_service_uuid
        start_endpoint = f"/api/v1/services/{urllib.parse.quote(cleanup_service_uuid, safe='')}/start"
        start_response = _http(
            controller,
            "POST",
            start_endpoint,
            body=None,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        start_receipt = {
            "method": "POST",
            "endpoint": start_endpoint,
            "status": start_response["status"],
            "ok": start_response["ok"],
            "response_sha256": start_response["response_sha256"],
            "byte_length": start_response["byte_length"],
            "elapsed_ms": start_response["elapsed_ms"],
            "service_uuid": cleanup_service_uuid,
            "service_name": service_name,
            "cleanup_scope": "cleanup1-qbft-vote-cleanup",
        }
        observations.append({key: start_receipt[key] for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")})
        if not start_response["ok"]:
            return {
                "step": "cleanup1",
                "node": node,
                "controller_id": controller_id,
                "service_uuid": parent_service_uuid,
                "cleanup_service_uuid": cleanup_service_uuid,
                "status": "failed",
                "reason": "start-failed",
                "create": create_receipt,
                "start": start_receipt,
                "health": None,
                "delete": None,
                "observations": observations,
                "compose_touched": False,
                "coolify_touched": True,
                "docker_touched": start_receipt is not None and start_receipt.get("ok") is True,
                "parent_redeploy_performed": False,
                "besu_restarted": False,
            }
        health_result = _wait_for_temporary_service_health(
            controller=controller,
            controller_id=controller_id,
            service_uuid=cleanup_service_uuid,
            service_name=service_name,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            max_wait_seconds=max_wait_seconds,
            poll_interval_seconds=poll_interval_seconds,
            opener=opener,
            observations=observations,
        )
        temporary_service_status = str(
            health_result.get("service_status") or health_result.get("final_status") or ""
        ).strip().lower()
        ok = health_result.get("healthy") is True or temporary_service_status == "exited"
        if ok and health_result.get("healthy") is not True and temporary_service_status == "exited":
            health_result = dict(health_result)
            health_result["reason"] = "temporary-service-exited"
        return {
            "step": "cleanup1",
            "node": node,
            "controller_id": controller_id,
            "service_uuid": parent_service_uuid,
            "cleanup_service_uuid": cleanup_service_uuid,
            "status": "pass" if ok else "failed",
            "reason": None if ok else health_result.get("reason", "health-failed"),
            "mode": mode,
            "cleanup1_execution": "temporary-coolify-service",
            "chain_cleanup_local_authority": True,
            "create": create_receipt,
            "start": start_receipt,
            "health": health_result,
            "delete": None,
            "observations": observations,
            "compose_touched": False,
            "coolify_touched": True,
            "docker_touched": start_receipt is not None and start_receipt.get("ok") is True,
            "parent_redeploy_performed": False,
            "besu_restarted": False,
        }
    finally:
        if cleanup_service_uuid is not None:
            endpoint = f"/api/v1/services/{urllib.parse.quote(cleanup_service_uuid, safe='')}"
            try:
                response = _http(
                    controller,
                    "DELETE",
                    endpoint,
                    body=None,
                    timeout=timeout,
                    max_response_bytes=max_response_bytes,
                    opener=opener,
                )
                delete_receipt = {
                    "method": "DELETE",
                    "endpoint": endpoint,
                    "status": response["status"],
                    "ok": response["ok"] or response["status"] in {404},
                    "response_sha256": response["response_sha256"],
                    "byte_length": response["byte_length"],
                    "elapsed_ms": response["elapsed_ms"],
                    "service_uuid": cleanup_service_uuid,
                    "service_name": service_name,
                    "cleanup_scope": "cleanup1-temporary-service-delete",
                }
                observations.append({key: delete_receipt[key] for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")})
                if health_result is not None:
                    health_result["temporary_service_delete"] = delete_receipt
            except Exception as exc:  # noqa: BLE001
                if health_result is not None:
                    health_result["temporary_service_delete"] = {
                        "ok": False,
                        "error_code": getattr(exc, "code", type(exc).__name__),
                        "error": str(exc),
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





def _helper_restart_manual_review_plan(steps: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for step in steps:
        if step.get("step") not in {"admission-voter", "activation-guardian", "genesis-proof-guardian"}:
            continue
        result = step.get("result")
        if not isinstance(result, Mapping):
            continue
        summary = result.get("summary")
        if isinstance(summary, Mapping) and summary.get("clean") is True:
            continue
        helper_restart = result.get("helper_restart")
        if not isinstance(helper_restart, Mapping):
            continue
        node = str(step.get("node") or result.get("node") or "").strip()
        service_uuid = str(step.get("service_uuid") or result.get("service_uuid") or "").strip()
        target_name = str(step.get("target_name") or result.get("target_name") or "").strip()
        controller_id = str(step.get("controller_id") or result.get("controller_id") or "").strip()
        target_application_uuid = str(result.get("target_application_uuid") or helper_restart.get("target_application_uuid") or "").strip()
        if not node or not service_uuid or not target_name:
            continue
        plan.append(
            {
                "node": node,
                "controller_id": controller_id,
                "service_uuid": service_uuid,
                "helper_service": target_name,
                "target_application_uuid": target_application_uuid,
                "reason": "existing helper mimic Compose rewrite was attempted but the helper child restart/start was not accepted",
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
    instant_deploy: bool = False,
    cleanup_all: bool = False,
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

    resolved_rpc_urls: list[str] = []
    rpc_sources: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []

    def finish(status: str) -> dict[str, Any]:
        helper_restart_plan = _helper_restart_manual_review_plan(steps)
        chain_cleanup_steps = [step for step in steps if step.get("step") == "chain-cleanup"]
        cleanup1_steps = [step for step in steps if step.get("step") == "cleanup1"]
        chain_touched = any(
            isinstance(step.get("result"), Mapping) and _chain_cleared_count(step["result"]) > 0
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
            "helper_restart_manual_review_plan": helper_restart_plan,
            "summary": {
                "clean": status == "pass",
                "complete": True,
                "chain_rpc_preflight_order": "not_used_with_cleanup1" if cleanup_all else "first",
                "chain_cleanup_order": "cleanup1-only" if cleanup_all else "chain-cleanup-only",
                "service_cleanup_order": [],
                "helper_cleanup_removed_from_v2": True,
                "helper_cleanup_replacement": "tools/mother_helper_cleanup2_yagni.py",
                "cleanup_all": bool(cleanup_all),
                "cleanup1_execution": "temporary-coolify-service" if cleanup_all else None,
                "cleanup1_performed": bool(cleanup1_steps),
                "cleanup1_passed": bool(cleanup1_steps) and all(_step_success(step) for step in cleanup1_steps),
                "chain_rpc_preflight_passed": any(step.get("step") == "chain-rpc-preflight" and _step_success(step) for step in steps),
                "chain_cleanup_performed": any(step.get("step") == "chain-cleanup" for step in steps) or bool(cleanup1_steps),
                "chain_touched": chain_touched,
                "service_cleanup_started": bool(service_steps),
                "service_cleanup_performed": bool(service_steps),
                "coolify_parent_redeploy_allowed": False,
                "coolify_parent_redeploy_performed": False,
                "coolify_touched": any(
                    step.get("coolify_touched") is True
                    or (isinstance(step.get("result"), Mapping) and step["result"].get("coolify_touched") is True)
                    for step in steps
                ),
                "docker_touched": any(
                    step.get("docker_touched") is True
                    or (isinstance(step.get("result"), Mapping) and step["result"].get("docker_touched") is True)
                    for step in steps
                ),
                "targeted_services": len(services),
                "admission_voter_target_nodes": sorted(voter_nodes),
                "helper_restart_manual_review_required": bool(helper_restart_plan),
                "helper_restart_manual_review_count": len(helper_restart_plan),
            },
        }

    if cleanup_all:
        if rpc_urls:
            steps.append(
                {
                    "step": "cleanup1",
                    "node": None,
                    "status": "failed",
                    "reason": "--rpc-url is not used with --cleanup-all; cleanup1 runs through temporary Coolify services on the target controllers",
                    "failed_before_service_cleanup": True,
                }
            )
            return finish("failed")
        for service in services:
            try:
                steps.append(
                    _run_cleanup1_coolify(
                        private_state,
                        network=network_id,
                        service=service,
                        final_validator_set=accepted_topology["final_validator_set"],
                        chain_id=accepted_topology["chain_id"],
                        mode=mode_name,
                        timeout=request_timeout,
                        max_response_bytes=response_limit,
                        max_wait_seconds=wait_limit,
                        poll_interval_seconds=poll_interval,
                        opener=opener,
                        progress=progress,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - fail before touching service cleanup
                failed = _step_error("cleanup1", service.get("node"), exc)
                failed["controller_id"] = service.get("controller_id")
                failed["service_uuid"] = service.get("service_uuid")
                failed["failed_before_service_cleanup"] = True
                steps.append(failed)
                return finish("failed")
            if not _step_success(steps[-1]):
                steps[-1]["failed_before_service_cleanup"] = True
                return finish("failed")
        return finish("pass")
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
        return finish("pass")



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
        description="Post-work cleanup v2 cleanup1-only orchestrator; helper cleanup is handled by mother_helper_cleanup2_yagni.py."
    )
    parser.add_argument("mode", choices=("inspect", "execute"))
    parser.add_argument("--runtime-state-root", required=True)
    parser.add_argument("--network", required=True)
    parser.add_argument("--topology-evidence", help="optional; defaults to latest passed clean current post-admission topology evidence on disk")
    parser.add_argument("--acknowledge-topology-evidence-sha256")
    parser.add_argument("--rpc-url", action="append", default=[], help="optional override; defaults to RPC URLs derived from topology/private state")
    parser.add_argument("--instant-deploy", action="store_true", help="forbidden in v2; retained only to fail safely before touching anything")
    parser.add_argument("--cleanup-all", action="store_true", help="run cleanup1 through temporary Coolify cleanup services and stop before helper cleanup")
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
            instant_deploy=args.instant_deploy,
            cleanup_all=args.cleanup_all,
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
