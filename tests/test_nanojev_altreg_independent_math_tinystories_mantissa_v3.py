"""CPU-only regression checks for the separate mantissa v3 fork."""
import sys
from pathlib import Path
import unittest
import torch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import nanojev_altreg_independent_math_tinystories_mantissa_v3 as v3


class MantissaTests(unittest.TestCase):
    def test_sign_exponent_mantissa(self):
        s,e,m=v3.log_parts(torch.tensor([[-135.,27.],[0.,1.]]))
        self.assertEqual(s.tolist(),[[-1.,1.],[0.,1.]])
        self.assertEqual(e.tolist(),[[2.,1.],[0.,0.]])
        self.assertAlmostEqual(float(m[0,0]),0.13033377,places=5)

    def test_identity_at_fork(self):
        a=v3.Reconstructor(32)
        de,dm,_,_=a(torch.randn(5,32),torch.randn(5,2))
        self.assertTrue(torch.equal(de,torch.zeros_like(de)))
        self.assertTrue(torch.equal(dm,torch.zeros_like(dm)))

    def test_frozen_parent_unchanged(self):
        v3.base.OP_IDS
        m=v3.base.MathHead(32,16,len(v3.base.OP_IDS))
        for p in m.parameters():p.requires_grad_(False)
        a=v3.Reconstructor(32)
        features=torch.randn(8,32)
        ops=torch.tensor([i%len(v3.base.OP_IDS) for i in range(8)])
        with torch.no_grad():old_xy,old_r,old_f=m(features,ops)
        corrected,r,f,_,_=v3.decoded(m,a,features,ops)
        self.assertTrue(torch.allclose(corrected,old_xy,atol=1e-6))
        self.assertTrue(torch.equal(r,old_r))
        self.assertTrue(torch.equal(f,old_f))
        self.assertFalse(any(p.requires_grad for p in m.parameters()))

    def test_precision_supervision_gradients(self):
        a=v3.Reconstructor(32)
        z=torch.randn(8,32);coord=torch.rand(8,2)*.1
        truth=torch.tensor([[135.,-27.]]*8)
        old=torch.tensor([[13.5,-27.]]*8)
        terms=v3.loss_terms(a(z,coord),old,truth,torch.asinh(truth)/v3.base.X_SCALE,
                            torch.asinh(old)/v3.base.X_SCALE)
        sum(terms.values()).backward()
        self.assertGreater(float(terms['relative']),0)
        self.assertGreater(float(a.exponent.weight.grad.abs().sum()),0)
        self.assertGreater(float(a.mantissa.weight.grad.abs().sum()),0)

    def test_no_orientation_specialization(self):
        src=Path(v3.__file__).read_text(encoding='utf-8')
        self.assertIn("stage",Path(v3.base.__file__).read_text(encoding='utf-8'))
        self.assertIn('((step-1)*args.batch_size+i)%2==1',src)
        self.assertIn("min(row['joint_5pct'] for row in order)",src)

    def test_parent_identity_rejected(self):
        a=v3.Reconstructor(32)
        with self.assertRaisesRegex(RuntimeError,'IDENTITY_DRIFT'):
            v3.check_snapshot({'schema':v3.SCHEMA,'config':{'a':1}},a,{'a':2})

if __name__=='__main__':unittest.main()
