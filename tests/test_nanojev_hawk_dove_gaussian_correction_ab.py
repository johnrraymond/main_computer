"""CPU contract and mocked end-to-end isolation tests for correction A/B."""
from __future__ import annotations

import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import torch
from torch import nn

from tools import nanojev_hawk_dove_gaussian_correction_ab as ab
from tools import nanojev_hawk_dove_gaussian_register_fork as f


class Tests(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(24)

    def test_both_heads_are_identity_at_initialization(self):
        x=torch.randn(8,13)
        for model in (ab.ScalarCorrection(13),ab.SignedGaussianCorrection(13)):
            d=model(x)
            if isinstance(d,tuple):d=d[0]
            self.assertTrue(torch.equal(d,torch.zeros_like(d)))
            self.assertLessEqual(float(d.detach().abs().max()),1e-10)

    def test_both_heads_receive_numerical_gradients(self):
        x=torch.randn(8,13)
        coarse=torch.full((8,),5.7)
        y=torch.tensor([450000.,500000.,410000.,520000.,410000.,300000.,600000.,550000.])
        for m in (ab.ScalarCorrection(13),ab.SignedGaussianCorrection(13)):
            val=m(x)
            if isinstance(val,tuple):
                delta,logits=val
                loss=ab.sample_loss(delta,coarse,y)+.025*m.gaussian_loss(logits,torch.log10(1+y.abs())-coarse)
            else:loss=ab.sample_loss(val,coarse,y)
            loss.backward()
            for param in m.parameters():
                self.assertIsNotNone(param.grad)
                self.assertTrue(torch.isfinite(param.grad).all())
                self.assertGreater(float(param.grad.abs().sum()),0.)

    def test_no_hidden_x_in_pack(self):
        a=f.xyop.pack(12345,750000.,'011')
        b=f.xyop.pack(-12345,750000.,'011')
        self.assertEqual(a,b)
        self.assertEqual(a[0:2],(0.,0.))

    def test_bank_is_signed_and_has_zero_center(self):
        m=ab.SignedGaussianCorrection(5)
        self.assertEqual(len(m.centers),129)
        self.assertAlmostEqual(float(m.centers[0]),-.4,places=6)
        self.assertEqual(float(m.centers[64]),0.)
        self.assertAlmostEqual(float(m.centers[-1]),.4,places=6)
        with self.assertRaisesRegex(ValueError,'INVALID_REGISTER'):
            ab.SignedGaussianCorrection(5,count=128)

    def test_not_a_fake_deterministic_calculator(self):
        features=torch.zeros(3,12)
        targets=torch.tensor([100000.,500000.,1000000.])
        head=ab.SignedGaussianCorrection(12)
        delta,logits=head(features)
        self.assertEqual(float(delta.detach().max()),0.)
        self.assertTrue(torch.equal(logits[0],logits[1]))
        # Ground truth only enters the objective, never the forward signature.
        loss=head.gaussian_loss(logits,torch.log10(1+targets.abs())-5)
        self.assertGreater(float(loss),0)

    def test_reconstruct_cannot_modify_frozen_sign(self):
        sign=torch.tensor([-1.,0.,1.])
        g=torch.tensor([5.,5.,5.])
        result=ab.reconstruct(sign,g)
        self.assertLess(float(result[0]),0.)
        self.assertEqual(float(result[1]),0.)
        self.assertGreater(float(result[2]),0.)

    def test_diagnostic_report_has_all_tiers_and_gate(self):
        arr={
            'features':torch.zeros(3,12),
            'coarse_g':torch.tensor([5.,5.,5.]),
            'sign':torch.ones(3),
            'original_x':torch.full((3,),99999.),
            'target':torch.full((3,),99999.),
        }
        dev={t:arr for t in ab.TIERS}
        arms={'scalar':ab.ScalarCorrection(12),'bank':ab.SignedGaussianCorrection(12)}
        r=ab.dataset_report(arms,dev,40.,0)
        for t in ab.TIERS:
            self.assertEqual(r['arms']['scalar']['scores'][t]['correct'],3)
            self.assertEqual(r['arms']['bank']['scores'][t]['correct'],3)
        self.assertTrue(r['arms']['scalar']['all_nine_pass'])

    def test_disallows_output_overlapping_donor_source_teacher(self):
        args=ab.parser().parse_args([])
        args.output=args.fork/'accidental-sibling'
        with self.assertRaisesRegex(ValueError,'OVERLAPS'):
            ab._validate_args(args)
        args.output=args.teacher.parent/'accidental'
        with self.assertRaisesRegex(ValueError,'OVERLAPS'):
            ab._validate_args(args)

    def test_float32_dev_ratio_matches_float64_reference_by_exact_case_count(self):
        # Windows CUDA failure was 26/48 in float32 vs Python float64.
        p32=float(torch.tensor(26 / 48, dtype=torch.float32))
        p64=26 / 48
        self.assertGreater(abs(p32-p64),1e-8)
        self.assertEqual(ab.count_from_accuracy(p32,48),26)
        self.assertEqual(ab.count_from_accuracy(p64,48),26)
        self.assertNotEqual(ab.count_from_accuracy(p32,48),ab.count_from_accuracy(27/48,48))

    def test_baseline_comparison_accepts_exact_same_cases_with_float_rounding(self):
        dev={t:{'features':torch.zeros(48,3),
                'coarse_g':torch.ones(48)*5., 'sign':torch.ones(48),
                'original_x':torch.ones(48)*99999.,
                'target':torch.cat([torch.ones(26)*99999.,torch.ones(22)*200000.])}
             for t in ab.TIERS}
        # All 48 original X outputs are 99999; exactly 26 truth rows pass.
        scores={t:{'joint_5pct':26/48,'n':48} for t in ab.TIERS}
        with mock.patch.object(ab.fork,'evaluate',return_value={'scores':scores}):
            observed=ab.validate_baseline(object(),object(),dev,Path('unneeded'),40.,1,48)
        self.assertTrue(all(score==26/48 for score in observed.values()))

    def test_invalid_fractional_case_count_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'INVALID_SCORE'):
            ab.count_from_accuracy(26.5/48,48)
        with self.assertRaisesRegex(ValueError,'INVALID_SCORE'):
            ab.count_from_accuracy(float('nan'),48)

    def test_baseline_comparison_rejects_divergent_gate_scoring(self):
        dev={t:{'features':torch.zeros(3,3),'coarse_g':torch.ones(3)*5,
                'sign':torch.ones(3),'original_x':torch.ones(3)*1e5,
                'target':torch.ones(3)*1e5} for t in ab.TIERS}
        scores={t:{'joint_5pct':0.,'n':3} for t in ab.TIERS}
        with mock.patch.object(ab.fork,'evaluate',return_value={'scores':scores}):
            with self.assertRaisesRegex(ValueError,'BASELINE_SCORE_MISMATCH'):
                ab.validate_baseline(object(),object(),dev,Path('unneeded'),40.,1,48)

    def test_provenance_check_rejects_wrong_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source.pt';teacher=root/'teacher.sqlite3';source.write_bytes(b'good');teacher.write_bytes(b'data')
            args=ab.parser().parse_args(['--source',str(source),'--teacher',str(teacher)])
            ck={'schema':f.SCHEMA,'fork_step':700,'identity':{'source_sha256':'bad','teacher_sha256':ab.fork.fingerprint(teacher),'mix':['*:*=011:1']}}
            with self.assertRaisesRegex(ValueError,'DRIFT'):
                ab._config(args,b'checkpoint',ck)


    def test_capture_uses_real_frozen_bank_input(self):
        # Exercise the same pre-hook the production frozen Gaussian uses,
        # including bank-spread and entropy features.
        from tests.test_nanojev_hawk_dove_gaussian_register_fork import MiniDonor
        base=f.GaussianRegisterDecoder(MiniDonor(),registers=64)
        model={'decoder':base}
        collector=ab.FrozenFeatures(base)
        def real_forward(model,prompts,channels,cache):
            batch=len(prompts)
            return model['decoder'](torch.randn(batch,7),torch.randn(batch,2),
                                    torch.zeros(batch,dtype=torch.long),
                                    torch.randn(batch,5,6),torch.ones(batch,5,dtype=torch.bool))
        try:
            with mock.patch.object(f,'forward_masked',side_effect=real_forward):
                got=collector.extract(model,['a','b'],[[0]*6,[0]*6],object())
            self.assertEqual(got['features'].shape,(2,11))
            self.assertEqual(got['coarse_g'].shape,(2,))
            self.assertFalse(any(p.requires_grad for p in base.parameters()))
            for value in got.values():
                self.assertTrue(torch.isfinite(value).all())
        finally:collector.close()

    def test_zero_correction_uses_exact_frozen_donor_output(self):
        # A recomputed expm1 may differ enough to flip a case on the ±5% boundary.
        original=torch.tensor([104900.],dtype=torch.float32)
        coarse=torch.tensor([math.log10(105101.)],dtype=torch.float32)
        for head in (ab.ScalarCorrection(2),ab.SignedGaussianCorrection(2)):
            predicted,delta,_=ab.predict(head,torch.ones(1,2),coarse,
                                          torch.ones(1),original)
            self.assertTrue(torch.equal(predicted,original))
            self.assertTrue(torch.equal(delta,torch.zeros_like(delta)))
        self.assertNotEqual(float(ab.reconstruct(torch.ones(1),coarse)[0]),float(original[0]))

    def test_full_baseline_production_scoring_batch_alignment(self):
        # Real precompute + FrozenFeatures + fork.evaluate + validate_baseline.
        # Fake numerical backend deliberately changes one borderline prediction
        # with batch size; this recreates a real 20/48-vs-21/48 class of mismatch.
        class TinyDecoder(nn.Module):
            def __init__(self):
                super().__init__()
                self.bank=nn.Linear(2,3)
                self.register_buffer('centers',torch.tensor([0.,3.5,7.]))
        decoder=TinyDecoder()
        model={'decoder':decoder,'device':'cpu'}
        collector=ab.FrozenFeatures(decoder)
        def dev_rows(teacher,split,tier,n,seed):
            return [{'prompt':f'{tier}/{i}','x':100000.,'y':150000.,
                     'mask':0,'case_id':f'{tier}/{i}'} for i in range(n)]
        def batch_sensitive_forward(m,prompts,channels,cache):
            assert len(prompts)==len(channels)
            n=len(prompts)
            logits=decoder.bank(torch.zeros(n,2,2))  # Hook captures real features.
            values=[105100. if len(prompts)==8 and p.startswith('T06/') and p.endswith('/0') else 104900.
                    for p in prompts]
            x=torch.tensor(values,dtype=torch.float32)
            g=torch.log10(1+x)
            signs=torch.tensor([[-5.,-5.,5.]],dtype=torch.float32).repeat(n,2,1)
            return {'xy':torch.stack((x,torch.ones_like(x)*150000.),1),
                    'sign':signs,'log_magnitude':torch.stack((g,g),1),
                    'bank_logits':logits}
        try:
            with mock.patch.object(ab.fork.science,'rows_for_probe',side_effect=dev_rows),\
                 mock.patch.object(ab.fork.rt,'token_eligible',return_value=True),\
                 mock.patch.object(ab.fork,'forward_masked',side_effect=batch_sensitive_forward):
                train,dev=ab.precompute(model,collector,object(),'unused',8,48,20261018,8,'cpu')
                self.assertEqual(len(train['T00']['target']),8)
                self.assertEqual(len(dev['T00']['target']),48)
                scores=ab.validate_baseline(model,object(),dev,'unused',40.,20261018,48)
                self.assertEqual(ab.count_from_accuracy(scores['T06'],48),48)
                # Force the OLD buggy capture batch size; actual correct-count
                # comparison must catch the one-case disagreement.
                with mock.patch.object(ab,'ORIGINAL_DEV_PROBE_BATCH_SIZE',8):
                    _,wrong=ab.precompute(model,collector,object(),'unused',8,48,20261018,8,'cpu')
                with self.assertRaisesRegex(ValueError,'BASELINE_SCORE_MISMATCH:T06'):
                    ab.validate_baseline(model,object(),wrong,'unused',40.,20261018,48)
        finally:
            collector.close()

    def test_training_lifecycle_isolated_from_donor_and_resumable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source'/'source.pt';teacher=root/'teacher'/'db.sqlite3';origin=root/'origin';output=root/'out'
            for item in (source,teacher):
                item.parent.mkdir(parents=True)
                item.write_bytes(b'this-is-immutable')
            origin.mkdir()
            ck={'schema':f.SCHEMA,'identity':{'source_sha256':f.fingerprint(source),
                 'teacher_sha256':f.fingerprint(teacher),'mix':['*:*=011:1'],
                 'registers':512,'sigma_steps':2.},'fork_step':45600,'decoder':{'x':torch.zeros(1)}}
            torch.save(ck,origin/'latest.pt')
            original=(origin/'latest.pt').read_bytes()
            def mock_loader(*a):
                return {'device':'cpu','config':{'max_prompt_tokens':32},'decoder':nn.Linear(1,1)}
            def mock_precompute(model,collector,cache,tch,tr,dev,s,bs,device):
                data={}
                for tier in ab.TIERS:
                    n=12
                    data[tier]={'features':torch.randn(n,7),
                                'coarse_g':torch.ones(n)*5.,'sign':torch.ones(n),
                                'original_x':torch.ones(n)*99999.,
                                'target':torch.ones(n)*120000.}
                return data,data
            args=ab.parser().parse_args(['--source',str(source),'--teacher',str(teacher),
                '--fork',str(origin),'--output',str(output),'--device','cpu',
                '--additional-steps','6','--chunk-steps','3','--views','2',
                '--train-per-tier','12','--dev-per-tier','12'])
            with mock.patch.object(ab.fork.ex,'load_current',side_effect=mock_loader),\
                 mock.patch.object(ab.fork,'build_for_resume',return_value=nn.Sequential(nn.Linear(1,1))),\
                 mock.patch.object(ab,'FrozenFeatures',return_value=mock.Mock()),\
                 mock.patch.object(ab.fork.cached,'FrozenCaptureCache',return_value=object()),\
                 mock.patch.object(ab,'precompute',side_effect=mock_precompute),\
                 mock.patch.object(ab,'validate_baseline',return_value={t:0 for t in ab.TIERS}):
                ab.run(args)
                saved=torch.load(output/'latest.pt',map_location='cpu',weights_only=True)
                self.assertEqual(saved['step'],6)
                self.assertTrue(saved['optimizers']['scalar']['state'])
                self.assertTrue(saved['optimizers']['bank']['state'])
                self.assertEqual((origin/'latest.pt').read_bytes(),original)
                self.assertEqual((output/'donor_snapshot.pt').read_bytes(),original)
                self.assertTrue((output/'probe-step-0000003.json').exists())
                self.assertTrue((output/'probe-step-0000006.json').exists())
                ab.run(args)
                resumed=torch.load(output/'latest.pt',map_location='cpu',weights_only=True)
                self.assertEqual(resumed['step'],12)
                self.assertEqual((origin/'latest.pt').read_bytes(),original)
                args.lr=.01
                with self.assertRaisesRegex(ValueError,'RESUME_CONFIG_DRIFT'):
                    ab.run(args)


if __name__=='__main__':unittest.main()
