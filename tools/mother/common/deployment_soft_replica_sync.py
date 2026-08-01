"""Internal-only synchronization proof for Mother’s C-side soft replica.

An expiring one-use release binds one successful C-side non-validator deployment
execution to one exact Compose update that adds a non-routable health guardian.
The guardian proves, entirely inside C's Compose network, that the replica uses
the committed genesis and chain ID, has the reserved C node identity, is peered
to A's exact bootnode, is no longer syncing, observes fresh advancing blocks,
and still sees A as the sole QBFT validator.  Mother observes only Coolify's
authenticated control plane and never exposes JSON-RPC or requires SSH.
"""

from __future__ import annotations

import base64
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from . import atomic_files
from .canonical import canonical_json
from .coolify_state import _DEFAULT_MAX_RESPONSE_BYTES, _DEFAULT_OPENER, resolve_coolify_controller
from .deployment_genesis_birth import (
    MotherDeploymentGenesisBirthError,
    _compose_semantic_sha256,
    _match_service_compose,
)
from .deployment_soft_replica import _public_node_id
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_RELEASE_KIND = "main_computer.mother.deployment_soft_replica_sync_release.v1"
_CLAIM_KIND = "main_computer.mother.deployment_soft_replica_sync_execution_claim.v1"
_EVIDENCE_KIND = "main_computer.mother.deployment_soft_replica_sync_evidence.v1"
_RELEASE_DIRECTORY = ("actions", "deployment-soft-replica-sync-releases")
_CLAIM_DIRECTORY = ("actions", "deployment-soft-replica-sync-execution-claims")
_EVIDENCE_DIRECTORY = ("evidence", "deployment-soft-replica-sync")
_EXECUTION_DIRECTORY = ("actions", "deployment-soft-replica-executions")
_REPLICA_RELEASE_DIRECTORY = ("actions", "deployment-soft-replica-releases")
_TRANSACTION_DIRECTORY = ("actions", "deployment-soft-replica-transactions")
_PROOF_IMAGE = "python:3.12-alpine"
_MIN_RELEASE_SECONDS = 30
_MAX_RELEASE_SECONDS = 900


class MotherDeploymentSoftReplicaSyncError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _identifier(value: Any, path: str) -> str:
    if type(value) is not str or not value.strip() or not re.fullmatch(r"[A-Za-z0-9._-]+", value.strip()):
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_INVALID", f"{path} is invalid"
        )
    return value.strip()


def _sha256(value: Any, path: str) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_INVALID", f"{path} must be SHA-256"
        )
    return value


def _parse_utc(value: Any, path: str) -> datetime:
    if type(value) is not str or not value:
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_INVALID", f"{path} must be a UTC timestamp"
        )
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_INVALID", f"{path} is malformed"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_INVALID", f"{path} must be UTC"
        )
    return parsed.astimezone(timezone.utc)


def _timestamp(value: str | None = None) -> str:
    parsed = datetime.now(timezone.utc) if value is None else _parse_utc(value, "created_at")
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _binding(private_state: PrivateStateReadResult) -> dict[str, Any]:
    return {
        "generation": private_state.binding.generation,
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


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


def _root(paths: PrivateStatePaths, parts: tuple[str, str]) -> Path:
    return paths.root / parts[0] / parts[1]


def _ensure_directory(paths: PrivateStatePaths, parts: tuple[str, str], operation: OperationIdentity) -> Path:
    current = paths.root
    for part in parts:
        current /= part
        atomic_files.ensure_durable_directory(current, operation=operation)
        _secure_private_path(current, is_directory=True, operation=operation)
    return current


def _relative(paths: PrivateStatePaths, path: Path, label: str) -> str:
    try:
        return path.resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix()
    except ValueError as exc:
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_PATH_UNSAFE", f"{label} is outside Mother state"
        ) from exc


def _resolve(paths: PrivateStatePaths, locator: Any, label: str) -> Path:
    if type(locator) is not str or not locator or "\\" in locator:
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_INVALID", f"{label} locator must be a relative POSIX path"
        )
    candidate = Path(locator)
    windows = PureWindowsPath(locator)
    if candidate.is_absolute() or windows.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_PATH_UNSAFE", f"{label} locator is unsafe"
        )
    result = (paths.root / candidate).resolve(strict=False)
    try:
        result.relative_to(paths.root.resolve(strict=False))
    except ValueError as exc:
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_PATH_UNSAFE", f"{label} locator escapes Mother state"
        ) from exc
    return result


def _load(path: Path, label: str) -> tuple[dict[str, Any], bytes, str]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_INVALID", f"{label} is not readable canonical JSON"
        ) from exc
    if type(value) is not dict or canonical_json(value) != raw:
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_INVALID", f"{label} is not canonical JSON"
        )
    return value, raw, hashlib.sha256(raw).hexdigest()


def _canonical_under(paths: PrivateStatePaths, path: Path, directory: tuple[str, str], label: str):
    candidate = path.resolve(strict=False)
    try:
        candidate.relative_to(_root(paths, directory).resolve(strict=False))
    except ValueError as exc:
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_PATH_UNSAFE", f"{label} is outside its canonical root"
        ) from exc
    return _load(candidate, label)


def _digest_without(document: Mapping[str, Any], field: str) -> str:
    return hashlib.sha256(canonical_json({key: value for key, value in document.items() if key != field})).hexdigest()


def _private_document(private_state: PrivateStateReadResult) -> Mapping[str, Any]:
    try:
        value = json.loads(private_state.canonical_object_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_STATE_INVALID", "Mother private state cannot be decoded"
        ) from exc
    if not isinstance(value, Mapping):
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_STATE_INVALID", "Mother private state is not a mapping"
        )
    return value


def _node_id_from_enode(value: Any) -> str:
    if type(value) is not str:
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_CHAIN_INVALID", "bootnode enode is missing"
        )
    match = re.fullmatch(r"enode://([0-9a-fA-F]{128})@([^:@/]+):(\d+)", value)
    if match is None or int(match.group(3)) != 30303:
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_CHAIN_INVALID", "bootnode enode is malformed"
        )
    return match.group(1).lower()


