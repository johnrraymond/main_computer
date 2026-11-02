import unittest
import numpy as np
from tools.nanojev_qwen_game_geometry_analyze import local_block, rankcorr
class NumericGeometryTests(unittest.TestCase):
    def test_rankcorr(self):
        self.assertAlmostEqual(rankcorr([1,2,3],[4,5,6]),1)
        self.assertIsNone(rankcorr([1,1,1],[3,2,1]))
    def test_controlled_triple(self):
        rows=[{'game':'X','level':0,'offset':o,'answer':o} for o in (-1,0,1)]
        d=np.array([[0,1,2],[1,0,1],[2,1,0]],float)
        out=local_block(rows,d)
        self.assertEqual(out['triples'],1)
        self.assertEqual(out['far_to_near_ratio'],2)
        self.assertEqual(out['far_closer_than_adjacent_rate'],0)
if __name__=='__main__': unittest.main()
