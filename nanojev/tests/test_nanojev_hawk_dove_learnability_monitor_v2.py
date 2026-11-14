"""Regression: 21-bank monitor must not mistake zero collapse for learning."""
import json
import math
import tempfile
import unittest
from pathlib import Path

import torch

from tools import nanojev_hawk_dove_gaussian_joint_decoder_21_learnability as runner
from tools import nanojev_hawk_dove_learnability_monitor as monitor


def case(step, train, dev, strict, sub_asinh, zero_asinh=None, whole_zero=None, clip=None, arm='decoder-only'):
    bands={str(i):{'n':1,'mean_absolute_asinh_error':3.,
                   'zero_predictor_mean_absolute_asinh_error':2.} for i in range(8)}
    bands['7']={'n':8,'mean_absolute_asinh_error':sub_asinh,
                'zero_predictor_mean_absolute_asinh_error':zero_asinh,
                'strict_relative_accuracy_5pct_nonzero':strict,
                'zero_predictor_accuracy_5pct_floor':.5,
                'accuracy_5pct_floor':.1}
    losses={'gradient_clip_fraction':clip,'gradient_clip_multiplier':.05,
            'gradient_global_norm_before_clip':25.,'decoder_delta_abs':6.}
    return {'schema':runner.SCHEMA+'.probe.v1','step':step,
            'learnability':{'arm':arm,'fractional_train_mae_asinh':train,
                'fractional_new_dev_mae_asinh':dev,
                'fractional_new_dev_zero_mae_asinh':whole_zero},
            'joint':{'accuracy_5pct':.1,'small_accuracy_5pct':.03},
            'nested90_unseen_dev':{'joint':{'accuracy_5pct':.01},'magnitude_bands':bands},
            'magnitude_bands_train':{'7':{'strict_relative_accuracy_5pct_nonzero':strict}},
            'chunk_mean_train_losses_and_gradients':losses if clip is not None else None}


def write_probe(root,p):
    (root/f'probe-step-{p["step"]:07d}.json').write_text(json.dumps(p))

