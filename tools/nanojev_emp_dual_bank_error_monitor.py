#!/usr/bin/env python3
"""Checkpoint-safe error-variance and bias instrumentation for the four-bank EMP ladder.

This module wraps the *unchanged* outward ladder. It adds measurements to the
existing frozen DEV probes via a temporary in-process wrapper around the
already called summary_for(); it performs NO additional forward passes or
training updates. The campaign's source SHA pins therefore remain valid.

TEST is deliberately NOT opened here: sealed test predictions can be assessed
later with --monitor-predictions-json, without feeding TEST results into gates.
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import math
import statistics
from contextlib import contextmanager
from pathlib import Path

import torch

from tools import nanojev_emp_dual_bank as dual
from tools import nanojev_emp_dual_bank_ladder as ladder
from tools.nanojev_zero_line_ladder import atomic_json
from tools.nanojev_zero_line_radius255_probe import sha256

SCHEMA = 'nanojev.emp-dual-bank-error-monitor.v1'
HISTORY = 'error_bias_monitor_v1.json'
CSV = 'error_bias_monitor_v1.csv'


def _moments(values):
    """Descriptive population SD (ddof=0) for an enumerated evaluation deck."""
    if not values:
        return {'n': 0, 'mean': None, 'stddev': None}
    xs = [float(v) for v in values]
    # fmean/pstdev avoid loss from hand-rolled sum-of-squares arithmetic.
    return {'n': len(xs), 'mean': statistics.fmean(xs),
            'stddev': statistics.pstdev(xs) if len(xs) > 1 else 0.0}


def error_statistics(rows, predictions):
    """Compute integer and normalized bias/dispersion without assuming normality.

    Raw error: prediction - target (numeric direction bias, original units).
    Signed relative error: (prediction - target) / abs(target), nonzero only.
    Scale error: prediction / target - 1, nonzero only. A negative value
    represents estimates towards/past zero even for negative targets.
    Magnitude error: abs(prediction) / abs(target) - 1, nonzero only.
    The three normalized quantities are NOT substituted for one another.
    """
    if len(rows) != len(predictions):
        raise ValueError('EMP_ERR_CARD_PREDICTION_MISMATCH')
    raw, rel, scale, mag, absolute = [], [], [], [], []
    zero_raw, pos_scale, neg_scale, pos_rel, neg_rel = [], [], [], [], []
    overshoot = undershoot = tip = 0
    for card, prediction in zip(rows, predictions):
        target = int(card[2])
        guess = int(prediction)
        error = guess - target
        raw.append(error)
        absolute.append(abs(error))
        if target == 0:
            zero_raw.append(error)
            continue
        q = guess / target
        signed_relative = error / abs(target)
        signed_scale = q - 1
        magnitude_relative = abs(guess) / abs(target) - 1
        rel.append(signed_relative)
        scale.append(signed_scale)
        mag.append(magnitude_relative)
        if target > 0:
            pos_scale.append(signed_scale)
            pos_rel.append(signed_relative)
        else:
            neg_scale.append(signed_scale)
            neg_rel.append(signed_relative)
        overshoot += magnitude_relative > 0
        undershoot += magnitude_relative < 0
        tip += q <= 0
    positive = _moments(pos_scale)
    negative = _moments(neg_scale)
    stats = {
        'cards': len(rows), 'nonzero': len(scale), 'zero_cards': len(zero_raw),
        'stddev_convention': 'population_ddof_0',
        'signed_error': _moments(raw),
        'absolute_error_mean': statistics.fmean(absolute) if absolute else None,
        'error_rmse': math.sqrt(statistics.fmean(x*x for x in raw)) if raw else None,
        'signed_relative_error': _moments(rel),
        'signed_scale_error': _moments(scale),
        'absolute_magnitude_error': _moments(mag),
        'positive_target_scale_error': positive,
        'negative_target_scale_error': negative,
        'positive_target_signed_relative_error': _moments(pos_rel),
        'negative_target_signed_relative_error': _moments(neg_rel),
        'scale_bias_sign_asymmetry': (
            positive['mean'] - negative['mean']
            if positive['n'] and negative['n'] else None),
        'magnitude_overshoot_count': overshoot,
        'magnitude_undershoot_count': undershoot,
        'tipping_violations': tip,
        'tipping_violation_rate': tip / len(scale) if scale else None,
        'zero_error': _moments(zero_raw),
        'zero_nonexact': sum(x != 0 for x in zero_raw),
    }
    return stats


def augmented_summary(rows, predictions):
    summary = _original_summary(rows, predictions)
    # Additional descriptive fields do not alter existing accuracy/gating fields.
    summary['error_bias_stats'] = error_statistics(rows, predictions)
    return summary


_original_summary = dual.summary_for


def condensed(scores):
    """Compact, structured stream for all four banks and both DEV views."""
    out = {}
    for bank in dual.BANKS:
        out[bank] = {}
        for subset in ('frontier', 'outer'):
            result = []
            for view, row in enumerate(scores[bank][subset]):
                stats = row.get('error_bias_stats')
                if stats is None:
                    continue
                result.append({
                    'view': view, 'n': stats['nonzero'],
                    'signed_bias': _round(stats['signed_error']['mean']),
                    'signed_stddev': _round(stats['signed_error']['stddev']),
                    'relative_bias': _round(stats['signed_relative_error']['mean']),
                    'relative_stddev': _round(stats['signed_relative_error']['stddev']),
                    'scale_bias': _round(stats['signed_scale_error']['mean']),
                    'scale_stddev': _round(stats['signed_scale_error']['stddev']),
                    'magnitude_bias': _round(stats['absolute_magnitude_error']['mean']),
                    'pos_scale_bias': _round(stats['positive_target_scale_error']['mean']),
                    'neg_scale_bias': _round(stats['negative_target_scale_error']['mean']),
                    'sign_asymmetry': _round(stats['scale_bias_sign_asymmetry']),
                })
            out[bank][subset] = result
    return out


def _round(x):
    return round(x, 6) if x is not None else None


def _update_monitor(stage, record, cfg_path, output_to_console):
    stats = condensed(record['banks'])
    if not any(stats[name]['frontier'] or stats[name]['outer'] for name in stats):
        return  # Existing historical records are not backfilled with invented values.
    dest = stage / HISTORY
    source_hash = sha256(cfg_path)
    if dest.is_file():
        hist = json.loads(dest.read_text('utf-8'))
        if hist.get('schema') != SCHEMA or hist.get('campaign_config_sha256') != source_hash:
            raise RuntimeError('EMP_ERR_MONITOR_HISTORY_DRIFT')
    else:
        hist = {'schema': SCHEMA, 'campaign_config_sha256': source_hash,
                'statistics': 'population_stddev_ddof_0; descriptive_bias_not_statistical_significance',
                'split': 'dev', 'probes': []}
    radius = int(record['radius'])
    step = int(record['step'])
    entry = {'radius': radius, 'step': step, 'source_baseline': bool(record.get('source_baseline', False)),
             'banks': stats}
    if hist['probes'] and (radius, step) < (hist['probes'][-1]['radius'], hist['probes'][-1]['step']):
        raise RuntimeError('EMP_ERR_MONITOR_NONMONOTONIC_STEP')
    if hist['probes'] and hist['probes'][-1]['radius'] == radius and hist['probes'][-1]['step'] == step:
        if hist['probes'][-1] != entry:
            raise RuntimeError('EMP_ERR_MONITOR_DUPLICATE_STEP_DRIFT')
        return
    hist['probes'].append(entry)
    atomic_json(dest, hist)
    _write_csv(stage / CSV, hist)
    if output_to_console:
        print('EMP_LADDER_ERROR_STATS=' + json.dumps(entry, sort_keys=True), flush=True)


def _write_csv(path, hist):
    columns = ['radius', 'step', 'bank', 'subset', 'view', 'n',
               'signed_bias', 'signed_stddev', 'relative_bias', 'relative_stddev',
               'scale_bias', 'scale_stddev', 'magnitude_bias',
               'pos_scale_bias', 'neg_scale_bias', 'sign_asymmetry']
    # Unlike a CSV append, atomic replace ensures interruption-safe writes.
    import io
    import os
    buf = io.StringIO(newline='')
    writer = csv.DictWriter(buf, fieldnames=columns)
    writer.writeheader()
    for probe in hist['probes']:
        for name, subsets in probe['banks'].items():
            for subset, views in subsets.items():
                for row in views:
                    writer.writerow({'radius': probe['radius'], 'step': probe['step'],
                                     'bank': name, 'subset': subset, **row})
    target = path.with_suffix(path.suffix + '.tmp')
    target.write_text(buf.getvalue(), encoding='utf-8')
    os.replace(target, path)


@contextmanager
def attach():
    """Temporary instrumentation; original training code never changes on disk."""
    old_summary = dual.summary_for
    old_save = ladder.save_stage
    if old_summary is not _original_summary:
        raise RuntimeError('EMP_ERR_MONITOR_ALREADY_ATTACHED')

    def monitored_save(stage, banks, opt, controls, events, rng, step, cursor, weights, streak, radius):
        old_save(stage, banks, opt, controls, events, rng, step, cursor, weights, streak, radius)
        log_path = stage / 'history.json'
        baseline_path = stage / 'baseline.json'
        if baseline_path.is_file() and not (stage / HISTORY).is_file():
            baseline = json.loads(baseline_path.read_text('utf-8'))
            _update_monitor(stage, baseline, stage.parents[1] / 'config.json', True)
        if log_path.is_file():
            log = json.loads(log_path.read_text('utf-8'))
            if log:
                _update_monitor(stage, log[-1], stage.parents[1] / 'config.json', True)

    try:
        dual.summary_for = augmented_summary
        ladder.save_stage = monitored_save
        yield
    finally:
        dual.summary_for = old_summary
        ladder.save_stage = old_save


def inspect(args, requested_radius=None):
    """Read-only diagnostic for an exhausted stage with no further training."""
    if not args.resume:
        raise ValueError('EMP_ERR_INSPECT_REQUIRES_RESUME')
    root, cfg, _, left, right = ladder.source_config(args)
    stages = root / 'stages'
    candidates = sorted((int(p.name.split('_')[-1]) for p in stages.glob('outward_*')
                         if p.is_dir() and (p / 'latest.pt').is_file())) if stages.exists() else []
    if not candidates:
        raise FileNotFoundError('EMP_ERR_NO_STAGE_CHECKPOINT')
    radius = requested_radius if requested_radius is not None else candidates[-1]
    if radius not in candidates:
        raise FileNotFoundError('EMP_ERR_RADIUS_CHECKPOINT_MISSING')
    stage = stages / f'outward_{radius}'
    checkpoint = torch.load(stage / 'latest.pt', map_location='cpu', weights_only=True)
    if checkpoint.get('schema') != ladder.SCHEMA + '.checkpoint' or checkpoint.get('radius') != radius:
        raise RuntimeError('EMP_ERR_STAGE_STATE_MISMATCH')
    base, _ = ladder.load_certified_model(args.ladder_output, 'cpu')
    banks = ladder.from_source(base, left, right, checkpoint['banks'], args.device)
    scores, _ = ladder.stage_probe(banks, args, radius)
    record = {'step': int(checkpoint['step']), 'radius': radius, 'banks': scores}
    _update_monitor(stage, record, root / 'config.json', True)
    return record


def analyze_prediction_json(path, output):
    """Offline analysis of externally generated, already opened DEV/TEST predictions.

    Does not look up held-out problems, select models, or touch training state.
    Input: {"split":"test", "rows":[{"target": 512,"prediction":500},...]}
    """
    obj = json.loads(Path(path).read_text('utf-8'))
    if not isinstance(obj, dict) or obj.get('split') not in ('dev', 'test'):
        raise ValueError('EMP_ERR_PREDICTION_SPLIT_REQUIRED')
    items = obj.get('rows')
    if not isinstance(items, list) or not items:
        raise ValueError('EMP_ERR_PREDICTIONS_REQUIRED')
    rows, predictions = [], []
    per_bank_view = {}
    for item in items:
        if not isinstance(item, dict) or type(item.get('target')) is not int or type(item.get('prediction')) is not int:
            raise ValueError('EMP_ERR_INTEGER_PREDICTIONS_REQUIRED')
        bank, view, subset = item.get('bank', 'unspecified'), item.get('view', 'unspecified'), item.get('subset', 'unspecified')
        if not isinstance(bank, str) or not bank or not isinstance(view, (str, int)) or not isinstance(subset, str) or not subset:
            raise ValueError('EMP_ERR_INVALID_TEST_GROUP')
        key = (bank, str(view), subset)
        card = (0, item['target'], item['target'])
        rows.append(card)
        predictions.append(item['prediction'])
        group = per_bank_view.setdefault(key, ([], []))
        group[0].append(card)
        group[1].append(item['prediction'])
    by_bank_view = {}
    for (bank, view, subset), (cards, preds) in sorted(per_bank_view.items()):
        by_bank_view.setdefault(bank, {}).setdefault(view, {})[subset] = error_statistics(cards, preds)
    report = {'schema': SCHEMA + '.offline', 'split': obj['split'],
              'statistics': error_statistics(rows, predictions), 'by_bank_view': by_bank_view}
    atomic_json(Path(output), report)
    print('EMP_ERROR_STATS_OFFLINE=' + json.dumps(report, sort_keys=True), flush=True)
    return report


def main(argv=None):
    monitor = argparse.ArgumentParser(add_help=False)
    monitor.add_argument('--monitor-inspect-only', action='store_true')
    monitor.add_argument('--monitor-radius', type=int)
    monitor.add_argument('--monitor-predictions-json')
    monitor.add_argument('--monitor-offline-output')
    extra, rest = monitor.parse_known_args(argv)
    if extra.monitor_predictions_json:
        if rest or extra.monitor_inspect_only or extra.monitor_radius is not None or not extra.monitor_offline_output:
            raise ValueError('EMP_ERR_OFFLINE_ARGUMENTS')
        return analyze_prediction_json(extra.monitor_predictions_json, extra.monitor_offline_output)
    if extra.monitor_offline_output:
        raise ValueError('EMP_ERR_OFFLINE_ONLY')
    args = ladder.parser().parse_args(rest)
    if args.device == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('EMP_LADDER_CUDA_UNAVAILABLE')
    torch.set_num_threads(args.cpu_threads)
    with attach():
        if extra.monitor_inspect_only:
            return inspect(args, extra.monitor_radius)
        if extra.monitor_radius is not None:
            raise ValueError('EMP_ERR_RADIUS_REQUIRES_INSPECT')
        return ladder.run(args)


if __name__ == '__main__':
    main()
