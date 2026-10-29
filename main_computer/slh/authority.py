"""Durable SLH organization/device authority. No certificates or marketplace wallets.

A single SQLite database holds durable identities, pending enrollments, used codes,
policies and audit events. Sessions are deliberately process-local and expire on restart.
The API server is loopback-only until an independent encrypted transport is proven.
"""
from __future__ import annotations
import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
import uuid

from .authenticator import public_key_fingerprint, verify_object


class SLHError(Exception):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code, self.status = code, status


def _required(value, name, *, max_length=160):
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise SLHError('INVALID_' + name.upper())
    return value.strip()


def _hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _row(row):
    return dict(row) if row is not None else None


class SLHAuthority:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        if os.name == 'posix':
            os.chmod(self.directory, 0o700)
        self.db_path = self.directory / 'organization.sqlite3'
        self._lock = threading.RLock()
        self._sessions = {}  # restart intentionally invalidates all bearer sessions
        self._initialize()

    @contextmanager
    def _db(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('PRAGMA busy_timeout=10000')
            with conn:
                yield conn
        finally:
            conn.close()

    def _initialize(self):
        with self._lock, self._db() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS organization(org_id TEXT PRIMARY KEY, initialized_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL,
                status TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                public_key TEXT NOT NULL, fingerprint TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
                name TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
                created_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS enrollments(id TEXT PRIMARY KEY, device_id TEXT NOT NULL UNIQUE,
                public_key TEXT NOT NULL, fingerprint TEXT NOT NULL, device_name TEXT NOT NULL,
                claimed_user TEXT NOT NULL, kind TEXT NOT NULL, requested_role TEXT NOT NULL,
                selected_admin TEXT NOT NULL, status TEXT NOT NULL, created_at INTEGER NOT NULL,
                authorized_by TEXT, inspection_by TEXT);
            CREATE TABLE IF NOT EXISTS enrollment_keys(id TEXT PRIMARY KEY, secret_hash TEXT NOT NULL UNIQUE,
                enrollment_id TEXT NOT NULL REFERENCES enrollments(id), issuer TEXT NOT NULL,
                allowed_role TEXT NOT NULL, expires_at INTEGER NOT NULL, redeemed_at INTEGER);
            CREATE TABLE IF NOT EXISTS challenges(id TEXT PRIMARY KEY, device_id TEXT NOT NULL,
                payload TEXT NOT NULL, expires_at INTEGER NOT NULL, consumed_at INTEGER);
            CREATE TABLE IF NOT EXISTS policies(name TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY AUTOINCREMENT, at INTEGER NOT NULL,
                actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL, detail TEXT NOT NULL);
            ''')
            c.execute('INSERT OR IGNORE INTO policies(name,value) VALUES (?,?)', ('physical_inspection_required', 'false'))
        if os.name == 'posix':
            os.chmod(self.db_path, 0o600)
        # The local installer owns the bootstrap secret, not a network client.
        if not self.initialized() and not (self.directory / 'bootstrap.secret').exists():
            self._create_bootstrap_secret()

    def _create_bootstrap_secret(self):
        path = self.directory / 'bootstrap.secret'
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, 'w', encoding='ascii') as f:
            f.write(secrets.token_urlsafe(40) + '\n')

    def initialized(self):
        with self._db() as c:
            return c.execute('SELECT 1 FROM organization LIMIT 1').fetchone() is not None

    def status(self):
        return {'initialized': self.initialized(), 'bootstrap': 'CLOSED' if self.initialized() else 'LOCAL_ONLY',
                'organization_id': self.org_id()}

    def org_id(self):
        with self._db() as c:
            row = c.execute('SELECT org_id FROM organization LIMIT 1').fetchone()
            return row['org_id'] if row else None

    def _audit(self, c, actor, action, target, detail=''):
        c.execute('INSERT INTO audit(at,actor,action,target,detail) VALUES (?,?,?,?,?)',
                  (int(time.time()), actor, action, target, detail))

    def bootstrap(self, data, *, loopback: bool):
        if not loopback:
            raise SLHError('BOOTSTRAP_LOCAL_ONLY', 403)
        if self.initialized():
            raise SLHError('ALREADY_INITIALIZED', 409)
        secret = _required(data.get('bootstrap_secret'), 'bootstrap_secret', max_length=200)
        path = self.directory / 'bootstrap.secret'
        with self._lock, self._db() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute('SELECT 1 FROM organization LIMIT 1').fetchone():
                raise SLHError('ALREADY_INITIALIZED', 409)
            if not path.exists() or not secrets.compare_digest(path.read_text().strip(), secret):
                raise SLHError('BOOTSTRAP_DENIED', 403)
            org_id = _required(data.get('org_id'), 'org_id')
            device_id = _required(data.get('device_id'), 'device_id')
            name = _required(data.get('admin_name'), 'admin_name')
            user_id = _required(data.get('admin_id'), 'admin_id')
            public_key = data.get('public_key')
            if not isinstance(public_key, str) or not public_key.strip() or len(public_key)>2048:
                raise SLHError('INVALID_PUBLIC_KEY')
            proof = data.get('proof')
            statement = {'version': 1, 'action':'slh.admin.bootstrap', 'org_id':org_id,
                         'admin_id':user_id, 'device_id':device_id, 'public_key':public_key}
            if not verify_object(public_key, statement, proof):
                raise SLHError('INVALID_KEY_PROOF', 403)
            fingerprint = public_key_fingerprint(public_key)
            now = int(time.time())
            c.execute('INSERT INTO organization VALUES (?,?)', (org_id, now))
            c.execute('INSERT INTO users VALUES (?,?,?,?)', (user_id,name,'admin','active'))
            c.execute('INSERT INTO devices VALUES (?,?,?,?,?,?,?,?,?)',
                      (device_id,user_id,public_key,fingerprint,'administrator',name,'','active',now))
            self._audit(c, user_id, 'bootstrap.admin', device_id)
        path.unlink(missing_ok=True)
        return {'status':'INITIALIZED', 'org_id':org_id, 'admin_id':user_id, 'device_id':device_id}

    def admin_directory(self):
        with self._db() as c:
            rows = c.execute("SELECT id,name FROM users WHERE role='admin' AND status='active' ORDER BY name").fetchall()
            return [{'id': r['id'], 'name':r['name']} for r in rows]

    def enrollment_request(self, data):
        if not self.initialized():
            raise SLHError('NOT_INITIALIZED', 409)
        selected = _required(data.get('selected_admin'), 'selected_admin')
        dev = _required(data.get('device_id'), 'device_id')
        key = data.get('public_key')
        if not isinstance(key, str) or not key.strip() or len(key)>2048:
            raise SLHError('INVALID_PUBLIC_KEY')
        kind = _required(data.get('kind'), 'kind')
        if kind not in ('worker','requester','administrator','phone'):
            raise SLHError('INVALID_DEVICE_KIND')
        name = _required(data.get('device_name'), 'device_name')
        owner = _required(data.get('claimed_user'), 'claimed_user')
        requested_role = _required(data.get('requested_role'), 'requested_role')
        if requested_role not in ('worker','requester','admin'):
            raise SLHError('INVALID_ROLE')
        nonce = _required(data.get('client_nonce'), 'client_nonce', max_length=128)
        if len(nonce) < 24:
            raise SLHError('INVALID_CLIENT_NONCE')
        statement = {k:data[k] for k in ('selected_admin','device_id','public_key','kind','device_name','claimed_user','requested_role','client_nonce')}
        statement.update({'version':1, 'action':'slh.device.enrollment.request','org_id':self.org_id()})
        if not verify_object(key, statement, data.get('proof')):
            raise SLHError('INVALID_KEY_PROOF', 403)
        fingerprint = public_key_fingerprint(key)
        req_id = uuid.uuid4().hex
        with self._lock, self._db() as c:
            c.execute('BEGIN IMMEDIATE')
            if not c.execute("SELECT 1 FROM users WHERE id=? AND role='admin' AND status='active'", (selected,)).fetchone():
                raise SLHError('UNKNOWN_ADMIN', 400)
            if c.execute('SELECT 1 FROM devices WHERE id=? OR fingerprint=?', (dev,fingerprint)).fetchone():
                raise SLHError('DEVICE_ALREADY_REGISTERED', 409)
            if c.execute('SELECT 1 FROM enrollments WHERE device_id=?', (dev,)).fetchone():
                raise SLHError('ENROLLMENT_EXISTS', 409)
            c.execute('INSERT INTO enrollments(id,device_id,public_key,fingerprint,device_name,claimed_user,kind,requested_role,selected_admin,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                      (req_id,dev,key,fingerprint,name,owner,kind,requested_role,selected,'pending',int(time.time())))
            self._audit(c, 'unauthenticated', 'enrollment.request', req_id)
        return {'request_id':req_id,'status':'PENDING_ADMIN', 'selected_admin':selected}

    def challenge(self, device_id):
        device_id = _required(device_id,'device_id')
        with self._lock, self._db() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute("SELECT d.id,d.status,u.status AS user_status FROM devices d JOIN users u ON d.user_id=u.id WHERE d.id=?", (device_id,)).fetchone()
            if not row or row['status']!='active' or row['user_status']!='active':
                raise SLHError('UNKNOWN_OR_REVOKED_DEVICE', 403)
            now=int(time.time())
            payload={'version':1,'purpose':'slh.known_device.login','org_id':self.org_id(),
                     'audience':'slh://' + self.org_id(),'device_id':device_id,
                     'challenge_id':uuid.uuid4().hex,'nonce':secrets.token_urlsafe(32),
                     'issued_at':now,'expires_at':now+60}
            c.execute('INSERT INTO challenges(id,device_id,payload,expires_at) VALUES (?,?,?,?)',
                      (payload['challenge_id'], device_id, json.dumps(payload,sort_keys=True),now+60))
            return payload

    def verify_login(self, data):
        device_id = _required(data.get('device_id'),'device_id')
        cid = _required(data.get('challenge_id'),'challenge_id')
        with self._lock, self._db() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT payload,expires_at,consumed_at FROM challenges WHERE id=? AND device_id=?', (cid,device_id)).fetchone()
            if not row or row['consumed_at'] is not None or int(time.time())>=row['expires_at']:
                raise SLHError('INVALID_OR_EXPIRED_CHALLENGE', 403)
            c.execute('UPDATE challenges SET consumed_at=? WHERE id=? AND consumed_at IS NULL', (int(time.time()),cid))
            device=c.execute('SELECT d.public_key,d.user_id,d.status,u.role,u.status AS user_status FROM devices d JOIN users u ON d.user_id=u.id WHERE d.id=?', (device_id,)).fetchone()
            if not device or device['status']!='active' or device['user_status']!='active' or not verify_object(device['public_key'], json.loads(row['payload']),data.get('signature')):
                self._audit(c,device_id,'auth.denied',cid)
                result=None
            else:
                token=secrets.token_urlsafe(48)
                expires_at=int(time.time())+900
                self._sessions[_hash(token)]={'device_id':device_id,'user_id':device['user_id'], 'expires_at':expires_at}
                self._audit(c,device_id,'auth.success',cid)
                result={'access_token':token,'token_type':'Bearer','expires_at':expires_at,
                        'device_id':device_id,'role':device['role']}
        if result is None:
            raise SLHError('INVALID_AUTHENTICATOR', 403)
        return result

    def authenticate(self, token, *, admin=False):
        if not token:
            raise SLHError('AUTH_REQUIRED',401)
        with self._lock:
            session = self._sessions.get(_hash(token))
            if not session or session['expires_at']<=time.time():
                raise SLHError('SESSION_INVALID',401)
            with self._db() as c:
                row=c.execute('SELECT d.status, u.role,u.status AS user_status FROM devices d JOIN users u ON d.user_id=u.id WHERE d.id=? AND u.id=?',
                              (session['device_id'],session['user_id'])).fetchone()
            if not row or row['status']!='active' or row['user_status']!='active':
                self._sessions.pop(_hash(token), None)
                raise SLHError('SESSION_REVOKED',401)
            if admin and row['role']!='admin':
                raise SLHError('ADMIN_REQUIRED',403)
            return {**session,'role':row['role']}

    def logout(self, token):
        # Logging out only invalidates the current bearer. Do not re-read the
        # organizational SQLite database during websocket teardown.
        if not token:
            raise SLHError('AUTH_REQUIRED', 401)
        with self._lock:
            if self._sessions.pop(_hash(token), None) is None:
                raise SLHError('SESSION_INVALID', 401)
        return {'status':'LOGGED_OUT'}

    def _inspection_required(self, c):
        return c.execute("SELECT value FROM policies WHERE name='physical_inspection_required'").fetchone()['value']=='true'

    def queue(self):
        with self._db() as c:
            return [_row(x) for x in c.execute("SELECT id,device_id,fingerprint,device_name,claimed_user,kind,requested_role,selected_admin,status,created_at,inspection_by FROM enrollments ORDER BY created_at DESC").fetchall()]

    def mark_inspected(self, req_id, admin_id):
        with self._lock, self._db() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute("UPDATE enrollments SET inspection_by=? WHERE id=? AND status='pending'", (admin_id,req_id)).rowcount != 1:
                raise SLHError('NOT_PENDING',409)
            self._audit(c,admin_id,'enrollment.physical_inspection_attested',req_id)
            return {'status':'INSPECTION_ATTESTED','request_id':req_id}

    def issue_key(self, req_id, admin_id, *, ttl=300):
        if not isinstance(ttl,int) or not 1<=ttl<=600:
            raise SLHError('INVALID_TTL')
        secret = secrets.token_urlsafe(36)
        with self._lock, self._db() as c:
            c.execute('BEGIN IMMEDIATE')
            r=c.execute('SELECT * FROM enrollments WHERE id=?', (req_id,)).fetchone()
            if not r or r['status']!='pending':
                raise SLHError('NOT_PENDING',409)
            if self._inspection_required(c) and not r['inspection_by']:
                raise SLHError('INSPECTION_REQUIRED',403)
            c.execute('INSERT INTO enrollment_keys VALUES (?,?,?,?,?,?,NULL)',
                      (uuid.uuid4().hex,_hash(secret),req_id,admin_id,r['requested_role'],int(time.time())+ttl))
            self._audit(c,admin_id,'enrollment.key.issued',req_id)
        return {'enrollment_key':secret, 'expires_in_seconds':ttl,'request_id':req_id,'single_use':True}

    def approve(self, req_id, *, admin_id=None, enrollment_key=None):
        if bool(admin_id) == bool(enrollment_key):
            raise SLHError('APPROVAL_METHOD_REQUIRED')
        with self._lock, self._db() as c:
            c.execute('BEGIN IMMEDIATE')
            r=c.execute('SELECT * FROM enrollments WHERE id=?', (req_id,)).fetchone()
            if not r or r['status']!='pending':
                raise SLHError('NOT_PENDING',409)
            if self._inspection_required(c) and not r['inspection_by']:
                raise SLHError('INSPECTION_REQUIRED',403)
            if enrollment_key:
                grant=c.execute('SELECT * FROM enrollment_keys WHERE secret_hash=? AND enrollment_id=?',
                                (_hash(enrollment_key),req_id)).fetchone()
                if not grant or grant['redeemed_at'] is not None or grant['expires_at']<=int(time.time()) or grant['allowed_role']!=r['requested_role']:
                    raise SLHError('INVALID_ENROLLMENT_KEY',403)
                actor=grant['issuer']
                c.execute('UPDATE enrollment_keys SET redeemed_at=? WHERE id=? AND redeemed_at IS NULL',
                          (int(time.time()),grant['id']))
            else:
                actor=admin_id
            user_id='user-'+uuid.uuid4().hex
            now=int(time.time())
            c.execute('INSERT INTO users VALUES (?,?,?,?)', (user_id,r['claimed_user'],r['requested_role'],'active'))
            c.execute('INSERT INTO devices VALUES (?,?,?,?,?,?,?,?,?)',
                      (r['device_id'],user_id,r['public_key'],r['fingerprint'],r['kind'],r['device_name'],'','active',now))
            c.execute("UPDATE enrollments SET status='approved',authorized_by=? WHERE id=?", (actor,req_id))
            self._audit(c,actor,'enrollment.approved',r['device_id'])
            return {'status':'APPROVED','request_id':req_id,'device_id':r['device_id'], 'user_id':user_id, 'authorized_by':actor}

    def deny(self, req_id, actor):
        with self._lock,self._db() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute("UPDATE enrollments SET status='denied',authorized_by=? WHERE id=? AND status='pending'", (actor,req_id)).rowcount!=1:
                raise SLHError('NOT_PENDING',409)
            self._audit(c,actor,'enrollment.denied',req_id)
        return {'status':'DENIED'}

    def devices(self):
        with self._db() as c:
            return [_row(r) for r in c.execute('SELECT d.id,d.user_id,d.fingerprint,d.kind,d.name,d.notes,d.status,d.created_at,u.name AS user_name,u.role FROM devices d JOIN users u ON d.user_id=u.id ORDER BY d.name')]

    def update_device(self, device_id, changes, actor):
        if not changes or set(changes)-{'name','notes'}:
            raise SLHError('INVALID_DEVICE_EDIT')
        with self._lock,self._db() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT id FROM devices WHERE id=?', (device_id,)).fetchone()
            if not row:raise SLHError('DEVICE_NOT_FOUND',404)
            for field,value in changes.items():
                if field=='name': value=_required(value,'name')
                elif not isinstance(value,str) or len(value)>2000:raise SLHError('INVALID_NOTES')
                c.execute(f'UPDATE devices SET {field}=? WHERE id=?', (value,device_id))
            self._audit(c,actor,'device.updated',device_id,','.join(sorted(changes)))
        return {'status':'UPDATED','device_id':device_id}

    def revoke_device(self, device_id, actor):
        with self._lock,self._db() as c:
            c.execute('BEGIN IMMEDIATE')
            d=c.execute("SELECT d.id,u.role FROM devices d JOIN users u ON d.user_id=u.id WHERE d.id=? AND d.status='active'", (device_id,)).fetchone()
            if not d:
                raise SLHError('DEVICE_NOT_ACTIVE',409)
            if d['role']=='admin':
                count=c.execute("SELECT COUNT(*) FROM devices d JOIN users u ON d.user_id=u.id WHERE d.status='active' AND u.status='active' AND u.role='admin'").fetchone()[0]
                if count<=1:
                    raise SLHError('LAST_ADMIN_PROTECTED',409)
            c.execute("UPDATE devices SET status='revoked' WHERE id=?", (device_id,))
            self._audit(c,actor,'device.revoked',device_id)
        return {'status':'REVOKED','device_id':device_id}

    def users(self):
        with self._db() as c:
            return [_row(x) for x in c.execute('SELECT id,name,role,status FROM users ORDER BY name')]

    def policy(self, value=None, actor=None):
        with self._lock,self._db() as c:
            if value is not None:
                if not isinstance(value,bool):raise SLHError('INVALID_POLICY')
                c.execute("UPDATE policies SET value=? WHERE name='physical_inspection_required'",('true' if value else 'false',))
                self._audit(c,actor,'policy.physical_inspection',str(value))
            return {'physical_inspection_required':self._inspection_required(c)}

    def audit(self):
        with self._db() as c:
            return [_row(r) for r in c.execute('SELECT seq,at,actor,action,target,detail FROM audit ORDER BY seq DESC LIMIT 200')]
