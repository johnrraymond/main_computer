"""P4 periodic aggregate Pinky Promise sweep, backed by the existing Hub ledger.

No per-request payment: one pending batch is retried until authoritative
confirmation. The organizational signing key is read only from an explicit
machine-local administrator-provisioned path; Worker/MSK credentials never
sign spending instructions. Missing setup means no transfer, not no debt.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid

from main_computer.credit_units import credit_wei_to_decimal_text
from main_computer.hub_credit_models import normalize_address
from main_computer.multisession_key_signing import private_key_to_address, sign_personal_message
from main_computer.pinky_reserve_transfer import SWEEP_SCHEMA, canonical_sweep_intent
from .authority import SLHError
from .organization_wallet import HubCreditReader
from .pinky_promise import ASSESSMENT_WEI

RESERVE_ENV = 'MAIN_COMPUTER_RESERVE_WALLET_ADDRESS'
SIGNER_ENV = 'SLH_PINKY_SWEEP_SIGNER_FILE'
INTERVAL_SECONDS = 300


class PinkySweep:
    def __init__(self, private, wallet, *, reader_factory=HubCreditReader, interval=INTERVAL_SECONDS):
        self.private=private
        self.wallet=wallet
        self.reader_factory=reader_factory
        self.interval=interval
        self._lock=threading.RLock()
        self._stop=threading.Event()
        self._thread=None
        self.last_attempt_ns=None
        self.last_error=None
        with self.private._lock, self.private._db() as db:
            # An uncertain pending legacy payment cannot be safely retried with
            # a different canonical signed message. Refuse before any migration.
            legacy = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pinky_sweeps'").fetchone()
            if legacy is not None:
                fields = {row['name'] for row in db.execute('PRAGMA table_info(pinky_sweeps)')}
                if 'org_id' in fields:
                    if db.execute("SELECT 1 FROM pinky_sweeps WHERE status='PENDING' LIMIT 1").fetchone():
                        raise SLHError('LEGACY_PENDING_SWEEP_REQUIRES_RECONCILIATION',409)
                    if db.execute('SELECT COUNT(DISTINCT org_id) n FROM pinky_sweeps').fetchone()['n'] > 1:
                        raise SLHError('LEGACY_SWEEP_SCOPE_CONFLICT',409)
                    db.execute('ALTER TABLE pinky_sweeps DROP COLUMN org_id')
            db.execute('''CREATE TABLE IF NOT EXISTS pinky_sweeps (
                sweep_id TEXT PRIMARY KEY,
                wallet_address TEXT NOT NULL, reserve_wallet_address TEXT NOT NULL,
                cluster_id TEXT NOT NULL, chain_id TEXT NOT NULL,
                credit_wei TEXT NOT NULL, status TEXT NOT NULL
                CHECK(status IN ('PENDING','CONFIRMED')),
                created_at_ns INTEGER NOT NULL, confirmed_at_ns INTEGER,
                receipt_id TEXT)''')
            db.execute('''CREATE TABLE IF NOT EXISTS pinky_sweep_items (
                obligation_id TEXT PRIMARY KEY REFERENCES pinky_obligations(obligation_id),
                sweep_id TEXT NOT NULL REFERENCES pinky_sweeps(sweep_id))''')
            db.execute('CREATE INDEX IF NOT EXISTS pinky_sweep_status ON pinky_sweeps(status)')

    def start(self):
        if self._thread is None:
            self._thread=threading.Thread(target=self._loop,name='slh-pinky-periodic-sweep',daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=6)
            self._thread=None

    def _loop(self):
        # One sweep immediately on service start, then every five minutes.
        while not self._stop.is_set():
            self.last_attempt_ns=time.time_ns()
            try:
                self.sweep()
                self.last_error=None
            except (OSError,ValueError,UnicodeError,sqlite3.Error,SLHError) as exc:
                self.last_error=exc.code if isinstance(exc,SLHError) else type(exc).__name__
                # Pending debt is durable; next periodic attempt retries.
            if self._stop.wait(self.interval):
                break

    def _authority(self):
        reserve=os.environ.get(RESERVE_ENV,'').strip().lower()
        signer_path=os.environ.get(SIGNER_ENV,'').strip()
        if (not re.fullmatch(r'0x[0-9a-f]{40}',reserve) or
                reserve=='0x'+'0'*40 or not signer_path):
            raise SLHError('PINKY_SWEEP_CONFIGURATION_REQUIRED',409)
        path=Path(signer_path)
        if path.is_symlink() or not path.is_file():
            raise SLHError('PINKY_SWEEP_SIGNER_UNAVAILABLE',503)
        if os.name=='posix' and path.stat().st_mode & 0o077:
            raise SLHError('PINKY_SWEEP_SIGNER_PERMISSIONS',503)
        secret=path.read_text(encoding='ascii').strip()
        if len(secret) not in (64,66):
            raise SLHError('PINKY_SWEEP_SIGNER_INVALID',503)
        try:
            signer=normalize_address(private_key_to_address(secret))
        except (ValueError,TypeError) as exc:
            raise SLHError('PINKY_SWEEP_SIGNER_INVALID',503) from exc
        if signer==reserve:
            raise SLHError('PINKY_SWEEP_RECIPIENT_CONFLICT',409)
        return reserve, signer,secret

    def status(self):
        with self.private._lock,self.private._db() as db:
            count=db.execute("SELECT COUNT(*) n FROM pinky_obligations").fetchone()['n']
            paid=db.execute('''SELECT COUNT(*) n FROM pinky_sweep_items i
                JOIN pinky_sweeps s ON i.sweep_id=s.sweep_id
                WHERE s.status='CONFIRMED' ''').fetchone()['n']
            pending=db.execute("SELECT COUNT(*) n FROM pinky_sweeps WHERE status='PENDING'").fetchone()['n']
            outstanding=sum(int(row['amount_credit_wei']) for row in db.execute('''
                SELECT o.amount_credit_wei FROM pinky_obligations o
                LEFT JOIN pinky_sweep_items i ON i.obligation_id=o.obligation_id
                LEFT JOIN pinky_sweeps s ON s.sweep_id=i.sweep_id
                WHERE s.status IS NULL OR s.status!='CONFIRMED' '''))
            return {'schema':'main-computer.slh-pinky-sweeps.v1',
                    'assessed_count':count,
                    'paid_count':paid,'outstanding_count':count-paid,
                    'outstanding_credit_wei':str(outstanding),
                    'outstanding_credits':credit_wei_to_decimal_text(outstanding),
                    'pending_sweeps':pending,
                    'last_confirmed':self._last_confirmed(db),
                    'interval_seconds':self.interval,
                    'last_attempt_ns':self.last_attempt_ns,'last_error':self.last_error}

    def _last_confirmed(self,db):
        row=db.execute('''SELECT sweep_id,credit_wei,receipt_id,confirmed_at_ns
            FROM pinky_sweeps WHERE status='CONFIRMED'
            ORDER BY confirmed_at_ns DESC LIMIT 1''').fetchone()
        return dict(row) if row else None

    def sweep(self):
        with self._lock:
            reserve, signer,secret=self._authority()
            # Hub is authoritative; no local snapshot grants spending rights.
            current=self.wallet.reconcile()
            if current['state']!='CURRENT' or not current['binding'] or not current['balance']:
                raise SLHError('PINKY_WALLET_NOT_CURRENT',503)
            binding=current['binding']
            if binding['wallet_address']!=signer:
                raise SLHError('PINKY_SIGNER_WALLET_MISMATCH',409)
            available=int(current['balance']['available_credit_wei'])
            with self.private._lock,self.private._db() as db:
                prepared=db.execute("SELECT * FROM pinky_sweeps WHERE status='PENDING' ORDER BY created_at_ns LIMIT 1").fetchone()
                if prepared is None:
                    # Preserve historic 0.1-credit obligations and new 0.2-
                    # credit obligations by summing each persisted amount.
                    candidates=db.execute('''
                        SELECT o.obligation_id,o.amount_credit_wei FROM pinky_obligations o
                        LEFT JOIN pinky_sweep_items i ON i.obligation_id=o.obligation_id
                        WHERE i.obligation_id IS NULL
                        ORDER BY o.assessed_at_ns,o.obligation_id LIMIT 1000''').fetchall()
                    ids=[]
                    credit_wei=0
                    for row in candidates:
                        charge=int(row['amount_credit_wei'])
                        if credit_wei+charge>available:break
                        ids.append(row['obligation_id'])
                        credit_wei+=charge
                    if not ids:
                        return {'status':'INSUFFICIENT_CREDITS' if candidates else 'NOTHING_DUE',
                                **self.status()}
                    sweep_id='psweep-'+uuid.uuid4().hex
                    db.execute('''INSERT INTO pinky_sweeps
                        (sweep_id,wallet_address,reserve_wallet_address,cluster_id,chain_id,
                         credit_wei,status,created_at_ns) VALUES (?,?,?,?,?,?,?,?)''',
                        (sweep_id,signer,reserve,binding['cluster_id'],
                         binding['chain_id'],str(credit_wei),'PENDING',time.time_ns()))
                    db.executemany('INSERT INTO pinky_sweep_items VALUES (?,?)',[(oid,sweep_id) for oid in ids])
                    prepared=db.execute('SELECT * FROM pinky_sweeps WHERE sweep_id=?',(sweep_id,)).fetchone()
                # Never redirect or resize a pending batch on reconfiguration.
                if (prepared['wallet_address']!=signer or prepared['reserve_wallet_address']!=reserve or
                    prepared['cluster_id']!=binding['cluster_id'] or prepared['chain_id']!=binding['chain_id']):
                    raise SLHError('PINKY_PENDING_SWEEP_BINDING_MISMATCH',409)
                sweep=dict(prepared)
            intent={'schema':SWEEP_SCHEMA,
                    'sweep_id':sweep['sweep_id'],'cluster_id':sweep['cluster_id'],
                    'chain_id':sweep['chain_id'],'wallet_address':signer,
                    'reserve_wallet_address':reserve,'credit_wei':sweep['credit_wei']}
            sig=sign_personal_message(canonical_sweep_intent(intent),secret)
            reader=self.reader_factory(binding['hub_url'],binding.get('tls_sha256'))
            receipt=reader.request('POST','/api/hub/v1/credits/pinky-reserve/sweep',
                                   {'intent':intent,'signature':sig})
            for key,value in (('sweep_id',sweep['sweep_id']),
                              ('wallet_address',signer),('reserve_wallet_address',reserve),
                              ('cluster_id',sweep['cluster_id']),('chain_id',sweep['chain_id']),
                              ('credit_wei',sweep['credit_wei'])):
                if receipt.get(key)!=value:
                    raise SLHError('PINKY_SWEEP_RECEIPT_MISMATCH',502)
            if (receipt.get('ok') is not True or receipt.get('status')!='confirmed' or
                receipt.get('settlement_rail')!='hub_internal_credit_transfer' or
                not isinstance(receipt.get('receipt_id'),str) or not receipt['receipt_id']):
                raise SLHError('PINKY_SWEEP_NOT_CONFIRMED',503)
            with self.private._lock,self.private._db() as db:
                existing=db.execute('SELECT status,receipt_id FROM pinky_sweeps WHERE sweep_id=?',(sweep['sweep_id'],)).fetchone()
                if existing['status']=='CONFIRMED':
                    if existing['receipt_id']!=receipt['receipt_id']:
                        raise SLHError('PINKY_RECEIPT_CONFLICT',409)
                else:
                    db.execute("UPDATE pinky_sweeps SET status='CONFIRMED',receipt_id=?,confirmed_at_ns=? WHERE sweep_id=? AND status='PENDING'",
                               (receipt['receipt_id'],time.time_ns(),sweep['sweep_id']))
            return {'status':'CONFIRMED','receipt_id':receipt['receipt_id'],
                    'sweep_id':sweep['sweep_id'],'credit_wei':sweep['credit_wei']}
