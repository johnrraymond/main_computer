from __future__ import annotations

import threading
from pathlib import Path

import pytest

from main_computer.stable_hub_topology import load_stable_hub_topology
from main_computer.stable_hub_worker_sessions import (
    InMemoryStableWorkerSessionStore,
    StableHubAcceptedWorkSessionDirectory,
    StableHubPayoutLedgerDirectory,
    StableHubWorkerMarketDirectory,
    StableHubWorkerSessionError,
)


DEV_TOPOLOGY = Path("deploy/stable-hub-lab/dev-topology.json")


def _topology():
    return load_stable_hub_topology(DEV_TOPOLOGY)


def _funded_ledger(store: InMemoryStableWorkerSessionStore, *, account_id: str = "acct-parity") -> StableHubPayoutLedgerDirectory:
    topology = _topology()
    ledger = StableHubPayoutLedgerDirectory(topology=topology, hub_id="dev-hub3", store=store)
    ledger.fund_account(
        account_id=account_id,
        wallet_address="0x" + "ab" * 20,
        credits="1",
        replace=True,
    )
    return ledger


def _create_hold(
    ledger: StableHubPayoutLedgerDirectory,
    *,
    account_id: str,
    request_id: str,
    session_id: str,
    worker_id: str,
) -> dict:
    return ledger.create_hold(
        account_id=account_id,
        wallet_address="0x" + "ab" * 20,
        request_id=request_id,
        session_id=session_id,
        run_id="run_" + session_id,
        worker_id=worker_id,
        selected_price={"amount": "0.25", "unit": "credit"},
        requester_max_price={"amount": "1", "unit": "credit"},
        partition="ring-1",
    )


def test_stable_backend_serializes_independent_directory_mutations_for_same_store() -> None:
    topology = _topology()
    store = InMemoryStableWorkerSessionStore()
    first = StableHubPayoutLedgerDirectory(topology=topology, hub_id="dev-hub3", store=store)
    second = StableHubAcceptedWorkSessionDirectory(topology=topology, hub_id="dev-hub3", store=store)

    assert first._lock is second._lock  # noqa: SLF001 - verifies store-level mutation boundary


def test_concurrent_same_account_holds_keep_all_holds_and_account_totals() -> None:
    store = InMemoryStableWorkerSessionStore()
    account_id = "acct-concurrent-holds"
    _funded_ledger(store, account_id=account_id)
    ledgers = [
        StableHubPayoutLedgerDirectory(topology=_topology(), hub_id="dev-hub3", store=store),
        StableHubPayoutLedgerDirectory(topology=_topology(), hub_id="dev-hub3", store=store),
    ]

    errors: list[BaseException] = []
    results: list[dict] = []

    def worker(index: int) -> None:
        try:
            results.append(
                _create_hold(
                    ledgers[index],
                    account_id=account_id,
                    request_id=f"req-concurrent-{index}",
                    session_id=f"sess_concurrent_{index}",
                    worker_id=f"worker-concurrent-{index}",
                )
            )
        except BaseException as exc:  # pragma: no cover - test failure diagnostics
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert not errors
    assert len(results) == 2
    status = ledgers[0].status()
    durable_holds = [
        hold for hold in status["holds"]
        if hold.get("account_id") == account_id and str(hold.get("request_id", "")).startswith("req-concurrent-")
    ]
    assert len(durable_holds) == 2
    account = ledgers[0].get_account(account_id)
    assert account is not None
    assert account["available_credit_wei"] == "500000000000000000"
    assert account["held_credit_wei"] == "500000000000000000"


def test_duplicate_request_index_returns_original_accepted_session() -> None:
    topology = _topology()
    store = InMemoryStableWorkerSessionStore()
    sessions_a = StableHubAcceptedWorkSessionDirectory(topology=topology, hub_id="dev-hub3", store=store)
    sessions_b = StableHubAcceptedWorkSessionDirectory(topology=topology, hub_id="dev-hub3", store=store)

    first = sessions_a.record_accepted(
        session_id="sess_request_index_a",
        run_id="run_a",
        request_id="req_indexed_once",
        requester_msk_id="msk-requester",
        requester_account_id="acct-indexed",
        requester_wallet_address="0x" + "11" * 20,
        worker_id="worker-indexed",
        worker_connection_id="conn-a",
        owner_hub_id="dev-hub3",
        owner_hub_url="http://127.0.0.1:8873",
        partition="ring-1",
        task_queue="main-computer-work-ring-1",
        work={"kind": "unit-test"},
        worker_acceptance={"type": "worker.work.accepted"},
        payout={"hold_id": "hold-a"},
    )
    second = sessions_b.record_accepted(
        session_id="sess_request_index_b",
        run_id="run_b",
        request_id="req_indexed_once",
        requester_msk_id="msk-requester",
        requester_account_id="acct-indexed",
        requester_wallet_address="0x" + "11" * 20,
        worker_id="worker-indexed",
        worker_connection_id="conn-b",
        owner_hub_id="dev-hub3",
        owner_hub_url="http://127.0.0.1:8873",
        partition="ring-1",
        task_queue="main-computer-work-ring-1",
        work={"kind": "unit-test"},
        worker_acceptance={"type": "worker.work.accepted"},
        payout={"hold_id": "hold-b"},
    )

    assert second["session_id"] == first["session_id"]
    assert sessions_a.get_session_for_request(
        requester_account_id="acct-indexed",
        request_id="req_indexed_once",
    )["session_id"] == first["session_id"]
    assert len(store.load()["accepted_sessions"]) == 1


