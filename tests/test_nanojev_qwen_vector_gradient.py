import json,tempfile,unittest
from pathlib import Path
import numpy as np
from tools.nanojev_qwen_vector_gradient import local_targets,evaluate_population

class GradientTests(unittest.TestCase):
    def test_linear_local_recovery(self):
        rng=np.random.default_rng(3)
        x=rng.uniform(-2,2,(300,2));y=7+3*x[:,0]-2*x[:,1]
        p,g,_=local_targets(x[:16],x[16:],y[16:],32,.001)
        self.assertLess(np.mean(abs(p-y[:16])),.02)
        self.assertLess(np.mean(abs(g[:,0]-3)),.03)
    def test_saved_data_holdout(self):
        rng=np.random.default_rng(13)
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);x=rng.normal(size=(200,18));y=5*x[:,0]-3*x[:,1]
            np.save(root/'activations.npy',x.astype('float32'))
            with (root/'probes.jsonl').open('w') as f:
                for v in y:f.write(json.dumps({'answer':float(v)})+'\n')
            result,pred=evaluate_population(root,rank=6,holdout=40,ks=(8,16),penalties=(.1,1.),alphas=(.5,1.))
            self.assertEqual(len(pred),40)
            self.assertEqual(result['holdout'],40)
            self.assertTrue(np.isfinite(result['holdout_corrected']['mae']))
            self.assertTrue(len(result['trials'])>0)
if __name__=='__main__':unittest.main()
