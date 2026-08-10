"""C2 non-validator replica synchronization proof.

This boundary consumes a clean C2 replica-standby verification and authorizes only
the next step: start/synchronize ``mainnetc-super2`` as a non-validator replica
and prove it has joined the existing mainnet chain.  It deliberately does not
authorize QBFT validator admission, validator votes, RPC routing, topology
publication, or treating C2 as active authority.
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

import yaml

from . import atomic_files
from .canonical import canonical_json
from .coolify_state import _DEFAULT_MAX_RESPONSE_BYTES, _DEFAULT_OPENER, resolve_coolify_controller
from .deployment_c2_replica_standby import (
    _C2_CONTROLLER,
    _C2_NODE,
    _EXECUTION_DIRECTORY as _STANDBY_EXECUTION_DIRECTORY,
    _EVIDENCE_KIND as _STANDBY_EVIDENCE_KIND,
    _compose_from_service_record as _standby_compose_from_service_record,
    _compose_semantic_report as _standby_compose_semantic_report,
    _public_node_id,
    _service_record as _standby_service_record,
)
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_A1_NODE = "mainneta-super1"
_A_CONTROLLER = "coolify-a"
_RELEASE_KIND = "main_computer.mother.deployment_c2_replica_sync_release.v1"
_CLAIM_KIND = "main_computer.mother.deployment_c2_replica_sync_execution_claim.v1"
_EVIDENCE_KIND = "main_computer.mother.deployment_c2_replica_sync_evidence.v1"

_RELEASE_DIRECTORY = ("actions", "deployment-c2-replica-sync-releases")
_CLAIM_DIRECTORY = ("actions", "deployment-c2-replica-sync-claims")
_EVIDENCE_DIRECTORY = ("evidence", "deployment-c2-replica-sync")
_STANDBY_EVIDENCE_DIRECTORY = ("evidence", "deployment-c2-replica-standby")

_MIN_RELEASE_SECONDS = 30
_MAX_RELEASE_SECONDS = 3600
_PROOF_IMAGE = "python:3.12-alpine"


class MotherDeploymentC2ReplicaSyncError(Exception):
    """C2 replica synchronization planning or execution failed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> MotherDeploymentC2ReplicaSyncError:
    return MotherDeploymentC2ReplicaSyncError(code, message)


def _identifier(value: Any, label: str) -> str:
    if type(value) is not str or not value.strip():
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_INVALID", f"{label} is missing")
    text = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.:/@+-]+", text):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_INVALID", f"{label} contains unsafe characters")
    return text


def _address(value: Any, label: str) -> str:
    text = str(value or "").lower()
    if re.fullmatch(r"0x[0-9a-f]{40}", text) is None:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_INVALID", f"{label} is not an address")
    return text


def _sha256(value: Any, label: str) -> str:
    text = str(value or "").lower()
    if re.fullmatch(r"[0-9a-f]{64}", text) is None:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_INVALID", f"{label} is not a SHA-256 digest")
    return text


def _parse_utc(value: Any, label: str) -> datetime:
    if type(value) is not str:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_INVALID", f"{label} is missing")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_INVALID", f"{label} is not an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_INVALID", f"{label} must include UTC")
    return parsed.astimezone(timezone.utc)


def _timestamp(value: str | None = None, *, path: str = "timestamp") -> str:
    parsed = _parse_utc(value, path) if value is not None else datetime.now(timezone.utc)
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _age(value: Any, *, now: datetime | None, path: str) -> int:
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    age = int((reference - _parse_utc(value, path)).total_seconds())
    if age < -1:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_FUTURE", f"{path} is in the future")
    return max(0, age)


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
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_STATE_INVALID", "private state cannot be parsed") from exc
    if not isinstance(data, Mapping):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_STATE_INVALID", "private state root is invalid")
    return dict(data)


def _root(paths: PrivateStatePaths, parts: tuple[str, ...]) -> Path:
    current = paths.root
    for part in parts:
        current /= part
    return current


def _ensure_directory(paths: PrivateStatePaths, parts: tuple[str, ...], *, operation: OperationIdentity) -> Path:
    current = paths.root
    for part in parts:
        current = current / part
        atomic_files.ensure_durable_directory(current, operation=operation)
        _secure_private_path(current, is_directory=True, operation=operation)
    return current


def _relative(paths: PrivateStatePaths, path: Path, *, label: str) -> str:
    try:
        return Path(path).resolve(strict=False).relative_to(paths.root.resolve(strict=False)).as_posix()
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_PATH_INVALID", f"{label} is outside runtime state") from exc


def _resolve_locator(paths: PrivateStatePaths, locator: Any, *, label: str) -> Path:
    if type(locator) is not str or not locator.strip():
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_PATH_INVALID", f"{label} locator is missing")
    text = locator.replace("\\", "/")
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text) or ".." in PureWindowsPath(text).parts or ".." in Path(text).parts:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_PATH_INVALID", f"{label} locator is unsafe")
    return (paths.root / Path(text)).resolve(strict=False)


def _beneath(paths: PrivateStatePaths, path: Path, directory: tuple[str, ...], *, label: str) -> Path:
    candidate = Path(path).resolve(strict=False)
    root = _root(paths, directory).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_PATH_INVALID", f"{label} must be beneath {'/'.join(directory)}") from exc
    return candidate


def _canonical_file(path: Path, *, label: str) -> tuple[dict[str, Any], bytes, str]:
    try:
        raw = Path(path).read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_INVALID", f"{label} could not be read as canonical JSON") from exc
    if not isinstance(data, dict) or canonical_json(data) != raw:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_INVALID", f"{label} is not canonical JSON")
    return data, raw, hashlib.sha256(raw).hexdigest()


def _canonical_under(paths: PrivateStatePaths, path: Path, directory: tuple[str, ...], label: str) -> tuple[dict[str, Any], bytes, str]:
    return _canonical_file(_beneath(paths, Path(path), directory, label=label), label=label)


def _digest_without(document: Mapping[str, Any], field: str) -> str:
    copy = dict(document)
    copy[field] = None
    return hashlib.sha256(canonical_json(copy)).hexdigest()


def _contains_sensitive(document: Any) -> bool:
    """Reject obvious secret leakage while allowing public 32-byte hashes.

    Unlike the older broad hexadecimal scan, this checks only sensitive key/value
    contexts and values explicitly named as private keys.
    """

    sensitive_keys = {"private_key", "secret", "api_token", "token", "password", "value"}
    if isinstance(document, Mapping):
        for key, value in document.items():
            name = str(key).lower()
            if any(marker in name for marker in sensitive_keys):
                if isinstance(value, str) and re.fullmatch(r"0x[0-9a-fA-F]{64}", value.strip()):
                    return True
                if isinstance(value, str) and "THISISASECRETTOKENVALUE" in value:
                    return True
            if _contains_sensitive(value):
                return True
    elif isinstance(document, list):
        return any(_contains_sensitive(item) for item in document)
    return False


