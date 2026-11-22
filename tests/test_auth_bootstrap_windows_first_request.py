"""Regression: cold Windows proof-link initialization must not block HTTPS handlers.

Uses disposable authority/TLS state. No production certificate or Windows profile
is touched. The fixture joins request threads before tempfile cleanup on Windows.
"""
from __future__ import annotations

import threading
import time

from main_computer import auth_bootstrap as auth
from tools.mcel_auth_bootstrap_smoke import request, running


def test_proof_link_is_initialized_before_first_http_request(tmp_path, monkeypatch):
    auth.provision(tmp_path)
    observed_writers = []
    original_replace = auth._replace_private

    def observe_replace(path, data):
        if path.name == auth.PROOF_LINK_NAME:
            observed_writers.append(threading.get_ident())
            # Simulate the slower Windows ACL work performed during creation.
            time.sleep(0.15)
        return original_replace(path, data)

    monkeypatch.setattr(auth, '_replace_private', observe_replace)
    main_thread = threading.get_ident()
    with running(tmp_path) as (port, cert, _):
        assert observed_writers == [main_thread]
        assert (tmp_path / auth.PROOF_LINK_NAME).is_file()

        def reject_unexpected_link_write(path, data):
            if path.name == auth.PROOF_LINK_NAME:
                raise AssertionError('First HTTP handler attempted proof-link creation')
            return original_replace(path, data)

        monkeypatch.setattr(auth, '_replace_private', reject_unexpected_link_write)
        status, _, headers = request(port, cert, 'GET', '/graphical')
        assert status == 303
        assert headers['Location'].startswith('/auth/login')


def test_smoke_fixture_joins_active_request_before_lockfile_cleanup(tmp_path, monkeypatch):
    auth.provision(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = auth.SessionStore.proof_current

    def stall_request_only(self):
        if threading.current_thread() is not threading.main_thread():
            entered.set()
            if not release.wait(5):
                raise AssertionError('Test failed to release simulated slow handler')
        return original(self)

    monkeypatch.setattr(auth.SessionStore, 'proof_current', stall_request_only)
    worker_result = []
    with running(tmp_path) as (port, cert, _):
        def client():
            try:
                status, _, _ = request(port, cert, 'GET', '/graphical')
                worker_result.append(status)
            except Exception as exc:
                worker_result.append(exc)

        worker = threading.Thread(target=client)
        worker.start()
        assert entered.wait(3), 'HTTP request never reached the handler'
        timer = threading.Timer(1.25, release.set)
        timer.start()
        start = time.monotonic()
    elapsed = time.monotonic() - start
    timer.join(timeout=2)
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert worker_result == [303]
    assert elapsed >= 1.0, 'Disposable server closed before its request finished'
    # This file was the source of WinError 32 during TemporaryDirectory cleanup.
    # Once the handler is finished it must be immediately deletable on Windows.
    (tmp_path / auth.PROOF_LOCK_NAME).unlink()
