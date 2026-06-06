from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from main_computer.config import MainComputerConfig
from main_computer.hub_credit_bridge_completion import (
    COMPUTE_CREDIT_BASE_UNITS,
    BridgeDeployment,
    DepositRecord,
    HubCreditBridgeCompletionService,
    HubCreditBridgeContractClient,
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
    assert result["delta_credit_wei"] == str(COMPUTE_CREDIT_BASE_UNITS)
    assert result["chain_completed_credit_wei"] == str(COMPUTE_CREDIT_BASE_UNITS)
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

    assert first["delta_credit_wei"] == str(COMPUTE_CREDIT_BASE_UNITS)
    assert first["completion_sent"] is False
    assert second["idempotent"] is True
    assert second["delta_credit_wei"] == "0"
    assert client.complete_calls == []
    assert second["account"]["available_credits"] == 1
    assert second["account"]["bridge_completed_credits"] == 1


def test_fractional_completed_units_are_recorded_without_whole_credit_divisibility(tmp_path: Path) -> None:
    fractional_units = COMPUTE_CREDIT_BASE_UNITS * 3 // 4
    client = FakeBridgeClient(completed=True, completed_units=fractional_units)
    service = _service(tmp_path, client)

    result = service.complete_wallet_funding_deposit({"deposit_id": DEPOSIT_ID, "wallet_address": WALLET})

    assert result["ok"] is True
    assert result["delta_credit_wei"] == str(fractional_units)
    assert result["delta_credits_display"] == "0.75"
    assert result["account"]["available_credit_wei"] == str(fractional_units)
    assert result["account"]["available_credits_display"] == "0.75"
    assert result["account"]["available_credits"] == 0


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
        chain_completed_credit_wei=2 * COMPUTE_CREDIT_BASE_UNITS,
        deposit_id=DEPOSIT_ID,
    )

    with pytest.raises(ValueError, match="ahead of the chain"):
        ledger.record_completed_bridge_deposit(
            account_id=WALLET,
            owner_address=WALLET,
            chain_completed_credit_wei=COMPUTE_CREDIT_BASE_UNITS,
            deposit_id="0x" + "cd" * 32,
        )



def test_contract_client_checksums_transaction_addresses_before_signing(monkeypatch: pytest.MonkeyPatch) -> None:
    lowercase_contract = "0xe7f1725e7734ce288f8367e1bb143e90bb3f0512"
    checksum_contract = "0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512"
    lowercase_admin = "0x6bef896c6cbe2a89dc3508c31ab8a2723153a0a4"
    checksum_admin = "0x6bef896c6Cbe2a89DC3508c31Ab8a2723153A0a4"
    signed_txs: list[dict] = []

    def fake_to_checksum_address(value: str) -> str:
        normalized = value.lower()
        if normalized == lowercase_contract:
            return checksum_contract
        if normalized == lowercase_admin:
            return checksum_admin
        raise AssertionError(f"unexpected checksum input: {value}")

    class FakeAccount:
        @staticmethod
        def from_key(private_key: str) -> SimpleNamespace:
            assert private_key == "0x" + "11" * 32
            return SimpleNamespace(address=lowercase_admin)

        @staticmethod
        def sign_transaction(tx: dict, private_key: str) -> SimpleNamespace:
            signed_txs.append(dict(tx))
            assert tx["to"] == checksum_contract
            return SimpleNamespace(raw_transaction=b"\x12\x34")

    monkeypatch.setitem(sys.modules, "eth_utils", SimpleNamespace(to_checksum_address=fake_to_checksum_address))
    monkeypatch.setitem(sys.modules, "eth_account", SimpleNamespace(Account=FakeAccount))

    class FakeRpc:
        def __init__(self) -> None:
            self.estimated_txs: list[dict] = []
            self.nonce_addresses: list[str] = []

        def chain_id(self) -> int:
            return 42424242

        def estimate_gas(self, tx: dict) -> int:
            self.estimated_txs.append(dict(tx))
            return 100_000

        def get_transaction_count(self, address: str) -> int:
            self.nonce_addresses.append(address)
            return 3

        def gas_price(self) -> int:
            return 1_000_000_000

        def send_raw_transaction(self, raw_tx: bytes | str) -> str:
            assert raw_tx == b"\x12\x34"
            return "0x" + "99" * 32

        def transaction_receipt(self, tx_hash: str) -> dict:
            return {"status": "0x1", "transactionHash": tx_hash}

    rpc = FakeRpc()
    client = HubCreditBridgeContractClient(
        rpc_url="http://127.0.0.1:18545",
        contract_address=lowercase_contract,
        chain_id=42424242,
        admin_private_key="0x" + "11" * 32,
        admin_address=lowercase_admin,
        rpc_client=rpc,  # type: ignore[arg-type]
        receipt_timeout_s=1.0,
    )

    result = client.complete_deposit(DEPOSIT_ID)

    assert result["tx_hash"] == "0x" + "99" * 32
    assert rpc.estimated_txs == [
        {
            "from": checksum_admin,
            "to": checksum_contract,
            "value": "0x0",
            "data": "0x8c503dc4" + DEPOSIT_ID[2:],
        }
    ]
    assert rpc.nonce_addresses == [checksum_admin]
    assert signed_txs[0]["to"] == checksum_contract
