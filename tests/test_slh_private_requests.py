"""P2 durability, access control and real managed private Worker execution."""
from pathlib import Path
import secrets
import tempfile
import threading
import time
import unittest

from main_computer.slh.authority import SLHAuthority
from main_computer.slh.authenticator import generate_identity, sign_object
from main_computer.slh.api import SLHAPI
from main_computer.slh.managed_gateway import SLHManagedGateway
from main_computer.slh.managed_client import ManagedSLHWorkerClient
from main_computer.slh.private_requests import PrivateRequests
from tools.slh_authenticator_cli import save_identity, load_identity


class TestPrivateRequests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        from tools.slh_seh_connector import service_identity
        service_identity(self.root/'state', create=True)
        self.auth=SLHAuthority(self.root/'state')
        self.identity_dir=self.root/'client-keys'
        key,pub=generate_identity()
        self.admin_key=key  # P3 admin policy tests sign real administrator sessions
        statement={'version':1,'action':'slh.admin.bootstrap','admin_id':'admin','device_id':'admin-device','public_key':pub}
        self.auth.bootstrap({**statement,'proof':sign_object(key,statement),
                             'admin_name':'admin','bootstrap_secret':(self.root/'state'/'bootstrap.secret').read_text().strip()},loopback=True)
        self.gateway=SLHManagedGateway(self.auth,host='127.0.0.1',port=0)
        thread=self.gateway.start_background()
        self.addCleanup(self.gateway.stop)
        for _ in range(200):
            if self.gateway._server:break
            if not thread.is_alive():raise RuntimeError('gateway did not start')
            time.sleep(0.01)
        self.assertIsNotNone(self.gateway._server)
        self.credits_wei=10**20
        class LabWallet:
            def __init__(wallet):wallet.balance=self.credits_wei
            def reconcile(wallet):
                return {'state':'CURRENT','balance':{'available_credit_wei':str(wallet.balance)}}
            status=reconcile
        self.wallet=LabWallet()
        self.gateway.scheduler.credit_provider=self.wallet.status
        self.private=PrivateRequests(self.root/'state',self.auth,self.gateway,
                                     execution_timeout=12,wallet_service=self.wallet)
        self.api=SLHAPI(self.auth,private_requests=self.private)

    def enroll(self,did,role):
        save_identity(self.identity_dir,did)
        key,pub=load_identity(self.identity_dir,did)
        statement={'selected_admin':'admin','device_id':did,'public_key':pub,
                   'kind':role,'device_name':did,'claimed_user':did,
                   'requested_role':role,'client_nonce':secrets.token_urlsafe(32)}
        signed={**statement,'version':1,'action':'slh.device.enrollment.request',
                'audience':'slh://'+self.auth.service_fingerprint()}
        req=self.auth.enrollment_request({**statement,'proof':sign_object(key,signed)})
        self.auth.approve(req['request_id'],admin_id='admin')
        challenge=self.auth.challenge(did)
        return self.auth.verify_login({'device_id':did,'challenge_id':challenge['challenge_id'],
                                       'signature':sign_object(key,challenge)})['access_token']

    def worker(self,did='worker-1',executor=None,price='1'):
        self.enroll(did,'worker')
        market={'rings':['ring-2'],'capabilities':['chat.completions'],
                'models':['mock-model'],'price':{'amount':price,'unit':'compute_credit'}}
        worker=ManagedSLHWorkerClient(device_id=did,identity_dir=self.identity_dir,
                     tls_fingerprint=self.gateway.fingerprint,
                     hub_url='https://127.0.0.1:'+str(self.gateway.port),
                     worker_id=did,auth_message={'type':'worker.auth','device_id':did,'market':market},
                     work_executor=executor or (lambda offer:{'status':'success','response':{'content': 'computed '+str(len(offer['work']['input']['prompt']))}}))
        worker.start()
        self.addCleanup(worker.close)
        return worker

    def req(self,key='x', prompt='five words'):
        return {'request_id':key,'model':'mock-model',
                'work':{'model':'mock-model','input':{'kind':'chat','prompt':prompt}}}

    def submit(self,token,b):return self.api.call('POST','/v1/work',b,token=token,loopback=False)
    def read(self,token,req):return self.api.call('GET','/v1/work/'+req,token=token,loopback=False)

    def test_real_private_worker_success_and_metadata_only_durable_replay(self):
        requester=self.enroll('req-1','requester')
        self.worker()
        status,result=self.submit(requester,self.req())
        self.assertEqual(status,200,result)
        self.assertEqual(result['status'],'SUCCEEDED')
        self.assertEqual(result['result']['response']['content'],'computed 10')
        self.assertEqual(result['worker_device_id'],'worker-1')
        self.assertEqual(len(result['result_hash']),64)
        st,reloaded=self.read(requester,'x')
        self.assertEqual(st,200)
        self.assertNotIn('result',reloaded)
        self.assertNotIn('five words',self.private.db_path.read_bytes().decode('latin1'))
        self.assertNotIn('computed 10',self.private.db_path.read_bytes().decode('latin1'))
        after=PrivateRequests(self.root/'state',self.auth,self.gateway)
        self.assertEqual(after.get({'role':'requester','device_id':'req-1'},'x')['status'],'SUCCEEDED')
        self.assertNotIn('result',self.submit(requester,self.req())[1])
        self.assertEqual(self.submit(requester,self.req(prompt='different'))[1]['error']['code'],'PRIVATE_REQUEST_ID_REUSED')
        self.assertEqual(self.gateway.scheduler.snapshot()[0]['reserved'],0)

    def test_cross_requester_access_denied_and_revoked_role(self):
        a=self.enroll('req-1','requester');b=self.enroll('req-2','requester')
        self.worker()
        self.assertEqual(self.submit(a,self.req())[1]['status'],'SUCCEEDED')
        self.assertEqual(self.read(b,'x')[0],404)
        self.assertEqual(self.read(b,'x')[1]['error']['code'],'PRIVATE_REQUEST_NOT_FOUND')
        self.assertEqual(self.submit(a,{**self.req(), 'device_id':'req-2'})[0],400)
        self.auth.revoke_device('req-1','admin')
        self.assertEqual(self.read(a,'x')[0],401)
        self.assertEqual(self.submit(a,self.req('new'))[0],401)

    def test_no_model_and_public_worker_collision(self):
        a=self.enroll('req-1','requester');self.worker()
        self.gateway.scheduler.mark_advertised('worker-1', True)
        self.assertEqual(self.submit(a,self.req())[1]['failure_code'],'PUBLIC_WITHDRAWAL_REQUIRED')
        self.assertEqual(self.submit(a,self.req('different'))[1]['failure_code'],'NO_ELIGIBLE_WORKER')
        self.gateway.scheduler.mark_advertised('worker-1',False)
        bad=self.req('wrong');bad['model']='other-model';bad['work']['model']='other-model'
        self.assertEqual(self.submit(a,bad)[1]['failure_code'],'NO_ELIGIBLE_WORKER')

    def test_uncertain_execution_is_not_replayed(self):
        a=self.enroll('req-1','requester')
        fired=threading.Event();release=threading.Event();completed=threading.Event();count=[0]
        def computation(offer):
            count[0]+=1;fired.set();release.wait(6)
            completed.set()
            return {'status':'success','response':{'content':'done'}}
        self.worker(executor=computation)
        self.private.execution_timeout=.2
        _,response=self.submit(a,self.req())
        self.assertTrue(fired.is_set())
        self.assertEqual(response['status'],'OUTCOME_UNKNOWN')
        self.assertNotIn('result',response)
        self.assertEqual(self.submit(a,self.req())[1]['status'],'OUTCOME_UNKNOWN')
        self.assertEqual(count[0],1)
        release.set()
        self.assertTrue(completed.wait(3))
        self.assertEqual(PrivateRequests(self.root/'state',self.auth,self.gateway).get({'role':'requester','device_id':'req-1'},'x')['status'],'OUTCOME_UNKNOWN')

    def test_worker_terminal_failure_is_not_success(self):
        a=self.enroll('req-1','requester')
        def fail(offer):raise RuntimeError('private prompt should never enter error journal')
        self.worker(executor=fail)
        _,result=self.submit(a,self.req())
        self.assertEqual(result['status'],'FAILED')
        self.assertEqual(result['failure_code'],'WORKER_TERMINAL_FAILED')
        self.assertNotIn('private prompt',self.private.db_path.read_bytes().decode('latin1'))

    def test_role_validation_and_prohibited_echo(self):
        self.worker()
        w=self.enroll('worker-2','worker')
        self.assertEqual(self.submit(w,self.req())[0],403)
        a=self.enroll('req-1','requester')
        echo=self.req();echo['work']['input']={'kind':'echo','prompt':'sensitive'}
        self.assertEqual(self.submit(a,echo)[1]['error']['code'],'PRIVATE_ECHO_NOT_ALLOWED')
        self.assertEqual(self.api.call('POST','/v1/work',self.req(),token='')[0],401)
        self.assertEqual(self.read(a,'missing')[0],404)

    def test_concurrent_same_id_single_execution(self):
        a=self.enroll('req-1','requester');count=[0];lock=threading.Lock()
        def calculate(offer):
            with lock:count[0]+=1
            time.sleep(.1)
            return {'status':'success','response':{'content':'real result'}}
        self.worker(executor=calculate)
        responses=[]
        threads=[threading.Thread(target=lambda:responses.append(self.submit(a,self.req()))) for _ in range(5)]
        for t in threads:t.start()
        for t in threads:t.join(5)
        self.assertEqual(len(responses),5)
        self.assertEqual(count[0],1)
        self.assertEqual(self.read(a,'x')[1]['status'],'SUCCEEDED')

    def test_interrupted_dispatch_recovers_unknown(self):
        a=self.enroll('req-1','requester')
        with self.private._db() as db:
            db.execute('INSERT INTO private_requests (requester_device_id,request_id,request_digest,model,status,admitted_at_ns,updated_at_ns) VALUES (?,?,?,?,?,?,?)',
                       ('req-1','lost','x','mock-model','DISPATCHED',1,1))
        recovered=PrivateRequests(self.root/'state',self.auth,self.gateway)
        self.assertEqual(recovered.get({'role':'requester','device_id':'req-1'},'lost')['status'],'OUTCOME_UNKNOWN')

