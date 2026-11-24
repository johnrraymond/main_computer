"""Deployment selection, launcher wiring, and real registered-host TLS checks."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from main_computer import onlyoffice_deployment as d
from tools import mcel_onlyoffice_deployment as tool


class DeploymentContracts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.work=Path(self.temp.name); self.state=self.work/'state'; self.state.mkdir()
        self.dev=self.work/'dev'; self.debug=self.work/'debug'; self.dev.mkdir(); self.debug.mkdir()
        for root in (self.dev,self.debug): (root/'probe.py').write_text('verified source')
        self.policy={'schema':1,'mode':'hardened','primary':'dev','roots':{'dev':str(self.dev),'debug':str(self.debug)},
                     'fingerprint':d.file_hashes(self.dev,['probe.py']),'image_tag':'latest','image_id':'sha256:abc'}
        self.write(self.policy)

    def write(self,policy):
        (self.state/d.POLICY_NAME).write_text(json.dumps(policy))

    def test_absent_policy_does_not_activate_and_rollback_resets_candidate(self):
        (self.state/d.POLICY_NAME).unlink()
        self.assertEqual(d.public_environment(self.state,self.dev),{})
        self.write({'schema':1,'mode':'legacy'})
        self.assertEqual(d.public_environment(self.state,self.dev)[d.MANAGED],'0')
        self.assertEqual(d.public_environment(self.state,self.dev)['MAIN_COMPUTER_HARDENED_CANDIDATE'],'0')

    def test_durable_per_lane_selection_with_one_auth_owner_and_no_secrets(self):
        a=d.public_environment(self.state,self.dev); b=d.public_environment(self.state,self.debug)
        self.assertEqual(a['MAIN_COMPUTER_AUTH_BOOTSTRAP_ENABLED'],'1')
        self.assertEqual(b['MAIN_COMPUTER_AUTH_BOOTSTRAP_ENABLED'],'0')
        self.assertEqual(b['MAIN_COMPUTER_ONLYOFFICE_CALLBACK_BASE_URL'],'http://host.docker.internal:28865')
        self.assertEqual(a['MAIN_COMPUTER_ONLYOFFICE_BROWSER_PUBLIC_URL'],b['MAIN_COMPUTER_ONLYOFFICE_BROWSER_PUBLIC_URL'])
        self.assertEqual(a['MAIN_COMPUTER_ONLYOFFICE_PROJECT'],b['MAIN_COMPUTER_ONLYOFFICE_PROJECT'])
        for env in (a,b):
            self.assertNotIn('MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET',env)
            self.assertNotIn('MAIN_COMPUTER_MAIN_LOG_TOKEN',env)
            self.assertEqual(env['MAIN_COMPUTER_ONLYOFFICE_JWT_ENABLED'],'true')
            self.assertEqual(env['MAIN_COMPUTER_ONLYOFFICE_ALLOW_META_IP_ADDRESS'],'false')

    def test_development_updates_do_not_require_promotion_but_release_stays_pinned(self):
        with self.assertRaisesRegex(RuntimeError,'UNREGISTERED'):d.public_environment(self.state,self.work)
        (self.dev/'probe.py').write_text('changed')
        env=d.public_environment(self.state,self.dev)
        self.assertEqual(env[d.MANAGED],'1')
        self.assertEqual(env['MAIN_COMPUTER_ONLYOFFICE_JWT_ENABLED'],'true')
        self.assertEqual(d.read_policy(self.state),self.policy)
        (self.debug/'probe.py').write_text('changed')
        with self.assertRaisesRegex(RuntimeError,'SOURCE_CHANGED'):d.public_environment(self.state,self.debug)
        self.assertEqual(d.public_environment(self.state,self.debug,check_source=False)[d.MANAGED],'1')

    def test_missing_dev_source_fails_closed(self):
        (self.dev/'probe.py').unlink()
        with self.assertRaises(FileNotFoundError):d.public_environment(self.state,self.dev)

    def test_runtime_receipt_tracks_current_source_per_lane(self):
        with patch.object(tool,'ALL_FILES',('probe.py',)):
            first=tool.runtime_source_fingerprints(self.policy)
            listeners={8765:10,8875:11,28865:12}
            report={'verdict':'PASS_NORMAL_STARTUP_REGISTERED_LANES', 'fingerprint':first['dev'],
                    'source_fingerprints':first,'roots':self.policy['roots'],
                    'listener_pids':{str(k):v for k,v in listeners.items()}}
            self.assertTrue(tool.runtime_receipt_matches(report,self.policy,first,listeners))
            (self.dev/'probe.py').write_text('new revision')
            current=tool.runtime_source_fingerprints(self.policy)
            self.assertNotEqual(current['dev'],first['dev'])
            self.assertFalse(tool.runtime_receipt_matches(report,self.policy,current,listeners))
            self.assertFalse(tool.runtime_receipt_matches(report,self.policy,first,{**listeners,8765:999}))
            self.assertFalse(tool.runtime_receipt_matches({**report,'listener_pids':{}},self.policy,first,listeners))
            historical={k:v for k,v in report.items() if k!='source_fingerprints'}
            self.assertFalse(tool.runtime_receipt_matches(historical,self.policy,first,listeners))

    def test_primary_owners_mutually_exclusive(self):
        self.policy['roots']['unleashed']=str(self.work/'unleashed');self.write(self.policy)
        with self.assertRaisesRegex(RuntimeError,'INVALID_DEPLOYMENT_LANE'):d.read_policy(self.state)

    def test_promotion_denied_without_explicit_authorization_or_source_evidence(self):
        opts=SimpleNamespace(authorize_cutover=False,primary='dev',debug_root=None,safe_root=None,tag='latest')
        with self.assertRaisesRegex(RuntimeError,'EXPLICIT_CUTOVER'):tool.promote(self.state,self.dev,opts)
        self.write({'schema':1,'mode':'legacy'});opts.authorize_cutover=True
        with patch.object(tool,'no_supervisors'),patch.object(tool,'source_evidence',side_effect=RuntimeError('SOURCE_MATCHED_LIVE_EVIDENCE_REQUIRED')):
            with self.assertRaisesRegex(RuntimeError,'SOURCE_MATCHED'):tool.promote(self.state,self.dev,opts)
        self.assertEqual(d.read_policy(self.state)['mode'],'legacy')

    def test_rollback_docker_failure_never_switches_to_legacy(self):
        opts=SimpleNamespace(authorize_rollback=True)
        with patch.object(tool,'no_supervisors'),patch.object(tool,'image_id',return_value='sha256:abc'),patch.object(tool,'compose',side_effect=RuntimeError('DOCKER_COMPOSE_FAILED')):
            with self.assertRaisesRegex(RuntimeError,'DOCKER_COMPOSE_FAILED'):tool.rollback(self.state,self.dev,opts)
        self.assertEqual(d.read_policy(self.state)['mode'],'hardened')

    def test_rollback_preserves_volumes_and_changes_selection_after_docker(self):
        calls=[]
        def compose(*args,**kwargs):
            calls.append((args[3],kwargs));self.assertEqual(d.read_policy(self.state)['mode'],'hardened')
        with patch.object(tool,'no_supervisors'),patch.object(tool,'image_id',return_value='sha256:abc'),patch.object(tool,'compose',side_effect=compose):
            tool.rollback(self.state,self.dev,SimpleNamespace(authorize_rollback=True))
        self.assertEqual(d.read_policy(self.state)['mode'],'legacy')
        self.assertEqual(calls[0][0],['stop','docs-https'])
        self.assertEqual(calls[1],(['up','-d','--pull','never','onlyoffice'],{'hardened':False}))

    def test_secondary_never_recreates_shared_docker(self):
        with patch.object(tool,'process_listeners',return_value={}),patch.object(tool,'docs_ready') as ready,patch.object(tool,'compose') as compose:
            tool.configure_docker(self.state,self.debug,'start'); ready.assert_called_once();compose.assert_not_called()
        with self.assertRaisesRegex(RuntimeError,'SHARED_DOCUMENTSERVER_STOP'):tool.configure_docker(self.state,self.debug,'stop')

    def test_lane_collision_refused_before_docker(self):
        with patch.object(tool,'process_listeners',return_value={8765:123}),patch.object(tool,'compose') as compose:
            with self.assertRaisesRegex(RuntimeError,'LANE_LISTENERS_NOT_RELEASED'):tool.configure_docker(self.state,self.dev,'start')
        compose.assert_not_called()

    def test_normal_launcher_uses_hardened_docker_before_supervisor_and_secrets_not_manifest(self):
        root=tool.ROOT
        ps=(root/'scripts/main-computer-start-stop.ps1').read_text(encoding='utf-8-sig')
        start=ps[ps.index('function Start-MainComputer('):ps.index('function Start-MainComputerDevHubOnly(')]
        self.assertLess(start.index('if ($hardened) { Invoke-MainComputerOnlyOfficeControl'),start.index('$process = Start-Process'))
        self.assertIn('MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET"] = ""',ps)
        self.assertIn('LANE_LISTENERS_NOT_RELEASED', (root/'tools/mcel_onlyoffice_deployment.py').read_text())
        self.assertIn('apply_runtime_environment(Path(args.root), include_jwt=(command == "run"))',(root/'main_computer/app_control.py').read_text())


class RegisteredTLS(unittest.TestCase):
    def test_real_tls_registered_host_and_resumable_sso(self):
        code=r'''
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from main_computer.auth_bootstrap import BootstrapAuthority, BootstrapServer, generate_tls_certificate, provision
from main_computer.auth_bootstrap_service_deployment import RegisteredHandler
from tools.mcel_onlyoffice_cutover import running, request, auth_cookie, DEV, DEBUG
with tempfile.TemporaryDirectory() as t:
    state=Path(t)/'auth';proof=provision(state);cert,key,_=generate_tls_certificate(state);authority=BootstrapAuthority(state)
    with running(BootstrapServer(('127.0.0.1',0),authority,cert=cert,key=key,isolated_backend_ports={'dev':8765,'debug':28865})) as server:
        server.RequestHandlerClass=RegisteredHandler;n=server.server_port
        assert request('safe.main-computer.localhost',n,cert,'/api/auth/bootstrap/session')[0]==403
        assert request('unleashed.main-computer.localhost',n,cert,'/api/auth/bootstrap/session')[0]==403
        assert request(DEV,n,cert,'/auth/handoff/start?target=safe.main-computer.localhost')[0]==403
        path='/auth/deployment/handoff/debug'
        status,headers,_=request(DEV,n,cert,path);assert status==303 and headers['Location'].startswith('/auth/login?next=')
        cookie=auth_cookie(server,proof,cert,host=DEV)
        status,headers,_=request(DEV,n,cert,path,cookie=cookie);assert status==303 and headers['Location']=='/auth/handoff/start?target='+DEBUG
        status,headers,_=request(DEV,n,cert,headers['Location'],cookie=cookie);assert status==303
        redeem=urlsplit(headers['Location']);path=redeem.path+'?'+redeem.query
        status,headers,_=request(DEBUG,n,cert,path);assert status==303 and headers['Set-Cookie'].split(';')[0]==cookie
        assert request(DEBUG,n,cert,path)[0]==401
        print('REGISTERED_REAL_TLS_PASS')
'''
        env={**os.environ,'MAIN_COMPUTER_HARDENED_CANDIDATE':'1','MAIN_COMPUTER_PRIMARY_OWNER':'dev','MAIN_COMPUTER_INSTALLATION_MODE':'dev'}
        r=subprocess.run([sys.executable,'-c',code],cwd=tool.ROOT,env=env,capture_output=True,text=True,timeout=600 if os.name=='nt' else 60)
        self.assertEqual(r.returncode,0,r.stdout+'\n'+r.stderr)
        self.assertIn('REGISTERED_REAL_TLS_PASS',r.stdout)

if __name__=='__main__':unittest.main()
