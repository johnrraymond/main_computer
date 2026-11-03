import json, tempfile, unittest
from pathlib import Path
import numpy as np
from tools.nanojev_qwen_centroid_numeracy import analyze, summarize

class Tests(unittest.TestCase):
    def test_scores(self):
        s=summarize(np.array([1.,2.,3.]),np.array([1.,3.,3.]))
        self.assertAlmostEqual(s['rounded_exact'],2/3)
        self.assertAlmostEqual(s['mae'],1/3)
    def test_saved_arrays(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'C4_T00';p.mkdir()
            rng=np.random.default_rng(4)
            n=160; a=rng.integers(-70,70,n);b=rng.integers(-70,70,n)
            y=a+b
            x=np.stack((a,b,y,rng.normal(size=n)),axis=-1).astype('float32')
            np.save(p/'activations.npy',x)
            (p/'probes.jsonl').write_text(''.join(json.dumps({'a':int(i),'b':int(j),'answer':int(k)})+'\n' for i,j,k in zip(a,b,y)))
            report=analyze(p,rank=3,pairs=200,knn_k=5)
            self.assertEqual(report['checks']['answer_arithmetic_disagreements'],0)
            self.assertEqual(report['points'],n)
            self.assertIn('answers',report['decoders'])
    def test_bad_labels(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'C4_T00';p.mkdir()
            np.save(p/'activations.npy',np.ones((30,4),dtype='float32'))
            (p/'probes.jsonl').write_text(''.join(json.dumps({'a':1,'b':1,'answer':4})+'\n' for _ in range(30)))
            with self.assertRaises(ValueError):analyze(p)
if __name__=='__main__':unittest.main()
