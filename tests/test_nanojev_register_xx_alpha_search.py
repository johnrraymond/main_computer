"""Offline checks for the XXXXXX-only halving / bisection LASSO search."""
from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import nanojev_register_xx_alpha_search as xx


class TestXXAlphaSearch(unittest.TestCase):
    def test_halves_only_after_success(self):
        gen=xx.make_schedule(.01,.0001,4,3)
        self.assertEqual(next(gen)[:2],(.005,'halve'))
        self.assertEqual(gen.send(True)[:2],(.0025,'halve'))
        self.assertEqual(gen.send(True)[:2],(.00125,'halve'))
        self.assertEqual(gen.send(True)[:2],(.000625,'halve'))
        with self.assertRaises(StopIteration):gen.send(True)

    def test_failure_then_bisect(self):
        gen=xx.make_schedule(.01,.0001,6,3)
        self.assertEqual(next(gen),(.005,'halve',.01,None))
        self.assertEqual(gen.send(True),(.0025,'halve',.005,None))
        self.assertEqual(gen.send(False),(.00375,'bisect',.005,.0025))
        self.assertEqual(gen.send(True),(.003125,'bisect',.00375,.0025))
        self.assertEqual(gen.send(False),(.0034375,'bisect',.00375,.003125))
        with self.assertRaises(StopIteration): gen.send(True)

    def test_immediate_failure_bisects_to_known_baseline(self):
        gen=xx.make_schedule(.01,.0001,2,2)
        self.assertEqual(next(gen)[0],.005)
        self.assertEqual(gen.send(False)[0],.0075)
        self.assertEqual(gen.send(False)[0],.00875)
        with self.assertRaises(StopIteration):gen.send(False)

    def test_floor_stops_halving_and_invalid_config(self):
        gen=xx.make_schedule(.01,.003,6,2)
        self.assertEqual(next(gen)[0],.005)
        with self.assertRaises(StopIteration):gen.send(True)
        for arguments in [(0.01,0.01,6,4),(0.01,-1,6,4),(0.01,.0001,0,4),(.01,.0001,2,-1)]:
            with self.subTest(arguments=arguments),self.assertRaises(ValueError):
                next(xx.make_schedule(*arguments))

    def test_new_head_can_warm_start_from_previous_converged_ratio(self):
        rng=np.random.default_rng(14)
        raw=rng.normal(size=(88,10)).astype(np.float32)
        z,cols,means,scales=xx.sparse.standardize_design(raw)
        y=2*raw[:,1]-raw[:,3]+rng.normal(0,.02,88)
        mask=np.ones(88,dtype=bool)
        initial=xx.fast.FastHeadFactory(z,max_iter=20000)
        initial.set_ratio(.01)
        initial.fit('my_head',mask,y)
        saved=initial.heads['my_head']
        baseline={'position_1_my_head':{**saved,'lasso_alpha':saved['alpha']}}
        factory=xx.SearchHeadFactory(z,max_iter=20000,baseline_heads=baseline,
            position=1,columns=cols)
        factory.set_ratio(.005)
        p=factory.fit('my_head',mask,y)
        self.assertEqual(p.shape,(88,))
        self.assertIn('position_1_my_head',factory.used_baseline_heads)
        state=next(iter(factory.states.values()))
        self.assertIn(.01,state.fits)
        self.assertIn(.005,state.fits)
        self.assertTrue(factory.heads['my_head']['kkt_passed'])
        self.assertTrue(factory.heads['my_head']['solver_converged'])
        # A failed global trial doesn't prevent returning to a more heavily
        # regularized value, and it must use the nearest STRONGER valid seed.
        factory.set_ratio(.0075)
        factory.fit('my_head',mask,y)
        self.assertEqual(state.fits[.0075]['alpha_ratio'],.0075)

    def test_loading_baseline_verifies_source_and_feature_layout(self):
        rng=np.random.default_rng(18)
        raw=rng.normal(size=(80,8)).astype(np.float32)
        z,cols,means,scales=xx.sparse.standardize_design(raw)
        target=raw[:,1]*2+raw[:,2]*.1
        fit=xx.fast.FastHeadFactory(z,max_iter=20000)
        fit.set_ratio(.01)
        fit.fit('my_head',np.ones(len(z),dtype=bool),target)
        one=fit.heads['my_head']
        heads={f'position_{i}_my_head':one for i in (1,2)}
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'capture'; root.mkdir()
            analysis=root/'old_results'
            layout={'layers':[1,2],'full_features':8,'samples':80,'slots':4,'readout_scope':'suffix'}
            xx.sparse.save_heads(analysis/'models'/'xx'/'alpha_0p01_stage_5',
                                 heads,cols,means,scales,layout)
            summary={'capture_root':str(root),'readout':{'scope':'suffix','tail_tokens':4},
                     'hyperparameters':{'representation':'paired','alpha_ratio':.01,
                                        'stage':5,'layers':'1,2'},
                     'rows':[{'template':'xx','alpha_ratio':.01,'joint_pass_rate':.538,
                              'coefficient_count':17,'all_heads_converged':True}]}
            xx.sparse.dec.write_json(analysis/'register_template_summary.json',summary)
            path=analysis/'register_template_summary.json'
            chosen,loaded=xx.read_verified_baseline(path,root,[1,2],5,summary['readout'],
                .01,cols,means,scales,z)
            self.assertAlmostEqual(chosen['joint_pass_rate'],.538)
            self.assertEqual(set(loaded),set(heads))
            with self.assertRaises(ValueError):
                xx.read_verified_baseline(path,root,[1,2],5,summary['readout'],
                    .01,cols,means,scales*1.2,z)
            with self.assertRaises(ValueError):
                xx.read_verified_baseline(path,Path(d)/'another', [1,2],5,
                    summary['readout'],.01,cols,means,scales,z)


