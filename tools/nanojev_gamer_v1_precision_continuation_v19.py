#!/usr/bin/env python3
"""V19: census-consistent, adaptive sequential two-margin optimizer.

The verified V14 post-consolidation model is the *only* optimization anchor.
V18's saved disposable checkpoint is independently reloaded and audited as
historical evidence, never overwritten or adopted as the starting state.
All accepted trials use V14/V16's full actual-inference TRAIN retention gate;
DEV and previously seen TEST are excluded from optimization and selection.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from tools import nanojev_gamer_v1_joint_constraint_v18 as v18
from tools import nanojev_gamer_v1_gradient_sign_audit_v17 as v17
from tools import nanojev_gamer_v1_nonlinear_feasibility_v16 as v16
from tools import nanojev_gamer_v1_margin_feasibility_v15 as v15
from tools import nanojev_gamer_v1_margin_consolidation_v13 as v13
from tools import nanojev_gamer_v1_boundary_isolation_v12 as v12
from tools import nanojev_gamer_v1_conflict_projection_v10 as v10
from tools import nanojev_gamer_v1_math_retention_v9 as v9
from tools import nanojev_gamer_v1_protected_math_repair_v7 as v7
from tools import nanojev_gamer_v1_protected_alignment_lab as v6
from tools import nanojev_gamer_v1_joint_head_repair as joint
from tools import nanojev_gamer_v1_head_only_lab as lab
from tools import nanojev_gamer_v1_sign_information_probe as probe

SCHEMA='nanojev.gamer.v1.precision-joint-continuation.v19'
CENSUS_BATCH=128 # Exactly V13's existing 128-wide census policy.


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _census_pair(torch,model,geom,indices):
    """Numerical authority: V13's *full* margin census, not a 2-case batch."""
    margins,_=v13._margin_census(torch,model,geom['encoded'],geom['old'],
                                geom['truth'],geom['required'])
    return [float(margins[i]) for i in indices]


