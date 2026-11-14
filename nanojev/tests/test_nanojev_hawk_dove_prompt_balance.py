"""Cheap and deterministic control tests; no GPU or pretrained model required."""
import random
import json
import unittest
from pathlib import Path
from unittest import mock

from tools import nanojev_hawk_dove_prompt_balance as bal
from tools import nanojev_hawk_dove_million_train as trainer
from tools import nanojev_hawk_dove_million as hd


class PromptBalanceTests(unittest.TestCase):
    def make_rows(self):
        return [{'case_id':f'{k}/T02/{m}', 'tier':'T02','mask':m,
                 'prompt':f'prompt-{k}-{m}', 'x':1000., 'y':2000.}
                for k in range(4) for m in range(4)]

    def test_priority_and_alias_distribution(self):
        self.assertEqual(bal.priority(None),1.0)
        self.assertEqual(bal.priority(.05),.15)
        self.assertEqual(bal.priority(1.),6.)
        probabilities, aliases=bal.alias_table([1.,1.,1.,6.])
        rng=random.Random(22)
        distribution=[0,0,0,0]
        for _ in range(50000):
            column=rng.randrange(4)
            pick=column if rng.random()<probabilities[column] else aliases[column]
            distribution[pick]+=1
        self.assertTrue(.64 < distribution[3]/50000 < .69,distribution)

    def test_focus_failed_prompts_and_keep_uniform_coverage(self):
        rows=self.make_rows()
        learner=bal.PromptBalance('teacher-1')
        learner.build('T02',rows)
        for row in rows:
            if row['case_id']==rows[-1]['case_id']:
                learner.observe(row,750,2000) # failing X
            else:
                learner.observe(row,1000,2000) # correct X/Y
        report=learner.commit('T02',300)
        self.assertEqual(report['measured'],16)
        self.assertEqual(report['measured_passing'],15)
        self.assertEqual(report['measured_failing'],1)
        rng=random.Random(19)
        choices=[learner.choose('T02',rng,rows)['case_id'] for _ in range(10000)]
        concentration=choices.count(rows[-1]['case_id'])/len(choices)
        self.assertGreater(concentration,.40)
        self.assertLess(concentration,.82)
        self.assertGreater(len(set(choices)),12)  # Old successes still refreshed.
        for _ in range(4):learner.observe(rows[-1],1000.,2000.)
        refreshed=learner.commit('T02',600)
        self.assertEqual(refreshed['measured_failing'],1) # EMA still decaying, not falsely reset
        assert learner.records['T02'][rows[-1]['case_id']]['error'] > .05
        for step in range(900,2400,300):
            learner.observe(rows[-1],1000.,2000.)
            learner.commit('T02',step)
        self.assertLessEqual(learner.records['T02'][rows[-1]['case_id']]['error'],.05)
        snapshot=learner.snapshot()
        revived=bal.PromptBalance('teacher-1',snapshot)
        revived.build('T02',rows)
        self.assertEqual(revived.records,learner.records)
        with self.assertRaisesRegex(ValueError,'LINEAGE_DRIFT'):
            bal.PromptBalance('teacher-other',snapshot)

    def test_samplers_are_independent_of_dev_test_and_tier_gates(self):
        rows=self.make_rows()
        learner=bal.PromptBalance('teacher-1')
        with mock.patch.object(hd,'read_rows',return_value=rows):
            old=trainer.choose_rows(Path('none'),'T02',414,20261019,4)
            unchanged=trainer.choose_rows(Path('none'),'T02',414,20261019,4,None,None)
            self.assertEqual(old,unchanged)
            updated=trainer.choose_rows(Path('none'),'T02',414,20261019,4,None,learner)
            self.assertEqual(len(updated),4)
            self.assertEqual({r['tier'] for r in updated},{'T02'})
        self.assertNotIn('T01',learner.records)

    def test_pending_does_not_serialize_as_committed(self):
        rows=self.make_rows()
        learner=bal.PromptBalance('h')
        learner.observe(rows[0],1000.,2000.)
        with self.assertRaisesRegex(ValueError,'PENDING_CHECKPOINT'):
            learner.snapshot()
        learner.commit('T02',300)
        self.assertEqual(learner.snapshot()['last_commit_step'],300)

