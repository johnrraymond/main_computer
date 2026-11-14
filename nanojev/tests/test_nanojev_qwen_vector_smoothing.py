import json, tempfile, unittest
from pathlib import Path
import numpy as np
from tools.nanojev_qwen_vector_smoothing import evaluate_population, smooth

class TestSmoothing(unittest.TestCase):
    def test_identity_and_bounded(self):
        X=np.array([[0.],[1.],[2.],[3.]])
        self.assertTrue(np.array_equal(smooth(X[:1],X[1:],2,0),X[:1]))
        self.assertGreater(smooth(X[:1],X[1:],2,.5)[0,0],0)
    def test_holdout_readout(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            rng=np.random.default_rng(71)
            X=rng.normal(size=(180,8)).astype('float32')
            y=X[:,0]*10+X[:,1]*4
            np.save(folder/'activations.npy',X)
            with (folder/'probes.jsonl').open('w') as f:
                for v in y:f.write(json.dumps({'answer':float(v)})+'\n')
            r,rows=evaluate_population(folder,holdout=30,rank=4,ks=(4,8),alphas=(.1,.5))
            self.assertEqual(len(rows),30)
            self.assertEqual(r['holdout_original']['n'] if 'n' in r['holdout_original'] else len(rows),30)
            self.assertEqual(len(r['trials']),4)
            self.assertTrue(np.isfinite(r['holdout_smoothed']['mae']))
if __name__=='__main__':unittest.main()
