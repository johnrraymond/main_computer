"""Local add-node identity reservation for missing Mother validator nodes.

This module intentionally performs no Coolify, chain, validator, or network
mutation.  It only extends the committed Mother private-state identity document
with a fully reserved node identity so the existing add-node prep flow can
resolve the requested validator address.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping

import yaml

from . import atomic_files
from .canonical import canonical_json
from .coolify_state import _DEFAULT_OPENER
from .deployment_topology_rectification import detect_topology_staleness

from .ethereum_identity import (
    generate_private_key,
    is_address,
    is_private_key,
    private_key_to_address,
    validate_identity,
)
from .models import OperationIdentity, PrivateStatePaths
from .private_state import (
    PrivateStateReadResult,
    prepare_private_state_successor,
    read_private_state,
    replace_verified_private_state,
)


_MOTHER_KIND = "main_computer.mother.private_state.v1"
_COMMAND_NAME = "tools/mother_deploy.py:add-node-reserve-identity"
_TOPOLOGY_REFRESH_KIND = "main_computer.mother.add_node_identity_reservation_topology_refresh_evidence.v1"
_TOPOLOGY_REFRESH_DIRECTORY = ("evidence", "deployment-node-add-post-admission-observe")
_TOPOLOGY_DISCOVERY_DIRECTORIES = (
    ("evidence", "deployment-node-add-post-admission-observe"),
    ("evidence", "deployment-node-remove-finalize"),
    ("evidence", "deployment-node-add-single-node-chain-and-hub-proof"),
    ("evidence", "deployment-live-topology-empty-rectification"),
)


class MotherDeploymentNodeIdentityReservationError(RuntimeError):
    """Add-node identity reservation failed closed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> MotherDeploymentNodeIdentityReservationError:
    return MotherDeploymentNodeIdentityReservationError(code, message)


def _identifier(value: Any, path: str) -> str:
    if type(value) is not str or not value:
        raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_INVALID", f"{path} must be a non-empty string")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if value in {".", ".."} or any(ch not in allowed for ch in value):
        raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_INVALID", f"{path} is not a safe identifier")
    return value


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_INVALID", f"{path} must be a mapping")
    return value


def _utc(value: str | None, path: str) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    if type(value) is not str or not value:
        raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_INVALID", f"{path} must be a UTC timestamp")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_INVALID", f"{path} is malformed") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_INVALID", f"{path} must be UTC")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _document(private_state: PrivateStateReadResult) -> dict[str, Any]:
    if not isinstance(private_state, PrivateStateReadResult):
        raise TypeError("private_state must be a PrivateStateReadResult")
    try:
        value = yaml.safe_load(private_state.document_bytes)
    except yaml.YAMLError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_PRIVATE_STATE_INVALID", "private state is not valid YAML") from exc
    if type(value) is not dict:
        raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_PRIVATE_STATE_INVALID", "private state must be a mapping")
    return value