if __name__=='__main__':unittest.main()


def test_credit_admission_and_net_recovery_with_one_worker():
    fixture=TestPrivateRequests('test_real_private_worker_success_and_metadata_only_durable_replay')
    fixture.setUp()
    try:
        token=fixture.enroll('req-credit','requester')
        fixture.worker()
        fixture.wallet.balance=10**17  # 0.1 credit: less than the 0.2 assessment
        rejected=fixture.submit(token,fixture.req('not-funded'))[1]
        assert rejected['failure_code']=='INSUFFICIENT_LOCAL_CREDITS'
        assert fixture.private.obligations()['count']==0
        fixture.wallet.balance=2*10**17
        accepted=fixture.submit(token,fixture.req('funded'))[1]
        assert accepted['status']=='SUCCEEDED'
        assert fixture.private.obligations()['outstanding_credit_wei']==str(2*10**17)
        # Unpaid local obligations cannot be counted as free Worker-bank credits.
        assert fixture.private.net_credit_status()['balance']['available_credit_wei']=='0'
        assert len(fixture.gateway.scheduler.plan().desired)==1
        rejected2=fixture.submit(token,fixture.req('second'))[1]
        assert rejected2['failure_code']=='INSUFFICIENT_LOCAL_CREDITS'
    finally:
        fixture.doCleanups()


