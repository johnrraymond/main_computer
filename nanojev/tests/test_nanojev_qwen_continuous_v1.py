"""GPU-free tests for the NanoJev Qwen exact-resume supervisor."""
import importlib.util
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / 'tools' / 'nanojev_qwen_continuous_v1.py'
sp = importlib.util.spec_from_file_location('nanojev_qwen_continuous_v1', SRC)
app = importlib.util.module_from_spec(sp)
sp.loader.exec_module(app)

CFG = {
    'backbone': 'qwen', 'precision_weight': 0.0,
    'parent_v1': {'sha256': 'v1parent', 'source_root': 'x'},
    'model_info': {'dtype': 'torch.float16', 'device': 'cuda'},
    'seed': 20261013, 'width': 128, 'batch_size': 16, 'lr': .0003,
    'val_examples': 128, 'test_examples': 128, 'max_prompt_tokens': 256,
    'precision_relative_tolerance': .05, 'precision_huber_beta': 1.0,
}


def fake_ckpt(step, rank=None, asinh=.04):
    return {'schema': app.EXPECTED_CHECKPOINT_SCHEMA, 'backbone': 'qwen',
            'config': CFG.copy(), 'step': step, 'stream': {'rng': 'test'},
            'optimizer': {}, 'torch_rng': 'test',
            'best': {'precision': {'step': step, 'rank': rank or [.02, .005, 0, -8]},
                     'asinh': {'step': step, 'score': asinh}}}


