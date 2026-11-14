"""Focused regression for the isolated 21-bank learnability experiment."""
from __future__ import annotations
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import torch
from tools import nanojev_hawk_dove_gaussian_joint_decoder_21_learnability as learn
from tools import nanojev_hawk_dove_learnability_monitor as monitor
from tools import nanojev_hawk_dove_nested90_expected as expected
from tools import nanojev_hawk_dove_nested90_mixture as old_mixture
from tools import nanojev_hawk_dove_million as hd
from tools import nanojev_hawk_dove_gaussian_joint_decoder_21 as old21
from tools import nanojev_hawk_dove_gaussian_zero_overflow_16 as prior
from tools import nanojev_hawk_dove_coarse_to_fine_gaussian as fine

P={hd.TAGS[0]:Decimal('9'),hd.TAGS[1]:Decimal('-7'),hd.TAGS[2]:Decimal('0'),hd.TAGS[3]:Decimal('1')}

class LearnabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r, cls.train, cls.dev=expected.plan(P,set(),512,128,20261021)
        cls.rows=expected.render_rows(cls.train,P,20261021,'train')

    def test_controlled_subset_keeps_all_bands_and_unique_inputs(self):
        chosen=learn.controlled_subset(self.rows,32,3)
        for tier in learn.TIERS:
            self.assertEqual(len(chosen[tier]),32)
            self.assertEqual(len({str(x['instance']) for x in chosen[tier]}),32)
            counts=[sum(old_mixture.bin_for(float(x['x']))==i for x in chosen[tier]) for i in range(8)]
            self.assertEqual(counts,list(old_mixture.quotas(32)))
        same=learn.controlled_subset(self.rows,32,3)
        self.assertEqual([r['instance'] for r in chosen['T00']],
                         [r['instance'] for r in same['T00']])

    def test_missing_subset_support_fails(self):
        sparse={t:self.rows[t][:10] for t in learn.TIERS}
        self.assertRaisesRegex(ValueError,'LEARNABILITY_MISSING_MAGNITUDE_SUPPORT',
                               learn.controlled_subset,sparse,8,5)

    def test_strict_and_zero_baseline_not_confused(self):
        src=learn.magnitude_band_metrics([(
           torch.tensor([.001,.5,4.]),torch.tensor([.01,.52,4.]),
           torch.tensor([10.,10.,10.]))])
        sub=src['7']
        self.assertEqual(sub['n'],2)
        self.assertEqual(sub['zero_predictor_accuracy_5pct_floor'],.5)
        self.assertEqual(sub['strict_relative_accuracy_5pct_nonzero'],.5)
        self.assertGreater(sub['mean_absolute_asinh_error'],0.)

    def test_cli_isolated_and_no_accuracy_abort(self):
        a=learn.parser().parse_args([])
        self.assertEqual(a.retention_fraction,0.)
        self.assertEqual(a.experiment_arm,'decoder-only')
        self.assertEqual(a.learnability_train_per_tier,32)
        self.assertIn('learnability',str(a.output))
        self.assertIn('learnability',learn.SCHEMA)

    def test_monitor_reports_both_learning_and_overfit(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            def emit(step,train,dev):
                record={'schema':learn.SCHEMA+'.probe.v1','step':step,
                     'learnability':{'arm':'decoder-only','fractional_train_mae_asinh':train,
                        'fractional_new_dev_mae_asinh':dev},
                     'joint':{'accuracy_5pct':.05,'small_accuracy_5pct':.01},
                     'nested90_unseen_dev':{'joint':{'accuracy_5pct':.07},
                         'magnitude_bands':{'7':{'strict_relative_accuracy_5pct_nonzero':.01,
                            'zero_predictor_accuracy_5pct_floor':.03,'accuracy_5pct_floor':.06}}},
                     'magnitude_bands_train':{'7':{'strict_relative_accuracy_5pct_nonzero':.01}},
                     'chunk_mean_train_losses_and_gradients':{'gradient_clip_fraction':.3}}
                (root/f'probe-step-{step:07d}.json').write_text(json.dumps(record))
            emit(0,8.,8.);emit(1000,4.,7.)
            a=monitor.review(root)
            self.assertEqual(a['assessment'],'TRAIN_AND_HELD_OUT_ERROR_DECREASING')
            self.assertEqual(a['latest']['fraction_steps_clipped'],.3)
            emit(2000,2.,9.)
            a=monitor.review(root)
            self.assertEqual(a['assessment'],'TRAIN_FIT_WITHOUT_HELD_OUT_GAIN')
            self.assertEqual(a['best_held_out_asinh_mae'],7.)
            self.assertFalse(a['test_split_read'])

    def test_training_modes_save_and_resume_without_accuracy_abort(self):
        torch.set_num_threads(1)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source=root/'source';teacher=root/'teacher';fixture=root/'fixture'
            source.write_bytes(b'S');teacher.write_bytes(b'T');fixture.write_bytes(b'F')
            ref_dir=root/'fine';best_dir=ref_dir/'best_checkpoints';best_dir.mkdir(parents=True)
            def put(path,obj):
                torch.save(obj,path);return path.read_bytes()
            original=put(ref_dir/'parent_snapshot.pt',{'schema':'original'})
            donor=put(ref_dir/'donor_snapshot.pt',{'schema':'donor'})
            refiner=fine.LocalRefiner(5,kind='gaussian',registers=9)
            ident={'schema':fine.SCHEMA,'parent_arm':'continuous',
                   'parent_sha256':learn.sha(original),'donor_sha256':learn.sha(donor),
                   'source_sha256':learn.fork.fingerprint(source),
                   'teacher_sha256':learn.fork.fingerprint(teacher),
                   'seed':20261018,'train_per_tier':8,'dev_per_tier':8,
                   'registers':9,'sigma_bins':2.,'floor':125000.,
                   'fraction':.5,'temperature':8.}
            first=best_dir/'best_small_gaussian.pt'
            put(first,{'schema':fine.SCHEMA,'identity':ident,'step':300,
                               'heads':{'gaussian':refiner.state_dict()}})
            first.with_suffix('.json').write_text(json.dumps({
                'checkpoint_sha256':learn.sha(first.read_bytes()),'arm':'gaussian','step':300}))
            old_system=old21.JointGaussianSystem(5,
                fine.LocalRefiner(5,kind='gaussian',registers=9),hidden=16)
            donor_dir=root/'donor21';donor_dir.mkdir();donor_path=donor_dir/'latest.pt'
            donor_ident={'stage2_sha256':learn.sha(first.read_bytes()),
                'original_parent_sha256':learn.sha(original),'donor_sha256':learn.sha(donor),
                'source_sha256':learn.fork.fingerprint(source),
                'teacher_sha256':learn.fork.fingerprint(teacher),
                'relative_max':2000000.,'bank_count':21,'hidden':16,'bank_init_sha256':None}
            put(donor_path,{'schema':old21.SCHEMA,'identity':donor_ident,
                'bank_states':{str(k):old_system.cascade.banks[str(k)].state_dict()
                               for k in old21.STAGES},
                'head_state':old_system.head.state_dict(),'step':1000})
            y=torch.tensor([1.,2.,3.,4.,10.,1000.,100000.,1000000.])
            batch={'features':torch.randn(8,5),'coarse':y+100.,'target':y}
            data={tier:batch for tier in old21.TIERS}
            rows={tier:[{'instance':f'new-{i}','x':float(y[i])} for i in range(8)] for tier in old21.TIERS}
            def mix_setup(a):
                a._mixture_fixture_sha256='fixture_sha'
                a._mixture_train_sha256='train_sha'
                a._mixture_dev_sha256='dev_sha'
                a._mixture_plan_sha256='plan_sha'
                return {'feasible':True},rows,rows
            def fake_old_features(a,m,d,b,c,split):
                self.assertIn(split,('dev','train'))
                return data,{t:[{'instance':f'original_{split}-{i}'} for i in range(8)]
                             for t in old21.TIERS}
            class Cache:
                def __init__(self,*a,**kw):pass
            fake_model={'device':'cpu','config':{'max_prompt_tokens':99}}
            for arm in ('decoder-only','joint'):
                destination=root/arm
                args=learn.parser().parse_args(['--refinement-checkpoint',str(first),
                    '--decoder-init-checkpoint',str(donor_path),'--source',str(source),
                    '--teacher',str(teacher),'--mixture-fixture',str(fixture),
                    '--output',str(destination),'--device','cpu','--dtype','float32',
                    '--train-per-tier','8','--dev-per-tier','8','--test-per-tier','8',
                    '--mixture-train-per-tier','8','--mixture-new-dev-per-tier','8',
                    '--learnability-train-per-tier','0','--experiment-arm',arm,
                    '--hidden','16','--views','16','--max-original-dev-collapse','.02',
                    '--max-steps','3','--chunk-steps','1','--additional-steps','1'])
                with mock.patch.object(learn,'build_mixture',side_effect=mix_setup), \
                     mock.patch.object(learn.fork.ex,'load_current',return_value=fake_model), \
                     mock.patch.object(fine,'init_frozen',return_value=(None,None)), \
                     mock.patch.object(learn.fork.cached,'FrozenCaptureCache',Cache), \
                     mock.patch.object(prior,'load_features',side_effect=fake_old_features), \
                     mock.patch.object(expected,'load_features',return_value=data):
                    learn.run(args)
                    ck=learn.master.load_state((destination/'latest.pt').read_bytes())
                    self.assertEqual(ck['step'],1)
                    self.assertEqual(ck['identity']['experiment_arm'],arm)
                    original_bank=old_system.cascade.banks[str(old21.STAGES[0])].state_dict()
                    changed=[not torch.equal(ck['bank_states'][str(k)]['readout.weight'],
                                      old_system.cascade.banks[str(k)].state_dict()['readout.weight'])
                             for k in old21.STAGES]
                    if arm=='decoder-only':
                        self.assertFalse(any(changed),'decoder-only must freeze ALL bank weights')
                    else:
                        self.assertTrue(any(changed),'joint arm must optimize bank weights')
                    report=json.loads((destination/'probe-step-0000001.json').read_text())
                    self.assertIn('learnability',report)
                    self.assertIn('gradient_clip_fraction',report['chunk_mean_train_losses_and_gradients'])
                    self.assertTrue((destination/'research_best_new_dev.pt').exists())
                    self.assertFalse(json.loads((destination/'research_best_new_dev.json').read_text())['deployment_eligible'])
                    learn.run(args)
                    self.assertEqual(learn.master.load_state((destination/'latest.pt').read_bytes())['step'],2)
                result=monitor.review(destination)
                self.assertEqual(result['latest']['step'],2)
                self.assertFalse(result['test_split_read'])

if __name__=='__main__':unittest.main()
