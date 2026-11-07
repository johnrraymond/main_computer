#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_STATE_FILE = Path("runtime/deployments/dev/latest.json")
LEGACY_STATE_FILE = Path("runtime/dev-chain/latest.json")
DEFAULT_REPORT_PATH = Path("runtime/hub/worker_local_chain_settlement_execution_smoke.json")
DEFAULT_RPC_URL = "http://127.0.0.1:18545"
DEFAULT_CHAIN_ID = 31337
EXECUTED_STATE = 4
MIN_LOCAL_SENDER_GAS_BALANCE_WEI = 10**17
TARGET_LOCAL_SENDER_GAS_BALANCE_WEI = 10_000 * 10**18

SELECTORS = {
    "XLagBridgeReserve.nextProposalId()": "2ab09d14",
    "XLagBridgeReserve.payoutDelayBlocks()": "5a8d8e42",
    "XLagBridgeReserve.proposalState(uint256)": "d26331d4",
    "XLagBridgeReserve.proposePayout(address,uint256,string,uint64)": "707216b1",
    "XLagBridgeReserve.secondPayout(uint256)": "a74928e3",
    "XLagBridgeReserve.executePayout(uint256)": "21814f90",
}

DEFAULT_ANVIL_OFFICES = [
    "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266",
    "0x70997970c51812dc3a010c7d01b50e0d17dc79c8",
    "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc",
    "0x90f79bf6eb2c4f870365e785982e1f101e93b906",
]


def clean_scope(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value or "").strip().lower()).strip("-")
    return text or f"local-{int(time.time())}"


def find_repo_root(start: Path) -> Path:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (
            (candidate / "new_patch.py").exists()
            or (candidate / "pyproject.toml").exists()
            or (candidate / "contracts").is_dir()
            or (candidate / ".git").exists()
        ):
            return candidate
    return current


def add_step(steps: list[dict[str, Any]], name: str, payload: dict[str, Any]) -> dict[str, Any]:
    step = {"name": name, "ok": bool(payload.get("ok", True)), "payload": payload}
    steps.append(step)
    return payload


def add_note(steps: list[dict[str, Any]], name: str, *, ok: bool = True, **payload: Any) -> dict[str, Any]:
    return add_step(steps, name, {"ok": ok, **payload})


def assert_equal(name: str, actual: object, expected: object) -> None:
    if actual != expected:
        raise AssertionError(f"{name}: expected {expected!r}, got {actual!r}")


def assert_not_contains(name: str, payload: object, needle: int) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if str(needle) in rendered:
        raise AssertionError(f"{name}: privacy-safe payload leaked exact amount {needle}")


def assert_contains(name: str, payload: object, needle: int) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if str(needle) not in rendered:
        raise AssertionError(f"{name}: expected audit payload to contain exact amount {needle}")


