"""CPU-only numerical fusion contracts and synthetic end-to-end training audit."""
from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import torch
from tools import nanojev_hawk_dove_million as hd
from tools import nanojev_hawk_dove_continuous_benchmark as old
from tools import nanojev_hawk_dove_numeric_pathway_probe as pathway
from tools import nanojev_hawk_dove_numeric_fusion_v1 as fusion


class NumericFusionContracts(unittest.TestCase):
    def setUp(self):
        self.payoffs={k:Decimal(v) for k,v in zip(hd.TAGS,('-4','3','0','1.5'))}

    def test_parse_all_scientific_masks_no_answer_leakage(self):
        for mask, slots in hd.masks('T02'):
            with self.subTest(mask=mask):
                prompt=hd.render('T02',940000,60000,self.payoffs,slots,hd.REP_SCIENCE)
                self.assertEqual(fusion.exact_input_counts(prompt),(940000,60000))
        plain=hd.render('T02',940000,60000,self.payoffs,(),hd.REP_SCIENCE)
        self.assertEqual(fusion.exact_input_counts(plain.replace('90000)', '345678)')),
                         (940000,60000))

    def test_roles_not_text_order_and_invalid_rule_rejected(self):
        p=hd.render('T02',940000,60000,self.payoffs,(),hd.REP_SCIENCE)
        before='(MUL 940000 HAWK_HAWK_PAYOFF)'
        after='(MUL 60000 HAWK_DOVE_PAYOFF)'
        self.assertIn(before,p);self.assertIn(after,p)
        swapped=p.replace(before,'__TMP__').replace(after,before).replace('__TMP__',after)
        self.assertEqual(fusion.exact_input_counts(swapped),(940000,60000))
        with self.assertRaises(ValueError):fusion.exact_input_counts(p.replace('(ADD','(SUB'))
        with self.assertRaises(ValueError):fusion.exact_input_counts(p.replace('ARGMAX','ARGMIN'))
        with self.assertRaises(ValueError):fusion.exact_input_counts(p.replace('940000','940001'))
        with self.assertRaises(ValueError):fusion.exact_input_counts(p.replace('HAWK_DOVE_PAYOFF','DOVE_DOVE_PAYOFF'))

    def test_learned_linear_coefficients_are_not_hardcoded(self):
        a=np.linspace(0,1,40);b=1-a
        counts=np.stack([a,b],-1)*fusion.SCALE
        # Deliberately different from Hawk–Dove to catch a hardcoded formula.
        labels=np.stack([2*a+7*b,-9*a+4*b],-1)*fusion.SCALE
        coef=fusion.affine_from_train(counts,labels)
        held=np.asarray([[170001,829999],[750000,250000]],dtype=np.float64)
        pred=np.column_stack([held/fusion.SCALE,np.ones(2)])@coef*fusion.SCALE
        truth=np.column_stack([2*held[:,0]+7*held[:,1],-9*held[:,0]+4*held[:,1]])
        np.testing.assert_allclose(pred,truth,atol=1e-7)

    def test_forward_two_outputs_and_numerical_gradient(self):
        rng=np.random.default_rng(3)
        coef=np.asarray([[.2,.4],[.8,-.7],[1.1,-.3]],dtype=np.float64)
        model=fusion.create_fusion(384,coef,.01)
        feats=torch.as_tensor(rng.normal(size=(5,2,192)),dtype=torch.float32)
        counts=torch.as_tensor(np.asarray([[0,1000000],[250000,750000],
                   [500000,500000],[750000,250000],[1000000,0]],dtype=np.float64))
        pred=model(feats,counts)
        self.assertEqual(tuple(pred.shape),(5,2))
        self.assertEqual(pred.dtype,torch.float64)
        loss=(pred/1e6).square().mean()
        loss.backward()
        self.assertIsNotNone(model.numeric.weight.grad)
        self.assertIsNotNone(model.semantic[-1].weight.grad)
        self.assertTrue(torch.isfinite(model.numeric.weight.grad).all())

    def test_exact_zero_robust_when_using_double_counts(self):
        # Float32 rounded inputs can fail the 5%-relative criterion for X=0.
        a=np.asarray([20000,400000,750000,900000],dtype=np.float64)
        b=1e6-a
        counts=np.column_stack([a,b])
        xy=np.column_stack([3*b-a,1.5*b])
        model=fusion.create_fusion(384,fusion.affine_from_train(counts,xy),.01)
        preds=model(torch.zeros((4,2,192)),torch.from_numpy(counts),zero_semantics=True).detach().numpy()
        self.assertLess(np.abs(preds-xy).max(),1e-6)
        self.assertEqual(old.accuracy(preds,xy)['joint_5pct'],1.0)

    def test_live_runtime_adapter_uses_prompt_not_teacher_targets(self):
        from torch import nn
        model=fusion.create_fusion(384, fusion.affine_from_train(
            np.asarray([[0,1000000],[500000,500000],[1000000,0]],dtype=np.float64),
            np.asarray([[3000000,1500000],[1000000,750000],[-1000000,0]],dtype=np.float64)), .01)
        fake_body=nn.Identity()
        fake_decoder=nn.Module()
        fake_decoder.body=fake_body
        frozen={'decoder':fake_decoder}
        one=hd.render('T02',940000,60000,self.payoffs,(),hd.REP_SCIENCE)
        two=hd.render('T02',250000,750000,self.payoffs,(),hd.REP_SCIENCE)
        def fake_forward(frozen_arg,prompts):
            self.assertIs(frozen_arg,frozen)
            fake_body(torch.zeros((len(prompts),2,192),dtype=torch.float32))
            return {'unused':True}, torch.zeros(len(prompts),1)
        from tools import nanojev_game_transform_experiment as ex
        with patch.object(ex,'forward',side_effect=fake_forward):
            prediction,features=fusion.predict_prompts(model,frozen,[one,two],'cpu')
        np.testing.assert_allclose(prediction,
            [[-760000,90000],[2000000,1125000]], atol=1e-6)
        self.assertEqual(features.shape,(2,2,192))

    def test_provenance_rejects_writing_inside_donor(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            args=SimpleNamespace(root=root, previous=root/'prior', pathway=root/'pathway',donor=root,
                                 teacher=root/'teacher',source=root/'source',pathway_epochs=80,pathway_lr=.001,
                                 pathway_batch_size=128,pathway_seed=20261019)
            with patch.object(fusion.benchmark,'verify'), patch.object(fusion.pathway,'verify'),\
                 patch.object(fusion.benchmark,'load_dataset'), patch.object(fusion.pathway,'load_capture'), \
                 patch.object(fusion.benchmark,'digest',return_value='hash'):
                with self.assertRaises(ValueError):fusion.manifest_identity(args)

    def test_end_to_end_training_and_splits_without_test(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);prev=root/'previous';prev.mkdir();proot=root/'probe';proot.mkdir()
            out=root/'candidate';out.mkdir(); donor=root/'donor';donor.mkdir()
            (donor/'latest.pt').write_bytes(b'donor');teacher=root/'teacher.db';teacher.write_bytes(b'db')
            (out/'manifest.json').write_text('{}',encoding='utf8')
            (prev/'manifest.json').write_text('{}',encoding='utf8')
            args=SimpleNamespace(root=out,previous=prev,pathway=proot,donor=donor,
                  teacher=teacher,source=root/'source',epochs=8,learning_rate=.0002,
                  semantic_scale=.0001,train_batch_size=32,seed=20261019)
            rows_by_split={}
            old_data_files={}
            for split, acounts in [('train',[0,5000,20000,100000,250000,400000,500000,600000,750000,900000]),
                                   ('dev',[35000,175000,430000,650000,870000])]:
                prompts=[];targets=[];feats=[];operands=[];masks=[];instances=[];cases=[]
                for i,a in enumerate(acounts):
                    b=1000000-a
                    for mask,slots in hd.masks('T02'):
                        prompt=hd.render('T02',a,b,self.payoffs,slots,hd.REP_SCIENCE)
                        case_id=f'{split}-{i}-T02-{mask}'
                        prompts.append({'prompt':prompt,'case_id':case_id,'instance':i,'mask':mask,
                                        'x':3*b-a,'y':1.5*b})
                        targets.append([3*b-a,1.5*b]);operands.append([a/1e6,b/1e6])
                        rng=np.random.default_rng(i*8+mask)
                        feats.append(rng.normal(size=(2,192)).astype(np.float32))
                        masks.append(mask);instances.append(i);cases.append(case_id)
                old.save_dataset(SimpleNamespace(root=prev,donor=donor,teacher=teacher),
                    split,np.array(feats),np.array(targets),np.array(operands),
                    np.zeros((len(targets),2),dtype=np.float32),masks,instances,cases)
                rows_by_split[split]=prompts
                old_data_files[split]=old.digest(prev/f'features-{split}.npz')
            def dataset(teacher_arg, split):
                self.assertIn(split,('train','dev'),'test labels must stay sealed')
                return rows_by_split[split]
            def corrected(args_in,split):
                return {'corrected_v3':np.zeros((len(rows_by_split[split]),2),dtype=np.float32)}
            with patch.object(fusion,'verify',return_value=None), \
                 patch.object(fusion.benchmark,'dataset_rows',side_effect=dataset), \
                 patch.object(fusion.pathway,'load_capture',side_effect=corrected):
                fusion.train(args)
                result=fusion.analyze(args)
                again=fusion.analyze(args)
            self.assertEqual(result,again)
            self.assertEqual(result['splits']['dev']['score']['numeric_only']['overall']['joint_5pct'],1.)
            self.assertGreaterEqual(result['splits']['dev']['score']['fusion']['overall']['joint_5pct'],.70)
            self.assertEqual(old.digest(prev/'features-train.npz'),old_data_files['train'])
            self.assertEqual(old.digest(prev/'features-dev.npz'),old_data_files['dev'])
            self.assertFalse((out/'features-test.npz').exists())
            self.assertFalse((out/'results-test.json').exists())
            self.assertTrue((out/'candidate.pt').is_file())
            self.assertTrue((out/'results.json').is_file())


if __name__ == '__main__':
    unittest.main()
