"""Contract and end-to-end CPU checks for oracle-blind matrix-held-out certification."""
from __future__ import annotations
import tempfile
from pathlib import Path
import unittest
import torch
from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as b

class BlindMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):torch.set_num_threads(1)

    def test_all_tiers_oracle_blind_input(self):
        matrix=(3,-1,1,2)
        for tier in b.TIERS:
            for mask in (0,1):
                prompt=b.render_blind(tier,200001,matrix,mask)
                data=b.parse_blind(prompt,tier,mask)
                self.assertEqual((data['a'],data['b']),(200001,799999))
                self.assertEqual(data['payoffs'],matrix)
                self.assertEqual(len(b.features_from_prompt(prompt,tier,mask,4)),b.INPUT_DIM)
                if tier=='T01':self.assertIn('COMPUTE_X',prompt)
                if tier=='T02':self.assertIn('COMPUTE_Y',prompt)
                self.assertNotIn('1000000',prompt)
        with self.assertRaises(ValueError):b.render_blind('T00',200000,matrix)

    def test_prompt_fails_closed_against_leak_and_tamper(self):
        p=b.render_blind('T01',200000,(3,-1,1,2),0)
        for bad in (p.replace('COMPUTE_X','123456'),p.replace('800000','800001'),
                    p.replace('BIND HAWK_HAWK_PAYOFF = 3','BIND HAWK_HAWK_PAYOFF = 3\nBIND X = 7')):
            with self.assertRaises(ValueError):b.parse_blind(bad,'T01',0)

    def test_matrix_splits_and_zero_coverage(self):
        args=b.parser().parse_args(['train','--device','cpu','--banks','24',
            '--train-matrices','8','--dev-matrices','3','--test-matrices','3',
            '--counts-per-matrix','6','--masks','2'])
        ds,dom,manifest=b._dataset(args,torch.device('cpu'))
        sets=[set(manifest['matrix_hashes'][s]) for s in ('train','dev','test')]
        self.assertFalse(sets[0]&sets[1] or sets[0]&sets[2] or sets[1]&sets[2])
        self.assertEqual(len(ds['dev']['rows']),3*6*len(b.TIERS)*2)
        self.assertEqual(tuple(dom[0]),(-5000000.,5000000.))

    def test_tie_and_zero_targets_are_not_randomly_discarded(self):
        # X = 4a, Y = 2b => tie at a ~= 333333; require its closest counts.
        counts=b._count_cases((4,0,0,2),7,16)
        self.assertIn(333333,counts)
        self.assertIn(333334,counts)
        # Both X and Y have an exact zero at a=500000.
        zeros=b._count_cases((1,-1,2,-2),7,16)
        self.assertIn(500000,zeros)
        self.assertEqual(len(zeros),16)

    def test_rank_and_joint_case_gate_not_z(self):
        fake={'overall':{'mean_tv':.02},'by_tier':{t:{'instances_all_masks_xy_hit_rate':.79,
              'independent_instances':10,'n':20,'x_within_tolerance':1.,
              'y_within_tolerance':1.,'xy_within_tolerance':.8,
              'zero_x_count':0,'zero_y_count':0} for t in b.TIERS}}
        self.assertEqual(b._gate(fake,.8)['status'],'FAIL')
        for t in b.TIERS:fake['by_tier'][t]['instances_all_masks_xy_hit_rate']=.8
        self.assertEqual(b._gate(fake,.8)['status'],'PASS')

    def test_cpu_training_and_read_only_audit(self):
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'run'
            args=b.parser().parse_args(['train','--output',str(output),'--device','cpu',
                '--banks','32','--grid-bins','32','--hidden','8',
                '--train-matrices','6','--dev-matrices','2','--test-matrices','2',
                '--counts-per-matrix','4','--masks','1','--batch-size','16',
                '--steps','3','--probe-every','1'])
            result=b.train(args)
            self.assertIn(result['status'],('PASS','FAIL'))
            self.assertFalse(result['uses_test_for_training_or_promotion'])
            self.assertFalse(result['oracle_XY_in_input'])
            self.assertTrue((output/'best.pt').is_file())
            auditargs=b.parser().parse_args(['audit','--checkpoint',str(output/'best.pt'),
                                            '--device','cpu','--output',str(output/'audit.json')])
            report=b.audit(auditargs)
            self.assertEqual(set(report['splits']),{'dev','test'})
            self.assertEqual(report['status'],result['status'])
            prompt_file=Path(temp)/'prompt.txt'
            prompt_file.write_text(b.render_blind('T08',500000,(2,-3,1,4),1))
            predicted=b.predict(b.parser().parse_args(['predict','--device','cpu',
                '--checkpoint',str(output/'best.pt'),'--tier','T08','--mask','1',
                '--prompt-file',str(prompt_file)]))
            self.assertFalse(predicted['oracle_labels_read'])
            self.assertTrue(isinstance(predicted['predicted_X'],float))
            with self.assertRaises(FileExistsError):b.train(args)

if __name__=='__main__':unittest.main()
