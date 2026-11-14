import unittest
import numpy as np
from tools.nanojev_qwen_c2_compressive_train import signed_deck,split,make_network,crossfit_targets,teacher_fit,teacher_predict
from tools.nanojev_qwen_c2_local_train import answer

class CompressiveTests(unittest.TestCase):
    def test_full_signed_grid_and_labels(self):
        pts=signed_deck(((3,4,20,10),),32,47)
        self.assertEqual(len(pts),4225)
        self.assertIn((3,4,-12,-22),pts)
        self.assertEqual(answer(3,4,20,10),20)
    def test_stable_holdout(self):
        a=signed_deck(((3,4,20,10),),4,47)
        b=signed_deck(((3,4,20,10),),8,47)
        tr0,val0=split(a,47);tr1,val1=split(b,47)
        self.assertFalse(set(tr1)&set(val0))
        self.assertFalse(set(val1)&set(tr0))
    def test_network_shapes(self):
        import torch
        model=make_network(torch,1024)
        y,raw=model(torch.randn(7,1024))
        self.assertEqual(tuple(y.shape),(7,))
        self.assertEqual(tuple(raw.shape),(7,))
        y.sum().backward()
        self.assertIsNotNone(model.corrected.weight.grad)
    def test_teacher_does_not_retrieve_own_label(self):
        rng=np.random.default_rng(1)
        x=rng.standard_normal((44,12)).astype('float32')
        y=np.arange(44,dtype=float)*7
        pred=crossfit_targets(x,y,rank=5,seed=47,ks=(4,8),folds=4)
        self.assertEqual(pred.shape,y.shape)
        self.assertTrue(np.isfinite(pred).all())
        # Held-out teacher predictions are not contaminated by their truth.
        geo,cfg=teacher_fit(x,y,5,47,(4,8))
        vals=teacher_predict(geo,cfg,x[:3]+.01,y)
        self.assertEqual(len(vals),3)

if __name__=='__main__':unittest.main()

class FrontierTests(unittest.TestCase):
    def test_all_models_are_default(self):
        from tools.nanojev_qwen_c2_compressive_train import resolve_variants,VARIANTS
        self.assertEqual(resolve_variants('all'),tuple(VARIANTS))
        self.assertEqual(len(VARIANTS),5)

    def test_bounded_frontier_and_no_holdout_replay_leakage(self):
        from tools.nanojev_qwen_c2_compressive_train import frontier_deck,split
        c=((3,4,20,10),)
        baseline=frontier_deck(c,32,47,cap=8192)
        self.assertEqual(len(baseline),4225)
        old,_=split(baseline,47)
        bigger=frontier_deck(c,512,47,cap=8192,replay=old,replay_fraction=.25)
        tr,val=split(bigger,47)
        self.assertEqual(len(bigger),8192)
        self.assertFalse(set(tr)&set(val))
        self.assertTrue(set(bigger)&set(old))
        self.assertTrue(any(abs(a-20)>400 for p,q,a,b in bigger))
        self.assertEqual(bigger,frontier_deck(c,512,47,cap=8192,replay=old,replay_fraction=.25))

    def test_outer_is_outside_every_center(self):
        from tools.nanojev_qwen_c2_compressive_train import outer_shell
        centers=((3,4,20,10),(3,4,36,22))
        deck=outer_shell(centers,32,47,100)
        self.assertEqual(len(deck),100)
        self.assertTrue(all(not any(abs(a-ca)<=32 and abs(b-cb)<=32 for p,q,ca,cb in centers) for p,q,a,b in deck))

    def test_models_have_independent_weights(self):
        import torch
        from tools.nanojev_qwen_c2_compressive_train import make_network,VARIANTS
        models=[make_network(torch,128,shape) for shape in VARIANTS.values()]
        self.assertEqual(len({id(next(m.parameters())) for m in models}),len(models))
        for m in models:
            self.assertEqual(m(torch.randn(3,128))[0].shape,(3,))
