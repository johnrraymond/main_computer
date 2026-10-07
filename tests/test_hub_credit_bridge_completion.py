from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
import sys

import pytest

from main_computer.config import MainComputerConfig
from main_computer.hub_credit_bridge_completion import (
    COMPUTE_CREDIT_BASE_UNITS,
    BridgeDeployment,
    DepositRecord,
    HubCreditBridgeCompletionService,
    HubCreditBridgeContractClient,
    JsonRpcClient,
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
        deployment_manifest_path=tmp_path / "runtime" / "deployments" / "dev" / "latest.json",
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


def test_json_rpc_client_sends_explicit_api_client_headers(monkeypatch) -> None:
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return b'{"jsonrpc":"2.0","id":1,"result":"0x1"}'

    def fake_urlopen(request, timeout):
        captured["headers"] = dict(request.header_items())
        captured["timeout"] = timeout
        return Response()

    monkeypatch.delenv("MAIN_COMPUTER_CHAIN_RPC_USER_AGENT", raising=False)
    monkeypatch.delenv("MAIN_COMPUTER_HUB_USER_AGENT", raising=False)
    monkeypatch.setattr("main_computer.hub_credit_bridge_completion.urlopen", fake_urlopen)

    assert JsonRpcClient("https://rpc.example.invalid", timeout_s=3).chain_id() == 1

    headers = captured["headers"]
    assert headers["Accept"] == "application/json"
    assert headers["Content-type"] == "application/json"
    assert headers["X-main-computer-client"] == "chain-rpc"
    assert headers["User-agent"].startswith("main-computer-chain-rpc/")
    assert "Python-urllib" not in headers["User-agent"]


def test_json_rpc_client_wraps_http_forbidden_with_method_and_body(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        raise HTTPError(
            request.full_url,
            403,
            "Forbidden",
            hdrs=None,
            fp=BytesIO(b'{"error":"blocked"}'),
        )

    monkeypatch.setattr("main_computer.hub_credit_bridge_completion.urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="Chain RPC request eth_getTransactionReceipt failed with HTTP 403"):
        JsonRpcClient("https://rpc.example.invalid", timeout_s=3).transaction_receipt("0x" + "ab" * 32)


def test_bridge_signer_bundle_loads_as_hub_admin_deployment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from main_computer.hub_credit_bridge_completion import load_bridge_deployment, load_hub_admin_private_key

    signer = tmp_path / "bridge-signer-bundle.json"
    private_key = "0x" + "44" * 32
    signer.write_text(
        __import__("json").dumps(
            {
                "schema": "main-computer.bridge-signer.v1",
                "network": "signer-test",
                "chain_id": 42424240,
                "chain_rpc_url": "https://mainnet-rpc.example.invalid",
                "contracts": {
                    "hub_credit_bridge_escrow": {
                        "address": CONTRACT,
                        "bridge_controller_address": ADMIN,
                    }
                },
                "bridge_controller": {"address": ADMIN, "private_key": private_key},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MAIN_COMPUTER_HUB_NETWORK", "signer-test")
    config = MainComputerConfig(workspace=tmp_path, hub_root=tmp_path / "hub")

    deployment = load_bridge_deployment(config, deployment_manifest_path=signer)

    assert deployment.chain_id == 42424240
    assert deployment.rpc_url == "https://mainnet-rpc.example.invalid"
    assert deployment.contract_address == CONTRACT
    assert deployment.bridge_controller_address == ADMIN
    assert deployment.hub_admin_address == ADMIN
    assert deployment.hub_admin_wallet_path == signer
    assert load_hub_admin_private_key(deployment) == private_key


def test_hub_executes_bridge_reconciliation_with_controller_signer(tmp_path: Path) -> None:
    class FakeReconciliationClient:
        def __init__(self) -> None:
            self.rectify_calls: list[tuple] = []
            self.withdraw_calls: list[dict] = []

        def deposit_record(self, deposit_id: str) -> DepositRecord:
            assert deposit_id == DEPOSIT_ID
            return DepositRecord(
                exists=True,
                completed=True,
                account=WALLET,
                payer=PAYER,
                amount_units=2 * COMPUTE_CREDIT_BASE_UNITS,
            )

        def rectify_spend(self, account: str, amount_units: int, rectification_id: str, memo: str) -> dict:
            self.rectify_calls.append((account, amount_units, rectification_id, memo))
            return {"tx_hash": "0x" + "66" * 32, "receipt": {"status": "0x1"}}

        def release_withdrawal(self, **kwargs) -> dict:
            self.withdraw_calls.append(dict(kwargs))
            return {"tx_hash": "0x" + "77" * 32, "receipt": {"status": "0x1"}}

    ledger = HubCreditLedger(tmp_path / "hub" / "compute_credits")
    ledger.issue(account_id=WALLET, owner_address=WALLET, credits=2, memo="test bridge funding")
    ledger.spend_request_credit_wei(
        account_id=WALLET,
        request_id="request-1",
        credit_wei=COMPUTE_CREDIT_BASE_UNITS,
        memo="test request charge",
    )
    client = FakeReconciliationClient()
    service = HubCreditBridgeCompletionService(
        ledger,
        MainComputerConfig(workspace=tmp_path, hub_root=tmp_path / "hub"),
        client=client,
        deployment=_deployment(tmp_path),
    )
    request_status = {
        "request_id": "request-1",
        "state": "completed",
        "account_id": WALLET,
        "bridge_deposit_id": DEPOSIT_ID,
    }

    result = service.execute_bridge_reconciliation(
        {
            "request_id": "request-1",
            "deposit_id": DEPOSIT_ID,
            "wallet_address": WALLET,
            "expected_bridge_credit_wei": str(2 * COMPUTE_CREDIT_BASE_UNITS),
            "expected_charged_credit_wei": str(COMPUTE_CREDIT_BASE_UNITS),
        },
        request_status=request_status,
    )

    assert result["ok"] is True
    assert result["signing_mode"] == "hub-bridge-controller"
    assert result["bridge_credit_wei"] == str(2 * COMPUTE_CREDIT_BASE_UNITS)
    assert result["charged_credit_wei"] == str(COMPUTE_CREDIT_BASE_UNITS)
    assert result["rectified_credit_wei"] == str(COMPUTE_CREDIT_BASE_UNITS)
    assert result["withdrawn_credit_wei"] == str(COMPUTE_CREDIT_BASE_UNITS)
    assert len(client.rectify_calls) == 1
    rect_account, rect_amount, rect_id, rect_memo = client.rectify_calls[0]
    assert rect_account == WALLET
    assert rect_amount == COMPUTE_CREDIT_BASE_UNITS
    assert rect_id == result["rectification_id"]
    assert rect_memo == "hub request bridge reconciliation request-1"
    assert client.withdraw_calls == [
        {
            "account": WALLET,
            "recipient": WALLET,
            "amount_units": COMPUTE_CREDIT_BASE_UNITS,
            "withdrawal_id": result["withdrawal_id"],
            "memo": "hub request bridge reconciliation request-1",
        }
    ]
    assert result["rectification"]["tx_hash"] == "0x" + "66" * 32
    assert result["withdrawal"]["tx_hash"] == "0x" + "77" * 32


def test_hub_bridge_reconciliation_rejects_client_amount_mismatch_before_signing(tmp_path: Path) -> None:
    class FakeReconciliationClient:
        def __init__(self) -> None:
            self.sign_calls = 0

        def deposit_record(self, deposit_id: str) -> DepositRecord:
            return DepositRecord(
                exists=True,
                completed=True,
                account=WALLET,
                payer=PAYER,
                amount_units=2 * COMPUTE_CREDIT_BASE_UNITS,
            )

        def rectify_spend(self, *args, **kwargs) -> dict:
            self.sign_calls += 1
            return {}

        def release_withdrawal(self, **kwargs) -> dict:
            self.sign_calls += 1
            return {}

    ledger = HubCreditLedger(tmp_path / "hub" / "compute_credits")
    ledger.issue(account_id=WALLET, owner_address=WALLET, credits=2, memo="test bridge funding")
    ledger.spend_request_credit_wei(
        account_id=WALLET,
        request_id="request-1",
        credit_wei=COMPUTE_CREDIT_BASE_UNITS,
        memo="test request charge",
    )
    client = FakeReconciliationClient()
    service = HubCreditBridgeCompletionService(
        ledger,
        MainComputerConfig(workspace=tmp_path, hub_root=tmp_path / "hub"),
        client=client,
        deployment=_deployment(tmp_path),
    )

    with pytest.raises(ValueError, match="client charged amount disagrees with Hub ledger"):
        service.execute_bridge_reconciliation(
            {
                "request_id": "request-1",
                "deposit_id": DEPOSIT_ID,
                "wallet_address": WALLET,
                "expected_charged_credit_wei": str(2 * COMPUTE_CREDIT_BASE_UNITS),
            },
            request_status={
                "request_id": "request-1",
                "state": "completed",
                "account_id": WALLET,
                "bridge_deposit_id": DEPOSIT_ID,
            },
        )

    assert client.sign_calls == 0


def test_hub_bridge_reconciliation_rejects_unbound_deposit_before_signing(tmp_path: Path) -> None:
    class FakeReconciliationClient:
        def __init__(self) -> None:
            self.deposit_reads = 0

        def deposit_record(self, deposit_id: str) -> DepositRecord:
            self.deposit_reads += 1
            raise AssertionError("unbound deposit must be rejected before chain access")

    client = FakeReconciliationClient()
    service = HubCreditBridgeCompletionService(
        HubCreditLedger(tmp_path / "hub" / "compute_credits"),
        MainComputerConfig(workspace=tmp_path, hub_root=tmp_path / "hub"),
        client=client,
        deployment=_deployment(tmp_path),
    )

    with pytest.raises(ValueError, match="bridge deposit does not match Hub request binding"):
        service.execute_bridge_reconciliation(
            {"request_id": "request-1", "deposit_id": "0x" + "cd" * 32},
            request_status={
                "request_id": "request-1",
                "state": "completed",
                "account_id": WALLET,
                "bridge_deposit_id": DEPOSIT_ID,
            },
        )

    assert client.deposit_reads == 0
