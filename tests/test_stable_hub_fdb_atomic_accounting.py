"""Regression tests for Stable Hub cross-process FoundationDB accounting.

A transactional fake models FDB's optimistic read/write conflict and automatic
whole-callback retry. No external FDB server, blockchain, or funds are needed.
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from main_computer.stable_hub_topology import load_stable_hub_topology
from main_computer.stable_hub_worker_sessions import (
    FoundationDbStableWorkerSessionStore,
    StableHubPayoutLedgerDirectory,
    StableHubWorkerMarketDirectory,
    StableHubWorkerSessionError,
    _SHARED_STORE_LOCKS,
    _SHARED_STORE_LOCKS_GUARD,
)


class _Conflict(Exception):
    pass


class _Future:
    def __init__(self, value: bytes | None) -> None:
        self.value = value

    def wait(self) -> bytes | None:
        return self.value


class _FakeTransaction:
    def __init__(self, database: "_FakeDatabase") -> None:
        self.database = database
        with database.lock:
            self.snapshot = dict(database.data)
            self.versions = dict(database.versions)
        self.reads: set[bytes] = set()
        self.writes: dict[bytes, bytes | None] = {}
        self.options = SimpleNamespace(set_timeout=lambda value: None, set_retry_limit=lambda value: None)

    def __getitem__(self, key: bytes) -> _Future:
        self.reads.add(key)
        if key in self.writes:
            return _Future(self.writes[key])
        return _Future(self.snapshot.get(key))

    def __setitem__(self, key: bytes, value: bytes) -> None:
        self.writes[key] = value

    def __delitem__(self, key: bytes) -> None:
        self.writes[key] = None

    def commit(self) -> None:
        barrier = self.database.commit_barrier
        if self.writes and barrier is not None:
            with self.database.lock:
                use_barrier = self.database.barrier_slots > 0
                if use_barrier:
                    self.database.barrier_slots -= 1
            if use_barrier:
                barrier.wait(timeout=5)
        with self.database.lock:
            if any(self.database.versions.get(k, 0) != self.versions.get(k, 0) for k in self.reads):
                self.database.conflicts += 1
                raise _Conflict("FDB conflict: the document changed after its read")
            for k, value in self.writes.items():
                self.database.version += 1
                self.database.versions[k] = self.database.version
                if value is None:
                    self.database.data.pop(k, None)
                else:
                    self.database.data[k] = value
            self.database.commits += bool(self.writes)


class _FakeDatabase:
    def __init__(self) -> None:
        self.data: dict[bytes, bytes] = {}
        self.versions: dict[bytes, int] = {}
        self.version = 0
        self.lock = threading.Lock()
        self.commits = 0
        self.conflicts = 0
        self.commit_barrier: threading.Barrier | None = None
        self.barrier_slots = 0

    def force_collision(self) -> None:
        self.commit_barrier = threading.Barrier(2)
        self.barrier_slots = 2


class _FakeFdb:
    tuple = SimpleNamespace(pack=lambda values: json.dumps(values, separators=(",", ":")).encode())

    def transactional(self, func: Any) -> Any:
        def invoke(database: _FakeDatabase) -> Any:
            for _ in range(10):
                transaction = _FakeTransaction(database)
                try:
                    result = func(transaction)
                    transaction.commit()
                    return result
                except _Conflict:
                    continue
            raise _Conflict("Exceeded 10 optimistic transaction attempts")
        return invoke


def _two_hubs():
    topology = load_stable_hub_topology(Path("deploy/hub-topology/dev-topology.json"))
    database = _FakeDatabase()
    fake_fdb = _FakeFdb()
    stores = []
    for _ in range(2):
        store = FoundationDbStableWorkerSessionStore(cluster_file="test.cluster", namespace="stable-atomic-test")
        store._opened = True
        store._db = database
        store._fdb = fake_fdb
        stores.append(store)
    a = StableHubPayoutLedgerDirectory(topology=topology, hub_id="dev-hub3", store=stores[0])
    c = StableHubPayoutLedgerDirectory(topology=topology, hub_id="dev-hub3", store=stores[1])
    # In production these are separate processes, with no shared Python lock.
    # Give the two test-directory objects distinct locks to simulate that fact.
    c._lock = threading.Lock()
    return database, stores, a, c


def _spend(ledger: StableHubPayoutLedgerDirectory, request: str, amount: str) -> dict[str, Any]:
    return ledger.spend_request_credit(
        account_id="account-one",
        wallet_address="0x" + "ab" * 20,
        request_id=request,
        session_id="sess_" + request,
        run_id="run_" + request,
        worker_id="worker-" + request,
        selected_price={"amount": amount, "unit": "credit"},
        requester_max_price={"amount": "10", "unit": "credit"},
        partition="ring-1",
        result={"ok": True},
    )


def test_two_hubs_retry_conflict_instead_of_losing_a_funding_update() -> None:
    db, stores, a, c = _two_hubs()
    a.fund_account(account_id="account-one", wallet_address="0x" + "ab" * 20, credits="1", replace=True)
    db.force_collision()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda ledger: ledger.fund_account(account_id="account-one", wallet_address="0x" + "ab" * 20, credits="0.25"),
            (a, c),
        ))
    assert db.conflicts >= 1
    assert len(results) == 2
    account = a.get_account("account-one")
    assert account["available_credit_wei"] == "1500000000000000000"
    assert len(stores[1].load()["payout_deposits"]) == 3


def test_two_hubs_cannot_double_spend_or_double_pay_one_available_balance() -> None:
    db, stores, a, c = _two_hubs()
    a.fund_account(account_id="account-one", wallet_address="0x" + "ab" * 20, credits="1", replace=True)
    db.force_collision()
    def attempt(ledger: StableHubPayoutLedgerDirectory, req: str) -> Any:
        try:
            return _spend(ledger, req, "0.75")
        except StableHubWorkerSessionError as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        fa = pool.submit(attempt, a, "one")
        fc = pool.submit(attempt, c, "two")
        outcomes = [fa.result(), fc.result()]
    assert db.conflicts >= 1
    success = [item for item in outcomes if isinstance(item, dict)]
    assert len(success) == 1
    assert outcomes.count("requester_funds_unavailable") == 1
    saved = stores[0].load()
    account = a.get_account("account-one")
    assert account["available_credit_wei"] == "250000000000000000"
    assert account["spent_credit_wei"] == "750000000000000000"
    assert len(saved["payout_charges"]) == len(saved["worker_earnings"]) == 1
    winner_req = "one" if success[0]["charge"]["request_id"] == "one" else "two"
    replay = _spend(a, winner_req, "0.75")
    assert replay["idempotent"] is True
    assert len(stores[1].load()["payout_charges"]) == 1


def test_cumulative_verified_bridge_watermark_is_atomic_and_replay_safe() -> None:
    db, stores, a, c = _two_hubs()
    wallet = "0x" + "cd" * 20
    db.force_collision()
    def complete(ledger: StableHubPayoutLedgerDirectory) -> dict[str, Any]:
        return ledger.record_completed_bridge_deposit(
            account_id="bridged-account", wallet_address=wallet,
            deposit_id="chain-deposit-1", chain_completed_credit_wei=10**18,
        )
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(complete, a)
        second = pool.submit(complete, c)
        results = [first.result(), second.result()]
    assert sorted(result["idempotent"] for result in results) == [False, True]
    assert db.conflicts >= 1
    assert a.get_account("bridged-account")["available_credit_wei"] == str(10**18)
    further = c.record_completed_bridge_deposit(
        account_id="bridged-account", wallet_address=wallet,
        deposit_id="chain-deposit-2", chain_completed_credit_wei=3 * 10**18,
    )
    assert further["delta_credit_wei"] == str(2 * 10**18)
    assert a.get_account("bridged-account")["bridge_completed_credit_wei"] == str(3 * 10**18)
    with pytest.raises(StableHubWorkerSessionError, match="ahead_of_chain"):
        a.record_completed_bridge_deposit(
            account_id="bridged-account", wallet_address=wallet,
            deposit_id="old-deposit", chain_completed_credit_wei=2 * 10**18,
        )
    with pytest.raises(StableHubWorkerSessionError, match="wallet_mismatch"):
        a.record_completed_bridge_deposit(
            account_id="bridged-account", wallet_address="0x" + "fe" * 20,
            deposit_id="wrong-wallet", chain_completed_credit_wei=4 * 10**18,
        )
    saved = stores[0].load()
    assert len(saved["payout_deposits"]) == 2
    assert len([t for t in saved["payout_transactions"].values() if t["transaction_type"] == "bridge_deposit_completed"]) == 2


def test_market_mutation_cannot_overwrite_a_separate_hubs_account_update() -> None:
    db, stores, a, c = _two_hubs()
    a.fund_account(account_id="account-one", wallet_address="0x" + "ab" * 20, credits="1", replace=True)
    topology = a.topology
    market = StableHubWorkerMarketDirectory(topology=topology, hub_id="dev-hub3", store=stores[1])
    market._lock = threading.Lock()  # isolated Hub C process
    db.force_collision()
    def market_update() -> Any:
        return market.record_worker_live(
            worker_id="worker-market",
            owner={"owner_hub_id": "dev-hub3", "owner_hub_url": "http://127.0.0.1:8873", "connection_id": "conn-1", "lease_epoch": 1},
            market_profile={"ring": "ring-1", "price": {"amount": "0.1", "unit": "credit"}},
            worker_msk_id="msk-worker", worker_wallet_address="0x" + "ef" * 20, worker_account_id="account-worker",
        )
    with ThreadPoolExecutor(max_workers=2) as pool:
        fa = pool.submit(a.fund_account, account_id="account-one", wallet_address="0x" + "ab" * 20, credits="0.5")
        fm = pool.submit(market_update)
        fa.result()
        fm.result()
    assert db.conflicts >= 1
    assert a.get_account("account-one")["available_credit_wei"] == "1500000000000000000"
    assert stores[0].load()["market_workers"]["worker-market"]["status"] == "live"


def test_existing_legacy_document_and_balances_survive_first_atomic_write() -> None:
    db, stores, a, _ = _two_hubs()
    legacy_key = stores[0]._legacy_key()
    db.data[legacy_key] = json.dumps({
        "payout_accounts": {"legacy": {
            "account_id": "legacy", "wallet_address": "0x" + "ab" * 20,
            "available_credit_wei": str(4 * 10**18), "held_credit_wei": "0", "spent_credit_wei": "0",
            "earned_credit_wei": "0", "bridge_completed_credit_wei": "0"
        }},
        "accepted_sessions": {"old_session": {"session_id": "old_session"}},
    }).encode("utf-8")
    assert a.get_account("legacy")["available_credit_wei"] == str(4 * 10**18)
    a.fund_account(account_id="legacy", wallet_address="0x" + "ab" * 20, credits="1")
    saved = stores[1].load()
    assert saved["payout_accounts"]["legacy"]["available_credit_wei"] == str(5 * 10**18)
    assert "old_session" in saved["accepted_sessions"]
    assert legacy_key not in db.data
    assert stores[0]._meta_key() in db.data


def test_fdb_detached_save_is_rejected_before_mutation() -> None:
    db, stores, _, _ = _two_hubs()
    with pytest.raises(RuntimeError, match="requires store.atomic"):
        stores[0].save({"payout_accounts": {"bad": {"account_id": "bad"}}})
    assert not db.data


def test_same_request_cannot_be_charged_again_with_changed_session_or_worker() -> None:
    _, stores, a, c = _two_hubs()
    a.fund_account(account_id="account-one", wallet_address="0x" + "ab" * 20, credits="1", replace=True)
    paid = _spend(a, "same-request", "0.3")
    assert paid["idempotent"] is False
    with pytest.raises(StableHubWorkerSessionError, match="request_charge_conflict"):
        c.spend_request_credit(
            account_id="account-one", wallet_address="0x" + "ab" * 20,
            request_id="same-request", session_id="sess_second", run_id="run_second",
            worker_id="another-worker", selected_price={"amount": "0.3", "unit": "credit"},
            requester_max_price={"amount": "1", "unit": "credit"}, partition="ring-1",
            result={"ok": True},
        )
    assert len(stores[0].load()["payout_charges"]) == 1
    assert len(stores[0].load()["worker_earnings"]) == 1
    assert a.get_account("account-one")["available_credit_wei"] == "700000000000000000"


def _reserve(ledger: StableHubPayoutLedgerDirectory, request: str, amount: str = "0.75") -> dict[str, Any]:
    return ledger.create_hold(
        account_id="account-one", wallet_address="0x" + "ab" * 20,
        request_id=request, session_id="sess_" + request,
        worker_id="worker-one", selected_price={"amount": amount, "unit": "credit"},
        requester_max_price={"amount": "1", "unit": "credit"},
    )


def test_cross_hub_reservations_never_overspend_available_balance() -> None:
    db, stores, a, c = _two_hubs()
    a.fund_account(account_id="account-one", wallet_address="0x" + "ab" * 20, credits="1", replace=True)
    db.force_collision()

    def attempt(ledger: StableHubPayoutLedgerDirectory, request: str) -> Any:
        try:
            return _reserve(ledger, request)
        except StableHubWorkerSessionError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(attempt, a, "one")
        second = pool.submit(attempt, c, "two")
        outcomes = [first.result(), second.result()]
    assert db.conflicts >= 1
    assert sum(isinstance(item, dict) for item in outcomes) == 1
    assert outcomes.count("requester_funds_unavailable") == 1
    assert a.get_account("account-one")["available_credit_wei"] == "250000000000000000"
    assert a.get_account("account-one")["held_credit_wei"] == "750000000000000000"
    assert len(stores[0].load()["payout_charges"]) == 0
    assert len(stores[0].load()["worker_earnings"]) == 0


def test_cross_hub_success_settlement_charges_one_reserved_request() -> None:
    _, stores, a, c = _two_hubs()
    a.fund_account(account_id="account-one", wallet_address="0x" + "ab" * 20, credits="1", replace=True)
    hold = _reserve(a, "success")
    assert _reserve(c, "success")["hold_id"] == hold["hold_id"]
    assert a.get_account("account-one")["spent_credit_wei"] == "0"
    assert not stores[0].load()["worker_earnings"]
    kwargs = dict(account_id="account-one", wallet_address="0x" + "ab" * 20,
                  request_id="success", session_id="sess_success", run_id="run_success",
                  worker_id="worker-one", selected_price={"amount": "0.75", "unit": "credit"},
                  requester_max_price={"amount": "1", "unit": "credit"},
                  partition="ring-1", hold_id=hold["hold_id"], result={"ok": True})
    first = c.spend_request_credit(**kwargs)
    replay = a.spend_request_credit(**kwargs)
    assert first["idempotent"] is False and replay["idempotent"] is True
    assert c.get_hold_for_request(account_id="account-one", request_id="success")["status"] == "charged"
    account = a.get_account("account-one")
    assert account["available_credit_wei"] == "250000000000000000"
    assert account["held_credit_wei"] == "0"
    assert account["spent_credit_wei"] == "750000000000000000"
    assert len(stores[0].load()["payout_charges"]) == len(stores[0].load()["worker_earnings"]) == 1
    with pytest.raises(StableHubWorkerSessionError, match="already_charged"):
        a.release_hold(hold_id=hold["hold_id"], session_id="sess_success")


def test_cross_hub_worker_failure_releases_hold_without_earning() -> None:
    _, stores, a, c = _two_hubs()
    a.fund_account(account_id="account-one", wallet_address="0x" + "ab" * 20, credits="1", replace=True)
    hold = _reserve(a, "failed")
    released = c.release_hold(hold_id=hold["hold_id"], session_id="sess_failed")
    assert released["status"] == "released"
    assert a.release_hold(hold_id=hold["hold_id"], session_id="sess_failed") == released
    assert a.get_account("account-one")["available_credit_wei"] == "1000000000000000000"
    assert a.get_account("account-one")["held_credit_wei"] == "0"
    assert a.get_account("account-one")["spent_credit_wei"] == "0"
    assert not stores[1].load()["payout_charges"]
    assert not stores[1].load()["worker_earnings"]
    with pytest.raises(StableHubWorkerSessionError, match="already_finalized"):
        _reserve(a, "failed")


def test_pending_holds_are_visible_in_read_only_payout_status() -> None:
    _, _, a, _ = _two_hubs()
    a.fund_account(account_id="account-one", wallet_address="0x" + "ab" * 20, credits="1", replace=True)
    _reserve(a, "pending", "0.6")
    status = a.status()
    assert status["totals"]["total_held_credit_wei"] == "600000000000000000"
    assert status["totals"]["total_available_credit_wei"] == "400000000000000000"
    assert any(hold.get("request_id") == "pending" and hold.get("status") == "held" for hold in status["holds"])


def test_incomplete_charge_record_fails_closed_instead_of_debiting_again() -> None:
    _, stores, a, c = _two_hubs()
    a.fund_account(account_id="account-one", wallet_address="0x" + "ab" * 20, credits="1", replace=True)
    _spend(a, "inconsistent", "0.3")
    before = dict(a.get_account("account-one") or {})

    def delete_earning() -> None:
        data = stores[0].load()
        data["worker_earnings"].clear()
        stores[0].save(data)

    stores[0].atomic(delete_earning)
    with pytest.raises(StableHubWorkerSessionError, match="request_charge_earning_inconsistent"):
        _spend(c, "inconsistent", "0.3")
    assert a.get_account("account-one")["available_credit_wei"] == before["available_credit_wei"]
    assert a.get_account("account-one")["spent_credit_wei"] == before["spent_credit_wei"]
