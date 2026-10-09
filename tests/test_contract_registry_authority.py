from __future__ import annotations

import json
from pathlib import Path

import pytest

from main_computer.contract_config import (
    ContractConfigError, get_contract_address, load_contract_config, write_contract_config,
)
from tools.genesis_hub_credit_escrow import DEFAULT_HUB_CREDIT_ESCROW_ADDRESS
from tools.genesis_native_reserve import DEFAULT_RESERVE_ADDRESS


def test_active_registry_replaces_old_deployments_at_genesis(tmp_path: Path) -> None:
    write_contract_config({"network": "mainnet", "contracts": {
        "hub_credit_bridge_escrow": "0x" + "1" * 40,
        "alpha-beta-lockout": "0x" + "2" * 40,
    }}, repo_root=tmp_path)
    write_contract_config({"network": "mainnet", "contracts": {
        "xlag-bridge-reserve": DEFAULT_RESERVE_ADDRESS,
        "hub_credit_bridge_escrow": DEFAULT_HUB_CREDIT_ESCROW_ADDRESS,
    }}, repo_root=tmp_path)
    path, value = load_contract_config("mainnet", repo_root=tmp_path, required=True)
    assert get_contract_address(value, "hub_credit_bridge_escrow") == DEFAULT_HUB_CREDIT_ESCROW_ADDRESS
    assert "alpha-beta-lockout" not in value
    assert value["xlag-bridge-reserve"] == DEFAULT_RESERVE_ADDRESS


def test_partial_deployment_updates_only_selected_address(tmp_path: Path) -> None:
    write_contract_config({"network": "mainnet", "contracts": {
        "hub_credit_bridge_escrow": DEFAULT_HUB_CREDIT_ESCROW_ADDRESS,
        "xlag-bridge-reserve": DEFAULT_RESERVE_ADDRESS,
    }}, repo_root=tmp_path)
    write_contract_config({"network": "mainnet", "contracts": {
        "hub_credit_bridge_escrow": "0x" + "a" * 40,
    }}, repo_root=tmp_path, merge_existing=True)
    _, value = load_contract_config("mainnet", repo_root=tmp_path, required=True)
    assert value == {"hub_credit_bridge_escrow": "0x" + "a" * 40,
                     "xlag-bridge-reserve": DEFAULT_RESERVE_ADDRESS}


def test_invalid_publisher_cannot_replace_active_registry(tmp_path: Path) -> None:
    write_contract_config({"network": "mainnet", "contracts": {
        "hub_credit_bridge_escrow": DEFAULT_HUB_CREDIT_ESCROW_ADDRESS,
    }}, repo_root=tmp_path)
    path = tmp_path / "main_computer" / "config" / "mainnet_contracts.json"
    before = path.read_bytes()
    with pytest.raises(ContractConfigError):
        write_contract_config({"network": "mainnet", "contracts": {
            "hub_credit_bridge_escrow": "not-an-address",
        }}, repo_root=tmp_path)
    assert path.read_bytes() == before