def _replica_chain(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    execution_path: Path,
    *,
    execution_max_age_seconds: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    execution, _, execution_sha = _canonical_under(
        paths, Path(execution_path), _EXECUTION_DIRECTORY, "soft replica execution"
    )
    if execution.get("kind") != "main_computer.mother.deployment_soft_replica_execution_result.v1" or execution.get("status") != "pass":
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_EXECUTION_INVALID", "soft replica execution is not a successful canonical result"
        )
    if execution.get("mother_binding") != _binding(private_state):
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_STALE_BINDING", "soft replica execution does not bind current Mother state"
        )
    completed = _parse_utc(execution.get("completed_at"), "completed_at")
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    age = int((reference - completed).total_seconds())
    if age < -1 or age > execution_max_age_seconds:
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_EXECUTION_STALE", "soft replica execution is outside the permitted age"
        )
    summary = execution.get("summary")
    if not isinstance(summary, Mapping) or not all([
        summary.get("complete") is True,
        summary.get("initial_chain_reverified") is True,
        summary.get("initial_node_read_only") is True,
        summary.get("replica_compose_update_succeeded") is True,
        summary.get("replica_deployment_requested") is True,
        summary.get("replica_synchronized") is False,
        summary.get("validator_vote_authorized") is False,
        summary.get("next_phase") == "prove-soft-replica-synchronization-before-validator-admission",
    ]):
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_EXECUTION_INVALID", "soft replica execution does not authorize synchronization proof"
        )
    replica_node = _identifier(execution.get("replica_node"), "replica node")
    if execution.get("nodes") != [replica_node] or replica_node != "mainnetc-super1":
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_EXECUTION_INVALID", "synchronization proof may target only mainnetc-super1"
        )
    release_ref = execution.get("release")
    if not isinstance(release_ref, Mapping):
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_EXECUTION_INVALID", "soft replica release binding is missing"
        )
    release_path = _resolve(paths, release_ref.get("locator"), "soft replica release")
    release, _, release_byte_sha = _canonical_under(
        paths, release_path, _REPLICA_RELEASE_DIRECTORY, "soft replica release"
    )
    release_digest = _digest_without(release, "soft_replica_release_sha256")
    if release.get("soft_replica_release_sha256") != release_digest or release_digest != release_ref.get("sha256"):
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_EXECUTION_INVALID", "soft replica release digest does not match execution"
        )
    if release.get("mother_binding") != _binding(private_state):
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_STALE_BINDING", "soft replica release binding changed"
        )
    plan = release.get("execution_plan")
    initial = release.get("initial_chain_precondition")
    transaction_ref = release.get("transaction")
    if not isinstance(plan, Mapping) or not isinstance(initial, Mapping) or not isinstance(transaction_ref, Mapping):
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_EXECUTION_INVALID", "soft replica release plan is incomplete"
        )
    transaction_path = _resolve(paths, transaction_ref.get("locator"), "soft replica transaction")
    transaction, _, transaction_byte_sha = _canonical_under(
        paths, transaction_path, _TRANSACTION_DIRECTORY, "soft replica transaction"
    )
    transaction_digest = _digest_without(transaction, "soft_replica_transaction_sha256")
    if not all([
        transaction.get("soft_replica_transaction_sha256") == transaction_digest,
        transaction_digest == transaction_ref.get("sha256"),
        transaction_byte_sha == transaction_ref.get("byte_sha256"),
        transaction.get("mother_binding") == _binding(private_state),
    ]):
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_EXECUTION_INVALID", "soft replica transaction binding changed"
        )
    replica = transaction.get("replica")
    chain = transaction.get("initial_chain")
    if not isinstance(replica, Mapping) or not isinstance(chain, Mapping):
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_EXECUTION_INVALID", "soft replica transaction chain data is missing"
        )
    original_compose = plan.get("compose", {}).get("canonical_text")
    if type(original_compose) is not str or hashlib.sha256(original_compose.encode("utf-8")).hexdigest() != execution.get("compose_sha256"):
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_EXECUTION_INVALID", "executed replica Compose commitment changed"
        )
    if execution.get("service_uuid") != plan.get("service_uuid") or execution.get("controller_id") != "coolify-c":
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_EXECUTION_INVALID", "executed replica service binding changed"
        )
    validator_set = chain.get("validator_set")
    if type(validator_set) is not list or len(validator_set) != 1 or re.fullmatch(r"0x[0-9a-f]{40}", str(validator_set[0]).lower()) is None:
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_CHAIN_INVALID", "current validator set is not the one-validator birth set"
        )
    chain_id = chain.get("chain_id")
    if type(chain_id) is not int or chain_id <= 0:
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_CHAIN_INVALID", "chain ID is invalid"
        )
    genesis_ref = transaction.get("genesis_transaction")
    if not isinstance(genesis_ref, Mapping):
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_CHAIN_INVALID", "genesis commitment is missing"
        )
    bootnode = chain.get("bootnode")
    if not isinstance(bootnode, Mapping):
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_CHAIN_INVALID", "bootnode binding is missing"
        )
    bootnode_enode = bootnode.get("enode")
    initial_node_id = _node_id_from_enode(bootnode_enode)
    state = _private_document(private_state)
    try:
        replica_private_key = state["networks"][execution["network"]]["validators"][replica_node]["private_key"]
    except (KeyError, TypeError) as exc:
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_STATE_INVALID", "replica validator identity is missing"
        ) from exc
    try:
        replica_node_id = _public_node_id(replica_private_key)
    except (TypeError, ValueError) as exc:
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_STATE_INVALID", "replica validator identity is invalid"
        ) from exc
    return {
        "execution": execution,
        "execution_path": Path(execution_path).resolve(strict=False),
        "execution_sha256": execution_sha,
        "execution_age_seconds": max(0, age),
        "release_path": release_path,
        "release_sha256": release_digest,
        "release_byte_sha256": release_byte_sha,
        "transaction_path": transaction_path,
        "transaction_sha256": transaction_digest,
        "transaction_byte_sha256": transaction_byte_sha,
        "network": _identifier(execution.get("network"), "network"),
        "initial_node": _identifier(initial.get("node"), "initial node"),
        "initial_controller_id": _identifier(initial.get("controller_id"), "initial controller"),
        "initial_service_uuid": _identifier(initial.get("service_uuid"), "initial service UUID"),
        "initial_proof_compose": dict(initial.get("proof_compose") or {}),
        "replica_node": replica_node,
        "replica_controller_id": "coolify-c",
        "replica_service_uuid": _identifier(plan.get("service_uuid"), "replica service UUID"),
        "original_compose": original_compose,
        "original_compose_sha256": _sha256(execution.get("compose_sha256"), "replica Compose SHA-256"),
        "chain_id": chain_id,
        "genesis_sha256": _sha256(genesis_ref.get("genesis_sha256"), "genesis SHA-256"),
        "validator_set": [str(validator_set[0]).lower()],
        "bootnode_enode": str(bootnode_enode),
        "initial_node_id": initial_node_id,
        "replica_node_id": replica_node_id,
        "replica_validator_address": str(replica.get("validator_address") or "").lower(),
    }


