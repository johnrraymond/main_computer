"""P3: fixed-per-request Pinky Promise liabilities, not credit payments.

The obligation table shares the PrivateRequests SQLite transaction with the
SUCCEEDED terminal transition.  No Hub balance is read or debited here and no
Reserve receipt or financial finality is claimed.
"""
from __future__ import annotations

import hashlib
import json
import time

from main_computer.credit_units import CREDIT_WEI_PER_CREDIT, credit_wei_to_decimal_text
from .authority import SLHError

LEGACY_POLICY_ID = 'fixed_per_request_v1'
POLICY_ID = 'local_reference_20_percent_v2'
REFERENCE_WEI = CREDIT_WEI_PER_CREDIT
ASSESSMENT_WEI = CREDIT_WEI_PER_CREDIT // 5
LEGACY_ASSESSMENT_WEI = CREDIT_WEI_PER_CREDIT // 10


class PinkyPromise:
    def __init__(self):
        pass

    @staticmethod
    def _migrate_legacy(db):
        # Migration is performed by the PrivateRequests database transaction.
        # Stable obligation_id and sweep_id values are NOT recalculated.
        for table in ('pinky_policy','pinky_policy_history','pinky_obligations'):
            if db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone() is None:
                continue
            columns = list(db.execute('PRAGMA table_info('+table+')'))
            if 'org_id' not in {row['name'] for row in columns}:
                continue
            if db.execute('SELECT COUNT(DISTINCT org_id) n FROM '+table).fetchone()['n'] > 1:
                raise SLHError('LEGACY_PINKY_LEDGER_SCOPE_CONFLICT',409)
            names = [row['name'] for row in columns if row['name'] != 'org_id']
            fields=[]
            for row in columns:
                if row['name']=='org_id':continue
                field='"'+row['name']+'" '+row['type']
                if row['notnull']:field+=' NOT NULL'
                if row['dflt_value'] is not None:field+=' DEFAULT '+row['dflt_value']
                fields.append(field)
            if table=='pinky_policy': fields.append('PRIMARY KEY (singleton)')
            elif table=='pinky_policy_history': fields.append('PRIMARY KEY (revision)')
            else:
                fields.append('PRIMARY KEY (obligation_id)')
                fields.append('UNIQUE (requester_device_id,request_id)')
            db.execute('CREATE TABLE '+table+'_no_scope ('+', '.join(fields)+')')
            cols=', '.join('"'+name+'"' for name in names)
            db.execute('INSERT INTO '+table+'_no_scope ('+cols+') SELECT '+cols+' FROM '+table)
            db.execute('DROP TABLE '+table)
            db.execute('ALTER TABLE '+table+'_no_scope RENAME TO '+table)

    def initialize(self, db):
        self._migrate_legacy(db)
        db.execute('''CREATE TABLE IF NOT EXISTS pinky_policy (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            revision INTEGER NOT NULL CHECK(revision >= 1),
            enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
            changed_at_ns INTEGER NOT NULL, changed_by TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS pinky_policy_history (
            revision INTEGER NOT NULL,
            enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
            changed_at_ns INTEGER NOT NULL, changed_by TEXT NOT NULL,
            PRIMARY KEY(revision))''')
        db.execute('''CREATE TABLE IF NOT EXISTS pinky_obligations (
            obligation_id TEXT PRIMARY KEY,
            requester_device_id TEXT NOT NULL,
            request_id TEXT NOT NULL, run_id TEXT NOT NULL,
            model TEXT NOT NULL, result_hash TEXT NOT NULL,
            policy_id TEXT NOT NULL, policy_revision INTEGER NOT NULL,
            reference_credit_wei TEXT NOT NULL, amount_credit_wei TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status='ASSESSED'),
            assessed_at_ns INTEGER NOT NULL,
            UNIQUE(requester_device_id,request_id))''')
        row = db.execute('SELECT revision,enabled FROM pinky_policy WHERE singleton=1').fetchone()
        if row is None:
            # A previously initialized policy must never be silently reset to
            # enabled after deletion/corruption of the singleton row.
            old = db.execute('SELECT COUNT(*) AS n FROM pinky_policy_history').fetchone()['n']
            if old:
                raise SLHError('PINKY_POLICY_HISTORY_MISMATCH',503)
            now = time.time_ns()
            db.execute('INSERT INTO pinky_policy VALUES(1,?,?,?,?)',
                       (1,1,now,'system-default'))
            db.execute('INSERT INTO pinky_policy_history VALUES(?,?,?,?)',
                       (1,1,now,'system-default'))
        else:
            expected = db.execute('''SELECT enabled FROM pinky_policy_history
                WHERE revision=?''',
                (row['revision'],)).fetchone()
            if expected is None or expected['enabled'] != row['enabled']:
                raise SLHError('PINKY_POLICY_HISTORY_MISMATCH',503)

    def snapshot(self, db):
        row = db.execute('SELECT * FROM pinky_policy WHERE singleton=1').fetchone()
        if row is None:
            raise SLHError('PINKY_POLICY_UNAVAILABLE',503)
        return {'schema':'main-computer.slh-pinky-policy.v1',
                'policy_id':POLICY_ID,'assessment_percent':20,
                'pricing_basis':'selected_worker_market_price',
                'revision':row['revision'],'enabled':bool(row['enabled']),
                'reference_credit_wei':str(REFERENCE_WEI),
                'assessment_credit_wei':str(ASSESSMENT_WEI),
                'assessment_credits':credit_wei_to_decimal_text(ASSESSMENT_WEI),
                'changed_at_ns':row['changed_at_ns'], 'changed_by':row['changed_by']}

    def update(self, db, body, actor):
        if not isinstance(body,dict) or set(body) != {'enabled','expected_revision','acknowledge_consequences'}:
            raise SLHError('INVALID_PINKY_POLICY_REQUEST',400)
        enabled, revision, acknowledged = (body[k] for k in ('enabled','expected_revision','acknowledge_consequences'))
        if type(enabled) is not bool or type(revision) is not int or revision < 1 or type(acknowledged) is not bool:
            raise SLHError('INVALID_PINKY_POLICY_REQUEST',400)
        current = self.snapshot(db)
        if current['revision'] != revision:
            raise SLHError('PINKY_POLICY_REVISION_CONFLICT',409)
        if not enabled and not acknowledged:
            raise SLHError('PINKY_DISABLE_ACK_REQUIRED',409)
        if enabled == current['enabled']:
            return current
        new_revision = revision + 1
        now=time.time_ns()
        db.execute('UPDATE pinky_policy SET enabled=?,revision=?,changed_at_ns=?,changed_by=? WHERE singleton=1',
                   (int(enabled),new_revision,now,actor))
        db.execute('INSERT INTO pinky_policy_history VALUES(?,?,?,?)',
                   (new_revision,int(enabled),now,actor))
        return self.snapshot(db)

    @staticmethod
    def obligation_id(requester, request_id):
        canonical = json.dumps([requester,request_id],separators=(',',':'),ensure_ascii=True)
        return 'pinky:v2:' + hashlib.sha256(canonical.encode('utf-8')).hexdigest()

    def record(self, db, row, result_hash, now_ns):
        """Insert in the SAME SQLite transaction as the success transition."""
        if not row['pinky_enabled']:
            raise SLHError('PINKY_ASSESSMENT_INVARIANT',500)
        if row['billing_policy_id'] not in (POLICY_ID, LEGACY_POLICY_ID) or row['pinky_policy_revision'] < 1:
            raise SLHError('PINKY_ASSESSMENT_INVARIANT',500)
        if not row['run_id'] or not result_hash or len(result_hash)!=64:
            raise SLHError('PINKY_FULFILLMENT_PROOF_REQUIRED',500)
        oid = self.obligation_id(row['requester_device_id'],row['request_id'])
        if row['billing_policy_id']==LEGACY_POLICY_ID:
            reference,amount=REFERENCE_WEI,LEGACY_ASSESSMENT_WEI
        else:
            if (row['reference_credit_wei'] is None or row['assessment_credit_wei'] is None):
                raise SLHError('PINKY_MISSING_PRICE_QUOTE',500)
            reference,amount=int(row['reference_credit_wei']),int(row['assessment_credit_wei'])
            if reference<=0 or amount!=(reference+4)//5:
                raise SLHError('PINKY_ASSESSMENT_INVARIANT',500)
        db.execute('''INSERT INTO pinky_obligations
            (obligation_id,requester_device_id,request_id,run_id,model,
             result_hash,policy_id,policy_revision,reference_credit_wei,
             amount_credit_wei,status,assessed_at_ns)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
            (oid,row['requester_device_id'],row['request_id'],row['run_id'],
             row['model'],result_hash,POLICY_ID,row['pinky_policy_revision'],
             str(reference),str(amount),'ASSESSED',now_ns))

    def obligations(self, db):
        rows = db.execute('''SELECT obligation_id,request_id,requester_device_id,run_id,
                model,result_hash,policy_id,policy_revision,reference_credit_wei,
                amount_credit_wei,status,assessed_at_ns
                FROM pinky_obligations ORDER BY assessed_at_ns,obligation_id LIMIT 100''').fetchall()
        # Assessments never imply payment; P4 provides a separate receipt-backed sweep journal.
        count=db.execute('SELECT COUNT(*) AS n FROM pinky_obligations').fetchone()['n']
        should_be = db.execute('''SELECT COUNT(*) AS n FROM private_requests
            WHERE status='SUCCEEDED' AND pinky_enabled=1''').fetchone()['n']
        if count != should_be:
            raise SLHError('PINKY_LEDGER_INCONSISTENT',500)
        has_sweeps=db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pinky_sweeps'").fetchone() is not None
        paid=0
        if has_sweeps:
            settled=db.execute('''SELECT o.amount_credit_wei FROM pinky_obligations o
                JOIN pinky_sweep_items i ON o.obligation_id=i.obligation_id
                JOIN pinky_sweeps s ON i.sweep_id=s.sweep_id
                WHERE s.status='CONFIRMED' ''').fetchall()
            paid=len(settled)
            paid_amount=sum(int(r['amount_credit_wei']) for r in settled)
        else:
            paid_amount=0
        total=sum(int(r['amount_credit_wei']) for r in db.execute(
            'SELECT amount_credit_wei FROM pinky_obligations'))
        return {'schema':'main-computer.slh-pinky-obligations.v1','paid_count':paid,
                'paid_total_credit_wei':str(paid_amount),
                'outstanding_credit_wei':str(total-paid_amount),
                'count':count,
                'assessed_total_credit_wei':str(total),
                'assessed_total_credits':credit_wei_to_decimal_text(total),
                'settlement_state':'P4_PERIODIC_SWEEP' if has_sweeps else 'NOT_IMPLEMENTED_P4',
                'returned':len(rows),'has_more':count > len(rows),
                'obligations':[dict(row) for row in rows]}