def rounded_down(value: int, precision_places: int) -> tuple[int, int, int]:
    precision = max(0, min(6, int(precision_places)))
    bucket = 10 ** (6 - precision)
    published = (max(0, int(value)) // bucket) * bucket
    return published, max(0, int(value)) - published, bucket


def post_json(url: str, payload: dict[str, Any], *, timeout: float = 10.0, allow_error: bool = False) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = {"error": body}
        data["_http_status"] = exc.code
        if not allow_error:
            raise RuntimeError(f"POST {url} failed with HTTP {exc.code}: {data}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"POST {url} returned a non-object response.")
    if data.get("error") and not allow_error:
        raise RuntimeError(f"POST {url} failed: {data['error']}")
    return data


def get_json(url: str, *, timeout: float = 10.0, allow_error: bool = False) -> dict[str, Any]:
    try:
        with urlopen(url, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = {"error": body}
        data["_http_status"] = exc.code
        if not allow_error:
            raise RuntimeError(f"GET {url} failed with HTTP {exc.code}: {data}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"GET {url} returned a non-object response.")
    if data.get("error") and not allow_error:
        raise RuntimeError(f"GET {url} failed: {data['error']}")
    return data


def normalize_address(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("expected Ethereum-style address, got an empty value")
    if not raw.startswith("0x"):
        raw = "0x" + raw
    if len(raw) != 42:
        raise ValueError(f"expected Ethereum-style address, got {value!r}")
    int(raw[2:], 16)
    return raw.lower()


def normalize_tx_hash(value: str) -> str:
    raw = str(value or "").strip()
    if not re.fullmatch(r"0x[0-9a-fA-F]{64}", raw):
        raise ValueError(f"expected Ethereum-style transaction hash, got {value!r}")
    return raw.lower()


def hex_quantity(value: int) -> str:
    if value < 0:
        raise ValueError("hex quantity cannot be negative")
    return hex(int(value))


def abi_uint(value: int) -> str:
    if value < 0:
        raise ValueError("ABI uint cannot be negative")
    return int(value).to_bytes(32, "big").hex()


def abi_address(value: str) -> str:
    return ("0" * 24) + normalize_address(value)[2:]


def abi_string_tail(value: str) -> str:
    raw = value.encode("utf-8")
    hex_text = raw.hex()
    padded_len = ((len(hex_text) + 63) // 64) * 64
    return abi_uint(len(raw)) + hex_text.ljust(padded_len, "0")


def abi_encode_address_uint_string_uint64(address: str, amount: int, memo: str, expires_block: int) -> str:
    head_words = 4
    string_offset = head_words * 32
    return "".join(
        [
            abi_address(address),
            abi_uint(amount),
            abi_uint(string_offset),
            abi_uint(expires_block),
            abi_string_tail(memo),
        ]
    )


def call_data(selector: str, *encoded_args: str) -> str:
    return "0x" + selector + "".join(encoded_args)


def propose_payout_data(recipient: str, amount_units: int, memo: str, expires_block: int) -> str:
    return call_data(
        SELECTORS["XLagBridgeReserve.proposePayout(address,uint256,string,uint64)"],
        abi_encode_address_uint_string_uint64(recipient, amount_units, memo, expires_block),
    )


def uint_call_data(selector_key: str, value: int) -> str:
    return call_data(SELECTORS[selector_key], abi_uint(value))


def word_at(hex_result: str, index: int = 0) -> str:
    clean = str(hex_result).removeprefix("0x")
    start = index * 64
    word = clean[start : start + 64]
    if len(word) != 64:
        raise ValueError(f"expected ABI word at index {index}, got {hex_result!r}")
    return word


def decode_uint(hex_result: str, index: int = 0) -> int:
    return int(word_at(hex_result, index), 16)


def address_from_topic(topic: str) -> str:
    clean = str(topic or "").removeprefix("0x").lower()
    if len(clean) != 64:
        raise ValueError(f"expected indexed address topic, got {topic!r}")
    return normalize_address("0x" + clean[-40:])


def load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def resolve_state_file(root: Path, requested: Path) -> Path | None:
    candidates = resolve_state_file_candidates(root, requested)
    return candidates[0] if candidates else None


def resolve_state_file_candidates(root: Path, requested: Path) -> list[Path]:
    candidates: list[Path] = []
    path = requested if requested.is_absolute() else root / requested
    if path.exists():
        candidates.append(path)

    if requested == DEFAULT_STATE_FILE:
        for fallback in (
            root / "runtime" / "deployments" / "dev" / "latest.json",
            root / LEGACY_STATE_FILE,
        ):
            if fallback.exists() and fallback not in candidates:
                candidates.append(fallback)
    return candidates


def load_state(path: Path | None) -> tuple[dict[str, Any], dict[str, str]]:
    if path is None:
        return {}, {}
    state = json.loads(path.read_text(encoding="utf-8"))
    env = load_env_file(path.with_suffix(".env"))
    return state, env


def contract_address_from_state_env(state: dict[str, Any], env: dict[str, str]) -> str | None:
    value = os.getenv("PHASE8_CONTRACT_ADDRESS") or env.get("MAIN_COMPUTER_XLAG_CONTRACT_ADDRESS") or deployment_address(state)
    if not value:
        return None
    return normalize_address(str(value))


def select_state(root: Path, requested: Path, contract_override: str | None = None) -> tuple[Path | None, dict[str, Any], dict[str, str], list[str]]:
    candidates = resolve_state_file_candidates(root, requested)
    loaded: list[tuple[Path, dict[str, Any], dict[str, str]]] = []
    for path in candidates:
        state, env = load_state(path)
        loaded.append((path, state, env))
        if contract_override or contract_address_from_state_env(state, env):
            return path, state, env, [str(candidate) for candidate in candidates]

    if loaded:
        path, state, env = loaded[0]
        return path, state, env, [str(candidate) for candidate in candidates]
    return None, {}, {}, []


def state_chain(state: dict[str, Any]) -> dict[str, Any]:
    value = state.get("chain")
    return value if isinstance(value, dict) else {}


def deployment_collections_from_state(state: dict[str, Any]) -> list[dict[str, Any]]:
    collections: list[dict[str, Any]] = []
    for key in ("deployments", "contracts"):
        value = state.get(key)
        if isinstance(value, dict):
            collections.append(value)

    nested = state.get("deployment")
    if isinstance(nested, dict):
        for key in ("deployments", "contracts"):
            value = nested.get(key)
            if isinstance(value, dict):
                collections.append(value)

    return collections


def deployments_from_state(state: dict[str, Any]) -> dict[str, Any]:
    collections = deployment_collections_from_state(state)
    return collections[0] if collections else {}


def first_address(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return normalize_address(value)
    return None


def deployment_address(state: dict[str, Any]) -> str | None:
    preferred_keys = ("xlag-bridge-reserve", "XLagBridgeReserve", "xlag")
    for deployments in deployment_collections_from_state(state):
        for key in preferred_keys:
            value = deployments.get(key)
            if isinstance(value, dict):
                address = first_address(value.get("address"), value.get("deployedTo"), value.get("deployed_to"))
                if address:
                    return address
            elif isinstance(value, str):
                return normalize_address(value)

        for key, value in deployments.items():
            key_text = str(key).lower()
            target = str(value.get("target", "") if isinstance(value, dict) else "").lower()
            if "xlagbridgereserve" not in key_text and "xlag-bridge-reserve" not in key_text and "xlagbridgereserve" not in target:
                continue
            if isinstance(value, dict):
                address = first_address(value.get("address"), value.get("deployedTo"), value.get("deployed_to"))
                if address:
                    return address
            elif isinstance(value, str):
                return normalize_address(value)
    return None


def offices_from_state(state: dict[str, Any], env: dict[str, str]) -> list[str]:
    env_offices = [env.get(f"MAIN_COMPUTER_DEV_OFFICE_{index}_ADDRESS", "") for index in range(4)]
    if all(env_offices):
        return [normalize_address(item) for item in env_offices]

    offices = state.get("offices")
    if isinstance(offices, list):
        addresses = []
        for item in offices:
            if isinstance(item, dict) and item.get("address"):
                addresses.append(normalize_address(str(item["address"])))
            elif isinstance(item, str):
                addresses.append(normalize_address(item))
        if len(addresses) >= 4:
            return addresses[:4]

    chain = state_chain(state)
    chain_offices = chain.get("offices")
    if isinstance(chain_offices, list):
        addresses = []
        for item in chain_offices:
            if isinstance(item, dict) and item.get("address"):
                addresses.append(normalize_address(str(item["address"])))
            elif isinstance(item, str):
                addresses.append(normalize_address(item))
        if len(addresses) >= 4:
            return addresses[:4]

    return list(DEFAULT_ANVIL_OFFICES)


def resolve_rpc_url(state: dict[str, Any], env: dict[str, str], override: str | None) -> str:
    if override:
        return override
    if os.getenv("PHASE8_RPC_URL"):
        return str(os.environ["PHASE8_RPC_URL"])
    chain = state_chain(state)
    for key in ("host_rpc_url", "rpc_url"):
        if chain.get(key):
            return str(chain[key])
    return env.get("MAIN_COMPUTER_ENERGY_CHAIN_RPC_URL") or DEFAULT_RPC_URL


def resolve_chain_id(state: dict[str, Any], env: dict[str, str], override: int | None) -> int:
    if override is not None:
        return int(override)
    if os.getenv("PHASE8_CHAIN_ID"):
        return int(os.environ["PHASE8_CHAIN_ID"], 0)
    chain = state_chain(state)
    if chain.get("chain_id") is not None:
        return int(chain["chain_id"])
    if env.get("MAIN_COMPUTER_XLAG_CHAIN_ID"):
        return int(env["MAIN_COMPUTER_XLAG_CHAIN_ID"], 0)
    if env.get("MAIN_COMPUTER_ENERGY_CHAIN_ID"):
        return int(env["MAIN_COMPUTER_ENERGY_CHAIN_ID"], 0)
    return DEFAULT_CHAIN_ID


def resolve_contract_address(state: dict[str, Any], env: dict[str, str], override: str | None) -> str:
    value = override or contract_address_from_state_env(state, env)
    if not value:
        raise RuntimeError(
            "No XLagBridgeReserve contract address was found. Run a local dev-chain deployment first "
            "(for example: python tools/dev-chain-reset.py --yes) or pass --contract-address."
        )
    return normalize_address(str(value))


def resolve_worker_payout_address(args: argparse.Namespace, offices: list[str]) -> str:
    value = args.worker_payout_address or os.getenv("PHASE8_WORKER_PAYOUT_ADDRESS")
    if value:
        return normalize_address(value)
    return normalize_address(offices[3])


def rpc(url: str, method: str, params: list[Any] | None = None, *, timeout: float = 10.0) -> Any:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}).encode("utf-8")
    request = Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    if "error" in data:
        raise RuntimeError(f"{method} RPC error: {data['error']}")
    return data.get("result")


def eth_call(url: str, to: str, data: str, *, timeout: float) -> str:
    return str(rpc(url, "eth_call", [{"to": normalize_address(to), "data": data}, "latest"], timeout=timeout))


def call_uint(url: str, to: str, selector_key: str, *args: str, timeout: float) -> int:
    return decode_uint(eth_call(url, to, call_data(SELECTORS[selector_key], *args), timeout=timeout))


def get_balance(url: str, address: str, *, timeout: float) -> int:
    return int(str(rpc(url, "eth_getBalance", [normalize_address(address), "latest"], timeout=timeout)), 16)


def set_local_dev_balance(url: str, address: str, balance_wei: int, *, timeout: float) -> dict[str, Any]:
    """Set a local dev-chain account balance through common local-node RPCs.

    This is intentionally scoped to the local settlement smoke path.  It repairs
    exhausted deterministic dev accounts without resetting the already-running
    chain or redeploying contracts.
    """

    clean_address = normalize_address(address)
    target = hex_quantity(max(0, int(balance_wei)))
    errors: dict[str, str] = {}
    for method in ("anvil_setBalance", "hardhat_setBalance", "evm_setAccountBalance"):
        try:
            rpc(url, method, [clean_address, target], timeout=timeout)
            return {
                "ok": True,
                "method": method,
                "address": clean_address,
                "target_balance_wei": int(balance_wei),
            }
        except Exception as exc:
            errors[method] = str(exc)
    return {
        "ok": False,
        "address": clean_address,
        "target_balance_wei": int(balance_wei),
        "errors": errors,
    }


def ensure_local_sender_gas_balance(
    url: str,
    address: str,
    *,
    timeout: float,
    minimum_balance_wei: int = MIN_LOCAL_SENDER_GAS_BALANCE_WEI,
    target_balance_wei: int = TARGET_LOCAL_SENDER_GAS_BALANCE_WEI,
) -> dict[str, Any]:
    clean_address = normalize_address(address)
    before = get_balance(url, clean_address, timeout=timeout)
    minimum = max(0, int(minimum_balance_wei))
    target = max(minimum, int(target_balance_wei))
    if before >= target:
        return {
            "ok": True,
            "address": clean_address,
            "refilled": False,
            "balance_before_wei": before,
            "balance_after_wei": before,
            "minimum_balance_wei": minimum,
            "target_balance_wei": target,
        }

    refill = set_local_dev_balance(url, clean_address, target, timeout=timeout)
    after = get_balance(url, clean_address, timeout=timeout)
    if after < target:
        details = refill.get("errors") if isinstance(refill, dict) else {}
        raise RuntimeError(
            "Local dev-chain sender has insufficient gas balance and could not be refilled "
            f"through local-node balance RPCs: address={clean_address}, balance={before}, "
            f"minimum={minimum}, after_refill={after}, errors={details}. "
            "Reset/redeploy the local dev chain with `python tools/dev-chain-reset.py --yes`, "
            "then restart the smoke exp Hub cluster."
        )

    return {
        "ok": True,
        "address": clean_address,
        "refilled": True,
        "balance_before_wei": before,
        "balance_after_wei": after,
        "minimum_balance_wei": minimum,
        "target_balance_wei": target,
        "refill": refill,
    }


def get_code(url: str, address: str, *, timeout: float) -> str:
    return str(rpc(url, "eth_getCode", [normalize_address(address), "latest"], timeout=timeout))


def send_transaction(url: str, tx: dict[str, Any], *, timeout: float) -> str:
    payload = dict(tx)
    sender = ""
    value = 0
    if "from" in payload:
        sender = normalize_address(str(payload["from"]))
        payload["from"] = sender
    if "to" in payload and payload["to"]:
        payload["to"] = normalize_address(str(payload["to"]))
    if payload.get("value") is not None:
        try:
            value = int(str(payload.get("value")), 16)
        except (TypeError, ValueError):
            value = 0
    try:
        return normalize_tx_hash(str(rpc(url, "eth_sendTransaction", [payload], timeout=timeout)))
    except RuntimeError as exc:
        if sender and "insufficient funds" in str(exc).lower():
            try:
                balance = get_balance(url, sender, timeout=timeout)
            except Exception as balance_exc:  # pragma: no cover - diagnostic fallback
                balance = f"unavailable: {balance_exc}"
            raise RuntimeError(
                "eth_sendTransaction failed with insufficient funds after local sender gas preflight: "
                f"from={sender}, value_wei={value}, balance_wei={balance}, tx={payload}. "
                "For a local dev chain, rerun `python tools/dev-chain-reset.py --yes`, "
                "then restart the smoke exp Hub cluster."
            ) from exc
        raise


def wait_receipt(url: str, tx_hash: str, *, timeout: float, poll_s: float) -> dict[str, Any]:
    clean_hash = normalize_tx_hash(tx_hash)
    deadline = time.monotonic() + timeout
    while time.monotonic() <= deadline:
        receipt = rpc(url, "eth_getTransactionReceipt", [clean_hash], timeout=timeout)
        if receipt:
            if not isinstance(receipt, dict):
                raise RuntimeError(f"eth_getTransactionReceipt returned a non-object for {clean_hash}: {receipt!r}")
            status = int(str(receipt.get("status", "0x0")), 16)
            if status != 1:
                raise RuntimeError(f"transaction failed: {clean_hash}: {receipt}")
            receipt.setdefault("transactionHash", clean_hash)
            return receipt
        time.sleep(poll_s)
    raise TimeoutError(f"timed out waiting for transaction receipt: {clean_hash}")


def mine_blocks(url: str, count: int, *, timeout: float) -> None:
    for _ in range(max(0, int(count))):
        rpc(url, "evm_mine", [], timeout=timeout)


def parse_payout_executed_event(
    receipt: dict[str, Any],
    *,
    contract_address: str,
    proposal_id: int,
    recipient_address: str,
    amount_units: int,
) -> dict[str, Any]:
    contract = normalize_address(contract_address)
    recipient = normalize_address(recipient_address)
    logs = receipt.get("logs") if isinstance(receipt.get("logs"), list) else []
    for log in logs:
        if not isinstance(log, dict):
            continue
        if normalize_address(str(log.get("address", ""))) != contract:
            continue
        topics = log.get("topics")
        if not isinstance(topics, list) or len(topics) < 3:
            continue
        try:
            event_proposal_id = int(str(topics[1]).removeprefix("0x"), 16)
            event_recipient = address_from_topic(str(topics[2]))
            event_amount = int(str(log.get("data", "0x0")).removeprefix("0x") or "0", 16)
        except Exception:
            continue
        if event_proposal_id == proposal_id and event_recipient == recipient and event_amount == amount_units:
            return {
                "ok": True,
                "event": "PayoutExecuted",
                "proposal_id": str(event_proposal_id),
                "recipient_address": event_recipient,
                "amount_units": event_amount,
                "log_index": int(str(log.get("logIndex", "0x0")), 16),
                "block_number": int(str(receipt.get("blockNumber", "0x0")), 16),
                "tx_hash": normalize_tx_hash(str(receipt.get("transactionHash", ""))),
                "contract_address": contract,
                "topic0": str(topics[0]) if topics else "",
            }
    raise RuntimeError(
        "executePayout receipt did not contain a matching PayoutExecuted event "
        f"for proposal={proposal_id}, recipient={recipient}, amount={amount_units}."
    )


def execute_local_chain_payout(
    *,
    rpc_url: str,
    expected_chain_id: int,
    contract_address: str,
    captain: str,
    beta_second: str,
    recipient_address: str,
    payout_units: int,
    fund_units: int,
    memo: str,
    expires_blocks: int,
    mine_extra_blocks: int,
    timeout: float,
    poll_s: float,
) -> dict[str, Any]:
    try:
        actual_chain_id = int(str(rpc(rpc_url, "eth_chainId", [], timeout=timeout)), 16)
        block_before = int(str(rpc(rpc_url, "eth_blockNumber", [], timeout=timeout)), 16)
    except Exception as exc:
        raise RuntimeError(
            f"Could not connect to local chain RPC at {rpc_url}. Start Anvil/dev-chain first, "
            "then pass --rpc-url and --contract-address if they differ from the deployment state."
        ) from exc

    if actual_chain_id != int(expected_chain_id):
        raise RuntimeError(f"Local chain id mismatch: expected {expected_chain_id}, got {actual_chain_id}.")

    code = get_code(rpc_url, contract_address, timeout=timeout)
    if not code or code == "0x":
        raise RuntimeError(f"XLagBridgeReserve contract address has no code: {contract_address}")

    contract = normalize_address(contract_address)
    captain = normalize_address(captain)
    beta_second = normalize_address(beta_second)
    recipient = normalize_address(recipient_address)
    payout_units = int(payout_units)
    fund_units = int(fund_units)

    sender_gas_topups: list[dict[str, Any]] = []
    seen_senders: set[str] = set()
    for sender in (captain, beta_second):
        if sender in seen_senders:
            continue
        seen_senders.add(sender)
        sender_gas_topups.append(ensure_local_sender_gas_balance(rpc_url, sender, timeout=timeout))

    next_id_before = call_uint(rpc_url, contract, "XLagBridgeReserve.nextProposalId()", timeout=timeout)
    payout_delay = call_uint(rpc_url, contract, "XLagBridgeReserve.payoutDelayBlocks()", timeout=timeout)
    reserve_before = get_balance(rpc_url, contract, timeout=timeout)
    recipient_before = get_balance(rpc_url, recipient, timeout=timeout)

    fund_tx_hash = ""
    fund_receipt: dict[str, Any] | None = None
    if fund_units > 0:
        fund_tx_hash = send_transaction(
            rpc_url,
            {"from": captain, "to": contract, "value": hex_quantity(fund_units)},
            timeout=timeout,
        )
        fund_receipt = wait_receipt(rpc_url, fund_tx_hash, timeout=timeout, poll_s=poll_s)

    current_block = int(str(rpc(rpc_url, "eth_blockNumber", [], timeout=timeout)), 16)
    expires_block = current_block + max(2, int(expires_blocks))
    proposal_id = next_id_before

    propose_tx_hash = send_transaction(
        rpc_url,
        {
            "from": captain,
            "to": contract,
            "data": propose_payout_data(recipient, payout_units, memo, expires_block),
        },
        timeout=timeout,
    )
    propose_receipt = wait_receipt(rpc_url, propose_tx_hash, timeout=timeout, poll_s=poll_s)

    next_id_after = call_uint(rpc_url, contract, "XLagBridgeReserve.nextProposalId()", timeout=timeout)
    if next_id_after != proposal_id + 1:
        raise RuntimeError(f"proposal id did not advance as expected: before={proposal_id}, after={next_id_after}")

    second_tx_hash = send_transaction(
        rpc_url,
        {
            "from": beta_second,
            "to": contract,
            "data": uint_call_data("XLagBridgeReserve.secondPayout(uint256)", proposal_id),
        },
        timeout=timeout,
    )
    second_receipt = wait_receipt(rpc_url, second_tx_hash, timeout=timeout, poll_s=poll_s)

    mined_blocks = int(payout_delay) + int(mine_extra_blocks)
    mine_blocks(rpc_url, mined_blocks, timeout=timeout)

    execute_tx_hash = send_transaction(
        rpc_url,
        {
            "from": captain,
            "to": contract,
            "data": uint_call_data("XLagBridgeReserve.executePayout(uint256)", proposal_id),
        },
        timeout=timeout,
    )
    execute_receipt = wait_receipt(rpc_url, execute_tx_hash, timeout=timeout, poll_s=poll_s)
    event = parse_payout_executed_event(
        execute_receipt,
        contract_address=contract,
        proposal_id=proposal_id,
        recipient_address=recipient,
        amount_units=payout_units,
    )

    proposal_state = call_uint(
        rpc_url,
        contract,
        "XLagBridgeReserve.proposalState(uint256)",
        abi_uint(proposal_id),
        timeout=timeout,
    )
    if proposal_state != EXECUTED_STATE:
        raise RuntimeError(f"proposal state after execute was {proposal_state}, expected {EXECUTED_STATE}")

    reserve_after = get_balance(rpc_url, contract, timeout=timeout)
    recipient_after = get_balance(rpc_url, recipient, timeout=timeout)
    recipient_delta = recipient_after - recipient_before
    if recipient_delta != payout_units:
        raise RuntimeError(f"recipient balance delta was {recipient_delta}, expected {payout_units}")

    return {
        "ok": True,
        "rpc_url": rpc_url,
        "chain_id": actual_chain_id,
        "block_before": block_before,
        "contract_address": contract,
        "captain": captain,
        "beta_second": beta_second,
        "recipient_address": recipient,
        "proposal_id": str(proposal_id),
        "payout_units": payout_units,
        "fund_units": fund_units,
        "payout_delay_blocks": payout_delay,
        "mined_blocks": mined_blocks,
        "reserve_balance_before_units": reserve_before,
        "reserve_balance_after_units": reserve_after,
        "recipient_balance_before_units": recipient_before,
        "recipient_balance_after_units": recipient_after,
        "recipient_delta_units": recipient_delta,
        "sender_gas_topups": sender_gas_topups,
        "transactions": {
            "fund": fund_tx_hash,
            "propose": propose_tx_hash,
            "second": second_tx_hash,
            "execute": execute_tx_hash,
        },
        "receipts": {
            "fund": fund_receipt,
            "propose": propose_receipt,
            "second": second_receipt,
            "execute": execute_receipt,
        },
        "payout_executed_event": event,
        "execute_tx_hash": execute_tx_hash,
        "settlement_tx_hash": execute_tx_hash,
        "tx_hash": execute_tx_hash,
        "block_number": event["block_number"],
        "log_index": event["log_index"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 8 local-chain worker settlement execution smoke.")
    parser.add_argument("--hub-url", default="http://127.0.0.1:8770", help="Running hub base URL.")
    parser.add_argument("--rpc-url", default=None, help="Local/dev chain RPC URL. Defaults to deployment state, PHASE8_RPC_URL, or 127.0.0.1:18545.")
    parser.add_argument("--chain-id", type=int, default=None, help="Expected local/dev chain id. Defaults to deployment state or 31337.")
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE, help="Deployment state with XLagBridgeReserve address/offices.")
    parser.add_argument("--contract-address", default="", help="Existing deployed XLagBridgeReserve address. Overrides deployment state.")
    parser.add_argument("--worker-payout-address", default="", help="Recipient address. Defaults to O3 from deployment state/default Anvil offices.")
    parser.add_argument("--captain-address", default="", help="Unlocked local-chain captain/O0 address. Defaults to deployment state/default Anvil O0.")
    parser.add_argument("--beta-second-address", default="", help="Unlocked local-chain second officer/O2 address. Defaults to deployment state/default Anvil O2.")
    parser.add_argument("--scope", default="", help="Fresh deterministic namespace for this smoke run.")
    parser.add_argument("--worker-index", type=int, default=0)
    parser.add_argument("--requester-index", type=int, default=0)
    parser.add_argument("--json", action="store_true", help="Print the full JSON report.")
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--credits", type=int, default=100_000_000)
    parser.add_argument("--max-credits", type=int, default=6_000_000)
    parser.add_argument("--worker-credits", type=int, default=5_500_123)
    parser.add_argument("--precision-places", type=int, default=3)
    parser.add_argument("--fund-units", type=int, default=None, help="Native dev-chain units to fund the reserve before payout. Defaults to rounded payout units.")
    parser.add_argument("--expires-blocks", type=int, default=100)
    parser.add_argument("--mine-extra-blocks", type=int, default=1)
    parser.add_argument("--timeout-s", type=float, default=20.0)
    parser.add_argument("--poll-s", type=float, default=0.25)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Retired: historical unauthenticated settlement HTTP APIs were deleted."""
    import sys
    print(
        'Retired: legacy Worker claim/settlement/proof/chain-execution HTTP '
        'mutations were deleted. No network, ledger, or chain activity performed.',
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
