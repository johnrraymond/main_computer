import unittest
import sys
import types
import numpy as np
from tools.nanojev_qwen_c0_balance import (
    exact_balanced_case,make_balance_deck,summarize_balance,save_balance_report)

class BalanceAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Only the centroids name list is needed for synthetic unit tests.
        from tools import nanojev_qwen_c0_balance as m
        cls.old=sys.modules.get('tools.nanojev_qwen_five_centroids')
        stub=types.ModuleType('tools.nanojev_qwen_five_centroids')
        stub.NAMES=tuple(('family',f'game_{i}') for i in range(100))
        sys.modules['tools.nanojev_qwen_five_centroids']=stub
        cls.deck=make_balance_deck(deltas=(0,1,2,4,8,16,32,64),magnitudes=(100,1000),samples_per_cell=8,seed=47)

    @classmethod
    def tearDownClass(cls):
        if cls.old is not None:sys.modules['tools.nanojev_qwen_five_centroids']=cls.old
        else:sys.modules.pop('tools.nanojev_qwen_five_centroids',None)

    def test_all_labels_exact(self):
        for r in self.deck:
            self.assertEqual(r['p']*r['a']-r['q']*r['b'],r['answer'])
            self.assertEqual(abs(r['answer']),r['imbalance'])
            self.assertGreater(r['a'],0)
            self.assertGreater(r['b'],0)

    def test_balanced_cells(self):
        from collections import Counter
        cells=Counter((r['magnitude_target'],r['imbalance'],r['sign']) for r in self.deck)
        self.assertEqual(cells[100,0,0],8)
        self.assertEqual(cells[100,1,1],8)
        self.assertEqual(cells[100,1,-1],8)
        self.assertEqual(cells[100,32,1],0) # magnitude constraint: 32 > 100/4
        self.assertEqual(cells[1000,64,-1],8)

    def test_independent_predictions_and_zero(self):
        y=np.asarray([r['answer'] for r in self.deck],float)
        report=summarize_balance(self.deck,{'exact':y,'shifted':y+1})
        self.assertEqual(report['exact']['overall']['mae'],0)
        self.assertEqual(report['exact']['overall']['rounded_exact_pct'],100)
        self.assertEqual(report['shifted']['overall']['mae'],1)
        self.assertEqual(next(x for x in report['exact']['by_delta'] if x['delta']==0)['zero_hit_pct'],100)
        self.assertEqual(len(report['exact']['cells']),len({(r['magnitude_target'],r['imbalance'],r['sign']) for r in self.deck}))

    def test_csv_and_json_outputs(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as t:
            y=np.asarray([r['answer'] for r in self.deck],float)
            report=save_balance_report(t,1,self.deck,{'encoder.zdoubleprime':y},dict(samples=8))
            self.assertEqual(report['models']['encoder.zdoubleprime']['overall']['mae'],0)
            self.assertTrue((Path(t)/'balance_cells.csv').exists())
            self.assertTrue((Path(t)/'balance_predictions.csv').exists())
            self.assertTrue((Path(t)/'balance_report.json').exists())

    def test_no_example_leakage(self):
        old={self.deck[0]['prompt']}
        rows=make_balance_deck(deltas=(0,1),magnitudes=(100,),samples_per_cell=8,seed=47,excluded_prompts=old)
        self.assertFalse(old & {r['prompt'] for r in rows})

if __name__=='__main__':unittest.main()
