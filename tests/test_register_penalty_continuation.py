"""Regression tests for sparse register-head penalty continuation."""
from pathlib import Path
from unittest.mock import patch
import sys
import unittest
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import nanojev_register_template_screen as reg

class RegisterPenaltyContinuationTests(unittest.TestCase):
    def test_schedule_filters_target_and_weaker_levels(self):
        self.assertEqual(reg.continuation_schedule('0.03,0.015,0.005',0.002),(.03,.015,.005))
        self.assertEqual(reg.continuation_schedule('0.03,0.015,0.005',0.01),(.03,.015))
        self.assertEqual(reg.continuation_schedule('0.03,0.015,0.005',0.05),())
        self.assertEqual(reg.continuation_schedule('none',0.002),())
        for bad in ('0.015,0.03','0.03,0.03','nan','2','0'):
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                reg.continuation_schedule(bad,.002)

    def test_new_head_learns_full_penalty_path_but_returns_final_fit_only(self):
        observed=[]
        def fake_fit(factory,name,subset,response):
            observed.append((float(factory.ratio),name))
            return np.full(len(subset),float(factory.ratio))
        state={'template':'xx','position':1,'head_count':0,'template_started':reg.time.perf_counter()}
        factory=reg.ProgressFastHeadFactory(np.ones((8,3),dtype=np.float32),
            progress=state, warmup_ratios=(.03,.015,.005),progress_every=10)
        factory.set_ratio(.002)
        with patch.object(reg.fast.FastHeadFactory,'fit',fake_fit), \
             patch.object(reg.base,'emit') as emitter:
            fitted=factory.fit('zero',np.ones(8,dtype=bool),np.arange(8))
        self.assertEqual([round(v[0],4) for v in observed],[.03,.015,.005,.002])
        self.assertTrue(np.all(fitted==.002))
        self.assertEqual(factory.ratio,.002)
        self.assertEqual(factory.continuation_fit_calls,3)
        self.assertEqual(state['head_count'],1)
        self.assertEqual(emitter.call_args.args[0],'register_analysis_head_progress')

    def test_actual_sparse_fit_matches_cold_lasso_objective(self):
        rng=np.random.default_rng(147)
        x=rng.normal(size=(120,22)).astype(np.float32)
        x[:,13]=x[:,2]+0.015*rng.normal(size=120)
        target=2*x[:,2]-.9*x[:,4]+.2*rng.normal(size=120)
        z,_,_,_=reg.sparse.standardize_design(x)
        mask=np.ones(len(target),dtype=bool)
        cold=reg.fast.FastHeadFactory(z,max_iter=5000,retry_multiplier=3,kkt_iters=12)
        cold.set_ratio(.04)
        expected=cold.fit('reg',mask,target)
        warm=reg.ProgressFastHeadFactory(z, warmup_ratios=(.2,.1),
            max_iter=5000,retry_multiplier=3,kkt_iters=12)
        warm.set_ratio(.04)
        actual=warm.fit('reg',mask,target)
        self.assertTrue(np.allclose(actual,expected,rtol=.002,atol=.003))
        self.assertEqual(warm.ratio,.04)
        self.assertTrue(warm.heads['reg']['solver_converged'])
        self.assertTrue(warm.heads['reg']['kkt_passed'])
        self.assertEqual(warm.heads['reg']['alpha_ratio'],.04)
        self.assertEqual(warm.continuation_fit_calls,2)

    def test_warmup_solver_failure_is_identified_and_target_restored(self):
        state={'template':'xx','position':1,'head_count':0,'template_started':reg.time.perf_counter()}
        factory=reg.ProgressFastHeadFactory(np.ones((8,3),dtype=np.float32),
            progress=state, warmup_ratios=(.03,.015,.005))
        factory.set_ratio(.002)
        def fake_fit(factory,name,subset,response):
            if factory.ratio==.015:
                raise reg.sparse.SparseFitFailure('SPARSE_HEAD_SOLVER_NOT_CONVERGED',
                   active_features=100,solver_last_dual_gap=.07)
            return np.zeros(len(subset))
        with patch.object(reg.fast.FastHeadFactory,'fit',fake_fit):
            with self.assertRaises(reg.sparse.SparseFitFailure) as out:
                factory.fit('zero',np.ones(8,dtype=bool),np.arange(8))
        self.assertEqual(out.exception.diagnostics['requested_alpha_ratio'],.002)
        self.assertEqual(out.exception.diagnostics['continuation_alpha_ratio'],.015)
        self.assertEqual(out.exception.diagnostics['active_features'],100)
        self.assertEqual(factory.ratio,.002)
        self.assertEqual(state['head_count'],0)

if __name__=='__main__':unittest.main()
