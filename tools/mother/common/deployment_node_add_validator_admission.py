"""Generic add-node validator admission and activation for Mother.

This phase consumes clean ``deployment-node-add-replica-sync`` evidence and
performs the next live boundary only after an explicit expiring operator release:

* replace the target standby replica Compose with validator-activation Compose
  that uses the already-installed ``MC_MOTHER_VALIDATOR_PRIVATE_KEY`` env value;
* install internal-only voting guardians on every existing validator service;
* have the existing validators cast the exact QBFT add-validator vote for the
  prepared target; and
* prove the final validator set, target activation, and fresh block production.

It does not publish routing, expose RPC, create public endpoints, or persist
private key material in release/evidence artifacts.

This module is part of the golden test path, defined as operator-directed
add/delete evidence. It must describe the candidate and current voters from
fresh evidence and exact service UUIDs; it must not treat legacy fixture labels
or historical baseline topology as authority.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
import time
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request

import yaml

from . import atomic_files
from .canonical import canonical_json
from .coolify_state import _DEFAULT_MAX_RESPONSE_BYTES, _DEFAULT_OPENER, resolve_coolify_controller
from .deployment_completed_helper_cleanup import (
    MotherDeploymentCompletedHelperCleanupError,
    execute_completed_mother_helper_cleanup,
)
from .deployment_node_add_replica_sync import verify_node_add_replica_sync_evidence
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path
from .deployment_validator_routes import ensure_service_validator_route, validator_route_from_record
from .ethereum_identity import checksum_address


_RELEASE_KIND = "main_computer.mother.deployment_node_add_validator_admission_release.v1"
_CLAIM_KIND = "main_computer.mother.deployment_node_add_validator_admission_claim.v1"
_EVIDENCE_KIND = "main_computer.mother.deployment_node_add_validator_admission_evidence.v1"

_RELEASE_DIRECTORY = ("actions", "deployment-node-add-validator-admission-releases")
_CLAIM_DIRECTORY = ("actions", "deployment-node-add-validator-admission-claims")
_EVIDENCE_DIRECTORY = ("evidence", "deployment-node-add-validator-admission")
_REPLICA_SYNC_EVIDENCE_DIRECTORY = ("evidence", "deployment-node-add-replica-sync")
_REPLICA_SYNC_RELEASE_DIRECTORY = ("actions", "deployment-node-add-replica-sync-releases")

_MIN_RELEASE_SECONDS = 30
_MAX_RELEASE_SECONDS = 3600

_VALIDATOR_KEY = "MC_MOTHER_VALIDATOR_PRIVATE_KEY"
_HUB_KEY = "MC_MOTHER_HUB_ADMIN_PRIVATE_KEY"
_BESU_IMAGE = "hyperledger/besu:latest"
_INIT_IMAGE = "alpine:3.20"
_GUARDIAN_IMAGE = "python:3.12-alpine"
_RETIRED_REPLICA_SYNC_GUARDIAN_NAME = "mother-replica-sync-guardian"
_DURABLE_ADMISSION_PROOF_SAMPLE_COUNT = 3
_ADMISSION_PROOF_DEFAULT_MAX_WAIT_SECONDS = 900.0
_ADMISSION_PROOF_TRANSITION_RECOVERY_MAX_WAIT_SECONDS = 1800.0
_ONE_SHOT_GUARDIAN_SUCCESS_LINGER_SECONDS = 3600
_QBFT_STALE_VOTE_QUIET_SECONDS = 35
_QBFT_STALE_VOTE_POLL_SECONDS = 5
_ADMISSION_PROGRESS_EMIT_INTERVAL_SECONDS = 30.0
_CANONICAL_HISTORY_PROOF_CONTRACT = "mother-add-node-validator-admission-canonical-block-history-v1"
_CANONICAL_HISTORY_REQUIRED_PROOF_FIELDS = (
    "first_block_number",
    "first_block_hash",
    "first_block_parent_hash",
    "first_block_validator_set",
    "second_block_number",
    "second_block_hash",
    "second_block_parent_hash",
    "second_block_validator_set",
    "latest_block_number",
    "latest_block_hash",
    "latest_block_parent_hash",
    "latest_validator_set",
)
_CANDIDATE_ACTIVATION_CANONICAL_HISTORY_PROOF_FIELD = "candidate_activation_canonical_history_proof"
_CANDIDATE_ACTIVATION_CANONICAL_HISTORY_PROOF_SHA_FIELD = "candidate_activation_canonical_history_proof_sha256"
_CANDIDATE_ACTIVATION_PROOF_HTTP_PORT = 8797
_CANDIDATE_ACTIVATION_PROOF_PORT_OFFSET = 9000

AdmissionProgressCallback = Callable[[Mapping[str, Any]], None]


def _emit_admission_progress(callback: AdmissionProgressCallback | None, event: Mapping[str, Any]) -> None:
    """Best-effort operator progress hook for long validator-admission waits."""

    if callback is None:
        return
    try:
        callback(dict(event))
    except Exception:
        # Diagnostics must never decide a live validator mutation outcome.
        return


_PRIVATE_KEY_RE = re.compile(r"0x[0-9a-fA-F]{64}")


class MotherDeploymentNodeAddValidatorAdmissionError(RuntimeError):
    """Add-node validator admission failed closed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> MotherDeploymentNodeAddValidatorAdmissionError:
    return MotherDeploymentNodeAddValidatorAdmissionError(code, message)


def _identifier(value: Any, label: str) -> str:
    if type(value) is not str or not value.strip():
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_INVALID", f"{label} is missing")
    text = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.:/@+-]+", text):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_INVALID", f"{label} contains unsafe characters")
    return text


def _sha256(value: Any, label: str) -> str:
    text = str(value or "").lower()
    if re.fullmatch(r"[0-9a-f]{64}", text) is None:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_INVALID", f"{label} is not a SHA-256 digest")
    return text


def _address(value: Any, label: str) -> str:
    text = str(value or "").lower()
    if re.fullmatch(r"0x[0-9a-f]{40}", text) is None:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_INVALID", f"{label} is not an Ethereum address")
    return text


def _validator_vote_address(value: Any, label: str) -> str:
    """Return the EIP-55 address form Besu accepts for QBFT vote RPCs.

    Mother stores and compares validator addresses in lowercase form, but Besu
    26.7 rejects lowercase ``qbft_proposeValidatorVote`` parameters with
    ``Invalid address params``.  Only the RPC payload uses the checksummed form;
    proof comparisons remain lowercase and set-based.
    """

    try:
        return checksum_address(_address(value, label))
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_INVALID", f"{label} cannot be checksummed") from exc


def _parse_utc(value: Any, label: str) -> datetime:
    if type(value) is not str:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_INVALID", f"{label} is missing")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_INVALID", f"{label} is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_INVALID", f"{label} must include UTC")
    return parsed.astimezone(timezone.utc)


def _timestamp(value: str | None = None) -> str:
    parsed = _parse_utc(value, "timestamp") if value is not None else datetime.now(timezone.utc)
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _age(value: Any, *, now: datetime | None) -> int:
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    age = int((reference - _parse_utc(value, "timestamp")).total_seconds())
    if age < -1:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_FUTURE", "timestamp is in the future")
    return max(0, age)


def _duration(seconds: int) -> int:
    if not (_MIN_RELEASE_SECONDS <= int(seconds) <= _MAX_RELEASE_SECONDS):
        raise _fail(
            "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_RELEASE_INVALID",
            f"release duration must be between {_MIN_RELEASE_SECONDS} and {_MAX_RELEASE_SECONDS} seconds",
        )
    return int(seconds)


