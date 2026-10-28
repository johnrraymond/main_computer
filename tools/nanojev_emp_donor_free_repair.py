#!/usr/bin/env python3
"""Donor-free, constrained repair of an already known NanoJev arithmetic defect.

Uses ONLY the pre-repair snapshot and labels for known defects/correct controls.
Never loads a later checkpoint, donor parameter deltas or a trained repair.
Baseline/sparse start from independent zero deltas to the five input tensors.
An explicit optional oracle-coordinate control may read a *mask*, never values.
Discovery/development/test arithmetic guards use disjoint deterministic folds;
TEST is evaluated only once, on the final DEV-selected candidate.
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

from tools import nanojev_emp_isolated_defects as iso
from tools import nanojev_emp_defect_cross_transfer as cross
from tools import nanojev_emp_backbone_readout_crossover as crossover
from tools import nanojev_emp_hard_lasso as lasso
from tools import nanojev_emp_dual_bank as dual
from tools.nanojev_zero_line_zero_only import input_tensor
from tools.nanojev_zero_line_ladder import atomic_json

SCHEMA = 'nanojev.emp-donor-free-targeted-repair.v1'
INPUT_KEYS = ('input.digit.weight', 'input.norm.weight', 'input.place.weight',
              'input.role.weight', 'input.sign.weight')
DEFAULT_DEFECTS = ((-455,442,-13),(-456,443,-13),(-457,444,-13),(-455,444,-11))
DEFAULT_ANCHORS = ((-456,443),(-457,444))


def parse_ints(value):
    result = tuple(int(x.strip()) for x in value.split(',') if x.strip())
    if not result or len(set(result)) != len(result):
        raise ValueError('DONOR_FREE_INVALID_RADII')
    return result


def parse_modes(value):
    modes = tuple(x.strip() for x in value.split(',') if x.strip())
    if not modes or len(modes) != len(set(modes)) or set(modes)-{'baseline','sparse','oracle'}:
        raise ValueError('DONOR_FREE_INVALID_MODES')
    return modes


def defects_from_csv(filename):
    if not filename:
        return [{'a':a,'b':b,'target':t} for a,b,t in DEFAULT_DEFECTS]
    with open(filename,encoding='utf-8-sig',newline='') as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames or not {'a','b','target'}.issubset(reader.fieldnames):
            raise ValueError('DONOR_FREE_DEFECT_COLUMNS')
        entries = [{'a':int(r['a']),'b':int(r['b']),'target':int(r['target'])} for r in reader]
    if not entries or len({(r['a'],r['b']) for r in entries})!=len(entries):
        raise ValueError('DONOR_FREE_BAD_DEFECTS')
    if any(r['a']+r['b']!=r['target'] for r in entries):
        raise ValueError('DONOR_FREE_BAD_DEFECT_LABEL')
    return entries


def neighborhood(defects, anchors, window, radius, max_delta):
    """(m, delta) neighborhoods on each anchor's fixed signed operand branch."""
    if window < 1:
        raise ValueError('DONOR_FREE_BAD_WINDOW')
    result = {(r['a'],r['b']):(r['a'],r['b'],r['target']) for r in defects}
    for a,b in anchors:
        # For current cancellation cluster, a is the negative operand and b positive.
        sign = 1 if a>=0 else -1
        for dm in range(-window,window+1):
            for dd in range(-window,window+1):
                aa=a+sign*dm
                bb=b-sign*dm+dd
                if (abs(aa)>radius or abs(bb)>radius or aa*bb>0 or
                        abs(aa+bb)>max_delta):
                    continue
                result[(aa,bb)] = (aa,bb,aa+bb)
    return [dict(a=a,b=b,target=t) for a,b,t in sorted(result.values())]


