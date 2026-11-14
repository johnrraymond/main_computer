"""Offline paired-format tests with a deterministic fake model."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np
import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import nanojev_logbar_activation_map as baseline
import nanojev_paired_logbar_activation_map as paired
from test_nanojev_decimal_number_activation_map import CharacterTokenizer, FakeModel


def fake_loader(model, args):
    return torch, FakeModel(), CharacterTokenizer(), 'cpu', {'source':'toy', 'revision':'test'}


def test_prompt_is_single_combined_representation_and_unique():
    assert paired.paired_prompt(2000) == 'Number: 2000, |||||||.||||||'
    assert paired.paired_prompt(-2000) == 'Number: -2000, -|||||||.||||||'
    assert paired.paired_prompt(0) == 'Number: 0, .'
    assert len({paired.paired_prompt(n) for n in baseline.grid()}) == 253
    assert baseline.encode(1900) == baseline.encode(2000)
    assert paired.paired_prompt(1900) != paired.paired_prompt(2000)
    with pytest.raises(ValueError):
        paired.paired_prompt(1.2)


def test_token_spans_capture_two_separate_segments():
    prompt, ids, dec_idx, bar_idx, all_idx = paired.token_spans(CharacterTokenizer(), -2000)
    assert prompt == 'Number: -2000, -|||||||.||||||'
    assert ''.join(chr(ids[i]) for i in dec_idx) == '-2000'
    assert ''.join(chr(ids[i]) for i in bar_idx) == '-|||||||.||||||'
    assert not set(dec_idx).intersection(bar_idx)
    assert ''.join(chr(ids[i]) for i in all_idx) == '-2000-|||||||.||||||'
    meta, vec = paired.capture(torch, FakeModel(), CharacterTokenizer(), -2000, 'cpu', 128)
    assert vec.shape == (2, 5, 3)
    assert float(vec[0, 0, 0]) == ord('|')
    assert float(vec[0, 2, 0]) == ord('0')
    assert meta['bar_token_count'] == len('-|||||||.||||||')
    assert meta['decimal_token_count'] == len('-2000')
    with pytest.raises(RuntimeError, match='TOKEN_BUDGET'):
        paired.capture(torch, FakeModel(), CharacterTokenizer(), 2000, 'cpu', 6)


def test_spearman_ranks_and_correlation_are_stable_with_ties():
    assert paired.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1)
    assert paired.spearman([1, 1, 2, 2], [5, 5, 7, 7]) == pytest.approx(1)
    assert paired.spearman([1, 1, 1], [2, 3, 4]) == 0.0
    x = np.asarray([[1.,2.], [2.,3.], [3.,4.]], dtype=np.float32)
    assert paired.pairwise_cosine_distances(x).shape == (3,3)


def test_prepare_only_does_not_load_model(tmp_path, capsys):
    args = paired.parser().parse_args(['--prepare-only', '--output-root', str(tmp_path/'p')])
    settings = paired.run(args, loader=lambda *_: (_ for _ in ()).throw(AssertionError('loaded')))
    output = json.loads(capsys.readouterr().out)
    assert output['numbers'] == 253 and output['captures'] == 253
    assert 'Number: 2000, |||||||.||||||' in output['examples']
    assert settings['fractional_digits'] == 1
    assert not (tmp_path/'p').exists()


def test_complete_capture_comparison_and_resume(tmp_path):
    base = tmp_path/'baseline'
    args_b = baseline.parser().parse_args(['--limit','7','--output-root',str(base), '--report-every','4'])
    base_report = baseline.run(args_b, loader=fake_loader)
    assert base_report['number_count'] == 7
    root = tmp_path/'paired'
    args = paired.parser().parse_args(['--limit','7','--baseline-root', str(base),
                                        '--output-root',str(root), '--report-every','4'])
    report = paired.run(args, loader=fake_loader)
    assert report['numbers'] == 7 and report['unique_paired_prompts'] == 7
    assert report['activation_shape'] == [7, 2, 5, 3]
    assert len(list((root/'records').glob('*.npz'))) == 7
    assert (root/'geometry_alignment.csv').is_file()
    assert (root/'headline_comparison.json').is_file()
    assert '1' in json.loads((root/'headline_comparison.json').read_text())
    assert (root/'layer_metrics.csv').is_file()
    assert (root/'number_mapping.csv').is_file()
    lines = (root/'geometry_alignment.csv').read_text(encoding='utf-8').splitlines()
    assert len(lines) == 1 + 2*2*3
    with pytest.raises(RuntimeError, match='OUTPUT_EXISTS'):
        paired.run(args, loader=fake_loader)
    args.resume = True
    assert paired.run(args, loader=fake_loader) == report
    args.analyze_only = True
    assert paired.run(args, loader=lambda *_: (_ for _ in ()).throw(AssertionError('loaded'))) == report
    args.fractional_digits = 2
    with pytest.raises(RuntimeError, match='MANIFEST_DRIFT'):
        paired.run(args, loader=fake_loader)


def test_missing_baseline_does_not_create_output(tmp_path):
    root = tmp_path/'new'
    args = paired.parser().parse_args(['--limit','5','--baseline-root',str(tmp_path/'absent'),
                                        '--output-root',str(root)])
    with pytest.raises(RuntimeError, match='BASELINE_MISSING'):
        paired.run(args, loader=fake_loader)
    assert not root.exists()
