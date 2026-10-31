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
import signal
from pathlib import Path
import ssl
import threading
from urllib.parse import parse_qs, urlsplit

from .api import SLHAPI
from .authority import SLHAuthority, SLHError
from .managed_gateway import SLHManagedGateway, SLHMarketplaceProxy, transport_identity
from .organization_state import read_config
from .storage import slh_state_dir
from .marketplace_auto import AutomaticMarketplace, read_settings


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
        auth = self.headers.get('Authorization', '')
        token = auth[7:] if auth.startswith('Bearer ') else ''
        authority = self.server.authority
        if method == 'GET' and path == '/v1/org/identity':
            self._respond(200, {'schema': 'main-computer.slh-org-identity.v1',
                                'organization_id': authority.org_id(),
                                'tls_sha256': self.server.fingerprint})
            return
        if method == 'GET' and path == '/v1/org/status':
            self._respond(200, {'schema': 'main-computer.slh-org-status.v1',
                                'operational': True, 'organization_id': authority.org_id(),
                                'worker_serving_enabled': self.server.workers_enabled,
                                'seh_enabled': self.server.seh_enabled,
                                'seh_connection_verified': False})
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
                    '/v1/org/admin/marketplace/connection-diagnostics'):
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
        # All normal organizational operations use SLHAPI, sharing its
        # authentication/permissions. Never dispatch bootstrap or localhost
        # endpoints through this network-facing transport.
        if not (path.startswith('/v1/auth/') or path.startswith('/v1/admin/')
                or path.startswith('/v1/enrollment/')):
            self._respond(404, {'error': {'code': 'ENDPOINT_NOT_FOUND'}})
            return
        status, response = self.server.api.call(method, path, body, token=token, loopback=False)
        if status < 300 and method == 'PATCH' and path == '/v1/admin/scheduler/policy':
            if self.server.marketplace_controller:
                self.server.marketplace_controller.wake()
        self._respond(status, response)

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
    tls_context, fingerprint = transport_identity(state_dir)
    server = OrganizationHTTPServer((config['host'], config['api_port']), OrganizationHTTPHandler)
    server.socket = tls_context.wrap_socket(server.socket, server_side=True)
    server.authority = authority
    server.api = SLHAPI(authority)
    server.fingerprint = fingerprint
    server.workers_enabled = config['workers_enabled']
    server.seh_enabled = config['seh_enabled']
    server.marketplace = None
    server.marketplace_controller = None
    server.gateway = None
    stop_watch = threading.Event()
    def check_controller():
        while not stop_watch.wait(2):
            try:
                _assert_controller_alive(parent_pid, parent_created)
            except RuntimeError:
                server.shutdown()
                return
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
    try:
        if config['workers_enabled']:
            gateway = SLHManagedGateway(authority, host=config['host'], port=config['worker_port'],
                                        allow_seh=config['seh_enabled'])
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
            server.marketplace = SLHMarketplaceProxy(gateway)
            server.marketplace_controller = AutomaticMarketplace(server.marketplace, state_dir)
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
        server.server_close()
        if server.marketplace_controller:
            server.marketplace_controller.stop()
        if server.marketplace:
            server.marketplace.close()
        if gateway:
            gateway.stop()
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
