#!/usr/bin/env python3
"""
Smoke-test the current HubCreditBridgeEscrow funding-contract assumptions.

Run from the repository root:

    python tools/smoke_fund_contract_preflight.py

This script intentionally uses Docker for every Anvil/Foundry interaction:
- tools/dev-chain-reset.py starts Anvil in a Foundry Docker container.
- forge test / forge inspect / forge create run in Foundry Docker containers.
- cast send / cast call / cast receipt run in Foundry Docker containers.

It does not require host forge/cast/anvil to be installed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen


FOUNDRY_IMAGE = "ghcr.io/foundry-rs/foundry:latest"

DEFAULT_CHAIN_ID = 42424242
DEFAULT_HOST_RPC_URL = "http://127.0.0.1:18545"
DEFAULT_PROJECT_NAME = "main-computer-dev"

# Anvil deterministic keys from tools/dev-chain-reset.py.
DEPLOYER_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
PAYER_PRIVATE_KEY = "0x59c6995e998f97a5a0044966f094538eeb8b1416d61b7aae62a49a6c8f6a3c11"

DEPLOYER_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
PAYER_ADDRESS = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"

TARGET = "src/HubCreditBridgeEscrow.sol:HubCreditBridgeEscrow"
EVENT_SIG = "CreditDeposited(bytes32,address,address,uint256,string)"
AMOUNT_WEI = 10**18

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
    raise SmokeFailure("Run this from the repo root or a child directory; could not find contracts/ and tools/dev-chain-reset.py")


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
    cmd = [
        docker_executable(),
        "run",
        "--rm",
    ]
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


def forge(root: Path, args: argparse.Namespace, forge_args: list[str], *, use_network: bool = False, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(docker_foundry_base(root, args, "forge", use_network=use_network) + forge_args, check=check, timeout=args.command_timeout_s)


def cast(root: Path, args: argparse.Namespace, cast_args: list[str], *, use_network: bool = True, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(docker_foundry_base(root, args, "cast", use_network=use_network) + cast_args, check=check, timeout=args.command_timeout_s)


def rpc(url: str, method: str, params: list | None = None) -> object:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}).encode("utf-8")
    req = Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=5) as response:
        data = json.loads(response.read().decode("utf-8"))
    if "error" in data:
        raise SmokeFailure(f"RPC error from {method}: {data['error']}")
    return data["result"]


def assert_rpc_chain(host_rpc_url: str, expected_chain_id: int) -> None:
    actual_hex = str(rpc(host_rpc_url, "eth_chainId"))
    actual = int(actual_hex, 16)
    if actual != expected_chain_id:
        raise SmokeFailure(f"Wrong chain id at {host_rpc_url}: expected {expected_chain_id}, got {actual}")
    print(f"OK: RPC is live at {host_rpc_url} with chain id {actual}")


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


def deterministic_bytes32(label: str) -> str:
    return "0x" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def assert_dev_chain_reset_gap(root: Path) -> None:
    script = (root / "tools" / "dev-chain-reset.py").read_text(encoding="utf-8")
    has_xlag = "xlag-bridge-reserve" in script and "src/XLagBridgeReserve.sol:XLagBridgeReserve" in script
    has_escrow = "HubCreditBridgeEscrow" in script or "hub-credit-bridge-escrow" in script
    if not has_xlag:
        raise SmokeFailure("Could not find existing XLag deployment support in tools/dev-chain-reset.py")
    if has_escrow:
        raise SmokeFailure("tools/dev-chain-reset.py already mentions HubCreditBridgeEscrow; update this smoke test expectation")
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
    run([sys.executable, "-m", "pytest", *PYTHON_TESTS, "-q"], cwd=root, timeout=args.command_timeout_s)


def run_forge_tests(root: Path, args: argparse.Namespace) -> None:
    if not (root / "contracts" / "test" / "HubCreditBridgeEscrow.t.sol").exists():
        raise SmokeFailure("Missing contracts/test/HubCreditBridgeEscrow.t.sol")
    forge(root, args, ["test", "--match-path", "test/HubCreditBridgeEscrow.t.sol", "-vvv"], use_network=False)


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
    assert_rpc_chain(args.host_rpc_url, args.chain_id)


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


def send_good_deposit(root: Path, args: argparse.Namespace, escrow: str, amount: int) -> tuple[str, str]:
    deposit_id = deterministic_bytes32(f"{args.run_id}:good-deposit:{time.time_ns()}")
    completed = cast(
        root,
        args,
        [
            "send",
            escrow,
            "depositFor(address,uint256,bytes32,string)",
            args.wallet,
            str(amount),
            deposit_id,
            "fund button preflight",
            "--value",
            str(amount),
            "--rpc-url",
            container_rpc_url(args.project_name, args.run_id),
            "--private-key",
            PAYER_PRIVATE_KEY,
            "--json",
        ],
        use_network=True,
    )
    tx_hash = parse_tx_hash(completed.stdout + "\n" + completed.stderr)
    print(f"OK: exact-value depositFor succeeded: tx={tx_hash}, depositId={deposit_id}")
    return tx_hash, deposit_id


def assert_deposited_units(root: Path, args: argparse.Namespace, escrow: str, expected: int) -> None:
    completed = cast(
        root,
        args,
        [
            "call",
            escrow,
            "depositedUnits(address)(uint256)",
            args.wallet,
            "--rpc-url",
            container_rpc_url(args.project_name, args.run_id),
        ],
        use_network=True,
    )
    actual = parse_uint(completed.stdout)
    if actual != expected:
        raise SmokeFailure(f"depositedUnits({args.wallet}) mismatch: expected {expected}, got {actual}")
    print(f"OK: depositedUnits({args.wallet}) == {actual}")


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


def assert_duplicate_deposit_fails(root: Path, args: argparse.Namespace, escrow: str, amount: int, deposit_id: str) -> None:
    assert_revert(
        root,
        args,
        [
            "send",
            escrow,
            "depositFor(address,uint256,bytes32,string)",
            args.wallet,
            str(amount),
            deposit_id,
            "duplicate should fail",
            "--value",
            str(amount),
            "--rpc-url",
            container_rpc_url(args.project_name, args.run_id),
            "--private-key",
            PAYER_PRIVATE_KEY,
        ],
        "duplicate deposit id",
    )


def assert_value_mismatch_fails(root: Path, args: argparse.Namespace, escrow: str, amount: int) -> None:
    bad_id = deterministic_bytes32(f"{args.run_id}:bad-value:{time.time_ns()}")
    assert_revert(
        root,
        args,
        [
            "send",
            escrow,
            "depositFor(address,uint256,bytes32,string)",
            args.wallet,
            str(amount),
            bad_id,
            "bad value should fail",
            "--value",
            str(amount - 1),
            "--rpc-url",
            container_rpc_url(args.project_name, args.run_id),
            "--private-key",
            PAYER_PRIVATE_KEY,
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
    text = completed.stdout
    lower_text = text.lower()
    if topic.lower() not in lower_text:
        raise SmokeFailure(f"Receipt did not contain CreditDeposited topic {topic}")

    try:
        receipt = parse_json_object(text)
        logs = receipt.get("logs") or []
        matched = False
        for log in logs:
            address = str(log.get("address") or "").lower()
            topics = [str(t).lower() for t in (log.get("topics") or [])]
            if address == escrow.lower() and topics and topics[0] == topic.lower():
                matched = True
                break
        if not matched:
            raise SmokeFailure("Receipt JSON contained topic text, but no log matched escrow address + topic[0]")
    except SmokeFailure:
        if escrow.lower() not in lower_text:
            raise

    print("OK: receipt contains CreditDeposited log from the escrow contract")


def cleanup_chain(args: argparse.Namespace) -> None:
    print("\nCleaning up Docker chain container/network...")
    run([docker_executable(), "rm", "-f", container_name(args.project_name, args.run_id)], check=False)
    run([docker_executable(), "network", "rm", network_name(args.project_name, args.run_id)], check=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke-test HubCreditBridgeEscrow before wiring the Worker Fund button.")
    parser.add_argument("--run-id", default="fund-button-preflight")
    parser.add_argument("--project-name", default=DEFAULT_PROJECT_NAME)
    parser.add_argument("--host-rpc-url", default=DEFAULT_HOST_RPC_URL)
    parser.add_argument("--chain-id", type=int, default=DEFAULT_CHAIN_ID)
    parser.add_argument("--foundry-image", default=FOUNDRY_IMAGE)
    parser.add_argument("--port-strategy", choices=("replace-project", "replace-any", "auto", "fail"), default="replace-project")
    parser.add_argument("--bridge-controller", default=DEPLOYER_ADDRESS)
    parser.add_argument("--wallet", default=PAYER_ADDRESS)
    parser.add_argument("--amount-wei", type=int, default=AMOUNT_WEI)
    parser.add_argument("--skip-python-tests", action="store_true")
    parser.add_argument("--skip-forge-tests", action="store_true")
    parser.add_argument("--skip-reset", action="store_true", help="Use an already-running project Anvil container/network.")
    parser.add_argument("--cleanup", action="store_true", help="Remove the dev-chain Docker container/network at the end.")
    parser.add_argument("--command-timeout-s", type=float, default=180.0)
    parser.add_argument("--reset-timeout-s", type=float, default=180.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = repo_root()

    print(f"Repository root: {root}")
    print(f"Foundry image: {args.foundry_image}")
    print(f"Run id: {args.run_id}")
    print(f"Host RPC: {args.host_rpc_url}")
    print(f"Container RPC: {container_rpc_url(args.project_name, args.run_id)}")
    print(f"Docker network: {network_name(args.project_name, args.run_id)}")
    print(f"Docker container: {container_name(args.project_name, args.run_id)}")

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
            assert_rpc_chain(args.host_rpc_url, args.chain_id)

        event_topic = assert_event_topic(root, args)
        escrow = deploy_escrow(root, args)
        tx_hash, deposit_id = send_good_deposit(root, args, escrow, args.amount_wei)
        assert_deposited_units(root, args, escrow, args.amount_wei)
        assert_duplicate_deposit_fails(root, args, escrow, args.amount_wei, deposit_id)
        assert_value_mismatch_fails(root, args, escrow, args.amount_wei)
        assert_receipt_has_credit_deposited(root, args, tx_hash, escrow, event_topic)

        print("\nSMOKE PASS")
        print(f"Escrow: {escrow}")
        print(f"Good deposit tx: {tx_hash}")
        print(f"Good deposit id: {deposit_id}")
        print("\nWhat this proves:")
        print("- Python-side bridge/import/UI assumptions still pass.")
        print("- HubCreditBridgeEscrow Foundry tests pass.")
        print("- The funding contract ABI is payable native depositFor, not ERC-20 approval.")
        print("- Project Anvil is started through the Docker-based dev-chain tool.")
        print("- Foundry deploy/cast operations work from Docker against the Anvil Docker network.")
        print("- Exact native-value deposit succeeds.")
        print("- Duplicate deposit id fails.")
        print("- Value mismatch fails.")
        print("- The deposit receipt contains CreditDeposited from the escrow contract.")
        print("- dev-chain-reset.py still lacks canonical HubCreditBridgeEscrow deployment wiring.")

        return 0
    except Exception as exc:
        print(f"\nSMOKE FAIL: {exc}", file=sys.stderr)
        return 1
    finally:
        if args.cleanup:
            cleanup_chain(args)


if __name__ == "__main__":
    raise SystemExit(main())