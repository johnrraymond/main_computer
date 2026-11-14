#!/usr/bin/env python3
"""TRAIN-only operator induction, exact arithmetic, DEV-only numerical control.

This is NOT a HybridLink model repair or evidence that recurrent states perform
multiplication. It tests whether a small symbolic bilinear operator learned from
visible numbers removes the near-zero precision bottleneck. No final X/Y or
teacher products are supplied to inference. Original gates never change.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import torch

from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as blind
from tools import nanojev_hawk_dove_gaussian_population_conditional as gauss
from tools import nanojev_hawk_dove_hybridlink_sequence as base
from tools import nanojev_hawk_dove_hybridlink_format_diagnostic as diag

SCHEMA = 'nanojev.hawk-dove.hybridlink-operator-control.v1'


def design(features):
    """All eight count/payoff cross terms, with no preselected winning terms.

    The terms themselves are known-format operators; the X/Y wiring is learned
    ONLY from TRAIN data, not supplied with fixed target-selection coefficients.
    """
    x = features.detach().to('cpu', dtype=torch.float64)
    if x.ndim != 2 or x.shape[1] != blind.INPUT_DIM:
        raise ValueError('OPERATOR_BAD_FEATURE_SHAPE')
    # Order: a*hh, a*hd, a*dh, a*dd, b*hh, b*hd, b*dh, b*dd.
    return (x[:, :2, None] * x[:, None, 2:6]).reshape(-1, 8)


def learn_operator(train, scale):
    a = design(train['features'])
    b = train['numeric'].detach().to('cpu', dtype=torch.float64) / float(scale)
    rank = int(torch.linalg.matrix_rank(a))
    if rank != 8:
        raise RuntimeError(f'OPERATOR_TRAIN_DESIGN_RANK_{rank}_NOT_8')
    weights = torch.linalg.lstsq(a, b).solution
    if weights.shape != (8, 2) or not bool(torch.isfinite(weights).all()):
        raise RuntimeError('OPERATOR_FIT_INVALID')
    return weights


def quantize_weights(weights, tolerance):
    """TRAIN-derived hypothesis constrained to a discrete symbolic vocabulary."""
    rounded = weights.round()
    distance = float((weights - rounded).abs().max())
    if distance > tolerance or float(rounded.abs().max()) > 2.:
        raise RuntimeError(f'OPERATOR_DISCRETE_HYPOTHESIS_UNSUPPORTED drift={distance}')
    return rounded.to(dtype=torch.int64), distance


def visible_integers(features, payoff_bound):
    """Reconstruct visible count/payoff integers, not target X or Y."""
    f = features.detach().to('cpu', dtype=torch.float64)
    a = (f[:, 0] * blind.TOTAL).round().to(torch.int64)
    b = (f[:, 1] * blind.TOTAL).round().to(torch.int64)
    payoffs = (f[:, 2:6] * int(payoff_bound)).round().to(torch.int64)
    if bool(((a+b) != blind.TOTAL).any()) or bool((a < 0).any()) or bool((b < 0).any()):
        raise RuntimeError('OPERATOR_VISIBLE_COUNT_RECONSTRUCTION_FAILED')
    if bool((payoffs.abs() > int(payoff_bound)).any()):
        raise RuntimeError('OPERATOR_PAYOFF_RECONSTRUCTION_FAILED')
    # Validate that the floats accurately encode the claimed visible integers;
    # no oracle labels are examined by this transformation.
    eps = 1.e-6
    if bool(((f[:, 0]-a/blind.TOTAL).abs() > eps).any()) or bool(((f[:, 1]-b/blind.TOTAL).abs() > eps).any()):
        raise RuntimeError('OPERATOR_COUNT_REPRESENTATION_DRIFT')
    if bool(((f[:, 2:6]-payoffs/int(payoff_bound)).abs() > eps).any()):
        raise RuntimeError('OPERATOR_PAYOFF_REPRESENTATION_DRIFT')
    products = torch.stack((a[:, None]*payoffs, b[:, None]*payoffs), dim=1).reshape(-1, 8)
    return products


def predict_symbolic(features, integer_weights, payoff_bound):
    if integer_weights.shape != (8, 2) or integer_weights.dtype != torch.int64:
        raise ValueError('OPERATOR_BAD_PROGRAM')
    p = visible_integers(features, payoff_bound)
    # Integral arithmetic makes cancellation and exact zero part of the
    # represented operation, instead of hoping a floating readout equals zero.
    return (p @ integer_weights).to(dtype=torch.float64)


def metrics(pred, split, scale, tolerance, near_fraction):
    return {
        'strict': base.lab.strict_metrics(pred, split, tolerance),
        'strata': diag._xy_strata(pred, split, scale, tolerance, near_fraction)['by_stratum']
    }


def intervention_test(split, integer_weights, payoff_bound, tolerance):
    features = split['features'].detach().clone().to('cpu')
    n = features.shape[0]
    if n < 2:
        raise ValueError('OPERATOR_NO_INTERVENTION_CASES')
    # Rotate only the visible payoff matrix between independent instances. The
    # effect is compared with predictions on original inputs, NEVER with oracle
    # labels for changed inputs (which were not provided by the dataset).
    transformed = features.clone()
    transformed[:, 2:6] = features.roll(n//2, dims=0)[:, 2:6]
    actual = predict_symbolic(features, integer_weights, payoff_bound)
    shifted = predict_symbolic(transformed, integer_weights, payoff_bound)
    changed = ((shifted-actual).abs().max(dim=1).values > tolerance)
    return {'changed_fraction': float(changed.to(torch.float64).mean()),
            'changed_rows': int(changed.sum()), 'total_rows': n,
            'note':'Only a causal input perturbation; not scored against stale X/Y labels'}


def run(args):
    if not (0 < args.tolerance < 1 and 0 < args.near_fraction < 1 and
            0 < args.quantization_tolerance <= .5 and 0 < args.min_teacher <= 1):
        raise ValueError('OPERATOR_BAD_OPTIONS')
    source = args.reference_checkpoint.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError('OPERATOR_REFERENCE_MISSING')
    if (output == source or output == source.parent or source.parent in output.parents):
        raise ValueError('OPERATOR_OUTPUT_INSIDE_REFERENCE_DIRECTORY')
    if output.exists() and any(output.iterdir()):
        raise FileExistsError('OPERATOR_OUTPUT_OVERWRITE_REFUSED')
    source_sha = base.file_sha256(source)
    checkpoint = torch.load(source, map_location='cpu', weights_only=True)
    if checkpoint.get('schema') != blind.SCHEMA or not isinstance(checkpoint.get('identity'),dict):
        raise RuntimeError('OPERATOR_REFERENCE_SCHEMA_DRIFT')
    ci = checkpoint['identity']
    splits, domain, manifest = blind._dataset(SimpleNamespace(**ci), torch.device('cpu'))
    if manifest['matrix_hashes'] != ci['matrix_hashes'] or [list(d) for d in domain] != ci['domain']:
        raise RuntimeError('OPERATOR_DATASET_IDENTITY_DRIFT')
    scale = base.xy_scale(ci['payoff_bound'])
    weights = learn_operator(splits['train'], scale)
    integer_weights, distance = quantize_weights(weights, args.quantization_tolerance)
    # TRAIN validation proves that integer execution is not being selected
    # by the unseen DEV answers. TEST is not touched by the metrics functions.
    train_predictions = predict_symbolic(splits['train']['features'], integer_weights, ci['payoff_bound'])
    train_truth = splits['train']['numeric'].detach().to('cpu', dtype=torch.float64)
    train_error = float((train_predictions-train_truth).abs().max())
    if train_error > 0.0:
        raise RuntimeError(f'OPERATOR_TRAIN_SYMBOLIC_RECONSTRUCTION_FAILED err={train_error}')
    dev = splits['dev']
    raw_predictions = design(dev['features']) @ weights * scale
    symbolic_predictions = predict_symbolic(dev['features'], integer_weights, ci['payoff_bound'])
    raw = metrics(raw_predictions, dev, scale, args.tolerance, args.near_fraction)
    symbolic = metrics(symbolic_predictions, dev, scale, args.tolerance, args.near_fraction)
    status = 'PASS_CONTROL_ONLY' if symbolic['strict']['min_tier_joint5'] >= args.min_teacher else 'FAIL'
    counterfactual = intervention_test(dev, integer_weights, ci['payoff_bound'], args.intervention_delta)
    result = {'schema': SCHEMA, 'status': status, 'reference_sha256': source_sha,
              'matrix_hashes': manifest['matrix_hashes'], 'train_design_rank': 8,
              'trained_on': 'TRAIN X/Y supervised operator routing only',
              'parameter_matrix_float64': weights.tolist(),
              'learned_program_integer': integer_weights.tolist(),
              'quantization_max_distance': distance, 'train_symbolic_max_abs_error': train_error,
              'dev_float64_linear': raw, 'dev_symbolic_integer': symbolic,
              'payoff_input_intervention': counterfactual,
              'certification_policy': ('CONTROL ONLY: explicit engineered bilinear input terms and integer evaluation; '
                      'not HybridLink latent computation, not autonomous rollout, no original gate promotion'),
              'data_policy': 'TRAIN for learning; DEV only for evaluation; TEST not evaluated'}
    if base.file_sha256(source) != source_sha:
        raise RuntimeError('OPERATOR_SOURCE_MUTATED')
    output.mkdir(parents=True, exist_ok=True)
    gauss._atomic_json(output/'result.json', result)
    print('HYBRIDLINK_OPERATOR_CONTROL='+json.dumps({'status':status,
          'float64':raw['strict']['min_tier_joint5'],
          'symbolic':symbolic['strict']['min_tier_joint5'],
          'strata':symbolic['strata'], 'train_max_error':train_error,
          'quantization_max_distance':distance, 'report':str(output/'result.json')},sort_keys=True),flush=True)
    print('OPERATOR_SOURCE_IMMUTABLE=TRUE TEST_UNTOUCHED=TRUE '
          'HYBRIDLINK_UNCHANGED=TRUE ORIGINAL_GATES_UNCHANGED=TRUE',flush=True)
    return result


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reference-checkpoint',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--tolerance',type=float,default=.05)
    p.add_argument('--near-fraction',type=float,default=.01)
    p.add_argument('--quantization-tolerance',type=float,default=.05)
    p.add_argument('--min-teacher',type=float,default=.8)
    p.add_argument('--intervention-delta',type=float,default=1.)
    return p


def main(argv=None):
    return run(parser().parse_args(argv))

if __name__=='__main__':
    main()