def test_private_admission_fails_closed_on_unverified_wallet():
    fixture=TestPrivateRequests('test_real_private_worker_success_and_metadata_only_durable_replay')
    fixture.setUp()
    try:
        token=fixture.enroll('req-credit','requester')
        fixture.worker()
        fixture.wallet.reconcile=lambda:{'state':'STALE','balance':{'available_credit_wei':str(10**20)}}
        failed=fixture.submit(token,fixture.req('stale'))[1]
        assert failed['failure_code']=='LOCAL_CREDIT_BALANCE_UNVERIFIED'
    finally:
        fixture.doCleanups()


def test_local_charge_tracks_worker_market_price_not_fixed_credit():
    fixture=TestPrivateRequests('test_real_private_worker_success_and_metadata_only_durable_replay')
    fixture.setUp()
    try:
        token=fixture.enroll('req-price','requester')
        fixture.worker(price='3')
        fixture.wallet.balance=5*10**17  # below 20% of a 3-credit job
        failed=fixture.submit(token,fixture.req('too-expensive'))[1]
        assert failed['failure_code']=='INSUFFICIENT_LOCAL_CREDITS'
        fixture.wallet.balance=6*10**17
        success=fixture.submit(token,fixture.req('affordable'))[1]
        assert success['status']=='SUCCEEDED'
        entries=fixture.private.obligations()['obligations']
        assert len(entries)==1
        assert entries[0]['reference_credit_wei']==str(3*10**18)
        assert entries[0]['amount_credit_wei']==str(6*10**17)
    finally:
        fixture.doCleanups()


