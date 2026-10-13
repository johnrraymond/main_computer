"""P0: Experimental live-session accounting matches Stable Hub terminal semantics.

The in-memory transactional fake tests the same FDB read/write keys and models,
without claiming a live server or blockchain integration test.
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from main_computer.credit_units import CREDIT_WEI_PER_CREDIT
from main_computer.exp_fdb_credit_ledger import ExperimentalFoundationDbCreditLedger


class Future:
    def __init__(self, value):
        self.value = value

    def wait(self):
        return self.value


class Tx:
    def __init__(self, db):
        self.db = db
        self.data = db.values

    def __getitem__(self, key):
        return Future(self.data.get(key))

    def __setitem__(self, key, value):
        self.data[key] = value

    def add_read_conflict_key(self, key):
        pass

    def add_write_conflict_key(self, key):
        pass

    def get_range(self, start, stop):
        return [SimpleNamespace(value=value) for key, value in sorted(self.data.items()) if start <= key < stop]


class DB:
    def __init__(self):
        self.values = {}
        self.lock = threading.RLock()


class FakeFDB:
    def transactional(self, callback):
        def wrapped(db):
            with db.lock:
                return callback(Tx(db))
        return wrapped


def ledger(db=None):
    db = db or DB()
    store = object.__new__(ExperimentalFoundationDbCreditLedger)
    store.db = db
    store.fdb = FakeFDB()
    store.namespace = 'test-exp-hold-lifecycle'
    store.pack = lambda *parts: ('/'.join((store.namespace, *map(str, parts)))).encode()
    store.range_for = lambda *parts: slice(store.pack(*parts) + b'/', store.pack(*parts) + b'0')
    return store


def seed(store, credits=5):
    store.issue(account_id='requester', credits=credits)


def test_success_only_charges_after_hold_and_only_once():
    store = ledger()
    seed(store)
    held = store.create_hold(account_id='requester', request_id='req-success', credits=2)
    hold_id = held['hold']['hold_id']
    assert store.get_account('requester').available_credits == 3
    assert store.get_account('requester').held_credits == 2
    assert store.list_charges(request_id='req-success') == []
    assert store.list_worker_earnings(request_id='req-success') == []
    charge = store.charge_hold(hold_id=hold_id, charged_credits=2, worker_node_id='worker-a')
    replay = store.charge_hold(hold_id=hold_id, charged_credits=2, worker_node_id='worker-a')
    assert not charge['idempotent']
    assert replay['idempotent']
    assert replay['charge']['charge_id'] == charge['charge']['charge_id']
    assert store.get_account('requester').available_credits == 3
    assert store.get_account('requester').held_credits == 0
    assert store.get_account('requester').spent_credits == 2
    assert len(store.list_charges(request_id='req-success')) == 1
    assert len(store.list_worker_earnings(request_id='req-success')) == 1
    with pytest.raises(ValueError, match='charged hold'):
        store.release_hold(hold_id=hold_id)


def test_worker_failure_releases_hold_no_charge_or_earning():
    store = ledger()
    seed(store)
    hold_id = store.create_hold(account_id='requester', request_id='req-fail', credits=2)['hold']['hold_id']
    result = store.release_hold(hold_id=hold_id, reason='worker_failed')
    replay = store.release_hold(hold_id=hold_id, reason='worker_failed')
    assert result['hold']['status'] == 'released'
    assert replay['idempotent']
    assert store.get_account('requester').available_credits == 5
    assert store.get_account('requester').held_credits == 0
    assert store.get_account('requester').spent_credits == 0
    assert store.list_worker_earnings(request_id='req-fail') == []
    assert store.list_charges(request_id='req-fail') == []
    with pytest.raises(ValueError, match='terminal hold'):
        store.charge_hold(hold_id=hold_id, charged_credits=2, worker_node_id='worker-a')


def test_simultaneous_requests_cannot_spend_reserved_funds():
    store = ledger()
    seed(store, credits=3)
    barrier = threading.Barrier(2)
    def reserve(request_id):
        barrier.wait()
        try:
            return store.create_hold(account_id='requester', request_id=request_id, credits=2)['hold']['status']
        except ValueError:
            return 'insufficient'
    with ThreadPoolExecutor(max_workers=2) as executor:
        result = list(executor.map(reserve, ['a', 'b']))
    assert sorted(result) == ['held', 'insufficient']
    assert store.get_account('requester').held_credits == 2
    assert store.get_account('requester').available_credits == 1
    with pytest.raises(ValueError, match='Insufficient'):
        store.spend_request_credit(account_id='requester', request_id='direct', credits=2)


def test_status_and_hold_query_show_real_reservations():
    store = ledger()
    seed(store)
    store.create_hold(account_id='requester', request_id='req-status', credits=2)
    assert len(store.list_holds(active_only=True)) == 1
    status = store.status()
    assert status['active_hold_count'] == 1
    assert status['totals']['held_credits'] == 2
    assert status['totals']['available_credits'] == 3
    assert status['totals']['charged_credits'] == 0


def test_hold_idempotency_rejects_amount_mismatch_and_terminal_reactivation():
    store = ledger()
    seed(store)
    first = store.create_hold(account_id='requester', request_id='req-idem', credits=1)
    assert store.create_hold(account_id='requester', request_id='req-idem', credits=1)['idempotent']
    with pytest.raises(ValueError, match='does not match'):
        store.create_hold(account_id='requester', request_id='req-idem', credits=2)
    store.release_hold(hold_id=first['hold']['hold_id'])
    with pytest.raises(ValueError, match='Cannot reactivate'):
        store.create_hold(account_id='requester', request_id='req-idem', credits=1)


def test_partial_charge_releases_unused_reservation():
    store = ledger()
    seed(store)
    hold_id = store.create_hold(account_id='requester', request_id='req-partial', credits=3)['hold']['hold_id']
    result = store.charge_hold(hold_id=hold_id, charged_credits=2, worker_node_id='worker-a')
    assert result['charge']['released_credits'] == 1
    assert store.get_account('requester').available_credits == 3
    assert store.get_account('requester').spent_credits == 2
    assert store.get_account('requester').held_credits == 0
