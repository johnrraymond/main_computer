"""Regression: TCP connect timeout must not silently govern HTTP read timeout.

All network listeners, keys and certificates below are disposable loopback
fixtures; no production MC state is accessed.
"""
from __future__ import annotations

import json
import time

from main_computer import auth_bootstrap as auth
from main_computer.auth_bootstrap_service import probe_detail
from tools.mcel_auth_bootstrap_smoke import request, running, signed


def test_slow_but_valid_session_issuance_does_not_close_after_four_seconds(tmp_path, monkeypatch):
    proof = auth.provision(tmp_path)
    original_verify = auth.BootstrapAuthority.verify

    def finish_after_delayed_disk_write(self, cid, signature):
        token = original_verify(self, cid, signature)
        assert token is not None
        # Reproduce the real Windows trace: proof was accepted and session
        # was issued, but the client closed before the HTTP 200 response.
        time.sleep(4.35)
        return token

    monkeypatch.setattr(auth.BootstrapAuthority, 'verify', finish_after_delayed_disk_write)
    with running(tmp_path) as (port, cert, _):
        status, challenge, _ = request(port, cert, 'GET', '/api/auth/bootstrap/challenge')
        assert status == 200
        start = time.monotonic()
        status, body, headers = request(port, cert, 'POST', '/api/auth/bootstrap/verify',
            body={'challenge_id': challenge['challenge_id'],
                  'signature': signed(proof, challenge)})
        assert time.monotonic() - start >= 4.2
        assert status == 200 and body['authenticated'] is True
        assert headers['Set-Cookie'].startswith(auth.COOKIE + '=')


def test_readiness_probe_does_not_fail_on_slow_session_disk_lock(tmp_path, monkeypatch):
    auth.provision(tmp_path)
    original_valid = auth.SessionStore.valid

    def delayed_valid(self, token):
        time.sleep(3.0)
        return original_valid(self, token)

    monkeypatch.setattr(auth.SessionStore, 'valid', delayed_valid)
    with running(tmp_path) as (port, cert, _):
        ready, stage = probe_detail(state=tmp_path, port=port)
        assert ready is True and stage == 'SESSION_API_VERIFIED'
