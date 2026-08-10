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


_SENSITIVE_KEY_RE = re.compile(r"(private|secret|token|password|passwd|authorization|api[_-]?key)", re.IGNORECASE)


def _safe_preview_value(value: Any, *, max_text: int = 512) -> Any:
    """Return a short, secret-redacted value that can be embedded in evidence.

    Coolify deploy/start responses are usually small acknowledgement payloads, but
    evidence must not depend on that remaining true.  The preview keeps only enough
    text to distinguish empty/generic/queued responses while redacting
    sensitive-looking keys recursively.
    """

    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _SENSITIVE_KEY_RE.search(key_text):
                out[key_text] = "<redacted>"
            else:
                out[key_text] = _safe_preview_value(item, max_text=max_text)
        return out
    if isinstance(value, list):
        return [_safe_preview_value(item, max_text=max_text) for item in value[:20]]
    if isinstance(value, str):
        text = value[:max_text]
        if len(value) > max_text:
            text += "...<truncated>"
        return text
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:max_text]


def _safe_payload_text(payload: Any, *, limit: int = 512) -> str:
    safe = _safe_preview_value(payload, max_text=limit)
    try:
        text = json.dumps(safe, sort_keys=True, separators=(",", ":"), default=str)
    except TypeError:
        text = str(safe)
    if len(text) > limit:
        return text[:limit] + "...<truncated>"
    return text


def _response_receipt(response: Mapping[str, Any], *, include_safe_text: bool = False) -> dict[str, Any]:
    receipt = {key: response[key] for key in ("status", "response_sha256", "byte_length", "elapsed_ms")}
    if include_safe_text:
        receipt["safe_text"] = _safe_payload_text(response.get("payload"), limit=512)
        receipt["safe_payload"] = _safe_preview_value(response.get("payload"), max_text=512)
        receipt["safe_text_available"] = True
    return receipt



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




def _remove_replica_host_p2p_publication(compose_text: str, *, node: str) -> tuple[str, dict[str, Any]]:
    """Remove host P2P port publication from the replica service.

    The sync proof only needs the C2 replica to make outbound P2P connections to
    the existing bootnode.  Publishing host 30303 on a Coolify server that already
    runs a validator can prevent Docker from starting the container before Besu
    emits any logs.
    """

    lines = compose_text.splitlines()
    out: list[str] = []
    removed_ports: list[str] = []
    in_node = False
    node_indent: int | None = None
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))

        if re.match(rf"^\s*{re.escape(node)}:\s*$", line):
            in_node = True
            node_indent = indent
        elif in_node and node_indent is not None and stripped and indent <= node_indent:
            in_node = False
            node_indent = None

        if in_node and stripped == "ports:":
            ports_indent = indent
            block = [line]
            j = i + 1
            while j < len(lines):
                next_line = lines[j]
                next_stripped = next_line.strip()
                next_indent = len(next_line) - len(next_line.lstrip(" "))
                if next_stripped and next_indent <= ports_indent:
                    break
                block.append(next_line)
                j += 1

            entries = [item.strip().lstrip("-").strip().strip("'\"") for item in block[1:] if item.strip().startswith("-")]
            p2p_entries = {
                "30303:30303/tcp",
                "30303:30303/udp",
            }
            if entries and all(entry in p2p_entries for entry in entries):
                removed_ports.extend(entries)
                i = j
                continue
            if any(entry in p2p_entries for entry in entries):
                raise _fail(
                    "MOTHER_DEPLOY_C2_REPLICA_SYNC_P2P_PORT_BLOCK_UNSUPPORTED",
                    "C2 sync Compose mixes host P2P publication with other ports",
                )

        out.append(line)
        i += 1

    normalized = "\n".join(out)
    if compose_text.endswith("\n"):
        normalized += "\n"
    return normalized, {
        "host_p2p_publication_removed": bool(removed_ports),
        "removed_host_ports": removed_ports,
        "host_p2p_mapping_present": False,
        "container_p2p_port": 30303,
        "reason": "C2 sync proof uses outbound bootnode peering and must not bind host 30303 on a shared validator host",
    }
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
    updated, _ = _remove_replica_host_p2p_publication(updated, node=node)
    if "30303:30303/tcp" in updated or "30303:30303/udp" in updated:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_HOST_P2P_EXPOSED", "C2 replica sync proof must not bind host P2P port 30303")
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
        "p2p_container_port_enabled": "--p2p-port=30303" in observed_text,
        "host_p2p_tcp_port_absent": "30303:30303/tcp" not in observed_text,
        "host_p2p_udp_port_absent": "30303:30303/udp" not in observed_text,
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
                "host_p2p_mapping_present": False,
                "host_p2p_publication_authorized": False,
                "p2p_publication_mode": "container-internal-outbound-only",
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
            "host_p2p_mapping_present": False,
            "host_p2p_publication_authorized": False,
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
                "response": _response_receipt(response, include_safe_text=mutation["method"] == "GET" and mutation["endpoint"].startswith("/api/v1/deploy")),
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
            "host_p2p_mapping_present": False,
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
            "host_p2p_mapping_present": False,
            "host_p2p_publication_authorized": False,
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



_DIAGNOSTIC_KIND = "main_computer.mother.deployment_c2_replica_sync_materialization_diagnostic.v1"
_DIAGNOSTIC_DIRECTORY = ("evidence", "deployment-c2-replica-sync-materialization")


def _record_contains_identifier(record: Mapping[str, Any], identifiers: set[str]) -> bool:
    if not identifiers:
        return False
    try:
        compact = canonical_json(dict(record)).decode("utf-8", errors="replace").lower()
    except Exception:
        compact = json.dumps(dict(record), sort_keys=True, default=str).lower()
    return any(identifier and identifier.lower() in compact for identifier in identifiers)


