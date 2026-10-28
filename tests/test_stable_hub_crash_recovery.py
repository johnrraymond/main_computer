"""Patch 2: deterministic survivor fencing, atomic rollback and credit proofs.

A transactional shared store models the durable FDB atomic callback. The
separate OS-process kill campaign is in tools/stable_hub_lab/crash_recovery.py.
"""
from __future__ import annotations

import copy
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from main_computer.stable_hub_recovery import recover_expired_worker_lease, recover_expired_worker_leases
from main_computer.stable_hub_topology import load_stable_hub_topology
from main_computer.stable_hub_worker_sessions import (
    StableHubAcceptedWorkSessionDirectory, StableHubPayoutLedgerDirectory,
    StableHubWorkerMarketDirectory, StableHubWorkerSessionDirectory,
    StableHubWorkerSessionError, _normalize_store,
)


class TransactionalMemoryStore:
    def __init__(self):
        self.document = _normalize_store({})
        self.mutex = threading.RLock()

    def load(self):
        with self.mutex:
            return copy.deepcopy(self.document)

    def save(self, data):
        with self.mutex:
            self.document = _normalize_store(copy.deepcopy(data))

    def atomic(self, operation):
        with self.mutex:
            before = copy.deepcopy(self.document)
            try:
                return operation()
            except BaseException:
                self.document = before
                raise


def _server(store, hub_id):
    topology = load_stable_hub_topology(Path('deploy/hub-topology/smoke-topology.json'))
    hub = topology.hub_by_id(hub_id)
    return SimpleNamespace(
        topology=topology, hub=hub,
        worker_session_directory=StableHubWorkerSessionDirectory(topology=topology, hub_id=hub_id, store=store),
        worker_market_directory=StableHubWorkerMarketDirectory(topology=topology, hub_id=hub_id, store=store),
        accepted_work_session_directory=StableHubAcceptedWorkSessionDirectory(topology=topology, hub_id=hub_id, store=store),
        payout_ledger_directory=StableHubPayoutLedgerDirectory(topology=topology, hub_id=hub_id, store=store),
        get_live_worker_session=lambda connection_id: None,
    )


def _scenario(*, hours_old=2, accepted=True):
    store = TransactionalMemoryStore()
    victim = _server(store, 'smoke-hub3')
    survivor = _server(store, 'smoke-hub1')
    account = 'captain-abc'
    wallet = '0x' + 'ab' * 20
    worker = 'conn_one'
    owner = victim.worker_session_directory.record_connected(
        worker_id=worker, connection_id=worker, multisession_key_id='msk_worker',
        wallet_address='0x' + 'cd' * 20, account_id='worker-abc',
    )
    victim.worker_market_directory.record_worker_live(
        worker_id=worker, owner=owner,
        market_profile={'rings': ['ring-2'], 'capabilities': ['python'],
                        'price': {'amount': '0.512', 'unit': 'credit'}, 'max_concurrency': 1},
        worker_msk_id='msk_worker', worker_wallet_address='0x' + 'cd' * 20,
        worker_account_id='worker-abc',
    )
    victim.payout_ledger_directory.fund_account(
        account_id=account, wallet_address=wallet, credits='1', replace=True,
    )
    if accepted:
        hold = victim.payout_ledger_directory.create_hold(
            account_id=account, wallet_address=wallet, request_id='req_one',
            session_id='sess_one', worker_id=worker, run_id='run_one', partition='ring-2',
            selected_price={'amount': '0.512', 'unit': 'credit'},
            requester_max_price={'amount': '1', 'unit': 'credit'},
        )
        victim.accepted_work_session_directory.record_accepted(
            session_id='sess_one', run_id='run_one', request_id='req_one',
            requester_msk_id='msk_captain', requester_account_id=account,
            requester_wallet_address=wallet, worker_id=worker,
            worker_connection_id=worker, owner_hub_id='smoke-hub3',
            owner_hub_url=victim.hub.hub_url, partition='ring-2',
            task_queue='main-computer-work-ring-2', work={'ring': 'ring-2'},
            worker_acceptance={'type': 'worker.work.accepted'},
            payout={'hold_id': hold['hold_id'], 'hold_status': 'held'},
        )
    now = datetime.now(timezone.utc)
    if hours_old:
        data = store.load()
        old = (now-timedelta(hours=hours_old)).isoformat()
        data['workers'][worker]['last_pong_at'] = old
        data['workers'][worker]['connected_at'] = old
        store.save(data)
    return store, victim, survivor, now


def _money(store):
    data = store.load()
    return data['payout_accounts']['captain-abc'], data


