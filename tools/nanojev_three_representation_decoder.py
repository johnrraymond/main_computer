#!/usr/bin/env python3
"""Fit activation -> actual integer x for decimal, log-bar and paired captures.

Uses ONLY pre-existing frozen-model activation records; NO model loading,
NO new prompts, NO training/validation split. The decoder is a regularized
linear affine function, complexity-matched by effective degrees of freedom.
Results are explicitly IN-SAMPLE descriptive fits, not generalization claims.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np

import nanojev_decimal_number_activation_map as dec
import nanojev_logbar_activation_map as lb
import nanojev_paired_logbar_activation_map as paired

SCHEMA = 'nanojev.three-representation-decoder.v1'
CHANNEL = 'numeric_mean'
DEFAULT_ROOT = Path.home() / 'NanoJev' / 'runs' / 'three_representation_decoder_v1'


def load_records(baseline_root: Path, paired_root: Path):
    """Strictly match the same 253 numbers, model and bar precision."""
    baseline_manifest = json.loads((baseline_root / 'manifest.json').read_text(encoding='utf-8'))
    paired_manifest = json.loads((paired_root / 'manifest.json').read_text(encoding='utf-8'))
    baseline_id = json.loads((baseline_root / 'model_identity.json').read_text(encoding='utf-8'))
    paired_id = json.loads((paired_root / 'model_identity.json').read_text(encoding='utf-8'))
    values = lb.grid()
    if baseline_manifest.get('schema') != lb.SCHEMA or paired_manifest.get('schema') != paired.SCHEMA:
        raise RuntimeError('DECODER_MANIFEST_SCHEMA_MISMATCH')
    if baseline_manifest.get('values') != values or paired_manifest.get('values') != values:
        raise RuntimeError('DECODER_GRID_MISMATCH')
    precision = baseline_manifest.get('fractional_digits')
    if precision not in (1, 2) or paired_manifest.get('fractional_digits') != precision:
        raise RuntimeError('DECODER_PRECISION_MISMATCH')
    if (baseline_id.get('model') != paired_id.get('model')
            or baseline_id.get('model_info') != paired_id.get('model_info')):
        raise RuntimeError('DECODER_MODEL_IDENTITY_MISMATCH')
    if paired_manifest.get('baseline_root') and Path(paired_manifest['baseline_root']).resolve() != baseline_root.resolve():
        raise RuntimeError('DECODER_PAIRED_BASELINE_ROOT_MISMATCH')

    output = {'decimal': [], 'logbar': [], 'paired': []}
    for n in values:
        _, d = lb.read_record(lb.record_path(baseline_root, 'decimal', n),
                              dec.prompt_for(n), baseline_id)
        _, b = lb.read_record(lb.record_path(baseline_root, 'logbar', lb.quantize(n, precision)),
                              dec.PREFIX + lb.encode(n, precision), baseline_id)
        _, p = paired.read_paired(paired_root, n, paired_id, precision)
        output['decimal'].append(d[:, dec.CHANNELS.index(CHANNEL), :])
        output['logbar'].append(b[:, dec.CHANNELS.index(CHANNEL), :])
        output['paired'].append(p[:, paired.CHANNELS.index(CHANNEL), :])
    output = {k: np.stack(v).astype(np.float64) for k, v in output.items()}
    shapes = {v.shape for v in output.values()}
    if len(shapes) != 1 or not all(np.isfinite(v).all() for v in output.values()):
        raise RuntimeError('DECODER_ACTIVATION_SHAPE_MISMATCH')
    return np.array(values, dtype=np.float64), output, baseline_id, precision


def ridge_fit_same_data(x: np.ndarray, y: np.ndarray, target_df: float):
    """Return in-sample predictions for an affine ridge decoder of fixed capacity.

    x: rows x neurons. The intercept is unpenalized and fitted by centering.
    Choose ridge lambda so trace[K(K+lambda I)^-1] = min(target_df, rank).
    This gives each representation the same *effective* decoder capacity,
    even though the activation scales and spectral concentrations differ.
    """
    x, y = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
    if x.ndim != 2 or len(x) != len(y) or len(x) < 3 or not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError('DECODER_BAD_INPUT')
    if not 0 < target_df < len(x) - 1:
        raise ValueError('DECODER_BAD_DF_BUDGET')
    centered = x - x.mean(axis=0)
    centered_y = y - y.mean()
    gram = centered @ centered.T
    eigvals, basis = np.linalg.eigh(gram)
    eigvals = np.maximum(eigvals, 0)
    tolerance = max(float(eigvals[-1]) * 1e-11, 1e-12)
    rank = int(np.sum(eigvals > tolerance))
    if rank == 0:
        parameters = {'weights': np.zeros(x.shape[1], dtype=np.float64), 'intercept': float(y.mean())}
        return np.full_like(y, y.mean()), {'effective_df': 0.0, 'ridge_lambda': None, 'rank': 0}, parameters
    budget = min(float(target_df), 0.99 * rank)
    pos = eigvals[eigvals > tolerance]
    # Monotonically decreasing effective degrees of freedom with lambda.
    low, high = 0.0, float(pos[-1])
    while float(np.sum(pos / (pos + high))) > budget:
        high *= 2.0
    for _ in range(64):
        mid = (low + high) / 2
        if float(np.sum(pos / (pos + mid))) > budget:
            low = mid
        else:
            high = mid
    lam = high
    projected_y = basis.T @ centered_y
    transfer = np.zeros_like(eigvals)
    transfer[eigvals > tolerance] = eigvals[eigvals > tolerance] / (eigvals[eigvals > tolerance] + lam)
    alpha = np.zeros_like(eigvals)
    alpha[eigvals > tolerance] = projected_y[eigvals > tolerance] / (eigvals[eigvals > tolerance] + lam)
    coefficients = centered.T @ (basis @ alpha)
    intercept = float(y.mean() - x.mean(axis=0) @ coefficients)
    predictions = x @ coefficients + intercept
    parameters = {'weights': coefficients, 'intercept': intercept}
    return predictions, {'effective_df': float(np.sum(transfer)), 'ridge_lambda': float(lam), 'rank': rank}, parameters


def metrics(y, predicted):
    y, predicted = np.asarray(y, dtype=np.float64), np.asarray(predicted, dtype=np.float64)
    err = predicted - y
    denominator = float(np.sum((y - y.mean()) ** 2))
    return {
        'mae': float(np.mean(np.abs(err))),
        'rmse': float(np.sqrt(np.mean(err * err))),
        'max_abs_error': float(np.max(np.abs(err))),
        'r2': float(1 - np.sum(err * err) / denominator) if denominator > 0 else 0.0,
    }


def irreducible_bar_error(y, precision):
    """Best possible observed-sample MAE and RMSE from collided bar-only codes.

    For MAE the optimal per-code prediction is the median of its numbers;
    for RMSE, it is the mean. These are sample-specific lower bounds.
    """
    grouped = {}
    for value in y:
        grouped.setdefault(lb.quantize(int(value), precision), []).append(float(value))
    pred_mae = np.array([np.median(grouped[lb.quantize(int(n), precision)]) for n in y])
    pred_rmse = np.array([np.mean(grouped[lb.quantize(int(n), precision)]) for n in y])
    return {'distinct_codes': len(grouped), 'mae_floor': float(np.mean(np.abs(pred_mae - y))),
            'rmse_floor': float(np.sqrt(np.mean((pred_rmse - y)**2)))}


def compare(y, arrays, layers, target_df):
    report_rows, pred_rows, models = [], [], {}
    for layer in layers:
        for kind in ('decimal', 'logbar', 'paired'):
            predicted, complexity, parameters = ridge_fit_same_data(arrays[kind][:, layer, :], y, target_df)
            models[f'layer_{layer}_{kind}'] = parameters
            row = {'layer': layer, 'representation': kind, 'channel': CHANNEL,
                   'samples': len(y), 'df_budget': target_df, **complexity,
                   **metrics(y, predicted)}
            report_rows.append(row)
            for n, got in zip(y, predicted):
                pred_rows.append({'layer': layer, 'representation': kind,
                                  'number': int(n), 'prediction': float(got),
                                  'error': float(got - n), 'abs_error': float(abs(got - n))})
        # ONE linear decoder, shared weights and intercept across all three encodings.
        # No representation-specific intercepts or preprocessing at evaluation.
        all_activations = np.concatenate([arrays[kind][:, layer, :]
                                          for kind in ('decimal', 'logbar', 'paired')])
        all_labels = np.tile(y, 3)
        predicted, complexity, parameters = ridge_fit_same_data(all_activations, all_labels, target_df)
        models[f'layer_{layer}_shared'] = parameters
        overall = {'layer': layer, 'representation': 'shared_all', 'channel': CHANNEL,
                   'samples': len(all_labels), 'df_budget': target_df,
                   **complexity, **metrics(all_labels, predicted)}
        report_rows.append(overall)
        for i, kind in enumerate(('decimal', 'logbar', 'paired')):
            this = predicted[i * len(y):(i+1) * len(y)]
            report_rows.append({'layer': layer, 'representation': 'shared_on_' + kind,
                                'channel': CHANNEL, 'samples': len(y), 'df_budget': target_df,
                                **complexity, **metrics(y, this)})
            for n, got in zip(y, this):
                pred_rows.append({'layer': layer, 'representation': 'shared_on_' + kind,
                                  'number': int(n), 'prediction': float(got),
                                  'error': float(got - n), 'abs_error': float(abs(got - n))})
    return report_rows, pred_rows, models


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline-root', type=Path, default=lb.DEFAULT_ROOT)
    p.add_argument('--paired-root', type=Path, default=paired.DEFAULT_ROOT)
    p.add_argument('--output-root', type=Path, default=DEFAULT_ROOT)
    p.add_argument('--layers', default='16,28', help='Comma-separated layer indices; default 16,28')
    p.add_argument('--effective-df', type=float, default=16,
                   help='Effective degrees of freedom for every decoder; default 16')
    p.add_argument('--self-test', action='store_true')
    return p


def run(args):
    if args.effective_df <= 0:
        raise ValueError('DECODER_BAD_EFFECTIVE_DF')
    y, arrays, identity, precision = load_records(args.baseline_root.expanduser().resolve(),
                                                  args.paired_root.expanduser().resolve())
    try:
        layers = sorted({int(v.strip()) for v in args.layers.split(',') if v.strip()})
    except ValueError as exc:
        raise ValueError('DECODER_BAD_LAYERS') from exc
    if not layers or any(l < 0 or l >= arrays['decimal'].shape[1] for l in layers):
        raise ValueError('DECODER_LAYERS_OUT_OF_RANGE')
    if args.effective_df >= len(y) - 1:
        raise ValueError('DECODER_DF_EXCEEDS_OBSERVATIONS')
    rows, pred_rows, models = compare(y, arrays, layers, args.effective_df)
    destination = args.output_root.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    dec.write_csv(destination / 'fit_metrics.csv', rows, list(rows[0]))
    dec.write_csv(destination / 'predictions.csv', pred_rows, list(pred_rows[0]))
    np.savez_compressed(destination / 'decoder_models.npz',
                        **{key + '_weights': model['weights'] for key, model in models.items()},
                        **{key + '_intercept': np.float64(model['intercept']) for key, model in models.items()})
    per_layer = {
        str(layer): {r['representation']: {'mae': r['mae'], 'rmse': r['rmse'], 'r2': r['r2'],
                                          'max_abs_error': r['max_abs_error'], 'effective_df': r['effective_df']}
                     for r in rows if r['layer'] == layer}
        for layer in layers
    }
    summary = {
        'schema': SCHEMA, 'metric_scope': 'in_sample_only',
        'objective': 'decode original signed integer x from frozen activation vectors',
        'model_identity': identity, 'channel': CHANNEL, 'layers': layers,
        'numbers': len(y), 'effective_df_budget': args.effective_df,
        'logbar_collision_floor': irreducible_bar_error(y, precision),
        'results': per_layer,
        'warning': ('Training and evaluation use the same numbers. Small errors measure '
                    'the ability of a capacity-limited linear decoder to fit this dataset, '
                    'NOT performance on unseen integers. Bar-only collisions imply '
                    'nonzero irreducible error. Shared decoder uses one set of weights.')
    }
    dec.write_json(destination / 'summary.json', summary)
    dec.emit('three_representation_decoder_complete', layers=layers, numbers=len(y),
             effective_df=args.effective_df, result_root=str(destination),
             results=per_layer, logbar_collision_floor=summary['logbar_collision_floor'])
    return summary


def self_test():
    y = np.linspace(-20, 20, 41)
    x = np.column_stack((y, 2*y, np.ones_like(y)))
    pred, info, params = ridge_fit_same_data(x, y, 2)
    assert np.allclose(pred, x @ params['weights'] + params['intercept'])
    assert metrics(y, pred)['r2'] > .999
    assert 0 < info['effective_df'] <= 2.01
    vals = np.array([-2000.0, -1950.0, 1950.0, 2000.0])
    assert irreducible_bar_error(vals, 1)['mae_floor'] >= 0
    dec.emit('three_representation_decoder_self_test_passed')


def main(argv=None):
    args = parser().parse_args(argv)
    if args.self_test:
        self_test()
    else:
        run(args)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, FileNotFoundError, KeyError) as exc:
        dec.emit('three_representation_decoder_error', message=str(exc))
        sys.exit(1)