def test_live_hub_single_worker_credit_recovery_reclaims_for_affordable_local_request():
    """Actual Hub ACK, not simulated planner release, gates local execution."""
    from tests.test_stable_hub_worker_live_session import (
        _start_server, _post_json, _signed_msk_request,
    )
    from main_computer.stable_hub_msk import InMemoryStableMultiSessionKeyStore
    from main_computer.stable_hub_worker_sessions import InMemoryStableWorkerSessionStore
    from main_computer.slh.managed_gateway import SLHMarketplaceProxy
    from main_computer.slh.marketplace_auto import AutomaticMarketplace

    fixture = TestPrivateRequests('test_real_private_worker_success_and_metadata_only_durable_replay')
    fixture.setUp()
    hub, thread = _start_server('dev-hub3', InMemoryStableMultiSessionKeyStore(),
                                InMemoryStableWorkerSessionStore())
    proxy = SLHMarketplaceProxy(fixture.gateway)
    try:
        token = fixture.enroll('requester-reclaim', 'requester')
        fixture.worker()
        fixture.wallet.balance = 5 * 10**17  # below reserve, above 0.2-credit assessment
        url = f'http://127.0.0.1:{hub.server_port}'
        issued = _post_json(url + '/api/hub/v1/credits/multisession-keys/request',
                            {'signed_request': _signed_msk_request(request_id='real-reclaim')})
        authorization = {'kind': 'multisession_key',
                         'multisession_key_id': issued['key']['id'],
                         'wallet_address': issued['key']['wallet_address'],
                         'chain_id': '42424242'}
        controller = AutomaticMarketplace(proxy, fixture.root / 'state')
        controller.authorize({'kind': 'multisession_key',
                              'multisession_key_id': authorization['multisession_key_id']})
        controller.configure({'enabled': True, 'hub_url': url}, 0)
        fixture.private.marketplace_controller = controller
        first = controller.reconcile()
        assert first['live_public_sessions'] == ['worker-1']
        connection = first['upstream_connection_ids']['worker-1']
        assert hub.worker_session_directory.get_owner(connection)['status'] == 'live'
        # The Hub has a real public connection until it commits its close.
        assert fixture.gateway.scheduler.snapshot()[0]['public_advertised']
        # Model a live public commitment. A private request must wait for its
        # release while the Hub connection remains registered, not preempt it.
        fixture.gateway.scheduler.reserve_public('held-public-job', device_id='worker-1')
        witnessed = {}
        def release_existing_job():
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not fixture.gateway.scheduler.withdrawals():
                time.sleep(.01)
            witnessed['busy_before_release'] = (
                fixture.gateway.scheduler.snapshot()[0]['reserved'] == 1
                and hub.worker_session_directory.get_owner(connection)['status'] == 'live')
            fixture.gateway.scheduler.release('held-public-job')
        release_thread = threading.Thread(target=release_existing_job)
        release_thread.start()
        code, response = fixture.submit(token, fixture.req('reclaim'))
        release_thread.join(timeout=2)
        assert witnessed.get('busy_before_release') is True
        assert code == 200, response
        assert response['status'] == 'SUCCEEDED', response
        assert response['worker_device_id'] == 'worker-1'
        assert response['result']['response']['content'] == 'computed 10'
        assert hub.worker_session_directory.get_owner(connection)['status'] == 'closed'
        assert hub.worker_market_directory.get_worker(connection)['status'] == 'closed'
        assert not proxy.sessions
        assert not fixture.gateway.scheduler.snapshot()[0]['public_advertised']
        assert fixture.private.obligations()['outstanding_credit_wei'] == str(2 * 10**17)
        # After the local reservation completes, a new (different) public
        # session can be offered to earn more credits below the threshold.
        restored = controller.reconcile()
        assert restored['live_public_sessions'] == ['worker-1']
        assert restored['upstream_connection_ids']['worker-1'] != connection
    finally:
        proxy.close()
        hub.shutdown(); hub.server_close(); thread.join(timeout=2)
        fixture.doCleanups()