def _binding(private_state: PrivateStateReadResult) -> dict[str, Any]:
    return {
        "private_state_kind": private_state.binding.private_state_kind,
        "generation": private_state.binding.generation,
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


def _binding_from_closure(closure: Any) -> dict[str, Any]:
    return {
        "private_state_kind": closure.binding.private_state_kind,
        "generation": closure.binding.generation,
        "content_sha256": closure.binding.content_hash.digest,
        "manifest_sha256": closure.binding.recovery_manifest_hash.digest,
    }


def _topology_binding_from_private_state(private_state: PrivateStateReadResult) -> dict[str, Any]:
    return {
        "generation": int(private_state.binding.generation),
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


def _topology_binding_from_full_binding(binding: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "generation": int(binding["generation"]),
        "content_sha256": str(binding["content_sha256"]),
        "manifest_sha256": str(binding["manifest_sha256"]),
    }


def _hash_digest_from_wire(value: Any, *, label: str) -> str:
    if not isinstance(value, Mapping) or value.get("algorithm") != "sha256" or not isinstance(value.get("digest"), str):
        raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_TOPOLOGY_INVALID", f"{label} hash is invalid")
    digest = str(value["digest"]).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_TOPOLOGY_INVALID", f"{label} hash digest is invalid")
    return digest


def _recovered_topology_bindings(private_state: PrivateStateReadResult) -> list[dict[str, Any]]:
    bindings: list[dict[str, Any]] = []
    for item in private_state.recovery_objects:
        match = re.fullmatch(r"predecessor/generation-(\d{8})/identity\.private\.meta\.json", item.relative_path)
        if not match:
            continue
        try:
            wire = json.loads(item.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_TOPOLOGY_INVALID", "predecessor private-state metadata is unreadable") from exc
        generation = int(wire.get("generation"))
        if generation != int(match.group(1)):
            raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_TOPOLOGY_INVALID", "predecessor private-state metadata generation is invalid")
        bindings.append(
            {
                "generation": generation,
                "content_sha256": _hash_digest_from_wire(wire.get("content_hash"), label="predecessor content"),
                "manifest_sha256": _hash_digest_from_wire(wire.get("recovery_manifest_hash"), label="predecessor recovery manifest"),
            }
        )
    bindings.sort(key=lambda item: int(item["generation"]), reverse=True)
    return bindings


def _topology_binding_key(binding: Mapping[str, Any]) -> tuple[int, str, str]:
    return (int(binding["generation"]), str(binding["content_sha256"]), str(binding["manifest_sha256"]))


def _accepted_topology_bindings(private_state: PrivateStateReadResult) -> list[dict[str, Any]]:
    seen: set[tuple[int, str, str]] = set()
    accepted: list[dict[str, Any]] = []
    for binding in [_topology_binding_from_private_state(private_state), *_recovered_topology_bindings(private_state)]:
        key = _topology_binding_key(binding)
        if key not in seen:
            accepted.append(binding)
            seen.add(key)
    return accepted


def _canonical_json_file(path: Path, *, label: str) -> tuple[dict[str, Any], bytes, str]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_TOPOLOGY_INVALID", f"cannot read {label}") from exc
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_TOPOLOGY_INVALID", f"{label} is not canonical JSON") from exc
    if not isinstance(document, dict):
        raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_TOPOLOGY_INVALID", f"{label} is not a JSON object")
    canonical = canonical_json(document)
    if canonical != payload:
        raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_TOPOLOGY_INVALID", f"{label} is not canonical JSON")
    return document, payload, hashlib.sha256(payload).hexdigest()


def _parse_completed_at(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _topology_evidence_sort_key(path: Path, document: Mapping[str, Any]) -> tuple[float, str]:
    completed = _parse_completed_at(document.get("completed_at"))
    timestamp = completed.timestamp() if completed is not None else path.stat().st_mtime
    return (timestamp, path.as_posix())


def _relative(paths: PrivateStatePaths, path: Path, *, label: str) -> str:
    try:
        return path.resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix()
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_TOPOLOGY_INVALID", f"{label} is outside Mother runtime state") from exc


def _topology_evidence_candidates(
    paths: PrivateStatePaths,
    *,
    network: str,
    accepted_topology_bindings: list[dict[str, Any]],
) -> list[tuple[int, Path, dict[str, Any], str]]:
    accepted = {_topology_binding_key(binding): index for index, binding in enumerate(accepted_topology_bindings)}
    candidates: list[tuple[int, Path, dict[str, Any], str]] = []
    for directory in _TOPOLOGY_DISCOVERY_DIRECTORIES:
        root = paths.root.joinpath(*directory)
        if not root.is_dir():
            continue
        for path in root.glob("*.json"):
            if not path.is_file():
                continue
            try:
                document, _payload, digest = _canonical_json_file(path, label="topology evidence")
            except MotherDeploymentNodeIdentityReservationError:
                continue
            if document.get("schema_version") != 1 or document.get("network") != network:
                continue
            binding = document.get("mother_binding")
            if not isinstance(binding, Mapping):
                continue
            try:
                priority = accepted[_topology_binding_key(binding)]
            except (KeyError, TypeError, ValueError):
                continue
            candidates.append((priority, path, document, digest))
    candidates.sort(key=lambda item: (item[0], -_topology_evidence_sort_key(item[1], item[2])[0], item[1].as_posix()))
    return candidates


def _select_latest_topology_evidence(
    paths: PrivateStatePaths,
    *,
    network: str,
    accepted_topology_bindings: list[dict[str, Any]],
    explicit_path: Path | None,
    acknowledged_sha256: str | None,
) -> tuple[Path | None, str | None, dict[str, Any] | None, str]:
    accepted = {_topology_binding_key(binding) for binding in accepted_topology_bindings}
    if explicit_path is not None:
        path = explicit_path.resolve(strict=False)
        document, _payload, digest = _canonical_json_file(path, label="topology evidence")
        if acknowledged_sha256 is not None and digest != _sha256(acknowledged_sha256, "topology evidence SHA-256"):
            raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_TOPOLOGY_ACK_MISMATCH", "topology evidence SHA-256 mismatch")
        if document.get("schema_version") != 1 or document.get("network") != network:
            raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_TOPOLOGY_INVALID", "topology evidence schema or network is invalid")
        binding = document.get("mother_binding")
        if not isinstance(binding, Mapping):
            raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_TOPOLOGY_INVALID", "topology evidence binding is missing")
        if _topology_binding_key(binding) not in accepted:
            raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_TOPOLOGY_BINDING_CHANGED", "topology evidence does not match the current or recoverable predecessor private-state binding")
        return path, digest, document, "explicit"

    candidates = _topology_evidence_candidates(
        paths,
        network=network,
        accepted_topology_bindings=accepted_topology_bindings,
    )
    if not candidates:
        if acknowledged_sha256 is not None:
            raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_TOPOLOGY_INVALID", "topology evidence SHA-256 was supplied but no topology evidence path was supplied")
        return None, None, None, "not-found"
    _priority, path, document, digest = candidates[0]
    return path, digest, document, "auto"


def _detect_topology_for_refresh(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    topology_evidence_path: Path,
    topology_evidence_sha: str,
    topology_evidence_document: Mapping[str, Any],
    *,
    network: str,
    current_topology_binding: Mapping[str, Any],
    topology_max_age_seconds: int,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    source_binding = topology_evidence_document.get("mother_binding")
    if source_binding == dict(current_topology_binding):
        return detect_topology_staleness(
            paths,
            private_state,
            topology_evidence_path,
            network=network,
            acknowledged_topology_evidence_sha256=topology_evidence_sha,
            max_age_seconds=topology_max_age_seconds,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )

    bridge_document = dict(topology_evidence_document)
    bridge_document["mother_binding"] = dict(current_topology_binding)
    bridge_payload = canonical_json(bridge_document)
    bridge_sha = hashlib.sha256(bridge_payload).hexdigest()
    safe_operation = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(private_state.binding.generation))[:32]
    bridge_dir = paths.root / "evidence" / "deployment-node-add-post-admission-observe"
    bridge_dir.mkdir(parents=True, exist_ok=True)
    bridge_path = bridge_dir / f".identity-reservation-topology-bridge-{safe_operation}-{bridge_sha[:16]}.json"
    bridge_path.write_bytes(bridge_payload)
    try:
        return detect_topology_staleness(
            paths,
            private_state,
            bridge_path,
            network=network,
            acknowledged_topology_evidence_sha256=bridge_sha,
            max_age_seconds=topology_max_age_seconds,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
    finally:
        try:
            bridge_path.unlink()
        except FileNotFoundError:
            pass


def _write_refreshed_topology_evidence(
    paths: PrivateStatePaths,
    *,
    network: str,
    node: str,
    host: str,
    generated_at: str,
    source_path: Path,
    source_sha256: str,
    source_document: Mapping[str, Any],
    detection: Mapping[str, Any],
    predecessor_binding: Mapping[str, Any],
    successor_topology_binding: Mapping[str, Any],
    operation: OperationIdentity,
) -> tuple[Path, str, dict[str, Any]]:
    summary = detection.get("summary")
    if not isinstance(summary, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_TOPOLOGY_INVALID", "topology detection summary is missing")
    if (
        detection.get("status") != "pass"
        or summary.get("clean") is not True
        or summary.get("topology_current") is not True
        or summary.get("manual_review_required") is True
        or summary.get("topology_stale") is True
    ):
        raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_TOPOLOGY_NOT_CURRENT", "latest topology evidence is not a clean current live topology")
    expected_nodes = list(detection.get("expected_nodes") or [])
    expected_validator_set = list(detection.get("expected_validator_set") or [])
    expected_services = dict(detection.get("expected_services") or {})
    if len(expected_nodes) != len(expected_validator_set):
        raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_TOPOLOGY_INVALID", "topology nodes and validators are not aligned")
    if set(expected_services) != set(expected_nodes):
        raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_TOPOLOGY_INVALID", "topology services are not aligned with nodes")
    completed = _utc(generated_at, "generated_at")
    topology = {
        "source": "add-node-identity-reservation-topology-refresh",
        "chain_id": detection.get("chain_id"),
        "genesis_sha256": detection.get("genesis_sha256"),
        "nodes": expected_nodes,
        "services": expected_services,
        "validator_count": len(expected_validator_set),
        "validator_set": expected_validator_set,
        "identity_catalog_refreshed": True,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
    }
    evidence: dict[str, Any] = {
        "kind": _TOPOLOGY_REFRESH_KIND,
        "schema_version": 1,
        "completed_at": completed,
        "observed_at": detection.get("observed_at"),
        "status": "pass",
        "failure": None,
        "mother_binding": dict(successor_topology_binding),
        "network": network,
        "mode": "add-node-identity-reservation-topology-refresh",
        "reserved_node": node,
        "reserved_host": host,
        "source_topology_evidence": {
            "path": str(source_path),
            "locator": _relative(paths, source_path, label="source topology evidence"),
            "sha256": source_sha256,
            "kind": source_document.get("kind"),
            "completed_at": source_document.get("completed_at"),
            "mother_binding": dict(predecessor_binding),
        },
        "predecessor_binding": dict(predecessor_binding),
        "successor_binding": dict(successor_topology_binding),
        "live_topology_observation": {
            "status": detection.get("status"),
            "observed_at": detection.get("observed_at"),
            "summary": dict(summary),
            "topology_evidence": dict(detection.get("topology_evidence") or {}),
        },
        "expected_service_observations": list(detection.get("expected_service_observations") or []),
        "observed_live_node_hints": list(detection.get("observed_live_node_hints") or []),
        "observed_service_hints": list(detection.get("observed_service_hints") or []),
        "target": detection.get("target"),
        "current_topology": topology,
        "final_topology": topology,
        "topology_diff": {
            "operation": "add-node-identity-reservation-topology-refresh",
            "added_nodes": [],
            "removed_nodes": [],
            "unchanged_nodes": expected_nodes,
            "pre_validator_count": len(expected_validator_set),
            "post_validator_count": len(expected_validator_set),
        },
        "authority": {
            "read_only_topology_refresh": True,
            "identity_reservation_previously_performed": True,
            "topology_current": True,
            "live_mutation_authorized": False,
        },
        "policy": {
            "allowed_http_methods": ["GET"],
            "coolify_control_plane_only": True,
            "manual_ssh_required": False,
            "network_access_performed": True,
            "live_mutation_performed": False,
            "finalize_mutation_performed": False,
            "chain_mutation_performed": False,
            "routing_or_topology_published": False,
            "public_http_endpoint_created": False,
            "public_endpoint_created": False,
            "validator_admission_performed": False,
            "validator_vote_performed": False,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "secrets_in_output": False,
        },
        "summary": {
            "clean": True,
            "complete": True,
            "topology_current": True,
            "topology_stale": False,
            "identity_catalog_refreshed": True,
            "refreshed_for_node": node,
            "refreshed_for_host": host,
            "current_nodes": expected_nodes,
            "current_validator_count": len(expected_validator_set),
            "network_access_performed": True,
            "live_mutation_performed": False,
            "routing_or_topology_published": False,
            "public_endpoint_created": False,
            "next_phase": f"add-node-prep-{network}",
        },
        "live_mutation_performed": False,
        "chain_mutation_performed": False,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
        "next_phase": f"add-node-prep-{network}",
    }
    payload = canonical_json(evidence)
    root = paths.root.joinpath(*_TOPOLOGY_REFRESH_DIRECTORY)
    atomic_files.ensure_durable_directory(root, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", completed) or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    preliminary = hashlib.sha256(payload).hexdigest()[:16]
    path = root / f"{stamp}-{network}-identity-refresh-{node}-{preliminary}.json"
    atomic_files.durable_create(path, payload, operation=operation)
    return path, hashlib.sha256(payload).hexdigest(), evidence




def _hub_key_path(network: str, node: str) -> str:
    return f"networks.{network}.node_seed_material.{node}.wallets.hub_admin.private_key"


def _target_shape(network: str, node: str, host: str, hub_address: str) -> dict[str, Any]:
    return {
        "controller_ref": f"networks.{network}.coolify.controllers.{host}",
        "desired_environment_name": network,
        "desired_service_name": node,
        "hub_admin_address": hub_address,
        "hub_admin_private_key_path": _hub_key_path(network, node),
        "key_material_status": "present",
        "live_resource_uuid": None,
        "status": "absent-awaiting-redeployment",
    }


def _node_shape(network: str, node: str, host: str) -> dict[str, Any]:
    return {
        "guard_route_reservation": f"{node}.guard",
        "host": host,
        "hub_route_reservation": f"{node}.hub",
        "rpc_route_reservation": f"{node}.rpc",
        "validator_ref": f"networks.{network}.validators.{node}",
    }


def _hub_seed_shape(node: str, generated_at: str, hub_key: str) -> dict[str, Any]:
    return {
        "wallets": {
            "hub_admin": {
                "address": private_key_to_address(hub_key),
                "metadata": {
                    "address_derivation": "secp256k1-keccak256-eip55",
                    "generated_at": generated_at,
                    "generated_by": _COMMAND_NAME,
                    "reason": f"Mother add-node {node} Hub administrator identity reservation",
                },
                "private_key": hub_key,
            }
        }
    }


def _validator_shape(private_key: str) -> dict[str, Any]:
    return {
        "address": private_key_to_address(private_key),
        "private_key": private_key,
    }


def _private_key(key_factory: Callable[[], str], path: str) -> str:
    try:
        key = key_factory()
    except Exception as exc:
        raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_SECRET_INVALID", f"{path} key factory failed") from exc
    if not is_private_key(key):
        raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_SECRET_INVALID", f"{path} key factory returned an invalid private key")
    return key


def _network_state(document: Mapping[str, Any], network: str) -> dict[str, Any]:
    if document.get("kind") != _MOTHER_KIND or document.get("schema_version") != 1:
        raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_PRIVATE_STATE_INVALID", "private state schema is invalid")
    networks = _mapping(document.get("networks"), "networks")
    return _mapping(networks.get(network), f"networks.{network}")


def _controller(network_state: Mapping[str, Any], host: str) -> dict[str, Any]:
    coolify = _mapping(network_state.get("coolify"), "coolify")
    controllers = _mapping(coolify.get("controllers"), "coolify.controllers")
    controller = _mapping(controllers.get(host), f"coolify.controllers.{host}")
    if controller.get("enabled") is False:
        raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_TARGET_INVALID", "target host is disabled")
    return controller


def _existing_hub_seed(seed_material: Mapping[str, Any], node: str) -> tuple[str, str] | None:
    node_seed = seed_material.get(node)
    if node_seed is None:
        return None
    node_seed = _mapping(node_seed, f"node_seed_material.{node}")
    wallets = _mapping(node_seed.get("wallets"), f"node_seed_material.{node}.wallets")
    hub = _mapping(wallets.get("hub_admin"), f"node_seed_material.{node}.wallets.hub_admin")
    private_key = hub.get("private_key")
    address = hub.get("address")
    if not is_private_key(private_key):
        raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_CONFLICT", "existing Hub admin seed has no valid private key")
    derived = private_key_to_address(private_key)
    if not is_address(address) or str(address).lower() != derived.lower():
        raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_CONFLICT", "existing Hub admin address does not match private key")
    return str(address), str(private_key)


def _existing_complete(
    network_state: Mapping[str, Any],
    *,
    network: str,
    node: str,
    host: str,
) -> dict[str, str] | None:
    deployment = _mapping(network_state.get("deployment"), "deployment")
    targets = _mapping(deployment.get("targets"), "deployment.targets")
    nodes = _mapping(network_state.get("nodes"), "nodes")
    validators = _mapping(network_state.get("validators"), "validators")
    seed_material = _mapping(network_state.get("node_seed_material"), "node_seed_material")

    target = targets.get(node)
    node_record = nodes.get(node)
    validator = validators.get(node)
    hub_seed = seed_material.get(node)
    present = [target is not None, node_record is not None, validator is not None, hub_seed is not None]
    if not any(present):
        return None
    if not all(present):
        raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_CONFLICT", "target identity is partially reserved; operator review required")

    target = _mapping(target, f"deployment.targets.{node}")
    node_record = _mapping(node_record, f"nodes.{node}")
    validator = _mapping(validator, f"validators.{node}")

    validate_identity(validator, path=f"validators.{node}")
    hub_address, _hub_key = _existing_hub_seed(seed_material, node)
    expected_target = _target_shape(network, node, host, hub_address)
    expected_node = _node_shape(network, node, host)
    if target != expected_target:
        raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_CONFLICT", "existing deployment target conflicts with requested node/host")
    if node_record != expected_node:
        raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_CONFLICT", "existing node reservation conflicts with requested node/host")
    return {
        "validator_address": validator["address"],
        "hub_admin_address": hub_address,
    }



def _repair_missing_deployment_target_identity(
    document: dict[str, Any],
    *,
    network: str,
    node: str,
    host: str,
) -> tuple[dict[str, Any], str, str] | None:
    """Repair the one deterministic partial identity cache shape.

    A previous interrupted/buggy reserve-identity run can leave the private
    state with the generated validator, node, and Hub-admin seed records but
    without the deployment target cache row.  That row is fully derivable from
    the existing Hub-admin seed plus the requested network/node/host, so repair
    it without generating or rotating any key material.  All other partial
    identity shapes remain ambiguous and fail closed.
    """

    result = deepcopy(document)
    network_state = _network_state(result, network)
    _controller(network_state, host)

    deployment = _mapping(network_state.get("deployment"), f"networks.{network}.deployment")
    targets = _mapping(deployment.setdefault("targets", {}), f"networks.{network}.deployment.targets")
    nodes = _mapping(network_state.get("nodes"), f"networks.{network}.nodes")
    validators = _mapping(network_state.get("validators"), f"networks.{network}.validators")
    seed_material = _mapping(network_state.get("node_seed_material"), f"networks.{network}.node_seed_material")

    target = targets.get(node)
    node_record = nodes.get(node)
    validator = validators.get(node)
    hub_seed = seed_material.get(node)
    if target is not None:
        return None
    if node_record is None or validator is None or hub_seed is None:
        return None

    node_record = _mapping(node_record, f"nodes.{node}")
    validator = _mapping(validator, f"validators.{node}")
    validate_identity(validator, path=f"validators.{node}")
    hub_address, _hub_key = _existing_hub_seed(seed_material, node)
    expected_node = _node_shape(network, node, host)
    if node_record != expected_node:
        raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_CONFLICT", "existing node reservation conflicts with requested node/host")

    targets[node] = _target_shape(network, node, host, hub_address)
    complete = _existing_complete(network_state, network=network, node=node, host=host)
    if complete is None:
        raise AssertionError("repaired identity did not read back")
    return result, str(validator["address"]), hub_address

def _add_missing_identity(
    document: dict[str, Any],
    *,
    network: str,
    node: str,
    host: str,
    generated_at: str,
    key_factory: Callable[[], str],
) -> tuple[dict[str, Any], str, str]:
    result = deepcopy(document)
    network_state = _network_state(result, network)
    _controller(network_state, host)

    deployment = _mapping(network_state.get("deployment"), f"networks.{network}.deployment")
    targets = _mapping(deployment.setdefault("targets", {}), f"networks.{network}.deployment.targets")
    nodes = _mapping(network_state.setdefault("nodes", {}), f"networks.{network}.nodes")
    validators = _mapping(network_state.setdefault("validators", {}), f"networks.{network}.validators")
    seed_material = _mapping(network_state.setdefault("node_seed_material", {}), f"networks.{network}.node_seed_material")

    # This function only handles the all-missing case.  Existing or partial
    # identities are validated by _existing_complete before this is called.
    if any(container.get(node) is not None for container in (targets, nodes, validators, seed_material)):
        raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_CONFLICT", "target identity appeared during reservation planning")

    validator_key = _private_key(key_factory, "validator")
    hub_key = _private_key(key_factory, "hub-admin")
    if validator_key == hub_key:
        raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_SECRET_INVALID", "validator and Hub admin identities must be distinct")

    validator = _validator_shape(validator_key)
    hub_seed = _hub_seed_shape(node, generated_at, hub_key)
    hub_address = hub_seed["wallets"]["hub_admin"]["address"]

    targets[node] = _target_shape(network, node, host, hub_address)
    nodes[node] = _node_shape(network, node, host)
    validators[node] = validator
    seed_material[node] = hub_seed
    deployment.setdefault("status", "identity-reserved-awaiting-executor")

    # Revalidate the resulting node by asking the complete-identity validator to
    # read it back from the generated document.
    complete = _existing_complete(network_state, network=network, node=node, host=host)
    if complete is None:
        raise AssertionError("generated identity did not read back")
    return result, validator["address"], hub_address


def reserve_add_node_identity(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    *,
    network: str,
    node: str,
    host: str,
    execute: bool,
    generated_at: str | None = None,
    operation: OperationIdentity,
    key_factory: Callable[[], str] = generate_private_key,
    refresh_topology_evidence: bool = False,
    topology_evidence_path: Path | None = None,
    acknowledged_topology_evidence_sha256: str | None = None,
    topology_max_age_seconds: int = 86400,
    timeout: float = 30.0,
    max_response_bytes: int = 4 * 1024 * 1024,
    opener: Any = _DEFAULT_OPENER,
) -> dict[str, Any]:
    """Ensure a requested add-node identity exists in Mother private state.

    When ``execute`` is false, this performs a deterministic dry-run planning
    pass and returns the successor binding that would be installed.  When true,
    it atomically advances private state exactly one generation for a missing
    identity.  Existing complete identities are treated as a no-op.
    """

    if not isinstance(paths, PrivateStatePaths):
        raise TypeError("paths must be PrivateStatePaths")
    if not isinstance(operation, OperationIdentity):
        raise TypeError("operation must be OperationIdentity")
    network = _identifier(network, "network")
    node = _identifier(node, "node")
    host = _identifier(host, "host")
    generated = _utc(generated_at, "generated_at")

    current_document = _document(private_state)
    network_state = _network_state(current_document, network)
    _controller(network_state, host)

    repaired_partial = False
    repair_document: dict[str, Any] | None = None
    repair_validator_address: str | None = None
    repair_hub_address: str | None = None
    try:
        existing = _existing_complete(network_state, network=network, node=node, host=host)
    except MotherDeploymentNodeIdentityReservationError as exc:
        if exc.code != "MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_CONFLICT":
            raise
        repaired = _repair_missing_deployment_target_identity(
            current_document,
            network=network,
            node=node,
            host=host,
        )
        if repaired is None:
            raise
        repair_document, repair_validator_address, repair_hub_address = repaired
        existing = None
        repaired_partial = True
    predecessor_binding = _binding(private_state)
    current_topology_binding = _topology_binding_from_private_state(private_state)
    accepted_topology_bindings = _accepted_topology_bindings(private_state)
    selected_topology_path: Path | None = None
    selected_topology_sha: str | None = None
    selected_topology_document: dict[str, Any] | None = None
    selected_topology_source = "disabled"
    topology_detection: dict[str, Any] | None = None
    selected_source_binding: dict[str, Any] | None = None
    if refresh_topology_evidence:
        selected_topology_path, selected_topology_sha, selected_topology_document, selected_topology_source = _select_latest_topology_evidence(
            paths,
            network=network,
            accepted_topology_bindings=accepted_topology_bindings,
            explicit_path=topology_evidence_path,
            acknowledged_sha256=acknowledged_topology_evidence_sha256,
        )
        if selected_topology_path is None or selected_topology_sha is None or selected_topology_document is None:
            raise _fail(
                "MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_TOPOLOGY_NOT_FOUND",
                "no predecessor-bound finalized topology evidence was found for refresh",
            )
        selected_source_binding = dict(selected_topology_document["mother_binding"])
        topology_detection = _detect_topology_for_refresh(
            paths,
            private_state,
            selected_topology_path,
            selected_topology_sha,
            selected_topology_document,
            network=network,
            current_topology_binding=current_topology_binding,
            topology_max_age_seconds=topology_max_age_seconds,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )

    if existing is not None:
        result = {
            "kind": "main_computer.mother.add_node_identity_reservation.v1",
            "schema_version": 1,
            "status": "pass",
            "network": network,
            "node": node,
            "host": host,
            "generated_at": generated,
            "predecessor_binding": predecessor_binding,
            "successor_binding": predecessor_binding,
            "identity_already_reserved": True,
            "partial_identity_repaired": False,
            "private_state_update_required": False,
            "private_state_updated": False,
            "validator_address": existing["validator_address"],
            "hub_admin_address": existing["hub_admin_address"],
            "generated_labels": [],
            "private_key_material_in_output": False,
            "network_access_performed": False,
            "live_mutation_performed": False,
            "next_phase": f"add-node-prep-{network}",
        }
        if refresh_topology_evidence:
            result["network_access_performed"] = True
            if selected_topology_path is not None and selected_topology_sha is not None and selected_topology_document is not None and topology_detection is not None:
                if not execute:
                    result["topology_refresh"] = {
                        "performed": False,
                        "source": selected_topology_source,
                        "source_evidence": {"path": str(selected_topology_path), "sha256": selected_topology_sha},
                        "private_state_binding_changed": False,
                        "write_skipped": "execute=false",
                    }
                else:
                    refreshed_path, refreshed_sha, _refreshed_document = _write_refreshed_topology_evidence(
                        paths,
                        network=network,
                        node=node,
                        host=host,
                        generated_at=generated,
                        source_path=selected_topology_path,
                        source_sha256=selected_topology_sha,
                        source_document=selected_topology_document,
                        detection=topology_detection,
                        predecessor_binding=selected_source_binding or current_topology_binding,
                        successor_topology_binding=current_topology_binding,
                        operation=operation,
                    )
                    result["topology_refresh"] = {
                        "performed": True,
                        "source": selected_topology_source,
                        "source_evidence": {"path": str(selected_topology_path), "sha256": selected_topology_sha},
                        "evidence": {"path": str(refreshed_path), "sha256": refreshed_sha},
                        "private_state_binding_changed": False,
                    }
                    result["refreshed_topology_evidence"] = str(refreshed_path)
                    result["refreshed_topology_evidence_sha256"] = refreshed_sha
        return result

    if repaired_partial:
        if repair_document is None or repair_validator_address is None or repair_hub_address is None:
            raise AssertionError("partial identity repair was selected without repair details")
        successor_document = repair_document
        validator_address = repair_validator_address
        hub_address = repair_hub_address
        generated_labels: list[str] = []
    else:
        successor_document, validator_address, hub_address = _add_missing_identity(
            current_document,
            network=network,
            node=node,
            host=host,
            generated_at=generated,
            key_factory=key_factory,
        )
        generated_labels = [f"validator:{node}", f"hub-admin:{node}"]
    closure = prepare_private_state_successor(
        private_state,
        successor_document,
        updated_at=generated,
        updated_by_action_id=operation.operation_id,
        operation=operation,
    )
    successor_binding = _binding_from_closure(closure)

    installed = False
    effective_successor_topology_binding = _topology_binding_from_full_binding(successor_binding)
    if execute:
        result = replace_verified_private_state(
            paths,
            closure,
            private_state.binding,
            operation=operation,
        )
        installed = bool(result.installed)
        if not installed:
            raise _fail("MOTHER_DEPLOY_NODE_IDENTITY_RESERVATION_CONFLICT", "private-state successor was not installed")
        # Re-read after installation so the refreshed topology evidence is bound
        # to the exact durable state subsequent add-node prep will load.
        refreshed_private_state = read_private_state(paths, operation=operation)
        effective_successor_topology_binding = _topology_binding_from_private_state(refreshed_private_state)

    result = {
        "kind": "main_computer.mother.add_node_identity_reservation.v1",
        "schema_version": 1,
        "status": "pass",
        "network": network,
        "node": node,
        "host": host,
        "generated_at": generated,
        "predecessor_binding": predecessor_binding,
        "successor_binding": successor_binding,
        "identity_already_reserved": False,
        "partial_identity_repaired": repaired_partial,
        "private_state_update_required": True,
        "private_state_updated": installed,
        "validator_address": validator_address,
        "hub_admin_address": hub_address,
        "generated_labels": generated_labels,
        "private_key_material_in_output": False,
        "network_access_performed": False,
        "live_mutation_performed": False,
        "next_phase": f"add-node-prep-{network}",
        "operator_note": "private-state binding changed; refreshed topology evidence was not requested",
    }
    if refresh_topology_evidence:
        if selected_topology_path is None or selected_topology_sha is None or selected_topology_document is None or topology_detection is None:
            raise AssertionError("topology refresh was requested but no topology evidence was selected")
        result["network_access_performed"] = True
        if not execute:
            result["topology_refresh"] = {
                "performed": False,
                "source": selected_topology_source,
                "source_evidence": {"path": str(selected_topology_path), "sha256": selected_topology_sha},
                "private_state_binding_changed": True,
                "write_skipped": "execute=false",
            }
            result["operator_note"] = "dry-run only; rerun with --execute to install private-state and write refreshed topology evidence"
        else:
            refreshed_path, refreshed_sha, _refreshed_document = _write_refreshed_topology_evidence(
                paths,
                network=network,
                node=node,
                host=host,
                generated_at=generated,
                source_path=selected_topology_path,
                source_sha256=selected_topology_sha,
                source_document=selected_topology_document,
                detection=topology_detection,
                predecessor_binding=selected_source_binding or current_topology_binding,
                successor_topology_binding=effective_successor_topology_binding,
                operation=operation,
            )
            result["topology_refresh"] = {
                "performed": True,
                "source": selected_topology_source,
                "source_evidence": {"path": str(selected_topology_path), "sha256": selected_topology_sha},
                "evidence": {"path": str(refreshed_path), "sha256": refreshed_sha},
                "private_state_binding_changed": True,
            }
            result["refreshed_topology_evidence"] = str(refreshed_path)
            result["refreshed_topology_evidence_sha256"] = refreshed_sha
            result["operator_note"] = "private-state binding changed; use refreshed_topology_evidence for add-node prep"
    return result


__all__ = [
    "MotherDeploymentNodeIdentityReservationError",
    "reserve_add_node_identity",
]
