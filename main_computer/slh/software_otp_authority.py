"""One-time software-authenticator factor for the existing SLH identity authority.

Not a new identity and not a replacement for P-256; only authenticated admins
can pair a factor. OTP approval is bound to the selected administrator in a
pending request and consumes a time step in the same database transaction.
"""
from __future__ import annotations
import base64
import os
import secrets
import time
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .software_otp import secret_bytes, valid_counter
from .storage import secure_private_state_file


class OTPRefused(Exception):
    def __init__(self, code, status=403):
        super().__init__(code)
        self.code, self.status = code, status


def _seal_key(authority, create=False):
    path = authority.directory / 'software-otp-sealing.key'
    if path.is_symlink():
        raise OTPRefused('SOFTWARE_OTP_SEAL_UNAVAILABLE',503)
    if create:
        try:
            fd=os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        except OSError:
            raise OTPRefused('SOFTWARE_OTP_SEAL_UNAVAILABLE',503) from None
        else:
            try:
                with os.fdopen(fd,'wb') as out:
                    out.write(secrets.token_bytes(32))
                    out.flush()
                    os.fsync(out.fileno())
                secure_private_state_file(path)
            except BaseException:
                raise OTPRefused('SOFTWARE_OTP_SEAL_UNAVAILABLE',503) from None
    try:
        key=path.read_bytes()
    except OSError:
        raise OTPRefused('SOFTWARE_OTP_SEAL_UNAVAILABLE',503) from None
    if len(key)!=32:
        raise OTPRefused('SOFTWARE_OTP_SEAL_UNAVAILABLE',503)
    return key


def _aad(admin_id,device_id):
    return ('slh.software-otp.v1:'+admin_id+':'+device_id).encode('utf8')


def _seal(authority,secret,admin_id,device_id):
    nonce=secrets.token_bytes(12)
    encrypted=AESGCM(_seal_key(authority,create=True)).encrypt(
        nonce,secret.encode('ascii'),_aad(admin_id,device_id))
    return base64.urlsafe_b64encode(nonce+encrypted).decode('ascii')


def _open(authority,sealed,admin_id,device_id):
    try:
        raw=base64.urlsafe_b64decode(sealed.encode('ascii'))
        secret=AESGCM(_seal_key(authority)).decrypt(
            raw[:12],raw[12:],_aad(admin_id,device_id)).decode('ascii')
        secret_bytes(secret)
        return secret
    except Exception as exc:
        if isinstance(exc,OTPRefused):
            raise
        raise OTPRefused('SOFTWARE_OTP_SEAL_UNAVAILABLE',503) from exc