def _sync_script(
    *,
    node: str,
    chain_id: int,
    genesis_sha256: str,
    validator_address: str,
    initial_node_id: str,
    replica_node_id: str,
) -> str:
    return "\n".join([
        "import hashlib, json, os, time, urllib.request",
        f"RPC = 'http://{node}:8545'",
        f"EXPECTED_CHAIN_ID = {chain_id}",
        f"EXPECTED_GENESIS_SHA256 = '{genesis_sha256}'",
        f"EXPECTED_VALIDATOR = '{validator_address.lower()}'",
        f"EXPECTED_INITIAL_NODE_ID = '{initial_node_id.lower()}'",
        f"EXPECTED_REPLICA_NODE_ID = '{replica_node_id.lower()}'",
        "PROOF = '/proof/proof.json'",
        "HEALTHY = '/proof/healthy'",
        "MAX_BLOCK_AGE_SECONDS = 45",
        "def rpc(method, params):",
        "    body = json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}, separators=(',', ':')).encode()",
        "    req = urllib.request.Request(RPC, data=body, headers={'Content-Type':'application/json','Host':'localhost'}, method='POST')",
        "    with urllib.request.urlopen(req, timeout=5) as response:",
        "        value = json.loads(response.read(1048576).decode())",
        "    if value.get('error') is not None or 'result' not in value:",
        "        raise RuntimeError(method + ' failed')",
        "    return value['result']",
        "def normalize_node_id(value):",
        "    text = str(value or '').lower()",
        "    return text[2:] if text.startswith('0x') else text",
        "def prove():",
        "    with open('/config/genesis.json', 'rb') as handle:",
        "        genesis_digest = hashlib.sha256(handle.read()).hexdigest()",
        "    if genesis_digest != EXPECTED_GENESIS_SHA256:",
        "        raise RuntimeError('genesis commitment mismatch')",
        "    chain_id = int(rpc('eth_chainId', []), 16)",
        "    if chain_id != EXPECTED_CHAIN_ID:",
        "        raise RuntimeError('chain id mismatch')",
        "    genesis = rpc('eth_getBlockByNumber', ['0x0', False])",
        "    if not isinstance(genesis, dict) or not genesis.get('hash'):",
        "        raise RuntimeError('genesis block missing')",
        "    local_info = rpc('admin_nodeInfo', [])",
        "    if not isinstance(local_info, dict) or normalize_node_id(local_info.get('id')) != EXPECTED_REPLICA_NODE_ID:",
        "        raise RuntimeError('replica node identity mismatch')",
        "    peers = rpc('admin_peers', [])",
        "    if not isinstance(peers, list) or EXPECTED_INITIAL_NODE_ID not in json.dumps(peers, sort_keys=True).lower():",
        "        raise RuntimeError('expected initial-node peer missing')",
        "    if int(rpc('net_peerCount', []), 16) < 1:",
        "        raise RuntimeError('peer count is zero')",
        "    if rpc('eth_syncing', []) is not False:",
        "        raise RuntimeError('replica is still syncing')",
        "    validators = [str(item).lower() for item in rpc('qbft_getValidatorsByBlockNumber', ['latest'])]",
        "    if validators != [EXPECTED_VALIDATOR]:",
        "        raise RuntimeError('validator set mismatch')",
        "    first = int(rpc('eth_blockNumber', []), 16)",
        "    time.sleep(4)",
        "    second = int(rpc('eth_blockNumber', []), 16)",
        "    if second <= first:",
        "        raise RuntimeError('block height did not advance')",
        "    latest = rpc('eth_getBlockByNumber', ['latest', False])",
        "    if not isinstance(latest, dict) or not latest.get('hash') or int(latest.get('number', '0x0'), 16) < second:",
        "        raise RuntimeError('latest block is missing')",
        "    block_time = int(latest.get('timestamp', '0x0'), 16)",
        "    current_time = int(time.time())",
        "    if block_time > current_time + 15 or current_time - block_time > MAX_BLOCK_AGE_SECONDS:",
        "        raise RuntimeError('latest block is stale')",
        "    proof = {'chain_id':chain_id,'genesis_block_present':True,'genesis_sha256':genesis_digest,'initial_node_peer_verified':True,'replica_node_id':EXPECTED_REPLICA_NODE_ID,'first_block_number':first,'second_block_number':second,'block_advance':second-first,'latest_block_hash':latest['hash'],'latest_block_timestamp':block_time,'syncing':False,'validator_set':[EXPECTED_VALIDATOR],'proved_at':time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        "    temporary = PROOF + '.tmp'",
        "    with open(temporary, 'w', encoding='utf-8') as handle:",
        "        json.dump(proof, handle, sort_keys=True, separators=(',', ':'))",
        "    os.replace(temporary, PROOF)",
        "    with open(HEALTHY, 'w', encoding='ascii') as handle:",
        "        handle.write(str(int(time.time())))",
        "while True:",
        "    try:",
        "        prove()",
        "    except Exception:",
        "        try: os.unlink(HEALTHY)",
        "        except FileNotFoundError: pass",
        "    time.sleep(6)",
        "",
    ])


def _internal_sync_compose(
    original: str,
    *,
    node: str,
    chain_id: int,
    genesis_sha256: str,
    validator_address: str,
    initial_node_id: str,
    replica_node_id: str,
) -> str:
    marker = "\nvolumes:\n"
    if original.count(marker) != 1 or "  mother-replica-sync-guardian:" in original:
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_COMPOSE_UNSUPPORTED",
            "released soft-replica Compose does not match the supported template",
        )
    if "8545:8545" in original:
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_RPC_EXPOSED", "replica Compose must not publish JSON-RPC"
        )
    script = _sync_script(
        node=node,
        chain_id=chain_id,
        genesis_sha256=genesis_sha256,
        validator_address=validator_address,
        initial_node_id=initial_node_id,
        replica_node_id=replica_node_id,
    )
    indented = "\n".join("        " + line for line in script.splitlines())
    guardian = "\n".join([
        "  mother-replica-sync-guardian:",
        f"    image: {_PROOF_IMAGE}",
        "    restart: unless-stopped",
        "    read_only: true",
        "    depends_on:",
        f"      {node}:",
        "        condition: service_started",
        "    command:",
        "      - python",
        "      - -u",
        "      - -c",
        "      - |",
        indented,
        "    healthcheck:",
        "      test:",
        "        - CMD",
        "        - python",
        "        - -c",
        "        - import os,time; p='/proof/healthy'; assert os.path.isfile(p) and time.time()-os.path.getmtime(p) < 45",
        "      interval: 10s",
        "      timeout: 5s",
        "      retries: 18",
        "      start_period: 30s",
        "    volumes:",
        "      - mother-config:/config:ro",
        "      - mother-sync-proof:/proof",
        "",
    ])
    updated = original.replace(marker, "\n" + guardian + marker, 1)
    if updated.count("  mother-data:\n") != 1:
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_COMPOSE_UNSUPPORTED", "replica volume declaration is unsupported"
        )
    updated = updated.replace("  mother-data:\n", "  mother-data:\n  mother-sync-proof:\n", 1)
    guardian_section = updated.split("  mother-replica-sync-guardian:", 1)[1].split("\nvolumes:\n", 1)[0]
    forbidden = ("ports:", "expose:", "traefik.", "domains:", "fqdn:")
    if any(item in guardian_section for item in forbidden):
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_GUARDIAN_EXPOSED",
            "synchronization guardian must not expose a port, URL, or proxy route",
        )
    return updated


