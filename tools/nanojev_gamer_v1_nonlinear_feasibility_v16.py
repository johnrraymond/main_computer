#!/usr/bin/env python3
"""V16: exact nonlinear trust-region feasibility on the existing V14 margin adapter.

The post-consolidation V14 specialist is restored and verified against the real
full-TRAIN retention ledger.  Only its feature-conditioned native/game margin
adapter can move; the V11 expanded specialist, V6 original branch, production
head, Qwen and LoRA are frozen.  Every trial is evaluated under the actual V6
confidence-gated sign inference, not a surrogate threshold.  Only TRAIN data
and previously verified prompts guide search.  DEV/TEST never guide selection.
This is a local nonlinear feasibility audit, not a proof of global impossibility.
No acquisition, original-weight modification, or production promotion occurs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from tools import nanojev_gamer_v1_margin_feasibility_v15 as v15
from tools import nanojev_gamer_v1_corrected_margin_v14 as v14
from tools import nanojev_gamer_v1_margin_consolidation_v13 as v13
from tools import nanojev_gamer_v1_boundary_isolation_v12 as v12
from tools import nanojev_gamer_v1_conflict_projection_v10 as v10
from tools import nanojev_gamer_v1_math_retention_v9 as v9
from tools import nanojev_gamer_v1_protected_math_repair_v7 as v7
from tools import nanojev_gamer_v1_protected_alignment_lab as v6
from tools import nanojev_gamer_v1_joint_head_repair as joint
from tools import nanojev_gamer_v1_head_only_lab as lab
from tools import nanojev_gamer_v1_sign_information_probe as probe

SCHEMA='nanojev.gamer.v1.nonlinear-margin-feasibility.v16'


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _radii(value):
    try:
        values=[float(t.strip()) for t in value.split(',')]
    except (ValueError,AttributeError) as exc:
        raise argparse.ArgumentTypeError('V16_INVALID_TRUST_RADII') from exc
    if (not 2<=len(values)<=10 or any(not math.isfinite(v) or v<=0 or v>10 for v in values)
            or values!=sorted(set(values))):
        raise argparse.ArgumentTypeError('V16_REQUIRE_2_TO_10_INCREASING_POSITIVE_RADII')
    return values


def _checked_prior15(folder, verified14, capture, prior_hash, focus, data, expected_reserve):
    path=Path(folder)/'report.json'
    if not path.is_file():
        raise ValueError('V16_V15_REPORT_MISSING')
    r=json.loads(path.read_text(encoding='utf-8'))
    g=r.get('geometry') or {}
    if (r.get('schema')!=v15.SCHEMA or r.get('status')!='DIAGNOSTIC_COMPLETE_NO_PROMOTION'
        or r.get('promotion_authorized') is not False
        or r.get('production_weights_modified') is not False
        or r.get('general_production_router')!='NOT_CERTIFIED'
        or r.get('synthetic_train_only_gradients') is not True
        or r.get('synthetic_dev_used_for_training') is not False
        or r.get('previously_seen_test_used_for_training_or_selection') is not False
        or r.get('training_capture_sha256')!=capture
        or r.get('v6_prior_sha256')!=prior_hash
        or r.get('frozen_lineage')!=data['probe_info']['lineage']
        or r.get('focus_group')!=focus or r.get('v14_verified')!=verified14
        or abs(float(r.get('config',{}).get('reserve',-1))-expected_reserve)>1e-9
        or not g.get('probe_is_diagnostic_only')
        or g.get('total_deficient_cases',-1)<0 or g.get('protected_cases',0)<1
        or not g.get('selected_ids') or not g.get('focus_cases')):
        raise ValueError('V16_V15_PROVENANCE_MISMATCH')
    return r, _sha(path)


def _geometry(torch, model, cp, spec, mu, sd, device, focus, reserve, max_cases, prompt_map):
    """Reuse V15 gradients; recompute case census against verified weights."""
    import numpy as np
    x,old,y,rows,routes=spec['train_synthetic']
    truth=np.sign(y).astype(np.int64)
    mask=cp['train_ledger_mask'].cpu().numpy().astype(bool)
    encoded=torch.from_numpy(v6._encode(x,old,routes,mu,sd)).to(device)
    oldt=torch.as_tensor(old,dtype=torch.float32,device=device)
    required=v13._required_margin(old,truth,reserve)
    margins,deficits=v13._margin_census(torch,model,encoded,oldt,truth,required)
    positions,deficient,focus_positions=v15._study_positions(rows,mask,deficits,focus,max_cases)
    before=v9._copy_state(model)
    model.eval()
    room_blocks=[];correction_blocks=[];activation_blocks=[]
    with torch.no_grad():
        for start in range(0,len(old),128):
            batch=encoded[start:start+128]
            baseline=model.base(batch)
            route=batch[:,-2:-1]>0.5
            z=torch.where(route,model.game(batch[:,:1024]),model.native(batch[:,:1024]))
            room=torch.clamp(v6.MAX_CLASS_LOGIT_CORRECTION-baseline[:,1:].abs(),min=0.)
            room_blocks.append(room.detach().cpu().numpy())
            correction_blocks.append((room*torch.tanh(z)).detach().cpu().numpy())
            activation_blocks.append(torch.tanh(z).abs().detach().cpu().numpy())
    room_all=np.concatenate(room_blocks)
    correction_all=np.concatenate(correction_blocks)
    activation_all=np.concatenate(activation_blocks)
    gradients=v13._affine_gradients(torch,model,before,encoded,oldt,truth,positions)
    # Restore before later checks even if a probing utility changed train/eval mode.
    model.load_state_dict(before,strict=True);model.eval()
    v15case={i:k for k,i in enumerate(positions)}
    gnorms=[float(torch.linalg.vector_norm(g)) for g in gradients]
    constraints=[]
    for i,r in enumerate(rows):
        if not mask[i]:
            continue
        entry={'id':r['id'],'group':r['group'],'surface':r['surface'],
            'target':r['target'],'prompt_sha256':r['prompt_sha256'],
            'decision_safety':float(margins[i]),'reserve_shortfall':float(deficits[i]),
            'minimum_class_logit_room':float(room_all[i].min()),
            'maximum_adapter_tanh_abs':float(activation_all[i].max()),
            'maximum_margin_adapter_abs_correction':float(abs(correction_all[i]).max()),
            'selected_for_gradient':int(i in v15case),
            'gradient_norm':gnorms[v15case[i]] if i in v15case else None}
        if i in focus_positions or deficits[i]>1e-10 or i in positions:
            if r['id'] not in prompt_map or probe.sha_text(prompt_map[r['id']])!=r['prompt_sha256']:
                raise ValueError('V16_PROMPT_FINGERPRINT_MISMATCH')
            entry['verified_prompt']=prompt_map[r['id']]
        constraints.append(entry)
    lin,direction=v15._gradient_span_feasibility(gradients,
                np.asarray([deficits[i] for i in positions],dtype=np.float64),1.)
    lin['selected_ids']=[rows[i]['id'] for i in positions]
    pair=lin.get('most_opposed_gradient_pair')
    if pair:
        pair.update(first_id=rows[positions[pair['first']]]['id'],
                    second_id=rows[positions[pair['second']]]['id'])
        for ix,name in ((pair['first'],'first'),(pair['second'],'second')):
            r=rows[positions[ix]]
            pair[name+'_group']=r['group'];pair[name+'_surface']=r['surface']
            pair[name+'_safety']=float(margins[positions[ix]])
            pair[name+'_reserve_shortfall']=float(deficits[positions[ix]])
            pair[name+'_verified_prompt']=prompt_map[r['id']]
    return {'rows':rows,'truth':truth,'mask':mask,'encoded':encoded,'old':oldt,
        'required':required,'margins':margins,'deficits':deficits,'positions':positions,
        'deficient':deficient,'focus_positions':focus_positions,'linear':lin,
        'linear_direction':direction,'gradients':gradients,'constraints':constraints,
        'anchor_weights':before,'initial_deficit':float(deficits[mask].sum()),
        'room_min_for_deficient':float(room_all[deficient].min()) if deficient else None,
        'tanh_max_for_deficient':float(activation_all[deficient].max()) if deficient else None}


def _actual_evaluation(torch,net,geom,spec,mu,sd,device, *, scope):
    """Exact full-TRAIN inference is mandatory for every candidate, including failed ones."""
    import numpy as np
    correct=v9._correct_train(torch,net,spec,mu,sd,device)
    mask=geom['mask']
    lost=mask & ~correct
    margins,deficits=v13._margin_census(torch,net,geom['encoded'],geom['old'],
                                        geom['truth'],geom['required'])
    count=float(deficits[mask].sum())
    # A candidate with numerical non-finites cannot be recorded as feasible.
    if not np.isfinite(margins).all() or not np.isfinite(deficits).all() or not math.isfinite(count):
        raise ValueError('V16_NONFINITE_NONLINEAR_PROBE')
    return {'retention_pass':not bool(lost.any()),'protected_lost':int(lost.sum()),
            'total_deficit':count,'focus_min_safety':float(margins[geom['focus_positions']].min()),
            'deficient_remaining':int((deficits[mask]>1e-8).sum()),
            'all_protected_reserves_met':bool((deficits[mask]<=1e-6).all()),
            'correct_train':int(correct.sum()),'scope':scope},margins,deficits,correct


def _gradient_step(torch,net,geom,model_state):
    """Full-adapter gradient of the nonlinear hinge merit at a candidate state."""
    net.load_state_dict(model_state,strict=True)
    net.train()
    params=list(net.parameters()); trainable=[p for p in params if p.requires_grad]
    safety=v13._class_margins(torch,net,geom['encoded'],geom['old'],geom['truth'])
    required=torch.as_tensor(geom['required'],device=safety.device,dtype=safety.dtype)
    mask=torch.as_tensor(geom['mask'],device=safety.device,dtype=torch.bool)
    shortage=torch.relu(required[mask]-safety[mask])
    loss=(shortage.square().sum()+.02*shortage.sum())
    values=torch.autograd.grad(loss,trainable,allow_unused=True)
    mapped={id(p):g for p,g in zip(trainable,values)}
    flat=torch.cat([torch.zeros(p.numel(),dtype=torch.float32)
        if mapped.get(id(p)) is None else mapped[id(p)].detach().cpu().float().reshape(-1)
        for p in params])
    net.zero_grad(set_to_none=True)
    if not bool(torch.isfinite(flat).all()):
        raise ValueError('V16_NONFINITE_MERIT_GRADIENT')
    return -flat


def _search_radius(torch,model,geom,spec,mu,sd,device,radius,max_iterations,
                   max_halvings,min_gain,record):
    """Nonlinear, radius-bounded sequential feasibility search from SAME anchor.

    Candidate directions include V15's minimum-norm simultaneous linearized
    correction and actual nonlinear reserve-loss gradients.  A point is added
    only after full TRAIN exact inference passes and aggregate protected reserve
    deficit strictly decreases.  Each radius starts from the original V14
    post-consolidation adapter to avoid sequential cross-radius leakage.
    """
    import numpy as np
    anchor=geom['anchor_weights']
    model.load_state_dict(anchor,strict=True)
    start,_,_,current_correct=_actual_evaluation(torch,model,geom,spec,mu,sd,device,scope='anchor')
    current=geom['initial_deficit']
    best=v9._copy_state(model)
    accepted=0;probed=0;status='NO_RETENTION_SAFE_IMPROVEMENT_FOUND'
    best_norm=0.;min_loss=0;within_target=False
    for iteration in range(1,max_iterations+1):
        if current<=1e-6:
            status='ALREADY_SATISFIED_AT_ANCHOR' if iteration==1 else 'EXACT_TARGET_FEASIBLE'
            break
        model.load_state_dict(best,strict=True)
        directions=[]
        if geom['linear_direction'] is not None:
            directions.append(('v15_simultaneous_linearized',geom['linear_direction']))
        directions.append(('nonlinear_full_adapter_gradient',_gradient_step(torch,model,geom,best)))
        # Refresh a minimum-norm correction in the local nonlinear region.
        if iteration>1 and geom['deficient']:
            margins,deficits=v13._margin_census(torch,model,geom['encoded'],geom['old'],
                                                 geom['truth'],geom['required'])
            indices=[i for i in geom['positions'] if deficits[i]>1e-9]
            if indices:
                gradients=v13._affine_gradients(torch,model,best,geom['encoded'],
                            geom['old'],geom['truth'],indices)
                linear,direction=v15._gradient_span_feasibility(gradients,
                            [float(deficits[i]) for i in indices],max(1.,radius))
                if direction is not None:
                    directions.append(('relinearized_deficit_constraints',direction))
        improvements=[]
        current_disp=v10._parameter_displacement(model,anchor)
        for name,direction in directions:
            norm=float(torch.linalg.vector_norm(direction))
            if norm<=1e-14:continue
            # First attempt the available radius, then halve. All displacements
            # are measured from the common, verified V14 checkpoint.
            scale=min(1.,radius/max(norm,1e-14))
            for halvings in range(max_halvings+1):
                alpha=scale*(2.**(-halvings))
                proposed=current_disp+alpha*direction
                displacement_norm=float(torch.linalg.vector_norm(proposed))
                if displacement_norm>radius*(1+1e-6):
                    # Project against spherical trust region centered at anchor.
                    proposed=proposed*(radius/displacement_norm)
                    displacement_norm=float(torch.linalg.vector_norm(proposed))
                v10._set_displacement(torch,model,anchor,proposed,1.)
                actual,_,actual_deficits,correct=_actual_evaluation(torch,model,geom,spec,
                                                       mu,sd,device,scope='trial')
                probed+=1
                improvement=current-actual['total_deficit']
                acquired_lost=int((current_correct & ~correct).sum())
                accepted_trial=(actual['retention_pass'] and acquired_lost==0
                                and improvement>min_gain)
                sampled_predicted=None;sampled_actual=None;sampled_error=None
                if iteration==1 and geom['positions']:
                    sampled_predicted=float(sum(max(0.,float(geom['deficits'][i])-
                        float(torch.dot(geom['gradients'][ix],proposed)))
                        for ix,i in enumerate(geom['positions'])))
                    sampled_actual=float(sum(actual_deficits[i] for i in geom['positions']))
                    sampled_error=sampled_actual-sampled_predicted
                record.append({'radius':radius,'iteration':iteration,'direction':name,
                    'halvings':halvings,'alpha':float(alpha),'displacement_norm':displacement_norm,
                    'sampled_first_order_predicted_shortfall':sampled_predicted,
                    'sampled_actual_nonlinear_shortfall':sampled_actual,
                    'sampled_first_order_error':sampled_error,
                    'nonlinear_deficit_before':current,
                    'nonlinear_deficit_after':actual['total_deficit'],
                    'nonlinear_improvement':improvement,
                    'newly_acquired_lost':acquired_lost,
                    'accepted':bool(accepted_trial),**actual})
                if accepted_trial:
                    improvements.append((actual['total_deficit'],name,
                                        v9._copy_state(model),displacement_norm,correct))
                model.load_state_dict(best,strict=True)
        if not improvements:break
        merit,name,best,new_norm,correct=min(improvements,key=lambda a:(a[0],a[1]))
        current=merit;best_norm=new_norm;accepted+=1
        current_correct=correct
        status='PARTIAL_EXACT_FEASIBLE_IMPROVEMENT'
        if current<=1e-6:
            status='EXACT_TARGET_FEASIBLE'
            break
    model.load_state_dict(best,strict=True)
    validated,_,_,actual=_actual_evaluation(torch,model,geom,spec,mu,sd,device,scope='final')
    if (not validated['retention_pass'] or (current_correct & ~actual).any() or
        abs(validated['total_deficit']-current)>1e-5):
        raise ValueError('V16_ACCEPTED_SOLUTION_NOT_REPRODUCIBLE')
    model.load_state_dict(anchor,strict=True)
    return {'radius':radius,'status':status,'iterations_accepted':accepted,
        'probes':probed,'initial_deficit':geom['initial_deficit'],
        'best_deficit':current,'improvement':geom['initial_deficit']-current,
        'best_displacement_norm':best_norm,'hard_full_train_retention':True,
        'target_met':status=='EXACT_TARGET_FEASIBLE',
        'focus_min_safety':validated['focus_min_safety'],
        'correct_train':validated['correct_train']},best,actual


def _validate(args,torch):
    if (args.mode!='study' or args.eval_every<1 or not 0<args.reserve<.5 or
        args.max_gradient_cases<6 or args.max_gradient_cases>64 or
        args.max_iterations<1 or args.max_iterations>100 or
        args.max_halvings<1 or args.max_halvings>16 or
        args.min_improvement<=0 or not math.isfinite(args.min_improvement) or
        not isinstance(args.trust_radii,list) or len(args.trust_radii)<2 or
        args.trust_radii!=sorted(set(args.trust_radii)) or
        any(not math.isfinite(v) or not 0<v<=10 for v in args.trust_radii)):
        raise ValueError('V16_INVALID_CONFIGURATION')
    if str(args.device).startswith('cuda') and not torch.cuda.is_available():
        raise ValueError('V16_CUDA_NOT_AVAILABLE')


def run(args):
    import numpy as np
    import torch
    _validate(args,torch)
    inputs=[args.run,args.expanded_run,args.input,args.source_features,args.prior,
            args.v11_run,args.v14_run,args.v15_run]
    out=lab._output(args.output,[Path(v) for v in inputs])
    if out.exists():raise FileExistsError(out)
    data,prepared=joint._prepare(args)
    spec,_=v6._routed_profiles(data,prepared)
    groups,capture=v9._groups_and_fingerprints(spec)
    _,mu,sd,_=v7._read_v6_checkpoint(torch,args.prior,data,spec)
    report11,cp11,models,proof=v12._verify_v11(torch,args.v11_run,data,capture,
                                                _sha(args.prior),spec,args.device)
    if not (np.array_equal(cp11['selected']['mean'].numpy(),mu) and
            np.array_equal(cp11['selected']['std'].numpy(),sd)):
        raise ValueError('V16_NORMALIZER_MISMATCH')
    report14,post,model,verified14=v15._check_prior14(args.v14_run,proof,capture,
           _sha(args.prior),data,args.focus_group,torch,spec,mu,sd,args.device)
    if (abs(args.reserve-report14['config']['reserve'])>1e-9 or
          report14['config']['margin_width']!=post['margin_width']):
        raise ValueError('V16_RESERVE_OR_MODEL_MISMATCH')
    report15,sha15=_checked_prior15(args.v15_run,verified14,capture,
                   _sha(args.prior),args.focus_group,data,args.reserve)
    prompts=v12._deck_prompts(data)
    geom=_geometry(torch,model,post,spec,mu,sd,args.device,args.focus_group,
                   args.reserve,args.max_gradient_cases,prompts)
    reported=report15['geometry']
    if (geom['linear']['selected_ids']!=reported['selected_ids'] or
        len(geom['deficient'])!=reported['total_deficient_cases'] or
        len(geom['constraints'])!=reported['protected_cases'] or
        abs(geom['initial_deficit']-reported['reserve_deficit_before'])>1e-5):
        raise ValueError('V16_V15_GRADIENT_EVIDENCE_MISMATCH')
    # Original V14 post-consolidation checkpoint always remains unchanged.
    if not all(not p.requires_grad for p in model.base.parameters()):
        raise ValueError('V16_SPECIALIST_BASE_TRAINABLE')
    original=v9._copy_state(model)
    trials=[];summaries=[];best=None;best_row=None
    for radius in args.trust_radii:
        summary,snap,correct=_search_radius(torch,model,geom,spec,mu,sd,
                 args.device,radius,args.max_iterations,args.max_halvings,
                 args.min_improvement,trials)
        summaries.append(summary)
        if (best_row is None or summary['best_deficit']<best_row['best_deficit']-1e-9):
            best_row=summary;best=snap
        print('V16_TRUST_REGION '+probe.canonical(summary),flush=True)
    model.load_state_dict(original,strict=True)
    if any(not torch.equal(model.state_dict()[k].cpu(),v.cpu())
           for k,v in original.items()):
        raise ValueError('V16_ORIGINAL_MODEL_MUTATED')
    if not np.array_equal(v9._correct_train(torch,model,spec,mu,sd,args.device),
                          geom['mask']):
        raise ValueError('V16_ORIGINAL_LEDGER_MUTATED')
    # Verify exact checkpoint against ALL hard TRAIN labels a final time.
    save_candidate=best_row is not None and best_row['improvement']>args.min_improvement
    original_retention_status='PASS'
    if save_candidate:
        model.load_state_dict(best,strict=True)
        verified,_,_,actual=_actual_evaluation(torch,model,geom,spec,mu,sd,
                                               args.device,scope='saved_candidate')
        if (not verified['retention_pass'] or
             abs(verified['total_deficit']-best_row['best_deficit'])>1e-5):
            raise ValueError('V16_CHECKPOINT_HARD_GATE_FAILED')
        best_mask=actual
        original_retention, _ =v6._evaluate(torch,model,spec,mu,sd,args.device)
        original_retention_status=original_retention['status']
        if original_retention_status!='PASS':
            raise ValueError('V16_ORIGINAL_GAME_RETENTION_FAILED')
    outcome=('ALREADY_SATISFIED_AT_ANCHOR' if geom['initial_deficit']<=1e-6 else
             'EXACT_NONLINEAR_TARGET_FEASIBLE' if best_row['target_met'] else
             'PARTIAL_FEASIBLE_IMPROVEMENT' if save_candidate else
             'NO_EXACT_IMPROVEMENT_FOUND_IN_TESTED_REGIONS')
    geometry=geom['linear']
    report={'schema':SCHEMA,'status':'DIAGNOSTIC_COMPLETE_NO_PROMOTION',
       'outcome':outcome,'search_scope':'LOCAL_NONLINEAR_TRUST_REGIONS_NOT_GLOBAL_CERTIFICATE',
       'selected_adapter':'EXISTING_V14_POST_CONSOLIDATION_NATIVE_GAME_WIDTH',
       'no_new_trainable_capacity':True,'general_acquisition_training':False,
       'hard_train_retention':'UNCHANGED_V14_ACTUAL_INFERENCE_ALL_ACQUIRED',
       'frozen_v11_specialist':True,'original_task_bypass':'UNCHANGED_V6',
       'training_capture_sha256':capture,'v6_prior_sha256':_sha(args.prior),
       'frozen_lineage':data['probe_info']['lineage'],
       'v11_verified':proof,'v14_verified':verified14,
       'v15_report_sha256':sha15,'focus_group':args.focus_group,
       'baseline_protected_train':int(geom['mask'].sum()),
       'original_retention_status':original_retention_status,
       'baseline_margin_deficit':geom['initial_deficit'],
       'minimum_deficient_case_headroom':geom['room_min_for_deficient'],
       'maximum_deficient_case_tanh_saturation':geom['tanh_max_for_deficient'],
       'remaining_deficient_cases':[geom['rows'][i]['id'] for i in geom['deficient']],
       'opposing_gradient_pair':geometry.get('most_opposed_gradient_pair'),
       'linearized':geometry,
       'radii':summaries,'best_region':best_row,
       'checkpoint_saved':bool(save_candidate),
       'config':{'trust_radii':args.trust_radii,'max_iterations':args.max_iterations,
                 'max_halvings':args.max_halvings,'reserve':args.reserve,
                 'max_gradient_cases':args.max_gradient_cases,
                 'min_improvement':args.min_improvement},
       'TRAIN_only_gradients_and_candidate_admission':True,
       'synthetic_dev_used_for_training_or_selection':False,
       'previously_seen_test_used_for_training_or_selection':False,
       'production_weights_modified':False,'promotion_authorized':False,
       'general_production_router':'NOT_CERTIFIED'}
    joint._verify_after(data)
    out.mkdir(parents=True,exist_ok=False)
    v10._csv(out/'protected_train_margin_constraints.csv',geom['constraints'])
    v10._csv(out/'nonlinear_trust_trials.csv',trials or [
        {'radius':args.trust_radii[0], 'iteration':0, 'direction':'NONE_TARGET_ALREADY_MET',
         'accepted':False, 'nonlinear_deficit_before':geom['initial_deficit'],
         'nonlinear_deficit_after':geom['initial_deficit'], 'retention_pass':True}])
    v10._csv(out/'trust_region_summary.csv',summaries)
    if save_candidate:
        torch.save({'schema':SCHEMA,'status':'DISPOSABLE_NON_PRODUCTION',
            'weights':best,'mean':torch.as_tensor(mu),'std':torch.as_tensor(sd),
            'train_capture_sha256':capture,'v14_post_sha256':verified14['v14_post_consolidation_sha256'],
            'v15_report_sha256':sha15,'frozen_lineage':data['probe_info']['lineage'],
            'margin_width':report14['config']['margin_width'],
            'capacity_width':report14['config']['capacity_width'],
            'train_ledger_mask':torch.as_tensor(best_mask),
            'hard_full_train_retention':True,'protected_original_passthrough':True,
            'original_task_bypass':'UNCHANGED_V6','promotion_authorized':False},
            out/'best_feasible_disposable_candidate.pt')
    (out/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True,
                                   allow_nan=False)+'\n',encoding='utf-8')
    print('V16_COMPLETE '+probe.canonical({'outcome':outcome,'regions':len(summaries),
        'best_deficit':best_row['best_deficit'],'checkpoint_saved':save_candidate,
        'promotion':False,'report':str(out/'report.json')}),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('study',),default='study')
    for name in ('run','expanded-run','input','source-features','prior',
                 'v11-run','v14-run','v15-run','output'):
        p.add_argument('--'+name,required=True,type=Path)
    p.add_argument('--device',default='cuda')
    p.add_argument('--focus-group',default=v12.FOCUS)
    p.add_argument('--trust-radii',type=_radii,default=[.03,.1,.25,.5,1.0])
    p.add_argument('--max-iterations',type=int,default=8)
    p.add_argument('--max-halvings',type=int,default=7)
    p.add_argument('--max-gradient-cases',type=int,default=24)
    p.add_argument('--reserve',type=float,default=.08)
    p.add_argument('--min-improvement',type=float,default=1e-7)
    p.add_argument('--eval-every',type=int,default=1)
    return p


def main(argv=None):
    run(parser().parse_args(argv));return 0


if __name__=='__main__':
    raise SystemExit(main())