def build_sets(run_cfg, defects, local, radii, per_radius, stage):
    """Train/DEV/TEST guards are drawn from disjoint original hash buckets.

    User-supplied known errors and local controls are explicitly exempt from
    the blind-fold guarantee; they are excluded from DEV and TEST by pair.
    """
    p = run_cfg.get('parameters', run_cfg)
    needed=('seed','start_radius','radius_step','initial_delta','delta_step','max_delta')
    if not isinstance(p,dict) or any(k not in p for k in needed):
        raise RuntimeError('DONOR_FREE_MISSING_SAVED_SAMPLER_CONFIG')
    seed=int(p['seed']); start=int(p['start_radius']); step=int(p['radius_step'])
    excluded={(r['a'],r['b']) for r in local}
    seen={'train':set(excluded),'dev':set(excluded),'test':set(excluded)}
    sets={'train':[],'dev':[],'test':[]}
    for radius in radii:
        if radius<start or radius>start+stage*step or (radius-start)%step:
            raise ValueError('DONOR_FREE_RADIUS_NOT_MASTERED')
        md=min(int(p['max_delta']),int(p['initial_delta'])+
               (radius-start)//step*int(p['delta_step']))
        generators={
            'train': iso.training_entries(radius,md,per_radius,seed),
            'dev': iso.probe_entries(radius,md,per_radius,seed,'probe'),
            'test': iso.probe_entries(radius,md,per_radius,seed,'test')}
        for phase,entries in generators.items():
            for e in entries:
                a,b,t=e['card'][:3]
                if (a,b) in seen[phase]:
                    continue
                if phase=='train' and iso.stable_bucket(e['card'],seed) in (0,1):
                    raise RuntimeError('DONOR_FREE_TRAIN_FOLD_LEAK')
                if phase=='dev' and iso.stable_bucket(e['card'],seed)!=0:
                    raise RuntimeError('DONOR_FREE_DEV_FOLD_LEAK')
                if phase=='test' and iso.stable_bucket(e['card'],seed)!=1:
                    raise RuntimeError('DONOR_FREE_TEST_FOLD_LEAK')
                seen[phase].add((a,b))
                sets[phase].append(dict(a=a,b=b,target=t,radius=radius))
    for a in ('train','dev','test'):
        if len(sets[a])<32:
            raise RuntimeError('DONOR_FREE_GUARD_TOO_SMALL:'+a)
    for a,b in (('train','dev'),('train','test'),('dev','test')):
        if set((r['a'],r['b']) for r in sets[a]) & set((r['a'],r['b']) for r in sets[b]):
            raise RuntimeError('DONOR_FREE_FOLD_OVERLAP')
    return sets


def input_masks(source, mode, oracle_mask_file=None):
    masks={k:torch.ones_like(source[k]) for k in INPUT_KEYS}
    if mode!='oracle':
        return masks
    if not oracle_mask_file:
        raise ValueError('DONOR_FREE_ORACLE_NEEDS_EXPLICIT_COORDINATE_FILE')
    manifest=json.loads(Path(oracle_mask_file).read_text('utf-8'))
    result={k:torch.zeros_like(source[k]) for k in INPUT_KEYS}
    if not isinstance(manifest.get('tensors'),list):
        raise RuntimeError('DONOR_FREE_ORACLE_MASK_FORMAT')
    for item in manifest['tensors']:
        k=item['tensor']
        if k not in result or list(source[k].shape)!=item['shape']:
            raise RuntimeError('DONOR_FREE_ORACLE_SHAPE_OR_KEY')
        for row in item.get('active_rows',[]):
            ridx=row['row']
            for col in row['dimensions']:
                if col<0 or col>=source[k].shape[-1]:
                    raise RuntimeError('DONOR_FREE_ORACLE_INDEX')
                if ridx is None:
                    if source[k].ndim!=1: raise RuntimeError('DONOR_FREE_ORACLE_BAD_ROW')
                    result[k][col]=1
                else:
                    if source[k].ndim!=2 or ridx<0 or ridx>=source[k].shape[0]:
                        raise RuntimeError('DONOR_FREE_ORACLE_BAD_ROW')
                    result[k][ridx,col]=1
    if not any(bool(v.any()) for v in result.values()):
        raise RuntimeError('DONOR_FREE_ORACLE_EMPTY')
    return result


def capture_teacher(model, rows, device, batch):
    """Detach old output distributions on allowed TRAIN preservation examples."""
    signs=[]; digits=[]
    model.eval()
    with torch.no_grad():
        for i in range(0,len(rows),batch):
            r=rows[i:i+batch]
            x=input_tensor([(v['a'],v['b'],v['target']) for v in r],device)
            o=model(x)
            signs.append(o['sign'].detach().cpu()); digits.append(o['digits'].detach().cpu())
    return {'sign':torch.cat(signs),'digits':torch.cat(digits)}


def preservation_kl(out, prior):
    s=F.kl_div(F.log_softmax(out['sign'],dim=-1),
               F.softmax(prior['sign'],dim=-1),reduction='batchmean')
    logd=F.log_softmax(out['digits'],dim=-1)
    pd=F.softmax(prior['digits'],dim=-1)
    d=F.kl_div(logd,pd,reduction='none').sum(-1).mean()
    return s+d


def configure_trainable(model, old, masks):
    """No other model parameters may receive gradients or change values."""
    model.requires_grad_(False).eval()
    names=dict(model.named_parameters())
    hooks=[]
    selected={}
    for k in INPUT_KEYS:
        if k not in names or k not in old or names[k].shape != old[k].shape:
            raise RuntimeError('DONOR_FREE_INVALID_INPUT_PARAMETER:'+k)
        p=names[k];p.requires_grad_(True)
        mask=masks[k].to(p.device,dtype=p.dtype)
        hooks.append(p.register_hook(lambda g,m=mask:g*m))
        selected[k]=p
    return selected,hooks


def proximal(model_params, old, masks, lr, l1, group_l1, max_norm):
    """Proximal shrinkage from the ORIGINAL weights, never donor values."""
    with torch.no_grad():
        for k,p in model_params.items():
            base=old[k].to(p.device)
            mask=masks[k].to(p.device,dtype=p.dtype)
            d=(p-base)*mask
            if l1>0:
                d=d.sign()*torch.relu(d.abs()-lr*l1)
            if group_l1>0:
                if d.ndim==2:
                    norms=d.norm(dim=-1,keepdim=True).clamp_min(1e-12)
                    d=d*torch.relu(1-lr*group_l1/norms)
                else:
                    d=d*torch.relu(1-lr*group_l1/d.norm().clamp_min(1e-12))
            p.copy_(base+d)
        if max_norm>0:
            norm=torch.sqrt(sum(((p-old[k].to(p.device))**2).sum()
                                for k,p in model_params.items()))
            if norm.item()>max_norm:
                ratio=max_norm/norm
                for k,p in model_params.items():
                    base=old[k].to(p.device)
                    p.copy_(base+(p-base)*ratio)


def diff_count(old,state,keys=INPUT_KEYS,threshold=1e-8):
    return sum(int(((state[k].detach().cpu()-old[k].cpu()).abs()>threshold).sum()) for k in keys)


def validate_frozen(old,state):
    if set(old)!=set(state):
        raise RuntimeError('DONOR_FREE_STATE_KEYS_CHANGED')
    for k in old:
        if k not in INPUT_KEYS and not torch.equal(old[k].cpu(),state[k].detach().cpu()):
            raise RuntimeError('DONOR_FREE_FROZEN_PARAMETER_CHANGED:'+k)


def predictions(model, rows, device, batch):
    model.eval()
    result=[]
    with torch.no_grad():
        for i in range(0,len(rows),batch):
            part=rows[i:i+batch]
            x=input_tensor([(r['a'],r['b'],r['target']) for r in part],device)
            out=model(x)
            decoded=dual.decoded(out)
            result.extend(int(v) for v in decoded)
    return result


def compare(old_pred,new_pred,rows):
    if len(old_pred)!=len(rows) or len(new_pred)!=len(rows):
        raise ValueError('DONOR_FREE_PREDICTION_COUNT_DRIFT')
    return {'cards':len(rows),
            'previously_correct':sum(o==r['target'] for r,o in zip(rows,old_pred)),
            'previously_wrong':sum(o!=r['target'] for r,o in zip(rows,old_pred)),
            'new_regressions':sum(o==r['target'] and n!=r['target'] for r,o,n in zip(rows,old_pred,new_pred)),
            'new_repairs':sum(o!=r['target'] and n==r['target'] for r,o,n in zip(rows,old_pred,new_pred)),
            'new_correct':sum(n==r['target'] for r,n in zip(rows,new_pred))}


def optimizer_step(model, optim, train_params, old, masks, defects, preservation, teacher,
                   rng, args, mode, device):
    picks=[rng.randrange(len(preservation)) for _ in range(args.preserve_batch)]
    selected=[preservation[i] for i in picks]
    xb=input_tensor([(r['a'],r['b'],r['target']) for r in defects],device)
    yb=torch.tensor([r['target'] for r in defects],dtype=torch.long,device=device)
    xp=input_tensor([(r['a'],r['b'],r['target']) for r in selected],device)
    yp=torch.tensor([r['target'] for r in selected],dtype=torch.long,device=device)
    t={'sign':teacher['sign'][picks].to(device),'digits':teacher['digits'][picks].to(device)}
    optim.zero_grad(set_to_none=True)
    fault=dual.loss_per_card(model(xb),yb).mean()
    ordinary=model(xp)
    preserve=dual.loss_per_card(ordinary,yp).mean()
    kd=preservation_kl(ordinary,t)
    norm=sum((p-old[k].to(p.device)).square().sum() for k,p in train_params.items())
    loss=args.defect_weight*fault+args.preserve_ce*preserve+args.distill_weight*kd+args.l2*norm
    if not bool(torch.isfinite(loss)):
        raise RuntimeError('DONOR_FREE_NONFINITE_LOSS')
    loss.backward()
    nn.utils.clip_grad_norm_(train_params.values(),args.clip,error_if_nonfinite=True)
    optim.step()
    proximal(train_params,old,masks,args.lr,
             args.sparsity if mode!='baseline' else 0.,
             args.group_sparsity if mode!='baseline' else 0.,args.max_delta_norm)
    return {'loss':float(loss.detach()),'defect_ce':float(fault.detach()),
            'preservation_ce':float(preserve.detach()),'kl':float(kd.detach())}


def stage_evaluate(model, defects, local, train_guard, dev_guard, baseline, args):
    """Training and DEV checks only. TEST is intentionally absent."""
    all_sets={'defects':defects,'local':local,'train':train_guard,'dev':dev_guard}
    pred={k:predictions(model,r,args.device,args.eval_batch) for k,r in all_sets.items()}
    scores={k:compare(baseline[k],pred[k],r) for k,r in all_sets.items()}
    defects_fixed=sum(p==r['target'] for p,r in zip(pred['defects'],defects))
    local_damaged=scores['local']['new_regressions']
    qualified=(defects_fixed==len(defects) and local_damaged==0 and
               scores['train']['new_regressions']<=args.max_train_regressions and
               scores['dev']['new_regressions']<=args.max_dev_regressions)
    return {'qualified':qualified,'defects_fixed':defects_fixed,'defects_total':len(defects),
            'local_damage':local_damaged,'train_guard':scores['train'],'dev_guard':scores['dev']}


def select_best(candidates):
    good=[r for r in candidates if r['qualified']]
    if not good:return None
    return min(good,key=lambda r:(r['dev_guard']['new_regressions'],r['train_guard']['new_regressions'],
                                   r['updated_elements'],r['delta_l2'],r['step'],r['mode']))


def save_csv(path, rows):
    if not rows:return
    temp=Path(str(path)+'.tmp')
    with temp.open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=sorted({key for row in rows for key in row}))
        writer.writeheader()
        for row in rows: writer.writerow({k:row.get(k,'') for k in writer.fieldnames})
    os.replace(temp,path)


