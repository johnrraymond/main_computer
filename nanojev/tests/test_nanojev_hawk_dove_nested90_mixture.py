"""Oracle-valid nested distribution, replay visibility, and 21-bank warmstart regression."""
import argparse
from collections import Counter
from decimal import Decimal
import unittest
from unittest.mock import patch

from tools import nanojev_hawk_dove_million as hd
from tools import nanojev_hawk_dove_nested90_mixture as mix
from tools import nanojev_hawk_dove_gaussian_joint_decoder_21_nested90 as trainer

P={hd.TAGS[0]:Decimal(1),hd.TAGS[1]:Decimal(0),
   hd.TAGS[2]:Decimal(0),hd.TAGS[3]:Decimal(1)}


class Nested90MixtureTests(unittest.TestCase):
    def test_nested_quotas_exact_and_monotone(self):
        n=10000
        q=mix.quotas(n)
        self.assertEqual(len(q),8)
        self.assertEqual(sum(q),n)
        self.assertEqual([sum(q[i+1:]) for i in range(7)],
                         [9000,8100,7290,6561,5905,5314,4783])

    def test_bin_edges_exclusive(self):
        self.assertEqual(mix.bin_for(-1_000_000),0)
        self.assertEqual(mix.bin_for(999999),1)
        self.assertEqual(mix.bin_for(-100000),1)
        self.assertEqual(mix.bin_for(0),7)
        self.assertEqual(mix.bin_for(-1),6)
        self.assertEqual(mix.bin_for(10),5)

    def test_plan_numerical_distribution_exact_and_replays_disclosed(self):
        # Fixed valid oracle X=a, Y=1M-a; only one unique a has |X|<1.
        # Exhaustive integer evaluation finds candidates in all bands.
        report,train,dev=mix.plan(P,set(),100,0,17)
        self.assertTrue(report['feasible'])
        self.assertFalse(report['unique_quotas_feasible'])
        self.assertEqual(len(train),100)
        self.assertEqual(dev,[])
        expected=mix.quotas(100)
        self.assertEqual([sum(mix.bin_for(a)==i for a in train) for i in range(8)],list(expected))
        self.assertEqual(train.count(0),expected[-1])
        self.assertGreater(report['repeated_training_presentations_per_tier'],0)

    def test_new_prompts_all_nine_tiers_are_oracle_certified(self):
        counts=[0,1,10,999,10000,99999,100000,1000000]
        rows=mix.render_rows(counts,P,42,'train')
        self.assertEqual(set(rows),set(hd.TIERS))
        for tier in hd.TIERS:
            self.assertEqual(len(rows[tier]),len(counts))
            self.assertEqual(len(set(r['case_id'] for r in rows[tier])),len(counts))
            for r,a in zip(rows[tier],counts):
                b=1_000_000-a
                full=tuple(s for i,s in enumerate(hd.SLOTS[tier]) if r['mask']&(1<<i))
                validated=hd.certify(r['prompt'],tier,a,b,P,full)
                self.assertEqual(r['x'],validated['x'])
                self.assertEqual(r['y'],validated['y'])

    def test_repeated_a_varies_prompt_mask_and_unique_case_ids(self):
        rows=mix.render_rows([0]*48,P,42,'train')
        for tier in hd.TIERS:
            self.assertEqual(len({r['case_id'] for r in rows[tier]}),48)
            if tier!='T08':
                self.assertGreater(len({r['prompt'] for r in rows[tier]}),1)

    def test_strict_unique_fails_instead_of_fake_novelty(self):
        report,train,dev=mix.plan(P,set(),100,0,17,strict_unique=True)
        self.assertFalse(report['feasible'])
        self.assertIsNone(train)
        self.assertIn('7',report['unique_instance_shortfalls'])

    def test_empty_bin_fails_closed(self):
        # Only one a=0, so all higher magnitude bands are impossible.
        report,train,dev=mix.plan(P,set(),100,0,17,candidates=[0])
        self.assertFalse(report['feasible'])
        self.assertIsNone(train)
        self.assertIn('0',report['unreachable_bands'])

    def test_separate_numerical_train_new_dev(self):
        report,train,dev=mix.plan(P,set(),10,10,17)
        self.assertTrue(report['feasible'] is False) # X=0 cannot appear in both without leakage
        self.assertIsNone(train)

    def test_identity_and_runner_isolated(self):
        args=trainer.parser().parse_args([])
        self.assertEqual(args.mixture_new_dev_per_tier,0)
        self.assertEqual(args.mixture_train_per_tier,512)
        self.assertEqual(trainer.BANK_COUNT,21)
        self.assertIn('nested90',trainer.SCHEMA)
        self.assertTrue(str(trainer.DEFAULT_OUTPUT).endswith('hawk_dove_gaussian_joint_decoder_21_nested90_v1'))


