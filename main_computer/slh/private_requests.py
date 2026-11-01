"""Durable SLH local work with atomic P3 obligation assessment.

No requester prompt or Worker response is stored. No Hub wallet, marketplace
session or Reserve transfer is invoked by this module.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid

from .authority import SLHError
from .pinky_promise import PinkyPromise, POLICY_ID

_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$')
_MODEL = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$')


class PrivateRequests:
    def __init__(self, state_dir: Path, authority, gateway, *, execution_timeout=120):
        self.authority = authority
        self.gateway = gateway
        self.org_id = authority.org_id()
        self.execution_timeout = execution_timeout
        self.pinky = PinkyPromise(self.org_id)
        self._lock = threading.RLock()
        path = Path(state_dir)
        path.mkdir(parents=True, exist_ok=True)
        if os.name == 'posix':
            os.chmod(path, 0o700)
        self.db_path = path / 'private_requests.sqlite3'
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS private_requests (
                 org_id TEXT NOT NULL, requester_device_id TEXT NOT NULL,
                 request_id TEXT NOT NULL, request_digest TEXT NOT NULL,
                 model TEXT NOT NULL, status TEXT NOT NULL,
                 worker_device_id TEXT, run_id TEXT,
                 attempt INTEGER NOT NULL DEFAULT 0,
                 admitted_at_ns INTEGER NOT NULL, updated_at_ns INTEGER NOT NULL,
                 fulfilled_at_ns INTEGER, result_hash TEXT, failure_code TEXT,
                 billing_policy_id TEXT NOT NULL DEFAULT 'p2-pre-assessment',
                 PRIMARY KEY (org_id,requester_device_id,request_id))''')
            columns = {column['name'] for column in db.execute('PRAGMA table_info(private_requests)')}
            # Existing P2 rows, including previous SUCCEEDED requests, are
            # nonbillable.  Never retroactively assess them during migration.
            if 'pinky_policy_revision' not in columns:
                db.execute('ALTER TABLE private_requests ADD COLUMN pinky_policy_revision INTEGER NOT NULL DEFAULT 0')
            if 'pinky_enabled' not in columns:
                db.execute('ALTER TABLE private_requests ADD COLUMN pinky_enabled INTEGER NOT NULL DEFAULT 0')
            self.pinky.initialize(db)
            # A crash between admission and dispatch cannot have executed work.
            # A crash after DISPATCHED might have executed and must not replay.
            db.execute("UPDATE private_requests SET status='FAILED', failure_code='INTERRUPTED_BEFORE_DISPATCH', updated_at_ns=? WHERE status='ADMITTED'", (time.time_ns(),))
            db.execute("UPDATE private_requests SET status='OUTCOME_UNKNOWN', failure_code='INTERRUPTED_AFTER_DISPATCH', updated_at_ns=? WHERE status='DISPATCHED'", (time.time_ns(),))
        if os.name == 'posix':
            os.chmod(self.db_path, 0o600)

    @contextmanager
    def _db(self):
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('PRAGMA busy_timeout=15000')
            conn.execute('PRAGMA synchronous=FULL')
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _requester(identity):
        if not identity or identity.get('role') != 'requester':
            raise SLHError('REQUESTER_REQUIRED', 403)
        return identity['device_id']

    @staticmethod
    def _public(row):
        return {'schema':'main-computer.slh-private-work.v1',
                'request_id':row['request_id'], 'model':row['model'],
                'status':row['status'], 'worker_device_id':row['worker_device_id'],
                'run_id':row['run_id'], 'attempt':row['attempt'],
                'admitted_at_ns':row['admitted_at_ns'],
                'updated_at_ns':row['updated_at_ns'],
                'fulfilled_at_ns':row['fulfilled_at_ns'],
                'result_hash':row['result_hash'], 'failure_code':row['failure_code'],
                'billing_policy_id':row['billing_policy_id'],
                'pinky_policy_revision':row['pinky_policy_revision']}

    def policy(self):
        with self._lock, self._db() as db:
            return self.pinky.snapshot(db)

    def update_policy(self, body, actor):
        with self._lock, self._db() as db:
            return self.pinky.update(db, body, actor)

    def obligations(self):
        with self._lock, self._db() as db:
            return self.pinky.obligations(db)

    def get(self, identity, request_id):
        requester = self._requester(identity)
        if not isinstance(request_id,str) or not _ID.fullmatch(request_id):
            raise SLHError('INVALID_PRIVATE_REQUEST_ID', 400)
        with self._db() as db:
            row = db.execute('SELECT * FROM private_requests WHERE org_id=? AND requester_device_id=? AND request_id=?',
                             (self.org_id,requester,request_id)).fetchone()
        if row is None:
            raise SLHError('PRIVATE_REQUEST_NOT_FOUND',404)
        return self._public(row)

    def _update(self, requester, req, status, *, worker=None, run_id=None, result_hash=None, failure_code=None):
        with self._lock, self._db() as db:
            db.execute('UPDATE private_requests SET status=?, worker_device_id=COALESCE(?,worker_device_id), run_id=COALESCE(?,run_id), result_hash=?, failure_code=?, updated_at_ns=?, fulfilled_at_ns=? WHERE org_id=? AND requester_device_id=? AND request_id=?',
                       (status,worker,run_id,result_hash,failure_code,time.time_ns(),
                        time.time_ns() if status=='SUCCEEDED' else None,
                        self.org_id,requester,req))
        return self.get({'role':'requester','device_id':requester},req)

    def _complete_success(self, requester, req, result_hash):
        # A successful fulfillment and its liability must commit together.
        # An exception rolls back both changes; after a crash the prior
        # DISPATCHED row becomes OUTCOME_UNKNOWN, never silently billable.
        with self._lock, self._db() as db:
            row = db.execute('SELECT * FROM private_requests WHERE org_id=? AND requester_device_id=? AND request_id=?',
                             (self.org_id,requester,req)).fetchone()
            if row is None or row['status'] != 'DISPATCHED':
                raise SLHError('PRIVATE_FULFILLMENT_STATE_CONFLICT',409)
            now=time.time_ns()
            if row['pinky_enabled']:
                self.pinky.record(db,row,result_hash,now)
            updated=db.execute('''UPDATE private_requests
                SET status='SUCCEEDED',result_hash=?,failure_code=NULL,
                    fulfilled_at_ns=?,updated_at_ns=?
                WHERE org_id=? AND requester_device_id=? AND request_id=?
                AND status='DISPATCHED' ''',
                (result_hash,now,now,self.org_id,requester,req))
            if updated.rowcount != 1:
                raise SLHError('PRIVATE_FULFILLMENT_STATE_CONFLICT',409)
        return self.get({'role':'requester','device_id':requester},req)

    def submit(self, identity, body):
        requester = self._requester(identity)
        if set(body) != {'request_id','model','work'}:
            raise SLHError('INVALID_PRIVATE_WORK_REQUEST',400)
        req, model, work = body['request_id'], body['model'], body['work']
        if not isinstance(req,str) or not _ID.fullmatch(req):
            raise SLHError('INVALID_PRIVATE_REQUEST_ID',400)
        if not isinstance(model,str) or not _MODEL.fullmatch(model):
            raise SLHError('INVALID_PRIVATE_MODEL',400)
        if not isinstance(work,dict) or work.get('model') != model or not isinstance(work.get('input'),dict):
            raise SLHError('INVALID_PRIVATE_WORK',400)
        # P2 supports only chat-model work, not arbitrary Worker capabilities
        # or the diagnostic echo bypass. A later capability needs its own policy.
        kind = work['input'].get('kind')
        if kind == 'echo':
            raise SLHError('PRIVATE_ECHO_NOT_ALLOWED',400)
        if kind not in ('chat','chat.completions') or set(work) - {'model','input','execution_limits'}:
            raise SLHError('PRIVATE_CAPABILITY_NOT_ALLOWED',400)
        if not isinstance(work['input'].get('prompt'),str) and not isinstance(work['input'].get('messages'),list):
            raise SLHError('PRIVATE_CHAT_INPUT_REQUIRED',400)
        try:
            encoded = json.dumps({'model':model,'work':work}, sort_keys=True, separators=(',',':'),allow_nan=False).encode('utf-8')
        except (TypeError,ValueError):
            raise SLHError('INVALID_PRIVATE_WORK',400)
        if len(encoded)>65536:
            raise SLHError('PRIVATE_WORK_TOO_LARGE',413)
        digest = hashlib.sha256(encoded).hexdigest()
        with self._lock, self._db() as db:
            row = db.execute('SELECT * FROM private_requests WHERE org_id=? AND requester_device_id=? AND request_id=?',
                             (self.org_id,requester,req)).fetchone()
            if row:
                if row['request_digest']!=digest:
                    raise SLHError('PRIVATE_REQUEST_ID_REUSED',409)
                return self._public(row)  # never reexecute on replay
            now=time.time_ns()
            policy=self.pinky.snapshot(db)
            db.execute('''INSERT INTO private_requests
                (org_id,requester_device_id,request_id,request_digest,model,status,
                 admitted_at_ns,updated_at_ns,billing_policy_id,pinky_policy_revision,pinky_enabled)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                (self.org_id,requester,req,digest,model,'ADMITTED',now,now,
                 POLICY_ID,policy['revision'],int(policy['enabled'])))
        # Race after auth or revocation cannot be allowed to dispatch.
        # Authentication was performed by SLHAPI, before this method is called.
        sid='private:'+uuid.uuid4().hex
        # Recheck enrollment before choosing a Worker after durable admission.
        if not any(d['id']==requester and d['role']=='requester' and d['status']=='active'
                   for d in self.authority.devices()):
            return self._update(requester,req,'FAILED',failure_code='REQUESTER_REVOKED')
        try:
            worker=self.gateway.scheduler.reserve_private(sid,model=model)
        except RuntimeError:
            return self._update(requester,req,'FAILED',failure_code='NO_ELIGIBLE_WORKER')
        try:
            if worker.device_id == requester or not self.gateway.scheduler.is_authorized(worker.device_id):
                return self._update(requester,req,'FAILED',failure_code='WORKER_UNAUTHORIZED')
            run_id=uuid.uuid4().hex
            self._update(requester,req,'DISPATCHED',worker=worker.device_id,run_id=run_id)
            try:
                result=self.gateway.scheduler.execute_private(
                    {'session_id':sid,'request_id':req,'run_id':run_id,'work':work},
                    timeout=self.execution_timeout)
            except Exception as exc:
                if isinstance(exc, RuntimeError) and str(exc)=='PRIVATE_WORKER_FAILED':
                    return self._update(requester,req,'FAILED',failure_code='WORKER_TERMINAL_FAILED')
                # Timeout, disconnect, send failure, or mismatched terminal: may
                # have executed. No automatic retry on a new worker.
                return self._update(requester,req,'OUTCOME_UNKNOWN',failure_code='EXECUTION_OUTCOME_UNCERTAIN')
            if result.get('status') != 'success' or result.get('debug_echo'):
                return self._update(requester,req,'FAILED',failure_code='INVALID_WORKER_RESULT')
            proof=hashlib.sha256(json.dumps(result,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')).hexdigest()
            response=self._complete_success(requester,req,proof)
            response['result']=result  # volatile: never persisted / journaled
            return response
        finally:
            self.gateway.scheduler.release(sid)