def build_soft_replica_sync_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    execution_path: Path,
    *,
    acknowledged_soft_replica_execution_sha256: str,
    selected_nodes: Iterable[str] = (),
    execution_max_age_seconds: int = 86400,
    expires_in_seconds: int = 300,
    created_at: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    chain = _replica_chain(
        paths, private_state, Path(execution_path),
        execution_max_age_seconds=execution_max_age_seconds,
        now=now,
    )
    acknowledged = _sha256(
        acknowledged_soft_replica_execution_sha256,
        "acknowledged soft replica execution SHA-256",
    )
    if acknowledged != chain["execution_sha256"]:
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_ACKNOWLEDGEMENT_MISMATCH",
            "operator acknowledgement does not match the exact soft replica execution",
        )
    requested = tuple(_identifier(item, "selected node") for item in selected_nodes)
    if requested and requested != (chain["replica_node"],):
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_SELECTION_MISMATCH",
            "synchronization proof may target only mainnetc-super1",
        )
    if type(expires_in_seconds) is not int or not _MIN_RELEASE_SECONDS <= expires_in_seconds <= _MAX_RELEASE_SECONDS:
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_TTL_INVALID",
            f"expires_in_seconds must be between {_MIN_RELEASE_SECONDS} and {_MAX_RELEASE_SECONDS}",
        )
    initial_compose = chain["initial_proof_compose"]
    if type(initial_compose.get("canonical_text")) is not str:
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_CHAIN_INVALID", "A proof Compose binding is missing"
        )
    proof_compose = _internal_sync_compose(
        chain["original_compose"],
        node=chain["replica_node"],
        chain_id=chain["chain_id"],
        genesis_sha256=chain["genesis_sha256"],
        validator_address=chain["validator_set"][0],
        initial_node_id=chain["initial_node_id"],
        replica_node_id=chain["replica_node_id"],
    )
    proof_bytes = proof_compose.encode("utf-8")
    proof_sha = hashlib.sha256(proof_bytes).hexdigest()
    original_semantic_sha = _compose_semantic_sha256(chain["original_compose"], "released replica Compose")
    proof_semantic_sha = _compose_semantic_sha256(proof_compose, "released replica synchronization Compose")
    initial_semantic_sha = _compose_semantic_sha256(initial_compose["canonical_text"], "released A proof Compose")
    body = {
        "name": chain["replica_node"],
        "docker_compose_raw": base64.b64encode(proof_bytes).decode("ascii"),
    }
    body_sha = hashlib.sha256(canonical_json(body)).hexdigest()
    created_text = _timestamp(created_at)
    created = _parse_utc(created_text, "created_at")
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    if created > reference + timedelta(seconds=1):
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_INVALID", "release creation time is in the future"
        )
    expires_at = (created + timedelta(seconds=expires_in_seconds)).isoformat(timespec="seconds").replace("+00:00", "Z")
    service_uuid = urllib.parse.quote(chain["replica_service_uuid"], safe="")
    release: dict[str, Any] = {
        "kind": _RELEASE_KIND,
        "schema_version": 1,
        "created_at": created_text,
        "expires_at": expires_at,
        "network": chain["network"],
        "mother_binding": _binding(private_state),
        "staged_scope": "prove-soft-replica-synchronization-before-validator-admission",
        "soft_replica_execution": {
            "locator": _relative(paths, chain["execution_path"], "soft replica execution"),
            "sha256": chain["execution_sha256"],
            "completed_at": chain["execution"]["completed_at"],
        },
        "operator_release": {
            "intent": "install-internal-only-soft-replica-sync-guardian-and-prove-synchronization",
            "acknowledged_soft_replica_execution_sha256": acknowledged,
            "requested_use_limit": 1,
        },
        "initial_chain_precondition": {
            "node": chain["initial_node"],
            "controller_id": chain["initial_controller_id"],
            "service_uuid": chain["initial_service_uuid"],
            "proof_compose": {
                **dict(initial_compose),
                "semantic_sha256": initial_semantic_sha,
            },
            "read_only": True,
        },
        "proof_plan": {
            "replica_node": chain["replica_node"],
            "controller_id": chain["replica_controller_id"],
            "service_uuid": chain["replica_service_uuid"],
            "chain_id": chain["chain_id"],
            "genesis_sha256": chain["genesis_sha256"],
            "validator_set": list(chain["validator_set"]),
            "bootnode_enode": chain["bootnode_enode"],
            "initial_node_id": chain["initial_node_id"],
            "replica_node_id": chain["replica_node_id"],
            "replica_validator_address": chain["replica_validator_address"],
            "original_compose": {
                "sha256": chain["original_compose_sha256"],
                "semantic_sha256": original_semantic_sha,
                "canonical_text": chain["original_compose"],
            },
            "proof_compose": {
                "sha256": proof_sha,
                "semantic_sha256": proof_semantic_sha,
                "byte_length": len(proof_bytes),
                "canonical_text": proof_compose,
                "guardian_image": _PROOF_IMAGE,
                "guardian_public_ports": [],
                "guardian_domains": [],
                "host_rpc_mapping_present": False,
            },
            "preconditions": [
                {"controller_id": "coolify-a", "method": "GET", "endpoint": "/api/v1/services", "assertion": "A remains running:healthy"},
                {"controller_id": "coolify-a", "method": "GET", "endpoint": f"/api/v1/services/{urllib.parse.quote(chain['initial_service_uuid'], safe='')}", "assertion": "A retains the exact birth-proof Compose"},
                {"controller_id": "coolify-c", "method": "GET", "endpoint": f"/api/v1/services/{service_uuid}", "assertion": "C retains the exact executed replica Compose"},
            ],
            "mutations": [
                {"ordinal": 1, "controller_id": "coolify-c", "method": "PATCH", "endpoint": f"/api/v1/services/{service_uuid}", "canonical_request_body": body, "body_sha256": body_sha, "success_statuses": [200, 201, 202]},
                {"ordinal": 2, "controller_id": "coolify-c", "method": "GET", "endpoint": f"/api/v1/deploy?uuid={service_uuid}&force=true", "canonical_request_body": None, "body_sha256": None, "success_statuses": [200, 201, 202]},
            ],
            "proof": {
                "transport": "coolify-control-plane-only",
                "manual_ssh_required": False,
                "public_endpoint_created": False,
                "guardian_internal_only": True,
                "predicates": [
                    "genesis-file-sha256",
                    "chain-id",
                    "genesis-block-present",
                    "reserved-replica-node-id",
                    "exact-initial-node-peer",
                    "peer-count-positive",
                    "eth-syncing-false",
                    "fresh-block-height-advancing",
                    "sole-qbft-validator",
                ],
                "success_signal": "exact C service reports running:healthy under the exact synchronization-proof Compose",
            },
        },
        "authority": {
            "synchronization_proof_authorized": True,
            "validator_vote_authorized": False,
            "validator_activation_authorized": False,
            "live_execution_authorized": False,
            "authorization_source": "explicit-operator-release",
        },
        "policy": {
            "allowed_http_methods": ["GET", "PATCH"],
            "initial_node_read_only": True,
            "replica_node_only": True,
            "manual_ssh_required": False,
            "public_http_endpoint_created": False,
            "host_rpc_mapping_present": False,
            "private_keys_materialized_in_memory_only": True,
            "private_keys_persisted": False,
            "secrets_in_output": False,
            "qbft_vote_performed": False,
        },
        "remaining_blockers": [
            {"code": "MOTHER_DEPLOY_VALIDATOR_ADMISSION_NOT_AUTHORIZED", "message": "synchronization proof does not authorize a QBFT validator-addition vote"},
        ],
        "summary": {
            "release_valid": True,
            "mutation_count": 2,
            "initial_node_read_only": True,
            "replica_node": chain["replica_node"],
            "manual_ssh_required": False,
            "public_http_endpoint_created": False,
            "validator_vote_authorized": False,
            "next_phase_after_apply": "stage-validator-admission-transaction",
        },
        "soft_replica_sync_release_sha256": None,
    }
    release["soft_replica_sync_release_sha256"] = _digest_without(release, "soft_replica_sync_release_sha256")
    if _contains_sensitive(release):
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_INVALID", "synchronization release contains sensitive material"
        )
    return release


