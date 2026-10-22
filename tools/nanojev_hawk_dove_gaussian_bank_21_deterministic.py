#!/usr/bin/env python3
"""Read-only, parameter-free 21-bank X -> fixture-derived Y -> Z estimator.

Bank weights ONLY predict X. Y is a mathematical consequence of X and this
fixed-payoff, fixed-total Hawk-Dove experiment, NOT a separately trained bank.
The forward estimator never reads an example's oracle X/Y/Z.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import tempfile
from decimal import Decimal
from pathlib import Path

import torch

SCHEMA = 'nanojev.hawk-dove.gaussian-bank-21.deterministic-estimate.v1'
REGION_ON = 0.50
OVERFLOW_ON = 0.75
SHAPE_KL_LIMIT = 6.0


def _quantile(values, fract):
    if not values:
        return None
    vals = sorted(values)
    pos = (len(vals)-1)*fract
    left = int(pos)
    right = min(left+1, len(vals)-1)
    return vals[left]*(1-(pos-left))+vals[right]*(pos-left)


def _weighted_median(pairs):
    """pairs: (estimate, nonnegative weight)"""
    ordered = sorted(pairs)
    total = sum(w for _,w in ordered)
    if total <= 0:
        return None
    balance = 0.
    for x,w in ordered:
        balance += w
        if balance >= total*.5:
            return x
    return ordered[-1][0]


def _bank_evidence(bank, probabilities):
    """Decode one bank from its P[L], P[interior 129], P[R] distribution."""
    p = [float(v) for v in probabilities]
    if len(p) != bank.registers+2 or any((not math.isfinite(x) or x < 0) for x in p):
        raise ValueError('DETERMINISTIC_BAD_BANK_PROBABILITIES')
    if abs(sum(p)-1.) > 5e-3:
        raise ValueError('DETERMINISTIC_NONNORMALIZED_BANK')
    l, r = p[0], p[-1]
    m = sum(p[1:-1])
    q = [v / m for v in p[1:-1]] if m > 1e-18 else None
    centers = [float(c) for c in bank.centers.detach().cpu().tolist()]
    result = {'stage_radius':float(bank.radius), 'lhs':l, 'interior':m, 'rhs':r,
              'region_gate':'lhs' if l >= OVERFLOW_ON and l > m and l > r else
                            'rhs' if r >= OVERFLOW_ON and r > m and r > l else
                            'interior' if m >= REGION_ON and m > max(l,r) else 'uncertain',
              'center':None, 'shape_kl':None, 'shape_score':0.,
              'spread':None, 'accepted':False, 'weight':0.}
    if q is None:
        return result
    center = math.fsum(w*c for w,c in zip(q,centers))
    variance = math.fsum(w*(c-center)**2 for w,c in zip(q,centers))
    spacing = float(bank.bin_spacing)
    # Fixed reference sigma is the intended oracle Gaussian geometry, not
    # an optimizable fit. No truth values enter this comparison.
    sigma = float(bank.sigma_bins)*spacing
    raw_log_reference = [-0.5*((c-center)/sigma)**2 for c in centers]
    peak = max(raw_log_reference)
    log_normalizer = peak + math.log(math.fsum(math.exp(v-peak) for v in raw_log_reference))
    kl = math.fsum(v*(math.log(v)-(log_ref-log_normalizer))
                   for v,log_ref in zip(q,raw_log_reference) if v > 0)
    kl = max(0.,kl)  # float rounding at a near-perfect match
    spread = math.sqrt(max(variance,0.))
    shape = math.exp(-kl)
    result.update(center=center,shape_kl=kl,shape_score=shape,spread=spread)
    if result['region_gate'] == 'interior' and kl <= SHAPE_KL_LIMIT:
        # The centers are NOT independent samples. This is a deterministic
        # precision/shape weighting, not a probability-calibrated posterior.
        weight = m*shape / max(sigma,1e-12)**2
        result.update(accepted=True,weight=weight)
    return result


def estimate_x(banks, distributions, *, overflow_policy="hard"):
    """Derive X solely from learned bank probabilities; NEVER access labels.

    LHS/RHS strong votes create consistency bounds; interior banks contribute
    their conditional centers weighted by precision and shape. Robust weighted
    median + compatible weighted center prevent coarse or disagreeing scales
    from forcing a wild estimate. If there is no acceptable interior: abstain.
    """
    if overflow_policy not in ("hard", "audit_ignore_hard_bounds"):
        raise ValueError("DETERMINISTIC_UNKNOWN_OVERFLOW_POLICY")
    if len(banks) != len(distributions) or len(banks) != 21:
        raise ValueError('DETERMINISTIC_WRONG_BANK_COUNT')
    evidence = [_bank_evidence(bank,p) for bank,p in zip(banks,distributions)]
    accepted = [(i,e) for i,e in enumerate(evidence) if e['accepted']]
    lower = max((e['stage_radius'] for e in evidence if e['region_gate']=='rhs'),default=None)
    upper = min((-e['stage_radius'] for e in evidence if e['region_gate']=='lhs'),default=None)
    overflow_conflict = lower is not None and upper is not None and lower >= upper
    if not accepted:
        return {'x':None,'uncertainty':None,'status':'NO_USABLE_INTERIOR',
                'confidence_kind':'heuristic_not_calibrated',
                'accepted_banks':0,'consensus_banks':0,'consensus_bank_indices':[],'effective_scales':0.,
                'overflow_bounds':{'lower':lower,'upper':upper,'contradiction':overflow_conflict},
                'banks':evidence}
    viable = []
    for i,e in accepted:
        if (overflow_policy == "audit_ignore_hard_bounds" or
            ((lower is None or e['center'] > lower) and (upper is None or e['center'] < upper))):
            viable.append((i,e))
    # Do not silently disregard contradictory overflow evidence. If all center
    # votes conflict, report abstention, not an arbitrary projected answer.
    if not viable or (overflow_conflict and overflow_policy == "hard"):
        return {'x':None,'uncertainty':None,'status':'OVERFLOW_CONFLICT',
                'confidence_kind':'heuristic_not_calibrated',
                'accepted_banks':len(accepted),'consensus_banks':0,'consensus_bank_indices':[],'effective_scales':0.,
                'overflow_bounds':{'lower':lower,'upper':upper,'contradiction':overflow_conflict},
                'banks':evidence}
    median = _weighted_median([(e['center'],e['weight']) for _,e in viable])
    # Robust coherence radius: compare each bank's *physical* Gaussian width
    # with the width of the finest (most heavily weighted) agreeing bank.
    best = max((e for _,e in viable),key=lambda e:e['weight'])
    scale = max(best['spread'],best['stage_radius']/64.,1e-9)
    compatible = [(i,e) for i,e in viable if abs(e['center']-median) <= 3*max(scale,e['spread'])]
    if not compatible:
        compatible = [max(viable,key=lambda pair:pair[1]['weight'])]
    w = math.fsum(e['weight'] for _,e in compatible)
    x = math.fsum(e['center']*(e['weight']/w) for _,e in compatible)
    # 'uncertainty' is a proxy based on each bank's width, discrepancies,
    # and shape imperfections. Correlated banks do not get 1/sqrt(N) credit.
    disagreement = math.sqrt(math.fsum((e['weight']/w)*(e['center']-x)**2
                                       for _,e in compatible))
    reference_spread = max(e['spread'] for _,e in compatible if e['weight'] >= w*.001) if any(
        e['weight'] >= w*.001 for _,e in compatible) else best['spread']
    lack_of_shape = math.fsum((e['weight']/w)*math.sqrt(max(e['shape_kl'],0.))
                              for _,e in compatible)
    uncertainty = math.sqrt(disagreement**2+reference_spread**2) * (1 + lack_of_shape)
    weight_sum_squared = math.fsum((e['weight']/w)**2 for _,e in compatible)
    n_eff = 1/max(weight_sum_squared,1e-30)
    confidence = 1/(1+uncertainty / max(abs(x),1.))
    status = 'ESTIMATED'
    if len(compatible)<len(accepted) or (overflow_conflict and overflow_policy != "hard"):
        status='ESTIMATED_WITH_DISAGREEMENT'
    return {'x':x,'uncertainty':uncertainty,'status':status,
            'confidence_kind':'heuristic_not_calibrated',
            'overflow_policy':overflow_policy,
            'relative_uncertainty_proxy':uncertainty/max(abs(x),1.),
            'confidence_score_heuristic':confidence,
            'accepted_banks':len(accepted),'consensus_banks':len(compatible),
            'consensus_bank_indices':[i+1 for i,_ in compatible],
            'effective_scales':n_eff,
            'shape_kl_consensus':math.fsum(e['weight']*e['shape_kl']/w for _,e in compatible),
            'center_disagreement':disagreement,
            'overflow_bounds':{'lower':lower,'upper':upper,'contradiction':overflow_conflict},
            'banks':evidence}


def fixture_transform(payoffs):
    """Algebraic relationship Y(X) given immutable fixture and A+B=1e6.

    X = TOTAL*HX_D + A*(HX_H-HX_D);
    Y = TOTAL*DV_D + A*(DV_H-DV_D).
    Source coefficients are not read from example labels.
    """
    from tools import nanojev_hawk_dove_million as hd
    hx_h,hx_d,dv_h,dv_d = [float(payoffs[tag]) for tag in hd.TAGS]
    dx = hx_h-hx_d
    if not math.isfinite(dx) or abs(dx)<1e-20:
        raise ValueError('DETERMINISTIC_CANNOT_DERIVE_Y_FROM_X_ZERO_SLOPE')
    return {'slope':(dv_h-dv_d)/dx,
            'x_offset':1_000_000*hx_d,
            'y_offset':1_000_000*dv_d,
            'total':1_000_000}


def predict(banks, distributions, transform, *, overflow_policy="hard"):
    estimate=estimate_x(banks,distributions,overflow_policy=overflow_policy)
    x=estimate['x']
    if x is None:
        estimate.update(y=None,z=None,z_max=None,y_uncertainty=None)
        return estimate
    y = transform['y_offset'] + transform['slope']*(x-transform['x_offset'])
    if not math.isfinite(y):
        raise FloatingPointError('DETERMINISTIC_NONFINITE_Y')
    # Z is the game choice (ARGMAX), z_max is optional numerical MAX(X,Y).
    z = 'HAWK' if x>y else 'DOVE' if x<y else 'TIE'
    estimate.update(y=y,z=z,z_max=max(x,y),
                    y_uncertainty=abs(transform['slope'])*estimate['uncertainty'],
                    y_origin='derived_from_predicted_X_and_fixed_payoff_fixture_not_Y_banks')
    return estimate


def _truth_score(p, row):
    """Oracle references are touched only after predict() returns."""
    target_x=float(row['x']);target_y=float(row['y']);true_z=row['z']
    predicted={k:p.get(k) for k in ('x','y','z','z_max','uncertainty','y_uncertainty',
                'status','accepted_banks','consensus_banks','consensus_bank_indices','effective_scales',
                'shape_kl_consensus','center_disagreement','relative_uncertainty_proxy',
                'confidence_score_heuristic','overflow_bounds')}
    predicted.update(tier=row['tier'],instance=row['instance'],
                     truth={'x':target_x,'y':target_y,'z':true_z,'z_max':max(target_x,target_y)})
    if p['x'] is not None:
        dx=p['x']-target_x;dy=p['y']-target_y
        predicted['errors']={'x_absolute':abs(dx),'x_relative':abs(dx)/max(abs(target_x),1.),
                              'y_absolute':abs(dy),'y_relative':abs(dy)/max(abs(target_y),1.),
                              'z_max_absolute':abs(p['z_max']-max(target_x,target_y)),
                              'z_correct':p['z']==true_z,
                              'x_within_uncertainty_proxy':abs(dx)<=p['uncertainty']}
    else:
        predicted['errors']=None
    predicted['bank_gate_trace']=[{'bank':i+1,'radius':e['stage_radius'],
                                  'gate':e['region_gate'],
                                  'lhs_strength':e['lhs'],
                                  'interior_strength':e['interior'],
                                  'rhs_strength':e['rhs'],
                                  'interior_center':e['center'],
                                  'shape_kl':e['shape_kl'],
                                  'weight':e['weight'],
                                  'accepted':e['accepted'],
                                  'used_in_consensus':(i+1) in p.get('consensus_bank_indices',[])}
                                 for i,e in enumerate(p['banks'])]
    return predicted


def summarize_cases(cases):
    success=[c for c in cases if c['errors'] is not None]
    report={'total':len(cases),'estimated':len(success),'abstained':len(cases)-len(success),
            'coverage_fraction':len(success)/len(cases) if cases else None,
            'abstention_reason_counts':{reason:sum(c['status']==reason for c in cases)
                                        for reason in ('NO_USABLE_INTERIOR','OVERFLOW_CONFLICT')}}
    if not success:
        report.update(x_success_rate_all_cases_5pct=0.,y_success_rate_all_cases_5pct=0.,
                      z_choice_success_rate_all_cases=0.)
        return report
    xe=[c['errors']['x_absolute'] for c in success]
    ye=[c['errors']['y_absolute'] for c in success]
    correct_x=sum(c['errors']['x_relative']<=.05 for c in success)
    correct_y=sum(c['errors']['y_relative']<=.05 for c in success)
    correct_z=sum(c['errors']['z_correct'] for c in success)
    report.update(x_mae=statistics.fmean(xe),x_median_ae=statistics.median(xe),
                  x_p90_ae=_quantile(xe,.9),y_mae=statistics.fmean(ye),
                  z_max_mae=statistics.fmean(c['errors']['z_max_absolute'] for c in success),
                  x_accuracy_5pct=correct_x/len(success),
                  y_accuracy_5pct=correct_y/len(success),
                  z_choice_accuracy=correct_z/len(success),
                  x_success_rate_all_cases_5pct=correct_x/len(cases),
                  y_success_rate_all_cases_5pct=correct_y/len(cases),
                  z_choice_success_rate_all_cases=correct_z/len(cases),
                  uncertainty_proxy_empirical_coverage=sum(c['errors']['x_within_uncertainty_proxy'] for c in success)/len(success),
                  confidence_score_mean=statistics.fmean(c['confidence_score_heuristic'] for c in success))
    return report


def select_eval_rows(rows, per_band):
    from tools import nanojev_hawk_dove_focus_1k_curriculum as focus
    from tools import nanojev_hawk_dove_gaussian_bank_21_supervision as bank_source
    subset={}
    for tier in bank_source.TIERS:
        selected=[]
        for name,_,_ in focus.BANDS:
            matching=sorted((r for r in rows[tier] if focus.band(r['x'])==name),
                            key=lambda r:r['instance'])
            if len(matching)<per_band:
                raise ValueError(f'DETERMINISTIC_INSUFFICIENT_DEV_SUPPORT:{tier}:{name}')
            # Deterministic spread, no cherry-picked positives.
            selected.extend(matching[(i*len(matching))//per_band] for i in range(per_band))
        subset[tier]=selected
    return subset


@torch.no_grad()
def evaluate_checkpoint(run_dir, *, device='cuda', dtype='float16', per_band=6,
                        allow_model_download=False, source=None, teacher=None, fixture=None,
                        show_cases=12, gate_ablation=False):
    """Rebuild frozen DEV inputs; read saved 21 bank states; do not train.

    Captures may use ground-truth Y as part of the LEGACY upstream 011 feature
    channel; that is an existing benchmark visibility limitation. The X bank
    decoder itself has no access to labels, and we flag that provenance.
    """
    from tools import nanojev_hawk_dove_gaussian_bank_21_supervision as supervision
    from tools import nanojev_hawk_dove_gaussian_joint_decoder_21_focus_1k_stable as stable
    from tools import nanojev_hawk_dove_nested90_expected as expected
    from tools import nanojev_hawk_dove_coarse_to_fine_gaussian as fine
    from tools import nanojev_hawk_dove_gaussian_register_fork as fork
    from tools import nanojev_hawk_dove_internal_curriculum_master as master

    path=Path(run_dir)
    ident=json.loads((path/'config.json').read_text(encoding='utf8'))
    if ident.get('schema')!=supervision.SCHEMA:
        raise ValueError('DETERMINISTIC_WRONG_TRAINING_IDENTITY')
    checkpoint=master.load_state(master.checked_file(path/'latest.pt'))
    if checkpoint.get('schema')!=supervision.SCHEMA or checkpoint.get('identity')!=ident:
        raise ValueError('DETERMINISTIC_CHECKPOINT_IDENTITY_DRIFT')
    args=supervision.parser().parse_args([])
    args.device=device;args.dtype=dtype;args.allow_model_download=allow_model_download
    args.source=Path(source) if source else args.source
    args.teacher=Path(teacher) if teacher else args.teacher
    args.mixture_fixture=Path(fixture) if fixture else Path(ident['mixture_fixture_path'])
    for attr in ('seed','mixture_train_per_tier','mixture_new_dev_per_tier','relative_max','hidden','batch_size'):
        if attr in ident:setattr(args,attr,ident[attr])
    if fork.fingerprint(args.source)!=ident['source_sha256'] or fork.fingerprint(args.teacher)!=ident['teacher_sha256']:
        raise ValueError('DETERMINISTIC_FROZEN_INPUT_SOURCE_DRIFT')
    payoffs,meta=expected.certified_payoffs(args.mixture_fixture,args.teacher)
    if meta['sha256']!=ident['mixture_fixture_sha256']:
        raise ValueError('DETERMINISTIC_PAYOFF_FIXTURE_DRIFT')
    info,train_rows,dev_rows=stable.build_mixture(args)
    if (args._mixture_train_sha256!=ident['mixture_generated_train_rows_sha256'] or
        args._mixture_dev_sha256!=ident['mixture_generated_dev_rows_sha256']):
        raise ValueError('DETERMINISTIC_DEV_GENERATION_IDENTITY_DRIFT')
    sample=select_eval_rows(dev_rows,per_band)
    for name in ('stage2_snapshot.pt','parent_snapshot.pt','donor_snapshot.pt'):
        raw=master.checked_file(path/name)
        sha=hashlib.sha256(raw).hexdigest()
        expected_sha={'stage2_snapshot.pt':ident['stage2_sha256'],
                      'parent_snapshot.pt':ident['original_parent_sha256'],
                      'donor_snapshot.pt':ident['donor_sha256']}[name]
        if sha!=expected_sha:raise ValueError('DETERMINISTIC_SNAPSHOT_DRIFT:'+name)
    transform=fixture_transform(payoffs)
    if per_band<1:raise ValueError('DETERMINISTIC_INVALID_SAMPLE_COUNT')
    model=fork.ex.load_current(args.source,args.device,args.dtype,args.allow_model_download,args.seed)
    original=master.load_state(master.checked_file(path/'parent_snapshot.pt'))
    donor=master.load_state(master.checked_file(path/'donor_snapshot.pt'))
    decoder,bridge=fine.init_frozen(argparse.Namespace(arm=ident['original_bridge_arm']),original,donor,model)
    with tempfile.TemporaryDirectory(prefix='nanojev-bank-deterministic-') as temp:
        cache=fork.cached.FrozenCaptureCache(Path(temp)/'capture',{
           'schema':supervision.SCHEMA+'.capture','source_sha256':ident['source_sha256'],
           'teacher_sha256':ident['teacher_sha256'],
           'max_prompt_tokens':model['config']['max_prompt_tokens']},args.cache_memory_mb)
        dev=expected.load_features(model,decoder,bridge,cache,args,sample,'dev')
    system=stable.construct_system(dev[supervision.TIERS[0]]['features'].shape[-1],
                                    master.checked_file(path/'stage2_snapshot.pt'),
                                    model['device'],args)
    for stage in supervision.STAGES:
        system.cascade.banks[str(stage)].load_state_dict(checkpoint['bank_states'][str(stage)],strict=True)
    system.eval()
    ordered_banks=[system.cascade.banks[str(stage)] for stage in supervision.STAGES]
    cases=[]
    shadow_cases=[]
    for tier in supervision.TIERS:
        batch=dev[tier]
        _,_,features=system.cascade.bank_input(batch)
        all_probs=torch.stack([bank(features)[1]['probabilities'] for bank in ordered_banks],dim=1)
        distributions=all_probs.detach().cpu().double().tolist()
        for probs,row in zip(distributions,sample[tier]):
            # PURE forward reconstruction. Row truth is used only in _truth_score.
            result=predict(ordered_banks,probs,transform)
            cases.append(_truth_score(result,row))
            if gate_ablation:
                shadow=predict(ordered_banks,probs,transform,overflow_policy="audit_ignore_hard_bounds")
                shadow_cases.append(_truth_score(shadow,row))
    by_band={}
    from tools import nanojev_hawk_dove_focus_1k_curriculum as focus
    for name,_,_ in focus.BANDS:
        by_band[name]=summarize_cases([c for c in cases if focus.band(c['truth']['x'])==name])
    summary=summarize_cases(cases)
    worst=sorted((c for c in cases if c['errors'] is not None),
                 key=lambda c:c['errors']['x_absolute'],reverse=True)
    report={'schema':SCHEMA,'status':'OK','read_only':True,'checkpoint_step':checkpoint['step'],
            'test_split_read':False,'gradient_updates':0,'additional_trainable_parameters':0,
            'x_origin':'learned_21_bank_probabilities_only',
            'y_origin':'algebraic_fixture_transform_of_predicted_X_NOT_independently_learned',
            'z_origin':'deterministic_ARGMAX_predicted_X_predicted_Y',
            'upstream_visibility_warning':'legacy 011 frozen feature capture may expose oracle Y; '
                                          'this is not a blind standalone input evaluation',
            'confidence_warning':'width/disagreement/shape proxy, NOT calibrated uncertainty; '
                                 '21 correlated banks do NOT confer 21 independent samples',
            'sample_selection':'deterministic_evenly_spaced_hashed_instances_per_tier_band',
            'samples_per_tier_per_band':per_band,
            'overall':summary,'by_x_band':by_band,
            'sample_cases':cases[:show_cases],
            'worst_x_cases':worst[:min(show_cases,len(worst))]}
    if gate_ablation:
        # A paired diagnostic only. The original report and selection remain
        # exactly unchanged. Ignore hard bounds in the shadow to determine
        # whether mistaken overflow gates are responsible for missed estimates.
        # Oracle truth participates ONLY in the subsequent score comparison.
        strict_wrong_bounds=0
        changed=0
        improved=0
        worsened=0
        tied=0
        for baseline,alternative in zip(cases,shadow_cases):
            truth=baseline['truth']['x']
            bounds=baseline['overflow_bounds']
            if ((bounds['lower'] is not None and truth <= bounds['lower']) or
                (bounds['upper'] is not None and truth >= bounds['upper'])):
                strict_wrong_bounds+=1
            old_error=baseline['errors']['x_absolute'] if baseline['errors'] else float('inf')
            new_error=alternative['errors']['x_absolute'] if alternative['errors'] else float('inf')
            if (baseline['x'] is None)!=(alternative['x'] is None) or (
                baseline['x'] is not None and alternative['x'] is not None and
                abs(baseline['x']-alternative['x'])>1e-9):
                changed+=1
            if new_error<old_error-1e-9:improved+=1
            elif new_error>old_error+1e-9:worsened+=1
            else:tied+=1
        report['gate_ablation']={
            'description':'Read-only paired diagnostic: identical bank distributions, but ignore hard LHS/RHS exclusion bounds in the shadow result. All errors and abstentions are counted. Not a production replacement.',
            'training_updates':0,
            'strict_original':summary,
            'shadow_without_hard_bounds':summarize_cases(shadow_cases),
            'shadow_by_x_band':{name:summarize_cases([c for c in shadow_cases if focus.band(c['truth']['x'])==name]) for name,_,_ in focus.BANDS},
            'paired_cases':len(cases),
            'strict_overflow_bounds_contradicted_by_oracle':strict_wrong_bounds,
            'predictions_changed':changed,
            'shadow_absolute_error_improved':improved,
            'shadow_absolute_error_worsened':worsened,
            'shadow_error_tied':tied,
            'case_examples':[{'tier':a['tier'],'truth_x':a['truth']['x'],
                              'strict_x':a['x'],'shadow_x':b['x'],
                              'strict_error':a['errors']['x_absolute'] if a['errors'] else None,
                              'shadow_error':b['errors']['x_absolute'] if b['errors'] else None,
                              'overflow_bounds':a['overflow_bounds']}
                             for a,b in zip(cases,shadow_cases)
                             if a['x'] != b['x']][:show_cases]
        }
    return report
