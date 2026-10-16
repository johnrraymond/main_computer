"""Pass-one captain numeracy task bank contract (stdlib only; no model/GPU)."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import nanojev_captain_numeracy_populate as n
from nanojev_training_task_store import TrainingTaskStore


class CaptainNumeracyPassOneTests(unittest.TestCase):
    def test_all_24_operations_and_every_frame_render_correctly(self):
        n.catalogue_check()
        self.assertEqual(len(n.SEMANTIC),24)
        self.assertEqual(len(n.TRAIN_OPERATIONS),20)
        for operation, semantic in n.SEMANTIC.items():
            for x,y in ((18,7),(2700,2500),(17,-3),(2000,200),(0,0),
                        (-2_000_000,2_000_000)):
                for row in n.FRAMES[semantic]:
                    with self.subTest(op=operation,frame=row.frame_id,x=x,y=y):
                        presentations=n.presentations(x,y,operation,row)
                        self.assertEqual(len(presentations),len(n.VARIANTS)*2)
                        self.assertTrue(all(p['prompt'].endswith('Answer:')
                                            for p in presentations.values()))
                        self.assertTrue(isinstance(n.truth(x,y,semantic),(int,float)))
                        self.assertEqual(presentations['registered_logbar__forward']['prompt'],
                                         n.registers.render(x,y,n.OPERATIONS[operation]))
                        self.assertNotIn('XXXXXX',presentations['story_raw__forward']['prompt'])
                        self.assertNotIn('YYYYYY',presentations['story_raw__forward']['prompt'])
                        self.assertNotIn('LogBar',presentations['story_raw__forward']['prompt'])
                        self.assertNotIn('operation:',presentations['story_raw__forward']['prompt'])

    def test_oracle_agrees_with_original_known_truth(self):
        expected={
            'add':14, 'subtract':20, 'reverse_subtract':-20,
            'multiply':-51, 'absolute_difference':20, 'maximum':17,
            'greater_than':1, 'average':7, 'minimum':-3,
            'positive_select':-3, 'absolute_greater_than':1,
            'double_subtract':37, 'double_sum':28, 'square_sum':196,
        }
        for operation,semantic in n.SEMANTIC.items():
            with self.subTest(operation=operation):
                self.assertEqual(n.truth(17,-3,semantic), expected[semantic])

    def test_numeric_pool_has_comparisons_zero_and_sign_boundaries(self):
        pairs=n.generate_pairs(20261015,2000)
        self.assertGreater(sum(a==b for a,b in pairs),50)
        self.assertGreater(sum(a==0 or b==0 for a,b in pairs),50)
        self.assertGreater(sum(a*b<0 for a,b in pairs),250)
        self.assertGreater(sum(0<abs(a-b)<=5 for a,b in pairs),50)
        self.assertEqual(len({n.pair_key(a,b) for a,b in pairs}),len(pairs))

    def test_scaffolding_fades_a_single_problem_without_target_change(self):
        x,y,op=2700,2500,'written_sub'
        row=n.FRAMES[n.SEMANTIC[op]][0]
        p=n.presentations(x,y,op,row)
        def text(variant,order='forward'):
            return p[variant+'__'+order]['prompt']
        self.assertIn(n.registers.logbar(x),text('story_full_logbar'))
        self.assertIn(n.registers.logbar(y),text('story_full_logbar'))
        self.assertNotIn('|',text('story_numeric_registers'))
        self.assertIn('XXXXXX = 2700',text('story_numeric_x_semantic_y'))
        self.assertIn('YYYYYY = '+row.y_role,text('story_numeric_x_semantic_y'))
        self.assertIn('XXXXXX = '+row.x_role,text('story_semantic_no_op'))
        self.assertNotIn('operation:',text('story_semantic_no_op'))
        self.assertNotIn('XXXXXX',text('story_raw'))
        self.assertNotEqual(text('story_raw'),text('story_raw','reordered'))
        self.assertEqual(n.truth(x,y,n.SEMANTIC[op]),200)

    def test_train_reserved_operation_and_pair_split_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'a'
            counts={'train':80,'dev':20,'predev':20,'test':20}
            result=n.populate(path,seed=91,counts=counts,include_heldout_eval=True)
            self.assertEqual(result['groups'],140)
            self.assertEqual(result['presentations'],140*len(n.VARIANTS)*2)
            self.assertEqual(n.verify_bank(path)['groups'],140)
            with TrainingTaskStore(path/'training-tasks.sqlite') as db:
                self.assertEqual(n.verify_split_pairs(db),counts)
                for split in n.SPLITS:
                    examples=list(db.iter_examples(split,'story_raw__forward',family=n.FAMILY))
                    self.assertEqual(len(examples),counts[split])
                    for row in examples:
                        target=row['target']
                        self.assertEqual(target['result'],n.truth(target['a'],target['b'],
                                                                  n.SEMANTIC[row['task']]))
                        self.assertEqual(row['labelStatus'],'verified')
                        if split=='train':
                            self.assertNotIn(row['task'],n.HELD_OUT)
                with self.assertRaisesRegex(ValueError,'TASK_BANK_SPLIT_RESERVE'):
                    db.claim(next(db.iter_examples('dev','story_raw__forward'))['groupId'],'train')

    def test_hash_reproducibility_and_fail_closed_existing_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            counts={'train':31,'dev':5,'predev':5,'test':5}
            n.populate(root/'one',seed=43,counts=counts)
            n.populate(root/'two',seed=43,counts=counts)
            with TrainingTaskStore(root/'one'/'training-tasks.sqlite') as a, \
                 TrainingTaskStore(root/'two'/'training-tasks.sqlite') as b:
                for split in n.SPLITS:
                    self.assertEqual(list(a.iter_examples(split,'story_raw__forward')),
                                     list(b.iter_examples(split,'story_raw__forward')))
            with self.assertRaises(FileExistsError):
                n.populate(root/'one',seed=43,counts=counts)
            # Manifest pins the bytes of the stored corpus.
            db_file=root/'two'/'training-tasks.sqlite'
            with db_file.open('ab') as f: f.write(b'corrupted')
            with self.assertRaisesRegex(RuntimeError,'CAPTAIN_DATABASE_SHA256_MISMATCH'):
                n.verify_bank(root/'two')

    def test_previews_and_reversed_order_do_not_swap_targets(self):
        result=n.preview(20261015)
        self.assertEqual(len(result['examples']),3)
        for item in result['examples']:
            self.assertNotEqual(item['presentations']['story_raw__forward'],
                                item['presentations']['story_raw__reordered'])
            self.assertNotIn('XXXXXX',item['presentations']['story_raw__forward'])


if __name__=='__main__':
    unittest.main()