def _http(
    controller: Any,
    method: str,
    path: str,
    *,
    body: Mapping[str, Any] | None,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    payload = canonical_json(dict(body)) if body is not None else None
    request = urllib.request.Request(
        controller.base_url.rstrip("/") + path,
        data=payload,
        method=method,
        headers={
            "Accept": "application/json",
            "User-Agent": "main-computer-c2-replica-sync/1",
            "Authorization": f"Bearer {controller.api_token}",
            **({"Content-Type": "application/json"} if payload is not None else {}),
        },
    )
    started = time.monotonic()
    try:
        response = opener.open(request, timeout=timeout)
        status = int(getattr(response, "status", response.getcode()))
        raw = response.read(max_response_bytes + 1)
        try:
            response.close()
        except Exception:
            pass
    except urllib.error.HTTPError as exc:
        raw = exc.read(max_response_bytes + 1)
        status = int(exc.code)
    if len(raw) > max_response_bytes:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RESPONSE_TOO_LARGE", "Coolify response exceeded maximum size")
    try:
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed = {"raw": raw.decode("utf-8", errors="replace")}
    return {
        "status": status,
        "ok": 200 <= status < 300,
        "payload": parsed,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def _raw_items(payload: Any) -> list[Mapping[str, Any]]:
    if type(payload) is list:
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        items: list[Mapping[str, Any]] = []
        if any(key in payload for key in ("uuid", "id", "name")):
            items.append(payload)
        for key in ("data", "service", "resource"):
            value = payload.get(key)
            if isinstance(value, Mapping):
                items.append(value)
            elif type(value) is list:
                items.extend(item for item in value if isinstance(item, Mapping))
        for key in ("services", "resources", "applications"):
            value = payload.get(key)
            if type(value) is list:
                items.extend(item for item in value if isinstance(item, Mapping))
        return items
    return []


def _service_record(payload: Any, *, service_uuid: str, node: str) -> Mapping[str, Any]:
    matches = [
        item for item in _raw_items(payload)
        if str(item.get("uuid") or item.get("id") or "") == service_uuid or item.get("name") == node
    ]
    if not matches:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_SERVICE_MISSING", f"service {node} is missing")
    uuid_matches = [item for item in matches if str(item.get("uuid") or item.get("id") or "") == service_uuid]
    return uuid_matches[0] if uuid_matches else matches[0]


def _service_status(item: Mapping[str, Any]) -> str:
    value = item.get("status")
    return value.strip().lower() if isinstance(value, str) else ""


def _node_id_from_enode(value: Any) -> str:
    text = str(value or "").lower()
    match = re.match(r"^enode://([0-9a-f]{128})@", text)
    if not match:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_CHAIN_INVALID", "bootnode enode is invalid")
    return match.group(1)


def _state_private_key(private_state: PrivateStateReadResult, *, network: str, node: str) -> str:
    state = _document(private_state)
    try:
        value = state["networks"][network]["validators"][node]["private_key"]
    except (KeyError, TypeError) as exc:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_STATE_INVALID", f"{node} validator identity is missing") from exc
    if type(value) is not str or re.fullmatch(r"0x[0-9a-fA-F]{64}", value.strip()) is None:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_STATE_INVALID", f"{node} validator identity is invalid")
    return value.strip()


def _expected_current_validators(private_state: PrivateStateReadResult, *, network: str, exclude_node: str) -> list[str]:
    state = _document(private_state)
    try:
        network_state = state["networks"][network]
        validators = network_state["validators"]
    except (KeyError, TypeError) as exc:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_STATE_INVALID", "validator state is missing") from exc
    ordered_nodes: list[str] = []
    deployment = network_state.get("deployment")
    if isinstance(deployment, Mapping) and isinstance(deployment.get("targets"), Mapping):
        ordered_nodes.extend(str(item) for item in deployment["targets"].keys())
    ordered_nodes.extend(str(item) for item in validators.keys())
    seen: set[str] = set()
    result: list[str] = []
    for node in ordered_nodes:
        if node in seen or node == exclude_node:
            continue
        seen.add(node)
        raw = validators.get(node) if isinstance(validators, Mapping) else None
        if isinstance(raw, Mapping) and isinstance(raw.get("address"), str):
            address = raw["address"].lower()
            if re.fullmatch(r"0x[0-9a-f]{40}", address):
                result.append(address)
    if not result:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_STATE_INVALID", "no pre-C2 validator set could be derived")
    return result


def _sync_script(
    *,
    node: str,
    chain_id: int,
    genesis_sha256: str,
    expected_validators: list[str],
    initial_node_id: str,
    replica_node_id: str,
    c2_validator_address: str,
) -> str:
    validators_json = json.dumps([item.lower() for item in expected_validators], separators=(",", ":"))
    return "\n".join([
        "import hashlib, json, os, time, urllib.request",
        f"RPC = 'http://{node}:8545'",
        f"EXPECTED_CHAIN_ID = {chain_id}",
        f"EXPECTED_GENESIS_SHA256 = '{genesis_sha256}'",
        f"EXPECTED_VALIDATORS = {validators_json}",
        f"EXPECTED_INITIAL_NODE_ID = '{initial_node_id.lower()}'",
        f"EXPECTED_REPLICA_NODE_ID = '{replica_node_id.lower()}'",
        f"C2_VALIDATOR_ADDRESS = '{c2_validator_address.lower()}'",
        "PROOF = '/proof/proof.json'",
        "HEALTHY = '/proof/healthy'",
        "MAX_BLOCK_AGE_SECONDS = 90",
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
        "    if validators != EXPECTED_VALIDATORS:",
        "        raise RuntimeError('validator set mismatch')",
        "    if C2_VALIDATOR_ADDRESS in validators:",
        "        raise RuntimeError('C2 is already a validator')",
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
        "    proof = {'chain_id':chain_id,'genesis_block_present':True,'genesis_sha256':genesis_digest,'initial_node_peer_verified':True,'replica_node_id':EXPECTED_REPLICA_NODE_ID,'first_block_number':first,'second_block_number':second,'block_advance':second-first,'latest_block_hash':latest['hash'],'latest_block_number':second,'latest_block_timestamp':block_time,'syncing':False,'validator_set':validators,'c2_validator_active':False,'proved_at':time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
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


def _proof_compose(
    original: str,
    *,
    node: str,
    chain_id: int,
    genesis_sha256: str,
    expected_validators: list[str],
    initial_node_id: str,
    replica_node_id: str,
    c2_validator_address: str,
) -> str:
    marker = "\nvolumes:\n"
    if original.count(marker) != 1:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_COMPOSE_UNSUPPORTED", "C2 standby Compose does not have a supported volume section")
    if "mother-replica-sync-guardian:" in original:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_COMPOSE_UNSUPPORTED", "C2 sync guardian is already present")
    if "8545:8545" in original:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RPC_EXPOSED", "C2 replica Compose must not publish JSON-RPC")
    script = _sync_script(
        node=node,
        chain_id=chain_id,
        genesis_sha256=genesis_sha256,
        expected_validators=expected_validators,
        initial_node_id=initial_node_id,
        replica_node_id=replica_node_id,
        c2_validator_address=c2_validator_address,
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
        "      retries: 24",
        "      start_period: 30s",
        "    volumes:",
        "      - mother-config:/config:ro",
        "      - mother-sync-proof:/proof",
        "",
    ])
    updated = original.replace("      main_computer.mother.replica-sync: blocked", "      main_computer.mother.replica-sync: proof-active")
    updated = updated.replace(marker, "\n" + guardian + marker, 1)
    if "  mother-data:\n" not in updated:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_COMPOSE_UNSUPPORTED", "C2 standby Compose has no mother-data volume")
    updated = updated.replace("  mother-data:\n", "  mother-data:\n  mother-sync-proof:\n", 1)
    guardian_section = updated.split("  mother-replica-sync-guardian:", 1)[1].split("\nvolumes:\n", 1)[0]
    forbidden = ("ports:", "expose:", "traefik.", "domains:", "fqdn:", "8545:8545")
    if any(item in guardian_section for item in forbidden):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_GUARDIAN_EXPOSED", "sync guardian must not expose a port, URL, or proxy route")
    return updated


def _compose_semantic_sha256(text: str) -> str:
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_COMPOSE_INVALID", "Compose YAML cannot be parsed") from exc
    return hashlib.sha256(canonical_json(parsed if isinstance(parsed, dict) else {})).hexdigest()


def _proof_compose_report(*, observed: str, expected: str, node: str) -> dict[str, Any]:
    observed_sha = hashlib.sha256(observed.encode("utf-8")).hexdigest()
    expected_sha = hashlib.sha256(expected.encode("utf-8")).hexdigest()
    exact = observed_sha == expected_sha
    try:
        doc = yaml.safe_load(observed)
    except yaml.YAMLError:
        doc = None
    services = doc.get("services") if isinstance(doc, Mapping) else None
    replica = services.get(node) if isinstance(services, Mapping) and isinstance(services.get(node), Mapping) else {}
    guardian = services.get("mother-replica-sync-guardian") if isinstance(services, Mapping) and isinstance(services.get("mother-replica-sync-guardian"), Mapping) else {}
    observed_text = observed
    checks = {
        "has_replica_service": bool(replica),
        "has_sync_guardian": bool(guardian),
        "guardian_internal_only": bool(guardian) and not any(key in guardian for key in ("ports", "expose", "domains", "fqdn")),
        "guardian_read_only": guardian.get("read_only") is True,
        "guardian_healthcheck_present": isinstance(guardian.get("healthcheck"), Mapping),
        "uses_genesis_file_arg": "--genesis-file=/config/genesis.json" in observed_text,
        "uses_node_private_key_file": "--node-private-key-file=/config/nodekey" in observed_text,
        "sync_mode_full": "--sync-mode=FULL" in observed_text,
        "bootnode_present": "--bootnodes=enode://" in observed_text,
        "p2p_tcp_port_present": "30303:30303/tcp" in observed_text,
        "p2p_udp_port_present": "30303:30303/udp" in observed_text,
        "rpc_not_host_published": "8545:8545" not in observed_text,
        "validator_activation_blocked": "main_computer.mother.validator-activation: blocked" in observed_text,
        "sync_not_blocked": "main_computer.mother.replica-sync: proof-active" in observed_text or "main_computer.mother.replica-sync: blocked" not in observed_text,
        "no_vote_script": "qbft_proposeValidatorVote" not in observed_text,
    }
    missing = [key for key, value in checks.items() if value is not True]
    yaml_equivalent = False
    try:
        yaml_equivalent = yaml.safe_load(observed) == yaml.safe_load(expected)
    except yaml.YAMLError:
        yaml_equivalent = False
    verified = exact or yaml_equivalent or not missing
    return {
        "expected_compose_sha256": expected_sha,
        "observed_compose_sha256": observed_sha,
        "exact_match": exact,
        "yaml_equivalent": yaml_equivalent,
        "coolify_normalized_compose_accepted": verified and not exact,
        "sync_compose_verified": verified,
        "required_checks": checks,
        "missing_required_checks": missing,
    }


def _standby_chain(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    standby_evidence_path: Path,
    *,
    max_age_seconds: int,
    now: datetime | None,
) -> dict[str, Any]:
    evidence, _, evidence_sha = _canonical_under(paths, Path(standby_evidence_path), _STANDBY_EVIDENCE_DIRECTORY, "C2 replica standby evidence")
    summary = evidence.get("summary")
    if (
        evidence.get("kind") != _STANDBY_EVIDENCE_KIND
        or evidence.get("mother_binding") != _binding(private_state)
        or evidence.get("node") != _C2_NODE
        or evidence.get("controller_id") != _C2_CONTROLLER
        or not isinstance(summary, Mapping)
        or summary.get("clean") is not True
        or summary.get("standby_compose_verified") is not True
        or summary.get("replica_sync_performed") is not False
        or summary.get("service_deploy_or_start_performed") is not False
        or summary.get("validator_vote_performed") is not False
        or summary.get("next_phase") != "stage-c2-replica-sync"
        or _contains_sensitive(evidence)
    ):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_STANDBY_INVALID", "C2 replica standby evidence is not a clean current sync gate")
    age = _age(evidence.get("observed_at"), now=now, path="standby evidence observed_at")
    if age > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_STANDBY_STALE", "C2 replica standby evidence is outside the permitted age")

    execution_ref = evidence.get("execution")
    if not isinstance(execution_ref, Mapping):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_STANDBY_INVALID", "standby evidence lacks execution binding")
    execution_path = _resolve_locator(paths, execution_ref.get("locator"), label="C2 replica standby execution")
    execution, _, execution_sha = _canonical_under(paths, execution_path, _STANDBY_EXECUTION_DIRECTORY, "C2 replica standby execution")
    if execution_sha != execution_ref.get("sha256") or execution.get("status") != "pass" or execution.get("mother_binding") != _binding(private_state):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_STANDBY_INVALID", "standby execution binding is invalid")

    tx_ref = execution.get("transaction")
    if not isinstance(tx_ref, Mapping):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_STANDBY_INVALID", "standby execution lacks transaction binding")
    tx_path = _resolve_locator(paths, tx_ref.get("locator"), label="C2 replica standby transaction")
    tx, _, tx_file_sha = _canonical_file(tx_path, label="C2 replica standby transaction")
    if tx.get("c2_replica_standby_transaction_sha256") != tx_ref.get("sha256"):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_STANDBY_INVALID", "standby transaction digest is invalid")
    replica = tx.get("replica")
    initial = tx.get("initial_chain")
    if not isinstance(replica, Mapping) or not isinstance(initial, Mapping):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_STANDBY_INVALID", "standby transaction lacks replica chain data")
    compose_info = replica.get("compose")
    if not isinstance(compose_info, Mapping) or type(compose_info.get("canonical_text")) is not str:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_STANDBY_INVALID", "standby transaction lacks canonical Compose")
    original_compose = compose_info["canonical_text"]
    if hashlib.sha256(original_compose.encode("utf-8")).hexdigest() != execution.get("compose_sha256"):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_STANDBY_INVALID", "standby Compose commitment changed")
    service_uuid = _identifier(evidence.get("service_uuid"), "C2 service UUID")
    chain_id = initial.get("chain_id")
    if type(chain_id) is not int or chain_id <= 0:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_CHAIN_INVALID", "chain ID is invalid")
    bootnode = initial.get("bootnode")
    if not isinstance(bootnode, Mapping):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_CHAIN_INVALID", "bootnode binding is missing")
    bootnode_enode = _identifier(bootnode.get("enode"), "bootnode enode")
    initial_node_id = _node_id_from_enode(bootnode_enode)
    genesis_sha = _sha256(replica.get("genesis_sha256") or tx.get("genesis_transaction", {}).get("genesis_sha256"), "genesis SHA-256")
    network = _identifier(evidence.get("network"), "network")
    c2_private_key = _state_private_key(private_state, network=network, node=_C2_NODE)
    c2_node_id = _public_node_id(c2_private_key)
    c2_address = _address(replica.get("validator_address"), "C2 validator address")
    expected_validators = _expected_current_validators(private_state, network=network, exclude_node=_C2_NODE)

    birth_locator = tx.get("genesis_birth_evidence", {}).get("locator") if isinstance(tx.get("genesis_birth_evidence"), Mapping) else None
    birth_service_uuid = ""
    if birth_locator:
        try:
            birth_path = _resolve_locator(paths, birth_locator, label="genesis birth evidence")
            birth, _, _ = _canonical_file(birth_path, label="genesis birth evidence")
            if type(birth.get("service_uuid")) is str:
                birth_service_uuid = birth["service_uuid"]
        except MotherDeploymentC2ReplicaSyncError:
            birth_service_uuid = ""

    return {
        "standby_evidence": evidence,
        "standby_evidence_path": Path(standby_evidence_path).resolve(strict=False),
        "standby_evidence_sha256": evidence_sha,
        "standby_age_seconds": age,
        "execution": execution,
        "execution_path": execution_path,
        "execution_sha256": execution_sha,
        "transaction": tx,
        "transaction_path": tx_path,
        "transaction_sha256": tx["c2_replica_standby_transaction_sha256"],
        "transaction_file_sha256": tx_file_sha,
        "network": network,
        "service_uuid": service_uuid,
        "controller_id": _C2_CONTROLLER,
        "replica_node": _C2_NODE,
        "initial_node": _A1_NODE,
        "initial_controller_id": _A_CONTROLLER,
        "initial_service_uuid": birth_service_uuid,
        "chain_id": chain_id,
        "genesis_sha256": genesis_sha,
        "bootnode_enode": bootnode_enode,
        "initial_node_id": initial_node_id,
        "replica_node_id": c2_node_id,
        "c2_validator_address": c2_address,
        "expected_validator_set": expected_validators,
        "original_compose": original_compose,
        "original_compose_sha256": execution["compose_sha256"],
    }


def build_c2_replica_sync_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    standby_evidence_path: Path,
    *,
    acknowledged_c2_replica_standby_evidence_sha256: str,
    selected_nodes: Iterable[str] = (),
    standby_max_age_seconds: int = 86400,
    expires_in_seconds: int = 300,
    created_at: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    chain = _standby_chain(
        paths,
        private_state,
        Path(standby_evidence_path),
        max_age_seconds=standby_max_age_seconds,
        now=now,
    )
    ack = _sha256(acknowledged_c2_replica_standby_evidence_sha256, "acknowledged C2 replica standby evidence SHA-256")
    if ack != chain["standby_evidence_sha256"]:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_ACKNOWLEDGEMENT_MISMATCH", "operator acknowledgement does not match the exact C2 standby evidence")
    requested = tuple(_identifier(item, "selected node") for item in selected_nodes)
    if requested and requested != (_C2_NODE,):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_SELECTION_MISMATCH", "C2 replica sync may target only mainnetc-super2")
    if type(expires_in_seconds) is not int or not _MIN_RELEASE_SECONDS <= expires_in_seconds <= _MAX_RELEASE_SECONDS:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_TTL_INVALID", f"expires_in_seconds must be between {_MIN_RELEASE_SECONDS} and {_MAX_RELEASE_SECONDS}")

    proof_compose = _proof_compose(
        chain["original_compose"],
        node=_C2_NODE,
        chain_id=chain["chain_id"],
        genesis_sha256=chain["genesis_sha256"],
        expected_validators=chain["expected_validator_set"],
        initial_node_id=chain["initial_node_id"],
        replica_node_id=chain["replica_node_id"],
        c2_validator_address=chain["c2_validator_address"],
    )
    proof_bytes = proof_compose.encode("utf-8")
    proof_sha = hashlib.sha256(proof_bytes).hexdigest()
    body = {
        "name": _C2_NODE,
        "docker_compose_raw": base64.b64encode(proof_bytes).decode("ascii"),
    }
    body_sha = hashlib.sha256(canonical_json(body)).hexdigest()
    created_text = _timestamp(created_at, path="created_at")
    created_dt = _parse_utc(created_text, "created_at")
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    if created_dt > reference + timedelta(seconds=1):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_INVALID", "release creation time is in the future")
    expires_at = (created_dt + timedelta(seconds=expires_in_seconds)).isoformat(timespec="seconds").replace("+00:00", "Z")
    service_uuid_quoted = urllib.parse.quote(chain["service_uuid"], safe="")
    release: dict[str, Any] = {
        "kind": _RELEASE_KIND,
        "schema_version": 1,
        "created_at": created_text,
        "expires_at": expires_at,
        "network": chain["network"],
        "operation_kind": "MOTHER-OP-ADD-NODE",
        "mother_binding": _binding(private_state),
        "node": _C2_NODE,
        "nodes": [_C2_NODE],
        "controller_id": _C2_CONTROLLER,
        "service_uuid": chain["service_uuid"],
        "staged_scope": "prove-c2-replica-sync-before-validator-admission",
        "c2_replica_standby_evidence": {
            "locator": _relative(paths, chain["standby_evidence_path"], label="C2 replica standby evidence"),
            "sha256": chain["standby_evidence_sha256"],
            "observed_at": chain["standby_evidence"].get("observed_at"),
        },
        "c2_replica_standby_execution": {
            "locator": _relative(paths, chain["execution_path"], label="C2 replica standby execution"),
            "sha256": chain["execution_sha256"],
            "completed_at": chain["execution"].get("completed_at"),
        },
        "c2_replica_standby_transaction": {
            "locator": _relative(paths, chain["transaction_path"], label="C2 replica standby transaction"),
            "sha256": chain["transaction_sha256"],
            "byte_sha256": chain["transaction_file_sha256"],
        },
        "operator_release": {
            "intent": "start-non-validator-c2-replica-and-prove-synchronization",
            "acknowledged_c2_replica_standby_evidence_sha256": ack,
            "requested_use_limit": 1,
        },
        "initial_chain_precondition": {
            "node": chain["initial_node"],
            "controller_id": chain["initial_controller_id"],
            "service_uuid": chain["initial_service_uuid"],
            "bootnode_enode": chain["bootnode_enode"],
            "bootnode_node_id_sha256": hashlib.sha256(chain["initial_node_id"].encode("ascii")).hexdigest(),
            "read_only": True,
        },
        "proof_plan": {
            "replica_node": _C2_NODE,
            "controller_id": _C2_CONTROLLER,
            "service_uuid": chain["service_uuid"],
            "chain_id": chain["chain_id"],
            "genesis_sha256": chain["genesis_sha256"],
            "expected_validator_set": list(chain["expected_validator_set"]),
            "c2_validator_address": chain["c2_validator_address"],
            "bootnode_enode": chain["bootnode_enode"],
            "initial_node_id": chain["initial_node_id"],
            "replica_node_id": chain["replica_node_id"],
            "original_compose": {
                "sha256": chain["original_compose_sha256"],
                "semantic_sha256": _compose_semantic_sha256(chain["original_compose"]),
                "canonical_text": chain["original_compose"],
            },
            "proof_compose": {
                "sha256": proof_sha,
                "semantic_sha256": _compose_semantic_sha256(proof_compose),
                "byte_length": len(proof_bytes),
                "canonical_text": proof_compose,
                "guardian_image": _PROOF_IMAGE,
                "guardian_public_ports": [],
                "guardian_domains": [],
                "host_rpc_mapping_present": False,
            },
            "preconditions": [
                {"controller_id": _C2_CONTROLLER, "method": "GET", "endpoint": f"/api/v1/services/{service_uuid_quoted}", "assertion": "C2 service still has the verified standby Compose"},
                {"controller_id": _A_CONTROLLER, "method": "GET", "endpoint": "/api/v1/services", "assertion": "A1 remains running:healthy", "optional_service_uuid": chain["initial_service_uuid"]},
            ],
            "mutations": [
                {"ordinal": 1, "mutation_id": f"{_C2_NODE}.install-sync-proof-compose", "controller_id": _C2_CONTROLLER, "method": "PATCH", "endpoint": f"/api/v1/services/{service_uuid_quoted}", "canonical_request_body": body, "body_sha256": body_sha, "success_statuses": [200, 201, 202]},
                {"ordinal": 2, "mutation_id": f"{_C2_NODE}.deploy-sync-proof-compose", "controller_id": _C2_CONTROLLER, "method": "GET", "endpoint": f"/api/v1/deploy?uuid={service_uuid_quoted}&force=true", "canonical_request_body": None, "body_sha256": None, "success_statuses": [200, 201, 202]},
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
                    "reserved-c2-node-id",
                    "exact-a1-bootnode-peer",
                    "peer-count-positive",
                    "eth-syncing-false",
                    "fresh-block-height-advancing",
                    "pre-c2-validator-set",
                    "c2-not-validator",
                ],
                "success_signal": "C2 service reports running:healthy under the synchronization-proof Compose",
            },
        },
        "authority": {
            "authorization_source": "explicit-operator-release",
            "synchronization_proof_authorized": True,
            "replica_start_authorized": True,
            "replica_sync_authorized": True,
            "validator_vote_authorized": False,
            "validator_activation_authorized": False,
            "routing_or_topology_publication_authorized": False,
            "live_execution_authorized": False,
            "requested_use_limit": 1,
        },
        "policy": {
            "allowed_http_methods": ["GET", "PATCH"],
            "coolify_control_plane_only": True,
            "initial_node_read_only": True,
            "replica_node_only": True,
            "manual_ssh_required": False,
            "public_http_endpoint_created": False,
            "host_rpc_mapping_present": False,
            "private_keys_materialized_in_memory_only": True,
            "private_keys_persisted": False,
            "secrets_in_output": False,
            "validator_vote_performed": False,
            "validator_activation_performed": False,
            "routing_or_topology_published": False,
        },
        "remaining_blockers": [
            {"code": "MOTHER_DEPLOY_VALIDATOR_ADMISSION_NOT_AUTHORIZED", "message": "C2 replica synchronization does not authorize QBFT admission or voting"},
            {"code": "MOTHER_DEPLOY_C2_RPC_ROUTING_NOT_AUTHORIZED", "message": "C2 replica synchronization does not authorize routing/topology publication"},
        ],
        "summary": {
            "release_valid": True,
            "mutation_count": 2,
            "service_deploy_or_start_authorized": True,
            "replica_sync_authorized": True,
            "replica_node": _C2_NODE,
            "initial_node_read_only": True,
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "validator_vote_authorized": False,
            "validator_activation_authorized": False,
            "routing_or_topology_publication_authorized": False,
            "next_phase_after_apply": "stage-c2-validator-admission",
        },
        "c2_replica_sync_release_sha256": None,
    }
    release["c2_replica_sync_release_sha256"] = _digest_without(release, "c2_replica_sync_release_sha256")
    if _contains_sensitive(release):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_INVALID", "C2 replica sync release contains sensitive material")
    return release


def write_c2_replica_sync_release(paths: PrivateStatePaths, release: Mapping[str, Any], *, operation: OperationIdentity) -> tuple[Path, str]:
    document = dict(release)
    if document.get("kind") != _RELEASE_KIND or document.get("c2_replica_sync_release_sha256") != _digest_without(document, "c2_replica_sync_release_sha256"):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RELEASE_INVALID", "C2 replica sync release is malformed")
    payload = canonical_json(document)
    root = _ensure_directory(paths, _RELEASE_DIRECTORY, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("created_at", "")))[:32] or "c2sync"
    destination = root / f"{stamp}-{document['c2_replica_sync_release_sha256'][:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RELEASE_CONFLICT", "release destination contains different bytes")
    else:
        atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, document["c2_replica_sync_release_sha256"]


