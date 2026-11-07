#!/usr/bin/env python3
"""V12: localize V11's dominant TRAIN sign conflict and test boundary calibration.

No Qwen/LoRA/production updates. V11 selected expanded capacity remains frozen;
only a six-parameter, format-specific class-logit calibration is trainable.
TRAIN-only preservation is checked on every accepted proposal under actual V6
inference (including abstention). DEV selects; examined TEST is diagnostics-only.
The correction never receives case IDs, labels, or group keys at inference.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from fractions import Fraction
from pathlib import Path

from tools import nanojev_gamer_v1_capacity_retention_v11 as v11
from tools import nanojev_gamer_v1_conflict_projection_v10 as v10
from tools import nanojev_gamer_v1_math_retention_v9 as v9
from tools import nanojev_gamer_v1_protected_math_repair_v7 as v7
from tools import nanojev_gamer_v1_protected_alignment_lab as v6
from tools import nanojev_gamer_v1_joint_head_repair as joint
from tools import nanojev_gamer_v1_head_only_lab as lab
from tools import nanojev_gamer_v1_sign_information_probe as probe

SCHEMA='nanojev.gamer.v1.boundary-isolation.v12'
FOCUS='train.offset.0041'


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _calibrator(torch,width):
    class Calibration(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.base=v11._expanded_model(torch,width)
            self.base.requires_grad_(False)
            self.route_bias=torch.nn.Parameter(torch.zeros((2,3)))
            self.protected=True
        def forward(self,x):
            if x.ndim!=2 or x.shape[1]!=1026:
                raise ValueError('V12_INVALID_ENCODED_FEATURE')
            residual=self.base(x)
            route=x[:,-2]
            ix=route.clamp(min=0,max=1).long()
            bias=torch.tanh(self.route_bias[ix])
            room=torch.clamp(v6.MAX_CLASS_LOGIT_CORRECTION - residual[:,1:].abs(),min=0.)
            delta=torch.cat((residual[:,:1],residual[:,1:]+room*bias),dim=1)
            return delta * (route[:,None]>=0).to(delta.dtype)
    return Calibration()


def _verify_v11(torch,folder,data,capture,prior_hash,spec,device):
    """Verify V11 selected AND terminal provenance, ledger and actual predictions."""
    import numpy as np
    root=Path(folder)
    report_file=root/'report.json'
    if not report_file.is_file():
        raise ValueError('V12_V11_REPORT_MISSING')
    report=json.loads(report_file.read_text(encoding='utf-8'))
    if (report.get('schema')!=v11.SCHEMA or
        report.get('status')!='EXPERIMENT_COMPLETE_NO_PROMOTION' or
        report.get('promotion_authorized') is not False or
        report.get('production_weights_modified') is not False or
        report.get('selected_arm')!='expanded_capacity_projected' or
        report.get('frozen_lineage')!=data['probe_info']['lineage'] or
        report.get('synthetic_dataset_sha256')!=data['probe_info']['dataset_sha256'] or
        report.get('original_train_cache_sha256')!=data['train_cache']['sha256'] or
        report.get('train_capture_sha256')!=capture or
        report.get('v6_prior_sha256')!=prior_hash or
        report.get('synthetic_train_only_retention_ledger') is not True or
        report.get('general_production_router')!='NOT_CERTIFIED' or
        report.get('source_dev_used_for_training') is not False or
        report.get('synthetic_dev_used_for_training') is not False or
        report.get('previously_seen_test_used_for_training_or_selection') is not False or
        report.get('terminal_states_saved_separately') is not True):
        raise ValueError('V12_V11_REPORT_PROVENANCE_MISMATCH')
    width=int(report['config']['capacity_width'])
    if not 8<=width<=256:
        raise ValueError('V12_V11_CAPACITY_INVALID')
    comparisons={(r['arm'],r['state']):r for r in report['comparisons']}
    cp={}; models={}; proof={}
    rows=spec['train_synthetic'][3]
    for state in ('selected','terminal'):
        path=root/f'expanded_capacity_projected_{state}_candidate.pt'
        if not path.is_file():
            raise ValueError('V12_V11_CHECKPOINT_MISSING_'+state)
        saved=torch.load(path,map_location='cpu',weights_only=True)
        ledger=saved.get('mathematical_train_retention_ledger',{})
        if (saved.get('schema')!=v11.SCHEMA or
            saved.get('status')!='DISPOSABLE_NON_PRODUCTION' or
            saved.get('arm')!='expanded_capacity_projected' or
            saved.get('state')!=state or
            saved.get('promotion_authorized') is not False or
            saved.get('protected_original_passthrough') is not True or
            saved.get('capture_bound_router_only') is not True or
            saved.get('frozen_lineage')!=data['probe_info']['lineage'] or
            saved.get('train_capture_sha256')!=capture or
            saved.get('prior_v6_sha256')!=prior_hash or
            saved.get('original_train_cache_sha256')!=data['train_cache']['sha256'] or
            saved.get('synthetic_dataset_sha256')!=data['probe_info']['dataset_sha256'] or
            saved.get('capacity_width')!=width or
            ledger.get('source_split')!='SYNTHETIC_TRAIN_ONLY' or
            ledger.get('train_capture_sha256')!=capture or
            ledger.get('checkpoint_step')!=saved.get('step') or
            saved.get('step')!=comparisons.get(('expanded_capacity_projected',state),{}).get('step')):
            raise ValueError('V12_V11_CHECKPOINT_PROVENANCE_MISMATCH_'+state)
        recorded=ledger.get('rows',[])
        if len(recorded)!=len(rows) or len(set(r.get('id') for r in recorded))!=len(rows):
            raise ValueError('V12_V11_LEDGER_INCOMPLETE_'+state)
        index={r['id']:r for r in rows}
        for entry in recorded:
            actual=index.get(entry['id'])
            if (actual is None or actual['group']!=entry.get('group') or
                actual['prompt_sha256']!=entry.get('prompt_sha256') or
                str(actual['target'])!=str(entry.get('target'))):
                raise ValueError('V12_V11_LEDGER_CAPTURE_CHANGED_'+state)
        net=v11._expanded_model(torch,width).to(device)
        net.load_state_dict(saved['weights'],strict=True)
        actual=v9._correct_train(torch,net,spec,saved['mean'].numpy(),saved['std'].numpy(),device)
        expected=np.asarray([bool(int(next(e['protected'] for e in recorded if e['id']==r['id']))) for r in rows])
        if not np.array_equal(actual,expected):
            raise ValueError('V12_V11_LEDGER_NOT_ACTUAL_'+state)
        if (int(actual.sum())!=ledger['correct_cases'] or
            int(actual.sum())!=comparisons[('expanded_capacity_projected',state)]['train_correct']):
            raise ValueError('V12_V11_LEDGER_COUNT_MISMATCH_'+state)
        cp[state]=saved;models[state]=net
        proof[state]={'checkpoint_sha256':_sha(path),'step':saved['step'],
            'train_correct':int(actual.sum()),'ledger_verified':True}
    if not torch.equal(cp['selected']['mean'],cp['terminal']['mean']) or not torch.equal(cp['selected']['std'],cp['terminal']['std']):
        raise ValueError('V12_V11_NORMALIZATION_CHANGED')
    return report,cp,models,proof


def _deck_prompts(data):
    info=data['probe_info'];rows=info['dataset']
    by_split=Counter(r['split'] for r in rows)
    ood=sum(r['family']=='hawk_indifference' for r in rows)//6
    deck=probe.make_deck(by_split['train']//6,by_split['dev']//6,
                          (by_split['test']-6*ood)//6,ood>0)
    if probe.deck_hash(deck)!=info['dataset_sha256'] or len(deck)!=len(rows):
        raise ValueError('V12_PROMPT_DECK_MISMATCH')
    out={}
    for expected,item in zip(rows,deck):
        if expected!=item.public() or probe.sha_text(item.prompt)!=expected['prompt_sha256']:
            raise ValueError('V12_PROMPT_FINGERPRINT_MISMATCH')
        out[item.id]=item.prompt
    return out


def _focus_atlas(torch,models,spec,mu,sd,device,focus,prompts,conflict_csv):
    import numpy as np
    rows,x,old,y,routes=v7._split_arrays(spec,'train')
    matching=[positions for positions,_ in joint._complete_training_groups(rows,y)
              if rows[int(positions[0])]['group']==focus]
    ix=list(matching[0]) if len(matching)==1 else []
    if len(ix)!=6 or [rows[i]['surface'] for i in ix]!=['source_native']*3+['spine_difference']*3:
        raise ValueError('V12_FOCUS_GROUP_NOT_COMPLETE_OR_ROUTED')
    from torch.nn import functional as F
    # Genuine frozen-feature similarity; no target or group features at runtime.
    z=torch.tensor(v6._encode(np.asarray(x)[ix],np.asarray(old)[ix],
                              np.asarray(routes)[ix],mu,sd)[:,:1024])
    unit=F.normalize(z,p=2,dim=1).numpy()
    cosine=unit@unit.T
    observations=[]
    for state,net in models.items():
        margins=v10._case_margins(torch,net,spec,mu,sd,device,ix)
        for pos,i in enumerate(ix):
            r=rows[i];m=margins[i];truth=v7._signed(Fraction(str(r['target'])))
            observations.append({'state':state,'id':r['id'],'group':r['group'],
                'surface':r['surface'],'prompt':prompts[r['id']],
                'prompt_sha256':r['prompt_sha256'],
                'target':str(r['target']),'target_sign':truth,
                'predicted_sign':m['prediction'], 'correct':int(truth==m['prediction']),
                'baseline_value':float(old[i]),'prediction_value':m['value'],
                'true_sign_margin':m['true_class_margin'],
                'residual_signed_log':m['residual_signed_log'],
                'residual_logit_l2':m['residual_logit_l2'],
                'paired_feature_cosine':float(cosine[pos,(pos+3)%6]),
                'contrast_feature_cosine':float(
                    (cosine[pos,(pos//3)*3]+cosine[pos,(pos//3)*3+2])/2
                    if pos%3==1 else cosine[pos,(pos//3)*3+2-(pos%3)]),
                'margin_risk':int(m['true_class_margin']<0.25)})
    conflicts=[]
    if conflict_csv.is_file():
        with conflict_csv.open(newline='',encoding='utf-8') as handle:
            for r in csv.DictReader(handle):
                if r.get('group')==focus and r.get('stage')=='raw_smallest':
                    if r.get('case_id') not in {rows[i]['id'] for i in ix}:
                        raise ValueError('V12_HISTORICAL_CONFLICT_ID_MISMATCH')
                    conflicts.append(r)
    return observations,Counter(r['case_id'] for r in conflicts)


def _make_state(torch,model,step,ledger,groups,ret,dev):
    return {'step':step,'weights':v9._copy_state(model),
        'ledger_mask':ledger.mask.copy(), 'acquired_step':ledger.acquired_step.copy(),
        'train':v9._memory_summary(ledger.mask,groups),'retention':ret,
        'dev':dev,'rank':joint._rank(ret,dev)}


def _repair(torch,starting_model,spec,mu,sd,device,focus,*,steps,eval_every,lr,seed,
            replay_weight,max_halvings,projection_passes,max_constraints):
    import numpy as np
    groups,capture=v9._groups_and_fingerprints(spec)
    rows,x,old,y,routes=v7._split_arrays(spec,'train')
    gidx=[i for i,(ix,_) in enumerate(groups) if rows[int(ix[0])]['group']==focus]
    if len(gidx)!=1:
        raise ValueError('V12_FOCUS_TRAIN_GROUP_INVALID')
    torch.manual_seed(seed)
    width=starting_model.native[0].out_features
    model=_calibrator(torch,width).to(device)
    model.base.load_state_dict(v9._copy_state(starting_model),strict=True)
    if any(p.requires_grad for p in model.base.parameters()):
        raise ValueError('V12_BASE_NOT_FROZEN')
    original_correct=v9._correct_train(torch,starting_model,spec,mu,sd,device)
    if not np.array_equal(original_correct,v9._correct_train(torch,model,spec,mu,sd,device)):
        raise ValueError('V12_ZERO_START_DRIFT')
    ledger=v9.MathematicalLedger(original_correct,rows,groups)
    xfull=torch.from_numpy(v6._encode(x,old,routes,mu,sd)).to(device)
    ofull=torch.as_tensor(old,dtype=torch.float32,device=device)
    yfull=torch.as_tensor(y,dtype=torch.float32,device=device)
    optimizer=torch.optim.AdamW([model.route_bias],lr=lr,weight_decay=0.)
    rng=np.random.default_rng(seed)
    ret,dev=v6._evaluate(torch,model,spec,mu,sd,device)
    if ret['status']!='PASS':raise ValueError('V12_ORIGINAL_RETENTION_FAILED_AT_START')
    best=_make_state(torch,model,0,ledger,groups,ret,dev)
    history=[];events=[];victims=[]
    focus_ix=np.asarray(groups[gidx[0]][0],dtype=np.int64)
    for step in range(1,steps+1):
        # TRAIN-only local intervention plus balanced random-group rehearsal.
        random_idx=int(rng.integers(len(groups)))
        replay=ledger.sample_replay_groups(rng,2)
        chosen=[groups[gidx[0]],groups[random_idx]]+[groups[int(i)] for i in replay]
        outcome,correct,lost=v10._attempt_update(torch,model,optimizer,
            (xfull,ofull,yfull),chosen,ledger,spec,mu,sd,device=device,
            step=step,arm='route_logit_calibration',max_halvings=max_halvings,
            replay_weight=replay_weight,projection_passes=projection_passes,
            max_constraints=max_constraints)
        victims.extend(lost)
        if outcome['accepted']:
            event=ledger.acquire(correct,step)
        else:
            event={'new_correct':0,**v9._memory_summary(ledger.mask,groups)}
        outcome.update({'new_correct':event['new_correct'],
            'train_locked':event['correct_cases']})
        events.append(outcome)
        if step%eval_every==0 or step==steps:
            if not ledger.accepts(v9._correct_train(torch,model,spec,mu,sd,device)):
                raise ValueError('V12_HARD_MATHEMATICAL_RETENTION_FAILED')
            ret,dev=v6._evaluate(torch,model,spec,mu,sd,device)
            if ret['status']!='PASS':
                raise ValueError('V12_ORIGINAL_TASK_RETENTION_FAILED')
            focus_correct=v9._correct_train(torch,model,spec,mu,sd,device)[focus_ix]
            record={'step':step,'dev_sign':dev['all']['balanced_sign_accuracy'],
                'dev_six':dev['cross_format']['complete_six_case_groups'],
                'train_correct':int(ledger.mask.sum()),
                'focus_correct':int(focus_correct.sum()),
                'accepted':sum(r['accepted'] for r in events),
                'rescues':sum(r['status']=='projected' for r in events),
                'rejected':sum(r['status']=='rejected' for r in events)}
            history.append(record)
            if joint._rank(ret,dev)>best['rank']:
                best=_make_state(torch,model,step,ledger,groups,ret,dev)
            print('V12_CALIBRATION_PROGRESS '+probe.canonical(record),flush=True)
    final=_make_state(torch,model,steps,ledger,groups,ret,dev)
    return model,best,final,events,victims,history


def _compare_dev(torch,reference,candidate,spec,mu,sd,device):
    baseline=v7._case_atlas(torch,reference,spec,mu,sd,device,'dev')
    changed=v7._case_atlas(torch,candidate,spec,mu,sd,device,'dev')
    rows,groups=v11._dev_transitions(baseline,changed,'route_logit_calibration','candidate')
    return rows,groups


def run(args):
    import numpy as np
    import torch
    if any(getattr(args,n,None) is None for n in
           ('run','expanded_run','input','source_features','prior','v11_run','output')):
        raise ValueError('V12_REQUIRED_INPUT_MISSING')
    if (args.steps<1 or args.steps>5000 or args.eval_every<1 or
        not math.isfinite(args.lr) or args.lr<=0 or
        not math.isfinite(args.replay_weight) or args.replay_weight<0 or
        not 0<=args.max_halvings<=12 or not 1<=args.projection_passes<=20 or
        not 1<=args.max_constraints<=128):
        raise ValueError('V12_INVALID_PARAMETERS')
    if str(args.device).startswith('cuda') and not torch.cuda.is_available():
        raise ValueError('V12_CUDA_NOT_AVAILABLE')
    output=lab._output(args.output,[Path(getattr(args,n)) for n in
          ('run','expanded_run','input','source_features','prior','v11_run')])
    data,prepared=joint._prepare(args)
    spec,_=v6._routed_profiles(data,prepared)
    groups,capture=v9._groups_and_fingerprints(spec)
    base,mu,sd,_=v7._read_v6_checkpoint(torch,args.prior,data,spec)
    base=base.to(args.device)
    v11_report,cp,models,proof=_verify_v11(torch,args.v11_run,data,capture,
                                               _sha(args.prior),spec,args.device)
    if not (np.array_equal(mu,cp['selected']['mean'].numpy()) and
            np.array_equal(sd,cp['selected']['std'].numpy())):
        raise ValueError('V12_V11_NORMALIZATION_MISMATCH')
    prompts=_deck_prompts(data)
    focus_rows,conflict_counts=_focus_atlas(torch,models,spec,mu,sd,args.device,
        args.focus_group,prompts,Path(args.v11_run)/'projected_control_conflict_cases.csv')
    # Historical counts supplied by V11's independently verified V10 witness;
    # v11's own attempted conflicts are checked below if files exist.
    if args.focus_group not in dict(v11_report['historical_top_conflicts']):
        raise ValueError('V12_UNVERIFIED_FOCUS_GROUP')
    selected=models['selected'];control=models['terminal']
    reference_ret,reference_dev=v6._evaluate(torch,selected,spec,mu,sd,args.device)
    if reference_ret['status']!='PASS':
        raise ValueError('V12_V11_REFERENCE_RETENTION_FAILURE')
    comparison=[{'state':'v11_selected','step':cp['selected']['step'],
       'train_correct':cp['selected']['mathematical_train_retention_ledger']['correct_cases'],
       'dev_sign':reference_dev['all']['balanced_sign_accuracy'],
       'dev_six':reference_dev['cross_format']['complete_six_case_groups'],
       'original_retention':reference_ret['status']}]
    snapshots={};events=[];victims=[];history=[];dev_cases=[];dev_groups=[]
    focus_after=[]
    if args.mode=='repair':
        candidate,best,terminal,events,victims,history=_repair(torch,selected,spec,mu,sd,
           args.device,args.focus_group,steps=args.steps,eval_every=args.eval_every,
           lr=args.lr,seed=args.seed,replay_weight=args.replay_weight,
           max_halvings=args.max_halvings,projection_passes=args.projection_passes,
           max_constraints=args.max_constraints)
        for name,snapshot in [('selected',best),('terminal',terminal)]:
            candidate.load_state_dict(snapshot['weights'],strict=True)
            if not np.array_equal(v9._correct_train(torch,candidate,spec,mu,sd,args.device),
                                  snapshot['ledger_mask']):
                raise ValueError('V12_SAVED_LEDGER_PREDICTION_MISMATCH')
            ret,dev=v6._evaluate(torch,candidate,spec,mu,sd,args.device)
            cases,groups_changed=_compare_dev(torch,selected,candidate,spec,mu,sd,args.device)
            for r in cases:r['state']=name
            for r in groups_changed:r['state']=name
            dev_cases+=cases;dev_groups+=groups_changed
            focus_current,_=_focus_atlas(torch,{name:candidate},spec,mu,sd,args.device,
                args.focus_group,prompts,
                Path(args.v11_run)/'projected_control_conflict_cases.csv')
            focus_after.extend(focus_current)
            comparison.append({'state':'calibration_'+name,'step':snapshot['step'],
               'train_correct':snapshot['train']['correct_cases'],
               'train_six':snapshot['train']['complete_six_case_groups'],
               'dev_sign':dev['all']['balanced_sign_accuracy'],
               'dev_six':dev['cross_format']['complete_six_case_groups'],
               'dev_gained_cases':sum(r['transition']=='newly_correct' for r in cases),
               'dev_lost_cases':sum(r['transition']=='lost_correct' for r in cases),
               'dev_gained_groups':sum(g['new_complete'] for g in groups_changed),
               'dev_lost_groups':sum(g['lost_complete'] for g in groups_changed),
               'original_retention':ret['status']})
            snapshots[name]=snapshot
    # No TEST examination: this is a focus-specific TRAIN hypothesis test and
    # DEV generalization observation; repeatedly examined test remains untouched.
    report={'schema':SCHEMA,'status':'EXPERIMENT_COMPLETE_NO_PROMOTION',
        'mode':args.mode,'focus_group':args.focus_group,
        'hypothesis':'FORMAT_SPECIFIC_CLASS_BOUNDARY_CALIBRATION',
        'frozen_lineage':data['probe_info']['lineage'],
        'synthetic_dataset_sha256':data['probe_info']['dataset_sha256'],
        'training_capture_sha256':capture,'v6_sha256':_sha(args.prior),
        'v11_report_sha256':_sha(Path(args.v11_run)/'report.json'),
        'v11_verified_checkpoints':proof,'focus_member_count':len(focus_rows)//2,
        'v11_historical_conflict_events':dict(v11_report['historical_top_conflicts']).get(args.focus_group),
        'focus_case_v11_conflicts':dict(conflict_counts),
        'focus_case_min_true_sign_margin_by_state':{name:min(float(r['true_sign_margin'])
            for r in focus_rows+focus_after if r['state']==name)
            for name in sorted({r['state'] for r in focus_rows+focus_after})},
        'comparison':comparison,
        'accepted_proposals':sum(r['accepted'] for r in events),
        'rescued_proposals':sum(r['status']=='projected' for r in events),
        'rejected_proposals':sum(r['status']=='rejected' for r in events),
        'calibration_parameter_count':6,
        'calibration_uses_format_not_group_id':True,
        'train_only_gradients_and_hard_retention':True,
        'original_task_hard_bypass':'UNCHANGED_V6',
        'general_production_router':'NOT_CERTIFIED',
        'source_dev_used_for_fitting':False,
        'synthetic_dev_used_for_fitting':False,
        'previously_seen_test_used_for_fitting_selection_or_metric':False,
        'production_weights_modified':False,
        'promotion_authorized':False,
        'causality':'BOUNDARY_HYPOTHESIS_TEST_NOT_CAUSAL_PROOF'}
    joint._verify_after(data)
    output.mkdir(parents=True,exist_ok=False)
    v10._csv(output/'focus_group_cases.csv',focus_rows)
    if focus_after:v10._csv(output/'focus_group_calibrated_cases.csv',focus_after)
    v10._csv(output/'comparison.csv',comparison)
    if history:v10._csv(output/'calibration_history.csv',history)
    if events:v10._csv(output/'calibration_attempts.csv',events)
    if victims:v10._csv(output/'calibration_conflict_cases.csv',victims)
    if dev_cases:v10._csv(output/'dev_case_transitions.csv',dev_cases)
    if dev_groups:v10._csv(output/'dev_group_transitions.csv',dev_groups)
    for name,snapshot in snapshots.items():
        checkpoint={'schema':SCHEMA,'status':'DISPOSABLE_NON_PRODUCTION',
            'state':name,'step':snapshot['step'],'weights':snapshot['weights'],
            'mean':torch.as_tensor(mu),'std':torch.as_tensor(sd),
            'train_capture_sha256':capture,'v11_report_sha256':report['v11_report_sha256'],
            'v6_prior_sha256':report['v6_sha256'],
            'base_v11_selected_checkpoint_sha256':proof['selected']['checkpoint_sha256'],
            'capacity_width':int(v11_report['config']['capacity_width']),
            'frozen_lineage':data['probe_info']['lineage'],
            'train_ledger_mask':torch.from_numpy(snapshot['ledger_mask']),
            'train_acquired_step':torch.from_numpy(snapshot['acquired_step']),
            'capture_bound_router_only':True,
            'protected_original_passthrough':True,
            'promotion_authorized':False}
        torch.save(checkpoint,output/f'calibration_{name}_candidate.pt')
    (output/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True,
                                             allow_nan=False)+'\n',encoding='utf-8')
    print('V12_RESULT '+probe.canonical({'mode':args.mode,'focus':args.focus_group,
        'comparisons':comparison,'accepted':report['accepted_proposals'],
        'rescues':report['rescued_proposals'],'report':str(output/'report.json'),
        'promotion':False}),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('diagnose','repair'),default='repair')
    p.add_argument('--run',type=Path,required=True)
    p.add_argument('--expanded-run',type=Path,required=True)
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--source-features',type=Path,required=True)
    p.add_argument('--prior',type=Path,required=True)
    p.add_argument('--v11-run',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--device',default='cuda')
    p.add_argument('--focus-group',default=FOCUS)
    p.add_argument('--steps',type=int,default=120)
    p.add_argument('--eval-every',type=int,default=15)
    p.add_argument('--lr',type=float,default=.001)
    p.add_argument('--seed',type=int,default=49)
    p.add_argument('--replay-weight',type=float,default=2.)
    p.add_argument('--max-halvings',type=int,default=7)
    p.add_argument('--projection-passes',type=int,default=4)
    p.add_argument('--max-constraints',type=int,default=24)
    return p


def main(argv=None):
    run(parser().parse_args(argv))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
