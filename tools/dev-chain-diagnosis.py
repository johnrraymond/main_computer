#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_STATE_FILE = Path("runtime/deployments/current.json")
LEGACY_STATE_FILE = Path("runtime/dev-chain/latest.json")
DEFAULT_REPORT_FILE = Path("runtime/dev-chain/smoke-latest.json")
DEFAULT_CHAIN_ID = 42424242
DEFAULT_RPC_URL = "http://127.0.0.1:18545"
DEFAULT_MAX_PAYOUT_WEI = 10**18
DEFAULT_PAYOUT_DELAY_BLOCKS = 1
DEFAULT_RESET_DELAY_BLOCKS = 1

DEFAULT_ANVIL_OFFICES = [
    "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266",
    "0x70997970c51812dc3a010c7d01b50e0d17dc79c8",
    "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc",
    "0x90f79bf6eb2c4f870365e785982e1f101e93b906",
]

SELECTORS = {
    "XLagBridgeReserve.OFFICE_COUNT()": "05cdb182",
    "XLagBridgeReserve.getOffice(uint8)": "a653a364",
    "XLagBridgeReserve.isOffice(address)": "1f18795c",
    "XLagBridgeReserve.officeIndexPlusOne(address)": "50ac700f",
    "XLagBridgeReserve.maxPayoutWei()": "e21a90a6",
    "XLagBridgeReserve.payoutDelayBlocks()": "5a8d8e42",
    "XLagBridgeReserve.resetDelayBlocks()": "68b44c2d",
    "XLagBridgeReserve.nextProposalId()": "2ab09d14",
    "XLagBridgeReserve.walletSmokeNonce()": "1a0cbd5d",
    "XLagBridgeReserve.frobNonce()": "216a323b",
    "AlphaBetaLockout.COUNCIL_SIZE()": "2d31011d",
    "AlphaBetaLockout.councilMember(uint256)": "4c318397",
    "AlphaBetaLockout.isCouncilMember(address)": "ebd7dc52",
}

ADDRESS_RE = re.compile(r"0x[a-fA-F0-9]{40}")


@dataclasses.dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    data: Any = None


def log(message: str = "") -> None:
    print(message, flush=True)


def normalize_address(value: str) -> str:
    match = ADDRESS_RE.search(str(value))
    if not match:
        raise ValueError(f"not an Ethereum address: {value!r}")
    return match.group(0).lower()


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
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_state(path: Path) -> tuple[dict[str, Any], dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"missing deploy state file: {path}")
    state = json.loads(path.read_text(encoding="utf-8"))
    env_path = path.with_suffix(".env")
    return state, load_env_file(env_path)


def first_int(*values: Any, default: int | None = None) -> int | None:
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        text = str(value).strip()
        if not text:
            continue
        try:
            return int(text, 0)
        except ValueError:
            continue
    return default


def recursive_address_list(value: Any) -> list[str] | None:
    if isinstance(value, list):
        addresses: list[str] = []
        for item in value:
            if isinstance(item, str) and ADDRESS_RE.fullmatch(item.strip()):
                addresses.append(normalize_address(item))
            elif isinstance(item, dict):
                candidate = item.get("address") or item.get("account") or item.get("public_address")
                if isinstance(candidate, str) and ADDRESS_RE.fullmatch(candidate.strip()):
                    addresses.append(normalize_address(candidate))
                else:
                    return None
            else:
                return None
        return addresses if len(addresses) >= 4 else None

    if isinstance(value, dict):
        preferred_keys = (
            "offices",
            "office_addresses",
            "office_accounts",
            "office_keys",
            "initial_offices",
            "accounts",
        )
        for key in preferred_keys:
            if key in value:
                found = recursive_address_list(value[key])
                if found:
                    return found[:4]
        for child in value.values():
            found = recursive_address_list(child)
            if found:
                return found[:4]
    return None


def extract_offices(state: dict[str, Any]) -> list[str]:
    found = recursive_address_list(state)
    if found:
        return found[:4]
    return DEFAULT_ANVIL_OFFICES.copy()


