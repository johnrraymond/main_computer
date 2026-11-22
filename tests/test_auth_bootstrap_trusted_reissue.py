"""Missing AAA recovery is trusted-local, invalidates all old cookies, keeps key."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest

from main_computer.auth_bootstrap import (
    BootstrapAuthority, COOKIE, PROOF_LINK_NAME, _load_json,
    provision, recover_deleted_proof_locally,
)
from main_computer.auth_bootstrap_service import preflight
from tools.mcel_auth_bootstrap_smoke import request, running


def _login(port, cert, proof):
    status, challenge, _ = request(port, cert, 'GET', '/api/auth/bootstrap/challenge')
    assert status == 200
    key = base64.urlsafe_b64decode(json.loads(proof.read_text())['key_b64'] + '=')
    message = BootstrapAuthority.message(challenge['challenge_id'], challenge['nonce'])
    signature = base64.urlsafe_b64encode(hmac.new(key, message, hashlib.sha256).digest()).decode().rstrip('=')
    status, _, headers = request(port, cert, 'POST', '/api/auth/bootstrap/verify',
        body={'challenge_id': challenge['challenge_id'], 'signature': signature})
    assert status == 200
    return headers['Set-Cookie'].split(';')[0]


def test_deleted_proof_reissued_by_trusted_local_open_preserves_key_but_revokes(tmp_path):
    original = provision(tmp_path)
    before = original.read_bytes()
    authority = BootstrapAuthority(tmp_path)
    old_token = authority.sessions.issue()
    old_generation = _load_json(tmp_path / PROOF_LINK_NAME)['generation']
    original.unlink()
    restored, created = recover_deleted_proof_locally(tmp_path)
    assert created and restored == original
    assert restored.is_file() and json.loads(restored.read_text())['key_b64'] == json.loads(before)['key_b64']
    assert not BootstrapAuthority(tmp_path).sessions.valid(old_token)
    assert _load_json(tmp_path / PROOF_LINK_NAME)['generation'] != old_generation
    assert _load_json(tmp_path / 'sessions.json') == {}
    assert BootstrapAuthority(tmp_path).sessions.proof_current()
    assert recover_deleted_proof_locally(tmp_path) == (original, False)


def test_real_https_reauth_after_local_reissue(tmp_path):
    proof = provision(tmp_path)
    with running(tmp_path) as (port, cert, _):
        old_cookie = _login(port, cert, proof)
        status, old, _ = request(port, cert, 'GET', '/api/auth/bootstrap/session', cookie=old_cookie)
        assert status == 200 and old['authenticated']
        proof.unlink()
        # The running MC-owned backend restores the proof on its first
        # request. Bob cannot supply a new key or bypass authentication.
        for endpoint in ('/api/auth/bootstrap/session', '/api/auth/bootstrap/challenge', '/auth/login'):
            request(port, cert, 'GET', endpoint)
        assert proof.is_file()
        assert not recover_deleted_proof_locally(tmp_path)[1]  # already recovered
        status, state, _ = request(port, cert, 'GET', '/api/auth/bootstrap/session')
        assert status == 200 and state['proof_reissued'] is True
        status, old, _ = request(port, cert, 'GET', '/api/auth/bootstrap/session', cookie=old_cookie)
        assert status == 200 and not old['authenticated']
        status, _, headers = request(port, cert, 'GET', '/graphical', cookie=old_cookie)
        assert status == 303 and headers['Location'].startswith('/auth/login')
        new_cookie = _login(port, cert, proof)
        status, fresh, _ = request(port, cert, 'GET', '/api/auth/bootstrap/session', cookie=new_cookie)
        assert status == 200 and fresh['authenticated']
        assert new_cookie != old_cookie
        status, old, _ = request(port, cert, 'GET', '/api/auth/bootstrap/session', cookie=old_cookie)
        assert status == 200 and not old['authenticated']


def test_preflight_reissues_proof_automatically_without_new_key(tmp_path, monkeypatch):
    proof = provision(tmp_path)
    key_before = json.loads(proof.read_text())['key_b64']
    old_session = BootstrapAuthority(tmp_path).sessions.issue()
    proof.unlink()
    monkeypatch.setattr('main_computer.auth_bootstrap_service.ensure_windows_user_trust', lambda *_args: None)
    reported, _, _ = preflight(state=tmp_path)
    assert reported == proof and proof.is_file()
    assert json.loads(proof.read_text())['key_b64'] == key_before
    assert not BootstrapAuthority(tmp_path).sessions.valid(old_session)
    # Idempotent subsequent startup must not revoke the replacement session.
    fresh = BootstrapAuthority(tmp_path).sessions.issue()
    preflight(state=tmp_path)
    assert BootstrapAuthority(tmp_path).sessions.valid(fresh)


def test_no_legacy_proof_backup_needed_for_reissue(tmp_path):
    proof = provision(tmp_path)
    BootstrapAuthority(tmp_path)
    assert not (tmp_path / 'auth-proof.json').exists()
    proof.unlink()
    assert recover_deleted_proof_locally(tmp_path)[1]
    assert proof.is_file()


def test_tampered_existing_aaa_not_silently_replaced(tmp_path):
    proof = provision(tmp_path)
    proof.write_text('{"type":"mc-proof-v2","key_b64":"invalid"}')
    with pytest.raises((RuntimeError, ValueError)):
        recover_deleted_proof_locally(tmp_path)
    assert 'invalid' in proof.read_text()


def test_legacy_session_timestamps_are_cleared_if_proof_disappears_before_link(tmp_path):
    proof = provision(tmp_path)
    old_token = 's' * 50
    digest = hashlib.sha256(old_token.encode()).hexdigest()
    (tmp_path / 'sessions.json').write_text(json.dumps({digest: 9999999999}))
    assert not (tmp_path / PROOF_LINK_NAME).exists()
    proof.unlink()
    assert recover_deleted_proof_locally(tmp_path)[1]
    assert not BootstrapAuthority(tmp_path).sessions.valid(old_token)
