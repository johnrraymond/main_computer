"""Fresh genesis must recover from stale Forge output, not install stale code."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.genesis_hub_credit_escrow import checked_escrow_layout
from tools.genesis_native_reserve import keccak256
from tools.mother.common import deployment_genesis as genesis


def artifact(*, owner_slot: int = 0, metadata=None):
    value = {
        "storageLayout": {"storage": [
            {"label": label, "slot": str(slot), "offset": 0}
            for label, slot in (
                ("owner", owner_slot), ("bridgeController", 1),
                ("bridgeControllers", 2), ("paused", 3),
            )
        ]},
        "deployedBytecode": {"object": "0x600160005260206000f3"},
    }
    if metadata is not None:
        value["metadata"] = metadata
    return value


def test_bridge_layout_drift_diagnostic_has_actual_slot():
    with pytest.raises(ValueError, match=r"expected slot=0 offset=0, got .*'slot': '7'"):
        checked_escrow_layout(artifact(owner_slot=7))


def test_default_fresh_genesis_rebuilds_stale_escrow_artifact(monkeypatch, tmp_path):
    stale = tmp_path / "stale.json"
    stale.write_text(json.dumps(artifact(owner_slot=7)), encoding="utf-8")
    generated = tmp_path / "generated.json"
    generated.write_text(json.dumps(artifact()), encoding="utf-8")
    monkeypatch.setattr(genesis, "_BRIDGE_ESCROW_ARTIFACT_CANDIDATES", (stale,))
    calls = []
    monkeypatch.setattr(genesis, "_compile_bridge_escrow_for_genesis", lambda: calls.append(1) or generated)
    assert genesis._bridge_escrow_artifact_from_path()["storageLayout"]["storage"][0]["slot"] == "0"
    assert calls == [1]


def test_second_cached_artifact_used_before_rebuild(monkeypatch, tmp_path):
    stale = tmp_path / "stale.json"
    good = tmp_path / "good.json"
    stale.write_text(json.dumps(artifact(owner_slot=8)), encoding="utf-8")
    good.write_text(json.dumps(artifact()), encoding="utf-8")
    monkeypatch.setattr(genesis, "_BRIDGE_ESCROW_ARTIFACT_CANDIDATES", (stale, good))
    monkeypatch.setattr(genesis, "_compile_bridge_escrow_for_genesis", lambda: pytest.fail("should not rebuild"))
    assert genesis._bridge_escrow_artifact_from_path() == artifact()


def test_explicit_stale_artifact_does_not_silently_rebuild(monkeypatch, tmp_path):
    stale = tmp_path / "stale.json"
    stale.write_text(json.dumps(artifact(owner_slot=7)), encoding="utf-8")
    monkeypatch.setattr(genesis, "_compile_bridge_escrow_for_genesis", lambda: pytest.fail("explicit artifact must be rejected"))
    with pytest.raises(genesis.MotherDeploymentGenesisError, match="storage layout changed at owner"):
        genesis._bridge_escrow_artifact_from_path(stale)


def test_source_mismatch_triggers_rebuild_even_if_layout_matches(monkeypatch, tmp_path):
    stale = tmp_path / "stale.json"
    stale.write_text(json.dumps(artifact(metadata={
        "sources": {"src/HubCreditBridgeEscrow.sol": {"keccak256": "0x" + "00" * 32}},
    })), encoding="utf-8")
    good = tmp_path / "good.json"
    good.write_text(json.dumps(artifact()), encoding="utf-8")
    monkeypatch.setattr(genesis, "_BRIDGE_ESCROW_ARTIFACT_CANDIDATES", (stale,))
    monkeypatch.setattr(genesis, "_compile_bridge_escrow_for_genesis", lambda: good)
    assert genesis._bridge_escrow_artifact_from_path() == artifact()


def test_current_source_commitment_accepts_forge_metadata():
    source = Path(genesis._REPO_ROOT / "contracts/src/HubCreditBridgeEscrow.sol").read_bytes()
    digest = "0x" + keccak256(source).hex()
    example = artifact(metadata={
        "sources": {"src/HubCreditBridgeEscrow.sol": {"keccak256": digest}},
    })
    assert genesis._bridge_escrow_source_matches(example)
    example["metadata"]["sources"]["src/HubCreditBridgeEscrow.sol"]["keccak256"] = "0x" + "ff" * 32
    assert not genesis._bridge_escrow_source_matches(example)


def test_fresh_genesis_compiles_only_current_escrow_source_in_isolated_workspace(monkeypatch, tmp_path):
    source = tmp_path / "contracts/src/HubCreditBridgeEscrow.sol"
    source.parent.mkdir(parents=True)
    source.write_text('contract HubCreditBridgeEscrow { address public owner; }', encoding="utf-8")
    monkeypatch.setattr(genesis, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(genesis.shutil, "which", lambda tool: "/usr/bin/forge" if tool == "forge" else None)
    invoked = []

    def fake_forge(cmd, *, cwd, capture_output, text, timeout):
        invoked.append((cmd, cwd))
        assert cmd == ["forge", "build", "--force", "--extra-output", "storageLayout"]
        assert cwd == tmp_path / "runtime/genesis-hub-credit-escrow-foundry"
        assert (cwd / "src/HubCreditBridgeEscrow.sol").read_bytes() == source.read_bytes()
        result = cwd / "out/HubCreditBridgeEscrow.sol/HubCreditBridgeEscrow.json"
        result.parent.mkdir(parents=True, exist_ok=True)
        result.write_text(json.dumps(artifact()), encoding="utf-8")
        return type("Build", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(genesis.subprocess, "run", fake_forge)
    result = genesis._compile_bridge_escrow_for_genesis()
    assert result.is_file()
    assert len(invoked) == 1
