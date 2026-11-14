import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
import nanojev_three_representation_decoder as subject
import nanojev_decimal_number_activation_map as dec
import nanojev_logbar_activation_map as lb
import nanojev_paired_logbar_activation_map as paired


def test_ridge_recovers_simple_linear_value():
    y = np.arange(-20, 21, dtype=float)
    matrix = np.stack((y, y*y, np.sin(y), np.ones_like(y)), axis=1)
    predicted, info, params = subject.ridge_fit_same_data(matrix, y, 2.0)
    assert np.allclose(predicted, matrix @ params['weights'] + params['intercept'])
    assert subject.metrics(y, predicted)['r2'] > .99
    assert info['effective_df'] == pytest.approx(2, abs=.0001)


def test_ridge_rejects_bad_complexity_and_shapes():
    with pytest.raises(ValueError, match='DECODER_BAD_DF_BUDGET'):
        subject.ridge_fit_same_data(np.ones((5, 2)), np.ones(5), 5)
    with pytest.raises(ValueError, match='DECODER_BAD_INPUT'):
        subject.ridge_fit_same_data(np.ones((5, 2)), np.ones(4), 2)


def test_constant_inputs_return_intercept():
    pred, info, params = subject.ridge_fit_same_data(np.ones((8, 4)), np.arange(8), 2)
    assert np.all(params['weights'] == 0)
    assert np.allclose(pred, 3.5)
    assert info['effective_df'] == 0


def test_bar_collision_floor_is_nonzero_for_duplicate_codes():
    y = np.asarray([-2000, -1950, 1950, 2000], dtype=float)
    v = subject.irreducible_bar_error(y, 1)
    assert v['distinct_codes'] < len(y)
    assert v['mae_floor'] > 0 and v['rmse_floor'] > 0


def test_shared_and_independent_fit_shape():
    y = np.arange(-4, 5, dtype=float)
    vec = np.stack([y, y**2, np.sin(y)], axis=1)
    a = np.stack([vec, vec*2], axis=1)
    arrays = {'decimal': a, 'logbar': a*.8, 'paired': a*1.4}
    rows, pred, models = subject.compare(y, arrays, [1], 2)
    assert len(models) == 4
    assert {r['representation'] for r in rows} == {
        'decimal', 'logbar', 'paired', 'shared_all',
        'shared_on_decimal', 'shared_on_logbar', 'shared_on_paired'}
    assert len(pred) == 6 * len(y)
    assert all(np.isfinite(r['mae']) for r in rows)


def test_actual_record_layout_and_identity_validation(tmp_path, monkeypatch):
    # Tiny equivalent of the real frozen captures, validating their paths and metadata.
    vals = [-1, 0, 1]
    monkeypatch.setattr(lb, 'grid', lambda: vals)
    base, pair = tmp_path/'base', tmp_path/'paired'
    (base/'records').mkdir(parents=True)
    (pair/'records').mkdir(parents=True)
    info = {'model': 'qwen', 'model_info': {'model_type': 'qwen3', 'device': 'cuda'}}
    base_id = {'schema': lb.SCHEMA, **info}
    pair_id = {'schema': paired.SCHEMA, **info}
    dec.write_json(base/'manifest.json', {'schema': lb.SCHEMA, 'values': vals, 'fractional_digits': 1})
    dec.write_json(pair/'manifest.json', {'schema': paired.SCHEMA, 'values': vals,
                                          'fractional_digits': 1, 'baseline_root': str(base.resolve())})
    dec.write_json(base/'model_identity.json', base_id)
    dec.write_json(pair/'model_identity.json', pair_id)
    for n in vals:
        for kind, key, prompt, owner in (
            ('decimal', n, dec.prompt_for(n), base),
            ('logbar', lb.quantize(n), dec.PREFIX+lb.encode(n), base),
            ('paired', n, paired.paired_prompt(n), pair),
        ):
            vector = np.full((29, len(paired.CHANNELS) if kind == 'paired' else len(dec.CHANNELS), 8), n, dtype=np.float32)
            if kind == 'paired':
                path = paired.record_path(owner, n)
                identity = pair_id
            else:
                path = lb.record_path(owner, kind, key)
                identity = base_id
            meta = {'prompt': prompt, 'number': n, 'prompt_sha256': dec.digest(prompt),
                    'model_identity': identity, 'shape': list(vector.shape)}
            dec.save_record(path, meta, vector)
    y, arrays, _, prec = subject.load_records(base, pair)
    assert prec == 1 and y.tolist() == vals
    assert {key: value.shape for key, value in arrays.items()} == {k: (3,29,8) for k in arrays}
    # Drift in model identity must not be silently accepted.
    dec.write_json(pair/'model_identity.json', {'schema': paired.SCHEMA, 'model':'different', 'model_info':{}})
    with pytest.raises(RuntimeError, match='DECODER_MODEL_IDENTITY_MISMATCH'):
        subject.load_records(base, pair)
