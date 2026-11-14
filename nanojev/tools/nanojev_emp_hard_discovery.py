#!/usr/bin/env python3
"""Large, read-only, hypothesis-neutral zero-line failure discovery.

Score many diverse TRAIN-only cancellation problems against one frozen four-bank
checkpoint, retain *all* successes and failures, discover descriptive predictors
on one set of anchors, and validate them on disjoint TRAIN anchors. Nothing is
trained or routed. DEV and sealed TEST are never sampled by this tool.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import random
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

import torch

from tools import nanojev_emp_dual_bank as dual
from tools import nanojev_emp_dual_bank_diagnostic as diag
from tools import nanojev_emp_dual_bank_ladder as ladder
from tools import nanojev_emp_hard_case_detector as detector
from tools import nanojev_zero_line_outward_return as outward
from tools import nanojev_zero_line_sampled_radius_ladder as old
from tools.nanojev_zero_line_ladder import atomic_json
from tools.nanojev_zero_line_radius255_probe import sha256

SCHEMA = 'nanojev.emp-hard-discovery.v1'
SAMPLER = 'alternate-native-broad-v1'


def broad_cards(radius, seed):
    """Variable-shape, TRAIN-family cancellation probes; no hypothesized cause.

    Equal exposure across answer categories and operand bands is by design;
    inferred rates always describe this mixture, NOT natural-world prevalence.
    """
    rng = random.Random(f'emp-hard-discovery-broad:{seed}:{radius}')
    classes = ('zero', 'unit', 'inner', 'log', 'uniform', 'edge')
    cursor = 0
    while True:
        band = cursor % len(old.BANDS)
        category = classes[(cursor // len(old.BANDS)) % len(classes)]
        cursor += 1
        lo, hi = old.BANDS[band]
        lo = max(lo, 32)
        for _ in range(15000):
            m = rng.randint(lo, hi)
            if old.stable_fold(m, seed) == 'train' and diag.specialist_partition(m, seed) == 'mine':
                break
        else:
            raise RuntimeError('EMP_HARD_DISCOVERY_BROAD_TRAIN_FAMILY_UNAVAILABLE')
        ceiling = min(radius, 2 * m)
        if category == 'zero':
            residual = 0
        elif category == 'unit':
            residual = 1
        elif category == 'inner':
            residual = rng.randint(1, min(32, ceiling))
        elif category == 'log':
            residual = max(1, min(ceiling, int(round(math.exp(rng.uniform(0, math.log(ceiling)))))))
        elif category == 'uniform':
            residual = rng.randint(1, ceiling)
        else:
            # Arithmetical boundaries are one of many generic probe families,
            # not a preselected borrow/carry hypothesis.
            base = 10 ** rng.randrange(max(1, len(str(ceiling))))
            residual = max(1, min(ceiling, base + rng.choice((-2, -1, 0, 1, 2))))
        if residual and rng.getrandbits(1):
            residual = -residual
        pair = old.pair_for(m, residual, swap=bool(rng.getrandbits(1)),
                            orientation=1 if rng.getrandbits(1) else -1)
        if pair is None:
            raise AssertionError('EMP_HARD_DISCOVERY_INVALID_ZERO_LINE_PAIR')
        yield ((*pair, residual, band, m), f'broad:{category}')


def native_cards(radius, seed):
    rng = random.Random(f'emp-hard-discovery-native:{seed}:{radius}')
    weights = [1.0] * len(old.BANDS)
    cursor = 0
    while True:
        batch, cursor = outward.generate_batch(rng, radius, seed, 256, weights,
                                                cursor, 'outward', radius, radius)
        for card in batch:
            card = tuple(int(x) for x in card)
            if old.stable_fold(card[4], seed) != 'train':
                raise RuntimeError('EMP_HARD_DISCOVERY_TRAIN_SPLIT_LEAK')
            if diag.specialist_partition(card[4], seed) == 'mine':
                yield card, 'native'


def candidates(radius, seed):
    """Globally deduplicated, deterministic mixture; no model-guided sampling."""
    native = native_cards(radius, seed)
    broad = broad_cards(radius, seed)
    seen = set()
    i = 0
    while True:
        source = native if i % 2 == 0 else broad
        for _ in range(100000):
            card, origin = next(source)
            a, b, d, band, anchor = card
            if (a, b) in seen:
                continue
            if a + b != d or max(abs(a), abs(b)) != anchor or abs(d) > radius:
                raise RuntimeError('EMP_HARD_DISCOVERY_INVALID_CANDIDATE')
            if old.stable_fold(anchor, seed) != 'train' or diag.specialist_partition(anchor, seed) != 'mine':
                raise RuntimeError('EMP_HARD_DISCOVERY_SPLIT_LEAK')
            seen.add((a, b))
            yield card, origin
            i += 1
            break
        else:
            raise RuntimeError('EMP_HARD_DISCOVERY_DUPLICATE_POOL_EXHAUSTED')


def labels_for(row, predictions):
    target = int(row[2])
    wrong = [name for name in dual.BANKS if not diag.correct(target, int(predictions[name]))]
    return {
        'wrong_count': len(wrong), 'wrong_banks': wrong,
        'all_four_wrong': len(wrong) == 4,
        'three_or_more_wrong': len(wrong) >= 3,
        'two_or_more_wrong': len(wrong) >= 2,
        'any_wrong': bool(wrong),
    }


def scored_rows(cards, predictions, checkpoint_sha, radius):
    if set(predictions) != set(dual.BANKS) or any(len(v) != len(cards) for v in predictions.values()):
        raise RuntimeError('EMP_HARD_DISCOVERY_PREDICTIONS_MISALIGNED')
    for i, (card, origin) in enumerate(cards):
        a, b, d, band, anchor = map(int, card)
        guesses = {name: int(predictions[name][i]) for name in dual.BANKS}
        row = {'a': a, 'b': b, 'target': d, 'band': band, 'anchor': anchor,
               'sampler': origin, 'predictions': guesses, 'radius': radius,
               'checkpoint_sha256': checkpoint_sha, 'split': 'train',
               **labels_for(card, guesses)}
        yield row


def atomic_gzip_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as raw:
            with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as gz:
                for row in rows:
                    gz.write((json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n').encode('utf8'))
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def iter_chunk_rows(folder, manifest):
    for chunk in manifest['chunks']:
        path = folder / chunk['path']
        if not path.is_file() or sha256(path) != chunk['sha256']:
            raise RuntimeError('EMP_HARD_DISCOVERY_CHUNK_TAMPERED_OR_MISSING')
        read = 0
        with gzip.open(path, 'rt', encoding='utf8') as handle:
            for line in handle:
                read += 1
                yield json.loads(line)
        if read != chunk['n']:
            raise RuntimeError('EMP_HARD_DISCOVERY_CHUNK_COUNT_DRIFT')


def _new_stats():
    return {'total': 0, 'hard': 0, 'features': defaultdict(lambda: [0, 0]),
            'groups': defaultdict(lambda: [0, 0])}


def analyze_rows(rows, seed, reservoir_max=30000, min_support=100):
    """Full-population feature comparisons; independent heldout anchor validation."""
    stats = defaultdict(_new_stats)
    rng = random.Random(f'emp-hard-discovery-reservoir:{seed}')
    reservoir = []
    total = 0
    patterns = Counter()
    bank_failures = Counter()
    seen_anchors = set()
    source_breakdown = defaultdict(lambda: [0, 0])
    failure_examples = []
    for row in rows:
        total += 1
        anchor = int(row['anchor'])
        fold = detector.detector_fold(anchor, seed)
        hard = int(row['all_four_wrong'])
        seen_anchors.add(anchor)
        patterns[str(row['wrong_count'])] += 1
        for bank in row['wrong_banks']:
            bank_failures[bank] += 1
        group = stats[fold]
        group['total'] += 1
        group['hard'] += hard
        fx = detector.features(row['a'], row['b'])
        for name, present in fx.items():
            if present:
                counts = group['features'][name]
                counts[0] += 1
                counts[1] += hard
        src = 'native' if row['sampler'] == 'native' else 'broad'
        source_breakdown[src][0] += 1
        source_breakdown[src][1] += hard
        for dim, value in (
            ('sampler', src),
            ('residual_shell', diag.shell(row['target'])),
            ('operand_shell', diag.shell(row['anchor'])),
            ('residual_sign', 'zero' if row['target'] == 0 else 'positive' if row['target'] > 0 else 'negative'),
            ('band', str(row['band'])),
        ):
            counts = group['groups'][f'{dim}:{value}']
            counts[0] += 1
            counts[1] += hard
        # Unbiased reservoir: keep successes AND failures. Never downsample by label.
        brief = (row['a'], row['b'], row['target'], row['band'], anchor, row['predictions'])
        if len(reservoir) < reservoir_max:
            reservoir.append(brief)
        else:
            j = rng.randrange(total)
            if j < reservoir_max:
                reservoir[j] = brief
        if hard and len(failure_examples) < 12:
            failure_examples.append({'a': row['a'], 'b': row['b'], 'target': row['target'],
                                     'predictions': row['predictions'], 'sampler': row['sampler']})
    if not total:
        raise RuntimeError('EMP_HARD_DISCOVERY_NO_SCORED_CASES')
    fit, holdout = stats['fit'], stats['evaluation']
    if not fit['total'] or not holdout['total']:
        raise RuntimeError('EMP_HARD_DISCOVERY_MISSING_ANCHOR_FOLD')

    def enriched(partition, name, value):
        s = stats[partition]
        n, hits = s['features'][name]
        if not value:
            n, hits = s['total']-n, s['hard']-hits
        baseline = s['hard']/s['total']
        return {'n': n, 'hard': hits, 'hard_rate': round(hits/n, 6) if n else None,
                'lift_vs_fold_baseline': round((hits/n)/baseline, 4) if n and baseline else None,
                'hard_rate_95pct_interval': detector.wilson(hits, n)}

    possible = []
    for name in sorted(detector.features(1, -1)):
        for val in (True, False):
            row = enriched('fit', name, val)
            other = enriched('fit', name, not val)
            if min(row['n'], other['n']) < min_support:
                continue
            difference = (row['hard_rate'] - other['hard_rate'])
            # Rank by separation supported by sample size, not tiny high lifts.
            strength = abs(difference) * math.sqrt(min(row['n'], other['n']))
            possible.append((strength, name, val, row, other))
    possible.sort(key=lambda x: (-x[0], x[1], x[2]))
    ranking = []
    for strength, name, val, found, contrast in possible[:30]:
        ranking.append({'feature': name, 'value': val,
                        'discovery': found, 'discovery_contrast': contrast,
                        'validation': enriched('evaluation', name, val),
                        'validation_contrast': enriched('evaluation', name, not val),
                        'discovery_strength': round(strength, 4)})
    # Detector tree is fitted and tested on an *unbiased* sample from all rows,
    # keeping full-population prevalence and separate anchor validation.
    det_report = None
    if len(reservoir) >= 100:
        cards = [r[:5] for r in reservoir]
        predicted = {name: [r[5][name] for r in reservoir] for name in dual.BANKS}
        try:
            det_report = detector.make_detector(cards, predicted, (dual.BANKS[0], dual.BANKS[1]),
                                                seed, target='all_four_wrong', depth=4, min_leaf=48)
        except ValueError as exc:
            if 'INSUFFICIENT_POSITIVE_HOLDOUT' not in str(exc):
                raise
            det_report = {'status': 'insufficient_hard_examples_in_uniform_reservoir'}

    def summary(s):
        return {'n': s['total'], 'all_four_wrong': s['hard'],
                'hard_rate': round(s['hard']/s['total'], 6) if s['total'] else None}
    groups = {}
    for key in sorted(set(fit['groups']) | set(holdout['groups'])):
        n = sum(stats[fold]['groups'][key][0] for fold in ('fit', 'evaluation'))
        hard = sum(stats[fold]['groups'][key][1] for fold in ('fit', 'evaluation'))
        groups[key] = {'n': n, 'hard': hard, 'hard_rate': round(hard/n, 6)}
    return {
        'scanned': total, 'unique_anchor_families': len(seen_anchors),
        'failures_by_wrong_bank_count': dict(sorted(patterns.items())),
        'per_bank_failures': dict(bank_failures),
        'all_four_wrong': patterns.get('4', 0),
        'all_four_wrong_rate': round(patterns.get('4', 0)/total, 6),
        'sampling': {k: {'n': v[0], 'all_four_wrong': v[1],
                         'rate': round(v[1]/v[0], 6)} for k, v in sorted(source_breakdown.items())},
        'discovery_fold': summary(fit), 'validation_fold': summary(holdout),
        'top_discovered_features_with_heldout_validation': ranking,
        'cohorts': groups, 'uniform_reservoir_n': len(reservoir),
        'predictive_detector': det_report,
        'first_hard_examples': failure_examples,
        'interpretation': 'Feature associations and detector lift are predictive, not proof of causation. '
                          'All feature and class rates are conditional on the stated sampling mixture. '
                          'Heldout anchors are new to the diagnostic, not necessarily to backbone training.',
    }


def _verify_paths(root, report_dir):
    if report_dir == root or root in report_dir.parents or report_dir in root.parents:
        raise ValueError('EMP_HARD_DISCOVERY_REPORT_MUST_BE_SEPARATE_FROM_CAMPAIGN')


def _model_context(root, ladder_output, radius, device, cpu_threads):
    cfg_path = root/'config.json'
    cfg = json.loads(cfg_path.read_text('utf8'))
    if cfg['schema'] != ladder.SCHEMA:
        raise RuntimeError('EMP_HARD_DISCOVERY_CAMPAIGN_SCHEMA_MISMATCH')
    stages = sorted(int(p.name[len('outward_'):]) for p in (root/'stages').glob('outward_*')
                    if p.is_dir() and (p/'latest.pt').is_file())
    if not stages:
        raise RuntimeError('EMP_HARD_DISCOVERY_NO_CHECKPOINT')
    radius = radius or stages[-1]
    if radius not in stages:
        raise RuntimeError('EMP_HARD_DISCOVERY_RADIUS_UNAVAILABLE')
    stage = root/'stages'/f'outward_{radius}'
    sc = json.loads((stage/'config.json').read_text('utf8'))
    if sc.get('radius') != radius or sc.get('campaign_config_sha256') != sha256(cfg_path):
        raise RuntimeError('EMP_HARD_DISCOVERY_STAGE_PROVENANCE_DRIFT')
    state, state_hash = diag._safe_checkpoint(stage/'latest.pt', radius)
    for item, key in (('source_left.pt', 'left_source_sha256'),
                      ('source_right.pt', 'right_source_sha256')):
        if sha256(root/item) != cfg[key]:
            raise RuntimeError('EMP_HARD_DISCOVERY_SOURCE_SNAPSHOT_DRIFT')
    if device == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('EMP_HARD_DISCOVERY_CUDA_UNAVAILABLE')
    torch.set_num_threads(cpu_threads)
    baseline, _ = ladder.load_certified_model(ladder_output, 'cpu')
    left, right = (dual.checked_source(root / item) for item in ('source_left.pt', 'source_right.pt'))
    banks = ladder.from_source(baseline, left, right, state['banks'], device)
    return cfg, radius, stage/'latest.pt', state_hash, state['step'], banks


def _manifest_new(config):
    return {'schema': SCHEMA, 'config': config, 'chunks': [], 'scanned': 0,
            'all_four_wrong': 0, 'complete': False}


def run(args):
    root = Path(args.output).expanduser().resolve()
    ladder_output = Path(args.ladder_output).expanduser().resolve()
    cfg, radius, checkpoint, checkpoint_sha, step, banks = _model_context(
        root, ladder_output, args.radius, args.device, args.cpu_threads)
    dest = (Path(args.report_dir).expanduser().resolve() if args.report_dir
            else root.parent/(root.name+f'_hard_discovery_{radius}'))
    _verify_paths(root, dest)
    config = {'campaign': str(root), 'radius': radius, 'seed': cfg['seed'],
              'checkpoint_sha256': checkpoint_sha, 'campaign_config_sha256': sha256(root/'config.json'),
              'target_hard': args.target_hard, 'max_candidates': args.max_candidates,
              'chunk_size': args.chunk_size, 'sampler': SAMPLER}
    manifest_path = dest/'manifest.json'
    if manifest_path.exists():
        if not args.resume:
            raise FileExistsError('EMP_HARD_DISCOVERY_ALREADY_EXISTS: pass --resume')
        manifest = json.loads(manifest_path.read_text('utf8'))
        if manifest['schema'] != SCHEMA or manifest['config'] != config:
            raise RuntimeError('EMP_HARD_DISCOVERY_RESUME_CONFIG_OR_CHECKPOINT_DRIFT')
        # Verify the whole committed corpus *before* trusting its counts.
        prior_count = sum(1 for _ in iter_chunk_rows(dest, manifest))
        if prior_count != manifest['scanned']:
            raise RuntimeError('EMP_HARD_DISCOVERY_MANIFEST_COUNT_DRIFT')
    else:
        if dest.exists() and any(dest.iterdir()):
            raise FileExistsError('EMP_HARD_DISCOVERY_REPORT_DIR_NOT_EMPTY')
        dest.mkdir(parents=True, exist_ok=True)
        manifest = _manifest_new(config)
        atomic_json(manifest_path, manifest)
    if not manifest['complete']:
        stream = candidates(radius, cfg['seed'])
        # Deterministically reconstruct generator state and the dedup set.
        for _ in range(manifest['scanned']):
            next(stream)
        while manifest['scanned'] < args.max_candidates and manifest['all_four_wrong'] < args.target_hard:
            count = min(args.chunk_size, args.max_candidates-manifest['scanned'])
            batch = [next(stream) for _ in range(count)]
            cards = [v[0] for v in batch]
            if sha256(checkpoint) != checkpoint_sha:
                raise RuntimeError('EMP_HARD_DISCOVERY_CHECKPOINT_CHANGED_STOP_TRAINER_FIRST')
            predictions = diag.predict_training_cases(banks, cards, args.device, args.eval_batch_size)
            labelled = list(scored_rows(batch, predictions, checkpoint_sha, radius))
            if sha256(checkpoint) != checkpoint_sha:
                raise RuntimeError('EMP_HARD_DISCOVERY_CHECKPOINT_CHANGED_STOP_TRAINER_FIRST')
            rel = f'chunks/chunk-{len(manifest["chunks"]):06d}.jsonl.gz'
            chunk_path = dest/rel
            if chunk_path.exists():
                raise RuntimeError('EMP_HARD_DISCOVERY_UNCOMMITTED_CHUNK_EXISTS')
            atomic_gzip_rows(chunk_path, labelled)
            hits = sum(r['all_four_wrong'] for r in labelled)
            manifest['chunks'].append({'path': rel, 'sha256': sha256(chunk_path),
                                       'n': count, 'all_four_wrong': hits})
            manifest['scanned'] += count
            manifest['all_four_wrong'] += hits
            atomic_json(manifest_path, manifest)
            print('EMP_HARD_DISCOVERY_PROGRESS='+json.dumps({
                'scanned': manifest['scanned'], 'all_four_wrong': manifest['all_four_wrong'],
                'goal': args.target_hard, 'hard_rate': round(manifest['all_four_wrong']/manifest['scanned'], 5),
                'chunks': len(manifest['chunks'])}, sort_keys=True), flush=True)
        if sha256(checkpoint) != checkpoint_sha:
            raise RuntimeError('EMP_HARD_DISCOVERY_CHECKPOINT_CHANGED_STOP_TRAINER_FIRST')
        manifest['complete'] = True
        atomic_json(manifest_path, manifest)
    if sha256(checkpoint) != checkpoint_sha:
        raise RuntimeError('EMP_HARD_DISCOVERY_CHECKPOINT_CHANGED_STOP_TRAINER_FIRST')
    analysis = analyze_rows(iter_chunk_rows(dest, manifest), cfg['seed'],
                            args.reservoir_max, args.min_support)
    if analysis['scanned'] != manifest['scanned'] or analysis['all_four_wrong'] != manifest['all_four_wrong']:
        raise RuntimeError('EMP_HARD_DISCOVERY_ANALYSIS_COUNT_MISMATCH')
    hard_path = dest/'all_four_wrong.jsonl.gz'
    # Export all four-bank failures, with the rest retained as labelled controls in chunks.
    atomic_gzip_rows(hard_path, (r for r in iter_chunk_rows(dest, manifest) if r['all_four_wrong']))
    report = {'schema': SCHEMA, 'checkpoint_sha256': checkpoint_sha, 'step': step,
              'radius': radius, 'target_hard': args.target_hard,
              'target_reached': manifest['all_four_wrong'] >= args.target_hard,
              'max_candidates': args.max_candidates, 'analysis': analysis,
              'manifest': str(manifest_path), 'all_four_wrong_export': str(hard_path),
              'all_four_wrong_export_sha256': sha256(hard_path),
              'retained_population': str(dest/'chunks'),
              'data_policy': 'TRAIN-only anchors with specialist-reserved families excluded; no DEV/TEST, '
                             'no optimizer updates, no routing, frozen checkpoint, complete scored controls retained.'}
    report_path = dest/'report.json'
    atomic_json(report_path, report)
    print('EMP_HARD_DISCOVERY_RESULT='+json.dumps({
        'scanned': analysis['scanned'], 'all_four_wrong': analysis['all_four_wrong'],
        'hard_rate': analysis['all_four_wrong_rate'], 'target_reached': report['target_reached'],
        'top_features': [{'feature': r['feature'], 'value': r['value'],
                          'validation': r['validation']} for r in
                         analysis['top_discovered_features_with_heldout_validation'][:8]],
        'predictive_signal': (analysis['predictive_detector'] or {}).get('predictive_signal'),
        'report': str(report_path), 'hard_export': str(hard_path)}, sort_keys=True), flush=True)
    return report


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True, help='Existing four-bank campaign root (read only)')
    p.add_argument('--ladder-output', required=True, help='Original certified ladder used by campaign')
    p.add_argument('--radius', type=int, help='Default: last saved outward radius')
    p.add_argument('--device', choices=('cpu', 'cuda'), default='cuda')
    p.add_argument('--cpu-threads', type=int, default=2)
    p.add_argument('--eval-batch-size', type=int, default=256)
    p.add_argument('--target-hard', type=int, default=20000)
    p.add_argument('--max-candidates', type=int, default=500000)
    p.add_argument('--chunk-size', type=int, default=4096)
    p.add_argument('--reservoir-max', type=int, default=30000)
    p.add_argument('--min-support', type=int, default=100)
    p.add_argument('--report-dir', help='Diagnostic output folder, never inside campaign')
    p.add_argument('--resume', action='store_true', help='Resume/finish a compatible already-started scan')
    return p


if __name__ == '__main__':
    args = parser().parse_args()
    if min(args.target_hard, args.max_candidates, args.chunk_size,
           args.reservoir_max, args.min_support, args.cpu_threads, args.eval_batch_size) < 1:
        raise ValueError('EMP_HARD_DISCOVERY_ARGUMENT_OUT_OF_RANGE')
    run(args)
