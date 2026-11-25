"""Worker-side signed enrollment. LMS signs; ORG alone admits and authenticates.

Only existing pinned SLH transports are used. Enrollment references are local
bookmarks, not an authority; each status is reobserved from the pinned peer.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import re
import secrets
import sqlite3
import ssl
from pathlib import Path
from urllib.parse import urlsplit

from main_computer.mcel_slh_first_run import SlhFirstRunController, FirstRunError
from main_computer.mcel_slh_network_client import (
    NetworkConnectionError, NetworkSlhClient, _connection, _json_request,
    _read_trust, _target, _endpoint,
)
from main_computer.mcel_seh_remote_client import RemoteSEHClient, _seh_endpoint
from main_computer.slh.organization_state import read_config
from main_computer.slh.storage import slh_state_dir, slh_app_user_dir

ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z')
RID = re.compile(r'[0-9a-f]{32}\Z')
PIN = re.compile(r'[0-9a-f]{64}\Z')


def refuse(code, status=400):
    raise NetworkConnectionError(code, status)


def target_connection(transport, endpoint, *, state_dir=None):
    """Return one fixed, previously trusted organization API transport."""
    factory = None
    if transport == 'local':
        if endpoint != '':
            refuse('LOCAL_ENDPOINT_MUST_BE_EMPTY')
        state = Path(state_dir) if state_dir is not None else slh_state_dir()
        cfg = read_config(state)
        if not cfg['enabled'] or not cfg['configured']:
            refuse('ORGANIZATION_NOT_RUNNING', 503)
        cert = state / 'managed-tls-cert.pem'
        if cert.is_symlink() or not cert.is_file():
            refuse('ORG_TLS_PIN_UNAVAILABLE', 503)
        pin = hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text(encoding='ascii'))).hexdigest()
        host = '127.0.0.1' if cfg['host'] == '0.0.0.0' else cfg['host']
        port = cfg['api_port']
    elif transport == 'slh':
        u = urlsplit(endpoint)
        if u.scheme != 'https' or not u.hostname or u.port is None or u.path not in ('','/') or u.query or u.fragment or u.username or u.password:
            refuse('SLH_API_ENDPOINT_REQUIRED')
        host, port = _target(u.hostname, u.port)
        pin = _read_trust(NetworkSlhClient().trust_path).get(_endpoint(host, port))
    elif transport == 'seh':
        endpoint = _seh_endpoint(endpoint)
        client = RemoteSEHClient()
        pin = _read_trust(client.trust_path).get('seh:' + endpoint)
        host, port = '127.0.0.1', 443
        factory = client._transport_factory({'kind':'seh','seh_url':endpoint,'allow_lab':False})
    else:
        refuse('INVALID_ENROLLMENT_TRANSPORT')
    if not isinstance(pin, str) or not PIN.fullmatch(pin):
        refuse('SLH_PAIRING_REQUIRED', 409)

    def call(method, path, body=None, token=''):
        conn, _ = _connection(host, port, pin, connection_factory=factory)
        try:
            return _json_request(conn, method, path, body, token)
        finally:
            conn.close()
    return call


class WorkerEnrollment:
    def __init__(self, *, lms=None, connection=None, journal=None):
        self.lms = lms or SlhFirstRunController()
        self.connection = connection or target_connection
        self.journal = Path(journal) if journal is not None else slh_app_user_dir() / 'worker-enrollment-references.sqlite3'

    def execute(self, action, args):
        if action not in ('request', 'status') or not isinstance(args, dict) or set(args) != {'deviceId','transport','endpoint'}:
            refuse('INVALID_ENROLLMENT_REQUEST')
        device, transport, endpoint = (args[k] for k in ('deviceId','transport','endpoint'))
        if (not isinstance(device, str) or not ID.fullmatch(device) or transport not in ('local','slh','seh')
                or not isinstance(endpoint, str) or len(endpoint)>512):
            refuse('INVALID_ENROLLMENT_ARGUMENTS')
        identity = self.lms._identity(device)
        call = self.connection(transport, endpoint)
        org = call('GET','/v1/org/identity')
        fingerprint = org.get('service_fingerprint')
        if org.get('schema') != 'main-computer.slh-org-identity.v1' or not isinstance(fingerprint,str) or not PIN.fullmatch(fingerprint):
            refuse('SLH_SERVICE_IDENTITY_INVALID',502)
        audience = 'slh://' + fingerprint
        def result(status, request_id='', role=''):
            return {'deviceId':device,'transport':transport,'endpoint':endpoint,'status':status,
                    'requestId':request_id,'role':role}
        # Never reuse a browser-supplied bearer or infer access from a bookmark.
        try:
            challenge = call('POST','/v1/auth/challenge',{'device_id':device})
        except NetworkConnectionError as exc:
            if exc.code != 'UNKNOWN_OR_REVOKED_DEVICE':
                raise
        else:
            if challenge.get('device_id') != device or challenge.get('audience') != audience or challenge.get('purpose') != 'slh.known_device.login':
                refuse('SLH_CHALLENGE_IDENTITY_MISMATCH',502)
            session = call('POST','/v1/auth/verify',{'device_id':device,'challenge_id':challenge['challenge_id'],
                              'signature':self.lms._sign(device,challenge)})
            token = session.get('access_token')
            if not isinstance(token,str) or not token:
                refuse('SLH_SESSION_INVALID',502)
            try:
                observed = call('GET','/v1/auth/session',token=token)
                role = observed.get('role')
                if observed.get('active') is not True or observed.get('device_id') != device or role not in ('worker','requester','admin') or session.get('role') != role:
                    refuse('SLH_SESSION_INVALID',502)
                return result('AUTHENTICATED' if role=='worker' else 'AUTHORIZED_OTHER_ROLE',role=role)
            finally:
                call('POST','/v1/auth/logout',{},token)
        self.journal.parent.mkdir(parents=True,exist_ok=True)
        if self.journal.is_symlink():
            refuse('ENROLLMENT_JOURNAL_UNSAFE',503)
        with closing(sqlite3.connect(self.journal,timeout=15)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS refs (audience TEXT, device TEXT, request TEXT, PRIMARY KEY(audience,device))')
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT request FROM refs WHERE audience=? AND device=?',(audience,device)).fetchone()
            if row:
                rid = row[0]
                if not RID.fullmatch(rid):
                    refuse('ENROLLMENT_REFERENCE_INVALID',503)
                try:
                    receipt = call('GET',f'/v1/enrollment/requests/{rid}/status')
                except NetworkConnectionError as exc:
                    if exc.code!='ENROLLMENT_NOT_FOUND':raise
                    # Administrator permanently removed the previous record.
                    # Never treat a missing record as permission to connect.
                    db.execute('DELETE FROM refs WHERE audience=? AND device=?',(audience,device))
                else:
                    if receipt.get('request_id') != rid or receipt.get('status') not in ('pending','approved','denied'):
                        refuse('ENROLLMENT_STATUS_INVALID',502)
                    return result({'pending':'PENDING_ADMIN','approved':'ACCESS_DENIED','denied':'DENIED'}[receipt['status']],rid)
            if action == 'status':
                return result('ACCESS_NOT_VERIFIED')
            admins = call('GET','/v1/enrollment/admins').get('admins')
            if not isinstance(admins,list) or not all(isinstance(a,dict) and isinstance(a.get('id'),str) for a in admins):
                refuse('SLH_ADMIN_DIRECTORY_INVALID',502)
            ids = {a['id'] for a in admins}
            if len(ids)!=1:
                refuse('SINGLE_ORGANIZATION_ADMIN_REQUIRED',409)
            administrator = next(iter(ids))
            fields = {'selected_admin':administrator,'device_id':device,'public_key':identity['public_key'],
                      'kind':'worker','requested_role':'worker','device_name':str(identity.get('label') or device)[:120],
                      'claimed_user':str(identity.get('label') or device)[:120],'client_nonce':secrets.token_urlsafe(32)}
            proof = self.lms._sign(device,{**fields,'version':1,'action':'slh.device.enrollment.request','audience':audience})
            receipt = call('POST','/v1/enrollment/requests',{**fields,'proof':proof})
            rid = receipt.get('request_id')
            if not isinstance(rid,str) or not RID.fullmatch(rid) or receipt.get('status')!='PENDING_ADMIN' or receipt.get('selected_admin')!=administrator:
                refuse('ENROLLMENT_RECEIPT_INVALID',502)
            db.execute('INSERT INTO refs VALUES (?,?,?)',(audience,device,rid))
            return result('PENDING_ADMIN',rid)


def handle_worker_enrollment(handler):
    """Same internal loopback/same-origin contract as SLH notebook RPC.

    The HTTPS gateway authenticates and rewrites its internal request before
    dispatch. No new gateway exemption or caller-supplied signing payload.
    """
    import ipaddress
    import json
    from http import HTTPStatus
    host = handler.headers.get('Host','').lower()
    origin = handler.headers.get('Origin','').lower()
    port = int(handler.server.server_address[1])
    try:
        local = ipaddress.ip_address(handler.client_address[0]).is_loopback
    except (ValueError,IndexError,TypeError):
        local = False
    if (not local or host not in (f'127.0.0.1:{port}',f'localhost:{port}') or origin!='http://'+host
            or handler.headers.get('Sec-Fetch-Site','same-origin')!='same-origin'
            or handler.headers.get('Content-Type','').split(';')[0].strip()!='application/json'):
        handler._send_json({'ok':False,'error':'LOCAL_ORIGIN_REQUIRED'},HTTPStatus.FORBIDDEN)
        return
    try:
        length = int(handler.headers.get('Content-Length','-1'))
        if not 2<=length<=4096:
            refuse('INVALID_ENROLLMENT_REQUEST')
        data = json.loads(handler.rfile.read(length))
        if not isinstance(data,dict) or set(data)!={'action','arguments'}:
            refuse('INVALID_ENROLLMENT_REQUEST')
        result = WorkerEnrollment().execute(data['action'],data['arguments'])
    except (NetworkConnectionError, FirstRunError) as exc:
        handler._send_json({'ok':False,'error':exc.code},exc.status)
        return
    except (ValueError,TypeError,KeyError):
        handler._send_json({'ok':False,'error':'INVALID_ENROLLMENT_REQUEST'},HTTPStatus.BAD_REQUEST)
        return
    except (OSError,sqlite3.Error):
        handler._send_json({'ok':False,'error':'ENROLLMENT_TRANSPORT_OR_JOURNAL_UNAVAILABLE'},HTTPStatus.SERVICE_UNAVAILABLE)
        return
    handler._send_json({'ok':True,'enrollment':result})