class AdaptiveCheckpointIntegrationTests(unittest.TestCase):
    def test_train_and_resume_without_reentering_flag(self):
        import tempfile,sys
        from types import ModuleType
        import torch
        from torch import nn
        from tools import nanojev_hawk_dove_million as hd
        from tools import nanojev_hawk_dove_million_train as train
        import torch._dynamo
        fixture=Path(__file__).resolve().parents[1]/'examples/hawk_dove_million_DEMO_ONLY.json'
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);db=root/'teacher.sqlite3'
            hd.build_database(db,fixture,instances=41)
            source=root/'source.pt';source.write_bytes(b'fake-source')
            parent=root/'parent.pt';parent.write_bytes(b'fake-parent')
            class Decoder(nn.Module):
                def __init__(self):
                    super().__init__();self.weight=nn.Parameter(torch.tensor(.2))
            class Head:
                @staticmethod
                def objective(out,truth,base,arm):
                    return (out['fraction'].mean()-torch.tanh(truth.mean()/3000000.))**2,None
                @staticmethod
                def compose_logits(s,e,f,maxv):
                    return torch.pow(10.,e.argmax(-1).float()+f).clamp(max=maxv)
            rt=ModuleType('tools.nanojev_game_math_curriculum_v10_runtime')
            ex=ModuleType('tools.nanojev_game_transform_experiment')
            def load(source,parent,**kw):
                return {'decoder':Decoder(),'gamehead':nn.Linear(1,1),
                        'v3':Head(),'base':None,'device':'cpu'},None,'fake'
            def forward(model,prompts):
                n=len(prompts)
                sign=torch.zeros(n,2,3);sign[:,:,2]=1
                exponent=torch.zeros(n,2,7);exponent[:,:,6]=1
                fraction=torch.sigmoid(model['decoder'].weight).expand(n,2)
                return {'sign':sign,'exponent':exponent,'fraction':fraction},None
            rt.load_model=load;rt.token_eligible=lambda model,prompt: True
            ex.load_current=lambda source,device,dtype,allow_model_download,seed:load(source,None)[0]
            ex.forward=forward
            args=train.make_parser().parse_args(['train','--teacher',str(db),
                '--source',str(source),'--parent',str(parent),'--output',str(root/'isolated'),
                '--device','cpu','--dtype','float32','--steps','2',
                '--chunk-steps','1','--views','2','--probe-per-tier','16',
                '--adaptive-prompts','--tier-gate','T02=70'])
            with mock.patch.dict(sys.modules,{'tools.nanojev_game_math_curriculum_v10_runtime':rt,
                                               'tools.nanojev_game_transform_experiment':ex}):
                result=train.run(args)
                self.assertEqual(result['step'],2)
                first=torch.load(root/'isolated'/'latest.pt',weights_only=True)
                self.assertIn('prompt_balance',first)
                self.assertEqual(first['prompt_balance']['last_commit_step'],2)
                self.assertTrue(first['prompt_balance']['records'])
                args.steps=3;args.adaptive_prompts=False
                resumed=train.run(args)
                self.assertEqual(resumed['step'],3)
                second=torch.load(root/'isolated'/'latest.pt',weights_only=True)
                self.assertIn('prompt_balance',second)
                self.assertEqual(second['prompt_balance']['last_commit_step'],3)
                self.assertEqual(first['config'],second['config'])
                self.assertEqual(json.loads((root/'isolated'/'tier-gates.json').read_text())['gate_percent_by_tier']['T02'],70)

class InstallerTests(unittest.TestCase):
    def test_guarded_source_preserving_upgrade(self):
        import zipfile,tempfile,hashlib,ast
        from tools import apply_nanojev_hawk_dove_prompt_balancer as installer
        reference=Path('/mnt/data/main_computer_test-20261019-004727.zip')
        if not reference.exists(): self.skipTest('reference ZIP only available in authoring session')
        with zipfile.ZipFile(reference) as z:
            source=z.read('main_computer_test\\tools\\nanojev_hawk_dove_million_train.py')
        original=source.decode('utf8')
        altered=installer.transformed(original,hashlib.sha256(source).hexdigest())
        self.assertIn('ADAPTIVE_BASE_TRAINER_SHA256',altered)
        self.assertIn('adaptive.observe(row,px,py)',altered)
        self.assertEqual(altered.count('report=probe(model,args.teacher,args.probe_per_tier,args.seed,gates)'),1)
        ast.parse(altered)
        # Unrelated runtime/cache instrumentation survives byte-for-byte.
        marker='\n# custom-cache-instrumentation-must-survive\n'
        updated=installer.transformed(original+marker,hashlib.sha256((original+marker).encode()).hexdigest())
        self.assertTrue(updated.endswith(marker))
        with self.assertRaisesRegex(ValueError,'INCOMPATIBLE_TRAINER'):
            installer.transformed(original.replace('def choose_rows(', 'def custom_rows('),
                                  hashlib.sha256(source).hexdigest())


if __name__ == '__main__':
    unittest.main()
