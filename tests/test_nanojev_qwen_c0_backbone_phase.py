"""Backbone phase isolation, label, and opt-in contract tests."""
import argparse
import unittest
from types import SimpleNamespace

from tools.nanojev_qwen_c0_balance import positive_ints
from tools.nanojev_qwen_c0_zero_line import make_disjoint_shells
from tools.nanojev_qwen_c0_backbone_phase import (
    validate_backbone_args, make_backbone_decks, encode_answer, collate,
)


def args(**changes):
    defaults = dict(zero_line_backbone_phase=True,zero_line_hard_train=True,
        zero_line_mixture=True,adapter=None,zero_line_backbone_steps=2,
        zero_line_backbone_batch=1,zero_line_backbone_accum=2,
        zero_line_backbone_eval_every=1,zero_line_backbone_eval_cases=16,
        zero_line_backbone_rank=16,zero_line_backbone_lr=1e-4,
        zero_line_backbone_min_improvement=.02,zero_line_backbone_train_cap=128,
        zero_line_program_weights='',zero_line_form_weights='',
        zero_line_delta_weights='',zero_line_magnitude_weights='',
        zero_line_mixture_per_cell=3,zero_line_nonzero_lookahead=2,seed=47)
    defaults.update(changes)
    return argparse.Namespace(**defaults)


class PhaseTests(unittest.TestCase):
    def test_opt_in_and_invalid_flags(self):
        validate_backbone_args(args(zero_line_backbone_phase=False))
        validate_backbone_args(args())
        for changes in ({'adapter':'already'}, {'zero_line_mixture':False},
                        {'zero_line_backbone_lr':0}, {'zero_line_backbone_rank':3},
                        {'zero_line_backbone_batch':0},
                        {'zero_line_backbone_min_improvement':2}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_backbone_args(args(**changes))

    def test_independent_decks_and_nonzero(self):
        radii=[0,1,2,4]; mags=(100,1000)
        shells=make_disjoint_shells(mags,radii,4,4,4,47)
        train,dev=make_backbone_decks(shells,radii,mags,args())
        self.assertTrue(train and dev)
        used_shell={r['prompt'] for s in shells.values() for kind in s for r in s[kind]}
        self.assertTrue(set(r['prompt'] for r in train).isdisjoint(r['prompt'] for r in dev))
        self.assertTrue(used_shell.isdisjoint(r['prompt'] for r in dev))
        self.assertTrue(any(r['answer']!=0 for r in train))
        self.assertTrue(any(r['focus_form']=='natural' for r in train))
        self.assertTrue(any(r['focus_form']=='arithmetic' for r in train))
        self.assertTrue(any(r['focus_program']=='P3' for r in train))

    def test_answer_only_supervision(self):
        class Tok:
            pad_token_id=0
            eos_token_id=9
            def encode(self,x,add_special_tokens=True):
                return ([1] if add_special_tokens else [])+[ord(c)%40+10 for c in x]
        tok=Tok()
        row=encode_answer(tok,'2*3 = ?',6,128)
        self.assertEqual(row['labels'][-1],9)
        self.assertTrue(all(t==-100 for t in row['labels'][:len(tok.encode('2*3 = ?'))]))
        import torch
        batch=collate(torch,tok,[row,encode_answer(tok,'a = ?',-7,128)],'cpu')
        self.assertEqual(batch['input_ids'].shape,batch['labels'].shape)
        self.assertTrue((batch['labels'][batch['attention_mask']==0] == -100).all())

if __name__=='__main__': unittest.main()
