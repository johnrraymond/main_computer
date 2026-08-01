"""One-use recovery for a stalled two-validator QBFT admission.

This phase is deliberately separate from the admission vote.  It never casts a
vote.  It resets the QBFT round timers by restarting C first and A second,
while binding both services to exact internal-only Compose documents.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
import time
from typing import Any
import urllib.parse

from . import atomic_files
from .canonical import canonical_json
from .coolify_state import _DEFAULT_MAX_RESPONSE_BYTES, _DEFAULT_OPENER, resolve_coolify_controller
from .deployment_genesis_birth import MotherDeploymentGenesisBirthError, _compose_semantic_sha256, _match_service_compose
from .deployment_validator_admission_release import build_validator_admission_release
from .deployment_validator_admission_executor import _http, _service_record, _service_status
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path

_RELEASE_KIND = "main_computer.mother.deployment_validator_quorum_recovery_release.v1"
_CLAIM_KIND = "main_computer.mother.deployment_validator_quorum_recovery_execution_claim.v1"
_EVIDENCE_KIND = "main_computer.mother.deployment_validator_quorum_recovery_evidence.v1"
_RELEASE_DIRECTORY = ("actions", "deployment-validator-quorum-recovery-releases")
_CLAIM_DIRECTORY = ("actions", "deployment-validator-quorum-recovery-execution-claims")
_EVIDENCE_DIRECTORY = ("evidence", "deployment-validator-quorum-recovery")
_TRANSACTION_DIRECTORY = ("actions", "deployment-validator-admission-transactions")
_PROOF_IMAGE = "python:3.12-alpine"
_MIN_RELEASE_SECONDS = 30
_MAX_RELEASE_SECONDS = 900


class MotherDeploymentValidatorQuorumRecoveryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _identifier(value: Any, path: str) -> str:
    if type(value) is not str or not value.strip() or re.fullmatch(r"[A-Za-z0-9._-]+", value.strip()) is None:
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_INVALID", f"{path} is invalid"
        )
    return value.strip()


def _sha256(value: Any, path: str) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_INVALID", f"{path} must be SHA-256"
        )
    return value


def _address(value: Any, path: str) -> str:
    if type(value) is not str or re.fullmatch(r"0x[0-9a-fA-F]{40}", value) is None:
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_INVALID", f"{path} must be an Ethereum address"
        )
    return value.lower()


def _node_id(value: Any, path: str) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-fA-F]{128}", value) is None:
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_INVALID", f"{path} must be a 128-hex node ID"
        )
    return value.lower()


def _parse_utc(value: Any, path: str) -> datetime:
    if type(value) is not str or not value:
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_INVALID", f"{path} must be UTC"
        )
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_INVALID", f"{path} is malformed"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_INVALID", f"{path} must be UTC"
        )
    return parsed.astimezone(timezone.utc)


def _timestamp(value: str | None = None) -> str:
    parsed = datetime.now(timezone.utc) if value is None else _parse_utc(value, "created_at")
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _duration(value: int) -> int:
    if type(value) is not int or isinstance(value, bool) or not _MIN_RELEASE_SECONDS <= value <= _MAX_RELEASE_SECONDS:
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_TTL_INVALID",
            f"expires_in_seconds must be between {_MIN_RELEASE_SECONDS} and {_MAX_RELEASE_SECONDS}",
        )
    return value


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


def _safe_message(value: Any) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    return text[:300] or "operation failed"


def _digest_without(document: Mapping[str, Any], field: str) -> str:
    return hashlib.sha256(canonical_json({key: value for key, value in document.items() if key != field})).hexdigest()


def _root(paths: PrivateStatePaths, parts: tuple[str, str]) -> Path:
    return paths.root / parts[0] / parts[1]


def _ensure_root(paths: PrivateStatePaths, parts: tuple[str, str], operation: OperationIdentity) -> Path:
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
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_PATH_UNSAFE", f"{label} is outside Mother state"
        ) from exc


def _resolve(paths: PrivateStatePaths, locator: Any, directory: tuple[str, str], label: str) -> Path:
    if type(locator) is not str or not locator or "\\" in locator:
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_PATH_UNSAFE", f"{label} locator is unsafe"
        )
    candidate = Path(locator)
    if candidate.is_absolute() or PureWindowsPath(locator).is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_PATH_UNSAFE", f"{label} locator is unsafe"
        )
    result = (paths.root / candidate).resolve(strict=False)
    expected = _root(paths, directory).resolve(strict=False)
    try:
        result.relative_to(expected)
    except ValueError as exc:
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_PATH_UNSAFE", f"{label} is outside its canonical directory"
        ) from exc
    return result


def _canonical_under(
    paths: PrivateStatePaths,
    path: Path,
    directory: tuple[str, str],
    label: str,
) -> tuple[dict[str, Any], bytes, str]:
    candidate = path.resolve(strict=False)
    expected = _root(paths, directory).resolve(strict=False)
    try:
        candidate.relative_to(expected)
        raw = candidate.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_INVALID", f"{label} is unreadable or outside its canonical directory"
        ) from exc
    if type(value) is not dict or canonical_json(value) != raw:
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_INVALID", f"{label} is not canonical JSON"
        )
    return value, raw, hashlib.sha256(raw).hexdigest()


def _replica_readiness_script(
    *, node: str, chain_id: int, genesis_sha256: str, validators: list[str], initial_node_id: str, replica_node_id: str
) -> str:
    desired = json.dumps(sorted(validators), separators=(",", ":"))
    return "\n".join([
        "import hashlib, json, os, time, traceback, urllib.request",
        f"RPC = 'http://{node}:8545'",
        f"EXPECTED_CHAIN_ID = {chain_id}",
        f"EXPECTED_GENESIS_SHA256 = '{genesis_sha256}'",
        f"EXPECTED_VALIDATORS = {desired}",
        f"EXPECTED_INITIAL_NODE_ID = '{initial_node_id}'",
        f"EXPECTED_REPLICA_NODE_ID = '{replica_node_id}'",
        "PROOF = '/proof/quorum-recovery-ready.json'",
        "HEALTHY = '/proof/quorum-recovery-ready'",
        "def rpc(method, params):",
        "    body = json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}, separators=(',', ':')).encode()",
        "    req = urllib.request.Request(RPC, data=body, headers={'Content-Type':'application/json','Host':'localhost'}, method='POST')",
        "    with urllib.request.urlopen(req, timeout=5) as response:",
        "        value = json.loads(response.read(1048576).decode())",
        "    if value.get('error') is not None or 'result' not in value: raise RuntimeError(method + ' failed')",
        "    return value['result']",
        "def norm(value):",
        "    text = str(value or '').lower()",
        "    return text[2:] if text.startswith('0x') else text",
        "def prove():",
        "    with open('/config/genesis.json', 'rb') as handle: digest = hashlib.sha256(handle.read()).hexdigest()",
        "    if digest != EXPECTED_GENESIS_SHA256: raise RuntimeError('genesis commitment mismatch')",
        "    if int(rpc('eth_chainId', []), 16) != EXPECTED_CHAIN_ID: raise RuntimeError('chain id mismatch')",
        "    genesis = rpc('eth_getBlockByNumber', ['0x0', False])",
        "    if not isinstance(genesis, dict) or not genesis.get('hash'): raise RuntimeError('genesis block missing')",
        "    info = rpc('admin_nodeInfo', [])",
        "    if not isinstance(info, dict) or norm(info.get('id')) != EXPECTED_REPLICA_NODE_ID: raise RuntimeError('replica node identity mismatch')",
        "    peers = rpc('admin_peers', [])",
        "    if not isinstance(peers, list) or EXPECTED_INITIAL_NODE_ID not in json.dumps(peers, sort_keys=True).lower(): raise RuntimeError('initial peer missing')",
        "    if int(rpc('net_peerCount', []), 16) < 1: raise RuntimeError('peer count is zero')",
        "    if rpc('eth_syncing', []) is not False: raise RuntimeError('replica is syncing')",
        "    current = sorted(set(str(item).lower() for item in rpc('qbft_getValidatorsByBlockNumber', ['latest'])))",
        "    if current != EXPECTED_VALIDATORS: raise RuntimeError('validator set mismatch')",
        "    proof = {'chain_id':EXPECTED_CHAIN_ID,'genesis_sha256':digest,'validator_set':current,'initial_node_peer_verified':True,'replica_node_id':EXPECTED_REPLICA_NODE_ID,'ready_for_quorum_reset':True,'proved_at':time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        "    temporary = PROOF + '.tmp'",
        "    with open(temporary, 'w', encoding='utf-8') as handle: json.dump(proof, handle, sort_keys=True, separators=(',', ':'))",
        "    os.replace(temporary, PROOF)",
        "    with open(HEALTHY, 'w', encoding='ascii') as handle: handle.write(str(int(time.time())))",
        "try: os.unlink(HEALTHY)",
        "except FileNotFoundError: pass",
        "while True:",
        "    try: prove()",
        "    except Exception:",
        "        traceback.print_exc()",
        "        try: os.unlink(HEALTHY)",
        "        except FileNotFoundError: pass",
        "    time.sleep(6)",
        "",
    ])


def _initial_quorum_script(
    *, node: str, chain_id: int, genesis_sha256: str, validators: list[str], candidate_node_id: str, rpc_request_sha256: str
) -> str:
    desired = json.dumps(sorted(validators), separators=(",", ":"))
    return "\n".join([
        "import hashlib, json, os, time, traceback, urllib.request",
        f"RPC = 'http://{node}:8545'",
        f"EXPECTED_CHAIN_ID = {chain_id}",
        f"EXPECTED_GENESIS_SHA256 = '{genesis_sha256}'",
        f"EXPECTED_VALIDATORS = {desired}",
        f"EXPECTED_CANDIDATE_NODE_ID = '{candidate_node_id}'",
        f"EXPECTED_REQUEST_SHA256 = '{rpc_request_sha256}'",
        "PROOF = '/proof/validator-quorum-recovery.json'",
        "HEALTHY = '/proof/validator-quorum-recovery-healthy'",
        "MAX_BLOCK_AGE_SECONDS = 60",
        "def rpc(method, params):",
        "    body = json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}, separators=(',', ':')).encode()",
        "    req = urllib.request.Request(RPC, data=body, headers={'Content-Type':'application/json','Host':'localhost'}, method='POST')",
        "    with urllib.request.urlopen(req, timeout=5) as response:",
        "        value = json.loads(response.read(1048576).decode())",
        "    if value.get('error') is not None or 'result' not in value: raise RuntimeError(method + ' failed')",
        "    return value['result']",
        "def prove():",
        "    with open('/config/genesis.json', 'rb') as handle: digest = hashlib.sha256(handle.read()).hexdigest()",
        "    if digest != EXPECTED_GENESIS_SHA256: raise RuntimeError('genesis commitment mismatch')",
        "    if int(rpc('eth_chainId', []), 16) != EXPECTED_CHAIN_ID: raise RuntimeError('chain id mismatch')",
        "    genesis = rpc('eth_getBlockByNumber', ['0x0', False])",
        "    if not isinstance(genesis, dict) or not genesis.get('hash'): raise RuntimeError('genesis block missing')",
        "    peers = rpc('admin_peers', [])",
        "    if not isinstance(peers, list) or EXPECTED_CANDIDATE_NODE_ID not in json.dumps(peers, sort_keys=True).lower(): raise RuntimeError('candidate peer missing')",
        "    current = sorted(set(str(item).lower() for item in rpc('qbft_getValidatorsByBlockNumber', ['latest'])))",
        "    if current != EXPECTED_VALIDATORS: raise RuntimeError('validator set mismatch')",
        "    first = int(rpc('eth_blockNumber', []), 16)",
        "    deadline = time.time() + 180",
        "    second = first",
        "    while time.time() < deadline and second <= first:",
        "        time.sleep(2)",
        "        second = int(rpc('eth_blockNumber', []), 16)",
        "    if second <= first: raise RuntimeError('block height did not advance after quorum reset')",
        "    latest = rpc('eth_getBlockByNumber', ['latest', False])",
        "    if not isinstance(latest, dict) or not latest.get('hash'): raise RuntimeError('latest block missing')",
        "    block_time = int(latest.get('timestamp', '0x0'), 16)",
        "    now = int(time.time())",
        "    if block_time > now + 15 or now - block_time > MAX_BLOCK_AGE_SECONDS: raise RuntimeError('latest block is stale')",
        "    proof = {'chain_id':EXPECTED_CHAIN_ID,'genesis_sha256':digest,'rpc_request_sha256':EXPECTED_REQUEST_SHA256,'vote_cast':False,'activation_reconciled':True,'validator_set':current,'candidate_peer_verified':True,'first_block_number':first,'second_block_number':second,'block_advance':second-first,'latest_block_hash':latest['hash'],'latest_block_timestamp':block_time,'proved_at':time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        "    temporary = PROOF + '.tmp'",
        "    with open(temporary, 'w', encoding='utf-8') as handle: json.dump(proof, handle, sort_keys=True, separators=(',', ':'))",
        "    os.replace(temporary, PROOF)",
        "    with open(HEALTHY, 'w', encoding='ascii') as handle: handle.write(str(int(time.time())))",
        "try: os.unlink(HEALTHY)",
        "except FileNotFoundError: pass",
        "while True:",
        "    try: prove()",
        "    except Exception:",
        "        traceback.print_exc()",
        "        try: os.unlink(HEALTHY)",
        "        except FileNotFoundError: pass",
        "    time.sleep(6)",
        "",
    ])


def _replace_guardian(original: str, old_name: str, new_name: str, script: str, *, volume: str, health_file: str) -> str:
    start_marker = f"  {old_name}:\n"
    end_marker = "\nvolumes:\n"
    if original.count(start_marker) != 1 or original.count(end_marker) != 1:
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_COMPOSE_UNSUPPORTED",
            f"Compose does not contain exactly one {old_name} guardian",
        )
    start = original.index(start_marker)
    end = original.index(end_marker, start)
    indented = "\n".join("        " + line for line in script.splitlines())
    guardian = "\n".join([
        f"  {new_name}:",
        f"    image: {_PROOF_IMAGE}",
        "    restart: unless-stopped",
        "    read_only: true",
        "    depends_on:",
        "      " + ("mainneta-super1" if "initial" in new_name else "mainnetc-super1") + ":",
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
        f"        - import os,time; p='{health_file}'; assert os.path.isfile(p) and time.time()-os.path.getmtime(p) < 45",
        "      interval: 10s",
        "      timeout: 5s",
        "      retries: 36",
        "      start_period: 30s",
        "    volumes:",
        "      - mother-config:/config:ro",
        f"      - {volume}:/proof",
        "",
    ])
    updated = original[:start] + guardian + original[end:]
    section = updated.split(f"  {new_name}:", 1)[1].split(end_marker, 1)[0]
    if any(item in section for item in ("ports:", "expose:", "traefik.", "domains:", "fqdn:")):
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_GUARDIAN_EXPOSED", "recovery guardian must remain internal-only"
        )
    if "8545:8545" in updated:
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_RPC_EXPOSED", "recovery Compose must not publish JSON-RPC"
        )
    return updated


def _replica_recovery_compose(
    original: str, *, chain_id: int, genesis_sha256: str, validators: list[str], initial_node_id: str,
    replica_node_id: str, bootnode_enode: str
) -> str:
    script = _replica_readiness_script(
        node="mainnetc-super1", chain_id=chain_id, genesis_sha256=genesis_sha256,
        validators=validators, initial_node_id=initial_node_id, replica_node_id=replica_node_id,
    )
    updated = _replace_guardian(
        original, "mother-replica-sync-guardian", "mother-validator-quorum-recovery-replica-guardian",
        script, volume="mother-sync-proof", health_file="/proof/quorum-recovery-ready",
    )
    static_encoded = base64.b64encode(canonical_json([bootnode_enode])).decode("ascii")
    genesis_line = "        chmod 0444 /config/genesis.json"
    if updated.count(genesis_line) != 1:
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_COMPOSE_UNSUPPORTED", "C init command is unsupported"
        )
    updated = updated.replace(
        genesis_line,
        "\n".join([
            genesis_line,
            f"        printf '%s' '{static_encoded}' | base64 -d > /config/static-nodes.json",
            "        chmod 0444 /config/static-nodes.json",
        ]),
        1,
    )
    bootnode_line = f"      - --bootnodes={bootnode_enode}"
    if updated.count(bootnode_line) != 1:
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_COMPOSE_UNSUPPORTED", "C bootnode binding is unsupported"
        )
    updated = updated.replace(
        bootnode_line,
        bootnode_line + "\n      - --static-nodes-file=/config/static-nodes.json",
        1,
    )
    return updated


def _initial_recovery_compose(
    original: str, *, chain_id: int, genesis_sha256: str, validators: list[str], candidate_node_id: str,
    rpc_request_sha256: str
) -> str:
    script = _initial_quorum_script(
        node="mainneta-super1", chain_id=chain_id, genesis_sha256=genesis_sha256,
        validators=validators, candidate_node_id=candidate_node_id, rpc_request_sha256=rpc_request_sha256,
    )
    return _replace_guardian(
        original, "mother-validator-admission-guardian", "mother-validator-quorum-recovery-initial-guardian",
        script, volume="mother-proof", health_file="/proof/validator-quorum-recovery-healthy",
    )


def build_validator_quorum_recovery_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    transaction_path: Path,
    *,
    acknowledged_transaction_sha256: str,
    selected_nodes: Iterable[str] = (),
    transaction_max_age_seconds: int = 86400,
    expires_in_seconds: int = 300,
    created_at: str | None = None,
) -> dict[str, Any]:
    base = build_validator_admission_release(
        paths,
        private_state,
        Path(transaction_path),
        acknowledged_transaction_sha256=acknowledged_transaction_sha256,
        selected_nodes=selected_nodes,
        transaction_max_age_seconds=transaction_max_age_seconds,
        expires_in_seconds=expires_in_seconds,
        created_at=created_at,
    )
    transaction_ref = base["transaction"]
    transaction_path = _resolve(paths, transaction_ref["locator"], _TRANSACTION_DIRECTORY, "validator-admission transaction")
    transaction, _, transaction_byte_sha = _canonical_under(
        paths, transaction_path, _TRANSACTION_DIRECTORY, "validator-admission transaction"
    )
    if transaction_byte_sha != transaction_ref["byte_sha256"]:
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_INVALID", "transaction byte digest mismatch"
        )
    admission_plan = base["execution_plan"]
    replica = base["replica_precondition"]
    current_a_compose = admission_plan["admission_compose"]["canonical_text"]
    stale_c_compose = replica["proof_compose"]["canonical_text"]
    desired = [_address(item, "desired validator") for item in admission_plan["desired_validator_set"]]
    chain_id = int(admission_plan["chain_id"])
    genesis_sha = _sha256(admission_plan["genesis_sha256"], "genesis SHA-256")
    candidate_node_id = _node_id(admission_plan["candidate_node_id"], "candidate node ID")
    sync_release_ref = transaction.get("synchronization_evidence")
    if not isinstance(sync_release_ref, Mapping):
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_INVALID", "synchronization evidence reference is missing"
        )
    # The bootnode and public node IDs are embedded in the exact C proof Compose.
    bootnode_match = re.search(r"--bootnodes=(enode://[0-9a-fA-F]{128}@[0-9A-Za-z.:-]+)", stale_c_compose)
    initial_id_match = re.search(r"EXPECTED_INITIAL_NODE_ID = '([0-9a-f]{128})'", stale_c_compose)
    replica_id_match = re.search(r"EXPECTED_REPLICA_NODE_ID = '([0-9a-f]{128})'", stale_c_compose)
    if not bootnode_match or not initial_id_match or not replica_id_match:
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_INVALID", "C proof Compose lacks peer identity bindings"
        )
    bootnode_enode = bootnode_match.group(1)
    initial_node_id = _node_id(initial_id_match.group(1), "initial node ID")
    replica_node_id = _node_id(replica_id_match.group(1), "replica node ID")
    if replica_node_id != candidate_node_id:
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_INVALID", "candidate node identity changed"
        )
    c_compose = _replica_recovery_compose(
        stale_c_compose,
        chain_id=chain_id,
        genesis_sha256=genesis_sha,
        validators=desired,
        initial_node_id=initial_node_id,
        replica_node_id=replica_node_id,
        bootnode_enode=bootnode_enode,
    )
    a_compose = _initial_recovery_compose(
        current_a_compose,
        chain_id=chain_id,
        genesis_sha256=genesis_sha,
        validators=desired,
        candidate_node_id=candidate_node_id,
        rpc_request_sha256=_sha256(admission_plan["rpc_request_sha256"], "RPC request SHA-256"),
    )
    a_bytes = a_compose.encode("utf-8")
    c_bytes = c_compose.encode("utf-8")
    a_body = {"name": "mainneta-super1", "docker_compose_raw": base64.b64encode(a_bytes).decode("ascii")}
    c_body = {"name": "mainnetc-super1", "docker_compose_raw": base64.b64encode(c_bytes).decode("ascii")}
    a_uuid = _identifier(base["initial_chain_precondition"]["service_uuid"], "A service UUID")
    c_uuid = _identifier(replica["service_uuid"], "C service UUID")
    a_encoded = urllib.parse.quote(a_uuid, safe="")
    c_encoded = urllib.parse.quote(c_uuid, safe="")
    created_text = base["created_at"]
    release: dict[str, Any] = {
        "kind": _RELEASE_KIND,
        "schema_version": 1,
        "created_at": created_text,
        "expires_at": base["expires_at"],
        "network": base["network"],
        "mother_binding": _binding(private_state),
        "staged_scope": "recover-stalled-two-validator-qbft-after-admission",
        "transaction": dict(transaction_ref),
        "preconditions": {
            "initial": {
                "node": "mainneta-super1",
                "controller_id": "coolify-a",
                "service_uuid": a_uuid,
                "accepted_statuses": ["degraded:unhealthy", "running:unhealthy", "starting:unhealthy", "exited"],
                "compose": {
                    "canonical_text": current_a_compose,
                    "sha256": hashlib.sha256(current_a_compose.encode("utf-8")).hexdigest(),
                    "semantic_sha256": _compose_semantic_sha256(current_a_compose, "stalled A admission Compose"),
                },
            },
            "replica": {
                "node": "mainnetc-super1",
                "controller_id": "coolify-c",
                "service_uuid": c_uuid,
                "accepted_statuses": ["degraded:unhealthy", "running:unhealthy", "starting:unhealthy", "exited"],
                "compose": {
                    "canonical_text": stale_c_compose,
                    "sha256": hashlib.sha256(stale_c_compose.encode("utf-8")).hexdigest(),
                    "semantic_sha256": _compose_semantic_sha256(stale_c_compose, "stale C synchronization Compose"),
                },
            },
            "validator_set": desired,
            "vote_already_active": True,
        },
        "execution_plan": {
            "chain_id": chain_id,
            "genesis_sha256": genesis_sha,
            "validator_set": desired,
            "rpc_request_sha256": admission_plan["rpc_request_sha256"],
            "initial_node_id": initial_node_id,
            "replica_node_id": replica_node_id,
            "bootnode_enode": bootnode_enode,
            "replica_readiness_compose": {
                "canonical_text": c_compose,
                "sha256": hashlib.sha256(c_bytes).hexdigest(),
                "semantic_sha256": _compose_semantic_sha256(c_compose, "C quorum-recovery readiness Compose"),
                "guardian_internal_only": True,
                "static_peer_enode": bootnode_enode,
            },
            "initial_quorum_compose": {
                "canonical_text": a_compose,
                "sha256": hashlib.sha256(a_bytes).hexdigest(),
                "semantic_sha256": _compose_semantic_sha256(a_compose, "A quorum-recovery proof Compose"),
                "guardian_internal_only": True,
            },
            "mutations": [
                {"ordinal": 1, "mutation_id": "mainnetc-super1.install-quorum-recovery-readiness", "controller_id": "coolify-c", "method": "PATCH", "endpoint": f"/api/v1/services/{c_encoded}", "canonical_request_body": c_body, "body_sha256": hashlib.sha256(canonical_json(c_body)).hexdigest(), "success_statuses": [200, 201, 202]},
                {"ordinal": 2, "mutation_id": "mainnetc-super1.restart-validator-for-quorum-reset", "controller_id": "coolify-c", "method": "GET", "endpoint": f"/api/v1/deploy?uuid={c_encoded}&force=true", "canonical_request_body": None, "body_sha256": None, "success_statuses": [200, 201, 202]},
                {"ordinal": 3, "mutation_id": "mainneta-super1.install-quorum-recovery-proof", "controller_id": "coolify-a", "method": "PATCH", "endpoint": f"/api/v1/services/{a_encoded}", "canonical_request_body": a_body, "body_sha256": hashlib.sha256(canonical_json(a_body)).hexdigest(), "success_statuses": [200, 201, 202]},
                {"ordinal": 4, "mutation_id": "mainneta-super1.restart-validator-for-quorum-reset", "controller_id": "coolify-a", "method": "GET", "endpoint": f"/api/v1/deploy?uuid={a_encoded}&force=true", "canonical_request_body": None, "body_sha256": None, "success_statuses": [200, 201, 202]},
            ],
            "proof": {
                "manual_ssh_required": False,
                "public_endpoint_created": False,
                "vote_performed": False,
                "restart_order": ["mainnetc-super1", "mainneta-super1"],
                "predicates": ["exact-validator-set-A-plus-C", "C-exact-node-identity", "C-static-peer-A", "A-exact-peer-C", "both-round-timers-reset", "fresh-block-height-advancing"],
            },
        },
        "authority": {
            "quorum_recovery_authorized": True,
            "validator_vote_authorized": False,
            "validator_activation_change_authorized": False,
            "requested_use_limit": 1,
            "live_execution_authorized": False,
            "authorization_source": "explicit-operator-release",
        },
        "policy": {
            "allowed_http_methods": ["GET", "PATCH"],
            "manual_ssh_required": False,
            "public_http_endpoint_created": False,
            "host_rpc_mapping_present": False,
            "private_keys_materialized": False,
            "private_keys_persisted": False,
            "secrets_in_output": False,
            "restart_all_validators": True,
            "restart_order": ["mainnetc-super1", "mainneta-super1"],
            "vote_replay_forbidden": True,
            "network_access_performed": False,
            "live_mutation_performed": False,
        },
        "summary": {
            "release_valid": True,
            "mutation_count": 4,
            "validator_vote_authorized": False,
            "quorum_recovery_authorized": True,
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "next_phase_after_apply": "stage-post-admission-steady-state",
        },
        "validator_quorum_recovery_release_sha256": None,
    }
    release["validator_quorum_recovery_release_sha256"] = _digest_without(release, "validator_quorum_recovery_release_sha256")
    if _contains_sensitive(release):
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_INVALID", "quorum recovery release contains sensitive material"
        )
    return release


def write_validator_quorum_recovery_release(paths: PrivateStatePaths, release: Mapping[str, Any], *, operation: OperationIdentity) -> tuple[Path, str]:
    document = dict(release)
    digest = _digest_without(document, "validator_quorum_recovery_release_sha256")
    if document.get("kind") != _RELEASE_KIND or document.get("validator_quorum_recovery_release_sha256") != digest or _contains_sensitive(document):
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_INVALID", "quorum recovery release is malformed"
        )
    payload = canonical_json(document)
    destination = _ensure_root(paths, _RELEASE_DIRECTORY, operation) / f"{re.sub(r'[^0-9A-Za-z]+', '', document['created_at'])[:32]}-{digest[:16]}.json"
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def verify_validator_quorum_recovery_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *, selected_nodes: Iterable[str] = (), max_age_seconds: int = 300,
    transaction_max_age_seconds: int = 86400, now: datetime | None = None,
) -> dict[str, Any]:
    document, raw, byte_sha = _canonical_under(paths, Path(release_path), _RELEASE_DIRECTORY, "quorum recovery release")
    if document.get("kind") != _RELEASE_KIND or document.get("mother_binding") != _binding(private_state) or _contains_sensitive(document):
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_INVALID", "quorum recovery release is invalid"
        )
    digest = _digest_without(document, "validator_quorum_recovery_release_sha256")
    if document.get("validator_quorum_recovery_release_sha256") != digest:
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_INVALID", "quorum recovery release digest mismatch"
        )
    created = _parse_utc(document.get("created_at"), "created_at")
    expires = _parse_utc(document.get("expires_at"), "expires_at")
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    age = int((reference - created).total_seconds())
    if age < -1 or reference > expires or age > max_age_seconds:
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_RELEASE_STALE", "quorum recovery release is expired or stale"
        )
    transaction_ref = document.get("transaction")
    if not isinstance(transaction_ref, Mapping):
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_INVALID", "transaction reference is missing"
        )
    transaction_path = _resolve(paths, transaction_ref.get("locator"), _TRANSACTION_DIRECTORY, "validator-admission transaction")
    expected = build_validator_quorum_recovery_release(
        paths,
        private_state,
        transaction_path,
        acknowledged_transaction_sha256=_sha256(transaction_ref.get("sha256"), "transaction SHA-256"),
        selected_nodes=selected_nodes,
        transaction_max_age_seconds=transaction_max_age_seconds,
        expires_in_seconds=int((expires - created).total_seconds()),
        created_at=document["created_at"],
    )
    if expected != document:
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_INVALID", "quorum recovery release does not rebuild exactly"
        )
    return {
        "clean": True,
        "release_path": str(Path(release_path)),
        "validator_quorum_recovery_release_sha256": digest,
        "byte_sha256": byte_sha,
        "created_at": document["created_at"],
        "expires_at": document["expires_at"],
        "network": document["network"],
        "nodes": ["mainnetc-super1", "mainneta-super1"],
        "mutation_count": 4,
        "validator_vote_authorized": False,
        "quorum_recovery_authorized": True,
        "live_execution_authorized": False,
        "manual_ssh_required": False,
        "public_endpoint_created": False,
        "mother_binding": dict(document["mother_binding"]),
    }


def inspect_validator_quorum_recovery_release(
    paths: PrivateStatePaths, private_state: PrivateStateReadResult, release_path: Path, *,
    acknowledged_release_sha256: str, selected_nodes: Iterable[str] = (), max_age_seconds: int = 300,
    transaction_max_age_seconds: int = 86400,
) -> dict[str, Any]:
    verified = verify_validator_quorum_recovery_release(
        paths, private_state, Path(release_path), selected_nodes=selected_nodes,
        max_age_seconds=max_age_seconds, transaction_max_age_seconds=transaction_max_age_seconds,
    )
    acknowledged = _sha256(acknowledged_release_sha256, "acknowledged release SHA-256")
    if acknowledged != verified["validator_quorum_recovery_release_sha256"]:
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_ACKNOWLEDGEMENT_MISMATCH", "operator acknowledgement does not match release"
        )
    claim_path = _root(paths, _CLAIM_DIRECTORY) / f"{acknowledged}.json"
    return {
        **verified,
        "execute_requested": False,
        "executor_implemented": True,
        "release_already_claimed": claim_path.exists(),
        "live_execution_authorized": True,
        "network_access_performed": False,
        "live_mutation_performed": False,
        "validator_vote_performed": False,
    }


def _verify_service(
    *, controller: Any, controller_id: str, node: str, service_uuid: str, expected_compose: str,
    accepted_statuses: list[str], timeout: float, max_response_bytes: int, opener: Any,
    receipts: list[dict[str, Any]], phase: str,
) -> str:
    inventory = _http(controller, "GET", "/api/v1/services", body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
    record = _service_record(inventory["payload"], service_uuid, node) if inventory["ok"] else None
    status = _service_status(record) if record is not None else ""
    receipts.append({"name": f"{phase}-status", "controller_id": controller_id, "method": "GET", "endpoint": "/api/v1/services", "status": inventory["status"], "response_sha256": inventory["response_sha256"], "service_status": status, "verified": inventory["ok"] and status in accepted_statuses})
    if not inventory["ok"] or status not in accepted_statuses:
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_PRECONDITION_FAILED", f"{node} status {status!r} is outside the exact recovery state"
        )
    endpoint = f"/api/v1/services/{service_uuid}"
    detail = _http(controller, "GET", endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
    if not detail["ok"]:
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_PRECONDITION_FAILED", f"{node} service detail failed"
        )
    try:
        binding = _match_service_compose(detail["payload"], expected_compose, f"{node} exact recovery Compose")
    except MotherDeploymentGenesisBirthError as exc:
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_COMPOSE_MISMATCH", _safe_message(exc)
        ) from exc
    receipts.append({"name": f"{phase}-compose", "controller_id": controller_id, "method": "GET", "endpoint": endpoint, "status": detail["status"], "response_sha256": detail["response_sha256"], "binding_mode": binding["mode"], "semantic_sha256": binding["semantic_sha256"], "verified": True})
    return status


def _wait_healthy(
    *, controller: Any, service_uuid: str, node: str, timeout: float, max_response_bytes: int,
    max_wait_seconds: float, poll_interval_seconds: float, opener: Any, observations: list[dict[str, Any]], phase: str,
) -> None:
    deadline = time.monotonic() + max_wait_seconds
    last = ""
    while True:
        inventory = _http(controller, "GET", "/api/v1/services", body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        if inventory["ok"]:
            last = _service_status(_service_record(inventory["payload"], service_uuid, node))
            observations.append({"phase": phase, "status": last, "response_sha256": inventory["response_sha256"], "observed_at": _timestamp()})
            if last == "running:healthy":
                return
        if time.monotonic() >= deadline:
            raise MotherDeploymentValidatorQuorumRecoveryError(
                "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_NOT_HEALTHY", f"{node} did not reach running:healthy (last status {last!r})"
            )
        time.sleep(max(0.0, poll_interval_seconds))


def _write_evidence(paths: PrivateStatePaths, evidence: Mapping[str, Any], operation: OperationIdentity) -> tuple[Path, str]:
    document = dict(evidence)
    if document.get("kind") != _EVIDENCE_KIND or _contains_sensitive(document):
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_EVIDENCE_INVALID", "quorum recovery evidence is malformed"
        )
    payload = canonical_json(document)
    digest = hashlib.sha256(payload).hexdigest()
    destination = _ensure_root(paths, _EVIDENCE_DIRECTORY, operation) / f"{re.sub(r'[^0-9A-Za-z]+', '', document['completed_at'])[:32]}-{digest[:16]}.json"
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def execute_validator_quorum_recovery_release(
    paths: PrivateStatePaths, private_state: PrivateStateReadResult, release_path: Path, *,
    acknowledged_release_sha256: str, selected_nodes: Iterable[str] = (), max_age_seconds: int = 300,
    transaction_max_age_seconds: int = 86400, timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES, max_wait_seconds: float = 360.0,
    poll_interval_seconds: float = 5.0, opener: Any = _DEFAULT_OPENER, operation: OperationIdentity,
) -> dict[str, Any]:
    inspected = inspect_validator_quorum_recovery_release(
        paths, private_state, Path(release_path), acknowledged_release_sha256=acknowledged_release_sha256,
        selected_nodes=selected_nodes, max_age_seconds=max_age_seconds,
        transaction_max_age_seconds=transaction_max_age_seconds,
    )
    if inspected["release_already_claimed"]:
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_RELEASE_ALREADY_CONSUMED", "quorum recovery release already has an execution claim"
        )
    release, _, _ = _canonical_under(paths, Path(release_path), _RELEASE_DIRECTORY, "quorum recovery release")
    digest = inspected["validator_quorum_recovery_release_sha256"]
    claim = {"kind": _CLAIM_KIND, "schema_version": 1, "claimed_at": _timestamp(), "release": {"locator": _relative(paths, Path(release_path), "quorum recovery release"), "sha256": digest}, "requested_use_limit": 1, "operation_id": operation.operation_id}
    claim_path = _ensure_root(paths, _CLAIM_DIRECTORY, operation) / f"{digest}.json"
    if claim_path.exists():
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_RELEASE_ALREADY_CONSUMED", "quorum recovery release already has an execution claim"
        )
    atomic_files.durable_create(claim_path, canonical_json(claim), operation=operation)
    _secure_private_path(claim_path, is_directory=False, operation=operation)

    controller_a = resolve_coolify_controller(private_state, release["network"], "coolify-a")
    controller_c = resolve_coolify_controller(private_state, release["network"], "coolify-c")
    preconditions: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    failure: dict[str, str] | None = None
    started = _timestamp()
    try:
        pre = release["preconditions"]
        plan = release["execution_plan"]
        _verify_service(controller=controller_a, controller_id="coolify-a", node=pre["initial"]["node"], service_uuid=pre["initial"]["service_uuid"], expected_compose=pre["initial"]["compose"]["canonical_text"], accepted_statuses=list(pre["initial"]["accepted_statuses"]), timeout=timeout, max_response_bytes=max_response_bytes, opener=opener, receipts=preconditions, phase="initial-stalled")
        _verify_service(controller=controller_c, controller_id="coolify-c", node=pre["replica"]["node"], service_uuid=pre["replica"]["service_uuid"], expected_compose=pre["replica"]["compose"]["canonical_text"], accepted_statuses=list(pre["replica"]["accepted_statuses"]), timeout=timeout, max_response_bytes=max_response_bytes, opener=opener, receipts=preconditions, phase="replica-stale")
        mutations = plan["mutations"]
        if type(mutations) is not list or len(mutations) != 4:
            raise MotherDeploymentValidatorQuorumRecoveryError(
                "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_INVALID", "released mutation set is malformed"
            )
        for mutation in mutations:
            controller_id = mutation["controller_id"]
            controller = controller_c if controller_id == "coolify-c" else controller_a
            method = mutation["method"]
            body = dict(mutation["canonical_request_body"]) if isinstance(mutation["canonical_request_body"], Mapping) else None
            response = _http(controller, method, mutation["endpoint"], body=body, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
            ok = response["status"] in mutation["success_statuses"]
            receipts.append({"ordinal": mutation["ordinal"], "mutation_id": mutation["mutation_id"], "controller_id": controller_id, "method": method, "endpoint": mutation["endpoint"], "body_sha256": mutation["body_sha256"], "response": {"status": response["status"], "response_sha256": response["response_sha256"], "byte_length": response["byte_length"], "elapsed_ms": response["elapsed_ms"], "ok": ok}, "live_write_acknowledged": ok, "status": "succeeded" if ok else "failed"})
            if not ok:
                raise MotherDeploymentValidatorQuorumRecoveryError(
                    "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_MUTATION_FAILED", f"Coolify rejected {mutation['mutation_id']!r}"
                )
            if mutation["ordinal"] == 2:
                _wait_healthy(controller=controller_c, service_uuid=pre["replica"]["service_uuid"], node=pre["replica"]["node"], timeout=timeout, max_response_bytes=max_response_bytes, max_wait_seconds=max_wait_seconds, poll_interval_seconds=poll_interval_seconds, opener=opener, observations=observations, phase="replica-readiness")
                _verify_service(controller=controller_c, controller_id="coolify-c", node=pre["replica"]["node"], service_uuid=pre["replica"]["service_uuid"], expected_compose=plan["replica_readiness_compose"]["canonical_text"], accepted_statuses=["running:healthy"], timeout=timeout, max_response_bytes=max_response_bytes, opener=opener, receipts=preconditions, phase="replica-ready")
            if mutation["ordinal"] == 4:
                _wait_healthy(controller=controller_a, service_uuid=pre["initial"]["service_uuid"], node=pre["initial"]["node"], timeout=timeout, max_response_bytes=max_response_bytes, max_wait_seconds=max_wait_seconds, poll_interval_seconds=poll_interval_seconds, opener=opener, observations=observations, phase="initial-quorum-proof")
                _verify_service(controller=controller_a, controller_id="coolify-a", node=pre["initial"]["node"], service_uuid=pre["initial"]["service_uuid"], expected_compose=plan["initial_quorum_compose"]["canonical_text"], accepted_statuses=["running:healthy"], timeout=timeout, max_response_bytes=max_response_bytes, opener=opener, receipts=preconditions, phase="initial-recovered")
                _verify_service(controller=controller_c, controller_id="coolify-c", node=pre["replica"]["node"], service_uuid=pre["replica"]["service_uuid"], expected_compose=plan["replica_readiness_compose"]["canonical_text"], accepted_statuses=["running:healthy"], timeout=timeout, max_response_bytes=max_response_bytes, opener=opener, receipts=preconditions, phase="replica-after-initial-restart")
    except MotherDeploymentValidatorQuorumRecoveryError as exc:
        failure = {"code": exc.code, "message": _safe_message(exc)}
    except Exception:
        failure = {"code": "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_UNEXPECTED_FAILURE", "message": "unexpected quorum recovery failure"}

    completed = _timestamp()
    succeeded = sum(item.get("status") == "succeeded" for item in receipts)
    complete = failure is None and succeeded == 4
    live_mutation = any(item.get("live_write_acknowledged") is True for item in receipts)
    evidence: dict[str, Any] = {
        "kind": _EVIDENCE_KIND,
        "schema_version": 1,
        "started_at": started,
        "completed_at": completed,
        "status": "pass" if complete else "failed",
        "mother_binding": dict(inspected["mother_binding"]),
        "network": release["network"],
        "nodes": ["mainnetc-super1", "mainneta-super1"],
        "release": {"locator": _relative(paths, Path(release_path), "quorum recovery release"), "sha256": digest},
        "execution_claim": {"locator": _relative(paths, claim_path, "quorum recovery claim")},
        "chain_id": release["execution_plan"]["chain_id"],
        "genesis_sha256": release["execution_plan"]["genesis_sha256"],
        "validator_set": list(release["execution_plan"]["validator_set"]) if complete else None,
        "precondition_receipts": preconditions,
        "mutation_receipts": receipts,
        "health_observations": observations,
        "failure": failure,
        "policy": {"manual_ssh_required": False, "public_endpoint_created": False, "vote_performed": False, "restart_all_validators": True, "restart_order": ["mainnetc-super1", "mainneta-super1"], "secrets_in_output": False},
        "summary": {
            "clean": complete,
            "quorum_recovered": complete,
            "validator_set_verified": complete,
            "blocks_advancing": complete,
            "latest_block_fresh": complete,
            "replica_static_peer_installed": complete,
            "replica_restarted_first": complete,
            "initial_restarted_second": complete,
            "validator_vote_performed": False,
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "planned_mutation_count": 4,
            "attempted_mutation_count": len(receipts),
            "succeeded_mutation_count": succeeded,
            "failed_mutation_count": sum(item.get("status") != "succeeded" for item in receipts),
            "network_access_performed": bool(preconditions or receipts or observations),
            "live_mutation_performed": live_mutation,
            "complete": complete,
            "next_phase": "stage-post-admission-steady-state" if complete else "manual-review-required",
        },
    }
    evidence_path, evidence_sha = _write_evidence(paths, evidence, operation)
    evidence["evidence"] = {"path": str(evidence_path), "sha256": evidence_sha}
    return evidence


def verify_validator_quorum_recovery_evidence(
    paths: PrivateStatePaths, private_state: PrivateStateReadResult, evidence_path: Path, *,
    selected_nodes: Iterable[str] = (), max_age_seconds: int = 300, now: datetime | None = None,
) -> dict[str, Any]:
    document, _, digest = _canonical_under(paths, Path(evidence_path), _EVIDENCE_DIRECTORY, "quorum recovery evidence")
    if document.get("kind") != _EVIDENCE_KIND or document.get("mother_binding") != _binding(private_state) or _contains_sensitive(document):
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_EVIDENCE_INVALID", "quorum recovery evidence is invalid"
        )
    requested = tuple(_identifier(item, "selected node") for item in selected_nodes)
    if requested and requested != ("mainnetc-super1",):
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_SELECTION_MISMATCH", "quorum recovery selection must be mainnetc-super1"
        )
    completed = _parse_utc(document.get("completed_at"), "completed_at")
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    age = int((reference - completed).total_seconds())
    if age < -1 or age > max_age_seconds:
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_EVIDENCE_STALE", "quorum recovery evidence is outside freshness window"
        )
    summary = document.get("summary")
    if document.get("status") != "pass" or not isinstance(summary, Mapping) or not all([
        summary.get("quorum_recovered") is True,
        summary.get("validator_set_verified") is True,
        summary.get("blocks_advancing") is True,
        summary.get("validator_vote_performed") is False,
        summary.get("complete") is True,
    ]):
        raise MotherDeploymentValidatorQuorumRecoveryError(
            "MOTHER_DEPLOY_VALIDATOR_QUORUM_RECOVERY_EVIDENCE_INVALID", "quorum recovery evidence does not prove completion"
        )
    return {
        "clean": True,
        "evidence_path": str(Path(evidence_path)),
        "evidence_sha256": digest,
        "age_seconds": age,
        "network": document["network"],
        "nodes": list(document["nodes"]),
        "chain_id": document["chain_id"],
        "genesis_sha256": document["genesis_sha256"],
        "validator_set": list(document["validator_set"]),
        "quorum_recovered": True,
        "blocks_advancing": True,
        "validator_vote_performed": False,
        "manual_ssh_required": False,
        "public_endpoint_created": False,
        "next_phase": "stage-post-admission-steady-state",
        "mother_binding": dict(document["mother_binding"]),
    }


__all__ = [
    "MotherDeploymentValidatorQuorumRecoveryError",
    "build_validator_quorum_recovery_release",
    "write_validator_quorum_recovery_release",
    "verify_validator_quorum_recovery_release",
    "inspect_validator_quorum_recovery_release",
    "execute_validator_quorum_recovery_release",
    "verify_validator_quorum_recovery_evidence",
]
