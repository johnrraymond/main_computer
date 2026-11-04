"""Configured, supervised SLH -> SEH connection owned by the SLH gateway.

Configuration is public routing metadata in the SLH state directory. The P-256
service signing key stays in that directory and is never serialized to reports.
An absent or disabled configuration starts no outbound connection.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections import deque
import time
import json
import os
from pathlib import Path
import re
import threading
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from main_computer.slh.seh_transport import SLHSEHConnector, _endpoint
from main_computer.seh.relay import LAB_ID

_FILENAME = 'seh-connection.json'
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')


@dataclass(frozen=True)
class SEHConnectionSettings:
    enabled: bool
    seh_url: str
    slh_id: str
    allow_loopback_lab: bool = False
    retry_seconds: float = 2.0

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> 'SEHConnectionSettings':
        if not isinstance(value, dict) or set(value) != {
            'enabled', 'seh_url', 'slh_id',
            'allow_loopback_lab', 'retry_seconds',
        }:
            raise ValueError('Invalid SLH SEH connection configuration')
        if type(value['enabled']) is not bool or type(value['allow_loopback_lab']) is not bool:
            raise ValueError('SEH enabled and allow_loopback_lab must be booleans')
        for key in ('slh_id',):
            if not isinstance(value[key], str) or not _ID.fullmatch(value[key]):
                raise ValueError(f'Invalid SEH {key}')
        if not isinstance(value['seh_url'], str):
            raise ValueError('Invalid SEH URL')
        _endpoint(value['seh_url'], allow_lab=value['allow_loopback_lab'])
        if type(value['retry_seconds']) not in (int, float) or not 0.1 <= value['retry_seconds'] <= 60:
            raise ValueError('SEH retry_seconds must be between 0.1 and 60')
        return cls(**value)


def read_seh_settings(state_dir: Path) -> SEHConnectionSettings | None:
    path = Path(state_dir) / _FILENAME
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding='utf-8'))
    # One-way removal of the former, non-authoritative organization label.
    # Validate the *entire* remaining configuration before rewriting it.
    if isinstance(data, dict) and 'organization_id' in data:
        updated = dict(data)
        updated.pop('organization_id')
        settings = SEHConnectionSettings.from_dict(updated)
        write_seh_settings(state_dir, settings)
        return settings
    return SEHConnectionSettings.from_dict(data)


def write_seh_settings(state_dir: Path, settings: SEHConnectionSettings) -> Path:
    # Round-trip validation also forbids secrets, unknown fields and non-loopback WS.
    from dataclasses import asdict
    data = asdict(settings)
    SEHConnectionSettings.from_dict(data)
    path = Path(state_dir) / _FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.json.tmp')
    try:
        with tmp.open('w', encoding='utf-8') as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write('\n')
        os.replace(tmp, path)
    finally:
        if tmp.exists():tmp.unlink()
    return path


def _load_service_signer(state_dir: Path):
    path = Path(state_dir) / 'seh-service-signing-key.pem'
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError('SLH SEH service identity must be P-256')
    return key


class SEHAutoConnection:
    """Reopens an authenticated tunnel whenever its configured SEH is available."""
    def __init__(self, state_dir: Path, *, gateway_port: int,
                 identity_private_key=None, lab_id: str | None = None,
                 settings_resolver=None):
        self.state_dir = Path(state_dir)
        self.settings = read_seh_settings(self.state_dir) if settings_resolver is None else None
        self._settings_resolver = settings_resolver
        if settings_resolver is None and (self.settings is None or not self.settings.enabled):
            raise ValueError('SLH SEH autoconnection requires an enabled configuration')
        if lab_id is not None and not LAB_ID.fullmatch(lab_id):
            raise ValueError('Invalid isolated lab ID')
        if identity_private_key is not None and (
                self.settings is None or not self.settings.allow_loopback_lab):
            raise ValueError('Injected identity is allowed only in a loopback lab')
        self.signer = identity_private_key or _load_service_signer(self.state_dir)
        self.lab_id = lab_id
        self.gateway_port = gateway_port
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._current = None
        self._thread = None
        self.connections = 0
        self.failures = 0
        self.last_error = None
        # Diagnostic state only. Never record credentials, URLs or exception messages.
        self._connection_attempts = 0
        self._phase = 'NOT_STARTED'
        self._last_http_status = None
        self._retired_stream_events = deque(maxlen=4096)
        self._tunnel_lifecycle = deque(maxlen=256)

    def stream_diagnostics_for_gateway_ports(self, ports):
        selected = set(ports)
        with self._lock:
            retained = list(self._retired_stream_events)
            current = self._current
            lifecycle = list(self._tunnel_lifecycle)
            attempts = self.connections
            failures = self.failures
            last_error = self.last_error
        if current is not None:
            retained.extend(current.diagnostics_for_gateway_ports(selected))
        tags = {row['stream_tag'] for row in retained
                if row.get('gateway_peer_port') in selected}
        # Include pre-failure events from retired connectors even after an
        # automatic reconnect replaces the connector object.
        selected_events = [dict(row) for row in retained
                           if row.get('stream_tag') in tags][-160:]
        return {'events': selected_events, 'tunnel_lifecycle': lifecycle[-32:],
                'connections': attempts, 'failures': failures,
                'last_error_class': last_error}

    def connection_diagnostics(self) -> dict:
        """Nonsecret snapshot of resident outbound-supervisor progress."""
        with self._lock:
            connected = self._current is not None and self._current._running.is_set()
            return {'phase': self._phase, 'connected': connected,
                    'attempts': self._connection_attempts,
                    'connections': self.connections, 'failures': self.failures,
                    'last_error_class': self.last_error,
                    'last_http_status': self._last_http_status}

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._current is not None and self._current._running.is_set()

    def drop_for_test(self) -> None:
        """For disposable lab instances only; never expose as an admin operation."""
        if self.settings is None or not self.settings.allow_loopback_lab:
            raise ValueError('Cannot interrupt production SEH connection for a test')
        with self._lock:
            current = self._current
        if current is not None:
            current.stop()

    def start(self):
        if self._thread is not None:
            raise RuntimeError('SEH connection supervisor already started')
        with self._lock:
            self._phase = 'STARTING'
        self._thread = threading.Thread(target=self._run, name='slh-seh-autoconnect', daemon=True)
        self._thread.start()
        return self

    def _run(self):
        while not self._stop.is_set():
            s = self.settings
            conn = None
            try:
                with self._lock:
                    self._phase = 'RESOLVING_CONFIGURATION'
                if self._settings_resolver is not None:
                    s = self._settings_resolver(self.state_dir)
                    if not isinstance(s, SEHConnectionSettings) or not s.enabled:
                        raise ValueError('Canonical SEH is not enabled')
                with self._lock:
                    self._phase = 'CONNECTING_OUTBOUND_WSS'
                    self._connection_attempts += 1
                conn = SLHSEHConnector(
                    seh_url=s.seh_url,
                    slh_id=s.slh_id, identity_private_key=self.signer,
                    gateway_port=self.gateway_port, allow_lab=s.allow_loopback_lab,
                    lab_id=self.lab_id).connect()
                with self._lock:
                    self._current = conn
                    self.connections += 1
                    self.last_error = None
                    self._last_http_status = None
                    self._phase = 'AUTHENTICATED'
                    self._tunnel_lifecycle.append({'event': 'seh_tunnel_connected',
                                                   'unix_ns': time.time_ns(),
                                                   'monotonic_ns': time.monotonic_ns()})
                while conn._running.is_set() and not self._stop.wait(0.1):
                    pass
            except Exception as exc:  # reconnect on transport/protocol failures; never leak credentials
                with self._lock:
                    self.failures += 1
                    self.last_error = type(exc).__name__
                    self._phase = 'RETRYING_AFTER_FAILURE'
                    response = getattr(exc, 'response', None)
                    status = getattr(response, 'status_code', None)
                    self._last_http_status = (status if type(status) is int and 100 <= status <= 599
                                              else None)
                    self._tunnel_lifecycle.append({'event': 'seh_tunnel_connect_failed',
                                                   'exception_class': type(exc).__name__,
                                                   'unix_ns': time.time_ns(),
                                                   'monotonic_ns': time.monotonic_ns()})
            finally:
                with self._lock:
                    if self._current is conn:
                        self._current = None
                if conn is not None:
                    # Preserve cause evidence BEFORE replacing the connector.
                    with conn._lock:
                        retired = list(conn._stream_events)
                    with self._lock:
                        self._retired_stream_events.extend(retired)
                        if not self._stop.is_set():
                            self._phase = 'RETRYING_AFTER_DISCONNECT'
                        if conn.last_error:
                            self.last_error = conn.last_error
                        self._tunnel_lifecycle.append({
                            'event': 'seh_tunnel_disconnected',
                            'error_class': conn.last_error,
                            'unix_ns': time.time_ns(),
                            'monotonic_ns': time.monotonic_ns()})
                    conn.stop()
            self._stop.wait(s.retry_seconds if s is not None else 2.0)

    def stop(self):
        self._stop.set()
        with self._lock:
            conn = self._current
        if conn is not None:
            conn.stop()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=12)
        with self._lock:
            self._phase = 'STOPPED'
