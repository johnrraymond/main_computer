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


DEFAULT_STATE_FILE = Path("runtime/deployments/current.json")
LEGACY_STATE_FILE = Path("runtime/dev-chain/latest.json")
DEFAULT_REPORT_PATH = Path("runtime/hub/worker_local_chain_settlement_execution_smoke.json")
DEFAULT_RPC_URL = "http://127.0.0.1:8545"
DEFAULT_CHAIN_ID = 31337
EXECUTED_STATE = 4

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
    path = requested if requested.is_absolute() else root / requested
    if path.exists():
        return path
    if requested == DEFAULT_STATE_FILE:
        legacy = root / LEGACY_STATE_FILE
        if legacy.exists():
            return legacy
    return None


def load_state(path: Path | None) -> tuple[dict[str, Any], dict[str, str]]:
    if path is None:
        return {}, {}
    state = json.loads(path.read_text(encoding="utf-8"))
    env = load_env_file(path.with_suffix(".env"))
    return state, env


def state_chain(state: dict[str, Any]) -> dict[str, Any]:
    value = state.get("chain")
    return value if isinstance(value, dict) else {}


def deployments_from_state(state: dict[str, Any]) -> dict[str, Any]:
    deployments = state.get("deployments")
    if isinstance(deployments, dict):
        return deployments
    contracts = state.get("contracts")
    return contracts if isinstance(contracts, dict) else {}


