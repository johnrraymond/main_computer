"""Governance graph, independent interpreter, and explicit failure policies."""
from __future__ import annotations
from collections import deque
import json
from pathlib import Path
import unittest
from tools import nanojev_contract_v3_governance_language as lang
from tools import nanojev_contract_v3_governance_model as model
from tools import nanojev_contract_v3_governance_reference as reference
from tools import nanojev_contract_v3_governance_verifier as verifier
from tools import nanojev_contract_v3_governance_solidity as solidity
from tools.nanojev_contract_v3_language import V3Error

FIXTURE=Path(__file__).resolve().parents[1]/'tools'/'fixtures'/'nanojev_contract_v3_governed_escrow_v1.json'

def raw():return json.loads(FIXTURE.read_text())

class GovernedV3Tests(unittest.TestCase):
    def test_contract_graph_passes(self):
        v=verifier.verify(raw())
        self.assertEqual('PASS_CONDITIONAL',v['status']);self.assertEqual([],v['blockers'])
        self.assertGreater(v['reachable_nodes'],200)
        self.assertEqual(v['unsettled_nodes']*4,v['liveness']['checked_scenarios'])

    def test_compiler_does_not_alter_legacy(self):
        self.assertEqual(3,len(lang.validate(raw())['milestones']))
        text=solidity.source(raw());self.assertIn('function resolveArbitratorSilence()',text)
        self.assertIn('function withdrawTo(address payable destination)',text)
        self.assertIn('function confirmArbitrator(address candidate)',text)
        self.assertIn('unallocated + totalPending + totalWithdrawn',text)

    def test_missing_policies_fail_closed(self):
        for k in list(raw()['policy']):
            x=raw();del x['policy'][k]
            with self.subTest(k=k),self.assertRaises(V3Error):lang.validate(x)

    def test_bad_policy_values(self):
        for key in ('on_review_silence','on_appointment_failure','on_arbitrator_silence'):
            x=raw();x['policy'][key]='wait_forever'
            with self.subTest(key=key),self.assertRaises(V3Error):lang.validate(x)

    def test_accounting_mutations_rejected(self):
        x=raw();x['milestones'][0]['amount_wei']+=1
        with self.assertRaises(V3Error):lang.validate(x)
        x=raw();x['milestones'][0]['amount_wei']=-10
        with self.assertRaises(V3Error):lang.validate(x)

    def test_deadline_constraints(self):
        for k,v in [('max_extensions',3),('extension_seconds',3600),('extension_decision_seconds',20000)]:
            x=raw();x['milestones'][0][k]=v
            if k!='extension_decision_seconds':
                with self.subTest(k=k),self.assertRaises(V3Error):lang.validate(x)
            else:
                self.assertTrue(lang.validate(x))
        x=raw();x['policy']['appointment_seconds']=x['policy']['ruling_seconds']
        with self.assertRaises(V3Error):lang.validate(x)

    def test_recovery_permissions(self):
        s=lang.validate(raw());node=model.initial(s)
        # Cannot bypass submission by calling approve or arb outcome
        for act,who in [('approve','payer'),('award_worker','arbitrator'),('grant_extension','payer')]:
            with self.subTest(action=act),self.assertRaises(V3Error):model.execute(s,node,act,who)
        # At deadline, worker may submit; payer may grant one extension.
        node=model.Node(**{**node.__dict__,'now':node.due})
        self.assertIn(('grant_extension','payer'),model.available(s,node))
        after=model.execute(s,node,'grant_extension','payer')
        self.assertEqual(after.due,node.due+s['milestones'][0]['extension_seconds'])
        with self.assertRaises(V3Error):model.execute(s,after,'grant_extension','worker')

    def test_timeout_recovery_when_all_parties_silent(self):
        s=lang.validate(raw());node=model.initial(s)
        n=model.Node(**{**node.__dict__,'now':node.due+s['milestones'][0]['extension_decision_seconds']+1})
        r=model.execute(s,n,'resolve_missed_deadline','anyone')
        self.assertEqual('refunded',r.phase)
        self.assertEqual(0,r.unallocated)
        self.assertEqual(s['amount_wei'],r.pending[0])

    def test_explicit_arbitration_and_refund(self):
        s=lang.validate(raw());n=model.initial(s)
        for action,who in [('submit','worker'),('reject','payer'),('file_dispute','worker'),
                           ('nominate_arbitrator','payer'),('confirm_arbitrator','worker'),
                           ('award_payer','arbitrator')]:
            n=model.execute(s,n,action,who)
        self.assertEqual('refunded',n.phase)
        self.assertEqual(s['amount_wei'],n.pending[0])

    def test_successful_three_stage_accounting(self):
        s=lang.validate(raw());n=model.initial(s)
        for index,amount in enumerate((3,4,5)):
            n=model.execute(s,n,'submit','worker')
            n=model.execute(s,n,'approve','payer')
            self.assertEqual((12-sum((3,4,5)[:index+1]))*10**18,n.unallocated)
            self.assertEqual(sum((3,4,5)[:index+1])*10**18,n.pending[1])
        self.assertEqual('completed',n.phase)

    def test_independent_global_clock_reference_differential(self):
        s=lang.validate(raw());start=model.initial(s)
        # Check every reachable path using an independent absolute-time implementation.
        q=deque([start]);paths={start:[]};checks=0
        while q:
            n=q.popleft()
            if len(paths)>=1000:break
            for a,child in model.successors(s,n,withdrawals=True):
                if child not in paths:
                    paths[child]=paths[n]+[(n,a,child)];q.append(child)
        for target,path in list(paths.items()):
            ref=reference.Protocol(s)
            for before,a,after in path:
                if a.startswith('warp:'):
                    ref.warp(int(a.split(':',1)[1])-before.now)
                elif a.startswith('withdraw:'):
                    ref.withdraw(a.split(':',1)[1])
                else:
                    actor=dict(model.available(s,before))[a]
                    ref.invoke(a,actor)
            self.assertEqual(target.phase,ref.state)
            self.assertEqual(target.index,ref.milestone)
            self.assertEqual(target.unallocated,ref.left)
            self.assertEqual(target.pending[0],ref.owed['payer'])
            self.assertEqual(target.pending[1],ref.owed['worker'])
            self.assertEqual(target.withdrawn[0],ref.withdrawn['payer'])
            self.assertEqual(target.withdrawn[1],ref.withdrawn['worker'])
            if target.phase=='active':
                self.assertEqual(target.due-target.now,ref.due-ref.now)
            checks+=1
        self.assertGreater(checks,300)

    def test_no_milestone_specific_solidity(self):
        x=raw();x['milestones']=x['milestones'][:2]
        x['amount_wei']=7000000000000000000
        a=solidity.source(x)
        self.assertIn('MILESTONES = 2',a)
        y=raw();y['policy']['on_review_silence']='refund_payer'
        b=solidity.source(y)
        self.assertIn('_refund();',b)
        self.assertNotEqual(a,b)

if __name__=='__main__':unittest.main()
