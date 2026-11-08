#!/usr/bin/env python3
"""V21: accumulated double-precision margin optimization; float32 admission authority.

All shadow computations use a genuine float64 copy of the V14 margin adapter and
its frozen V11 base, with captured TRAIN features cast to double. Every shadow
update is checked against actual float32 inference over all acquired TRAIN cases.
An export additionally requires a strictly measured joint float32 improvement.
No DEV/TEST is used; V18/V19/V20 are immutable, provenance-checked evidence.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path

from tools import nanojev_gamer_v1_precision_resolution_v20 as v20
from tools import nanojev_gamer_v1_precision_continuation_v19 as v19
from tools import nanojev_gamer_v1_nonlinear_feasibility_v16 as v16
from tools import nanojev_gamer_v1_margin_consolidation_v13 as v13
from tools import nanojev_gamer_v1_margin_feasibility_v15 as v15
from tools import nanojev_gamer_v1_joint_constraint_v18 as v18
from tools import nanojev_gamer_v1_gradient_sign_audit_v17 as v17
from tools import nanojev_gamer_v1_boundary_isolation_v12 as v12
from tools import nanojev_gamer_v1_math_retention_v9 as v9
from tools import nanojev_gamer_v1_protected_math_repair_v7 as v7
from tools import nanojev_gamer_v1_protected_alignment_lab as v6
from tools import nanojev_gamer_v1_joint_head_repair as joint
from tools import nanojev_gamer_v1_head_only_lab as lab
from tools import nanojev_gamer_v1_sign_information_probe as probe
from tools import nanojev_gamer_v1_conflict_projection_v10 as v10

SCHEMA='nanojev.gamer.v1.shadow-precision-continuation.v21'


def _validate_v20(report,report19,report19_sha,trials19_sha,cp19_sha):
    """Historical replay must be the same read-only verified V19 evidence."""
    counts=report.get('classification',{}).get('classification_counts',{})
    if (report.get('schema')!=v20.SCHEMA or
        report.get('status')!='READ_ONLY_VERIFIED_DIAGNOSTIC_NO_PROMOTION' or
        report.get('v19_report_sha256')!=report19_sha or
        report.get('v19_trials_sha256')!=trials19_sha or
        report.get('v19_checkpoint_verified',{}).get('checkpoint_sha256')!=cp19_sha or
        report.get('v19_checkpoint_verified',{}).get('verified') is not True or
        report.get('v19_checkpoint_verified',{}).get('max_float32_parameter_difference_from_replay')!=0 or
        report.get('v19_trial_count_verified')!=report19.get('summary',{}).get('trial_count') or
        report.get('v19_accepted_updates')!=report19.get('summary',{}).get('accepted_iterations') or
        report.get('source_anchor')!='VERIFIED_V14_POST_CONSOLIDATION' or
        report.get('TRAIN_only_gradients') is not True or
        report.get('hard_train_retention_unchanged') is not True or
        report.get('checkpoint_saved') is not False or
        report.get('production_weights_modified') is not False or
        report.get('promotion_authorized') is not False or
        report.get('synthetic_dev_used_for_training_or_selection') is not False or
        report.get('previously_seen_test_used_for_training_or_selection') is not False or
        sum(counts.values())!=report.get('candidate_probes_verified') or
        report.get('candidate_probes_verified',0)<1):
        raise ValueError('V21_V20_VERIFIED_REPLAY_MISMATCH')
    return True


def _shadow(torch,model,geom):
    """Distinct double model and geometry; never silently upcast a rounded output."""
    import numpy as np
    shadow=copy.deepcopy(model).double().eval()
    gd={**geom,'encoded':geom['encoded'].double(),
        'old':geom['old'].double(),
        'required':np.asarray(geom['required'],dtype=np.float64).copy()}
    if (next(shadow.parameters()).dtype!=torch.float64 or
        not all(not p.requires_grad for p in shadow.base.parameters()) or
        not all(p.dtype==torch.float64 for p in shadow.parameters()) or
        not all(p.dtype==torch.float32 for p in model.parameters())):
        raise ValueError('V21_SHADOW_DTYPE_OR_FREEZE_MISMATCH')
    return shadow,gd


def _margins(torch,net,geom,indices):
    values,deficits=v13._margin_census(torch,net,geom['encoded'],geom['old'],
                                       geom['truth'],geom['required'])
    return [float(values[i]) for i in indices],float(deficits[geom['mask']].sum()),values


def _gradient64(torch,net,geom,indices):
    """Full census batch; parameter and gradient accumulation stay float64."""
    net.eval()
    params=list(net.parameters())
    trainable=[p for p in params if p.requires_grad]
    result=[]
    for i in indices:
        start=(int(i)//128)*128
        stop=min(start+128,len(geom['truth']))
        observed=v13._class_margins(torch,net,geom['encoded'][start:stop],
                  geom['old'][start:stop],geom['truth'][start:stop])
        derivatives=torch.autograd.grad(observed[int(i)-start],trainable,allow_unused=True)
        by_id={id(p):g for p,g in zip(trainable,derivatives)}
        flattened=torch.cat([torch.zeros(p.numel(),dtype=torch.float64)
            if by_id.get(id(p)) is None else by_id[id(p)].detach().cpu().double().reshape(-1)
            for p in params])
        if not bool(torch.isfinite(flattened).all()):
            raise ValueError('V21_NONFINITE_DOUBLE_GRADIENT')
        result.append(flattened)
    return result


def _joint64(torch,gradients,needs,trust_radius):
    """Two-halfspace min-norm QP in float64; no float32 direction cast."""
    import numpy as np
    G=torch.stack([g.detach().cpu().double() for g in gradients]);
    gram=G@G.T
    b=torch.as_tensor(needs,dtype=torch.float64)
    if not all(bool(torch.isfinite(z).all()) for z in (G,gram,b)) or any(float(z)<0 for z in b):
        raise ValueError('V21_INVALID_DOUBLE_QP')
    options=[]
    scale=max(1e-14,float(b.max())*1e-8)
    for i in (0,1):
        if float(gram[i,i])>1e-25:
            c=torch.zeros(2,dtype=torch.float64);c[i]=b[i]/gram[i,i]
            if bool(((gram@c-b)>=-scale).all()):
                options.append((float(c@gram@c),G.T@c,'SINGLE_ACTIVE'))
    if float(torch.linalg.det(gram))>1e-25:
        c=torch.linalg.solve(gram,b)
        if bool((c>=-1e-12).all()) and bool(((gram@c-b)>=-scale).all()):
            options.append((float(c@gram@c),G.T@c,'TWO_ACTIVE'))
    if not options:
        return None,{'status':'DEGENERATE_OR_INCONSISTENT_LOCAL_QP'}
    _,direction,active=min(options,key=lambda v:v[0]);length=float(torch.linalg.vector_norm(direction))
    if length>trust_radius:
        direction=direction*(trust_radius/length)
    if not bool(torch.isfinite(direction).all()):
        raise ValueError('V21_NONFINITE_QP_DIRECTION')
    return direction,{'status':'DOUBLE_QP_WITHIN_TRUST' if length<=trust_radius else 'DOUBLE_QP_TRUST_SCALED',
                      'active_set':active,'unscaled_norm':length,
                      'condition':float(torch.linalg.cond(gram)),
                      'predicted':(G@direction).tolist()}


def _runtime_probe(torch,runtime,geometry,spec,mu,sd,device,shadow_weights,ledger,indices):
    """Never validate retention with the double shadow or shadow-only margins."""
    import numpy as np
    before=v9._copy_state(runtime)
    try:
        runtime.load_state_dict(shadow_weights,strict=True)  # intentional fp64 -> fp32 rounding
        runtime.eval()
        assessment,margin,_,correct=v16._actual_evaluation(
                torch,runtime,geometry,spec,mu,sd,device,scope='v21_runtime_trial')
        if not np.array_equal(correct & ledger,ledger):
            return assessment,[float(margin[i]) for i in indices],correct,False
        return assessment,[float(margin[i]) for i in indices],correct,bool(assessment['retention_pass'])
    finally:
        runtime.load_state_dict(before,strict=True);runtime.eval()


def _runtime_exportable(pair,merit,anchor_pair,anchor_merit,*,min_gain,min_merit):
    """Absolute float32 improvements; never substitute a float64 merit for export."""
    return (all(float(p)-float(q)>=min_gain for p,q in zip(pair,anchor_pair))
            and anchor_merit-float(merit)>=min_merit)


def _finite_difference64(torch,net,geom,state,indices,direction,epsilon):
    """Central differences in the *same* double geometry as analytic gradients."""
    grad=_gradient64(torch,net,geom,indices)
    length=float(torch.linalg.vector_norm(direction))
    if not math.isfinite(length) or length<=1e-25:
        return {'trusted':False,'reason':'ZERO_DIRECTION'}
    unit=direction/length
    analytic=[float(torch.dot(g,unit)) for g in grad]
    try:
        v10._set_displacement(torch,net,state,unit,epsilon)
        plus,_,_=_margins(torch,net,geom,indices)
        v10._set_displacement(torch,net,state,unit,-epsilon)
        minus,_,_=_margins(torch,net,geom,indices)
    finally:
        net.load_state_dict(state,strict=True);net.eval()
    measured=[(p-m)/(2.*epsilon) for p,m in zip(plus,minus)]
    consistent=all(math.isfinite(a) and math.isfinite(b) and
        abs(a-b)<=max(2e-6,.04*abs(a)) for a,b in zip(analytic,measured))
    return {'trusted':consistent,'reason':'DOUBLE_DERIVATIVES_AGREE' if consistent else 'DOUBLE_DERIVATIVE_MISMATCH',
            'analytic':analytic,'finite_difference':measured}


def _solve(torch,runtime,geom,spec,mu,sd,device,*,iterations,stalls_max,target_step,
           min_shadow_gain,min_runtime_gain,min_runtime_merit,trust_radius,
           max_halvings,fd_epsilon,trial_rows):
    import numpy as np
    indices=list(geom['deficient'])
    if len(indices)!=2:
        raise ValueError('V21_REQUIRES_TWO_VERIFIED_DEFICIENT_CASES')
    starting_runtime=v9._copy_state(runtime)
    double,geo64=_shadow(torch,runtime,geom)
    starting_double=v9._copy_state(double)
    baseline_result,baseline_margins,_,initial_correct=v16._actual_evaluation(
                        torch,runtime,geom,spec,mu,sd,device,scope='v21_anchor')
    if not np.array_equal(initial_correct,geom['mask']) or not baseline_result['retention_pass']:
        raise ValueError('V21_INVALID_V14_TRAIN_ANCHOR')
    baseline_pair=[float(baseline_margins[i]) for i in indices]
    baseline_merit=float(baseline_result['total_deficit'])
    mask=initial_correct.copy()
    pair64,merit64,_=_margins(torch,double,geo64,indices)
    starting_pair64=pair64.copy();starting_merit64=merit64
    current=v9._copy_state(double)
    best_export=None;best_export_merit=baseline_merit
    accepted_shadow=0;runtime_exports=0;stalls=0
    active_target=target_step;history=[]
    try:
        for iteration in range(1,iterations+1):
            double.load_state_dict(current,strict=True);double.eval()
            pair64,merit64,_=_margins(torch,double,geo64,indices)
            required=[float(geo64['required'][i]) for i in indices]
            deficits=[max(0.,r-s) for r,s in zip(required,pair64)]
            if max(deficits)<=1e-11:
                history.append({'iteration':iteration,'status':'DOUBLE_RESERVE_MET'})
                break
            gradients=_gradient64(torch,double,geo64,indices)
            admitted=False
            for name,ratio in [('balanced',(1.,1.)),('favor_a',(1.,.25)),('favor_b',(.25,1.))]:
                needs=[min(deficits[j],active_target*ratio[j]) for j in (0,1)]
                direction,qp=_joint64(torch,gradients,needs,trust_radius)
                if direction is None:
                    trial_rows.append({'iteration':iteration,'direction':name,'decision':qp['status']})
                    continue
                fd=_finite_difference64(torch,double,geo64,current,indices,direction,fd_epsilon)
                if not fd['trusted']:
                    trial_rows.append({'iteration':iteration,'direction':name,'decision':fd['reason'],
                                       'analytic':fd.get('analytic'),'measured':fd.get('finite_difference')})
                    continue
                for h in range(max_halvings+1):
                    alpha=2.**(-h)
                    v10._set_displacement(torch,double,current,direction,alpha)
                    proposed=v9._copy_state(double)
                    next_pair,next_merit,_=_margins(torch,double,geo64,indices)
                    double_gains=[x-y for x,y in zip(next_pair,pair64)]
                    double_gain=merit64-next_merit
                    if not (min(double_gains)>min_shadow_gain and double_gain>min_shadow_gain):
                        trial_rows.append({'iteration':iteration,'direction':name,'halvings':h,
                            'double_min_gain':min(double_gains),'double_merit_gain':double_gain,
                            'decision':'DOUBLE_JOINT_NOT_IMPROVED'})
                        continue
                    assessment,runtime_pair,correct,safe=_runtime_probe(torch,runtime,geom,spec,mu,sd,device,
                                              proposed,mask,indices)
                    exportable=safe and _runtime_exportable(runtime_pair,
                                assessment['total_deficit'],baseline_pair,baseline_merit,
                                min_gain=min_runtime_gain,min_merit=min_runtime_merit)
                    trial_rows.append({'iteration':iteration,'direction':name,'halvings':h,
                        'double_min_gain':min(double_gains),'double_merit_gain':double_gain,
                        'float32_gain_a_from_anchor':runtime_pair[0]-baseline_pair[0],
                        'float32_gain_b_from_anchor':runtime_pair[1]-baseline_pair[1],
                        'float32_merit_gain_from_anchor':baseline_merit-assessment['total_deficit'],
                        'float32_correct_train':assessment['correct_train'],
                        'retention_pass':safe,'exportable':bool(exportable),
                        'decision':'SHADOW_REJECTED_RUNTIME_RETENTION' if not safe else
                                   'SHADOW_ACCEPTED_EXPORTED' if exportable else 'SHADOW_ACCEPTED_SUB_RESOLUTION'})
                    if not safe:
                        continue
                    # The shadow is allowed to continue without a resolved float32
                    # improvement, but every committed shadow state passed runtime retention.
                    newly_acquired=bool((correct & ~mask).any())
                    current=proposed;mask=mask | correct;accepted_shadow+=1;stalls=0
                    pair64=next_pair;merit64=next_merit;admitted=True
                    if newly_acquired and best_export is not None:
                        # A previous export must not escape without knowledge
                        # acquired by a later shadow step. Revoke that export
                        # unless its float32 predictions also preserve the
                        # enlarged ledger; the current step remains authoritative.
                        _,_,_,prior_export_safe=_runtime_probe(torch,runtime,geom,
                            spec,mu,sd,device,best_export,mask,indices)
                        if not prior_export_safe:
                            best_export=None
                            best_export_merit=baseline_merit
                    if exportable and assessment['total_deficit']<best_export_merit-1e-12:
                        best_export=copy.deepcopy(proposed)
                        best_export_merit=float(assessment['total_deficit'])
                        runtime_exports+=1
                    history.append({'iteration':iteration,'status':'SHADOW_COMMITTED',
                        'direction':name,'halvings':h,'double_merit':merit64,
                        'float32_merit':assessment['total_deficit'],
                        'float32_exportable':exportable,'shadow_accepted':accepted_shadow,
                        'runtime_export_updates':runtime_exports})
                    print('V21_PROGRESS '+probe.canonical({'iteration':iteration,
                        'shadow_accepted':accepted_shadow,'float32_export_updates':runtime_exports,
                        'double_deficit':merit64,'runtime_deficit':assessment['total_deficit'],
                        'runtime_joint_exportable':exportable}),flush=True)
                    break
                if admitted:
                    break
            if not admitted:
                stalls+=1;active_target=max(1e-10,active_target/2.)
                history.append({'iteration':iteration,'status':'SHADOW_STALL',
                                'stalls':stalls,'next_target':active_target})
                if stalls>=stalls_max:
                    break
            else:
                active_target=min(target_step*8.,active_target*1.25)
        # Authoritative final check of any exportable fp32 checkpoint.
        final_pair64,final_merit64,_=_margins(torch,double,geo64,indices)
        if best_export is not None:
            assessment,runtime_pair,correct,safe=_runtime_probe(torch,runtime,geom,spec,mu,sd,
                                             device,best_export,mask,indices)
            if (not safe or not _runtime_exportable(runtime_pair,assessment['total_deficit'],
                 baseline_pair,baseline_merit,min_gain=min_runtime_gain,min_merit=min_runtime_merit)
                 or abs(assessment['total_deficit']-best_export_merit)>2e-6):
                raise ValueError('V21_EXPORTED_CHECKPOINT_REVALIDATION_FAILED')
            runtime.load_state_dict(best_export,strict=True);runtime.eval()
            original,_=v6._evaluate(torch,runtime,spec,mu,sd,device)
            if original['status']!='PASS':
                raise ValueError('V21_ORIGINAL_TASK_RETENTION_FAILED')
            runtime.load_state_dict(starting_runtime,strict=True);runtime.eval()
        summary={'status':'FLOAT32_JOINT_IMPROVEMENT' if best_export is not None else
                           'SHADOW_ONLY_IMPROVEMENT' if accepted_shadow else 'NO_SHADOW_IMPROVEMENT',
            'shadow_accepted_steps':accepted_shadow,'float32_export_updates':runtime_exports,
            'checkpoint_saved':best_export is not None,
            'baseline_float32_pair':baseline_pair,'baseline_float32_deficit':baseline_merit,
            'baseline_float64_pair':starting_pair64,'baseline_float64_deficit':starting_merit64,
            'final_float64_pair':final_pair64,'final_float64_deficit':final_merit64,
            'best_export_float32_deficit':best_export_merit if best_export is not None else None,
            'acquired_protected_train':int(mask.sum()),'hard_full_train_retention':True,
            'historical_v14_anchor_unchanged':True,'iterations':history,
            'no_float64_only_export':True}
        return summary,best_export,mask
    finally:
        runtime.load_state_dict(starting_runtime,strict=True);runtime.eval()
        double.load_state_dict(starting_double,strict=True);double.eval()


def _validate(args,torch):
    checks=(args.reserve,args.target_step,args.min_shadow_gain,args.min_runtime_gain,
            args.min_runtime_merit,args.trust_radius,args.fd_epsilon)
    if (args.mode!='solve' or not all(math.isfinite(v) for v in checks)
        or not 0<args.reserve<.5 or not 0<args.target_step<=.01
        or not 0<args.min_shadow_gain<args.target_step
        or not 0<args.min_runtime_gain<=.001 or not 0<args.min_runtime_merit<=.001
        or not 0<args.trust_radius<=1 or not 0<args.fd_epsilon<=.1
        or not 1<=args.max_iterations<=200 or not 1<=args.max_stalls<=40
        or not 1<=args.max_halvings<=16 or not 6<=args.max_gradient_cases<=64
        or str(args.device).startswith('cuda') and not torch.cuda.is_available()):
        raise ValueError('V21_INVALID_CONFIGURATION')


def run(args):
    import numpy as np
    import torch
    _validate(args,torch)
    paths=[args.run,args.expanded_run,args.input,args.source_features,args.prior,
       args.v11_run,args.v14_run,args.v15_run,args.v16_run,args.v17_run,
       args.v18_run,args.v19_run,args.v20_run]
    out=lab._output(args.output,[Path(x) for x in paths])
    if out.exists():
        raise FileExistsError('V21_OUTPUT_ALREADY_EXISTS: '+str(out))
    f19=Path(args.v19_run)/'report.json'
    c19=Path(args.v19_run)/'precision_joint_disposable_candidate.pt'
    f20=Path(args.v20_run)/'report.json'
    t19=Path(args.v19_run)/'precision_joint_trials.csv'
    if not all(p.is_file() for p in (f19,c19,f20,t19)):
        raise ValueError('V21_HISTORY_EVIDENCE_MISSING')
    r19=json.loads(f19.read_text(encoding='utf-8'))
    r20=json.loads(f20.read_text(encoding='utf-8'))
    _validate_v20(r20,r19,v19._sha(f19),v19._sha(t19),v19._sha(c19))
    data,prepared=joint._prepare(args)
    spec,_=v6._routed_profiles(data,prepared)
    _,capture=v9._groups_and_fingerprints(spec)
    _,mu,sd,_=v7._read_v6_checkpoint(torch,args.prior,data,spec)
    _,cp11,_,proof=v12._verify_v11(torch,args.v11_run,data,capture,
                                  v19._sha(args.prior),spec,args.device)
    if not (np.array_equal(cp11['selected']['mean'].numpy(),mu)
            and np.array_equal(cp11['selected']['std'].numpy(),sd)):
        raise ValueError('V21_NORMALIZER_MISMATCH')
    report14,post,model,verified14=v15._check_prior14(args.v14_run,proof,capture,
          v19._sha(args.prior),data,args.focus_group,torch,spec,mu,sd,args.device)
    _,sha15=v16._checked_prior15(args.v15_run,verified14,capture,
                                v19._sha(args.prior),args.focus_group,data,args.reserve)
    _,sha16=v17._checked_v16(args.v16_run,sha15,capture,verified14,data,
                             v19._sha(args.prior),args.focus_group,args.reserve)
    _,sha17=v18._checked_v17(args.v17_run,sha16,capture,verified14,data,
                             v19._sha(args.prior),args.focus_group,args.reserve)
    geom=v16._geometry(torch,model,post,spec,mu,sd,args.device,args.focus_group,
                       args.reserve,args.max_gradient_cases,v12._deck_prompts(data))
    evidence=v19._verify_v18(torch,args.v18_run,sha17,verified14,capture,data,
                  v19._sha(args.prior),args.focus_group,args.reserve,model,
                  geom,spec,mu,sd,args.device)
    v20._validate_saved_report(r19,r19['config'],capture,data,proof,
             verified14,sha15,sha16,sha17,evidence,v19._sha(args.prior),args.focus_group)
    if r20.get('frozen_lineage')!=data['probe_info']['lineage']:
        raise ValueError('V21_V20_FROZEN_LINEAGE_MISMATCH')
    before=v9._copy_state(model)
    trial_rows=[]
    result,weights,mask=_solve(torch,model,geom,spec,mu,sd,args.device,
       iterations=args.max_iterations,stalls_max=args.max_stalls,
       target_step=args.target_step,min_shadow_gain=args.min_shadow_gain,
       min_runtime_gain=args.min_runtime_gain,min_runtime_merit=args.min_runtime_merit,
       trust_radius=args.trust_radius,max_halvings=args.max_halvings,
       fd_epsilon=args.fd_epsilon,trial_rows=trial_rows)
    if any(not torch.equal(model.state_dict()[k].cpu(),v.cpu()) for k,v in before.items()):
        raise ValueError('V21_V14_SOURCE_WEIGHTS_CHANGED')
    if not np.array_equal(v9._correct_train(torch,model,spec,mu,sd,args.device),geom['mask']):
        raise ValueError('V21_V14_SOURCE_LEDGER_CHANGED')
    report={'schema':SCHEMA,'status':'EXPERIMENT_COMPLETE_NO_PROMOTION',
       'algorithm':'FLOAT64_SHADOW_QP_FLOAT32_EXACT_RUNTIME_ADMISSION',
       'summary':result,'checkpoint_saved':weights is not None,'mode':'solve',
       'source_anchor':'VERIFIED_V14_POST_CONSOLIDATION',
       'measurement_policy':'DOUBLE_SHADOW_128_CENSUS_AND_FLOAT32_FULL_TRAIN_INFERENCE',
       'config':{'reserve':args.reserve,'max_iterations':args.max_iterations,
           'max_stalls':args.max_stalls,'target_step':args.target_step,
           'min_shadow_gain':args.min_shadow_gain,'min_runtime_gain':args.min_runtime_gain,
           'min_runtime_merit':args.min_runtime_merit,'trust_radius':args.trust_radius,
           'max_halvings':args.max_halvings,'fd_epsilon':args.fd_epsilon,
           'max_gradient_cases':args.max_gradient_cases},
       'v19_report_sha256':v19._sha(f19),'v19_trials_sha256':v19._sha(t19),
       'v19_checkpoint_sha256':v19._sha(c19),'v20_report_sha256':v19._sha(f20),
       'v18_checkpoint_sha256':evidence['v18_checkpoint_sha256'],
       'frozen_lineage':data['probe_info']['lineage'],'training_capture_sha256':capture,
       'v6_prior_sha256':v19._sha(args.prior),'v11_verified':proof,
       'v14_verified':verified14,'v15_report_sha256':sha15,
       'v16_report_sha256':sha16,'v17_report_sha256':sha17,
       'frozen_v11_specialist':True,'no_new_trainable_capacity':True,
       'hard_train_retention':'UNCHANGED_V14_FULL_TRAIN_EXACT_INFERENCE',
       'original_task_bypass':'UNCHANGED_V6','general_acquisition_training':False,
       'synthetic_dev_used_for_training_or_selection':False,
       'previously_seen_test_used_for_training_or_selection':False,
       'production_weights_modified':False,'promotion_authorized':False,
       'general_production_router':'NOT_CERTIFIED'}
    if (v19._sha(f19)!=report['v19_report_sha256'] or
        v19._sha(t19)!=report['v19_trials_sha256'] or
        v19._sha(c19)!=report['v19_checkpoint_sha256'] or
        v19._sha(f20)!=report['v20_report_sha256']):
        raise ValueError('V21_HISTORICAL_EVIDENCE_CHANGED_DURING_RUN')
    joint._verify_after(data)
    out.mkdir(parents=True,exist_ok=False)
    v10._csv(out/'shadow_precision_trials.csv',trial_rows or [{'decision':'NO_TRIALS'}])
    v10._csv(out/'shadow_precision_iterations.csv',result['iterations'] or [{'status':result['status']}])
    if weights is not None:
        # Saved weights are intentionally float32, never the shadow float64 state.
        torch.save({'schema':SCHEMA,'status':'DISPOSABLE_NON_PRODUCTION',
          'weights':{k:v.detach().cpu().float() for k,v in weights.items()},
          'mean':torch.as_tensor(mu),'std':torch.as_tensor(sd),
          'training_capture_sha256':capture,
          'v14_post_sha256':verified14['v14_post_consolidation_sha256'],
          'v19_checkpoint_sha256':report['v19_checkpoint_sha256'],
          'v20_report_sha256':report['v20_report_sha256'],
          'frozen_lineage':data['probe_info']['lineage'],
          'margin_width':report14['config']['margin_width'],
          'capacity_width':report14['config']['capacity_width'],
          'train_ledger_mask':torch.as_tensor(mask),
          'hard_full_train_retention':True,'protected_original_passthrough':True,
          'promotion_authorized':False},out/'shadow_precision_disposable_candidate.pt')
    (out/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True,
                                     allow_nan=False)+'\n',encoding='utf-8')
    print('V21_COMPLETE '+probe.canonical({'status':result['status'],
      'shadow_accepted':result['shadow_accepted_steps'],
      'float32_export_updates':result['float32_export_updates'],
      'checkpoint_saved':bool(weights is not None),'promotion':False,
      'report':str(out/'report.json')}),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('solve',),default='solve')
    for name in ('run','expanded-run','input','source-features','prior','v11-run','v14-run',
                 'v15-run','v16-run','v17-run','v18-run','v19-run','v20-run','output'):
        p.add_argument('--'+name,required=True,type=Path)
    p.add_argument('--device',default='cuda')
    p.add_argument('--focus-group',default=v12.FOCUS)
    p.add_argument('--reserve',type=float,default=.08)
    p.add_argument('--max-gradient-cases',type=int,default=24)
    p.add_argument('--max-iterations',type=int,default=60)
    p.add_argument('--max-stalls',type=int,default=8)
    p.add_argument('--target-step',type=float,default=2e-6)
    p.add_argument('--min-shadow-gain',type=float,default=1e-12)
    p.add_argument('--min-runtime-gain',type=float,default=2e-7)
    p.add_argument('--min-runtime-merit',type=float,default=3e-7)
    p.add_argument('--trust-radius',type=float,default=.05)
    p.add_argument('--max-halvings',type=int,default=8)
    p.add_argument('--fd-epsilon',type=float,default=.002)
    return p


def main(argv=None):
    run(parser().parse_args(argv));return 0


if __name__=='__main__':
    raise SystemExit(main())
