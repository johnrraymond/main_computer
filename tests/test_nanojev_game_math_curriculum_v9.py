"""CPU-only contract tests for V9; no model download, no optimizer or CUDA."""
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from tools import nanojev_game_math_curriculum_v9 as v9


class ArithmeticAndGating(unittest.TestCase):
    def test_zero_absolute_error_and_both_operands(self):
        self.assertTrue(v9.within(.049,0.))
        self.assertFalse(v9.within(.051,0.))
        self.assertTrue(v9.within(-10.4,-10))
        self.assertFalse(v9.within(-10.6,-10))
        self.assertEqual(v9.metrics([{'status':'ok','x_ok':True,'y_ok':False,
                                     'joint_ok':False,'coupled_Z':{'within_5pct':True}}])['joint_xy'],0)

    def test_coupled_z_uses_predictions_not_truth(self):
        good=v9.coupled_z(10,4,'SUBTRACT',true_z=6)
        self.assertTrue(good['within_5pct'])
        bad=v9.coupled_z(11,4,'SUBTRACT',true_z=6)
        self.assertFalse(bad['within_5pct'])
        self.assertEqual(v9.number(bad['prediction']),7)
        self.assertFalse(v9.coupled_z(1,0,'DIVIDE',true_z=10)['within_5pct'])
        self.assertEqual(v9.number(v9.coupled_z(3,7,'ARGMAX')['prediction']),1)
        self.assertEqual(v9.number(v9.coupled_z(3,7,'MAX')['prediction']),7)

    def test_gate_blocks_missing_cases_and_unscorable(self):
        rec=[{'status':'ok','x_ok':True,'y_ok':True,'joint_ok':True,
              'coupled_Z':{'within_5pct':True}} for _ in range(16)]
        self.assertTrue(v9.gate(v9.metrics(rec))['passed'])
        rec[0]['status']='unscorable'
        self.assertFalse(v9.gate(v9.metrics(rec))['passed'])
        self.assertFalse(v9.gate(v9.metrics(rec[:15]))['passed'])
        scores={'T00':v9.metrics([{'status':'ok','x_ok':True,'y_ok':True,'joint_ok':True,
                                  'coupled_Z':{'within_5pct':True}} for _ in range(16)]),
                'T01':v9.metrics(rec)}
        self.assertEqual(v9.first_failing(scores),'T01')
        self.assertEqual(v9.first_failing({'T00':scores['T00']}),'T01')

    def test_teacher_mutation_replay_and_coupling(self):
        source=Path('/mnt/data/v8_full_verification/game_transformations.sqlite3')
        if not source.is_file():self.skipTest('Source fixture not mounted')
        with sqlite3.connect(source) as db:
            prompt=db.execute('SELECT prompt FROM native_v6_tiers WHERE tier=8 ORDER BY case_id LIMIT 1').fetchone()[0]
        a=v9.deterministic_ladder(prompt,17,11)
        b=v9.deterministic_ladder(prompt,17,29)
        self.assertEqual(a['answer'],b['answer'])
        self.assertEqual(len(a['prompts']),9)
        self.assertEqual(a['prompts']['T00'],b['prompts']['T00'])
        z=v9.coupled_z(v9.number(a['answer']['X']),v9.number(a['answer']['Y']),
                       a['answer']['OP'],true_z=a['answer']['Z'])
        self.assertTrue(z['within_5pct'])

    def test_native_record_rejects_wrong_y_and_couples_z(self):
        answer={'X':{'numerator':10,'denominator':1},'Y':{'numerator':2,'denominator':1},
                'OP':'SUBTRACT','Z':{'numerator':8,'denominator':1}}
        row={'id':'a','game':'g','objective':'scalar_value','answer':answer,
             'prompts':{'T00':'hello'}}
        correct=v9.native_records([row],'T00',lambda _: (10,2))[0]
        wrong=v9.native_records([row],'T00',lambda _: (10,5))[0]
        self.assertTrue(correct['coupled_Z']['within_5pct'])
        self.assertFalse(wrong['joint_ok'])
        self.assertFalse(wrong['coupled_Z']['within_5pct'])
        self.assertEqual(v9.metrics([wrong])['y'],0)

    def test_database_generates_only_from_native_v8(self):
        source=Path('/mnt/data/v8_full_verification/game_transformations.sqlite3')
        if not source.is_file():self.skipTest('No source fixture mounted')
        with TemporaryDirectory() as tmp:
            a=Path(tmp)/'a.sqlite3';b=Path(tmp)/'b.sqlite3'
            r=v9.make_teacher(source,a,seed=9,limit_per_split=1)
            v9.make_teacher(source,b,seed=9,limit_per_split=1)
            self.assertEqual(a.read_bytes(),b.read_bytes())
            self.assertEqual(r['prompts'],r['cases']*9)
            with sqlite3.connect(a) as cn:
                self.assertFalse(cn.execute('PRAGMA foreign_key_check').fetchall())
            for row in v9.read_cases(a,'train'):
                self.assertEqual(len(row['prompts']),9)
                self.assertEqual(row['answer']['OP'] in v9.MATH_OPERATIONS,True)
            with self.assertRaises(FileExistsError):v9.make_teacher(source,a,limit_per_split=1)


if __name__=='__main__':unittest.main()
