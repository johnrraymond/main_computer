"""Direct contracts for 16 zero-centered Gaussian banks plus exact overflow labels."""
import io
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import torch

from tools import nanojev_hawk_dove_gaussian_zero_overflow_16 as ladder
from tools import nanojev_hawk_dove_coarse_to_fine_gaussian as fine


class ZeroCenteredOverflowTests(unittest.TestCase):
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

    def test_sixteen_zero_centers_and_halved_radii(self):
        casc=self.new_cascade()
        self.assertEqual(len(casc.banks),16)
        for i,k in enumerate(ladder.STAGES):
            bank=casc.banks[str(k)]
            radius=2e6/(2**i)
            self.assertEqual(bank.radius,radius)
            self.assertEqual(float(bank.centers[0]),-radius)
            self.assertEqual(float(bank.centers[-1]),radius)
            self.assertEqual(float(bank.centers[4]),0.)
            self.assertEqual(bank.readout.out_features,11) # 9 interior + 2 overflow
            if i:self.assertEqual(casc.banks[str(k-1)].radius,2*radius)
        self.assertEqual(casc.banks['18'].radius,61.03515625)
        self.assertFalse(any(p.requires_grad for p in casc.stage2.parameters()))

    def test_overflow_exact_one_hundred_percent_and_boundary_interior(self):
        bank=ladder.ZeroOverflowBank(2,radius=250000.,registers=9)
        y=torch.tensor([-300000.,-250000.,0.,100000.,250000.,300000.])
        label=bank.supervised_distribution(y)
        self.assertTrue(torch.equal(label[0],torch.tensor([1.]+[0.]*10)))
        self.assertTrue(torch.equal(label[-1],torch.tensor([0.]*10+[1.])))
        self.assertTrue(torch.allclose(label.sum(-1),torch.ones(6),atol=1e-6))
        self.assertEqual(float(label[1,0]),0.)
        self.assertEqual(float(label[-2,-1]),0.)
        self.assertTrue(torch.allclose(label[1:-1,1:-1].sum(-1),torch.ones(4),atol=1e-6))
        self.assertEqual(int(label[2,1:-1].argmax()),4)
        self.assertEqual(int(label[1,1:-1].argmax()),0)
        self.assertEqual(int(label[-2,1:-1].argmax()),8)

    def test_interior_chi_squared_gaussian_label(self):
        bank=ladder.ZeroOverflowBank(3,radius=80.,registers=9,sigma_bins=2.)
        p=bank.supervised_distribution(torch.tensor([0.]))[0,1:-1]
        # One-bin distance -> exp(-1/8), two bins -> exp(-1/2)
        self.assertAlmostEqual(float(p[5]/p[4]),math.exp(-.5*(1/2)**2),places=6)
        self.assertAlmostEqual(float(p[6]/p[4]),math.exp(-.5*(2/2)**2),places=6)
        self.assertEqual(float(bank.supervised_distribution(torch.tensor([100.]))[0,-1]),1.)

    def test_untrained_banks_preserve_frozen_baseline_and_no_target_leak(self):
        casc=self.new_cascade();data=self.sample()
        predicted=casc.upto(data)
        for k in ladder.STAGES:self.assertTrue(torch.equal(predicted[2],predicted[k]))
        changed=casc.upto({**data,'target':torch.full_like(data['target'],-88888.)})
        for k in predicted:self.assertTrue(torch.equal(predicted[k],changed[k]))
        self.assertTrue(all(v['selected_stage'].eq(2).all() for k,v in casc.upto(data,with_details=True)[1].items() if k>2))

    def test_finest_confident_interior_wins_without_additive_corrections(self):
        casc=self.new_cascade();data=self.sample()
        s2=casc.upto(data)[2]
        with torch.no_grad():
            # Force stage 3 to propose its +500,000 interior register.
            casc.banks['3'].readout.bias[6]=30.
        pred,detail=casc.upto(data,with_details=True)
        self.assertTrue(torch.all(pred[3]==500000.))
        self.assertTrue(torch.all(detail[3]['selected_stage']==3))
        self.assertTrue(torch.equal(pred[18],pred[3]))
        with torch.no_grad():
            # Force stage 4 to propose 0 (a finer bank).
            casc.banks['4'].readout.bias[5]=30.
        pred,detail=casc.upto(data,with_details=True)
        self.assertTrue(torch.all(pred[4].abs()<1.))
        self.assertTrue(torch.all(detail[4]['selected_stage']==4))
        with torch.no_grad():
            # Bank 4 now reports right-overflow. Bank 3 wins again.
            casc.banks['4'].readout.bias[5]=0.
            casc.banks['4'].readout.bias[-1]=40.
        pred,detail=casc.upto(data,with_details=True)
        self.assertTrue(torch.equal(pred[18],pred[3]))
        self.assertTrue(torch.all(detail[18]['selected_stage']==3))
        self.assertFalse(torch.equal(pred[3],s2))

    def test_loss_finite_and_overflow_gradients_trainable(self):
        head=ladder.ZeroOverflowBank(2,radius=100.,registers=17)
        x=torch.tensor([[-2.,1.],[-1.,1.],[0.,1.],[1.,1.],[2.,1.]])
        y=torch.tensor([-250.,-100.,0.,100.,250.])
        batch={'features':x,'target':y}
        loss=ladder.objective(head,batch,2.,200.)
        self.assertTrue(torch.isfinite(loss))
        loss.backward()
        self.assertGreater(float(head.readout.weight.grad.abs().sum()),0.)
        with torch.no_grad():
            before=loss.item()
        opt=torch.optim.Adam(head.parameters(),lr=.03)
        for _ in range(800):
            opt.zero_grad()
            ladder.objective(head,batch,2.,200.).backward()
            opt.step()
        self.assertLess(ladder.objective(head,batch,2.,200.).item(),before*.35)
        _,info=head(x)
        self.assertGreater(float(info['p_left'][0]),.9)
        self.assertGreater(float(info['p_right'][-1]),.9)
        self.assertTrue(bool(info['accepted'][2]))

    def test_static_support_and_overflow_metrics_in_probe(self):
        casc=self.new_cascade();dev={t:self.sample() for t in ladder.TIERS}
        report=ladder.probe(casc,dev,40.,250000.,0)
        self.assertEqual(report['stages']['2']['macro_accuracy_5pct'],report['stages']['18']['macro_accuracy_5pct'])
        self.assertEqual(report['stages']['3']['fixed_support_coverage'],1.)
        self.assertLess(report['stages']['18']['fixed_support_coverage'],1.)
        self.assertEqual(report['stages']['18']['chosen_fraction'],0.)
        self.assertIn('overflow_class_accuracy',report['stages']['18'])

    def test_budget_and_isolated_default_output(self):
        args=ladder.parser().parse_args([])
        self.assertEqual(args.additional_steps,48000)
        self.assertEqual(args.best_stage,18)
        self.assertIn('zero_overflow_16_v2',str(args.output))
        self.assertEqual(ladder.active_stage(1,3),3)
        self.assertEqual(ladder.active_stage(48,3),18)
        self.assertEqual(ladder.fixed_radius(3),2e6)
        with self.assertRaisesRegex(ValueError,'OUTSIDE_BUDGET'):ladder.active_stage(49,3)
        self.assertNotIn('relative_weight',ladder.identity(args,'a','b','c',{'source_sha256':'s','teacher_sha256':'t','parent_arm':'continuous'}))

    def test_mock_training_resume_and_untouched_test_split(self):
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
                self.assertGreater(float(latest['bank_states']['3']['readout.weight'].abs().sum()),0.)
                self.assertGreater(float(latest['bank_states']['4']['readout.weight'].abs().sum()),0.)
                self.assertEqual(float(latest['bank_states']['5']['readout.weight'].abs().sum()),0.)
                args.additional_steps=2
                ladder.run(args)
                self.assertEqual(ladder.master.load_state((root/'ladder/latest.pt').read_bytes())['step'],6)
                self.assertNotIn('test',seen)
                self.assertFalse(list((root/'ladder').glob('best_checkpoints/best_small_stage18*.pt')))
                args.test_only=True;args.best_stage=5;args.additional_steps=30
                seen.clear()
                ladder.run(args)
                self.assertEqual(set(seen),{'test'})
                self.assertTrue(list((root/'ladder').glob('test-small-stage5-*.json')))
                self.assertEqual(original_sha,fine.fork.fingerprint(path))

if __name__=='__main__':unittest.main()
