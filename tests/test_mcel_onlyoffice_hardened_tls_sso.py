"""Real ephemeral TLS: canonical SANs, one-use cross-host handoff, docs isolation."""
from __future__ import annotations

import os
import time
from pathlib import Path
import subprocess
import sys
import unittest


SCRIPT = r'''
import base64, hashlib, hmac, http.client, json, os, socket, ssl, tempfile, threading
from pathlib import Path
from cryptography import x509
from main_computer.auth_bootstrap import BootstrapAuthority, BootstrapServer, generate_tls_certificate, provision, HOST
from main_computer.auth_hostnames import HOSTS
print('MC_OO_TLS_SSO_STAGE import-complete', flush=True)
assert HOST == 'dev.main-computer.localhost', HOST
with tempfile.TemporaryDirectory(prefix='mc-candidate-sso-') as dirname:
    state = Path(dirname)
    proof = provision(state)
    print('MC_OO_TLS_SSO_STAGE proof-provisioned', flush=True)
    cert, key, thumb = generate_tls_certificate(state)
    print('MC_OO_TLS_SSO_STAGE certificate-generated', flush=True)
    leaf = x509.load_pem_x509_certificate(cert.read_bytes())
    names = set(leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName))
    assert names == set(HOSTS.values()), names
    assert not leaf.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    with BootstrapServer(('127.0.0.1', 0), BootstrapAuthority(state), cert=cert, key=key) as server:
        # Test-only: wait for handlers to release native Windows proof locks
        # before TemporaryDirectory attempts to delete disposable state.
        server.daemon_threads = False
        runner = threading.Thread(target=server.serve_forever, daemon=True)
        runner.start()
        print('MC_OO_TLS_SSO_STAGE tls-listener-ready', flush=True)
        port = server.server_port
        # Native Windows proof/ACL reconciliation can exceed five seconds.
        # Keep a bounded per-request budget independent of the child deadline.
        request_timeout = 60 if os.name == 'nt' else 5
        class TLS(http.client.HTTPSConnection):
            def __init__(self, host):
                super().__init__(host, port, context=ssl.create_default_context(cafile=str(cert)), timeout=request_timeout)
            def connect(self):
                sock = socket.create_connection(('127.0.0.1', port), timeout=request_timeout)
                self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        def request(host, method, path, body=None, cookie=None):
            # Never print query strings: handoff URLs contain bearer tickets.
            endpoint = path.split('?', 1)[0]
            print(f'MC_OO_TLS_SSO_REQUEST host={host} method={method} endpoint={endpoint}', flush=True)
            conn = TLS(host)
            headers = {'Host':f'{host}:{port}'}
            if body is not None:
                headers['Content-Type']='application/json'
                headers['Origin']=f'https://{host}:{port}'
            if cookie:headers['Cookie']=cookie
            try:
                conn.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=headers)
                response = conn.getresponse()
                return response.status, dict(response.getheaders()), response.read()
            except TimeoutError as exc:
                raise AssertionError(
                    f'TLS_SSO_REQUEST_TIMEOUT host={host} method={method} '
                    f'endpoint={endpoint} limit_seconds={request_timeout}'
                ) from exc
            finally:
                conn.close()
        try:
            result = request(HOST, 'GET', '/api/auth/bootstrap/challenge')
            assert result[0] == 200
            challenge = json.loads(result[2])
            secret = base64.urlsafe_b64decode(json.loads(proof.read_text())['key_b64']+'=')
            digest = hmac.new(secret, BootstrapAuthority.message(challenge['challenge_id'], challenge['nonce']), hashlib.sha256).digest()
            signature = base64.urlsafe_b64encode(digest).decode().rstrip('=')
            result = request(HOST, 'POST', '/api/auth/bootstrap/verify', {'challenge_id':challenge['challenge_id'], 'signature':signature})
            assert result[0] == 200
            first = result[1]['Set-Cookie'].split(';')[0]
            print('MC_OO_TLS_SSO_STAGE authenticated-primary', flush=True)
            result = request(HOST, 'GET', '/auth/handoff/start?target=debug.main-computer.localhost',cookie=first)
            assert result[0] == 303
            link = result[1]['Location']
            assert link.startswith(f'https://debug.main-computer.localhost:{port}/auth/handoff/redeem?ticket=')
            ticket = link.split('ticket=',1)[1]
            result = request('debug.main-computer.localhost','GET','/auth/handoff/redeem?ticket='+ticket)
            assert result[0] == 303
            second = result[1]['Set-Cookie'].split(';')[0]
            print('MC_OO_TLS_SSO_STAGE redeemed-debug', flush=True)
            assert first == second
            result = request('debug.main-computer.localhost','GET','/api/auth/bootstrap/session',cookie=second)
            assert result[0] == 200 and json.loads(result[2])['authenticated']
            assert request('debug.main-computer.localhost','GET','/auth/handoff/redeem?ticket='+ticket)[0] == 401
            assert request('onlyoffice.main-computer.localhost','GET','/api/auth/bootstrap/session')[0] == 403
            assert request('unleashed.main-computer.localhost','GET','/api/auth/bootstrap/session')[0] == 403
            print('MC_OO_TLS_SSO_STAGE replay-and-origin-denied', flush=True)
            assert request(HOST,'GET','/auth/handoff/start?target=onlyoffice.main-computer.localhost',cookie=first)[0] == 403
            server.authority.sessions.revoke(first.split('=',1)[1])
            result = request('debug.main-computer.localhost','GET','/api/auth/bootstrap/session',cookie=second)
            assert result[0] == 200 and not json.loads(result[2])['authenticated']
            print('MC_OO_TLS_SSO_STAGE revocation-confirmed', flush=True)
        finally:
            server.shutdown()
            runner.join(timeout=4)
print('HARDENED_TLS_SSO_SAN_HANDOFF_REPLAY_REVOCATION PASS')
'''


class TLSAndSSOTests(unittest.TestCase):
    def test_ephemeral_real_tls_cross_host_session(self):
        env=dict(os.environ, MAIN_COMPUTER_HARDENED_CANDIDATE='1', MAIN_COMPUTER_PRIMARY_OWNER='dev')
        # On Windows, real per-file DACL hardening launches PowerShell and
        # icacls repeatedly while provisioning disposable TLS/session state.
        # The inner HTTP requests also need Windows-appropriate timeouts.
        # Keep every real Windows permission check and a bounded test deadline.
        timeout_seconds = 660 if os.name == 'nt' else 30
        started = time.monotonic()
        try:
            result = subprocess.run([sys.executable, '-c', SCRIPT], env=env,
                capture_output=True, text=True, timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode(errors='replace') if isinstance(exc.stdout, bytes) else (exc.stdout or '')
            stderr = exc.stderr.decode(errors='replace') if isinstance(exc.stderr, bytes) else (exc.stderr or '')
            stages = [line for line in stdout.splitlines() if line.startswith('MC_OO_TLS_SSO_STAGE ')]
            last_stage = stages[-1] if stages else 'no-stage-reached'
            self.fail(f'TLS_SSO_CHILD_TIMEOUT seconds={timeout_seconds} stage={last_stage} '
                      f'elapsed={time.monotonic()-started:.1f}\n{stdout}\n{stderr}')
        self.assertEqual(result.returncode, 0,
                         msg=f'TLS_SSO_CHILD_EXIT={result.returncode}\n'+result.stdout+'\n'+result.stderr)
        self.assertIn('HARDENED_TLS_SSO_SAN_HANDOFF_REPLAY_REVOCATION PASS',result.stdout)


if __name__=='__main__':unittest.main()
