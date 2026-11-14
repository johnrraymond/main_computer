"""Deterministic, offline contract tests; no downloaded model or GPU required."""
from pathlib import Path
import hashlib
import importlib
import json
import math
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import numpy as np
import torch
import nanojev_altreg_attention_curriculum_mix_v4 as module

class CurriculumTests(unittest.TestCase):
    def test_preview_baseline_exact(self):
        for reverse in (False,True):
            self.assertEqual(module.curriculum_prompt(17,-3,'sym_sub','registered_logbar',reverse),
                             module.base.prompt(17,-3,'sym_sub',reverse))

    def test_complete_operation_coverage(self):
        for op in module.base.OPERATIONS:
            for stage in module.STAGES:
                for reverse in (False,True):
                    text=module.curriculum_prompt(17,-3,op,stage,reverse)
                    self.assertIn('Answer:',text)
                    self.assertTrue(text.strip())

    def test_per_stage_scaffolding(self):
        for reverse in (False,True):
            current=module.curriculum_prompt(17,-3,'sym_add','registered_logbar',reverse)
            raw=module.curriculum_prompt(17,-3,'sym_add','one_raw_operand',reverse)
            no_bar=module.curriculum_prompt(17,-3,'sym_add','one_logbar_removed',reverse)
            mixed=module.curriculum_prompt(17,-3,'sym_add','one_raw_no_logbar',reverse)
            plain=module.curriculum_prompt(17,-3,'sym_add','plain_expression',reverse)
            self.assertIn(module.base.registers.FIRST,current)
            self.assertIn(module.base.registers.SECOND,current)
            self.assertEqual(sum(x in raw for x in (module.base.registers.FIRST,module.base.registers.SECOND)),1)
            self.assertIn(module.base.registers.FIRST,no_bar)
            self.assertIn(module.base.registers.SECOND,no_bar)
            self.assertEqual(sum(x in mixed for x in (module.base.registers.FIRST,module.base.registers.SECOND)),1)
            for text in (plain,):
                self.assertNotIn(module.base.registers.FIRST,text)
                self.assertNotIn(module.base.registers.SECOND,text)
                self.assertNotIn('LogBar',text)
                self.assertNotIn('|',text)
            self.assertEqual(len(module.STAGES),5)
        self.assertEqual(set(module.STAGES),{'registered_logbar','one_raw_operand','one_logbar_removed','one_raw_no_logbar','plain_expression'})
        self.assertNotIn('story_problem',module.STAGES)

    def test_no_operation_side_channel_on_raw_tiers(self):
        a={'operation':'sym_add'};b={'operation':'sym_multiply'}
        for stage in ('plain_expression',):
            values=module.operation_ids([a,b],[stage,stage],'cpu')
            self.assertEqual(values.tolist(),[0,0],stage)
        for stage in module.STAGES[:4]:
            values=module.operation_ids([a,b],[stage,stage],'cpu')
            self.assertEqual(values.tolist(),[module.base.OP_IDS['sym_add'],
                module.base.OP_IDS['sym_multiply']],stage)
        self.assertRaises(ValueError,module.operation_ids,[a],['registered_logbar','plain_expression'],'cpu')

    def test_semantic_operation_targets_and_catalog(self):
        self.assertEqual(set(module.OP_GROUPS),set(module.base.OPERATIONS))
        self.assertEqual(len(set(module.OP_CLASSES)),len(module.OP_CLASSES))
        self.assertEqual(module.operation_targets([{'operation':'sym_add'},
            {'operation':'written_add'}, {'operation':'algebra_difference'}],'cpu').tolist(),
            [module.OP_INDEX['add']]*3)
        self.assertEqual(module.operation_targets([{'operation':'sym_sub'},
            {'operation':'sym_reverse_sub'}],'cpu').tolist(),
            [module.OP_INDEX['subtract'],module.OP_INDEX['reverse_subtract']])

    def test_operation_classifier_gradients(self):
        head=module.OperationHead(8,16)
        tokens=torch.randn(3,7,8)
        mask=torch.tensor([[1,1,1,0,0,0,0],[1,1,1,1,1,1,1],[1,1,0,0,0,0,0]],dtype=torch.bool)
        logits=head(tokens,mask)
        self.assertEqual(tuple(logits.shape),(3,len(module.OP_CLASSES)))
        loss=module.F.cross_entropy(logits,torch.tensor([0,1,2]))
        loss.backward()
        self.assertTrue(any(p.grad is not None and bool((p.grad!=0).any()) for p in head.parameters()))

    def test_semantic_operation_aliases(self):
        for op1,op2 in [('sym_add','written_add'),('sym_sub','written_sub'),
                        ('sym_multiply','written_product')]:
            self.assertEqual(module.OP_GROUPS[op1],module.OP_GROUPS[op2])

    def test_exact_counts_and_phase_boundaries(self):
        rng = __import__('random').Random(19)
        for counts in [(16,0,0,0,0), (4,3,3,3,3), (0,0,0,0,16)]:
            for _ in range(10):
                stages = module.exact_batch_stages(counts,rng)
                self.assertEqual(len(stages),16)
                self.assertEqual([stages.count(stage) for stage in module.STAGES],list(counts))
        phases=((16,0,0,0,0),(4,3,3,3,3))
        self.assertEqual(module.phase_counts(1,2000,phases),(0,phases[0]))
        self.assertEqual(module.phase_counts(2000,2000,phases),(0,phases[0]))
        self.assertEqual(module.phase_counts(2001,2000,phases),(1,phases[1]))
        self.assertEqual(module.phase_counts(100000,2000,phases),(1,phases[1]))

    def test_argument_selection_and_validation(self):
        parse=module.parser().parse_args
        self.assertEqual(module.resolve_phases(parse(['--mix-counts','4,3,3,3,3'])),((4,3,3,3,3),))
        self.assertEqual(module.resolve_phases(parse(['--phase-counts','16,0,0,0,0',
                                                     '--phase-counts','8,4,4,0,0'])),
                         ((16,0,0,0,0),(8,4,4,0,0)))
        with self.assertRaisesRegex(ValueError,'CURRICULUM_SELECT_MIX_COUNTS_OR_PHASE_COUNTS'):
            module.resolve_phases(parse(['--mix-counts','16,0,0,0,0','--phase-counts','8,4,4,0,0']))
        for invalid in ['-1,17,0,0,0','4,3,3,3,2','3,3,3,3','1,2,cat,4,9']:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    module.resolve_phases(parse(['--mix-counts='+invalid]))
        with self.assertRaisesRegex(ValueError,'CURRICULUM_EXPLICIT_COUNTS'):
            module.resolve_phases(parse(['--batch-size','8']))

    def test_full_bank_traversal_and_legacy_draw_unchanged(self):
        class Bank:
            def __init__(self):
                self.rows={('train','story_semantic_no_op'):[
                    ({'a':i,'b':0,'operation':'sym_add','prompt':f'forward {i}'},
                     {'a':i,'b':0,'operation':'sym_add','prompt':f'reverse {i}'})
                    for i in range(4096)]}
            def draw_with_partner(self,*args):return module.CaptainBank.draw_with_partner(self,*args)
            def draw(self,*args):return module.CaptainBank.draw(self,*args)
        bank=Bank()
        # Old sampler has 256 distinct indices for this fixed Captain slot.
        legacy={bank.draw('train','story_semantic_no_op',step,2,16)['a']
                for step in range(1,4097)}
        revised={bank.draw_with_partner('train','story_semantic_no_op',step,2,16)[0]['a']
                 for step in range(1,4097)}
        self.assertEqual(len(legacy),256)
        self.assertEqual(len(revised),4096)
        self.assertEqual(module.captain_coprime_stride(4096,16),17)
        self.assertEqual(math.gcd(module.captain_coprime_stride(45,16),45),1)

    def test_extra_semantic_samples_are_distinct_pairs(self):
        class Bank:
            def __init__(self):
                self.rows={('train','story_semantic_no_op'):[
                    ({'a':i,'b':-i,'operation':'sym_add','prompt':f'forward {i}'},
                     {'a':i,'b':-i,'operation':'sym_add','prompt':f'reverse {i}'})
                    for i in range(4096)]}
            def draw_with_partner(self,*args):return module.CaptainBank.draw_with_partner(self,*args)
        prompts,examples,stages=module.focus_semantic_prompts(
            Bank(),'story_semantic_no_op',60001,3,16)
        self.assertEqual(len(prompts),4)
        self.assertEqual(len({x['a'] for x in examples}),2)
        self.assertEqual(examples[0],examples[1]);self.assertEqual(examples[2],examples[3])
        self.assertTrue(all(name=='captain:story_semantic_no_op' for name in stages))
        self.assertEqual(module.focused_semantic_variant({'plain_expression':1,'story_numeric_no_op':1,
                                                          'story_semantic_no_op':1}),'story_semantic_no_op')
        milestones=[{'start_step':52001,'counts':{'story_numeric_x_semantic_y':1}},
                    {'start_step':56001,'counts':{'story_semantic_with_op':1}},
                    {'start_step':60001,'counts':{'story_semantic_no_op':1}}]
        self.assertEqual(module.previous_semantic_variant(milestones,60001,'story_semantic_no_op'),
                         'story_semantic_with_op')
        self.assertIsNone(module.previous_semantic_variant(milestones,52001,'story_numeric_x_semantic_y'))

    def test_captain_partner_is_same_example_not_new_random_draw(self):
        class Bank:
            def __init__(self):self.rows={}
            def draw_with_partner(self,*args):return module.CaptainBank.draw_with_partner(self,*args)
            def draw(self,*args):return module.CaptainBank.draw(self,*args)
        bank=Bank()
        bank.rows[('train','story_numeric_x_semantic_y')]=[
            ({'a':17,'b':-3,'operation':'sym_sub','prompt':'forward story'},
             {'a':17,'b':-3,'operation':'sym_sub','prompt':'reversed story'})]
        first = module.CaptainBank.draw(bank,'train','story_numeric_x_semantic_y',2,0,16)
        selected,other = module.CaptainBank.draw_with_partner(
            bank,'train','story_numeric_x_semantic_y',2,0,16)
        self.assertEqual(first,selected)
        self.assertNotEqual(selected['prompt'],other['prompt'])
        self.assertEqual((selected['a'],selected['b'],selected['operation']),
                         (other['a'],other['b'],other['operation']))
        stages=['plain_expression','registered_logbar']
        examples=[{'a':1,'b':2,'operation':'sym_add'}]*2
        result=module.overlay_batch(stages,examples,{'story_numeric_x_semantic_y':1},bank,2,2,
                                    pair_prompts=True)
        names,modified,prompts,partners=result
        self.assertEqual(len(partners),1)
        self.assertEqual(modified[partners[0][0]]['a'],17)
        self.assertEqual(modified[partners[0][0]]['b'],-3)
        self.assertNotEqual(prompts[partners[0][0]],partners[0][1])
        self.assertEqual(len(module.overlay_batch(stages,examples,
            {'story_numeric_x_semantic_y':1},bank,2,2)),3)

    def test_averaging_cannot_hide_or_corrupt_paired_metric(self):
        targets=np.asarray([[10.,20.],[10.,20.],[10.,20.]])
        original=np.asarray([[10.,20.],[10.,20.],[10.,20.]])
        reversed=np.asarray([[100000.,20.],[-10.,20.],[10.,20.]])
        # Prediction 0 has an extreme reversed exponent. Prediction 1 is an X
        # sign error in reversed only. Paired correctness must reject both.
        predicted={'original':original,'reversed':reversed,
                   'averaged':(original+reversed)/2}
        logits=np.asarray([[10.,0.]]*3)
        classified={'original':logits,'reversed':logits}
        score=module.captain_paired_accuracy(predicted,classified,targets,np.zeros(3,dtype=int))
        self.assertAlmostEqual(score['joint_5pct'],1/3)
        self.assertAlmostEqual(score['operand_5pct'],4/6)
        self.assertAlmostEqual(score['joint_5pct_and_operation'],1/3)
        self.assertGreater(score['mean_abs_relative_error'],1000)
        self.assertEqual(score['operation_accuracy'],1.)

    def test_partner_supervision_backpropagates_to_decoder(self):
        class Parent:
            def extract(self,x):return x[:,:4]
            def xy_coord(self,h):return h[:,:2]
        class Decoder(torch.nn.Module):
            def __init__(self):
                super().__init__();self.scale=torch.nn.Parameter(torch.tensor(0.4))
            def forward(self,h,xy,op,seq,mask):
                n=len(h)
                return {'sign':self.scale*torch.ones(n,2,3),
                        'exponent':self.scale*torch.ones(n,2,7),
                        'fraction':self.scale*torch.ones(n,2)}
        def fake_capture(*args):
            n=len(args[3]);return torch.ones(n,8),torch.ones(n,4,3),torch.ones(n,4,dtype=torch.bool)
        decoder=Decoder()
        with patch.object(module.v3,'full_capture',fake_capture):
            loss=module.paired_captain_target_loss(
                decoder,Parent(),object(),object(),'cpu',[(0,'reordered prompt')],
                [{'a':17,'b':-3,'operation':'sym_add'}],['captain:story_raw'],512)
        self.assertTrue(torch.isfinite(loss))
        loss.backward()
        self.assertIsNotNone(decoder.scale.grad)
        self.assertTrue(bool(torch.isfinite(decoder.scale.grad)))

    def test_sidecar_pinned_at_existing_checkpoint_and_auto_resumed(self):
        from types import SimpleNamespace
        bank=SimpleNamespace(sha256='a'*64)
        cap={'database_sha256':bank.sha256}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            flag=SimpleNamespace(resume=True,captain_paired_supervision=True)
            with self.assertRaisesRegex(RuntimeError,'REQUIRES_CAPTAIN_CHECKPOINT_RESUME'):
                module.captain_paired_plan(flag,root,0,cap,bank)
            first=module.captain_paired_plan(flag,root,49500,cap,bank)
            self.assertEqual(first['installed_after_step'],49500)
            self.assertEqual(first['target_weight'],1.)
            self.assertEqual(first['sampler'],'coprime-full-bank-v2')
            self.assertEqual(first['focus_pair_count'],2)
            self.assertEqual(first['replay_pair_count'],1)
            # A resumed command need not repeat the opt-in flag. The sidecar
            # keeps the loss activated, preventing silent mixed-lineage training.
            flag.captain_paired_supervision=False
            continued=module.captain_paired_plan(flag,root,50000,cap,bank)
            self.assertEqual(continued,first)
            with self.assertRaisesRegex(RuntimeError,'PAIRED_PLAN_DRIFT'):
                module.captain_paired_plan(flag,root,49499,cap,bank)
            with self.assertRaisesRegex(RuntimeError,'PAIRED_PLAN_DRIFT'):
                module.captain_paired_plan(flag,root,50000,cap,SimpleNamespace(sha256='b'*64))

    def test_paired_resume_integration_without_gpu(self):
        class Parent:
            def eval(self):return self
            def extract(self,x):return torch.zeros(len(x),4)
            def xy_coord(self,h):return torch.zeros(len(h),2)
        class Frozen(torch.nn.Module):pass
        class Stream:
            def __init__(self,seed,reserved):self.draws=0
            def get(self):self.draws+=1;return {'a':17,'b':-3,'operation':'sym_add'}
            def state_dict(self):return {'draws':self.draws}
            def load_state_dict(self,s):self.draws=s['draws']
        class FakeBank:
            sha256='f'*64
            def draw_with_partner(self,split,variant,step,rank,batch_size):
                f={'a':17,'b':-3,'operation':'sym_add','prompt':'forward example'}
                r={**f,'prompt':'reverse example'}
                return (f,r) if step%2 else (r,f)
            def draw(self,*args):return self.draw_with_partner(*args)[0]
            def validation_pairs(self,variant,limit):
                return [(self.draw_with_partner('dev',variant,j,0,2)) for j in range(limit)]
        def capture(backbone,tokenizer,device,prompts,max_tokens):
            n=len(prompts)
            return torch.randn(n,32),torch.randn(n,5,8),torch.ones(n,5,dtype=torch.bool)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';out=root/'out';parent_root=root/'parent'
            source.mkdir();parent_root.mkdir()
            (parent_root/'best-precision.pt').write_bytes(b'fixed-parent')
            parent_sha=hashlib.sha256(b'fixed-parent').hexdigest()
            decoder=module.v3.V3Decoder(4,len(module.base.OP_IDS),'attention',6,16,8)
            cfg={'arm':'attention','backbone':'qwen','hidden':16,
                 'parent_root':str(parent_root),'parent_checkpoint':'best-precision',
                 'parent_sha256':parent_sha,'max_prompt_tokens':512}
            torch.save({'schema':module.v3.SCHEMA,'config':cfg,'step':300,
                        'head':decoder.state_dict()},source/'best.pt')
            cache={'validation':{'examples':[{'a':17,'b':-3,'operation':'sym_add'}]*2}}
            parent_get=lambda *a:({'config':{'input_dim':32,'width':4,'seed':23}},cache,Parent(),parent_sha)
            args=module.parser().parse_args(['--source-root',str(source),'--output-root',str(out),
                    '--device','cpu','--steps','1','--phase-steps','1','--batch-size','2',
                    '--eval-every','1','--validation-examples','2',
                    '--mix-counts','0,0,0,0,2','--captain-milestone','1:story_raw=1',
                    '--captain-eval-examples','2'])
            bank=FakeBank()
            pinned={'milestones':[{'start_step':1,'counts':{'story_raw':1}}],
                    'database_sha256':bank.sha256,'eval_examples':2,'installed_after_step':0}
            with patch.object(module.v3,'get_parent',parent_get), \
                 patch.object(module.v3,'full_capture',capture), \
                 patch.object(module.base.cassette,'load_source',lambda *a:(None,Frozen(),object(),'cpu',None)), \
                 patch.object(module.base.registers,'build_plan',lambda *a:(None,[])), \
                 patch.object(module.base,'Stream',Stream), \
                 patch.object(module,'captain_plan',lambda *a,**kw:(pinned,bank)):
                self.assertEqual(module.run(args),0)
                early=torch.load(out/'latest.pt',weights_only=True)
                self.assertNotIn('selection_metric',early['config'])
                args.steps=2;args.resume=True;args.captain_paired_supervision=True
                self.assertEqual(module.run(args),0)
                second=torch.load(out/'latest.pt',weights_only=True)
                self.assertEqual(second['config'],early['config'])
                self.assertEqual(second['step'],2)
                self.assertEqual(second['best']['selection_metric'],'captain-semantic-paired-focus-v2')
                self.assertEqual(json.loads((out/'validation-step-00000002.json').read_text())['selection_metric'],
                                 'captain-semantic-paired-focus-v2')
                self.assertTrue((out/module.PAIRED_PLAN_FILE).is_file())
                self.assertTrue((out/'latest-pre-paired.pt').is_file())
                self.assertEqual(torch.load(out/'latest-pre-paired.pt',weights_only=True)['step'],1)
                self.assertTrue((out/'best-pre-paired.pt').is_file())
                args.captain_paired_supervision=False;args.steps=3
                self.assertEqual(module.run(args),0)
                self.assertEqual(torch.load(out/'latest.pt',weights_only=True)['step'],3)
                # Opt in to a fixed composition: old pinned milestones are
                # ignored, but remain on disk for historical provenance.
                args.captain_milestone=[]
                args.training_mix='plain_expression=1,captain:story_semantic_no_op=1'
                args.steps=4
                self.assertEqual(module.run(args),0)
                fourth=json.loads((out/'validation-step-00000004.json').read_text())
                self.assertEqual(fourth['selection_metric'],'fixed-paired-joint-5pct-v3')
                self.assertEqual(fourth['effective_mix_counts'],
                                 {'plain_expression':1,'captain:story_semantic_no_op':1})
                self.assertTrue(fourth['fixed_breakdown_sha256'])
                self.assertEqual(json.loads((out/module.FIXED_BREAKDOWN_FILE).read_text())[
                                 'additional_focus_examples'],0)
                self.assertTrue((out/'latest-pre-breakdown.pt').is_file())
                args.training_mix=None;args.steps=5
                self.assertEqual(module.run(args),0)
                fifth=json.loads((out/'validation-step-00000005.json').read_text())
                self.assertEqual(fifth['effective_mix_counts'],fourth['effective_mix_counts'])
                self.assertEqual(fifth['fixed_breakdown_sha256'],fourth['fixed_breakdown_sha256'])
                args.training_mix='captain:story_raw=2';args.steps=6
                self.assertEqual(module.run(args),0)
                sixth=json.loads((out/'validation-step-00000006.json').read_text())
                self.assertEqual(sixth['effective_mix_counts'],{'captain:story_raw':2})
                self.assertNotEqual(sixth['fixed_breakdown_sha256'],fifth['fixed_breakdown_sha256'])
                self.assertEqual(len(json.loads((out/module.FIXED_BREAKDOWN_FILE).read_text())['history']),1)

    def test_independent_source_and_resume(self):
        class Parent:
            def eval(self):return self
            def extract(self,flat):return torch.zeros(len(flat),4)
            def xy_coord(self,h):return torch.zeros(len(h),2)
        class Frozen(torch.nn.Module):
            pass
        class Stream:
            def __init__(self,seed,reserved):self.draws=0
            def get(self):self.draws+=1;return {'a':17,'b':-3,'operation':'sym_add'}
            def state_dict(self):return {'draws':self.draws}
            def load_state_dict(self,data):self.draws=data['draws']
        def captures(backbone,tokenizer,device,prompts,max_tokens):
            count=len(prompts)
            return (torch.randn(count,32),torch.randn(count,5,8),torch.ones(count,5,dtype=torch.bool))
        with tempfile.TemporaryDirectory() as temporary:
            t=Path(temporary);source=t/'source';output=t/'curriculum';parent_root=t/'parent'
            source.mkdir();parent_root.mkdir();(parent_root/'best-precision.pt').write_bytes(b'fixed-parent')
            parent_sha=hashlib.sha256(b'fixed-parent').hexdigest()
            head=module.v3.V3Decoder(4,len(module.base.OP_IDS),'attention',6,16,8)
            cp={'schema':module.v3.SCHEMA,'config':{'arm':'attention','backbone':'qwen',
                'hidden':16,'parent_root':str(parent_root),'parent_checkpoint':'best-precision',
                'parent_sha256':parent_sha,'max_prompt_tokens':512},'step':300,'head':head.state_dict()}
            torch.save(cp,source/'best.pt')
            cache={'validation':{'examples':[{'a':17,'b':-3,'operation':'sym_add'},
                                              {'a':8,'b':2,'operation':'sym_sub'}]}}
            get_parent=lambda base,args:( {'config':{'input_dim':32,'width':4,'seed':23}},cache,Parent(),parent_sha)
            args=module.parser().parse_args(['--source-root',str(source),'--output-root',str(output),
                '--device','cpu','--steps','1','--phase-steps','1','--batch-size','2',
                '--eval-every','1','--validation-examples','2',
                '--mix-counts','1,0,0,0,1'])
            with patch.object(module.v3,'get_parent',get_parent),\
                 patch.object(module.v3,'full_capture',captures),\
                 patch.object(module.base.cassette,'load_source',lambda *a:(None,Frozen(),object(),'cpu',None)),\
                 patch.object(module.base.registers,'build_plan',lambda *a:(None,[])),\
                 patch.object(module.base,'Stream',Stream):
                self.assertEqual(module.run(args),0)
                saved=torch.load(output/'latest.pt',weights_only=True)
                self.assertEqual(saved['step'],1)
                self.assertIn('operation_head',saved)
                self.assertIn('operation_loss_weight',saved['config'])
                score=json.loads((output/'validation-step-00000001.json').read_text())
                self.assertIn('operation_accuracy',score['validation']['registered_logbar']['averaged'])
                self.assertIn('plain_expression',score['validation'])
                self.assertEqual(score['mix_counts']['plain_expression'],1)
                self.assertEqual(saved['config']['phases'],[[1,0,0,0,1]])
                self.assertTrue((output/'best-curriculum.pt').is_file())
                self.assertEqual(torch.load(source/'best.pt',weights_only=True)['step'],300)
                args.resume=True;args.steps=2
                self.assertEqual(module.run(args),0)
                after=torch.load(output/'latest.pt',weights_only=True)
                self.assertEqual(after['step'],2)
                self.assertIn('operation_head',torch.load(output/'best-curriculum.pt',weights_only=True))
                self.assertTrue(any(not torch.equal(saved['operation_head'][k],after['operation_head'][k])
                    for k in saved['operation_head']))
                self.assertEqual(torch.load(source/'best.pt',weights_only=True)['step'],300)
                args.mix_counts='2,0,0,0,0'
                with self.assertRaisesRegex(RuntimeError,'CURRICULUM_RESUME_CONFIG_MISMATCH'):
                    module.run(args)

