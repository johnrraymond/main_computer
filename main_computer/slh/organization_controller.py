"""Localhost-only owner of the separately spawned SLH Organization Service."""
from __future__ import annotations

from collections import deque
import hashlib
import os
import psutil
import http.client
import json
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import threading
import time

from .organization_process import stop_recorded_process, verified_launched_pid
from .organization_state import (read_config, save_config, initialize_disabled,
                                 validate_config, OrganizationConfigError)
from .authority import SLHError, SLHAuthority


class OrganizationController:
    def __init__(self, authority: SLHAuthority, *, python_executable=None, runner=None,
                 automatic_start=True):
        self.authority = authority
        self.directory = authority.directory
        initialize_disabled(self.directory)
        self._lock = threading.RLock()
        self._process = None
        self._last_error = None
        self._paused = False  # Explicit runtime Stop, distinct from persistent Disable.
        self._stop = threading.Event()
        self._restarts = deque()
        self._python = python_executable or sys.executable
        self._runner = runner or subprocess.Popen
        self._monitor = None
        if automatic_start:
            with self._lock:
                try:
                    if (read_config(self.directory)['enabled']
                            and read_config(self.directory)['activation_confirmed']
                            and authority.initialized()):
                        self._launch()
                except (OSError, RuntimeError, OrganizationConfigError) as exc:
                    self._last_error = type(exc).__name__ + ': ' + str(exc)
            self._monitor = threading.Thread(target=self._watch, name='slh-organization-watch', daemon=True)
            self._monitor.start()

    def _launch(self):
        config = read_config(self.directory)
        if (not config['enabled'] or not config['configured'] or not config['activation_confirmed']
                or not self.authority.initialized()):
            raise SLHError('ORGANIZATION_NOT_READY', 409)
        if self._process is not None and self._process.poll() is None:
            return
        # Recover an orphan from a prior manager before launching a second
        # listener. Both UI and CLI teardown use this same identity record.
        stop_recorded_process(self.directory)
        logfile = self.directory / 'organization-service.log'
        with logfile.open('ab') as stream:
            self._process = self._runner(
                [self._python, '-m', 'main_computer.slh.organization_service', 'run',
                 '--state-dir', str(self.directory),
                 '--parent-pid', str(os.getpid()),
                 '--parent-created', str(psutil.Process(os.getpid()).create_time())],
                stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                close_fds=True)
        self._last_error = None

    def _listening(self, config):
        process = self._process
        if process is None or process.poll() is not None:
            return False
        try:
            sock = socket.create_connection(('127.0.0.1' if config['host'] == '0.0.0.0' else config['host'], config['api_port']), timeout=0.25)
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with ctx.wrap_socket(sock, server_hostname='localhost') as tls:
                peer = tls.getpeercert(binary_form=True)
                own_cert = self.directory / 'managed-tls-cert.pem'
                if not own_cert.exists():
                    return False
                expected = ssl.PEM_cert_to_DER_cert(own_cert.read_text(encoding='ascii'))
                if not hashlib.sha256(peer).digest() == hashlib.sha256(expected).digest():
                    return False
                tls.sendall(b'GET /v1/org/status HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
                return b'200' in tls.recv(64).split(b'\r\n', 1)[0]
        except (OSError, ssl.SSLError, ValueError):
            return False

    def status(self):
        with self._lock:
            try:
                config = read_config(self.directory)
            except OrganizationConfigError:
                return {'configured': False, 'enabled': False, 'state': 'configuration_error',
                        'listening': False, 'bind_host': None, 'api_port': None,
                        'network_access': 'UNVERIFIED',
                        'last_error': 'Invalid organization configuration'}
            proc = self._process
            running = proc is not None and proc.poll() is None
            # Windows venv python.exe can be a redirector: Popen.pid is then
            # the launcher, while the child records its real PID. Report only
            # a verified, controller-owned ORG-SLH process to callers.
            try:
                service_pid = (verified_launched_pid(
                    self.directory, launcher_pid=proc.pid,
                    controller_pid=os.getpid(),
                    controller_created=psutil.Process(os.getpid()).create_time())
                    if running else None)
            except (OSError, RuntimeError):
                service_pid = None
            listening = running and self._listening(config)
            if listening:
                state = 'running'
            elif running:
                state = 'starting'
            elif self._paused and config['enabled'] and config['configured']:
                state = 'stopped'
            elif self._last_error and config['enabled']:
                state = 'failed'
            elif config['enabled']:
                state = 'stopped_unexpectedly'
            elif config['configured']:
                state = 'disabled'
            else:
                state = 'not_configured'
            return {'configured': config['configured'], 'enabled': config['enabled'],
                    'state': state, 'listening': listening,
                    # An observed local TLS handshake proves only local readiness;
                    # binding a LAN interface does NOT prove remote reachability.
                    'bind_host': config['host'] if config['configured'] else None,
                    'network_access': ('NOT_CONFIGURED' if not config['configured'] else
                                       'LOOPBACK_ONLY' if config['host'].startswith('127.') else
                                       'BIND_CONFIGURED_REACHABILITY_UNVERIFIED'),
                    'pid': service_pid,
                    'api_port': config['api_port'] if config['configured'] else None,
                    'worker_port': config['worker_port'] if config['workers_enabled'] else None,
                    'workers_enabled': config['workers_enabled'],
                    'seh_enabled': config['seh_enabled'], 'last_error': self._last_error}

    def config(self):
        with self._lock:
            try:
                return read_config(self.directory)
            except OrganizationConfigError as exc:
                raise SLHError('ORGANIZATION_CONFIG_ERROR', 409) from exc

    def configure(self, changes):
        if not isinstance(changes, dict) or not changes or set(changes) - {
                'host', 'api_port', 'worker_port', 'workers_enabled', 'seh_enabled'}:
            raise SLHError('INVALID_ORGANIZATION_CONFIG', 400)
        with self._lock:
            cfg = self.config()
            if cfg['enabled']:
                raise SLHError('DISABLE_ORGANIZATION_BEFORE_RECONFIGURATION', 409)
            try:
                cfg = save_config(self.directory, {**cfg, **changes, 'configured': True, 'enabled': False})
            except OrganizationConfigError as exc:
                raise SLHError('INVALID_ORGANIZATION_CONFIG', 400) from exc
            return cfg

    def reconfigure_and_enable(self, changes):
        """One authorized, serialized bind change with bounded recovery.

        Persisted enablement is an operational policy, not a requirement that
        the administrator must manually disable before reconfiguring. Keep the
        controller lock across the transition so _watch cannot relaunch the old
        listener during the intentional restart. Do not alter authority, worker
        policy, SEH state or identity records.
        """
        if not isinstance(changes, dict) or not changes or set(changes) - {'host', 'api_port', 'worker_port'}:
            raise SLHError('INVALID_ORGANIZATION_CONFIG', 400)
        with self._lock:
            original = self.config()
            if not self.authority.initialized():
                raise SLHError('ORGANIZATION_NOT_READY', 409)
            try:
                desired = validate_config({**original, **changes,
                                           'configured': True, 'enabled': True,
                                           'activation_confirmed': True})
            except OrganizationConfigError as exc:
                raise SLHError('INVALID_ORGANIZATION_CONFIG', 400) from exc
            same_listener = all(original[k] == desired[k] for k in
                                ('host', 'api_port', 'worker_port', 'workers_enabled', 'seh_enabled'))
            if original['enabled'] and same_listener and self._listening(original):
                return self.status()

            was_paused = self._paused
            self._restarts.clear()
            try:
                self._terminate()
                save_config(self.directory, desired)
                self._paused = False
                self._launch()
                if not self._await_listening(desired):
                    raise SLHError('ORGANIZATION_START_UNVERIFIED', 503)
                self._last_error = None
                return self.status()
            except Exception as exc:
                # Never leave the requested listener persisted if it did not
                # become ready. Restore the old policy, including disabled.
                recovery_failed = False
                try:
                    self._terminate()
                    save_config(self.directory, original)
                    self._paused = was_paused
                    if original['enabled'] and not was_paused:
                        self._launch()
                        if not self._await_listening(original):
                            recovery_failed = True
                    self._restarts.clear()
                except Exception:
                    recovery_failed = True
                if recovery_failed:
                    self._last_error = 'Organization reconfiguration failed; old listener recovery failed'
                    raise SLHError('ORGANIZATION_RECONFIGURE_RECOVERY_FAILED', 503) from exc
                if not original['enabled']:
                    self._last_error = None
                raise SLHError('ORGANIZATION_RECONFIGURE_FAILED_RESTORED', 503) from exc

    def _await_listening(self, config, *, timeout=8.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._listening(config):
                return True
            if self._process is None or self._process.poll() is not None:
                return False
            time.sleep(0.1)
        return self._listening(config)

    def enable(self):
        with self._lock:
            cfg = self.config()
            if not cfg['configured'] or not self.authority.initialized():
                raise SLHError('ORGANIZATION_NOT_READY', 409)
            if cfg['enabled'] and not self._paused and self._process and self._process.poll() is None:
                return self.status()
            self._paused = False
            save_config(self.directory, {**cfg, 'enabled': True, 'activation_confirmed': True})
            self._restarts.clear()
            try:
                self._launch()
            except (OSError, RuntimeError) as exc:
                self._last_error = type(exc).__name__ + ': ' + str(exc)
                raise SLHError('ORGANIZATION_START_FAILED', 503) from exc
            return self.status()

    def stop(self):
        """Stop the current child without changing persistent enablement.

        The watchdog must not resurrect an explicitly stopped child. A new
        manager process may apply the persisted auto-start policy normally.
        """
        with self._lock:
            cfg = self.config()
            if not cfg['configured'] or not cfg['enabled'] or self.status()['state'] != 'running':
                raise SLHError('ORGANIZATION_NOT_RUNNING', 409)
            self._paused = True
            self._terminate()
            self._last_error = None
            return self.status()

    def disable(self):
        with self._lock:
            cfg = self.config()
            save_config(self.directory, {**cfg, 'enabled': False})
            self._paused = False
            self._terminate()
            self._last_error = None
            return self.status()

    def restart(self):
        with self._lock:
            if not self.config()['enabled']:
                raise SLHError('ORGANIZATION_NOT_ENABLED', 409)
            self._paused = False
            self._terminate()
            self._launch()
            return self.status()

    def _seh_child_request(self, method: str, path: str, *, token: str | None = None) -> dict | None:
        """Read or authorize the resident child only over its pinned TLS socket."""
        cfg = read_config(self.directory)
        if not cfg['enabled'] or self._process is None or self._process.poll() is not None:
            return None
        cert_path = self.directory / 'managed-tls-cert.pem'
        if not cert_path.is_file():
            return None
        try:
            expected = hashlib.sha256(ssl.PEM_cert_to_DER_cert(
                cert_path.read_text(encoding='ascii'))).digest()
            host = '127.0.0.1' if cfg['host'] == '0.0.0.0' else cfg['host']
            with socket.create_connection((host, cfg['api_port']), timeout=2) as sock:
                # A governed POST verifies remote Coolify trust inside the child;
                # it may take longer than a status read, but remains bounded.
                if token is not None:
                    sock.settimeout(30)
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                with ctx.wrap_socket(sock, server_hostname='localhost') as tls:
                    if hashlib.sha256(tls.getpeercert(binary_form=True)).digest() != expected:
                        return None
                    headers = f'{method} {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n'
                    if token is not None:
                        headers += f'Authorization: Bearer {token}\r\n'
                        headers += 'Content-Type: application/json\r\nContent-Length: 2\r\n'
                    body = '{}' if token is not None else ''
                    tls.sendall((headers + '\r\n' + body).encode('ascii'))
                    response = http.client.HTTPResponse(tls)
                    response.begin()
                    if response.status != 200:
                        return None
                    data = json.loads(response.read(4096))
                    return data if isinstance(data, dict) else None
        except (OSError, ssl.SSLError, ValueError, TypeError, http.client.HTTPException):
            return None

    def _seh_child_status(self) -> dict | None:
        data = self._seh_child_request('GET', '/v1/org/status')
        if not data or data.get('schema') != 'main-computer.slh-org-status.v1':
            return None
        return data

    def seh_status(self) -> dict:
        """Observes resident SLH -> SEH connectivity; never contacts Coolify."""
        with self._lock:
            cfg = self.config()
            child = self._seh_child_status()
            verified = bool(cfg['enabled'] and cfg['seh_enabled'] and child and
                            child.get('seh_enabled') is True and
                            child.get('seh_connection_verified') is True and
                            child.get('private_requests_available') is True and
                            child.get('pinky_sweeper_available') is True and
                            child.get('worker_serving_enabled') is True)
            authenticated = bool(cfg['enabled'] and cfg['seh_enabled'] and child and
                                 child.get('seh_enabled') is True and
                                 child.get('seh_connection_verified') is True)
            return {'status': 'CONNECTED_VERIFIED' if verified else 'NOT_CONNECTED',
                    'outbound_tunnel_authenticated': authenticated,
                    'organization_enabled': cfg['enabled'],
                    'seh_enabled': cfg['seh_enabled'],
                    'workers_enabled': cfg['workers_enabled'],
                    'private_requests_available': bool(child and child.get('private_requests_available')),
                    'pinky_sweeper_available': bool(child and child.get('pinky_sweeper_available')),
                    'pinky_sweeper_running': bool(child and child.get('pinky_sweeper_running')),
                    'pinky_sweep_last_attempt_ns': (child.get('pinky_sweep_last_attempt_ns') if child else None),
                    'pinky_sweep_last_error': (child.get('pinky_sweep_last_error') if child else None),
                    'pinky_sweep_last_confirmed': bool(child and child.get('pinky_sweep_last_confirmed')),
                    'resident_child_observed': child is not None,
                    'resident_activation_error': (child.get('seh_activation_error')
                                                  if child else None),
                    'resident_outbound_phase': (child.get('seh_outbound_phase')
                                                if child else None),
                    'resident_outbound_attempts': (child.get('seh_outbound_attempts')
                                                   if child else None),
                    'resident_outbound_connections': (child.get('seh_outbound_connections')
                                                      if child else None),
                    'resident_outbound_failures': (child.get('seh_outbound_failures')
                                                   if child else None),
                    'resident_outbound_last_error_class': (child.get('seh_outbound_last_error_class')
                                                          if child else None),
                    'resident_outbound_last_http_status': (child.get('seh_outbound_last_http_status')
                                                           if child else None)}

    def disconnect_seh(self) -> dict:
        """Persistently disable the owned outbound relay, keeping ORG-SLH running.

        The deployed SEH application is not stopped; disabling its sole SLH
        connection removes administrative and Worker reachability through it.
        """
        with self._lock:
            cfg = self.config()
            if not cfg['configured']:
                raise SLHError('ORGANIZATION_NOT_READY', 409)
            try:
                save_config(self.directory, {**cfg, 'seh_enabled': False})
            except (OSError, OrganizationConfigError) as exc:
                raise SLHError('SEH_CONNECTION_CONFIG_SAVE_FAILED', 503) from exc
            return {'status': 'DISCONNECTION_REQUESTED',
                    'connection_enabled': False,
                    'organization_enabled': cfg['enabled']}

    def activate_seh(self, *, execute: bool, expected_generation: int | None = None,
                     token: str | None = None) -> dict:
        """Governed one-SLH/one-SEH enablement; recover deleted app when verified.

        Preflight precedes any mutation. Mother supplies identity only; SEH
        owns Coolify lifecycle and SLH owns the enabled outbound connection.
        """
        from main_computer.slh.storage import machine_root
        from main_computer.slh.seh_canonical_connection import (
            resolve_canonical_connection, SEHApplicationDeleted)
        from tools.seh_control.git_deployment import (
            SEHGitError, SEHGitPublicationError, restore_deleted_canonical_application,
            reconcile_pending_recovery, _canonical_selection)
        from main_computer.slh.seh_autoconnect import read_seh_settings

        if execute and not token:
            raise SLHError('ADMIN_SESSION_REQUIRED', 403)
        with self._lock:
            cfg = self.config()
            if not self.authority.initialized():
                raise SLHError('SLH_ADMIN_INITIALIZATION_REQUIRED', 409)
            # Activation includes provisioning the resident Worker gateway.
            # The outbound SEH supervisor is owned by that gateway; a fresh
            # administrator bootstrap deliberately does not start it by itself.
            # A never-configured listener is confined to loopback, not a
            # surprise LAN-facing socket. Existing explicit bindings survive.
            needs_worker_setup = not cfg['configured'] or not cfg['workers_enabled']
            if read_seh_settings(self.directory) is not None:
                raise SLHError('SEH_LEGACY_LOCAL_SETTINGS_REQUIRE_RECONCILIATION', 409)
            root = machine_root()
            try:
                record, _, generation, _ = _canonical_selection(root, 'mainnet')
            except (ValueError, OSError, RuntimeError):
                raise SLHError('SEH_IDENTITY_OR_DEPLOYMENT_STATE_UNVERIFIED', 409) from None
            if expected_generation is not None and (type(expected_generation) is not int or
                                                     expected_generation != generation):
                raise SLHError('IDENTITY_SNAPSHOT_CHANGED', 409)
            existing_uuid = record['deployment']['application_uuid']
            # All remote probes are read-only. This verifies the existing UUID,
            # origin, Coolify placement, signing-key trust and Mother binding.
            recovery = None
            try:
                # Reconcile the exact previously-created UUID and any forced
                # build receipt before local activation. Never CREATE twice.
                pending = reconcile_pending_recovery(root, 'mainnet',
                                                     state_dir=self.directory, execute=execute)
                if pending is not None:
                    if pending['status'] == 'PLAN_ONLY':
                        return pending
                    if pending['status'] != 'REPLACEMENT_QUEUED_NOT_VERIFIED':
                        return {**pending, 'status': 'INCOMPLETE',
                                'failed_stage': pending.get('failed_stage', 'recovery_build_reconciliation')}
                    recovery = pending
                settings = resolve_canonical_connection(
                    self.directory, mother_root=root, require_enabled=False)
            except SEHApplicationDeleted:
                # This is not a signing-key mismatch. Coolify returned an
                # explicit 404 for the recorded UUID. The signed administrator
                # can recover its one application in the same activation.
                try:
                    restored = restore_deleted_canonical_application(
                        root, 'mainnet', execute=execute, state_dir=self.directory)
                except SEHGitError as exc:
                    if str(exc).startswith('Missing SEH Git build source: '):
                        # The snapshot/export may omit deploy/seh/Dockerfile.
                        # Show the missing build input, never fabricate it.
                        return {'status': 'INCOMPLETE', 'failed_stage': 'git_build_source_missing',
                                'required_source': str(exc).split(': ', 1)[1],
                                'mother_generation': generation, 'remote_mutation': False}
                    if isinstance(exc, SEHGitPublicationError):
                        return {'status': 'INCOMPLETE', 'failed_stage': 'git_source_publication',
                                'git_source_reason': str(exc),
                                **({'git_repair_command': exc.git_command} if exc.git_command else {}),
                                'mother_generation': generation, 'remote_mutation': False}
                    raise SLHError('SEH_REMOTE_APPLICATION_RECOVERY_REFUSED', 409) from None
                except Exception as exc:
                    # Unexpected Coolify recovery errors may follow a successful
                    # create request. Never claim a clean refusal or silently
                    # retry creation: inventory must be re-observed first.
                    # Exception text may contain credentials; report class only.
                    return {'status': 'INCOMPLETE',
                            'failed_stage': 'coolify_deleted_application_recovery_unhandled',
                            'error_type': type(exc).__name__,
                            'mother_generation': generation,
                            'remote_mutation': 'UNKNOWN',
                            'outbound_tunnel_authenticated': False}
                if restored['status'] == 'PLAN_ONLY':
                    return restored
                if restored['status'] != 'REPLACEMENT_QUEUED_NOT_VERIFIED':
                    return {**restored, 'status': 'INCOMPLETE',
                            'failed_stage': restored.get('failed_stage', 'coolify_application_recovery'),
                            'remote_recovery': restored['status']}
                # The replacement was verified and committed to SEH state.
                # Continue the SAME authorized call
                # through normal local activation, then let the CLI wait for
                # authenticated WSS. Never count Coolify's queue as success.
                recovery = restored
                generation = restored['mother_generation']
                existing_uuid = restored['application_uuid']
                try:
                    settings = resolve_canonical_connection(
                        self.directory, mother_root=root, require_enabled=False)
                except (ValueError, RuntimeError, OSError):
                    return {**restored, 'status': 'INCOMPLETE',
                            'failed_stage': 'new_application_trust_readback'}
            except (ValueError, RuntimeError, OSError):
                raise SLHError('SEH_REMOTE_TRUST_PREFLIGHT_REFUSED', 409) from None
            if not settings.seh_url.startswith('wss://'):
                raise SLHError('SEH_PUBLIC_WSS_UNVERIFIED', 409)
            result = {'application_uuid': existing_uuid, 'network': 'mainnet',
                      'seh_url': settings.seh_url, 'mother_generation': generation,
                      'remote_mutation': recovery.get('remote_mutation', False) if recovery else False,
                      **({'former_application_uuid': recovery['former_application_uuid'],
                          'remote_recovery': recovery['status']} if recovery else {}),
                      'worker_service_setup': ('REQUIRED' if needs_worker_setup
                                               else 'ALREADY_CONFIGURED'),
                      'worker_bind_policy': ('LOOPBACK_DEFAULT' if not cfg['configured']
                                             else 'PRESERVE_EXISTING')}
            if not execute:
                return {'status': 'PLAN_ONLY', **result}

            result['mother_mutation'] = 'UNCHANGED'
            try:
                updated = self.config()
                if not updated['configured'] or not updated['workers_enabled']:
                    # A child that has no Worker listener cannot host an SEH
                    # supervisor. It has no local Worker jobs to interrupt;
                    # restart only that child if it was enabled without Workers.
                    if updated['enabled']:
                        self.disable()
                    changes = {'workers_enabled': True, 'seh_enabled': True}
                    if not updated['configured']:
                        changes['host'] = '127.0.0.1'
                    self.configure(changes)
                    self.enable()
                else:
                    if not updated['seh_enabled']:
                        save_config(self.directory, {**updated, 'seh_enabled': True})
                    # The child owns its outbound supervisor. Its existing
                    # parent-watch loop observes this administrator-approved
                    # local configuration change and starts the supervisor in
                    # process, without restarting or rebinding active Workers.
                    # Never forward a Local Service bearer session into the
                    # child: sessions are process-local to each SLHAuthority.
                    if not updated['enabled']:
                        self.enable()
            except (OSError, RuntimeError, SLHError, OrganizationConfigError) as exc:
                return {'status': 'INCOMPLETE', 'failed_stage': 'resident_service_activation',
                        'error_type': type(exc).__name__, **result}
            return {'status': 'ACTIVATION_STARTED', **result}

    def _terminate(self):
        # The shared PID identity is authoritative. Keep Popen as a fallback
        # only for a freshly launched child that has not published it yet.
        proc = self._process
        stop_recorded_process(self.directory)
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        self._process = None

    def _watch(self):
        while not self._stop.wait(2):
            with self._lock:
                if self._stop.is_set():
                    break
                try:
                    cfg = read_config(self.directory)
                    if self._paused or not cfg['enabled'] or not cfg['activation_confirmed'] or not self.authority.initialized():
                        continue
                    if self._process is not None and self._process.poll() is None:
                        continue
                    now = time.monotonic()
                    while self._restarts and now - self._restarts[0] >= 300:
                        self._restarts.popleft()
                    if len(self._restarts) >= 3:
                        self._last_error = 'Organization Service repeatedly exited; restart limit reached'
                        continue
                    self._restarts.append(now)
                    self._launch()
                except (OSError, RuntimeError, OrganizationConfigError, SLHError) as exc:
                    self._last_error = type(exc).__name__ + ': ' + str(exc)

    def close(self):
        self._stop.set()
        if self._monitor is not None:
            self._monitor.join(timeout=3)
        with self._lock:
            self._terminate()
