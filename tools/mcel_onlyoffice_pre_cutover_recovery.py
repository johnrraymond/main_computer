"""Bounded, observable verification of disposable Windows proof-file recovery.

The real BootstrapAuthority applies Windows ACLs when a deleted proof is
reissued. A standard 8-10-second HTTP timeout is not the recovery deadline.
This module changes TEST HARNESS timing only; it never changes the authority,
its lock, its ACL policy, or the expected fail-closed HTTP response.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from typing import Callable, TypeVar

T = TypeVar('T')


def await_reissue_http(request: Callable[[], T], *, process: subprocess.Popen | None = None,
                       label: str, deadline_seconds: float = 90.0,
                       progress_seconds: float = 10.0) -> T:
    """Run a single real HTTP recovery probe within an explicit wall-time bound.

    The actual socket should have its own timeout >= deadline_seconds. Do NOT
    retry the request: retries could mask a torn or partially committed proof
    generation. Failure or lack of evidence must not be counted as a PASS.
    """
    if not 0 < progress_seconds <= deadline_seconds:
        raise ValueError('invalid recovery reporting interval')
    result: list[T] = []
    error: list[Exception] = []
    finished = threading.Event()

    def work() -> None:
        try:
            result.append(request())
        except Exception as exc:
            error.append(exc)
        finally:
            finished.set()

    thread = threading.Thread(target=work, daemon=True,
                              name='mc-oo-disposable-proof-reissue-probe')
    started = time.monotonic()
    print(f'{label} START max_wait={deadline_seconds:g}s source-backed-reissue=true',flush=True)
    thread.start()
    while not finished.is_set():
        elapsed = time.monotonic() - started
        remaining = deadline_seconds - elapsed
        if remaining <= 0:
            raise TimeoutError(f'{label} TIMEOUT waited={elapsed:.1f}s '
                               'disposable proof recovery did not finish; certification BLOCKED')
        finished.wait(min(progress_seconds, remaining))
        if not finished.is_set():
            elapsed = time.monotonic() - started
            if process is not None and process.poll() is not None:
                raise RuntimeError(f'{label} FIXTURE_EXITED exit={process.returncode} '
                                   f'waited={elapsed:.1f}s; certification BLOCKED')
            print(f'{label} WAITING elapsed={elapsed:.1f}s; '
                  'Windows ACL reconciliation must finish before acceptance', flush=True)
    if error:
        raise RuntimeError(f'{label} REQUEST_FAILED error={type(error[0]).__name__}: {error[0]}') from error[0]
    if len(result) != 1:
        raise RuntimeError(f'{label} NO_RESPONSE certification BLOCKED')
    print(f'{label} COMPLETED elapsed={time.monotonic()-started:.1f}s', flush=True)
    return result[0]


def stop_disposable_fixture(process: subprocess.Popen, *, grace_seconds: float = 5.0) -> None:
    """Stop only the exact disposable child created by the calling smoke test.

    On Windows, ACL helpers are subprocess descendants. Killing only the lab
    Python parent during a timed-out request can strand PowerShell/icacls
    descendants with an open .proof-link.lock, causing WinError 32 on cleanup.
    The process tree is terminated before its private temp folder is removed.
    No process is discovered by name, port, or broad PID search.
    """
    if process.poll() is not None:
        return
    if os.name == 'nt':
        try:
            completed = subprocess.run(
                ['taskkill.exe', '/F', '/T', '/PID', str(process.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=12, check=False)
            # A racing normal exit is fine, but an unsuccessful tree kill with
            # a live parent must not silently be accepted.
            if completed.returncode != 0 and process.poll() is None:
                process.kill()
        except (OSError, subprocess.TimeoutExpired):
            if process.poll() is None:
                process.kill()
    else:
        process.terminate()
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=grace_seconds)
