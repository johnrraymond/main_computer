#!/usr/bin/env python3
"""Read-only, exhaustive radius-255 zero-line transfer probe.

Load the certified radius-127 digit-token backbone; measure novel pairs
A+B=delta for delta in {-2,-1,0,+1,+2} *before* radius-255 training.
Controls and an in-domain anchor are evaluated without training or TEST use.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path

import torch

from tools.nanojev_addition_curriculum import AdditionNet, POWERS
from tools.nanojev_zero_line_ladder import SCHEMA as LADDER_SCHEMA, prediction_values

SCHEMA = 'nanojev.zero-line-radius255-probe.v1'
INNER_RADIUS = 127
OUTER_RADIUS = 255
TARGET_RESIDUALS = (-2, -1, 0, 1, 2)
CONTROL_RESIDUALS = (-64, -32, -16, 16, 32, 64)
SHELL_BANDS = ((128, 159), (160, 191), (192, 223), (224, 255))
INNER_BANDS = ((0, 31), (32, 63), (64, 95), (96, 127))


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def band_for(magnitude, region):
    bands = INNER_BANDS if region == 'known' else SHELL_BANDS
    return next(f'{lo}-{hi}' for lo, hi in bands if lo <= magnitude <= hi)


def generate_cards(region, residuals):
    """Enumerate all valid signed ordered A,B pairs for each residual."""
    if region not in ('known', 'shell'):
        raise ValueError('ZERO_PROBE_INVALID_REGION')
    limit = INNER_RADIUS if region == 'known' else OUTER_RADIUS
    result = []
    for delta in residuals:
        for a in range(-limit, limit + 1):
            b = delta - a  # Oracle strictly for labels/data generation.
            if not -limit <= b <= limit:
                continue
            m = max(abs(a), abs(b))
            if region == 'shell' and m <= INNER_RADIUS:
                continue
            if region == 'known' and m > INNER_RADIUS:
                continue
            result.append({'a': a, 'b': b, 'delta': delta,
                           'region': region, 'band': band_for(m, region),
                           'a_sign': 'negative' if a < 0 else 'positive' if a > 0 else 'zero',
                           'magnitude': m})
    return result


def load_certified_model(ladder_root, device):
    root = Path(ladder_root).expanduser().resolve()
    config_path = root / 'config.json'
    checkpoint_path = root / 'rungs' / 'radius_127_passed.pt'
    if not config_path.is_file() or not checkpoint_path.is_file():
        raise FileNotFoundError('ZERO_PROBE_CERTIFIED_SOURCE_MISSING')
    conf = json.loads(config_path.read_text(encoding='utf-8'))
    if conf.get('schema') != LADDER_SCHEMA:
        raise RuntimeError('ZERO_PROBE_CONFIG_SCHEMA_MISMATCH')
    st = torch.load(checkpoint_path, map_location='cpu', weights_only=True)
    report = st.get('report', {})
    if (st.get('schema') != LADDER_SCHEMA + '.passed' or
            st.get('radius') != INNER_RADIUS or
            report.get('passed') is not True or
            report.get('streak', 0) < report.get('stability_target', 3) or
            report.get('mastery', {}).get('groups', {}).get('all', {}).get('accuracy') != 1.):
        raise RuntimeError('ZERO_PROBE_SOURCE_NOT_CERTIFIED')
    settings = conf['settings']
    model = AdditionNet('digit_tokens', int(settings['width']), int(settings['heads']),
                        int(settings['layers']), int(settings['rounds']),
                        int(settings['seed']))
    model.load_state_dict(st['model'], strict=True)
    model = model.to(device)
    model.eval()
    return model, {'checkpoint': str(checkpoint_path), 'checkpoint_sha256': sha256(checkpoint_path),
                   'config_sha256': sha256(config_path), 'step': int(st['step']),
                   'source_radius': INNER_RADIUS, 'source_streak': int(report['streak'])}


@torch.inference_mode()
def predict_cards(model, cards, device, batch_size):
    """Evaluate model outputs, never use the target to construct predictions."""
    powers = torch.tensor(POWERS, dtype=torch.long, device=device)
    for start in range(0, len(cards), batch_size):
        batch = cards[start:start + batch_size]
        x = torch.tensor([(row['a'], row['b']) for row in batch], dtype=torch.long, device=device)
        out = model(x)
        decoded, signs, digits = prediction_values(out)
        y = torch.tensor([row['delta'] for row in batch], dtype=torch.long, device=device)
        target_digits = ((y.abs().unsqueeze(-1) // powers) % 10)
        exact = ((decoded == y) & (signs == y.sign()) & (digits == target_digits).all(-1))
        for row, value, sign, passed in zip(batch, decoded.cpu().tolist(),
                                            signs.cpu().tolist(), exact.cpu().tolist()):
            row['prediction'] = int(value)
            row['predicted_sign'] = int(sign)
            row['exact'] = bool(passed)
            row['abs_error'] = abs(int(value) - row['delta'])
            row['sign_correct'] = sign == (1 if row['delta'] > 0 else -1 if row['delta'] < 0 else 0)
            row['false_zero'] = value == 0 and row['delta'] != 0
            row['missed_zero'] = value != 0 and row['delta'] == 0
    return cards


def metrics(cards):
    if not cards:
        return None
    n = len(cards)
    return {'cards': n, 'correct': sum(r['exact'] for r in cards),
            'exact_rate': sum(r['exact'] for r in cards) / n,
            'sign_correct': sum(r['sign_correct'] for r in cards),
            'sign_rate': sum(r['sign_correct'] for r in cards) / n,
            'mae': sum(r['abs_error'] for r in cards) / n,
            'false_zero': sum(r['false_zero'] for r in cards),
            'missed_zero': sum(r['missed_zero'] for r in cards)}


def breakdown(cards):
    by_residual = {str(d): metrics([r for r in cards if r['delta'] == d])
                   for d in sorted(set(r['delta'] for r in cards))}
    band_names = list(dict.fromkeys(r['band'] for r in cards))
    by_band = {name: metrics([r for r in cards if r['band'] == name]) for name in band_names}
    matrix = {str(delta): {name: metrics([r for r in cards if r['delta'] == delta and r['band'] == name])
                           for name in band_names}
              for delta in sorted(set(r['delta'] for r in cards))}
    by_orientation = {name: metrics([r for r in cards if r['a_sign'] == name])
                      for name in ('negative', 'zero', 'positive')}
    return {'overall': metrics(cards), 'by_residual': by_residual, 'by_magnitude_band': by_band,
            'residual_x_band': matrix, 'by_A_sign': by_orientation}


def invariant_audits(cards):
    """Check actual model predictions across compositions, not latent similarity."""
    index = {(r['a'], r['b']): r for r in cards}
    swaps = []
    for (a, b), r in index.items():
        if a < b and (b, a) in index:
            other = index[(b, a)]
            swaps.append(bool(r['exact'] and other['exact'] and
                              r['prediction'] == other['prediction']))
    pair_counts = []
    for delta in sorted(set(r['delta'] for r in cards)):
        candidates = [r for r in cards if r['delta'] == delta]
        bands = sorted(set(r['band'] for r in candidates))
        for i, b1 in enumerate(bands):
            g1 = [r for r in candidates if r['band'] == b1]
            for b2 in bands[i+1:]:
                g2 = [r for r in candidates if r['band'] == b2]
                # Exact fraction of cross-band pairs that BOTH preserve delta.
                total = len(g1) * len(g2)
                good = sum(r['exact'] for r in g1) * sum(r['exact'] for r in g2)
                if total:
                    pair_counts.append({'delta': delta, 'bands': [b1, b2],
                                        'correct_pairs': good, 'pairs': total,
                                        'rate': good / total})
    n = sum(p['pairs'] for p in pair_counts)
    k = sum(p['correct_pairs'] for p in pair_counts)
    return {'commutativity_both_exact': {'pairs': len(swaps), 'both_exact': sum(swaps),
                                        'rate': sum(swaps)/len(swaps) if swaps else None},
            'cross_band_same_delta': {'pairs': n, 'both_exact': k,
                                      'rate': k/n if n else None, 'per_pair': pair_counts}}


def atomic_json(path, obj):
    p = Path(path)
    tmp = p.with_suffix(p.suffix + '.tmp')
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    os.replace(tmp, p)


def run(args):
    if args.device == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('ZERO_PROBE_CUDA_UNAVAILABLE')
    if args.eval_batch_size < 1 or args.cpu_threads < 1:
        raise ValueError('ZERO_PROBE_INVALID_ARGUMENTS')
    root = Path(args.ladder_output).expanduser().resolve()
    outdir = Path(args.output).expanduser().resolve()
    if root == outdir or root in outdir.parents or outdir in root.parents:
        raise ValueError('ZERO_PROBE_OUTPUT_MUST_BE_SEPARATE_FROM_LADDER')
    if outdir.exists() and any(outdir.iterdir()):
        raise FileExistsError('ZERO_PROBE_OUTPUT_OVERWRITE_REFUSED')
    torch.set_num_threads(args.cpu_threads)
    model, source = load_certified_model(root, args.device)
    groups = {
        'known_near': generate_cards('known', TARGET_RESIDUALS),
        'unseen_near': generate_cards('shell', TARGET_RESIDUALS),
        'unseen_controls': generate_cards('shell', CONTROL_RESIDUALS),
    }
    for name, cards in groups.items():
        predict_cards(model, cards, args.device, args.eval_batch_size)
    checkpoint = Path(source['checkpoint'])
    if sha256(checkpoint) != source['checkpoint_sha256']:
        raise RuntimeError('ZERO_PROBE_CHECKPOINT_CHANGED_DURING_READONLY_EVAL')
    report = {'schema': SCHEMA, 'read_only': True, 'trained': False,
              'test_untouched': True, 'source': source,
              'target_residuals': TARGET_RESIDUALS, 'control_residuals': CONTROL_RESIDUALS,
              'known_near': breakdown(groups['known_near']),
              'unseen_near': breakdown(groups['unseen_near']),
              'unseen_controls': breakdown(groups['unseen_controls']),
              'invariance_near': invariant_audits(groups['unseen_near']),
              'interpretation': 'held-out radius-255 outer-shell inference, no radius-255 training'}
    outdir.mkdir(parents=True, exist_ok=True)
    atomic_json(outdir / 'result.json', report)
    with (outdir / 'cards.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream)
        writer.writerow(('set', 'A', 'B', 'target_delta', 'predicted_delta', 'predicted_sign',
                         'exact', 'sign_correct', 'abs_error', 'false_zero', 'missed_zero',
                         'magnitude', 'magnitude_band', 'A_sign'))
        for name, cards in groups.items():
            for r in cards:
                writer.writerow((name, r['a'], r['b'], r['delta'], r['prediction'],
                                 r['predicted_sign'], int(r['exact']), int(r['sign_correct']),
                                 r['abs_error'], int(r['false_zero']), int(r['missed_zero']),
                                 r['magnitude'], r['band'], r['a_sign']))
    print('ZERO_LINE_RADIUS255_PROBE=' + json.dumps({
        'source_step': source['step'],
        'known_near': report['known_near']['overall'],
        'unseen_near': report['unseen_near']['overall'],
        'unseen_by_residual': {k: round(v['exact_rate'], 6)
                               for k, v in report['unseen_near']['by_residual'].items()},
        'unseen_controls': report['unseen_controls']['overall'],
        'cross_band': report['invariance_near']['cross_band_same_delta']['rate'],
        'report': str(outdir / 'result.json')}, sort_keys=True), flush=True)
    print('ZERO_LINE_SOURCE_IMMUTABLE=TRUE ZERO_LINE_TEST_UNTOUCHED=TRUE NO_TRAINING=TRUE', flush=True)
    return report


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ladder-output', type=Path, required=True,
                   help='Directory of the completed radius-127 ladder')
    p.add_argument('--output', type=Path, required=True,
                   help='New, separate directory for this read-only probe')
    p.add_argument('--device', choices=('cpu', 'cuda'), default='cuda')
    p.add_argument('--eval-batch-size', type=int, default=256)
    p.add_argument('--cpu-threads', type=int, default=4)
    return p


def main(argv=None):
    return run(parser().parse_args(argv))


if __name__ == '__main__':
    main()
