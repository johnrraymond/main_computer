"""Patch 1 model/operation routing integrity: both Hub selectors and worker result."""
from pathlib import Path
import pytest

from main_computer.model_execution_contract import (
    ModelContractError, advertised_models, advertised_operations,
    request_contract, market_supports, verify_execution_result,
)
from main_computer.stable_hub_worker_sessions import (
    InMemoryStableWorkerSessionStore, StableHubWorkerMarketDirectory,
    StableHubWorkerSessionError, normalize_worker_market_profile,
    normalize_request_market_constraints,
)
from main_computer.stable_hub_topology import load_stable_hub_topology


def _work(model):
    return {'model':model,'ring':'ring-2','capabilities':['chat.completions'],
            'input':{'kind':'chat','prompt':'compute'}}


def _market(model, price):
    return {'rings':['ring-2'],'capabilities':['chat.completions'],
            'models':[model], 'price':{'amount':price,'unit':'compute_credit'},
            'max_concurrency':1}


def test_no_implicit_model_or_operation_and_no_alias_matching():
    with pytest.raises(ModelContractError): request_contract({'input':{'kind':'chat'}})
    with pytest.raises(ModelContractError): request_contract({'model':'foo','input':{'kind':'echo'}})
    with pytest.raises(ModelContractError): advertised_models({'model':'foo'})
    with pytest.raises(ModelContractError): advertised_operations({'capabilities':[]})
    with pytest.raises(ModelContractError): request_contract({'model':'foo','operation':'chat.completions',
                                                              'capabilities':['shell']})
    assert not market_supports(_market('Foo:1','0.1'),_work('foo:1'))
    assert market_supports(_market('Foo:1','0.1'),_work('Foo:1'))


def test_explicit_model_operation_and_result_must_agree():
    work=_work('model-beta')
    assert request_contract(work)==('model-beta','chat.completions',('chat.completions',))
    assert verify_execution_result(work, {'status':'success','response':{'model':'model-beta'}})=='model-beta'
    for result in ({'status':'success','response':{'model':'model-alpha'}},
                   {'status':'success','response':{'content':'no identity'}},
                   {'status':'failed','response':{'model':'model-beta'}},
                   {'status':'success','response':{'model':'model-beta'},
                    'solution':{'model':{'family':'model-alpha'}}}):
        with pytest.raises(ModelContractError): verify_execution_result(work,result)


def test_worker_advertisements_require_real_model_and_operation():
    for profile in ({'capabilities':['chat.completions']},
                    {'models':['a']},
                    {'models':['a'],'capabilities':['chat.completions'],'model':'b'}):
        with pytest.raises(StableHubWorkerSessionError):normalize_worker_market_profile(profile)


def test_shared_hub_market_selects_exact_model_instead_of_cheapest_other_model():
    topology=load_stable_hub_topology(Path('deploy/hub-topology/dev-topology.json'))
    store=InMemoryStableWorkerSessionStore()
    market=StableHubWorkerMarketDirectory(topology=topology,hub_id='dev-hub3',store=store)
    ownerhub=topology.hub_by_id('dev-hub3')
    for did,model,price in [('worker-a','model-alpha','0.1'),('worker-b','model-beta','0.2')]:
        owner={'worker_id':did,'status':'live','owner_hub_id':'dev-hub3',
               'owner_hub_url':ownerhub.hub_url,'connection_id':'conn_'+did,
               'lease_epoch':1}
        row=market.record_worker_live(worker_id=did,owner=owner,market_profile=_market(model,price),
                                      worker_msk_id='key',worker_wallet_address='0x'+'1'*40,
                                      worker_account_id='acct')
        assert row['models']==[model]
    selected=market.select_worker_for_work(_work('model-beta'))
    assert selected['worker_id']=='worker-b'
    assert selected['selection']['model']=='model-beta'
    assert market.select_worker_for_work(_work('missing')) is None
    assert market.select_worker_for_work({'model':'model-beta','operation':'shell','ring':'ring-2'}) is None
    # Legacy durable records with no model list must never be selectable.
    store_data=store.load()
    store_data['market_workers']['worker-b'].pop('models')
    store.save(store_data)
    assert market.select_worker_for_work(_work('model-beta')) is None


def test_exp_fdb_live_websocket_selector_uses_same_contract():
    from main_computer.exp_fdb_hub import ExperimentalFoundationDbHubServerHandler as Handler
    row={'status':'live', **_market('model-alpha','0.1'),'active_sessions':0}
    assert Handler._exp_live_market_record_matches_work(None,row,_work('model-alpha'))
    assert not Handler._exp_live_market_record_matches_work(None,row,_work('model-beta'))
    assert not Handler._exp_live_market_record_matches_work(None,row,
        {'model':'model-alpha','operation':'shell','ring':'ring-2'})


def test_hub_contract_rejects_missing_explicit_model_before_selection():
    with pytest.raises(StableHubWorkerSessionError):
        normalize_request_market_constraints({'ring':'ring-2','capabilities':['chat.completions']})