def verify_c2_replica_sync_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    standby_max_age_seconds: int = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    release, raw, byte_sha = _canonical_under(paths, Path(release_path), _RELEASE_DIRECTORY, "C2 replica sync release")
    if release.get("kind") != _RELEASE_KIND or release.get("mother_binding") != _binding(private_state) or _contains_sensitive(release):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RELEASE_INVALID", "C2 replica sync release kind or binding is invalid")
    digest = _digest_without(release, "c2_replica_sync_release_sha256")
    if release.get("c2_replica_sync_release_sha256") != digest:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RELEASE_INVALID", "C2 replica sync release digest does not match")
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    created = _parse_utc(release.get("created_at"), "created_at")
    expires = _parse_utc(release.get("expires_at"), "expires_at")
    if reference < created - timedelta(seconds=1) or reference > expires or int((reference - created).total_seconds()) > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RELEASE_EXPIRED", "C2 replica sync release is outside its authority window")
    standby_ref = release.get("c2_replica_standby_evidence")
    if not isinstance(standby_ref, Mapping):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RELEASE_INVALID", "standby evidence binding is missing")
    expected = build_c2_replica_sync_release(
        paths,
        private_state,
        _resolve_locator(paths, standby_ref.get("locator"), label="C2 replica standby evidence"),
        acknowledged_c2_replica_standby_evidence_sha256=_sha256(standby_ref.get("sha256"), "standby evidence SHA-256"),
        selected_nodes=selected_nodes,
        standby_max_age_seconds=standby_max_age_seconds,
        expires_in_seconds=int((expires - created).total_seconds()),
        created_at=release.get("created_at"),
        now=reference,
    )
    if canonical_json(expected) != raw:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RELEASE_INVALID", "C2 replica sync release no longer matches its exact inputs")
    plan = release["proof_plan"]
    return {
        "clean": True,
        "release_path": str(Path(release_path).resolve(strict=False)),
        "c2_replica_sync_release_sha256": digest,
        "byte_sha256": byte_sha,
        "c2_replica_standby_evidence_sha256": standby_ref["sha256"],
        "mother_binding": dict(release["mother_binding"]),
        "network": release["network"],
        "node": _C2_NODE,
        "nodes": [_C2_NODE],
        "initial_node": _A1_NODE,
        "replica_node": _C2_NODE,
        "controller_id": _C2_CONTROLLER,
        "service_uuid": release["service_uuid"],
        "chain_id": plan["chain_id"],
        "genesis_sha256": plan["genesis_sha256"],
        "proof_compose_sha256": plan["proof_compose"]["sha256"],
        "expected_validator_set": list(plan["expected_validator_set"]),
        "mutation_count": len(plan["mutations"]),
        "service_mutation_count": 2,
        "created_at": release["created_at"],
        "expires_at": release["expires_at"],
        "staged_scope": release["staged_scope"],
        "synchronization_proof_authorized": True,
        "replica_start_authorized": True,
        "replica_sync_authorized": True,
        "validator_vote_authorized": False,
        "validator_activation_authorized": False,
        "routing_or_topology_publication_authorized": False,
        "live_execution_authorized": False,
        "network_access_performed": False,
        "live_mutation_performed": False,
        "remaining_blocker_codes": [
            "MOTHER_DEPLOY_C2_REPLICA_SYNC_EXECUTOR_NOT_RUN",
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_NOT_AUTHORIZED",
            "MOTHER_DEPLOY_C2_RPC_ROUTING_NOT_AUTHORIZED",
        ],
    }


