#!/usr/bin/env python3
"""V17: read-only signed finite-difference audit of V14 margin gradients.

Verify the V11/V14/V15/V16 chain, then evaluate both signs of normalized
parameter-space margin gradients against actual nonlinear class-safety and the
full exact-inference TRAIN ledger. The only trainable *parameters in the model*
are V14's existing margin adapter. No optimizer runs, weights are committed,
DEV/TEST fitting occurs, or production changes are authorized.

Finite differences measure a local directional derivative, NOT a global
feasibility certificate. An inconclusive small-step result is not called an
architecture failure; report both measured and predicted quantities.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

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

SCHEMA='nanojev.gamer.v1.signed-finite-difference.v17'


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _steps(text):
    try:
        values=[float(t.strip()) for t in str(text).split(',')]
    except (ValueError,TypeError) as exc:
        raise argparse.ArgumentTypeError('V17_INVALID_STEP_SIZES') from exc
    if (not 2<=len(values)<=12 or any(not math.isfinite(v) or not 0<v<=1 for v in values)
            or values!=sorted(set(values))):
        raise argparse.ArgumentTypeError('V17_REQUIRE_2_TO_12_INCREASING_POSITIVE_STEPS')
    return values


def _checked_v16(root,report15_sha,capture,verified14,data,prior_sha,focus,reserve):
    path=Path(root)/'report.json'
    if not path.is_file():
        raise ValueError('V17_V16_REPORT_MISSING')
    r=json.loads(path.read_text(encoding='utf-8'))
    if (r.get('schema')!=v16.SCHEMA or r.get('status')!='DIAGNOSTIC_COMPLETE_NO_PROMOTION'
        or r.get('promotion_authorized') is not False
        or r.get('production_weights_modified') is not False
        or r.get('general_acquisition_training') is not False
        or r.get('TRAIN_only_gradients_and_candidate_admission') is not True
        or r.get('synthetic_dev_used_for_training_or_selection') is not False
        or r.get('previously_seen_test_used_for_training_or_selection') is not False
        or r.get('frozen_v11_specialist') is not True
        or r.get('checkpoint_saved') is not False
        or r.get('baseline_protected_train')<1
        or r.get('v15_report_sha256')!=report15_sha
        or r.get('training_capture_sha256')!=capture
        or r.get('v14_verified')!=verified14
        or r.get('frozen_lineage')!=data['probe_info']['lineage']
        or r.get('v6_prior_sha256')!=prior_sha
        or r.get('focus_group')!=focus
        or abs(float(r.get('config',{}).get('reserve',-1))-reserve)>1e-9):
        raise ValueError('V17_V16_PROVENANCE_MISMATCH')
    return r,_sha(path)


def _flat_gradient(torch,model,scalar):
    """Parameter order matches V10's _set_displacement, including frozen zeros."""
    params=list(model.parameters())
    trainable=[p for p in params if p.requires_grad]
    gradients=torch.autograd.grad(scalar,trainable,allow_unused=True)
    lookup={id(p):g for p,g in zip(trainable,gradients)}
    return torch.cat([torch.zeros(p.numel(),dtype=torch.float32)
        if lookup.get(id(p)) is None else lookup[id(p)].detach().cpu().float().reshape(-1)
        for p in params])


