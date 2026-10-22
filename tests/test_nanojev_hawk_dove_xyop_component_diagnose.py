"""CPU-only guardrails for read-only XYOP V3 component diagnosis."""
import unittest
from unittest import mock

import torch

from tools import nanojev_hawk_dove_xyop_component_diagnose as diag
from tools import nanojev_hawk_dove_xyop_mask_train as xyop
from tools import nanojev_hawk_dove_scientific_train as science
from tools import nanojev_altreg_math_v3_decoder_experiment as v3
from tools import nanojev_game_transform_experiment as ex
from tools import nanojev_game_math_curriculum_v10_runtime as rt


def logits(numbers, exp_count=8):
    """Predict exact sign/exponent/mantissa for two slots, while tests modify them."""
    xy=torch.tensor(numbers,dtype=torch.float32)
    s,e,f,_=v3.target_parts(xy,exp_count-1,'cpu')
    ss=torch.full((len(numbers),2,3),-20.)
    ee=torch.full((len(numbers),2,exp_count),-20.)
    for i in range(len(numbers)):
        for j in range(2):
            ss[i,j,s[i,j]]=20.
            ee[i,j,e[i,j]]=20.
    return {'sign':ss,'exponent':ee,'fraction':f}


class ComponentsTests(unittest.TestCase):
    def test_hidden_x_only_excludes_revealed_y(self):
        targets=[(200000.,300000.)]
        out=logits(targets)
        out['sign'][0,1,:]=torch.tensor([20.,-20.,-20.])
        pred=v3.compose_logits(out['sign'],out['exponent'],out['fraction'],10_000_000).tolist()
        rows=diag.decode_component_rows(out,pred,targets,['011'],[1.],['a/T05/03'],[3],10_000_000)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['slot'],'x')
        self.assertTrue(rows[0]['within_5pct'])
        self.assertTrue(rows[0]['sign_correct'])
        self.assertTrue(rows[0]['exponent_correct'])
        self.assertAlmostEqual(diag.summarize(rows)['within_5pct'],1.)
        self.assertEqual(rows[0]['case_id'],'a/T05/03')

    def test_sqlite_text_targets_decode_as_numeric_values(self):
        # Teacher DB read_rows returns decimal *strings*, not Python floats.
        targets=[('200000.0', '-300000.0'), ('-400000.0', '225000.0')]
        expected=[(float(x), float(y)) for x,y in targets]
        out=logits(expected)
        pred=v3.compose_logits(out['sign'],out['exponent'],out['fraction'],10_000_000).tolist()
        rows=diag.decode_component_rows(out,pred,targets,['011','011'],
                                        [1.,1.],['a/T05/01','b/T05/01'],[1,1],10_000_000)
        self.assertEqual([r['true'] for r in rows],[200000.,-400000.])
        self.assertTrue(all(r['within_5pct'] for r in rows))
        self.assertTrue(all(r['sign_correct'] for r in rows))
        self.assertAlmostEqual(diag.summarize(rows)['within_5pct'],1.)

    def test_malformed_teacher_numeric_targets_fail_closed(self):
        out=logits([(200000.,300000.)])
        pred=v3.compose_logits(out['sign'],out['exponent'],out['fraction'],10_000_000).tolist()
        for targets in [[('bad','300000')],[('NaN','300000')],[('200000',)]]:
            with self.subTest(targets=targets), self.assertRaisesRegex(ValueError,'BAD_NUMERIC_TARGET'):
                diag.decode_component_rows(out,pred,targets,['011'],[1.],['a'],[0],10_000_000)

    def test_full_tier_trace_accepts_string_teacher_rows(self):
        row={'case_id':'dev/T02/00','tier':'T02','mask':0,'prompt':'THE PROMPT',
             'x':'-200000.0','y':'300000.0','full_count':0,'z':'DOVE'}
        out=logits([(-200000.,300000.)])
        def mocked_advancement(active_model, rows):
            got=science.predict(active_model,[rows[0]['prompt']])[0]
            self.assertAlmostEqual(got[0],float(row['x']),delta=.1)
            return {'joint_5pct':1.,'x_5pct':1.,'y_5pct':None}
        with mock.patch.object(ex,'forward',return_value=(out,None)), \
             mock.patch.object(science,'eval_tier',side_effect=mocked_advancement), \
             mock.patch.object(rt,'token_eligible',return_value=True):
            report=diag.diagnose_tier({'v3':v3},[row],xyop.parse_mixes(['*:*=011:1']),7,10_000_000)
        self.assertEqual(report['hidden']['x']['n'],1)
        self.assertEqual(report['hidden']['x']['within_5pct'],1.)

    def test_wrong_exponent_is_identified_and_oracle_rescues(self):
        target=[(-200000.,300000.)]
        out=logits(target)
        out['exponent'][0,0].fill_(-20)
        out['exponent'][0,0,6]=20.
        pred=v3.compose_logits(out['sign'],out['exponent'],out['fraction'],10_000_000).tolist()
        result=diag.decode_component_rows(out,pred,target,['011'],[1.],['case'],[0],10_000_000)[0]
        self.assertFalse(result['within_5pct'])
        self.assertTrue(result['sign_correct'])
        self.assertFalse(result['exponent_correct'])
        self.assertAlmostEqual(result['mantissa_abs_error'],0.)
        self.assertTrue(result['oracle_exponent_within_5pct'])
        self.assertFalse(result['oracle_sign_within_5pct'])

    def test_wrong_sign_and_wrong_mantissa_are_separable(self):
        target=[(-200000.,300000.),(200000.,300000.)]
        out=logits(target)
        out['sign'][0,0,:]=torch.tensor([-20.,-20.,20.])
        out['fraction'][1,0]=.8
        pred=v3.compose_logits(out['sign'],out['exponent'],out['fraction'],10_000_000).tolist()
        rows=diag.decode_component_rows(out,pred,target,['011','011'],[1.,1.],['s','m'],[0,1],10_000_000)
        self.assertTrue(rows[0]['oracle_sign_within_5pct'])
        self.assertTrue(rows[1]['oracle_mantissa_within_5pct'])
        self.assertFalse(rows[0]['sign_correct'])
        self.assertTrue(rows[1]['sign_correct'])
        self.assertTrue(rows[1]['exponent_correct'])
        self.assertGreater(rows[1]['mantissa_abs_error'],.49)

    def test_zero_exponent_and_mantissa_are_not_scored(self):
        targets=[(0.,123000.)]
        out=logits(targets)
        pred=v3.compose_logits(out['sign'],out['exponent'],out['fraction'],10_000_000).tolist()
        r=diag.decode_component_rows(out,pred,targets,['011'],[1.],['zero'],[0],10_000_000)[0]
        self.assertFalse(r['exponent_applicable'])
        self.assertIsNone(r['true_exponent'])
        self.assertIsNone(r['mantissa_abs_error'])
        summary=diag.summarize([r])
        self.assertIsNone(summary['exponent_accuracy_nonzero'])
        self.assertIsNone(summary['mantissa_mae_log10_nonzero'])

    def test_weighted_statistics_and_masked_fields(self):
        targets=[(100000.,1.),(100000.,1.)]
        out=logits(targets)
        out['sign'][1,0]=torch.tensor([20.,-20.,-20.])
        pred=v3.compose_logits(out['sign'],out['exponent'],out['fraction'],10_000_000).tolist()
        r=diag.decode_component_rows(out,pred,targets,['011','011'],[3.,1.],['a','b'],[0,0],10_000_000)
        summary=diag.summarize(r)
        self.assertAlmostEqual(summary['within_5pct'],.75)
        self.assertAlmostEqual(summary['sign_accuracy'],.75)
        self.assertEqual(summary['n'],2)
        self.assertEqual(summary['weighted_n'],4.)

    def test_mixture_matched_scoring_parity_and_trace_restore(self):
        row={'case_id':'dev/T02/00','tier':'T02','mask':0,'prompt':'THE PROMPT',
             'x':-200000.,'y':300000.,'full_count':0,'z':'DOVE'}
        out=logits([(row['x'],row['y'])])
        model={'v3':v3}
        original_predict=science.predict
        def mocked_advancement(active_model, rows):
            got=science.predict(active_model,[row['prompt']])[0]
            self.assertAlmostEqual(got[0],row['x'],delta=.1)
            return {'joint_5pct':1.,'x_5pct':1.,'y_5pct':None}
        with mock.patch.object(ex,'forward',return_value=(out,None)), \
             mock.patch.object(science,'eval_tier',side_effect=mocked_advancement), \
             mock.patch.object(rt,'token_eligible',return_value=True):
            report=diag.diagnose_tier(model,[row],xyop.parse_mixes(['*:*=011:1']),7,10_000_000)
        self.assertIs(science.predict,original_predict)
        self.assertEqual(report['hidden']['x']['n'],1)
        self.assertEqual(report['hidden']['y']['n'],0)
        self.assertAlmostEqual(report['hidden']['x']['within_5pct'],1.)

    def test_component_parity_detects_wrong_evaluator(self):
        row={'case_id':'dev/T02/00','tier':'T02','mask':0,'prompt':'P',
             'x':200000.,'y':300000.,'full_count':0,'z':'HAWK'}
        model={'v3':v3}
        def bad_evaluator(model, rows):
            science.predict(model,[rows[0]['prompt']])
            return {'x_5pct':0.,'y_5pct':None}
        with mock.patch.object(ex,'forward',return_value=(logits([(200000.,300000.)]),None)), \
             mock.patch.object(science,'eval_tier',side_effect=bad_evaluator), \
             mock.patch.object(rt,'token_eligible',return_value=True):
            with self.assertRaisesRegex(RuntimeError,'SCORING_PARITY'):
                diag.diagnose_tier(model,[row],xyop.parse_mixes(['*:*=011:1']),7,10_000_000)

    def test_bad_tiers_and_mixtures_rejected(self):
        for x in ('T04,T04','T09','T01,',''):
            with self.assertRaises(ValueError): diag.tiers_arg(x)
        self.assertEqual(diag.tiers_arg('T08,T02'),('T02','T08'))
        with self.assertRaises(ValueError):
            diag.decode_component_rows(logits([(1.,2.)]),[(1.,2.)],[(1.,2.)],
                                       ['111'],[1.],['x'],[0],10_000_000)

if __name__=='__main__':unittest.main()