class SupervisorTests(unittest.TestCase):
    def test_checkpoints_reject_nonzero_weight_and_wrong_backbone(self):
        cfg, step = app.inspect_checkpoint(fake_ckpt(5700))
        self.assertEqual(step, 5700)
        self.assertEqual(cfg['precision_weight'], 0)
        bad = fake_ckpt(5700)
        bad['config']['precision_weight'] = .01
        with self.assertRaisesRegex(RuntimeError, 'WEIGHT_ZERO'):
            app.inspect_checkpoint(bad)
        bad = fake_ckpt(5700)
        bad['backbone'] = 'tinystories'
        with self.assertRaisesRegex(RuntimeError, 'WRONG_CHECKPOINT'):
            app.inspect_checkpoint(bad)

    def test_exact_resume_inherits_full_training_config(self):
        cmd = app.build_command('python', 'trainer.py', Path('run'), CFG, 1250, False)
        self.assertIn('--resume', cmd)
        for name, val in [('additional-steps', '1250'), ('precision-weight', '0.0'),
                          ('val-examples', '128'), ('seed', '20261013')]:
            idx = cmd.index('--' + name)
            self.assertEqual(cmd[idx + 1], val)
        self.assertNotIn('--allow-model-download', cmd)
        self.assertEqual(cmd[cmd.index('--dtype') + 1], 'float16')

    def test_crash_recovery_uses_remaining_steps_not_full_new_cycle(self):
        state = {'active_cycle': {'number': 2, 'start_step': 1000, 'target_step': 3000},
                 'cycles': [{}]}
        active, remaining = app.next_cycle(state, 2400, 2000)
        self.assertEqual(remaining, 600)
        self.assertEqual(active['target_step'], 3000)
        with self.assertRaisesRegex(RuntimeError, 'STEP_DRIFT'):
            app.next_cycle(state, 3200, 2000)

    def test_journal_is_placed_outside_results_and_archives_best(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            root, meta = d/'run', d/'journal'
            root.mkdir()
            for name in ('latest.pt', 'best-precision.pt', 'best-asinh.pt'):
                (root/name).write_bytes(name.encode())
            ckpt = fake_ckpt(1000)
            state = app.load_or_initialize(meta/'campaign-state.json', CFG, 'scriptsha', ckpt, root, meta)
            self.assertTrue((meta/'baseline'/'latest.pt').is_file())
            self.assertEqual(sorted(p.name for p in root.iterdir()),
                             ['best-asinh.pt', 'best-precision.pt', 'latest.pt'])
            active, remaining = app.next_cycle(state, 1000, 2000)
            self.assertEqual(remaining, 2000)
            state['active_cycle'] = active
            app.write_json_atomic(meta/'campaign-state.json', state)
            # Interruption after the last training checkpoint but before the
            # supervisor finalization is recoverable on the next invocation.
            resumed = app.load_or_initialize(meta/'campaign-state.json', CFG,
                                             'scriptsha', ckpt, root, meta)
            self.assertEqual(app.next_cycle(resumed, 3000, 2000)[1], 0)
            app.write_json_atomic(root/'test-last.json', {'step': 3000})
            record = app.finalize_cycle(resumed, active, fake_ckpt(3000, [.03, .01, 0, -7], .025),
                                        root, meta, 8)
            self.assertTrue(record['numeric_improved'])
            self.assertTrue(record['asinh_improved'])
            self.assertEqual(resumed['active_cycle'], None)
            self.assertEqual(len(resumed['cycles']), 1)
            self.assertTrue((meta/'champions'/'cycle-000001-best-precision.pt').is_file())
            self.assertEqual(resumed['best_precision_rank'][0], .03)

    def test_identity_and_trainer_drift_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            root, meta = d/'run', d/'journal';root.mkdir()
            for name in ('latest.pt', 'best-precision.pt', 'best-asinh.pt'):
                (root/name).write_bytes(b'test')
            app.load_or_initialize(meta/'campaign-state.json', CFG, 'a', fake_ckpt(10), root, meta)
            with self.assertRaisesRegex(RuntimeError, 'TRAINER_CHANGED'):
                app.load_or_initialize(meta/'campaign-state.json', CFG, 'b', fake_ckpt(10), root, meta)
            changed = dict(CFG, lr=.0001)
            with self.assertRaisesRegex(RuntimeError, 'IDENTITY_DRIFT'):
                app.load_or_initialize(meta/'campaign-state.json', changed, 'a', fake_ckpt(10), root, meta)

    def test_bootstrap_isolated_from_control(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            source, root = d/'control', d/'continuous'
            source.mkdir()
            (source/'best-precision.pt').write_bytes(b'best-numeric')
            (source/'best-asinh.pt').write_bytes(b'best-asinh')
            (source/'validation-cache.pt').write_bytes(b'cached-features')
            (source/'fork.json').write_text('original-source-data')
            def fake_load(path):
                if path.name == 'validation-cache.pt':
                    return {'config': CFG.copy()}
                return fake_ckpt(1000)
            with mock.patch.object(app, 'read_checkpoint', side_effect=fake_load):
                step = app.bootstrap_from_verified_best(root, source)
            self.assertEqual(step, 1000)
            self.assertEqual((root/'latest.pt').read_bytes(), b'best-numeric')
            self.assertEqual((root/'best-precision.pt').read_bytes(), b'best-numeric')
            self.assertEqual((root/'best-asinh.pt').read_bytes(), b'best-numeric')
            self.assertEqual((source/'best-asinh.pt').read_bytes(), b'best-asinh')
            self.assertEqual((source/'fork.json').read_text(), 'original-source-data')
            self.assertEqual(app.read_json(root/'continuous-origin.json')['source_best_step'], 1000)
            with mock.patch.object(app, 'read_checkpoint', side_effect=fake_load):
                with self.assertRaisesRegex(RuntimeError, 'DESTINATION_ALREADY_EXISTS'):
                    app.bootstrap_from_verified_best(root, source)

    def test_full_two_cycle_manager_with_fake_trainer_and_no_cuda(self):
        # Exercises actual process execution, state journaling, checkpoint
        # persistence and result verification without loading any model.
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            source, root, meta = d/'source', d/'primary', d/'metadata'
            source.mkdir()
            state = fake_ckpt(1000)
            (source/'best-precision.pt').write_text(json.dumps(state))
            (source/'validation-cache.pt').write_text(json.dumps({'config': CFG}))
            trainer = d/'fake_trainer.py'
            trainer.write_text("""import json, sys
from pathlib import Path
args = sys.argv
root = Path(args[args.index('--results-root')+1])
count = int(args[args.index('--additional-steps')+1])
last = json.loads((root/'latest.pt').read_text())
last['step'] += count
last['best']['precision']['rank'][0] += .001
last['best']['precision']['step'] = last['step']
last['best']['asinh']['score'] -= .001
last['best']['asinh']['step'] = last['step']
for name in ('latest.pt','best-precision.pt','best-asinh.pt'):
    (root/name).write_text(json.dumps(last))
(root/'test-last.json').write_text(json.dumps({'step':last['step']}))
print(json.dumps({'event':'fake_training_completed','step':last['step']}))
""", encoding='utf-8')
            def json_ckpt(path):
                return json.loads(path.read_text())
            with mock.patch.object(app, 'read_checkpoint', side_effect=json_ckpt):
                result = app.main(['--results-root', str(root), '--bootstrap-source', str(source),
                                   '--meta-root', str(meta), '--trainer', str(trainer),
                                   '--python', sys.executable,
                                   '--cycle-steps', '10', '--cycles', '2'])
            self.assertEqual(result, 0)
            journal = app.read_json(meta/'campaign-state.json')
            self.assertEqual(len(journal['cycles']), 2)
            self.assertEqual(journal['cycles'][0]['target_step'], 1010)
            self.assertEqual(journal['cycles'][1]['target_step'], 1020)
            self.assertEqual(json.loads((root/'latest.pt').read_text())['step'], 1020)
            self.assertEqual(json.loads((source/'best-precision.pt').read_text())['step'], 1000)
            self.assertTrue((meta/'champions'/'cycle-000002-best-precision.pt').exists())

    def test_lock_is_exclusive(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'lock'
            with app.exclusive_lock(path):
                with self.assertRaisesRegex(RuntimeError, 'ALREADY_RUNNING'):
                    with app.exclusive_lock(path): pass
            with app.exclusive_lock(path): pass


if __name__ == '__main__':
    unittest.main()
