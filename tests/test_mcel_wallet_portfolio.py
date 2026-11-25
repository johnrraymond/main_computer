"""Disposable treasury integration; no real network, credentials or funds."""
from pathlib import Path
import time
import json
import subprocess
import shutil
import pytest
from main_computer.wallet_portfolio import canonical_read,verify_read,ledger_view,transfer_status
from main_computer.multisession_key_signing import private_key_to_address,sign_personal_message
from main_computer.org_credit_transfer import canonical_transfer_intent,SCHEMA
from main_computer.hub_credit_indexer import wallet_account_id
from main_computer.stable_hub_topology import load_stable_hub_topology
from main_computer.stable_hub_worker_sessions import InMemoryStableWorkerSessionStore,StableHubPayoutLedgerDirectory
from main_computer.mcel_applications_registry import read_registry
from main_computer.mcel_wallet_application import compile_wallet_application,render_wallet_page
from main_computer.mcel_wallet_route import execute_portfolio_operation
ROOT=Path(__file__).resolve().parents[1]
KEY='11'*32; SOURCE=private_key_to_address(KEY); OTHER=private_key_to_address('22'*32)

@pytest.fixture
def ledger():
    d=StableHubPayoutLedgerDirectory(topology=load_stable_hub_topology(ROOT/'deploy/hub-topology/dev-topology.json'),hub_id='dev-hub1',store=InMemoryStableWorkerSessionStore())
    d.fund_account(account_id=wallet_account_id(SOURCE),wallet_address=SOURCE,credits='5',replace=True)
    return d

def test_signed_read_separate_from_spending_and_scope(ledger):
    chain=str(ledger.topology.network['chain_id']);now=int(time.time())
    proof={'schema':'main-computer.wallet-read.v1','cluster_id':ledger.cluster_id,'chain_id':chain,'wallet_address':SOURCE,'expires_at':now+300}
    signature=sign_personal_message(canonical_read(proof),KEY)
    assert verify_read(proof,signature,ledger.cluster_id,chain,now)==SOURCE
    with pytest.raises(ValueError): verify_read(proof,signature,'other',chain,now)
    with pytest.raises(ValueError): verify_read(proof,signature,ledger.cluster_id,chain,now+301)
    with pytest.raises(ValueError): verify_read({**proof,'wallet_address':OTHER},signature,ledger.cluster_id,chain,now)
    with pytest.raises(ValueError): ledger.transfer_org_credits(intent=proof,signature=signature)

def test_wallet_scoped_history_bridge_and_exact_transfer(ledger):
    data=ledger.store.load();a=data['payout_accounts'][wallet_account_id(SOURCE)];a['bridge_completed_credit_wei']='3000000000000000000'
    data['payout_deposit_intents']={'x':{'account_id':wallet_account_id(SOURCE),'wallet_address':SOURCE,'deposit_id':'source-deposit','deposit_amount_units':'3000000000000000000','status':'finalized'},'y':{'account_id':wallet_account_id(OTHER),'wallet_address':OTHER,'deposit_id':'other-deposit','status':'prepared'}};ledger.store.save(data)
    intent={'schema':SCHEMA,'transfer_id':'p1','cluster_id':ledger.cluster_id,'chain_id':str(ledger.topology.network['chain_id']),'wallet_address':SOURCE,'recipient_wallet_address':OTHER,'credit_wei':'1000000000000000001','expires_at':int(time.time())+300}
    receipt=ledger.transfer_org_credits(intent=intent,signature=sign_personal_message(canonical_transfer_intent(intent),KEY))
    a=ledger_view(ledger,SOURCE,limit=1);b=ledger_view(ledger,OTHER)
    assert a['balance']['available_credit_wei']=='3999999999999999999' and a['balance']['bridge_completed_credit_wei']=='3000000000000000000'
    assert b['balance']['available_credit_wei']=='1000000000000000001'
    assert a['bridge_deposit_count']==1 and a['bridge_deposits'][0]['deposit_id']=='source-deposit'
    assert b['bridge_deposit_count']==1 and b['bridge_deposits'][0]['deposit_id']=='other-deposit'
    assert len(a['transactions'])==1 and a['next_cursor']
    older=ledger_view(ledger,SOURCE,limit=1,before=a['next_cursor']);assert older['transactions'][0]['transaction_id']!=a['transactions'][0]['transaction_id']
    assert all(t['transaction_id']!=receipt['receipt_id']+':out' for t in b['transactions'])
    assert 'metadata' not in a['transactions'][0]

def test_expired_recovery_is_atomically_sealed(ledger):
    intent={'schema':SCHEMA,'transfer_id':'lost','cluster_id':ledger.cluster_id,'chain_id':str(ledger.topology.network['chain_id']),'wallet_address':SOURCE,'recipient_wallet_address':OTHER,'credit_wei':'1','expires_at':int(time.time())-120}
    sig=sign_personal_message(canonical_transfer_intent(intent),KEY)
    assert transfer_status(ledger,intent,sig)['status']=='expired_not_found'
    with pytest.raises(ValueError,match='expired_not_committed'):ledger.transfer_org_credits(intent=intent,signature=sig)
    assert ledger_view(ledger,SOURCE)['balance']['available_credit_wei']=='5000000000000000000'

