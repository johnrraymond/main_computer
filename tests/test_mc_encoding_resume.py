"""Reproduce missing quote failure and exercise resume without live inference."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tools import mc_encoding_checker as ck
from tools import mc_encoding_execution as ex


def loc(path,line,quote,end=None):
    return {'path':path,'line':line,'end_line':line if end is None else end,'quote':quote}


def item(name,verdict,doc,impl):
    links=[]
    if doc and impl:
        links=[{'source':impl[0],'documentation':doc,'source_id':'src.'+name,'document_id':'req.'+name,'source_relation':'implements','document_relation':'implemented-by'}]
    return {'requirement_or_responsibility':name,'requirement_kind':'normative' if doc else 'unknown',
            'documentation_location':doc,'implementation_locations':impl,'verdict':verdict,
            'encoding_status':'MISSING_LINK','evidence':[dict(x,observation='Observed behavior') for x in ([doc] if doc else [])+impl],
            'proposed_links':links,'required_verification':[]}


def answer(scan,issue,ev):
    fid=ck.digest(json.dumps(issue,sort_keys=True))[:20]
    doc='docs/contract.md'; src='src/mathops.py'; orphan='src/orphan.py'
    if issue['path']==doc:
        items=[item('add','AGREEMENT',loc(doc,3,'return a + b'),[loc(src,2,'return a + b')]),
               item('multiply','DISAGREEMENT',loc(doc,4,'return a * b'),[loc(src,5,'return a - b')]),
               item('divide','MISSING_IMPLEMENTATION',loc(doc,5,'return a / b'),[])]
    else:
        items=[item('unique_orphan function behavior','MISSING_DOCUMENTATION',None,[loc(orphan,1,'def unique_orphan():')])]
        # Exact defect from the actual user's control-02 review: two missing quote fields.
        items[0]['evidence']=[{'path':orphan,'line':1,'end_line':1,'observation':'def unique_orphan():'},
                              {'path':doc,'line':3,'end_line':3,'observation':'The function add shall return a + b.'}]
    return {'finding_id':fid,'searched_scope':{'paths':sorted({r['path'] for r in ev.reads}),'searches':[],'limitations':[]},'items':items}


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.s,self.targets=ex.controls(self.root)
        self.ev=ex.Evidence(self.s)
        for path in self.ev.paths: self.ev.perform({'tool':'read','path':path,'start':1,'end':5})
        for role,qs in [('implementation',('divide','division')),('documentation',('unique_orphan','orphan'))]:
            for q in qs: self.ev.perform({'tool':'search','role':role,'query':q})
        self.issue=next(x for x in self.s.issues if x['path']=='src/orphan.py' and x['kind']=='UNCHECKED_CODE')
        self.a=answer(self.s,self.issue,self.ev); self.fid=self.a['finding_id']

    def test_actual_missing_quote_pattern_repairs_without_semantic_changes(self):
        self.assertEqual(len(ex.validate(self.a,self.ev,self.fid,'src/orphan.py','CODE_TO_DOC')),2)
        repaired,events=ex.repair_citations(self.a,self.ev)
        self.assertEqual(len(events),2); self.assertEqual(ex.validate(repaired,self.ev,self.fid,'src/orphan.py','CODE_TO_DOC'),[])
        self.assertEqual(ex.oracle({'status':'EVIDENCE_PASS','answer':repaired},'src/orphan.py')['status'],'PASS')
        original=copy.deepcopy(repaired)
        for entry in original['items'][0]['evidence']: del entry['quote']
        self.assertEqual(original,self.a)

    def test_no_fabrication_or_unread_repairs(self):
        self.a['items'][0]['evidence'][0]['observation']='def nonexistent():'
        self.ev.reads=[r for r in self.ev.reads if r['path']!='docs/contract.md']
        repaired,events=ex.repair_citations(self.a,self.ev)
        self.assertEqual(events,[]); self.assertEqual(repaired,self.a)
        self.assertTrue(ex.validate(repaired,self.ev,self.fid,'src/orphan.py','CODE_TO_DOC'))

    def test_never_replace_present_invalid_quote(self):
        self.a['items'][0]['evidence'][0]['quote']='invented'
        repaired,events=ex.repair_citations(self.a,self.ev)
        self.assertEqual(len(events),1); self.assertEqual(repaired['items'][0]['evidence'][0]['quote'],'invented')
        self.assertTrue(ex.validate(repaired,self.ev,self.fid,'src/orphan.py','CODE_TO_DOC'))

    def test_replay_rejects_changed_hash_and_search(self):
        for tool,field,value in [('read','sha256','wrong'),('search','matching_files',900)]:
            records=copy.deepcopy(self.ev.actions); next(r for r in records if r['tool']==tool)[field]=value
            with self.assertRaises(ValueError): ex.replay_evidence(self.s,records)

    def test_schema_rejects_missing_quote_and_unknown_fields(self):
        bad=ex.schema_errors({'final':self.a},ex.TURN_SCHEMA)
        self.assertTrue(bad)
        fixed,_=ex.repair_citations(self.a,self.ev)
        self.assertFalse(ex.schema_errors({'final':fixed},ex.TURN_SCHEMA))
        fixed['items'][0]['verdict']='invented'; self.assertTrue(ex.schema_errors({'final':fixed},ex.TURN_SCHEMA))
        self.assertTrue(ex.schema_errors({'actions':[{'tool':'write','path':'src/orphan.py'}]},ex.TURN_SCHEMA))

    def test_saved_response_repair_needs_no_investigator_call(self):
        previous={'actions':self.ev.actions,'answer':self.a,'transcript':['original']}
        def never(_): self.fail('investigator should not be called')
        result=ex.investigate(never,self.s,'prompt',self.fid,'src/orphan.py','CODE_TO_DOC',previous=previous)
        self.assertEqual(result['status'],'EVIDENCE_PASS'); self.assertEqual(len(result['repairs']),2)
        self.assertEqual(result['transcript'],['original'])

    def test_targeted_model_correction_when_literal_repair_impossible(self):
        self.a['items'][0]['evidence'][0]['observation']='Paraphrase, not literal evidence.'
        prev={'actions':self.ev.actions,'answer':self.a}
        correct,_=ex.repair_citations(self.a,self.ev)
        correct['items'][0]['evidence'][0]['quote']='def unique_orphan():'
        calls=[]
        def provider(messages):
            calls.append(copy.deepcopy(messages)); return json.dumps({'final':correct})
        result=ex.investigate(provider,self.s,'prompt',self.fid,'src/orphan.py','CODE_TO_DOC',previous=prev)
        self.assertEqual(result['status'],'EVIDENCE_PASS',result['errors']); self.assertEqual(len(calls),1)
        self.assertIn('Validation errors',calls[0][-1]['content']); self.assertIn('quote',calls[0][-1]['content'])

    def test_resume_cli_preserves_pass_and_repairs_failed_control(self):
        out=self.root/'runtime/reports/mc_encoding'; old=out/'execution-old'; old.mkdir(parents=True)
        entries=[]
        for j,(path,kind,direction) in enumerate(self.targets,1):
            issue=next(x for x in self.s.issues if x['path']==path and x['kind']==kind)
            a=answer(self.s,issue,self.ev); label=f'control-{j:02d}'
            evaluation={'status':'PASS','items':[{'index':i+1,'status':'PASS','reason':'scripted'} for i in range(len(a['items']))],'omissions':[]} if j==1 else {'status':'NOT_RUN'}
            result={'status':'EVIDENCE_PASS' if j==1 else 'EVIDENCE_FAIL','answer':a,'actions':self.ev.actions,'evaluation':evaluation,
                    'finding_id':a['finding_id'],'path':path,'direction':direction,'kind':kind,'errors':[], 'transcript':['original']}
            result['oracle']=ex.oracle(result,path)
            artifact=label+'.json'; (old/artifact).write_text(json.dumps(result))
            entries.append({'case':label,'path':path,'direction':direction,'evidence':result['status'],'evaluation':evaluation['status'],'oracle':result['oracle']['status'],'artifact':artifact})
        (old/'summary.json').write_text(json.dumps({'model':'qwen-fake','judge_model':'qwen-fake','cases':entries}))
        original={p.name:p.read_bytes() for p in old.iterdir()}; calls=[]
        class Fake:
            model='qwen-fake'
            def __init__(self,*args): pass
            def __call__(self,messages):
                calls.append(messages[0]['content'])
                if messages[0]['content']!=ex.JUDGE: raise AssertionError('investigator called on resumed control')
                return json.dumps({'status':'PASS','items':[{'index':1,'status':'PASS','reason':'scripted judge'}],'omissions':[]})
        with patch.object(ex,'Ollama',Fake):
            self.assertEqual(ex.main(['--root',str(self.root),'--controls-only','--resume','execution-old']),0)
        self.assertEqual(calls,[ex.JUDGE]); self.assertEqual(original,{p.name:p.read_bytes() for p in old.iterdir()})
        new=next(p for p in out.glob('execution-*') if p!=old)
        summary=json.loads((new/'summary.json').read_text())
        self.assertEqual(summary['cases'][0]['resume_status'],'REUSED_VALIDATED_PASS')
        self.assertEqual(summary['cases'][1]['resume_status'],'REPAIRED_OR_REEVALUATED')
        self.assertEqual(len(json.loads((new/'control-02.json').read_text())['repairs']),2)
        self.assertEqual(summary['status'],'PASS_REVIEW_REQUIRED')
        self.assertEqual(summary['resumed_from'],'execution-old')
        self.assertEqual(summary['semantic_certification'],'NOT_PERFORMED')
        # Tampered evidence fails closed, not reused.
        artifact=old/'control-01.json'; data=json.loads(artifact.read_text()); data['actions'][0]['sha256']='wrong'; artifact.write_text(json.dumps(data))
        with patch.object(ex,'Ollama',Fake):
            self.assertEqual(ex.main(['--root',str(self.root),'--controls-only','--resume','execution-old']),2)
        self.assertEqual(calls,[ex.JUDGE])

    def test_judge_repair_not_semantic_override(self):
        r={'status':'EVIDENCE_PASS','answer':{'items':[{}]},'actions':[]}; calls=[]
        def provider(messages):
            calls.append(copy.deepcopy(messages))
            if len(calls)==1: return json.dumps({'status':'PASS','items':[],'omissions':[]})
            return json.dumps({'status':'FAIL','items':[{'index':1,'status':'FAIL','reason':'unsupported'}],'omissions':[]})
        self.assertEqual(ex.evaluate(provider,r)['status'],'FAIL'); self.assertEqual(len(calls),2)
        self.assertIn('validation_errors',calls[1][-1]['content'])

if __name__=='__main__': unittest.main()
