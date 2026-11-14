"""CPU tests: math, gradients, no-label operand extraction, identity, and controls."""
from __future__ import annotations
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import torch

from tools import nanojev_hawk_dove_million as hd
from tools import nanojev_hawk_dove_continuous_benchmark as bm


class ContinuousT02BenchTests(unittest.TestCase):
    def test_signed_symlog_is_continuous_invertible(self):
        vals=torch.tensor([-2e6,-1e6,-1e5,-1.,0.,1.,1e5,1e6,3e6])
        out=bm.symexp(bm.symlog(vals))
        self.assertTrue(torch.allclose(vals,out,rtol=1e-6,atol=0.1))
        self.assertEqual(bm.symlog(vals)[4].item(),0.)
        self.assertLess((bm.symlog(torch.tensor([99999.]))-bm.symlog(torch.tensor([100001.]))).abs().item(),0.001)

    def test_each_continuous_head_backpropagates_to_both_slots(self):
        true=torch.tensor([[320000.,495000.],[-200000.,300000.],[0.,0.],[3000000.,1500000.]])
        for method in ('direct','symlog','twohot'):
            with self.subTest(method=method):
                model=bm.create_model(32,method)
                inp=torch.randn(4,2,32,requires_grad=True)
                out=model(inp)
                loss=bm.training_loss(out,true,method)
                self.assertTrue(torch.isfinite(loss))
                loss.backward()
                self.assertGreater(inp.grad[:,0,:].abs().sum().item(),0)
                self.assertGreater(inp.grad[:,1,:].abs().sum().item(),0)

    def test_twohot_exact_boundary_and_mass(self):
        val=torch.tensor([[0.,bm.GRID_MIN],[bm.GRID_MAX,1.25]])
        logits=torch.zeros(2,2,bm.GRID_SIZE,requires_grad=True)
        loss=bm.twohot_loss(logits,val)
        self.assertAlmostEqual(loss.item(),np.log(bm.GRID_SIZE),places=4)
        loss.backward()
        self.assertTrue(torch.isfinite(logits.grad).all())
        with self.assertRaises(ValueError):bm.twohot_loss(logits,torch.full((2,2),bm.GRID_MAX+1))

    def test_raw_regression_negative_and_zero(self):
        for value in (-1e6, -200000.,0.,100000.,3e6):
            x=torch.tensor([[[value/bm.SCALE]]])
            decoded=bm.decode(x,'direct')
            self.assertAlmostEqual(decoded.item(),value,delta=0.3)

    def test_operand_extraction_independent_of_labels(self):
        payoffs={k:Decimal(v) for k,v in zip(hd.TAGS,('-4','3','0','1.5'))}
        for full in ((),hd.SLOTS['T02']):
            prompt=hd.render('T02',350000,650000,payoffs,full,hd.REP_SCIENCE)
            self.assertEqual(bm.operands_from_t02(prompt),[.35,.65])
        with self.assertRaises(ValueError):bm.operands_from_t02('(ARGMAX 100 200)')

    def test_shuffled_t02_target_not_used_for_operand_extraction(self):
        payoffs={k:Decimal(v) for k,v in zip(hd.TAGS,('-4','3','0','1.5'))}
        prompt=hd.render('T02',705000,295000,payoffs,hd.SLOTS['T02'][:2],hd.REP_SCIENCE)
        self.assertEqual(bm.operands_from_t02(prompt),[.705,.295])
        # Tampering with the supplied Y does not change parsed numerical inputs.
        self.assertEqual(bm.operands_from_t02(prompt.replace('442500','999999')),[.705,.295])

    def test_trainable_linear_control_generalizes_the_actual_linear_rule(self):
        torch.manual_seed(7)
        model=bm.create_model(192,'operands')
        optimizer=torch.optim.Adam(model.parameters(),lr=.045)
        av=torch.linspace(0.,1.,151)
        input=torch.stack([av,1-av],dim=-1)
        b=input[:,1]
        outputs=torch.stack([3*b-av,1.5*b],dim=-1)
        for _ in range(600):
            optimizer.zero_grad()
            loss=bm.training_loss(model(input),outputs*bm.SCALE,'operands')
            loss.backward();optimizer.step()
        test_a=torch.tensor([.047,.269,.561,.715,.827,.953])
        t=torch.stack([test_a,1-test_a],dim=-1)
        expect=torch.stack([3*t[:,1]-t[:,0],1.5*t[:,1]],dim=-1)
        self.assertLess((model(t)-expect).abs().max().item(),0.012)

    def test_accuracy_absolute_and_relative_metrics(self):
        gold=np.array([[1e5,-1e5],[0,0],[1e6,1.5e6]],dtype=np.float32)
        pred=gold.copy();pred[0,0]+=8000
        metric=bm.accuracy(pred,gold)
        self.assertAlmostEqual(metric['x_5pct'],2/3)
        self.assertAlmostEqual(metric['y_5pct'],1.)
        self.assertAlmostEqual(metric['joint_5pct'],2/3)

    def test_feature_file_hash_and_identity_verified(self):
        with tempfile.TemporaryDirectory() as base:
            root=Path(base)
            args=SimpleNamespace(root=root,donor=root/'donor',teacher=root/'teacher')
            args.donor.mkdir();(args.donor/'latest.pt').write_bytes(b'fake-ckpt')
            (root/'teacher').write_bytes(b'fixture')
            (root/'manifest.json').write_text('{}')
            values=np.ones((2,2,8),np.float32)
            bm.save_dataset(args,'dev',values,np.ones((2,2)),np.zeros((2,2)),np.ones((2,2)),[0,1],[0,1],['a','b'])
            self.assertEqual(bm.load_dataset(args,'dev')['features'].shape,(2,2,8))
            with (root/'features-dev.npz').open('ab') as f:f.write(b'evil')
            with self.assertRaises(ValueError):bm.load_dataset(args,'dev')

    def test_end_to_end_synthetic_readouts_and_probe(self):
        with tempfile.TemporaryDirectory() as base:
            root=Path(base)
            donor=root/'donor';donor.mkdir()
            (donor/'latest.pt').write_bytes(b'synthetic frozen donor')
            (root/'teacher').write_bytes(b'synthetic teacher')
            (root/'manifest.json').write_text('{}')
            args=SimpleNamespace(root=root,donor=donor,teacher=root/'teacher',
                train_device='cpu',epochs=8,seed=24,learning_rate=0.003,
                train_batch_size=16)
            rng=np.random.default_rng(3)
            for split,ns in [('train',12),('dev',3)]:
                av=np.repeat(np.linspace(.02,.98,ns),8)
                bv=1-av
                inputs=np.column_stack((av,bv)).astype(np.float32)
                targets=np.column_stack(((3*bv-av)*1e6,1.5*bv*1e6)).astype(np.float32)
                feat=np.zeros((len(inputs),2,8),dtype=np.float32)
                feat[:,0,0]=inputs[:,0]
                feat[:,0,1]=inputs[:,1]
                feat[:,1,0]=inputs[:,0]
                feat[:,1,1]=inputs[:,1]
                bm.save_dataset(args,split,feat,targets,inputs,np.zeros_like(targets),
                    np.tile(np.arange(8),ns),np.repeat(np.arange(ns),8),
                    [f'{i:04d}/T02/{j:02d}' for i in range(ns) for j in range(8)])
            with patch.object(bm,'verify',return_value={}):
                bm.fit(args)
                summary=bm.report(args,'dev')
            self.assertEqual(set(summary['report']),{'original_V3',*bm.METHODS})
            self.assertEqual(summary['n'],24)
            self.assertEqual(set(summary['report']['direct']['per_mask']),set(str(i) for i in range(8)))
            self.assertTrue(all((root/(method+'.pt')).exists() for method in bm.METHODS))

    def test_control_only_reads_t02_multiplication_operands(self):
        payoffs={k:Decimal(v) for k,v in zip(hd.TAGS,('-4','3','0','1.5'))}
        a,b=940000,60000
        for full in ((),hd.SLOTS['T02']):
            p=hd.render('T02',a,b,payoffs,full,hd.REP_SCIENCE)
            self.assertEqual(bm.operands_from_t02(p),[.94,.06])

if __name__=='__main__':unittest.main()