def test_canonical_registry_and_compilation():
    registry=read_registry(ROOT);w=next(x for x in registry['apps'] if x['id']=='wallet');assert w['mount']=='semantic' and w['route']=='/applications/wallet'
    ir,comp=compile_wallet_application();assert len(ir['intents'])>=14
    page=render_wallet_page();assert 'McelSemanticWalletPorts.create' in page and 'bridgeDetails' in page

def test_all_four_network_inventory_and_no_msk_exposure(monkeypatch,tmp_path):
    from main_computer.mcel_slh_network_client import NetworkSlhClient
    monkeypatch.setattr(NetworkSlhClient,'__init__',lambda self:setattr(self,'trust_path',tmp_path/'trust.json'))
    class Handler:
        def _worker_runtime_cached_multisession_wallets_for_hub(self,url):return [SOURCE]
    r=execute_portfolio_operation(Handler(),{'operation':'inventory'})
    assert {n['network'] for n in r['networks']}=={'mainnet','testnet','test','dev'}
    assert all(n['wallets']==[SOURCE] for n in r['networks'])
    assert next(n for n in r['networks'] if n['network']=='test')['hub']!=next(n for n in r['networks'] if n['network']=='testnet')['hub']
    assert 'multisession_key_id' not in json.dumps(r)

def test_mocked_browser_ports():
    if not shutil.which('node'):pytest.skip('Node unavailable')
    subprocess.run(['node',str(ROOT/'mcel_apps/wallet/tests/portfolio_ports.cjs')],cwd=ROOT,check=True,timeout=20)

@pytest.fixture
def local_org_binding(tmp_path):
    import sqlite3
    from main_computer.slh.organization_wallet import OrganizationWallet, _digest_key
    from main_computer.slh.marketplace_auto import update_settings, save_authorization
    root=tmp_path/'slh'
    wallet=OrganizationWallet(root)
    hub='https://test.example'
    key='disposable-private-msk'
    update_settings(root,{'hub_url':hub},0)
    save_authorization(root,{'kind':'multisession_key','multisession_key_id':key})
    with sqlite3.connect(wallet.path) as db:
        db.execute('INSERT INTO binding VALUES(1,?,?,?,?,?,?,?,?,?,?,?)',
                   (hub,'test-cluster','test-hub','42424241',SOURCE,wallet_account_id(SOURCE),_digest_key(key),None,'SYSTEM_TLS',1,123))
    return root,wallet.path,key


def test_local_wallet_inventory_without_identity_or_service(local_org_binding, monkeypatch):
    from main_computer.mcel_wallet_route import local_wallet_binding
    from main_computer.mcel_slh_network_client import NetworkSlhClient
    from main_computer.mcel_slh_first_run import SlhFirstRunController
    root,path,key=local_org_binding
    before=path.read_bytes()
    monkeypatch.setattr('main_computer.slh.storage.slh_state_dir',lambda:root)
    monkeypatch.setattr(NetworkSlhClient,'__init__',lambda self:setattr(self,'trust_path',root/'trust.json'))
    def forbidden(*args,**kwargs):pytest.fail('Discovery must not start or log into ORG-SLH')
    monkeypatch.setattr(SlhFirstRunController,'login',forbidden)
    class Handler:
        def _worker_runtime_cached_multisession_wallets_for_hub(self,url):return []
    result=execute_portfolio_operation(Handler(),{'operation':'inventory'})
    saved=result['localOrg']
    assert saved['state']=='BOUND' and saved['binding']['wallet_address']==SOURCE
    assert saved['binding']['chain_id']=='42424241' and saved['binding']['hub_url']=='https://test.example'
    assert key not in json.dumps(result) and 'msk_digest' not in json.dumps(result)
    assert path.read_bytes()==before
    missing=root/'missing'
    assert local_wallet_binding(missing)=={'state':'NOT_CONFIGURED','binding':None}
    assert not missing.exists()


def test_local_live_read_uses_existing_scoped_authorization(local_org_binding):
    from main_computer.mcel_wallet_route import observe_local_wallet
    from main_computer.slh.authority import SLHError
    root,path,key=local_org_binding
    original=path.read_bytes()
    calls=[]
    class Reader:
        trust_mode='SYSTEM_TLS'
        def __init__(self,url,pin):assert url=='https://test.example' and pin is None
        def request(self,method,path,body,*,bearer_token):
            calls.append((method,path,body,bearer_token))
            assert bearer_token==key and body['wallet_address']==SOURCE
            return {'ok':True,'wallet_address':SOURCE,'account_id':wallet_account_id(SOURCE),'cluster_id':'test-cluster','chain_id':'42424241','balance':{'available_credit_wei':'1','bridge_completed_credit_wei':'2'}}
    assert observe_local_wallet(OTHER,'',root,Reader)['state']=='LOCAL_ORG_BINDING_CHANGED'
    assert calls==[]
    result=observe_local_wallet(SOURCE,'',root,Reader)
    assert result['state']=='CURRENT' and result['portfolio']['balance']['bridge_completed_credit_wei']=='2'
    assert key not in json.dumps(result) and path.read_bytes()==original
    class Offline(Reader):
        def request(self,*a,**k):raise SLHError('HUB_ACCOUNT_UNAVAILABLE',503)
    result=observe_local_wallet(SOURCE,'',root,Offline)
    assert result['binding']['wallet_address']==SOURCE and result['state']=='HUB_READ_UNAVAILABLE_OR_NOT_AUTHORIZED' and 'portfolio' not in result
    assert path.read_bytes()==original