def _census_gradients(torch,model,geom,indices):
    """Differentiation uses exactly the same 128-aligned batch as the census.

    The selected cases are extracted from their full census chunks. No single
    or two-example batch is ever used for the gradient or measured safety.
    """
    model.eval()
    result=[]
    for i in indices:
        start=(int(i)//CENSUS_BATCH)*CENSUS_BATCH
        stop=min(start+CENSUS_BATCH,len(geom['truth']))
        values=v13._class_margins(torch,model,geom['encoded'][start:stop],
                                 geom['old'][start:stop],geom['truth'][start:stop])
        result.append(v17._flat_gradient(torch,model,values[int(i)-start]))
    return result


def _finite_difference(torch,model,geom,state,indices,direction,epsilon,*,
                       significant_floor=.003):
    """Symmetric signed derivative check under the same 128-batch census."""
    norm=float(torch.linalg.vector_norm(direction))
    if not math.isfinite(norm) or norm<1e-14:
        return {'trusted':False,'reason':'ZERO_DIRECTION'}
    unit=direction.detach().cpu().float()/norm
    model.load_state_dict(state,strict=True);model.eval()
    gradients=_census_gradients(torch,model,geom,indices)
    predicted=[float(torch.dot(g,unit)) for g in gradients]
    try:
        v10._set_displacement(torch,model,state,unit,epsilon)
        plus=_census_pair(torch,model,geom,indices)
        v10._set_displacement(torch,model,state,unit,-epsilon)
        minus=_census_pair(torch,model,geom,indices)
    finally:
        model.load_state_dict(state,strict=True);model.eval()
    measured=[(a-b)/(2*epsilon) for a,b in zip(plus,minus)]
    if not all(math.isfinite(x) for x in [*predicted,*measured]):
        return {'trusted':False,'reason':'NONFINITE_DERIVATIVE'}
    # Float32 class logits have a measurable noise floor. Near-zero
    # directional derivatives are explicitly INCONCLUSIVE, not sign errors.
    threshold=max(significant_floor,6e-6/epsilon)
    significant=[abs(x)>threshold for x in predicted]
    mismatch=[j for j in (0,1) if significant[j] and (
        predicted[j]*measured[j]<=0 or abs(measured[j]-predicted[j])>
        max(.003,.35*abs(predicted[j])))]
    return {'trusted':not mismatch and any(significant),
            'reason':'SIGN_OR_SCALE_MISMATCH' if mismatch else
                     'FINITE_DIFFERENCE_INCONCLUSIVE' if not any(significant) else 'DERIVATIVES_AGREE',
            'analytic':predicted,'finite_difference':measured,
            'inconclusive_indices':[j for j,v in enumerate(significant) if not v],
            'disagree_indices':mismatch,'epsilon':float(epsilon),
            'measurement_policy':'V13_CENSUS_128_ALIGNED'}


def _exact_probe(torch,model,geom,spec,mu,sd,device,source,direction,alpha,indices):
    try:
        v10._set_displacement(torch,model,source,direction,alpha)
        assessment,margins,_,correct=v16._actual_evaluation(
              torch,model,geom,spec,mu,sd,device,scope='v19_trial')
        return assessment,[float(margins[i]) for i in indices],correct,v9._copy_state(model)
    finally:
        model.load_state_dict(source,strict=True);model.eval()


def _admissible(assessment,pair,previous,required,min_gain,min_merit,old_merit,acquired_lost=0):
    """Use the exact authority; never trade acquired answers for confidence."""
    if not assessment['retention_pass'] or assessment['protected_lost'] or acquired_lost:
        return False,'HARD_CUMULATIVE_TRAIN_RETENTION_LOSS'
    if not all(math.isfinite(float(p)) for p in pair):
        return False,'NONFINITE_SAFETY'
    for j in (0,1):
        if previous[j]<required[j]-1e-7:
            if pair[j]-previous[j]<min_gain:
                return False,'JOINT_SAFETY_NOT_IMPROVED'
        elif pair[j]<previous[j]-1e-7:
            return False,'SATISFIED_MARGIN_REGRESSED'
    if old_merit-float(assessment['total_deficit'])<min_merit:
        return False,'AGGREGATE_DEFICIT_NOT_IMPROVED'
    return True,'EXACT_CENSUS_JOINT_IMPROVEMENT'


def _search(torch,model,geom,spec,mu,sd,device,*,max_iterations,max_stalls,
            target_step,min_target_step,trust_radius,max_halvings,
            fd_epsilon,min_joint_gain,min_merit_gain,trial_rows,probe_observer=None):
    import numpy as np
    anchor=geom['anchor_weights']
    model.load_state_dict(anchor,strict=True);model.eval()
    indices=geom['deficient']
    if len(indices)!=2:
        raise ValueError('V19_REQUIRES_TWO_VERIFIED_DEFICIENT_CASES')
    required=[float(geom['required'][i]) for i in indices]
    initial,_,_,correct=v16._actual_evaluation(torch,model,geom,spec,mu,sd,device,scope='anchor')
    if not initial['retention_pass'] or not np.array_equal(correct,geom['mask']):
        raise ValueError('V19_INVALID_V14_ANCHOR')
    pair=_census_pair(torch,model,geom,indices)
    # Measurement policy invariant: exact acceptance census == standalone
    # authoritative census, to within a realistic float32 repeat tolerance.
    assessment,margin_values,_,_=v16._actual_evaluation(torch,model,geom,spec,mu,sd,
                                             device,scope='measurement_consistency')
    if max(abs(pair[j]-float(margin_values[indices[j]])) for j in (0,1))>1e-6:
        raise ValueError('V19_AUTHORITY_MARGIN_MISMATCH')
    baseline_pair=pair.copy();baseline_merit=float(initial['total_deficit'])
    current=baseline_merit;state=v9._copy_state(model)
    acquired_mask=correct.copy()
    accepted=0;stalls=0;iterations=[];derivative_checks=[]
    active_target=float(target_step)
    for iteration in range(1,max_iterations+1):
        model.load_state_dict(state,strict=True);model.eval()
        pair=_census_pair(torch,model,geom,indices)
        deficits=[max(0.,r-p) for r,p in zip(required,pair)]
        if max(deficits)<=1e-6:
            break
        grads=_census_gradients(torch,model,geom,indices)
        # Every direction is a joint-constrained QP, with a small set of
        # explicit different allocations to avoid the cancellation basin.
        directions=[]
        for name,ratio in [('balanced',(1.,1.)),('favor_a',(1.,.25)),
                           ('favor_b',(.25,1.))]:
            needs=[min(deficits[j],active_target*ratio[j]) for j in (0,1)]
            direction,detail=v18._joint_min_norm(torch,grads[0],grads[1],needs,trust_radius)
            directions.append((name,direction,detail,needs))
        proposals=[]
        for name,direction,detail,needs in directions:
            if direction is None:
                derivative_checks.append({'iteration':iteration,'direction':name,
                      'trusted':False,'reason':detail['status']})
                continue
            checked=_finite_difference(torch,model,geom,state,indices,direction,fd_epsilon)
            derivative_checks.append({'iteration':iteration,'direction':name,**checked})
            if not checked['trusted']:
                trial_rows.append({'iteration':iteration,'direction':name,
                     'decision':'DERIVATIVE_NOT_TRUSTED','fd_reason':checked['reason']})
                continue
            for h in range(max_halvings+1):
                alpha=2.**(-h)
                result,after,correct_trial,candidate=_exact_probe(torch,model,geom,spec,mu,sd,device,
                                             state,direction,alpha,indices)
                acquired_lost=int((acquired_mask & ~correct_trial).sum())
                permissible,reason=_admissible(result,after,pair,required,
                      min_joint_gain,min_merit_gain,current,acquired_lost)
                gain=current-float(result['total_deficit'])
                trial_rows.append({'iteration':iteration,'direction':name,'halvings':h,
                  'alpha':alpha,'target_step':active_target,
                  'distance':float(torch.linalg.vector_norm(direction))*alpha,
                  'before_a':pair[0],'before_b':pair[1],
                  'after_a':after[0],'after_b':after[1],
                  'gain_a':after[0]-pair[0],'gain_b':after[1]-pair[1],
                  'merit_before':current,'merit_after':float(result['total_deficit']),
                  'merit_gain':gain,'retention_pass':result['retention_pass'],
                  'protected_lost':result['protected_lost'],
                  'acquired_lost':acquired_lost,
                  'correct_train':result['correct_train'],
                  'qp_condition':detail.get('condition_number'),
                  'decision':reason,'admitted':bool(permissible)})
                # Optional read-only instrumentation for V20.  The default
                # V19 path performs no extra evaluation or state mutation.
                if probe_observer is not None:
                    probe_observer(iteration=iteration,direction_name=name,
                        halvings=h,alpha=alpha,model=model,geometry=geom,
                        state=state,candidate=candidate,direction=direction,
                        indices=indices,before_pair=pair,after_pair=after,
                        current_merit=current,evaluation=result,
                        decision=reason,admitted=bool(permissible),
                        correct_trial=correct_trial)
                if permissible:
                    proposals.append((float(result['total_deficit']),name,h,after,candidate,result))
        if not proposals:
            stalls+=1
            iterations.append({'iteration':iteration,'status':'NO_ACCEPTABLE_STEP',
               'target_step':active_target,'stalls':stalls,'deficit':current,
               'safety_a':pair[0],'safety_b':pair[1]})
            active_target/=4.
            if stalls>=max_stalls or active_target<min_target_step:
                break
            continue
        new_merit,name,halvings,after,candidate,evaluation=min(
                       proposals,key=lambda p:(p[0],p[2],p[1]))
        # Revalidate after a fresh strict restoration of the winning state.
        model.load_state_dict(candidate,strict=True);model.eval()
        verified,margin_values,_,correct=v16._actual_evaluation(
                          torch,model,geom,spec,mu,sd,device,scope='precommit')
        verified_pair=[float(margin_values[i]) for i in indices]
        stale_lost=int((acquired_mask & ~correct).sum())
        stable,reason=_admissible(verified,verified_pair,pair,required,
                                   min_joint_gain,min_merit_gain,current,stale_lost)
        if not stable or not np.array_equal(correct & geom['mask'],geom['mask']):
            raise ValueError('V19_PRECOMMIT_VERIFICATION_FAILED_'+reason)
        if max(abs(verified_pair[j]-after[j]) for j in (0,1))>1e-6:
            raise ValueError('V19_CENSUS_REPEAT_NOT_STABLE')
        state=v9._copy_state(model);current=float(verified['total_deficit']);accepted+=1
        acquired_mask=acquired_mask | correct
        stalls=0
        # Set the NEXT desired increase near the scale that actually passed.
        # This avoids repeating V18's 0.0005 request when 0.000004 survived.
        realized=min(max(0.,verified_pair[j]-pair[j]) for j in (0,1))
        active_target=min(target_step,max(min_target_step,4.*realized))
        iterations.append({'iteration':iteration,'status':'COMMITTED_VERIFIED_STEP',
           'direction':name,'halvings':halvings,'target_step_next':active_target,
           'realized_min_gain':realized,'deficit':current,
           'safety_a':verified_pair[0],'safety_b':verified_pair[1]})
        print('V19_PROGRESS '+probe.canonical({'iteration':iteration,'accepted':accepted,
              'deficit':current,'safety_a':verified_pair[0],
              'safety_b':verified_pair[1],'next_target_step':active_target}),flush=True)
    model.load_state_dict(state,strict=True);model.eval()
    result,margins,_,correct=v16._actual_evaluation(torch,model,geom,spec,mu,sd,device,scope='final')
    final_pair=[float(margins[i]) for i in indices]
    if (not result['retention_pass'] or not np.array_equal(correct & geom['mask'],geom['mask'])
        or bool((acquired_mask & ~correct).any())):
        raise ValueError('V19_FINAL_RETENTION_FAILED')
    if abs(float(result['total_deficit'])-current)>1e-6:
        raise ValueError('V19_FINAL_MERIT_NOT_REPEATABLE')
    jointly_improved=all(final_pair[j]-baseline_pair[j]>=min_joint_gain for j in (0,1))
    if accepted and not jointly_improved:
        raise ValueError('V19_ACCEPTED_BUT_NOT_JOINTLY_IMPROVED')
    summary={'status':'JOINT_TARGET_MET' if result['all_protected_reserves_met'] else
             'JOINT_PARTIAL_IMPROVEMENT' if jointly_improved else 'NO_JOINT_STEP_FOUND',
       'initial_deficit':baseline_merit,'final_deficit':current,
       'baseline_safety':baseline_pair,'final_safety':final_pair,
       'accepted_iterations':accepted,'rejected_intervals':stalls,
       'trial_count':len(trial_rows),'joint_improved':bool(jointly_improved),
       'all_protected_reserves_met':bool(result['all_protected_reserves_met']),
       'baseline_correct_train':int(initial['correct_train']),
       'final_correct_train':int(result['correct_train']),
       'final_acquired_protected_train':int(acquired_mask.sum()),
       'hard_full_train_retention':True,'measurement_policy':'V13_CENSUS_128_ALIGNED',
       'iterations':iterations,'derivative_checks':derivative_checks}
    model.load_state_dict(anchor,strict=True);model.eval()
    return summary,state


def _verify_v18(torch,root,sha17,verified14,capture,data,prior_sha,focus,reserve,
                model,geom,spec,mu,sd,device):
    """Audit the historical v18 checkpoint; *never* use it as train anchor."""
    import numpy as np
    report_path=Path(root)/'report.json'
    checkpoint_path=Path(root)/'joint_margin_disposable_candidate.pt'
    if not report_path.is_file() or not checkpoint_path.is_file():
        raise ValueError('V19_V18_EVIDENCE_MISSING')
    r=json.loads(report_path.read_text(encoding='utf-8'))
    if (r.get('schema')!=v18.SCHEMA or r.get('status')!='EXPERIMENT_COMPLETE_NO_PROMOTION'
        or r.get('checkpoint_saved') is not True
        or r.get('promotion_authorized') is not False
        or r.get('production_weights_modified') is not False
        or r.get('general_acquisition_training') is not False
        or r.get('frozen_v11_specialist') is not True
        or r.get('TRAIN_only_gradients_and_candidate_admission') is not True
        or r.get('synthetic_dev_used_for_training_or_selection') is not False
        or r.get('previously_seen_test_used_for_training_or_selection') is not False
        or r.get('hard_train_retention')!='UNCHANGED_V14_FULL_TRAIN_EXACT_INFERENCE'
        or r.get('v17_report_sha256')!=sha17 or r.get('v14_verified')!=verified14
        or r.get('training_capture_sha256')!=capture
        or r.get('frozen_lineage')!=data['probe_info']['lineage']
        or r.get('v6_prior_sha256')!=prior_sha or r.get('focus_group')!=focus
        or abs(float(r.get('config',{}).get('reserve',-1))-reserve)>1e-9
        or r.get('summary',{}).get('hard_full_train_retention') is not True
        or r.get('summary',{}).get('joint_improved') is not True):
        raise ValueError('V19_V18_PROVENANCE_MISMATCH')
    cp=torch.load(checkpoint_path,map_location='cpu',weights_only=True)
    if (cp.get('schema')!=v18.SCHEMA or cp.get('status')!='DISPOSABLE_NON_PRODUCTION'
        or cp.get('promotion_authorized') is not False
        or cp.get('hard_full_train_retention') is not True
        or cp.get('training_capture_sha256')!=capture
        or cp.get('v14_post_sha256')!=verified14['v14_post_consolidation_sha256']
        or cp.get('v17_report_sha256')!=sha17
        or cp.get('frozen_lineage')!=data['probe_info']['lineage']
        or not np.array_equal(cp['train_ledger_mask'].numpy(),geom['mask'])):
        raise ValueError('V19_V18_CHECKPOINT_PROVENANCE_MISMATCH')
    # V18's saved ledger was verified against the V14 baseline above.  The
    # cumulative mask constructed later by _search is not in scope during
    # historical verification; the independently verified checkpoint mask is
    # the authority for this read-only audit.
    evidence_mask=cp['train_ledger_mask'].detach().cpu().numpy().astype(bool)
    before=v9._copy_state(model)
    try:
        model.load_state_dict(cp['weights'],strict=True);model.eval()
        result,margins,_,correct=v16._actual_evaluation(torch,model,geom,spec,mu,sd,
                                                 device,scope='v18_evidence')
        if (not result['retention_pass'] or
            not np.all(np.asarray(correct,dtype=bool)[evidence_mask])):
            raise ValueError('V19_V18_HARD_RETENTION_MISMATCH')
        if abs(result['total_deficit']-float(r['summary']['best_deficit']))>2e-6:
            raise ValueError('V19_V18_DEFICIT_MISMATCH')
        pair=[float(margins[i]) for i in geom['deficient']]
        if max(abs(pair[j]-float(r['summary']['final_safety'][j])) for j in (0,1))>2e-6:
            raise ValueError('V19_V18_MARGIN_MISMATCH')
    finally:
        model.load_state_dict(before,strict=True);model.eval()
    return {'v18_report_sha256':_sha(report_path),
            'v18_checkpoint_sha256':_sha(checkpoint_path),
            'v18_deficit':result['total_deficit'],
            'v18_safety':pair,'v18_retention_verified':True}


def _validate(args,torch):
    values=(args.reserve,args.target_step,args.min_target_step,args.trust_radius,
           args.fd_epsilon,args.min_joint_gain,args.min_merit_gain)
    if (args.mode!='solve' or not all(math.isfinite(v) for v in values)
        or not 0<args.reserve<.5 or not 0<args.min_target_step<=args.target_step<=.08
        or not 0<args.min_joint_gain<args.min_target_step
        or not 0<args.min_merit_gain<=args.min_joint_gain*2
        or not 0<args.trust_radius<=1 or not 0<args.fd_epsilon<=.1
        or not 1<=args.max_iterations<=100 or not 1<=args.max_stalls<=12
        or not 1<=args.max_halvings<=14 or not 6<=args.max_gradient_cases<=64):
        raise ValueError('V19_INVALID_CONFIGURATION')
    if str(args.device).startswith('cuda') and not torch.cuda.is_available():
        raise ValueError('V19_CUDA_NOT_AVAILABLE')


def run(args):
    import numpy as np
    import torch
    _validate(args,torch)
    inputs=[args.run,args.expanded_run,args.input,args.source_features,args.prior,
            args.v11_run,args.v14_run,args.v15_run,args.v16_run,args.v17_run,args.v18_run]
    out=lab._output(args.output,[Path(v) for v in inputs])
    if out.exists():raise FileExistsError(out)
    data,prepared=joint._prepare(args)
    spec,_=v6._routed_profiles(data,prepared)
    _,capture=v9._groups_and_fingerprints(spec)
    _,mu,sd,_=v7._read_v6_checkpoint(torch,args.prior,data,spec)
    _,cp11,_,proof=v12._verify_v11(torch,args.v11_run,data,capture,
                                  _sha(args.prior),spec,args.device)
    if not (np.array_equal(cp11['selected']['mean'].numpy(),mu)
            and np.array_equal(cp11['selected']['std'].numpy(),sd)):
        raise ValueError('V19_NORMALIZER_MISMATCH')
    report14,post,model,verified14=v15._check_prior14(args.v14_run,proof,capture,
             _sha(args.prior),data,args.focus_group,torch,spec,mu,sd,args.device)
    if (abs(args.reserve-report14['config']['reserve'])>1e-9
        or report14['config']['margin_width']!=post['margin_width']):
        raise ValueError('V19_V14_RESERVE_OR_WIDTH_MISMATCH')
    _,sha15=v16._checked_prior15(args.v15_run,verified14,capture,
                            _sha(args.prior),args.focus_group,data,args.reserve)
    _,sha16=v17._checked_v16(args.v16_run,sha15,capture,verified14,data,
                            _sha(args.prior),args.focus_group,args.reserve)
    _,sha17=v18._checked_v17(args.v17_run,sha16,capture,verified14,data,
                            _sha(args.prior),args.focus_group,args.reserve)
    prompt_map=v12._deck_prompts(data)
    geom=v16._geometry(torch,model,post,spec,mu,sd,args.device,args.focus_group,
                       args.reserve,args.max_gradient_cases,prompt_map)
    if not all(not p.requires_grad for p in model.base.parameters()):
        raise ValueError('V19_FROZEN_V11_SPECIALIST_UNFROZEN')
    evidence=_verify_v18(torch,args.v18_run,sha17,verified14,capture,data,
                         _sha(args.prior),args.focus_group,args.reserve,model,
                         geom,spec,mu,sd,args.device)
    anchor=v9._copy_state(model)
    trials=[]
    summary,best=_search(torch,model,geom,spec,mu,sd,args.device,
         max_iterations=args.max_iterations,max_stalls=args.max_stalls,
         target_step=args.target_step,min_target_step=args.min_target_step,
         trust_radius=args.trust_radius,max_halvings=args.max_halvings,
         fd_epsilon=args.fd_epsilon,min_joint_gain=args.min_joint_gain,
         min_merit_gain=args.min_merit_gain,trial_rows=trials)
    if any(not torch.equal(model.state_dict()[k].cpu(),p.cpu()) for k,p in anchor.items()):
        raise ValueError('V19_V14_SOURCE_WEIGHTS_CHANGED')
    if not np.array_equal(v9._correct_train(torch,model,spec,mu,sd,args.device),geom['mask']):
        raise ValueError('V19_V14_SOURCE_LEDGER_CHANGED')
    save=bool(summary['joint_improved'] and summary['accepted_iterations']>0)
    final_mask=None
    if save:
        model.load_state_dict(best,strict=True);model.eval()
        actual,margins,_,final_mask=v16._actual_evaluation(torch,model,geom,spec,mu,sd,
                                                       args.device,scope='checkpoint')
        if not actual['retention_pass'] or abs(actual['total_deficit']-summary['final_deficit'])>1e-6:
            raise ValueError('V19_CHECKPOINT_REVALIDATION_FAILED')
        original,_=v6._evaluate(torch,model,spec,mu,sd,args.device)
        if original['status']!='PASS':
            raise ValueError('V19_ORIGINAL_RETENTION_FAILED')
        model.load_state_dict(anchor,strict=True);model.eval()
    report={'schema':SCHEMA,'status':'EXPERIMENT_COMPLETE_NO_PROMOTION',
       'mode':'solve','algorithm':'CENSUS_ALIGNED_ADAPTIVE_TWO_MARGIN_QP',
       'outcome':summary['status'],'summary':summary,
       'config':{'reserve':args.reserve,'max_iterations':args.max_iterations,
                 'max_stalls':args.max_stalls,'target_step':args.target_step,
                 'min_target_step':args.min_target_step,
                 'trust_radius':args.trust_radius,'max_halvings':args.max_halvings,
                 'fd_epsilon':args.fd_epsilon,'min_joint_gain':args.min_joint_gain,
                 'min_merit_gain':args.min_merit_gain,
                 'max_gradient_cases':args.max_gradient_cases},
       'reference_v18':evidence,'checkpoint_saved':save,
       'source_anchor':'VERIFIED_V14_POST_CONSOLIDATION_NOT_V18',
       'measurement_authority':'V13_FULL_TRAIN_128_CASE_CENSUS',
       'hard_train_retention':'UNCHANGED_V14_FULL_TRAIN_EXACT_INFERENCE',
       'original_task_bypass':'UNCHANGED_V6',
       'frozen_v11_specialist':True,'no_new_trainable_capacity':True,
       'general_acquisition_training':False,'focus_group':args.focus_group,
       'baseline_protected_train':int(geom['mask'].sum()),
       'deficient_cases':[geom['rows'][i]['id'] for i in geom['deficient']],
       'training_capture_sha256':capture,
       'frozen_lineage':data['probe_info']['lineage'],
       'v6_prior_sha256':_sha(args.prior),
       'v11_verified':proof,'v14_verified':verified14,
       'v15_report_sha256':sha15,'v16_report_sha256':sha16,
       'v17_report_sha256':sha17,
       'synthetic_dev_used_for_training_or_selection':False,
       'previously_seen_test_used_for_training_or_selection':False,
       'production_weights_modified':False,'promotion_authorized':False,
       'general_production_router':'NOT_CERTIFIED'}
    if (_sha(Path(args.v18_run)/'report.json')!=evidence['v18_report_sha256']
        or _sha(Path(args.v18_run)/'joint_margin_disposable_candidate.pt')!=evidence['v18_checkpoint_sha256']):
        raise ValueError('V19_V18_EVIDENCE_CHANGED_DURING_RUN')
    joint._verify_after(data)
    out.mkdir(parents=True,exist_ok=False)
    v10._csv(out/'precision_joint_trials.csv',trials or [{'decision':'NO_TRIALS'}])
    v10._csv(out/'precision_iteration_summary.csv',summary['iterations'] or [{'status':summary['status']}])
    v10._csv(out/'precision_derivative_checks.csv',summary['derivative_checks'] or [{'reason':'NO_GRADIENT_CHECKS'}])
    if save:
        torch.save({'schema':SCHEMA,'status':'DISPOSABLE_NON_PRODUCTION',
          'weights':best,'mean':torch.as_tensor(mu),'std':torch.as_tensor(sd),
          'training_capture_sha256':capture,
          'v14_post_sha256':verified14['v14_post_consolidation_sha256'],
          'v18_checkpoint_sha256':evidence['v18_checkpoint_sha256'],
          'frozen_lineage':data['probe_info']['lineage'],
          'margin_width':report14['config']['margin_width'],
          'capacity_width':report14['config']['capacity_width'],
          'train_ledger_mask':torch.as_tensor(final_mask),
          'hard_full_train_retention':True,
          'protected_original_passthrough':True,'promotion_authorized':False},
          out/'precision_joint_disposable_candidate.pt')
    (out/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True,
                                            allow_nan=False)+'\n',encoding='utf-8')
    print('V19_COMPLETE '+probe.canonical({'status':summary['status'],
          'accepted':summary['accepted_iterations'],'joint_improved':summary['joint_improved'],
          'initial_deficit':summary['initial_deficit'],
          'final_deficit':summary['final_deficit'],
          'checkpoint_saved':save,'promotion':False,'report':str(out/'report.json')}),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('solve',),default='solve')
    for name in ('run','expanded-run','input','source-features','prior','v11-run',
                 'v14-run','v15-run','v16-run','v17-run','v18-run','output'):
        p.add_argument('--'+name,required=True,type=Path)
    p.add_argument('--device',default='cuda')
    p.add_argument('--focus-group',default=v12.FOCUS)
    p.add_argument('--reserve',type=float,default=.08)
    p.add_argument('--max-gradient-cases',type=int,default=24)
    p.add_argument('--max-iterations',type=int,default=18)
    p.add_argument('--max-stalls',type=int,default=4)
    p.add_argument('--target-step',type=float,default=.0005)
    p.add_argument('--min-target-step',type=float,default=1e-6)
    p.add_argument('--trust-radius',type=float,default=.05)
    p.add_argument('--max-halvings',type=int,default=10)
    p.add_argument('--fd-epsilon',type=float,default=.002)
    p.add_argument('--min-joint-gain',type=float,default=2e-7)
    p.add_argument('--min-merit-gain',type=float,default=3e-7)
    return p


def main(argv=None):
    run(parser().parse_args(argv));return 0


if __name__=='__main__':
    raise SystemExit(main())
