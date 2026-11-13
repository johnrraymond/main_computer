"""SLH/SEH integrated lab safety and real socket acceptance checks."""
from __future__ import annotations

import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest import mock

from tools.slh_seh_lab.run_lab import (DEFAULT_TOPOLOGY, LabRefused,
                                       _ports_free, plan, validate_topology, verify_local)
from tools.slh_seh_lab.lab_relay import SEHTestRelay


class TestSLHSEHLab(unittest.TestCase):
    def test_plan_reuses_smoke_cluster_without_side_effects(self):
        result = plan(DEFAULT_TOPOLOGY)
        self.assertEqual(result['topology']['hub_urls'],
                         ['http://127.0.0.1:8870','http://127.0.0.1:8871','http://127.0.0.1:8872'])
        self.assertEqual(result['topology']['namespace'],'main-computer-hub-smoke')
        self.assertTrue(result['no_external_urls'])
        self.assertEqual(result['status'],'PLANNED_ONLY')
        self.assertEqual(result['limits']['production_seh'],'LOCAL_RUNTIME_TESTED_NO_COOLIFY')

    def test_refuse_external_topologies_and_namespaces(self):
        base = json.loads(Path(DEFAULT_TOPOLOGY).read_text())
        cases = [
            lambda d: d['network'].update(network_kind='mainnet'),
            lambda d: d['storage'].update(namespace='main-computer-stable-hub-dev'),
            lambda d: d['hubs'][0].update(hub_url='https://mainnet-hub.greatlibrary.io'),
            lambda d: d.update(entry_urls=['https://testnet-hub.greatlibrary.io',*d['entry_urls'][1:]]),
        ]
        with tempfile.TemporaryDirectory() as td:
            for i, change in enumerate(cases):
                payload=json.loads(json.dumps(base))
                change(payload)
                filename=Path(td)/f'{i}.json'
                filename.write_text(json.dumps(payload))
                with self.subTest(i=i), self.assertRaises((LabRefused,ValueError)):
                    validate_topology(filename)

    def test_busy_port_not_reclaimed(self):
        with socket.socket() as busy:
            busy.bind(('127.0.0.1',0))
            self.assertEqual(_ports_free([f'http://127.0.0.1:{busy.getsockname()[1]}']),
                             [busy.getsockname()[1]])

    def test_logout_invalidates_bearer_without_database_lookup(self):
        from main_computer.slh.authority import SLHAuthority, SLHError, _hash
        with tempfile.TemporaryDirectory() as td:
            auth = SLHAuthority(Path(td))
            token = 'test-bearer'
            auth._sessions[_hash(token)] = {'device_id':'irrelevant','user_id':'irrelevant',
                                            'expires_at':9999999999}
            with mock.patch.object(auth, 'authenticate', side_effect=AssertionError('DB lookup')):
                self.assertEqual(auth.logout(token)['status'],'LOGGED_OUT')
            self.assertNotIn(_hash(token), auth._sessions)
            with self.assertRaises(SLHError):
                auth.logout(token)

    def test_seh_settings_fail_closed_and_disabled_state(self):
        from main_computer.slh.seh_autoconnect import (
            SEHConnectionSettings, write_seh_settings, read_seh_settings)
        from main_computer.slh.authority import SLHAuthority
        from main_computer.slh.managed_gateway import SLHManagedGateway
        with tempfile.TemporaryDirectory() as td:
            state=Path(td)
            self.assertIsNone(read_seh_settings(state))
            with self.assertRaises(ValueError):
                write_seh_settings(state,SEHConnectionSettings(
                    True,'ws://example.com:8080',False,2))
            with self.assertRaises(ValueError):
                write_seh_settings(state,SEHConnectionSettings(
                    True,'ws://127.0.0.1:18080',False,2))
            self.assertIsNone(read_seh_settings(state))
            disabled=SEHConnectionSettings(False,'ws://127.0.0.1:18080',
                                            True,0.2)
            write_seh_settings(state,disabled)
            self.assertEqual(read_seh_settings(state),disabled)
            # Disabled config must not create an identity or contact an SEH.
            auth=SLHAuthority(state)
            gateway=SLHManagedGateway(auth,host='127.0.0.1',port=0)
            self.assertIsNone(gateway.seh_connection)
            self.assertFalse((state/'seh-service-signing-key.pem').exists())

    def test_relay_refuses_public_binding(self):
        with self.assertRaises(ValueError):
            SEHTestRelay('0.0.0.0',0,'127.0.0.1',8898)
        with self.assertRaises(ValueError):
            SEHTestRelay('127.0.0.1',0,'8.8.8.8',8898)

    def test_paid_cycle_rejects_external_or_nonlab_funding(self):
        from tools.slh_seh_lab.paid_cycle import verify_paid_cycle
        with self.assertRaisesRegex(ValueError,'non-loopback'):
            verify_paid_cycle(hub_url='https://mainnet-hub.greatlibrary.io', proxy=None,
                              gateway=None,ledger=None,worker_account_id='',lab_mode='three-hub-smoke-fdb')
        with self.assertRaisesRegex(ValueError,'disposable'):
            verify_paid_cycle(hub_url='http://127.0.0.1:8872', proxy=None,
                              gateway=None,ledger=None,worker_account_id='',lab_mode='mainnet')

    def test_verify_deployed_reports_coolify_and_isolated_reconnect(self):
        from types import SimpleNamespace
        from tools.slh_seh_lab.run_lab import verify_deployed
        class DummyConn:
            def connect(self):return self
            def stop(self):pass
        saved={'schema':'main-computer.slh-seh-lab.v2', 'mode':'verify-deployed',
               'status':'PASS','slh':{'gates':{'paid_worker_earning_exactly_once':True},'pass':True}}
        response={'schema':'main-computer.seh-local-coolify.v2','status':'DEPLOYED_VERIFIED',
                  'service_uuid':'lab-service','service_name':'main-computer-seh-org-slh',
                  'health_url':'http://127.0.0.1:18080/healthz','image_status':'BUILT_ON_COOLIFY_DOCKER_ENGINE'}
        with mock.patch('tools.slh_seh_lab.local_coolify.find_healthy_deployed',return_value=None), \
             mock.patch('tools.slh_seh_lab.local_coolify.deploy_local',return_value=response) as deploy, \
             mock.patch('tools.slh_seh_connector.service_identity',return_value=(object(),'public')), \
             mock.patch('tools.slh_seh_lab.run_lab._seh_http_identity',side_effect=lambda p,path:
                       {'service':'main_computer.seh','live':True,'slh_connected':True,'isolated_lab_channels':True}), \
             mock.patch('tools.slh_seh_lab.run_lab.run_smoke',return_value=saved) as smoke, \
             mock.patch('main_computer.slh.seh_transport.SLHSEHConnector',return_value=DummyConn()):
            result=verify_deployed(DEFAULT_TOPOLOGY,use_existing=False,
                     report_dir=Path('runtime/reports'),slh='slh',
                     port=18080,compose_output=Path('runtime/reports/compose.yml'))
        deploy.assert_called_once()
        self.assertTrue(deploy.call_args.kwargs['build'])
        self.assertEqual(smoke.call_args.kwargs['deployed']['slh'],'slh')
        self.assertEqual(len(smoke.call_args.kwargs['deployed']['lab_id']),32)
        self.assertEqual(result['status'],'PASS')
        self.assertTrue(result['slh']['gates']['deployed_original_slh_tunnel_preserved'])
        self.assertTrue(result['slh']['gates']['deployed_isolated_tunnel_reconnect'])
        self.assertEqual(result['coolify_deployment']['service_uuid'],'lab-service')
        self.assertNotIn('identity_key',result['coolify_deployment'])

    def test_verify_deployed_reuses_existing_coolify_without_redeploy(self):
        from tools.slh_seh_lab.run_lab import verify_deployed
        class DummyConn:
            def connect(self):return self
            def stop(self):pass
        saved={'schema':'lab','status':'PASS', 'slh':{'gates':{'paid':True},'pass':True}}
        existing={'status':'DEPLOYED_VERIFIED','service_uuid':'existing-uuid',
                  'service_name':'main-computer-seh-org-slh',
                  'health_url':'http://127.0.0.1:18080/healthz',
                  'image_status':'EXISTING_IMAGE_REUSED','existing_service_reused':True}
        with mock.patch('tools.slh_seh_lab.local_coolify.find_healthy_deployed',return_value=existing), \
             mock.patch('tools.slh_seh_lab.local_coolify.deploy_local') as deploy, \
             mock.patch('tools.slh_seh_connector.service_identity',return_value=(object(),'public')), \
             mock.patch('tools.slh_seh_lab.run_lab._seh_http_identity',return_value={
                        'service':'main_computer.seh','live':True,'slh_connected':True,'isolated_lab_channels':True}), \
             mock.patch('tools.slh_seh_lab.run_lab.run_smoke',return_value=saved), \
             mock.patch('main_computer.slh.seh_transport.SLHSEHConnector',return_value=DummyConn()):
            result=verify_deployed(DEFAULT_TOPOLOGY,use_existing=False,
                     report_dir=Path('runtime/reports'),slh='slh',
                     port=18080,compose_output=Path('runtime/reports/compose.yml'))
        deploy.assert_not_called()
        self.assertTrue(result['coolify_deployment']['existing_service_reused'])
        self.assertEqual(result['status'],'PASS')

    def test_verify_deployed_paid_lane_runs_with_no_original_tunnel(self):
        """Reproduce /readyz 503 after an SEH upgrade: paid lane must run."""
        from tools.slh_seh_lab.run_lab import verify_deployed
        class DummyConn:
            def connect(self):return self
            def stop(self):pass
        response={'status':'DEPLOYED_VERIFIED','service_uuid':'lab-service',
                  'service_name':'main-computer-seh-org-slh',
                  'health_url':'http://127.0.0.1:18080/healthz',
                  'image_status':'BUILT_ON_COOLIFY_DOCKER_ENGINE'}
        trial={'schema':'lab','status':'PASS', 'slh':{'gates':{'paid':True},'pass':True}}
        def http(_, path):
            return ({'service':'main_computer.seh','live':True} if path=='/healthz' else
                    {'service':'main_computer.seh','isolated_lab_channels':True} if path=='/lab-capabilities' else
                    {})
        with mock.patch('tools.slh_seh_lab.local_coolify.find_healthy_deployed',return_value=None), \
             mock.patch('tools.slh_seh_lab.local_coolify.deploy_local',return_value=response), \
             mock.patch('tools.slh_seh_connector.service_identity',return_value=(object(),'public')), \
             mock.patch('tools.slh_seh_lab.run_lab._seh_http_identity',side_effect=http), \
             mock.patch('tools.slh_seh_lab.run_lab.run_smoke',return_value=trial) as smoke, \
             mock.patch('main_computer.slh.seh_transport.SLHSEHConnector',return_value=DummyConn()):
            result=verify_deployed(DEFAULT_TOPOLOGY,use_existing=False,
                     report_dir=Path('runtime/reports'),slh='slh',
                     port=18080,compose_output=Path('runtime/reports/compose.yml'))
        smoke.assert_called_once()
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(result['primary_tunnel']['status'],'NOT_CONNECTED_AT_START')
        self.assertNotIn('deployed_original_slh_tunnel_preserved',result['slh']['gates'])
        self.assertTrue(result['slh']['gates']['deployed_isolated_tunnel_reconnect'])

    def test_verify_deployed_primary_lost_does_not_skip_paid_cycle(self):
        """If our upgrade loses a previously live tunnel, FAIL but keep paid evidence."""
        from tools.slh_seh_lab.run_lab import verify_deployed
        class DummyConn:
            def connect(self):return self
            def stop(self):pass
        response={'status':'DEPLOYED_VERIFIED','service_uuid':'lab-service',
                  'service_name':'main-computer-seh-org-slh',
                  'health_url':'http://127.0.0.1:18080/healthz',
                  'image_status':'BUILT_ON_COOLIFY_DOCKER_ENGINE'}
        trial={'schema':'lab','status':'PASS', 'slh':{'gates':{'paid':True},'pass':True}}
        ready_calls=iter([True,False])
        def http(_, path):
            return ({'service':'main_computer.seh','live':True} if path=='/healthz' else
                    {'service':'main_computer.seh','isolated_lab_channels':True} if path=='/lab-capabilities' else
                    {'service':'main_computer.seh','slh_connected':next(ready_calls,False)})
        with mock.patch('tools.slh_seh_lab.local_coolify.find_healthy_deployed',return_value=None), \
             mock.patch('tools.slh_seh_lab.local_coolify.deploy_local',return_value=response), \
             mock.patch('tools.slh_seh_connector.service_identity',return_value=(object(),'public')), \
             mock.patch('tools.slh_seh_lab.run_lab._seh_http_identity',side_effect=http), \
             mock.patch('tools.slh_seh_lab.run_lab._wait',return_value=False), \
             mock.patch('tools.slh_seh_lab.run_lab.run_smoke',return_value=trial) as smoke, \
             mock.patch('main_computer.slh.seh_transport.SLHSEHConnector',return_value=DummyConn()):
            result=verify_deployed(DEFAULT_TOPOLOGY,use_existing=False,
                     report_dir=Path('runtime/reports'),slh='slh',
                     port=18080,compose_output=Path('runtime/reports/compose.yml'))
        smoke.assert_called_once()
        self.assertEqual(result['status'],'FAIL')
        self.assertTrue(result['slh']['gates']['paid'])
        self.assertFalse(result['slh']['gates']['deployed_original_slh_tunnel_preserved'])
        self.assertEqual(result['primary_tunnel']['status'],'LOST_DURING_VERIFICATION')

    def test_verify_deployed_refuses_unhealthy_seh_even_when_deployment_claims_success(self):
        from tools.slh_seh_lab.run_lab import verify_deployed
        response={'status':'DEPLOYED_VERIFIED','service_uuid':'lab-service'}
        with mock.patch('tools.slh_seh_lab.local_coolify.find_healthy_deployed',return_value=None), \
             mock.patch('tools.slh_seh_lab.local_coolify.deploy_local',return_value=response), \
             mock.patch('tools.slh_seh_lab.run_lab._seh_http_identity',return_value={}), \
             mock.patch('tools.slh_seh_lab.run_lab.run_smoke') as smoke:
            with self.assertRaisesRegex(LabRefused,'not healthy'):
                verify_deployed(DEFAULT_TOPOLOGY,use_existing=False,
                     report_dir=Path('runtime/reports'),slh='slh',
                     port=18080,compose_output=Path('runtime/reports/compose.yml'))
            smoke.assert_not_called()

    def test_paid_work_through_isolated_channel_while_primary_remains_connected(self):
        from tools.slh_seh_lab.run_lab import _in_memory_hub, _local_demo
        from main_computer.slh.authenticator import generate_identity
        from main_computer.seh.relay import SEHRelay
        from main_computer.slh.seh_transport import SLHSEHConnector
        signer, public = generate_identity()
        relay = SEHRelay(
                          slh_public_key=public,host='127.0.0.1',port=0).start()
        url=f'ws://127.0.0.1:{relay.port}'
        primary=SLHSEHConnector(seh_url=url,identity_private_key=signer,
                        gateway_port=1,allow_lab=True).connect()
        server,thread = _in_memory_hub()
        try:
            trial=_local_demo(f'http://127.0.0.1:{server.server_port}',
                    topology_label='in-memory-stable-hub',payout_ledger=server.payout_ledger_directory,
                    deployed={'org':'lab-organization','slh':'my-slh','url':url,
                              'lab_id':'1'*32,'identity_key':signer})
            self.assertTrue(trial['pass'],trial)
            self.assertTrue(trial['gates']['seh_paid_worker_executed_through_real_relay'])
            self.assertTrue(relay.tunnel_ready, 'primary tunnel must remain connected')
            self.assertGreaterEqual(relay.authenticated_slh_count,3)
            self.assertTrue(trial['gates']['seh_configured_autoreconnect_after_drop'])
        finally:
            primary.stop()
            relay.stop()
            server.shutdown();server.server_close();thread.join(timeout=3)

    def test_complete_in_memory_stable_hub_and_relay(self):
        result=verify_local()
        self.assertEqual(result['status'],'PASS',result)
        self.assertTrue(all(result['slh']['gates'].values()))
        self.assertEqual(result['seh_coolify_contract']['status'],'PASS')
        self.assertTrue(result['real_process_credit_recovery']['credit_recovery']['pass'])
        self.assertEqual(result['production_seh'],'LOCAL_RUNTIME_TESTED_COOLIFY_NOT_TESTED')
        self.assertTrue(result['slh']['gates']['seh_paid_worker_executed_through_real_relay'])
        self.assertTrue(result['slh']['gates']['seh_configured_autoconnect_on_slh_start'])
        self.assertTrue(result['slh']['gates']['seh_configured_autoreconnect_after_drop'])
        self.assertTrue(result['slh']['gates']['seh_configured_disconnect_on_slh_stop'])
        self.assertEqual(result['paid_work_settlement'],'LAB_LEDGER_SETTLED_NOT_BRIDGED')
        paid=result['slh']['paid_marketplace']
        self.assertTrue(paid['pass'],paid)
        self.assertEqual(paid['charge_count'],1)
        self.assertEqual(paid['earning_count'],1)
        self.assertEqual(paid['charged_credit_wei'],paid['earned_credit_wei'])
        self.assertTrue(paid['managed_worker_proof'])
        self.assertTrue(paid['gates']['idempotent_request_not_double_charged'])
        self.assertTrue(paid['gates']['wallet_authorized_pending_batch'])



