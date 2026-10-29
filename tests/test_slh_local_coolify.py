import base64
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from main_computer.slh.authenticator import generate_identity
from tools.slh_seh_lab.local_coolify import compose, prepare, LocalCoolifyError

class TestLocalCoolify(TestCase):
    def setUp(self):
        _, public = generate_identity()
        self.key = base64.b64encode(public.encode('ascii')).decode('ascii')

    def test_no_private_key_or_api_secret(self):
        value = compose(org='my-company', slh='my-slh',public_b64=self.key,
                        image='main-computer-seh:local',port=18080)
        self.assertIn('127.0.0.1:18080:8080',value)
        self.assertIn('pull_policy: never',value)
        self.assertIn('read_only: true',value)
        self.assertIn(self.key,value)
        self.assertNotIn('PRIVATE KEY',value)
        self.assertNotIn('SLH_COOLIFY_TOKEN',value)

    def test_injection_refused(self):
        for org in ('','bad\nprivileged: true','foo:bar'):
            with self.subTest(org=org),self.assertRaises(LocalCoolifyError):
                compose(org=org,slh='my-slh',public_b64=self.key,
                        image='main-computer-seh:local',port=18080)

    def test_prepare_no_docker_api_calls(self):
        with TemporaryDirectory() as temp:
            base=Path(temp)
            (base/'deploy/seh').mkdir(parents=True)
            (base/'deploy/seh/Dockerfile').write_text('FROM scratch')
            with patch('tools.slh_seh_lab.local_coolify.service_identity',return_value=(None,self.key)):
                with patch('tools.slh_seh_lab.local_coolify.subprocess.run',side_effect=AssertionError('not building')):
                    res=prepare(root=base,output=base/'compose.yml')
            self.assertEqual(res['status'],'PREPARED_NOT_DEPLOYED')
            self.assertTrue((base/'compose.yml').is_file())

    def test_wrong_docker_engine_fails_before_build(self):
        with TemporaryDirectory() as temp:
            base=Path(temp)
            (base/'deploy/seh').mkdir(parents=True)
            (base/'deploy/seh/Dockerfile').write_text('FROM scratch')
            class Process:
                returncode=0
                stdout=b'other-service\n'
                stderr=b''
            with patch('tools.slh_seh_lab.local_coolify.service_identity',return_value=(None,self.key)):
                with patch('tools.slh_seh_lab.local_coolify.subprocess.run',return_value=Process()) as run:
                    with self.assertRaises(LocalCoolifyError):
                        prepare(root=base,output=base/'compose.yml',build=True)
                    self.assertEqual(run.call_count,1)
            self.assertFalse((base/'compose.yml').exists())

    def test_windows_non_utf8_docker_output_does_not_crash_or_fake_success(self):
        # The actual failure was an undecodable cp1252 byte in Docker output.
        # Capturing subprocess bytes avoids Windows reader-thread decode errors.
        with TemporaryDirectory() as temp:
            base = Path(temp)
            (base/'deploy/seh').mkdir(parents=True)
            (base/'deploy/seh/Dockerfile').write_text('FROM scratch')
            class Process:
                def __init__(self, output):
                    self.returncode = 0
                    self.stdout = output
                    self.stderr = b''
            cases = [
                Process(b'coolify\n'),
                Process(b'#0 build output \x81 \xf0\x9f\x93\xa6\n'),
                Process(b'sha256:' + b'a'*64 + b'\n'),
            ]
            with patch('tools.slh_seh_lab.local_coolify.service_identity',return_value=(None,self.key)):
                with patch('tools.slh_seh_lab.local_coolify.subprocess.run',side_effect=cases) as run:
                    result = prepare(root=base,output=base/'compose.yml',build=True)
            self.assertEqual(result['image_status'], 'BUILT_ON_COOLIFY_DOCKER_ENGINE')
            self.assertTrue((base/'compose.yml').exists())
            self.assertEqual(run.call_count, 3)
            for call in run.call_args_list:
                self.assertNotIn('text', call.kwargs)
                self.assertNotIn('encoding', call.kwargs)

    def test_missing_inspected_image_id_does_not_claim_build(self):
        with TemporaryDirectory() as temp:
            base = Path(temp)
            (base/'deploy/seh').mkdir(parents=True)
            (base/'deploy/seh/Dockerfile').write_text('FROM scratch')
            class Process:
                returncode = 0
                stderr = b''
                def __init__(self, output):
                    self.stdout = output
            cases = [Process(b'coolify\n'),Process(b'build ok\n'),Process(b'\x81invalid-image-id\n')]
            with patch('tools.slh_seh_lab.local_coolify.service_identity',return_value=(None,self.key)):
                with patch('tools.slh_seh_lab.local_coolify.subprocess.run',side_effect=cases):
                    with self.assertRaisesRegex(LocalCoolifyError,'valid image ID'):
                        prepare(root=base,output=base/'compose.yml',build=True)
            self.assertFalse((base/'compose.yml').exists())

