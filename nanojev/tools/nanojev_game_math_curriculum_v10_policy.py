#!/usr/bin/env python3
"""Adjustable advancement gate and independent accuracy diagnostics for V10.1.

The 5% numerical tolerance, paired targets, and two-probe promotion logic
belong to the existing V10 runtime and trainer and are deliberately unchanged.
"""
from __future__ import annotations

import argparse
from tools import nanojev_game_math_curriculum_v10_runtime as rt

DIAGNOSTIC_PERCENTAGES = (70, 80, 90, 95)


def add_gate_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--advancement-percent', '--advancement-precent',
                        dest='advancement_percent', type=float, default=None,
                        metavar='PERCENT',
                        help='X, Y and joint X/Y advancement floor in percent; '
                             'e.g. 70, 75, or 65 (default 80). '
                             'Both spellings accepted for compatibility.')
    parser.add_argument('--gate-fraction', type=float, default=None,
                        help='Existing decimal gate option (e.g. 0.70); '
                             'if also supplied, must match --advancement-percent.')


def resolve_gate(args: argparse.Namespace) -> float:
    """Normalize either public percentage spelling or old decimal gate.

    Set the old `gate_fraction` attribute so existing V10 call sites and
    checkpoint config preserve their stable schema and JSON field names.
    """
    percentage = getattr(args, 'advancement_percent', None)
    fraction = getattr(args, 'gate_fraction', None)
    if percentage is not None:
        if not 0 < percentage <= 100:
            raise ValueError('V10_ADVANCEMENT_PERCENT_OUT_OF_RANGE')
        normalized = round(percentage / 100.0, 12)
        if fraction is not None and abs(fraction - normalized) > 1e-10:
            raise ValueError('V10_CONFLICTING_ADVANCEMENT_GATES')
        fraction = normalized
    if fraction is None:
        fraction = 0.80
    if not 0 < fraction <= 1:
        raise ValueError('V10_GATE_FRACTION_OUT_OF_RANGE')
    args.gate_fraction = fraction
    return fraction


def attach_diagnostics(report: dict, min_count: int) -> dict:
    """Keep 80/90/95 reports independently of the selected training gate.

    These thresholds use the same X, Y and joint within-5%-accuracy contract
    as advancement, but never influence scheduler state.
    """
    gates = {}
    for percentage in DIAGNOSTIC_PERCENTAGES:
        stages = {stage: rt.passed(score, percentage / 100.0, min_count)
                  for stage, score in report['scores'].items()}
        gates[str(percentage)] = {
            'passing': sum(stages.values()),
            'total': len(stages),
            'first_failing_in_selection': next((s for s, ok in stages.items() if not ok), None),
            'pass_by_stage': stages,
        }
    report['diagnostic_gates'] = gates
    report['advancement_percent'] = round(100.0 * report['gate_fraction'], 10)
    return report
