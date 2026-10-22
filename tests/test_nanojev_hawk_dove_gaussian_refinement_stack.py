"""Sequential six-Gaussian-bank extension: identity, conditioning, resume and test."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import torch

from tools import nanojev_hawk_dove_gaussian_refinement_stack as stack
from tools import nanojev_hawk_dove_coarse_to_fine_gaussian as fine


class GaussianEightStageTests(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(12)

    @staticmethod
    def sample(rows=12):
        x=torch.tensor([100000.,-120000.,20000.,220000.,2000000.,-1500000.]*((rows+5)//6))[:rows]
        # Numerical features come from the frozen model, not true-X targets.
        feat=torch.stack([torch.sin(x/600000.),torch.cos(x/400000.),
                          torch.arange(rows).float()/rows,torch.ones(rows),x.sign()],dim=1)
        coarse=x+torch.tensor([25000.,12000.,-7000.,-28000.,120000.,-85000.]*((rows+5)//6))[:rows]
        return {'features':feat,'coarse':coarse,'target':x}

    def test_six_banks_are_sequential_and_initially_identical_to_stage2(self):
        h=fine.LocalRefiner(5,kind='gaussian',registers=9)
        casc=stack.Cascade(5,h,zoom=.72)
        d=self.sample()
        out=casc.upto(d)
        self.assertEqual(sorted(out),list(range(2,9)))
        self.assertEqual(len(casc.banks),6)
        for k in stack.STAGES:self.assertTrue(torch.equal(out[2],out[k]))
        radii=[casc.banks[str(k)].floor for k in stack.STAGES]
        self.assertTrue(all(radii[i]>radii[i+1] for i in range(5)))
        self.assertFalse(any(p.requires_grad for p in casc.stage2.parameters()))

    def test_real_chain_uses_predecessor_posterior_and_refines_sequentially(self):
        h=fine.LocalRefiner(5,kind='gaussian',registers=9)
        casc=stack.Cascade(5,h,zoom=.72)
        d=self.sample()
        with torch.no_grad():
            casc.banks['3'].readout.bias[-1]=2.0
            casc.banks['4'].readout.bias[0]=2.0
        out=casc.upto(d)
        self.assertGreater(float((out[3]-out[2]).abs().mean().detach()),100.)
        self.assertGreater(float((out[4]-out[3]).abs().mean().detach()),100.)
        self.assertTrue(torch.equal(out[4],out[8]))
        # A later bank is centered on the prior prediction, not on stage 2.
        expected_inputs=casc.active_inputs(d,4)
        self.assertTrue(torch.allclose(expected_inputs['coarse'],out[3]))
        self.assertFalse(torch.allclose(expected_inputs['features'][:,-6],torch.zeros_like(expected_inputs['features'][:,-6])))
        self.assertEqual(expected_inputs['features'].shape[1],5+9+6)

    def test_ground_truth_cannot_change_any_forward_output(self):
        casc=stack.Cascade(5,fine.LocalRefiner(5,kind='gaussian',registers=9))
        d=self.sample()
        with torch.no_grad():casc.banks['3'].readout.bias[-1]=3.
        original=casc.upto(d)
        altered=casc.upto({**d,'target':torch.full_like(d['target'], -999999.)})
        for k in original:self.assertTrue(torch.equal(original[k],altered[k]))

    def test_active_stage_boundaries_and_budget(self):
        self.assertEqual([stack.active_stage(k,3) for k in (1,3,4,6,7,9,10,12,13,15,16,18)],
                         [3,3,4,4,5,5,6,6,7,7,8,8])
        for k in (0,19):
            with self.assertRaisesRegex(ValueError,'OUTSIDE_BUDGET'):stack.active_stage(k,3)

    def test_zoom_is_fine_and_distinct_even_near_zero(self):
        casc=stack.Cascade(5,fine.LocalRefiner(5,kind='gaussian',registers=9),zoom=.72)
        self.assertGreater(casc.banks['3'].floor,casc.banks['8'].floor)
        for k in stack.STAGES:
            h=casc.banks[str(k)]
            self.assertEqual(h.kind,'gaussian')
            self.assertTrue(torch.isfinite(h.centers).all())
            self.assertEqual(h.registers,9)

    def test_parent_snapshot_verifies_hash_and_architecture(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source=root/'source';teacher=root/'teacher'
            source.write_bytes(b'S');teacher.write_bytes(b'T')
            run=root/'fine';best=run/'best_checkpoints';best.mkdir(parents=True)
            original=b'original';donor=b'donor';first=b'first'
            # Invalid zip provenance must be refused, even if file exists.
            (run/'parent_snapshot.pt').write_bytes(original)
            (run/'donor_snapshot.pt').write_bytes(donor)
            path=best/'best_small_gaussian.pt';path.write_bytes(first)
            path.with_suffix('.json').write_text(json.dumps({'checkpoint_sha256':'bad','arm':'gaussian','step':0}))
            args=stack.parser().parse_args(['--refinement-checkpoint',str(path),
              '--source',str(source),'--teacher',str(teacher),'--output',str(root/'output')])
            with self.assertRaises(Exception):stack.check_parent(args)

    def test_end_to_end_six_banks_resume_and_test_split_isolated(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            source=root/'source';teacher=root/'teacher';source.write_bytes(b'S');teacher.write_bytes(b'T')
            fine_root=root/'fine';best=fine_root/'best_checkpoints';best.mkdir(parents=True)
            original={'schema':'fake-original'};donor={'schema':'fake-donor'}
            buf=io.BytesIO();torch.save(original,buf);original_bytes=buf.getvalue();(fine_root/'parent_snapshot.pt').write_bytes(original_bytes)
            buf=io.BytesIO();torch.save(donor,buf);donor_bytes=buf.getvalue();(fine_root/'donor_snapshot.pt').write_bytes(donor_bytes)
            first_head=fine.LocalRefiner(5,kind='gaussian',registers=9)
            id_={'schema':fine.SCHEMA,'parent_arm':'continuous','parent_sha256':stack.digest(original_bytes),
                'donor_sha256':stack.digest(donor_bytes),'source_sha256':fine.fork.fingerprint(source),
                'teacher_sha256':fine.fork.fingerprint(teacher),'seed':20261018,'dev_per_tier':8,
                'train_per_tier':8,'registers':9,'sigma_bins':2.,'floor':125000.,
                'fraction':.5,'temperature':8.}
            first={'schema':fine.SCHEMA,'identity':id_,'step':300,
                   'heads':{'gaussian':first_head.state_dict()}}
            path=best/'best_small_gaussian.pt';torch.save(first,path)
            train={t:self.sample(8) for t in stack.TIERS}
            dev={t:self.sample(8) for t in stack.TIERS}
            expected=stack.probe(stack.Cascade(5,first_head),dev,40,250000,0)
            path.with_suffix('.json').write_text(json.dumps({
                'checkpoint_sha256':fine.fork.fingerprint(path),'arm':'gaussian',
                'step':300,'metric':'small',
                'rank':stack.rank(expected['stages']['2'],'small')}))
            args=stack.parser().parse_args(['--refinement-checkpoint',str(path),
               '--source',str(source),'--teacher',str(teacher),'--output',str(root/'stack'),
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
                return data,{t:[{'instance':f'{split}/{i//2}'} for i in range(8)] for t in stack.TIERS}
            with mock.patch.object(stack.fork.ex,'load_current',return_value=model),\
                 mock.patch.object(fine,'init_frozen',return_value=(object(),object())),\
                 mock.patch.object(stack.fork.cached,'FrozenCaptureCache',Cache),\
                 mock.patch.object(stack,'load_features',side_effect=records):
                original_sha=fine.fork.fingerprint(path)
                stack.run(args)
                self.assertNotIn('test',seen)
                latest=stack.master.load_state((root/'stack/latest.pt').read_bytes())
                self.assertEqual(latest['step'],4)
                self.assertEqual(original_sha,fine.fork.fingerprint(path))
                self.assertGreater(float(latest['bank_states']['3']['readout.weight'].abs().sum()),0.)
                self.assertGreater(float(latest['bank_states']['4']['readout.weight'].abs().sum()),0.)
                self.assertEqual(float(latest['bank_states']['5']['readout.weight'].abs().sum()),0.)
                args.additional_steps=2
                stack.run(args)
                self.assertEqual(stack.master.load_state((root/'stack/latest.pt').read_bytes())['step'],6)
                self.assertGreater(float(stack.master.load_state((root/'stack/latest.pt').read_bytes())['bank_states']['5']['readout.weight'].abs().sum()),0.)
                self.assertNotIn('test',seen)
                args.test_only=True;args.additional_steps=12
                seen.clear();stack.run(args)
                self.assertEqual(set(seen),{'test'})
                self.assertTrue(list((root/'stack').glob('test-small-stage8-*.json')))
                self.assertEqual(original_sha,fine.fork.fingerprint(path))

if __name__=='__main__':unittest.main()
