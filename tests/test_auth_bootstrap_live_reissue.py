"""Live, no-restart recovery of The proof file AAA proof from the protected authority.

Every test uses a disposable state directory, real HTTPS server and proof.
No real Windows account, profile file, browser session or MC process is touched.
"""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import json
import threading
import time

from main_computer.auth_bootstrap import (
    AUTHORITY_NAME, BootstrapAuthority, COOKIE, PROOF_LINK_NAME, provision,
)
from main_computer.auth_bootstrap_service import monitor_proof_file
from tools.mcel_auth_bootstrap_smoke import request, running


def _login(port, cert, proof, *, challenge=None):
    if challenge is None:
        status, challenge, _ = request(port, cert, 'GET', '/api/auth/bootstrap/challenge')
        assert status == 200
    record = json.loads(proof.read_text())
    key = base64.urlsafe_b64decode(record['key_b64'] + '=')
    message = BootstrapAuthority.message(challenge['challenge_id'], challenge['nonce'])
    signature = base64.urlsafe_b64encode(hmac.new(key, message, hashlib.sha256).digest()).decode().rstrip('=')
    status, body, headers = request(port, cert, 'POST', '/api/auth/bootstrap/verify',
        body={'challenge_id': challenge['challenge_id'], 'signature': signature})
    return status, body, headers


def test_no_restart_backend_reissues_and_explains_forced_login(tmp_path):
    proof = provision(tmp_path)
    original_key = json.loads(proof.read_text())['key_b64']
    authority_before = (tmp_path / AUTHORITY_NAME).read_bytes()
    with running(tmp_path) as (port, cert, _):
        status, _, headers = _login(port, cert, proof)
        assert status == 200
        cookie = headers['Set-Cookie'].split(';')[0]
        status, session, _ = request(port, cert, 'GET', '/api/auth/bootstrap/session', cookie=cookie)
        assert status == 200 and session['authenticated']
        proof.unlink()
        # This is an ordinary HTTPS request, without a local open/start command.
        status, session, _ = request(port, cert, 'GET', '/api/auth/bootstrap/session', cookie=cookie)
        assert status == 200
        assert not session['authenticated']
        assert session['proof_available'] and session['proof_reissued']
        assert proof.exists() and json.loads(proof.read_text())['key_b64'] == original_key
        assert (tmp_path / AUTHORITY_NAME).read_bytes() == authority_before
        status, _, headers = request(port, cert, 'GET', '/graphical', cookie=cookie)
        assert status == 303 and headers['Location'].startswith('/auth/login')
        status, _, _ = request(port, cert, 'GET', '/auth/login', cookie=cookie)
        assert status == 200
        status, result, _ = _login(port, cert, proof)
        assert status == 200 and result['authenticated']
        fresh_cookie = _login_cookie_from_result(port, cert, proof)
        status, new_session, _ = request(port, cert, 'GET', '/api/auth/bootstrap/session', cookie=fresh_cookie)
        assert status == 200 and new_session['authenticated']
        assert new_session['proof_reissued'] is False
        status, prior, _ = request(port, cert, 'GET', '/api/auth/bootstrap/session', cookie=cookie)
        assert status == 200 and not prior['authenticated']


def _login_cookie_from_result(port, cert, proof):
    status, _, headers = _login(port, cert, proof)
    assert status == 200
    return headers['Set-Cookie'].split(';')[0]


def test_existing_challenge_cannot_survive_aaa_reissue(tmp_path):
    proof = provision(tmp_path)
    with running(tmp_path) as (port, cert, _):
        status, old_challenge, _ = request(port, cert, 'GET', '/api/auth/bootstrap/challenge')
        assert status == 200
        # The original key remains valid for a *new* challenge after reissue,
        # but an unconsumed signature from the old generation is rejected.
        record = json.loads(proof.read_text())
        key = base64.urlsafe_b64decode(record['key_b64'] + '=')
        message = BootstrapAuthority.message(old_challenge['challenge_id'], old_challenge['nonce'])
        old_signature = base64.urlsafe_b64encode(hmac.new(key, message, hashlib.sha256).digest()).decode().rstrip('=')
        proof.unlink()
        status, state, _ = request(port, cert, 'GET', '/api/auth/bootstrap/session')
        assert status == 200 and state['proof_reissued']
        status, result, headers = request(port, cert, 'POST', '/api/auth/bootstrap/verify',
            body={'challenge_id': old_challenge['challenge_id'], 'signature': old_signature})
        assert status in (401, 403) and result.get('authenticated') is not True
        assert 'Set-Cookie' not in headers
        assert _login(port, cert, proof)[0] == 200


