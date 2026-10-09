"""Genesis installation of HubCreditBridgeEscrow on ordinary Besu.

Genesis does not run constructors: install compiler runtime bytecode and
constructor-equivalent owner/controller storage directly in alloc.
"""
from __future__ import annotations

from typing import Any

from tools.genesis_native_reserve import address, contract_runtime, mapping_slot, word

DEFAULT_HUB_CREDIT_ESCROW_ADDRESS = "0x000000000000000000000000000000000000e5c0"


def checked_escrow_layout(artifact: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Reject stale/incompatible compiler layouts before seeding contract state.

    These are the slots of contracts/src/HubCreditBridgeEscrow.sol. In
    particular, bridgeControllers is a Solidity mapping, so a wrong slot
    could make the genesis-installed controller unable to operate the escrow.
    """
    raw = artifact.get("storageLayout", {}).get("storage", [])
    if not isinstance(raw, list):
        raise ValueError("HubCreditBridgeEscrow storage layout is missing")
    layout = {field.get("label"): field for field in raw if isinstance(field, dict)}
    for label, slot, offset in (
        ("owner", 0, 0),
        ("bridgeController", 1, 0),
        ("bridgeControllers", 2, 0),
        ("paused", 3, 0),
    ):
        item = layout.get(label)
        try:
            actual = (int(item["slot"]), int(item["offset"])) if item else None
        except (KeyError, TypeError, ValueError):
            actual = None
        if actual != (slot, offset):
            raise ValueError(
                f"HubCreditBridgeEscrow storage layout changed at {label}: "
                f"expected slot={slot} offset={offset}, got {item!r}"
            )
    return layout


def escrow_allocation(
    *, artifact: dict[str, Any], owner: str, bridge_controller: str,
    contract_address: str = DEFAULT_HUB_CREDIT_ESCROW_ADDRESS,
) -> tuple[dict[str, Any], dict[str, Any]]:
    owner = address(owner)
    bridge_controller = address(bridge_controller)
    contract_address = address(contract_address)
    if len({owner, bridge_controller, contract_address}) != 3 or any(
        int(item, 16) == 0 for item in (owner, bridge_controller, contract_address)
    ):
        raise ValueError("escrow owner, bridge controller, and contract must be distinct nonzero addresses")
    layout = checked_escrow_layout(artifact)
    runtime = contract_runtime(artifact)
    storage = {
        word(0): word(int(owner, 16)),
        word(1): word(int(bridge_controller, 16)),
        word(mapping_slot(bridge_controller, 2)): word(1),
    }
    # The constructor leaves paused/locked false, and all escrow records empty.
    result = {"balance": "0x0", "code": runtime, "storage": storage}
    profile = {
        "address": contract_address,
        "owner": owner,
        "bridge_controller": bridge_controller,
        "method": "genesis-alloc-predeploy",
        "storage_slot_count": len(storage),
    }
    return result, profile