class TestSLHLocalDeploymentConfig(TestCase):
    def test_successful_auto_deploy_configures_future_slh_start_without_keys(self):
        """A verified Coolify deployment also enables gateway-owned SEH connectivity."""
        import io
        from contextlib import redirect_stdout
        from main_computer.slh.seh_autoconnect import read_seh_settings
        from tools.slh_seh_lab.run_lab import main
        with TemporaryDirectory() as temp:
            result={'status':'DEPLOYED_VERIFIED','image_status':'EXISTING_IMAGE_REUSED',
                    'compose':str(Path(temp)/'compose.yml'),
                    'service_name':'main-computer-seh-my-company-my-slh',
                    'service_uuid':'test-service', 'deployment_uuid':'test-deploy',
                    'health_url':'http://127.0.0.1:18080/healthz'}
            with patch('tools.slh_seh_lab.local_coolify.deploy_local',return_value=result), \
                 patch('tools.slh_seh_connector.default_state',return_value=Path(temp)), \
                 redirect_stdout(io.StringIO()):
                rc=main(['deploy-local-coolify','--reuse-image',
                         '--org-id','my-company','--slh-id','my-slh'])
            self.assertEqual(rc,0)
            settings=read_seh_settings(Path(temp))
            self.assertTrue(settings.enabled)
            self.assertEqual(settings.seh_url,'ws://127.0.0.1:18080')
            self.assertEqual(settings.organization_id,'my-company')
            self.assertEqual(settings.slh_id,'my-slh')
            self.assertTrue(settings.allow_loopback_lab)
            self.assertNotIn('private_key',(Path(temp)/'seh-connection.json').read_text())


class TestExistingCoolifyDiscovery(TestCase):
    def test_existing_seh_requires_capability_and_actual_coolify_service(self):
        from tools.slh_seh_lab.local_coolify import find_healthy_deployed
        from unittest.mock import Mock
        from io import BytesIO
        import json
        class Response:
            def __init__(self, payload, status=200):
                self.status=status
                self.payload=payload
            def read(self, n):return json.dumps(self.payload).encode()[:n]
        class Conn:
            response=None
            def __init__(self, *args, **kwargs):pass
            def request(self, *args):pass
            def getresponse(self):return self.response
            def close(self):pass
        api=Mock()
        api.dashboard_url.return_value='http://127.0.0.1:8000'
        api.ensure_api_token.return_value=(True,'token-ok','PRIVATE_TEST_TOKEN')
        api.find_service_uuid_via_api.return_value=(True,'found','uuid-from-coolify')
        with patch('http.client.HTTPConnection',Conn):
            Conn.response=Response({'service':'unrelated','isolated_lab_channels':True})
            self.assertIsNone(find_healthy_deployed(org='org',slh='slh',port=18080,adapter=api))
            api.ensure_api_token.assert_not_called()
            Conn.response=Response({'service':'main_computer.seh','isolated_lab_channels':True})
            result=find_healthy_deployed(org='org',slh='slh',port=18080,adapter=api)
        self.assertEqual(result['service_uuid'],'uuid-from-coolify')
        self.assertTrue(result['existing_service_reused'])
        self.assertNotIn('PRIVATE_TEST_TOKEN',str(result))
        api.find_service_uuid_via_api.assert_called_once()

