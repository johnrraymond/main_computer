import unittest
from tools.nanojev_qwen_five_centroids import POPS, make_rows

class TestFiveCentroids(unittest.TestCase):
    def test_source_contracts_and_uniqueness(self):
        for name in POPS:
            rows=make_rows(name,120,seed=47)
            self.assertEqual(len(rows),120)
            self.assertEqual(len({r['prompt'] for r in rows}),120)
            self.assertTrue(all(isinstance(r['answer'],int) for r in rows))
    def test_t00_canonical_signed_literals(self):
        rows=make_rows('C4_T00',200)
        for r in rows:
            a,b=r['a'],r['b']
            lit=lambda n:str(n) if n>=0 else f'({n})'
            self.assertEqual(r['prompt'],f'PROBLEM: {lit(a)}+{lit(b)}')
            self.assertEqual(r['answer'],a+b)
            self.assertLessEqual(abs(r['answer']),16)
    def test_reproducible(self):
        self.assertEqual(make_rows('C1_shotgun_language',40),make_rows('C1_shotgun_language',40))

if __name__=='__main__':unittest.main()