def _direction_vectors(torch,model,geom):
    """Construct margin-increasing and objective-decreasing direction families.

    V15 gradients are already derivatives of decision *safety*, not deficit.
    Recompute the objective at the unchanged anchor so the diagnostic checks
    the same quantity later used in finite differences.
    """
    rows=geom['rows']; indexes=geom['deficient']
    if not len(indexes):
        return {}, {'status':'NO_DEFICIENT_CASES'}
    if len(indexes)!=2:
        raise ValueError('V17_EXPECTED_TWO_DEFICIENT_CASES')
    state=geom['anchor_weights'];model.load_state_dict(state,strict=True)
    model.eval()
    torch_grads=[]
    for i in indexes:
        safety=v13._class_margins(torch,model,geom['encoded'][i:i+1],
                                  geom['old'][i:i+1],geom['truth'][i:i+1]).sum()
        torch_grads.append(_flat_gradient(torch,model,safety))
    # Exact unconstrained hinge objective used in the report, computed with
    # autograd in the SAME execution mode as the nonlinear probes.
    required=torch.as_tensor(geom['required'],device=geom['encoded'].device,dtype=torch.float32)
    mask=torch.as_tensor(geom['mask'],device=geom['encoded'].device,dtype=torch.bool)
    safety=v13._class_margins(torch,model,geom['encoded'],geom['old'],geom['truth'])
    merit=torch.relu(required[mask]-safety[mask]).sum()
    hinge_gradient=_flat_gradient(torch,model,merit)
    if (not all(bool(torch.isfinite(g).all()) for g in [*torch_grads,hinge_gradient])
        or any(float(torch.linalg.vector_norm(g))<=1e-12 for g in torch_grads)):
        raise ValueError('V17_NONFINITE_OR_ZERO_MARGIN_GRADIENT')
    directions={
        'case_negative_safety_gradient':torch_grads[0],
        'case_positive_safety_gradient':torch_grads[1],
        'sum_safety_gradients':torch_grads[0]+torch_grads[1],
        'difference_safety_gradients':torch_grads[0]-torch_grads[1],
        'exact_hinge_descent':-hinge_gradient,
        'v16_nonlinear_merit_descent':v16._gradient_step(torch,model,geom,state),
    }
    if geom.get('linear_direction') is not None:
        directions['v15_simultaneous_linearized']=geom['linear_direction']
    norm_a=float(torch.linalg.vector_norm(torch_grads[0]))
    norm_b=float(torch.linalg.vector_norm(torch_grads[1]))
    cos=float(torch.dot(torch_grads[0],torch_grads[1])/(norm_a*norm_b))
    diag={'status':'GRADIENTS_COMPUTED','deficient_ids':[rows[i]['id'] for i in indexes],
          'deficient_indices':indexes,'gradient_norms':[norm_a,norm_b],
          'gradient_cosine':cos,
          'exact_hinge_objective':float(merit.detach().item()),
          'exact_hinge_gradient_norm':float(torch.linalg.vector_norm(hinge_gradient)),
          'analytic_deficit_directional_gradient':'GRADIENT_OF_SUM_RELU_REQUIRED_MINUS_DECISION_SAFETY'}
    return directions,diag


