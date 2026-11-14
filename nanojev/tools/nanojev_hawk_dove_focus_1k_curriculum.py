"""Oracle-verified, balanced expected-count curriculum for 1 <= |X| < 1000.

Uses the original payoff fixture and 1,000,000 total expected encounters;
only the encounter mix varies. X and Y labels are ALWAYS recomputed from
exact Decimal counts; target X is never inserted into the forward inputs.
This curriculum is an explicitly separate research distribution.
"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
import hashlib
import random

import torch
from tools import nanojev_hawk_dove_nested90_expected as expected
from tools import nanojev_hawk_dove_million as hd

SCHEMA = 'nanojev.hawk-dove.focus-1k-expected.v1'
# Exclusive |X| bands, ascending numerical magnitude.
BANDS = (('1-10', Decimal('1'), Decimal('10')),
         ('10-100', Decimal('10'), Decimal('100')),
         ('100-1000', Decimal('100'), Decimal('1000')))


def band(x):
    a = abs(Decimal(str(x)))
    for name, lo, hi in BANDS:
        if lo <= a < hi:
            return name
    return None


def counts_per_band(n):
    if n < 3:
        raise ValueError('FOCUS_1K_TOO_FEW_INSTANCES')
    q, rem = divmod(n, 3)
    return {name: q + int(i < rem) for i, (name, _, _) in enumerate(BANDS)}


def _segments(payoffs):
    with localcontext() as ctx:
        ctx.prec = 70
        slope, offset, _ = expected.spans(payoffs)
        if not slope:
            return slope, offset, {}
        x0 = offset
        x1 = offset + expected.TOTAL * slope
        lowest, highest = min(x0, x1), max(x0, x1)
        ans = {}
        for name, lo, hi in BANDS:
            parts = []
            for left, right in ((-hi, -lo), (lo, hi)):
                l, r = max(lowest, left), min(highest, right)
                if l < r:
                    parts.append((l, r))
            ans[name] = parts
        return slope, offset, ans


def plan(payoffs, excluded, n_train, n_dev, seed):
    """Produce disjoint UNIQUE expected counts in all three bands.

    `excluded` are original integer teacher counts; fractional encounters
    cannot collide, and held-out NEW fractional values are disjoint.
    Fail closed if a band is unreachable or unique samples cannot be found.
    """
    if n_train < 3 or n_dev < 3:
        raise ValueError('FOCUS_1K_TRAIN_AND_DEV_REQUIRED')
    train_quotas = counts_per_band(n_train)
    dev_quotas = counts_per_band(n_dev)
    with localcontext() as ctx:
        ctx.prec = 70
        slope, offset, segments = _segments(payoffs)
        unavailable = [name for name, _, _ in BANDS if not segments.get(name)]
        report = {'schema': SCHEMA, 'feasible': not unavailable,
                  'oracle_kind': 'exact_decimal_expected_encounters',
                  'train_per_tier': n_train, 'new_dev_per_tier': n_dev,
                  'bands': list(train_quotas),
                  'requested_train_by_band': train_quotas,
                  'requested_new_dev_by_band': dev_quotas,
                  'unreachable_bands': unavailable,
                  'sampling_policy': 'equal_band_1_10_100_1000_unique_counts',
                  'test_split_read': False}
        if unavailable:
            return report, None, None
        rng = random.Random(f'focus-1k-v1/{seed}')
        reserved = set()
        choices = {}
        for name, _, _ in BANDS:
            need = train_quotas[name] + dev_quotas[name]
            samples = []
            for ordinal in range(need):
                for _attempt in range(20000):
                    l, r = rng.choice(segments[name])
                    u = Decimal(rng.randrange(1, 10**15)) / Decimal(10**15)
                    x = l + (r - l) * u
                    a = ((x - offset) / slope).quantize(expected.GRID, rounding=ROUND_HALF_EVEN)
                    # Original integer-count teacher is never reused.
                    if not 0 < a < expected.TOTAL or a == a.to_integral_value() or a in reserved:
                        continue
                    actual_x, actual_y, _ = expected.expected_oracle(a, payoffs)
                    if (band(actual_x) != name or abs(actual_y) > Decimal('9999999') or
                        abs(actual_x) > Decimal('9999999')):
                        continue
                    reserved.add(a)
                    samples.append(a)
                    break
                else:
                    raise ValueError(f'FOCUS_1K_UNIQUE_SUPPORT_EXHAUSTED:{name}:{ordinal}')
            rng.shuffle(samples)
            choices[name] = samples
        train = []
        dev = []
        for name, _, _ in BANDS:
            cut = train_quotas[name]
            train += choices[name][:cut]
            dev += choices[name][cut:]
        rng.shuffle(train)
        rng.shuffle(dev)
        if len(set(train)) != n_train or len(set(dev)) != n_dev or set(train) & set(dev):
            raise ValueError('FOCUS_1K_SPLIT_LEAKAGE_OR_REPLAY')
        if any(a == a.to_integral_value() for a in train + dev):
            raise ValueError('FOCUS_1K_INTEGER_REUSE')
        report.update({'unique_train_instances': len(train), 'unique_new_dev_instances': len(dev),
                       'train_actual_by_band': dict(Counter(band(expected.expected_oracle(a, payoffs)[0]) for a in train)),
                       'dev_actual_by_band': dict(Counter(band(expected.expected_oracle(a, payoffs)[0]) for a in dev)),
                       'repeated_train_instances': 0, 'repeated_new_dev_instances': 0,
                       'feasible': True})
        return report, train, dev


class Sampler:
    """Every minibatch draws from *all three* focus bands and all nine tiers.

    Draws use TRAIN tensors exclusively, with replacement across steps.
    Each epoch sees a changing selection of numerical instances.
    """
    def __init__(self, train):
        self.train = train
        self.by_band = {name: [] for name, _, _ in BANDS}
        self.by_tier_band = {(tier, name): [] for tier in hd.TIERS for name, _, _ in BANDS}
        for tier in hd.TIERS:
            for i, y in enumerate(train[tier]['target'].tolist()):
                name = band(y)
                if name is None:
                    raise ValueError('FOCUS_1K_OUT_OF_RANGE_TRAINING_TARGET')
                self.by_band[name].append((tier, i))
                self.by_tier_band[tier, name].append(i)
        if any(not indices for indices in self.by_tier_band.values()):
            raise ValueError('FOCUS_1K_MISSING_TIER_BAND_TRAINING')

    def draw(self, seed, step, views):
        if views < 9:
            raise ValueError('FOCUS_1K_MIN_VIEWS_9')
        rng = random.Random(f'focus-1k-draw/{seed}/{step}/{views}')
        selected = []
        # Rotate three bands × nine tiers; 27-view windows guarantee full coverage.
        for j in range(views):
            t = hd.TIERS[(step * views + j) % len(hd.TIERS)]
            name = BANDS[(step * views + j) // len(hd.TIERS) % 3][0]
            # For default views=32, every band appears in the batch; as steps
            # advance a 27-draw block visits every (tier,band) combination.
            choices = self.by_tier_band[t, name]
            selected.append((t, rng.choice(choices)))
        rng.shuffle(selected)
        return {k: torch.stack([self.train[t][k][i] for t, i in selected], dim=0)
                  for k in ('features','coarse','target')}
