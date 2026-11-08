#!/usr/bin/env python3
"""V18: sequential exact-verified joint margin constraints, without new capacity.

Reconstruct and verify V11/V14/V15/V16/V17 lineage. The ONLY movable weights
are V14's existing feature-conditioned margin adapter; its V11 specialist and
V6 original-game branch remain frozen. Optimize the two documented TRAIN-only
near-zero signed-game cases jointly via minimum-norm two-halfspace quadratic
steps. Recalculate actual margins and gradients after every accepted step.

Every intermediate and final proposal must pass full actual-inference TRAIN
retention. An accepted checkpoint must also improve BOTH targeted safety margins
relative to the prior committed checkpoint. When direct QP stalls, a bounded
TWO-STEP path may temporarily trade margin, but never sacrifice correct signs;
only its exact-verified jointly improved endpoint can be committed. All source
checkpoints are restored; DEV and previously examined TEST are not used for
optimization or selection. No production promotion is authorized.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

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

SCHEMA='nanojev.gamer.v1.sequential-joint-constraint.v18'


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _checked_v17(folder, sha16, capture, verified14, data, prior_sha, focus, reserve):
    path=Path(folder)/'report.json'
    if not path.is_file():
        raise ValueError('V18_V17_REPORT_MISSING')
    r=json.loads(path.read_text(encoding='utf-8'))
    if (r.get('schema')!=v17.SCHEMA or r.get('status')!='DIAGNOSTIC_COMPLETE_NO_PROMOTION'
        or r.get('mode')!='audit' or r.get('read_only') is not True
        or r.get('checkpoint_saved') is not False or r.get('promotion_authorized') is not False
        or r.get('production_weights_modified') is not False
        or r.get('general_acquisition_training') is not False
        or r.get('TRAIN_only_gradients_and_probes') is not True
        or r.get('hard_train_retention_unchanged') is not True
        or r.get('synthetic_dev_used_for_training_or_selection') is not False
        or r.get('previously_seen_test_used_for_training_or_selection') is not False
        or r.get('v16_report_sha256')!=sha16 or r.get('training_capture_sha256')!=capture
        or r.get('v14_verified')!=verified14
        or r.get('frozen_lineage')!=data['probe_info']['lineage']
        or r.get('v6_prior_sha256')!=prior_sha or r.get('focus_group')!=focus
        or abs(float(r.get('config',{}).get('reserve',-1))-reserve)>1e-9
        or r.get('baseline_protected_train',0)<1
        or r.get('decision',{}).get('sign_error_proven') is not False):
        raise ValueError('V18_V17_PROVENANCE_MISMATCH')
    return r,_sha(path)


def _joint_min_norm(torch, grad_a, grad_b, needs, radius):
    """Analytic 2-halfspace min-norm QP; active-set enumerated in float64.

    min 1/2 ||d||^2 subject to g_A.d>=need_A and g_B.d>=need_B.
    If exact linear solution exceeds radius, scale it inside the local trust
    region. That scaled result is a PROPOSAL, not a feasibility certificate.
    """
    import numpy as np
    if len(needs)!=2 or not 0<radius<=10 or any(not math.isfinite(float(n)) or float(n)<0 for n in needs):
        raise ValueError('V18_BAD_JOINT_CONSTRAINTS')
    grads=[grad_a.detach().cpu().double(),grad_b.detach().cpu().double()]
    if grads[0].shape!=grads[1].shape or grads[0].ndim!=1 or not all(bool(torch.isfinite(g).all()) for g in grads):
        raise ValueError('V18_BAD_MARGIN_GRADIENTS')
    G=np.stack([g.numpy() for g in grads]); b=np.asarray(needs,dtype=np.float64)
    gram=G@G.T
    candidates=[]
    if np.max(b)<1e-15:
        return torch.zeros_like(grad_a,dtype=torch.float32),{'status':'RESERVES_ALREADY_MET','condition_number':None,'min_norm':0.,'requested':b.tolist(),'predicted':[0.,0.],'scaled_to_trust':False}
    for k in (0,1):
        if gram[k,k]>1e-20:
            c=np.zeros(2);c[k]=b[k]/gram[k,k]
            if np.min(gram@c-b)>=-max(1e-9,1e-6*max(b)):
                candidates.append((float(c@gram@c),c,'SINGLE_ACTIVE'))
    condition=float(np.linalg.cond(gram)) if gram.max()>0 else float('inf')
    if np.linalg.det(gram)>1e-20:
        c=np.linalg.solve(gram,b)
        if np.min(c)>=-1e-9 and np.min(gram@c-b)>=-max(1e-9,1e-6*max(b)):
            candidates.append((float(c@gram@c),c,'TWO_ACTIVE'))
    if not candidates:
        return None,{'status':'DEGENERATE_OR_INCONSISTENT_LOCAL_QP','condition_number':condition,'requested':b.tolist()}
    _,c,active=min(candidates,key=lambda item:item[0])
    direction=G.T@c
    length=float(np.linalg.norm(direction))
    scaled=length>radius
    if scaled:
        direction=direction*(radius/length)
    actual=G@direction
    if np.min(actual)<-1e-9 or not np.isfinite(direction).all():
        raise ValueError('V18_INVALID_QP_DIRECTION')
    return torch.from_numpy(direction.astype(np.float32)),{
        'status':'LOCAL_QP_TRUST_SCALED' if scaled else 'LOCAL_QP_WITHIN_TRUST',
        'active_set':active,'condition_number':condition,'min_norm':length,
        'requested':b.tolist(),'predicted':actual.tolist(),
        'scaled_to_trust':bool(scaled)}


def _two_margins(torch,model,geom,indices):
    model.eval()
    with torch.no_grad():
        value=v13._class_margins(torch,model,geom['encoded'][indices],
                      geom['old'][indices],geom['truth'][indices])
    return [float(x) for x in value.detach().cpu().tolist()]


def _two_gradients(torch,model,geom,indices):
    """Autograd at exact CURRENT state, in V10 full-parameter order."""
    model.eval()
    return [v17._flat_gradient(torch,model,v13._class_margins(
                torch,model,geom['encoded'][i:i+1],geom['old'][i:i+1],
                geom['truth'][i:i+1]).sum()) for i in indices]


def _signed_direction_check(torch,model,geom,state,indices,direction,epsilon):
    """Verify local directional sign, without trusting tiny float32 deltas."""
    length=float(torch.linalg.vector_norm(direction))
    if not math.isfinite(length) or length<1e-14:
        return {'trusted':False,'reason':'ZERO_DIRECTION'}
    unit=direction.detach().cpu().float()/length
    model.load_state_dict(state,strict=True)
    gradients=_two_gradients(torch,model,geom,indices)
    predicted=[float(torch.dot(g,unit)) for g in gradients]
    observed=[]
    try:
        for sign in (1,-1):
            v10._set_displacement(torch,model,state,unit,sign*epsilon)
            observed.append(_two_margins(torch,model,geom,indices))
    finally:
        model.load_state_dict(state,strict=True);model.eval()
    measured=[(a-b)/(2*epsilon) for a,b in zip(observed[0],observed[1])]
    if not all(math.isfinite(x) for x in [*measured,*predicted]):
        return {'trusted':False,'reason':'NONFINITE_FINITE_DIFFERENCE'}
    # A significant derivative must have the same sign as the actual central
    # difference. Smaller derivatives are labeled inconclusive rather than bad.
    significant=[abs(p)>max(.003,6e-6/epsilon) for p in predicted]
    bad=[k for k in (0,1) if significant[k] and
         (predicted[k]*measured[k]<=0 or abs(measured[k]-predicted[k])>
          max(.003,.35*abs(predicted[k])))]
    uncertain=not any(significant)
    return {'trusted':not bool(bad) and not uncertain,
        'reason':'FINITE_DIFFERENCE_INCONCLUSIVE' if uncertain else
          'DERIVATIVES_AGREE' if not bad else 'SIGN_OR_SCALE_MISMATCH',
        'analytic':predicted,'finite_difference':measured,
        'inconclusive_indices':[i for i,flag in enumerate(significant) if not flag],
        'disagree_indices':bad,'epsilon':float(epsilon)}


def _exact_trial(torch,model,geom,spec,mu,sd,device,base_state,direction,alpha,indices,scope):
    try:
        v10._set_displacement(torch,model,base_state,direction,alpha)
        result,margins,deficits,correct=v16._actual_evaluation(
              torch,model,geom,spec,mu,sd,device,scope=scope)
        return result,[float(margins[i]) for i in indices],correct,v9._copy_state(model)
    finally:
        model.load_state_dict(base_state,strict=True);model.eval()


def _candidate_ok(result,pair,previous,required,min_gain):
    if not result['retention_pass'] or result['protected_lost']:
        return False,'RETENTION_LOSS'
    # All still-deficient cases must improve together, not merely improve
    # the sum by sacrificing confidence in the other mathematical sign.
    for i in (0,1):
        if previous[i]<required[i]-1e-7 and pair[i]<previous[i]+min_gain:
            return False,'JOINT_MARGIN_NOT_IMPROVED'
        if previous[i]>=required[i]-1e-7 and pair[i]<previous[i]-1e-6:
            return False,'SATISFIED_RESERVE_REGRESSED'
    return True,'BOTH_MARGIN_CONSTRAINTS_IMPROVED'


def _sequential_search(torch,model,geom,spec,mu,sd,device,*,max_iterations,
                       target_step,trust_radius,max_halvings,fd_epsilon,
                       min_joint_gain,path_step,enable_path,trial_rows):
    """Exact, hard-TRAIN gated, locally relinearized two-constraint search.

    `execute_exact` and `compute_gradients` are indirect only through model,
    allowing the actual V14 implementation to remain the sole authority.
    """
    import numpy as np
    anchor=geom['anchor_weights'];model.load_state_dict(anchor,strict=True);model.eval()
    indices=geom['deficient']
    if len(indices)!=2:
        return {'status':'NOT_EXACTLY_TWO_DEFICIENT_CASES',
            'deficient_count':len(indices),'accepted_iterations':0,
            'initial_deficit':geom['initial_deficit'],'best_deficit':geom['initial_deficit'],
            'joint_improved':False,'trial_count':0,'line_checks':[],
            'iterations':[],'baseline_safety':[],'final_safety':[],
            'baseline_correct_train':int(geom['mask'].sum()),
            'final_correct_train':int(geom['mask'].sum()),
            'path_commits':0,'hard_full_train_retention':True,
            'all_protected_reserves_met':geom['initial_deficit']<=1e-6},v9._copy_state(model)
    required=[float(geom['required'][i]) for i in indices]
    initial_pair=_two_margins(torch,model,geom,indices)
    baseline,_,_,base_correct=v16._actual_evaluation(torch,model,geom,spec,mu,sd,device,scope='baseline')
    if not baseline['retention_pass'] or not np.array_equal(base_correct,geom['mask']):
        raise ValueError('V18_INVALID_ANCHOR_LEDGER')
    current=baseline['total_deficit'];best=v9._copy_state(model)
    accepted=0;path_commits=0;line_checks=[];iteration_rows=[]
    for iteration in range(1,max_iterations+1):
        model.load_state_dict(best,strict=True);model.eval()
        pair=_two_margins(torch,model,geom,indices)
        deficits=[max(0.,r-p) for r,p in zip(required,pair)]
        if max(deficits)<=1e-6:
            break
        gradients=_two_gradients(torch,model,geom,indices)
        needs=[min(target_step,d) for d in deficits]
        direction,diagnostic=_joint_min_norm(torch,gradients[0],gradients[1],needs,trust_radius)
        if direction is None:
            check={'trusted':False,'reason':'QP_INCONCLUSIVE'}
        else:
            check=_signed_direction_check(torch,model,geom,best,indices,direction,fd_epsilon)
        line_checks.append({'iteration':iteration,'path':'direct_qp',**check})
        model.load_state_dict(best,strict=True)
        proposals=[]
        if direction is not None and check['trusted'] and float(torch.linalg.vector_norm(direction))>1e-14:
            for h in range(max_halvings+1):
                alpha=2.**(-h)
                actual,after,correct,weights=_exact_trial(torch,model,geom,spec,mu,sd,
                               device,best,direction,alpha,indices,'direct_qp')
                permitted,reason=_candidate_ok(actual,after,pair,required,min_joint_gain)
                gain=current-actual['total_deficit']
                if permitted and gain<=1e-7:
                    permitted=False;reason='NO_AGGREGATE_IMPROVEMENT'
                trial_rows.append({'iteration':iteration,'stage':'direct_qp','direction':'two_halfspace_min_norm',
                  'halvings':h,'alpha':alpha,'distance':float(torch.linalg.vector_norm(direction))*alpha,
                  'qp_condition':diagnostic.get('condition_number'),
                  'qp_requested_a':needs[0],'qp_requested_b':needs[1],
                  'before_a':pair[0],'before_b':pair[1],'after_a':after[0],'after_b':after[1],
                  'joint_improved':permitted,'merit_gain':gain,'retention_pass':actual['retention_pass'],
                  'protected_lost':actual['protected_lost'],'decision':reason})
                if permitted:
                    proposals.append((actual['total_deficit'],weights,after,'direct_qp'))
        else:
            trial_rows.append({'iteration':iteration,'stage':'gradient_check',
                'direction':'two_halfspace_min_norm','decision':check['reason'],'joint_improved':False})
        if not proposals and enable_path:
            # Move through an exact retention-safe intermediate state. The
            # final endpoint MUST improve both original margins. Atomic commit
            # of the two-step path; intermediate is never itself a checkpoint.
            for case_idx,grad in enumerate(gradients):
                norm=float(torch.linalg.vector_norm(grad))
                if norm<1e-14:continue
                for sign in (1,-1):
                    seed=grad*(sign*path_step/norm)
                    intermediate,midpair,_,midstate=_exact_trial(torch,model,geom,spec,mu,sd,
                                      device,best,seed,1.,indices,'path_intermediate')
                    trial_rows.append({'iteration':iteration,'stage':'path_intermediate',
                        'direction':f'case_{case_idx}_{sign}','distance':path_step,
                        'before_a':pair[0],'before_b':pair[1],
                        'after_a':midpair[0],'after_b':midpair[1],
                        'retention_pass':intermediate['retention_pass'],
                        'protected_lost':intermediate['protected_lost'],
                        'merit_gain':current-intermediate['total_deficit'],
                        'joint_improved':False,'decision':'INTERMEDIATE_ONLY_NO_COMMIT'})
                    if not intermediate['retention_pass']:continue
                    model.load_state_dict(midstate,strict=True);model.eval()
                    grads_mid=_two_gradients(torch,model,geom,indices)
                    need_mid=[max(0.,min(target_step,max(0.,r-pair[i]))+(pair[i]-midpair[i]))
                              for i,r in enumerate(required)]
                    direction_mid,diag_mid=_joint_min_norm(torch,grads_mid[0],grads_mid[1],need_mid,
                                                            trust_radius)
                    if direction_mid is None:
                        model.load_state_dict(best,strict=True);continue
                    check_mid=_signed_direction_check(torch,model,geom,midstate,indices,direction_mid,
                                                       fd_epsilon)
                    line_checks.append({'iteration':iteration,'path':f'case_{case_idx}_{sign}',**check_mid})
                    if check_mid['trusted'] and float(torch.linalg.vector_norm(direction_mid))>1e-14:
                        for h in range(max_halvings+1):
                            alpha=2.**(-h)
                            actual,after,_,candidate=_exact_trial(torch,model,geom,spec,mu,sd,
                                  device,midstate,direction_mid,alpha,indices,'path_final')
                            permitted,reason=_candidate_ok(actual,after,pair,required,min_joint_gain)
                            gain=current-actual['total_deficit']
                            total_vector=seed+direction_mid*alpha
                            distance=float(torch.linalg.vector_norm(total_vector))
                            if distance>trust_radius*(1+1e-6):
                                permitted=False;reason='TRUST_RADIUS_EXCEEDED'
                            if permitted and gain<=1e-7:
                                permitted=False;reason='NO_AGGREGATE_IMPROVEMENT'
                            trial_rows.append({'iteration':iteration,'stage':'path_final',
                              'direction':f'case_{case_idx}_{sign}','halvings':h,'alpha':alpha,
                              'distance':distance,
                              'before_a':pair[0],'before_b':pair[1],
                              'intermediate_a':midpair[0],'intermediate_b':midpair[1],
                              'after_a':after[0],'after_b':after[1],
                              'retention_pass':actual['retention_pass'],
                              'protected_lost':actual['protected_lost'],'merit_gain':gain,
                              'joint_improved':permitted,'decision':reason})
                            if permitted:
                                proposals.append((actual['total_deficit'],candidate,after,'two_step_path'))
                    model.load_state_dict(best,strict=True)
        if not proposals:
            iteration_rows.append({'iteration':iteration,'status':'NO_JOINT_EXACT_STEP',
                'deficit':current,'safety_a':pair[0],'safety_b':pair[1],
                'qp_status':diagnostic['status'],'qp_condition':diagnostic.get('condition_number')})
            break
        new_merit,chosen,after,path=min(proposals,key=lambda item:(item[0],item[3]))
        if new_merit>=current-1e-7:
            raise ValueError('V18_NON_MONOTONIC_ACCEPTANCE')
        best=chosen;current=new_merit;accepted+=1
        if path=='two_step_path':path_commits+=1
        iteration_rows.append({'iteration':iteration,'status':'COMMITTED_JOINT_IMPROVEMENT',
             'path':path,'deficit':current,'safety_a':after[0],'safety_b':after[1],
             'qp_condition':diagnostic.get('condition_number')})
        print('V18_JOINT_PROGRESS '+probe.canonical({'iteration':iteration,
             'accepted':accepted,'path':path,'deficit':current,
             'safety_a':after[0],'safety_b':after[1]}),flush=True)
    model.load_state_dict(best,strict=True)
    verified,finalm,_,correct=v16._actual_evaluation(torch,model,geom,spec,mu,sd,device,scope='final')
    final_pair=[float(finalm[i]) for i in indices]
    if (not verified['retention_pass'] or not np.array_equal(geom['mask'] & correct,geom['mask'])
        or abs(verified['total_deficit']-current)>1e-5):
        raise ValueError('V18_FINAL_HARD_RETENTION_FAILED')
    final_joint=all(p>q+min_joint_gain*.5 for p,q in zip(final_pair,initial_pair))
    if accepted and not final_joint:
        raise ValueError('V18_COMMITTED_WITHOUT_BOTH_MARGINS_IMPROVING')
    summary={'status':'JOINT_RESERVE_TARGET_MET' if max(required[i]-final_pair[i] for i in (0,1))<=1e-6 else
               'JOINT_PARTIAL_IMPROVEMENT' if accepted else 'NO_JOINT_IMPROVEMENT_FOUND',
             'accepted_iterations':accepted,'path_commits':path_commits,
             'baseline_safety':initial_pair,'final_safety':final_pair,
             'baseline_correct_train':int(baseline['correct_train']),
             'final_correct_train':int(verified['correct_train']),
             'initial_deficit':float(baseline['total_deficit']),
             'best_deficit':float(current),
             'joint_improved':bool(final_joint),
             'all_protected_reserves_met':bool(verified['all_protected_reserves_met']),
             'hard_full_train_retention':True,'trial_count':len(trial_rows),
             'line_checks':line_checks,'iterations':iteration_rows}
    model.load_state_dict(anchor,strict=True);model.eval()
    return summary,best


def _validate(args,torch):
    if (args.mode!='solve' or not 0<args.reserve<.5 or
        not 1<=args.max_iterations<=100 or not 1<=args.max_halvings<=12 or
        not 6<=args.max_gradient_cases<=64 or
        not 0<args.target_step<=.08 or not 0<args.trust_radius<=1 or
        not 0<args.fd_epsilon<=.1 or not 0<args.min_joint_gain<args.target_step or
        not 0<args.path_step<=args.trust_radius or
        not all(math.isfinite(v) for v in (args.reserve,args.target_step,args.trust_radius,
               args.fd_epsilon,args.min_joint_gain,args.path_step))):
        raise ValueError('V18_INVALID_CONFIGURATION')
    if str(args.device).startswith('cuda') and not torch.cuda.is_available():
        raise ValueError('V18_CUDA_NOT_AVAILABLE')


def run(args):
    import numpy as np
    import torch
    _validate(args,torch)
    inputs=[args.run,args.expanded_run,args.input,args.source_features,args.prior,
            args.v11_run,args.v14_run,args.v15_run,args.v16_run,args.v17_run]
    out=lab._output(args.output,[Path(v) for v in inputs])
    if out.exists():raise FileExistsError(out)
    data,prepared=joint._prepare(args)
    spec,_=v6._routed_profiles(data,prepared)
    _,capture=v9._groups_and_fingerprints(spec)
    _,mu,sd,_=v7._read_v6_checkpoint(torch,args.prior,data,spec)
    report11,cp11,models,proof=v12._verify_v11(torch,args.v11_run,data,capture,
                                               _sha(args.prior),spec,args.device)
    if not (np.array_equal(cp11['selected']['mean'].numpy(),mu) and
            np.array_equal(cp11['selected']['std'].numpy(),sd)):
        raise ValueError('V18_NORMALIZER_MISMATCH')
    report14,post,model,verified14=v15._check_prior14(args.v14_run,proof,capture,
             _sha(args.prior),data,args.focus_group,torch,spec,mu,sd,args.device)
    if (abs(args.reserve-report14['config']['reserve'])>1e-9 or
        report14['config']['margin_width']!=post['margin_width']):
        raise ValueError('V18_RESERVE_OR_ADAPTER_MISMATCH')
    report15,sha15=v16._checked_prior15(args.v15_run,verified14,capture,
                            _sha(args.prior),args.focus_group,data,args.reserve)
    report16,sha16=v17._checked_v16(args.v16_run,sha15,capture,verified14,data,
                                   _sha(args.prior),args.focus_group,args.reserve)
    report17,sha17=_checked_v17(args.v17_run,sha16,capture,verified14,data,
                               _sha(args.prior),args.focus_group,args.reserve)
    prompt_map=v12._deck_prompts(data)
    geom=v16._geometry(torch,model,post,spec,mu,sd,args.device,args.focus_group,
                       args.reserve,args.max_gradient_cases,prompt_map)
    if (int(geom['mask'].sum())!=report17['baseline_protected_train'] or
        abs(geom['initial_deficit']-report17['baseline_margin_deficit'])>1e-6 or
        [geom['rows'][i]['id'] for i in geom['deficient']]!=report17['deficient_cases']):
        raise ValueError('V18_V17_GEOMETRY_MISMATCH')
    if not all(not p.requires_grad for p in model.base.parameters()):
        raise ValueError('V18_FROZEN_SPECIALIST_WAS_UNFROZEN')
    anchor=v9._copy_state(model);trial_rows=[]
    summary,best=_sequential_search(torch,model,geom,spec,mu,sd,args.device,
       max_iterations=args.max_iterations,target_step=args.target_step,
       trust_radius=args.trust_radius,max_halvings=args.max_halvings,
       fd_epsilon=args.fd_epsilon,min_joint_gain=args.min_joint_gain,
       path_step=args.path_step,enable_path=not args.no_path,trial_rows=trial_rows)
    if any(not torch.equal(model.state_dict()[k].cpu(),p.cpu()) for k,p in anchor.items()):
        raise ValueError('V18_SOURCE_WEIGHTS_CHANGED')
    if not np.array_equal(v9._correct_train(torch,model,spec,mu,sd,args.device),geom['mask']):
        raise ValueError('V18_SOURCE_LEDGER_CHANGED')
    save=bool(summary['joint_improved'] and summary['accepted_iterations']>0)
    final_mask=None;original_retention='PASS'
    if save:
        model.load_state_dict(best,strict=True)
        actual,final_margins,_,final_mask=v16._actual_evaluation(torch,model,geom,spec,mu,sd,
                                                         args.device,scope='checkpoint')
        if (not actual['retention_pass'] or abs(actual['total_deficit']-summary['best_deficit'])>1e-5
            or any(float(final_margins[i])<s-1e-6
                   for i,s in zip(geom['deficient'],summary['final_safety']))):
            raise ValueError('V18_CHECKPOINT_REVALIDATION_FAILED')
        original,_=v6._evaluate(torch,model,spec,mu,sd,args.device)
        original_retention=original['status']
        if original_retention!='PASS':
            raise ValueError('V18_ORIGINAL_GAME_RETENTION_FAILED')
        model.load_state_dict(anchor,strict=True)
    report={'schema':SCHEMA,'status':'EXPERIMENT_COMPLETE_NO_PROMOTION',
       'outcome':summary['status'],'mode':'solve',
       'algorithm':'SEQUENTIAL_TWO_HALFSPACE_MIN_NORM_QP_PLUS_EXACT_RELINEARIZATION',
       'constraint_source':'TWO_VERIFIED_TRAIN_DEFICITS_NO_GROUP_ID_INFERENCE',
       'frozen_v11_specialist':True,'original_task_bypass':'UNCHANGED_V6',
       'hard_train_retention':'UNCHANGED_V14_FULL_TRAIN_EXACT_INFERENCE',
       'no_new_trainable_capacity':True,'general_acquisition_training':False,
       'checkpoint_saved':save,'original_retention_status':original_retention,
       'baseline_protected_train':int(geom['mask'].sum()),
       'baseline_margin_deficit':float(geom['initial_deficit']),
       'deficient_cases':[geom['rows'][i]['id'] for i in geom['deficient']],
       'focus_group':args.focus_group,'verified_prompts':{
           geom['rows'][i]['id']:prompt_map[geom['rows'][i]['id']] for i in geom['deficient']},
       'summary':summary,'training_capture_sha256':capture,
       'frozen_lineage':data['probe_info']['lineage'],
       'v6_prior_sha256':_sha(args.prior),'v11_verified':proof,'v14_verified':verified14,
       'v15_report_sha256':sha15,'v16_report_sha256':sha16,'v17_report_sha256':sha17,
       'config':{'reserve':args.reserve,'max_iterations':args.max_iterations,
          'target_step':args.target_step,'trust_radius':args.trust_radius,
          'max_halvings':args.max_halvings,'fd_epsilon':args.fd_epsilon,
          'min_joint_gain':args.min_joint_gain,'path_step':args.path_step,
          'enable_path':not args.no_path,'max_gradient_cases':args.max_gradient_cases},
       'TRAIN_only_gradients_and_candidate_admission':True,
       'synthetic_dev_used_for_training_or_selection':False,
       'previously_seen_test_used_for_training_or_selection':False,
       'production_weights_modified':False,'promotion_authorized':False,
       'general_production_router':'NOT_CERTIFIED'}
    joint._verify_after(data)
    out.mkdir(parents=True,exist_ok=False)
    v10._csv(out/'joint_constraint_trials.csv',trial_rows or [{'stage':'NO_TRIALS','decision':summary['status']}])
    v10._csv(out/'joint_iteration_summary.csv',summary['iterations'] or [{'status':summary['status']}])
    v10._csv(out/'finite_difference_checks.csv',summary['line_checks'] or [{'reason':'NO_GRADIENT_CHECKS'}])
    if save:
        torch.save({'schema':SCHEMA,'status':'DISPOSABLE_NON_PRODUCTION',
          'weights':best,'mean':torch.as_tensor(mu),'std':torch.as_tensor(sd),
          'training_capture_sha256':capture,
          'v14_post_sha256':verified14['v14_post_consolidation_sha256'],
          'v17_report_sha256':sha17,
          'frozen_lineage':data['probe_info']['lineage'],
          'margin_width':report14['config']['margin_width'],
          'capacity_width':report14['config']['capacity_width'],
          'train_ledger_mask':torch.as_tensor(final_mask),
          'hard_full_train_retention':True,'protected_original_passthrough':True,
          'original_task_bypass':'UNCHANGED_V6','promotion_authorized':False},
          out/'joint_margin_disposable_candidate.pt')
    (out/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf-8')
    print('V18_COMPLETE '+probe.canonical({'status':summary['status'],
         'accepted':summary['accepted_iterations'],'joint_improved':summary['joint_improved'],
         'baseline_deficit':summary['initial_deficit'],'best_deficit':summary['best_deficit'],
         'checkpoint_saved':save,'promotion':False,'report':str(out/'report.json')}),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('solve',),default='solve')
    for name in ('run','expanded-run','input','source-features','prior','v11-run',
                 'v14-run','v15-run','v16-run','v17-run','output'):
        p.add_argument('--'+name,required=True,type=Path)
    p.add_argument('--device',default='cuda')
    p.add_argument('--focus-group',default=v12.FOCUS)
    p.add_argument('--reserve',type=float,default=.08)
    p.add_argument('--max-gradient-cases',type=int,default=24)
    p.add_argument('--max-iterations',type=int,default=12)
    p.add_argument('--target-step',type=float,default=.0005)
    p.add_argument('--trust-radius',type=float,default=.05)
    p.add_argument('--max-halvings',type=int,default=7)
    p.add_argument('--fd-epsilon',type=float,default=.002)
    p.add_argument('--min-joint-gain',type=float,default=1e-6)
    p.add_argument('--path-step',type=float,default=.002)
    p.add_argument('--no-path',action='store_true')
    return p


def main(argv=None):
    run(parser().parse_args(argv));return 0


if __name__=='__main__':
    raise SystemExit(main())