def inspect_c2_replica_sync_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    standby_max_age_seconds: int = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    verified = verify_c2_replica_sync_release(
        paths,
        private_state,
        Path(release_path),
        selected_nodes=selected_nodes,
        max_age_seconds=max_age_seconds,
        standby_max_age_seconds=standby_max_age_seconds,
        now=now,
    )
    if _sha256(acknowledged_release_sha256, "acknowledged release SHA-256") != verified["c2_replica_sync_release_sha256"]:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RELEASE_ACKNOWLEDGEMENT_MISMATCH", "C2 replica sync release acknowledgement does not match")
    claim = _root(paths, _CLAIM_DIRECTORY) / f"{verified['c2_replica_sync_release_sha256']}.json"
    return {
        **verified,
        "executor_implemented": True,
        "execute_requested": False,
        "release_already_claimed": claim.exists(),
        "live_execution_authorized": True,
        "network_access_performed": False,
        "live_mutation_performed": False,
        "service_deploy_or_start_performed": False,
        "replica_synchronized": False,
        "initial_node_read_only": True,
        "guardian_internal_only": True,
        "remaining_blocker_codes": [
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_NOT_AUTHORIZED",
            "MOTHER_DEPLOY_C2_RPC_ROUTING_NOT_AUTHORIZED",
        ],
    }


