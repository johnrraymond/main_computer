import json, tempfile, unittest
from pathlib import Path
import numpy as np
from tools.nanojev_qwen_centroid_surface import analyze, fit_landmark, project

class TestSurface(unittest.TestCase):
    def test_out_of_sample_projection(self):
        rng=np.random.default_rng(5);X=rng.normal(size=(110,9));tr=np.arange(90)
        m=fit_landmark(X,tr,'euclidean',6,30,3)
        self.assertTrue(set(m['ids']).issubset(set(tr)))
        z=project(X,m,'euclidean');self.assertEqual(z.shape,(110,m['rank']))
        self.assertTrue(np.isfinite(z).all())
    def test_smooth_synthetic(self):
        rng=np.random.default_rng(8)
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'C4_T00';p.mkdir();y=np.arange(-110,110,dtype=float)
            X=np.column_stack([y/50., (y/50.)**2, rng.normal(0,.01,(len(y),12))]).astype('float32')
            np.save(p/'activations.npy',X)
            (p/'probes.jsonl').write_text(''.join(json.dumps({'id':str(i),'answer':v})+'\n' for i,v in enumerate(y)))
            out=Path(t)/'out';r=analyze(p,out,landmarks=48,rank=8)
            self.assertEqual(r['training']+r['test_n'],220)
            self.assertLess(r['heldout']['mae'],r['constant_median']['mae'])
            self.assertTrue((out/'heldout_predictions.csv').exists())
if __name__=='__main__':unittest.main()
