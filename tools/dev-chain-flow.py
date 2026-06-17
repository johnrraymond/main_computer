#!/usr/bin/env python3
"""
Run a Compute Credits reserve payout flow against the current soft dev-chain deployment.

This proves the deployed reserve can hold and move the native dev-chain value unit:

  fund XLagBridgeReserve with native dev-chain value
  propose payout from O0
  second payout from O2
  mine the delay block
  execute payout
  verify recipient balance increased
  verify proposal state is EXECUTED

Run after a successful dev-chain reset/deploy/smoke:

  python .\\dev-chain-flow.py

The script uses Anvil's unlocked dev accounts on the isolated soft chain. It does
not use a token contract. Compute Credits are modeled as native dev-chain base
units for this local settlement smoke path:

  1 Compute Credit = 10^18 base units
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from main_computer.prod_lock import require_unlocked_production_state


COMPUTE_CREDIT_BASE_UNITS = 10**18
ENG_WEI = COMPUTE_CREDIT_BASE_UNITS  # Deprecated compatibility alias.
DEFAULT_DEPLOYMENT_FILE = Path("runtime/deployments/dev/latest.json")
LEGACY_DEV_CHAIN_STATE_FILE = Path("runtime/dev-chain/latest.json")
DEFAULT_STATE_FILE = DEFAULT_DEPLOYMENT_FILE
DEFAULT_REPORT_FILE = Path("runtime/dev-chain/flow-latest.json")
DEFAULT_RPC_URL = "http://127.0.0.1:18545"
DEFAULT_CHAIN_ID = 42424242
DEFAULT_FUND_CREDITS = "1"
DEFAULT_FUND_ENG = DEFAULT_FUND_CREDITS  # Deprecated compatibility alias.
DEFAULT_PAYOUT_CREDITS = "0.125"
DEFAULT_PAYOUT_ENG = DEFAULT_PAYOUT_CREDITS  # Deprecated compatibility alias.
DEFAULT_MEMO = "compute credit reserve payout flow"
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


@dataclass
class Step:
    name: str
    ok: bool
    detail: str
    data: Any = None


def log(message: str = "") -> None:
    print(message, flush=True)


def normalize_address(value: str) -> str:
    raw = str(value or "").strip()
    if not raw.startswith("0x"):
        raw = "0x" + raw
    if len(raw) != 42:
        raise ValueError(f"expected Ethereum-style address, got {value!r}")
    int(raw[2:], 16)
    return raw.lower()


def find_repo_root(start: Path) -> Path:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (
            (candidate / "new_patch.py").exists()
            or (candidate / "pyproject.toml").exists()
            or (candidate / "docker-compose.dev.yml").exists()
            or (candidate / ".git").exists()
        ):
            return candidate
    return current


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


def resolve_state_file(root: Path, requested: Path) -> Path:
    """Resolve the deployment state path used by the flow script.

    The app-facing production-shaped deployment publication is the default.
    Keep the legacy dev-chain state as a compatibility fallback only when the
    default publication has not been written yet.
    """

    path = requested if requested.is_absolute() else root / requested
    if requested == DEFAULT_DEPLOYMENT_FILE and not path.exists():
        legacy_path = root / LEGACY_DEV_CHAIN_STATE_FILE
        if legacy_path.exists():
            return legacy_path
    return path


def load_state(path: Path) -> tuple[dict[str, Any], dict[str, str]]:
    state = json.loads(path.read_text(encoding="utf-8"))
    env = load_env_file(path.with_suffix(".env"))
    return state, env


def parse_compute_credits(value: str) -> int:
    text = str(value).strip()
    if not text:
        raise ValueError("empty Compute Credits amount")
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"invalid Compute Credits amount: {value!r}") from exc
    if amount <= 0:
        raise ValueError("Compute Credits amount must be positive")
    scaled = amount * COMPUTE_CREDIT_BASE_UNITS
    if scaled != scaled.to_integral_value():
        raise ValueError("Compute Credits amount has more than 18 decimal places")
    return int(scaled)


def parse_eng(value: str) -> int:
    """Deprecated compatibility alias for pre-C0 operator scripts."""
    return parse_compute_credits(value)


def format_compute_credits(amount_base_units: int) -> str:
    sign = "-" if amount_base_units < 0 else ""
    value = abs(int(amount_base_units))
    whole = value // COMPUTE_CREDIT_BASE_UNITS
    fraction = value % COMPUTE_CREDIT_BASE_UNITS
    if fraction == 0:
        return f"{sign}{whole} Compute Credits"
    return f"{sign}{whole}.{str(fraction).rjust(18, '0').rstrip('0')} Compute Credits"


def format_eng(amount_wei: int) -> str:
    """Deprecated compatibility alias for pre-C0 operator scripts."""
    return format_compute_credits(amount_wei)


def hex_quantity(value: int) -> str:
    if value < 0:
        raise ValueError("hex quantity cannot be negative")
    return hex(value)


def abi_uint(value: int) -> str:
    if value < 0:
        raise ValueError("ABI uint cannot be negative")
    return int(value).to_bytes(32, "big").hex()


def abi_address(value: str) -> str:
    address = normalize_address(value)
    return ("0" * 24) + address[2:]


def abi_string_tail(value: str) -> str:
    raw = value.encode("utf-8")
    length = abi_uint(len(raw))
    hex_text = raw.hex()
    padded_len = ((len(hex_text) + 63) // 64) * 64
    return length + hex_text.ljust(padded_len, "0")


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


def propose_payout_data(recipient: str, amount_wei: int, memo: str, expires_block: int) -> str:
    return call_data(
        SELECTORS["XLagBridgeReserve.proposePayout(address,uint256,string,uint64)"],
        abi_encode_address_uint_string_uint64(recipient, amount_wei, memo, expires_block),
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


def first_address(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return normalize_address(value)
    return None


def deployment_address(state: dict[str, Any], *keys: str) -> str:
    deployments = state.get("deployments")
    if not isinstance(deployments, dict):
        deployments = state.get("contracts")
    if not isinstance(deployments, dict):
        raise ValueError("deployment state has no deployments/contracts map")
    for key in keys:
        value = deployments.get(key)
        if isinstance(value, dict):
            address = first_address(value.get("address"), value.get("deployedTo"))
            if address:
                return address
    raise ValueError(f"could not find deployment address for {keys}")


def offices_from_state(state: dict[str, Any]) -> list[str]:
    offices = state.get("offices")
    if isinstance(offices, list):
        addresses = []
        for item in offices:
            if isinstance(item, dict) and item.get("address"):
                addresses.append(normalize_address(item["address"]))
            elif isinstance(item, str):
                addresses.append(normalize_address(item))
        if len(addresses) >= 4:
            return addresses[:4]

    chain = state.get("chain", {})
    if isinstance(chain, dict):
        chain_offices = chain.get("offices")
        if isinstance(chain_offices, list):
            addresses = [normalize_address(item["address"] if isinstance(item, dict) else item) for item in chain_offices]
            if len(addresses) >= 4:
                return addresses[:4]

    return list(DEFAULT_ANVIL_OFFICES)


def rpc_url_from_state(state: dict[str, Any], env: dict[str, str], override: str | None) -> str:
    if override:
        return override
    chain = state.get("chain", {})
    if isinstance(chain, dict):
        if chain.get("host_rpc_url"):
            return str(chain["host_rpc_url"])
        if chain.get("rpc_url"):
            return str(chain["rpc_url"])
    return env.get("MAIN_COMPUTER_ENERGY_CHAIN_RPC_URL") or DEFAULT_RPC_URL


def chain_id_from_state(state: dict[str, Any], env: dict[str, str], override: int | None) -> int:
    if override is not None:
        return override
    chain = state.get("chain", {})
    if isinstance(chain, dict) and chain.get("chain_id") is not None:
        return int(chain["chain_id"])
    if env.get("MAIN_COMPUTER_ENERGY_CHAIN_ID"):
        return int(env["MAIN_COMPUTER_ENERGY_CHAIN_ID"], 0)
    return DEFAULT_CHAIN_ID


def http_post_json(url: str, payload: dict[str, Any], timeout: float) -> tuple[dict[str, Any], int, str | None]:
    body = json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=timeout) as response:
        text = response.read().decode("utf-8")
        return json.loads(text), response.status, text


def rpc(url: str, method: str, params: list[Any] | None = None, *, timeout: float = 10.0) -> Any:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}
    data, _status, _text = http_post_json(url, payload, timeout)
    if "error" in data:
        raise RuntimeError(f"{method} RPC error: {data['error']}")
    return data.get("result")


def eth_call(url: str, to: str, data: str, *, timeout: float, rpc_func=rpc) -> str:
    return str(rpc_func(url, "eth_call", [{"to": normalize_address(to), "data": data}, "latest"], timeout=timeout))


def call_uint(url: str, to: str, selector_key: str, *args: str, timeout: float, rpc_func=rpc) -> int:
    return decode_uint(eth_call(url, to, call_data(SELECTORS[selector_key], *args), timeout=timeout, rpc_func=rpc_func))


def balance(url: str, address: str, *, timeout: float, rpc_func=rpc) -> int:
    return int(str(rpc_func(url, "eth_getBalance", [normalize_address(address), "latest"], timeout=timeout)), 16)


def send_transaction(
    url: str,
    tx: dict[str, Any],
    *,
    timeout: float,
    rpc_func=rpc,
) -> str:
    payload = dict(tx)
    if "from" in payload:
        payload["from"] = normalize_address(payload["from"])
    if "to" in payload and payload["to"]:
        payload["to"] = normalize_address(payload["to"])
    tx_hash = rpc_func(url, "eth_sendTransaction", [payload], timeout=timeout)
    return str(tx_hash)


def wait_receipt(url: str, tx_hash: str, *, timeout: float, poll_s: float, rpc_func=rpc) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() <= deadline:
        receipt = rpc_func(url, "eth_getTransactionReceipt", [tx_hash], timeout=timeout)
        if receipt:
            status = int(str(receipt.get("status", "0x0")), 16)
            if status != 1:
                raise RuntimeError(f"transaction failed: {tx_hash}: {receipt}")
            return receipt
        time.sleep(poll_s)
    raise TimeoutError(f"timed out waiting for transaction receipt: {tx_hash}")


def mine_blocks(url: str, count: int, *, timeout: float, rpc_func=rpc) -> None:
    for _ in range(max(0, count)):
        rpc_func(url, "evm_mine", [], timeout=timeout)


def append_step(steps: list[Step], name: str, ok: bool, detail: str, data: Any = None) -> None:
    status = "PASS" if ok else "FAIL"
    log(f"{status}: {name}: {detail}")
    steps.append(Step(name=name, ok=ok, detail=detail, data=data))


def run_flow(
    *,
    state: dict[str, Any],
    env: dict[str, str],
    rpc_url: str | None,
    expected_chain_id: int | None,
    fund_wei: int,
    payout_wei: int,
    memo: str,
    recipient: str | None,
    expires_blocks: int,
    mine_extra_blocks: int,
    timeout: float,
    poll_s: float,
    rpc_func=rpc,
) -> tuple[bool, dict[str, Any], list[Step]]:
    if state.get("dry_run"):
        raise RuntimeError("latest deploy state is a dry-run preview; run dev-chain-reset.py --yes first")

    url = rpc_url_from_state(state, env, rpc_url)
    chain_id_expected = chain_id_from_state(state, env, expected_chain_id)
    offices = offices_from_state(state)
    captain = offices[0]
    beta_second = offices[2]
    recipient_address = normalize_address(recipient) if recipient else offices[3]
    reserve = deployment_address(state, "xlag-bridge-reserve", "XLagBridgeReserve", "xlag")

    if payout_wei > fund_wei:
        raise ValueError("payout amount must be less than or equal to fund amount")

    steps: list[Step] = []

    chain_id = int(str(rpc_func(url, "eth_chainId", [], timeout=timeout)), 16)
    append_step(steps, "chain-id", chain_id == chain_id_expected, f"chain_id={chain_id}, expected={chain_id_expected}")

    block_before = int(str(rpc_func(url, "eth_blockNumber", [], timeout=timeout)), 16)
    append_step(steps, "block-readable", True, f"block={block_before}")

    reserve_before = balance(url, reserve, timeout=timeout, rpc_func=rpc_func)
    recipient_before = balance(url, recipient_address, timeout=timeout, rpc_func=rpc_func)
    append_step(
        steps,
        "native-eng-balances-before",
        True,
        f"reserve={format_compute_credits(reserve_before)}, recipient={format_compute_credits(recipient_before)}",
        {"reserve_wei": reserve_before, "recipient_wei": recipient_before},
    )

    next_id_before = call_uint(
        url,
        reserve,
        "XLagBridgeReserve.nextProposalId()",
        timeout=timeout,
        rpc_func=rpc_func,
    )
    payout_delay = call_uint(
        url,
        reserve,
        "XLagBridgeReserve.payoutDelayBlocks()",
        timeout=timeout,
        rpc_func=rpc_func,
    )

    fund_tx = send_transaction(
        url,
        {"from": captain, "to": reserve, "value": hex_quantity(fund_wei)},
        timeout=timeout,
        rpc_func=rpc_func,
    )
    fund_receipt = wait_receipt(url, fund_tx, timeout=timeout, poll_s=poll_s, rpc_func=rpc_func)
    append_step(steps, "fund-reserve", True, f"funded {format_compute_credits(fund_wei)}", fund_receipt)

    current_block = int(str(rpc_func(url, "eth_blockNumber", [], timeout=timeout)), 16)
    expires_block = current_block + expires_blocks
    proposal_id = next_id_before

    propose_tx = send_transaction(
        url,
        {
            "from": captain,
            "to": reserve,
            "data": propose_payout_data(recipient_address, payout_wei, memo, expires_block),
        },
        timeout=timeout,
        rpc_func=rpc_func,
    )
    propose_receipt = wait_receipt(url, propose_tx, timeout=timeout, poll_s=poll_s, rpc_func=rpc_func)
    append_step(steps, "propose-payout", True, f"proposal_id={proposal_id}, amount={format_compute_credits(payout_wei)}", propose_receipt)

    next_id_after = call_uint(
        url,
        reserve,
        "XLagBridgeReserve.nextProposalId()",
        timeout=timeout,
        rpc_func=rpc_func,
    )
    append_step(
        steps,
        "proposal-id-advanced",
        next_id_after == proposal_id + 1,
        f"before={proposal_id}, after={next_id_after}",
    )

    second_tx = send_transaction(
        url,
        {
            "from": beta_second,
            "to": reserve,
            "data": uint_call_data("XLagBridgeReserve.secondPayout(uint256)", proposal_id),
        },
        timeout=timeout,
        rpc_func=rpc_func,
    )
    second_receipt = wait_receipt(url, second_tx, timeout=timeout, poll_s=poll_s, rpc_func=rpc_func)
    append_step(steps, "second-payout", True, f"seconded by O2 {beta_second}", second_receipt)

    mine_count = int(payout_delay) + int(mine_extra_blocks)
    mine_blocks(url, mine_count, timeout=timeout, rpc_func=rpc_func)
    append_step(steps, "mine-delay", True, f"mined {mine_count} block(s) for payoutDelayBlocks={payout_delay}")

    execute_tx = send_transaction(
        url,
        {
            "from": captain,
            "to": reserve,
            "data": uint_call_data("XLagBridgeReserve.executePayout(uint256)", proposal_id),
        },
        timeout=timeout,
        rpc_func=rpc_func,
    )
    execute_receipt = wait_receipt(url, execute_tx, timeout=timeout, poll_s=poll_s, rpc_func=rpc_func)
    append_step(steps, "execute-payout", True, f"executed proposal_id={proposal_id}", execute_receipt)

    state_after = call_uint(
        url,
        reserve,
        "XLagBridgeReserve.proposalState(uint256)",
        abi_uint(proposal_id),
        timeout=timeout,
        rpc_func=rpc_func,
    )
    append_step(steps, "proposal-state-executed", state_after == EXECUTED_STATE, f"state={state_after}, expected={EXECUTED_STATE}")

    reserve_after = balance(url, reserve, timeout=timeout, rpc_func=rpc_func)
    recipient_after = balance(url, recipient_address, timeout=timeout, rpc_func=rpc_func)
    recipient_delta = recipient_after - recipient_before
    reserve_expected = reserve_before + fund_wei - payout_wei

    append_step(
        steps,
        "recipient-compute-credit-received",
        recipient_delta == payout_wei,
        f"delta={format_compute_credits(recipient_delta)}, expected={format_compute_credits(payout_wei)}",
        {"before_wei": recipient_before, "after_wei": recipient_after, "delta_wei": recipient_delta},
    )
    append_step(
        steps,
        "reserve-compute-credit-balance",
        reserve_after == reserve_expected,
        f"actual={format_compute_credits(reserve_after)}, expected={format_compute_credits(reserve_expected)}",
        {"before_wei": reserve_before, "after_wei": reserve_after, "expected_wei": reserve_expected},
    )

    ok = all(step.ok for step in steps)
    summary = {
        "ok": ok,
        "run_id": state.get("run_id"),
        "rpc_url": url,
        "chain_id": chain_id,
        "reserve": reserve,
        "captain": captain,
        "beta_second": beta_second,
        "recipient": recipient_address,
        "proposal_id": proposal_id,
        "fund_wei": fund_wei,
        "fund_credits": format_compute_credits(fund_wei),
        "payout_wei": payout_wei,
        "payout_credits": format_compute_credits(payout_wei),
        "reserve_balance_before_wei": reserve_before,
        "reserve_balance_after_wei": reserve_after,
        "recipient_balance_before_wei": recipient_before,
        "recipient_balance_after_wei": recipient_after,
        "recipient_delta_wei": recipient_delta,
        "transactions": {
            "fund": fund_tx,
            "propose": propose_tx,
            "second": second_tx,
            "execute": execute_tx,
        },
    }
    return ok, summary, steps


def write_report(path: Path, summary: dict[str, Any], steps: list[Step]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "summary": summary,
        "steps": [asdict(step) for step in steps],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    log()
    log(f"Wrote report: {path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a Compute Credits reserve payout flow on the soft dev-chain deployment.")
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_FILE)
    parser.add_argument("--rpc-url", default=None)
    parser.add_argument("--chain-id", type=int, default=None)
    parser.add_argument("--fund-credits", "--fund-eng", dest="fund_credits", default=DEFAULT_FUND_CREDITS)
    parser.add_argument("--payout-credits", "--payout-eng", dest="payout_credits", default=DEFAULT_PAYOUT_CREDITS)
    parser.add_argument("--recipient", default=None, help="Recipient address. Defaults to O3 from latest deploy state.")
    parser.add_argument("--memo", default=DEFAULT_MEMO)
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
    report_path = args.report if args.report.is_absolute() else root / args.report

    log(f"Repository root: {root}")
    log(f"Deploy state: {state_path}")
    log()

    try:
        require_unlocked_production_state(
            root,
            state_path,
            report_path,
            action="run dev-chain payout flow",
        )
        state, env = load_state(state_path)
        fund_wei = parse_compute_credits(args.fund_credits)
        payout_wei = parse_compute_credits(args.payout_credits)

        log("Compute Credits reserve payout flow")
        log("===================================")

        ok, summary, steps = run_flow(
            state=state,
            env=env,
            rpc_url=args.rpc_url,
            expected_chain_id=args.chain_id,
            fund_wei=fund_wei,
            payout_wei=payout_wei,
            memo=args.memo,
            recipient=args.recipient,
            expires_blocks=args.expires_blocks,
            mine_extra_blocks=args.mine_extra_blocks,
            timeout=args.timeout_s,
            poll_s=args.poll_s,
        )
        write_report(report_path, summary, steps)
        if ok:
            log("PASS: Compute Credits moved through the X-LAG reserve payout lifecycle.")
            return 0
        log("FAIL: Compute Credits reserve payout flow did not satisfy all checks.")
        return 1
    except Exception as exc:  # noqa: BLE001 - operator-facing script
        log()
        log(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
