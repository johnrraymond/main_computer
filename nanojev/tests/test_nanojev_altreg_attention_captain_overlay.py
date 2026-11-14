"""Regression tests: installed Pass-One overlay on a live v4 exact-mix lineage."""
from pathlib import Path
import hashlib
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

import torch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import nanojev_altreg_attention_curriculum_mix_v4 as mod
import nanojev_captain_numeracy_populate as pop


class CaptainOverlayTests(unittest.TestCase):
    def test_milestones_and_side_channel(self):
        parsed=mod.parse_captain_milestones([
            '3:story_full_logbar=1,story_one_logbar_x=2',
            '8:story_numeric_no_op=2,story_raw=1'],4)
        self.assertEqual(mod.active_captain_counts(2,parsed),(0,{}))
        self.assertEqual(mod.active_captain_counts(5,parsed),(3,{'story_full_logbar':1,'story_one_logbar_x':2}))
        self.assertEqual(mod.active_captain_counts(8,parsed),(8,{'story_numeric_no_op':2,'story_raw':1}))
        for invalid in (['5:story_raw=5'],['0:story_raw=1'],['1:story_raw=1','1:story_raw=2'],
                        ['2:unknown=1'],['2:story_raw=2,story_raw=1']):
            with self.assertRaises(ValueError):mod.parse_captain_milestones(invalid,4)
        e={'operation':'sym_add'}
        self.assertEqual(mod.operation_ids([e,e],['captain:story_full_logbar','captain:story_raw'],'cpu').tolist(),
                         [mod.base.OP_IDS['sym_add'],0])

    def test_real_bank_overlay_targets_and_holdouts(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'bank'
            pop.populate(root,seed=123,counts={'train':64,'dev':8,'predev':8,'test':8})
            bank=mod.CaptainBank(root,excluded_pairs={(-3,17)})
            self.assertEqual(bank.sha256,json.loads((root/'manifest.json').read_text())['databaseSha256'])
            raw=bank.draw('train','story_raw',10,0,4)
            self.assertIn('Answer:',raw['prompt'])
            self.assertNotIn(mod.base.registers.FIRST,raw['prompt'])
            stages=['registered_logbar','plain_expression','one_raw_no_logbar','plain_expression']
            examples=[{'a':17,'b':-3,'operation':'sym_add'}]*4
            amended,target,rendered=mod.overlay_batch(stages,examples,
                {'story_full_logbar':1,'story_raw':2},bank,10,4)
            self.assertEqual(sum(x.startswith('captain:') for x in amended),3)
            self.assertEqual(amended.count('plain_expression'),0)
            self.assertEqual(amended.count('registered_logbar'),1)
            self.assertEqual(sum(p is not None for p in rendered),3)
            self.assertEqual(mod.operation_targets(target,'cpu').numel(),4)
            # Deterministic and independent of any training RNG state.
            self.assertEqual(mod.overlay_batch(stages,examples,
                {'story_full_logbar':1,'story_raw':2},bank,10,4),(amended,target,rendered))
            with self.assertRaisesRegex(RuntimeError,'BANK_SHA_MISMATCH'):
                mod.CaptainBank(root,expected_sha='0'*64)

    def test_old_checkpoint_resume_add_schedule_and_resume_again(self):
        class Parent:
            def eval(self):return self
            def extract(self,flat):return torch.zeros(len(flat),4)
            def xy_coord(self,h):return torch.zeros(len(h),2)
        class Frozen(torch.nn.Module):pass
        class Stream:
            def __init__(self,seed,reserved):self.draws=0
            def get(self):self.draws+=1;return {'a':17,'b':-3,'operation':'sym_add'}
            def state_dict(self):return {'draws':self.draws}
            def load_state_dict(self,data):self.draws=data['draws']
        prompts_seen=[]
        def captures(backbone,tokenizer,device,prompts,max_tokens):
            prompts_seen.extend(prompts)
            count=len(prompts)
            return (torch.randn(count,32),torch.randn(count,5,8),torch.ones(count,5,dtype=torch.bool))
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            pop.populate(root/'bank',seed=34,counts={'train':50,'dev':8,'predev':8,'test':8})
            source=root/'source';output=root/'curriculum';parent_root=root/'parent'
            source.mkdir();parent_root.mkdir()
            (parent_root/'best-precision.pt').write_bytes(b'fixed-parent')
            parent_sha=hashlib.sha256(b'fixed-parent').hexdigest()
            head=mod.v3.V3Decoder(4,len(mod.base.OP_IDS),'attention',6,16,8)
            torch.save({'schema':mod.v3.SCHEMA,'config':{'arm':'attention','backbone':'qwen',
                'hidden':16,'parent_root':str(parent_root),'parent_checkpoint':'best-precision',
                'parent_sha256':parent_sha,'max_prompt_tokens':512},'step':300,'head':head.state_dict()},source/'best.pt')
            cache={'validation':{'examples':[{'a':17,'b':-3,'operation':'sym_add'},
                                              {'a':8,'b':2,'operation':'sym_sub'}]}}
            get_parent=lambda base,args:({'config':{'input_dim':32,'width':4,'seed':23}},cache,Parent(),parent_sha)
            args=mod.parser().parse_args(['--source-root',str(source),'--output-root',str(output),
                '--device','cpu','--steps','1','--phase-steps','1','--batch-size','2',
                '--eval-every','1','--validation-examples','2', '--mix-counts','1,0,0,0,1'])
            with patch.object(mod.v3,'get_parent',get_parent),\
                 patch.object(mod.v3,'full_capture',captures),\
                 patch.object(mod.base.cassette,'load_source',lambda *a:(None,Frozen(),object(),'cpu',None)),\
                 patch.object(mod.base.registers,'build_plan',lambda *a:(None,[])),\
                 patch.object(mod.base,'Stream',Stream):
                self.assertEqual(mod.run(args),0)
                pre=torch.load(output/'latest.pt',weights_only=True)
                args.resume=True;args.steps=2
                args.captain_bank=root/'bank'
                args.captain_milestone=['2:story_full_logbar=1']
                args.captain_eval_examples=2
                self.assertEqual(mod.run(args),0)
                post=torch.load(output/'latest.pt',weights_only=True)
                self.assertEqual(post['config'],pre['config'])
                self.assertEqual(post['stream']['draws'],pre['stream']['draws']+2)
                plan=json.loads((output/mod.CAPTAIN_PLAN_FILE).read_text())
                self.assertEqual(plan['installed_after_step'],1)
                score=json.loads((output/'validation-step-00000002.json').read_text())
                self.assertEqual(score['captain_milestone_step'],2)
                self.assertEqual(score['effective_mix_counts']['captain:story_full_logbar'],1)
                self.assertIn('captain:story_full_logbar',score['validation'])
                self.assertTrue(any('shuttle' in p.lower() or 'navigation' in p.lower() or
                                    'reports' in p.lower() or 'control' in p.lower() or
                                    'guidance' in p.lower() for p in prompts_seen))
                args.steps=3;args.captain_milestone=[];args.captain_bank=None
                # Only --resume needed once plan was pinned, plus legacy identity.
                self.assertEqual(mod.run(args),0)
                self.assertEqual(torch.load(output/'latest.pt',weights_only=True)['step'],3)
                args.captain_milestone=['4:story_raw=1']
                with self.assertRaisesRegex(RuntimeError,'PLAN_ALREADY_PINNED'):
                    mod.run(args)

if __name__=='__main__':unittest.main()