def write_soft_replica_sync_release(
    paths: PrivateStatePaths,
    release: Mapping[str, Any],
    *,
    operation: OperationIdentity,
) -> tuple[Path, str]:
    document = dict(release)
    digest = _digest_without(document, "soft_replica_sync_release_sha256")
    if document.get("kind") != _RELEASE_KIND or document.get("soft_replica_sync_release_sha256") != digest or _contains_sensitive(document):
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_INVALID", "synchronization release is malformed"
        )
    payload = canonical_json(document)
    root = _ensure_directory(paths, _RELEASE_DIRECTORY, operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("created_at", "")))[:32] or "replicasync"
    destination = root / f"{stamp}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise MotherDeploymentSoftReplicaSyncError(
                "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_CONFLICT", "synchronization release path contains different bytes"
            )
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def verify_soft_replica_sync_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    execution_max_age_seconds: int = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    release, raw, byte_sha = _canonical_under(paths, Path(release_path), _RELEASE_DIRECTORY, "synchronization release")
    if release.get("kind") != _RELEASE_KIND or release.get("mother_binding") != _binding(private_state) or _contains_sensitive(release):
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_RELEASE_INVALID", "synchronization release kind or binding is invalid"
        )
    digest = _digest_without(release, "soft_replica_sync_release_sha256")
    if release.get("soft_replica_sync_release_sha256") != digest:
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_RELEASE_INVALID", "synchronization release digest does not match"
        )
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    created = _parse_utc(release.get("created_at"), "created_at")
    expires = _parse_utc(release.get("expires_at"), "expires_at")
    if reference < created - timedelta(seconds=1) or reference > expires or int((reference - created).total_seconds()) > max_age_seconds:
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_RELEASE_EXPIRED", "synchronization release is outside its authority window"
        )
    execution_ref = release.get("soft_replica_execution")
    if not isinstance(execution_ref, Mapping):
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_RELEASE_INVALID", "soft replica execution binding is missing"
        )
    execution_path = _resolve(paths, execution_ref.get("locator"), "soft replica execution")
    expected = build_soft_replica_sync_release(
        paths,
        private_state,
        execution_path,
        acknowledged_soft_replica_execution_sha256=_sha256(execution_ref.get("sha256"), "soft replica execution SHA-256"),
        selected_nodes=selected_nodes,
        execution_max_age_seconds=execution_max_age_seconds,
        expires_in_seconds=int((expires - created).total_seconds()),
        created_at=release.get("created_at"),
        now=reference,
    )
    if canonical_json(expected) != raw:
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_RELEASE_INVALID", "synchronization release no longer matches its exact inputs"
        )
    plan = release["proof_plan"]
    return {
        "clean": True,
        "release_path": str(Path(release_path).resolve(strict=False)),
        "soft_replica_sync_release_sha256": digest,
        "byte_sha256": byte_sha,
        "soft_replica_execution_sha256": execution_ref["sha256"],
        "mother_binding": dict(release["mother_binding"]),
        "network": release["network"],
        "nodes": [plan["replica_node"]],
        "initial_node": release["initial_chain_precondition"]["node"],
        "replica_node": plan["replica_node"],
        "controller_id": plan["controller_id"],
        "service_uuid": plan["service_uuid"],
        "chain_id": plan["chain_id"],
        "genesis_sha256": plan["genesis_sha256"],
        "proof_compose_sha256": plan["proof_compose"]["sha256"],
        "mutation_count": len(plan["mutations"]),
        "created_at": release["created_at"],
        "expires_at": release["expires_at"],
        "staged_scope": release["staged_scope"],
        "synchronization_proof_authorized": True,
        "validator_vote_authorized": False,
        "live_execution_authorized": False,
        "manual_ssh_required": False,
        "public_http_endpoint_created": False,
        "remaining_blocker_codes": ["MOTHER_DEPLOY_SOFT_REPLICA_SYNC_EXECUTOR_NOT_RUN", "MOTHER_DEPLOY_VALIDATOR_ADMISSION_NOT_AUTHORIZED"],
    }


def inspect_soft_replica_sync_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    execution_max_age_seconds: int = 86400,
) -> dict[str, Any]:
    verified = verify_soft_replica_sync_release(
        paths,
        private_state,
        Path(release_path),
        selected_nodes=selected_nodes,
        max_age_seconds=max_age_seconds,
        execution_max_age_seconds=execution_max_age_seconds,
    )
    if _sha256(acknowledged_release_sha256, "acknowledged release SHA-256") != verified["soft_replica_sync_release_sha256"]:
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_RELEASE_ACKNOWLEDGEMENT_MISMATCH",
            "synchronization release acknowledgement does not match",
        )
    claim = _root(paths, _CLAIM_DIRECTORY) / f"{verified['soft_replica_sync_release_sha256']}.json"
    return {
        **verified,
        "executor_implemented": True,
        "execute_requested": False,
        "release_already_claimed": claim.exists(),
        "live_execution_authorized": True,
        "network_access_performed": False,
        "live_mutation_performed": False,
        "replica_synchronized": False,
        "initial_node_read_only": True,
        "guardian_internal_only": True,
        "remaining_blocker_codes": ["MOTHER_DEPLOY_VALIDATOR_ADMISSION_NOT_AUTHORIZED"],
    }


