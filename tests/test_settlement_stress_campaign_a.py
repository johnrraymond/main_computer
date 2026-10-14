"""P3 Campaign A harness regression and control-characterization tests.

These tests exercise the ownership contract for both ledger implementations.
They use memory/fake FDB. Live cross-process FoundationDB is CLI-only.
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from main_computer.stable_hub_topology import load_stable_hub_topology
from main_computer.stable_hub_worker_sessions import (
    InMemoryStableWorkerSessionStore, StableHubPayoutLedgerDirectory,
)
from test_exp_fdb_hold_lifecycle_p0 import DB, ledger
from tools.settlement_stress.run_lab import (
    WALLET, audit, attempt, configure_topology, intentions, seed, snapshot, new_batch,
    write_json, read_json, StressError,
)


def make_store(lane):
    if lane == 'exp':
        return ledger(DB())
    return StableHubPayoutLedgerDirectory(
        topology=load_stable_hub_topology(Path('deploy/hub-topology/dev-topology.json')),
        hub_id='dev-hub3', store=InMemoryStableWorkerSessionStore(),
    )


@pytest.mark.parametrize('experiment', ['claim', 'batch', 'payout', 'confirm'])
def test_exp_campaign_a_32_contenders_stays_financially_consistent(experiment):
    model = make_store('exp')
    initial = seed('exp', experiment, model)
    actions = intentions(experiment, 32, 424242, initial)
    with ThreadPoolExecutor(max_workers=32) as pool:
        outcomes = list(pool.map(lambda item: attempt('exp', experiment, model, item), actions))
    result = audit(experiment, initial, snapshot('exp', model), outcomes)
    assert result['ok'], result
    assert result['attempts'] == 32
    assert result['max_observed_concurrent_calls'] >= 1


@pytest.mark.parametrize('experiment', ['claim', 'confirm'])
def test_stable_control_currently_passes_claim_and_confirmation(experiment):
    model = make_store('stable')
    initial = seed('stable', experiment, model)
    actions = intentions(experiment, 32, 424242, initial)
    with ThreadPoolExecutor(max_workers=32) as pool:
        outcomes = list(pool.map(lambda item: attempt('stable', experiment, model, item), actions))
    result = audit(experiment, initial, snapshot('stable', model), outcomes)
    assert result['ok'], result


@pytest.mark.parametrize('experiment', ['batch', 'payout'])
def test_stable_control_enforces_batch_and_payout_ownership(experiment):
    model = make_store('stable')
    initial = seed('stable', experiment, model)
    actions = intentions(experiment, 12, 424242, initial)
    outcomes = [attempt('stable', experiment, model, action) for action in actions]
    result = audit(experiment, initial, snapshot('stable', model), outcomes)
    assert result['ok'], result


def test_auditor_rejects_duplicate_batch_and_earning_ownership():
    initial = {'earning_ids': ['e1'], 'claim_ids': ['c1'], 'batch_id': 'b1'}
    state = {'worker_claims': [
                {'claim_id': 'c1', 'earning_ids': ['e1']},
                {'claim_id': 'c2', 'earning_ids': ['e1']},
            ],
            'settlement_batches': [
                {'batch_id': 'b1', 'claim_ids': ['c1'], 'total_credit_wei_exact': '10',
                 'total_credit_wei_published': '8', 'dust_credit_wei': '2'},
                {'batch_id': 'b2', 'claim_ids': ['c1'], 'total_credit_wei_exact': '10',
                 'total_credit_wei_published': '8', 'dust_credit_wei': '2'},
            ], 'bridge_payouts': [], 'mock_chain_wallets': []}
    verdict = audit('batch', initial, state, [])
    assert not verdict['ok']
    assert 'duplicate earning ownership by distinct claims' in verdict['errors']
    assert 'duplicate claim membership in distinct batches' in verdict['errors']


def test_auditor_rejects_bad_amount_conservation_and_unexpected_error():
    state = {'worker_claims': [], 'settlement_batches': [
        {'batch_id': 'bad', 'claim_ids': ['c1'], 'total_credit_wei_exact': '10',
         'total_credit_wei_published': '9', 'dust_credit_wei': '0'},
    ], 'bridge_payouts': [], 'mock_chain_wallets': []}
    result = audit('batch', {'claim_ids': ['c1']}, state,
                   [{'ok': False, 'rejected': False, 'kind': 'RuntimeError', 'error': 'bad', 'latency_ms': 1}])
    assert not result['ok']
    assert result['unexpected_errors'] == 1
    assert any('conservation' in err for err in result['errors'])


def test_auditor_rejects_successful_response_with_wrong_intent():
    state = {'worker_claims': [{'claim_id': 'c1', 'earning_ids': ['e1']}],
             'settlement_batches': [], 'bridge_payouts': [], 'mock_chain_wallets': []}
    result = audit('claim', {'earning_ids': ['e1']}, state, [
        {'ok': True, 'record_id': 'c1', 'intent': {'ids': ['e2']},
         'record_earning_ids': ['e1'], 'latency_ms': 1},
    ])
    assert not result['ok']
    assert 'claim returned a different earning set than was requested' in result['errors']


def test_intentions_are_seeded_and_repeatable():
    initial = {'earning_ids': ['e1'], 'claim_ids': ['c1', 'c2', 'c3'],
               'batch_id': 'b1', 'payout_id': 'p1'}
    for exp in ('claim', 'batch', 'payout', 'confirm'):
        assert intentions(exp, 32, 1234, initial) == intentions(exp, 32, 1234, initial)
        assert len(intentions(exp, 32, 1234, initial)) == 32


def test_ledger_lab_keeps_persistent_report_atomic(tmp_path):
    p = tmp_path / 'report.json'
    write_json(p, {'ok': True, 'seed': 424242})
    assert read_json(p)['seed'] == 424242
    assert not (tmp_path / 'report.json.tmp').exists()


def test_stress_cli_fails_closed_if_fdb_missing(tmp_path):
    import subprocess
    result = subprocess.run([sys.executable, '-m', 'tools.settlement_stress.run_lab',
        '--cluster-file', str(tmp_path / 'missing.cluster'), '--rounds', '1'],
        cwd=Path.cwd(), text=True, capture_output=True)
    assert result.returncode != 0
    assert 'cluster file not found' in result.stderr


def test_campaign_keeps_running_after_failed_lane(tmp_path, monkeypatch):
    from tools.settlement_stress import run_lab
    cluster = tmp_path / 'docker.cluster'
    cluster.write_text('127.0.0.1:4550', encoding='utf-8')
    calls = []
    def fake_round(**kw):
        calls.append((kw['lane'], kw['exp']))
        ok = not (kw['lane'] == 'stable' and kw['exp'] == 'batch')
        return {'ok': ok, 'lane': kw['lane'], 'experiment': kw['exp'],
                'errors': [] if ok else ['duplicate batch claim']}
    monkeypatch.setattr(run_lab, 'run_round', fake_round)
    rc = run_lab.main(['--cluster-file', str(cluster), '--rounds', '1', '--clients', '32',
                       '--report-dir', str(tmp_path / 'reports')])
    assert rc == 1
    assert len(calls) == 8
    report = read_json(tmp_path / 'reports' / 'last-run.json')
    assert report['status'] == 'FAIL'
    assert len(report['results']) == 8


def test_exp_batch_idempotency_rejects_changed_financial_intent():
    """Reproduce the 32-client scenario: same key must not change claim ownership."""
    model = make_store('exp')
    fixture = seed('exp', 'batch', model)
    claims = fixture['claim_ids']
    first = new_batch('exp', model, claims[:2], 'racing-batch-key')
    assert first['claim_ids'] == claims[:2]
    # A reordered set is the same economic request; idempotent replay is legal.
    replay = new_batch('exp', model, list(reversed(claims[:2])), 'racing-batch-key')
    assert replay['batch_id'] == first['batch_id']
    assert replay['claim_ids'] == first['claim_ids']
    with pytest.raises(ValueError, match='different claim_ids'):
        new_batch('exp', model, claims[1:], 'racing-batch-key')
    with pytest.raises(ValueError, match='different payout precision'):
        model.create_worker_settlement_batch(
            worker_node_id='campaign-a-worker', claim_ids=claims[:2],
            idempotency_key='racing-batch-key', precision_places=5,
        )
    with pytest.raises(ValueError, match='different bridge account'):
        model.create_worker_settlement_batch(
            worker_node_id='campaign-a-worker', claim_ids=claims[:2],
            idempotency_key='racing-batch-key', bridge_account_id='different-dust-account',
        )
    state = snapshot('exp', model)
    assert len(state['settlement_batches']) == 1
    assert state['settlement_batches'][0]['claim_ids'] == claims[:2]


def test_exp_conflicting_batch_key_rejected_without_new_batch_under_parallel_calls():
    model = make_store('exp')
    fixture = seed('exp', 'batch', model)
    claims = fixture['claim_ids']
    actions = [
        {'key': 'collision', 'ids': claims[:2]},
        {'key': 'collision', 'ids': claims[1:]},
    ] * 16
    with ThreadPoolExecutor(max_workers=32) as pool:
        responses = list(pool.map(lambda a: attempt('exp', 'batch', model, a), actions))
    audit_result = audit('batch', fixture, snapshot('exp', model), responses)
    assert audit_result['ok'], audit_result
    assert audit_result['unique_batches'] == 1
    assert any(r.get('rejected') for r in responses)
