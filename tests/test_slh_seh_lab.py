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
                    True,'ws://example.com:8080','org','slh',False,2))
            with self.assertRaises(ValueError):
                write_seh_settings(state,SEHConnectionSettings(
                    True,'ws://127.0.0.1:18080','org','slh',False,2))
            self.assertIsNone(read_seh_settings(state))
            disabled=SEHConnectionSettings(False,'ws://127.0.0.1:18080',
                                            'org','slh',True,0.2)
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
                     report_dir=Path('runtime/reports'),org='org',slh='slh',
                     port=18080,compose_output=Path('runtime/reports/compose.yml'))
        deploy.assert_called_once()
        self.assertTrue(deploy.call_args.kwargs['build'])
        self.assertEqual(smoke.call_args.kwargs['deployed']['org'],'org')
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
                     report_dir=Path('runtime/reports'),org='org',slh='slh',
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
                     report_dir=Path('runtime/reports'),org='org',slh='slh',
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
                     report_dir=Path('runtime/reports'),org='org',slh='slh',
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
                     report_dir=Path('runtime/reports'),org='org',slh='slh',
                     port=18080,compose_output=Path('runtime/reports/compose.yml'))
            smoke.assert_not_called()

    def test_paid_work_through_isolated_channel_while_primary_remains_connected(self):
        from tools.slh_seh_lab.run_lab import _in_memory_hub, _local_demo
        from main_computer.slh.authenticator import generate_identity
        from main_computer.seh.relay import SEHRelay
        from main_computer.slh.seh_transport import SLHSEHConnector
        signer, public = generate_identity()
        relay = SEHRelay(organization_id='lab-organization',slh_id='my-slh',
                          slh_public_key=public,host='127.0.0.1',port=0).start()
        url=f'ws://127.0.0.1:{relay.port}'
        primary=SLHSEHConnector(seh_url=url,organization_id='lab-organization',
                        slh_id='my-slh',identity_private_key=signer,
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
        self.assertTrue(paid['gates']['wallet_authorized_settlement'])


if __name__ == '__main__':
    unittest.main()