def test_initial_managed_worker_auth_retries_only_transient_handshake_timeout():
    from tools.slh_seh_lab.service_lifecycle import _await_initial_worker_auth
    with mock.patch('tools.slh_seh_lab.service_lifecycle._managed_auth',
                    side_effect=[TimeoutError('opening handshake not ready'), True]) as auth:
        assert _await_initial_worker_auth(39001, 'worker', object(), expected_pin='pin', timeout=2) is True
        assert auth.call_count == 2
        assert auth.call_args.kwargs['expected_pin'] == 'pin'


def test_initial_managed_worker_auth_never_retries_bad_pin_or_rejected_device():
    from tools.slh_seh_lab.service_lifecycle import _await_initial_worker_auth, ServiceLabFailure
    for rejection in ('SLH TLS pin changed', 'Worker login not accepted'):
        with mock.patch('tools.slh_seh_lab.service_lifecycle._managed_auth',
                        side_effect=ServiceLabFailure(rejection)) as auth:
            with unittest.TestCase().assertRaisesRegex(ServiceLabFailure, rejection):
                _await_initial_worker_auth(39001, 'worker', object(), expected_pin='pin', timeout=2)
            assert auth.call_count == 1


def test_initial_managed_worker_auth_still_fails_when_listener_never_responds():
    from tools.slh_seh_lab.service_lifecycle import _await_initial_worker_auth, ServiceLabFailure
    with mock.patch('tools.slh_seh_lab.service_lifecycle._managed_auth',
                    side_effect=TimeoutError('handshake timeout')) as auth:
        with unittest.TestCase().assertRaisesRegex(ServiceLabFailure,
                                                  'Timed out: initial managed Worker'):
            _await_initial_worker_auth(39001, 'worker', object(), expected_pin='pin', timeout=0.01)
        assert auth.call_count >= 1