def find_deployment_by_key(state: dict[str, Any], keys: tuple[str, ...]) -> Any:
    deployments = state.get("deployments")
    if isinstance(deployments, dict):
        lowered = {str(key).lower(): value for key, value in deployments.items()}
        for key in keys:
            if key.lower() in lowered:
                return lowered[key.lower()]
        for value in deployments.values():
            if isinstance(value, dict):
                target = str(value.get("target") or value.get("contract") or "").lower()
                if any(key.lower() in target for key in keys):
                    return value
    return None


def address_from_value(value: Any) -> str | None:
    if isinstance(value, str):
        match = ADDRESS_RE.search(value)
        return normalize_address(match.group(0)) if match else None
    if isinstance(value, dict):
        for key in (
            "address",
            "deployedTo",
            "deployed_to",
            "contract_address",
            "contractAddress",
            "deployed_address",
        ):
            if key in value:
                found = address_from_value(value[key])
                if found:
                    return found
        for child in value.values():
            found = address_from_value(child)
            if found:
                return found
    if isinstance(value, list):
        for child in value:
            found = address_from_value(child)
            if found:
                return found
    return None


def extract_contract_address(
    state: dict[str, Any],
    env: dict[str, str],
    *,
    contract: str,
    keys: tuple[str, ...],
    env_keys: tuple[str, ...],
) -> str:
    for env_key in env_keys:
        if env_key in env:
            found = address_from_value(env[env_key])
            if found:
                return found

    deployment = find_deployment_by_key(state, keys)
    found = address_from_value(deployment)
    if found:
        return found

    # Last resort: recursively scan for an object whose target names the contract.
    wanted = contract.lower()

    def scan(value: Any) -> str | None:
        if isinstance(value, dict):
            target = str(value.get("target") or value.get("contract") or value.get("name") or "").lower()
            if wanted in target or any(key.lower() in target for key in keys):
                candidate = address_from_value(value)
                if candidate:
                    return candidate
            for child in value.values():
                candidate = scan(child)
                if candidate:
                    return candidate
        elif isinstance(value, list):
            for child in value:
                candidate = scan(child)
                if candidate:
                    return candidate
        return None

    found = scan(state)
    if found:
        return found

    raise ValueError(f"could not find deployed address for {contract}")


def rpc_url_from_state(state: dict[str, Any], env: dict[str, str], override: str | None) -> str:
    if override:
        return override
    chain = state.get("chain") if isinstance(state.get("chain"), dict) else {}
    for value in (
        chain.get("host_rpc_url"),
        chain.get("rpc_url"),
        state.get("host_rpc_url"),
        state.get("rpc_url"),
        env.get("HOST_RPC_URL"),
        env.get("MAIN_COMPUTER_ENERGY_CHAIN_RPC_URL"),
    ):
        if value:
            return str(value)
    return DEFAULT_RPC_URL


def chain_id_from_state(state: dict[str, Any], env: dict[str, str], override: int | None) -> int:
    if override is not None:
        return override
    chain = state.get("chain") if isinstance(state.get("chain"), dict) else {}
    return first_int(
        chain.get("chain_id"),
        state.get("chain_id"),
        env.get("CHAIN_ID"),
        env.get("MAIN_COMPUTER_ENERGY_CHAIN_ID"),
        default=DEFAULT_CHAIN_ID,
    ) or DEFAULT_CHAIN_ID


def deployed_was_dry_run(state: dict[str, Any]) -> bool:
    return bool(state.get("dry_run"))


def http_post_json(url: str, payload: dict[str, Any], timeout: float) -> tuple[dict[str, Any], int, str | None]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw), int(response.status), None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"error": raw}
        return payload, int(exc.code), str(exc)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"error": str(exc)}, 0, str(exc)


def rpc(url: str, method: str, params: list[Any] | None = None, *, timeout: float = 5.0) -> Any:
    payload = {"jsonrpc": "2.0", "method": method, "params": params or [], "id": 1}
    response, status, error = http_post_json(url, payload, timeout)
    if error or status != 200:
        raise RuntimeError(f"{method} failed: {error or response}")
    if "error" in response:
        raise RuntimeError(f"{method} returned error: {response['error']}")
    return response.get("result")


