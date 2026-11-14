"""Offline tests for the nonsearch logarithmic bar activation experiment."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import nanojev_logbar_activation_map as lm
from test_nanojev_decimal_number_activation_map import CharacterTokenizer, FakeModel


def test_number_grid_and_log_bar_values():
    assert lm.grid()[0] == -2000 and lm.grid()[-1] == 2000
    assert len(lm.grid()) == 253
    expected = {0: '.', 1: '.|||||||', 2: '|.|', 10: '||.||||',
                100: '||||.||||||', 1000: '||||||.|||||||||',
                2000: '|||||||.||||||'}
    for n, code in expected.items():
        assert lm.encode(n) == code
        if n:
            assert lm.encode(-n) == '-' + code
    assert lm.encode(2000, 2) == '|||||||.||||||:'
    assert lm.encode(-2000, 2) == '-|||||||.||||||:'
    with pytest.raises(ValueError):
        lm.encode(2001)
    with pytest.raises(ValueError):
        lm.encode(4.5)
    with pytest.raises(ValueError):
        lm.encode(100, 3)


def test_collapse_is_explicit_not_silently_discarded():
    rows, groups = lm.number_bank(lm.grid(), 1)
    assert len(rows) == 253 and len(groups) == 119
    assert lm.encode(1900) == lm.encode(2000)
    assert next(r for r in rows if r['number'] == 2000)['collision_group_size'] > 1
    assert len(lm.number_bank(lm.grid(), 2)[1]) == 253


def test_capture_sign_and_bar_token_indices():
    m, v = lm.capture_prompt(torch, FakeModel(), CharacterTokenizer(),
                             'Number: -|||||||.||||||', 'cpu', 64)
    assert m['numeric_token_count'] == len('-|||||||.||||||')
    assert m['input_token_count'] == len('Number: -|||||||.||||||')
    assert v.shape == (2, 2, 3)
    with pytest.raises(RuntimeError, match='TOKEN_BUDGET'):
        lm.capture_prompt(torch, FakeModel(), CharacterTokenizer(), 'Number: |||||||.||||||', 'cpu', 4)


def test_prepare_does_not_load(tmp_path, capsys):
    args = lm.parser().parse_args(['--prepare-only', '--output-root', str(tmp_path / 'not_created')])
    lm.run(args, loader=lambda *_: (_ for _ in ()).throw(AssertionError('loaded')))
    event = json.loads(capsys.readouterr().out)
    assert event['numbers'] == 253 and event['unique_logbar_codes'] == 119
    assert not (tmp_path / 'not_created').exists()


def test_capture_resume_analysis_mock(tmp_path):
    def loader(_model, _args):
        return torch, FakeModel(), CharacterTokenizer(), 'cpu', {'source': 'toy', 'revision': 'test'}
    args = lm.parser().parse_args(['--limit', '5', '--output-root', str(tmp_path / 'a'),
                                   '--report-every', '2'])
    result = lm.run(args, loader=loader)
    assert result['number_count'] == 5
    assert result['decimal_captures'] == 5
    assert result['logbar_captures'] == 5
    assert len(list((tmp_path / 'a' / 'records').glob('*.npz'))) == 10
    assert (tmp_path / 'a' / 'number_mapping.csv').is_file()
    assert (tmp_path / 'a' / 'layer_metrics.csv').is_file()
    assert (tmp_path / 'a' / 'pca_coordinates.csv').is_file()
    with pytest.raises(RuntimeError, match='OUTPUT_EXISTS'):
        lm.run(args, loader=loader)
    args.resume = True
    assert lm.run(args, loader=loader) == result
    args.analyze_only = True
    assert lm.run(args, loader=lambda *_: (_ for _ in ()).throw(AssertionError('loaded'))) == result
    args.fractional_digits = 2
    with pytest.raises(RuntimeError, match='MANIFEST_DRIFT'):
        lm.run(args, loader=loader)
