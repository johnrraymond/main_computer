#!/usr/bin/env python3
"""Enumerate all subsets of certified NanoJev input-tensor deltas; narrow rows/dimensions.

No training. This is a donor-based repair subset experiment, not donor-free repair.
Old readout and all unselected parameters remain untouched. A separate post-
selection arithmetic audit is held out from the combinatorial/refinement search.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import torch

from tools import nanojev_emp_targeted_repair as targeted
from tools import nanojev_emp_backbone_readout_crossover as crossover
from tools import nanojev_emp_defect_cross_transfer as cross
from tools import nanojev_emp_hard_lasso as lasso
from tools.nanojev_zero_line_ladder import atomic_json

SCHEMA = 'nanojev.emp-exhaustive-input-repair.v1'


def combinations(keys):
    """Every subset exactly once, including unchanged baseline, stable order."""
    keys = tuple(sorted(keys))
    if not 1 <= len(keys) <= 10 or len(keys) != len(set(keys)):
        raise ValueError('EXHAUSTIVE_EXPECTS_UNIQUE_1_TO_10_TENSORS')
    return [(mask, tuple(keys[i] for i in range(len(keys)) if mask & (1 << i)))
            for mask in range(1 << len(keys))]


def validate_donor(before, after, payload, selected, result):
    if payload.get('schema') != targeted.SCHEMA + '.checkpoint':
        raise RuntimeError('EXHAUSTIVE_BAD_DONOR_CHECKPOINT_SCHEMA')
    if (result.get('status') != 'certified' or not result.get('repair_exported') or
            tuple(result.get('selected_tensors', [])) != tuple(selected) or
            tuple(payload.get('selected_tensors', ())) != tuple(selected)):
        raise RuntimeError('EXHAUSTIVE_DONOR_SELECTION_DRIFT')
    eligible = set(targeted.changed_keys(before, after))
    if not set(selected).issubset(eligible) or any(not k.startswith('input.') for k in selected):
        raise RuntimeError('EXHAUSTIVE_DONOR_NOT_INPUT_ONLY')
    exact = targeted.mix_by_keys(before, after, selected)
    actual = payload.get('model')
    if not isinstance(actual, dict) or set(exact) != set(actual):
        raise RuntimeError('EXHAUSTIVE_DONOR_MODEL_KEYS_DRIFT')
    if any(not torch.equal(t, actual[k]) for k, t in exact.items()):
        raise RuntimeError('EXHAUSTIVE_DONOR_PARAMETER_DRIFT')
    return exact


def changed_element_count(before, after, selected):
    return sum(int(torch.count_nonzero(before[k] != after[k])) for k in selected)


def local_eval(model, states, evidence, device, batch):
    result = crossover.evaluate_arm(model, states, evidence, device, batch)
    return targeted.verdict(evidence, result)


def guard_eval(model, before, states, guards, device, batch):
    return targeted.score_guard(model, before, states, guards, device, batch)[0]


def enumerate_verified(model, before, after, selected, evidence, guard,
                       device, batch, max_regressions=0, emit=None):
    """Every subset gets local evaluation. Every locally sufficient subset gets guard."""
    records=[]
    for mask, subset in combinations(selected):
        states=targeted.mix_by_keys(before, after, subset)
        loc=local_eval(model, states, evidence, device, batch)
        saf=None
        if loc['passes']:
            saf=guard_eval(model, before, states, guard, device, batch)
        safe=bool(saf is not None and saf['new_regressions'] <= max_regressions)
        record={'mask':mask,'binary_mask':format(mask,f'0{len(selected)}b'),
                'tensors':list(subset),'tensor_count':len(subset),
                'updated_elements':changed_element_count(before, after, subset),
                'local_fixed':loc['fixed'],'local_failures':loc['failures'],
                'local_control_damage':loc['damaged_controls'],
                'local_correct':loc['local_correct'],
                'local_gold_log_score':loc['mean_gold_log_score'],
                'local_pass':loc['passes'],'guard_checked':saf is not None,
                'guard_cards':saf['cards'] if saf else None,
                'guard_regressions':saf['new_regressions'] if saf else None,
                'guard_repairs':saf['new_repairs'] if saf else None,
                'screen_pass':safe}
        records.append(record)
        if emit is not None: emit(record)
    winners=[r for r in records if r['screen_pass']]
    winners.sort(key=lambda r:(r['tensor_count'],r['updated_elements'],
                               -r['local_gold_log_score'],r['binary_mask']))
    return records, winners


def as_matrix(rows, path):
    targeted.atomic_csv(path,rows)


def verify_screening_provenance(target_dir, targeted_result, guard):
    """Require the exact same input-card guard the donor previously certified."""
    prior=targeted_result.get('safety_guard',{})
    if (prior.get('new_regressions')!=0 or prior.get('cards')!=len(guard)):
        raise RuntimeError('EXHAUSTIVE_ORIGINAL_GUARD_SUMMARY_DRIFT')
    archive=Path(target_dir)/'guard_cases.csv'
    if not archive.exists():
        raise RuntimeError('EXHAUSTIVE_MISSING_ORIGINAL_GUARD_CARDS')
    with archive.open(newline='',encoding='utf-8-sig') as f:
        reader=csv.DictReader(f)
        if not reader.fieldnames or not {'a','b','target','old_correct','new_correct'}.issubset(reader.fieldnames):
            raise RuntimeError('EXHAUSTIVE_ORIGINAL_GUARD_COLUMNS_DRIFT')
        previous=[(int(r['a']),int(r['b']),int(r['target'])) for r in reader]
    current=[(int(r['a']),int(r['b']),int(r['target'])) for r in guard]
    if len(current)!=len(previous) or sorted(current)!=sorted(previous):
        raise RuntimeError('EXHAUSTIVE_ORIGINAL_GUARD_CARDS_DRIFT')


def row_groups(before, after, keys):
    """Nonoverlapping groups: one embedding row, or an entire vector tensor."""
    groups=[]
    for key in sorted(keys):
        tensor=before[key]
        if tensor.ndim==2:
            groups.extend((key, i) for i in range(tensor.shape[0]))
        elif tensor.ndim==1:
            groups.append((key,None))
        else:
            raise RuntimeError('EXHAUSTIVE_UNSUPPORTED_INPUT_TENSOR_DIMENSION:' + key)
    return groups


def active_delta_count(before, state, keys):
    return sum(int(torch.count_nonzero(before[k] != state[k])) for k in keys)


def maybe_delete(old, trial, evidence, guard, model, device, batch, max_regressions):
    loc=local_eval(model, trial, evidence, device, batch)
    saf=(guard_eval(model, old, trial, guard, device, batch)
         if loc['passes'] else None)
    accept=bool(saf is not None and saf['new_regressions'] <= max_regressions)
    return accept,loc,saf


def refine_rows(model, before, state, keys, evidence, guard, device, batch,
                max_regressions=0, emit=None):
    """Greedy row deletion using BOTH local and screening-guard gates."""
    current={k:v.clone() for k,v in state.items()}
    records=[]
    for name,row in row_groups(before,state,keys):
        trial={k:v.clone() for k,v in current.items()}
        if row is None:
            if torch.equal(trial[name],before[name]):continue
            trial[name]=before[name].clone()
        else:
            if torch.equal(trial[name][row],before[name][row]):continue
            trial[name][row]=before[name][row]
        accept,loc,saf=maybe_delete(before,trial,evidence,guard,model,device,batch,max_regressions)
        entry={'phase':'row','name':name,'row':row,'dimension':None,
               'accepted':accept,'local_fixed':loc['fixed'],
               'local_control_damage':loc['damaged_controls'],
               'guard_regressions':None if saf is None else saf['new_regressions'],
               'remaining_elements':active_delta_count(before,trial if accept else current,keys)}
        records.append(entry)
        if emit is not None:emit(entry)
        if accept: current=trial
    return current, records


def refine_dimensions(model, before, state, keys, evidence, guard, device, batch,
                      max_regressions=0, emit=None):
    """Try removing a common width dimension across remaining input updates."""
    current={k:v.clone() for k,v in state.items()}
    widths={v.shape[-1] for k,v in current.items() if k in keys}
    if len(widths)!=1:raise RuntimeError('EXHAUSTIVE_INCOMPATIBLE_REPAIR_WIDTHS')
    records=[]
    for dim in range(next(iter(widths))):
        trial={k:v.clone() for k,v in current.items()}
        active=False
        for k in keys:
            if not torch.equal(trial[k][...,dim],before[k][...,dim]):
                active=True
                trial[k][...,dim]=before[k][...,dim]
        if not active:continue
        accept,loc,saf=maybe_delete(before,trial,evidence,guard,model,device,batch,max_regressions)
        entry={'phase':'dimension','name':'all_selected_input_tensors','row':None,
               'dimension':dim,'accepted':accept,'local_fixed':loc['fixed'],
               'local_control_damage':loc['damaged_controls'],
               'guard_regressions':None if saf is None else saf['new_regressions'],
               'remaining_elements':active_delta_count(before,trial if accept else current,keys)}
        records.append(entry)
        if emit is not None:emit(entry)
        if accept:current=trial
    return current,records


def delta_manifest(before, after, candidate, selected):
    """Explicit row/dimension mask; every changed coordinate matches donor delta."""
    result=[]
    for k in selected:
        original=before[k];donor=after[k];actual=candidate[k]
        use=(actual != original)
        if not bool(torch.all((actual==original) | (actual==donor))):
            raise RuntimeError('EXHAUSTIVE_UNAUTHORIZED_PARAMETER_VALUE:' + k)
        changed=int(torch.count_nonzero(use))
        rows=[]
        if actual.ndim==2:
            for i in range(actual.shape[0]):
                cols=torch.nonzero(use[i],as_tuple=False).flatten().tolist()
                if cols:rows.append({'row':i,'dimensions':cols})
        else:
            cols=torch.nonzero(use,as_tuple=False).flatten().tolist()
            if cols:rows.append({'row':None,'dimensions':cols})
        result.append({'tensor':k,'shape':list(original.shape),'changed_elements':changed,
                       'donor_changed_elements':int(torch.count_nonzero(original!=donor)),
                       'active_rows':rows})
    return result


def parse_radii(text):
    radii=tuple(int(x.strip()) for x in text.split(',') if x.strip())
    if not radii or len(set(radii))!=len(radii):
        raise ValueError('EXHAUSTIVE_BAD_HOLDOUT_RADII')
    return radii


def run(args):
    target=Path(args.targeted).resolve();out=Path(args.output).resolve()
    old_dir=Path(args.campaign).resolve()
    if out in {target,old_dir} or out==target.parent:
        raise ValueError('EXHAUSTIVE_OUTPUT_MUST_BE_SEPARATE')
    targeted_config=json.loads((target/'config.json').read_text('utf-8'))
    targeted_result=json.loads((target/'result.json').read_text('utf-8'))
    artifact=target/'targeted_repair.pt'
    if targeted_result.get('repair_checkpoint_sha256') != cross.sha256(artifact):
        raise RuntimeError('EXHAUSTIVE_CERTIFIED_DONOR_HASH_MISMATCH')
    if old_dir != Path(targeted_config['campaign']).resolve():
        raise RuntimeError('EXHAUSTIVE_CAMPAIGN_MISMATCH')
    run_path=Path(targeted_config['run']).resolve()
    source=Path(targeted_config['cross_transfer']).resolve()
    before_stage=int(targeted_config['before_stage']);after_stage=int(targeted_config['after_stage'])
    cfg=json.loads((run_path/'config.json').read_text('utf-8'))
    p0=run_path/f'stage_{before_stage:03d}'/'mastered_snapshot.pt'
    p1=run_path/f'stage_{after_stage:03d}'/'mastered_snapshot.pt'
    crossover.check_evidence_provenance(source,run_path,before_stage,after_stage,p0,p1)
    if (cross.sha256(p0)!=targeted_config['before_sha256'] or
            cross.sha256(p1)!=targeted_config['after_sha256'] or
            cross.sha256(source/'natural_repair.csv')!=targeted_config['evidence_sha256']):
        raise RuntimeError('EXHAUSTIVE_CHECKPOINT_OR_EVIDENCE_DRIFT')
    before=cross.snapshot(p0,cfg,before_stage)
    after=cross.snapshot(p1,cfg,after_stage)
    checkpoint=torch.load(artifact,map_location='cpu',weights_only=True)
    selected=tuple(sorted(targeted_result['selected_tensors']))
    validate_donor(before,after,checkpoint,selected,targeted_result)
    if len(selected)!=5:
        raise RuntimeError('EXHAUSTIVE_EXPECTED_FIVE_INPUT_TENSORS')
    if args.guard_per_radius is not None and args.guard_per_radius != targeted_config['guard_per_radius']:
        raise ValueError('EXHAUSTIVE_GUARD_SAMPLE_MUST_MATCH_DONOR')
    guard_radii=tuple(int(i) for i in targeted_config['guard_radii'])
    holdout_radii=parse_radii(args.holdout_radii)
    if set(holdout_radii)&set(guard_radii):
        raise ValueError('EXHAUSTIVE_HOLDOUT_RADIUS_OVERLAPS_SCREENING')
    evidence=crossover.read_evidence(source)
    screening=targeted.guards(cfg,guard_radii,int(targeted_config['guard_per_radius']),before_stage,evidence)
    verify_screening_provenance(target,targeted_result,screening)
    # The final audit is not consulted during selection. Re-exclude all screening cards.
    excluded=evidence+[{'a':r['a'],'b':r['b']} for r in screening]
    final_guard=targeted.guards(cfg,holdout_radii,args.holdout_per_radius,before_stage,excluded)
    config={'schema':SCHEMA,'targeted':str(target),'targeted_sha256':cross.sha256(artifact),
            'run':str(run_path),'cross_transfer':str(source),'campaign':str(old_dir),
            'before_sha256':cross.sha256(p0),'after_sha256':cross.sha256(p1),
            'tensor_keys':list(selected),'screening_radii':list(guard_radii),
            'screening_cases':len(screening),'final_holdout_radii':list(holdout_radii),
            'final_holdout_cases':len(final_guard),'device':args.device,'batch':args.batch,
            'max_guard_regressions':args.max_guard_regressions,
            'refine_rows':args.refine_rows,'refine_dimensions':args.refine_dimensions,
            'holdout_per_radius':args.holdout_per_radius}
    out.mkdir(parents=True,exist_ok=True)
    prior=out/'config.json'
    if prior.exists() and json.loads(prior.read_text('utf-8'))!=config:
        raise RuntimeError('EXHAUSTIVE_OUTPUT_CONFIG_DRIFT')
    atomic_json(prior,config)
    model,_,_,_,_=lasso.initialize(old_dir,args.ladder_output,
                                  old_dir/args.hard_state_dir/'latest.pt',args.device)
    if set(model.state_dict())!=set(before):
        raise RuntimeError('EXHAUSTIVE_MODEL_KEYS_DRIFT')
    model.requires_grad_(False).eval()
    old=local_eval(model,before,evidence,args.device,args.batch)
    if old['fixed']!=0 or old['damaged_controls']!=0:
        raise RuntimeError('EXHAUSTIVE_BASELINE_DRIFT')
    baseline=targeted.mix_by_keys(before,after,selected)
    donor=local_eval(model,baseline,evidence,args.device,args.batch)
    if not donor['passes']:
        raise RuntimeError('EXHAUSTIVE_DONOR_LOCAL_REPAIR_DRIFT')
    screening_baseline=guard_eval(model,before,baseline,screening,args.device,args.batch)
    if screening_baseline['new_regressions']!=0:
        raise RuntimeError('EXHAUSTIVE_DONOR_SCREENING_DRIFT')
    def emit_combo(entry):
        print('EMP_REPAIR_COMBINATION='+json.dumps({k:entry[k] for k in
              ('mask','tensor_count','local_fixed','local_control_damage','guard_regressions','screen_pass')},
              sort_keys=True),flush=True)
    rows,winners=enumerate_verified(model,before,after,selected,evidence,screening,
                                   args.device,args.batch,args.max_guard_regressions,emit_combo)
    as_matrix(rows,out/'combinations.csv')
    atomic_json(out/'combinations.json',{'schema':SCHEMA,'combinations':rows,'qualified':len(winners)})
    if not winners:
        result={'schema':SCHEMA,'status':'no_qualified_subset','combinations':len(rows),
                'qualified':0,'repair_exported':False,'output':str(out)}
        (out/'exhaustive_repair.pt').unlink(missing_ok=True)
        atomic_json(out/'result.json',result)
        print('EMP_EXHAUSTIVE_REPAIR_RESULT='+json.dumps(result,sort_keys=True),flush=True)
        return result
    best=winners[0]
    state=targeted.mix_by_keys(before,after,best['tensors'])
    refinement=[]
    def emit_refine(row):
        print('EMP_REPAIR_REFINEMENT='+json.dumps({k:row[k] for k in
              ('phase','name','row','dimension','accepted','remaining_elements')},sort_keys=True),flush=True)
    if args.refine_rows:
        state,history=refine_rows(model,before,state,best['tensors'],evidence,screening,
                                  args.device,args.batch,args.max_guard_regressions,emit_refine)
        refinement.extend(history)
    if args.refine_dimensions:
        state,history=refine_dimensions(model,before,state,best['tensors'],evidence,screening,
                                        args.device,args.batch,args.max_guard_regressions,emit_refine)
        refinement.extend(history)
    if refinement:as_matrix(refinement,out/'refinement.csv')
    manifest=delta_manifest(before,after,state,best['tensors'])
    atomic_json(out/'active_delta.json',{'schema':SCHEMA,'tensors':manifest})
    local=local_eval(model,state,evidence,args.device,args.batch)
    local_rows=crossover.evaluate_arm(model,state,evidence,args.device,args.batch)
    as_matrix([{**r,'repaired_prediction':n['prediction'],'repaired_correct':n['correct']}
               for r,n in zip(evidence,local_rows)],out/'local_cases.csv')
    screening_result=guard_eval(model,before,state,screening,args.device,args.batch)
    if not local['passes'] or screening_result['new_regressions']>args.max_guard_regressions:
        raise RuntimeError('EXHAUSTIVE_FINAL_SCREENING_DRIFT')
    # Final holdout is evaluated precisely ONCE, after selection/refinement.
    final,final_rows=targeted.score_guard(model,before,state,final_guard,args.device,args.batch)
    as_matrix(final_rows,out/'final_holdout_cases.csv')
    certified=final['new_regressions']<=args.max_guard_regressions
    remaining=active_delta_count(before,state,best['tensors'])
    result={'schema':SCHEMA,'status':'certified' if certified else 'final_holdout_rejected',
            'combinations':len(rows),'qualified_tensor_subsets':len(winners),
            'smallest_screened_tensor_subset':best['tensors'],
            'smallest_screened_tensor_count':best['tensor_count'],
            'donor_tensors':list(selected),'donor_updated_elements':changed_element_count(before,after,selected),
            'updated_elements':remaining,'refinement_attempts':len(refinement),
            'refinement_accepted':sum(i['accepted'] for i in refinement),
            'local':local,'screening_guard':screening_result,'final_holdout':final,
            'repair_exported':certified,'output':str(out),
            'limitations':['The 32-way search is exhaustive only over the five previously retained tensor deltas.',
                           'Row/dimension pruning is greedy and not a globally minimal element-level repair.',
                           'All candidate edits come from a later trained checkpoint; this is not donor-free repair.',
                           'Finite TEST-fold screening and post-selection holdout are not universal safety guarantees.',
                           'Parameter importance is conditional on this checkpoint and these arithmetic cases.']}
    artifact_path=out/'exhaustive_repair.pt'
    if certified:
        payload={'schema':SCHEMA+'.checkpoint','source_checkpoint_sha256':cross.sha256(p0),
                 'donor_checkpoint_sha256':cross.sha256(p1),'targeted_checkpoint_sha256':cross.sha256(artifact),
                 'selected_tensors':list(best['tensors']),'updated_elements':remaining,
                 'model':state,'run_config':cfg,'local':local,
                 'screening_guard':screening_result,'final_holdout':final}
        targeted.atomic_checkpoint(artifact_path,payload)
        roundtrip=torch.load(artifact_path,map_location='cpu',weights_only=True)
        if (roundtrip['schema'] != SCHEMA+'.checkpoint' or
                not local_eval(model,roundtrip['model'],evidence,args.device,args.batch)['passes']):
            artifact_path.unlink(missing_ok=True)
            raise RuntimeError('EXHAUSTIVE_CHECKPOINT_ROUNDTRIP_FAILED')
        result['repair_checkpoint']=str(artifact_path)
        result['repair_checkpoint_sha256']=cross.sha256(artifact_path)
    else: artifact_path.unlink(missing_ok=True)
    atomic_json(out/'result.json',result)
    print('EMP_EXHAUSTIVE_REPAIR_RESULT='+json.dumps(result,sort_keys=True),flush=True)
    return result


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--targeted',required=True,help='Existing certified targeted-repair output directory')
    p.add_argument('--campaign',required=True)
    p.add_argument('--ladder-output',required=True)
    p.add_argument('--hard-state-dir',default='hard_emp_frontier_v2')
    p.add_argument('--output',required=True)
    p.add_argument('--device',default='cuda' if torch.cuda.is_available() else 'cpu')
    p.add_argument('--batch',type=int,default=128)
    p.add_argument('--holdout-radii',default='160,224,320,448')
    p.add_argument('--holdout-per-radius',type=int,default=1024)
    p.add_argument('--guard-per-radius',type=int,default=None,help='Optional exact-match assertion')
    p.add_argument('--max-guard-regressions',type=int,default=0)
    p.add_argument('--refine-rows',action=argparse.BooleanOptionalAction,default=True)
    p.add_argument('--refine-dimensions',action=argparse.BooleanOptionalAction,default=True)
    return p


if __name__=='__main__':
    run(parser().parse_args())
