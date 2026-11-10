"""Explicit LMS-SLH -> ORG-SLH root-authorized identity enrollment.

LMS-SLH uses the existing local Authenticator API for public identity and
signing. The persistent ORG root is read only on this trusted host; it never
appears in a local HTTP response. The HTTPS peer is checked against the
installed ORG TLS certificate *before* transmitting that root bearer.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import re
import secrets
import ssl
import time

from .authority import SLHAuthority, SLHError
from .local_authenticator_api import LocalAuthenticatorAPI
from .organization_state import OrganizationConfigError, read_config

_ROOT_GRANT_PATH = '/v1/admin/root/identities'
_DEVICE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z')
_ERROR_CODE = re.compile(r'[A-Z][A-Z0-9_]{2,63}\Z')
_MAX_RESPONSE = 8192


class LocalRootAdministratorRegistration:
    """One narrowly scoped local operation; no identity creation or enrollment UI."""

    def __init__(self, authority: SLHAuthority, authenticator: LocalAuthenticatorAPI):
        self.authority = authority
        self.authenticator = authenticator

    def _identity(self, device_id: str) -> dict:
        status, identity = self.authenticator.call(
            'GET', f'/v1/local/authenticator/identities/{device_id}', loopback=True)
        if status != 200:
            error = identity.get('error', {})
            raise SLHError(error.get('code', 'LOCAL_IDENTITY_UNAVAILABLE'), status)
        fingerprint = identity.get('fingerprint')
        if (identity.get('device_id') != device_id or identity.get('usable') is not True or
                not isinstance(identity.get('public_key'), str) or
                not isinstance(fingerprint, str) or
                not re.fullmatch(r'[a-f0-9]{64}', fingerprint)):
            raise SLHError('LOCAL_IDENTITY_UNAVAILABLE', 409)
        return identity

    def _sign(self, device_id: str, statement: dict) -> str:
        status, result = self.authenticator.call(
            'POST', f'/v1/local/authenticator/identities/{device_id}/sign',
            {'challenge': statement}, loopback=True)
        if status != 200:
            error = result.get('error', {})
            raise SLHError(error.get('code', 'LOCAL_IDENTITY_SIGNING_FAILED'), status)
        if (result.get('device_id') != device_id or
                not isinstance(result.get('signature'), str) or
                not 1 <= len(result['signature']) <= 512):
            raise SLHError('LOCAL_IDENTITY_SIGNING_FAILED', 503)
        return result['signature']

    def register(self, body: dict) -> dict:
        if not isinstance(body, dict) or (set(body) not in (
                {'device_id'}, {'device_id', 'bootstrap_only'})):
            raise SLHError('INVALID_REQUEST', 400)
        bootstrap_only = body.get('bootstrap_only', False)
        if type(bootstrap_only) is not bool or ('bootstrap_only' in body and bootstrap_only is not True):
            raise SLHError('INVALID_REQUEST', 400)
        device_id = body['device_id']
        if not isinstance(device_id, str) or not _DEVICE_ID.fullmatch(device_id):
            raise SLHError('INVALID_IDENTITY_ID', 400)
        try:
            config = read_config(self.authority.directory)
        except OrganizationConfigError as exc:
            raise SLHError('ORGANIZATION_CONFIG_INVALID', 503) from exc
        if not config['enabled'] or not config['configured']:
            raise SLHError('ORGANIZATION_NOT_RUNNING', 503)
        if bootstrap_only:
            # LMS-SLH consults the shared, authoritative local ORG database,
            # without a cross-service network status query. ORG checks again
            # atomically when the actual authorization grant is committed.
            state = self.authority.administrator_enrollment_status()['state']
            if state not in ('MISSING', 'NO_ACTIVE_DEVICE'):
                raise SLHError('ADMINISTRATOR_ALREADY_ENROLLED' if state == 'ENROLLED'
                               else 'ADMINISTRATOR_STATE_UNAVAILABLE', 409)
        identity = self._identity(device_id)
        statement = {
            'version': 1, 'action': 'slh.admin.root.register',
            'audience': 'slh://' + self.authority.service_fingerprint(),
            'device_id': device_id, 'public_key': identity['public_key'],
            'client_nonce': secrets.token_urlsafe(32), 'issued_at': int(time.time()),
        }
        if bootstrap_only:
            statement['bootstrap_only'] = True
        signature = self._sign(device_id, statement)
        # Reuse V6-A's existing root validation and disk location. The local
        # service must never create or repair the ORG-owned root file.
        root = self.authority._read_local_auth_key()
        response = self._send(config, root, {**{
            k: statement[k] for k in ('device_id', 'public_key', 'client_nonce', 'issued_at')},
            'proof': signature,
            **({'bootstrap_only': True} if bootstrap_only else {}),
        })
        if (response.get('status') not in ('REGISTERED', 'ALREADY_REGISTERED') or
                response.get('device_id') != device_id or
                response.get('fingerprint') != identity['fingerprint'] or
                response.get('role') != 'admin'):
            raise SLHError('ORG_GRANT_RESPONSE_INVALID', 502)
        return {k: response[k] for k in ('status', 'device_id', 'fingerprint', 'role')}

    def _send(self, config: dict, root: str, request: dict) -> dict:
        cert_path = self.authority.directory / 'managed-tls-cert.pem'
        if cert_path.is_symlink() or not cert_path.is_file():
            raise SLHError('ORG_TLS_PIN_UNAVAILABLE', 503)
        try:
            expected = hashlib.sha256(ssl.PEM_cert_to_DER_cert(
                cert_path.read_text(encoding='ascii'))).digest()
        except (OSError, ValueError, ssl.SSLError) as exc:
            raise SLHError('ORG_TLS_PIN_UNAVAILABLE', 503) from exc
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        hostname = '127.0.0.1' if config['host'] == '0.0.0.0' else config['host']
        conn = http.client.HTTPSConnection(hostname, config['api_port'], timeout=3, context=ctx)
        try:
            conn.connect()
            # Never send LOCAL-AUTH-KEY until exact same-connection TLS pin is verified.
            actual = hashlib.sha256(conn.sock.getpeercert(binary_form=True)).digest()
            if not secrets.compare_digest(actual, expected):
                raise SLHError('ORG_TLS_PIN_MISMATCH', 503)
            raw = json.dumps(request, separators=(',', ':')).encode('utf-8')
            conn.request('POST', _ROOT_GRANT_PATH, raw, headers={
                'Accept': 'application/json', 'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + root, 'Connection': 'close',
            })
            reply = conn.getresponse()
            if not reply.getheader('Content-Type', '').startswith('application/json'):
                raise SLHError('ORG_GRANT_RESPONSE_INVALID', 502)
            raw_response = reply.read(_MAX_RESPONSE + 1)
            if len(raw_response) > _MAX_RESPONSE:
                raise SLHError('ORG_GRANT_RESPONSE_INVALID', 502)
            result = json.loads(raw_response)
            if not isinstance(result, dict):
                raise SLHError('ORG_GRANT_RESPONSE_INVALID', 502)
            if reply.status not in (200, 201):
                error = result.get('error') if isinstance(result.get('error'), dict) else {}
                code = error.get('code', 'ORG_GRANT_REFUSED')
                if not isinstance(code, str) or not _ERROR_CODE.fullmatch(code):
                    code = 'ORG_GRANT_REFUSED'
                raise SLHError(code, reply.status if reply.status in (400, 401, 403, 404, 409) else 503)
            return result
        except SLHError:
            raise
        except (OSError, ssl.SSLError, http.client.HTTPException, ValueError,
                TypeError, UnicodeError) as exc:
            raise SLHError('ORG_TRANSPORT_UNAVAILABLE', 503) from exc
        finally:
            conn.close()
