"""Proof tests for v7 mutation class scheduling and cold source rebuild."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from tools import nanojev_game_mutation_brain_v7 as brain
from tools import nanojev_game_native_objectives_v6 as native
from tools import nanojev_game_math_smoke as smoke


def matrix_prompt():
    f = native._facts('matrix','captain_ship_defense','Captain Hull Preservation',
      {'U':[[[4,-13],[-16,-7]],[[-24,2],[0,-1]]],
       'row_actions':['Intercept','Evade'],'column_actions':['Raid','Withdraw']},
      'best_response',0)
    return native.render08(f)


def scalar_prompt():
    f = native._facts('scalar','markov_one_step',native.legacy.SCALAR_MODELS['markov_one_step'][0],
      {'parameters':{'r':1,'gamma':0.9,'p':0.6,'v_good':5,'v_bad':-2}},'scalar_value')
    return native.render08(f)


class MutationBrainPureTests(unittest.TestCase):
    def test_order_changes_trace_but_not_math(self):
        p = matrix_prompt()
        a = brain.run(p,order_seed=11,mutation_seed=17)
        b = brain.run(p,order_seed=29,mutation_seed=17)
        self.assertEqual(a['final'],b['final'])
        self.assertEqual(a['final_projection'],b['final_projection'])
        self.assertEqual([x['checkpoint_hash'] for x in a['levels']],
                         [x['checkpoint_hash'] for x in b['levels']])
        self.assertNotEqual([x['order'] for x in a['levels']],
                            [x['order'] for x in b['levels']])
        self.assertEqual(a['checkpoint_count'],9)
        self.assertTrue(a['composition_proven'])

    def test_parameter_seed_changes_numerical_projection(self):
        a = brain.run(matrix_prompt(),order_seed=11,mutation_seed=17)
        b = brain.run(matrix_prompt(),order_seed=11,mutation_seed=18)
        self.assertNotEqual(a['final_projection'],b['final_projection'])
        # An action label need not change when the payoffs change.
        self.assertEqual(a['final']['Z'],b['final']['Z'])

    def test_scalar_auxiliary_classes_and_coupling(self):
        p=scalar_prompt()
        a=brain.run(p,order_seed=11,mutation_seed=17)
        b=brain.run(p,order_seed=29,mutation_seed=17)
        self.assertEqual(a['final'],b['final'])
        for stage in a['levels']:
            self.assertEqual(stage['order'][-1],'Z_DOMAIN')
            self.assertEqual(len(stage['changes']),4)
            self.assertTrue({'X','Y','Z1','Z2'} <= {x['name'] for x in stage['changes']})
            self.assertEqual(stage['steps'][-1]['status'],'PASS')
        self.assertEqual(a['single_source_mutations'],36)

    def test_coupled_XY_footprint_is_measured(self):
        # In bargaining, d1 is used in BOTH T00 operands. A mutation to it is XY.
        title=native.legacy.SCALAR_MODELS['nash_bargain_split'][0]
        prompt=native.render08(native._facts('scalar','nash_bargain_split',title,
            {'parameters':{'d1':2,'d2':3,'V':18}},'scalar_value'))
        result=brain.run(prompt,order_seed=3,mutation_seed=8)
        classes={step['operand_footprint'] for level in result['levels'] for step in level['steps']
                 if 'operand_footprint' in step}
        self.assertIn('XY',classes)

    def test_mixed_equilibrium_coupled_source_paths(self):
        matrix={'U':[[[4,-13],[-16,-7]],[[-24,2],[0,-1]]],
                'row_actions':['Intercept','Evade'],'column_actions':['Raid','Withdraw']}
        for mode in ('mixed_row','mixed_col'):
            f=native._facts('matrix','captain_ship_defense','Captain Hull Preservation',matrix,mode)
            contract=brain.assert_seed_contract(native.render08(f))
            self.assertTrue(contract['same_final_for_order_variations'])
            self.assertTrue(contract['different_projection_for_parameter_variations'])

    def test_dependency_barrier_and_stale_state_rejected(self):
        p=matrix_prompt(); f=native.parse08(p)
        plan=brain._plan(f,8,17)
        bad_order=('Z_DOMAIN',)+tuple(sorted(plan))
        with self.assertRaisesRegex(ValueError,'CHECKPOINT_PROMOTED'):
            brain._execute_order(f,8,plan,bad_order)
        item=next(iter(plan.values()))
        invalid=dict(item);invalid['before']=item['before']+1
        with self.assertRaisesRegex(ValueError,'STALE_MUTATION'):
            brain._apply(f,invalid,8)

    def test_reproduction_and_tamper_check(self):
        p=matrix_prompt()
        x=brain.run(p,order_seed=101,mutation_seed=7)
        y=brain.run(p,order_seed=101,mutation_seed=7)
        self.assertEqual(x,y)
        with self.assertRaises(ValueError):
            brain.run(p.replace('Intercept','Hacked',1),order_seed=101,mutation_seed=7)


class MutationBrainGenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=TemporaryDirectory(prefix='nanojev-v7-test-')
        cls.root=Path(cls.tmp.name)
        cls.a=cls.root/'a';cls.b=cls.root/'b'
        cls.first=smoke.generate(cls.a,20261016,4)
        cls.second=smoke.generate(cls.b,20261016,4)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_full_rebuild_is_byte_identical(self):
        self.assertEqual(self.first['status'],'PASS')
        self.assertEqual(self.second['status'],'PASS')
        for fname in smoke.FILES:
            self.assertEqual(smoke.file_sha(self.a/fname),smoke.file_sha(self.b/fname),fname)
        metadata=json.loads((self.a/'manifest.json').read_text())
        self.assertIn('nanojev_game_mutation_brain_v7.py',metadata['source_sha256'])
        self.assertEqual(metadata['version'],smoke.VERSION)

    def test_mutation_source_schema_and_provenance(self):
        report=json.loads((self.a/'mutation_brain_checkpoint_proof.json').read_text())
        self.assertEqual(report['games_proven'],33)
        self.assertEqual(report['realizations'],66)
        self.assertEqual(report['checkpoints'],594)
        self.assertGreater(report['auxiliary_source_mutations'],0)
        with sqlite3.connect(self.a/'mutation_brain_v7.sqlite3') as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mutation_v7_runs').fetchone()[0],66)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM mutation_v7_checkpoints').fetchone()[0],594)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
        with sqlite3.connect(self.a/'game_transformations.sqlite3') as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM native_v6_game_coverage WHERE status="catalog_only_no_oracle"').fetchone()[0],196)


if __name__=='__main__':unittest.main()
