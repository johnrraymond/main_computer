"""Deterministic brain acceptance tests, including no early oracles/recompilation."""
import json
from contextlib import closing
import sqlite3
import unittest
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory

from tools import nanojev_deterministic_brain_v8 as brain
from tools import nanojev_game_native_objectives_v6 as native
from tools import nanojev_game_math_smoke as smoke
from tools import nanojev_game_math_rebuild_audit as rebuild
from tools import nanojev_game_mutation_brain_v7 as v7


def sample_matrix(mode='mixed_row'):
    f = native._facts('matrix', 'captain_ship_defense', 'Captain Hull Preservation',
        {'U': [[[4,-13],[-16,-7]],[[-24,2],[0,-1]]],
         'row_actions': ['Intercept','Evade'], 'column_actions': ['Raid','Withdraw']},
        mode, 0 if mode == 'best_response' else None)
    return native.render08(f)


def sample_scalar():
    title = native.legacy.SCALAR_MODELS['markov_one_step'][0]
    f = native._facts('scalar', 'markov_one_step', title,
        {'parameters': {'r':1,'gamma':.9,'p':.6,'v_good':5,'v_bad':-2}},'scalar_value')
    return native.render08(f)


class LocalBrainTests(unittest.TestCase):
    def test_end_to_end_matrix_and_scalar(self):
        for p in (sample_matrix('best_response'), sample_matrix('mixed_row'),
                  sample_matrix('mixed_col'), sample_scalar()):
            output = brain.run(p, mutation_seed=17, order_seed=11)
            self.assertTrue(output['independent_oracle_verified'])
            self.assertEqual(output['closed_checkpoints'], 9)
            self.assertEqual(output['transition_count'], 8)
            self.assertTrue(output['T00_executed'])
            self.assertEqual([x['tier'] for x in output['checkpoints']], list(range(8,-1,-1)))
            self.assertEqual(output['checkpoints'][-1]['steps'][-1]['mutation'], 'Z_DOMAIN')

    def test_no_t08_restart_or_early_oracle_in_execution(self):
        prompt = sample_scalar()
        with patch.object(v7, 'state_at', side_effect=AssertionError('RECOMPILED')):
            with patch.object(native, 'begin', side_effect=AssertionError('BEGIN_RESTART')):
                with patch.object(native, 'step', side_effect=AssertionError('NATIVE_STEP')):
                    with patch.object(native.legacy, 'scalar_oracle', side_effect=AssertionError('EARLY_ORACLE')):
                        r = brain.run(prompt, mutation_seed=17, order_seed=11, verify=False)
        self.assertFalse(r['independent_oracle_verified'])
        self.assertTrue(r['T00_executed'])
        # Independent oracle IS invoked if and only if requested after T00.
        with patch.object(native.legacy, 'scalar_oracle', side_effect=ValueError('POST_T00_ORACLE')):
            with self.assertRaisesRegex(ValueError, 'POST_T00_ORACLE'):
                brain.run(prompt, mutation_seed=17, order_seed=11, verify=True)

    def test_seed_replay_commutation_and_actual_order_diversity(self):
        p = sample_scalar()
        c = brain.audit_seed_contract(p)
        self.assertEqual(c['status'], 'PASS')
        self.assertGreater(c['order_variants'], 0)
        a = brain.run(p, mutation_seed=17, order_seed=11)
        b = brain.run(p, mutation_seed=18, order_seed=11)
        self.assertNotEqual(a['final'], b['final'])
        self.assertNotEqual(a['canonical_T00'], b['canonical_T00'])

    def test_bind_public_inputs_and_reject_answers(self):
        f = sample_scalar()
        a = brain.run(f, mutation_seed=17, order_seed=11)
        b = brain.run(f, mutation_seed=17, order_seed=11,
                      inputs={'parameters': {'r': 4.5}})
        self.assertNotEqual(a['final'], b['final'])
        with self.assertRaisesRegex(ValueError, 'ONLY_PARAMETERS_BINDABLE'):
            brain.run(f, mutation_seed=17, order_seed=11, inputs={'Z': 100})
        matrix = sample_matrix('mixed_col')
        m = brain.run(matrix, mutation_seed=17, order_seed=11,
                      inputs={'U': [[[4,-13],[-16,-7]],[[-25,2],[0,-1]]]})
        self.assertEqual(m['final']['OP'], 'DIVIDE')

    def test_unclean_checkpoint_cannot_descend(self):
        st = brain.begin(sample_matrix())
        with self.assertRaisesRegex(ValueError, 'CHECKPOINT_GATE_NOT_LAST'):
            # Scheduler cannot make a partial checkpoint portable.
            changes = brain.plan(st, 17, brain.state_identity(st))
            bad_order = ('Z_DOMAIN',) + tuple(sorted(changes))
            with patch.object(v7, '_schedule', return_value=bad_order):
                brain.execute_checkpoint(st, order_seed=11, mutation_seed=17,
                                         input_id=brain.state_identity(st))

    def test_stale_mutation_and_structural_domain_rejected(self):
        st = brain.begin(sample_matrix('mixed_row'))
        changes = brain.plan(st, 17, brain.state_identity(st))
        mutated = dict(changes['X']); mutated['before'] += 10
        with self.assertRaisesRegex(ValueError, 'STALE_MUTATION'):
            brain.apply_local(st, mutated)
        with self.assertRaisesRegex(ValueError, 'NO_INTERIOR_MIXED_NASH'):
            brain.begin(sample_matrix('mixed_row'),inputs={'U': [[[4,-13],[-16,-7]], [[4,2],[-16,-1]]]})

    def test_symbolic_dependency_xy(self):
        title = native.legacy.SCALAR_MODELS['nash_bargain_split'][0]
        p = native.render08(native._facts('scalar','nash_bargain_split', title,
                {'parameters': {'d1':2,'d2':3,'V':18}},'scalar_value'))
        st = brain.begin(p)
        self.assertEqual(brain._path_role(st['facts'], ('param','d1')), 'XY')
        output = brain.run(p, mutation_seed=10, order_seed=11)
        self.assertTrue(any(c['dependency_footprint']=='XY'
                            for cp in output['checkpoints'] for c in cp['changes']))


class RebuildAndCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory(prefix='nanojev-v8-')
        cls.path = Path(cls.temp.name)
        cls.generated = smoke.generate(cls.path/'generated',20261016,4)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_all_native_t08_cases_execute(self):
        with sqlite3.connect(self.path/'generated'/'game_transformations.sqlite3') as db:
            rows = db.execute('SELECT prompt FROM native_v6_tiers WHERE tier=8 ORDER BY case_id').fetchall()
        self.assertEqual(len(rows),284)
        for (prompt,) in rows:
            result = brain.run(prompt,mutation_seed=17,order_seed=11)
            self.assertTrue(result['independent_oracle_verified'])

    def test_new_proof_bank_source_only(self):
        summary = self.generated['deterministic_brain']
        self.assertEqual(summary['games'],33)
        self.assertEqual(summary['runs'],66)
        self.assertEqual(summary['closed_checkpoints'],594)
        with sqlite3.connect(self.path/'generated'/'deterministic_brain_v8.sqlite3') as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM deterministic_v8_runs').fetchone()[0],66)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM deterministic_v8_checkpoints').fetchone()[0],594)
        manifest=json.loads((self.path/'generated'/'manifest.json').read_text())
        self.assertIn('nanojev_deterministic_brain_v8.py', manifest['source_sha256'])

    def test_proof_banks_close_sqlite_handles_before_staging_promotion(self):
        # The transaction context manager commits but does NOT close sqlite3
        # connections. Holding a Python reference exposes the WinError 32 leak.
        class TrackedConnection(sqlite3.Connection):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.close_count = 0

            def close(self):
                self.close_count += 1
                return super().close()

        real_connect = sqlite3.connect
        source_path = self.path/'generated'/'game_transformations.sqlite3'
        for name, exporter in (
            ('v7', v7.export_proof_bank),
            ('v8', brain.export_proof_bank),
        ):
            with self.subTest(exporter=name):
                target = self.path/f'{name}_handle_test.sqlite3'
                tracked = []

                def capture(path, *args, **kwargs):
                    if Path(path).resolve() == target.resolve():
                        kwargs['factory'] = TrackedConnection
                        cn = real_connect(path, *args, **kwargs)
                        tracked.append(cn)
                        return cn
                    return real_connect(path, *args, **kwargs)

                with closing(real_connect(source_path)) as src:
                    with patch.object(sqlite3, 'connect', side_effect=capture):
                        exporter(src, target, seed=20261016)
                self.assertEqual(len(tracked), 1)
                self.assertEqual(tracked[0].close_count, 1)
                with self.assertRaises(sqlite3.ProgrammingError):
                    tracked[0].execute('SELECT 1')
                # On Windows this rename is rejected if the handle is open.
                moved = target.with_suffix('.moved.sqlite3')
                target.rename(moved)
                self.assertTrue(moved.is_file())
                moved.unlink()

    def test_two_independent_clean_rebuilds(self):
        report=rebuild.audit(instances=4,seed=20261016)
        self.assertEqual(report['status'],'PASS')
        self.assertEqual(report['artifacts_compared'],len(smoke.FILES))


if __name__=='__main__': unittest.main()
