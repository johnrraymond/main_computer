#!/usr/bin/env python3
"""Competence-first radius spiral and local activation reconstruction for NanoJev.

Trains an independent cloned Hard EMP. A radius is accepted only after both
finite-TRAIN memorization and fixed, disjoint held-out probe acceptance. After
training, previously accepted probes are retested for retention regressions;
isolated errors and small local error clusters (at least six of eight
direct neighbors correct) are modeled from nearby correct forward activations. Models are checked on withheld *correct*
neighbors before interventions. No original campaign/heads/detector is changed.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import random
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from xml.sax.saxutils import escape

import torch
from torch import nn

from tools import nanojev_emp_first_failure as ff
from tools import nanojev_emp_dual_bank as dual
from tools import nanojev_emp_hard_lasso as lasso
from tools.nanojev_zero_line_zero_only import input_tensor, MAX_ABS
from tools.nanojev_zero_line_ladder import atomic_checkpoint, atomic_json

SCHEMA = 'nanojev.emp-isolated-defect-reconstruction.v1'


def stable_bucket(card, seed):
    payload = f'isolated:{seed}:{int(card[0])}:{int(card[1])}'.encode()
    return hashlib.sha256(payload).digest()[0] % 5


def regime(card):
    a, b = card[:2]
    return 'cancellation' if a*b < 0 else 'same_sign_or_zero'


def make_entry(m, d, sign, swap, radius, max_delta):
    if m < 1 or abs(d) > max_delta:
        return None
    a, b = sign*m, d-sign*m
    if swap:
        a, b = b, a
    if max(abs(a), abs(b)) > radius or max(abs(a), abs(b)) > MAX_ABS:
        return None
    # Zero-line experiment: exclude same-sign cases that confounded the Atlas.
    if a*b > 0:
        return None
    c = ff.card(a, b)
    return {'card': c, 'm': int(m), 'delta': int(d), 'sign': int(sign),
            'swap': bool(swap), 'regime': regime(c)}


def finite_candidates(radius, max_delta, seed, target_bucket):
    """Enumerate only reasonably small integer domains, then sample without bias.

    This prevents millions of futile duplicate draws in a nearly exhausted
    training fold at small radii. Every ordered input belongs to one fold.
    """
    volume = radius * (2 * max_delta + 1) * 4
    if volume > 250_000:
        return None
    cards = {}
    for m in range(1, radius + 1):
        for d in range(-max_delta, max_delta + 1):
            for sign in (-1, 1):
                for swap in (False, True):
                    entry = make_entry(m, d, sign, swap, radius, max_delta)
                    if entry is not None and stable_bucket(entry['card'], seed) in target_bucket:
                        cards.setdefault(tuple(entry['card'][:2]), entry)
    return list(cards.values())


def probe_entries(radius, max_delta, count, seed, phase='probe'):
    """Fixed disjoint probe cards; always log the actual finite probe count."""
    rng = random.Random(f'iso-probe:{seed}:{radius}:{max_delta}:{count}:{phase}')
    target_bucket = 1 if phase == 'test' else 0
    finite = finite_candidates(radius, max_delta, seed, {target_bucket})
    if finite is not None:
        rng.shuffle(finite)
        return finite[:count]
    points = {}
    attempts = 0
    while len(points) < count and attempts < max(20_000, count*150):
        attempts += 1
        m = rng.randint(1, radius)
        d = rng.randint(-max_delta, max_delta)
        e = make_entry(m, d, rng.choice((-1, 1)), bool(rng.randrange(2)), radius, max_delta)
        if e is not None and stable_bucket(e['card'], seed) == target_bucket:
            points.setdefault(tuple(e['card'][:2]), e)
    if len(points) < count:
        raise RuntimeError(f'ISOLATED_PROBE_DECK_EXHAUSTED:{len(points)}:{count}')
    return list(points.values())


def training_entries(radius, max_delta, count, seed):
    rng = random.Random(f'iso-train:{seed}:{radius}:{max_delta}:{count}')
    finite = finite_candidates(radius, max_delta, seed, {2, 3, 4})
    if finite is not None:
        rng.shuffle(finite)
        result = finite[:count]
    else:
        points = {}
        attempts = 0
        while len(points) < count and attempts < max(20_000, count*150):
            attempts += 1
            m = rng.randint(1, radius)
            d = rng.randint(-max_delta, max_delta)
            e = make_entry(m, d, rng.choice((-1, 1)), bool(rng.randrange(2)), radius, max_delta)
            if e is not None and stable_bucket(e['card'], seed) not in (0, 1):
                points.setdefault(tuple(e['card'][:2]), e)
        result = list(points.values())
    if len(result) < min(12, count):
        raise RuntimeError('ISOLATED_TRAIN_DECK_EXHAUSTED')
    return result


def score(model, entries, device, batch):
    scored = ff.score_cards(model, entries, SimpleNamespace(device=device,eval_batch=batch,failure_mode='exact'))
    for row in scored:
        row['error'] = int(row['prediction']) - int(row['card'][2])
    return scored


def train_to_gate(model, optimizer, train, probe, args, stage, retention=None,
                  resume_steps=0, rng_state=None, checkpoint=None):
    """Fit TRAIN only; DEV is read-only and checked at fixed intervals."""
    deck = [row['card'] for row in train]
    rng = random.Random(f'iso-fit:{args.seed}:{stage}')
    if rng_state is not None:
        rng.setstate(rng_state)
    metric = ff.deck_metrics(model,deck,SimpleNamespace(device=args.device,eval_batch=args.eval_batch))
    held = score(model,probe,args.device,args.eval_batch)
    probe_acc = sum(r['exact'] for r in held)/len(held)
    retention = retention or []
    old = score(model,retention,args.device,args.eval_batch) if retention else []
    retention_acc = sum(r['exact'] for r in old)/len(old) if old else 1.
    steps=0
    while steps < args.stage_steps and not (metric['mean_ce']<=args.loss_target and
                                                 metric['exact_accuracy']==1 and probe_acc>=args.mastery_gate and
                                                 retention_acc>=args.retention_gate):
        model.train()
        cards = [deck[rng.randrange(len(deck))] for _ in range(args.batch)]
        x = input_tensor(cards,args.device)
        y = torch.tensor([r[2] for r in cards],dtype=torch.long,device=args.device)
        optimizer.zero_grad(set_to_none=True)
        loss = dual.loss_per_card(model(x),y).mean()
        if not torch.isfinite(loss):
            raise FloatingPointError('ISOLATED_NONFINITE_LOSS')
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(),1.,error_if_nonfinite=True)
        optimizer.step()
        steps+=1
        if steps%args.check_every==0 or steps==args.stage_steps:
            metric = ff.deck_metrics(model,deck,SimpleNamespace(device=args.device,eval_batch=args.eval_batch))
            held = score(model,probe,args.device,args.eval_batch)
            probe_acc = sum(r['exact'] for r in held)/len(held)
            old = score(model,retention,args.device,args.eval_batch) if retention else []
            retention_acc = sum(r['exact'] for r in old)/len(old) if old else 1.
            if checkpoint is not None:
                checkpoint(resume_steps+steps, rng.getstate())
    accepted=(metric['mean_ce']<=args.loss_target and metric['exact_accuracy']==1 and
              probe_acc>=args.mastery_gate and retention_acc>=args.retention_gate)
    return {'steps':resume_steps+steps,'steps_this_invocation':steps,'train_count':len(train),'probe_count':len(probe),
            **metric,'probe_exact_accuracy':probe_acc,'retention_exact_accuracy':retention_acc,
            'retention_count':len(retention),'accepted':accepted},held


def branch_neighborhood(center, radius, max_delta, window):
    """Actual unit-step (m,d) grid on the same signed, ordered operand branch."""
    result=[]
    for dm in range(-window,window+1):
        for dd in range(-window,window+1):
            e=make_entry(center['m']+dm,center['delta']+dd,
                         center['sign'],center['swap'],radius,max_delta)
            if e is None:
                continue
            e.update(m_offset=dm,delta_offset=dd,center=dm==0 and dd==0)
            result.append(e)
    return result


def direct_neighbors(rows):
    bycoord={(r['m_offset'],r['delta_offset']):r for r in rows}
    offsets=[(x,y) for x in (-1,0,1) for y in (-1,0,1) if (x,y)!=(0,0)]
    return [bycoord[o] for o in offsets if o in bycoord]


def isolated_eligibility(scored, radius, max_delta, window, min_direct_correct=6):
    """Admit tight error clusters when enough direct controls are correct.

    The complete 8-neighbor grid remains mandatory to avoid silently treating
    out-of-domain samples as successes. Channel reconstruction still requires
    its *own* sufficient correct controls and held-out accuracy validation.
    """
    bycoord={(r['m_offset'],r['delta_offset']):r for r in scored}
    center=bycoord.get((0,0))
    adjacent=direct_neighbors(scored)
    direct_good=sum(bool(r['exact']) for r in adjacent)
    reason = ('missing_center' if center is None else
              'center_correct' if center['exact'] else
              'incomplete_neighbors' if len(adjacent)!=8 else
              'neighbor_failure' if direct_good<min_direct_correct else
              'isolated' if direct_good==8 else
              'local_cluster')
    return reason,adjacent


def design(rows, degree, scale):
    features=[]
    for r in rows:
        x=float(r['m_offset'])/scale
        y=float(r['delta_offset'])/scale
        vec=[1.,x,y]
        if degree==2:
            vec.extend((x*x,x*y,y*y))
        features.append(vec)
    return torch.tensor(features,dtype=torch.float64)


def ridge_fit(x,y,alpha):
    # Do not regularize the intercept; stable for underdetermined designs.
    penalty=torch.eye(x.shape[1],dtype=torch.float64)*alpha
    penalty[0,0]=0.
    return torch.linalg.solve(x.T@x+penalty,x.T@y)


def local_fit(controls, actual, degree=2, ridge=0.01, holdouts=6,
              min_signal=0.05, required_r2=0.0, threshold=3.0):
    """Predict failure-channel activations using only correct neighbors.

    Fit/holdout partitions are deterministic and disjoint. Reject individual
    channels with poor held-out predictions, rather than hallucinating a smooth
    expected neuron state at a discontinuity. This is local interpolation,
    not a ground-truth neural state or unique causal explanation.
    """
    if len(controls)<holdouts+(6 if degree==2 else 3):
        return {'valid':False,'reason':'insufficient_correct_controls'}
    keys=set(actual[1]);
    for row,acts in controls:keys &= set(acts)
    if not keys:return {'valid':False,'reason':'no_common_activation_layers'}
    controls=sorted(controls,key=lambda q:(abs(q[0]['m_offset'])+abs(q[0]['delta_offset']),
                     q[0]['m_offset'],q[0]['delta_offset']))
    # Spread holdouts across the neighborhood, never include center.
    withheld=controls[::max(1,len(controls)//holdouts)][:holdouts]
    withheld_ids={id(r) for r in withheld}
    fitting=[r for r in controls if id(r) not in withheld_ids]
    if len(fitting)<(6 if degree==2 else 3):
        return {'valid':False,'reason':'insufficient_fitting_controls'}
    scale=max(1.,max(max(abs(r['m_offset']),abs(r['delta_offset'])) for r,_ in controls))
    fit_x=design([r for r,_ in fitting],degree,scale)
    test_x=design([r for r,_ in withheld],degree,scale)
    center_x=design([actual[0]],degree,scale)
    results=[];expected={};quality={};trained={}
    for layer in sorted(keys):
        y=torch.stack([v[layer].double() for _,v in fitting])
        yhold=torch.stack([v[layer].double() for _,v in withheld])
        weights=ridge_fit(fit_x,y,ridge)
        prediction=(center_x@weights).squeeze(0)
        yguess=test_x@weights
        # Channel-local holdout error normalized by *observed* control scale;
        # avoid declaring a channel smooth because mean squared loss is small
        # while its absolute variations are also tiny.
        mse=((yguess-yhold)**2).mean(0)
        denom=yhold.std(0,unbiased=False).clamp_min(min_signal)
        nrmse=torch.sqrt(mse)/denom
        observed=actual[1][layer].double()
        dev=(observed-prediction).abs()
        # Anomaly must beat ordinary withheld prediction deviations, with
        # a conservative floor that prevents division by tiny noise values.
        err_scale=torch.sqrt(mse).clamp_min(min_signal)
        z=dev/err_scale
        expected[layer]=prediction.float()
        quality[layer]=nrmse.float()
        trained[layer]=weights.float()
        for ch in range(prediction.numel()):
            if float(nrmse[ch]) > required_r2:
                continue
            results.append({'layer':layer,'channel':ch,
                'predicted':float(prediction[ch]),'observed':float(observed[ch]),
                'activation_residual':float(observed[ch]-prediction[ch]),
                'standardized_residual':float(z[ch]),
                'holdout_nrmse':float(nrmse[ch]),
                'qualified':bool(z[ch]>=threshold)})
    qualified=sorted([v for v in results if v['qualified']],
                     key=lambda v:(-v['standardized_residual'],v['holdout_nrmse'],v['layer'],v['channel']))
    return {'valid':bool(results),'reason':'ok' if results else 'no_predictable_channels',
            'fit_controls':len(fitting),'heldout_controls':len(withheld),
            'layers':len(expected),'predictable_channels':len(results),
            'candidates':qualified,'expected':expected,'quality':quality,'weights':trained,
            'scale':scale,'degree':degree,'ridge':ridge,'holdout_rows':[r for r,_ in withheld]}


def patch_predict(model,card,device,replacements):
    """No-op and counterfactual calls use identical hook placement.

    replacements are {(module_name@invocation_index, channel): value}; empty
    replacements must still install a hook for a particular intervention site,
    passed as None values, to guard against hook-sensitive fast-path changes.
    """
    modules=dict(ff.activation_modules(model))
    grouped={}
    for (key,ch),v in replacements.items():
        name,index=key.rsplit('@',1)
        if name not in modules:raise RuntimeError(f'ISOLATED_UNKNOWN_LAYER:{name}')
        grouped.setdefault(name,{}).setdefault(int(index),{})[int(ch)]=v
    handles=[];counts=Counter()
    for name,rounds in grouped.items():
        def make_hook(layer_name,per_round):
            def hook(_module,_input,output):
                index=counts[layer_name];counts[layer_name]+=1
                change=per_round.get(index)
                if change is None or all(v is None for v in change.values()):return None
                edited=output.clone()
                for channel,value in change.items():
                    if value is None:continue
                    if edited.ndim==3:edited[:,-1,channel]=float(value)
                    else:edited[:,channel]=float(value)
                return edited
            return hook
        handles.append(modules[name].register_forward_hook(make_hook(name,rounds)))
    try:
        model.eval()
        with torch.inference_mode():
            result=dual.decoded(model(input_tensor([card],device)))[0]
    finally:
        for h in handles:h.remove()
    for name,rounds in grouped.items():
        if counts[name] <= max(rounds):
            raise RuntimeError('ISOLATED_INTERVENTION_NOT_REACHED')
    return int(result)


def evaluate_repair(model, center, holdouts, candidate, predicted, args):
    layer,channel=candidate['layer'],candidate['channel']
    coordinate=(layer,channel)
    site={coordinate:None}
    raw=int(center['prediction'])
    no_op=patch_predict(model,center['card'],args.device,site)
    if no_op!=raw:
        return {'layer':layer,'channel':channel,'status':'hook_prediction_drift',
                'baseline':raw,'no_op':no_op,'valid':False}
    altered=patch_predict(model,center['card'],args.device,{coordinate:predicted})
    tested=0;damaged=0;control_drift=0
    for row in holdouts:
        origin=patch_predict(model,row['card'],args.device,site)
        if origin!=row['prediction']:
            control_drift+=1
            continue
        # Only patch center; to test collateral damage we replace a control's
        # channel with its locally predicted counterfactual value too.
        control_pred=args._predict_holdout(layer,channel,row)
        changed=patch_predict(model,row['card'],args.device,{coordinate:control_pred})
        tested+=1
        damaged+=int(changed!=row['card'][2])
    return {'layer':layer,'channel':channel,'status':'tested','valid':True,
            'before':raw,'after':altered,'expected':center['card'][2],
            'repaired':altered==center['card'][2],
            'holdout_controls_tested':tested,'holdout_controls_damaged':damaged,
            'holdout_hook_drifts':control_drift}


def model_at(model, weights, row, layer, channel, scale, degree):
    x=design([row],degree,scale)
    return float((x@weights[layer].double())[0,channel])


def neighborhood_diagnostic(model, selected, radius, max_delta, args, folder):
    cases=branch_neighborhood(selected,radius,max_delta,args.window)
    if not any(r['center'] for r in cases):
        return {'status':'missing_center'}
    scored=score(model,cases,args.device,args.eval_batch)
    status, neighbors=isolated_eligibility(scored,radius,max_delta,args.window,
                                           args.min_direct_correct)
    folder.mkdir(parents=True,exist_ok=True)
    write_svg(folder/'arithmetic_neighborhood.svg',scored)
    write_gzip(folder/'local_predictions.jsonl.gz',scored)
    center=next(r for r in scored if r['center'])
    summary={'schema':SCHEMA+'.event','status':status,'radius':radius,
             'center':ff.clean_row(center),'neighborhood_count':len(scored),
             'wrong_neighbors':sum(not r['exact'] for r in scored if not r['center']),
             'direct_correct':sum(r['exact'] for r in neighbors),
             'direct_total':len(neighbors),
             'min_direct_correct':args.min_direct_correct}
    if status not in ('isolated','local_cluster'):
        atomic_json(folder/'report.json',summary)
        return summary
    # Isolation must be confirmed under an activation-capture path. If changing
    # the hook environment changes the answer, exclude rather than overwrite.
    capture_rows=[];drift=[]
    for row in scored:
        if not row['exact'] and not row['center']:continue
        prediction,acts=ff.capture(model,row['card'],args.device)
        if prediction!=row['prediction']:
            drift.append({'card':list(row['card']),'original':row['prediction'],'capture':prediction})
            continue
        capture_rows.append((row,acts))
    central=next((x for x in capture_rows if x[0]['center']),None)
    if central is None:
        summary.update(status='capture_mismatch',capture_disagreements=drift)
        atomic_json(folder/'report.json',summary);return summary
    controls=[c for c in capture_rows if c[0]['exact'] and not c[0]['center']]
    fit=local_fit(controls,central,args.degree,args.ridge,args.holdouts,
                  args.signal_floor,args.max_holdout_nrmse,args.anomaly_threshold)
    summary.update(capture_disagreements=drift,model_valid=fit['valid'],
                   local_model_reason=fit['reason'])
    if not fit['valid']:
        atomic_json(folder/'report.json',summary);return summary
    ff.activation_heatmap_svg(folder/'activation_residual_heatmap.svg',
        [{'layer':v['layer'],'channel':v['channel'],
          'anomaly':v['standardized_residual']} for v in fit['candidates']])
    summary.update(reconstruction_valid=True,fit_controls=fit['fit_controls'],heldout_controls=fit['heldout_controls'],
                   predictable_channels=fit['predictable_channels'],
                   anomalous_candidates=len(fit['candidates']),
                   candidates=fit['candidates'][:args.report_top])
    # Keep full predictions and activation state for independent analysis.
    atomic_checkpoint(folder/'activation_reconstruction.pt',{'schema':SCHEMA+'.activations',
        'center':central,'controls':controls,'expected':fit['expected'],
        'quality':fit['quality'],'fitted_weights':fit['weights']})
    class _A:
        def __init__(self, orig, fn):self.device=orig.device;self._predict_holdout=fn
    helper=_A(args,lambda layer,ch,row:model_at(model,fit['weights'],row,layer,ch,fit['scale'],args.degree))
    heldout=[]
    for r in fit['holdout_rows']:
        old=next((x for x in controls if x[0] is r),None)
        if old:heldout.append(old[0])
    repair=[]
    for cand in fit['candidates'][:args.interventions]:
        repair.append(evaluate_repair(model,center,heldout,cand,cand['predicted'],helper))
    summary['interventions']=repair
    summary['repaired']=sum(x.get('repaired',False) for x in repair)
    atomic_json(folder/'report.json',summary)
    return summary


def write_gzip(path,rows):
    temp=path.with_suffix(path.suffix+'.tmp')
    with gzip.open(temp,'wt',encoding='utf-8') as fp:
        for row in rows:fp.write(json.dumps(ff.clean_row(row),sort_keys=True)+'\n')
    os.replace(temp,path)


def write_svg(path,scored):
    width=17; coords={(r['m_offset'],r['delta_offset']):r for r in scored}
    mx=max((abs(x) for x,y in coords),default=1);dy=max((abs(y) for x,y in coords),default=1)
    lines=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{120+width*(2*mx+1)}" height="{75+width*(2*dy+1)}">',
           '<rect width="100%" height="100%" fill="#f8fafc"/>',
           '<text x="10" y="18" font-size="13">Arithmetic defect neighborhood (green correct, red incorrect)</text>']
    for (x,y),row in coords.items():
        px=55+(x+mx)*width;py=27+(dy-y)*width
        color='#7ebaaa' if row['exact'] else '#d57670'
        lines.append(f'<rect x="{px}" y="{py}" width="16" height="16" fill="{color}"/>')
        if x==0 and y==0:lines.append(f'<rect x="{px}" y="{py}" width="16" height="16" fill="none" stroke="black" stroke-width="2"/>')
    lines.append('</svg>')
    path.write_text('\n'.join(lines),encoding='utf-8')


def pick_candidates(model,probe,prior,radius,max_delta,args):
    # Prior accepted *identical* cards are retested; regressions are preferred
    # compact clusters with sufficient correct neighbors also qualify.
    now=score(model,probe,args.device,args.eval_batch)
    former={(tuple(r['card'][:2])):r for r in prior}
    candidates=[]
    for r in now:
        if r['exact']:continue
        old=former.get(tuple(r['card'][:2]))
        if old is None:continue
        prior_good=bool(old['exact'])
        # Interior of the currently trained region: full 8-neighbor grid.
        if not (2<=r['m']<radius-args.window and abs(r['delta'])+args.window<=max_delta):continue
        candidates.append((0 if prior_good else 1, r['m'],abs(r['delta']),r))
    candidates.sort(key=lambda q:q[:3])
    return [r for _,_,_,r in candidates],now


def validate_args(args):
    if (args.start_radius<8 or args.radius_step<1 or args.max_radius<args.start_radius or
        args.max_radius>MAX_ABS//2 or args.initial_delta<2 or args.delta_step<0 or
        args.max_delta<args.initial_delta or args.window<1 or args.max_delta<args.window+1 or
        args.train_size<12 or args.probe_size<16 or args.audit_size<16 or args.stage_steps<1 or
        args.eval_batch<1 or args.batch<2 or args.check_every<1 or
        not 0<args.loss_target<1 or not .5<=args.mastery_gate<=1 or
        args.degree not in (1,2) or args.holdouts<2 or args.interventions<0 or
        args.rehearsal<0 or args.retention_cases<0 or not .5<=args.retention_gate<=1 or
        args.max_events<0 or not 0<=args.min_direct_correct<=8 or
        args.max_holdout_nrmse<=0 or args.anomaly_threshold<=0):
        raise ValueError('ISOLATED_INVALID_ARGUMENTS')


def stage_config(args,digest,reverse):
    keys=('seed','start_radius','radius_step','max_radius','initial_delta','delta_step',
          'max_delta','train_size','probe_size','audit_size','stage_steps','loss_target',
          'mastery_gate','check_every','batch','eval_batch','lr','window',
          'degree','ridge','holdouts','signal_floor','max_holdout_nrmse',
          'rehearsal','retention_cases','retention_gate',
          'anomaly_threshold','interventions','max_events','report_top')
    return {'schema':SCHEMA,'source_sha256':digest,'reverse_sha256':reverse,
            'parameters':{k:getattr(args,k) for k in keys}}


def revisit_stage_candidates(model, out, history, config, args):
    """Re-evaluate saved candidate grids without modifying the trained spiral.

    Reads the historical stage checkpoint, not final/latest.pt weights. Places
    results in a separate directory so original reports remain untouched.
    The two new CLI options are diagnostic-only and deliberately excluded from
    the training/resume config, which must remain byte-compatible with v1.
    """
    stage=args.revisit_stage
    if stage<0 or stage>=len(history):
        raise ValueError(f'ISOLATED_REVISIT_STAGE_NOT_ACCEPTED:{stage}')
    folder=out/f'stage_{stage:03d}'
    source=folder/'mastered_snapshot.pt'
    if not source.is_file():
        raise FileNotFoundError(f'ISOLATED_REVISIT_SNAPSHOT_MISSING:{source}')
    saved=torch.load(source,map_location='cpu',weights_only=True)
    if (saved.get('schema')!=SCHEMA+'.mastery-snapshot' or
        saved.get('config')!=config or int(saved.get('stage',-1))!=stage):
        raise RuntimeError('ISOLATED_REVISIT_SNAPSHOT_DRIFT')
    model.load_state_dict(saved['model'])
    model.eval()
    records=history[stage].get('candidate_reports',[])
    if not records:
        raise RuntimeError(f'ISOLATED_REVISIT_NO_SAVED_CANDIDATES:{stage}')
    destination=folder/f'relaxed_min_direct_{args.min_direct_correct}'
    reports=[]
    for index,item in enumerate(records):
        source_report=folder/f'candidate_{index:03d}'/'report.json'
        if not source_report.is_file():
            raise FileNotFoundError(f'ISOLATED_REVISIT_REPORT_MISSING:{source_report}')
        old=json.loads(source_report.read_text(encoding='utf-8'))
        center=old['center']
        for key in ('m','delta','sign','swap'):
            if key not in center:
                raise RuntimeError(f'ISOLATED_REVISIT_CENTER_MISSING:{key}')
        new=neighborhood_diagnostic(model,center,history[stage]['radius'],
                                    history[stage]['max_delta'],args,
                                    destination/f'candidate_{index:03d}')
        reports.append({'candidate':item['candidate'],'old_status':old['status'],
                        'status':new['status'],'reconstruction_valid':new.get('reconstruction_valid',False),
                        'repaired':new.get('repaired',0),
                        'report':str(destination/f'candidate_{index:03d}'/'report.json')})
    result={'schema':SCHEMA+'.revisit','stage':stage,'radius':history[stage]['radius'],
            'max_delta':history[stage]['max_delta'],'min_direct_correct':args.min_direct_correct,
            'checkpoint':str(source),'candidate_count':len(reports),
            'local_clusters':sum(r['status']=='local_cluster' for r in reports),
            'isolated':sum(r['status']=='isolated' for r in reports),
            'reconstructable':sum(r['reconstruction_valid'] for r in reports),
            'repairs':sum(r['repaired'] for r in reports),'reports':reports}
    atomic_json(destination/'result.json',result)
    print('EMP_ISOLATED_REVISIT_RESULT='+json.dumps(result,sort_keys=True),flush=True)
    return result


def run(args):
    validate_args(args)
    source=Path(args.campaign).expanduser().resolve()
    out=Path(args.output).expanduser().resolve()
    model,_detector,source_opt,digest,reverse=lasso.initialize(source,args.ladder_output,
                              source/args.hard_state_dir/'latest.pt',args.device)
    config=stage_config(args,digest,reverse)
    out.mkdir(parents=True,exist_ok=True)
    if (out/'config.json').exists():
        if json.loads((out/'config.json').read_text())!=config:raise RuntimeError('ISOLATED_RESUME_CONFIG_DRIFT')
    else:atomic_json(out/'config.json',config)
    model.requires_grad_(True)
    optimizer=torch.optim.AdamW(model.parameters(),lr=args.lr,weight_decay=0.)
    optimizer.load_state_dict(source_opt)
    for group in optimizer.param_groups:group['lr']=args.lr
    history=[]
    latest=out/'latest.pt'
    if latest.is_file():
        saved=torch.load(latest,map_location='cpu',weights_only=True)
        if saved['schema']!=SCHEMA+'.checkpoint' or saved['config']!=config:
            raise RuntimeError('ISOLATED_CHECKPOINT_CONFIG_DRIFT')
        model.load_state_dict(saved['model'])
        optimizer.load_state_dict(saved['optimizer'])
        history=saved['history']
        for group in optimizer.param_groups:group['lr']=args.lr
        # An interrupted commit can leave a previous stage's pending file
        # after latest.pt has already accepted that stage. Verify then retire.
        stale=out/'pending.pt'
        if stale.is_file():
            p=torch.load(stale,map_location='cpu',weights_only=True)
            if p.get('schema')!=SCHEMA+'.pending' or p.get('config')!=config:
                raise RuntimeError('ISOLATED_PENDING_CHECKPOINT_DRIFT')
            if int(p['stage'])==len(history)-1:
                stale.unlink()
    if args.revisit_stage is not None:
        return revisit_stage_candidates(model,out,history,config,args)
    nstage=(args.max_radius-args.start_radius)//args.radius_step+1
    for stage in range(len(history),nstage):
        radius=args.start_radius+stage*args.radius_step
        max_delta=min(args.max_delta,args.initial_delta+stage*args.delta_step)
        train=training_entries(radius,max_delta,args.train_size,args.seed)
        if history and args.rehearsal:
            rng=random.Random(f'iso-rehearsal:{args.seed}:{stage}')
            remembered=[]
            for old in history:
                remembered.extend(training_entries(old['radius'],old['max_delta'],
                                                  args.train_size,args.seed))
            rng.shuffle(remembered)
            seen={tuple(r['card'][:2]) for r in train}
            added=0
            for r in remembered:
                key=tuple(r['card'][:2])
                if key in seen:continue
                train.append(r);seen.add(key);added+=1
                if added>=args.rehearsal:break
        probe=probe_entries(radius,max_delta,args.probe_size,args.seed)
        test=probe_entries(radius,max_delta,args.probe_size,args.seed,'test')
        audit=probe_entries(radius,max_delta,args.audit_size,args.seed,'audit')
        if not probe:raise RuntimeError('ISOLATED_EMPTY_PROBE')
        train_keys={tuple(r['card'][:2]) for r in train}
        probe_keys={tuple(r['card'][:2]) for r in probe}
        test_keys={tuple(r['card'][:2]) for r in test}
        audit_keys={tuple(r['card'][:2]) for r in audit}
        if (train_keys&probe_keys or train_keys&test_keys or probe_keys&test_keys
            or audit_keys&(train_keys|test_keys)):
            raise RuntimeError('ISOLATED_SPLIT_OVERLAP')
        retention=[]
        for old in history:
            old_probe=probe_entries(old['radius'],old['max_delta'],args.probe_size,args.seed)
            retention.extend(old_probe[:args.retention_cases])
        pending=out/'pending.pt'
        prior_steps=0
        rng_state=None
        if pending.is_file():
            interrupted=torch.load(pending,map_location='cpu',weights_only=True)
            if interrupted['schema']!=SCHEMA+'.pending' or interrupted['config']!=config or interrupted['stage']!=stage:
                raise RuntimeError('ISOLATED_PENDING_CHECKPOINT_DRIFT')
            model.load_state_dict(interrupted['model'])
            optimizer.load_state_dict(interrupted['optimizer'])
            for group in optimizer.param_groups:group['lr']=args.lr
            prior_steps=int(interrupted['steps'])
            rng_state=interrupted['rng_state']
        def save_pending(total_steps, state):
            atomic_checkpoint(pending,{'schema':SCHEMA+'.pending','config':config,
                       'stage':stage,'steps':total_steps,'rng_state':state,
                       'model':model.state_dict(),'optimizer':optimizer.state_dict()})
        metrics,held=train_to_gate(model,optimizer,train,probe,args,stage,retention,
                      resume_steps=prior_steps,rng_state=rng_state,checkpoint=save_pending)
        row={'stage':stage,'radius':radius,'max_delta':max_delta,'training':metrics,
             'probe_count':len(held),'status':'accepted' if metrics['accepted'] else 'not_mastered'}
        if not metrics['accepted']:
            atomic_json(out/'result.json',{'schema':SCHEMA,'status':'not_mastered','stage':stage,'last':row,
                                            'accepted_stages':len(history)})
            print('EMP_ISOLATED_MASTERY_LIMIT='+json.dumps(row,sort_keys=True),flush=True)
            return row
        # Stage snapshot before any subsequent training: exact historical replay.
        local=out/f'stage_{stage:03d}'
        local.mkdir(parents=True,exist_ok=True)
        atomic_checkpoint(local/'mastered_snapshot.pt',{'schema':SCHEMA+'.mastery-snapshot',
               'stage':stage,'radius':radius,'config':config,'model':model.state_dict(),
               'optimizer':optimizer.state_dict()})
        write_gzip(local/'probe_predictions.jsonl.gz',held)
        audit_scored=score(model,audit,args.device,args.eval_batch)
        write_gzip(local/'audit_predictions.jsonl.gz',audit_scored)
        row['audit_count']=len(audit_scored)
        row['audit_exact_accuracy']=(sum(r['exact'] for r in audit_scored)/len(audit_scored)
                                     if audit_scored else None)
        test_result=score(model,test,args.device,args.eval_batch)
        write_gzip(local/'untouched_test_predictions.jsonl.gz',test_result)
        row['test_count']=len(test_result)
        row['test_exact_accuracy']=(sum(r['exact'] for r in test_result)/len(test_result) if test_result else None)
        observed=[];discovered=set()
        # Accepted earlier probes are never optimized upon; checking them after
        # training reveals regressions within previously demonstrated competence.
        for old in history:
            old_probe=probe_entries(old['radius'],old['max_delta'],args.audit_size,args.seed,'audit')
            old_archive=out/f"stage_{old['stage']:03d}"/'audit_predictions.jsonl.gz'
            with gzip.open(old_archive,'rt',encoding='utf-8') as fp:
                prior=[json.loads(line) for line in fp]
            candidates,current=pick_candidates(model,old_probe,prior,radius,max_delta,args)
            for c in candidates:
                key=tuple(c['card'][:2])
                if key in discovered:continue
                discovered.add(key)
                observed.append({'source_stage':old['stage'],'previously_correct':
                                 bool(next(p for p in prior if tuple(p['card'][:2])==key)['exact']),
                                 'center':c})
        # Also seek exceptionally rare errors in the current, otherwise mastered
        # probe. Small neighboring error clusters can qualify for reconstruction.
        for c in audit_scored:
            if c['exact'] or c['m']<2 or c['m']>=radius-args.window or abs(c['delta'])+args.window>max_delta:continue
            key=tuple(c['card'][:2])
            if key not in discovered:
                discovered.add(key)
                observed.append({'source_stage':stage,'previously_correct':False,'center':c})
        observed.sort(key=lambda x:(not x['previously_correct'],x['center']['m'],abs(x['center']['delta'])))
        reports=[]
        for index,item in enumerate(observed):
            if len(reports)>=args.max_events:break
            report=neighborhood_diagnostic(model,item['center'],radius,max_delta,args,
                                           local/f'candidate_{index:03d}')
            reports.append({'source_stage':item['source_stage'],
                            'previously_correct':item['previously_correct'],
                            'candidate':item['center']['card'][:2],
                            'status':report['status'],
                            'repaired':report.get('repaired',0),
                            'report':str(local/f'candidate_{index:03d}'/'report.json')})
        row.update(candidates_seen=len(observed),candidate_reports=reports,
                   isolated=sum(r['status']=='isolated' for r in reports),
                   local_clusters=sum(r['status']=='local_cluster' for r in reports),
                   reconstructable=sum(r.get('reconstruction_valid',False) for r in [
                       json.loads(Path(item['report']).read_text(encoding='utf-8')) for item in reports]))
        history.append(row)
        atomic_checkpoint(latest,{'schema':SCHEMA+'.checkpoint','config':config,
                                  'history':history,'model':model.state_dict(),
                                  'optimizer':optimizer.state_dict()})
        atomic_json(out/'history.json',history)
        atomic_json(out/'result.json',{'schema':SCHEMA,'status':'in_progress',
                    'accepted_stages':len(history),'last':row,'output':str(out)})
        if pending.exists():pending.unlink()
        print('EMP_ISOLATED_STAGE='+json.dumps({k:v for k,v in row.items() if k!='candidate_reports'},sort_keys=True),flush=True)
    result={'schema':SCHEMA,'status':'complete','accepted_stages':len(history),
            'candidates':sum(h['candidates_seen'] for h in history),
            'isolated':sum(h['isolated'] for h in history),
            'local_clusters':sum(h.get('local_clusters',0) for h in history),
            'output':str(out),'history':str(out/'history.json')}
    atomic_json(out/'result.json',result)
    print('EMP_ISOLATED_RESULT='+json.dumps(result,sort_keys=True),flush=True)
    return result


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--campaign',required=True)
    p.add_argument('--ladder-output',required=True)
    p.add_argument('--hard-state-dir',default='hard_emp_frontier_v2')
    p.add_argument('--output',required=True)
    p.add_argument('--device',default='cuda' if torch.cuda.is_available() else 'cpu')
    p.add_argument('--seed',type=int,default=20261025)
    p.add_argument('--start-radius',type=int,default=32)
    p.add_argument('--radius-step',type=int,default=16)
    p.add_argument('--max-radius',type=int,default=512)
    p.add_argument('--initial-delta',type=int,default=8)
    p.add_argument('--delta-step',type=int,default=1)
    p.add_argument('--max-delta',type=int,default=32)
    p.add_argument('--train-size',type=int,default=2048)
    p.add_argument('--probe-size',type=int,default=2048)
    p.add_argument('--audit-size',type=int,default=8192)
    p.add_argument('--stage-steps',type=int,default=12000)
    p.add_argument('--loss-target',type=float,default=.001)
    p.add_argument('--mastery-gate',type=float,default=.995)
    p.add_argument('--retention-gate',type=float,default=.995)
    p.add_argument('--retention-cases',type=int,default=256)
    p.add_argument('--rehearsal',type=int,default=512)
    p.add_argument('--check-every',type=int,default=250)
    p.add_argument('--batch',type=int,default=128)
    p.add_argument('--eval-batch',type=int,default=256)
    p.add_argument('--lr',type=float,default=.0001)
    p.add_argument('--window',type=int,default=2)
    p.add_argument('--min-direct-correct',type=int,default=6,
        help='Minimum correct direct neighbors out of eight (8 restores strict isolation)')
    p.add_argument('--revisit-stage',type=int,default=None,
        help='Diagnostic-only replay of archived candidates using the exact saved stage snapshot')
    p.add_argument('--degree',type=int,choices=(1,2),default=2)
    p.add_argument('--ridge',type=float,default=.01)
    p.add_argument('--holdouts',type=int,default=6)
    p.add_argument('--signal-floor',type=float,default=.05)
    p.add_argument('--max-holdout-nrmse',type=float,default=.6)
    p.add_argument('--anomaly-threshold',type=float,default=3.)
    p.add_argument('--interventions',type=int,default=8)
    p.add_argument('--max-events',type=int,default=16)
    p.add_argument('--report-top',type=int,default=32)
    return p


if __name__=='__main__':
    run(parser().parse_args())
