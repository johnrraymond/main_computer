from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "fake-main-computer.private.yaml"

REMOTE_WALLET_ROLES = {
    "deployer",
    "captain",
    "o1",
    "o2",
    "o3",
    "hub_admin",
    "escrow_owner",
}


def _load_fixture() -> dict[str, Any]:
    payload = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_fake_private_state_template_has_parallel_testnet_and_mainnet_boot_shape() -> None:
    state = _load_fixture()
    assert state["schema_version"] == 1
    assert set(state["coolify"]["hosts"]) == {"A", "B"}

    testnet = state["networks"]["testnet"]
    mainnet = state["networks"]["mainnet"]

    for network_name, network in {"testnet": testnet, "mainnet": mainnet}.items():
        assert network["kind"] == network_name
        assert network["remote_coolify_hosts"] == ["A", "B"]
        assert set(network["wallets"]) == REMOTE_WALLET_ROLES
        assert "main_computer" not in network["wallets"]
        assert "contracts" not in network
        assert set(network["hub"]["instances"]) == {
            f"{network_name}-hub1",
            f"{network_name}-hub2",
            f"{network_name}-hub3",
        }
        assert network["foundationdb"]["configure"] == {"redundancy": "double", "storage": "ssd"}
        assert set(network["qbft"]["instances"]) == {
            "validator-rpc-1",
            "validator-1",
            "validator-2",
            "rpc-1",
        }
        assert {
            instance["coolify_host"]
            for instance in network["qbft"]["instances"].values()
        } == {"A", "B"}

    assert testnet["chain_id"] == 42424241
    assert mainnet["chain_id"] == 42424240


def test_fake_private_state_template_uses_only_fake_or_null_private_values() -> None:
    state = _load_fixture()

    for host in state["coolify"]["hosts"].values():
        assert str(host["url"]).endswith(".example.test")
        assert str(host["api_token"]).startswith("fake-coolify-token-")
        assert str(host["public_ip"]).startswith("198.51.100.")
        assert str(host["vpn_ip"]).startswith("10.")

    for role, wallet in state["networks"]["testnet"]["wallets"].items():
        assert role in REMOTE_WALLET_ROLES
        assert str(wallet["address"]).startswith("0x100000000000000000000000000000000000000")
        assert str(wallet["private_key"]).startswith("0x")
        assert len(str(wallet["private_key"])) == 66

    for wallet in state["networks"]["mainnet"]["wallets"].values():
        assert wallet["address"] is None
        assert wallet["private_key"] is None

    rendered = FIXTURE.read_text(encoding="utf-8")
    assert "greatlibrary.io" not in rendered
    assert "main_computer:" not in rendered
