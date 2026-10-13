import sys
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import nanojev_three_representation_decoder as base
import nanojev_three_representation_decoder_sweep as subject


def test_12_point_grid_and_invalid_budgets():
    assert subject.DEFAULT_BUDGETS == (16, 24, 32, 40, 48, 56, 64, 72, 80, 96, 112, 128)
    assert len(subject.parse_budgets('16,24,32,40,48,56,64,72,80,96,112,128')) == 12
    with pytest.raises(ValueError):
        subject.parse_budgets('16,16,128')
    with pytest.raises(ValueError):
        subject.parse_budgets('a,2')


def test_cached_fit_equivalent_to_original_ridge():
    r = np.random.default_rng(1337)
    x = r.normal(size=(33, 12))
    y = 2*x[:, 0] - x[:, 3] + r.normal(0, .5, 33)
    cached = subject.CachedRidge(x, y)
    for complexity in (1, 2, 5, 9, 15):
        got, info, params = cached.fit(complexity)
        ref, ref_info, _ = base.ridge_fit_same_data(x, y, complexity)
        np.testing.assert_allclose(got, ref, atol=1e-7, rtol=1e-7)
        assert info['effective_df'] == pytest.approx(ref_info['effective_df'], abs=1e-7)
        np.testing.assert_allclose(got, x @ params['weights'] + params['intercept'])


def test_logbar_rank_cap_properly_reported():
    y = np.arange(25, dtype=float)
    x = np.repeat(np.arange(5), 5).astype(float)[:, None]
    fit = subject.CachedRidge(x, y)
    _, info, _ = fit.fit(12)
    assert info['rank'] == 1
    assert info['capacity_capped']
    assert info['effective_df'] < 1


def test_sweep_identical_grid_and_saved_model_predictions():
    y = np.arange(-10, 11, dtype=float)
    x = np.stack((y, y*y, np.sin(y)), axis=1)
    arrays = {kind: np.stack((x*factor, x*factor), axis=1)
              for kind, factor in (('decimal', 1), ('logbar', .8), ('paired', 1.1))}
    rows, preds, models = subject.sweep(y, arrays, [1], (2., 4., 8.))
    assert len(rows) == 3*7
    assert len(preds) == 3*6*len(y)
    assert len(models) == 3*4
    for b in (2., 4., 8.):
        subset = [r for r in rows if r['df_budget'] == b]
        assert len(subset) == 7
        for kind in subject.KINDS:
            model = models[f'layer_1_df_{b:g}_{kind}']
            predictions = arrays[kind][:, 1, :] @ model['weights'] + model['intercept']
            assert base.metrics(y, predictions)['mae'] == pytest.approx(
                next(r['mae'] for r in subset if r['representation'] == kind))
    for kind in subject.PLOT_KINDS:
        svg = subject.svg_curve(rows, 1)
        ET.fromstring(svg)
        assert kind in svg


def test_constant_vector_handled():
    y = np.arange(20, dtype=float)
    x = np.ones((20, 10))
    fit = subject.CachedRidge(x, y)
    pred, info, param = fit.fit(4)
    assert info['rank'] == 0
    assert info['capacity_capped']
    assert np.allclose(pred, y.mean())
    assert np.all(param['weights'] == 0)
