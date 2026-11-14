"""CPU-only V10 deterministic-lattice/SQLite contract tests."""
from __future__ import annotations

from pathlib import Path
from contextlib import closing
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from tools import nanojev_deterministic_brain_v10 as brain
from tools import nanojev_game_math_curriculum_v10_build as build
from tools import nanojev_game_native_objectives_v6 as native
from tools import nanojev_game_transformation_library as legacy


def scalar_prompt(x=9., y=12.) -> str:
    title = legacy.SCALAR_MODELS['robust_minimum'][0]
    f = native._facts('scalar', 'robust_minimum', title,
                      {'parameters': {'loss_case_1': x, 'loss_case_2': y}},
                      'scalar_value')
    return native.render08(f)


def matrix_prompt() -> str:
    name, _, rows, cols, U = legacy.MATRICES['prisoners_dilemma']
    f = native._facts('matrix', 'prisoners_dilemma', name,
                      {'row_actions': list(rows), 'column_actions': list(cols),
                       'U': U}, 'best_response', 0)
    return native.render08(f)


def source_fixture(path: Path, bad_split: bool = False) -> None:
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript('''CREATE TABLE native_v6_cases (
            case_id TEXT PRIMARY KEY,instance_id TEXT NOT NULL,split TEXT NOT NULL,
            game_id TEXT NOT NULL,objective TEXT NOT NULL,t08_sha256 TEXT NOT NULL,
            status TEXT NOT NULL);
            CREATE TABLE native_v6_tiers (
            case_id TEXT NOT NULL,tier INTEGER NOT NULL,prompt TEXT NOT NULL,
            PRIMARY KEY(case_id,tier));''')
        samples = [('case-a','source-a','train','robust_minimum','scalar_value',scalar_prompt()),
                   ('case-b','source-b','dev','robust_minimum','scalar_value',scalar_prompt(5, 13)),
                   ('case-c','source-c','test','prisoners_dilemma','best_response',matrix_prompt())]
        if bad_split:
            x = list(samples[1]);x[1] = 'source-a';samples[1] = tuple(x)
        for cid,iid,split,game,objective,prompt in samples:
            db.execute('INSERT INTO native_v6_cases VALUES (?,?,?,?,?,?,?)',
                       (cid,iid,split,game,objective,native.sha(prompt),'PASS'))
            db.execute('INSERT INTO native_v6_tiers VALUES (?,?,?)',(cid,8,prompt))


