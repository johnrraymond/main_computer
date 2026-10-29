"""Policy, reservations, and session-drain acceptance tests for SLH scheduler."""
from __future__ import annotations
import json
from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace

from main_computer.slh.authority import SLHAuthority, SLHError
from main_computer.slh.authenticator import generate_identity, sign_object
from main_computer.slh.api import SLHAPI
from main_computer.slh.managed_gateway import ManagedScheduler, ManagedWorker, SLHMarketplaceProxy
from main_computer.slh.scheduler_policy import SchedulerPolicyStore, normalize_policy, compute_plan


class FakeSocket:
    def send(self, msg): pass


class FakeUpstream:
    def __init__(self, *, busy=False):
        self.is_alive=True
        self.has_active_work=busy
        self.closed=False
    def close(self, *, reason):
        self.closed=True;self.is_alive=False


class TestSLHSchedulerPolicy(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.authority=SLHAuthority(Path(self.temp.name))
        key,public=generate_identity()
        payload={'version':1,'action':'slh.admin.bootstrap','org_id':'test-org',
                 'admin_id':'owner','device_id':'admin-pc','public_key':public}
        self.admin_key=key
        self.authority.bootstrap({**payload,'admin_name':'Owner',
            'bootstrap_secret':(Path(self.temp.name)/'bootstrap.secret').read_text().strip(),
            'proof':sign_object(key,payload)},loopback=True)
        self.store=SchedulerPolicyStore(self.authority)

    def make_scheduler(self, count=6):
        sch=ManagedScheduler(policy_store=self.store)
        for i in range(count):sch.register(ManagedWorker(f'w{i}',FakeSocket(),{},['model']))
        return sch

    def test_revision_audit_and_admin_auth(self):
        api=SLHAPI(self.authority)
        code,body=api.call('GET','/v1/admin/scheduler/policy')
        self.assertEqual(code,401)
        payload=self.store.get()
        self.assertEqual(payload['revision'],0)
        with self.assertRaises(SLHError):self.store.update({'mode':'dedicated'},expected_revision=4,actor='owner')
        revised=self.store.update({'mode':'dedicated','dedicated_public':['w1']},expected_revision=0,actor='owner')
        self.assertEqual(revised['revision'],1)
        self.assertEqual(SchedulerPolicyStore(SLHAuthority(Path(self.temp.name))).get()['policy']['mode'],'dedicated')
        self.assertIn('scheduler.policy.updated',[a['action'] for a in self.authority.audit()])
        self.assertIn('/v1/admin/scheduler/policy',api.openapi()['paths'])

    def test_authenticated_admin_api_changes_policy_and_refuses_stale_write(self):
        api=SLHAPI(self.authority)
        challenge=self.authority.challenge('admin-pc')
        token=self.authority.verify_login({'device_id':'admin-pc',
            'challenge_id':challenge['challenge_id'],
            'signature':sign_object(self.admin_key,challenge)})['access_token']
        status,read=api.call('GET','/v1/admin/scheduler/policy',token=token,loopback=True)
        self.assertEqual(status,200)
        status,changed=api.call('PATCH','/v1/admin/scheduler/policy',
            {'expected_revision':read['revision'],'changes':{'max_public_percent':50}},
            token=token,loopback=True)
        self.assertEqual(status,200)
        self.assertEqual(changed['policy']['max_public_percent'],50)
        status,stale=api.call('PATCH','/v1/admin/scheduler/policy',
            {'expected_revision':read['revision'],'changes':{'max_public_percent':40}},
            token=token,loopback=True)
        self.assertEqual(status,409)
        self.assertEqual(stale['error']['code'],'SCHEDULER_REVISION_CONFLICT')
        self.authority.logout(token)

    def test_reject_invalid_and_conflicting_policy(self):
        for change in ({'max_public_percent':101},{'min_private_idle_percent':-1},
                       {'rotation_seconds':1},{'mode':'unknown'},
                       {'dedicated_public':['w1'],'dedicated_private':['w1']},
                       {'credit_cap':'NaN'},{'credit_period':'second'},
                       {'unrecognized':True}):
            with self.subTest(change=change),self.assertRaises(SLHError):
                normalize_policy(change)

    def test_private_first_guarantees_ready_reserve(self):
        sch=self.make_scheduler(20)
        plan=sch.plan()
        self.assertEqual(plan.max_public_slots,15)
        self.assertEqual(plan.private_idle_target,5)
        self.assertEqual(len(plan.desired),15)
        for did in plan.desired:sch.mark_advertised(did,True)
        # Actual private capacity is outside the advertised set.
        private=sch.reserve_private('private-1')
        self.assertNotIn(private.device_id,plan.desired)
        with self.assertRaises(RuntimeError):sch.reserve_private('private-2',model='unavailable-model')
        sch.release('private-1')

    def test_dedicated_and_percentage_constraints(self):
        self.store.update({'mode':'dedicated','dedicated_public':['w0','w2'],
                           'dedicated_private':['w1'], 'max_public_percent':100},
                          expected_revision=0,actor='owner')
        sch=self.make_scheduler(6)
        self.assertEqual(set(sch.plan().desired),{'w0','w2'})
        self.assertNotIn('w1',sch.plan().desired)

    def test_round_robin_changes_assignments_without_preempting(self):
        self.store.update({'mode':'round_robin','rotation_seconds':10,
                           'max_public_percent':25,'min_private_idle_percent':25},
                          expected_revision=0,actor='owner')
        sch=self.make_scheduler(4)
        a=sch.plan(now=0).desired
        for did in a:sch.mark_advertised(did,True)
        sch.reserve_public('job1',device_id=a[0],now=0)
        b=sch.plan(now=10)
        self.assertIn(a[0],b.desired) # Busy job cannot be preempted
        sch.release('job1')
        rotated=sch.plan(now=10)
        self.assertNotEqual(a,rotated.desired)

    def test_credit_cap_fails_closed_without_verified_meter(self):
        self.store.update({'credit_cap':'100','credit_period':'day'},expected_revision=0,actor='owner')
        sch=self.make_scheduler(6)
        plan=sch.plan()
        self.assertEqual(plan.desired,())
        self.assertEqual(plan.reason,'CREDIT_CAP_REQUIRES_VERIFIED_METER')
        self.assertEqual(plan.max_public_slots,0)

    def test_policy_drains_only_idle_public_sessions(self):
        sch=self.make_scheduler(4)
        plan=sch.plan()
        clients={}
        for did in plan.desired:
            sch.mark_advertised(did,True)
            clients[did]=FakeUpstream()
        busy=plan.desired[0]
        sch.reserve_public('active-paid-job',device_id=busy)
        gw=SimpleNamespace(scheduler=sch)
        proxy=SLHMarketplaceProxy(gw)
        proxy.sessions=clients
        self.store.update({'max_public_percent':0},expected_revision=0,actor='owner')
        report=proxy.reconcile(hub_url='http://127.0.0.1:1',auth={})
        self.assertIn(busy,report['occupied_public_workers'])
        self.assertIn(busy,proxy.sessions)
        self.assertEqual(len(proxy.sessions),1)
        sch.release('active-paid-job')
        report=proxy.reconcile(hub_url='http://127.0.0.1:1',auth={})
        self.assertEqual(proxy.sessions,{})

    def test_unhealthy_worker_does_not_count_toward_holdout(self):
        w=[{'device_id':'a','active':True},{'device_id':'b','active':True},
           {'device_id':'c','active':False}]
        p=normalize_policy({'min_private_idle_percent':50,'max_public_percent':100})
        plan=compute_plan(p,w,set())
        self.assertEqual(plan.private_idle_target,1)
        self.assertEqual(plan.max_public_slots,1)

    def test_runtime_report_is_labeled_and_stale_safe(self):
        self.assertEqual(self.store.runtime_status()['status'],'NOT_OBSERVED')
        self.store.write_runtime({'desired_public_workers':['w1'],'live_public_sessions':['w1'],
                                  'reason':'policy'})
        self.assertEqual(self.store.runtime_status()['status'],'OBSERVED')
        api=SLHAPI(self.authority)
        self.assertEqual(api.openapi()['paths']['/v1/admin/scheduler/status']['get']['x-implementation'],'implemented')

if __name__=='__main__':unittest.main()
