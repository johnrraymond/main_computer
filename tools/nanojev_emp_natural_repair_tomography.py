#!/usr/bin/env python3
"""Paired natural-repair activation tomography on immutable NanoJev snapshots.

The before/after checkpoints are two *different* networks. Every forward and
reverse edit is therefore explicitly a cross-checkpoint activation transplant,
not proof of a unique computational circuit. Channel candidates are selected
leave-one-repaired-case-out and tested on the excluded error case. Stable
control cards and matched-control perturbations are reported separately.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

import torch

from tools import nanojev_emp_defect_cross_transfer as cross
from tools import nanojev_emp_isolated_defects as iso
from tools import nanojev_emp_first_failure as ff
from tools import nanojev_emp_hard_lasso as lasso
from tools import nanojev_emp_dual_bank as dual
from tools.nanojev_zero_line_zero_only import input_tensor
from tools.nanojev_zero_line_ladder import atomic_json

SCHEMA = 'nanojev.emp-natural-repair-tomography.v1'
SITES = ('backbone.layers.0@0', 'backbone.layers.0@1',
         'backbone.layers.1@0', 'backbone.layers.1@1', 'norm@0', 'norm@1')


def site_spec(key):
    name, n = key.rsplit('@', 1)
    return name, int(n)


def capture_site(model, card, device, key, channel=None, replacement=None):
    """One hooked module at one invocation; baseline and edits share topology."""
    name, which = site_spec(key)
    modules = dict(ff.activation_modules(model))
    if name not in modules:
        raise RuntimeError(f'NATURAL_TOMO_MISSING_LAYER:{name}')
    if (replacement is None) != (channel is None):
        raise ValueError('NATURAL_TOMO_EDIT_REQUIRES_CHANNEL_AND_VALUE')
    count = [0]
    arrays = []

    def hook(_module, _inputs, output):
        idx = count[0]
        count[0] += 1
        if idx != which:
            return None
        vector = ff.select_latent(output)
        if vector.shape[0] != 1:
            raise RuntimeError('NATURAL_TOMO_NON_SINGLETON_BATCH')
        arrays.append(vector[0].detach().to('cpu', dtype=torch.float64).clone())
        if replacement is None:
            return None
        if not 0 <= channel < vector.shape[-1]:
            raise ValueError('NATURAL_TOMO_CHANNEL_OUT_OF_BOUNDS')
        new = output.clone()
        if new.ndim == 3:
            new[:, -1, channel] = float(replacement)
        elif new.ndim == 2:
            new[:, channel] = float(replacement)
        else:
            raise RuntimeError('NATURAL_TOMO_UNSUPPORTED_ACTIVATION')
        return new

    handle = modules[name].register_forward_hook(hook)
    try:
        model.eval()
        with torch.inference_mode():
            raw = model(input_tensor([card], device))
            prediction = int(dual.decoded(raw)[0])
            logits = {k:v.detach() for k, v in raw.items() if k in ('sign','digits')}
    finally:
        handle.remove()
    if len(arrays) != 1:
        raise RuntimeError(f'NATURAL_TOMO_INVOCATION_MISSING:{key}:{len(arrays)}')
    return {'prediction':prediction, 'vector': arrays[0], 'logits':logits}


def encode_case(row):
    return (int(row['a']), int(row['b']))


def read_baseline(path):
    with Path(path).open(encoding='utf-8', newline='') as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise RuntimeError('NATURAL_TOMO_MISSING_BASELINE_CASES')
    cases = {}
    for r in rows:
        key = encode_case(r)
        if key in cases:
            raise RuntimeError('NATURAL_TOMO_DUPLICATE_CASE')
        target = int(r['target'])
        if sum(key) != target:
            raise RuntimeError('NATURAL_TOMO_INVALID_ARITHMETIC_CARD')
        cases[key] = {'target':target,'before':int(r['before']),
                      'after':int(r['after']), 'repaired':r['repaired'].lower()=='true',
                      'regressed':r['regressed'].lower()=='true'}
    return cases


def verify_baseline(cases, scored_before, scored_after):
    first = {tuple(r['card'][:2]): r for r in scored_before}
    second = {tuple(r['card'][:2]): r for r in scored_after}
    if set(first) != set(second) or set(first) != set(cases):
        raise RuntimeError('NATURAL_TOMO_CASE_SET_DRIFT')
    for key, saved in cases.items():
        if (first[key]['prediction'] != saved['before'] or
            second[key]['prediction'] != saved['after'] or
            saved['target'] != first[key]['card'][2]):
            raise RuntimeError(f'NATURAL_TOMO_PREDICTION_DRIFT:{key}')
        actual_repair = (saved['before'] != saved['target'] and
                         saved['after'] == saved['target'])
        if actual_repair != saved['repaired']:
            raise RuntimeError('NATURAL_TOMO_REPAIR_FLAG_DRIFT')
    return first


def contrasting_channels(repaired_deltas, stable_deltas, heldout, top):
    """LOFO selection: no activation of heldout defect influences its ranking."""
    fit = [v for key, v in repaired_deltas.items() if key != heldout]
    if not fit or not stable_deltas:
        return []
    a = torch.stack(fit)
    b = torch.stack(list(stable_deltas.values()))
    # Difference in checkpoint effects, divided by robust dispersion of the
    # *stable* controls. This is descriptive, not a calibrated p-value.
    contrast = a.mean(0) - b.mean(0)
    median = b.median(0).values
    mad = (b - median).abs().median(0).values
    scale = (mad * 1.4826).clamp_min(0.05)
    score = contrast.abs() / scale
    idx = torch.argsort(score, descending=True, stable=True)[:top]
    return [{'channel':int(i),'contrast':float(contrast[i]),
             'scaled_difference':float(score[i]),'control_mad':float(mad[i])}
            for i in idx]


def nearest_stable(target, stable_keys, n):
    return sorted(stable_keys,key=lambda x:(abs(x[0]-target[0])+abs(x[1]-target[1]),x))[:n]


def summarize_effects(records):
    trials = [r for r in records if r['status'] == 'tested']
    return {'attempts':len(records), 'tested':len(trials),
            'forward_repairs':sum(r['forward_repaired'] for r in trials),
            'reverse_regressions':sum(r['reverse_regressed'] for r in trials),
            'both_directions':sum(r['forward_repaired'] and r['reverse_regressed'] for r in trials),
            'matched_control_repairs':sum(r['matched_control_repaired'] for r in trials),
            'forward_control_damage':sum(r['forward_controls_damaged'] for r in trials),
            'reverse_control_damage':sum(r['reverse_controls_damaged'] for r in trials)}


def intervention(model, card, device, site, channel, value):
    outcome = capture_site(model,card,device,site,channel,float(value))
    return outcome['prediction']


def write_csv(path, rows):
    if not rows:
        return
    keys = list(dict.fromkeys(k for row in rows for k in row))
    tmp = Path(str(path) + '.tmp')
    with tmp.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


def run(args):
    if (args.before_stage >= args.after_stage or args.top_per_layer < 1 or
        args.controls < 1 or args.batch < 1 or args.min_direct_correct < 1):
        raise ValueError('NATURAL_TOMO_INVALID_ARGUMENTS')
    runroot=Path(args.run).resolve()
    evidence=Path(args.cross_transfer).resolve()
    out=Path(args.output).resolve()
    if not (evidence/'natural_repair.csv').exists():
        raise FileNotFoundError('NATURAL_TOMO_MISSING_CROSS_TRANSFER')
    ecfg=json.loads((evidence/'config.json').read_text(encoding='utf-8'))
    config=json.loads((runroot/'config.json').read_text(encoding='utf-8'))
    history=json.loads((runroot/'history.json').read_text(encoding='utf-8'))
    if (ecfg['before_stage'] != args.before_stage or ecfg['after_stage'] != args.after_stage or
        args.after_stage >= len(history) or
        Path(ecfg['source_run']).name != runroot.name):
        raise RuntimeError('NATURAL_TOMO_STAGE_PROVENANCE_DRIFT')
    before_path=runroot/f'stage_{args.before_stage:03d}'/'mastered_snapshot.pt'
    after_path=runroot/f'stage_{args.after_stage:03d}'/'mastered_snapshot.pt'
    if (cross.sha256(before_path) != ecfg['before_sha256'] or
        cross.sha256(after_path) != ecfg['after_sha256']):
        raise RuntimeError('NATURAL_TOMO_CHECKPOINT_SHA_DRIFT')
    before=cross.snapshot(before_path,config,args.before_stage)
    after=cross.snapshot(after_path,config,args.after_stage)
    cases=read_baseline(evidence/'natural_repair.csv')
    centers=cross.read_centers(runroot,args.before_stage,args.min_direct_correct)
    model,_,_,_,_=lasso.initialize(Path(args.campaign).resolve(),args.ladder_output,
             Path(args.campaign).resolve()/args.hard_state_dir/'latest.pt',args.device)
    model.requires_grad_(False).eval()
    radius=int(history[args.before_stage]['radius'])
    max_delta=int(history[args.before_stage]['max_delta'])
    model.load_state_dict(before,strict=True)
    scanned,failures=cross.gather_cluster(model,centers,radius,max_delta,args.device,args.batch,
                                           int(ecfg['window']))
    model.load_state_dict(after,strict=True)
    rescored=iso.score(model,scanned,args.device,args.batch)
    first=verify_baseline(cases,scanned,rescored)
    good=[k for k,v in cases.items() if v['before']==v['target'] and v['after']==v['target']]
    repaired=[k for k,v in cases.items() if v['repaired']]
    if len(good)<args.controls or len(repaired)<2:
        raise RuntimeError('NATURAL_TOMO_INSUFFICIENT_PAIRED_DATA')
    cfg={'schema':SCHEMA,'source_cross_transfer':str(evidence),
         'before_sha256':ecfg['before_sha256'],'after_sha256':ecfg['after_sha256'],
         'before_stage':args.before_stage,'after_stage':args.after_stage,
         'cases':len(cases),'repaired_cases':len(repaired),'stable_controls':len(good),
         'top_per_layer':args.top_per_layer,'controls_per_failure':args.controls,
         'sites':list(SITES),'device':args.device}
    out.mkdir(parents=True,exist_ok=True)
    prev=out/'config.json'
    if prev.exists() and json.loads(prev.read_text(encoding='utf-8'))!=cfg:
        raise RuntimeError('NATURAL_TOMO_OUTPUT_CONFIG_DRIFT')
    atomic_json(prev,cfg)
    # All sites are captured with exactly the same hook topology for both
    # checkpoints. Any parity drift is retained, never coerced.
    captures={'before':{},'after':{}}
    drifts=[]
    for label,state in [('before',before),('after',after)]:
        model.load_state_dict(state,strict=True)
        for site in SITES:
            for key in sorted(cases):
                result=capture_site(model,first[key]['card'],args.device,site)
                if result['prediction'] != cases[key][label]:
                    drifts.append({'checkpoint':label,'site':site,'card':list(key),
                                   'expected':cases[key][label],
                                   'hook_prediction':result['prediction']})
                    continue
                captures[label][(key,site)] = result['vector']
        print('EMP_NATURAL_TOMO_CAPTURE='+json.dumps({'checkpoint':label,
            'vectors':len(captures[label]),'drifts':sum(d['checkpoint']==label for d in drifts)}),flush=True)
    # Archive validated native activations and parity audit before any edits.
    torch.save({'schema':SCHEMA+'.activations','before':captures['before'],
                'after':captures['after'],'cases':cases,'drifts':drifts},
                out/'activations.pt')
    atomic_json(out/'capture_drifts.json',drifts)
    rows=[]; interventions=[]
    for site in SITES:
        valid=[k for k in cases if (k,site) in captures['before'] and
                                  (k,site) in captures['after']]
        changed={k:captures['after'][(k,site)]-captures['before'][(k,site)]
                 for k in valid}
        stable={k:changed[k] for k in good if k in changed}
        fixed={k:changed[k] for k in repaired if k in changed}
        if not stable or len(fixed)<2:
            continue
        stable_tensor=torch.stack(list(stable.values()))
        r_tensor=torch.stack(list(fixed.values()))
        contrast=r_tensor.mean(0)-stable_tensor.mean(0)
        median=stable_tensor.median(0).values
        mad=(stable_tensor-median).abs().median(0).values
        scale=(mad*1.4826).clamp_min(0.05)
        for ch in range(len(contrast)):
            rows.append({'site':site,'channel':ch,'repair_mean_change':float(r_tensor[:,ch].mean()),
                'control_mean_change':float(stable_tensor[:,ch].mean()),
                'repair_minus_control':float(contrast[ch]),
                'control_mad':float(mad[ch]),
                'scaled_difference':float(abs(contrast[ch])/scale[ch]),
                'repaired_n':len(fixed),'stable_control_n':len(stable)})
        for held in repaired:
            if held not in fixed:continue
            picks=contrasting_channels(fixed,stable,held,args.top_per_layer)
            controls=nearest_stable(held,[k for k in good if k in stable],args.controls)
            model.load_state_dict(before,strict=True)
            for rank,candidate in enumerate(picks,1):
                ch=candidate['channel']; original=first[held]['card']; gold=cases[held]['target']
                base_before=captures['before'][(held,site)][ch]
                after_target=captures['after'][(held,site)][ch]
                matched=controls[0]
                donor_shift=changed[matched][ch]
                # Verify that replacing the channel by its own unedited value
                # preserves the prediction, with identical hook topology.
                sham_before=intervention(model,original,args.device,site,ch,base_before)
                if sham_before != cases[held]['before']:
                    interventions.append({'a':held[0],'b':held[1],'site':site,'channel':ch,
                        'status':'before_sham_drift','sham_prediction':sham_before})
                    continue
                forward=intervention(model,original,args.device,site,ch,after_target)
                matched_prediction=intervention(model,original,args.device,site,ch,
                                                base_before+donor_shift)
                forward_damage=0
                for ctrl in controls:
                    pred=intervention(model,first[ctrl]['card'],args.device,site,ch,
                              captures['after'][(ctrl,site)][ch])
                    forward_damage += pred != cases[ctrl]['target']
                model.load_state_dict(after,strict=True)
                sham_after=intervention(model,original,args.device,site,ch,after_target)
                if sham_after != cases[held]['after']:
                    interventions.append({'a':held[0],'b':held[1],'site':site,'channel':ch,
                        'status':'after_sham_drift','sham_prediction':sham_after})
                    model.load_state_dict(before,strict=True)
                    continue
                reversed_pred=intervention(model,original,args.device,site,ch,base_before)
                reverse_damage=0
                for ctrl in controls:
                    pred=intervention(model,first[ctrl]['card'],args.device,site,ch,
                             captures['before'][(ctrl,site)][ch])
                    reverse_damage += pred != cases[ctrl]['target']
                model.load_state_dict(before,strict=True)
                interventions.append({'a':held[0],'b':held[1],'target':gold,
                    'site':site,'channel':ch,'rank_without_heldout':rank,
                    'loo_scaled_difference':candidate['scaled_difference'],
                    'before':cases[held]['before'],'after':cases[held]['after'],
                    'forward_prediction':forward,'forward_repaired':forward==gold,
                    'reverse_prediction':reversed_pred,'reverse_regressed':reversed_pred!=gold,
                    'reverse_restores_old_error':reversed_pred==cases[held]['before'],
                    'matched_control_prediction':matched_prediction,
                    'matched_control_repaired':matched_prediction==gold,
                    'forward_controls_damaged':forward_damage,
                    'reverse_controls_damaged':reverse_damage,
                    'controls_tested':len(controls),'status':'tested'})
        print('EMP_NATURAL_TOMO_SITE='+json.dumps({'site':site,
           'scored_channels':len(contrast),'repairs_evaluated':sum(r['site']==site for r in interventions)}),flush=True)
    write_csv(out/'channel_changes.csv',rows)
    write_csv(out/'transplants.csv',interventions)
    summary=summarize_effects(interventions)
    summary.update({'schema':SCHEMA,'status':'complete','cases':len(cases),
       'repaired_cases':len(repaired),'stable_controls':len(good),'sites':len(SITES),
       'activation_drifts':len(drifts),'channel_contrasts':len(rows),
       'checkpoint_sha256':[ecfg['before_sha256'],ecfg['after_sha256']],
       'limitations':['Cross-checkpoint transplant values need not inhabit the same representation',
         'Four repairs are insufficient for population-level effect estimates',
         'LOFO ranks share stable controls and are exploratory',
         'No singular neuron can be inferred from a successful single-channel transplant'],
       'output':str(out)})
    atomic_json(out/'result.json',summary)
    print('EMP_NATURAL_TOMOGRAPHY_RESULT='+json.dumps(summary,sort_keys=True),flush=True)
    return summary


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
    p.add_argument('--min-direct-correct',type=int,default=6)
    p.add_argument('--top-per-layer',type=int,default=4)
    p.add_argument('--controls',type=int,default=6)
    p.add_argument('--batch',type=int,default=128)
    return p


if __name__=='__main__':
    run(parser().parse_args())
