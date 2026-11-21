"""MCEL SLH local software authenticator: identity-bound and never exposes a seed.

Only the current 30-second OTP is eligible for display, and only after a
fresh authenticated administrator session for the selected P-256 identity.
The ORG-SLH endpoint remains the configured, certificate-pinned local service.
"""
from __future__ import annotations

import re

from main_computer.mcel_slh_worker_bridge import (
    OrganizationWorkerClient, WorkerBridgeError, validate_admin_bearer,
)
from main_computer.slh.authenticator_store import LocalIdentityStore, IdentityStoreError
from main_computer.slh.storage import slh_state_dir

_DEVICE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z')
_FINGERPRINT = re.compile(r'[a-f0-9]{64}\Z')

class SoftwareAuthenticatorError(Exception):
    def __init__(self, code, status=403):
        super().__init__(code)
        self.code = code
        self.status = status


def execute(operation, device_id, token, *, client=None, store=None):
    if operation not in ('status', 'pair', 'current', 'revoke') or not isinstance(device_id, str) or not _DEVICE.fullmatch(device_id):
        raise SoftwareAuthenticatorError('INVALID_AUTHENTICATOR_REQUEST',400)
    validate_admin_bearer(token)
    client = client or OrganizationWorkerClient()
    # LMS-SLH owns the machine-local identity directory. The interactive
    # user's generic identity directory is a DIFFERENT authority and caused
    # real IDENTITY_NOT_FOUND failures for identities observed from LMS.
    store = store or LocalIdentityStore(slh_state_dir() / 'identities')
    try:
        session = client.request('GET','/v1/auth/session',token)
        if (session.get('active') is not True or session.get('role')!='admin' or
                session.get('device_id')!=device_id):
            raise SoftwareAuthenticatorError('ADMIN_IDENTITY_MISMATCH',403)
        identity=store.get(device_id)
        if identity.get('usable') is not True:
            raise SoftwareAuthenticatorError('LOCAL_IDENTITY_UNAVAILABLE',403)
        peer=client.request('GET','/v1/org/identity',token)
        fingerprint=peer.get('service_fingerprint')
        if not isinstance(fingerprint,str) or not _FINGERPRINT.fullmatch(fingerprint):
            raise SoftwareAuthenticatorError('SLH_SERVICE_IDENTITY_UNVERIFIED',503)
        state=client.request('GET','/v1/admin/authenticator/software-otp',token)
        active=state.get('active') is True
        if active and state.get('device_id')!=device_id:
            raise SoftwareAuthenticatorError('AUTHENTICATOR_DEVICE_MISMATCH',403)
        try:
            current=store.current_software_otp(device_id,fingerprint)
            locally_paired=True
        except IdentityStoreError as exc:
            if exc.code!='SOFTWARE_OTP_NOT_ACTIVATED':
                raise
            locally_paired=False
            current=None
        if operation=='status':
            return {'active':active, 'localFactor':locally_paired,
                    'status':('PAIRED' if active and locally_paired else
                              'LOCAL_PAIRING_REQUIRED' if active else
                              'REGISTRATION_REQUIRED' if locally_paired else 'NOT_PAIRED'),
                    'deviceId':device_id, 'periodSeconds':30,
                    'message':'One-time authenticator for the selected local administrator and verified SLH'}
        if operation=='pair':
            if active:
                raise SoftwareAuthenticatorError('SOFTWARE_OTP_ALREADY_REGISTERED',409)
            if not locally_paired:
                store.activate_software_otp(device_id,fingerprint)
                current=store.current_software_otp(device_id,fingerprint)
            # Secret remains entirely on server; no MCEL state, browser receipt,
            # response, or activity log can contain it.
            secret=store.software_otp_pairing_secret(device_id,fingerprint)
            result=client.request('POST','/v1/admin/authenticator/software-otp',token,
                                  {'secret':secret,'code':current['code'],
                                   'service_fingerprint':fingerprint})
            if result.get('status')!='REGISTERED':
                raise SoftwareAuthenticatorError('SOFTWARE_OTP_REGISTRATION_UNVERIFIED',502)
            return {'outcome':'PAIRED','message':'Paired. The first time step was consumed; use the next rotating code.',
                    'deviceId':device_id}
        if operation=='revoke':
            if not active:
                raise SoftwareAuthenticatorError('SOFTWARE_OTP_NOT_REGISTERED',404)
            result=client.request('POST','/v1/admin/authenticator/software-otp/revoke',token,{})
            if result.get('status')!='REVOKED':
                raise SoftwareAuthenticatorError('SOFTWARE_OTP_REVOCATION_UNVERIFIED',502)
            if locally_paired:
                store.reset_software_otp(device_id,fingerprint)
            return {'outcome':'REVOKED','message':'The authenticator was revoked on this SLH.',
                    'deviceId':device_id}
        # Opening an identity's More details is the explicit administrator
        # action. Initialize its existing software factor once, if needed,
        # rather than expose Pair/Revoke as everyday UI operations.
        if not active:
            if not locally_paired:
                store.activate_software_otp(device_id,fingerprint)
                current=store.current_software_otp(device_id,fingerprint)
                locally_paired=True
            secret=store.software_otp_pairing_secret(device_id,fingerprint)
            result=client.request('POST','/v1/admin/authenticator/software-otp',token,
                                  {'secret':secret,'code':current['code'],
                                   'service_fingerprint':fingerprint})
            if result.get('status')!='REGISTERED':
                raise SoftwareAuthenticatorError('SOFTWARE_OTP_REGISTRATION_UNVERIFIED',502)
            # Registration consumes its proof step. It is not an enrollment
            # passkey, even though the same algorithm produced it.
            return {'code':None,'expiresAt':current['expires_at'],
                    'periodSeconds':30,'deviceId':device_id,'usable':False}
        if not locally_paired:
            raise SoftwareAuthenticatorError('LOCAL_SOFTWARE_OTP_FACTOR_MISSING',409)
        if state.get('locked') is True:
            raise SoftwareAuthenticatorError('SOFTWARE_OTP_RATE_LIMITED',429)
        if state.get('code_available') is not True:
            return {'code':None,'expiresAt':current['expires_at'],
                    'periodSeconds':30,'deviceId':device_id,'usable':False}
        # Code is the sole short-lived secret crossing the loopback response;
        # never return it in declarative MCEL state or operation history.
        return {'code':current['code'],'expiresAt':current['expires_at'],
                'periodSeconds':30,'deviceId':device_id,'usable':True}

    except WorkerBridgeError as exc:
        raise SoftwareAuthenticatorError(exc.code,exc.status) from exc
    except IdentityStoreError as exc:
        raise SoftwareAuthenticatorError(exc.code,exc.status) from exc
