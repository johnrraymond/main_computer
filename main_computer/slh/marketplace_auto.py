"""Organization-owned automatic marketplace reconciliation.

This is an opt-in operational controller, not a second scheduler.  The existing
SchedulerPolicyStore is the capacity authority.  Hub authorization never goes
into configuration or status responses.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading
import time
from urllib.parse import urlsplit

from .authority import SLHError

CONFIG_NAME = 'marketplace-participation.json'
AUTH_NAME = 'marketplace-authorization.private.json'
DEFAULT = {'schema': 'main-computer.slh-marketplace-participation.v1',
           'revision': 0, 'enabled': False, 'hub_url': None,
           'interval_seconds': 30}


def _atomic_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile('w', prefix='.market-', suffix='.tmp',
                                         dir=path.parent, encoding='utf-8', delete=False) as f:
            tmp = Path(f.name)
            if os.name == 'posix':
                os.fchmod(f.fileno(), 0o600)
            json.dump(data, f, sort_keys=True)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def read_settings(state_dir: Path) -> dict:
    path = Path(state_dir) / CONFIG_NAME
    if not path.exists():
        return dict(DEFAULT)
    data = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(data, dict) or set(data) != set(DEFAULT)
            or data['schema'] != DEFAULT['schema']
            or type(data['revision']) is not int or data['revision'] < 0
            or type(data['enabled']) is not bool
            or type(data['interval_seconds']) is not int
            or not 1 <= data['interval_seconds'] <= 3600):
        raise SLHError('INVALID_MARKETPLACE_CONFIG', 409)
    _check_url(data['hub_url'])
    return data


def _check_url(value):
    if value is None:
        return
    if not isinstance(value, str) or len(value) > 2048:
        raise SLHError('INVALID_MARKETPLACE_HUB_URL')
    url = urlsplit(value)
    if (url.scheme not in ('http', 'https') or not url.hostname
            or url.username is not None or url.password is not None
            or url.query or url.fragment or url.path not in ('', '/')):
        raise SLHError('INVALID_MARKETPLACE_HUB_URL')
    try:
        _ = url.port
    except ValueError as exc:
        raise SLHError('INVALID_MARKETPLACE_HUB_URL') from exc


def update_settings(state_dir: Path, changes: dict, expected_revision: int) -> dict:
    if (not isinstance(changes, dict) or not changes
            or set(changes) - {'enabled', 'hub_url', 'interval_seconds'}):
        raise SLHError('INVALID_MARKETPLACE_CONFIG')
    if type(expected_revision) is not int:
        raise SLHError('MARKETPLACE_REVISION_REQUIRED')
    existing = read_settings(state_dir)
    if existing['revision'] != expected_revision:
        raise SLHError('MARKETPLACE_REVISION_CONFLICT', 409)
    proposed = {**existing, **changes, 'revision': expected_revision + 1}
    if type(proposed['enabled']) is not bool or (type(proposed['interval_seconds']) is not int
            or not 1 <= proposed['interval_seconds'] <= 3600):
        raise SLHError('INVALID_MARKETPLACE_CONFIG')
    _check_url(proposed['hub_url'])
    if proposed['enabled'] and not proposed['hub_url']:
        raise SLHError('MARKETPLACE_HUB_REQUIRED', 409)
    _atomic_json(Path(state_dir) / CONFIG_NAME, proposed)
    return proposed


def save_authorization(state_dir: Path, value: dict):
    if (not isinstance(value, dict) or value.get('kind') != 'multisession_key'
            or not isinstance(value.get('multisession_key_id'), str)
            or not 1 <= len(value['multisession_key_id']) <= 4096
            or set(value) - {'kind', 'multisession_key_id'}):
        raise SLHError('INVALID_MARKETPLACE_AUTHORIZATION')
    _atomic_json(Path(state_dir) / AUTH_NAME, value)


def load_authorization(state_dir: Path) -> dict | None:
    path = Path(state_dir) / AUTH_NAME
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(value, dict) or value.get('kind') != 'multisession_key'
            or not isinstance(value.get('multisession_key_id'), str)):
        raise SLHError('INVALID_MARKETPLACE_AUTHORIZATION', 409)
    return value


class AutomaticMarketplace:
    """One reconciler for manual and automatic calls; owns no policy decisions."""
    def __init__(self, market, state_dir: Path):
        self.market = market
        self.state_dir = Path(state_dir)
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self.attempts = 0
        self.failures = 0
        self.consecutive_failures = 0
        self.last_success = None
        self.last_attempt = None
        self.last_error = None
        self.last_result = None
        self.decisions = []
        self._restart_required = False

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name='slh-marketplace-reconcile')
        self._thread.start()

    def wake(self, *_args):
        self._wake.set()

    def _loop(self):
        # On initial startup, disabled marketplace is a no-op.  Events wake
        # promptly; periodic poll also notices policy changes and missed events.
        delay = 0
        while not self._stop.is_set():
            self._wake.wait(timeout=delay)
            self._wake.clear()
            if self._stop.is_set():
                break
            try:
                settings = read_settings(self.state_dir)
                self.reconcile()
                delay = (min(60, 2 ** min(self.consecutive_failures - 1, 6))
                         if self.consecutive_failures else settings['interval_seconds'])
            except (OSError, ValueError, SLHError, RuntimeError, ConnectionError):
                delay = min(max(1, delay * 2), 60)

    def reconcile(self) -> dict:
        with self._lock:
            settings = read_settings(self.state_dir)
            enabled = settings['enabled']
            # Disabling must drain prior sessions without cancelling paid work.
            if not enabled and not self.market.sessions:
                self.last_error = None
                return {'state': 'disabled', 'live_public_sessions': []}
            auth = load_authorization(self.state_dir) if enabled else {}
            missing_auth = enabled and not auth
            if missing_auth:
                enabled = False  # Drain stale slots if credentials disappear.
            self.attempts += 1
            self.last_attempt = time.time()
            try:
                # A changed Hub or credential must never silently retain
                # sessions authenticated against the old destination/key.
                if enabled and self._restart_required:
                    draining = self.market.reconcile(hub_url=settings['hub_url'] or '',
                                                     auth={}, enabled=False)
                    if self.market.sessions:
                        result = {**draining, 'state': 'waiting_for_in_flight_work'}
                    else:
                        self._restart_required = False
                        result = self.market.reconcile(hub_url=settings['hub_url'] or '',
                                                       auth=auth, enabled=True)
                else:
                    result = self.market.reconcile(hub_url=settings['hub_url'] or '',
                                                   auth=auth, enabled=enabled)
            except (RuntimeError, ValueError, OSError, ConnectionError, SLHError) as exc:
                self.failures += 1
                self.consecutive_failures += 1
                self.last_error = type(exc).__name__  # never report secrets
                raise
            self.last_result = result
            failed_slots = [x for x in result.get('blocked', []) if isinstance(x, str) and ':' in x]
            if missing_auth or failed_slots:
                self.failures += 1
                self.consecutive_failures += 1
                self.last_error = ('MISSING_HUB_AUTHORIZATION' if missing_auth
                                   else 'UPSTREAM_SESSION_RECONCILE_BLOCKED')
            else:
                self.last_success = time.time()
                self.consecutive_failures = 0
                self.last_error = None
            # Bounded, nonsecret decision trace for diagnostics and stress.
            self.decisions.append({'time': self.last_attempt,
                                   'config_revision': settings['revision'],
                                   'desired': result.get('desired_public_workers', []),
                                   'live': result.get('live_public_sessions', []),
                                   'blocked': result.get('blocked', []),
                                   'reason': result.get('reason')})
            self.decisions = self.decisions[-100:]
            return result

    def configure(self, changes: dict, expected_revision: int) -> dict:
        with self._lock:
            previous = read_settings(self.state_dir)
            result = update_settings(self.state_dir, changes, expected_revision)
            if previous['hub_url'] != result['hub_url']:
                self._restart_required = True
            self.wake()
            return result

    def authorize(self, value: dict):
        with self._lock:
            save_authorization(self.state_dir, value)
            self._restart_required = True
            self.wake()

    def status(self) -> dict:
        with self._lock:
            settings = read_settings(self.state_dir)
            # No authorization value or filename in API responses.
            authorization_present = (Path(self.state_dir) / AUTH_NAME).is_file()
            return {'schema': 'main-computer.slh-marketplace-auto.v1',
                    'configuration': settings, 'authorization_present': authorization_present,
                    'attempts': self.attempts, 'failures': self.failures,
                    'consecutive_failures': self.consecutive_failures,
                    'last_attempt': self.last_attempt, 'last_success': self.last_success,
                    'last_error': self.last_error, 'last_result': self.last_result,
                    'reauthorization_pending': self._restart_required,
                    'recent_decisions': list(self.decisions[-8:])}

    def stop(self):
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=15)
        with self._lock:
            pass  # do not close upstream sessions during reconciliation
