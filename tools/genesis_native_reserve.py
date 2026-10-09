"""Construct a stock-Besu genesis allocation for the existing XLagBridgeReserve.

This does NOT deploy anything or mint after birth. It predeploys runtime EVM bytecode
and replicates the contract constructor's storage initialization using Forge's
compiler-reported storage layout (fail-closed when the layout changes).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MAX_UINT256 = (1 << 256) - 1
WEI_PER_NATIVE = 10**18
DEFAULT_RESERVE_ADDRESS = "0x000000000000000000000000000000000000c0de"
ROUND_CONSTANTS = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)
ROTATIONS = (
    (0, 36, 3, 41, 18), (1, 44, 10, 45, 2), (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56), (27, 20, 39, 8, 14),
)
MASK64 = (1 << 64) - 1


def keccak256(data: bytes) -> bytes:
    """Ethereum Keccak-256 (not NIST SHA3-256), stdlib-only for genesis mapping slots."""
    rate = 136
    padding = bytearray(data)
    padding.append(0x01)
    while len(padding) % rate != rate - 1:
        padding.append(0)
    padding.append(0x80)
    lanes = [0] * 25
    for start in range(0, len(padding), rate):
        block = padding[start : start + rate]
        for i in range(rate // 8):
            lanes[i] ^= int.from_bytes(block[i * 8 : i * 8 + 8], "little")
        for rc in ROUND_CONSTANTS:
            c = [lanes[x] ^ lanes[x + 5] ^ lanes[x + 10] ^ lanes[x + 15] ^ lanes[x + 20] for x in range(5)]
            for x in range(5):
                d = c[(x - 1) % 5] ^ (((c[(x + 1) % 5] << 1) | (c[(x + 1) % 5] >> 63)) & MASK64)
                for y in range(5):
                    lanes[x + 5 * y] ^= d
            b = [0] * 25
            for x in range(5):
                for y in range(5):
                    v = lanes[x + 5 * y]
                    shift = ROTATIONS[x][y]
                    b[y + 5 * ((2 * x + 3 * y) % 5)] = ((v << shift) | (v >> (64 - shift))) & MASK64 if shift else v
            for x in range(5):
                for y in range(5):
                    lanes[x + 5 * y] = b[x + 5 * y] ^ ((~b[(x + 1) % 5 + 5 * y]) & b[(x + 2) % 5 + 5 * y])
            lanes[0] ^= rc
    return b"".join(x.to_bytes(8, "little") for x in lanes)[:32]


def word(n: int) -> str:
    if not 0 <= n <= MAX_UINT256:
        raise ValueError(f"storage uint256 out of range: {n}")
    return "0x" + n.to_bytes(32, "big").hex()


def address(value: str) -> str:
    h = str(value).lower().removeprefix("0x")
    if len(h) != 40:
        raise ValueError(f"Expected 20-byte address: {value!r}")
    int(h, 16)
    return "0x" + h


def mapping_slot(addr: str, slot: int) -> int:
    return int.from_bytes(keccak256(bytes.fromhex(address(addr)[2:]).rjust(32, b"\0") + slot.to_bytes(32, "big")), "big")


def checked_layout(artifact: dict[str, Any]) -> dict[str, dict[str, Any]]:
    layout = artifact.get("storageLayout", {}).get("storage", [])
    fields = {entry["label"]: entry for entry in layout}
    # Solidity storage layout is an external interface for genesis allocations.
    # Verify every constructor-initialized location before writing one byte.
    expected = {
        "_offices": (0, 0),
        "officeIndexPlusOne": (4, 0),
        "maxPayoutWei": (8, 0),
        "payoutDelayBlocks": (9, 0),
        "resetDelayBlocks": (9, 8),
        "nextProposalId": (10, 0),
    }
    for name, (slot, offset) in expected.items():
        item = fields.get(name)
        if item is None or int(item["slot"]) != slot or int(item["offset"]) != offset:
            raise ValueError(f"XLagBridgeReserve storage layout changed at {name}: expected slot={slot} offset={offset}, got {item}")
    return fields


def contract_runtime(artifact: dict[str, Any]) -> str:
    deployed = artifact.get("deployedBytecode")
    code = deployed.get("object") if isinstance(deployed, dict) else deployed
    if not isinstance(code, str) or not code.startswith("0x") or len(code) < 20:
        raise ValueError("compiled XLagBridgeReserve deployedBytecode missing")
    if "__" in code or len(code) % 2:
        raise ValueError("unlinked or invalid XLagBridgeReserve deployedBytecode")
    bytes.fromhex(code[2:])
    return code.lower()


def reserve_allocation(
    *, artifact: dict[str, Any], captain: str, deployer: str,
    first_officer: str, second_officer: str, third_officer: str,
    initial_captain_wei: int, initial_deployer_wei: int,
    max_payout_wei: int, payout_delay_blocks: int = 0,
    reset_delay_blocks: int = 0,
    reserve_address: str = DEFAULT_RESERVE_ADDRESS,
    hub_admin_addresses: tuple[str, ...] = (),
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Seed the exact maximum uint256 supply, with each office funded equally.

    Genesis transfers are not runtime minting. The stock EVM can only release
    pre-funded native units from this contract. The four distinct officer wallets
    receive the captain's amount; the deployer receives its configured amount.
    """
    checked_layout(artifact)
    code = contract_runtime(artifact)
    officers = [address(x) for x in (captain, first_officer, second_officer, third_officer)]
    if len(set(officers)) != 4 or any(int(x, 16) == 0 for x in officers):
        raise ValueError("All four nonzero reserve officer addresses must be distinct")
    reserve = address(reserve_address)
    deployer = address(deployer)
    if int(reserve, 16) == 0 or reserve in officers or reserve == deployer:
        raise ValueError("Reserve must not overlap any funded signer or zero address")
    if not 0 < initial_captain_wei < MAX_UINT256 or not 0 < initial_deployer_wei < MAX_UINT256:
        raise ValueError("Initial captain and deployer balances must be positive")
    if not 0 < max_payout_wei <= MAX_UINT256:
        raise ValueError("maxPayoutWei must be positive uint256")
    if not 0 <= payout_delay_blocks < 1 << 64 or not 0 <= reset_delay_blocks < 1 << 64:
        raise ValueError("Delay must fit uint64")

    funded = {office: initial_captain_wei for office in officers}
    if deployer in funded:
        if initial_deployer_wei != initial_captain_wei:
            raise ValueError("Overlapping deployer/officer must have the same genesis funding amount")
    else:
        funded[deployer] = initial_deployer_wei
    hubs = [address(item) for item in hub_admin_addresses]
    if len(set(hubs)) != len(hubs):
        raise ValueError("Duplicate Hub administrator genesis address")
    if any(int(item, 16) == 0 or item == reserve for item in hubs):
        raise ValueError("Hub administrator address cannot be zero or the escrow contract")
    if any(item in funded for item in hubs):
        raise ValueError("Hub administrator address overlaps an officer or deployer")
    funded.update({item: initial_captain_wei for item in hubs})
    allocated = sum(funded.values())
    remaining = MAX_UINT256 - allocated
    if remaining <= 0:
        raise ValueError("Initial funded accounts exhaust uint256 reserve")

    # XLagBridgeReserve constructor: address[4] _offices at slots 0..3,
    # officeIndexPlusOne mapping at slot 4; scalar values at slots 8..10.
    storage = {word(index): word(int(office, 16)) for index, office in enumerate(officers)}
    for index, office in enumerate(officers):
        storage[word(mapping_slot(office, 4))] = word(index + 1)
    storage[word(8)] = word(max_payout_wei)
    storage[word(9)] = word(payout_delay_blocks | (reset_delay_blocks << 64))
    storage[word(10)] = word(1)

    alloc: dict[str, dict[str, Any]] = {
        office[2:]: {"balance": hex(balance)} for office, balance in funded.items()
    }
    alloc[reserve[2:]] = {"balance": hex(remaining), "code": code, "storage": storage}
    if sum(int(record["balance"], 16) for record in alloc.values()) != MAX_UINT256:
        raise AssertionError("Genesis total native supply does not equal MAX_UINT256")
    profile = {
        "enabled": True,
        "mechanism": "genesis-funded-stock-besu-XLagBridgeReserve",
        "contract": reserve,
        "captain": officers[0],
        "deployer": deployer,
        "offices": officers,
        "initial_officer_wei_each": initial_captain_wei,
        "initial_deployer_wei": initial_deployer_wei,
        "hub_admin_addresses": hubs,
        "hub_admin_count": len(hubs),
        "initial_hub_admin_wei_each": initial_captain_wei,
        "initial_reserve_wei": remaining,
        "total_initial_supply_wei": str(MAX_UINT256),
        "max_payout_wei": max_payout_wei,
    }
    return alloc, profile


