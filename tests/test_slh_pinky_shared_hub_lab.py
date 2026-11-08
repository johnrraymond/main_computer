"""Fail-closed routing tests for the real three-Hub Pinky economic lane.

These tests deliberately do not represent a successful FoundationDB run. The
Windows laboratory command provides that evidence independently.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tools.slh_seh_lab.run_lab import LabRefused, run_smoke, DEFAULT_TOPOLOGY, plan
from tools.slh_seh_lab.pinky_integrated import verify_pinky_shared_hub
from tools.slh_seh_lab.service_lifecycle import ServiceLabFailure


def test_plan_identifies_pinky_as_real_hub_only():
    assert 'REAL_SMOKE_FDB' in plan(DEFAULT_TOPOLOGY)['pinky_integrated']


def test_existing_cluster_requires_configured_reserve_before_touching_cluster(tmp_path):
    with patch('tools.slh_seh_lab.service_lifecycle.verify_services',
               return_value={'pass':True}), patch('tools.slh_seh_lab.decision_stress.verify_decisions',
               return_value={'pass':True}), patch('tools.stable_hub_lab.run_lab.check_cluster') as checked:
        with pytest.raises(LabRefused,match='--pinky-reserve-wallet'):
            run_smoke(DEFAULT_TOPOLOGY,use_existing=True,report_dir=tmp_path,
                      pinky_integrated=True)
        checked.assert_not_called()


def test_economic_lane_never_accepts_an_in_memory_hub():
    from main_computer.stable_hub_worker_sessions import (
        InMemoryStableWorkerSessionStore, StableHubPayoutLedgerDirectory)
    from main_computer.stable_hub_topology import load_stable_hub_topology
    topology=load_stable_hub_topology(DEFAULT_TOPOLOGY)
    ledger=StableHubPayoutLedgerDirectory(topology=topology,
        hub_id=topology.hubs[2].hub_id,store=InMemoryStableWorkerSessionStore())
    with pytest.raises(ServiceLabFailure,match='actual FoundationDB'):
        verify_pinky_shared_hub(root=Path('.'),state=Path('.'),local=None,
           local_port=0,local_token='',api_port=0,worker_port=0,
           hub_url=topology.hubs[2].hub_url,
           hub_identity={'hub_id':topology.hubs[2].hub_id,'cluster_id':topology.cluster_id},
           ledger=ledger,org_wallet_private='11'*32,
           org_wallet_address='0x'+'1'*40,worker=None,
           reserve_wallet='0x'+'2'*40,market_controller=None)


def test_in_memory_local_verification_does_not_claim_pinky_economic_pass():
    from tools.slh_seh_lab.run_lab import verify_local
    class MockHub:
        server_port=12000
        payout_ledger_directory=object()
        worker_session_directory=object()
        def shutdown(self):pass
        def server_close(self):pass
    class MockThread:
        def join(self,timeout):pass
    success={'pass':True,'status':'PASS','gates':{'ok':True}}
    from tools.slh_seh_lab.credit_recovery_acceptance import REQUIRED_GATES
    from main_computer.credit_units import CREDIT_WEI_PER_CREDIT
    recovery={**success, 'credit_recovery':{**success,
        'gates':{key:True for key in REQUIRED_GATES},
        'worker_count':2, 'local_requests_succeeded':5,'local_requests_refused':1,
        'source_earning_credit_wei':str(CREDIT_WEI_PER_CREDIT),
        'obligations_credit_wei':str(CREDIT_WEI_PER_CREDIT)}}
    with patch('tools.slh_seh_lab.service_lifecycle.verify_services',return_value=success), \
         patch('tools.slh_seh_lab.decision_stress.verify_decisions',return_value=success), \
         patch('tools.slh_seh_lab.run_lab._in_memory_hub',return_value=(MockHub(),MockThread())), \
         patch('tools.slh_seh_lab.run_lab._local_demo',return_value=success), \
         patch('tools.slh_seh_lab.run_lab._mock_coolify_gate',return_value=success), \
         patch('tools.slh_seh_lab.run_lab._verify_four_issue_hardening',return_value={'pass':True}), \
         patch('tools.slh_seh_lab.run_lab._credit_edge_regressions',return_value={'pass':True,'cases_passed':6}), \
         patch('tools.slh_seh_lab.real_paid_service.verify_real_process_paid',
               side_effect=lambda **kw: recovery if kw.get('credit_recovery_acceptance') else success):
        result=verify_local()
    assert result['status']=='PASS'
    assert 'pinky_integrated' not in result


def test_settled_paid_worker_earning_funds_wallet_once():
    """One real Hub earning, not a fabricated grant, becomes spendable on settle."""
    from main_computer.credit_units import CREDIT_WEI_PER_CREDIT
    from main_computer.stable_hub_worker_sessions import (
        InMemoryStableWorkerSessionStore, StableHubPayoutLedgerDirectory)
    from main_computer.stable_hub_topology import load_stable_hub_topology
    from main_computer.hub_credit_indexer import wallet_account_id
    topology=load_stable_hub_topology(DEFAULT_TOPOLOGY)
    store=InMemoryStableWorkerSessionStore()
    ledger=StableHubPayoutLedgerDirectory(topology=topology,
        hub_id=topology.hubs[2].hub_id,store=store)
    source='0x'+'11'*20
    worker='0x'+'22'*20
    ledger.fund_account(account_id=wallet_account_id(source),wallet_address=source,
                        credits='2',replace=True)
    hold=ledger.create_hold(account_id=wallet_account_id(source),wallet_address=source,
                            request_id='pinky-audit-request',session_id='sess_pinky_audit',
                            run_id='run_pinky_audit',worker_id='wallet-backed-worker',
                            selected_price={'amount':'1','unit':'compute_credit'},
                            requester_max_price={'amount':'2','unit':'compute_credit'},partition='ring-2')
    charge=ledger.spend_request_credit(
        account_id=wallet_account_id(source),wallet_address=source,
        request_id='pinky-audit-request',session_id='sess_pinky_audit',
        run_id='run_pinky_audit',worker_id='wallet-backed-worker',
        worker_account_id=worker,
        selected_price={'amount':'1','unit':'compute_credit'},
        requester_max_price={'amount':'2','unit':'compute_credit'},
        partition='ring-2',hold_id=hold['hold_id'],result={'ok':True})
    assert ledger.get_account(wallet_account_id(worker)) is None
    claim=ledger.record_worker_claim(worker_id=worker,
             earning_ids=[charge['worker_earning']['earning_id']],idempotency_key='lab-worker-claim')
    batch=ledger.create_worker_settlement_batch(worker_id=worker,
             claim_ids=[claim['claim_id']],idempotency_key='lab-worker-batch')
    ledger.settle_worker_settlement_batch(batch_id=batch['batch_id'],settlement_reference='lab-receipt')
    funded=ledger.get_account(wallet_account_id(worker))
    assert int(funded['available_credit_wei'])==CREDIT_WEI_PER_CREDIT
    assert int(funded['earned_credit_wei'])==CREDIT_WEI_PER_CREDIT
    assert int(ledger.get_account(wallet_account_id(source))['spent_credit_wei'])==CREDIT_WEI_PER_CREDIT
    ledger.settle_worker_settlement_batch(batch_id=batch['batch_id'],settlement_reference='lab-receipt')
    assert ledger.get_account(wallet_account_id(worker))['available_credit_wei']==str(CREDIT_WEI_PER_CREDIT)
    assert len([v for v in store.load()['payout_transactions'].values()
             if v['transaction_type']=='worker_settlement_wallet_credit'])==1


def test_wallet_settlement_cannot_also_use_legacy_bridge_payout():
    """The Hub credit-wallet receipt and a legacy bridge payout are not two earnings."""
    from main_computer.stable_hub_worker_sessions import (
        InMemoryStableWorkerSessionStore, StableHubPayoutLedgerDirectory,
        StableHubWorkerSessionError)
    from main_computer.stable_hub_topology import load_stable_hub_topology
    from main_computer.hub_credit_indexer import wallet_account_id
    topo=load_stable_hub_topology(DEFAULT_TOPOLOGY)
    ledger=StableHubPayoutLedgerDirectory(topology=topo,hub_id=topo.hubs[2].hub_id,
                store=InMemoryStableWorkerSessionStore())
    payer='0x'+'bb'*20
    worker='0x'+'cc'*20
    ledger.fund_account(account_id=payer,wallet_address=payer,credits='2',replace=True)
    charge=ledger.spend_request_credit(account_id=payer,wallet_address=payer,
                request_id='lab-no-double-bridge',session_id='sess_no_double_bridge',
                run_id='run_no_double_bridge',worker_id='physical-worker',
                worker_account_id=worker,
                selected_price={'amount':'1','unit':'compute_credit'},
                requester_max_price={'amount':'2','unit':'compute_credit'},
                partition='ring-2',result={'status':'success'})
    claim=ledger.record_worker_claim(worker_id=worker,
             earning_ids=[charge['worker_earning']['earning_id']])
    batch=ledger.create_worker_settlement_batch(worker_id=worker,
             claim_ids=[claim['claim_id']])
    ledger.settle_worker_settlement_batch(batch_id=batch['batch_id'])
    assert ledger.get_account(wallet_account_id(worker))['available_credits_display']=='1'
    with pytest.raises(StableHubWorkerSessionError,match='wallet_settlement_already_credited'):
        ledger.request_bridge_payout(worker_id=worker,batch_id=batch['batch_id'])


def test_integrated_http_timeout_reports_operation_without_request_content():
    from tools.slh_seh_lab.pinky_integrated import _lab_http
    with patch('tools.slh_seh_lab.pinky_integrated._http', side_effect=TimeoutError('read operation timed out')) as request:
        with pytest.raises(ServiceLabFailure, match=r'POST /v1/accounting/sweep.*TimeoutError'):
            _lab_http(1234, 'POST', '/v1/accounting/sweep',
                      {'private_prompt':'never appear'}, bearer='secret', tls=True)
        assert request.call_args.kwargs['timeout'] == 15


def test_sweep_row_closes_sqlite_even_when_read_raises(tmp_path):
    from tools.slh_seh_lab.pinky_integrated import _read_sweep_row
    connection = SimpleNamespace(close=lambda: None)
    from unittest.mock import Mock
    connection.close = Mock()
    connection.execute = Mock(side_effect=OSError('fake read failure'))
    with patch('tools.slh_seh_lab.pinky_integrated.sqlite3.connect', return_value=connection):
        with pytest.raises(OSError, match='fake read failure'):
            _read_sweep_row(tmp_path, 'psweep-test')
        connection.close.assert_called_once_with()


def test_sweep_row_closes_sqlite_on_success(tmp_path):
    import sqlite3
    from tools.slh_seh_lab.pinky_integrated import _read_sweep_row
    db_path = tmp_path / 'private_requests.sqlite3'
    with sqlite3.connect(db_path) as connection:
        connection.execute('CREATE TABLE pinky_sweeps (sweep_id TEXT PRIMARY KEY, credit_wei TEXT)')
        connection.execute('INSERT INTO pinky_sweeps VALUES (?,?)', ('psweep-test', '200'))
    row = _read_sweep_row(tmp_path, 'psweep-test')
    assert row == {'sweep_id':'psweep-test','credit_wei':'200'}
    with pytest.raises(ServiceLabFailure, match='no local batch record'):
        _read_sweep_row(tmp_path, 'missing')


def _fake_integrated_paid(topo, *, cycle=1, pinky_pass=True, corrupt_pinky_evidence=False):
    # A distinct verified economic cycle for orchestration tests. Real FDB
    # acceptance is only established by the Windows smoke lab, not this mock.
    wei = 10**18
    pinky_wei = 4*10**17
    org = f'org-{cycle}'
    wallet = f'worker-{cycle}'
    reserve_before = (cycle-1)*pinky_wei
    market = {'status':'PASS','pass':True, 'gates':{'paid':True, 'market_isolation_capability_verified': True},
              'market_isolation_capability': f'slh.lab.pinky.mock-{cycle}',
              'funding':'SYNTHETIC_LAB_ONLY',
              'charged_credit_wei':str(wei),'earned_credit_wei':str(wei),
              'request_id':f'paid-{cycle}','session_id':f'sess-{cycle}',
              'accounting':{
                  'buyer_account_id':f'buyer-{cycle}',
                  'buyer_available_before_wei':str(2*wei),
                  'buyer_available_after_wei':str(wei),
                  'buyer_spent_delta_wei':str(wei),
                  'buyer_held_after_wei':'0',
                  'worker_available_before_wei':'0',
                  'worker_available_after_batch_wei':'0',
                  'worker_earned_after_batch_wei':'0',
                  'worker_account_id':wallet,'charge_id':f'charge-{cycle}',
                  'earning_id':f'earning-{cycle}',}}
    pinky = {'pass': pinky_pass,'status':'PASS' if pinky_pass else 'FAIL',
             'gates':{'reserve_received':pinky_pass},
             'hub_id':topo.hubs[2].hub_id,'cluster_id':topo.cluster_id,
             'namespace':'main-computer-hub-smoke',
             'earned_credit_wei':str(wei if not corrupt_pinky_evidence else wei-1),
             'assessed_credit_wei':str(pinky_wei),'swept_credit_wei':str(pinky_wei),
             'worker_account_id':wallet,
             'local_lab_settlement': {
                 'status': 'LOCAL_LEDGER_SETTLED_NOT_BRIDGED',
                 'batch_id':f'batch-{cycle}',
                 'earning_id':f'earning-{cycle}',
                 'credit_wei':str(wei),
                 'worker_account_id':wallet,
                 'verified_available_wei':str(wei),
                 'verified_earned_wei':str(wei)},
             'limitations':{'source_funding':'LAB_REQUESTER_SYNTHETIC'},
             'accounting':{
                 'organization_id':org,'organization_account_id':wallet,
                 'wallet_after_earning_wei':str(wei),
                 'wallet_after_sweep_wei':str(wei-pinky_wei),
                 'reserve_account_id':'reserve',
                 'reserve_before_wei':str(reserve_before),
                 'reserve_after_wei':str(reserve_before+pinky_wei),
                 'private_request_ids':[f'private-{cycle}-1',f'private-{cycle}-2'],
                 'private_succeeded_count':2,
                 'obligation_count_before_sweep':2,
                 'obligation_paid_count_after_sweep':2,
                 'obligation_outstanding_before_wei':str(pinky_wei),
                 'obligation_outstanding_after_wei':'0',
                 'sweep_id':f'sweep-{cycle}','hub_receipt_id':f'receipt-{cycle}',}}
    return {'pass':True,'status':'PASS','gates':{'paid':True},
            'paid_marketplace':market,'pinky_integrated':pinky}



@pytest.mark.parametrize('corrupt', [
    lambda s: s.pop('verified_available_wei'),
    lambda s: s.pop('verified_earned_wei'),
    lambda s: s.pop('batch_id'),
    lambda s: s.update(status='CLAIM_BATCHED_NOT_SETTLED_OR_BRIDGED'),
    lambda s: s.update(credit_wei='1'),
    lambda s: s.update(earning_id='wrong-earning'),
    lambda s: s.update(worker_account_id='wrong-wallet'),
    lambda s: s.update(verified_available_wei='2000000000000000000'),
    lambda s: s.update(verified_earned_wei='2000000000000000000'),
])
def test_economic_reconciliation_rejects_missing_or_wrong_real_settlement_proof(corrupt):
    from tools.slh_seh_lab.economic_reconciliation import reconcile_cycle
    from main_computer.stable_hub_topology import load_stable_hub_topology
    paid = _fake_integrated_paid(load_stable_hub_topology(DEFAULT_TOPOLOGY))
    corrupt(paid['pinky_integrated']['local_lab_settlement'])
    result = reconcile_cycle(paid, cycle=1)
    assert result['pass'] is False
    assert result['gates']['earned_credit_deposited_to_org'] is False


def test_economic_reconciliation_uses_post_settlement_fdb_proof_not_prebatch_balance():
    from tools.slh_seh_lab.economic_reconciliation import reconcile_cycle
    from main_computer.stable_hub_topology import load_stable_hub_topology
    paid = _fake_integrated_paid(load_stable_hub_topology(DEFAULT_TOPOLOGY))
    assert 'worker_available_after_settlement_wei' not in paid['paid_marketplace']['accounting']
    result = reconcile_cycle(paid, cycle=1)
    assert result['pass'] is True
    assert result['gates']['earned_credit_deposited_to_org'] is True
    assert result['gates']['org_wallet_debited_exactly_once'] is True
    assert result['org_wallet_after_paid_wei'] == str(10**18)



def test_economic_reconciliation_rejects_precredited_pending_earnings():
    from tools.slh_seh_lab.economic_reconciliation import reconcile_cycle
    from main_computer.stable_hub_topology import load_stable_hub_topology
    paid = _fake_integrated_paid(load_stable_hub_topology(DEFAULT_TOPOLOGY))
    paid['paid_marketplace']['accounting']['worker_earned_after_batch_wei'] = str(10**18)
    result = reconcile_cycle(paid, cycle=1)
    assert result['pass'] is False
    assert result['gates']['earned_credit_deposited_to_org'] is False


def test_economic_reconciliation_rejects_wrong_sweep_wallet_balance():
    from tools.slh_seh_lab.economic_reconciliation import reconcile_cycle
    from main_computer.stable_hub_topology import load_stable_hub_topology
    paid = _fake_integrated_paid(load_stable_hub_topology(DEFAULT_TOPOLOGY))
    paid['pinky_integrated']['accounting']['wallet_after_earning_wei'] = '2000000000000000000'
    result = reconcile_cycle(paid, cycle=1)
    assert result['pass'] is False
    assert result['gates']['org_wallet_debited_exactly_once'] is False


def _exercise_smoke_report(tmp_path, *, integrated, pinky_pass=True,
                           corrupt_pinky_evidence=False, repeat=1,
                           alter_cycle=None):
    """Exercise orchestration without pretending mock Hubs prove FDB acceptance."""
    from types import SimpleNamespace, ModuleType
    import sys
    fake_paid_module = ModuleType('tools.slh_seh_lab.real_paid_service')
    from main_computer.stable_hub_topology import load_stable_hub_topology
    topo = load_stable_hub_topology(DEFAULT_TOPOLOGY)
    ready = {'pass': True, 'status': 'PASS', 'gates': {'test': True}}
    calls = []
    def paid_stub(**kwargs):
        cycle=len(calls)+1
        calls.append(kwargs)
        result=_fake_integrated_paid(topo, cycle=cycle,
                      pinky_pass=pinky_pass,corrupt_pinky_evidence=corrupt_pinky_evidence)
        if alter_cycle:
            alter_cycle(result,cycle)
        return result if integrated else ready
    fake_paid_module.verify_real_process_paid = paid_stub
    health = {'cluster_id': topo.cluster_id, 'checks': [
        {'ok': True, 'hub_id': hub.hub_id} for hub in topo.hubs]}
    with patch('tools.slh_seh_lab.service_lifecycle.verify_services', return_value=ready), \
         patch('tools.slh_seh_lab.decision_stress.verify_decisions', return_value=ready), \
         patch('tools.slh_seh_lab.run_lab._launch_cluster', return_value=([], [], health)), \
         patch('tools.stable_hub_lab.run_lab.require_durable_execution_cluster'), \
         patch('main_computer.stable_hub_worker_sessions.stable_worker_session_store_from_topology',return_value=object()), \
         patch('main_computer.stable_hub_worker_sessions.StableHubPayoutLedgerDirectory', return_value=SimpleNamespace()), \
         patch.dict(sys.modules, {'tools.slh_seh_lab.real_paid_service': fake_paid_module}), \
         patch('tools.slh_seh_lab.run_lab._verify_four_issue_hardening',return_value={'pass':True}), \
         patch('tools.slh_seh_lab.run_lab._mock_coolify_gate',return_value={'status':'PASS'}), \
         patch('tools.slh_seh_lab.run_lab._local_demo', return_value=ready) as legacy:
        report = run_smoke(DEFAULT_TOPOLOGY, use_existing=False,
                           report_dir=tmp_path, pinky_integrated=integrated,
                           pinky_repeat=repeat)
        return report, legacy.call_count


def test_pinky_integrated_skips_second_legacy_paid_work_and_checkpoints(tmp_path):
    import json
    report, legacy_calls = _exercise_smoke_report(tmp_path, integrated=True)
    assert legacy_calls == 0
    assert report['status'] == 'PASS'
    assert report['slh']['status'] == 'NOT_RUN'
    assert report['pinky_internal_reserve_sweep'] == 'CONFIRMED_IN_FDB'
    checkpoint = json.loads((tmp_path / 'pinky-integrated-checkpoint.json').read_text())
    assert checkpoint['schema'] == 'main-computer.slh-pinky-integrated-checkpoint.v1'
    assert checkpoint['pass'] is True
    assert checkpoint['pinky_integrated']['swept_credit_wei'] == str(4 * 10**17)
    assert checkpoint['legacy_second_paid_demo'] == 'NOT_RUN'
    assert checkpoint['hub_cluster']['ids'] == ['smoke-hub1', 'smoke-hub2', 'smoke-hub3']


def test_pinky_integrated_fails_closed_when_reserve_evidence_fails(tmp_path):
    import json
    report, legacy_calls = _exercise_smoke_report(tmp_path, integrated=True, pinky_pass=False)
    assert legacy_calls == 0
    assert report['status'] == 'FAIL'
    assert report['pinky_internal_reserve_sweep'] == 'NOT_VERIFIED'
    checkpoint = json.loads((tmp_path / 'pinky-integrated-checkpoint.json').read_text())
    assert checkpoint['pass'] is False
    assert checkpoint['pinky_integrated']['pass'] is False


def test_ordinary_smoke_still_executes_second_legacy_paid_demo(tmp_path):
    report, legacy_calls = _exercise_smoke_report(tmp_path, integrated=False)
    assert legacy_calls == 1
    assert report['status'] == 'PASS'
    assert report['slh']['pass'] is True
    assert 'pinky_integrated' not in report
    assert not (tmp_path / 'pinky-integrated-checkpoint.json').exists()


def test_pinky_integrated_refuses_forged_success_without_exact_earnings(tmp_path):
    report, legacy_calls = _exercise_smoke_report(
        tmp_path, integrated=True, corrupt_pinky_evidence=True)
    assert legacy_calls == 0
    assert report['status'] == 'FAIL'
    assert report['pinky_internal_reserve_sweep'] == 'NOT_VERIFIED'


def test_pinky_repeated_three_cycles_have_distinct_wallets_and_cumulative_reserve(tmp_path):
    import json
    report, legacy = _exercise_smoke_report(tmp_path, integrated=True, repeat=3)
    assert legacy == 0
    assert report['status'] == 'PASS'
    result = report['pinky_repeatability']
    assert result['status'] == 'PASS'
    assert result['cycles_completed'] == 3
    assert all(result['gates'].values())
    assert result['total_buyer_debit_wei'] == str(3*10**18)
    assert result['total_earned_credit_wei'] == str(3*10**18)
    assert result['total_reserve_swept_wei'] == str(12*10**17)
    assert len({c['organization_account_id'] for c in result['cycles']}) == 3
    assert len({c['buyer_account_id'] for c in result['cycles']}) == 3
    assert len({c['receipt_id'] for c in result['cycles']}) == 3
    for n in (1,2,3):
        single=json.loads((tmp_path/f'pinky-economic-cycle-{n:02d}.json').read_text())
        assert single['cycle']==n and single['status']=='PASS'
    receipt=json.loads((tmp_path/'pinky-economic-reconciliation.json').read_text())
    assert receipt['status']=='PASS'
    checkpoint=json.loads((tmp_path/'pinky-integrated-checkpoint.json').read_text())
    assert checkpoint['repeatability']['cycles_completed']==3
    assert checkpoint['status']=='PASS'


def test_pinky_repeat_fails_closed_on_duplicate_org_wallet(tmp_path):
    def duplicate(result,n):
        if n==2:
            result['paid_marketplace']['accounting']['worker_account_id']='worker-1'
            result['pinky_integrated']['accounting']['organization_account_id']='worker-1'
            result['pinky_integrated']['worker_account_id']='worker-1'
    report,legacy=_exercise_smoke_report(tmp_path,integrated=True,repeat=3,alter_cycle=duplicate)
    assert report['status']=='FAIL' and legacy==0
    assert report['pinky_repeatability']['cycles_completed']==2
    assert report['pinky_repeatability']['gates']['unique_org_wallets'] is False
    assert not (tmp_path/'pinky-economic-cycle-03.json').exists()


def test_pinky_repeat_fails_closed_on_corrupt_reserve_delta(tmp_path):
    def corrupt(result,n):
        if n==2:
            result['pinky_integrated']['accounting']['reserve_after_wei']=str(10**18)
    report,_=_exercise_smoke_report(tmp_path,integrated=True,repeat=3,alter_cycle=corrupt)
    assert report['status']=='FAIL'
    assert report['pinky_repeatability']['cycles_completed']==2
    assert report['pinky_repeatability']['cycles'][-1]['gates']['reserve_credited_exactly_once'] is False


def test_pinky_repeat_rejects_invalid_counts(tmp_path):
    from tools.slh_seh_lab.run_lab import LabRefused
    with pytest.raises(LabRefused,match='1..10'):
        run_smoke(DEFAULT_TOPOLOGY,use_existing=False,report_dir=tmp_path,
                  pinky_integrated=True,pinky_repeat=11)


def test_isolated_marketplace_sale_rejects_stale_cheaper_workers():
    """Repetition must select its own live Worker, even when old offers linger."""
    from main_computer.stable_hub_worker_sessions import (
        InMemoryStableWorkerSessionStore, StableHubWorkerMarketDirectory)
    from main_computer.stable_hub_topology import load_stable_hub_topology
    topology = load_stable_hub_topology(DEFAULT_TOPOLOGY)
    market = StableHubWorkerMarketDirectory(
        topology=topology, hub_id=topology.hubs[2].hub_id,
        store=InMemoryStableWorkerSessionStore())
    def offer(worker_id, price, capabilities):
        market.record_worker_live(
            worker_id=worker_id,
            owner={'worker_id': worker_id, 'status': 'live',
                   'owner_hub_id': topology.hubs[2].hub_id,
                   'owner_hub_url': topology.hubs[2].hub_url,
                   'connection_id': 'conn-' + worker_id, 'lease_epoch': 1},
            market_profile={'rings': ['ring-2'], 'capabilities': capabilities,
                            'models': ['mock-model'],
                            'price': {'amount': price, 'unit': 'compute_credit'}},
            worker_msk_id='msk-' + worker_id,
            worker_wallet_address='0x' + '12'*20, worker_account_id='acct-' + worker_id)
    offer('old-lab-worker', '0', ['chat.completions', 'slh.lab.pinky.old'])
    offer('current-lab-worker', '1', ['chat.completions', 'slh.lab.pinky.current'])
    assert market.select_worker_for_work({
        'ring': 'ring-2', 'capabilities': ['chat.completions'],
        'max_price': {'amount': '2', 'unit': 'compute_credit'},
        'model': 'mock-model',
    })['worker_id'] == 'old-lab-worker'
    assert market.select_worker_for_work({
        'ring': 'ring-2', 'capabilities': ['chat.completions', 'slh.lab.pinky.current'],
        'max_price': {'amount': '2', 'unit': 'compute_credit'},
        'model': 'mock-model',
    })['worker_id'] == 'current-lab-worker'


def test_reconciliation_fails_if_market_capability_not_verified():
    from tools.slh_seh_lab.economic_reconciliation import reconcile_cycle
    from main_computer.stable_hub_topology import load_stable_hub_topology
    paid = _fake_integrated_paid(load_stable_hub_topology(DEFAULT_TOPOLOGY), cycle=1)
    paid['paid_marketplace']['gates']['market_isolation_capability_verified'] = False
    result = reconcile_cycle(paid, cycle=1)
    assert result['pass'] is False
    assert result['gates']['cycle_market_worker_isolated'] is False


def test_distinct_cycle_capabilities_are_required():
    from tools.slh_seh_lab.economic_reconciliation import summarize_cycles, reconcile_cycle
    from main_computer.stable_hub_topology import load_stable_hub_topology
    topology = load_stable_hub_topology(DEFAULT_TOPOLOGY)
    first = reconcile_cycle(_fake_integrated_paid(topology, cycle=1), cycle=1)
    second = reconcile_cycle(_fake_integrated_paid(topology, cycle=2), cycle=2)
    second['market_isolation_capability'] = first['market_isolation_capability']
    result = summarize_cycles([first, second], requested=2)
    assert result['gates']['unique_market_isolation_capabilities'] is False
    assert result['pass'] is False


def test_terminal_stage_ignores_recovered_tls_pin_failure(tmp_path):
    from tools.slh_seh_lab.lab_stage_diagnostics import record, enable, disable, summarize
    path = tmp_path / 'stages.jsonl'
    token = enable(path)
    try:
        record('preflight.worker_websocket.tls_pin', 'error', error_type='ServiceLabFailure')
        record('pinky.real_process_and_economic_cycle', 'begin', cycle=2)
        record('pinky.public_paid_cycle', 'begin')
        record('pinky.public_paid_cycle', 'error', error_type='RuntimeError')
        record('pinky.real_process_and_economic_cycle', 'error', cycle=2)
        record('lab', 'error')
    finally:
        disable(token)
    summary = summarize(path)
    assert summary['last_failure']['stage'] == 'preflight.worker_websocket.tls_pin'
    assert summary['fatal_failure']['stage'] == 'pinky.public_paid_cycle'
