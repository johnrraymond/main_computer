"""Opening the managed browser must be an observable proof/session check.

All state and browser subprocesses are disposable. No real user profile touched.
"""
from __future__ import annotations

import json
from main_computer import auth_bootstrap_service as service
from main_computer.auth_bootstrap import BootstrapAuthority, provision
from tools.mcel_auth_bootstrap_smoke import request, running


def _capture_browser(monkeypatch):
    launched = []

    def fake_popen(argv, **kwargs):
        launched.append((argv, kwargs))
        return object()

    monkeypatch.setattr(service, 'wait_for_probe', lambda **_kw: (True, 'SESSION_API_VERIFIED'))
    monkeypatch.setattr(service, '_chromium', lambda: 'fake-chromium')
    monkeypatch.setattr(service.subprocess, 'Popen', fake_popen)
    return launched


def test_open_reconciles_deleted_aaa_and_forces_fresh_login(monkeypatch, tmp_path, capsys):
    proof = provision(tmp_path)
    authority = BootstrapAuthority(tmp_path)
    token = authority.sessions.issue()
    assert authority.sessions.valid(token)
    proof.unlink()

    launched = _capture_browser(monkeypatch)
    service.open_managed_browser(state=tmp_path, port=8875)

    assert proof.is_file(), 'trusted local open must reissue the missing MC proof'
    assert not authority.sessions.valid(token), 'deleted AAA must revoke existing session'
    assert json.loads((tmp_path / 'sessions.json').read_text()) == {}
    assert len(launched) == 1
    argv = launched[0][0]
    assert '--new-window' in argv
    assert argv[-1] == 'https://mc.test:8875/auth/login?next=%2Fgraphical'
    output = capsys.readouterr().out
    assert f'proof-file={proof}' in output
    assert 'proof-status=REISSUED_REAUTH_REQUIRED' in output
    assert 'sessions-invalidated' in output


def test_open_preserves_session_when_aaa_exists(monkeypatch, tmp_path, capsys):
    proof = provision(tmp_path)
    authority = BootstrapAuthority(tmp_path)
    token = authority.sessions.issue()
    launched = _capture_browser(monkeypatch)
    service.open_managed_browser(state=tmp_path, port=8875)
    assert proof.is_file()
    assert authority.sessions.valid(token)
    assert '--new-window' in launched[0][0]
    assert 'proof-status=AVAILABLE' in capsys.readouterr().out


def test_deleted_aaa_blocks_cookie_navigation_and_challenge(tmp_path):
    proof = provision(tmp_path)
    with running(tmp_path) as (port, cert, _thumb):
        authority = BootstrapAuthority(tmp_path)
        raw_cookie = authority.sessions.issue()
        from main_computer.auth_bootstrap import COOKIE
        cookie = f'{COOKIE}={raw_cookie}'
        status, session, _ = request(port, cert, 'GET', '/api/auth/bootstrap/session', cookie=cookie)
        assert status == 200 and session['authenticated'] is True
        assert session['proof_available'] is True
        proof.unlink()
        status, session, _ = request(port, cert, 'GET', '/api/auth/bootstrap/session', cookie=cookie)
        assert status == 200 and session['authenticated'] is False
        assert session['proof_available'] is True
        assert session['proof_reissued'] is True
        assert proof.is_file()
        status, _, hdrs = request(port, cert, 'GET', '/graphical', cookie=cookie)
        assert status == 303 and hdrs['Location'].startswith('/auth/login')
        status, _, _ = request(port, cert, 'GET', '/auth/login', cookie=cookie)
        assert status == 200
        status, ch, _ = request(port, cert, 'GET', '/api/auth/bootstrap/challenge')
        assert status == 200 and ch['algorithm'] == 'HMAC-SHA256'
        assert proof.is_file()


def test_restored_file_does_not_restore_old_cookie(tmp_path):
    proof = provision(tmp_path)
    saved = proof.read_bytes()
    authority = BootstrapAuthority(tmp_path)
    old_token = authority.sessions.issue()
    proof.unlink()
    assert not authority.sessions.valid(old_token)
    proof.write_bytes(saved)
    assert not authority.sessions.valid(old_token)
    new_token = authority.sessions.issue()
    assert authority.sessions.valid(new_token)
    assert not authority.sessions.valid(old_token)


def test_login_js_displays_missing_proof_explicitly():
    from main_computer import auth_bootstrap
    script = (auth_bootstrap.Path(auth_bootstrap.__file__).parent / 'web' / 'auth_login.js').read_text()
    assert 'result.proof_available === false' in script
    assert 'result.proof_reissued === true' in script
    assert 'regenerated the MC proof file' in script
    assert 'pickButton.disabled = true;' in script


def test_open_reissues_before_readiness_when_supervisor_is_still_starting(monkeypatch, tmp_path):
    import pytest
    proof = provision(tmp_path)
    authority = BootstrapAuthority(tmp_path)
    token = authority.sessions.issue()
    proof.unlink()

    def verify_before_probe(**_kwargs):
        assert proof.is_file(), 'reissue must precede HTTPS readiness'
        assert not authority.sessions.valid(token)
        return False, 'TCP_CONNECTION_REFUSED'

    monkeypatch.setattr(service, 'wait_for_probe', verify_before_probe)
    with pytest.raises(RuntimeError, match='AUTH_BOOTSTRAP_NOT_READY'):
        service.open_managed_browser(state=tmp_path, port=8875)
    assert proof.is_file()
