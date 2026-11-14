"""Tests for the new decimal-only activation map; no HF downloads."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np
import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import nanojev_decimal_number_activation_map as dm


def test_default_grid_is_symmetric_and_dense_near_zero():
    vals = dm.grid()
    assert len(vals) == 213
    assert len(set(vals)) == 213
    assert vals == sorted(vals)
    assert vals[0] == -1000 and vals[-1] == 1000
    assert [n for n in vals if -20 <= n <= 20] == list(range(-20, 21))
    assert [n for n in vals if 0 < n <= 200 and n > 20] == list(range(25, 201, 5))
    assert [n for n in vals if 200 < n <= 500] == list(range(210, 501, 10))
    assert [n for n in vals if 500 < n <= 1000] == list(range(525, 1001, 25))
    assert all(-n in vals for n in vals)


def test_minimal_decimal_prompt():
    assert dm.prompt_for(-1000) == 'Number: -1000'
    assert dm.prompt_for(0) == 'Number: 0'
    assert dm.prompt_for(37) == 'Number: 37'
    with pytest.raises(ValueError):
        dm.prompt_for(1.4)


class CharacterTokenizer:
    """Each codepoint is a token; tests whether negative signs are included."""
    def __call__(self, prompt, add_special_tokens=False, return_offsets_mapping=False):
        assert not add_special_tokens and return_offsets_mapping
        return {'input_ids': [ord(c) for c in prompt],
                'offset_mapping': [(i, i+1) for i in range(len(prompt))]}


class FakeModel:
    def __call__(self, input_ids, attention_mask, use_cache, output_hidden_states):
        assert use_cache is False and output_hidden_states is True
        assert input_ids.ndim == 2 and input_ids.shape[0] == 1
        assert attention_mask.shape == input_ids.shape
        arr = input_ids.float().unsqueeze(-1)
        # Deterministic toy representations with distinct token information.
        h0 = torch.cat([arr, arr * 0 + 1, arr * 0 + 4], -1)
        h1 = torch.cat([arr * 2, arr * 3, arr * 0 - 1], -1)
        return type('Output', (), {'hidden_states': (h0, h1)})()


def test_token_span_and_pooled_model_capture():
    ids, indices = dm.numeric_indices(CharacterTokenizer(), 'Number: -19')
    assert len(indices) == 3
    assert ''.join(chr(ids[i]) for i in indices) == '-19'
    meta, vec = dm.capture(torch, FakeModel(), CharacterTokenizer(), -19, 'cpu', 50)
    assert vec.shape == (2, 2, 3)
    assert meta['numeric_token_count'] == 3
    assert meta['input_token_count'] == len('Number: -19')
    assert float(vec[0, 0, 0]) == ord('9')
    assert float(vec[0, 1, 0]) == pytest.approx((ord('-')+ord('1')+ord('9')) / 3)
    with pytest.raises(RuntimeError, match='TOKEN_BUDGET'):
        dm.capture(torch, FakeModel(), CharacterTokenizer(), -19, 'cpu', 5)


def test_correlations_and_unsupervised_pca():
    n = np.arange(-10, 11, dtype=np.float64)
    x = np.column_stack([n, n ** 2, np.ones_like(n)])
    corr = dm.pearson_features(x, n)
    assert corr[0] == pytest.approx(1)
    assert abs(corr[1]) < 1e-12
    assert corr[2] == 0
    coords, explained = dm.pca_two(x)
    assert coords.shape == (len(n), 2)
    assert sum(explained) == pytest.approx(1)
    near, random_baseline, count = dm.neighborhood_metrics(x, n)
    assert near <= random_baseline
    assert count == 0


def test_resume_analysis_roundtrip_without_model(tmp_path):
    root = tmp_path / 'decimal'
    root.mkdir()
    (root / 'records').mkdir()
    values = [-2, -1, 0, 1, 2]
    identity = {'schema': dm.SCHEMA, 'model': 'toy', 'model_info': {'revision': 'frozen'}}
    for i, n in enumerate(values):
        prompt = dm.prompt_for(n)
        vec = np.empty((2, 2, 3), dtype=np.float32)
        for layer in range(2):
            for channel in range(2):
                vec[layer, channel] = (n, n * n, layer + channel)
        meta = {'number': n, 'prompt': prompt, 'prompt_sha256': dm.digest(prompt),
                'model_identity': identity, 'input_token_count': len(prompt),
                'numeric_token_count': len(str(n)), 'input_ids': [1, 2],
                'numeric_token_indices': [1], 'shape': list(vec.shape)}
        dm.save_record(dm.record_path(root, i), meta, vec)
    report = dm.analyze(root, values, identity)
    assert report['activation_shape'] == [5, 2, 2, 3]
    assert (root / 'pca_coordinates.csv').exists()
    assert (root / 'numbers.csv').exists()
    assert len((root / 'layer_metrics.csv').read_text().splitlines()) == 5
    with pytest.raises(RuntimeError, match='MODEL_DRIFT'):
        dm.analyze(root, values, {'different': True})


def test_prepare_only_does_not_load_model(tmp_path, capsys):
    args = dm.parser().parse_args(['--prepare-only', '--limit', '5', '--output-root', str(tmp_path / 'results')])
    dm.run(args, loader=lambda *_: (_ for _ in ()).throw(AssertionError('loaded')))
    event = json.loads(capsys.readouterr().out)
    assert event['sample_count'] == 5
    assert set(event['grid']) == {-2, -1, 0, 1, 2}
    assert not (tmp_path / 'results').exists()


def test_full_run_resume_and_readonly_reanalysis(tmp_path):
    root = tmp_path / 'run'
    def loader(model_name, args):
        return (torch, FakeModel(), CharacterTokenizer(), 'cpu',
                {'source': 'toy', 'resolved_commit': 'test123', 'dtype': 'torch.float32', 'device': 'cpu'})
    args = dm.parser().parse_args(['--limit', '5', '--report-every', '3', '--output-root', str(root)])
    report = dm.run(args, loader=loader)
    assert report['number_count'] == 5
    assert report['activation_shape'] == [5, 2, 2, 3]
    assert len(list((root / 'records').glob('*.npz'))) == 5
    assert json.loads((root / 'manifest.json').read_text())['numbers'] == [-2, -1, 0, 1, 2]
    with pytest.raises(RuntimeError, match='OUTPUT_EXISTS'):
        dm.run(args, loader=loader)
    args.resume = True
    report_again = dm.run(args, loader=loader)
    assert report_again == report
    args.analyze_only = True
    report_again = dm.run(args, loader=lambda *_: (_ for _ in ()).throw(AssertionError('model loaded')))
    assert report_again == report
    args.limit = 6
    with pytest.raises(RuntimeError, match='MANIFEST_DRIFT'):
        dm.run(args, loader=loader)
