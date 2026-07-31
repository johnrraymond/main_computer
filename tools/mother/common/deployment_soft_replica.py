"""Offline compiler for the first soft replica configuration.

This boundary consumes fresh, canonical proof that the A-side first genesis is
healthy and compiles the exact C-side Besu configuration needed to join that
chain as a non-validator replica.  It performs no network access and grants no
startup or QBFT vote authority.
"""

from __future__ import annotations

import base64
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path, PureWindowsPath
import re
from typing import Any
from urllib.parse import quote, urlsplit

from . import atomic_files
from .canonical import canonical_json
from .coolify_state import resolve_coolify_controller
from .deployment_genesis import verify_deployment_genesis_transaction
from .deployment_genesis_birth import verify_genesis_birth_evidence
from .ethereum_identity import is_private_key
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_TRANSACTION_KIND = "main_computer.mother.deployment_soft_replica_transaction.v1"
_TRANSACTION_DIRECTORY = ("actions", "deployment-soft-replica-transactions")
_BIRTH_EVIDENCE_DIRECTORY = ("evidence", "deployment-genesis-birth")
_BIRTH_RELEASE_DIRECTORY = ("actions", "deployment-genesis-birth-releases")
_GENESIS_EXECUTION_DIRECTORY = ("actions", "deployment-genesis-executions")
_GENESIS_RELEASE_DIRECTORY = ("actions", "deployment-genesis-releases")
_GENESIS_TRANSACTION_DIRECTORY = ("actions", "deployment-genesis-transactions")
_BESU_IMAGE = "hyperledger/besu:latest"
_INIT_IMAGE = "alpine:3.20"


