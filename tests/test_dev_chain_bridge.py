from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from main_computer.dev_chain_bridge import DevChainBridgeAdapter, DevChainBridgeError, bytes32_from_text


def _write_wallet(path: Path, *, address: str, private_key: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": "unit-wallet",
                "address": address,
                "private_key": private_key,
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_bytes32_from_text_is_stable_and_hash_shaped() -> None:
    first = bytes32_from_text("hub-deposit:dep-1")
    second = bytes32_from_text("hub-deposit:dep-1")

    assert first == second
    assert first.startswith("0x")
    assert len(first) == 66


def test_dev_chain_bridge_adapter_records_deposit_and_payout_with_redacted_commands(tmp_path: Path) -> None:
    requester = "0x1111111111111111111111111111111111111111"
    controller = "0x2222222222222222222222222222222222222222"
    worker = "0x3333333333333333333333333333333333333333"
    escrow = "0x4444444444444444444444444444444444444444"
    requester_key = "0x" + "1" * 64
    controller_key = "0x" + "2" * 64

    requester_wallet_path = tmp_path / "runtime" / "deployments" / "dev" / "runs" / "unit" / "smoke-client-wallet-42424242.json"
    controller_wallet_path = tmp_path / "runtime" / "deployments" / "dev" / "runs" / "unit" / "hub-admin-wallet-42424242.json"
    _write_wallet(requester_wallet_path, address=requester, private_key=requester_key)
    _write_wallet(controller_wallet_path, address=controller, private_key=controller_key)

    deployment_path = tmp_path / "runtime" / "deployments" / "dev" / "latest.json"
    deployment_path.parent.mkdir(parents=True, exist_ok=True)
    deployment_path.write_text(
        json.dumps(
            {
                "chain": {
                    "container_rpc_url": "http://chain-unit:8545",
                    "network": "unit-network",
                    "chain_id": 42424242,
                },
                "contracts": {
                    "hub_credit_bridge_escrow": {"address": escrow},
                },
                "smoke_client": {
                    "address": requester,
                    "wallet_path": "runtime/deployments/dev/runs/unit/smoke-client-wallet-42424242.json",
                },
                "hub_admin": {
                    "address": controller,
                    "wallet_path": "runtime/deployments/dev/runs/unit/hub-admin-wallet-42424242.json",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    commands: list[list[str]] = []

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"transactionHash": "0x" + f"{len(commands):064x}"}) + "\n",
            stderr="",
        )

    adapter = DevChainBridgeAdapter.from_deployment(
        repo_root=tmp_path,
        deployment_path=deployment_path,
        command_runner=fake_runner,
    )

    deposit = adapter.record_requester_deposit(
        account_wallet_address=requester,
        amount_units=200,
        deposit_id="hub-deposit-1",
        memo="unit deposit",
    )
    payout = adapter.record_worker_payout(
        source_account_wallet_address=requester,
        worker_wallet_address=worker,
        amount_units=2,
        payout_id="hub-payout-1",
        memo="unit payout",
    )

    assert len(commands) == 3
    assert commands[0][commands[0].index("--network") + 1] == "unit-network"
    assert "depositFor(address,uint256,bytes32,string)" in commands[0]
    assert "completeDeposit(bytes32)" in commands[1]
    assert "releaseWithdrawal(address,address,uint256,bytes32,string)" in commands[2]
    assert commands[0][commands[0].index("--value") + 1] == "200"
    assert commands[0][commands[0].index("--private-key") + 1] == requester_key
    assert commands[1][commands[1].index("--private-key") + 1] == controller_key
    assert deposit.to_dict()["transaction_hashes"] == ["0x" + "1".zfill(64), "0x" + "2".zfill(64)]
    assert payout.to_dict()["transaction_hashes"] == ["0x" + "3".zfill(64)]
    assert deposit.to_dict()["transactions"][0]["command"][deposit.to_dict()["transactions"][0]["command"].index("--private-key") + 1] == "<redacted>"
    assert payout.to_dict()["transactions"][0]["command"][payout.to_dict()["transactions"][0]["command"].index("--private-key") + 1] == "<redacted>"


def test_dev_chain_bridge_adapter_prefers_public_contract_config_for_contract_address(tmp_path: Path) -> None:
    requester = "0x1111111111111111111111111111111111111111"
    controller = "0x2222222222222222222222222222222222222222"
    deployment_escrow = "0x3333333333333333333333333333333333333333"
    config_escrow = "0x4444444444444444444444444444444444444444"
    requester_wallet = tmp_path / "runtime" / "deployments" / "testnet" / "smoke-client-wallet.json"
    controller_wallet = tmp_path / "runtime" / "deployments" / "testnet" / "hub-admin-wallet.json"
    _write_wallet(requester_wallet, address=requester, private_key="0x" + "1" * 64)
    _write_wallet(controller_wallet, address=controller, private_key="0x" + "2" * 64)

    deployment_path = tmp_path / "runtime" / "deployments" / "testnet" / "latest.json"
    deployment_path.parent.mkdir(parents=True, exist_ok=True)
    deployment_path.write_text(
        json.dumps(
            {
                "environment": "testnet",
                "chain": {"rpc_url": "http://deployment-rpc", "chain_id": 42424241},
                "contracts": {"hub_credit_bridge_escrow": {"address": deployment_escrow}},
                "smoke_client": {"address": requester, "wallet_path": str(requester_wallet)},
                "hub_admin": {"address": controller, "wallet_path": str(controller_wallet)},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    contracts_path = tmp_path / "main_computer" / "config" / "testnet_contracts.json"
    contracts_path.parent.mkdir(parents=True, exist_ok=True)
    contracts_path.write_text(json.dumps({"hub_credit_bridge_escrow": config_escrow}) + "\n", encoding="utf-8")

    adapter = DevChainBridgeAdapter.from_deployment(
        repo_root=tmp_path,
        deployment_path=deployment_path,
        contracts_path=contracts_path,
        network_key="testnet",
        command_runner=lambda command: subprocess.CompletedProcess(command, 0, stdout="0x" + "a" * 64, stderr=""),
    )

    assert adapter.escrow_address == config_escrow
    assert adapter.rpc_url == "http://deployment-rpc"


def test_dev_chain_bridge_adapter_can_start_unsigned_from_public_contract_config(tmp_path: Path) -> None:
    escrow = "0x4444444444444444444444444444444444444444"
    contracts_path = tmp_path / "main_computer" / "config" / "testnet_contracts.json"
    contracts_path.parent.mkdir(parents=True, exist_ok=True)
    contracts_path.write_text(json.dumps({"hub_credit_bridge_escrow": escrow}) + "\n", encoding="utf-8")

    adapter = DevChainBridgeAdapter.from_deployment(
        repo_root=tmp_path,
        deployment_path=tmp_path / "runtime" / "deployments" / "testnet" / "latest.json",
        contracts_path=contracts_path,
        network_key="testnet",
        fallback_rpc_url="https://testnet-rpc.example.invalid",
        allow_missing_signer=True,
        command_runner=lambda command: subprocess.CompletedProcess(command, 0, stdout="0x" + "a" * 64, stderr=""),
    )

    assert adapter.escrow_address == escrow
    assert adapter.rpc_url == "https://testnet-rpc.example.invalid"
    assert adapter.signer_configured is False
    with pytest.raises(DevChainBridgeError, match="signer is not configured"):
        adapter.record_requester_deposit(
            account_wallet_address="0x1111111111111111111111111111111111111111",
            amount_units=1,
            deposit_id="dep-unsigned",
            memo="unsigned",
        )