def _finite_difference_audit(torch,model,geom,spec,mu,sd,device,step_sizes,
                             *, execute_exact=v16._actual_evaluation):
    """Evaluate +/- perturbations in fixed directions, always restoring anchor.

    Delta is a PARAMETER displacement norm, not learning rate. No state is
    accumulated and no candidate is saved regardless of its measured merit.
    """
    import numpy as np
    anchor=geom['anchor_weights']
    model.load_state_dict(anchor,strict=True);model.eval()
    baseline,base_margins,_,base_correct=execute_exact(torch,model,geom,spec,mu,sd,device,scope='anchor')
    if not baseline['retention_pass'] or not np.array_equal(base_correct,geom['mask']):
        raise ValueError('V17_ANCHOR_RETENTION_MISMATCH')
    directions,diagnostic=_direction_vectors(torch,model,geom)
    trials=[];checks=[];idx=geom['deficient']
    try:
        if not directions:
            return trials,checks,diagnostic
        for name,direction in directions.items():
            length=float(torch.linalg.vector_norm(direction))
            if length<=1e-12 or not math.isfinite(length):
                continue
            unit=direction.detach().cpu().float()/length
            if not bool(torch.isfinite(unit).all()):
                raise ValueError('V17_INVALID_PROBE_DIRECTION')
            # Independently compute the analytic directional derivative for
            # every deficient case. This is the sign we are checking.
            model.load_state_dict(anchor,strict=True);model.eval()
            gradients=[]
            for i in idx:
                scalar=v13._class_margins(torch,model,geom['encoded'][i:i+1],
                                       geom['old'][i:i+1],geom['truth'][i:i+1]).sum()
                gradients.append(_flat_gradient(torch,model,scalar))
            predicted=[float(torch.dot(g,unit)) for g in gradients]
            observations={}
            for size in step_sizes:
                for sign in (-1,1):
                    # The only model mutation is a temporary, complete state
                    # copy; restore after each trial even on inference failure.
                    try:
                        v10._set_displacement(torch,model,anchor,unit,float(sign)*size)
                        model.eval()
                        exact,margins,deficits,correct=execute_exact(torch,model,geom,spec,
                                                                  mu,sd,device,scope='trial')
                        shifts=[float(margins[i]-base_margins[i]) for i in idx]
                        observed_merit=float(exact['total_deficit']-baseline['total_deficit'])
                        if not all(math.isfinite(v) for v in [*shifts,observed_merit]):
                            raise ValueError('V17_NONFINITE_EXACT_FINITE_DIFFERENCE')
                        row={'direction':name,'sign':sign,'step_norm':size,
                            'measured_displacement_norm':float(size),
                            'protected_retention_pass':bool(exact['retention_pass']),
                            'protected_lost':int(exact['protected_lost']),
                            'newly_correct_train':int((correct & ~base_correct).sum()),
                            'total_deficit_before':float(baseline['total_deficit']),
                            'total_deficit_after':float(exact['total_deficit']),
                            'deficit_improvement':-observed_merit,
                            'deficient_a_id':geom['rows'][idx[0]]['id'],
                            'deficient_b_id':geom['rows'][idx[1]]['id'],
                            'deficient_a_delta_safety':shifts[0],
                            'deficient_b_delta_safety':shifts[1],
                            'predicted_a_delta_safety':predicted[0]*sign*size,
                            'predicted_b_delta_safety':predicted[1]*sign*size,
                            'predicted_aggregate_deficit_delta':-sum(predicted)*sign*size,
                            'measured_aggregate_deficit_delta':observed_merit,
                            'all_protected_reserves_met':bool(exact['all_protected_reserves_met'])}
                        trials.append(row)
                        observations[(size,sign)]=row
                    finally:
                        model.load_state_dict(anchor,strict=True);model.eval()
                plus=observations[(size,1)];minus=observations[(size,-1)]
                for k,label in enumerate(('a','b')):
                    measured=(plus[f'deficient_{label}_delta_safety']-
                              minus[f'deficient_{label}_delta_safety'])/(2*size)
                    predicted_derivative=predicted[k]
                    # Numerical comparison expressed as quantities, not a
                    # binary claim at precision below float32 resolution.
                    abs_error=abs(measured-predicted_derivative)
                    scale=max(1e-4,abs(predicted_derivative))
                    tolerance=max(5e-4,0.10*abs(predicted_derivative))
                    checks.append({'direction':name,'step_norm':size,
                       'case_id':geom['rows'][idx[k]]['id'],
                       'analytic_directional_derivative':predicted_derivative,
                       'central_finite_difference_derivative':measured,
                       'absolute_error':abs_error,'relative_error_scaled':abs_error/scale,
                       'within_tolerance':bool(abs_error<=tolerance),
                       'sign_agrees':bool((abs(predicted_derivative)<tolerance and
                                            abs(measured)<tolerance) or
                                           (predicted_derivative*measured>0)),
                       'both_signs_retention_safe':bool(plus['protected_retention_pass'] and
                                                        minus['protected_retention_pass'])})
                # The actual optimization target is the aggregate hinge
                # deficit. Audit its sign independently of per-case margins.
                objective_expected=-sum(predicted)
                objective_observed=(plus['measured_aggregate_deficit_delta']-
                                    minus['measured_aggregate_deficit_delta'])/(2*size)
                obj_err=abs(objective_observed-objective_expected)
                obj_tol=max(5e-4,.1*abs(objective_expected))
                checks.append({'direction':name,'step_norm':size,
                    'case_id':'AGGREGATE_PROTECTED_RESERVE_DEFICIT',
                    'analytic_directional_derivative':objective_expected,
                    'central_finite_difference_derivative':objective_observed,
                    'absolute_error':obj_err,
                    'relative_error_scaled':obj_err/max(1e-4,abs(objective_expected)),
                    'within_tolerance':bool(obj_err<=obj_tol),
                    'sign_agrees':bool((abs(objective_expected)<obj_tol and
                         abs(objective_observed)<obj_tol) or
                        (objective_observed*objective_expected>0)),
                    'both_signs_retention_safe':bool(plus['protected_retention_pass'] and
                                                     minus['protected_retention_pass'])})
    finally:
        model.load_state_dict(anchor,strict=True);model.eval()
    return trials,checks,diagnostic