def first_address(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return normalize_address(value)
    return None


def deployment_address(state: dict[str, Any]) -> str | None:
    deployments = deployments_from_state(state)
    for key in ("xlag-bridge-reserve", "XLagBridgeReserve", "xlag"):
        value = deployments.get(key)
        if isinstance(value, dict):
            address = first_address(value.get("address"), value.get("deployedTo"))
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
    value = override or os.getenv("PHASE8_CONTRACT_ADDRESS") or env.get("MAIN_COMPUTER_XLAG_CONTRACT_ADDRESS") or deployment_address(state)
    if not value:
        raise RuntimeError(
            "No XLagBridgeReserve contract address was found. Run a local dev-chain deployment first "
            "(for example: python tools/dev-chain-reset.py --yes) or pass --contract-address."
        )
    return normalize_address(value)


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


def get_code(url: str, address: str, *, timeout: float) -> str:
    return str(rpc(url, "eth_getCode", [normalize_address(address), "latest"], timeout=timeout))


def send_transaction(url: str, tx: dict[str, Any], *, timeout: float) -> str:
    payload = dict(tx)
    if "from" in payload:
        payload["from"] = normalize_address(str(payload["from"]))
    if "to" in payload and payload["to"]:
        payload["to"] = normalize_address(str(payload["to"]))
    return normalize_tx_hash(str(rpc(url, "eth_sendTransaction", [payload], timeout=timeout)))


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
        "settlement_tx_hash": execute_tx_hash,
        "block_number": event["block_number"],
        "log_index": event["log_index"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 8 local-chain worker settlement execution smoke.")
    parser.add_argument("--hub-url", default="http://127.0.0.1:8770", help="Running hub base URL.")
    parser.add_argument("--rpc-url", default=None, help="Local/dev chain RPC URL. Defaults to deployment state, PHASE8_RPC_URL, or 127.0.0.1:8545.")
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
    parser = build_parser()
    args = parser.parse_args(argv)

    root = find_repo_root(Path.cwd())
    state_path = resolve_state_file(root, args.state_file)
    state, env = load_state(state_path)
    hub_url = args.hub_url.rstrip("/")
    rpc_url = resolve_rpc_url(state, env, args.rpc_url)
    chain_id = resolve_chain_id(state, env, args.chain_id)
    offices = offices_from_state(state, env)
    contract_address = ""
    captain = normalize_address(args.captain_address) if args.captain_address else offices[0]
    beta_second = normalize_address(args.beta_second_address) if args.beta_second_address else offices[2]
    worker_payout_address = resolve_worker_payout_address(args, offices)

    scope = clean_scope(args.scope or f"local-phase8-chain-{int(time.time())}")
    requester = f"phase8-local-chain-requester-{args.requester_index}-{scope}"
    worker = f"paid-mock-worker-phase8-chain-{args.worker_index}-{scope}"
    request_key = f"phase8-local-chain-request-{scope}"
    claim_key = f"phase8-local-chain-claim-{scope}"
    batch_key = f"phase8-local-chain-batch-{scope}"
    receipt_key = f"phase8-local-chain-receipt-{scope}"
    exact_reject_key = f"phase8-local-chain-exact-reject-{scope}"

    expected_published, expected_dust, expected_bucket = rounded_down(args.worker_credits, args.precision_places)
    fund_units = int(args.fund_units if args.fund_units is not None else expected_published)
    report_path = args.report_path if args.report_path.is_absolute() else root / args.report_path

    steps: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "ok": False,
        "phase": "phase8-local-chain-worker-settlement-execution",
        "hub_url": hub_url,
        "scope": scope,
        "state_file": str(state_path) if state_path else None,
        "rpc_url": rpc_url,
        "chain_id": chain_id,
        "precision_places": args.precision_places,
        "rounding_bucket_credits": expected_bucket,
        "requester": {"account_id": requester, "self_contained_phase8": True},
        "worker": {"node_id": worker, "self_contained_phase8": True},
        "steps": steps,
    }

    try:
        contract_address = resolve_contract_address(state, env, args.contract_address)
        report["contract_address"] = contract_address
        report["worker_payout_address"] = worker_payout_address

        health = add_step(steps, "hub health", get_json(f"{hub_url}/api/hub/v1/health", timeout=5))
        assert_equal("hub health ok", health.get("ok"), True)

        actual_chain_id = int(str(rpc(rpc_url, "eth_chainId", [], timeout=args.timeout_s)), 16)
        add_note(
            steps,
            "local chain rpc reachable",
            chain_id=actual_chain_id,
            expected_chain_id=chain_id,
            rpc_url=rpc_url,
            contract_address=contract_address,
        )
        assert_equal("local chain id", actual_chain_id, chain_id)
        code = get_code(rpc_url, contract_address, timeout=args.timeout_s)
        if not code or code == "0x":
            raise AssertionError(f"XLagBridgeReserve has no code at {contract_address}")
        add_note(steps, "local reserve contract has code", contract_code_bytes=max(0, (len(code) - 2) // 2))

        issued = add_step(
            steps,
            "fund requester credits",
            post_json(
                f"{hub_url}/api/hub/v1/credits/admin/issue",
                {
                    "account_id": requester,
                    "credits": args.credits,
                    "memo": "phase8 local-chain settlement execution smoke funding",
                    "metadata": {"phase8_local_chain_settlement_execution": True, "scope": scope},
                },
            ),
        )
        assert_equal("fund requester ok", issued.get("ok"), True)

        registered = add_step(
            steps,
            "register phase8 local-chain worker",
            post_json(
                f"{hub_url}/api/hub/v1/workers/register",
                {
                    "node_id": worker,
                    "endpoint": "http://127.0.0.1:1",
                    "model": "mock-fast-chat",
                    "models": ["mock-fast-chat"],
                    "credits_per_request": args.worker_credits,
                    "settlement_precision_places": args.precision_places,
                    "capabilities": {
                        "provider": "mock",
                        "worker_pull_v0": True,
                        "phase8_local_chain_settlement_execution": True,
                    },
                },
            ),
        )
        assert_equal("register worker ok", registered.get("ok"), True)

        add_step(
            steps,
            "worker heartbeat",
            post_json(
                f"{hub_url}/api/hub/v1/workers/heartbeat",
                {"worker_node_id": worker, "status": "available", "model": "mock-fast-chat"},
            ),
        )

        submitted = add_step(
            steps,
            "submit high precision paid worker-pull request",
            post_json(
                f"{hub_url}/api/hub/v1/requests",
                {
                    "account_id": requester,
                    "client_node_id": requester,
                    "model": "mock-fast-chat",
                    "prompt": "phase8 local-chain settlement execution smoke",
                    "max_credits": args.max_credits,
                    "execution_mode": "worker_pull_v0",
                    "metadata": {
                        "worker_pull_v0": True,
                        "phase8_local_chain_settlement_execution": True,
                        "mock_provider_config": {"answer": "phase8 local-chain settlement execution answer"},
                    },
                    "idempotency_key": request_key,
                },
            ),
        )["request"]
        request_id = str(submitted["request_id"])

        completed = submitted
        if submitted.get("state") != "completed":
            assert_equal("submitted state", submitted.get("state"), "queued")
            polled = add_step(steps, "worker polls lease", post_json(f"{hub_url}/api/hub/v1/workers/poll", {"worker_node_id": worker}))
            lease = polled.get("lease")
            if not isinstance(lease, dict):
                raise AssertionError("worker did not receive a lease")
            assert_equal("leased request id", lease.get("request_id"), request_id)

            completed = add_step(
                steps,
                "worker submits successful result",
                post_json(
                    f"{hub_url}/api/hub/v1/workers/results",
                    {
                        "worker_node_id": worker,
                        "request_id": lease["request_id"],
                        "lease_id": lease["lease_id"],
                        "result": {
                            "status": "success",
                            "response": {
                                "content": "phase8 local-chain settlement execution answer",
                                "provider": "mock-worker",
                                "model": "mock-fast-chat",
                                "metadata": {"phase8_local_chain_settlement_execution": True},
                            },
                        },
                    },
                ),
            )["request"]

        assert_equal("completed state", completed.get("state"), "completed")
        assert_equal("charged high precision credits", int(completed.get("charged_credits", 0)), args.worker_credits)
        earning_id = str(completed.get("worker_earning_id") or "")
        if not earning_id:
            raise AssertionError("completed request did not expose worker_earning_id")

        payout_queue = (completed.get("response") or {}).get("metadata", {}).get("hub", {}).get("payout_queue", {})
        add_step(steps, "check response payout queue privacy", {"ok": True, "payout_queue": payout_queue})
        assert_not_contains("response payout_queue", payout_queue, args.worker_credits)
        assert_contains("response payout_queue rounded", payout_queue, expected_published)

        public_status = add_step(steps, "query normal hub status", get_json(f"{hub_url}/api/hub/status"))
        assert_not_contains("normal hub status energy", public_status.get("energy", {}), args.worker_credits)
        assert_contains("normal hub status rounded energy", public_status.get("energy", {}), expected_published)
        assert_equal("normal hub energy privacy", public_status["energy"]["payout_queue"]["privacy"]["exact_amounts_hidden"], True)

        audit_status = add_step(steps, "query audit hub status", get_json(f"{hub_url}/api/hub/status?{urlencode({'audit': '1'})}"))
        assert_contains("audit hub status energy", audit_status.get("energy", {}), args.worker_credits)

        public_payouts = add_step(
            steps,
            "query normal legacy payout summary",
            get_json(f"{hub_url}/api/hub/payouts?{urlencode({'node_id': worker})}"),
        )
        assert_equal("normal payout published credits", int(public_payouts.get("pending_credits", -1)), expected_published)
        assert_not_contains("normal legacy payout summary", public_payouts, args.worker_credits)
        assert_equal("normal payout privacy", public_payouts["privacy"]["exact_amounts_hidden"], True)

        audit_payouts = add_step(
            steps,
            "query audit legacy payout summary",
            get_json(f"{hub_url}/api/hub/payouts?{urlencode({'node_id': worker, 'audit': '1'})}"),
        )
        assert_equal("audit payout exact credits", int(audit_payouts.get("pending_credits_exact", -1)), args.worker_credits)
        assert_contains("audit legacy payout summary", audit_payouts, args.worker_credits)

        claim = add_step(
            steps,
            "record exact worker claim",
            post_json(
                f"{hub_url}/api/hub/v1/workers/claims",
                {
                    "worker_node_id": worker,
                    "idempotency_key": claim_key,
                    "memo": "phase8 local-chain settlement execution claim",
                    "metadata": {"phase8_local_chain_settlement_execution": True, "scope": scope},
                },
            ),
        )
        assert_equal("claim ok", claim.get("ok"), True)
        claim_id = str((claim.get("claim") or {}).get("claim_id") or "")
        if not claim_id:
            raise AssertionError("claim response did not include a claim_id")

        public_settlement = add_step(
            steps,
            "query normal worker settlement",
            get_json(f"{hub_url}/api/hub/v1/workers/settlements?{urlencode({'worker_node_id': worker})}"),
        )
        assert_not_contains("normal worker settlement", public_settlement, args.worker_credits)
        assert_equal("normal settlement privacy", public_settlement["privacy"]["exact_amounts_hidden"], True)

        audit_settlement = add_step(
            steps,
            "query audit worker settlement",
            get_json(f"{hub_url}/api/hub/v1/workers/settlements?{urlencode({'worker_node_id': worker, 'audit': '1'})}"),
        )
        same_scope_already_settled = int(audit_settlement.get("settleable_units_exact", 0)) == 0 and int(
            audit_settlement.get("settled_units_exact", 0)
        ) == args.worker_credits
        if same_scope_already_settled:
            assert_equal("normal settlement published after prior run", int(public_settlement.get("settled_units_published", -1)), expected_published)
        else:
            assert_equal("normal settlement published", int(public_settlement.get("settleable_units_published", -1)), expected_published)
            assert_equal("audit settlement exact", int(audit_settlement.get("settleable_units_exact", -1)), args.worker_credits)
            assert_equal("audit settlement published", int(audit_settlement.get("settleable_units_published", -1)), expected_published)
            assert_equal("audit settlement dust", int(audit_settlement.get("settleable_dust_units", -1)), expected_dust)

        batch = add_step(
            steps,
            "create rounded settlement batch",
            post_json(
                f"{hub_url}/api/hub/v1/workers/settlements/batches",
                {
                    "worker_node_id": worker,
                    "idempotency_key": batch_key,
                    "bridge_account_id": "bridge-worker-payout-dust",
                    "metadata": {"phase8_local_chain_settlement_execution": True, "scope": scope},
                },
            ),
        )
        assert_equal("batch ok", batch.get("ok"), True)
        batch_payload = batch.get("batch")
        if not isinstance(batch_payload, dict):
            raise AssertionError("settlement batch was not returned")
        assert_equal("batch exact", int(batch_payload.get("total_credits_exact", 0)), args.worker_credits)
        assert_equal("batch published", int(batch_payload.get("total_credits_published", 0)), expected_published)
        assert_equal("batch dust", int(batch_payload.get("dust_credits", 0)), expected_dust)

        if batch_payload.get("status") != "settled":
            bad_exact_receipt = add_step(
                steps,
                "reject exact high precision chain receipt",
                post_json(
                    f"{hub_url}/api/hub/v1/workers/settlements/chain-executions",
                    {
                        "batch_id": batch_payload["batch_id"],
                        "chain_id": chain_id,
                        "contract_address": contract_address,
                        "recipient_address": worker_payout_address,
                        "payout_units_executed": args.worker_credits,
                        "settlement_tx_hash": "0x" + "8" * 64,
                        "proposal_id": "bad-exact-high-precision",
                        "block_number": 1,
                        "payout_rail": "xlag-bridge-reserve-local",
                        "operator_id": f"phase8-local-chain-operator-{scope}",
                        "idempotency_key": exact_reject_key,
                    },
                    allow_error=True,
                ),
            )
            assert_equal("bad exact receipt status", int(bad_exact_receipt.get("_http_status", 0)), 400)
            if "rounded published settlement amount" not in str(bad_exact_receipt.get("error", "")):
                raise AssertionError(f"exact high precision receipt failed for the wrong reason: {bad_exact_receipt}")

            chain_execution = add_step(
                steps,
                "execute rounded payout on local chain",
                execute_local_chain_payout(
                    rpc_url=rpc_url,
                    expected_chain_id=chain_id,
                    contract_address=contract_address,
                    captain=captain,
                    beta_second=beta_second,
                    recipient_address=worker_payout_address,
                    payout_units=expected_published,
                    fund_units=fund_units,
                    memo=f"phase8 settlement {batch_payload['batch_id']}",
                    expires_blocks=args.expires_blocks,
                    mine_extra_blocks=args.mine_extra_blocks,
                    timeout=args.timeout_s,
                    poll_s=args.poll_s,
                ),
            )
        else:
            existing_tx_hash = str(batch_payload.get("settlement_tx_hash") or "")
            if not existing_tx_hash:
                raise AssertionError("idempotent settled batch is missing settlement_tx_hash")
            metadata = batch_payload.get("metadata") if isinstance(batch_payload.get("metadata"), dict) else {}
            chain_execution = add_step(
                steps,
                "reuse prior local chain receipt for idempotent scope",
                {
                    "ok": True,
                    "already_recorded": True,
                    "chain_id": int(metadata.get("chain_id", chain_id)),
                    "contract_address": str(metadata.get("contract_address", contract_address)).lower(),
                    "recipient_address": str(metadata.get("recipient_address", worker_payout_address)).lower(),
                    "proposal_id": str(metadata.get("proposal_id", "")),
                    "payout_units": int(metadata.get("payout_units_executed", expected_published)),
                    "settlement_tx_hash": existing_tx_hash.lower(),
                    "block_number": int(metadata.get("block_number", 0) or 0),
                },
            )

        assert_equal("chain payout amount", int(chain_execution.get("payout_units", 0)), expected_published)
        assert_equal("chain id", int(chain_execution.get("chain_id", 0)), chain_id)
        chain_tx_hash = normalize_tx_hash(str(chain_execution.get("settlement_tx_hash", "")))
        proposal_id = str(chain_execution.get("proposal_id", ""))
        block_number = int(chain_execution.get("block_number", 0) or 0)

        proof_payload = {
            "phase8_local_chain_execution": True,
            "executed_credits": expected_published,
            "bridge_retained_credits": expected_dust,
            "precision_places": args.precision_places,
            "chain_id": chain_id,
            "contract_address": contract_address,
            "recipient_address": worker_payout_address,
            "proposal_id": proposal_id,
            "settlement_tx_hash": chain_tx_hash,
            "block_number": block_number,
            "event": chain_execution.get("payout_executed_event", {}),
        }

        settled = add_step(
            steps,
            "record real local-chain payout receipt with hub",
            post_json(
                f"{hub_url}/api/hub/v1/workers/settlements/chain-executions",
                {
                    "batch_id": batch_payload["batch_id"],
                    "chain_id": chain_id,
                    "contract_address": contract_address,
                    "recipient_address": worker_payout_address,
                    "payout_units_executed": expected_published,
                    "settlement_tx_hash": chain_tx_hash,
                    "proposal_id": proposal_id,
                    "block_number": block_number,
                    "payout_rail": "xlag-bridge-reserve-local",
                    "operator_id": f"phase8-local-chain-operator-{scope}",
                    "settlement_proof": proof_payload,
                    "idempotency_key": receipt_key,
                    "metadata": {"phase8_local_chain_settlement_execution": True, "scope": scope},
                },
            ),
        )
        assert_equal("hub receipt ok", settled.get("ok"), True)
        assert_equal("hub receipt settled published credits", int(settled.get("settled_credits", 0)), expected_published)
        assert_equal("hub receipt bridge retained credits", int(settled.get("bridge_retained_credits", -1)), expected_dust)

        settled_batch = settled.get("batch") if isinstance(settled.get("batch"), dict) else {}
        proof_id = str(settled_batch.get("settlement_proof_id", ""))
        proof_hash = str(settled_batch.get("settlement_proof_hash", ""))
        if not proof_id.startswith("proof_"):
            raise AssertionError(f"chain settlement proof id missing: {proof_id!r}")
        assert_equal("proof hash length", len(proof_hash), 64)
        assert_equal("payout rail", str(settled_batch.get("payout_rail", "")), "xlag-bridge-reserve-local")

        hub_execution = settled.get("chain_payout_execution") if isinstance(settled.get("chain_payout_execution"), dict) else {}
        assert_equal("hub chain execution amount", int(hub_execution.get("payout_units_executed", 0)), expected_published)
        assert_equal("hub chain execution tx", str(hub_execution.get("settlement_tx_hash", "")).lower(), chain_tx_hash.lower())

        duplicate_receipt = add_step(
            steps,
            "duplicate local-chain receipt does not settle again",
            post_json(
                f"{hub_url}/api/hub/v1/workers/settlements/chain-executions",
                {
                    "batch_id": batch_payload["batch_id"],
                    "chain_id": chain_id,
                    "contract_address": contract_address,
                    "recipient_address": worker_payout_address,
                    "payout_units_executed": expected_published,
                    "settlement_tx_hash": chain_tx_hash,
                    "proposal_id": proposal_id,
                    "block_number": block_number,
                    "payout_rail": "xlag-bridge-reserve-local",
                    "operator_id": f"phase8-local-chain-operator-{scope}",
                    "settlement_proof": proof_payload,
                    "idempotency_key": receipt_key,
                },
            ),
        )
        assert_equal("duplicate receipt ok", duplicate_receipt.get("ok"), True)
        assert_equal("duplicate receipt additional", int(duplicate_receipt.get("additional_settled_credits", -1)), 0)

        public_after = add_step(
            steps,
            "query normal worker settlement after receipt",
            get_json(f"{hub_url}/api/hub/v1/workers/settlements?{urlencode({'worker_node_id': worker})}"),
        )
        assert_not_contains("normal worker settlement after receipt", public_after, args.worker_credits)
        assert_equal("normal settled published after", int(public_after.get("settled_units_published", -1)), expected_published)

        audit_after = add_step(
            steps,
            "query audit worker settlement after receipt",
            get_json(f"{hub_url}/api/hub/v1/workers/settlements?{urlencode({'worker_node_id': worker, 'audit': '1'})}"),
        )
        assert_equal("audit settled exact after", int(audit_after.get("settled_units_exact", -1)), args.worker_credits)
        assert_equal("audit settled published after", int(audit_after.get("settled_units_published", -1)), expected_published)
        assert_equal("audit bridge retained after", int(audit_after.get("bridge_retained_units", -1)), expected_dust)
        audit_json = json.dumps(audit_after, sort_keys=True)
        if chain_tx_hash not in audit_json:
            raise AssertionError("audit settlement did not include chain transaction hash")
        if contract_address not in audit_json:
            raise AssertionError("audit settlement did not include contract address")

        report.update(
            {
                "ok": True,
                "paid_request": {
                    "request_id": request_id,
                    "request_idempotency_key": request_key,
                    "state": completed.get("state"),
                    "charged_units": int(completed.get("charged_credits", 0)),
                    "worker_earning_id": earning_id,
                },
                "claim_id": claim_id,
                "settlement_batch_id": batch_payload["batch_id"],
                "exact_worker_earning_units": args.worker_credits,
                "claimed_units_exact": int((claim.get("claim") or {}).get("claimed_credits", 0)),
                "rounded_payout_units": expected_published,
                "chain_payout_units_executed": expected_published,
                "bridge_retained_units": expected_dust,
                "chain_id": chain_id,
                "contract_address": contract_address,
                "worker_payout_address": worker_payout_address,
                "proposal_id": proposal_id,
                "chain_tx_hash": chain_tx_hash,
                "settlement_proof_id": proof_id,
                "settlement_proof_hash": proof_hash,
                "hub_recorded_receipt": True,
                "duplicate_receipt_additional_units": int(duplicate_receipt.get("additional_settled_credits", -1)),
                "normal_surfaces_leak_exact_amount": False,
                "admin_audit_reconciles_exact_amount": True,
                "exact_high_precision_receipt_rejected": True,
                "local_chain_execution": chain_execution,
                "hub_chain_payout_execution": hub_execution,
            }
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(
                "ok phase8 local-chain worker settlement execution: "
                f"exact={args.worker_credits} rounded={expected_published} "
                f"chain_executed={expected_published} bridge_retained={expected_dust} "
                f"tx={chain_tx_hash} duplicate_additional=0 normal_leak=false"
            )
        return 0
    except Exception as exc:
        report["ok"] = False
        report["error"] = str(exc)
        if not contract_address and args.contract_address:
            report["contract_address"] = args.contract_address
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        print(f"phase8 local-chain worker settlement execution smoke failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
