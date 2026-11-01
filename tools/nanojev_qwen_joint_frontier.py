#!/usr/bin/env python3
"""One continuing optimizer: T00-T08, HDB00-HDB08, and nine mathematical families.

The stressed arithmetic checkpoint seeds a new isolated, *joint* LoRA experiment.
Each optimizer step trains ALL THREE populations. Held-out problems determine
weaknesses, never become training targets. No separate mathematical task handoff.
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
import random
import re
import math
import time
from pathlib import Path

from tools import nanojev_qwen_lm_radius_ladder as ladder
from tools import nanojev_qwen_lm_frontier_stress_repair as frontier
from tools import nanojev_qwen_hdb_balance as hdb
from tools import nanojev_qwen_hdb_difficulty as difficulty
from tools import nanojev_qwen_competency_diagnostic as competency
from tools import nanojev_qwen_lm_frontier_tasks as math_tasks

SCHEMA = 'nanojev.qwen.joint-t-hdb-mathematical-frontier.v3'
LEGACY_SCHEMA = 'nanojev.qwen.joint-t-hdb-numeric-balance-frontier.v2'



# Focus is a mutable optimizer *schedule*, not a change in model geometry or
# evaluation data. The three mandatory minibatch populations never disappear.
FOCUS_DEFAULT_WEIGHT = 4.0
FOCUS_MAX_WEIGHT = 20.0


def _parse_focus(spec, policy):
    """Parse --focus '[T07=6,HDB08=4,algebra:L1=5]'.

    Bare names mean 4x; `none` removes a previous focus on resume. Supports
    T, HDB, math, a nine-family math task, and an exact mathematical level.
    Weights select training examples; they do not multiply gradient loss.
    """
    if spec is None:
        return None  # Preserve saved focus on resume.
    raw=spec.strip()
    if raw.lower() in ('none','off','clear'):
        return {}
    if raw.startswith('[') or raw.endswith(']'):
        if not raw.startswith('[') or not raw.endswith(']'):
            raise ValueError('JOINT_FOCUS_BAD_BRACKETS')
        raw=raw[1:-1].strip()
    if not raw:
        raise ValueError('JOINT_FOCUS_EMPTY')
    selected={}
    for element in raw.split(','):
        element=element.strip()
        if not element:
            raise ValueError('JOINT_FOCUS_EMPTY_ELEMENT')
        if element.count('=')>1:
            raise ValueError('JOINT_FOCUS_BAD_WEIGHT:'+element)
        name,sep,value=element.partition('=')
        name=name.strip()
        if not name:
            raise ValueError('JOINT_FOCUS_MISSING_TARGET')
        if sep:
            try:weight=float(value)
            except ValueError:raise ValueError('JOINT_FOCUS_BAD_WEIGHT:'+element) from None
        else:
            weight=FOCUS_DEFAULT_WEIGHT
        if not math.isfinite(weight) or not 1 <= weight <= FOCUS_MAX_WEIGHT:
            raise ValueError('JOINT_FOCUS_WEIGHT_OUT_OF_RANGE:'+element)
        upper=name.upper()
        if upper in ('T','HDB'):
            target=upper
        elif upper in ('MATH','MATHEMATICS'):
            target='math'
        elif re.fullmatch(r'T(?:0[0-8])',upper):
            target=upper
        elif re.fullmatch(r'HDB(?:0[0-8])',upper):
            target=upper
        else:
            normalized=name.lower().replace('-','_')
            if normalized.startswith('math:'):
                normalized=normalized[5:]
            task,divider,level=normalized.partition(':')
            if task not in policy['math_tasks']:
                raise ValueError('JOINT_FOCUS_UNKNOWN_TARGET:'+name)
            if divider:
                if not re.fullmatch(r'l\d+',level):
                    raise ValueError('JOINT_FOCUS_BAD_LEVEL:'+name)
                index=int(level[1:])
                if index >= policy['math_levels']:
                    raise ValueError('JOINT_FOCUS_LEVEL_OUT_OF_RANGE:'+name)
                target=f'{task}:L{index}'
            else:
                target=task
        if target in selected:
            raise ValueError('JOINT_FOCUS_DUPLICATE:'+target)
        selected[target]=weight
    return dict(sorted((k,v) for k,v in selected.items() if v>1.0))


def _focus_weight(focus,label):
    if not focus:return 1.0
    group=('T' if label.startswith('T') else
           'HDB' if label.startswith('HDB') else 'math')
    family=label.split(':L',1)[0] if group=='math' else group
    return max(1.0,focus.get(group,1.0),focus.get(family,1.0),
               focus.get(label,1.0))


def _focus_groups(policy,focus):
    labels={'t':[f'T{i:02d}' for i in range(9)],
            'hdb':[f'HDB{i:02d}' for i in range(9)],
            'mathematics':_math_labels(policy)}
    return {group:1+sum(_focus_weight(focus,label)-1 for label in members)
            for group,members in labels.items()}


def _focus_labels(policy,focus):
    return {label:_focus_weight(focus,label)
            for label in ([f'T{i:02d}' for i in range(9)]+
                [f'HDB{i:02d}' for i in range(9)]+_math_labels(policy))
            if _focus_weight(focus,label)>1}


def _focus_current(state):
    results={}
    for bucket,key,field in (('t_current','by_rung','correct_now'),
                             ('h_current','by_tier','correct'),
                             ('math_current','by_task_level','correct')):
        for label,record in state.get(bucket,{}).get(key,{}).items():
            results[label]={'correct':record.get(field,record.get('correct',0)),
                            'tested':record.get('tested',0)}
    return results


def _focus_exposure(state):
    return {**{k:v['examples'] for k,v in state.get('t_exposures',{}).items()},
            **{k:v['examples'] for k,v in state.get('h_exposures',{}).items()},
            **{k:v['examples'] for k,v in state.get('math_exposures',{}).items()}}


def _activate_focus(state,policy,focus):
    old=state.get('focus',{})
    if old==focus:
        return False
    state['focus']=copy.deepcopy(focus)
    state['focus_since_step']=state['step']
    state['focus_baseline_eval_step']=state.get('last_checked_step',0)
    state['focus_baseline']=_focus_current(state)
    state['focus_baseline_exposure']=_focus_exposure(state)
    state.setdefault('focus_history',[]).append({'at_step':state['step'],
        'previous':copy.deepcopy(old),'next':copy.deepcopy(focus),
        'baseline_eval_step':state['focus_baseline_eval_step']})
    return True


def _focus_feedback(state,policy):
    if 'math_tasks' not in policy or 'math_levels' not in policy:
        # Reading status from a historical v2 T/HDB-only run must still work
        # before the v3 mathematical baseline has been migrated.
        return {'requested':state.get('focus',{}),
                'diagnostic_state':'legacy_frontier_not_yet_migrated'}
    focus=state.get('focus',{})
    active=_focus_labels(policy,focus)
    current=_focus_current(state)
    exposures=_focus_exposure(state)
    base=state.get('focus_baseline',{})
    base_exp=state.get('focus_baseline_exposure',{})
    return {'requested':focus,'effective_targets':active,
            'surplus_group_weights':_focus_groups(policy,focus),
            'since_optimizer_step':state.get('focus_since_step'),
            'optimizer_steps_since_focus':state['step']-state.get('focus_since_step',state['step']),
            'baseline_evaluated_at_step':state.get('focus_baseline_eval_step'),
            'targets':{label:{'weight':weight,
                'direct_examples_since_focus':exposures.get(label,0)-base_exp.get(label,0),
                'baseline':base.get(label),
                'latest':current.get(label),
                'correct_delta_on_fixed_deck':(
                    current[label]['correct']-base[label]['correct']
                    if label in base and label in current else None)}
                for label,weight in active.items()},
            'interpretation':'training exposure and measured accuracy change, not evidence of a structural defect'}

def _out(args):
    root = frontier._out(args)
    path = getattr(args, 'joint_output', None)
    return Path(path) if path else root.with_name(root.name + '_joint_t_hdb_numeric_frontier_v2')


def _policy(args):
    hp = hdb._policy(args)
    steps = int(getattr(args, 'joint_steps', 1200))
    check = int(getattr(args, 'joint_check_every', 100))
    pool_refresh = int(getattr(args, 'joint_t_refresh_every', 20))
    old = frontier._radii(args)[-1]
    outer = int(getattr(args, 'joint_t_max_radius', 0)) or min(args.operand_limit, old * 4)
    hdb_min = float(getattr(args, 'joint_hdb_min_share', 0.4))
    hdb_max = float(getattr(args, 'joint_hdb_max_share', 0.7))
    levels = int(getattr(args, 'joint_math_levels', 2))
    eval_per_level = int(getattr(args, 'joint_math_eval_per_level', 3))
    if (levels < 1 or levels > 6 or eval_per_level < 1 or eval_per_level > 32 or
        steps < 1 or check < 1 or pool_refresh < 1 or
        not old < outer <= args.operand_limit or
        not 0.2 <= hdb_min <= hdb_max <= 0.8 or
        args.batch_size < 4):
        raise ValueError('JOINT_FRONTIER_INVALID_POLICY')
    hp['steps']=steps  # One shared clock controls both frontiers.
    return {'schema':SCHEMA,'steps':steps,'check_every':check,
            't_refresh_every':pool_refresh,'t_seed_radius':old,
            't_max_radius':outer,'hdb_min_share':hdb_min,
            'hdb_max_share':hdb_max,'seed':args.seed,'batch_size':args.batch_size,
            'lr':args.lr,'max_seq':hp['max_seq'],'hdb':hp,
            'arithmetic_parent_policy':frontier._policy(args),
            'math_tasks_version':math_tasks.VERSION,
            'math_tasks':list(math_tasks.TASKS),
            'math_levels':levels,'math_eval_per_level':eval_per_level}


def _eval(args,policy):
    # All evaluation decks are fixed, independent of training/harvest data.
    low=policy['t_seed_radius']; high=policy['t_max_radius']
    count=max(36,int(getattr(args,'joint_t_eval_per_shell',72)))
    trows=frontier._deck(low,0,count,args,'JOINT-HELDOUT-INNER-V1')
    trows+=frontier._deck(high,low+1,count,args,'JOINT-HELDOUT-OUTER-V1')
    hrows=hdb._evaluation(policy['hdb'])
    chain=frontier._chain_deck(trows,args.max_rung)
    return trows,hrows,chain


def _math_labels(policy):
    return [f'{task}:L{level}' for level in range(policy['math_levels'])
            for task in policy['math_tasks']]


def _math_eval(args, policy, trows, hrows):
    """Fixed independent diagnostic corpus; actual counts expose finite classes.

    Repeated finite Boolean/probability cases cannot all be unique. Never
    describe the requested budget as the tested denominator. Reserve distinct
    source identities for training, then report the actual scored cases.
    """
    excluded={r['source'] for r in (*trows,*hrows)}
    rows=[]
    for level in range(policy['math_levels']):
        for task in policy['math_tasks']:
            gathered=0
            for attempt in range(3000):
                if gathered>=policy['math_eval_per_level']:
                    break
                index=1_000_000 + level*100_000 + attempt
                row=math_tasks.make_case(task,level,index,seed=args.seed,
                    operand_boundary=args.operand_limit,start_rung=args.max_rung)
                if row['source'] in excluded:
                    continue
                excluded.add(row['source']);rows.append(row);gathered+=1
            if not gathered:
                raise RuntimeError(f'JOINT_MATH_EVAL_NO_CASES:{task}:L{level}')
    return rows


def _math_train_row(args,policy,step,label,forbidden):
    task,lev=label.rsplit(':L',1);level=int(lev)
    # Disjoint index band from diagnostics, with exact source checks for finite
    # generators that can produce the same prompt under different indices.
    for attempt in range(3000):
        index=10_000+step*4096+attempt
        row=math_tasks.make_case(task,level,index,seed=args.seed,
            operand_boundary=args.operand_limit,start_rung=args.max_rung)
        if row['source'] not in forbidden:
            return row
    raise RuntimeError(f'JOINT_MATH_TRAIN_EXHAUSTED:{label}')


def _math_metrics(rows,before,after):
    def fresh():
        return {'tested':0,'correct':0,'gained':0,'lost':0,'net':0}
    total=fresh();by_task={};by_level={}
    for row in rows:
        k=frontier._key(row);a=bool(before[k]['correct']);b=bool(after[k]['correct'])
        buckets=(total,by_task.setdefault(row['task'],fresh()),
            by_level.setdefault(f"{row['task']}:L{row['level']}",fresh()))
        for d in buckets:
            d['tested']+=1;d['correct']+=int(b)
            d['gained']+=int(not a and b);d['lost']+=int(a and not b)
            d['net']=d['gained']-d['lost']
    for d in (total,*by_task.values(),*by_level.values()):
        d['accuracy']=d['correct']/d['tested'] if d['tested'] else None
    return {'total':total,'by_task':by_task,'by_task_level':by_level}


def _t_pool(args,policy,step,forbidden):
    count=max(72,int(getattr(args,'joint_t_train_cases_per_shell',144)))
    old=policy['t_seed_radius'];end=policy['t_max_radius']
    # Multiple frontier shells remain active. The labels rotate after each
    # refresh; no repeating a fixed 128-case pool for the entire run.
    radii=[old]
    while radii[-1]<end:
        radii.append(min(end,radii[-1]*2))
    slices=[]
    block=step//policy['t_refresh_every']
    for i,r in enumerate(radii):
        lo=0 if i==0 else radii[i-1]+1
        rows=frontier._deck(r,lo,count,args,f'JOINT-TRAIN-B{block}-R{i}')
        slices.extend(row for row in rows if row['source'] not in forbidden)
    if not slices:
        raise RuntimeError('JOINT_FRONTIER_NO_TRAINING_EXAMPLES')
    return slices


def _priority(stats,labels,exposures,step,*,focus=None,seed=0):
    # Keep the original adaptive schedule unchanged in an unfocused run.
    # With focus, multiply *sampling* probabilities by target weights while
    # retaining an absolute nonzero chance of revisiting every tier.
    if focus:
        probabilities=[]
        for label in labels:
            record=stats.get(label,{})
            correct=record.get('correct',record.get('correct_now',0))
            tested=record.get('tested',0)
            deficit=1-(correct/tested) if tested else 1
            seen=exposures.get(label,{}).get('examples',0)
            probabilities.append(_focus_weight(focus,label)*
                (0.40+deficit+0.20/(1+seen/25)))
        rng=random.Random((seed<<20) ^ ((step+1)*104729) ^ len(labels)*997)
        return rng.choices(labels,weights=probabilities,k=1)[0]
    # Weakness measured on an immutable heldout deck controls *which* fresh
    # training rows to sample. Every fourth choice explores all tiers.
    if step%4==0:
        return labels[(step//4)%len(labels)]
    def score(label):
        record=stats.get(label,{})
        correct=record.get('correct',record.get('correct_now',0)); tested=record.get('tested',0)
        accuracy=(correct/tested) if tested else 0
        seen=exposures.get(label,{}).get('examples',0)
        return (1.0-accuracy)+0.20/(1.0+seen/25.0)
    maximum=max(score(label) for label in labels)
    ties=[label for label in labels if abs(score(label)-maximum)<1e-12]
    return ties[step%len(ties)]


def _mode(state,policy,step,*,focus=None):
    if focus:
        weights=_focus_groups(policy,focus)
        rng=random.Random((policy['seed']<<20)^((step+1)*142867))
        major=rng.choices(['t-major','hdb-major','math-major'],
                    weights=[weights['t'],weights['hdb'],weights['mathematics']],k=1)[0]
        # Reporting must distinguish the old HDB/T deficit heuristic from the
        # deliberate user-selected population weight schedule.
        return major,weights['hdb']/sum(weights.values())
    # At least one third of surplus batch slots revisit the entire older
    # mathematical task frontier, rather than only T and HDB.
    if step%3==2:
        return 'math-major',1/3
    t=state.get('t_current',{}).get('by_rung',{})
    h=state.get('h_current',{}).get('by_tier',{})
    t_deficit=sum(1.0-v.get('correct',v.get('correct_now',0))/v['tested'] for v in t.values() if v['tested'])/max(1,len(t))
    h_deficit=sum(1.0-v['correct']/v['tested'] for v in h.values() if v['tested'])/max(1,len(h))
    # HDB fraction of HDB-MAJOR optimizer steps, clamped to guarantee both
    # families meaningful training. No family can capture the entire run.
    ratio=h_deficit/max(1e-9,h_deficit+t_deficit)
    share=max(policy['hdb_min_share'],min(policy['hdb_max_share'],ratio))
    return ('hdb-major' if ((step*7)%10)<round(10*share) else 't-major'),share


def _pick_t(rows,tier,rng):
    matches=[r for r in rows if r['rung']==int(tier[1:])]
    return rng.choice(matches or rows)


def _batch(args,policy,step,state,tpool,forbidden):
    rng=random.Random(args.seed ^ (step*425341))
    focus=state.get('focus',{})
    mode,share=_mode(state,policy,step,focus=focus)
    priority=lambda stats,labels,exposure,at:_priority(
        stats,labels,exposure,at,focus=focus,seed=policy['seed'])
    t_stats=state.get('t_current',{}).get('by_rung',{})
    h_stats=state.get('h_current',{}).get('by_tier',{})
    m_stats=state.get('math_current',{}).get('by_task_level',{})
    t_tier=priority(t_stats,[f'T{x:02d}' for x in range(9)],state['t_exposures'],step)
    h_tier=priority(h_stats,[f'HDB{x:02d}' for x in range(9)],state['h_exposures'],step)
    m_label=priority(m_stats,_math_labels(policy),state['math_exposures'],step)
    # Never let the T/HDB competition remove the original mathematical
    # frontier. One row of each population is present at every optimizer step.
    rows=[_pick_t(tpool,t_tier,rng),
          _math_train_row(args,policy,step,m_label,forbidden)]
    variant=rng.choice([(c,p,q) for c in hdb.TRAIN_COMMON
                        for p,q in hdb.TRAIN_SLOPES if not (c==1 and p==q==1)])
    margin=hdb._offset_limit(step,policy['steps'],policy['hdb']['max_offset'])
    k=rng.randint(1,margin)
    tier=int(h_tier[3:])
    if mode=='math-major':
        another=priority(m_stats,_math_labels(policy),state['math_exposures'],step+4)
        rows.append(_math_train_row(args,policy,step+17,another,forbidden))
        rows.append(hdb._row(tier,(-k,0,k)[step%3],variant,label=f'JOINT-HDB-S{step}'))
    elif mode=='hdb-major':
        # Two of -k/0/+k rotate on consecutive HDB-major updates; do not
        # misreport the two examples as a complete three-side balance test.
        sides=[(-k,0),(0,k),(k,-k)][step%3]
        rows.extend(hdb._row(tier,offset,variant,label=f'JOINT-HDB-S{step}')
                    for offset in sides)
    else:
        rows.append(_pick_t(tpool,priority(t_stats,
            [f'T{x:02d}' for x in range(9)],state['t_exposures'],step+rng.randint(1,10000)),rng))
        rows.append(hdb._row(tier,(-k,0,k)[step%3],variant,label=f'JOINT-HDB-S{step}'))
    while len(rows)<args.batch_size:
        # Without --focus, preserve the historical extra math behavior.
        # With --focus, EVERY surplus slot obeys the weighted population
        # schedule, including batches larger than four.
        slot=len(rows)
        extra,_=_mode(state,policy,step+slot*17,focus=focus) if focus else ('math-major',None)
        if extra=='math-major':
            label=priority(m_stats,_math_labels(policy),state['math_exposures'],step+slot*4)
            rows.append(_math_train_row(args,policy,step+slot*10,label,forbidden))
        elif extra=='t-major':
            label=priority(t_stats,[f'T{x:02d}' for x in range(9)],state['t_exposures'],step+slot*4)
            rows.append(_pick_t(tpool,label,rng))
        else:
            label=priority(h_stats,[f'HDB{x:02d}' for x in range(9)],state['h_exposures'],step+slot*4)
            rows.append(hdb._row(int(label[3:]),(-k,0,k)[(step+slot)%3],variant,
                    label=f'JOINT-HDB-S{step}-E{slot}'))
    if len(rows)!=args.batch_size:
        raise RuntimeError('JOINT_FRONTIER_BAD_MINIBATCH_SIZE')
    if any(r['source'] in forbidden for r in rows):
        raise RuntimeError('JOINT_FRONTIER_EVAL_LEAKAGE')
    return rows,mode,share


def _exposures(policy=None):
    t={f'T{x:02d}':{'examples':0,'steps':0} for x in range(9)}
    h=hdb._exposures()
    if policy is None:
        return t,h
    return t,h,{x:{'examples':0,'steps':0} for x in _math_labels(policy)}


def _add_exposures(state,rows):
    seen_t=set();seen_h=set();seen_m=set()
    for row in rows:
        if row.get('family')=='hdb':
            key=row['tier'];bucket=state['h_exposures'][key]
            bucket['examples']+=1;bucket[row['zero_side']]+=1;seen_h.add(key)
        elif row.get('task') in math_tasks.TASKS:
            key=f"{row['task']}:L{row['level']}"
            state['math_exposures'][key]['examples']+=1;seen_m.add(key)
        else:
            key=f"T{row['rung']:02d}";state['t_exposures'][key]['examples']+=1;seen_t.add(key)
    for key in seen_t:state['t_exposures'][key]['steps']+=1
    for key in seen_h:state['h_exposures'][key]['steps']+=1
    for key in seen_m:state['math_exposures'][key]['steps']+=1


def _score_t(bb,tok,rows,args):
    return frontier._score(bb,tok,rows,args,greedy=competency._diagnostic_greedy(args),
        progress=lambda done,total:frontier._emit('QWEN_JOINT_T_PROGRESS',
                                         {'done':done,'total':total}))


def _metrics(state,trows,hrows,chains,t_now,h_now,c_now):
    t_base=frontier._transitions(state['t_initial'],t_now,trows)
    t_interval=frontier._transitions(state['t_previous'],t_now,trows)
    h_base=hdb._metrics(hrows,state['h_initial'],h_now)
    h_interval=hdb._metrics(hrows,state['h_previous'],h_now)
    c_base=frontier._chain_metrics(state['c_initial'],c_now,chains)
    c_interval=frontier._chain_metrics(state['c_previous'],c_now,chains)
    return t_base,t_interval,h_base,h_interval,c_base,c_interval


def _learning_state(state, tier, correct, tested, gained, lost, direct_examples, *, family, interval_exposure=None):
    """Describe evidence of acquisition, including provisional stalls.

    The held-out deck is deliberately small; a stalled *sample* is never
    characterized as proof that a mathematical skill is unlearnable.
    """
    previous=state.setdefault('stall_checks',{}).get(tier,0)
    net=gained-lost
    exposed=direct_examples if interval_exposure is None else interval_exposure
    stalled=previous+1 if correct<tested and net<=0 and exposed>0 else (
        previous if exposed==0 and correct<tested and net==0 else 0)
    state['stall_checks'][tier]=stalled
    classification=('unmeasured' if tested==0 else
                    'regressing' if net<0 else
                    'acquiring' if net>0 else
                    'correct_on_current_deck' if correct==tested else
                    'provisionally_stalled' if stalled>=3 else
                    'not_improving_yet')
    return {'family':family,'tier':tier,'correct':correct,'tested':tested,
            'accuracy':correct/tested if tested else None,
            'interval_gained':gained,'interval_lost':lost,
            'interval_net':net,'direct_examples_cumulative':direct_examples,
            'direct_examples_this_interval':exposed,
            'nonpositive_net_checks':stalled,'classification':classification,
            'scope':'fixed heldout diagnostic only; no proof of global learning limit'}


def _save(out,state,bb,zero,opt):
    ladder._atomic_torch(out/'working.pt',{'schema':SCHEMA,'state':state,
                        'lora':zero.lora_state(bb),'optimizer':opt.state_dict()})
    ladder._atomic_json(out/'progress.json',state)


def _monitor_cadence(args):
    """Live monitoring settings do not define model geometry or eval decks."""
    heartbeat=int(getattr(args,'joint_heartbeat_every',0))
    probe=int(getattr(args,'joint_probe_every',0))
    save=int(getattr(args,'joint_save_every',0))
    if heartbeat<0 or probe<0 or save<0:
        raise ValueError('JOINT_MONITOR_INTERVAL_INVALID')
    return heartbeat,probe,save


def _probe_deck(trows,hrows,mathrows):
    """Frozen subset of the authoritative deck, with tier/side/level coverage.

    A probe is a warning signal, NEVER an optimizer scheduling update or
    replacement for fixed-deck full retention and chain auditing.
    """
    chosen=[]
    for rows,key in (
        (trows,lambda r:('T',r['radius'],r['rung'])),
        (hrows,lambda r:('HDB',r['tier'],r['zero_side'])),
        (mathrows,lambda r:('math',r['task'],r['level']))):
        seen=set(); group=[]
        for r in rows:
            k=key(r)
            if k not in seen:
                group.append(r);seen.add(k)
        chosen.append(group)
    return tuple(chosen)


def _probe_metrics(rows,scores,previous,key):
    tested=len(rows); correct=0;lost=0;gained=0
    for row in rows:
        digest=key(row)
        now=bool(scores[digest]['correct'])
        prior=bool(previous[digest]['correct'])
        correct+=int(now)
        lost+=int(prior and not now)
        gained+=int(not prior and now)
    return {'tested':tested,'correct':correct,'accuracy':correct/tested if tested else None,
            'gained_since_full_audit':gained,'lost_since_full_audit':lost,
            'net_since_full_audit':gained-lost}


def _write_heartbeat(out,state,loss):
    """Tiny on-disk indicator; NO model inference and NO optimizer serialization.

    An in-flight heartbeat does not constitute a resumable checkpoint.
    """
    heartbeat={'kind':'uncommitted_live_heartbeat', 'step':state['step'],
        'last_full_audit_step':state.get('last_checked_step',0),
        'last_loss':loss,'training_seconds':state['training_seconds'],
        'focus':state.get('focus',{}),
        'examples_seen':{
            'T':sum(v['examples'] for v in state['t_exposures'].values()),
            'HDB':sum(v['examples'] for v in state['h_exposures'].values()),
            'mathematics':sum(v['examples'] for v in state['math_exposures'].values())}}
    ladder._atomic_json(out/'joint_heartbeat.json',heartbeat)
    frontier._emit('QWEN_JOINT_HEARTBEAT',heartbeat)
    return heartbeat


def _run_probe(out,state,bb,tok,args,decks):
    t,h,m=decks
    start=time.perf_counter()
    tnow=_score_t(bb,tok,t,args)
    hnow=hdb._score_hdb(bb,tok,h,args,state['policy']['hdb'])
    mnow=_score_math(bb,tok,m,args)
    probe={'kind':'non_authoritative_fixed_deck_probe','step':state['step'],
        'last_full_audit_step':state.get('last_checked_step',0),
        'T':_probe_metrics(t,tnow,state['t_previous'],frontier._key),
        'HDB':_probe_metrics(h,hnow,state['h_previous'],lambda r:competency._digest(r['source'])),
        'mathematics':_probe_metrics(m,mnow,state['math_previous'],frontier._key),
        'chain_coverage':'none; see the next authoritative full audit',
        'interpretation':'Possible regression on sampled fixed cases only; does not update training priorities',
        'evaluation_seconds':time.perf_counter()-start}
    ladder._atomic_json(out/'joint_probe_report.json',probe)
    frontier._emit('QWEN_JOINT_PROBE',probe)
    return probe


def _status(args):
    p=_out(args)/'progress.json'
    if not p.exists():raise FileNotFoundError('JOINT_FRONTIER_NO_PROGRESS')
    state=json.loads(p.read_text(encoding='utf8'))
    heartbeat_path=_out(args)/'joint_heartbeat.json'
    live=(json.loads(heartbeat_path.read_text(encoding='utf8'))
          if heartbeat_path.is_file() else None)
    probe_path=_out(args)/'joint_probe_report.json'
    probe=(json.loads(probe_path.read_text(encoding='utf8'))
          if probe_path.is_file() else None)
    # A heartbeat can be newer than the last persisted training checkpoint.
    if live is not None and live.get('step',-1)<state['step']:
        live=None  # A resumed/recovered checkpoint supersedes an old heartbeat.
    if probe is not None and probe.get('step',-1)<state.get('last_checked_step',0):
        probe=None
    return {'status':state['status'],'step':state['step'],
            'checkpoint_step':state['step'],
            'live_heartbeat':live,'latest_probe':probe,
            'full_audit_step':state.get('last_checked_step',0),
            'monitoring_warning':'heartbeat and probe are observational, not recoverable checkpoints',
            'training_seconds':state['training_seconds'],
            'report':str(_out(args)/'joint_frontier_report.json'),
            't_exposures':state['t_exposures'],'hdb_exposures':state['h_exposures'],
            'math_exposures':state.get('math_exposures',{}),
            'focus_schedule':state.get('focus',{}),
            'focus_history':state.get('focus_history',[]),
            'focus_feedback':_focus_feedback(state,state['policy'])}


def latest_competency(args):
    """Read last scheduled three-population audit without loading or training Qwen.

    A legacy v2 report can temporarily coexist with a v3 migration baseline
    when no new optimizer steps have yet been authorized. Label that distinctly.
    """
    out=_out(args);path=out/'joint_frontier_report.json'
    if not path.exists():
        raise FileNotFoundError('JOINT_FRONTIER_NO_COMPETENCY_REPORT')
    report=json.loads(path.read_text(encoding='utf8'))
    progress=out/'progress.json';deck=out/'math_eval_deck.json'
    state=(json.loads(progress.read_text(encoding='utf8'))
           if progress.is_file() else {})
    no_math_steps=(state.get('migration_from')==LEGACY_SCHEMA and
                   state.get('step')==state.get('math_baseline_step') and
                   not any(v['examples'] for v in state.get('math_exposures',{}).values()))
    if 'mathematics' not in report or no_math_steps:
        if not deck.is_file() or 'math_initial' not in state:
            raise RuntimeError('JOINT_FRONTIER_MATH_DIAGNOSTIC_NOT_INITIALIZED')
        rows=json.loads(deck.read_text(encoding='utf8'))
        from_start=_math_metrics(rows,state['math_initial'],state['math_initial'])
        report['mathematics']={'from_baseline':from_start,
             'interval':from_start,'families':list(math_tasks.TASKS),
             'baseline_at_optimizer_step':state['math_baseline_step']}
        report['diagnostic_state']='migration_baseline_only_no_math_training_yet'
        report['step']=state['step']
    report['active_focus_schedule']=state.get('focus',{})
    if 'policy' in state:
        report['active_focus_feedback']=_focus_feedback(state,state['policy'])
    report['focus_report_is_current']=(report['step']==state.get('step') and
        report.get('focus_schedule',{})==state.get('focus',{}))
    return report


def saved_difficulty(args):
    p=_out(args)/'hdb_difficulty_report.json'
    if not p.is_file():raise FileNotFoundError('JOINT_FRONTIER_NO_DIFFICULTY_REPORT')
    report=json.loads(p.read_text(encoding='utf8'))
    frontier._emit('QWEN_JOINT_DIFFICULTY',difficulty.quick_summary(report))
    return report


def _compatible_policy(saved,policy,*,legacy=False):
    """Only allow the optimizer step budget to increase; pin other training rules."""
    if int(saved.get('steps',0))>policy['steps']:
        return False
    expected=copy.deepcopy(policy)
    expected['steps']=saved['steps']
    expected['hdb']['steps']=saved['steps']
    # Full-audit frequency is operational monitoring, not a frozen training
    # geometry/deck contract. Accept rescheduling on resume, but explicitly
    # report it. Other training policy remains pinned.
    expected['check_every']=saved['check_every']
    if legacy:
        for key in ('math_tasks_version','math_tasks','math_levels','math_eval_per_level'):
            expected.pop(key)
        expected['schema']=LEGACY_SCHEMA
    return saved==expected



HDB_SCORING_VERSION = 'hdb-complete-packet-decoding.v1'


def _training_target_integrity(tok, policy):
    """Inspect the *full* HDB teacher-forcing target under saved max_seq.

    The existing ladder._batch_loss rejects overflow instead of truncating. This
    independent preflight checks all supported training coefficient combinations
    at zero and offset extremes, without training on or probing heldout inputs.
    """
    seen=0
    highest_source=highest_target=highest_total=0
    violations=[]
    for tier in range(9):
        for common in hdb.TRAIN_COMMON:
            for p,q in hdb.TRAIN_SLOPES:
                if (common,p,q)==(1,1,1):
                    continue
                for k in (-policy['hdb']['max_offset'],0,policy['hdb']['max_offset']):
                    row=hdb._row(tier,k,(common,p,q),label='TRAIN-INTEGRITY-ONLY')
                    source_ids=tok(row['source']+'\n',add_special_tokens=False)['input_ids']
                    target_ids=tok(row['target']+'\n',add_special_tokens=False)['input_ids']
                    total=len(source_ids)+len(target_ids)
                    seen+=1
                    highest_source=max(highest_source,len(source_ids))
                    highest_target=max(highest_target,len(target_ids))
                    highest_total=max(highest_total,total)
                    if (not source_ids or not target_ids or total>policy['max_seq'] or
                        row['target']!=row['expected'] or
                        (tier<=6 and (row['complete_chain'][1]!=row['target'] or
                         row['complete_chain'][-1]!=f"PROBLEM: {row['delta']}"))):
                        violations.append({'tier':row['tier'],'k':k,'coefficients':[common,p,q],
                                           'source_tokens':len(source_ids),'target_tokens':len(target_ids),
                                           'combined_tokens':total})
    result={'rows_checked':seen,'max_source_tokens':highest_source,
            'max_target_tokens':highest_target,'max_combined_tokens':highest_total,
            'saved_training_max_seq':policy['max_seq'],
            'training_target_not_truncated_by_batch_loss':not violations,
            'violations':violations[:20],
            'scope':'synthetic TRAINING matrices only; heldout examples never enter the optimizer'}
    if violations:
        raise RuntimeError('HDB_TRAINING_TARGET_INTEGRITY_FAILED:'+json.dumps(result,sort_keys=True))
    return result


def _rescore_hdb_checkpoint(args, *, save=True, snapshot_path=None, report_dir=None):
    """Read-only scoring at the existing learned LoRA: no optimizer mutations.

    Writes distinct rescore artefacts; never edits working.pt, progress.json,
    existing competency/history JSON or previous difficulty reports.
    """
    import torch
    out=_out(args)
    cp=Path(snapshot_path) if snapshot_path is not None else out/'working.pt'
    if not cp.is_file():
        raise FileNotFoundError('HDB_RESCORE_REQUIRES_JOINT_WORKING_PT')
    reports=Path(report_dir) if report_dir is not None else out
    original_sha=frontier._sha(cp)
    obj=torch.load(cp,map_location='cpu',weights_only=True)
    state=obj['state']
    if obj.get('schema') not in (SCHEMA,LEGACY_SCHEMA):
        raise RuntimeError('HDB_RESCORE_UNKNOWN_JOINT_CHECKPOINT_SCHEMA')
    policy=state['policy']
    if state.get('parent_sha256') is None:
        raise RuntimeError('HDB_RESCORE_MISSING_PARENT_FINGERPRINT')
    rows_path=out/'hdb_eval_deck.json'
    if not rows_path.is_file():
        raise FileNotFoundError('HDB_RESCORE_REQUIRES_FROZEN_HELDOUT_DECK')
    rows=json.loads(rows_path.read_text(encoding='utf8'))
    if len(rows)!=len({r['source'] for r in rows}):
        raise RuntimeError('HDB_RESCORE_DUPLICATE_HELDOUT_SOURCE')
    intel=str(getattr(args,'device',''))=='dml'
    full_deck_sha=ladder.deck_fingerprint(rows)
    full_deck_count=len(rows)
    selected_args=args
    adapter_name=None
    if intel:
        # This is a smoke test unless the operator explicitly requests 0.
        limit=int(getattr(args,'intel_smoke_per_tier',1))
        if limit<0:raise ValueError('INTEL_DML_SMOKE_PER_TIER_MUST_BE_NONNEGATIVE')
        if limit:
            chosen=[];counts={}
            for row in rows:  # Only the existing immutable heldout deck.
                label=row['tier']
                if counts.get(label,0)<limit:
                    chosen.append(row)
                    counts[label]=counts.get(label,0)+1
            rows=chosen
        # Never let the Qwen source loader interpret "dml" as CUDA.
        # It verifies the entire model and LoRA checkpoint lineage on CPU.
        load_args=copy.copy(args)
        load_args.device='cpu'
    else:
        load_args=args
    # Load the same parent the original joint checkpoint was trained from.
    parent,parent_sha=hdb._checkpoint(load_args,policy['hdb'])
    if parent_sha!=state['parent_sha256']:
        raise RuntimeError('HDB_RESCORE_PARENT_FINGERPRINT_MISMATCH')
    bb,tok,zero,_=hdb._model(load_args,parent)
    zero.restore_lora(bb,obj['lora'])
    bb.eval()
    if intel:
        from tools import nanojev_qwen_intel_dml as intel_dml
        device,adapter_name=intel_dml.intel_device(int(getattr(args,'dml_index',-1)))
        bb=intel_dml.prepare_model_for_intel(bb,device)
        selected_args=copy.copy(args)
        selected_args.device=device
        # Small evaluation batches limit integrated-memory pressure.
        selected_args.eval_batch=1
        print('INTEL_DML_QWEN_READY='+json.dumps({
            'adapter':adapter_name,'device':str(device),
            'model':'actual Qwen3-0.6B + checkpoint LoRA',
            'source_deck_cases':full_deck_count,'selected_cases':len(rows),
            'read_only':True,'cuda_used':False},sort_keys=True),flush=True)
    training=_training_target_integrity(tok,policy)
    before=state.get('h_previous',{})
    start=time.perf_counter()
    try:
        current=hdb._score_hdb(bb,tok,rows,selected_args,policy['hdb'])
    except Exception as exc:
        if intel:
            raise RuntimeError('INTEL_DML_QWEN_INFERENCE_FAILED: '
                               f'{type(exc).__name__}: {exc}') from exc
        raise
    elapsed=time.perf_counter()-start
    legacy_score_available=all(competency._digest(r['source']) in before for r in rows)
    tier_results={}
    changes={'previously_correct_now_correct':0,'previously_wrong_now_correct':0,
             'previously_correct_now_wrong':0,'previously_wrong_still_wrong':0}
    for row in rows:
        key=competency._digest(row['source']);value=current[key]
        label=row['tier']
        bucket=tier_results.setdefault(label,{'tested':0,'correct':0,'length_exhausted':0,
                   'premature_eos':0,'exact_prefix_incomplete':0,'legacy_correct':0})
        bucket['tested']+=1;bucket['correct']+=int(value['correct'])
        bucket['length_exhausted']+=int(value['generation_truncated'])
        bucket['premature_eos']+=int(value['premature_eos'])
        bucket['exact_prefix_incomplete']+=int(value['exact_prefix'])
        if legacy_score_available:
            was=bool(before[key]['correct']);now=bool(value['correct'])
            bucket['legacy_correct']+=int(was)
            changes[('previously_correct_' if was else 'previously_wrong_')+
                    ('now_correct' if now else ('now_wrong' if was else 'still_wrong'))]+=1
    # Baseline=current here: no fictitious learning gains from regrading alone.
    difficulty_report,errors=difficulty.analyze_hdb(rows,current,current)
    summary={'schema':'nanojev.qwen.hdb-readonly-rescore.v1',
        'scoring_version':HDB_SCORING_VERSION,'checkpoint_step':state['step'],
        'last_training_audit_step':state.get('last_checked_step'),
        'checkpoint_sha256':original_sha,
        'checkpoint_source':'immutable_hardlink_snapshot' if snapshot_path is not None else 'working.pt',
        'evaluation_only':True,
        'optimizer_steps_performed':0,'model_weights_changed':False,
        'eval_deck_fingerprint':ladder.deck_fingerprint(rows),
        'full_eval_deck_fingerprint':full_deck_sha,
        'full_eval_deck_cases':full_deck_count,
        'evaluated_cases':len(rows),
        'complete_frozen_deck':len(rows)==full_deck_count,
        'smoke_only':len(rows)!=full_deck_count,
        'backend':('intel_directml' if intel else str(getattr(args,'device','cuda'))),
        'adapter':adapter_name,
        'cuda_used':False if intel else None,
        'evaluation_seconds':elapsed,'budget_rule':'max(saved_minimum,expected_target_tokens+16)',
        'decoding':'exact heldout greedy output with EOS/newline/length termination reason',
        'training_target_integrity':training,
        'tiers':tier_results,'legacy_score_available':legacy_score_available,
        'legacy_comparison_warning':'Scores differ by evaluation method; corrections are NOT learning gains',
        'legacy_vs_new_outcomes':changes if legacy_score_available else None,
        'failure_types':difficulty_report['failure_types'],
        'graded':current}
    if save:
        ladder._atomic_json(reports/'hdb_rescore_report.json',summary)
        target=reports/'hdb_rescore_failures.jsonl';temp=target.with_name(target.name+'.tmp')
        with temp.open('w',encoding='utf8') as writer:
            for error in errors:
                writer.write(json.dumps({'step':state['step'],**error},sort_keys=True)+'\n')
        temp.replace(target)
    result={k:v for k,v in summary.items() if k!='graded'}
    result['report']=str(reports/'hdb_rescore_report.json') if save else None
    result['failures']=str(reports/'hdb_rescore_failures.jsonl') if save else None
    return result


def _live_rescore_dir(args):
    """Separate rescore outputs from the actively written training workspace."""
    out=_out(args).resolve()
    root=Path(getattr(args,'rescore_output',None) or out/'concurrent_hdb_rescores').resolve()
    if root==out or root==out.parent:
        raise ValueError('HDB_LIVE_RESCORE_OUTPUT_MUST_BE_SEPARATE')
    # The snapshot MUST be on the same filesystem as working.pt so os.link
    # captures one committed, immutable inode without reading the live file.
    root.mkdir(parents=True,exist_ok=True)
    return root


def _rescore_hdb_live(args):
    """Pin an atomic working.pt version without locking the training writer.

    The trainer saves via os.replace(temp,working.pt). An NTFS hardlink to
    working.pt therefore points to either the old or the new *complete* file,
    never its partially written temporary replacement. Copying the large
    live checkpoint here would hold a Windows read handle during replacement
    and can interfere with the trainer, so there is intentionally no copy
    fallback if the filesystem does not support hardlinks.
    """
    out=_out(args)
    cp=out/'working.pt'
    if not cp.is_file():
        raise FileNotFoundError('HDB_LIVE_RESCORE_REQUIRES_JOINT_WORKING_PT')
    directory=Path(tempfile.mkdtemp(prefix='rescore_',dir=_live_rescore_dir(args)))
    snapshot=directory/'checkpoint_snapshot.pt'
    try:
        try:
            os.link(cp,snapshot)
        except OSError as exc:
            raise RuntimeError('HDB_LIVE_RESCORE_REQUIRES_HARDLINK_CAPABLE_SAME_VOLUME') from exc
        # The original training workspace is read-only for this process.
        return _rescore_hdb_checkpoint(args,snapshot_path=snapshot,report_dir=directory)
    finally:
        # Keep only the scored report and failure examples, not a retained
        # hardlink to every historical checkpoint. Results carry SHA and step.
        snapshot.unlink(missing_ok=True)


def rescore_hdb(args):
    if getattr(args,'rescore_live',False):
        result=_rescore_hdb_live(args)
    else:
        result=_rescore_hdb_checkpoint(args)
    frontier._emit('QWEN_HDB_RESCORE',result)
    return result




def _rebase_hdb_diagnostic(out, state, bb, tok, args, hrows, hchains, policy, cp_sha):
    """Rebaseline a legacy 48-token HDB audit without changing learned weights.

    Reuses a separately generated read-only rescore only if it was calculated
    from *this exact checkpoint*. Otherwise one re-evaluation is necessary.
    Legacy score snapshots remain in state for forensic comparison, but aren't
    allowed to masquerade as newly acquired competence.
    """
    sidecar=out/'hdb_rescore_report.json'
    scores=None
    used_sidecar=False
    started=time.perf_counter()
    if sidecar.is_file():
        old=json.loads(sidecar.read_text(encoding='utf8'))
        if (old.get('checkpoint_sha256')==cp_sha and
            old.get('scoring_version')==HDB_SCORING_VERSION and
            old.get('checkpoint_step')==state['step'] and
            len(old.get('graded',{}))==len(hrows)):
            scores=old['graded']
            used_sidecar=True
    if scores is None:
        scores=hdb._score_hdb(bb,tok,hrows,args,policy['hdb'])
    state['hdb_pre_correction_scores']={
        'initial':state['h_initial'],'previous':state['h_previous'],
        'last_audit_step':state.get('last_checked_step'),
        'reason':'legacy fixed 48-token HDB evaluations were not length-safe'}
    state['h_initial']=scores
    state['h_previous']=scores
    state['h_current']=hdb._metrics(hrows,scores,scores)
    # HDB chain hop budgets changed as well; compare future chains against
    # newly measured chains, not the old 48-token chain diagnostic.
    chains=hdb._score_balance_chains(bb,tok,hchains,args,policy['hdb'])
    state['hdb_pre_correction_chain_scores']={
        'initial':state['hc_initial'],'previous':state['hc_previous']}
    state['hc_initial']=chains
    state['hc_previous']=chains
    state['hdb_scoring_version']=HDB_SCORING_VERSION
    state['hdb_diagnostic_rebased_step']=state['step']
    elapsed=time.perf_counter()-started
    state['evaluation_seconds']=state.get('evaluation_seconds',0.0)+elapsed
    return {'step':state['step'],'method':'reused_readonly_rescore' if used_sidecar else 'fresh_evaluation',
            'evaluation_seconds':elapsed,
            'previous_hdb_scores_retained':True,
            'optimizer_and_lora_unchanged':True,
            'new_hdb_gain_baseline_step':state['step']}


def _score_math(bb,tok,rows,args):
    # Different task generators, identical exact textual Qwen-LM grading.
    return _score_t(bb,tok,rows,args)


def run(args):
    import torch
    policy=_policy(args)
    heartbeat_every,probe_every,save_every=_monitor_cadence(args)
    requested_focus=_parse_focus(getattr(args,'focus',None),policy)
    parent,parent_sha=hdb._checkpoint(args,policy['hdb'])
    out=_out(args)
    ladder._outputs_safe(out,[frontier._out(args),Path(args.output),
        Path(args.stage1_checkpoint).parent,Path(args.text_checkpoint).parent])
    out.mkdir(parents=True,exist_ok=True)
    cp=out/'working.pt'
    if not cp.is_file() and any(out.iterdir()):
        raise RuntimeError('JOINT_FRONTIER_REFUSES_NONEMPTY_UNKNOWN_WORKSPACE')
    bb,tok,zero,ps=hdb._model(args,parent)
    trows,hrows,chains=_eval(args,policy)
    mathrows=_math_eval(args,policy,trows,hrows)
    old_eval_sha=ladder.deck_fingerprint(trows)+':'+ladder.deck_fingerprint(hrows)
    eval_sha=old_eval_sha+':'+ladder.deck_fingerprint(mathrows)
    hchains=hdb._chain_deck(hrows)
    if cp.exists():
        obj=torch.load(cp,map_location='cpu',weights_only=True)
        state=obj['state']
        legacy=obj.get('schema')==LEGACY_SCHEMA
        if (obj.get('schema') not in (SCHEMA,LEGACY_SCHEMA) or
            not _compatible_policy(state['policy'],policy,legacy=legacy) or
            state['parent_sha256']!=parent_sha or
            state['eval_sha256']!=(old_eval_sha if legacy else eval_sha)):
            raise RuntimeError('JOINT_FRONTIER_RESUME_SOURCE_POLICY_OR_DECK_DRIFT')
        zero.restore_lora(bb,obj['lora'])
        previous_check_every=state['policy']['check_every']
        opt=frontier._optimizer(ps,args,obj['optimizer'])
        if (state.get('hdb_scoring_version')!=HDB_SCORING_VERSION and
                state['step']<policy['steps']):
            bb.eval()
            correction=_rebase_hdb_diagnostic(out,state,bb,tok,args,hrows,hchains,policy,
                                              frontier._sha(cp))
            _save(out,state,bb,zero,opt)
            frontier._emit('QWEN_HDB_DIAGNOSTIC_REBASE',correction)
        if legacy:
            # Preserve the complete learned LoRA/optimizer from the T+HDB run.
            # The missing math baseline is *now*, not falsely claimed as step 0.
            preserved=out/'pre_math_frontier_migration.pt'
            if not preserved.exists():
                ladder._atomic_torch(preserved,obj)
            bb.eval()
            initial_math=_score_math(bb,tok,mathrows,args)
            state.update(schema=SCHEMA,policy=policy,eval_sha256=eval_sha,
                math_initial=initial_math,math_previous=initial_math,
                math_current=_math_metrics(mathrows,initial_math,initial_math),
                math_exposures=_exposures(policy)[2],
                math_baseline_step=state['step'],
                last_math_exposures={})
            state.setdefault('modes',{})['math-major']=0
            state['migration_from']=LEGACY_SCHEMA
            if state['step']<policy['steps']:
                state['status']='TRAINING'
            ladder._atomic_json(out/'math_eval_deck.json',mathrows)
            _save(out,state,bb,zero,opt)
            frontier._emit('QWEN_JOINT_MATH_MIGRATION',{
                'step':state['step'],'preserved':str(preserved),
                'mathematical_baseline_at_step':state['math_baseline_step'],
                'continuation_steps':policy['steps']-state['step']})
        elif state['policy']['steps']<policy['steps']:
            state['policy']=policy
            state['status']='TRAINING'
            _save(out,state,bb,zero,opt)
        if previous_check_every!=policy['check_every']:
            state['policy']=policy
            _save(out,state,bb,zero,opt)
            frontier._emit('QWEN_JOINT_AUDIT_CADENCE_CHANGED',{
                'step':state['step'],'previous_every':previous_check_every,
                'next_every':policy['check_every'],
                'evaluation_decks_unchanged':True})
        if requested_focus is not None and requested_focus != state.get('focus',{}):
            # A mutable schedule switch is not an optimizer restart. Keep an
            # immutable copy of the full previous working checkpoint.
            revisions=len(state.get('focus_history',[]))
            backup=out/'focus_snapshots'/f'before_step_{state["step"]:08d}_revision_{revisions:03d}.pt'
            ladder._atomic_torch(backup,torch.load(cp,map_location='cpu',weights_only=True))
            _activate_focus(state,policy,requested_focus)
            _save(out,state,bb,zero,opt)
            frontier._emit('QWEN_JOINT_FOCUS_CHANGED',{
                'at_step':state['step'],'focus':state['focus'],
                'snapshot':str(backup),'optimizer_preserved':True})
    else:
        opt=frontier._optimizer(ps,args,parent['optimizer'])
        bb.eval()
        frontier._emit('QWEN_JOINT_BASELINE_START',{'t':len(trows),
                       'hdb':len(hrows),'chains':len(chains),
                       'hdb_balance_chains':len(hchains)})
        first=time.perf_counter()
        t0=_score_t(bb,tok,trows,args)
        m0=_score_math(bb,tok,mathrows,args)
        h0=hdb._score_hdb(bb,tok,hrows,args,policy['hdb'])
        frontier._emit('QWEN_JOINT_CHAIN_START',{'cases':len(chains)})
        c0=frontier._chain_score(bb,tok,chains,args,greedy=competency._diagnostic_greedy(args))
        hc0=hdb._score_balance_chains(bb,tok,hchains,args,policy['hdb'])
        texp,hexp,mexp=_exposures(policy)
        state={'schema':SCHEMA,'policy':policy,'parent_sha256':parent_sha,
               'eval_sha256':eval_sha,'step':0,'status':'TRAINING',
               't_initial':t0,'t_previous':t0,'h_initial':h0,'h_previous':h0,
               'c_initial':c0,'c_previous':c0,'hc_initial':hc0,'hc_previous':hc0,
               't_current':frontier._transitions(t0,t0,trows),
               'h_current':hdb._metrics(hrows,h0,h0),
               't_exposures':texp,'h_exposures':hexp,'math_exposures':mexp,
               'math_initial':m0,'math_previous':m0,
               'math_current':_math_metrics(mathrows,m0,m0),
               'math_baseline_step':0,
               'training_seconds':0.,'evaluation_seconds':time.perf_counter()-first,
               'last_checked_step':0,'last_training_seconds':0.,
               'modes':{'t-major':0,'hdb-major':0,'math-major':0},
               'focus':{},'focus_history':[],
               'hdb_scoring_version':HDB_SCORING_VERSION}
        if requested_focus:
            _activate_focus(state,policy,requested_focus)
        ladder._atomic_json(out/'t_eval_deck.json',trows)
        ladder._atomic_json(out/'hdb_eval_deck.json',hrows)
        ladder._atomic_json(out/'math_eval_deck.json',mathrows)
        _save(out,state,bb,zero,opt)
        frontier._emit('QWEN_JOINT_FRONTIER_START',{'output':str(out),
             'arithmetic_parent_step':parent['state']['phase_step'],
             't_radius_range':[policy['t_seed_radius'],policy['t_max_radius']],
             'hdb_tiers':[f'HDB{i:02d}' for i in range(9)],
             'training':'T, HDB numeric balance, and nine mathematical task families in same optimizer steps; all active throughout',
             'math_families':list(math_tasks.TASKS),
             'hdb_target_contract':'PROBLEM: signed integer; no strategy classifier'})
    if state['status']=='COMPLETE':
        frontier._emit('QWEN_JOINT_FRONTIER_DONE',{'already_complete':True,'step':state['step']})
        return state
    forbidden={r['source'] for r in [*trows,*hrows,*mathrows]}
    probe_decks=_probe_deck(trows,hrows,mathrows) if probe_every else None
    pool=[];block=-1
    trainargs=copy.copy(args)
    trainargs.max_seq=policy['max_seq']
    for step in range(state['step'],policy['steps']):
        b=step//policy['t_refresh_every']
        if b!=block:
            pool=_t_pool(args,policy,step,forbidden)
            block=b
        rows,mode,share=_batch(args,policy,step,state,pool,forbidden)
        bb.train(); start=time.perf_counter()
        loss=ladder._train_step(bb,tok,opt,rows,trainargs)
        if str(args.device).startswith('cuda'):torch.cuda.synchronize()
        state['training_seconds']+=time.perf_counter()-start
        bb.eval();state['step']=step+1
        _add_exposures(state,rows)
        state['modes'][mode]+=1
        full_due=(state['step']%policy['check_every']==0 or state['step']==policy['steps'])
        if heartbeat_every and state['step']%heartbeat_every==0 and not full_due:
            _write_heartbeat(out,state,loss)
        if probe_every and state['step']%probe_every==0 and not full_due:
            _run_probe(out,state,bb,tok,args,probe_decks)
        if save_every and state['step']%save_every==0 and not full_due:
            _save(out,state,bb,zero,opt)
            frontier._emit('QWEN_JOINT_LIGHT_CHECKPOINT',{
                'step':state['step'],'last_full_audit_step':state['last_checked_step'],
                'reason':'periodic_unscored_checkpoint',
                'checkpoint':str(out/'working.pt')})
        if not full_due:
            continue
        tick=time.perf_counter()
        frontier._emit('QWEN_JOINT_EVAL_START',{'step':step+1,'t_cases':len(trows),
                     'hdb_cases':len(hrows),'math_cases':len(mathrows),
                     'chain_cases':len(chains)})
        tnow=_score_t(bb,tok,trows,args)
        mnow=_score_math(bb,tok,mathrows,args)
        hnow=hdb._score_hdb(bb,tok,hrows,args,policy['hdb'])
        frontier._emit('QWEN_JOINT_CHAIN_START',{'step':step+1,'cases':len(chains)})
        cnow=frontier._chain_score(bb,tok,chains,args,
                                  greedy=competency._diagnostic_greedy(args))
        hcnow=hdb._score_balance_chains(bb,tok,hchains,args,policy['hdb'])
        dt=time.perf_counter()-tick
        state['evaluation_seconds']+=dt
        tb,ti,hb,hi,cb,ci=_metrics(state,trows,hrows,chains,tnow,hnow,cnow)
        mb=_math_metrics(mathrows,state['math_initial'],mnow)
        mi=_math_metrics(mathrows,state['math_previous'],mnow)
        hcb=hdb._chain_metrics(hchains,state['hc_initial'],hcnow)
        hci=hdb._chain_metrics(hchains,state['hc_previous'],hcnow)
        interval_steps=state['step']-state['last_checked_step']
        elapsed=state['training_seconds']-state['last_training_seconds']
        rates={'t_net_per_100_steps':100*ti['net']/interval_steps,
               'hdb_net_per_100_steps':100*hi['total']['net']/interval_steps,
               'chain_net_per_100_steps':100*ci['net']/interval_steps,
               'math_net_per_100_steps':100*mi['total']['net']/interval_steps,
               't_net_per_training_hour':3600*ti['net']/max(elapsed,1e-9),
               'hdb_net_per_training_hour':3600*hi['total']['net']/max(elapsed,1e-9),
               'math_net_per_training_hour':3600*mi['total']['net']/max(elapsed,1e-9)}
        # Record per-tier exposure and acquisition rate, not just global accuracy.
        t_velocities={}
        for tier,metric in ti['by_rung'].items():
            cur=state['t_exposures'][tier]['examples']
            prev=state.get('last_t_exposures',{}).get(tier,{}).get('examples',0)
            t_velocities[tier]={'net_since_last_check':metric['net'],
                'net_per_100_direct_examples':100*metric['net']/max(1,cur-prev),
                'interval_training_examples':cur-prev,
                'net_per_training_hour':3600*metric['net']/max(elapsed,1e-9),
                'cumulative_training_examples':cur}
        velocities={}
        for tier,metric in hi['by_tier'].items():
            cur=state['h_exposures'][tier]['examples']
            prev=state.get('last_h_exposures',{}).get(tier,{}).get('examples',0)
            velocities[tier]={'net_since_last_check':metric['net'],
                'net_per_100_direct_examples':100*metric['net']/max(1,cur-prev),
                'interval_training_examples':cur-prev,
                'net_per_training_hour':3600*metric['net']/max(elapsed,1e-9),
                'cumulative_training_examples':cur}
        math_velocities={}
        for label,metric in mi['by_task_level'].items():
            cur=state['math_exposures'][label]['examples']
            prev=state.get('last_math_exposures',{}).get(label,{}).get('examples',0)
            math_velocities[label]={'net_since_last_check':metric['net'],
                'net_per_100_direct_examples':100*metric['net']/max(1,cur-prev),
                'interval_training_examples':cur-prev,
                'cumulative_training_examples':cur}
        difficult,he,te=difficulty.summarize(hrows,trows,state['h_initial'],hnow,
            state['t_initial'],tnow,interval=hi['by_tier'],
            exposures=state['h_exposures'],rates=velocities,step=state['step'])
        tier_learning={}
        for tier,metric in ti['by_rung'].items():
            tier_learning[tier]=_learning_state(state,tier,metric['correct_now'],
                metric['tested'],metric['gained'],metric['lost'],
                state['t_exposures'][tier]['examples'],family='T',
                interval_exposure=t_velocities[tier]['interval_training_examples'])
        for tier,metric in hi['by_tier'].items():
            tier_learning[tier]=_learning_state(state,tier,metric['correct'],
                metric['tested'],metric['gained'],metric['lost'],
                state['h_exposures'][tier]['examples'],family='HDB',
                interval_exposure=velocities[tier]['interval_training_examples'])
        for label,metric in mi['by_task_level'].items():
            tier_learning[label]=_learning_state(state,label,metric['correct'],
                metric['tested'],metric['gained'],metric['lost'],
                state['math_exposures'][label]['examples'],family='mathematics',
                interval_exposure=math_velocities[label]['interval_training_examples'])
        # Scores from all three populations drive the next training batch.
        state['t_current']={'by_rung':tb['by_rung']}
        state['h_current']={'by_tier':hb['by_tier']}
        state['math_current']={'by_task_level':mb['by_task_level']}
        state['t_previous']=tnow;state['h_previous']=hnow;state['math_previous']=mnow
        state['c_previous']=cnow
        state['hc_previous']=hcnow
        state['last_checked_step']=state['step']
        state['last_training_seconds']=state['training_seconds']
        state['last_h_exposures']=copy.deepcopy(state['h_exposures'])
        state['last_t_exposures']=copy.deepcopy(state['t_exposures'])
        state['last_math_exposures']=copy.deepcopy(state['math_exposures'])
        report={'schema':SCHEMA,'step':state['step'],
             't':{'from_start':tb,'interval':ti},
             'hdb':{'from_start':hb,'interval':hi},
             'mathematics':{'from_baseline':mb,'interval':mi,
                'baseline_at_optimizer_step':state['math_baseline_step'],
                'families':list(math_tasks.TASKS)},
             'chains':{'from_start':cb,'interval':ci},
             'hdb_numeric_balance_chains':{'from_start':hcb,'interval':hci},
             'learning_rates':rates,'t_rung_velocity':t_velocities,
             'hdb_tier_velocity':velocities,
             'math_task_velocity':math_velocities,
             'per_tier_learning_state':tier_learning,
             'training_exposure':{'t':state['t_exposures'],'hdb':state['h_exposures'],
                                  'mathematics':state['math_exposures']},
             'focus_schedule':state.get('focus',{}),
             'focus_feedback':_focus_feedback(state,policy),
             'focus_history':state.get('focus_history',[]),
             'optimizer_step_modes':state['modes'],
             'hdb_scoring_version':state.get('hdb_scoring_version'),
             'hdb_diagnostic_rebased_step':state.get('hdb_diagnostic_rebased_step'),
             'training_seconds':state['training_seconds'],
             'evaluation_seconds_cumulative':state['evaluation_seconds'],
             'last_loss':loss,'t_max_radius':policy['t_max_radius'],
             'hdb_max_offset_now':hdb._offset_limit(step,policy['steps'],policy['hdb']['max_offset']),
             'weakness_report':'hdb_difficulty_report.json'}
        if state['step']==policy['steps']:
            state['status']='COMPLETE'
        _save(out,state,bb,zero,opt)
        if heartbeat_every:
            _write_heartbeat(out,state,loss)
        ladder._atomic_json(out/'joint_frontier_report.json',report)
        difficulty.write_report(out,difficult,he,te)
        frontier._append(out/'joint_frontier_history.jsonl',report)
        frontier._emit('QWEN_JOINT_WEAKNESSES',{
            'step':state['step'],
            'provisional_stalls':[v for v in tier_learning.values()
                if v['classification']=='provisionally_stalled'],
            'regressions':[v for v in tier_learning.values()
                if v['classification']=='regressing'],
            'weakest_t':difficult['t_retention']['ranked_retention_risks'][:3],
            'weakest_hdb':difficult['hdb']['ranked_tiers'][:3],
            'weakest_mathematics':sorted(mb['by_task_level'].items(),
                key=lambda item:(item[1]['accuracy'],item[0]))[:3],
            'zero_line':difficulty.quick_summary(difficult)['zero_line_accuracy'],
            'report':str(out/'joint_frontier_report.json')})
        frontier._emit('QWEN_JOINT_FRONTIER_CHECK',{
          'step':state['step'],'t_net':tb['net'],'t_interval_net':ti['net'],
          'hdb_net':hb['total']['net'],'hdb_interval_net':hi['total']['net'],
          'hdb_full_balance_correct':hcb['correct'],
          'hdb_full_balance_tested':hcb['tested'],
          'chain_correct':cb['strict_correct'],'chain_tested':cb['tested'],
          'weakest_hdb':difficult['hdb']['ranked_tiers'][:3],
          'weakest_t':difficult['t_retention']['ranked_retention_risks'][:3],
          't_examples':sum(x['examples'] for x in state['t_exposures'].values()),
          'hdb_examples':sum(x['examples'] for x in state['h_exposures'].values()),
          'math_examples':sum(x['examples'] for x in state['math_exposures'].values()),
          'math_net':mb['total']['net'],'math_interval_net':mi['total']['net'],
          'math_correct':mb['total']['correct'],'math_tested':mb['total']['tested'],
          'training_seconds':round(state['training_seconds'],2),
          'evaluation_seconds':round(dt,2),
          'hdb_major_share_target':round(share,3),
          'focus':state.get('focus',{}),
          'focus_feedback':_focus_feedback(state,policy)})
    if state['status']!='COMPLETE':
        state['status']='COMPLETE'
        _save(out,state,bb,zero,opt)
    frontier._emit('QWEN_JOINT_FRONTIER_DONE',{'step':state['step'],
         'report':str(out/'joint_frontier_report.json')})
    return state
