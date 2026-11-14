"""Focused 1..1000 experiment: authentic oracle, balanced training, research probes."""
from __future__ import annotations
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import torch
from tools import nanojev_hawk_dove_focus_1k_curriculum as focus
from tools import nanojev_hawk_dove_gaussian_joint_decoder_21_focus_1k_stable as trainer
from tools import nanojev_hawk_dove_focus_1k_monitor as monitor
from tools import nanojev_hawk_dove_nested90_expected as expected
from tools import nanojev_hawk_dove_million as hd
from tools import nanojev_hawk_dove_gaussian_joint_decoder_21 as old21
from tools import nanojev_hawk_dove_coarse_to_fine_gaussian as fine
from tools import nanojev_hawk_dove_gaussian_zero_overflow_16 as prior

P={hd.TAGS[0]:Decimal('9'),hd.TAGS[1]:Decimal('-7'),hd.TAGS[2]:Decimal('0'),hd.TAGS[3]:Decimal('1')}

class FocusedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.audit,cls.train,cls.dev=focus.plan(P,set(),900,180,20261018)

    def test_no_float32_subtractive_cancellation_at_small_targets(self):
        base=torch.tensor([1_000_000.]*6+[2_000_000.],dtype=torch.float32)
        truth=torch.tensor([1.,1.01,1.04,5.,10.,1000.,1.],dtype=torch.float32)
        z=torch.asinh(truth)
        delta=z-torch.asinh(base)
        out=trainer.stable_inverse_asinh(base,z,delta)
        self.assertTrue(torch.allclose(out,truth,atol=0.0005,rtol=0.))
        old=base+(torch.sinh(z)-torch.sinh(torch.asinh(base)))
        self.assertGreater(float((old-truth).abs().max()),.2)

    def test_exact_initial_frozen_fallback_still_has_gradient(self):
        base=torch.tensor([1_000_000.],dtype=torch.float32)
        z=torch.asinh(base).detach().requires_grad_()
        delta=torch.zeros_like(z)
        prediction=trainer.stable_inverse_asinh(base,z,delta)
        self.assertEqual(float(prediction),float(base))
        g=torch.autograd.grad(prediction,z)[0]
        self.assertGreater(float(g),0.)

    def test_clamped_outlier_numerical_gradient_is_not_zero(self):
        base=torch.tensor([1_000_000.],dtype=torch.float32)
        z=torch.tensor([22.],requires_grad=True)
        pred=trainer.stable_inverse_asinh(base,z,torch.tensor([1.]))
        self.assertTrue(bool(torch.isfinite(pred).all()))
        loss=torch.log1p((pred-10.).abs()/10.)
        grad=torch.autograd.grad(loss,z)[0]
        self.assertGreater(float(grad.abs()),0.1)
        old_z=torch.tensor([22.],requires_grad=True)
        old_loss=torch.log1p((torch.sinh(old_z.clamp(-20,20))-10.).abs()/10.)
        self.assertEqual(float(torch.autograd.grad(old_loss,old_z)[0]),0.)

    def test_float32_no_nan_extremes_and_shape_validation(self):
        base=torch.tensor([1e6,-2e6])
        z=torch.tensor([100.,-100.],requires_grad=True)
        got=trainer.stable_inverse_asinh(base,z,torch.ones_like(z))
        self.assertTrue(bool(torch.isfinite(got).all()))
        with self.assertRaises(ValueError):
            trainer.stable_inverse_asinh(base[:1],z,torch.ones_like(z))
        with self.assertRaises(FloatingPointError):
            trainer.stable_inverse_asinh(base,torch.tensor([float('nan'),0.]),torch.ones_like(z))

    def test_oracle_coverage_is_balanced_unique_and_nonzero(self):
        self.assertTrue(self.audit['feasible'])
        self.assertEqual(self.audit['train_actual_by_band'],{'1-10':300,'10-100':300,'100-1000':300})
        self.assertEqual(self.audit['dev_actual_by_band'],{'1-10':60,'10-100':60,'100-1000':60})
        self.assertEqual(len(set(self.train)),900)
        self.assertEqual(len(set(self.dev)),180)
        self.assertFalse(set(self.train)&set(self.dev))
        self.assertTrue(all(a != a.to_integral_value() for a in self.train+self.dev))
        self.assertTrue(all(1 <= abs(expected.expected_oracle(a,P)[0]) < 1000 for a in self.train+self.dev))

    def test_hawk_dove_tiers_and_oracles_preserved(self):
        rows=expected.render_rows(self.train[:15],P,20261018,'train')
        for tier in hd.TIERS:
            for row in rows[tier]:
                got=expected.certify_one(row['prompt'],tier,Decimal(row['expected_a']),P)
                self.assertEqual(got['x'],row['x'])
                self.assertEqual(got['y'],row['y'])
                self.assertEqual(got['z'],row['z'])

    def test_unreachable_bands_fail_closed(self):
        q=dict(P)
        q[hd.TAGS[0]]=Decimal('1')
        q[hd.TAGS[1]]=Decimal('1')
        a, t, d=focus.plan(q,set(),900,180,42)
        self.assertFalse(a['feasible'])
        self.assertIsNone(t)
        self.assertIsNone(d)

    def test_sampler_enforces_all_three_bands_each_step(self):
        rows={tier:{'features':torch.randn(9,5),
                    'coarse':torch.zeros(9),
                    'target':torch.tensor([1.2,2.4,6.7,12.,25.,70.,120.,250.,900.])}
              for tier in hd.TIERS}
        s=focus.Sampler(rows)
        for step in range(20):
            d=s.draw(20261018,step,32)
            actual=[focus.band(x) for x in d['target'].tolist()]
            for name in ('1-10','10-100','100-1000'):
                self.assertGreaterEqual(actual.count(name),9)

    def test_parser_no_implicit_subsampling(self):
        a=trainer.parser().parse_args([])
        self.assertEqual(a.learnability_train_per_tier,0)
        self.assertEqual(a.mixture_train_per_tier,900)
        self.assertEqual(a.mixture_new_dev_per_tier,180)
        self.assertEqual(a.retention_fraction,0)
        self.assertEqual(a.experiment_arm,'decoder-only')
        self.assertEqual(trainer.BANK_COUNT,21)
        self.assertIn('focus_1k_stable_v2',str(a.output))

    def test_monitor_range_accuracy_not_overall_accuracy(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for step,m in [(0,0.),(250,.5)]:
                doc={'step':step,'focus_1k':{'train_by_band':{},'new_dev_by_band':{}},
                     'chunk_mean_train_losses_and_gradients':{'gradient_clip_fraction':1.,'numeric_loss':5.}}
                for name in ('1-10','10-100','100-1000'):
                    doc['focus_1k']['train_by_band'][name]={'n':90,'mae':5.-m,'accuracy_5pct_floor':m}
                    doc['focus_1k']['new_dev_by_band'][name]={'n':90,'mae':6.-m,'accuracy_5pct_floor':m}
                (root/f'probe-step-{step:07d}.json').write_text(json.dumps(doc))
            result=monitor.review(root)
            self.assertEqual(result['step'],250)
            self.assertTrue(result['persistent_clipping_warning'])
            self.assertEqual(result['bands']['1-10']['assessment'],'PRECISION_AND_GENERALIZATION_IMPROVING')
            self.assertEqual(result['bands']['10-100']['held_out_accuracy_gain_points'],50.)

    def test_resume_training_keeps_focus_1k_probes(self):
        torch.set_num_threads(1)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            source=root/'source';teacher=root/'teacher';fixture=root/'fixture'
            source.write_bytes(b'S');teacher.write_bytes(b'T');fixture.write_bytes(b'F')
            ref_dir=root/'fine';best_dir=ref_dir/'best_checkpoints';best_dir.mkdir(parents=True)
            def put(path,obj):
                torch.save(obj,path);return path.read_bytes()
            original=put(ref_dir/'parent_snapshot.pt',{'schema':'original'})
            donor=put(ref_dir/'donor_snapshot.pt',{'schema':'donor'})
            refiner=fine.LocalRefiner(5,kind='gaussian',registers=9)
            identity={'schema':fine.SCHEMA,'parent_arm':'continuous',
                  'parent_sha256':trainer.sha(original),'donor_sha256':trainer.sha(donor),
                  'source_sha256':trainer.fork.fingerprint(source),
                  'teacher_sha256':trainer.fork.fingerprint(teacher),
                  'seed':20261018,'train_per_tier':9,'dev_per_tier':9,
                  'registers':9,'sigma_bins':2.,'floor':125000.,
                  'fraction':.5,'temperature':8.}
            first=best_dir/'best_small_gaussian.pt'
            put(first,{'schema':fine.SCHEMA,'identity':identity,'step':300,
                           'heads':{'gaussian':refiner.state_dict()}})
            first.with_suffix('.json').write_text(json.dumps({
                 'checkpoint_sha256':trainer.sha(first.read_bytes()),'arm':'gaussian','step':300}))
            old_system=old21.JointGaussianSystem(5,
                fine.LocalRefiner(5,kind='gaussian',registers=9),hidden=16)
            donor_dir=root/'donor21';donor_dir.mkdir();donor_path=donor_dir/'latest.pt'
            donor_id={'stage2_sha256':trainer.sha(first.read_bytes()),
                'original_parent_sha256':trainer.sha(original),'donor_sha256':trainer.sha(donor),
                'source_sha256':trainer.fork.fingerprint(source),
                'teacher_sha256':trainer.fork.fingerprint(teacher),
                'relative_max':2000000.,'bank_count':21,'hidden':16,'bank_init_sha256':None}
            put(donor_path,{'schema':old21.SCHEMA,'identity':donor_id,
                'bank_states':{str(k):old_system.cascade.banks[str(k)].state_dict()
                               for k in old21.STAGES},
                'head_state':old_system.head.state_dict(),'step':1000})
            y=torch.tensor([2.,4.,7.,12.,25.,55.,110.,350.,900.])
            batch={'features':torch.randn(9,5),'coarse':y+100.,'target':y}
            data={tier:batch for tier in old21.TIERS}
            rows={tier:[{'instance':f'new-{i}','x':float(y[i])} for i in range(9)]
                  for tier in old21.TIERS}
            def fake_mixture(a):
                a._mixture_fixture_sha256='fixture_sha'
                a._mixture_train_sha256='train_sha'
                a._mixture_dev_sha256='dev_sha'
                a._mixture_plan_sha256='plan_sha'
                return {'feasible':True,'requested_train_by_band':{'1-10':3,'10-100':3,'100-1000':3},
                        'requested_new_dev_by_band':{'1-10':3,'10-100':3,'100-1000':3}},rows,rows
            def fake_old_features(a,m,d,b,c,split):
                return data,{t:[{'instance':f'original_{split}-{i}'} for i in range(9)]
                             for t in old21.TIERS}
            class Cache:
                def __init__(self,*a,**kw):pass
            fake_model={'device':'cpu','config':{'max_prompt_tokens':99}}
            args=trainer.parser().parse_args(['--refinement-checkpoint',str(first),
                '--decoder-init-checkpoint',str(donor_path),'--source',str(source),
                '--teacher',str(teacher),'--mixture-fixture',str(fixture),
                '--output',str(root/'focus'),'--device','cpu','--dtype','float32',
                '--train-per-tier','9','--dev-per-tier','9','--test-per-tier','9',
                '--mixture-train-per-tier','9','--mixture-new-dev-per-tier','9',
                '--hidden','16','--views','27','--max-steps','3',
                '--chunk-steps','1','--additional-steps','1'])
            with mock.patch.object(trainer,'build_mixture',side_effect=fake_mixture), \
                 mock.patch.object(trainer.fork.ex,'load_current',return_value=fake_model), \
                 mock.patch.object(fine,'init_frozen',return_value=(None,None)), \
                 mock.patch.object(trainer.fork.cached,'FrozenCaptureCache',Cache), \
                 mock.patch.object(prior,'load_features',side_effect=fake_old_features), \
                 mock.patch.object(expected,'load_features',return_value=data):
                trainer.run(args)
                checkpoint=trainer.master.load_state((args.output/'latest.pt').read_bytes())
                self.assertEqual(checkpoint['step'],1)
                probe=json.loads((args.output/'probe-step-0000001.json').read_text())
                self.assertEqual(probe['focus_1k']['train_by_band']['1-10']['n'],27)
                self.assertEqual(probe['focus_1k']['new_dev_by_band']['10-100']['n'],27)
                self.assertEqual(monitor.review(args.output)['status'],'OK')
                trainer.run(args)
                self.assertEqual(trainer.master.load_state((args.output/'latest.pt').read_bytes())['step'],2)

if __name__=='__main__': unittest.main()