class V10Brain(unittest.TestCase):
    def setUp(self):
        self.frozen = brain.freeze(scalar_prompt(), mutation_seed=17, order_seed=11)

    def test_same_frozen_math_all_tiers_and_order_independent(self):
        result = brain.prove_all(self.frozen)
        self.assertEqual(result['presentation_count'],81+sum(map(len,brain.BRIDGE_NODES.values())))
        self.assertEqual(result['commuting_diamonds'],36)
        self.assertEqual(len(result['tiers']),9)
        self.assertEqual(self.frozen['snapshot_sha256'],result['snapshot_sha256'])
        self.assertEqual(self.frozen['answer_sha256'],result['answer_sha256'])
        second = brain.freeze(scalar_prompt(),mutation_seed=17,order_seed=29)
        self.assertEqual(second['answer'],self.frozen['answer'])
        self.assertEqual(second['snapshot'],self.frozen['snapshot'])
        self.assertEqual(second['native_prompts'],self.frozen['native_prompts'])
        self.assertEqual(result,brain.prove_all(self.frozen))

    def test_xy_paths_commute_at_every_math_tier(self):
        for tier in brain.TIERS:
            with self.subTest(tier=tier):
                proof=brain.prove_tier(self.frozen,tier)
                self.assertTrue(all(d['paths_equivalent'] for d in proof['diamonds']))
                self.assertEqual(len(set(proof['prompts'].values())),9)
                for start, x, y, end, xop, yop in brain.DIAMONDS:
                    initial=frozenset(brain.NODES[start])
                    state_x,_=brain.apply(self.frozen,tier,initial,xop)
                    xy,p_xy=brain.apply(self.frozen,tier,state_x,yop)
                    state_y,_=brain.apply(self.frozen,tier,initial,yop)
                    yx,p_yx=brain.apply(self.frozen,tier,state_y,xop)
                    self.assertEqual(xy,yx)
                    self.assertEqual(p_xy,p_yx)
                    self.assertEqual(p_xy,proof['prompts'][end])

    def test_register_removal_is_single_operand_and_raw_T08_is_story(self):
        p=brain.prove_tier(self.frozen,'T02')['prompts']
        self.assertIn(brain.SECOND+' = ',p['register_x'])
        self.assertNotIn(brain.FIRST+' = ',p['register_x'])
        self.assertIn(brain.FIRST+' = ',p['register_y'])
        self.assertNotIn(brain.SECOND+' = ',p['register_y'])
        self.assertIn('using '+brain.FIRST+' and ',p['register_y'])
        self.assertIn('using ',p['raw'])
        self.assertNotIn(brain.FIRST,p['raw'])
        self.assertNotIn(brain.SECOND,p['raw'])
        self.assertTrue(brain.render(self.frozen,'T08','raw').startswith(
            native.render08(self.frozen['snapshot'])+'\nMATHEMATICAL_RULE: '))
        self.assertNotIn('XXXXXX',brain.render(self.frozen,'T08','raw'))
        self.assertNotIn('YYYYYYY',brain.render(self.frozen,'T08','raw'))

    def test_exact_v9_anchors_and_atomic_grammar_bridge(self):
        for tier in brain.TIERS:
            with self.subTest(tier=tier):
                proof=brain.prove_bridge(self.frozen,tier)
                self.assertEqual(proof['prompts']['v9_anchor'],self.frozen['native_prompts'][tier])
                self.assertEqual(proof['merge_prompt_sha256'],brain.digest(
                    brain.render(self.frozen,tier,'full')))
                self.assertEqual(len(proof['prompts']),len(brain.BRIDGE_NODES[tier]))
        t00=brain.bridge_prompts(self.frozen,'T00')
        self.assertTrue(t00['v9_anchor'].startswith('XXXXXX: '))
        self.assertIn('\n\nOP: ',t00['v9_anchor'])
        self.assertEqual(t00['grammar_answer'],brain.render(self.frozen,'T00','no_logbars'))
        self.assertNotEqual(t00['grammar_equals_x'],t00['grammar_equals_y'])
        t02=brain.bridge_prompts(self.frozen,'T02')
        self.assertEqual(t02['grammar_answer'],brain.render(self.frozen,'T02','raw'))
        self.assertEqual(t02['bridge_registered'],brain.render(self.frozen,'T02','no_logbars'))

    def test_scalar_fractions_not_truncated_and_matrix_oracle(self):
        self.assertNotEqual(brain.logbar('0.9'),brain.logbar('0.0'))
        m=brain.freeze(matrix_prompt(),mutation_seed=4,order_seed=2)
        proof=brain.prove_all(m)
        self.assertEqual(proof['commuting_diamonds'],36)
        self.assertEqual(m['answer']['OP'],'ARGMAX')

    def test_tamper_detection(self):
        broken=dict(self.frozen)
        broken['snapshot']=dict(self.frozen['snapshot'])
        broken['snapshot']['game_name']='Changed'
        with self.assertRaises(ValueError):brain.prove_all(broken)
        with self.assertRaises(ValueError):brain.apply(self.frozen,'T02',frozenset(),'not_real')
        with self.assertRaises(ValueError):brain._render(self.frozen,'T02',frozenset({'drop_x_register'}))


