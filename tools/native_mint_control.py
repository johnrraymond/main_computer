#!/usr/bin/env python3
"""Mother-owned QBFT native-mint control.

This tool does not introduce a permanent mint contract.  It uses Besu QBFT's
native ``blockreward`` + ``miningbeneficiary`` transition mechanism.  Every
accepted chain node receives the same future transition schedule in its
``genesis.json``.  The network is quiesced while the updated config is staged,
then restarted before the activation block.

Normal operators should use ``mother_mutate_harness.py native-mint*`` rather
than invoking this module directly.
"""

from __future__ import annotations

import argparse
import base64
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.mother.common.canonical import canonical_json
from tools.mother.common.coolify_state import resolve_coolify_controller
from tools.mother.common.deployment_coolify_context import load_controller_config
from tools.mother.common.deployment_genesis import MotherDeploymentGenesisError, _genesis_policy
from tools.mother.common.errors import MotherError
from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import PrivateStateReadResult, read_private_state


SCHEMA = "main-computer.native-mint-operation.v1"
TOPOLOGY_EVIDENCE_KIND = "main_computer.mother.native_mint_topology_evidence.v1"
EVIDENCE_KIND = "main_computer.mother.native_mint_evidence.v1"
STATE_DIRECTORY = "native-mint"
TOPOLOGY_EVIDENCE_DIRECTORY = "native-mint-topology"
OPERATION_EVIDENCE_DIRECTORY = "native-mint"
HELPER_IMAGE = "python:3.12-alpine"
DEFAULT_ACTIVATION_LEAD_BLOCKS = 180
DEFAULT_CLOSE_LEAD_BLOCKS = 30
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
DEFAULT_HELPER_WAIT_SECONDS = 120.0
DEFAULT_RPC_WAIT_SECONDS = 300.0
DEFAULT_POLL_SECONDS = 2.0
DEFAULT_HELPER_PROGRESS_SECONDS = 10.0
DEFAULT_HELPER_EXIT_LOG_GRACE_SECONDS = 10.0
DEFAULT_HELPER_PROOF_HOLD_SECONDS = 60.0
DEFAULT_HELPER_LOG_TIMEOUT_SECONDS = 5.0
HELPER_NAME_PREFIX = "mother-native-mint-"
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
UUID_RE = re.compile(r"^[A-Za-z0-9._-]+$")

_BASELINE_DIRECTORIES = (
    "deployment-node-add-post-admission-observe",
    "deployment-node-remove-finalize",
    "deployment-node-add-single-node-chain-and-hub-proof",
    "deployment-live-current-topology",
    "deployment-live-topology-empty-rectification",
    TOPOLOGY_EVIDENCE_DIRECTORY,
)


class NativeMintError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _fail(code: str, message: str) -> NativeMintError:
    return NativeMintError(code, message)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or not UUID_RE.fullmatch(value.strip()):
        raise _fail("NATIVE_MINT_INVALID", f"{field} is not a safe identifier")
    return value.strip()


