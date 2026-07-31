"""Explicit expiring release for the exact A-side first-genesis deployment.

The release binds one canonical genesis transaction to one initial-node service
update and one deploy request.  It is secret-free, performs no network access,
and deliberately excludes every soft-admission target.
"""

from __future__ import annotations

import base64
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
from typing import Any
import urllib.parse

from . import atomic_files
from .canonical import canonical_json
from .deployment_genesis import verify_deployment_genesis_transaction
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_RELEASE_KIND = "main_computer.mother.deployment_genesis_release.v1"
_RELEASE_DIRECTORY = ("actions", "deployment-genesis-releases")
_TRANSACTION_DIRECTORY = ("actions", "deployment-genesis-transactions")
_MIN_RELEASE_SECONDS = 30
_MAX_RELEASE_SECONDS = 900
_BESU_IMAGE = "hyperledger/besu:latest"
_INIT_IMAGE = "alpine:3.20"


class MotherDeploymentGenesisReleaseError(RuntimeError):
    """A first-genesis release failed closed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _identifier(value: Any, path: str) -> str:
    if type(value) is not str or not value.strip():
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_INVALID", f"{path} must be a non-empty string"
        )
    text = value.strip()
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if text in {".", ".."} or any(character not in allowed for character in text):
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_INVALID", f"{path} is not a safe identifier"
        )
    return text


def _sha256(value: Any, path: str) -> str:
    if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_INVALID", f"{path} must be a lowercase SHA-256 digest"
        )
    return value


def _parse_utc(value: Any, path: str) -> datetime:
    if type(value) is not str or not value:
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_INVALID", f"{path} must be a UTC timestamp"
        )
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_INVALID", f"{path} is malformed"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_INVALID", f"{path} must be UTC"
        )
    return parsed.astimezone(timezone.utc)


def _utc_timestamp(value: Any, path: str) -> str:
    parsed = datetime.now(timezone.utc) if value is None else _parse_utc(value, path)
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _duration(value: Any) -> int:
    if type(value) is not int or isinstance(value, bool):
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_INVALID", "expires_in_seconds must be an integer"
        )
    if value < _MIN_RELEASE_SECONDS or value > _MAX_RELEASE_SECONDS:
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_TTL_INVALID",
            f"expires_in_seconds must be between {_MIN_RELEASE_SECONDS} and {_MAX_RELEASE_SECONDS}",
        )
    return value


def _contains_sensitive_key(value: Any) -> bool:
    forbidden = {
        "access_token", "api_token", "credential", "mnemonic", "password",
        "private_key", "refresh_token", "secret", "seed",
    }
    if isinstance(value, Mapping):
        return any(str(key).lower() in forbidden or _contains_sensitive_key(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def _digest_without(value: Mapping[str, Any], field: str) -> str:
    payload = dict(value)
    payload.pop(field, None)
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def _binding(private_state: PrivateStateReadResult) -> dict[str, Any]:
    return {
        "generation": private_state.binding.generation,
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


def _release_root(paths: PrivateStatePaths) -> Path:
    return paths.root / _RELEASE_DIRECTORY[0] / _RELEASE_DIRECTORY[1]


def _transaction_root(paths: PrivateStatePaths) -> Path:
    return paths.root / _TRANSACTION_DIRECTORY[0] / _TRANSACTION_DIRECTORY[1]


def _relative_locator(paths: PrivateStatePaths, candidate: Path, *, label: str) -> str:
    try:
        return Path(candidate).resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix()
    except ValueError as exc:
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_PATH_UNSAFE", f"{label} must be beneath the canonical Mother root"
        ) from exc


def _resolve_locator(paths: PrivateStatePaths, locator: Any, *, label: str) -> Path:
    if type(locator) is not str or not locator or "\\" in locator:
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_INVALID", f"{label} locator must be a relative POSIX path"
        )
    candidate = Path(locator)
    pure = PureWindowsPath(locator)
    if candidate.is_absolute() or pure.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_PATH_UNSAFE", f"{label} locator is unsafe"
        )
    resolved = (paths.root / candidate).resolve(strict=False)
    try:
        resolved.relative_to(paths.root.resolve(strict=False))
    except ValueError as exc:
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_PATH_UNSAFE", f"{label} locator escapes Mother state"
        ) from exc
    return resolved


def _load_canonical_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = Path(path).read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_INVALID", f"{label} could not be read as canonical JSON"
        ) from exc
    if type(value) is not dict or canonical_json(value) != raw:
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_INVALID", f"{label} is not canonical JSON"
        )
    return value, raw


def _initial_target(transaction: Mapping[str, Any]) -> dict[str, Any]:
    initial_node = _identifier(transaction.get("genesis", {}).get("initial_node"), "genesis.initial_node")
    targets = transaction.get("service_targets")
    if type(targets) is not list:
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_INVALID", "genesis service target set is missing"
        )
    matches = [item for item in targets if isinstance(item, Mapping) and item.get("node") == initial_node]
    if len(matches) != 1:
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_INVALID", "genesis transaction does not bind one initial target"
        )
    target = dict(matches[0])
    if not all([
        target.get("mode") == "initial",
        target.get("phase") == "install-mother-owned-first-genesis",
        target.get("role") == "initial-validator",
        target.get("service_start_authorized") is False,
        target.get("validator_activation_authorized") is False,
    ]):
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_INVALID", "initial target policy is malformed"
        )
    return target


def _first_genesis_compose(*, node: str, chain_id: int, genesis: Mapping[str, Any]) -> str:
    genesis_bytes = canonical_json(dict(genesis))
    encoded = base64.b64encode(genesis_bytes).decode("ascii")
    return "\n".join(
        [
            f"name: {node}",
            "",
            "services:",
            "  mother-genesis-init:",
            f"    image: {_INIT_IMAGE}",
            '    restart: "no"',
            "    environment:",
            '      MC_MOTHER_VALIDATOR_PRIVATE_KEY: "${MC_MOTHER_VALIDATOR_PRIVATE_KEY}"',
            "    volumes:",
            "      - mother-config:/config",
            "      - mother-data:/var/lib/besu",
            "    command:",
            "      - sh",
            "      - -ec",
            "      - |",
            "        umask 077",
            f"        printf '%s' '{encoded}' | base64 -d > /config/genesis.json",
            '        key="$${MC_MOTHER_VALIDATOR_PRIVATE_KEY#0x}"',
            '        test "$${#key}" -eq 64',
            '        printf \'%s\' "$${key}" > /config/nodekey',
            "        mkdir -p /var/lib/besu",
            "        chown -R 1000:1000 /config /var/lib/besu",
            "        chmod 0400 /config/nodekey",
            "        chmod 0444 /config/genesis.json",
            f"  {node}:",
            f"    image: {_BESU_IMAGE}",
            "    restart: unless-stopped",
            "    depends_on:",
            "      mother-genesis-init:",
            "        condition: service_completed_successfully",
            "    command:",
            "      - --data-path=/var/lib/besu",
            "      - --genesis-file=/config/genesis.json",
            "      - --node-private-key-file=/config/nodekey",
            f"      - --network-id={chain_id}",
            "      - --sync-mode=FULL",
            "      - --data-storage-format=BONSAI",
            "      - --p2p-enabled=true",
            "      - --p2p-port=30303",
            "      - --rpc-http-enabled=true",
            "      - --rpc-http-host=0.0.0.0",
            "      - --rpc-http-port=8545",
            "      - --rpc-http-api=ETH,NET,WEB3,QBFT,ADMIN",
            "      - --host-allowlist=localhost,127.0.0.1",
            "      - --min-gas-price=0",
            "    ports:",
            '      - "127.0.0.1:8545:8545/tcp"',
            '      - "30303:30303/tcp"',
            '      - "30303:30303/udp"',
            "    volumes:",
            "      - mother-config:/config:ro",
            "      - mother-data:/var/lib/besu",
            "    labels:",
            "      main_computer.mother.stage: first-genesis",
            f"      main_computer.mother.node: {node}",
            "",
            "volumes:",
            "  mother-config:",
            "  mother-data:",
            "",
        ]
    )


def _execution_plan(transaction: Mapping[str, Any]) -> dict[str, Any]:
    target = _initial_target(transaction)
    node = _identifier(target.get("node"), "initial target node")
    controller_id = _identifier(target.get("controller_id"), "initial target controller_id")
    service_uuid = _identifier(target.get("service_uuid"), "initial target service_uuid")
    genesis_block = transaction.get("genesis")
    if not isinstance(genesis_block, Mapping):
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_INVALID", "genesis document is missing"
        )
    genesis = genesis_block.get("canonical_json")
    if not isinstance(genesis, Mapping):
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_INVALID", "canonical genesis is missing"
        )
    chain_id = genesis_block.get("chain_id")
    if type(chain_id) is not int or chain_id <= 0:
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_INVALID", "genesis chain ID is invalid"
        )
    genesis_sha = _sha256(genesis_block.get("canonical_json_sha256"), "genesis SHA-256")
    if hashlib.sha256(canonical_json(dict(genesis))).hexdigest() != genesis_sha:
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_INVALID", "canonical genesis digest does not match"
        )
    compose = _first_genesis_compose(node=node, chain_id=chain_id, genesis=genesis)
    compose_bytes = compose.encode("utf-8")
    compose_sha = hashlib.sha256(compose_bytes).hexdigest()
    body = {
        "name": node,
        "docker_compose_raw": base64.b64encode(compose_bytes).decode("ascii"),
    }
    body_sha = hashlib.sha256(canonical_json(body)).hexdigest()
    encoded_uuid = urllib.parse.quote(service_uuid, safe="")
    return {
        "scope": "install-and-start-first-genesis-on-initial-node",
        "initial_node": node,
        "controller_id": controller_id,
        "service_uuid": service_uuid,
        "genesis_sha256": genesis_sha,
        "chain_id": chain_id,
        "compose": {
            "format": "docker-compose-yaml",
            "besu_image": _BESU_IMAGE,
            "init_image": _INIT_IMAGE,
            "sha256": compose_sha,
            "byte_length": len(compose_bytes),
            "canonical_text": compose,
            "contains_private_key_value": False,
        },
        "preconditions": [
            {
                "method": "GET",
                "endpoint": "/api/v1/services",
                "assertion": "service UUID and node name exist exactly once on the initial controller",
            },
            {
                "method": "GET",
                "endpoint": f"/api/v1/services/{encoded_uuid}/envs",
                "assertion": "both reserved identity environment keys exist exactly once",
                "required_keys": ["MC_MOTHER_HUB_ADMIN_PRIVATE_KEY", "MC_MOTHER_VALIDATOR_PRIVATE_KEY"],
            },
        ],
        "mutations": [
            {
                "ordinal": 1,
                "mutation_id": f"{node}.install-first-genesis-compose",
                "method": "PATCH",
                "endpoint": f"/api/v1/services/{encoded_uuid}",
                "canonical_request_body": body,
                "body_sha256": body_sha,
                "success_statuses": [200, 201, 202],
            },
            {
                "ordinal": 2,
                "mutation_id": f"{node}.deploy-first-genesis",
                "method": "GET",
                "endpoint": f"/api/v1/deploy?uuid={encoded_uuid}&force=true",
                "canonical_request_body": None,
                "body_sha256": None,
                "success_statuses": [200, 201, 202],
            },
        ],
        "excluded_targets": [
            {
                "node": item.get("node"),
                "controller_id": item.get("controller_id"),
                "service_uuid": item.get("service_uuid"),
                "reason": "soft replica admission requires an independently proven initial chain",
            }
            for item in transaction.get("service_targets", [])
            if isinstance(item, Mapping) and item.get("node") != node
        ],
        "postcondition_scope": "deployment-request-accepted-only",
        "initial_chain_proof_required_next": True,
    }


def build_deployment_genesis_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    transaction_path: Path,
    *,
    acknowledged_genesis_transaction_sha256: str,
    selected_nodes: Iterable[str] = (),
    expires_in_seconds: int = 300,
    created_at: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not isinstance(paths, PrivateStatePaths):
        raise TypeError("paths must be PrivateStatePaths")
    if not isinstance(private_state, PrivateStateReadResult):
        raise TypeError("private_state must be PrivateStateReadResult")
    acknowledged = _sha256(acknowledged_genesis_transaction_sha256, "acknowledged_genesis_transaction_sha256")
    lifetime = _duration(expires_in_seconds)
    reference_now = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    verified = verify_deployment_genesis_transaction(paths, private_state, Path(transaction_path))
    if acknowledged != verified["genesis_transaction_sha256"]:
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_ACKNOWLEDGEMENT_MISMATCH",
            "operator acknowledgement does not match the exact genesis transaction SHA-256",
        )
    candidate = Path(verified["transaction_path"])
    transaction, raw = _load_canonical_json(candidate, label="genesis transaction")
    plan = _execution_plan(transaction)
    requested = tuple(_identifier(item, "selected node") for item in selected_nodes)
    if requested and requested != (plan["initial_node"],):
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_SELECTION_MISMATCH",
            "first-genesis release may target only the exact initial node",
        )
    created_text = _utc_timestamp(created_at, "created_at")
    created = _parse_utc(created_text, "created_at")
    if created > reference_now + timedelta(seconds=1):
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_INVALID", "genesis release creation time is in the future"
        )
    expires_at = (created + timedelta(seconds=lifetime)).isoformat(timespec="seconds").replace("+00:00", "Z")
    release: dict[str, Any] = {
        "kind": _RELEASE_KIND,
        "schema_version": 1,
        "created_at": created_text,
        "expires_at": expires_at,
        "network": verified["network"],
        "mother_binding": _binding(private_state),
        "genesis_transaction": {
            "locator": _relative_locator(paths, candidate, label="genesis transaction"),
            "sha256": verified["genesis_transaction_sha256"],
            "byte_sha256": hashlib.sha256(raw).hexdigest(),
            "genesis_sha256": verified["genesis_sha256"],
        },
        "operator_release": {
            "intent": "install-and-start-exact-first-genesis-on-initial-node",
            "acknowledged_genesis_transaction_sha256": acknowledged,
            "requested_use_limit": 1,
            "expires_in_seconds": lifetime,
        },
        "execution_plan": plan,
        "authority": {
            "transaction_apply_authorized": True,
            "live_execution_authorized": False,
            "authorization_source": "explicit-operator-release",
        },
        "policy": {
            "network_access_performed": False,
            "live_mutation_performed": False,
            "secrets_in_output": False,
            "private_keys_materialized": False,
            "initial_node_only": True,
            "soft_replica_untouched": True,
            "automatic_rollback_authorized": False,
        },
        "resolved_blocker_codes": ["MOTHER_DEPLOY_GENESIS_RELEASE_REQUIRED"],
        "remaining_blockers": [
            {
                "code": "MOTHER_DEPLOY_GENESIS_EXECUTOR_NOT_IMPLEMENTED",
                "message": "the one-use first-genesis executor must consume this release",
            }
        ],
        "summary": {
            "release_valid": True,
            "target_count": 1,
            "mutation_count": 2,
            "initial_node": plan["initial_node"],
            "genesis_sha256": plan["genesis_sha256"],
            "compose_sha256": plan["compose"]["sha256"],
            "transaction_apply_authorized": True,
            "live_execution_authorized": False,
            "remaining_blocker_codes": ["MOTHER_DEPLOY_GENESIS_EXECUTOR_NOT_IMPLEMENTED"],
            "next_phase_after_apply": "prove-initial-chain-birth",
        },
    }
    if _contains_sensitive_key(release):
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_INVALID", "genesis release contains a sensitive field"
        )
    release["genesis_release_sha256"] = _digest_without(release, "genesis_release_sha256")
    return release


def write_deployment_genesis_release(
    paths: PrivateStatePaths,
    release: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    if not isinstance(paths, PrivateStatePaths):
        raise TypeError("paths must be PrivateStatePaths")
    if not isinstance(operation, OperationIdentity):
        raise TypeError("operation must be OperationIdentity")
    document = dict(release)
    digest = _digest_without(document, "genesis_release_sha256")
    if document.get("kind") != _RELEASE_KIND or document.get("genesis_release_sha256") != digest or _contains_sensitive_key(document):
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_INVALID", "genesis release is malformed, unbound, or sensitive"
        )
    payload = canonical_json(document)
    current = paths.root
    for part in _RELEASE_DIRECTORY:
        current = current / part
        atomic_files.ensure_durable_directory(current, operation=operation)
        _secure_private_path(current, is_directory=True, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("created_at", "")))[:32] or "genesisrelease"
    network = _identifier(document.get("network"), "network")
    destination = _release_root(paths) / f"{stamp}-{network}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise MotherDeploymentGenesisReleaseError(
                "MOTHER_DEPLOY_GENESIS_RELEASE_CONFLICT", "genesis release destination contains different bytes"
            )
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    if destination.read_bytes() != payload:
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_WRITE_FAILED", "genesis release reread mismatch"
        )
    return destination, digest


def verify_deployment_genesis_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    now: datetime | None = None,
) -> dict[str, Any]:
    if type(max_age_seconds) is not int or max_age_seconds <= 0:
        raise ValueError("max_age_seconds must be a positive integer")
    root = _release_root(paths).resolve(strict=False)
    candidate = Path(release_path).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_PATH_UNSAFE", "genesis release must be beneath the canonical release root"
        ) from exc
    release, raw = _load_canonical_json(candidate, label="genesis release")
    if release.get("kind") != _RELEASE_KIND or release.get("genesis_release_sha256") != _digest_without(release, "genesis_release_sha256") or _contains_sensitive_key(release):
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_INVALID", "genesis release is modified, unbound, or sensitive"
        )
    if release.get("mother_binding") != _binding(private_state):
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_STALE_BINDING", "genesis release does not bind the current Mother generation"
        )
    created = _parse_utc(release.get("created_at"), "created_at")
    expires = _parse_utc(release.get("expires_at"), "expires_at")
    lifetime = int((expires - created).total_seconds())
    _duration(lifetime)
    reference_now = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    if created > reference_now + timedelta(seconds=1):
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_INVALID", "genesis release creation time is in the future"
        )
    if reference_now > expires:
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_EXPIRED", "genesis release has expired"
        )
    if (reference_now - created).total_seconds() > max_age_seconds:
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_STALE_TIME", "genesis release is outside the permitted freshness window"
        )
    binding = release.get("genesis_transaction")
    if not isinstance(binding, Mapping):
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_INVALID", "genesis transaction binding is missing"
        )
    transaction_path = _resolve_locator(paths, binding.get("locator"), label="genesis transaction")
    try:
        transaction_path.relative_to(_transaction_root(paths).resolve(strict=False))
    except ValueError as exc:
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_PATH_UNSAFE", "bound genesis transaction is outside the canonical transaction root"
        ) from exc
    transaction_raw = transaction_path.read_bytes()
    if hashlib.sha256(transaction_raw).hexdigest() != binding.get("byte_sha256"):
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_TRANSACTION_MISMATCH", "bound genesis transaction bytes no longer match"
        )
    verified = verify_deployment_genesis_transaction(paths, private_state, transaction_path)
    if verified["genesis_transaction_sha256"] != binding.get("sha256") or verified["genesis_sha256"] != binding.get("genesis_sha256"):
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_TRANSACTION_MISMATCH", "bound genesis transaction digest no longer matches"
        )
    plan = release.get("execution_plan")
    if not isinstance(plan, Mapping):
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_INVALID", "execution plan is missing"
        )
    requested = tuple(_identifier(item, "selected node") for item in selected_nodes)
    if requested and requested != (plan.get("initial_node"),):
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_SELECTION_MISMATCH", "release does not cover the requested initial node"
        )
    rebuilt = build_deployment_genesis_release(
        paths,
        private_state,
        transaction_path,
        acknowledged_genesis_transaction_sha256=verified["genesis_transaction_sha256"],
        selected_nodes=(str(plan.get("initial_node")),),
        expires_in_seconds=lifetime,
        created_at=release.get("created_at"),
        now=reference_now,
    )
    if rebuilt != release:
        raise MotherDeploymentGenesisReleaseError(
            "MOTHER_DEPLOY_GENESIS_RELEASE_MISMATCH", "genesis release no longer matches the exact transaction and policy"
        )
    return {
        "clean": True,
        "release_path": str(candidate),
        "genesis_release_sha256": release["genesis_release_sha256"],
        "byte_sha256": hashlib.sha256(raw).hexdigest(),
        "created_at": release["created_at"],
        "expires_at": release["expires_at"],
        "mother_binding": dict(release["mother_binding"]),
        "network": release["network"],
        "nodes": [plan["initial_node"]],
        "initial_node": plan["initial_node"],
        "controller_id": plan["controller_id"],
        "service_uuid": plan["service_uuid"],
        "genesis_transaction_sha256": verified["genesis_transaction_sha256"],
        "genesis_sha256": plan["genesis_sha256"],
        "compose_sha256": plan["compose"]["sha256"],
        "mutation_count": len(plan["mutations"]),
        "transaction_apply_authorized": True,
        "live_execution_authorized": False,
        "remaining_blocker_codes": ["MOTHER_DEPLOY_GENESIS_EXECUTOR_NOT_IMPLEMENTED"],
        "network_access_performed": False,
        "live_mutation_performed": False,
        "staged_scope": plan["scope"],
    }


__all__ = [
    "MotherDeploymentGenesisReleaseError",
    "build_deployment_genesis_release",
    "verify_deployment_genesis_release",
    "write_deployment_genesis_release",
]