def abi_uint(value: int) -> str:
    if value < 0:
        raise ValueError("negative ABI uint not supported")
    return hex(value)[2:].rjust(64, "0")


def abi_address(value: str) -> str:
    address = normalize_address(value)[2:]
    return address.rjust(64, "0")


def call_data(selector: str, *encoded_args: str) -> str:
    return "0x" + selector + "".join(encoded_args)


def word_at(hex_result: str, index: int = 0) -> str:
    clean = str(hex_result).removeprefix("0x")
    start = index * 64
    end = start + 64
    if len(clean) < end:
        raise ValueError(f"short ABI result: {hex_result!r}")
    return clean[start:end]


def decode_uint(hex_result: str, index: int = 0) -> int:
    return int(word_at(hex_result, index), 16)


def decode_bool(hex_result: str, index: int = 0) -> bool:
    return decode_uint(hex_result, index) != 0


def decode_address(hex_result: str, index: int = 0) -> str:
    return "0x" + word_at(hex_result, index)[-40:].lower()


def eth_call(url: str, to: str, data: str, *, timeout: float) -> str:
    return str(rpc(url, "eth_call", [{"to": normalize_address(to), "data": data}, "latest"], timeout=timeout))


def call_uint(url: str, to: str, selector_key: str, *args: str, timeout: float) -> int:
    result = eth_call(url, to, call_data(SELECTORS[selector_key], *args), timeout=timeout)
    return decode_uint(result)


def call_bool(url: str, to: str, selector_key: str, *args: str, timeout: float) -> bool:
    result = eth_call(url, to, call_data(SELECTORS[selector_key], *args), timeout=timeout)
    return decode_bool(result)


def call_address(url: str, to: str, selector_key: str, *args: str, timeout: float) -> str:
    result = eth_call(url, to, call_data(SELECTORS[selector_key], *args), timeout=timeout)
    return decode_address(result)


def append_check(results: list[CheckResult], name: str, ok: bool, detail: str, data: Any = None) -> bool:
    results.append(CheckResult(name=name, ok=ok, detail=detail, data=data))
    return ok


