#!/usr/bin/env python3
"""Independent exact-balance audit for C0 payoff encoders.

The audit is never used to fit neural models, deterministic geometry, or select
checkpoints. It tests exact cancellation and signed power-of-two imbalances
against multiple independently controlled payoff-term magnitudes.
"""
from __future__ import annotations
import csv
import hashlib
import json
import math
from pathlib import Path
import numpy as np

DEFAULT_DELTAS = (0, 1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192)
DEFAULT_MAGNITUDES = (100, 1000, 10000, 100000, 1000000)


def positive_ints(text, *, include_zero=False):
    values=tuple(int(v.strip()) for v in text.split(','))
    if not values or any(v < (0 if include_zero else 1) for v in values) or len(set(values))!=len(values):
        raise ValueError('expected distinct nonnegative integers' if include_zero else 'expected distinct positive integers')
    return values


def exact_balanced_case(delta, magnitude, pair, index, family_game, seed):
    """Construct positive integral operands satisfying p*a-q*b=delta exactly.

    For coprime p,q, a = delta*inverse(p,q) mod q. Choose an a near
    magnitude/p and advance by multiples of q. This varies input values while
    preserving the exact signed imbalance and keeping both large terms near M.
    """
    p,q=pair
    if math.gcd(p,q)!=1 or q<=1 or p<=1:
        raise ValueError('p and q must be coprime integers > 1')
    if abs(delta)>magnitude//4:
        return None # Do not conflate cancellation tests with tiny operand terms.
    a0=(delta*pow(p,-1,q))%q
    # Jitter each pair in a controlled local interval without changing the
    # targeted term-magnitude decade.
    token=f'{seed}:{magnitude}:{delta}:{p}:{q}:{index}'.encode()
    jitter=int.from_bytes(hashlib.sha256(token).digest()[:4],'big')%35-17
    approximate=max(1, (magnitude + (jitter*magnitude)//200)//p)
    t=round((approximate-a0)/q)
    a=a0+t*q
    b=(p*a-delta)//q
    if a<=0 or b<=0:
        return None
    left=p*a;right=q*b
    if not (magnitude//2<=min(left,right) and max(left,right)<=2*magnitude):
        return None
    family,game=family_game
    prompt=(f'In the {game} game ({family}), an agent receives {p} payoff units for each of {a} '
            f'type-A encounters and loses {q} payoff units for each of {b} type-B encounters. '
            'What is its exact total signed payoff? Answer with one integer.')
    return {'prompt':prompt,'answer':int(delta),'p':p,'q':q,'a':a,'b':b,'game':game,
            'family':family,'scale':'balance_audit','imbalance':abs(delta),
            'sign':int(np.sign(delta)),'magnitude_target':int(magnitude),
            'left_term':int(left),'right_term':int(right)}


def make_balance_deck(*, deltas=DEFAULT_DELTAS, magnitudes=DEFAULT_MAGNITUDES,
                      samples_per_cell=8, seed=47, excluded_prompts=()):
    """Fixed seeded diagnostic; balanced count for each feasible signed cell."""
    from tools.nanojev_qwen_five_centroids import NAMES
    if samples_per_cell<1: raise ValueError('samples_per_cell must be >= 1')
    excluded=set(excluded_prompts)
    # Varied coprime coefficient ratios; do not rely on just 3a-2b.
    pairs=((3,2),(5,3),(7,4),(4,3),(7,5),(9,7),(11,8),(13,9),
           (8,5),(11,7),(13,11),(17,13),(19,17),(5,4),(9,5),(11,9))
    rows=[];seen=set()
    for magnitude in magnitudes:
        for d in deltas:
            for sign in ((0,) if d==0 else (-1,1)):
                target=d*sign
                got=0
                for attempt in range(samples_per_cell*100):
                    idx=attempt+(magnitude*31+d*17+sign*19+seed)
                    pair=pairs[idx%len(pairs)]
                    name=NAMES[(idx*17+attempt//len(pairs))%len(NAMES)]
                    row=exact_balanced_case(target,magnitude,pair,attempt,name,seed)
                    if row is None or row['prompt'] in excluded or row['prompt'] in seen:
                        continue
                    seen.add(row['prompt']);rows.append(row);got+=1
                    if got==samples_per_cell:break
                if d<=magnitude//4 and got!=samples_per_cell:
                    raise RuntimeError(f'failed balance audit coverage: magnitude={magnitude}, delta={target}, got={got}')
    return rows


def error_stats(true,pred):
    yt=np.asarray(true,dtype=np.float64);yp=np.asarray(pred,dtype=np.float64)
    if len(yt)==0:return {'n':0}
    err=np.abs(yp-yt)
    nearest=np.rint(yp)
    sign_true=np.sign(yt);sign_pred=np.sign(yp)
    return {'n':int(len(yt)), 'mae':float(err.mean()),'median_abs_error':float(np.median(err)),
            'p95_abs_error':float(np.percentile(err,95)),
            'rounded_exact_pct':float(100*np.mean(nearest==yt)),
            'within_1_pct':float(100*np.mean(err<=1)),
            'sign_accuracy_pct':float(100*np.mean(sign_pred==sign_true)),
            'false_zero_pct':float(100*np.mean((nearest==0)&(yt!=0))),
            'zero_hit_pct':float(100*np.mean((nearest==0)&(yt==0))) if np.any(yt==0) else None,
            'mean_signed_error':float(np.mean(yp-yt))}


def summarize_balance(rows,predictions):
    """Per-imbalance/term-magnitude, signed and pooled bands, plus aggregate."""
    y=np.asarray([r['answer'] for r in rows],np.float64)
    mags=sorted({r['magnitude_target'] for r in rows})
    ds=sorted({r['imbalance'] for r in rows})
    result={}
    for method,pred in predictions.items():
        pred=np.asarray(pred,np.float64)
        if len(pred)!=len(rows):raise ValueError('prediction size mismatch for '+method)
        cells=[]; bands=[]
        for d in ds:
            mask=np.asarray([r['imbalance']==d for r in rows])
            bands.append({'delta':d,**error_stats(y[mask],pred[mask])})
            for m in mags:
                for sign in ((0,) if d==0 else (-1,1)):
                    which=np.asarray([r['imbalance']==d and r['magnitude_target']==m and r['sign']==sign for r in rows])
                    if which.any():cells.append({'delta':d,'magnitude':m,'sign':sign,**error_stats(y[which],pred[which])})
        magnitude_rows=[]
        for m in mags:
            mask=np.asarray([r['magnitude_target']==m for r in rows])
            magnitude_rows.append({'magnitude':m,**error_stats(y[mask],pred[mask])})
        result[method]={'overall':error_stats(y,pred),'by_delta':bands,
                        'by_magnitude':magnitude_rows,'cells':cells}
    return result


def save_balance_report(directory,stage,rows,predictions,settings):
    out=Path(directory);out.mkdir(parents=True,exist_ok=True)
    result=summarize_balance(rows,predictions)
    payload={'schema':'nanojev.c0.balance-audit.v1','stage':stage,'settings':settings,
             'interpretation':'Untrained diagnostic deck. No audit prompts/answers enter fitting or checkpoint selection.',
             'models':result}
    (out/'balance_report.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
    with (out/'balance_predictions.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.writer(f)
        w.writerow(['game','family','p','q','a','b','left_term','right_term','magnitude_target',
                    'delta','sign','true',*predictions.keys()])
        for i,r in enumerate(rows):
            w.writerow([r['game'],r['family'],r['p'],r['q'],r['a'],r['b'],r['left_term'],r['right_term'],
                        r['magnitude_target'],r['imbalance'],r['sign'],r['answer'],
                        *[float(predictions[name][i]) for name in predictions]])
    with (out/'balance_cells.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.writer(f)
        w.writerow(['method','delta','magnitude','sign','n','mae','median_abs_error','p95_abs_error',
                    'rounded_exact_pct','within_1_pct','sign_accuracy_pct','false_zero_pct','zero_hit_pct'])
        for method,data in result.items():
            for c in data['cells']:
                w.writerow([method,*[c.get(k) for k in ('delta','magnitude','sign','n','mae','median_abs_error','p95_abs_error','rounded_exact_pct','within_1_pct','sign_accuracy_pct','false_zero_pct','zero_hit_pct')]])
    return payload
