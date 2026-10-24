"""Read-only checkpoint-derived three-predictor failure/consensus tests."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import torch

from tools import nanojev_hawk_dove_gaussian_minority_report as mr
from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as ref
from tools import nanojev_hawk_dove_gaussian_two_backbone as duo


def _row(instance, tier, mask, x=100.0, y=100.0):
    return {'instance': instance, 'tier': tier, 'mask': mask, 'x': x, 'y': y}


class LogicTests(unittest.TestCase):
    def test_eight_patterns_are_ordered_and_all_masks_required(self):
        rows = []
        p = {b: [] for b in mr.MODELS}
        for i in range(8):
            bits = f'{i:03b}'
            for mask in (0, 1):
                rows.append(_row(f'case-{i}', mr.TIERS[0], mask))
                for k, b in enumerate(mr.MODELS):
                    good = bits[k] == '1'
                    p[b].append((100., 100.) if good else (200., 200.))
        result = mr._pattern(rows, p, .05)
        self.assertEqual(result['instances']['n'], 8)
        self.assertEqual(result['presentations']['n'], 16)
        self.assertTrue(all(result['instances']['counts'][f'{i:03b}'] == 1 for i in range(8)))
        self.assertEqual(result['instances']['at_least_one_success_rate'], 7/8)
        self.assertEqual(result['pairwise_oracle_instance_coverage']['reference+pythia'], 6/8)

        # One failed presentation makes that *instance* fail for its model.
        p['reference'][9] = (200., 200.)
        altered = mr._pattern(rows, p, .05)
        self.assertEqual(altered['instances']['counts']['000'], 2)
        self.assertEqual(altered['instances']['counts']['100'], 0)

    def test_strict_zero_and_consensus_and_z(self):
        self.assertEqual(mr._hits((.001, 100), (0, 100), .05), (False, True))
        self.assertEqual(mr._hits((0, 105), (0, 100), .05), (True, True))
        self.assertEqual(mr._z((5, 4)), 'HAWK')
        self.assertEqual(mr._z((3, 4)), 'DOVE')
        self.assertEqual(mr._z((4, 4)), 'TIE')
        p = {'reference': (0, 9), 'pythia': (6, 3), 'tinystories': (6, 6)}
        self.assertEqual(mr.ensemble_pair(p, 'mean_three'), (4, 6))
        self.assertEqual(mr.ensemble_pair(p, 'median_three'), (6, 6))
        self.assertEqual(mr.ensemble_pair(p, 'mean_reference_pythia'), (3, 6))
        self.assertEqual(mr._z_vote([_row('x', 'T01', 0, 0, 9)],
                              {k: [v] for k, v in p.items()})['three_way_abstentions'], 1)

    def test_dev_selection_does_not_consult_test(self):
        dev = {'candidates': {rule: {'min_tier_joint_all_masks_rate': 0.,
                                     'mean_tier_joint_all_masks_rate': 0.}
                              for rule in mr.RULES}}
        dev['candidates']['mean_three']['min_tier_joint_all_masks_rate'] = .6
        dev['candidates']['reference']['min_tier_joint_all_masks_rate'] = .5
        self.assertEqual(mr.select_on_dev(dev), 'mean_three')
        dev['candidates']['pythia']['min_tier_joint_all_masks_rate'] = .6
        dev['candidates']['pythia']['mean_tier_joint_all_masks_rate'] = .3
        dev['candidates']['mean_three']['mean_tier_joint_all_masks_rate'] = .7
        self.assertEqual(mr.select_on_dev(dev), 'mean_three')

    def test_magnitude_mask_and_examples(self):
        rows = [_row('a', 'T01', 0, 0, 10), _row('a', 'T01', 1, 0, 10)]
        for row in rows:
            row['matrix_index'] = 0
        p = {'reference': [(0, 10), (0, 10)],
             'pythia': [(.01, 10), (.01, 10)],
             'tinystories': [(0, 10), (0, 20)]}
        s = mr._difficulty_and_mask_symmetry(rows, p, .05)
        self.assertEqual(s['reference']['accuracy_by_target_magnitude']['X:exact_zero']['hits'], 2)
        self.assertEqual(s['pythia']['accuracy_by_target_magnitude']['X:exact_zero']['hits'], 0)
        self.assertEqual(s['tinystories']['mask_equivalence']['joint_pass_disagreement_rate'], .5*2)
        examples = mr._case_examples(rows, p, .05)
        self.assertEqual(examples['100'][0]['instance'], 'a')
        self.assertEqual(len(examples['100'][0]['masks']), 2)

    def test_error_asymmetry_and_signed_correlation(self):
        rows = [_row('a', 'T01', 0, 100, 100), _row('b', 'T01', 0, 100, 100)]
        p = {'reference': [(100, 110), (100, 110)],
             'pythia': [(110, 100), (110, 100)],
             'tinystories': [(100, 100), (100, 100)]}
        s = mr._error_symmetry(rows, p, .05)
        self.assertEqual(s['per_model_XY_failure_asymmetry']['reference']['presentation_counts']['X_only_hit'], 2)
        self.assertEqual(s['per_model_XY_failure_asymmetry']['pythia']['presentation_counts']['Y_only_hit'], 2)
        self.assertEqual(s['per_model_XY_failure_asymmetry']['tinystories']['presentation_counts']['both_hit'], 2)


class CheckpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)

    def _fixture(self, root: Path):
        args = duo.parser().parse_args(['train', '--backbone', 'pythia', '--device', 'cpu',
            '--banks', '16', '--grid-bins', '32', '--hidden', '8', '--steps', '2',
            '--seed', '1701', '--train-matrices', '3', '--dev-matrices', '2',
            '--test-matrices', '2', '--counts-per-matrix', '3', '--masks', '2',
            '--batch-size', '16'])
        data, domain, manifest = ref._dataset(args, torch.device('cpu'))
        rid = ref._identity(args, manifest, domain)
        reference_path = root / 'reference' / 'best.pt'
        reference_path.parent.mkdir(parents=True)
        m = ref.BlindDensityNetwork(16, 32, 1701, 8)
        torch.save({'schema': ref.SCHEMA, 'step': 2, 'identity': rid,
                    'model': m.state_dict()}, reference_path)
        for b in ('tinystories', 'pythia'):
            path = root / 'duo' / b
            path.mkdir(parents=True)
            dim = 8 if b == 'tinystories' else 16
            g = torch.Generator().manual_seed(dim)
            cache = {'schema': duo.SCHEMA + '.activations', 'backbone': b,
                     'model_id': duo.cassette.SOURCE[b][0],
                     'source_info': {'resolved_commit': 'fixture'},
                     'capture': 'middle and last hidden layer, last four tokens, FP32, no labels',
                     'input_dim': dim,
                     'splits': {s: {'activations': torch.randn(len(data[s]['rows']), dim, generator=g),
                                    'prompt_sha256': duo.prompt_hash(data[s]['rows'])}
                                for s in ('train', 'dev', 'test')}}
            ident = duo.identity(args, b, manifest, domain, cache)
            model = duo.IndependentGaussianHead(dim, 16, 32, 1701, 8)
            torch.save({'schema': duo.SCHEMA, 'step': 2, 'identity': ident,
                        'model': model.state_dict()}, path / 'best.pt')
            torch.save(cache, path / 'feature_cache.pt')
            (path / 'config.json').write_text(json.dumps(ident))
            (path / 'matrix_split_manifest.json').write_text(json.dumps(manifest))
        return reference_path, root / 'duo'

    def test_read_only_three_checkpoints_and_full_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            r, d = self._fixture(root)
            original = {p: mr._sha256(p) for p in root.rglob('*') if p.is_file()}
            data, p, prov, ident, manifest = mr.collect_from_checkpoints(r, d, 'cpu', 16)
            report = mr.build_report(data, p, prov, ident, manifest)
            self.assertEqual(report['model_order'], list(mr.MODELS))
            self.assertFalse(report['reference_is_qwen'])
            self.assertFalse(report['selection_policy']['test_used_for_selection'])
            self.assertIn(report['selection_policy']['selected_rule'], mr.RULES)
            self.assertEqual(report['DEV']['joint_failure_patterns']['instances']['n'], 2*3*len(mr.TIERS))
            self.assertIn('mean_three', report['TEST']['candidates'])
            self.assertIn('zero_targets', report['TEST']['error_symmetry']['per_model_XY_failure_asymmetry']['reference'])
            self.assertEqual(original, {p: mr._sha256(p) for p in root.rglob('*') if p.is_file()})

    def test_cli_report_written_once_sources_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference, duo_root = self._fixture(root)
            before = {str(p): mr._sha256(p) for p in root.rglob('*') if p.is_file()}
            dest = root / 'diagnostics' / 'minority_report.json'
            args = ['--reference-checkpoint', str(reference),
                    '--duo-root', str(duo_root), '--output', str(dest),
                    '--device', 'cpu', '--eval-batch-size', '16',
                    '--cpu-threads', '1']
            with mock.patch('sys.stdout'):
                mr.main(args)
            self.assertTrue(dest.is_file())
            self.assertEqual(json.loads(dest.read_text())['selection_policy']['test_used_for_selection'], False)
            self.assertEqual(before, {k: mr._sha256(Path(k)) for k in before})
            with self.assertRaisesRegex(FileExistsError, 'MINORITY_OUTPUT_ALREADY_EXISTS_NO_OVERWRITE'):
                mr.main(args)

    def test_fail_closed_on_wrong_split_or_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            r, d = self._fixture(root)
            path = d / 'tinystories' / 'matrix_split_manifest.json'
            x = json.loads(path.read_text()); x['matrix_hashes']['test'] = []
            path.write_text(json.dumps(x))
            with self.assertRaisesRegex(RuntimeError, 'MINORITY_DUO_MATRIX_MANIFEST_DRIFT'):
                mr.collect_from_checkpoints(r, d, 'cpu', 16)

    def test_missing_duo_or_mismatched_backbone_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            r, d = self._fixture(root)
            path = d / 'pythia' / 'best.pt'
            state = torch.load(path, weights_only=True)
            state['identity']['backbone'] = 'tinystories'
            torch.save(state, path)
            with self.assertRaisesRegex(RuntimeError, 'MINORITY_DUO_OR_REFERENCE_IDENTITY_DRIFT'):
                mr.collect_from_checkpoints(r, d, 'cpu', 16)

if __name__ == '__main__':
    unittest.main()
