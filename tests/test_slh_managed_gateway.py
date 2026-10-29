"""Real TLS-pinned Worker/SLH session tests; no paid marketplace mutations."""
from __future__ import annotations

from pathlib import Path
import secrets
import tempfile
import threading
import time
import unittest

from main_computer.slh.authority import SLHAuthority
from main_computer.slh.authenticator import generate_identity,sign_object
from main_computer.slh.managed_gateway import SLHManagedGateway,ManagedScheduler,ManagedWorker
from main_computer.slh.managed_client import ManagedSLHWorkerClient
from tools.slh_authenticator_cli import save_identity, load_identity


class TestSLHManagedGateway(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.auth=SLHAuthority(self.root/'state')
        admin_key,public=generate_identity()
        statement={'version':1,'action':'slh.admin.bootstrap','org_id':'lab',
                   'admin_id':'owner','device_id':'admin','public_key':public}
        self.auth.bootstrap({**statement,'proof':sign_object(admin_key,statement),
                             'admin_name':'owner','bootstrap_secret':(self.root/'state'/'bootstrap.secret').read_text().strip()},loopback=True)
        self.identity_dir=self.root/'auth'

    def enroll(self, did):
        save_identity(self.identity_dir,did)
        key,public=load_identity(self.identity_dir,did)
        statement={'selected_admin':'owner','device_id':did,'public_key':public,
                   'kind':'worker','device_name':did,'claimed_user':'test',
                   'requested_role':'worker','client_nonce':secrets.token_urlsafe(32)}
        signed={**statement,'version':1,'action':'slh.device.enrollment.request','org_id':'lab'}
        result=self.auth.enrollment_request({**statement,'proof':sign_object(key,signed)})
        self.auth.approve(result['request_id'],admin_id='owner')

    def make_client(self, gateway, did, *, pin=None, callback=None):
        market={'rings':['ring-2'],'capabilities':['chat.completions'],'models':['mock-model'],
                'price':{'amount':'1','unit':'compute_credit'},'max_concurrency':1}
        return ManagedSLHWorkerClient(device_id=did,identity_dir=self.identity_dir,
            tls_fingerprint=pin or gateway.fingerprint, hub_url='https://127.0.0.1:'+str(gateway.port),
            worker_id=did,auth_message={'type':'worker.auth','device_id':did,'market':market},
            work_executor=callback or (lambda offer:{'status':'success','response':{'content':'completed'}}))

    def run_gateway(self):
        gateway=SLHManagedGateway(self.auth,host='127.0.0.1',port=0)
        thread=gateway.start_background()
        self.addCleanup(gateway.stop)
        for _ in range(100):
            if gateway._server:return gateway
            time.sleep(0.02)
        raise AssertionError('gateway did not start')

    def test_managed_no_wallet_and_reserved_execute(self):
        self.enroll('worker-1')
        gateway=self.run_gateway()
        client=self.make_client(gateway,'worker-1')
        snapshot=client.start()
        self.assertEqual(snapshot['accepted']['service'],'main_computer.slh')
        for _ in range(40):
            if gateway.scheduler.snapshot():break
            time.sleep(0.02)
        self.assertEqual(len(gateway.scheduler.snapshot()),1)
        sid='session1';w=gateway.scheduler.reserve(sid,device_id='worker-1')
        with self.assertRaises(RuntimeError):gateway.scheduler.reserve('session2',device_id='worker-1')
        output=gateway.scheduler.execute_authorized({'session_id':sid,'request_id':'req1','run_id':'run1',
                                                        'work':{'model':'mock-model','input':'fake'}})
        self.assertEqual(output['status'],'success')
        self.assertEqual(gateway.scheduler.snapshot()[0]['reserved'],0)
        client.close()

    def test_two_walletless_workers_share_real_stable_hub_msk(self):
        from tests.test_stable_hub_worker_live_session import (
            _start_server,_post_json,_signed_msk_request,
        )
        from main_computer.stable_hub_msk import InMemoryStableMultiSessionKeyStore
        from main_computer.stable_hub_worker_sessions import InMemoryStableWorkerSessionStore
        from main_computer.slh.managed_gateway import SLHMarketplaceProxy
        for did in ('worker-1','worker-2'):self.enroll(did)
        gateway=self.run_gateway()
        clients=[self.make_client(gateway,did) for did in ('worker-1','worker-2')]
        hub,thread=_start_server('dev-hub3',InMemoryStableMultiSessionKeyStore(),
                                 InMemoryStableWorkerSessionStore())
        proxy=SLHMarketplaceProxy(gateway)
        try:
            for client in clients:client.start()
            url=f'http://127.0.0.1:{hub.server_port}'
            issued=_post_json(url+'/api/hub/v1/credits/multisession-keys/request',
                              {'signed_request':_signed_msk_request(request_id='slh-two-walletless')})
            auth={'kind':'multisession_key','multisession_key_id':issued['key']['id'],
                  'wallet_address':issued['key']['wallet_address'],'chain_id':'42424242'}
            a=proxy.connect_device('worker-1',hub_url=url,auth=auth)
            b=proxy.connect_device('worker-2',hub_url=url,auth=auth)
            self.assertTrue(a['connected'] and b['connected'])
            self.assertEqual(len(proxy.sessions),2)
            accounts={x.accepted.get('account_id') for x in proxy.sessions.values()}
            connections={x.accepted.get('connection_id') for x in proxy.sessions.values()}
            self.assertEqual(accounts,{issued['key']['account_id']})
            self.assertEqual(len(connections),2)
            proxy.disconnect_device('worker-1')
            self.assertIn('worker-2',proxy.sessions)
            self.assertTrue(proxy.sessions['worker-2'].is_alive)
        finally:
            proxy.close()
            for client in clients:client.close()
            hub.shutdown();hub.server_close();thread.join(timeout=2)

    def test_policy_reconcile_changes_real_stable_hub_advertisements(self):
        from tests.test_stable_hub_worker_live_session import _start_server,_post_json,_signed_msk_request
        from main_computer.stable_hub_msk import InMemoryStableMultiSessionKeyStore
        from main_computer.stable_hub_worker_sessions import InMemoryStableWorkerSessionStore
        from main_computer.slh.managed_gateway import SLHMarketplaceProxy
        from main_computer.slh.scheduler_policy import SchedulerPolicyStore
        for did in ('worker-a','worker-b','worker-c','worker-d'):self.enroll(did)
        gateway=self.run_gateway()
        clients=[self.make_client(gateway,did) for did in ('worker-a','worker-b','worker-c','worker-d')]
        hub,thread=_start_server('dev-hub3',InMemoryStableMultiSessionKeyStore(),
                                 InMemoryStableWorkerSessionStore())
        proxy=SLHMarketplaceProxy(gateway)
        try:
            for client in clients:client.start()
            url=f'http://127.0.0.1:{hub.server_port}'
            issued=_post_json(url+'/api/hub/v1/credits/multisession-keys/request',
                              {'signed_request':_signed_msk_request(request_id='scheduler-policy-msk')})
            auth={'kind':'multisession_key','multisession_key_id':issued['key']['id'],
                  'wallet_address':issued['key']['wallet_address'],'chain_id':'42424242'}
            store=SchedulerPolicyStore(self.auth)
            store.update({'max_public_percent':50,'min_private_idle_percent':25},
                         expected_revision=0,actor='owner')
            first=proxy.reconcile(hub_url=url,auth=auth)
            self.assertEqual(len(first['live_public_sessions']),2)
            self.assertEqual(len(set(x.accepted.get('connection_id') for x in proxy.sessions.values())),2)
            self.assertEqual({x.accepted.get('account_id') for x in proxy.sessions.values()},
                             {issued['key']['account_id']})
            store.update({'max_public_percent':0},expected_revision=1,actor='owner')
            drained=proxy.reconcile(hub_url=url,auth=auth)
            self.assertEqual(drained['live_public_sessions'],[])
            self.assertEqual(len(drained['drained']),2)
        finally:
            proxy.close()
            for client in clients:client.close()
            hub.shutdown();hub.server_close();thread.join(timeout=2)

    def test_unenrolled_rejected_and_tls_pin_mismatch(self):
        self.enroll('worker-1')
        gateway=self.run_gateway()
        wrong=self.make_client(gateway,'worker-1',pin='0'*64)
        with self.assertRaisesRegex(RuntimeError,'SLH_TLS_PIN_MISMATCH'):wrong.start()
        save_identity(self.identity_dir,'unknown')
        unknown=self.make_client(gateway,'unknown')
        with self.assertRaises(RuntimeError):unknown.start()
        unknown.close(reason='unenrolled')
        self.assertEqual(gateway.scheduler.snapshot(),[])

    def test_revoked_worker_cannot_reserve_after_authentication(self):
        self.enroll('worker-1')
        gateway=self.run_gateway()
        client=self.make_client(gateway,'worker-1')
        try:
            client.start()
            self.auth.revoke_device('worker-1','owner')
            with self.assertRaises(RuntimeError):
                gateway.scheduler.reserve('future-job',device_id='worker-1')
        finally:client.close()

    def test_existing_worker_runtime_accepts_managed_mode_without_wallet(self):
        from main_computer.viewport_routes_energy import ViewportEnergyRoutesMixin
        from types import SimpleNamespace
        runtime=ViewportEnergyRoutesMixin()
        runtime.server=SimpleNamespace(config=SimpleNamespace(hub_url='http://127.0.0.1:8871'))
        settings=runtime._sanitize_worker_settings({
            'selectedNetwork':'slh','workerAutoConnectNetwork':'slh','sellerEnabled':True,
            'managedSLHUrl':'https://127.0.0.1:8898','managedSLHDeviceId':'worker-1',
            'managedSLHFingerprint':'a'*64,'managedSLHIdentityDir':str(self.identity_dir),
        })
        setup=runtime._worker_runtime_setup_state(settings)
        self.assertTrue(setup['ready'])
        self.assertFalse(setup['multisessionAuthorizationPresent'])
        self.assertEqual(runtime._worker_runtime_worker_id(settings),'worker-1')
        self.assertEqual(runtime._worker_runtime_hub_url(settings),'https://127.0.0.1:8898')

    def test_wallet_file_uses_existing_stable_hub_signature_key(self):
        from tools.slh_managed_gateway import wallet_from_file,authenticated_upstream
        walletfile=self.root/'wallet.json'
        walletfile.write_text('"0x'+('33'*32)+'"')
        wallet=wallet_from_file(walletfile)
        self.assertEqual(wallet.address.lower(),'0x5cbdd86a2fa8dc4bddd8a8f69dba48572eec07fb')
        with self.assertRaises(ValueError):authenticated_upstream('mainnet',walletfile)

    def test_server_transport_identity_persistent(self):
        a=SLHManagedGateway(self.auth,port=0)
        b=SLHManagedGateway(self.auth,port=0)
        self.assertEqual(a.fingerprint,b.fingerprint)
        self.assertNotEqual(a.fingerprint,'0'*64)

    def test_scheduler_disconnect_and_isolation(self):
        sch=ManagedScheduler()
        class Fake:
            def send(self,*args):pass
        a=ManagedWorker('a',Fake(),{},['a'])
        b=ManagedWorker('b',Fake(),{},['b'])
        sch.register(a);sch.register(b)
        sch.reserve('s1',device_id='a')
        sch.reserve('s2',device_id='b')
        sch.unregister(a)
        self.assertEqual(sch.worker_for('s2').device_id,'b')
        with self.assertRaises(ConnectionError):sch.worker_for('s1')

if __name__=='__main__':unittest.main()
