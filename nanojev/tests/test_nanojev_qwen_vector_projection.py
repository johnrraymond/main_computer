import json,tempfile,unittest
from pathlib import Path
import numpy as np
from tools.nanojev_qwen_vector_projection import local_affine,evaluate_population

class LocalProjectionTests(unittest.TestCase):
    def test_plane_removes_orthogonal_displacement(self):
        ref=np.array([[-2.,0.],[-1.,0.],[0.,0.],[1.,0.],[2.,0.]])
        q=np.array([[.3,4.]])
        out=local_affine(q,ref,np.array([[0,1,2,3,4]]),1,1.)
        np.testing.assert_allclose(out,[[.3,0.]],atol=1e-9)
        np.testing.assert_allclose(local_affine(q,ref,np.array([[0,1,2,3,4]]),1,0),q)
    def test_end_to_end_holdout(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);rng=np.random.default_rng(12)
            x=rng.normal(size=(180,24));y=5*x[:,0]-2*x[:,1]+rng.normal(size=180)
            np.save(root/'activations.npy',x.astype('float32'))
            with (root/'probes.jsonl').open('w') as f:
                for v in y:f.write(json.dumps({'answer':float(v)})+'\n')
            r,rows=evaluate_population(root,holdout=40,rank=6,ks=(8,16),local_ranks=(1,2,4),alphas=(.25,1.))
            self.assertEqual(len(rows),40);self.assertEqual(r['holdout'],40)
            self.assertIn(r['selected']['method'],('identity','mean','local_pca'))
            self.assertTrue(np.isfinite(r['holdout_modified']['mae']))
            self.assertTrue(any(t['method']=='local_pca' for t in r['trials']))
if __name__=='__main__':unittest.main()
