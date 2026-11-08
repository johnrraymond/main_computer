#!/usr/bin/env python3
"""V15: TRAIN-only local margin feasibility audit and matched multi-seed V14 replication.

No changes to V14 architecture, training losses, hard full-TRAIN sign gate,
original-task bypass, or selection rank. A linearized gradient-span search is
DIAGNOSTIC ONLY: it never certifies global structural infeasibility and never
commits weights. Verified v14 output supplies the post-consolidation checkpoint
for the focus-case audit; deterministic seed pairs restart from v11 selected.
DEV only ranks already-retention-safe checkpoints. Previously seen TEST is not
accessed. All checkpoints are disposable; promotion is always false.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from tools import nanojev_gamer_v1_corrected_margin_v14 as v14
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

SCHEMA='nanojev.gamer.v1.margin-feasibility-and-multiseed.v15'
ARMS=v14.ARMS


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _seeds(text):
    try:
        vals=[int(x.strip()) for x in str(text).split(',')]
    except (TypeError,ValueError) as e:
        raise argparse.ArgumentTypeError('V15_INVALID_SEEDS') from e
    if not 2<=len(vals)<=8 or len(set(vals))!=len(vals) or any(x<0 or x>=2**31 for x in vals):
        raise argparse.ArgumentTypeError('V15_REQUIRE_2_TO_8_UNIQUE_SEEDS')
    return vals


def _check_prior14(root, proof, capture, prior_hash, data, focus, torch, spec, mu, sd, device):
    """Verify capture identity, model lineage and actual TRAIN ledger of V14."""
    import numpy as np
    source=Path(root)/'report.json'
    cpfile=Path(root)/'post_consolidation_candidate.pt'
    if not source.is_file() or not cpfile.is_file():
        raise ValueError('V15_V14_EVIDENCE_MISSING')
    report=json.loads(source.read_text(encoding='utf-8'))
    if (report.get('schema')!=v14.SCHEMA or
        report.get('status')!='EXPERIMENT_COMPLETE_NO_PROMOTION' or
        report.get('promotion_authorized') is not False or
        report.get('production_weights_modified') is not False or
        report.get('matched_training_schedule') is not True or
        report.get('source_dev_used_for_training') is not False or
        report.get('synthetic_dev_used_for_training') is not False or
        report.get('previously_seen_test_used_for_training_or_selection') is not False or
        report.get('training_capture_sha256')!=capture or
        report.get('v6_prior_sha256')!=prior_hash or
        report.get('v11_verified')!=proof or
        report.get('frozen_lineage')!=data['probe_info']['lineage'] or
        report.get('focus_group')!=focus or
        report.get('general_production_router')!='NOT_CERTIFIED' or
        len(report.get('comparisons',[]))!=4):
        raise ValueError('V15_V14_PROVENANCE_MISMATCH')
    cfg=report['config']
    cp=torch.load(cpfile,map_location='cpu',weights_only=True)
    if (cp.get('schema')!=v14.SCHEMA or
        cp.get('status')!='DISPOSABLE_NON_PRODUCTION' or
        cp.get('state')!='post_consolidation' or
        cp.get('promotion_authorized') is not False or
        cp.get('protected_original_passthrough') is not True or
        cp.get('train_capture_sha256')!=capture or
        cp.get('frozen_lineage')!=data['probe_info']['lineage'] or
        cp.get('margin_width')!=cfg['margin_width'] or
        cp.get('capacity_width')!=cfg['capacity_width'] or
        not np.array_equal(cp['mean'].numpy(),mu) or
        not np.array_equal(cp['std'].numpy(),sd)):
        raise ValueError('V15_V14_CHECKPOINT_PROVENANCE_MISMATCH')
    model=v13._margin_model(torch,cfg['margin_width'],cfg['capacity_width']).to(device)
    model.load_state_dict(cp['weights'],strict=True)
    seen=v9._correct_train(torch,model,spec,mu,sd,device)
    expected=cp['train_ledger_mask'].cpu().numpy().astype(bool)
    acquired=cp['train_acquired_step'].cpu().numpy()
    if (not np.array_equal(seen,expected) or
        not np.array_equal(expected,acquired>=0) or
        int(seen.sum())!=report['consolidation']['final_locked']):
        raise ValueError('V15_V14_LEDGER_NOT_ACTUAL')
    return report,cp,model,{'v14_report_sha256':_hash(source),
             'v14_post_consolidation_sha256':_hash(cpfile),'train_ledger_verified':True}


def _study_positions(rows,protected,deficits,focus,limit):
    import numpy as np
    focus_ix=[i for i,r in enumerate(rows) if r['group']==focus]
    if len(focus_ix)!=6 or not any(protected[i] for i in focus_ix):
        raise ValueError('V15_FOCUS_NOT_COMPLETE_OR_HAS_NO_PROTECTED_CASES')
    deficient=[int(i) for i in np.flatnonzero(protected & (deficits>1e-10))]
    deficient.sort(key=lambda i:(-float(deficits[i]),str(rows[i]['id'])))
    prioritized=list(dict.fromkeys([i for i in focus_ix if protected[i]]+deficient))
    if len(prioritized)>limit:
        protected_focus=[i for i in focus_ix if protected[i]]
        prioritized=protected_focus+sorted((i for i in deficient if i not in protected_focus),
                 key=lambda i:(-float(deficits[i]),str(rows[i]['id'])))[:limit-len(protected_focus)]
    return prioritized,deficient,focus_ix


def _gradient_span_feasibility(grads,needs,trust_radius):
    """Minimum-norm linearized solution in sampled deficit-gradient span.

    SLSQP numerical failure is INCONCLUSIVE. Finite trust-radius exclusion is
    never labeled global structural incompatibility. Does not mutate model.
    """
    import numpy as np
    import torch
    try:
        from scipy.optimize import minimize
    except ImportError:
        minimize=None
    G=np.stack([g.detach().cpu().numpy().astype(np.float64) for g in grads],axis=0)
    if not (np.isfinite(G).all() and np.isfinite(needs).all()):
        raise ValueError('V15_NONFINITE_MARGIN_GRADIENT')
    needs=np.asarray(needs,dtype=np.float64)
    if len(needs)!=len(G) or (needs<0).any():
        raise ValueError('V15_INVALID_LINEAR_TARGETS')
    norms=np.linalg.norm(G,axis=1)
    if np.any((norms<1e-12)&(needs>1e-9)):
        return {'status':'FIRST_ORDER_ZERO_GRADIENT_FOR_REQUIRED_RESERVE',
                'num_constraints':len(G),'linearized_feasible':False,
                'minimal_norm':None,'trust_radius':float(trust_radius)},None
    normed=G/np.maximum(norms[:,None],1e-12)
    Gram=normed@normed.T
    b=needs/np.maximum(norms,1e-12)
    def obj(c):return .5*float(c@Gram@c)
    def jac(c):return Gram@c
    if minimize is not None:
        res=minimize(obj,np.zeros(len(G),dtype=np.float64),jac=jac,
              method='SLSQP',constraints=[{'type':'ineq',
              'fun':lambda c:Gram@c-b,'jac':lambda c:Gram}],
              options={'maxiter':400,'ftol':1e-11})
        coefficients=res.x
        optimizer_success=bool(res.success)
        method='SCIPY_SLSQP'
    else:
        # Optional-dependency fallback. Cyclic row-space projections are a
        # feasibility probe, not a minimum-norm certificate.
        coefficients=np.zeros(len(G),dtype=np.float64)
        for _ in range(2048):
            for i in range(len(G)):
                shortage=b[i]-float(Gram[i]@coefficients)
                if shortage>0 and Gram[i,i]>1e-12:
                    coefficients[i]+=shortage/Gram[i,i]
            if float(np.max(b-Gram@coefficients))<=1e-6:
                break
            if float(np.linalg.norm(coefficients))>1e7:
                break
        optimizer_success=bool(np.max(b-Gram@coefficients)<=1e-6)
        method='NUMPY_CYCLIC_HALFSPACE_FALLBACK'
    d=normed.T@coefficients
    achieved=G@d
    residual=needs-achieved
    fits=bool(np.max(residual)<=1e-5 and np.isfinite(d).all())
    norm=float(np.linalg.norm(d)) if fits else None
    ok_in_trust=bool(fits and norm<=trust_radius*(1+1e-6))
    state=('LINEARIZED_TARGET_FEASIBLE_WITHIN_TRUST' if ok_in_trust else
           'LINEARIZED_TARGET_REQUIRES_LARGER_STEP' if fits else
           'LINEARIZED_SOLVER_INCONCLUSIVE')
    cos=normed@normed.T
    ii,jj=np.triu_indices(len(G),1)
    min_pair=None
    if len(ii):
        p=int(np.argmin(cos[ii,jj]))
        min_pair={'first':int(ii[p]),'second':int(jj[p]),
                  'gradient_cosine':float(cos[ii[p],jj[p]])}
    return {'status':state,'num_constraints':len(G),
            'linearized_feasible':fits,'minimal_norm':norm,
            'trust_radius':float(trust_radius),'within_trust':ok_in_trust,
            'smallest_gradient_norm':float(norms.min()),
            'largest_required_increase':float(needs.max()),
            'max_linearized_shortfall':float(max(0,residual.max())) if fits else None,
            'optimizer_success':optimizer_success,'solver_method':method,
            'most_opposed_gradient_pair':min_pair},torch.from_numpy(d.astype(np.float32)) if fits else None


def _margin_study(torch,model,cp,spec,mu,sd,device,focus,reserve,max_cases,trust_radius,*,prompts=None):
    """Train-only gradient and exact-gate audit. Never retains trial weights."""
    import numpy as np
    tx,old,y,rows,routes=spec['train_synthetic']
    truth=np.sign(y).astype(np.int64)
    from fractions import Fraction
    if any(int(np.sign(Fraction(str(r['target']))))!=int(truth[i]) for i,r in enumerate(rows)):
        raise ValueError('V15_TRAIN_TRUTH_MISMATCH')
    required=v13._required_margin(old,truth,reserve)
    encoded=torch.from_numpy(v6._encode(tx,old,routes,mu,sd)).to(device)
    oldt=torch.as_tensor(old,dtype=torch.float32,device=device)
    before=v9._copy_state(model)
    protected=cp['train_ledger_mask'].cpu().numpy().astype(bool)
    margins,deficits=v13._margin_census(torch,model,encoded,oldt,truth,required)
    positions,deficient,focus_ix=_study_positions(rows,protected,deficits,focus,max_cases)
    case_rows=[{'id':r['id'],'group':r['group'],'surface':r['surface'],
         'prompt_sha256':r['prompt_sha256'],'target':r['target'],
         'protected':int(protected[i]),'decision_safety':float(margins[i]),
         'reserve_shortfall':float(deficits[i]),'gradient_selected':int(i in positions)}
         for i,r in enumerate(rows) if protected[i]]
    gradients=v13._affine_gradients(torch,model,before,encoded,oldt,truth,positions)
    for i,g in zip(positions,gradients):
        for entry in case_rows:
            if entry['id']==rows[i]['id']:
                entry['gradient_norm']=float(torch.linalg.vector_norm(g))
                break
    for entry in case_rows:
        if prompts is not None and (entry['group']==focus or entry['reserve_shortfall']>0):
            if entry['id'] not in prompts or probe.sha_text(prompts[entry['id']])!=entry['prompt_sha256']:
                raise ValueError('V15_PROMPT_FINGERPRINT_MISMATCH')
            entry['verified_prompt']=prompts[entry['id']]
    needs=np.asarray([deficits[i] for i in positions],dtype=np.float64)
    feasibility,direction=_gradient_span_feasibility(gradients,needs,trust_radius)
    feasibility['selected_ids']=[rows[i]['id'] for i in positions]
    feasibility['protected_cases']=int(protected.sum())
    feasibility['total_deficient_cases']=len(deficient)
    feasibility['deficient_cases_omitted_from_gradient_span']=len(set(deficient)-set(positions))
    feasibility['focus_cases']=[rows[i]['id'] for i in focus_ix]
    feasibility['reserve_deficit_before']=float(deficits[protected].sum())
    feasibility['reserve_deficit_after_best_exact_probe']=None
    feasibility['exact_probe_accepted']=False
    feasibility['probe_is_diagnostic_only']=True
    probes=[]
    try:
        if direction is not None:
            norm=float(torch.linalg.vector_norm(direction))
            scale=min(1.,trust_radius/max(norm,1e-12))
            for h in range(5):
                alpha=scale*(2.**(-h))
                v10._set_displacement(torch,model,before,direction,alpha)
                ok,correct=v14._hard_probe(torch,model,spec,
                      v9.MathematicalLedger(protected,rows,v9._groups_and_fingerprints(spec)[0]),
                      mu,sd,device)
                nmargin,ndeficit=v13._margin_census(torch,model,encoded,oldt,truth,required)
                deficit_total=float(ndeficit[protected].sum())
                probes.append({'scale':float(alpha),'hard_retention_pass':bool(ok),
                    'protected_lost':int((protected&~correct).sum()),
                    'reserve_deficit_sum':deficit_total,
                    'focus_min_safety':float(nmargin[focus_ix].min()),
                    'all_sampled_reserves_met':bool(np.all(ndeficit[positions]<=1e-6))})
            safe=[p for p in probes if p['hard_retention_pass']]
            if safe:
                best=min(safe,key=lambda p:p['reserve_deficit_sum'])
                feasibility['exact_probe_accepted']=True
                feasibility['reserve_deficit_after_best_exact_probe']=best['reserve_deficit_sum']
                feasibility['best_exact_probe_improved_deficit']=(best['reserve_deficit_sum']<
                      feasibility['reserve_deficit_before']-1e-7)
    finally:
        model.load_state_dict(before,strict=True)
    if not np.array_equal(v9._correct_train(torch,model,spec,mu,sd,device),protected):
        raise ValueError('V15_DIAGNOSTIC_MUTATED_MODEL')
    return case_rows,feasibility,probes


def _checkpoint(torch,path,model,snap,mu,sd,capture,seed,arm,state,lineage):
    model.load_state_dict(snap['weights'],strict=True)
    torch.save({'schema':SCHEMA,'status':'DISPOSABLE_NON_PRODUCTION',
      'weights':snap['weights'],'mean':torch.as_tensor(mu),'std':torch.as_tensor(sd),
      'seed':seed,'arm':arm,'state':state,'step':snap['step'],
      'train_capture_sha256':capture,'frozen_lineage':lineage,
      'train_ledger_mask':torch.as_tensor(snap['ledger_mask']),
      'train_acquired_step':torch.as_tensor(snap['acquired_step']),
      'protected_original_passthrough':True,'promotion_authorized':False},path)


def _validate(args,torch):
    if (args.mode not in ('study','sweep') or
        args.steps<1 or args.consolidation_steps<1 or args.eval_every<1 or
        not (0<args.reserve<.5) or
        not (8<=args.margin_width<=256) or
        args.max_gradient_cases<6 or args.max_gradient_cases>64 or
        not math.isfinite(args.trust_radius) or args.trust_radius<=0 or
        not math.isfinite(args.lr) or args.lr<=0 or
        not math.isfinite(args.consolidation_lr) or args.consolidation_lr<=0 or
        args.max_halvings<0 or args.max_halvings>12 or
        not 1<=args.max_constraints<=128 or
        not 1<=args.projection_passes<=20 or
        not math.isfinite(args.replay_weight) or args.replay_weight<0 or
        not isinstance(args.seeds,list) or len(set(args.seeds))!=len(args.seeds) or
        not 2<=len(args.seeds)<=8):
        raise ValueError('V15_INVALID_CONFIGURATION')
    if str(args.device).startswith('cuda') and not torch.cuda.is_available():
        raise ValueError('V15_CUDA_NOT_AVAILABLE')


def run(args):
    import numpy as np
    import torch
    _validate(args,torch)
    inputs=[args.run,args.expanded_run,args.input,args.source_features,
            args.prior,args.v11_run,args.v14_run]
    out=lab._output(args.output,[Path(v) for v in inputs])
    if out.exists():
        raise FileExistsError(out)
    data,prepared=joint._prepare(args)
    spec,_=v6._routed_profiles(data,prepared)
    groups,capture=v9._groups_and_fingerprints(spec)
    _,mu,sd,_=v7._read_v6_checkpoint(torch,args.prior,data,spec)
    report11,cp11,models,proof=v12._verify_v11(
        torch,args.v11_run,data,capture,_hash(args.prior),spec,args.device)
    if not (np.array_equal(mu,cp11['selected']['mean'].numpy()) and
            np.array_equal(sd,cp11['selected']['std'].numpy())):
        raise ValueError('V15_NORMALIZER_MISMATCH')
    report14,post,post_model,verified=_check_prior14(args.v14_run,proof,capture,
            _hash(args.prior),data,args.focus_group,torch,spec,mu,sd,args.device)
    if args.margin_width!=report14['config']['margin_width'] or args.reserve!=report14['config']['reserve']:
        raise ValueError('V15_CONFIG_NOT_V14_MATCHED')
    prompt_map=v12._deck_prompts(data)
    case_rows,geometry,probes=_margin_study(torch,post_model,post,spec,mu,sd,
          args.device,args.focus_group,args.reserve,args.max_gradient_cases,
          args.trust_radius,prompts=prompt_map)
    baseline=v7._case_atlas(torch,models['selected'],spec,mu,sd,args.device,'dev')
    comparisons=[];consolidation_rows=[];dev_cases=[];dev_groups=[];seeds_summary=[];snapshots=[]
    capacity_width=report11['config']['capacity_width']
    if args.mode=='sweep':
        for seed in args.seeds:
            net,initial,consolidated,attempts=v14._consolidate(
                torch,cp11['selected']['weights'],spec,mu,sd,args.device,
                margin_width=args.margin_width,capacity_width=capacity_width,
                steps=args.consolidation_steps,lr=args.consolidation_lr,
                seed=seed,reserve=args.reserve,max_halvings=args.max_halvings,
                projection_passes=args.projection_passes,
                max_constraints=args.max_constraints,eval_every=args.eval_every)
            # Reload both states to measure their respective actual deficits.
            censuses=[]
            for snapshot in (initial,consolidated):
                net.load_state_dict(snapshot['weights'],strict=True)
                mx,dx=v13._margin_census(torch,net,
                   torch.from_numpy(v6._encode(spec['train_synthetic'][0],
                     spec['train_synthetic'][1],spec['train_synthetic'][4],mu,sd)).to(args.device),
                   torch.as_tensor(spec['train_synthetic'][1],device=args.device,dtype=torch.float32),
                   np.sign(spec['train_synthetic'][2]).astype(np.int64),
                   v13._required_margin(spec['train_synthetic'][1],
                       np.sign(spec['train_synthetic'][2]).astype(np.int64),args.reserve))
                censuses.append(float(dx[initial['ledger_mask']].sum()))
            stat={'seed':seed,'consolidation_accepted':sum(a['accepted'] for a in attempts),
              'deficit_before':censuses[0],'deficit_after':censuses[1],
              'margin_improved':bool(censuses[1]<censuses[0]-1e-7)}
            consolidation_rows.extend([{'seed':seed,**a} for a in attempts])
            snapshots.append((seed,'post_consolidation','post_consolidation',consolidated,
                              v13._margin_model(torch,args.margin_width,capacity_width)))
            for arm in ARMS:
                snap=initial if arm==ARMS[0] else consolidated
                model,best,ledger,train_events,victims,history,stats=v10._fit_arm(
                    torch,spec,snap['weights'],mu,sd,arm='expanded_capacity_projected',
                    device=args.device,steps=args.steps,eval_every=args.eval_every,
                    lr=args.lr,seed=seed,replay_weight=args.replay_weight,
                    max_halvings=args.max_halvings,
                    projection_passes=args.projection_passes,
                    max_constraints=args.max_constraints,
                    model_builder=lambda t:v14._acquisition_model(
                         t,args.margin_width,capacity_width),capture_terminal=True)
                terminal=stats.pop('terminal_snapshot')
                stat[arm+'_accepted']=stats['accepted_updates']
                stat[arm+'_rescues']=stats['projection_rescues']
                for state,checkpoint in (('selected',best),('terminal',terminal)):
                    model.load_state_dict(checkpoint['weights'],strict=True)
                    actual=v9._correct_train(torch,model,spec,mu,sd,args.device)
                    if not np.array_equal(actual,checkpoint['ledger_mask']):
                        raise ValueError('V15_CHECKPOINT_LEDGER_NOT_ACTUAL')
                    ret,dev=v6._evaluate(torch,model,spec,mu,sd,args.device)
                    if ret['status']!='PASS':
                        raise ValueError('V15_ORIGINAL_RETENTION_FAILED')
                    atlas=v7._case_atlas(torch,model,spec,mu,sd,args.device,'dev')
                    changed,groups_changed=v11._dev_transitions(baseline,atlas,arm,state)
                    gains=sum(r['transition']=='newly_correct' for r in changed)
                    lost=sum(r['transition']=='lost_correct' for r in changed)
                    group_gains=sum(r['new_complete'] for r in groups_changed)
                    group_losses=sum(r['lost_complete'] for r in groups_changed)
                    item={'seed':seed,'arm':arm,'state':state,'step':checkpoint['step'],
                         'train_correct':int(actual.sum()),
                         'train_six':v9._memory_summary(actual,groups)['complete_six_case_groups'],
                         'dev_correct':int(dev['all']['correct_class']),
                         'dev_sign':float(dev['all']['balanced_sign_accuracy']),
                         'dev_six':int(dev['cross_format']['complete_six_case_groups']),
                         'dev_zero_recall':float(dev['all']['zero_recall']),
                         'dev_gained_cases':gains,'dev_lost_cases':lost,
                         'dev_gained_groups':group_gains,'dev_lost_groups':group_losses,
                         'original_retention':'PASS',
                         'accepted_updates':stats['accepted_updates'],
                         'projection_rescues':stats['projection_rescues']}
                    comparisons.append(item)
                    dev_cases.extend([{'seed':seed,**r} for r in changed]);dev_groups.extend(
                        [{'seed':seed,**r} for r in groups_changed])
                    snapshots.append((seed,arm,state,checkpoint,model))
                    if state=='selected':
                        stat[arm+'_dev_correct']=item['dev_correct']
                        stat[arm+'_dev_six']=item['dev_six']
                print('V15_ARM '+probe.canonical({'seed':seed,'arm':arm,
                    'accepted':stats['accepted_updates'],'rescues':stats['projection_rescues'],
                    'selected_step':best['step'],'terminal_train':stats['full_run_final_train']}),flush=True)
            seeds_summary.append(stat)
    selected_rows=[r for r in comparisons if r['state']=='selected']
    by_seed={s:{} for s in args.seeds}
    for r in selected_rows:by_seed[r['seed']][r['arm']]=r
    paired=[]
    for seed,items in by_seed.items():
        if len(items)==2:
            control=items[ARMS[0]];treatment=items[ARMS[1]]
            paired.append({'seed':seed,'delta_dev_correct':treatment['dev_correct']-control['dev_correct'],
                           'delta_dev_six':treatment['dev_six']-control['dev_six'],
                           'delta_accepted_updates':treatment['accepted_updates']-control['accepted_updates'],
                           'delta_train_correct':treatment['train_correct']-control['train_correct']})
    seed_aggregate={'seed_count':len(paired),
       'accepted_gain_seeds':sum(p['delta_accepted_updates']>0 for p in paired),
       'dev_case_gain_seeds':sum(p['delta_dev_correct']>0 for p in paired),
       'dev_group_gain_seeds':sum(p['delta_dev_six']>0 for p in paired),
       'dev_case_loss_seeds':sum(p['delta_dev_correct']<0 for p in paired),
       'mean_accepted_update_delta':float(np.mean([p['delta_accepted_updates'] for p in paired])) if paired else None,
       'mean_dev_correct_delta':float(np.mean([p['delta_dev_correct'] for p in paired])) if paired else None,
       'mean_dev_six_delta':float(np.mean([p['delta_dev_six'] for p in paired])) if paired else None,
       'not_a_significance_claim':True}
    report={'schema':SCHEMA,'status':'DIAGNOSTIC_COMPLETE_NO_PROMOTION',
       'mode':args.mode,'frozen_lineage':data['probe_info']['lineage'],
       'synthetic_dataset_sha256':data['probe_info']['dataset_sha256'],
       'training_capture_sha256':capture,'v6_prior_sha256':_hash(args.prior),
       'v11_verified':proof,'v14_verified':verified,'focus_group':args.focus_group,
       'geometry':geometry,'paired_seed_results':paired,'seed_aggregate':seed_aggregate,'per_seed':seeds_summary,
       'comparisons':comparisons,
       'config':{'seeds':args.seeds,'steps':args.steps,'consolidation_steps':args.consolidation_steps,
          'eval_every':args.eval_every,'lr':args.lr,'consolidation_lr':args.consolidation_lr,
          'reserve':args.reserve,'margin_width':args.margin_width,
          'capacity_width':capacity_width,'trust_radius':args.trust_radius,
          'max_gradient_cases':args.max_gradient_cases},
       'interpretation':'LOCAL_FIRST_ORDER_GRADIENT_SPAN_ONLY_NOT_GLOBAL_INFEASIBILITY',
       'hard_train_retention':'UNCHANGED_V14_ACTUAL_INFERENCE_ALL_ACQUIRED',
       'original_task_bypass':'UNCHANGED_V6',
       'synthetic_train_only_gradients':True,'synthetic_dev_used_for_training':False,
       'previously_seen_test_used_for_training_or_selection':False,
       'production_weights_modified':False,'promotion_authorized':False,
       'general_production_router':'NOT_CERTIFIED'}
    joint._verify_after(data)
    out.mkdir(parents=True,exist_ok=False)
    v10._csv(out/'protected_train_margin_deficits.csv',case_rows)
    v10._csv(out/'linearized_exact_probe.csv',probes or [{'scale':0,'hard_retention_pass':True,
       'protected_lost':0,'reserve_deficit_sum':geometry['reserve_deficit_before'],
       'focus_min_safety':None,'all_sampled_reserves_met':False}])
    if args.mode=='sweep':
        v10._csv(out/'seed_pairs.csv',paired)
        v10._csv(out/'comparisons.csv',comparisons)
        v10._csv(out/'consolidation_attempts.csv',consolidation_rows)
        v10._csv(out/'dev_case_transitions.csv',dev_cases)
        v10._csv(out/'dev_group_transitions.csv',dev_groups)
        for seed,arm,state,snap,net in snapshots:
            if state=='post_consolidation':
                net=net.to(args.device)
            _checkpoint(torch,out/f'seed{seed}_{arm}_{state}.pt',net,snap,
                        mu,sd,capture,seed,arm,state,data['probe_info']['lineage'])
    (out/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True,
             allow_nan=False)+'\n',encoding='utf-8')
    print('V15_COMPLETE '+probe.canonical({'status':report['status'],
       'mode':args.mode,'gradient_status':geometry['status'],
       'seeds':len(paired),'promotion':False,'report':str(out/'report.json')}),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('study','sweep'),default='sweep')
    for name in ('run','expanded-run','input','source-features','prior',
                 'v11-run','v14-run','output'):
        p.add_argument('--'+name,required=True,type=Path)
    p.add_argument('--device',default='cuda')
    p.add_argument('--focus-group',default=v12.FOCUS)
    p.add_argument('--seeds',type=_seeds,default=[47,49,53])
    p.add_argument('--steps',type=int,default=180)
    p.add_argument('--consolidation-steps',type=int,default=90)
    p.add_argument('--eval-every',type=int,default=15)
    p.add_argument('--lr',type=float,default=.001)
    p.add_argument('--consolidation-lr',type=float,default=.0005)
    p.add_argument('--reserve',type=float,default=.08)
    p.add_argument('--margin-width',type=int,default=32)
    p.add_argument('--max-gradient-cases',type=int,default=24)
    p.add_argument('--trust-radius',type=float,default=.1)
    p.add_argument('--replay-weight',type=float,default=2.)
    p.add_argument('--max-halvings',type=int,default=7)
    p.add_argument('--projection-passes',type=int,default=4)
    p.add_argument('--max-constraints',type=int,default=24)
    return p


def main(argv=None):
    run(parser().parse_args(argv));return 0


if __name__=='__main__':
    raise SystemExit(main())
