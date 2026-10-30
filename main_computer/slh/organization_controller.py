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

from .organization_state import (read_config, save_config, initialize_disabled,
                                 OrganizationConfigError)
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
                        'listening': False, 'last_error': 'Invalid organization configuration'}
            proc = self._process
            running = proc is not None and proc.poll() is None
            listening = running and self._listening(config)
            if listening:
                state = 'running'
            elif running:
                state = 'starting'
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
                    'pid': proc.pid if running else None,
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

    def enable(self):
        with self._lock:
            cfg = self.config()
            if not cfg['configured'] or not self.authority.initialized():
                raise SLHError('ORGANIZATION_NOT_READY', 409)
            if cfg['enabled'] and self._process and self._process.poll() is None:
                return self.status()
            save_config(self.directory, {**cfg, 'enabled': True, 'activation_confirmed': True})
            self._restarts.clear()
            try:
                self._launch()
            except (OSError, RuntimeError) as exc:
                self._last_error = type(exc).__name__ + ': ' + str(exc)
                raise SLHError('ORGANIZATION_START_FAILED', 503) from exc
            return self.status()

    def disable(self):
        with self._lock:
            cfg = self.config()
            save_config(self.directory, {**cfg, 'enabled': False})
            self._terminate()
            self._last_error = None
            return self.status()

    def restart(self):
        with self._lock:
            if not self.config()['enabled']:
                raise SLHError('ORGANIZATION_NOT_ENABLED', 409)
            self._terminate()
            self._launch()
            return self.status()

    def _terminate(self):
        proc, self._process = self._process, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

    def _watch(self):
        while not self._stop.wait(2):
            with self._lock:
                if self._stop.is_set():
                    break
                try:
                    cfg = read_config(self.directory)
                    if not cfg['enabled'] or not cfg['activation_confirmed'] or not self.authority.initialized():
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