def _disposable_recovery_pending_earning():
    """Real in-memory Hub ledger objects, not a mocked settlement response."""
    from main_computer.stable_hub_worker_sessions import (
        InMemoryStableWorkerSessionStore, StableHubPayoutLedgerDirectory)
    from main_computer.stable_hub_topology import load_stable_hub_topology
    from main_computer.hub_credit_indexer import wallet_account_id
    from main_computer.credit_units import CREDIT_WEI_PER_CREDIT
    from tests.test_stable_hub_worker_live_session import DEV_TOPOLOGY
    topo = load_stable_hub_topology(DEV_TOPOLOGY)
    ledger = StableHubPayoutLedgerDirectory(topology=topo, hub_id='dev-hub3',
                                           store=InMemoryStableWorkerSessionStore())
    buyer = '0x' + '11'*20
    org_wallet = '0x' + '22'*20
    org_account = wallet_account_id(org_wallet)
    request_id, session_id = 'recovery-proof-request', 'recovery-proof-session'
    ledger.fund_account(account_id=wallet_account_id(buyer), wallet_address=buyer,
                        credits='2', replace=True)
    hold = ledger.create_hold(
        account_id=wallet_account_id(buyer), wallet_address=buyer,
        request_id=request_id, session_id=session_id, run_id='recovery-proof-run',
        worker_id='live-worker', selected_price={'amount':'1','unit':'compute_credit'},
        requester_max_price={'amount':'2','unit':'compute_credit'}, partition='ring-2')
    charge = ledger.spend_request_credit(
        account_id=wallet_account_id(buyer), wallet_address=buyer,
        request_id=request_id, session_id=session_id, run_id='recovery-proof-run',
        worker_id='live-worker', worker_account_id=org_account,
        selected_price={'amount':'1','unit':'compute_credit'},
        requester_max_price={'amount':'2','unit':'compute_credit'},
        partition='ring-2', hold_id=hold['hold_id'], result={'ok':True})
    earning_id = charge['worker_earning']['earning_id']
    claim = ledger.record_worker_claim(worker_id=org_account,
            earning_ids=[earning_id], idempotency_key='claim-'+request_id)
    batch = ledger.create_worker_settlement_batch(worker_id=org_account,
            claim_ids=[claim['claim_id']], idempotency_key='smoke-batch-'+request_id)
    paid = {'pass':True, 'terminal_status':'succeeded',
            'settlement':'CLAIM_BATCHED_NOT_SETTLED_OR_BRIDGED',
            'gates':{'paid':True}, 'worker_account_id':org_account,
            'accounting':{'worker_account_id':org_account,'earning_id':earning_id},
            'request_id':request_id, 'session_id':session_id,
            'earned_credit_wei':str(CREDIT_WEI_PER_CREDIT)}
    return ledger, paid, org_wallet, org_account, batch