if __name__=='__main__':unittest.main()

# Keep a full runner smoke separate from oracle tests, using fake frozen
# captures. This checks actual checkpoint import, training, resume, and
# untouched test split without requiring a Qwen download.
class Nested90RunSmoke(unittest.TestCase):
    def test_import_21_checkpoint_train_and_resume(self):
        import io
        import json
        import tempfile
        from pathlib import Path
        from unittest import mock
        import torch
        from tools import nanojev_hawk_dove_gaussian_joint_decoder_21 as old21
        from tools import nanojev_hawk_dove_gaussian_zero_overflow_16 as prior
        from tools import nanojev_hawk_dove_coarse_to_fine_gaussian as fine
        torch.set_num_threads(1)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            source=root/'source';teacher=root/'teacher';fixture=root/'fixture'
            source.write_bytes(b'S');teacher.write_bytes(b'T');fixture.write_bytes(b'F')
            ref_dir=root/'fine';best_dir=ref_dir/'best_checkpoints';best_dir.mkdir(parents=True)
            def frozen_file(path,obj):
                torch.save(obj,path);return path.read_bytes()
            original=frozen_file(ref_dir/'parent_snapshot.pt',{'schema':'original'})
            donor=frozen_file(ref_dir/'donor_snapshot.pt',{'schema':'donor'})
            refiner=fine.LocalRefiner(5,kind='gaussian',registers=9)
            ident={'schema':fine.SCHEMA,'parent_arm':'continuous',
                   'parent_sha256':trainer.sha(original),'donor_sha256':trainer.sha(donor),
                   'source_sha256':trainer.fork.fingerprint(source),
                   'teacher_sha256':trainer.fork.fingerprint(teacher),
                   'seed':20261018,'train_per_tier':8,'dev_per_tier':8,
                   'registers':9,'sigma_bins':2.,'floor':125000.,
                   'fraction':.5,'temperature':8.}
            first=best_dir/'best_small_gaussian.pt'
            frozen_file(first,{'schema':fine.SCHEMA,'identity':ident,'step':300,
                               'heads':{'gaussian':refiner.state_dict()}})
            first.with_suffix('.json').write_text(json.dumps({
                'checkpoint_sha256':trainer.sha(first.read_bytes()),'arm':'gaussian','step':300}))
            old_system=old21.JointGaussianSystem(5,fine.LocalRefiner(5,kind='gaussian',registers=9),hidden=16)
            with torch.no_grad():old_system.head.legacy.network[-1].bias.fill_(.11)
            donor_dir=root/'donor21';donor_dir.mkdir();donor_path=donor_dir/'latest.pt'
            donor_ident={'stage2_sha256':trainer.sha(first.read_bytes()),
                'original_parent_sha256':trainer.sha(original),'donor_sha256':trainer.sha(donor),
                'source_sha256':trainer.fork.fingerprint(source),
                'teacher_sha256':trainer.fork.fingerprint(teacher),
                'relative_max':2000000.,'bank_count':21,'hidden':16,'bank_init_sha256':None}
            frozen_file(donor_path,{'schema':old21.SCHEMA,'identity':donor_ident,
                'bank_states':{str(k):old_system.cascade.banks[str(k)].state_dict()
                               for k in old21.STAGES},
                'head_state':old_system.head.state_dict(),'step':1000})
            args=trainer.parser().parse_args(['--refinement-checkpoint',str(first),
                '--decoder-init-checkpoint',str(donor_path),'--source',str(source),
                '--teacher',str(teacher),'--mixture-fixture',str(fixture),
                '--output',str(root/'nested_run'),'--device','cpu','--dtype','float32',
                '--train-per-tier','8','--dev-per-tier','8','--test-per-tier','8',
                '--mixture-train-per-tier','8','--hidden','16','--views','16',
                '--max-steps','3','--chunk-steps','1','--additional-steps','1'])
            y=torch.tensor([1.,2.,3.,4.,10.,1000.,100000.,1000000.])
            batch={'features':torch.randn(8,5),'coarse':y+100.,'target':y}
            data={tier:batch for tier in old21.TIERS}
            rows={tier:[{'instance':f'new-{i}'} for i in range(8)] for tier in old21.TIERS}
            captured=[]
            def mixture_setup(a):
                a._mixture_fixture_sha256='fixture_sha'
                a._mixture_train_sha256='train_sha'
                a._mixture_dev_sha256=None
                a._mixture_plan_sha256='plan_sha'
                return {'feasible':True,'unique_training_instances':8},rows,None
            def fake_old_features(a,m,d,b,c,split):
                captured.append(split)
                self.assertEqual(split,'dev')
                return data,{t:[{'instance':f'original_dev-{i}'} for i in range(8)] for t in old21.TIERS}
            class Cache:
                def __init__(self,*a,**kw):pass
            fake_model={'device':'cpu','config':{'max_prompt_tokens':99}}
            with mock.patch.object(trainer,'build_mixture',side_effect=mixture_setup), \
                 mock.patch.object(trainer.fork.ex,'load_current',return_value=fake_model), \
                 mock.patch.object(fine,'init_frozen',return_value=(None,None)), \
                 mock.patch.object(trainer.fork.cached,'FrozenCaptureCache',Cache), \
                 mock.patch.object(prior,'load_features',side_effect=fake_old_features), \
                 mock.patch.object(mix,'load_features',return_value=data):
                trainer.run(args)
                ck=trainer.master.load_state((root/'nested_run/latest.pt').read_bytes())
                self.assertEqual(ck['step'],1)
                self.assertEqual(ck['identity']['bank_count'],21)
                self.assertEqual(ck['identity']['sampler'],
                  'tier_balanced_uniform_new_oracle_verified_nested90_instances')
                self.assertEqual(ck['identity']['decoder_init_sha256'],
                                 trainer.sha(donor_path.read_bytes()))
                self.assertEqual(len(ck['bank_states']),21)
                report=json.loads((root/'nested_run/probe-step-0000001.json').read_text())
                self.assertEqual(report['joint']['cases'],72)
                self.assertIn('mixture_feasibility',report)
                self.assertIn('gaussian_gradient_multiplier',
                              report['chunk_mean_train_losses_and_gradients'])
                self.assertEqual(captured,['dev'])
                trainer.run(args)
                ck=trainer.master.load_state((root/'nested_run/latest.pt').read_bytes())
                self.assertEqual(ck['step'],2)

