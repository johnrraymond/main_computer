"""The 16 fixed-anchor Gaussian banks keep absolute geometry and state separated."""
import io
import math
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import torch

from tools import nanojev_hawk_dove_gaussian_fixed_ladder_16 as ladder
from tools import nanojev_hawk_dove_coarse_to_fine_gaussian as fine


class FixedLadder16Tests(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(12)
        torch.set_num_threads(1)

    @staticmethod
    def sample(rows=12):
        x=torch.tensor([100000.,-120000.,20000.,220000.,2000000.,-1500000.]*((rows+5)//6))[:rows]
        feat=torch.stack([torch.sin(x/600000.),torch.cos(x/400000.),
                          torch.arange(rows).float()/rows,torch.ones(rows),x.sign()],dim=1)
        coarse=x+torch.tensor([25000.,12000.,-7000.,-28000.,120000.,-85000.]*((rows+5)//6))[:rows]
        return {'features':feat,'coarse':coarse,'target':x}

    @staticmethod
    def new_cascade():
        return ladder.Cascade(5,fine.LocalRefiner(5,kind='gaussian',registers=9))

    def test_sixteen_absolute_anchor_pairs_half_toward_zero(self):
        casc=self.new_cascade()
        self.assertEqual(len(casc.banks),16)
        self.assertEqual(ladder.STAGES,tuple(range(3,19)))
        self.assertEqual(ladder.fixed_anchor(3),1000000.)
        self.assertEqual(ladder.fixed_anchor(18),30.517578125)
        for k in ladder.STAGES:
            bank=casc.banks[str(k)]
            anchor=ladder.fixed_anchor(k)
            self.assertEqual(bank.anchor,anchor)
            self.assertEqual(bank.sigma,anchor/2)
            physical=bank.centers*2*anchor
            # The two +/- anchors and the zero point lie on the fixed grid.
            self.assertEqual(float(physical[2]),-anchor)
            self.assertEqual(float(physical[4]),0.)
            self.assertEqual(float(physical[6]),anchor)
            self.assertEqual(float(physical[0]),-2*anchor)
            self.assertEqual(float(physical[-1]),2*anchor)
            if k>3:
                self.assertEqual(anchor,ladder.fixed_anchor(k-1)/2)
                # At their midpoint both neighboring Gaussian lobes have
                # meaningful tails (rather than only grid-interval overlap).
                prior=ladder.fixed_anchor(k-1)
                midpoint=(prior+anchor)/2
                prior_response=math.exp(-.5*((midpoint-prior)/(prior/2))**2)
                next_response=math.exp(-.5*((midpoint-anchor)/(anchor/2))**2)
                self.assertGreater(prior_response,.6)
                self.assertGreater(next_response,.6)
                # All of this finer interval overlaps the previous interval.
                self.assertLessEqual(abs(float(physical[-1])),
                                     2*ladder.fixed_anchor(k-1))
            self.assertFalse(any(p.requires_grad for p in casc.stage2.parameters()))

    def test_initial_all_banks_exact_identity_and_no_target_leak(self):
        casc=self.new_cascade();data=self.sample()
        preds=casc.upto(data)
        self.assertEqual(sorted(preds),list(range(2,19)))
        for k in ladder.STAGES:
            self.assertTrue(torch.equal(preds[2],preds[k]),k)
        with torch.no_grad():
            casc.banks['3'].mix.bias.fill_(2.)
            casc.banks['3'].readout.bias[-1]=2.
        changed=casc.upto(data)
        self.assertGreater(float((changed[3]-changed[2]).abs().mean().detach()),100.)
        altered=casc.upto({**data,'target':torch.full_like(data['target'],-999999.)})
        for k in changed:
            self.assertTrue(torch.equal(changed[k],altered[k]),k)
        self.assertTrue(torch.equal(changed[3],changed[18]))

    def test_bank_candidate_grid_never_moves_with_previous_prediction(self):
        bank=ladder.FixedAnchorBank(6,anchor=1e6,registers=9)
        features=torch.ones(3,6)
        with torch.no_grad():
            bank.readout.bias[-1]=2.
            bank.mix.bias.fill_(2.)
        a,info_a=bank(features,torch.tensor([-2e6,0.,2e6]))
        b,info_b=bank(features,torch.tensor([0.,200.,-200.]))
        self.assertTrue(torch.equal(info_a['proposal'],info_b['proposal']))
        self.assertTrue(torch.equal(info_a['probabilities'],info_b['probabilities']))
        self.assertFalse(torch.equal(a,b))
        self.assertTrue(torch.all(info_a['radius']==2e6))

    def test_global_normalization_does_not_explode_at_smallest_anchor(self):
        features=torch.ones(2,6)
        previous=torch.tensor([1_000_000.,-1_000_000.])
        data={'features':features,'coarse':previous,'target':torch.tensor([20000.,-20000.])}
        first=ladder.FixedAnchorBank(6,anchor=1_000_000.,registers=9)
        last=ladder.FixedAnchorBank(6,anchor=30.517578125,registers=9)
        # Both use the same global/relative loss for identical initial outputs.
        a=ladder.objective(first,data,2.,250000.,.2,2e6)
        b=ladder.objective(last,data,2.,250000.,.2,2e6)
        self.assertAlmostEqual(a.item(),b.item(),places=6)
        self.assertTrue(torch.isfinite(a))
        self.assertTrue(torch.isfinite(b))

    def test_no_confusion_between_support_and_predecessor_coverage(self):
        casc=self.new_cascade()
        dev={t:self.sample(12) for t in ladder.TIERS}
        report=ladder.probe(casc,dev,40.,250000.,0)
        self.assertIsNotNone(report['stages']['2']['small_local_coverage'])
        self.assertIsNone(report['stages']['18']['small_local_coverage'])
        self.assertIsNotNone(report['stages']['18']['small_fixed_support_coverage'])
        self.assertEqual(report['stages']['3']['fixed_support_coverage'],1.)
        self.assertLess(report['stages']['18']['fixed_support_coverage'],1.)

    def test_stage_progression_and_budget(self):
        self.assertEqual(ladder.active_stage(1,3),3)
        self.assertEqual(ladder.active_stage(3,3),3)
        self.assertEqual(ladder.active_stage(4,3),4)
        self.assertEqual(ladder.active_stage(48,3),18)
        for step in (0,49):
            with self.assertRaisesRegex(ValueError,'OUTSIDE_BUDGET'):
                ladder.active_stage(step,3)
        args=ladder.parser().parse_args([])
        self.assertEqual(args.additional_steps,48000)
        self.assertEqual(args.relative_max,2e6)
        self.assertEqual(args.best_stage,18)
        self.assertEqual(len(ladder.STAGES)*args.steps_per_bank,args.additional_steps)

    def test_resume_requires_same_geometry(self):
        with tempfile.TemporaryDirectory() as td:
            out=Path(td)
            args=ladder.parser().parse_args(['--relative-max','2000000'])
            self.assertNotEqual(ladder.identity(args,'a','b','c',{'source_sha256':'s','teacher_sha256':'t',
                 'parent_arm':'gaussian'}),{**ladder.identity(args,'a','b','c',{'source_sha256':'s',
                 'teacher_sha256':'t','parent_arm':'gaussian'}),'relative_max':1000000.})

    def test_training_resume_keeps_frozen_parent_and_test_split_isolated(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            source=root/'source';teacher=root/'teacher';source.write_bytes(b'S');teacher.write_bytes(b'T')
            fine_root=root/'fine';best=fine_root/'best_checkpoints';best.mkdir(parents=True)
            original={'schema':'fake-original'};donor={'schema':'fake-donor'}
            buf=io.BytesIO();torch.save(original,buf);original_bytes=buf.getvalue();(fine_root/'parent_snapshot.pt').write_bytes(original_bytes)
            buf=io.BytesIO();torch.save(donor,buf);donor_bytes=buf.getvalue();(fine_root/'donor_snapshot.pt').write_bytes(donor_bytes)
            first_head=fine.LocalRefiner(5,kind='gaussian',registers=9)
            id_={'schema':fine.SCHEMA,'parent_arm':'continuous','parent_sha256':ladder.digest(original_bytes),
                'donor_sha256':ladder.digest(donor_bytes),'source_sha256':fine.fork.fingerprint(source),
                'teacher_sha256':fine.fork.fingerprint(teacher),'seed':20261018,'dev_per_tier':8,
                'train_per_tier':8,'registers':9,'sigma_bins':2.,'floor':125000.,
                'fraction':.5,'temperature':8.}
            first={'schema':fine.SCHEMA,'identity':id_,'step':300,
                   'heads':{'gaussian':first_head.state_dict()}}
            path=best/'best_small_gaussian.pt';torch.save(first,path)
            train={t:self.sample(8) for t in ladder.TIERS}
            dev={t:self.sample(8) for t in ladder.TIERS}
            expected=ladder.probe(ladder.Cascade(5,first_head),dev,40.,250000.,0)
            path.with_suffix('.json').write_text(json.dumps({
                'checkpoint_sha256':fine.fork.fingerprint(path),'arm':'gaussian',
                'step':300,'metric':'small',
                'rank':ladder.rank(expected['stages']['2'],'small')}))
            args=ladder.parser().parse_args(['--refinement-checkpoint',str(path),
               '--source',str(source),'--teacher',str(teacher),'--output',str(root/'ladder'),
               '--device','cpu','--dtype','float32','--train-per-tier','8',
               '--dev-per-tier','8','--test-per-tier','8',
               '--additional-steps','4','--steps-per-bank','2',
               '--chunk-steps','1','--views','9'])
            model={'device':'cpu','config':{'max_prompt_tokens':99}}
            seen=[]
            class Cache:
                def __init__(self,*a,**k):pass
            def records(_args,_model,_decoder,_bridge,_cache,split):
                seen.append(split)
                data=train if split=='train' else dev
                return data,{t:[{'instance':f'{split}/{i//2}'} for i in range(8)] for t in ladder.TIERS}
            with mock.patch.object(ladder.fork.ex,'load_current',return_value=model),\
                 mock.patch.object(fine,'init_frozen',return_value=(object(),object())),\
                 mock.patch.object(ladder.fork.cached,'FrozenCaptureCache',Cache),\
                 mock.patch.object(ladder,'load_features',side_effect=records):
                original_sha=fine.fork.fingerprint(path)
                ladder.run(args)
                self.assertNotIn('test',seen)
                latest=ladder.master.load_state((root/'ladder/latest.pt').read_bytes())
                self.assertEqual(latest['step'],4)
                self.assertEqual(len(latest['bank_states']),16)
                self.assertEqual(original_sha,fine.fork.fingerprint(path))
                self.assertGreater(float(latest['bank_states']['3']['mix.weight'].abs().sum()),0.)
                self.assertGreater(float(latest['bank_states']['4']['mix.weight'].abs().sum()),0.)
                self.assertEqual(float(latest['bank_states']['5']['mix.weight'].abs().sum()),0.)
                args.additional_steps=2
                ladder.run(args)
                self.assertEqual(ladder.master.load_state((root/'ladder/latest.pt').read_bytes())['step'],6)
                self.assertGreater(float(ladder.master.load_state((root/'ladder/latest.pt').read_bytes())['bank_states']['5']['mix.weight'].abs().sum()),0.)
                self.assertNotIn('test',seen)
                self.assertFalse(list((root/'ladder').glob('best_checkpoints/best_small_stage18*.pt')))
                args.test_only=True;args.best_stage=5;args.additional_steps=30
                seen.clear()
                ladder.run(args)
                self.assertEqual(set(seen),{'test'})
                self.assertTrue(list((root/'ladder').glob('test-small-stage5-*.json')))
                self.assertEqual(original_sha,fine.fork.fingerprint(path))

if __name__=='__main__':unittest.main()
