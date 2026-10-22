#!/usr/bin/env python3
"""Read-only diagnosis of the NanoJev Gaussian magnitude-register fork.

Compares saved per-chunk development vectors without loading Qwen, or inspects
actual sign/register outputs, held-out errors, and gradient pressure on CUDA.
Never calls an optimizer, changes the checkpoint, or uses the teacher as a predictor.

Examples (using the NanoJev Python environment):
  python -m tools.nanojev_hawk_dove_gaussian_register_diagnose --history-only
  python -m tools.nanojev_hawk_dove_gaussian_register_diagnose --device cuda
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import csv
import json
import math
import os
from pathlib import Path
import statistics
import tempfile

import torch
import torch.nn.functional as F

from tools import nanojev_hawk_dove_gaussian_register_fork as fork

SCHEMA = 'nanojev.hawk-dove.gaussian-register-diagnosis.v1'
TIERS = tuple(fork.hd.TIERS)
EPS = 1e-12


def numeric(value):
    n = float(value)
    if not math.isfinite(n):
        raise ValueError('REGISTER_DIAG_NONFINITE_NUMERIC')
    return n


def relative_error(predicted, truth):
    return abs(float(predicted) - float(truth)) / max(1., abs(float(truth)))


def mean(values):
    return sum(values) / len(values) if values else None


def quantile(values, fraction):
    if not values:
        return None
    a = sorted(values)
    pos = (len(a) - 1) * fraction
    left = int(pos)
    right = min(left + 1, len(a) - 1)
    return a[left] + (pos - left) * (a[right] - a[left])


def weighted_mean(records, field):
    pairs = [(float(r['weight']), float(r[field])) for r in records if r.get(field) is not None]
    denominator = sum(w for w, _ in pairs)
    return sum(w * v for w, v in pairs) / denominator if denominator else None


def weighted_rate(records, field):
    return weighted_mean(records, field)


def linear_slope(points):
    if len(points) < 2:
        return None
    xbar = mean([x for x, _ in points]); ybar = mean([y for _, y in points])
    denominator = sum((x - xbar)**2 for x, _ in points)
    return sum((x-xbar)*(y-ybar) for x, y in points)/denominator if denominator else None


def gate_report_paths(root):
    return sorted(root.glob('probe-fork-step-*.json'))


def read_history(root, window):
    """Fixed held-out dev scores from saved probes; no checkpoint/Qwen required."""
    entries = []
    for path in gate_report_paths(root):
        obj = json.loads(path.read_text(encoding='utf-8'))
        if 'scores' not in obj or 'fork_step' not in obj:
            continue
        step = int(obj['fork_step'])
        scores = {tier: numeric(obj['scores'][tier]['joint_5pct']) for tier in TIERS}
        entries.append({'step':step, 'gate':numeric(obj['gate_percent']),
                        'first_failing':obj.get('first_failing'), 'scores':scores,
                        'active_tiers':tuple(obj.get('active_tiers',TIERS))})
    unique = {r['step']:r for r in entries}
    entries = [unique[k] for k in sorted(unique)]
    recent = entries[-window:]
    tiers = {}
    for tier in TIERS:
        points = [(e['step'],e['scores'][tier]) for e in recent]
        data = [v for _, v in points]
        tiers[tier] = {'first_5pct':entries[0]['scores'][tier] if entries else None,
                       'latest_5pct':entries[-1]['scores'][tier] if entries else None,
                       'best_5pct':max(e['scores'][tier] for e in entries) if entries else None,
                       'recent_min_5pct':min(data) if data else None,
                       'recent_max_5pct':max(data) if data else None,
                       'recent_slope_accuracy_points_per_1000_steps': (
                           None if (s:=linear_slope(points)) is None else 100*s)}
    focus_counts = Counter(e['first_failing'] or 'none' for e in recent)
    transfers = defaultdict(list)
    # The trainer chooses the next focus from the previous probe's first_failing;
    # if that was None it trains the last active tier. We infer, not assume,
    # this relationship and explicitly mark it in the report.
    for before,after in zip(recent, recent[1:]):
        active=before['active_tiers']
        focus=before['first_failing'] or (active[-1] if active else None)
        if focus is None or after['step']<=before['step']:
            continue
        deltas={tier:after['scores'][tier]-before['scores'][tier] for tier in TIERS}
        others=[delta for tier,delta in deltas.items() if tier!=focus]
        transfers[focus].append({'from_step':before['step'],'to_step':after['step'],
                                 'focused_delta':deltas[focus],
                                 'others_mean_delta':mean(others),
                                 'other_tiers_declining':sum(d < -EPS for d in others),
                                 'deltas':deltas})
    transfer_summary={}
    for focus,rows in sorted(transfers.items()):
        improvements=[r for r in rows if r['focused_delta']>EPS]
        transfer_summary[focus]={
            'chunks':len(rows),
            'focus_mean_gain_percentage_points':100*mean([r['focused_delta'] for r in rows]),
            'other_mean_gain_percentage_points':100*mean([r['others_mean_delta'] for r in rows]),
            'focus_up_other_down_chunks':sum(r['other_tiers_declining']>0 for r in improvements),
            'focus_up_chunks':len(improvements),
            'focus_up_other_down_fraction':sum(r['other_tiers_declining']>0 for r in improvements)/len(improvements) if improvements else None,
            'transitions':rows}
    return {'n_saved_probes':len(entries),'recent_window':len(recent),
            'last_step':entries[-1]['step'] if entries else None,
            'last_gate_percent':entries[-1]['gate'] if entries else None,
            'recent_first_failing_counts':dict(focus_counts),
            'tier_trends':tiers,'by_inferred_training_focus':transfer_summary,
            'gate_advancements':[{'fork_step':entry['step'],'gate_percent':entry['gate']}
               for i,entry in enumerate(entries) if i==0 or entry['gate']>entries[i-1]['gate']]}


def parse_tiers(raw):
    choices=[x.strip().upper() for x in raw.split(',')]
    if not choices or len(choices)!=len(set(choices)) or any(t not in TIERS for t in choices):
        raise ValueError('REGISTER_DIAG_INVALID_TIERS')
    return tuple(t for t in TIERS if t in choices)


def sample_metrics(outputs, truths, patterns, weights, case_ids, sublevels, centers, sigma):
    """Actual register forward outputs only; oracle interventions are diagnostic."""
    logits=outputs['bank_logits'].detach().float().cpu()
    signs=outputs['sign'].detach().float().cpu()
    gs=outputs['log_magnitude'].detach().float().cpu()
    xy=outputs['xy'].detach().float().cpu()
    if logits.ndim!=3 or logits.shape[1]!=2 or signs.shape!=(len(patterns),2,3) or gs.shape!=(len(patterns),2):
        raise ValueError('REGISTER_DIAG_OUTPUT_SHAPE_DRIFT')
    if len(weights)!=len(patterns) or len(truths)!=len(patterns) or len(case_ids)!=len(patterns):
        raise ValueError('REGISTER_DIAG_TASK_SHAPE_DRIFT')
    centers=centers.detach().float().cpu()
    p=torch.softmax(logits,dim=-1)
    out=[]
    for i,pattern in enumerate(patterns):
        if len(pattern)!=3 or pattern[:2]=='11' or weights[i]<=0:
            raise ValueError('REGISTER_DIAG_BAD_PATTERN')
        for slot,axis in enumerate(('x','y')):
            if pattern[slot]=='1':
                continue
            true=numeric(truths[i][slot]);pred=numeric(xy[i,slot]);
            gtrue=math.log10(1+abs(true));gpred=numeric(gs[i,slot])
            dist=(centers-gtrue)/sigma
            q=torch.softmax(-.5*dist.square(),dim=-1)
            probs=p[i,slot]
            kl=float((q*(q.clamp_min(1e-30).log()-probs.clamp_min(1e-30).log())).sum())
            entropy=float(-(probs*probs.clamp_min(1e-30).log()).sum())
            spread=float(((centers-gpred).square()*probs).sum().sqrt())
            predicted_sign=int(signs[i,slot].argmax())-1
            actual_sign=0 if true==0 else (-1 if true<0 else 1)
            sign_correct=predicted_sign==actual_sign
            # Oracle interventions only quantify which output is failing, never
            # participate in predictions, training or grading.
            oracle_sign=actual_sign*math.expm1(gpred*math.log(10))
            oracle_magnitude=predicted_sign*abs(true)
            score=relative_error(pred,true)
            mass=float(probs[(centers-gtrue).abs()<=2*sigma].sum())
            out.append({'case_id':str(case_ids[i]),'sublevel':int(sublevels[i]),
                        'visibility':pattern,'slot':axis,'weight':numeric(weights[i]),
                        'truth':true,'predicted':pred,
                        'relative_error':score,'within_1pct':score<=.01,
                        'within_5pct':score<=.05,'within_10pct':score<=.10,
                        'sign_correct':sign_correct,'true_sign':actual_sign,
                        'predicted_sign':predicted_sign,
                        'true_log_magnitude':gtrue,'predicted_log_magnitude':gpred,
                        'log_magnitude_error':abs(gpred-gtrue),
                        'signed_log_magnitude_bias':gpred-gtrue,
                        'oracle_sign_5pct':relative_error(oracle_sign,true)<=.05,
                        'oracle_magnitude_5pct':relative_error(oracle_magnitude,true)<=.05,
                        'gaussian_kl':max(0.,kl),'register_entropy':entropy,
                        'effective_registers':math.exp(min(50,entropy)),
                        'register_spread_log10':spread,
                        'mass_within_2sigma_of_target':mass,
                        'register_edge_mass':float(probs[:2].sum()+probs[-2:].sum()),
                        'distance_to_decimal_boundary_log10':min(gtrue-math.floor(gtrue), math.ceil(gtrue)-gtrue)})
    return out


def summarize_cases(cases, worst=10):
    if not cases:
        return {'n':0}
    denom=sum(c['weight'] for c in cases)
    return {'n':len(cases),'weighted_n':denom,
            'within_1pct':weighted_rate(cases,'within_1pct'),
            'within_5pct':weighted_rate(cases,'within_5pct'),
            'within_10pct':weighted_rate(cases,'within_10pct'),
            'sign_accuracy':weighted_rate(cases,'sign_correct'),
            'oracle_sign_5pct':weighted_rate(cases,'oracle_sign_5pct'),
            'oracle_magnitude_5pct':weighted_rate(cases,'oracle_magnitude_5pct'),
            'median_relative_error':quantile([c['relative_error'] for c in cases],.5),
            'p90_relative_error':quantile([c['relative_error'] for c in cases],.9),
            'mean_relative_error':weighted_mean(cases,'relative_error'),
            'mean_log_magnitude_error':weighted_mean(cases,'log_magnitude_error'),
            'signed_log_magnitude_bias':weighted_mean(cases,'signed_log_magnitude_bias'),
            'mean_gaussian_kl':weighted_mean(cases,'gaussian_kl'),
            'mean_register_entropy':weighted_mean(cases,'register_entropy'),
            'mean_effective_registers':weighted_mean(cases,'effective_registers'),
            'mean_register_spread_log10':weighted_mean(cases,'register_spread_log10'),
            'mass_within_2sigma_of_target':weighted_mean(cases,'mass_within_2sigma_of_target'),
            'mean_edge_mass':weighted_mean(cases,'register_edge_mass'),
            'near_decimal_boundary_5pct':weighted_rate(
                [c for c in cases if c['distance_to_decimal_boundary_log10']<=.05],'within_5pct'),
            'far_from_decimal_boundary_5pct':weighted_rate(
                [c for c in cases if c['distance_to_decimal_boundary_log10']>.05],'within_5pct'),
            'worst_cases': sorted(cases,key=lambda row:row['relative_error'],reverse=True)[:worst]}


def grad_norm(objective,parameters,retain_graph=True):
    grads=torch.autograd.grad(objective,parameters,allow_unused=True,retain_graph=retain_graph)
    return math.sqrt(sum(float(grad.detach().float().square().sum()) for grad in grads if grad is not None))


def gradient_audit(model, cache, teacher, mixes, seed, tier, n, gaussian_weight):
    """Read-only gradients, on actual fixed dev examples; no .backward/.step()."""
    rows=fork.science.rows_for_probe(teacher,'dev',tier,max(8,n),seed)[:n]
    decoder=model['decoder']
    groups={name:tuple(module.parameters()) for name,module in (
        ('bank',decoder.bank), ('body',decoder.body),
        ('xyop_mask_adapter',decoder.xyop_mask_adapter),
        ('numeric_adapter',decoder.numeric_adapter))}
    measures=defaultdict(lambda:defaultdict(list))
    count=0
    for row in rows:
        mixture=fork.xyop.resolved_mix(mixes,tier,row['mask'])
        for pattern,weight in mixture:
            if pattern[:2]!='01' or weight<=0:
                continue
            channel=fork.xyop.pack(row['x'],row['y'],pattern)
            with torch.enable_grad():
                out=fork.forward_masked(model,[row['prompt']],[channel],cache)
                truth=torch.tensor([[numeric(row['x']),numeric(row['y'])]],dtype=torch.float32,device=model['device'])
                g=torch.log10(1+truth.abs())
                mag=F.smooth_l1_loss(out['log_magnitude'],g,beta=.05,reduction='none')
                q=F.softmax(-.5*((decoder.centers.view(1,1,-1)-g[:,:,None])/decoder.sigma).square(),dim=-1)
                pressure=F.kl_div(F.log_softmax(out['bank_logits'],dim=-1),q,reduction='none').sum(-1)
                terms={'x_magnitude':mag[0,0], 'x_gaussian_weighted':gaussian_weight*pressure[0,0],
                       'y_magnitude_visible':mag[0,1], 'y_gaussian_visible_weighted':gaussian_weight*pressure[0,1]}
                for group,pars in groups.items():
                    for term,loss in terms.items():
                        measures[group][term].append(grad_norm(loss,pars))
            count+=1
    return {'n_hidden_x_visible_y':count,'grad_norm_mean':{
        group:{term:mean(values) for term,values in item.items()} for group,item in measures.items()},
        'note':'Gradient norms for individual held-out examples, no optimizer step. Compare weighted Gaussian with continuous magnitude; visible-Y terms indicate auxiliary gradient allocation.'}


def hint_sensitivity(model, cache, teacher, mixes, seed, tier, n, perturbation):
    if n<=0:
        return {'n':0}
    rows=fork.science.rows_for_probe(teacher,'dev',tier,max(8,n),seed)[:n]
    deltas=[]
    with torch.no_grad():
        for row in rows:
            if not any(pattern[:2]=='01' and weight>0 for pattern,weight in fork.xyop.resolved_mix(mixes,tier,row['mask'])):
                continue
            y=numeric(row['y']);x=numeric(row['x']);prompts=[row['prompt']]*3
            channels=[fork.xyop.pack(x,y*scale,'011') for scale in (1-perturbation,1,1+perturbation)]
            pred=fork.forward_masked(model,prompts,channels,cache)
            lo,base,hi=(numeric(v) for v in pred['log_magnitude'].detach().cpu()[:,0])
            # Delta of predicted magnitude coordinate, rather than decoded sign.
            deltas.append({'case_id':str(row['case_id']), 'true_y':y,
                           'center_log_magnitude':base,'minus_log_magnitude':lo,
                           'plus_log_magnitude':hi,
                           'absolute_log_magnitude_response':abs(hi-lo),
                           'local_slope_per_relative_y':(hi-lo)/(2*perturbation)})
    return {'n':len(deltas),'perturbation_fraction':perturbation,
            'mean_abs_log_magnitude_response':mean([d['absolute_log_magnitude_response'] for d in deltas]),
            'mean_signed_slope':mean([d['local_slope_per_relative_y'] for d in deltas]),
            'cases':deltas}


def atomic_json(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile('w',encoding='utf8',dir=path.parent,
                                     prefix='diag-',suffix='.tmp',delete=False) as f:
        tmp=Path(f.name)
        json.dump(data,f,indent=2,sort_keys=True,allow_nan=False)
        f.write('\n')
    try:os.replace(tmp,path)
    finally:tmp.unlink(missing_ok=True)


def write_cases(path,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    if not rows:return
    with path.open('w',newline='',encoding='utf8') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]),extrasaction='ignore')
        writer.writeheader();writer.writerows(rows)


def live(args, root, identity, checkpoint, history):
    if checkpoint.get('schema')!=fork.SCHEMA or checkpoint.get('identity')!=identity:
        raise ValueError('REGISTER_DIAG_CHECKPOINT_IDENTITY_DRIFT')
    source=Path(identity['source']);teacher=Path(identity['teacher'])
    if fork.fingerprint(source)!=identity['source_sha256'] or fork.fingerprint(teacher)!=identity['teacher_sha256']:
        raise ValueError('REGISTER_DIAG_PROVENANCE_DRIFT')
    mixes=fork.xyop.parse_mixes(identity['mix'])
    selected=parse_tiers(args.tiers)
    model=fork.ex.load_current(source,args.device,args.dtype,args.allow_model_download,int(identity['seed']))
    model['decoder']=fork.build_for_resume(model,checkpoint,int(identity['registers']),float(identity['sigma_steps']))
    model['decoder'].eval()
    ident={'schema':fork.SCHEMA+'.capture-cache','source_sha256':identity['source_sha256'],
           'teacher_sha256':identity['teacher_sha256'],
           'capture_max_tokens':model['config']['max_prompt_tokens']}
    # Isolate diagnostic capture-cache writes from the training process.
    cache=fork.cached.FrozenCaptureCache(root/'diagnostics'/'frozen-capture-cache-v1',ident,args.cache_memory_mb)
    details={};all_cases=[]
    n=args.probe_per_tier if args.probe_per_tier is not None else int(identity['probe_per_tier'])
    for tier in selected:
        rows=fork.science.rows_for_probe(teacher,'dev',tier,n,int(identity['seed']))
        tasks=[]
        for row in rows:
            if not fork.rt.token_eligible(model,row['prompt']):raise ValueError('REGISTER_DIAG_INELIGIBLE:'+tier)
            mixture=fork.xyop.resolved_mix(mixes,tier,row['mask']);denom=sum(w for _,w in mixture)
            for pattern,w in mixture:
                if pattern[:2]!='11':
                    tasks.append((row,pattern,w/denom))
        if not tasks:raise ValueError('REGISTER_DIAG_NO_HIDDEN_TASKS:'+tier)
        cases=[]
        for i in range(0,len(tasks),4):
            batch=tasks[i:i+4]
            channels=[fork.xyop.pack(r['x'],r['y'],pattern) for r,pattern,_ in batch]
            with torch.no_grad():
                outputs=fork.forward_masked(model,[r['prompt'] for r,_,_ in batch],channels,cache)
            cases+=sample_metrics(outputs,
                [(r['x'],r['y']) for r,_,_ in batch],[pattern for _,pattern,_ in batch],
                [w for _,_,w in batch],[r['case_id'] for r,_,_ in batch],
                [r['mask'] for r,_,_ in batch],model['decoder'].centers,model['decoder'].sigma)
        # The native gate assesses BOTH hidden targets jointly, not just X.
        # For multiple visibility patterns recombine by task; most runs are 011.
        totals=defaultdict(lambda:[0.,0.])
        for case in cases:
            key=(case['case_id'],case['visibility'])
            if key not in totals:totals[key]=[case['weight'],1.]
            totals[key][1]*=float(case['within_5pct'])
        joint=sum(w*ok for w,ok in totals.values())/sum(w for w,_ in totals.values())
        existing=(history['last_step']==int(checkpoint['fork_step'])
                  and n==int(identity['probe_per_tier']))
        if existing:
            saved=history['tier_trends'][tier]['latest_5pct']
            if saved is not None and abs(joint-saved)>1e-6:
                raise RuntimeError(f'REGISTER_DIAG_ADVANCEMENT_PARITY:{tier}:{joint}:{saved}')
        hidden={'x':summarize_cases([c for c in cases if c['slot']=='x'],args.worst),
                'y':summarize_cases([c for c in cases if c['slot']=='y'],args.worst)}
        details[tier]={'joint_5pct':joint,'hidden':hidden,
                       'sublevels':{str(m):summarize_cases([c for c in cases if c['sublevel']==m],0)
                                    for m in sorted({c['sublevel'] for c in cases})},
                       'magnitude_decades':{str(k):summarize_cases(
                           [c for c in cases if math.floor(c['true_log_magnitude'])==k],0)
                           for k in sorted({math.floor(c['true_log_magnitude']) for c in cases})}}
        if args.gradient_samples:
            details[tier]['gradient_audit']=gradient_audit(model,cache,teacher,mixes,int(identity['seed']),
                       tier,args.gradient_samples,float(identity['gaussian_weight']))
        if args.hint_samples:
            details[tier]['visible_y_hint_sensitivity']=hint_sensitivity(model,cache,teacher,mixes,
                       int(identity['seed']),tier,args.hint_samples,args.hint_perturbation)
        all_cases.extend([dict(tier=tier,**c) for c in cases])
        fork.emit('register_diagnosis_tier',tier=tier,step=int(checkpoint['fork_step']),
                  score_5pct=joint,sign_x=hidden['x'].get('sign_accuracy'),
                  mean_log_error_x=hidden['x'].get('mean_log_magnitude_error'),
                  gaussian_kl_x=hidden['x'].get('mean_gaussian_kl'))
    return details,all_cases


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,default=fork.DEFAULT_OUTPUT,help='Existing independent fork directory')
    p.add_argument('--history-only',action='store_true',help='CPU-only saved-probe trend/retention analysis; no Qwen or checkpoint load')
    p.add_argument('--history-window',type=int,default=40,help='Recent saved probes used for trend and tier-transfer analysis')
    p.add_argument('--tiers',default=','.join(TIERS))
    p.add_argument('--probe-per-tier',type=int,default=None,help='Use saved fixed dev size by default; parity checks require exact count')
    p.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    p.add_argument('--dtype',choices=('float16','bfloat16','float32'),default='float16')
    p.add_argument('--cache-memory-mb',type=int,default=512)
    p.add_argument('--gradient-samples',type=int,default=4,help='Non-updating dev examples/tier used for gradient balance audit; 0 skips')
    p.add_argument('--hint-samples',type=int,default=8,help='Dev examples/tier used to perturb visible Y; 0 skips')
    p.add_argument('--hint-perturbation',type=float,default=.01)
    p.add_argument('--worst',type=int,default=8,help='Worst cases to retain in JSON per tier')
    p.add_argument('--report',type=Path,default=None,help='JSON output path; defaults to fork/diagnostics/...')
    p.add_argument('--allow-model-download',action='store_true')
    return p


def run(args):
    if args.history_window<2 or args.gradient_samples<0 or args.hint_samples<0 or args.worst<0 or args.cache_memory_mb<1 or not 0<args.hint_perturbation<1:
        raise ValueError('REGISTER_DIAG_BAD_OPTIONS')
    root=args.output.expanduser().resolve();identity=json.loads((root/'config.json').read_text(encoding='utf8'))
    if identity['schema']!=fork.SCHEMA:raise ValueError('REGISTER_DIAG_IDENTITY_DRIFT')
    history=read_history(root,args.history_window)
    if not history['n_saved_probes']:
        raise ValueError('REGISTER_DIAG_NO_SAVED_DEV_HISTORY')
    fork.emit('register_diagnosis_history',fork_step=history['last_step'],gate_percent=history['last_gate_percent'],
              recent_first_failing=history['recent_first_failing_counts'],
              tier_slopes_percentage_points_per_1000_steps={t:history['tier_trends'][t]['recent_slope_accuracy_points_per_1000_steps'] for t in TIERS},
              inferred_focus_transfer={t:{k:v for k,v in item.items() if k!='transitions'} for t,item in history['by_inferred_training_focus'].items()})
    report={'schema':SCHEMA,'generated_utc':datetime.now(timezone.utc).isoformat(),
            'fork_root':str(root),'history':history,'checkpoint_unchanged':True,
            'read_only_training_state':True,
            'limitations':['Historical focus is inferred from the previous saved first_failing; tier interactions are associations, not causal proof.',
                           'Per-step logged training losses are not held-out loss curves.',
                           'Development cases used for gate advancement are not an independent final test split.']}
    if not args.history_only:
        checkpoint_path=root/'latest.pt'
        snapshot=torch.load(checkpoint_path,map_location='cpu',weights_only=True)
        report['fork_step']=int(snapshot['fork_step']);report['gate_percent']=float(snapshot['gate_percent'])
        details,cases=live(args,root,identity,snapshot,history)
        report['tiers']=details
    else:
        report['fork_step']=history['last_step'];report['gate_percent']=history['last_gate_percent'];cases=[]
    path=(args.report or root/'diagnostics'/f'gaussian-register-diagnosis-step-{report["fork_step"]:07d}.json').expanduser().resolve()
    # Keep reports in diagnostics, never write to latest.pt or to config.json.
    if path in (root/'latest.pt',root/'config.json') or path.suffix.lower()!='.json':
        raise ValueError('REGISTER_DIAG_UNSAFE_REPORT_PATH')
    atomic_json(path,report)
    if cases:
        write_cases(path.with_suffix('.cases.csv'),cases)
    fork.emit('register_diagnosis_report',fork_step=report['fork_step'],gate_percent=report['gate_percent'],
              tiers_examined=list(report.get('tiers',{})),history_probes=history['n_saved_probes'],
              checkpoint_unchanged=True,report_path=str(path),
              cases_path=str(path.with_suffix('.cases.csv')) if cases else None)
    return report


def main(argv=None):
    return run(parser().parse_args(argv))


if __name__=='__main__':main()
