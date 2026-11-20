"""Reproduces LMS timeout/503 cascade behind a slow unrelated request.

All network I/O is loopback against a disposable LMS; no real ORG or SEH
service, production credentials, or configuration are touched.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from http.client import HTTPConnection
from threading import Event, RLock, Thread
from time import monotonic
from types import SimpleNamespace

from main_computer.slh.local_service import create_server
from main_computer.slh.organization_controller import OrganizationController


def _get(port, path, timeout=1):
    conn = HTTPConnection('127.0.0.1', port, timeout=timeout)
    try:
        conn.request('GET', path)
        response = conn.getresponse()
        return response.status, json.loads(response.read())
    finally:
        conn.close()


def test_resident_lms_serves_identity_and_status_while_another_http_operation_is_blocked(tmp_path, monkeypatch):
    """The prior single-threaded ExclusiveSLHServer stalls until gate releases."""
    # Production applies machine ACLs; tests use a disposable directory that
    # must remain accessible to the interactive pytest user across requests.
    def protect(directory):
        directory = Path(directory).resolve()
        if not directory.is_relative_to(tmp_path.resolve()):
            raise PermissionError('Test must not change non-disposable SLH state')
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == 'posix':
            directory.chmod(0o700)
        return directory

    monkeypatch.setattr('main_computer.slh.local_service.secure_machine_state_dir', protect)
    entered, release = Event(), Event()
    with create_server(state_dir=tmp_path, port=0) as server:
        original = server.get_app()

        def slow_app(environ, start_response):
            if environ['PATH_INFO'] == '/test/blocked':
                entered.set()
                release.wait(3)
                payload = b'{}'
                start_response('200 OK', [('Content-Type', 'application/json'),
                                          ('Content-Length', str(len(payload)))])
                return [payload]
            return original(environ, start_response)

        server.set_app(slow_app)
        assert server.base_environ['wsgi.multithread'] is True
        assert server.allow_reuse_address is False
        assert server.allow_reuse_port is False
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        blocked = Thread(target=lambda: _get(server.server_port, '/test/blocked', timeout=4), daemon=True)
        blocked.start()
        try:
            assert entered.wait(2), 'Blocked request did not reach WSGI application'
            started = monotonic()
            code, status = _get(server.server_port, '/v1/local/status', timeout=1)
            first_duration = monotonic() - started
            assert code == 200 and status['local_service']['state'] == 'running'
            assert first_duration < .7, f'Unrelated status blocked for {first_duration:.3f}s'

            started = monotonic()
            code, identities = _get(server.server_port,
                                    '/v1/local/authenticator/identities', timeout=1)
            second_duration = monotonic() - started
            assert code == 200 and identities['identities']
            assert second_duration < .7, f'Identity lookup blocked for {second_duration:.3f}s'
        finally:
            release.set()
            blocked.join(3)
            server.shutdown()
            thread.join(3)
        assert not blocked.is_alive()


def _fake_controller():
    controller = object.__new__(OrganizationController)
    controller._lock = RLock()
    controller._process = None
    controller._paused = False
    controller.config = lambda: {
        'enabled': True, 'seh_enabled': True, 'workers_enabled': True,
    }
    return controller


def test_slow_seh_tls_observation_does_not_hold_controller_lock():
    """A 2s child TLS read must not delay routine local controller reads."""
    controller = _fake_controller()
    entered, release = Event(), Event()
    result = []

    def slow_child():
        entered.set()
        release.wait(3)
        return None

    controller._seh_child_status = slow_child
    worker = Thread(target=lambda: result.append(controller.seh_status()), daemon=True)
    worker.start()
    try:
        assert entered.wait(2), 'SEH observation did not begin'
        # Use a separate thread so a lock regression cannot hang the test runner.
        read_finished = Event()
        reader = Thread(target=lambda: (controller.config(), read_finished.set()), daemon=True)
        reader.start()
        assert read_finished.wait(.4), 'Controller lock held across SEH network I/O'
        reader.join(1)
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive()
    assert result[0]['status'] == 'NOT_CONNECTED'


def test_org_restart_during_seh_probe_discards_stale_child_evidence():
    """Releasing the lock must never certify a connection from a stopped child."""
    controller = _fake_controller()
    first = SimpleNamespace(poll=lambda: None)
    second = SimpleNamespace(poll=lambda: None)
    controller._process = first
    entered, release = Event(), Event()
    result = []

    def slow_child():
        entered.set()
        release.wait(3)
        return {
            'seh_enabled': True, 'seh_connection_verified': True,
            'private_requests_available': True, 'pinky_sweeper_available': True,
            'worker_serving_enabled': True,
        }

    controller._seh_child_status = slow_child
    worker = Thread(target=lambda: result.append(controller.seh_status()), daemon=True)
    worker.start()
    try:
        assert entered.wait(2)
        with controller._lock:
            controller._process = second
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive()
    assert result[0]['status'] == 'NOT_CONNECTED'
    assert result[0]['outbound_tunnel_authenticated'] is False
    assert result[0]['resident_child_observed'] is False
