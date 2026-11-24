"""Resident HTTPS Main Computer authentication bootstrap, owned by Main Computer's supervisor.

Windows ./start calls `preflight` before replacing running services. The resident
supervisor starts `run`, restarts it on failure, and stops it with ./stop.
Authentication state is deliberately stored outside the checkout and is NEVER
removed by stopping or restarting a process. Existing HTTP ingress is still
unsecured until the later application security cutover.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
import http.client

from main_computer.auth_bootstrap import (
    BootstrapAuthority, BootstrapServer, DEFAULT_PORT, HOST, proof_file_path,
    _private_folder, ensure_windows_user_trust, generate_tls_certificate,
    provision, recover_deleted_proof_locally, state_directory,
)

PID_NAME = '.main_computer_auth_bootstrap.pid'


def _port(name: str, fallback: int) -> int:
    raw = str(os.environ.get(name, '')).strip()
    try:
        value = int(raw) if raw else fallback
    except ValueError as exc:
        raise ValueError(f'INVALID_{name}') from exc
    if not 1024 <= value <= 65535:
        raise ValueError(f'INVALID_{name}')
    return value


def preflight(*, state: Path) -> tuple[Path, Path, Path]:
    """Idempotent provisioning; never regenerate an existing key or session."""
    started = time.perf_counter()
    previous = started
    def stage(name):
        nonlocal previous
        now = time.perf_counter()
        print(f'MC_AUTH_BOOTSTRAP timing {name}={(now-previous)*1000:.0f}ms total={(now-started)*1000:.0f}ms', flush=True)
        previous = now
    proof = provision(state)
    # Initial startup uses the same private-authority reissue rule as the live
    # HTTPS service. Revocation precedes recreation under the session lock.
    if not proof.is_file():
        proof, recovered = recover_deleted_proof_locally(state)
        if recovered:
            print('MC_AUTH_BOOTSTRAP AAA_REISSUED old-sessions-revoked reauthentication-required', flush=True)
    stage("proof")
    cert, _key, thumb = generate_tls_certificate(state)
    stage("certificate")
    ensure_windows_user_trust(cert, thumb)
    stage("trust")
    authority = BootstrapAuthority(state)  # ACL validation and durable session loading
    # Reconcile deletion/replacement even when no browser request is pending.
    # This also prevents startup from silently preserving a cookie after the
    # proof was removed while MC was stopped.
    authority.sessions.proof_current()
    stage("session")
    return proof, cert, _key


class _LoopbackTLS(http.client.HTTPSConnection):
    def __init__(self, port: int, cert: Path):
        context = ssl.create_default_context(cafile=str(cert))
        # Connection establishment and health-response reads have different
        # deadlines. A valid request may queue briefly behind a private-state
        # write; treating that as a dead service causes false startup warnings.
        super().__init__(HOST, port, context=context, timeout=12)
        self.target_port = port

    def connect(self) -> None:
        sock = socket.create_connection(('127.0.0.1', self.target_port), timeout=2.5)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=HOST)
            self.sock.settimeout(self.timeout)
        except BaseException:
            sock.close()
            raise


def probe_detail(*, state: Path, port: int) -> tuple[bool, str]:
    """Diagnose the EXACT HTTPS readiness boundary, without weakening TLS.

    A browser can load /auth/login while the session API is broken, and the
    Windows certificate store can trust a certificate different from the one
    in this CLI's private auth state. Neither condition should be hidden by a
    generic NOT_READY message. No keys or session tokens are printed.
    """
    cert = state / ('tls-cert-localhost.pem' if os.environ.get('MAIN_COMPUTER_HARDENED_CANDIDATE') == '1' else 'tls-cert.pem')
    if not cert.is_file():
        return False, 'TLS_CERT_FILE_MISSING'
    try:
        connection = _LoopbackTLS(port, cert)
    except (OSError, ValueError, ssl.SSLError) as exc:
        return False, f'TLS_CONTEXT_FAILED:{type(exc).__name__}:{exc}'
    try:
        connection.request('GET', '/api/auth/bootstrap/session', headers={
            'Host': f'{HOST}:{port}', 'Accept': 'application/json'})
        response = connection.getresponse()
        status = response.status
        body = response.read(2048)
        if status != 200:
            return False, f'SESSION_HTTP_STATUS:{status}'
        try:
            record = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return False, 'SESSION_INVALID_JSON'
        if not isinstance(record, dict) or record.get('ok') is not True or not isinstance(record.get('authenticated'), bool):
            return False, 'SESSION_UNEXPECTED_RESPONSE'
        return True, 'SESSION_API_VERIFIED'
    except ssl.SSLCertVerificationError as exc:
        return False, f'TLS_CERTIFICATE_VERIFY_FAILED:{exc.verify_message}'
    except ssl.SSLError as exc:
        return False, f'TLS_HANDSHAKE_FAILED:{type(exc).__name__}:{exc}'
    except ConnectionRefusedError:
        return False, 'TCP_CONNECTION_REFUSED'
    except (TimeoutError, socket.timeout):
        return False, 'CONNECTION_TIMEOUT'
    except (OSError, ValueError, http.client.HTTPException) as exc:
        return False, f'HTTPS_SESSION_REQUEST_FAILED:{type(exc).__name__}:{exc}'
    finally:
        connection.close()


def probe(*, state: Path, port: int) -> bool:
    return probe_detail(state=state, port=port)[0]


def wait_for_probe(*, state: Path, port: int, wait_seconds: float) -> tuple[bool, str]:
    """Wait through a supervised startup race, but never accept a bad reply."""
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        ready, detail = probe_detail(state=state, port=port)
        if ready or time.monotonic() >= deadline:
            return ready, detail
        time.sleep(min(0.4, max(0.0, deadline - time.monotonic())))


def _chromium() -> str:
    choices = [os.environ.get('MC_AUTH_BOOTSTRAP_BROWSER'), shutil.which('msedge'),
               shutil.which('chrome'), shutil.which('chromium')]
    for parent in (os.environ.get('PROGRAMFILES'), os.environ.get('PROGRAMFILES(X86)'),
                   os.environ.get('LOCALAPPDATA')):
        if parent:
            for suffix in ('Microsoft/Edge/Application/msedge.exe',
                           'Google/Chrome/Application/chrome.exe'):
                choices.append(str(Path(parent) / suffix))
    for candidate in choices:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise RuntimeError('CHROMIUM_OR_EDGE_NOT_FOUND (MC_AUTH_BOOTSTRAP_BROWSER may override)')


def open_managed_browser(*, state: Path, port: int) -> None:
    """Use MC's own browser profile; hostname mapping is browser-only."""
    # Local proof recovery runs before HTTPS readiness probing so the AAA
    # proof is reissued even when the resident service is still starting.
    proof, recovered = recover_deleted_proof_locally(state)
    ready, detail = wait_for_probe(state=state, port=port, wait_seconds=12)
    if not ready:
        raise RuntimeError(f'AUTH_BOOTSTRAP_NOT_READY reason={detail}')
    # `open` must be an observable authentication check, not merely a request
    # to show a possibly already-open /graphical tab. Reconcile the AAA proof
    # against the durable session store before launching the managed browser.
    # A deleted/replaced proof revokes prior cookies even if the HTTPS server
    # has received no requests since the file changed.
    available = BootstrapAuthority(state).sessions.proof_current()
    print(f'MC_AUTH_BOOTSTRAP proof-file={proof}', flush=True)
    proof_status = 'REISSUED_REAUTH_REQUIRED' if recovered else ('AVAILABLE' if available else 'INVALID')
    print(f'MC_AUTH_BOOTSTRAP proof-status={proof_status}', flush=True)
    if recovered:
        print('MC_AUTH_BOOTSTRAP sessions-invalidated; AAA proof reissued from the protected MC authority; '
              'select it to authenticate again', flush=True)
    elif not available:
        print('MC_AUTH_BOOTSTRAP proof-invalid; repair requires explicit local intervention', flush=True)
    browser_host = HOST
    if os.environ.get('MAIN_COMPUTER_HARDENED_CANDIDATE') == '1':
        from main_computer.auth_hostnames import canonical_host
        browser_host = canonical_host(os.environ.get('MAIN_COMPUTER_INSTALLATION_MODE', 'dev'))
        if browser_host == canonical_host('onlyoffice'):
            raise RuntimeError('INVALID_BROWSER_APPLICATION_MODE')
        os.startfile(f'https://{browser_host}:{port}/auth/login?next=%2Fgraphical')
        return
    profile = _private_folder(state / 'browser-profile')
    argv = [
        _chromium(), f'--user-data-dir={profile}',
        '--host-resolver-rules=MAP mc.test 127.0.0.1, EXCLUDE localhost',
        '--no-first-run', '--no-default-browser-check', '--new-window',
        # An existing valid session redirects straight back to /graphical.
        # A missing/replaced AAA proof cannot use an old session cookie.
        f'https://{browser_host}:{port}/auth/login?next=%2Fgraphical',
    ]
    subprocess.Popen(argv, cwd=str(state), stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)


