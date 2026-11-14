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
import nanojev_altreg_attention_curriculum_v4 as module

class CurriculumTests(unittest.TestCase):
    def test_preview_baseline_exact(self):
        for reverse in (False,True):
            self.assertEqual(module.curriculum_prompt(17,-3,'sym_sub','registered_logbar',reverse),
                             module.base.prompt(17,-3,'sym_sub',reverse))

    def test_complete_operation_coverage(self):
        self.assertEqual(set(module.STORIES),set(module.base.OPERATIONS))
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
            story=module.curriculum_prompt(17,-3,'sym_add','story_problem',reverse)
            self.assertIn(module.base.registers.FIRST,current)
            self.assertIn(module.base.registers.SECOND,current)
            self.assertEqual(sum(x in raw for x in (module.base.registers.FIRST,module.base.registers.SECOND)),1)
            self.assertIn(module.base.registers.FIRST,no_bar)
            self.assertIn(module.base.registers.SECOND,no_bar)
            self.assertEqual(sum(x in mixed for x in (module.base.registers.FIRST,module.base.registers.SECOND)),1)
            for text in (plain,story):
                self.assertNotIn(module.base.registers.FIRST,text)
                self.assertNotIn(module.base.registers.SECOND,text)
                self.assertNotIn('LogBar',text)
                self.assertNotIn('|',text)
            self.assertNotIn('LogBar',mixed)

    def test_story_paraphrases_are_distinct_and_unscaffolded(self):
        self.assertEqual(set(module.STORIES),set(module.STORY_PARAPHRASES))
        a,b=17,-3
        for operation in module.STORIES:
            with self.subTest(operation=operation):
                original=module.curriculum_prompt(a,b,operation,'story_problem',False)
                reversed_=module.curriculum_prompt(a,b,operation,'story_problem',True)
                self.assertNotEqual(original,reversed_)
                for prompt in (original,reversed_):
                    self.assertEqual(prompt.count(str(a)),1)
                    self.assertEqual(prompt.count(str(b)),1)
                    for scaffold in ('LogBar','XXXXXX','YYYYYY','first input','second input'):
                        self.assertNotIn(scaffold,prompt)
                self.assertNotEqual(original.find(str(a))<original.find(str(b)),
                                    reversed_.find(str(a))<reversed_.find(str(b)))

    def test_prompt_revision_covers_story_paraphrases(self):
        old_revision=module.prompt_revision()
        old_text=module.STORY_PARAPHRASES['sym_add']
        try:
            module.STORY_PARAPHRASES['sym_add']='An alternative change of {b} modifies a balance of {a}. What remains?'
            self.assertNotEqual(old_revision,module.prompt_revision())
        finally:
            module.STORY_PARAPHRASES['sym_add']=old_text
        self.assertEqual(old_revision,module.prompt_revision())

    def test_no_operation_side_channel(self):
        a={'operation':'sym_add'};b={'operation':'sym_multiply'}
        values=module.operation_ids([a,b],['story_problem','plain_expression'],'cpu')
        self.assertEqual(values.tolist(),[0,0])
        values=module.operation_ids([a,b],['registered_logbar','one_logbar_removed'],'cpu')
        self.assertEqual(values.tolist(),[module.base.OP_IDS['sym_add'],module.base.OP_IDS['sym_multiply']])

    def test_phase_mixes(self):
        for row in module.MIXES:
            self.assertAlmostEqual(sum(row),1.)
            self.assertTrue(all(x>=0 for x in row))
        self.assertEqual(module.phase_mix(1,20)[0],0)
        self.assertEqual(module.phase_mix(20,20)[0],0)
        self.assertEqual(module.phase_mix(21,20)[0],1)
        self.assertEqual(module.phase_mix(500,20)[0],5)
        self.assertGreater(module.MIXES[-1][-1],0)
        self.assertGreater(module.MIXES[-1][0],0)

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
                '--eval-every','1','--validation-examples','2'])
            with patch.object(module.v3,'get_parent',get_parent),\
                 patch.object(module.v3,'full_capture',captures),\
                 patch.object(module.base.cassette,'load_source',lambda *a:(None,Frozen(),object(),'cpu',None)),\
                 patch.object(module.base.registers,'build_plan',lambda *a:(None,[])),\
                 patch.object(module.base,'Stream',Stream):
                self.assertEqual(module.run(args),0)
                self.assertEqual(torch.load(output/'latest.pt',weights_only=True)['step'],1)
                self.assertTrue((output/'best-curriculum.pt').is_file())
                self.assertEqual(torch.load(source/'best.pt',weights_only=True)['step'],300)
                args.resume=True;args.steps=2
                self.assertEqual(module.run(args),0)
                self.assertEqual(torch.load(output/'latest.pt',weights_only=True)['step'],2)
                self.assertEqual(torch.load(source/'best.pt',weights_only=True)['step'],300)

if __name__=='__main__':unittest.main()
