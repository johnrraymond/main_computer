"""Read-only root-cause diagnostic checks for oracle-blind matrix holdout."""
from __future__ import annotations

import csv
import hashlib
from pathlib import Path
import tempfile
import unittest

import torch

from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as bm
from tools import nanojev_hawk_dove_gaussian_population_blind_matrix_diagnose as d


class BlindMatrixDiagnosisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)

    def test_zero_strictness_and_center_vs_bank_shift(self):
        matrix=(1,-1,2,-2)
        a=500000  # Both arithmetic targets are exactly zero.
        group=[]
        for mask in (0,1):
            row={'prompt':bm.render_blind('T03',a,matrix,mask),'tier':'T03',
                 'mask':mask,'instance':'dev/matrix-0/a-500000',
                 'matrix_index':0,'x':0.,'y':0.}
            out=d._single_row(row,(1.,0.),(0.,0.),0.01,0.01,'dev','hash',.05)
            group.append(out)
            self.assertFalse(out['XY_pass'])
            self.assertTrue(out['center_XY_pass'])
            self.assertIsNone(out['X_relative_error'])
            self.assertEqual(out['magnitude_bucket'],'zero')
            self.assertEqual(out['X_bank_shift'],1.)
        details=d._group_summary({('T03',row['instance']):group})
        self.assertEqual(details['cases'],1)
        self.assertEqual(details['joint_rate'],0)
        self.assertEqual(details['center_joint_rate'],1.)
        self.assertEqual(details['failure_classes']['X_only_fail'],1)

    def test_all_masks_joint_not_mean_of_individual(self):
        matrix=(2,0,1,-1)
        group=[]
        for mask in (0,1):
            row={'prompt':bm.render_blind('T05',500000,matrix,mask),'tier':'T05',
                 'mask':mask,'instance':'dev/matrix-0/a-500000',
                 'matrix_index':0,'x':1000000.,'y':0.}
            predicted=(1000000.,0.) if mask==0 else (900000.,0.)
            group.append(d._single_row(row,predicted,predicted,.01,.02,'dev','hash',.05))
        score=d._group_summary({('T05',row['instance']):group})
        self.assertEqual(score['cases'],1)
        self.assertEqual(score['joint_hits'],0)
        self.assertEqual(score['X_hits'],0)

    def test_magnitude_and_root_buckets(self):
        self.assertEqual(d.magnitude_bucket(0),'zero')
        self.assertEqual(d.magnitude_bucket(5),'(1,10]')
        self.assertEqual(d.magnitude_bucket(90000),'(10000,100000]')
        self.assertEqual(d.root_proximity(500000,(1,-1,2,-2)),'X_zero:exact')
        self.assertEqual(d.count_bucket(0),'endpoint')
        self.assertEqual(d.count_bucket(500000),'interior')

    def test_cpu_end_to_end_matches_certification_and_does_not_mutate_checkpoint(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            trainargs=bm.parser().parse_args(['train','--device','cpu',
                '--output',str(root/'train'),'--banks','24','--grid-bins','32',
                '--hidden','8','--train-matrices','5','--dev-matrices','2',
                '--test-matrices','2','--counts-per-matrix','4','--masks','2',
                '--batch-size','16','--steps','2','--probe-every','1'])
            bm.train(trainargs)
            checkpoint=root/'train'/'best.pt'
            before=hashlib.sha256(checkpoint.read_bytes()).hexdigest()
            args=d.parser().parse_args(['--checkpoint',str(checkpoint),
                '--device','cpu','--output',str(root/'diagnosis')])
            result=d.diagnose(args)
            self.assertEqual(set(result['splits']),{'dev'})
            self.assertEqual(result['checkpoint_sha256'],before)
            self.assertEqual(hashlib.sha256(checkpoint.read_bytes()).hexdigest(),before)
            self.assertTrue((root/'diagnosis'/'diagnosis.json').is_file())
            with (root/'diagnosis'/'dev_cases.csv').open(newline='') as f:
                rows=list(csv.DictReader(f))
            self.assertEqual(len(rows),2*4*len(bm.TIERS)*2)
            self.assertEqual(set(rows[0]),set(d.CASE_FIELDS))
            for tier in bm.TIERS:
                self.assertEqual(
                    result['splits']['dev']['by_tier'][tier]['joint_rate'],
                    result['splits']['dev']['certification_gate']['tiers'][tier]['joint_all_masks_instance_rate'])
            with self.assertRaises(FileExistsError):d.diagnose(args)
            testargs=d.parser().parse_args(['--checkpoint',str(checkpoint),
                '--device','cpu','--splits','test','--output',str(root/'testdiagnosis')])
            with self.assertRaisesRegex(ValueError,'EXPLICIT_ACKNOWLEDGMENT'):
                d.diagnose(testargs)
            testargs.acknowledge_test_exposure=True
            result=d.diagnose(testargs)
            self.assertTrue(result['test_exposure_acknowledged'])
            self.assertEqual(set(result['splits']),{'test'})

if __name__=='__main__':unittest.main()
