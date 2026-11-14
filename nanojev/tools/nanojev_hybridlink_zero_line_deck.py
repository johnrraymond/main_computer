#!/usr/bin/env python3
"""Deterministic bounded, full-chain diagnostic cases for the signed-zero saddle.

This deck is generated *for evaluation only*.  No targets may enter generated
HybridLink rollouts; a passing latent cosine is not an arithmetic certificate.
The first pair cancels a magnitude-band anchor into a small signed residual;
every subsequent partial sum stays near zero. No suffix-only examples.
"""
from __future__ import annotations

import hashlib
import json
import random
from collections import defaultdict

from tools import nanojev_qwen_problem_answer_stage1 as stage1

SCHEMA = 'nanojev.hybridlink.generated-bounded-zero-line-deck.v1'


def _rand(seed, rung, delta, band_index, variant, attempt):
    # Stable across processes/Python hash randomization; independent cell RNGs.
    key = f'{SCHEMA}|{seed}|{rung}|{delta}|{band_index}|{variant}|{attempt}'
    return random.Random(int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], 'big'))


def _numbers(rung, delta, low, high, seed, band_index, variant, attempt):
    rng = _rand(seed, rung, delta, band_index, variant, attempt)
    # A walk backwards from delta yields exactly rung tail increments.  All
    # accumulated sums and increments stay in [-8, 8] and [-high, high].
    states = [delta]
    limit = min(8, high)
    for step in range(rung):
        current = states[-1]
        options = [i for i in range(-limit, limit+1)
                   if abs(i-current) <= high]
        # Prefer genuine changes and frequent returns/crossings around zero.
        nontrivial = [i for i in options if i != current]
        options = nontrivial or options
        # Draws are stable yet vary the sign/path across cases.
        s = options[rng.randrange(len(options))]
        states.append(s)
    states.reverse()
    initial = states[0]
    if low == 0:
        anchors = [high, max(1,high//2), 1, high-1, 2, high-2, 3, high-3]
    else:
        anchors = [high, low, (low+high)//2, low+1, high-1,
                   (2*low+high)//3, (low+2*high)//3, low+2]
    anchor = anchors[(variant+attempt) % len(anchors)]
    sign = (1 if initial > 0 else -1 if initial < 0 else (1 if (variant+attempt)%2 == 0 else -1))
    first = sign * anchor
    second = initial - first
    # The first anchor takes the same sign as the nearby saddle state, so
    # cancellation never pushes its partner outside the chosen band.
    assert low <= max(abs(first),abs(second)) <= high
    assert all(abs(v) <= 8 for v in states)
    tail = [b-a for a,b in zip(states,states[1:])]
    nums = [first,second,*tail]
    assert len(nums) == rung+2 and sum(nums) == delta
    assert max(map(abs,nums)) <= high
    acc = 0
    for v in nums:
        acc += v
        assert abs(acc) <= high
    return nums


def build(*, max_rung, radius, operand_limit, per_cell=1, seed=42257, oracle):
    """Return (by_rung, available, selected) compatible with readiness.catalog.

    Only fully supported magnitude bands are used. For a partially capped band,
    its exact [lower, operand_limit] label comes from readiness.target_bands.
    """
    from tools import nanojev_hybridlink_readiness as ready
    by_rung={};available={};selected=[]
    bands=ready.target_bands(operand_limit)
    for rung in range(max_rung+1):
        cases=[];counts=defaultdict(int)
        for delta in range(-radius,radius+1):
            for band_index,(high,label) in enumerate(bands):
                low = 0 if band_index == 0 else bands[band_index-1][0]+1
                seen=set()
                for variant in range(per_cell):
                    for attempt in range(100):
                        nums=_numbers(rung,delta,low,high,seed,band_index,variant,attempt)
                        key=tuple(nums)
                        if key not in seen:break
                    else:
                        raise ValueError('ZERO_LINE_UNABLE_TO_GENERATE_DISTINCT_CASES')
                    seen.add(key)
                    rows=stage1._rows_for(nums,'synthetic-zero-line',len(cases))
                    chain=[r['source'] for r in rows]+[rows[-1]['target']]
                    meta=oracle(chain,stage1)
                    assert meta['answer']==delta and meta['max_literal']>=low
                    assert meta['max_literal']<=high and meta['steps']==rung+1
                    rec={'rung':rung,'chain':chain,'full_chain':True,**meta,
                         'origin':'synthetic_diagnostic_only',
                         'case_id':f'ZL-T{rung:02d}-D{delta:+03d}-B{band_index}-V{variant:02d}',
                         'band':label,'recipe':'bounded_cancellation_walk'}
                    cases.append(rec);counts[(rung,delta,label)]+=1
        by_rung[rung]=cases;available[rung]=dict(counts);selected.extend(cases)
    # Guard exact-data provenance; no generated source is a training reference.
    return by_rung,available,selected


def fingerprint(cases):
    payload=json.dumps([(c['case_id'],c['chain']) for c in cases],separators=(',',':'))
    return hashlib.sha256(payload.encode()).hexdigest()