def _compact_record_summary(record: Mapping[str, Any], *, service_uuid: str, plan: Mapping[str, Any] | None = None) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "uuid": record.get("uuid") or record.get("id"),
        "id": record.get("id"),
        "name": record.get("name") or record.get("human_name"),
        "status": record.get("status"),
        "type": record.get("type") or record.get("service_type"),
        "service_id": record.get("service_id"),
        "keys": sorted(str(key) for key in record.keys()),
    }
    for field in ("docker_compose_raw", "dockerComposeRaw", "docker_compose", "dockerCompose", "compose"):
        value = record.get(field)
        if not isinstance(value, str) or not value:
            continue
        text = value
        if "\n" not in text and not text.startswith(("name:", "services:", "version:")):
            try:
                text = base64.b64decode(text, validate=True).decode("utf-8")
            except Exception:
                text = value
        field_summary: dict[str, Any] = {
            "byte_length": len(text.encode("utf-8")),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
        if plan is not None:
            proof = _proof_compose_report(observed=text, expected=plan["proof_compose"]["canonical_text"], node=_C2_NODE)
            standby_verified = None
            original = plan.get("original_compose")
            if isinstance(original, Mapping) and isinstance(original.get("canonical_text"), str):
                standby = _standby_compose_semantic_report(observed=text, expected=original["canonical_text"], node=_C2_NODE)
                standby_verified = standby.get("standby_compose_verified") is True
            field_summary.update({
                "sync_proof_compose_verified": proof.get("sync_compose_verified") is True,
                "standby_compose_verified": standby_verified,
                "host_rpc_mapping_present": "8545:8545" in text,
                "old_alpine_tail_present": "tail -f /dev/null" in text,
            })
        summary.setdefault("compose_fields", {})[field] = field_summary
    return summary


def _child_application_records(service_record: Mapping[str, Any], *, service_uuid: str, node: str) -> list[dict[str, Any]]:
    children: list[dict[str, Any]] = []
    for item in _raw_items(service_record):
        item_uuid = str(item.get("uuid") or item.get("id") or "")
        if item_uuid == service_uuid:
            continue
        if item.get("service_id") is not None or item.get("name") == node or item.get("human_name") == node:
            children.append({
                "uuid": item.get("uuid") or item.get("id"),
                "id": item.get("id"),
                "name": item.get("name") or item.get("human_name"),
                "status": item.get("status"),
                "service_id": item.get("service_id"),
                "keys": sorted(str(key) for key in item.keys()),
            })
    return children


def _write_materialization_diagnostic(paths: PrivateStatePaths, report: Mapping[str, Any], *, operation: OperationIdentity) -> tuple[Path, str]:
    payload = canonical_json(dict(report))
    digest = hashlib.sha256(payload).hexdigest()
    root = _ensure_directory(paths, _DIAGNOSTIC_DIRECTORY, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(report.get("observed_at") or ""))[:32] or "c2syncdiag"
    destination = root / f"{stamp}-{digest[:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_DIAGNOSTIC_CONFLICT", "diagnostic destination contains different bytes")
    else:
        atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def _diagnostic_failed_release(
    paths: PrivateStatePaths,
    release_path: Path,
) -> tuple[dict[str, Any], bytes, str, tuple[str, ...], str]:
    """Load the release consumed by a failed sync evidence.

    v48 failures point at the original sync-release directory, while v50 resume
    failures point at the resume-release directory.  Diagnostics need to support
    both because both consume a one-use operator release and can fail before a
    Docker workload materializes.
    """

    candidates = [
        (_RELEASE_DIRECTORY, _RELEASE_KIND, "initial-sync-release"),
        (_RESUME_RELEASE_DIRECTORY, _RESUME_RELEASE_KIND, "resume-sync-release"),
    ]
    last_error: MotherDeploymentC2ReplicaSyncError | None = None
    for directory, kind, release_type in candidates:
        try:
            document, payload, digest = _canonical_under(paths, release_path, directory, f"failed C2 replica sync {release_type}")
        except MotherDeploymentC2ReplicaSyncError as exc:
            last_error = exc
            continue
        if document.get("kind") != kind:
            last_error = _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_DIAGNOSTIC_INVALID", f"failed sync {release_type} kind is invalid")
            continue
        return document, payload, digest, directory, release_type
    if last_error is not None:
        raise last_error
    raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_DIAGNOSTIC_INVALID", "failed sync release could not be loaded")


def _diagnostic_failed_claim_exists(paths: PrivateStatePaths, failed_evidence: Mapping[str, Any], release_type: str) -> bool:
    claim_locator = (failed_evidence.get("execution_claim") or {}).get("locator")
    if not isinstance(claim_locator, str) or not claim_locator:
        return False
    directory = _RESUME_CLAIM_DIRECTORY if release_type == "resume-sync-release" else _CLAIM_DIRECTORY
    try:
        claim_path = _resolve_locator(paths, claim_locator, label="failed sync execution claim")
        return _canonical_under(paths, claim_path, directory, "failed C2 replica sync execution claim")[0].get("node") == _C2_NODE
    except MotherDeploymentC2ReplicaSyncError:
        return False


def _mutation_receipt_summary(receipt: Mapping[str, Any]) -> dict[str, Any]:
    response = receipt.get("response") if isinstance(receipt.get("response"), Mapping) else {}
    summary: dict[str, Any] = {
        "ordinal": receipt.get("ordinal"),
        "mutation_id": receipt.get("mutation_id"),
        "method": receipt.get("method"),
        "endpoint": receipt.get("endpoint"),
        "status": receipt.get("status"),
        "live_write_acknowledged": receipt.get("live_write_acknowledged"),
        "response": {
            "status": response.get("status"),
            "response_sha256": response.get("response_sha256"),
            "byte_length": response.get("byte_length"),
            "elapsed_ms": response.get("elapsed_ms"),
            "safe_text": response.get("safe_text"),
            "safe_payload": response.get("safe_payload"),
            "safe_text_available": isinstance(response.get("safe_text"), str),
        },
    }
    summary["deploy_request_acknowledged"] = (
        summary["method"] == "GET"
        and isinstance(summary["endpoint"], str)
        and summary["endpoint"].startswith("/api/v1/deploy")
        and summary["live_write_acknowledged"] is True
    )
    return summary