def test_proof_monitor_reissues_with_no_http_request(tmp_path):
    proof = provision(tmp_path)
    authority = BootstrapAuthority(tmp_path)
    old = authority.sessions.issue()
    stop = threading.Event()
    monitor = threading.Thread(target=monitor_proof_file,
        args=(authority, stop), kwargs={'interval': 0.03}, daemon=True)
    monitor.start()
    try:
        proof.unlink()
        deadline = time.monotonic() + 3
        while not proof.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert proof.is_file(), 'running backend should restore AAA without browser traffic'
        assert not authority.sessions.valid(old)
        assert authority.sessions.reissue_pending()
    finally:
        stop.set()
        monitor.join(timeout=3)
        assert not monitor.is_alive()


def test_missing_private_authority_fails_closed_without_new_key(tmp_path):
    proof = provision(tmp_path)
    with running(tmp_path) as (port, cert, _):
        status, _, headers = _login(port, cert, proof)
        assert status == 200
        old_cookie = headers['Set-Cookie'].split(';')[0]
        authority_file = tmp_path / AUTHORITY_NAME
        authority_backup = authority_file.read_bytes()
        authority_file.unlink()
        proof.unlink()
        status, state, _ = request(port, cert, 'GET', '/api/auth/bootstrap/session', cookie=old_cookie)
        assert status == 200 and not state['authenticated']
        assert not state['proof_available']
        assert not proof.exists()
        status, challenge, _ = request(port, cert, 'GET', '/api/auth/bootstrap/challenge')
        assert status == 503 and challenge['error'] == 'MC_PROOF_UNAVAILABLE'
        authority_file.write_bytes(authority_backup)
        status, state, _ = request(port, cert, 'GET', '/api/auth/bootstrap/session', cookie=old_cookie)
        assert status == 200 and state['proof_available'] and state['proof_reissued']
        assert not state['authenticated']


def test_concurrent_missing_proof_probes_do_not_resurrect_sessions(tmp_path):
    proof = provision(tmp_path)
    with running(tmp_path) as (port, cert, _):
        status, _, headers = _login(port, cert, proof)
        assert status == 200
        old_cookie = headers['Set-Cookie'].split(';')[0]
        old_generation = json.loads((tmp_path / PROOF_LINK_NAME).read_text())['generation']
        proof.unlink()
        def query(_):
            return request(port, cert, 'GET', '/api/auth/bootstrap/session', cookie=old_cookie)
        with ThreadPoolExecutor(max_workers=8) as pool:
            answers = list(pool.map(query, range(12)))
        assert all(status == 200 and not state['authenticated'] for status, state, _ in answers)
        assert proof.is_file()
        link = json.loads((tmp_path / PROOF_LINK_NAME).read_text())
        assert link['status'] == 'active' and link['reissued_reauth_required']
        assert link['generation'] != old_generation
        assert json.loads((tmp_path / 'sessions.json').read_text()) == {}


def test_modified_existing_proof_is_not_overwritten_by_live_server(tmp_path):
    proof = provision(tmp_path)
    with running(tmp_path) as (port, cert, _):
        status, _, headers = _login(port, cert, proof)
        assert status == 200
        cookie = headers['Set-Cookie'].split(';')[0]
        invalid = '{"type":"mc-proof-v2","key_b64":"invalid"}'
        proof.write_text(invalid)
        status, state, _ = request(port, cert, 'GET', '/api/auth/bootstrap/session', cookie=cookie)
        assert status == 200 and not state['authenticated'] and not state['proof_available']
        assert proof.read_text() == invalid
        status, ch, _ = request(port, cert, 'GET', '/api/auth/bootstrap/challenge')
        assert status == 503 and ch['error'] == 'MC_PROOF_UNAVAILABLE'
