import json
from decimal import Decimal as D
from pathlib import Path
from contextlib import closing
import sqlite3
import tempfile
import unittest
from unittest import mock

from tools import nanojev_hawk_dove_lisp as hd
from tools import nanojev_hawk_dove_lisp_train as train

DEMO={
  'HAWK_HAWK_PAYOFF':D('-1'),
  'HAWK_DOVE_PAYOFF':D('3'),
  'DOVE_HAWK_PAYOFF':D('0'),
  'DOVE_DOVE_PAYOFF':D('1.5'),
}

class TeacherTests(unittest.TestCase):
    def test_demo_math(self):
        a,b=D('0.2'),D('0.8')
        self.assertEqual(hd.oracle(a,b,DEMO),(D('2.2'),D('1.20'),'HAWK'))
        self.assertEqual(hd.oracle(D('0.6'),D('0.4'),DEMO)[2],'TIE')
        self.assertEqual(hd.oracle(D('0.8'),D('0.2'),DEMO)[2],'DOVE')

    def test_real_expected_values_all_tiers(self):
        for a in (D('0'),D('0.2'),D('0.6'),D('0.8'),D('1')):
            b=1-a
            for tier in hd.TIERS:
                for column in ((0,1) if tier in ('T00','T01') else (0,)):
                    for v in (range(16) if tier=='T03' else (0,)):
                        prompt=hd.lisp(a,b,tier,variation=v,column=column)
                        self.assertEqual(hd.validate_prompt(prompt,tier,a,b,DEMO,column),
                            hd.evaluate_presentation(prompt,DEMO))

    def test_payoff_fixes_arbitrary_constants(self):
        p={k:D(i) for i,k in enumerate(hd.TAGS,start=2)}
        for i in range(11):
            a,b=D(i)/10,1-D(i)/10
            truth=hd.oracle(a,b,p)[:2]
            for tier in hd.TIERS[2:]:
                for v in (range(16) if tier=='T03' else (0,)):
                    prompt=hd.lisp(a,b,tier,variation=v)
                    self.assertEqual(hd.evaluate_presentation(prompt,p),truth)

    def test_bindings_not_printed_and_no_registration(self):
        for tier in hd.TIERS:
            p=hd.lisp(D('.2'),D('.8'),tier)
            self.assertNotIn('INPUT_A',p);self.assertNotIn('INPUT_B',p)
            self.assertNotIn('=-1',p);self.assertNotIn(' = ',p)
            self.assertNotIn('With(',p);self.assertNotIn('Add(',p)
            for k in hd.TAGS:self.assertLessEqual(hd.ATOM.findall(p).count(k),1)

    def test_pure_lisp_parser_rejects_bad(self):
        for s in ('(ADD 1 2','(ADD 1 2))','(ADD 1) (ADD 2)','', '(BOGUS 2 3)'):
            with self.assertRaises(ValueError):hd.evaluate_presentation(s,DEMO)

    def test_reject_swapped_column_and_probabilities(self):
        with self.assertRaises(ValueError):hd.lisp(D('.3'),D('.8'),'T02')
        p=hd.lisp(D('.2'),D('.8'),'T02').replace(
          '(MUL 0.2 HAWK_HAWK_PAYOFF)','(MUL 0.8 HAWK_HAWK_PAYOFF)')
        with self.assertRaises(AssertionError):hd.validate_prompt(p,'T02',D('.2'),D('.8'),DEMO)
        with self.assertRaises(ValueError):hd.evaluate_presentation(
          '(HAWK_DOVE (BELIEFS (HAWK 0.5) (DOVE 0.8)) (ARGMAX (X) (Y)))',DEMO)

    def test_non_normalized_independent_weights(self):
        a,b=D('0.2'),D('0.3')
        gold=hd.oracle(a,b,DEMO)[:2]
        self.assertEqual(gold,(D('0.7'),D('0.45')))
        for tier in hd.TIERS[2:]:
            for v in range(16) if tier=='T03' else (0,):
                p=hd.lisp(a,b,tier,variation=v,mode='weights')
                self.assertEqual(hd.evaluate_presentation(p,DEMO),gold)
        with self.assertRaises(ValueError):hd.lisp(a,b,'T02',mode='probabilities')

    def test_logbars_do_not_change_exact_number(self):
        self.assertEqual(hd.number('0.2,||||.'),D('0.2'))
        self.assertEqual(hd.eval_math(hd.parse('(MUL 0.2,|. HAWK_HAWK_PAYOFF)'),DEMO),D('-0.2'))

    def test_built_teacher_preflight_and_disjoint_splits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'payoffs.json';p.write_text(json.dumps({
              'binding_version':'test','payoffs':{k:str(v) for k,v in DEMO.items()}}))
            db=root/'teacher.sqlite3';info=hd.build_db(db,p,40)
            self.assertEqual(info['config']['rows'],41*26)
            m=train.teacher_info(db)
            self.assertEqual(m['config']['schema'],hd.SCHEMA)
            with closing(sqlite3.connect(db)) as cn:
                duplicate=cn.execute('SELECT instance,COUNT(DISTINCT split) FROM cases GROUP BY instance HAVING COUNT(DISTINCT split)>1').fetchall()
                self.assertEqual(duplicate,[])
            for t in ('T00','T01'):
                rows=train.rows_for_probe(db,'train',t,8,0)
                self.assertEqual({int(r['case_id'].split('/')[-2]) for r in rows},{0,1})
            rows=train.rows_for_probe(db,'train','T03',16,0)
            self.assertEqual({int(r['case_id'].split('/')[-1]) for r in rows},set(range(16)))
            with self.assertRaises(FileExistsError):hd.build_db(db,p,40)


    def test_training_loop_cpu_mock_adapter(self):
        """Exercise optimizer/checkpoint/probe behavior without claiming NanoJev training."""
        import sys
        from types import ModuleType, SimpleNamespace
        import torch
        from torch import nn
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);bindings=root/'payoffs.json'
            bindings.write_text(json.dumps({'binding_version':'training-smoke',
                'payoffs':{k:str(v) for k,v in DEMO.items()}}))
            db=root/'teacher.sqlite3';hd.build_db(db,bindings,80)
            source=root/'source.pt';parent=root/'parent.pt'
            source.write_text('mock-source');parent.write_text('mock-parent')
            class Decoder(nn.Module):
                def __init__(self):
                    super().__init__();self.weight=nn.Parameter(torch.tensor(0.2))
            class V3:
                @staticmethod
                def objective(output,target,base,mode):return torch.mean((output-target)**2),None
                @staticmethod
                def predict(output,base,mode):return output
            rt=ModuleType('tools.nanojev_game_math_curriculum_v10_runtime')
            ex=ModuleType('tools.nanojev_game_transform_experiment')
            def make_model(source,parent,**opts):
                model={'decoder':Decoder(),'gamehead':nn.Linear(1,1),
                       'v3':V3(),'base':None,'device':'cpu'}
                return model,{},'not-needed'
            def forward(model,prompts):
                values=[]
                for p in prompts:
                    # Deliberately imperfect dummy differentiable regressor.
                    feature=torch.tensor([len(p)/300.,p.count('MUL')/10.],dtype=torch.float32)
                    values.append(feature*model['decoder'].weight)
                return torch.stack(values),None
            ex.forward=forward
            rt.load_model=make_model
            rt.token_eligible=lambda m,p:True
            rt.batch_predict=lambda m,ps:[tuple(v) for v in forward(m,ps)[0].detach().tolist()]
            arg=train.make_parser().parse_args(['train','--teacher',str(db),'--source',str(source),
                '--parent',str(parent),'--output',str(root/'output'),'--device','cpu',
                '--dtype','float32','--steps','2','--chunk-steps','1',
                '--views','2','--probe-per-tier','16'])
            with mock.patch.dict(sys.modules,{
                'tools.nanojev_game_math_curriculum_v10_runtime':rt,
                'tools.nanojev_game_transform_experiment':ex}):
                result=train.run(arg)
            self.assertEqual(result['status'],'CHECKPOINT_SAVED')
            checkpoint=torch.load(root/'output/latest.pt',weights_only=True,map_location='cpu')
            self.assertEqual(checkpoint['step'],2)
            self.assertEqual(checkpoint['schema'],train.CHECKPOINT_SCHEMA)
            self.assertEqual(checkpoint['config']['Z_loss_weight'],0)
            self.assertTrue((root/'output/probe-step-000002.json').is_file())
            self.assertEqual(source.read_text(),'mock-source')
            self.assertEqual(parent.read_text(),'mock-parent')

    def test_teacher_not_actual_learned_head(self):
        self.assertTrue(hd.load_bindings(Path(__file__).parents[1]/'examples/hawk_dove_DEMO_ONLY.json')[1]['demo_only'])

if __name__=='__main__':unittest.main()
