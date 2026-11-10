"""Reusable per-machine localhost Authenticator API; shared by SLH and viewport.

No organization, administrator session, or SEH dependency. Deliberately no UI.
"""
from __future__ import annotations

import re
from pathlib import Path

from .authenticator_store import LocalIdentityStore, IdentityStoreError

_ROUTE = re.compile(r'^/v1/local/authenticator/identities/([A-Za-z0-9][A-Za-z0-9_.-]{0,63})(/sign)?$')


class LocalAuthenticatorAPI:
    def __init__(self, identity_dir: Path | None = None):
        self.identity_dir = identity_dir

    def call(self, method: str, path: str, body: dict | None = None, *, loopback: bool = False):
        if not loopback:
            return 403, {'error': {'code': 'LOCAL_TRANSPORT_ONLY'}}
        if body is None:
            body = {}
        if not isinstance(body, dict):
            return 400, {'error': {'code': 'INVALID_REQUEST'}}
        try:
            store = LocalIdentityStore(self.identity_dir)
            if method == 'GET' and path == '/v1/local/authenticator/status':
                return 200, {
                    'schema': 'main-computer.local-authenticator.v1',
                    'available': True, 'credential_type': 'p256_device',
                    'storage': 'user_local', 'protection': 'none',
                    'supported_protection_modes': ['none'],
                    'password_protection_available': False,
                    'hardware_key_protection_available': False,
                }
            if method == 'GET' and path == '/v1/local/authenticator/identities':
                # Includes the installation's designated ordinary identity.
                return 200, store.list()
            if method == 'POST' and path == '/v1/local/authenticator/identities':
                if set(body) - {'device_id', 'label'}:
                    raise IdentityStoreError('INVALID_REQUEST')
                if 'device_id' not in body:
                    return 201, store.create_generated(label=body.get('label'))
                return 201, store.create(body['device_id'], label=body.get('label'))
            matched = _ROUTE.fullmatch(path)
            if matched:
                device_id, sign_suffix = matched.groups()
                if method == 'GET' and not sign_suffix:
                    return 200, store.get(device_id)
                if method == 'POST' and sign_suffix:
                    if set(body) != {'challenge'}:
                        raise IdentityStoreError('INVALID_REQUEST')
                    return 200, store.sign(device_id, body['challenge'])
            return 404, {'error': {'code': 'ENDPOINT_NOT_FOUND'}}
        except IdentityStoreError as exc:
            return exc.status, {'error': {'code': exc.code}}
        except (ValueError, TypeError, OverflowError):
            return 400, {'error': {'code': 'INVALID_REQUEST'}}
