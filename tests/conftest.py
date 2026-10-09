"""Pure-Python test-only Forge artifact fixture for Mother genesis reconstruction.

Production never uses this artifact: monkeypatch restores the actual compiled-artifact
paths after each test. Tests also exercise the missing-artifact error separately.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _mother_genesis_compiler_test_artifact(request, monkeypatch, tmp_path):
    module = request.module.__name__
    if not (
        module.startswith(("tests.test_mother_", "test_mother_"))
        or module.startswith(("tests.test_native_mint_control", "test_native_mint_control"))
    ):
        return
    from tools.mother.common import deployment_genesis

    artifact = {
        "storageLayout": {"storage": [
            {"label": name, "slot": str(slot), "offset": offset}
            for name, slot, offset in (
                ("_offices", 0, 0),
                ("officeIndexPlusOne", 4, 0),
                ("maxPayoutWei", 8, 0),
                ("payoutDelayBlocks", 9, 0),
                ("resetDelayBlocks", 9, 8),
                ("nextProposalId", 10, 0),
            )
        ]},
        "deployedBytecode": {"object": "0x600160005260206000f3"},
    }
    path = tmp_path / "test-only-XLagBridgeReserve.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    monkeypatch.setattr(deployment_genesis, "_RESERVE_ARTIFACT_CANDIDATES", (path,))
    bridge_artifact = {
        "storageLayout": {"storage": [
            {"label": name, "slot": str(slot), "offset": 0}
            for name, slot in (("owner", 0), ("bridgeController", 1), ("bridgeControllers", 2), ("paused", 3))
        ]},
        "deployedBytecode": {"object": "0x600160005260206000f3"},
    }
    bridge_path = tmp_path / "test-only-HubCreditBridgeEscrow.json"
    bridge_path.write_text(json.dumps(bridge_artifact), encoding="utf-8")
    monkeypatch.setattr(deployment_genesis, "_BRIDGE_ESCROW_ARTIFACT_CANDIDATES", (bridge_path,))