def test_disposable_recovery_settlement_credits_only_verified_pending_batch_once():
    from tools.slh_seh_lab.credit_recovery_acceptance import _settle_disposable_recovery_earning
    from main_computer.credit_units import CREDIT_WEI_PER_CREDIT
    ledger, paid, wallet, account, batch = _disposable_recovery_pending_earning()
    assert ledger.get_account(account) is None
    result = _settle_disposable_recovery_earning(ledger=ledger, paid=paid,
                                                  org_wallet_address=wallet)
    assert result['status'] == 'IN_MEMORY_LAB_LEDGER_SETTLED_NOT_BRIDGED'
    assert result['batch_id'] == batch['batch_id']
    assert result['verified_available_wei'] == str(CREDIT_WEI_PER_CREDIT)
    assert result['verified_earned_wei'] == str(CREDIT_WEI_PER_CREDIT)
    transactions = [x for x in ledger.store.load()['payout_transactions'].values()
                    if x['transaction_type'] == 'worker_settlement_wallet_credit']
    assert len(transactions) == 1


def test_disposable_recovery_settlement_refuses_mismatched_earning():
    from tools.slh_seh_lab.credit_recovery_acceptance import _settle_disposable_recovery_earning
    ledger, paid, wallet, account, _ = _disposable_recovery_pending_earning()
    paid['accounting']['earning_id'] = 'forged-earning'
    with unittest.TestCase().assertRaisesRegex(RuntimeError, 'lineage is incomplete'):
        _settle_disposable_recovery_earning(ledger=ledger, paid=paid,
                                            org_wallet_address=wallet)
    assert ledger.get_account(account) is None