def _interpret(trials,checks,step_sizes):
    """Separate derivative validation from merit improvement and retention."""
    if not trials:
        return {'status':'NO_DIRECTIONS_TO_TEST','sign_error_proven':False}
    best_small=min(step_sizes)
    local=[c for c in checks if c['step_norm']==best_small and
           abs(c['analytic_directional_derivative'])>max(5e-4,.10*abs(c['analytic_directional_derivative']))]
    disagreements=[c for c in local if not c['sign_agrees']]
    local_agreements=len(local)-len(disagreements)
    safe=[t for t in trials if t['protected_retention_pass']]
    improving=[t for t in safe if t['deficit_improvement']>1e-7]
    safe_improvements=sorted(improving,key=lambda t:(-t['deficit_improvement'],t['step_norm']))
    status=('FINITE_DIFFERENCE_DIRECTION_MISMATCH_REQUIRES_INVESTIGATION' if disagreements else
            'BOTH_SIGN_LOCAL_PROBES_CONFIRM_GRADIENTS' if local and local_agreements==len(local) else
            'FINITE_DIFFERENCE_INCONCLUSIVE_AT_SMALLEST_STEP')
    return {'status':status,'local_derivative_checks':len(local),
            'local_sign_agreements':local_agreements,'local_sign_disagreements':len(disagreements),
            'retention_safe_probes':len(safe),'total_probes':len(trials),
            'retention_safe_merit_improvements':len(improving),
            'best_safe_trial':safe_improvements[0] if safe_improvements else None,
            'sign_error_proven':False,
            'interpretation_caution':'LOCAL_FINITE_DIFFERENCES_ONLY_NO_GLOBAL_OPTIMIZER_CERTIFICATE'}


def _validate(args,torch):
    if (args.mode!='audit' or not 0<args.reserve<.5 or
        args.max_gradient_cases<6 or args.max_gradient_cases>64 or
        not isinstance(args.steps,list) or len(args.steps)<2 or
        args.steps!=sorted(set(args.steps)) or any(not math.isfinite(v) or not 0<v<=1 for v in args.steps)):
        raise ValueError('V17_INVALID_CONFIGURATION')
    if str(args.device).startswith('cuda') and not torch.cuda.is_available():
        raise ValueError('V17_CUDA_NOT_AVAILABLE')


