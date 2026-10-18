"""V10.2: source-grounded X/Y derivation audit, all native game families.

No GPU, weights, internet, or production database needed. The separate
builder verify command audits every row of the user's generated teacher.
"""
from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools import nanojev_deterministic_brain_v10 as brain
from tools import nanojev_game_math_curriculum_v10_build as build
from tools import nanojev_game_math_curriculum_v10_smoke as smoke
from tools import nanojev_game_native_objectives_v6 as native


class OperandAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory()
        root = Path(cls.temp.name)
        cls.source = root/'native.sqlite3'
        cls.teacher = root/'teacher.sqlite3'
        cls.coverage = smoke.emit_fixture(cls.source)
        cls.built = build.make_teacher(cls.source, cls.teacher, seed=20261017)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_all_native_math_objectives_and_tiers(self):
        self.assertEqual(self.coverage, {'source_cases':71,'game_families':33})
        self.assertEqual(self.built['presentations'], 71*141)
        self.assertEqual(self.built['operand_derivation_proofs'], 71*6*2)
        self.assertEqual(build.verify_teacher(self.teacher)['status'], 'PASS')

    def test_all_cases_hide_correct_operand_without_target_leakage(self):
        with sqlite3.connect(self.teacher) as db:
            for case, snapshot_json in db.execute('SELECT case_id,snapshot_json FROM cases'):
                snapshot = __import__('json').loads(snapshot_json)
                formulas = brain.operand_formulas(snapshot)
                for tier in brain.TIERS[3:]:
                    stages = dict(db.execute('''SELECT s.assistance_node,p.prompt
                        FROM presentations p JOIN stages s USING(stage_id)
                        WHERE p.case_id=? AND s.math_tier=?''',(case,tier)))
                    for side,stage in [('X','x_raw_y_full'),('Y','y_raw_x_full')]:
                        with self.subTest(case=case,tier=tier,side=side):
                            prompt = stages[stage]
                            self.assertIn(formulas[side],prompt)
                            self.assertNotIn((brain.FIRST if side=='X' else brain.SECOND)+' = ',prompt)
                            if snapshot['kind']=='matrix':
                                self.assertIn('U[', prompt)
                                self.assertNotIn('INDIFFERENCE_NUMERATOR',
                                                 prompt if side=='X' else '')
                            if tier=='T03' and side=='Y':
                                instruction=prompt.split('GAME_FACTS:')[0]
                                self.assertIn(' AND '+brain.canon(formulas['Y']),instruction)
                                self.assertNotIn(' AND '+str(float(native._one_operand(snapshot,'Y')[0])),instruction)
                    raw=stages['raw']
                    self.assertIn(formulas['X'],raw)
                    self.assertIn(formulas['Y'],raw)
                    self.assertNotIn(brain.FIRST,raw)
                    self.assertNotIn(brain.SECOND,raw)
                    if tier=='T08':
                        self.assertTrue(raw.startswith(native.render08(snapshot)))
                        self.assertNotIn('GAME_FACTS:',raw)

    def test_interpreter_rejects_code_execution(self):
        with sqlite3.connect(self.teacher) as db:
            snapshot = __import__('json').loads(db.execute('SELECT snapshot_json FROM cases LIMIT 1').fetchone()[0])
        for bad in ("__import__('os').system('exit 1')", 'U.__class__', 'lambda: 1',
                    '[x for x in (1,2)]'):
            with self.subTest(bad=bad), self.assertRaises((ValueError, SyntaxError)):
                brain.evaluate_operand_formula(bad,snapshot)

    def test_proof_or_prompt_tampering_fails_verification(self):
        with sqlite3.connect(self.teacher) as db:
            case = db.execute('SELECT case_id FROM cases LIMIT 1').fetchone()[0]
            db.execute("UPDATE operand_derivation_proofs SET expression='1' WHERE case_id=? AND math_tier='T03' AND side='X'",(case,))
        with self.assertRaisesRegex(AssertionError,'OPERAND_DERIVATION_PROOF_DRIFT'):
            build.verify_teacher(self.teacher,oracle_recheck=False)


if __name__ == '__main__':
    unittest.main()


class WarmStartContract(unittest.TestCase):
    def test_isolated_migration_preserves_weights_and_old_checkpoint(self):
        import json
        import torch
        from unittest.mock import patch
        from tools import nanojev_game_math_curriculum_v10_train as train
        from tools import nanojev_game_math_curriculum_v10_runtime as rt
        with TemporaryDirectory() as temp:
            root=Path(temp)
            source=root/'source.pt';source.write_bytes(b'source checkpoint fake bytes')
            parent=root/'parent.pt';parent.write_bytes(b'v9 parent fake bytes')
            db=root/'source.sqlite3';teacher=root/'audited.sqlite3'
            smoke.emit_fixture(db)
            build.make_teacher(db,teacher,seed=77,limit_per_split=1)
            old_dir=root/'old';old_dir.mkdir()
            warm=old_dir/'latest.pt'
            torch.manual_seed(11)
            decoder=torch.nn.Linear(1,2)
            gamehead=torch.nn.Linear(1,1)
            known_weights={k:v.clone() for k,v in decoder.state_dict().items()}
            torch.save({'schema':rt.SCHEMA,'step':250250,
                        'config':{'source_sha256':rt.source_hash(source),
                                  'parent_sha256':rt.source_hash(parent),
                                  'teacher_sha256':'intentionally-different-old-teacher'},
                        'decoder':decoder.state_dict(),
                        'game_operator_head':gamehead.state_dict()},warm)
            original=rt.source_hash(warm)
            args=train.make_parser().parse_args([
                '--teacher-db',str(teacher),'--source-checkpoint',str(source),
                '--parent-checkpoint',str(parent),'--warm-start-checkpoint',str(warm),
                '--output',str(root/'new'), '--steps','1','--chunk-steps','1',
                '--probe-limit','1','--min-count','1','--device','cpu',
                '--dtype','float32','--advancement-percent','50'])
            def fake_load(*a,**kwargs):
                torch.manual_seed(44)
                return ({'decoder':torch.nn.Linear(1,2),
                         'gamehead':torch.nn.Linear(1,1)},
                        {'step':245500},rt.source_hash(parent))
            def fake_loss(model,prompt,x,y):
                prediction=model['decoder'](torch.ones(1,1))
                return ((prediction-torch.tensor([[x,y]]))**2).mean()
            state_before=[]
            def fake_predict(model,prompts):
                if not state_before:
                    state_before.append({k:v.clone() for k,v in model['decoder'].state_dict().items()})
                return [(0.,0.) for _ in prompts]
            with patch.object(rt,'load_model',side_effect=fake_load), \
                 patch.object(rt,'batch_predict',side_effect=fake_predict), \
                 patch.object(rt,'token_eligible',return_value=True), \
                 patch.object(train,'objective',side_effect=fake_loss):
                result=train.run(args)
            self.assertEqual(result['step'],250251)
            self.assertEqual(result['v10_steps'],1)
            self.assertTrue(torch.equal(state_before[0]['weight'],known_weights['weight']))
            self.assertTrue(torch.equal(state_before[0]['bias'],known_weights['bias']))
            self.assertEqual(rt.source_hash(warm),original)
            with open(root/'new'/'v10-config.json') as f:config=json.load(f)
            self.assertEqual(config['warm_start_sha256'],original)
            self.assertEqual(config['warm_start_step'],250250)
            self.assertEqual(config['gate_fraction'],0.50)
