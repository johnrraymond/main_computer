"""Evidence-contract regressions for the ORG MSK release gate.

Run the real-process functional lane with python -m tools.slh_seh_lab.org_msk_passthrough.
It exits nonzero unless every required production contract is independently verified.
"""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from tools.slh_seh_lab import org_msk_passthrough as gate
from main_computer.stable_hub_msk import InMemoryStableMultiSessionKeyStore
from main_computer.hub_credit_indexer import wallet_account_id

WALLET = '0x' + '12' * 20
ACCOUNT = wallet_account_id(WALLET)
EXTERNAL = '0x' + '34' * 20
CHAIN = '42424242'


def _record():
    return {'status':'active','wallet_address':WALLET,'verified':{
        'wallet_address':WALLET,'recovered_address':WALLET,'account_id':ACCOUNT,'chain_id':CHAIN}}


@pytest.mark.parametrize('change', ['wrong-wallet','wrong-chain','revoked','wrong-account','old','multiple'])
def test_fresh_hub_msk_refuses_inadequate_proof(change):
    row = _record(); before = {}; rows = {'new':row}
    if change == 'wrong-wallet': row['verified']['recovered_address'] = EXTERNAL
    elif change == 'wrong-chain': row['verified']['chain_id'] = '42424240'
    elif change == 'revoked': row['status'] = 'revoked'
    elif change == 'wrong-account': row['verified']['account_id'] = EXTERNAL
    elif change == 'old': before = {'new':deepcopy(row)}
    elif change == 'multiple': rows['second'] = deepcopy(row)
    store = InMemoryStableMultiSessionKeyStore({'keys':rows})
    assert gate._fresh_verified_key(store,before,WALLET,CHAIN) is None


def test_fresh_hub_msk_matches_verified_wallet_and_chain():
    store = InMemoryStableMultiSessionKeyStore({'keys':{'new':_record()}})
    assert gate._fresh_verified_key(store,{},WALLET,CHAIN) == 'new'


@pytest.mark.parametrize('missing', sorted(gate.REQUIRED))
def test_each_required_witness_is_mandatory(missing):
    report=gate._new_report('test')
    report['checks']={key:True for key in gate.REQUIRED if key != missing}
    report['checks']['external_msk_worker_passthrough_control']=True
    assert gate._finish(report)['status']=='BLOCKED'
    assert report['missing']==[missing]


def test_complete_report_requires_all_boolean_witnesses():
    report=gate._new_report('test'); report['checks']={key:True for key in gate.REQUIRED}
    assert gate._finish(report)['status']=='PASS'
    report['checks']['credentials_not_returned']='PASS'
    assert gate._finish(report)['status']=='BLOCKED'


def _outbound_case():
    amount=str(10**18)
    state={'accepted_sessions':{'s':{'request_id':'r','session_id':'s','requester_account_id':ACCOUNT,
        'status':'succeeded','worker_result':{'result':{'response':{'content':'correct'}}}}},
        'payout_charges':{'c':{'session_id':'s','request_id':'r','account_id':ACCOUNT,'worker_account_id':EXTERNAL,'charged_credit_wei':amount}},
        'worker_earnings':{'e':{'session_id':'s','request_id':'r','worker_id':EXTERNAL,
            'worker_node_id':'external-connection','earned_credit_wei':amount}}}
    before={'spent_credit_wei':'0','available_credit_wei':str(2*10**18)}
    after={'spent_credit_wei':amount,'available_credit_wei':amount,'held_credit_wei':'0'}
    return state,before,after


def _check(state,before,after):
    return gate._outbound_evidence(SimpleNamespace(store=SimpleNamespace(load=lambda:state)),
        'r',ACCOUNT,EXTERNAL,'external-connection','correct',before,after)


def test_outbound_requires_real_job_result_and_exact_accounting():
    assert _check(*_outbound_case())


@pytest.mark.parametrize('defect',['wrong-buyer','wrong-worker','wrong-connection','duplicate-charge',
    'wrong-charge-account','wrong-result','wrong-debit','hold-not-released','wrong-earning','wrong-session'])
def test_outbound_refuses_false_positive(defect):
    state,before,after=_outbound_case()
    if defect=='wrong-buyer': state['accepted_sessions']['s']['requester_account_id']=EXTERNAL
    elif defect=='wrong-worker': state['worker_earnings']['e']['worker_id']=ACCOUNT
    elif defect=='wrong-connection': state['worker_earnings']['e']['worker_node_id']='other'
    elif defect=='duplicate-charge': state['payout_charges']['second']=deepcopy(state['payout_charges']['c'])
    elif defect=='wrong-charge-account': state['payout_charges']['c']['account_id']=EXTERNAL
    elif defect=='wrong-result': state['accepted_sessions']['s']['worker_result']['result']['response']['content']='wrong'
    elif defect=='wrong-debit': after['spent_credit_wei']='0'
    elif defect=='hold-not-released': after['held_credit_wei']='1'
    elif defect=='wrong-earning': state['worker_earnings']['e']['earned_credit_wei']='0'
    elif defect=='wrong-session': state['payout_charges']['c']['session_id']='other'
    assert not _check(state,before,after)


def test_secret_report_helpers():
    assert gate._no_secret({'status':'OK'},'private-token')
    assert not gate._no_secret({'nested':{'value':'private-token'}},'private-token')
    assert gate._http_observation(400,{'error':{'code':'INVALID_REQUEST','message':'private-token'}})=={'http':400,'error_code':'INVALID_REQUEST'}
