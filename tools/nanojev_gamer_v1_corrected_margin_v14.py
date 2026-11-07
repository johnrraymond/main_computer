#!/usr/bin/env python3
"""V14: matched margin-consolidation ablation with restored V11 acquisition capacity.

Consolidation fits ONLY a small format-conditioned adapter on mathematical TRAIN.
It protects all acquired TRAIN sign decisions using the exact confidence-gated
V6 inference, not the additional V13 per-case never-decrease margin floors.
Both acquisition arms use identical trainable V11 residual + margin adapter,
identical full-TRAIN monotonic sign gate, replay, projection, optimizer and seed.
The only treatment is whether consolidation preceded acquisition. DEV ranks
checkpoints; previously examined TEST is not accessed. Every saved weight state
is checked against its own TRAIN-only ledger. No promotion is authorized.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path

from tools import nanojev_gamer_v1_margin_consolidation_v13 as v13
from tools import nanojev_gamer_v1_boundary_isolation_v12 as v12
from tools import nanojev_gamer_v1_capacity_retention_v11 as v11
from tools import nanojev_gamer_v1_conflict_projection_v10 as v10
from tools import nanojev_gamer_v1_math_retention_v9 as v9
from tools import nanojev_gamer_v1_protected_math_repair_v7 as v7
from tools import nanojev_gamer_v1_protected_alignment_lab as v6
from tools import nanojev_gamer_v1_joint_head_repair as joint
from tools import nanojev_gamer_v1_head_only_lab as lab
from tools import nanojev_gamer_v1_sign_information_probe as probe

SCHEMA='nanojev.gamer.v1.corrected-margin-consolidation.v14'
ARMS=('no_consolidation_control','consolidated_acquisition')


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _acquisition_model(torch, margin_width, capacity_width):
    """Same state keys as V13, but gradients flow through the V11 specialist.

    V11's inner V6 base remains frozen. Margin correction is always limited by
    the remaining V6 class-logit correction room. No id or target enters forward.
    """
    class Acquisition(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.base=v11._expanded_model(torch,capacity_width)
            self.native=torch.nn.Sequential(torch.nn.Linear(1024,margin_width),
                torch.nn.GELU(),torch.nn.Linear(margin_width,3))
            self.game=torch.nn.Sequential(torch.nn.Linear(1024,margin_width),
                torch.nn.GELU(),torch.nn.Linear(margin_width,3))
            for branch in (self.native,self.game):
                torch.nn.init.zeros_(branch[-1].weight)
                torch.nn.init.zeros_(branch[-1].bias)
            self.protected=True
        def forward(self,x):
            if x.ndim!=2 or x.shape[1]!=1026:
                raise ValueError('V14_BAD_ENCODED_INPUT')
            baseline=self.base(x)  # NOT no_grad: V11 native/game/decoder are trainable.
            route=x[:,-2:-1]
            z=torch.where(route>0.5,self.game(x[:,:1024]),self.native(x[:,:1024]))
            room=torch.clamp(v6.MAX_CLASS_LOGIT_CORRECTION-baseline[:,1:].abs(),min=0.)
            out=torch.cat((baseline[:,:1],baseline[:,1:]+room*torch.tanh(z)),dim=1)
            return out*(route>=0).to(out.dtype)
    model=Acquisition()
    if not all(not p.requires_grad for p in model.base.base.parameters()):
        raise ValueError('V14_ORIGINAL_BASE_TRAINABLE')
    if not all(p.requires_grad for p in (*model.base.native.parameters(),
             *model.base.game.parameters(),*model.base.decoder.parameters(),
             *model.native.parameters(),*model.game.parameters())):
        raise ValueError('V14_ACQUISITION_CAPACITY_FROZEN')
    return model


def _hard_probe(torch,model,spec,ledger,mu,sd,device):
    correct=v9._correct_train(torch,model,spec,mu,sd,device)
    return ledger.accepts(correct),correct


def _candidate_consolidation(torch,model,before,displacement,spec,ledger,encoded,
                             oldt,truth,required,mu,sd,device,max_halvings,
                             objective_before):
    """Try any exact-sign-safe step that strictly improves TRAIN reserve deficit.

    No continuous per-item floor is imposed: some margins can temporarily
    shrink while every acquired answer remains exactly classified correctly.
    """
    import numpy as np
    closest=None
    for h in range(max_halvings+1):
        alpha=2.**(-h)
        v10._set_displacement(torch,model,before,displacement,alpha)
        safe,correct=_hard_probe(torch,model,spec,ledger,mu,sd,device)
        if not safe:
            continue
        margins,deficits=v13._margin_census(torch,model,encoded,oldt,truth,required)
        total=float(deficits[ledger.mask].sum())
        if total<objective_before-1e-7:
            return alpha,correct,margins,total
        if closest is None or total<closest:
            closest=total
    return None,None,None,closest


def _consolidate(torch,starting,spec,mu,sd,device,*,margin_width,capacity_width,
                 steps,lr,seed,reserve,max_halvings,projection_passes,
                 max_constraints,eval_every):
    """V13 correction class + original V13 freeze, but only hard sign gate.

    The objective measures aggregate deficit on protected TRAIN examples;
    it cannot use DEV/TEST for proposal admission or checkpoint selection.
    An accepted update must reduce this quantity and preserve all TRAIN signs.
    """
    import numpy as np
    groups,capture=v9._groups_and_fingerprints(spec)
    tx,old,y,rows,routes=spec['train_synthetic']
    truth=np.sign(y).astype(np.int64)
    torch.manual_seed(seed)
    net=v13._margin_model(torch,margin_width,capacity_width).to(device)
    net.base.load_state_dict(starting,strict=True)
    if not all(not p.requires_grad for p in net.base.parameters()):
        raise ValueError('V14_CONSOLIDATION_BASE_NOT_FROZEN')
    encoded=torch.from_numpy(v6._encode(tx,old,routes,mu,sd)).to(device)
    oldt=torch.as_tensor(old,dtype=torch.float32,device=device)
    required=v13._required_margin(old,truth,reserve)
    ledger=v9.MathematicalLedger(v9._correct_train(torch,net,spec,mu,sd,device),rows,groups)
    margins,deficit=v13._margin_census(torch,net,encoded,oldt,truth,required)
    start={'step':0,'weights':v9._copy_state(net),
           'ledger_mask':ledger.mask.copy(),'acquired_step':ledger.acquired_step.copy()}
    before_total=float(deficit[ledger.mask].sum())
    opt=torch.optim.AdamW([p for p in net.parameters() if p.requires_grad],
                          lr=lr,weight_decay=0.)
    attempts=[]
    for step in range(1,steps+1):
        prior=v9._copy_state(net);optimizer_before=copy.deepcopy(opt.state_dict())
        net.train();opt.zero_grad(set_to_none=True)
        signed_safety=v13._class_margins(torch,net,encoded,oldt,truth)
        locked=torch.as_tensor(ledger.mask,device=device,dtype=torch.bool)
        target=torch.as_tensor(required,device=device,dtype=signed_safety.dtype)
        shortage=torch.relu(target[locked]-signed_safety[locked])
        # Include the whole protected population, with extra focus on its
        # worst reserves. No case-specific inference routing or identifiers.
        loss=shortage.square().mean()+0.02*shortage.mean()
        if not bool(torch.isfinite(loss)):
            raise ValueError('V14_NONFINITE_CONSOLIDATION_LOSS')
        if float(loss.detach())<=1e-14:
            attempts.append({'step':step,'status':'already_sufficient','accepted':0,
                             'deficit_sum':before_total,'protected':int(ledger.mask.sum())})
            break
        loss.backward();torch.nn.utils.clip_grad_norm_(
            [p for p in net.parameters() if p.requires_grad],1.)
        opt.step()
        displacement=v10._parameter_displacement(net,prior)
        alpha,correct,next_margins,next_total=_candidate_consolidation(
            torch,net,prior,displacement,spec,ledger,encoded,oldt,truth,required,
            mu,sd,device,max_halvings,before_total)
        status='raw' if alpha is not None else 'rejected'
        projection_detail={}
        if alpha is None:
            # Projection is a proposal only. Exact full-TRAIN decisions and
            # aggregate shortfall reduction still decide acceptance.
            v10._set_displacement(torch,net,prior,displacement,2.**(-max_halvings))
            correct_small=v9._correct_train(torch,net,spec,mu,sd,device)
            fragile=np.flatnonzero(ledger.mask & ~correct_small).tolist()
            net.load_state_dict(prior,strict=True)
            current,_=v13._margin_census(torch,net,encoded,oldt,truth,required)
            weakest=sorted(np.flatnonzero(ledger.mask),key=lambda i:
                          (current[i],str(rows[i]['id'])))
            indices=list(dict.fromkeys(fragile+weakest))[:max_constraints]
            grads=v13._affine_gradients(torch,net,prior,encoded,oldt,truth,indices)
            # Protect the actual decision at first order, not a newly imposed
            # V13 floor; positive reserve is created by the consolidation loss.
            offsets=[float(-current[i]+1e-5) for i in indices]
            projected,projection_detail=v13._affine_projection(
                displacement,grads,offsets,projection_passes)
            alpha,correct,next_margins,next_total=_candidate_consolidation(
                torch,net,prior,projected,spec,ledger,encoded,oldt,truth,
                required,mu,sd,device,max_halvings,before_total)
            if alpha is not None:
                status='projected'
        if alpha is None:
            net.load_state_dict(prior,strict=True)
            opt.load_state_dict(optimizer_before)
        else:
            if status=='projected' or alpha<1.:
                opt.load_state_dict(optimizer_before)
            ledger.acquire(correct,step)
            # Newly acquired answers enter the next objective immediately;
            # do not compare deficits from different ledger populations.
            _,updated_deficit=v13._margin_census(
                torch,net,encoded,oldt,truth,required)
            before_total=float(updated_deficit[ledger.mask].sum())
        attempts.append({'step':step,'status':status,'accepted':int(alpha is not None),
                         'alpha':float(alpha or 0.),'deficit_sum':float(before_total),
                         'protected':int(ledger.mask.sum()),
                         'projection_constraints':projection_detail.get('constraints',0)})
        if step%eval_every==0 or step==steps:
            print('V14_CONSOLIDATION_PROGRESS '+probe.canonical({
                'step':step,'accepted':sum(a['accepted'] for a in attempts),
                'rejected':sum(a['status']=='rejected' for a in attempts),
                'deficit_sum':float(before_total),'train_locked':int(ledger.mask.sum())}),
                flush=True)
    safe,actual=_hard_probe(torch,net,spec,ledger,mu,sd,device)
    if not safe or not np.array_equal(actual,ledger.mask):
        raise ValueError('V14_CONSOLIDATION_RETENTION_DRIFT')
    final={'step':steps,'weights':v9._copy_state(net),
           'ledger_mask':ledger.mask.copy(),'acquired_step':ledger.acquired_step.copy()}
    return net,start,final,attempts


def _verify_v13(root,capture,prior_hash,proof,provenance,focus):
    source=Path(root)/'report.json'
    if not source.is_file():
        raise ValueError('V14_V13_REPORT_MISSING')
    report=json.loads(source.read_text(encoding='utf-8'))
    if (report.get('schema')!=v13.SCHEMA or
        report.get('status')!='EXPERIMENT_COMPLETE_NO_PROMOTION' or
        report.get('training_capture_sha256')!=capture or
        report.get('v6_prior_sha256')!=prior_hash or
        report.get('v11_verified')!=proof or
        report.get('v12_verified')!=provenance or
        report.get('focus_group')!=focus or
        report.get('source_dev_used_for_training') is not False or
        report.get('synthetic_dev_used_for_training') is not False or
        report.get('previously_seen_test_used_for_training_or_selection') is not False or
        report.get('production_weights_modified') is not False or
        report.get('promotion_authorized') is not False or
        len(report.get('comparison',[]))!=4):
        raise ValueError('V14_V13_PROVENANCE_MISMATCH')
    return {'report_sha256':_sha(source),
            'historical_consolidation_accepted':report['consolidation']['accepted'],
            'historical_selected_arm':report['selected']}


def _validate(args,torch):
    fields=('run','expanded_run','input','source_features','prior',
            'v11_run','v12_run','v13_run','output')
    if any(getattr(args,name,None) is None for name in fields):
        raise ValueError('V14_REQUIRED_INPUT_MISSING')
    if (not (1<=args.steps<=20000 and args.eval_every>=1 and
             1<=args.consolidation_steps<=20000) or
        not math.isfinite(args.lr) or args.lr<=0 or
        not math.isfinite(args.consolidation_lr) or args.consolidation_lr<=0 or
        not (0<args.reserve<0.5) or not (8<=args.margin_width<=256) or
        not (0<=args.max_halvings<=12) or
        not (1<=args.projection_passes<=20) or
        not (1<=args.max_constraints<=128) or
        not math.isfinite(args.replay_weight) or args.replay_weight<0):
        raise ValueError('V14_INVALID_CONFIGURATION')
    if str(args.device).startswith('cuda') and not torch.cuda.is_available():
        raise ValueError('V14_CUDA_NOT_AVAILABLE')
    return fields


def run(args):
    import numpy as np
    import torch
    fields=_validate(args,torch)
    out=lab._output(args.output,[Path(getattr(args,n)) for n in fields[:-1]])
    data,prepared=joint._prepare(args)
    spec,_=v6._routed_profiles(data,prepared)
    groups,capture=v9._groups_and_fingerprints(spec)
    _,mu,sd,_=v7._read_v6_checkpoint(torch,args.prior,data,spec)
    report11,cp11,models,proof=v12._verify_v11(
        torch,args.v11_run,data,capture,_sha(args.prior),spec,args.device)
    if not (np.array_equal(mu,cp11['selected']['mean'].numpy()) and
            np.array_equal(sd,cp11['selected']['std'].numpy())):
        raise ValueError('V14_NORMALIZER_CHANGED')
    provenance=v13._check_v12(args.v12_run,proof,capture,_sha(args.prior),
                     args.focus_group,_sha(Path(args.v11_run)/'report.json'))
    verified13=_verify_v13(args.v13_run,capture,_sha(args.prior),proof,
                           provenance,args.focus_group)
    capacity_width=report11['config']['capacity_width']
    prior=cp11['selected']['weights']
    baseline_cases=v7._case_atlas(torch,models['selected'],spec,mu,sd,args.device,'dev')
    adapter,initial,consolidated,stage_a=_consolidate(
        torch,prior,spec,mu,sd,args.device,margin_width=args.margin_width,
        capacity_width=capacity_width,steps=args.consolidation_steps,
        lr=args.consolidation_lr,seed=args.seed,reserve=args.reserve,
        max_halvings=args.max_halvings,projection_passes=args.projection_passes,
        max_constraints=args.max_constraints,eval_every=args.eval_every)
    tx,old,y,rows,routes=spec['train_synthetic']
    truth=np.sign(y).astype(np.int64)
    encoded=torch.from_numpy(v6._encode(tx,old,routes,mu,sd)).to(args.device)
    oldt=torch.as_tensor(old,device=args.device,dtype=torch.float32)
    required=v13._required_margin(old,truth,args.reserve)
    tracked=[]
    for title,snapshot in (('before',initial),('after',consolidated)):
        adapter.load_state_dict(snapshot['weights'],strict=True)
        margins,deficits=v13._margin_census(torch,adapter,encoded,oldt,truth,required)
        observed=v9._correct_train(torch,adapter,spec,mu,sd,args.device)
        if not np.array_equal(observed,snapshot['ledger_mask']):
            raise ValueError('V14_CONSOLIDATION_SNAPSHOT_LEDGER_MISMATCH')
        for i,r in enumerate(rows):
            tracked.append({'stage':title,'id':r['id'],'group':r['group'],
                'surface':r['surface'],'prompt_sha256':r['prompt_sha256'],
                'protected':int(snapshot['ledger_mask'][i]),
                'target_sign':int(truth[i]),'decision_safety':float(margins[i]),
                'deficit':float(deficits[i]),'reserve':args.reserve})
    saved={};comp=[];cases=[];group_changes=[]
    for arm in ARMS:
        seed_state=initial if arm==ARMS[0] else consolidated
        def builder(t):return _acquisition_model(t,args.margin_width,capacity_width)
        model,best,ledger,attempts,victims,history,stats=v10._fit_arm(
            torch,spec,seed_state['weights'],mu,sd,
            arm='expanded_capacity_projected',device=args.device,
            steps=args.steps,eval_every=args.eval_every,lr=args.lr,
            seed=args.seed,replay_weight=args.replay_weight,
            max_halvings=args.max_halvings,projection_passes=args.projection_passes,
            max_constraints=args.max_constraints,model_builder=builder,
            capture_terminal=True)
        terminal=stats.pop('terminal_snapshot')
        saved[arm]={'model':model,'selected':best,'terminal':terminal,
                    'attempts':attempts,'victims':victims,'history':history,
                    'diagnostics':stats,'initial':seed_state}
        print('V14_ARM '+probe.canonical({'arm':arm,'accepted':stats['accepted_updates'],
             'rescues':stats['projection_rescues'],'selected_step':best['step'],
             'terminal_train':stats['full_run_final_train']}),flush=True)
    for arm,record in saved.items():
        net=record['model']
        for state in ('selected','terminal'):
            snap=record[state]
            net.load_state_dict(snap['weights'],strict=True)
            checked=v9._correct_train(torch,net,spec,mu,sd,args.device)
            if (not np.array_equal(checked,snap['ledger_mask']) or
                not np.array_equal(checked,snap['acquired_step']>=0)):
                raise ValueError('V14_ACQUISITION_SNAPSHOT_LEDGER_MISMATCH')
            ret,dev=v6._evaluate(torch,net,spec,mu,sd,args.device)
            if ret['status']!='PASS' or dev['all']['balanced_sign_accuracy']!=snap['dev']['all']['balanced_sign_accuracy']:
                raise ValueError('V14_SNAPSHOT_RETENTION_OR_DEV_MISMATCH')
            atlas=v7._case_atlas(torch,net,spec,mu,sd,args.device,'dev')
            dc,dg=v11._dev_transitions(baseline_cases,atlas,arm,state)
            cases.extend(dc);group_changes.extend(dg)
            item={'arm':arm,'state':state,'step':snap['step'],
              **v9._memory_summary(checked,groups),
              'dev_correct':dev['all']['correct_class'],
              'dev_sign':dev['all']['balanced_sign_accuracy'],
              'dev_zero_recall':dev['all']['zero_recall'],
              'dev_six':dev['cross_format']['complete_six_case_groups'],
              'dev_triples':dev['all']['triple_all_correct'],
              'dev_gained_cases':sum(c['transition']=='newly_correct' for c in dc),
              'dev_lost_cases':sum(c['transition']=='lost_correct' for c in dc),
              'dev_gained_groups':sum(g['new_complete'] for g in dg),
              'dev_lost_groups':sum(g['lost_complete'] for g in dg),
              'original_retention':ret['status']}
            comp.append(item)
    selected=max((r for r in comp if r['state']=='selected'),
             key=lambda r:saved[r['arm']]['selected']['rank'])
    focus_before=[r['decision_safety'] for r in tracked if r['stage']=='before' and r['group']==args.focus_group]
    focus_after=[r['decision_safety'] for r in tracked if r['stage']=='after' and r['group']==args.focus_group]
    if len(focus_before)!=6 or len(focus_after)!=6:
        raise ValueError('V14_FOCUS_GROUP_INVALID')
    initial_locked=initial['ledger_mask'];final_locked=consolidated['ledger_mask']
    protected_ids={r['id'] for i,r in enumerate(rows) if initial_locked[i]}
    sumdef=lambda state:sum(r['deficit'] for r in tracked
                            if r['stage']==state and r['id'] in protected_ids)
    con_summary={'attempts':len(stage_a),'accepted':sum(a['accepted'] for a in stage_a),
       'rejected':sum(a['status']=='rejected' for a in stage_a),
       'rescued':sum(a['status']=='projected' for a in stage_a),
       'initial_locked':int(initial_locked.sum()),'final_locked':int(final_locked.sum()),
       'minimum_focus_safety_before':float(min(focus_before)),
       'minimum_focus_safety_after':float(min(focus_after)),
       'initial_protected_deficit_sum_before':float(sumdef('before')),
       'initial_protected_deficit_sum_after':float(sumdef('after')),
       'target_reserve':float(args.reserve)}
    report={'schema':SCHEMA,'status':'EXPERIMENT_COMPLETE_NO_PROMOTION',
       'frozen_lineage':data['probe_info']['lineage'],
       'synthetic_dataset_sha256':data['probe_info']['dataset_sha256'],
       'original_train_cache_sha256':data['train_cache']['sha256'],
       'training_capture_sha256':capture,'v6_prior_sha256':_sha(args.prior),
       'v11_verified':proof,'v12_verified':provenance,'v13_verified':verified13,
       'focus_group':args.focus_group,'consolidation':con_summary,
       'comparisons':comp,'selected_arm':selected['arm'],
       'selected_step':selected['step'],
       'diagnostics':{arm:rec['diagnostics'] for arm,rec in saved.items()},
       'config':{'steps':args.steps,'eval_every':args.eval_every,'lr':args.lr,
                 'consolidation_steps':args.consolidation_steps,
                 'consolidation_lr':args.consolidation_lr,'reserve':args.reserve,
                 'margin_width':args.margin_width,'capacity_width':capacity_width,
                 'seed':args.seed,'replay_weight':args.replay_weight,
                 'max_halvings':args.max_halvings,
                 'projection_passes':args.projection_passes,
                 'max_constraints':args.max_constraints},
       'stage_a_gate':'EXACT_INFERENCE_MONOTONIC_TRAIN_SIGNS_AND_AGGREGATE_DEFICIT_IMPROVEMENT',
       'stage_b_gate':'UNCHANGED_V10_EXACT_INFERENCE_MONOTONIC_TRAIN_SIGNS',
       'stage_b_trainable_capacity':'V11_EXPANDED_RESIDUAL_PLUS_MATCHED_MARGIN_ADAPTER',
       'frozen_v6_base_verified':True,'matched_training_schedule':True,
       'synthetic_train_only_retention_ledger':True,
       'synthetic_dev_used_for_training':False,'source_dev_used_for_training':False,
       'previously_seen_test_used_for_training_or_selection':False,
       'production_weights_modified':False,'promotion_authorized':False,
       'capture_bound_router_only':True,'general_production_router':'NOT_CERTIFIED'}
    joint._verify_after(data)
    out.mkdir(parents=True,exist_ok=False)
    v10._csv(out/'comparison.csv',comp)
    v10._csv(out/'consolidation_attempts.csv',stage_a)
    v10._csv(out/'train_margin_census.csv',tracked)
    v10._csv(out/'dev_case_transitions.csv',cases)
    v10._csv(out/'dev_group_transitions.csv',group_changes)
    for arm,record in saved.items():
        v10._csv(out/f'{arm}_attempts.csv',record['attempts'])
        v10._csv(out/f'{arm}_history.csv',record['history'])
        if record['victims']:
            v10._csv(out/f'{arm}_conflict_cases.csv',record['victims'])
        for state in ('selected','terminal'):
            snap=record[state]
            cp={'schema':SCHEMA,'status':'DISPOSABLE_NON_PRODUCTION',
              'arm':arm,'state':state,'step':snap['step'],
              'weights':snap['weights'],'mean':torch.as_tensor(mu),
              'std':torch.as_tensor(sd),'margin_width':args.margin_width,
              'capacity_width':capacity_width,'train_capture_sha256':capture,
              'v11_selected_sha256':proof['selected']['checkpoint_sha256'],
              'v13_report_sha256':verified13['report_sha256'],
              'frozen_lineage':data['probe_info']['lineage'],
              'mathematical_train_retention_ledger':{
                'source_split':'SYNTHETIC_TRAIN_ONLY',
                'train_capture_sha256':capture,'rows':v11._ledger_csv(
                    rows,snap['ledger_mask'],snap['acquired_step'])},
              'protected_original_passthrough':True,
              'capture_bound_router_only':True,'promotion_authorized':False}
            torch.save(cp,out/f'{arm}_{state}_candidate.pt')
            if arm==selected['arm'] and state=='selected':
                torch.save(cp,out/'disposable_candidate.pt')
    torch.save({'schema':SCHEMA,'status':'DISPOSABLE_NON_PRODUCTION',
      'state':'post_consolidation','weights':consolidated['weights'],
      'mean':torch.as_tensor(mu),'std':torch.as_tensor(sd),
      'margin_width':args.margin_width,'capacity_width':capacity_width,
      'train_capture_sha256':capture,
      'train_ledger_mask':torch.as_tensor(consolidated['ledger_mask']),
      'train_acquired_step':torch.as_tensor(consolidated['acquired_step']),
      'frozen_lineage':data['probe_info']['lineage'],
      'protected_original_passthrough':True,'promotion_authorized':False},
      out/'post_consolidation_candidate.pt')
    (out/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True,
       allow_nan=False)+'\n',encoding='utf-8')
    for item in comp:
        print('V14_RESULT '+probe.canonical(item),flush=True)
    print('V14_COMPLETE '+probe.canonical({'selected':selected['arm'],
       'consolidation_accepted':con_summary['accepted'],
       'report':str(out/'report.json'),'promotion':False}),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('train',),default='train')
    for name in ('run','expanded-run','input','source-features','prior',
                 'v11-run','v12-run','v13-run','output'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--device',default='cuda')
    p.add_argument('--steps',type=int,default=180)
    p.add_argument('--consolidation-steps',type=int,default=90)
    p.add_argument('--eval-every',type=int,default=15)
    p.add_argument('--lr',type=float,default=.001)
    p.add_argument('--consolidation-lr',type=float,default=.0005)
    p.add_argument('--reserve',type=float,default=.08)
    p.add_argument('--margin-width',type=int,default=32)
    p.add_argument('--focus-group',default=v12.FOCUS)
    p.add_argument('--seed',type=int,default=49)
    p.add_argument('--replay-weight',type=float,default=2.)
    p.add_argument('--max-halvings',type=int,default=7)
    p.add_argument('--projection-passes',type=int,default=4)
    p.add_argument('--max-constraints',type=int,default=24)
    return p


def main(argv=None):
    run(parser().parse_args(argv));return 0


if __name__=='__main__':
    raise SystemExit(main())
