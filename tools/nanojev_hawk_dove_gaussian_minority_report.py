#!/usr/bin/env python3
"""Read-only, checkpoint-derived Minority Report diagnostic for three Gaussian predictors.

The third predictor is the *numeric-only reference*, not a Qwen head. For
TinyStories/Pythia, the frozen activations are read from their certified caches;
no transformers model, training, or network access is needed.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path

import torch

from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as ref
from tools import nanojev_hawk_dove_gaussian_population_conditional as base
from tools import nanojev_hawk_dove_gaussian_two_backbone as duo

SCHEMA = 'nanojev.hawk-dove.gaussian-minority-report.v1'
MODELS = ('reference', 'pythia', 'tinystories')
TIERS = ref.TIERS
PAIR_NAMES = (('reference', 'pythia'), ('reference', 'tinystories'),
              ('pythia', 'tinystories'))
RULES = ('reference', 'pythia', 'tinystories', 'mean_three', 'median_three',
         'mean_reference_pythia', 'mean_reference_tinystories',
         'mean_pythia_tinystories')


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def _load_checkpoint(path: Path, expected_schema: str):
    if not path.is_file():
        raise FileNotFoundError('MINORITY_MISSING_CHECKPOINT:' + str(path))
    checkpoint = torch.load(path, map_location='cpu', weights_only=True)
    if checkpoint.get('schema') != expected_schema or not isinstance(checkpoint.get('identity'), dict):
        raise RuntimeError('MINORITY_CHECKPOINT_SCHEMA_DRIFT:' + str(path))
    if int(checkpoint['step']) < 0:
        raise RuntimeError('MINORITY_CHECKPOINT_STEP_INVALID:' + str(path))
    return checkpoint


def _args_from_identity(ident: dict, device: str, batch_size: int):
    return argparse.Namespace(**{**ident, 'device': device, 'eval_batch_size': batch_size,
                                  'cpu_threads': 1})


def _dataset_verified(reference_ident: dict, device: torch.device, batch_size: int):
    args = _args_from_identity(reference_ident, 'cpu', batch_size)
    ref._validate(args)
    data, domain, manifest = ref._dataset(args, torch.device('cpu'))
    expected = ref._identity(args, manifest, domain)
    if expected != reference_ident:
        raise RuntimeError('MINORITY_REFERENCE_IDENTITY_OR_MATRIX_SPLIT_DRIFT')
    return data, domain, manifest, args


def _verify_duo(checkpoint, backbone, run_path, reference_ident, data, manifest):
    ident = checkpoint['identity']
    if ident.get('backbone') != backbone or ident.get('baseline_identity') != reference_ident:
        raise RuntimeError('MINORITY_DUO_OR_REFERENCE_IDENTITY_DRIFT:' + backbone)
    config_file = run_path / 'config.json'
    manifest_file = run_path / 'matrix_split_manifest.json'
    cache_file = run_path / 'feature_cache.pt'
    for p in (config_file, manifest_file, cache_file):
        if not p.is_file():
            raise FileNotFoundError('MINORITY_DUO_PROVENANCE_MISSING:' + str(p))
    if json.loads(config_file.read_text(encoding='utf8')) != ident:
        raise RuntimeError('MINORITY_DUO_CONFIG_DRIFT:' + backbone)
    if json.loads(manifest_file.read_text(encoding='utf8')) != manifest:
        raise RuntimeError('MINORITY_DUO_MATRIX_MANIFEST_DRIFT:' + backbone)
    cache = torch.load(cache_file, map_location='cpu', weights_only=True)
    dim = duo.validate_cache(cache, backbone, data)
    if duo.identity(_args_from_identity(reference_ident, 'cpu', 128), backbone,
                    manifest, tuple(tuple(x) for x in reference_ident['domain']), cache) != ident:
        raise RuntimeError('MINORITY_DUO_CAPTURE_IDENTITY_DRIFT:' + backbone)
    return cache, dim


@torch.no_grad()
def _predict(model, data, domain, device, batch_size, cache=None):
    model.eval()
    out = {}
    for split in ('dev', 'test'):
        rows = data[split]['rows']
        preds = []
        for start in range(0, len(rows), batch_size):
            end = min(len(rows), start + batch_size)
            numerical = data[split]['features'][start:end]
            if cache is None:
                features = numerical
            else:
                activations = cache['splits'][split]['activations'][start:end]
                features = torch.cat((numerical, activations), dim=-1)
            density = model(features.to(device))
            values = base.numeric_decode(density, domain).cpu().tolist()
            for x, y in values:
                if not math.isfinite(x) or not math.isfinite(y):
                    raise RuntimeError('MINORITY_NONFINITE_MODEL_PREDICTION')
                preds.append((float(x), float(y)))
        if len(preds) != len(rows):
            raise RuntimeError('MINORITY_PREDICTION_COUNT_DRIFT')
        out[split] = preds
    return out


def collect_from_checkpoints(reference_checkpoint: Path, duo_root: Path,
                             device: str = 'cpu', batch_size: int = 128):
    """Recompute all three predictors from DEV-selected best weights, read-only."""
    reference_checkpoint = Path(reference_checkpoint).expanduser().resolve()
    duo_root = Path(duo_root).expanduser().resolve()
    device_obj = torch.device(device)
    if device == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('MINORITY_CUDA_UNAVAILABLE')
    state = _load_checkpoint(reference_checkpoint, ref.SCHEMA)
    ident = state['identity']
    data, domain, manifest, args = _dataset_verified(ident, device_obj, batch_size)
    domain = tuple(tuple(d) for d in domain)
    model = ref.BlindDensityNetwork(ident['banks'], ident['grid_bins'],
                                    ident['seed'], ident['hidden']).to(device_obj)
    model.load_state_dict(state['model'], strict=True)
    predictions = {'reference': _predict(model, data, domain, device_obj, batch_size)}
    provenance = {'reference': {'checkpoint': str(reference_checkpoint),
                                 'sha256': _sha256(reference_checkpoint), 'step': state['step'],
                                 'type': 'numeric-only, not Qwen'}}
    del model

    for backbone in ('tinystories', 'pythia'):
        run_path = duo_root / backbone
        checkpoint_path = run_path / 'best.pt'
        best = _load_checkpoint(checkpoint_path, duo.SCHEMA)
        cache, dim = _verify_duo(best, backbone, run_path, ident, data, manifest)
        model = duo.IndependentGaussianHead(dim, ident['banks'], ident['grid_bins'],
                                             ident['seed'], ident['hidden']).to(device_obj)
        model.load_state_dict(best['model'], strict=True)
        predictions[backbone] = _predict(model, data, domain, device_obj, batch_size, cache)
        provenance[backbone] = {'checkpoint': str(checkpoint_path),
                                'sha256': _sha256(checkpoint_path), 'step': best['step'],
                                'feature_cache_sha256': _sha256(run_path / 'feature_cache.pt'),
                                'source_model': best['identity']['model_id'],
                                'type': 'fresh independent head with explicit numeric inputs'}
        del model, cache
    # Every model was evaluated in the same ordered dataset, never joined loosely.
    return data, predictions, provenance, ident, manifest


def _hits(pred, truth, tolerance):
    return tuple(abs(float(p) - float(y)) <= tolerance * abs(float(y))
                 for p, y in zip(pred, truth))


def _z(pair):
    return 'HAWK' if pair[0] > pair[1] else 'DOVE' if pair[0] < pair[1] else 'TIE'


def _mean(pairs):
    return (sum(p[0] for p in pairs) / len(pairs),
            sum(p[1] for p in pairs) / len(pairs))


def _median(pairs):
    return tuple(sorted(p[j] for p in pairs)[len(pairs) // 2] for j in (0, 1))


def ensemble_pair(predictions, rule):
    if rule in MODELS:
        return predictions[rule]
    if rule == 'mean_three':
        return _mean([predictions[b] for b in MODELS])
    if rule == 'median_three':
        return _median([predictions[b] for b in MODELS])
    if rule.startswith('mean_'):
        for first, second in PAIR_NAMES:
            if rule == f'mean_{first}_{second}':
                return _mean([predictions[first], predictions[second]])
    raise ValueError('MINORITY_UNRECOGNIZED_ENSEMBLE_RULE:' + rule)


def _rate(numerator, denominator):
    return numerator / denominator if denominator else None


def _pearson(xs, ys):
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    ax = sum(xs) / len(xs); ay = sum(ys) / len(ys)
    x2 = sum((v - ax) ** 2 for v in xs)
    y2 = sum((v - ay) ** 2 for v in ys)
    if x2 == 0 or y2 == 0:
        return None
    return sum((a - ax) * (b - ay) for a, b in zip(xs, ys)) / math.sqrt(x2 * y2)


def _score(rows, predictions, tolerance):
    """Tier gate is all masks per instance; no denominator relaxation at zero."""
    tier_groups = defaultdict(lambda: defaultdict(list))
    totals = Counter()
    for row, pair in zip(rows, predictions):
        hx, hy = _hits(pair, (row['x'], row['y']), tolerance)
        totals['presentations'] += 1
        totals['joint'] += hx and hy
        totals['X'] += hx
        totals['Y'] += hy
        totals['Z'] += _z(pair) == _z((row['x'], row['y']))
        tier_groups[row['tier']][row['instance']].append(hx and hy)
    if len(rows) != len(predictions):
        raise RuntimeError('MINORITY_SCORE_ROW_COUNT_DRIFT')
    tier_rates = {t: _rate(sum(all(v) for v in tier_groups[t].values()), len(tier_groups[t]))
                  for t in TIERS}
    if any(rate is None for rate in tier_rates.values()):
        raise RuntimeError('MINORITY_MISSING_TIER')
    return {'presentations': totals['presentations'],
            'joint_presentation_rate': _rate(totals['joint'], totals['presentations']),
            'X_rate': _rate(totals['X'], totals['presentations']),
            'Y_rate': _rate(totals['Y'], totals['presentations']),
            'Z_rate': _rate(totals['Z'], totals['presentations']),
            'min_tier_joint_all_masks_rate': min(tier_rates.values()),
            'mean_tier_joint_all_masks_rate': sum(tier_rates.values()) / len(tier_rates),
            'by_tier_joint_all_masks_rate': tier_rates}


def _pattern(rows, predictions, tolerance, dimension='joint'):
    """Eight success/failure patterns: mask presentations and all-mask instances."""
    presentation = Counter(); inst = defaultdict(lambda: defaultdict(dict))
    by_tier = defaultdict(Counter)
    for i, row in enumerate(rows):
        truth = (row['x'], row['y'])
        hit = {}
        for b in MODELS:
            hx, hy = _hits(predictions[b][i], truth, tolerance)
            hit[b] = hx and hy if dimension == 'joint' else hx if dimension == 'X' else hy
            inst[(row['tier'], row['instance'])][b][row['mask']] = hit[b]
        bitstr = ''.join('1' if hit[b] else '0' for b in MODELS)
        presentation[bitstr] += 1
        by_tier[row['tier']][bitstr] += 1
    by_instance = Counter()
    per_tier_instance = defaultdict(Counter)
    for (tier, _), entries in inst.items():
        masks = {frozenset(m.keys()) for m in entries.values()}
        if len(masks) != 1:
            raise RuntimeError('MINORITY_INCOMPLETE_MODEL_MASK_ALIGNMENT')
        pattern = ''.join('1' if all(entries[b].values()) else '0' for b in MODELS)
        by_instance[pattern] += 1
        per_tier_instance[tier][pattern] += 1
    all_patterns = [f'{i:03b}' for i in range(8)]
    def serialize(counts):
        total = sum(counts.values())
        return {'n': total, 'counts': {k: counts[k] for k in all_patterns},
                'rates': {k: _rate(counts[k], total) for k in all_patterns},
                'at_least_one_success_rate': _rate(total - counts['000'], total),
                'unanimous_failure_rate': _rate(counts['000'], total),
                'unanimous_success_rate': _rate(counts['111'], total)}
    pair_oracles = {}
    for a, b in PAIR_NAMES:
        positions = (MODELS.index(a), MODELS.index(b))
        pair_oracles[f'{a}+{b}'] = _rate(
            sum(count for bits, count in by_instance.items()
                if any(bits[p] == '1' for p in positions)),
            sum(by_instance.values()))
    return {'model_order': list(MODELS),
            'interpretation': '1 = success; joint requires X and Y within tolerance; instance requires every mask',
            'presentations': serialize(presentation),
            'instances': serialize(by_instance),
            'per_tier_presentations': {t: serialize(by_tier[t]) for t in TIERS},
            'per_tier_instances': {t: serialize(per_tier_instance[t]) for t in TIERS},
            'pairwise_oracle_instance_coverage': pair_oracles}


def _error_symmetry(rows, predictions, tolerance):
    out = {}
    for model in MODELS:
        counts = Counter(); zero_counts = Counter(); sign = Counter()
        x_err = []; y_err = []
        for row, pair in zip(rows, predictions[model]):
            truth = (row['x'], row['y'])
            hx, hy = _hits(pair, truth, tolerance)
            label = ('both_hit' if hx and hy else 'X_only_hit' if hx else
                     'Y_only_hit' if hy else 'both_fail')
            counts[label] += 1
            for j, k in enumerate(('X', 'Y')):
                if truth[j] == 0:
                    zero_counts[k + '_zero_total'] += 1
                    zero_counts[k + '_zero_hit'] += pair[j] == 0
            dx, dy = pair[0] - truth[0], pair[1] - truth[1]
            x_err.append(dx); y_err.append(dy)
            if dx != 0 and dy != 0:
                sign['nonzero_XY_errors'] += 1
                sign['same_direction'] += (dx > 0) == (dy > 0)
        out[model] = {'presentation_counts': {k: counts[k] for k in
                          ('both_hit', 'X_only_hit', 'Y_only_hit', 'both_fail')},
                      'X_only_failure_rate': _rate(counts['Y_only_hit'], len(rows)),
                      'Y_only_failure_rate': _rate(counts['X_only_hit'], len(rows)),
                      'signed_XY_error_pearson': _pearson(x_err, y_err),
                      'XY_error_sign_agreement': _rate(sign['same_direction'], sign['nonzero_XY_errors']),
                      'zero_targets': dict(zero_counts)}
    pairwise = {}
    for a, b in PAIR_NAMES:
        record = {}
        for j, axis in enumerate(('X', 'Y')):
            ea = [predictions[a][i][j] - row['x' if j == 0 else 'y']
                  for i, row in enumerate(rows)]
            eb = [predictions[b][i][j] - row['x' if j == 0 else 'y']
                  for i, row in enumerate(rows)]
            nonzero = [(x, y) for x, y in zip(ea, eb) if x != 0 and y != 0]
            record[axis] = {'signed_error_pearson': _pearson(ea, eb),
                            'same_direction_rate': _rate(
                                sum((x > 0) == (y > 0) for x, y in nonzero), len(nonzero))}
        pairwise[f'{a}+{b}'] = record
    return {'per_model_XY_failure_asymmetry': out, 'pairwise_signed_error_correlation': pairwise}


def _z_vote(rows, source_predictions):
    agreed = correct = abstained = 0
    for i, row in enumerate(rows):
        votes = Counter(_z(source_predictions[m][i]) for m in MODELS)
        most_common = votes.most_common()
        if most_common[0][1] < 2:
            abstained += 1
            continue
        agreed += 1
        correct += most_common[0][0] == _z((row['x'], row['y']))
    return {'majority_coverage': _rate(agreed, len(rows)),
            'majority_accuracy_all_presentations_abstain_as_fail': _rate(correct, len(rows)),
            'majority_accuracy_when_decided': _rate(correct, agreed),
            'three_way_abstentions': abstained}




def _magnitude_bucket(value):
    if value == 0:
        return 'exact_zero'
    a = abs(value)
    for threshold in (1, 10, 100, 1000, 10000, 100000):
        if a <= threshold:
            return f'abs_le_{threshold}'
    return 'abs_gt_100000'


def _difficulty_and_mask_symmetry(rows, predictions, tolerance):
    """Error behavior across target scales and equivalent prompt masks."""
    result = {}
    for model in MODELS:
        buckets = defaultdict(Counter)
        masks = defaultdict(dict)
        for i, row in enumerate(rows):
            pair = predictions[model][i]
            truth = (row['x'], row['y'])
            hx, hy = _hits(pair, truth, tolerance)
            for j, axis in enumerate(('X', 'Y')):
                name = axis + ':' + _magnitude_bucket(truth[j])
                buckets[name]['total'] += 1
                buckets[name]['hits'] += (hx if j == 0 else hy)
            masks[(row['tier'], row['instance'])][row['mask']] = (pair, hx and hy)
        drift = Counter()
        for variants in masks.values():
            if len(variants) == 1:
                continue
            if len(variants) != 2 or set(variants) != {0, 1}:
                raise RuntimeError('MINORITY_MASK_PAIR_MISSING_OR_DUPLICATED')
            drift['paired_instances'] += 1
            (a, success_a), (b, success_b) = variants[0], variants[1]
            drift['different_joint_pass_status'] += success_a != success_b
            drift['mean_abs_X_mask_difference_sum'] += abs(a[0] - b[0])
            drift['mean_abs_Y_mask_difference_sum'] += abs(a[1] - b[1])
        result[model] = {
            'accuracy_by_target_magnitude': {
                key: {'n': v['total'], 'hits': v['hits'],
                      'rate': _rate(v['hits'], v['total'])}
                for key, v in sorted(buckets.items())},
            'mask_equivalence': {
                'paired_instances': drift['paired_instances'],
                'joint_pass_disagreement_rate': _rate(
                    drift['different_joint_pass_status'], drift['paired_instances']),
                'mean_abs_X_prediction_drift': _rate(
                    drift['mean_abs_X_mask_difference_sum'], drift['paired_instances']),
                'mean_abs_Y_prediction_drift': _rate(
                    drift['mean_abs_Y_mask_difference_sum'], drift['paired_instances'])}}
    return result


def _case_examples(rows, predictions, tolerance, per_pattern=8):
    """Concrete instance-level evidence, with all masks and predictor outputs."""
    by_instance = defaultdict(list)
    for index, row in enumerate(rows):
        by_instance[(row['tier'], row['instance'])].append(index)
    samples = {f'{i:03b}': [] for i in range(8)}
    for (tier, instance), indices in sorted(by_instance.items()):
        hit = {}
        for model in MODELS:
            hit[model] = all(all(_hits(predictions[model][i],
                                       (rows[i]['x'], rows[i]['y']), tolerance))
                             for i in indices)
        pattern = ''.join('1' if hit[b] else '0' for b in MODELS)
        if len(samples[pattern]) >= per_pattern:
            continue
        row = rows[indices[0]]
        samples[pattern].append({
            'instance': instance, 'tier': tier, 'matrix_index': row['matrix_index'],
            'true_X': row['x'], 'true_Y': row['y'],
            'masks': [{
                'mask': rows[i]['mask'],
                'predictions': {b: {'X': predictions[b][i][0],
                                     'Y': predictions[b][i][1],
                                     'Z': _z(predictions[b][i]),
                                     'joint_hit': all(_hits(predictions[b][i],
                                              (rows[i]['x'], rows[i]['y']), tolerance))}
                                for b in MODELS}}
                      for i in sorted(indices, key=lambda i: rows[i]['mask'])]})
    return samples


def analyze_split(data_split, model_predictions, tolerance):
    rows = data_split['rows']
    if set(model_predictions) != set(MODELS):
        raise RuntimeError('MINORITY_MODEL_MISSING')
    if any(len(v) != len(rows) for v in model_predictions.values()):
        raise RuntimeError('MINORITY_MODEL_PRESENTATION_COUNT_MISMATCH')
    candidates = {rule: [ensemble_pair({b: model_predictions[b][i] for b in MODELS}, rule)
                         for i in range(len(rows))] for rule in RULES}
    return {
        'candidates': {name: _score(rows, values, tolerance) for name, values in candidates.items()},
        'joint_failure_patterns': _pattern(rows, model_predictions, tolerance),
        'X_failure_patterns': _pattern(rows, model_predictions, tolerance, 'X'),
        'Y_failure_patterns': _pattern(rows, model_predictions, tolerance, 'Y'),
        'error_symmetry': _error_symmetry(rows, model_predictions, tolerance),
        'majority_Z': _z_vote(rows, model_predictions),
        'target_magnitude_and_mask_symmetry': _difficulty_and_mask_symmetry(rows, model_predictions, tolerance),
        'example_instances_by_joint_pattern': _case_examples(rows, model_predictions, tolerance),
    }


def select_on_dev(dev):
    # Rank by the official all-masks, minimum-tier X/Y gate, then tier mean.
    # Fixed RULES order supplies deterministic tie-break; TEST never selects.
    return max(RULES, key=lambda name: (
        dev['candidates'][name]['min_tier_joint_all_masks_rate'],
        dev['candidates'][name]['mean_tier_joint_all_masks_rate'],
        -RULES.index(name)))


def build_report(data, predictions, provenance, identity, manifest, tolerance=.05):
    if not (0 < tolerance < 1) or not math.isfinite(tolerance):
        raise ValueError('MINORITY_INVALID_TOLERANCE')
    dev = analyze_split(data['dev'], {b: predictions[b]['dev'] for b in MODELS}, tolerance)
    winner = select_on_dev(dev)
    test = analyze_split(data['test'], {b: predictions[b]['test'] for b in MODELS}, tolerance)
    selected_dev = dev['candidates'][winner]
    selected_test = test['candidates'][winner]
    return {'schema': SCHEMA, 'read_only_sources': True,
            'model_order': list(MODELS),
            'reference_is_qwen': False,
            'independent_head_provenance': provenance,
            'identity': {'seed': identity['seed'], 'banks': identity['banks'],
                         'grid_bins': identity['grid_bins'], 'masks': identity['masks'],
                         'tolerance': tolerance, 'matrix_hashes': manifest['matrix_hashes']},
            'selection_policy': {'split': 'DEV', 'criterion': 'highest weakest-tier joint X/Y all-mask instance accuracy, then mean-tier accuracy; predetermined rule order breaks ties',
                                 'candidate_rules': list(RULES), 'selected_rule': winner,
                                 'test_used_for_selection': False,
                                 'no_post_hoc_test_winner': True},
            'selected_consensus': {'rule': winner, 'DEV': selected_dev, 'TEST': selected_test,
                                   'test_gain_vs_reference': selected_test['min_tier_joint_all_masks_rate'] - test['candidates']['reference']['min_tier_joint_all_masks_rate'],
                                   'test_gain_vs_pythia': selected_test['min_tier_joint_all_masks_rate'] - test['candidates']['pythia']['min_tier_joint_all_masks_rate']},
            'DEV': dev, 'TEST': test}


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reference-checkpoint', type=Path, required=True)
    p.add_argument('--duo-root', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--device', choices=('cpu', 'cuda'), default='cpu')
    p.add_argument('--eval-batch-size', type=int, default=128)
    p.add_argument('--cpu-threads', type=int, default=4)
    p.add_argument('--tolerance', type=float, default=.05)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    if args.eval_batch_size < 1 or args.cpu_threads < 1:
        raise ValueError('MINORITY_INVALID_BATCH_OR_THREADS')
    if args.device == 'cpu':
        torch.set_num_threads(args.cpu_threads)
    output = args.output.expanduser().resolve()
    if output.exists():
        raise FileExistsError('MINORITY_OUTPUT_ALREADY_EXISTS_NO_OVERWRITE:' + str(output))
    # Do not write anything before every input checkpoint is validated and all
    # model predictions are recomputed, so a failed identity check leaves no report.
    data, predictions, provenance, identity, manifest = collect_from_checkpoints(
        args.reference_checkpoint, args.duo_root, args.device, args.eval_batch_size)
    report = build_report(data, predictions, provenance, identity, manifest, args.tolerance)
    output.parent.mkdir(parents=True, exist_ok=True)
    base._atomic_json(output, report)
    print(json.dumps({'report': str(output),
                      'selected_rule': report['selection_policy']['selected_rule'],
                      'DEV_selected_min_joint5': report['selected_consensus']['DEV']['min_tier_joint_all_masks_rate'],
                      'TEST_selected_min_joint5': report['selected_consensus']['TEST']['min_tier_joint_all_masks_rate'],
                      'TEST_oracle_any_head_coverage': report['TEST']['joint_failure_patterns']['instances']['at_least_one_success_rate'],
                      'TEST_reference': report['TEST']['candidates']['reference']['min_tier_joint_all_masks_rate'],
                      'TEST_pythia': report['TEST']['candidates']['pythia']['min_tier_joint_all_masks_rate'],
                      'TEST_tinystories': report['TEST']['candidates']['tinystories']['min_tier_joint_all_masks_rate']}, indent=2), flush=True)
    return report


if __name__ == '__main__':
    main()
