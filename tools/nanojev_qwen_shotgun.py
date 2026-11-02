#!/usr/bin/env python3
"""Deterministic, varied mathematical-balance acquisition population.

Shotgun is a fourth joint optimizer population, not a restatement of HDB07/08.
Equivalent underlying payoff balances have multiple surface representations.
The complete frozen evaluation deck never enters the optimizer. Generated
train cases are independent of step-budget progression and explicitly vary
scale, cancellation, offset, coefficients, order, and language.
"""
from __future__ import annotations
import hashlib
import json
import random

VERSION = 'nanojev.qwen.shotgun.v1'
MODES = ('products', 'contributions', 'structured', 'prose',
         'reordered_prose', 'table', 'intermediate', 'word_problem')
SCALES = (1, 10, 100, 1000, 100000, 10000000)
# All errors are exact signed integers; none are interpreted as a class label.


def _random(seed, index, mode):
    payload=json.dumps((VERSION,int(seed),int(index)))
    return random.Random(int.from_bytes(hashlib.sha256(payload.encode()).digest()[:8], 'big'))


def case(mode, index, *, seed=42257, evaluation=False):
    if mode not in MODES or index<0:
        raise ValueError('SHOTGUN_INVALID_CASE')
    rng=_random(seed,index,mode)
    p=rng.randint(2,21);q=rng.randint(2,21)
    scale=SCALES[index%len(SCALES)]
    # Offset is NOT a function of the optimizer's 24M-step ceiling.
    # Balance is exactly -(p+q)*offset. Zero, negative and positive appear.
    offset_choices=(0,1,-1,2,-2,8,-8,64,-64,1024,-1024)
    offset=offset_choices[index%len(offset_choices)]
    multiplier=max(scale, abs(offset)+1)
    hawks=q*multiplier+offset
    doves=p*multiplier-offset
    hawk_co=-p; dove_co=q
    hc=hawk_co*hawks;dc=dove_co*doves
    total=hc+dc
    if mode=='products':
        prompt=f'({hawk_co}*{hawks})+({dove_co}*{doves})'
    elif mode=='contributions':
        prompt=f'Combine numerical contributions ({hc})+({dc})'
    elif mode=='structured':
        prompt=f'Balance: Hawk encounters={hawks}; Hawk contribution/encounter={hawk_co}; Dove encounters={doves}; Dove contribution/encounter={dove_co}. Compute the signed total.'
    elif mode=='prose':
        prompt=f'Each of {hawks} Hawk encounters contributes {hawk_co} to the balance. Each of {doves} Dove encounters contributes +{dove_co}. What is the final signed balance?'
    elif mode=='reordered_prose':
        prompt=f'Find the signed aggregate. A Dove encounter adds {dove_co}; there are {doves} Dove encounters. There are also {hawks} Hawk encounters, each subtracting {p}.'
    elif mode=='table':
        prompt=f'Encounter counts and coefficients: [Dove: {doves}, {dove_co}] [Hawk: {hawks}, {hawk_co}]. Return the sum of count times coefficient.'
    elif mode=='intermediate':
        prompt=f'Hawk subtotal={hc}; Dove subtotal={dc}. Add both subtotals and return the signed balance.'
    else:
        prompt=f'A balance starts at zero. It loses {p} points on each of {hawks} Hawk turns and gains {q} points on each of {doves} Dove turns. Where does the balance finish?'
    src='PROBLEM: '+prompt
    return {'id':f'SHOTGUN:{mode}:{index}', 'family':'shotgun','task':'shotgun',
            'mode':mode,'scale':scale,'zero_side':'zero' if total==0 else ('positive' if total>0 else 'negative'),
            'source':src,'target':'PROBLEM: '+str(total),'terminal_kind':'int',
            'metadata':{'hawk_count':hawks,'dove_count':doves,'hawk_coefficient':hawk_co,
                        'dove_coefficient':dove_co,'offset':offset,'expected_balance':total,
                        'is_evaluation':bool(evaluation)}}


def evaluation(seed, per_mode=6):
    """Full frozen mode coverage. Indices never overlap training's >=10k band."""
    if not 1<=per_mode<=32: raise ValueError('SHOTGUN_EVAL_CASES_OUT_OF_RANGE')
    return [case(mode,i,seed=seed,evaluation=True)
            for mode in MODES for i in range(per_mode)]


def train_case(seed,step,mode,forbidden):
    """Fresh deterministic sample with exact source-disjointness enforcement."""
    for attempt in range(128):
        row=case(mode,10000+step*131+attempt,seed=seed)
        if row['source'] not in forbidden:
            return row
    raise RuntimeError('SHOTGUN_TRAIN_EVAL_COLLISION')


