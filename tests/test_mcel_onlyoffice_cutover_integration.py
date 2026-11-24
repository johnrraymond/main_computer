from pathlib import Path
import os
from types import SimpleNamespace
import subprocess
import sys
import unittest
from unittest.mock import patch, MagicMock
from main_computer.onlyoffice_secure_runtime import OnlyOfficeDenied, fetch_edited_document

ROOT=Path(__file__).resolve().parents[1]

class CacheTransportTests(unittest.TestCase):
    def setUp(self):
        self.handler=SimpleNamespace(server=SimpleNamespace(config=SimpleNamespace(onlyoffice_mode='docker')),
            _onlyoffice_internal_url=lambda:'http://127.0.0.1:18085',
            _onlyoffice_browser_document_server_url=lambda:'https://onlyoffice.main-computer.localhost:18086')
    def test_public_forwarded_and_docker_cache_urls_use_fixed_transport(self):
        for url in ('https://onlyoffice.main-computer.localhost:18086/cache/files/id/output.xlsx?md5=abc', 'http://onlyoffice/cache/files/id/output.xlsx?md5=abc'):
            with self.subTest(url=url), patch('main_computer.onlyoffice_secure_runtime.http.client.HTTPConnection') as connect:
                c=connect.return_value;res=c.getresponse.return_value;res.status=200;res.getheader.return_value='0';res.read.return_value=b'bytes'
                self.assertEqual(fetch_edited_document(self.handler,url),b'bytes')
                connect.assert_called_once_with('127.0.0.1',18085,timeout=15)
                self.assertEqual(c.request.call_args.args,('GET','/cache/files/id/output.xlsx?md5=abc'))
    def test_wrong_origin_port_or_path_is_denied_before_network(self):
        urls=('https://onlyoffice.main-computer.localhost:18087/cache/files/x',
              'https://onlyoffice.main-computer.localhost.evil:18086/cache/files/x',
              'http://onlyoffice:8080/cache/files/x',
              'http://onlyoffice/cache/files/../secret','http://onlyoffice/cache/files/%2e%2e/secret',
              'http://onlyoffice/cache/files/%252e%252e/secret', 'http://onlyoffice/cache/files/%5csecret',
              'https://user@onlyoffice.main-computer.localhost:18086/cache/files/x',
              'http://127.0.0.1:18085/cache/not-files/x')
        with patch('main_computer.onlyoffice_secure_runtime.http.client.HTTPConnection') as connect:
            for url in urls:
                with self.subTest(url=url), self.assertRaises(OnlyOfficeDenied):fetch_edited_document(self.handler,url)
            connect.assert_not_called()
    def test_redirect_never_followed(self):
        with patch('main_computer.onlyoffice_secure_runtime.http.client.HTTPConnection') as connect:
            c=connect.return_value;c.getresponse.return_value.status=302
            with self.assertRaises(OnlyOfficeDenied):fetch_edited_document(self.handler,'http://onlyoffice/cache/files/a')
            c.request.assert_called_once()

class GatewayLiveRegression(unittest.TestCase):
    def test_real_authenticated_proxy_dispatch(self):
        # Exercises the former uninitialized-path regression with actual TLS,
        # auth proof, two ephemeral lane listeners, and signed backend ingress.
        code='from pathlib import Path; import tempfile; from tools.mcel_onlyoffice_cutover import gateway; t=tempfile.TemporaryDirectory(); gateway(Path(t.name)); t.cleanup()'
        env={**os.environ,'MAIN_COMPUTER_HARDENED_CANDIDATE':'1','MAIN_COMPUTER_PRIMARY_OWNER':'dev','MAIN_COMPUTER_INSTALLATION_MODE':'dev'}
        result=subprocess.run([sys.executable,'-c',code],cwd=ROOT,env=env,capture_output=True,text=True,timeout=720 if os.name=='nt' else 60)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertIn('authenticated-real-gateway-post PASS',result.stdout)
        self.assertIn('debug-routes-to-distinct-backend PASS',result.stdout)
        self.assertIn('revocation-blocks-backend PASS',result.stdout)

if __name__=='__main__':unittest.main()
