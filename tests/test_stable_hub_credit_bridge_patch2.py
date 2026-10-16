"""Patch 2: Stable wallet balance/charges + verified escrow deposit adapter.

No live chain, signing key, FDB cluster, or real credit transfer is used.
"""
from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from main_computer.config import MainComputerConfig
from main_computer.hub_credit_bridge_completion import (
    BridgeDeployment, DepositRecord, HubCreditBridgeCompletionService,
)
from main_computer.hub_credit_indexer import wallet_account_id
from main_computer.stable_hub import (
    _StableHubBridgeLedgerAdapter, create_stable_hub_server,
)
from main_computer.stable_hub_topology import load_stable_hub_topology
from main_computer.stable_hub_worker_sessions import (
    StableHubPayoutLedgerDirectory, StableHubWorkerSessionError,
)
from test_stable_hub_fdb_atomic_accounting import _two_hubs


WALLET = "0x" + "ab" * 20
DEPOSIT = "0x" + "01" * 32
SECOND_DEPOSIT = "0x" + "02" * 32
CONTRACT = "0x" + "cd" * 20
UNIT = 10**18
FUNDING_TX = "0x" + "ee" * 32


class FakeEscrow:
    def __init__(self, *, historical_units: int = 0) -> None:
        self.recorded = {
            DEPOSIT: {"amount": UNIT, "completed": False},
            SECOND_DEPOSIT: {"amount": 2 * UNIT, "completed": False},
        }
        self.historical_units = historical_units
        self.completions = 0

    def deposit_record(self, deposit_id: str) -> DepositRecord:
        record = self.recorded.get(deposit_id)
        if record is None:
            return DepositRecord(False, False, "", "", 0)
        return DepositRecord(True, record["completed"], WALLET, WALLET, record["amount"])

    def verify_deposit_transaction(self, tx_hash: str, *, deposit_id: str, deposit: DepositRecord) -> dict:
        if tx_hash != FUNDING_TX or deposit_id not in self.recorded:
            raise ValueError("bridge_deposit_transaction_event_mismatch")
        if deposit.amount_units != self.recorded[deposit_id]["amount"]:
            raise ValueError("bridge_deposit_transaction_event_mismatch")
        return {"status": "0x1", "blockNumber": "0xf", "transactionHash": tx_hash}

    def complete_deposit(self, deposit_id: str) -> dict:
        if not self.recorded[deposit_id]["completed"]:
            self.recorded[deposit_id]["completed"] = True
            self.completions += 1
        return {"tx_hash": "0x" + "ff" * 32, "receipt": {"blockNumber": "0x10"}}

    def completed_deposit_units(self, account: str) -> int:
        assert account == WALLET
        return self.historical_units + sum(
            record["amount"] for record in self.recorded.values() if record["completed"]
        )


def service(ledger: StableHubPayoutLedgerDirectory, chain: FakeEscrow) -> HubCreditBridgeCompletionService:
    return HubCreditBridgeCompletionService(
        _StableHubBridgeLedgerAdapter(ledger), MainComputerConfig.from_env(),
        client=chain,
        deployment=BridgeDeployment(
            chain_id=42424240, rpc_url="http://invalid.local", contract_address=CONTRACT,
            bridge_controller_address="0x" + "ef" * 20,
            hub_admin_address="0x" + "ef" * 20,
            hub_admin_wallet_path=Path("no-real-key.json"),
            deployment_manifest_path=Path("no-real-manifest.json"),
        ),
    )


def complete(svc: HubCreditBridgeCompletionService, deposit_id: str = DEPOSIT) -> dict:
    return svc.complete_wallet_funding_deposit({
        "deposit_id": deposit_id, "wallet_address": WALLET, "tx_hash": FUNDING_TX,
        "chain_id": 42424240, "contract_address": CONTRACT,
    })


