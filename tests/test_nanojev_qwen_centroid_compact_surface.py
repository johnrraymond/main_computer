import json,tempfile,unittest
from pathlib import Path
import numpy as np
from tools.nanojev_qwen_centroid_compact_surface import experiment, kernel, choose_centers
class TestCompactSurface(unittest.TestCase):
    def test_basis(self):
        x=kernel(np.array([[0.,2.]]),1.)
        self.assertAlmostEqual(x[0,0],1.)
        self.assertEqual(len(choose_centers(np.zeros((100,3)),16,47)),16)
    def test_end_to_end(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)/'C4_T00';root.mkdir()
            rng=np.random.default_rng(22);v=np.linspace(-5,5,240)
            X=np.column_stack((v,np.sin(v),rng.normal(0,.025,(len(v),8)))).astype('float32')
            np.save(root/'activations.npy',X)
            (root/'probes.jsonl').write_text(''.join(json.dumps({'answer':float(z)})+'\n' for z in v))
            out=Path(t)/'out';r=experiment(root,out,landmarks=35,rank=6,center_counts=(12,24))
            self.assertEqual(r['train'],216)
            self.assertEqual(r['holdout'],24)
            self.assertLess(r['surface']['mae'],r['constant']['mae'])
            self.assertTrue((out/'surface_model.npz').is_file())
            self.assertTrue((out/'heldout_predictions.csv').is_file())
if __name__=='__main__':unittest.main()