def verify_state(
    state: dict[str, Any],
    env: dict[str, str],
    *,
    rpc_url: str | None,
    expected_chain_id: int | None,
    timeout: float,
    allow_dry_run: bool,
    expected_max_payout_wei: int,
    expected_payout_delay_blocks: int,
    expected_reset_delay_blocks: int,
) -> tuple[bool, list[CheckResult], dict[str, Any]]:
    results: list[CheckResult] = []

    if deployed_was_dry_run(state) and not allow_dry_run:
        append_check(results, "deploy-state", False, "latest deploy state is marked dry_run=true")
        return False, results, {}

    url = rpc_url_from_state(state, env, rpc_url)
    chain_id_expected = chain_id_from_state(state, env, expected_chain_id)
    offices = extract_offices(state)

    xlag = extract_contract_address(
        state,
        env,
        contract="XLagBridgeReserve",
        keys=("xlag-bridge-reserve", "XLagBridgeReserve", "xlag"),
        env_keys=("XLAG_BRIDGE_RESERVE_ADDRESS", "MAIN_COMPUTER_XLAG_CONTRACT_ADDRESS"),
    )
    alpha = extract_contract_address(
        state,
        env,
        contract="AlphaBetaLockout",
        keys=("alpha-beta-lockout", "AlphaBetaLockout", "alpha"),
        env_keys=("ALPHA_BETA_LOCKOUT_ADDRESS", "MAIN_COMPUTER_ALPHA_BETA_LOCKOUT_CONTRACT_ADDRESS"),
    )
    hub_credit_bridge_escrow = extract_contract_address(
        state,
        env,
        contract="HubCreditBridgeEscrow",
        keys=("hub_credit_bridge_escrow", "hub-credit-bridge-escrow", "HubCreditBridgeEscrow"),
        env_keys=("HUB_CREDIT_BRIDGE_ESCROW_ADDRESS", "MAIN_COMPUTER_HUB_CREDIT_BRIDGE_ESCROW_ADDRESS"),
    )

    summary = {
        "rpc_url": url,
        "expected_chain_id": chain_id_expected,
        "offices": offices,
        "contracts": {
            "alpha-beta-lockout": alpha,
            "xlag-bridge-reserve": xlag,
            "hub_credit_bridge_escrow": hub_credit_bridge_escrow,
        },
    }

    chain_id = int(str(rpc(url, "eth_chainId", timeout=timeout)), 16)
    block_number = int(str(rpc(url, "eth_blockNumber", timeout=timeout)), 16)
    append_check(results, "chain-id", chain_id == chain_id_expected, f"chain_id={chain_id}, expected={chain_id_expected}")
    append_check(results, "block-number", block_number >= 0, f"block={block_number}")

    for key, address in (
        ("alpha-beta-lockout", alpha),
        ("xlag-bridge-reserve", xlag),
        ("hub_credit_bridge_escrow", hub_credit_bridge_escrow),
    ):
        code = str(rpc(url, "eth_getCode", [address, "latest"], timeout=timeout))
        append_check(results, f"{key}.code", code not in ("0x", "0x0", ""), f"code bytes={max((len(code) - 2) // 2, 0)}")

    alpha_size = call_uint(url, alpha, "AlphaBetaLockout.COUNCIL_SIZE()", timeout=timeout)
    append_check(results, "alpha-beta-lockout.council-size", alpha_size == 4, f"COUNCIL_SIZE={alpha_size}")

    for index, expected in enumerate(offices):
        actual = call_address(url, alpha, "AlphaBetaLockout.councilMember(uint256)", abi_uint(index), timeout=timeout)
        append_check(
            results,
            f"alpha-beta-lockout.council-member-{index}",
            actual == expected,
            f"actual={actual}, expected={expected}",
        )
        is_member = call_bool(url, alpha, "AlphaBetaLockout.isCouncilMember(address)", abi_address(expected), timeout=timeout)
        append_check(results, f"alpha-beta-lockout.is-member-{index}", is_member, f"{expected} is council member")

    office_count = call_uint(url, xlag, "XLagBridgeReserve.OFFICE_COUNT()", timeout=timeout)
    append_check(results, "xlag.office-count", office_count == 4, f"OFFICE_COUNT={office_count}")

    for index, expected in enumerate(offices):
        actual = call_address(url, xlag, "XLagBridgeReserve.getOffice(uint8)", abi_uint(index), timeout=timeout)
        append_check(results, f"xlag.office-{index}", actual == expected, f"actual={actual}, expected={expected}")
        is_office = call_bool(url, xlag, "XLagBridgeReserve.isOffice(address)", abi_address(expected), timeout=timeout)
        append_check(results, f"xlag.is-office-{index}", is_office, f"{expected} is office")
        index_plus_one = call_uint(url, xlag, "XLagBridgeReserve.officeIndexPlusOne(address)", abi_address(expected), timeout=timeout)
        append_check(
            results,
            f"xlag.office-index-plus-one-{index}",
            index_plus_one == index + 1,
            f"actual={index_plus_one}, expected={index + 1}",
        )

    max_payout = call_uint(url, xlag, "XLagBridgeReserve.maxPayoutWei()", timeout=timeout)
    payout_delay = call_uint(url, xlag, "XLagBridgeReserve.payoutDelayBlocks()", timeout=timeout)
    reset_delay = call_uint(url, xlag, "XLagBridgeReserve.resetDelayBlocks()", timeout=timeout)
    next_proposal_id = call_uint(url, xlag, "XLagBridgeReserve.nextProposalId()", timeout=timeout)
    balance = int(str(rpc(url, "eth_getBalance", [xlag, "latest"], timeout=timeout)), 16)

    append_check(results, "xlag.max-payout-wei", max_payout == expected_max_payout_wei, f"actual={max_payout}, expected={expected_max_payout_wei}")
    append_check(results, "xlag.payout-delay-blocks", payout_delay == expected_payout_delay_blocks, f"actual={payout_delay}, expected={expected_payout_delay_blocks}")
    append_check(results, "xlag.reset-delay-blocks", reset_delay == expected_reset_delay_blocks, f"actual={reset_delay}, expected={expected_reset_delay_blocks}")
    wallet_smoke_nonce = call_uint(url, xlag, "XLagBridgeReserve.walletSmokeNonce()", timeout=timeout)
    frob_nonce = call_uint(url, xlag, "XLagBridgeReserve.frobNonce()", timeout=timeout)

    append_check(results, "xlag.next-proposal-id", next_proposal_id >= 1, f"nextProposalId={next_proposal_id}, expected>=1")
    append_check(results, "xlag.balance-readable", balance >= 0, f"balanceWei={balance}")
    append_check(results, "xlag.wallet-smoke-nonce-readable", wallet_smoke_nonce >= 0, f"walletSmokeNonce={wallet_smoke_nonce}")
    append_check(results, "xlag.frob-nonce-readable", frob_nonce >= 0, f"frobNonce={frob_nonce}")

    summary["observed"] = {
        "chain_id": chain_id,
        "block_number": block_number,
        "xlag": {
            "max_payout_wei": max_payout,
            "payout_delay_blocks": payout_delay,
            "reset_delay_blocks": reset_delay,
            "next_proposal_id": next_proposal_id,
            "balance_wei": balance,
            "wallet_smoke_nonce": wallet_smoke_nonce,
            "frob_nonce": frob_nonce,
        },
    }

    ok = all(result.ok for result in results)
    return ok, results, summary


