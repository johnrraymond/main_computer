from __future__ import annotations

from pathlib import Path

import pytest

from main_computer.config import MainComputerConfig
from main_computer.hub_credit_bridge_completion import (
    COMPUTE_CREDIT_BASE_UNITS,
    BridgeDeployment,
    DepositRecord,
    HubCreditBridgeCompletionService,
)
from main_computer.hub_credit_ledger import HubCreditLedger


DEPOSIT_ID = "0x" + "ab" * 32
WALLET = "0x1111111111111111111111111111111111111111"
PAYER = "0x2222222222222222222222222222222222222222"
CONTRACT = "0x3333333333333333333333333333333333333333"
ADMIN = "0x4444444444444444444444444444444444444444"


class FakeBridgeClient:
    def __init__(self, *, completed: bool = False, completed_units: int = 0, account: str = WALLET) -> None:
        self.completed = completed
        self.completed_units = completed_units
        self.account = account
        self.complete_calls: list[str] = []

    def deposit_record(self, deposit_id: str) -> DepositRecord:
        return DepositRecord(
            exists=True,
            completed=self.completed,
            account=self.account,
            payer=PAYER,
            amount_units=COMPUTE_CREDIT_BASE_UNITS,
        )

    def complete_deposit(self, deposit_id: str) -> dict:
        self.complete_calls.append(deposit_id)
        self.completed = True
        self.completed_units = COMPUTE_CREDIT_BASE_UNITS
        return {"tx_hash": "0x" + "55" * 32, "receipt": {"blockNumber": "0x7", "status": "0x1"}}

    def completed_deposit_units(self, account: str) -> int:
        return self.completed_units


def _deployment(tmp_path: Path) -> BridgeDeployment:
    wallet_path = tmp_path / "hub-admin-wallet.json"
    wallet_path.write_text("{}", encoding="utf-8")
    return BridgeDeployment(
        chain_id=42424242,
        rpc_url="http://127.0.0.1:18545",
        contract_address=CONTRACT,
        bridge_controller_address=ADMIN,
        hub_admin_address=ADMIN,
        hub_admin_wallet_path=wallet_path,
        current_json_path=tmp_path / "current.json",
    )


def _service(tmp_path: Path, client: FakeBridgeClient) -> HubCreditBridgeCompletionService:
    config = MainComputerConfig(workspace=tmp_path, hub_root=tmp_path / "hub")
    return HubCreditBridgeCompletionService(
        HubCreditLedger(tmp_path / "hub" / "compute_credits"),
        config,
        client=client,
        deployment=_deployment(tmp_path),
    )


def test_complete_wallet_funding_deposit_sends_chain_completion_and_records_delta(tmp_path: Path) -> None:
    client = FakeBridgeClient(completed=False, completed_units=0)
    service = _service(tmp_path, client)

    result = service.complete_wallet_funding_deposit({"deposit_id": DEPOSIT_ID, "wallet_address": WALLET})

    assert result["ok"] is True
    assert result["completion_sent"] is True
    assert result["delta_credits"] == 1
    assert result["chain_completed_credits"] == 1
    assert client.complete_calls == [DEPOSIT_ID]
    account = result["account"]
    assert account["account_id"] == WALLET
    assert account["available_credits"] == 1
    assert account["bridge_completed_credits"] == 1
    assert result["transaction"]["transaction_type"] == "bridge_deposit_completed"


def test_completed_wallet_funding_deposit_is_idempotent_locally(tmp_path: Path) -> None:
    client = FakeBridgeClient(completed=True, completed_units=COMPUTE_CREDIT_BASE_UNITS)
    service = _service(tmp_path, client)

    first = service.complete_wallet_funding_deposit({"deposit_id": DEPOSIT_ID, "wallet_address": WALLET})
    second = service.complete_wallet_funding_deposit({"deposit_id": DEPOSIT_ID, "wallet_address": WALLET})

    assert first["delta_credits"] == 1
    assert first["completion_sent"] is False
    assert second["idempotent"] is True
    assert second["delta_credits"] == 0
    assert client.complete_calls == []
    assert second["account"]["available_credits"] == 1
    assert second["account"]["bridge_completed_credits"] == 1


def test_wallet_mismatch_is_rejected_before_completion(tmp_path: Path) -> None:
    client = FakeBridgeClient(completed=False, completed_units=0)
    service = _service(tmp_path, client)

    with pytest.raises(ValueError, match="wallet_address does not match"):
        service.complete_wallet_funding_deposit(
            {
                "deposit_id": DEPOSIT_ID,
                "wallet_address": "0x9999999999999999999999999999999999999999",
            }
        )

    assert client.complete_calls == []


def test_ledger_rejects_local_completed_total_ahead_of_chain(tmp_path: Path) -> None:
    ledger = HubCreditLedger(tmp_path / "ledger")
    ledger.record_completed_bridge_deposit(
        account_id=WALLET,
        owner_address=WALLET,
        chain_completed_credits=2,
        deposit_id=DEPOSIT_ID,
    )

    with pytest.raises(ValueError, match="ahead of the chain"):
        ledger.record_completed_bridge_deposit(
            account_id=WALLET,
            owner_address=WALLET,
            chain_completed_credits=1,
            deposit_id="0x" + "cd" * 32,
        )