def fit_mode(model, original, masks, mode, defects, local, train_guard, dev_guard,
             old_scores, preservation, teacher, args, out):
    """Independent initialization. Never read the later trained model."""
    model.load_state_dict(original,strict=True)
    params,hooks=configure_trainable(model,original,masks)
    optim=torch.optim.Adam(params.values(),lr=args.lr)
    rng=random.Random(f'donor-free:{args.seed}:{mode}')
    history=[];qualified=[];best_state=None
    best_info=None
    try:
        for step in range(1,args.steps+1):
            metrics=optimizer_step(model,optim,params,original,masks,defects,preservation,
                                   teacher,rng,args,mode,args.device)
            if step%args.check_every and step!=args.steps:
                continue
            with torch.no_grad():
                report=stage_evaluate(model,defects,local,train_guard,dev_guard,old_scores,args)
                state={k:v.detach().cpu().clone() for k,v in model.state_dict().items()}
                validate_frozen(original,state)
                report.update(step=step,mode=mode,updated_elements=diff_count(original,state),
                    delta_l2=math.sqrt(sum(float((state[k]-original[k]).square().sum())
                                           for k in INPUT_KEYS)),**metrics)
                history.append(report)
                if report['qualified']:
                    qualified.append(report)
                    if select_best([report,best_info] if best_info else [report]) is report:
                        best_info=report;best_state=state
                print('EMP_DONOR_FREE_CHECK='+json.dumps({k:report[k] for k in
                     ('mode','step','defects_fixed','qualified','local_damage','updated_elements')},sort_keys=True),flush=True)
    finally:
        for h in hooks:h.remove()
        model.requires_grad_(False)
    prune_trials=[]
    if mode=='sparse' and best_state is not None:
        best_state,prune_trials=prune_sparse_candidate(model,original,best_state,
                    defects,local,train_guard,dev_guard,old_scores,args)
        model.load_state_dict(best_state,strict=True)
        best_info={**best_info,'updated_elements':diff_count(original,best_state),
            'delta_l2':math.sqrt(sum(float((best_state[k]-original[k]).square().sum())
                                      for k in INPUT_KEYS)),
            'pruning_accepted':sum(bool(r['accepted']) for r in prune_trials)}
    atomic_json(out/f'{mode}_history.json',{'schema':SCHEMA,'checks':history,
                                            'prune_trials':prune_trials})
    return best_info,best_state



