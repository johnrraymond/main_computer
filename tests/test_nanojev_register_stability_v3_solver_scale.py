"""Focused tests for staged full-coordinate LASSO training on v3 capture."""
from __future__ import annotations
from collections import Counter
import unittest
import numpy as np
import nanojev_register_stability_v3_analyze as ana


class TestStagedV3Fitting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _, cls.tasks, _, _ = ana.gen.build_plan()
        cls.train = np.asarray([i for i,t in enumerate(cls.tasks) if t['split']=='train'],dtype=np.int32)

    def test_each_pair_uses_different_operation_families(self):
        for count in (2,4,8):
            chosen=ana.choose_fit_rows(self.tasks,self.train,count,20261013)
            self.assertEqual(len(chosen),2004*count)
            self.assertEqual(len(set(chosen.tolist())),len(chosen))
            pairs={}
            for local in chosen:
                t=self.tasks[int(self.train[local])]
                self.assertEqual(t['split'],'train')
                pairs.setdefault(t['pair_id'],[]).append(t)
            self.assertEqual(len(pairs),2004)
            self.assertTrue(all(len(group)==count for group in pairs.values()))
            if count!=8:
                self.assertTrue(all(len(set(t['family'] for t in group))==count for group in pairs.values()))
            totals=Counter(self.tasks[int(self.train[row])]['family'] for row in chosen)
            self.assertEqual(sum(totals.values()),len(chosen))
            if count==2:
                self.assertLess(max(totals.values())-min(totals.values()),400)

    def test_sampling_repeatable_independent_of_holdouts(self):
        a=ana.choose_fit_rows(self.tasks,self.train,2,42)
        b=ana.choose_fit_rows(self.tasks,self.train,2,42)
        np.testing.assert_array_equal(a,b)
        self.assertFalse(any(self.tasks[int(self.train[row])]['operation'] in ana.gen.HELD_OUT_OPERATIONS for row in a))

    def test_zero_head_uses_all_rare_positives(self):
        vals=np.asarray([self.tasks[int(i)]['a'] for i in self.train])
        mask=ana.choose_zero_head_rows(self.tasks,self.train,vals,1,8,20261013)
        nzero=int(np.sum(vals==0))
        self.assertEqual(nzero,104)
        self.assertTrue(mask[vals==0].all())
        self.assertEqual(int(mask.sum()),nzero*9)
        self.assertEqual(int(np.sum(mask&(vals!=0))),nzero*8)
        families=Counter(self.tasks[int(self.train[i])]['family'] for i in np.flatnonzero(mask&(vals!=0)))
        self.assertLess(max(families.values())-min(families.values()),20)
        np.testing.assert_array_equal(mask,ana.choose_zero_head_rows(self.tasks,self.train,vals,1,8,20261013))

    def test_fit_mask_applies_to_all_heads_except_zero(self):
        n=16
        vals=np.asarray([0,0,1,2,3,4,-1,-2,-3,-4,6,7,-6,-7,8,-8],dtype=float)
        fit_mask=np.zeros(n,dtype=bool);fit_mask[0:10]=True
        zero_mask=np.zeros(n,dtype=bool);zero_mask[[0,1,3,5,7,10,12]]=True
        class Spy:
            def __init__(self): self.calls={};self.heads={}
            def set_ratio(self,ratio): pass
            def fit(self,name,subset,response):
                self.calls[name]=(np.asarray(subset).copy(),np.asarray(response).copy())
                self.heads[name]={'nonzero':0}
        spy=Spy()
        ana.fit_single_position(np.zeros((n,3),dtype=np.float32),vals,(2,5),spy,.03,
                                fit_mask=fit_mask,zero_mask=zero_mask)
        np.testing.assert_array_equal(spy.calls['zero'][0],zero_mask)
        np.testing.assert_array_equal(spy.calls['zero'][1],np.where(vals[zero_mask]!=0,-1.,1.))
        for name,(mask,_) in spy.calls.items():
            if name!='zero': self.assertFalse(np.any(mask & ~fit_mask),name)

    def test_split_zero_reporting_with_absent_validation_zeros(self):
        tasks=[dict(pair_id=i,operation='sym_add',family='symbolic',a=a,b=b)
               for i,(a,b) in enumerate([(0,3),(5,6),(-4,0)])]
        pred=np.asarray([[0.,3.],[0.,6.],[-4.,0.]])
        score=ana.summarize_scores(tasks,[0,1,2],pred,'val',set())
        self.assertEqual(score['zero_detection']['position_1']['zero_recall'],1.)
        self.assertEqual(score['zero_detection']['position_1']['nonzero_false_zero_rate'],.5)
        tasks2=[{**t,'a':t['a'] or 1,'b':t['b'] or 1} for t in tasks]
        score2=ana.summarize_scores(tasks2,[0,1,2],pred,'val',set())
        self.assertIsNone(score2['zero_detection']['position_1']['zero_recall'])

if __name__=='__main__': unittest.main()