def test_verified_chain_completion_credits_once_on_both_hubs() -> None:
    db, stores, a, c = _two_hubs()
    chain = FakeEscrow()
    first = complete(service(a, chain))
    replay = complete(service(c, chain))
    assert first["ok"] is True
    assert first["delta_credit_wei"] == str(UNIT)
    assert replay["idempotent"] is True
    assert replay["delta_credit_wei"] == "0"
    assert chain.completions == 1
    assert c.get_account(wallet_account_id(WALLET))["available_credit_wei"] == str(UNIT)
    second = complete(service(c, chain), SECOND_DEPOSIT)
    assert second["delta_credit_wei"] == str(2 * UNIT)
    assert a.get_account(wallet_account_id(WALLET))["available_credit_wei"] == str(3 * UNIT)
    snapshot = stores[0].load()
    assert len(snapshot["payout_deposits"]) == 2
    assert {item["status"] for item in snapshot["payout_deposit_intents"].values()} == {"finalized"}
    assert {item["funding_tx_hash"] for item in snapshot["payout_deposits"].values()} == {FUNDING_TX}


def test_chain_success_then_ledger_unavailable_is_retryable_without_double_credit() -> None:
    _, _, a, c = _two_hubs()
    chain = FakeEscrow()
    svc = service(a, chain)
    original = svc.ledger.record_completed_bridge_deposit
    svc.ledger.record_completed_bridge_deposit = lambda **kwargs: (_ for _ in ()).throw(OSError("fdb unavailable"))
    with pytest.raises(OSError, match="fdb unavailable"):
        complete(svc)
    assert chain.completions == 1
    assert a.get_account(wallet_account_id(WALLET)) is None
    recovered = complete(service(c, chain))
    assert recovered["delta_credit_wei"] == str(UNIT)
    assert complete(service(a, chain))["delta_credit_wei"] == "0"
    assert chain.completions == 1


def test_unrelated_historical_total_does_not_block_individual_deposit() -> None:
    _, _, a, c = _two_hubs()
    chain = FakeEscrow(historical_units=7 * UNIT)
    first = complete(service(a, chain))
    assert first["delta_credit_wei"] == str(UNIT)
    assert first["chain_completed_credit_wei"] == str(8 * UNIT)
    assert chain.completions == 1
    assert c.get_account(wallet_account_id(WALLET))["available_credit_wei"] == str(UNIT)
    assert complete(service(c, chain))["delta_credit_wei"] == "0"


def test_unknown_deposit_and_wrong_wallet_never_create_credits() -> None:
    _, _, a, _ = _two_hubs()
    svc = service(a, FakeEscrow())
    with pytest.raises(ValueError, match="Unknown bridge deposit"):
        complete(svc, "0x" + "ab" * 32)
    with pytest.raises(ValueError, match="does not match deposit account"):
        svc.complete_wallet_funding_deposit({"deposit_id": DEPOSIT, "wallet_address": "0x" + "ef" * 20})
    assert a.get_account(wallet_account_id(WALLET)) is None


class MemoryStore:
    def __init__(self):
        self.payload: dict = {}
        self.lock = threading.RLock()

    def load(self) -> dict:
        with self.lock:
            return json.loads(json.dumps(self.payload))

    def save(self, data: dict) -> None:
        with self.lock:
            self.payload = json.loads(json.dumps(data))


