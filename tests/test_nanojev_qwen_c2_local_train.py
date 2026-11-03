import unittest
from tools.nanojev_qwen_c2_local_train import answer,prompt,parse_centers,local_points,deck,split_deck,deterministic_test_deck

class C2Local(unittest.TestCase):
    def test_contract(self):
        self.assertEqual(prompt(3,4,20,10),'PROBLEM: (3*20)-(4*10)')
        self.assertEqual(answer(3,4,20,10),20)
    def test_neighborhood(self):
        center=(3,4,20,10)
        self.assertEqual(len(local_points(center,4)),81)
        pts=deck((center,),4,47)
        self.assertEqual(len(pts),len(set(pts)))
        tr,te=split_deck(pts,47)
        self.assertFalse(set(tr)&set(te))
        shell=deterministic_test_deck((center,),4,47)
        self.assertFalse(set(pts)&set(shell))
    def test_overlap(self):
        c=((3,4,20,10),(3,4,22,12))
        pts=deck(c,4,47)
        self.assertLess(len(pts),162)
        self.assertTrue(all(answer(*x)==x[0]*x[2]-x[1]*x[3] for x in pts))
    def test_coefficients(self):
        self.assertGreater(len(deck(((3,4,20,10),),1,47,vary_coefficients=True)),9)
        self.assertEqual(parse_centers('3,4,20,10;4,5,30,20')[1],(4,5,30,20))
if __name__=='__main__':unittest.main()
