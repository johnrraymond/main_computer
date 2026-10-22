"""New, exactly certified EXPECTED-COUNT Hawk–Dove nested-90 curriculum.

The original teacher uses integer observations. Here a,b are continuous
expected counts across 1,000,000 encounters, a+b=1,000,000. The four fixed
payoff coefficients remain unchanged. Every emitted X,Y is recomputed from
the exact Decimal a,b and payoff fixture, not altered by rounding the oracle.
The existing teacher, numeric adapter and its checkpoint fingerprint are
never modified. This is a *different task distribution*, explicitly named.
"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal, localcontext, ROUND_HALF_EVEN
import hashlib
import json
import random
import re

import torch
from tools import nanojev_hawk_dove_million as hd
from tools import nanojev_hawk_dove_nested90_mixture as old
from tools import nanojev_hawk_dove_coarse_to_fine_gaussian as fine
from tools import nanojev_hawk_dove_gaussian_register_fork as fork

SCHEMA='nanojev.hawk-dove.nested90-expectation.v1'
THRESHOLDS=old.THRESHOLDS
quotas=old.quotas
bin_for=old.bin_for
original_counts=old.original_counts
certified_payoffs=old.certified_payoffs
source_signature=old.source_signature

TOTAL=Decimal(1_000_000)
GRID=Decimal('0.000000000001')


def expected_oracle(a, payoffs):
    with localcontext() as ctx:
        ctx.prec=70
        a=Decimal(a)
        b=TOTAL-a
        if not (a.is_finite() and b.is_finite() and 0<=a<=TOTAL):
            raise ValueError('EXPECTED_COUNT_OUT_OF_RANGE')
        x=a*payoffs[hd.TAGS[0]]+b*payoffs[hd.TAGS[1]]
        y=a*payoffs[hd.TAGS[2]]+b*payoffs[hd.TAGS[3]]
        if not all(v.is_finite() for v in (x,y)):
            raise ValueError('EXPECTED_NONFINITE_ORACLE')
        return x,y,('HAWK' if x>y else 'DOVE' if x<y else 'TIE')


def spans(payoffs):
    x0=TOTAL*payoffs[hd.TAGS[1]]
    dx=payoffs[hd.TAGS[0]]-payoffs[hd.TAGS[1]]
    xmin=min(x0,x0+TOTAL*dx)
    xmax=max(x0,x0+TOTAL*dx)
    bounds=[]
    for i in range(8):
        if i==0:
            targets=[(None,-Decimal(THRESHOLDS[0])),(Decimal(THRESHOLDS[0]),None)]
        elif i==7:
            targets=[(-Decimal(THRESHOLDS[-1]),Decimal(THRESHOLDS[-1]))]
        else:
            hi=Decimal(THRESHOLDS[i-1]);lo=Decimal(THRESHOLDS[i])
            targets=[(-hi,-lo),(lo,hi)]
        current=[]
        for left,right in targets:
            a=xmin if left is None else max(xmin,left)
            b=xmax if right is None else min(xmax,right)
            if a<b:
                current.append((a,b))
        bounds.append(current)
    return dx,x0,bounds


def plan(payoffs,excluded,n_train,n_dev,seed,*,candidates=None,strict_unique=False,reallocate_empty=False):
    """Sample distinct Decimal expected-count values, never repeat labels.

    Exclusions apply to integer teacher counts; fractional expected counts do
    not collide with them. `candidates` is deliberately not supported here.
    """
    if candidates is not None:
        raise ValueError('EXPECTED_CURRICULUM_NO_INTEGER_CANDIDATES')
    if n_train<1 or n_dev<0:
        raise ValueError('NESTED90_INVALID_PLAN_SIZE')
    with localcontext() as ctx:
        ctx.prec=70
        dx,x0,geometry=spans(payoffs)
        n=len(geometry)
        train_need=list(quotas(n_train));dev_need=list(quotas(n_dev)) if n_dev else [0]*n
        original_train=list(train_need);original_dev=list(dev_need)
        transfers=[]
        if reallocate_empty:
            for i in range(n):
                if not geometry[i] and train_need[i]+dev_need[i]:
                    available=[j for j in range(n) if geometry[j]]
                    if available:
                        dest=min(available,key=lambda j:(j<i,abs(j-i)))
                        transfers.append({'from_band':i,'to_band':dest,'train':train_need[i],'new_dev':dev_need[i]})
                        train_need[dest]+=train_need[i];dev_need[dest]+=dev_need[i]
                        train_need[i]=0;dev_need[i]=0
        impossible={str(i):{'required':train_need[i]+dev_need[i],'available':0}
                    for i in range(n) if (train_need[i]+dev_need[i]) and not geometry[i]}
        report={'schema':SCHEMA+'.feasibility.v1',
                'oracle_kind':'exact_decimal_expected_encounters_not_integer_observations',
                'payoff_tags_unchanged':True,'encounter_total':'1000000',
                'thresholds':THRESHOLDS,'requested_train_per_tier':n_train,
                'requested_new_dev_per_tier':n_dev,
                'requested_quota_train_by_exclusive_band':original_train,
                'requested_quota_new_dev_by_exclusive_band':original_dev,
                'quota_train_by_exclusive_band':train_need,'quota_new_dev_by_exclusive_band':dev_need,
                'available_x_intervals_by_exclusive_band':[
                  [{'lo':str(lo),'hi':str(hi)} for lo,hi in band] for band in geometry],
                'unique_quotas_feasible':not impossible,'unique_instance_shortfalls':impossible,
                'unreachable_bands':impossible,'empty_band_transfers':transfers,
                'distribution_exact':not transfers,'feasible':not impossible}
        if impossible:
            return report,None,None
        if dx==0:
            report['feasible']=False;report['unreachable_bands']={'all':'EXPECTED_NO_VARIATION_IN_X'}
            return report,None,None
        rng=random.Random(f'expected90/{seed}')
        sampled=[];used=set()
        # Interleave bands but preserve quota deterministically. Uniform draws
        # in X-space, not a-space; round the INPUT to a finite exact decimal.
        for i in range(n):
            segments=geometry[i]
            for j in range(train_need[i]+dev_need[i]):
                for attempt in range(20000):
                    lo,hi=segments[rng.randrange(len(segments))]
                    u=Decimal(rng.randrange(1,10**15))/Decimal(10**15)
                    requested_x=lo+(hi-lo)*u
                    a=((requested_x-x0)/dx).quantize(GRID,rounding=ROUND_HALF_EVEN)
                    if a==a.to_integral_value() or not 0<a<TOTAL or a in used:
                        continue
                    actual_x,actual_y,_=expected_oracle(a,payoffs)
                    if bin_for(actual_x)!=i or abs(actual_x)>Decimal('9999999') or abs(actual_y)>Decimal('9999999'):
                        continue
                    used.add(a)
                    sampled.append((i,a))
                    break
                else:
                    raise ValueError(f'EXPECTED90_UNIQUE_COVERAGE_FAILED:band={i}:requested={j}')
        training=[];new_dev=[]
        for i in range(n):
            entries=[a for k,a in sampled if k==i]
            rng.shuffle(entries)
            training.extend(entries[:train_need[i]])
            new_dev.extend(entries[train_need[i]:])
        rng.shuffle(training);rng.shuffle(new_dev)
        assert len(set(training))==len(training) and not (set(training)&set(new_dev))
        report['unique_training_instances']=len(training)
        report['repeated_training_presentations_per_tier']=0
        report['unique_new_dev_instances']=len(new_dev)
        report['repeated_new_dev_presentations_per_tier']=0
        report['sampling_policy']='unique_fractional_expected_counts_exact_oracle'
        report['train_actual_bands']=dict(Counter(bin_for(expected_oracle(a,payoffs)[0]) for a in training))
        report['actual_cumulative_percent_below_threshold']={
            str(t):100*sum(train_need[i] for i in range(j+1,n))/n_train
            for j,t in enumerate(THRESHOLDS)}
        report['nonzero_below_one_training']=sum(0<abs(expected_oracle(a,payoffs)[0])<1 for a in training)
        report['min_abs_nonzero_x']=str(min((abs(expected_oracle(a,payoffs)[0]) for a in training if expected_oracle(a,payoffs)[0]!=0),default=Decimal(0)))
        return report,training,new_dev


def render_one(tier,a,p):
    """Preserve operation trees and payoff tags, make expectation semantics clear."""
    if tier not in hd.TIERS:
        raise ValueError('EXPECTED_BAD_TIER')
    a=Decimal(a);b=TOTAL-a
    x,y,_=expected_oracle(a,p)
    # Full numerical annotation masks were designed for integer LogBar values;
    # no bars/scientific approximations are fabricated for decimal operands.
    vals={'a':a,'b':b,'x':x,'y':y}
    prompt=hd.PLACEHOLDER.sub(lambda m:hd.canonical_num(vals[m.group(1)[0]]),hd.TEMPLATES[tier])
    if tier in ('T07','T08'):
        prompt=prompt.replace(f'Your opponent plays Hawk {hd.canonical_num(a)} out of a million times.',
             f'Across one million encounters, the expected number of Hawk plays is {hd.canonical_num(a)}.')
        prompt=prompt.replace(f'Your opponent plays Dove {hd.canonical_num(b)} out of the same million times.',
             f'Across those encounters, the expected number of Dove plays is {hd.canonical_num(b)}.')
    certified=certify_one(prompt,tier,a,p)
    return prompt,certified


def certify_one(prompt,tier,a,p):
    a=Decimal(a);b=TOTAL-a
    x,y,z=expected_oracle(a,p)
    if prompt!=hd.PLACEHOLDER.sub(lambda m:hd.canonical_num({'a':a,'b':b,'x':x,'y':y}[m.group(1)[0]]),hd.TEMPLATES[tier]).replace(
         f'Your opponent plays Hawk {hd.canonical_num(a)} out of a million times.',
         f'Across one million encounters, the expected number of Hawk plays is {hd.canonical_num(a)}.').replace(
         f'Your opponent plays Dove {hd.canonical_num(b)} out of the same million times.',
         f'Across those encounters, the expected number of Dove plays is {hd.canonical_num(b)}.'):
        raise ValueError('EXPECTED_PROMPT_TEMPLATE_DRIFT')
    if '|' in prompt or re.search(r'\d(?:\.\d+)?[eE][+-]?\d+',prompt):
        raise ValueError('EXPECTED_UNSUPPORTED_ANNOTATION')
    if tier!='T08':
        expr=prompt[prompt.index('(ARGMAX'):]
        with localcontext() as ctx:
            ctx.prec=70
            if hd.evaluate(hd.parse_lisp(expr),p)!=(x,y):
                raise ValueError('EXPECTED_ORACLE_MISMATCH')
    if tier in ('T07','T08'):
        if f'expected number of Hawk plays is {hd.canonical_num(a)}' not in prompt or f'expected number of Dove plays is {hd.canonical_num(b)}' not in prompt:
            raise ValueError('EXPECTED_SEMANTIC_MISMATCH')
    return {'x':hd.canonical_num(x),'y':hd.canonical_num(y),'z':z}


def render_rows(counts,payoffs,seed,split):
    rows={t:[] for t in hd.TIERS}
    for tier in hd.TIERS:
        for ordinal,a in enumerate(counts):
            prompt,result=render_one(tier,a,payoffs)
            # Mask is zero: annotations for decimal numbers are intentionally
            # absent and explicitly described in run reports.
            token=hashlib.sha256(f'{a}'.encode()).hexdigest()[:20]
            rows[tier].append({'instance':f'expected-a-{token}',
                'case_id':f'expected/{split}/{token}/{tier}/{ordinal:07d}',
                'tier':tier,'mask':0,'full_count':0,'prompt':prompt,
                'x':result['x'],'y':result['y'],'z':result['z'],
                'expected_a':str(a),'expected_b':str(TOTAL-a)})
    return rows


def encode_expected(row):
    """Donor adapter's 12-coordinate role encoding, with Decimal counts.

    Role data is derived from prompt-visible expected counts, never X or Y.
    This local reader does not edit the frozen donor's adapter/checkpoint.
    """
    tier=row['tier'];a=Decimal(row['expected_a']);b=Decimal(row['expected_b'])
    certify_one(row['prompt'],tier,a,row['_payoffs']) if '_payoffs' in row else None
    has_h=tier in ('T02','T03','T04','T05','T06','T07')
    has_d=tier in ('T01','T03','T04','T05','T06','T07')
    is_prose=tier in ('T07','T08')
    scaled=[float(a/TOTAL),float(b/TOTAL)]
    values=[scaled[0] if has_h else 0.,scaled[1] if has_h else 0.,
            scaled[0] if has_d else 0.,scaled[1] if has_d else 0.]
    flags=[float(has_h),float(has_h),float(has_d),float(has_d)]
    prose=(scaled if is_prose else [0.,0.])
    return tuple(values+flags+prose+[float(is_prose)]*2)


def capture_expected(model,collector,cache,rows,batch_size,device):
    """Frozen pre-body capture with expected-count adapter features.

    Reproduces upstream forward_masked without monkeypatching any globally
    shared numeric adapter function (whose hash is part of donor identity).
    """
    from tools import nanojev_hawk_dove_gaussian_internal_bank_ab as internal
    import torch
    pieces={k:[] for k in ('features','original_x','original_y','original_g','target_x','target_y')}
    for start in range(0,len(rows),batch_size):
        chunk=rows[start:start+batch_size]
        for r in chunk:
            if not fork.rt.token_eligible(model,r['prompt']):
                raise ValueError('EXPECTED_PROMPT_TOKEN_INELIGIBLE:'+r['case_id'])
        decoder=model['decoder'];dev=model['device']
        prompts=[r['prompt'] for r in chunk]
        channels=[fork.xyop.pack(r['x'],r['y'],'011') for r in chunk]
        collector.last=None
        with torch.no_grad():
            flat,seq,mask=cache.capture(model,prompts)
            h=model['parent'].extract(flat.to(dev))
            pxy=model['parent'].xy_coord(h)
            atom=torch.tensor([encode_expected(r) for r in chunk],dtype=torch.float32,device=dev)
            xy_hint=torch.tensor(channels,dtype=torch.float32,device=dev)
            h=h+decoder.numeric_adapter(atom)+decoder.xyop_mask_adapter(xy_hint)
            op=torch.full((len(chunk),),model['base'].OP_IDS['written_sub'],device=dev,dtype=torch.long)
            output=decoder(h,pxy,op,seq.to(dev),mask.to(dev))
            pre=collector.last
            if pre is None or pre.ndim!=3 or pre.shape[:2]!=(len(chunk),2):
                raise ValueError('EXPECTED_CAPTURE_MISSING')
        pieces['features'].append(pre.float().detach())
        for dst,src in [('original_x',output['xy'][:,0]),('original_y',output['xy'][:,1]),('original_g',output['log_magnitude'])]:
            pieces[dst].append(src.detach().float())
        pieces['target_x'].append(torch.as_tensor([float(r['x']) for r in chunk],device=device))
        pieces['target_y'].append(torch.as_tensor([float(r['y']) for r in chunk],device=device))
    return {k:torch.cat(v,dim=0) for k,v in pieces.items()}


def load_features(model,decoder,bridge,cache,args,rows,split):
    collection=fine.internal.FrozenPreBody(decoder)
    result={}
    try:
        for tier in hd.TIERS:
            group=rows[tier]
            batch=args.batch_size if split=='train' else fine.internal.DEV_BATCH
            captured=capture_expected(model,collection,cache,group,batch,model['device'])
            result[tier]=fine.feature_from_parent(decoder,bridge,captured)
            fork.emit('nested90_expected_features_ready',tier=tier,split=split,rows=len(group))
    finally:
        collection.close()
    return result

Sampler=old.Sampler
