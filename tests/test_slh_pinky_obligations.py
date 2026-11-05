"""P3: immutable assessment, administrator opt-out, and zero Hub spending."""
import threading
import unittest
import sqlite3
from unittest.mock import patch

from main_computer.credit_units import CREDIT_WEI_PER_CREDIT
from main_computer.slh.authority import SLHError
from main_computer.slh.authenticator import sign_object
from main_computer.slh.private_requests import PrivateRequests
from main_computer.slh.pinky_promise import ASSESSMENT_WEI, POLICY_ID, PinkyPromise


class TestPinkyObligations(unittest.TestCase):
    def setUp(self):
        from tests.test_slh_private_requests import TestPrivateRequests as _Fixture
        f=_Fixture('test_real_private_worker_success_and_metadata_only_durable_replay')
        f.setUp()
        self.addCleanup(f.doCleanups)
        self.f=f
        self.requester=f.enroll('req-1','requester')

    def admin(self):
        challenge=self.f.auth.challenge('admin-device')
        return self.f.auth.verify_login({'device_id':'admin-device',
            'challenge_id':challenge['challenge_id'],
            'signature':sign_object(self.f.admin_key,challenge)})['access_token']

    def ledger(self):
        return self.f.private.obligations()

    def test_success_fixed_fraction_same_transaction_and_replay(self):
        self.assertEqual(ASSESSMENT_WEI, CREDIT_WEI_PER_CREDIT // 5)
        self.f.worker()
        status, result=self.f.submit(self.requester,self.f.req('one'))
        self.assertEqual(status,200,result)
        self.assertEqual(result['status'],'SUCCEEDED')
        entries=self.ledger()
        self.assertEqual(entries['count'],1)
        self.assertEqual(entries['assessed_total_credit_wei'],str(ASSESSMENT_WEI))
        self.assertEqual(entries['assessed_total_credits'],'0.2')
        self.assertEqual(entries['settlement_state'],'NOT_IMPLEMENTED_P4')
        item=entries['obligations'][0]
        self.assertEqual(item['reference_credit_wei'],str(CREDIT_WEI_PER_CREDIT))
        self.assertEqual(item['amount_credit_wei'],str(ASSESSMENT_WEI))
        self.assertEqual(item['result_hash'],result['result_hash'])
        self.assertEqual(item['policy_id'],POLICY_ID)
        self.assertEqual(item['policy_revision'],1)
        self.assertEqual(item['status'],'ASSESSED')
        self.assertEqual(item['obligation_id'],PinkyPromise.obligation_id('req-1','one'))
        self.assertEqual(self.f.submit(self.requester,self.f.req('one'))[1]['status'],'SUCCEEDED')
        after=PrivateRequests(self.f.root/'state',self.f.auth,self.f.gateway)
        self.assertEqual(after.obligations(),entries)
        raw=after.db_path.read_bytes().decode('latin1')
        self.assertNotIn('five words',raw)
        self.assertNotIn('computed 10',raw)

    def test_two_requests_same_request_id_different_requesters_are_separate(self):
        self.f.enroll('req-2','requester')
        with self.f.private._db() as db:
            for requester in ('req-1','req-2'):
                db.execute('''INSERT INTO private_requests
                    (requester_device_id,request_id,request_digest,model,status,
                     run_id,admitted_at_ns,updated_at_ns,billing_policy_id,pinky_policy_revision,pinky_enabled,
                     reference_credit_wei,assessment_credit_wei)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (requester,'same','digest','mock-model','DISPATCHED',
                     'run-'+requester,1,1,POLICY_ID,1,1,str(10**18),str(2*10**17)))
        for requester in ('req-1','req-2'):
            self.f.private._complete_success(requester,'same','a'*64)
        self.assertEqual(self.ledger()['count'],2)
        self.assertEqual(self.ledger()['assessed_total_credits'],'0.4')
        self.assertEqual(len({v['obligation_id'] for v in self.ledger()['obligations']}),2)

    def test_admin_policy_disables_future_eligible_requests_only(self):
        self.f.worker()
        admin=self.admin()
        uri='/v1/admin/pinky-promise/policy'
        status,policy=self.f.api.call('GET',uri,token=admin)
        self.assertEqual(status,200)
        self.assertTrue(policy['enabled'])
        self.assertEqual(policy['revision'],1)
        self.assertEqual(self.f.api.call('GET',uri,token=self.requester)[0],403)
        self.assertEqual(self.f.api.call('PATCH',uri,{'enabled':False,'expected_revision':1,
            'acknowledge_consequences':False},token=admin)[1]['error']['code'],'PINKY_DISABLE_ACK_REQUIRED')
        self.assertEqual(self.f.submit(self.requester,self.f.req('before'))[1]['status'],'SUCCEEDED')
        payload={'enabled':False,'expected_revision':1,'acknowledge_consequences':True}
        status,changed=self.f.api.call('PATCH',uri,payload,token=admin)
        self.assertEqual(status,200,changed)
        self.assertEqual(changed['revision'],2)
        self.assertFalse(changed['enabled'])
        self.assertEqual(self.f.api.call('PATCH',uri,payload,token=admin)[1]['error']['code'],
                         'PINKY_POLICY_REVISION_CONFLICT')
        self.assertEqual(self.f.submit(self.requester,self.f.req('after'))[1]['failure_code'],
                         'LOCAL_ASSESSMENT_DISABLED')
        self.assertEqual(self.ledger()['count'],1)
        self.assertEqual(self.ledger()['obligations'][0]['request_id'],'before')
        after=PrivateRequests(self.f.root/'state',self.f.auth,self.f.gateway)
        self.assertFalse(after.policy()['enabled'])
        self.assertEqual(after.policy()['revision'],2)
        self.assertEqual(after.obligations()['count'],1)
        status,enabled=self.f.api.call('PATCH',uri,
            {'enabled':True,'expected_revision':2,'acknowledge_consequences':False},token=admin)
        self.assertEqual(status,200,enabled)
        self.assertEqual(enabled['revision'],3)
        self.assertEqual(self.f.submit(self.requester,self.f.req('new'))[1]['status'],'SUCCEEDED')
        self.assertEqual(self.ledger()['count'],2)
        self.assertEqual({v['policy_revision'] for v in self.ledger()['obligations']},{1,3})

    def test_in_flight_request_keeps_admission_policy(self):
        f=self.f
        self.f.worker()
        admin=self.admin()
        # Admissions capture the policy revision before execution; opt-out
        # does not erase a liability already committed or in flight.
        with f.private._lock, f.private._db() as db:
            p=f.private.pinky.snapshot(db)
            db.execute('''INSERT INTO private_requests
               (requester_device_id,request_id,request_digest,model,status,
                run_id,admitted_at_ns,updated_at_ns,billing_policy_id,pinky_policy_revision,pinky_enabled,
                reference_credit_wei,assessment_credit_wei)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
               ('req-1','in-flight','digest','mock-model','DISPATCHED',
                'run-1',1,1,POLICY_ID,p['revision'],int(p['enabled']),str(10**18),str(2*10**17)))
        self.f.api.call('PATCH','/v1/admin/pinky-promise/policy',
            {'enabled':False,'expected_revision':1,'acknowledge_consequences':True},token=admin)
        f.private._complete_success('req-1','in-flight','a'*64)
        self.assertEqual(self.ledger()['count'],1)
        self.assertEqual(self.ledger()['obligations'][0]['policy_revision'],1)

    def test_failure_unknown_and_invalid_work_no_assessment(self):
        self.assertEqual(self.f.submit(self.requester,self.f.req('unavailable'))[1]['status'],'FAILED')
        self.assertEqual(self.f.submit(self.requester,{'bogus':'request'})[0],400)
        with self.f.private._db() as db:
            db.execute('''INSERT INTO private_requests
               (requester_device_id,request_id,request_digest,model,status,
                run_id,admitted_at_ns,updated_at_ns,billing_policy_id,pinky_policy_revision,pinky_enabled,
                reference_credit_wei,assessment_credit_wei)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
               ('req-1','unknown','digest','mock-model','DISPATCHED',
                'run',1,1,POLICY_ID,1,1,str(10**18),str(2*10**17)))
        recovered=PrivateRequests(self.f.root/'state',self.f.auth,self.f.gateway)
        self.assertEqual(recovered.get({'role':'requester','device_id':'req-1'},'unknown')['status'],'OUTCOME_UNKNOWN')
        self.assertEqual(recovered.obligations()['count'],0)

    def test_atomic_rollback_if_assessment_insert_fails(self):
        with self.f.private._db() as db:
            db.execute('''INSERT INTO private_requests
               (requester_device_id,request_id,request_digest,model,status,
                run_id,admitted_at_ns,updated_at_ns,billing_policy_id,pinky_policy_revision,pinky_enabled,
                reference_credit_wei,assessment_credit_wei)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
               ('req-1','rollback','digest','mock-model','DISPATCHED',
                'run',1,1,POLICY_ID,1,1,str(10**18),str(2*10**17)))
        with patch.object(self.f.private.pinky,'record',side_effect=RuntimeError('failed write')):
            with self.assertRaises(RuntimeError):
                self.f.private._complete_success('req-1','rollback','a'*64)
        self.assertEqual(self.ledger()['count'],0)
        self.assertEqual(self.f.private.get({'role':'requester','device_id':'req-1'},'rollback')['status'],
                         'DISPATCHED')
        recovered=PrivateRequests(self.f.root/'state',self.f.auth,self.f.gateway)
        self.assertEqual(recovered.get({'role':'requester','device_id':'req-1'},'rollback')['status'],
                         'OUTCOME_UNKNOWN')
        self.assertEqual(recovered.obligations()['count'],0)

    def test_legacy_p2_success_does_not_gain_retroactive_assessment(self):
        with self.f.private._db() as db:
            db.execute('''INSERT INTO private_requests
                (requester_device_id,request_id,request_digest,model,status,
                 run_id,admitted_at_ns,updated_at_ns,fulfilled_at_ns,result_hash)
                VALUES (?,?,?,?,?,?,?,?,?,?)''',
                ('req-1','legacy','d','mock-model','SUCCEEDED','run',1,1,1,'b'*64))
        reopened=PrivateRequests(self.f.root/'state',self.f.auth,self.f.gateway)
        self.assertEqual(reopened.get({'role':'requester','device_id':'req-1'},'legacy')['billing_policy_id'],
                         'p2-pre-assessment')
        self.assertEqual(reopened.obligations()['count'],0)

    def test_actual_p2_schema_upgrade_does_not_retroactively_charge(self):
        legacy=self.f.root/'legacy-p2'
        legacy.mkdir()
        with sqlite3.connect(legacy/'private_requests.sqlite3') as db:
            db.execute('''CREATE TABLE private_requests (
                org_id TEXT NOT NULL, requester_device_id TEXT NOT NULL,
                request_id TEXT NOT NULL, request_digest TEXT NOT NULL,
                model TEXT NOT NULL,status TEXT NOT NULL,
                worker_device_id TEXT,run_id TEXT,attempt INTEGER NOT NULL DEFAULT 0,
                admitted_at_ns INTEGER NOT NULL,updated_at_ns INTEGER NOT NULL,
                fulfilled_at_ns INTEGER,result_hash TEXT,failure_code TEXT,
                billing_policy_id TEXT NOT NULL DEFAULT 'p2-pre-assessment',
                PRIMARY KEY (org_id,requester_device_id,request_id))''')
            db.execute('''INSERT INTO private_requests
                (org_id,requester_device_id,request_id,request_digest,model,status,
                 admitted_at_ns,updated_at_ns,fulfilled_at_ns,result_hash)
                VALUES (?,?,?,?,?,?,?,?,?,?)''',
                ('private-lab','req-1','old-success','d','mock-model','SUCCEEDED',1,1,1,'a'*64))
        migrated=PrivateRequests(legacy,self.f.auth,self.f.gateway)
        self.assertEqual(migrated.obligations()['count'],0)
        self.assertEqual(migrated.get({'role':'requester','device_id':'req-1'},'old-success')['status'],
                         'SUCCEEDED')
        self.assertEqual(migrated.policy()['assessment_credits'],'0.2')

    def test_missing_policy_row_cannot_silently_restore_default(self):
        with self.f.private._db() as db:
            db.execute('DELETE FROM pinky_policy WHERE singleton=1')
        with self.assertRaises(SLHError) as raised:
            PrivateRequests(self.f.root/'state',self.f.auth,self.f.gateway)
        self.assertEqual(raised.exception.code,'PINKY_POLICY_HISTORY_MISMATCH')

    def test_admin_read_only_ledger_api_and_no_public_access(self):
        self.f.worker()
        self.assertEqual(self.f.submit(self.requester,self.f.req('report'))[1]['status'],'SUCCEEDED')
        path='/v1/accounting/obligations'
        self.assertEqual(self.f.api.call('GET',path,token='')[0],401)
        self.assertEqual(self.f.api.call('GET',path,token=self.requester)[0],403)
        status,body=self.f.api.call('GET',path,token=self.admin())
        self.assertEqual(status,200,body)
        self.assertEqual(body['count'],1)
        self.assertEqual(self.f.api.call('POST',path,token=self.admin())[0],404)
        self.assertEqual(self.f.api.call('GET','/openapi.json')[1]['paths'][path]['get']['x-implementation'],
                         'implemented')
        from main_computer.slh.api import SLHAPI
        self.assertEqual(SLHAPI(self.f.auth).call('GET',path,token=self.admin())[0],501)

    def test_admin_ledger_endpoint_over_real_organization_tls(self):
        from tests.test_slh_private_execution_integration import TestPrivateExecutionNetwork
        net=TestPrivateExecutionNetwork('test_real_tls_private_request_and_access_control')
        net.setUp()
        self.addCleanup(net.doCleanups)
        f=net.fixture
        requester=f.enroll('req-1','requester')
        f.worker()
        self.assertEqual(net.call('POST','/v1/work',requester,f.req('net-p3'))[1]['status'],'SUCCEEDED')
        challenge=f.auth.challenge('admin-device')
        token=f.auth.verify_login({'device_id':'admin-device',
            'challenge_id':challenge['challenge_id'],
            'signature':sign_object(f.admin_key,challenge)})['access_token']
        status,ledger=net.call('GET','/v1/accounting/obligations',token)
        self.assertEqual(status,200,ledger)
        self.assertEqual(ledger['assessed_total_credits'],'0.2')
        self.assertEqual(net.call('GET','/v1/accounting/obligations',requester)[0],403)

    def test_concurrent_duplicate_submit_one_liability(self):
        f=self.f
        self.f.worker()
        records=[]
        threads=[threading.Thread(target=lambda:records.append(f.submit(self.requester,f.req('parallel'))))
                 for _ in range(5)]
        for t in threads:t.start()
        for t in threads:t.join(12)
        self.assertEqual(len(records),5)
        self.assertEqual(self.ledger()['count'],1)
        self.assertEqual(self.ledger()['assessed_total_credits'],'0.2')

if __name__=='__main__':unittest.main()
