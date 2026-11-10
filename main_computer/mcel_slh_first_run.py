"""Bounded on-demand SLH first-run adapter for the local MCEL application.

The local OS account executing the viewport is the bootstrap trust boundary. No
remote/local-network caller may use this adapter: the viewport route must enforce
loopback, exact same-origin, a non-simple operation header and explicit user action.
Only the existing loopback SLH API can initialize/configure the authority. Never
edit authority SQLite/config files or invent a second authentication path.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time

from main_computer.mcel_slh_worker_bridge import OrganizationWorkerClient, WorkerBridgeError
from main_computer.slh.authenticator_store import LocalIdentityStore
from main_computer.slh.ports import DEFAULT_SLH_API_PORT, DEFAULT_SLH_MANAGED_PORT
from main_computer.slh.storage import slh_state_dir

_ROOT = Path(__file__).resolve().parents[1]
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z')
_ACTIONS = frozenset(('start', 'bootstrap', 'configure', 'activate', 'login', 'logout'))
_START_LOCK = threading.RLock()


class FirstRunError(Exception):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code, self.status = code, status


def _identity_id(value: object) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise FirstRunError('INVALID_ADMIN_DEVICE_ID')
    return value


def _port(value: object) -> int:
    if type(value) is int:
        out = value
    elif isinstance(value, str) and re.fullmatch(r'[0-9]{1,5}', value):
        out = int(value)
    else:
        raise FirstRunError('INVALID_PORT')
    if not 1 <= out <= 65535 or out == DEFAULT_SLH_API_PORT:
        raise FirstRunError('INVALID_PORT')
    return out


def _assigned_private_ip(value: object) -> str:
    """No wildcard, public, link-local or unassigned exposure in first-run."""
    if not isinstance(value, str):
        raise FirstRunError('INVALID_BIND_HOST')
    try:
        address = ipaddress.IPv4Address(value)
    except (ipaddress.AddressValueError, ValueError) as exc:
        raise FirstRunError('INVALID_BIND_HOST') from exc
    if str(address) == '127.0.0.1':
        return str(address)
    if (not address.is_private or address.is_loopback or address.is_link_local or
            address.is_unspecified or address.is_multicast or address.is_reserved):
        raise FirstRunError('UNSAFE_BIND_HOST', 403)
    import psutil
    assigned = {entry.address for entries in psutil.net_if_addrs().values() for entry in entries
                if entry.family == socket.AF_INET}
    if str(address) not in assigned:
        raise FirstRunError('BIND_HOST_NOT_ASSIGNED', 409)
    return str(address)


class SlhFirstRunController:
    """No ambient administrative authority: every consequential call is explicit.

    Identity discovery and signing are delegated to the LMS-SLH API; the
    viewport neither accesses production private keys nor invents authority.
    Tokens and bootstrap secrets never appear in status, semantic state, logs
    or operation receipts.
    """
    def __init__(self, *, state_dir: Path | None = None, identity_dir: Path | None = None,
                 local_port: int = DEFAULT_SLH_API_PORT, runner=None):
        self.state_dir = Path(state_dir) if state_dir is not None else slh_state_dir()
        # Only an explicit disposable test store is exposed for fixture setup.
        # Operational signing and public-key discovery always call LMS-SLH.
        self.identity_dir = Path(identity_dir) if identity_dir is not None else None
        self.identities = LocalIdentityStore(identity_dir) if identity_dir is not None else None
        self.local_port = local_port
        self.runner = runner or subprocess.Popen

    def _request(self, method: str, path: str, body: dict | None = None, token: str = '') -> dict:
        if (method, path) not in {
            ('GET', '/v1/local/status'), ('GET', '/v1/local/organization/config'),
            ('POST', '/v1/auth/challenge'), ('POST', '/v1/auth/verify'),
            ('POST', '/v1/bootstrap/begin'), ('POST', '/v1/local/organization/enable'),
            ('POST', '/v1/local/organization/admin/identities'),
            ('PATCH', '/v1/local/organization/config'),
        } and not (re.fullmatch(r'/v1/bootstrap/[0-9a-f]{32}/administrator', path) and method == 'PUT') and not (
            re.fullmatch(r'/v1/bootstrap/[0-9a-f]{32}/activate-p256', path) and method == 'POST') and not (
            method == 'GET' and re.fullmatch(r'/v1/local/authenticator/identities/[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', path)) and not (
            method == 'POST' and re.fullmatch(r'/v1/local/authenticator/identities/[A-Za-z0-9][A-Za-z0-9_.-]{0,63}/sign', path)):
            raise FirstRunError('OPERATION_NOT_ADMITTED', 403)
        connection = http.client.HTTPConnection('127.0.0.1', self.local_port, timeout=3)
        try:
            payload = None if body is None else json.dumps(body, separators=(',', ':')).encode()
            headers = {'Accept': 'application/json', 'Connection': 'close'}
            if token:
                headers['Authorization'] = 'Bearer ' + token
            if payload is not None:
                headers['Content-Type'] = 'application/json'
            connection.request(method, path, body=payload, headers=headers)
            response = connection.getresponse()
            raw = response.read(65537)
            if len(raw) > 65536:
                raise FirstRunError('LOCAL_RESPONSE_TOO_LARGE', 502)
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise FirstRunError('LOCAL_RESPONSE_INVALID', 502)
            if response.status >= 300:
                error = result.get('error', {})
                code = error.get('code', 'LOCAL_OPERATION_REFUSED') if isinstance(error, dict) else 'LOCAL_OPERATION_REFUSED'
                if not isinstance(code, str) or not re.fullmatch(r'[A-Z_]{3,64}', code):
                    code = 'LOCAL_OPERATION_REFUSED'
                raise FirstRunError(code, response.status if response.status in (400, 401, 403, 404, 409) else 503)
            return result
        except FirstRunError:
            raise
        except (OSError, ValueError, UnicodeError, http.client.HTTPException) as exc:
            raise FirstRunError('SLH_LOCAL_UNAVAILABLE', 503) from exc
        finally:
            connection.close()

    def _identity(self, device_id: str) -> dict:
        device_id = _identity_id(device_id)
        result = self._request('GET', f'/v1/local/authenticator/identities/{device_id}')
        if (result.get('device_id') != device_id or not isinstance(result.get('public_key'), str)
                or not isinstance(result.get('fingerprint'), str) or result.get('usable') is not True):
            raise FirstRunError('LOCAL_IDENTITY_UNVERIFIED', 503)
        return result

    def _sign(self, device_id: str, challenge: dict) -> str:
        device_id = _identity_id(device_id)
        signed = self._request('POST',
            f'/v1/local/authenticator/identities/{device_id}/sign', {'challenge': challenge})
        signature = signed.get('signature')
        if (signed.get('device_id') != device_id or not isinstance(signature, str)
                or not 1 <= len(signature) <= 512):
            raise FirstRunError('LOCAL_IDENTITY_SIGNATURE_UNVERIFIED', 503)
        return signature

    def status(self) -> dict:
        try:
            data = self._request('GET', '/v1/local/status')
            if data.get('schema') != 'main-computer.slh-local-status.v1' or (
                data.get('local_service', {}).get('binding') != 'loopback'):
                raise FirstRunError('LOCAL_IDENTITY_UNVERIFIED', 503)
            return {'local': 'RUNNING', 'initialized': bool(data.get('initialization', {}).get('initialized')),
                    'organization': data.get('organization_service', {}).get('state', 'NOT_OBSERVED')}
        except FirstRunError as exc:
            if exc.code == 'SLH_LOCAL_UNAVAILABLE':
                return {'local': 'UNREACHABLE', 'initialized': None, 'organization': 'NOT_OBSERVED'}
            raise

    def start(self) -> dict:
        with _START_LOCK:
            current = self.status()
            if current['local'] == 'RUNNING':
                return {'outcome': 'ALREADY_RUNNING', 'message': 'Existing loopback SLH control service verified'}
            command = [sys.executable, '-m', 'main_computer.slh.gateway_service', 'run',
                       '--state-dir', str(self.state_dir), '--port', str(self.local_port)]
            if self.identity_dir is not None:
                command += ['--identity-dir', str(self.identity_dir)]
            try:
                proc = self.runner(command, cwd=str(_ROOT), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   close_fds=True)
            except (OSError, ValueError) as exc:
                raise FirstRunError('LOCAL_START_FAILED', 503) from exc
            for _ in range(50):
                time.sleep(0.1)
                if self.status()['local'] == 'RUNNING':
                    return {'outcome': 'STARTED', 'message': 'Loopback SLH control service verified; no boot task created'}
                if proc.poll() is not None:
                    break
            raise FirstRunError('LOCAL_START_UNVERIFIED', 503)

    def bootstrap(self, *, admin_id: str, admin_name: str, device_id: str,
                  acknowledge_p256_only: bool) -> dict:
        if acknowledge_p256_only is not True:
            raise FirstRunError('P256_ONLY_ACKNOWLEDGEMENT_REQUIRED')
        admin_id, device_id = _identity_id(admin_id), _identity_id(device_id)
        if not isinstance(admin_name, str) or not 1 <= len(admin_name.strip()) <= 120:
            raise FirstRunError('INVALID_ADMIN_NAME')
        if self.status()['local'] != 'RUNNING':
            raise FirstRunError('SLH_LOCAL_UNAVAILABLE', 503)
        if self.status()['initialized']:
            raise FirstRunError('ALREADY_INITIALIZED', 409)
        secret_path = self.state_dir / 'bootstrap.secret'
        if secret_path.is_symlink() or not secret_path.is_file():
            raise FirstRunError('LOCAL_BOOTSTRAP_SECRET_UNAVAILABLE', 503)
        try:
            secret = secret_path.read_text(encoding='ascii').strip()
            if not 32 <= len(secret) <= 200:
                raise FirstRunError('LOCAL_BOOTSTRAP_SECRET_INVALID', 503)
            # The first administrator must select an existing LMS-SLH key.
            # MCEL neither creates keys nor reads private key files.
            identity = self._identity(device_id)
            public = identity['public_key']
            pending = self._request('POST', '/v1/bootstrap/begin', {'bootstrap_secret': secret})
            transaction_id = pending['transaction_id']
            proof = {**pending['proof_context'], 'admin_id': admin_id,
                     'device_id': device_id, 'public_key': public}
            self._request('PUT', f'/v1/bootstrap/{transaction_id}/administrator',
                          {'admin_id': admin_id, 'admin_name': admin_name.strip(),
                           'device_id': device_id, 'public_key': public,
                           'proof': self._sign(device_id, proof), 'bootstrap_secret': secret})
            self._request('POST', f'/v1/bootstrap/{transaction_id}/activate-p256',
                          {'bootstrap_secret': secret, 'defer_totp': True})
        except FirstRunError:
            raise
        except (KeyError, OSError, TypeError, ValueError) as exc:
            raise FirstRunError('BOOTSTRAP_FAILED', 503) from exc
        if not self.status()['initialized']:
            raise FirstRunError('BOOTSTRAP_UNVERIFIED', 503)
        return {'outcome': 'INITIALIZED', 'message': 'First administrator established; P-256 device-only authentication, no TOTP',
                'deviceId': device_id}

    def _local_admin_token(self, device_id: str) -> str:
        try:
            self._identity(device_id)
            challenge = self._request('POST', '/v1/auth/challenge', {'device_id': device_id})
            session = self._request('POST', '/v1/auth/verify',
                                    {'device_id': device_id, 'challenge_id': challenge['challenge_id'],
                                     'signature': self._sign(device_id, challenge)})
            token = session.get('access_token')
            if not isinstance(token, str) or len(token) < 24:
                raise FirstRunError('LOCAL_ADMIN_LOGIN_REFUSED', 403)
            self._request('GET', '/v1/local/organization/config', token=token)
            return token
        except (KeyError, TypeError, ValueError) as exc:
            raise FirstRunError('LOCAL_ADMIN_LOGIN_REFUSED', 403) from exc

    def configure(self, *, device_id: str, host: str, api_port: object,
                  worker_port: object) -> dict:
        host = _assigned_private_ip(host)
        api, worker = _port(api_port), _port(worker_port)
        if api == worker:
            raise FirstRunError('DUPLICATE_PORT')
        token = self._local_admin_token(device_id)
        self._request('PATCH', '/v1/local/organization/config',
                      {'host': host, 'api_port': api, 'worker_port': worker,
                       'workers_enabled': True, 'seh_enabled': False}, token=token)
        actual = self._request('GET', '/v1/local/organization/config', token=token)
        if (actual.get('host') != host or actual.get('api_port') != api or
                actual.get('worker_port') != worker or actual.get('enabled') or
                actual.get('seh_enabled') or not actual.get('workers_enabled')):
            raise FirstRunError('ORGANIZATION_CONFIGURATION_UNVERIFIED', 503)
        return {'outcome': 'CONFIGURED', 'message': 'Worker service configured but disabled; SEH remains disabled',
                'deviceId': device_id}

    def activate(self, *, device_id: str) -> dict:
        token = self._local_admin_token(device_id)
        self._request('POST', '/v1/local/organization/enable', {}, token=token)
        # An enable receipt alone does not mean the child is accepting TLS.
        for _ in range(50):
            observed = self.status()
            if observed['organization'] == 'running':
                return {'outcome': 'RUNNING', 'message': 'Organization Service running and TLS listening',
                        'deviceId': device_id}
            if observed['organization'] in ('failed', 'stopped_unexpectedly', 'configuration_error'):
                raise FirstRunError('ORGANIZATION_START_UNVERIFIED', 503)
            time.sleep(0.1)
        raise FirstRunError('ORGANIZATION_START_UNVERIFIED', 503)

    def login(self, *, device_id: str) -> dict:
        """One click: establish a genuinely fresh organization, then log in.

        An existing initialized organization cannot be re-bootstrapped. An
        already-enrolled administrator is never replaced or expanded by login.
        The trusted local service checks its own authority state; no remote
        enrollment lookup and no browser-managed root/bootstrap material.
        """
        device_id = _identity_id(device_id)
        with _START_LOCK:
            try:
                identity = self._identity(device_id)
                local = self._request('GET', '/v1/local/status')
                if local.get('schema') != 'main-computer.slh-local-status.v1':
                    raise FirstRunError('LOCAL_STATUS_UNVERIFIED', 503)
                enrollment = local.get('administrator_enrollment')
                state = enrollment.get('state') if isinstance(enrollment, dict) else None
                initialization = local.get('initialization')
                initialized = (initialization.get('initialized') if isinstance(initialization, dict) else None)
                fresh = initialized is False and state == 'NOT_ESTABLISHED'
                if fresh:
                    # The existing one-use local bootstrap establishes the
                    # organization. The selected ordinary identity supplies
                    # both its device and administrator labels; no form or
                    # hand-authored role-named key is needed.
                    name = identity.get('label') or device_id
                    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
                        name = device_id
                    self.bootstrap(admin_id=device_id, admin_name=name,
                                   device_id=device_id, acknowledge_p256_only=True)
                    # A new organization must have a TLS listener before it
                    # can authenticate. Configure only a fresh installation,
                    # using loopback defaults; preserve any pre-existing
                    # explicit network configuration when resetting the DB.
                    current = self._request('GET', '/v1/local/status')
                    org = current.get('organization_service')
                    if not isinstance(org, dict):
                        raise FirstRunError('ORGANIZATION_STATE_UNAVAILABLE', 503)
                    if org.get('configured') is False and org.get('state') == 'not_configured':
                        self.configure(device_id=device_id, host='127.0.0.1',
                                       api_port=DEFAULT_SLH_MANAGED_PORT + 1,
                                       worker_port=DEFAULT_SLH_MANAGED_PORT)
                    elif org.get('configured') is not True:
                        raise FirstRunError('ORGANIZATION_STATE_UNAVAILABLE', 409)
                    if self.status()['organization'] != 'running':
                        self.activate(device_id=device_id)
                elif initialized is True and state in ('MISSING', 'NO_ACTIVE_DEVICE'):
                    # V6-C conditional root grant: the ORG endpoint atomically
                    # rechecks that the active administrator set is empty.
                    self._request('POST', '/v1/local/organization/admin/identities',
                                  {'device_id': device_id, 'bootstrap_only': True})
                elif initialized is True and state == 'ENROLLED':
                    pass  # No grant. Only normal signed authentication.
                else:
                    raise FirstRunError('ADMINISTRATOR_STATE_UNAVAILABLE', 409)
                result = OrganizationWorkerClient(state_dir=self.state_dir).login_with_signer(
                    device_id, lambda challenge: self._sign(device_id, challenge))
            except WorkerBridgeError as exc:
                raise FirstRunError(exc.code, exc.status) from exc
            return result

    def logout(self, token: str) -> dict:
        try:
            return OrganizationWorkerClient(state_dir=self.state_dir).logout(token)
        except WorkerBridgeError as exc:
            raise FirstRunError(exc.code, exc.status) from exc

    def execute(self, action: str, values: dict, *, token: str = '') -> dict:
        if action not in _ACTIONS or not isinstance(values, dict):
            raise FirstRunError('OPERATION_NOT_ADMITTED', 403)
        signatures = {
            'start': (), 'bootstrap': ('adminId', 'adminName', 'deviceId', 'acknowledgeP256Only'),
            'configure': ('adminDeviceId', 'host', 'apiPort', 'workerPort'),
            'activate': ('adminDeviceId',), 'login': ('adminDeviceId',), 'logout': (),
        }
        if set(values) != set(signatures[action]):
            raise FirstRunError('INVALID_SETUP_ARGUMENTS')
        if action == 'start': return self.start()
        if action == 'bootstrap':
            return self.bootstrap(admin_id=values['adminId'], admin_name=values['adminName'],
                device_id=values['deviceId'], acknowledge_p256_only=values['acknowledgeP256Only'])
        if action == 'configure':
            return self.configure(device_id=values['adminDeviceId'], host=values['host'],
                api_port=values['apiPort'], worker_port=values['workerPort'])
        if action == 'activate': return self.activate(device_id=values['adminDeviceId'])
        if action == 'login': return self.login(device_id=values['adminDeviceId'])
        return self.logout(token)