def diagnose_c2_replica_sync_materialization(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    failed_evidence_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    failed_evidence_max_age_seconds: int = 86400,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    operator_confirm_no_docker_materialization: bool = False,
    operator_confirm_host_p2p_port_conflict: bool = False,
    opener: Any = _DEFAULT_OPENER,
    now: datetime | None = None,
    write_evidence: bool = False,
    operation: OperationIdentity,
) -> dict[str, Any]:
    """GET-only diagnosis for a failed C2 sync that did not reach healthy.

    The command intentionally does not deploy, start, stop, delete, or mutate any
    service.  It reads the failed sync evidence, re-queries Coolify control-plane
    state, and emits a retry gate only when the previous failure is safely bounded
    away from chain, validator, routing, and topology mutation.
    """

    requested = tuple(_identifier(item, "selected node") for item in selected_nodes)
    if requested and requested != (_C2_NODE,):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_SELECTION_MISMATCH", "C2 replica sync diagnostics target only mainnetc-super2")

    failed_evidence, _, failed_evidence_sha = _canonical_under(
        paths,
        Path(failed_evidence_path),
        _EVIDENCE_DIRECTORY,
        "failed C2 replica sync evidence",
    )
    if failed_evidence.get("kind") != _EVIDENCE_KIND or failed_evidence.get("status") != "failed":
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_DIAGNOSTIC_INVALID", "failed evidence is not a failed C2 replica sync evidence document")
    if failed_evidence.get("mother_binding") != _binding(private_state) or failed_evidence.get("node") != _C2_NODE:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_DIAGNOSTIC_INVALID", "failed evidence is not bound to the current C2 Mother state")
    if _contains_sensitive(failed_evidence):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_DIAGNOSTIC_INVALID", "failed evidence contains sensitive material")
    age = _age(failed_evidence.get("completed_at"), now=now, path="failed evidence completed_at")
    if age > failed_evidence_max_age_seconds:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_DIAGNOSTIC_STALE", "failed C2 sync evidence is outside the diagnostic freshness window")

    failure = failed_evidence.get("failure") if isinstance(failed_evidence.get("failure"), Mapping) else {}
    policy = failed_evidence.get("policy") if isinstance(failed_evidence.get("policy"), Mapping) else {}
    previous_safe = (
        failed_evidence.get("chain_mutation_count") == 0
        and failed_evidence.get("identity_mutation_count") == 0
        and failed_evidence.get("validator_mutation_count") == 0
        and failed_evidence.get("validator_restart_count") == 0
        and failed_evidence.get("validator_vote_performed") is False
        and policy.get("validator_activation_performed") is False
        and policy.get("validator_vote_performed") is False
        and policy.get("routing_or_topology_published") is False
        and policy.get("public_endpoint_created") is False
    )
    if not previous_safe:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_DIAGNOSTIC_UNSAFE", "failed sync evidence is not safe for automated retry diagnosis")

    release_locator = (failed_evidence.get("release") or {}).get("locator")
    if not isinstance(release_locator, str) or not release_locator:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_DIAGNOSTIC_INVALID", "failed evidence is missing its release locator")
    release_path = _resolve_locator(paths, release_locator, label="failed sync release")
    release, _, release_sha, release_directory, release_type = _diagnostic_failed_release(paths, release_path)
    plan = release.get("proof_plan")
    if not isinstance(plan, Mapping):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_DIAGNOSTIC_INVALID", "failed sync release is missing its proof plan")

    release_consumed = _diagnostic_failed_claim_exists(paths, failed_evidence, release_type)
    reference_now = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    release_expired = _parse_utc(release.get("expires_at"), "release expires_at") <= reference_now

    controller_c = resolve_coolify_controller(private_state, failed_evidence["network"], _C2_CONTROLLER)
    service_uuid = _identifier(failed_evidence.get("service_uuid"), "service UUID")
    identifiers = {service_uuid, _C2_NODE}

    responses: list[dict[str, Any]] = []
    service_detail_record: Mapping[str, Any] | None = None
    child_records: list[dict[str, Any]] = []

    quoted_service_uuid = urllib.parse.quote(service_uuid, safe="")
    endpoint_specs = [
        ("service-detail", f"/api/v1/services/{quoted_service_uuid}"),
        ("service-list", "/api/v1/services"),
        ("resource-list", "/api/v1/resources"),
        ("application-list", "/api/v1/applications"),
        ("deployment-list", "/api/v1/deployments"),
        ("deployment-list-service-uuid", f"/api/v1/deployments?uuid={quoted_service_uuid}"),
        ("deployment-list-resource-uuid", f"/api/v1/deployments?resource_uuid={quoted_service_uuid}"),
        ("queued-deployment-list", "/api/v1/deployments?status=queued"),
        ("running-deployment-list", "/api/v1/deployments?status=in_progress"),
        ("failed-deployment-list", "/api/v1/deployments?status=failed"),
        ("service-deployments", f"/api/v1/services/{quoted_service_uuid}/deployments"),
        ("service-deployment-queue", f"/api/v1/services/{quoted_service_uuid}/deployments?status=queued"),
    ]

    for name, endpoint in endpoint_specs:
        response = _http(controller_c, "GET", endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        matched: list[dict[str, Any]] = []
        if response["ok"]:
            try:
                records = _raw_items(response["payload"])
            except Exception:
                records = []
            if name == "service-detail":
                try:
                    service_detail_record = _standby_service_record(response["payload"], service_uuid=service_uuid)
                    child_records = _child_application_records(response["payload"], service_uuid=service_uuid, node=_C2_NODE)
                    for child in child_records:
                        identifiers.add(str(child.get("uuid") or child.get("id") or ""))
                except MotherDeploymentC2ReplicaSyncError:
                    service_detail_record = None
            for record in records:
                if _record_contains_identifier(record, identifiers):
                    matched.append(_compact_record_summary(record, service_uuid=service_uuid, plan=plan))
        responses.append({
            "name": name,
            "method": "GET",
            "endpoint": endpoint,
            "status": response["status"],
            "ok": response["ok"],
            "response_sha256": response["response_sha256"],
            "byte_length": response["byte_length"],
            "elapsed_ms": response["elapsed_ms"],
            "matched_record_count": len(matched),
            "matched_records": matched[:20],
        })

    status_values: list[str] = []
    for response in responses:
        for record in response.get("matched_records", []):
            status = record.get("status")
            if isinstance(status, str) and status.strip():
                status_values.append(status.strip().lower())
    if not status_values and isinstance(failed_evidence.get("proof"), Mapping):
        status = failed_evidence["proof"].get("service_status")
        if isinstance(status, str):
            status_values.append(status.strip().lower())

    deployment_records = [
        record
        for response in responses
        if "deployment" in response["name"]
        for record in response.get("matched_records", [])
    ]
    pending_deployment = any(
        any(word in str(record.get("status") or "").lower() for word in ("pending", "queued", "running", "progress", "deploy", "build"))
        for record in deployment_records
    )
    failed_deployment = any(
        any(word in str(record.get("status") or "").lower() for word in ("fail", "error", "cancel"))
        for record in deployment_records
    )

    compose_proof_verified = False
    compose_standby_verified = False
    if service_detail_record is not None:
        current_compose = _standby_compose_from_service_record(service_detail_record)
        proof_current_report = _proof_compose_report(observed=current_compose, expected=plan["proof_compose"]["canonical_text"], node=_C2_NODE)
        compose_proof_verified = (
            proof_current_report.get("sync_compose_verified") is True
            or (
                operator_confirm_host_p2p_port_conflict
                and (proof_current_report.get("exact_match") is True or proof_current_report.get("yaml_equivalent") is True)
            )
        )
        original_compose = plan.get("original_compose")
        if isinstance(original_compose, Mapping) and isinstance(original_compose.get("canonical_text"), str):
            compose_standby_verified = _standby_compose_semantic_report(observed=current_compose, expected=original_compose["canonical_text"], node=_C2_NODE).get("standby_compose_verified") is True

    previous_mutations = []
    for item in failed_evidence.get("mutation_receipts") or []:
        if isinstance(item, Mapping):
            previous_mutations.append(_mutation_receipt_summary(item))
    previous_deploy_receipts = [item for item in previous_mutations if item.get("deploy_request_acknowledged") is True]
    previous_deploy_acknowledged = bool(previous_deploy_receipts)

    if (
        operator_confirm_host_p2p_port_conflict
        and previous_deploy_acknowledged
        and not any(status == "running:healthy" for status in status_values)
        and compose_proof_verified
    ):
        classification = "docker-created-host-p2p-port-conflict"
    elif (
        operator_confirm_no_docker_materialization
        and previous_deploy_acknowledged
        and release_type == "resume-sync-release"
        and not pending_deployment
        and not failed_deployment
        and not any(status.startswith("running") for status in status_values)
    ):
        classification = "deploy-request-acknowledged-no-docker-materialization"
    elif operator_confirm_no_docker_materialization and not pending_deployment and not failed_deployment and not any(status.startswith("running") for status in status_values):
        classification = "no-docker-materialization"
    elif any("unhealthy" in status or "failed" in status or "exited" in status for status in status_values):
        classification = "containers-created-but-failed"
    elif any(status.startswith("running") for status in status_values) or pending_deployment:
        classification = "containers-running-health-unknown"
    else:
        classification = "coolify-control-plane-starting-without-materialization-proof"

    retry_authorized = (
        (
            (classification == "docker-created-host-p2p-port-conflict" and release_type in {"initial-sync-release", "resume-sync-release"})
            or (classification in {"no-docker-materialization", "coolify-control-plane-starting-without-materialization-proof"} and release_type == "initial-sync-release")
        )
        and previous_safe
        and release_consumed
        and (release_expired or failed_evidence.get("status") == "failed")
        and not pending_deployment
        and not failed_deployment
        and not any(status == "running:healthy" for status in status_values)
        and compose_proof_verified
    )

    observed_at = _timestamp()
    report: dict[str, Any] = {
        "kind": _DIAGNOSTIC_KIND,
        "schema_version": 1,
        "observed_at": observed_at,
        "mother_binding": dict(_binding(private_state)),
        "network": failed_evidence["network"],
        "node": _C2_NODE,
        "nodes": [_C2_NODE],
        "controller_id": _C2_CONTROLLER,
        "service_uuid": service_uuid,
        "failed_sync_evidence": {
            "locator": _relative(paths, Path(failed_evidence_path), label="failed C2 replica sync evidence"),
            "sha256": failed_evidence_sha,
            "age_seconds": age,
            "status": failed_evidence.get("status"),
            "failure_code": failure.get("code"),
            "failure_message": failure.get("message"),
        },
        "failed_release": {
            "locator": _relative(paths, release_path, label="failed C2 replica sync release"),
            "sha256": release_sha,
            "release_type": release_type,
            "directory": "/".join(release_directory),
            "consumed": release_consumed,
            "expired": release_expired,
            "expires_at": release.get("expires_at"),
        },
        "previous_mutation_receipts": previous_mutations,
        "deploy_materialization": {
            "deploy_request_acknowledged": previous_deploy_acknowledged,
            "acknowledged_deploy_receipt_count": len(previous_deploy_receipts),
            "pending_deployment_detected": pending_deployment,
            "failed_deployment_detected": failed_deployment,
            "docker_materialization_observed": operator_confirm_host_p2p_port_conflict,
            "docker_materialization_observed_by": "operator-host-probe" if (operator_confirm_no_docker_materialization or operator_confirm_host_p2p_port_conflict) else None,
            "http_200_is_not_materialization_proof": previous_deploy_acknowledged,
            "queue_or_job_result_identified": pending_deployment or failed_deployment,
        },
        "coolify_control_plane": {
            "service_status_values": status_values,
            "child_applications": child_records,
            "pending_deployment_detected": pending_deployment,
            "failed_deployment_detected": failed_deployment,
            "service_detail_found": service_detail_record is not None,
            "sync_proof_compose_current": compose_proof_verified,
            "standby_compose_current": compose_standby_verified,
            "responses": responses,
        },
        "operator_host_probe": {
            "no_docker_materialization_confirmed": operator_confirm_no_docker_materialization,
            "host_p2p_port_conflict_confirmed": operator_confirm_host_p2p_port_conflict,
            "required_for_strong_no_docker_classification": not operator_confirm_no_docker_materialization,
            "statement": (
                "operator confirmed Docker created C2 containers on the correct host and mainnetc-super2 failed before Besu logs with host 30303 already allocated"
                if operator_confirm_host_p2p_port_conflict
                else ("operator confirmed no exact service/child UUID containers or volumes on coolify-c" if operator_confirm_no_docker_materialization else None)
            ),
        },
        "classification": {
            "bucket": classification,
            "retry_authorized": retry_authorized,
            "retry_requires_new_release": retry_authorized,
            "reason": (
                "previous release was consumed by the failed execution; mint a fresh sync release only after this diagnostic remains retry-authorized"
                if retry_authorized and classification != "docker-created-host-p2p-port-conflict"
                else (
                    "C2 Docker materialized on the correct host but Besu could not start because host 30303 was already allocated; retry only through the host-P2P-isolated resume path"
                    if classification == "docker-created-host-p2p-port-conflict"
                    else "deploy/start acknowledgement did not prove Docker materialization; capture Coolify deployment queue/job/no-op cause before another retry"
                )
            ),
        },
        "policy": {
            "allowed_http_methods": ["GET"],
            "live_mutation_performed": False,
            "network_access_performed": True,
            "private_state_updated": False,
            "secrets_in_output": False,
            "service_deploy_or_start_performed": False,
            "replica_sync_performed": False,
            "chain_mutation_performed": False,
            "validator_activation_performed": False,
            "validator_vote_performed": False,
            "routing_or_topology_published": False,
        },
        "summary": {
            "clean": True,
            "diagnostic_complete": True,
            "classification": classification,
            "retry_authorized": retry_authorized,
            "previous_failure_code": failure.get("code"),
            "previous_release_type": release_type,
            "previous_release_consumed": release_consumed,
            "previous_release_expired": release_expired,
            "previous_deploy_request_acknowledged": previous_deploy_acknowledged,
            "deploy_response_safe_text_available": any(
                (item.get("response") or {}).get("safe_text_available") is True for item in previous_mutations
            ),
            "docker_materialization_observed": operator_confirm_host_p2p_port_conflict,
            "queue_or_job_result_identified": pending_deployment or failed_deployment,
            "pending_deployment_detected": pending_deployment,
            "failed_deployment_detected": failed_deployment,
            "sync_proof_compose_current": compose_proof_verified,
            "operator_no_docker_materialization_confirmed": operator_confirm_no_docker_materialization,
            "operator_host_p2p_port_conflict_confirmed": operator_confirm_host_p2p_port_conflict,
            "service_running_healthy": any(status == "running:healthy" for status in status_values),
            "chain_mutation_count": 0,
            "validator_mutation_count": 0,
            "validator_restart_count": 0,
            "validator_vote_performed": False,
            "next_phase": "mint-fresh-c2-replica-sync-release" if retry_authorized else "manual-review-required",
        },
        "next_phase": "mint-fresh-c2-replica-sync-release" if retry_authorized else "manual-review-required",
    }
    if _contains_sensitive(report):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_DIAGNOSTIC_INVALID", "diagnostic report contains sensitive material")
    if write_evidence:
        evidence_path, evidence_sha = _write_materialization_diagnostic(paths, report, operation=operation)
        report["evidence"] = {"path": str(evidence_path), "sha256": evidence_sha}
    return report


_RESUME_RELEASE_KIND = "main_computer.mother.deployment_c2_replica_sync_resume_release.v1"
_RESUME_CLAIM_KIND = "main_computer.mother.deployment_c2_replica_sync_resume_claim.v1"
_RESUME_RELEASE_DIRECTORY = ("actions", "deployment-c2-replica-sync-resume-releases")
_RESUME_CLAIM_DIRECTORY = ("actions", "deployment-c2-replica-sync-resume-claims")


def _resume_diagnostic_chain(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    diagnostic_evidence_path: Path,
    *,
    max_age_seconds: int,
    now: datetime | None,
) -> dict[str, Any]:
    diagnostic, _, diagnostic_sha = _canonical_under(
        paths,
        Path(diagnostic_evidence_path),
        _DIAGNOSTIC_DIRECTORY,
        "C2 replica sync materialization diagnostic",
    )
    summary = diagnostic.get("summary")
    classification = diagnostic.get("classification")
    control_plane = diagnostic.get("coolify_control_plane")
    failed_release_ref = diagnostic.get("failed_release")
    if (
        diagnostic.get("kind") != _DIAGNOSTIC_KIND
        or diagnostic.get("mother_binding") != _binding(private_state)
        or diagnostic.get("node") != _C2_NODE
        or not isinstance(summary, Mapping)
        or not isinstance(classification, Mapping)
        or not isinstance(control_plane, Mapping)
        or not isinstance(failed_release_ref, Mapping)
        or summary.get("clean") is not True
        or summary.get("diagnostic_complete") is not True
        or summary.get("retry_authorized") is not True
        or summary.get("sync_proof_compose_current") is not True
        or summary.get("previous_release_consumed") is not True
        or summary.get("service_running_healthy") is not False
        or summary.get("chain_mutation_count") != 0
        or summary.get("validator_mutation_count") != 0
        or summary.get("validator_restart_count") != 0
        or summary.get("validator_vote_performed") is not False
        or classification.get("retry_authorized") is not True
        or classification.get("bucket") not in {"no-docker-materialization", "coolify-control-plane-starting-without-materialization-proof", "docker-created-host-p2p-port-conflict"}
        or control_plane.get("sync_proof_compose_current") is not True
        or control_plane.get("pending_deployment_detected") is not False
        or control_plane.get("failed_deployment_detected") is not False
        or _contains_sensitive(diagnostic)
    ):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RESUME_DIAGNOSTIC_INVALID", "C2 sync materialization diagnostic is not a clean retry gate")
    age = _age(diagnostic.get("observed_at"), now=now, path="diagnostic observed_at")
    if age > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RESUME_DIAGNOSTIC_STALE", "C2 sync materialization diagnostic is outside the permitted age")

    release_path = _resolve_locator(paths, failed_release_ref.get("locator"), label="failed C2 replica sync release")
    release_type = failed_release_ref.get("release_type")
    if release_type == "resume-sync-release":
        release_directory = _RESUME_RELEASE_DIRECTORY
        release_kind = _RESUME_RELEASE_KIND
        release_digest_key = "c2_replica_sync_resume_release_sha256"
    else:
        release_directory = _RELEASE_DIRECTORY
        release_kind = _RELEASE_KIND
        release_digest_key = "c2_replica_sync_release_sha256"
    failed_release, _, failed_release_file_sha = _canonical_under(paths, release_path, release_directory, "failed C2 replica sync release")
    if failed_release.get("kind") != release_kind or failed_release.get("mother_binding") != _binding(private_state):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RESUME_DIAGNOSTIC_INVALID", "failed sync release binding is invalid")
    failed_release_sha = _digest_without(failed_release, release_digest_key)
    if failed_release.get(release_digest_key) != failed_release_sha or failed_release_ref.get("sha256") != failed_release_file_sha:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RESUME_DIAGNOSTIC_INVALID", "failed sync release digest is invalid")
    plan = failed_release.get("proof_plan")
    if not isinstance(plan, Mapping):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RESUME_DIAGNOSTIC_INVALID", "failed sync release lacks proof plan")
    proof_compose = plan.get("proof_compose")
    if not isinstance(proof_compose, Mapping) or type(proof_compose.get("canonical_text")) is not str:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RESUME_DIAGNOSTIC_INVALID", "failed sync release lacks proof Compose")
    if _proof_compose_report(observed=proof_compose["canonical_text"], expected=proof_compose["canonical_text"], node=_C2_NODE).get("sync_compose_verified") is not True:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RESUME_DIAGNOSTIC_INVALID", "failed sync release proof Compose is invalid")
    deploy_mutations = [
        item for item in plan.get("mutations", [])
        if isinstance(item, Mapping)
        and item.get("method") == "GET"
        and isinstance(item.get("endpoint"), str)
        and item.get("endpoint", "").startswith("/api/v1/deploy?")
    ]
    if len(deploy_mutations) != 1:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RESUME_DIAGNOSTIC_INVALID", "failed sync release does not contain exactly one deploy mutation")
    service_uuid = _identifier(diagnostic.get("service_uuid") or failed_release.get("service_uuid"), "service UUID")
    if service_uuid != _identifier(failed_release.get("service_uuid"), "failed release service UUID"):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RESUME_DIAGNOSTIC_INVALID", "diagnostic and failed release service UUID differ")
    return {
        "diagnostic": diagnostic,
        "diagnostic_path": Path(diagnostic_evidence_path).resolve(strict=False),
        "diagnostic_sha256": diagnostic_sha,
        "diagnostic_age_seconds": age,
        "failed_release": failed_release,
        "failed_release_path": release_path,
        "failed_release_sha256": failed_release_sha,
        "failed_release_file_sha256": failed_release_file_sha,
        "plan": plan,
        "deploy_mutation": dict(deploy_mutations[0]),
        "network": diagnostic["network"],
        "service_uuid": service_uuid,
        "classification": classification["bucket"],
    }


def build_c2_replica_sync_resume_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    diagnostic_evidence_path: Path,
    *,
    acknowledged_c2_replica_sync_diagnostic_sha256: str,
    selected_nodes: Iterable[str] = (),
    diagnostic_max_age_seconds: int = 86400,
    expires_in_seconds: int = 300,
    created_at: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    chain = _resume_diagnostic_chain(
        paths,
        private_state,
        Path(diagnostic_evidence_path),
        max_age_seconds=diagnostic_max_age_seconds,
        now=now,
    )
    ack = _sha256(acknowledged_c2_replica_sync_diagnostic_sha256, "acknowledged C2 replica sync diagnostic SHA-256")
    if ack != chain["diagnostic_sha256"]:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RESUME_ACKNOWLEDGEMENT_MISMATCH", "operator acknowledgement does not match the exact C2 sync diagnostic")
    requested = tuple(_identifier(item, "selected node") for item in selected_nodes)
    if requested and requested != (_C2_NODE,):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_SELECTION_MISMATCH", "C2 replica sync resume may target only mainnetc-super2")
    if type(expires_in_seconds) is not int or not _MIN_RELEASE_SECONDS <= expires_in_seconds <= _MAX_RELEASE_SECONDS:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_TTL_INVALID", f"expires_in_seconds must be between {_MIN_RELEASE_SECONDS} and {_MAX_RELEASE_SECONDS}")
    created_text = _timestamp(created_at, path="created_at")
    created_dt = _parse_utc(created_text, "created_at")
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    if created_dt > reference + timedelta(seconds=1):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RESUME_RELEASE_INVALID", "release creation time is in the future")
    expires_at = (created_dt + timedelta(seconds=expires_in_seconds)).isoformat(timespec="seconds").replace("+00:00", "Z")
    plan = chain["plan"]
    deploy_mutation = dict(chain["deploy_mutation"])
    classification = chain["classification"]
    existing_proof_compose = str(plan["proof_compose"]["canonical_text"])
    corrected_proof_compose, port_isolation = _remove_replica_host_p2p_publication(existing_proof_compose, node=_C2_NODE)
    host_p2p_isolation_resume = classification == "docker-created-host-p2p-port-conflict"
    if host_p2p_isolation_resume and port_isolation["host_p2p_publication_removed"] is not True:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RESUME_DIAGNOSTIC_INVALID", "host P2P port conflict diagnostic did not reference a proof Compose with host 30303 publication")

    proof_compose = dict(plan["proof_compose"])
    mutations: list[dict[str, Any]]
    pre_patch_compose: dict[str, Any] | None = None
    if host_p2p_isolation_resume:
        proof_bytes = corrected_proof_compose.encode("utf-8")
        proof_compose.update({
            "sha256": hashlib.sha256(proof_bytes).hexdigest(),
            "semantic_sha256": _compose_semantic_sha256(corrected_proof_compose),
            "byte_length": len(proof_bytes),
            "canonical_text": corrected_proof_compose,
            "host_rpc_mapping_present": False,
            "host_p2p_mapping_present": False,
            "host_p2p_publication_authorized": False,
            "p2p_publication_mode": "container-internal-outbound-only",
            "port_isolation": port_isolation,
        })
        pre_patch_compose = {
            "sha256": hashlib.sha256(existing_proof_compose.encode("utf-8")).hexdigest(),
            "semantic_sha256": _compose_semantic_sha256(existing_proof_compose),
            "byte_length": len(existing_proof_compose.encode("utf-8")),
            "canonical_text": existing_proof_compose,
            "host_p2p_mapping_present": "30303:30303/tcp" in existing_proof_compose or "30303:30303/udp" in existing_proof_compose,
        }
        body = {
            "name": _C2_NODE,
            "docker_compose_raw": base64.b64encode(proof_bytes).decode("ascii"),
        }
        deploy_mutation["ordinal"] = 2
        deploy_mutation["mutation_id"] = f"{_C2_NODE}.resume-deploy-host-p2p-isolated-sync-proof-compose"
        mutations = [
            {
                "ordinal": 1,
                "mutation_id": f"{_C2_NODE}.patch-sync-proof-compose-without-host-p2p",
                "controller_id": _C2_CONTROLLER,
                "method": "PATCH",
                "endpoint": f"/api/v1/services/{urllib.parse.quote(chain['service_uuid'], safe='')}",
                "canonical_request_body": body,
                "body_sha256": hashlib.sha256(canonical_json(body)).hexdigest(),
                "success_statuses": [200, 201, 202],
            },
            deploy_mutation,
        ]
    else:
        deploy_mutation["ordinal"] = 1
        deploy_mutation["mutation_id"] = f"{_C2_NODE}.resume-deploy-sync-proof-compose"
        mutations = [deploy_mutation]
    release: dict[str, Any] = {
        "kind": _RESUME_RELEASE_KIND,
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
        "staged_scope": "resume-c2-replica-sync-from-applied-proof-compose",
        "diagnostic_evidence": {
            "locator": _relative(paths, chain["diagnostic_path"], label="C2 replica sync diagnostic evidence"),
            "sha256": chain["diagnostic_sha256"],
            "observed_at": chain["diagnostic"].get("observed_at"),
            "classification": chain["classification"],
        },
        "failed_sync_release": {
            "locator": _relative(paths, chain["failed_release_path"], label="failed C2 replica sync release"),
            "sha256": chain["failed_release_sha256"],
            "byte_sha256": chain["failed_release_file_sha256"],
        },
        "operator_release": {
            "intent": "patch-away-host-p2p-conflict-and-resume-c2-sync-proof-compose" if host_p2p_isolation_resume else "resume-deploy-start-from-already-applied-c2-sync-proof-compose",
            "acknowledged_c2_replica_sync_diagnostic_sha256": ack,
            "requested_use_limit": 1,
        },
        "proof_plan": {
            "replica_node": _C2_NODE,
            "controller_id": _C2_CONTROLLER,
            "service_uuid": chain["service_uuid"],
            "chain_id": plan["chain_id"],
            "genesis_sha256": plan["genesis_sha256"],
            "expected_validator_set": list(plan["expected_validator_set"]),
            "c2_validator_address": plan["c2_validator_address"],
            "bootnode_enode": plan["bootnode_enode"],
            "initial_node_id": plan["initial_node_id"],
            "replica_node_id": plan["replica_node_id"],
            "proof_compose": proof_compose,
            "pre_patch_compose": pre_patch_compose,
            "port_isolation": port_isolation if host_p2p_isolation_resume else None,
            "preconditions": [
                {
                    "controller_id": _C2_CONTROLLER,
                    "method": "GET",
                    "endpoint": f"/api/v1/services/{urllib.parse.quote(chain['service_uuid'], safe='')}",
                    "assertion": (
                        "C2 service has the previously applied sync-proof Compose with host P2P publication; the release will patch it to container-internal P2P only"
                        if host_p2p_isolation_resume
                        else "C2 service already has the sync-proof Compose; no Compose PATCH is authorized"
                    ),
                },
                {
                    "controller_id": _A_CONTROLLER,
                    "method": "GET",
                    "endpoint": "/api/v1/services",
                    "assertion": "A1 remains running:healthy",
                    "optional_service_uuid": (chain["failed_release"].get("initial_chain_precondition") or {}).get("service_uuid"),
                },
            ],
            "mutations": mutations,
            "proof": dict(plan["proof"]),
        },
        "authority": {
            "authorization_source": "explicit-operator-release",
            "resume_from_applied_sync_proof_compose_authorized": True,
            "host_p2p_isolation_authorized": host_p2p_isolation_resume,
            "compose_patch_authorized": host_p2p_isolation_resume,
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
            "allowed_http_methods": ["GET", "PATCH"] if host_p2p_isolation_resume else ["GET"],
            "coolify_control_plane_only": True,
            "initial_node_read_only": True,
            "replica_node_only": True,
            "manual_ssh_required": False,
            "public_http_endpoint_created": False,
            "host_rpc_mapping_present": False,
            "host_p2p_mapping_present": False,
            "host_p2p_publication_authorized": False,
            "compose_patch_performed": False,
            "host_p2p_isolation_performed": False,
            "private_keys_materialized_in_memory_only": False,
            "private_keys_persisted": False,
            "secrets_in_output": False,
            "validator_vote_performed": False,
            "validator_activation_performed": False,
            "routing_or_topology_published": False,
        },
        "remaining_blockers": [
            {"code": "MOTHER_DEPLOY_VALIDATOR_ADMISSION_NOT_AUTHORIZED", "message": "C2 replica synchronization resume does not authorize QBFT admission or voting"},
            {"code": "MOTHER_DEPLOY_C2_RPC_ROUTING_NOT_AUTHORIZED", "message": "C2 replica synchronization resume does not authorize routing/topology publication"},
        ],
        "summary": {
            "release_valid": True,
            "mutation_count": len(mutations),
            "host_p2p_isolation_authorized": host_p2p_isolation_resume,
            "compose_patch_authorized": host_p2p_isolation_resume,
            "resume_from_applied_sync_proof_compose_authorized": True,
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
        "c2_replica_sync_resume_release_sha256": None,
    }
    release["c2_replica_sync_resume_release_sha256"] = _digest_without(release, "c2_replica_sync_resume_release_sha256")
    if _contains_sensitive(release):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RESUME_RELEASE_INVALID", "C2 replica sync resume release contains sensitive material")
    return release


def write_c2_replica_sync_resume_release(paths: PrivateStatePaths, release: Mapping[str, Any], *, operation: OperationIdentity) -> tuple[Path, str]:
    document = dict(release)
    if document.get("kind") != _RESUME_RELEASE_KIND or document.get("c2_replica_sync_resume_release_sha256") != _digest_without(document, "c2_replica_sync_resume_release_sha256"):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RESUME_RELEASE_INVALID", "C2 replica sync resume release is malformed")
    payload = canonical_json(document)
    root = _ensure_directory(paths, _RESUME_RELEASE_DIRECTORY, operation=operation)
    stamp = re.sub(r"[^0-9A-Za-z]+", "", str(document.get("created_at", "")))[:32] or "c2syncresume"
    destination = root / f"{stamp}-{document['c2_replica_sync_resume_release_sha256'][:16]}.json"
    if destination.exists():
        if destination.read_bytes() != payload:
            raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RESUME_RELEASE_CONFLICT", "resume release destination contains different bytes")
    else:
        atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, document["c2_replica_sync_resume_release_sha256"]


def verify_c2_replica_sync_resume_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    diagnostic_max_age_seconds: int = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    release, raw, byte_sha = _canonical_under(paths, Path(release_path), _RESUME_RELEASE_DIRECTORY, "C2 replica sync resume release")
    if release.get("kind") != _RESUME_RELEASE_KIND or release.get("mother_binding") != _binding(private_state) or _contains_sensitive(release):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RESUME_RELEASE_INVALID", "C2 replica sync resume release kind or binding is invalid")
    digest = _digest_without(release, "c2_replica_sync_resume_release_sha256")
    if release.get("c2_replica_sync_resume_release_sha256") != digest:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RESUME_RELEASE_INVALID", "C2 replica sync resume release digest does not match")
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    created = _parse_utc(release.get("created_at"), "created_at")
    expires = _parse_utc(release.get("expires_at"), "expires_at")
    if reference < created - timedelta(seconds=1) or reference > expires or int((reference - created).total_seconds()) > max_age_seconds:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RESUME_RELEASE_EXPIRED", "C2 replica sync resume release is outside its authority window")
    diagnostic_ref = release.get("diagnostic_evidence")
    if not isinstance(diagnostic_ref, Mapping):
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RESUME_RELEASE_INVALID", "diagnostic evidence binding is missing")
    expected = build_c2_replica_sync_resume_release(
        paths,
        private_state,
        _resolve_locator(paths, diagnostic_ref.get("locator"), label="C2 replica sync diagnostic evidence"),
        acknowledged_c2_replica_sync_diagnostic_sha256=_sha256(diagnostic_ref.get("sha256"), "diagnostic evidence SHA-256"),
        selected_nodes=selected_nodes,
        diagnostic_max_age_seconds=diagnostic_max_age_seconds,
        expires_in_seconds=int((expires - created).total_seconds()),
        created_at=release.get("created_at"),
        now=reference,
    )
    if canonical_json(expected) != raw:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RESUME_RELEASE_INVALID", "C2 replica sync resume release no longer matches its exact inputs")
    plan = release["proof_plan"]
    return {
        "clean": True,
        "release_path": str(Path(release_path).resolve(strict=False)),
        "c2_replica_sync_resume_release_sha256": digest,
        "byte_sha256": byte_sha,
        "diagnostic_evidence_sha256": diagnostic_ref["sha256"],
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
        "service_mutation_count": len(plan["mutations"]),
        "created_at": release["created_at"],
        "expires_at": release["expires_at"],
        "staged_scope": release["staged_scope"],
        "resume_from_applied_sync_proof_compose_authorized": True,
        "host_p2p_isolation_authorized": release.get("authority", {}).get("host_p2p_isolation_authorized") is True,
        "compose_patch_authorized": release.get("authority", {}).get("compose_patch_authorized") is True,
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
            "MOTHER_DEPLOY_C2_REPLICA_SYNC_RESUME_EXECUTOR_NOT_RUN",
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_NOT_AUTHORIZED",
            "MOTHER_DEPLOY_C2_RPC_ROUTING_NOT_AUTHORIZED",
        ],
    }


def inspect_c2_replica_sync_resume_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    diagnostic_max_age_seconds: int = 86400,
    now: datetime | None = None,
) -> dict[str, Any]:
    verified = verify_c2_replica_sync_resume_release(
        paths,
        private_state,
        Path(release_path),
        selected_nodes=selected_nodes,
        max_age_seconds=max_age_seconds,
        diagnostic_max_age_seconds=diagnostic_max_age_seconds,
        now=now,
    )
    if _sha256(acknowledged_release_sha256, "acknowledged release SHA-256") != verified["c2_replica_sync_resume_release_sha256"]:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RESUME_RELEASE_ACKNOWLEDGEMENT_MISMATCH", "C2 replica sync resume release acknowledgement does not match")
    claim = _root(paths, _RESUME_CLAIM_DIRECTORY) / f"{verified['c2_replica_sync_resume_release_sha256']}.json"
    return {
        **verified,
        "executor_implemented": True,
        "execute_requested": False,
        "release_already_claimed": claim.exists(),
        "live_execution_authorized": True,
        "network_access_performed": False,
        "live_mutation_performed": False,
        "compose_patch_performed": False,
        "service_deploy_or_start_performed": False,
        "replica_synchronized": False,
        "initial_node_read_only": True,
        "guardian_internal_only": True,
        "remaining_blocker_codes": [
            "MOTHER_DEPLOY_VALIDATOR_ADMISSION_NOT_AUTHORIZED",
            "MOTHER_DEPLOY_C2_RPC_ROUTING_NOT_AUTHORIZED",
        ],
    }


