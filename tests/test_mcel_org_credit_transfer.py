"""Disposable Hub ledger and MCEL ORG bridge acceptance; no live funds."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import time
import pytest
from main_computer.hub_credit_indexer import wallet_account_id
from main_computer.multisession_key_signing import private_key_to_address,sign_personal_message
from main_computer.org_credit_transfer import SCHEMA,canonical_transfer_intent
from main_computer.stable_hub_topology import load_stable_hub_topology
from main_computer.stable_hub_worker_sessions import InMemoryStableWorkerSessionStore,StableHubPayoutLedgerDirectory
from main_computer.mcel_wallet_application import compile_wallet_application,render_wallet_page
from main_computer.mcel_wallet_route import execute_wallet_operation
from main_computer.mcel_slh_worker_bridge import WorkerBridgeError
from main_computer.mcel_slh_network_client import NetworkConnectionError

ROOT=Path(__file__).resolve().parents[1]
KEY='11'*32
OTHER='22'*32
SOURCE=private_key_to_address(KEY)
RECIPIENT=private_key_to_address(OTHER)

@pytest.fixture
def ledger():
    topology=load_stable_hub_topology(ROOT/'deploy/hub-topology/dev-topology.json')
    result=StableHubPayoutLedgerDirectory(topology=topology,hub_id='dev-hub1',store=InMemoryStableWorkerSessionStore())
    result.fund_account(account_id=wallet_account_id(SOURCE),wallet_address=SOURCE,credits='3',replace=True)
    return result


def intent(ledger,**kw):
    return {'schema':SCHEMA,'transfer_id':'disposable-01','cluster_id':ledger.cluster_id,
            'chain_id':str(ledger.topology.network['chain_id']),'wallet_address':SOURCE,
            'recipient_wallet_address':RECIPIENT,'credit_wei':'1250000000000000001',
            'expires_at':int(time.time())+300,**kw}


def send(ledger,value,key=KEY):
    return ledger.transfer_org_credits(intent=value,signature=sign_personal_message(canonical_transfer_intent(value),key))


def available(ledger,wallet):
    a=ledger.get_account(wallet_account_id(wallet))
    return int(a['available_credit_wei']) if a else 0


def test_atomic_conservation_fractional_exact_and_idempotency(ledger):
    value=intent(ledger)
    receipt=send(ledger,value)
    assert receipt['status']=='confirmed'
    assert available(ledger,SOURCE)==1749999999999999999
    assert available(ledger,RECIPIENT)==1250000000000000001
    assert send(ledger,value)==receipt
    transactions=ledger.store.load()['payout_transactions']
    assert {v['transaction_type'] for k,v in transactions.items() if k.startswith(receipt['receipt_id'])}=={'org_credit_transfer_out','org_credit_transfer_in'}
    with pytest.raises(ValueError,match='idempotency_conflict'):
        send(ledger,{**value,'credit_wei':'1'})
    assert available(ledger,SOURCE)+available(ledger,RECIPIENT)==3*10**18


def test_wrong_signature_wrong_scope_and_expired_fail_without_mutation(ledger):
    value=intent(ledger)
    for change,key,reason in [({},OTHER,'signer_mismatch'),({'chain_id':'1'},KEY,'scope_mismatch'),({'cluster_id':'foreign'},KEY,'scope_mismatch'),({'expires_at':int(time.time())-1},KEY,'expired_or_future'),({'credit_wei':'3000000000000000001'},KEY,'insufficient_available')]:
        with pytest.raises(ValueError,match=reason):send(ledger,{**value,**change},key)
    assert available(ledger,SOURCE)==3*10**18 and available(ledger,RECIPIENT)==0


def test_held_credits_untouched_and_unspendable(ledger):
    data=ledger.store.load();a=data['payout_accounts'][wallet_account_id(SOURCE)];a['available_credit_wei']='1000000000000000000';a['held_credit_wei']='2000000000000000000';ledger.store.save(data)
    with pytest.raises(ValueError,match='insufficient_available'):send(ledger,intent(ledger))
    send(ledger,intent(ledger,credit_wei='1000000000000000000'))
    a=ledger.get_account(wallet_account_id(SOURCE));assert a['available_credit_wei']=='0' and a['held_credit_wei']=='2000000000000000000'


def test_concurrent_double_spend_prevented(ledger):
    def action(i):
        try:return send(ledger,intent(ledger,transfer_id=f'concurrent-{i}',credit_wei='2000000000000000000'))['status']
        except ValueError as e:return str(e)
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(action,[1,2]))
    assert results.count('confirmed')==1
    assert available(ledger,SOURCE)==10**18 and available(ledger,RECIPIENT)==2*10**18


def test_lost_response_retry_even_after_expiry(ledger,monkeypatch):
    value=intent(ledger);receipt=send(ledger,value)
    monkeypatch.setattr('main_computer.stable_hub_worker_sessions.time.time',lambda:value['expires_at']+1)
    assert send(ledger,value)==receipt
    assert available(ledger,RECIPIENT)==int(value['credit_wei'])


BASE={'target':'local','identity':'disposable-admin','address':'','port':'','pin':'','operation':'inspect','arguments':{}}

class Controller:
    def login(self,*,device_id):return {'role':'admin','access_token':'disposable-token'}

class Client:
    def __init__(self):self.calls=[]
    def request(self,*args):self.calls.append(args);return {'schema':'main-computer.slh-org-wallet.v1'}
    def logout(self,token):self.calls.append(('logout',token))


def test_local_bridge_exact_operation_and_logout():
    c=Client();r=execute_wallet_operation(BASE,local_controller=Controller(),local_client=c)
    assert r['schema']=='main-computer.slh-org-wallet.v1'
    assert c.calls==[('GET','/v1/wallets','disposable-token',None),('logout','disposable-token')]
    with pytest.raises(ValueError):execute_wallet_operation({**BASE,'operation':'arbitrary'},local_controller=Controller(),local_client=c)
    with pytest.raises(ValueError):execute_wallet_operation({**BASE,'address':'https://other.invalid'},local_controller=Controller(),local_client=c)


def test_network_pairing_does_not_forward_operations():
    class Network:
        def connect(self,*args,**kw):return {'pairingRequired':True,'endpoint':'10.0.0.2:9000','tlsSha256':'a'*64}
        def admin_request(self,*args):raise AssertionError('not paired')
    result=execute_wallet_operation({**BASE,'target':'network','address':'10.0.0.2','port':'9000'},network_client=Network())
    assert result['pairingRequired'] is True


def test_native_wallet_semantics_and_package_catalog():
    from main_computer.mcel_application_packages import build_application_package_catalog
    ir,composition=compile_wallet_application()
    assert ir['application']['appId']=='wallet' and not ir['layouts']
    intents={i['id']:i for i in ir['intents']}
    assert intents['intent:send-credits']['risk']=='consequential'
    assert intents['intent:send-credits']['confirmationRequired'] is True
    assert 'McelSemanticWalletPorts' in render_wallet_page()
    assert build_application_package_catalog(ROOT).ok
