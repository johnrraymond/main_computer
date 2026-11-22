"""Acceptance of ./start-managed MC authentication without touching installed services."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

from main_computer.auth_bootstrap import BootstrapAuthority, COOKIE, proof_file_path
from main_computer.auth_bootstrap_service import preflight, probe, PID_NAME, _LoopbackTLS
from main_computer.service_supervisor import ServiceSupervisor


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return int(sock.getsockname()[1])


def _fingerprints(root: Path) -> dict[str, str]:
    hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
              for name in ('authority.json', 'tls-cert.pem', 'tls-key.pem')}
    hashes['auth-proof'] = hashlib.sha256(proof_file_path(root).read_bytes()).hexdigest()
    return hashes


def test_bootstrap_preflight_is_idempotent_and_sessions_persist(tmp_path):
    state = tmp_path / 'protected-state'
    proof, cert, key = preflight(state=state)
    assert proof.is_file() and cert.is_file() and key.is_file()
    baseline = _fingerprints(state)
    auth = BootstrapAuthority(state)
    token = auth.sessions.issue()
    assert auth.sessions.valid(token)
    proof2, cert2, key2 = preflight(state=state)
    assert (proof2, cert2, key2) == (proof, cert, key)
    assert _fingerprints(state) == baseline
    assert BootstrapAuthority(state).sessions.valid(token)


def test_supervisor_controls_auth_child_only_when_started_via_policy(tmp_path, monkeypatch):
    monkeypatch.delenv('MAIN_COMPUTER_AUTH_BOOTSTRAP_ENABLED', raising=False)
    disabled = ServiceSupervisor(root=tmp_path, python_command=sys.executable, output_func=None)
    assert 'auth' not in [spec.name for spec in disabled._start_ordered_specs()]
    monkeypatch.setenv('MAIN_COMPUTER_AUTH_BOOTSTRAP_ENABLED', '1')
    enabled = ServiceSupervisor(root=tmp_path, python_command=sys.executable, output_func=None)
    specs = enabled._start_ordered_specs()
    assert 'auth' in [spec.name for spec in specs]
    assert [spec.name for spec in specs].index('auth') < [spec.name for spec in specs].index('app')
    spec = next(spec for spec in specs if spec.name == 'auth')
    assert spec.command(python_command=sys.executable, root=tmp_path)[-1] == 'run'
    assert enabled._stop_ordered_children() == []


def test_actual_auth_process_start_stop_restart_preserves_session(tmp_path):
    root = tmp_path / 'checkout'
    root.mkdir()
    state = tmp_path / 'state'
    port = _free_port()
    backend = _free_port()
    while backend == port:
        backend = _free_port()
    preflight(state=state)
    stable = _fingerprints(state)
    cookie = BootstrapAuthority(state).sessions.issue()
    code_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, MAIN_COMPUTER_AUTH_BOOTSTRAP_PORT=str(port),
               MAIN_COMPUTER_CONTROL_PORT=str(backend),
               PYTHONPATH=str(code_root) + os.pathsep + os.environ.get('PYTHONPATH', ''))
    argv = [sys.executable, '-m', 'main_computer.auth_bootstrap_service',
            '--root', str(root), '--state-dir', str(state), 'run']
    for _index in range(2):
        process = subprocess.Popen(argv, cwd=str(root), env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 12
            while not probe(state=state, port=port) and time.monotonic() < deadline:
                assert process.poll() is None, process.stderr.read().decode(errors='replace')
                time.sleep(0.1)
            assert probe(state=state, port=port)
            assert int(json.loads((root / PID_NAME).read_text())['pid']) == process.pid
            assert BootstrapAuthority(state).sessions.valid(cookie)
            if _index == 1:
                # Revoking from a separate CLI process must invalidate the
                # already-running HTTPS server's cached session immediately.
                result = subprocess.run(
                    [sys.executable, '-m', 'tools.mcel_auth_bootstrap',
                     'revoke-all', '--state-dir', str(state)],
                    env=env, cwd=str(root), capture_output=True, text=True, timeout=8)
                assert result.returncode == 0, result.stderr
                connection = _LoopbackTLS(port, state / 'tls-cert.pem')
                try:
                    connection.request('GET', '/api/auth/bootstrap/session',
                        headers={'Host': f'mc.test:{port}', 'Cookie': f'{COOKIE}={cookie}'})
                    response = connection.getresponse()
                    assert response.status == 200
                    assert json.loads(response.read())['authenticated'] is False
                finally:
                    connection.close()
        finally:
            process.terminate()
            process.wait(timeout=8)
            process.stderr.close()
        assert not probe(state=state, port=port)
        assert _fingerprints(state) == stable
    assert not BootstrapAuthority(state).sessions.valid(cookie)


def test_wiring_does_not_launch_separate_unmanaged_service():
    root = Path(__file__).resolve().parents[1]
    start = (root / 'scripts' / 'main-computer-start-stop.ps1').read_text(encoding='utf-8-sig')
    browser = (root / 'scripts' / 'open-main-computer-browser.ps1').read_text()
    assert 'main_computer.auth_bootstrap_service --root $RootPath preflight' in start
    assert 'main_computer.auth_bootstrap_service --root $RootPath status' in start
    assert 'main_computer.auth_bootstrap_service' in start
    assert '.main_computer_auth_bootstrap.pid' in start
    assert 'main_computer.auth_bootstrap_service --root $resolvedRoot open' in browser
    assert 'Managed MC authentication service is not ready. Refusing insecure browser fallback.' in browser


def test_supervisor_auto_restarts_auth_and_stops_it(monkeypatch, tmp_path):
    monkeypatch.setenv('MAIN_COMPUTER_AUTH_BOOTSTRAP_ENABLED', '1')
    starts = {}
    terminated = []

    class FakeProcess:
        counter = 21000
        def __init__(self, polls):
            FakeProcess.counter += 1
            self.pid = FakeProcess.counter
            self.polls = list(polls)
        def poll(self):
            if len(self.polls) > 1:
                return self.polls.pop(0)
            return self.polls[0]

    def factory(command, cwd, stdout, stderr):
        module = command[2]
        starts[module] = starts.get(module, 0) + 1
        if module == 'main_computer.auth_bootstrap_service' and starts[module] == 1:
            return FakeProcess([None, 9])
        return FakeProcess([None])

    supervisor = ServiceSupervisor(
        root=tmp_path, python_command=sys.executable, process_factory=factory,
        process_terminator=terminated.append, sleep_func=lambda _: None,
        output_func=None,
    )
    state = supervisor.supervise(max_loops=3)
    assert starts['main_computer.auth_bootstrap_service'] == 2
    assert state['children']['auth']['restart_count'] == 1
    assert state['children']['auth']['last_exit_code'] == 9
    pid = state['children']['auth']['pid']
    supervisor._stop_children(reason='test-shutdown')
    assert pid in terminated
    assert 'auth' not in supervisor._children
