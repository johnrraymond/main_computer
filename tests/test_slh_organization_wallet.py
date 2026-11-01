"""P1 wallet binding is authoritative observation only, never credit minting."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

import pytest

from main_computer.credit_units import CREDIT_WEI_PER_CREDIT
from main_computer.hub_credit_indexer import wallet_account_id
from main_computer.slh.authority import SLHError
from main_computer.slh.marketplace_auto import save_authorization, update_settings
from main_computer.slh.organization_wallet import HubCreditReader, OrganizationWallet

WALLET = '0x' + 'a'*40
ACCOUNT = wallet_account_id(WALLET)


class FakeHub:
    def __init__(self, url, pin, state):
        self.trust_mode = 'LAB_ONLY'
        self.state = state
        self.url = url
        self.pin = pin

    def request(self, method, path, data=None):
        if self.state.get('down'):
            raise SLHError('HUB_ACCOUNT_UNAVAILABLE', 503)
        if method == 'POST':
            assert path.endswith('/multisession-keys/validate')
            self.state['validations'] += 1
            key = data['multisession_authorization']['multisession_key_id']
            return {'ok':True, 'valid':not self.state.get('revoked',False),
                    'ready':not self.state.get('revoked',False),
                    'multisession_key_id':key,'hub_id':self.state.get('hub_id','hub-a'),
                    'cluster_id':self.state.get('cluster_id','cluster-a'),
                    'chain_id':self.state.get('chain_id','42424242'),
                    'wallet_address':self.state.get('wallet',WALLET),
                    'account_id':self.state.get('account',ACCOUNT)}
        assert method == 'GET' and path.endswith('wallet_address='+WALLET)
        amount = self.state.get('available',str(CREDIT_WEI_PER_CREDIT))
        balance = {'available_credit_wei':amount,'held_credit_wei':self.state.get('held','0'),
                   'spent_credit_wei':'0','bridge_completed_credit_wei':'0',
                   'wallet_address':self.state.get('balance_wallet',WALLET),
                   'owner_address':self.state.get('owner',WALLET),
                   'account_id':self.state.get('balance_account',ACCOUNT)}
        self.state['reads'] += 1
        return {'ok':True,'wallet_address':self.state.get('balance_wallet',WALLET),
                'account_id':self.state.get('balance_account',ACCOUNT),
                'unit':'compute_credit','account':balance}


def setup(tmp_path):
    state = {'validations':0,'reads':0}
    settings = update_settings(tmp_path, {'hub_url':'http://127.0.0.1:8872'}, 0)
    assert not settings['enabled']  # P1 must never opt in to marketplace silently.
    save_authorization(tmp_path, {'kind':'multisession_key','multisession_key_id':'secret-msk'})
    reader = lambda url,pin:FakeHub(url,pin,state)
    wallet=OrganizationWallet(tmp_path,'organization-a',reader_factory=reader)
    return wallet,state


def bind(wallet):
    return wallet.bind(expected_revision=0,expected_hub_id='hub-a',
                       expected_cluster_id='cluster-a',expected_chain_id='42424242')


def test_durable_one_wallet_and_fractional_credit(tmp_path):
    wallet,state=setup(tmp_path)
    assert wallet.status()['state']=='UNBOUND'
    status=bind(wallet)
    assert status['state']=='CURRENT'
    assert status['binding']['wallet_address']==WALLET
    assert status['balance']['available_credit_wei']==str(CREDIT_WEI_PER_CREDIT)
    assert status['balance']['available_credits_display']=='1'
    assert status['spendable_authorized'] is False
    assert status['balance']['unclaimed_worker_earnings']=='NOT_PROVEN_BY_BALANCE_ENDPOINT'
    assert 'secret-msk' not in json.dumps(status)
    assert len(status['balance']['proof_digest'])==64
    state['available']=str(CREDIT_WEI_PER_CREDIT//10)
    state['held']=str(CREDIT_WEI_PER_CREDIT//10)
    status=wallet.reconcile()
    assert status['balance']['available_credits_display']=='0.1'
    assert status['balance']['held_credits_display']=='0.1'
    restarted=OrganizationWallet(tmp_path,'organization-a',reader_factory=wallet.reader_factory)
    assert restarted.status()['state']=='CURRENT'
    assert restarted.status()['balance']['available_credit_wei']==str(CREDIT_WEI_PER_CREDIT//10)
    assert (tmp_path/'organization-wallet.sqlite3').is_file()


def test_revoked_msk_and_hub_outage_leave_stale_not_spendable(tmp_path):
    wallet,state=setup(tmp_path)
    bind(wallet)
    snapshot=wallet.status()['balance']['proof_digest']
    state['revoked']=True
    with pytest.raises(SLHError,match='HUB_MSK_INVALID'):
        wallet.reconcile()
    assert wallet.status()['state']=='STALE'
    assert wallet.status()['balance']['proof_digest']==snapshot
    state.pop('revoked')
    state['down']=True
    with pytest.raises(SLHError,match='HUB_ACCOUNT_UNAVAILABLE'):
        wallet.reconcile()
    assert wallet.status()['spendable_authorized'] is False
    state.pop('down')
    assert wallet.reconcile()['state']=='CURRENT'


@pytest.mark.parametrize('alteration',[
    {'hub_id':'hub-b'}, {'cluster_id':'cluster-b'}, {'chain_id':'wrong'},
    {'wallet':'0x'+'b'*40}, {'account':'0x'+'b'*40},
    {'balance_wallet':'0x'+'b'*40}, {'balance_account':'0x'+'b'*40},
    {'owner':'0x'+'b'*40},
    {'available':'-1'}, {'available':'0.1'}, {'available':1.5}, {'available':True},
    {'held':'-20'}, {'available':'9'*80},
])
def test_bad_hub_proof_preserves_last_known_snapshot(tmp_path,alteration):
    wallet,state=setup(tmp_path)
    bind(wallet)
    digest=wallet.status()['balance']['proof_digest']
    state.update(alteration)
    with pytest.raises(SLHError):
        wallet.reconcile()
    assert wallet.status()['state']=='STALE'
    assert wallet.status()['balance']['proof_digest']==digest


def test_binding_revision_auth_rotation_and_wallet_migration_guard(tmp_path):
    wallet,state=setup(tmp_path)
    bind(wallet)
    with pytest.raises(SLHError,match='WALLET_REVISION_CONFLICT'):
        bind(wallet)
    save_authorization(tmp_path, {'kind':'multisession_key','multisession_key_id':'new-msk'})
    assert wallet.status()['state']=='STALE'
    with pytest.raises(SLHError,match='WALLET_BINDING_CHANGED'):
        wallet.reconcile()
    rebound=wallet.bind(expected_revision=1,expected_hub_id='hub-a',
                        expected_cluster_id='cluster-a',expected_chain_id='42424242')
    assert rebound['state']=='CURRENT' and rebound['binding']['revision']==2
    state['wallet']='0x'+'c'*40
    state['account']=wallet_account_id(state['wallet'])
    with pytest.raises(SLHError,match='WALLET_MIGRATION_REQUIRED'):
        wallet.bind(expected_revision=2,expected_hub_id='hub-a',
                    expected_cluster_id='cluster-a',expected_chain_id='42424242')
    assert wallet.status()['binding']['revision']==2


def test_concurrent_binding_and_reconcile_does_not_duplicate(tmp_path):
    wallet,state=setup(tmp_path)
    def attempt(_):
        try:
            return bind(wallet)['binding']['revision']
        except SLHError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=12) as pool:
        results=list(pool.map(attempt,range(30)))
    assert results.count(1)==1
    assert all(r==1 or r=='WALLET_REVISION_CONFLICT' for r in results)
    with ThreadPoolExecutor(max_workers=10) as pool:
        rows=list(pool.map(lambda _:wallet.reconcile()['balance']['available_credits_display'],range(30)))
    assert rows==['1']*30


def test_observation_expiry_and_changed_hub_url(tmp_path):
    wallet,state=setup(tmp_path)
    bind(wallet)
    wallet.clock=lambda:10**11
    assert wallet.status()['state']=='STALE'
    wallet.clock=lambda:1
    update_settings(tmp_path, {'hub_url':'http://127.0.0.1:8873'}, 1)
    assert wallet.status()['state']=='STALE'
    with pytest.raises(SLHError,match='WALLET_BINDING_CHANGED'):
        wallet.reconcile()


def test_untrusted_remote_http_and_bad_pins_blocked():
    for url in ('http://evil.example:8872','http://192.168.1.1','http://127.0.0.1:9@evil.example',
                'http://127.0.0.1:80/unsafe','https://host.example/redirect'):
        with pytest.raises(SLHError):
            HubCreditReader(url)
    with pytest.raises(SLHError,match='INVALID_HUB_TLS_PIN'):
        HubCreditReader('https://hub.example', 'not-a-hash')
    assert HubCreditReader('http://127.0.0.1:8888').trust_mode=='LAB_ONLY'
    assert HubCreditReader('https://hub.example').trust_mode=='SYSTEM_TLS'


def test_actual_http_protocol_no_redirects(tmp_path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a): pass
        def do_GET(self):
            self.send_response(302)
            self.send_header('Location','http://evil.example/api/hub/v1/credits/balance')
            self.send_header('Content-Length','0')
            self.end_headers()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        reader=HubCreditReader('http://127.0.0.1:'+str(server.server_port))
        with pytest.raises(SLHError,match='HUB_ACCOUNT_UNAVAILABLE'):
            reader.request('GET','/api/hub/v1/credits/balance?wallet_address='+WALLET)
    finally:
        server.shutdown();server.server_close();thread.join()


def test_read_only_organization_api_enforces_admin_and_exposes_no_secrets(tmp_path):
    from main_computer.slh.api import SLHAPI
    class Authority:
        def authenticate(self, token, *, admin=False):
            if token != 'admin':
                raise SLHError('ADMIN_REQUIRED' if token else 'AUTH_REQUIRED',403 if token else 401)
            return {'user_id':'administrator','device_id':'admin-device','role':'admin'}
    wallet,state=setup(tmp_path)
    bind(wallet)
    api=SLHAPI(Authority(),wallet_service=wallet)
    assert api.call('GET','/v1/wallets')[0]==401
    assert api.call('GET','/v1/wallets',token='known-worker')[0]==403
    assert api.call('GET','/v1/wallets',token='admin')[1]['state']=='CURRENT'
    status,credits=api.call('GET','/v1/credits',token='admin')
    assert status==200
    assert credits['balance']['available_credits_display']=='1'
    assert credits['spendable_authorized'] is False
    assert 'secret-msk' not in json.dumps(credits)
    assert api.call('POST','/v1/accounting/reconcile',{'credit_wei':'100'},token='admin')[0]==400
    assert api.call('POST','/v1/accounting/reconcile',{},token='admin')[0]==200
    assert api.openapi()['paths']['/v1/credits']['get']['x-implementation']=='implemented'
    bare=SLHAPI(Authority())
    assert bare.call('GET','/v1/credits',token='admin')[0]==501
    assert bare.openapi()['paths']['/v1/credits']['get']['x-implementation']=='unimplemented'


def test_actual_http_hub_validation_and_balance_protocol(tmp_path):
    state={'available':str(CREDIT_WEI_PER_CREDIT//10)}
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a): pass
        def _json(self, body):
            data=json.dumps(body).encode()
            self.send_response(200);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        def do_POST(self):
            assert self.path=='/api/hub/v1/credits/multisession-keys/validate'
            payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(payload['multisession_authorization']['multisession_key_id'])
            self._json({'ok':True,'valid':True,'ready':True,'hub_id':'hub-a','cluster_id':'cluster-a',
                        'chain_id':'42424242','wallet_address':WALLET,'account_id':ACCOUNT,
                        'multisession_key_id':payload['multisession_authorization']['multisession_key_id']})
        def do_GET(self):
            assert self.path=='/api/hub/v1/credits/balance?wallet_address='+WALLET
            self._json({'ok':True,'wallet_address':WALLET,'account_id':ACCOUNT,'unit':'compute_credit',
                        'account':{'account_id':ACCOUNT,'wallet_address':WALLET,'owner_address':WALLET,
                                   'available_credit_wei':state['available'],'held_credit_wei':'0'}})
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        url='http://127.0.0.1:'+str(server.server_port)
        update_settings(tmp_path, {'hub_url':url}, 0)
        save_authorization(tmp_path, {'kind':'multisession_key','multisession_key_id':'production-format-key'})
        wallet=OrganizationWallet(tmp_path,'org-a')
        status=bind(wallet)
        assert status['state']=='CURRENT'
        assert status['balance']['available_credit_wei']==str(CREDIT_WEI_PER_CREDIT//10)
        assert status['binding']['trust_mode']=='LAB_ONLY'
        assert requests==['production-format-key']
        assert wallet.reconcile()['balance']['available_credits_display']=='0.1'
    finally:
        server.shutdown();server.server_close();thread.join()


def test_bound_wallet_blocks_cross_account_reauthorization_and_hub_switch(tmp_path):
    from main_computer.slh.marketplace_auto import AutomaticMarketplace, load_authorization
    wallet,state=setup(tmp_path)
    bind(wallet)
    class Proxy:
        sessions={}
        def reconcile(self, **kwargs):
            return {'live_public_sessions':[],'desired_public_workers':[], 'blocked':[], 'reason':'test'}
    controller=AutomaticMarketplace(Proxy(),tmp_path,wallet_service=wallet)
    state['wallet']='0x'+'b'*40
    state['account']=wallet_account_id(state['wallet'])
    with pytest.raises(SLHError,match='HUB_WALLET_MISMATCH'):
        controller.authorize({'kind':'multisession_key','multisession_key_id':'different-wallet-msk'})
    assert load_authorization(tmp_path)['multisession_key_id']=='secret-msk'
    with pytest.raises(SLHError,match='WALLET_MIGRATION_REQUIRED'):
        controller.configure({'hub_url':'http://127.0.0.1:9999'},1)
    state.pop('wallet');state.pop('account')
    controller.authorize({'kind':'multisession_key','multisession_key_id':'same-wallet-new-msk'})
    assert wallet.status()['state']=='STALE'
    assert load_authorization(tmp_path)['multisession_key_id']=='same-wallet-new-msk'
    assert wallet.bind(expected_revision=1,expected_hub_id='hub-a',
                       expected_cluster_id='cluster-a',expected_chain_id='42424242')['state']=='CURRENT'
