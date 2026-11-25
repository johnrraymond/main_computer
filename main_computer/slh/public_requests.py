"""ORG-owned paid requester gateway. No MSK or continuation secret leaves SLH.

The administrator opts in with a cumulative maximum-liability budget. A durable
admission retains its full price ceiling (including ambiguous upstream outcomes)
until explicitly raising the budget; this intentionally errs against overspending.
The Stable Hub remains the authoritative debit and idempotency authority.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit

from main_computer.stable_hub_worker_sessions import normalize_request_market_constraints
from .authority import SLHError
from .marketplace_auto import load_authorization, read_settings
from .organization_wallet import HubCreditReader
from .storage import secure_private_state_file

PATH = '/v1/marketplace/work/requests'
POLICY_PATH = '/v1/org/admin/marketplace/requester-policy'
REQUEST_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z')


def _credit(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,78}', value):
        raise SLHError('INVALID_PUBLIC_SPENDING_LIMIT', 400)
    return int(value)


def _request_id(value):
    if not isinstance(value, str) or not REQUEST_ID.fullmatch(value):
        raise SLHError('INVALID_PUBLIC_REQUEST_ID', 400)
    return value


class PublicRequests:
    def __init__(self, state_dir, controller):
        self.state_dir = Path(state_dir)
        self.controller = controller
        self.wallet = controller.wallet_service
        self.path = self.state_dir / 'public_requests.private.sqlite3'
        new_file = not self.path.exists()
        if self.path.is_symlink():
            raise SLHError('UNTRUSTED_PUBLIC_REQUEST_STORE',503)
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS policy(singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                 revision INTEGER NOT NULL, enabled INTEGER NOT NULL, max_request TEXT NOT NULL, budget TEXT NOT NULL)''')
            db.execute("INSERT OR IGNORE INTO policy VALUES(1,0,0,'0','0')")
            db.execute('''CREATE TABLE IF NOT EXISTS admissions(request_id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL, digest TEXT NOT NULL, max_credit TEXT NOT NULL,
                hub_url TEXT NOT NULL, hub_id TEXT NOT NULL, cluster_id TEXT NOT NULL,
                chain_id TEXT NOT NULL, account_id TEXT NOT NULL, session_id TEXT,
                owner_id TEXT, owner_url TEXT, continuation TEXT)''')
        if new_file:
            secure_private_state_file(self.path)
        elif os.name == 'posix':
            os.chmod(self.path, 0o600)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _policy(db):
        row = db.execute('SELECT * FROM policy WHERE singleton=1').fetchone()
        used = sum(int(r['max_credit']) for r in db.execute('SELECT max_credit FROM admissions'))
        return {'revision': row['revision'], 'enabled': bool(row['enabled']),
                'max_request_credit_wei': row['max_request'], 'budget_credit_wei': row['budget'],
                'reserved_ceiling_credit_wei': str(used)}

    def policy(self):
        with self._db() as db:
            return self._policy(db)

    def configure(self, body):
        if (set(body) != {'expected_revision','enabled','max_request_credit_wei','budget_credit_wei'}
                or type(body['expected_revision']) is not int or type(body['enabled']) is not bool):
            raise SLHError('INVALID_PUBLIC_SPENDING_POLICY', 400)
        limit, budget = _credit(body['max_request_credit_wei']), _credit(body['budget_credit_wei'])
        if body['enabled'] and not 0 < limit <= budget:
            raise SLHError('INVALID_PUBLIC_SPENDING_LIMIT', 400)
        with self.controller._lock, self._db() as db:
            prior = self._policy(db)
            if body['expected_revision'] != prior['revision']:
                raise SLHError('PUBLIC_SPENDING_REVISION_CONFLICT', 409)
            db.execute('UPDATE policy SET revision=revision+1,enabled=?,max_request=?,budget=? WHERE singleton=1',
                       (body['enabled'], str(limit), str(budget)))
            return self._policy(db)

    @staticmethod
    def _device(identity):
        if identity.get('role') != 'requester':
            raise SLHError('REQUESTER_REQUIRED', 403)
        return identity['device_id']

    def _binding(self, *, reconcile):
        current = self.wallet.reconcile() if reconcile else self.wallet.status()
        binding = current.get('binding')
        if not binding or (reconcile and current['state'] != 'CURRENT'):
            raise SLHError('ORG_WALLET_NOT_CURRENT', 409)
        with self.wallet._lock, self.wallet._db() as db:
            internal = self.wallet._binding(db)
        return internal, current.get('balance')

    @staticmethod
    def _same_binding(row, binding):
        return all(row[k] == binding[k] for k in ('hub_url','hub_id','cluster_id','chain_id','account_id'))

    @staticmethod
    def _reader(binding):
        return HubCreditReader(binding['hub_url'], binding['tls_sha256'])

    def _owner_reader(self, binding, owner_id, owner_url):
        entry = self._reader(binding)
        # A local fixture may advertise its production-shaped URL but bind an
        # ephemeral loopback port. Same owner always uses the configured entry.
        if owner_id == binding['hub_id'] and (entry.trust_mode != 'SYSTEM_TLS' or
                owner_url.rstrip('/') == binding['hub_url'].rstrip('/')):
            return entry
        # Cross-Hub routing is permitted only to a CA-trusted, verified peer
        # advertised by the already trusted entry Hub. Never forward credentials
        # to caller-controlled URLs or downgrade TLS.
        if entry.trust_mode != 'SYSTEM_TLS':
            raise SLHError('PUBLIC_CONTINUATION_PEER_NOT_TRUSTED', 502)
        parsed = urlsplit(owner_url)
        if parsed.scheme != 'https':
            raise SLHError('PUBLIC_CONTINUATION_PEER_NOT_TRUSTED', 502)
        topology = entry.request('GET','/api/hub/v1/topology').get('topology') or {}
        if topology.get('cluster_id') != binding['cluster_id'] or not any(
                p.get('hub_id') == owner_id and owner_url in (p.get('hub_url'),p.get('public_url'))
                for p in topology.get('hubs', []) if isinstance(p, dict)):
            raise SLHError('PUBLIC_CONTINUATION_PEER_NOT_TRUSTED', 502)
        peer = HubCreditReader(owner_url)
        identity = peer.request('GET','/api/hub/v1/hub-identity')
        if (identity.get('service') != 'main_computer.stable_hub' or
                identity.get('hub_id') != owner_id or identity.get('cluster_id') != binding['cluster_id'] or
                str((identity.get('network') or {}).get('chain_id')) != binding['chain_id']):
            raise SLHError('PUBLIC_CONTINUATION_PEER_NOT_TRUSTED', 502)
        return peer

    def submit(self, identity, body):
        device = self._device(identity)
        if set(body) != {'request_id','work'} or not isinstance(body['work'], dict):
            raise SLHError('INVALID_PUBLIC_REQUEST', 400)
        req = _request_id(body['request_id'])
        work = body['work']
        try:
            # Bound raw decimal syntax before the Hub normalizer can expand
            # a hostile exponent or allocate an unbounded decimal string.
            raw = work.get('max_price')
            if not isinstance(raw, dict) or set(raw) != {'amount','unit'} or raw['unit'] != 'compute_credit':
                raise ValueError()
            if not isinstance(raw['amount'], str) or not re.fullmatch(r'[0-9]{1,10}(?:\.[0-9]{1,18})?',raw['amount']):
                raise ValueError()
            constraints = normalize_request_market_constraints(work)
            price = constraints['max_price']
            if not price or price['unit'] != 'compute_credit' or len(price['amount']) > 78:
                raise ValueError()
            # Exact integer arithmetic; Decimal's default precision is not
            # enough for an 10+18-digit maximum price.
            whole, _, fraction = raw['amount'].partition('.')
            ceiling = int(whole)*10**18 + int(fraction.ljust(18,'0'))
            if ceiling <= 0:
                raise ValueError()
            digest = hashlib.sha256(json.dumps(work,sort_keys=True,separators=(',', ':'),allow_nan=False).encode()).hexdigest()
        except (TypeError, ValueError, ArithmeticError) as exc:
            raise SLHError('INVALID_PUBLIC_REQUEST_PRICE_OR_WORK', 400) from exc
        # The same controller serializes admission, configuration and credential
        # rotation. The database commits BEFORE network submission; a restart
        # can only retry the same Hub request ID and identical work.
        with self.controller._lock:
            if (getattr(self.controller, 'financial_startup', False) and
                    self.controller.financial_readiness.get('state') != 'READY'):
                raise SLHError('ORG_FINANCIAL_NOT_READY', 503)
            binding, balance = self._binding(reconcile=True)
            with self._db() as db:
                row = db.execute('SELECT * FROM admissions WHERE request_id=?',(req,)).fetchone()
                if row:
                    if row['device_id'] != device:
                        raise SLHError('PUBLIC_REQUEST_NOT_FOUND',404)
                    if row['digest'] != digest or not self._same_binding(row,binding):
                        raise SLHError('PUBLIC_REQUEST_CONFLICT',409)
                    if row['session_id']:
                        return {'request_id':req,'session_id':row['session_id'],'status':'accepted','idempotent':True}
                policy = self._policy(db)
                if not policy['enabled'] or not read_settings(self.state_dir)['enabled']:
                    raise SLHError('PUBLIC_REQUESTS_DISABLED',403)
                if ceiling > int(policy['max_request_credit_wei']):
                    raise SLHError('PUBLIC_REQUEST_PRICE_LIMIT',403)
                if int(policy['reserved_ceiling_credit_wei']) + (0 if row else ceiling) > int(policy['budget_credit_wei']):
                    raise SLHError('PUBLIC_REQUEST_BUDGET_LIMIT',403)
                if not row and ceiling > int(balance['available_credit_wei']):
                    raise SLHError('PUBLIC_REQUEST_INSUFFICIENT_CREDITS',409)
                if not row:
                    db.execute('INSERT INTO admissions(request_id,device_id,digest,max_credit,hub_url,hub_id,cluster_id,chain_id,account_id) VALUES(?,?,?,?,?,?,?,?,?)',
                               (req,device,digest,str(ceiling),*(binding[k] for k in ('hub_url','hub_id','cluster_id','chain_id','account_id'))))
            auth = load_authorization(self.state_dir)
            # Binding reconciliation validated the saved key and account.
            reply = self._reader(binding).request('POST','/api/hub/v1/work/requests',
                      {'request_id':req,'work':work,'multisession_authorization':auth})
            sid, token = reply.get('session_id'),reply.get('continuation_token')
            owner = reply.get('execution_hub') or {}
            owner_id, owner_url = owner.get('hub_id'),owner.get('hub_url')
            if (reply.get('ok') is not True or reply.get('accepted') is not True or reply.get('request_id') != req
                    or reply.get('cluster_id') != binding['cluster_id'] or reply.get('hub_id') != binding['hub_id']
                    or not isinstance(sid,str) or not REQUEST_ID.fullmatch(sid)
                    or not isinstance(token,str) or not 1 <= len(token) <= 4096 or not isinstance(owner_id,str) or not isinstance(owner_url,str)):
                raise SLHError('INVALID_PUBLIC_HUB_RECEIPT',502)
            self._owner_reader(binding,owner_id,owner_url)
            with self._db() as db:
                db.execute('UPDATE admissions SET session_id=?,owner_id=?,owner_url=?,continuation=? WHERE request_id=?',
                           (sid,owner_id,owner_url,token,req))
            return {'request_id':req,'session_id':sid,'status':'accepted','idempotent':bool(reply.get('idempotent'))}

    def get(self, identity, request_id):
        device, req = self._device(identity), _request_id(request_id)
        with self.controller._lock:
            with self._db() as db:
                row = db.execute('SELECT * FROM admissions WHERE request_id=? AND device_id=?',(req,device)).fetchone()
            if not row:
                raise SLHError('PUBLIC_REQUEST_NOT_FOUND',404)
            if not row['session_id']:
                return {'request_id':req,'status':'outcome_unknown'}
            binding, _ = self._binding(reconcile=False)
            if not self._same_binding(row,binding):
                raise SLHError('PUBLIC_REQUEST_BINDING_CHANGED',409)
            reader = self._owner_reader(binding,row['owner_id'],row['owner_url'])
            reply = reader.request('GET',f"/api/hub/v1/work/sessions/{row['session_id']}/stream",bearer_token=row['continuation'])
            record = reply.get('accepted_session') or {}
            if (reply.get('ok') is not True or reply.get('cluster_id') != binding['cluster_id'] or
                    reply.get('hub_id') != row['owner_id'] or record.get('request_id') != req or record.get('session_id') != row['session_id']):
                raise SLHError('INVALID_PUBLIC_HUB_RECEIPT',502)
            status = record.get('status')
            result = {'request_id':req,'session_id':row['session_id'],'status':status}
            if status == 'succeeded':
                result['result'] = (record.get('worker_result') or {}).get('result')
            return result
