"""Online per-presentation training sampler for the isolated Hawk-Dove curriculum.

Uses ONLY train-split predictions recorded during existing optimizer forwards.
Never evaluates, mines, or stores held-out dev/test predictions. Tier gates,
objective, checkpoint ancestry and per-tier advancement are out of scope.
"""
from __future__ import annotations

import math
from typing import Any

SCHEMA = 'nanojev.hawk-dove.prompt-balance.v1'


def relative_error(predicted: float, target: float) -> float:
    return abs(float(predicted) - float(target)) / max(1.0, abs(float(target)))


def priority(error: float | None) -> float:
    """Never zero: solved prompts remain eligible for weighted and uniform draws."""
    if error is None:
        return 1.0  # unexplored training presentation
    if error <= 0.05:
        return 0.15
    return 1.0 + min(5.0, 2.0 * (error / 0.05 - 1.0))


def alias_table(weights: list[float]) -> tuple[list[float], list[int]]:
    """Numerically bounded O(n) preparation, O(1) weighted sampling."""
    count = len(weights)
    if not count:
        raise ValueError('PROMPT_BALANCE_EMPTY_POOL')
    if any(not math.isfinite(w) or w <= 0 for w in weights):
        raise ValueError('PROMPT_BALANCE_INVALID_WEIGHTS')
    total = sum(weights)
    scaled = [w * count / total for w in weights]
    smaller = [i for i, w in enumerate(scaled) if w < 1.0]
    larger = [i for i, w in enumerate(scaled) if w >= 1.0]
    prob = [1.0] * count
    alias = list(range(count))
    while smaller and larger:
        lo = smaller.pop()
        hi = larger.pop()
        prob[lo] = scaled[lo]
        alias[lo] = hi
        scaled[hi] -= 1.0 - scaled[lo]
        (smaller if scaled[hi] < 1.0 else larger).append(hi)
    return prob, alias


class PromptBalance:
    """Checkpoint-serializable error-directed sampler; no model inference added."""

    def __init__(self, teacher_sha256: str, state: dict | None = None):
        self.teacher_sha256 = teacher_sha256
        if state is None:
            self.records: dict[str, dict[str, dict]] = {}
            self.last_commit_step = 0
        else:
            if (not isinstance(state, dict) or state.get('schema') != SCHEMA
                    or state.get('teacher_sha256') != teacher_sha256
                    or not isinstance(state.get('records'), dict)):
                raise ValueError('PROMPT_BALANCE_CHECKPOINT_LINEAGE_DRIFT')
            self.records = state['records']
            self.last_commit_step = int(state.get('last_commit_step', 0))
        self.pending: dict[str, dict[str, list[float]]] = {}
        self.cache: dict[str, tuple[list[dict], list[float], list[int]]] = {}

    def snapshot(self) -> dict:
        if self.pending:
            raise ValueError('PROMPT_BALANCE_PENDING_CHECKPOINT')
        return {'schema': SCHEMA, 'teacher_sha256': self.teacher_sha256,
                'last_commit_step': self.last_commit_step, 'records': self.records}

    def build(self, tier: str, rows: list[dict]):
        """Weights change only after a completed chunk, never mid-chunk."""
        if not rows:
            raise ValueError('PROMPT_BALANCE_EMPTY_POOL')
        if any(row['tier'] != tier for row in rows):
            raise ValueError('PROMPT_BALANCE_CROSS_TIER_POOL')
        if len({row['case_id'] for row in rows}) != len(rows):
            raise ValueError('PROMPT_BALANCE_DUPLICATE_PRESENTATIONS')
        state = self.records.get(tier, {})
        weights = [priority(state[r['case_id']]['error'] if r['case_id'] in state else None)
                   for r in rows]
        prob, alias = alias_table(weights)
        self.cache[tier] = (rows, prob, alias)

    def choose(self, tier: str, rng, rows: list[dict]):
        if tier not in self.cache:
            self.build(tier, rows)
        pool, prob, alias = self.cache[tier]
        # Exactly 20% uniform refresher including previously mastered prompts.
        if rng.random() < 0.20:
            idx = rng.randrange(len(pool))
        else:
            column = rng.randrange(len(pool))
            idx = column if rng.random() < prob[column] else alias[column]
        return pool[idx]

    def observe(self, row: dict, predicted_x: float, predicted_y: float):
        ex = relative_error(predicted_x, row['x'])
        ey = relative_error(predicted_y, row['y'])
        if not math.isfinite(ex) or not math.isfinite(ey):
            raise ValueError('PROMPT_BALANCE_NONFINITE_PREDICTION')
        observations = self.pending.setdefault(row['tier'], {}).setdefault(row['case_id'], [])
        observations.append(max(ex, ey))

    def commit(self, tier: str, step: int) -> dict[str, Any]:
        measured = self.pending.pop(tier, {})
        records = self.records.setdefault(tier, {})
        for case_id, errors in measured.items():
            observed = sum(errors) / len(errors)
            previous = records.get(case_id)
            ema = observed if previous is None else (0.5 * previous['error'] + 0.5 * observed)
            records[case_id] = {'error': float(ema), 'seen': (previous['seen'] if previous else 0)+len(errors),
                                'last_step': int(step)}
        self.last_commit_step = int(step)
        if tier in self.cache:
            self.build(tier, self.cache[tier][0])
        return self.summary(tier, len(measured))

    def summary(self, tier: str, refreshed: int = 0) -> dict:
        records = self.records.get(tier, {})
        pool = self.cache.get(tier)
        n = len(pool[0]) if pool else 0
        passed = sum(r['error'] <= .05 for r in records.values())
        failures = len(records) - passed
        return {'tier': tier, 'train_presentations': n, 'measured': len(records),
                'unmeasured': max(0, n - len(records)),
                'measured_passing': passed, 'measured_failing': failures,
                'refreshed_this_chunk': refreshed,
                'uniform_refresh_fraction': .20, 'priority_min': .15,
                'priority_max': 6., 'last_commit_step': self.last_commit_step,
                'train_split_only': True}
