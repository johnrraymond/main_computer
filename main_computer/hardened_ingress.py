"""Fail-closed, opt-in authenticated Viewport ingress for MCEL candidate certification.

The normal legacy route is not automatically promoted.  Hardened mode requires
that every non-public backend request carry a short-lived, body-bound assertion
issued by the TLS bootstrap *after* checking its persistent browser session.
Document Server callbacks are separately authenticated by document capability
and JWT inside the ONLYOFFICE handler; a browser session must never be sent to
Document Server.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import secrets
import threading
import time
from http import HTTPStatus
from urllib.parse import urlsplit

HEADER = 'X-MC-Gateway-Assertion'
MAX_BODY = 70 * 1024 * 1024  # 50 MiB XLSX uploads after base64 / JSON overhead
PUBLIC_ROUTES = frozenset({'/health', '/healthz', '/healthcheck', '/api/health'})
DOC_ROUTES = frozenset({'/api/applications/onlyoffice/file', '/api/applications/onlyoffice/callback'})


def enabled() -> bool:
    return os.environ.get('MAIN_COMPUTER_HARDENED_CANDIDATE') == '1'


def _json_b64(fields: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(fields, sort_keys=True, separators=(',', ':')).encode()).decode().rstrip('=')


def _signing_key(key: bytes) -> bytes:
    return hmac.new(key, b'MC-GATEWAY-ASSERTION-V1', hashlib.sha256).digest()


def issue_assertion(key: bytes, *, session: str, method: str, path: str, body: bytes, host: str,
                    now: float | None = None) -> str:
    if not session or len(body) > MAX_BODY or not path.startswith('/') or '://' in path:
        raise ValueError('INVALID_GATEWAY_ASSERTION_INPUT')
    fields = {'v': 1, 't': int(time.time() if now is None else now), 'n': secrets.token_hex(16),
              's': session, 'm': method, 'p': path, 'h': host.lower(),
              'd': hashlib.sha256(body).hexdigest()}
    data = _json_b64(fields)
    tag = hmac.new(_signing_key(key), data.encode('ascii'), hashlib.sha256).hexdigest()
    return data + '.' + tag


class AssertionVerifier:
    def __init__(self, authority, *, clock=time.time):
        self.authority = authority
        self.clock = clock
        self.lock = threading.RLock()
        self.used: dict[str, int] = {}

    def verify(self, assertion: str, *, method: str, path: str, body: bytes, host: str) -> bool:
        if not isinstance(assertion, str) or len(assertion) > 1024 or assertion.count('.') != 1:
            return False
        encoded, tag = assertion.split('.')
        good = hmac.new(_signing_key(self.authority.key), encoded.encode('ascii'), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(good, tag):
            return False
        try:
            fields = json.loads(base64.urlsafe_b64decode(encoded + '==='))
        except (ValueError, UnicodeError):
            return False
        now = int(self.clock())
        if (not isinstance(fields, dict) or fields.get('v') != 1
                or type(fields.get('t')) is not int or abs(fields['t'] - now) > 15
                or not isinstance(fields.get('n'), str) or len(fields['n']) != 32
                or fields.get('m') != method or fields.get('p') != path
                or fields.get('h') != host.lower()
                or not hmac.compare_digest(str(fields.get('d', '')), hashlib.sha256(body).hexdigest())
                or not isinstance(fields.get('s'), str)):
            return False
        with self.lock:
            self.used = {n: deadline for n, deadline in self.used.items() if deadline >= now}
            if fields['n'] in self.used:
                return False
            # Revalidate against persistent, revocable session state at every
            # request; a valid HMAC does NOT authorize a revoked browser cookie.
            if not self.authority.sessions.valid(fields['s']):
                return False
            self.used[fields['n']] = now + 20
            return True


def _reject(handler, status: HTTPStatus, error: str) -> bool:
    handler._send_json({'ok': False, 'error': error}, status=status)
    return False


def admit(handler, method: str) -> bool:
    """Run before any legacy router, including its early direct dispatch paths."""
    if not enabled():
        return True
    path = urlsplit(handler.path).path
    if method == 'GET' and path in PUBLIC_ROUTES:
        return True
    if path in DOC_ROUTES:
        # The ONLYOFFICE routes verify signed document capabilities/JWT before
        # storage/network effects. Never accept an unauthenticated general API
        # route as an exception to ingress.
        return True
    verifier = getattr(handler.server, 'mc_gateway_verifier', None)
    if verifier is None:
        return _reject(handler, HTTPStatus.SERVICE_UNAVAILABLE, 'GATEWAY_AUTH_UNCONFIGURED')
    if handler.headers.get('Transfer-Encoding'):
        return _reject(handler, HTTPStatus.BAD_REQUEST, 'CHUNKED_BODY_NOT_ALLOWED')
    body = b''
    if method in ('POST', 'PUT', 'PATCH'):
        lengths = handler.headers.get_all('Content-Length', [])
        if len(lengths) != 1 or not lengths[0].isdigit() or int(lengths[0]) > MAX_BODY:
            return _reject(handler, HTTPStatus.BAD_REQUEST, 'INVALID_BODY_LENGTH')
        body = handler.rfile.read(int(lengths[0]))
        if len(body) != int(lengths[0]):
            return _reject(handler, HTTPStatus.BAD_REQUEST, 'TRUNCATED_BODY')
        handler.rfile = io.BytesIO(body)
    assertion = handler.headers.get(HEADER, '')
    host = handler.headers.get('Host', '')
    if not verifier.verify(assertion, method=method, path=handler.path, body=body, host=host):
        return _reject(handler, HTTPStatus.UNAUTHORIZED, 'GATEWAY_AUTH_REQUIRED')
    return True