def run(args):
    import numpy as np
    import torch
    _validate(args,torch)
    inputs=[args.run,args.expanded_run,args.input,args.source_features,args.prior,
            args.v11_run,args.v14_run,args.v15_run,args.v16_run]
    out=lab._output(args.output,[Path(v) for v in inputs])
    if out.exists():
        raise FileExistsError(out)
    data,prepared=joint._prepare(args)
    spec,_=v6._routed_profiles(data,prepared)
    _,capture=v9._groups_and_fingerprints(spec)
    _,mu,sd,_=v7._read_v6_checkpoint(torch,args.prior,data,spec)
    report11,cp11,models,proof=v12._verify_v11(torch,args.v11_run,data,capture,
                                               _sha(args.prior),spec,args.device)
    if (not np.array_equal(cp11['selected']['mean'].numpy(),mu) or
        not np.array_equal(cp11['selected']['std'].numpy(),sd)):
        raise ValueError('V17_NORMALIZER_MISMATCH')
    report14,post,model,verified14=v15._check_prior14(args.v14_run,proof,capture,
                  _sha(args.prior),data,args.focus_group,torch,spec,mu,sd,args.device)
    if abs(args.reserve-report14['config']['reserve'])>1e-9:
        raise ValueError('V17_RESERVE_MISMATCH')
    report15,sha15=v16._checked_prior15(args.v15_run,verified14,capture,
                    _sha(args.prior),args.focus_group,data,args.reserve)
    report16,sha16=_checked_v16(args.v16_run,sha15,capture,verified14,data,
                               _sha(args.prior),args.focus_group,args.reserve)
    prompt_map=v12._deck_prompts(data)
    geom=v16._geometry(torch,model,post,spec,mu,sd,args.device,args.focus_group,
                       args.reserve,args.max_gradient_cases,prompt_map)
    if (int(geom['mask'].sum())!=report16['baseline_protected_train'] or
        abs(geom['initial_deficit']-report16['baseline_margin_deficit'])>1e-6 or
        [geom['rows'][i]['id'] for i in geom['deficient']]!=report16['remaining_deficient_cases']):
        raise ValueError('V17_V16_GEOMETRY_MISMATCH')
    before=v9._copy_state(model)
    trials,checks,gradient_geometry=_finite_difference_audit(torch,model,geom,spec,mu,sd,
                                                           args.device,args.steps)
    decision=_interpret(trials,checks,args.steps)
    model.load_state_dict(before,strict=True)
    if (any(not torch.equal(model.state_dict()[name].detach().cpu(),val.detach().cpu())
            for name,val in before.items()) or
        not np.array_equal(v9._correct_train(torch,model,spec,mu,sd,args.device),geom['mask'])):
        raise ValueError('V17_AUDIT_MODIFIED_WEIGHTS_OR_TRAIN_LEDGER')
    report={'schema':SCHEMA,'status':'DIAGNOSTIC_COMPLETE_NO_PROMOTION',
       'mode':'audit','read_only':True,'hard_train_retention_unchanged':True,
       'frozen_v11_specialist':True,'original_task_bypass':'UNCHANGED_V6',
       'general_acquisition_training':False,'checkpoint_saved':False,
       'training_capture_sha256':capture,'frozen_lineage':data['probe_info']['lineage'],
       'v6_prior_sha256':_sha(args.prior),'v11_verified':proof,
       'v14_verified':verified14,'v15_report_sha256':sha15,
       'v16_report_sha256':sha16,'focus_group':args.focus_group,
       'baseline_protected_train':int(geom['mask'].sum()),
       'baseline_margin_deficit':geom['initial_deficit'],
       'deficient_cases':[geom['rows'][i]['id'] for i in geom['deficient']],
       'gradient_geometry':gradient_geometry,'decision':decision,
       'config':{'steps':args.steps,'reserve':args.reserve,
                 'max_gradient_cases':args.max_gradient_cases},
       'TRAIN_only_gradients_and_probes':True,
       'synthetic_dev_used_for_training_or_selection':False,
       'previously_seen_test_used_for_training_or_selection':False,
       'production_weights_modified':False,'promotion_authorized':False,
       'general_production_router':'NOT_CERTIFIED'}
    joint._verify_after(data)
    out.mkdir(parents=True,exist_ok=False)
    v10._csv(out/'finite_difference_trials.csv',trials or [{'direction':'NONE_NO_DEFICIENT_CASES','sign':0,'step_norm':0,'protected_retention_pass':True}])
    v10._csv(out/'gradient_consistency.csv',checks or [{'direction':'NONE_NO_DEFICIENT_CASES','step_norm':0,'sign_agrees':True}])
    v10._csv(out/'deficient_case_gradients.csv',[{'case_id':geom['rows'][i]['id'],
            'verified_prompt':prompt_map[geom['rows'][i]['id']],
            'target':geom['rows'][i]['target'],
            'safety':float(geom['margins'][i]),
            'reserve_deficit':float(geom['deficits'][i])} for i in geom['deficient']] or [{'case_id':'NONE_NO_DEFICIENT_CASES','reserve_deficit':0}])
    (out/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True,
                                        allow_nan=False)+'\n',encoding='utf-8')
    print('V17_COMPLETE '+probe.canonical({'status':decision['status'],
          'probes':len(trials),'retention_safe':decision.get('retention_safe_probes'),
          'improvements':decision.get('retention_safe_merit_improvements'),
          'promotion':False,'report':str(out/'report.json')}),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('audit',),default='audit')
    for name in ('run','expanded-run','input','source-features','prior',
                 'v11-run','v14-run','v15-run','v16-run','output'):
        p.add_argument('--'+name,required=True,type=Path)
    p.add_argument('--device',default='cuda')
    p.add_argument('--focus-group',default=v12.FOCUS)
    p.add_argument('--steps',type=_steps,default=[.0001,.0005,.002,.01,.05])
    p.add_argument('--max-gradient-cases',type=int,default=24)
    p.add_argument('--reserve',type=float,default=.08)
    return p


def main(argv=None):
    run(parser().parse_args(argv));return 0


if __name__=='__main__':
    raise SystemExit(main())