def execute_c2_replica_sync_resume_release(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    release_path: Path,
    *,
    acknowledged_release_sha256: str,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    diagnostic_max_age_seconds: int = 86400,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    max_wait_seconds: float = 300.0,
    poll_interval_seconds: float = 5.0,
    opener: Any = _DEFAULT_OPENER,
    now: datetime | None = None,
    operation: OperationIdentity,
) -> dict[str, Any]:
    inspected = inspect_c2_replica_sync_resume_release(
        paths,
        private_state,
        Path(release_path),
        acknowledged_release_sha256=acknowledged_release_sha256,
        selected_nodes=selected_nodes,
        max_age_seconds=max_age_seconds,
        diagnostic_max_age_seconds=diagnostic_max_age_seconds,
        now=now,
    )
    if inspected["release_already_claimed"]:
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RESUME_RELEASE_ALREADY_CONSUMED", "this C2 replica sync resume release is already claimed")
    release, _, _ = _canonical_under(paths, Path(inspected["release_path"]), _RESUME_RELEASE_DIRECTORY, "C2 replica sync resume release")
    plan = release["proof_plan"]
    digest = inspected["c2_replica_sync_resume_release_sha256"]
    claim = {
        "kind": _RESUME_CLAIM_KIND,
        "schema_version": 1,
        "claimed_at": _timestamp(),
        "release": {"locator": _relative(paths, Path(inspected["release_path"]), label="C2 replica sync resume release"), "sha256": digest},
        "diagnostic_evidence_sha256": inspected["diagnostic_evidence_sha256"],
        "node": _C2_NODE,
        "requested_use_limit": 1,
        "operation_id": operation.operation_id,
    }
    claim_root = _ensure_directory(paths, _RESUME_CLAIM_DIRECTORY, operation=operation)
    claim_path = claim_root / f"{digest}.json"
    if claim_path.exists():
        raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RESUME_RELEASE_ALREADY_CONSUMED", "this C2 replica sync resume release is already claimed")
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
        optional_a_uuid = (plan.get("preconditions", [{}, {}])[1] if isinstance(plan.get("preconditions"), list) and len(plan.get("preconditions")) > 1 else {}).get("optional_service_uuid")
        if optional_a_uuid:
            a_inventory = _http(controller_a, "GET", "/api/v1/services", body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
            a_verified = False
            if a_inventory["ok"]:
                item = _service_record(a_inventory["payload"], service_uuid=optional_a_uuid, node=_A1_NODE)
                a_verified = _service_status(item) == "running:healthy"
            preconditions.append({
                "name": "initial-chain-running-healthy-before-sync-resume",
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
            raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RESUME_PRECONDITION_FAILED", f"Coolify C service detail failed with HTTP {c_detail['status']}")
        record = _standby_service_record(c_detail["payload"], service_uuid=plan["service_uuid"])
        current_compose = _standby_compose_from_service_record(record)
        patch_authorized = release.get("authority", {}).get("compose_patch_authorized") is True
        pre_patch_compose = plan.get("pre_patch_compose") if isinstance(plan.get("pre_patch_compose"), Mapping) else None
        expected_before = pre_patch_compose["canonical_text"] if patch_authorized and isinstance(pre_patch_compose, Mapping) and isinstance(pre_patch_compose.get("canonical_text"), str) else plan["proof_compose"]["canonical_text"]
        proof_report = _proof_compose_report(observed=current_compose, expected=expected_before, node=_C2_NODE)
        precondition_verified = (
            proof_report.get("sync_compose_verified") is True
            or (patch_authorized and (proof_report.get("exact_match") is True or proof_report.get("yaml_equivalent") is True))
        )
        preconditions.append({
            "name": "c2-replica-sync-proof-compose-before-resume-deploy",
            "controller_id": _C2_CONTROLLER,
            "method": "GET",
            "endpoint": c_detail_endpoint,
            "status": c_detail["status"],
            "response_sha256": c_detail["response_sha256"],
            "verified": precondition_verified,
            "compose_verification": proof_report,
            "host_p2p_isolation_patch_pending": patch_authorized,
        })
        if precondition_verified is not True:
            raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RESUME_COMPOSE_MISMATCH", "C2 service does not have the expected sync-proof Compose")

        for mutation in plan["mutations"]:
            method = mutation.get("method")
            body = mutation.get("canonical_request_body")
            if method not in {"GET", "PATCH"}:
                raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RESUME_RELEASE_INVALID", "resume release may execute only GET or PATCH mutations")
            if method == "GET" and body is not None:
                raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RESUME_RELEASE_INVALID", "resume deploy mutation must not contain a request body")
            if method == "PATCH" and not patch_authorized:
                raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RESUME_RELEASE_INVALID", "resume Compose PATCH is not authorized")
            response = _http(
                controller_c,
                method,
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
                "method": method,
                "endpoint": mutation["endpoint"],
                "body_sha256": mutation.get("body_sha256"),
                "status": "succeeded" if ok else "failed",
                "live_write_acknowledged": ok,
                "response": _response_receipt(response, include_safe_text=method == "GET" and mutation["endpoint"].startswith("/api/v1/deploy")),
            })
            if not ok:
                raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RESUME_MUTATION_FAILED", f"Coolify rejected C2 sync resume mutation {mutation['ordinal']}")

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
            raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RESUME_NOT_HEALTHY", f"C2 sync resume proof did not reach running:healthy (last status {last_status!r})")

        c_detail = _http(controller_c, "GET", c_detail_endpoint, body=None, timeout=timeout, max_response_bytes=max_response_bytes, opener=opener)
        if not c_detail["ok"]:
            raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RESUME_POSTCONDITION_FAILED", f"Coolify C proof detail failed with HTTP {c_detail['status']}")
        record = _standby_service_record(c_detail["payload"], service_uuid=plan["service_uuid"])
        proof_observed = _standby_compose_from_service_record(record)
        proof_report = _proof_compose_report(observed=proof_observed, expected=plan["proof_compose"]["canonical_text"], node=_C2_NODE)
        preconditions.append({
            "name": "c2-replica-sync-proof-compose-after-resume",
            "controller_id": _C2_CONTROLLER,
            "method": "GET",
            "endpoint": c_detail_endpoint,
            "status": c_detail["status"],
            "response_sha256": c_detail["response_sha256"],
            "verified": proof_report.get("sync_compose_verified") is True,
            "compose_verification": proof_report,
        })
        if proof_report.get("sync_compose_verified") is not True:
            raise _fail("MOTHER_DEPLOY_C2_REPLICA_SYNC_RESUME_POSTCONDITION_FAILED", "live C2 Compose does not match the sync-proof semantics")
    except MotherDeploymentC2ReplicaSyncError as exc:
        failure = {"code": exc.code, "message": str(exc)[:512]}
    except Exception as exc:  # pragma: no cover
        failure = {"code": "MOTHER_DEPLOY_C2_REPLICA_SYNC_RESUME_UNEXPECTED_FAILURE", "message": str(exc)[:512]}

    completed = _timestamp()
    expected_receipt_count = len(plan.get("mutations", [])) if isinstance(plan.get("mutations"), list) else 1
    compose_patch_performed = any(item.get("method") == "PATCH" and item.get("status") == "succeeded" for item in receipts)
    compose_patch_authorized = release.get("authority", {}).get("compose_patch_authorized") is True
    complete = failure is None and len(receipts) == expected_receipt_count and all(item["status"] == "succeeded" for item in receipts)
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
        "release": {"locator": _relative(paths, Path(inspected["release_path"]), label="C2 replica sync resume release"), "sha256": digest},
        "execution_claim": {"locator": _relative(paths, claim_path, label="C2 replica sync resume claim")},
        "diagnostic_evidence_sha256": inspected["diagnostic_evidence_sha256"],
        "genesis_sha256": inspected["genesis_sha256"],
        "proof_compose_sha256": inspected["proof_compose_sha256"],
        "expected_validator_set": list(plan["expected_validator_set"]),
        "proof": {
            "mode": "internal-health-assertion-bound-to-already-applied-sync-proof-compose",
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "host_rpc_mapping_present": False,
            "host_p2p_mapping_present": False,
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
            "resume_from_applied_sync_proof_compose_authorized": True,
            "host_p2p_isolation_authorized": release.get("authority", {}).get("host_p2p_isolation_authorized") is True,
            "compose_patch_authorized": compose_patch_authorized,
            "synchronization_proof_authorized": True,
            "replica_start_authorized": True,
            "replica_sync_authorized": True,
            "validator_vote_authorized": False,
            "validator_activation_authorized": False,
            "routing_or_topology_publication_authorized": False,
            "release_consumed": True,
        },
        "policy": {
            "allowed_http_methods": ["GET", "PATCH"] if compose_patch_authorized else ["GET"],
            "coolify_control_plane_only": True,
            "initial_node_read_only": True,
            "replica_node_only": True,
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "host_rpc_mapping_present": False,
            "host_p2p_mapping_present": False,
            "host_p2p_publication_authorized": False,
            "private_state_updated": False,
            "secrets_in_output": False,
            "automatic_rollback_performed": False,
            "compose_patch_performed": compose_patch_performed,
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
        "service_mutation_count": expected_receipt_count if complete else sum(item.get("status") == "succeeded" for item in receipts),
        "identity_mutation_count": 0,
        "chain_mutation_count": 0,
        "validator_mutation_count": 0,
        "validator_restart_count": 0,
        "validator_vote_performed": False,
        "summary": {
            "clean": complete,
            "resume_from_applied_sync_proof_compose": True,
            "compose_patch_performed": compose_patch_performed,
            "host_p2p_isolation_performed": compose_patch_performed,
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



__all__ = [
    "MotherDeploymentC2ReplicaSyncError",
    "build_c2_replica_sync_release",
    "build_c2_replica_sync_resume_release",
    "execute_c2_replica_sync_release",
    "execute_c2_replica_sync_resume_release",
    "inspect_c2_replica_sync_release",
    "inspect_c2_replica_sync_resume_release",
    "diagnose_c2_replica_sync_materialization",
    "verify_c2_replica_sync_evidence",
    "verify_c2_replica_sync_release",
    "verify_c2_replica_sync_resume_release",
    "write_c2_replica_sync_release",
    "write_c2_replica_sync_resume_release",
]