def _json_request(base: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    request = Request(base + path, data=data, headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
    try:
        with urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_stable_http_wallet_balance_funding_charge_history_and_disabled_refunds() -> None:
    topology = load_stable_hub_topology(Path("deploy/hub-topology/dev-topology.json"))
    store = MemoryStore()
    ledger = StableHubPayoutLedgerDirectory(topology=topology, hub_id="dev-hub1", store=store)
    chain = FakeEscrow()
    server = create_stable_hub_server(
        topology=topology, hub_id="dev-hub1", bind_host="127.0.0.1", bind_port=0,
        worker_session_store=store, bridge_completion_service=service(ledger, chain),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        balance_path = "/api/hub/v1/credits/balance?" + urlencode({"wallet_address": WALLET})
        status, initial = _json_request(base, balance_path)
        assert status == 200 and initial["account"]["available_credit_wei"] == "0"
        assert ledger.get_account(wallet_account_id(WALLET)) is None  # read-only
        status, funded = _json_request(base, "/api/hub/v1/credits/wallet-funding/complete", {
            "deposit_id": DEPOSIT, "wallet_address": WALLET, "tx_hash": FUNDING_TX,
        })
        assert status == 200 and funded["delta_credit_wei"] == str(UNIT)
        status, after = _json_request(base, balance_path)
        assert status == 200 and after["account"]["available_credit_wei"] == str(UNIT)
        charge = ledger.spend_request_credit(
            account_id=wallet_account_id(WALLET), wallet_address=WALLET,
            request_id="req-1", session_id="sess-1", run_id="run-1", worker_id="worker-1",
            selected_price={"amount": "0.25", "unit": "credit"},
            requester_max_price={"amount": "1", "unit": "credit"}, partition="ring-1",
        )
        status, after_spend = _json_request(base, balance_path)
        assert status == 200 and after_spend["account"]["available_credit_wei"] == str(UNIT - UNIT // 4)
        status, history = _json_request(base, "/api/hub/v1/credits/charges?request_id=req-1")
        assert status == 200 and history["charges"][0]["charge_id"] == charge["charge"]["charge_id"]
        assert history["charges"][0]["charged_credit_wei"] == str(UNIT // 4)
        for path in ("/api/hub/v1/credits/wallet-funding/import", "/api/hub/v1/credits/bridge-reconciliation/execute", "/api/hub/v1/credits/bridge-reconciliation/record"):
            status, result = _json_request(base, path, {})
            assert status == 501 and result["error"] == "stable_hub_unverified_financial_operation_disabled"
        status, replay = _json_request(base, "/api/hub/v1/credits/wallet-funding/complete", {"deposit_id": DEPOSIT, "wallet_address": WALLET, "tx_hash": FUNDING_TX})
        assert status == 200 and replay["delta_credit_wei"] == "0"
        status, after_replay = _json_request(base, balance_path)
        assert after_replay["account"]["available_credit_wei"] == str(UNIT - UNIT // 4)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_disabled_financial_posts_preserve_http11_connection() -> None:
    """A rejected POST must not leave its JSON body to become a phantom request."""
    topology = load_stable_hub_topology(Path("deploy/hub-topology/dev-topology.json"))
    server = create_stable_hub_server(
        topology=topology, hub_id="dev-hub1", bind_host="127.0.0.1", bind_port=0,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        for path in (
            "/api/hub/v1/credits/wallet-funding/import",
            "/api/hub/v1/credits/bridge-reconciliation/execute",
            "/api/hub/v1/credits/bridge-reconciliation/record",
        ):
            conn.request("POST", path, body="{}", headers={"Content-Type": "application/json"})
            response = conn.getresponse()
            assert response.version == 11
            assert response.status == 501
            assert json.loads(response.read())["error"] == "stable_hub_unverified_financial_operation_disabled"
        conn.request("GET", "/health")
        response = conn.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["ok"] is True
    finally:
        conn.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_missing_real_signer_bundle_fails_closed_without_calling_completion(monkeypatch):
    import main_computer.stable_hub as module
    topology = load_stable_hub_topology(Path("deploy/hub-topology/dev-topology.json"))
    store = MemoryStore()
    server = create_stable_hub_server(topology=topology, hub_id="dev-hub1", bind_host="127.0.0.1", bind_port=0, worker_session_store=store)
    monkeypatch.setattr(module, "load_bridge_deployment", lambda config: (_ for _ in ()).throw(FileNotFoundError("missing")))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, result = _json_request(f"http://127.0.0.1:{server.server_port}", "/api/hub/v1/credits/wallet-funding/complete", {"deposit_id": DEPOSIT, "wallet_address": WALLET, "tx_hash": FUNDING_TX})
        assert status == 503 and result["error"] == "bridge_completion_unavailable"
        assert store.load().get("payout_accounts") is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_chain_receipt_without_completed_deposit_does_not_mint_credit() -> None:
    _, _, a, _ = _two_hubs()
    class IncompleteEscrow(FakeEscrow):
        def complete_deposit(self, deposit_id: str) -> dict:
            self.completions += 1
            return {"tx_hash": "0x" + "ff" * 32, "receipt": {"status": "0x1"}}
    chain = IncompleteEscrow()
    with pytest.raises(RuntimeError, match="not confirmed on-chain"):
        complete(service(a, chain))
    assert a.get_account(wallet_account_id(WALLET)) is None


def test_precompleted_verified_deposit_is_idempotent_on_shared_ledger() -> None:
    _, _, a, c = _two_hubs()
    chain = FakeEscrow()
    chain.recorded[DEPOSIT]["completed"] = True
    result = complete(service(a, chain))
    assert result["delta_credit_wei"] == str(UNIT)
    assert result["completion_sent"] is False
    assert complete(service(c, chain))["delta_credit_wei"] == "0"
    assert chain.completions == 0
    assert a.get_account(wallet_account_id(WALLET))["available_credit_wei"] == str(UNIT)


def test_actual_contract_client_requires_matching_mined_deposit_event(monkeypatch):
    """Never infer deposit credit solely from a submitted transaction hash."""
    import sys
    from main_computer.hub_credit_bridge_completion import HubCreditBridgeContractClient
    # Isolate receipt matching; the cryptographic topic computation is provided
    # by the same eth_utils.keccak interface used in production.
    fake_topic = bytes.fromhex("de" * 32)
    monkeypatch.setitem(sys.modules, "eth_utils", SimpleNamespace(keccak=lambda **_kw: fake_topic))

    def receipt(amount=UNIT, *, contract=CONTRACT, deposit=DEPOSIT, status="0x1"):
        return {
            "status": status, "blockNumber": "0x10", "transactionHash": FUNDING_TX,
            "to": CONTRACT,
            "logs": [{
                "address": contract,
                "topics": ["0x" + fake_topic.hex(), deposit, "0x" + WALLET[2:].rjust(64, "0"), "0x" + WALLET[2:].rjust(64, "0")],
                "data": "0x" + f"{amount:064x}" + f"{64:064x}",
            }],
        }

    class Rpc:
        value = receipt()
        def transaction_receipt(self, tx_hash):
            assert tx_hash == FUNDING_TX
            return self.value

    rpc = Rpc()
    client = HubCreditBridgeContractClient(
        rpc_url="http://invalid.local", contract_address=CONTRACT,
        chain_id=42424240, admin_private_key="", admin_address=WALLET,
        rpc_client=rpc,
    )
    record = DepositRecord(True, False, WALLET, WALLET, UNIT)
    assert client.verify_deposit_transaction(FUNDING_TX, deposit_id=DEPOSIT, deposit=record)["status"] == "0x1"
    for changed in (receipt(amount=2*UNIT), receipt(contract="0x"+"ff"*20), receipt(deposit=SECOND_DEPOSIT), receipt(status="0x0")):
        rpc.value = changed
        with pytest.raises(ValueError, match="bridge_deposit_transaction_"):
            client.verify_deposit_transaction(FUNDING_TX, deposit_id=DEPOSIT, deposit=record)
    rpc.value = None
    with pytest.raises(ValueError, match="not_mined"):
        client.verify_deposit_transaction(FUNDING_TX, deposit_id=DEPOSIT, deposit=record)


def test_funding_completion_rejects_missing_or_unverified_tx_hash_before_signing():
    _, stores, a, _ = _two_hubs()
    chain = FakeEscrow()
    svc = service(a, chain)
    with pytest.raises(ValueError, match="tx_hash"):
        svc.complete_wallet_funding_deposit({"deposit_id": DEPOSIT, "wallet_address": WALLET})
    with pytest.raises(ValueError, match="transaction_event_mismatch"):
        svc.complete_wallet_funding_deposit({"deposit_id": DEPOSIT, "wallet_address": WALLET, "tx_hash": "0x" + "cc"*32})
    assert chain.completions == 0
    assert stores[0].load().get("payout_deposit_intents", {}) == {}