def prune_sparse_candidate(model, original, state, defects, local, train_guard,
                           dev_guard, old_scores, args):
    """Magnitude-based deletion of *independently learned* deltas.

    Each deletion is gated on TRAIN, local controls, and DEV, never TEST.
    The first failed deletion does not preclude smaller deletions succeeding.
    """
    current={k:v.detach().cpu().clone() for k,v in state.items()}
    audit=[]
    for fraction in (.5,.25,.10,.05,.02):
        values=[]
        for k in INPUT_KEYS:
            difference=(current[k]-original[k]).abs().reshape(-1)
            values.extend((float(x),k,i) for i,x in enumerate(difference) if float(x)>1e-8)
        if len(values)<2:break
        values.sort(key=lambda v:(v[0],v[1],v[2]))
        n=max(1,int(len(values)*fraction))
        trial={k:v.clone() for k,v in current.items()}
        for _,k,i in values[:n]:
            trial[k].view(-1)[i]=original[k].view(-1)[i]
        model.load_state_dict(trial,strict=True)
        metrics=stage_evaluate(model,defects,local,train_guard,dev_guard,old_scores,args)
        audit.append({'fraction':fraction,'deleted':n,'accepted':metrics['qualified'],
                      'remaining':diff_count(original,trial),
                      'dev_regressions':metrics['dev_guard']['new_regressions']})
        if metrics['qualified']:
            current=trial
    model.load_state_dict(current,strict=True)
    validate_frozen(original,current)
    return current,audit

