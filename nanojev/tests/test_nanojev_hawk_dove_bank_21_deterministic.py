"""Tests of parameter-free, read-only 21-bank reconstruction and monitor opt-in."""
import contextlib
import io
import json
import math
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

import torch
from tools import nanojev_hawk_dove_gaussian_bank_21_deterministic as deterministic
from tools import nanojev_hawk_dove_gaussian_bank_21_monitor as monitor
from tools import nanojev_hawk_dove_gaussian_bank_21_supervision as training
from tools.nanojev_hawk_dove_gaussian_zero_overflow_16 import ZeroOverflowBank
from tools.nanojev_hawk_dove_million import TAGS


class DeterministicBankTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)
        cls.banks=[ZeroOverflowBank(3,radius=2_000_000*2**-i,registers=129)
                   for i in range(21)]

    def oracle(self,x):
        return [bank.supervised_distribution(torch.tensor([float(x)]))[0].tolist()
                for bank in self.banks]

    def test_ideal_oracle_reconstruction_across_scales(self):
        for x in [1.1,5.,-8.,20.,-70.,200.,-500.,700.]:
            with self.subTest(x=x):
                e=deterministic.estimate_x(self.banks,self.oracle(x))
                self.assertEqual(e['status'],'ESTIMATED')
                self.assertLess(abs(e['x']-x),.1)
                self.assertGreater(e['accepted_banks'],0)
                self.assertGreater(e['uncertainty'],0)

    def test_no_usable_gaussian_must_abstain(self):
        flat=[[.01]+[.98/129]*129+[.01] for _ in self.banks]
        result=deterministic.estimate_x(self.banks,flat)
        self.assertIsNone(result['x'])
        self.assertEqual(result['status'],'NO_USABLE_INTERIOR')
        self.assertEqual(result['accepted_banks'],0)

    def test_overflow_conflict_is_not_hidden(self):
        probs=self.oracle(5.)
        probs[19]=[1.]+[0.]*130  # at radius 3.8: wrong LEFT vote
        probs[20]=[0.]*130+[1.]  # at radius 1.9: RIGHT vote
        result=deterministic.estimate_x(self.banks,probs)
        self.assertEqual(result['status'],'OVERFLOW_CONFLICT')
        self.assertIsNone(result['x'])
        self.assertTrue(result['overflow_bounds']['contradiction'])

    def test_bad_single_overflow_vote_can_be_audited_without_erasing_failure(self):
        x=-65.75
        probabilities=self.oracle(x)
        # Bank 13 should be interior for X=-65.75 (radius 488.28125).
        # A confidently wrong LHS vote must remain visible as an error.
        probabilities[12]=[1.]+[0.]*130
        strict=deterministic.estimate_x(self.banks,probabilities)
        shadow=deterministic.estimate_x(
            self.banks,probabilities,overflow_policy='audit_ignore_hard_bounds')
        self.assertEqual(strict['status'],'OVERFLOW_CONFLICT')
        self.assertIsNone(strict['x'])
        self.assertAlmostEqual(shadow['x'],x,places=3)
        self.assertEqual(shadow['overflow_bounds']['upper'],-488.28125)
        self.assertEqual(shadow['overflow_policy'],'audit_ignore_hard_bounds')
        # No changes are made to the underlying distributions.
        self.assertEqual(probabilities[12][0],1.)

    def test_unknown_gate_policy_fails_closed(self):
        with self.assertRaisesRegex(ValueError,'UNKNOWN_OVERFLOW_POLICY'):
            deterministic.estimate_x(self.banks,self.oracle(5.),overflow_policy='oracle')

    def test_payoff_transform_is_algebraic_not_oracle_label(self):
        p={TAGS[0]:Decimal('4'),TAGS[1]:Decimal('1'),
           TAGS[2]:Decimal('2'),TAGS[3]:Decimal('0')}
        transform=deterministic.fixture_transform(p)
        output=deterministic.predict(self.banks,self.oracle(500.),transform)
        self.assertAlmostEqual(output['x'],500.,places=3)
        expected_y=(500.-1_000_000)*2/3
        self.assertAlmostEqual(output['y'],expected_y,places=3)
        self.assertEqual(output['z'],'HAWK')
        self.assertAlmostEqual(output['z_max'],500.,places=3)
        self.assertEqual(output['y_origin'],
            'derived_from_predicted_X_and_fixed_payoff_fixture_not_Y_banks')

    def test_zero_slope_refuses_to_invent_y(self):
        p={TAGS[0]:Decimal('1'),TAGS[1]:Decimal('1'),
           TAGS[2]:Decimal('2'),TAGS[3]:Decimal('3')}
        with self.assertRaisesRegex(ValueError,'ZERO_SLOPE'):
            deterministic.fixture_transform(p)

    def test_oracle_truth_only_used_by_score_not_predict(self):
        transform={'slope':1.,'x_offset':0.,'y_offset':0.}
        p=deterministic.predict(self.banks,self.oracle(20.),transform)
        self.assertIsNotNone(p['x'])
        a={'x':'20','y':'20','z':'TIE','tier':'T00','instance':'a'}
        b={'x':'5','y':'7','z':'DOVE','tier':'T00','instance':'a'}
        e1=deterministic._truth_score(p,a)
        e2=deterministic._truth_score(p,b)
        self.assertEqual(e1['x'],e2['x'])
        self.assertNotEqual(e1['errors']['x_absolute'],e2['errors']['x_absolute'])
        self.assertEqual(len(e1['bank_gate_trace']),21)

    def test_held_out_summary_counts_abstentions(self):
        sample=[{'status':'NO_USABLE_INTERIOR','errors':None},
           {'status':'ESTIMATED','errors':{'x_absolute':1.,'y_absolute':2.,
                'x_relative':.01,'y_relative':.02,'z_max_absolute':1.,'z_correct':True,'x_within_uncertainty_proxy':True},
            'confidence_score_heuristic':.1}]
        report=deterministic.summarize_cases(sample)
        self.assertEqual(report['abstained'],1)
        self.assertEqual(report['estimated'],1)
        self.assertEqual(report['coverage_fraction'],.5)
        self.assertEqual(report['x_accuracy_5pct'],1.)
        self.assertEqual(report['x_success_rate_all_cases_5pct'],.5)

    def test_monitor_defaults_do_not_load_model(self):
        with tempfile.TemporaryDirectory() as temp:
            fake={'status':'OK','schema':monitor.SCHEMA}
            with mock.patch.object(monitor,'review',return_value=fake),mock.patch(
                    'tools.nanojev_hawk_dove_gaussian_bank_21_deterministic.evaluate_checkpoint',
                    return_value={'checkpoint_step':20}) as estimate:
                out=io.StringIO()
                with contextlib.redirect_stdout(out):
                    monitor.main(['--run-dir',temp])
                self.assertNotIn('deterministic_test',json.loads(out.getvalue()))
                estimate.assert_not_called()
                out=io.StringIO()
                with contextlib.redirect_stdout(out):
                    monitor.main(['--run-dir',temp,'--deterministic-test',
                                  '--device','cpu','--dtype','float32',
                                  '--samples-per-band','2'])
                self.assertEqual(json.loads(out.getvalue())['deterministic_test']['checkpoint_step'],20)
                self.assertEqual(estimate.call_args.kwargs['per_band'],2)

    def test_validation_rejects_false_probability_and_missing_banks(self):
        with self.assertRaisesRegex(ValueError,'BAD_BANK_PROBABILITIES'):
            deterministic._bank_evidence(self.banks[0],[float('nan')]*131)
        with self.assertRaisesRegex(ValueError,'WRONG_BANK_COUNT'):
            deterministic.estimate_x(self.banks[:2],self.oracle(4.)[:2])

    def test_unsupported_region_is_not_treated_as_correct(self):
        p=self.oracle(100.)
        result=deterministic.estimate_x(self.banks,p)
        self.assertGreater(sum(r['region_gate']=='rhs' for r in result['banks']),0)
        self.assertGreater(sum(r['region_gate']=='interior' for r in result['banks']),0)
        self.assertLess(result['accepted_banks'],21)

    def test_center_shape_badness_stays_visible_even_if_result_unavailable(self):
        odds=self.oracle(5.)
        odds[16]=[.01]+[.98/129]*129+[.01]
        result=deterministic.estimate_x(self.banks,odds)
        self.assertFalse(result['banks'][16]['accepted'])
        self.assertGreater(result['banks'][16]['shape_kl'],6.)

    def test_read_only_checkpoint_to_dev_scoring_path(self):
        from tools import nanojev_hawk_dove_gaussian_joint_decoder_21_focus_1k_stable as stable
        from tools import nanojev_hawk_dove_nested90_expected as expected
        from tools import nanojev_hawk_dove_coarse_to_fine_gaussian as fine
        from tools import nanojev_hawk_dove_gaussian_register_fork as fork
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);run=root/'run';run.mkdir()
            source=root/'source';source.write_bytes(b's')
            teacher=root/'teacher';teacher.write_bytes(b't')
            fixture=root/'fixture';fixture.write_bytes(b'f')
            def put(name,obj):
                path=run/name
                torch.save(obj,path)
                return __import__('hashlib').sha256(path.read_bytes()).hexdigest()
            stages={'stage2_snapshot.pt':put('stage2_snapshot.pt',{'model':'stage2'}),
                    'parent_snapshot.pt':put('parent_snapshot.pt',{'model':'original'}),
                    'donor_snapshot.pt':put('donor_snapshot.pt',{'model':'donor'})}
            ident={'schema':training.SCHEMA,'mixture_fixture_path':str(fixture),
                'mixture_fixture_sha256':'fixture_test',
                'source_sha256':fork.fingerprint(source),
                'teacher_sha256':fork.fingerprint(teacher),
                'original_bridge_arm':'continuous',
                'mixture_train_per_tier':3,'mixture_new_dev_per_tier':3,
                'mixture_generated_train_rows_sha256':'train_test',
                'mixture_generated_dev_rows_sha256':'dev_test',
                'hidden':16,'relative_max':2000000.,'seed':7,
                'stage2_sha256':stages['stage2_snapshot.pt'],
                'original_parent_sha256':stages['parent_snapshot.pt'],
                'donor_sha256':stages['donor_snapshot.pt']}
            (run/'config.json').write_text(json.dumps(ident))
            sys=stable.JointGaussianSystem(5,fine.LocalRefiner(5,kind='gaussian',registers=129),hidden=16)
            # Oracle Gaussian of X=50 is a stationary *synthetic bank predictor*.
            for bank in sys.cascade.banks.values():
                goal=bank.supervised_distribution(torch.tensor([50.]))[0]
                with torch.no_grad():
                    bank.readout.weight.zero_()
                    bank.readout.bias.copy_(goal.clamp_min(1e-30).log())
            put('latest.pt',{'schema':training.SCHEMA,'identity':ident,'step':999,
                'bank_states':{str(k):sys.cascade.banks[str(k)].state_dict()
                               for k in training.STAGES}})
            rows={tier:[{'tier':tier,'instance':f'dev-{tier}-{x}',
                         'x':str(x),'y':str(x/2),'z':'HAWK'}
                        for x in (5,50,500)] for tier in training.TIERS}
            dataset={tier:{'features':torch.zeros(3,5),'coarse':torch.zeros(3),
                           'target':torch.tensor([5.,50.,500.])} for tier in training.TIERS}
            def build(args):
                args._mixture_train_sha256='train_test'
                args._mixture_dev_sha256='dev_test'
                return {},rows,rows
            class Cache:
                def __init__(self,*args,**kwargs):pass
            fixture_payoffs={TAGS[0]:Decimal('2'),TAGS[1]:Decimal('0'),
                             TAGS[2]:Decimal('1'),TAGS[3]:Decimal('0')}
            original=list(sorted(run.iterdir()))
            with mock.patch.object(stable,'build_mixture',side_effect=build), \
                 mock.patch.object(expected,'certified_payoffs',return_value=(fixture_payoffs,{'sha256':'fixture_test'})), \
                 mock.patch.object(fork.ex,'load_current',return_value={'device':'cpu','config':{'max_prompt_tokens':30}}), \
                 mock.patch.object(fine,'init_frozen',return_value=(None,None)), \
                 mock.patch.object(fork.cached,'FrozenCaptureCache',Cache), \
                 mock.patch.object(expected,'load_features',return_value=dataset), \
                 mock.patch.object(stable,'construct_system',return_value=sys):
                out=deterministic.evaluate_checkpoint(run,device='cpu',dtype='float32',
                    per_band=1,source=source,teacher=teacher,fixture=fixture,show_cases=3)
                audited=deterministic.evaluate_checkpoint(run,device='cpu',dtype='float32',
                    per_band=1,source=source,teacher=teacher,fixture=fixture,show_cases=3,
                    gate_ablation=True)
            self.assertNotIn('gate_ablation',out)
            self.assertEqual(audited['overall'],out['overall'])
            audit=audited['gate_ablation']
            self.assertEqual(audit['paired_cases'],27)
            self.assertEqual(audit['training_updates'],0)
            self.assertEqual(audit['predictions_changed'] >= 0,True)
            self.assertEqual(audit['shadow_absolute_error_improved']+
                             audit['shadow_absolute_error_worsened']+
                             audit['shadow_error_tied'],27)
            self.assertEqual(out['checkpoint_step'],999)
            self.assertEqual(out['gradient_updates'],0)
            self.assertEqual(out['additional_trainable_parameters'],0)
            self.assertEqual(out['overall']['total'],27)
            self.assertGreater(out['overall']['estimated'],0)
            self.assertLess(out['overall']['x_accuracy_5pct'],1)
            self.assertEqual(list(sorted(run.iterdir())),original)
            self.assertEqual(out['by_x_band']['1-10']['total'],9)
            self.assertIn('upstream_visibility_warning',out)


if __name__=='__main__':unittest.main()
