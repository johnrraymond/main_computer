#!/usr/bin/env python3
"""Twelve-point in-sample numerical reconstruction curve from frozen activations.

Adds no model inference or new activation captures. Saves individual fitted
linear decoders, all reconstructed values, all metrics and standalone SVG curves.
The x axis in SVG uses *achieved*, rather than requested, effective df.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from xml.sax.saxutils import escape

import numpy as np

import nanojev_decimal_number_activation_map as dec
import nanojev_three_representation_decoder as base

SCHEMA = 'nanojev.three-representation-decoder-sweep.v1'
DEFAULT_ROOT = Path.home() / 'NanoJev' / 'runs' / 'three_representation_decoder_sweep_v1'
DEFAULT_BUDGETS = (16, 24, 32, 40, 48, 56, 64, 72, 80, 96, 112, 128)
KINDS = ('decimal', 'logbar', 'paired')
PLOT_KINDS = ('decimal', 'logbar', 'paired', 'shared_all')
COLORS = {'decimal': '#276eaa', 'logbar': '#b45309', 'paired': '#16846e', 'shared_all': '#9955a0'}


def parse_budgets(raw: str) -> tuple[float, ...]:
    try:
        budgets = tuple(float(v.strip()) for v in raw.split(',') if v.strip())
    except ValueError as exc:
        raise ValueError('DECODER_BAD_SWEEP_BUDGETS') from exc
    if len(budgets) < 2 or not all(np.isfinite(x) and x > 0 for x in budgets):
        raise ValueError('DECODER_BAD_SWEEP_BUDGETS')
    if any(a >= b for a, b in zip(budgets, budgets[1:])):
        raise ValueError('DECODER_BUDGETS_NOT_INCREASING')
    return budgets


class CachedRidge:
    """One Gram eigendecomposition for many ridge effective-DF budgets."""

    def __init__(self, x: np.ndarray, y: np.ndarray):
        self.x = np.asarray(x, dtype=np.float64)
        self.y = np.asarray(y, dtype=np.float64)
        if (self.x.ndim != 2 or self.y.ndim != 1 or len(self.x) != len(self.y)
                or len(self.y) < 3 or not np.isfinite(self.x).all() or not np.isfinite(self.y).all()):
            raise ValueError('DECODER_BAD_INPUT')
        self.xmean = self.x.mean(axis=0)
        self.ymean = float(self.y.mean())
        self.centered = self.x - self.xmean
        eigenvalues, eigenvectors = np.linalg.eigh(self.centered @ self.centered.T)
        eigenvalues = np.maximum(eigenvalues, 0.0)
        tolerance = max(float(eigenvalues[-1]) * 1e-11, 1e-12)
        valid = eigenvalues > tolerance
        self.s = eigenvalues[valid]
        self.u = eigenvectors[:, valid]
        self.z = self.u.T @ (self.y - self.ymean)
        self.rank = len(self.s)

    def fit(self, requested_df: float):
        if not np.isfinite(requested_df) or not 0 < requested_df < len(self.y) - 1:
            raise ValueError('DECODER_BAD_DF_BUDGET')
        if self.rank == 0:
            coefficients = np.zeros(self.x.shape[1], dtype=np.float64)
            intercept = self.ymean
            info = {'effective_df': 0.0, 'ridge_lambda': None, 'rank': 0,
                    'capacity_capped': True}
            return np.full_like(self.y, self.ymean), info, {'weights': coefficients, 'intercept': intercept}
        target = min(float(requested_df), 0.99 * self.rank)
        low, high = 0., float(self.s[-1])
        while float(np.sum(self.s / (self.s + high))) > target:
            high *= 2.
        for _ in range(64):
            mid = (low + high) / 2
            if float(np.sum(self.s / (self.s + mid))) > target:
                low = mid
            else:
                high = mid
        lam = high
        alpha = self.u @ (self.z / (self.s + lam))
        coefficients = self.centered.T @ alpha
        intercept = float(self.ymean - self.xmean @ coefficients)
        predictions = self.x @ coefficients + intercept
        info = {'effective_df': float(np.sum(self.s / (self.s + lam))),
                'ridge_lambda': float(lam), 'rank': self.rank,
                'capacity_capped': float(requested_df) > 0.99 * self.rank + 1e-9}
        return predictions, info, {'weights': coefficients, 'intercept': intercept}


def sweep(y: np.ndarray, arrays: dict, layers: list[int], budgets: tuple[float, ...]):
    """Fit 3 independent and 1 truly shared decoder at every layer and budget."""
    metric_rows, prediction_rows, models = [], [], {}
    for layer in layers:
        fitted = {kind: CachedRidge(arrays[kind][:, layer, :], y) for kind in KINDS}
        shared_x = np.concatenate([arrays[kind][:, layer, :] for kind in KINDS])
        shared_y = np.tile(y, len(KINDS))
        fitted['shared_all'] = CachedRidge(shared_x, shared_y)
        for budget in budgets:
            name = format(budget, 'g')
            for kind, fit in fitted.items():
                predicted, info, model = fit.fit(budget)
                models[f'layer_{layer}_df_{name}_{kind}'] = model
                metric_rows.append({'layer': layer, 'representation': kind,
                                    'channel': base.CHANNEL, 'samples': len(fit.y),
                                    'df_budget': budget, **info, **base.metrics(fit.y, predicted)})
                if kind == 'shared_all':
                    for i, subkind in enumerate(KINDS):
                        subset = predicted[i*len(y):(i+1)*len(y)]
                        metric_rows.append({'layer': layer,
                                            'representation': 'shared_on_' + subkind,
                                            'channel': base.CHANNEL, 'samples': len(y),
                                            'df_budget': budget, **info, **base.metrics(y, subset)})
                        for n, got in zip(y, subset):
                            prediction_rows.append({'layer': layer,
                                                    'representation': 'shared_on_' + subkind,
                                                    'df_budget': budget, 'number': int(n),
                                                    'prediction': float(got),
                                                    'error': float(got - n),
                                                    'abs_error': float(abs(got - n))})
                else:
                    for n, got in zip(y, predicted):
                        prediction_rows.append({'layer': layer,
                                                'representation': kind, 'df_budget': budget,
                                                'number': int(n), 'prediction': float(got),
                                                'error': float(got - n),
                                                'abs_error': float(abs(got - n))})
    return metric_rows, prediction_rows, models


def svg_curve(rows: list[dict], layer: int, metric: str = 'mae') -> str:
    """Self-contained plot showing actual effective df; no plotting library needed."""
    selected = {kind: sorted((r for r in rows if r['layer'] == layer and r['representation'] == kind),
                             key=lambda r: r['df_budget']) for kind in PLOT_KINDS}
    series = [r for records in selected.values() for r in records]
    width, height = 940, 580
    left, right, top, bottom = 90, 240, 64, 72
    pw, ph = width-left-right, height-top-bottom
    xmin = 0.0
    xmax = max(float(r['df_budget']) for r in series)*1.04
    ymin = 0.0
    ymax = max(float(r[metric]) for r in series)*1.12 or 1.
    xpx = lambda val: left + (float(val)-xmin)/(xmax-xmin)*pw
    ypx = lambda val: top + ph - (float(val)-ymin)/(ymax-ymin)*ph
    output = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
              '<rect width="100%" height="100%" fill="white"/>',
              f'<text x="{left}" y="29" font-size="20" font-family="Arial" font-weight="bold">Layer {layer} — {metric.upper()} vs achieved effective degrees of freedom</text>']
    for tick in range(6):
        value = ymax*tick/5
        y = ypx(value)
        output.append(f'<line x1="{left}" x2="{left+pw}" y1="{y:.2f}" y2="{y:.2f}" stroke="#dedede"/>')
        output.append(f'<text x="{left-9}" y="{y+5:.2f}" text-anchor="end" font-size="13" font-family="Arial">{value:.0f}</text>')
    for tick in (0, 16, 32, 48, 64, 80, 96, 112, 128):
        x = xpx(tick)
        output.append(f'<text x="{x:.2f}" y="{top+ph+20}" text-anchor="middle" font-size="12" font-family="Arial">{tick}</text>')
    output.append(f'<line x1="{left}" y1="{top+ph}" x2="{left+pw}" y2="{top+ph}" stroke="#333"/>')
    output.append(f'<text x="{left+pw/2}" y="{height-23}" text-anchor="middle" font-size="15" font-family="Arial">Achieved effective degrees of freedom</text>')
    output.append(f'<text x="{left}" y="{top-11}" font-size="13" font-family="Arial">{metric.upper()} (original number units)</text>')
    for i, kind in enumerate(PLOT_KINDS):
        recs = selected[kind]
        color = COLORS[kind]
        pts = ' '.join(f'{xpx(r["effective_df"]):.2f},{ypx(r[metric]):.2f}' for r in recs)
        output.append(f'<polyline fill="none" stroke="{color}" stroke-width="2.5" points="{pts}"/>')
        for r in recs:
            x, y = xpx(r['effective_df']), ypx(r[metric])
            if r['capacity_capped']:
                output.append(f'<rect x="{x-4:.2f}" y="{y-4:.2f}" width="8" height="8" fill="white" stroke="{color}" stroke-width="2"/>')
            else:
                output.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3" fill="{color}"/>')
        ly = top+18+i*31
        output.append(f'<line x1="{left+pw+21}" y1="{ly}" x2="{left+pw+50}" y2="{ly}" stroke="{color}" stroke-width="3"/>')
        output.append(f'<text x="{left+pw+58}" y="{ly+5}" font-size="13" font-family="Arial">{escape(kind)}</text>')
    output.append(f'<text x="{left+pw+21}" y="{top+ph-43}" font-size="12" font-family="Arial">Outlined square =</text>')
    output.append(f'<text x="{left+pw+21}" y="{top+ph-26}" font-size="12" font-family="Arial">capacity capped</text>')
    output.append(f'<text x="{left+pw+21}" y="{top+ph-9}" font-size="12" font-family="Arial">In-sample fit only</text>')
    output.append('</svg>')
    return '\n'.join(output)+'\n'


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline-root', type=Path, default=base.lb.DEFAULT_ROOT)
    p.add_argument('--paired-root', type=Path, default=base.paired.DEFAULT_ROOT)
    p.add_argument('--output-root', type=Path, default=DEFAULT_ROOT)
    p.add_argument('--layers', default='16,28')
    p.add_argument('--df-budgets', default=','.join(map(str, DEFAULT_BUDGETS)),
                   help='Increasing comma-separated df budgets; default is 12 values from 16 through 128')
    p.add_argument('--self-test', action='store_true')
    return p


def run(args):
    budgets = parse_budgets(args.df_budgets)
    y, arrays, identity, precision = base.load_records(args.baseline_root.expanduser().resolve(),
                                                       args.paired_root.expanduser().resolve())
    try:
        layers = sorted({int(v.strip()) for v in args.layers.split(',') if v.strip()})
    except ValueError as exc:
        raise ValueError('DECODER_BAD_LAYERS') from exc
    if not layers or any(l < 0 or l >= arrays['decimal'].shape[1] for l in layers):
        raise ValueError('DECODER_LAYERS_OUT_OF_RANGE')
    if max(budgets) >= len(y) - 1:
        raise ValueError('DECODER_DF_EXCEEDS_OBSERVATIONS')
    rows, preds, models = sweep(y, arrays, layers, budgets)
    dest = args.output_root.expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)
    dec.write_csv(dest / 'fit_curve.csv', rows, list(rows[0]))
    dec.write_csv(dest / 'predictions.csv', preds, list(preds[0]))
    np.savez_compressed(dest / 'decoder_models.npz',
                        **{key+'_weights': model['weights'] for key, model in models.items()},
                        **{key+'_intercept': np.float64(model['intercept']) for key, model in models.items()})
    for layer in layers:
        for metric in ('mae', 'rmse'):
            (dest / f'{metric}_curve_layer{layer}.svg').write_text(svg_curve(rows, layer, metric), encoding='utf-8')
    final_rows = [r for r in rows if r['df_budget'] == budgets[-1]]
    summary = {'schema': SCHEMA, 'metric_scope': 'in_sample_only', 'numbers': len(y),
               'layers': layers, 'df_budgets': list(budgets), 'points_per_curve': len(budgets),
               'channel': base.CHANNEL, 'model_identity': identity,
               'logbar_collision_floor': base.irreducible_bar_error(y, precision),
               'last_budget_results': {str(layer): {r['representation']: {
                   'mae': r['mae'], 'rmse': r['rmse'], 'r2': r['r2'],
                   'effective_df': r['effective_df'], 'rank': r['rank'],
                   'capacity_capped': r['capacity_capped']}
                   for r in final_rows if r['layer'] == layer} for layer in layers},
               'interpretation': ('Identical captured numbers used for fit and evaluation. '
                                  'High-dimensional decoders can overfit; this is an in-sample '
                                  'reconstruction capacity curve, not a generalization test. '
                                  'x-axes reflect achieved effective df; requested budget may '
                                  'exceed numerical rank, especially for collided log-bar codes.')}
    dec.write_json(dest / 'summary.json', summary)
    dec.emit('three_representation_decoder_sweep_complete',
             numbers=len(y), layers=layers, df_budgets=list(budgets),
             comparisons=len(rows), root=str(dest), last_budget_results=summary['last_budget_results'])
    return summary


def self_test():
    budgets = parse_budgets(','.join(map(str, DEFAULT_BUDGETS)))
    assert len(budgets) == 12 and budgets[0] == 16 and budgets[-1] == 128
    y = np.arange(-20, 21, dtype=float)
    x = np.column_stack((y, y**2, np.cos(y)))
    fit = CachedRidge(x, y)
    for df in (1., 2., 12.):
        p, info, model = fit.fit(df)
        reference, info_ref, model_ref = base.ridge_fit_same_data(x, y, df)
        assert np.allclose(p, reference, rtol=1e-7, atol=1e-6)
        assert np.allclose(model['weights'], model_ref['weights'], rtol=1e-6, atol=1e-6)
        assert abs(info['effective_df'] - info_ref['effective_df']) < 1e-6
    dec.emit('three_representation_decoder_sweep_self_test_passed')


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
        dec.emit('three_representation_decoder_sweep_error', message=str(exc))
        sys.exit(1)