def test_survivor_reconciles_abandoned_acceptance_in_one_atomic_transaction():
    store, victim, survivor, now = _scenario()
    result = recover_expired_worker_leases(survivor, now=now, lease_seconds=90)
    account, data = _money(store)
    assert result['recovered'] == result['failed_sessions'] == 1
    assert not result['errors']
    assert data['workers']['conn_one']['status'] == 'closed'
    assert data['market_workers']['conn_one']['status'] == 'closed'
    assert data['workers']['conn_one']['recovered_by_hub_id'] == 'smoke-hub1'
    assert data['accepted_sessions']['sess_one']['status'] == 'failed'
    assert data['accepted_sessions']['sess_one']['worker_failure']['error']['reason_code'] == 'worker_owner_lease_expired_recovered'
    assert next(iter(data['payout_holds'].values()))['status'] == 'released'
    assert int(account['available_credit_wei']) == 10**18
    assert int(account['held_credit_wei']) == int(account['spent_credit_wei']) == 0
    assert data['payout_charges'] == data['worker_earnings'] == {}
    assert recover_expired_worker_leases(survivor, now=now, lease_seconds=90)['recovered'] == 0
    with pytest.raises(StableHubWorkerSessionError, match='worker_owner_lease_fenced'):
        victim.worker_session_directory.record_pong(worker_id='conn_one', connection_id='conn_one')


def test_fresh_heartbeat_prevents_lease_recovery():
    store, _, survivor, now = _scenario(hours_old=0)
    assert recover_expired_worker_leases(survivor, now=now, lease_seconds=90)['recovered'] == 0
    account, data = _money(store)
    assert data['workers']['conn_one']['status'] == 'live'
    assert int(account['held_credit_wei']) == 512_000_000_000_000_000


def test_changed_generation_is_fenced_against_obsolete_recovery_attempt():
    store, victim, survivor, now = _scenario()
    # The current Worker has reconnected with a distinct, durable generation.
    victim.worker_session_directory.record_connected(
        worker_id='conn_one', connection_id='conn_new',
        multisession_key_id='msk_worker', wallet_address='0x' + 'cd' * 20,
        account_id='worker-abc',
    )
    outcome = recover_expired_worker_lease(
        survivor, worker_id='conn_one', connection_id='conn_one', lease_epoch=1,
        now=now, lease_seconds=90,
    )
    assert outcome['reason'] == 'generation_changed'
    account, data = _money(store)
    assert data['workers']['conn_one']['connection_id'] == 'conn_new'
    assert data['workers']['conn_one']['status'] == 'live'
    assert data['accepted_sessions']['sess_one']['status'] == 'accepted'
    assert int(account['held_credit_wei']) == 512_000_000_000_000_000


def test_corrupt_hold_fails_closed_without_partial_owner_closure():
    store, _, survivor, now = _scenario()
    data = store.load()
    data['payout_holds'][next(iter(data['payout_holds']))]['status'] = 'charged'
    store.save(data)
    with pytest.raises(StableHubWorkerSessionError, match='recovery_open_session_hold_not_proven_held'):
        recover_expired_worker_lease(
            survivor, worker_id='conn_one', connection_id='conn_one', lease_epoch=1,
            now=now, lease_seconds=90,
        )
    data = store.load()
    assert data['workers']['conn_one']['status'] == 'live'
    assert data['accepted_sessions']['sess_one']['status'] == 'accepted'


def test_failed_cleanup_retries_and_rollback_preserves_all_holds(monkeypatch):
    store, _, survivor, now = _scenario()
    method = survivor.payout_ledger_directory.release_hold
    def fail_once(**kwargs):
        raise RuntimeError('injected FDB cleanup outage')
    monkeypatch.setattr(survivor.payout_ledger_directory, 'release_hold', fail_once)
    error = recover_expired_worker_leases(survivor, now=now, lease_seconds=90)
    assert error['recovered'] == 0 and len(error['errors']) == 1
    account, data = _money(store)
    assert data['workers']['conn_one']['status'] == 'live'
    assert data['market_workers']['conn_one']['status'] == 'live'
    assert data['accepted_sessions']['sess_one']['status'] == 'accepted'
    assert int(account['held_credit_wei']) == 512_000_000_000_000_000
    monkeypatch.setattr(survivor.payout_ledger_directory, 'release_hold', method)
    assert recover_expired_worker_leases(survivor, now=now, lease_seconds=90)['recovered'] == 1
    account, data = _money(store)
    assert int(account['held_credit_wei']) == 0


def test_closed_owner_with_unfinished_hold_is_retried_even_if_heartbeat_recent():
    store, _, survivor, now = _scenario(hours_old=0)
    data = store.load()
    data['workers']['conn_one']['status'] = 'closed'
    data['market_workers']['conn_one']['status'] = 'closed'
    store.save(data)
    assert recover_expired_worker_leases(survivor, now=now, lease_seconds=90)['failed_sessions'] == 1
    assert _money(store)[1]['accepted_sessions']['sess_one']['status'] == 'failed'


def test_expired_connection_without_acceptance_is_retired_without_credit_mutation():
    store, _, survivor, now = _scenario(accepted=False)
    assert recover_expired_worker_leases(survivor, now=now, lease_seconds=90)['recovered'] == 1
    account, data = _money(store)
    assert data['workers']['conn_one']['status'] == 'closed'
    assert int(account['available_credit_wei']) == 10**18
    assert not data['payout_holds']
