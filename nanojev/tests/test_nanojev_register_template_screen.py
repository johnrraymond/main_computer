import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import nanojev_register_template_screen as reg


class TestRegistrationExperiment(unittest.TestCase):
    def args(self, *args):
        return reg.parser().parse_args(list(args))

    def test_pair_inputs_remain_distinct_and_operation_changes(self):
        sample = (2_000_000, -3)
        addition=reg.render('xx','add','decimal',sample)
        subtract=reg.render('xx','sub','decimal',sample)
        self.assertIn('XXXXXX = 2000000\nYYYYYY = -3',addition)
        self.assertIn('operation: XXXXXX + YYYYYY',addition)
        self.assertIn('operation: XXXXXX - YYYYYY',subtract)
        self.assertEqual(addition.split('operation:')[0],subtract.split('operation:')[0])
        self.assertEqual(addition.count('2000000'),1)
        self.assertEqual(addition.count('-3'),1)

    def test_all_representation_formats(self):
        self.assertEqual(reg.number_text('decimal',0),'0')
        self.assertIn(',',reg.number_text('paired',-2000))
        self.assertNotEqual(reg.number_text('logbar',2000),'2000')

    def test_invalid_choices_rejected(self):
        with self.assertRaises(ValueError): reg.parse_csv('ab,ab',reg.TEMPLATES,'TEMPLATES')
        with self.assertRaises(ValueError): reg.parse_csv('unknown',reg.OPERATIONS,'OPERATIONS')

    def test_one_setting_and_small_plan(self):
        args=self.args('--pair-count','20','--templates','ab,xx',
                       '--operations','add,sub','--representation','decimal',
                       '--alpha-ratio','0.002','--stage','1')
        p=reg.make_plan(args)
        self.assertEqual(len(p['prompts']),80)
        self.assertEqual(p['hyperparameters']['alpha_ratio'],0.002)
        self.assertEqual(p['hyperparameters']['stage'],1)
        self.assertEqual(len(p['pairs']),20)

    def test_import_hyperparams_no_coefficients(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'summary.json'
            path.write_text(json.dumps({'layers':[16,28],'rows':[
                {'representation':'decimal','alpha_ratio':.03,'stage':1,
                 'joint_pass_rate':.7,'coefficient_count':100},
                {'representation':'paired','alpha_ratio':.005,'stage':3,
                 'joint_pass_rate':.9,'coefficient_count':999,
                 'model_coefficients':[1,2,3]}]}))
            args=self.args('--inherit-summary',str(path),'--stage','2')
            selected,inherited=reg.choose_hyperparams(args)
            self.assertEqual(selected['representation'],'paired')
            self.assertEqual(selected['alpha_ratio'],.005)
            self.assertEqual(selected['stage'],2)
            self.assertNotIn('model_coefficients',selected)
            self.assertIsNotNone(inherited)

    def make_records(self,root,settings,width=3,layers=3):
        root=Path(root)
        (root/'records').mkdir(parents=True)
        identity={'model':'synthetic','model_info':{'test':True}}
        reg.sparse.dec.write_json(root/'model_identity.json',identity)
        reg.sparse.dec.write_json(root/'manifest.json',settings)
        for task in settings['prompts'].values():
            digest=task['digest']
            n=7 if task['template']=='ab' else 11
            value=int(settings['pairs'][task['pair_id']][0])
            tensor=np.ones((layers,n,width),dtype=np.float16)*(np.sign(value)*min(abs(value),10))
            meta={'prompt':task['prompt'],'sha256':digest,'identity':identity,
                  'shape':list(tensor.shape),'input_ids':list(range(n)),'input_token_count':n}
            with reg.record_path(root,digest).open('wb') as f:
                np.savez_compressed(f,metadata=json.dumps(meta),hidden_states=tensor)
        return identity

    def test_readout_suffix_and_full_without_pooling(self):
        with tempfile.TemporaryDirectory() as d:
            args=self.args('--pair-count','20','--templates','ab,xx',
                           '--operations','add,gt','--representation','decimal')
            settings=reg.make_plan(args)
            identity=self.make_records(d,settings)
            mat,pairs,ops,layout=reg.design_matrix(Path(d),settings,'ab',[1,2],identity)
            self.assertEqual(mat.shape,(40,2*4*3))
            self.assertEqual(layout['pooling'],'none')
            self.assertEqual(ops[:2],['add','gt'])
            self.assertEqual(pairs[0].tolist(),pairs[1].tolist())
            self.assertEqual(mat[0,0],np.sign(pairs[0,0])*min(abs(pairs[0,0]),10))
            settings['readout']['scope']='full'
            mat_full,_,_,layout_full=reg.design_matrix(Path(d),settings,'xx',[1,2],identity)
            self.assertEqual(mat_full.shape,(40,2*11*3))
            self.assertEqual(layout_full['slots'],11)

    def test_reanalyze_hyperparams_and_scope_without_recapture(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            initial=self.args('--pair-count','20','--representation','decimal','--alpha-ratio','0.03')
            settings=reg.make_plan(initial)
            self.make_records(root,settings)
            override=self.args('--analyze-only','--output-root',str(root),
                '--alpha-ratio','0.005','--stage','3','--layers','0,2',
                '--readout-scope','full')
            changed=reg.capture(override)
            self.assertEqual(changed['hyperparameters']['alpha_ratio'],.005)
            self.assertEqual(changed['hyperparameters']['stage'],3)
            self.assertEqual(changed['readout']['scope'],'full')
            untouched=json.loads((root/'manifest.json').read_text())
            self.assertEqual(untouched['hyperparameters']['alpha_ratio'],.03)
            self.assertEqual(untouched['readout']['scope'],'suffix')
            bad=self.args('--analyze-only','--output-root',str(root),
                '--representation','paired')
            with self.assertRaisesRegex(ValueError,'RECAPTURE_REQUIRED'):
                reg.capture(bad)

    def test_cannot_analyze_missing_capture(self):
        with tempfile.TemporaryDirectory() as d:
            args=self.args('--analyze-only','--output-root',str(Path(d)/'absent'))
            with self.assertRaises(FileNotFoundError): reg.capture(args)

    def test_operation_drift_and_per_op_scores(self):
        pairs=np.asarray([[10,20],[10,20],[30,40],[30,40]],dtype=int)
        pred=np.array([[10,20],[10,20],[30,40],[33,40]],dtype=float)
        overall,byop,robust,passing,rel=reg.operation_metrics(pairs,pred,['add','sub','add','sub'])
        self.assertAlmostEqual(overall['both_within_5pct_rate'],.75)
        self.assertEqual(robust['all_operations_joint_pass_rate'],.5)
        self.assertGreater(robust['normalized_operation_drift_max'],.05)
        self.assertAlmostEqual(byop['sub']['joint_pass_rate'],.5)

    def test_prepare_mode_never_writes_files(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'new'
            args=self.args('--prepare-only','--output-root',str(root),
                           '--pair-count','20','--templates','ab','--operations','add')
            with patch.object(reg.base,'emit') as emit:
                self.assertEqual(len(reg.capture(args)['prompts']),20)
                self.assertFalse(root.exists())
                self.assertEqual(emit.call_args.args[0],'register_plan')

    def test_analysis_writes_separate_fresh_models(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'capture'
            args=self.args('--analyze-only','--pair-count','20',
                           '--templates','ab,xx','--operations','add,sub',
                           '--representation','decimal', '--layers','0,1',
                           '--stage','1','--alpha-ratio','0.1',
                           '--output-root',str(root),'--results-root',str(Path(d)/'results'))
            settings=reg.make_plan(args)
            identity=self.make_records(root,settings,width=4,layers=3)
            # The smoke tests path, scoping and writes without spending CPU time
            # in the high-dimensional sparse optimizer.
            def fake_fit(z, y, cuts, ratio, factory):
                factory.heads['toy']={'indices':np.array([0],dtype=int),'weights':np.array([1.]),
                    'intercept':0.,'nonzero':1,'solver_warning_count':0,
                    'solver_converged':True,'kkt_passed':True,'alpha':ratio}
                return {'predicted':y.copy(),'oracle':y.copy()}
            with patch.object(reg.sparse.hier,'fit_hierarchy',side_effect=fake_fit),\
                 patch.object(reg.sparse,'save_heads') as write_models:\
                scores=reg.analyze(args,settings)
            self.assertEqual(len(scores),2)
            self.assertEqual({s['template'] for s in scores},{'ab','xx'})
            self.assertEqual(write_models.call_count,2)
            self.assertTrue((Path(d)/'results'/'register_template_scores.csv').is_file())
            self.assertTrue((Path(d)/'results'/'register_predictions_xx.csv').is_file())
            self.assertFalse((root/'analysis').exists())


    def test_capture_preflight_fails_before_lasso_on_partial_records(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            args=self.args('--analyze-only','--pair-count','20',
                           '--templates','ab','--operations','add,sub',
                           '--representation','decimal', '--layers','0,1',
                           '--stage','1','--alpha-ratio','0.1',
                           '--output-root',str(root),'--results-root',str(root/'new_analysis'))
            settings=reg.make_plan(args)
            self.make_records(root,settings,width=4,layers=3)
            missing=next(iter(settings['prompts'].values()))['digest']
            reg.record_path(root,missing).unlink()
            with patch.object(reg.base,'emit') as emit, \
                 patch.object(reg.sparse.hier,'fit_hierarchy') as fit:
                with self.assertRaisesRegex(RuntimeError,'REGISTER_CAPTURE_INCOMPLETE'):
                    reg.analyze(args,settings)
            self.assertFalse(fit.called)
            events=[call.args[0] for call in emit.call_args_list]
            self.assertEqual(events[:2],['register_analysis_start','register_analysis_capture_incomplete'])
            self.assertFalse((root/'new_analysis').exists())

    def test_complete_preflight_and_progress_head_events(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            args=self.args('--analyze-only','--pair-count','20',
                           '--templates','ab','--operations','add,sub',
                           '--representation','decimal', '--layers','0,1',
                           '--stage','1','--alpha-ratio','0.1',
                           '--output-root',str(root),'--results-root',str(root/'new_analysis'),
                           '--progress-every-heads','1')
            settings=reg.make_plan(args)
            self.make_records(root,settings,width=4,layers=3)
            def fake_fit(z,y,cuts,ratio,factory):
                # Actual progress is produced by calling a factory.fit head.
                with patch.object(reg.fast.FastHeadFactory,'fit',return_value=np.zeros(len(y))):
                    factory.fit('zero', np.ones(len(y),dtype=bool), np.zeros(len(y)))
                factory.heads['toy']={'indices':np.array([0],dtype=int),'weights':np.array([1.]),
                    'intercept':0.,'nonzero':1,'solver_warning_count':0,
                    'solver_converged':True,'kkt_passed':True,'alpha':ratio}
                return {'predicted':y.copy(),'oracle':y.copy()}
            with patch.object(reg.base,'emit') as emit, \
                 patch.object(reg.sparse.hier,'fit_hierarchy',side_effect=fake_fit), \
                 patch.object(reg.sparse,'save_heads'):
                result=reg.analyze(args,settings)
            events=[call.args[0] for call in emit.call_args_list]
            self.assertEqual(len(result),1)
            for key in ('register_analysis_start','register_analysis_capture_ready',
                        'register_analysis_template_start','register_analysis_design_ready',
                        'register_analysis_standardized','register_analysis_position_start',
                        'register_analysis_head_progress','register_analysis_position_complete',
                        'register_template_result'):
                self.assertIn(key,events)
            self.assertEqual(events.count('register_analysis_head_progress'),2)

    def test_wait_for_concurrent_capture(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            args=self.args('--analyze-only','--wait-for-capture',
                           '--capture-poll-seconds','0.01')
            settings=reg.make_plan(self.args('--pair-count','20',
                '--templates','ab','--operations','add'))
            self.make_records(root,settings,width=3,layers=3)
            digest=next(iter(settings['prompts'].values()))['digest']
            path=reg.record_path(root,digest)
            moved=path.with_suffix('.waiting')
            path.rename(moved)
            def finish():
                import time
                time.sleep(0.05)
                moved.rename(path)
            from threading import Thread
            writer=Thread(target=finish)
            writer.start()
            try:
                with patch.object(reg.base,'emit') as emit:
                    reg.ensure_analysis_capture(root,settings,args)
                events=[call.args[0] for call in emit.call_args_list]
                self.assertIn('register_analysis_waiting_for_capture',events)
                self.assertEqual(events[-1],'register_analysis_capture_ready')
            finally:
                writer.join()

if __name__=='__main__': unittest.main()
