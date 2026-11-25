import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tools import mc_encoding_checker as ck
from tools import mc_encoding_execution as ex


def loc(path,line,quote):
    return {'path':path,'line':line,'end_line':line,'quote':quote}


def item(name,verdict,doc,impl):
    links=[]
    if doc and impl:
        links=[{'source':impl[0],'documentation':doc,'source_id':'src.'+name,'document_id':'req.'+name,'source_relation':'implements','document_relation':'implemented-by'}]
    return {'requirement_or_responsibility':name,'requirement_kind':'normative' if doc else 'unknown',
            'documentation_location':doc,'implementation_locations':impl,'verdict':verdict,
            'encoding_status':'MISSING_LINK' if links else 'NOT_APPLICABLE',
            'evidence':[dict(x,observation='Observed literal behavior or requirement') for x in ([doc] if doc else [])+impl],
            'proposed_links':links,'required_verification':[]}


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.s,_=ex.controls(Path(self.tmp.name)); self.ev=ex.Evidence(self.s)
        for path in self.ev.paths: self.ev.perform({'tool':'read','path':path,'start':1,'end':5})
        for q in ('divide','division'): self.ev.perform({'tool':'search','role':'implementation','query':q})
        for q in ('unique_orphan','orphan'): self.ev.perform({'tool':'search','role':'documentation','query':q})
        doc='docs/contract.md'; src='src/mathops.py'
        self.items=[item('add','AGREEMENT',loc(doc,3,'return a + b'),[loc(src,2,'return a + b')]),
                    item('multiply','DISAGREEMENT',loc(doc,4,'return a * b'),[loc(src,5,'return a - b')]),
                    item('divide','MISSING_IMPLEMENTATION',loc(doc,5,'return a / b'),[])]
        self.answer={'finding_id':'f','searched_scope':{'paths':[doc,src],'searches':['divide','division'],'limitations':[]},'items':self.items}
    def check(self,a=None): return ex.validate(a or self.answer,self.ev,'f','docs/contract.md','DOC_TO_CODE')
    def test_mixed_evidence_and_oracle(self):
        self.assertEqual(self.check(),[])
        self.assertEqual(ex.oracle({'status':'EVIDENCE_PASS','answer':self.answer},'docs/contract.md')['status'],'PASS')
    def test_fabricated_quote_rejected(self):
        self.items[1]['implementation_locations'][0]['quote']='return a * b'
        self.assertTrue(self.check())
    def test_unread_source_rejected(self):
        self.ev.reads=[r for r in self.ev.reads if r['path']!='src/mathops.py']; self.assertTrue(self.check())
    def test_absence_without_search_rejected(self):
        self.ev.searches=[]; self.assertTrue(any('missing implementation' in e for e in self.check()))
    def test_invalid_reciprocal_rejected(self):
        self.items[0]['proposed_links'][0]['document_relation']='governs'; self.assertTrue(self.check())
    def test_wrong_semantics_fails_oracle_even_with_real_quotes(self):
        self.items[1]['verdict']='AGREEMENT'; self.assertEqual(self.check(),[])
        self.assertEqual(ex.oracle({'status':'EVIDENCE_PASS','answer':self.answer},'docs/contract.md')['status'],'FAIL')
    def test_read_only(self):
        for a in [{'tool':'write','path':'src/mathops.py'},{'tool':'read','path':'../secret'},{'tool':'read','path':'/etc/passwd'},{'tool':'read','path':'src/mathops.py','start':0}]:
            with self.assertRaises(ValueError): self.ev.perform(a)
    def test_reverse_absence(self):
        orphan=loc('src/orphan.py',1,'def unique_orphan():'); a=copy.deepcopy(self.answer)
        a['items']=[item('unique_orphan','MISSING_DOCUMENTATION',None,[orphan])]
        self.assertEqual(ex.validate(a,self.ev,'f','src/orphan.py','CODE_TO_DOC'),[])
        self.assertEqual(ex.oracle({'status':'EVIDENCE_PASS','answer':a},'src/orphan.py')['status'],'PASS')
    def test_protocol_and_evaluator(self):
        actions=[{'tool':'read','path':'src/mathops.py','start':1,'end':5}]+[{'tool':'search','query':q,'role':'implementation'} for q in ('divide','division')]
        answers=iter([json.dumps({'actions':actions}),json.dumps({'final':self.answer})]); provider=lambda _:next(answers)
        r=ex.investigate(provider,self.s,'actual prompt','f','docs/contract.md','DOC_TO_CODE',turns=2)
        self.assertEqual(r['status'],'EVIDENCE_PASS',r['errors'])
        judge={'status':'PASS','items':[{'index':i,'status':'PASS','reason':'supported'} for i in range(1,4)],'omissions':[]}
        self.assertEqual(ex.evaluate(lambda _:json.dumps(judge),r)['status'],'PASS')
        judge['items'].pop(); self.assertEqual(ex.evaluate(lambda _:json.dumps(judge),r)['status'],'FAIL')
    def test_protocol_retries_wrong_finding(self):
        a=copy.deepcopy(self.answer); a['finding_id']='wrong'
        r=ex.investigate(lambda _:json.dumps({'final':a}),self.s,'prompt','f','docs/contract.md','DOC_TO_CODE',turns=1)
        self.assertEqual(r['status'],'EVIDENCE_FAIL'); self.assertIn('wrong finding_id',r['errors'])
    def test_full_control_cli_with_scripted_provider(self):
        # Transport-independent integration: scripted replies are not AI evidence.
        root=Path(self.tmp.name)
        def reply(messages):
            if messages[0]['content']==ex.JUDGE:
                payload=json.loads(messages[1]['content']); n=len(payload['answer']['items'])
                return json.dumps({'status':'PASS','items':[{'index':j,'status':'PASS','reason':'scripted test'} for j in range(1,n+1)],'omissions':[]})
            body=messages[1]['content']; fid=body.splitlines()[0].split(': ')[1]; is_doc='Location: docs/contract.md' in body
            if len(messages)==3:
                role='implementation' if is_doc else 'documentation'
                qs=['divide','division'] if is_doc else ['unique_orphan','orphan']
                actions=[{'tool':'search','query':q,'role':role} for q in qs]
                if is_doc: actions.append({'tool':'read','path':'src/mathops.py','start':1,'end':5})
                return json.dumps({'actions':actions})
            items=self.items if is_doc else [item('unique_orphan','MISSING_DOCUMENTATION',None,[loc('src/orphan.py',1,'def unique_orphan():')])]
            paths=['docs/contract.md','src/mathops.py'] if is_doc else ['src/orphan.py']
            return json.dumps({'final':{'finding_id':fid,'searched_scope':{'paths':paths,'searches':[],'limitations':[]},'items':items}})
        class Fake:
            model='scripted-test-only'
            def __init__(self,*args): pass
            def __call__(self,messages): return reply(messages)
        with patch.object(ex,'Ollama',Fake):
            self.assertEqual(ex.main(['--root',str(root),'--controls-only']),0)
        run_dir=next((root/'runtime/reports/mc_encoding').glob('execution-*'))
        summary=json.loads((run_dir/'summary.json').read_text())
        self.assertEqual(summary['status'],'PASS_REVIEW_REQUIRED'); self.assertEqual(len(summary['cases']),2)
        self.assertEqual(summary['semantic_certification'],'NOT_PERFORMED')
        self.assertTrue((run_dir/'review.txt').is_file())
        self.assertFalse((root/'runtime/reports/mc_encoding/receipts.json').exists())

    def test_provider_refuses_nonlocal(self):
        with self.assertRaises(ValueError): ex.Ollama('http://example.com','',1)
    def test_no_fixture_as_evidence(self):
        path='src/mc_encoding_prompt_replay_v1.json'; self.s.texts[path]='fake'; self.s.kinds[path]='code'
        self.assertNotIn(path,ex.Evidence(self.s).paths); self.assertNotIn(path,ck.CandidateIndex(self.s).paths)

if __name__=='__main__': unittest.main()
