#!/usr/bin/env python3
"""Read-only diagnosis of a saved HybridLink curriculum FORMAT checkpoint.

Reports the strict X/Y metric alongside algebraic sum-of-products controls,
zero/near-zero effects, independent teacher-token interventions, and relative
objective gradients. Never trains, writes model weights, or evaluates TEST.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import torch
from torch.nn import functional as F

from tools import nanojev_hawk_dove_hybridlink_curriculum as curriculum
from tools import nanojev_hawk_dove_hybridlink_sequence as base


SCHEMA = 'nanojev.hawk-dove.hybridlink-format-diagnostic.v1'


def _as_native(metric):
    return {k: (float(v) if isinstance(v, float) else v) for k, v in metric.items()}


def _xy_metrics(pred, split, tolerance):
    return _as_native(base.lab.strict_metrics(pred, split, tolerance))


def _xy_strata(pred, split, scale, tolerance, near_fraction=.01):
    """Each tier/instance counted once, only when ALL masks are hits."""
    truth = split['numeric'].detach().cpu()
    pred = pred.detach().cpu()
    strict = ((pred - truth).abs() <= tolerance * truth.abs()).all(-1)
    zero = (truth == 0).any(-1)
    near = (truth.abs() <= near_fraction * scale).any(-1)
    records = {}
    for i, row in enumerate(split['rows']):
        key = (row['tier'], row['instance'])
        item = records.setdefault(key, {'hits': [], 'zero': False, 'near': False})
        item['hits'].append(bool(strict[i]))
        item['zero'] |= bool(zero[i]); item['near'] |= bool(near[i])
    counts = {'all': [0, 0], 'exact_zero': [0, 0],
              'near_zero_nonzero': [0, 0], 'away_from_zero': [0, 0]}
    tier_stats = {}
    for (tier, _), rec in records.items():
        hit = all(rec['hits'])
        cat = ('exact_zero' if rec['zero'] else
               'near_zero_nonzero' if rec['near'] else 'away_from_zero')
        for bucket in ('all', cat):
            counts[bucket][0] += 1; counts[bucket][1] += int(hit)
        st = tier_stats.setdefault(tier, {'instances': 0, 'exact_zero_instances': 0,
                                          'near_zero_instances': 0, 'passes': 0})
        st['instances'] += 1
        st['exact_zero_instances'] += int(rec['zero'])
        st['near_zero_instances'] += int(rec['near'])
        st['passes'] += int(hit)
    return {
        'by_stratum': {k: {'instances': n, 'passed': hits,
                           'joint_all_masks_rate': hits / n if n else None}
                       for k, (n, hits) in counts.items()},
        'per_tier': {k: {**d, 'joint_all_masks_rate': d['passes'] / d['instances']}
                     for k, d in sorted(tier_stats.items())},
        'exact_zero_coordinates': int((truth == 0).sum()),
        'nonzero_coordinates_within_one_percent_scale': int(
            ((truth != 0) & (truth.abs() <= near_fraction * scale)).sum()),
        'near_fraction_of_scale': near_fraction,
        'strict_zero_policy': 'zero target requires exact floating-point zero prediction',
    }


def _grad_norms(model, features, truth, scale):
    """Task-specific gradient diagnostics; no backward accumulation or update."""
    model.eval()
    answer, next_pred, consumed = model.teacher_predictions(features)
    products = base.v4.stage_values(features)
    losses = {
        'answer': base.numeric_loss(answer, truth / scale),
        'next_product_weighted': 2.0 * F.mse_loss(next_pred, products),
        'consumed_product_weighted': 0.3 * F.mse_loss(consumed, products),
    }
    groups = {
        'backbone': [(k, p) for k, p in model.backbone.named_parameters() if p.requires_grad],
        'seed_numeric': [(k, p) for k, p in model.seed_numeric.named_parameters() if p.requires_grad],
        'answer_head': [(k, p) for k, p in model.answer.named_parameters() if p.requires_grad],
    }
    result = {}
    for name, loss in losses.items():
        group_grads = {}
        for g, items in groups.items():
            grads = torch.autograd.grad(loss, [p for _, p in items], retain_graph=True,
                                        allow_unused=True)
            squares = sum(float(grad.detach().square().sum()) for grad in grads if grad is not None)
            group_grads[g] = squares ** .5
        result[name] = {'weighted_loss': float(loss.detach()), 'gradient_l2': group_grads}
    return result


@torch.no_grad()
def _evaluate(model, dev, scale, batch_size, tolerance):
    model.eval()
    direct, next_products, consumed_products, exact_products, seed_states, teachers = [], [], [], [], [], []
    for x in dev['features'].split(batch_size):
        teacher, states = model.teacher_state(x)
        direct.append(model.answer(states[:, -1]).cpu())
        next_products.append(model.next_product(states[:, 1:5]).squeeze(-1).cpu())
        consumed_products.append(model.value_probe(states[:, 2:]).squeeze(-1).cpu())
        exact_products.append(base.v4.stage_values(x).cpu())
        seed_states.append(model.seed(x).cpu()); teachers.append(teacher.cpu())
    a = torch.cat(direct); nxt = torch.cat(next_products)
    consumed = torch.cat(consumed_products); actual = torch.cat(exact_products)
    tokens = torch.cat(teachers)
    # Keep original device for donor selection / backbone on GPU; do not use
    # target X/Y to construct interventions.
    donors = base.v4.distinct_instance_thought_donors(dev['features'],
                                                     tokens.to(dev['features'].device),
                                                     dev['rows']).cpu()
    interventions = {'zero_tokens': [], 'distinct_instance_shuffled_tokens': []}
    cursor = 0
    for x in dev['features'].split(batch_size):
        count = len(x)
        original = tokens[cursor:cursor + count].to(x.device)
        shuffled = tokens.index_select(0, donors[cursor:cursor + count]).to(x.device)
        s = model.seed(x)
        for name, supplied in [('zero_tokens', torch.zeros_like(original)),
                               ('distinct_instance_shuffled_tokens', shuffled)]:
            state = model.backbone(torch.cat((s, supplied), dim=1))
            interventions[name].append(model.answer(state[:, -1]).cpu())
        cursor += count
    xy_predictions = {
        'direct_head': a * scale,
        'consumed_product_sum': base.lab.product_xy(consumed, scale),
        'next_product_sum': base.lab.product_xy(nxt, scale),
        'exact_visible_product_sum': base.lab.product_xy(actual, scale),
        'zero_teacher': torch.cat(interventions['zero_tokens']) * scale,
        'shuffled_teacher': torch.cat(interventions['distinct_instance_shuffled_tokens']) * scale,
    }
    return {
        'xy': {k: _xy_metrics(v, dev, tolerance) for k, v in xy_predictions.items()},
        'strict_accuracy_breakdown': _xy_strata(xy_predictions['direct_head'], dev,
                                               scale, tolerance),
        'product_error': {
            'next': curriculum._product_stats(nxt, actual),
            'consumed': curriculum._product_stats(consumed, actual),
        },
        'teacher_interventions': {
            k: {'mean_abs_change_from_direct_xy': float(
                    (xy_predictions[k] - xy_predictions['direct_head']).abs().mean()),
                'note': 'Output shift does not by itself prove causal use of product values'}
            for k in ('zero_teacher', 'shuffled_teacher')
        },
        'oracle_visible_sum': {
            'max_abs_error': float((xy_predictions['exact_visible_product_sum'] -
                                    dev['numeric'].cpu()).abs().max()),
            'note': 'Algebraic sanity control, not a learned-model score',
        },
    }


def run(args):
    if args.device == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('FORMAT_DIAG_CUDA_UNAVAILABLE')
    source = args.reference_checkpoint.expanduser().resolve()
    root = args.output.expanduser().resolve()
    if not (root / 'config.json').is_file():
        raise FileNotFoundError('FORMAT_DIAG_CONFIG_MISSING')
    identity = json.loads((root / 'config.json').read_text(encoding='utf8'))
    if identity.get('schema') != curriculum.SCHEMA:
        raise RuntimeError('FORMAT_DIAG_SCHEMA_DRIFT')
    sha = base.file_sha256(source)
    if sha != identity.get('source_sha256'):
        raise RuntimeError('FORMAT_DIAG_REFERENCE_DRIFT')
    reference, splits, _, config = base.load_source(source, args.device)
    options = SimpleNamespace(**identity['options'])
    if curriculum.make_identity(options, source, config) != identity:
        raise RuntimeError('FORMAT_DIAG_EXPERIMENT_IDENTITY_DRIFT')
    if args.arm not in curriculum.ARMS:
        raise ValueError('FORMAT_DIAG_UNKNOWN_ARM')
    model = curriculum.CurriculumModel(reference, options.heads, options.layers).to(args.device)
    ck = root / 'arms' / args.arm / 'format' / 'latest.pt'
    resume = curriculum._load_checkpoint(ck, identity, args.arm, 'format', model)
    checkpoint_sha = base.file_sha256(ck)
    scale = base.xy_scale(config['source_identity']['payoff_bound'])
    eval_result = _evaluate(model, splits['dev'], scale, args.eval_batch_size, options.tolerance)
    n = min(args.gradient_samples, len(splits['train']['rows']))
    if n < 1: raise ValueError('FORMAT_DIAG_NO_TRAIN_SAMPLES')
    gradient = _grad_norms(model, splits['train']['features'][:n],
                           splits['train']['numeric'][:n], scale)
    result = {'schema': SCHEMA, 'phase': 'format', 'arm': args.arm,
              'trained_step': int(resume['step']), 'source_sha256': sha,
              'checkpoint_sha256': checkpoint_sha, 'dataset_policy': 'DEV only; TEST not evaluated',
              'evaluation': eval_result, 'loss_gradients_on_train': gradient,
              'interpretation_note': ('Assess strict zero/near-zero distribution, '
                                      'algebraic product-sum readout, and teacher-token interventions '
                                      'separately before redesigning the backbone.')}
    if base.file_sha256(source) != sha or base.file_sha256(ck) != checkpoint_sha:
        raise RuntimeError('FORMAT_DIAG_INPUT_MUTATION_DETECTED')
    if args.report:
        report = args.report.expanduser().resolve()
        if report in (source, ck):raise ValueError('FORMAT_DIAG_REFUSE_OVERWRITE_INPUT')
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf8')
    print('CURRICULUM_FORMAT_DIAGNOSTIC=' + json.dumps({
        'step': result['trained_step'],
        'teacher_head': eval_result['xy']['direct_head']['min_tier_joint5'],
        'sum_consumed': eval_result['xy']['consumed_product_sum']['min_tier_joint5'],
        'sum_next': eval_result['xy']['next_product_sum']['min_tier_joint5'],
        'oracle_sum': eval_result['xy']['exact_visible_product_sum']['min_tier_joint5'],
        'zero_instances': eval_result['strict_accuracy_breakdown']['by_stratum']['exact_zero']['instances'],
        'away_from_zero': eval_result['strict_accuracy_breakdown']['by_stratum']['away_from_zero'],
        'shuffled_head': eval_result['xy']['shuffled_teacher']['min_tier_joint5'],
        'gradient_l2': gradient,
        'report': str(args.report) if args.report else None}, sort_keys=True), flush=True)
    print('CURRICULUM_SOURCE_IMMUTABLE=TRUE TEST_UNTOUCHED=TRUE', flush=True)
    return result


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reference-checkpoint', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--device', choices=('cpu', 'cuda'), default='cuda')
    p.add_argument('--arm', choices=curriculum.ARMS, default='recurrent')
    p.add_argument('--eval-batch-size', type=int, default=256)
    p.add_argument('--gradient-samples', type=int, default=128)
    p.add_argument('--report', type=Path, default=None)
    return p


def main(argv=None):
    return run(parser().parse_args(argv))


if __name__ == '__main__':
    main()