def write_report(path: Path, ok: bool, results: list[CheckResult], summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ok": ok,
        "summary": summary,
        "checks": [dataclasses.asdict(result) for result in results],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke-check a soft dev-chain deployment.")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_FILE, help="deploy state JSON, default runtime/deployments/current.json")
    parser.add_argument("--rpc-url", default=None, help="override host RPC URL")
    parser.add_argument("--chain-id", type=int, default=None, help="override expected chain id")
    parser.add_argument("--timeout-s", type=float, default=5.0)
    parser.add_argument("--allow-dry-run", action="store_true", help="do not fail immediately if the state says dry_run=true")
    parser.add_argument("--expect-max-payout-wei", type=int, default=DEFAULT_MAX_PAYOUT_WEI)
    parser.add_argument("--expect-payout-delay-blocks", type=int, default=DEFAULT_PAYOUT_DELAY_BLOCKS)
    parser.add_argument("--expect-reset-delay-blocks", type=int, default=DEFAULT_RESET_DELAY_BLOCKS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_FILE)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    repo = find_repo_root(Path.cwd())
    state_path = args.state if args.state.is_absolute() else repo / args.state
    if args.state == DEFAULT_STATE_FILE and not state_path.exists():
        legacy_state_path = repo / LEGACY_STATE_FILE
        if legacy_state_path.exists():
            state_path = legacy_state_path
    report_path = args.report if args.report.is_absolute() else repo / args.report

    log(f"Repository root: {repo}")
    log(f"Deploy state: {state_path}")

    try:
        state, env = load_state(state_path)
        ok, results, summary = verify_state(
            state,
            env,
            rpc_url=args.rpc_url,
            expected_chain_id=args.chain_id,
            timeout=args.timeout_s,
            allow_dry_run=args.allow_dry_run,
            expected_max_payout_wei=args.expect_max_payout_wei,
            expected_payout_delay_blocks=args.expect_payout_delay_blocks,
            expected_reset_delay_blocks=args.expect_reset_delay_blocks,
        )
    except Exception as exc:
        results = [CheckResult("smoke-exception", False, str(exc))]
        summary = {}
        ok = False

    log()
    log("Dev-chain smoke checks")
    log("======================")
    for result in results:
        status = "PASS" if result.ok else "FAIL"
        log(f"{status}: {result.name}: {result.detail}")

    write_report(report_path, ok, results, summary)
    log()
    log(f"Wrote report: {report_path}")

    if ok:
        log("PASS: soft dev-chain deployment is readable and contract configuration matches expected state.")
        return 0

    log("FAIL: soft dev-chain smoke check failed.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
