"""Deterministic target-magnitude sampling for the existing GAMER C0 Wide head.

Only certified *training* answers determine eligibility. The oracle numerical
answer is never passed into the model as an input. Dev/test remain untouched.
"""
from __future__ import annotations

import math
from collections import defaultdict

import numpy as np


FOCUS_CLASSES = ('zero', 'negative', 'positive')
DEFAULT_POLICY = {'focus': 0.0, 'radius': 1.0}


def resolve_policy(*, focus=None, radius=None, previous=None):
    """Omitted CLI options inherit the last committed policy on a resume."""
    policy = dict(DEFAULT_POLICY)
    if previous is not None:
        if not isinstance(previous, dict) or set(previous) != {'focus', 'radius'}:
            raise ValueError('GAMER_ZERO_LINE_CHECKPOINT_POLICY_INVALID')
        policy.update(previous)
    if focus is not None:
        policy['focus'] = float(focus)
    if radius is not None:
        policy['radius'] = float(radius)
    if not math.isfinite(policy['focus']) or not 0.0 <= policy['focus'] <= 1.0:
        raise ValueError('GAMER_ZERO_LINE_FOCUS_MUST_BE_BETWEEN_ZERO_AND_ONE')
    if not math.isfinite(policy['radius']) or policy['radius'] <= 0.0:
        raise ValueError('GAMER_ZERO_LINE_RADIUS_MUST_BE_POSITIVE_AND_FINITE')
    return policy


class ZeroLineSampler:
    """Uniform baseline plus game-balanced zero, negative and positive bands.

    A focus=0 draw uses the pre-existing vectorized NumPy draw exactly, so
    historical uniform checkpoint resumption keeps its RNG sequence unchanged.
    """
    def __init__(self, train, *, focus, radius):
        self.policy = resolve_policy(focus=focus, radius=radius)
        self.length = len(train)
        if not self.length:
            raise ValueError('GAMER_ZERO_LINE_TRAINING_CORPUS_EMPTY')
        by_class = {key: defaultdict(list) for key in FOCUS_CLASSES}
        all_games = set()
        for i, row in enumerate(train):
            game = row['game_id']
            all_games.add(game)
            answer = float(row['answer'])
            if not math.isfinite(answer):
                raise ValueError('GAMER_ZERO_LINE_NONFINITE_CERTIFIED_ANSWER')
            kind = ('zero' if answer == 0.0 else
                    'negative' if -radius <= answer < 0.0 else
                    'positive' if 0.0 < answer <= radius else None)
            if kind:
                by_class[kind][game].append(i)
        self.groups = {
            key: tuple((game, tuple(by_class[key][game])) for game in sorted(by_class[key]))
            for key in FOCUS_CLASSES
        }
        self.coverage = {
            'train_cases': self.length,
            'train_games': len(all_games),
            'eligible_cases_by_class': {key: sum(len(indices) for _, indices in self.groups[key])
                                        for key in FOCUS_CLASSES},
            'eligible_games_by_class': {key: len(self.groups[key]) for key in FOCUS_CLASSES},
            'eligible_games_any': len(set().union(*(set(by_class[k]) for k in FOCUS_CLASSES))),
            'games_without_eligible_examples': sorted(all_games - set().union(
                *(set(by_class[k]) for k in FOCUS_CLASSES))),
            'target_basis': 'certified_training_answer_not_residual',
            'focus_sampling': 'uniform_class_then_uniform_game_then_uniform_case',
            'baseline_sampling': 'uniform_certified_training_case',
        }
        if focus > 0.0:
            absent = [key for key in FOCUS_CLASSES if not self.groups[key]]
            if absent:
                raise ValueError('GAMER_ZERO_LINE_FOCUS_MISSING_TARGET_CLASS: '+','.join(absent))

    def draw(self, rng, batch_size):
        """Return indices and strata. Uses only the passed checkpointed RNG."""
        if batch_size < 1:
            raise ValueError('GAMER_ZERO_LINE_BATCH_MUST_BE_POSITIVE')
        if self.policy['focus'] == 0.0:
            return rng.integers(0, self.length, size=batch_size), ['uniform'] * batch_size
        choices = np.empty(batch_size, dtype=np.int64)
        strata = []
        for i in range(batch_size):
            if rng.random() >= self.policy['focus']:
                choices[i] = rng.integers(0, self.length)
                strata.append('uniform')
            else:
                kind = FOCUS_CLASSES[int(rng.integers(0, len(FOCUS_CLASSES)))]
                group = self.groups[kind]
                _, indices = group[int(rng.integers(0, len(group)))]
                choices[i] = indices[int(rng.integers(0, len(indices)))]
                strata.append(kind)
        return choices, strata


def target_zero_line_metrics(rows, predictions, *, radius):
    """Held-out target bands, separate from optional zero_line_residual tags."""
    truth = np.asarray([float(r['answer']) for r in rows], dtype=np.float64)
    prediction = np.asarray(predictions, dtype=np.float64)
    if truth.shape != prediction.shape or not np.isfinite(prediction).all():
        raise ValueError('GAMER_ZERO_LINE_INVALID_PREDICTIONS')
    classes = {
        'zero': truth == 0.0,
        'negative': (truth < 0.0) & (truth >= -radius),
        'positive': (truth > 0.0) & (truth <= radius),
        'outside_radius': np.abs(truth) > radius,
    }
    result = {'radius': float(radius), 'target_basis': 'certified_answer', 'by_class': {}}
    for kind, mask in classes.items():
        n = int(mask.sum())
        if not n:
            result['by_class'][kind] = {'cases': 0}
            continue
        errs = np.abs(prediction[mask] - truth[mask])
        entry = {'cases': n, 'mae': float(errs.mean()),
                 'p90_abs_error': float(np.percentile(errs, 90)),
                 'within_0_1': float(np.mean(errs <= 0.1)),
                 'within_1': float(np.mean(errs <= 1.0)),
                 'within_1e_6': float(np.mean(errs <= 1e-6))}
        if kind == 'zero':
            entry['predicted_zero_within_0_1'] = entry['within_0_1']
        else:
            entry['correct_sign'] = float(np.mean(np.sign(prediction[mask]) == np.sign(truth[mask])))
        result['by_class'][kind] = entry
    return result
