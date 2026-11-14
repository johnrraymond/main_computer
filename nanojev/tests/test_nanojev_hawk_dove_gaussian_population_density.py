"""CPU checks for sequential population density experiments and arbitrary --only counts."""
from __future__ import annotations

import argparse
import csv
import json
import tempfile
import unittest
from pathlib import Path

import torch
from tools import nanojev_hawk_dove_gaussian_population_density as population


class GaussianPopulationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)

    def test_only_defaults_and_arbitrary_counts(self):
        p=population.parser()
        self.assertEqual(p.parse_args([]).only,(200,2000,20000,200000))
        self.assertEqual(p.parse_args(['--only','5000,50000']).only,(5000,50000))
        self.assertEqual(p.parse_args(['--only','12,2,7']).only,(12,2,7))
        for bad in ('0','-2','2,,3','2,2','3.2','1,a',''):
            with self.assertRaises(argparse.ArgumentTypeError):
                population.parse_counts(bad)

    def test_literal_density_addition_and_conservation(self):
        idx, kernel = population.bank_geometry(230, 128, 17, torch.device('cpu'))
        weights = torch.zeros(230, requires_grad=True)
        density = population.accumulate(weights, idx, kernel, 128)
        self.assertAlmostEqual(float(density.detach().sum()),1.,places=5)
        self.assertGreaterEqual(float(density.detach().min()),0.)
        # Each kernel contributes full mass; far-off supports are exactly zero
        # under the explicitly stated five-sigma truncation.
        self.assertTrue(bool(torch.allclose(kernel.sum(-1),torch.ones(230),atol=1e-6)))
        loss=density.square().sum();loss.backward()
        self.assertTrue(bool(torch.isfinite(weights.grad).all()))
        self.assertGreater(float(weights.grad.abs().sum()),0.)

    def test_nested_bank_geometry_even_for_custom_counts(self):
        a=population.bank_geometry(25,128,123,torch.device('cpu'))
        b=population.bank_geometry(50,128,123,torch.device('cpu'))
        self.assertTrue(torch.equal(a[0],b[0][:25]))
        self.assertTrue(torch.equal(a[1],b[1][:25]))

    def test_small_sequential_real_learning_and_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            args=population.parser().parse_args(['--only','32,250', '--device','cpu',
                '--steps','24','--probe-every','12','--grid-bins','64','--seed','47',
                '--output',tmp])
            comparison=population.run(args)
            self.assertEqual(comparison['order'],[32,250])
            self.assertEqual([r['bank_count'] for r in comparison['records']],[32,250])
            for n in (32,250):
                directory=Path(tmp)/f'banks-{n:09d}'
                summary=json.loads((directory/'summary.json').read_text())
                self.assertEqual(summary['status'],'COMPLETE')
                self.assertEqual(len(summary['history']),3)
                self.assertLess(summary['final_reconstruction']['total_variation'],
                                summary['initial_reconstruction']['total_variation'])
                self.assertAlmostEqual(summary['final_reconstruction']['total_probability_mass'],1.,places=5)
                self.assertTrue((directory/'curves-step-000024.svg').exists())
                with (directory/'curves-step-000024.csv').open() as handle:
                    points=list(csv.DictReader(handle))
                self.assertEqual(len(points),64)
                self.assertEqual(set(points[0]),{'x_fraction','target_mass',
                    'uniform_population_mass','learned_population_mass',
                    'residual_target_minus_learned'})
            prior = population.run(args)
            self.assertEqual(prior['records'][0]['final_total_variation'],
                             comparison['records'][0]['final_total_variation'])
            args.steps=25
            with self.assertRaisesRegex(ValueError,'POPULATION_CONFIG_DRIFT'):
                population.run(args)

    def test_preflight_fails_before_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'new'
            args=population.parser().parse_args(['--only','200,200000',
                 '--device','cpu','--memory-limit-mb','1','--output',str(out)])
            with self.assertRaisesRegex(ValueError,'POPULATION_MEMORY_BUDGET_EXCEEDED'):
                population.run(args)
            self.assertFalse(out.exists())


if __name__=='__main__': unittest.main()
