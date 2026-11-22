"""Real TLS, native file-picker and durable-session bootstrap acceptance.

Does not modify or contact production MC. Uses disposable state and ports.
Windows production-cert trust is covered separately by the existing cookie
Windows smoke. SPKI pin is used only in the disposable Playwright browser test.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import os
import shutil
import socket
import ssl
import sys
import tempfile
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from main_computer.auth_bootstrap import (
    BootstrapAuthority, BootstrapServer, COOKIE, HOST, PROOF_LINK_NAME, _unb64,
    generate_tls_certificate, provision, recover_deleted_proof_locally,
)


class LocalTLSConnection(http.client.HTTPSConnection):
    def __init__(self, port: int, cafile: Path):
        # Windows private-state writes run certificate/ACL checks through
        # native utilities. A four-second read deadline classified slow secure
        # writes as server hangs, while their lock file was still open.
        super().__init__(HOST, port, context=ssl.create_default_context(cafile=str(cafile)),
                         timeout=45 if os.name == 'nt' else 12)
        self.target_port = port

    def connect(self) -> None:
        # Use a short TCP connect deadline, but honor the longer HTTP read
        # deadline configured above. A custom HTTPSConnection.connect() does
        # not reset the timeout automatically after ssl.wrap_socket(). Windows
        # ACL-protected session issuance can legitimately take >4 seconds.
        sock = socket.create_connection(('127.0.0.1', self.target_port), timeout=4)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=HOST)
            self.sock.settimeout(self.timeout)
        except BaseException:
            sock.close()
            raise


def request(port: int, cert: Path, method: str, path: str, *, body: dict | None = None,
            cookie: str = '', host: str | None = None, origin: str | None = None,
            extra: dict[str, str] | None = None) -> tuple[int, dict, dict]:
    conn = LocalTLSConnection(port, cert)
    headers = {'Host': host if host is not None else f'{HOST}:{port}'}
    if cookie:
        headers['Cookie'] = cookie
    if body is not None:
        headers['Content-Type'] = 'application/json'
    if origin is not None:
        headers['Origin'] = origin
    elif method == 'POST':
        headers['Origin'] = f'https://{HOST}:{port}'
    if extra:
        headers.update(extra)
    raw = json.dumps(body).encode() if body is not None else None
    try:
        conn.request(method, path, body=raw, headers=headers)
        result = conn.getresponse()
        data = result.read()
        try:
            decoded = json.loads(data)
        except (ValueError, UnicodeError):
            decoded = {'body': data.decode(errors='replace')}
        return result.status, decoded, dict(result.getheaders())
    finally:
        conn.close()


class FixtureBootstrapServer(BootstrapServer):
    # Production HTTP handlers are deliberately daemon threads so a malicious
    # slow client cannot block supervised shutdown. The disposable fixture,
    # however, must *join* handlers before Windows removes .proof-link.lock.
    daemon_threads = False
    block_on_close = True


@contextmanager
def running(root: Path, *, backend_port: int | None = None):
    cert, key, thumb = generate_tls_certificate(root)
    server = FixtureBootstrapServer(('127.0.0.1', 0), BootstrapAuthority(root),
                                    backend_port=backend_port, cert=cert, key=key)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, cert, thumb
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=4)


def signed(proof: Path, challenge: dict) -> str:
    record = json.loads(proof.read_text())
    key = _unb64(record['key_b64'])
    message = BootstrapAuthority.message(challenge['challenge_id'], challenge['nonce'])
    digest = hmac.new(key, message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode('ascii').rstrip('=')


def browser_smoke(root: Path, proof: Path, cert: Path, port: int) -> bool:
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        print(f'MC_AUTH_BOOTSTRAP_SMOKE native-file-picker UNVERIFIED missing-library={exc}', flush=True)
        return False
    leaf = x509.load_pem_x509_certificate(cert.read_bytes())
    spki = leaf.public_key().public_bytes(serialization.Encoding.DER,
                                          serialization.PublicFormat.SubjectPublicKeyInfo)
    pin = base64.b64encode(hashlib.sha256(spki).digest()).decode()
    with sync_playwright() as playwright:
        executable = os.environ.get('CHROMIUM_EXECUTABLE') or shutil.which('chromium')
        if not executable and not Path(playwright.chromium.executable_path).exists():
            print('MC_AUTH_BOOTSTRAP_SMOKE native-file-picker UNVERIFIED no-chromium', flush=True)
            return False
        options = dict(executable_path=executable) if executable else {}
        args = ['--host-resolver-rules=MAP mc.test 127.0.0.1, EXCLUDE localhost',
                '--no-proxy-server', '--ignore-certificate-errors-spki-list=' + pin]
        profile = root / 'test-chromium-profile'
        for index in range(2):
            context = playwright.chromium.launch_persistent_context(
                str(profile), headless=True, args=args, **options)
            try:
                page = context.new_page()
                page.goto(f'https://{HOST}:{port}/graphical', wait_until='domcontentloaded')
                if index == 0:
                    page.wait_for_url(f'https://{HOST}:{port}/auth/login?next=%2Fgraphical', timeout=12000)
                    with page.expect_file_chooser() as waiting:
                        page.locator('#auth-proof-file-button').click()
                    waiting.value.set_files(str(proof))
                    page.wait_for_url(f'https://{HOST}:{port}/graphical', timeout=20000)
                    print('MC_AUTH_BOOTSTRAP_SMOKE native-picker-on-dedicated-https-login PASS', flush=True)
                else:
                    page.wait_for_url(f'https://{HOST}:{port}/graphical', timeout=15000)
                    print('MC_AUTH_BOOTSTRAP_SMOKE browser-restart-no-reupload PASS', flush=True)
                assert 'Main Computer Control Panel' in page.title()
                cookies = context.cookies()
                mc = [value for value in cookies if value['name'] == COOKIE]
                assert len(mc) == 1 and mc[0]['httpOnly'] and mc[0]['secure']
                assert page.evaluate('localStorage.length') == 0
                print(f'MC_AUTH_BOOTSTRAP_SMOKE secure-httponly-cookie-{index} PASS', flush=True)
            finally:
                context.close()
    return True


def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    if argv not in ([], ['run'], ['run', '--no-browser'], ['--no-browser']):
        print('Usage: python -m tools.mcel_auth_bootstrap_smoke run')
        return 2
    passed = 0
    failed = []

    def check(name: str, condition: bool):
        nonlocal passed
        print(f'MC_AUTH_BOOTSTRAP_SMOKE {name} {"PASS" if condition else "FAIL"}', flush=True)
        passed += bool(condition)
        if not condition:
            failed.append(name)

    with tempfile.TemporaryDirectory(prefix='mc-user-bootstrap-') as directory:
        root = Path(directory)
        proof = provision(root)
        record = json.loads(proof.read_text())
        check('private-provisioned-key', len(_unb64(record['key_b64'])) == 32)
        check('no-key-in-session-database', not (root / 'sessions.json').exists())
        check('provision-idempotent', provision(root) == proof)
        original_graphical = (Path(__file__).resolve().parents[1] / 'main_computer' / 'web' / 'graphical.html').read_text()
        check('existing-graphical-button-navigates-to-secure-login',
              'data-auth-login-url="https://mc.test:8875/auth/login?next=%2Fgraphical"' in original_graphical
              and 'window.location.assign(target.href)' in original_graphical
              and 'userProofPicker' not in original_graphical)
        with running(root) as (port, cert, _thumb):
            check('proof-link-reconciled-before-first-http',
                  (root / PROOF_LINK_NAME).is_file())
            status, _, headers = request(port, cert, 'GET', '/graphical')
            check('graphical-redirects-to-real-login', status == 303 and
                  headers.get('Location') == '/auth/login?next=%2Fgraphical')
            status, login, headers = request(port, cert, 'GET', '/auth/login?next=%2Fgraphical')
            check('dedicated-login-page-with-file-picker', status == 200 and
                  'Select MC Proof File' in login['body'] and
                  '/auth/login.js' in login['body'])
            check('saved-proof-location-visible-with-copy', status == 200 and
                  '%USERPROFILE%\\AAA_AUTH_MAIN_COMPUTER.json' in login['body'] and
                  'auth-proof-copy-path' in login['body'])
            check('login-page-has-csp', 'script-src' in headers.get('Content-Security-Policy', ''))
            status, js, _ = request(port, cert, 'GET', '/auth/login.js')
            check('browser-computes-challenge-locally', status == 200 and
                  'crypto.subtle.sign' in js['body'] and '/api/auth/bootstrap/' in js['body'])
            check('proof-location-copied-without-reading-secret', status == 200 and
                  'navigator.clipboard.writeText(pathInput.value)' in js['body'] and
                  'pickInput.click()' in js['body'])
            status, _, headers = request(port, cert, 'GET', '/applications',
                 extra={'Accept': 'text/html'})
            check('other-html-navigation-redirects-to-login', status == 303 and
                  headers.get('Location') == '/auth/login?next=%2Fapplications')
            status, _, _ = request(port, cert, 'GET', '/api/unprotected',
                 extra={'Accept': 'text/html'})
            check('api-does-not-redirect-to-html', status == 401)
            status, _, _ = request(port, cert, 'GET', '/api/auth/bootstrap/provision')
            check('bob-cannot-provision-over-http-api', status == 404)
            status, _, _ = request(port, cert, 'POST', '/api/auth/bootstrap/provision', body={})
            check('bob-cannot-provision-over-post', status == 404)
            status, challenge, _ = request(port, cert, 'GET', '/api/auth/bootstrap/challenge')
            check('real-challenge-issued', status == 200 and challenge.get('algorithm') == 'HMAC-SHA256')
            challenge_id = challenge['challenge_id']
            signature = signed(proof, challenge)
            status, _, _ = request(port, cert, 'POST', '/api/auth/bootstrap/verify',
                                   body={'challenge_id': challenge_id,
                                         'signature': signature,
                                         'key_b64': record['key_b64']})
            check('permanent-key-in-request-rejected', status == 400)
            status, _, _ = request(port, cert, 'POST', '/api/auth/bootstrap/verify',
                                   body={'challenge_id': challenge_id, 'signature': 'invalid'})
            check('forged-proof-denied', status == 401)
            status, _, _ = request(port, cert, 'POST', '/api/auth/bootstrap/verify',
                                   body={'challenge_id': challenge_id, 'signature': signature})
            check('replayed-used-challenge-denied', status == 401)
            status, challenge, _ = request(port, cert, 'GET', '/api/auth/bootstrap/challenge')
            status, verified, headers = request(port, cert, 'POST', '/api/auth/bootstrap/verify',
                 body={'challenge_id': challenge['challenge_id'], 'signature': signed(proof, challenge)})
            cookie = headers.get('Set-Cookie', '').split(';')[0]
            check('one-key-one-session-issued', status == 200 and verified.get('authenticated') is True)
            check('secure-hostonly-persistent-cookie',
                  COOKIE in cookie and 'Secure; HttpOnly; SameSite=Strict' in headers.get('Set-Cookie', '')
                  and 'Max-Age=' in headers.get('Set-Cookie', ''))
            status, public, _ = request(port, cert, 'GET', '/api/auth/bootstrap/session', cookie=cookie)
            check('same-session-authorized', status == 200 and public['authenticated'])
            status, _, headers = request(port, cert, 'GET', '/auth/login?next=%2Fgraphical', cookie=cookie)
            check('login-returns-user-to-graphical', status == 303 and
                  headers.get('Location') == '/graphical')
            status, _, headers = request(port, cert, 'GET', '/auth/login?next=%2F%2Fevil.invalid', cookie=cookie)
            check('login-rejects-external-return-target', status == 303 and
                  headers.get('Location') == '/graphical')
            status, graph, headers = request(port, cert, 'GET', '/graphical', cookie=cookie)
            check('authenticated-graphical-renders', status == 200 and
                  'Main Computer Control Panel' in graph['body'] and
                  f'https://{HOST}:{port}/auth/login' in graph['body'])
            status, public, _ = request(port, cert, 'GET', '/api/auth/bootstrap/session',
                                        cookie=COOKIE + '=bogus')
            check('bob-forged-cookie-denied', status == 200 and not public['authenticated'])
            status, public, _ = request(port, cert, 'GET', '/api/auth/bootstrap/session',
                                        cookie=f'{cookie}; {cookie}')
            check('duplicate-session-cookie-denied', status == 200 and not public['authenticated'])
            status, _, _ = request(port, cert, 'GET', '/api/auth/bootstrap/challenge', host='127.0.0.1')
            check('spoofed-host-denied', status == 403)
            status, challenge, _ = request(port, cert, 'GET', '/api/auth/bootstrap/challenge')
            status, _, _ = request(port, cert, 'POST', '/api/auth/bootstrap/verify',
                 body={'challenge_id': challenge['challenge_id'], 'signature': signed(proof, challenge)},
                 origin='http://attacker.invalid')
            check('hostile-origin-denied', status == 403)
            status, _, _ = request(port, cert, 'POST', '/api/auth/bootstrap/verify',
                 body={'challenge_id': challenge['challenge_id'], 'signature': signed(proof, challenge)})
            check('origin-denial-did-not-consume-proof', status == 200)
        with running(root) as (port, cert, _thumb):
            status, data, _ = request(port, cert, 'GET', '/api/auth/bootstrap/session', cookie=cookie)
            check('server-restart-persistent-session', status == 200 and data['authenticated'])
            status, _, _ = request(port, cert, 'GET', '/graphical', cookie=cookie)
            check('restart-no-login-redirect', status == 200)
            status, data, _ = request(port, cert, 'POST', '/api/auth/bootstrap/logout', cookie=cookie, body={})
            check('session-logout', status == 200 and not data['authenticated'])
            status, data, _ = request(port, cert, 'GET', '/api/auth/bootstrap/session', cookie=cookie)
            check('revoked-cookie-denied', status == 200 and not data['authenticated'])
            status, challenge, _ = request(port, cert, 'GET', '/api/auth/bootstrap/challenge')
            status, _, headers = request(port, cert, 'POST', '/api/auth/bootstrap/verify',
                body={'challenge_id': challenge['challenge_id'], 'signature': signed(proof, challenge)})
            check('proof-can-login-again-after-logout', status == 200)
            fresh_cookie = headers.get('Set-Cookie', '').split(';')[0]
            if os.getenv('MC_AUTH_BOOTSTRAP_SKIP_BROWSER') != '1' and '--no-browser' not in argv:
                try:
                    check('native-browser-end-to-end', browser_smoke(root, proof, cert, port))
                except Exception as exc:
                    print(f'MC_AUTH_BOOTSTRAP_SMOKE browser-error {type(exc).__name__}: {str(exc)[:250]}', flush=True)
                    check('native-browser-end-to-end', False)
        session_data = json.loads((root / 'sessions.json').read_text())
        check('session-store-does-not-expose-key', record['key_b64'] not in json.dumps(session_data))
        check('session-state-is-permanent', bool(session_data))
        with running(root) as (port, cert, _thumb):
            status, data, _ = request(port, cert, 'GET', '/api/auth/bootstrap/session', cookie=fresh_cookie)
            check('restored-process-keeps-session-with-AAA-proof', status == 200 and data['authenticated'])
            saved_proof = proof.read_bytes()
            proof.unlink()
            status, data, _ = request(port, cert, 'GET', '/api/auth/bootstrap/session', cookie=fresh_cookie)
            check('AAA-deletion-revokes-browser-session', status == 200 and not data['authenticated'])
            check('backend-auto-reissues-missing-AAA', status == 200 and proof.is_file()
                  and json.loads(proof.read_bytes())['key_b64']
                  == json.loads(saved_proof)['key_b64'])
            check('session-explains-automatic-reissue', data.get('proof_available') is True
                  and data.get('proof_reissued') is True)
            status, _, headers = request(port, cert, 'GET', '/graphical', cookie=fresh_cookie)
            check('AAA-deletion-forces-login-redirect', status == 303 and
                  headers.get('Location', '').startswith('/auth/login'))
            status, data, _ = request(port, cert, 'GET', '/api/auth/bootstrap/challenge')
            check('auto-reissued-AAA-offers-immediate-new-challenge', status == 200
                  and isinstance(data.get('challenge_id'), str))
            status, data, _ = request(port, cert, 'GET', '/api/auth/bootstrap/session', cookie=fresh_cookie)
            check('reissued-AAA-does-not-revive-old-cookie', status == 200 and not data['authenticated'])
            status, challenge, _ = request(port, cert, 'GET', '/api/auth/bootstrap/challenge')
            status, data, headers = request(port, cert, 'POST', '/api/auth/bootstrap/verify',
                body={'challenge_id': challenge['challenge_id'], 'signature': signed(proof, challenge)})
            new_cookie = headers.get('Set-Cookie', '').split(';')[0]
            status2, data2, _ = request(port, cert, 'GET', '/api/auth/bootstrap/session', cookie=new_cookie)
            check('restored-AAA-issues-new-persistent-session', status == 200 and
                  status2 == 200 and data2['authenticated'])
            check('successful-reauth-clears-reissue-notice', data2.get('proof_reissued') is False)
    print('MC_AUTH_BOOTSTRAP_SMOKE SUMMARY ' + json.dumps(
        {'status': 'PASS' if not failed else 'FAIL', 'passed': passed,
         'failed': failed, 'production_modified': False}, sort_keys=True), flush=True)
    return 0 if not failed else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
