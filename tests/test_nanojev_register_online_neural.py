"""Offline checks for independent online numeric decoder; never download Qwen."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import torch

SRC = Path(__file__).resolve().parents[1]/'tools'/'nanojev_register_online_neural.py'
import sys
sys.path.insert(0,str(SRC.parent))
spec=importlib.util.spec_from_file_location('nanojev_neural',SRC)
n=importlib.util.module_from_spec(spec)
spec.loader.exec_module(n)

class TinyTokenizer:
    pad_token_id=0
    eos_token_id=1
    def __call__(self, txt, add_special_tokens=False):
        return {'input_ids':[2+(ord(c)%25) for c in txt]}

class FakeBackbone:
    def __call__(self, input_ids,attention_mask,position_ids,output_hidden_states,use_cache):
        assert not use_cache and output_hidden_states
        assert position_ids.shape==input_ids.shape
        assert (attention_mask[:,0]==1).all()
        v=input_ids.float()[:,:,None].expand(-1,-1,1024)
        return type('FakeResult',(),{'hidden_states':tuple(v*0.001+level for level in range(29))})

class OnlineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pairs,cls.tasks,_,_=n.v3.build_plan()

    def test_number_targets_and_lossless_inversion(self):
        values=[[-2_000_000,2_000_000],[0,-3],[200,0]]
        t=n.targets(torch,values,'cpu')
        self.assertTrue(np.array_equal(n.recover(torch,t).numpy(),np.asarray(values)))

    def test_exact_zero_and_joint(self):
        hits=n.correctness([[0,99],[1,99],[-3,1]],[[0,100],[0,100],[-3,0]])
        self.assertEqual(hits.all(axis=1).tolist(),[True,False,False])
        d=n.summarize_predictions(np.array([[0,99],[1,99]]),np.array([[0,100],[0,100]]),['a','b'],['a','b'])
        self.assertEqual(d['x_zero_recall'],.5)

    def test_fresh_pairs_and_heldout_ops(self):
        forbidden=n.reserved_pairs(self.tasks)
        g=n.FreshPairs(17,forbidden)
        vals=g.batch(256)
        self.assertEqual(len(set((x['a'],x['b']) for x in vals)),256)
        self.assertTrue(all((x['a'],x['b']) not in forbidden for x in vals))
        self.assertTrue(all(x['operation'] not in n.v3.HELD_OUT_OPERATIONS for x in vals))
        self.assertEqual(set(v['family'] for v in vals),set(n.v3.FAMILIES))

    def test_stream_resume_exact(self):
        g=n.FreshPairs(41,n.reserved_pairs(self.tasks))
        g.batch(50)
        snap=g.state_dict()
        next_a=g.batch(15)
        replacement=n.FreshPairs(41,n.reserved_pairs(self.tasks))
        replacement.load_state_dict(snap)
        self.assertEqual(next_a,replacement.batch(15))

    def test_batch_suffix_padding_captures_only_real_tokens(self):
        p=['abcdefghijklmn','abcde','abcdefgh']
        feats=n.extract_batched(torch,FakeBackbone(),TinyTokenizer(),p,'cpu',256)
        self.assertEqual(tuple(feats.shape),(3,8192))
        for i,prompt in enumerate(p):
            tokens=TinyTokenizer()(prompt)['input_ids'][-4:]
            resh=feats[i].reshape(2,4,1024)
            for layer,idx in ((16,0),(28,1)):
                self.assertTrue(torch.allclose(resh[idx,:,0],
                     torch.tensor(tokens,dtype=torch.float32)*.001+layer,atol=1.e-5))
        self.assertFalse(feats.requires_grad)
        net=n.model_factory(torch,8)
        loss=net(feats).square().mean()
        loss.backward()  # fails if frozen capture returned an inference tensor
        self.assertIsNotNone(net[1].weight.grad)

    def test_invalid_token_length(self):
        with self.assertRaises(RuntimeError):
            n.extract_batched(torch,FakeBackbone(),TinyTokenizer(),['abc'],'cpu',256)
        with self.assertRaises(RuntimeError):
            n.extract_batched(torch,FakeBackbone(),TinyTokenizer(),['abcdefgh'],'cpu',6)

    def test_model_gradient_only_small_head(self):
        net=n.model_factory(torch,32)
        self.assertEqual(sum(v.numel() for v in net.parameters()),263298)
        x=torch.randn(16,8192)
        y=n.targets(torch,[[k,-k-3] for k in range(16)],'cpu')
        optimizer=torch.optim.AdamW(net.parameters(),lr=.002)
        old=net[1].weight.detach().clone()
        loss=torch.nn.functional.smooth_l1_loss(net(x),y)
        optimizer.zero_grad();loss.backward();optimizer.step()
        self.assertFalse(torch.equal(old,net[1].weight))

    def test_checkpoint_roundtrip_and_drift(self):
        config={'dummy':'test'};ident={'model':'qwen'}
        g=n.FreshPairs(99,n.reserved_pairs(self.tasks));g.batch(10)
        net=n.model_factory(torch,16);opt=torch.optim.AdamW(net.parameters())
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'latest.pt'
            n.save_checkpoint(torch,path,net,opt,3,config,g,.42,ident)
            fresh=n.model_factory(torch,16);freshopt=torch.optim.AdamW(fresh.parameters())
            stream=n.FreshPairs(99,n.reserved_pairs(self.tasks))
            step,best=n.load_checkpoint(torch,path,fresh,freshopt,stream,config,ident,'cpu')
            self.assertEqual(step,3);self.assertEqual(best,.42)
            self.assertEqual(g.batch(5),stream.batch(5))
            with self.assertRaisesRegex(RuntimeError,'DRIFT'):
                n.load_checkpoint(torch,path,fresh,freshopt,stream,{'wrong':1},ident,'cpu')

    def test_resume_cuda_rng_stays_cpu_byte_tensor(self):
        # Regression: on CUDA runs, torch.load(map_location='cuda') moved
        # CUDA RNG-state tensors to GPU and set_rng_state_all rejected them.
        from unittest.mock import patch
        config={'dummy':'cuda-rng-regression'}
        ident={'model':'qwen'}
        stream=n.FreshPairs(99,n.reserved_pairs(self.tasks))
        stream.batch(10)
        net=n.model_factory(torch,8)
        opt=torch.optim.AdamW(net.parameters())
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'latest.pt'
            n.save_checkpoint(torch,path,net,opt,100,config,stream,0.0,ident)
            ck=torch.load(path,map_location='cpu',weights_only=True)
            ck['cuda_rng']=[torch.randint(0,255,(128,),dtype=torch.uint8)]
            torch.save(ck,path)
            restored=n.model_factory(torch,8)
            restored_opt=torch.optim.AdamW(restored.parameters())
            restored_stream=n.FreshPairs(99,n.reserved_pairs(self.tasks))
            observed=[]
            def check_rng_states(states):
                self.assertEqual(len(states),1)
                self.assertEqual(states[0].device.type,'cpu')
                self.assertEqual(states[0].dtype,torch.uint8)
                observed.append(states[0].clone())
            with patch.object(torch.cuda,'is_available',return_value=True), \
                 patch.object(torch.cuda,'set_rng_state_all',side_effect=check_rng_states), \
                 patch.object(n.torch if hasattr(n,'torch') else torch,'load',wraps=torch.load) as loader:
                step,best=n.load_checkpoint(torch,path,restored,restored_opt,
                    restored_stream,config,ident,device='cuda')
            self.assertEqual(step,100)
            self.assertEqual(best,0.0)
            self.assertEqual(len(observed),1)
            self.assertEqual(loader.call_args.kwargs['map_location'],'cpu')
            self.assertEqual(stream.batch(5),restored_stream.batch(5))

    def test_scoring_zero_empty_is_none(self):
        data=n.summarize_predictions(np.asarray([[10,15]]),np.asarray([[10,14]]),['add'],['symbolic'])
        self.assertIsNone(data['x_zero_recall'])
        self.assertEqual(data['joint_pass_rate'],0.)

# Lightweight end-to-end exercise of CLI training/resume/evaluation. The real
# benchmark's identity is mocked; no model weights or 23k activations required.
class ZIntegrationTests(unittest.TestCase):
    def test_training_resume_and_eval_pipeline(self):
        from unittest.mock import patch
        from contextlib import redirect_stdout
        from io import StringIO
        import sys
        real_tasks=[{'a':1,'b':4,'split':'val','operation':'sym_add','family':'symbolic'},
                    {'a':-1,'b':3,'split':'test','operation':'sym_sub','family':'symbolic'},
                    {'a':0,'b':0,'split':'control','operation':'written_average','family':'written'}]
        m={'tasks_sha256':'mock_sha'}
        model_info={'resolved_commit':'commit','source':'Qwen/Qwen3-0.6B','dtype':'torch.float32'}
        model_identity={'model':'qwen','model_info':model_info}
        def fake_bench(root):return m,real_tasks
        def fake_eval(root,manifest,tasks,split,count=None,seed=19):
            x=np.zeros((4,8192),dtype=np.float32)
            x[:,16:32]=np.arange(4,dtype=np.float32)[:,None]
            y=np.array([[1,2],[2,3],[-1,3],[0,4]],dtype=np.int64)
            return x,y,['a','b','c','d'],['symbolic']*4
        def fake_load_source(*args):
            backbone=FakeBackbone()
            backbone.eval=lambda:backbone
            return torch,backbone,TinyTokenizer(),'cpu',model_info
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);v3root=root/'v3';v3root.mkdir()
            n.atomic_json(v3root/'model_identity.json',model_identity)
            params=['--device','cpu','--dtype','float32','--steps','2',
                    '--batch-size','4','--width','8','--val-examples','4',
                    '--eval-every','1','--checkpoint-every','1','--report-every','1',
                    '--v3-root',str(v3root),'--results-root',str(root/'run')]
            with patch.object(n,'read_benchmark',fake_bench), \
                patch.object(n,'load_eval',fake_eval), \
                patch.object(n,'verify_capture_alignment',lambda *a,**kw:None), \
                patch.dict(sys.modules,{'nanojev_numerate_spine_cassette_smoke':type('Mock',(),{'load_source':fake_load_source})()}), \
                redirect_stdout(StringIO()):
                self.assertEqual(n.main(params),0)
                self.assertTrue((root/'run'/'latest.pt').exists())
                self.assertTrue((root/'run'/'best.pt').exists())
                ck=torch.load(root/'run'/'latest.pt',weights_only=True)
                self.assertEqual(ck['step'],2)
                self.assertEqual(len(ck['random_stream']['seen']),8)
                self.assertEqual(n.main(params[:params.index('--steps')]+['--steps','4','--resume']+params[params.index('--batch-size'):]),0)
                self.assertEqual(torch.load(root/'run'/'latest.pt',weights_only=True)['step'],4)
                self.assertEqual(n.main(params+['--eval-only']),0)

if __name__=='__main__':unittest.main()
