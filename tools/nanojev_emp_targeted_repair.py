#!/usr/bin/env python3
"""Constrained, no-training parameter-delta repair from two verified EMP snapshots.

Treat the observed stage-27 -> stage-28 change as repair material. Keep the old
sign/digit readouts and greedily remove unnecessary backbone parameter deltas
while requiring all original failed cards to become correct and all previously
correct local controls to remain correct. Certify on a separate, unchanged
TEST-bucket guard before exporting a standalone checkpoint. No optimizer steps.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

import torch

from tools import nanojev_emp_backbone_readout_crossover as crossover
from tools import nanojev_emp_defect_cross_transfer as cross
from tools import nanojev_emp_hard_lasso as lasso
from tools import nanojev_emp_isolated_defects as iso
from tools.nanojev_zero_line_ladder import atomic_json

SCHEMA = 'nanojev.emp-targeted-repair.v1'


def component(name):
    """Stable, disjoint group assignment; use real AdditionNet state names."""
    if crossover.is_readout(name):
        return 'readout'
    if name.startswith('backbone.layers.'):
        parts = name.split('.')
        if len(parts) >= 4 and parts[2].isdigit():
            return '.'.join(parts[:3])
    if name.startswith('input.'):
        return 'input'
    if name.startswith('regression_head.'):
        return 'regression_head'
    return name.split('.', 1)[0]


def changed_keys(before, after):
    crossover.verify_parts(before, after)
    return tuple(sorted(k for k in before
                        if not crossover.is_readout(k) and
                        not torch.equal(before[k], after[k])))


def mix_by_keys(before, after, selected):
    selected = set(selected)
    if selected - set(changed_keys(before, after)):
        raise RuntimeError('TARGETED_REPAIR_UNKNOWN_OR_READOUT_UPDATE')
    return {k:(after[k] if k in selected else before[k]) for k in before}


def verdict(rows, evaluated):
    if len(rows) != len(evaluated):
        raise RuntimeError('TARGETED_REPAIR_EVALUATION_SIZE_MISMATCH')
    failures = [i for i,r in enumerate(rows) if r['previously_wrong']]
    controls = [i for i,r in enumerate(rows) if not r['previously_wrong']]
    fixed = sum(evaluated[i]['prediction'] == rows[i]['target'] for i in failures)
    damaged = sum(evaluated[i]['prediction'] != rows[i]['target'] for i in controls)
    return {'fixed':fixed,'failures':len(failures),
            'damaged_controls':damaged,'controls':len(controls),
            'local_correct':sum(v['prediction']==r['target'] for v,r in zip(evaluated,rows)),
            'local_total':len(rows),
            'mean_gold_log_score':sum(v['gold_log_score'] for v in evaluated)/len(rows),
            'passes':fixed==len(failures) and damaged==0}


def tensors_by_group(keys):
    groups = {}
    for k in keys:
        groups.setdefault(component(k), []).append(k)
    return {name:tuple(keys) for name,keys in sorted(groups.items())}


def greedy_prune(before,after,rows,model,device,batch,log):
    """Backward deletion. Guaranteed sufficient; only locally irreducible.

    It is NOT a globally minimum-cardinality subset (interactions possible).
    """
    all_keys=changed_keys(before,after)
    selected=set(all_keys)
    seen={}
    def test(which, phase, removed):
        subset=frozenset(which)
        if subset in seen:
            return seen[subset]
        predictions=crossover.evaluate_arm(model,mix_by_keys(before,after,subset),rows,device,batch)
        result=verdict(rows,predictions)
        log.append({'phase':phase,'removed':removed,'remaining_tensors':len(subset),**result})
        seen[subset]=result
        return result
    baseline=test(set(),'baseline','')
    full=test(selected,'all_backbone','')
    if baseline['fixed'] or baseline['damaged_controls'] or not full['passes']:
        raise RuntimeError('TARGETED_REPAIR_CROSSOVER_BASELINE_FAILED')
    # Functional groups, then individual changed tensors. Sorted ordering makes
    # the result deterministic but does not claim a unique minimum.
    removed_groups=[]
    groups=tensors_by_group(all_keys)
    progress=True
    while progress:
        progress=False
        for group,keys in groups.items():
            active=set(keys)&selected
            if not active:continue
            candidate=selected-active
            if test(candidate,'group_delete',group)['passes']:
                selected=candidate;removed_groups.append(group);progress=True
    removed_tensors=[]
    progress=True
    while progress:
        progress=False
        for key in sorted(tuple(selected)):
            candidate=selected-{key}
            if test(candidate,'tensor_delete',key)['passes']:
                selected=candidate;removed_tensors.append(key);progress=True
    final=test(selected,'selected','')
    if not final['passes']:
        raise RuntimeError('TARGETED_REPAIR_LOCAL_GATE_FAILED')
    return {'selected':tuple(sorted(selected)), 'groups_removed':removed_groups,
            'tensors_removed':removed_tensors,'baseline':baseline,'all_backbone':full,
            'final':final,'evaluations':len(seen)}


def guards(run_config, radii, per_radius, before_stage, evidence_rows):
    """Frozen TEST bucket, using the *saved* isolated-run sampling parameters.

    Real isolated runs store these under config['parameters']; older or test
    fixtures may supply the flat mapping. Never invent a seed, since changing
    it changes the train/probe/TEST partition and invalidates the guard.
    """
    parameters = run_config.get('parameters', run_config)
    required = ('seed', 'start_radius', 'radius_step',
                'initial_delta', 'delta_step', 'max_delta')
    if not isinstance(parameters, dict):
        raise RuntimeError('TARGETED_REPAIR_INVALID_RUN_PARAMETERS')
    missing = [key for key in required if key not in parameters]
    if missing:
        raise RuntimeError('TARGETED_REPAIR_MISSING_RUN_PARAMETERS:' + ','.join(missing))
    seed=int(parameters['seed'])
    start=int(parameters['start_radius'])
    step=int(parameters['radius_step'])
    old_pairs={(r['a'],r['b']) for r in evidence_rows}
    seen=set(old_pairs)
    entries=[]
    for radius in radii:
        if radius < start or radius > start+before_stage*step or (radius-start)%step:
            raise ValueError('TARGETED_REPAIR_INVALID_GUARD_RADIUS')
        delta=min(int(parameters['max_delta']),int(parameters['initial_delta'])+
                  (radius-start)//step*int(parameters['delta_step']))
        for e in iso.probe_entries(radius,delta,per_radius,seed,'test'):
            a,b=e['card'][:2]
            if (a,b) in seen:continue
            seen.add((a,b))
            entries.append({'a':a,'b':b,'target':e['card'][2],'radius':radius})
    if len(entries)<min(64,per_radius):
        raise RuntimeError('TARGETED_REPAIR_GUARD_TOO_SMALL')
    return entries


def score_guard(model,before,repair,guard,device,batch):
    old=crossover.evaluate_arm(model,before,guard,device,batch)
    new=crossover.evaluate_arm(model,repair,guard,device,batch)
    rows=[]
    for x,o,n in zip(guard,old,new):
        rows.append({**x,'old_prediction':o['prediction'],
                     'new_prediction':n['prediction'],
                     'old_correct':o['correct'],'new_correct':n['correct'],
                     'new_regression':o['correct'] and not n['correct'],
                     'new_repair':not o['correct'] and n['correct']})
    return {'cards':len(rows),'previously_correct':sum(x['old_correct'] for x in rows),
            'previously_wrong':sum(not x['old_correct'] for x in rows),
            'new_regressions':sum(x['new_regression'] for x in rows),
            'new_repairs':sum(x['new_repair'] for x in rows),
            'new_correct':sum(x['new_correct'] for x in rows)}, rows


def atomic_csv(path, rows):
    if not rows:raise RuntimeError('TARGETED_REPAIR_EMPTY_TABLE')
    p=Path(path);tmp=p.with_suffix('.csv.tmp')
    with tmp.open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    os.replace(tmp,p)


def atomic_checkpoint(path, payload):
    path=Path(path);temp=path.with_suffix('.pt.tmp')
    torch.save(payload,temp);os.replace(temp,path)


def run(args):
    if args.before_stage < 0 or args.after_stage <= args.before_stage or args.batch<1 or args.guard_per_radius<1:
        raise ValueError('TARGETED_REPAIR_INVALID_ARGUMENTS')
    run=Path(args.run).resolve();source=Path(args.cross_transfer).resolve();out=Path(args.output).resolve()
    campaign=Path(args.campaign).resolve()
    if out in (run,source,campaign) or out==run.parent:
        raise ValueError('TARGETED_REPAIR_OUTPUT_MUST_BE_SEPARATE')
    run_cfg=json.loads((run/'config.json').read_text('utf-8'))
    p0=run/f'stage_{args.before_stage:03d}'/'mastered_snapshot.pt'
    p1=run/f'stage_{args.after_stage:03d}'/'mastered_snapshot.pt'
    crossover.check_evidence_provenance(source,run,args.before_stage,args.after_stage,p0,p1)
    before=cross.snapshot(p0,run_cfg,args.before_stage)
    after=cross.snapshot(p1,run_cfg,args.after_stage)
    evidence=crossover.read_evidence(source)
    groups=crossover.verify_parts(before,after)
    radii=tuple(int(x.strip()) for x in args.guard_radii.split(',') if x.strip())
    if not radii or len(set(radii))!=len(radii):
        raise ValueError('TARGETED_REPAIR_EMPTY_OR_DUPLICATED_GUARD_RADII')
    cfg={'schema':SCHEMA,'run':str(run),'cross_transfer':str(source),'campaign':str(campaign),
         'before_stage':args.before_stage,'after_stage':args.after_stage,
         'before_sha256':cross.sha256(p0),'after_sha256':cross.sha256(p1),
         'evidence_sha256':cross.sha256(source/'natural_repair.csv'),
         'device':args.device,'batch':args.batch,'guard_radii':radii,
         'guard_per_radius':args.guard_per_radius,
         'max_guard_regressions':args.max_guard_regressions}
    out.mkdir(parents=True,exist_ok=True)
    if (out/'config.json').exists() and json.loads((out/'config.json').read_text('utf-8'))!=json.loads(json.dumps(cfg)):
        raise RuntimeError('TARGETED_REPAIR_OUTPUT_CONFIG_DRIFT')
    atomic_json(out/'config.json',cfg)
    model,_,_,_,_=lasso.initialize(campaign,args.ladder_output,
                       campaign/args.hard_state_dir/'latest.pt',args.device)
    if set(model.state_dict())!=set(before):
        raise RuntimeError('TARGETED_REPAIR_MODEL_KEYS_DIFFER')
    model.requires_grad_(False).eval()
    # Existing evidence must reproduce without drift, including the B1R0
    # full-backbone swap that previously repaired all four errors.
    old=crossover.evaluate_arm(model,before,evidence,args.device,args.batch)
    full_state=crossover.mix_state(before,after,True,False)
    full=crossover.evaluate_arm(model,full_state,evidence,args.device,args.batch)
    if any(v['prediction']!=r['before'] for v,r in zip(old,evidence)):
        raise RuntimeError('TARGETED_REPAIR_OLD_CHECKPOINT_DRIFT')
    if verdict(evidence,full)['passes'] is False:
        raise RuntimeError('TARGETED_REPAIR_FULL_BACKBONE_NOT_SUFFICIENT')
    trial=[]
    search=greedy_prune(before,after,evidence,model,args.device,args.batch,trial)
    selected=set(search['selected'])
    candidate=mix_by_keys(before,after,selected)
    checks=crossover.evaluate_arm(model,candidate,evidence,args.device,args.batch)
    local=verdict(evidence,checks)
    if not local['passes']:raise RuntimeError('TARGETED_REPAIR_INTERNAL_LOCAL_DRIFT')
    guard_cards=guards(run_cfg,radii,args.guard_per_radius,args.before_stage,evidence)
    safety, guard_rows=score_guard(model,before,candidate,guard_cards,args.device,args.batch)
    certified=safety['new_regressions']<=args.max_guard_regressions
    tensors=[]
    for name in sorted(selected):
        delta=after[name].detach().double()-before[name].detach().double()
        tensors.append({'name':name,'component':component(name),
                        'elements':delta.numel(),'changed_elements':int(torch.count_nonzero(delta)),
                        'delta_l2':float(torch.linalg.vector_norm(delta))})
    atomic_json(out/'search.json',{'schema':SCHEMA,'status':'complete',**search})
    with (out/'search_trials.jsonl').open('w',encoding='utf-8') as f:
        for row in trial:f.write(json.dumps(row,sort_keys=True)+'\n')
    atomic_csv(out/'selected_tensors.csv',tensors or [
        {'name':'','component':'','elements':0,'changed_elements':0,'delta_l2':0.}])
    atomic_csv(out/'guard_cases.csv',guard_rows)
    local_rows=[{**row,'new_prediction':prediction['prediction'],
                 'new_correct':prediction['correct']} for row,prediction in zip(evidence,checks)]
    atomic_csv(out/'local_cases.csv',local_rows)
    repaired_path=out/'targeted_repair.pt'
    result={'schema':SCHEMA,'status':'certified' if certified else 'guard_rejected',
            'repair_exported':certified,'source_sha256':[cfg['before_sha256'],cfg['after_sha256']],
            'local':local,'safety_guard':safety,'selected_tensors':list(search['selected']),
            'selected_tensor_count':len(selected),
            'available_changed_backbone_tensors':len(changed_keys(before,after)),
            'selected_elements':sum(t['changed_elements'] for t in tensors),
            'removed_components':search['groups_removed'],'removed_tensors':search['tensors_removed'],
            'evaluated_subsets':search['evaluations'],'output':str(out),
            'limitations':['Repair material comes from a LATER trained checkpoint; this does not yet synthesize edits for an unseen defect.',
                           'Backward elimination yields a locally irreducible tensor subset, not a globally minimal or unique mechanism.',
                           'Certification checks a finite, previously held-out TEST-bucket sample; it is not universal arithmetic correctness.',
                           'Selected channels/tensors may share dependencies and cannot be declared unique causal neurons.']}
    if certified:
        payload={'schema':SCHEMA+'.checkpoint','source_stage':args.before_stage,
                 'donor_stage':args.after_stage,'config':run_cfg,
                 'provenance':cfg,'selected_tensors':search['selected'],
                 'local_gate':local,'guard_gate':safety,'model':candidate}
        atomic_checkpoint(repaired_path,payload)
        reloaded=torch.load(repaired_path,map_location='cpu',weights_only=True)
        if reloaded['schema']!=SCHEMA+'.checkpoint' or tuple(reloaded['selected_tensors'])!=search['selected']:
            raise RuntimeError('TARGETED_REPAIR_CHECKPOINT_ROUNDTRIP_FAILED')
        recheck=crossover.evaluate_arm(model,reloaded['model'],evidence,args.device,args.batch)
        if verdict(evidence,recheck)!=local:
            repaired_path.unlink(missing_ok=True)
            raise RuntimeError('TARGETED_REPAIR_CHECKPOINT_INFERENCE_ROUNDTRIP_FAILED')
        result['repair_checkpoint']=str(repaired_path)
        result['repair_checkpoint_sha256']=cross.sha256(repaired_path)
    else:
        # Never leave a stale artifact appearing certified after a failed rerun.
        repaired_path.unlink(missing_ok=True)
    atomic_json(out/'result.json',result)
    print('EMP_TARGETED_REPAIR_RESULT='+json.dumps(result,sort_keys=True),flush=True)
    return result


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',required=True)
    p.add_argument('--cross-transfer',required=True)
    p.add_argument('--campaign',required=True)
    p.add_argument('--ladder-output',required=True)
    p.add_argument('--hard-state-dir',default='hard_emp_frontier_v2')
    p.add_argument('--output',required=True)
    p.add_argument('--device',default='cuda' if torch.cuda.is_available() else 'cpu')
    p.add_argument('--before-stage',type=int,default=27)
    p.add_argument('--after-stage',type=int,default=28)
    p.add_argument('--batch',type=int,default=128)
    p.add_argument('--guard-radii',default='128,256,384,464')
    p.add_argument('--guard-per-radius',type=int,default=1024)
    p.add_argument('--max-guard-regressions',type=int,default=0)
    return p


if __name__=='__main__':
    run(parser().parse_args())
