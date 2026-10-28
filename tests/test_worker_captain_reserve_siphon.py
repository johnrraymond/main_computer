"""Offline: never use a live wallet, RPC, Hub, or real funds."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from main_computer.captain_cli import CaptainRuntime, CaptainWallet
from tools import worker_captain_reserve_siphon as siphon

U = 10**18
CAPTAIN = '0x' + 'a'*40
BETA = '0x' + 'b'*40


def balance(available: int, completed: int = 3*U):
    return {'ok': True, 'wallet_address': CAPTAIN, 'account': {
        'wallet_address': CAPTAIN, 'owner_address': CAPTAIN,
        'available_credit_wei': str(available),
        'bridge_completed_credit_wei': str(completed),
    }}


def test_small_deficit_siphons_only_shortfall_plus_small_buffer():
    result = siphon.plan_siphon(balance(464*U//1000), wallet=CAPTAIN, required_wei=512*U//1000)
    assert result == {'needed': True, 'top_up_wei': 73*U//1000, 'payout_wei': 78*U//1000}


def test_sufficient_funds_do_not_trigger_reserve_payout():
    assert not siphon.plan_siphon(balance(512*U//1000), wallet=CAPTAIN,
                                  required_wei=512*U//1000)['needed']


def test_large_deficit_refuses_instead_of_widening_budget():
    with pytest.raises(siphon.SiphonRefused, match='deficit_exceeds_small_siphon_limit'):
        siphon.plan_siphon(balance(0), wallet=CAPTAIN, required_wei=512*U//1000)


def test_ordinary_bridge_capacity_is_not_bypassed():
    with pytest.raises(siphon.SiphonRefused, match='ordinary_bridge_capacity_remains'):
        siphon.plan_siphon(balance(464*U//1000, U), wallet=CAPTAIN,
                           required_wei=512*U//1000)


def test_invalid_account_identity_and_prices_refuse():
    with pytest.raises(siphon.SiphonRefused, match='invalid_hub_credit_evidence'):
        siphon.plan_siphon(balance(0), wallet=BETA, required_wei=512*U//1000)
    with pytest.raises(siphon.SiphonRefused, match='invalid_worker_price'):
        siphon.plan_siphon(balance(0), wallet=CAPTAIN, required_wei=0)


def test_daily_journal_counts_only_initial_authorizations_once(tmp_path):
    folder = tmp_path/'journal'
    siphon._write_event(folder, {'status': 'initiated', 'top_up_wei': str(U//10), 'operation': 'one'})
    siphon._write_event(folder, {'status': 'completed', 'operation': 'one'})
    siphon._write_event(folder, {'status': 'initiated', 'top_up_wei': str(U//20), 'operation': 'two'})
    assert siphon._charged_today(folder) == 15*U//100


def test_complete_governed_payout_and_bridge_completion_with_idempotency_lock(monkeypatch, tmp_path):
    captain_wallet = CaptainWallet('captain', 'O0', 'Captain', CAPTAIN, 'captain-secret')
    beta_wallet = CaptainWallet('o2', 'O2', 'Second Officer', BETA, 'beta-secret')
    runtime = CaptainRuntime(
        config=SimpleNamespace(hub_url=siphon.TESTNET_HUB),
        deployment_path=tmp_path/'runtime/deployments/testnet/latest.json',
        deployment={}, wallet=captain_wallet, rpc_url='testnet://mock', chain_id=siphon.CHAIN,
        xlag_address='0x'+'1'*40, network='testnet',
        bridge_escrow_address='0x'+'2'*40, bridge_controller_address='0x'+'3'*40,
    )
    state = {'available': 464*U//1000, 'proposal_state': 1}
    calls = []
    monkeypatch.setattr(siphon, '_runtime', lambda root: runtime)
    monkeypatch.setattr(siphon, '_fetch_hub_credit_balance', lambda *_a, **_k: balance(state['available']))
    monkeypatch.setattr(siphon, 'resolve_captain_wallet', lambda *_a, **_k: beta_wallet)
    monkeypatch.setattr(siphon, '_read_office', lambda _rpc,_addr,office: CAPTAIN if office==0 else BETA)
    monkeypatch.setattr(siphon, '_read_uint', lambda _rpc,_addr,key,*args: {
        'max': U, 'delay': 0, 'next': 91, 'state': state['proposal_state']
    }[next(k for k,v in siphon.SELECTORS.items() if v==key)])
    monkeypatch.setattr(siphon, '_require_hub_bridge_completion_ready', lambda *_a, **_k: None)
    monkeypatch.setattr(siphon, '_require_no_other_captain_pending_deposits', lambda *_a, **_k: None)

    class FakeRpc:
        def __init__(self, *_a, **_k): pass
        def chain_id(self): return siphon.CHAIN
        def rpc(self, method, params=None):
            if method == 'eth_getBalance': return hex(10*U)
            if method == 'eth_blockNumber': return '0x64'
            raise AssertionError(method)
    monkeypatch.setattr(siphon, 'JsonRpcClient', FakeRpc)

    def send_contract(in_runtime, *, function_signature, function_args, **kwargs):
        calls.append((function_signature, in_runtime.wallet.address, function_args))
        if function_signature == 'executePayout(uint256)':
            state['proposal_state'] = 4
        return {'transaction_hash': '0x' + f'{len(calls):064x}', 'block_number': 100}
    monkeypatch.setattr(siphon, 'send_captain_contract_transaction', send_contract)
    monkeypatch.setattr(siphon, '_verify_proposal_receipt', lambda *_a, **_k: None)
    def send_deposit(*_a, **kw):
        calls.append(('deposit', CAPTAIN, kw['deposit_credit_wei']))
        return {'deposit_id':'0x'+'c'*64, 'transaction_hash':'0x'+'f'*64, 'receipt':{}}
    monkeypatch.setattr(siphon, 'send_captain_bridge_deposit', send_deposit)
    def complete(*_a, **kw):
        state['available'] += 73*U//1000
        return {'ok': True}
    monkeypatch.setattr(siphon, '_complete_captain_bridge_deposit', complete)

    result = siphon.run(root=tmp_path, required_wei=512*U//1000, execute=True)
    assert result['ok'] and result['status']=='funded'
    assert [x[0] for x in calls] == [
        'proposePayout(address,uint256,string,uint64)',
        'secondPayout(uint256)', 'executePayout(uint256)', 'deposit'
    ]
    assert calls[0][1]==CAPTAIN and calls[1][1]==BETA
    assert calls[-1][2]==73*U//1000
    assert not (tmp_path/siphon.STATE_DIR/'active.json').exists()
    assert len(list((tmp_path/siphon.STATE_DIR).glob('*.jsonl'))) == 1
    # The second invocation sees a funded Hub balance, so no second payout.
    again = siphon.run(root=tmp_path, required_wei=512*U//1000, execute=True)
    assert again['status']=='already_funded' and len(calls)==4


def test_unknown_transaction_outcome_retains_lock_to_block_duplicate(monkeypatch, tmp_path):
    # An existing lock cannot be ignored, even if the available balance is low.
    # No existing recovery record is deleted to make the test pass.
    lock = tmp_path/siphon.STATE_DIR/'active.json'
    lock.parent.mkdir(parents=True)
    lock.write_text('{"status":"proposing"}',encoding='utf8')
    captain_wallet = CaptainWallet('captain','O0','Captain',CAPTAIN,'x')
    runtime = CaptainRuntime(SimpleNamespace(hub_url=siphon.TESTNET_HUB), tmp_path/'latest.json',
                             {}, captain_wallet, 'mock-rpc', siphon.CHAIN,
                             '0x'+'1'*40, 'testnet', '0x'+'2'*40, '0x'+'3'*40)
    monkeypatch.setattr(siphon,'_runtime',lambda root: runtime)
    monkeypatch.setattr(siphon,'_fetch_hub_credit_balance',lambda *_a,**_k: balance(464*U//1000))
    monkeypatch.setattr(siphon,'resolve_captain_wallet',lambda *_a,**_k: CaptainWallet('o2','O2','Second',BETA,'x'))
    monkeypatch.setattr(siphon,'_read_office',lambda _rpc,_addr,idx: CAPTAIN if idx==0 else BETA)
    monkeypatch.setattr(siphon,'_read_uint',lambda _rpc,_addr,key,*_a: U)
    monkeypatch.setattr(siphon,'_require_hub_bridge_completion_ready',lambda *_a,**_k: None)
    monkeypatch.setattr(siphon,'_require_no_other_captain_pending_deposits',lambda *_a,**_k: None)
    class FakeRpc:
        def __init__(self,*_a,**_k):pass
        def chain_id(self):return siphon.CHAIN
        def rpc(self,method,params=None):return hex(10*U)
    monkeypatch.setattr(siphon,'JsonRpcClient',FakeRpc)
    with pytest.raises(siphon.SiphonRefused,match='reserve_siphon_pending_reconciliation'):
        siphon.run(root=tmp_path,required_wei=512*U//1000,execute=True)
    assert lock.exists()


def test_event_identity_prevents_seconding_another_proposal(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, 'eth_utils', SimpleNamespace(keccak=lambda text: b'\x11'*32))
    receipt = {'logs': [{'address': '0x'+'1'*40, 'topics': [
        '0x'+'11'*32, '0x'+f'{91:064x}',
        '0x'+'0'*24+CAPTAIN[2:], '0x'+'0'*24+CAPTAIN[2:]],
        'data': '0x'+f'{78*U//1000:064x}'+'0'*64}]}
    siphon._verify_proposal_receipt(receipt, reserve='0x'+'1'*40,
                                    proposal=91, captain=CAPTAIN, amount=78*U//1000)
    with pytest.raises(siphon.SiphonRefused,match='reserve_proposal_event_mismatch'):
        siphon._verify_proposal_receipt(receipt, reserve='0x'+'1'*40,
                                        proposal=92, captain=CAPTAIN, amount=78*U//1000)
