import unittest
import numpy as np
from tools.nanojev_qwen_five_centroids import make_rows
from tools.nanojev_qwen_c0_compressive_train import (
    c0_stage_rows, split, signed_log, inverse_signed_log,
    VARIANTS, resolve_variants, holdout_key
)
class C0ScaleTrainingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows=make_rows('C0_games', n=10000, seed=47)
    def test_original_c0_prompt_and_labels(self):
        row=self.rows[0]
        self.assertIn(row['game'],row['prompt'])
        self.assertEqual(row['answer'],row['p']*row['a']-row['q']*row['b'])
        self.assertIn('signed payoff',row['prompt'])
    def test_scale_bands_and_replay(self):
        a=c0_stage_rows(self.rows,1,300,47)
        tr,va=split(a,47)
        b=c0_stage_rows(self.rows,10,300,47,replay=tr,fraction=.25)
        tr2,va2=split(b,47)
        self.assertEqual(len(a),300)
        self.assertEqual(len(b),300)
        self.assertFalse({r['prompt'] for r in tr}&{r['prompt'] for r in va})
        self.assertFalse({r['prompt'] for r in tr}&{r['prompt'] for r in va2})
        self.assertTrue(any(r['scale']==1 for r in b))
        self.assertTrue(any(r['scale']==10 for r in b))
    def test_signed_log_invertible_in_test_range(self):
        arr=np.array([-5e7,-15.,0.,15.,5e7])
        np.testing.assert_allclose(inverse_signed_log(signed_log(arr)),arr,rtol=1e-12,atol=1e-6)
    def test_all_five_default(self):
        self.assertEqual(resolve_variants('all'),tuple(VARIANTS))
    def test_holdout_stability(self):
        row=self.rows[21]
        self.assertEqual(holdout_key(row,47),holdout_key(dict(row),47))
if __name__=='__main__': unittest.main()

class IndependentTrainingTests(unittest.TestCase):
    def test_neural_loss_is_exact_only_and_gradients_flow(self):
        import torch
        from tools.nanojev_qwen_c0_compressive_train import neural_loss
        corrected=torch.tensor([0.7,-0.2,1.4],requires_grad=True)
        uncorrected=torch.tensor([0.2,-0.5,0.3],requires_grad=True)
        target=torch.tensor([0.0,0.5,-0.3])
        loss=neural_loss(torch,corrected,uncorrected,target,beta=.1,aux_weight=.25)
        expected=(torch.nn.functional.smooth_l1_loss(corrected,target,beta=.1)
                 +.25*torch.nn.functional.smooth_l1_loss(uncorrected,target,beta=.1))
        self.assertAlmostEqual(loss.item(),expected.item(),places=7)
        loss.backward()
        self.assertTrue(torch.any(corrected.grad!=0))
        self.assertTrue(torch.any(uncorrected.grad!=0))

    def test_reported_teacher_is_independent_from_training_loss(self):
        import ast,inspect
        from tools import nanojev_qwen_c0_compressive_train as m
        src=inspect.getsource(m.run)
        tree=ast.parse(src)
        calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call)]
        neural_calls=[n for n in calls if isinstance(n.func,ast.Name) and n.func.id=='neural_loss']
        self.assertEqual(len(neural_calls),1)
        self.assertNotIn('teacher_tr',src)
        self.assertNotIn('imitation',src)
        self.assertNotIn('teacher_weight*',src)
        self.assertIn('teacher_val',src)
        self.assertIn('teacher_outer',src)
