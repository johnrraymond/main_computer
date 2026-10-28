#!/usr/bin/env python3
"""Donor-free, parameter-group discovery and local repair of NanoJev arithmetic defects.

No post-defect snapshot/donor, repaired checkpoint, oracle mask or predetermined
editable tensor names is read. Discovery scores *every trainable named parameter*
from defect gradients and gradients of correct controls at the defective model.
Cumulative ranked group budgets are independently optimized from that checkpoint.
Only TRAIN/DEV influence search, ablations, and selection; TEST is opened once.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
from pathlib import Path

import torch
from torch import nn
from torch.nn import functional as F

from tools import nanojev_emp_donor_free_repair as prior
from tools import nanojev_emp_defect_cross_transfer as cross
from tools import nanojev_emp_hard_lasso as lasso
from tools import nanojev_emp_dual_bank as dual
from tools.nanojev_zero_line_zero_only import input_tensor
from tools.nanojev_zero_line_ladder import atomic_json, atomic_checkpoint

SCHEMA = 'nanojev.emp-autodiscovered-donor-free-repair.v1'


def group_name(name: str) -> str:
    """Architectural prefix grouping, deliberately independent of repair location.

    Each embedding/normalization module is its own group; a transformer layer
    is a group, and heads retain their bias and weight as a single component.
    """
    p = name.split('.')
    if len(p) >= 3 and p[0] == 'backbone' and p[1] == 'layers':
        return '.'.join(p[:3])
    if len(p) >= 3 and p[0] == 'input':
        return '.'.join(p[:2])
    return p[0]


def parameter_groups(model):
    groups = {}
    for name, param in model.named_parameters():
        if param.is_floating_point():
            groups.setdefault(group_name(name), []).append(name)
    if not groups:
        raise RuntimeError('AUTO_REPAIR_NO_TRAINABLE_PARAMETERS')
    return groups


def score_from_grads(model, fault_grad, safe_grad, groups):
    """Rank gradient repair opportunity, penalizing opposed control gradients.

    High defect gradient per sqrt(parameter count) is useful, but also retain
    a raw-norm ranking to avoid systematically hiding wide transformer layers.
    This is a *priority heuristic*, not evidence of a unique causal circuit.
    """
    names = dict(model.named_parameters())
    rows = []
    for group, keys in groups.items():
        defect2 = control2 = inner = 0.
        count = 0
        for k in keys:
            p = names[k]
            count += p.numel()
            a = fault_grad.get(k)
            b = safe_grad.get(k)
            if a is None:
                continue
            defect2 += float(a.double().square().sum())
            if b is not None:
                control2 += float(b.double().square().sum())
                inner += float((a.double()*b.double()).sum())
        gd = math.sqrt(defect2); gs = math.sqrt(control2)
        cosine = inner/max(gd*gs, 1e-20) if gd and gs else 0.
        # -gradient(update) increases safe loss if gradients oppose.
        penalty = 1 + max(0., -cosine)
        efficiency = gd/max(math.sqrt(count),1.)/penalty
        rows.append(dict(group=group,tensor_count=len(keys),elements=count,
                         defect_grad_l2=gd,control_grad_l2=gs,
                         gradient_cosine=cosine,score=efficiency,
                         raw_score=gd/penalty))
    return rows


def interleave_priorities(rows):
    """Combine density- and total-gradient ranking; all groups remain visible."""
    dense = sorted(rows, key=lambda r:(-r['score'],-r['raw_score'],r['group']))
    raw = sorted(rows, key=lambda r:(-r['raw_score'],-r['score'],r['group']))
    output = []
    for i in range(len(rows)):
        for ranking in (dense,raw):
            group = ranking[i]['group']
            if group not in output and ranking[i]['defect_grad_l2'] > 0:
                output.append(group)
    return output


def gradient_discovery(model, defects, safe_rows, device, batch):
    """One gradient pass over defects and correct controls; no checkpoint donors."""
    groups = parameter_groups(model)
    params = dict(model.named_parameters())
    keys = list(params)
    active = [params[k] for k in keys]
    model.eval()
    model.requires_grad_(True)
    try:
        x = input_tensor([(r['a'],r['b'],r['target']) for r in defects],device)
        y = torch.tensor([r['target'] for r in defects],device=device,dtype=torch.long)
        fault = dual.loss_per_card(model(x),y).mean()
        fg = torch.autograd.grad(fault,active,allow_unused=True)
        fault_grad = {k: g.detach().cpu() for k,g in zip(keys,fg) if g is not None}
        control_grad = {}
        for start in range(0,len(safe_rows),batch):
            rows=safe_rows[start:start+batch]
            x=input_tensor([(r['a'],r['b'],r['target']) for r in rows],device)
            y=torch.tensor([r['target'] for r in rows],device=device,dtype=torch.long)
            control=dual.loss_per_card(model(x),y).mean() * (len(rows)/len(safe_rows))
            cg=torch.autograd.grad(control,active,allow_unused=True)
            for k,g in zip(keys,cg):
                if g is not None:
                    control_grad[k]=control_grad.get(k,torch.zeros_like(g.detach().cpu()))+g.detach().cpu()
    finally:
        model.requires_grad_(False)
    scores=score_from_grads(model,fault_grad,control_grad,groups)
    return groups,scores,interleave_priorities(scores)


def expansion_schedule(text, total, max_groups):
    if total<=0 or max_groups<1:
        raise ValueError('AUTO_REPAIR_INVALID_DISCOVERY_BUDGET')
    boundary=min(total,max_groups)
    numbers=prior.parse_ints(text)
    if any(x<1 for x in numbers):
        raise ValueError('AUTO_REPAIR_INVALID_EXPANSIONS')
    return sorted(set(min(boundary,x) for x in numbers) | {boundary})


def select_editable(model, baseline, groups, selected):
    """Exclude every unselected parameter from optimizer, verify exact freeze."""
    model.load_state_dict(baseline,strict=True)
    model.eval().requires_grad_(False)
    keys={key for g in selected for key in groups[g]}
    params={}
    for name,p in model.named_parameters():
        if name in keys:
            if not p.is_floating_point():
                raise RuntimeError('AUTO_REPAIR_NONFLOAT_PARAMETER')
            p.requires_grad_(True)
            params[name]=p
    if set(params)!=keys:
        raise RuntimeError('AUTO_REPAIR_UNAVAILABLE_GROUP_PARAMETER')
    return params


def frozen_match(before, after, allowed):
    if set(before)!=set(after):
        raise RuntimeError('AUTO_REPAIR_STATE_KEYS_CHANGED')
    for k in before:
        if k not in allowed and not torch.equal(before[k].cpu(),after[k].detach().cpu()):
            raise RuntimeError('AUTO_REPAIR_FROZEN_CHANGED:'+k)


def delta_counts(before, state, allowed, threshold=1e-8):
    return sum(int(((state[k].detach().cpu()-before[k].cpu()).abs()>threshold).sum())
               for k in allowed)


def step(model, optimizer, params, original, defects, preservation, teacher,
         rng, args, device):
    inds=[rng.randrange(len(preservation)) for _ in range(args.preserve_batch)]
    controls=[preservation[i] for i in inds]
    xd=input_tensor([(r['a'],r['b'],r['target']) for r in defects],device)
    yd=torch.tensor([r['target'] for r in defects],device=device,dtype=torch.long)
    xp=input_tensor([(r['a'],r['b'],r['target']) for r in controls],device)
    yp=torch.tensor([r['target'] for r in controls],device=device,dtype=torch.long)
    target={k:v[inds].to(device) for k,v in teacher.items()}
    optimizer.zero_grad(set_to_none=True)
    defect_ce=dual.loss_per_card(model(xd),yd).mean()
    out=model(xp)
    ce=dual.loss_per_card(out,yp).mean()
    kl=prior.preservation_kl(out,target)
    norm=sum((p-original[k].to(p.device)).square().sum() for k,p in params.items())
    loss=args.defect_weight*defect_ce+args.preserve_ce*ce+args.distill_weight*kl+args.l2*norm
    if not bool(torch.isfinite(loss)):
        raise RuntimeError('AUTO_REPAIR_NONFINITE_LOSS')
    loss.backward()
    nn.utils.clip_grad_norm_(list(params.values()),args.clip,error_if_nonfinite=True)
    optimizer.step()
    with torch.no_grad():
        # L2 trust region applies to the *whole* selected edit, not each tensor.
        if args.max_delta_norm > 0:
            size=torch.sqrt(sum((p-original[k].to(p.device)).square().sum()
                                for k,p in params.items()))
            if size > args.max_delta_norm:
                scale=args.max_delta_norm/size
                for k,p in params.items():
                    base=original[k].to(p.device)
                    p.copy_(base+(p-base)*scale)
    return dict(loss=float(loss.detach()),defect_ce=float(defect_ce.detach()),
                preservation_ce=float(ce.detach()),kl=float(kl.detach()))


def qualify(model, defects, local, train_guard, dev_guard, reference, args):
    sets={'defects':defects,'local':local,'train':train_guard,'dev':dev_guard}
    results={k:prior.compare(reference[k],prior.predictions(model,rs,args.device,args.eval_batch),rs)
             for k,rs in sets.items()}
    good=(results['defects']['new_correct']==len(defects) and
          results['local']['new_regressions']==0 and
          results['train']['new_regressions']<=args.max_train_regressions and
          results['dev']['new_regressions']<=args.max_dev_regressions)
    return dict(qualified=bool(good),defects_fixed=results['defects']['new_correct'],
                local_damage=results['local']['new_regressions'],
                train_regressions=results['train']['new_regressions'],
                dev_regressions=results['dev']['new_regressions'],
                dev_repairs=results['dev']['new_repairs'])


def improve(left,right):
    """DEV-preserving ranking; TEST is excluded even after it is measured."""
    if right is None:return True
    return (left['dev_regressions'],left['train_regressions'],
            left['edit_elements'],left['delta_norm'],left['step']) < (
            right['dev_regressions'],right['train_regressions'],
            right['edit_elements'],right['delta_norm'],right['step'])


def optimize_budget(model, original, groups, chosen, defects, local, train_guard,
                    dev_guard, reference, preservation, teacher, args, output, index):
    params=select_editable(model,original,groups,chosen)
    count=sum(p.numel() for p in params.values())
    if count>args.max_candidate_elements:
        return dict(groups=list(chosen),status='skipped_element_budget',editable_elements=count),None
    opt=torch.optim.Adam(list(params.values()),lr=args.lr)
    rng=random.Random(f'auto-repair:{args.seed}:{index}')
    best=None; best_state=None;checks=[]
    keys=set(params)
    for iteration in range(1,args.search_steps+1):
        metrics=step(model,opt,params,original,defects,preservation,teacher,rng,args,args.device)
        if iteration%args.check_every and iteration!=args.search_steps:
            continue
        score=qualify(model,defects,local,train_guard,dev_guard,reference,args)
        state={k:v.detach().cpu().clone() for k,v in model.state_dict().items()}
        frozen_match(original,state,keys)
        changes=delta_counts(original,state,keys)
        magnitude=math.sqrt(sum(float((state[k]-original[k]).square().sum()) for k in keys))
        record={**score,**metrics,'step':iteration,'edit_elements':changes,
                'delta_norm':magnitude}
        checks.append(record)
        print('EMP_AUTO_REPAIR_CHECK='+json.dumps({
              'budget_groups':len(chosen),'step':iteration,'fixed':score['defects_fixed'],
              'local_damage':score['local_damage'],'dev_regressions':score['dev_regressions'],
              'qualified':score['qualified']},sort_keys=True),flush=True)
        if score['qualified'] and improve(record,best):
            best=record;best_state=state
    model.requires_grad_(False)
    atomic_json(output/f'budget_{index:02d}_checks.json',{'groups':list(chosen),
               'editable_elements':count,'checks':checks})
    return {'groups':list(chosen),'status':'qualified' if best else 'not_qualified',
            'editable_elements':count,'selected':best},best_state


def ablate_groups(model, original, state, groups, chosen, defects, local,
                  train_guard, dev_guard, reference, args):
    """Delete discovered edits group-by-group; never refit or consult TEST."""
    active=list(chosen)
    current={k:v.clone() for k,v in state.items()}
    audit=[]
    for group in list(chosen):
        trial={k:v.clone() for k,v in current.items()}
        for k in groups[group]:trial[k]=original[k].clone()
        model.load_state_dict(trial,strict=True)
        q=qualify(model,defects,local,train_guard,dev_guard,reference,args)
        audit.append(dict(group=group,accepted=q['qualified'],**q))
        if q['qualified']:
            current=trial;active.remove(group)
    model.load_state_dict(current,strict=True)
    allowed={k for g in active for k in groups[g]}
    frozen_match(original,current,allowed)
    return active,current,audit


def run(args):
    if (args.search_steps<1 or args.check_every<1 or args.preserve_batch<1 or
        args.eval_batch<1 or args.max_candidate_elements<1 or args.lr<=0 or
        args.max_delta_norm<0 or args.discovery_controls<1):
        raise ValueError('AUTO_REPAIR_INVALID_ARGUMENTS')
    root=Path(args.run).resolve();campaign=Path(args.campaign).resolve();output=Path(args.output).resolve()
    if output in (root,campaign,root.parent) or root in output.parents or campaign in output.parents:
        raise ValueError('AUTO_REPAIR_OUTPUT_MUST_BE_SEPARATE')
    snap=root/f'stage_{args.stage:03d}'/'mastered_snapshot.pt'
    config=json.loads((root/'config.json').read_text('utf-8'))
    original=cross.snapshot(snap,config,args.stage)
    defects=prior.defects_from_csv(args.defects)
    local=prior.neighborhood(defects,
            prior.DEFAULT_ANCHORS if not args.defects else [(r['a'],r['b']) for r in defects],
            args.window,args.radius,args.max_delta)
    guards=prior.build_sets(config,defects,local,prior.parse_ints(args.guard_radii),
                           args.guard_per_radius,args.stage)
    model,_,_,_,_=lasso.initialize(campaign,args.ladder_output,
                                  campaign/args.hard_state_dir/'latest.pt',args.device)
    if set(model.state_dict())!=set(original):
        raise RuntimeError('AUTO_REPAIR_MODEL_ARCHITECTURE_DRIFT')
    model.load_state_dict(original,strict=True);model.eval().requires_grad_(False)
    sets={'defects':defects,'local':local,'train':guards['train'],'dev':guards['dev']}
    baseline={name:prior.predictions(model,rows,args.device,args.eval_batch)
              for name,rows in sets.items()}
    if any(p==r['target'] for r,p in zip(defects,baseline['defects'])):
        raise RuntimeError('AUTO_REPAIR_EXPECTED_DEFECT_NOT_REPRODUCED')
    defect_pairs={(r['a'],r['b']) for r in defects}
    if any(p!=r['target'] for r,p in zip(local,baseline['local'])
           if (r['a'],r['b']) not in defect_pairs):
        raise RuntimeError('AUTO_REPAIR_UNDECLARED_LOCAL_FAILURES')
    train_old_predictions=baseline['train']
    train_good=[r for r,p in zip(guards['train'],train_old_predictions) if p==r['target']]
    baseline['train']=[p for r,p in zip(guards['train'],train_old_predictions) if p==r['target']]
    local_good=[r for r,p in zip(local,baseline['local']) if p==r['target']]
    if len(train_good)<32 or not local_good:
        raise RuntimeError('AUTO_REPAIR_INSUFFICIENT_CORRECT_CONTROLS')
    preservation=train_good+local_good
    teacher=prior.capture_teacher(model,preservation,args.device,args.eval_batch)
    # Diagnosis uses only correctly predicted examples from TRAIN and known local controls.
    discovery_controls=local_good+train_good[:args.discovery_controls]
    groups,scores,ranked=gradient_discovery(model,defects,discovery_controls,
                                          args.device,args.eval_batch)
    if not ranked:
        raise RuntimeError('AUTO_REPAIR_NO_NONZERO_GRADIENT_GROUPS')
    schedule=expansion_schedule(args.expand,len(ranked),args.max_groups)
    source_hash=cross.sha256(snap)
    setup={'schema':SCHEMA,'checkpoint':str(snap),'checkpoint_sha256':source_hash,
           'campaign':str(campaign),'group_ranking':ranked,'group_schedule':schedule,
           'defects':defects,'local_cases':len(local),
           'guard_counts':{k:len(v) for k,v in guards.items()},
           'args':{k:v for k,v in vars(args).items() if k not in ('output','run','campaign','ladder_output')}}
    output.mkdir(parents=True,exist_ok=True)
    config_path=output/'config.json'
    if config_path.exists() and json.loads(config_path.read_text('utf-8'))!=setup:
        raise RuntimeError('AUTO_REPAIR_OUTPUT_CONFIG_DRIFT')
    atomic_json(config_path,setup)
    atomic_json(output/'discovery.json',{'schema':SCHEMA,'groups':groups,
                                       'ranking':ranked,'scores':scores})
    print('EMP_AUTO_REPAIR_DISCOVERY='+json.dumps({'ranked_groups':ranked,
        'budgets':schedule,'total_groups':len(groups)},sort_keys=True),flush=True)
    attempts=[];chosen=None;candidate=None
    for i,n in enumerate(schedule):
        selected=ranked[:n]
        summary,state=optimize_budget(model,original,groups,selected,defects,local,
                    train_good,guards['dev'],baseline,preservation,teacher,args,output,i)
        attempts.append(summary)
        if state is not None:
            chosen=selected;candidate=state
            break
    report={'schema':SCHEMA,'status':'no_qualified_candidate','repair_exported':False,
            'source_sha256':source_hash,'discovered_groups':ranked,'attempts':attempts,
            'test_cards':len(guards['test']),
            'limitations':['The experiment is donor-free but supervised by known arithmetic errors.',
                           'Gradient-ranking identifies candidate editable groups, not unique causal components.',
                           'Only the explored ranked group budgets were optimized; no global search guarantee.',
                           'Local/DEV groups influenced selection; TEST is evaluated only after selection.',
                           'Finite test samples do not prove correctness outside the tested domain.']}
    artifact=output/'autodiscovered_repair.pt'
    artifact.unlink(missing_ok=True)
    if candidate is not None:
        active,refined,ablation=ablate_groups(model,original,candidate,groups,chosen,
                                   defects,local,train_good,guards['dev'],baseline,args)
        editable={k for g in active for k in groups[g]}
        frozen_match(original,refined,editable)
        model.load_state_dict(original,strict=True)
        old_test=prior.predictions(model,guards['test'],args.device,args.eval_batch)
        model.load_state_dict(refined,strict=True)
        new_test=prior.predictions(model,guards['test'],args.device,args.eval_batch)
        holdout=prior.compare(old_test,new_test,guards['test'])
        q=qualify(model,defects,local,train_good,guards['dev'],baseline,args)
        acceptable=bool(q['qualified'] and
                        holdout['new_regressions']<=args.max_test_regressions)
        report.update(status='certified' if acceptable else 'test_rejected',
            repair_exported=acceptable,selected_groups=list(chosen),
            retained_groups=active,group_ablations=ablation,
            updated_elements=delta_counts(original,refined,editable),
            final_test=holdout,final_development=q)
        if acceptable:
            atomic_checkpoint(artifact,{'schema':SCHEMA,'state_dict':refined,'checkpoint_sha256':source_hash,
                        'retained_groups':active,'discovery_ranking':ranked})
            saved=torch.load(artifact,map_location='cpu',weights_only=True)
            if saved['schema']!=SCHEMA or set(saved['state_dict'])!=set(original):
                artifact.unlink(missing_ok=True)
                raise RuntimeError('AUTO_REPAIR_ROUNDTRIP_INVALID')
            frozen_match(original,saved['state_dict'],editable)
            if any(not torch.equal(saved['state_dict'][k],refined[k]) for k in refined):
                artifact.unlink(missing_ok=True)
                raise RuntimeError('AUTO_REPAIR_ROUNDTRIP_WEIGHT_DRIFT')
            model.load_state_dict(saved['state_dict'],strict=True)
            if not qualify(model,defects,local,train_good,guards['dev'],baseline,args)['qualified']:
                artifact.unlink(missing_ok=True)
                raise RuntimeError('AUTO_REPAIR_ROUNDTRIP_GATE_FAILED')
            report.update(repair_checkpoint=str(artifact),repair_checkpoint_sha256=cross.sha256(artifact))
    atomic_json(output/'result.json',report)
    print('EMP_AUTO_REPAIR_RESULT='+json.dumps({k:report.get(k) for k in (
          'status','repair_exported','selected_groups','retained_groups','updated_elements',
          'test_cards')},sort_keys=True),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',required=True)
    p.add_argument('--campaign',required=True)
    p.add_argument('--ladder-output',required=True)
    p.add_argument('--hard-state-dir',default='hard_emp_frontier_v2')
    p.add_argument('--output',required=True)
    p.add_argument('--stage',type=int,default=27)
    p.add_argument('--device',default='cuda' if torch.cuda.is_available() else 'cpu')
    p.add_argument('--defects',default=None,help='Optional defect CSV: a,b,target')
    p.add_argument('--radius',type=int,default=464)
    p.add_argument('--max-delta',type=int,default=32)
    p.add_argument('--window',type=int,default=2)
    p.add_argument('--guard-radii',default='128,256,384,464')
    p.add_argument('--guard-per-radius',type=int,default=1024)
    p.add_argument('--discovery-controls',type=int,default=64)
    p.add_argument('--max-groups',type=int,default=14)
    p.add_argument('--expand',default='1,2,3,5,8,11,14')
    p.add_argument('--max-candidate-elements',type=int,default=100000)
    p.add_argument('--search-steps',type=int,default=300)
    p.add_argument('--check-every',type=int,default=50)
    p.add_argument('--preserve-batch',type=int,default=64)
    p.add_argument('--eval-batch',type=int,default=128)
    p.add_argument('--lr',type=float,default=.001)
    p.add_argument('--defect-weight',type=float,default=8.)
    p.add_argument('--preserve-ce',type=float,default=.35)
    p.add_argument('--distill-weight',type=float,default=1.5)
    p.add_argument('--l2',type=float,default=.01)
    p.add_argument('--max-delta-norm',type=float,default=1.)
    p.add_argument('--clip',type=float,default=1.)
    p.add_argument('--max-train-regressions',type=int,default=0)
    p.add_argument('--max-dev-regressions',type=int,default=0)
    p.add_argument('--max-test-regressions',type=int,default=0)
    p.add_argument('--seed',type=int,default=20261027)
    return p


if __name__=='__main__':
    run(parser().parse_args())
