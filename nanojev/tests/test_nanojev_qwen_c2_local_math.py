import unittest
import numpy as np
from tools.nanojev_qwen_c2_local_math import fit_geometry,transform_geometry,local_predict,compare
from tools.nanojev_qwen_c2_local_train import deck,answer,prompt

class LocalMath(unittest.TestCase):
    def test_synthetic_relation(self):
        rng=np.random.default_rng(12)
        x=rng.uniform(-2,2,size=(240,7)).astype(np.float32)
        y=3*x[:,0]-2*x[:,1]+4
        xt=rng.uniform(-2,2,size=(40,7)).astype(np.float32)
        yt=3*xt[:,0]-2*xt[:,1]+4
        r=compare(x,y,{'validation':(xt,yt)},rank=7,seed=12,ks=(16,32),penalties=(.001,.1))
        self.assertEqual(r['sets']['validation']['local']['n'],40)
        self.assertLess(r['sets']['validation']['local']['mae'],r['sets']['validation']['constant']['mae'])
        self.assertTrue(np.isfinite(r['sets']['validation']['local_predictions']).all())

    def test_train_only_projection(self):
        rng=np.random.default_rng(6)
        x=rng.normal(size=(60,8)).astype(np.float32)
        geo=fit_geometry(x,4,seed=10)
        z=transform_geometry(geo,x[:6])
        self.assertEqual(z.shape,(6,4))
        self.assertEqual(geo['train'].shape,(60,4))
        self.assertTrue(np.isfinite(z).all())

    def test_c2_prompt_and_disjoint_split(self):
        points=deck(((3,4,20,10),),4,47)
        tr=[x for x in points if hash_of(x)%10!=0]
        va=[x for x in points if hash_of(x)%10==0]
        self.assertFalse(set(tr)&set(va))
        self.assertEqual(prompt(3,4,20,10),'PROBLEM: (3*20)-(4*10)')
        self.assertEqual(answer(3,4,20,10),20)

def hash_of(x):
    import hashlib
    return int(hashlib.sha256(f'47:{x}'.encode()).hexdigest()[:8],16)

if __name__=='__main__':unittest.main()

class StageComparison(unittest.TestCase):
    def test_report_and_identical_decks(self):
        import tempfile, types, pathlib, json
        import torch
        import tools.nanojev_qwen_c2_local_train as trainer
        pts=deck(((3,4,20,10),),5,47)
        train,val,outer=pts[:70],pts[70:95],pts[95:]
        class Head(torch.nn.Module):
            def forward(self,x):
                return (x[:,0:1]*0.025)
        def fake_features(torch_,model,tokenizer,chunk,device,maxlen):
            return torch.tensor([[float(a),float(b),float(p),float(q),
                    float(p*a-q*b),float(a+b)] for p,q,a,b in chunk],dtype=torch.float32)
        original=trainer.features
        trainer.features=fake_features
        try:
            args=types.SimpleNamespace(device='cpu',batch_size=16,max_length=128,
                                      compare_rank=5,seed=47,compare_ks='8,16')
            with tempfile.TemporaryDirectory() as td:
                report=trainer.compare_stage(torch,torch.nn.Identity(),None,Head(),train,val,outer,
                                             args,scale=100.,output=pathlib.Path(td),radius=5)
                saved=json.loads((pathlib.Path(td)/'comparisons/radius_5/report.json').read_text())
                self.assertEqual(saved['sets']['validation']['neural']['n'],len(val))
                self.assertEqual(saved['sets']['validation']['local']['n'],len(val))
                self.assertEqual(saved['sets']['outer']['local']['n'],len(outer))
                self.assertTrue((pathlib.Path(td)/'comparisons/radius_5/validation_predictions.csv').exists())
        finally:
            trainer.features=original
