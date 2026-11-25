"""Replay the supplied semantic rejection using reconstructed fixture actions.
Scripted investigator/judge replies exercise the pipeline, not live AI quality.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tools import mc_encoding_checker as ck
from tools import mc_encoding_execution as ex


class SemanticRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.scan,self.targets=ex.controls(self.root)
        self.records=json.loads((Path(__file__).parent/'fixtures/mc_encoding_semantic_rejection_v1.json').read_text())
        self.ev=ex.Evidence(self.scan)
        for path in self.ev.paths:
            self.ev.perform({'tool':'read','path':path,'start':1,'end':5})
        for role,queries in [('implementation',['divide','def divide']),('documentation',['unique_orphan','orphan'])]:
            for query in queries: self.ev.perform({'tool':'search','role':role,'query':query})
        for i,(anchor,kind,direction) in enumerate(self.targets,1):
            issue=next(x for x in self.scan.issues if x['path']==anchor and x['kind']==kind)
            r=self.records[f'control-{i:02d}']; fid=ck.digest(json.dumps(issue,sort_keys=True))[:20]
            self.assertEqual(r['answer']['finding_id'],fid)
            r.update(path=anchor,kind=kind,direction=direction,finding_id=fid,actions=copy.deepcopy(self.ev.actions))
        self.previous=self.records['control-02']
        self.issue=next(x for x in self.scan.issues if x['path']=='src/orphan.py' and x['kind']=='UNCHECKED_CODE')
        self.fixed=copy.deepcopy(self.previous['answer']); it=self.fixed['items'][0]
        cite={'path':'src/orphan.py','line':1,'end_line':2,'quote':'def unique_orphan():\n    return 42'}
        it['implementation_locations']=[cite]
        it['evidence']=[dict(cite,observation='The function returns 42. Two repository-wide documentation content searches for unique_orphan and orphan examined one document with no hits. The full contract was read and covers only other functions; no corresponding documentation was found in this scope.')]
        it['encoding_status']='NOT_APPLICABLE'
        it['required_verification']=['Determine whether this observed responsibility merits documentation under project policy.']
        self.pass_judge={'status':'PASS','items':[{'index':1,'status':'PASS','reason':'Scoped absence supported by source read and complete document search; no claim about documentation policy.'}],'omissions':[]}

    def test_coverage_measures_reads_and_searches_without_claiming_absence(self):
        coverage=ex.counterpart_coverage(self.ev,'CODE_TO_DOC')
        self.assertEqual(coverage['indexed_opposite_role_files'],1)
        self.assertEqual(coverage['fully_read_paths'],['docs/contract.md'])
        self.assertEqual([r['matching_files'] for r in coverage['whole_role_searches']],[0,0])
        partial=ex.Evidence(self.scan)
        partial.perform({'tool':'read','path':'docs/contract.md','start':1,'end':2})
        partial.perform({'tool':'read','path':'docs/contract.md','start':4,'end':5})
        self.assertEqual(ex.counterpart_coverage(partial,'CODE_TO_DOC')['fully_read_opposite_role_files'],0)
        partial.perform({'tool':'read','path':'docs/contract.md','start':3,'end':3})
        self.assertEqual(ex.counterpart_coverage(partial,'CODE_TO_DOC')['fully_read_opposite_role_files'],1)

    def test_rejection_fixture_excluded_from_discovery(self):
        path='tests/fixtures/mc_encoding_semantic_rejection_v1.json'
        self.scan.texts[path]='synthetic'; self.scan.kinds[path]='code'
        self.assertNotIn(path,ex.Evidence(self.scan).paths)
        self.assertNotIn(path,ck.CandidateIndex(self.scan).paths)

    def test_actual_rejection_is_semantic_not_formatting(self):
        self.assertTrue(ex.semantic_rejection(self.previous))
        self.assertEqual(ex.validate(self.previous['answer'],self.ev,self.previous['finding_id'],self.previous['path'],self.previous['direction']),[])
        self.assertEqual(ex.oracle(self.previous,self.previous['path'])['status'],'PASS')
        self.assertEqual(self.previous['evaluation']['status'],'FAIL')

    def test_targeted_repair_and_fresh_judge(self):
        calls=[]
        def investigator(messages):
            calls.append('investigator')
            self.assertIn('Prior semantic review',messages[-1]['content'])
            self.assertIn('The critique may itself be wrong',messages[-1]['content'])
            self.assertNotIn('"expected":',messages[-1]['content'])
            return json.dumps({'final':self.fixed})
        def judge(messages):
            calls.append('judge'); data=json.loads(messages[1]['content'])
            self.assertEqual(data['target']['direction'],'CODE_TO_DOC')
            self.assertEqual(data['target']['path'],'src/orphan.py')
            self.assertIn('does NOT require an existing normative clause',messages[0]['content'])
            self.assertIn('incidental defects in supporting files are not omissions',messages[0]['content'])
            self.assertEqual(data['answer'],self.fixed)
            self.assertEqual(data['observations'],self.previous['actions'])
            self.assertEqual(data['counterpart_coverage']['fully_read_paths'],['docs/contract.md'])
            self.assertNotIn('oracle',data)
            return json.dumps(self.pass_judge)
        result=ex.case(investigator,judge,self.scan,self.issue,'CODE_TO_DOC',2,None,previous=self.previous)
        self.assertEqual(calls,['investigator','judge']); self.assertEqual(result['status'],'EVIDENCE_PASS')
        self.assertEqual(result['evaluation']['status'],'PASS'); self.assertEqual(ex.oracle(result,result['path'])['status'],'PASS')
        self.assertEqual(result['semantic_repairs'][0]['evaluation'],self.previous['evaluation'])
        self.assertEqual(result['semantic_repairs'][0]['answer'],self.previous['answer'])

    def test_repeat_rejection_still_fails_and_is_bounded(self):
        calls=[]
        def investigator(_): calls.append('repair'); return json.dumps({'final':self.fixed})
        def judge(_): calls.append('judge'); return json.dumps(self.previous['evaluation'])
        result=ex.case(investigator,judge,self.scan,self.issue,'CODE_TO_DOC',2,None,previous=self.previous)
        self.assertEqual(calls,['repair','judge']); self.assertEqual(result['evaluation']['status'],'FAIL')
        self.assertEqual(len(result['semantic_repairs']),1)

    def test_repair_cannot_bypass_evidence_gate(self):
        fabricated=copy.deepcopy(self.fixed); fabricated['items'][0]['evidence'][0]['quote']='return 43'
        def judge(_): self.fail('invalid evidence must not reach judge')
        result=ex.case(lambda _:json.dumps({'final':fabricated}),judge,self.scan,self.issue,'CODE_TO_DOC',1,None,previous=self.previous)
        self.assertEqual(result['status'],'EVIDENCE_FAIL'); self.assertEqual(result['evaluation']['status'],'NOT_RUN')
        self.assertEqual(ex.oracle(result,result['path'])['status'],'FAIL')

    def test_fresh_semantic_failure_is_repaired_once(self):
        calls=[]
        def investigator(messages):
            calls.append('investigator')
            if 'Prior semantic review' in messages[-1]['content']:
                a=copy.deepcopy(self.fixed); a['searched_scope']['paths']=['src/orphan.py','docs/contract.md']
                return json.dumps({'final':a})
            if len(messages)==3: return json.dumps({'actions':[{'tool':'read','path':'docs/contract.md','start':1,'end':5}]+[{'tool':'search','role':'documentation','query':q} for q in ('unique_orphan','orphan')]})
            a=copy.deepcopy(self.previous['answer']); a['searched_scope']['paths']=['src/orphan.py','docs/contract.md']
            return json.dumps({'final':a})
        def judge(_):
            calls.append('judge')
            return json.dumps(self.previous['evaluation'] if calls.count('judge')==1 else self.pass_judge)
        result=ex.case(investigator,judge,self.scan,self.issue,'CODE_TO_DOC',2,None)
        self.assertEqual(calls,['investigator','investigator','judge','investigator','judge'])
        self.assertEqual(result['evaluation']['status'],'PASS'); self.assertEqual(len(result['semantic_repairs']),1)

    def test_resume_both_controls_preserve_old_report_and_oracle(self):
        out=self.root/'runtime/reports/mc_encoding'; old=out/'execution-i4yn2lrh'; old.mkdir(parents=True)
        entries=[]
        for label,r in self.records.items():
            name=label+'.json'; (old/name).write_text(json.dumps(r))
            entries.append({'case':label,'path':r['path'],'direction':r['direction'],'evidence':r['status'],'evaluation':r['evaluation']['status'],'oracle':r['oracle']['status'],'artifact':name})
        (old/'summary.json').write_text(json.dumps({'model':'qwen-scripted','judge_model':'qwen-scripted','cases':entries}))
        before={p.name:p.read_bytes() for p in old.iterdir()}; calls=[]; test=self
        class Fake:
            model='qwen-scripted'
            def __init__(self,*args): pass
            def __call__(self,messages):
                if messages[0]['content']==ex.JUDGE:
                    calls.append('judge'); return json.dumps(test.pass_judge)
                calls.append('repair'); test.assertIn('Prior semantic review',messages[-1]['content'])
                return json.dumps({'final':test.fixed})
        with patch.object(ex,'Ollama',Fake):
            self.assertEqual(ex.main(['--root',str(self.root),'--resume',old.name,'--controls-only']),0)
        self.assertEqual(calls,['repair','judge']); self.assertEqual(before,{p.name:p.read_bytes() for p in old.iterdir()})
        new=next(p for p in out.glob('execution-*') if p!=old); summary=json.loads((new/'summary.json').read_text())
        self.assertEqual(summary['cases'][0]['resume_status'],'REUSED_VALIDATED_PASS')
        self.assertEqual(summary['cases'][1]['resume_status'],'REPAIRED_OR_REEVALUATED')
        self.assertEqual(summary['semantic_certification'],'NOT_PERFORMED')
        self.assertIn('semantic_repairs',(new/'review.txt').read_text())

if __name__=='__main__': unittest.main()