def test_disposable_recovery_settlement_refuses_non_dev_hub():
    from tools.slh_seh_lab.credit_recovery_acceptance import _settle_disposable_recovery_earning
    ledger, paid, wallet, account, _ = _disposable_recovery_pending_earning()
    ledger.topology.network['network_kind'] = 'mainnet'
    with unittest.TestCase().assertRaisesRegex(RuntimeError, 'disposable in-memory dev Hub'):
        _settle_disposable_recovery_earning(ledger=ledger, paid=paid,
                                            org_wallet_address=wallet)
    assert ledger.get_account(account) is None



def test_disposable_private_worker_assignment_matches_durable_run_and_hash(tmp_path):
    import sqlite3
    from tools.slh_seh_lab.credit_recovery_acceptance import _verified_disposable_private_assignment
    root = tmp_path / 'mc-slh-two-service-lab-paid-test'
    state = root / 'state'
    state.mkdir(parents=True)
    with sqlite3.connect(state / 'private_requests.sqlite3') as conn:
        conn.execute('''CREATE TABLE private_requests (requester_device_id TEXT,
                        request_id TEXT,status TEXT,worker_device_id TEXT,
                        run_id TEXT,result_hash TEXT)''')
        conn.execute('INSERT INTO private_requests VALUES (?,?,?,?,?,?)',
                     ('requester', 'req', 'SUCCEEDED', 'real-selected-worker', 'run', 'hash'))
    public = {'request_id':'req', 'status':'SUCCEEDED', 'run_id':'run', 'result_hash':'hash'}
    assert 'worker_device_id' not in public
    assert _verified_disposable_private_assignment(root, requester='requester',
            request_id='req', public_result=public) == 'real-selected-worker'
    for corrupt in ({**public,'result_hash':'forged'},
                    {**public,'run_id':'forged'},
                    {**public,'status':'FAILED'},
                    {**public,'request_id':'wrong'}):
        assert _verified_disposable_private_assignment(root, requester='requester',
                request_id='req', public_result=corrupt) is None
    assert _verified_disposable_private_assignment(root, requester='wrong-requester',
            request_id='req', public_result=public) is None
    with unittest.TestCase().assertRaisesRegex(RuntimeError,'disposable lab root'):
        _verified_disposable_private_assignment(tmp_path, requester='requester',
                request_id='req', public_result=public)


