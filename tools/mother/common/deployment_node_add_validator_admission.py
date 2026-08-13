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

from collections.abc import Iterable, Mapping
import base64
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


def _extract_genesis_b64(compose_text: str) -> str:
    match = re.search(r"printf '%s' '([^']+)' \| base64 -d > /config/genesis\.json", compose_text)
    if not match:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_GENESIS_MISSING", "replica-sync Compose does not contain canonical genesis material")
    return match.group(1)


def _activation_guardian_script(
    *,
    target_node: str,
    target_node_id: str,
    desired_validators: Iterable[str],
    chain_id: int,
    genesis_sha256: str,
) -> str:
    desired = [item.lower() for item in desired_validators]
    return "\n".join([
        "import hashlib, json, os, time, traceback, urllib.request",
        f"RPC = 'http://{target_node}:8545'",
        f"TARGET_NODE = {target_node!r}",
        f"EXPECTED_NODE_ID = {target_node_id.lower()!r}",
        f"EXPECTED_CHAIN_ID = {int(chain_id)}",
        f"EXPECTED_GENESIS_SHA256 = {genesis_sha256!r}",
        f"EXPECTED_DESIRED = {desired!r}",
        "PROOF = '/proof/target-add-node-validator-admission.json'",
        "HEALTHY = '/proof/target-add-node-validator-admission-healthy'",
        "LAST_ERROR = '/proof/target-add-node-validator-admission-last-error.json'",
        "MAX_BLOCK_AGE_SECONDS = 120",
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
        "def same_set(left, right): return sorted(left) == sorted(right)",
        "def write_json(path, payload):",
        "    tmp = path + '.tmp'",
        "    with open(tmp, 'w', encoding='utf-8') as handle: json.dump(payload, handle, sort_keys=True, separators=(',', ':'))",
        "    os.replace(tmp, path)",
        "def prove():",
        "    with open('/config/genesis.json', 'rb') as handle:",
        "        if hashlib.sha256(handle.read()).hexdigest() != EXPECTED_GENESIS_SHA256: raise RuntimeError('genesis commitment mismatch')",
        "    if int(rpc('eth_chainId', []), 16) != EXPECTED_CHAIN_ID: raise RuntimeError('chain id mismatch')",
        "    node_info = rpc('admin_nodeInfo', [])",
        "    actual_node_id = normalize_node_id(node_info.get('id')) if isinstance(node_info, dict) else ''",
        "    if actual_node_id != EXPECTED_NODE_ID: raise RuntimeError('target validator node identity mismatch')",
        "    deadline = time.time() + 180",
        "    final = validators()",
        "    while time.time() < deadline and not same_set(final, EXPECTED_DESIRED):",
        "        time.sleep(2)",
        "        final = validators()",
        "    if not same_set(final, EXPECTED_DESIRED): raise RuntimeError('desired validator set not reached')",
        "    if rpc('eth_syncing', []) is not False: raise RuntimeError('target is still syncing')",
        "    first = int(rpc('eth_blockNumber', []), 16)",
        "    time.sleep(4)",
        "    second = int(rpc('eth_blockNumber', []), 16)",
        "    if second <= first: raise RuntimeError('block height did not advance')",
        "    latest = rpc('eth_getBlockByNumber', ['latest', False])",
        "    if not isinstance(latest, dict) or not latest.get('hash'): raise RuntimeError('latest block missing')",
        "    block_time = int(latest.get('timestamp', '0x0'), 16)",
        "    now = int(time.time())",
        "    if block_time > now + 15 or now - block_time > MAX_BLOCK_AGE_SECONDS: raise RuntimeError('latest block is stale')",
        "    proof = {'target_node':TARGET_NODE,'target_node_id':actual_node_id,'target_validator_node_identity_verified':True,'chain_id':EXPECTED_CHAIN_ID,'genesis_sha256':EXPECTED_GENESIS_SHA256,'desired_validator_set':EXPECTED_DESIRED,'final_validator_set':final,'first_block_number':first,'second_block_number':second,'block_advance':second-first,'latest_block_hash':latest['hash'],'latest_block_timestamp':block_time,'proved_at':time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        "    write_json(PROOF, proof)",
        "    try: os.unlink(LAST_ERROR)",
        "    except FileNotFoundError: pass",
        "    with open(HEALTHY, 'w', encoding='ascii') as handle: handle.write(str(int(time.time())))",
        "while True:",
        "    try:",
        "        prove()",
        "    except Exception as exc:",
        "        try: os.unlink(HEALTHY)",
        "        except FileNotFoundError: pass",
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
) -> str:
    decoded = base64.b64decode(genesis_b64.encode("ascii"), validate=True)
    if hashlib.sha256(decoded).hexdigest() != genesis_sha256:
        raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_GENESIS_INVALID", "activation genesis does not match committed hash")
    guardian_script = _activation_guardian_script(
        target_node=target_node,
        target_node_id=target_node_id,
        desired_validators=desired_validators,
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
        "      - --node-private-key-file=/config/nodekey",
        f"      - --network-id={int(chain_id)}",
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
        f"      - --host-allowlist=localhost,127.0.0.1,{target_node},mother-add-node-validator-activation-guardian",
        "      - --min-gas-price=0",
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
        "        - import os,time; p='/proof/target-add-node-validator-admission-healthy'; assert os.path.isfile(p) and time.time()-os.path.getmtime(p) < 45",
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
    current_validators: Iterable[str],
    desired_validators: Iterable[str],
    chain_id: int,
    genesis_sha256: str,
    request_sha256: str,
) -> str:
    current = [item.lower() for item in current_validators]
    desired = [item.lower() for item in desired_validators]
    request = {"jsonrpc": "2.0", "id": 1, "method": "qbft_proposeValidatorVote", "params": [candidate.lower(), True]}
    request_json = json.dumps(request, sort_keys=True, separators=(",", ":"))
    return "\n".join([
        "import hashlib, json, os, time, traceback, urllib.request",
        f"RPC = 'http://{voter}:8545'",
        f"VOTER_NODE = {voter!r}",
        f"EXPECTED_CHAIN_ID = {int(chain_id)}",
        f"EXPECTED_GENESIS_SHA256 = {genesis_sha256!r}",
        f"EXPECTED_CURRENT = {current!r}",
        f"EXPECTED_DESIRED = {desired!r}",
        f"CANDIDATE_VALIDATOR = {candidate.lower()!r}",
        f"REQUEST = json.loads({request_json!r})",
        f"EXPECTED_REQUEST_SHA256 = {request_sha256!r}",
        "SAFE = VOTER_NODE.replace('-', '_')",
        "PROOF = '/proof/' + SAFE + '-add-node-validator-admission.json'",
        "HEALTHY = '/proof/' + SAFE + '-add-node-validator-admission-healthy'",
        "LAST_ERROR = '/proof/' + SAFE + '-add-node-validator-admission-last-error.json'",
        "MAX_BLOCK_AGE_SECONDS = 120",
        "def encoded(value): return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()",
        "def rpc(method, params):",
        "    body = encoded({'jsonrpc':'2.0','id':1,'method':method,'params':params})",
        "    req = urllib.request.Request(RPC, data=body, headers={'Content-Type':'application/json','Host':'localhost'}, method='POST')",
        "    with urllib.request.urlopen(req, timeout=5) as response:",
        "        value = json.loads(response.read(1048576).decode())",
        "    if value.get('error') is not None or 'result' not in value: raise RuntimeError(method + ' failed: ' + repr(value.get('error')))",
        "    return value['result']",
        "def validators(): return [str(item).lower() for item in rpc('qbft_getValidatorsByBlockNumber', ['latest'])]",
        "def same_set(left, right): return sorted(left) == sorted(right)",
        "def write_json(path, payload):",
        "    tmp = path + '.tmp'",
        "    with open(tmp, 'w', encoding='utf-8') as handle: json.dump(payload, handle, sort_keys=True, separators=(',', ':'))",
        "    os.replace(tmp, path)",
        "def prove():",
        "    if hashlib.sha256(encoded(REQUEST)).hexdigest() != EXPECTED_REQUEST_SHA256: raise RuntimeError('vote request commitment mismatch')",
        "    if int(rpc('eth_chainId', []), 16) != EXPECTED_CHAIN_ID: raise RuntimeError('chain id mismatch')",
        "    with open('/config/genesis.json', 'rb') as handle:",
        "        if hashlib.sha256(handle.read()).hexdigest() != EXPECTED_GENESIS_SHA256: raise RuntimeError('genesis commitment mismatch')",
        "    current = validators()",
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
        "    first = int(rpc('eth_blockNumber', []), 16)",
        "    time.sleep(4)",
        "    second = int(rpc('eth_blockNumber', []), 16)",
        "    if second <= first: raise RuntimeError('block height did not advance')",
        "    latest = rpc('eth_getBlockByNumber', ['latest', False])",
        "    if not isinstance(latest, dict) or not latest.get('hash'): raise RuntimeError('latest block missing')",
        "    block_time = int(latest.get('timestamp', '0x0'), 16)",
        "    now = int(time.time())",
        "    if block_time > now + 15 or now - block_time > MAX_BLOCK_AGE_SECONDS: raise RuntimeError('latest block is stale')",
        "    proof = {'voter_node':VOTER_NODE,'chain_id':EXPECTED_CHAIN_ID,'genesis_sha256':EXPECTED_GENESIS_SHA256,'rpc_request_sha256':EXPECTED_REQUEST_SHA256,'vote_submitted':vote_submitted,'expected_current_validator_set':EXPECTED_CURRENT,'desired_validator_set':EXPECTED_DESIRED,'final_validator_set':final,'first_block_number':first,'second_block_number':second,'block_advance':second-first,'latest_block_hash':latest['hash'],'latest_block_timestamp':block_time,'proved_at':time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        "    write_json(PROOF, proof)",
        "    try: os.unlink(LAST_ERROR)",
        "    except FileNotFoundError: pass",
        "    with open(HEALTHY, 'w', encoding='ascii') as handle: handle.write(str(int(time.time())))",
        "while True:",
        "    try:",
        "        prove()",
        "    except Exception as exc:",
        "        try: os.unlink(HEALTHY)",
        "        except FileNotFoundError: pass",
        "        write_json(LAST_ERROR, {'error':str(exc),'type':type(exc).__name__,'traceback':traceback.format_exc(limit=4),'observed_at':time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})",
        "    time.sleep(6)",
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
        "restart": "unless-stopped",
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
    target_node_id = _public_node_id(_validator_private_key(private_state, network=network, node=target_node))
    activation_compose = _candidate_activation_compose(
        target_node=target_node,
        genesis_b64=genesis_b64,
        bootnode_enode=bootnode_enode,
        chain_id=chain_id,
        genesis_sha256=genesis_sha256,
        target_node_id=target_node_id,
        desired_validators=desired_set,
    )
    vote_requests = []
    for node in voter_nodes:
        request = {"jsonrpc": "2.0", "id": 1, "method": "qbft_proposeValidatorVote", "params": [candidate, True]}
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
        },
        "admission_plan": {
            "candidate_node": context["target_node"],
            "candidate_validator_address": context["target_validator_address"],
            "voter_nodes": list(context["voter_nodes"]),
            "vote_threshold": "all-existing-validators",
            "logical_vote_count": len(context["voter_nodes"]),
            "current_validator_set": list(context["current_validator_set"]),
            "desired_validator_set": list(context["desired_validator_set"]),
            "chain_id": context["chain_id"],
            "genesis_sha256": context["genesis_sha256"],
            "bootnode": dict(context["bootnode"]),
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
            "replica_sync_authorized": False,
            "service_creation_authorized": False,
            "identity_install_authorized": False,
            "routing_or_topology_publication_authorized": False,
            "requested_use_limit": 1,
        },
        "policy": {
            "allowed_http_methods": ["GET", "PATCH"],
            "compiler": "mother-native-add-node-validator-admission-v1",
            "coolify_control_plane_only": True,
            "all_existing_validator_votes_required": True,
            "manual_ssh_required": False,
            "public_http_endpoint_created": False,
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
            "routing_or_topology_publication_authorized": False,
            "manual_ssh_required": False,
            "public_endpoint_created": False,
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
        "public_endpoint_created": False,
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
                "service_status": _service_status(detail_record),
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
            "service_status": _service_status(detail_record),
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


def _component_healthy(record: Mapping[str, Any], *, names: Iterable[str]) -> bool:
    status = _service_status(record)
    if status == "running:healthy":
        return True
    expected = set(names)
    for child in _children(record):
        if str(child.get("name") or child.get("service") or "") in expected and child.get("status") == "running:healthy":
            return True
    return False


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
    max_wait_seconds: float = 300.0,
    poll_interval_seconds: float = 5.0,
    opener: Any = _DEFAULT_OPENER,
    now: datetime | None = None,
    operation: OperationIdentity,
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
    post_admission_cleanup: dict[str, Any] | None = None
    admission_proven = False

    target = release["target"]
    plan = release["admission_plan"]
    candidate_node = _identifier(target["node"], "candidate node")
    target_controller_id = _identifier(target["controller_id"], "target controller")
    target_uuid = _identifier(target["service_uuid"], "target service UUID")
    voter_nodes = list(plan["voter_nodes"])
    desired_set = [_address(item, "desired validator") for item in plan["desired_validator_set"]]
    current_set = [_address(item, "current validator") for item in plan["current_validator_set"]]
    candidate = _address(plan["candidate_validator_address"], "candidate validator")
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
        deploy_endpoint = f"/api/v1/deploy?uuid={urllib.parse.quote(target_uuid, safe='')}&force=true"
        deploy = _http(target_controller, "GET", deploy_endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        deploy_ok = deploy["status"] in {200, 201, 202}
        receipts.append({
            "ordinal": len(receipts) + 1,
            "mutation_id": f"{candidate_node}.deploy-validator-activation-compose",
            "controller_id": target_controller_id,
            "node": candidate_node,
            "service_uuid": target_uuid,
            "method": "GET",
            "endpoint": deploy_endpoint,
            "body_sha256": None,
            "guardian_service": target_guardian_name,
            "response": _safe_response(deploy),
            "live_write_acknowledged": deploy_ok,
            "status": "succeeded" if deploy_ok else "failed",
        })
        if not deploy_ok:
            raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_MUTATION_FAILED", f"Coolify rejected target deploy with HTTP {deploy['status']}")

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
            deploy_endpoint = f"/api/v1/deploy?uuid={urllib.parse.quote(uuid, safe='')}&force=true"
            deploy = _http(controller, "GET", deploy_endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
            deploy_ok = deploy["status"] in {200, 201, 202}
            receipts.append({
                "ordinal": len(receipts) + 1,
                "mutation_id": f"{voter}.deploy-add-node-validator-admission-guardian",
                "controller_id": controller_id,
                "node": voter,
                "service_uuid": uuid,
                "method": "GET",
                "endpoint": deploy_endpoint,
                "body_sha256": None,
                "guardian_service": guardian_name,
                "response": _safe_response(deploy),
                "live_write_acknowledged": deploy_ok,
                "status": "succeeded" if deploy_ok else "failed",
            })
            if not deploy_ok:
                raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_MUTATION_FAILED", f"Coolify rejected {voter} guardian deploy with HTTP {deploy['status']}")

        deadline = time.monotonic() + max_wait_seconds
        healthy: set[str] = set()
        last_statuses: dict[str, str] = {}
        node_to_controller = {candidate_node: target_controller_id}
        for item in plan["rpc_requests"]:
            node_to_controller[_identifier(item["voter_node"], "voter node")] = _identifier(item["controller_id"], "voter controller")
        while True:
            healthy.clear()
            for node in [candidate_node, *voter_nodes]:
                controller_id = node_to_controller[node]
                controller = controllers[controller_id]
                service_uuid = all_service_uuids.get(node)
                if service_uuid:
                    endpoint = f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}"
                    service_response = _http(controller, "GET", endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
                else:
                    endpoint = "/api/v1/services"
                    service_response = _http(controller, "GET", endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
                if service_response["ok"]:
                    record = _find_service_record(service_response["payload"], node=node, service_uuid=service_uuid)
                    guard_names = [node]
                    if node == candidate_node:
                        guard_names.append(target_guardian_name)
                    else:
                        guard_names.append(voter_guardian_names[node])
                    status = _service_status(record)
                    component_healthy = _component_healthy(record, names=guard_names)
                    last_statuses[node] = status
                    if component_healthy:
                        healthy.add(node)
                    observations.append({
                        "node": node,
                        "controller_id": controller_id,
                        "endpoint": endpoint,
                        "service_uuid": service_uuid,
                        "status": status,
                        "component_or_service_healthy": component_healthy,
                        "response_sha256": service_response["response_sha256"],
                        "observed_at": _timestamp(),
                    })
            if set([candidate_node, *voter_nodes]) <= healthy:
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(max(0.0, poll_interval_seconds))
        if not (set([candidate_node, *voter_nodes]) <= healthy):
            raise _fail("MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_NOT_HEALTHY", f"validator-admission guardians did not become healthy: {last_statuses!r}")

        admission_proven = True
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
        except MotherDeploymentCompletedHelperCleanupError as exc:
            raise _fail(
                "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_POST_ADMISSION_HEALTH_UNCLEAN",
                str(exc)[:700],
            ) from exc
        post_admission_cleanup = _cleanup_summary(cleanup_result, paths=paths)
        if cleanup_result.get("status") != "pass" or not isinstance(cleanup_result.get("summary"), Mapping) or cleanup_result["summary"].get("clean") is not True:
            raise _fail(
                "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_POST_ADMISSION_HEALTH_UNCLEAN",
                "post-admission service health cleanup did not reach a clean top-level Coolify service state",
            )

    except MotherDeploymentNodeAddValidatorAdmissionError as exc:
        failure = {"code": exc.code, "message": str(exc)[:700]}
    except Exception as exc:  # pragma: no cover
        failure = {"code": "MOTHER_DEPLOY_NODE_ADD_VALIDATOR_ADMISSION_UNEXPECTED_FAILURE", "message": str(exc)[:700]}

    completed = _timestamp()
    planned_mutations = 2 + 2 * len(voter_nodes)
    succeeded = sum(item.get("status") == "succeeded" for item in receipts)
    required_healthy_nodes = set([candidate_node, *voter_nodes])
    healthy_nodes = {item.get("node") for item in observations if item.get("component_or_service_healthy") is True}
    cleanup_clean = (
        isinstance(post_admission_cleanup, Mapping)
        and isinstance(post_admission_cleanup.get("summary"), Mapping)
        and post_admission_cleanup["summary"].get("clean") is True
    )
    complete = failure is None and admission_proven and succeeded == planned_mutations and required_healthy_nodes <= healthy_nodes and cleanup_clean
    live_mutation = any(item.get("live_write_acknowledged") is True for item in receipts)
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
        "target_host": target_controller_id,
        "created_service_uuid": target_uuid,
        "voter_nodes": voter_nodes,
        "release": {"locator": _relative(paths, Path(inspected["release_path"]), label="add-node validator-admission release"), "sha256": digest},
        "execution_claim": {"locator": _relative(paths, claim_path, label="add-node validator-admission claim")},
        "source_replica_sync_evidence": dict(release["source_replica_sync_evidence"]),
        "chain_id": int(plan["chain_id"]),
        "genesis_sha256": _sha256(plan["genesis_sha256"], "genesis SHA-256"),
        "current_validator_set": current_set,
        "desired_validator_set": desired_set,
        "precondition_receipts": preconditions,
        "mutation_receipts": receipts,
        "health_observations": observations,
        "post_admission_cleanup": post_admission_cleanup,
        "failure": failure,
        "policy": {
            "allowed_http_methods": ["GET", "PATCH"],
            "coolify_control_plane_only": True,
            "all_existing_validator_votes_required": True,
            "manual_ssh_required": False,
            "public_http_endpoint_created": False,
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
            "validator_vote_proven": admission_proven,
            "validator_activation_proven": admission_proven,
            "routing_or_topology_publication_authorized": False,
        },
        "summary": {
            "clean": complete,
            "complete": complete,
            "target_validator_identity_activated": admission_proven,
            "current_validator_set_reverified": admission_proven,
            "final_validator_set_verified": admission_proven,
            "desired_validator_count": len(desired_set),
            "current_validator_count": len(current_set),
            "logical_vote_count": len(voter_nodes),
            "all_existing_validator_votes_required": True,
            "planned_mutation_count": planned_mutations,
            "attempted_mutation_count": len(receipts),
            "succeeded_mutation_count": succeeded,
            "failed_mutation_count": sum(item.get("status") != "succeeded" for item in receipts),
            "network_access_performed": bool(preconditions or receipts or observations),
            "live_mutation_performed": live_mutation,
            "validator_vote_performed": admission_proven,
            "validator_activation_performed": admission_proven,
            "routing_or_topology_publication_authorized": False,
            "routing_or_topology_published": False,
            "public_endpoint_created": False,
            "manual_ssh_required": False,
            "post_admission_cleanup_clean": cleanup_clean,
            "post_admission_cleanup_performed": post_admission_cleanup is not None,
            "target_service_top_level_healthy": cleanup_clean,
            "replica_sync_evidence_reverified": any(item.get("replica_sync_proven") is True for item in preconditions),
            "blocks_advancing": admission_proven,
            "latest_block_fresh": admission_proven,
            "target_host": target_controller_id,
            "target_node": candidate_node,
            "next_phase": f"add-node-post-admission-observe-{inspected['network']}" if complete else "manual-review-required",
        },
        "next_phase": f"add-node-post-admission-observe-{inspected['network']}" if complete else "manual-review-required",
        "validator_mutation_count": 1 if admission_proven else 0,
        "validator_vote_performed": admission_proven,
        "validator_activation_performed": admission_proven,
        "validator_restart_count": 1 if admission_proven else 0,
        "chain_mutation_count": 1 if admission_proven else 0,
        "service_mutation_count": succeeded,
    }
    evidence_path, evidence_sha = _write_evidence(paths, evidence, operation=operation)
    evidence["evidence"] = {"path": str(evidence_path), "sha256": evidence_sha}
    return evidence


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
        summary.get("blocks_advancing") is True,
        summary.get("latest_block_fresh") is True,
        summary.get("public_endpoint_created") is False,
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
        "final_validator_set": list(document["desired_validator_set"]),
        "validator_vote_proven": True,
        "validator_activation_proven": True,
        "routing_or_topology_published": False,
        "public_endpoint_created": False,
        "next_phase": document["next_phase"],
    }


__all__ = [
    "MotherDeploymentNodeAddValidatorAdmissionError",
    "build_node_add_validator_admission_release",
    "write_node_add_validator_admission_release",
    "verify_node_add_validator_admission_release",
    "inspect_node_add_validator_admission_release",
    "execute_node_add_validator_admission_release",
    "verify_node_add_validator_admission_evidence",
]