def seed(
    network_files: Path, *, artifact_path: Path,
    deployer_address: str, reserve_address: str,
    captain_balance_wei: int, deployer_balance_wei: int, max_payout_wei: int,
) -> dict[str, Any]:
    network_files = Path(network_files)
    genesis_path = network_files / "genesis.json"
    keys = sorted(x for x in (network_files / "keys").iterdir() if x.is_dir())
    if len(keys) != 4:
        raise ValueError(f"Expected four local QBFT validator keys; found {len(keys)}")
    offices = [address(x.name) for x in keys]
    artifact = json.loads(Path(artifact_path).read_text(encoding="utf-8"))
    alloc, profile = reserve_allocation(
        artifact=artifact, captain=offices[0], first_officer=offices[1],
        second_officer=offices[2], third_officer=offices[3],
        deployer=deployer_address,
        initial_captain_wei=captain_balance_wei,
        initial_deployer_wei=deployer_balance_wei,
        max_payout_wei=max_payout_wei,
        reserve_address=reserve_address,
    )
    genesis = json.loads(genesis_path.read_text(encoding="utf-8"))
    # Exact supply: never merge in old smoke prefunded accounts. This also
    # intentionally preserves the existing QBFT genesis config and extraData.
    genesis["alloc"] = alloc
    genesis_path.write_text(json.dumps(genesis, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return profile
