"""Resident loopback SLH control plane (Patch 1, never an org listener).

Supervisor-owned invocation: python -m main_computer.slh.gateway_service run
Optional OS boot invocation: python -m main_computer.slh.local_service run
Both use the same exclusive 127.0.0.1:8897 listener and state directory.
"""
from __future__ import annotations

import argparse
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import socket
import time
from typing import Callable
from wsgiref.simple_server import make_server

from main_computer.slh.api import SLHAPI
from main_computer.slh.authenticator_store import LocalIdentityStore
from main_computer.slh.local_authenticator_api import LocalAuthenticatorAPI
from main_computer.slh.local_root_registration import LocalRootAdministratorRegistration
from main_computer.slh.authority import SLHAuthority
from main_computer.slh.organization_state import initialize_disabled, organization_status
from main_computer.slh.organization_controller import OrganizationController
from main_computer.slh.ports import DEFAULT_SLH_API_HOST, DEFAULT_SLH_API_PORT
from main_computer.slh.storage import guard_legacy_state, secure_machine_state_dir, slh_state_dir
from tools.slh_auth_harness import ExclusiveSLHServer
from tools.slh_seh_connector import service_identity


def local_status(authority: SLHAuthority, port: int, controller=None) -> dict:
    return {
        'schema': 'main-computer.slh-local-status.v1',
        'local_service': {'state': 'running', 'binding': 'loopback',
                          'host': DEFAULT_SLH_API_HOST, 'port': port},
        'initialization': authority.status(),
        'administrator_enrollment': authority.administrator_enrollment_status(),
        'organization_service': (controller.status() if controller is not None and authority.initialized()
                                 and controller.config()['enabled'] else organization_status(authority.directory)),
    }


def create_server(*, state_dir: Path, port: int = DEFAULT_SLH_API_PORT,
                  authority_factory: Callable[[Path], SLHAuthority] = SLHAuthority,
                  identity_dir: Path | None = None):
    """Initialize local-only state then bind; caller owns serve_forever/shutdown."""
    directory = Path(state_dir)
    guard_legacy_state(directory)
    # Prepare the signing identity before normal bootstrap writes config or SQL.
    # A nonempty pre-existing state without this key is NOT a fresh SLH.
    if not directory.exists() or not any(directory.iterdir()):
        service_identity(directory, create=True, directory_protector=secure_machine_state_dir)
    else:
        secure_machine_state_dir(directory)
    initialize_disabled(directory)
    # The resident LMS-SLH owns the installation's ordinary local identities.
    # This is independent of Organization Service activation and enrollment.
    resolved_identity_dir = Path(identity_dir) if identity_dir is not None else directory / 'identities'
    if identity_dir is None:
        secure_machine_state_dir(resolved_identity_dir)
    LocalIdentityStore(resolved_identity_dir).ensure_default_identity()
    authority = authority_factory(directory)
    controller = OrganizationController(authority)
    bound_port = [port]
    authenticator = LocalAuthenticatorAPI(resolved_identity_dir)
    api = SLHAPI(authority, local_status_provider=lambda: local_status(authority, bound_port[0], controller),
                 organization_controller=controller,
                 local_authenticator=authenticator,
                 local_root_registration=LocalRootAdministratorRegistration(authority, authenticator))
    try:
        server = make_server(DEFAULT_SLH_API_HOST, port, api.wsgi_app, server_class=ExclusiveSLHServer)
    except BaseException:
        controller.close()
        raise
    original_close = server.server_close
    def close_with_child():
        controller.close()
        original_close()
    server.server_close = close_with_child
    server.organization_controller = controller
    bound_port[0] = server.server_port
    return server


def _existing_local_service(port: int) -> bool:
    """Validate that an already-bound port is our own local status endpoint."""
    conn = HTTPConnection(DEFAULT_SLH_API_HOST, port, timeout=2)
    try:
        conn.request('GET', '/v1/local/status', headers={'Host': f'127.0.0.1:{port}'})
        response = conn.getresponse()
        data = json.loads(response.read(8192))
        return (response.status == 200 and isinstance(data, dict)
                and data.get('schema') == 'main-computer.slh-local-status.v1'
                and data.get('local_service', {}).get('binding') == 'loopback')
    except (OSError, ValueError, TypeError):
        return False
    finally:
        conn.close()


def run(*, state_dir: Path | None = None, port: int = DEFAULT_SLH_API_PORT,
        identity_dir: Path | None = None, attach_existing: bool = False, poll_seconds: float = 5.0,
        sleep: Callable[[float], None] = time.sleep) -> int:
    directory = Path(state_dir) if state_dir is not None else slh_state_dir()
    while True:
        if attach_existing and _existing_local_service(port):
            print(json.dumps({'service': 'slh-local', 'state': 'attached-to-existing',
                              'port': port}), flush=True)
            while _existing_local_service(port):
                sleep(max(0.1, poll_seconds))
            continue
        try:
            with create_server(state_dir=directory, port=port, identity_dir=identity_dir) as server:
                print(json.dumps({'service': 'slh-local', 'state': 'listening',
                                  'host': DEFAULT_SLH_API_HOST, 'port': server.server_port,
                                  'organization_service': 'not-started'}), flush=True)
                server.serve_forever(poll_interval=0.5)
            return 0
        except OSError as exc:
            if not attach_existing or not _existing_local_service(port):
                print(json.dumps({'service': 'slh-local', 'state': 'listener-unavailable',
                                  'error': str(exc)}), flush=True)
                return 2
            # A Windows startup task may already own this service. Supervisor
            # follows it and takes over only when the boot instance disappears.
            print(json.dumps({'service': 'slh-local', 'state': 'attached-to-existing',
                              'port': port}), flush=True)
            while _existing_local_service(port):
                sleep(max(0.1, poll_seconds))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Resident localhost SLH control service')
    parser.add_argument('--root', type=Path, default=Path('.'), help='Main Computer install root')
    parser.add_argument('--state-dir', type=Path, default=None)
    parser.add_argument('--port', type=int, default=int(os.environ.get('MAIN_COMPUTER_SLH_LOCAL_PORT', DEFAULT_SLH_API_PORT)))
    parser.add_argument('--identity-dir', type=Path, default=None,
                        help='Explicit disposable identity store override for on-demand local testing')
    parser.add_argument('--attach-existing', action='store_true')
    parser.add_argument('command', choices=('run',))
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    return run(state_dir=args.state_dir, port=args.port, identity_dir=args.identity_dir,
               attach_existing=args.attach_existing)


if __name__ == '__main__':
    raise SystemExit(main())
