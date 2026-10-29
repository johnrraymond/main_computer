#!/usr/bin/env python3
"""Qwen LM-head one-hop acquisition: T00 -> ANSWER; T01..T08 -> PROBLEM.

This deliberately does NOT invoke HybridLink, detector, numeric sidecars, or a
final-answer executor. Targets are used for masked supervised fine-tuning ONLY;
greedy LM-head continuation of the source packet is the *only* certificate.
Every optimizer-step budget is durable, per-rung, and never auto-extended.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import random
import re
import shutil
import time
from pathlib import Path

from tools import nanojev_qwen_problem_answer_stage1 as stage1

SCHEMA = 'nanojev.qwen.lm-exact-radius-ladder.v1'
RADII_DEFAULT = '0,1,2,4,8'
TARGET = re.compile(r'^(ANSWER: -?(?:0|[1-9]\d*)|PROBLEM: .+)$')


def _hash(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def parse_radii(spec: str, operand_limit: int):
    try:
        radii = [int(s.strip()) for s in spec.split(',')]
    except ValueError as exc:
        raise ValueError('LADDER_INVALID_RADII') from exc
    if not radii or radii[0] != 0 or any(x < 0 or x > operand_limit for x in radii) or any(a >= b for a,b in zip(radii,radii[1:])):
        raise ValueError('LADDER_RADII_MUST_START_ZERO_GROW_STRICTLY_AND_FIT_OPERAND_LIMIT')
    return tuple(radii)


def bands(limit: int):
    if limit < 1:raise ValueError('LADDER_OPERAND_LIMIT_MUST_BE_POSITIVE')
    edges=(8,64,1024,32768,1000000)
    out=[];prev=-1
    for edge in (*edges,limit):
        cap=min(edge,limit)
        if cap>prev:
            out.append((prev+1,cap,f'{prev+1}-{cap}'))
            prev=cap
        if prev==limit:break
    return tuple(out)


def _rng(*parts):
    return random.Random(int(_hash(parts)[:16],16))


def _bounded_numbers(rung, residual, low, high, limit, seed, variant, attempt=0):
    """Return complete reduction leaves, with ALL literal/partial magnitudes bounded.

    The first leaf is a magnitude-band anchor. Subsequent leaves are chosen
    from intervals that preserve mathematical feasibility for remaining leaves.
    No literal or partial sum is silently clamped or oracle-corrected.
    """
    n=rung+2
    if abs(residual)>min(limit,n*high):return None
    rng=_rng(SCHEMA,seed,rung,residual,low,high,variant,attempt)
    chooser=(variant+attempt)%3
    anchor = high if chooser==0 else (low if chooser==1 else (low+high)//2)
    anchor=max(1,anchor)
    sign=(-1 if residual<0 else 1) if (variant+attempt)%2==0 else (-1 if residual>=0 else 1)
    # At saturation the anchor must point in the direction of the sum.
    if abs(residual)>(n-2)*high: sign=-1 if residual<0 else 1
    first=sign*anchor
    remaining=residual-first
    if abs(remaining)>(n-1)*high:
        first=(-1 if residual<0 else 1)*high
        remaining=residual-first
    if abs(remaining)>(n-1)*high:return None
    nums=[first];partial=first
    for k in range(1,n):
        rest=n-k-1
        lo=max(-high,remaining-rest*high,-limit-partial)
        hi=min(high,remaining+rest*high,limit-partial)
        if lo>hi:return None
        if k==n-1:pick=remaining
        else:
            # Prefer cancellation near zero, but include varied valid paths.
            best=max(lo,min(hi,-partial))
            if attempt == 0 and variant%3==0:pick=best
            else:pick=rng.randint(lo,hi)
        nums.append(pick);remaining-=pick;partial+=pick
    if remaining or partial!=residual:return None
    if not(low<=max(map(abs,nums))<=high):return None
    assert all(abs(v)<=limit for v in nums)
    accum=0
    for x in nums:
        accum+=x
        assert abs(accum)<=limit
    return tuple(nums)


def _case(rung, nums, tag):
    rows=stage1._rows_for(list(nums),'ladder',0)
    assert len(rows)==rung+1
    source=rows[0]['source'];target=rows[0]['target']
    if rung==0:assert target.startswith('ANSWER: ')
    else:assert target.startswith('PROBLEM: ')
    return {'id':tag,'rung':rung,'residual':sum(nums),'source':source,'target':target,
            'leaves':list(nums),'complete_chain':[r['source'] for r in rows]+[rows[-1]['target']]}


def deck(rung,radius,limit,per_cell=8,seed=42257,t00_mode='exhaustive'):
    """Deterministic *finite* acquisition contract, no holdout semantics.

    T00 exhaustive is all ORDERED pairs in [-limit,limit] with sum in [-R,R].
    Upper rungs are explicitly a finite sample of complete reduction chains.
    Cells physically impossible for a given band are marked unreachable.
    """
    if rung<0 or not 0<=radius<=limit or per_cell<1:raise ValueError('LADDER_BAD_DECK_ARGUMENTS')
    if t00_mode=='exhaustive' and rung==0:
        cases=[]
        for d in range(-radius,radius+1):
            for a in range(-limit,limit+1):
                b=d-a
                if -limit<=b<=limit:
                    cases.append(_case(0,(a,b),f'T00-D{d:+d}-A{a:+d}'))
        assert len(cases)==(2*radius+1)*(2*limit+1)-radius*(radius+1)
        return cases,{'kind':'exhaustive_ordered_t00','cells':2*radius+1,
                       'unreachable_cells':[],'undersampled_cells':[],
                       'case_count':len(cases)}
    cases=[];unreachable=[];undersampled=[];covered=0
    for d in range(-radius,radius+1):
        for lo,hi,label in bands(limit):
            if abs(d)>min(limit,(rung+2)*hi):
                unreachable.append({'residual':d,'band':label,'reason':'arithmetic_unreachable'})
                continue
            seen=set();count=0
            for variant in range(per_cell):
                for attempt in range(200):
                    nums=_bounded_numbers(rung,d,lo,hi,limit,seed,variant,attempt)
                    if nums is not None and nums not in seen:break
                else:break
                seen.add(nums)
                cases.append(_case(rung,nums,f'T{rung:02d}-D{d:+d}-B{label}-V{variant}'))
                count+=1
            if count!=per_cell:
                undersampled.append({'residual':d,'band':label,'observed':count,'requested':per_cell})
            else:covered+=1
    return cases,{'kind':'finite_deterministic_full_chain_deck','cells':covered,
                  'unreachable_cells':unreachable,'undersampled_cells':undersampled,
                  'case_count':len(cases),'cases_per_cell':per_cell}


def deck_fingerprint(rows):
    return _hash([(c['id'],c['source'],c['target']) for c in rows])


def parse_packet(text):
    """Accept one and only one canonical emitted line; NEVER execute a reduction."""
    cleaned=text.replace('\r\n','\n').replace('\r','\n')
    if cleaned.endswith('\n'):cleaned=cleaned[:-1]
    lines=cleaned.split('\n')
    if len(lines)!=1:return None
    value=lines[0]
    if not TARGET.fullmatch(value):return None
    return value


def correctness(source,target,completion):
    got=parse_packet(completion)
    return got==target,got


def _atomic_json(path,payload):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp')
    temp.write_text(json.dumps(payload,sort_keys=True,indent=2)+'\n',encoding='utf8')
    os.replace(temp,path)


def _atomic_torch(path,payload):
    import torch
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp')
    torch.save(payload,temp);os.replace(temp,path)


def _outputs_safe(output,parents):
    out=Path(output).resolve()
    for item in parents:
        p=Path(item).resolve()
        if out==p or out in p.parents or p in out.parents:
            raise RuntimeError('LADDER_OUTPUT_OVERLAPS_INPUT')


def _config(args):
    radii=parse_radii(args.radii,args.operand_limit)
    if not 0<=args.max_rung<=8:raise ValueError('LADDER_MAX_RUNG_OUT_OF_RANGE')
    if args.steps_per_rung<1 or args.repair_steps<1 or args.batch_size<1 or args.lr<=0 or args.max_seq<16 or args.max_new_tokens<8:
        raise ValueError('LADDER_BAD_TRAINING_HYPERPARAMETERS')
    if args.max_tokens<args.max_seq or args.per_cell<1 or args.replay_per_batch<0 or args.replay_per_batch>=args.batch_size:
        raise ValueError('LADDER_BAD_CONTEXT_OR_COVERAGE')
    if getattr(args,'probe_cases',128)<1 or getattr(args,'audit_progress_every',32)<0:
        raise ValueError('LADDER_BAD_CONTEXT_OR_COVERAGE')
    return {'schema':SCHEMA,'radii':list(radii),'operand_limit':args.operand_limit,
       'max_rung':args.max_rung,'per_cell':args.per_cell,'seed':args.seed,'t00_mode':args.t00_mode,
       'steps_per_rung':args.steps_per_rung,'repair_steps':args.repair_steps,
       'batch_size':args.batch_size,'replay_per_batch':args.replay_per_batch,
       'lr':args.lr,'max_seq':args.max_seq,
       'max_tokens':args.max_tokens,'max_new_tokens':args.max_new_tokens,
       'stage1_checkpoint':str(Path(args.stage1_checkpoint).resolve()) if args.stage1_checkpoint else None,
       'text_checkpoint':str(Path(args.text_checkpoint).resolve()) if args.text_checkpoint else None,
       'model_family':'Qwen q/v LoRA, frozen base + frozen LM vocabulary head',
       'grading':'greedy LM-head exact one-hop emitted packet, NOT teacher-forcing or cosine',
       'no_hybridlink':True,'no_detector':True,'no_numeric_sidecars':True}


def _batch_loss(bb,tok,rows,device,max_seq):
    import torch
    examples=[]
    for case in rows:
        src=tok(case['source']+'\n',add_special_tokens=False)['input_ids']
        suffix=tok(case['target']+'\n',add_special_tokens=False)['input_ids']
        if not src or not suffix or len(src)+len(suffix)>max_seq:
            raise ValueError(f'LADDER_TOKEN_LIMIT:{case["id"]}')
        examples.append((src+suffix,[-100]*len(src)+suffix))
    pad=tok.pad_token_id if tok.pad_token_id is not None else tok.eos_token_id
    if pad is None:raise ValueError('LADDER_TOKENIZER_MISSING_PAD')
    length=max(len(a) for a,_ in examples)
    x=torch.full((len(rows),length),int(pad),device=device,dtype=torch.long)
    y=torch.full_like(x,-100);mask=torch.zeros_like(x)
    for i,(a,b) in enumerate(examples):
        x[i,:len(a)]=torch.tensor(a,device=device)
        y[i,:len(b)]=torch.tensor(b,device=device)
        mask[i,:len(a)]=1
    result=bb(input_ids=x,attention_mask=mask,labels=y,return_dict=True,use_cache=False)
    return result.loss


def _greedy(bb,tok,sources,device,max_tokens,max_new_tokens):
    """Greedy unassisted one-line Qwen LM-head generation, batched by length.

    Left-padding with attention mask avoids short inputs attending to pad tokens.
    Decoder is used directly; no numerical sidecars/teacher states are invoked.
    """
    import torch
    head=bb.get_output_embeddings();decoder=bb.get_decoder()
    if head is None:raise RuntimeError('LADDER_LM_HEAD_UNAVAILABLE')
    pieces=[tok(s+'\n',add_special_tokens=False)['input_ids'] for s in sources]
    if any(not x or len(x)+max_new_tokens>max_tokens for x in pieces):
        raise ValueError('LADDER_INFERENCE_CONTEXT_LIMIT')
    pad=tok.pad_token_id if tok.pad_token_id is not None else tok.eos_token_id
    if pad is None:raise ValueError('LADDER_NO_PAD_ID')
    lengths=[len(p) for p in pieces];width=max(lengths);size=len(pieces)
    ids=torch.full((size,width),int(pad),device=device,dtype=torch.long)
    mask=torch.zeros_like(ids)
    for i,p in enumerate(pieces):
        ids[i,width-len(p):]=torch.tensor(p,device=device)
        mask[i,width-len(p):]=1
    new=[[] for _ in pieces];done=[False]*size
    eos=tok.eos_token_id
    eos_set={int(eos)} if isinstance(eos,int) else set(eos or ())
    with torch.inference_mode():
        for _ in range(max_new_tokens):
            out=decoder(input_ids=ids,attention_mask=mask,use_cache=False,return_dict=True)
            h=out.last_hidden_state[:,-1,:]
            weight=getattr(head,'weight',None)
            logits=head(h.to(dtype=weight.dtype if weight is not None else h.dtype))
            tokens=logits.argmax(dim=-1).tolist()
            for i,token in enumerate(tokens):
                if done[i]:continue
                if token in eos_set:done[i]=True;continue
                new[i].append(token)
                current=tok.decode(new[i],skip_special_tokens=True)
                if '\n' in current or '\r' in current:done[i]=True
            if all(done):break
            extra=torch.tensor([[int(pad) if done[i] else int(tokens[i])] for i in range(size)],device=device)
            ids=torch.cat((ids,extra),dim=1)
            active=torch.tensor([[0 if done[i] else 1] for i in range(size)],device=device)
            mask=torch.cat((mask,active),dim=1)
    return [tok.decode(x,skip_special_tokens=True) for x in new]


def _probe_rows(rows, budget=128):
    """Stable diagnostic probe across signed result and operand bands.

    A probe is only an early training signal; it can NEVER issue a certificate.
    The full exact deck is still required after the probe passes.
    """
    if budget < 1:raise ValueError('LADDER_PROBE_BUDGET_MUST_BE_POSITIVE')
    if len(rows) <= budget:return rows
    by_cell={}
    for i,row in enumerate(rows):
        magnitude=max(abs(n) for n in row.get('leaves',()))
        band=0 if magnitude<=8 else (1 if magnitude<=64 else 2)
        cell=(row.get('residual',0),band)
        by_cell.setdefault(cell,[]).append(i)
    chosen=set()
    # Visit every attainable radius-by-band cell where the budget permits.
    representatives=[indices[len(indices)//2] for _,indices in sorted(by_cell.items())]
    if len(representatives)>budget:
        representatives=[representatives[min(len(representatives)-1,
            (j*(len(representatives)-1))//max(1,budget-1))] for j in range(budget)]
    chosen.update(representatives)
    remaining=budget-len(chosen)
    if remaining:
        # Cover the tails and interiors of the ordered full deck, not its prefix.
        for j in range(remaining):
            chosen.add((j*(len(rows)-1))//max(1,remaining-1))
    if len(chosen)<budget:
        rng=_rng('LADDER_PROBE_FILL',len(rows),budget,rows[0]['id'])
        remaining_indices=[i for i in range(len(rows)) if i not in chosen]
        chosen.update(rng.sample(remaining_indices,min(budget-len(chosen),len(remaining_indices))))
    return [rows[i] for i in sorted(chosen)]


def _sampled_progress_rows(rows, sample_cases, sample_count, seed):
    """Deterministic, rotating, signed/magnitude-stratified acquisition samples.

    Prefer disjoint samples; sampling is an acquisition decision, never proof
    about the complete deck. No fixed holdout: samples change with attempts.
    """
    if sample_cases<1 or sample_count<1:raise ValueError('LADDER_BAD_ADVANCEMENT_SAMPLE')
    available=list(rows)
    groups=[]
    for sample_index in range(sample_count):
        if not available:available=list(rows)
        rng=_rng('LADDER_ADVANCE_SAMPLE',seed,sample_index,len(rows))
        by_cell={}
        for row in available:
            magnitude=max(map(abs,row.get('leaves',()) or (0,)))
            band=0 if magnitude<=8 else (1 if magnitude<=64 else (2 if magnitude<=1024 else 3))
            by_cell.setdefault((row.get('residual',0),band),[]).append(row)
        chosen=[]
        cells=list(sorted(by_cell))
        rng.shuffle(cells)
        for cell in cells[:min(sample_cases,len(cells))]:
            chosen.append(rng.choice(by_cell[cell]))
        used={r['id'] for r in chosen}
        remaining=[r for r in available if r['id'] not in used]
        chosen.extend(rng.sample(remaining,min(sample_cases-len(chosen),len(remaining))))
        # Important for sequential error tripwires: never process an ordered
        # stratum prefix and extrapolate its error rate to the entire deck.
        rng.shuffle(chosen)
        groups.append(chosen)
        chosen_ids={r['id'] for r in chosen}
        available=[r for r in available if r['id'] not in chosen_ids]
    return groups


def _retention_sample_rows(rows, budget, deck_sha256, attempt):
    """Rotating proportional stratified sample, WITHOUT replacing the original deck.

    The sample itself (not the separately graded failure-regression set) is
    used for the 95% decision. Strata cover signed residuals, direction and
    operand magnitude. Proportional quotas keep raw sample accuracy a
    reasonable estimate of full-deck accuracy, rather than over-weighting
    rare cases. A repeatable cyclic permutation reduces overlap across checks.
    """
    if budget<1:raise ValueError('LADDER_RETENTION_SAMPLE_MUST_BE_POSITIVE')
    if len(rows)<=budget:return list(rows)
    if attempt<0:raise ValueError('LADDER_RETENTION_ATTEMPT_MUST_BE_NONNEGATIVE')
    strata={}
    for row in rows:
        leaves=row.get('leaves',())
        first=leaves[0] if leaves else 0
        magnitude=max((abs(v) for v in leaves),default=0)
        band=0 if magnitude<=8 else (1 if magnitude<=64 else (2 if magnitude<=1024 else 3))
        residual=row.get('residual',0)
        sign=lambda v: -1 if v<0 else (1 if v>0 else 0)
        strata.setdefault((sign(residual),sign(first),band),[]).append(row)
    # Proportional allocation with randomized tie breaks across attempts.
    # No separate fixed holdout: every member remains eligible on later passes.
    rng=_rng('LADDER_RETENTION_QUOTAS',deck_sha256,attempt)
    quotas={};remainders=[];allocated=0
    for label,group in sorted(strata.items()):
        ideal=budget*len(group)/len(rows)
        count=int(ideal)
        quotas[label]=count;allocated+=count
        remainders.append((ideal-count,rng.random(),label))
    for _,_,label in sorted(remainders,reverse=True)[:budget-allocated]:
        quotas[label]+=1
    selected=[]
    for label,group in sorted(strata.items()):
        number=quotas[label]
        if not number:continue
        ordered=list(group)
        _rng('LADDER_RETENTION_PERMUTATION',deck_sha256,label).shuffle(ordered)
        start=(attempt*number)%len(ordered)
        selected.extend(ordered[(start+i)%len(ordered)] for i in range(number))
    assert len(selected)==budget and len({r['id'] for r in selected})==budget
    # The proportionally sampled strata must be interleaved before a gate
    # stops at the first mathematically disqualifying number of errors.
    _rng('LADDER_RETENTION_GATE_ORDER',deck_sha256,attempt).shuffle(selected)
    return selected


def _project_errors(tested, errors, population):
    """Project the error count for a randomly ordered, partially seen deck.

    A Jeffreys Beta(1/2,1/2) posterior regularizes very short runs. The
    already observed errors are counted exactly; only the unobserved cases
    are extrapolated. This is a *model-based estimate*, not a certificate or
    a sequentially valid confidence bound. A sequentially stopped sample
    should not be marketed as an ordinary fixed-n confidence interval.
    """
    if not (0<=errors<=tested<=population):
        raise ValueError('LADDER_INVALID_ERROR_PROJECTION')
    unknown=population-tested
    rate=(errors+0.5)/(tested+1) if tested else 0.5
    expected=errors+unknown*rate
    return {'observed_errors':errors,'observed_tested':tested,
            'population_total':population,'unseen_cases':unknown,
            'observed_error_rate':errors/tested if tested else None,
            'estimated_error_rate':expected/population if population else None,
            'estimated_total_errors':round(expected,2),
            'raw_rate_extrapolated_errors':round(errors+unknown*(errors/tested if tested else 0),2),
            'estimate_kind':'jeffreys_exchangeable_projection',
            'estimate_is_exact':unknown==0,
            'estimate_caveat':('exact_census' if unknown==0 else
                'model_based_random_order_estimate_not_a_certification_or_confidence_bound')}


def _retention_settings(args):
    budget=int(getattr(args,'retention_sample_cases',128))
    full_every=int(getattr(args,'retention_full_every',10))
    known_limit=int(getattr(args,'retention_known_failure_cases',32))
    if budget<1 or full_every<0 or known_limit<0:
        raise ValueError('LADDER_BAD_RETENTION_SAMPLING_POLICY')
    return budget,full_every,known_limit


def _periodic_retention_due(state,args):
    _,interval,_=_retention_settings(args)
    # Only completed provisional advancements count. A failed gate repeats
    # this same census, not a fresh milestone or an invented certificate.
    return bool(interval and (len(state.get('provisional',[]))+1)%interval==0)

def _gate_full_interval(args):
    interval=int(getattr(args,'gate_full_every',10))
    if interval<0:raise ValueError('LADDER_BAD_GATE_FULL_INTERVAL')
    return interval


def _advancement_policy(args):
    _gate_full_interval(args)
    mode=getattr(args,'advance_mode','exact')
    threshold=float(getattr(args,'advance_accuracy',.95))
    sample_count=int(getattr(args,'advance_probes',2))
    if mode not in ('exact','sampled') or not math.isfinite(threshold) or not 0<threshold<=1 or sample_count<1:
        raise ValueError('LADDER_BAD_ADVANCEMENT_POLICY')
    return {'mode':mode,'accuracy':threshold,'probes':sample_count}


def _record_advancement_policy(state,args):
    policy=_advancement_policy(args)
    state.setdefault('provisional',[])
    old=state.get('advancement_policy',{'mode':'exact','accuracy':.95,'probes':2})
    if old!=policy:
        state.setdefault('advancement_policy_history',[]).append({
            'from':old,'to':policy,'steps_by_rung':copy.deepcopy(state['steps_by_rung'])})
    state['advancement_policy']=policy
    if old['mode']!=policy['mode']:
        prior_target=None
        old_index=state.get('radius_index',-1)
        if (0<=state.get('rung',-1)<=state['config']['max_rung'] and
                0<=old_index<len(state['config']['radii'])):
            prior_target=(state['rung'],state['config']['radii'][old_index])
        target=_first_unfinished_target(state['config'],state['certificates'],
            preferred=prior_target if state['status'] not in ('CERTIFIED_ALL','PROVISIONAL_ALL') else None,
            provisional=state.get('provisional',[]) if policy['mode']=='sampled' else [],
            radius_first=_schedule_radius_first(state,policy['mode']))
        if target is None:
            state['status']='PROVISIONAL_ALL' if policy['mode']=='sampled' else 'CERTIFIED_ALL'
        else:
            state['rung'],radius=target
            state['radius_index']=state['config']['radii'].index(radius)
            if state['status'] in ('PROVISIONAL_ALL','CERTIFIED_ALL'):
                state['status']='READY'
    return policy


def _schedule_radius_first(state, advancement_mode='sampled'):
    """Backward compatible until a curriculum is explicitly selected."""
    selected=state.get('curriculum',{}).get('mode')
    if selected is None:
        return advancement_mode=='sampled'
    if selected not in ('radius-major','rung-major'):
        raise RuntimeError('LADDER_UNRECOGNIZED_CURRICULUM')
    return selected=='radius-major'


def _record_curriculum(state,args,advancement=None):
    """Change *scheduling*, never model weights, optimizer, or certificates.

    Rung-major acquires all requested signed residual radii at the current
    depth before visiting deeper expressions. An existing deeper unfinished
    target is deferred; it is not silently certified or forgotten.
    """
    mode=getattr(args,'curriculum',None)
    if mode is None:
        mode=state.get('curriculum',{}).get('mode')
    if mode is None:
        mode=('radius-major' if (advancement or state.get('advancement_policy',{})).get('mode','exact')=='sampled'
              else 'rung-major')
    if mode not in ('radius-major','rung-major'):
        raise ValueError('LADDER_BAD_CURRICULUM')
    old=state.get('curriculum',{}).get('mode')
    explicitly_selected=getattr(args,'curriculum',None) is not None
    state['curriculum']={'mode':mode}
    # An old campaign whose user did not select a new scheduler keeps its
    # active unfinished target, matching the original resume contract.
    if old==mode or (old is None and not explicitly_selected):
        return state['curriculum']
    cfg=state['config']
    previous=None
    index=state.get('radius_index',0)
    if 0<=state.get('rung',-1)<=cfg['max_rung'] and 0<=index<len(cfg['radii']):
        previous=(state['rung'],cfg['radii'][index])
    advancement=advancement or state.get('advancement_policy',{'mode':'exact'})
    target=_first_unfinished_target(cfg,state.get('certificates',[]),
        provisional=(state.get('provisional',[]) if advancement['mode']=='sampled' else []),
        radius_first=mode=='radius-major')
    if target is None:
        state['status']='PROVISIONAL_ALL' if advancement['mode']=='sampled' else 'CERTIFIED_ALL'
    else:
        state['rung'],radius=target
        state['radius_index']=cfg['radii'].index(radius)
        spent=state['steps_by_rung'][str(state['rung'])]
        allocated=state['budget_by_rung'][str(state['rung'])]
        state['status']='BUDGET_EXHAUSTED' if spent>=allocated else 'READY'
    event={'from':old,'to':mode,'target_before':list(previous) if previous else None,
           'target_after':list(target) if target else None,
           'deferred_unfinished_target':list(previous) if previous and previous!=target else None,
           'provisional_preserved':len(state.get('provisional',[])),
           'certificates_preserved':len(state.get('certificates',[])),
           'steps_by_rung':copy.deepcopy(state['steps_by_rung'])}
    state.setdefault('curriculum_history',[]).append(event)
    print('QWEN_LADDER_CURRICULUM='+json.dumps(event,sort_keys=True),flush=True)
    return state['curriculum']


def _snapshot_curriculum_transition(out,state,args):
    """One-way, immutable safety checkpoint before changing a trained path.

    Saves the *existing* working.pt (LoRA, optimizer, campaign record) without
    modifying it. Never overwrite an earlier migration snapshot on resume.
    """
    selected=getattr(args,'curriculum',None)
    prior=state.get('curriculum',{}).get('mode')
    if selected is None or selected==prior:
        return None
    if not (state.get('attempts',0) or state.get('certificates') or
            state.get('provisional') or any(state.get('steps_by_rung',{}).values())):
        return None
    source=Path(out)/'working.pt'
    if not source.is_file():
        raise RuntimeError('LADDER_CURRICULUM_SNAPSHOT_MISSING_SOURCE')
    index=state.get('radius_index',0)
    radii=state['config']['radii']
    radius=radii[index] if 0<=index<len(radii) else 0
    target=(Path(out)/'curriculum_snapshots'/
            f'before_{selected}_T{state["rung"]:02d}_R{radius:04d}.pt')
    target.parent.mkdir(parents=True,exist_ok=True)
    if not target.exists():
        temp=target.with_suffix('.pt.tmp')
        shutil.copy2(source,temp)
        os.replace(temp,target)
    state.setdefault('curriculum_snapshots',[])
    location=str(target.relative_to(out))
    if location not in state['curriculum_snapshots']:
        state['curriculum_snapshots'].append(location)
    print('QWEN_LADDER_CURRICULUM_SNAPSHOT='+json.dumps({
        'path':location,'mode':selected,'prior_mode':prior,
        'source':'working.pt','existing_weights_preserved':True},sort_keys=True),flush=True)
    return target


def _balanced_repair_centers(failures,center_lookup,rotation=0):
    """Round-robin failure centers by depth, radius and exact signed residual.

    This prevents a large zero-residual deck or the first damaged certificate
    from consuming all small center-first repair batches. Actual failed rows
    precede neighbors; this only changes which failure gets attention first.
    """
    grouped={}
    seen=set()
    for f in failures:
        key=(f['rung'],f['radius'],f['id'])
        if key in seen or key not in center_lookup:continue
        seen.add(key)
        row=center_lookup[key]
        grouped.setdefault((key[0],key[1],row['residual']),[]).append(key)
    keys=sorted(grouped)
    if keys:
        offset=rotation%len(keys)
        keys=keys[offset:]+keys[:offset]
    ordered=[]
    while keys:
        following=[]
        for key in keys:
            group=grouped[key]
            ordered.append(group.pop(0))
            if group:following.append(key)
        keys=following
    return ordered


def _sampled_progress_grade(bb,tok,rows,args,rung,radius,attempt,tag):
    policy=_advancement_policy(args)
    groups=_sampled_progress_rows(rows,getattr(args,'probe_cases',128),
        policy['probes'],(rung,radius,attempt))
    results=[]
    for index,group in enumerate(groups):
        audited=_audit(bb,tok,group,args.device,args.max_tokens,args.max_new_tokens,
            args.eval_batch,fail_limit=args.fail_limit,
            min_passed=_required_retained(len(group),policy['accuracy']),
            population_total=len(rows),
            progress_every=getattr(args,'audit_progress_every',32),
            progress_tag=f'{tag}_sample_{index+1}',rung=rung,radius=radius)
        results.append(audited)
    passed=sum(a['passed'] for a in results)
    tested=sum(a['tested'] for a in results)
    total=sum(a['total'] for a in results)
    failures=[f for a in results for f in a['failures']]
    ready=all(a['passed']>=_required_retained(a['total'],policy['accuracy'])
              and a['tested']==a['total'] for a in results)
    per_sample=[{'passed':a['passed'],'tested':a['tested'],'total':a['total'],
                 'tripwire_tripped':a.get('tripwire_tripped',False),
                 'allowed_errors':a['total']-_required_retained(a['total'],policy['accuracy']),
                 'error_projection':a.get('error_projection',_project_errors(
                     a['tested'],a['tested']-a['passed'],len(rows)))} for a in results]
    # Different samples may overlap on a small deck; keep their projections
    # distinct instead of falsely treating duplicates as independent cases.
    estimate=sum(a['error_projection']['estimated_total_errors'] for a in per_sample)/len(per_sample)
    grade={'all_pass':bool(ready),'tested':tested,'passed':passed,'total':total,
           'failures':failures[:args.fail_limit],
           'truncated':any(a.get('truncated',False) for a in results),
           'tripwire_tripped':any(a.get('tripwire_tripped',False) for a in results),
           'sample_only':True,'samples':len(results),'readiness':ready,
           'accuracy':passed/tested if tested else 0,
           'estimated_current_deck_errors':round(estimate,2),
           'per_sample':per_sample}
    return grade


def _audit(bb,tok,rows,device,max_tokens,max_new_tokens,batch_size,*,fail_limit=64,
           stop_at_first=False,progress_every=0,progress_tag='full',rung=None,
           radius=None,record_pass_ids=False,min_passed=None,population_total=None):
    """Exact LM-head grading. Capped failure reporting is not certification.

    progress_every logs *after* each completed evaluation batch, not a timer.
    """
    if min_passed is not None and not 0<=min_passed<=len(rows):
        raise ValueError('LADDER_INVALID_RETENTION_THRESHOLD')
    if population_total is None:population_total=len(rows)
    if population_total<len(rows):raise ValueError('LADDER_BAD_GATE_POPULATION')
    count=0;passed=0;fail=[];errors_seen=0;pass_ids=[]
    rng=_rng('AUDIT_FAILURE_RESERVOIR',len(rows),rows[0]['id'] if rows else '')
    next_progress=max(1,progress_every)
    for start in range(0,len(rows),batch_size):
        subset=rows[start:start+batch_size]
        generated=_greedy(bb,tok,[r['source'] for r in subset],device,max_tokens,max_new_tokens)
        for row,response in zip(subset,generated):
            count+=1;ok,parsed=correctness(row['source'],row['target'],response)
            if ok:
                passed+=1
                if record_pass_ids:pass_ids.append(row['id'])
            else:
                errors_seen+=1
                issue={'id':row['id'],'source':row['source'],'expected':row['target'],
                       'emitted':response,'parsed':parsed}
                if len(fail)<fail_limit:fail.append(issue)
                elif fail_limit>0:
                    index=rng.randrange(errors_seen)
                    if index<fail_limit:fail[index]=issue
            if min_passed is not None and passed+len(rows)-count<min_passed:
                # This deck can no longer reach its exact retention threshold.
                # Capture real errors without wasting the remainder of a long
                # greedy LM-head audit; never claim a measured full accuracy.
                projection=_project_errors(count,errors_seen,population_total)
                allowed=len(rows)-min_passed
                print('QWEN_LADDER_GATE_TRIPWIRE='+json.dumps({
                    'phase':progress_tag,'rung':rung,'radius':radius,
                    'tested':count,'sample_total':len(rows),'errors':errors_seen,
                    'allowed_errors':allowed,'error_projection':projection,
                    'gate_failed':True,'audit_complete':False},sort_keys=True),flush=True)
                result={'all_pass':False,'tested':count,'passed':passed,'total':len(rows),
                        'failures':fail,'truncated':True,'tripwire_tripped':True,
                        'allowed_errors':allowed,'error_projection':projection,
                        'retention_threshold_impossible':True}
                if record_pass_ids:result['passed_ids']=pass_ids
                return result
            if not ok and stop_at_first:
                result={'all_pass':False,'tested':count,'passed':passed,'total':len(rows),
                        'failures':fail,'truncated':True}
                if record_pass_ids:result['passed_ids']=pass_ids
                return result
        if progress_every and (count >= next_progress or count==len(rows)):
            print('QWEN_LADDER_AUDIT_PROGRESS='+json.dumps({
                'phase':progress_tag,'rung':rung,'radius':radius,
                'tested':count,'total':len(rows),'passed':passed,
                'failed':count-passed},sort_keys=True),flush=True)
            next_progress=count+progress_every
    result={'all_pass':passed==len(rows),'tested':count,'passed':passed,'total':len(rows),
            'failures':fail,'truncated':False,'tripwire_tripped':False,
            'error_projection':_project_errors(count,errors_seen,population_total)}
    if record_pass_ids:result['passed_ids']=pass_ids
    return result


def _train_step(bb,tok,optimizer,rows,args):
    import torch
    from tools import nanojev_qwen_zero_line_backbone as zero
    optimizer.zero_grad(set_to_none=True)
    amp=zero.resolve_amp_dtype(args.device,'auto') if str(args.device).startswith('cuda') else 'cpu'
    with torch.autocast('cuda' if str(args.device).startswith('cuda') else 'cpu',
                        enabled=str(args.device).startswith('cuda'),
                        dtype=torch.bfloat16 if amp=='bfloat16' else torch.float16):
        loss=_batch_loss(bb,tok,rows,args.device,args.max_seq)
    if not bool(torch.isfinite(loss)):raise RuntimeError('LADDER_NONFINITE_TRAIN_LOSS')
    loss.backward()
    ps=[p for _,p in zero.lora_named_parameters(bb)]
    if not ps or any(p.grad is None or not bool(torch.isfinite(p.grad).all()) for p in ps):
        raise RuntimeError('LADDER_LORA_MISSING_OR_NONFINITE_GRADIENT')
    torch.nn.utils.clip_grad_norm_(ps,1.)
    optimizer.step()
    return float(loss.detach())


def _fresh_parent(args):
    from tools import nanojev_qwen_numeric_dual_cutover as parent
    from tools import nanojev_qwen_zero_line_backbone as zero
    config,ck=parent.load_parent(args)
    model,tok,probe,sources,frozen,dirs=parent._load_qwen(stage1,args,config,ck)
    bb=model.backbone
    bb.eval();bb.requires_grad_(False);model.head.requires_grad_(False)
    return bb,tok,sources,frozen,dirs,zero


def _new_state(cfg,source_hash,frozen_hash):
    return {'schema':SCHEMA,'config':cfg,'config_hash':_hash(cfg),'source_sha256':source_hash,
            'frozen_sha256':frozen_hash,'status':'READY','rung':0,'radius_index':0,
            'steps_by_rung':{str(i):0 for i in range(cfg['max_rung']+1)},
            'budget_by_rung':{str(i):cfg['steps_per_rung'] for i in range(cfg['max_rung']+1)},
            'certificates':[],'last_audit':None,'attempts':0}


def _persist(out,bb,zero,state,optimizer=None):
    # Checkpoint and JSON are both durable; checkpoint contains state so an
    # interrupted JSON write can be recovered from the newer checkpoint.
    obj={'schema':SCHEMA,'state':state,'config':state.get('config'),
         'lora':zero.lora_state(bb),
         'optimizer':optimizer.state_dict() if optimizer else None}
    _atomic_torch(out/'working.pt',obj)
    _atomic_json(out/'progress.json',state)


def _first_unfinished_target(cfg,certificates,preferred=None,*,provisional=(),radius_first=False):
    """Find missing certifications in the *requested* schedule.

    Historical certificates (including radii omitted from the new schedule)
    remain retained/protected; they simply need not be acquired again.
    """
    completed={(c['rung'],c['radius']) for c in [*certificates,*provisional]}
    available={(r,radius) for r in range(cfg['max_rung']+1) for radius in cfg['radii']}
    if preferred in available and preferred not in completed:
        return preferred
    targets=([(rung,radius) for radius in cfg['radii']
              for rung in range(cfg['max_rung']+1)] if radius_first else
             [(rung,radius) for rung in range(cfg['max_rung']+1)
              for radius in cfg['radii']])
    return next((target for target in targets if target not in completed),None)


def _migrate_radius_schedule(state,new_cfg):
    """Modify scheduling only, retaining LoRA, optimizer, budget, and history.

    The currently unfinished target keeps running if it still belongs to the
    requested schedule. Once it certifies, find the next missing target. This
    prevents new intermediate radii from discarding provisional T01 progress.
    """
    prior=state['config']
    if (not isinstance(prior.get('radii'),list) or
            any(k!='radii' and prior.get(k)!=new_cfg.get(k)
                for k in set(prior)|set(new_cfg))):
        raise RuntimeError('LADDER_RESUME_CONFIG_DRIFT')
    if any('deck_config' not in c for c in state.get('certificates',[])):
        raise RuntimeError('LADDER_SCHEDULE_MIGRATION_NEEDS_CERTIFICATE_DECK_CONFIG')
    old_target=None
    old_index=state.get('radius_index',0)
    if 0<=old_index<len(prior['radii']) and 0<=state['rung']<=prior['max_rung']:
        old_target=(state['rung'],prior['radii'][old_index])
    mode=state.get('advancement_policy',{}).get('mode','exact')
    preferred=(old_target if state.get('status') not in ('CERTIFIED_ALL','PROVISIONAL_ALL')
               and state.get('curriculum',{}).get('mode')!='rung-major' else None)
    next_target=_first_unfinished_target(new_cfg,state.get('certificates',[]),preferred,
        provisional=state.get('provisional',[]) if mode=='sampled' else [],
        radius_first=_schedule_radius_first(state,mode))
    previous_status=state.get('status')
    before_hash=state['config_hash']
    state['config']=copy.deepcopy(new_cfg)
    state['config_hash']=_hash(new_cfg)
    if next_target is None:
        state['status']='PROVISIONAL_ALL' if mode=='sampled' else 'CERTIFIED_ALL'
    else:
        rung,radius=next_target
        state['rung']=rung
        state['radius_index']=new_cfg['radii'].index(radius)
        # Per-rung budget is never reset by a schedule edit.
        if state['steps_by_rung'][str(rung)]>=state['budget_by_rung'][str(rung)]:
            state['status']='BUDGET_EXHAUSTED'
        elif previous_status in ('CERTIFIED_ALL','PROVISIONAL_ALL','BUDGET_EXHAUSTED',
                                 'BLOCKED_UNDERSAMPLED_DECK'):
            state['status']='READY'
    history=state.setdefault('radius_schedule_history',[])
    history.append({'from':list(prior['radii']),
                    'to':list(new_cfg['radii']),
                    'old_config_sha256':before_hash,
                    'new_config_sha256':state['config_hash'],
                    'target_before':list(old_target) if old_target is not None else None,
                    'target_after':list(next_target) if next_target is not None else None,
                    'steps_by_rung':copy.deepcopy(state['steps_by_rung']),
                    'certificates_preserved':len(state.get('certificates',[]))})
    return history[-1]


def _load_resume(out,cfg,bb,zero,source_hash,frozen_hash):
    import torch
    obj=torch.load(out/'working.pt',map_location='cpu',weights_only=True)
    state=obj.get('state')
    if (obj.get('schema')!=SCHEMA or not isinstance(state,dict) or
            not isinstance(state.get('config'),dict) or
            state.get('config_hash')!=_hash(state['config'])):
        raise RuntimeError('LADDER_RESUME_CONFIG_DRIFT')
    if obj.get('config') is not None and obj['config']!=state['config']:
        raise RuntimeError('LADDER_RESUME_CONFIG_MISMATCH')
    if state['source_sha256']!=source_hash or state['frozen_sha256']!=frozen_hash:
        raise RuntimeError('LADDER_RESUME_MODEL_DRIFT')
    changed=state['config']!=cfg
    if changed:
        info=_migrate_radius_schedule(state,cfg)
    zero.restore_lora(bb,obj['lora'])
    if changed:
        # Rewrite checkpoint metadata atomically without touching either the
        # existing LoRA weights OR the exact AdamW optimizer state. This also
        # persists schedule changes even when a campaign has already halted.
        obj['state']=state
        obj['config']=cfg
        _atomic_torch(out/'working.pt',obj)
        _atomic_json(out/'progress.json',state)
        print('QWEN_LADDER_RADIUS_SCHEDULE_CHANGED='+json.dumps(info,sort_keys=True),flush=True)
    return state,obj.get('optimizer')


def _case_bank(cfg,rung,radius):
    return deck(rung,radius,cfg['operand_limit'],cfg['per_cell'],cfg['seed'],cfg['t00_mode'])


def _certified_bank(cfg,certs):
    # Keep each certificate's original numerical scope on later expansion.
    result=[]
    for c in certs:
        oldcfg=c.get('deck_config',cfg)
        result.append((c,_case_bank(oldcfg,c['rung'],c['radius'])[0]))
    return result


def _adaptive_policy(args):
    """Mutable experimental controller policy, independent of deck identity.

    Fixed mode remains the reference experiment. Adaptive values and decisions
    are stored in progress.json; changes to them do not rewrite certificates.
    """
    policy={'enabled':bool(getattr(args,'adaptive_repair',False)),
        'check_min':int(getattr(args,'adaptive_check_min',5)),
        'check_max':int(getattr(args,'adaptive_check_max',100)),
        'repair_min':int(getattr(args,'adaptive_repair_min',2)),
        'repair_max':int(getattr(args,'adaptive_repair_max',20)),
        'harvest_cases':int(getattr(args,'adaptive_harvest_cases',128)),
        'failure_target':int(getattr(args,'adaptive_failure_target',8)),
        'full_steps':int(getattr(args,'adaptive_full_steps',1000))}
    if (policy['check_min']<1 or policy['check_max']<policy['check_min'] or
            policy['repair_min']<1 or policy['repair_max']<policy['repair_min'] or
            policy['harvest_cases']<1 or policy['failure_target']<1 or
            policy['full_steps']<0):
        raise ValueError('LADDER_BAD_ADAPTIVE_CONTROLLER_POLICY')
    return policy


def _adaptive_control(state,args):
    """Opt-in policy survives checkpoint restart and is adjustable in place."""
    policy=_adaptive_policy(args)
    if not policy['enabled']:
        return None
    control=state.setdefault('adaptive_controller',{
        'policy':copy.deepcopy(policy),'targets':{},'decisions':[],
        'last_full_steps':sum(state['steps_by_rung'].values())})
    if control['policy']!=policy:
        control.setdefault('policy_history',[]).append({
            'from':control['policy'],'to':policy,
            'at_steps':sum(state['steps_by_rung'].values())})
        control['policy']=copy.deepcopy(policy)
    return control


def _adaptive_target(control,policy,rung,radius,args):
    key=f'{rung}:{radius}'
    target=control['targets'].setdefault(key,{
        'check_steps':max(policy['check_min'],min(policy['check_max'],int(args.repair_steps))),
        'repair_steps':max(policy['repair_min'],min(policy['repair_max'],
            int(getattr(args,'local_repair_steps',10)))),
        'checks':0,'repairs':0,'last_gain':None,'last_retention_ok':None,
        'harvest_cases':policy['harvest_cases'],'last_harvest_yield':None})
    target['check_steps']=max(policy['check_min'],min(policy['check_max'],target['check_steps']))
    target['repair_steps']=max(policy['repair_min'],min(policy['repair_max'],target['repair_steps']))
    target['harvest_cases']=max(8,min(policy['harvest_cases'],target.get('harvest_cases',policy['harvest_cases'])))
    return target


def _adaptive_decide(target,policy,before,after,guard_before,guard_after,repair_steps,
                     *,elapsed_seconds=None):
    """Feedback update: shorten checks near the frontier or on interference.

    Grow check batches only while new-rung competence is low and historical
    competence is stable. Increase local repair effort when damage persists;
    shrink it when fewer steps suffice. This is a bounded heuristic controller,
    not a claim of optimal reinforcement learning.
    """
    previous=target['check_steps'];previous_repair=target['repair_steps']
    gain=after-before
    retention_ok=bool(guard_after.get('ok',True))
    if not retention_ok:
        target['check_steps']=max(policy['check_min'],math.ceil(previous/2))
        target['repair_steps']=min(policy['repair_max'],max(previous_repair+1,
                                                        math.ceil(previous_repair*1.5)))
        reason='retention_below_floor'
    elif before>=0.85 or after>=0.85:
        # Near an advancement boundary, frequent checks avoid overshooting.
        target['check_steps']=max(policy['check_min'],math.ceil(previous/2))
        if repair_steps and guard_before and not guard_before.get('ok',True):
            target['repair_steps']=max(policy['repair_min'],previous_repair-1)
        reason='near_acquisition_frontier'
    else:
        target['check_steps']=min(policy['check_max'],max(previous+1,math.ceil(previous*1.4)))
        if repair_steps and retention_ok:
            target['repair_steps']=max(policy['repair_min'],previous_repair-1)
        reason='stable_retention_extend_acquisition'
    target['checks']+=1
    target['repairs']+=int(bool(repair_steps))
    target['last_gain']=round(gain,6)
    target['last_retention_ok']=retention_ok
    return {'reason':reason,'acquisition_accuracy_before':round(before,6),
            'acquisition_accuracy_after':round(after,6),
            'acquisition_gain':round(gain,6),'retention_ok':retention_ok,
            'check_steps_before':previous,'check_steps_next':target['check_steps'],
            'repair_steps_before':previous_repair,'repair_steps_next':target['repair_steps'],
            'repair_steps_used':repair_steps,
            'elapsed_seconds':round(elapsed_seconds,3) if elapsed_seconds is not None else None}


def _adaptive_harvest(bb,tok,cfg,histories,guard,args,state,attempt,*,target=None):
    """After a tripped gate, gather fault *locations* without another full census.

    Small decks are completely inspected. Large decks receive rotating,
    magnitude/sign-stratified random samples, stopped at a discovery budget.
    The samples do NOT change the original gate accuracy or certification.
    """
    policy=_adaptive_policy(args)
    if not guard.get('failures') or guard.get('ok'):
        return {'attempted':False,'cases_tested':0,'new_centers':0,'reason':'no_failed_gate'}
    bank={(c['rung'],c['radius']):(c,rows) for c,rows in _certified_bank(cfg,histories)}
    observed={(f['rung'],f['radius'],f['id']) for f in guard['failures']}
    max_budget=target['harvest_cases'] if target is not None else policy['harvest_cases']
    remaining_budget=max_budget
    new=[];examined=0;start=time.perf_counter()
    for entry in guard['certificates']:
        if entry['ok'] or remaining_budget<=0:continue
        ident=(entry['rung'],entry['radius'])
        if ident not in bank:raise RuntimeError('LADDER_ADAPTIVE_MISSING_HISTORICAL_DECK')
        cert,rows=bank[ident]
        if deck_fingerprint(rows)!=cert['deck_sha256']:
            raise RuntimeError('LADDER_CERTIFIED_DECK_DRIFT')
        known_ids={f[2] for f in observed if f[:2]==ident}
        # When the deck is small, harvest every remaining case to discover
        # all damaged centers; for large decks rotate a stratified subset.
        small_deck=len(rows)<=32
        if small_deck:
            candidates=[r for r in rows if r['id'] not in known_ids]
        else:
            selected=_retention_sample_rows(rows,
                min(len(rows),remaining_budget),cert['deck_sha256'],
                attempt+int(state.get('attempts',0))+100000)
            candidates=[r for r in selected if r['id'] not in known_ids]
        if not small_deck and len(candidates)>remaining_budget:
            candidates=candidates[:remaining_budget]
        # Audit in batches. Stop looking once we have enough *distinct*
        # repair centers, or when two consecutive batches produce none.
        newly_found=0;empty_batches=0
        for offset in range(0,len(candidates),max(1,args.eval_batch)):
            batch=candidates[offset:offset+max(1,args.eval_batch)]
            result=_audit(bb,tok,batch,args.device,args.max_tokens,
                args.max_new_tokens,args.eval_batch,
                fail_limit=len(batch),progress_every=0,
                progress_tag='adaptive_harvest',rung=ident[0],radius=ident[1])
            examined+=result['tested'];remaining_budget-=result['tested']
            got=0
            for failure in result['failures']:
                key=(*ident,failure['id'])
                if key not in observed:
                    observed.add(key)
                    new.append({'rung':ident[0],'radius':ident[1],**failure})
                    got+=1;newly_found+=1
            empty_batches=0 if got else empty_batches+1
            if (not small_deck and (newly_found>=policy['failure_target'] or
                empty_batches>=2 or remaining_budget<=0)):
                break
    if new:
        guard['failures'].extend(new)
        known_limit=_retention_settings(args)[2]
        if known_limit:
            known_bank=state.setdefault('retention_known_failures',{})
            for ident,(cert,rows) in bank.items():
                key=f'{ident[0]}:{ident[1]}:{cert["deck_sha256"]}'
                prior=list(known_bank.get(key,[]))
                for failure in new:
                    if (failure['rung'],failure['radius'])!=ident:continue
                    if failure['id'] in prior:prior.remove(failure['id'])
                    prior.append(failure['id'])
                known_bank[key]=prior[-known_limit:]
    if target is not None:
        # More failed locations per test warrant broader harvesting next time;
        # unproductive scans reduce the search budget. Never zero it out.
        if not new:
            target['harvest_cases']=max(8,math.ceil(max_budget/2))
        elif len(new)>=policy['failure_target']:
            target['harvest_cases']=min(policy['harvest_cases'],max_budget*2)
        target['last_harvest_yield']=round(len(new)/max(1,examined),6)
    info={'attempted':True,'cases_tested':examined,'new_centers':len(new),
          'known_centers':len(observed),'budget':max_budget,
          'next_budget':target['harvest_cases'] if target is not None else max_budget,
          'elapsed_seconds':round(time.perf_counter()-start,3),
          'purpose':'repair_discovery_not_accuracy_estimation'}
    print('QWEN_LADDER_ADAPTIVE_HARVEST='+json.dumps(info,sort_keys=True),flush=True)
    return info


def _learning_policy(args):
    """Separate recorded policy from the immutable acquisition/deck config.

    Keeping it outside the config hash permits an explicit, auditable policy
    migration of an existing zero-fork working.pt without rewriting any prior
    certificate or altering its deck lineage.
    """
    floor=float(getattr(args,'retention_floor',0.95))
    local_radius=int(getattr(args,'local_radius',4))
    local_steps=int(getattr(args,'local_repair_steps',10))
    if not 0 < floor <= 1 or not math.isfinite(floor):
        raise ValueError('LADDER_RETENTION_FLOOR_MUST_BE_IN_0_1')
    if local_radius<1 or local_steps<1:
        raise ValueError('LADDER_BAD_LOCAL_REPAIR_PARAMETERS')
    return {'strategy':'commit_then_local_radius_repair_v1',
            'retention_floor':floor,'local_radius':local_radius,
            'local_repair_steps':local_steps}


def _record_learning_policy(state,args):
    policy=_learning_policy(args)
    prior=state.get('learning_policy')
    if prior is not None and prior!=policy:
        raise RuntimeError('LADDER_LEARNING_POLICY_DRIFT')
    if prior is None:
        # An old campaign may contain provisional steps from the former
        # rollback-on-regression policy; preserve that history and record the
        # one-way switch instead of silently claiming those steps were kept.
        state['learning_policy']=policy
        state['learning_policy_migration']={
            'from':'legacy_rollback_if_retention_regressed',
            'to':policy['strategy'],
            'at_steps_by_rung':copy.deepcopy(state['steps_by_rung'])}
    return policy


def _required_retained(total,floor):
    """For 2,049 T00/R0 cases at 95%, require >=1,947 exact packets."""
    return math.ceil(total*floor-1e-10)


def _audit_retention(bb,tok,cfg,certs,args,*,sample_cases=None,attempt=0,state=None,
                     force_complete=False):
    """Protect previous decks using either a full census or a rotating sample.

    The random sample alone determines the 95% accuracy estimate. A small
    durable, targeted regression set is checked separately and never added to
    the sample's denominator. Both sources contribute real failures to local
    repair. Exact certificate decks and immutable weights are not modified.
    """
    floor=_learning_policy(args)['retention_floor']
    _,_,known_limit=_retention_settings(args)
    known_bank=(state.setdefault('retention_known_failures',{}) if state is not None else {})
    details=[];failed=[];passed=0;tested=0;total=0
    for cert,rows in _certified_bank(cfg,certs):
        if deck_fingerprint(rows)!=cert['deck_sha256']:
            raise RuntimeError('LADDER_CERTIFIED_DECK_DRIFT')
        # Original certificates always supply their OWN immutable deck scope.
        sample_only=sample_cases is not None and len(rows)>sample_cases
        if sample_only:
            ordered=_retention_sample_rows(rows,sample_cases,cert['deck_sha256'],attempt)
        else:
            ordered=list(rows)
            _rng('LADDER_RETENTION_SPREAD',cert['deck_sha256']).shuffle(ordered)
        key=f"{cert['rung']}:{cert['radius']}:{cert['deck_sha256']}"
        old_known=list(known_bank.get(key,[])) if known_limit else []
        if len(old_known)>known_limit:old_known=old_known[-known_limit:]
        by_id={r['id']:r for r in rows}
        if any(identifier not in by_id for identifier in old_known):
            raise RuntimeError('LADDER_RETENTION_FAILURE_BANK_DECK_DRIFT')
        audited_ids={r['id'] for r in ordered}
        # Historical failures are replayed *outside* the statistical sample.
        # They can trigger repair without biasing the estimate used by the gate.
        regression=[by_id[identifier] for identifier in old_known
                    if identifier not in audited_ids] if sample_only else []
        required=_required_retained(len(ordered),floor)
        print('QWEN_LADDER_RETENTION_BEGIN='+json.dumps({
            'rung':cert['rung'],'radius':cert['radius'],'total':len(rows),
            'tested_target':len(ordered),'sample_only':sample_only,
            'known_failure_target':len(regression),'minimum_accuracy':floor},sort_keys=True),flush=True)
        audit=_audit(bb,tok,ordered,args.device,args.max_tokens,args.max_new_tokens,
                args.eval_batch,fail_limit=getattr(args,'fail_limit',64),
                stop_at_first=False,min_passed=None if force_complete else required,
                population_total=len(rows),
                progress_every=5*getattr(args,'audit_progress_every',32),
                progress_tag='retention',rung=cert['rung'],radius=cert['radius'])
        regression_audit=None
        if regression:
            regression_audit=_audit(bb,tok,regression,args.device,args.max_tokens,
                args.max_new_tokens,args.eval_batch,fail_limit=max(getattr(args,'fail_limit',64),
                known_limit),progress_every=0,
                progress_tag='known_failure',rung=cert['rung'],radius=cert['radius'])
        observed_failures=list(audit['failures'])+(regression_audit['failures'] if regression_audit else [])
        if state is not None and known_limit:
            for failure in observed_failures:
                identifier=failure['id']
                if identifier not in by_id:raise RuntimeError('LADDER_RETENTION_UNKNOWN_FAILED_CASE')
                if identifier in old_known:old_known.remove(identifier)
                old_known.append(identifier)
            known_bank[key]=old_known[-known_limit:]
        ok=audit['tested']==len(ordered) and audit['passed']>=required
        entry={'rung':cert['rung'],'radius':cert['radius'],
               'passed':audit['passed'],'tested':audit['tested'],
               'total':len(ordered),'deck_total':len(rows),
               'sample_only':sample_only,'required':required,
               'complete':audit['tested']==len(ordered),
               'accuracy':(audit['passed']/len(ordered) if audit['tested']==len(ordered) else None),
               'accuracy_upper_bound':(audit['passed']+len(ordered)-audit['tested'])/len(ordered),
               'tripwire_tripped':audit.get('tripwire_tripped',False),
               'allowed_errors':len(ordered)-required,
               'error_projection':audit.get('error_projection',_project_errors(
                   audit['tested'],audit['tested']-audit['passed'],len(rows))),
               'known_failure_tested':len(regression),
               'known_failure_failed':len(regression_audit['failures']) if regression_audit else 0,
               'known_failure_cached':len(known_bank.get(key,[])),
               'ok':ok,'failures':observed_failures}
        details.append(entry)
        # Never duplicate failures detected in both the sample and regression.
        failed.extend({'rung':cert['rung'],'radius':cert['radius'],**failure}
                      for failure in observed_failures)
        passed+=audit['passed'];tested+=audit['tested'];total+=len(ordered)
        print('QWEN_LADDER_RETENTION='+json.dumps({k:v for k,v in entry.items()
              if k!='failures'},sort_keys=True),flush=True)
    return {'ok':all(g['ok'] for g in details),'passed':passed,'tested':tested,'total':total,
            'floor':floor,'certificates':details,'failures':failed}

def _local_radius_rows(cfg,certs,guard,local_radius):
    """Generate supervised neighbors of ACTUAL failed certificate problems.

    For a failure (a,b), perturb adjacent leaves by (+d,-d). This preserves
    the exact signed residual/target and explores a small operand-radius
    neighborhood. For deeper chains, perturb any adjacent leaf pair while
    preserving the original operand and intermediate-sum bounds. No oracle is
    used at inference; these are labeled training examples only.
    """
    bank={(c['rung'],c['radius'],r['id']):r
          for c,rows in _certified_bank(cfg,certs) for r in rows}
    result={};centers=0
    increments={0}
    for d in range(1,local_radius+1):
        increments.update((-d,d))
    for failure in guard.get('failures',[]):
        center=bank.get((failure['rung'],failure['radius'],failure['id']))
        if center is None:raise RuntimeError('LADDER_LOCAL_REPAIR_UNKNOWN_FAILED_CASE')
        centers+=1
        leaves=tuple(center['leaves']);rung=center['rung']
        for i in range(len(leaves)-1):
            for d in sorted(increments):
                new=list(leaves)
                new[i]+=d;new[i+1]-=d
                if any(abs(v)>cfg['operand_limit'] for v in new):continue
                partial=0;valid=True
                for v in new:
                    partial+=v
                    if abs(partial)>cfg['operand_limit']:
                        valid=False;break
                if not valid:continue
                case=_case(rung,tuple(new),center['id']+f'-LOCAL-{i}-{d:+d}')
                if case['residual']!=center['residual']:
                    raise RuntimeError('LADDER_LOCAL_REPAIR_RESIDUAL_DRIFT')
                result[(case['source'],case['target'])]=case
    return list(result.values()),centers


def _train_local_radius(bb,tok,optimizer,guard,certs,cfg,cases,
                        args,rng,state,out,zero,rung,remaining,*,step_cap=None,center_first=False):
    """Always commit local recovery updates; never restore the pre-edit LoRA."""
    if not guard.get('failures') or remaining<=0:
        return {'attempted':False,'steps':0,'reason':'no_errors_or_budget'}
    policy=_learning_policy(args)
    local,centers=_local_radius_rows(cfg,certs,guard,policy['local_radius'])
    if not local:
        return {'attempted':False,'steps':0,'reason':'no_feasible_neighbors'}
    prior=_certified_bank(cfg,certs)
    used=min(step_cap if step_cap is not None else policy['local_repair_steps'],remaining)
    # Failed cases get direct supervised attention before drawing broad local
    # neighbors. The source examples remain part of the actual repair deck.
    center_lookup={(c['rung'],c['radius'],row['id']):row
                   for c,rows in prior for row in rows} if center_first else {}
    centers_to_train=_balanced_repair_centers(
        guard['failures'],center_lookup,state.get('attempts',0)) if center_first else []
    center_rows=[center_lookup[k] for k in centers_to_train if k in center_lookup]
    # Subsequent neighbor picks proceed from small offsets outward.
    if center_first:
        def distance(row):
            match=re.search(r'-LOCAL-\d+-([+-]\d+)$',row['id'])
            return abs(int(match.group(1))) if match else 0
        local=sorted(local,key=lambda row:(distance(row),row['id']))
        # A flat sorted list can consume nearly the whole repair budget on
        # neighbors of the first failed case. Instead distribute attention
        # over distinct damaged certificates/centers before increasing the
        # offset radius for any one center.
        neighborhoods=[]
        for ident in centers_to_train:
            prefix=ident[2]+'-LOCAL-'
            group=[row for row in local
                   if row['rung']==ident[0] and row['id'].startswith(prefix)]
            if group:neighborhoods.append(group)
    losses=[]
    start_time=time.perf_counter()
    for step in range(used):
        # Target damaged arithmetic heavily while replaying the NEW rung and
        # other historical memories; do not simply memorize T00 back at the
        # expense of T01's emerging reduction control.
        nlocal=max(1,args.batch_size//2)
        if center_first:
            batch=[]
            for position in range(step*nlocal,(step+1)*nlocal):
                if position<len(center_rows):
                    batch.append(center_rows[position])
                elif neighborhoods:
                    offset=position-len(center_rows)
                    group=neighborhoods[offset%len(neighborhoods)]
                    ring=offset//len(neighborhoods)
                    batch.append(group[ring%len(group)])
                else:
                    batch.append(local[(position-len(center_rows))%len(local)])
        else:
            batch=[rng.choice(local) for _ in range(nlocal)]
        if cases and len(batch)<args.batch_size:
            batch.append(rng.choice(cases))
        while len(batch)<args.batch_size:
            if prior and rng.randrange(2):
                _,old=rng.choice(prior);batch.append(rng.choice(old))
            elif cases:batch.append(rng.choice(cases))
            else:batch.append(rng.choice(local))
        bb.train()
        key=str(rung)
        state['steps_by_rung'][key]+=1
        _charge_step(out,rung,state['steps_by_rung'][key])
        losses.append(_train_step(bb,tok,optimizer,batch,args))
    bb.eval()
    # Persist before the audit. Even a bad retention result must never roll
    # back the trained weights or optimizer momentum.
    _persist(out,bb,zero,state,optimizer)
    return {'attempted':True,'steps':used,'centers':centers,
            'local_training_cases':len(local),'radius':policy['local_radius'],
            'center_groups':len(set((k[0],k[1],center_lookup[k]['residual'])
                for k in centers_to_train)) if center_first else None,
            'elapsed_training_seconds':round(time.perf_counter()-start_time,3),
            'last_loss':losses[-1]}


def _candidate_grade(bb,tok,probe_rows,cases,args,rung,radius,tag):
    probe=_audit(bb,tok,probe_rows,args.device,args.max_tokens,args.max_new_tokens,
           args.eval_batch,fail_limit=args.fail_limit,
           progress_every=getattr(args,'audit_progress_every',32),
           progress_tag=tag+'_probe',rung=rung,radius=radius,
           record_pass_ids=True)
    if not probe['all_pass']:return probe,probe,'probe'
    grade=_audit(bb,tok,cases,args.device,args.max_tokens,args.max_new_tokens,
           args.eval_batch,fail_limit=args.fail_limit,
           progress_every=getattr(args,'audit_progress_every',32),
           progress_tag=tag+'_full',rung=rung,radius=radius)
    return probe,grade,'full'


def _expand_from(checkpoint,state,new_cfg,bb,zero):
    """Start a new campaign with a larger radius and verified old certificates."""
    import torch
    path=Path(checkpoint)
    if not path.is_file():raise FileNotFoundError('LADDER_WARM_START_MISSING')
    old=torch.load(path,map_location='cpu',weights_only=True)
    previous=old.get('state')
    if old.get('schema')!=SCHEMA or not isinstance(previous,dict) or previous.get('status')!='CERTIFIED_ALL':
        raise RuntimeError('LADDER_WARM_START_REQUIRES_CERTIFIED_ALL')
    prior=previous.get('config')
    if not isinstance(prior,dict) or prior.get('schema')!=SCHEMA or _hash(prior)!=previous['config_hash']:
        raise RuntimeError('LADDER_WARM_START_CONFIG_DRIFT')
    if previous['source_sha256']!=state['source_sha256'] or previous['frozen_sha256']!=state['frozen_sha256']:
        raise RuntimeError('LADDER_WARM_START_BACKBONE_DRIFT')
    protected=('stage1_checkpoint','text_checkpoint','seed','t00_mode','model_family','grading',
               'no_hybridlink','no_detector','no_numeric_sidecars')
    if any(prior[k]!=new_cfg[k] for k in protected):raise RuntimeError('LADDER_WARM_START_LINEAGE_CHANGED')
    if new_cfg['operand_limit']<prior['operand_limit'] or new_cfg['max_rung']<prior['max_rung']:
        raise RuntimeError('LADDER_WARM_START_CANNOT_SHRINK_DOMAIN')
    oldr=prior['radii'];newr=new_cfg['radii']
    if newr[:len(oldr)]!=oldr or len(newr)==len(oldr):
        raise RuntimeError('LADDER_WARM_START_NEEDS_APPENDED_RADIUS')
    zero.restore_lora(bb,old['lora'])
    state['certificates']=copy.deepcopy(previous['certificates'])
    state['new_radius_start']=len(oldr)
    state['radius_index']=len(oldr)
    state['parent_campaign_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    return state


def _fork_certified_zero(parent_dir, state, cfg, bb, zero):
    """Branch a zero-radius T00..T08 ladder from the immutable T00/R0 snapshot.

    The parent's working.pt is used only for lineage checks. Its LoRA may
    already contain uncertified T00/R1 updates, which must never be imported.
    """
    import torch
    root=Path(parent_dir).resolve()
    work=root/'working.pt'
    cert_file=root/'certified_T00_R0000.pt'
    if not work.is_file() or not cert_file.is_file():
        raise FileNotFoundError('LADDER_ZERO_FORK_REQUIRES_WORKING_AND_CERTIFIED_T00_R0')
    if cfg['radii']!=[0] or cfg['max_rung']<1:
        raise RuntimeError('LADDER_ZERO_FORK_REQUIRES_RADIUS_ZERO_AND_UPPER_RUNGS')
    parent=torch.load(work,map_location='cpu',weights_only=True)
    snap=torch.load(cert_file,map_location='cpu',weights_only=True)
    prior_state=parent.get('state')
    if parent.get('schema')!=SCHEMA or not isinstance(prior_state,dict):
        raise RuntimeError('LADDER_ZERO_FORK_INVALID_PARENT')
    prior=prior_state.get('config')
    if not isinstance(prior,dict) or prior.get('schema')!=SCHEMA or _hash(prior)!=prior_state.get('config_hash'):
        raise RuntimeError('LADDER_ZERO_FORK_PARENT_CONFIG_DRIFT')
    if prior_state.get('source_sha256')!=state['source_sha256'] or prior_state.get('frozen_sha256')!=state['frozen_sha256']:
        raise RuntimeError('LADDER_ZERO_FORK_PARENT_MODEL_DRIFT')
    # A change to *any* training or oracle property except radius is forbidden.
    if prior['radii'][0]!=0 or (prior|{'radii':[0]})!=cfg:
        raise RuntimeError('LADDER_ZERO_FORK_CONFIG_MISMATCH')
    cert=snap.get('certificate')
    cert_cfg=cert.get('deck_config') if isinstance(cert,dict) else None
    # A snapshot's hash refers to the immutable config at *certification*.
    # Its campaign may later change only the radius schedule, so its current
    # working config hash is not necessarily the certificate's original hash.
    if (snap.get('schema')!=SCHEMA or snap.get('status')!='CERTIFIED' or
            not isinstance(cert_cfg,dict) or
            snap.get('config_hash')!=_hash(cert_cfg) or
            (cert_cfg|{'radii':[0]})!=(prior|{'radii':[0]}) or
            cert.get('rung')!=0 or cert.get('radius')!=0 or
            type(cert.get('steps_used')) is not int or not 0<=cert['steps_used']<=cfg['steps_per_rung']):
        raise RuntimeError('LADDER_ZERO_FORK_INVALID_CERTIFICATE')
    # Require the snapshot to be the one in the certified history. This catches
    # a substituted or stale same-named snapshot, not just an invalid deck.
    if not any(c==cert for c in prior_state.get('certificates',[])):
        raise RuntimeError('LADDER_ZERO_FORK_CERTIFICATE_NOT_IN_PARENT_HISTORY')
    rows,scope=_case_bank(prior,0,0)
    if (not rows or scope!=cert.get('scope') or len(rows)!=cert.get('case_count') or
            deck_fingerprint(rows)!=cert.get('deck_sha256')):
        raise RuntimeError('LADDER_ZERO_FORK_CERTIFIED_DECK_DRIFT')
    if prior_state.get('steps_by_rung',{}).get('0',-1)<cert['steps_used']:
        raise RuntimeError('LADDER_ZERO_FORK_PARENT_BUDGET_DRIFT')
    zero.restore_lora(bb,snap['lora'])
    state['certificates']=[copy.deepcopy(cert)]
    state['rung']=1
    state['radius_index']=0
    state['steps_by_rung']['0']=cert['steps_used']
    state['inherited_steps_by_rung']={'0':cert['steps_used']}
    state['zero_fork']={
        'parent_directory':str(root),
        'parent_config_sha256':prior_state['config_hash'],
        'certified_snapshot_sha256':hashlib.sha256(cert_file.read_bytes()).hexdigest(),
        'certificate_deck_sha256':cert['deck_sha256'],
        'first_rung':1,
        'source':'certified_T00_R0000.pt (never working.pt LoRA)',
    }
    return state


def _ensure_local_zero_snapshot(out, state, fork_dir=None):
    """Require the imported certificate to survive as a self-contained copy."""
    import shutil
    meta=state.get('zero_fork')
    if not meta:return
    local=Path(out)/'certified_T00_R0000.pt'
    digest=meta['certified_snapshot_sha256']
    if not local.is_file():
        if not fork_dir:
            raise RuntimeError('LADDER_ZERO_FORK_LOCAL_SNAPSHOT_MISSING')
        src=Path(fork_dir)/'certified_T00_R0000.pt'
        if not src.is_file() or hashlib.sha256(src.read_bytes()).hexdigest()!=digest:
            raise RuntimeError('LADDER_ZERO_FORK_PARENT_SNAPSHOT_DRIFT')
        temp=local.with_name(local.name+'.tmp')
        try:
            shutil.copyfile(src,temp)
            if hashlib.sha256(temp.read_bytes()).hexdigest()!=digest:
                raise RuntimeError('LADDER_ZERO_FORK_PARENT_SNAPSHOT_DRIFT')
            os.replace(temp,local)
        finally:
            temp.unlink(missing_ok=True)
    if hashlib.sha256(local.read_bytes()).hexdigest()!=digest:
        raise RuntimeError('LADDER_ZERO_FORK_LOCAL_SNAPSHOT_DRIFT')


def _spent_ledger(out, inherited=None):
    """Read durable pre-step charges, starting after inherited fork costs.

    The first local charge on a forked T00 lineage is 51, not 1, when its
    accepted T00 snapshot consumed 50 steps. Original campaigns use zero.
    """
    base=inherited or {}
    charges={}
    path=Path(out)/'budget_ledger.jsonl'
    if not path.exists():return charges
    for line in path.read_text(encoding='utf8').splitlines():
        event=json.loads(line)
        if event.get('event')!='spend' or type(event.get('step')) is not int or type(event.get('rung')) is not int:
            raise RuntimeError('LADDER_INVALID_BUDGET_LEDGER')
        rung=str(event['rung'])
        last=charges.get(rung,base.get(rung,0))
        if event['step']!=last+1:
            raise RuntimeError('LADDER_NONMONOTONE_BUDGET_LEDGER')
        charges[rung]=event['step']
    return charges


def _charge_step(out,rung,step):
    """Write-ahead charge; if training crashes, spent budget is NOT refunded."""
    path=Path(out)/'budget_ledger.jsonl'
    path.parent.mkdir(parents=True,exist_ok=True)
    event={'event':'spend','rung':rung,'step':step}
    with path.open('a',encoding='utf8') as stream:
        stream.write(json.dumps(event,sort_keys=True)+'\n')
        stream.flush();os.fsync(stream.fileno())


def _budget_rule(state,extra):
    if extra<0:raise ValueError('LADDER_NEGATIVE_BUDGET_EXTENSION')
    if extra and state['status']!='BUDGET_EXHAUSTED':
        raise RuntimeError('LADDER_EXTENSION_ONLY_AFTER_BUDGET_EXHAUSTION')
    if extra:
        key=str(state['rung'])
        state['budget_by_rung'][key]+=extra
        state['status']='READY'
    return state


def run(args):
    import torch
    from tools import nanojev_qwen_zero_line_backbone as zero
    cfg=_config(args)
    _learning_policy(args)
    _advancement_policy(args)
    adaptive_policy=_adaptive_policy(args)
    if not args.stage1_checkpoint or not args.text_checkpoint:raise ValueError('LADDER_REQUIRES_PARENT_CHECKPOINTS')
    out=Path(args.output)
    if args.warm_start_ladder and getattr(args,'fork_certified_zero_from',None):
        raise RuntimeError('LADDER_CANNOT_WARM_START_AND_FORK_ZERO')
    fork_dir=getattr(args,'fork_certified_zero_from',None)
    _outputs_safe(out,[Path(args.stage1_checkpoint).parent,Path(args.text_checkpoint).parent]+
                  ([Path(args.warm_start_ladder).parent] if args.warm_start_ladder else [])+
                  ([Path(fork_dir)] if fork_dir else []))
    if out.exists() and any(out.iterdir()) and not (out/'working.pt').exists():
        raise RuntimeError('LADDER_REFUSES_NONEMPTY_UNRECOGNIZED_OUTPUT')
    bb,tok,sources,frozen,dirs,zero=_fresh_parent(args)
    _outputs_safe(out,dirs)
    if (out/'working.pt').exists():
        if args.warm_start_ladder:raise RuntimeError('LADDER_WARM_START_REQUIRES_NEW_OUTPUT')
        state,optimizer_state=_load_resume(out,cfg,bb,zero,sources,frozen)
        if fork_dir:
            source=state.get('zero_fork')
            if not source or source['parent_directory']!=str(Path(fork_dir).resolve()):
                raise RuntimeError('LADDER_ZERO_FORK_RESUME_SOURCE_MISMATCH')
            snapshot=Path(fork_dir)/'certified_T00_R0000.pt'
            if (not snapshot.is_file() or hashlib.sha256(snapshot.read_bytes()).hexdigest()!=
                    source['certified_snapshot_sha256']):
                raise RuntimeError('LADDER_ZERO_FORK_PARENT_SNAPSHOT_DRIFT')
    else:
        state=_new_state(cfg,sources,frozen);optimizer_state=None
        if args.warm_start_ladder:
            state=_expand_from(Path(args.warm_start_ladder),state,cfg,bb,zero)
        elif fork_dir:
            state=_fork_certified_zero(fork_dir,state,cfg,bb,zero)
        _persist(out,bb,zero,state)
    # A crash after initial state persistence but before copying the snapshot
    # is restartable with the original fork option; never accept a substituted
    # local certificate or silently proceed without one.
    _ensure_local_zero_snapshot(out,state,fork_dir)
    _record_learning_policy(state,args)
    _retention_settings(args)
    advancement=_record_advancement_policy(state,args)
    _snapshot_curriculum_transition(out,state,args)
    _record_curriculum(state,args,advancement)
    adaptive_control=_adaptive_control(state,args)
    for rung,charged in _spent_ledger(out,state.get('inherited_steps_by_rung')).items():
        if rung not in state['steps_by_rung']:raise RuntimeError('LADDER_BUDGET_LEDGER_UNKNOWN_RUNG')
        if charged<state['steps_by_rung'][rung]:raise RuntimeError('LADDER_BUDGET_LEDGER_BEHIND_CHECKPOINT')
        state['steps_by_rung'][rung]=charged
    _budget_rule(state,args.extend_budget)
    if state['status'] in ('CERTIFIED_ALL','PROVISIONAL_ALL'):
        print('QWEN_LADDER_RESULT='+json.dumps({'status':state['status'],'certificates':state['certificates'][-5:],
             'provisional':len(state.get('provisional',[]))},sort_keys=True),flush=True)
        return state
    if state['status']=='BUDGET_EXHAUSTED':
        print('QWEN_LADDER_HALT='+json.dumps({'status':'BUDGET_EXHAUSTED','rung':state['rung'],
           'steps':state['steps_by_rung'][str(state['rung'])],'budget':state['budget_by_rung'][str(state['rung'])],
           'next_action':'explicit --extend-budget N or change strategy'},sort_keys=True),flush=True)
        return state
    ps=[p for _,p in zero.lora_named_parameters(bb)]
    if not ps:raise RuntimeError('LADDER_NO_TRAINABLE_QWEN_LORA')
    for p in ps:p.requires_grad_(True)
    optimizer=torch.optim.AdamW(ps,lr=args.lr,weight_decay=0)
    if optimizer_state is not None:optimizer.load_state_dict(optimizer_state)
    # Persist the policy migration with the original optimizer intact, before
    # any new updates. Resume must never silently revert to rollback mode.
    _persist(out,bb,zero,state,optimizer)
    rng=random.Random(cfg['seed']+state['steps_by_rung'][str(state['rung'])]*17+state['rung']*101)
    while state['rung']<=cfg['max_rung']:
        rung=state['rung'];radius=cfg['radii'][state['radius_index']];key=str(rung)
        adaptive_target=(_adaptive_target(adaptive_control,adaptive_policy,rung,radius,args)
                         if adaptive_control is not None else None)
        adaptive_start=time.perf_counter() if adaptive_target is not None else None
        cases,scope=_case_bank(cfg,rung,radius)
        if not cases:raise RuntimeError('LADDER_NO_TESTABLE_CASES')
        if scope['undersampled_cells']:
            state['status']='BLOCKED_UNDERSAMPLED_DECK'
            state['last_audit']={'rung':rung,'radius':radius,'scope':scope}
            _persist(out,bb,zero,state,optimizer)
            print('QWEN_LADDER_HALT='+json.dumps({'status':state['status'],
                  'rung':rung,'radius':radius,'undersampled':len(scope['undersampled_cells']),
                  'next_action':'new campaign with smaller --per-cell or improved deck'},sort_keys=True),flush=True)
            return state
        # Exact mode uses a stable pre-probe before exhaustive certification.
        # Sampled mode rotates stratified acquisition probes and advances
        # provisionally, never presenting samples as exhaustive certificates.
        probe_rows=_probe_rows(cases,getattr(args,'probe_cases',128))
        bb.eval()
        sampled=advancement['mode']=='sampled'
        if sampled:
            gate_number=int(state.get('readiness_gate_count',0))+1
            state['readiness_gate_count']=gate_number
            interval=_gate_full_interval(args)
            periodic_gate=bool(interval and gate_number%interval==0)
            base_probe=_sampled_progress_grade(bb,tok,cases,args,rung,radius,
                state['attempts'],'readiness')
            full_checked=False
            grade=base_probe
            if periodic_gate:
                # An actual full census, even if the error threshold has been
                # crossed. This is validation, never a new exact certificate.
                census=_audit(bb,tok,cases,args.device,args.max_tokens,
                    args.max_new_tokens,args.eval_batch,fail_limit=args.fail_limit,
                    progress_every=5*getattr(args,'audit_progress_every',32),
                    progress_tag='periodic_gate_full',rung=rung,radius=radius)
                census_ok=census['passed']>=_required_retained(len(cases),advancement['accuracy'])
                grade['readiness']=bool(grade['readiness'] and census_ok)
                grade['all_pass']=grade['readiness']
                if not census_ok:
                    # A passing random sample can miss concentrated failures.
                    # Expose the complete-census counterexamples to the normal
                    # always-commit acquisition/repair path rather than
                    # treating a failed gate as an empty failure bank.
                    grade['failures']=(grade['failures']+census['failures'])[:args.fail_limit]
                    grade['periodic_census_rejected']=True
                state['last_periodic_gate']={'rung':rung,'radius':radius,
                    'gate_number':gate_number,'tested':census['tested'],
                    'errors':census['tested']-census['passed'],
                    'allowed_errors':len(cases)-_required_retained(len(cases),advancement['accuracy']),
                    'passed':census['passed'],'deck_total':len(cases),
                    'accuracy':census['passed']/len(cases),'ok':census_ok}
                print('QWEN_LADDER_PERIODIC_GATE_FULL='+json.dumps(
                    state['last_periodic_gate'],sort_keys=True),flush=True)
        else:
            base_probe=_audit(bb,tok,probe_rows,args.device,args.max_tokens,
                args.max_new_tokens,args.eval_batch,fail_limit=args.fail_limit,
                progress_every=getattr(args,'audit_progress_every',32),
                progress_tag='probe',rung=rung,radius=radius,record_pass_ids=True)
            full_checked=base_probe['all_pass']
            grade=(_audit(bb,tok,cases,args.device,args.max_tokens,args.max_new_tokens,
                         args.eval_batch,fail_limit=args.fail_limit,
                         progress_every=getattr(args,'audit_progress_every',32),
                         progress_tag='exhaustive',rung=rung,radius=radius)
                   if full_checked else base_probe)
        state['last_audit']={'rung':rung,'radius':radius,**grade,'scope':scope,
                             'audit_kind':('sampled' if sampled else ('full' if full_checked else 'probe')),
                             'probe_cases':len(probe_rows)}
        print('QWEN_LADDER_AUDIT='+json.dumps({'rung':rung,'radius':radius,'steps':state['steps_by_rung'][key],
             'budget':state['budget_by_rung'][key],'passed':grade['passed'],
             'tested':grade['tested'],'total':grade['total'],
             'truncated':grade.get('truncated',False),
             'all_pass':grade['all_pass'],'audit_kind':('sampled' if sampled else ('full' if full_checked else 'probe')),
             'certification_eligible':False,
             'current_deck_exact':full_checked and grade['all_pass'],
             'retention_pending':full_checked and grade['all_pass'],
             'scope':scope['kind'],
             'tripwire_tripped':grade.get('tripwire_tripped',False),
             'estimated_current_deck_errors':grade.get('estimated_current_deck_errors')},
             sort_keys=True),flush=True)
        # Certification remains exact for the NEW rung; historical decks only
        # need to satisfy the recorded per-certificate retention floor.
        guard=None
        if sampled and grade['readiness']:
            # Every historical deck is sampled during routine provisional
            # advancement; the occasional explicit periodic gate is a full
            # census of every deck, including earlier provisional milestones.
            # Neither a sampled gate nor its diagnostic can issue a certificate.
            periodic_full=_periodic_retention_due(state,args)
            sample_budget=None if periodic_full else _retention_settings(args)[0]
            histories=[*state['certificates'],*state.get('provisional',[])]
            print('QWEN_LADDER_RETENTION_SCOPE='+json.dumps({
                'mode':'periodic_full' if periodic_full else 'rotating_stratified_sample',
                'historical_decks':len(histories),'sample_cases':sample_budget,
                'next_provisional_milestone':len(state.get('provisional',[]))+1},
                sort_keys=True),flush=True)
            guard=_audit_retention(bb,tok,cfg,histories,args,
                sample_cases=sample_budget,attempt=state['attempts'],state=state,
                force_complete=periodic_full)
            if guard['ok']:
                milestone={'rung':rung,'radius':radius,'case_count':len(cases),
                     'deck_sha256':deck_fingerprint(cases),'scope':scope,
                     'steps_used':state['steps_by_rung'][key],
                     'deck_config':copy.deepcopy(cfg),
                     'sample_accuracy':grade['accuracy'],
                     'sample_passed':grade['passed'],
                     'sample_tested':grade['tested'],
                     'sample_groups':grade['per_sample'],
                     'advance_accuracy':advancement['accuracy'],
                     'retention_scope':'periodic_full' if periodic_full else 'sampled',
                     'provisional':True}
                state.setdefault('provisional',[]).append(milestone)
                print('QWEN_LADDER_PROVISIONAL_ADVANCE='+json.dumps({
                    'rung':rung,'radius':radius,'sample_passed':grade['passed'],
                    'sample_tested':grade['tested'],'sample_accuracy':grade['accuracy'],
                    'advance_accuracy':advancement['accuracy'],
                    'retention_ok':True,'retention_scope':milestone['retention_scope'],
                    'exact_certificate':False},sort_keys=True),flush=True)
                next_target=_first_unfinished_target(cfg,state['certificates'],
                    provisional=state['provisional'],
                    radius_first=_schedule_radius_first(state,advancement['mode']))
                previous_rung=rung
                if next_target is None:
                    state['rung']=cfg['max_rung']+1
                    state['radius_index']=0
                    state['status']='PROVISIONAL_ALL'
                else:
                    state['rung'],next_radius=next_target
                    state['radius_index']=cfg['radii'].index(next_radius)
                    if state['rung']!=previous_rung:
                        optimizer=torch.optim.AdamW(ps,lr=args.lr,weight_decay=0)
                    state['status']='READY'
                _persist(out,bb,zero,state,optimizer if state['status']!='PROVISIONAL_ALL' else None)
                if state['status']=='PROVISIONAL_ALL':break
                continue
        if full_checked and grade['all_pass']:
            guard=_audit_retention(bb,tok,cfg,state['certificates'],args,state=state)
            if guard['ok']:
                cert={'rung':rung,'radius':radius,'case_count':len(cases),'deck_sha256':deck_fingerprint(cases),
                      'scope':scope,'steps_used':state['steps_by_rung'][key],
                      'deck_config':cfg}
                state['certificates'].append(cert)
                cert_path=out/f'certified_T{rung:02d}_R{radius:04d}.pt'
                if cert_path.exists():raise RuntimeError('LADDER_CERTIFIED_CHECKPOINT_ALREADY_EXISTS')
                _atomic_torch(cert_path,{
                     'schema':SCHEMA,'status':'CERTIFIED','certificate':cert,
                     'lora':zero.lora_state(bb),'config_hash':state['config_hash']})
                next_target=_first_unfinished_target(cfg,state['certificates'],
                    radius_first=_schedule_radius_first(state,advancement['mode']))
                previous_rung=rung
                if next_target is None:
                    state['rung']=cfg['max_rung']+1
                    state['radius_index']=0
                    state['status']='CERTIFIED_ALL'
                else:
                    state['rung'],next_radius=next_target
                    state['radius_index']=cfg['radii'].index(next_radius)
                    # Keep the optimizer across schedule changes; the normal
                    # rung transition still starts with a fresh optimizer.
                    if state['rung']!=previous_rung:
                        optimizer=torch.optim.AdamW(ps,lr=args.lr,weight_decay=0)
                    state['status']='READY'
                _persist(out,bb,zero,state,optimizer if state['status']!='CERTIFIED_ALL' else None)
                if state['status']=='CERTIFIED_ALL':break
                continue
        remaining=state['budget_by_rung'][key]-state['steps_by_rung'][key]
        if remaining<=0:
            state['status']='BUDGET_EXHAUSTED';_persist(out,bb,zero,state,optimizer)
            break
        # Always commit each T01+ acquisition update, including improvements
        # that temporarily damage T00 format or other certified arithmetic.
        used=0;losses=[]
        if not (full_checked and grade['all_pass']) and not (sampled and grade['readiness']):
            lookup={r['id']:r for r in cases}
            failures=[lookup[x['id']] for x in grade['failures']]
            replay_decks=_certified_bank(cfg,
                [*state['certificates'],*state.get('provisional',[])] if sampled else state['certificates'])
            if not failures:raise RuntimeError('LADDER_FAILURE_BANK_EMPTY')
            used=min(adaptive_target['check_steps'] if adaptive_target is not None else args.repair_steps,remaining)
            for _ in range(used):
                nreplay=min(args.replay_per_batch,len(replay_decks),args.batch_size-1)
                nfail=max(1,(args.batch_size-nreplay)//2)
                batch=[rng.choice(failures) for _ in range(nfail)]
                for _ in range(nreplay):
                    _,old_deck=rng.choice(replay_decks)
                    batch.append(rng.choice(old_deck))
                while len(batch)<args.batch_size:batch.append(rng.choice(cases))
                bb.train()
                # Charged once before every optimizer step, even if interrupted.
                state['steps_by_rung'][key]+=1
                _charge_step(out,rung,state['steps_by_rung'][key])
                losses.append(_train_step(bb,tok,optimizer,batch,args))
            bb.eval()
            # Commit BEFORE judging the new weights. Retention is now a
            # repair target, NOT a reason to reject an update.
            _persist(out,bb,zero,state,optimizer)
        if sampled:
            histories=[*state['certificates'],*state.get('provisional',[])]
            if used==0 and grade['readiness'] and guard is not None:
                # A provisionally ready rung already passed its sampled gate
                # and historical retention was checked above. If retention
                # failed, no model update occurred: reusing those results
                # avoids an entire redundant current + historical audit.
                candidate_grade=grade
                candidate_kind='sampled'
                candidate_probe=grade
            else:
                candidate_grade=_sampled_progress_grade(bb,tok,cases,args,rung,radius,
                      state['attempts']+1,'committed')
                candidate_probe=candidate_grade
                candidate_kind='sampled'
                guard=_audit_retention(bb,tok,cfg,histories,args,
                     sample_cases=_retention_settings(args)[0],attempt=state['attempts']+1,
                     state=state)
        else:
            candidate_probe,candidate_grade,candidate_kind=_candidate_grade(
                bb,tok,probe_rows,cases,args,rung,radius,'committed')
            guard=(_audit_retention(bb,tok,cfg,state['certificates'],args,state=state)
                   if guard is None or used else guard)
        histories=[*state['certificates'],*state.get('provisional',[])] if sampled else state['certificates']
        if adaptive_target is not None:
            step_count=sum(state['steps_by_rung'].values())
            full_steps=adaptive_policy['full_steps']
            if (full_steps and histories and
                    step_count-adaptive_control['last_full_steps']>=full_steps):
                # Persistent cadence covers stalled rungs that never advance.
                full_guard=_audit_retention(bb,tok,cfg,histories,args,
                    state=state,force_complete=True)
                adaptive_control['last_full_steps']=step_count
                print('QWEN_LADDER_ADAPTIVE_FULL_RETENTION='+json.dumps({
                    'step_count':step_count,'passed':full_guard['passed'],
                    'tested':full_guard['tested'],'ok':full_guard['ok'],
                    'interval_steps':full_steps},sort_keys=True),flush=True)
                guard=full_guard
            if not guard['ok']:
                _adaptive_harvest(bb,tok,cfg,histories,guard,args,state,state['attempts'],
                                  target=adaptive_target)
        guard_before_repair=guard
        print('QWEN_LADDER_COMMIT='+json.dumps({
            'rung':rung,'radius':radius,'steps':used,
            'candidate_passed':candidate_grade['passed'],
            'candidate_tested':candidate_grade['tested'],
            'candidate_total':candidate_grade['total'],
            'candidate_audit_kind':candidate_kind,
            'retention_passed':guard['passed'],'retention_tested':guard.get('tested',guard['total']),
            'retention_total':guard['total'],
            'retention_ok':guard['ok'],
            'committed':True},sort_keys=True),flush=True)
        local=_train_local_radius(bb,tok,optimizer,guard,
             histories,cfg,cases,args,rng,state,out,zero,rung,
             state['budget_by_rung'][key]-state['steps_by_rung'][key],
             step_cap=adaptive_target['repair_steps'] if adaptive_target is not None else None,
             center_first=adaptive_target is not None)
        if local['attempted']:
            if sampled:
                post_grade=_sampled_progress_grade(bb,tok,cases,args,rung,radius,
                    state['attempts']+2,'post_local')
                post_probe=post_grade;post_kind='sampled'
                post_guard=_audit_retention(bb,tok,cfg,
                    [*state['certificates'],*state.get('provisional',[])],args,
                    sample_cases=_retention_settings(args)[0],attempt=state['attempts']+2,
                    state=state)
            else:
                post_probe,post_grade,post_kind=_candidate_grade(
                    bb,tok,probe_rows,cases,args,rung,radius,'post_local')
                post_guard=_audit_retention(bb,tok,cfg,state['certificates'],args,state=state)
            print('QWEN_LADDER_LOCAL_REPAIR='+json.dumps({
                'rung':rung,'radius':radius,**local,
                'retention_before':guard['passed'],
                'retention_after':post_guard['passed'],
                'retention_tested':post_guard.get('tested',post_guard['total']),
                'retention_total':post_guard['total'],
                'retention_ok':post_guard['ok'],
                'current_before':candidate_grade['passed'],
                'current_after':post_grade['passed'],
                'current_audit_kind':post_kind,
                'committed':True},sort_keys=True),flush=True)
            guard=post_guard;candidate_grade=post_grade;candidate_kind=post_kind
        result={'rung':rung,'radius':radius,'candidate':'COMMITTED',
                'baseline_passed':grade['passed'],
                'candidate_passed':candidate_grade['passed'],
                'candidate_tested':candidate_grade['tested'],
                'candidate_total':candidate_grade['total'],
                'candidate_audit_kind':candidate_kind,
                'retention_ok':guard['ok'],'retention_passed':guard['passed'],
                'retention_tested':guard.get('tested',guard['total']),
                'retention_total':guard['total'],'retention_floor':guard['floor'],
                'acquisition_steps':used,'local_repair':local}
        if losses:result['last_train_loss']=losses[-1]
        state['last_retention']={'passed':guard['passed'],
              'tested':guard.get('tested',guard['total']),'total':guard['total'],
              'floor':guard['floor'],'ok':guard['ok'],
              'per_certificate':[{k:v for k,v in g.items() if k!='failures'}
                                 for g in guard['certificates']]}
        state['last_committed_probe']={'passed':candidate_grade['passed'],
              'total':candidate_grade['total'],'kind':candidate_kind}
        if adaptive_target is not None:
            before=grade['passed']/grade['tested'] if grade['tested'] else 0.0
            after=candidate_grade['passed']/candidate_grade['tested'] if candidate_grade['tested'] else 0.0
            decision=_adaptive_decide(adaptive_target,adaptive_policy,before,after,
                guard_before_repair,guard,local.get('steps',0),
                elapsed_seconds=time.perf_counter()-adaptive_start)
            decision.update({'harvest_next_cases':adaptive_target['harvest_cases'],
                             'repair_training_seconds':local.get('elapsed_training_seconds'),
                             'rung':rung,'radius':radius,
                             'attempt':state['attempts']+1,
                             'steps_total':state['steps_by_rung'][key]})
            adaptive_control['decisions'].append(decision)
            adaptive_control['decisions']=adaptive_control['decisions'][-100:]
            print('QWEN_LADDER_ADAPTIVE_DECISION='+json.dumps(decision,sort_keys=True),flush=True)
        state['attempts']+=1
        state['status']='READY'
        _persist(out,bb,zero,state,optimizer)
        print('QWEN_LADDER_REPAIR='+json.dumps(result,sort_keys=True),flush=True)
    if state['status']=='BUDGET_EXHAUSTED':
        print('QWEN_LADDER_HALT='+json.dumps({'status':state['status'],'rung':state['rung'],
             'radius':cfg['radii'][state['radius_index']],'consumed':state['steps_by_rung'][str(state['rung'])],
             'allocated':state['budget_by_rung'][str(state['rung'])],
             'next_action':'human decision; --extend-budget N is explicit'},sort_keys=True),flush=True)
    else:
        print('QWEN_LADDER_RESULT='+json.dumps({'status':state['status'],
             'certificates':len(state['certificates']),
             'provisional':len(state.get('provisional',[])),
             'steps_by_rung':state['steps_by_rung']},sort_keys=True),flush=True)
    return state


def preview(args):
    cfg=_config(args);radii=cfg['radii'];summary=[]
    for rung in range(cfg['max_rung']+1):
        for radius in radii:
            cases,scope=_case_bank(cfg,rung,radius)
            summary.append({'rung':rung,'radius':radius,'cases':len(cases),
                            'scope':scope['kind'],'covered_cells':scope['cells'],
                            'unreachable_cells':len(scope['unreachable_cells']),
                            'undersampled_cells':len(scope['undersampled_cells']),
                            'zero_cases':sum(x['residual']==0 for x in cases),
                            'deck_sha256':deck_fingerprint(cases)})
    res={'schema':SCHEMA,'stage':'preview','no_model_loaded':True,
         'radii':radii,'operand_limit':args.operand_limit,'max_rung':cfg['max_rung'],
         'steps_per_rung':args.steps_per_rung,'per_rung':summary,
         'scope_note':'T00 exhaustive ordered pairs; T01+ finite deck, not exhaustive universal certificate'}
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    _atomic_json(out/'deck_preview.json',res)
    print('QWEN_LADDER_PREVIEW='+json.dumps(res,sort_keys=True),flush=True)
    return res


def status(args):
    p=Path(args.output)/'progress.json'
    if not p.is_file():raise FileNotFoundError('LADDER_NO_PROGRESS_FILE')
    state=json.loads(p.read_text(encoding='utf8'))
    print('QWEN_LADDER_STATUS='+json.dumps(state,sort_keys=True),flush=True)
    return state


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=('preview','run','status'),default='preview')
    p.add_argument('--stage1-checkpoint');p.add_argument('--text-checkpoint')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--output',default=str(Path.home()/'NanoJev'/'runs'/'qwen_lm_radius_ladder_v1'))
    p.add_argument('--radii',default=RADII_DEFAULT,
                   help='Mutable radius acquisition schedule; may change on resume without forking')
    p.add_argument('--operand-limit',type=int,default=1024)
    p.add_argument('--max-rung',type=int,default=8)
    p.add_argument('--per-cell',type=int,default=8)
    p.add_argument('--t00-mode',choices=('exhaustive','sampled'),default='exhaustive')
    p.add_argument('--steps-per-rung',type=int,default=10000)
    p.add_argument('--repair-steps',type=int,default=25)
    p.add_argument('--retention-floor',type=float,default=0.95,
                   help='Minimum exact LM packet accuracy on EACH previously certified deck')
    p.add_argument('--local-radius',type=int,default=4,
                   help='Signed +/- operand neighborhood of damaged certified problems')
    p.add_argument('--local-repair-steps',type=int,default=10,
                   help='Extra targeted optimizer steps after a committed update, within rung budget')
    p.add_argument('--batch-size',type=int,default=4)
    p.add_argument('--replay-per-batch',type=int,default=1)
    p.add_argument('--eval-batch',type=int,default=8)
    p.add_argument('--fail-limit',type=int,default=64)
    p.add_argument('--probe-cases',type=int,default=128,help='Per-sample diagnostic and sampled-advancement cases')
    p.add_argument('--curriculum',choices=('radius-major','rung-major'),default=None,
                   help='Scheduling only: rung-major completes R0..Rn at each depth before moving deeper;'
                        ' preserves weights, optimizer, prior milestones and resume selection')
    p.add_argument('--advance-mode',choices=('exact','sampled'),default='exact',
                   help='Opt-in sampled provisional rung advancement; exact certificate mode remains available')
    p.add_argument('--advance-accuracy',type=float,default=.95,
                   help='Required correctness on EACH independent advancement sample (sampled mode)')
    p.add_argument('--advance-probes',type=int,default=2,
                   help='Disjoint rotating advancement samples per decision (sampled mode)')
    p.add_argument('--retention-sample-cases',type=int,default=128,
                   help='Rotating stratified cases per historical deck during routine sampled retention')
    p.add_argument('--retention-known-failure-cases',type=int,default=32,
                   help='Max previously failed case IDs replayed separately per historical deck')
    p.add_argument('--gate-full-every',type=int,default=10,
                   help='Every Nth sampled readiness gate fully evaluates the current deck; 0 disables')
    p.add_argument('--retention-full-every',type=int,default=10,
                   help='Full historical retention census every N provisional advancements (0 disables)')
    p.add_argument('--audit-progress-every',type=int,default=32,
                   help='Probe progress every N cases; retention progress every 5*N (~80%% fewer lines)')
    p.add_argument('--adaptive-repair',action='store_true',
                   help='Opt-in adaptive acquisition checks, failure harvesting, and local repair scheduling')
    p.add_argument('--adaptive-check-min',type=int,default=5,
                   help='Fewest optimizer steps between adaptive acquisition gates')
    p.add_argument('--adaptive-check-max',type=int,default=100,
                   help='Most optimizer steps between adaptive acquisition gates')
    p.add_argument('--adaptive-repair-min',type=int,default=2,
                   help='Minimum local repair optimizer steps per intervention')
    p.add_argument('--adaptive-repair-max',type=int,default=20,
                   help='Maximum local repair optimizer steps per intervention')
    p.add_argument('--adaptive-harvest-cases',type=int,default=128,
                   help='Max extra historical cases evaluated to find repair centers after a tripped gate')
    p.add_argument('--adaptive-failure-target',type=int,default=8,
                   help='Stop large-deck failure harvesting after this many new distinct centers')
    p.add_argument('--adaptive-full-steps',type=int,default=1000,
                   help='Full historical retention census every N optimizer steps even on stalled rungs; 0 disables')
    p.add_argument('--lr',type=float,default=0.00005)
    p.add_argument('--max-seq',type=int,default=192)
    p.add_argument('--max-tokens',type=int,default=256)
    p.add_argument('--max-new-tokens',type=int,default=64)
    p.add_argument('--extend-budget',type=int,default=0)
    p.add_argument('--warm-start-ladder',help='Certified previous working.pt; new output, expanded radii')
    p.add_argument('--fork-certified-zero-from',help='Original campaign folder with certified_T00_R0000.pt; new output and --radii 0')
    p.add_argument('--seed',type=int,default=42257)
    p.add_argument('--baseline');p.add_argument('--backbone');p.add_argument('--activation');p.add_argument('--format')
    p.add_argument('--allow-model-download',action='store_true')
    args=p.parse_args(argv)
    if args.stage=='status':return status(args)
    if args.stage=='preview':return preview(args)
    return run(args)

if __name__=='__main__':main()
