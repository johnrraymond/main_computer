from __future__ import annotations

import pytest

from tools.genesis_hub_credit_escrow import escrow_allocation, DEFAULT_HUB_CREDIT_ESCROW_ADDRESS
from tools.genesis_native_reserve import mapping_slot, word


def fake_bridge_artifact():
    return {
        "storageLayout": {"storage": [
            {"label": name, "slot": str(slot), "offset": 0}
            for name, slot in (("owner", 0), ("bridgeController", 1), ("bridgeControllers", 2), ("paused", 3))
        ]},
        "deployedBytecode": {"object": "0x600160005260206000f3"},
    }


def test_hub_credit_bridge_escrow_installed_with_constructor_equivalent_state():
    owner = "0x" + "11" * 20
    controller = "0x" + "22" * 20
    alloc, profile = escrow_allocation(artifact=fake_bridge_artifact(), owner=owner, bridge_controller=controller)
    assert alloc["balance"] == "0x0"
    assert alloc["code"] == "0x600160005260206000f3"
    assert alloc["storage"][word(0)] == word(int(owner, 16))
    assert alloc["storage"][word(1)] == word(int(controller, 16))
    assert alloc["storage"][word(mapping_slot(controller, 2))] == word(1)
    assert profile["address"] == DEFAULT_HUB_CREDIT_ESCROW_ADDRESS
    assert profile["method"] == "genesis-alloc-predeploy"


def test_escrow_storage_layout_drift_fails_closed():
    artifact = fake_bridge_artifact()
    artifact["storageLayout"]["storage"][0]["slot"] = "7"
    with pytest.raises(ValueError, match="layout changed"):
        escrow_allocation(
            artifact=artifact, owner="0x" + "11" * 20, bridge_controller="0x" + "22" * 20,
        )
