"""Redeploy-time LMS status isolation: no controller-lock head-of-line stalls."""
from collections import deque
import threading
import time

from main_computer.slh import organization_controller as module
from main_computer.slh.organization_controller import OrganizationController
from main_computer.slh.organization_state import DEFAULTS, save_config


class Process:
    def __init__(self, pid):
        self.pid = pid
        self.alive = True

    def poll(self):
        return None if self.alive else 0


def controller(tmp_path, monkeypatch):
    save_config(tmp_path, {**DEFAULTS, 'configured': True,
                           'enabled': True, 'activation_confirmed': True})
    ctl = OrganizationController.__new__(OrganizationController)
    ctl.directory = tmp_path
    ctl._lock = threading.RLock()
    ctl._process = Process(8001)
    ctl._paused = False
    ctl._last_error = None
    ctl._restarts = deque()
    ctl._listening = lambda cfg: True
    monkeypatch.setattr(module, 'verified_launched_pid',
                        lambda *args, **kwargs: 8001)
    return ctl


def test_redeploy_controller_lock_does_not_block_read_only_status(tmp_path, monkeypatch):
    ctl = controller(tmp_path, monkeypatch)
    done = threading.Event()
    outcome = []

    def read_status():
        try:
            outcome.append(ctl.status())
        finally:
            done.set()

    # Models an approved deployment holding the mutation lock during a
    # remote operation. The old implementation blocked here until release.
    thread = threading.Thread(target=read_status, daemon=True)
    with ctl._lock:
        thread.start()
        finished_while_deploy_active = done.wait(timeout=0.8)
    thread.join(timeout=1)
    assert finished_while_deploy_active, 'Status queued behind deployment lock'
    assert outcome[0]['state'] == 'running'
    assert outcome[0]['listening'] is True


def test_one_slow_tls_status_observation_does_not_serialize_other_reads(tmp_path, monkeypatch):
    ctl = controller(tmp_path, monkeypatch)
    entered = threading.Event()
    resume = threading.Event()
    original = ctl._listening
    result = []

    def delayed(config):
        if threading.current_thread().name == 'slow-status':
            entered.set()
            assert resume.wait(timeout=3)
        return original(config)

    ctl._listening = delayed
    slow = threading.Thread(target=lambda: result.append(ctl.status()),
                            name='slow-status', daemon=True)
    slow.start()
    try:
        assert entered.wait(1)
        t0 = time.monotonic()
        # A second status check has its own TLS admission path; never wait
        # behind another request's possibly slow ORG-SLH connection.
        fast = ctl.status()
        elapsed = time.monotonic() - t0
        assert elapsed < 0.6, f'Independent status request blocked {elapsed:.3f}s'
        assert fast['state'] == 'running'
    finally:
        resume.set()
        slow.join(timeout=2)
    assert not slow.is_alive()
    assert result[0]['state'] == 'running'


def test_replaced_child_during_status_check_cannot_prove_current_listener(tmp_path, monkeypatch):
    ctl = controller(tmp_path, monkeypatch)
    entered = threading.Event()
    resume = threading.Event()
    result = []
    cleared = []
    monkeypatch.setattr(module, 'clear_listener_bind_failure',
                        lambda directory: cleared.append(directory))

    def delayed(config):
        entered.set()
        assert resume.wait(timeout=3)
        return True  # Positive response from the *old* child.

    ctl._listening = delayed
    thread = threading.Thread(target=lambda: result.append(ctl.status()), daemon=True)
    thread.start()
    try:
        assert entered.wait(1)
        ctl._process = Process(8002)
    finally:
        resume.set()
        thread.join(timeout=2)
    assert not thread.is_alive()
    assert result[0]['state'] != 'running'
    assert result[0]['listening'] is False
    assert result[0]['pid'] is None
    assert not cleared, 'Stale TLS evidence cleared failure from replacement child'


def test_configuration_change_during_tls_probe_cannot_claim_running(tmp_path, monkeypatch):
    ctl = controller(tmp_path, monkeypatch)
    entered = threading.Event()
    resume = threading.Event()
    result = []

    def delayed(config):
        entered.set()
        assert resume.wait(timeout=3)
        return True

    ctl._listening = delayed
    thread = threading.Thread(target=lambda: result.append(ctl.status()), daemon=True)
    thread.start()
    try:
        assert entered.wait(1)
        save_config(tmp_path, {**DEFAULTS, 'configured': True, 'enabled': False})
    finally:
        resume.set()
        thread.join(timeout=2)
    assert not thread.is_alive()
    assert result[0]['listening'] is False
    assert result[0]['state'] != 'running'
    assert result[0]['enabled'] is False


def test_status_does_not_clear_failure_record_during_deployment(tmp_path, monkeypatch):
    ctl = controller(tmp_path, monkeypatch)
    cleared = []
    monkeypatch.setattr(module, 'clear_listener_bind_failure',
                        lambda directory: cleared.append(directory))
    values = []
    worker = threading.Thread(target=lambda: values.append(ctl.status()), daemon=True)
    with ctl._lock:
        worker.start()
        worker.join(timeout=0.8)
        observed_while_busy = bool(values)
        cleared_while_busy = bool(cleared)
    worker.join(timeout=1)
    assert observed_while_busy, 'Readiness must not wait for deployment lock'
    assert not cleared_while_busy, 'Readiness must not mutate failure evidence during deployment'
    assert values[0]['state'] == 'running'
    # Once the mutation lock is free, a new verified TLS observation may clear
    # the historical bind failure through the existing secured path.
    assert ctl.status()['state'] == 'running'
    assert cleared == [tmp_path]
