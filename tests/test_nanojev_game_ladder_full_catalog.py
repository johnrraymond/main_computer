"""V5: full existing source-oracle horizontal ladder expansion tests."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tools import nanojev_game_ladder_full_catalog as full
from tools import nanojev_game_transformation_library as legacy


class FullLibraryTests(unittest.TestCase):
    def _fresh(self,root:Path):
        db=root/'base.sqlite3'
        legacy.build(db,20261016,4)
        conn=sqlite3.connect(db)
        conn.execute('PRAGMA foreign_keys=ON')
        return conn

    def test_all_legacy_games_and_oracles_get_nine_tiers(self):
        with TemporaryDirectory() as tmp:
            with self._fresh(Path(tmp)) as db:
                report=full.build_and_verify(db)
                self.assertEqual(report['status'],'PASS')
                self.assertEqual(report['source_cases'],524)
                self.assertEqual(report['tiers'],9*524)
                self.assertEqual(report['stateful_edges'],8*524)
                self.assertEqual(report['games_with_full_bridges'],33)
                self.assertEqual(report['catalog_only_blocked'],196)
                for gid,count in db.execute('''SELECT game_id,COUNT(*) FROM full_ladder_v5_cases
                  GROUP BY game_id'''):
                    self.assertGreater(count,0,gid)
                self.assertEqual(db.execute('SELECT COUNT(DISTINCT program_id) FROM full_ladder_v5_edges').fetchone()[0],8)

    def test_numerical_work_happens_only_at_resolved_operand_edges(self):
        f={'kind':'matrix','game_id':'hawk_dove','game_name':'Hawk–Dove',
           'transform':'change_one_payoff', 'row_actions':['Hawk','Dove'],
           'column_actions':['Hawk','Dove'], 'U':legacy.MATRICES['hawk_dove'][4]}
        state=full.begin(full.t08(f))
        with patch.object(legacy,'matrix_transform',side_effect=AssertionError('premature execution')):
            for tier in range(8,4,-1):
                self.assertEqual(state['tier'],tier)
                state,_=full.step(state)
        self.assertEqual(state['tier'],4)
        self.assertIsNone(state['X']);self.assertIsNone(state['Y'])
        state,edge=full.step(state)
        self.assertEqual(edge['steps'][0]['opcode'],'APPLY_MATRIX_TRANSFORM')
        self.assertIsNone(state['X']);self.assertIsNotNone(state['Y'])
        state,_=full.step(state)
        self.assertEqual(state['X'],-2)
        while state['tier']:state,_=full.step(state)
        self.assertEqual(full.finish(state)['Z'],{'numerator':-2,'denominator':1})

    def test_source_data_altered_and_catalog_only_operation_are_rejected(self):
        f={'kind':'matrix','game_id':'hawk_dove','game_name':'Hawk–Dove',
           'transform':'swap_players', 'row_actions':['Hawk','Dove'],
           'column_actions':['Hawk','Dove'], 'U':legacy.MATRICES['hawk_dove'][4]}
        prompt=full.t08(f)
        with self.assertRaises(ValueError):full.begin(prompt.replace('swap_players','reveal_type'))
        with self.assertRaises(ValueError):full.begin(prompt.replace('Hawk–Dove','Chicken'))
        state=full.begin(prompt)
        for _ in range(4):state,_=full.step(state)
        state['prompt']=state['prompt'].replace('Subtract','Add')
        with self.assertRaisesRegex(ValueError,'PROMPT_STATE_NOT_CONSISTENT'):full.step(state)

    def test_persisted_tier_and_oracle_tampering_fail(self):
        with TemporaryDirectory() as tmp:
            with self._fresh(Path(tmp)) as db:
                full.build_and_verify(db)
                cid=db.execute('SELECT case_id FROM full_ladder_v5_cases ORDER BY case_id LIMIT 1').fetchone()[0]
                p=db.execute('SELECT prompt FROM full_ladder_v5_tiers WHERE case_id=? AND tier=8',(cid,)).fetchone()[0]
                db.execute('UPDATE full_ladder_v5_tiers SET prompt=? WHERE case_id=? AND tier=8',(p+' FALSE',cid))
                with self.assertRaisesRegex(AssertionError,'FULL_T08_TAMPER'):full.verify(db)
                db.execute('UPDATE full_ladder_v5_tiers SET prompt=? WHERE case_id=? AND tier=8',(p,cid))
                gold=db.execute('SELECT full_oracle_json FROM full_ladder_v5_cases WHERE case_id=?',(cid,)).fetchone()[0]
                db.execute('UPDATE full_ladder_v5_cases SET full_oracle_json=? WHERE case_id=?',('{}',cid))
                with self.assertRaisesRegex(AssertionError,'FULL_GOLD_MISMATCH'):full.verify(db)
                db.execute('UPDATE full_ladder_v5_cases SET full_oracle_json=? WHERE case_id=?',(gold,cid))
                self.assertEqual(full.verify(db)['status'],'PASS')

    def test_scalar_math_executed_from_public_parameters(self):
        gid='bayes_posterior'
        name=legacy.SCALAR_MODELS[gid][0]
        f={'kind':'scalar','game_id':gid,'game_name':name,'transform':'scalar_parameter_shift',
           'parameters':{'prior':.35,'l_true':.8,'l_false':.2},'changed':'prior',
           'increment':.05,'formula':legacy.SCALAR_MODELS[gid][2]}
        proof=full.walk(full.t08(f))
        before=legacy.scalar_oracle(gid,f['parameters'])
        new=dict(f['parameters']);new['prior']=.4
        after=legacy.scalar_oracle(gid,new)
        z=proof['end']['Z'];self.assertAlmostEqual(z['numerator']/z['denominator'],before-after)
        self.assertEqual(len(proof['tiers']),9)
        self.assertEqual(proof['edges'][4]['steps'][0]['opcode'],'APPLY_SCALAR_PARAMETER_SHIFT')


if __name__=='__main__':unittest.main()