def _address(value: object, field: str) -> str:
    if not isinstance(value, str) or not ADDRESS_RE.fullmatch(value.strip()):
        raise _fail("NATIVE_MINT_INVALID", f"{field} is not an Ethereum address")
    return value.strip()


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise _fail("NATIVE_MINT_INVALID", f"{field} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise _fail("NATIVE_MINT_INVALID", f"{field} must be a positive integer") from exc
    if parsed <= 0:
        raise _fail("NATIVE_MINT_INVALID", f"{field} must be a positive integer")
    return parsed


def _nonnegative_int(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise _fail("NATIVE_MINT_INVALID", f"{field} must be a non-negative integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise _fail("NATIVE_MINT_INVALID", f"{field} must be a non-negative integer") from exc
    if parsed < 0:
        raise _fail("NATIVE_MINT_INVALID", f"{field} must be a non-negative integer")
    return parsed


def _amount_wei(args: argparse.Namespace, *, per_block: bool = False) -> int:
    wei = getattr(args, "wei_per_block", None) if per_block else getattr(args, "amount_wei", None)
    native = getattr(args, "native_per_block", None) if per_block else getattr(args, "amount_native", None)
    if wei is not None and native is not None:
        raise _fail("NATIVE_MINT_INVALID", "specify either a wei amount or a native-unit amount, not both")
    if wei is not None:
        return _positive_int(wei, "wei amount")
    if native is None:
        raise _fail("NATIVE_MINT_INVALID", "native mint amount is required")
    try:
        dec = Decimal(str(native))
    except InvalidOperation as exc:
        raise _fail("NATIVE_MINT_INVALID", "native-unit amount is invalid") from exc
    if dec <= 0:
        raise _fail("NATIVE_MINT_INVALID", "native-unit amount must be positive")
    scaled = dec * Decimal(10**18)
    if scaled != scaled.to_integral_value():
        raise _fail("NATIVE_MINT_INVALID", "native-unit amount has more than 18 decimal places")
    return int(scaled)


def _operation_identity(command: str, network: str, operation_id: str | None = None) -> OperationIdentity:
    op_id = operation_id or f"native-mint-{command}-{_stamp()}-{os.getpid()}-{time.time_ns() % 1_000_000_000:09d}"
    return OperationIdentity(
        operation_id=op_id,
        request_id=f"native-mint-control-{command}",
        network=network,
        operation_kind="MOTHER-OP-NATIVE-MINT",
    )


def _paths(runtime_state_root: Path):
    return MotherPaths(runtime_state_root=runtime_state_root).resolve_private_state_paths()


def _load_private(runtime_state_root: Path, operation: OperationIdentity) -> tuple[Any, PrivateStateReadResult, dict[str, Any]]:
    paths = _paths(runtime_state_root)
    private = read_private_state(paths, operation=operation)
    try:
        document = json.loads(private.canonical_object_bytes.decode("utf-8"))
    except Exception as exc:
        raise _fail("NATIVE_MINT_PRIVATE_STATE_INVALID", "Mother private state is not canonical JSON") from exc
    if not isinstance(document, dict):
        raise _fail("NATIVE_MINT_PRIVATE_STATE_INVALID", "Mother private state is not an object")
    return paths, private, document


def _network_state(document: Mapping[str, Any], network: str) -> Mapping[str, Any]:
    try:
        body = document["networks"][network]
    except (KeyError, TypeError) as exc:
        raise _fail("NATIVE_MINT_NETWORK_MISSING", f"network {network!r} is missing from Mother private state") from exc
    if not isinstance(body, Mapping):
        raise _fail("NATIVE_MINT_NETWORK_MISSING", f"network {network!r} is malformed")
    return body


def _rpc_url(network_state: Mapping[str, Any]) -> str:
    route = network_state.get("rpc_route")
    if isinstance(route, Mapping):
        for key in ("url", "rpc_url"):
            value = route.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value.rstrip("/")
        for key in ("host", "hostname", "public_host"):
            value = route.get(key)
            if isinstance(value, str) and re.fullmatch(r"[a-z0-9.-]+", value):
                return f"https://{value}"
    allfather = network_state.get("allfather")
    domain = allfather.get("public_domain") if isinstance(allfather, Mapping) else None
    if not isinstance(domain, str) or not re.fullmatch(r"[a-z0-9.-]+", domain):
        domain = "greatlibrary.io"
    return f"https://mainnet-rpc.{domain}"


def _rpc(rpc_url: str, method: str, params: list[Any], *, timeout: float = DEFAULT_TIMEOUT) -> Any:
    raw = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        rpc_url,
        data=raw,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "main-computer-native-mint/1.0 (+https://greatlibrary.io)",
            "X-Main-Computer-Client": "native-mint-control",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(DEFAULT_MAX_RESPONSE_BYTES + 1)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        raise _fail("NATIVE_MINT_RPC_FAILED", f"{method} failed against {rpc_url}: {type(exc).__name__}") from exc
    if len(body) > DEFAULT_MAX_RESPONSE_BYTES:
        raise _fail("NATIVE_MINT_RPC_FAILED", f"{method} response exceeded the size limit")
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise _fail("NATIVE_MINT_RPC_FAILED", f"{method} returned invalid JSON") from exc
    if not isinstance(payload, Mapping) or payload.get("error") is not None or "result" not in payload:
        raise _fail("NATIVE_MINT_RPC_FAILED", f"{method} returned a JSON-RPC error")
    return payload["result"]


def _rpc_int(rpc_url: str, method: str, params: list[Any] | None = None) -> int:
    value = _rpc(rpc_url, method, params or [])
    if not isinstance(value, str) or not value.startswith("0x"):
        raise _fail("NATIVE_MINT_RPC_FAILED", f"{method} returned an invalid quantity")
    return int(value, 16)


def _latest_block(rpc_url: str) -> int:
    return _rpc_int(rpc_url, "eth_blockNumber")


def _balance(rpc_url: str, address: str, block: int | str = "latest") -> int:
    block_ref = hex(block) if isinstance(block, int) else block
    return _rpc_int(rpc_url, "eth_getBalance", [address, block_ref])


def _validators(rpc_url: str) -> list[str]:
    value = _rpc(rpc_url, "qbft_getValidatorsByBlockNumber", ["latest"])
    if not isinstance(value, list) or not value:
        raise _fail("NATIVE_MINT_VALIDATOR_SET_INVALID", "QBFT validator set is empty or malformed")
    result = [_address(item, "validator address") for item in value]
    if len({item.lower() for item in result}) != len(result):
        raise _fail("NATIVE_MINT_VALIDATOR_SET_INVALID", "QBFT validator set contains duplicates")
    return result


def _timestamp_from_document(path: Path, document: Mapping[str, Any]) -> float:
    for key in ("completed_at", "created_at"):
        value = document.get(key)
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
    match = re.search(r"\d{8}T\d{6}Z", path.name)
    if match:
        try:
            return datetime.strptime(match.group(0), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            pass
    return path.stat().st_mtime


def _latest_baseline(runtime_state_root: Path) -> tuple[Path, dict[str, Any]]:
    root = runtime_state_root / "mother" / "evidence"
    candidates: list[tuple[float, int, str, Path, dict[str, Any]]] = []
    for directory in _BASELINE_DIRECTORIES:
        folder = root / directory
        if not folder.is_dir():
            continue
        for path in folder.glob("*.json"):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(doc, dict):
                continue
            topology = _extract_topology(doc, required=False)
            if topology is None:
                continue
            candidates.append((_timestamp_from_document(path, doc), path.stat().st_mtime_ns, path.name, path, doc))
    if not candidates:
        raise _fail("NATIVE_MINT_TOPOLOGY_MISSING", "no accepted Mother topology evidence is available")
    candidates.sort(reverse=True)
    return candidates[0][3], candidates[0][4]


def _extract_topology(document: Mapping[str, Any], *, required: bool = True) -> dict[str, Any] | None:
    for key in ("final_topology", "current_topology", "topology", "post_add_topology", "post_removal_topology"):
        value = document.get(key)
        if isinstance(value, Mapping) and isinstance(value.get("nodes"), list) and isinstance(value.get("services"), Mapping):
            return dict(value)
    if required:
        raise _fail("NATIVE_MINT_TOPOLOGY_MISSING", "baseline evidence does not contain a current topology")
    return None


def _chain_identity(document: Mapping[str, Any], topology: Mapping[str, Any]) -> tuple[int, str]:
    chain_id = document.get("chain_id", topology.get("chain_id"))
    genesis_sha = document.get("genesis_sha256", topology.get("genesis_sha256"))
    try:
        chain_id_int = int(chain_id)
    except (TypeError, ValueError) as exc:
        raise _fail("NATIVE_MINT_TOPOLOGY_MISSING", "baseline chain_id is missing") from exc
    if not isinstance(genesis_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", genesis_sha):
        raise _fail("NATIVE_MINT_TOPOLOGY_MISSING", "baseline genesis_sha256 is missing or invalid")
    return chain_id_int, genesis_sha


def _candidate_genesis_dicts(value: Any):
    if isinstance(value, Mapping):
        if isinstance(value.get("config"), Mapping) and isinstance(value.get("alloc"), Mapping) and "extraData" in value:
            yield dict(value)
        for child in value.values():
            if isinstance(child, (Mapping, list)):
                yield from _candidate_genesis_dicts(child)
    elif isinstance(value, list):
        for item in value:
            yield from _candidate_genesis_dicts(item)


def _reconstruct_genesis_from_private_state(
    private_doc: Mapping[str, Any],
    *,
    network: str,
    expected_sha: str,
) -> dict[str, Any] | None:
    """Rebuild the original Mother genesis policy and accept only an exact hash match.

    Historical topology evidence intentionally carries the genesis commitment, not
    necessarily the full genesis object.  Native-mint needs the full object because
    it appends future QBFT transitions.  The initial Mother genesis is deterministic
    from current private state, so try every reserved validator as the possible
    initial validator and accept a candidate only when its canonical bytes reproduce
    the already-accepted genesis SHA-256 exactly.

    This is deliberately fail-closed: policy drift, wallet rotation, or ambiguous
    state produces no candidate rather than silently manufacturing a new genesis.
    """
    try:
        network_state = _network_state(private_doc, network)
    except NativeMintError:
        return None
    validators = network_state.get("validators")
    if not isinstance(validators, Mapping):
        return None

    matches: dict[bytes, dict[str, Any]] = {}
    seen_addresses: set[str] = set()
    for raw in validators.values():
        if not isinstance(raw, Mapping):
            continue
        address = raw.get("address")
        if not isinstance(address, str) or not ADDRESS_RE.fullmatch(address.strip()):
            continue
        normalized = address.strip().lower()
        if normalized in seen_addresses:
            continue
        seen_addresses.add(normalized)
        try:
            candidate, _alloc_addresses = _genesis_policy(
                private_doc,
                network=network,
                initial_validator_address=address,
            )
        except MotherDeploymentGenesisError:
            continue
        raw_candidate = canonical_json(candidate)
        if _sha_bytes(raw_candidate) == expected_sha:
            matches[raw_candidate] = candidate

    if len(matches) == 1:
        return next(iter(matches.values()))
    return None


def _find_genesis(
    runtime_state_root: Path,
    expected_sha: str,
    baseline_doc: Mapping[str, Any],
    *,
    private_doc: Mapping[str, Any] | None = None,
    network: str | None = None,
) -> dict[str, Any]:
    for candidate in _candidate_genesis_dicts(baseline_doc):
        if _sha_bytes(canonical_json(candidate)) == expected_sha:
            return candidate
    mother_root = runtime_state_root / "mother"
    for branch in ("actions", "evidence"):
        root = mother_root / branch
        if not root.exists():
            continue
        for path in root.rglob("*.json"):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            for candidate in _candidate_genesis_dicts(doc):
                if _sha_bytes(canonical_json(candidate)) == expected_sha:
                    return candidate

    if private_doc is not None and network is not None:
        reconstructed = _reconstruct_genesis_from_private_state(
            private_doc,
            network=network,
            expected_sha=expected_sha,
        )
        if reconstructed is not None:
            return reconstructed

    raise _fail(
        "NATIVE_MINT_GENESIS_MISSING",
        (
            f"canonical genesis artifact {expected_sha} was not found in Mother state "
            "and could not be reconstructed exactly from current Mother genesis policy"
        ),
    )


def _resolve_recipient(document: Mapping[str, Any], network: str, value: str) -> tuple[str, str]:
    if ADDRESS_RE.fullmatch(value):
        return _address(value, "recipient"), "explicit-address"
    network_state = _network_state(document, network)
    wallets = network_state.get("wallets")
    if isinstance(wallets, Mapping):
        wallet = wallets.get(value)
        if isinstance(wallet, Mapping) and isinstance(wallet.get("address"), str):
            return _address(wallet["address"], f"wallet {value} address"), f"wallet:{value}"
    raise _fail("NATIVE_MINT_RECIPIENT_UNKNOWN", f"{value!r} is neither an address nor a known {network} wallet role")


def _qbft_transition_list(genesis: dict[str, Any]) -> list[dict[str, Any]]:
    config = genesis.get("config")
    if not isinstance(config, dict):
        raise _fail("NATIVE_MINT_GENESIS_INVALID", "genesis config is missing")
    qbft = config.get("qbft")
    if not isinstance(qbft, dict):
        raise _fail("NATIVE_MINT_GENESIS_INVALID", "genesis QBFT config is missing")
    base_reward = qbft.get("blockreward", "0")
    try:
        if int(str(base_reward), 0) != 0:
            raise _fail("NATIVE_MINT_UNMANAGED_REWARD_ACTIVE", "base QBFT blockreward is non-zero; refusing to overlay native-mint control")
    except ValueError as exc:
        raise _fail("NATIVE_MINT_GENESIS_INVALID", "base QBFT blockreward is invalid") from exc
    config_transitions = config.setdefault("transitions", {})
    if not isinstance(config_transitions, dict):
        raise _fail("NATIVE_MINT_GENESIS_INVALID", "genesis transitions is not an object")
    qbft_transitions = config_transitions.setdefault("qbft", [])
    if not isinstance(qbft_transitions, list):
        raise _fail("NATIVE_MINT_GENESIS_INVALID", "genesis transitions.qbft is not a list")
    normalized: list[dict[str, Any]] = []
    seen: set[int] = set()
    for raw in qbft_transitions:
        if not isinstance(raw, Mapping) or type(raw.get("block")) is not int or int(raw["block"]) < 0:
            raise _fail("NATIVE_MINT_GENESIS_INVALID", "QBFT transition contains an invalid block")
        block = int(raw["block"])
        if block in seen:
            raise _fail("NATIVE_MINT_GENESIS_INVALID", f"QBFT transitions contain duplicate block {block}")
        seen.add(block)
        normalized.append(dict(raw))
    normalized.sort(key=lambda item: int(item["block"]))
    config_transitions["qbft"] = normalized
    return normalized


def _upsert_transition(transitions: list[dict[str, Any]], block: int, updates: Mapping[str, Any]) -> None:
    matches = [item for item in transitions if item.get("block") == block]
    if len(matches) > 1:
        raise _fail("NATIVE_MINT_GENESIS_INVALID", f"multiple QBFT transitions already exist at block {block}")
    if matches:
        item = matches[0]
        for key, value in updates.items():
            if key in item and item[key] != value:
                raise _fail("NATIVE_MINT_TRANSITION_CONFLICT", f"QBFT transition block {block} already sets {key}={item[key]!r}")
            item[key] = value
    else:
        transitions.append({"block": block, **dict(updates)})
        transitions.sort(key=lambda item: int(item["block"]))


def _effective_reward_state(genesis: Mapping[str, Any], block: int) -> tuple[int, str]:
    config = genesis.get("config")
    if not isinstance(config, Mapping):
        raise _fail("NATIVE_MINT_GENESIS_INVALID", "genesis config is missing")
    qbft = config.get("qbft")
    if not isinstance(qbft, Mapping):
        raise _fail("NATIVE_MINT_GENESIS_INVALID", "genesis QBFT config is missing")
    reward = int(str(qbft.get("blockreward", "0")), 0)
    beneficiary = str(qbft.get("miningbeneficiary") or "")
    transition_root = config.get("transitions")
    entries = transition_root.get("qbft", []) if isinstance(transition_root, Mapping) else []
    if isinstance(entries, list):
        for item in sorted((x for x in entries if isinstance(x, Mapping) and type(x.get("block")) is int), key=lambda x: int(x["block"])):
            if int(item["block"]) > block:
                break
            if "blockreward" in item:
                reward = int(str(item["blockreward"]), 0)
            if "miningbeneficiary" in item:
                beneficiary = str(item["miningbeneficiary"] or "")
    return reward, beneficiary


def _build_open_genesis(genesis: Mapping[str, Any], *, current_block: int, activation_block: int, expiration_block: int, recipient: str, wei_per_block: int) -> dict[str, Any]:
    if activation_block <= current_block:
        raise _fail("NATIVE_MINT_PREP_STALE", "activation block must be in the future")
    if expiration_block <= activation_block:
        raise _fail("NATIVE_MINT_INVALID", "expiration block must be after activation block")
    result = json.loads(json.dumps(genesis))
    reward, beneficiary = _effective_reward_state(result, current_block)
    if reward != 0:
        raise _fail("NATIVE_MINT_UNMANAGED_REWARD_ACTIVE", f"native block reward is already active at block {current_block}: reward={reward} beneficiary={beneficiary!r}")
    transitions = _qbft_transition_list(result)
    future_reward_transitions = [
        item for item in transitions
        if int(item["block"]) > current_block and ("blockreward" in item or "miningbeneficiary" in item)
    ]
    if future_reward_transitions:
        raise _fail("NATIVE_MINT_FUTURE_WINDOW_EXISTS", "future QBFT reward/beneficiary transitions already exist; close/finish the existing native-mint schedule first")
    _upsert_transition(transitions, activation_block, {"blockreward": str(wei_per_block), "miningbeneficiary": recipient})
    _upsert_transition(transitions, expiration_block, {"blockreward": "0", "miningbeneficiary": ""})
    return result


def _build_close_genesis(genesis: Mapping[str, Any], *, current_block: int, active: Mapping[str, Any], close_block: int) -> tuple[dict[str, Any], int]:
    result = json.loads(json.dumps(genesis))
    transitions = _qbft_transition_list(result)
    activation = int(active["activation_block"])
    expiration = int(active["expiration_block"])
    if current_block >= expiration:
        return result, expiration
    if current_block < activation:
        # Nothing has minted yet.  Cancel the future ON transition in place and
        # keep the already scheduled OFF transition.  This is safe because the
        # altered transition is still in the future on every node.
        match = next((item for item in transitions if int(item["block"]) == activation), None)
        if not isinstance(match, dict):
            raise _fail("NATIVE_MINT_CLOSE_INVALID", "active manifest activation transition is missing")
        match["blockreward"] = "0"
        match["miningbeneficiary"] = ""
        return result, activation
    actual_close = min(close_block, expiration)
    _upsert_transition(transitions, actual_close, {"blockreward": "0", "miningbeneficiary": ""})
    return result, actual_close


def _operation_root(runtime_state_root: Path) -> Path:
    return runtime_state_root / "mother" / STATE_DIRECTORY


def _operations_dir(runtime_state_root: Path) -> Path:
    return _operation_root(runtime_state_root) / "operations"


def _operation_path(runtime_state_root: Path, operation_id: str) -> Path:
    return _operations_dir(runtime_state_root) / f"{operation_id}.json"


def _write_json(path: Path, document: Mapping[str, Any]) -> None:
    raw = canonical_json(dict(document))
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".tmp-{os.getpid()}-{time.time_ns()}")
    with temp.open("wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        doc = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise _fail("NATIVE_MINT_STATE_INVALID", f"could not read {path}") from exc
    if not isinstance(doc, dict) or canonical_json(doc) != raw:
        raise _fail("NATIVE_MINT_STATE_INVALID", f"{path} is not canonical JSON")
    return doc


def _load_operation(runtime_state_root: Path, operation_id: str) -> tuple[Path, dict[str, Any]]:
    path = _operation_path(runtime_state_root, _identifier(operation_id, "operation_id"))
    if not path.is_file():
        raise _fail("NATIVE_MINT_OPERATION_MISSING", f"native-mint operation {operation_id!r} does not exist")
    doc = _read_json(path)
    if doc.get("schema") != SCHEMA:
        raise _fail("NATIVE_MINT_STATE_INVALID", "native-mint operation schema is unsupported")
    return path, doc


def _active_operation(runtime_state_root: Path, network: str) -> dict[str, Any] | None:
    directory = _operations_dir(runtime_state_root)
    if not directory.is_dir():
        return None
    candidates: list[tuple[int, str, dict[str, Any]]] = []
    for path in directory.glob("*.json"):
        try:
            doc = _read_json(path)
        except NativeMintError:
            continue
        if doc.get("schema") != SCHEMA or doc.get("network") != network or doc.get("mode") not in {"mint", "open"}:
            continue
        if doc.get("status") not in {"prepared", "rolling-out", "genesis-staged", "applied", "finalized"}:
            continue
        try:
            expiration = int(doc["window"]["expiration_block"])
        except Exception:
            continue
        candidates.append((expiration, str(doc.get("operation_id") or ""), doc))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][2]


def _topology_targets(private_doc: Mapping[str, Any], network: str, topology: Mapping[str, Any], rpc_validators: list[str]) -> list[dict[str, str]]:
    nodes = topology.get("nodes")
    services = topology.get("services")
    if not isinstance(nodes, list) or not isinstance(services, Mapping):
        raise _fail("NATIVE_MINT_TOPOLOGY_MISSING", "topology nodes/services are missing")
    network_state = _network_state(private_doc, network)
    validators = network_state.get("validators")
    reservations = network_state.get("nodes")
    if not isinstance(validators, Mapping) or not isinstance(reservations, Mapping):
        raise _fail("NATIVE_MINT_PRIVATE_STATE_INVALID", "validator/node reservations are missing")
    live_set = {item.lower() for item in rpc_validators}
    mapped_live: set[str] = set()
    targets: list[dict[str, str]] = []
    for raw_node in nodes:
        node = _identifier(raw_node, "topology node")
        service = services.get(node)
        reservation = reservations.get(node)
        validator = validators.get(node)
        if not isinstance(service, Mapping) or not isinstance(reservation, Mapping) or not isinstance(validator, Mapping):
            raise _fail("NATIVE_MINT_TOPOLOGY_MISSING", f"{node} lacks service/reservation/validator state")
        controller = _identifier(service.get("controller_id") or reservation.get("host"), f"{node} controller")
        service_uuid = _identifier(service.get("service_uuid") or service.get("created_service_uuid"), f"{node} service_uuid")
        address = _address(validator.get("address"), f"{node} validator address")
        if address.lower() in live_set:
            mapped_live.add(address.lower())
        targets.append({"node": node, "controller_id": controller, "service_uuid": service_uuid, "validator_address": address})
    if mapped_live != live_set:
        missing = sorted(live_set - mapped_live)
        raise _fail("NATIVE_MINT_VALIDATOR_SET_UNMAPPED", f"live validator set contains addresses not represented by the accepted topology: {missing}")
    return targets


def _coolify_http(controller: Any, method: str, endpoint: str, *, body: Mapping[str, Any] | None = None, timeout: float = DEFAULT_TIMEOUT, max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES) -> dict[str, Any]:
    if not endpoint.startswith("/api/v1/") or "\\" in endpoint or "\x00" in endpoint:
        raise _fail("NATIVE_MINT_COOLIFY_REQUEST_INVALID", "unsafe Coolify endpoint")
    payload = canonical_json(dict(body)) if body is not None else None
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {controller.api_token}",
        "User-Agent": "main-computer-native-mint/1",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(controller.base_url + endpoint, data=payload, method=method.upper(), headers=headers)
    try:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                status = int(response.status)
                raw = response.read(max_response_bytes + 1)
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            raw = exc.read(max_response_bytes + 1)
    except (urllib.error.URLError, OSError) as exc:
        raise _fail("NATIVE_MINT_COOLIFY_REQUEST_FAILED", f"Coolify {method} {endpoint} failed") from exc
    if len(raw) > max_response_bytes:
        raise _fail("NATIVE_MINT_COOLIFY_REQUEST_FAILED", "Coolify response exceeded the size limit")
    try:
        decoded = json.loads(raw.decode("utf-8")) if raw else None
    except Exception:
        decoded = raw.decode("utf-8", errors="replace")[:2048]
    return {"status": status, "ok": 200 <= status < 300, "payload": decoded}


def _response_uuid(payload: Any) -> str:
    found: set[str] = set()
    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if key in {"uuid", "service_uuid"} and isinstance(item, str) and UUID_RE.fullmatch(item):
                    found.add(item)
                elif isinstance(item, (Mapping, list)):
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(payload)
    if len(found) != 1:
        raise _fail("NATIVE_MINT_COOLIFY_RESPONSE_INVALID", "Coolify service creation response did not contain exactly one service UUID")
    return next(iter(found))


def _controller_config(private: PrivateStateReadResult, network: str, controller_id: str) -> dict[str, Any]:
    return load_controller_config(
        private,
        network=network,
        controller_id=controller_id,
        allowed_controllers={controller_id},
        error_factory=lambda code, message: NativeMintError(code, message),
        rejected_code="NATIVE_MINT_CONTROLLER_REJECTED",
        invalid_code="NATIVE_MINT_PRIVATE_STATE_INVALID",
        placement_description="native mint control",
    )


def _helper_compose(*, helper_name: str, parent_service_uuid: str, mode: str, expected_old_sha: str, new_genesis_b64: str = "", new_sha: str = "") -> str:
    parent = _identifier(parent_service_uuid, "parent service uuid")
    if mode not in {"probe", "write"}:
        raise _fail("NATIVE_MINT_INVALID", "helper mode is invalid")
    mount_source = f"/var/lib/docker/volumes/{parent}_mother-config/_data"
    script = r'''import base64,hashlib,json,os,pathlib,sys
p=pathlib.Path('/config/genesis.json')
mode=os.environ['MODE']
expected=os.environ['EXPECTED_OLD_SHA']
new_sha=os.environ.get('NEW_SHA','')
try:
    raw=p.read_bytes()
    cur=hashlib.sha256(raw).hexdigest()
    result={'mode':mode,'before_sha256':cur,'expected_old_sha256':expected}
    if mode=='probe':
        result['ok']=cur in {expected,new_sha} if new_sha else cur==expected
    else:
        if cur==new_sha:
            result.update({'ok':True,'classification':'already-applied','after_sha256':cur})
        elif cur!=expected:
            result.update({'ok':False,'classification':'unexpected-prestate'})
        else:
            data=base64.b64decode(os.environ['NEW_GENESIS_B64'], validate=True)
            actual=hashlib.sha256(data).hexdigest()
            if actual!=new_sha: raise RuntimeError('new genesis sha mismatch')
            tmp=p.with_name('genesis.json.native-mint.tmp')
            with tmp.open('wb') as h:
                h.write(data); h.flush(); os.fsync(h.fileno())
            os.chmod(tmp,0o444); os.replace(tmp,p)
            fd=os.open(str(p.parent),os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)
            after=hashlib.sha256(p.read_bytes()).hexdigest()
            result.update({'ok':after==new_sha,'classification':'written','after_sha256':after})
except Exception as exc:
    result={'mode':mode,'ok':False,'classification':'helper-error','error_type':type(exc).__name__,'error':str(exc)[:512]}
print('NATIVE_MINT_HELPER_RESULT='+json.dumps(result,sort_keys=True,separators=(',',':')),flush=True)
sys.exit(0 if result.get('ok') else 23)
'''
    encoded_script = base64.b64encode(script.encode("utf-8")).decode("ascii")
    wrapper = f"""mkdir -p /run/mother-helper
printf '%s' '{encoded_script}' | base64 -d > /run/mother-helper/native_mint_helper.py
chmod 0500 /run/mother-helper/native_mint_helper.py
if python -u /run/mother-helper/native_mint_helper.py; then
  printf '%s\n' 'NATIVE_MINT_HELPER_WRAPPER=python-ok'
  touch /run/mother-helper/result-ok
else
  printf '%s\n' 'NATIVE_MINT_HELPER_WRAPPER=python-nonzero' >&2
  touch /run/mother-helper/result-failed
fi
touch /run/mother-helper/result-ready
exec sleep 900
"""
    healthcheck = 'test "$(cat /proc/1/comm)" = "sleep" && test -f /run/mother-helper/result-ok'
    document: dict[str, Any] = {
        "services": {
            helper_name: {
                "image": HELPER_IMAGE,
                "restart": "no",
                "read_only": True,
                "tmpfs": ["/run/mother-helper:size=256k,mode=0700"],
                "environment": {
                    "MODE": mode,
                    "EXPECTED_OLD_SHA": expected_old_sha,
                    "NEW_SHA": new_sha,
                    "NEW_GENESIS_B64": new_genesis_b64,
                },
                "entrypoint": ["/bin/sh", "-ec"],
                "command": [wrapper],
                "healthcheck": {
                    "test": ["CMD-SHELL", healthcheck],
                    "interval": "2s",
                    "timeout": "5s",
                    "retries": 20,
                    "start_period": "2s",
                },
                "volumes": [
                    {
                        "type": "bind",
                        "source": mount_source,
                        "target": "/config",
                        "read_only": mode == "probe",
                    }
                ],
                "labels": {
                    "main_computer.mother.stage": "native-mint-control",
                    "main_computer.mother.parent-service-uuid": parent,
                    "main_computer.mother.disposable-service-row": "true",
                },
            }
        }
    }
    return yaml.safe_dump(document, sort_keys=False)


def _helper_body(config: Mapping[str, Any], *, helper_name: str, compose: str, network: str) -> dict[str, Any]:
    return {
        "project_uuid": config["project_uuid"],
        "server_uuid": config["server_uuid"],
        "environment_name": network,
        "docker_compose_raw": base64.b64encode(compose.encode("utf-8")).decode("ascii"),
        "name": helper_name,
        "description": "Ephemeral Mother QBFT native-mint genesis helper",
        "instant_deploy": False,
    }


def _extract_helper_marker(payload: Any) -> dict[str, Any] | None:
    texts: list[str] = []
    def walk(value: Any) -> None:
        if isinstance(value, str):
            texts.append(value)
        elif isinstance(value, Mapping):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(payload)
    for text in texts:
        for line in text.splitlines():
            marker = "NATIVE_MINT_HELPER_RESULT="
            if marker in line:
                raw = line.split(marker, 1)[1].strip()
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    return value
    return None


def _helper_log_text(payload: Any) -> str:
    if isinstance(payload, Mapping):
        logs = payload.get("logs")
        if isinstance(logs, str):
            return logs
    if isinstance(payload, str):
        return payload
    texts: list[str] = []
    def walk(value: Any) -> None:
        if isinstance(value, str):
            texts.append(value)
        elif isinstance(value, Mapping):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(payload)
    return "\n".join(texts)


def _helper_application(detail_payload: Any, helper_name: str) -> tuple[str | None, str | None, str | None]:
    if not isinstance(detail_payload, Mapping):
        return None, None, None
    applications = detail_payload.get("applications")
    if not isinstance(applications, list):
        return None, None, None
    candidates = [item for item in applications if isinstance(item, Mapping)]
    exact = [item for item in candidates if item.get("name") == helper_name]
    selected = exact[0] if exact else (candidates[0] if len(candidates) == 1 else None)
    if selected is None:
        return None, None, None
    raw_uuid = selected.get("uuid")
    raw_name = selected.get("name")
    raw_status = selected.get("status") or selected.get("state")
    app_uuid = raw_uuid if isinstance(raw_uuid, str) and UUID_RE.fullmatch(raw_uuid) else None
    app_name = raw_name if isinstance(raw_name, str) and raw_name else None
    status = raw_status.lower() if isinstance(raw_status, str) else None
    return app_uuid, app_name, status


def _helper_log_candidates(helper_uuid: str, application_uuid: str | None, application_name: str | None) -> list[tuple[str, str]]:
    service_q = urllib.parse.quote(helper_uuid, safe="")
    candidates: list[tuple[str, str]] = []
    # Prefer nested application logs after materialization. The parent
    # service/subresource route can block while Coolify is reconciling.
    if application_uuid:
        app_q = urllib.parse.quote(application_uuid, safe="")
        candidates.extend([
            (
                "service-application",
                f"/api/v1/services/{service_q}/applications/{app_q}/logs?lines=100&show_timestamps=false",
            ),
            (
                "application-resource",
                f"/api/v1/applications/{app_q}/logs?lines=100",
            ),
        ])
    if application_name:
        name_q = urllib.parse.quote(application_name, safe="")
        candidates.append((
            "service-subresource",
            f"/api/v1/services/{service_q}/logs?sub_service_name={name_q}&lines=100&show_timestamps=false",
        ))
    return candidates

def _helper_error_message(payload: Any) -> str | None:
    if isinstance(payload, Mapping):
        value = payload.get("message")
        if isinstance(value, str) and value.strip():
            return value.strip()[:240]
    if isinstance(payload, str) and payload.strip():
        return payload.strip()[:240]
    return None


def _helper_log_probe(controller: Any, *, helper_uuid: str, application_uuid: str | None, application_name: str | None, timeout: float) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str]:
    attempts: list[dict[str, Any]] = []
    last_log_text = ""
    probe_timeout = min(float(timeout), DEFAULT_HELPER_LOG_TIMEOUT_SECONDS)
    for kind, endpoint in _helper_log_candidates(helper_uuid, application_uuid, application_name):
        try:
            response = _coolify_http(controller, "GET", endpoint, timeout=probe_timeout)
        except NativeMintError as exc:
            attempts.append({
                "kind": kind,
                "status": None,
                "has_logs": False,
                "marker": False,
                "error": f"{exc.code}: {exc.message}"[:240],
            })
            continue
        payload = response.get("payload")
        text = _helper_log_text(payload) if response.get("ok") else ""
        if text:
            last_log_text = text
        marker = _extract_helper_marker(payload) if response.get("ok") else None
        attempts.append({
            "kind": kind,
            "status": response.get("status"),
            "has_logs": bool(text),
            "marker": marker is not None,
            "error": None if response.get("ok") else _helper_error_message(payload),
        })
        if marker is not None:
            return marker, attempts, last_log_text
    return None, attempts, last_log_text

def _helper_attempt_summary(attempts: list[dict[str, Any]]) -> str:
    if not attempts:
        return "none"
    parts: list[str] = []
    for item in attempts:
        part = f"{item.get('kind')}:{item.get('status')}:logs={'yes' if item.get('has_logs') else 'no'}:marker={'yes' if item.get('marker') else 'no'}"
        error = item.get("error")
        if isinstance(error, str) and error:
            clean = error.replace("\r", " ").replace("\n", " ")
            part += f":error={clean!r}"
        parts.append(part)
    return ",".join(parts)


def _run_helper(*, private: PrivateStateReadResult, network: str, controller_id: str, parent_service_uuid: str, mode: str, expected_old_sha: str, new_genesis: bytes | None = None, new_sha: str = "", timeout: float = DEFAULT_TIMEOUT, wait_seconds: float = DEFAULT_HELPER_WAIT_SECONDS) -> dict[str, Any]:
    controller = resolve_coolify_controller(private, network, controller_id, require_enabled=True, require_token=True)
    config = _controller_config(private, network, controller_id)
    helper_name = f"mother-native-mint-{mode}-{parent_service_uuid[:8]}-{time.time_ns() % 1_000_000:06d}"
    encoded = base64.b64encode(new_genesis).decode("ascii") if new_genesis is not None else ""
    compose = _helper_compose(helper_name=helper_name, parent_service_uuid=parent_service_uuid, mode=mode, expected_old_sha=expected_old_sha, new_genesis_b64=encoded, new_sha=new_sha)
    print(
        f"NATIVE_MINT_HELPER: create controller={controller_id} mode={mode} parent_service={parent_service_uuid} helper={helper_name}",
        file=sys.stderr,
        flush=True,
    )
    create = _coolify_http(controller, "POST", "/api/v1/services", body=_helper_body(config, helper_name=helper_name, compose=compose, network=network), timeout=timeout)
    if not create["ok"]:
        raise _fail("NATIVE_MINT_HELPER_CREATE_FAILED", f"{controller_id} helper create failed with HTTP {create['status']}")
    helper_uuid = _response_uuid(create["payload"])
    print(
        f"NATIVE_MINT_HELPER: created controller={controller_id} helper_uuid={helper_uuid}; force-deploying",
        file=sys.stderr,
        flush=True,
    )
    try:
        deploy = _coolify_http(
            controller,
            "POST",
            "/api/v1/deploy",
            body={"uuid": helper_uuid, "force": True},
            timeout=timeout,
        )
        if not deploy["ok"]:
            raise _fail("NATIVE_MINT_HELPER_DEPLOY_FAILED", f"{controller_id} helper forced deploy failed with HTTP {deploy['status']}")
        started_at = time.monotonic()
        deadline = started_at + wait_seconds
        next_progress = started_at
        terminal_since: float | None = None
        materialized = False
        last_status: Any = None
        last_app_uuid: str | None = None
        last_app_name: str | None = None
        last_app_status: str | None = None
        last_attempts: list[dict[str, Any]] = []
        last_log_text = ""
        while True:
            detail = _coolify_http(controller, "GET", f"/api/v1/services/{urllib.parse.quote(helper_uuid, safe='')}", timeout=timeout)
            if detail["ok"]:
                last_status = detail["payload"]
                app_uuid, app_name, app_status = _helper_application(detail["payload"], helper_name)
                if app_uuid is not None:
                    last_app_uuid = app_uuid
                if app_name is not None:
                    last_app_name = app_name
                if app_status is not None:
                    last_app_status = app_status
            if last_app_status and last_app_status.startswith("running"):
                materialized = True

            marker: dict[str, Any] | None = None
            attempts: list[dict[str, Any]] = []
            log_text = ""
            if materialized:
                marker, attempts, log_text = _helper_log_probe(
                    controller,
                    helper_uuid=helper_uuid,
                    application_uuid=last_app_uuid,
                    application_name=last_app_name,
                    timeout=timeout,
                )
                last_attempts = attempts
                if log_text:
                    last_log_text = log_text
            if marker is not None:
                elapsed = time.monotonic() - started_at
                print(
                    f"NATIVE_MINT_HELPER: proof controller={controller_id} mode={mode} app_status={last_app_status or 'unknown'} elapsed={elapsed:.1f}s endpoint={next((a['kind'] for a in attempts if a.get('marker')), 'unknown')}",
                    file=sys.stderr,
                    flush=True,
                )
                if marker.get("ok") is not True:
                    raise _fail("NATIVE_MINT_REMOTE_GENESIS_MISMATCH", f"{controller_id}/{parent_service_uuid} helper rejected genesis prestate: {marker}")
                return marker

            now = time.monotonic()
            terminal = bool(materialized and last_app_status and (
                last_app_status.startswith("exited")
                or last_app_status.startswith("stopped")
                or last_app_status.startswith("dead")
                or last_app_status.startswith("failed")
            ))
            if terminal and terminal_since is None:
                terminal_since = now
                print(
                    f"NATIVE_MINT_HELPER: application terminal controller={controller_id} app_uuid={last_app_uuid or 'unknown'} status={last_app_status}; allowing {DEFAULT_HELPER_EXIT_LOG_GRACE_SECONDS:.0f}s for final logs",
                    file=sys.stderr,
                    flush=True,
                )
            elif not terminal:
                terminal_since = None

            if now >= next_progress:
                elapsed = now - started_at
                print(
                    f"NATIVE_MINT_HELPER: poll controller={controller_id} mode={mode} elapsed={elapsed:.1f}s app_uuid={last_app_uuid or 'unresolved'} app_status={last_app_status or 'unknown'} materialized={'yes' if materialized else 'no'} logs=[{_helper_attempt_summary(last_attempts) if materialized else 'not-probed'}]",
                    file=sys.stderr,
                    flush=True,
                )
                next_progress = now + DEFAULT_HELPER_PROGRESS_SECONDS

            if terminal_since is not None and now - terminal_since >= DEFAULT_HELPER_EXIT_LOG_GRACE_SECONDS:
                tail = last_log_text[-1200:].replace("\r", "\\r").replace("\n", "\\n") if last_log_text else "<no logs returned>"
                raise _fail(
                    "NATIVE_MINT_HELPER_EXITED_NO_MARKER",
                    f"{controller_id} helper application exited without a proof marker; app_uuid={last_app_uuid or 'unknown'} status={last_app_status or 'unknown'} log_endpoints=[{_helper_attempt_summary(last_attempts)}] log_tail={tail}",
                )
            if now >= deadline:
                tail = last_log_text[-1200:].replace("\r", "\\r").replace("\n", "\\n") if last_log_text else "<no logs returned>"
                raise _fail(
                    "NATIVE_MINT_HELPER_TIMEOUT",
                    f"{controller_id} helper did not produce a proof marker before timeout; app_uuid={last_app_uuid or 'unknown'} status={last_app_status or 'unknown'} log_endpoints=[{_helper_attempt_summary(last_attempts)}] log_tail={tail}; last={str(last_status)[:512]}",
                )
            time.sleep(DEFAULT_POLL_SECONDS)
    finally:
        # Normal operation cleans its own helper.  The explicit native-mint
        # --cleanup command below removes leftovers from interrupted/older runs.
        try:
            deleted = _coolify_http(controller, "DELETE", f"/api/v1/services/{urllib.parse.quote(helper_uuid, safe='')}", timeout=timeout)
            print(
                f"NATIVE_MINT_HELPER: cleanup controller={controller_id} helper_uuid={helper_uuid} http={deleted.get('status')}",
                file=sys.stderr,
                flush=True,
            )
        except Exception as exc:
            print(
                f"NATIVE_MINT_HELPER: cleanup warning controller={controller_id} helper_uuid={helper_uuid} error={type(exc).__name__}: {exc}",
                file=sys.stderr,
                flush=True,
            )

def _coolify_records(payload: Any) -> list[Mapping[str, Any]]:
    records: list[Mapping[str, Any]] = []
    if isinstance(payload, Mapping):
        records.append(payload)
        for key in ("data", "services", "resources"):
            nested = payload.get(key)
            if isinstance(nested, list):
                records.extend(item for item in nested if isinstance(item, Mapping))
            elif isinstance(nested, Mapping):
                records.append(nested)
    elif isinstance(payload, list):
        records.extend(item for item in payload if isinstance(item, Mapping))
    return records


def _native_mint_controller_ids(private_doc: Mapping[str, Any], network: str) -> list[str]:
    network_state = _network_state(private_doc, network)
    coolify = network_state.get("coolify")
    controllers = coolify.get("controllers") if isinstance(coolify, Mapping) else None
    if not isinstance(controllers, Mapping):
        raise _fail("NATIVE_MINT_PRIVATE_STATE_INVALID", f"{network} Coolify controller map is missing")
    result: list[str] = []
    for raw_id, raw_cfg in controllers.items():
        if not isinstance(raw_id, str) or not isinstance(raw_cfg, Mapping):
            continue
        if raw_cfg.get("enabled", True) is not True:
            continue
        result.append(_identifier(raw_id, "controller_id"))
    if not result:
        raise _fail("NATIVE_MINT_PRIVATE_STATE_INVALID", f"{network} has no enabled Coolify controllers")
    return sorted(set(result))


def _cleanup_helpers(args: argparse.Namespace) -> dict[str, Any]:
    network = _identifier(args.network, "network")
    runtime_root = Path(args.runtime_state_root)
    op = _operation_identity("cleanup", network)
    _paths_obj, private, private_doc = _load_private(runtime_root, op)
    deleted: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    matched: list[dict[str, Any]] = []

    for controller_id in _native_mint_controller_ids(private_doc, network):
        controller = resolve_coolify_controller(private, network, controller_id, require_enabled=True, require_token=True)
        listing = _coolify_http(controller, "GET", "/api/v1/services", timeout=args.timeout)
        if listing.get("ok") is not True:
            failed.append({"controller_id": controller_id, "stage": "list", "http_status": listing.get("status")})
            continue
        seen: set[str] = set()
        for item in _coolify_records(listing.get("payload")):
            name = item.get("name")
            service_uuid = item.get("uuid") or item.get("service_uuid")
            if not isinstance(name, str) or not name.startswith(HELPER_NAME_PREFIX):
                continue
            if not isinstance(service_uuid, str) or not service_uuid.strip() or service_uuid in seen:
                continue
            seen.add(service_uuid)
            service_uuid = _identifier(service_uuid, "helper service_uuid")
            record = {"controller_id": controller_id, "name": name, "service_uuid": service_uuid}
            matched.append(record)
            if args.dry_run:
                continue
            response = _coolify_http(
                controller,
                "DELETE",
                f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}",
                timeout=args.timeout,
            )
            if response.get("ok") is True or response.get("status") == 404:
                deleted.append({**record, "http_status": response.get("status")})
                print(
                    f"NATIVE_MINT_CLEANUP: deleted controller={controller_id} helper_uuid={service_uuid} name={name}",
                    file=sys.stderr,
                    flush=True,
                )
            else:
                failed.append({**record, "stage": "delete", "http_status": response.get("status")})

    return {
        "ok": not failed,
        "status": "dry-run" if args.dry_run else "cleaned",
        "network": network,
        "matched_count": len(matched),
        "deleted_count": len(deleted),
        "failed_count": len(failed),
        "matched": matched,
        "deleted": deleted,
        "failed": failed,
    }


def _service_statuses(payload: Any, node: str) -> list[str]:
    statuses: list[str] = []
    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            name = value.get("name")
            status = value.get("status") or value.get("state")
            if name == node and isinstance(status, str):
                statuses.append(status.lower())
            for item in value.values():
                if isinstance(item, (Mapping, list)):
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(payload)
    return statuses


def _service_control(private: PrivateStateReadResult, *, network: str, target: Mapping[str, str], action: str, timeout: float = DEFAULT_TIMEOUT) -> None:
    controller_id = target["controller_id"]
    service_uuid = target["service_uuid"]
    controller = resolve_coolify_controller(private, network, controller_id, require_enabled=True, require_token=True)
    body: Mapping[str, Any] | None = None
    if action == "stop":
        method, endpoint = "POST", f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}/stop?docker_cleanup=false"
    elif action == "start":
        # Starting an existing service row through /services/<uuid>/start can
        # acknowledge successfully without materializing the Compose application.
        # Use the same forced-deploy primitive as native-mint helpers and Mother's
        # proven Coolify deployment paths so a stopped validator is actually
        # recreated from its staged Compose/genesis state.
        method, endpoint = "POST", "/api/v1/deploy"
        body = {"uuid": service_uuid, "force": True}
    else:
        raise _fail("NATIVE_MINT_INVALID", "service control action is invalid")
    response = _coolify_http(controller, method, endpoint, body=body, timeout=timeout)
    tolerated = {400, 409} if action == "stop" else set()
    if not response["ok"] and response["status"] not in tolerated:
        raise _fail("NATIVE_MINT_SERVICE_CONTROL_FAILED", f"{action} failed for {target['node']} on {controller_id}: HTTP {response['status']}")


def _wait_service_running(private: PrivateStateReadResult, *, network: str, target: Mapping[str, str], timeout: float = DEFAULT_TIMEOUT, wait_seconds: float = DEFAULT_RPC_WAIT_SECONDS) -> None:
    controller = resolve_coolify_controller(private, network, target["controller_id"], require_enabled=True, require_token=True)
    endpoint = f"/api/v1/services/{urllib.parse.quote(target['service_uuid'], safe='')}"
    deadline = time.monotonic() + wait_seconds
    last: list[str] = []
    while True:
        response = _coolify_http(controller, "GET", endpoint, timeout=timeout)
        if response["ok"]:
            last = _service_statuses(response["payload"], target["node"])
            if any(status.startswith("running") for status in last):
                return
        if time.monotonic() >= deadline:
            raise _fail("NATIVE_MINT_SERVICE_START_TIMEOUT", f"{target['node']} did not return to running state; statuses={last}")
        time.sleep(DEFAULT_POLL_SECONDS)


def _wait_service_stopped(private: PrivateStateReadResult, *, network: str, target: Mapping[str, str], timeout: float = DEFAULT_TIMEOUT, wait_seconds: float = DEFAULT_RPC_WAIT_SECONDS) -> None:
    controller = resolve_coolify_controller(private, network, target["controller_id"], require_enabled=True, require_token=True)
    endpoint = f"/api/v1/services/{urllib.parse.quote(target['service_uuid'], safe='')}"
    deadline = time.monotonic() + wait_seconds
    last: list[str] = []
    while True:
        response = _coolify_http(controller, "GET", endpoint, timeout=timeout)
        if response["ok"]:
            last = _service_statuses(response["payload"], target["node"])
            if last and all(not status.startswith("running") for status in last):
                return
        if time.monotonic() >= deadline:
            raise _fail("NATIVE_MINT_SERVICE_STOP_TIMEOUT", f"{target['node']} did not reach stopped state; statuses={last}")
        time.sleep(DEFAULT_POLL_SECONDS)


def _wait_rpc(rpc_url: str, chain_id: int, *, wait_seconds: float = DEFAULT_RPC_WAIT_SECONDS) -> int:
    deadline = time.monotonic() + wait_seconds
    last = ""
    while True:
        try:
            observed_chain = _rpc_int(rpc_url, "eth_chainId")
            block = _latest_block(rpc_url)
            if observed_chain == chain_id:
                return block
            last = f"chain_id={observed_chain}"
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
        if time.monotonic() >= deadline:
            raise _fail("NATIVE_MINT_RPC_RECOVERY_TIMEOUT", f"shared RPC did not recover after validator restart; last={last}")
        time.sleep(DEFAULT_POLL_SECONDS)


def _wait_block(rpc_url: str, target_block: int, *, wait_seconds: float = DEFAULT_RPC_WAIT_SECONDS) -> int:
    deadline = time.monotonic() + wait_seconds
    last = -1
    while True:
        last = _latest_block(rpc_url)
        if last >= target_block:
            return last
        if time.monotonic() >= deadline:
            raise _fail("NATIVE_MINT_BLOCK_TIMEOUT", f"chain did not reach block {target_block}; last={last}")
        time.sleep(DEFAULT_POLL_SECONDS)


def _write_topology_evidence(runtime_state_root: Path, *, operation: Mapping[str, Any], baseline_path: Path, baseline_doc: Mapping[str, Any], new_genesis: Mapping[str, Any], new_sha: str) -> Path:
    topology = _extract_topology(baseline_doc)
    assert topology is not None
    topology["genesis_sha256"] = new_sha
    now = _utc_now()
    document = {
        "kind": TOPOLOGY_EVIDENCE_KIND,
        "schema_version": 1,
        "status": "pass",
        "network": operation["network"],
        "chain_id": operation["chain_id"],
        "genesis_sha256": new_sha,
        "created_at": now,
        "completed_at": now,
        "operation_id": operation["operation_id"],
        "native_mint": {
            "mode": operation["mode"],
            "recipient": operation.get("recipient"),
            "activation_block": operation.get("window", {}).get("activation_block"),
            "expiration_block": operation.get("window", {}).get("expiration_block"),
            "new_genesis_sha256": new_sha,
        },
        "source_baseline_evidence": {
            "path": str(baseline_path),
            "sha256": _sha_file(baseline_path),
            "completed_at": baseline_doc.get("completed_at") or baseline_doc.get("created_at"),
        },
        "genesis": dict(new_genesis),
        "current_topology": topology,
        "summary": {
            "complete": True,
            "clean": True,
            "current_nodes": list(topology.get("nodes") or []),
            "current_validator_set": list(topology.get("validator_set") or []),
            "completed_at": now,
            "native_mint_config_rollout_verified": True,
        },
    }
    path = runtime_state_root / "mother" / "evidence" / TOPOLOGY_EVIDENCE_DIRECTORY / f"{_stamp()}-{operation['operation_id']}.json"
    _write_json(path, document)
    return path


def _prepare(args: argparse.Namespace) -> dict[str, Any]:
    network = _identifier(args.network, "network")
    op = _operation_identity(args.mode, network, args.operation_id)
    paths, private, private_doc = _load_private(Path(args.runtime_state_root), op)
    network_state = _network_state(private_doc, network)
    rpc_url = _rpc_url(network_state)
    chain_id = _positive_int(network_state.get("chain_id"), "chain_id")
    live_chain = _rpc_int(rpc_url, "eth_chainId")
    if live_chain != chain_id:
        raise _fail("NATIVE_MINT_CHAIN_ID_MISMATCH", f"shared RPC reports chain_id {live_chain}, expected {chain_id}")
    current_block = _latest_block(rpc_url)
    rpc_validators = _validators(rpc_url)
    baseline_path, baseline_doc = _latest_baseline(Path(args.runtime_state_root))
    topology = _extract_topology(baseline_doc)
    assert topology is not None
    baseline_chain_id, old_sha = _chain_identity(baseline_doc, topology)
    if baseline_chain_id != chain_id:
        raise _fail("NATIVE_MINT_CHAIN_ID_MISMATCH", "accepted topology chain ID disagrees with private state")
    genesis = _find_genesis(
        Path(args.runtime_state_root),
        old_sha,
        baseline_doc,
        private_doc=private_doc,
        network=network,
    )
    qbft_config = genesis.get("config", {}).get("qbft", {}) if isinstance(genesis.get("config"), Mapping) else {}
    block_period_seconds = _positive_int(
        qbft_config.get("blockperiodseconds", 1) if isinstance(qbft_config, Mapping) else 1,
        "blockperiodseconds",
    )
    targets = _topology_targets(private_doc, network, topology, rpc_validators)
    if not targets:
        raise _fail("NATIVE_MINT_TOPOLOGY_MISSING", "accepted topology contains no chain nodes")

    mode = args.mode
    recipient: str | None = None
    recipient_source: str | None = None
    wei_per_block = 0
    blocks = 0
    activation = 0
    expiration = 0
    close_of: str | None = None
    close_block: int | None = None

    if mode in {"mint", "open"}:
        recipient, recipient_source = _resolve_recipient(private_doc, network, args.to)
        wei_per_block = _amount_wei(args, per_block=(mode == "open"))
        blocks = 1 if mode == "mint" else _positive_int(args.blocks, "blocks")
        if mode == "open" and blocks < 2:
            raise _fail("NATIVE_MINT_INVALID", "native-mint-open requires --blocks >= 2; use native-mint for a one-block pulse")
        lead = _positive_int(args.activation_lead_blocks, "activation_lead_blocks")
        activation = current_block + lead
        expiration = activation + blocks
        new_genesis = _build_open_genesis(genesis, current_block=current_block, activation_block=activation, expiration_block=expiration, recipient=recipient, wei_per_block=wei_per_block)
    elif mode == "close":
        active = _active_operation(Path(args.runtime_state_root), network)
        if active is None:
            raise _fail("NATIVE_MINT_NO_ACTIVE_WINDOW", "there is no recorded native-mint window to close")
        close_of = str(active["operation_id"])
        lead = _positive_int(args.close_lead_blocks, "close_lead_blocks")
        new_genesis, close_block = _build_close_genesis(genesis, current_block=current_block, active=active["window"], close_block=current_block + lead)
        recipient = str(active.get("recipient") or "") or None
        recipient_source = str(active.get("recipient_source") or "") or None
        wei_per_block = int(active.get("wei_per_block") or 0)
        activation = int(active["window"]["activation_block"])
        expiration = int(active["window"]["expiration_block"])
        blocks = int(active.get("blocks") or 0)
    else:
        raise _fail("NATIVE_MINT_INVALID", f"unsupported mode {mode}")

    new_raw = canonical_json(new_genesis)
    new_sha = _sha_bytes(new_raw)
    operation = {
        "schema": SCHEMA,
        "operation_id": op.operation_id,
        "mode": mode,
        "network": network,
        "chain_id": chain_id,
        "rpc_url": rpc_url,
        "status": "prepared",
        "created_at": _utc_now(),
        "baseline": {"path": str(baseline_path), "sha256": _sha_file(baseline_path)},
        "old_genesis_sha256": old_sha,
        "new_genesis_sha256": new_sha,
        "genesis": new_genesis,
        "targets": targets,
        "recipient": recipient,
        "recipient_source": recipient_source,
        "wei_per_block": wei_per_block,
        "blocks": blocks,
        "max_mint_wei": wei_per_block * blocks,
        "window": {"activation_block": activation, "expiration_block": expiration, "manual_close_block": close_block},
        "close_of_operation_id": close_of,
        "prep_block": current_block,
        "block_period_seconds": block_period_seconds,
        "validator_set": rpc_validators,
        "topology_node_count": len(targets),
        "topology_evidence": None,
        "proof": None,
    }
    _write_json(_operation_path(Path(args.runtime_state_root), op.operation_id), operation)
    return {
        "ok": True,
        "status": "prepared",
        "operation_id": op.operation_id,
        "mode": mode,
        "network": network,
        "chain_id": chain_id,
        "current_block": current_block,
        "block_period_seconds": block_period_seconds,
        "activation_block": activation,
        "expiration_block": expiration,
        "manual_close_block": close_block,
        "recipient": recipient,
        "recipient_source": recipient_source,
        "wei_per_block": wei_per_block,
        "blocks": blocks,
        "max_mint_wei": wei_per_block * blocks,
        "old_genesis_sha256": old_sha,
        "new_genesis_sha256": new_sha,
        "target_count": len(targets),
        "targets": [{k: item[k] for k in ("node", "controller_id", "service_uuid")} for item in targets],
    }


def _execution_current_block(rpc_url: str, chain_id: int, operation: Mapping[str, Any]) -> tuple[int, bool]:
    recovering_rollout = operation.get("status") in {"rolling-out", "genesis-staged"}
    try:
        current_chain = _rpc_int(rpc_url, "eth_chainId")
        if current_chain != chain_id:
            raise _fail("NATIVE_MINT_CHAIN_ID_MISMATCH", "shared RPC chain ID changed since prep")
        return _latest_block(rpc_url), True
    except NativeMintError as exc:
        if exc.code == "NATIVE_MINT_CHAIN_ID_MISMATCH" or not recovering_rollout:
            raise
        return int(operation.get("rollout_started_block") or operation.get("prep_block") or 0), False


def _can_auto_refresh_stale(
    operation: Mapping[str, Any],
    probe_results: list[dict[str, Any]],
    old_sha: str,
    *,
    live_rpc_available: bool = False,
) -> bool:
    all_old = bool(probe_results) and all(item.get("sha256") == old_sha for item in probe_results)
    if not all_old:
        return False
    status = operation.get("status")
    if status == "prepared":
        return True
    # A previous attempt can mark rolling-out before its first stop request.
    # If live RPC still works and every accepted node still has the old genesis,
    # no genesis rollout has actually happened and the window is safe to refresh.
    return status == "rolling-out" and live_rpc_available


def _stale_refresh_args(runtime_root: Path, operation: Mapping[str, Any]) -> argparse.Namespace:
    mode = str(operation.get("mode") or "")
    window = operation.get("window") if isinstance(operation.get("window"), Mapping) else {}
    prep_block = int(operation.get("prep_block") or 0)
    activation = int(window.get("activation_block") or 0)
    manual_close = window.get("manual_close_block")
    activation_lead = max(1, activation - prep_block) if activation > prep_block else DEFAULT_ACTIVATION_LEAD_BLOCKS
    close_lead = (
        max(1, int(manual_close) - prep_block)
        if manual_close is not None and int(manual_close) > prep_block
        else DEFAULT_CLOSE_LEAD_BLOCKS
    )
    wei_per_block = int(operation.get("wei_per_block") or 0)
    return argparse.Namespace(
        runtime_state_root=str(runtime_root),
        mode=mode,
        network=str(operation.get("network") or ""),
        operation_id=str(operation.get("operation_id") or ""),
        to=str(operation.get("recipient") or "") or None,
        amount_wei=str(wei_per_block) if mode == "mint" else None,
        amount_native=None,
        wei_per_block=str(wei_per_block) if mode == "open" else None,
        native_per_block=None,
        blocks=int(operation.get("blocks") or 2),
        activation_lead_blocks=activation_lead,
        close_lead_blocks=close_lead,
    )


def _refresh_stale_prepared_operation(runtime_root: Path, operation: Mapping[str, Any]) -> dict[str, Any]:
    if operation.get("status") not in {"prepared", "rolling-out"}:
        raise _fail("NATIVE_MINT_PREP_STALE", "native-mint transition is stale after genesis staging began; refusing automatic re-prep")
    operation_id = str(operation.get("operation_id") or "")
    old_activation = int(operation.get("window", {}).get("activation_block") or 0)
    print(
        f"NATIVE_MINT_PROGRESS: stale prepared operation {operation_id}; refreshing transition window from activation={old_activation}",
        file=sys.stderr,
        flush=True,
    )
    refreshed = _prepare(_stale_refresh_args(runtime_root, operation))
    print(
        f"NATIVE_MINT_PROGRESS: refreshed prep operation={operation_id} current_block={refreshed['current_block']} activation={refreshed['activation_block']} expiration={refreshed['expiration_block']}",
        file=sys.stderr,
        flush=True,
    )
    return refreshed


def _execute(args: argparse.Namespace) -> dict[str, Any]:
    operation_id = _identifier(args.operation_id, "operation_id")
    runtime_root = Path(args.runtime_state_root)
    path, operation = _load_operation(runtime_root, operation_id)
    if operation.get("status") in {"applied", "finalized"}:
        return {"ok": True, "status": operation["status"], "operation_id": operation_id, "already_applied": True, "proof": operation.get("proof")}
    network = _identifier(operation.get("network"), "network")
    op = _operation_identity("do", network, operation_id)
    _, private, private_doc = _load_private(runtime_root, op)
    rpc_url = str(operation["rpc_url"])
    chain_id = int(operation["chain_id"])
    recovering_rollout = operation.get("status") in {"rolling-out", "genesis-staged"}
    # Prefer live chain height whenever it is still observable.  A failed first
    # stop request can leave status="rolling-out" even though every validator is
    # still running and the chain is advancing.  Only fall back to the recorded
    # rollout block when RPC is genuinely unavailable during recovery.
    current_block, live_rpc_available = _execution_current_block(rpc_url, chain_id, operation)
    activation = int(operation["window"]["activation_block"])
    expiration = int(operation["window"]["expiration_block"])
    manual_close = operation["window"].get("manual_close_block")
    effective_transition = int(manual_close) if manual_close is not None else activation
    stale_margin = 12
    all_targets = list(operation["targets"])
    new_sha = str(operation["new_genesis_sha256"])
    old_sha = str(operation["old_genesis_sha256"])
    new_raw = canonical_json(operation["genesis"])

    # If this is a fresh rollout, require enough future space to stop, stage,
    # and restart the entire accepted chain before the first changed block.
    if current_block >= effective_transition - stale_margin:
        # A completed/partial resume is still safe if every node already carries
        # the new config.  Probe below before deciding this is stale.
        pass

    print(f"NATIVE_MINT_PROGRESS: probe {len(all_targets)} accepted chain node genesis files", file=sys.stderr, flush=True)
    probe_results: list[dict[str, Any]] = []
    for index, target in enumerate(all_targets, 1):
        marker = _run_helper(private=private, network=network, controller_id=target["controller_id"], parent_service_uuid=target["service_uuid"], mode="probe", expected_old_sha=old_sha, new_sha=new_sha)
        observed = str(marker.get("before_sha256") or "")
        if observed not in {old_sha, new_sha}:
            raise _fail("NATIVE_MINT_REMOTE_GENESIS_MISMATCH", f"{target['node']} genesis is {observed}, expected old/new native-mint hash")
        probe_results.append({"node": target["node"], "sha256": observed})
        print(f"NATIVE_MINT_PROGRESS: probe {index}/{len(all_targets)} node={target['node']} sha={observed[:12]}", file=sys.stderr, flush=True)

    all_new = all(item["sha256"] == new_sha for item in probe_results)
    if not all_new and current_block >= effective_transition - stale_margin:
        if _can_auto_refresh_stale(operation, probe_results, old_sha, live_rpc_available=live_rpc_available):
            _refresh_stale_prepared_operation(runtime_root, operation)
            return _execute(args)
        raise _fail(
            "NATIVE_MINT_PREP_STALE",
            f"current block {current_block} is too close to transition {effective_transition}; rollout state is not safe for automatic re-prep",
        )

    if not all_new:
        if not recovering_rollout:
            operation["status"] = "rolling-out"
            operation["rollout_started_block"] = current_block
            operation["rollout_started_at"] = _utc_now()
            _write_json(path, operation)
        print("NATIVE_MINT_PROGRESS: stopping all accepted chain-node services before genesis transition rollout", file=sys.stderr, flush=True)
        for target in all_targets:
            _service_control(private, network=network, target=target, action="stop")
        for target in all_targets:
            _wait_service_stopped(private, network=network, target=target)
        print("NATIVE_MINT_PROGRESS: all accepted chain-node services stopped", file=sys.stderr, flush=True)
        print("NATIVE_MINT_PROGRESS: writing identical QBFT transition genesis to every accepted chain node", file=sys.stderr, flush=True)
        for index, target in enumerate(all_targets, 1):
            _run_helper(private=private, network=network, controller_id=target["controller_id"], parent_service_uuid=target["service_uuid"], mode="write", expected_old_sha=old_sha, new_genesis=new_raw, new_sha=new_sha)
            print(f"NATIVE_MINT_PROGRESS: write {index}/{len(all_targets)} node={target['node']} verified", file=sys.stderr, flush=True)
        operation["status"] = "genesis-staged"
        operation["genesis_staged_at"] = _utc_now()
        _write_json(path, operation)

    # If a previous attempt failed after all config volumes were staged, the
    # nodes may still be stopped.  Starting an already-running Coolify service
    # is tolerated by _service_control, so this is deliberately idempotent.
    print("NATIVE_MINT_PROGRESS: starting accepted chain-node services", file=sys.stderr, flush=True)
    start_errors: list[str] = []
    for target in all_targets:
        try:
            _service_control(private, network=network, target=target, action="start")
        except Exception as exc:
            start_errors.append(f"{target['node']}: {exc}")
    if start_errors:
        raise _fail("NATIVE_MINT_SERVICE_CONTROL_FAILED", "one or more chain nodes failed to start: " + "; ".join(start_errors))
    for target in all_targets:
        _wait_service_running(private, network=network, target=target)

    resumed_block = _wait_rpc(rpc_url, chain_id)
    if resumed_block >= effective_transition and operation["mode"] in {"mint", "open"} and not all_new:
        raise _fail("NATIVE_MINT_ACTIVATION_RACE", f"chain resumed at block {resumed_block}, not before activation block {effective_transition}")

    # Verify every config volume now carries the new exact genesis bytes.
    for target in all_targets:
        marker = _run_helper(private=private, network=network, controller_id=target["controller_id"], parent_service_uuid=target["service_uuid"], mode="probe", expected_old_sha=old_sha, new_sha=new_sha)
        if marker.get("before_sha256") != new_sha:
            raise _fail("NATIVE_MINT_REMOTE_GENESIS_MISMATCH", f"{target['node']} did not retain new genesis after restart")

    baseline_path = Path(str(operation["baseline"]["path"]))
    baseline_doc = json.loads(baseline_path.read_text(encoding="utf-8"))
    topology_evidence = _write_topology_evidence(runtime_root, operation=operation, baseline_path=baseline_path, baseline_doc=baseline_doc, new_genesis=operation["genesis"], new_sha=new_sha)

    proof: dict[str, Any] = {
        "config_rollout_verified": True,
        "resumed_block": resumed_block,
        "node_count": len(all_targets),
        "new_genesis_sha256": new_sha,
        "topology_evidence_path": str(topology_evidence),
        "topology_evidence_sha256": _sha_file(topology_evidence),
    }

    if operation["mode"] == "mint":
        recipient = _address(operation["recipient"], "recipient")
        print(f"NATIVE_MINT_PROGRESS: waiting for one-block mint pulse at block {activation}", file=sys.stderr, flush=True)
        latest_before_wait = _latest_block(rpc_url)
        remaining_blocks = max(0, expiration - latest_before_wait)
        block_period = max(1, int(operation.get("block_period_seconds") or 1))
        proof_wait = max(DEFAULT_RPC_WAIT_SECONDS, remaining_blocks * block_period * 3 + 120)
        _wait_block(rpc_url, expiration, wait_seconds=proof_wait)
        before = _balance(rpc_url, recipient, activation - 1)
        after = _balance(rpc_url, recipient, activation)
        delta = after - before
        expected = int(operation["wei_per_block"])
        if delta != expected:
            raise _fail("NATIVE_MINT_PROOF_FAILED", f"recipient balance delta at mint block was {delta}, expected exactly {expected}")
        proof.update({
            "mint_verified": True,
            "mint_block": activation,
            "recipient": recipient,
            "balance_before_wei": before,
            "balance_after_wei": after,
            "minted_wei": delta,
            "automatic_off_block": expiration,
            "off_block_reached": True,
        })
    elif operation["mode"] == "open":
        proof.update({
            "window_staged": True,
            "activation_block": activation,
            "expiration_block": expiration,
            "automatic_off_transition_present": True,
        })
    elif operation["mode"] == "close":
        close_block = int(operation["window"].get("manual_close_block") or expiration)
        latest_before_wait = _latest_block(rpc_url)
        remaining_blocks = max(0, close_block - latest_before_wait)
        block_period = max(1, int(operation.get("block_period_seconds") or 1))
        close_wait = max(DEFAULT_RPC_WAIT_SECONDS, remaining_blocks * block_period * 3 + 120)
        _wait_block(rpc_url, close_block, wait_seconds=close_wait)
        proof.update({"close_verified": True, "close_block": close_block})

    operation["status"] = "applied"
    operation["applied_at"] = _utc_now()
    operation["topology_evidence"] = {"path": str(topology_evidence), "sha256": _sha_file(topology_evidence)}
    operation["proof"] = proof
    _write_json(path, operation)
    return {"ok": True, "status": "applied", "operation_id": operation_id, "mode": operation["mode"], "proof": proof}


def _inspect(args: argparse.Namespace) -> dict[str, Any]:
    network = _identifier(args.network, "network")
    operation_id = getattr(args, "operation_id", None)
    op = _operation_identity("inspect", network, operation_id)
    _, _private, private_doc = _load_private(Path(args.runtime_state_root), op)
    network_state = _network_state(private_doc, network)
    rpc_url = _rpc_url(network_state)
    chain_id = _positive_int(network_state.get("chain_id"), "chain_id")
    observed_chain = _rpc_int(rpc_url, "eth_chainId")
    block = _latest_block(rpc_url)
    validators = _validators(rpc_url)
    baseline_path, baseline_doc = _latest_baseline(Path(args.runtime_state_root))
    topology = _extract_topology(baseline_doc)
    assert topology is not None
    baseline_chain, genesis_sha = _chain_identity(baseline_doc, topology)
    active = _active_operation(Path(args.runtime_state_root), network)
    active_summary = None
    if active is not None:
        activation = int(active["window"]["activation_block"])
        expiration = int(active["window"]["expiration_block"])
        manual_close = active["window"].get("manual_close_block")
        effective_expiration = min(expiration, int(manual_close)) if manual_close is not None else expiration
        state = "pending" if block < activation else "active" if block < effective_expiration else "closed"
        active_summary = {
            "operation_id": active["operation_id"],
            "mode": active["mode"],
            "recipient": active.get("recipient"),
            "wei_per_block": active.get("wei_per_block"),
            "activation_block": activation,
            "expiration_block": expiration,
            "effective_expiration_block": effective_expiration,
            "state": state,
        }
    return {
        "ok": observed_chain == chain_id == baseline_chain,
        "status": "observed",
        "network": network,
        "chain_id": observed_chain,
        "block_number": block,
        "validator_set": validators,
        "genesis_sha256": genesis_sha,
        "baseline_evidence": str(baseline_path),
        "active_window": active_summary,
    }


def _operation_inspect(args: argparse.Namespace) -> dict[str, Any]:
    runtime_root = Path(args.runtime_state_root)
    _path, operation = _load_operation(runtime_root, args.operation_id)
    rpc_url = str(operation["rpc_url"])
    block = _latest_block(rpc_url)
    proof = operation.get("proof") if isinstance(operation.get("proof"), Mapping) else {}
    mode = operation["mode"]
    verified = bool(proof.get("config_rollout_verified")) and operation.get("status") in {"applied", "finalized"}
    if mode == "mint":
        verified = verified and proof.get("mint_verified") is True and proof.get("off_block_reached") is True
    elif mode == "open":
        verified = verified and proof.get("automatic_off_transition_present") is True
    elif mode == "close":
        verified = verified and proof.get("close_verified") is True
    return {"ok": verified, "status": "verified" if verified else "unverified", "operation_id": operation["operation_id"], "mode": mode, "block_number": block, "proof": proof}


def _finalize(args: argparse.Namespace) -> dict[str, Any]:
    runtime_root = Path(args.runtime_state_root)
    path, operation = _load_operation(runtime_root, args.operation_id)
    inspected = _operation_inspect(argparse.Namespace(runtime_state_root=str(runtime_root), operation_id=args.operation_id))
    if inspected.get("ok") is not True:
        raise _fail("NATIVE_MINT_FINALIZE_UNVERIFIED", "native-mint operation proof is not verified")
    operation["status"] = "finalized"
    operation["finalized_at"] = _utc_now()
    _write_json(path, operation)
    evidence = {
        "kind": EVIDENCE_KIND,
        "schema_version": 1,
        "status": "pass",
        "completed_at": operation["finalized_at"],
        "network": operation["network"],
        "chain_id": operation["chain_id"],
        "operation_id": operation["operation_id"],
        "mode": operation["mode"],
        "recipient": operation.get("recipient"),
        "wei_per_block": operation.get("wei_per_block"),
        "blocks": operation.get("blocks"),
        "max_mint_wei": operation.get("max_mint_wei"),
        "window": operation.get("window"),
        "old_genesis_sha256": operation["old_genesis_sha256"],
        "new_genesis_sha256": operation["new_genesis_sha256"],
        "proof": operation.get("proof"),
        "topology_evidence": operation.get("topology_evidence"),
    }
    out = runtime_root / "mother" / "evidence" / OPERATION_EVIDENCE_DIRECTORY / f"{_stamp()}-{operation['operation_id']}.json"
    _write_json(out, evidence)
    return {"ok": True, "status": "finalized", "operation_id": operation["operation_id"], "mode": operation["mode"], "evidence_path": str(out), "evidence_sha256": _sha_file(out)}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-state-root", default=str(REPO_ROOT / "runtime" / "state"))
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    inspect = sub.add_parser("inspect")
    inspect.add_argument("network")
    inspect.add_argument("--operation-id")
    inspect.set_defaults(handler=_inspect)

    prep = sub.add_parser("prep")
    prep.add_argument("mode", choices=("mint", "open", "close"))
    prep.add_argument("network")
    prep.add_argument("--operation-id")
    prep.add_argument("--to")
    prep.add_argument("--amount-wei")
    prep.add_argument("--amount-native")
    prep.add_argument("--wei-per-block")
    prep.add_argument("--native-per-block")
    prep.add_argument("--blocks", type=int, default=2)
    prep.add_argument("--activation-lead-blocks", type=int, default=DEFAULT_ACTIVATION_LEAD_BLOCKS)
    prep.add_argument("--close-lead-blocks", type=int, default=DEFAULT_CLOSE_LEAD_BLOCKS)
    prep.set_defaults(handler=_prepare)

    do = sub.add_parser("do")
    do.add_argument("--operation-id", required=True)
    do.set_defaults(handler=_execute)

    op_inspect = sub.add_parser("operation-inspect")
    op_inspect.add_argument("--operation-id", required=True)
    op_inspect.set_defaults(handler=_operation_inspect)

    finalize = sub.add_parser("finalize")
    finalize.add_argument("--operation-id", required=True)
    finalize.set_defaults(handler=_finalize)

    cleanup = sub.add_parser("cleanup")
    cleanup.add_argument("network")
    cleanup.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    cleanup.add_argument("--dry-run", action="store_true")
    cleanup.set_defaults(handler=_cleanup_helpers)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = args.handler(args)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")) if args.json else json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("ok") is True else 1
    except NativeMintError as exc:
        payload = {"ok": False, "code": exc.code, "message": exc.message}
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")) if args.json else f"{exc.code}: {exc.message}")
        return 2
    except MotherError as exc:
        payload = {"ok": False, "code": exc.code, "message": exc.message}
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")) if args.json else f"{exc.code}: {exc.message}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
