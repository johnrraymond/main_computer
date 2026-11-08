"""Pure-ledger unit fixtures; real three-Hub/FDB acceptance runs on Windows."""
from copy import deepcopy
from pathlib import Path

import pytest

from main_computer.credit_units import CREDIT_WEI_PER_CREDIT
from main_computer.hub_credit_indexer import wallet_account_id
from main_computer.stable_hub_topology import load_stable_hub_topology
from main_computer.stable_hub_worker_sessions import (
    InMemoryStableWorkerSessionStore, StableHubPayoutLedgerDirectory,
)
from tools.slh_seh_lab.pinky_integrated import _settle_verified_smoke_earning
from tools.slh_seh_lab.service_lifecycle import ServiceLabFailure


def _paid_fixture():
    topo = load_stable_hub_topology(Path('deploy/hub-topology/smoke-topology.json'))
    ledger = StableHubPayoutLedgerDirectory(
        topology=topo, hub_id=topo.hubs[2].hub_id,
        store=InMemoryStableWorkerSessionStore())
    org = '0x' + '22' * 20
    buyer = '0x' + '11' * 20
    buyer_account, org_account = wallet_account_id(buyer), wallet_account_id(org)
    request_id, session_id = 'pinky-smoke-test-request', 'pinky-smoke-test-session'
    ledger.fund_account(account_id=buyer_account, wallet_address=buyer,
                        credits='2', replace=True)
    hold = ledger.create_hold(
        account_id=buyer_account, wallet_address=buyer,
        request_id=request_id, session_id=session_id,
        run_id='pinky-smoke-test-run', worker_id='lab-worker',
        selected_price={'amount': '1', 'unit': 'compute_credit'},
        requester_max_price={'amount': '2', 'unit': 'compute_credit'},
        partition='ring-2')
    sold = ledger.spend_request_credit(
        account_id=buyer_account, wallet_address=buyer,
        request_id=request_id, session_id=session_id,
        run_id='pinky-smoke-test-run', worker_id='lab-worker',
        worker_account_id=org_account,
        selected_price={'amount': '1', 'unit': 'compute_credit'},
        requester_max_price={'amount': '2', 'unit': 'compute_credit'},
        partition='ring-2', hold_id=hold['hold_id'], result={'ok': True})
    earning = sold['worker_earning']
    claim = ledger.record_worker_claim(
        worker_id=org_account, earning_ids=[earning['earning_id']],
        idempotency_key='smoke-claim-'+request_id)
    ledger.create_worker_settlement_batch(
        worker_id=org_account, claim_ids=[claim['claim_id']],
        idempotency_key='smoke-batch-'+request_id)
    paid = {
        'pass': True, 'gates': {'all_paid_work_checks': True},
        'terminal_status': 'succeeded',
        'settlement': 'CLAIM_BATCHED_NOT_SETTLED_OR_BRIDGED',
        'worker_account_id': org_account,
        'accounting': {'worker_account_id': org_account,
                       'earning_id': earning['earning_id']},
        'request_id': request_id, 'session_id': session_id,
        'earned_credit_wei': str(CREDIT_WEI_PER_CREDIT),
    }
    return ledger, org, paid


def test_helper_requires_real_smoke_fdb_authority():
    ledger, org, paid = _paid_fixture()
    with pytest.raises(ServiceLabFailure, match='real smoke/FDB authority'):
        _settle_verified_smoke_earning(ledger=ledger, paid=paid,
                                      org_wallet_address=org)
    assert ledger.get_account(wallet_account_id(org)) is None


def test_internal_smoke_settlement_verifies_lineage_and_idempotency(monkeypatch):
    # Substitute the store TYPE solely to exercise the detailed accounting
    # algorithm in memory; this does not certify FoundationDB durability.
    import main_computer.stable_hub_worker_sessions as sessions
    monkeypatch.setattr(sessions, 'FoundationDbStableWorkerSessionStore',
                        InMemoryStableWorkerSessionStore)
    ledger, org, paid = _paid_fixture()
    proof = _settle_verified_smoke_earning(
        ledger=ledger, paid=paid, org_wallet_address=org)
    assert proof['status'] == 'LOCAL_LEDGER_SETTLED_NOT_BRIDGED'
    assert proof['verified_available_wei'] == str(CREDIT_WEI_PER_CREDIT)
    assert int(ledger.get_account(wallet_account_id(org))['available_credit_wei']) == CREDIT_WEI_PER_CREDIT
    batches = ledger.store.load()['payout_settlements']
    assert list(batches.values())[0]['status'] == 'settled'
    assert len(batches) == 1


@pytest.mark.parametrize('tamper', ['wallet', 'gates', 'amount', 'earning'])
def test_internal_smoke_settlement_rejects_unproven_economic_lineage(monkeypatch, tamper):
    import main_computer.stable_hub_worker_sessions as sessions
    monkeypatch.setattr(sessions, 'FoundationDbStableWorkerSessionStore',
                        InMemoryStableWorkerSessionStore)
    ledger, org, paid = _paid_fixture()
    paid = deepcopy(paid)
    if tamper == 'wallet':
        paid['worker_account_id'] = '0x'+'55'*20
    elif tamper == 'gates':
        paid['gates']['all_paid_work_checks'] = False
    elif tamper == 'amount':
        paid['earned_credit_wei'] = '9'
    else:
        paid['accounting']['earning_id'] = 'fabricated-earning'
    with pytest.raises(ServiceLabFailure):
        _settle_verified_smoke_earning(
            ledger=ledger, paid=paid, org_wallet_address=org)
    assert ledger.get_account(wallet_account_id(org)) is None
