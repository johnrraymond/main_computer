"""Atomic smoke mechanics; all model replies are scripted, not live AI claims."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tools import mc_encoding_atomic as atom
from tools import mc_encoding_checker as ck
from tools import mc_encoding_execution as ex


class AtomicTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.scan,_=ex.controls(self.root)
        self.scan.index=ck.CandidateIndex(self.scan)
        self.store=self.root/'atomic'; self.store.mkdir()
        self.doc=next(i for i in self.scan.issues if i['kind']=='UNCHECKED_DOCUMENT')
        self.code=next(i for i in self.scan.issues if i['path']=='src/orphan.py' and i['kind']=='UNCHECKED_CODE')
        self.units=[{'start':n,'end':n,'responsibility':name,'queries':[name,extra]} for n,name,extra in [(3,'add','a + b'),(4,'multiply','a * b'),(5,'divide','division')]]
        self.orphan={'start':1,'end':2,'responsibility':'unique_orphan returns 42','queries':['unique_orphan','orphan']}
        self.calls=[]

    def provider(self,messages):
        prompt=messages[0]['content']; data=json.loads(messages[1]['content'])
        self.assertLessEqual(sum(len(m['content']) for m in messages),atom.PACKET_CHARS)
        if prompt==atom.PLAN_PROMPT:
            self.calls.append('plan')
            return json.dumps({'units':self.units if data['direction']=='DOC_TO_CODE' else [self.orphan],'uncovered':[]})
        self.calls.append('decision')
        if 'packet' in data: data=data['packet']
        unit=data['unit']['responsibility']; code=data['direction']=='CODE_TO_DOC'
        d={'kind':'unknown' if code else 'normative','verdict':'MISSING_DOCUMENTATION' if code else {'add':'AGREEMENT','multiply':'DISAGREEMENT','divide':'MISSING_IMPLEMENTATION'}[unit],
           'counterparts':[],'evidence':[{'id':'A','observation':'The anchor defines the target responsibility.'}],
           'link_relation':'none','required_verification':[]}
        if unit in ('add','multiply'):
            k=next(k for k,r in data['blocks'].items() if r['path']=='src/mathops.py')
            d['counterparts']=[k]; d['link_relation']='implements'; d['evidence'].append({'id':k,'observation':'The implementation matches or conflicts with the specified operation.'})
        return json.dumps(d)

    def judge(self,messages):
        self.calls.append('judge'); data=json.loads(messages[1]['content'])
        self.assertIn('packet',data); self.assertIn('decision',data)
        self.assertLessEqual(sum(len(m['content']) for m in messages),atom.PACKET_CHARS)
        return json.dumps({'status':'PASS','items':[{'index':1,'status':'PASS','reason':'scripted fixture judgment'}],'omissions':[]})

    def no_call(self,_): self.fail('unexpected model call')

    def test_both_directions_all_four_oracle_verdicts(self):
        for issue,direction in ((self.doc,'DOC_TO_CODE'),(self.code,'CODE_TO_DOC')):
            r=atom.run_case(self.provider,self.judge,self.scan,issue,direction,self.store,0,lambda _:None)
            self.assertEqual(r['status'],'EVIDENCE_PASS',r['errors'])
            self.assertEqual(ex.oracle(r,issue['path'])['status'],'PASS')
            self.assertTrue(r['atomic_complete'])
            for item in r['answer']['items']:
                for ev in item['evidence']:
                    self.assertIn(ev['quote'],self.scan.texts[ev['path']])

    def test_resume_one_new_unit_at_a_time(self):
        one=atom.run_case(self.provider,self.judge,self.scan,self.doc,'DOC_TO_CODE',self.store,1,lambda _:None)
        self.assertEqual(one['status'],'PARTIAL'); self.assertEqual(self.calls,['plan','decision','judge'])
        self.calls.clear()
        two=atom.run_case(self.provider,self.judge,self.scan,self.doc,'DOC_TO_CODE',self.store,1,lambda _:None)
        self.assertEqual(two['status'],'PARTIAL'); self.assertEqual(self.calls,['decision','judge'])
        self.assertTrue(two['atomic_units'][0]['reused']); self.assertEqual(two['atomic_new_units'],1)
        self.calls.clear()
        three=atom.run_case(self.provider,self.judge,self.scan,self.doc,'DOC_TO_CODE',self.store,1,lambda _:None)
        self.assertEqual(three['status'],'EVIDENCE_PASS'); self.assertEqual(self.calls,['decision','judge'])
        self.assertEqual(ex.oracle(three,self.doc['path'])['status'],'PASS')
        self.calls.clear()
        four=atom.run_case(self.no_call,self.no_call,self.scan,self.doc,'DOC_TO_CODE',self.store,0,lambda _:None)
        self.assertEqual(four['status'],'EVIDENCE_PASS'); self.assertEqual(four['atomic_new_units'],0)

    def test_interrupted_judge_preserves_decision(self):
        def interrupt(_): raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt): atom.assess(self.provider,interrupt,self.scan,self.doc,'DOC_TO_CODE',self.units[0],self.store,lambda _:None)
        record=atom.read_json(next(self.store.glob('unit-*.json')))
        self.assertEqual(record['status'],'EVIDENCE_PASS'); self.assertEqual(record['evaluation']['status'],'NOT_RUN')
        r=atom.assess(self.no_call,self.judge,self.scan,self.doc,'DOC_TO_CODE',self.units[0],self.store,lambda _:None)
        self.assertEqual(r['evaluation']['status'],'PASS')

    def test_judge_rejection_is_not_promoted(self):
        def reject(_): return json.dumps({'status':'FAIL','items':[{'index':1,'status':'FAIL','reason':'unsupported'}],'omissions':[]})
        r=atom.run_case(self.provider,reject,self.scan,self.doc,'DOC_TO_CODE',self.store,0,lambda _:None)
        self.assertEqual(r['status'],'BLOCKED'); self.assertEqual(len(r['atomic_units']),1); self.assertEqual(r['answer']['items'],[])

    def test_fake_ids_and_unsupported_absence_rejected(self):
        ev,blocks,packet=atom.make_packet(self.scan,self.doc,'DOC_TO_CODE',self.units[0]); d=json.loads(self.provider([{'content':atom.DECISION_PROMPT},{'content':json.dumps(packet)}]))
        bad=copy.deepcopy(d); bad['counterparts']=['E999']
        with self.assertRaises(ValueError): atom.hydrate(bad,ev,blocks,packet,'id')
        bad=copy.deepcopy(d); bad['evidence']=[{'id':'A','observation':'source only'}]
        with self.assertRaises(ValueError): atom.hydrate(bad,ev,blocks,packet,'id')
        ev,blocks,packet=atom.make_packet(self.scan,self.code,'CODE_TO_DOC',self.orphan)
        packet['coverage']['absence_eligible']=False
        d=json.loads(self.provider([{'content':atom.DECISION_PROMPT},{'content':json.dumps(packet)}]))
        with self.assertRaisesRegex(ValueError,'absence not supported'): atom.hydrate(d,ev,blocks,packet,'id')

    def test_snapshot_recheck_and_resume_invalidation(self):
        r=atom.assess(self.provider,self.judge,self.scan,self.code,'CODE_TO_DOC',self.orphan,self.store,lambda _:None)
        self.assertEqual(r['evaluation']['status'],'PASS'); self.calls.clear()
        (self.root/'docs/contract.md').write_text('unique_orphan now documented\n')
        self.assertIn('docs/contract.md',atom.changed_absence_scope(self.scan,['src'],['docs'],{'documentation'}))
        (self.root/'docs/another.md').write_text('another doc\n')
        self.assertIn('docs/another.md',atom.changed_absence_scope(self.scan,['src'],['docs'],{'documentation'}))
        newer=ck.scan(self.root,['src'],['docs']); newer.index=ck.CandidateIndex(newer)
        # Recomputed packet is different; a previous judge PASS must not be reused.
        with self.assertRaises(AssertionError): atom.assess(self.no_call,self.no_call,newer,self.code,'CODE_TO_DOC',self.orphan,self.store,lambda _:None)

    def test_plan_bounds_and_empty_discovery(self):
        chunk=atom.anchor_chunks(self.scan,self.doc)[0]
        self.assertTrue(atom.validate_plan({'units':[],'uncovered':[]},chunk))
        outside=copy.deepcopy(self.units[0]); outside['end']=99
        self.assertTrue(atom.validate_plan({'units':[outside],'uncovered':[]},chunk))
        self.assertTrue(atom.validate_plan({'units':[self.units[0],self.units[0]],'uncovered':[]},chunk))
        def empty(_): return json.dumps({'units':[],'uncovered':['Cannot classify this passage.']})
        r=atom.run_case(empty,self.no_call,self.scan,self.doc,'DOC_TO_CODE',self.store,0,lambda _:None)
        self.assertEqual(r['status'],'BLOCKED'); self.assertFalse(r['atomic_complete'])

    def test_symbol_scope_and_oversize_line(self):
        issue=next(x for x in self.scan.issues if x.get('symbol')=='multiply')
        chunks=atom.anchor_chunks(self.scan,issue)
        self.assertEqual([(x['start'],x['end']) for x in chunks],[(4,5)])
        s=copy.deepcopy(self.scan); s.texts[self.doc['path']]='a'*3000+'\nother\n'
        self.assertIn('blocked',atom.anchor_chunks(s,self.doc)[0]); self.assertEqual(atom.anchor_chunks(s,self.doc)[1]['start'],2)

    def test_checkpoint_transfer_preserves_original(self):
        atom.assess(self.provider,self.judge,self.scan,self.code,'CODE_TO_DOC',self.orphan,self.store,lambda _:None)
        old=self.root/'old'; old.mkdir(); self.store.rename(old/'atomic')
        before={p.name:p.read_bytes() for p in (old/'atomic').iterdir()}
        self.store.mkdir(); atom.copy_checkpoints((old,{}),self.store)
        result=atom.assess(self.no_call,self.no_call,self.scan,self.code,'CODE_TO_DOC',self.orphan,self.store,lambda _:None)
        self.assertTrue(result['_reused']); self.assertEqual(before,{p.name:p.read_bytes() for p in (old/'atomic').iterdir()})

    def test_bounded_candidates_do_not_enable_absence(self):
        for i in range(12): (self.root/'docs'/f'other{i}.md').write_text('unique_orphan description\n'+('details\n'*80))
        s=ck.scan(self.root,['src'],['docs']); s.index=ck.CandidateIndex(s)
        ev,blocks,packet=atom.make_packet(s,self.code,'CODE_TO_DOC',self.orphan)
        self.assertLessEqual(len(blocks),atom.MAX_READS)
        self.assertFalse(packet['coverage']['absence_eligible']); self.assertGreater(packet['coverage']['candidate_files_not_read'],0)

    def test_cli_pilot_reuses_controls_and_reports_partial_scope(self):
        from test_mc_encoding_resume import answer
        old=self.root/'runtime/reports/mc_encoding/execution-old'; old.mkdir(parents=True)
        ev=ex.Evidence(self.scan)
        for p in ev.paths: ev.perform({'tool':'read','path':p,'start':1,'end':5})
        for role,qs in [('implementation',('divide','division')),('documentation',('unique_orphan','orphan'))]:
            for q in qs: ev.perform({'tool':'search','role':role,'query':q})
        entries=[]
        for i,(issue,direction) in enumerate(((self.doc,'DOC_TO_CODE'),(self.code,'CODE_TO_DOC')),1):
            a,_=ex.repair_citations(answer(self.scan,issue,ev),ev)
            self.assertFalse(ex.validate(a,ev,a['finding_id'],issue['path'],direction))
            evaluation={'status':'PASS','items':[{'index':j+1,'status':'PASS','reason':'fixture'} for j in range(len(a['items']))],'omissions':[]}
            r={'finding_id':a['finding_id'],'path':issue['path'],'kind':issue['kind'],'direction':direction,'status':'EVIDENCE_PASS',
               'answer':a,'actions':ev.actions,'evaluation':evaluation}
            r['oracle']=ex.oracle(r,issue['path']); self.assertEqual(r['oracle']['status'],'PASS')
            label=f'control-{i:02d}'; atom.write_json(old/(label+'.json'),r)
            entries.append({'case':label,'artifact':label+'.json','path':issue['path'],'direction':direction,'evidence':'EVIDENCE_PASS','evaluation':'PASS','oracle':'PASS'})
        atom.write_json(old/'summary.json',{'model':'qwen-fake','judge_model':'qwen-fake','cases':entries})
        source=self.root/'main_computer/mcel_knowledge/ir_bridge.py'; source.parent.mkdir(parents=True)
        source.write_text('def compile_knowledge_ir(x):\n    return x\n')
        (self.root/'docs/mcel_diagnostic_plugins.md').write_text('# Plugin\nThe plugin shall register.\n')
        calls=[]
        class Fake:
            model='qwen-fake'
            def __init__(self,*args): self.predict=4096; self.context=8192; self.response_schema=None
            def __call__(self,messages):
                calls.append(messages[0]['content']); data=json.loads(messages[1]['content'])
                if messages[0]['content']==atom.PLAN_PROMPT:
                    return json.dumps({'units':[{'start':data['start'],'end':data['end'],'responsibility':'fixture responsibility','queries':['register','compile_knowledge_ir']}],'uncovered':[]})
                if messages[0]['content']==atom.DECISION_PROMPT:
                    return json.dumps({'kind':'unknown','verdict':'UNVERIFIED','counterparts':[], 'evidence':[{'id':'A','observation':'Anchor was read.'}],'link_relation':'none','required_verification':['Locate governing contract or implementation.']})
                if messages[0]['content'].startswith(ex.JUDGE):
                    return json.dumps({'status':'PASS','items':[{'index':1,'status':'PASS','reason':'scripted review'}],'omissions':[]})
                raise AssertionError('unexpected legacy investigation')
        args=['--root',str(self.root),'--atomic','--examples','2,7','--atomic-unit-limit','1','--resume','execution-old']
        with patch.object(ex,'Ollama',Fake): self.assertEqual(ex.main(args),0)
        new=next(p for p in old.parent.glob('execution-*') if p!=old)
        summary=atom.read_json(new/'summary.json')
        self.assertEqual(summary['status'],'ATOMIC_PILOT_PASS_REVIEW_REQUIRED')
        self.assertEqual(summary['atomic_units_reviewed'],2); self.assertEqual(summary['atomic_units_conclusive'],0)
        self.assertEqual(summary['semantic_certification'],'NOT_PERFORMED')
        self.assertEqual([e['case'] for e in summary['cases']],['control-01','control-02','example-02','example-07'])
        self.assertTrue(all(e['resume_status']=='REUSED_VALIDATED_PASS' for e in summary['cases'][:2]))
        self.assertEqual(len(calls),6)
        calls.clear(); args[-1]=new.name
        with patch.object(ex,'Ollama',Fake): self.assertEqual(ex.main(args),0)
        self.assertEqual(calls,[])

if __name__=='__main__': unittest.main()