class MonitorV2Tests(unittest.TestCase):
    def test_runner_emits_true_transformed_zero_and_frozen_baselines(self):
        output=runner.magnitude_band_metrics([(
            torch.tensor([.125,-.875, 50000.]),
            torch.tensor([.12,-.87, 48000.]),
            torch.tensor([30.,29.,45000.]))])
        sub=output['7']
        self.assertEqual(sub['n'],2)
        self.assertAlmostEqual(sub['zero_predictor_mean_absolute_asinh_error'],
             (abs(math.asinh(.125))+abs(math.asinh(-.875)))/2)
        self.assertLess(sub['mean_absolute_asinh_error'],sub['zero_predictor_mean_absolute_asinh_error'])
        self.assertGreater(sub['frozen_mean_absolute_asinh_error'],sub['mean_absolute_asinh_error'])
        self.assertEqual(sub['strict_relative_accuracy_5pct_nonzero'],1.)

    def test_decreasing_error_without_beating_zero_is_not_learnability(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            write_probe(root,case(0,10.,10.,0.,.5,.4,8.))
            write_probe(root,case(250,8.,8.,.01,.4,.4,8.,clip=1.))
            write_probe(root,case(500,5.,6.,.02,.39,.4,8.,clip=1.))
            write_probe(root,case(750,4.,5.,.03,.42,.4,8.,clip=.996))
            report=monitor.review(root)
            self.assertEqual(report['assessment'],'TRAIN_AND_HELD_OUT_ERROR_DECREASING')
            self.assertEqual(report['learnability_verdict'],
                            'HELD_OUT_ASINH_IMPROVING_BUT_NUMERICAL_LEARNING_NOT_ESTABLISHED')
            self.assertIn('PERSISTENT_GRADIENT_CLIPPING',report['warnings'])
            self.assertAlmostEqual(report['latest']['new_dev_subunit_gain_over_zero_asinh_mae'],-.02)
            self.assertEqual(report['best_held_out_step'],750)
            self.assertEqual(report['latest']['mean_clip_multiplier'],.05)
            self.assertFalse(report['test_split_read'])

    def test_both_zero_baselines_beaten_reports_learning_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            write_probe(root,case(0,10.,10.,0.,.5,.4,8.))
            write_probe(root,case(300,4.,6.,.2,.2,.4,8.,clip=.2))
            report=monitor.review(root)
            self.assertEqual(report['learnability_verdict'],'EVIDENCE_OF_HELD_OUT_NUMERICAL_LEARNING')
            self.assertIn('SUBUNIT_ASINH_BETTER_THAN_ZERO',report['learning_signals'])
            self.assertIn('STRICT_NONZERO_SUBUNIT_ACCURACY_IMPROVED',report['learning_signals'])

    def test_old_probes_remain_valid_and_missing_baselines_not_invented(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            p=case(0,10.,10.,0.,.5,None,None)
            p['learnability'].pop('fractional_new_dev_zero_mae_asinh')
            write_probe(root,p)
            p=case(250,4.,5.,.1,.2,None,None,clip=1.)
            p['learnability'].pop('fractional_new_dev_zero_mae_asinh')
            write_probe(root,p)
            report=monitor.review(root)
            self.assertEqual(report['learnability_verdict'],'ZERO_BASELINE_EVIDENCE_MISSING')
            self.assertIsNone(report['zero_baseline_gap_latest']['subunit_new_dev_asinh'])
            self.assertIn('ZERO_ASINH_BASELINE_NOT_MEASURED_IN_HISTORICAL_PROBE',report['warnings'])

    def test_old_main_test_not_broken(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            write_probe(root,case(0,8.,8.,.01,.5,.3,8.))
            write_probe(root,case(1000,4.,7.,.01,.5,.3,8.,clip=.3))
            self.assertEqual(monitor.review(root)['assessment'],
                             'TRAIN_AND_HELD_OUT_ERROR_DECREASING')
            write_probe(root,case(2000,2.,9.,.01,.5,.3,8.,clip=.3))
            self.assertEqual(monitor.review(root)['assessment'],
                             'TRAIN_FIT_WITHOUT_HELD_OUT_GAIN')

    def test_pairwise_fails_closed_if_missing_config_or_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            a,b=Path(tmp)/'a',Path(tmp)/'b'
            a.mkdir();b.mkdir()
            for root,arm in ((a,'decoder-only'),(b,'joint')):
                write_probe(root,case(0,10.,10.,0.,.5,.4,8.,arm=arm))
                write_probe(root,case(250,5.,6. if arm=='decoder-only' else 5.,.1,.3,.4,8.,arm=arm))
            self.assertEqual(monitor.compare([a,b])['ab_comparison']['reason'],'MISSING_CONFIG_IDENTITY')
            config={k:'same' for k in monitor.CRITICAL_CONFIG}
            for root,arm in ((a,'decoder-only'),(b,'joint')):
                (root/'config.json').write_text(json.dumps(dict(config,experiment_arm=arm)))
            report=monitor.compare([a,b])
            self.assertEqual(report['ab_comparison']['status'],'COMPARABLE')
            self.assertEqual(report['ab_comparison']['lower_held_out_asinh_mae_arm'],'joint')
            cfg=dict(config,experiment_arm='joint',lr='.2')
            (b/'config.json').write_text(json.dumps(cfg))
            report=monitor.compare([a,b])
            self.assertEqual(report['ab_comparison']['reason'],'DIFFERENT_TRAINING_OR_DATA_IDENTITY')

    def test_eight_digit_steps_sort_numerically_and_history_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            write_probe(root,case(0,10.,10.,0.,.5,.4,8.))
            write_probe(root,case(9000000,7.,7.,.01,.3,.4,8.,clip=.1))
            write_probe(root,case(10000000,5.,5.,.02,.2,.4,8.,clip=.1))
            report=monitor.review(root,history_limit=1)
            self.assertEqual(report['latest']['step'],10000000)
            self.assertEqual(report['best_held_out_step'],10000000)
            self.assertEqual(report['history_length_total'],3)
            self.assertTrue(report['history_truncated'])
            self.assertEqual(len(report['history']),1)
            self.assertEqual(report['first']['step'],0)

    def test_step_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp)/'probe-step-0000001.json').write_text(json.dumps(case(2,1.,1.,0.,0.4)))
            with self.assertRaisesRegex(ValueError,'FILENAME_STEP_MISMATCH'):
                monitor.review(tmp)

if __name__=='__main__':unittest.main()
