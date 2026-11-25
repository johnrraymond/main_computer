"""Adversarial coverage of the local admission boundary; live gate tests Hub I/O."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import threading
from types import SimpleNamespace

import pytest
from main_computer.slh.authority import SLHError
from main_computer.slh.marketplace_auto import save_authorization, update_settings
from main_computer.slh.public_requests import PublicRequests
from main_computer.slh.pinky_sweep import load_organization_signer, SIGNER_ENV
from main_computer.multisession_key_signing import private_key_to_address

ORG = {'role':'requester','device_id':'requester-one'}
OTHER = {'role':'requester','device_id':'requester-two'}
BINDING = {'hub_url':'http://127.0.0.1:8873','hub_id':'hub-one','cluster_id':'cluster',
           'chain_id':'42424242','account_id':'account-one','tls_sha256':None}
CREDIT = 10**18

class Reader:
    trust_mode = 'LAB_ONLY'
    def __init__(self):
        self.posts=[]; self.accepted={}; self.lose=False
    def request(self,method,path,body=None,*,bearer_token=''):
        if method=='POST':
            assert body['multisession_authorization']['multisession_key_id']=='private-msk'
            req=body['request_id']; self.posts.append(req)
            record=self.accepted.setdefault(req,{'request_id':req,'session_id':'sess_'+req,
                'status':'succeeded','worker_result':{'result':{'response':{'content':'answer'}}}})
            if self.lose:
                self.lose=False
                raise SLHError('HUB_ACCOUNT_UNAVAILABLE',503)
            return {'ok':True,'accepted':True,'request_id':req,'session_id':record['session_id'],
                    'cluster_id':'cluster','hub_id':'hub-one','execution_hub':{'hub_id':'hub-one','hub_url':BINDING['hub_url']},
                    'continuation_token':'private-continuation'}
        assert bearer_token=='private-continuation'
        record=next(r for r in self.accepted.values() if r['session_id'] in path)
        return {'ok':True,'cluster_id':'cluster','hub_id':'hub-one','accepted_session':record}

@pytest.fixture
def env(tmp_path, monkeypatch):
    controller=SimpleNamespace(_lock=threading.RLock(),wallet_service=None)
    update_settings(tmp_path,{'enabled':True,'hub_url':BINDING['hub_url']},0)
    save_authorization(tmp_path,{'kind':'multisession_key','multisession_key_id':'private-msk'})
    reader=Reader(); binding=deepcopy(BINDING)
    monkeypatch.setattr(PublicRequests,'_binding',lambda self,**kwargs:(binding,{'available_credit_wei':str(10*CREDIT)}))
    monkeypatch.setattr(PublicRequests,'_reader',staticmethod(lambda binding:reader))
    return PublicRequests(tmp_path,controller),reader,binding


def request(req='r1',price='1'):
    return {'request_id':req,'work':{'ring':'ring-2','max_price':{'unit':'compute_credit','amount':price},
                'capabilities':['chat.completions'],'model':'mock-model','input':{'kind':'chat','value':'test'}}}


def enable(g,budget=CREDIT,limit=CREDIT):
    return g.configure({'expected_revision':g.policy()['revision'],'enabled':True,
        'max_request_credit_wei':str(limit),'budget_credit_wei':str(budget)})


def test_disabled_default_and_exact_budget_replay(env):
    g,r,_=env
    with pytest.raises(SLHError,match='PUBLIC_REQUESTS_DISABLED'): g.submit(ORG,request())
    enable(g)
    first=g.submit(ORG,request())
    assert g.submit(ORG,request())['session_id']==first['session_id']
    assert g.get(ORG,'r1')['result']['response']['content']=='answer'
    assert 'private-' not in json.dumps([first,g.get(ORG,'r1'),g.policy()])
    with pytest.raises(SLHError,match='PUBLIC_REQUEST_BUDGET_LIMIT'): g.submit(ORG,request('r2'))
    assert r.posts==['r1']


def test_different_device_cannot_replay_or_read(env):
    g,r,_=env;enable(g);g.submit(ORG,request())
    for operation in (lambda:g.submit(OTHER,request()),lambda:g.get(OTHER,'r1')):
        with pytest.raises(SLHError,match='PUBLIC_REQUEST_NOT_FOUND'): operation()
    altered=request();altered['work']['input']['value']='different'
    with pytest.raises(SLHError,match='PUBLIC_REQUEST_CONFLICT'):g.submit(ORG,altered)
    assert r.posts==['r1']


@pytest.mark.parametrize('price',['0','-1','NaN','1e99999999','0.0000000000000000001','2',2])
def test_invalid_or_excess_price_never_submitted(env,price):
    g,r,_=env;enable(g)
    with pytest.raises(SLHError):g.submit(ORG,request(price=price))
    assert r.posts==[] and g.policy()['reserved_ceiling_credit_wei']=='0'


def test_forged_credentials_and_wrong_role_denied(env):
    g,r,_=env;enable(g)
    with pytest.raises(SLHError,match='INVALID_PUBLIC_REQUEST'):g.submit(ORG,{**request(),'multisession_authorization':{}})
    with pytest.raises(SLHError,match='REQUESTER_REQUIRED'):g.submit({'role':'worker','device_id':'w'},request())
    assert not r.posts


def test_ambiguous_admission_survives_restart_and_cannot_double_reserve(env):
    g,r,_=env;enable(g);r.lose=True
    with pytest.raises(SLHError,match='HUB_ACCOUNT_UNAVAILABLE'):g.submit(ORG,request())
    new=PublicRequests(g.state_dir,g.controller)
    assert new.get(ORG,'r1')['status']=='outcome_unknown'
    with pytest.raises(SLHError,match='PUBLIC_REQUEST_BUDGET_LIMIT'):new.submit(ORG,request('r2'))
    assert new.submit(ORG,request())['session_id']=='sess_r1'
    assert len(r.accepted)==1 and r.posts==['r1','r1']
    assert new.policy()['reserved_ceiling_credit_wei']==str(CREDIT)


def test_concurrent_requesters_share_one_budget(env):
    g,r,_=env;enable(g)
    def run(n):
        try:g.submit(ORG,request('r'+str(n)));return True
        except SLHError as exc:
            assert exc.code=='PUBLIC_REQUEST_BUDGET_LIMIT';return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(run,range(8)))==1
    assert len(r.posts)==1


def test_bound_account_cannot_change_between_replays(env):
    g,r,b=env;enable(g);g.submit(ORG,request());b['account_id']='different'
    with pytest.raises(SLHError,match='PUBLIC_REQUEST_CONFLICT'):g.submit(ORG,request())
    with pytest.raises(SLHError,match='PUBLIC_REQUEST_BINDING_CHANGED'):g.get(ORG,'r1')
    assert r.posts==['r1']


def test_config_revision_and_limit_are_validated(env):
    g,_,_=env;enable(g)
    with pytest.raises(SLHError,match='REVISION_CONFLICT'):g.configure({'expected_revision':0,'enabled':True,'max_request_credit_wei':'1','budget_credit_wei':'1'})
    with pytest.raises(SLHError,match='INVALID_PUBLIC_SPENDING_LIMIT'):enable(g,budget=1,limit=2)


def test_machine_signer_does_not_require_reserve(tmp_path,monkeypatch):
    key=tmp_path/'key'; key.write_text('11'*32);key.chmod(0o600)
    monkeypatch.setenv(SIGNER_ENV,str(key)); monkeypatch.delenv('MAIN_COMPUTER_RESERVE_WALLET_ADDRESS',raising=False)
    assert load_organization_signer()==(private_key_to_address('11'*32),'11'*32)


def test_resident_financial_readiness_is_required_before_paid_submission(env):
    g, r, _ = env
    enable(g)
    g.controller.financial_startup = True
    for state in ('INITIALIZING', 'BLOCKED'):
        g.controller.financial_readiness = {'state': state}
        with pytest.raises(SLHError, match='ORG_FINANCIAL_NOT_READY'):
            g.submit(ORG, request())
        assert not r.posts and g.policy()['reserved_ceiling_credit_wei'] == '0'
    g.controller.financial_readiness = {'state': 'READY'}
    assert g.submit(ORG, request())['status'] == 'accepted'
