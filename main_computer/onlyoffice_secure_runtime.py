"""Hardened ONLYOFFICE candidate: document grants, signed callbacks, SSRF-safe saves.

Every browser operation is first authenticated by the signed gateway ingress.
Document Server callbacks and downloads authenticate independently using an
installation/document/key capability; a callback also requires an ONLYOFFICE
outbox JWT.  Revision ordering comes from the persistent DurableSaveJournal.
Nothing here changes production routing or enables the candidate automatically.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import time
from urllib.parse import urlsplit

from main_computer.onlyoffice_durable_journal import DurableSaveJournal

MAX_XLSX = 50 * 1024 * 1024
GRANT_SECONDS = 8 * 3600


class OnlyOfficeDenied(ValueError):
    pass


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def _unb64(value: str, *, max_chars: int = 2048) -> bytes:
    if not isinstance(value, str) or len(value) > max_chars or not re.fullmatch('[A-Za-z0-9_-]+', value):
        raise OnlyOfficeDenied('INVALID_BASE64URL')
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def shared_secret(config) -> bytes:
    if not getattr(config, 'onlyoffice_jwt_enabled', False):
        raise OnlyOfficeDenied('ONLYOFFICE_JWT_REQUIRED')
    secret = str(getattr(config, 'onlyoffice_jwt_secret', '') or '')
    if len(secret.encode('utf-8')) < 32:
        raise OnlyOfficeDenied('ONLYOFFICE_256_BIT_JWT_SECRET_REQUIRED')
    return secret.encode('utf-8')


def _key(secret: bytes, purpose: bytes) -> bytes:
    return hmac.new(secret, b'MC-OO-HARDENED-V1-' + purpose, hashlib.sha256).digest()


def installation_mode() -> str:
    mode = os.environ.get('MAIN_COMPUTER_INSTALLATION_MODE', '').strip().lower()
    if mode not in {'dev', 'unleashed', 'debug', 'safe'}:
        raise OnlyOfficeDenied('UNKNOWN_INSTALLATION_MODE')
    return mode


def journal_for(handler) -> DurableSaveJournal:
    return DurableSaveJournal(handler._onlyoffice_root(), _key(shared_secret(handler.server.config), b'journal'))


def active_key(handler, path: Path) -> str:
    journal = journal_for(handler)
    mode = installation_mode()
    status = journal.current(path, mode=mode)
    if status is None:
        key = secrets.token_hex(16)
        journal.register(path, mode=mode, key=key)
        return key
    if status['final_closed']:
        new_key = secrets.token_hex(16)
        journal.rotate_key(path, mode=mode, old_key=status['key'], new_key=new_key)
        return new_key
    return status['key']


def grant(secret: bytes, *, path: str, mode: str, key: str, purpose: str,
          expires: int | None = None) -> str:
    if purpose not in {'download', 'callback'}:
        raise OnlyOfficeDenied('BAD_GRANT_PURPOSE')
    exp = int(time.time()) + GRANT_SECONDS if expires is None else int(expires)
    fields = {'v': 1, 'p': path, 'm': mode, 'k': key, 'a': purpose, 'e': exp}
    raw = _b64(json.dumps(fields, sort_keys=True, separators=(',', ':')).encode())
    tag = hmac.new(_key(secret, b'capability'), raw.encode(), hashlib.sha256).hexdigest()
    return raw + '.' + tag


def check_grant(secret: bytes, token: str, *, path: str, mode: str, purpose: str,
                current_key: str, now: int | None = None) -> bool:
    if not isinstance(token, str) or len(token) > 1024 or token.count('.') != 1:
        return False
    raw, mac = token.split('.')
    expected = hmac.new(_key(secret, b'capability'), raw.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, mac):
        return False
    try:
        fields = json.loads(_unb64(raw))
    except (ValueError, UnicodeError):
        return False
    when = int(time.time() if now is None else now)
    return (isinstance(fields, dict) and fields.get('v') == 1
            and fields.get('p') == path and fields.get('m') == mode
            and fields.get('a') == purpose and fields.get('k') == current_key
            and type(fields.get('e')) is int and when <= fields['e'] <= when + GRANT_SECONDS + 60)


def verified_key(handler, path: Path, token: str, purpose: str) -> str:
    mode = installation_mode()
    journal = journal_for(handler)
    status = journal.current(path, mode=mode)
    if status is None or status['final_closed']:
        raise OnlyOfficeDenied('NO_ACTIVE_DOCUMENT_GRANT')
    key = status['key']
    if not check_grant(shared_secret(handler.server.config), token,
                       path=handler._onlyoffice_relative_path(path), mode=mode,
                       purpose=purpose, current_key=key):
        raise OnlyOfficeDenied('INVALID_DOCUMENT_CAPABILITY')
    return key


def _decode_jwt(token: str, secret: bytes) -> dict:
    if not isinstance(token, str) or len(token) > 16384 or token.count('.') != 2:
        raise OnlyOfficeDenied('MISSING_ONLYOFFICE_CALLBACK_JWT')
    a, b, tag = token.split('.')
    try:
        head = json.loads(_unb64(a, max_chars=16384))
        payload = json.loads(_unb64(b, max_chars=16384))
        received = _unb64(tag)
    except (ValueError, UnicodeError):
        raise OnlyOfficeDenied('MALFORMED_CALLBACK_JWT') from None
    if not isinstance(head, dict) or head.get('alg') != 'HS256' or head.get('typ', 'JWT') != 'JWT':
        raise OnlyOfficeDenied('BAD_CALLBACK_JWT_ALGORITHM')
    expected = hmac.new(secret, (a + '.' + b).encode('ascii'), hashlib.sha256).digest()
    if not hmac.compare_digest(received, expected):
        raise OnlyOfficeDenied('BAD_CALLBACK_JWT_SIGNATURE')
    if not isinstance(payload, dict):
        raise OnlyOfficeDenied('BAD_CALLBACK_JWT_PAYLOAD')
    now = int(time.time())
    if ('exp' in payload and (type(payload['exp']) is not int or payload['exp'] < now)):
        raise OnlyOfficeDenied('EXPIRED_CALLBACK_JWT')
    if ('nbf' in payload and (type(payload['nbf']) is not int or payload['nbf'] > now + 30)):
        raise OnlyOfficeDenied('FUTURE_CALLBACK_JWT')
    return payload


def verify_signed_callback(headers, body: dict, secret: bytes, *, expected_key: str) -> dict:
    if not isinstance(body, dict) or not isinstance(headers, object):
        raise OnlyOfficeDenied('INVALID_CALLBACK_BODY')
    auth = str(headers.get('Authorization', '') or '')
    jwt = auth[7:] if auth.startswith('Bearer ') else str(body.get('token', '') or '')
    data = _decode_jwt(jwt, secret)
    if 'operation' in data and data['operation'] != 'callback':
        raise OnlyOfficeDenied('WRONG_JWT_OPERATION')
    signed = data.get('payload', data)
    if not isinstance(signed, dict):
        raise OnlyOfficeDenied('BAD_SIGNED_CALLBACK')
    # Reject injected/modified fields even when a valid JWT exists.
    unsigned = {k: v for k, v in body.items() if k != 'token'}
    if any(k not in signed or signed[k] != v for k, v in unsigned.items()):
        raise OnlyOfficeDenied('CALLBACK_BODY_DIFFERENT_FROM_SIGNED_PAYLOAD')
    if signed.get('key') != expected_key or signed.get('status') != body.get('status'):
        raise OnlyOfficeDenied('CALLBACK_DOCUMENT_KEY_MISMATCH')
    return signed


def fetch_edited_document(handler, edited_url: str) -> bytes:
    """No redirects, no proxy, no DNS lookup: connect only to pinned loopback.

    Reject all callback-provided network destinations outside the configured
    local Document Server, including private networks and cloud metadata hosts.
    The allowed cache path is still bound to the signed JWT and document grant.
    """
    base = urlsplit(handler._onlyoffice_internal_url())
    target = urlsplit(edited_url)
    if (base.scheme not in {'http', 'https'} or base.hostname not in {'127.0.0.1', 'localhost', '::1'}
            or target.scheme != base.scheme or target.hostname not in {base.hostname, '127.0.0.1', 'localhost'}
            or target.username or target.password or target.fragment or not target.path.startswith('/cache/')
            or target.port not in {None, base.port}):
        raise OnlyOfficeDenied('CALLBACK_URL_OUTSIDE_PINNED_DOCUMENT_SERVER')
    port = base.port or (443 if base.scheme == 'https' else 80)
    if target.port is not None and target.port != port:
        raise OnlyOfficeDenied('CALLBACK_URL_WRONG_PORT')
    connection = (http.client.HTTPSConnection if base.scheme == 'https' else http.client.HTTPConnection)(
        '127.0.0.1', port, timeout=15)
    try:
        path = target.path + ('?' + target.query if target.query else '')
        connection.request('GET', path, headers={'Host': base.netloc, 'Accept': 'application/octet-stream'})
        response = connection.getresponse()
        if response.status != 200:
            raise OnlyOfficeDenied('DOCUMENT_SERVER_DOWNLOAD_HTTP_' + str(response.status))
        if int(response.getheader('Content-Length') or 0) > MAX_XLSX:
            raise OnlyOfficeDenied('OVERSIZED_DOCUMENT_SERVER_SAVE')
        data = response.read(MAX_XLSX + 1)
        if len(data) > MAX_XLSX:
            raise OnlyOfficeDenied('OVERSIZED_DOCUMENT_SERVER_SAVE')
        return data
    finally:
        connection.close()
