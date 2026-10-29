"""Canonical port, real-loopback HTTP bootstrap and persistent passphrase-free authenticator gates."""
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from wsgiref.simple_server import make_server

from main_computer.slh.api import SLHAPI
from main_computer.slh.authority import SLHAuthority
from main_computer.slh.ports import DEFAULT_SLH_API_HOST, DEFAULT_SLH_API_PORT, DEFAULT_SLH_API_ORIGIN
from tools.slh_authenticator_cli import save_identity, load_identity, bootstrap_admin, login
from main_computer.slh.authenticator import public_key_fingerprint


class TestSLHCanonicalPortAndCLI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def test_default_port_is_distinct_from_existing_service_and_openapi(self):
        self.assertEqual(DEFAULT_SLH_API_PORT, 8897)
        self.assertEqual(DEFAULT_SLH_API_ORIGIN, 'http://127.0.0.1:8897')
        self.assertNotEqual(DEFAULT_SLH_API_PORT, 8767)  # Main Log/OpenClaw
        api = SLHAPI(SLHAuthority(self.directory/'state'))
        self.assertEqual(api.openapi()['servers'][0]['url'], DEFAULT_SLH_API_ORIGIN)

    def test_unencrypted_key_never_overwrites_and_persists(self):
        identities = self.directory/'authenticators'
        saved = save_identity(identities, 'admin-pc')
        raw = (identities/'admin-pc.key.pem').read_bytes()
        self.assertIn(b'BEGIN PRIVATE KEY',raw)
        self.assertNotIn(b'BEGIN EC PRIVATE KEY',raw)
        self.assertFalse(saved['encrypted'])
        private, public = load_identity(identities,'admin-pc')
        self.assertEqual(saved['fingerprint'],public_key_fingerprint(public))
        self.assertIn('BEGIN PUBLIC KEY',public)
        with self.assertRaises(FileExistsError):
            save_identity(identities,'admin-pc')
        self.assertEqual((identities/'admin-pc.key.pem').read_bytes(),raw)
        self.assertIsNotNone(private)

    def test_real_http_bootstrap_and_login_with_persistent_identity(self):
        state = self.directory/'state'
        identities = self.directory/'identities'
        save_identity(identities, 'admin-pc')
        authority = SLHAuthority(state)
        server = make_server('127.0.0.1',0,SLHAPI(authority).wsgi_app)
        port = server.server_port
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            result = bootstrap_admin(port=port,state_dir=state,identity_dir=identities,device_id='admin-pc',
                org_id='test-org',admin_id='alice',admin_name='Alice Admin')
            self.assertEqual(result['status'],'INITIALIZED')
            self.assertFalse((state/'bootstrap.secret').exists())
            result = login(port=port,identity_dir=identities,device_id='admin-pc')
            self.assertTrue(result['authenticated'])
            self.assertEqual(result['role'],'admin')
            self.assertEqual(result['session'],'validated_and_logged_out')
            # Restarting the authority does not lose registered identity or stored key.
            restarted = SLHAuthority(state)
            self.assertTrue(restarted.initialized())
            self.assertEqual(restarted.devices()[0]['id'],'admin-pc')
            _, public = load_identity(identities,'admin-pc')
            self.assertEqual(restarted.devices()[0]['fingerprint'],public_key_fingerprint(public))
        finally:
            server.shutdown()
            thread.join(timeout=4)
            server.server_close()

    def test_occupied_port_refuses_to_claim_listener_ready(self):
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as occupied:
            occupied.bind(('127.0.0.1',0))
            occupied.listen(1)
            port = occupied.getsockname()[1]
            proc = subprocess.run([sys.executable,'-m','tools.slh_auth_harness','serve',
                '--state-dir',str(self.directory/'state'),'--port',str(port)],
                capture_output=True,text=True,timeout=12)
            self.assertEqual(proc.returncode,2,proc.stdout+proc.stderr)
            self.assertNotIn('API listening at',proc.stdout)
            self.assertIn('SLH_LISTENER_UNAVAILABLE',proc.stdout)
            self.assertIn(str(port),proc.stdout)


if __name__ == '__main__':
    unittest.main()
