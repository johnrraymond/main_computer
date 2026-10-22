"""Oracle-checked nested-90% Hawk-Dove numerical-instance curriculum.

The target is X (NOT a or b): P(|X| < 10**k) = 0.9**(7-k)
for k=6,5,...,0, while integer a+b=1,000,000 and payoff tags remain
fixed. A feasibility audit rejects schedules the oracle cannot realize with
unique numerical instances. Repeated numerical instances are reported explicitly; the exact mixture
may require replay because the fixed integer oracle has sparse tiny-X support.
"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import random
import sqlite3

import torch
from tools import nanojev_hawk_dove_million as hd
from tools import nanojev_hawk_dove_coarse_to_fine_gaussian as fine

THRESHOLDS=(1_000_000,100_000,10_000,1_000,100,10,1)
SCHEMA='nanojev.hawk-dove.nested90-mixture.v1'


def quotas(n):
    if n < 1:raise ValueError('NESTED90_INVALID_COUNT')
    cumulative=[round(n*(.9**(i+1))) for i in range(len(THRESHOLDS))]
    return tuple([n-cumulative[0]]+[cumulative[i]-cumulative[i+1] for i in range(len(cumulative)-1)]+[cumulative[-1]])


def bin_for(x):
    a=abs(x)
    for i,threshold in enumerate(THRESHOLDS):
        if a>=threshold:return i
    return len(THRESHOLDS)


def original_counts(teacher,*,splits=('dev','test')):
    uri='file:'+Path(teacher).resolve().as_posix()+'?mode=ro'
    with sqlite3.connect(uri,uri=True) as con:
        rows=con.execute('SELECT a FROM instances WHERE split IN ('+','.join('?' for _ in splits)+')',tuple(splits)).fetchall()
    return {int(r[0]) for r in rows}


def certified_payoffs(fixture,teacher):
    # A changed fixture would silently redefine the meaning of the old model.
    manifest=hd.db_manifest(teacher)['config']
    payoffs,fixture_meta=hd.read_fixture(fixture)
    if manifest['fixture']['sha256']!=fixture_meta['sha256']:
        raise ValueError('NESTED90_PAYOFF_FIXTURE_DIFFERENT_FROM_PARENT_TEACHER')
    # The official Hawk-Dove teacher permits decimal payoff coefficients.
    # Its LogBar prompt compiler requires integral X and Y *outputs*, not
    # integral payoff coefficients. Rejecting the fixture here excludes valid
    # parent teachers. Candidate-by-candidate validation happens below.
    if not all(v.is_finite() for v in payoffs.values()):
        raise ValueError('NESTED90_NONFINITE_PAYOFF')
    return payoffs,fixture_meta


def available_counts(payoffs,excluded,*,candidates=None):
    """Find valid integer-count cases whose *X and Y targets* are integers.

    Payoff coefficients themselves may be exact decimal fractions. This is
    consistent with hd.oracle/hd.values_for/hd.render, which require integral
    outputs for the original numerical compiler. Arithmetic stays Decimal;
    converting the coefficients to int would silently change the game.
    """
    p=payoffs
    if not all(p[tag].is_finite() for tag in hd.TAGS):
        raise ValueError('NESTED90_NONFINITE_PAYOFF')
    x0=Decimal(1_000_000)*p[hd.TAGS[1]]
    dx=p[hd.TAGS[0]]-p[hd.TAGS[1]]
    y0=Decimal(1_000_000)*p[hd.TAGS[3]]
    dy=p[hd.TAGS[2]]-p[hd.TAGS[3]]
    buckets=[[] for _ in range(len(THRESHOLDS)+1)]
    source=range(1_000_001) if candidates is None else candidates
    for a in source:
        if a in excluded or not isinstance(a,int) or not 0<=a<=1_000_000:
            continue
        da=Decimal(a)
        x=x0+da*dx
        y=y0+da*dy
        # Certification and all 9 tier renderers require *both* outputs
        # integral; a merely rounded answer cannot be certified.
        if x!=x.to_integral_value() or y!=y.to_integral_value():
            continue
        if abs(x)>9_999_999 or abs(y)>9_999_999:
            continue
        buckets[bin_for(x)].append(a)
    return buckets


def plan(payoffs,excluded,n_train,n_dev,seed,*,candidates=None,strict_unique=False,reallocate_empty=False):
    if n_train<1 or n_dev<0:raise ValueError('NESTED90_INVALID_PLAN_SIZE')
    buckets=available_counts(payoffs,excluded,candidates=candidates)
    need_train=quotas(n_train)
    need_dev=quotas(n_dev) if n_dev else (0,)*len(need_train)
    have=[len(v) for v in buckets]
    original_quotas_train=need_train
    original_quotas_dev=need_dev
    transfers=[]
    if reallocate_empty:
        desired_train=list(need_train);desired_dev=list(need_dev)
        for i in range(len(have)):
            if not have[i] and desired_train[i]+desired_dev[i]>0:
                # Prefer the next *smaller* reachable magnitude for maximum
                # precision pressure; if none exists, use a larger band.
                choices=[j for j in range(len(have)) if have[j]>0]
                if not choices:break
                dest=min(choices,key=lambda j:(j<i,abs(j-i)))
                transfers.append({'from_band':i,'to_band':dest,
                                  'train':desired_train[i],'new_dev':desired_dev[i]})
                desired_train[dest]+=desired_train[i]
                desired_dev[dest]+=desired_dev[i]
                desired_train[i]=0;desired_dev[i]=0
        need_train=tuple(desired_train);need_dev=tuple(desired_dev)
    report={'schema':SCHEMA+'.feasibility.v1','thresholds':THRESHOLDS,
            'requested_train_per_tier':n_train,'requested_new_dev_per_tier':n_dev,
            'requested_quota_train_by_exclusive_band':original_quotas_train,
            'requested_quota_new_dev_by_exclusive_band':original_quotas_dev,
            'quota_train_by_exclusive_band':need_train,
            'quota_new_dev_by_exclusive_band':need_dev,
            'empty_band_transfers':transfers,
            'distribution_exact':not transfers,
            'available_valid_distinct_instances_not_in_old_holdouts_by_exclusive_band':have,
            'available_valid_distinct_instances_below_threshold':{
                str(t):sum(have[i] for i in range(j+1,len(have)))
                for j,t in enumerate(THRESHOLDS)},
            'excluded_original_instances':len(excluded)}
    short={str(i):{'required':need_train[i]+need_dev[i],'available':have[i]}
           for i in range(len(have)) if need_train[i]+need_dev[i]>have[i]}
    report['unique_instance_shortfalls']=short
    report['unique_quotas_feasible']=not short
    # Feasible with replacement only if each occupied bin has at least one
    # valid count (and two when distinct train/new-dev support is requested).
    unavailable={str(i):{'required':need_train[i]+need_dev[i],'available':have[i]}
                 for i in range(len(have))
                 if (need_train[i]+need_dev[i]>0 and
                     (have[i]==0 or (need_train[i]>0 and need_dev[i]>0 and have[i]<2)))}
    report['unreachable_bands']=unavailable
    report['feasible']=not unavailable and (not strict_unique or not short)
    if not report['feasible']:return report,None,None
    rng=random.Random('nested90/'+str(seed))
    train=[];dev=[]
    for i,values in enumerate(buckets):
        if not (need_train[i] or need_dev[i]):continue
        pool=list(values);rng.shuffle(pool)
        # Reserve at least one distinct held-out numerical instance in each
        # populated dev band. Never overlap numerical instances across splits.
        reserved_dev=pool[:1] if need_dev[i] else []
        pool=pool[1:] if reserved_dev else pool
        def fill(candidates,n):
            if n<=len(candidates):return candidates[:n]
            return candidates[:]+[rng.choice(candidates) for _ in range(n-len(candidates))]
        train.extend(fill(pool,need_train[i]))
        dev.extend(fill(reserved_dev,need_dev[i]))
    rng.shuffle(train);rng.shuffle(dev)
    assert not (set(train)&set(dev))
    report['unique_training_instances']=len(set(train))
    report['repeated_training_presentations_per_tier']=len(train)-len(set(train))
    report['unique_new_dev_instances']=len(set(dev))
    report['repeated_new_dev_presentations_per_tier']=len(dev)-len(set(dev))
    report['sampling_policy']='exact_nested90_with_explicit_replay_when_unique_oracle_support_insufficient'
    report['train_actual_bands']=dict(Counter(bin_for(hd.oracle(a,1_000_000-a,payoffs)[0]) for a in train))
    report['actual_cumulative_percent_below_threshold']={
        str(t):100*sum(need_train[i] for i in range(j+1,len(need_train)))/n_train
        for j,t in enumerate(THRESHOLDS)}
    return report,train,dev


def render_rows(counts,payoffs,seed,split,representation=hd.REP_LEGACY):
    rows={t:[] for t in hd.TIERS}
    for tier in hd.TIERS:
        for ordinal,a in enumerate(counts):
            b=1_000_000-a
            masks=list(hd.masks(tier,'lattice'))
            # Deterministic varied LogBar masks at every numerical instance.
            mask,full=masks[int.from_bytes(hashlib.sha256(f'{seed}/{split}/{tier}/{a}/{ordinal}'.encode()).digest()[:8],'big')%len(masks)]
            prompt=hd.render(tier,a,b,payoffs,full,representation=representation)
            certified=hd.certify(prompt,tier,a,b,payoffs,full,representation=representation)
            rows[tier].append({'instance':f'new-a-{a:07d}',
                               'case_id':f'new/{split}/{a:07d}/{tier}/{mask:02d}/{ordinal:07d}',
                               'tier':tier,'mask':mask,'full_count':len(full),
                               'prompt':prompt,'x':certified['x'],'y':certified['y'],
                               'z':certified['z']})
    return rows


def source_signature(rows):
    h=hashlib.sha256()
    for tier in hd.TIERS:
        for row in rows[tier]:
            h.update(json.dumps(row,sort_keys=True,ensure_ascii=False).encode('utf8'))
            h.update(b'\n')
    return h.hexdigest()


def load_features(model,decoder,bridge,cache,args,rows,split):
    """Use the existing frozen pre-body capture path without touching old teacher."""
    collection=fine.internal.FrozenPreBody(decoder)
    result={}
    try:
        for tier in hd.TIERS:
            group=rows[tier]
            batch=args.batch_size if split=='train' else fine.internal.DEV_BATCH
            captured=fine.internal.capture_rows(model,collection,cache,group,batch,model['device'])
            result[tier]=fine.feature_from_parent(decoder,bridge,captured)
            fine.fork.emit('nested90_features_ready',tier=tier,split=split,rows=len(group))
    finally:
        collection.close()
    return result


class Sampler:
    """Tier-balanced draws, uniform over truly distinct nested-90% instances.

    Do NOT apply the legacy half-magnitude-band-balancer; it would destroy
    the explicitly requested nested percentages.
    """
    def __init__(self,train):
        self.train=train
        n={len(train[t]['target']) for t in hd.TIERS}
        if len(n)!=1 or min(n)<1:raise ValueError('NESTED90_BAD_TIER_SUPPORT')
        self.n=n.pop()

    def draw(self,seed,step,views):
        rng=random.Random(f'nested90/{seed}/{step}/{views}')
        chosen=[(hd.TIERS[(step*views+j)%len(hd.TIERS)],rng.randrange(self.n)) for j in range(views)]
        rng.shuffle(chosen)
        return {k:torch.stack([self.train[t][k][i] for t,i in chosen],0)
                for k in ('features','coarse','target')}
