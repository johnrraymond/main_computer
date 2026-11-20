"""LMS /v1/local/status must remain responsive while deployment owns controller._lock.

Exercises the real HTTP route on a disposable LMS; never starts an ORG child,
contacts Coolify, or mutates production state.
"""
from __future__ import annotations

from http.client import HTTPConnection
import json
import os
from pathlib import Path
from threading import Event, Thread
from time import monotonic

from main_computer.slh.local_service import create_server
from main_computer.slh.organization_state import DEFAULTS, save_config


def test_http_status_does_not_reacquire_deployment_lock(tmp_path, monkeypatch):
    """The previous controller.status() fix left controller.config() in local_status()."""
    def protect(directory):
        path = Path(directory).resolve()
        if not path.is_relative_to(tmp_path.resolve()):
            raise PermissionError('Disposable state only')
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == 'posix':
            path.chmod(0o700)
        return path

    monkeypatch.setattr('main_computer.slh.local_service.secure_machine_state_dir', protect)
    # Prevent this test's disposable controller from trying to launch an ORG process.
    monkeypatch.setattr('main_computer.slh.organization_controller.OrganizationController._watch',
                        lambda self: self._stop.wait())
    with create_server(state_dir=tmp_path, port=0) as server:
        controller = server.organization_controller
        save_config(tmp_path, {**DEFAULTS, 'configured': True,
                               'enabled': True, 'activation_confirmed': True})
        # Status endpoint should take the enabled-controller branch; no need
        # to actually enroll an administrator to prove an HTTP lock property.
        monkeypatch.setattr(server.get_app().__self__.authority, 'initialized', lambda: True)
        monkeypatch.setattr(controller, 'status',
                            lambda: {'state': 'starting', 'enabled': True,
                                     'configured': True, 'listening': False})
        server_thread = Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        finished = Event()
        outcome = {}

        def fetch():
            conn = HTTPConnection('127.0.0.1', server.server_port, timeout=1.5)
            start = monotonic()
            try:
                conn.request('GET', '/v1/local/status')
                response = conn.getresponse()
                outcome['status'] = response.status
                outcome['data'] = json.loads(response.read())
            except Exception as exc:
                outcome['error'] = type(exc).__name__
            finally:
                outcome['seconds'] = monotonic() - start
                conn.close()
                finished.set()

        worker = Thread(target=fetch, daemon=True)
        try:
            with controller._lock:
                worker.start()
                completed_during_deploy = finished.wait(.75)
            assert completed_during_deploy, 'HTTP status route blocked behind deployment lock'
            assert outcome.get('status') == 200, outcome
            assert outcome['data']['organization_service']['state'] == 'starting'
            assert outcome['seconds'] < .75, outcome
        finally:
            worker.join(timeout=2)
            server.shutdown()
            server_thread.join(timeout=3)
