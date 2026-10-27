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


def test_individual_verified_bridge_deposits_are_atomic_and_replay_safe() -> None:
    db, stores, a, c = _two_hubs()
    wallet = "0x" + "cd" * 20
    from main_computer.hub_credit_indexer import wallet_account_id
    account_id = wallet_account_id(wallet)
    contract = "0x" + "ee" * 20
    chain_id = 42424240
    def prepare(ledger, deposit_id, amount, cumulative):
        ledger.prepare_bridge_deposit_completion(
            account_id=account_id, wallet_address=wallet,
            deposit_id=deposit_id, chain_id=chain_id, contract_address=contract,
            deposit_amount_units=amount, chain_completed_credit_wei=cumulative,
            already_completed=True,
        )
    def credit(ledger, deposit_id, amount, cumulative):
        return ledger.record_completed_bridge_deposit(
            account_id=account_id, wallet_address=wallet,
            deposit_id=deposit_id, deposit_amount_units=amount,
            chain_completed_credit_wei=cumulative, chain_id=chain_id,
            contract_address=contract,
        )
    prepare(a, "chain-deposit-1", 10**18, 10**18)
    db.force_collision()
    with ThreadPoolExecutor(max_workers=2) as pool:
        fa = pool.submit(credit, a, "chain-deposit-1", 10**18, 10**18)
        fc = pool.submit(credit, c, "chain-deposit-1", 10**18, 10**18)
        results = [fa.result(), fc.result()]
    assert sorted(result["idempotent"] for result in results) == [False, True]
    assert db.conflicts >= 1
    assert a.get_account(account_id)["available_credit_wei"] == str(10**18)
    prepare(c, "chain-deposit-2", 2 * 10**18, 100 * 10**18)
    further = credit(c, "chain-deposit-2", 2 * 10**18, 100 * 10**18)
    assert further["delta_credit_wei"] == str(2 * 10**18)
    assert a.get_account(account_id)["bridge_completed_credit_wei"] == str(3 * 10**18)
    with pytest.raises(StableHubWorkerSessionError, match="replay_identity_conflict"):
        credit(a, "chain-deposit-2", 3 * 10**18, 100 * 10**18)
    with pytest.raises(StableHubWorkerSessionError, match="wallet_mismatch"):
        a.record_completed_bridge_deposit(
            account_id=account_id, wallet_address="0x" + "fe" * 20,
            deposit_id="chain-deposit-1", deposit_amount_units=10**18,
            chain_completed_credit_wei=10**18, chain_id=chain_id,
            contract_address=contract,
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


def test_identical_timestamps_do_not_overwrite_distinct_funding_deposits(monkeypatch: pytest.MonkeyPatch) -> None:
    """An FDB account balance must never conceal a lost deposit audit entry."""
    import main_computer.stable_hub_worker_sessions as stable
    monkeypatch.setattr(stable, "utc_now", lambda: "2026-10-13T19:53:39.025658+00:00")
    _, stores, a, _ = _two_hubs()
    wallet = "0x" + "ab" * 20
    a.fund_account(account_id="account-one", wallet_address=wallet, credits="1", replace=True)
    a.fund_account(account_id="account-one", wallet_address=wallet, credits="0.25")
    a.fund_account(account_id="account-one", wallet_address=wallet, credits="0.25")
    data = stores[0].load()
    assert a.get_account("account-one")["available_credit_wei"] == "1500000000000000000"
    assert len(data["payout_deposits"]) == 3
    assert len(data["payout_transactions"]) == 3
    assert len({item["deposit_id"] for item in data["payout_deposits"].values()}) == 3


def test_worker_capacity_requires_matching_owner_in_same_store_transaction() -> None:
    from main_computer.stable_hub_worker_sessions import StableHubWorkerSessionDirectory

    db, stores, ledger, _ = _two_hubs()
    topology = ledger.topology
    owner_dir = StableHubWorkerSessionDirectory(topology=topology, hub_id="dev-hub3", store=stores[0])
    market_dir = StableHubWorkerMarketDirectory(topology=topology, hub_id="dev-hub3", store=stores[0])
    owner = owner_dir.record_connected(
        worker_id="worker-atomic-reserve", connection_id="conn_reserve",
        multisession_key_id="msk", wallet_address="0x" + "12" * 20,
        account_id="acct",
    )
    market_dir.record_worker_live(
        worker_id="worker-atomic-reserve", owner=owner,
        market_profile={"ring": "ring-2"}, worker_msk_id="msk",
        worker_wallet_address="0x" + "12" * 20, worker_account_id="acct",
    )
    owner_dir.record_closed(worker_id="worker-atomic-reserve", connection_id="conn_reserve")
    assert market_dir.get_worker("worker-atomic-reserve")["status"] == "live"
    with pytest.raises(StableHubWorkerSessionError, match="worker_not_live"):
        market_dir.reserve_worker_capacity(
            worker_id="worker-atomic-reserve", connection_id="conn_reserve",
            lease_epoch=owner["lease_epoch"],
        )
    assert market_dir.get_worker("worker-atomic-reserve")["active_sessions"] == 0


def test_worker_owner_and_market_close_rollback_together_on_error() -> None:
    from main_computer.stable_hub_worker_sessions import StableHubWorkerSessionDirectory

    db, stores, ledger, _ = _two_hubs()
    topology = ledger.topology
    owner_dir = StableHubWorkerSessionDirectory(topology=topology, hub_id="dev-hub3", store=stores[0])
    market_dir = StableHubWorkerMarketDirectory(topology=topology, hub_id="dev-hub3", store=stores[0])
    owner = owner_dir.record_connected(
        worker_id="worker-close-rollback", connection_id="conn_rollback",
        multisession_key_id="msk", wallet_address="0x" + "12" * 20,
        account_id="acct",
    )
    market_dir.record_worker_live(
        worker_id="worker-close-rollback", owner=owner,
        market_profile={"ring": "ring-2"}, worker_msk_id="msk",
        worker_wallet_address="0x" + "12" * 20, worker_account_id="acct",
    )

    def abort_after_owner_close():
        owner_dir.record_closed(worker_id="worker-close-rollback", connection_id="conn_rollback")
        raise RuntimeError("injected fault between owner and market close")

    with pytest.raises(RuntimeError, match="injected fault"):
        stores[0].atomic(abort_after_owner_close)
    assert owner_dir.get_owner("worker-close-rollback")["status"] == "live"
    assert market_dir.get_worker("worker-close-rollback")["status"] == "live"

    def close_both():
        owner_dir.record_closed(worker_id="worker-close-rollback", connection_id="conn_rollback")
        market_dir.record_worker_closed(worker_id="worker-close-rollback", connection_id="conn_rollback")

    stores[0].atomic(close_both)
    assert owner_dir.get_owner("worker-close-rollback")["status"] == "closed"
    assert market_dir.get_worker("worker-close-rollback")["status"] == "closed"


def test_worker_close_and_hold_release_are_one_fdb_commit_with_crash_rollback() -> None:
    """An abort after owner close must not strand a committed credit hold."""
    from main_computer.stable_hub import close_worker_connection_and_settle
    from main_computer.stable_hub_worker_sessions import (
        StableHubAcceptedWorkSessionDirectory, StableHubWorkerSessionDirectory,
    )

    db, stores, ledger, _other = _two_hubs()
    store = stores[0]
    topology = load_stable_hub_topology(Path("deploy/hub-topology/dev-topology.json"))
    owner_dir = StableHubWorkerSessionDirectory(topology=topology, hub_id="dev-hub3", store=store)
    market_dir = StableHubWorkerMarketDirectory(topology=topology, hub_id="dev-hub3", store=store)
    accepted_dir = StableHubAcceptedWorkSessionDirectory(topology=topology, hub_id="dev-hub3", store=store)
    worker_id, connection_id = "worker-crash-atomic", "connection-crash-atomic"
    wallet = "0x" + "ab" * 20
    owner = owner_dir.record_connected(
        worker_id=worker_id, connection_id=connection_id,
        multisession_key_id="msk-worker", wallet_address=wallet, account_id="account-worker",
    )
    market_dir.record_worker_live(
        worker_id=worker_id, owner=owner,
        market_profile={"ring": "ring-3", "price": {"amount": "1.024", "unit": "credit"}, "max_concurrency": 1},
        worker_msk_id="msk-worker", worker_wallet_address=wallet, worker_account_id="account-worker",
    )
    ledger.fund_account(account_id="account-one", wallet_address=wallet, credits="2", replace=True)
    hold = ledger.create_hold(
        account_id="account-one", wallet_address=wallet, request_id="request-crash-atomic",
        session_id="session-crash-atomic", worker_id=worker_id, run_id="run-crash-atomic",
        partition="ring-3", selected_price={"amount": "1.024", "unit": "credit"},
        requester_max_price={"amount": "2", "unit": "credit"},
    )
    accepted_dir.record_accepted(
        session_id="session-crash-atomic", run_id="run-crash-atomic", request_id="request-crash-atomic",
        requester_msk_id="msk-requester", requester_account_id="account-one",
        requester_wallet_address=wallet, worker_id=worker_id, worker_connection_id=connection_id,
        owner_hub_id="dev-hub3", owner_hub_url="http://127.0.0.1:8873", partition="ring-3",
        task_queue="main-computer-work-ring-3", work={"kind": "unit-test"},
        worker_acceptance={"type": "worker.work.accepted"},
        payout={"hold_id": hold["hold_id"], "hold_status": "held"},
    )
    server = SimpleNamespace(
        worker_session_directory=owner_dir, worker_market_directory=market_dir,
        accepted_work_session_directory=accepted_dir, payout_ledger_directory=ledger,
    )
    original_atomic = store.atomic
    before = store.load()
    commits_before = db.commits

    def abort_before_commit(action):
        # Nested directory calls join the already-open FDB transaction. Inject
        # an exception only on the top-level close, after all its writes.
        if getattr(store._transaction_local, "transaction", None) is not None:
            return original_atomic(action)

        def crashed():
            action()
            raise RuntimeError("injected_crash_before_fdb_commit")
        return original_atomic(crashed)

    store.atomic = abort_before_commit
    try:
        with pytest.raises(RuntimeError, match="injected_crash_before_fdb_commit"):
            close_worker_connection_and_settle(server, worker_id=worker_id, connection_id=connection_id)
    finally:
        store.atomic = original_atomic
    assert db.commits == commits_before
    assert store.load() == before
    assert owner_dir.get_owner(worker_id)["status"] == "live"
    assert ledger.get_hold_for_request(account_id="account-one", request_id="request-crash-atomic")["status"] == "held"

    closed_owner, count = close_worker_connection_and_settle(
        server, worker_id=worker_id, connection_id=connection_id,
    )
    assert closed_owner["status"] == "closed"
    assert count == 1
    assert db.commits == commits_before + 1
    assert accepted_dir.get_session("session-crash-atomic")["status"] == "failed"
    assert ledger.get_hold_for_request(account_id="account-one", request_id="request-crash-atomic")["status"] == "released"
    assert ledger.get_account("account-one")["available_credit_wei"] == str(2 * 10**18)

    # A repeat close cannot issue a second refund or a second failure record.
    assert close_worker_connection_and_settle(
        server, worker_id=worker_id, connection_id=connection_id,
    )[1] == 0
    assert ledger.get_account("account-one")["available_credit_wei"] == str(2 * 10**18)