def run(args):
    if (args.steps<1 or args.check_every<1 or args.preserve_batch<1 or
        args.eval_batch<1 or args.max_delta_norm<0):
        raise ValueError('DONOR_FREE_INVALID_HYPERPARAMETERS')
    modes=parse_modes(args.modes)
    root=Path(args.run).resolve();out=Path(args.output).resolve()
    campaign=Path(args.campaign).resolve()
    if out in (root,campaign) or out==root.parent:
        raise ValueError('DONOR_FREE_OUTPUT_MUST_BE_SEPARATE')
    snap_path=root/f'stage_{args.stage:03d}'/'mastered_snapshot.pt'
    cfg=json.loads((root/'config.json').read_text('utf-8'))
    original=cross.snapshot(snap_path,cfg,args.stage)
    for k in INPUT_KEYS:
        if k not in original:
            raise RuntimeError('DONOR_FREE_MISSING_INPUT_TENSOR:'+k)
    defects=defects_from_csv(args.defects)
    local=neighborhood(defects,DEFAULT_ANCHORS if not args.defects else [(r['a'],r['b']) for r in defects],
                       args.window,args.radius,args.max_delta)
    if not set((r['a'],r['b']) for r in defects).issubset({(r['a'],r['b']) for r in local}):
        raise RuntimeError('DONOR_FREE_DEFECT_MISSING_FROM_NEIGHBORHOOD')
    guards=build_sets(cfg,defects,local,parse_ints(args.guard_radii),args.guard_per_radius,args.stage)
    model,_,_,_,_=lasso.initialize(campaign,args.ladder_output,
                            campaign/args.hard_state_dir/'latest.pt',args.device)
    if set(model.state_dict())!=set(original):
        raise RuntimeError('DONOR_FREE_MODEL_ARCHITECTURE_DRIFT')
    model.load_state_dict(original,strict=True);model.eval().requires_grad_(False)
    all_sets={'defects':defects,'local':local,'train':guards['train'],'dev':guards['dev']}
    baseline={k:predictions(model,r,args.device,args.eval_batch) for k,r in all_sets.items()}
    wrong=[r for r,p in zip(defects,baseline['defects']) if p!=r['target']]
    if len(wrong)!=len(defects):
        raise RuntimeError('DONOR_FREE_EXPECTED_KNOWN_FAILURES_NOT_REPRODUCED')
    if any(p!=r['target'] for r,p in zip(local,baseline['local']) if (r['a'],r['b']) not in
           {(e['a'],e['b']) for e in defects}):
        raise RuntimeError('DONOR_FREE_LOCAL_HAS_UNDECLARED_FAILURES')
    train_guard=[r for r,p in zip(guards['train'],baseline['train']) if p==r['target']]
    if len(train_guard)<32:
        raise RuntimeError('DONOR_FREE_TOO_FEW_PRESERVATION_CONTROLS')
    # Cache reference distributions for training controls plus the observed local correct neighbors.
    local_good=[r for r,p in zip(local,baseline['local']) if p==r['target']]
    preserve=train_guard+local_good
    teacher=capture_teacher(model,preserve,args.device,args.eval_batch)
    config={'schema':SCHEMA,'run':str(root),'stage':args.stage,'source_sha256':cross.sha256(snap_path),
            'campaign':str(campaign),'output':str(out),'modes':modes,
            'known_defects':defects,'local_cards':len(local),
            'guard_counts':{k:len(v) for k,v in guards.items()},
            'seed':args.seed,'parameters':{k:v for k,v in vars(args).items()
                     if k not in ('output', 'run', 'campaign','ladder_output','hard_state_dir')},
            'oracle_coordinate_file_sha256':(cross.sha256(Path(args.oracle_mask)) if args.oracle_mask
                                               else None)}
    out.mkdir(parents=True,exist_ok=True)
    config_path=out/'config.json'
    if config_path.exists() and json.loads(config_path.read_text('utf-8'))!=json.loads(json.dumps(config)):
        raise RuntimeError('DONOR_FREE_OUTPUT_CONFIG_DRIFT')
    atomic_json(config_path,config)
    atomic_json(out/'initial.json',{'schema':SCHEMA,'defects':[{**r,'old_prediction':p}
                     for r,p in zip(defects,baseline['defects'])],
                     'local_correct':len(local_good),'train_preservation':len(train_guard)})
    candidates=[]
    for mode in modes:
        mask=input_masks(original,mode,args.oracle_mask)
        choice,state=fit_mode(model,original,mask,mode,defects,local,train_guard,
                              guards['dev'],baseline,preserve,teacher,args,out)
        if choice is not None:
            candidates.append((choice,state))
    selected=select_best([r for r,s in candidates])
    result={'schema':SCHEMA,'status':'no_qualified_candidate','repair_exported':False,
            'source_checkpoint_sha256':cross.sha256(snap_path),'modes':list(modes),
            'defect_cases':len(defects),'local_cards':len(local),
            'train_preservation':len(train_guard),'dev_cards':len(guards['dev']),
            'test_cards':len(guards['test']),'qualified_modes':[r['mode'] for r,_ in candidates],
            'output':str(out),
            'limitations':['Known defects are supervised labels, not independent test cases.',
                           'Local observed controls are optimization data, not blind validation.',
                           'TEST guard is a finite held-out sample, not a universal correctness proof.',
                           'The explicit oracle-coordinate mode uses donor-discovered LOCATION information.',
                           'Only the five input tensors are editable.']}
    artifact=out/'donor_free_repair.pt'
    if selected is not None:
        state=next(s for r,s in candidates if r is selected)
        validate_frozen(original,state)
        model.load_state_dict(state,strict=True);model.eval()
        # TEST predictions are computed precisely once, after candidate selection.
        base_model_test_predictions=crossover.evaluate_arm(model,original,guards['test'],args.device,args.eval_batch)
        test_old=[r['prediction'] for r in base_model_test_predictions]
        model.load_state_dict(state,strict=True)
        test_new=predictions(model,guards['test'],args.device,args.eval_batch)
        final_test=compare(test_old,test_new,guards['test'])
        result.update(selected_mode=selected['mode'],selection=selected,
                      final_test=final_test,updated_elements=diff_count(original,state),
                      status='certified' if final_test['new_regressions']<=args.max_test_regressions else 'test_rejected')
        if result['status']=='certified':
            payload={'schema':SCHEMA+'.checkpoint','model':state,'run_config':cfg,
                     'source_checkpoint_sha256':result['source_checkpoint_sha256'],
                     'mode':selected['mode'],'known_defects':defects,
                     'selection':selected,'test_guard':final_test}
            temp=out/'donor_free_repair.pt.tmp'
            torch.save(payload,temp);os.replace(temp,artifact)
            loaded=torch.load(artifact,map_location='cpu',weights_only=True)
            validate_frozen(original,loaded['model'])
            model.load_state_dict(loaded['model'],strict=True)
            if stage_evaluate(model,defects,local,train_guard,guards['dev'],baseline,args)['qualified'] is False:
                artifact.unlink(missing_ok=True)
                raise RuntimeError('DONOR_FREE_ROUNDTRIP_GATE_FAILED')
            result.update(repair_exported=True,repair_checkpoint=str(artifact),
                          repair_checkpoint_sha256=cross.sha256(artifact))
    if not result['repair_exported']:
        artifact.unlink(missing_ok=True)
    atomic_json(out/'result.json',result)
    print('EMP_DONOR_FREE_RESULT='+json.dumps({k:result[k] for k in
          ('status','repair_exported','qualified_modes','defect_cases','test_cards')},sort_keys=True),flush=True)
    return result


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',required=True)
    p.add_argument('--campaign',required=True)
    p.add_argument('--ladder-output',required=True)
    p.add_argument('--hard-state-dir',default='hard_emp_frontier_v2')
    p.add_argument('--output',required=True)
    p.add_argument('--stage',type=int,default=27)
    p.add_argument('--device',default='cuda' if torch.cuda.is_available() else 'cpu')
    p.add_argument('--defects',default=None,help='Optional CSV with a,b,target columns')
    p.add_argument('--modes',default='baseline,sparse')
    p.add_argument('--oracle-mask',default=None,help='Explicit oracle-coordinate control; never loads parameter values')
    p.add_argument('--radius',type=int,default=464)
    p.add_argument('--max-delta',type=int,default=32)
    p.add_argument('--window',type=int,default=2)
    p.add_argument('--guard-radii',default='128,256,384,464')
    p.add_argument('--guard-per-radius',type=int,default=1024)
    p.add_argument('--steps',type=int,default=1200)
    p.add_argument('--check-every',type=int,default=50)
    p.add_argument('--preserve-batch',type=int,default=64)
    p.add_argument('--eval-batch',type=int,default=128)
    p.add_argument('--lr',type=float,default=0.001)
    p.add_argument('--defect-weight',type=float,default=8.)
    p.add_argument('--preserve-ce',type=float,default=.35)
    p.add_argument('--distill-weight',type=float,default=1.5)
    p.add_argument('--l2',type=float,default=.01)
    p.add_argument('--sparsity',type=float,default=.0001)
    p.add_argument('--group-sparsity',type=float,default=.0001)
    p.add_argument('--max-delta-norm',type=float,default=1.)
    p.add_argument('--clip',type=float,default=1.)
    p.add_argument('--max-train-regressions',type=int,default=0)
    p.add_argument('--max-dev-regressions',type=int,default=0)
    p.add_argument('--max-test-regressions',type=int,default=0)
    p.add_argument('--seed',type=int,default=20261027)
    return p


if __name__=='__main__':
    run(parser().parse_args())