def _write_evidence(paths: PrivateStatePaths, evidence: Mapping[str, Any], *, operation: OperationIdentity) -> tuple[Path, str]:
    payload = canonical_json(dict(evidence))
    digest = hashlib.sha256(payload).hexdigest()
    root = _ensure_directory(paths, _EVIDENCE_DIRECTORY, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(evidence.get("completed_at") or evidence.get("started_at") or ""))[:32] or "c2sync"
    destination = root / f"{stamp}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_EVIDENCE_CONFLICT", "evidence destination contains different bytes")
    else:
        atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def execute_c2_replica_sync_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    standby_max_age_seconds: int = 86400,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    max_wait_seconds: float = 300.0,
    poll_interval_seconds: float = 5.0,
    opener: Any = _DEFAULT_OPENER,
    now: datetime | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    inspected = inspect_c2_replica_sync_release(
        paths,
        private_state,
        Path(release_path),
        acknowledged_release_sha256=acknowledged_release_sha256,
        selected_nodes=selected_nodes,
        max_age_seconds=max_age_seconds,
        standby_max_age_seconds=standby_max_age_seconds,
        now=now,
    )
    if inspected["release_already_claimed"]:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RELEASE_ALREADY_CONSUMED", "this C2 replica sync release is already claimed")
    release, _, _ = _canonical_under(paths, Path(inspected["release_path"]), _RELEASE_DIRECTORY, "C2 replica sync release")
    plan = release["proof_plan"]
    digest = inspected["c2_replica_sync_release_sha256"]
    claim = {
        "kind": _CLAIM_KIND,
        "schema_version": 1,
        "claimed_at": _timestamp(),
        "release": {"locator": _relative(paths, Path(inspected["release_path"]), label="C2 replica sync release"), "sha256": digest},
        "c2_replica_standby_evidence_sha256": inspected["c2_replica_standby_evidence_sha256"],
        "node": _C2_NODE,
        "requested_use_limit": 1,
        "operation_id": operation.operation_id,
    }
    claim_root = _ensure_directory(paths, _CLAIM_DIRECTORY, operation=operation)
    claim_path = claim_root / f"{digest}.json"
    if claim_path.exists():
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RELEASE_ALREADY_CONSUMED", "this C2 replica sync release is already claimed")
    atomic_files.durable_create(claim_path, canonical_json(claim), operation=operation)
    _secure_private_path(claim_path, is_directory=False, operation=operation)

    controller_a = resolve_coolify_controller(private_state, inspected["network"], _A_CONTROLLER)
    controller_c = resolve_coolify_controller(private_state, inspected["network"], _C2_CONTROLLER)
    started = _timestamp()
    preconditions: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    failure: dict[str, str] | None = None

    try:
        if release["initial_chain_precondition"].get("service_uuid"):
            a_inventory = _http(controller_a, "GET", "/api/v1/services", body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
            a_verified = False
            if a_inventory["ok"]:
                item = _service_record(a_inventory["payload"], service_uuid=release["initial_chain_precondition"]["service_uuid"], node=_A1_NODE)
                a_verified = _service_status(item) == "running:healthy"
            preconditions.append({
                "name": "initial-chain-running-healthy-before-sync-proof",
                "controller_id": _A_CONTROLLER,
                "method": "GET",
                "endpoint": "/api/v1/services",
                "status": a_inventory["status"],
                "response_sha256": a_inventory["response_sha256"],
                "verified": a_verified,
                "service_status": "running:healthy" if a_verified else None,
            })
            if not a_verified:
                raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_INITIAL_CHAIN_UNHEALTHY", "A1 is not running:healthy")

        c_detail_endpoint = f"/api/v1/services/{urllib.parse.quote(plan['service_uuid'], safe='')}"
        c_detail = _http(controller_c, "GET", c_detail_endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        if not c_detail["ok"]:
            raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_PRECONDITION_FAILED", f"Coolify C service detail failed with HTTP {c_detail['status']}")
        record = _standby_service_record(c_detail["payload"], service_uuid=plan["service_uuid"])
        current_compose = _standby_compose_from_service_record(record)
        standby_report = _standby_compose_semantic_report(observed=current_compose, expected=plan["original_compose"]["canonical_text"], node=_C2_NODE)
        preconditions.append({
            "name": "c2-replica-standby-compose-before-sync",
            "controller_id": _C2_CONTROLLER,
            "method": "GET",
            "endpoint": c_detail_endpoint,
            "status": c_detail["status"],
            "response_sha256": c_detail["response_sha256"],
            "verified": standby_report.get("standby_compose_verified") is True,
            "compose_verification": standby_report,
        })
        if standby_report.get("standby_compose_verified") is not True:
            raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_STANDBY_MISMATCH", "C2 service no longer has the verified standby Compose")

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
                "mutation_id": mutation["mutation_id"],
                "controller_id": _C2_CONTROLLER,
                "method": mutation["method"],
                "endpoint": mutation["endpoint"],
                "body_sha256": mutation.get("body_sha256"),
                "status": "succeeded" if ok else "failed",
                "live_write_acknowledged": ok,
                "response": {key: response[key] for key in ("status", "response_sha256", "byte_length", "elapsed_ms")},
            })
            if not ok:
                raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_MUTATION_FAILED", f"Coolify rejected C2 sync mutation {mutation['ordinal']}")

        deadline = time.monotonic() + max_wait_seconds
        healthy = False
        last_status = ""
        while True:
            inventory = _http(controller_c, "GET", "/api/v1/services", body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
            if inventory["ok"]:
                item = _service_record(inventory["payload"], service_uuid=plan["service_uuid"], node=_C2_NODE)
                last_status = _service_status(item)
                observations.append({"status": last_status, "response_sha256": inventory["response_sha256"], "observed_at": _timestamp()})
                if last_status == "running:healthy":
                    healthy = True
                    break
            if time.monotonic() >= deadline:
                break
            time.sleep(max(0.0, poll_interval_seconds))
        if not healthy:
            raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_NOT_HEALTHY", f"C2 sync proof did not reach running:healthy (last status {last_status!r})")

        c_detail = _http(controller_c, "GET", c_detail_endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        if not c_detail["ok"]:
            raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_POSTCONDITION_FAILED", f"Coolify C proof detail failed with HTTP {c_detail['status']}")
        record = _standby_service_record(c_detail["payload"], service_uuid=plan["service_uuid"])
        proof_observed = _standby_compose_from_service_record(record)
        proof_report = _proof_compose_report(observed=proof_observed, expected=plan["proof_compose"]["canonical_text"], node=_C2_NODE)
        preconditions.append({
            "name": "c2-replica-sync-proof-compose",
            "controller_id": _C2_CONTROLLER,
            "method": "GET",
            "endpoint": c_detail_endpoint,
            "status": c_detail["status"],
            "response_sha256": c_detail["response_sha256"],
            "verified": proof_report.get("sync_compose_verified") is True,
            "compose_verification": proof_report,
        })
        if proof_report.get("sync_compose_verified") is not True:
            raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_POSTCONDITION_FAILED", "live C2 Compose does not match the sync-proof semantics")
    except MotherDeploymentC2ReplicaSyncError as exc:
        failure = {"code": exc.code, "message": str(exc)[:512]}
    except Exception as exc:  # pragma: no cover
        failure = {"code": "MOTHER_DEPLOY_C2_REPLICA_SYNC_UNEXPECTED_FAILURE", "message": str(exc)[:512]}

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
        "node": _C2_NODE,
        "nodes": [_C2_NODE],
        "initial_node": _A1_NODE,
        "replica_node": _C2_NODE,
        "controller_id": _C2_CONTROLLER,
        "service_uuid": inspected["service_uuid"],
        "release": {"locator": _relative(paths, Path(inspected["release_path"]), label="C2 replica sync release"), "sha256": digest},
        "execution_claim": {"locator": _relative(paths, claim_path, label="C2 replica sync claim")},
        "c2_replica_standby_evidence_sha256": inspected["c2_replica_standby_evidence_sha256"],
        "genesis_sha256": inspected["genesis_sha256"],
        "proof_compose_sha256": inspected["proof_compose_sha256"],
        "expected_validator_set": list(plan["expected_validator_set"]),
        "proof": {
            "mode": "internal-health-assertion-bound-to-sync-proof-compose",
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "host_rpc_mapping_present": False,
            "guardian_internal_only": True,
            "service_status": observations[-1]["status"] if observations else None,
            "predicates_proven_by_guardian": list(plan["proof"]["predicates"]),
            "chain_id": plan["chain_id"],
            "genesis_sha256": plan["genesis_sha256"],
            "expected_validator_set": list(plan["expected_validator_set"]),
            "c2_validator_address": plan["c2_validator_address"],
            "c2_validator_active": False,
            "bootnode_enode": plan["bootnode_enode"],
            "initial_node_id_sha256": hashlib.sha256(plan["initial_node_id"].encode("ascii")).hexdigest(),
            "replica_node_id_sha256": hashlib.sha256(plan["replica_node_id"].encode("ascii")).hexdigest(),
        },
        "authority": {
            "synchronization_proof_authorized": True,
            "replica_start_authorized": True,
            "replica_sync_authorized": True,
            "validator_vote_authorized": False,
            "validator_activation_authorized": False,
            "routing_or_topology_publication_authorized": False,
            "release_consumed": True,
        },
        "policy": {
            "allowed_http_methods": ["GET", "PATCH"],
            "coolify_control_plane_only": True,
            "initial_node_read_only": True,
            "replica_node_only": True,
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "host_rpc_mapping_present": False,
            "private_state_updated": False,
            "secrets_in_output": False,
            "automatic_rollback_performed": False,
            "service_deploy_or_start_performed": complete,
            "replica_sync_performed": complete,
            "validator_vote_performed": False,
            "validator_activation_performed": False,
            "routing_or_topology_published": False,
        },
        "precondition_receipts": preconditions,
        "mutation_receipts": receipts,
        "health_observations": observations,
        "failure": failure,
        "service_mutation_count": 2 if complete else sum(item.get("status") == "succeeded" for item in receipts),
        "identity_mutation_count": 0,
        "chain_mutation_count": 0,
        "validator_mutation_count": 0,
        "validator_restart_count": 0,
        "validator_vote_performed": False,
        "summary": {
            "clean": complete,
            "initial_chain_reverified": complete,
            "replica_synchronized": complete,
            "sync_compose_verified": complete,
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
            "c2_not_validator": complete,
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "initial_node_read_only": True,
            "service_deploy_or_start_performed": complete,
            "replica_sync_performed": complete,
            "validator_vote_authorized": False,
            "validator_activation_authorized": False,
            "routing_or_topology_publication_authorized": False,
            "network_access_performed": bool(preconditions or receipts or observations),
            "live_mutation_performed": any(item.get("live_write_acknowledged") for item in receipts),
            "complete": complete,
            "next_phase": "stage-c2-validator-admission" if complete else "manual-review-required",
        },
        "next_phase": "stage-c2-validator-admission" if complete else "manual-review-required",
    }
    evidence_path, evidence_sha = _write_evidence(paths, evidence, operation=operation)
    evidence["evidence"] = {"path": str(evidence_path), "sha256": evidence_sha}
    return evidence


def verify_c2_replica_sync_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    now: datetime | None = None,
) -> dict[str, Any]:
    evidence, _, evidence_sha = _canonical_under(paths, Path(evidence_path), _EVIDENCE_DIRECTORY, "C2 replica sync evidence")
    summary = evidence.get("summary")
    proof = evidence.get("proof")
    authority = evidence.get("authority")
    if (
        evidence.get("kind") != _EVIDENCE_KIND
        or evidence.get("status") != "pass"
        or evidence.get("mother_binding") != _binding(private_state)
        or evidence.get("node") != _C2_NODE
        or not isinstance(summary, Mapping)
        or not isinstance(proof, Mapping)
        or not isinstance(authority, Mapping)
        or summary.get("clean") is not True
        or summary.get("replica_synchronized") is not True
        or summary.get("service_running_healthy") is not True
        or summary.get("genesis_file_commitment_verified") is not True
        or summary.get("chain_id_verified") is not True
        or summary.get("initial_node_peer_verified") is not True
        or summary.get("sync_complete") is not True
        or summary.get("blocks_advancing") is not True
        or summary.get("latest_block_fresh") is not True
        or summary.get("validator_set_verified") is not True
        or summary.get("c2_not_validator") is not True
        or summary.get("manual_ssh_required") is not False
        or summary.get("public_endpoint_created") is not False
        or summary.get("validator_vote_authorized") is not False
        or summary.get("validator_activation_authorized") is not False
        or summary.get("routing_or_topology_publication_authorized") is not False
        or summary.get("next_phase") != "stage-c2-validator-admission"
        or proof.get("guardian_internal_only") is not True
        or proof.get("host_rpc_mapping_present") is not False
        or proof.get("service_status") != "running:healthy"
        or authority.get("validator_vote_authorized") is not False
        or authority.get("validator_activation_authorized") is not False
        or authority.get("routing_or_topology_publication_authorized") is not False
        or _contains_sensitive(evidence)
    ):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_EVIDENCE_INVALID", "C2 replica sync evidence assertions are incomplete")
    age = _age(evidence.get("completed_at"), now=now, path="completed_at")
    if age > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_EVIDENCE_STALE", "C2 replica sync evidence is outside the freshness window")
    requested = tuple(_identifier(item, "selected node") for item in selected_nodes)
    if requested and requested != (_C2_NODE,):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_SELECTION_MISMATCH", "C2 replica sync evidence targets only mainnetc-super2")
    return {
        "clean": True,
        "evidence_path": str(Path(evidence_path).resolve(strict=False)),
        "evidence_sha256": evidence_sha,
        "age_seconds": age,
        "mother_binding": dict(evidence["mother_binding"]),
        "network": evidence["network"],
        "node": _C2_NODE,
        "nodes": [_C2_NODE],
        "initial_node": _A1_NODE,
        "replica_node": _C2_NODE,
        "controller_id": _C2_CONTROLLER,
        "service_uuid": evidence["service_uuid"],
        "chain_id": proof["chain_id"],
        "genesis_sha256": evidence["genesis_sha256"],
        "expected_validator_set": list(proof["expected_validator_set"]),
        "replica_synchronized": True,
        "service_running_healthy": True,
        "initial_chain_reverified": True,
        "manual_ssh_required": False,
        "public_endpoint_created": False,
        "guardian_internal_only": True,
        "validator_vote_authorized": False,
        "validator_activation_authorized": False,
        "routing_or_topology_publication_authorized": False,
        "next_phase": "stage-c2-validator-admission",
    }


__all__ = [
    "MotherDeploymentC2ReplicaSyncError",
    "build_c2_replica_sync_release",
    "execute_c2_replica_sync_release",
    "inspect_c2_replica_sync_release",
    "verify_c2_replica_sync_evidence",
    "verify_c2_replica_sync_release",
    "write_c2_replica_sync_release",
]