if __name__=='__main__':unittest.main()

class TestEndToEndSearch(unittest.TestCase):
    def test_xx_only_search_checkpoints_and_bisects(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'v2'; root.mkdir()
            settings={'schema':xx.reg.SCHEMA,'templates':['ab','xx'],
                      'hyperparameters':{'representation':'paired'},
                      'operations':['add','sub'],
                      'prompts':{'xx:0:add':{'template':'xx','digest':'abc','prompt':'test'}},
                      'pairs':[[1,2],[3,4]]}
            xx.sparse.dec.write_json(root/'manifest.json',settings)
            xx.sparse.dec.write_json(root/'model_identity.json',{'model':'fake'})
            baseline=root/'old_results'/'register_template_summary.json'
            baseline.parent.mkdir()
            xx.sparse.dec.write_json(baseline,{'placeholder':True})
            args=xx.parser().parse_args(['--output-root',str(root),
                '--baseline-summary',str(baseline),'--results-root',str(root/'xx_search'),
                '--max-halvings','4','--bisect-steps','2','--threads','1',
                '--heartbeat-seconds','30'])
            # Synthetic full-token matrix and its exact target pairs.
            pairs=np.array([[1.,2.],[1.,2.],[3.,4.],[3.,4.]],dtype=np.float64)
            op=['add','sub','add','sub']
            x=np.array([[3,4,5,6,7,8,9,10],[4,5,6,7,8,9,10,11],
                        [11,10,9,8,7,6,5,4],[10,9,8,7,6,5,4,3]],dtype=np.float32)
            layout={'layers':[16,28],'full_features':8,'samples':4,'slots':4,
                    'readout_scope':'suffix'}
            calls=[]
            def fake_hierarchy(features,target,cuts,ratio,factory):
                calls.append((ratio,factory.position))
                if ratio <= .0025:
                    raise xx.sparse.SparseFitFailure('SPARSE_HEAD_SOLVER_NOT_CONVERGED',
                        alpha_ratio=ratio)
                factory.heads={'zero':{'indices':np.array([0],np.int32),
                   'weights':np.array([0.]),'intercept':0.,'alpha_ratio':ratio,
                   'nonzero':1,'training_samples':4,'alpha':ratio,
                   'alpha_max':1.,'kkt_passed':True,'kkt_violation':0.,
                   'solver_converged':True,'solver_warning_count':0,
                   'solver_warning_fits':0,'solver_fit_calls':1,
                   'solver_last_iterations':4,'solver_last_dual_gap':0.}}
                return {'predicted':target}
            with (patch.object(xx.reg,'ensure_analysis_capture'),
                  patch.object(xx.reg,'get_record',return_value=({},np.zeros((29,3,1)))),
                  patch.object(xx.reg,'design_matrix',return_value=(x,pairs,op,layout)),
                  patch.object(xx,'read_verified_baseline',
                      return_value=({'joint_pass_rate':.538,'coefficient_count':8339},{})),
                  patch.object(xx.sparse.hier,'fit_hierarchy',side_effect=fake_hierarchy)):
                xx.search(args)
            info=json.loads((root/'xx_search'/'xx_alpha_search_summary.json').read_text())
            self.assertTrue(info['search_complete'])
            self.assertEqual([a['ratio'] for a in info['attempts']],
                             [.005,.0025,.00375,.003125])
            self.assertEqual([a['success'] for a in info['attempts']],
                             [True,False,True,True])
            self.assertEqual(info['lowest_converged']['ratio'],.003125)
            self.assertEqual(len(info['failures']),1)
            self.assertEqual(sorted(set(p for _,p in calls)),[1,2])
            self.assertTrue((root/'xx_search'/'models'/'alpha_0p005_stage_5'/'heads.json').is_file())
            self.assertTrue((root/'xx_search'/'models'/'alpha_0p003125_stage_5'/'heads.json').is_file())
            with (patch.object(xx.reg,'ensure_analysis_capture'),
                  patch.object(xx.reg,'get_record',return_value=({},np.zeros((29,3,1)))),
                  patch.object(xx.reg,'design_matrix',return_value=(x,pairs,op,layout)),
                  patch.object(xx,'read_verified_baseline',
                      return_value=({'joint_pass_rate':.538,'coefficient_count':8339},{}))):
                with self.assertRaisesRegex(RuntimeError,'XX_EXISTING_SEARCH_RESULTS'):
                    xx.search(args) # Explicit --resume required.
                args.resume=True
                xx.search(args) # Completed search is safely a no-op.
