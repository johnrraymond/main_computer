"""V3 source-contract and end-to-end learned-population tests."""
import math
from pathlib import Path
import tempfile
import unittest
import torch
from tools import nanojev_hawk_dove_chi_population_blind_matrix as v3
from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as old


class PopulationV3Tests(unittest.TestCase):
    def test_2000_bank_chi_geometry(self):
        means,logs,alpha,basis,edges=v3.magnitude_geometry(2000,128,1e-5,5e6,1.65)
        self.assertEqual(int((means<1).sum()),1195)
        self.assertTrue(bool((means[1:]>means[:-1]).all()))
        self.assertLess(float((basis.sum(-1)-1).abs().max()),3e-6)
        self.assertGreater(float(2/alpha[0].sqrt()),1.)
        self.assertLess(float(2/alpha[-1].sqrt()),.04)

    def test_reference_bank_population_reconstructs_full_scale(self):
        model=v3.ChiMagnitudeNetwork(2000,64,16,8,1e-5,5e6,1.65)
        for number in (1e-5,.001,.01,.1,1.,1.5,2.,10.,1000.,1e5,1e6,4e6):
            target=torch.full((1,2),number)
            l,r,wl,wr=v3.reference_bank_targets(target,model)
            with torch.no_grad():
                layer=model.encoder[-1];layer.weight.zero_();layer.bias.fill_(-100.)
                for channel in range(2):
                    if float(wl[0,channel])>0:
                        layer.bias[channel*model.banks+l[0,channel]]=math.log(float(wl[0,channel]))
                    if float(wr[0,channel])>0:
                        layer.bias[channel*model.banks+r[0,channel]]=math.log(float(wr[0,channel]))
                    layer.bias[2*model.banks+channel*3+2]=20.
            predicted=model(torch.zeros(1,old.INPUT_DIM),with_density=False)
            self.assertLess(float(((predicted['numeric']-target).abs()/target).max().detach()),.001)

    def test_learned_bank_logit_ablation_moves_numerical_answer(self):
        model=v3.ChiMagnitudeNetwork(128,64,16,8,1e-5,5e6,1.65)
        features=torch.zeros(2,old.INPUT_DIM)
        with torch.no_grad():
            layer=model.encoder[-1];layer.weight.zero_();layer.bias.fill_(-30.)
            layer.bias[[0,model.banks]]=20.
            layer.bias[[2*model.banks+2,2*model.banks+5]]=20.
        low=model(features)
        self.assertTrue(bool(torch.allclose(low['magnitude'],
                           (low['weights']*model.bank_means).sum(-1))))
        with torch.no_grad():
            layer.bias[[0,model.banks]]=-30.
            layer.bias[[model.banks-1,2*model.banks-1]]=20.
        high=model(features)
        self.assertGreater(float((high['numeric']-low['numeric']).abs().min().detach()),1e6)
        self.assertGreater(float((high['density']-low['density']).abs().sum().detach()),.1)
        self.assertEqual(model.encoder[-1].out_features,2*model.banks+6)
        self.assertNotIn('proposal_log10',high)

    def test_exact_zero_and_zero_bank_supervision_mask(self):
        model=v3.ChiMagnitudeNetwork(32,48,12,5,1e-5,5e6,1.65)
        with torch.no_grad():
            final=model.encoder[-1];final.weight.zero_();final.bias.zero_()
            final.bias[[2*model.banks+1,2*model.banks+4]]=20.
        x=torch.ones(3,old.INPUT_DIM)
        self.assertTrue(bool((model(x)['numeric']==0).all()))
        loss,info=v3.loss_and_scores(model,x,torch.zeros(3,2))
        self.assertTrue(bool(torch.isfinite(loss)))
        self.assertAlmostEqual(float(info['bank_ce']),0.,places=6)

    def test_gradients_reach_learned_bank_logits_and_stay_finite(self):
        torch.manual_seed(100)
        model=v3.ChiMagnitudeNetwork(64,64,16,5,1e-5,5e6,1.65)
        x=torch.randn(6,old.INPUT_DIM)
        y=torch.tensor([[1.,2.],[0.,-4.],[100.,-1e5],[-3.,4.],[1e-5,.1],[-1e6,1e6]])
        loss,components=v3.loss_and_scores(model,x,y)
        self.assertTrue(bool(torch.isfinite(loss)))
        loss.backward()
        self.assertGreater(float(model.encoder[-1].weight.grad[:2*model.banks].abs().sum()),0.)
        self.assertTrue(bool(torch.isfinite(model.encoder[-1].weight.grad).all()))
        model.zero_grad(set_to_none=True)
        model(x,with_density=False)['magnitude'].sum().backward()
        self.assertGreater(float(model.encoder[-1].weight.grad[:2*model.banks].abs().sum()),0.)

    def test_tiny_mixed_scale_dataset_can_fit(self):
        torch.set_num_threads(2)
        torch.manual_seed(155)
        model=v3.ChiMagnitudeNetwork(128,96,32,1,1e-5,5e6,1.65)
        x=torch.zeros(5,old.INPUT_DIM);x[:,:5]=torch.eye(5)
        y=torch.tensor([[1.,2.],[-4.,300000.],[0.,0.],[1e6,-2e5],[-500.,-700.]])
        opt=torch.optim.AdamW(model.parameters(),lr=.003,weight_decay=0.)
        for step in range(450):
            opt.zero_grad(set_to_none=True)
            loss,_=v3.loss_and_scores(model,x,y,geometry_loss=False)
            loss.backward();opt.step()
        pred=model(x,with_density=False)['numeric']
        self.assertTrue(bool(((pred-y).abs()<=.05*y.abs()).all()),(pred.detach().tolist(),y.tolist()))

    def test_cpu_smoke_train_audit_predict_diagnose(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);output=root/'run'
            args=['train','--device','cpu','--output',str(output),
              '--banks','32','--grid-bins','48','--hidden','8',
              '--train-matrices','4','--dev-matrices','2','--test-matrices','2',
              '--counts-per-matrix','4','--masks','1','--batch-size','16',
              '--steps','3','--probe-every','1','--eval-batch-size','32']
            report=v3.train(v3.parser().parse_args(args))
            self.assertTrue(report['numeric_depends_on_learned_bank_weights'])
            self.assertFalse(report['oracle_XY_in_input'])
            self.assertFalse(report['uses_test_for_training_or_promotion'])
            audit=v3.audit(v3.parser().parse_args(['audit','--checkpoint',str(output/'best.pt')]))
            self.assertEqual(audit['splits']['dev']['gate'],report['dev']['gate'])
            prompt=root/'prompt.txt'
            prompt.write_text(old.render_blind('T08',400000,(1,-2,3,4),0))
            prediction=v3.predict(v3.parser().parse_args(['predict','--checkpoint',
                str(output/'best.pt'),'--prompt-file',str(prompt),'--tier','T08']))
            self.assertFalse(prediction['oracle_labels_read'])
            diagnosis=v3.diagnose(v3.parser().parse_args(['diagnose','--checkpoint',
                str(output/'best.pt'),'--output',str(root/'diagnosis')]))
            self.assertTrue((root/'diagnosis'/'dev_cases.csv').exists())
            self.assertEqual(diagnosis['geometry']['decode'],
                             'learned bank activations, gamma analytical means')
            with self.assertRaises(FileExistsError):v3.train(v3.parser().parse_args(args))

if __name__=='__main__':unittest.main()
