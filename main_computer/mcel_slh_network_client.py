"""Explicit, pinned ORG-SLH authentication from the local MCEL viewport.

A first-time peer is *not* authenticated merely because TLS encrypts traffic:
its observed self-signed certificate must be explicitly confirmed.  Signing is
performed by the LMS-SLH authenticator; no private key is loaded here or sent
to the browser. An unknown remote device never triggers enrollment.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import threading
import time

from main_computer.mcel_slh_first_run import SlhFirstRunController, FirstRunError
from main_computer.mcel_slh_network_probe import validate_target, NetworkSlhError
from main_computer.slh.storage import slh_app_user_dir, secure_private_state_file

_TRUST_LOCK = threading.RLock()
_SESSION_LOCK = threading.RLock()
_SESSIONS = {}  # opaque local tickets, never raw remote bearer tokens in the browser
_MAX_BODY = 65536
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z')
_PIN = re.compile(r'[a-fA-F0-9]{64}\Z')


class NetworkConnectionError(Exception):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code, self.status = code, status


def _target(address, port):
    # Keep the original local/private IP SSRF boundary; a TLS URL is not an
    # invitation to reach local control planes, arbitrary Internet hosts, etc.
    try:
        host, valid_port, _ = validate_target(address, port, '0'*64)
    except NetworkSlhError as exc:
        raise NetworkConnectionError(exc.code) from exc
    return host, valid_port


def _endpoint(host, port):
    return f'[{host}]:{port}' if ':' in host else f'{host}:{port}'


def _read_trust(path):
    if path.is_symlink():
        raise NetworkConnectionError('NETWORK_TRUST_STORE_UNSAFE', 503)
    if not path.exists():
        return {}
    try:
        if path.stat().st_size > 65536:
            raise ValueError('too large')
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or len(data) > 128 or any(
            not isinstance(k, str) or len(k) > 100 or
            not isinstance(v, str) or not _PIN.fullmatch(v) for k,v in data.items()
        ):
            raise ValueError('invalid trust store')
        return data
    except (OSError, ValueError, TypeError) as exc:
        raise NetworkConnectionError('NETWORK_TRUST_STORE_INVALID', 503) from exc


def _write_trust(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive temporary creation plus atomic replacement, no symlink following.
    temp = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
    try:
        with temp.open('x', encoding='utf-8') as f:
            json.dump(data, f, sort_keys=True, separators=(',', ':'))
            f.flush()
            os.fsync(f.fileno())
        secure_private_state_file(temp)
        if path.is_symlink():
            raise NetworkConnectionError('NETWORK_TRUST_STORE_UNSAFE', 503)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _json_request(conn, method, path, body=None, token=''):
    raw = None if body is None else json.dumps(body, separators=(',', ':')).encode('utf-8')
    headers = {'Accept':'application/json'}
    if raw is not None:
        headers['Content-Type'] = 'application/json'
    if token:
        headers['Authorization'] = 'Bearer ' + token
    conn.request(method, path, body=raw, headers=headers)
    reply = conn.getresponse()
    data = reply.read(_MAX_BODY+1)
    if len(data) > _MAX_BODY or not reply.getheader('Content-Type','').startswith('application/json'):
        raise NetworkConnectionError('NETWORK_RESPONSE_INVALID', 502)
    try:
        value = json.loads(data)
    except (UnicodeError, ValueError) as exc:
        raise NetworkConnectionError('NETWORK_RESPONSE_INVALID', 502) from exc
    if not isinstance(value, dict):
        raise NetworkConnectionError('NETWORK_RESPONSE_INVALID', 502)
    if reply.status not in (200,201):
        error = value.get('error', {})
        code = error.get('code') if isinstance(error, dict) else None
        if not isinstance(code, str) or not re.fullmatch(r'[A-Z_]{3,64}', code):
            code = 'NETWORK_ORG_REQUEST_REFUSED'
        raise NetworkConnectionError(code, reply.status if reply.status in (400,401,403,404,409) else 503)
    return value


def _connection(host, port, pin=None, *, connection_factory=None):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # certificate pin is checked *before* any HTTP
    conn = (connection_factory or http.client.HTTPSConnection)(host, port, timeout=4, context=context)
    try:
        conn.connect()
        fingerprint = hashlib.sha256(conn.sock.getpeercert(binary_form=True)).hexdigest()
        if pin and not hmac.compare_digest(pin, fingerprint):
            raise NetworkConnectionError('NETWORK_TLS_IDENTITY_CHANGED', 409)
        return conn, fingerprint
    except BaseException:
        conn.close()
        raise


class NetworkSlhClient:
    def __init__(self, *, trust_path=None, signer=None, connection_factory=None):
        self.trust_path = Path(trust_path) if trust_path is not None else slh_app_user_dir() / 'trusted-network-slh.json'
        self.signer = signer or (lambda device_id, challenge: SlhFirstRunController()._sign(device_id, challenge))
        self.connection_factory = connection_factory

    def connect(self, address, port, admin_device_id, *, confirm_pin=None):
        host, port = _target(address, port)
        if not isinstance(admin_device_id, str) or not _ID.fullmatch(admin_device_id):
            raise NetworkConnectionError('LOCAL_IDENTITY_REQUIRED')
        label = _endpoint(host, port)
        if confirm_pin is not None and (not isinstance(confirm_pin, str) or not _PIN.fullmatch(confirm_pin)):
            raise NetworkConnectionError('NETWORK_TRUST_CONFIRMATION_INVALID')
        # Lookup and TLS connection share one controlled boundary. Pin changes
        # are never silently re-trusted, even if an attacker controls the host.
        with _TRUST_LOCK:
            trusted = _read_trust(self.trust_path)
            prior = trusted.get(label)
        if prior is not None and confirm_pin and not hmac.compare_digest(prior, confirm_pin.lower()):
            raise NetworkConnectionError('NETWORK_TLS_IDENTITY_CHANGED', 409)
        try:
            conn, fingerprint = _connection(host, port, prior or (confirm_pin.lower() if confirm_pin else None),
                                           connection_factory=self.connection_factory)
            try:
                identity = _json_request(conn, 'GET', '/v1/org/identity')
                if identity.get('schema') != 'main-computer.slh-org-identity.v1' or not isinstance(identity.get('tls_sha256'), str) or not hmac.compare_digest(identity['tls_sha256'].lower(), fingerprint):
                    raise NetworkConnectionError('NETWORK_SERVICE_IDENTITY_MISMATCH', 502)
                status = _json_request(conn, 'GET', '/v1/org/status')
                if status.get('schema') != 'main-computer.slh-org-status.v1' or status.get('operational') is not True:
                    raise NetworkConnectionError('NETWORK_SERVICE_NOT_READY', 503)
                if prior is None and confirm_pin is None:
                    return {'pairingRequired':True, 'endpoint':label, 'tlsSha256':fingerprint}
                if prior is None:
                    with _TRUST_LOCK:
                        current = _read_trust(self.trust_path)
                        if current.get(label) not in (None, fingerprint):
                            raise NetworkConnectionError('NETWORK_TLS_IDENTITY_CHANGED', 409)
                        current[label] = fingerprint
                        _write_trust(self.trust_path, current)
                # Login challenge is requested only after pinned TLS identity
                # verification, and the selected LMS identity signs the exact
                # service-issued challenge without revealing the private key.
                challenge = _json_request(conn, 'POST', '/v1/auth/challenge', {'device_id':admin_device_id})
                if challenge.get('device_id') != admin_device_id or not isinstance(challenge.get('challenge_id'),str):
                    raise NetworkConnectionError('NETWORK_AUTH_CHALLENGE_INVALID', 502)
                try:
                    signature = self.signer(admin_device_id, challenge)
                except FirstRunError as exc:
                    raise NetworkConnectionError(exc.code, exc.status) from exc
                if not isinstance(signature, str) or not 1 <= len(signature) <= 512:
                    raise NetworkConnectionError('LOCAL_IDENTITY_SIGNATURE_INVALID', 503)
                session = _json_request(conn, 'POST', '/v1/auth/verify', {
                    'device_id':admin_device_id, 'challenge_id':challenge['challenge_id'], 'signature':signature})
                token = session.get('access_token')
                if not isinstance(token, str) or len(token) < 24 or session.get('device_id') != admin_device_id:
                    raise NetworkConnectionError('NETWORK_AUTH_SESSION_INVALID', 502)
                verified = _json_request(conn, 'GET', '/v1/auth/session', token=token)
                if verified.get('active') is not True or verified.get('device_id') != admin_device_id:
                    raise NetworkConnectionError('NETWORK_AUTH_SESSION_INVALID', 502)
                role = verified.get('role')
                if not isinstance(role, str) or len(role) > 40:
                    raise NetworkConnectionError('NETWORK_AUTH_SESSION_INVALID', 502)
                services = 'AUTHENTICATED'
                if role == 'admin':
                    service_status = _json_request(conn, 'GET', '/v1/org/admin/services', token=token)
                    services = ('WORKER_SERVICE_ENABLED' if service_status.get('worker_serving_enabled') is True
                                else 'WORKER_SERVICE_DISABLED' if service_status.get('worker_serving_enabled') is False
                                else 'SERVICE_STATUS_UNVERIFIED')
                expires = session.get('expires_at')
                if type(expires) is not int or expires <= int(time.time()):
                    raise NetworkConnectionError('NETWORK_AUTH_SESSION_INVALID', 502)
                ticket = secrets.token_urlsafe(32)
                with _SESSION_LOCK:
                    now = int(time.time())
                    for key,record in list(_SESSIONS.items()):
                        if record['expires'] <= now:
                            _SESSIONS.pop(key,None)
                    _SESSIONS[ticket] = {'host':host, 'port':port, 'pin':fingerprint,
                                         'device':admin_device_id, 'token':token, 'expires':expires}
                return {'pairingRequired':False,'sessionTicket':ticket,'endpoint':label,
                        'tlsSha256':fingerprint,'organizationState':'RUNNING',
                        'authenticationState':'AUTHENTICATED','role':role,'deviceId':admin_device_id,
                        'services':services,'observedAt':datetime.now(timezone.utc).isoformat(timespec='seconds'),
                        'explanation':'Verified TLS identity and signed device session'}
            finally:
                conn.close()
        except NetworkConnectionError:
            raise
        except (OSError, ssl.SSLError, http.client.HTTPException, ValueError, TypeError) as exc:
            raise NetworkConnectionError('NETWORK_SLH_UNAVAILABLE', 503) from exc

    def admin_request(self, ticket, method, path, body=None):
        if not isinstance(ticket,str) or not 30 <= len(ticket) <= 128:
            raise NetworkConnectionError('NETWORK_SESSION_REQUIRED',401)
        with _SESSION_LOCK:
            session=_SESSIONS.get(ticket)
            if session is None:
                raise NetworkConnectionError('NETWORK_SESSION_NOT_FOUND',401)
            session=dict(session)
        if session['expires']<=int(time.time()):
            with _SESSION_LOCK:_SESSIONS.pop(ticket,None)
            raise NetworkConnectionError('NETWORK_SESSION_EXPIRED',401)
        # The caller must use the exact allowlisted notebook request mapping.
        from main_computer.mcel_slh_identity_notebook import _READS, _ACTIONS
        allowed={(m,p) for m,p in _READS.values()} | {(m,p) for m,p in _ACTIONS.values() if '{id}' not in p}
        allowed_path=any(re.fullmatch(p.replace('{id}',r'[A-Za-z0-9_.-]{1,160}'),path) and m==method
                         for m,p in _ACTIONS.values() if '{id}' in p)
        if (method,path) not in allowed and not allowed_path:
            raise NetworkConnectionError('NETWORK_OPERATION_NOT_ADMITTED',403)
        try:
            conn,_=_connection(session['host'],session['port'],session['pin'],
                               connection_factory=self.connection_factory)
            try:
                return _json_request(conn,method,path,body,token=session['token'])
            finally:conn.close()
        except NetworkConnectionError:raise
        except (OSError,ssl.SSLError,http.client.HTTPException) as exc:
            raise NetworkConnectionError('NETWORK_SLH_UNAVAILABLE',503) from exc

    def disconnect(self, ticket):
        if not isinstance(ticket, str) or not 30 <= len(ticket) <= 128:
            raise NetworkConnectionError('NETWORK_SESSION_REQUIRED', 401)
        with _SESSION_LOCK:
            session = _SESSIONS.pop(ticket, None)
        if not session:
            raise NetworkConnectionError('NETWORK_SESSION_NOT_FOUND', 401)
        try:
            conn, _ = _connection(session['host'],session['port'], session['pin'],
                                  connection_factory=self.connection_factory)
            try:
                _json_request(conn,'POST','/v1/auth/logout',{},token=session['token'])
            finally:
                conn.close()
        except NetworkConnectionError:
            raise
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            raise NetworkConnectionError('NETWORK_LOGOUT_UNVERIFIED',503) from exc
        return {'authenticationState':'DISCONNECTED','explanation':'Remote session revoked'}
