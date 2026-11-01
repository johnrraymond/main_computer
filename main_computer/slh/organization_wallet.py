"""SLH read-only, Hub-authoritative organization credit reconciliation (P1).

An MSK proves account association, not spending authority.  This local SQLite
store contains only a verified binding and last observed balance; it cannot
mint, reserve, settle, or claim Hub credits.  P3/P4 obligations and transfers
must never infer spendability from a stale snapshot.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import ssl
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

from main_computer.credit_units import credit_wei_to_decimal_text
from main_computer.hub_credit_indexer import wallet_account_id
from main_computer.hub_credit_models import normalize_address

from .authority import SLHError
from .marketplace_auto import load_authorization, read_settings

DB_NAME = 'organization-wallet.sqlite3'
MAX_RESPONSE_BYTES = 128 * 1024
MAX_OBSERVATION_AGE_SECONDS = 120
_SHA256 = re.compile(r'^[a-f0-9]{64}$')
_NONNEGATIVE = re.compile(r'^(0|[1-9][0-9]*)$')


def _nonnegative_wei(value, field):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise SLHError('INVALID_HUB_BALANCE', 502)
    raw = str(value)
    if len(raw) > 78 or not _NONNEGATIVE.fullmatch(raw):
        raise SLHError('INVALID_HUB_BALANCE', 502)
    return int(raw)


def _identity(value, field):
    if not isinstance(value, str) or not 1 <= len(value) <= 160 or not value.strip():
        raise SLHError('INVALID_HUB_IDENTITY', 502)
    return value.strip()


def _digest_key(key):
    if not isinstance(key, str) or not key or len(key) > 4096:
        raise SLHError('INVALID_MARKETPLACE_AUTHORIZATION', 409)
    return hashlib.sha256(key.encode('utf-8')).hexdigest()


class HubCreditReader:
    """Single-origin, nonredirecting JSON client: CA-valid HTTPS or explicit cert pin.

    Plain HTTP is accepted only on the same machine for the isolated lab; a
    loopback observation is marked LAB_ONLY and is not production attestation.
    """

    def __init__(self, url: str, tls_sha256: str | None = None):
        parsed = urlsplit(url)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or
                parsed.username is not None or parsed.password is not None or
                parsed.query or parsed.fragment or parsed.path not in ('', '/') or
                len(url) > 2048):
            raise SLHError('UNTRUSTED_HUB_ORIGIN', 409)
        try:
            port = parsed.port
        except ValueError as exc:
            raise SLHError('UNTRUSTED_HUB_ORIGIN', 409) from exc
        self.host = parsed.hostname
        self.port = port or (443 if parsed.scheme == 'https' else 80)
        self.scheme = parsed.scheme
        self.tls_sha256 = tls_sha256
        if tls_sha256 is not None and (not isinstance(tls_sha256, str) or
                                       not _SHA256.fullmatch(tls_sha256)):
            raise SLHError('INVALID_HUB_TLS_PIN', 400)
        if self.scheme == 'http' and (self.host not in ('127.0.0.1', '::1', 'localhost') or tls_sha256):
            raise SLHError('UNTRUSTED_HUB_ORIGIN', 409)
        self.trust_mode = ('LAB_ONLY' if self.scheme == 'http' else
                           'TLS_PINNED' if tls_sha256 else 'SYSTEM_TLS')

    def request(self, method: str, path: str, data: dict | None = None):
        if method not in ('POST', 'GET') or not path.startswith('/api/hub/v1/'):
            raise SLHError('INVALID_HUB_REQUEST')
        connection = None
        try:
            if self.scheme == 'https':
                # Explicit pin may authenticate an otherwise self-signed Hub;
                # never transmit the MSK before the peer certificate is checked.
                ctx = ssl.create_default_context()
                if self.tls_sha256 is not None:
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                connection = http.client.HTTPSConnection(self.host, self.port, timeout=5, context=ctx)
                connection.connect()
                if self.tls_sha256 is not None:
                    cert = connection.sock.getpeercert(binary_form=True)
                    if not cert or hashlib.sha256(cert).hexdigest() != self.tls_sha256:
                        raise SLHError('HUB_TLS_PIN_MISMATCH', 503)
            else:
                connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
            raw = json.dumps(data, separators=(',', ':')).encode() if data is not None else None
            connection.request(method, path, body=raw, headers={
                'Content-Type': 'application/json', 'Accept': 'application/json', 'Connection': 'close',
            })
            response = connection.getresponse()
            if response.status != 200:
                raise SLHError('HUB_ACCOUNT_UNAVAILABLE', 503)
            size = response.getheader('Content-Length')
            if size is not None and (not size.isdigit() or int(size) > MAX_RESPONSE_BYTES):
                raise SLHError('INVALID_HUB_RESPONSE', 502)
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise SLHError('INVALID_HUB_RESPONSE', 502)
            value = json.loads(body)
            if not isinstance(value, dict):
                raise SLHError('INVALID_HUB_RESPONSE', 502)
            return value
        except SLHError:
            raise
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError, http.client.HTTPException) as exc:
            raise SLHError('HUB_ACCOUNT_UNAVAILABLE', 503) from exc
        finally:
            if connection is not None:
                connection.close()


class OrganizationWallet:
    def __init__(self, state_dir: Path, org_id: str, *, reader_factory=HubCreditReader, clock=time.time):
        self.path = Path(state_dir) / DB_NAME
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.org_id = _identity(org_id, 'org_id')
        self.reader_factory = reader_factory
        self.clock = clock
        self._lock = threading.RLock()
        with self._db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS binding(
              singleton INTEGER PRIMARY KEY CHECK(singleton=1), org_id TEXT NOT NULL,
              hub_url TEXT NOT NULL, cluster_id TEXT NOT NULL, hub_id TEXT NOT NULL,
              chain_id TEXT NOT NULL, wallet_address TEXT NOT NULL, account_id TEXT NOT NULL,
              msk_digest TEXT NOT NULL, tls_sha256 TEXT, trust_mode TEXT NOT NULL,
              revision INTEGER NOT NULL, bound_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS observation(
              singleton INTEGER PRIMARY KEY CHECK(singleton=1), binding_revision INTEGER NOT NULL,
              observed_at INTEGER NOT NULL, available_credit_wei TEXT NOT NULL,
              held_credit_wei TEXT NOT NULL, spent_credit_wei TEXT NOT NULL,
              bridge_completed_credit_wei TEXT NOT NULL, hub_earned_credit_wei TEXT, proof_digest TEXT NOT NULL,
              state TEXT NOT NULL, reason TEXT NOT NULL);
            ''')
        if os.name == 'posix':
            os.chmod(self.path, 0o600)

    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=10000')
        return _DBContext(db)

    def _binding(self, db):
        row = db.execute('SELECT * FROM binding WHERE singleton=1').fetchone()
        return dict(row) if row else None

    def _observation(self, db):
        row = db.execute('SELECT * FROM observation WHERE singleton=1').fetchone()
        return dict(row) if row else None

    def _settings(self):
        settings = read_settings(self.path.parent)
        auth = load_authorization(self.path.parent)
        if not settings['hub_url'] or auth is None:
            raise SLHError('ORG_WALLET_AUTHORIZATION_REQUIRED', 409)
        return settings['hub_url'], auth['multisession_key_id']

    def _validate(self, reader, key_id, *, hub_id=None, cluster_id=None, chain_id=None):
        result = reader.request('POST', '/api/hub/v1/credits/multisession-keys/validate', {
            'multisession_authorization': {'kind': 'multisession_key', 'multisession_key_id': key_id},
        })
        if result.get('ok') is not True or result.get('valid') is not True or result.get('ready') is not True:
            raise SLHError('HUB_MSK_INVALID', 409)
        actual_hub = _identity(result.get('hub_id'), 'hub_id')
        actual_cluster = _identity(result.get('cluster_id'), 'cluster_id')
        actual_chain = _identity(str(result.get('chain_id') or ''), 'chain_id')
        if ((hub_id is not None and actual_hub != hub_id) or
                (cluster_id is not None and actual_cluster != cluster_id) or
                (chain_id is not None and actual_chain != chain_id) or
                result.get('multisession_key_id') != key_id):
            raise SLHError('HUB_IDENTITY_MISMATCH', 409)
        try:
            wallet = normalize_address(result['wallet_address'])
            account = wallet_account_id(wallet)
        except (ValueError, KeyError, TypeError) as exc:
            raise SLHError('INVALID_HUB_IDENTITY', 502) from exc
        if result.get('account_id') != account:
            raise SLHError('HUB_WALLET_MISMATCH', 409)
        return {'hub_id': actual_hub, 'cluster_id': actual_cluster,
                'chain_id': actual_chain, 'wallet_address': wallet, 'account_id': account}

    def _balance(self, reader, wallet, account):
        result = reader.request('GET', '/api/hub/v1/credits/balance?wallet_address=' + quote(wallet, safe=''))
        if (result.get('ok') is not True or result.get('wallet_address') != wallet or
                result.get('account_id') != account or result.get('unit') != 'compute_credit'):
            raise SLHError('HUB_BALANCE_IDENTITY_MISMATCH', 409)
        body = result.get('account')
        if (not isinstance(body, dict) or body.get('account_id') != account or
                body.get('wallet_address') != wallet or
                body.get('owner_address', wallet) != wallet):
            raise SLHError('HUB_BALANCE_IDENTITY_MISMATCH', 409)
        quantities = {}
        for key in ('available_credit_wei', 'held_credit_wei',
                    'spent_credit_wei', 'bridge_completed_credit_wei'):
            if key not in body and key == 'available_credit_wei':
                raise SLHError('INVALID_HUB_BALANCE', 502)
            quantities[key] = str(_nonnegative_wei(body.get(key, '0'), key))
        if 'available_credits_display' in body and body['available_credits_display'] != credit_wei_to_decimal_text(int(quantities['available_credit_wei'])):
            raise SLHError('INVALID_HUB_BALANCE', 502)
        quantities['hub_earned_credit_wei'] = (str(_nonnegative_wei(body['earned_credit_wei'], 'earned_credit_wei'))
                                               if 'earned_credit_wei' in body else None)
        return quantities, hashlib.sha256(json.dumps(result, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

    def validate_new_authorization(self, hub_url: str | None, key_id: str):
        # A new public MSK must not silently point Workers at an unrelated
        # financial wallet after organization binding has been established.
        with self._lock, self._db() as db:
            binding = self._binding(db)
        if binding is None:
            return  # First authorization still requires explicit verified bind.
        if hub_url != binding['hub_url']:
            raise SLHError('WALLET_MIGRATION_REQUIRED', 409)
        reader = self.reader_factory(binding['hub_url'], binding['tls_sha256'])
        proof = self._validate(reader, key_id, hub_id=binding['hub_id'],
                               cluster_id=binding['cluster_id'], chain_id=binding['chain_id'])
        if proof['wallet_address'] != binding['wallet_address'] or proof['account_id'] != binding['account_id']:
            raise SLHError('HUB_WALLET_MISMATCH', 409)

    def bind(self, *, expected_revision: int, expected_hub_id: str, expected_cluster_id: str,
             expected_chain_id: str, tls_sha256: str | None = None):
        if type(expected_revision) is not int or expected_revision < 0:
            raise SLHError('WALLET_REVISION_REQUIRED', 400)
        expected_hub_id = _identity(expected_hub_id, 'hub_id')
        expected_cluster_id = _identity(expected_cluster_id, 'cluster_id')
        expected_chain_id = _identity(expected_chain_id, 'chain_id')
        with self._lock:
            hub_url, key = self._settings()
            reader = self.reader_factory(hub_url, tls_sha256)
            proof = self._validate(reader, key, hub_id=expected_hub_id,
                                   cluster_id=expected_cluster_id, chain_id=expected_chain_id)
            # Refuse a different wallet or Hub *before* accepting its balance;
            # P3/P4 must supply a debt-preserving migration contract.
            with self._db() as db:
                prior = self._binding(db)
                if prior and any(prior[k] != v for k, v in (
                        ('org_id', self.org_id), ('hub_url', hub_url),
                        ('hub_id', expected_hub_id), ('cluster_id', expected_cluster_id),
                        ('chain_id', expected_chain_id), ('wallet_address', proof['wallet_address']),
                        ('account_id', proof['account_id']))):
                    raise SLHError('WALLET_MIGRATION_REQUIRED', 409)
            quantities, digest = self._balance(reader, proof['wallet_address'], proof['account_id'])
            now = int(self.clock())
            with self._db() as db:
                current = self._binding(db)
                revision = current['revision'] if current else 0
                if revision != expected_revision:
                    raise SLHError('WALLET_REVISION_CONFLICT', 409)
                if current and any(current[key] != new for key, new in (
                        ('org_id', self.org_id), ('hub_url', hub_url),
                        ('hub_id', expected_hub_id), ('cluster_id', expected_cluster_id),
                        ('chain_id', expected_chain_id), ('wallet_address', proof['wallet_address']),
                        ('account_id', proof['account_id']))):
                    # No unproved wallet rotation while obligations could exist.
                    raise SLHError('WALLET_MIGRATION_REQUIRED', 409)
                new_revision = revision + 1
                db.execute('INSERT OR REPLACE INTO binding VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (self.org_id, hub_url, expected_cluster_id, expected_hub_id,
                            expected_chain_id, proof['wallet_address'], proof['account_id'],
                            _digest_key(key), tls_sha256, reader.trust_mode, new_revision, now))
                self._save_observation(db, new_revision, now, quantities, digest)
            return self.status()

    @staticmethod
    def _save_observation(db, revision, now, quantities, digest):
        db.execute('INSERT OR REPLACE INTO observation VALUES(1,?,?,?,?,?,?,?,?,?,?)',
                   (revision, now, quantities['available_credit_wei'], quantities['held_credit_wei'],
                    quantities['spent_credit_wei'], quantities['bridge_completed_credit_wei'],
                    quantities['hub_earned_credit_wei'], digest, 'CURRENT', ''))

    def _mark_stale(self, reason):
        with self._db() as db:
            if self._observation(db):
                db.execute('UPDATE observation SET state=?, reason=? WHERE singleton=1', ('STALE', reason))

    def reconcile(self):
        with self._lock:
            try:
                with self._db() as db:
                    binding = self._binding(db)
                if binding is None:
                    raise SLHError('ORG_WALLET_NOT_BOUND', 409)
                hub_url, key = self._settings()
                if (hub_url != binding['hub_url'] or _digest_key(key) != binding['msk_digest'] or
                        self.org_id != binding['org_id']):
                    raise SLHError('WALLET_BINDING_CHANGED', 409)
                reader = self.reader_factory(hub_url, binding['tls_sha256'])
                if reader.trust_mode != binding['trust_mode']:
                    raise SLHError('HUB_TRUST_CHANGED', 409)
                proof = self._validate(reader, key, hub_id=binding['hub_id'],
                                       cluster_id=binding['cluster_id'], chain_id=binding['chain_id'])
                if proof['wallet_address'] != binding['wallet_address'] or proof['account_id'] != binding['account_id']:
                    raise SLHError('HUB_WALLET_MISMATCH', 409)
                quantities, digest = self._balance(reader, binding['wallet_address'], binding['account_id'])
                now = int(self.clock())
                with self._db() as db:
                    active = self._binding(db)
                    if active != binding:
                        raise SLHError('WALLET_REVISION_CONFLICT', 409)
                    self._save_observation(db, binding['revision'], now, quantities, digest)
            except SLHError as exc:
                self._mark_stale(exc.code)
                raise
            return self.status()

    def status(self):
        with self._lock, self._db() as db:
            binding = self._binding(db)
            observation = self._observation(db)
            if binding is None:
                return {'schema':'main-computer.slh-org-wallet.v1','state':'UNBOUND',
                        'spendable_authorized':False, 'binding':None, 'balance':None}
            public = {k: binding[k] for k in (
                'org_id','hub_url','cluster_id','hub_id','chain_id','wallet_address',
                'account_id','revision','bound_at','trust_mode')}
            state = 'STALE'
            reason = 'NO_VERIFIED_OBSERVATION'
            if observation:
                state = observation['state']
                reason = observation['reason']
                if observation['binding_revision'] != binding['revision']:
                    state, reason = 'STALE','BINDING_REVISION_CHANGED'
                else:
                    age = int(self.clock()) - observation['observed_at']
                    if not 0 <= age <= MAX_OBSERVATION_AGE_SECONDS:
                        state, reason = 'STALE','OBSERVATION_EXPIRED'
            try:
                hub_url, key = self._settings()
                if (hub_url != binding['hub_url'] or _digest_key(key) != binding['msk_digest'] or
                        self.org_id != binding['org_id']):
                    state, reason = 'STALE','WALLET_BINDING_CHANGED'
            except SLHError as exc:
                state, reason = 'STALE', exc.code
            # This is an observation, not a spending authorization; P4 must
            # fetch and debit atomically from the authoritative Hub.
            balance = None
            if observation:
                balance = {key: observation[key] for key in (
                    'available_credit_wei','held_credit_wei', 'spent_credit_wei',
                    'bridge_completed_credit_wei','hub_earned_credit_wei',
                    'observed_at','proof_digest')}
                balance['available_credits_display'] = credit_wei_to_decimal_text(int(observation['available_credit_wei']))
                balance['held_credits_display'] = credit_wei_to_decimal_text(int(observation['held_credit_wei']))
                balance['hub_earned_credits_display'] = (
                    credit_wei_to_decimal_text(int(observation['hub_earned_credit_wei']))
                    if observation['hub_earned_credit_wei'] is not None else None)
                balance['unclaimed_worker_earnings'] = 'NOT_PROVEN_BY_BALANCE_ENDPOINT'
                balance['pending_payouts'] = 'NOT_EXPOSED_BY_BALANCE_ENDPOINT'
            return {'schema':'main-computer.slh-org-wallet.v1', 'state':state,
                    'reason': reason if state!='CURRENT' else None,
                    'spendable_authorized':False,  # read-only observation, never a debit grant
                    'binding':public, 'balance':balance}


class _DBContext:
    def __init__(self, db):
        self.db = db
    def __enter__(self):
        self.db.execute('BEGIN IMMEDIATE')
        return self.db
    def __exit__(self, typ, exc, tb):
        try:
            if typ is None:
                self.db.commit()
            else:
                self.db.rollback()
        finally:
            self.db.close()