if __name__ == '__main__':
    unittest.main()


def test_initial_worker_auth_retries_wrapped_windows_openssl_handshake_once():
    """The Windows SSL internal-error traceback is wrapped by websockets 16."""
    import hashlib
    import ssl
    from websockets.exceptions import ConnectionClosedError
    from tools.slh_seh_lab.service_lifecycle import _await_initial_worker_auth

    attempts = []
    class FakeSocket:
        def getpeercert(self, *, binary_form):
            assert binary_form
            return b'disposable-lab-cert'
    class FakeConnection:
        socket = FakeSocket()
        def __init__(self):
            self.receives = iter([
                json.dumps({'type': 'slh.auth.challenge', 'challenge': {'nonce': 'test'}}),
                json.dumps({'type': 'hub.auth.accepted', 'device_id': 'lab-worker'}),
                json.dumps({'type': 'hub.ping', 'ping_id': 'test-ping'}),
                json.dumps({'type': 'hub.pong.accepted'}),
            ])
            self.sent = []
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def recv(self, *, timeout): return next(self.receives)
        def send(self, frame): self.sent.append(json.loads(frame)['type'])

    def fake_connect(*args, **kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            try:
                raise ssl.SSLError('[SSL] internal error (_ssl.c:2427)')
            except ssl.SSLError as root:
                raise ConnectionClosedError(None, None) from root
        return FakeConnection()

    with mock.patch('websockets.sync.client.connect', side_effect=fake_connect), \
         mock.patch('tools.slh_seh_lab.service_lifecycle.sign_object', return_value='proof'):
        assert _await_initial_worker_auth(
            42001, 'lab-worker', object(),
            expected_pin=hashlib.sha256(b'disposable-lab-cert').hexdigest(), timeout=2)
    assert len(attempts) == 2
    assert attempts[0]['open_timeout'] == 5


def test_initial_worker_auth_does_not_retry_protocol_close_without_socket_cause():
    from websockets.exceptions import ConnectionClosedError
    from tools.slh_seh_lab.service_lifecycle import _await_initial_worker_auth
    with mock.patch('websockets.sync.client.connect',
                    side_effect=ConnectionClosedError(None, None)) as attempted:
        with unittest.TestCase().assertRaises(ConnectionClosedError):
            _await_initial_worker_auth(42001, 'worker', object(),
                                       expected_pin='pin', timeout=2)
    assert attempted.call_count == 1


def test_initial_worker_auth_never_retries_wrapped_post_handshake_auth_close():
    """No retry of socket closes raised during Worker authentication itself."""
    import hashlib
    import ssl
    from websockets.exceptions import ConnectionClosedError
    from tools.slh_seh_lab.service_lifecycle import _await_initial_worker_auth

    class FakeConnection:
        class Socket:
            def getpeercert(self, *, binary_form): return b'lab-cert'
        socket = Socket()
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def send(self, frame): pass
        def recv(self, *, timeout):
            try:
                raise ssl.SSLError('[SSL] internal error')
            except ssl.SSLError as root:
                raise ConnectionClosedError(None, None) from root

    with mock.patch('websockets.sync.client.connect', return_value=FakeConnection()) as attempted:
        with unittest.TestCase().assertRaises(ConnectionClosedError):
            _await_initial_worker_auth(42001, 'worker', object(),
                        expected_pin=hashlib.sha256(b'lab-cert').hexdigest(), timeout=2)
    assert attempted.call_count == 1


def test_initial_worker_auth_does_not_retry_permission_failure_wrapped_in_close():
    from websockets.exceptions import ConnectionClosedError
    from tools.slh_seh_lab.service_lifecycle import _await_initial_worker_auth
    def permission_denied(*args, **kwargs):
        try:
            raise PermissionError('access denied')
        except PermissionError as root:
            raise ConnectionClosedError(None, None) from root
    with mock.patch('websockets.sync.client.connect', side_effect=permission_denied) as attempted:
        with unittest.TestCase().assertRaises(ConnectionClosedError):
            _await_initial_worker_auth(42001, 'worker', object(), expected_pin='pin', timeout=2)
    assert attempted.call_count == 1


def test_seh_relay_positive_worker_auth_uses_bounded_transient_retry():
    from tools.slh_seh_lab.service_lifecycle import (
        _await_initial_worker_auth, TransientWorkerHandshakeError)
    with mock.patch('tools.slh_seh_lab.service_lifecycle._managed_auth',
                    side_effect=[TransientWorkerHandshakeError('TLS opening transport close'), True]) as auth:
        assert _await_initial_worker_auth(42002, 'lab-worker', object(),
                  expected_pin='pin', timeout=2, via_relay=True) is True
        assert auth.call_count == 2
        assert auth.call_args.kwargs['via_relay'] is True


def test_tls_handshake_retry_still_times_out_fail_closed_if_every_connect_fails():
    import ssl
    from websockets.exceptions import ConnectionClosedError
    from tools.slh_seh_lab.service_lifecycle import _await_initial_worker_auth, ServiceLabFailure

    def fails(*args, **kwargs):
        try:
            raise ssl.SSLError('[SSL] internal error')
        except ssl.SSLError as err:
            raise ConnectionClosedError(None, None) from err

    with mock.patch('websockets.sync.client.connect', side_effect=fails) as attempted:
        with unittest.TestCase().assertRaisesRegex(ServiceLabFailure,
                                                  'Timed out: initial managed Worker'):
            _await_initial_worker_auth(42002, 'lab-worker', object(),
                                       expected_pin='pin', timeout=0.02)
    assert attempted.call_count >= 1
