"""Offline checks for the +/-2M, 117-effective-DF experiment."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np
import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import nanojev_multiscale_activation_decoder as exp
import nanojev_three_representation_decoder as old
class CharacterTokenizer:
    def __call__(self, prompt, add_special_tokens=False, return_offsets_mapping=False):
        assert not add_special_tokens and return_offsets_mapping
        return {'input_ids':[ord(c) for c in prompt],
                'offset_mapping':[(i,i+1) for i in range(len(prompt))]}


class FakeModel:
    def __call__(self, input_ids, attention_mask, use_cache, output_hidden_states):
        assert use_cache is False and output_hidden_states
        arr = input_ids.float().unsqueeze(-1)
        a = torch.cat([arr, arr*0+1, arr*0+4], -1)
        b = torch.cat([arr*2, arr*3, arr*0-1], -1)
        return type('Output', (), {'hidden_states':(a,b)})()


def fake_loader(_model, _args):
    return torch, FakeModel(), CharacterTokenizer(), 'cpu', {'source':'fake', 'revision':'one'}


def test_grid_matches_plan_and_retains_symmetry():
    numbers = exp.grid()
    assert len(numbers) == 507
    assert numbers == sorted(numbers)
    assert numbers[0] == -2_000_000 and numbers[-1] == 2_000_000
    assert numbers.count(0) == 1
    assert {abs(v) for v in numbers if v > 0} == {abs(v) for v in numbers if v < 0}
    assert len([n for n in numbers if 0<n<=20]) == 20
    assert len([n for n in numbers if 20<n<=200]) == 36
    assert len([n for n in numbers if 200<n<=2000]) == 36
    assert len([n for n in numbers if 2000<n<=20000]) == 36
    assert len([n for n in numbers if 20000<n<=200000]) == 36
    assert len([n for n in numbers if n>200000]) == 89


def test_encoding_and_collision_bookkeeping():
    assert exp.encode(2_000) == '|||||||.||||||'
    assert exp.encode(2_000_000) == '||||||||||||||.|||||'
    assert exp.encode(-2_000_000) == '-||||||||||||||.|||||'
    assert exp.encode(0) == '.'
    assert exp.prompt('paired', 2_000_000) == 'Number: 2000000, ||||||||||||||.|||||'
    tasks, groups = exp.task_bank(exp.grid(), 1)
    assert len(groups) == 239
    assert len(tasks) == 507*2+239
    assert len(set((kind,key) for kind,key,_ in tasks)) == len(tasks)
    assert exp.collision_floor(np.array(exp.grid()), 1)['unique_codes'] == 239
    with pytest.raises(ValueError):
        exp.quantize(2_000_001)


def test_token_spans_avoid_separator_and_find_negative():
    text, ids, decimal, bar = exp.token_spans(CharacterTokenizer(), -2_000_000)
    assert text == 'Number: -2000000, -||||||||||||||.|||||'
    assert ''.join(chr(ids[i]) for i in decimal) == '-2000000'
    assert ''.join(chr(ids[i]) for i in bar) == '-||||||||||||||.|||||'
    meta, vec = exp.capture_paired(torch, FakeModel(), CharacterTokenizer(),
                                    -2_000_000, 'cpu', 128, 1)
    assert meta['decimal_token_count'] == 8
    assert vec.shape == (2, 5, 3)
    assert vec[0,1,0] != vec[0,3,0]
    with pytest.raises(RuntimeError, match='TOKEN_LIMIT'):
        exp.capture_paired(torch, FakeModel(), CharacterTokenizer(), 2_000_000, 'cpu', 8, 1)


def test_ridge_small_and_larger_row_count_matches_reference():
    rng = np.random.default_rng(5)
    for shape in ((17, 32), (40, 6)):
        features = rng.normal(size=shape)
        y = features[:,0]*27 + features[:,1]*3 + 5
        pred, info, params = exp.ridge_fit(features, y, 4.)
        expected, meta, _ = old.ridge_fit_same_data(features, y, 4.)
        assert np.allclose(pred, expected, atol=1e-6)
        assert abs(info['effective_df'] - meta['effective_df']) < 1e-6
        assert np.allclose(features@params['weights']+params['intercept'], pred)


def test_prepare_only_doesnt_load(tmp_path, capsys):
    out = tmp_path/'absent'
    args = exp.parser().parse_args(['--prepare-only','--output-root',str(out)])
    exp.run(args, loader=lambda *_: (_ for _ in ()).throw(AssertionError('model loaded')))
    reply = json.loads(capsys.readouterr().out.strip())
    assert reply['numbers'] == 507 and reply['unique_logbar'] == 239
    assert reply['total_evaluations'] == 1253
    assert reply['effective_df'] == 117
    assert not out.exists()


def test_capture_analyze_resume_and_change_df_without_model(tmp_path):
    root = tmp_path/'run'
    args = exp.parser().parse_args(['--limit','11','--effective-df','4',
                                    '--layers','1','--report-every','50','--output-root',str(root)])
    result = exp.run(args, loader=fake_loader)
    assert result['number_count'] == 11
    assert result['effective_df_requested'] == 4
    assert (root/'number_mapping.csv').is_file()
    assert (root/'fit_metrics.csv').is_file()
    assert (root/'band_metrics.csv').is_file()
    assert (root/'decoder_models.npz').is_file()
    assert len(list((root/'records').glob('*.npz'))) == 33
    with pytest.raises(RuntimeError, match='USE_RESUME'):
        exp.run(args, loader=fake_loader)
    args.analyze_only = True
    assert exp.run(args, loader=lambda *_: (_ for _ in ()).throw(AssertionError('model loaded'))) == result
    args.analyze_only = False
    args.resume = True
    assert exp.run(args, loader=lambda *_: (_ for _ in ()).throw(AssertionError('model loaded'))) == result
    args.effective_df = 5
    updated = exp.run(args, loader=lambda *_: (_ for _ in ()).throw(AssertionError('model loaded')))
    assert updated['effective_df_requested'] == 5
    args.max_prompt_tokens = 64
    with pytest.raises(RuntimeError, match='MANIFEST_DRIFT'):
        exp.run(args, loader=fake_loader)


def test_repeated_logbar_has_consistent_features(tmp_path):
    # The -2000 to 2000 test already found collisions; here check matching q keys.
    values = exp.grid()
    groups = exp.task_bank(values, 1)[1]
    assert any(len(members)>1 for members in groups.values())
    for q, members in groups.items():
        assert all(exp.encode(n) == exp.prompt('logbar', q).removeprefix('Number: ') for n in members)
