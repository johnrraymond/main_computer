"""Ordered base-10 exponent and combined log-magnitude supervision.

Adds differentiable losses to the existing V3 sign/exponent-class/mantissa
objective. No new model parameters; existing checkpoint and optimizer load as-is.
"""
from __future__ import annotations

import math

DEFAULT_EXPONENT_WEIGHT = 0.25
DEFAULT_MAGNITUDE_WEIGHT = 0.50


def supervised_parts(out, true_xy, exponent_weight=DEFAULT_EXPONENT_WEIGHT,
                     magnitude_weight=DEFAULT_MAGNITUDE_WEIGHT):
    import torch
    import torch.nn.functional as F

    if not (math.isfinite(exponent_weight) and exponent_weight >= 0 and
            math.isfinite(magnitude_weight) and magnitude_weight >= 0):
        raise ValueError('INVALID_ORDERED_EXPONENT_WEIGHTS')
    logits = out['exponent']
    fraction = out['fraction']
    if logits.ndim != 3 or logits.shape[1] != 2 or fraction.shape != logits.shape[:2] or true_xy.shape != fraction.shape:
        raise ValueError('INVALID_ORDERED_EXPONENT_SHAPES')
    if not bool(torch.isfinite(logits).all() and torch.isfinite(fraction).all() and torch.isfinite(true_xy).all()):
        raise ValueError('NONFINITE_ORDERED_EXPONENT_INPUT')

    gold_log = torch.log10(true_xy.float().abs().clamp_min(1.))
    gold_exponent = torch.floor(gold_log).clamp(0, logits.shape[-1] - 1)
    nonzero = true_xy.abs() >= 1.
    indices = torch.arange(logits.shape[-1], device=logits.device, dtype=logits.dtype)
    expected_exponent = (torch.softmax(logits.float(), dim=-1) * indices.float()).sum(-1)
    expected_log_magnitude = expected_exponent + fraction.float()
    x_terms = []
    y_terms = []
    details = {}
    for slot, label, container in ((0, 'x', x_terms), (1, 'y', y_terms)):
        valid = nonzero[:, slot]
        if bool(valid.any()):
            eloss = F.smooth_l1_loss(expected_exponent[valid, slot], gold_exponent[valid, slot], beta=0.5)
            mloss = F.smooth_l1_loss(expected_log_magnitude[valid, slot], gold_log[valid, slot], beta=0.5)
            container.append((eloss, mloss))
            details[label + '_exponent_distance'] = float(eloss.detach())
            details[label + '_log_magnitude'] = float(mloss.detach())
        else:
            details[label + '_exponent_distance'] = 0.0
            details[label + '_log_magnitude'] = 0.0
    # Sum per-coordinate means / 2 gives each coordinate equal opportunity,
    # without including zero targets in the logarithmic losses.
    pairs = x_terms + y_terms
    if pairs:
        ex_loss = sum(pair[0] for pair in pairs) / len(pairs)
        mag_loss = sum(pair[1] for pair in pairs) / len(pairs)
    else:
        ex_loss = logits.sum() * 0.
        mag_loss = fraction.sum() * 0.
    addition = exponent_weight * ex_loss + magnitude_weight * mag_loss
    details['weighted_exponent_distance'] = float((exponent_weight * ex_loss).detach())
    details['weighted_log_magnitude'] = float((magnitude_weight * mag_loss).detach())
    return addition, details


def enhanced_objective(original, out, true_xy, base, arm, *, exponent_weight=DEFAULT_EXPONENT_WEIGHT,
                       magnitude_weight=DEFAULT_MAGNITUDE_WEIGHT, **kwargs):
    old_loss, old_parts = original(out, true_xy, base, arm, **kwargs)
    if arm == 'residual':
        raise ValueError('ORDERED_EXPONENT_REQUIRES_FACTORIZED_OR_ATTENTION')
    extra, parts = supervised_parts(out, true_xy, exponent_weight, magnitude_weight)
    return old_loss + extra, {**old_parts, **parts}
