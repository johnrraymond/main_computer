"""Read-only nested90 diagnostic regression and miniature integration suite."""
from __future__ import annotations
import json
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from tools import nanojev_hawk_dove_nested90_diagnostic as diag
from tools import nanojev_hawk_dove_nested90_expected as mix
from tools import nanojev_hawk_dove_nested90_mixture as old
from tools import nanojev_hawk_dove_million as hd
from tools import nanojev_hawk_dove_gaussian_joint_decoder_21_nested90_expected as runner

P={hd.TAGS[0]:Decimal('9'),hd.TAGS[1]:Decimal('-7'),
   hd.TAGS[2]:Decimal('0'),hd.TAGS[3]:Decimal('1')}


def fake_probe(step,*,head=.64,base=.55,new=.68,train=.77,sub=.63,with_new=True):
    bands={str(i):{'n':20,'accuracy_5pct_floor':.65,'frozen_accuracy_5pct_floor':.5,
                       'strict_relative_accuracy_5pct_nonzero':.2,'nonzero_n':20,
                       'mae':.12,'fixed_vs_stage2':5,'broken_vs_stage2':2} for i in range(8)}
    bands['7']['accuracy_5pct_floor']=sub
    case={'schema':runner.SCHEMA+'.probe.v1','step':step,
        'joint':{'accuracy_5pct':head,'small_accuracy_5pct':.6},
        'frozen_stage2':{'accuracy_5pct':base,'small_accuracy_5pct':.45},
        'train':{'accuracy_5pct':train,'small_accuracy_5pct':.7},
        'magnitude_bands_train':bands,
        'magnitude_bands_original_dev':bands,
        'bank_dev':[{'bank':j+1,'interior_n':20,'interior_numeric_accuracy_5pct':.4,
                     'overflow_accuracy':.9} for j in range(21)],
        'improved_vs_base_cases':16,'regressed_vs_base_cases':10,
        'mixture_feasibility':{'frozen_feature_resolution':{'train':{},
            'new_dev':{t:{'aliased_distinct_target_cases':0} for t in hd.TIERS}}},
        'chunk_mean_train_losses_and_gradients':{'numeric_gradient_head_norm':.5,
                          'gaussian_to_numeric_bank_gradient_ratio':.23}}
    if with_new:
        case['nested90_unseen_dev']={'joint':{'accuracy_5pct':new,'small_accuracy_5pct':.6},
            'frozen_stage2':{'accuracy_5pct':.52,'small_accuracy_5pct':.45},
            'magnitude_bands':bands}
    return case


class DiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report,cls.train,cls.dev=mix.plan(P,set(),512,128,20261021)

    def test_all_tier_preflight_and_precision_warning(self):
        a=SimpleNamespace(fixture=Path('ignored'),teacher=Path('ignored'),train_per_tier=512,
                          dev_per_tier=128,seed=20261021,reallocate_empty_bands=False)
        actual_calls=[]
        def get_original(teacher,*,splits=('dev','test')):
            actual_calls.append(splits)
            if 'test' in splits:raise AssertionError('TEST_SPLIT_READ')
            return {437500}
        with mock.patch.object(old,'certified_payoffs',return_value=(P,{'sha256':'abc'})),\
             mock.patch.object(old,'original_counts',side_effect=get_original):
            result=diag.preflight(a)
        self.assertEqual(actual_calls,[('dev',),('train',)])
        self.assertFalse(result['test_split_read'])
        self.assertEqual(result['summary']['distinct_below_one_targets'],245)
        self.assertIn('all_tier_prompt_oracle_certification', [r['name'] for r in result['checks']])
        self.assertEqual(next(r for r in result['checks'] if r['name']=='all_tier_prompt_oracle_certification')['status'],'pass')
        self.assertEqual(result['status'],'warn')  # float32 role collisions reported, not hidden

    def test_float32_aliases_measured_separately_from_qwen(self):
        enc=diag.numeric_encoding_audit(self.train,self.dev,P)
        row=enc['train']['per_tier']['T07']
        self.assertLess(row['operand_plus_visible_y_float32']['unique_input_vectors'],245)
        self.assertEqual(row['operand_plus_visible_y_float32']['target_cases'],245)
        self.assertGreater(row['operand_plus_visible_y_float32']['max_target_span_sharing_vector'],'0')

    def test_hidden_x_channel_never_leaks(self):
        r={'a':self.train[0],'b':mix.TOTAL-self.train[0],
           'x':mix.expected_oracle(self.train[0],P)[0],
           'y':mix.expected_oracle(self.train[0],P)[1]}
        _,combined=diag._fractional_encoding(r,'T03')
        self.assertEqual(combined[12],0.)
        self.assertEqual(combined[13],0.)

    def test_magnitude_evaluation_floor_vs_true_relative(self):
        import torch
        metric=runner.magnitude_band_metrics([
            (torch.tensor([.1,.9,0.,150.]),torch.tensor([.14,.94,.04,148.]),
             torch.tensor([.2,.2,0.,150.]))])
        self.assertEqual(metric['7']['n'],3)
        self.assertEqual(metric['7']['accuracy_5pct_floor'],1.)
        self.assertEqual(metric['7']['strict_relative_accuracy_5pct_nonzero'],.5)
        self.assertEqual(metric['7']['nonzero_n'],2)
        self.assertEqual(metric['4']['n'],1) # 100<=150<1000

    def test_frozen_feature_vector_collision_detection(self):
        import torch
        split={tier:{'target':torch.tensor([.1,.2,.9,2.]),
                     'features':torch.tensor([[[1.,2.]],[[1.,2.]],[[3.,2.]],[[4.,2.]]]),'coarse':torch.zeros(4)}
               for tier in hd.TIERS}
        report=runner.frozen_feature_resolution(split)
        self.assertEqual(report['T03']['subunit_cases'],3)
        self.assertEqual(report['T03']['unique_frozen_vectors'],2)
        self.assertEqual(report['T03']['aliased_distinct_target_cases'],2)
        self.assertEqual(report['T03']['aliased_downstream_distinct_target_cases'],2)

    def test_frozen_feature_collision_but_distinct_coarse_is_not_downstream_collision(self):
        import torch
        split={tier:{'target':torch.tensor([.1,.2]),
                     'features':torch.tensor([[[1.,2.]],[[1.,2.]]]),
                     'coarse':torch.tensor([5.,6.])} for tier in hd.TIERS}
        result=runner.frozen_feature_resolution(split)['T00']
        self.assertEqual(result['aliased_distinct_target_cases'],2)
        self.assertEqual(result['aliased_downstream_distinct_target_cases'],0)

    def test_monitor_pending_no_crash_claim(self):
        with tempfile.TemporaryDirectory() as td:
            result=diag.monitor(self._args(Path(td)))
        self.assertEqual(result['status'],'warn')
        self.assertIn('No completed probe yet',str(result))

    @staticmethod
    def _args(path):
        return SimpleNamespace(run_dir=path,last_n=12,max_generalization_gap=.15,
                     max_small_gap=.25,allowed_regression=.02,collapse_limit=.20,gradient_ratio_limit=.25,
                     plateau_probes=3,min_improvement=.001)

    def test_monitor_valid_and_deteriorating_trend(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)
            for step,new in [(0,.61),(1000,.67),(2000,.63),(3000,.62),(4000,.58)]:
                case=fake_probe(step,head=.5,train=.99,new=new)
                (path/f'probe-step-{step:07d}.json').write_text(json.dumps(case))
            out=diag.monitor(self._args(path))
        names={x['name']:x['status'] for x in out['checks']}
        self.assertEqual(names['new_dev_improvement_recent'],'warn')
        self.assertEqual(names['original_integer_dev_retention'],'fail')
        self.assertEqual(names['expected_count_generalization_gap'],'warn')
        self.assertEqual(names['gaussian_bank_gradient_bounded'],'pass')
        self.assertIn('new_dev_below_one',out)
        self.assertEqual(len(out['history']),5)

    def test_monitor_missing_new_dev_blocks_good_news(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)
            (path/'probe-step-0000000.json').write_text(json.dumps(fake_probe(0,with_new=False)))
            out=diag.monitor(self._args(path))
        self.assertEqual(next(x for x in out['checks'] if x['name']=='independent_expected_count_new_dev')['status'],'fail')

    def test_monitor_malformed_probe_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td);(path/'probe-step-0001000.json').write_text('{bad')
            out=diag.monitor(self._args(path))
        self.assertEqual(out['status'],'fail')

    def test_monitor_zero_grad_fails_health_check(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)
            p=fake_probe(1000)
            p['chunk_mean_train_losses_and_gradients']['numeric_gradient_head_norm']=0.
            (path/'probe-step-0001000.json').write_text(json.dumps(p))
            out=diag.monitor(self._args(path))
        self.assertEqual(next(x for x in out['checks'] if x['name']=='numerical_head_gradient_alive')['status'],'warn')