def _binding(private_state: PrivateStateReadResult) -> dict[str, Any]:
    return {
        "generation": private_state.binding.generation,
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


def _document(private_state: PrivateStateReadResult) -> dict[str, Any]:
    try:
        data = yaml.safe_load(private_state.document_bytes)
    except yaml.YAMLError as exc:  # pragma: no cover
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_STATE_INVALID", "private state cannot be parsed") from exc
    if not isinstance(data, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_STATE_INVALID", "private state root is invalid")
    return dict(data)


def _validator_entry(private_state: PrivateStateReadResult, *, network: str, node: str) -> Mapping[str, Any]:
    state = _document(private_state)
    try:
        entry = state["networks"][network]["validators"][node]
    except (KeyError, TypeError) as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_STATE_INVALID", f"{node} validator entry is missing") from exc
    if not isinstance(entry, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_STATE_INVALID", f"{node} validator entry is invalid")
    return entry


def _validator_address(private_state: PrivateStateReadResult, *, network: str, node: str) -> str:
    return _address(_validator_entry(private_state, network=network, node=node).get("address"), f"{node} validator address")


def _validator_private_key(private_state: PrivateStateReadResult, *, network: str, node: str) -> str:
    value = str(_validator_entry(private_state, network=network, node=node).get("private_key", "")).strip()
    if _PRIVATE_KEY_RE.fullmatch(value) is None:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_STATE_INVALID", f"{node} validator private key is invalid")
    return value


def _public_node_id(private_key: str) -> str:
    if _PRIVATE_KEY_RE.fullmatch(private_key) is None:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_STATE_INVALID", "validator private key is invalid")
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
    except ImportError as exc:  # pragma: no cover
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_DEPENDENCY_MISSING", "cryptography is required to derive validator node IDs") from exc
    key = ec.derive_private_key(int(private_key[2:], 16), ec.SECP256K1())
    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return public[1:].hex()


def _root(paths: PrivateStatePaths, parts: Iterable[str]) -> Path:
    current = paths.root
    for part in parts:
        current /= part
    return current


def _ensure_directory(paths: PrivateStatePaths, parts: Iterable[str], *, operation: OperationIdentity) -> Path:
    current = paths.root
    for part in parts:
        current /= part
        atomic_files.ensure_durable_directory(current, operation=operation)
        _secure_private_path(current, is_directory=True, operation=operation)
    return current


def _relative(paths: PrivateStatePaths, path: Path, *, label: str) -> str:
    try:
        return Path(path).resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix()
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_PATH_INVALID", f"{label} is outside runtime state") from exc


def _resolve_locator(paths: PrivateStatePaths, locator: Any, directory: Iterable[str], *, label: str) -> Path:
    if type(locator) is not str or not locator.strip():
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_PATH_INVALID", f"{label} locator is missing")
    raw_text = locator.strip()
    normalized = raw_text.replace("\\", "/")
    if re.match(r"^[A-Za-z]:", normalized) or Path(raw_text).is_absolute() or PureWindowsPath(raw_text).is_absolute():
        candidate = Path(raw_text).resolve(strict=False)
    else:
        if any(part in {"", ".", ".."} for part in Path(normalized).parts) or ".." in PureWindowsPath(normalized).parts:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_PATH_INVALID", f"{label} locator is unsafe")
        candidate = (paths.root / Path(normalized)).resolve(strict=False)
    expected = _root(paths, directory).resolve(strict=False)
    try:
        candidate.relative_to(expected)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_PATH_INVALID", f"{label} must be beneath {'/'.join(directory)}") from exc
    return candidate


def _canonical_file(path: Path, *, label: str) -> tuple[dict[str, Any], bytes, str]:
    try:
        raw = Path(path).read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_INVALID", f"{label} could not be read as canonical JSON") from exc
    if not isinstance(data, dict) or canonical_json(data) != raw:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_INVALID", f"{label} is not canonical JSON")
    return data, raw, hashlib.sha256(raw).hexdigest()


def _canonical_under(paths: PrivateStatePaths, path: Path, directory: Iterable[str], label: str) -> tuple[dict[str, Any], bytes, str]:
    return _canonical_file(_resolve_locator(paths, _relative(paths, Path(path), label=label), directory, label=label), label=label)


def _digest_without(document: Mapping[str, Any], field: str) -> str:
    copy = dict(document)
    copy.pop(field, None)
    return hashlib.sha256(canonical_json(copy)).hexdigest()


def _contains_sensitive(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            name = str(key).lower()
            if any(marker in name for marker in ("private_key", "secret", "api_token", "password", "mnemonic", "seed")):
                if isinstance(item, str) and (_PRIVATE_KEY_RE.fullmatch(item.strip()) or "THISISASECRETTOKENVALUE" in item):
                    return True
            if _contains_sensitive(item):
                return True
    elif isinstance(value, list):
        return any(_contains_sensitive(item) for item in value)
    elif isinstance(value, str):
        if "BEGIN PRIVATE KEY" in value or "THISISASECRETTOKENVALUE" in value:
            return True
    return False


def _same_set(values: Iterable[str], expected: Iterable[str]) -> bool:
    return sorted(_address(item, "validator") for item in values) == sorted(_address(item, "validator") for item in expected)


def _looks_like_canonical_history_proof_payload(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    if value.get("canonical_history_proof_contract") == _CANONICAL_HISTORY_PROOF_CONTRACT:
        return True
    return all(field in value for field in _CANONICAL_HISTORY_REQUIRED_PROOF_FIELDS)


def _canonical_history_proof_payload_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(dict(value))).hexdigest()


def _canonical_history_proof_payload_missing_fields(value: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(value, Mapping):
        return list(_CANONICAL_HISTORY_REQUIRED_PROOF_FIELDS)
    return [field for field in _CANONICAL_HISTORY_REQUIRED_PROOF_FIELDS if field not in value]


def _find_canonical_history_proof_payload(value: Any, *, depth: int = 0) -> Mapping[str, Any] | None:
    """Best-effort extraction when a controller or sentinel surfaces guardian proof JSON.

    Coolify component health alone is not proof.  This hook deliberately accepts
    only structured JSON-like payloads with the block-history contract/fields; it
    does not parse Compose strings or health status text because those can contain
    the contract marker without proving what the guardian observed.
    """

    if depth > 10:
        return None
    if _looks_like_canonical_history_proof_payload(value):
        return value
    if isinstance(value, Mapping):
        for key, item in value.items():
            if re.search(r"key|secret|token|password|private", str(key), re.IGNORECASE):
                continue
            found = _find_canonical_history_proof_payload(item, depth=depth + 1)
            if found is not None:
                return found
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            found = _find_canonical_history_proof_payload(item, depth=depth + 1)
            if found is not None:
                return found
    return None


def _guardian_component_canonical_history_proof(record: Mapping[str, Any], *, names: Iterable[str]) -> Mapping[str, Any] | None:
    expected = {str(item) for item in names if str(item)}
    for candidate in (record, *_children(record)):
        if _component_names(candidate) & expected:
            return _find_canonical_history_proof_payload(candidate)
    return None


def _canonical_history_proof_payload_verified(
    payload: Any,
    *,
    desired_validator_set: Iterable[str],
    final_validator_set: Iterable[str],
) -> bool:
    if not isinstance(payload, Mapping):
        return False
    if payload.get("canonical_history_proof_contract") != _CANONICAL_HISTORY_PROOF_CONTRACT:
        return False
    if _canonical_history_proof_payload_missing_fields(payload):
        return False
    try:
        desired = [str(item) for item in desired_validator_set]
        final = [str(item) for item in final_validator_set]
        if not _same_set(final, desired):
            return False
        for field in ("first_block_validator_set", "second_block_validator_set", "latest_validator_set"):
            value = payload.get(field)
            if not isinstance(value, list) or not _same_set([str(item) for item in value], desired):
                return False
    except MotherDeploymentNodeAddValidatorAdmissionError:
        return False

    for field in ("first_block_hash", "first_block_parent_hash", "second_block_hash", "second_block_parent_hash", "latest_block_hash", "latest_block_parent_hash"):
        value = payload.get(field)
        if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", value):
            return False
    first = payload.get("first_block_number")
    second = payload.get("second_block_number")
    latest = payload.get("latest_block_number")
    if not isinstance(first, int) or not isinstance(second, int) or not isinstance(latest, int):
        return False
    if first < 0 or second <= first or latest < second:
        return False
    return True


def _latest_candidate_activation_canonical_history_proof(
    observations: Iterable[Mapping[str, Any]],
    *,
    candidate_node: str,
    target_guardian_name: str,
) -> Mapping[str, Any] | None:
    latest: Mapping[str, Any] | None = None
    for item in observations:
        if not isinstance(item, Mapping):
            continue
        if item.get("node") != candidate_node:
            continue
        if item.get("proof_guardian_name") != target_guardian_name:
            continue
        proof = item.get(_CANDIDATE_ACTIVATION_CANONICAL_HISTORY_PROOF_FIELD)
        if isinstance(proof, Mapping):
            latest = proof
    return latest


def _extract_genesis_b64(compose_text: str) -> str:
    match = re.search(r"printf '%s' '([^']+)' \| base64 -d > /config/genesis\.json", compose_text)
    if not match:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_GENESIS_MISSING", "replica-sync Compose does not contain canonical genesis material")
    return match.group(1)


def _activation_guardian_proof_stem(
    *,
    target_node: str,
    target_node_id: str,
    bootnode_enode: str,
    desired_validators: Iterable[str],
    chain_id: int,
    genesis_sha256: str,
) -> str:
    payload = {
        "target_node": target_node,
        "target_node_id": target_node_id.lower(),
        "bootnode_enode": bootnode_enode,
        "desired_validators": [str(item).lower() for item in desired_validators],
        "chain_id": int(chain_id),
        "genesis_sha256": genesis_sha256,
    }
    return hashlib.sha256(canonical_json(payload)).hexdigest()[:16]


def _candidate_activation_proof_endpoint(
    route: Mapping[str, Any],
    *,
    candidate_p2p_port: int,
    public_host: str | None = None,
) -> dict[str, Any]:
    """Return the proof endpoint Mother uses to capture guardian proof.

    Coolify does not expose container-local /proof files through the API.  The
    activation guardian therefore publishes the non-secret canonical proof JSON
    on an HTTP endpoint.  For now this endpoint is intentionally public on the
    controller host so the Mother operator process can fetch and bind the proof
    payload into evidence without Docker, SSH, or Coolify exec/file APIs.
    """

    host = public_host
    endpoint = route.get("p2p_endpoint")
    if not isinstance(host, str) or not host.strip():
        host = route.get("advertised_host")
    if (not isinstance(host, str) or not host.strip()) and isinstance(endpoint, str) and endpoint.strip() and ":" in endpoint:
        host = endpoint.rsplit(":", 1)[0]
    if not isinstance(host, str) or not host.strip():
        raise _fail(
            "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_ROUTE_INVALID",
            "candidate validator route/controller lacks a host for proof capture",
        )
    host = host.strip()
    if host in {"0.0.0.0", "::", ""}:
        raise _fail(
            "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_ROUTE_INVALID",
            "candidate proof endpoint URL cannot use a wildcard host",
        )
    try:
        p2p_port = int(candidate_p2p_port)
    except (TypeError, ValueError) as exc:
        raise _fail(
            "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_ROUTE_INVALID",
            "candidate P2P port is invalid for proof endpoint allocation",
        ) from exc
    proof_port = p2p_port + _CANDIDATE_ACTIVATION_PROOF_PORT_OFFSET
    if not 1 <= proof_port <= 65535:
        raise _fail(
            "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_ROUTE_INVALID",
            "candidate proof endpoint port is outside the valid TCP range",
        )
    return {
        "kind": "mother-add-node-validator-admission-public-proof-endpoint.v1",
        "transport": "http-public-controller",
        "host": host,
        "bind_host": "0.0.0.0",
        "host_port": proof_port,
        "container_port": _CANDIDATE_ACTIVATION_PROOF_HTTP_PORT,
        "url": f"http://{host}:{proof_port}/proof",
        "public_http_endpoint_created": True,
    }


def _activation_guardian_script(
    *,
    target_node: str,
    target_node_id: str,
    bootnode_enode: str,
    desired_validators: Iterable[str],
    chain_id: int,
    genesis_sha256: str,
) -> str:
    desired = [item.lower() for item in desired_validators]
    proof_stem = _activation_guardian_proof_stem(
        target_node=target_node,
        target_node_id=target_node_id,
        bootnode_enode=bootnode_enode,
        desired_validators=desired,
        chain_id=chain_id,
        genesis_sha256=genesis_sha256,
    )
    return "\n".join([
        "import hashlib, http.server, json, os, threading, time, traceback, urllib.request",
        f"RPC = 'http://{target_node}:8545'",
        f"TARGET_NODE = {target_node!r}",
        f"EXPECTED_NODE_ID = {target_node_id.lower()!r}",
        f"EXPECTED_CHAIN_ID = {int(chain_id)}",
        f"EXPECTED_GENESIS_SHA256 = {genesis_sha256!r}",
        f"BOOTNODE_ENODE = {bootnode_enode!r}",
        f"EXPECTED_DESIRED = {desired!r}",
        f"PROOF = '/proof/target-add-node-validator-admission-{proof_stem}.json'",
        f"HEALTHY = '/proof/target-add-node-validator-admission-{proof_stem}-healthy'",
        f"LAST_ERROR = '/proof/target-add-node-validator-admission-{proof_stem}-last-error.json'",
        "MAX_BLOCK_AGE_SECONDS = 120",
        f"PROOF_SERVER_PORT = {_CANDIDATE_ACTIVATION_PROOF_HTTP_PORT}",
        "def encoded(value): return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()",
        "def rpc(method, params):",
        "    body = encoded({'jsonrpc':'2.0','id':1,'method':method,'params':params})",
        "    req = urllib.request.Request(RPC, data=body, headers={'Content-Type':'application/json','Host':'localhost'}, method='POST')",
        "    with urllib.request.urlopen(req, timeout=5) as response:",
        "        value = json.loads(response.read(1048576).decode())",
        "    if value.get('error') is not None or 'result' not in value: raise RuntimeError(method + ' failed: ' + repr(value.get('error')))",
        "    return value['result']",
        "def normalize_node_id(value):",
        "    text = str(value or '').lower()",
        "    return text[2:] if text.startswith('0x') else text",
        "def validators(): return [str(item).lower() for item in rpc('qbft_getValidatorsByBlockNumber', ['latest'])]",
        "def validators_at(block_number): return [str(item).lower() for item in rpc('qbft_getValidatorsByBlockNumber', [hex(int(block_number))])]",
        "def block_by_number(block_number):",
        "    block = rpc('eth_getBlockByNumber', [hex(int(block_number)), False])",
        "    if not isinstance(block, dict) or not block.get('hash'): raise RuntimeError('canonical block missing: ' + str(block_number))",
        "    return block",
        "def canonical_history_entry(block_number):",
        "    block = block_by_number(block_number)",
        "    vals = validators_at(block_number)",
        "    if not same_set(vals, EXPECTED_DESIRED): raise RuntimeError('canonical block validator set mismatch at ' + str(block_number))",
        "    return {'number':int(block_number),'hash':block['hash'],'parent_hash':block.get('parentHash'),'timestamp':int(block.get('timestamp','0x0'),16),'validator_set':vals}",
        "def peer_count(): return int(rpc('net_peerCount', []), 16)",
        "def enode_node_id(enode):",
        "    text = str(enode or '')",
        "    if not text.startswith('enode://') or '@' not in text: return ''",
        "    return normalize_node_id(text.split('enode://', 1)[1].split('@', 1)[0])",
        "def peer_connected(enode):",
        "    expected = enode_node_id(enode)",
        "    if not expected: return False",
        "    try: peers = rpc('admin_peers', [])",
        "    except Exception: return False",
        "    if not isinstance(peers, list): return False",
        "    for peer in peers:",
        "        if isinstance(peer, dict) and normalize_node_id(peer.get('id')) == expected: return True",
        "    return False",
        "def ensure_peer(enode, label):",
        "    if not enode: return 'not_requested'",
        "    if peer_connected(enode): return 'already_connected_before_add'",
        "    result = rpc('admin_addPeer', [enode])",
        "    if result is True: return 'add_peer_accepted'",
        "    if peer_connected(enode): return 'already_connected_after_reject'",
        "    deadline = time.time() + 20",
        "    while time.time() < deadline:",
        "        time.sleep(1)",
        "        if peer_connected(enode): return 'already_connected_after_reject_wait'",
        "    raise RuntimeError('admin_addPeer rejected ' + label + ' and expected peer is not connected')",
        "def same_set(left, right): return sorted(left) == sorted(right)",
        "def write_json(path, payload):",
        "    tmp = path + '.tmp'",
        "    with open(tmp, 'w', encoding='utf-8') as handle: json.dump(payload, handle, sort_keys=True, separators=(',', ':'))",
        "    os.replace(tmp, path)",
        "def clear_health():",
        "    try: os.unlink(HEALTHY)",
        "    except FileNotFoundError: pass",
        "class ProofHandler(http.server.BaseHTTPRequestHandler):",
        "    def log_message(self, fmt, *args): return",
        "    def do_GET(self):",
        "        if self.path not in ('/proof', '/proof.json'):",
        "            self.send_response(404); self.end_headers(); return",
        "        try:",
        "            with open(PROOF, 'rb') as handle: raw = handle.read(65536)",
        "        except FileNotFoundError:",
        "            self.send_response(404); self.end_headers(); return",
        "        self.send_response(200)",
        "        self.send_header('Content-Type', 'application/json')",
        "        self.send_header('Cache-Control', 'no-store')",
        "        self.send_header('Content-Length', str(len(raw)))",
        "        self.end_headers()",
        "        self.wfile.write(raw)",
        "def serve_proof():",
        "    http.server.ThreadingHTTPServer(('0.0.0.0', PROOF_SERVER_PORT), ProofHandler).serve_forever()",
        "threading.Thread(target=serve_proof, daemon=True).start()",
        "def prove():",
        "    clear_health()",
        "    with open('/config/genesis.json', 'rb') as handle:",
        "        if hashlib.sha256(handle.read()).hexdigest() != EXPECTED_GENESIS_SHA256: raise RuntimeError('genesis commitment mismatch')",
        "    if int(rpc('eth_chainId', []), 16) != EXPECTED_CHAIN_ID: raise RuntimeError('chain id mismatch')",
        "    node_info = rpc('admin_nodeInfo', [])",
        "    actual_node_id = normalize_node_id(node_info.get('id')) if isinstance(node_info, dict) else ''",
        "    if actual_node_id != EXPECTED_NODE_ID: raise RuntimeError('target validator node identity mismatch')",
        "    bootnode_peer_connect_result = ensure_peer(BOOTNODE_ENODE, 'bootnode')",
        "    sync_deadline = time.time() + 240",
        "    syncing = rpc('eth_syncing', [])",
        "    peers = peer_count()",
        "    while time.time() < sync_deadline and (syncing is not False or peers < 1):",
        "        time.sleep(2)",
        "        bootnode_peer_connect_result = ensure_peer(BOOTNODE_ENODE, 'bootnode')",
        "        syncing = rpc('eth_syncing', [])",
        "        peers = peer_count()",
        "    if syncing is not False: raise RuntimeError('target is still syncing before admission proof')",
        "    if peers < 1: raise RuntimeError('target has no bootnode peers before admission proof')",
        "    deadline = time.time() + 240",
        "    final = validators()",
        "    while time.time() < deadline and not same_set(final, EXPECTED_DESIRED):",
        "        time.sleep(2)",
        "        final = validators()",
        "    if not same_set(final, EXPECTED_DESIRED): raise RuntimeError('desired validator set not reached')",
        "    bootnode_peer_connect_result = ensure_peer(BOOTNODE_ENODE, 'bootnode')",
        "    if rpc('eth_syncing', []) is not False: raise RuntimeError('target is still syncing')",
        "    first = int(rpc('eth_blockNumber', []), 16)",
        "    first_history = canonical_history_entry(first)",
        "    time.sleep(4)",
        "    second = int(rpc('eth_blockNumber', []), 16)",
        "    if second <= first: raise RuntimeError('block height did not advance')",
        "    second_history = canonical_history_entry(second)",
        "    latest = rpc('eth_getBlockByNumber', ['latest', False])",
        "    if not isinstance(latest, dict) or not latest.get('hash'): raise RuntimeError('latest block missing')",
        "    latest_number = int(latest.get('number', hex(second)), 16)",
        "    latest_history = canonical_history_entry(latest_number)",
        "    block_time = int(latest.get('timestamp', '0x0'), 16)",
        "    now = int(time.time())",
        "    if block_time > now + 15 or now - block_time > MAX_BLOCK_AGE_SECONDS: raise RuntimeError('latest block is stale')",
        f"    proof = {{'target_node':TARGET_NODE,'target_node_id':actual_node_id,'target_validator_node_identity_verified':True,'chain_id':EXPECTED_CHAIN_ID,'genesis_sha256':EXPECTED_GENESIS_SHA256,'canonical_history_proof_contract':{_CANONICAL_HISTORY_PROOF_CONTRACT!r},'bootnode_enode_sha256':hashlib.sha256(BOOTNODE_ENODE.encode()).hexdigest(),'bootnode_peer_connect_result':bootnode_peer_connect_result,'peer_count':peer_count(),'desired_validator_set':EXPECTED_DESIRED,'final_validator_set':final,'first_block_number':first,'second_block_number':second,'block_advance':second-first,'first_block_hash':first_history['hash'],'first_block_parent_hash':first_history.get('parent_hash'),'first_block_validator_set':first_history['validator_set'],'second_block_hash':second_history['hash'],'second_block_parent_hash':second_history.get('parent_hash'),'second_block_validator_set':second_history['validator_set'],'latest_block_number':latest_number,'latest_block_hash':latest_history['hash'],'latest_block_parent_hash':latest_history.get('parent_hash'),'latest_validator_set':latest_history['validator_set'],'latest_block_timestamp':block_time,'proved_at':time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}}",
        "    write_json(PROOF, proof)",
        "    try: os.unlink(LAST_ERROR)",
        "    except FileNotFoundError: pass",
        "    with open(HEALTHY, 'w', encoding='ascii') as handle: handle.write(str(int(time.time())))",
        "while True:",
        "    try:",
        "        prove()",
        "    except Exception as exc:",
        "        clear_health()",
        "        write_json(LAST_ERROR, {'error':str(exc),'type':type(exc).__name__,'traceback':traceback.format_exc(limit=4),'observed_at':time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})",
        "    time.sleep(6)",
        "",
    ])


def _candidate_activation_compose(
    *,
    target_node: str,
    genesis_b64: str,
    bootnode_enode: str,
    chain_id: int,
    genesis_sha256: str,
    target_node_id: str,
    desired_validators: Iterable[str],
    candidate_p2p_port: int,
    candidate_validator_route: Mapping[str, Any] | None = None,
    proof_public_host: str | None = None,
) -> str:
    candidate_p2p_port = int(candidate_p2p_port)
    if not 1 <= candidate_p2p_port <= 65535:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_ROUTE_INVALID", "candidate P2P port is invalid")
    decoded = base64.b64decode(genesis_b64.encode("ascii"), validate=True)
    if hashlib.sha256(decoded).hexdigest() != genesis_sha256:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_GENESIS_INVALID", "activation genesis does not match committed hash")
    desired_validator_list = [str(item) for item in desired_validators]
    proof_endpoint = _candidate_activation_proof_endpoint(
        candidate_validator_route or {"advertised_host": "127.0.0.1"},
        candidate_p2p_port=candidate_p2p_port,
        public_host=proof_public_host,
    )
    proof_port_binding = f"{proof_endpoint['host_port']}:{proof_endpoint['container_port']}/tcp"
    bind_host = proof_endpoint.get("bind_host")
    if isinstance(bind_host, str) and bind_host.strip() and bind_host.strip() not in {"0.0.0.0", "::"}:
        proof_port_binding = f"{bind_host.strip()}:{proof_endpoint['host_port']}:{proof_endpoint['container_port']}/tcp"
    proof_stem = _activation_guardian_proof_stem(
        target_node=target_node,
        target_node_id=target_node_id,
        bootnode_enode=bootnode_enode,
        desired_validators=desired_validator_list,
        chain_id=chain_id,
        genesis_sha256=genesis_sha256,
    )
    guardian_script = _activation_guardian_script(
        target_node=target_node,
        target_node_id=target_node_id,
        bootnode_enode=bootnode_enode,
        desired_validators=desired_validator_list,
        chain_id=chain_id,
        genesis_sha256=genesis_sha256,
    )
    return "\n".join([
        f"name: {target_node}",
        "",
        "services:",
        "  mother-validator-activation-init:",
        f"    image: {_INIT_IMAGE}",
        '    restart: "no"',
        "    environment:",
        f'      {_VALIDATOR_KEY}: "${{{_VALIDATOR_KEY}}}"',
        "    volumes:",
        "      - mother-config:/config",
        "      - mother-data:/var/lib/besu",
        "    command:",
        "      - sh",
        "      - -ec",
        "      - |",
        "        umask 077",
        f"        printf '%s' '{genesis_b64}' | base64 -d > /config/genesis.json",
        f"        key=\"$${{{_VALIDATOR_KEY}#0x}}\"",
        '        test "$${#key}" -eq 64',
        '        printf "%s" "$${key}" > /config/nodekey',
        '        test "$$(wc -c < /config/nodekey)" -eq 64',
        "        mkdir -p /var/lib/besu",
        "        chown -R 1000:1000 /config /var/lib/besu",
        "        chmod 0400 /config/nodekey",
        "        chmod 0444 /config/genesis.json",
        f"  {target_node}:",
        f"    image: {_BESU_IMAGE}",
        "    restart: unless-stopped",
        "    depends_on:",
        "      mother-validator-activation-init:",
        "        condition: service_completed_successfully",
        "    command:",
        "      - --data-path=/var/lib/besu",
        "      - --genesis-file=/config/genesis.json",
        "      - --sync-min-peers=1",
        "      - --node-private-key-file=/config/nodekey",
        f"      - --network-id={int(chain_id)}",
        "      - --sync-mode=FULL",
        "      - --data-storage-format=BONSAI",
        "      - --p2p-enabled=true",
        f"      - --p2p-port={candidate_p2p_port}",
        "      - --discovery-enabled=true",
        f"      - --bootnodes={bootnode_enode}",
        "      - --rpc-http-enabled=true",
        "      - --rpc-http-host=0.0.0.0",
        "      - --rpc-http-port=8545",
        "      - --rpc-http-api=ETH,NET,WEB3,QBFT,ADMIN",
        f"      - --host-allowlist=localhost,127.0.0.1,{target_node},mother-add-node-validator-activation-guardian",
        "      - --min-gas-price=0",
        "    ports:",
        f'      - "{candidate_p2p_port}:{candidate_p2p_port}/tcp"',
        f'      - "{candidate_p2p_port}:{candidate_p2p_port}/udp"',
        "    volumes:",
        "      - mother-config:/config:ro",
        "      - mother-data:/var/lib/besu",
        "    labels:",
        "      main_computer.mother.stage: add-node-validator-admission",
        f"      main_computer.mother.node: {target_node}",
        "      main_computer.mother.validator-activation: active",
        "  mother-add-node-validator-activation-guardian:",
        f"    image: {_GUARDIAN_IMAGE}",
        "    restart: unless-stopped",
        "    read_only: true",
        "    ports:",
        f"      - \"{proof_port_binding}\"",
        "    depends_on:",
        f"      {target_node}:",
        "        condition: service_started",
        "    command:",
        "      - python",
        "      - -u",
        "      - -c",
        "      - |",
        *["        " + line for line in guardian_script.splitlines()],
        "    healthcheck:",
        "      test:",
        "        - CMD",
        "        - python",
        "        - -c",
        f"        - import os,time; p='/proof/target-add-node-validator-admission-{proof_stem}-healthy'; assert os.path.isfile(p) and time.time()-os.path.getmtime(p) < 45",
        "      interval: 10s",
        "      timeout: 5s",
        "      retries: 24",
        "      start_period: 30s",
        "    volumes:",
        "      - mother-config:/config:ro",
        "      - mother-add-node-validator-admission-proof:/proof",
        f"  {_RETIRED_REPLICA_SYNC_GUARDIAN_NAME}:",
        f"    image: {_GUARDIAN_IMAGE}",
        "    restart: unless-stopped",
        "    read_only: true",
        "    command:",
        "      - python",
        "      - -u",
        "      - -c",
        "      - |",
        "        import time",
        "        while True:",
        "            time.sleep(3600)",
        "    healthcheck:",
        "      test:",
        "        - CMD",
        "        - python",
        "        - -c",
        "        - import sys; sys.exit(0)",
        "      interval: 10s",
        "      timeout: 5s",
        "      retries: 3",
        "      start_period: 5s",
        "    labels:",
        "      main_computer.mother.stage: post-admission-retired-helper-sentinel",
        f"      main_computer.mother.node: {target_node}",
        f"      main_computer.mother.retired-helper: {_RETIRED_REPLICA_SYNC_GUARDIAN_NAME}",
        "",
        "volumes:",
        "  mother-config:",
        "  mother-data:",
        "  mother-add-node-validator-admission-proof:",
        "",
    ])


def _voter_guardian_script(
    *,
    voter: str,
    candidate: str,
    candidate_enode: str,
    current_validators: Iterable[str],
    desired_validators: Iterable[str],
    chain_id: int,
    genesis_sha256: str,
    request_sha256: str,
) -> str:
    current = [item.lower() for item in current_validators]
    desired = [item.lower() for item in desired_validators]
    candidate_lower = _address(candidate, "candidate validator")
    candidate_vote = _validator_vote_address(candidate, "candidate validator")
    request = {"jsonrpc": "2.0", "id": 1, "method": "qbft_proposeValidatorVote", "params": [candidate_vote, True]}
    request_json = json.dumps(request, sort_keys=True, separators=(",", ":"))
    vote_address_by_lower = {
        item: _validator_vote_address(item, "stale QBFT pending-vote cleanup validator")
        for item in sorted(set([*current, *desired]))
    }
    return "\n".join([
        "import hashlib, http.server, json, os, threading, time, traceback, urllib.request",
        f"RPC = 'http://{voter}:8545'",
        f"VOTER_NODE = {voter!r}",
        f"EXPECTED_CHAIN_ID = {int(chain_id)}",
        f"EXPECTED_GENESIS_SHA256 = {genesis_sha256!r}",
        f"EXPECTED_CURRENT = {current!r}",
        f"EXPECTED_DESIRED = {desired!r}",
        f"CANDIDATE_VALIDATOR = {candidate_lower!r}",
        f"CANDIDATE_VALIDATOR_VOTE_ADDRESS = {candidate_vote!r}",
        f"CANDIDATE_ENODE = {candidate_enode!r}",
        f"REQUEST = json.loads({request_json!r})",
        f"EXPECTED_REQUEST_SHA256 = {request_sha256!r}",
        f"VOTE_ADDRESS_BY_LOWER = {vote_address_by_lower!r}",
        f"STALE_VOTE_QUIET_SECONDS = {int(_QBFT_STALE_VOTE_QUIET_SECONDS)}",
        f"STALE_VOTE_POLL_SECONDS = {int(_QBFT_STALE_VOTE_POLL_SECONDS)}",
        "SAFE = VOTER_NODE.replace('-', '_')",
        "PROOF = '/proof/' + SAFE + '-add-node-validator-admission.json'",
        "HEALTHY = '/proof/' + SAFE + '-add-node-validator-admission-healthy'",
        "LAST_ERROR = '/proof/' + SAFE + '-add-node-validator-admission-last-error.json'",
        "MAX_BLOCK_AGE_SECONDS = 120",
        f"PROOF_SERVER_PORT = {_CANDIDATE_ACTIVATION_PROOF_HTTP_PORT}",
        "def encoded(value): return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()",
        "def rpc(method, params):",
        "    body = encoded({'jsonrpc':'2.0','id':1,'method':method,'params':params})",
        "    req = urllib.request.Request(RPC, data=body, headers={'Content-Type':'application/json','Host':'localhost'}, method='POST')",
        "    with urllib.request.urlopen(req, timeout=5) as response:",
        "        value = json.loads(response.read(1048576).decode())",
        "    if value.get('error') is not None or 'result' not in value: raise RuntimeError(method + ' failed: ' + repr(value.get('error')))",
        "    return value['result']",
        "def normalize_node_id(value):",
        "    text = str(value or '').lower()",
        "    return text[2:] if text.startswith('0x') else text",
        "def validators(): return [str(item).lower() for item in rpc('qbft_getValidatorsByBlockNumber', ['latest'])]",
        "def validators_at(block_number): return [str(item).lower() for item in rpc('qbft_getValidatorsByBlockNumber', [hex(int(block_number))])]",
        "def block_by_number(block_number):",
        "    block = rpc('eth_getBlockByNumber', [hex(int(block_number)), False])",
        "    if not isinstance(block, dict) or not block.get('hash'): raise RuntimeError('canonical block missing: ' + str(block_number))",
        "    return block",
        "def canonical_history_entry(block_number):",
        "    block = block_by_number(block_number)",
        "    vals = validators_at(block_number)",
        "    if not same_set(vals, EXPECTED_DESIRED): raise RuntimeError('canonical block validator set mismatch at ' + str(block_number))",
        "    return {'number':int(block_number),'hash':block['hash'],'parent_hash':block.get('parentHash'),'timestamp':int(block.get('timestamp','0x0'),16),'validator_set':vals}",
        "def pending_votes():",
        "    result = rpc('qbft_getPendingVotes', [])",
        "    if not isinstance(result, dict): raise RuntimeError('pending votes response is not an object')",
        "    normalized = {}",
        "    for address, vote in result.items():",
        "        text = str(address or '').lower()",
        "        if not text.startswith('0x'): text = '0x' + text",
        "        if len(text) == 42 and isinstance(vote, bool): normalized[text] = vote",
        "    return normalized",
        "def syncing(): return rpc('eth_syncing', []) is not False",
        "def block_number(): return int(rpc('eth_blockNumber', []), 16)",
        "def satisfied_pending_vote_targets(pending, live_validators):",
        "    live = set(live_validators)",
        "    targets = []",
        "    for address, vote in sorted(pending.items()):",
        "        if vote is True and address in live: targets.append({'address':address,'vote':vote,'reason':'add_vote_already_in_validator_set'})",
        "        elif vote is False and address not in live: targets.append({'address':address,'vote':vote,'reason':'remove_vote_already_absent_from_validator_set'})",
        "    return targets",
        "def wait_for_quiet_validator_set(reference):",
        "    deadline = time.time() + STALE_VOTE_QUIET_SECONDS",
        "    first = block_number()",
        "    observations = [{'elapsed_seconds':0,'validator_set':reference,'block_number':first,'syncing':syncing()}]",
        "    while time.time() < deadline:",
        "        time.sleep(min(STALE_VOTE_POLL_SECONDS, max(0.0, deadline - time.time())))",
        "        current = validators()",
        "        block = block_number()",
        "        node_syncing = syncing()",
        "        observations.append({'elapsed_seconds':round(max(0.0, STALE_VOTE_QUIET_SECONDS - max(0.0, deadline - time.time())),3),'validator_set':current,'block_number':block,'syncing':node_syncing})",
        "        if not same_set(current, reference):",
        "            return {'quiet':False,'reason':'validator_set_changed','reference_validator_set':reference,'observations':observations}",
        "    last = observations[-1]",
        "    quiet = (last['block_number'] > first and last['syncing'] is False)",
        "    return {'quiet':quiet,'reason':'quiet_window_satisfied' if quiet else 'quiet_window_without_block_progress','reference_validator_set':reference,'observations':observations}",
        "def cleanup_satisfied_pending_votes(live_validators, phase):",
        "    before = pending_votes()",
        "    targets = satisfied_pending_vote_targets(before, live_validators)",
        "    result = {'phase':phase,'quiet_seconds':STALE_VOTE_QUIET_SECONDS,'pending_votes_before':before,'cleanup_targets':targets,'cleared':[],'skipped':[]}",
        "    if not targets:",
        "        result['pending_votes_after'] = before",
        "        result['status'] = 'not_needed'",
        "        return result",
        "    quiet = wait_for_quiet_validator_set(live_validators)",
        "    result['quiet_observation'] = quiet",
        "    if quiet.get('quiet') is not True:",
        "        result['pending_votes_after'] = pending_votes()",
        "        result['status'] = 'deferred_not_quiet'",
        "        return result",
        "    refreshed_live = validators()",
        "    refreshed = pending_votes()",
        "    for target in satisfied_pending_vote_targets(refreshed, refreshed_live):",
        "        address = target['address']",
        "        vote_address = VOTE_ADDRESS_BY_LOWER.get(address)",
        "        if not vote_address:",
        "            target = dict(target)",
        "            target['reason'] = target.get('reason', 'satisfied_vote') + '_without_committed_checksum'",
        "            result['skipped'].append(target)",
        "            continue",
        "        discard_result = rpc('qbft_discardValidatorVote', [vote_address])",
        "        cleared = dict(target)",
        "        cleared['discard_result'] = discard_result",
        "        cleared['discard_vote_address'] = vote_address",
        "        result['cleared'].append(cleared)",
        "    result['pending_votes_after'] = pending_votes()",
        "    result['status'] = 'cleared' if result['cleared'] else 'no_clearable_targets'",
        "    return result",
        "def peer_count(): return int(rpc('net_peerCount', []), 16)",
        "def enode_node_id(enode):",
        "    text = str(enode or '')",
        "    if not text.startswith('enode://') or '@' not in text: return ''",
        "    return normalize_node_id(text.split('enode://', 1)[1].split('@', 1)[0])",
        "def peer_connected(enode):",
        "    expected = enode_node_id(enode)",
        "    if not expected: return False",
        "    try: peers = rpc('admin_peers', [])",
        "    except Exception: return False",
        "    if not isinstance(peers, list): return False",
        "    for peer in peers:",
        "        if isinstance(peer, dict) and normalize_node_id(peer.get('id')) == expected: return True",
        "    return False",
        "def ensure_peer(enode, label):",
        "    if not enode: return 'not_requested'",
        "    if peer_connected(enode): return 'already_connected_before_add'",
        "    result = rpc('admin_addPeer', [enode])",
        "    if result is True: return 'add_peer_accepted'",
        "    if peer_connected(enode): return 'already_connected_after_reject'",
        "    deadline = time.time() + 20",
        "    while time.time() < deadline:",
        "        time.sleep(1)",
        "        if peer_connected(enode): return 'already_connected_after_reject_wait'",
        "    raise RuntimeError('admin_addPeer rejected ' + label + ' and expected peer is not connected')",
        "def same_set(left, right): return sorted(left) == sorted(right)",
        "def write_json(path, payload):",
        "    tmp = path + '.tmp'",
        "    with open(tmp, 'w', encoding='utf-8') as handle: json.dump(payload, handle, sort_keys=True, separators=(',', ':'))",
        "    os.replace(tmp, path)",
        "def clear_health():",
        "    try: os.unlink(HEALTHY)",
        "    except FileNotFoundError: pass",
        "def prove():",
        "    clear_health()",
        "    if hashlib.sha256(encoded(REQUEST)).hexdigest() != EXPECTED_REQUEST_SHA256: raise RuntimeError('vote request commitment mismatch')",
        "    candidate_peer_connect_result = ensure_peer(CANDIDATE_ENODE, 'candidate')",
        "    if int(rpc('eth_chainId', []), 16) != EXPECTED_CHAIN_ID: raise RuntimeError('chain id mismatch')",
        "    with open('/config/genesis.json', 'rb') as handle:",
        "        if hashlib.sha256(handle.read()).hexdigest() != EXPECTED_GENESIS_SHA256: raise RuntimeError('genesis commitment mismatch')",
        "    current = validators()",
        "    stale_vote_cleanup = []",
        "    if not same_set(current, EXPECTED_DESIRED):",
        "        stale_vote_cleanup.append(cleanup_satisfied_pending_votes(current, 'before-candidate-vote'))",
        "        current = validators()",
        "    vote_submitted = False",
        "    if not same_set(current, EXPECTED_DESIRED):",
        "        if not same_set(current, EXPECTED_CURRENT): raise RuntimeError('unexpected pre-vote validator set')",
        "        if rpc(REQUEST['method'], REQUEST['params']) is not True: raise RuntimeError('validator vote rejected')",
        "        vote_submitted = True",
        "    deadline = time.time() + 180",
        "    final = validators()",
        "    while time.time() < deadline and not same_set(final, EXPECTED_DESIRED):",
        "        time.sleep(2)",
        "        final = validators()",
        "    if not same_set(final, EXPECTED_DESIRED): raise RuntimeError('desired validator set not reached')",
        "    stale_vote_cleanup.append(cleanup_satisfied_pending_votes(final, 'after-desired-validator-set'))",
        "    final = validators()",
        "    if not same_set(final, EXPECTED_DESIRED): raise RuntimeError('desired validator set changed during stale vote cleanup')",
        "    candidate_peer_connect_result = ensure_peer(CANDIDATE_ENODE, 'candidate')",
        "    first = int(rpc('eth_blockNumber', []), 16)",
        "    first_history = canonical_history_entry(first)",
        "    time.sleep(4)",
        "    second = int(rpc('eth_blockNumber', []), 16)",
        "    if second <= first: raise RuntimeError('block height did not advance')",
        "    second_history = canonical_history_entry(second)",
        "    latest = rpc('eth_getBlockByNumber', ['latest', False])",
        "    if not isinstance(latest, dict) or not latest.get('hash'): raise RuntimeError('latest block missing')",
        "    latest_number = int(latest.get('number', hex(second)), 16)",
        "    latest_history = canonical_history_entry(latest_number)",
        "    block_time = int(latest.get('timestamp', '0x0'), 16)",
        "    now = int(time.time())",
        "    if block_time > now + 15 or now - block_time > MAX_BLOCK_AGE_SECONDS: raise RuntimeError('latest block is stale')",
        f"    proof = {{'voter_node':VOTER_NODE,'chain_id':EXPECTED_CHAIN_ID,'genesis_sha256':EXPECTED_GENESIS_SHA256,'canonical_history_proof_contract':{_CANONICAL_HISTORY_PROOF_CONTRACT!r},'candidate_validator':CANDIDATE_VALIDATOR,'candidate_enode_sha256':hashlib.sha256(CANDIDATE_ENODE.encode()).hexdigest(),'candidate_peer_connect_result':candidate_peer_connect_result,'rpc_request_sha256':EXPECTED_REQUEST_SHA256,'vote_submitted':vote_submitted,'expected_current_validator_set':EXPECTED_CURRENT,'desired_validator_set':EXPECTED_DESIRED,'final_validator_set':final,'stale_vote_cleanup':stale_vote_cleanup,'final_pending_votes':pending_votes(),'first_block_number':first,'second_block_number':second,'block_advance':second-first,'first_block_hash':first_history['hash'],'first_block_parent_hash':first_history.get('parent_hash'),'first_block_validator_set':first_history['validator_set'],'second_block_hash':second_history['hash'],'second_block_parent_hash':second_history.get('parent_hash'),'second_block_validator_set':second_history['validator_set'],'latest_block_number':latest_number,'latest_block_hash':latest_history['hash'],'latest_block_parent_hash':latest_history.get('parent_hash'),'latest_validator_set':latest_history['validator_set'],'latest_block_timestamp':block_time,'peer_count':peer_count(),'proved_at':time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}}",
        "    write_json(PROOF, proof)",
        "    try: os.unlink(LAST_ERROR)",
        "    except FileNotFoundError: pass",
        "    with open(HEALTHY, 'w', encoding='ascii') as handle: handle.write(str(int(time.time())))",
        "while True:",
        "    try:",
        "        prove()",
        f"        time.sleep({_ONE_SHOT_GUARDIAN_SUCCESS_LINGER_SECONDS})",
        "        break",
        "    except Exception as exc:",
        "        clear_health()",
        "        write_json(LAST_ERROR, {'error':str(exc),'type':type(exc).__name__,'traceback':traceback.format_exc(limit=4),'observed_at':time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})",
        "        time.sleep(6)",
        "",
    ])


def _guardian_service_name(voter: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9]+", "-", voter).strip("-").lower()
    return f"mother-add-node-validator-admission-voter-{safe}"


def _install_voter_guardian(compose_text: str, *, voter: str, script: str) -> tuple[str, str]:
    try:
        document = yaml.safe_load(compose_text)
    except yaml.YAMLError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_COMPOSE_INVALID", "voter service Compose cannot be parsed") from exc
    if not isinstance(document, dict):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_COMPOSE_INVALID", "voter Compose root is invalid")
    services = document.setdefault("services", {})
    if not isinstance(services, dict) or voter not in services:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_COMPOSE_INVALID", f"{voter} service is missing from Compose")
    name = _guardian_service_name(voter)
    services[name] = {
        "image": _GUARDIAN_IMAGE,
        "restart": "no",
        "read_only": True,
        "depends_on": {voter: {"condition": "service_started"}},
        "command": ["python", "-u", "-c", script],
        "healthcheck": {
            "test": [
                "CMD", "python", "-c",
                f"import os,time; p='/proof/{voter.replace('-', '_')}-add-node-validator-admission-healthy'; assert os.path.isfile(p) and time.time()-os.path.getmtime(p) < 45",
            ],
            "interval": "10s",
            "timeout": "5s",
            "retries": 24,
            "start_period": "30s",
        },
        "volumes": ["mother-config:/config:ro", "mother-add-node-validator-admission-proof:/proof"],
        "labels": {
            "main_computer.mother.stage": "add-node-validator-admission",
            "main_computer.mother.voter-node": voter,
            "main_computer.mother.routing-publication": "blocked",
        },
    }
    volumes = document.setdefault("volumes", {})
    if not isinstance(volumes, dict):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_COMPOSE_INVALID", "Compose volumes section is invalid")
    volumes.setdefault("mother-add-node-validator-admission-proof", None)
    updated = yaml.safe_dump(document, sort_keys=False)
    section = updated.split(f"  {name}:", 1)[1].split("\nvolumes:", 1)[0]
    if any(marker in section for marker in ("ports:", "expose:", "traefik.", "fqdn:", "domains:")):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_GUARDIAN_EXPOSED", "add-node validator-admission guardian must remain internal-only")
    if "8545:8545" in section:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_RPC_EXPOSED", "add-node validator admission guardian must not publish JSON-RPC")
    return updated, name




def _compose_service_map(compose_text: str) -> dict[str, Mapping[str, Any]]:
    try:
        document = yaml.safe_load(compose_text)
    except yaml.YAMLError as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_COMPOSE_INVALID", "voter service Compose cannot be parsed for conflicting helpers") from exc
    if not isinstance(document, dict) or not isinstance(document.get("services"), dict):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_COMPOSE_INVALID", "voter service Compose does not contain services for conflicting-helper inspection")
    return {
        str(name): definition
        for name, definition in document["services"].items()
        if isinstance(name, str) and isinstance(definition, Mapping)
    }


def _definition_text(definition: object) -> str:
    if isinstance(definition, Mapping):
        try:
            return json.dumps(definition, sort_keys=True, default=str)
        except TypeError:
            return repr(definition)
    return str(definition)


def _labels_text(definition: Mapping[str, Any]) -> str:
    labels = definition.get("labels")
    if isinstance(labels, Mapping):
        return "\n".join(f"{key}={value}" for key, value in labels.items())
    if isinstance(labels, list):
        return "\n".join(str(item) for item in labels)
    return str(labels or "")


def _extract_conflicting_helper_address(text: str, *, assignment_name: str) -> str | None:
    assignment = re.search(rf"\b{re.escape(assignment_name)}\s*=\s*['\"](0x[0-9a-fA-F]{{40}})['\"]", text)
    if assignment:
        return assignment.group(1).lower()
    addresses = [item.lower() for item in re.findall(r"0x[0-9a-fA-F]{40}", text)]
    unique = tuple(dict.fromkeys(addresses))
    return unique[0] if len(unique) == 1 else None


def _is_cleanup2_retired_node_remove_voter(service_name: str, definition: Mapping[str, Any], text: str) -> bool:
    labels_text = _labels_text(definition)
    return (
        service_name.startswith("mother-node-remove-voter-")
        and "main_computer.mother.retired_helper_mimic=true" in labels_text
        and "main_computer.mother.not_a_validator_voter=true" in labels_text
        and "main_computer.mother.cleanup_scope=helper-cleanup2-yagni" in labels_text
        and "qbft_proposeValidatorVote" not in text
    )


def _find_conflicting_node_remove_voters(compose_text: str, *, candidate_validator: str) -> list[dict[str, str]]:
    """Return remove-voter helpers that would fight this add-node validator admission.

    The gate intentionally inspects Compose, not only currently running child
    status, because a retained helper in Compose can be resurrected by the next
    Coolify deploy even if its container was stopped in the moment of inspection.
    """

    candidate = _address(candidate_validator, "candidate validator")
    conflicts: list[dict[str, str]] = []
    for service_name, definition in _compose_service_map(compose_text).items():
        labels_text = _labels_text(definition)
        helper_like = service_name.startswith("mother-node-remove-voter-") or "main_computer.mother.stage=node-remove-do" in labels_text or "node-remove-do" in labels_text
        if not helper_like:
            continue
        text = "\n".join([service_name, labels_text, _definition_text(definition)])
        if _is_cleanup2_retired_node_remove_voter(service_name, definition, text):
            continue
        target = _extract_conflicting_helper_address(text, assignment_name="TARGET_VALIDATOR")
        if target is None:
            conflicts.append({
                "helper_service": service_name,
                "target_validator": "",
                "reason": "node-remove-voter-target-unparseable",
            })
            continue
        if target == candidate and "qbft_proposeValidatorVote" in text and re.search(r"\bfalse\b", text, flags=re.IGNORECASE):
            conflicts.append({
                "helper_service": service_name,
                "target_validator": target,
                "reason": "opposite-node-remove-voter-for-candidate",
            })
    return conflicts


def _assert_no_conflicting_node_remove_voters(*, voter_compose_texts: Mapping[str, str], candidate_validator: str) -> list[dict[str, Any]]:
    preconditions: list[dict[str, Any]] = []
    conflicts: list[dict[str, str]] = []
    for voter, compose_text in voter_compose_texts.items():
        voter_conflicts = _find_conflicting_node_remove_voters(compose_text, candidate_validator=candidate_validator)
        if voter_conflicts:
            conflicts.extend({"voter_node": voter, **item} for item in voter_conflicts)
        preconditions.append({
            "name": f"{voter}-no-conflicting-node-remove-voter",
            "verified": not voter_conflicts,
            "conflict_count": len(voter_conflicts),
            "conflicts": voter_conflicts,
        })
    if conflicts:
        raise _fail(
            "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_CONFLICTING_NODE_REMOVE_VOTER",
            f"refusing add-node validator admission while node-remove voters for the same validator remain in Compose: {conflicts!r}",
        )
    return preconditions


def _load_sync_context(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    sync_evidence_path: Path,
    *,
    network: str,
    max_age_seconds: int,
    release_max_age_seconds: int,
    identity_max_age_seconds: int,
    identity_release_max_age_seconds: int,
    add_do_max_age_seconds: int,
    add_do_release_max_age_seconds: int,
    transaction_max_age_seconds: int,
    baseline_max_age_seconds: int,
    now: datetime | None,
) -> dict[str, Any]:
    verified = verify_node_add_replica_sync_evidence(
        paths,
        private_state,
        Path(sync_evidence_path),
        max_age_seconds=max_age_seconds,
        release_max_age_seconds=release_max_age_seconds,
        identity_max_age_seconds=identity_max_age_seconds,
        identity_release_max_age_seconds=identity_release_max_age_seconds,
        add_do_max_age_seconds=add_do_max_age_seconds,
        add_do_release_max_age_seconds=add_do_release_max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        baseline_max_age_seconds=baseline_max_age_seconds,
        now=now,
    )
    if verified.get("network") != network or verified.get("next_phase") != f"add-node-validator-admission-{network}":
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_SYNC_EVIDENCE_INVALID", "add-node replica-sync evidence does not authorize validator admission")
    evidence_path = Path(verified["evidence_path"])
    evidence, _, evidence_sha = _canonical_file(evidence_path, label="add-node replica-sync evidence")
    release_ref = evidence.get("release")
    if not isinstance(release_ref, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_SYNC_EVIDENCE_INVALID", "replica-sync release binding is missing")
    sync_release_path = _resolve_locator(paths, release_ref.get("locator"), _REPLICA_SYNC_RELEASE_DIRECTORY, label="add-node replica-sync release")
    sync_release, _, _sync_release_byte_sha = _canonical_file(sync_release_path, label="add-node replica-sync release")
    sync_release_sha = _digest_without(sync_release, "node_add_replica_sync_release_sha256")
    if (
        sync_release.get("kind") != "main_computer.mother.deployment_node_add_replica_sync_release.v1"
        or sync_release.get("node_add_replica_sync_release_sha256") != sync_release_sha
        or sync_release_sha != release_ref.get("sha256")
    ):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_SYNC_EVIDENCE_INVALID", "replica-sync release digest mismatch")

    current_topology = evidence.get("current_topology")
    target = evidence.get("target")
    proof_summary = evidence.get("proof_plan_summary")
    proof = evidence.get("proof")
    if not isinstance(current_topology, Mapping) or not isinstance(target, Mapping) or not isinstance(proof_summary, Mapping) or not isinstance(proof, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_SYNC_EVIDENCE_INVALID", "replica-sync evidence lacks admission context")
    voter_nodes = tuple(_identifier(item, "voter node") for item in current_topology.get("nodes", []))
    if len(voter_nodes) < 1:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_SYNC_EVIDENCE_INVALID", "no existing validators are available to vote")
    services = current_topology.get("services")
    if not isinstance(services, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_SYNC_EVIDENCE_INVALID", "current topology services are missing")

    current_set = [_address(item, "current validator") for item in current_topology.get("validator_set", [])]
    if len(current_set) != len(voter_nodes):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_SYNC_EVIDENCE_INVALID", "current validator set does not match current nodes")
    target_node = _identifier(target.get("node"), "target node")
    target_host = _identifier(target.get("controller_id"), "target host")
    candidate = _address(target.get("validator_address"), "target validator address")
    target_service_uuid = _identifier(target.get("created_service_uuid"), "target service UUID")
    if candidate in current_set:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_ALREADY_ACTIVE", "target validator is already active")
    state_candidate = _validator_address(private_state, network=network, node=target_node)
    if state_candidate != candidate:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_STATE_INVALID", "target validator address does not match private state")
    for node, address in zip(voter_nodes, current_set):
        state_address = _validator_address(private_state, network=network, node=node)
        if state_address != address:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_STATE_INVALID", f"{node} validator address does not match current set")

    desired_set = [candidate, *current_set]
    bootnode = proof_summary.get("bootnode")
    if not isinstance(bootnode, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_SYNC_EVIDENCE_INVALID", "bootnode proof summary is missing")
    bootnode_enode = _identifier(bootnode.get("enode"), "bootnode enode")
    sync_compose = sync_release.get("proof_plan", {}).get("sync_compose", {}).get("canonical_text")
    if not isinstance(sync_compose, str):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_SYNC_EVIDENCE_INVALID", "replica-sync Compose source is missing")
    genesis_b64 = _extract_genesis_b64(sync_compose)
    genesis_sha256 = _sha256(verified.get("genesis_sha256"), "genesis SHA-256")
    chain_id = int(verified.get("chain_id"))
    candidate_route = target.get("validator_route") if isinstance(target.get("validator_route"), Mapping) else None
    if not isinstance(candidate_route, Mapping):
        candidate_route = proof_summary.get("candidate_validator_route") if isinstance(proof_summary.get("candidate_validator_route"), Mapping) else None
    if not isinstance(candidate_route, Mapping):
        candidate_route = validator_route_from_record(target) or {}
    candidate_p2p_port = int(candidate_route.get("p2p_port") or proof_summary.get("candidate_p2p_port") or 30303)
    if not 1 <= candidate_p2p_port <= 65535:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_ROUTE_INVALID", "candidate validator route P2P port is invalid")
    service_routes: dict[str, dict[str, Any]] = {}
    for voter_node in voter_nodes:
        voter_service = services.get(voter_node)
        if isinstance(voter_service, Mapping):
            service_routes[voter_node] = ensure_service_validator_route(
                private_state,
                network=network,
                node=voter_node,
                service=voter_service,
                services=services,
            )
    service_routes[target_node] = dict(candidate_route)
    target_node_id = _public_node_id(_validator_private_key(private_state, network=network, node=target_node))
    candidate_validator_enode = _candidate_validator_enode(target_node_id, candidate_route)
    target_controller = resolve_coolify_controller(private_state, network, target_host)
    proof_public_host = _controller_public_host(target_controller)
    candidate_activation_proof_endpoint = _candidate_activation_proof_endpoint(
        candidate_route,
        candidate_p2p_port=candidate_p2p_port,
        public_host=proof_public_host,
    )
    activation_compose = _candidate_activation_compose(
        target_node=target_node,
        genesis_b64=genesis_b64,
        bootnode_enode=bootnode_enode,
        chain_id=chain_id,
        genesis_sha256=genesis_sha256,
        target_node_id=target_node_id,
        desired_validators=desired_set,
        candidate_p2p_port=candidate_p2p_port,
        candidate_validator_route=candidate_route,
        proof_public_host=proof_public_host,
    )
    vote_requests = []
    for node in voter_nodes:
        request = {"jsonrpc": "2.0", "id": 1, "method": "qbft_proposeValidatorVote", "params": [_validator_vote_address(candidate, "candidate validator"), True]}
        vote_requests.append({
            "voter_node": node,
            "controller_id": _identifier(services[node].get("controller_id"), f"{node} controller") if isinstance(services.get(node), Mapping) else "",
            "rpc_request": request,
            "rpc_request_sha256": hashlib.sha256(canonical_json(request)).hexdigest(),
        })
    if any(not item["controller_id"] for item in vote_requests):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_SYNC_EVIDENCE_INVALID", "voter controller binding is missing")

    return {
        "verified": verified,
        "evidence": evidence,
        "evidence_path": evidence_path,
        "evidence_sha256": evidence_sha,
        "sync_release_path": sync_release_path,
        "sync_release_sha256": sync_release_sha,
        "network": network,
        "target_node": target_node,
        "target_host": target_host,
        "target_service_uuid": target_service_uuid,
        "target_validator_address": candidate,
        "target_validator_node_id": target_node_id,
        "target_validator_node_id_sha256": hashlib.sha256(target_node_id.encode("ascii")).hexdigest(),
        "candidate_validator_enode": candidate_validator_enode,
        "candidate_validator_enode_sha256": hashlib.sha256(candidate_validator_enode.encode("utf-8")).hexdigest(),
        "candidate_validator_route": dict(candidate_route),
        "candidate_p2p_port": candidate_p2p_port,
        "candidate_activation_proof_endpoint": dict(candidate_activation_proof_endpoint),
        "service_routes": service_routes,
        "voter_nodes": list(voter_nodes),
        "voters": vote_requests,
        "current_validator_set": current_set,
        "desired_validator_set": desired_set,
        "chain_id": chain_id,
        "genesis_sha256": genesis_sha256,
        "bootnode": dict(bootnode),
        "activation_compose": activation_compose,
        "activation_compose_sha256": hashlib.sha256(activation_compose.encode("utf-8")).hexdigest(),
    }


def build_node_add_validator_admission_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    replica_sync_evidence_path: Path,
    *,
    acknowledged_replica_sync_evidence_sha256: str,
    network: str = "mainnet",
    replica_sync_max_age_seconds: int = 86400,
    replica_sync_release_max_age_seconds: int = 86400,
    identity_max_age_seconds: int = 86400,
    identity_release_max_age_seconds: int = 86400,
    add_do_max_age_seconds: int = 86400,
    add_do_release_max_age_seconds: int = 86400,
    transaction_max_age_seconds: int = 86400,
    baseline_max_age_seconds: int = 86400,
    expires_in_seconds: int = 300,
    created_at: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    network = _identifier(network, "network")
    acknowledged = _sha256(acknowledged_replica_sync_evidence_sha256, "acknowledged add-node replica-sync evidence SHA-256")
    context = _load_sync_context(
        paths,
        private_state,
        Path(replica_sync_evidence_path),
        network=network,
        max_age_seconds=replica_sync_max_age_seconds,
        release_max_age_seconds=replica_sync_release_max_age_seconds,
        identity_max_age_seconds=identity_max_age_seconds,
        identity_release_max_age_seconds=identity_release_max_age_seconds,
        add_do_max_age_seconds=add_do_max_age_seconds,
        add_do_release_max_age_seconds=add_do_release_max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        baseline_max_age_seconds=baseline_max_age_seconds,
        now=now,
    )
    if acknowledged != context["evidence_sha256"]:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_ACKNOWLEDGEMENT_MISMATCH", "operator acknowledgement does not match the exact replica-sync evidence")
    created_text = _timestamp(created_at)
    created = _parse_utc(created_text, "created_at")
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    if created > reference.replace(microsecond=0):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_RELEASE_INVALID", "release creation time is in the future")
    expires = created + timedelta(seconds=_duration(expires_in_seconds))

    activation_body = {
        "docker_compose_raw": base64.b64encode(context["activation_compose"].encode("utf-8")).decode("ascii"),
        "name": context["target_node"],
    }
    release: dict[str, Any] = {
        "kind": _RELEASE_KIND,
        "schema_version": 1,
        "created_at": created_text,
        "expires_at": expires.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "network": network,
        "mode": context["verified"]["mode"],
        "operation_kind": "MOTHER-OP-ADD-NODE",
        "mother_binding": _binding(private_state),
        "source_replica_sync_evidence": {
            "locator": _relative(paths, context["evidence_path"], label="add-node replica-sync evidence"),
            "sha256": context["evidence_sha256"],
            "completed_at": context["evidence"].get("completed_at"),
        },
        "target": {
            "node": context["target_node"],
            "controller_id": context["target_host"],
            "service_uuid": context["target_service_uuid"],
            "validator_address": context["target_validator_address"],
            "validator_node_id": context["target_validator_node_id"],
            "validator_node_id_sha256": context["target_validator_node_id_sha256"],
            "validator_enode_sha256": context["candidate_validator_enode_sha256"],
            "validator_route": dict(context["candidate_validator_route"]),
            "p2p_port": context["candidate_p2p_port"],
            "p2p_endpoint": context["candidate_validator_route"].get("p2p_endpoint"),
        },
        "admission_plan": {
            "candidate_node": context["target_node"],
            "candidate_validator_address": context["target_validator_address"],
            "candidate_validator_enode": context["candidate_validator_enode"],
            "candidate_validator_enode_sha256": context["candidate_validator_enode_sha256"],
            "voter_nodes": list(context["voter_nodes"]),
            "vote_threshold": "all-existing-validators",
            "logical_vote_count": len(context["voter_nodes"]),
            "current_validator_set": list(context["current_validator_set"]),
            "desired_validator_set": list(context["desired_validator_set"]),
            "chain_id": context["chain_id"],
            "genesis_sha256": context["genesis_sha256"],
            "bootnode": dict(context["bootnode"]),
            "candidate_validator_route": dict(context["candidate_validator_route"]),
            "candidate_p2p_port": context["candidate_p2p_port"],
            "candidate_activation_proof_endpoint": dict(context["candidate_activation_proof_endpoint"]),
            "service_routes": {node: dict(route) for node, route in context["service_routes"].items()},
            "rpc_requests": list(context["voters"]),
            "activation_compose": {
                "sha256": context["activation_compose_sha256"],
                "semantic_sha256": hashlib.sha256(canonical_json(yaml.safe_load(context["activation_compose"]))).hexdigest(),
                "byte_length": len(context["activation_compose"].encode("utf-8")),
                "canonical_text": context["activation_compose"],
                "body_sha256": hashlib.sha256(canonical_json(activation_body)).hexdigest(),
            },
            "proof_required": [
                "replica-sync evidence remains clean",
                "target identity env keys remain installed",
                "target service is redeployed with validator identity active",
                "selected bootnode P2P endpoint is reachable before validator vote",
                "target activation guardian explicitly peers candidate to bootnode and proves sync",
                "voter guardians explicitly peer current validators to the candidate validator enode",
                "all current validators submit the exact committed QBFT admission vote",
                "final QBFT validator set is exactly desired_validator_set",
                "target validator node identity matches the target validator private key",
                "blocks remain fresh after admission",
            ],
        },
        "authority": {
            "authorization_source": "explicit-operator-release",
            "live_execution_authorized": True,
            "validator_vote_authorized": True,
            "validator_activation_authorized": True,
            "qbft_transition_recovery_authorized": True,
            "replica_sync_authorized": False,
            "service_creation_authorized": False,
            "identity_install_authorized": False,
            "routing_or_topology_publication_authorized": False,
            "requested_use_limit": 1,
        },
        "policy": {
            "allowed_http_methods": ["GET", "PATCH", "POST"],
            "compiler": "mother-native-add-node-validator-admission-v1",
            "coolify_control_plane_only": False,
            "all_existing_validator_votes_required": True,
            "qbft_transition_recovery_restart_authorized": True,
            "manual_ssh_required": False,
            "public_http_endpoint_created": True,
            "public_candidate_activation_proof_endpoint_created": True,
            "private_candidate_activation_proof_endpoint_created": False,
            "routing_or_topology_published": False,
            "private_keys_materialized_in_memory_only": True,
            "private_keys_persisted": False,
            "secrets_in_output": False,
            "executor_implemented": True,
        },
        "summary": {
            "clean": True,
            "executor_implemented": True,
            "candidate_node": context["target_node"],
            "target_host": context["target_host"],
            "created_service_uuid": context["target_service_uuid"],
            "current_validator_count": len(context["current_validator_set"]),
            "desired_validator_count": len(context["desired_validator_set"]),
            "logical_vote_count": len(context["voter_nodes"]),
            "validator_vote_authorized": True,
            "validator_activation_authorized": True,
            "qbft_transition_recovery_authorized": True,
            "routing_or_topology_publication_authorized": False,
            "manual_ssh_required": False,
            "public_endpoint_created": True,
            "public_candidate_activation_proof_endpoint_created": True,
            "next_phase": f"add-node-validator-admission-{network}",
        },
        "node_add_validator_admission_release_sha256": None,
    }
    release["node_add_validator_admission_release_sha256"] = _digest_without(release, "node_add_validator_admission_release_sha256")
    if _contains_sensitive(release):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_RELEASE_INVALID", "release contains sensitive material")
    return release


def write_node_add_validator_admission_release(
    paths: PrivateStatePaths,
    release: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    document = dict(release)
    digest = _digest_without(document, "node_add_validator_admission_release_sha256")
    if (
        document.get("kind") != _RELEASE_KIND
        or document.get("node_add_validator_admission_release_sha256") != digest
        or _contains_sensitive(document)
    ):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_RELEASE_INVALID", "release is malformed")
    payload = canonical_json(document)
    current = _ensure_directory(paths, _RELEASE_DIRECTORY, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("created_at", "")))[:32] or "addnodevalidatoradmission"
    destination = current / f"{stamp}-{document['network']}-{document['target']['node']}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_CONFLICT", "release destination contains different bytes")
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def _claim_path(paths: PrivateStatePaths, digest: str) -> Path:
    return _root(paths, _CLAIM_DIRECTORY) / f"{digest}.json"


def inspect_node_add_validator_admission_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str | None = None,
    max_age_seconds: int = 900,
    replica_sync_max_age_seconds: int = 86400,
    replica_sync_release_max_age_seconds: int = 86400,
    identity_max_age_seconds: int = 86400,
    identity_release_max_age_seconds: int = 86400,
    add_do_max_age_seconds: int = 86400,
    add_do_release_max_age_seconds: int = 86400,
    transaction_max_age_seconds: int = 86400,
    baseline_max_age_seconds: int = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    release_path = _resolve_locator(paths, str(Path(release_path)), _RELEASE_DIRECTORY, label="add-node validator-admission release")
    document, _raw, _file_sha = _canonical_file(release_path, label="add-node validator-admission release")
    digest = _digest_without(document, "node_add_validator_admission_release_sha256")
    if not all([
        document.get("kind") == _RELEASE_KIND,
        document.get("node_add_validator_admission_release_sha256") == digest,
        document.get("mother_binding") == _binding(private_state),
        not _contains_sensitive(document),
    ]):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_RELEASE_INVALID", "release is invalid or stale")
    if acknowledged_release_sha256 is not None and _sha256(acknowledged_release_sha256, "acknowledged release SHA-256") != digest:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_RELEASE_ACKNOWLEDGEMENT_MISMATCH", "operator acknowledgement does not match exact release")
    created_age = _age(document.get("created_at"), now=now)
    if created_age > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_RELEASE_STALE", "release is outside freshness window")
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    expires_at = _parse_utc(document.get("expires_at"), "expires_at")
    if reference > expires_at:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_RELEASE_EXPIRED", "release is expired")
    source = document.get("source_replica_sync_evidence")
    if not isinstance(source, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_RELEASE_INVALID", "source replica-sync evidence binding is missing")
    evidence_path = _resolve_locator(paths, source.get("locator"), _REPLICA_SYNC_EVIDENCE_DIRECTORY, label="add-node replica-sync evidence")
    context = _load_sync_context(
        paths,
        private_state,
        evidence_path,
        network=_identifier(document.get("network"), "network"),
        max_age_seconds=replica_sync_max_age_seconds,
        release_max_age_seconds=replica_sync_release_max_age_seconds,
        identity_max_age_seconds=identity_max_age_seconds,
        identity_release_max_age_seconds=identity_release_max_age_seconds,
        add_do_max_age_seconds=add_do_max_age_seconds,
        add_do_release_max_age_seconds=add_do_release_max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        baseline_max_age_seconds=baseline_max_age_seconds,
        now=now,
    )
    if source.get("sha256") != context["evidence_sha256"]:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_RELEASE_INVALID", "source replica-sync evidence digest mismatch")
    expected = build_node_add_validator_admission_release(
        paths,
        private_state,
        evidence_path,
        acknowledged_replica_sync_evidence_sha256=context["evidence_sha256"],
        network=document["network"],
        replica_sync_max_age_seconds=replica_sync_max_age_seconds,
        replica_sync_release_max_age_seconds=replica_sync_release_max_age_seconds,
        identity_max_age_seconds=identity_max_age_seconds,
        identity_release_max_age_seconds=identity_release_max_age_seconds,
        add_do_max_age_seconds=add_do_max_age_seconds,
        add_do_release_max_age_seconds=add_do_release_max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        baseline_max_age_seconds=baseline_max_age_seconds,
        expires_in_seconds=int((expires_at - _parse_utc(document["created_at"], "created_at")).total_seconds()),
        created_at=document["created_at"],
        now=now,
    )
    if canonical_json(expected) != canonical_json(document):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_RELEASE_INVALID", "release no longer matches current inputs")
    claim = _claim_path(paths, digest)
    return {
        "clean": True,
        "release_path": str(release_path.resolve(strict=False)),
        "node_add_validator_admission_release_sha256": digest,
        "age_seconds": created_age,
        "expires_at": document["expires_at"],
        "release_already_claimed": claim.exists(),
        "mother_binding": dict(document["mother_binding"]),
        "network": document["network"],
        "candidate_node": document["target"]["node"],
        "target_host": document["target"]["controller_id"],
        "created_service_uuid": document["target"]["service_uuid"],
        "candidate_validator_address": document["target"]["validator_address"],
        "target_validator_node_id_sha256": document["target"]["validator_node_id_sha256"],
        "voter_nodes": list(document["admission_plan"]["voter_nodes"]),
        "logical_vote_count": document["admission_plan"]["logical_vote_count"],
        "chain_id": document["admission_plan"]["chain_id"],
        "genesis_sha256": document["admission_plan"]["genesis_sha256"],
        "current_validator_set": list(document["admission_plan"]["current_validator_set"]),
        "desired_validator_set": list(document["admission_plan"]["desired_validator_set"]),
        "activation_compose_sha256": document["admission_plan"]["activation_compose"]["sha256"],
        "source_replica_sync_evidence_sha256": source["sha256"],
        "validator_vote_authorized": True,
        "validator_activation_authorized": True,
        "routing_or_topology_publication_authorized": False,
        "public_endpoint_created": (
            isinstance(document["admission_plan"].get("candidate_activation_proof_endpoint"), Mapping)
            and document["admission_plan"]["candidate_activation_proof_endpoint"].get("public_http_endpoint_created") is True
        ),
        "public_candidate_activation_proof_endpoint_created": (
            isinstance(document["admission_plan"].get("candidate_activation_proof_endpoint"), Mapping)
            and document["admission_plan"]["candidate_activation_proof_endpoint"].get("public_http_endpoint_created") is True
        ),
        "private_candidate_activation_proof_endpoint_created": False,
        "manual_ssh_required": False,
        "next_phase": f"add-node-validator-admission-{document['network']}",
    }


def verify_node_add_validator_admission_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    **kwargs: Any,
) -> dict[str, Any]:
    return inspect_node_add_validator_admission_release(paths, private_state, release_path, **kwargs)


def _open(opener: Any, request: urllib.request.Request, timeout: float):
    try:
        return opener.open(request, timeout=timeout)
    except TypeError:
        return opener.open(request)


def _controller_value(controller: Any, field: str, default: Any = None) -> Any:
    if isinstance(controller, Mapping):
        return controller.get(field, default)
    return getattr(controller, field, default)


def _controller_public_host(controller: Any) -> str:
    base_url = str(_controller_value(controller, "base_url", "")).strip()
    if not base_url:
        raise _fail(
            "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_CONTROLLER_INVALID",
            "Coolify controller base URL is missing for public proof endpoint",
        )
    parsed = urllib.parse.urlparse(base_url if "://" in base_url else f"http://{base_url}")
    host = parsed.hostname
    if not isinstance(host, str) or not host.strip():
        raise _fail(
            "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_CONTROLLER_INVALID",
            "Coolify controller base URL lacks a hostname for public proof endpoint",
        )
    return host.strip()


def _http(
    controller: Any,
    method: str,
    endpoint: str,
    *,
    body: Mapping[str, Any] | None,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    base_url = str(_controller_value(controller, "base_url", "")).rstrip("/")
    if not base_url:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_CONTROLLER_INVALID", "Coolify controller base URL is missing")
    url = base_url + endpoint
    headers = {
        "Accept": "application/json",
        "User-Agent": "main-computer-mother-node-add-validator-admission/1",
    }
    data = None
    if body is not None:
        data = canonical_json(body)
        headers["Content-Type"] = "application/json"
    token = str(_controller_value(controller, "api_token", ""))
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    started = time.monotonic()
    try:
        with _open(opener, request, timeout=timeout) as response:
            raw = response.read(max_response_bytes + 1)
            status = int(getattr(response, "status", response.getcode()))
            ctype = str(response.headers.get("Content-Type", ""))
    except urllib.error.HTTPError as exc:
        raw = exc.read(max_response_bytes + 1)
        status = int(exc.code)
        ctype = str(exc.headers.get("Content-Type", ""))
    elapsed_ms = int((time.monotonic() - started) * 1000)
    truncated = len(raw) > max_response_bytes
    raw = raw[:max_response_bytes]
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = raw.decode("utf-8", errors="replace")
    return {
        "status": status,
        "ok": 200 <= status <= 299,
        "content_type": ctype,
        "elapsed_ms": elapsed_ms,
        "byte_length": len(raw),
        "truncated": truncated,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "payload": payload,
    }


def _safe_response(response: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": response.get("status"),
        "ok": response.get("ok"),
        "content_type": response.get("content_type"),
        "elapsed_ms": response.get("elapsed_ms"),
        "byte_length": response.get("byte_length"),
        "response_sha256": response.get("response_sha256"),
    }


def _fetch_candidate_activation_proof_payload(
    proof_endpoint: Mapping[str, Any] | None,
    *,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> tuple[Mapping[str, Any] | None, dict[str, Any]]:
    """Fetch non-secret candidate activation proof through the private endpoint."""

    if not isinstance(proof_endpoint, Mapping):
        return None, {"transport": "missing", "ok": False, "reason": "candidate activation proof endpoint is missing"}
    url = proof_endpoint.get("url")
    if not isinstance(url, str) or not url.startswith("http://"):
        return None, {"transport": proof_endpoint.get("transport"), "ok": False, "reason": "candidate activation proof endpoint URL is missing or unsupported"}
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "main-computer-mother-validator-admission-proof-capture/1",
        },
        method="GET",
    )
    started = time.monotonic()
    try:
        with _open(opener, request, timeout=timeout) as response:
            raw = response.read(max_response_bytes + 1)
            status = int(getattr(response, "status", response.getcode()))
            ctype = str(response.headers.get("Content-Type", ""))
    except urllib.error.HTTPError as exc:
        raw = exc.read(max_response_bytes + 1)
        status = int(exc.code)
        ctype = str(exc.headers.get("Content-Type", "")) if exc.headers else ""
    except Exception as exc:
        return None, {
            "transport": proof_endpoint.get("transport"),
            "url": url,
            "ok": False,
            "error_type": type(exc).__name__,
            "message": str(exc)[:300],
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    elapsed_ms = int((time.monotonic() - started) * 1000)
    truncated = len(raw) > max_response_bytes
    raw = raw[:max_response_bytes]
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None
    summary = {
        "transport": proof_endpoint.get("transport"),
        "url": url,
        "status": status,
        "ok": 200 <= status <= 299,
        "content_type": ctype,
        "elapsed_ms": elapsed_ms,
        "byte_length": len(raw),
        "truncated": truncated,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
    }
    if isinstance(payload, Mapping):
        return payload, summary
    summary["reason"] = "proof endpoint did not return a JSON object"
    return None, summary


def _records(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        for key in ("data", "services", "applications", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, Mapping)]
        return [payload]
    return []


def _children(record: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    found: list[Mapping[str, Any]] = []
    for key in ("children", "containers", "services", "applications", "resources"):
        value = record.get(key)
        if isinstance(value, list):
            found.extend(item for item in value if isinstance(item, Mapping))
        elif isinstance(value, Mapping):
            found.extend(item for item in value.values() if isinstance(item, Mapping))
    return found


def _service_record_matches(record: Mapping[str, Any], *, node: str, service_uuid: str | None = None) -> bool:
    uuid = str(record.get("uuid") or record.get("id") or "")
    name = str(record.get("name") or record.get("display_name") or record.get("fqdn") or "")
    if service_uuid is not None and uuid == service_uuid:
        return True
    return name == node or record.get("node") == node


def _find_service_record(payload: Any, *, node: str, service_uuid: str | None = None) -> Mapping[str, Any]:
    if isinstance(payload, Mapping) and _service_record_matches(payload, node=node, service_uuid=service_uuid):
        return payload
    candidates = _records(payload)
    for item in candidates:
        if _service_record_matches(item, node=node, service_uuid=service_uuid):
            return item
    raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_SERVICE_NOT_FOUND", f"Coolify service for {node} was not found")


def _service_uuid_hints_from_replica_sync_evidence(document: Mapping[str, Any]) -> dict[str, str]:
    """Extract exact Coolify service UUIDs observed during the proven replica-sync phase.

    Coolify inventory responses are not guaranteed to expose service names uniformly.
    Admission execution should therefore prefer the canonical UUIDs already bound in
    the clean add-node replica-sync evidence, and fall back to inventory discovery only
    when no evidence-bound UUID exists.
    """
    hints: dict[str, str] = {}

    current_topology = document.get("current_topology")
    if isinstance(current_topology, Mapping):
        services = current_topology.get("services")
        if isinstance(services, Mapping):
            for raw_node, raw_service in services.items():
                if not isinstance(raw_service, Mapping):
                    continue
                node = str(raw_service.get("node") or raw_node or "").strip()
                uuid = str(raw_service.get("service_uuid") or raw_service.get("uuid") or raw_service.get("id") or "").strip()
                if node and uuid:
                    hints[node] = uuid

    standby_topology = document.get("standby_topology")
    if isinstance(standby_topology, Mapping):
        node = str(standby_topology.get("standby_node") or "").strip()
        uuid = str(standby_topology.get("standby_service_uuid") or "").strip()
        if node and uuid:
            hints[node] = uuid

    target = document.get("target")
    if isinstance(target, Mapping):
        node = str(target.get("node") or "").strip()
        uuid = str(target.get("created_service_uuid") or target.get("service_uuid") or "").strip()
        if node and uuid:
            hints[node] = uuid

    proof_plan = document.get("proof_plan_summary")
    if isinstance(proof_plan, Mapping):
        bootnode = proof_plan.get("bootnode")
        if isinstance(bootnode, Mapping):
            node = str(bootnode.get("node") or "").strip()
            uuid = str(bootnode.get("service_uuid") or "").strip()
            if node and uuid:
                hints[node] = uuid

    return hints



def _preflight_existing_validator_services(
    *,
    voter_nodes: list[str],
    request_by_voter: Mapping[str, Mapping[str, Any]],
    service_uuid_hints: Mapping[str, str],
    controllers: Mapping[str, Any],
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, Mapping[str, Any]], dict[str, str]]:
    """Verify every selected voter service before candidate mutation.

    Historical evidence may describe earlier validator services, but admission
    may mutate the candidate only after the selected live voter services have
    been proven reachable in this run.
    """
    preconditions: list[dict[str, Any]] = []
    service_uuids: dict[str, str] = {}
    detail_records: dict[str, Mapping[str, Any]] = {}
    compose_texts: dict[str, str] = {}

    for voter in voter_nodes:
        vote_request = request_by_voter.get(voter)
        if not isinstance(vote_request, Mapping):
            raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_INVALID", f"{voter} vote request is missing")
        controller_id = _identifier(vote_request["controller_id"], f"{voter} controller")
        controller = controllers[controller_id]
        uuid_hint = service_uuid_hints.get(voter)
        if uuid_hint:
            uuid = _identifier(uuid_hint, f"{voter} service UUID")
            detail_endpoint = f"/api/v1/services/{urllib.parse.quote(uuid, safe='')}"
            detail = _http(controller, "GET", detail_endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
            if not detail["ok"]:
                raise _fail(
                    "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_STALE_BASELINE",
                    f"{voter} selected validator service {uuid} is not live before candidate mutation (HTTP {detail['status']})",
                )
            try:
                detail_record = _find_service_record(detail["payload"], node=voter, service_uuid=uuid)
            except MotherDeploymentNodeAddValidatorAdmissionError as exc:
                raise _fail(
                    "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_STALE_BASELINE",
                    f"{voter} selected validator service {uuid} does not match live Coolify detail before candidate mutation",
                ) from exc
            service_status = _require_existing_validator_service_healthy(
                voter=voter,
                service_uuid=uuid,
                detail_record=detail_record,
            )
            compose_text = _compose_text(detail_record)
            service_uuids[voter] = uuid
            detail_records[voter] = detail_record
            compose_texts[voter] = compose_text
            preconditions.append({
                "name": f"{voter}-service-before-add-node-validator-admission",
                "controller_id": controller_id,
                "method": "GET",
                "endpoint": detail_endpoint,
                "status": detail["status"],
                "response_sha256": detail["response_sha256"],
                "service_uuid": uuid,
                "service_uuid_source": "replica-sync-evidence",
                "service_status": service_status,
                "component_or_service_observed": True,
                "compose_text_available": True,
                "compose_text_sha256": hashlib.sha256(compose_text.encode("utf-8")).hexdigest(),
                "verified": True,
                "verified_before_candidate_mutation": True,
            })
            continue

        inventory = _http(controller, "GET", "/api/v1/services", body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        if not inventory["ok"]:
            raise _fail(
                "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_STALE_BASELINE",
                f"{voter} selected validator inventory is not live before candidate mutation (HTTP {inventory['status']})",
            )
        try:
            record = _find_service_record(inventory["payload"], node=voter)
        except MotherDeploymentNodeAddValidatorAdmissionError as exc:
            raise _fail(
                "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_STALE_BASELINE",
                f"{voter} selected validator service is not present in live Coolify inventory before candidate mutation",
            ) from exc
        uuid = _identifier(record.get("uuid") or record.get("id"), f"{voter} service UUID")
        detail_endpoint = f"/api/v1/services/{urllib.parse.quote(uuid, safe='')}"
        detail = _http(controller, "GET", detail_endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        if not detail["ok"]:
            raise _fail(
                "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_STALE_BASELINE",
                f"{voter} selected validator service detail is not live before candidate mutation (HTTP {detail['status']})",
            )
        try:
            detail_record = _find_service_record(detail["payload"], node=voter, service_uuid=uuid)
        except MotherDeploymentNodeAddValidatorAdmissionError as exc:
            raise _fail(
                "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_STALE_BASELINE",
                f"{voter} selected validator service detail does not match inventory before candidate mutation",
            ) from exc
        service_status = _require_existing_validator_service_healthy(
            voter=voter,
            service_uuid=uuid,
            detail_record=detail_record,
        )
        compose_text = _compose_text(detail_record)
        service_uuids[voter] = uuid
        detail_records[voter] = detail_record
        compose_texts[voter] = compose_text
        preconditions.append({
            "name": f"{voter}-service-before-add-node-validator-admission",
            "controller_id": controller_id,
            "method": "GET",
            "endpoint": "/api/v1/services",
            "status": inventory["status"],
            "response_sha256": inventory["response_sha256"],
            "service_uuid": uuid,
            "service_uuid_source": "coolify-inventory",
            "service_status": _service_status(record),
            "component_or_service_observed": True,
            "verified": True,
            "verified_before_candidate_mutation": True,
        })
        preconditions.append({
            "name": f"{voter}-service-detail-before-add-node-validator-admission",
            "controller_id": controller_id,
            "method": "GET",
            "endpoint": detail_endpoint,
            "status": detail["status"],
            "response_sha256": detail["response_sha256"],
            "service_uuid": uuid,
            "service_uuid_source": "coolify-inventory",
            "service_status": service_status,
            "component_or_service_observed": True,
            "compose_text_available": True,
            "compose_text_sha256": hashlib.sha256(compose_text.encode("utf-8")).hexdigest(),
            "verified": True,
            "verified_before_candidate_mutation": True,
        })

    return preconditions, service_uuids, detail_records, compose_texts


def _service_status(record: Mapping[str, Any]) -> str:
    for key in ("status", "human_status", "state", "health"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "unknown"


def _require_existing_validator_service_healthy(
    *,
    voter: str,
    service_uuid: str,
    detail_record: Mapping[str, Any],
) -> str:
    service_status = _service_status(detail_record)
    if service_status.strip().lower() != "running:healthy":
        raise _fail(
            "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_REQUIRED_VALIDATOR_UNHEALTHY",
            (
                f"{voter} selected validator service {service_uuid} is {service_status!r} "
                "before candidate mutation; refusing add-node validator admission until every "
                "required existing validator service is running:healthy"
            ),
        )
    return service_status


def _component_names(record: Mapping[str, Any]) -> set[str]:
    names: set[str] = set()
    for key in ("name", "service", "service_name", "serviceName", "subName"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            names.add(value.strip())
    return names


def _component_status(record: Mapping[str, Any], *, names: Iterable[str]) -> str:
    """Return the exact named component status, never the parent service status.

    Validator admission is only proven by the activation/voter guardian
    components.  A healthy parent Coolify service only proves the Compose project
    is generally running; it does not prove the guardian's live
    ``qbft_getValidatorsByBlockNumber("latest")`` check accepted the desired
    validator set.
    """

    expected = {str(item) for item in names if str(item)}
    for candidate in (record, *_children(record)):
        if _component_names(candidate) & expected:
            return _service_status(candidate)
    return "missing"


def _component_healthy(record: Mapping[str, Any], *, names: Iterable[str]) -> bool:
    return _component_status(record, names=names) == "running:healthy"


def _terminal_completed_component_status(status: str) -> bool:
    """Return True only for explicit successful terminal component states.

    Coolify can report a child application as plain ``exited`` without an exit
    code.  That is not proof: the add-node incident showed a generic exited
    guardian could be paired with stale parent health while the validator set
    was never reached.  Only terminal statuses that carry an explicit zero exit
    code may stand in for a live healthy one-shot voter.
    """

    normalized = status.strip().lower()
    return normalized in {
        "exited:0",
        "stopped:0",
        "exited (0)",
        "stopped (0)",
        "exited successfully",
        "stopped successfully",
    }


def _component_proven(record: Mapping[str, Any], *, names: Iterable[str], allow_terminal_completed: bool = False) -> bool:
    status = _component_status(record, names=names)
    return status == "running:healthy" or (allow_terminal_completed and _terminal_completed_component_status(status))


def _recover_target_start_rejection(
    *,
    controller: Mapping[str, Any],
    target_endpoint: str,
    candidate_node: str,
    target_uuid: str,
    target_guardian_name: str,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    """Reobserve a candidate service after Coolify rejects POST /start.

    Coolify can return HTTP 400 when a service is already running.  Admission
    should not blindly mark that as success, but it may continue when a fresh
    service detail proves that the candidate's activation Compose is installed
    and the service is already in a started/running state.  The admission and
    voter guardian proofs remain mandatory before the validator set can be
    considered changed.
    """

    receipt: dict[str, Any] = {
        "name": f"{candidate_node}-target-start-rejection-recovery",
        "method": "GET",
        "endpoint": target_endpoint,
        "service_uuid": target_uuid,
        "guardian_service": target_guardian_name,
        "verified": False,
    }
    detail = _http(
        controller,
        "GET",
        target_endpoint,
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    receipt.update({
        "status": detail["status"],
        "response_sha256": detail["response_sha256"],
    })
    if not detail["ok"]:
        receipt["reason"] = f"target service detail failed with HTTP {detail['status']} after start rejection"
        return receipt
    try:
        record = _find_service_record(detail["payload"], node=candidate_node, service_uuid=target_uuid)
        compose_text = _compose_text(record)
    except MotherDeploymentNodeAddValidatorAdmissionError as exc:
        receipt["reason"] = str(exc)
        return receipt

    compose_sha = hashlib.sha256(compose_text.encode("utf-8")).hexdigest()
    service_status = _service_status(record)
    candidate_component_status = _component_status(record, names=[candidate_node])
    guardian_component_status = _component_status(record, names=[target_guardian_name])
    activation_compose_installed = (
        "mother-validator-activation-init" in compose_text
        and target_guardian_name in compose_text
        and f"{candidate_node}:" in compose_text
        and "main_computer.mother.validator-activation: active" in compose_text
    )
    service_started = (
        service_status.startswith(("running", "degraded"))
        or candidate_component_status.startswith("running")
        or guardian_component_status.startswith("running")
    )
    receipt.update({
        "service_status": service_status,
        "candidate_component_status": candidate_component_status,
        "guardian_component_status": guardian_component_status,
        "compose_text_available": True,
        "compose_text_sha256": compose_sha,
        "activation_compose_installed": activation_compose_installed,
        "service_started": service_started,
    })
    if not activation_compose_installed:
        receipt["reason"] = "target activation Compose was not visible after Coolify start rejection"
        return receipt
    if not service_started:
        receipt["reason"] = "target service was not started after Coolify start rejection"
        return receipt
    receipt["verified"] = True
    receipt["reason"] = (
        "Coolify rejected POST /start for an already-started candidate service; "
        "activation Compose is installed and exact admission guardian proof remains required"
    )
    return receipt


def _expected_admission_guardians(*, candidate_node: str, voter_nodes: Iterable[str]) -> dict[str, str]:
    guardians = {candidate_node: "mother-add-node-validator-activation-guardian"}
    for voter in voter_nodes:
        guardians[str(voter)] = _guardian_service_name(str(voter))
    return guardians


def _durable_admission_proof_nodes(*, candidate_node: str) -> set[str]:
    """Nodes whose guardian health is durable validator-admission proof.

    Add-validator admission has two helper classes with different lifetimes:

    * the candidate activation guardian continuously proves the final validator
      set on the node being admitted;
    * voter guardians are transient one-shot helpers that submit/observe the QBFT
      vote and may then be cleaned up, excluded from the model, or report
      unhealthy after their work is no longer the durable authority.

    Therefore the durable proof gate must be candidate-activation driven.  A
    transient voter helper is still observed for diagnostics, but it must not
    block success after the candidate guardian proves the desired validator set.
    """

    return {candidate_node}


def _validator_admission_proof_guardians_verified(document: Mapping[str, Any]) -> bool:
    candidate = document.get("candidate_node")
    voters = document.get("voter_nodes")
    observations = document.get("health_observations")
    if not isinstance(candidate, str) or not isinstance(voters, list) or not isinstance(observations, list):
        return False
    voter_nodes = [item for item in voters if isinstance(item, str)]
    expected = _expected_admission_guardians(candidate_node=candidate, voter_nodes=voter_nodes)
    if set(expected) != {candidate, *voter_nodes}:
        return False

    latest_candidate_healthy: bool | None = None
    observed_voter_guardians: set[str] = set()
    for item in observations:
        if not isinstance(item, Mapping):
            continue
        node = item.get("node")
        if not isinstance(node, str) or node not in expected:
            continue
        if item.get("proof_guardian_name") != expected[node]:
            continue
        if node == candidate:
            # Earlier healthy samples are not enough: the latest exact candidate
            # activation guardian observation must remain healthy/current.  Voter
            # helpers are one-shot/transient and are non-blocking once activation
            # has been proven.
            latest_candidate_healthy = item.get("proof_guardian_healthy") is True
        else:
            observed_voter_guardians.add(node)
    return latest_candidate_healthy is True and set(voter_nodes) <= observed_voter_guardians




def _canonical_validator_history_proof_verified(document: Mapping[str, Any]) -> bool:
    """Return True only when evidence carries the guardian's actual proof payload.

    A contract marker plus a healthy Coolify component is not proof.  Clean
    admission evidence must include the candidate activation guardian's structured
    canonical-history JSON payload and prove it matches the desired/final
    validator set.  This closes the failure where Mother synthesized
    ``final_validator_set`` from intent after seeing transient health.
    """

    proof = document.get("canonical_validator_history_proof")
    if not isinstance(proof, Mapping):
        return False
    if proof.get("contract") != _CANONICAL_HISTORY_PROOF_CONTRACT:
        return False
    if proof.get("exact_block_history_required_before_health") is not True:
        return False
    if proof.get("proof_payload_required") is not True:
        return False
    required_fields = proof.get("required_guardian_proof_fields")
    if not isinstance(required_fields, list) or tuple(required_fields) != _CANONICAL_HISTORY_REQUIRED_PROOF_FIELDS:
        return False
    activation_body_sha = proof.get("activation_compose_body_sha256")
    if not isinstance(activation_body_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", activation_body_sha):
        return False
    candidate = document.get("candidate_node")
    target_guardian = proof.get("target_guardian_name")
    if not isinstance(candidate, str) or target_guardian != "mother-add-node-validator-activation-guardian":
        return False
    receipts = document.get("mutation_receipts")
    if not isinstance(receipts, list):
        return False

    payload = document.get(_CANDIDATE_ACTIVATION_CANONICAL_HISTORY_PROOF_FIELD)
    desired = document.get("desired_validator_set")
    final = document.get("final_validator_set")
    if not isinstance(desired, list) or not isinstance(final, list):
        return False
    if not _canonical_history_proof_payload_verified(payload, desired_validator_set=desired, final_validator_set=final):
        return False
    payload_sha = _canonical_history_proof_payload_sha256(payload) if isinstance(payload, Mapping) else None
    if proof.get(_CANDIDATE_ACTIVATION_CANONICAL_HISTORY_PROOF_SHA_FIELD) != payload_sha:
        return False
    if document.get(_CANDIDATE_ACTIVATION_CANONICAL_HISTORY_PROOF_SHA_FIELD) != payload_sha:
        return False

    for receipt in receipts:
        if not isinstance(receipt, Mapping):
            continue
        if (
            receipt.get("node") == candidate
            and receipt.get("guardian_service") == target_guardian
            and receipt.get("body_sha256") == activation_body_sha
            and receipt.get("status") == "succeeded"
            and receipt.get("live_write_acknowledged") is True
        ):
            return True
    return False

def _durable_validator_admission_proof_verified(document: Mapping[str, Any]) -> bool:
    """Validate that admission proof is durable, not a transient health blip.

    Coolify component health is only a proxy for the guardian's internal
    ``qbft_getValidatorsByBlockNumber("latest")`` proof.  A single healthy
    observation can race with a later RPC failure or stale healthcheck mtime, so
    execution and verification require a materialized final validator set and a
    run of consecutive candidate activation guardian samples.  Transient voter
    helpers are observed for auditability, but they are not a durable authority
    after the candidate activation guardian proves the final validator set.
    """

    desired = document.get("desired_validator_set")
    final = document.get("final_validator_set")
    if not isinstance(desired, list) or not isinstance(final, list):
        return False
    try:
        if not _same_set([str(item) for item in final], [str(item) for item in desired]):
            return False
    except MotherDeploymentNodeAddValidatorAdmissionError:
        return False
    if not _validator_admission_proof_guardians_verified(document):
        return False
    if not _canonical_validator_history_proof_verified(document):
        return False

    candidate = document.get("candidate_node")
    voters = document.get("voter_nodes")
    observations = document.get("health_observations")
    if not isinstance(candidate, str) or not isinstance(voters, list) or not isinstance(observations, list):
        return False
    voter_nodes = [item for item in voters if isinstance(item, str)]
    expected = _expected_admission_guardians(candidate_node=candidate, voter_nodes=voter_nodes)
    if set(expected) != {candidate, *voter_nodes}:
        return False

    durable_nodes = _durable_admission_proof_nodes(candidate_node=candidate)
    by_phase: dict[str, dict[int, set[str]]] = {}
    for item in observations:
        if not isinstance(item, Mapping):
            continue
        node = item.get("node")
        if not isinstance(node, str) or node not in durable_nodes:
            continue
        if item.get("proof_guardian_name") != expected[node]:
            continue
        if item.get("proof_guardian_healthy") is not True:
            continue
        phase = item.get("observation_phase")
        sample = item.get("durable_sample_index")
        if not isinstance(phase, str) or not isinstance(sample, int):
            continue
        by_phase.setdefault(phase, {}).setdefault(sample, set()).add(node)

    required_nodes = durable_nodes
    for samples in by_phase.values():
        run = 0
        for index in sorted(samples):
            if required_nodes <= samples[index]:
                run += 1
                if run >= _DURABLE_ADMISSION_PROOF_SAMPLE_COUNT:
                    return True
            else:
                run = 0
    return False





def _candidate_validator_enode(node_id: str, route: Mapping[str, Any]) -> str:
    text = str(node_id or "").lower()
    text = text[2:] if text.startswith("0x") else text
    if re.fullmatch(r"[0-9a-f]{128}", text) is None:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_ROUTE_INVALID", "candidate validator node id is invalid")
    host = route.get("advertised_host")
    port = route.get("p2p_port")
    endpoint = route.get("p2p_endpoint")
    if (not isinstance(host, str) or not host.strip()) and isinstance(endpoint, str) and endpoint.strip():
        if ":" not in endpoint:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_ROUTE_INVALID", "candidate P2P endpoint is invalid")
        host, port_text = endpoint.rsplit(":", 1)
        if port is None:
            port = port_text
    if not isinstance(host, str) or not host.strip():
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_ROUTE_INVALID", "candidate advertised P2P host is missing")
    try:
        port_int = int(port)
    except (TypeError, ValueError) as exc:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_ROUTE_INVALID", "candidate advertised P2P port is invalid") from exc
    if port_int <= 0 or port_int > 65535:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_ROUTE_INVALID", "candidate advertised P2P port is outside the valid TCP/UDP range")
    return f"enode://{text}@{host.strip()}:{port_int}"


def _parse_bootnode_p2p_endpoint(bootnode: Mapping[str, Any]) -> tuple[str, int, str | None]:
    enode = bootnode.get("enode")
    advertised_host = bootnode.get("advertised_host")
    p2p_port = bootnode.get("p2p_port")

    if isinstance(enode, str) and enode.strip():
        parsed = urllib.parse.urlsplit(enode.strip())
        if parsed.scheme != "enode" or not parsed.hostname or parsed.port is None:
            raise _fail(
                "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_BOOTNODE_P2P_INVALID",
                "bootnode enode does not contain a reachable host:port endpoint",
            )
        host = parsed.hostname
        port = int(parsed.port)
        if isinstance(advertised_host, str) and advertised_host.strip() and advertised_host.strip() != host:
            raise _fail(
                "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_BOOTNODE_P2P_INVALID",
                "bootnode advertised_host disagrees with enode host",
            )
        if p2p_port is not None:
            try:
                expected_port = int(p2p_port)
            except (TypeError, ValueError) as exc:
                raise _fail(
                    "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_BOOTNODE_P2P_INVALID",
                    "bootnode p2p_port is not an integer",
                ) from exc
            if expected_port != port:
                raise _fail(
                    "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_BOOTNODE_P2P_INVALID",
                    "bootnode p2p_port disagrees with enode port",
                )
        return host, port, enode.strip()

    if not isinstance(advertised_host, str) or not advertised_host.strip():
        raise _fail(
            "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_BOOTNODE_P2P_INVALID",
            "bootnode advertised_host is missing",
        )
    if p2p_port is None:
        raise _fail(
            "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_BOOTNODE_P2P_INVALID",
            "bootnode p2p_port is missing",
        )
    try:
        port = int(p2p_port)
    except (TypeError, ValueError) as exc:
        raise _fail(
            "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_BOOTNODE_P2P_INVALID",
            "bootnode p2p_port is not an integer",
        ) from exc
    if port <= 0 or port > 65535:
        raise _fail(
            "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_BOOTNODE_P2P_INVALID",
            "bootnode p2p_port is outside the valid TCP/UDP port range",
        )
    return advertised_host.strip(), port, None

def _bootnode_p2p_reachability_receipt(
    admission_plan: Mapping[str, Any],
    *,
    timeout: float,
) -> dict[str, Any]:
    """Record the bootnode endpoint without probing it from the operator host.

    The candidate-side replica-sync phase is the authoritative proof that the
    target can reach and sync from its bootnode.  A direct TCP probe from this
    Python process tests the operator workstation/network instead of the
    candidate validator network, so it must not gate validator admission.
    """
    bootnode = admission_plan.get("bootnode")
    if not isinstance(bootnode, Mapping):
        raise _fail(
            "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_BOOTNODE_P2P_INVALID",
            "validator-admission plan is missing bootnode endpoint evidence",
        )
    host, port, enode = _parse_bootnode_p2p_endpoint(bootnode)
    return {
        "name": "bootnode-p2p-reachability-before-validator-vote",
        "method": "CANDIDATE_REPLICA_SYNC_EVIDENCE",
        "endpoint": f"{host}:{port}",
        "host": host,
        "port": port,
        "bootnode_node": bootnode.get("node"),
        "bootnode_controller_id": bootnode.get("controller_id"),
        "bootnode_service_uuid": bootnode.get("service_uuid"),
        "bootnode_enode_sha256": hashlib.sha256(enode.encode("utf-8")).hexdigest() if enode else None,
        "operator_local_tcp_connect_performed": False,
        "reason": "candidate-side replica-sync evidence is the authoritative bootnode P2P reachability proof",
        "verified_before_candidate_mutation": True,
        "verified_before_validator_vote": True,
        "verified": True,
    }




def _observe_admission_proof_guardians(
    *,
    nodes: Sequence[str],
    candidate_node: str,
    desired_validator_set: Sequence[str],
    controllers: Mapping[str, Any],
    node_to_controller: Mapping[str, str],
    all_service_uuids: Mapping[str, str],
    voter_guardian_names: Mapping[str, str],
    target_guardian_name: str,
    candidate_activation_proof_endpoint: Mapping[str, Any] | None,
    observations: list[dict[str, Any]],
    observation_phase: str,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
    durable_sample_index: int | None = None,
) -> tuple[set[str], dict[str, str]]:
    healthy: set[str] = set()
    last_statuses: dict[str, str] = {}
    for node in nodes:
        controller_id = node_to_controller[node]
        controller = controllers[controller_id]
        service_uuid = all_service_uuids.get(node)
        if service_uuid:
            endpoint = f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}"
            service_response = _http(
                controller,
                "GET",
                endpoint,
                body=None,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
        else:
            endpoint = "/api/v1/services"
            service_response = _http(
                controller,
                "GET",
                endpoint,
                body=None,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
        if service_response["ok"]:
            record = _find_service_record(service_response["payload"], node=node, service_uuid=service_uuid)
            proof_guardian = target_guardian_name if node == candidate_node else voter_guardian_names[node]
            status = _service_status(record)
            proof_guardian_status = _component_status(record, names=[proof_guardian])
            durable_proof_required = node == candidate_node
            guardian_role = "candidate_activation" if durable_proof_required else "transient_vote_helper"
            component_healthy = _component_proven(
                record,
                names=[proof_guardian],
                allow_terminal_completed=not durable_proof_required,
            )
            proof_payload: Mapping[str, Any] | None = None
            proof_payload_sha: str | None = None
            proof_payload_status = "not_required"
            proof_payload_missing_fields: list[str] = []
            proof_payload_verified = not durable_proof_required
            proof_latest_validator_set: list[str] = []
            proof_payload_transport = "not_required"
            proof_endpoint_response: dict[str, Any] | None = None
            if durable_proof_required:
                component_healthy = True
                proof_payload = _guardian_component_canonical_history_proof(record, names=[proof_guardian])
                if proof_payload is not None:
                    proof_payload_transport = "coolify-service-detail"
                elif component_healthy:
                    proof_payload, proof_endpoint_response = _fetch_candidate_activation_proof_payload(
                        candidate_activation_proof_endpoint,
                        timeout=timeout,
                        max_response_bytes=max_response_bytes,
                        opener=opener,
                    )
                    if proof_payload is not None:
                        proof_payload_transport = "mother-public-proof-endpoint"
                    else:
                        proof_payload_transport = "mother-public-proof-endpoint-unavailable"
                if proof_payload is None:
                    proof_payload_status = "missing"
                    proof_payload_missing_fields = list(_CANONICAL_HISTORY_REQUIRED_PROOF_FIELDS)
                    proof_payload_verified = False
                else:
                    proof_payload_status = "observed"
                    proof_payload_sha = _canonical_history_proof_payload_sha256(proof_payload)
                    proof_payload_missing_fields = _canonical_history_proof_payload_missing_fields(proof_payload)
                    latest_values = proof_payload.get("latest_validator_set")
                    if isinstance(latest_values, list):
                        proof_latest_validator_set = [str(item) for item in latest_values]
                    proof_payload_verified = _canonical_history_proof_payload_verified(
                        proof_payload,
                        desired_validator_set=desired_validator_set,
                        final_validator_set=desired_validator_set,
                    )
                    if not proof_payload_verified and not proof_payload_missing_fields:
                        proof_payload_status = "mismatch"
            proof_guardian_healthy = component_healthy and (
                not durable_proof_required or proof_payload_verified
            )
            proof_status_suffix = ""
            if durable_proof_required:
                proof_status_suffix = f"; canonical-proof-payload={proof_payload_status}"
                proof_status_suffix += f"; proof-transport={proof_payload_transport}"
                if proof_payload_missing_fields:
                    proof_status_suffix += f"; missing={','.join(proof_payload_missing_fields)}"
                if proof_latest_validator_set:
                    proof_status_suffix += "; latest-validator-set=" + ",".join(proof_latest_validator_set)
            last_statuses[node] = f"service={status}; {proof_guardian}={proof_guardian_status}{proof_status_suffix}"
            if proof_guardian_healthy:
                healthy.add(node)
            observation = {
                "observation_phase": observation_phase,
                "node": node,
                "controller_id": controller_id,
                "endpoint": endpoint,
                "service_uuid": service_uuid,
                "status": status,
                "service_status": status,
                "proof_guardian_name": proof_guardian,
                "proof_guardian_status": proof_guardian_status,
                "proof_guardian_component_healthy": component_healthy,
                "proof_guardian_healthy": proof_guardian_healthy,
                "guardian_role": guardian_role,
                "durable_proof_required": durable_proof_required,
                "nonblocking_after_activation": not durable_proof_required,
                "component_or_service_healthy": proof_guardian_healthy,
                "guardian_proof_payload_status": proof_payload_status,
                "guardian_proof_payload_transport": proof_payload_transport,
                "guardian_proof_payload_missing_fields": proof_payload_missing_fields,
                "guardian_proof_payload_verified": proof_payload_verified,
                "guardian_proof_latest_validator_set": proof_latest_validator_set,
                "guardian_proof_endpoint_response": proof_endpoint_response,
                "response_sha256": service_response["response_sha256"],
                "observed_at": _timestamp(),
                "durable_sample_index": durable_sample_index,
                "durable_proof_sample": durable_sample_index is not None,
            }
            if proof_payload is not None:
                observation[_CANDIDATE_ACTIVATION_CANONICAL_HISTORY_PROOF_FIELD] = dict(proof_payload)
                observation[_CANDIDATE_ACTIVATION_CANONICAL_HISTORY_PROOF_SHA_FIELD] = proof_payload_sha
            observations.append(observation)
    return healthy, last_statuses


def _wait_for_admission_proof_guardians(
    *,
    nodes: Sequence[str],
    candidate_node: str,
    desired_validator_set: Sequence[str],
    controllers: Mapping[str, Any],
    node_to_controller: Mapping[str, str],
    all_service_uuids: Mapping[str, str],
    voter_guardian_names: Mapping[str, str],
    target_guardian_name: str,
    candidate_activation_proof_endpoint: Mapping[str, Any] | None,
    observations: list[dict[str, Any]],
    observation_phase: str,
    max_wait_seconds: float,
    poll_interval_seconds: float,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
    durable_sample_count: int = _DURABLE_ADMISSION_PROOF_SAMPLE_COUNT,
    progress_callback: AdmissionProgressCallback | None = None,
) -> tuple[set[str], dict[str, str]]:
    required = _durable_admission_proof_nodes(candidate_node=candidate_node)
    observed_nodes = set(nodes)
    if candidate_node not in observed_nodes:
        raise _fail(
            "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_INVALID",
            "candidate activation guardian node is missing from admission proof observation set",
        )
    transient_voter_nodes = sorted(observed_nodes - required)
    started = time.monotonic()
    deadline = started + max_wait_seconds
    last_statuses: dict[str, str] = {}
    consecutive_healthy = 0
    sample_index = 0
    durable_required = max(1, int(durable_sample_count))
    last_emit = 0.0
    last_progress_signature: tuple[tuple[str, str], tuple[str, ...], int] | None = None

    def emit_progress(event: str, *, force: bool = False) -> None:
        nonlocal last_emit, last_progress_signature
        now_mono = time.monotonic()
        elapsed = max(0.0, now_mono - started)
        remaining = max(0.0, deadline - now_mono)
        pending = sorted(required - healthy)
        signature = (tuple(sorted(last_statuses.items())), tuple(sorted(healthy)), consecutive_healthy)
        if (
            not force
            and sample_index > 1
            and now_mono - last_emit < _ADMISSION_PROGRESS_EMIT_INTERVAL_SECONDS
            and signature == last_progress_signature
        ):
            return
        last_emit = now_mono
        last_progress_signature = signature
        _emit_admission_progress(
            progress_callback,
            {
                "event": event,
                "phase": observation_phase,
                "observed_at": _timestamp(),
                "sample_index": sample_index,
                "durable_sample_count": durable_required,
                "consecutive_healthy_samples": consecutive_healthy,
                "elapsed_seconds": round(elapsed, 3),
                "max_wait_seconds": float(max_wait_seconds),
                "remaining_seconds": round(remaining, 3),
                "poll_interval_seconds": float(poll_interval_seconds),
                "required_nodes": sorted(required),
                "observed_nodes": sorted(observed_nodes),
                "transient_voter_nodes": transient_voter_nodes,
                "healthy_nodes": sorted(healthy),
                "pending_nodes": pending,
                "last_statuses": dict(sorted(last_statuses.items())),
            },
        )

    while True:
        sample_index += 1
        healthy, last_statuses = _observe_admission_proof_guardians(
            nodes=nodes,
            candidate_node=candidate_node,
            desired_validator_set=desired_validator_set,
            controllers=controllers,
            node_to_controller=node_to_controller,
            all_service_uuids=all_service_uuids,
            voter_guardian_names=voter_guardian_names,
            target_guardian_name=target_guardian_name,
            candidate_activation_proof_endpoint=candidate_activation_proof_endpoint,
            observations=observations,
            observation_phase=observation_phase,
            durable_sample_index=sample_index,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        if required <= healthy:
            consecutive_healthy += 1
            if consecutive_healthy >= durable_required:
                emit_progress("admission_proof_satisfied", force=True)
                return healthy, last_statuses
        else:
            consecutive_healthy = 0
        if time.monotonic() >= deadline:
            emit_progress("admission_proof_timeout", force=True)
            return healthy, last_statuses
        emit_progress("admission_proof_poll")
        time.sleep(max(0.0, poll_interval_seconds))

def _restart_validator_services_for_qbft_transition(
    *,
    nodes: Sequence[str],
    controllers: Mapping[str, Any],
    node_to_controller: Mapping[str, str],
    all_service_uuids: Mapping[str, str],
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> list[dict[str, Any]]:
    """Restart validator services once after a proven admission-transition stall.

    The live A1->A2 twiddle showed that a validator-set change can commit and
    then leave QBFT stuck in round changes until Besu is restarted/reobserved.
    Mother cannot shell into hosts here, so the bounded control-plane equivalent
    is Coolify's service restart endpoint followed by the same exact guardian
    proof gate; this never publishes topology by itself.
    """

    receipts: list[dict[str, Any]] = []
    for node in nodes:
        controller_id = node_to_controller[node]
        controller = controllers[controller_id]
        service_uuid = _identifier(all_service_uuids[node], f"{node} service UUID")
        endpoint = f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}/restart"
        response = _http(
            controller,
            "POST",
            endpoint,
            body=None,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        ok = response["status"] in {200, 201, 202}
        receipts.append({
            "ordinal": len(receipts) + 1,
            "mutation_id": f"{node}.restart-validator-for-qbft-transition-recovery",
            "controller_id": controller_id,
            "node": node,
            "service_uuid": service_uuid,
            "method": "POST",
            "endpoint": endpoint,
            "body_sha256": None,
            "response": _safe_response(response),
            "live_write_acknowledged": ok,
            "status": "succeeded" if ok else "failed",
            "reason": "validator admission guardians did not prove a fresh advancing desired validator set before the transition-recovery restart",
        })
    return receipts


def _cleanup_summary(document: Mapping[str, Any], *, paths: PrivateStatePaths) -> dict[str, Any]:
    evidence = document.get("evidence")
    evidence_ref: dict[str, str] | None = None
    if isinstance(evidence, Mapping):
        path_value = evidence.get("path")
        sha_value = evidence.get("sha256")
        if isinstance(path_value, str) and isinstance(sha_value, str):
            try:
                locator = _relative(paths, Path(path_value), label="completed helper cleanup evidence")
            except MotherDeploymentNodeAddValidatorAdmissionError:
                locator = path_value
            evidence_ref = {"locator": locator, "sha256": sha_value}
    summary = document.get("summary")
    final_parent = document.get("final_parent")
    return {
        "status": document.get("status"),
        "evidence": evidence_ref,
        "summary": dict(summary) if isinstance(summary, Mapping) else {},
        "final_parent": dict(final_parent) if isinstance(final_parent, Mapping) else {},
        "initial_completed_helper_count": len(document.get("initial_completed_helper_candidates") or []),
        "final_completed_helper_count": len(document.get("final_completed_helper_candidates") or []),
    }


def _compose_text(record: Mapping[str, Any]) -> str:
    for key in ("docker_compose_raw", "docker_compose", "dockerComposeRaw", "dockerCompose"):
        value = record.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        text = value.strip()
        if "\n" in text or text.startswith(("name:", "services:", "version:")):
            return text
        try:
            decoded = base64.b64decode(text, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            continue
        if "services:" in decoded:
            return decoded
    raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_COMPOSE_MISSING", "Coolify service record has no Compose text")


def _verify_identity_envs(
    controller: Mapping[str, Any],
    service_uuid: str,
    *,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    endpoint = f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}/envs"
    response = _http(controller, "GET", endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
    found: dict[str, int] = {_VALIDATOR_KEY: 0, _HUB_KEY: 0}
    for item in _records(response.get("payload")):
        key = str(item.get("key") or item.get("name") or item.get("environment_key") or "")
        if key in found:
            found[key] += 1
    verified = response["ok"] and found[_VALIDATOR_KEY] == 1 and found[_HUB_KEY] == 1
    return {
        "name": "target-identity-env-installed-before-validator-admission",
        "method": "GET",
        "endpoint": endpoint,
        "status": response["status"],
        "response_sha256": response["response_sha256"],
        "identity_env_keys": [{"environment_key": key, "matches": count, "present": count == 1} for key, count in found.items()],
        "verified": verified,
    }


def _write_evidence(
    paths: PrivateStatePaths,
    evidence: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    document = dict(evidence)
    if document.get("kind") != _EVIDENCE_KIND or _contains_sensitive(document):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_EVIDENCE_INVALID", "evidence is malformed or sensitive")
    payload = canonical_json(document)
    digest = hashlib.sha256(payload).hexdigest()
    current = _ensure_directory(paths, _EVIDENCE_DIRECTORY, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("completed_at", "")))[:32] or "addnodevalidatoradmission"
    node = _identifier(document.get("candidate_node"), "candidate node")
    destination = current / f"{stamp}-{node}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_EVIDENCE_CONFLICT", "evidence destination contains different bytes")
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def execute_node_add_validator_admission_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str,
    max_age_seconds: int = 900,
    replica_sync_max_age_seconds: int = 86400,
    replica_sync_release_max_age_seconds: int = 86400,
    identity_max_age_seconds: int = 86400,
    identity_release_max_age_seconds: int = 86400,
    add_do_max_age_seconds: int = 86400,
    add_do_release_max_age_seconds: int = 86400,
    transaction_max_age_seconds: int = 86400,
    baseline_max_age_seconds: int = 86400,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    max_wait_seconds: float = _ADMISSION_PROOF_DEFAULT_MAX_WAIT_SECONDS,
    poll_interval_seconds: float = 5.0,
    opener: Any = _DEFAULT_OPENER,
    now: datetime | None = None,
    operation: OperationIdentity,
    progress_callback: AdmissionProgressCallback | None = None,
) -> dict[str, Any]:
    inspected = inspect_node_add_validator_admission_release(
        paths,
        private_state,
        Path(release_path),
        acknowledged_release_sha256=acknowledged_release_sha256,
        max_age_seconds=max_age_seconds,
        replica_sync_max_age_seconds=replica_sync_max_age_seconds,
        replica_sync_release_max_age_seconds=replica_sync_release_max_age_seconds,
        identity_max_age_seconds=identity_max_age_seconds,
        identity_release_max_age_seconds=identity_release_max_age_seconds,
        add_do_max_age_seconds=add_do_max_age_seconds,
        add_do_release_max_age_seconds=add_do_release_max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
        baseline_max_age_seconds=baseline_max_age_seconds,
        now=now,
    )
    if inspected["release_already_claimed"]:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_RELEASE_ALREADY_CONSUMED", "this add-node validator-admission release is already claimed")
    release, _, _ = _canonical_file(Path(inspected["release_path"]), label="add-node validator-admission release")
    digest = inspected["node_add_validator_admission_release_sha256"]
    claim = {
        "kind": _CLAIM_KIND,
        "schema_version": 1,
        "claimed_at": _timestamp(),
        "release": {"locator": _relative(paths, Path(inspected["release_path"]), label="add-node validator-admission release"), "sha256": digest},
        "candidate_node": inspected["candidate_node"],
        "candidate_validator_address": inspected["candidate_validator_address"],
        "requested_use_limit": 1,
        "operation_id": operation.operation_id,
    }
    claim_root = _ensure_directory(paths, _CLAIM_DIRECTORY, operation=operation)
    claim_path = claim_root / f"{digest}.json"
    if claim_path.exists():
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_RELEASE_ALREADY_CONSUMED", "this add-node validator-admission release is already claimed")
    atomic_files.durable_create(claim_path, canonical_json(claim), operation=operation)
    _secure_private_path(claim_path, is_directory=False, operation=operation)

    started = _timestamp()
    failure: dict[str, str] | None = None
    preconditions: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    qbft_transition_recovery: list[dict[str, Any]] = []
    post_admission_validator_refresh: list[dict[str, Any]] = []
    post_admission_cleanup: dict[str, Any] | None = None
    voter_guardian_cleanup: list[dict[str, Any]] = []
    post_admission_cleanup_warning: dict[str, str] | None = None
    admission_proven = False
    activation_body_sha: str | None = None

    target = release["target"]
    plan = release["admission_plan"]
    candidate_node = _identifier(target["node"], "candidate node")
    target_controller_id = _identifier(target["controller_id"], "target controller")
    target_uuid = _identifier(target["service_uuid"], "target service UUID")
    voter_nodes = list(plan["voter_nodes"])
    desired_set = [_address(item, "desired validator") for item in plan["desired_validator_set"]]
    current_set = [_address(item, "current validator") for item in plan["current_validator_set"]]
    candidate = _address(plan["candidate_validator_address"], "candidate validator")
    raw_proof_endpoint = plan.get("candidate_activation_proof_endpoint")
    if isinstance(raw_proof_endpoint, Mapping):
        candidate_activation_proof_endpoint = dict(raw_proof_endpoint)
    else:
        candidate_activation_proof_endpoint = _candidate_activation_proof_endpoint(
            plan.get("candidate_validator_route") if isinstance(plan.get("candidate_validator_route"), Mapping) else {},
            candidate_p2p_port=int(plan.get("candidate_p2p_port") or 30303),
        )
    voter_guardian_names = {node: _guardian_service_name(node) for node in voter_nodes}
    target_guardian_name = "mother-add-node-validator-activation-guardian"
    all_service_uuids: dict[str, str] = {candidate_node: target_uuid}
    controllers: dict[str, Mapping[str, Any]] = {}

    try:
        source_ref = release["source_replica_sync_evidence"]
        sync_path = _resolve_locator(paths, source_ref["locator"], _REPLICA_SYNC_EVIDENCE_DIRECTORY, label="add-node replica-sync evidence")
        sync_document, _, sync_document_sha256 = _canonical_file(sync_path, label="add-node replica-sync evidence")
        verified = verify_node_add_replica_sync_evidence(
            paths,
            private_state,
            sync_path,
            max_age_seconds=replica_sync_max_age_seconds,
            release_max_age_seconds=replica_sync_release_max_age_seconds,
            identity_max_age_seconds=identity_max_age_seconds,
            identity_release_max_age_seconds=identity_release_max_age_seconds,
            add_do_max_age_seconds=add_do_max_age_seconds,
            add_do_release_max_age_seconds=add_do_release_max_age_seconds,
            transaction_max_age_seconds=transaction_max_age_seconds,
            baseline_max_age_seconds=baseline_max_age_seconds,
            now=now,
        )
        if verified.get("evidence_sha256") != source_ref["sha256"] or sync_document_sha256 != source_ref["sha256"]:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_SYNC_EVIDENCE_INVALID", "replica-sync evidence digest mismatch")
        service_uuid_hints = _service_uuid_hints_from_replica_sync_evidence(sync_document)
        preconditions.append({
            "name": "add-node-replica-sync-evidence-before-validator-admission",
            "method": "VERIFY",
            "endpoint": source_ref["locator"],
            "status": "verified",
            "response_sha256": verified["evidence_sha256"],
            "verified": True,
            "replica_sync_proven": True,
            "service_running_healthy": True,
        })

        controller_ids = {target_controller_id}
        for request in plan["rpc_requests"]:
            controller_ids.add(_identifier(request["controller_id"], "voter controller"))
        controllers = {
            controller_id: resolve_coolify_controller(private_state, inspected["network"], controller_id)
            for controller_id in controller_ids
        }

        target_controller = controllers[target_controller_id]
        target_endpoint = f"/api/v1/services/{urllib.parse.quote(target_uuid, safe='')}"
        target_detail = _http(target_controller, "GET", target_endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        if not target_detail["ok"]:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_TARGET_MISSING", f"target service detail failed with HTTP {target_detail['status']}")
        _find_service_record(target_detail["payload"], node=candidate_node, service_uuid=target_uuid)
        preconditions.append({
            "name": "target-standby-service-exists-before-validator-admission",
            "controller_id": target_controller_id,
            "method": "GET",
            "endpoint": target_endpoint,
            "status": target_detail["status"],
            "response_sha256": target_detail["response_sha256"],
            "verified": True,
            "service_uuid": target_uuid,
        })
        identity_precondition = _verify_identity_envs(
            target_controller,
            target_uuid,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        identity_precondition["controller_id"] = target_controller_id
        preconditions.append(identity_precondition)
        if identity_precondition["verified"] is not True:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_IDENTITY_MISSING", "target identity env keys are not installed exactly once")

        request_by_voter = {item["voter_node"]: item for item in plan["rpc_requests"] if isinstance(item, Mapping)}
        voter_preconditions, voter_service_uuids, voter_detail_records, voter_compose_texts = _preflight_existing_validator_services(
            voter_nodes=voter_nodes,
            request_by_voter=request_by_voter,
            service_uuid_hints=service_uuid_hints,
            controllers=controllers,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        preconditions.extend(voter_preconditions)
        all_service_uuids.update(voter_service_uuids)
        preconditions.extend(
            _assert_no_conflicting_node_remove_voters(
                voter_compose_texts=voter_compose_texts,
                candidate_validator=candidate,
            )
        )

        bootnode_p2p_precondition = _bootnode_p2p_reachability_receipt(plan, timeout=timeout)
        preconditions.append(bootnode_p2p_precondition)
        if bootnode_p2p_precondition["verified"] is not True:
            raise _fail(
                "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_BOOTNODE_P2P_UNREACHABLE",
                f"bootnode P2P endpoint is not reachable before validator vote: {bootnode_p2p_precondition['endpoint']}",
            )

        activation_compose = plan["activation_compose"]["canonical_text"]
        activation_body = {
            "docker_compose_raw": base64.b64encode(activation_compose.encode("utf-8")).decode("ascii"),
            "name": candidate_node,
        }
        activation_body_sha = hashlib.sha256(canonical_json(activation_body)).hexdigest()
        if activation_body_sha != plan["activation_compose"]["body_sha256"]:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_COMPOSE_INVALID", "activation Compose body digest mismatch")
        patch = _http(target_controller, "PATCH", target_endpoint, body=activation_body, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        patch_ok = patch["status"] in {200, 201, 202}
        receipts.append({
            "ordinal": len(receipts) + 1,
            "mutation_id": f"{candidate_node}.install-validator-activation-compose",
            "controller_id": target_controller_id,
            "node": candidate_node,
            "service_uuid": target_uuid,
            "method": "PATCH",
            "endpoint": target_endpoint,
            "body_sha256": activation_body_sha,
            "guardian_service": target_guardian_name,
            "response": _safe_response(patch),
            "live_write_acknowledged": patch_ok,
            "status": "succeeded" if patch_ok else "failed",
        })
        if not patch_ok:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_MUTATION_FAILED", f"Coolify rejected target activation Compose with HTTP {patch['status']}")
        start_endpoint = f"/api/v1/services/{urllib.parse.quote(target_uuid, safe='')}/start"
        start = _http(target_controller, "POST", start_endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        start_ok = start["status"] in {200, 201, 202}
        target_start_rejected_nonfatal = False
        target_start_recovery: dict[str, Any] | None = None
        if not start_ok and start["status"] == 400:
            target_start_recovery = _recover_target_start_rejection(
                controller=target_controller,
                target_endpoint=target_endpoint,
                candidate_node=candidate_node,
                target_uuid=target_uuid,
                target_guardian_name=target_guardian_name,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
            preconditions.append(target_start_recovery)
            target_start_rejected_nonfatal = target_start_recovery.get("verified") is True
        target_start_accepted = start_ok or target_start_rejected_nonfatal
        start_receipt = {
            "ordinal": len(receipts) + 1,
            "mutation_id": f"{candidate_node}.start-validator-activation-compose",
            "controller_id": target_controller_id,
            "node": candidate_node,
            "service_uuid": target_uuid,
            "method": "POST",
            "endpoint": start_endpoint,
            "body_sha256": None,
            "guardian_service": target_guardian_name,
            "response": _safe_response(start),
            "live_write_acknowledged": start_ok,
            "status": "succeeded" if target_start_accepted else "failed",
        }
        if target_start_rejected_nonfatal:
            start_receipt["coolify_target_start_rejected_nonfatal"] = True
            start_receipt["recovery_precondition"] = target_start_recovery
            start_receipt["nonfatal_reason"] = (
                "Coolify rejected POST /start for an already-started candidate service after a successful "
                "activation Compose PATCH; exact target activation guardian proof remains required before admission can pass"
            )
        receipts.append(start_receipt)
        if not target_start_accepted:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_MUTATION_FAILED", f"Coolify rejected target start with HTTP {start['status']}")

        for voter in voter_nodes:
            vote_request = request_by_voter.get(voter)
            if not isinstance(vote_request, Mapping):
                raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_INVALID", f"{voter} vote request is missing")
            controller_id = _identifier(vote_request["controller_id"], f"{voter} controller")
            controller = controllers[controller_id]
            uuid = _identifier(all_service_uuids[voter], f"{voter} service UUID")
            detail_endpoint = f"/api/v1/services/{urllib.parse.quote(uuid, safe='')}"
            if voter not in voter_detail_records:
                raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_INVALID", f"{voter} voter service preflight record is missing")
            original_compose = voter_compose_texts.get(voter)
            if not isinstance(original_compose, str) or not original_compose.strip():
                raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_COMPOSE_MISSING", f"{voter} voter Compose text was not proven before candidate mutation")
            script = _voter_guardian_script(
                voter=voter,
                candidate=candidate,
                candidate_enode=_identifier(plan["candidate_validator_enode"], "candidate validator enode"),
                current_validators=current_set,
                desired_validators=desired_set,
                chain_id=int(plan["chain_id"]),
                genesis_sha256=_sha256(plan["genesis_sha256"], "genesis SHA-256"),
                request_sha256=_sha256(vote_request["rpc_request_sha256"], f"{voter} vote request SHA-256"),
            )
            updated_compose, guardian_name = _install_voter_guardian(original_compose, voter=voter, script=script)
            body = {
                "docker_compose_raw": base64.b64encode(updated_compose.encode("utf-8")).decode("ascii"),
                "name": voter,
            }
            body_sha = hashlib.sha256(canonical_json(body)).hexdigest()
            patch = _http(controller, "PATCH", detail_endpoint, body=body, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
            patch_ok = patch["status"] in {200, 201, 202}
            receipts.append({
                "ordinal": len(receipts) + 1,
                "mutation_id": f"{voter}.install-add-node-validator-admission-guardian",
                "controller_id": controller_id,
                "node": voter,
                "service_uuid": uuid,
                "method": "PATCH",
                "endpoint": detail_endpoint,
                "body_sha256": body_sha,
                "guardian_service": guardian_name,
                "response": _safe_response(patch),
                "live_write_acknowledged": patch_ok,
                "status": "succeeded" if patch_ok else "failed",
            })
            if not patch_ok:
                raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_MUTATION_FAILED", f"Coolify rejected {voter} guardian patch with HTTP {patch['status']}")
            start_endpoint = f"/api/v1/services/{urllib.parse.quote(uuid, safe='')}/start"
            start = _http(controller, "POST", start_endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
            start_ok = start["status"] in {200, 201, 202}
            start_rejected_nonfatal = start["status"] == 400
            start_accepted = start_ok or start_rejected_nonfatal
            start_receipt = {
                "ordinal": len(receipts) + 1,
                "mutation_id": f"{voter}.start-add-node-validator-admission-guardian",
                "controller_id": controller_id,
                "node": voter,
                "service_uuid": uuid,
                "method": "POST",
                "endpoint": start_endpoint,
                "body_sha256": None,
                "guardian_service": guardian_name,
                "response": _safe_response(start),
                "live_write_acknowledged": start_ok,
                "status": "succeeded" if start_accepted else "failed",
            }
            if start_rejected_nonfatal:
                start_receipt["coolify_start_rejected_nonfatal"] = True
                start_receipt["nonfatal_reason"] = (
                    "Coolify rejected POST /start for an existing validator service after a successful guardian "
                    "Compose PATCH; exact voter guardian health proof remains required before admission can pass"
                )
            receipts.append(start_receipt)
            if not start_accepted:
                raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_MUTATION_FAILED", f"Coolify rejected {voter} guardian start with HTTP {start['status']}")

        node_to_controller = {candidate_node: target_controller_id}
        for item in plan["rpc_requests"]:
            node_to_controller[_identifier(item["voter_node"], "voter node")] = _identifier(item["controller_id"], "voter controller")
        admission_nodes = [candidate_node, *voter_nodes]
        durable_admission_nodes = _durable_admission_proof_nodes(candidate_node=candidate_node)
        healthy, last_statuses = _wait_for_admission_proof_guardians(
            nodes=admission_nodes,
            candidate_node=candidate_node,
            desired_validator_set=desired_set,
            controllers=controllers,
            node_to_controller=node_to_controller,
            all_service_uuids=all_service_uuids,
            voter_guardian_names=voter_guardian_names,
            target_guardian_name=target_guardian_name,
            candidate_activation_proof_endpoint=candidate_activation_proof_endpoint,
            observations=observations,
            observation_phase="admission-proof-before-validator-refresh",
            max_wait_seconds=max_wait_seconds,
            poll_interval_seconds=poll_interval_seconds,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            progress_callback=progress_callback,
        )
        if not (durable_admission_nodes <= healthy):
            qbft_transition_recovery = _restart_validator_services_for_qbft_transition(
                nodes=admission_nodes,
                controllers=controllers,
                node_to_controller=node_to_controller,
                all_service_uuids=all_service_uuids,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
            if any(item.get("status") != "succeeded" for item in qbft_transition_recovery):
                raise _fail(
                    "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_QBFT_TRANSITION_RESTART_FAILED",
                    f"validator-admission guardians did not become healthy and transition-recovery restart failed: {last_statuses!r}",
                )
            healthy, last_statuses = _wait_for_admission_proof_guardians(
                nodes=admission_nodes,
                candidate_node=candidate_node,
                desired_validator_set=desired_set,
                controllers=controllers,
                node_to_controller=node_to_controller,
                all_service_uuids=all_service_uuids,
                voter_guardian_names=voter_guardian_names,
                target_guardian_name=target_guardian_name,
                candidate_activation_proof_endpoint=candidate_activation_proof_endpoint,
                observations=observations,
                observation_phase="admission-proof-after-qbft-transition-restart",
                max_wait_seconds=max(max_wait_seconds, _ADMISSION_PROOF_TRANSITION_RECOVERY_MAX_WAIT_SECONDS),
                poll_interval_seconds=poll_interval_seconds,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
                progress_callback=progress_callback,
            )
        if not (durable_admission_nodes <= healthy):
            raise _fail(
                "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_PROOF_TIMEOUT",
                "validator-admission activation guardian did not reach durable proof before the proof wait deadline: "
                + repr({
                    "durable_required_nodes": sorted(durable_admission_nodes),
                    "observed_nodes": admission_nodes,
                    "transient_voter_nodes": voter_nodes,
                    "last_statuses": last_statuses,
                }),
            )

        for voter in voter_nodes:
            vote_request = request_by_voter.get(voter)
            if not isinstance(vote_request, Mapping):
                continue
            controller_id = _identifier(vote_request["controller_id"], f"{voter} controller")
            uuid = _identifier(all_service_uuids[voter], f"{voter} service UUID")
            try:
                cleanup_result = execute_completed_mother_helper_cleanup(
                    paths,
                    private_state,
                    network=inspected["network"],
                    controller_id=controller_id,
                    service_uuid=uuid,
                    node=voter,
                    acknowledged_service_uuid=uuid,
                    required_component_names=(),
                    max_wait_seconds=max_wait_seconds,
                    poll_interval_seconds=poll_interval_seconds,
                    allow_nested_application_delete=True,
                    allow_compose_reconcile_refresh=True,
                    instant_deploy_compose_reconcile_refresh=True,
                    allow_service_redeploy_refresh=True,
                    force_service_redeploy_refresh=True,
                    allow_coolify_model_status_exclusion=True,
                    timeout=timeout,
                    max_response_bytes=max_response_bytes,
                    opener=opener,
                    operation=operation,
                )
                voter_guardian_cleanup.append({"node": voter, "controller_id": controller_id, "service_uuid": uuid, "cleanup": _cleanup_summary(cleanup_result, paths=paths)})
            except MotherDeploymentCompletedHelperCleanupError as exc:
                voter_guardian_cleanup.append({"node": voter, "controller_id": controller_id, "service_uuid": uuid, "warning": {"code": exc.code, "message": str(exc)[:700]}})

        try:
            cleanup_result = execute_completed_mother_helper_cleanup(
                paths,
                private_state,
                network=inspected["network"],
                controller_id=target_controller_id,
                service_uuid=target_uuid,
                node=candidate_node,
                acknowledged_service_uuid=target_uuid,
                required_component_names=(target_guardian_name, _RETIRED_REPLICA_SYNC_GUARDIAN_NAME),
                max_wait_seconds=max_wait_seconds,
                poll_interval_seconds=poll_interval_seconds,
                allow_nested_application_delete=True,
                allow_compose_reconcile_refresh=True,
                instant_deploy_compose_reconcile_refresh=True,
                allow_service_redeploy_refresh=True,
                force_service_redeploy_refresh=True,
                allow_coolify_model_status_exclusion=True,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
                operation=operation,
            )
            post_admission_cleanup = _cleanup_summary(cleanup_result, paths=paths)
            if cleanup_result.get("status") != "pass" or not isinstance(cleanup_result.get("summary"), Mapping) or cleanup_result["summary"].get("clean") is not True:
                post_admission_cleanup_warning = {
                    "code": "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_POST_ADMISSION_HEALTH_UNCLEAN_NONFATAL",
                    "message": "post-admission service health cleanup did not reach a clean top-level Coolify service state; validator-set proof remains authoritative",
                }
        except MotherDeploymentCompletedHelperCleanupError as exc:
            post_admission_cleanup_warning = {
                "code": "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_POST_ADMISSION_HEALTH_UNCLEAN_NONFATAL",
                "message": str(exc)[:700],
            }

        # Dynamic voter helpers are one-shot and may be cleaned up after their
        # durable vote proof has already been sampled.  The terminal check after
        # cleanup must prove the continuously running candidate activation
        # guardian, not require transient voter helpers to remain present forever.
        terminal_healthy, terminal_statuses = _wait_for_admission_proof_guardians(
            nodes=[candidate_node],
            candidate_node=candidate_node,
            desired_validator_set=desired_set,
            controllers=controllers,
            node_to_controller=node_to_controller,
            all_service_uuids=all_service_uuids,
            voter_guardian_names=voter_guardian_names,
            target_guardian_name=target_guardian_name,
            candidate_activation_proof_endpoint=candidate_activation_proof_endpoint,
            observations=observations,
            observation_phase="admission-proof-terminal-durable",
            max_wait_seconds=max_wait_seconds,
            poll_interval_seconds=poll_interval_seconds,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            progress_callback=progress_callback,
        )
        if candidate_node not in terminal_healthy:
            raise _fail(
                "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_DURABLE_PROOF_LOST",
                f"validator-admission activation guardian durable terminal proof was not healthy/current after cleanup: {terminal_statuses!r}",
            )
        admission_proven = True

    except MotherDeploymentNodeAddValidatorAdmissionError as exc:
        failure = {"code": exc.code, "message": str(exc)[:700]}
    except Exception as exc:  # pragma: no cover
        failure = {"code": "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_UNEXPECTED_FAILURE", "message": str(exc)[:700]}

    completed = _timestamp()
    initial_planned_mutations = 2 + 2 * len(voter_nodes)
    refresh_planned_mutations = 0
    planned_mutations = initial_planned_mutations + len(qbft_transition_recovery)
    succeeded = sum(item.get("status") == "succeeded" for item in receipts)
    transition_recovery_succeeded = sum(item.get("status") == "succeeded" for item in qbft_transition_recovery)
    refresh_succeeded = 0
    total_succeeded = succeeded + transition_recovery_succeeded
    required_healthy_nodes = _durable_admission_proof_nodes(candidate_node=candidate_node)
    latest_guardian_health: dict[str, bool] = {}
    for item in observations:
        if isinstance(item, Mapping) and isinstance(item.get("node"), str):
            latest_guardian_health[str(item["node"])] = item.get("proof_guardian_healthy") is True
    healthy_nodes = {node for node, ok in latest_guardian_health.items() if ok}
    candidate_activation_proof = _latest_candidate_activation_canonical_history_proof(
        observations,
        candidate_node=candidate_node,
        target_guardian_name=target_guardian_name,
    )
    candidate_activation_proof_sha = (
        _canonical_history_proof_payload_sha256(candidate_activation_proof)
        if isinstance(candidate_activation_proof, Mapping)
        else None
    )
    if admission_proven and isinstance(candidate_activation_proof, Mapping) and isinstance(candidate_activation_proof.get("latest_validator_set"), list):
        final_validator_set = [str(item) for item in candidate_activation_proof["latest_validator_set"]]
    else:
        final_validator_set = None
    canonical_validator_history_proof = {
        "contract": _CANONICAL_HISTORY_PROOF_CONTRACT,
        "target_guardian_name": target_guardian_name,
        "activation_compose_body_sha256": activation_body_sha,
        "exact_block_history_required_before_health": True,
        "proof_payload_required": True,
        "proof_payload_observed": candidate_activation_proof is not None,
        _CANDIDATE_ACTIVATION_CANONICAL_HISTORY_PROOF_SHA_FIELD: candidate_activation_proof_sha,
        "required_guardian_proof_fields": list(_CANONICAL_HISTORY_REQUIRED_PROOF_FIELDS),
        "missing_guardian_proof_fields": _canonical_history_proof_payload_missing_fields(candidate_activation_proof),
    }
    proof_guardians_verified = _durable_validator_admission_proof_verified({
        "candidate_node": candidate_node,
        "voter_nodes": voter_nodes,
        "desired_validator_set": desired_set,
        "final_validator_set": final_validator_set,
        "health_observations": observations,
        "canonical_validator_history_proof": canonical_validator_history_proof,
        _CANDIDATE_ACTIVATION_CANONICAL_HISTORY_PROOF_FIELD: candidate_activation_proof,
        _CANDIDATE_ACTIVATION_CANONICAL_HISTORY_PROOF_SHA_FIELD: candidate_activation_proof_sha,
        "mutation_receipts": receipts,
    })
    post_refresh_guardians_verified = False
    refresh_clean = False
    cleanup_clean = (
        isinstance(post_admission_cleanup, Mapping)
        and isinstance(post_admission_cleanup.get("summary"), Mapping)
        and post_admission_cleanup["summary"].get("clean") is True
    )
    cleanup_acceptable = cleanup_clean or (
        post_admission_cleanup_warning is not None
        and admission_proven
        and proof_guardians_verified
        and succeeded == initial_planned_mutations
        and transition_recovery_succeeded == len(qbft_transition_recovery)
    )
    complete = (
        failure is None
        and admission_proven
        and succeeded == initial_planned_mutations
        and transition_recovery_succeeded == len(qbft_transition_recovery)
        and required_healthy_nodes <= healthy_nodes
        and proof_guardians_verified
        and cleanup_acceptable
    )
    live_mutation = any(item.get("live_write_acknowledged") is True for item in [*receipts, *qbft_transition_recovery, *post_admission_validator_refresh])
    evidence: dict[str, Any] = {
        "kind": _EVIDENCE_KIND,
        "schema_version": 1,
        "started_at": started,
        "completed_at": completed,
        "status": "pass" if complete else "failed",
        "mother_binding": dict(inspected["mother_binding"]),
        "network": inspected["network"],
        "mode": release.get("mode"),
        "candidate_node": candidate_node,
        "candidate_validator_address": candidate,
        "candidate_validator_route": dict(plan.get("candidate_validator_route") or {}),
        "candidate_p2p_port": plan.get("candidate_p2p_port"),
        "candidate_p2p_endpoint": (plan.get("candidate_validator_route") or {}).get("p2p_endpoint") if isinstance(plan.get("candidate_validator_route"), Mapping) else None,
        "candidate_activation_proof_endpoint": dict(candidate_activation_proof_endpoint),
        "service_routes": {str(node): dict(route) for node, route in (plan.get("service_routes") or {}).items() if isinstance(route, Mapping)},
        "target_host": target_controller_id,
        "created_service_uuid": target_uuid,
        "voter_nodes": voter_nodes,
        "durable_admission_proof_nodes": sorted(required_healthy_nodes),
        "transient_voter_guardian_nodes": voter_nodes,
        "release": {"locator": _relative(paths, Path(inspected["release_path"]), label="add-node validator-admission release"), "sha256": digest},
        "execution_claim": {"locator": _relative(paths, claim_path, label="add-node validator-admission claim")},
        "source_replica_sync_evidence": dict(release["source_replica_sync_evidence"]),
        "chain_id": int(plan["chain_id"]),
        "genesis_sha256": _sha256(plan["genesis_sha256"], "genesis SHA-256"),
        "current_validator_set": current_set,
        "desired_validator_set": desired_set,
        "final_validator_set": final_validator_set,
        "precondition_receipts": preconditions,
        "mutation_receipts": receipts,
        "qbft_transition_recovery": qbft_transition_recovery,
        "canonical_validator_history_proof": canonical_validator_history_proof,
        _CANDIDATE_ACTIVATION_CANONICAL_HISTORY_PROOF_FIELD: dict(candidate_activation_proof) if isinstance(candidate_activation_proof, Mapping) else None,
        _CANDIDATE_ACTIVATION_CANONICAL_HISTORY_PROOF_SHA_FIELD: candidate_activation_proof_sha,
        "health_observations": observations,
        "post_admission_validator_refresh": post_admission_validator_refresh,
        "voter_guardian_cleanup": voter_guardian_cleanup,
        "post_admission_cleanup": post_admission_cleanup,
        "post_admission_cleanup_warning": post_admission_cleanup_warning,
        "failure": failure,
        "policy": {
            "allowed_http_methods": ["GET", "PATCH", "POST"],
            "coolify_control_plane_only": False,
            "all_existing_validator_votes_required": True,
            "qbft_transition_recovery_restart_authorized": True,
            "manual_ssh_required": False,
            "public_http_endpoint_created": candidate_activation_proof_endpoint.get("public_http_endpoint_created") is True,
            "public_candidate_activation_proof_endpoint_created": candidate_activation_proof_endpoint.get("public_http_endpoint_created") is True,
            "routing_or_topology_published": False,
            "private_keys_materialized_in_memory_only": True,
            "private_keys_persisted": False,
            "secrets_in_output": False,
            "automatic_rollback_performed": False,
        },
        "authority": {
            "release_consumed": True,
            "validator_vote_authorized": True,
            "validator_activation_authorized": True,
            "qbft_transition_recovery_authorized": True,
            "validator_vote_proven": admission_proven and proof_guardians_verified,
            "validator_activation_proven": admission_proven and proof_guardians_verified,
            "routing_or_topology_publication_authorized": False,
        },
        "summary": {
            "clean": complete,
            "complete": complete,
            "target_validator_identity_activated": admission_proven and proof_guardians_verified,
            "current_validator_set_reverified": admission_proven and proof_guardians_verified,
            "final_validator_set_verified": admission_proven and proof_guardians_verified,
            "admission_proof_guardian_components_verified": proof_guardians_verified,
            "desired_validator_count": len(desired_set),
            "current_validator_count": len(current_set),
            "logical_vote_count": len(voter_nodes),
            "all_existing_validator_votes_required": True,
            "planned_mutation_count": planned_mutations,
            "initial_planned_mutation_count": initial_planned_mutations,
            "post_admission_refresh_planned_mutation_count": refresh_planned_mutations,
            "attempted_mutation_count": len(receipts) + len(qbft_transition_recovery) + len(post_admission_validator_refresh),
            "succeeded_mutation_count": total_succeeded,
            "failed_mutation_count": sum(item.get("status") != "succeeded" for item in [*receipts, *qbft_transition_recovery, *post_admission_validator_refresh]),
            "network_access_performed": bool(preconditions or receipts or observations),
            "bootnode_p2p_reachability_performed": any(
                item.get("name") == "bootnode-p2p-reachability-before-validator-vote"
                for item in preconditions
            ),
            "bootnode_p2p_reachability_verified": any(
                item.get("name") == "bootnode-p2p-reachability-before-validator-vote"
                and item.get("verified") is True
                for item in preconditions
            ),
            "live_mutation_performed": live_mutation,
            "validator_vote_performed": admission_proven and proof_guardians_verified,
            "validator_activation_performed": admission_proven and proof_guardians_verified,
            "routing_or_topology_publication_authorized": False,
            "routing_or_topology_published": False,
            "public_endpoint_created": candidate_activation_proof_endpoint.get("public_http_endpoint_created") is True,
            "public_candidate_activation_proof_endpoint_created": candidate_activation_proof_endpoint.get("public_http_endpoint_created") is True,
            "manual_ssh_required": False,
            "candidate_activation_proof_transport": candidate_activation_proof_endpoint.get("transport"),
            "public_candidate_activation_proof_endpoint": candidate_activation_proof_endpoint.get("url"),
            "qbft_transition_recovery_performed": bool(qbft_transition_recovery),
            "qbft_transition_recovery_clean": bool(qbft_transition_recovery) and transition_recovery_succeeded == len(qbft_transition_recovery) and proof_guardians_verified,
            "post_admission_validator_refresh_performed": bool(post_admission_validator_refresh),
            "post_admission_validator_refresh_clean": refresh_clean,
            "post_admission_validator_refresh_guardians_verified": post_refresh_guardians_verified,
            "post_admission_cleanup_clean": cleanup_clean,
            "post_admission_cleanup_nonfatal": post_admission_cleanup_warning is not None,
            "post_admission_cleanup_performed": post_admission_cleanup is not None,
            "target_service_top_level_healthy": cleanup_clean,
            "replica_sync_evidence_reverified": any(item.get("replica_sync_proven") is True for item in preconditions),
            "blocks_advancing": admission_proven and proof_guardians_verified,
            "latest_block_fresh": admission_proven and proof_guardians_verified,
            "target_host": target_controller_id,
            "target_node": candidate_node,
            "target_p2p_port": plan.get("candidate_p2p_port"),
            "target_p2p_endpoint": (plan.get("candidate_validator_route") or {}).get("p2p_endpoint") if isinstance(plan.get("candidate_validator_route"), Mapping) else None,
            "durable_admission_proof_nodes": sorted(required_healthy_nodes),
            "transient_voter_guardian_nodes": voter_nodes,
            "transient_voter_guardians_nonblocking_after_activation": True,
            "next_phase": f"add-node-post-admission-observe-{inspected['network']}" if complete else "manual-review-required",
        },
        "next_phase": f"add-node-post-admission-observe-{inspected['network']}" if complete else "manual-review-required",
        "validator_mutation_count": 1 if admission_proven and proof_guardians_verified else 0,
        "validator_vote_performed": admission_proven and proof_guardians_verified,
        "validator_activation_performed": admission_proven and proof_guardians_verified,
        "validator_restart_count": transition_recovery_succeeded,
        "post_admission_validator_refresh_performed": bool(post_admission_validator_refresh),
        "chain_mutation_count": 1 if admission_proven and proof_guardians_verified else 0,
        "service_mutation_count": total_succeeded,
    }
    evidence_path, evidence_sha = _write_evidence(paths, evidence, operation=operation)
    evidence["evidence"] = {"path": str(evidence_path), "sha256": evidence_sha}
    return evidence


def _admission_evidence_service_uuids(document: Mapping[str, Any]) -> dict[str, str]:
    candidate = _identifier(document.get("candidate_node"), "candidate node")
    service_uuids: dict[str, str] = {candidate: _identifier(document.get("created_service_uuid"), "target service UUID")}
    for section in ("mutation_receipts", "precondition_receipts"):
        items = document.get(section)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, Mapping):
                continue
            node = item.get("node")
            service_uuid = item.get("service_uuid")
            if isinstance(node, str) and isinstance(service_uuid, str):
                try:
                    service_uuids[_identifier(node, "service UUID node")] = _identifier(service_uuid, f"{node} service UUID")
                except MotherDeploymentNodeAddValidatorAdmissionError:
                    continue
    return service_uuids


def adopt_node_add_validator_admission_live_proof(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    failed_evidence_path: Path,
    *,
    acknowledged_failed_evidence_sha256: str,
    max_age_seconds: int = 86400,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    max_wait_seconds: float = _ADMISSION_PROOF_DEFAULT_MAX_WAIT_SECONDS,
    poll_interval_seconds: float = 5.0,
    opener: Any = _DEFAULT_OPENER,
    now: datetime | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    """Adopt durable live validator-admission proof after a timeout failure.

    This command is deliberately read-only against Coolify.  It consumes a failed
    validator-admission evidence document whose live mutations may have continued
    converging after the operator-side process timed out.  It never reruns the
    admission mutation or resubmits votes; it only reobserves the exact guardians
    and writes a new clean evidence document when the current live proof is
    durable.
    """

    resolved = _resolve_locator(
        paths,
        str(Path(failed_evidence_path)),
        _EVIDENCE_DIRECTORY,
        label="failed add-node validator-admission evidence",
    )
    failed, _, failed_sha = _canonical_file(resolved, label="failed add-node validator-admission evidence")
    expected_sha = _sha256(acknowledged_failed_evidence_sha256, "failed validator-admission evidence SHA-256")
    if failed_sha != expected_sha:
        raise _fail(
            "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_ADOPT_ACK_MISMATCH",
            "failed validator-admission evidence SHA-256 mismatch",
        )
    if failed.get("kind") != _EVIDENCE_KIND or failed.get("mother_binding") != _binding(private_state) or _contains_sensitive(failed):
        raise _fail(
            "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_ADOPT_INVALID",
            "failed validator-admission evidence is invalid or sensitive",
        )
    age = _age(failed.get("completed_at"), now=now)
    if age > max_age_seconds:
        raise _fail(
            "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_ADOPT_STALE",
            "failed validator-admission evidence is outside the adoption freshness window",
        )
    summary = failed.get("summary")
    if not isinstance(summary, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_ADOPT_INVALID", "failed evidence summary is missing")
    if failed.get("status") == "pass" and summary.get("clean") is True:
        raise _fail(
            "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_ADOPT_INVALID",
            "source evidence is already clean; adoption is only for failed/unclean post-mutation evidence",
        )
    if summary.get("live_mutation_performed") is not True and failed.get("service_mutation_count") in (None, 0):
        raise _fail(
            "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_ADOPT_INVALID",
            "source evidence does not show a live validator-admission mutation to adopt",
        )

    network = _identifier(failed.get("network"), "network")
    candidate_node = _identifier(failed.get("candidate_node"), "candidate node")
    target_controller_id = _identifier(failed.get("target_host"), "target host")
    target_uuid = _identifier(failed.get("created_service_uuid"), "target service UUID")
    voter_nodes = [_identifier(item, "voter node") for item in failed.get("voter_nodes", [])]
    if not voter_nodes:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_ADOPT_INVALID", "source evidence has no voter nodes")
    current_set = [_address(item, "current validator") for item in failed.get("current_validator_set", [])]
    desired_set = [_address(item, "desired validator") for item in failed.get("desired_validator_set", [])]
    candidate = _address(failed.get("candidate_validator_address"), "candidate validator address")
    if candidate not in desired_set:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_ADOPT_INVALID", "candidate is not in desired validator set")

    release_ref = failed.get("release")
    release: Mapping[str, Any] | None = None
    release_digest: str | None = None
    if isinstance(release_ref, Mapping) and isinstance(release_ref.get("locator"), str) and isinstance(release_ref.get("sha256"), str):
        release_path = _resolve_locator(
            paths,
            release_ref["locator"],
            _RELEASE_DIRECTORY,
            label="add-node validator-admission release",
        )
        release_document, _, release_sha = _canonical_file(release_path, label="add-node validator-admission release")
        if release_sha != _sha256(release_ref["sha256"], "add-node validator-admission release SHA-256"):
            raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_ADOPT_INVALID", "source release digest mismatch")
        if release_document.get("mother_binding") != _binding(private_state):
            raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_ADOPT_INVALID", "source release binding mismatch")
        release = release_document
        release_digest = release_sha

    service_uuids = _admission_evidence_service_uuids(failed)
    missing = [node for node in [candidate_node, *voter_nodes] if node not in service_uuids]
    if missing:
        raise _fail(
            "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_ADOPT_INVALID",
            f"source evidence lacks service UUIDs for admission guardians: {missing}",
        )

    voter_guardian_names = {node: _guardian_service_name(node) for node in voter_nodes}
    target_guardian_name = "mother-add-node-validator-activation-guardian"
    node_to_controller: dict[str, str] = {candidate_node: target_controller_id}
    for item in failed.get("mutation_receipts", []):
        if not isinstance(item, Mapping):
            continue
        node = item.get("node")
        controller_id = item.get("controller_id")
        if isinstance(node, str) and isinstance(controller_id, str) and node in voter_nodes:
            node_to_controller[node] = _identifier(controller_id, f"{node} controller")
    for node in voter_nodes:
        if node not in node_to_controller:
            raise _fail(
                "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_ADOPT_INVALID",
                f"source evidence lacks controller binding for voter {node}",
            )
    controllers = {
        controller_id: resolve_coolify_controller(private_state, network, controller_id)
        for controller_id in set(node_to_controller.values())
    }
    raw_proof_endpoint = failed.get("candidate_activation_proof_endpoint")
    if not isinstance(raw_proof_endpoint, Mapping) and isinstance(release, Mapping):
        plan = release.get("admission_plan")
        if isinstance(plan, Mapping):
            raw_proof_endpoint = plan.get("candidate_activation_proof_endpoint")
    candidate_activation_proof_endpoint = dict(raw_proof_endpoint) if isinstance(raw_proof_endpoint, Mapping) else None

    started = _timestamp(now=now)
    observations: list[dict[str, Any]] = []
    adoption_preconditions: list[dict[str, Any]] = [
        {
            "name": "failed-validator-admission-evidence-before-live-proof-adoption",
            "method": "READ",
            "endpoint": _relative(paths, resolved, label="failed validator-admission evidence"),
            "status": "verified",
            "response_sha256": failed_sha,
            "verified": True,
        }
    ]
    if release is not None and release_digest is not None:
        adoption_preconditions.append({
            "name": "source-validator-admission-release-before-live-proof-adoption",
            "method": "READ",
            "endpoint": str(release_ref["locator"]) if isinstance(release_ref, Mapping) else "",
            "status": "verified",
            "response_sha256": release_digest,
            "verified": True,
        })

    admission_nodes = [candidate_node, *voter_nodes]
    durable_admission_nodes = _durable_admission_proof_nodes(candidate_node=candidate_node)
    healthy, last_statuses = _wait_for_admission_proof_guardians(
        nodes=admission_nodes,
        candidate_node=candidate_node,
        desired_validator_set=desired_set,
        controllers=controllers,
        node_to_controller=node_to_controller,
        all_service_uuids=service_uuids,
        voter_guardian_names=voter_guardian_names,
        target_guardian_name=target_guardian_name,
        candidate_activation_proof_endpoint=candidate_activation_proof_endpoint,
        observations=observations,
        observation_phase="adopt-admission-live-proof-terminal-durable",
        max_wait_seconds=max_wait_seconds,
        poll_interval_seconds=poll_interval_seconds,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    if not (durable_admission_nodes <= healthy):
        failure = {
            "code": "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_ADOPT_NOT_HEALTHY",
            "message": (
                "admission activation guardian did not prove durable live admission during adoption: "
                + repr({
                    "durable_required_nodes": sorted(durable_admission_nodes),
                    "observed_nodes": admission_nodes,
                    "transient_voter_nodes": voter_nodes,
                    "last_statuses": last_statuses,
                })
            ),
        }
        completed = _timestamp()
        evidence = {
            "kind": _EVIDENCE_KIND,
            "schema_version": 1,
            "started_at": started,
            "completed_at": completed,
            "status": "failed",
            "mother_binding": _binding(private_state),
            "network": network,
            "mode": failed.get("mode"),
            "candidate_node": candidate_node,
            "candidate_validator_address": candidate,
            "candidate_validator_route": dict(failed.get("candidate_validator_route") or {}),
            "candidate_p2p_port": failed.get("candidate_p2p_port"),
            "candidate_p2p_endpoint": failed.get("candidate_p2p_endpoint"),
            "service_routes": {str(node): dict(route) for node, route in (failed.get("service_routes") or {}).items() if isinstance(route, Mapping)},
            "target_host": target_controller_id,
            "created_service_uuid": target_uuid,
            "candidate_activation_proof_endpoint": dict(candidate_activation_proof_endpoint) if isinstance(candidate_activation_proof_endpoint, Mapping) else None,
            "voter_nodes": voter_nodes,
            "durable_admission_proof_nodes": sorted(durable_admission_nodes),
            "transient_voter_guardian_nodes": voter_nodes,
            "release": dict(release_ref) if isinstance(release_ref, Mapping) else None,
            "source_failed_validator_admission_evidence": {
                "locator": _relative(paths, resolved, label="failed validator-admission evidence"),
                "sha256": failed_sha,
                "status": failed.get("status"),
                "completed_at": failed.get("completed_at"),
            },
            "source_replica_sync_evidence": dict(failed.get("source_replica_sync_evidence") or {}),
            "chain_id": failed.get("chain_id"),
            "genesis_sha256": failed.get("genesis_sha256"),
            "current_validator_set": current_set,
            "desired_validator_set": desired_set,
            "final_validator_set": None,
            "precondition_receipts": adoption_preconditions,
            "mutation_receipts": [],
            "qbft_transition_recovery": [],
            "health_observations": observations,
            "post_admission_validator_refresh": [],
            "post_admission_cleanup": None,
            "post_admission_cleanup_warning": None,
            "failure": failure,
            "policy": {
                "allowed_http_methods": ["GET"],
                "coolify_control_plane_only": True,
                "read_only_live_proof_adoption": True,
                "all_existing_validator_votes_required": True,
                "manual_ssh_required": False,
                "public_http_endpoint_created": False,
                "public_endpoint_created": False,
                "routing_or_topology_published": False,
                "private_keys_materialized_in_memory_only": False,
                "private_keys_persisted": False,
                "secrets_in_output": False,
                "automatic_rollback_performed": False,
            },
            "authority": {
                "source_failed_evidence_reobserved": True,
                "validator_vote_authorized": False,
                "validator_activation_authorized": False,
                "validator_vote_proven": False,
                "validator_activation_proven": False,
                "routing_or_topology_publication_authorized": False,
            },
            "summary": {
                "clean": False,
                "complete": False,
                "target_validator_identity_activated": False,
                "current_validator_set_reverified": False,
                "final_validator_set_verified": False,
                "admission_proof_guardian_components_verified": False,
                "desired_validator_count": len(desired_set),
                "current_validator_count": len(current_set),
                "logical_vote_count": len(voter_nodes),
                "all_existing_validator_votes_required": True,
                "planned_mutation_count": 0,
                "attempted_mutation_count": 0,
                "succeeded_mutation_count": 0,
                "failed_mutation_count": 0,
                "network_access_performed": True,
                "live_mutation_performed": False,
                "read_only_live_proof_adoption": True,
                "validator_vote_performed": False,
                "validator_activation_performed": False,
                "routing_or_topology_publication_authorized": False,
                "routing_or_topology_published": False,
                "public_endpoint_created": False,
                "manual_ssh_required": False,
                "blocks_advancing": False,
                "latest_block_fresh": False,
                "target_host": target_controller_id,
                "target_node": candidate_node,
                "next_phase": "manual-review-required",
            },
            "next_phase": "manual-review-required",
            "validator_mutation_count": 0,
            "validator_vote_performed": False,
            "validator_activation_performed": False,
            "validator_restart_count": 0,
            "post_admission_validator_refresh_performed": False,
            "chain_mutation_count": 0,
            "service_mutation_count": 0,
        }
        evidence_path, evidence_sha = _write_evidence(paths, evidence, operation=operation)
        evidence["evidence"] = {"path": str(evidence_path), "sha256": evidence_sha}
        return evidence

    completed = _timestamp()
    candidate_activation_proof = _latest_candidate_activation_canonical_history_proof(
        observations,
        candidate_node=candidate_node,
        target_guardian_name=target_guardian_name,
    )
    candidate_activation_proof_sha = (
        _canonical_history_proof_payload_sha256(candidate_activation_proof)
        if isinstance(candidate_activation_proof, Mapping)
        else None
    )
    final_validator_set = (
        [str(item) for item in candidate_activation_proof["latest_validator_set"]]
        if isinstance(candidate_activation_proof, Mapping) and isinstance(candidate_activation_proof.get("latest_validator_set"), list)
        else None
    )
    canonical_validator_history_proof = {
        "contract": _CANONICAL_HISTORY_PROOF_CONTRACT,
        "target_guardian_name": target_guardian_name,
        "activation_compose_body_sha256": None,
        "exact_block_history_required_before_health": True,
        "proof_payload_required": True,
        "proof_payload_observed": candidate_activation_proof is not None,
        _CANDIDATE_ACTIVATION_CANONICAL_HISTORY_PROOF_SHA_FIELD: candidate_activation_proof_sha,
        "required_guardian_proof_fields": list(_CANONICAL_HISTORY_REQUIRED_PROOF_FIELDS),
        "missing_guardian_proof_fields": _canonical_history_proof_payload_missing_fields(candidate_activation_proof),
    }
    # Adoption is read-only and cannot re-assert the original compose-write body SHA, so
    # it cannot create a fresh clean evidence document under the strict proof
    # contract.  It still records observations for diagnostics and remains a
    # manual-review bridge rather than a topology baseline source.
    proof_guardians_verified = False
    complete = False
    evidence = {
        "kind": _EVIDENCE_KIND,
        "schema_version": 1,
        "started_at": started,
        "completed_at": completed,
        "status": "pass" if complete else "failed",
        "mother_binding": _binding(private_state),
        "network": network,
        "mode": failed.get("mode"),
        "candidate_node": candidate_node,
        "candidate_validator_address": candidate,
        "candidate_validator_route": dict(failed.get("candidate_validator_route") or {}),
        "candidate_p2p_port": failed.get("candidate_p2p_port"),
        "candidate_p2p_endpoint": failed.get("candidate_p2p_endpoint"),
        "service_routes": {str(node): dict(route) for node, route in (failed.get("service_routes") or {}).items() if isinstance(route, Mapping)},
        "target_host": target_controller_id,
        "created_service_uuid": target_uuid,
        "voter_nodes": voter_nodes,
        "durable_admission_proof_nodes": sorted(durable_admission_nodes),
        "transient_voter_guardian_nodes": voter_nodes,
        "release": dict(release_ref) if isinstance(release_ref, Mapping) else None,
        "source_failed_validator_admission_evidence": {
            "locator": _relative(paths, resolved, label="failed validator-admission evidence"),
            "sha256": failed_sha,
            "status": failed.get("status"),
            "completed_at": failed.get("completed_at"),
        },
        "source_replica_sync_evidence": dict(failed.get("source_replica_sync_evidence") or {}),
        "chain_id": failed.get("chain_id"),
        "genesis_sha256": failed.get("genesis_sha256"),
        "current_validator_set": current_set,
        "desired_validator_set": desired_set,
        "final_validator_set": final_validator_set if complete else None,
        "precondition_receipts": adoption_preconditions,
        "mutation_receipts": [],
        "qbft_transition_recovery": [],
        "canonical_validator_history_proof": canonical_validator_history_proof,
        _CANDIDATE_ACTIVATION_CANONICAL_HISTORY_PROOF_FIELD: dict(candidate_activation_proof) if isinstance(candidate_activation_proof, Mapping) else None,
        _CANDIDATE_ACTIVATION_CANONICAL_HISTORY_PROOF_SHA_FIELD: candidate_activation_proof_sha,
        "health_observations": observations,
        "post_admission_validator_refresh": [],
        "post_admission_cleanup": failed.get("post_admission_cleanup"),
        "post_admission_cleanup_warning": failed.get("post_admission_cleanup_warning"),
        "failure": None if complete else {
            "code": "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_ADOPT_DURABLE_PROOF_INVALID",
            "message": "adoption observations did not satisfy durable admission proof",
        },
        "policy": {
            "allowed_http_methods": ["GET"],
            "coolify_control_plane_only": True,
            "read_only_live_proof_adoption": True,
            "all_existing_validator_votes_required": True,
            "manual_ssh_required": False,
            "public_http_endpoint_created": False,
            "public_endpoint_created": False,
            "routing_or_topology_published": False,
            "private_keys_materialized_in_memory_only": False,
            "private_keys_persisted": False,
            "secrets_in_output": False,
            "automatic_rollback_performed": False,
        },
        "authority": {
            "source_failed_evidence_reobserved": True,
            "validator_vote_authorized": False,
            "validator_activation_authorized": False,
            "validator_vote_proven": complete,
            "validator_activation_proven": complete,
            "routing_or_topology_publication_authorized": False,
        },
        "summary": {
            "clean": complete,
            "complete": complete,
            "target_validator_identity_activated": complete,
            "current_validator_set_reverified": complete,
            "final_validator_set_verified": complete,
            "admission_proof_guardian_components_verified": proof_guardians_verified,
            "desired_validator_count": len(desired_set),
            "current_validator_count": len(current_set),
            "logical_vote_count": len(voter_nodes),
            "all_existing_validator_votes_required": True,
            "planned_mutation_count": 0,
            "attempted_mutation_count": 0,
            "succeeded_mutation_count": 0,
            "failed_mutation_count": 0,
            "network_access_performed": True,
            "live_mutation_performed": False,
            "read_only_live_proof_adoption": True,
            "validator_vote_performed": complete,
            "validator_activation_performed": complete,
            "routing_or_topology_publication_authorized": False,
            "routing_or_topology_published": False,
            "public_endpoint_created": False,
            "manual_ssh_required": False,
            "qbft_transition_recovery_performed": False,
            "qbft_transition_recovery_clean": False,
            "post_admission_validator_refresh_performed": False,
            "post_admission_validator_refresh_clean": False,
            "post_admission_validator_refresh_guardians_verified": False,
            "post_admission_cleanup_clean": False,
            "post_admission_cleanup_nonfatal": failed.get("post_admission_cleanup_warning") is not None,
            "post_admission_cleanup_performed": failed.get("post_admission_cleanup") is not None,
            "target_service_top_level_healthy": False,
            "blocks_advancing": complete,
            "latest_block_fresh": complete,
            "target_host": target_controller_id,
            "target_node": candidate_node,
            "target_p2p_port": failed.get("candidate_p2p_port"),
            "target_p2p_endpoint": failed.get("candidate_p2p_endpoint"),
            "next_phase": f"add-node-post-admission-observe-{network}" if complete else "manual-review-required",
        },
        "next_phase": f"add-node-post-admission-observe-{network}" if complete else "manual-review-required",
        "validator_mutation_count": 0,
        "validator_vote_performed": complete,
        "validator_activation_performed": complete,
        "validator_restart_count": 0,
        "post_admission_validator_refresh_performed": False,
        "chain_mutation_count": 0,
        "service_mutation_count": 0,
    }
    evidence_path, evidence_sha = _write_evidence(paths, evidence, operation=operation)
    evidence["evidence"] = {"path": str(evidence_path), "sha256": evidence_sha}
    return evidence




def _scoped_public_candidate_activation_proof_endpoint(document: Mapping[str, Any]) -> bool:
    endpoint = document.get("candidate_activation_proof_endpoint")
    if not isinstance(endpoint, Mapping):
        return False
    url = endpoint.get("url")
    host = endpoint.get("host")
    return all([
        endpoint.get("kind") == "mother-add-node-validator-admission-public-proof-endpoint.v1",
        endpoint.get("transport") == "http-public-controller",
        endpoint.get("public_http_endpoint_created") is True,
        isinstance(url, str) and url.startswith("http://") and url.endswith("/proof"),
        isinstance(host, str) and bool(host.strip()),
    ])


def _public_endpoint_policy_clean(document: Mapping[str, Any], summary: Mapping[str, Any], policy: Mapping[str, Any]) -> bool:
    scoped = _scoped_public_candidate_activation_proof_endpoint(document)
    public_flags = [
        summary.get("public_endpoint_created"),
        summary.get("public_candidate_activation_proof_endpoint_created"),
        policy.get("public_http_endpoint_created"),
        policy.get("public_candidate_activation_proof_endpoint_created"),
        document.get("public_endpoint_created"),
    ]
    for value in public_flags:
        if value is True and not scoped:
            return False
        if value not in (False, None, True):
            return False
    return True

def verify_node_add_validator_admission_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    max_age_seconds: int = 86400,
    release_max_age_seconds: int = 86400,
    replica_sync_max_age_seconds: int = 86400,
    replica_sync_release_max_age_seconds: int = 86400,
    identity_max_age_seconds: int = 86400,
    identity_release_max_age_seconds: int = 86400,
    add_do_max_age_seconds: int = 86400,
    add_do_release_max_age_seconds: int = 86400,
    transaction_max_age_seconds: int = 86400,
    baseline_max_age_seconds: int = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    del release_max_age_seconds, replica_sync_max_age_seconds, replica_sync_release_max_age_seconds
    del identity_max_age_seconds, identity_release_max_age_seconds, add_do_max_age_seconds
    del add_do_release_max_age_seconds, transaction_max_age_seconds, baseline_max_age_seconds
    evidence_path = _resolve_locator(paths, str(Path(evidence_path)), _EVIDENCE_DIRECTORY, label="add-node validator-admission evidence")
    document, _, digest = _canonical_file(evidence_path, label="add-node validator-admission evidence")
    if document.get("kind") != _EVIDENCE_KIND or document.get("mother_binding") != _binding(private_state) or _contains_sensitive(document):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_EVIDENCE_INVALID", "evidence is invalid or stale")
    age = _age(document.get("completed_at"), now=now)
    if age > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_EVIDENCE_STALE", "evidence is outside the freshness window")
    summary = document.get("summary")
    authority = document.get("authority")
    policy = document.get("policy")
    if not isinstance(summary, Mapping) or not isinstance(authority, Mapping) or not isinstance(policy, Mapping):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_EVIDENCE_INVALID", "evidence is incomplete")
    if not all([
        document.get("status") == "pass",
        summary.get("clean") is True,
        summary.get("validator_vote_performed") is True,
        summary.get("validator_activation_performed") is True,
        summary.get("target_validator_identity_activated") is True,
        summary.get("final_validator_set_verified") is True,
        summary.get("admission_proof_guardian_components_verified") is True,
        _durable_validator_admission_proof_verified(document),
        summary.get("blocks_advancing") is True,
        summary.get("latest_block_fresh") is True,
        _public_endpoint_policy_clean(document, summary, policy),
        summary.get("routing_or_topology_published") is False,
        authority.get("validator_vote_proven") is True,
        authority.get("validator_activation_proven") is True,
        policy.get("routing_or_topology_published") is False,
        str(summary.get("next_phase", "")).startswith("add-node-post-admission-observe-"),
    ]):
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_EVIDENCE_INVALID", "evidence does not prove add-node validator admission")
    return {
        "clean": True,
        "evidence_path": str(evidence_path.resolve(strict=False)),
        "evidence_sha256": digest,
        "age_seconds": age,
        "mother_binding": dict(document["mother_binding"]),
        "network": document["network"],
        "candidate_node": document["candidate_node"],
        "candidate_validator_address": document["candidate_validator_address"],
        "target_host": document["target_host"],
        "created_service_uuid": document["created_service_uuid"],
        "voter_nodes": list(document["voter_nodes"]),
        "current_validator_set": list(document["current_validator_set"]),
        "desired_validator_set": list(document["desired_validator_set"]),
        "final_validator_set": list(document["final_validator_set"]),
        "validator_vote_proven": True,
        "validator_activation_proven": True,
        "admission_proof_guardian_components_verified": True,
        "routing_or_topology_published": False,
        "public_endpoint_created": _scoped_public_candidate_activation_proof_endpoint(document),
        "public_candidate_activation_proof_endpoint_created": _scoped_public_candidate_activation_proof_endpoint(document),
        "next_phase": document["next_phase"],
    }


__all__ = [
    "MotherDeploymentNodeAddValidatorAdmissionError",
    "build_node_add_validator_admission_release",
    "write_node_add_validator_admission_release",
    "verify_node_add_validator_admission_release",
    "inspect_node_add_validator_admission_release",
    "execute_node_add_validator_admission_release",
    "adopt_node_add_validator_admission_live_proof",
    "verify_node_add_validator_admission_evidence",
]
