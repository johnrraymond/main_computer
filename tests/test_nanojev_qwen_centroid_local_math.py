import json
import tempfile
import unittest
from pathlib import Path
import numpy as np
from tools.nanojev_qwen_centroid_local_math import local_predict, run

class LocalMathTests(unittest.TestCase):
    def test_smooth_local_number_line(self):
        rng=np.random.default_rng(8)
        a=np.linspace(-3,3,100)
        z=np.column_stack((a,a*a*.02,rng.normal(0,.005,len(a))))
        pred=local_predict(z,3*a+5,z[::7],k=16,degree=1,alpha=.1)
        self.assertLess(np.mean(np.abs(pred-(3*a[::7]+5))),.15)

    def test_holdout_and_files(self):
        rng=np.random.default_rng(17)
        with tempfile.TemporaryDirectory() as tmp:
            src=Path(tmp)/'C3_fixed_addition';src.mkdir()
            a=rng.integers(-30,31,125)
            b=rng.integers(-30,31,125)
            np.save(src/'activations.npy',np.column_stack((a,b,a+b,rng.normal(size=125))).astype('float32'))
            with (src/'probes.jsonl').open('w') as f:
                for x,y in zip(a,b):f.write(json.dumps({'a':int(x),'b':int(y),'answer':int(x+y)})+'\n')
            result=run(src,Path(tmp)/'out',landmarks=24,rank=3,ks=(8,),alphas=(.1,))
            self.assertEqual(result['train']+result['holdout'],125)
            self.assertEqual(len(result['validation_trials']),2)
            self.assertTrue((Path(tmp)/'out'/'heldout_predictions.csv').exists())
            self.assertGreater(result['scores']['constant']['mae'],result['scores']['local']['mae'])
if __name__=='__main__':unittest.main()
