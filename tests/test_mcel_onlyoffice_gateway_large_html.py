"""Regression: the real authenticated gateway must carry native MCEL legacy HTML."""
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class GatewayLargeHTML(unittest.TestCase):
    def test_real_tls_large_application_and_bounded_non_html(self):
        code = r'''
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from main_computer.auth_bootstrap import BootstrapAuthority, BootstrapServer, generate_tls_certificate, provision
from main_computer.auth_bootstrap_service_deployment import RegisteredHandler
from main_computer.hardened_ingress import AssertionVerifier, admit
from tools.mcel_onlyoffice_cutover import ProbeHandler, running, request, auth_cookie, DEV

HTML = b'<html><body><div id="onlyoffice-app">' + b'x' * 10_000_000 + b'</div></body></html>'
class App(ProbeHandler):
    def do_GET(self):
        if not admit(self, 'GET'): return
        path = urlsplit(self.path).path
        kind = 'text/html; charset=utf-8' if path != '/applications/binary' else 'application/octet-stream'
        body = HTML if path != '/applications/oversized' else b'x' * (32 * 1024 * 1024 + 1)
        self.send_response(200)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try: self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError): pass
with TemporaryDirectory() as tmp:
    state = Path(tmp) / 'auth'
    proof = provision(state); cert, key, _ = generate_tls_certificate(state)
    authority = BootstrapAuthority(state)
    with running(ThreadingHTTPServer(('127.0.0.1', 0), App)) as app:
        app.mc_gateway_verifier = AssertionVerifier(authority)
        with running(BootstrapServer(('127.0.0.1', 0), authority, cert=cert, key=key, isolated_backend_ports={'dev': app.server_port})) as server:
            server.RequestHandlerClass = RegisteredHandler
            n = server.server_port
            child = '/applications/onlyoffice?mcel-shell-child=1'
            status, headers, _ = request(DEV, n, cert, child)
            assert status == 401 and headers['X-Frame-Options'] == 'DENY', (status, headers)
            cookie = auth_cookie(server, proof, cert)
            status, headers, body = request(DEV, n, cert, child, cookie=cookie)
            assert status == 200 and body == HTML, (status, body[:200])
            assert headers['X-Frame-Options'] == 'SAMEORIGIN' and headers['Content-Security-Policy'] == "frame-ancestors 'self'"
            assert all(name+';dur=' in headers['Server-Timing'] for name in ('auth','upstream','body','gateway')), headers
            assert headers['Cache-Control'] == 'no-store'
            for path in ('/api/large', '/applications/binary', '/applications/oversized'):
                status, headers, body = request(DEV, n, cert, path, cookie=cookie)
                assert status == 502 and b'BACKEND_RESPONSE_TOO_LARGE' in body and headers['X-Frame-Options'] == 'DENY', (path, status, body[:200])
            print('LARGE_NATIVE_APPLICATION_GATEWAY_PASS')
'''
        env = {**os.environ, 'MAIN_COMPUTER_HARDENED_CANDIDATE': '1', 'MAIN_COMPUTER_PRIMARY_OWNER': 'dev', 'MAIN_COMPUTER_INSTALLATION_MODE': 'dev'}
        result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=900 if os.name == 'nt' else 180)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('LARGE_NATIVE_APPLICATION_GATEWAY_PASS', result.stdout)

if __name__ == '__main__': unittest.main()
