#!/usr/bin/env python3
"""Progressive arithmetic-memory frontier by autonomous, donor-free targeted repair.

No held-out DEV/TEST split: arithmetic labels are exact and generated as needed.
Every previously correct audited calculation is protected against regression.
An exhaustive census proves completeness only within the explicitly defined
signed cancellation family; large ranges use reproducible Monte Carlo audits
and are labelled SAMPLE_ONLY, never claimed fully mastered.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
from pathlib import Path

import torch

from tools import nanojev_emp_autodiscover_repair as auto
from tools import nanojev_emp_donor_free_repair as donor
from tools import nanojev_emp_defect_cross_transfer as cross
from tools import nanojev_emp_hard_lasso as lasso
from tools import nanojev_emp_isolated_defects as iso
from tools.nanojev_emp_hard_case_bank import HardCaseBank
from tools.nanojev_zero_line_zero_only import MAX_ABS
from tools.nanojev_zero_line_ladder import atomic_json, atomic_checkpoint

SCHEMA = 'nanojev.emp-progressive-memory-frontier.v1'


def row(a, b):
    return {'a': int(a), 'b': int(b), 'target': int(a+b)}


def pair_key(r):
    return (r['a'], r['b'])


def domain_bound(radius, delta):
    """Conservative candidate count; exact count is measured if enumerated."""
    # Exact cardinality for the opposite-sign/zero cancellation domain.
    # For residual 0: 2*r+1; for each +/-d: 4*(r-d+1).
    m=min(radius,delta)
    return 2*radius+1+4*(m*(radius+1)-m*(m+1)//2)


def enumerate_domain(radius, delta, lower=0):
    """All ordered opposite-sign/zero near-cancellation cards up to radius.

    The shell checks actual maximum operand magnitude, including boundary
    cases where the second operand is larger than the nominal anchor m.
    """
    if not (0 <= lower < radius <= MAX_ABS) or delta < 0:
        raise ValueError('FRONTIER_INVALID_DOMAIN')
    result = {(0,0): row(0,0)} if lower == 0 else {}
    for m in range(max(1,lower-delta),radius+1):
        for d in range(-delta,delta+1):
            for sign in (-1,1):
                a=sign*m
                b=d-a
                for x,y in ((a,b),(b,a)):
                    if x*y>0 or not (lower < max(abs(x),abs(y)) <= radius):
                        continue
                    result[(x,y)] = row(x,y)
    return [result[k] for k in sorted(result)]


def sampled_domain(radius, delta, lower, count, seed, wave):
    """Reproducible independent seeded draws, stratified across signs and residuals.

    No folds, no permanently withheld problems. The wave identifier changes
    the PRNG stream and makes the sampling audit reproducible.
    """
    if count<1 or not (0<=lower<radius<=MAX_ABS):
        raise ValueError('FRONTIER_INVALID_SAMPLE')
    rng=random.Random(f'frontier:{seed}:{wave}:{lower}:{radius}:{delta}')
    cards={}
    # Fixed edge landmarks give each wave hard boundary cases.
    anchors=[max(1,lower-delta),max(1,lower),lower+1,radius-1,radius]
    deltas=list(dict.fromkeys([0,1,-1,2,-2,delta,-delta]))
    for m in anchors:
        if not (1<=m<=radius): continue
        for d in deltas:
            if abs(d)>delta: continue
            for sign in (-1,1):
                for swap in (False,True):
                    a=sign*m;b=d-a
                    if swap:a,b=b,a
                    if a*b<=0 and lower < max(abs(a),abs(b))<=radius:
                        cards[(a,b)]=row(a,b)
    if len(cards)>count:
        keys=sorted(cards)
        # Preserve both extremes while limiting the deterministic anchors.
        cards={k:cards[k] for k in keys[:count]}
    attempts=0
    while len(cards)<count and attempts<count*250:
        attempts+=1
        m=rng.randint(max(1,lower-delta),radius)
        d=rng.randint(-delta,delta)
        sign=-1 if rng.randrange(2) else 1
        a=sign*m;b=d-a
        if rng.randrange(2):a,b=b,a
        if a*b<=0 and lower < max(abs(a),abs(b))<=radius:
            cards.setdefault((a,b),row(a,b))
    return [cards[k] for k in sorted(cards)]


def audit_cards(radius, delta, cap, count, seed, wave, lower=0):
    if domain_bound(radius,delta)<=cap:
        items=enumerate_domain(radius,delta,lower)
        return items, {'coverage':'exhaustive','cases':len(items),'wave':wave}
    items=sampled_domain(radius,delta,lower,count,seed,wave)
    return items, {'coverage':'monte_carlo','cases':len(items),'wave':wave,
                   'draws_requested':count,'domain_size_upper_bound':domain_bound(radius,delta)}



def scheduled_audit(args, radius, wave, bank):
    """Exhaustive when cheap; otherwise blend bank, shell and exploration.

    The bank records past trouble, but a permanently nonzero fresh draw
    budget prevents fixation on known errors. The audit is NOT an oracle over
    unenumerated arithmetic pairs.
    """
    count=domain_bound(radius,args.max_delta)
    if count<=args.census_cap:
        cards,meta=audit_cards(radius,args.max_delta,args.census_cap,
                               args.audit_size,args.seed,wave)
        return cards,{**meta,'hard_cases_injected':0,'exploration_cards':len(cards),
                      'shell_cards':0}
    bank_limit=min(args.hard_audit_cards,max(0,args.audit_size//3))
    hard=bank.prioritized(radius,args.max_delta,bank_limit,seed=args.seed,wave=wave)
    # At least one third of the budget explores the full radius.
    remaining=args.audit_size-len(hard)
    shell_count=min(args.shell_audit_cards,remaining//2)
    global_count=remaining-shell_count
    exploration=sampled_domain(radius,args.max_delta,0,global_count,args.seed,wave)
    shell=sampled_domain(radius,args.max_delta,max(0,radius-args.radius_step),
                         shell_count,args.seed,wave+100000) if shell_count else []
    cards=merge(hard,exploration,shell)
    return cards,{'coverage':'monte_carlo','cases':len(cards),'wave':wave,
                 'domain_size_exact':count,'hard_cases_injected':len(hard),
                 'exploration_cards':len(exploration),'shell_cards':len(shell)}


def verify_protected_core(model, before, candidate, radius, args):
    """Prior fully certified core remains an exact constraint even if frontier sampled."""
    if not radius:
        return True,{'cards':0,'new_regressions':0}
    size=domain_bound(radius,args.max_delta)
    if size>args.protected_core_cap:
        raise RuntimeError('FRONTIER_PROTECTED_CORE_TOO_LARGE:increase --protected-core-cap')
    cards=enumerate_domain(radius,args.max_delta)
    try:
        model.load_state_dict(before,strict=True)
        old=donor.predictions(model,cards,args.device,args.eval_batch)
        model.load_state_dict(candidate,strict=True)
        new=donor.predictions(model,cards,args.device,args.eval_batch)
        result=compare_predictions(cards,old,new)
        return result['new_regressions']==0 and result['new_errors']==0,result
    finally:
        model.load_state_dict(candidate,strict=True)


def merge(*groups):
    result={}
    for group in groups:
        for r in group: result[pair_key(r)]=r
    return [result[k] for k in sorted(result)]


def compare_predictions(rows,old,new):
    if len(rows)!=len(old) or len(old)!=len(new):
        raise RuntimeError('FRONTIER_PREDICTION_LENGTH_DRIFT')
    previous=sum(p==r['target'] for r,p in zip(rows,old))
    current=sum(p==r['target'] for r,p in zip(rows,new))
    regress=sum(p==r['target'] and n!=r['target'] for r,p,n in zip(rows,old,new))
    repairs=sum(p!=r['target'] and n==r['target'] for r,p,n in zip(rows,old,new))
    return {'cards':len(rows),'old_correct':previous,'new_correct':current,
            'old_errors':len(rows)-previous,'new_errors':len(rows)-current,
            'new_regressions':regress,'new_repairs':repairs}


def choose_cluster(rows, preds, limit, seed):
    failing=[r for r,p in zip(rows,preds) if p!=r['target']]
    if not failing:return []
    rng=random.Random(f'frontier-cluster:{seed}:{len(failing)}')
    center=rng.choice(failing)
    def branch(r):
        return (int(r['a']<0),int(abs(r['a'])>=abs(r['b'])),int(r['b']<0))
    # Prefer related nearby failures, but never assume they share a cause.
    ordered=sorted(failing,key=lambda r: (branch(r)!=branch(center),
        abs(r['target']-center['target']),
        abs(max(abs(r['a']),abs(r['b']))-max(abs(center['a']),abs(center['b']))),
        abs(r['a']-center['a'])+abs(r['b']-center['b'])))
    return ordered[:limit]


def safeguard(current_rows,current_preds,candidate_preds,required,minimum_gain=1):
    measured=compare_predictions(current_rows,current_preds,candidate_preds)
    return (measured['new_regressions']==0 and measured['new_repairs']>=minimum_gain and
            all(candidate_preds[i]==current_rows[i]['target'] for i in required)),measured


def make_preservation(rows,predictions,defects,limit,seed):
    good=[r for r,p in zip(rows,predictions) if p==r['target']]
    if not good:
        raise RuntimeError('FRONTIER_NO_EXISTING_CORRECT_MEMORIES')
    x=defects[0]
    near=sorted(good,key=lambda r:abs(r['a']-x['a'])+abs(r['b']-x['b']))[:min(64,limit)]
    random_choices=random.Random(f'frontier-controls:{seed}').sample(good,min(limit,len(good)))
    return merge(near,random_choices)



def scaled_candidate(original, trained, editable_keys, scale):
    """Interpolate an edit from the *current* certified/working model.

    No donor checkpoint is read. All frozen tensors remain bit-identical.
    """
    if not 0 < scale <= 1 or set(original) != set(trained):
        raise ValueError('FRONTIER_INVALID_REPAIR_SCALE')
    output={k:v.clone() for k,v in original.items()}
    for k in editable_keys:
        if k not in original or original[k].shape != trained[k].shape:
            raise RuntimeError('FRONTIER_EDITABLE_TENSOR_DRIFT')
        output[k]=original[k]+(trained[k]-original[k])*scale
    return output


def new_regression_rows(cards, previous, candidate, excluded=()):
    """Mine exact counterexamples: formerly memorized answers lost by this edit."""
    if len(cards)!=len(previous) or len(cards)!=len(candidate):
        raise RuntimeError('FRONTIER_REPLAY_LENGTH_DRIFT')
    known=set(excluded)
    return [r for r,old,new in zip(cards,previous,candidate)
            if old==r['target'] and new!=r['target'] and pair_key(r) not in known]


def weighted_rehearsal(base_rows, hard_rows, multiplier):
    """Oversample newly threatened memories rather than treating them as TEST."""
    if multiplier < 1:
        raise ValueError('FRONTIER_INVALID_REPLAY_MULTIPLIER')
    base=merge(base_rows)
    # A threatened card that is already present in the base sample still
    # needs to receive extra weight; duplicate rows are intentional here.
    return base+list(hard_rows)*multiplier


def capture_baseline_teacher(model, baseline, rehearsal, args):
    """Capture output preservation targets from the pre-edit model only."""
    working=state_copy(model)
    try:
        model.load_state_dict(baseline,strict=True)
        return donor.capture_teacher(model,rehearsal,args.device,args.eval_batch)
    finally:
        model.load_state_dict(working,strict=True)


def probe_scales(model, original, trained, editable_keys, census, previous,
                 defects, controls, args):
    """Trust-region backtracking over an independently trained correction.

    Evaluate all remembered arithmetic on every accepted line-search candidate.
    Never relax the no-regression rule or compare only an isolated defect.
    """
    reference={pair_key(r):r['target'] for r in defects}
    chosen=None;best=None;checks=[]
    try:
        for scale in args.repair_scales:
            trial=scaled_candidate(original,trained,editable_keys,scale)
            model.load_state_dict(trial,strict=True)
            quick=merge(defects,controls)
            quick_p=donor.predictions(model,quick,args.device,args.eval_batch)
            bykey={pair_key(r):p for r,p in zip(quick,quick_p)}
            if any(bykey.get(k)!=value for k,value in reference.items()) or any(
                    bykey.get(pair_key(r))!=r['target'] for r in controls):
                checks.append({'scale':scale,'qualified':False,'quick_failed':True})
                continue
            predicted=donor.predictions(model,census,args.device,args.eval_batch)
            required={i for i,r in enumerate(census) if pair_key(r) in reference}
            passed,metric=safeguard(census,previous,predicted,required)
            checks.append({'scale':scale,'qualified':passed,'quick_failed':False,**metric})
            if passed:
                norm=math.sqrt(sum(float((trial[k]-original[k]).square().sum())
                                   for k in editable_keys))
                rank=(-metric['new_repairs'],auto.delta_counts(original,trial,editable_keys),norm)
                if best is None or rank<best:
                    best=rank;chosen=(trial,metric,scale)
    finally:
        model.load_state_dict(trained,strict=True)
    return chosen,checks

def try_repair(model, original, census, old_preds, defects, args, seed, wave):
    """Counterexample-guided, donor-free repair with a strict memory contract.

    Unlike the initial runner, a rejected candidate is *not* discarded as
    uninformative. Previously correct answers it broke are added to a weighted
    rehearsal bank. The bank is only a training aid: the full census remains
    the acceptance oracle. Backtracking also tests smaller edits.
    """
    correct=make_preservation(census,old_preds,defects,args.preserve_cards,seed)
    bank=getattr(args,'_hard_bank',None)
    if bank is not None:
        prioritized=bank.prioritized(max(max(abs(r['a']),abs(r['b'])) for r in census),
                          args.max_delta,args.replay_hard_limit,seed=args.seed,wave=wave)
        correct_keys={pair_key(r) for r,p in zip(census,old_preds) if p==r['target']}
        bank_rehearsal=[r for r in prioritized if pair_key(r) in correct_keys]
        correct=merge(correct,bank_rehearsal)
    groups,scores,ranked=auto.gradient_discovery(model,defects,
                            correct[:args.discovery_controls],args.device,args.eval_batch)
    if not ranked:return None,{'status':'no_gradient_groups','ranked_groups':[]}
    budgets=auto.expansion_schedule(args.expand,len(ranked),args.max_groups)
    required={i for i,r in enumerate(census) if pair_key(r) in {pair_key(d) for d in defects}}
    all_attempts=[]
    hard={}  # Persistent across parameter budgets; every entry was correct before this transaction.
    correctly_remembered={pair_key(r) for r,p in zip(census,old_preds) if p==r['target']}
    for budget in budgets:
        selected=ranked[:budget]
        params=auto.select_editable(model,original,groups,selected)
        if sum(p.numel() for p in params.values())>args.max_candidate_elements:
            all_attempts.append({'groups':selected,'status':'over_budget'})
            continue
        optimizer=torch.optim.Adam(params.values(),lr=args.lr)
        rng=random.Random(f'frontier-fit:{args.seed}:{seed}:{wave}:{budget}')
        best_state=None;best_metric=None;best_scale=None
        checks=[]
        keys=set(params)
        rehearsal=weighted_rehearsal(correct,list(hard.values()),args.replay_multiplier)
        teacher=capture_baseline_teacher(model,original,rehearsal,args)
        for step in range(1,args.search_steps+1):
            metrics=auto.step(model,optimizer,params,original,defects,rehearsal,teacher,
                              rng,args,args.device)
            if step%args.check_every and step!=args.search_steps:continue
            quick=merge(defects,correct,hard.values())
            guess=donor.predictions(model,quick,args.device,args.eval_batch)
            bykey={pair_key(r):p for r,p in zip(quick,guess)}
            defects_ok=all(bykey[pair_key(r)]==r['target'] for r in defects)
            controls_ok=all(bykey[pair_key(r)]==r['target'] for r in quick if
                            pair_key(r) in correctly_remembered)
            note={'step':step,'fixed':sum(bykey[pair_key(r)]==r['target'] for r in defects),
                  'controls_ok':controls_ok,'groups':selected,'loss':metrics['loss'],
                  'replay_hard_count':len(hard),'replay_cards':len(rehearsal)}
            newly=[]
            if defects_ok:
                trained=state_copy(model)
                if controls_ok:
                    fresh=donor.predictions(model,census,args.device,args.eval_batch)
                    passed,snapshot_metric=safeguard(census,old_preds,fresh,required)
                    note.update(snapshot_metric)
                    if passed:
                        norm=math.sqrt(sum(float((trained[k]-original[k]).square().sum()) for k in keys))
                        rank=(-snapshot_metric['new_repairs'],auto.delta_counts(original,trained,keys),norm,step)
                        if best_metric is None or rank<best_metric:
                            best_metric=rank;best_state=trained;best_scale=1.0
                    else:
                        newly=new_regression_rows(census,old_preds,fresh,hard)
                # The loss landscape can permit a small safe edit where the
                # full optimizer step breaks dozens of unrelated memories.
                if best_state is None and args.repair_scales:
                    selected_scale,probes=probe_scales(model,original,trained,keys,
                                              census,old_preds,defects,correct,
                                              args)
                    note['scale_checks']=probes
                    if selected_scale is not None:
                        trial,metric,alpha=selected_scale
                        norm=math.sqrt(sum(float((trial[k]-original[k]).square().sum()) for k in keys))
                        best_metric=(-metric['new_repairs'],auto.delta_counts(original,trial,keys),norm,step)
                        best_state=trial;best_scale=alpha
                # A failed quick control is itself an exact memory regression.
                newly=merge(newly,[r for r in quick if pair_key(r) in correctly_remembered and
                                   bykey[pair_key(r)]!=r['target'] and pair_key(r) not in hard])
            elif not controls_ok:
                newly=[r for r in quick if pair_key(r) in correctly_remembered and
                       bykey[pair_key(r)]!=r['target'] and pair_key(r) not in hard]
            if bank is not None and newly:
                bank.regressions(newly,radius=max(max(abs(r['a']),abs(r['b'])) for r in census),
                          delta=args.max_delta,wave=f'repair:{seed}:{wave}:{step}')
            for r in newly:
                if len(hard)>=args.replay_hard_limit:break
                if pair_key(r) in correctly_remembered:hard.setdefault(pair_key(r),r)
            note['newly_mined']=len(newly)
            note['replay_hard_count_after']=len(hard)
            note['qualified']=best_state is not None
            checks.append(note)
            print('EMP_FRONTIER_REPAIR_CHECK='+json.dumps({k:v for k,v in note.items()
                if k in ('step','fixed','controls_ok','qualified','new_repairs','new_regressions',
                         'groups','newly_mined','replay_hard_count_after','scale_checks')},
                sort_keys=True),flush=True)
            if best_state is not None and args.stop_on_first_safe:
                break
            if len(hard) and newly:
                rehearsal=weighted_rehearsal(correct,list(hard.values()),args.replay_multiplier)
                teacher=capture_baseline_teacher(model,original,rehearsal,args)
                print('EMP_FRONTIER_REPLAY='+json.dumps({'groups':selected,
                       'hard_memories':len(hard),'weighted_training_cards':len(rehearsal)},
                       sort_keys=True),flush=True)
        model.requires_grad_(False)
        all_attempts.append({'groups':selected,'checks':checks,'qualified':best_state is not None})
        if best_state is not None:
            model.load_state_dict(best_state,strict=True)
            return best_state,{'status':'qualified','selected_groups':selected,
                               'updated_elements':auto.delta_counts(original,best_state,keys),
                               'observed_repairs':-best_metric[0],
                               'selected_scale':best_scale,'hard_memories_mined':len(hard),
                               'attempts':all_attempts}
    model.load_state_dict(original,strict=True)
    return None,{'status':'no_safe_repair','ranked_groups':ranked,
                 'hard_memories_mined':len(hard),'attempts':all_attempts}

def state_copy(model):
    return {k:v.detach().cpu().clone() for k,v in model.state_dict().items()}


def source_state(args):
    root=Path(args.run).resolve()
    snap=root/f'stage_{args.stage:03d}'/'mastered_snapshot.pt'
    cfg=json.loads((root/'config.json').read_text('utf-8'))
    base=cross.snapshot(snap,cfg,args.stage)
    digest=cross.sha256(snap)
    if args.start_frontier_checkpoint:
        if args.start_repair:
            raise ValueError('FRONTIER_START_CHECKPOINTS_MUTUALLY_EXCLUSIVE')
        previous=Path(args.start_frontier_checkpoint).resolve()
        obj=torch.load(previous,map_location='cpu',weights_only=True)
        cfg_old=obj.get('config',{})
        if (obj.get('schema')!=SCHEMA+'.checkpoint' or
            cfg_old.get('source_sha256')!=digest or set(obj.get('state_dict',{}))!=set(base)):
            raise RuntimeError('FRONTIER_PRIOR_CHECKPOINT_PROVENANCE_MISMATCH')
        previous_radius=int(obj.get('certified_radius',0))
        if previous_radius>args.start_radius:
            raise ValueError('FRONTIER_PRIOR_RADIUS_EXCEEDS_START')
        old_core=int(obj.get('protected_exact_radius',
            previous_radius if obj.get('history',[]) and
            obj['history'][-1].get('certification')=='exhaustive' else 0))
        return obj['state_dict'],digest,{'source_snapshot':str(snap),
            'start_frontier_checkpoint':str(previous),'start_frontier_sha256':cross.sha256(previous),
            'inherited_certified_radius':previous_radius,'inherited_exact_core':old_core,
            'inherited_sampled_memories':obj.get('sampled_memories',[])}
    if not args.start_repair:
        return base,digest,{'source_snapshot':str(snap),'start_repair':None}
    path=Path(args.start_repair).resolve()
    obj=torch.load(path,map_location='cpu',weights_only=True)
    if not isinstance(obj.get('state_dict'),dict) or obj.get('checkpoint_sha256')!=digest:
        raise RuntimeError('FRONTIER_START_REPAIR_SOURCE_MISMATCH')
    if set(obj['state_dict'])!=set(base):
        raise RuntimeError('FRONTIER_START_REPAIR_ARCHITECTURE_MISMATCH')
    return obj['state_dict'],digest,{'source_snapshot':str(snap),
                                      'start_repair':str(path),'start_repair_sha256':cross.sha256(path)}


def validate_args(args):
    if not (0 < args.start_radius <= args.max_radius <= MAX_ABS and
            args.radius_step>0 and args.max_delta>=0 and args.census_cap>0 and
            args.audit_size>0 and args.preserve_cards>0 and args.discovery_controls>0 and
            args.eval_batch>0 and args.search_steps>0 and args.check_every>0 and
            args.repair_cycles>0 and args.fault_batch>0 and args.max_candidate_elements>0 and
            args.lr>0 and args.preserve_batch>0 and args.max_groups>0 and
            args.max_delta_norm>=0 and args.recheck_waves>0 and args.max_memory_cards>0 and
            args.replay_hard_limit>0 and args.replay_multiplier>0 and
            args.hard_audit_cards>=0 and args.shell_audit_cards>=0 and
            args.hard_neighbor_seeds>=0 and args.protected_core_cap>0 and
            all(0 < x < 1 for x in args.repair_scales)):
        raise ValueError('FRONTIER_INVALID_ARGUMENTS')
    if args.require_exhaustive and domain_bound(args.max_radius,args.max_delta)>args.census_cap:
        raise ValueError('FRONTIER_EXHAUSTIVE_LIMIT:increase --census-cap or disable --require-exhaustive')


def run(args):
    validate_args(args)
    root=Path(args.run).resolve();campaign=Path(args.campaign).resolve();out=Path(args.output).resolve()
    if out in (root,campaign) or root in out.parents or campaign in out.parents:
        raise ValueError('FRONTIER_OUTPUT_MUST_BE_SEPARATE')
    src,digest,provenance=source_state(args)
    model,_,_,_,_=lasso.initialize(campaign,args.ladder_output,
                                  campaign/args.hard_state_dir/'latest.pt',args.device)
    if set(model.state_dict())!=set(src):
        raise RuntimeError('FRONTIER_MODEL_ARCHITECTURE_DRIFT')
    model.load_state_dict(src,strict=True);model.eval().requires_grad_(False)
    config={'schema':SCHEMA,'source_sha256':digest,'provenance':provenance,
            'args':{k:v for k,v in vars(args).items() if k not in ('output','run','campaign','ladder_output') and not k.startswith('_')}}
    out.mkdir(parents=True,exist_ok=True)
    config_path=out/'config.json'
    if config_path.exists() and json.loads(config_path.read_text('utf-8'))!=config:
        raise RuntimeError('FRONTIER_OUTPUT_CONFIG_DRIFT')
    atomic_json(config_path,config)
    latest=out/'latest.pt';pending=out/'pending.pt'
    history=[];certified_radius=0;memory=list(provenance.get('inherited_sampled_memories',[]))
    protected_core_radius=int(provenance.get('inherited_exact_core',0))
    pending_radius=None
    bank=HardCaseBank(args.hard_bank_path or out/'hard_cases.sqlite3',
                      source_sha256=digest,max_delta=args.max_delta)
    args._hard_bank=bank
    if latest.exists():
        old=torch.load(latest,map_location='cpu',weights_only=True)
        if old.get('schema')!=SCHEMA+'.checkpoint' or old.get('config')!=config:
            raise RuntimeError('FRONTIER_RESUME_DRIFT')
        model.load_state_dict(old['state_dict'],strict=True)
        history=old['history'];certified_radius=int(old['certified_radius'])
        memory=old.get('sampled_memories',[])
        protected_core_radius=int(old.get('protected_exact_radius',protected_core_radius))
    if pending.exists():
        working=torch.load(pending,map_location='cpu',weights_only=True)
        if working.get('schema')!=SCHEMA+'.pending' or working.get('config')!=config or \
           working.get('certified_radius')!=certified_radius:
            raise RuntimeError('FRONTIER_PENDING_RESUME_DRIFT')
        pending_radius=int(working['radius'])
        model.load_state_dict(working['state_dict'],strict=True)
    # Every radius is re-evaluated; existing correct answers are literal memories.
    radii=[args.start_radius]+list(range(args.start_radius+args.radius_step,
                                        args.max_radius+1,args.radius_step))
    if radii[-1]!=args.max_radius:radii.append(args.max_radius)
    total_repairs=0
    for radius in radii:
        if radius<=certified_radius:continue
        if pending_radius is not None and radius!=pending_radius:
            raise RuntimeError('FRONTIER_PENDING_RADIUS_DRIFT')
        failure_reason=None;success=False;repairs=[];audits=[];audit_meta=None
        acquired={}
        for iteration in range(args.repair_cycles+1):
            drawn,coverage=scheduled_audit(args,radius,iteration+radius*10000,bank)
            cards=merge(drawn,memory,acquired.values())
            audit_meta={**coverage,'memory_archive_cards':len(memory),'tested_cards':len(cards)}
            predictions=donor.predictions(model,cards,args.device,args.eval_batch)
            faults=sum(p!=r['target'] for r,p in zip(cards,predictions))
            bank_observation=bank.observe(cards,predictions,radius=radius,delta=args.max_delta,
                                 wave=f'census:{radius}:{iteration}')
            bank.neighbors(bank_observation['new_or_repeated_failures'][:args.hard_neighbor_seeds],
                           radius=radius,delta=args.max_delta,wave=f'neighbors:{radius}:{iteration}')
            audits.append({'cycle':iteration,'errors':faults,'correct':len(cards)-faults,
                           'cards':len(cards),'coverage':coverage['coverage']})
            print('EMP_FRONTIER_AUDIT='+json.dumps({'radius':radius,'cycle':iteration,
                'errors':faults,'cards':len(cards),'coverage':coverage['coverage'],
                'certified_radius':certified_radius},sort_keys=True),flush=True)
            if faults==0:
                # Repeated fresh MC draws are *additional generated tests*,
                # not withheld sets. Exhaustive coverage needs no resampling.
                verified=True
                for extra in range(1,args.recheck_waves if coverage['coverage']=='monte_carlo' else 1):
                    other,_=audit_cards(radius,args.max_delta,args.census_cap,
                           args.audit_size,args.seed,10000+iteration*args.recheck_waves+extra)
                    extra_pred=donor.predictions(model,other,args.device,args.eval_batch)
                    for r in other:acquired[pair_key(r)]=r
                    if any(p!=r['target'] for r,p in zip(other,extra_pred)):
                        verified=False;failure_reason='new_monte_carlo_failures';break
                if verified:
                    success=True;break
            if iteration==args.repair_cycles:
                failure_reason='repair_cycle_budget_exhausted';break
            defects=choose_cluster(cards,predictions,args.fault_batch,args.seed+radius*100+iteration)
            if not defects:
                failure_reason='no_discovered_defects';break
            before=state_copy(model)
            candidate,diag=try_repair(model,before,cards,predictions,defects,args,
                                args.seed+radius,iteration)
            if candidate is None:
                failure_reason=diag['status'];repairs.append(diag);break
            # After selection, reevaluate the *entire* current radius; when
            # sampled, also test rotating PRNG draws and rollback on regressions.
            verified=True;extra_stats=[]
            for extra in range(1,args.recheck_waves if coverage['coverage']=='monte_carlo' else 1):
                other,_=audit_cards(radius,args.max_delta,args.census_cap,
                         args.audit_size,args.seed,10000+iteration*args.recheck_waves+extra)
                model.load_state_dict(before,strict=True)
                oldp=donor.predictions(model,other,args.device,args.eval_batch)
                model.load_state_dict(candidate,strict=True)
                newp=donor.predictions(model,other,args.device,args.eval_batch)
                score=compare_predictions(other,oldp,newp)
                extra_stats.append(score)
                for r in other:acquired[pair_key(r)]=r
                if score['new_regressions']:
                    verified=False;break
            if verified and protected_core_radius and coverage['coverage']=='monte_carlo':
                core_ok,core_score=verify_protected_core(model,before,candidate,
                                                         protected_core_radius,args)
                extra_stats.append({'protected_exact_core':protected_core_radius,**core_score})
                if not core_ok:verified=False
            if not verified:
                model.load_state_dict(before,strict=True)
                failure_reason='rotating_or_protected_core_regression'
                repairs.append({'status':'rejected_rotating_audit','details':diag,'rotating':extra_stats})
                break
            model.load_state_dict(candidate,strict=True)
            repairs.append({'status':'accepted','details':diag,'rotating':extra_stats})
            total_repairs+=1
            # Recoverable partial progress: this checkpoint is safe on the
            # entire observed memory census, but its radius is NOT certified.
            atomic_checkpoint(pending,{'schema':SCHEMA+'.pending','config':config,
                  'certified_radius':certified_radius,'radius':radius,
                  'state_dict':state_copy(model)})
            print('EMP_FRONTIER_REPAIR_ACCEPT='+json.dumps({'radius':radius,
                     'cycle':iteration,'groups':diag['selected_groups'],
                     'updated_elements':diag['updated_elements'],
                     'observed_repairs':diag.get('observed_repairs')},sort_keys=True),flush=True)
        report={'radius':radius,'accepted':success,'coverage':audit_meta,
                'audits':audits,'first_errors':audits[0]['errors'],
                'last_errors':audits[-1]['errors'],
                'repairs':repairs,'failure_reason':failure_reason,
                'certification':'exhaustive' if success and audit_meta['coverage']=='exhaustive' else
                                'sampled_only' if success else 'not_certified'}
        history.append(report)
        if success:
            # The concrete arithmetic examples remain acquired memories, not
            # discarded validation folds. Exhaustive domains can be regenerated
            # exactly; sampled domains must archive every examined correct card.
            if audit_meta['coverage']=='monte_carlo':
                memory=merge(memory,cards,acquired.values())
                if len(memory)>args.max_memory_cards:
                    raise RuntimeError('FRONTIER_MEMORY_ARCHIVE_LIMIT:increase --max-memory-cards')
            certified_radius=radius
            if audit_meta['coverage']=='exhaustive':
                protected_core_radius=radius
            pending.unlink(missing_ok=True)
            pending_radius=None
            atomic_checkpoint(latest,{'schema':SCHEMA+'.checkpoint','config':config,
                          'state_dict':state_copy(model),'certified_radius':radius,
                          'sampled_memories':memory,'protected_exact_radius':protected_core_radius,
                          'history':history})
            print('EMP_FRONTIER_ADVANCE='+json.dumps({'radius':radius,
                 'certification':report['certification'],
                 'repair_cycles':len([r for r in repairs if r['status']=='accepted'])},sort_keys=True),flush=True)
        else:
            # Persist diagnostics, not an uncertified checkpoint.
            break
        atomic_json(out/'history.json',history)
    atomic_json(out/'history.json',history)
    status='complete' if len(history)>0 and history[-1]['accepted'] and certified_radius==args.max_radius else 'stalled'
    result={'schema':SCHEMA,'status':status,'certified_radius':certified_radius,
            'certification':history[-1]['certification'] if history else None,
            'total_repairs':total_repairs,'history':str(out/'history.json'),
            'checkpoint':str(latest) if latest.exists() else None,
        'partial_checkpoint':str(pending) if pending.exists() else None,
            'last':history[-1] if history else None,
            'limitations':['This experiment covers opposite-sign/zero cancellation up to the configured residual; not arbitrary addition.',
                           'Sampled-only radii are not exhaustive mathematical mastery.',
                           'No DEV/TEST holdout: all evaluated arithmetic is eligible for acquisition and repair.',
                           'The group search is heuristic and edits are parameter-local training, not zero-training inference.',
                           'Hard-case priority and seeded Monte Carlo sampling cannot prove zero error outside enumerated regions.'],
            'hard_bank':str(bank.path),'hard_case_summary':bank.summary(),
            'protected_exact_radius':protected_core_radius}
    result['hard_case_exports']=bank.export(out)
    atomic_json(out/'result.json',result)
    print('EMP_FRONTIER_HARD_BANK='+json.dumps({'path':str(bank.path),
          'protected_exact_radius':protected_core_radius,**bank.summary()},sort_keys=True),flush=True)
    print('EMP_FRONTIER_RESULT='+json.dumps({k:result[k] for k in
            ('status','certified_radius','certification','total_repairs','checkpoint','partial_checkpoint')},sort_keys=True),flush=True)
    bank.close()
    del args._hard_bank
    return result


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',required=True)
    p.add_argument('--campaign',required=True)
    p.add_argument('--ladder-output',required=True)
    p.add_argument('--hard-state-dir',default='hard_emp_frontier_v2')
    p.add_argument('--start-repair',default=None,help='Certified donor-free repair checkpoint built from the chosen stage')
    p.add_argument('--start-frontier-checkpoint',default=None,help='Carry forward a prior certified frontier latest.pt')
    p.add_argument('--hard-bank-path',default=None,help='SQLite hard-pair ledger; defaults to OUTPUT/hard_cases.sqlite3')
    p.add_argument('--hard-audit-cards',type=int,default=1024)
    p.add_argument('--shell-audit-cards',type=int,default=2048)
    p.add_argument('--hard-neighbor-seeds',type=int,default=128)
    p.add_argument('--protected-core-cap',type=int,default=250000)
    p.add_argument('--output',required=True)
    p.add_argument('--stage',type=int,default=27)
    p.add_argument('--device',default='cuda' if torch.cuda.is_available() else 'cpu')
    p.add_argument('--start-radius',type=int,default=464)
    p.add_argument('--radius-step',type=int,default=16)
    p.add_argument('--max-radius',type=int,default=640)
    p.add_argument('--max-delta',type=int,default=32)
    p.add_argument('--census-cap',type=int,default=250000)
    p.add_argument('--audit-size',type=int,default=8192)
    p.add_argument('--max-memory-cards',type=int,default=250000)
    p.add_argument('--require-exhaustive',action=argparse.BooleanOptionalAction,default=True)
    p.add_argument('--recheck-waves',type=int,default=3)
    p.add_argument('--repair-cycles',type=int,default=6)
    p.add_argument('--fault-batch',type=int,default=4)
    p.add_argument('--preserve-cards',type=int,default=256)
    p.add_argument('--discovery-controls',type=int,default=64)
    p.add_argument('--expand',default='1,2,3,5')
    p.add_argument('--max-groups',type=int,default=5)
    p.add_argument('--max-candidate-elements',type=int,default=100000)
    p.add_argument('--search-steps',type=int,default=300)
    p.add_argument('--stop-on-first-safe',action=argparse.BooleanOptionalAction,default=True)
    p.add_argument('--check-every',type=int,default=50)
    p.add_argument('--preserve-batch',type=int,default=64)
    p.add_argument('--eval-batch',type=int,default=256)
    p.add_argument('--lr',type=float,default=.001)
    p.add_argument('--defect-weight',type=float,default=8.)
    p.add_argument('--preserve-ce',type=float,default=.35)
    p.add_argument('--distill-weight',type=float,default=1.5)
    p.add_argument('--l2',type=float,default=.01)
    p.add_argument('--max-delta-norm',type=float,default=1.)
    p.add_argument('--clip',type=float,default=1.)
    p.add_argument('--replay-hard-limit',type=int,default=512,
                   help='Maximum exact memory regressions used as counterexample rehearsal per repair attempt')
    p.add_argument('--replay-multiplier',type=int,default=8,
                   help='Repeat each mined correct card this many times in the preservation stream')
    p.add_argument('--repair-scales',type=float,nargs='*',default=[.75,.5,.25,.125,.0625],
                   help='Backtracking factors tested against the full census; empty disables')
    p.add_argument('--seed',type=int,default=20261027)
    return p


if __name__=='__main__':
    run(parser().parse_args())
