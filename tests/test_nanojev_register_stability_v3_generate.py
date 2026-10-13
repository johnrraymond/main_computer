"""Offline, model-free checks for the 23,552-prompt register v3 generator."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'tools' / 'nanojev_register_stability_v3_generate.py'
spec = importlib.util.spec_from_file_location('register_v3', SCRIPT)
reg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reg)


class TestRegisterV3(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pairs, cls.tasks, cls.op_counts, cls.family_counts = reg.build_plan()

    def test_exact_ceiling(self):
        self.assertEqual(len(self.tasks), 23552)
        self.assertLessEqual(len(self.tasks), 24000)
        self.assertEqual(Counter(t['cohort'] for t in self.tasks), {'main':20480,'control':3072})
        self.assertEqual(len(self.pairs), 2688)

    def test_all_unique(self):
        self.assertEqual(len({(p['a'], p['b']) for p in self.pairs}),2688)
        self.assertEqual(len({t['sha256'] for t in self.tasks}),23552)
        self.assertTrue(all(reg.digest(t['prompt'])==t['sha256'] for t in self.tasks))

    def test_full_operation_control(self):
        by_pair=Counter(t['pair_id'] for t in self.tasks)
        for p in self.pairs:
            self.assertEqual(by_pair[p['pair_id']],24 if p['cohort']=='control' else 8)
        self.assertEqual(len({t['operation'] for t in self.tasks if t['cohort']=='control'}),24)

    def test_heldout_ops(self):
        self.assertFalse(set(reg.HELD_OUT_OPERATIONS) & {
            t['operation'] for t in self.tasks if t['cohort']=='main'})
        self.assertTrue(set(reg.HELD_OUT_OPERATIONS) <= {
            t['operation'] for t in self.tasks if t['cohort']=='control'})

    def test_diverse_families_each_main_pair(self):
        by_pair={}
        for t in self.tasks:
            if t['cohort']=='main': by_pair.setdefault(t['pair_id'],set()).add(t['family'])
        self.assertTrue(all(families==set(reg.FAMILIES) for families in by_pair.values()))

    def test_reversed_groups_together(self):
        seen={}
        for p in self.pairs:
            k=p['pair_group']
            state=(p['cohort'],p['split'])
            self.assertEqual(seen.setdefault(k,state),state)
        self.assertTrue(all(p['cohort']=='control' for p in self.pairs if p['split']=='control'))

    def test_grid_and_paired_format(self):
        self.assertEqual(len(reg.numeric_grid()),507)
        self.assertEqual(reg.logbar(0),'.')
        self.assertEqual(reg.logbar(-1),'-.|||||||')
        sample=reg.render(2_000_000,-3,reg.OPERATIONS[0])
        self.assertIn('XXXXXX = 2000000, ',sample)
        self.assertIn('YYYYYY = -3, ',sample)
        self.assertEqual(sample.count('\n'),3)
        self.assertTrue(sample.endswith('\nAnswer:'))
        self.assertNotIn('2000000',sample.split('operation: ')[1])

    def test_reproducibility_and_new_seed(self):
        _,again,_,_=reg.build_plan()
        self.assertEqual([t['sha256'] for t in self.tasks], [t['sha256'] for t in again])
        _,different,_,_=reg.build_plan(seed=20261014)
        self.assertNotEqual(self.tasks[0]['sha256'],different[0]['sha256'])

    def test_fixed_budget_guard(self):
        for max_prompts in (23000,24001):
            with self.assertRaises(ValueError): reg.build_plan(max_prompts=max_prompts)
        with self.assertRaises(ValueError): reg.assign_pairs(123,2048,128)

    def test_generate_check_and_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'capture'
            args=reg.parser().parse_args(['--generate-only','--output-root',str(root)])
            m,tasks=reg.setup(args)
            self.assertEqual(m['expected_unique_prompts'],23552)
            self.assertEqual(len((root/'prompts.jsonl').read_text().splitlines()),23552)
            self.assertEqual(reg.setup(args)[0],m)
            self.assertFalse(reg.check_capture(args,tasks))
            altered=reg.parser().parse_args(['--generate-only','--seed','42','--output-root',str(root)])
            with self.assertRaisesRegex(RuntimeError,'DRIFT'): reg.setup(altered)

    def test_dry_run_creates_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'no_files'
            args=reg.parser().parse_args(['--prepare-only','--output-root',str(root)])
            reg.setup(args,write=False)
            self.assertFalse(root.exists())

    def test_capture_with_dummy_model_and_resume(self):
        """Verify capture shape, storage layer map, atomic records, and no-model resumption."""
        import types
        import numpy as np
        import threadpoolctl
        class FakeCassette:
            def load_source(self, model, args):
                return None,object(),None,'cpu',{'revision':'offline-test'}
        def fake_capture(torch,model,tokenizer,text,device,limit,store_dtype):
            length=5
            states=np.ones((29,length,4),dtype=np.float16)
            return {'prompt':text,'sha256':reg.digest(text),'input_ids':[1]*length,
                    'input_token_count':length,'shape':list(states.shape)},states
        fake=types.ModuleType('nanojev_full_activation_sparse_hierarchy')
        fake.cassette=FakeCassette()
        fake.capture_full=fake_capture
        with tempfile.TemporaryDirectory() as directory,patch.dict(sys.modules,{
            'nanojev_full_activation_sparse_hierarchy':fake}):
            args=reg.parser().parse_args(['--output-root',directory,'--capture-only',
                                          '--max-new-captures','3','--report-every','1'])
            _,tasks=reg.setup(args)
            reg.capture(args,tasks)
            paths=list((Path(directory)/'records').glob('*.npz'))
            self.assertEqual(len(paths),3)
            with np.load(paths[0],allow_pickle=False) as data:
                self.assertEqual(data['hidden_states'].shape,(2,5,4))
                md=json.loads(str(data['metadata'].item()))
                self.assertEqual(md['stored_layers'],[16,28])
            args.resume=True
            reg.capture(args,tasks)
            self.assertEqual(len(list((Path(directory)/'records').glob('*.npz'))),6)
            # Refuse resumed records whose source digest no longer matches.
            with np.load(paths[0],allow_pickle=False) as data:
                md=json.loads(str(data['metadata'].item()))
                tensor=data['hidden_states']
            md['sha256']='incorrect'
            with paths[0].open('wb') as stream:
                np.savez_compressed(stream,metadata=json.dumps(md),hidden_states=tensor)
            with self.assertRaisesRegex(RuntimeError,'INVALID'):
                reg.capture(args,tasks)


if __name__=='__main__':
    unittest.main()