class RetentionTrainingTests(unittest.TestCase):
    def test_mixed_sampler_contains_genuine_train_examples_from_both_sources(self):
        import torch
        frac={t:{'features':torch.full((8,2),1.),'coarse':torch.ones(8),
                 'target':torch.full((8,),.25)} for t in hd.TIERS}
        integer={t:{'features':torch.full((8,2),2.),'coarse':torch.full((8,),3.),
                    'target':torch.full((8,),1000.)} for t in hd.TIERS}
        sampler=runner.RetentionSampler(frac,integer,retention_fraction=.25,relative_max=2000000.)
        self.assertEqual(sampler.counts(32),(24,8))
        one=sampler.draw(10,2,32)
        two=sampler.draw(10,2,32)
        self.assertTrue(torch.equal(one['target'],two['target']))
        self.assertEqual(int((one['target']==.25).sum()),24)
        self.assertEqual(int((one['target']==1000.).sum()),8)
        self.assertEqual(len(one['features']),32)

    def test_promotion_rejects_original_forgetting_even_with_new_improvement(self):
        report={'joint':{'accuracy_5pct':.02,'small_accuracy_5pct':.1},
                'frozen_stage2':{'accuracy_5pct':.55,'small_accuracy_5pct':.11},
                'nested90_unseen_dev':{'joint':{'accuracy_5pct':.8},
                                      'frozen_stage2':{'accuracy_5pct':.07}}}
        gate=runner.promotion_gate(report,max_original_dev_drop=.02,required_new_dev_gain=0.)
        self.assertFalse(gate['promotable'])
        self.assertEqual(gate['reason'],'ORIGINAL_RETENTION_FAILED')
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(runner.save_best(Path(td),{'identity':{}},
                {**report,'promotion_gate':gate}),[])
            self.assertFalse((Path(td)/'best_checkpoints').exists())

    def test_promotion_requires_new_dev_better_than_stage2(self):
        report={'joint':{'accuracy_5pct':.54,'small_accuracy_5pct':.11},
                'frozen_stage2':{'accuracy_5pct':.55,'small_accuracy_5pct':.11},
                'nested90_unseen_dev':{'joint':{'accuracy_5pct':.06},
                                      'frozen_stage2':{'accuracy_5pct':.07}}}
        gate=runner.promotion_gate(report,max_original_dev_drop=.02,required_new_dev_gain=0.)
        self.assertFalse(gate['promotable'])
        self.assertEqual(gate['reason'],'NEW_DEV_GAIN_FAILED')
        report['nested90_unseen_dev']['joint']['accuracy_5pct']=.08
        self.assertTrue(runner.promotion_gate(report,max_original_dev_drop=.02,required_new_dev_gain=0.)['promotable'])
        del report['nested90_unseen_dev']
        self.assertFalse(runner.promotion_gate(report,max_original_dev_drop=.02,required_new_dev_gain=0.)['promotable'])

    def test_monitor_v2_collapse_is_a_failure(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)
            p=fake_probe(1000,head=.01,base=.55,new=.08)
            p['schema']=runner.SCHEMA+'.probe.v1'
            p['promotion_gate']={'promotable':False,'reason':'ORIGINAL_RETENTION_FAILED'}
            p['integer_replay_train_accuracy']=.50
            p['mixture_feasibility']['optimizer_streams']={'original_integer_replay_per_batch':8}
            p['nested90_unseen_dev']['per_bank']=[{'bank':j+1,'interior_n':8} for j in range(21)]
            (path/'probe-step-0001000.json').write_text(json.dumps(p))
            report=diag.monitor(DiagnosticTests._args(path))
        status={r['name']:r['status'] for r in report['checks']}
        self.assertEqual(status['catastrophic_original_integer_forgetting'],'fail')
        self.assertEqual(status['original_integer_dev_retention'],'fail')
        self.assertEqual(status['integer_train_replay_stream_present'],'pass')
        self.assertEqual(status['every_bank_has_interior_new_dev'],'pass')

if __name__=='__main__':unittest.main()

class TestSplitProtection(unittest.TestCase):
    def test_expected_runner_audit_avoids_test_rows(self):
        args=runner.parser().parse_args(['--mixture-audit-only'])
        args.mixture_fixture=Path('fixture');args.teacher=Path('teacher')
        calls=[]
        def old_counts(teacher,*,splits=('dev','test')):
            calls.append(splits)
            if 'test' in splits:raise AssertionError('TEST_SPLIT_READ')
            return set()
        with mock.patch.object(runner.mixture,'certified_payoffs',return_value=(P,{'sha256':'abc'})),\
             mock.patch.object(runner.mixture,'original_counts',side_effect=old_counts),\
             mock.patch.object(runner.mixture,'plan',return_value=({'feasible':True},[],[])):\
            runner.audit_only(args)
        self.assertEqual(calls,[('dev',)])
