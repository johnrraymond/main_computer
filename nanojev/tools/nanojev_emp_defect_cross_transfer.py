#!/usr/bin/env python3
"""Fixed-checkpoint cross-transfer and natural-repair test for NanoJev defects.

Loads successive *saved* mastered snapshots, measures the same local error
cluster before and after training, and fits two independent correct-only local
activation predictors. Site-only hooks are used for baseline AND intervention.
No training, checkpoint modification, or arithmetic oracle in model inputs.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
from pathlib import Path

import torch

from tools import nanojev_emp_dual_bank as dual
from tools import nanojev_emp_first_failure as ff
from tools import nanojev_emp_hard_lasso as lasso
from tools import nanojev_emp_isolated_defects as iso
from tools.nanojev_zero_line_ladder import atomic_json
from tools.nanojev_zero_line_zero_only import input_tensor

SCHEMA = 'nanojev.emp-defect-cross-transfer.v1'
DEFAULT_SITES = (
    ('norm@1', 25), ('backbone.layers.1@1', 25),
    ('norm@1', 31), ('norm@1', 35),
    ('backbone.layers.1@1', 35), ('backbone.layers.0@1', 31),
    ('norm@1', 4), ('backbone.layers.1@0', 47),
)


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def snapshot(path, config, stage):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f'CROSS_TRANSFER_MISSING_SNAPSHOT:{path}')
    obj = torch.load(path, map_location='cpu', weights_only=True)
    if (obj.get('schema') != iso.SCHEMA+'.mastery-snapshot' or
            obj.get('stage') != stage or obj.get('config') != config or
            not isinstance(obj.get('model'), dict)):
        raise RuntimeError(f'CROSS_TRANSFER_SNAPSHOT_DRIFT:{stage}')
    return obj['model']


def read_centers(run, stage, min_direct):
    root=Path(run)/f'stage_{stage:03d}'/f'relaxed_min_direct_{min_direct}'
    rs=[]
    for p in sorted(root.glob('candidate_*/report.json')):
        r=json.loads(p.read_text(encoding='utf-8'))
        if r.get('status') not in ('isolated','local_cluster') or not r.get('reconstruction_valid'):
            continue
        center=r['center']
        for k in ('m','delta','sign','swap'):
            if k not in center:
                raise RuntimeError(f'CROSS_TRANSFER_CENTER_MISSING:{k}')
        rs.append(center)
    if len(rs)<2:
        raise RuntimeError('CROSS_TRANSFER_NEEDS_TWO_RECONSTRUCTABLE_CENTERS')
    return rs


def gather_cluster(model, centers, radius, max_delta, device, batch, window):
    """Preserve actual signed/ordered operand branch; merge overlapping grids."""
    points={}
    for center in centers:
        for row in iso.branch_neighborhood(center,radius,max_delta,window):
            key=tuple(row['card'][:2]); points.setdefault(key,row)
    scored=iso.score(model,list(points.values()),device,batch)
    failures=[r for r in scored if not r['exact']]
    if not failures:
        raise RuntimeError('CROSS_TRANSFER_NO_FAILING_CASES_AT_BEFORE_CHECKPOINT')
    return scored,failures


def norm_key(site):
    name,invocation=site[0].rsplit('@',1)
    return name,int(invocation),int(site[1])


def response(model,card,device,site,replacement=None):
    """Identical single-site hook topology for baseline and interventions.

    Record the *native* pre-edit activation, return decoded answer and a signed
    true-answer vs original-wrong-answer log-score margin separately.
    """
    name,idx,channel=norm_key(site)
    modules=dict(ff.activation_modules(model))
    if name not in modules:raise RuntimeError(f'CROSS_TRANSFER_UNKNOWN_MODULE:{name}')
    count=[0]; captured=[]
    def hook(_module,_input,output):
        which=count[0];count[0]+=1
        if which!=idx:return None
        if not isinstance(output,torch.Tensor):raise RuntimeError('CROSS_TRANSFER_INVALID_OUTPUT')
        raw=ff.select_latent(output)
        if channel>=raw.shape[-1] or channel<0:raise RuntimeError('CROSS_TRANSFER_INVALID_CHANNEL')
        captured.append(float(raw[0,channel].detach()))
        if replacement is None:return None
        edited=output.clone()
        if edited.ndim==3:edited[:,-1,channel]=float(replacement)
        elif edited.ndim==2:edited[:,channel]=float(replacement)
        else:raise RuntimeError('CROSS_TRANSFER_INVALID_RANK')
        return edited
    handle=modules[name].register_forward_hook(hook)
    try:
        model.eval()
        with torch.inference_mode():
            outputs=model(input_tensor([card],device))
            prediction=int(dual.decoded(outputs)[0])
            # Raw output logits are retained for the target-vs-wrong comparison.
            logits={k:v.detach() for k,v in outputs.items() if k in ('sign','digits')}
    finally:
        handle.remove()
    if not captured:raise RuntimeError('CROSS_TRANSFER_HOOK_NOT_REACHED')
    return {'prediction':prediction,'activation':captured[0],'logits':logits}


def ll_value(logits,value):
    """Unnormalized joint output-class log score, using native loss definition."""
    device=logits['sign'].device
    target=torch.tensor([int(value)],dtype=torch.long,device=device)
    return -float(dual.loss_per_card(logits,target)[0].item())


def signed_margin(result,gold,original_error):
    return ll_value(result['logits'],gold)-ll_value(result['logits'],original_error)


def branch_for(row, m, delta, radius, max_delta):
    return iso.make_entry(int(m),int(delta),int(row['sign']),bool(row['swap']),radius,max_delta)


def collect_controls(model, target, radius, max_delta, device, batch, window, line_extent):
    """Dense 2D points plus extended constant-answer line; unique cards only."""
    points={}
    for row in iso.branch_neighborhood(target,radius,max_delta,window):
        points.setdefault(tuple(row['card'][:2]),row)
    for m in range(max(1,target['m']-line_extent),target['m']+line_extent+1):
        e=branch_for(target,m,target['delta'],radius,max_delta)
        if e is not None:
            points.setdefault(tuple(e['card'][:2]),e)
    rows=iso.score(model,list(points.values()),device,batch)
    center_key=tuple(target['card'][:2])
    controls=[r for r in rows if r['exact'] and tuple(r['card'][:2])!=center_key]
    # Same-target line points are selected independently of the neural values.
    same=sorted([r for r in controls if r['delta']==target['delta']],key=lambda r:r['m'])
    return controls,same


def holdout_ids(same, target_m, count):
    """Choose held-out same-target points on both sides without using activations."""
    low=[r for r in same if r['m']<target_m]
    high=[r for r in same if r['m']>target_m]
    if len(low)<3 or len(high)<3 or count<2:
        return []
    # Spread holdout points, preserving at least two training points per side.
    take_low=min(len(low)-2,count//2)
    take_high=min(len(high)-2,count-take_low)
    if take_low<1 or take_high<1:return []
    low=sorted(low,key=lambda r:abs(r['m']-target_m))
    high=sorted(high,key=lambda r:abs(r['m']-target_m))
    selected=low[1::2][:take_low]+high[1::2][:take_high]
    if len(selected)<2:return []
    return [tuple(r['card'][:2]) for r in selected]


def fit_models(training, heldout, target, site, device, model, min_holdouts, max_nrmse, ridge):
    """Fit both approaches to EXACTLY the same held-out correct line samples.

    Fit only on correct results with native site hook predictions matching the
    original arithmetic scorer. Return explicit reasons if no valid model.
    """
    rows=[]; drifts=[]
    for r in training+heldout:
        result=response(model,r['card'],device,site)
        if result['prediction']!=r['prediction']:
            drifts.append({'card':r['card'][:2],'scored':r['prediction'],
                           'hook':result['prediction']})
            continue
        rows.append((r,result['activation']))
    held_keys={tuple(r['card'][:2]) for r in heldout}
    fit=[(r,v) for r,v in rows if tuple(r['card'][:2]) not in held_keys]
    test=[(r,v) for r,v in rows if tuple(r['card'][:2]) in held_keys]
    if len(test)<min_holdouts or len(fit)<8:
        return {'status':'insufficient_parity_controls','hook_drifts':drifts,
                'fitting_controls':len(fit),'heldout_controls':len(test)}
    one_d=[(r,v) for r,v in fit if r['delta']==target['delta']]
    if len(one_d)<4 or not (any(r['m']<target['m'] for r,_ in one_d) and
                              any(r['m']>target['m'] for r,_ in one_d)):
        return {'status':'insufficient_bracket','hook_drifts':drifts}
    scale=max(1.,max(abs(r['m']-target['m']) for r,_ in fit))
    def line_design(items):
        return torch.tensor([[1.,(r['m']-target['m'])/scale] for r,_ in items],dtype=torch.float64)
    x1=line_design(one_d)
    w1=iso.ridge_fit(x1,torch.tensor([[v] for _,v in one_d],dtype=torch.float64),ridge)
    t1=line_design(test)
    expect1=float((torch.tensor([[1.,0.]],dtype=torch.float64)@w1)[0,0])
    pred1=(t1@w1).flatten()
    scale2=max(1.,max(max(abs(r['m']-target['m']),abs(r['delta']-target['delta'])) for r,_ in fit))
    def loc(rows):
        return [{**r,'m_offset':r['m']-target['m'],'delta_offset':r['delta']-target['delta']} for r,_ in rows]
    x2=iso.design(loc(fit),2,scale2)
    w2=iso.ridge_fit(x2,torch.tensor([[v] for _,v in fit],dtype=torch.float64),ridge)
    pred2=(iso.design(loc(test),2,scale2)@w2).flatten()
    expect2=float((iso.design([{'m_offset':0,'delta_offset':0}],2,scale2)@w2)[0,0])
    obs=torch.tensor([v for _,v in test],dtype=torch.float64)
    control_scale=max(.05,float(obs.std(unbiased=False)))
    def metrics(pred):
        rmse=float(torch.sqrt(((pred-obs)**2).mean()))
        return {'rmse':rmse,'nrmse':rmse/control_scale,'qualified':rmse/control_scale<=max_nrmse}
    return {'status':'ok','hook_drifts':drifts,'fitting_controls':len(fit),
            'heldout_controls':len(test),'heldout_cards':[r['card'][:2] for r,_ in test],
            'control_scale':control_scale,
            'line':{'predicted':expect1,**metrics(pred1)},
            'quadratic':{'predicted':expect2,**metrics(pred2)},
            'weights':{'line':w1,'quadratic':w2},'scales':{'line':scale,'quadratic':scale2},
            'heldout_rows':[r for r,_ in test]}


def predict_control(fit,method,row,target):
    w=fit['weights'][method]
    if method=='line':
        x=torch.tensor([[1.,(row['m']-target['m'])/fit['scales']['line']]],dtype=torch.float64)
    else:
        x=iso.design([{'m_offset':row['m']-target['m'],
                       'delta_offset':row['delta']-target['delta']}],2,fit['scales']['quadratic'])
    return float((x@w)[0,0])


def dose(observed,predicted,strength):
    return float(observed+strength*(predicted-observed))


def validate_args(args):
    if (args.after_stage<=args.before_stage or args.before_stage<0 or args.window<2 or
        args.line_extent<args.window+3 or args.min_holdouts<2 or args.max_nrmse<=0 or
        args.ridge<0 or not args.doses or args.controls<1):
        raise ValueError('CROSS_TRANSFER_INVALID_ARGUMENTS')


def evaluate_target(model, target, before_prediction, site, method, fit, args, controls):
    base=response(model,target['card'],args.device,site)
    gold=int(target['card'][2]); original=int(before_prediction)
    if base['prediction']!=original:
        return {'status':'hook_baseline_drift','hook_prediction':base['prediction'],'original':original}
    predicted=fit[method]['predicted'];delta=predicted-base['activation']
    baseline_margin=signed_margin(base,gold,original)
    graded=[]
    for strength in args.doses:
        new=response(model,target['card'],args.device,site,dose(base['activation'],predicted,strength))
        graded.append({'strength':strength,'prediction':new['prediction'],
                       'repaired':new['prediction']==gold,
                       'margin':signed_margin(new,gold,original),
                       'margin_gain':signed_margin(new,gold,original)-baseline_margin})
    # Control dose with same magnitude but opposite direction at SAME site.
    opposite=response(model,target['card'],args.device,site,base['activation']-delta)
    # Control perturbation at an unrelated deterministic channel, same magnitude.
    # Deterministic matched random-site control; never select the tested channel.
    # The NanoJev readout representation has 64 channels.
    choices=[j for j in range(64) if j!=site[1]]
    other_ch=random.Random(f'{args.seed}:{target["card"][:2]}:{site}').choice(choices)
    other_site=(site[0],other_ch)
    other_base=response(model,target['card'],args.device,other_site)
    other_result=None
    if other_base['prediction']==original:
        other_result=response(model,target['card'],args.device,other_site,
                              other_base['activation']+delta)
    # Collateral test on withheld correct controls; all have never fit this value.
    collateral=[]
    for row in controls[:args.controls]:
        original_control=response(model,row['card'],args.device,site)
        if original_control['prediction']!=row['card'][2]:
            collateral.append({'card':row['card'][:2],'status':'control_baseline_drift'})
            continue
        test_value=predict_control(fit,method,row,target)
        after=response(model,row['card'],args.device,site,test_value)
        collateral.append({'card':row['card'][:2], 'original':original_control['prediction'],
                           'after':after['prediction'],'damaged':after['prediction']!=row['card'][2]})
    return {'status':'tested','baseline_prediction':base['prediction'],
            'observed_activation':base['activation'],'predicted_activation':predicted,
            'baseline_margin':baseline_margin,'dose_response':graded,
            'repaired_at_1':any(x['strength']==1. and x['repaired'] for x in graded),
            'opposite_dose':{'prediction':opposite['prediction'],
                             'margin':signed_margin(opposite,gold,original)},
            'other_channel_control':{'site':other_site,'status':'tested' if other_result else 'baseline_drift',
                'prediction':other_result['prediction'] if other_result else None,
                'margin':signed_margin(other_result,gold,original) if other_result else None},
            'controls':collateral,
            'controls_damaged':sum(r.get('damaged',False) for r in collateral),
            'controls_tested':sum(r.get('status')!='control_baseline_drift' for r in collateral)}


def analyze_pair(model, target, args, site):
    # The model stays frozen at the historical before-stage snapshot.
    nearby, same=collect_controls(model,target,args.radius,args.max_delta,args.device,args.batch,
                                 args.window,args.line_extent)
    ids=holdout_ids(same,target['m'],args.holdouts)
    if len(ids)<args.min_holdouts:
        return {'status':'insufficient_same_target_holdouts','same_target_correct':len(same)}
    withheld=[r for r in nearby if tuple(r['card'][:2]) in set(ids)]
    train=[r for r in nearby if tuple(r['card'][:2]) not in set(ids)]
    fit=fit_models(train,withheld,target,site,args.device,model,args.min_holdouts,
                   args.max_nrmse,args.ridge)
    if fit['status']!='ok':return fit
    center_native=response(model,target['card'],args.device,site)
    if center_native['prediction']!=target['prediction']:
        return {'status':'center_hook_drift','expected':target['prediction'],
                'got':center_native['prediction']}
    public={k:v for k,v in fit.items() if k not in ('weights','heldout_rows')}
    results={}
    for name in ('quadratic','line'):
        # Even an unqualified method is probed for completeness, but any
        # resulting fix is labeled exploratory, not a validated repair.
        results[name]=evaluate_target(model,target,target['prediction'],site,name,fit,args,
                                      fit['heldout_rows'])
        results[name]['reconstruction_qualified']=bool(fit[name]['qualified'])
        results[name]['evidence_class']=('validated' if fit[name]['qualified'] else 'exploratory_unqualified')
    public['interventions']=results
    public['observed_activation']=center_native['activation']
    return public


def atomic_csv(path, rows):
    if not rows:return
    keys=list(rows[0]); temp=Path(str(path)+'.tmp')
    with temp.open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=keys);writer.writeheader();writer.writerows(rows)
    os.replace(temp,path)


def run(args):
    validate_args(args)
    path=Path(args.run).resolve(); out=Path(args.output).resolve()
    config=json.loads((path/'config.json').read_text(encoding='utf-8'))
    # Exact original experiment parameters determine native coordinate limits.
    history=json.loads((path/'history.json').read_text(encoding='utf-8'))
    if args.after_stage>=len(history):raise RuntimeError('CROSS_TRANSFER_AFTER_STAGE_NOT_ACCEPTED')
    args.radius=int(history[args.before_stage]['radius']);args.max_delta=int(history[args.before_stage]['max_delta'])
    centers=read_centers(path,args.before_stage,args.min_direct_correct)
    before_path=path/f'stage_{args.before_stage:03d}'/'mastered_snapshot.pt'
    after_path=path/f'stage_{args.after_stage:03d}'/'mastered_snapshot.pt'
    before=snapshot(before_path,config,args.before_stage)
    after=snapshot(after_path,config,args.after_stage)
    model,_,_,_,_=lasso.initialize(Path(args.campaign).resolve(),args.ladder_output,
                  Path(args.campaign).resolve()/args.hard_state_dir/'latest.pt',args.device)
    model.requires_grad_(False).eval()
    out.mkdir(parents=True,exist_ok=True)
    params={'schema':SCHEMA,'before_stage':args.before_stage,'after_stage':args.after_stage,
            'before_sha256':sha256(before_path),'after_sha256':sha256(after_path),
            'source_run':str(path),'campaign':str(Path(args.campaign).resolve()),
            'ladder_output':str(Path(args.ladder_output).resolve()),
            'hard_state_dir':args.hard_state_dir,'min_direct_correct':args.min_direct_correct,
            'seed':args.seed,'batch':args.batch,
            'window':args.window,'line_extent':args.line_extent,
            'holdouts':args.holdouts,'min_holdouts':args.min_holdouts,
            'max_nrmse':args.max_nrmse,'ridge':args.ridge,'doses':args.doses,
            'controls':args.controls,'sites':DEFAULT_SITES}
    cfg=out/'config.json'
    if cfg.exists() and json.loads(cfg.read_text(encoding='utf-8'))!=json.loads(json.dumps(params)):
        raise RuntimeError('CROSS_TRANSFER_OUTPUT_CONFIG_DRIFT')
    atomic_json(cfg,params)
    model.load_state_dict(before,strict=True)
    scanned,failures=gather_cluster(model,centers,args.radius,args.max_delta,args.device,args.batch,args.window)
    # Reject any silent mismatch with the exact two archived defect cases.
    indexed={tuple(r['card'][:2]):r for r in scanned}
    for center in centers:
        item=indexed.get(tuple(center['card'][:2]))
        if item is None or item['prediction']!=center['prediction'] or item['exact']:
            raise RuntimeError('CROSS_TRANSFER_ARCHIVED_CENTER_PREDICTION_DRIFT')
    after_predictions={}
    model.load_state_dict(after,strict=True)
    for r in iso.score(model,scanned,args.device,args.batch):
        after_predictions[tuple(r['card'][:2])]=r
    before_wrong={tuple(r['card'][:2]) for r in failures}
    natural=[]
    for r in scanned:
        key=tuple(r['card'][:2]); later=after_predictions[key]
        natural.append({'a':key[0],'b':key[1],'target':r['card'][2],
                        'before':r['prediction'],'after':later['prediction'],
                        'previously_wrong':key in before_wrong,
                        'repaired':key in before_wrong and later['exact'],
                        'regressed':r['exact'] and not later['exact']})
    atomic_csv(out/'natural_repair.csv',natural)
    model.load_state_dict(before,strict=True)
    events=[]; rows=[]
    for idx,target in enumerate(failures):
        event={'card':target['card'],'before_prediction':target['prediction'],
               'after_prediction':after_predictions[tuple(target['card'][:2])]['prediction'],
               'sites':[]}
        for site in DEFAULT_SITES:
            record=analyze_pair(model,target,args,site)
            # Compare native same-hook activations across checkpoints on exact card.
            before_state=response(model,target['card'],args.device,site)
            model.load_state_dict(after,strict=True)
            after_state=response(model,target['card'],args.device,site)
            model.load_state_dict(before,strict=True)
            record['site']={'layer':site[0],'channel':site[1]}
            record['natural']={'before_activation':before_state['activation'],
                               'after_activation':after_state['activation'],
                               'delta':after_state['activation']-before_state['activation'],
                               'before_prediction':before_state['prediction'],
                               'after_prediction':after_state['prediction'],
                               'before_margin':signed_margin(before_state,target['card'][2],target['prediction']),
                               'after_margin':signed_margin(after_state,target['card'][2],target['prediction'])}
            event['sites'].append(record)
            for method in ('quadratic','line'):
                r=record.get('interventions',{}).get(method,{})
                rows.append({'a':target['card'][0],'b':target['card'][1],
                             'layer':site[0],'channel':site[1],'method':method,
                             'model_status':record.get('status'),
                             'holdout_nrmse':record.get(method,{}).get('nrmse'),
                             'qualified':record.get(method,{}).get('qualified'),
                             'intervention_status':r.get('status'),
                             'repaired_at_1':r.get('repaired_at_1',False),
                             'controls_tested':r.get('controls_tested',0),
                             'controls_damaged':r.get('controls_damaged',0),
                             'naturally_repaired':after_state['prediction']==target['card'][2],
                             'evidence_class':r.get('evidence_class')})
        events.append(event)
        atomic_json(out/f'failure_{idx:03d}.json',event)
        print('EMP_DEFECT_TRANSFER_EVENT='+json.dumps({'index':idx,'card':target['card'][:3],
               'before':target['prediction'],'after':event['after_prediction'],
               'evaluated_sites':len(event['sites'])},sort_keys=True),flush=True)
    atomic_csv(out/'interventions.csv',rows)
    result={'schema':SCHEMA,'status':'complete','before_stage':args.before_stage,
            'after_stage':args.after_stage,'cluster_cases':len(scanned),
            'cluster_failures':len(failures),'natural_repairs':sum(x['repaired'] for x in natural),
            'natural_regressions':sum(x['regressed'] for x in natural),
            'trained_only_before':True,'sites_per_failure':len(DEFAULT_SITES),
            'interventions_tested':sum(r['intervention_status']=='tested' for r in rows),
            'repaired_interventions':sum(bool(r['repaired_at_1']) for r in rows),
            'validated_repairs':sum(bool(r['repaired_at_1']) and bool(r['qualified']) for r in rows),
            'exploratory_repairs':sum(bool(r['repaired_at_1']) and not bool(r['qualified']) for r in rows),
            'qualified_models':sum(bool(r['qualified']) for r in rows),
            'output':str(out),'limitations':['Local correct-only fits are not unique neural ground truth',
                'Output margin is native sign/digit composite log score, not a normalized probability',
                'Random-channel and opposite-dose interventions are controls, not a mechanistic proof']}
    atomic_json(out/'result.json',result)
    print('EMP_DEFECT_CROSS_TRANSFER_RESULT='+json.dumps(result,sort_keys=True),flush=True)
    return result


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',required=True)
    p.add_argument('--campaign',required=True)
    p.add_argument('--ladder-output',required=True)
    p.add_argument('--hard-state-dir',default='hard_emp_frontier_v2')
    p.add_argument('--output',required=True)
    p.add_argument('--device',default='cuda' if torch.cuda.is_available() else 'cpu')
    p.add_argument('--before-stage',type=int,default=27)
    p.add_argument('--after-stage',type=int,default=28)
    p.add_argument('--min-direct-correct',type=int,default=6)
    p.add_argument('--window',type=int,default=2)
    p.add_argument('--line-extent',type=int,default=12)
    p.add_argument('--holdouts',type=int,default=6)
    p.add_argument('--min-holdouts',type=int,default=4)
    p.add_argument('--max-nrmse',type=float,default=.6)
    p.add_argument('--ridge',type=float,default=.01)
    p.add_argument('--doses',type=str,default='0,0.25,0.5,1,1.5,2')
    p.add_argument('--controls',type=int,default=6)
    p.add_argument('--batch',type=int,default=128)
    p.add_argument('--seed',type=int,default=20261027)
    return p


if __name__=='__main__':
    a=parser().parse_args()
    a.doses=[float(x) for x in a.doses.split(',')]
    run(a)
