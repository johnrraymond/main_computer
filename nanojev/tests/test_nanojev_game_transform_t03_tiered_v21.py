"""T03 V2.1: source truth, oracle isolation, immutable cutover and gated training."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools import nanojev_game_transform_t03_tiered_v21 as v2
from tools import nanojev_game_transform_ladder_map as ladder
from tools import nanojev_game_transform_experiment as ex


class GeneratorContract(unittest.TestCase):
    def test_native_bridge_is_byte_exact(self):
        from tools import nanojev_altreg_independent_math_qwen_precision_v2 as base
        from tools import nanojev_altreg_attention_curriculum_mix_v4 as mix
        from tools import nanojev_game_transform_level_probe as probe
        for orient in ('forward','reordered'):
            row=v2.bridge_case('T03a0',20261015,'train',2)
            self.assertEqual(v2.bridge_prompt('T03a0',row,orient,mix,base),
                             probe.native_prompt(mix,base,row,orient=='reordered'))

    def test_each_bridge_introduces_one_requirement_and_does_not_leak_y(self):
        from tools import nanojev_altreg_independent_math_qwen_precision_v2 as base
        from tools import nanojev_altreg_attention_curriculum_mix_v4 as mix
        for tier in v2.BRIDGE:
            for orientation in ('forward','reordered'):
                row=v2.bridge_case(tier,20261015,'train',3)
                self.assertEqual(row['y'],row['x']+row['data']['delta'])
                prompt=v2.bridge_prompt(tier,row,orientation,mix,base)
                self.assertIn('XXXXXX = ',prompt)
                self.assertIn('operation: Subtract YYYYYY from XXXXXX.',prompt)
                if tier in v2.BRIDGE[3:]:
                    self.assertIn('YYYYYY = [compute from expression]',prompt)
                    self.assertNotIn('YYYYYY = '+str(row['y'])+',',prompt)
                else:self.assertIn('YYYYYY = '+str(row['y'])+',',prompt)

    def test_expression_uses_only_native_register_assignment(self):
        from tools import nanojev_altreg_independent_math_qwen_precision_v2 as base
        from tools import nanojev_altreg_attention_curriculum_mix_v4 as mix
        for tier in ('T03a1','T03b0','T03b1','T03b2'):
            for orientation in ('forward','reordered'):
                row=v2.bridge_case(tier,20261015,'train',3)
                text=v2.bridge_prompt(tier,row,orientation,mix,base)
                expression=text.splitlines()[0]
                self.assertIn('OFFSET',expression)
                self.assertIn(f'OFFSET is {row["data"]["delta"]:+d}',expression)
                self.assertNotIn('YYYYYY =',expression)
                self.assertNotIn('XXXXXX =',expression)
                self.assertEqual(text.count('XXXXXX = '),1)
                self.assertEqual(text.count('YYYYYY = '),1)
                if tier=='T03a1':
                    self.assertIn('YYYYYY = '+base.registers.number_repr(row['y']),text)
                else:
                    self.assertIn('YYYYYY = [compute from expression]',text)
                    self.assertNotIn('YYYYYY = '+base.registers.number_repr(row['y']),text)

    def test_offset_echo_diagnostic_is_distinct_from_correct_y(self):
        # Offset=4, X=-2, Y=2: echoing offset is not correct arithmetic.
        case={'id':'one','x':-2,'y':2,'data':{'delta':4}}
        rec={'case_id':'one','status':'ok','truth':{'x':-2,'y':2}}
        echoes=[{**rec,'orientation':o,'prediction':[-2,4.03]}
                for o in ('forward','reordered')]
        got=v2.offset_echo_score(echoes,[case])
        self.assertEqual(got,{'n':1,'echo_rate':1.0,'y_exact_rate':0.0})
        computed=[{**rec,'orientation':o,'prediction':[-2,2.01]}
                  for o in ('forward','reordered')]
        got=v2.offset_echo_score(computed,[case])
        self.assertEqual(got,{'n':1,'echo_rate':0.0,'y_exact_rate':1.0})
        self.assertEqual(v2.offset_echo_score(computed[:1],[case])['n'],0)

    def test_disjoint_x_train_dev_test_and_distinct_case_oracles(self):
        for tier in v2.BRIDGE:
            observed={}
            for split in ('train','dev','test'):
                vals=[v2.bridge_case(tier,20261015,split,i) for i in range(240)]
                self.assertTrue(all(x['x']%3=={'train':0,'dev':1,'test':2}[split] for x in vals))
                self.assertTrue(all(x['x']+x['data']['delta']==x['y'] for x in vals))
                observed[split]={v['x'] for v in vals}
            self.assertFalse(observed['train'] & observed['dev'])
            self.assertFalse(observed['train'] & observed['test'])
            self.assertFalse(observed['dev'] & observed['test'])
            rows=v2.bridge_pool(tier,20261015,256)
            self.assertEqual(len(rows),min(256,v2.POOL_CAP[tier]))
            self.assertEqual(len({(r['x'],r['data']['delta']) for r in rows}),len(rows))

    def test_training_excludes_t08_and_restores_unmodified_trainer(self):
        self.assertTrue(all('T08' not in x and 'T03j' not in x for x in v2.STAGES))
        snap=(ladder.LEVELS,ladder.TRAINABLE,ladder.eligible_pools,ladder.contextual_prompt)
        with v2.install_adapter(20261015,64):
            self.assertIn('T03a0',ladder.TRAINABLE)
            self.assertIn('T03i',ladder.TRAINABLE)
            self.assertNotIn('T03j',ladder.TRAINABLE)
        self.assertEqual(snap,(ladder.LEVELS,ladder.TRAINABLE,ladder.eligible_pools,ladder.contextual_prompt))

    def test_default_interface_has_no_manual_checkpoint_or_report(self):
        args=v2.parser().parse_args([])
        self.assertEqual(args.action,'probe')
        self.assertIsNone(args.experimental_checkpoint)
        self.assertEqual(args.gate_limit,48)

    def test_existing_live_probe_routes_to_v2_only_when_available(self):
        from tools import nanojev_game_transform_t03_live_probe as live
        with TemporaryDirectory() as temp:
            root=Path(temp)
            folder=root/'NanoJev/runs/game_transform_t03_tiered_v21/stage-00'
            folder.mkdir(parents=True)
            (folder/'latest.pt').write_bytes(b'fake published checkpoint')
            old=root/'NanoJev/runs/game_transform_t03_tiered_v2/stage-00'
            old.mkdir(parents=True)
            (old/'latest.pt').write_bytes(b'old checkpoint')
            with patch.object(Path,'home',return_value=root),\
                 patch.object(v2,'run_probe') as v2_probe,\
                 patch.object(live,'run') as v1_probe:
                live.main([])
                v2_probe.assert_called_once()
                v1_probe.assert_not_called()
                live.main(['--run',str(root/'old-v1')])
                v1_probe.assert_called_once()


class PromotionContract(unittest.TestCase):
    @staticmethod
    def report(stage:int,pass_gate:bool):
        scores={}
        for tier in ('T00','T02f'):
            scores[f'baseline/dev/{tier}']={'0.1':{'n':48,'paired_xy':.99}}
            scores[f'trained/dev/{tier}']={'0.1':{'n':48,'paired_xy':.96 if pass_gate else .88}}
        for tier,(tol,metric,minimum) in v2.GATES[stage].items():
            scores[f'trained/dev/{tier}']={tol:{'n':48,metric:minimum+.02}}
        return {'scores':scores}

    def test_retention_regression_prevents_promotion(self):
        for stage in range(len(v2.STAGES)):
            self.assertTrue(v2.stage_gate(self.report(stage,True),stage)[0])
            ok,why=v2.stage_gate(self.report(stage,False),stage)
            self.assertFalse(ok)
            self.assertTrue(any('T00:retention' in s for s in why))

    def test_missing_dev_evidence_never_counts_as_pass(self):
        ok,reasons=v2.stage_gate({'scores':{}},0)
        self.assertFalse(ok)
        self.assertTrue(any('evidence_missing' in x for x in reasons))

    def test_integer_accuracy_is_two_orientation_and_absolute_at_zero(self):
        template={'case_id':'one','status':'ok','truth':{'x':0,'y':8}}
        a={**template,'orientation':'forward','prediction':[.49,8.49]}
        b={**template,'orientation':'reordered','prediction':[.49,8.51]}
        result=v2.integer_exact_score([a,b])
        self.assertEqual(result['n'],1)
        self.assertEqual(result['x'],1.)
        self.assertEqual(result['y'],0.)
        b['prediction']=[.49,8.49]
        result=v2.integer_exact_score([a,b])
        self.assertEqual(result['paired_xy'],1.)

    def test_incomplete_checkpoint_not_exposed_as_live_result(self):
        from tools import nanojev_game_transform_t03_live_probe as live
        with TemporaryDirectory() as root:
            p=Path(root)
            with self.assertRaises(FileNotFoundError):
                live.snapshot_checkpoint(p/'not-yet-saved.pt',p/'reports')


class CpuForkSmoke(unittest.TestCase):
    def test_cpu_fork_preserves_parent_and_runs_actual_optimizer_step(self):
        import torch
        from contextlib import redirect_stdout
        from io import StringIO
        from tools import nanojev_altreg_attention_curriculum_mix_v4 as mix
        from tools import nanojev_altreg_math_v3_decoder_experiment as v3
        from tools import nanojev_altreg_independent_math_qwen_precision_v2 as base

        class Parent(torch.nn.Module):
            def extract(self,flat):return torch.zeros(len(flat),16)
            def xy_coord(self,h):return torch.zeros(len(h),2)
        class Backbone(torch.nn.Module):pass
        class Tokenizer:
            def __call__(self,prompt,add_special_tokens=False):
                return {'input_ids':list(range(max(6,len(prompt)//12)))}
        def get_parent(*_):return ({'config':{'input_dim':96,'width':16}},None,Parent(),'parent_sha')
        def capture(_backbone,_tok,_device,prompts,_max):
            a=[];b=[]
            for prompt in prompts:
                digest=hashlib.sha256(prompt.encode()).digest()
                a.append(torch.tensor([(digest[i%32]-128)/128 for i in range(96)]))
                b.append(torch.tensor([[(digest[(i*7+j)%32]-128)/128 for j in range(24)]
                                       for i in range(8)]))
            return torch.stack(a),torch.stack(b),torch.ones(len(a),8,dtype=torch.bool)
        with TemporaryDirectory() as temp:
            root=Path(temp);db=root/'db.sqlite3';ex.generate(db,instances_per_game=3)
            torch.manual_seed(7)
            decoder=v3.V3Decoder(16,len(base.OP_IDS),'attention',6,32,24)
            op=mix.OperationHead(24,32)
            source=root/'source.pt'
            torch.save({'schema':mix.SCHEMA,'step':104500,
                'config':{'backbone':'qwen','parent_root':str(root),
                          'parent_checkpoint':'best','parent_sha256':'parent_sha',
                          'hidden':32,'max_prompt_tokens':512},
                'head':decoder.state_dict(),'operation_head':op.state_dict()},source)
            diagnostic=root/'diag.json';oldmap=root/'old-map.json'
            oldmap.write_text(json.dumps({'schema':ladder.SCHEMA,
                'phases':{'t02_focus':{'T02a':1,'retention':1}}}))
            parent=root/'parent';parent.mkdir()
            args=SimpleNamespace(database=db,source_checkpoint=source,
                 experimental_checkpoint=None,levels=','.join(ladder.DIAG_LEVELS),
                 limit_per_split=1,include_unchanged=False,report=diagnostic,
                 diagnostic=diagnostic,level_map=oldmap,phase='t02_focus',
                 output=parent,steps=1,save_every=1,report_every=1,
                 lr=1e-4,op_weight=0,seed=20261015,cache_per_level=1,
                 retention_cache=2,refresh_every=10,max_wall_hours=0,
                 device='cpu',dtype='float32',allow_model_download=False)
            with patch.object(v3,'get_parent',get_parent),patch.object(v3,'full_capture',capture),\
                 patch.object(base.cassette,'load_source',lambda *_:(None,Backbone(),Tokenizer(),'cpu',None)):
                with redirect_stdout(StringIO()):
                    ladder.diagnose(args)
                    ladder.train(args)
                cp=torch.load(parent/'latest.pt',map_location='cpu',weights_only=True)
                cp['step']=220000;torch.save(cp,parent/'latest.pt')
                parent_digest=ex.filehash(parent/'latest.pt')
                target=v2.parser().parse_args(['train','--device','cpu','--dtype','float32',
                    '--steps','220001','--cache-per-level','1',
                    '--pool-per-tier','8','--save-every','1','--report-every','1'])
                target.output=root/'v2';target.from_output=parent
                target.database=db;target.source_checkpoint=source;target.diagnostic=diagnostic
                with redirect_stdout(StringIO()):
                    result=v2.train_chunk(target,0,220001)
                self.assertEqual(result['step'],220001)
                self.assertEqual(ex.filehash(parent/'latest.pt'),parent_digest)
                child=target.output/'stage-00'/'latest.pt'
                trained=torch.load(child,map_location='cpu',weights_only=True)
                self.assertEqual([x['phase'] for x in trained['phase_history']],
                                 ['t02_focus','t03_v21_s0'])
                self.assertIn('optimizer',trained)
                # End-to-end read-only retention + bridge probe on the fork.
                with redirect_stdout(StringIO()):
                    report=v2.evaluate(target,child,2)
                self.assertEqual(report['steps'],{'baseline':220000,'trained':220001})
                for tier in ('T00','T02f','T03a0','T03a1','T03b0'):
                    self.assertIn('trained/dev/'+tier,report['scores'])
                self.assertTrue(all('prediction' not in x for x in report['scores'].values()))
                self.assertIn('offset_diagnostic',report['scores']['trained/dev/T03b0'])
                self.assertIn('offset',report['examples']['T03b0'][0])
                self.assertFalse((target.output/'stage-01').exists())
                # The next phase forks the *trained* checkpoint with the same
                # optimizer and creates its own sealed phase map.
                v2.prepare_stage(1,target,220002)
                next_cp=torch.load(target.output/'stage-01'/'latest.pt',
                                   map_location='cpu',weights_only=True)
                self.assertEqual(next_cp['step'],220001)
                self.assertEqual(ex.filehash(child),
                    ex.filehash(target.output/'stage-01'/'latest.pt'))
                with redirect_stdout(StringIO()):
                    advanced=v2.train_chunk(target,1,220002)
                    resumed=v2.train_chunk(target,1,220003)
                self.assertEqual((advanced['step'],resumed['step']), (220002,220003))
                next_cp=torch.load(target.output/'stage-01'/'latest.pt',
                                   map_location='cpu',weights_only=True)
                self.assertEqual([x['phase'] for x in next_cp['phase_history']],
                    ['t02_focus','t03_v21_s0','t03_v21_s1'])
                self.assertEqual(ex.filehash(parent/'latest.pt'),parent_digest)
                with self.assertRaisesRegex(RuntimeError,'T03_V21_CACHE_DRIFT'):
                    target.refresh_every=999
                    v2.prepare_stage(0,target,220002)
                target.refresh_every=1000
                target.chunk_steps=1;target.gate_limit=2;target.steps=220005
                synthetic_scores={}
                for tier in ('T00','T02f'):
                    synthetic_scores[f'baseline/dev/{tier}']={'0.1':{'n':2,'paired_xy':1.}}
                    synthetic_scores[f'trained/dev/{tier}']={'0.1':{'n':2,'paired_xy':1.}}
                for tier,(tol,metric,minimum) in v2.GATES[1].items():
                    synthetic_scores[f'trained/dev/{tier}']={tol:{'n':2,metric:1.}}
                def fake_evaluate(_args,_checkpoint,_limit,report_path,**filters):
                    self.assertEqual(filters['splits'],('dev',))
                    ex.write_json(report_path,{'scores':synthetic_scores})
                    return {'scores':synthetic_scores}
                with patch.object(v2,'evaluate',side_effect=fake_evaluate),redirect_stdout(StringIO()):
                    progressed=v2.train(target)
                self.assertEqual(progressed['step'],220005)
                self.assertEqual(progressed['stage'],2)
                self.assertTrue((target.output/'stage-02'/'latest.pt').is_file())
                parent_of_next=torch.load(target.output/'stage-02'/'latest.pt',
                                          weights_only=True,map_location='cpu')
                self.assertEqual(parent_of_next['step'],220005)
                self.assertEqual(ex.filehash(parent/'latest.pt'),parent_digest)


if __name__=='__main__':unittest.main()
