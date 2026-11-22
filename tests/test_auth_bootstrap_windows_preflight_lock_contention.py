"""Windows ./start preflight must survive a busy proof/session lock.

The Windows C runtime's LK_LOCK aborts with EDEADLOCK after 10 tries.
All Windows locking behaviors here are tested with a deterministic CRT stub,
and the integration test exercises the real filesystem lock on this platform.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import errno
import threading
import time

import pytest

from main_computer.auth_bootstrap import (
    BootstrapAuthority, SessionStore, _acquire_windows_proof_lock,
)
from main_computer.auth_bootstrap_service import preflight


class FakeWindowsLocking:
    LK_NBLCK = 2
    LK_UNLCK = 0

    def __init__(self, *, busy_attempts=0, error_code=36):
        self.busy_attempts = busy_attempts
        self.error_code = error_code
        self.calls = []

    def locking(self, fd, mode, nbytes):
        self.calls.append((fd, mode, nbytes))
        if len(self.calls) <= self.busy_attempts:
            raise OSError(self.error_code, 'Resource deadlock avoided')


def test_windows_preflight_lock_survives_more_than_ten_contended_attempts():
    stub = FakeWindowsLocking(busy_attempts=14, error_code=36)
    _acquire_windows_proof_lock(123, lock_module=stub, wait_seconds=3, retry_seconds=0.001)
    assert len(stub.calls) == 15
    assert all(mode == stub.LK_NBLCK for _, mode, _ in stub.calls)


def test_windows_preflight_lock_retries_normal_lock_violations():
    for error_code in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
        stub = FakeWindowsLocking(busy_attempts=2, error_code=error_code)
        _acquire_windows_proof_lock(123, lock_module=stub, wait_seconds=2, retry_seconds=0.001)
        assert len(stub.calls) == 3


def test_windows_preflight_lock_fails_closed_on_permanent_contention():
    stub = FakeWindowsLocking(busy_attempts=10000)
    with pytest.raises(TimeoutError, match='AUTH_BOOTSTRAP_PROOF_LOCK_TIMEOUT'):
        _acquire_windows_proof_lock(123, lock_module=stub, wait_seconds=0.045,
                                    retry_seconds=0.005)
    assert len(stub.calls) > 1
    assert all(mode == stub.LK_NBLCK for _, mode, _ in stub.calls)


def test_windows_preflight_lock_does_not_retry_invalid_descriptor():
    stub = FakeWindowsLocking(busy_attempts=100, error_code=errno.EBADF)
    with pytest.raises(OSError) as error:
        _acquire_windows_proof_lock(123, lock_module=stub, wait_seconds=2,
                                    retry_seconds=0.001)
    assert error.value.errno == errno.EBADF
    assert len(stub.calls) == 1


def test_preflight_waits_for_running_service_lock_and_preserves_session(tmp_path):
    state = tmp_path / 'auth-state'
    preflight(state=state)
    original = BootstrapAuthority(state)
    old_session = original.sessions.issue()
    assert original.sessions.valid(old_session)
    holding = threading.Event()
    release = threading.Event()

    def hold_lock():
        with original.sessions.lock, original.sessions._exclusive():
            holding.set()
            assert release.wait(timeout=10)

    with ThreadPoolExecutor(max_workers=2) as pool:
        holder = pool.submit(hold_lock)
        assert holding.wait(timeout=5)
        runner = pool.submit(preflight, state=state)
        time.sleep(0.08)
        assert not runner.done(), 'preflight must not ignore an existing exclusive lock'
        release.set()
        holder.result(timeout=10)
        proof, cert, tls_key = runner.result(timeout=15)
    assert proof.is_file() and cert.is_file() and tls_key.is_file()
    assert BootstrapAuthority(state).sessions.valid(old_session)