def test_worker_capacity_reservation_rejects_second_same_epoch_claim() -> None:
    topology = _topology()
    store = InMemoryStableWorkerSessionStore()
    market = StableHubWorkerMarketDirectory(topology=topology, hub_id="dev-hub3", store=store)
    market.record_worker_live(
        worker_id="worker-reserve-once",
        owner={
            "owner_hub_id": "dev-hub3",
            "owner_hub_url": "http://127.0.0.1:8873",
            "connection_id": "conn-reserve",
            "lease_epoch": 7,
        },
        market_profile={"ring": "ring-1", "price": {"amount": "0.01", "unit": "credit"}, "max_concurrency": 1},
        worker_msk_id="msk-worker",
        worker_wallet_address="0x" + "22" * 20,
        worker_account_id="acct-worker",
    )

    reserved = market.reserve_worker_capacity(
        worker_id="worker-reserve-once",
        connection_id="conn-reserve",
        lease_epoch=7,
    )
    assert reserved["active_sessions"] == 1

    with pytest.raises(StableHubWorkerSessionError, match="worker_capacity_unavailable"):
        market.reserve_worker_capacity(
            worker_id="worker-reserve-once",
            connection_id="conn-reserve",
            lease_epoch=7,
        )


def test_settlement_and_bridge_confirmations_are_same_receipt_idempotent_only() -> None:
    store = InMemoryStableWorkerSessionStore()
    ledger = _funded_ledger(store, account_id="acct-settlement-conflict")
    hold = _create_hold(
        ledger,
        account_id="acct-settlement-conflict",
        request_id="req-settlement-conflict",
        session_id="sess_settlement_conflict",
        worker_id="worker-settlement-conflict",
    )
    charged = ledger.charge_hold(
        hold_id=hold["hold_id"],
        session_id="sess_settlement_conflict",
        request_id="req-settlement-conflict",
        worker_id="worker-settlement-conflict",
        result={"ok": True},
    )
    claim = ledger.record_worker_claim(
        worker_id="worker-settlement-conflict",
        earning_ids=[charged["worker_earning"]["earning_id"]],
        idempotency_key="claim-conflict",
    )
    batch = ledger.create_worker_settlement_batch(
        worker_id="worker-settlement-conflict",
        claim_ids=[claim["claim_id"]],
        idempotency_key="batch-conflict",
    )

    settled = ledger.settle_worker_settlement_batch(
        batch_id=batch["batch_id"],
        settlement_reference="receipt-a",
        idempotency_key="settle-a",
    )
    assert settled["status"] == "settled"
    replay = ledger.settle_worker_settlement_batch(
        batch_id=batch["batch_id"],
        settlement_reference="receipt-a",
        idempotency_key="settle-a",
    )
    assert replay["settlement_reference"] == "receipt-a"
    with pytest.raises(StableHubWorkerSessionError, match="settlement_reference_conflict"):
        ledger.settle_worker_settlement_batch(
            batch_id=batch["batch_id"],
            settlement_reference="receipt-b",
            idempotency_key="settle-b",
        )

    bridge = ledger.request_bridge_payout(
        worker_id="worker-settlement-conflict",
        batch_id=batch["batch_id"],
        idempotency_key="bridge-conflict",
    )
    confirmed = ledger.confirm_bridge_payout(
        bridge_payout_id=bridge["bridge_payout_id"],
        settlement_reference="bridge-receipt-a",
    )
    assert confirmed["status"] == "confirmed"
    confirmed_replay = ledger.confirm_bridge_payout(
        bridge_payout_id=bridge["bridge_payout_id"],
        settlement_reference="bridge-receipt-a",
    )
    assert confirmed_replay["settlement_reference"] == "bridge-receipt-a"
    with pytest.raises(StableHubWorkerSessionError, match="bridge_confirmation_reference_conflict"):
        ledger.confirm_bridge_payout(
            bridge_payout_id=bridge["bridge_payout_id"],
            settlement_reference="bridge-receipt-b",
        )
