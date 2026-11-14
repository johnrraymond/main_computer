"""Offline interface, regression routing, and isolation checks for register v3."""
from __future__ import annotations
from collections import defaultdict
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import sys

TOOLS=Path(__file__).resolve().parents[1]/'tools'
sys.path.insert(0,str(TOOLS))
import nanojev_register_stability_v3_analyze as ana


class TestRegV3Analyze(unittest.TestCase):
    def test_plan_layout_and_no_implicit_layer_transfer(self):
        data={'capture':{'stored_layers':[16,28],'max_prompt_tokens':256},'expected_unique_prompts':23552}
        layout=ana.layout_for(data,[16,28],'suffix',4)
        self.assertEqual(layout['stored_layer_indices'],[0,1])
        self.assertEqual(layout['full_features'],8192)
        self.assertEqual(layout['pooling'],'none')
        with self.assertRaisesRegex(ValueError,'UNCAPTURED'):ana.layout_for(data,[1,28],'suffix',4)
        with self.assertRaisesRegex(ValueError,'SUFFIX'):ana.layout_for(data,[16],'full',4)

    def test_pair_pass_requires_both_and_zero_exact(self):
        y=np.array([[10,-10],[0,100],[1,2]],float)
        p=np.array([[10.4,-10.3],[1e-9,104],[1.07,2.00]],float)
        mask,joint,relative=ana.pair_accuracy(y,p)
        self.assertEqual(joint.tolist(),[True,False,False])
        self.assertFalse(mask[1,0]);self.assertTrue(mask[1,1])

    def test_score_control_really_held_out_and_drift(self):
        ops=list(ana.gen.OPERATIONS)
        pair=(17,-3)
        tasks=[{'pair_id':0,'task_id':i,'a':pair[0],'b':pair[1],
                'operation':op,'family':family,'split':'control'} for i,(op,family,_) in enumerate(ops)]
        pred=np.tile(np.array([[17.,-3.]]),(24,1))
        score=ana.summarize_scores(tasks,list(range(24)),pred,'control',set(ana.gen.HELD_OUT_OPERATIONS))
        self.assertEqual(score['control_heldout_operation_samples'],4)
        self.assertEqual(score['control_heldout_operation_joint_pass_rate'],1.)
        self.assertEqual(score['control_all_24_operations_joint_pass_rate'],1.)
        self.assertEqual(score['control_mean_normalized_operation_drift'],0.)
        pred[23,1]=10.
        score=ana.summarize_scores(tasks,list(range(24)),pred,'control',set(ana.gen.HELD_OUT_OPERATIONS))
        self.assertEqual(score['control_all_24_operations_joint_pass_rate'],0.)

    def test_training_normalization_excludes_holdout(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'m.npy'
            mm=np.lib.format.open_memmap(path,mode='w+',dtype=np.float32,shape=(14,3))
            mm[:]=np.array([[i,2*i,8] for i in range(1,13)]+[[1000,2000,800],[5000,10000,900]],np.float32)
            del mm
            mat=np.load(path,mmap_mode='r')
            z,good,means,scales=ana.standardize_training(mat,np.arange(12))
            self.assertEqual(good.tolist(),[0,1])
            np.testing.assert_allclose(means,[6.5,13])
            np.testing.assert_allclose(z.mean(0),[0,0],atol=1e-6)

    def test_head_routing_with_known_coefficients(self):
        # Simulated trained two-register heads, predict using sign and bands.
        z=np.array([[100.,-2.],[50.,-3.]],dtype=np.float32)
        def h(indices,weight,intercept):
            return {'indices':np.asarray(indices,dtype=np.int32),'weights':np.asarray(weight,dtype=np.float64),'intercept':intercept}
        heads={'position_1_zero':h([],[], -1.),'position_1_sign':h([0],[1.],0.),
               'position_1_route_above_200':h([] ,[], -1.),
               'position_1_expert_0_pos':h([0],[1.],0.),
               'position_2_zero':h([],[], -1.),'position_2_sign':h([1],[1.],0.),
               'position_2_route_above_200':h([],[], -1.),
               'position_2_expert_0_neg':h([1],[-1.],0.)}
        p1=ana.predict_position(z,heads,1,(200,))
        p2=ana.predict_position(z,heads,2,(200,))
        np.testing.assert_array_equal(p1,[100.,50.])
        np.testing.assert_array_equal(p2,[-2.,-3.])

    def test_regime_head_targets_and_order(self):
        n=100
        y=np.array([-5.,5.,-100.,100.,-500.,500.,-5000.,5000.,-30000.,30000.,-500000.,500000.] * 10)
        class FakeFactory:
            def __init__(self): self.names=[];self.responses=[];self.heads={}
            def set_ratio(self,ratio):self.ratio=ratio
            def fit(self,name,mask,response):
                self.names.append(name);self.responses.append(np.asarray(response).copy())
                self.heads[name]={'nonzero':0};return None
        fac=FakeFactory()
        ana.fit_single_position(np.zeros((len(y),2),dtype=np.float32),y,(20,200,2000,20000,200000),fac,.01)
        self.assertEqual(fac.names[:2],['zero','sign'])
        self.assertIn('expert_5_neg',fac.names)
        self.assertIn('expert_0_pos',fac.names)
        self.assertEqual(len(fac.names),19)

    def test_actual_capture_task_and_split_counts(self):
        pairs,tasks,_,_=ana.gen.build_plan()
        by_split=defaultdict(set)
        for task in tasks: by_split[task['split']].add(task['pair_group'])
        for left in ('train','val','test','control'):
            for right in ('train','val','test','control'):
                if left!=right:self.assertFalse(by_split[left]&by_split[right])
        self.assertEqual(len(tasks),23552)
        self.assertEqual(sum(t['split']=='train' for t in tasks),8*2004)

    def test_binary_search_schedule(self):
        gen=ana.xx.make_schedule(.01,.0001,6,2)
        self.assertEqual(next(gen)[:2],(.005,'halve'))
        self.assertEqual(gen.send(True)[:2],(.0025,'halve'))
        result=gen.send(False)
        self.assertEqual(result[:2],(.00375,'bisect'))
        self.assertEqual(gen.send(True)[:2],(.003125,'bisect'))

    def test_parser_defaults(self):
        a=ana.parser().parse_args([])
        self.assertEqual((a.start_alpha_ratio,a.stage,a.tail_tokens),(.03,5,4))
        ana.validate_args(a)

    def test_model_checkpoint_roundtrip_when_columns_were_constant(self):
        # Confirm compatibility with the saved-head format used by xx search.
        with tempfile.TemporaryDirectory() as folder:
            features=np.random.default_rng(9).normal(size=(14,3)).astype(np.float32)
            columns=np.asarray([0,2,5],dtype=np.int32)
            means=np.asarray([.1,.3,.5],dtype=np.float32)
            scales=np.asarray([1,2,3],dtype=np.float32)
            model={'indices':np.array([0,2],dtype=np.int32),
                   'weights':np.array([.2,-.4]),'intercept':7.5,
                   'nonzero':2,'alpha':.01,'alpha_max':1.,'alpha_ratio':.01,
                   'kkt_passed':True,'kkt_violation':0.,'training_samples':14,
                   'solver_converged':True,'solver_warning_count':0,
                   'solver_warning_fits':0,'solver_fit_calls':1,
                   'solver_last_iterations':3,'solver_last_dual_gap':0.}
            heads={'position_1_zero':model,'position_2_zero':model}
            layout={'layers':[16,28],'slots':4,'full_features':3,
                    'original_full_features':8192,'samples':14,'readout_scope':'suffix'}
            path=Path(folder)/'model'
            ana.sparse.save_heads(path,heads,columns,means,scales,layout)
            loaded=ana.xx.read_saved_models(path,[16,28],{'scope':'suffix','tail_tokens':4},
                columns,means,scales,features)
            self.assertEqual(set(loaded),set(heads))
            np.testing.assert_array_equal(loaded['position_1_zero']['indices'],[0,2])

    def test_partial_cache_resumes_only_with_matching_fingerprint(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'source';(root/'records').mkdir(parents=True)
            out=Path(folder)/'results';tasks=[];identity={'fake':True}
            m={'tasks_sha256':'fake','capture':{'stored_layers':[16,28]},'expected_unique_prompts':3}
            layout=ana.layout_for({'capture':{'stored_layers':[16,28],'max_prompt_tokens':32},'expected_unique_prompts':3},[16,28],'suffix',2,width=2)
            for i in range(3):
                name=f'x{i}';digest=ana.gen.digest(name)
                data=np.ones((2,3,2),dtype=np.float16)*i
                md={'prompt':name,'sha256':digest,'identity':identity,'shape':list(data.shape),
                    'generator_schema':ana.gen.SCHEMA,'stored_layers':[16,28],
                    'input_token_count':3}
                with (root/'records'/(digest+'.npz')).open('wb') as f:
                    np.savez_compressed(f,metadata=json.dumps(md),hidden_states=data)
                tasks.append({'prompt':name,'sha256':digest})
            cache=out/'design_cache';cache.mkdir(parents=True)
            stem=ana.cache_name(layout)
            path=cache/(stem+'.partial.npy')
            buffer=np.lib.format.open_memmap(path,mode='w+',dtype=np.float32,shape=(3,8))
            buffer[0]=0.;buffer.flush();del buffer
            fingerprint={'generator_tasks_sha256':'fake','model_identity':identity,'layout':layout}
            ana.gen.write_atomic(cache/(stem+'.partial.json'),json.dumps({'fingerprint':fingerprint,'next_row':1}))
            result=ana.build_or_load_matrix(root,out,m,tasks,identity,layout,{},report_every=2)
            self.assertEqual(result.shape,(3,8))
            self.assertTrue(np.all(result[2]==2))
            self.assertFalse((cache/(stem+'.partial.json')).exists())

    def test_cache_builder_roundtrip_tiny(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'source';(root/'records').mkdir(parents=True)
            out=Path(folder)/'results';tasks=[]
            identity={'fake':True}
            manifest={'tasks_sha256':'fake','capture':{'stored_layers':[16,28]},'expected_unique_prompts':2}
            for i in range(2):
                text=f'p{i}';hashv=ana.gen.digest(text)
                hidden=np.ones((2,5,3),dtype=np.float16)*i
                metadata={'prompt':text,'sha256':hashv,'identity':identity,'shape':[2,5,3],
                          'generator_schema':ana.gen.SCHEMA,'stored_layers':[16,28],
                          'input_token_count':5}
                with (root/'records'/(hashv+'.npz')).open('wb') as f:
                    np.savez_compressed(f,metadata=json.dumps(metadata),hidden_states=hidden)
                tasks.append({'prompt':text,'sha256':hashv})
            layout=ana.layout_for({'capture':{'stored_layers':[16,28],'max_prompt_tokens':256},'expected_unique_prompts':2},[16,28],'suffix',4,width=3)
            mat=ana.build_or_load_matrix(root,out,manifest,tasks,identity,layout,{},report_every=2)
            self.assertEqual(mat.shape,(2,24))
            np.testing.assert_array_equal(mat[1],np.ones(24))
            again=ana.build_or_load_matrix(root,out,manifest,tasks,identity,layout,{},report_every=2)
            np.testing.assert_array_equal(mat,again)
            self.assertFalse((root/'records'/'corrupt').exists())

if __name__=='__main__':unittest.main()
