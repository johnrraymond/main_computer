"""Fail-closed tests for source-generated game-math smoke."""
from __future__ import annotations
from fractions import Fraction
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from tools import nanojev_game_math_registry as m
from tools import nanojev_game_math_smoke as smoke


class MathDefinitionTests(unittest.TestCase):
    def test_every_executable_definition_has_independent_gold(self):
        report=m.verify_definitions()
        self.assertEqual(report['executable'],45)
        self.assertGreaterEqual(report['vectors_checked'],45)
        self.assertEqual(len(m.OPERATORS),61)

    def test_fraction_preservation(self):
        self.assertEqual(m.execute('MIDPOINT',100,'62.5'),Fraction(325,4))
        self.assertEqual(m.execute('DIVIDE',1,4),Fraction(1,4))
        self.assertEqual(m.out(m.execute('MIDPOINT',100,'62.5')),
                         {'numerator':325,'denominator':4})

    def test_ambiguity_disambiguated(self):
        self.assertEqual(m.execute('LOW_PAYOFF',125),Fraction(125,2))
        self.assertEqual(m.execute('LOW_INTELLIGENCE',125),
                         (Fraction(125,2),Fraction(375,2)))
        self.assertNotEqual(m.OPERATORS['LOW_PAYOFF']['output'],
                            m.OPERATORS['LOW_INTELLIGENCE']['output'])

    def test_auction_optimum_exact_and_sensitive_to_intel(self):
        lower,upper=m.execute('LOW_INTELLIGENCE',125)
        own=m.execute('OWN_MEDIUM_NEED',100)
        self.assertEqual(m.execute('OPTIMAL_BID_UNIFORM',own,lower,upper),Fraction(325,4))
        self.assertEqual(m.execute('FIRST_PRICE_EXPECTED_SURPLUS',100,'81.25',lower,upper),Fraction(45,16))
        for bid in ('70','75','80','85','90','95','100'):
            self.assertLessEqual(m.execute('FIRST_PRICE_EXPECTED_SURPLUS',100,bid,lower,upper),Fraction(45,16))
        new_lower,new_upper=m.execute('LOW_INTELLIGENCE',100)
        self.assertEqual(m.execute('OPTIMAL_BID_UNIFORM',own,new_lower,new_upper),75)

    def test_catalog_only_rejected(self):
        with self.assertRaises(NotImplementedError):m.execute('GENERAL_AUCTION_OPTIMAL_BID',[],[],[])

    def test_dependency_cycle_rejected(self):
        original=m.OPERATORS['ADD']['dependencies']
        try:
            m.OPERATORS['ADD']['dependencies']=['MIDPOINT']
            with self.assertRaisesRegex(AssertionError,'DEPENDENCY_CYCLE'):m.verify_definitions()
        finally:m.OPERATORS['ADD']['dependencies']=original

    def test_missing_definition_rejected(self):
        original=m.OPERATORS['ADD']['formula']
        try:
            m.OPERATORS['ADD']['formula']=''
            with self.assertRaisesRegex(AssertionError,'INCOMPLETE_DEFINITION'):m.verify_definitions()
        finally:m.OPERATORS['ADD']['formula']=original

    def test_invalid_domain_rejected(self):
        for op,values in [('DIVIDE',(1,0)),('CDF_UNIFORM',(2,3,3)),
                          ('OPTIMAL_BID_UNIFORM',(100,10,10))]:
            with self.subTest(op=op):
                with self.assertRaises((ZeroDivisionError,ValueError)):m.execute(op,*values)


class GameMathSmokeTests(unittest.TestCase):
    def test_three_frozen_games_nine_tiers(self):
        graphs=smoke.canonical_graphs()
        self.assertEqual(len(graphs),3)
        for graph in graphs:
            with self.subTest(game=graph['id']):
                self.assertEqual(len(graph['prompts']),9)
                self.assertEqual(m.execute(graph['op'],graph['x'],graph['y']),graph['z'])
                self.assertNotIn('1.5',graph['prompts'][8])
        self.assertEqual(next(g['z'] for g in graphs if g['op']=='MIDPOINT'),Fraction(325,4))

    def test_full_source_build_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            report=smoke.run(Path(td)/'verified',20261016,4,False)
            self.assertEqual(report['status'],'PASS')
            root=Path(td)/'verified'
            self.assertEqual({p.name for p in root.iterdir()},set(smoke.FILES))
            with sqlite3.connect(root/'game_transformations.sqlite3') as db:
                self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
                self.assertEqual(db.execute('SELECT COUNT(*) FROM math_operators').fetchone()[0],len(smoke._source_ops()))
                self.assertEqual(db.execute('SELECT COUNT(*) FROM games').fetchone()[0],231)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM curriculum_presentations').fetchone()[0],27)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM math_execution_graphs WHERE output_semantics='bid_credits'").fetchone()[0],1)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM math_head_capabilities WHERE head_status='measured'").fetchone()[0],0)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM math_growth_requirements WHERE status='catalog_only'").fetchone()[0],196)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM math_operator_tests WHERE result='PASS'").fetchone()[0],72)
                a=db.execute("SELECT prompt,target_json FROM curriculum_presentations WHERE graph_id='first_price_captain_uncertainty' AND tier=8").fetchone()
                self.assertEqual(json.loads(a[1])['Z'],{'numerator':325,'denominator':4})
                self.assertIn('125',a[0]);self.assertIn('100',a[0])
                self.assertNotIn('62.5',a[0]);self.assertNotIn('81.25',a[0])
            with sqlite3.connect(root/'math_ladder_task_bank.sqlite3') as bank:
                self.assertEqual(bank.execute('SELECT COUNT(*) FROM task_cases').fetchone()[0],27)
            with sqlite3.connect(root/'nanojev_game_tasks.sqlite3') as bank:
                self.assertIn('task_groups', {r[0] for r in bank.execute("SELECT name FROM sqlite_master WHERE type='table'")})
            manifest=json.loads((root/'manifest.json').read_text())
            for name,checksum in manifest['generated_sha256'].items():
                self.assertEqual(smoke.file_sha(root/name),checksum)
            with self.assertRaises(FileExistsError):smoke.run(root,20261016,4,False)
            # The safe replacement gate rejects manual edits to generated outputs.
            (root/'math_definitions.json').write_text('{}\n')
            with self.assertRaisesRegex(RuntimeError,'MODIFIED_OUTPUT'):smoke.run(root,20261016,4,True)

    def test_foreign_folder_never_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            target=Path(td)/'foreign';target.mkdir();(target/'file.txt').write_text('keep')
            with self.assertRaisesRegex(RuntimeError,'FOREIGN_LAYOUT'):
                smoke.run(target,20261016,4,True)
            self.assertEqual((target/'file.txt').read_text(),'keep')


if __name__=='__main__':unittest.main()
