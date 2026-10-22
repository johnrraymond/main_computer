"""The Stable Hub must materialize its signer or refuse financial service."""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def _launcher():
    path = Path(__file__).resolve().parents[1] / "run-stable-hub.py"
    spec = importlib.util.spec_from_file_location("test_stable_hub_launcher", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _configure(monkeypatch, tmp_path):
    root = tmp_path / "testnetb-hub1"
    remote = root / "private" / "bridge-signer" / "bridge-signer-bundle.json"
    monkeypatch.setenv("MAIN_COMPUTER_HUB_ENABLE_BRIDGE_WRITES", "true")
    monkeypatch.setenv("MAIN_COMPUTER_HUB_DEV_CHAIN_DEPLOYMENT_PATH", str(remote))
    monkeypatch.setenv("MAIN_COMPUTER_HUB_CONTROL_RUNTIME_DIR", str(root))
    monkeypatch.setenv("MAIN_COMPUTER_HUB_CONTROL_NETWORK", "testnet")
    monkeypatch.setenv("MAIN_COMPUTER_HUB_NETWORK", "testnet")
    monkeypatch.setenv("MAIN_COMPUTER_HUB_CONTROL_CHAIN_ID", "42424241")
    monkeypatch.setenv("MAIN_COMPUTER_HUB_CONTRACTS_PATH", str(
        Path(__file__).resolve().parents[1] / "main_computer/config/testnet_contracts.json"
    ))
    signer = "0x" + "aa" * 20
    bundle = {
        "schema": "main-computer.bridge-signer.v1", "network": "testnet",
        "chain_id": 42424241, "chain_rpc_url": "https://testnet-rpc.example.invalid",
        "contracts": {"hub_credit_bridge_escrow": {
            "address": "0x" + "0" * 36 + "e5c0", "bridge_controller_address": signer,
        }},
        "bridge_controller": {"address": signer, "private_key": "0x" + "44" * 32},
    }
    monkeypatch.setenv("MAIN_COMPUTER_BRIDGE_SIGNER_BUNDLE_B64",
                       base64.b64encode(json.dumps(bundle).encode()).decode())
    return remote, signer, bundle


def test_materializes_authoritative_testnet_signer_and_private_permissions(tmp_path, monkeypatch):
    mod = _launcher()
    path, signer, _ = _configure(monkeypatch, tmp_path)
    monkeypatch.setitem(sys.modules, "eth_account", SimpleNamespace(
        Account=SimpleNamespace(from_key=lambda _: SimpleNamespace(address=signer))))
    mod._install_authoritative_bridge_signer()
    assert json.loads(path.read_text())["network"] == "testnet"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize("mutation", ["missing", "wrong-network", "wrong-chain", "outside-root"])
def test_rejects_unusable_signer_without_writing(tmp_path, monkeypatch, mutation):
    mod = _launcher()
    path, _, bundle = _configure(monkeypatch, tmp_path)
    if mutation == "missing":
        monkeypatch.delenv("MAIN_COMPUTER_BRIDGE_SIGNER_BUNDLE_B64")
    if mutation in {"wrong-network", "wrong-chain"}:
        bundle["network" if mutation == "wrong-network" else "chain_id"] = (
            "mainnet" if mutation == "wrong-network" else 42424240
        )
        monkeypatch.setenv("MAIN_COMPUTER_BRIDGE_SIGNER_BUNDLE_B64",
                           base64.b64encode(json.dumps(bundle).encode()).decode())
    if mutation == "outside-root":
        monkeypatch.setenv("MAIN_COMPUTER_HUB_DEV_CHAIN_DEPLOYMENT_PATH", str(tmp_path / "bad.json"))
    with pytest.raises(RuntimeError):
        mod._install_authoritative_bridge_signer()
    assert not path.exists()


def test_readonly_stable_hub_does_not_require_secret(tmp_path, monkeypatch):
    mod = _launcher()
    remote, _, _ = _configure(monkeypatch, tmp_path)
    monkeypatch.setenv("MAIN_COMPUTER_HUB_ENABLE_BRIDGE_WRITES", "false")
    monkeypatch.delenv("MAIN_COMPUTER_BRIDGE_SIGNER_BUNDLE_B64")
    mod._install_authoritative_bridge_signer()
    assert not remote.exists()
