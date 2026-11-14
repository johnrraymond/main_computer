import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import closing

from tools import nanojev_game_transformation_library as g


class GameTransformationMathTests(unittest.TestCase):
    def test_hawk_dove_equilibrium(self):
        self.assertEqual(g.pure_nash(g.MATRICES['hawk_dove'][4]), [[0,1],[1,0]])
        self.assertAlmostEqual(g.scalar_oracle('hawk_dove_mixed', {'V':6.,'C':10.}),.6)

    def test_chicken_dove_is_ordinal_relation_not_cardinal_identity(self):
        self.assertEqual(g.pure_nash(g.MATRICES['chicken'][4]), [[0,1],[1,0]])
        self.assertNotEqual(g.MATRICES['hawk_dove'][4],g.MATRICES['chicken'][4])

    def test_prisoner_defection(self):
        self.assertEqual(g.pure_nash(g.MATRICES['prisoners_dilemma'][4]), [[1,1]])

    def test_matching_pennies_has_no_pure_equilibrium(self):
        self.assertEqual(g.pure_nash(g.MATRICES['matching_pennies'][4]), [])

    def test_renamings_preserve_mapped_equilibrium(self):
        for gname in g.MATRICES:
            a=g.MATRICES[gname][4]
            before=g.pure_nash(a)
            for op in ('swap_players','permute_row','permute_column'):
                b=g.matrix_transform(a,op)
                self.assertEqual(g.pure_nash(b),g.expected_eq_mapping(before,op))

    def test_affine_positive_preserves_pure_equilibrium(self):
        for name in g.MATRICES:
            original=g.MATRICES[name][4]
            for op in ('positive_affine','shared_payoff_shift'):
                self.assertEqual(g.pure_nash(original),g.pure_nash(g.matrix_transform(original,op)))

    def test_composition_identity(self):
        for spec in g.MATRICES.values():
            self.assertEqual(spec[4],g.matrix_transform(spec[4],'compose_transformations'))

    def test_mutation_changes_game_not_claimed_equivalent(self):
        game=g.MATRICES['prisoners_dilemma'][4]
        self.assertNotEqual(game,g.matrix_transform(game,'change_one_payoff'))
        self.assertNotEqual(game,g.matrix_transform(game,'reverse_payoff_sign'))

    def test_all_scalar_oracles_finite(self):
        for name,(_,_,_,p,change,inc) in g.SCALAR_MODELS.items():
            before=g.scalar_oracle(name,p)
            after=dict(p);after[change]+=inc
            result=g.scalar_oracle(name,after)
            import math
            self.assertTrue(math.isfinite(before) and math.isfinite(result),name)

    def test_negative_and_zero_payoffs_still_valid(self):
        payoffs=[[[-1,0],[0,1]],[[0,0],[-2,4]]]
        self.assertEqual(len(g.matrix_transform(payoffs,'swap_players')),2)

    def test_catalog_breadth_and_dictionary(self):
        self.assertGreaterEqual(len(g.FAMILIES),20)
        self.assertGreaterEqual(len(g.TERMS),70)
        self.assertIn('option_manipulation',[x[0] for x in g.TRANSFORMS])
        self.assertIn('jacket',[x[0] for x in g.TERMS])
        self.assertIn('hawk_dove',g.MATRICES)


class DatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.base=Path(cls.tmp.name)
        cls.db=cls.base/'games.sqlite3'
        cls.report=g.build(cls.db,seed=20261015,cases_per_game=6)

    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def test_full_sql_records(self):
        self.assertGreaterEqual(self.report['counts']['games'],190)
        self.assertGreaterEqual(self.report['counts']['transformation_cases'],500)
        self.assertEqual(self.report['counts']['presentations'],self.report['counts']['transformation_cases']*3)
        self.assertEqual(self.report['counts']['training_objectives'],1)
        self.assertEqual(self.report['counts']['training_mix'],11)

    def test_corpus_self_verification(self):
        with closing(sqlite3.connect(self.db)) as cn:
            self.assertEqual('ok',g.verify(cn)['integrity'])

    def test_objective_is_in_sql_and_not_milestone_schedule(self):
        with closing(sqlite3.connect(self.db)) as cn:
            objective=json.loads(cn.execute('SELECT full_objective_json FROM training_objectives').fetchone()[0])
            self.assertIn('paired_consistency',objective['losses'])
            self.assertIn('counterfactual_contrast',objective['losses'])
            self.assertEqual(objective['mix_policy']['batch_size'],16)
            self.assertIn('never transition by training step',objective['mix_policy']['composition'])

    def test_oracles_only_attached_to_executable(self):
        with closing(sqlite3.connect(self.db)) as cn:
            unresolved=cn.execute('''SELECT COUNT(*) FROM transformation_cases c
                JOIN transformations t ON t.op_id=c.op_id WHERE t.status<>'executable' ''').fetchone()[0]
            self.assertEqual(unresolved,0)
            catalog=cn.execute("SELECT COUNT(*) FROM games WHERE status='catalog_only'").fetchone()[0]
            self.assertGreater(catalog,100)

    def test_all_variants_grouped_same_split(self):
        with closing(sqlite3.connect(self.db)) as cn:
            mis=cn.execute('''SELECT COUNT(*) FROM transformation_cases c JOIN instances i ON i.instance_id=c.instance_id
                WHERE c.split<>i.split''').fetchone()[0]
            self.assertEqual(mis,0)
            distinct=cn.execute('SELECT COUNT(DISTINCT split) FROM transformation_cases').fetchone()[0]
            self.assertEqual(distinct,3)

    def test_sql_dictionary_includes_semantic_roles(self):
        with closing(sqlite3.connect(self.db)) as cn:
            terms={row[0] for row in cn.execute('SELECT term_id FROM dictionary')}
            for term in ('operand_x','operand_y','payoff_affine','jacket','byzantine_fault','game_isomorphism'):
                self.assertIn(term,terms)

    def test_export_jsonl_matches_sql_rows(self):
        counts=g.export_jsonl(self.db,self.base/'jsonl')
        self.assertEqual(sum(counts.values()),self.report['counts']['presentations'])
        first=json.loads((self.base/'jsonl'/'train.jsonl').read_text(encoding='utf8').splitlines()[0])
        self.assertIn('target',first)
        self.assertIn('orientation',first)

    def test_import_into_existing_training_task_store(self):
        path=self.base/'training_tasks.sqlite3'
        result=g.export_task_bank(self.db,path)
        self.assertEqual(result['groups'],self.report['counts']['transformation_cases'])
        self.assertEqual(result['presentations'],self.report['counts']['presentations'])
        from tools.nanojev_training_task_store import TrainingTaskStore
        with TrainingTaskStore(path) as store:
            self.assertTrue(store.verify()['db_integrity'])

    def test_build_is_deterministic_and_never_overwrites(self):
        import hashlib
        other=self.base/'repeat.sqlite3'
        g.build(other,seed=20261015,cases_per_game=6)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(),hashlib.sha256(other.read_bytes()).hexdigest())
        with self.assertRaises(FileExistsError):g.build(self.db,seed=20261015,cases_per_game=6)


if __name__=='__main__':unittest.main()

class EvaluatorTests(unittest.TestCase):
    def test_oracle_and_negative_control(self):
        from tools import nanojev_game_transform_eval as ev
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'library.sqlite3'
            g.build(db,cases_per_game=4)
            gold=ev.predictions_from_db(db,'dev')
            good=ev.evaluate(db,gold,'dev')
            self.assertEqual(good['strict_paired_5pct'],1)
            self.assertEqual(good['strict_paired_transformation_exact'],1)
            bad=[dict(x) for x in gold]
            bad[0]['operator']='not_a_real_operator'
            bad[0]['y']=float(bad[0]['y'])+200000
            damaged=ev.evaluate(db,bad,'dev')
            self.assertLess(damaged['strict_paired_5pct'],1)
            self.assertLess(damaged['transformation_exact'],1)
            self.assertLess(damaged['operator_accuracy'],1)

    def test_missing_predictions_fail_accuracy(self):
        from tools import nanojev_game_transform_eval as ev
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'library.sqlite3'
            g.build(db,cases_per_game=4)
            r=ev.evaluate(db,[],'dev')
            self.assertEqual(r['provided'],0)
            self.assertEqual(r['strict_paired_transformation_exact'],0)
