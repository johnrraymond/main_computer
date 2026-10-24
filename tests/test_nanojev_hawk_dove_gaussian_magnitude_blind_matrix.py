"""V4 regression checks: Gaussian kernels + continuous location + local populations."""
from pathlib import Path
import tempfile
import unittest
import torch
from tools import nanojev_hawk_dove_gaussian_magnitude_blind_matrix as v4
from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as old


class GaussianMagnitudeV4Tests(unittest.TestCase):
    def test_2000_gaussian_banks_are_small_dense_and_normalized(self):
        means,logs,sigma,basis,edges=v4.magnitude_geometry(2000,128,1e-5,5e6,1.65)
        self.assertEqual(int((means<1).sum()),1195)
        self.assertTrue(bool((means[1:]>means[:-1]).all()))
        self.assertLess(float((basis.sum(-1)-1).abs().max()),3e-6)
        self.assertTrue(bool(torch.isfinite(basis).all()))
        self.assertTrue(bool((sigma>0).all()))
        self.assertAlmostEqual(float(sigma[-1]/means[-1]),.06,places=5)

    def test_oracle_local_population_exactly_reconstructs_magnitudes(self):
        model=v4.GaussianMagnitudeNetwork(2000,64,16,3,1e-5,5e6,1.65)
        for value in (1e-5,.001,.01,.1,1.,2.,10.,100.,1000.,1e5,1e6,4e6):
            target=torch.full((1,2),value)
            weights=model.activate_local_banks(target)
            numeric,magnitude=model.decode_population(weights,torch.full((1,2),2))
            self.assertLess(float(((numeric-target).abs()/target).max()),.0002)
            self.assertTrue(bool((weights>0).sum(-1).le(2).all()))
            self.assertLess(float((weights.sum(-1)-1).abs().max()),1e-6)

    def test_continuous_location_and_gaussian_population_control_numbers(self):
        model=v4.GaussianMagnitudeNetwork(128,64,16,1,1e-5,5e6,1.65)
        f=torch.zeros(1,old.INPUT_DIM)
        with torch.no_grad():
            head=model.encoder[-1]
            head.weight.zero_();head.bias.zero_()
            head.bias[[4,7]]=20.  # sign positive for X and Y
            head.bias[:2]=.02
        low=model(f)
        with torch.no_grad():head.bias[:2]=.35
        high=model(f)
        self.assertGreater(float((high['numeric']-low['numeric']).abs().min().detach()),1e6)
        self.assertGreater(float((high['density']-low['density']).abs().sum().detach()),.1)
        self.assertEqual(model.encoder[-1].out_features,8)
        self.assertTrue(bool(torch.allclose(high['numeric'],
                            (high['weights']*model.bank_means).sum(-1),atol=.1)))
        self.assertLessEqual(int((high['weights']>0).sum(-1).max()),2)

    def test_zero_is_exact_categorical_not_a_small_gaussian(self):
        model=v4.GaussianMagnitudeNetwork(32,48,12,1,1e-5,5e6,1.65)
        with torch.no_grad():
            head=model.encoder[-1];head.weight.zero_();head.bias.zero_()
            head.bias[[3,6]]=20.  # zero class indices 2+1, 2+4
        features=torch.ones(3,old.INPUT_DIM)
        self.assertTrue(bool((model(features)['numeric']==0).all()))
        loss,parts=v4.loss_and_scores(model,features,torch.zeros(3,2))
        self.assertTrue(bool(torch.isfinite(loss)))
        self.assertIn('location_mse',parts)

    def test_location_supervision_reaches_original_coordinate(self):
        torch.manual_seed(7)
        model=v4.GaussianMagnitudeNetwork(48,48,16,1,1e-5,5e6,1.65)
        x=torch.randn(8,old.INPUT_DIM)
        y=torch.tensor([[1e6,2e6],[-3e6,2e6],[0.,0.],[-1e6,-2e6],
                        [2e5,1e5],[-3e5,4e5],[1.,-1.],[2e6,0.]])
        loss,parts=v4.loss_and_scores(model,x,y)
        loss.backward()
        self.assertGreater(float(model.encoder[-1].weight.grad[:2].abs().sum()),0.)
        self.assertTrue(bool(torch.isfinite(model.encoder[-1].weight.grad).all()))
        model.zero_grad(set_to_none=True)
        model(x,with_density=False)['magnitude'].sum().backward()
        self.assertGreater(float(model.encoder[-1].weight.grad[:2].abs().sum()),0.)

    def test_tiny_signed_continuous_locations_fit_without_magnitude_conflict(self):
        torch.set_num_threads(2)
        torch.manual_seed(55)
        model=v4.GaussianMagnitudeNetwork(128,64,32,1,1e-5,5e6,1.65)
        x=torch.zeros(6,old.INPUT_DIM);x[:,:6]=torch.eye(6)
        y=torch.tensor([[1e6,2e6],[-2e6,1e6],[0.,0.],
                        [-1e6,-2e6],[-3e5,4e5],[1e5,-1e5]])
        opt=torch.optim.AdamW(model.parameters(),lr=.003,weight_decay=0.)
        for _ in range(250):
            opt.zero_grad(set_to_none=True)
            loss,_=v4.loss_and_scores(model,x,y,geometry_loss=False)
            loss.backward();opt.step()
        prediction=model(x,with_density=False)['numeric']
        self.assertTrue(bool(((prediction-y).abs()<=.05*y.abs()).all()),
                        prediction.detach().tolist())

    def test_cpu_train_audit_diagnose_and_predict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);output=root/'run'
            arg=['train','--device','cpu','--output',str(output),
                 '--banks','32','--grid-bins','48','--hidden','8',
                 '--train-matrices','4','--dev-matrices','2','--test-matrices','2',
                 '--counts-per-matrix','4','--masks','1','--batch-size','16',
                 '--steps','3','--probe-every','1','--eval-batch-size','32']
            report=v4.train(v4.parser().parse_args(arg))
            self.assertTrue(report['numeric_depends_on_localized_bank_weights'])
            self.assertFalse(report['uses_test_for_training_or_promotion'])
            audit=v4.audit(v4.parser().parse_args(['audit','--checkpoint',str(output/'best.pt')]))
            self.assertEqual(audit['splits']['dev']['gate'],report['dev']['gate'])
            prompt=root/'prompt.txt'
            prompt.write_text(old.render_blind('T08',400000,(1,-2,3,4),0))
            prediction=v4.predict(v4.parser().parse_args(['predict','--checkpoint',
                str(output/'best.pt'),'--prompt-file',str(prompt),'--tier','T08']))
            self.assertFalse(prediction['oracle_labels_read'])
            diagnosis=v4.diagnose(v4.parser().parse_args(['diagnose','--checkpoint',
                str(output/'best.pt'),'--output',str(root/'diagnosis')]))
            self.assertTrue((root/'diagnosis'/'dev_cases.csv').exists())
            self.assertEqual(diagnosis['geometry']['decode'],
                             'continuous location to localized Gaussian bank means')
            with self.assertRaises(FileExistsError):v4.train(v4.parser().parse_args(arg))

if __name__=='__main__':unittest.main()
