"""LMS-owned, fixed-route SEH management IPC.

The resident LMS is the only SEH management executor. A colocated ORG-SLH
can forward a *previously authorized* request using an ephemeral HMAC key
inherited from its LMS parent. No public/SEH socket accepts these messages.
Ordinary same-machine clients use the loopback-only LMS listener directly.
"""
from __future__ import annotations

import hashlib
from contextvars import ContextVar
import hmac
import http.client
import os
import json
import re
import secrets
import threading
import time

from .ports import DEFAULT_SLH_API_PORT

# Request-scoped attribution is set only after signed IPC verification.
CURRENT_PRINCIPAL = ContextVar("seh_management_principal", default="local-machine")


class LMSForwardingError(Exception):
    def __init__(self, code: str, status: int = 503):
        super().__init__(code)
        self.code, self.status = code, status


def management_path(path: str) -> bool:
    return path in {
        '/v1/local/seh/deployment', '/v1/local/seh/deployment/controllers',
        '/v1/local/seh/deployment/selection', '/v1/local/seh/deployment/harness',
        '/v1/local/seh/deployment/operations',
    } or bool(re.fullmatch(r'/v1/local/seh/deployment/operations/[A-Za-z0-9][A-Za-z0-9_.-]{0,127}(?:/cancel)?', path))


def local_path(remote_path: str) -> str:
    prefix = '/v1/org/admin/seh/deployment'
    if remote_path != prefix and not remote_path.startswith(prefix + '/'):
        raise LMSForwardingError('LMS_FORWARD_ROUTE_REFUSED', 403)
    result = '/v1/local/seh/deployment' + remote_path[len(prefix):]
    if not management_path(result):
        raise LMSForwardingError('LMS_FORWARD_ROUTE_REFUSED', 403)
    return result


def canonical_body(body: dict | None) -> bytes:
    if body is None:
        return b''
    if type(body) is not dict:
        raise LMSForwardingError('LMS_FORWARD_INVALID_BODY', 400)
    value = json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                       allow_nan=False).encode('ascii')
    if len(value) > 8192:
        raise LMSForwardingError('LMS_FORWARD_BODY_TOO_LARGE', 413)
    return value


def signature(key: bytes, method: str, path: str, stamp: str, nonce: str,
              raw: bytes, principal: str = "local-machine") -> str:
    message = b'\n'.join((b'main-computer.seh-lms-ipc.v1', method.encode('ascii'),
                          path.encode('ascii'), stamp.encode('ascii'),
                          nonce.encode('ascii'), principal.encode('ascii'),
                          hashlib.sha256(raw).hexdigest().encode('ascii')))
    return hmac.new(key, message, hashlib.sha256).hexdigest()


class ForwardAdmission:
    def __init__(self, key: bytes):
        if type(key) is not bytes or len(key) != 32:
            raise ValueError('LMS IPC requires an ephemeral 256-bit key')
        self.key = key
        self._lock = threading.Lock()
        self._recent = {}

    def verify(self, method: str, path: str, raw: bytes, headers: dict) -> str:
        stamp = headers.get('HTTP_X_LMS_TIMESTAMP', '')
        nonce = headers.get('HTTP_X_LMS_NONCE', '')
        proof = headers.get('HTTP_X_LMS_SIGNATURE', '')
        principal = headers.get('HTTP_X_LMS_PRINCIPAL', '')
        if (headers.get('HTTP_X_LMS_FORWARDED') != 'v1' or
                not stamp.isascii() or not re.fullmatch(r'[0-9]{10,11}', stamp) or
                not re.fullmatch(r'[0-9a-f]{32}', nonce) or
                not re.fullmatch(r'[0-9a-f]{64}', proof) or
                not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,159}',principal) or
                method not in ('GET','POST') or not management_path(path)):
            raise LMSForwardingError('LMS_FORWARD_AUTH_REQUIRED', 403)
        now = int(time.time())
        if abs(now-int(stamp)) > 30 or not hmac.compare_digest(
            proof, signature(self.key, method, path, stamp, nonce, raw, principal)):
            raise LMSForwardingError('LMS_FORWARD_AUTH_REQUIRED', 403)
        with self._lock:
            self._recent = {n:t for n,t in self._recent.items() if t > now-65}
            if nonce in self._recent:
                raise LMSForwardingError('LMS_FORWARD_REPLAY_REFUSED', 403)
            if len(self._recent) >= 2048:
                raise LMSForwardingError('LMS_FORWARD_REPLAY_CACHE_FULL', 503)
            self._recent[nonce] = now
        return principal


