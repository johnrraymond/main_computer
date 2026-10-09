#!/usr/bin/env python3
"""Genesis-funded native reserve smoke on STOCK Besu QBFT, no custom client.

Birth four validator/officer wallets and a deployer with initial balances,
put every remaining uint256 wei into predeployed XLagBridgeReserve, then
prove the existing captain-propose / beta-officer-second / execute path
releases 20 native to a new recipient on every validator and RPC node.

Note: XLagBridgeReserve payout authorization is captain + beta second (not
three-of-four). The three-of-four vote is used for office RESET only.
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
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import smoke_besu_qbft_one_validator as qbft
import genesis_native_reserve as reserve

DEFAULT_IMAGE = "hyperledger/besu:26.8.1"
DEFAULT_AMOUNT_NATIVE = "20"
DEFAULT_RECIPIENT = qbft.DEFAULT_FUNDED_ACCOUNTS[1]
DEFAULT_GAS_LIMIT = 500_000
DEFAULT_GAS_PRICE_WEI = 10_000_000_000
DEFAULT_EXPIRY_LEAD_BLOCKS = 100
DEFAULT_CAPTAIN_NATIVE = "10000"
DEFAULT_DEPLOYER_NATIVE = "10000"


class NativeReserveSmokeError(RuntimeError):
    pass


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def native_to_wei(value: str) -> int:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid native amount: {value!r}") from exc
    result = amount * reserve.WEI_PER_NATIVE
    if not amount.is_finite() or amount <= 0 or result != result.to_integral_value():
        raise ValueError("Native amount must be positive, finite and have at most 18 decimals")
    integer = int(result)
    if not 0 < integer <= reserve.MAX_UINT256:
        raise ValueError("Native amount exceeds uint256")
    return integer


def compile_reserve(runtime_dir: Path, *, foundry_image: str) -> Path:
    """Compile only the existing contract into an isolated Foundry workspace.

    No custom Besu build, no contracts deployed as transactions. Genesis
    allocation uses this artifact's runtime bytecode and storage layout.
    """
    workspace = runtime_dir / "reserve-foundry"
    src = workspace / "src"
    src.mkdir(parents=True, exist_ok=True)
    source = repo_root() / "contracts" / "src" / "XLagBridgeReserve.sol"
    if not source.exists():
        raise NativeReserveSmokeError(f"Existing reserve contract not found: {source}")
    shutil.copy2(source, src / source.name)
    (workspace / "foundry.toml").write_text(
        '[profile.default]\nsrc = "src"\nout = "out"\n'
        'solc_version = "0.8.24"\noptimizer = true\noptimizer_runs = 200\n',
        encoding="utf-8",
    )
    cmd = [
        "docker", "run", "--rm", "--entrypoint", "sh",
        "-v", f"{workspace.resolve()}:/work", "-w", "/work",
        foundry_image, "-lc", "forge build --extra-output storageLayout",
    ]
    print("RESERVE_FORGE_BUILD: " + " ".join(cmd))
    completed = subprocess.run(cmd, cwd=repo_root(), text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if completed.returncode:
        raise NativeReserveSmokeError(
            f"Foundry could not compile XLagBridgeReserve (exit={completed.returncode}):\n"
            + (completed.stdout or "")[-4000:]
        )
    artifact = workspace / "out" / "XLagBridgeReserve.sol" / "XLagBridgeReserve.json"
    if not artifact.exists():
        raise NativeReserveSmokeError(f"Foundry did not produce reserve artifact: {artifact}")
    # Check the layout now, before tearing down any existing local smoke lab.
    compiled = json.loads(artifact.read_text(encoding="utf-8"))
    reserve.checked_layout(compiled)
    reserve.contract_runtime(compiled)
    return artifact


def _parse_quantity(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 16) if value.startswith("0x") else int(value)
    raise NativeReserveSmokeError(f"Invalid RPC quantity: {value!r}")


def _rpc_urls(metadata: dict[str, Any]) -> list[str]:
    validators = metadata.get("validator_rpc_urls") or metadata.get("rpc_urls")
    fullnode = metadata.get("public_rpc_url")
    if not isinstance(validators, list) or len(validators) != 4 or not isinstance(fullnode, str):
        raise NativeReserveSmokeError("Missing four validator RPCs and the full-node RPC")
    return [*validators, fullnode]


def _key(runtime_dir: Path, validator: dict[str, Any]) -> str:
    path = runtime_dir / str(validator["data_dir"]) / "key"
    value = path.read_text(encoding="utf-8").strip()
    value = value if value.startswith("0x") else "0x" + value
    if not re.fullmatch(r"0x[0-9a-fA-F]{64}", value):
        raise NativeReserveSmokeError(f"Invalid generated validator key at {path}")
    return value


def balance(url: str, account: str) -> int:
    return _parse_quantity(qbft.rpc_call(url, "eth_getBalance", [account, "latest"]))


def contract_uint(url: str, contract: str, signature: str) -> int:
    selector = reserve.keccak256(signature.encode("ascii"))[:4]
    data = qbft.rpc_call(url, "eth_call", [{"to": contract, "data": "0x" + selector.hex()}, "latest"])
    return _parse_quantity(data)


def signed_contract_call(*, foundry_image: str, private_key: str, signer_index: int,
                         contract: str, signature: str, arguments: list[str]) -> dict[str, Any]:
    env = os.environ.copy()
    env["RESERVE_PRIVATE_KEY"] = private_key
    env["RESERVE_RPC_URL"] = f"http://{qbft.validator_container(signer_index)}:{qbft.RPC_CONTAINER_PORT}"
    env["RESERVE_ADDRESS"] = contract
    # Each ABI argument is a distinct positional environment expansion, so the
    # memo (which may contain spaces) remains one argument.
    for index, argument in enumerate(arguments):
        env[f"RESERVE_ARG_{index}"] = argument
    arg_shell = " ".join(f'"$RESERVE_ARG_{i}"' for i in range(len(arguments)))
    shell = (
        f'exec cast send "$RESERVE_ADDRESS" "{signature}" {arg_shell} '
        '--private-key "$RESERVE_PRIVATE_KEY" '
        '--rpc-url "$RESERVE_RPC_URL" --legacy '
        f'--gas-limit {DEFAULT_GAS_LIMIT} --gas-price {DEFAULT_GAS_PRICE_WEI} --json'
    )
    cmd = ["docker", "run", "--rm", "--network", qbft.DOCKER_NETWORK,
           "--entrypoint", "sh", "-e", "RESERVE_PRIVATE_KEY", "-e", "RESERVE_RPC_URL",
           "-e", "RESERVE_ADDRESS"]
    for i in range(len(arguments)):
        cmd.extend(["-e", f"RESERVE_ARG_{i}"])
    cmd.extend([foundry_image, "-lc", shell])
    print(f"RESERVE_TX: officer={signer_index} method={signature}")
    completed = subprocess.run(cmd, cwd=repo_root(), env=env, text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if completed.returncode:
        raise NativeReserveSmokeError(
            f"{signature} transaction failed exit={completed.returncode}:\n{(completed.stdout or '')[-3500:]}"
        )
    try:
        receipt = json.loads((completed.stdout or "").strip())
    except json.JSONDecodeError as exc:
        raise NativeReserveSmokeError(f"Invalid cast receipt: {(completed.stdout or '')[-1500:]}") from exc
    if _parse_quantity(receipt.get("status")) != 1:
        raise NativeReserveSmokeError(f"{signature} reverted: {receipt}")
    return receipt


def wait_height(urls: list[str], height: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    observations = []
    while time.monotonic() < deadline:
        observations = []
        for url in urls:
            try:
                observations.append(_parse_quantity(qbft.rpc_call(url, "eth_blockNumber")))
            except Exception:
                observations.append(-1)
        if all(n >= height for n in observations):
            return
        time.sleep(0.5)
    raise NativeReserveSmokeError(f"Nodes did not reach block {height}; observed={observations}")


def balance_convergence(urls: list[str], account: str, expected: int, timeout: float) -> list[int]:
    deadline = time.monotonic() + timeout
    values: list[int] = []
    while time.monotonic() < deadline:
        try:
            values = [balance(url, account) for url in urls]
        except Exception:
            values = []
        if len(values) == len(urls) and all(value == expected for value in values):
            return values
        time.sleep(0.5)
    raise NativeReserveSmokeError(f"{account}: expected balance {expected}, got {values}")


def block_consensus(urls: list[str], block_number: int) -> tuple[str, str]:
    blocks = [qbft.rpc_call(url, "eth_getBlockByNumber", [hex(block_number), False]) for url in urls]
    hashes = {block["hash"] for block in blocks}
    roots = {block["stateRoot"] for block in blocks}
    if len(hashes) != 1 or len(roots) != 1:
        raise NativeReserveSmokeError(f"Network diverged on payout block: hashes={hashes}, roots={roots}")
    return next(iter(hashes)), next(iter(roots))



def verify_genesis_contract(
    *, runtime_dir: Path, artifact_path: Path, urls: list[str],
    contract: str, officers: list[str], deployer: str,
    captain_wei: int, deployer_wei: int, reserve_wei: int,
    max_payout_wei: int, recipient: str,
) -> dict[str, Any]:
    """Prove the escrow was BORN at block 0, not subsequently deployed.

    Cross-check the compiler's *runtime* bytecode, the actual generated genesis
    allocation, and block-zero JSON-RPC on all four validators plus the RPC-only
    full node. Also check the constructor-equivalent storage via both raw slots
    and the contract's view methods. No transaction is submitted here.
    """
    genesis_path = runtime_dir / "genesis.json"
    genesis = json.loads(genesis_path.read_text(encoding="utf-8"))
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    reserve.checked_layout(artifact)
    expected_code = reserve.contract_runtime(artifact)
    alloc = genesis.get("alloc")
    if not isinstance(alloc, dict):
        raise NativeReserveSmokeError("Generated genesis has no alloc")
    entry = alloc.get(contract.lower()[2:])
    if not isinstance(entry, dict):
        raise NativeReserveSmokeError("Escrow contract is not allocated at genesis")
    if str(entry.get("code", "")).lower() != expected_code:
        raise NativeReserveSmokeError("Genesis escrow runtime does not match compiled XLagBridgeReserve")
    if _parse_quantity(entry.get("balance")) != reserve_wei:
        raise NativeReserveSmokeError("Genesis escrow allocation has an incorrect reserve balance")

    initial_balances = {office: captain_wei for office in officers}
    if deployer in initial_balances:
        if deployer_wei != captain_wei:
            raise NativeReserveSmokeError("Overlapping deployer/officer has mismatched funding")
    else:
        initial_balances[deployer] = deployer_wei
    initial_balances[contract] = reserve_wei
    if sum(initial_balances.values()) != reserve.MAX_UINT256:
        raise NativeReserveSmokeError("Genesis allocations do not total MAX_UINT256")
    for address, expected in initial_balances.items():
        account = alloc.get(address.lower()[2:])
        if not isinstance(account, dict) or _parse_quantity(account.get("balance")) != expected:
            raise NativeReserveSmokeError(f"Incorrect genesis funding for {address}")

    # Constructor-equivalent storage. Slot 4 itself is the mapping declaration;
    # each address's entry is stored at keccak256(pad(address) || pad(4)).
    expected_slots = {reserve.word(i): int(office, 16) for i, office in enumerate(officers)}
    for i, office in enumerate(officers):
        expected_slots[reserve.word(reserve.mapping_slot(office, 4))] = i + 1
    expected_slots[reserve.word(8)] = max_payout_wei
    expected_slots[reserve.word(9)] = 0  # both uint64 deployment delays are zero
    expected_slots[reserve.word(10)] = 1  # nextProposalId constructor/field initializer
    storage = entry.get("storage")
    if not isinstance(storage, dict):
        raise NativeReserveSmokeError("Genesis escrow is missing constructor storage")
    for slot, expected_value in expected_slots.items():
        if _parse_quantity(storage.get(slot)) != expected_value:
            raise NativeReserveSmokeError(f"Genesis contract storage mismatch at {slot}")

    observed_hashes: set[str] = set()
    observed_roots: set[str] = set()
    for node_index, url in enumerate(urls, start=1):
        block_zero = qbft.rpc_call(url, "eth_getBlockByNumber", ["0x0", False])
        if not isinstance(block_zero, dict) or _parse_quantity(block_zero.get("number")) != 0:
            raise NativeReserveSmokeError(f"node {node_index}: genesis block unavailable")
        if block_zero.get("transactions") != []:
            raise NativeReserveSmokeError(f"node {node_index}: genesis unexpectedly has deployment transactions")
        if not isinstance(block_zero.get("hash"), str) or not isinstance(block_zero.get("stateRoot"), str):
            raise NativeReserveSmokeError(f"node {node_index}: malformed genesis block proof")
        observed_hashes.add(block_zero["hash"].lower())
        observed_roots.add(block_zero["stateRoot"].lower())

        code_at_zero = qbft.rpc_call(url, "eth_getCode", [contract, "0x0"])
        if not isinstance(code_at_zero, str) or code_at_zero.lower() != expected_code:
            raise NativeReserveSmokeError(f"node {node_index}: block-0 escrow bytecode != compiled runtime")
        for slot, expected_value in expected_slots.items():
            # eth_getStorageAt expects a JSON-RPC QUANTITY (no leading zeros),
            # while genesis alloc requires 32-byte padded storage keys.
            actual = qbft.rpc_call(url, "eth_getStorageAt", [contract, hex(int(slot, 16)), "0x0"])
            if _parse_quantity(actual) != expected_value:
                raise NativeReserveSmokeError(
                    f"node {node_index}: block-0 storage slot {slot} expected {expected_value}, got {actual}"
                )
        for address, expected in initial_balances.items():
            actual = _parse_quantity(qbft.rpc_call(url, "eth_getBalance", [address, "0x0"]))
            if actual != expected:
                raise NativeReserveSmokeError(
                    f"node {node_index}: block-0 balance {address} expected {expected}, got {actual}"
                )
        if _parse_quantity(qbft.rpc_call(url, "eth_getBalance", [recipient, "0x0"])) != 0:
            raise NativeReserveSmokeError(f"node {node_index}: payout recipient already funded at genesis")

        # The actual EVM executes constructor-initialized getters at block zero.
        def call(signature: str) -> bytes:
            selector = reserve.keccak256(signature.encode("ascii"))[:4]
            data = qbft.rpc_call(
                url, "eth_call", [{"to": contract, "data": "0x" + selector.hex()}, "0x0"]
            )
            if not isinstance(data, str) or not data.startswith("0x"):
                raise NativeReserveSmokeError(f"node {node_index}: bad genesis eth_call {signature}")
            return bytes.fromhex(data[2:])

        for signature, expected in (
            ("maxPayoutWei()", max_payout_wei),
            ("nextProposalId()", 1),
            ("payoutDelayBlocks()", 0),
            ("resetDelayBlocks()", 0),
        ):
            result = call(signature)
            if len(result) != 32 or int.from_bytes(result, "big") != expected:
                raise NativeReserveSmokeError(f"node {node_index}: block-0 getter {signature} incorrect")
        offices_result = call("getOffices()")
        if len(offices_result) != 128:
            raise NativeReserveSmokeError(f"node {node_index}: block-0 getOffices() returned wrong shape")
        returned_offices = [
            "0x" + offices_result[i * 32 + 12 : (i + 1) * 32].hex()
            for i in range(4)
        ]
        if returned_offices != officers:
            raise NativeReserveSmokeError(f"node {node_index}: block-0 getOffices() mismatches genesis")

    if len(observed_hashes) != 1 or len(observed_roots) != 1:
        raise NativeReserveSmokeError(
            f"Genesis block differs between nodes: hashes={observed_hashes}, stateRoots={observed_roots}"
        )
    return {
        "contract_predeployed_at_block_zero": True,
        "genesis_contract_runtime_matches_compiler": True,
        "genesis_contract_code_bytes": (len(expected_code) - 2) // 2,
        "genesis_contract_code_keccak256": "0x" + reserve.keccak256(bytes.fromhex(expected_code[2:])).hex(),
        "genesis_json_sha256": hashlib.sha256(genesis_path.read_bytes()).hexdigest(),
        "genesis_block_hash": next(iter(observed_hashes)),
        "genesis_state_root": next(iter(observed_roots)),
        "genesis_block_transactions": 0,
        "constructor_storage_slots_verified_per_node": len(expected_slots),
        "constructor_getters_verified": True,
        "genesis_nodes_verified": len(urls),
        "genesis_supply_exactly_max_uint256": True,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Genesis-funded uint256 native reserve smoke on stock Besu QBFT.")
    parser.add_argument("--amount-native", default=DEFAULT_AMOUNT_NATIVE)
    parser.add_argument("--captain-native", default=DEFAULT_CAPTAIN_NATIVE,
                        help="Each of the four office wallets receives this many native units at birth.")
    parser.add_argument("--deployer-native", default=DEFAULT_DEPLOYER_NATIVE)
    parser.add_argument("--recipient", default=DEFAULT_RECIPIENT)
    parser.add_argument("--image", default=DEFAULT_IMAGE, help="Stock Besu image, never a custom mint client.")
    parser.add_argument("--foundry-image", default=qbft.DEFAULT_FOUNDRY_IMAGE)
    parser.add_argument("--runtime-dir", default=str(qbft.DEFAULT_RUNTIME_DIR))
    parser.add_argument("--docker-subnet", default="10.241.0.0/24")
    parser.add_argument("--chain-id", type=int, default=qbft.DEFAULT_CHAIN_ID)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--expiry-lead-blocks", type=int, default=DEFAULT_EXPIRY_LEAD_BLOCKS)
    parser.add_argument("--reserve-address", default=reserve.DEFAULT_RESERVE_ADDRESS)
    parser.add_argument("--skip-build", action="store_true",
                        help="Compatibility: skip Forge rebuild only if the compiled genesis artifact already exists.")
    return parser.parse_args(argv)


def execute(args: argparse.Namespace) -> dict[str, Any]:
    native_amount_wei = native_to_wei(args.amount_native)
    captain_wei = native_to_wei(args.captain_native)
    deployer_wei = native_to_wei(args.deployer_native)
    recipient = reserve.address(args.recipient)
    reserve_address = reserve.address(args.reserve_address)
    if args.expiry_lead_blocks < 10 or args.expiry_lead_blocks > (1 << 32):
        raise NativeReserveSmokeError("Expiry lead must be between 10 and 2**32 blocks")
    runtime_dir = (Path(args.runtime_dir) if Path(args.runtime_dir).is_absolute()
                   else repo_root() / args.runtime_dir).resolve()
    artifact = runtime_dir / "reserve-foundry" / "out" / "XLagBridgeReserve.sol" / "XLagBridgeReserve.json"
    if args.skip_build:
        if not artifact.exists():
            raise NativeReserveSmokeError(f"--skip-build requires existing contract artifact: {artifact}")
        reserve.checked_layout(json.loads(artifact.read_text(encoding="utf-8")))
    else:
        artifact = compile_reserve(runtime_dir, foundry_image=args.foundry_image)

    qbft_args = qbft.parse_args([
        "restart", "--image", args.image, "--runtime-dir", str(runtime_dir),
        "--docker-subnet", args.docker_subnet, "--chain-id", str(args.chain_id),
        "--timeout-seconds", str(args.timeout_seconds),
        "--genesis-native-reserve", "--genesis-reserve-artifact", str(artifact),
        "--genesis-reserve-address", reserve_address,
        "--genesis-captain-balance-wei", str(captain_wei),
        "--genesis-deployer-balance-wei", str(deployer_wei),
        "--genesis-reserve-max-payout-wei", str(native_amount_wei),
    ])
    if qbft.restart(qbft_args) != 0:
        raise NativeReserveSmokeError("Stock Besu raw QBFT lab failed to start")
    metadata = qbft.load_metadata(runtime_dir)
    if not isinstance(metadata, dict):
        raise NativeReserveSmokeError("Smoke metadata was not written")
    profile = metadata.get("genesis_native_reserve", {})
    if not profile.get("enabled"):
        raise NativeReserveSmokeError("Genesis reserve was not enabled")
    validators = metadata.get("validators", [])
    if len(validators) != 4:
        raise NativeReserveSmokeError("Expected exactly four QBFT validators")
    office_addresses = [reserve.address(val["address"]) for val in validators]
    if office_addresses != profile["offices"]:
        raise NativeReserveSmokeError("Genesis officers differ from active validators")
    urls = _rpc_urls(metadata)
    expected_initial_reserve = int(profile["initial_reserve_wei"])
    if expected_initial_reserve != reserve.MAX_UINT256 - 4 * captain_wei - deployer_wei:
        raise NativeReserveSmokeError("Wrong exact-supply reserve genesis amount")
    genesis_proof = verify_genesis_contract(
        runtime_dir=runtime_dir, artifact_path=artifact, urls=urls,
        contract=reserve_address, officers=office_addresses,
        deployer=qbft.DEFAULT_FUNDED_ACCOUNTS[0],
        captain_wei=captain_wei, deployer_wei=deployer_wei,
        reserve_wei=expected_initial_reserve, max_payout_wei=native_amount_wei,
        recipient=recipient,
    )
    print(
        "GENESIS_ESCROW_PREDEPLOY_OK: "
        f"block={genesis_proof['genesis_block_hash']} "
        f"code_keccak256={genesis_proof['genesis_contract_code_keccak256']} "
        f"nodes={genesis_proof['genesis_nodes_verified']} "
        f"verified_storage_slots_per_node={genesis_proof['constructor_storage_slots_verified_per_node']}"
    )
    # Assert balances on all 4 validators and the RPC-only node, not just genesis JSON.
    for address in office_addresses:
        balance_convergence(urls, address, captain_wei, args.timeout_seconds)
    balance_convergence(urls, reserve_address, expected_initial_reserve, args.timeout_seconds)
    balance_convergence(urls, qbft.DEFAULT_FUNDED_ACCOUNTS[0], deployer_wei, args.timeout_seconds)
    before_recipient = balance(urls[-1], recipient)
    if before_recipient != 0:
        raise NativeReserveSmokeError(f"Recipient must start unfunded, got {before_recipient}")
    if contract_uint(urls[-1], reserve_address, "maxPayoutWei()") != native_amount_wei:
        raise NativeReserveSmokeError("Predeployed reserve constructor maxPayout storage mismatch")
    if contract_uint(urls[-1], reserve_address, "nextProposalId()") != 1:
        raise NativeReserveSmokeError("Predeployed reserve constructor nextProposalId storage mismatch")

    now = max(_parse_quantity(qbft.rpc_call(u, "eth_blockNumber")) for u in urls)
    expiry = now + args.expiry_lead_blocks
    captain_key = _key(runtime_dir, validators[0])
    second_key = _key(runtime_dir, validators[2])  # index 2 = SECOND_OFFICER, a permitted beta second
    executor_key = _key(runtime_dir, validators[1])  # index 1 = FIRST_OFFICER, non-approver of payout

    proposed = signed_contract_call(
        foundry_image=args.foundry_image, private_key=captain_key, signer_index=1,
        contract=reserve_address, signature="proposePayout(address,uint256,string,uint64)",
        arguments=[recipient, str(native_amount_wei), "genesis reserve smoke", str(expiry)],
    )
    wait_height(urls, _parse_quantity(proposed["blockNumber"]), args.timeout_seconds)
    proposal_id = contract_uint(urls[-1], reserve_address, "nextProposalId()") - 1
    if proposal_id != 1:
        raise NativeReserveSmokeError(f"Unexpected first proposal id {proposal_id}")
    balance_convergence(urls, recipient, before_recipient, args.timeout_seconds)
    balance_convergence(urls, reserve_address, expected_initial_reserve, args.timeout_seconds)

    seconded = signed_contract_call(
        foundry_image=args.foundry_image, private_key=second_key, signer_index=3,
        contract=reserve_address, signature="secondPayout(uint256)", arguments=[str(proposal_id)],
    )
    wait_height(urls, _parse_quantity(seconded["blockNumber"]), args.timeout_seconds)
    balance_convergence(urls, recipient, before_recipient, args.timeout_seconds)
    balance_convergence(urls, reserve_address, expected_initial_reserve, args.timeout_seconds)

    executed = signed_contract_call(
        foundry_image=args.foundry_image, private_key=executor_key, signer_index=2,
        contract=reserve_address, signature="executePayout(uint256)", arguments=[str(proposal_id)],
    )
    payout_block = _parse_quantity(executed["blockNumber"])
    wait_height(urls, payout_block, args.timeout_seconds)
    recipient_balances = balance_convergence(urls, recipient, before_recipient + native_amount_wei, args.timeout_seconds)
    reserve_balances = balance_convergence(urls, reserve_address, expected_initial_reserve - native_amount_wei, args.timeout_seconds)
    payout_hash, payout_state_root = block_consensus(urls, payout_block)
    return {
        "ok": True,
        "schema": "main-computer-genesis-native-reserve-smoke.v1",
        "mechanism": "stock-Besu-genesis-funded-XLagBridgeReserve-transfer",
        "genesis_predeployment": genesis_proof,
        "besu_image": args.image,
        "chain_id": args.chain_id,
        "max_uint256_supply_wei": str(reserve.MAX_UINT256),
        "captain_and_each_officer_initial_wei": captain_wei,
        "officers": office_addresses,
        "officers_funded": 4,
        "deployer": qbft.DEFAULT_FUNDED_ACCOUNTS[0],
        "deployer_initial_wei": deployer_wei,
        "reserve": reserve_address,
        "reserve_genesis_wei": str(expected_initial_reserve),
        "reserve_after_wei": str(expected_initial_reserve - native_amount_wei),
        "recipient": recipient,
        "recipient_before_wei": before_recipient,
        "recipient_after_wei": before_recipient + native_amount_wei,
        "released_native": str(args.amount_native),
        "proposal_id": proposal_id,
        "captain_proposed": True,
        "beta_officer_seconded": True,
        "first_officer_executed_without_seconding": True,
        "recipient_balances_all_nodes": recipient_balances,
        "reserve_balances_all_nodes": [str(v) for v in reserve_balances],
        "payout_block": payout_block,
        "payout_block_hash": payout_hash,
        "payout_state_root": payout_state_root,
        "all_nodes_converged": True,
        "new_native_units_minted_after_genesis": 0,
        "custom_besu_image_required": False,
        "mother": "none",
        "coolify": "none",
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(sys.argv[1:] if argv is None else argv))
    print("QBFT_GENESIS_NATIVE_RESERVE_SMOKE")
    print(f"stock_besu_image={args.image}")
    print(f"native_release_amount={args.amount_native}")
    print("four_officers_each_funded_equal_to_captain=yes")
    print("deployer_separately_funded=yes")
    print("native_release_rule=captain-propose, beta-second, execute")
    try:
        result = execute(args)
    except Exception as exc:
        print(f"QBFT_GENESIS_NATIVE_RESERVE_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    print("QBFT_GENESIS_NATIVE_RESERVE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