def status(authority,admin_id):
    with authority._db() as c:
        row=c.execute('SELECT device_id,locked_until,last_accepted_step FROM admin_software_otp WHERE admin_id=?',
                      (admin_id,)).fetchone()
    return {'active':row is not None, 'device_id':row['device_id'] if row else None,
            'locked':bool(row and row['locked_until']>time.time()),
            'code_available':bool(row and int(time.time())//30>row['last_accepted_step']),
            'algorithm':'TOTP-SHA256','period_seconds':30,'digits':8}


def provision(authority,admin_id,device_id,secret,code):
    from .authority import SLHError
    try:
        secret_bytes(secret)
        step=valid_counter(secret,code)
    except ValueError:
        raise SLHError('INVALID_SOFTWARE_OTP_SECRET',400) from None
    if step is None:
        raise SLHError('INVALID_SOFTWARE_OTP_PROOF',403)
    with authority._lock,authority._db() as c:
        c.execute('BEGIN IMMEDIATE')
        device=c.execute('''SELECT d.id FROM devices d JOIN users u ON u.id=d.user_id
            WHERE d.id=? AND u.id=? AND d.status='active' AND
            u.status='active' AND u.role='admin' ''',(device_id,admin_id)).fetchone()
        if not device:
            raise SLHError('ADMIN_REQUIRED',403)
        if c.execute('SELECT 1 FROM admin_software_otp WHERE admin_id=?',(admin_id,)).fetchone():
            raise SLHError('SOFTWARE_OTP_ALREADY_REGISTERED',409)
        sealed=_seal(authority,secret,admin_id,device_id)
        c.execute('''INSERT INTO admin_software_otp
            (admin_id,device_id,sealed_secret,last_accepted_step) VALUES (?,?,?,?)''',
            (admin_id,device_id,sealed,step))
        authority._audit(c,admin_id,'auth.software_otp.registered',device_id)
    return {'status':'REGISTERED','admin_identity':admin_id,'device_id':device_id,
            'algorithm':'TOTP-SHA256','period_seconds':30,'digits':8}


def revoke(authority,admin_id):
    from .authority import SLHError
    with authority._lock,authority._db() as c:
        c.execute('BEGIN IMMEDIATE')
        if not c.execute('DELETE FROM admin_software_otp WHERE admin_id=?',(admin_id,)).rowcount:
            raise SLHError('SOFTWARE_OTP_NOT_REGISTERED',404)
        authority._audit(c,admin_id,'auth.software_otp.revoked',admin_id)
    return {'status':'REVOKED','admin_identity':admin_id}


def approve(authority,request_id,admin_id,code):
    from .authority import SLHError, _required
    import re
    _required(request_id,'request_id')
    _required(admin_id,'admin_identity')
    if not isinstance(code,str) or not re.fullmatch('[0-9]{8}',code):
        raise SLHError('INVALID_ONE_TIME_CODE',403)
    error=None
    result=None
    with authority._lock,authority._db() as c:
        c.execute('BEGIN IMMEDIATE')
        request=c.execute('SELECT * FROM enrollments WHERE id=?',(request_id,)).fetchone()
        if not request or request['status']!='pending':
            raise SLHError('NOT_PENDING',409)
        if request['selected_admin']!=admin_id:
            raise SLHError('ADMIN_IDENTITY_MISMATCH',403)
        if request['requested_role']=='admin':
            raise SLHError('ADMIN_ROLE_REQUIRES_SIGNED_ADMIN_SESSION',403)
        if authority._inspection_required(c) and not request['inspection_by']:
            raise SLHError('INSPECTION_REQUIRED',403)
        identity=c.execute('''SELECT o.*,u.status AS user_status,u.role AS user_role,
            d.status AS device_status FROM admin_software_otp o
            JOIN users u ON u.id=o.admin_id
            JOIN devices d ON d.id=o.device_id AND d.user_id=u.id
            WHERE o.admin_id=?''',(admin_id,)).fetchone()
        now=int(time.time())
        if not identity or identity['user_role']!='admin' or identity['user_status']!='active' or identity['device_status']!='active':
            raise SLHError('SOFTWARE_OTP_NOT_REGISTERED',403)
        if identity['locked_until']>now:
            raise SLHError('SOFTWARE_OTP_RATE_LIMITED',429)
        secret=_open(authority,identity['sealed_secret'],admin_id,identity['device_id'])
        step=valid_counter(secret,code,now=now)
        if step is None or step<=identity['last_accepted_step']:
            attempts=identity['failed_attempts']+1
            lock_until=now+300 if attempts>=5 else 0
            c.execute('''UPDATE admin_software_otp SET failed_attempts=?,locked_until=?
                WHERE admin_id=?''',(0 if lock_until else attempts,lock_until,admin_id))
            authority._audit(c,admin_id,'enrollment.software_otp.denied',request_id)
            error=('SOFTWARE_OTP_RATE_LIMITED' if lock_until else 'INVALID_ONE_TIME_CODE')
        else:
            c.execute('''UPDATE admin_software_otp SET last_accepted_step=?,
                failed_attempts=0,locked_until=0 WHERE admin_id=?''',(step,admin_id))
            result=authority._activate_enrollment(c,request,admin_id,'software_otp')
    if error:
        raise SLHError(error,429 if error=='SOFTWARE_OTP_RATE_LIMITED' else 403)
    return result