def monitor_proof_file(authority: BootstrapAuthority, stop: threading.Event,
                       *, interval: float = 1.0) -> None:
    """Notice AAA deletion even while no browser request is in flight.

    The resident MC-owned service reissues from its private authority.
    Every protected HTTP request independently checks proof state, so a Bob
    request cannot exploit the polling interval to reuse an old session.
    """
    while not stop.wait(interval):
        try:
            authority.sessions.proof_current()
        except (OSError, RuntimeError, ValueError, PermissionError) as exc:
            print(f'MC_AUTH_BOOTSTRAP WARNING proof-monitor-failed '
                  f'reason={type(exc).__name__}', flush=True)


def serve(*, root: Path, state: Path, port: int, backend_port: int) -> None:
    if port == backend_port:
        raise ValueError('AUTH_AND_BACKEND_PORT_MUST_DIFFER')
    _proof, cert, key = preflight(state=state)
    authority = BootstrapAuthority(state)
    with BootstrapServer(('127.0.0.1', port), authority, backend_port=backend_port,
                         cert=cert, key=key) as server:
        pid_file = root / PID_NAME
        payload = {'pid': os.getpid(), 'root': str(root), 'service': 'main_computer.auth_bootstrap_service'}
        pid_file.write_text(json.dumps(payload) + '\n', encoding='utf-8')

        def _on_term(_signum: int, _frame: object) -> None:
            # serve_forever() runs on the main thread; shutdown() must be called
            # from another thread to avoid deadlock.
            threading.Thread(target=server.shutdown, daemon=True).start()

        previous = {}
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                previous[signum] = signal.signal(signum, _on_term)
            except (ValueError, OSError):
                pass
        stop_proof_monitor = threading.Event()
        proof_monitor = threading.Thread(target=monitor_proof_file,
            args=(authority, stop_proof_monitor), name='mc-auth-proof-monitor', daemon=True)
        proof_monitor.start()
        try:
            print(f'MC_AUTH_BOOTSTRAP READY https://{HOST}:{port}/auth/login?next=%2Fgraphical', flush=True)
            print('MC_AUTH_BOOTSTRAP managed-by=main_computer.service_supervisor', flush=True)
            print('MC_AUTH_BOOTSTRAP backend-authenticated-candidate' if os.environ.get('MAIN_COMPUTER_HARDENED_CANDIDATE') == '1' else 'MC_AUTH_BOOTSTRAP existing-unsecured-HTTP-ingress-unchanged', flush=True)
            server.serve_forever(poll_interval=0.2)
        finally:
            stop_proof_monitor.set()
            proof_monitor.join(timeout=15)
            for signum, prior in previous.items():
                signal.signal(signum, prior)
            try:
                current = json.loads(pid_file.read_text(encoding='utf-8'))
                if current.get('pid') == os.getpid():
                    pid_file.unlink()
            except (OSError, ValueError):
                pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd(), help='MC checkout root')
    parser.add_argument('--state-dir', type=Path, default=None, help='Isolated test state, never the repo runtime')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('preflight')
    commands.add_parser('run')
    status = commands.add_parser('status')
    status.add_argument('--wait-seconds', type=float, default=0)
    commands.add_parser('open')
    opts = parser.parse_args(argv)
    try:
        root = opts.root.resolve()
        if os.name != 'nt' and opts.state_dir is None:
            raise RuntimeError('WINDOWS_REQUIRED_FOR_REAL_AUTH_STATE')
        state = opts.state_dir or state_directory()
        port = _port('MAIN_COMPUTER_AUTH_BOOTSTRAP_PORT', DEFAULT_PORT)
        backend_port = _port('MAIN_COMPUTER_CONTROL_PORT', 8765)
        if port == backend_port:
            raise ValueError('AUTH_AND_BACKEND_PORT_MUST_DIFFER')
        if opts.command == 'preflight':
            proof, cert, _key = preflight(state=state)
            print('MC_AUTH_BOOTSTRAP preflight PASS (persistent state preserved)', flush=True)
            print(f'MC_AUTH_BOOTSTRAP proof-file={proof}', flush=True)
            if not proof.is_file():
                print('MC_AUTH_BOOTSTRAP WARNING auth-proof-missing; recovery not completed', flush=True)
            print(f'MC_AUTH_BOOTSTRAP managed-https-url=https://{HOST}:{port}/auth/login', flush=True)
        elif opts.command == 'run':
            serve(root=root, state=state, port=port, backend_port=backend_port)
        elif opts.command == 'open':
            open_managed_browser(state=state, port=port)
            print('MC_AUTH_BOOTSTRAP browser-open-requested PASS', flush=True)
        elif opts.command == 'status':
            ready, detail = wait_for_probe(state=state, port=port, wait_seconds=opts.wait_seconds)
            print(f'MC_AUTH_BOOTSTRAP status {"READY" if ready else "UNAVAILABLE"} https://{HOST}:{port}/auth/login reason={detail}', flush=True)
            if not ready:
                return 1
        return 0
    except Exception as exc:
        print(f'MC_AUTH_BOOTSTRAP ERROR {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