def test_live_hub_missing_ack_never_frees_worker_for_private_execution():
    from tests.test_stable_hub_worker_live_session import (
        _start_server, _post_json, _signed_msk_request,
    )
    from main_computer.stable_hub_msk import InMemoryStableMultiSessionKeyStore
    from main_computer.stable_hub_worker_sessions import InMemoryStableWorkerSessionStore
    from main_computer.slh.managed_gateway import SLHMarketplaceProxy

    fixture = TestPrivateRequests('test_real_private_worker_success_and_metadata_only_durable_replay')
    fixture.setUp()
    hub, thread = _start_server('dev-hub3', InMemoryStableMultiSessionKeyStore(),
                                InMemoryStableWorkerSessionStore())
    proxy = SLHMarketplaceProxy(fixture.gateway)
    try:
        fixture.worker()
        fixture.wallet.balance = 5 * 10**17
        url = f'http://127.0.0.1:{hub.server_port}'
        issued = _post_json(url + '/api/hub/v1/credits/multisession-keys/request',
                            {'signed_request': _signed_msk_request(request_id='missing-ack')})
        auth = {'kind': 'multisession_key', 'multisession_key_id': issued['key']['id'],
                'wallet_address': issued['key']['wallet_address'], 'chain_id': '42424242'}
        proxy.connect_device('worker-1', hub_url=url, auth=auth)
        slot = proxy.sessions['worker-1']
        original = slot.retire_with_hub_ack
        slot.retire_with_hub_ack = lambda timeout=5: False
        assert proxy.disconnect_device('worker-1') is False
        assert 'worker-1' in proxy.sessions
        assert fixture.gateway.scheduler.snapshot()[0]['public_advertised']
        try:
            fixture.gateway.scheduler.reserve_private('unsafe', model='mock-model')
        except RuntimeError:
            pass
        else:
            raise AssertionError('local Worker was released without a Hub acknowledgment')
        slot.retire_with_hub_ack = original
        assert proxy.disconnect_device('worker-1') is True
        assert not fixture.gateway.scheduler.snapshot()[0]['public_advertised']
    finally:
        proxy.close()
        hub.shutdown(); hub.server_close(); thread.join(timeout=2)
        fixture.doCleanups()


def test_live_hub_two_workers_reclaim_one_without_disturbing_other_public_slot():
    from tests.test_stable_hub_worker_live_session import (
        _start_server, _post_json, _signed_msk_request,
    )
    from main_computer.stable_hub_msk import InMemoryStableMultiSessionKeyStore
    from main_computer.stable_hub_worker_sessions import InMemoryStableWorkerSessionStore
    from main_computer.slh.managed_gateway import SLHMarketplaceProxy
    from main_computer.slh.marketplace_auto import AutomaticMarketplace

    fixture = TestPrivateRequests('test_real_private_worker_success_and_metadata_only_durable_replay')
    fixture.setUp()
    hub, thread = _start_server('dev-hub3', InMemoryStableMultiSessionKeyStore(),
                                InMemoryStableWorkerSessionStore())
    proxy = SLHMarketplaceProxy(fixture.gateway)
    try:
        token = fixture.enroll('requester-two', 'requester')
        fixture.worker('worker-1'); fixture.worker('worker-2')
        fixture.wallet.balance = 5 * 10**17
        url = f'http://127.0.0.1:{hub.server_port}'
        issued = _post_json(url + '/api/hub/v1/credits/multisession-keys/request',
                            {'signed_request': _signed_msk_request(request_id='two-worker-reclaim')})
        controller = AutomaticMarketplace(proxy, fixture.root / 'state')
        controller.authorize({'kind': 'multisession_key',
                              'multisession_key_id': issued['key']['id']})
        controller.configure({'enabled': True, 'hub_url': url}, 0)
        fixture.private.marketplace_controller = controller
        first = controller.reconcile()
        assert set(first['live_public_sessions']) == {'worker-1', 'worker-2'}
        original = dict(first['upstream_connection_ids'])
        code, outcome = fixture.submit(token, fixture.req('two-worker-local'))
        assert code == 200 and outcome['status'] == 'SUCCEEDED', outcome
        assert outcome['worker_device_id'] == 'worker-1'
        assert hub.worker_session_directory.get_owner(original['worker-1'])['status'] == 'closed'
        assert hub.worker_session_directory.get_owner(original['worker-2'])['status'] == 'live'
        assert set(proxy.sessions) == {'worker-2'}
        assert proxy.sessions['worker-2'].is_alive
        assert proxy.sessions['worker-2'].accepted['connection_id'] == original['worker-2']
        # Returning to public earnings creates a fresh Worker-1 slot while
        # retaining Worker-2's original Hub-owned connection.
        restored = controller.reconcile()
        assert set(restored['live_public_sessions']) == {'worker-1', 'worker-2'}
        assert restored['upstream_connection_ids']['worker-1'] != original['worker-1']
        assert restored['upstream_connection_ids']['worker-2'] == original['worker-2']
    finally:
        proxy.close()
        hub.shutdown(); hub.server_close(); thread.join(timeout=2)
        fixture.doCleanups()
