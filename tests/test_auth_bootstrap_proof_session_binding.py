"""Proof-file binding: persistent browser sessions never outlive their MC proof.

All tests use disposable TLS/authority files; no real user profile is accessed.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import shutil
import time

from main_computer.auth_bootstrap import (
    AUTHORITY_NAME, COOKIE, PROOF_LINK_NAME, PROOF_NAME, BootstrapAuthority,
    proof_file_path, provision,
)
from tools.mcel_auth_bootstrap_smoke import request, running


def _signin(port, cert, proof):
    status, challenge, _ = request(port, cert, 'GET', '/api/auth/bootstrap/challenge')
    assert status == 200, challenge
    key = base64.urlsafe_b64decode(json.loads(proof.read_text())['key_b64'] + '=')
    message = BootstrapAuthority.message(challenge['challenge_id'], challenge['nonce'])
    signature = base64.urlsafe_b64encode(hmac.new(key, message, hashlib.sha256).digest()).decode().rstrip('=')
    status, result, headers = request(port, cert, 'POST', '/api/auth/bootstrap/verify',
        body={'challenge_id': challenge['challenge_id'], 'signature': signature})
    assert status == 200 and result['authenticated'] is True
    return headers['Set-Cookie'].split(';')[0]


def _session(port, cert, cookie):
    status, result, _ = request(port, cert, 'GET', '/api/auth/bootstrap/session', cookie=cookie)
    assert status == 200
    return result['authenticated']


def test_deleting_proof_immediately_revokes_old_session_and_blocks_login(tmp_path):
    proof = provision(tmp_path)
    saved = proof.read_bytes()
    with running(tmp_path) as (port, cert, _thumb):
        cookie = _signin(port, cert, proof)
        assert _session(port, cert, cookie)
        generation = json.loads((tmp_path / PROOF_LINK_NAME).read_text())['generation']
        proof.unlink()
        assert not _session(port, cert, cookie)
        assert json.loads((tmp_path / 'sessions.json').read_text()) == {}
        status, _, headers = request(port, cert, 'GET', '/graphical', cookie=cookie)
        assert status == 303 and headers['Location'].startswith('/auth/login')
        status, result, _ = request(port, cert, 'GET', '/api/auth/bootstrap/challenge')
        assert status == 200 and result['algorithm'] == 'HMAC-SHA256'
        assert proof.is_file() and proof.read_bytes() == saved
        assert provision(tmp_path) == proof
        assert not _session(port, cert, cookie)
        assert json.loads((tmp_path / PROOF_LINK_NAME).read_text())['generation'] != generation
        new_cookie = _signin(port, cert, proof)
        assert _session(port, cert, new_cookie)
        assert not _session(port, cert, cookie)


def test_proof_replacement_same_secret_still_revokes_session(tmp_path):
    proof = provision(tmp_path)
    authority = BootstrapAuthority(tmp_path)
    token = authority.sessions.issue()
    old_generation = json.loads((tmp_path / PROOF_LINK_NAME).read_text())['generation']
    replacement = tmp_path / 'replacement'
    replacement.write_bytes(proof.read_bytes())
    os.replace(replacement, proof)
    assert not authority.sessions.valid(token)
    assert json.loads((tmp_path / PROOF_LINK_NAME).read_text())['generation'] != old_generation
    # Date sorting alone must never revoke a valid session.
    restored = authority.sessions.issue()
    os.utime(proof, None)
    assert authority.sessions.valid(restored)
    assert BootstrapAuthority(tmp_path).sessions.valid(restored)


def test_deleted_proof_during_shutdown_reissues_but_revokes_old_sessions(tmp_path, monkeypatch):
    proof = provision(tmp_path)
    session = BootstrapAuthority(tmp_path).sessions.issue()
    proof.unlink()
    assert not BootstrapAuthority(tmp_path).sessions.valid(session)
    assert provision(tmp_path) == proof
    assert proof.is_file()  # The running backend auto-restored it.
    from main_computer.auth_bootstrap_service import preflight
    monkeypatch.setattr('main_computer.auth_bootstrap_service.ensure_windows_user_trust', lambda *_args: None)
    preflight(state=tmp_path)  # ./start performs trusted recovery
    assert proof.is_file()
    assert not BootstrapAuthority(tmp_path).sessions.valid(session)
    assert json.loads((tmp_path / PROOF_LINK_NAME).read_text())['status'] == 'active'
    fresh = BootstrapAuthority(tmp_path).sessions.issue()
    assert BootstrapAuthority(tmp_path).sessions.valid(fresh)
    assert not BootstrapAuthority(tmp_path).sessions.valid(session)


def test_wrong_key_and_corrupt_proof_deny_both_cookie_and_challenge(tmp_path):
    proof = provision(tmp_path)
    with running(tmp_path) as (port, cert, _thumb):
        cookie = _signin(port, cert, proof)
        assert _session(port, cert, cookie)
        proof.write_text(json.dumps({'type': 'mc-proof-v2', 'key_b64': 'A' * 43}))
        assert not _session(port, cert, cookie)
        status, result, _ = request(port, cert, 'GET', '/api/auth/bootstrap/challenge')
        assert status == 503 and result['error'] == 'MC_PROOF_UNAVAILABLE'
        assert json.loads((tmp_path / PROOF_LINK_NAME).read_text())['status'] == 'invalid'


def test_pre_link_session_store_is_revoked_on_new_proof_format(tmp_path):
    proof = provision(tmp_path)
    old_token = 'old-' + 'a' * 42
    digest = hashlib.sha256(old_token.encode()).hexdigest()
    (tmp_path / 'sessions.json').write_text(json.dumps({digest: int(time.time()) + 3600}))
    auth = BootstrapAuthority(tmp_path)
    assert not auth.sessions.valid(old_token)
    assert json.loads((tmp_path / 'sessions.json').read_text()) == {}
    assert provision(tmp_path) == proof
    fresh = auth.sessions.issue()
    assert BootstrapAuthority(tmp_path).sessions.valid(fresh)


def test_old_proof_link_and_cookie_are_revoked_without_migration(tmp_path):
    proof = provision(tmp_path)
    original = proof.read_bytes()
    stale_token = 'x' * 60
    digest = hashlib.sha256(stale_token.encode()).hexdigest()
    (tmp_path / PROOF_LINK_NAME).write_text(json.dumps({
        'version': 1, 'generation': 'old-generation',
        'status': 'active', 'fingerprint': 'prior-proof',
    }))
    (tmp_path / 'sessions.json').write_text(json.dumps({
        digest: {'generation': 'old-generation', 'until': 9999999999},
    }))
    proof.unlink()  # The new filename did not exist on the previous install.
    assert provision(tmp_path) == proof
    assert proof.is_file() and proof.read_bytes() == original
    assert json.loads((tmp_path / PROOF_LINK_NAME).read_text())['version'] == 2
    assert json.loads((tmp_path / 'sessions.json').read_text()) == {}
    auth = BootstrapAuthority(tmp_path)
    assert not auth.sessions.valid(stale_token)
    assert auth.sessions.valid(auth.sessions.issue())


def test_reissuance_does_not_require_an_old_named_proof(tmp_path):
    # The current authority reconstructs the proof without relying on an old file.
    proof = provision(tmp_path)
    saved = proof.read_bytes()
    proof.unlink()
    assert not (tmp_path / PROOF_LINK_NAME).exists()
    assert provision(tmp_path) == proof
    assert proof.read_bytes() == saved
    assert BootstrapAuthority(tmp_path).sessions.proof_current()


def test_other_windows_user_cannot_be_modeled_by_disposable_directory(tmp_path):
    # This suite deliberately does not certify actual separate-user Windows DACL
    # separation; existing Windows cookie/ACL acceptance covers that boundary.
    assert PROOF_NAME == 'AAA_AUTH_MAIN_COMPUTER.json'
