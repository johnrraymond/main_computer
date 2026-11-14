"""Offline mathematical/integration contracts. Live Qwen checkpoint tested on user GPU."""
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest

from tools import nanojev_game_transform_experiment as ex
from tools import nanojev_game_transformation_library as mathlib


class TransformExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.db=Path(cls.tmp.name)/'verified_games.sqlite3'
        cls.report=ex.generate(cls.db,instances_per_game=5)

    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def test_catalogs_are_preserved_and_source_teachers_unverified(self):
        with sqlite3.connect(self.db) as cn:
            self.assertGreaterEqual(cn.execute('SELECT COUNT(*) FROM games').fetchone()[0],220)
            self.assertEqual(cn.execute('SELECT COUNT(*) FROM integrated_source_game_catalog').fetchone()[0],39)
            self.assertEqual(cn.execute('SELECT COUNT(*) FROM integrated_source_transform_catalog').fetchone()[0],19)
            self.assertTrue(all(row[0]=='catalog_only_unverified_teacher' for row in cn.execute(
                'SELECT DISTINCT training_status FROM integrated_source_game_catalog')))
            obj=json.loads(cn.execute('SELECT full_json FROM experiment_objectives').fetchone()[0])
            self.assertIn('executable_now',obj)
            self.assertIn('proposed_not_yet_executable',obj)

    def test_mathematics_and_split_are_checked(self):
        status=ex.inspect(self.db)
        self.assertTrue(status['positive_oracle_pass'])
        self.assertTrue(status['negative_oracle_rejected'])
        self.assertGreater(status['splits'].get('train',0),100)
        self.assertGreater(status['splits'].get('game_transfer',0),30)
        with sqlite3.connect(self.db) as cn:
            self.assertFalse(cn.execute('PRAGMA foreign_key_check').fetchall())
            seen=dict(cn.execute('SELECT game_id,group_concat(DISTINCT split) FROM experiment_cases GROUP BY game_id'))
            self.assertTrue(all(seen[g]=='game_transfer' for g in ex.RESERVED_GAMES))

    def test_predictions_are_not_copied_from_targets(self):
        with sqlite3.connect(self.db) as cn:
            for case_id,prompt,orient in cn.execute('SELECT case_id,prompt,orientation FROM experiment_prompts'):
                self.assertNotIn('"value_after"',prompt)
                self.assertNotIn('"U_prime"',prompt)
                self.assertIn('XXXXXX',prompt)
                self.assertIn('YYYYYY',prompt)
            self.assertEqual(cn.execute('SELECT COUNT(*) FROM experiment_prompts').fetchone()[0],
                             cn.execute('SELECT COUNT(*) FROM experiment_cases').fetchone()[0]*2)

    def test_paired_metric_exact_positive_and_negative(self):
        case={'id':'c','x':12.0,'y':20.0,'operator':'permute_row'}
        p={'c':{'forward':[12.0,20.0],'reordered':[12.0,20.0],
                'op_forward':'permute_row','op_reordered':'permute_row'}}
        perfect=ex.metric(p,[case]);self.assertEqual(perfect['accuracy']['paired5'],1.)
        self.assertEqual(perfect['operator_paired_accuracy'],1.)
        p['c']['reordered'][1]=22.0
        worse=ex.metric(p,[case]);self.assertEqual(worse['accuracy']['paired5'],0.)
        self.assertEqual(worse['accuracy']['paired10'],1.)

    def test_true_composition_is_noncommutative_and_oracle_checked(self):
        m=mathlib.MATRICES['hawk_dove'][4]
        first=mathlib.matrix_transform(mathlib.matrix_transform(m,'permute_row'),'change_one_payoff')
        swapped=mathlib.matrix_transform(mathlib.matrix_transform(m,'change_one_payoff'),'permute_row')
        self.assertEqual(first,mathlib.matrix_transform(m,'compose_transformations'))
        self.assertNotEqual(first,swapped)

    def test_sampler_full_bank_and_mix_is_fixed(self):
        pool=[{'id':str(i)} for i in range(128)]
        selected={ex.select_case(pool,s,0,0)['id'] for s in range(128)}
        self.assertEqual(len(selected),128)
        self.assertEqual(sum(ex.parse_mix(None).values()),8)
        self.assertEqual(ex.parse_mix('retention=1,positive_affine=3'),
                         {'positive_affine':3,'retention':1})
        with self.assertRaises(ValueError):ex.parse_mix('positive_affine=2')

    def test_torch_optimizer_changes_real_weights_in_cpu_smoke(self):
        # Tests a true supervised training gradient, without misrepresenting it
        # as a successful live V4 checkpoint experiment.
        try:import torch
        except ImportError:self.skipTest('torch unavailable')
        torch.manual_seed(7)
        head=torch.nn.Linear(2,2)
        x=torch.tensor([[1.,0.],[0.,1.]])
        y=torch.tensor([[2.,5.],[7.,3.]])
        optimizer=torch.optim.AdamW(head.parameters(),lr=.1)
        before=[p.detach().clone() for p in head.parameters()]
        for _ in range(8):
            loss=torch.nn.functional.mse_loss(head(x),y)
            optimizer.zero_grad();loss.backward();optimizer.step()
        self.assertTrue(any(not torch.equal(a,b.detach()) for a,b in zip(before,head.parameters())))

if __name__=='__main__':unittest.main()

class ArbitraryActionDimensions(unittest.TestCase):
    def test_three_by_three_action_relabeling(self):
        m=[[[10*r+c,10*c+r] for c in range(3)] for r in range(3)]
        row=mathlib.matrix_transform(m,'permute_row')
        col=mathlib.matrix_transform(m,'permute_column')
        self.assertEqual(row[0],m[2])
        self.assertEqual(col[0][0],m[0][2])
        self.assertEqual(mathlib.pure_nash(row),
                         mathlib.expected_eq_mapping(mathlib.pure_nash(m),'permute_row',(3,3)))
        self.assertEqual(mathlib.pure_nash(col),
                         mathlib.expected_eq_mapping(mathlib.pure_nash(m),'permute_column',(3,3)))