def _open(opener: Any, request: urllib.request.Request, timeout: float):
    return opener.open(request, timeout=timeout) if hasattr(opener, "open") else opener(request, timeout=timeout)


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
    data = canonical_json(dict(body)) if body is not None else None
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {controller.api_token}",
        "User-Agent": "main-computer-mother-soft-replica-sync/1",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(controller.base_url + endpoint, data=data, headers=headers, method=method)
    started = time.monotonic()
    try:
        try:
            response = _open(opener, request, timeout)
            status = int(getattr(response, "status", response.getcode()))
            raw = response.read(max_response_bytes + 1)
            response.close()
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            raw = exc.read(max_response_bytes + 1)
    except (urllib.error.URLError, OSError) as exc:
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_REQUEST_FAILED", "Coolify request failed"
        ) from exc
    if len(raw) > max_response_bytes:
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_RESPONSE_TOO_LARGE", "Coolify response is too large"
        )
    try:
        payload: Any = json.loads(raw.decode("utf-8")) if raw.strip() else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = raw.decode("utf-8", errors="replace")
    return {
        "status": status,
        "ok": 200 <= status < 300,
        "payload": payload,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "elapsed_ms": max(0, int((time.monotonic() - started) * 1000)),
    }


def _records(payload: Any) -> list[Mapping[str, Any]]:
    if type(payload) is list:
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        records: list[Mapping[str, Any]] = [payload]
        for key in ("services", "data", "resource", "service"):
            nested = payload.get(key)
            if type(nested) is list:
                records.extend(item for item in nested if isinstance(item, Mapping))
            elif isinstance(nested, Mapping):
                records.append(nested)
        return records
    return []


def _service_record(payload: Any, service_uuid: str, node: str) -> Mapping[str, Any]:
    matches = [
        item for item in _records(payload)
        if str(item.get("uuid") or item.get("id") or "") == service_uuid
        and str(item.get("name") or "") == node
    ]
    if len(matches) != 1:
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_SERVICE_MISMATCH", "Coolify service binding does not match"
        )
    return matches[0]


def _service_status(item: Mapping[str, Any]) -> str:
    return str(item.get("status") or item.get("state") or "")


