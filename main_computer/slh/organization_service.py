"""Network-reachable, TLS-only SLH Organization Service (never bootstrap).

The host-local control plane starts this as a separate process after an
administrator explicitly enables it.  Existing SLHAuthority device sessions
and admin APIs are reused here; no privileged loopback endpoints are exposed.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import psutil
import re
import signal
from pathlib import Path
import ssl
import threading
from urllib.parse import parse_qs, urlsplit

from .api import SLHAPI
from .authority import SLHAuthority, SLHError
from .managed_gateway import SLHManagedGateway, SLHMarketplaceProxy, transport_identity
from .organization_state import read_config, save_config, OrganizationConfigError
from .organization_process import registered_process
from .storage import slh_state_dir
from .marketplace_auto import AutomaticMarketplace, read_settings
from .organization_wallet import OrganizationWallet
from .private_requests import PrivateRequests
from .pinky_sweep import PinkySweep


_SEH_OPERATION_LOCK = threading.RLock()

# SEH carries TLS ciphertext; admission is therefore enforced by ORG-SLH on
# the separate loopback listener, *before* normal API dispatch or authorization.
# It is deliberately independent of the MCEL notebook/client allowlist.
_SEH_ID = r'[A-Za-z0-9][A-Za-z0-9_.-]{0,159}'
_SEH_REQUEST_ID = r'[0-9a-f]{32}'
_SEH_ADMIN_EXACT = frozenset({
    ('GET', '/v1/org/identity'), ('GET', '/v1/org/status'),
    ('POST', '/v1/auth/challenge'), ('POST', '/v1/auth/verify'),
    ('GET', '/v1/auth/session'), ('POST', '/v1/auth/logout'),
    ('GET', '/v1/enrollment/admins'), ('POST', '/v1/enrollment/requests'),
    ('POST', '/v1/enrollment/approvals'),
    ('GET', '/v1/admin/devices'), ('GET', '/v1/admin/devices/trash'),
    ('POST', '/v1/admin/devices/trash/empty'),
    ('GET', '/v1/admin/enrollments'),
    ('GET', '/v1/admin/policies'), ('PATCH', '/v1/admin/policies'),
})
_SEH_ADMIN_VARIABLE = tuple((method, re.compile(pattern + r'\Z')) for method,pattern in (
    ('GET', rf'/v1/enrollment/requests/{_SEH_REQUEST_ID}/status'),
    ('GET', rf'/v1/admin/devices/{_SEH_ID}'),
    ('PATCH', rf'/v1/admin/devices/{_SEH_ID}'),
    ('POST', rf'/v1/admin/devices/{_SEH_ID}/revoke'),
    ('POST', rf'/v1/admin/devices/{_SEH_ID}/reactivate'),
    ('POST', rf'/v1/admin/devices/{_SEH_ID}/remove'),
    ('POST', rf'/v1/admin/enrollments/{_SEH_REQUEST_ID}/deny'),
    ('POST', rf'/v1/admin/enrollments/{_SEH_REQUEST_ID}/keys'),
))


def seh_identity_operation_allowed(method: str, path: str) -> bool:
    return ((method, path) in _SEH_ADMIN_EXACT or
            any(method == verb and pattern.fullmatch(path)
                for verb, pattern in _SEH_ADMIN_VARIABLE))


def make_seh_identity_listener(*, authority, api, tls_context, fingerprint):
    """Loopback-only, TLS-only SEH identity API; no separate authority/store.

    Its port is ephemeral, passed directly to the resident gateway in memory;
    it is NEVER a configurable remote destination or public-facing listener.
    """
    listener = OrganizationHTTPServer(('127.0.0.1', 0), OrganizationHTTPHandler)
    try:
        listener.socket = tls_context.wrap_socket(listener.socket, server_side=True)
        listener.authority = authority
        listener.api = api
        listener.fingerprint = fingerprint
        listener.seh_identity_only = True
        return listener
    except BaseException:
        listener.server_close()
        raise


class OrganizationHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class OrganizationHTTPHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, format, *args):
        # Never include bearer tokens in logs; avoid logging full URLs.
        pass

    def do_GET(self): self._dispatch()
    def do_POST(self): self._dispatch()
    def do_PUT(self): self._dispatch()
    def do_PATCH(self): self._dispatch()

    def _dispatch(self):
        path = self.path.split('?', 1)[0]
        method = self.command
        restricted = getattr(self.server, 'seh_identity_only', False)
        if restricted and (self.path != path or
                           not seh_identity_operation_allowed(method, path)):
            self._respond(403, {'error': {'code': 'SEH_OPERATION_NOT_ALLOWED'}})
            return
        if self.headers.get('Origin'):
            self._respond(403, {'error': {'code': 'CROSS_ORIGIN_DENIED'}})
            return
        length = self.headers.get('Content-Length', '0')
        try:
            size = int(length)
        except (TypeError, ValueError):
            size = -1
        if not 0 <= size <= 131072:
            self._respond(413, {'error': {'code': 'BODY_TOO_LARGE'}})
            return
        if size and self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
            self._respond(415, {'error': {'code': 'JSON_REQUIRED'}})
            return
        try:
            body = json.loads(self.rfile.read(size)) if size else {}
        except (ValueError, UnicodeDecodeError):
            self._respond(400, {'error': {'code': 'INVALID_JSON'}})
            return
        if not isinstance(body, dict):
            self._respond(400, {'error': {'code': 'INVALID_JSON_OBJECT'}})
            return
        # Never allow the general policies API to change physical inspection
        # or future policies over the relay. Only the single Trash retention
        # boolean may be written through this listener.
        if restricted and method == 'PATCH' and path == '/v1/admin/policies' and (
                set(body) != {'remove_trash_after_30_days'} or
                type(body.get('remove_trash_after_30_days')) is not bool):
            self._respond(403, {'error': {'code': 'SEH_OPERATION_NOT_ALLOWED'}})
            return
        auth = self.headers.get('Authorization', '')
        token = auth[7:] if auth.startswith('Bearer ') else ''
        authority = self.server.authority
        if method == 'GET' and path == '/v1/org/identity':
            self._respond(200, {'schema': 'main-computer.slh-org-identity.v1',
                                'tls_sha256': self.server.fingerprint})
            return
        if method == 'GET' and path == '/v1/org/status' and restricted:
            # The Remote SEH handshake needs readiness, not host, scheduler,
            # marketplace, lifecycle, or other operational diagnostics.
            self._respond(200, {'schema': 'main-computer.slh-org-status.v1',
                                'operational': True})
            return
        if method == 'GET' and path == '/v1/org/status':
            supervisor = (self.server.gateway.seh_connection
                          if self.server.gateway else None)
            inspect_supervisor = getattr(supervisor, 'connection_diagnostics', None)
            diagnostics = inspect_supervisor() if callable(inspect_supervisor) else {}
            sweeper = self.server.pinky_sweeper
            # No sensitive ledger rows, signer paths, or receipt IDs in status.
            get_sweep_status = getattr(sweeper, 'status', None)
            sweep_thread = getattr(sweeper, '_thread', None)
            sweep_status = get_sweep_status() if callable(get_sweep_status) else {}
            self._respond(200, {'schema': 'main-computer.slh-org-status.v1',
                                'operational': True,
                                'worker_serving_enabled': self.server.workers_enabled,
                                'seh_enabled': self.server.seh_enabled,
                                'seh_connection_verified': bool(self.server.gateway and
                                    self.server.gateway.seh_connection and
                                    self.server.gateway.seh_connection.connected),
                                'private_requests_available': self.server.private_requests is not None,
                                'pinky_sweeper_available': sweeper is not None,
                                'pinky_sweeper_running': bool(sweep_thread and sweep_thread.is_alive()),
                                'pinky_sweep_last_attempt_ns': sweep_status.get('last_attempt_ns'),
                                'pinky_sweep_last_error': sweep_status.get('last_error'),
                                'pinky_sweep_last_confirmed': bool(sweep_status.get('last_confirmed')),
                                'seh_activation_error': getattr(self.server, 'seh_activation_error', None),
                                'seh_outbound_phase': diagnostics.get('phase', 'NOT_STARTED'),
                                'seh_outbound_attempts': diagnostics.get('attempts', 0),
                                'seh_outbound_connections': diagnostics.get('connections', 0),
                                'seh_outbound_failures': diagnostics.get('failures', 0),
                                'seh_outbound_last_error_class': diagnostics.get('last_error_class'),
                                'seh_outbound_last_http_status': diagnostics.get('last_http_status'),
                                'trash_retention_last_error': getattr(self.server, 'trash_retention_last_error', None),
                                'trash_retention_last_removed_count': getattr(self.server, 'trash_retention_last_removed_count', 0)})
            return
        # Administrative lifecycle applies to the resident SLH-owned outbound
        # SEH connection, not to the external Coolify application's process.
        # The SEH has no administrator database or organization authority.
        if path == '/v1/org/admin/seh/connection' and method in ('GET', 'POST'):
            try:
                identity = authority.authenticate(token, admin=True)
            except SLHError as exc:
                self._respond(exc.status, {'error': {'code': exc.code}})
                return
            cfg = read_config(self.server.state_dir)
            gateway = self.server.gateway
            if method == 'GET':
                self._respond(200, self._seh_connection_status(cfg))
                return
            if set(body) != {'action'} or body['action'] not in ('start', 'stop', 'restart'):
                self._respond(400, {'error': {'code': 'INVALID_SEH_CONNECTION_OPERATION'}})
                return
            action = body['action']
            if not cfg['configured'] or not cfg['enabled'] or not cfg['workers_enabled'] or gateway is None:
                self._respond(409, {'error': {'code': 'SEH_RESIDENT_GATEWAY_NOT_ENABLED'}})
                return
            # No arbitrary remote URL, target, identity, or Coolify credential
            # can be supplied by this API. The existing canonical resolver
            # checks the selected SLH deployment before each connection start.
            try:
                with getattr(self.server, 'seh_operation_lock', _SEH_OPERATION_LOCK):
                    cfg = read_config(self.server.state_dir)
                    if action == 'stop':
                        # Persist first, acknowledge the caller, then tear
                        # down the tunnel. Stopping it here would sever a
                        # request made *through* this very SEH before the
                        # HTTP response reached the administrator.
                        save_config(self.server.state_dir, {**cfg, 'seh_enabled': False})
                        response = self._seh_connection_status(read_config(self.server.state_dir))
                        response['status'] = 'DISCONNECTION_SCHEDULED'
                    elif action == 'restart':
                        if not cfg['seh_enabled']:
                            self._respond(409, {'error': {'code':'SEH_CONNECTION_NOT_ENABLED'}})
                            return
                        response = self._seh_connection_status(cfg)
                        response['status'] = 'RESTART_SCHEDULED'
                    else:
                        if not cfg['seh_enabled']:
                            save_config(self.server.state_dir, {**cfg, 'seh_enabled': True})
                        try:
                            gateway.allow_seh = True
                            gateway.start_canonical_seh()
                        except (RuntimeError, ValueError, OSError):
                            if not cfg['seh_enabled']:
                                save_config(self.server.state_dir, cfg)
                                gateway.allow_seh = False
                            raise
                        self.server.seh_enabled = True
                        response = self._seh_connection_status(read_config(self.server.state_dir))
                        response['status'] = 'CONNECTION_STARTED'
                self._respond(200, response)
                if action == 'stop':
                    threading.Timer(.3, _refresh_outbound_seh, args=(self.server,)).start()
                elif action == 'restart':
                    threading.Timer(.3, _restart_outbound_seh, args=(self.server,)).start()
            except (OSError, RuntimeError, ValueError, OrganizationConfigError) as exc:
                self._respond(409, {'error': {'code': 'SEH_CONNECTION_OPERATION_REFUSED',
                                               'error_type': type(exc).__name__}})
            return
        if method == 'POST' and path == '/v1/org/admin/seh/activate':
            try:
                authority.authenticate(token, admin=True)
            except SLHError as exc:
                self._respond(exc.status, {'error': {'code': exc.code}})
                return
            cfg = read_config(self.server.state_dir)
            if not cfg['seh_enabled'] or not self.server.workers_enabled or not self.server.gateway:
                self._respond(409, {'error': {'code': 'SEH_RESIDENT_GATEWAY_NOT_ENABLED'}})
                return
            try:
                with getattr(self.server, 'seh_operation_lock', _SEH_OPERATION_LOCK):
                    self.server.gateway.allow_seh = True
                    self.server.gateway.start_canonical_seh()
            except (RuntimeError, ValueError, OSError) as exc:
                self._respond(409, {'error': {'code': 'SEH_CANONICAL_CONNECTION_REFUSED',
                                              'error_type': type(exc).__name__}})
                return
            self.server.seh_enabled = True
            self._respond(200, {'status': 'ACTIVATION_STARTED',
                                'outbound_connection_owner': 'resident-slh'})
            return
        if method == 'GET' and path == '/v1/org/admin/services':
            try:
                authority.authenticate(token, admin=True)
            except SLHError as exc:
                self._respond(exc.status, {'error': {'code': exc.code}})
                return
            self._respond(200, {'organization': authority.status(),
                                'worker_serving_enabled': self.server.workers_enabled,
                                'seh_enabled': self.server.seh_enabled,
                                'organization_api': 'running',
                                'worker_listener': 'running' if self.server.workers_enabled else 'disabled'})
            return
        # The marketplace is owned by this process, alongside the real Worker
        # scheduler. Calls are authenticated against the SAME organizational
        # admin identity as every other organization API. Neither the Local
        # Service nor a lab process creates upstream public slots here.
        if path in ('/v1/org/admin/marketplace/status',
                    '/v1/org/admin/marketplace/reconcile',
                    '/v1/org/admin/marketplace/config',
                    '/v1/org/admin/marketplace/authorization',
                    '/v1/org/admin/marketplace/connection-diagnostics',
                    '/v1/org/admin/marketplace/wallet/bind'):
            try:
                authority.authenticate(token, admin=True)
            except SLHError as exc:
                self._respond(exc.status, {'error': {'code': exc.code}})
                return
            controller = self.server.marketplace_controller
            if controller is None:
                self._respond(409, {'error': {'code': 'WORKER_SERVING_DISABLED'}})
                return
            try:
                if path.endswith('/connection-diagnostics') and method == 'GET':
                    params = parse_qs(urlsplit(self.path).query)
                    selected = None
                    if 'device_ids' in params:
                        raw_ids = params['device_ids'][0]
                        selected = {value for value in raw_ids.split(',') if value}
                        if (len(raw_ids) > 8192 or len(selected) > 64 or
                                any(len(value) > 128 for value in selected)):
                            self._respond(400, {'error': {'code': 'INVALID_DIAGNOSTIC_FILTER'}})
                            return
                    self._respond(200, self.server.gateway.connection_diagnostics(device_ids=selected))
                    return
                if path.endswith('/status') and method == 'GET':
                    self._respond(200, self._marketplace_status())
                    return
                if path.endswith('/config') and method == 'GET':
                    self._respond(200, controller.status())
                    return
                if path.endswith('/config') and method == 'PATCH':
                    if set(body) != {'changes', 'expected_revision'}:
                        raise SLHError('INVALID_MARKETPLACE_REQUEST')
                    self._respond(200, controller.configure(body['changes'], body['expected_revision']))
                    return
                if path.endswith('/authorization') and method == 'POST':
                    if set(body) != {'multisession_authorization'}:
                        raise SLHError('INVALID_MARKETPLACE_REQUEST')
                    controller.authorize(body['multisession_authorization'])
                    self._respond(200, {'authorization_present': True})
                    return
                if path.endswith('/wallet/bind') and method == 'POST':
                    result = controller.bind_wallet(body)
                    self._respond(200, result)
                    return
                if path.endswith('/reconcile') and method == 'POST':
                    # The manual endpoint is preserved, but never allows
                    # bypassing disabled participation or saved Hub identity.
                    if body:
                        raise SLHError('INVALID_MARKETPLACE_REQUEST')
                    result = controller.reconcile()
                    self._respond(200, {**result, 'marketplace': self._marketplace_status()})
                    return
            except SLHError as exc:
                self._respond(exc.status, {'error': {'code': exc.code}})
                return
            except (TypeError, ValueError, RuntimeError, OSError) as exc:
                self._respond(422, {'error': {'code': 'MARKETPLACE_OPERATION_FAILED',
                                              'kind': type(exc).__name__}})
                return
            self._respond(405, {'error': {'code': 'METHOD_NOT_ALLOWED'}})
            return
        # Full Coolify lifecycle management is available only over the DIRECT
        # TLS ORG-SLH channel. The distinct SEH identity listener rejects this
        # path before reaching this handler, even for a valid administrator.
        if path == '/v1/org/admin/seh/deployment' or path.startswith('/v1/org/admin/seh/deployment/'):
            if self.path != path:
                self._respond(400, {'error': {'code': 'SEH_MANAGEMENT_QUERY_NOT_ALLOWED'}})
                return
            from .seh_deployment_management import management_request, SEHManagementRefused
            try:
                authority.authenticate(token, admin=True)
                manager = getattr(self.server, 'seh_deployment_manager', None)
                if manager is None:
                    raise SEHManagementRefused('SEH_MANAGEMENT_UNAVAILABLE', 503)
                result = management_request(manager, method, path, body)
            except SLHError as exc:
                self._respond(exc.status, {'error': {'code': exc.code}})
                return
            except SEHManagementRefused as exc:
                self._respond(exc.status, {'error': {'code': exc.code}})
                return
            self._respond(200, result)
            return
        # All normal organizational operations use SLHAPI, sharing its
        # authentication/permissions. Never dispatch bootstrap or localhost
        # endpoints through this network-facing transport.
        if not (path.startswith('/v1/auth/') or path.startswith('/v1/admin/')
                or path.startswith('/v1/enrollment/')
                or path == '/v1/work' or path.startswith('/v1/work/')
                or path in ('/v1/wallets', '/v1/credits', '/v1/accounting/reconcile',
                            '/v1/accounting/obligations', '/v1/accounting/sweeps',
                            '/v1/accounting/sweep')):
            self._respond(404, {'error': {'code': 'ENDPOINT_NOT_FOUND'}})
            return
        status, response = self.server.api.call(method, path, body, token=token, loopback=False)
        if status < 300 and method == 'PATCH' and path == '/v1/admin/scheduler/policy':
            if self.server.marketplace_controller:
                self.server.marketplace_controller.wake()
        self._respond(status, response)

    def _seh_connection_status(self, cfg):
        connection = (self.server.gateway.seh_connection if self.server.gateway else None)
        snapshot = (connection.connection_diagnostics() if connection is not None else {})
        return {'schema': 'main-computer.slh-seh-connection.v1',
                'configuration_owner': 'org-slh',
                'relay_role': 'opaque-passthrough',
                'connection_enabled': cfg['seh_enabled'],
                'resident_gateway_ready': self.server.gateway is not None,
                'connected': snapshot.get('connected') is True,
                'phase': snapshot.get('phase', 'NOT_STARTED'),
                'last_error_class': snapshot.get('last_error_class')}

    def _marketplace_status(self):
        market = self.server.marketplace
        # Export only nonsecret status. The upstream MSK never appears in a
        # status response, log or public identity endpoint.
        with market._lock:
            sessions = {
                did: {'device_id': did,
                      'connection_id': client.accepted.get('connection_id'),
                      'account_id': client.accepted.get('account_id'),
                      'connected': bool(client.is_alive)}
                for did, client in market.sessions.items()
            }
        return {'schema': 'main-computer.slh-org-marketplace.v1',
                'sessions': sessions,
                'workers': self.server.gateway.scheduler.snapshot(),
                'automatic': self.server.marketplace_controller.status()}

    def _respond(self, status, data):
        raw = json.dumps(data, sort_keys=True).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(raw)


def _restart_outbound_seh(server) -> None:
    """After HTTP acknowledgement, restart only this SLH's relay supervisor.

    The ORG-SLH API and Worker listener remain live. The ordinary resident
    watcher will retry a failed reconnect without a second authority path.
    """
    if server.gateway is None or not server.workers_enabled:
        return
    try:
        with getattr(server, 'seh_operation_lock', _SEH_OPERATION_LOCK):
            if not read_config(server.state_dir)['seh_enabled']:
                return
            previous = server.gateway.seh_connection
            if previous is not None:
                previous.stop()
                server.gateway.seh_connection = None
            server.gateway.allow_seh = True
            server.gateway.start_canonical_seh()
            server.seh_enabled = True
    except (OSError, RuntimeError, ValueError) as exc:
        server.seh_activation_error = type(exc).__name__


def _refresh_outbound_seh(server) -> None:
    """Honor Local Service-approved SEH enablement in the existing child.

    Both processes read the same protected organization configuration, but
    they DO NOT share bearer sessions. The Worker gateway owns connection
    startup and supervision; this function never authenticates a new device,
    creates a credential, or rebinds the Worker listener.
    """
    if server.gateway is None or not server.workers_enabled:
        return
    try:
        with getattr(server, 'seh_operation_lock', _SEH_OPERATION_LOCK):
            cfg = read_config(server.state_dir)
            if not cfg['seh_enabled']:
                connection = server.gateway.seh_connection
                if connection is not None:
                    connection.stop()
                    server.gateway.seh_connection = None
                server.gateway.allow_seh = False
                server.seh_enabled = False
                return
            if server.gateway.seh_connection is None:
                server.gateway.allow_seh = True
                server.gateway.start_canonical_seh()
            server.seh_enabled = True
        server.seh_activation_error = None
    except (RuntimeError, ValueError, OSError) as exc:
        # Keep the resident child and existing Worker services running. The
        # supervisor will try again on the next parent-watch iteration.
        # Expose only the exception class, never remote keys or credentials.
        server.seh_activation_error = type(exc).__name__


def _assert_controller_alive(parent_pid: int, parent_created: float) -> None:
    if parent_pid <= 0 or parent_created <= 0:
        raise RuntimeError('Organization Service requires controlling Local Service')
    try:
        proc = psutil.Process(parent_pid)
        if abs(proc.create_time() - parent_created) > 0.01 or not proc.is_running():
            raise RuntimeError('Controlling Local Service is not running')
    except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
        raise RuntimeError('Controlling Local Service is unavailable') from exc


def run(*, state_dir: Path, parent_pid: int, parent_created: float) -> int:
    _assert_controller_alive(parent_pid, parent_created)
    authority = SLHAuthority(state_dir)
    if not authority.initialized():
        raise RuntimeError('Organization Service cannot start before administrator bootstrap')
    config = read_config(state_dir)
    if not config['configured'] or not config['enabled'] or not config['activation_confirmed']:
        raise RuntimeError('Organization Service is not explicitly enabled')
    # Every launch path publishes this child's PID and creation time before
    # announcing a listener, and removes only its own record on exit.
    with registered_process(state_dir, parent_pid=parent_pid, parent_created=parent_created):
        return _run_ready_service(state_dir, parent_pid, parent_created, authority, config)


def _trash_retention_loop(authority: SLHAuthority, stop: threading.Event, server=None,
                          *, interval_seconds: float = 60.0) -> None:
    # Durable ORG-owned policy; no MCEL page or connected browser is necessary.
    while not stop.is_set():
        try:
            result=authority.purge_expired_trash()
        except Exception as exc:
            # A cleanup fault must not kill the identity service. Expose the
            # error class, not keys, tokens, or potentially sensitive paths.
            if server is not None:
                server.trash_retention_last_error=type(exc).__name__
        else:
            if server is not None:
                server.trash_retention_last_error=None
                server.trash_retention_last_removed_count=result['removed_count']
        if stop.wait(interval_seconds):
            break


def _run_ready_service(state_dir: Path, parent_pid: int, parent_created: float,
                       authority: SLHAuthority, config: dict) -> int:
    # ORG-SLH, not LMS-SLH, creates and owns the durable bearer root.
    authority.ensure_local_auth_key()
    tls_context, fingerprint = transport_identity(state_dir)
    server = OrganizationHTTPServer((config['host'], config['api_port']), OrganizationHTTPHandler)
    server.socket = tls_context.wrap_socket(server.socket, server_side=True)
    server.seh_identity_only = False
    server.authority = authority
    server.state_dir = Path(state_dir)
    server.wallet_service = OrganizationWallet(state_dir)
    from .seh_deployment_management import SLHSEHDeploymentManager
    server.seh_deployment_manager = SLHSEHDeploymentManager(state_dir=state_dir)
    server.api = SLHAPI(authority, wallet_service=server.wallet_service,
                        root_api_enabled=True)
    server.fingerprint = fingerprint
    server.workers_enabled = config['workers_enabled']
    server.seh_enabled = config['seh_enabled']
    server.seh_activation_error = None
    server.seh_operation_lock = threading.RLock()
    server.marketplace = None
    server.marketplace_controller = None
    server.gateway = None
    server.private_requests = None
    server.pinky_sweeper = None
    stop_watch = threading.Event()
    server.trash_retention_last_error=None
    server.trash_retention_last_removed_count=0
    trash_thread = threading.Thread(target=_trash_retention_loop,
                                    args=(authority,stop_watch,server),daemon=True,
                                    name='slh-org-trash-retention')
    trash_thread.start()
    def check_controller():
        while not stop_watch.wait(2):
            try:
                _assert_controller_alive(parent_pid, parent_created)
            except RuntimeError:
                server.shutdown()
                return
            _refresh_outbound_seh(server)
    watcher = threading.Thread(target=check_controller, daemon=True,
                               name='slh-local-parent-watch')
    watcher.start()
    def handle_shutdown(_signum, _frame):
        threading.Thread(target=server.shutdown, daemon=True).start()
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, handle_shutdown)
        signal.signal(signal.SIGINT, handle_shutdown)
    gateway = None
    gateway_thread = None
    seh_admin_server = None
    seh_admin_thread = None
    try:
        if config['workers_enabled']:
            seh_admin_server = make_seh_identity_listener(
                authority=authority, api=server.api,
                tls_context=tls_context, fingerprint=fingerprint)
            seh_admin_thread = threading.Thread(
                target=seh_admin_server.serve_forever,
                kwargs={'poll_interval': 0.2}, daemon=True,
                name='slh-seh-identity-listener')
            seh_admin_thread.start()
            gateway = SLHManagedGateway(authority, host=config['host'], port=config['worker_port'],
                                        allow_seh=config['seh_enabled'],
                                        seh_admin_target=('127.0.0.1', seh_admin_server.server_port))
            gateway_thread = gateway.start_background()
            # The worker gateway must actually bind before the Organization API
            # announces readiness. A failed bind aborts the whole child.
            import time
            for _ in range(100):
                if gateway._server is not None:
                    break
                if not gateway_thread.is_alive():
                    raise RuntimeError('Worker listener failed to start')
                time.sleep(0.03)
            if gateway._server is None:
                raise RuntimeError('Worker listener startup timed out')
            server.gateway = gateway
            # Private work remains on the authenticated local managed-worker
            # scheduler, whether or not the SEH outbound tunnel is enabled.
            # Do not route private payloads through the public SEH marketplace.
            # The private ledger and periodic Reserve sweeper must remain live
            # while the existing public relay is connected.
            server.private_requests = PrivateRequests(state_dir, authority, gateway,
                                                      wallet_service=server.wallet_service)
            gateway.scheduler.credit_provider = server.private_requests.net_credit_status
            server.api.private_requests = server.private_requests
            server.pinky_sweeper = PinkySweep(server.private_requests, server.wallet_service)
            server.api.pinky_sweeper = server.pinky_sweeper
            server.pinky_sweeper.start()
            server.marketplace = SLHMarketplaceProxy(gateway)
            server.marketplace_controller = AutomaticMarketplace(server.marketplace, state_dir, wallet_service=server.wallet_service)
            server.private_requests.marketplace_controller = server.marketplace_controller
            gateway.on_worker_registered = server.marketplace_controller.wake
            gateway.on_worker_disconnected = server.marketplace_controller.wake
            server.marketplace_controller.start()
        print(json.dumps({'service': 'slh-organization', 'state': 'listening',
                          'host': config['host'], 'api_port': config['api_port'],
                          'worker_port': config['worker_port'] if config['workers_enabled'] else None,
                          'tls_sha256': fingerprint}), flush=True)
        server.serve_forever(poll_interval=0.2)
    finally:
        stop_watch.set()
        watcher.join(timeout=3)
        trash_thread.join(timeout=3)
        server.server_close()
        if server.pinky_sweeper:
            server.pinky_sweeper.stop()
        if server.marketplace_controller:
            server.marketplace_controller.stop()
        if server.marketplace:
            server.marketplace.close()
        if gateway:
            gateway.stop()
        if seh_admin_server is not None:
            seh_admin_server.shutdown()
            seh_admin_server.server_close()
            if seh_admin_thread is not None:
                seh_admin_thread.join(timeout=3)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='SLH Organization Service — TLS only')
    parser.add_argument('command', choices=['run'])
    parser.add_argument('--state-dir', type=Path, default=slh_state_dir())
    parser.add_argument('--parent-pid', type=int, required=True)
    parser.add_argument('--parent-created', type=float, required=True)
    args = parser.parse_args(argv)
    return run(state_dir=args.state_dir, parent_pid=args.parent_pid,
               parent_created=args.parent_created)


if __name__ == '__main__':
    raise SystemExit(main())