class V10Database(unittest.TestCase):
    def test_wrong_legacy_source_preflight_is_actionable(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);src=root/'legacy.sqlite3';out=root/'teacher.sqlite3'
            with closing(sqlite3.connect(src)) as db, db:
                db.execute('CREATE TABLE transformation_cases (case_id TEXT PRIMARY KEY)')
            with self.assertRaisesRegex(ValueError,
                  'V10_NATIVE_SOURCE_SCHEMA_MISSING.*native_v6_cases'):
                build.make_teacher(src,out)
            self.assertFalse(out.exists())
            with self.assertRaisesRegex(ValueError,
                  'V10_NATIVE_SOURCE_SCHEMA_MISSING'):
                build.source_info(src)

    def test_native_source_preflight_reports_case_counts(self):
        with TemporaryDirectory() as temp:
            src=Path(temp)/'native.sqlite3'
            source_fixture(src)
            report=build.source_info(src)
            self.assertEqual(report['status'],'PASS')
            self.assertEqual(report['eligible_native_t08_cases'],3)
            self.assertEqual(report['splits'],{'train':1,'dev':1,'test':1})

    def test_build_rebuild_verify_and_immutable(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);source=root/'native.sqlite3';a=root/'a.sqlite3';b=root/'b.sqlite3'
            source_fixture(source)
            result=build.make_teacher(source,a,seed=99)
            self.assertEqual(result['cases'],3)
            self.assertEqual(result['presentations'],3*(9*9+sum(map(len,brain.BRIDGE_NODES.values()))))
            self.assertEqual(result['equivalence_proofs'],3*9*5)
            self.assertEqual(build.verify_teacher(a)['status'],'PASS')
            build.make_teacher(source,b,seed=99)
            self.assertEqual(a.read_bytes(),b.read_bytes())
            with self.assertRaises(FileExistsError):build.make_teacher(source,a,seed=99)
            with closing(sqlite3.connect(a)) as db, db:
                self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())
                self.assertEqual(db.execute('SELECT COUNT(*) FROM stage_edges').fetchone()[0],116+sum(len(brain.bridge_edges(t)) for t in brain.TIERS))
                self.assertEqual(db.execute('SELECT COUNT(DISTINCT answer_sha256) FROM presentations').fetchone()[0],3)

    def test_inspect_displays_true_equivalence_paths(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);src=root/'source.sqlite3';out=root/'teacher.sqlite3'
            source_fixture(src)
            build.make_teacher(src,out,seed=9,limit_per_split=1)
            t02=build.inspect_teacher(out,tier='T02')
            t08=build.inspect_teacher(out,tier='T08',case_id=t02['case_id'])
            self.assertEqual(len(t02['prompts']),9+len(brain.BRIDGE_NODES['T02']))
            self.assertEqual(len(t02['equivalent_transformation_paths']),5)
            self.assertEqual(t02['answer_sha256'],t08['answer_sha256'])
            self.assertEqual(t02['snapshot_sha256'],t08['snapshot_sha256'])
            self.assertEqual(len(next(x for x in t08['equivalent_transformation_paths']
                                      if x['family']=='whole_side_xy')['x_first']),4)

    def test_tamper_transformation_class_or_edge_detected(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);src=root/'source.sqlite3';out=root/'teacher.sqlite3'
            source_fixture(src)
            build.make_teacher(src,out,seed=8,limit_per_split=1)
            with closing(sqlite3.connect(out)) as db, db:
                db.execute("UPDATE mutation_classes SET invariant='BAD' WHERE class_id='drop_x_logbar'")
            with self.assertRaises(AssertionError):build.verify_teacher(out,oracle_recheck=False)

    def test_tamper_prompt_detected(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);src=root/'source.sqlite3';out=root/'teacher.sqlite3'
            source_fixture(src)
            build.make_teacher(src,out,seed=7,limit_per_split=1)
            with closing(sqlite3.connect(out)) as db, db:
                db.execute("UPDATE presentations SET prompt='BAD' WHERE stage_id='T08/raw'")
            with self.assertRaises(AssertionError):build.verify_teacher(out)

    def test_tamper_bridge_prompt_or_proof_detected(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);src=root/'source.sqlite3';out=root/'teacher.sqlite3'
            source_fixture(src)
            build.make_teacher(src,out,seed=23)
            with closing(sqlite3.connect(out)) as db, db:
                db.execute("UPDATE presentations SET prompt='SPOOFED' WHERE stage_id='T00/v9_anchor'")
            with self.assertRaises(AssertionError):build.verify_teacher(out,oracle_recheck=False)
        with TemporaryDirectory() as temp:
            root=Path(temp);src=root/'source.sqlite3';out=root/'teacher.sqlite3'
            source_fixture(src)
            build.make_teacher(src,out,seed=24)
            with closing(sqlite3.connect(out)) as db, db:
                db.execute("UPDATE bridge_proofs SET merged_prompt_sha256='BAD' WHERE family='bridge_register_xy'")
            with self.assertRaises(AssertionError):build.verify_teacher(out,oracle_recheck=False)

    def test_split_ancestry_leak_rejected_before_db(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);src=root/'source.sqlite3';out=root/'teacher.sqlite3'
            source_fixture(src,bad_split=True)
            with self.assertRaises(ValueError):build.make_teacher(src,out)
            self.assertFalse(out.exists())


if __name__=='__main__':
    unittest.main()