def _write_document(
    paths: PrivateStatePaths,
    document: Mapping[str, Any],
    operation: OperationIdentity,
) -> tuple[Path, str]:
    value = dict(document)
    if value.get("kind") != _EVIDENCE_KIND or _contains_sensitive(value):
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_INVALID", "synchronization evidence is malformed or sensitive"
        )
    payload = canonical_json(value)
    digest = hashlib.sha256(payload).hexdigest()
    root = _ensure_directory(paths, _EVIDENCE_DIRECTORY, operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(value.get("completed_at", "")))[:32] or "replicasync"
    destination = root / f"{stamp}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise MotherDeploymentSoftReplicaSyncError(
                "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_CONFLICT", "synchronization evidence path contains different bytes"
            )
        return destination, digest
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def execute_soft_replica_sync_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    execution_max_age_seconds: int = 86400,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    max_wait_seconds: float = 240.0,
    poll_interval_seconds: float = 5.0,
    opener: Any = _DEFAULT_OPENER,
    operation: OperationIdentity,
) -> dict[str, Any]:
    inspected = inspect_soft_replica_sync_release(
        paths,
        private_state,
        Path(release_path),
        acknowledged_release_sha256=acknowledged_release_sha256,
        selected_nodes=selected_nodes,
        max_age_seconds=max_age_seconds,
        execution_max_age_seconds=execution_max_age_seconds,
    )
    if inspected["release_already_claimed"]:
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_RELEASE_ALREADY_CONSUMED", "synchronization release already has a claim"
        )
    release, _, _ = _canonical_under(paths, Path(inspected["release_path"]), _RELEASE_DIRECTORY, "synchronization release")
    plan = release["proof_plan"]
    initial = release["initial_chain_precondition"]
    digest = inspected["soft_replica_sync_release_sha256"]
    claim = {
        "kind": _CLAIM_KIND,
        "schema_version": 1,
        "claimed_at": _timestamp(),
        "release": {
            "locator": _relative(paths, Path(inspected["release_path"]), "synchronization release"),
            "sha256": digest,
        },
        "soft_replica_execution_sha256": inspected["soft_replica_execution_sha256"],
        "node": inspected["replica_node"],
        "requested_use_limit": 1,
        "operation_id": operation.operation_id,
    }
    claim_root = _ensure_directory(paths, _CLAIM_DIRECTORY, operation)
    claim_path = claim_root / f"{digest}.json"
    if claim_path.exists():
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_RELEASE_ALREADY_CONSUMED", "synchronization release already has a claim"
        )
    atomic_files.durable_create(claim_path, canonical_json(claim), operation=operation)
    _secure_private_path(claim_path, is_directory=False, operation=operation)

    controller_a = resolve_coolify_controller(private_state, inspected["network"], "coolify-a")
    controller_c = resolve_coolify_controller(private_state, inspected["network"], "coolify-c")
    started = _timestamp()
    preconditions: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    failure: dict[str, str] | None = None
    try:
        a_inventory = _http(controller_a, "GET", "/api/v1/services", body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        a_health = {
            "name": "initial-chain-running-healthy-before-sync-proof",
            "controller_id": "coolify-a",
            "method": "GET",
            "endpoint": "/api/v1/services",
            "status": a_inventory["status"],
            "response_sha256": a_inventory["response_sha256"],
            "verified": False,
        }
        preconditions.append(a_health)
        if not a_inventory["ok"]:
            raise MotherDeploymentSoftReplicaSyncError(
                "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_PRECONDITION_FAILED", "Coolify A service inventory failed"
            )
        a_item = _service_record(a_inventory["payload"], initial["service_uuid"], initial["node"])
        if _service_status(a_item) != "running:healthy":
            raise MotherDeploymentSoftReplicaSyncError(
                "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_INITIAL_CHAIN_UNHEALTHY", "A is not running:healthy"
            )
        a_health.update({"verified": True, "service_status": "running:healthy"})

        a_detail_endpoint = f"/api/v1/services/{urllib.parse.quote(initial['service_uuid'], safe='')}"
        a_detail = _http(controller_a, "GET", a_detail_endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        if not a_detail["ok"]:
            raise MotherDeploymentSoftReplicaSyncError(
                "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_PRECONDITION_FAILED", "Coolify A service detail failed"
            )
        _service_record(a_detail["payload"], initial["service_uuid"], initial["node"])
        try:
            a_binding = _match_service_compose(a_detail["payload"], initial["proof_compose"]["canonical_text"], "A birth-proof Compose")
        except MotherDeploymentGenesisBirthError as exc:
            raise MotherDeploymentSoftReplicaSyncError(
                "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_INITIAL_CHAIN_MISMATCH", str(exc)[:512]
            ) from exc
        preconditions.append({
            "name": "initial-proof-compose-binding-before-sync-proof",
            "controller_id": "coolify-a",
            "method": "GET",
            "endpoint": a_detail_endpoint,
            "status": a_detail["status"],
            "response_sha256": a_detail["response_sha256"],
            "verified": True,
            "binding_mode": a_binding["mode"],
            "semantic_sha256": a_binding["semantic_sha256"],
        })

        c_detail_endpoint = f"/api/v1/services/{urllib.parse.quote(plan['service_uuid'], safe='')}"
        c_detail = _http(controller_c, "GET", c_detail_endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        if not c_detail["ok"]:
            raise MotherDeploymentSoftReplicaSyncError(
                "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_PRECONDITION_FAILED", "Coolify C service detail failed"
            )
        _service_record(c_detail["payload"], plan["service_uuid"], plan["replica_node"])
        try:
            c_binding = _match_service_compose(c_detail["payload"], plan["original_compose"]["canonical_text"], "executed replica Compose")
        except MotherDeploymentGenesisBirthError as exc:
            raise MotherDeploymentSoftReplicaSyncError(
                "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_REPLICA_MISMATCH", str(exc)[:512]
            ) from exc
        preconditions.append({
            "name": "executed-replica-compose-binding",
            "controller_id": "coolify-c",
            "method": "GET",
            "endpoint": c_detail_endpoint,
            "status": c_detail["status"],
            "response_sha256": c_detail["response_sha256"],
            "verified": True,
            "binding_mode": c_binding["mode"],
            "semantic_sha256": c_binding["semantic_sha256"],
        })

        for mutation in plan["mutations"]:
            body = mutation.get("canonical_request_body")
            response = _http(
                controller_c,
                mutation["method"],
                mutation["endpoint"],
                body=dict(body) if isinstance(body, Mapping) else None,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
            ok = response["status"] in mutation["success_statuses"]
            receipts.append({
                "ordinal": mutation["ordinal"],
                "controller_id": "coolify-c",
                "method": mutation["method"],
                "endpoint": mutation["endpoint"],
                "body_sha256": mutation["body_sha256"],
                "status": "succeeded" if ok else "failed",
                "live_write_acknowledged": ok,
                "response": {key: response[key] for key in ("status", "response_sha256", "byte_length", "elapsed_ms")},
            })
            if not ok:
                raise MotherDeploymentSoftReplicaSyncError(
                    "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_MUTATION_FAILED", f"Coolify rejected synchronization mutation {mutation['ordinal']}"
                )

        deadline = time.monotonic() + max_wait_seconds
        healthy = False
        last_status = ""
        while True:
            inventory = _http(controller_c, "GET", "/api/v1/services", body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
            if inventory["ok"]:
                item = _service_record(inventory["payload"], plan["service_uuid"], plan["replica_node"])
                last_status = _service_status(item)
                observations.append({
                    "status": last_status,
                    "response_sha256": inventory["response_sha256"],
                    "observed_at": _timestamp(),
                })
                if last_status == "running:healthy":
                    healthy = True
                    break
            if time.monotonic() >= deadline:
                break
            time.sleep(max(0.0, poll_interval_seconds))
        if not healthy:
            raise MotherDeploymentSoftReplicaSyncError(
                "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_NOT_HEALTHY",
                f"synchronization guardian did not reach running:healthy (last status {last_status!r})",
            )

        c_detail = _http(controller_c, "GET", c_detail_endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        if not c_detail["ok"]:
            raise MotherDeploymentSoftReplicaSyncError(
                "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_POSTCONDITION_FAILED", "Coolify C proof service detail failed"
            )
        _service_record(c_detail["payload"], plan["service_uuid"], plan["replica_node"])
        try:
            proof_binding = _match_service_compose(c_detail["payload"], plan["proof_compose"]["canonical_text"], "replica synchronization-proof Compose")
        except MotherDeploymentGenesisBirthError as exc:
            raise MotherDeploymentSoftReplicaSyncError(
                "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_POSTCONDITION_FAILED", str(exc)[:512]
            ) from exc
        preconditions.append({
            "name": "synchronization-proof-compose-binding",
            "controller_id": "coolify-c",
            "method": "GET",
            "endpoint": c_detail_endpoint,
            "status": c_detail["status"],
            "response_sha256": c_detail["response_sha256"],
            "verified": True,
            "binding_mode": proof_binding["mode"],
            "semantic_sha256": proof_binding["semantic_sha256"],
        })

        a_inventory = _http(controller_a, "GET", "/api/v1/services", body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        if not a_inventory["ok"]:
            raise MotherDeploymentSoftReplicaSyncError(
                "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_POSTCONDITION_FAILED", "Coolify A final service inventory failed"
            )
        a_item = _service_record(a_inventory["payload"], initial["service_uuid"], initial["node"])
        if _service_status(a_item) != "running:healthy":
            raise MotherDeploymentSoftReplicaSyncError(
                "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_INITIAL_CHAIN_UNHEALTHY", "A stopped being running:healthy during proof"
            )
        preconditions.append({
            "name": "initial-chain-running-healthy-after-sync-proof",
            "controller_id": "coolify-a",
            "method": "GET",
            "endpoint": "/api/v1/services",
            "status": a_inventory["status"],
            "response_sha256": a_inventory["response_sha256"],
            "verified": True,
            "service_status": "running:healthy",
        })
        a_detail = _http(controller_a, "GET", a_detail_endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        if not a_detail["ok"]:
            raise MotherDeploymentSoftReplicaSyncError(
                "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_POSTCONDITION_FAILED", "Coolify A final service detail failed"
            )
        _service_record(a_detail["payload"], initial["service_uuid"], initial["node"])
        try:
            a_final_binding = _match_service_compose(a_detail["payload"], initial["proof_compose"]["canonical_text"], "A birth-proof Compose")
        except MotherDeploymentGenesisBirthError as exc:
            raise MotherDeploymentSoftReplicaSyncError(
                "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_INITIAL_CHAIN_MISMATCH", str(exc)[:512]
            ) from exc
        preconditions.append({
            "name": "initial-proof-compose-binding-after-sync-proof",
            "controller_id": "coolify-a",
            "method": "GET",
            "endpoint": a_detail_endpoint,
            "status": a_detail["status"],
            "response_sha256": a_detail["response_sha256"],
            "verified": True,
            "binding_mode": a_final_binding["mode"],
            "semantic_sha256": a_final_binding["semantic_sha256"],
        })
    except MotherDeploymentSoftReplicaSyncError as exc:
        failure = {"code": exc.code, "message": str(exc)[:512]}
    except Exception:
        failure = {
            "code": "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_UNEXPECTED_FAILURE",
            "message": "unexpected synchronization-proof failure",
        }

    completed = _timestamp()
    complete = failure is None and len(receipts) == 2 and all(item["status"] == "succeeded" for item in receipts)
    evidence = {
        "kind": _EVIDENCE_KIND,
        "schema_version": 1,
        "started_at": started,
        "completed_at": completed,
        "status": "pass" if complete else "failed",
        "mother_binding": dict(inspected["mother_binding"]),
        "network": inspected["network"],
        "nodes": [inspected["replica_node"]],
        "initial_node": inspected["initial_node"],
        "replica_node": inspected["replica_node"],
        "controller_id": inspected["controller_id"],
        "service_uuid": inspected["service_uuid"],
        "release": {
            "locator": _relative(paths, Path(inspected["release_path"]), "synchronization release"),
            "sha256": digest,
        },
        "execution_claim": {"locator": _relative(paths, claim_path, "synchronization claim")},
        "soft_replica_execution_sha256": inspected["soft_replica_execution_sha256"],
        "genesis_sha256": inspected["genesis_sha256"],
        "proof_compose_sha256": inspected["proof_compose_sha256"],
        "proof": {
            "mode": "internal-health-assertion-bound-to-exact-compose",
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "host_rpc_mapping_present": False,
            "guardian_internal_only": True,
            "service_status": observations[-1]["status"] if observations else None,
            "predicates_proven_by_guardian": list(plan["proof"]["predicates"]),
            "chain_id": plan["chain_id"],
            "validator_set": list(plan["validator_set"]),
            "bootnode_enode": plan["bootnode_enode"],
            "initial_node_id_sha256": hashlib.sha256(plan["initial_node_id"].encode("ascii")).hexdigest(),
            "replica_node_id_sha256": hashlib.sha256(plan["replica_node_id"].encode("ascii")).hexdigest(),
        },
        "authority": {
            "synchronization_proof_authorized": True,
            "validator_vote_authorized": False,
            "validator_activation_authorized": False,
            "release_consumed": True,
        },
        "policy": {
            "allowed_http_methods": ["GET", "PATCH"],
            "coolify_control_plane_only": True,
            "initial_node_read_only": True,
            "replica_node_only": True,
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "secrets_in_output": False,
            "automatic_rollback_performed": False,
            "qbft_vote_performed": False,
        },
        "precondition_receipts": preconditions,
        "mutation_receipts": receipts,
        "health_observations": observations,
        "failure": failure,
        "summary": {
            "clean": complete,
            "initial_chain_reverified": complete,
            "replica_synchronized": complete,
            "compose_commitment_verified": complete,
            "service_running_healthy": complete,
            "genesis_file_commitment_verified": complete,
            "chain_id_verified": complete,
            "genesis_block_present": complete,
            "replica_node_identity_verified": complete,
            "initial_node_peer_verified": complete,
            "peer_count_positive": complete,
            "sync_complete": complete,
            "blocks_advancing": complete,
            "latest_block_fresh": complete,
            "validator_set_verified": complete,
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "initial_node_read_only": True,
            "validator_vote_authorized": False,
            "validator_activation_authorized": False,
            "network_access_performed": bool(preconditions or receipts or observations),
            "live_mutation_performed": any(item.get("live_write_acknowledged") for item in receipts),
            "complete": complete,
            "next_phase": "stage-validator-admission-transaction" if complete else "manual-review-required",
        },
    }
    evidence_path, evidence_sha = _write_document(paths, evidence, operation)
    evidence["evidence"] = {"path": str(evidence_path), "sha256": evidence_sha}
    return evidence


def verify_soft_replica_sync_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    now: datetime | None = None,
) -> dict[str, Any]:
    evidence, _, evidence_sha = _canonical_under(paths, Path(evidence_path), _EVIDENCE_DIRECTORY, "synchronization evidence")
    if evidence.get("kind") != _EVIDENCE_KIND or evidence.get("status") != "pass" or evidence.get("mother_binding") != _binding(private_state):
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_EVIDENCE_INVALID", "synchronization evidence is not a clean current result"
        )
    summary = evidence.get("summary")
    proof = evidence.get("proof")
    authority = evidence.get("authority")
    if not isinstance(summary, Mapping) or not isinstance(proof, Mapping) or not isinstance(authority, Mapping) or not all([
        summary.get("clean") is True,
        summary.get("initial_chain_reverified") is True,
        summary.get("replica_synchronized") is True,
        summary.get("service_running_healthy") is True,
        summary.get("replica_node_identity_verified") is True,
        summary.get("initial_node_peer_verified") is True,
        summary.get("sync_complete") is True,
        summary.get("blocks_advancing") is True,
        summary.get("latest_block_fresh") is True,
        summary.get("validator_set_verified") is True,
        summary.get("manual_ssh_required") is False,
        summary.get("public_endpoint_created") is False,
        summary.get("initial_node_read_only") is True,
        summary.get("validator_vote_authorized") is False,
        summary.get("next_phase") == "stage-validator-admission-transaction",
        proof.get("guardian_internal_only") is True,
        proof.get("host_rpc_mapping_present") is False,
        proof.get("service_status") == "running:healthy",
        authority.get("validator_vote_authorized") is False,
    ]):
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_EVIDENCE_INVALID", "synchronization evidence assertions are incomplete"
        )
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    completed = _parse_utc(evidence.get("completed_at"), "completed_at")
    age = int((reference - completed).total_seconds())
    if age < -1 or age > max_age_seconds:
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_EVIDENCE_STALE", "synchronization evidence is outside the freshness window"
        )
    node = _identifier(evidence.get("replica_node"), "replica node")
    requested = tuple(_identifier(item, "selected node") for item in selected_nodes)
    if requested and requested != (node,):
        raise MotherDeploymentSoftReplicaSyncError(
            "MOTHER_DEPLOY_SOFT_REPLICA_SYNC_SELECTION_MISMATCH", "synchronization evidence targets only mainnetc-super1"
        )
    return {
        "clean": True,
        "evidence_path": str(Path(evidence_path).resolve(strict=False)),
        "evidence_sha256": evidence_sha,
        "age_seconds": max(0, age),
        "mother_binding": dict(evidence["mother_binding"]),
        "network": evidence["network"],
        "nodes": [node],
        "initial_node": evidence["initial_node"],
        "replica_node": node,
        "chain_id": proof["chain_id"],
        "genesis_sha256": evidence["genesis_sha256"],
        "validator_set": list(proof["validator_set"]),
        "replica_synchronized": True,
        "initial_chain_reverified": True,
        "manual_ssh_required": False,
        "public_endpoint_created": False,
        "guardian_internal_only": True,
        "validator_vote_authorized": False,
        "next_phase": "stage-validator-admission-transaction",
    }


__all__ = [
    "MotherDeploymentSoftReplicaSyncError",
    "build_soft_replica_sync_release",
    "execute_soft_replica_sync_release",
    "inspect_soft_replica_sync_release",
    "verify_soft_replica_sync_evidence",
    "verify_soft_replica_sync_release",
    "write_soft_replica_sync_release",
]
