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

POLICY_ID = 'fixed_per_request_v1'
REFERENCE_WEI = CREDIT_WEI_PER_CREDIT
ASSESSMENT_WEI = CREDIT_WEI_PER_CREDIT // 10


class PinkyPromise:
    def __init__(self, org_id: str):
        self.org_id = org_id

    def initialize(self, db):
        db.execute('''CREATE TABLE IF NOT EXISTS pinky_policy (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            org_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision >= 1),
            enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
            changed_at_ns INTEGER NOT NULL, changed_by TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS pinky_policy_history (
            org_id TEXT NOT NULL, revision INTEGER NOT NULL,
            enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
            changed_at_ns INTEGER NOT NULL, changed_by TEXT NOT NULL,
            PRIMARY KEY(org_id,revision))''')
        db.execute('''CREATE TABLE IF NOT EXISTS pinky_obligations (
            obligation_id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL, requester_device_id TEXT NOT NULL,
            request_id TEXT NOT NULL, run_id TEXT NOT NULL,
            model TEXT NOT NULL, result_hash TEXT NOT NULL,
            policy_id TEXT NOT NULL, policy_revision INTEGER NOT NULL,
            reference_credit_wei TEXT NOT NULL, amount_credit_wei TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status='ASSESSED'),
            assessed_at_ns INTEGER NOT NULL,
            UNIQUE(org_id,requester_device_id,request_id))''')
        row = db.execute('SELECT org_id,revision,enabled FROM pinky_policy WHERE singleton=1').fetchone()
        if row is None:
            # A previously initialized policy must never be silently reset to
            # enabled after deletion/corruption of the singleton row.
            old = db.execute('SELECT COUNT(*) AS n FROM pinky_policy_history').fetchone()['n']
            if old:
                raise SLHError('PINKY_POLICY_HISTORY_MISMATCH',503)
            now = time.time_ns()
            db.execute('INSERT INTO pinky_policy VALUES(1,?,?,?,?,?)',
                       (self.org_id,1,1,now,'system-default'))
            db.execute('INSERT INTO pinky_policy_history VALUES(?,?,?,?,?)',
                       (self.org_id,1,1,now,'system-default'))
        elif row['org_id'] != self.org_id:
            raise SLHError('PINKY_ORG_MISMATCH',409)
        else:
            expected = db.execute('''SELECT enabled FROM pinky_policy_history
                WHERE org_id=? AND revision=?''',
                (self.org_id,row['revision'])).fetchone()
            if expected is None or expected['enabled'] != row['enabled']:
                raise SLHError('PINKY_POLICY_HISTORY_MISMATCH',503)

    def snapshot(self, db):
        row = db.execute('SELECT * FROM pinky_policy WHERE singleton=1').fetchone()
        if row is None or row['org_id'] != self.org_id:
            raise SLHError('PINKY_POLICY_UNAVAILABLE',503)
        return {'schema':'main-computer.slh-pinky-policy.v1',
                'org_id':self.org_id,'policy_id':POLICY_ID,
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
        db.execute('UPDATE pinky_policy SET enabled=?,revision=?,changed_at_ns=?,changed_by=? WHERE singleton=1 AND org_id=?',
                   (int(enabled),new_revision,now,actor,self.org_id))
        db.execute('INSERT INTO pinky_policy_history VALUES(?,?,?,?,?)',
                   (self.org_id,new_revision,int(enabled),now,actor))
        return self.snapshot(db)

    @staticmethod
    def obligation_id(org_id, requester, request_id):
        canonical = json.dumps([org_id,requester,request_id],separators=(',',':'),ensure_ascii=True)
        return 'pinky:v1:' + hashlib.sha256(canonical.encode('utf-8')).hexdigest()

    def record(self, db, row, result_hash, now_ns):
        """Insert in the SAME SQLite transaction as the success transition."""
        if row['org_id'] != self.org_id or not row['pinky_enabled']:
            raise SLHError('PINKY_ASSESSMENT_INVARIANT',500)
        if row['billing_policy_id'] != POLICY_ID or row['pinky_policy_revision'] < 1:
            raise SLHError('PINKY_ASSESSMENT_INVARIANT',500)
        if not row['run_id'] or not result_hash or len(result_hash)!=64:
            raise SLHError('PINKY_FULFILLMENT_PROOF_REQUIRED',500)
        oid = self.obligation_id(row['org_id'],row['requester_device_id'],row['request_id'])
        db.execute('''INSERT INTO pinky_obligations
            (obligation_id,org_id,requester_device_id,request_id,run_id,model,
             result_hash,policy_id,policy_revision,reference_credit_wei,
             amount_credit_wei,status,assessed_at_ns)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (oid,self.org_id,row['requester_device_id'],row['request_id'],row['run_id'],
             row['model'],result_hash,POLICY_ID,row['pinky_policy_revision'],
             str(REFERENCE_WEI),str(ASSESSMENT_WEI),'ASSESSED',now_ns))

    def obligations(self, db):
        rows = db.execute('''SELECT obligation_id,request_id,requester_device_id,run_id,
                model,result_hash,policy_id,policy_revision,reference_credit_wei,
                amount_credit_wei,status,assessed_at_ns
                FROM pinky_obligations WHERE org_id=? ORDER BY assessed_at_ns,obligation_id LIMIT 100''',
                (self.org_id,)).fetchall()
        # Assessments never imply payment; P4 provides a separate receipt-backed sweep journal.
        count=db.execute('SELECT COUNT(*) AS n FROM pinky_obligations WHERE org_id=?',
                         (self.org_id,)).fetchone()['n']
        should_be = db.execute('''SELECT COUNT(*) AS n FROM private_requests
            WHERE org_id=? AND status='SUCCEEDED' AND pinky_enabled=1''',
            (self.org_id,)).fetchone()['n']
        if count != should_be:
            raise SLHError('PINKY_LEDGER_INCONSISTENT',500)
        has_sweeps=db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pinky_sweeps'").fetchone() is not None
        paid=0
        if has_sweeps:
            paid=db.execute('''SELECT COUNT(*) n FROM pinky_sweep_items i
                JOIN pinky_sweeps s ON i.sweep_id=s.sweep_id
                WHERE s.org_id=? AND s.status='CONFIRMED' ''',(self.org_id,)).fetchone()['n']
        return {'schema':'main-computer.slh-pinky-obligations.v1','paid_count':paid,
                'paid_total_credit_wei':str(paid*ASSESSMENT_WEI),
                'outstanding_credit_wei':str((count-paid)*ASSESSMENT_WEI),
                'org_id':self.org_id, 'count':count,
                'assessed_total_credit_wei':str(count * ASSESSMENT_WEI),
                'assessed_total_credits':credit_wei_to_decimal_text(count * ASSESSMENT_WEI),
                'settlement_state':'P4_PERIODIC_SWEEP' if has_sweeps else 'NOT_IMPLEMENTED_P4',
                'returned':len(rows),'has_more':count > len(rows),
                'obligations':[dict(row) for row in rows]}