class MotherDeploymentSoftReplicaError(RuntimeError):
    """Soft-replica staging or verification failed closed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _identifier(value: Any, path: str) -> str:
    if type(value) is not str or not value.strip():
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_INVALID", f"{path} must be a non-empty string"
        )
    text = value.strip()
    if text in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9._-]+", text):
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_INVALID", f"{path} is not a safe identifier"
        )
    return text


def _sha256(value: Any, path: str) -> str:
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_INVALID", f"{path} must be a lowercase SHA-256 digest"
        )
    return value


def _parse_utc(value: Any, path: str) -> datetime:
    if type(value) is not str or not value:
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_INVALID", f"{path} must be a UTC timestamp"
        )
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_INVALID", f"{path} is malformed"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_INVALID", f"{path} must be UTC"
        )
    return parsed.astimezone(timezone.utc)


def _timestamp(value: str | None) -> str:
    parsed = datetime.now(timezone.utc) if value is None else _parse_utc(value, "created_at")
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _binding(private_state: PrivateStateReadResult) -> dict[str, Any]:
    return {
        "generation": private_state.binding.generation,
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


def _private_document(private_state: PrivateStateReadResult) -> dict[str, Any]:
    try:
        value = json.loads(private_state.canonical_object_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_STATE_INVALID", "Mother private state is not canonical JSON"
        ) from exc
    if type(value) is not dict:
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_STATE_INVALID", "Mother private state is not an object"
        )
    return value


def _contains_sensitive(value: Any) -> bool:
    forbidden = {
        "access_token", "api_token", "credential", "mnemonic", "password",
        "private_key", "refresh_token", "secret", "seed",
    }
    if isinstance(value, Mapping):
        return any(str(key).lower() in forbidden or _contains_sensitive(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_sensitive(item) for item in value)
    return False


def _resolve(paths: PrivateStatePaths, locator: Any, label: str) -> Path:
    if type(locator) is not str or not locator or "\\" in locator:
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_PATH_UNSAFE", f"{label} locator must be relative POSIX"
        )
    candidate = Path(locator)
    pure = PureWindowsPath(locator)
    if candidate.is_absolute() or pure.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_PATH_UNSAFE", f"{label} locator is unsafe"
        )
    result = (paths.root / candidate).resolve(strict=False)
    try:
        result.relative_to(paths.root.resolve(strict=False))
    except ValueError as exc:
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_PATH_UNSAFE", f"{label} locator escapes Mother state"
        ) from exc
    return result


def _relative(paths: PrivateStatePaths, path: Path, label: str) -> str:
    try:
        return path.resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix()
    except ValueError as exc:
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_PATH_UNSAFE", f"{label} is outside Mother state"
        ) from exc


def _canonical_under(
    paths: PrivateStatePaths,
    path: Path,
    directory: tuple[str, str],
    label: str,
) -> tuple[dict[str, Any], bytes, str]:
    expected = (paths.root / directory[0] / directory[1]).resolve(strict=False)
    candidate = path.resolve(strict=False)
    try:
        candidate.relative_to(expected)
    except ValueError as exc:
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_PATH_UNSAFE", f"{label} is outside its canonical directory"
        ) from exc
    try:
        raw = candidate.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_INVALID", f"{label} is not readable canonical JSON"
        ) from exc
    if type(value) is not dict or canonical_json(value) != raw:
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_INVALID", f"{label} is not canonical JSON"
        )
    return value, raw, hashlib.sha256(raw).hexdigest()


def _public_node_id(private_key: str) -> str:
    if not is_private_key(private_key):
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_STATE_INVALID", "initial validator private key is invalid"
        )
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
    except ImportError as exc:  # pragma: no cover
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_DEPENDENCY_MISSING",
            "cryptography is required to derive the public Besu node ID",
        ) from exc
    key = ec.derive_private_key(int(private_key[2:], 16), ec.SECP256K1())
    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return public[1:].hex()


def _advertised_host(base_url: str) -> str:
    parsed = urlsplit(base_url)
    host = parsed.hostname
    if not host:
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_CONTROLLER_INVALID", "initial controller URL has no hostname"
        )
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r"[A-Za-z0-9.-]+", host) or host.startswith(".") or host.endswith("."):
            raise MotherDeploymentSoftReplicaError(
                "MOTHER_DEPLOY_SOFT_REPLICA_CONTROLLER_INVALID", "initial controller hostname is unsafe"
            )
        return host.lower()
    return f"[{address.compressed}]" if address.version == 6 else address.compressed


def _trace_genesis_transaction(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    birth_evidence_path: Path,
    *,
    max_age_seconds: int,
) -> tuple[dict[str, Any], dict[str, Any], Path, str, dict[str, Any], Path, str]:
    verified_birth = verify_genesis_birth_evidence(
        paths,
        private_state,
        birth_evidence_path,
        selected_nodes=("mainneta-super1",),
        max_age_seconds=max_age_seconds,
    )
    evidence, _, evidence_sha = _canonical_under(
        paths, birth_evidence_path, _BIRTH_EVIDENCE_DIRECTORY, "genesis-birth evidence"
    )
    birth_release_ref = evidence.get("release")
    if not isinstance(birth_release_ref, Mapping):
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_CHAIN_INVALID", "birth evidence lacks birth-release binding"
        )
    birth_release_path = _resolve(paths, birth_release_ref.get("locator"), "genesis-birth release")
    birth_release, _, birth_release_sha = _canonical_under(
        paths, birth_release_path, _BIRTH_RELEASE_DIRECTORY, "genesis-birth release"
    )
    if _sha256(birth_release.get("release_sha256"), "birth release SHA-256") != _sha256(
        birth_release_ref.get("sha256"), "birth evidence release SHA-256"
    ):
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_CHAIN_INVALID", "birth release digest mismatch"
        )
    execution_ref = birth_release.get("genesis_execution")
    if not isinstance(execution_ref, Mapping):
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_CHAIN_INVALID", "birth release lacks genesis execution binding"
        )
    execution_path = _resolve(paths, execution_ref.get("locator"), "genesis execution")
    execution, _, execution_sha = _canonical_under(
        paths, execution_path, _GENESIS_EXECUTION_DIRECTORY, "genesis execution"
    )
    expected_execution_sha = _sha256(execution_ref.get("sha256"), "birth release execution SHA-256")
    if execution_sha != expected_execution_sha or evidence.get("genesis_execution_sha256") != expected_execution_sha:
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_CHAIN_INVALID", "birth evidence execution digest mismatch"
        )
    release_ref = execution.get("release")
    if not isinstance(release_ref, Mapping):
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_CHAIN_INVALID", "genesis execution lacks release binding"
        )
    release_path = _resolve(paths, release_ref.get("locator"), "genesis release")
    release, _, release_sha = _canonical_under(
        paths, release_path, _GENESIS_RELEASE_DIRECTORY, "genesis release"
    )
    if _sha256(release.get("genesis_release_sha256"), "genesis release SHA-256") != _sha256(
        release_ref.get("sha256"), "genesis execution release SHA-256"
    ):
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_CHAIN_INVALID", "genesis release digest mismatch"
        )
    transaction_ref = release.get("genesis_transaction")
    if not isinstance(transaction_ref, Mapping):
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_CHAIN_INVALID", "genesis release lacks transaction binding"
        )
    transaction_path = _resolve(paths, transaction_ref.get("locator"), "genesis transaction")
    verified_tx = verify_deployment_genesis_transaction(
        paths,
        private_state,
        transaction_path,
        selected_nodes=("mainneta-super1", "mainnetc-super1"),
    )
    transaction, _, transaction_sha = _canonical_under(
        paths, transaction_path, _GENESIS_TRANSACTION_DIRECTORY, "genesis transaction"
    )
    if transaction_sha != _sha256(transaction_ref.get("byte_sha256"), "genesis transaction byte SHA-256"):
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_CHAIN_INVALID", "genesis transaction byte digest mismatch"
        )
    if verified_tx["genesis_transaction_sha256"] != _sha256(
        transaction_ref.get("sha256"), "genesis transaction SHA-256"
    ):
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_CHAIN_INVALID", "genesis transaction digest mismatch"
        )
    return (
        verified_birth, evidence, birth_evidence_path.resolve(strict=False), evidence_sha,
        transaction, transaction_path.resolve(strict=False), transaction_sha,
    )


def _target(transaction: Mapping[str, Any], node: str) -> dict[str, Any]:
    targets = transaction.get("service_targets")
    if type(targets) is not list:
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_TRANSACTION_INVALID", "genesis service targets are missing"
        )
    matches = [item for item in targets if isinstance(item, Mapping) and item.get("node") == node]
    if len(matches) != 1:
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_TRANSACTION_INVALID", "soft replica target is not unique"
        )
    target = dict(matches[0])
    if not all([
        target.get("mode") == "soft",
        target.get("role") == "prospective-validator",
        target.get("phase") == "admit-replica-after-birth",
        target.get("service_start_authorized") is False,
        target.get("validator_activation_authorized") is False,
    ]):
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_TRANSACTION_INVALID", "soft replica target policy is malformed"
        )
    return target


def _replica_compose(
    *,
    node: str,
    chain_id: int,
    genesis: Mapping[str, Any],
    bootnode_enode: str,
) -> str:
    encoded_genesis = base64.b64encode(canonical_json(dict(genesis))).decode("ascii")
    return "\n".join([
        f"name: {node}",
        "",
        "services:",
        "  mother-replica-init:",
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
        f"        printf '%s' '{encoded_genesis}' | base64 -d > /config/genesis.json",
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
        "      mother-replica-init:",
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
        "      - --discovery-enabled=true",
        f"      - --bootnodes={bootnode_enode}",
        "      - --rpc-http-enabled=true",
        "      - --rpc-http-host=0.0.0.0",
        "      - --rpc-http-port=8545",
        "      - --rpc-http-api=ETH,NET,WEB3,QBFT,ADMIN",
        f"      - --host-allowlist=localhost,127.0.0.1,{node}",
        "      - --min-gas-price=0",
        "    ports:",
        '      - "30303:30303/tcp"',
        '      - "30303:30303/udp"',
        "    volumes:",
        "      - mother-config:/config:ro",
        "      - mother-data:/var/lib/besu",
        "    labels:",
        "      main_computer.mother.stage: soft-replica",
        f"      main_computer.mother.node: {node}",
        "      main_computer.mother.validator-activation: blocked",
        "",
        "volumes:",
        "  mother-config:",
        "  mother-data:",
        "",
    ])


def _digest_without(document: Mapping[str, Any], field: str) -> str:
    return hashlib.sha256(canonical_json({key: value for key, value in document.items() if key != field})).hexdigest()


def build_soft_replica_transaction(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    birth_evidence_path: Path,
    *,
    network: str = "mainnet",
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    created_at: str | None = None,
) -> dict[str, Any]:
    network = _identifier(network, "network")
    requested = tuple(_identifier(item, "selected node") for item in selected_nodes)
    if requested and requested != ("mainnetc-super1",):
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SELECTION_MISMATCH",
            "soft replica configuration may target only mainnetc-super1",
        )
    (
        verified_birth, birth_evidence, evidence_path, evidence_sha,
        genesis_tx, genesis_tx_path, genesis_tx_byte_sha,
    ) = _trace_genesis_transaction(
        paths,
        private_state,
        Path(birth_evidence_path),
        max_age_seconds=max_age_seconds,
    )
    if verified_birth["network"] != network:
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_NETWORK_MISMATCH", "birth evidence network does not match request"
        )
    genesis_block = genesis_tx.get("genesis")
    if not isinstance(genesis_block, Mapping) or not isinstance(genesis_block.get("canonical_json"), Mapping):
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_TRANSACTION_INVALID", "canonical genesis is missing"
        )
    genesis = dict(genesis_block["canonical_json"])
    genesis_sha = _sha256(genesis_block.get("canonical_json_sha256"), "genesis SHA-256")
    if hashlib.sha256(canonical_json(genesis)).hexdigest() != genesis_sha:
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_TRANSACTION_INVALID", "genesis commitment does not match"
        )
    if genesis_sha != verified_birth["genesis_sha256"]:
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_CHAIN_INVALID", "birth proof and genesis transaction disagree"
        )
    chain_id = genesis_block.get("chain_id")
    if type(chain_id) is not int or chain_id <= 0 or chain_id != verified_birth["chain_id"]:
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_CHAIN_INVALID", "chain ID is missing or inconsistent"
        )
    initial_node = _identifier(genesis_block.get("initial_node"), "initial node")
    if initial_node != "mainneta-super1":
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_CHAIN_INVALID", "starter soft-replica compiler requires mainneta-super1"
        )
    replica_node = "mainnetc-super1"
    target = _target(genesis_tx, replica_node)
    admission = target.get("admission")
    if not isinstance(admission, Mapping) or admission.get("requires_initial_chain_proof") is not True:
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_TRANSACTION_INVALID", "soft admission descriptor is missing"
        )
    current_validators = list(admission.get("current_validator_set") or [])
    if current_validators != list(verified_birth["validator_set"]):
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_VALIDATOR_SET_MISMATCH",
            "birth proof validator set does not match the soft admission prerequisite",
        )

    state = _private_document(private_state)
    try:
        initial_private_key = state["networks"][network]["validators"][initial_node]["private_key"]
    except (KeyError, TypeError) as exc:
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_STATE_INVALID", "initial validator identity is missing"
        ) from exc
    public_node_id = _public_node_id(initial_private_key)
    initial_controller = resolve_coolify_controller(
        private_state, network, "coolify-a", require_enabled=True, require_token=False
    )
    advertised_host = _advertised_host(initial_controller.base_url)
    bootnode_enode = f"enode://{public_node_id}@{advertised_host}:30303"

    compose = _replica_compose(
        node=replica_node,
        chain_id=chain_id,
        genesis=genesis,
        bootnode_enode=bootnode_enode,
    )
    compose_bytes = compose.encode("utf-8")
    compose_sha = hashlib.sha256(compose_bytes).hexdigest()
    service_uuid = _identifier(target.get("service_uuid"), "replica service UUID")
    controller_id = _identifier(target.get("controller_id"), "replica controller")
    if controller_id != "coolify-c":
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_TRANSACTION_INVALID", "soft replica is not bound to coolify-c"
        )
    body = {
        "name": replica_node,
        "docker_compose_raw": base64.b64encode(compose_bytes).decode("ascii"),
    }
    body_sha = hashlib.sha256(canonical_json(body)).hexdigest()
    created_text = _timestamp(created_at)
    created = _parse_utc(created_text, "created_at")
    if created > datetime.now(timezone.utc).replace(microsecond=0):
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_INVALID", "transaction creation time is in the future"
        )
    encoded_uuid = quote(service_uuid, safe="")
    transaction: dict[str, Any] = {
        "kind": _TRANSACTION_KIND,
        "schema_version": 1,
        "created_at": created_text,
        "network": network,
        "mother_binding": _binding(private_state),
        "staged_scope": "configure-soft-replica-without-validator-admission",
        "genesis_birth_evidence": {
            "locator": _relative(paths, evidence_path, "genesis-birth evidence"),
            "sha256": evidence_sha,
            "completed_at": birth_evidence.get("completed_at"),
            "initial_chain_proven": True,
        },
        "genesis_transaction": {
            "locator": _relative(paths, genesis_tx_path, "genesis transaction"),
            "sha256": _sha256(genesis_tx.get("genesis_transaction_sha256"), "genesis transaction SHA-256"),
            "byte_sha256": genesis_tx_byte_sha,
            "genesis_sha256": genesis_sha,
        },
        "initial_chain": {
            "node": initial_node,
            "controller_id": "coolify-a",
            "chain_id": chain_id,
            "validator_set": current_validators,
            "bootnode": {
                "enode": bootnode_enode,
                "node_id_sha256": hashlib.sha256(public_node_id.encode("ascii")).hexdigest(),
                "advertised_host": advertised_host,
                "p2p_port": 30303,
            },
        },
        "replica": {
            "node": replica_node,
            "mode": "soft",
            "role_before_admission": "non-validator-replica",
            "controller_id": controller_id,
            "service_uuid": service_uuid,
            "validator_address": str(target.get("validator_address")).lower(),
            "identity_commitments": dict(target.get("identity_commitments") or {}),
            "current_validator_set": current_validators,
            "desired_validator_set_after_later_vote": list(admission.get("desired_validator_set") or []),
            "compose": {
                "format": "docker-compose-yaml",
                "besu_image": _BESU_IMAGE,
                "init_image": _INIT_IMAGE,
                "canonical_text": compose,
                "sha256": compose_sha,
                "byte_length": len(compose_bytes),
                "contains_private_key_value": False,
                "host_rpc_mapping_present": False,
                "public_http_endpoint_created": False,
            },
        },
        "future_write_set": [
            {
                "ordinal": 1,
                "mutation_id": f"{replica_node}.install-soft-replica-compose",
                "controller_id": controller_id,
                "method": "PATCH",
                "endpoint": f"/api/v1/services/{encoded_uuid}",
                "canonical_request_body": body,
                "body_sha256": body_sha,
                "success_statuses": [200, 201, 202],
            },
            {
                "ordinal": 2,
                "mutation_id": f"{replica_node}.deploy-soft-replica",
                "controller_id": controller_id,
                "method": "GET",
                "endpoint": f"/api/v1/deploy?uuid={encoded_uuid}&force=true",
                "canonical_request_body": None,
                "body_sha256": None,
                "success_statuses": [200, 201, 202],
            },
        ],
        "authority": {
            "configuration_apply_authorized": False,
            "replica_start_authorized": False,
            "validator_vote_authorized": False,
            "validator_activation_authorized": False,
        },
        "policy": {
            "network_access_performed": False,
            "live_mutation_performed": False,
            "service_deploy_or_start_performed": False,
            "qbft_vote_performed": False,
            "initial_node_mutated": False,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "secrets_in_output": False,
            "manual_ssh_required": False,
            "public_http_endpoint_created": False,
        },
        "remaining_blockers": [
            {
                "code": "MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_REQUIRED",
                "message": "an explicit expiring operator release is required for this exact C-side configuration",
            },
            {
                "code": "MOTHER_DEPLOY_SOFT_REPLICA_EXECUTOR_NOT_IMPLEMENTED",
                "message": "the one-use C-side configuration executor is not implemented in this patch",
            },
            {
                "code": "MOTHER_DEPLOY_VALIDATOR_ADMISSION_NOT_AUTHORIZED",
                "message": "configuring and synchronizing C does not authorize a QBFT validator-addition vote",
            },
        ],
        "summary": {
            "transaction_valid": True,
            "target_count": 1,
            "future_mutation_count": 2,
            "initial_chain_proven": True,
            "replica_configured": False,
            "replica_started": False,
            "validator_vote_authorized": False,
            "persisted_secret_value_count": 0,
            "next_phase_after_apply": "prove-soft-replica-synchronization-before-validator-admission",
            "blocker_codes": [
                "MOTHER_DEPLOY_SOFT_REPLICA_EXECUTOR_NOT_IMPLEMENTED",
                "MOTHER_DEPLOY_SOFT_REPLICA_RELEASE_REQUIRED",
                "MOTHER_DEPLOY_VALIDATOR_ADMISSION_NOT_AUTHORIZED",
            ],
        },
    }
    if _contains_sensitive(transaction):
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_TRANSACTION_INVALID", "soft replica transaction contains sensitive material"
        )
    transaction["soft_replica_transaction_sha256"] = _digest_without(
        transaction, "soft_replica_transaction_sha256"
    )
    return transaction


def write_soft_replica_transaction(
    paths: PrivateStatePaths,
    transaction: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    document = dict(transaction)
    digest = _digest_without(document, "soft_replica_transaction_sha256")
    if (
        document.get("kind") != _TRANSACTION_KIND
        or document.get("soft_replica_transaction_sha256") != digest
        or _contains_sensitive(document)
    ):
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_TRANSACTION_INVALID", "soft replica transaction is malformed"
        )
    payload = canonical_json(document)
    current = paths.root
    for part in _TRANSACTION_DIRECTORY:
        current = current / part
        atomic_files.ensure_durable_directory(current, operation=operation)
        _secure_private_path(current, is_directory=True, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("created_at", "")))[:32] or "replica"
    network = _identifier(document.get("network"), "network")
    destination = current / f"{stamp}-{network}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise MotherDeploymentSoftReplicaError(
                "MOTHER_DEPLOY_SOFT_REPLICA_TRANSACTION_CONFLICT", "transaction destination contains different bytes"
            )
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    if destination.read_bytes() != payload:
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_TRANSACTION_WRITE_FAILED", "transaction verification after write failed"
        )
    return destination, digest


def verify_soft_replica_transaction(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    transaction_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    now: datetime | None = None,
) -> dict[str, Any]:
    document, raw, byte_sha = _canonical_under(
        paths, Path(transaction_path), _TRANSACTION_DIRECTORY, "soft replica transaction"
    )
    digest = _digest_without(document, "soft_replica_transaction_sha256")
    if (
        document.get("kind") != _TRANSACTION_KIND
        or document.get("soft_replica_transaction_sha256") != digest
        or document.get("mother_binding") != _binding(private_state)
        or _contains_sensitive(document)
    ):
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_TRANSACTION_INVALID", "soft replica transaction is invalid or stale"
        )
    requested = tuple(_identifier(item, "selected node") for item in selected_nodes)
    if requested and requested != ("mainnetc-super1",):
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SELECTION_MISMATCH", "soft replica transaction targets only mainnetc-super1"
        )
    created = _parse_utc(document.get("created_at"), "created_at")
    reference_now = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    age = int((reference_now - created).total_seconds())
    if age < -1 or age > max_age_seconds:
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_TRANSACTION_STALE", "soft replica transaction is outside the freshness window"
        )
    evidence_ref = document.get("genesis_birth_evidence")
    if not isinstance(evidence_ref, Mapping):
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_TRANSACTION_INVALID", "birth evidence binding is missing"
        )
    evidence_path = _resolve(paths, evidence_ref.get("locator"), "genesis-birth evidence")
    expected = build_soft_replica_transaction(
        paths,
        private_state,
        evidence_path,
        network=_identifier(document.get("network"), "network"),
        selected_nodes=("mainnetc-super1",),
        max_age_seconds=max_age_seconds,
        created_at=document.get("created_at"),
    )
    if canonical_json(expected) != raw:
        raise MotherDeploymentSoftReplicaError(
            "MOTHER_DEPLOY_SOFT_REPLICA_TRANSACTION_INVALID", "soft replica transaction no longer matches current inputs"
        )
    replica = document["replica"]
    initial = document["initial_chain"]
    return {
        "clean": True,
        "transaction_path": str(Path(transaction_path).resolve(strict=False)),
        "soft_replica_transaction_sha256": digest,
        "byte_sha256": byte_sha,
        "age_seconds": max(0, age),
        "mother_binding": dict(document["mother_binding"]),
        "network": document["network"],
        "nodes": [replica["node"]],
        "initial_node": initial["node"],
        "replica_node": replica["node"],
        "chain_id": initial["chain_id"],
        "genesis_sha256": document["genesis_transaction"]["genesis_sha256"],
        "bootnode_enode": initial["bootnode"]["enode"],
        "compose_sha256": replica["compose"]["sha256"],
        "future_mutation_count": len(document["future_write_set"]),
        "persisted_secret_value_count": 0,
        "configuration_apply_authorized": False,
        "replica_start_authorized": False,
        "validator_vote_authorized": False,
        "live_execution_authorized": False,
        "network_access_performed": False,
        "live_mutation_performed": False,
        "manual_ssh_required": False,
        "public_http_endpoint_created": False,
        "staged_scope": document["staged_scope"],
        "next_phase": "release-and-apply-soft-replica-configuration",
    }


__all__ = [
    "MotherDeploymentSoftReplicaError",
    "build_soft_replica_transaction",
    "verify_soft_replica_transaction",
    "write_soft_replica_transaction",
]
