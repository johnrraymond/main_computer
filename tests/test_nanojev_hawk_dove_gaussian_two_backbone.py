"""Regression/smoke tests for independent TinyStories and Pythia Gaussian heads."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import torch

from tools import nanojev_hawk_dove_gaussian_two_backbone as duo
from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as reference


class FakeFrozen(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.emb = torch.nn.Embedding(12, 4)
        for p in self.parameters():
            p.requires_grad_(False)

    def get_input_embeddings(self):
        return self.emb


def fake_source(name, args):
    return object(), FakeFrozen(), object(), args.device, {'resolved_commit': 'test-fixed', 'model_type': name}


def fake_capture(model, tokenizer, device, prompts, max_tokens):
    return torch.tensor([[float(byte) / 255 for byte in hashlib.sha256(p.encode()).digest()]
                         for p in prompts], dtype=torch.float32)


class IndependentHeadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)

    def args(self, root, backbone='tinystories'):
        return duo.parser().parse_args([
            'train', '--backbone', backbone, '--device', 'cpu',
            '--output', str(root / backbone), '--banks', '24', '--grid-bins', '32',
            '--hidden', '8', '--train-matrices', '5', '--dev-matrices', '2',
            '--test-matrices', '2', '--counts-per-matrix', '3', '--masks', '1',
            '--batch-size', '16', '--steps', '2', '--probe-every', '1',
            '--capture-batch-size', '32'])

    def test_only_two_backbones(self):
        self.assertEqual(duo.BACKBONES, ('tinystories', 'pythia'))
        with self.assertRaises(SystemExit):
            duo.parser().parse_args(['train', '--backbone', 'qwen'])

    def test_same_gaussian_architecture_fresh_not_frozen(self):
        a = duo.IndependentGaussianHead(32, 24, 32, 41, 8)
        b = duo.IndependentGaussianHead(48, 24, 32, 41, 8)
        self.assertEqual(set(a.state_dict()) - {'backbone_projection.1.weight'},
                         set(b.state_dict()) - {'backbone_projection.1.weight'})
        self.assertTrue(torch.equal(a.encoder[0].weight, b.encoder[0].weight))
        self.assertTrue(torch.equal(a.centers, b.centers))
        self.assertTrue(all(p.requires_grad for p in a.parameters()))
        self.assertFalse(hasattr(a, 'lm_residual'))
        self.assertEqual(a.banks, 24)
        self.assertEqual(a.bins, 32)

    def test_numeric_and_backbone_branches_both_train(self):
        model = duo.IndependentGaussianHead(32, 24, 32, 23, 8)
        x = torch.randn(12, reference.INPUT_DIM + 32)
        output = model.locations(x)
        output.square().sum().backward()
        self.assertGreater(model.encoder[0].weight.grad.abs().sum().item(), 0)
        self.assertGreater(model.backbone_projection[1].weight.grad.abs().sum().item(), 0)
        self.assertGreater(model.encoder[-1].weight.grad.abs().sum().item(), 0)
        with torch.no_grad():
            with_backbone = model.locations(x)
            model.backbone_enabled = False
            numerical_only = model.locations(x)
        self.assertFalse(torch.equal(with_backbone, numerical_only))
        with self.assertRaisesRegex(ValueError, 'DUO_BOUND_INPUT_DIMENSION_DRIFT'):
            model(torch.randn(12, 32))

    def test_learns_continuous_numeric_targets_from_zero_training(self):
        model = duo.IndependentGaussianHead(32, 24, 32, 91, 8)
        gen = torch.Generator().manual_seed(45)
        x = torch.randn(48, reference.INPUT_DIM + 32, generator=gen)
        # A stable target which can be learned by a small network, exercising
        # both location outputs and genuine updates to the model's weights.
        target = torch.tensor([[.75, .25]]).repeat(48, 1)
        opt = torch.optim.AdamW(model.parameters(), lr=.015)
        with torch.no_grad():
            start = (model.locations(x) - target).square().mean().item()
        for _ in range(75):
            opt.zero_grad()
            loss = (model.locations(x) - target).square().mean()
            loss.backward()
            opt.step()
        with torch.no_grad():
            end = (model.locations(x) - target).square().mean().item()
        self.assertLess(end, start * .25)

    def test_cache_strict_prompt_and_fp32(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, _, _ = duo._dataset(self.args(Path(tmp)), torch.device('cpu'))
            cache = {'schema': duo.SCHEMA + '.activations',
                     'backbone': 'tinystories',
                     'model_id': duo.cassette.SOURCE['tinystories'][0],
                     'input_dim': 32, 'source_info': {'resolved_commit': 'fixed'},
                     'capture': 'unit',
                     'splits': {k: {'prompt_sha256': duo.prompt_hash(v['rows']),
                                    'activations': torch.zeros(len(v['rows']), 32)}
                                for k, v in data.items()}}
            self.assertEqual(duo.validate_cache(cache, 'tinystories', data), 32)
            original = data['train']['features'].clone()
            duo._install_activations(data, cache, torch.device('cpu'))
            self.assertTrue(torch.equal(data['train']['features'][:, :reference.INPUT_DIM], original))
            self.assertEqual(data['train']['features'].shape[1], reference.INPUT_DIM + 32)
            cache['splits']['dev']['prompt_sha256'] = 'bad'
            with self.assertRaisesRegex(RuntimeError, 'DUO_CACHE_PROMPT_OR_SPLIT_DRIFT'):
                duo.validate_cache(cache, 'tinystories', data)
            cache['splits']['dev']['prompt_sha256'] = duo.prompt_hash(data['dev']['rows'])
            cache['splits']['dev']['activations'] = cache['splits']['dev']['activations'].half()
            with self.assertRaisesRegex(RuntimeError, 'DUO_CACHE_REQUIRES_FINITE_FP32'):
                duo.validate_cache(cache, 'tinystories', data)

    def test_two_independent_training_checkpoints_predictions_and_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summaries = []
            for backbone in duo.BACKBONES:
                args = self.args(root, backbone)
                with mock.patch.object(duo.cassette, 'load_source', side_effect=fake_source), \
                     mock.patch.object(duo, 'capture_hidden', side_effect=fake_capture):
                    summary = duo.train(args)
                summaries.append(summary)
                self.assertTrue((args.output / 'best.pt').exists())
                self.assertTrue((args.output / 'predictions_dev.jsonl').exists())
                self.assertTrue((args.output / 'predictions_test.jsonl').exists())
                self.assertTrue(summary['fresh_head'])
                self.assertIn('no pretrained Gaussian', summary['identity']['initial_weights'])
                state = torch.load(args.output / 'best.pt', weights_only=True)
                self.assertEqual(state['schema'], duo.SCHEMA)
                self.assertNotIn('lm_residual', str(state['model'].keys()))
                audited = duo.audit(duo.parser().parse_args([
                    'audit', '--checkpoint', str(args.output / 'best.pt')]))
                self.assertEqual(audited['splits']['dev']['gate'], summary['dev']['gate'])
                pred = [json.loads(line) for line in
                        (args.output / 'predictions_dev.jsonl').read_text().splitlines()]
                self.assertEqual(len(pred), 2 * 3 * len(reference.TIERS))
                self.assertTrue(all('predicted_X' in row and 'predicted_Y' in row and
                                    row['predicted_Z'] in ('HAWK', 'DOVE', 'TIE') for row in pred))
            self.assertEqual(summaries[0]['manifest']['matrix_hashes'],
                             summaries[1]['manifest']['matrix_hashes'])
            self.assertNotEqual(summaries[0]['backbone'], summaries[1]['backbone'])

    def test_train_all_dispatch_only_two_independent_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reuse = root / 'cache'
            for backbone in duo.BACKBONES:
                (reuse / backbone).mkdir(parents=True)
                (reuse / backbone / 'feature_cache.pt').touch()
            args = duo.parser().parse_args([
                'train-all', '--output', str(root / 'two'), '--device', 'cpu',
                '--reuse-cache-root', str(reuse), '--steps', '2'])
            seen = []
            def simulate(argv, check):
                self.assertTrue(check)
                self.assertNotIn('--reference-checkpoint', argv)
                self.assertEqual(argv[argv.index('--reuse-cache-root') + 1], str(reuse))
                backbone = argv[argv.index('--backbone') + 1]
                out = Path(argv[argv.index('--output') + 1]); out.mkdir()
                seen.append(backbone)
                (out / 'summary.json').write_text(json.dumps({
                    'identity': {'model_id': duo.cassette.SOURCE[backbone][0]},
                    'status': 'FAIL', 'best_step': 2,
                    'dev': {'gate': {'min_tier_joint_instance_rate': .4}},
                    'test': {'gate': {'min_tier_joint_instance_rate': .3}},
                    'manifest': {'matrix_hashes': {'train': ['a'], 'dev': ['b'], 'test': ['c']}}}))
            with mock.patch.object(duo.subprocess, 'run', side_effect=simulate):
                report = duo.train_all(args)
            self.assertEqual(seen, list(duo.BACKBONES))
            self.assertEqual(set(report['backbones']), set(duo.BACKBONES))

    def test_preflight_import_error_before_any_training(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = duo.parser().parse_args([
                'train-all', '--output', str(root / 'two'), '--device', 'cpu'])
            with mock.patch.object(duo.subprocess, 'run', side_effect=
                                   subprocess.CalledProcessError(1, 'python')) as run:
                with self.assertRaisesRegex(RuntimeError, 'DUO_CAPTURE_DEPENDENCY_PREFLIGHT_FAILED'):
                    duo.train_all(args)
            self.assertEqual(run.call_count, 1)


    def test_resume_replays_interruption_with_full_adam_scheduler_sampler_state(self):
        # Compare complete run against a simulated process crash immediately
        # after an atomic checkpoint, then resume in a new train() invocation.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clean = self.args(root / 'clean')
            clean.steps = 5
            clean.probe_every = 2
            clean.checkpoint_every = 1
            interrupted = self.args(root / 'interrupted')
            interrupted.steps = 5
            interrupted.probe_every = 2
            interrupted.checkpoint_every = 1
            with mock.patch.object(duo.cassette, 'load_source', side_effect=fake_source), \
                 mock.patch.object(duo, 'capture_hidden', side_effect=fake_capture):
                duo.train(clean)
                original_save = duo._save_progress
                def simulate_crash(*args, **kwargs):
                    original_save(*args, **kwargs)
                    if args[1] == 3:
                        raise KeyboardInterrupt('SIMULATED_POWER_LOSS_AFTER_CHECKPOINT')
                with mock.patch.object(duo, '_save_progress', side_effect=simulate_crash):
                    with self.assertRaisesRegex(KeyboardInterrupt, 'SIMULATED_POWER_LOSS'):
                        duo.train(interrupted)
            latest_before = torch.load(interrupted.output / 'latest.pt', weights_only=True)
            self.assertEqual(latest_before['step'], 3)
            self.assertIn('optimizer', latest_before)
            self.assertIn('scheduler', latest_before)
            self.assertIn('sampler_rng', latest_before)
            self.assertEqual(latest_before['phase_end'], 5)
            interrupted.resume = True
            with mock.patch.object(duo, 'make_cache', side_effect=AssertionError('recaptured')):
                duo.train(interrupted)
            resumed = torch.load(interrupted.output / 'latest.pt', weights_only=True)
            direct = torch.load(clean.output / 'latest.pt', weights_only=True)
            self.assertEqual(resumed['step'], 5)
            self.assertEqual(resumed['history'], direct['history'])
            self.assertEqual(resumed['best_step'], direct['best_step'])
            self.assertEqual(resumed['best_rank'], direct['best_rank'])
            self.assertEqual(resumed['scheduler']['last_epoch'],
                             direct['scheduler']['last_epoch'])
            for name in direct['model']:
                self.assertTrue(torch.equal(resumed['model'][name], direct['model'][name]), name)
            self.assertTrue(torch.equal(resumed['sampler_rng'], direct['sampler_rng']))

    def test_extension_phase_preserves_adam_state_and_starts_new_cosine(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = self.args(root)
            with mock.patch.object(duo.cassette, 'load_source', side_effect=fake_source), \
                 mock.patch.object(duo, 'capture_hidden', side_effect=fake_capture):
                duo.train(args)
            first = torch.load(args.output / 'latest.pt', weights_only=True)
            args.resume = True
            args.steps = 4
            duo.train(args)
            second = torch.load(args.output / 'latest.pt', weights_only=True)
            self.assertEqual((second['phase_start'], second['phase_end']), (2, 4))
            self.assertEqual(second['step'], 4)
            self.assertEqual(second['scheduler']['last_epoch'], 2)
            self.assertEqual(second['history'][-1]['step'], 4)
            self.assertGreater(len(second['optimizer']['state']), 0)
            self.assertEqual(first['identity'], second['identity'])
            self.assertEqual(second['weights_only_origin'], False)

    def test_legacy_best_only_migration_is_explicit_and_archives_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = self.args(root, 'pythia')
            with mock.patch.object(duo.cassette, 'load_source', side_effect=fake_source), \
                 mock.patch.object(duo, 'capture_hidden', side_effect=fake_capture):
                duo.train(args)
            legacy = torch.load(args.output / 'best.pt', weights_only=True)
            (args.output / 'latest.pt').unlink()
            args.resume = True
            args.steps = 5
            with self.assertRaisesRegex(RuntimeError, 'DUO_LEGACY_CHECKPOINT_HAS_NO_OPTIMIZER_RNG'):
                duo.train(args)
            self.assertFalse((args.output / 'before_weights_only_resume').exists())
            args.weights_only_resume = True
            duo.train(args)
            self.assertTrue((args.output / 'before_weights_only_resume' / 'best.pt').exists())
            archived = torch.load(args.output / 'before_weights_only_resume' / 'best.pt', weights_only=True)
            self.assertEqual(legacy['step'], archived['step'])
            for name in legacy['model']:
                self.assertTrue(torch.equal(legacy['model'][name], archived['model'][name]))
            saved = torch.load(args.output / 'latest.pt', weights_only=True)
            self.assertEqual(saved['step'], 5)
            self.assertEqual(saved['weights_only_origin'], True)
            self.assertEqual((saved['phase_start'], saved['phase_end']), (legacy['step'], 5))
            self.assertTrue((args.output / 'summary.json').is_file())
            self.assertTrue((args.output / 'before_weights_only_resume' / 'summary.json').is_file())
            report = duo.audit(duo.parser().parse_args([
                'audit', '--checkpoint', str(args.output / 'best.pt')]))
            self.assertEqual(report['splits']['dev']['gate'],
                             json.loads((args.output / 'summary.json').read_text())['dev']['gate'])

    def test_resume_finalizes_after_last_step_checkpoint_without_retraining(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.args(Path(tmp))
            with mock.patch.object(duo.cassette, 'load_source', side_effect=fake_source), \
                 mock.patch.object(duo, 'capture_hidden', side_effect=fake_capture):
                duo.train(args)
            before = torch.load(args.output / 'latest.pt', weights_only=True)
            (args.output / 'summary.json').unlink()
            (args.output / 'predictions_dev.jsonl').unlink()
            args.resume = True
            with mock.patch.object(duo, '_save_progress', wraps=duo._save_progress) as save:
                duo.train(args)
            self.assertEqual(save.call_count, 1)
            after = torch.load(args.output / 'latest.pt', weights_only=True)
            self.assertEqual(after['step'], before['step'])
            self.assertEqual(after['history'], before['history'])
            for name in before['model']:
                self.assertTrue(torch.equal(before['model'][name], after['model'][name]))
            self.assertTrue((args.output / 'summary.json').is_file())
            self.assertTrue((args.output / 'predictions_dev.jsonl').is_file())

    def test_resume_refuses_changed_split_or_model_parameters(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.args(Path(tmp))
            with mock.patch.object(duo.cassette, 'load_source', side_effect=fake_source), \
                 mock.patch.object(duo, 'capture_hidden', side_effect=fake_capture):
                duo.train(args)
            args.resume = True
            args.steps = 4
            args.seed += 1
            with self.assertRaisesRegex(RuntimeError, 'DUO_CACHE_PROMPT_OR_SPLIT_DRIFT'):
                duo.train(args)
            args.seed -= 1
            args.banks += 1
            with self.assertRaisesRegex(RuntimeError, 'DUO_RESUME_IDENTITY_DRIFT'):
                duo.train(args)

    def test_resume_all_dispatch_existing_experiments_without_recapture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for backbone in duo.BACKBONES:
                sub = root / backbone
                sub.mkdir()
                (sub / 'config.json').write_text('{}')
            args = duo.parser().parse_args(['train-all', '--output', str(root),
                                            '--device', 'cpu', '--resume',
                                            '--weights-only-resume', '--steps', '30000'])
            seen = []
            def simulate(argv, check):
                self.assertTrue(check)
                self.assertIn('--resume', argv)
                self.assertIn('--weights-only-resume', argv)
                self.assertNotIn('--reuse-cache-root', argv)
                self.assertEqual(argv[argv.index('--steps') + 1], '30000')
                backbone = argv[argv.index('--backbone') + 1]
                seen.append(backbone)
                sub = root / backbone
                (sub / 'summary.json').write_text(json.dumps({
                    'identity': {'model_id': duo.cassette.SOURCE[backbone][0]},
                    'status': 'FAIL', 'best_step': 100,
                    'dev': {'gate': {'min_tier_joint_instance_rate': .4}},
                    'test': {'gate': {'min_tier_joint_instance_rate': .3}},
                    'manifest': {'matrix_hashes': {'train': ['a'], 'dev': ['b'], 'test': ['c']}}}))
            with mock.patch.object(duo.subprocess, 'run', side_effect=simulate):
                duo.train_all(args)
            self.assertEqual(seen, list(duo.BACKBONES))


if __name__ == '__main__':
    unittest.main()