class FixedBreakdownTests(unittest.TestCase):
    def test_parse_exact_distribution_without_milestones(self):
        spec=('registered_logbar=1,one_raw_operand=1,one_logbar_removed=1,'
              'one_raw_no_logbar=1,plain_expression=1,captain:plain_expression=1,'
              'captain:story_full_logbar=1,captain:story_one_logbar_x=1,'
              'captain:story_numeric_registers=1,captain:story_numeric_x_semantic_y=1,'
              'captain:story_semantic_with_op=2,captain:story_numeric_no_op=1,'
              'captain:story_semantic_no_op=2,captain:story_one_semantic_x=1')
        d=module.parse_training_breakdown(spec,16)
        self.assertEqual(sum(d.values()),16)
        self.assertEqual(d['captain:story_semantic_no_op'],2)
        self.assertNotIn('captain:story_raw',d)
        self.assertEqual(d,module.parse_training_breakdown(','.join(reversed(spec.split(','))),16))
        for broken in ('registered_logbar=1', 'captain:story_raw=17',
                       'captain:story_raw=1,captain:story_raw=15',
                       'captain:story_unknown=16','captain:story_raw=-1',
                       'captain:story_raw=15,registered_logbar=wat',
                       'registered_logbar=16'):
            with self.subTest(spec=broken),self.assertRaises(ValueError):
                module.parse_training_breakdown(broken,16)

    def test_fixed_exact_batch_and_orientation_targets(self):
        class FakeBank:
            def __init__(self):self.calls=[]
            def draw_with_partner(self,split,variant,step,rank,batch_size):
                self.calls.append((variant,step,rank))
                a={'a':17,'b':-3,'operation':'sym_add','prompt':variant+' forward'}
                b={**a,'prompt':variant+' reverse'}
                return a,b
        bank=FakeBank()
        source=[{'a':101,'b':103,'operation':'sym_multiply'} for _ in range(4)]
        mix={'plain_expression':1,'captain:story_semantic_with_op':2,
             'captain:story_numeric_no_op':1}
        rng=__import__('random').Random(7)
        stages,examples,prompts,partners=module.fixed_breakdown_batch(source,mix,bank,7,4,rng)
        self.assertEqual(dict(__import__('collections').Counter(stages)),mix)
        self.assertEqual(len(partners),3)
        self.assertEqual(len(set(rank for _,_,rank in bank.calls)),3)
        for slot,partner in partners:
            self.assertEqual((examples[slot]['a'],examples[slot]['b']),(17,-3))
            self.assertIn('reverse',partner)
            self.assertIn('forward',prompts[slot])
        self.assertEqual(sum(p is None for p in prompts),1)

    def test_pin_resume_and_explicit_reallocation(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            (root/'latest.pt').write_bytes(b'original latest')
            (root/'best-curriculum.pt').write_bytes(b'original best')
            bank=SimpleNamespace(sha256='d'*64)
            cap={'database_sha256':bank.sha256}
            paired={'schema':module.PAIRED_PLAN_SCHEMA}
            flag=SimpleNamespace(resume=True,_requested_training_mix={
                'plain_expression':1,'captain:story_raw':1},batch_size=2)
            a=module.fixed_breakdown_plan(flag,root,50000,cap,bank,paired)
            self.assertEqual(a['installed_after_step'],50000)
            self.assertEqual(a['additional_focus_examples'],0)
            self.assertEqual(a['additional_replay_examples'],0)
            self.assertEqual((root/'latest-pre-breakdown.pt').read_bytes(),b'original latest')
            self.assertEqual((root/'best-pre-breakdown.pt').read_bytes(),b'original best')
            flag._requested_training_mix=None
            self.assertEqual(module.fixed_breakdown_plan(flag,root,50100,cap,bank,paired),a)
            flag._requested_training_mix={'captain:story_raw':2}
            b=module.fixed_breakdown_plan(flag,root,50100,cap,bank,paired)
            self.assertEqual(b['counts'],{'captain:story_raw':2})
            self.assertEqual(b['history'][0]['counts'],a['counts'])
            self.assertNotEqual(a['mix_sha256'],b['mix_sha256'])
            self.assertEqual(module.fixed_breakdown_plan(flag,root,50200,cap,bank,paired),b)
            with self.assertRaisesRegex(RuntimeError,'SIDECAR_DRIFT'):
                module.fixed_breakdown_plan(flag,root,49900,cap,bank,paired)

if __name__=='__main__':unittest.main()