class LMSManagementClient:
    """Only exact deployment-management operations, never arbitrary proxying."""
    def __init__(self, *, port: int | None = None, key: bytes | None = None,
                 connection_factory=None):
        if port is None:
            port=int(os.environ.get('MAIN_COMPUTER_SLH_LOCAL_PORT',DEFAULT_SLH_API_PORT))
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('Invalid LMS port')
        self.port = port
        self.key = key
        self.connection_factory = connection_factory or http.client.HTTPConnection

    def request(self, method: str, path: str, body: dict | None = None,
                *, principal: str | None = None) -> dict:
        if (method, path) not in {
            ('GET','/v1/local/seh/deployment'),
            ('POST','/v1/local/seh/deployment/controllers'),
            ('POST','/v1/local/seh/deployment/selection'),
            ('POST','/v1/local/seh/deployment/harness'),
            ('GET','/v1/local/seh/deployment/operations'),
        } and not ((method == 'GET' and re.fullmatch(
                r'/v1/local/seh/deployment/operations/[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', path)) or
                (method == 'POST' and re.fullmatch(
                r'/v1/local/seh/deployment/operations/[A-Za-z0-9][A-Za-z0-9_.-]{0,127}/cancel', path))):
            raise LMSForwardingError('LMS_OPERATION_NOT_ADMITTED', 403)
        if method == 'GET' and body is not None:
            raise LMSForwardingError('LMS_OPERATION_NOT_ADMITTED', 403)
        raw = canonical_body(body)
        headers = {'Host':f'127.0.0.1:{self.port}', 'Accept':'application/json',
                   'Connection':'close'}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        if self.key is not None:
            if type(principal) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,159}',principal):
                raise LMSForwardingError('LMS_FORWARD_PRINCIPAL_REQUIRED',403)
            stamp, nonce = str(int(time.time())), secrets.token_hex(16)
            headers.update({'X-LMS-Forwarded':'v1', 'X-LMS-Principal':principal, 'X-LMS-Timestamp':stamp,
                            'X-LMS-Nonce':nonce,
                            'X-LMS-Signature':signature(self.key,method,path,stamp,nonce,raw,principal)})
        try:
            conn=self.connection_factory('127.0.0.1', self.port, timeout=120)
            try:
                conn.request(method,path,body=raw or None,headers=headers)
                response=conn.getresponse()
                content=response.read(131073)
                if len(content)>131072:
                    raise LMSForwardingError('LMS_RESPONSE_TOO_LARGE',502)
                result=json.loads(content)
                if not isinstance(result,dict):
                    raise LMSForwardingError('LMS_RESPONSE_INVALID',502)
                if response.status not in (200,201):
                    code=result.get('error',{}).get('code') if isinstance(result.get('error'),dict) else None
                    if type(code) is not str or not re.fullmatch(r'[A-Z_]{3,80}',code):
                        code='LMS_MANAGEMENT_REFUSED'
                    raise LMSForwardingError(code,response.status)
                return result
            finally:
                conn.close()
        except LMSForwardingError:
            raise
        except (OSError, http.client.HTTPException, ValueError,UnicodeError):
            raise LMSForwardingError('LMS_MANAGEMENT_UNAVAILABLE',503) from None