def exposures():
    return {mode:{'examples':0,'steps':0} for mode in MODES}


def metrics(rows,before,after,key):
    def fresh(): return {'tested':0,'correct':0,'gained':0,'lost':0,'net':0}
    total=fresh();by_mode={};by_scale={};by_side={}
    for row in rows:
        k=key(row)
        a=bool(before[k]['correct']);b=bool(after[k]['correct'])
        buckets=(total,by_mode.setdefault(row['mode'],fresh()),
                 by_scale.setdefault(str(row['scale']),fresh()),
                 by_side.setdefault(row['zero_side'],fresh()))
        for d in buckets:
            d['tested']+=1;d['correct']+=int(b)
            d['gained']+=int(b and not a);d['lost']+=int(a and not b)
            d['net']=d['gained']-d['lost']
    for d in (total,*by_mode.values(),*by_scale.values(),*by_side.values()):
        d['accuracy']=d['correct']/d['tested'] if d['tested'] else None
    return {'total':total,'by_mode':by_mode,'by_scale':by_scale,'by_side':by_side}


def error_distribution(rows, scores, key):
    """Diagnostic only: normalized residuals, robust spread and sign accuracy.

    Report by presentation mode as well as by scale. Never treat an approximate
    answer as exactly correct and never use these heldouts for supervision.
    """
    from statistics import median
    from math import log1p, copysign
    from tools.nanojev_qwen_lm_radius_ladder import numeric_terminal

    def quantile(values,fraction):
        if not values:return None
        v=sorted(values);a=(len(v)-1)*fraction;lo=int(a);hi=min(lo+1,len(v)-1)
        return v[lo]+(v[hi]-v[lo])*(a-lo)

    def summary(samples):
        residuals=[];correct_sign=0;good=0
        for row,record in samples:
            val=numeric_terminal(record.get('emitted',''))
            if val is None:continue
            answer=row['metadata']['expected_balance']
            unit=abs(row['metadata']['hawk_coefficient'])+row['metadata']['dove_coefficient']
            normalized=(val-answer)/unit
            residuals.append(normalized)
            good+=int(val==answer)
            correct_sign+=int((val>0)-(val<0)==(answer>0)-(answer<0))
        transformed=[copysign(log1p(abs(x)),x) for x in residuals]
        q25=quantile(transformed,.25);q75=quantile(transformed,.75)
        var=((q75-q25)/1.349)**2 if q25 is not None else None
        return {'tested':len(samples),'numeric_outputs':len(residuals),
                'exact_correct_numeric':good,
                'correct_sign_rate_of_numeric':correct_sign/len(residuals) if residuals else None,
                'median_absolute_error_offset_units':median(map(abs,residuals)) if residuals else None,
                'p90_absolute_error_offset_units':quantile([abs(x) for x in residuals],.9),
                'robust_variance_signed_log_offset_error':var}
    grouped={'by_mode':{},'by_scale':{},'by_side':{}}
    for row in rows:
        for field,key_name in (('by_mode','mode'),('by_scale','scale'),('by_side','zero_side')):
            grouped[field].setdefault(str(row[key_name]),[]).append((row,scores[key(row)]))
    return {'schema':'nanojev.shotgun.normalized-error.v1','total':summary([(r,scores[key(r)]) for r in rows]),
            **{field:{group:summary(samples) for group,samples in groups.items()}
               for field,groups in grouped.items()}}


def compare_distributions(before, after):
    """Matched fixed-deck, same-scoring-change diagnostics; not training gains."""
    if not before or before.get('schema')!=after.get('schema'):
        return None
    names=('correct_sign_rate_of_numeric','median_absolute_error_offset_units',
           'p90_absolute_error_offset_units','robust_variance_signed_log_offset_error')

    def compare(a,b):
        if a is None or b is None:return None
        compatible=(a['tested']==b['tested'] and
                    a['numeric_outputs']==a['tested'] and
                    b['numeric_outputs']==b['tested'])
        return {'fully_comparable_numeric_residuals':compatible,
                'previous_numeric_outputs':a['numeric_outputs'],
                'current_numeric_outputs':b['numeric_outputs'],
                'deltas':{field:(b[field]-a[field] if compatible and
                        a[field] is not None and b[field] is not None else None)
                          for field in names}}
    return {'total':compare(before['total'],after['total']),
            **{group:{label:compare(before.get(group,{}).get(label),item)
                       for label,item in after[group].items()}
               for group in ('by_mode','by_scale','by_side')}}
