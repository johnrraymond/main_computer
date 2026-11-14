import unittest
import numpy as np
from tools.nanojev_qwen_game_geometry_dense_smoke import probes,distances,classical_landmark,extend,choose_landmarks
class TestDenseGeometry(unittest.TestCase):
    def test_population(self):
        r=probes();self.assertEqual(len(r),900)
        r=probes(offsets=range(-16,17));self.assertEqual(len(r),9900)
        self.assertEqual(len(set(x['game'] for x in r)),100)
    def test_landmark_coordinates(self):
        x=np.random.default_rng(1).normal(size=(10,6));D=distances(x,x,'euclidean')
        a,b,u,l,z,meta=classical_landmark(D)
        self.assertTrue(np.allclose(z,extend(D,a,b,u,l),atol=1e-7))
        self.assertEqual(z.shape,(10,2))
    def test_all_metrics(self):
        x=np.random.default_rng(3).normal(size=(8,6))
        for k in ('euclidean','manhattan','cosine','complex_log','whitened'):
            d=distances(x,x,k,np.mean(x,axis=0),np.maximum(np.std(x,axis=0),1e-6))
            self.assertTrue(np.isfinite(d).all());self.assertTrue(np.allclose(d,d.T))
if __name__=='__main__': unittest.main()
