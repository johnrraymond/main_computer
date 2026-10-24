"""Contract tests for unsigned, small-dense gamma magnitude population."""
import math
from pathlib import Path
import tempfile
import unittest

import torch

from tools import nanojev_hawk_dove_chi_magnitude_blind_matrix as c
from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as old


class ChiMagnitudeTests(unittest.TestCase):
    def test_dense_near_zero_and_sparse_in_absolute_units(self):
        centers, logs, alpha, basis, edges=c.magnitude_geometry(2000,256,1e-5,5e6,1.65)
        self.assertTrue(bool((centers[1:]>centers[:-1]).all()))
        self.assertTrue(bool((alpha[1:]>=alpha[:-1]).all()))
        self.assertEqual(int((centers < 1).sum()),1195)
        self.assertLess(int((centers >= 1e5).sum()),200)
        self.assertTrue(bool((basis>=0).all()))
        self.assertLess(float((basis.sum(-1)-1).abs().max()),2e-6)
        self.assertGreater(float(2/alpha[0].sqrt()),1.)
        self.assertLess(float(2/alpha[-1].sqrt()),.04)
        self.assertLess(float(centers[10]-centers[9]),float(centers[-1]-centers[-2]))

    def test_oracle_reference_geometry_interpolates_subunit_and_large(self):
        args=c.parser().parse_args(['inspect-banks','--banks','200','--grid-bins','64'])
        report=c.inspect_banks(args)
        self.assertEqual(sum(s['banks'] for s in report['shell_allocation']),200)
        self.assertTrue(all(e['relative_error']<1e-6 for e in report['reference_interpolation_only_not_learned']))

    def test_independent_sign_exact_zero_and_gamma_mass(self):
        model=c.ChiMagnitudeNetwork(64,64,8,5,1e-5,5e6,1.65)
        with torch.no_grad():
            # Make exact-zero class dominant for both output heads.
            model.encoder[-1].weight.zero_()
            model.encoder[-1].bias.zero_()
            model.encoder[-1].bias[[3,6]] = 12.
        f=torch.randn(4,old.INPUT_DIM)
        out=model(f)
        self.assertTrue(bool((out['numeric']==0).all()))
        self.assertTrue(bool((out['density']>=0).all()))
        self.assertLess(float((out['density'].sum(-1)-1).abs().max().detach()),2e-6)
        self.assertTrue(bool((out['magnitude']>0).all()))

    def test_small_magnitude_supervision_finite_gradient(self):
        model=c.ChiMagnitudeNetwork(64,64,12,8,1e-5,5e6,1.65)
        f=torch.randn(5,old.INPUT_DIM)
        y=torch.tensor([[0.,0.],[1.,-1.],[1e-5,.01],[-1e6,1e6],[100.,-2.]])
        loss,components=c.loss_and_scores(model,f,y)
        self.assertTrue(bool(torch.isfinite(loss)))
        loss.backward()
        self.assertGreater(float(model.encoder[0].weight.grad.abs().sum()),0)
        self.assertTrue(bool(torch.isfinite(model.encoder[0].weight.grad).all()))
        self.assertGreater(float(components['gamma_kl'].detach()),0)

    def test_oracle_center_is_not_distorted_by_bank_aggregation(self):
        # Regression for V1: a perfectly correct log-space center of 1e6
        # decoded to ~1.068e6, violating the 5% certification before learning.
        # Every reference sample must decode within 5% AND match its gamma
        # density sufficiently closely across the full magnitude span.
        model=c.ChiMagnitudeNetwork(2000,512,128,4,1e-5,5e6,1.65)
        for number in (1e-5,.001,.01,.1,1.,1.5,2.,10.,100.,1e3,1e4,1e5,1e6,4e6):
            lo,hi=math.log10(model.min_mag),math.log10(model.max_mag)
            loc=(math.log10(number)-lo)/(hi-lo)
            model.encoder[-1].weight.data.zero_()
            model.encoder[-1].bias.data.zero_()
            logit=math.log(loc/(1-loc)) if 0<loc<1 else (-20. if loc<=0 else 20.)
            model.encoder[-1].bias.data[:2]=logit
            model.encoder[-1].bias.data[[4,7]]=20.
            values=torch.full((1,2),number)
            prediction=model(torch.zeros(1,old.INPUT_DIM))
            for output in range(2):
                recovered=float(prediction['numeric'][0,output].detach())
                self.assertLess(abs(recovered-number)/number,.01,(number,recovered))
                expected=c.gamma_target(values,torch.zeros_like(values,dtype=torch.bool),model)
                tv=float(.5*(prediction['density'][0,output]-expected[0,output]).abs().sum().detach())
                self.assertLess(tv,.05,(number,tv))

    def test_interpolated_gamma_weights_train_center(self):
        model=c.ChiMagnitudeNetwork(128,128,16,4,1e-5,5e6,1.65)
        output=model(torch.ones(8,old.INPUT_DIM))
        self.assertLess(float((output['weights'].sum(-1)-1).abs().max().detach()),1e-6)
        self.assertLess(float((output['weights']>0).sum(-1).max()),3)
        output['magnitude'].sum().backward()
        self.assertGreater(float(model.encoder[-1].weight.grad[:2].abs().sum()),0)

    def test_prompt_features_still_oracle_blind(self):
        for tier in old.TIERS:
            for mask in (0,1):
                prompt=old.render_blind(tier,503001,(2,-3,1,4),mask)
                parsed=old.parse_blind(prompt,tier,mask)
                self.assertEqual(parsed['a'],503001)
                self.assertEqual(len(old.features_from_prompt(prompt,tier,mask,4)),old.INPUT_DIM)

    def test_cpu_smoke_train_audit_and_standalone_predict(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); output=root/'run'
            args=c.parser().parse_args(['train','--device','cpu','--output',str(output),
                 '--banks','32','--grid-bins','48','--hidden','8',
                 '--train-matrices','4','--dev-matrices','2','--test-matrices','2',
                 '--counts-per-matrix','4','--masks','1','--batch-size','16',
                 '--steps','3','--probe-every','1','--eval-batch-size','32'])
            result=c.train(args)
            self.assertEqual(result['identity']['bank_geometry'],
                             'small-dense log10 magnitude centers, gamma chi-square kernels')
            self.assertEqual(result['identity']['zero'],'separate categorical atom, exact numerical 0')
            self.assertFalse(result['uses_test_for_training_or_promotion'])
            self.assertEqual(result['status'],'FAIL')
            audit=c.audit(c.parser().parse_args(['audit','--checkpoint',str(output/'best.pt'),
                                                   '--output',str(root/'audit.json')]))
            self.assertEqual(audit['status'],result['status'])
            prompt=root/'prompt.txt'
            prompt.write_text(old.render_blind('T08',400000,(1,-2,3,4),0),encoding='utf8')
            pred=c.predict(c.parser().parse_args(['predict','--checkpoint',str(output/'best.pt'),
                    '--prompt-file',str(prompt),'--tier','T08']))
            self.assertFalse(pred['oracle_labels_read'])
            self.assertTrue(math.isfinite(pred['predicted_X']))
            diag=c.diagnose(c.parser().parse_args(['diagnose','--checkpoint',str(output/'best.pt'),
                              '--output',str(root/'diagnosis')]))
            self.assertTrue((root/'diagnosis'/'dev_cases.csv').is_file())
            self.assertEqual(diag['splits']['dev']['by_tier']['T01']['joint_rate'],
                             audit['splits']['dev']['gate']['tiers']['T01']['joint_all_masks_instance_rate'])
            with self.assertRaises(FileExistsError):c.train(args)

if __name__=='__main__':unittest.main()
