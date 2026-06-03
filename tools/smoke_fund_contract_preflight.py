#!/usr/bin/env python3
"""
Fresh-wallet faucet-to-escrow smoke test for the Worker Fund button path.

Run from the repository root:

    python tools/smoke_fund_contract_preflight.py

This intentionally uses Docker for Anvil/Foundry/cast operations.
It starts an embedded local ViewportServer only to exercise the real
/api/xlag/dev/faucet route; it does not require the full app to already be
running.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


FOUNDRY_IMAGE = "ghcr.io/foundry-rs/foundry:latest"

DEFAULT_CHAIN_ID = 42424242
DEFAULT_HOST_RPC_URL = "http://127.0.0.1:18545"
DEFAULT_PROJECT_NAME = "main-computer-dev"

DEPLOYER_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
DEPLOYER_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"

TARGET = "src/HubCreditBridgeEscrow.sol:HubCreditBridgeEscrow"
EVENT_SIG = "CreditDeposited(bytes32,address,address,uint256,string)"

BASE_UNITS_PER_CREDIT = 10**18
DEFAULT_FAUCET_CREDITS = "5"
DEFAULT_DEPOSIT_CREDITS = "1"

PYTHON_TESTS = [
    "tests/test_hub_credit_indexer.py",
    "tests/test_worker_app_layout_contract.py",
    "tests/test_bridge_escrow_paid_mock_spend_smoke.py",
    "tests/test_dev_chain_ledger_bridge_script.py",
]


class SmokeFailure(RuntimeError):
    pass


def repo_root() -> Path:
    start = Path.cwd().resolve()
    for candidate in (start, *start.parents):
        if (candidate / "contracts").is_dir() and (candidate / "tools" / "dev-chain-reset.py").exists():
            return candidate
    raise SmokeFailure(
        "Run this from the repo root or a child directory; "
        "could not find contracts/ and tools/dev-chain-reset.py"
    )


def ensure_repo_import_path(root: Path) -> None:
    """Make direct script execution from tools/ import the checkout package.

    When Python executes tools/smoke_fund_contract_preflight.py, sys.path[0]
    is tools/, not the repository root. The embedded faucet route imports the
    local main_computer package, so the checkout root must be first on sys.path.
    """

    root_text = str(root)
    if root_text in sys.path:
        sys.path.remove(root_text)
    sys.path.insert(0, root_text)


def docker_executable() -> str:
    return shutil.which("docker") or "docker"


def docker_mount_path(path: Path) -> str:
    resolved = path.resolve()
    if os.name == "nt":
        return resolved.as_posix()
    return str(resolved)


def network_name(project_name: str, run_id: str) -> str:
    return f"{project_name}-soft-{run_id}"


def container_name(project_name: str, run_id: str) -> str:
    return f"{project_name}-chain-{run_id}"


def container_rpc_url(project_name: str, run_id: str) -> str:
    return f"http://{container_name(project_name, run_id)}:8545"


def run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    timeout: int | float | None = None,
    echo: bool = True,
) -> subprocess.CompletedProcess[str]:
    if echo:
        print("\n$ " + " ".join(cmd), flush=True)

    completed = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )

    if completed.stdout:
        print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
    if completed.stderr:
        print(completed.stderr, end="" if completed.stderr.endswith("\n") else "\n", file=sys.stderr)

    if check and completed.returncode != 0:
        raise SmokeFailure(f"Command failed with exit {completed.returncode}: {' '.join(cmd)}")

    return completed


def docker_foundry_base(root: Path, args: argparse.Namespace, entrypoint: str, *, use_network: bool) -> list[str]:
    cmd = [docker_executable(), "run", "--rm"]
    if use_network:
        cmd += ["--network", network_name(args.project_name, args.run_id)]

    cmd += [
        "-v",
        f"{docker_mount_path(root)}:/workspace",
        "-w",
        "/workspace/contracts",
        "--entrypoint",
        entrypoint,
        args.foundry_image,
    ]
    return cmd


def forge(
    root: Path,
    args: argparse.Namespace,
    forge_args: list[str],
    *,
    use_network: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return run(
        docker_foundry_base(root, args, "forge", use_network=use_network) + forge_args,
        check=check,
        timeout=args.command_timeout_s,
    )


def cast(
    root: Path,
    args: argparse.Namespace,
    cast_args: list[str],
    *,
    use_network: bool = True,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return run(
        docker_foundry_base(root, args, "cast", use_network=use_network) + cast_args,
        check=check,
        timeout=args.command_timeout_s,
    )


def rpc(url: str, method: str, params: list | None = None) -> object:
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}
    ).encode("utf-8")
    request = Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=5) as response:
        data = json.loads(response.read().decode("utf-8"))
    if "error" in data:
        raise SmokeFailure(f"RPC error from {method}: {data['error']}")
    return data.get("result")


def rpc_balance(url: str, address: str) -> int:
    value = rpc(url, "eth_getBalance", [address, "latest"])
    if not isinstance(value, str):
        raise SmokeFailure(f"eth_getBalance returned non-string value: {value!r}")
    return int(value, 16)


def wait_for_rpc_chain(host_rpc_url: str, expected_chain_id: int, timeout_s: float = 20.0) -> None:
    deadline = time.time() + timeout_s
    last_error: Exception | None = None

    while time.time() < deadline:
        try:
            actual_hex = str(rpc(host_rpc_url, "eth_chainId"))
            actual = int(actual_hex, 16)
            if actual != expected_chain_id:
                raise SmokeFailure(
                    f"Wrong chain id at {host_rpc_url}: expected {expected_chain_id}, got {actual}"
                )
            print(f"OK: RPC is live at {host_rpc_url} with chain id {actual}")
            return
        except Exception as exc:
            last_error = exc
            time.sleep(0.5)

    raise SmokeFailure(f"RPC did not become ready at {host_rpc_url}: {last_error}")


def wait_for_tx_receipt(host_rpc_url: str, tx_hash: str, timeout_s: float = 20.0) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        result = rpc(host_rpc_url, "eth_getTransactionReceipt", [tx_hash])
        if isinstance(result, dict):
            return result
        time.sleep(0.25)
    raise SmokeFailure(f"Timed out waiting for tx receipt: {tx_hash}")


def wait_for_balance_at_least(host_rpc_url: str, address: str, minimum_wei: int, timeout_s: float = 20.0) -> int:
    deadline = time.time() + timeout_s
    last = 0

    while time.time() < deadline:
        last = rpc_balance(host_rpc_url, address)
        if last >= minimum_wei:
            return last
        time.sleep(0.25)

    raise SmokeFailure(
        f"Timed out waiting for {address} balance >= {minimum_wei}; last balance was {last}"
    )


def parse_json_object(text: str) -> dict:
    text = text.strip()

    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

    raise SmokeFailure("Could not parse JSON output")


def parse_address(text: str) -> str:
    try:
        obj = parse_json_object(text)
        for key in ("deployedTo", "contractAddress", "address"):
            value = obj.get(key)
            if isinstance(value, str) and re.fullmatch(r"0x[0-9a-fA-F]{40}", value):
                return value
    except SmokeFailure:
        pass

    for pattern in (
        r'"(?:deployedTo|contractAddress|address)"\s*:\s*"(0x[0-9a-fA-F]{40})"',
        r"Deployed to:\s*(0x[0-9a-fA-F]{40})",
        r"\b(0x[0-9a-fA-F]{40})\b",
    ):
        match = re.search(pattern, text)
        if match:
            return match.group(1)

    raise SmokeFailure("Could not parse deployed contract address")


def parse_tx_hash(text: str) -> str:
    try:
        obj = parse_json_object(text)
        for key in ("transactionHash", "txHash", "hash"):
            value = obj.get(key)
            if isinstance(value, str) and re.fullmatch(r"0x[0-9a-fA-F]{64}", value):
                return value
    except SmokeFailure:
        pass

    match = re.search(r"\b(0x[0-9a-fA-F]{64})\b", text)
    if not match:
        raise SmokeFailure("Could not parse transaction hash")
    return match.group(1)


def parse_uint(text: str) -> int:
    stripped = text.strip()

    decimals = re.findall(r"(?<![A-Za-z0-9])([0-9]+)(?![A-Za-z0-9])", stripped)
    if decimals:
        return int(decimals[-1])

    hexes = re.findall(r"0x[0-9a-fA-F]+", stripped)
    if hexes:
        return int(hexes[-1], 16)

    raise SmokeFailure(f"Could not parse uint from output: {stripped!r}")


def credit_amount_to_wei(value: str) -> int:
    text = str(value).strip()
    if re.fullmatch(r"[0-9]+", text):
        return int(text) * BASE_UNITS_PER_CREDIT
    return int(Decimal(text) * Decimal(BASE_UNITS_PER_CREDIT))


def deterministic_bytes32(label: str) -> str:
    return "0x" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def make_new_wallet(root: Path, args: argparse.Namespace) -> tuple[str, str]:
    private_key = "0x" + secrets.token_hex(32)

    completed = cast(
        root,
        args,
        ["wallet", "address", "--private-key", private_key],
        use_network=False,
    )

    match = re.search(r"0x[0-9a-fA-F]{40}", completed.stdout + "\n" + completed.stderr)
    if not match:
        raise SmokeFailure("Could not derive address for fresh test wallet")

    address = match.group(0)

    if address.lower() == DEPLOYER_ADDRESS.lower():
        raise SmokeFailure("Fresh wallet unexpectedly matched deployer address")

    print(f"OK: created fresh test wallet {address}")
    return private_key, address


def assert_dev_chain_reset_gap(root: Path) -> None:
    script = (root / "tools" / "dev-chain-reset.py").read_text(encoding="utf-8")

    has_xlag = (
        "xlag-bridge-reserve" in script
        and "src/XLagBridgeReserve.sol:XLagBridgeReserve" in script
    )
    has_escrow = "HubCreditBridgeEscrow" in script or "hub-credit-bridge-escrow" in script

    if not has_xlag:
        raise SmokeFailure("Could not find existing XLag deployment support in tools/dev-chain-reset.py")
    if has_escrow:
        raise SmokeFailure(
            "tools/dev-chain-reset.py already mentions HubCreditBridgeEscrow; "
            "update this smoke test expectation"
        )

    print("OK: dev-chain-reset.py deploys existing root contracts but not HubCreditBridgeEscrow yet")


def assert_abi_native_deposit_only(root: Path, args: argparse.Namespace) -> None:
    completed = forge(root, args, ["inspect", TARGET, "abi"], use_network=False)
    abi_text = completed.stdout

    if "depositFor" not in abi_text:
        raise SmokeFailure("ABI did not contain depositFor")
    if "approve" in abi_text or "transferFrom" in abi_text:
        raise SmokeFailure("ABI unexpectedly contains ERC-20-style approve/transferFrom")
    if "payable" not in abi_text:
        raise SmokeFailure("ABI did not make depositFor visibly payable")

    print("OK: ABI contains payable depositFor and no approve/transferFrom")


def run_python_tests(root: Path, args: argparse.Namespace) -> None:
    missing = [path for path in PYTHON_TESTS if not (root / path).exists()]
    if missing:
        raise SmokeFailure(f"Missing expected Python test file(s): {missing}")

    run(
        [sys.executable, "-m", "pytest", *PYTHON_TESTS, "-q"],
        cwd=root,
        timeout=args.command_timeout_s,
    )


def run_forge_tests(root: Path, args: argparse.Namespace) -> None:
    test_path = root / "contracts" / "test" / "HubCreditBridgeEscrow.t.sol"
    if not test_path.exists():
        raise SmokeFailure("Missing contracts/test/HubCreditBridgeEscrow.t.sol")

    forge(
        root,
        args,
        ["test", "--match-path", "test/HubCreditBridgeEscrow.t.sol", "-vvv"],
        use_network=False,
    )


def start_anvil_via_project_tool(root: Path, args: argparse.Namespace) -> None:
    cmd = [
        sys.executable,
        "tools/dev-chain-reset.py",
        "--yes",
        "--run-id",
        args.run_id,
        "--project-name",
        args.project_name,
        "--host-rpc-url",
        args.host_rpc_url,
        "--chain-id",
        str(args.chain_id),
        "--foundry-image",
        args.foundry_image,
        "--port-strategy",
        args.port_strategy,
        "--no-deploy",
    ]

    run(cmd, cwd=root, timeout=args.reset_timeout_s)
    wait_for_rpc_chain(args.host_rpc_url, args.chain_id)


def deploy_escrow(root: Path, args: argparse.Namespace) -> str:
    completed = forge(
        root,
        args,
        [
            "create",
            TARGET,
            "--rpc-url",
            container_rpc_url(args.project_name, args.run_id),
            "--private-key",
            DEPLOYER_PRIVATE_KEY,
            "--broadcast",
            "--json",
            "--constructor-args",
            args.bridge_controller,
        ],
        use_network=True,
    )

    address = parse_address(completed.stdout + "\n" + completed.stderr)
    print(f"OK: deployed HubCreditBridgeEscrow at {address}")
    return address


def assert_event_topic(root: Path, args: argparse.Namespace) -> str:
    completed = cast(root, args, ["sig-event", EVENT_SIG], use_network=False)
    topic = completed.stdout.strip().splitlines()[-1].strip()

    if not re.fullmatch(r"0x[0-9a-fA-F]{64}", topic):
        raise SmokeFailure(f"Unexpected event topic output for {EVENT_SIG}: {completed.stdout!r}")

    print(f"OK: {EVENT_SIG} topic is {topic}")
    return topic


def http_json(url: str, *, method: str = "GET", payload: dict | None = None, timeout_s: float = 10.0) -> dict:
    data = None
    headers = {"Accept": "application/json"}

    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(url, data=data, headers=headers, method=method)

    try:
        with urlopen(request, timeout=timeout_s) as response:
            body = response.read().decode("utf-8")
            result = json.loads(body)
            if not isinstance(result, dict):
                raise SmokeFailure(f"Expected JSON object from {url}, got {type(result).__name__}")
            return result
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SmokeFailure(f"HTTP {exc.code} from {url}: {body}") from exc
    except URLError as exc:
        raise SmokeFailure(f"Could not reach {url}: {exc}") from exc


def start_embedded_viewport_server(root: Path, args: argparse.Namespace):
    ensure_repo_import_path(root)

    from main_computer.config import MainComputerConfig
    from main_computer.energy_chain import EnergyChainClient
    from main_computer.viewport import ViewportServer

    imported_from = Path(__import__("main_computer").__file__ or "").resolve()
    print(f"OK: importing main_computer from {imported_from}")

    runtime_path = root / "runtime" / "deployments" / "current.json"
    offices = (
        {
            "office": "O0",
            "title": "Captain",
            "address": DEPLOYER_ADDRESS,
            "private_key": DEPLOYER_PRIVATE_KEY,
        },
    )

    try:
        data = json.loads(runtime_path.read_text(encoding="utf-8"))
        raw_offices = data.get("offices")
        if isinstance(raw_offices, list) and raw_offices:
            offices = tuple(office for office in raw_offices if isinstance(office, dict)) or offices
    except Exception:
        pass

    config = MainComputerConfig(
        workspace=root,
        energy_chain_rpc_url=args.host_rpc_url,
        energy_chain_id=args.chain_id,
        energy_chain_rpc_url_source="smoke",
        energy_chain_id_source="smoke",
        xlag_chain_id=args.chain_id,
        xlag_chain_id_source="smoke",
        dev_chain_run_id=args.run_id,
        dev_chain_runtime_path=runtime_path,
        dev_chain_runtime_source="deployment-runtime",
        dev_chain_offices=offices,
    )

    server = ViewportServer(("127.0.0.1", 0), config, verbose=False)
    server.energy_chain = EnergyChainClient(
        args.host_rpc_url,
        expected_chain_id=args.chain_id,
        timeout_s=5.0,
        rpc_url_source="smoke",
        expected_chain_id_source="smoke",
    )

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{server.server_port}"
    print(f"OK: embedded ViewportServer started at {base_url}")

    return server, thread, base_url


def stop_embedded_viewport_server(server, thread) -> None:
    if server is not None:
        server.shutdown()
        server.server_close()
    if thread is not None:
        thread.join(timeout=5)


def faucet_fund_new_wallet(root: Path, args: argparse.Namespace, wallet_address: str, expected_amount_wei: int) -> dict:
    server = None
    thread = None

    try:
        server, thread, base_url = start_embedded_viewport_server(root, args)

        status = http_json(f"{base_url}/api/xlag/dev/faucet", method="GET")
        if not status.get("ready"):
            raise SmokeFailure(f"Faucet status was not ready: {json.dumps(status, indent=2)}")

        print(f"OK: faucet API readiness says ready; faucet_from={status.get('faucet_from')}")

        result = http_json(
            f"{base_url}/api/xlag/dev/faucet",
            method="POST",
            payload={"address": wallet_address, "amount_credits": args.faucet_credits},
        )

        if not result.get("ok"):
            raise SmokeFailure(f"Faucet POST did not return ok: {json.dumps(result, indent=2)}")

        if str(result.get("to", "")).lower() != wallet_address.lower():
            raise SmokeFailure(f"Faucet funded unexpected target: {result.get('to')} != {wallet_address}")

        if int(str(result.get("amount_wei"))) != expected_amount_wei:
            raise SmokeFailure(
                f"Faucet amount mismatch: expected {expected_amount_wei}, got {result.get('amount_wei')}"
            )

        tx_hash = str(result.get("tx_hash") or "")
        if not re.fullmatch(r"0x[0-9a-fA-F]{64}", tx_hash):
            raise SmokeFailure(f"Faucet did not return a valid tx_hash: {tx_hash!r}")

        wait_for_tx_receipt(args.host_rpc_url, tx_hash)
        balance = wait_for_balance_at_least(args.host_rpc_url, wallet_address, expected_amount_wei)

        print(f"OK: faucet funded fresh wallet; tx={tx_hash}; balance={balance}")
        return result

    finally:
        stop_embedded_viewport_server(server, thread)


def send_good_deposit(
    root: Path,
    args: argparse.Namespace,
    escrow: str,
    wallet_private_key: str,
    wallet_address: str,
    amount: int,
) -> tuple[str, str]:
    deposit_id = deterministic_bytes32(f"{args.run_id}:fresh-wallet-good-deposit:{time.time_ns()}")

    completed = cast(
        root,
        args,
        [
            "send",
            escrow,
            "depositFor(address,uint256,bytes32,string)",
            wallet_address,
            str(amount),
            deposit_id,
            "fresh wallet faucet funded bridge deposit",
            "--value",
            str(amount),
            "--rpc-url",
            container_rpc_url(args.project_name, args.run_id),
            "--private-key",
            wallet_private_key,
            "--json",
        ],
        use_network=True,
    )

    tx_hash = parse_tx_hash(completed.stdout + "\n" + completed.stderr)
    print(f"OK: faucet-funded fresh wallet depositFor succeeded: tx={tx_hash}, depositId={deposit_id}")

    return tx_hash, deposit_id


def assert_deposited_units(root: Path, args: argparse.Namespace, escrow: str, wallet_address: str, expected: int) -> None:
    completed = cast(
        root,
        args,
        [
            "call",
            escrow,
            "depositedUnits(address)(uint256)",
            wallet_address,
            "--rpc-url",
            container_rpc_url(args.project_name, args.run_id),
        ],
        use_network=True,
    )

    actual = parse_uint(completed.stdout)
    if actual != expected:
        raise SmokeFailure(f"depositedUnits({wallet_address}) mismatch: expected {expected}, got {actual}")

    print(f"OK: depositedUnits({wallet_address}) == {actual}")


def assert_revert(root: Path, args: argparse.Namespace, cmd_args: list[str], expected_hint: str) -> None:
    completed = cast(root, args, cmd_args, use_network=True, check=False)
    combined = (completed.stdout or "") + "\n" + (completed.stderr or "")

    if completed.returncode == 0:
        raise SmokeFailure(f"Expected revert/failure containing {expected_hint!r}, but command succeeded")

    if expected_hint.lower() not in combined.lower():
        raise SmokeFailure(
            f"Command failed as expected, but did not include hint {expected_hint!r}.\n"
            f"Output was:\n{combined}"
        )

    print(f"OK: expected revert/failure observed: {expected_hint}")


def assert_duplicate_deposit_fails(
    root: Path,
    args: argparse.Namespace,
    escrow: str,
    wallet_private_key: str,
    wallet_address: str,
    amount: int,
    deposit_id: str,
) -> None:
    assert_revert(
        root,
        args,
        [
            "send",
            escrow,
            "depositFor(address,uint256,bytes32,string)",
            wallet_address,
            str(amount),
            deposit_id,
            "duplicate should fail",
            "--value",
            str(amount),
            "--rpc-url",
            container_rpc_url(args.project_name, args.run_id),
            "--private-key",
            wallet_private_key,
        ],
        "duplicate deposit id",
    )


def assert_value_mismatch_fails(
    root: Path,
    args: argparse.Namespace,
    escrow: str,
    wallet_private_key: str,
    wallet_address: str,
    amount: int,
) -> None:
    bad_id = deterministic_bytes32(f"{args.run_id}:fresh-wallet-bad-value:{time.time_ns()}")

    assert_revert(
        root,
        args,
        [
            "send",
            escrow,
            "depositFor(address,uint256,bytes32,string)",
            wallet_address,
            str(amount),
            bad_id,
            "bad value should fail",
            "--value",
            str(amount - 1),
            "--rpc-url",
            container_rpc_url(args.project_name, args.run_id),
            "--private-key",
            wallet_private_key,
        ],
        "value mismatch",
    )


def assert_receipt_has_credit_deposited(root: Path, args: argparse.Namespace, tx_hash: str, escrow: str, topic: str) -> None:
    completed = cast(
        root,
        args,
        [
            "receipt",
            tx_hash,
            "--rpc-url",
            container_rpc_url(args.project_name, args.run_id),
            "--json",
        ],
        use_network=True,
    )

    receipt = parse_json_object(completed.stdout)
    logs = receipt.get("logs") or []

    if not isinstance(logs, list):
        raise SmokeFailure("Receipt JSON did not contain a logs array")

    for log in logs:
        if not isinstance(log, dict):
            continue

        address = str(log.get("address") or "").lower()
        topics = [str(t).lower() for t in (log.get("topics") or [])]

        if address == escrow.lower() and topics and topics[0] == topic.lower():
            print("OK: receipt contains CreditDeposited log from the escrow contract")
            return

    raise SmokeFailure(f"Receipt did not contain CreditDeposited topic {topic} from escrow {escrow}")


def cleanup_chain(args: argparse.Namespace) -> None:
    print("\nCleaning up Docker chain container/network...")
    run([docker_executable(), "rm", "-f", container_name(args.project_name, args.run_id)], check=False)
    run([docker_executable(), "network", "rm", network_name(args.project_name, args.run_id)], check=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fresh-wallet faucet-to-HubCreditBridgeEscrow smoke test before wiring the Worker Fund button."
    )

    parser.add_argument("--run-id", default="fund-button-preflight")
    parser.add_argument("--project-name", default=DEFAULT_PROJECT_NAME)
    parser.add_argument("--host-rpc-url", default=DEFAULT_HOST_RPC_URL)
    parser.add_argument("--chain-id", type=int, default=DEFAULT_CHAIN_ID)
    parser.add_argument("--foundry-image", default=FOUNDRY_IMAGE)

    parser.add_argument(
        "--port-strategy",
        choices=("replace-project", "replace-any", "auto", "fail"),
        default="replace-project",
    )

    parser.add_argument("--bridge-controller", default=DEPLOYER_ADDRESS)
    parser.add_argument(
        "--faucet-credits",
        default=DEFAULT_FAUCET_CREDITS,
        help="Credits sent to the fresh wallet through /api/xlag/dev/faucet.",
    )
    parser.add_argument(
        "--deposit-credits",
        default=DEFAULT_DEPOSIT_CREDITS,
        help="Credits deposited from the fresh wallet into HubCreditBridgeEscrow.",
    )

    parser.add_argument("--skip-python-tests", action="store_true")
    parser.add_argument("--skip-forge-tests", action="store_true")
    parser.add_argument(
        "--skip-reset",
        action="store_true",
        help="Use an already-running project Anvil container/network.",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Remove the dev-chain Docker container/network at the end.",
    )

    parser.add_argument("--command-timeout-s", type=float, default=180.0)
    parser.add_argument("--reset-timeout-s", type=float, default=180.0)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = repo_root()
    ensure_repo_import_path(root)
    os.chdir(root)

    faucet_wei = credit_amount_to_wei(args.faucet_credits)
    deposit_wei = credit_amount_to_wei(args.deposit_credits)

    if deposit_wei <= 0:
        print("\nSMOKE FAIL: deposit amount must be greater than zero", file=sys.stderr)
        return 1

    if faucet_wei <= deposit_wei:
        print("\nSMOKE FAIL: faucet amount must exceed deposit amount to leave room for gas", file=sys.stderr)
        return 1

    print(f"Repository root: {root}")
    print(f"Foundry image: {args.foundry_image}")
    print(f"Run id: {args.run_id}")
    print(f"Host RPC: {args.host_rpc_url}")
    print(f"Container RPC: {container_rpc_url(args.project_name, args.run_id)}")
    print(f"Docker network: {network_name(args.project_name, args.run_id)}")
    print(f"Docker container: {container_name(args.project_name, args.run_id)}")
    print(f"Faucet amount wei: {faucet_wei}")
    print(f"Bridge deposit wei: {deposit_wei}")

    try:
        assert_dev_chain_reset_gap(root)

        if not args.skip_python_tests:
            run_python_tests(root, args)
        else:
            print("SKIP: Python tests")

        if not args.skip_forge_tests:
            run_forge_tests(root, args)
        else:
            print("SKIP: Foundry contract tests")

        assert_abi_native_deposit_only(root, args)

        if not args.skip_reset:
            start_anvil_via_project_tool(root, args)
        else:
            print("SKIP: dev-chain reset; checking existing RPC")
            wait_for_rpc_chain(args.host_rpc_url, args.chain_id)

        deployer_balance = rpc_balance(args.host_rpc_url, DEPLOYER_ADDRESS)
        if deployer_balance <= faucet_wei + deposit_wei:
            raise SmokeFailure(f"Deployer/faucet account balance too low: {deployer_balance}")

        print(f"OK: deployer/faucet account {DEPLOYER_ADDRESS} balance is {deployer_balance}")

        wallet_private_key, wallet_address = make_new_wallet(root, args)

        starting_balance = rpc_balance(args.host_rpc_url, wallet_address)
        if starting_balance != 0:
            raise SmokeFailure(f"Fresh wallet unexpectedly started with balance {starting_balance}: {wallet_address}")

        print(f"OK: fresh wallet starts unfunded: {wallet_address}")

        faucet_result = faucet_fund_new_wallet(root, args, wallet_address, faucet_wei)

        funded_balance = rpc_balance(args.host_rpc_url, wallet_address)
        if funded_balance < faucet_wei:
            raise SmokeFailure(f"Fresh wallet was not funded enough: {funded_balance} < {faucet_wei}")

        event_topic = assert_event_topic(root, args)
        escrow = deploy_escrow(root, args)

        before_deposit_balance = rpc_balance(args.host_rpc_url, wallet_address)
        if before_deposit_balance < deposit_wei:
            raise SmokeFailure(f"Fresh wallet balance too low for deposit: {before_deposit_balance} < {deposit_wei}")

        tx_hash, deposit_id = send_good_deposit(
            root,
            args,
            escrow,
            wallet_private_key,
            wallet_address,
            deposit_wei,
        )

        wait_for_tx_receipt(args.host_rpc_url, tx_hash)

        assert_deposited_units(root, args, escrow, wallet_address, deposit_wei)
        assert_duplicate_deposit_fails(
            root,
            args,
            escrow,
            wallet_private_key,
            wallet_address,
            deposit_wei,
            deposit_id,
        )
        assert_value_mismatch_fails(
            root,
            args,
            escrow,
            wallet_private_key,
            wallet_address,
            deposit_wei,
        )
        assert_receipt_has_credit_deposited(root, args, tx_hash, escrow, event_topic)

        ending_balance = rpc_balance(args.host_rpc_url, wallet_address)

        print("\nSMOKE PASS")
        print(f"Fresh wallet: {wallet_address}")
        print(f"Faucet tx: {faucet_result.get('tx_hash')}")
        print(f"Escrow: {escrow}")
        print(f"Good deposit tx: {tx_hash}")
        print(f"Good deposit id: {deposit_id}")
        print(f"Fresh wallet ending balance wei: {ending_balance}")

        print("\nWhat this proves:")
        print("- A new, previously unfunded wallet can be funded through /api/xlag/dev/faucet.")
        print("- The same faucet-funded wallet can fund HubCreditBridgeEscrow with native depositFor.")
        print("- The funding contract path is native value, not ERC-20 approval.")
        print("- Duplicate deposit id and value mismatch failures are still enforced.")
        print("- The receipt contains CreditDeposited from the escrow contract.")
        print("- dev-chain-reset.py still needs canonical HubCreditBridgeEscrow deployment/config wiring before Fund can avoid manual bridge addresses.")

        return 0

    except Exception as exc:
        print(f"\nSMOKE FAIL: {exc}", file=sys.stderr)
        return 1

    finally:
        if args.cleanup:
            cleanup_chain(args)


if __name__ == "__main__":
    raise SystemExit(main())