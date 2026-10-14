"""P3 Stable ownership contract: batch and bridge payout are one-owner resources."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from main_computer.stable_hub_worker_sessions import (
    StableHubPayoutLedgerDirectory, StableHubWorkerSessionError,
)
from test_settlement_stress_campaign_a import make_store
from tools.settlement_stress.run_lab import (
    WORKER, attempt, audit, intentions, new_batch, new_payout, seed, settle, snapshot,
)


def test_stable_batch_refuses_claim_already_owned_by_another_batch():
    ledger = make_store('stable')
    initial = seed('stable', 'batch', ledger)
    a, b, c = initial['claim_ids']
    first = new_batch('stable', ledger, [a, b], 'batch-first')
    assert first['claim_ids'] == [a, b]
    with pytest.raises(StableHubWorkerSessionError, match='worker_claim_already_batched'):
        new_batch('stable', ledger, [b, c], 'batch-second')
    assert [x['batch_id'] for x in snapshot('stable', ledger)['settlement_batches']] == [first['batch_id']]
    assert next(c0 for c0 in snapshot('stable', ledger)['worker_claims'] if c0['claim_id'] == c)['settlement_status'] == 'not_settled'
    second = new_batch('stable', ledger, [c], 'batch-disjoint')
    assert second['batch_id'] != first['batch_id']


def test_stable_batch_key_replay_only_for_same_claim_set():
    ledger = make_store('stable')
    initial = seed('stable', 'batch', ledger)
    a, b, c = initial['claim_ids']
    first = new_batch('stable', ledger, [a, b], 'one-key')
    replay = new_batch('stable', ledger, [b, a], 'one-key')
    assert replay['batch_id'] == first['batch_id']
    with pytest.raises(StableHubWorkerSessionError, match='settlement_batch_idempotency_conflict'):
        new_batch('stable', ledger, [a, c], 'one-key')
    with pytest.raises(StableHubWorkerSessionError, match='duplicate_claim_ids'):
        new_batch('stable', ledger, [c, c], 'new-key')
    assert len(snapshot('stable', ledger)['settlement_batches']) == 1


def test_stable_bridge_payout_one_owner_and_conflicting_keys_rejected():
    ledger = make_store('stable')
    initial = seed('stable', 'payout', ledger)
    batch_id = initial['batch_id']
    first = new_payout('stable', ledger, batch_id, 'payout-first')
    assert new_payout('stable', ledger, batch_id, 'payout-first')['payout_id'] == first['payout_id']
    with pytest.raises(StableHubWorkerSessionError, match='bridge_payout_batch_conflict'):
        new_payout('stable', ledger, batch_id, 'payout-second')
    assert len(snapshot('stable', ledger)['bridge_payouts']) == 1
    ledger.fail_bridge_payout(bridge_payout_id=first['payout_id'], reason='ambiguous external state')
    with pytest.raises(StableHubWorkerSessionError, match='bridge_payout_batch_conflict'):
        new_payout('stable', ledger, batch_id, 'payout-after-fail')
    assert len(snapshot('stable', ledger)['bridge_payouts']) == 1


def test_stable_bridge_key_cannot_point_to_different_batches():
    ledger = make_store('stable')
    initial = seed('stable', 'batch', ledger)
    a, b, _ = initial['claim_ids']
    first_batch = new_batch('stable', ledger, [a], 'batch-a')
    second_batch = new_batch('stable', ledger, [b], 'batch-b')
    settle('stable', ledger, first_batch['batch_id'], 'settle-a')
    settle('stable', ledger, second_batch['batch_id'], 'settle-b')
    first = new_payout('stable', ledger, first_batch['batch_id'], 'shared-payout-key')
    with pytest.raises(StableHubWorkerSessionError, match='bridge_payout_idempotency_conflict'):
        new_payout('stable', ledger, second_batch['batch_id'], 'shared-payout-key')
    assert len(snapshot('stable', ledger)['bridge_payouts']) == 1
    assert first['batch_id'] == first_batch['batch_id']


@pytest.mark.parametrize('experiment', ['batch', 'payout'])
def test_stable_32_contender_campaign_a_passes(experiment):
    ledger = make_store('stable')
    initial = seed('stable', experiment, ledger)
    actions = intentions(experiment, 32, 424242, initial)
    # Distinct directory instances share one durable store, as multiple Hub
    # handlers do within one process. The live campaign additionally uses FDB
    # and three independent OS processes.
    actors = [ledger] + [
        StableHubPayoutLedgerDirectory(
            topology=ledger.topology, hub_id=ledger.hub.hub_id, store=ledger.store,
        ) for _ in range(2)
    ]
    with ThreadPoolExecutor(max_workers=32) as pool:
        results = list(pool.map(
            lambda indexed: attempt('stable', experiment, actors[indexed[0] % 3], indexed[1]),
            enumerate(actions),
        ))
    verdict = audit(experiment, initial, snapshot('stable', ledger), results)
    assert verdict['ok'], verdict
    assert verdict['attempts'] == 32