class TestAutoDeployLocalCoolify(TestCase):
    """Website Builder local Coolify helper is the only deployment authority."""

    def _fake(self):
        from unittest.mock import Mock
        m = Mock()
        m.dashboard_url.return_value = 'http://127.0.0.1:8000'
        m.ensure_infra_status.return_value = (True, 'local infra ok')
        m.ensure_api_token.return_value = (True, 'local token ready', 'SENSITIVE_LOCAL_TOKEN')
        m.local_deploy_target_from_db.return_value = (True, 'target', {'server_uuid':'srv','destination_uuid':'dst'})
        m.find_local_project_uuid_via_api.return_value = (True, 'project', 'project-uuid')
        m.ensure_project_environment_via_api_or_db.return_value = (True, 'environment')
        m.ensure_docker_compose_service_via_api.return_value = (True, 'reconciled', 'service-uuid')
        m.trigger_smoke_service_deploy_via_api.return_value = (True, 'queued', 'deployment-uuid')
        m.drain_local_coolify_deployment_queue.return_value = (True, 'queue drained')
        return m

    def test_one_command_builds_reconciles_deploys_and_verifies(self):
        from tools.slh_seh_lab.local_coolify import deploy_local
        with TemporaryDirectory() as t:
            path = Path(t)/'compose.yml'
            path.write_text('services:\n  seh:\n    image: main-computer-seh:local\n')
            fake=self._fake()
            with patch('tools.slh_seh_lab.local_coolify.prepare',return_value={
                'status':'PREPARED_NOT_DEPLOYED','compose':str(path), 'health_url':'http://127.0.0.1:18080/healthz',
                'image_status':'BUILT_ON_COOLIFY_DOCKER_ENGINE'}) as build:
                result=deploy_local(org='my-company',slh='my-slh',root=Path(t),
                                    coolify=fake,health_probe=lambda url: url.endswith('/healthz'))
            self.assertEqual(result['status'],'DEPLOYED_VERIFIED')
            self.assertFalse(result['coolify_api_key_required_from_user'])
            self.assertNotIn('SENSITIVE_LOCAL_TOKEN',str(result))
            build.assert_called_once()
            self.assertTrue(build.call_args.kwargs['build'])
            fake.ensure_docker_compose_service_via_api.assert_called_once()
            self.assertEqual(fake.ensure_docker_compose_service_via_api.call_args.kwargs['service_name'],
                             'main-computer-seh-my-company-my-slh')
            fake.trigger_smoke_service_deploy_via_api.assert_called_once_with(
                Path(t).resolve(),'SENSITIVE_LOCAL_TOKEN','service-uuid')
            fake.drain_local_coolify_deployment_queue.assert_called_once()

    def test_never_targets_remote_coolify(self):
        from tools.slh_seh_lab.local_coolify import deploy_local
        with TemporaryDirectory() as t:
            path=Path(t)/'compose.yml'
            path.write_text('services:\n  seh:\n    image: main-computer-seh:local\n')
            fake=self._fake()
            fake.dashboard_url.return_value='https://coolify.example.com'
            with patch('tools.slh_seh_lab.local_coolify.prepare',return_value={
                'compose':str(path),'health_url':'http://127.0.0.1:18080/healthz'}):
                with self.assertRaisesRegex(LocalCoolifyError,'non-loopback'):
                    deploy_local(root=Path(t),coolify=fake,health_probe=lambda u: True)
            fake.ensure_api_token.assert_not_called()

    def test_failed_queue_never_reports_deployed(self):
        from tools.slh_seh_lab.local_coolify import deploy_local
        with TemporaryDirectory() as t:
            path=Path(t)/'compose.yml'
            path.write_text('services:\n  seh:\n    image: main-computer-seh:local\n')
            fake=self._fake()
            fake.drain_local_coolify_deployment_queue.return_value=(False,'queue failed')
            with patch('tools.slh_seh_lab.local_coolify.prepare',return_value={
                'compose':str(path),'health_url':'http://127.0.0.1:18080/healthz'}):
                with self.assertRaisesRegex(LocalCoolifyError,'deploy queue failed'):
                    deploy_local(root=Path(t),coolify=fake,health_probe=lambda u: True)

    def test_health_requires_seh_identity(self):
        from tools.slh_seh_lab.local_coolify import _verify_seh_health
        from unittest.mock import Mock
        from io import BytesIO
        class Resp:
            status=200
            def __init__(self, content): self.content=content
            def __enter__(self): return self
            def __exit__(self,*args): return False
            def read(self,n): return self.content[:n]
        with patch('urllib.request.urlopen',return_value=Resp(b'{"service":"unrelated","live":true}')):
            self.assertFalse(_verify_seh_health('http://127.0.0.1:18080/healthz'))
        with patch('urllib.request.urlopen',return_value=Resp(b'{"service":"main_computer.seh","live":true}')):
            self.assertTrue(_verify_seh_health('http://127.0.0.1:18080/healthz'))