class Nested90FeasibilityFallbackTests(unittest.TestCase):
    def test_empty_band_reallocated_downward_only_with_opt_in(self):
        payoffs={hd.TAGS[0]:Decimal(9),hd.TAGS[1]:Decimal(-7),
                 hd.TAGS[2]:Decimal(0),hd.TAGS[3]:Decimal(1)}
        bad,_,_=mix.plan(payoffs,set(),512,0,2026)
        self.assertFalse(bad['feasible'])
        self.assertIn('6',bad['unreachable_bands'])
        accepted,train,_=mix.plan(payoffs,set(),512,0,2026,reallocate_empty=True)
        self.assertTrue(accepted['feasible'])
        self.assertFalse(accepted['distribution_exact'])
        self.assertEqual(accepted['quota_train_by_exclusive_band'][6],0)
        self.assertEqual(accepted['quota_train_by_exclusive_band'][7],272)
        self.assertEqual(accepted['empty_band_transfers'],
                         [{'from_band':6,'to_band':7,'train':27,'new_dev':0}])
        self.assertEqual(len(train),512)
        self.assertEqual(train.count(437500),272)
        self.assertAlmostEqual(accepted['actual_cumulative_percent_below_threshold']['1'],100*272/512)


class FractionalPayoffFixtureCompatibilityTests(unittest.TestCase):
    """Production teacher may bind fractional payoffs; X/Y must be integral."""
    FRAC = {
        hd.TAGS[0]: Decimal('2.5'),
        hd.TAGS[1]: Decimal('-1.5'),
        hd.TAGS[2]: Decimal('0.25'),
        hd.TAGS[3]: Decimal('0.25'),
    }
    FRAC_SPARSE = {
        hd.TAGS[0]: Decimal('2.5'),
        hd.TAGS[1]: Decimal('-1.5'),
        hd.TAGS[2]: Decimal('0.25'),
        hd.TAGS[3]: Decimal('0'),
    }

    def test_certified_fixture_accepts_noninteger_payoff_coefficients(self):
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            fixture=Path(td)/'fixture.json'
            fixture.write_text(json.dumps({
                'schema':'nanojev.hawk-dove.million-counts.fixture.v1',
                'binding_version':'fractional-test-v1',
                'encounter_total':1_000_000,
                'payoffs':{k:str(v) for k,v in self.FRAC.items()},
            }))
            _,fixture_meta=hd.read_fixture(fixture)
            with patch.object(hd,'db_manifest',return_value={
                'config':{'fixture':{'sha256':fixture_meta['sha256']}}
            }):
                p,meta=mix.certified_payoffs(fixture,Path(td)/'teacher.sqlite')
            self.assertEqual(p,self.FRAC)
            self.assertEqual(meta,fixture_meta)
            with patch.object(hd,'db_manifest',return_value={
                'config':{'fixture':{'sha256':'not-the-parent-fixture'}}
            }):
                with self.assertRaisesRegex(ValueError,'FIXTURE_DIFFERENT_FROM_PARENT'):
                    mix.certified_payoffs(fixture,Path(td)/'teacher.sqlite')

    def test_candidate_filters_nonintegral_xy_without_rounding(self):
        candidates=[0,1,2,3,4,8,375000,375001,1_000_000]
        buckets=mix.available_counts(self.FRAC_SPARSE,{8},candidates=candidates)
        available=set().union(*map(set,buckets))
        # Y=0.25*a. Only multiples of four produce integral Y.
        self.assertEqual(available,{0,4,375000,1_000_000})
        self.assertEqual(hd.oracle(375000,625000,self.FRAC_SPARSE)[0],Decimal('0'))
        self.assertIn(375000,buckets[7])
        self.assertIn(1_000_000,buckets[0])
        for a in available:
            x,y,_=hd.oracle(a,1_000_000-a,self.FRAC_SPARSE)
            self.assertEqual(x,x.to_integral_value())
            self.assertEqual(y,y.to_integral_value())

    def test_plan_and_nine_prompt_tiers_with_fractional_payoffs(self):
        report,train,_=mix.plan(self.FRAC,set(),100,0,17)
        self.assertTrue(report['feasible'])
        self.assertTrue(report['distribution_exact'])
        self.assertEqual(len(train),100)
        self.assertEqual([sum(mix.bin_for(hd.oracle(a,1_000_000-a,self.FRAC)[0])==i
                              for a in train) for i in range(8)],list(mix.quotas(100)))
        self.assertEqual({int(k):v for k,v in report['train_actual_bands'].items()},
                         dict(Counter(mix.bin_for(hd.oracle(a,1_000_000-a,self.FRAC)[0])
                                      for a in train)))
        certified_rows=mix.render_rows([0,4,375000,999996],self.FRAC,11,'train')
        for tier,rows in certified_rows.items():
            self.assertEqual(len(rows),4)
            for row,a in zip(rows,[0,4,375000,999996]):
                full=tuple(s for i,s in enumerate(hd.SLOTS[tier]) if row['mask']&(1<<i))
                expected=hd.certify(row['prompt'],tier,a,1_000_000-a,self.FRAC,full)
                self.assertEqual(row['x'],expected['x'])
                self.assertEqual(row['y'],expected['y'])

    def test_fractional_output_invalid_cannot_be_rendered(self):
        bad_a=1
        self.assertNotEqual(hd.oracle(bad_a,1_000_000-bad_a,self.FRAC_SPARSE)[1].to_integral_value(),
                            hd.oracle(bad_a,1_000_000-bad_a,self.FRAC_SPARSE)[1])
        with self.assertRaisesRegex(ValueError,'NONINTEGRAL_TARGET_UNSUPPORTED'):
            mix.render_rows([bad_a],self.FRAC_SPARSE,11,'train')

    def test_real_sqlite_teacher_audit_and_new_prompt_generation(self):
        """Regression: both user entry points pass the fractional fixture guard."""
        import contextlib
        import io
        import json
        import sqlite3
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            fixture=root/'fixture.json'
            teacher=root/'teacher.sqlite3'
            fixture.write_text(json.dumps({
                'schema':'nanojev.hawk-dove.million-counts.fixture.v1',
                'binding_version':'fractional-test-v1',
                'encounter_total':1_000_000,
                'payoffs':{k:str(v) for k,v in self.FRAC.items()},
            }))
            _,fixture_meta=hd.read_fixture(fixture)
            with sqlite3.connect(teacher) as conn:
                conn.execute('CREATE TABLE manifest(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
                conn.execute('CREATE TABLE instances(a INTEGER,b INTEGER,split TEXT NOT NULL)')
                conn.executemany('INSERT INTO instances VALUES (?,?,?)',[
                    (100000,900000,'train'),(750000,250000,'dev'),
                    (1000000,0,'test')])
                cfg={'schema':hd.SCHEMA,'fixture':fixture_meta}
                conn.execute('INSERT INTO manifest VALUES (?,?)',('config',json.dumps(cfg)))
            args=trainer.parser().parse_args([
                '--teacher',str(teacher),'--mixture-fixture',str(fixture),
                '--mixture-audit-only','--mixture-train-per-tier','512',
            ])
            with contextlib.redirect_stdout(io.StringIO()):
                result=trainer.audit_only(args)
            self.assertTrue(result['feasible'])
            self.assertTrue(result['distribution_exact'])
            self.assertGreater(result['unique_training_instances'] if 'unique_training_instances' in result else 0,0)
            # Train entry performs the identical build before GPU model loading.
            report,rows,dev=trainer.build_mixture(args)
            self.assertEqual(sum(map(len,rows.values())),9*512)
            self.assertIsNone(dev)
            self.assertTrue(report['feasible'])
            self.assertEqual(report['generated_train_instance_count'],512)
            self.assertNotIn(750000,{int(r['instance'].split('-')[2]) for r in rows['T00']})
