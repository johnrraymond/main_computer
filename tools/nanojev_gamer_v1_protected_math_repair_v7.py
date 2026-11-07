#!/usr/bin/env python3
"""V7: training-only failure atlas and targeted repair of protected v6 specialist.

Continues the exact selected v6 dual_protected_unaligned checkpoint, frozen
Qwen, LoRA, and step-500 production head. Protected inference and capture-bound
routing are imported unchanged from v6. DEV selects; previously examined TEST
is diagnostic-only. No model promotion or arbitrary-prompt routing.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path

from tools import nanojev_gamer_v1_protected_alignment_lab as v6
from tools import nanojev_gamer_v1_joint_head_repair as joint
from tools import nanojev_gamer_v1_head_only_lab as lab
from tools import nanojev_gamer_v1_sign_information_probe as probe

SCHEMA = 'nanojev.gamer.v1.protected-math-repair.v7'
ARMS = ('uniform_continuation', 'zero_reversal_uniform', 'zero_reversal_focused')
REQUIRED_PRIOR = 'dual_protected_unaligned'


def _signed(v):
    return -1 if v < 0 else 1 if v > 0 else 0


def _sign_name(s):
    return {-1: 'negative', 0: 'zero', 1: 'positive'}[int(s)]


def _operand_band(v):
    a=abs(Fraction(str(v)))
    if a<100:return 'below_100'
    if a<1000:return '100_to_1000'
    return '1000_or_more'


def _magnitude_band(v):
    a = abs(Fraction(str(v)))
    if not a: return 'exact_zero'
    if a < Fraction(1, 20): return 'below_0.05'
    if a < 1: return '0.05_to_1'
    if a <= 2: return '1_to_2'
    return 'above_2'


def _split_arrays(spec, split):
    """Return aligned rows/features/targets/predictions; never use TEST for fitting."""
    if split == 'train':
        features, old, y, rows, routes = spec['train_synthetic']
    elif split in ('dev', 'test'):
        rows, all_targets, all_old, features, indices, routes = spec[split + '_synthetic']
        import numpy as np
        ix = np.asarray(indices, dtype=np.int64)
        rows = [rows[int(i)] for i in ix]
        y = np.asarray(all_targets)[ix]
        old = np.asarray(all_old)[ix]
    else:
        raise ValueError('V7_INVALID_SPLIT')
    if not (len(rows) == len(features) == len(y) == len(old) == len(routes)):
        raise ValueError('V7_SPLIT_FEATURE_LENGTH_MISMATCH')
    return rows, features, old, y, routes


def _case_atlas(torch, net, spec, mu, sigma, device, split):
    """Strictly descriptive: per-prompt errors, abstention, confidence, exact target."""
    import numpy as np
    rows, x, old, y, route = _split_arrays(spec, split)
    cls, pred, delta = v6._predict(torch, net, x, old, route, mu, sigma,
                                  device, return_delta=True)
    original_cls = lab._classify_continuous(old)
    logits = joint._baseline_logits(old) + delta[:, 1:]
    margins = np.sort(logits, axis=1)[:, -1] - np.sort(logits, axis=1)[:, -2]
    result = []
    for i, row in enumerate(rows):
        exact = Fraction(str(row['target']))
        truth = _signed(exact)
        surface = row['surface']
        admitted = int(route[i]) in (0, 1)
        if admitted != (surface in ('source_native', 'spine_difference')):
            raise ValueError('V7_ATLAS_ROUTE_SURFACE_MISMATCH')
        center = None
        operand_max = None
        if row['family']=='signed_offset':
            parts=row['group'].split('.')
            if len(parts)!=3 or parts[0]!=split or parts[1]!='offset':
                raise ValueError('V7_INVALID_SIGNED_OFFSET_GROUP_ID')
            center=probe._center(split,int(parts[2]))
            operand_max=max(abs(center),abs(center+exact))
        result.append({
            'operand_center':str(center) if center is not None else None,
            'operand_max_abs':str(operand_max) if operand_max is not None else None,
            'operand_band':_operand_band(operand_max) if operand_max is not None else 'out_of_family',
            'id': row['id'], 'group': row['group'], 'split': split,
            'family': row['family'], 'surface': surface,
            'prompt_sha256': row['prompt_sha256'],
            'target_fraction': str(exact), 'target_sign': truth,
            'target_sign_name': _sign_name(truth),
            'magnitude_band': _magnitude_band(exact),
            'baseline_sign': int(original_cls[i]), 'predicted_sign': int(cls[i]),
            'baseline_value': float(old[i]), 'predicted_value': float(pred[i]),
            'correct': int(cls[i] == truth),
            'original_correct': int(original_cls[i] == truth),
            'zero_miss': int(truth == 0 and cls[i] != 0),
            'nonzero_as_zero': int(truth != 0 and cls[i] == 0),
            'wrong_direction': int(truth != 0 and cls[i] == -truth),
            'correction_admitted': int(admitted),
            'correction_applied': int(np.any(delta[i] != 0)),
            'decision_margin': float(margins[i]),
            'residual_signed_log': float(delta[i, 0]),
            'residual_class_max': float(np.max(np.abs(delta[i, 1:]))),
        })
    # Uncertified OOD cases must keep exactly their original predictions.
    for r in result:
        if not r['correction_admitted'] and (r['correction_applied'] or
             r['predicted_value'] != r['baseline_value'] or
             r['predicted_sign'] != r['baseline_sign']):
            raise ValueError('V7_UNCERTIFIED_TASK_CHANGED')
    return result


def _group_atlas(cases):
    grouped = defaultdict(list)
    for row in cases:
        grouped[row['group']].append(row)
    records = []
    for key, members in sorted(grouped.items()):
        if len(members) != 6 or len({r['split'] for r in members}) != 1:
            raise ValueError('V7_INVALID_SIX_CASE_GROUP')
        surface_map = defaultdict(dict)
        for r in members:
            sign = r['target_sign']
            if sign in surface_map[r['surface']]:
                raise ValueError('V7_DUPLICATE_GROUP_SIGN')
            surface_map[r['surface']][sign] = r
        if (len(surface_map) != 2 or
            any(set(part) != {-1, 0, 1} for part in surface_map.values())):
            raise ValueError('V7_INCOMPLETE_SIGN_TRIPLE')
        names = sorted(surface_map)
        left, right = (surface_map[name] for name in names)
        if any(left[s]['target_fraction'] != right[s]['target_fraction']
               for s in (-1, 0, 1)):
            raise ValueError('V7_MISMATCHED_MATHEMATICAL_TARGETS')
        good = [r for r in members if r['correct']]
        zero_misses = sum(r['zero_miss'] for r in members)
        direction_misses = sum((r['target_sign'] != 0) and not r['correct']
                               for r in members)
        pair_both = sum(left[s]['correct'] and right[s]['correct']
                        for s in (-1, 0, 1))
        agreement = sum(left[s]['predicted_sign'] == right[s]['predicted_sign']
                        for s in (-1, 0, 1))
        triple_left = int(all(left[s]['correct'] for s in (-1, 0, 1)))
        triple_right = int(all(right[s]['correct'] for s in (-1, 0, 1)))
        # Magnitude is exact rational, not float threshold classification.
        sign_target = abs(Fraction(left[1]['target_fraction']))
        records.append({'group': key, 'split': members[0]['split'],
                        'family': members[0]['family'],
                        'surfaces': '+'.join(names),
                        'magnitude_band': _magnitude_band(sign_target),
                        'positive_offset_fraction': str(sign_target),
                        'correct_cases': len(good),
                        'zero_misses': zero_misses,
                        'nonzero_misses': direction_misses,
                        'reversal_miss': int(direction_misses > 0),
                        'surface_disagreement_pairs': 3 - agreement,
                        'both_correct_pairs': pair_both,
                        'left_complete_triple': triple_left,
                        'right_complete_triple': triple_right,
                        'complete_six': int(len(good) == 6),
                        'all_corrections_denied': int(all(
                            not r['correction_admitted'] for r in members))})
    return records


def _atlas_summary(cases, groups):
    by_surface_sign = defaultdict(lambda: {'total': 0, 'correct': 0,
                                            'zero_misses': 0,
                                            'wrong_direction': 0,
                                            'nonzero_as_zero': 0})
    by_magnitude = Counter()
    for row in cases:
        rec = by_surface_sign[row['surface'] + '/' + row['target_sign_name']]
        rec['total'] += 1; rec['correct'] += row['correct']
        rec['zero_misses'] += row['zero_miss']
        rec['wrong_direction'] += row['wrong_direction']
        rec['nonzero_as_zero'] += row['nonzero_as_zero']
        by_magnitude[(row['surface'], row['magnitude_band'], 'total')] += 1
        by_magnitude[(row['surface'], row['magnitude_band'], 'correct')] += row['correct']
    return {'cases': len(cases), 'groups': len(groups),
        'correct_cases': sum(r['correct'] for r in cases),
        'complete_six_groups': sum(r['complete_six'] for r in groups),
        'zero_misses': sum(r['zero_miss'] for r in cases),
        'reversal_miss_groups': sum(r['reversal_miss'] for r in groups),
        'group_surface_disagreements': sum(r['surface_disagreement_pairs'] for r in groups),
        'by_surface_and_sign': dict(by_surface_sign),
        'by_surface_and_magnitude': [
            {'surface': surface, 'magnitude_band': band,
             'total': by_magnitude[(surface, band, 'total')],
             'correct': by_magnitude[(surface, band, 'correct')]}
            for surface, band, field in sorted(by_magnitude)
            if field == 'total']}


def _training_group_weights(train_groups, groups, *, uniform_fraction=0.5):
    """Adaptive sampling uses TRAIN atlas only; every group retains probability."""
    import numpy as np
    if len(train_groups) != len(groups):
        raise ValueError('V7_GROUP_COUNT_MISMATCH')
    # The caller supplies records in the verified complete-group order.
    if len({g['group'] for g in train_groups}) != len(groups):
        raise ValueError('V7_DUPLICATE_TRAIN_GROUP')
    raw = np.asarray([
        1.0 + 2.0*g['zero_misses'] + 1.5*g['nonzero_misses'] +
        2.0*g['reversal_miss'] + 0.75*g['surface_disagreement_pairs']
        for g in train_groups], dtype=np.float64)
    raw = np.clip(raw, 1., 16.)
    p = uniform_fraction / len(raw) + (1-uniform_fraction)*raw/raw.sum()
    if not np.isclose(p.sum(),1.) or np.any(p <= 0):
        raise ValueError('V7_INVALID_TRAIN_SAMPLING')
    return p


def _targeted_mathematical_loss(torch, net, x, old, target, *, zero_weight=2.,
                                reversal_weight=1.):
    """Hard sign margins and exact-zero/reversal numerical constraints.

    x is concatenated COMPLETE six-case groups in (-,0,+), (-,0,+) order.
    The target is exact mathematical supervision converted to float only for
    differentiable regression; labels are computed from its *actual* sign.
    """
    from torch.nn import functional as F
    if x.ndim != 2 or len(x) < 6 or len(x)%6:
        raise ValueError('V7_TARGETED_REQUIRES_SIX_CASE_BATCH')
    n = len(x)//6
    labels = torch.sign(target).long()+1
    if not bool(torch.all(labels.reshape(n,6) == torch.tensor(
            [[0,1,2,0,1,2]],device=x.device).expand(n,-1))):
        raise ValueError('V7_INCORRECT_SIGN_GROUP_ORDER')
    residual = net(x)
    logits = joint._head_logits(torch, old, residual)
    positives = logits.gather(1, labels[:,None])[:,0]
    wrong = logits.clone()
    wrong.scatter_(1, labels[:,None], -1e5)
    margin = positives - wrong.max(dim=1).values
    required = torch.where(labels==1,1.0,0.75)
    hinge = F.relu(required-margin).square()
    zero_class = hinge[labels==1].mean()
    nonzero_class = hinge[labels!=1].mean()
    log_value = torch.sign(old)*torch.log1p(old.abs())+residual[:,0]
    by_group = log_value.reshape(n,6)
    zero_numeric = (by_group[:,1].square()+by_group[:,4].square()).mean()/2
    reversal = ((by_group[:,0]+by_group[:,2]).square()+
                (by_group[:,3]+by_group[:,5]).square()).mean()/2
    pair = ((by_group[:,:3]-by_group[:,3:]).square()).mean()
    loss = zero_weight*(zero_class+0.25*zero_numeric) + nonzero_class + \
           reversal_weight*(reversal+0.25*pair)
    return loss, {'zero_class':float(zero_class.detach()),
                  'zero_signed_log':float(zero_numeric.detach()),
                  'nonzero_class':float(nonzero_class.detach()),
                  'sign_reversal':float(reversal.detach()),
                  'matched_numeric':float(pair.detach())}


def _read_v6_checkpoint(torch, path, data, spec):
    import numpy as np
    path=Path(path)
    if not path.is_file():
        raise FileNotFoundError('V7_SELECTED_V6_CHECKPOINT_MISSING: '+str(path))
    cp=torch.load(path,map_location='cpu',weights_only=True)
    report_path=path.parent/'report.json'
    if not report_path.is_file():
        raise FileNotFoundError('V7_V6_SELECTED_REPORT_MISSING: '+str(report_path))
    prior_report=json.loads(report_path.read_text(encoding='utf-8'))
    selected=next((r for r in prior_report.get('candidates',[])
                   if r.get('id')==REQUIRED_PRIOR),None)
    if (prior_report.get('schema')!=v6.SCHEMA or
        prior_report.get('status')!='EXPERIMENT_COMPLETE_NO_PROMOTION' or
        prior_report.get('selected_candidate')!=REQUIRED_PRIOR or
        not selected or selected.get('selected_step')!=cp.get('selected_step') or
        prior_report.get('synthetic_dataset_sha256')!=cp.get('synthetic_dataset_sha256') or
        prior_report.get('lineage')!=cp.get('frozen_lineage')):
        raise ValueError('V7_PRIOR_NOT_VERIFIED_V6_SELECTED_WINNER')
    if (cp.get('schema')!=v6.SCHEMA or
        cp.get('arm')!=REQUIRED_PRIOR or
        cp.get('status')!='DISPOSABLE_NON_PRODUCTION' or
        cp.get('promotion_authorized') is not False or
        cp.get('protected_original_passthrough') is not True or
        cp.get('correction_policy')!='CAPTURE_BOUND_FAIL_CLOSED_NO_PRODUCTION_ROUTER'):
        raise ValueError('V7_PRIOR_NOT_SELECTED_PROTECTED_UNALIGNED_V6')
    if (cp['frozen_lineage'] != data['probe_info']['lineage'] or
        cp['original_train_cache_sha256']!=data['train_cache']['sha256'] or
        cp['synthetic_dataset_sha256']!=data['probe_info']['dataset_sha256']):
        raise ValueError('V7_SOURCE_FINGERPRINT_CHANGED')
    for field, value in (('max_signed_log_correction',v6.MAX_SIGNED_LOG_CORRECTION),
                         ('max_class_logit_correction',v6.MAX_CLASS_LOGIT_CORRECTION),
                         ('min_class_change_margin',v6.MIN_CLASS_CHANGE_MARGIN)):
        if cp.get(field)!=value:
            raise ValueError('V7_PRIOR_PROTECTION_POLICY_CHANGED: '+field)
    sx, _, _, _ = spec['train_source']
    tx = spec['train_synthetic'][0]
    expected_mu,expected_sd=joint._normalization(sx,tx)
    mu=np.asarray(cp['mean'],dtype=np.float32)
    sd=np.asarray(cp['std'],dtype=np.float32)
    if (mu.shape!=(1024,) or sd.shape!=(1024,) or
        not np.array_equal(mu,expected_mu) or not np.array_equal(sd,expected_sd)):
        raise ValueError('V7_PRIOR_NORMALIZATION_MISMATCH')
    net=v6._build_aligned_model(torch,protected=True)
    net.load_state_dict(cp['weights'],strict=True)
    return net,mu,sd,cp


def _fit_continuation(torch, spec, initial, mu, sigma, *, focused, targeted_loss=False, device,
                      steps, eval_every, seed, lr, zero_weight,
                      reversal_weight):
    import numpy as np
    torch.manual_seed(seed)
    net=v6._build_aligned_model(torch,protected=True).to(device)
    net.load_state_dict(initial,strict=True)
    sx,sold,sy,sroute=spec['train_source']
    tx,told,ty,trows,troute=spec['train_synthetic']
    groups=joint._complete_training_groups(trows,ty)
    encoded=v6._encode(tx,told,troute,mu,sigma)
    tx_tensor=torch.from_numpy(encoded).to(device)
    old_tensor=torch.as_tensor(told,device=device,dtype=torch.float32)
    target_tensor=torch.as_tensor(ty,device=device,dtype=torch.float32)
    rng=np.random.default_rng(seed)
    optimizer=torch.optim.AdamW(net.parameters(),lr=lr,weight_decay=1e-4)
    # Initialize from the audited selected v6 model, not the zero head.
    def assess(step, loss=None, losses=None):
        retention,dev=v6._evaluate(torch,net,spec,mu,sigma,device)
        rank=joint._rank(retention,dev)
        hist=joint._history_entry(step,loss,retention,dev,rank,losses)
        return rank,retention,dev,hist
    rank,retention,dev,first=assess(0)
    hist=[first]
    best={'step':0,'rank':rank,'retention':retention,'dev':dev,
          'state':{k:v.detach().cpu().clone() for k,v in net.state_dict().items()}}
    train_case=_case_atlas(torch,net,spec,mu,sigma,device,'train')
    train_groups=_group_atlas(train_case)
    group_order=[trows[positions[0]]['group'] for positions,_ in groups]
    if len(set(group_order))!=len(groups):
        raise ValueError('V7_INCOMPLETE_TRAIN_GROUP_ORDER')
    sample_prob=None
    def refresh():
        nonlocal train_case,train_groups,sample_prob
        train_case=_case_atlas(torch,net,spec,mu,sigma,device,'train')
        train_groups=_group_atlas(train_case)
        index={g['group']:g for g in train_groups}
        ordered=[index[k] for k in group_order]
        sample_prob=_training_group_weights(ordered,groups)
    refresh()
    for step in range(1,steps+1):
        net.train()
        if focused:
            choose=rng.choice(len(groups),size=4,replace=True,p=sample_prob)
        else:
            choose=rng.choice(len(groups),size=4,replace=True)
        idx=np.concatenate([groups[int(j)][0] for j in choose])
        local_triples=[(6*k+j*3,6*k+j*3+1,6*k+j*3+2)
                       for k in range(len(choose)) for j in range(2)]
        optimizer.zero_grad(set_to_none=True)
        x=tx_tensor[idx]; old=old_tensor[idx]; y=target_tensor[idx]
        synthetic,parts=joint._training_loss(torch,net,x,old,y,
                      torch.zeros(len(idx),dtype=torch.bool,device=device),
                      weight=1.,source=False)
        triple=joint._triple_loss(torch,net,x,old,local_triples)
        if targeted_loss:
            target,extras=_targeted_mathematical_loss(torch,net,x,old,y,
                  zero_weight=zero_weight,reversal_weight=reversal_weight)
        else:
            target=triple.new_zeros(())
            extras={'zero_class':0.,'zero_signed_log':0.,'nonzero_class':0.,
                    'sign_reversal':0.,'matched_numeric':0.}
        loss=synthetic+2*triple+target
        if not torch.isfinite(loss):
            raise ValueError('V7_NONFINITE_TRAINING_LOSS')
        loss.backward()
        torch.nn.utils.clip_grad_norm_(net.parameters(),1.0)
        optimizer.step()
        if step % eval_every==0 or step==steps:
            rank,r,d,h=assess(step,float(loss.detach()),
                {'synthetic':parts,'triple':float(triple.detach()),
                 'zero_reversal':extras,'focused_training':focused})
            hist.append(h)
            if rank>best['rank']:
                best={'step':step,'rank':rank,'retention':r,'dev':d,
                      'state':{k:v.detach().cpu().clone() for k,v in net.state_dict().items()}}
            if focused and step < steps:
                refresh()  # TRAIN ONLY; never inspect DEV/TEST for sampling.
    net.load_state_dict(best['state'])
    net.eval()
    original_class,original_value=v6._predict(torch,net,sx,sold,sroute,mu,sigma,device)
    if not (np.array_equal(original_value,np.asarray(sold,dtype=np.float64)) and
            np.array_equal(original_class,lab._classify_continuous(sold))):
        raise ValueError('V7_ORIGINAL_TRAIN_PASSTHROUGH_FAILED')
    return net,best,hist,{'training_groups':len(groups),'seed':seed,
        'focus_updated_from':'TRAIN_ONLY' if focused else 'NONE',
        'targeted_zero_reversal_loss':bool(targeted_loss),
        'source_train_exact_passthrough':True,'synthetic_dev_used_for_fitting':False,
        'test_used_for_fitting_or_selection':False}


def _write_csv(path, rows):
    if not rows:
        raise ValueError('V7_EMPTY_ATLAS')
    with Path(path).open('w',newline='',encoding='utf-8') as file:
        writer=csv.DictWriter(file,fieldnames=list(rows[0]))
        writer.writeheader();writer.writerows(rows)


def _outcome(torch, net, spec, mu, sigma, device, name, step, retention, dev):
    _,test=v6._evaluate(torch,net,spec,mu,sigma,device,'test_synthetic')
    meta,y,old,_,part,_=spec['test_synthetic']
    baseline=lab._group_scores(meta,y,old,lab._classify_continuous(old),part)
    gate=lab._gates(test,retention,baseline['all'])
    gate['correct_cross_format_invariance']=joint._cross_format_gate(test['cross_format'])
    gate['all_bounded_gates_pass']=bool(gate['all_bounded_gates_pass'] and
                                     gate['correct_cross_format_invariance'])
    return {'id':name,'selected_step':step,'original_dev_retention':retention,
        'dev':dev,'already_seen_test_diagnostic_only':test,'gates':gate,
        'promotion_authorized':False}


def _validate_args(args):
    for name in ('run','expanded_run','input','source_features','prior','output'):
        if getattr(args,name,None) is None:
            raise ValueError('V7_REQUIRED_ARGUMENT_MISSING: '+name)
    if (args.steps<1 or args.steps>20000 or args.eval_every<1 or args.lr<=0 or
        args.zero_weight<0 or args.reversal_weight<0 or
        not all(math.isfinite(x) for x in (args.lr,args.zero_weight,
                                            args.reversal_weight))):
        raise ValueError('V7_INVALID_CONFIGURATION')


def run(args):
    import numpy as np
    import torch
    _validate_args(args)
    if args.device.startswith('cuda') and not torch.cuda.is_available():
        raise RuntimeError('V7_CUDA_UNAVAILABLE')
    roots=[args.run,args.expanded_run,args.input,args.source_features,args.prior]
    out=lab._output(args.output,[Path(x) for x in roots])
    data,prepared=joint._prepare(args)
    spec,routes=v6._routed_profiles(data,prepared)
    net,mu,sigma,cp=_read_v6_checkpoint(torch,args.prior,data,spec)
    net=net.to(args.device)
    reference_retention,reference_dev=v6._evaluate(torch,net,spec,mu,sigma,args.device)
    if reference_retention['status']!='PASS':
        raise ValueError('V7_V6_REFERENCE_RETENTION_NOT_PASS')
    reference=_outcome(torch,net,spec,mu,sigma,args.device,'v6_reference',
                       cp['selected_step'],reference_retention,reference_dev)
    initial={k:v.detach().cpu().clone() for k,v in net.state_dict().items()}
    artifacts={};atlas_summary={}
    out.mkdir(parents=True,exist_ok=False)
    for split in ('train','dev','test'):
        cases=_case_atlas(torch,net,spec,mu,sigma,args.device,split)
        groups=_group_atlas(cases)
        name='previously_seen_test' if split=='test' else split
        _write_csv(out/f'v6_reference_cases_{name}.csv',cases)
        _write_csv(out/f'v6_reference_groups_{name}.csv',groups)
        atlas_summary[split]=_atlas_summary(cases,groups)
    print('V7_ATLAS '+probe.canonical({'reference_step':cp['selected_step'],
        'dev':{k:atlas_summary['dev'][k] for k in
               ('zero_misses','reversal_miss_groups','complete_six_groups')},
        'previously_seen_test_diagnostic_only':{k:atlas_summary['test'][k] for k in
               ('zero_misses','reversal_miss_groups','complete_six_groups')}}),flush=True)
    outcomes=[reference]
    states={'v6_reference':initial}
    histories={}
    if args.mode=='repair':
        for arm in ARMS:
            net,best,hist,training=_fit_continuation(torch,spec,initial,mu,sigma,
                focused=(arm=='zero_reversal_focused'),
                targeted_loss=(arm!='uniform_continuation'),device=args.device,
                steps=args.steps,eval_every=args.eval_every,seed=args.seed,
                lr=args.lr,zero_weight=args.zero_weight,
                reversal_weight=args.reversal_weight)
            outcome=_outcome(torch,net,spec,mu,sigma,args.device,arm,best['step'],
                             best['retention'],best['dev'])
            outcome['training']=training
            outcomes.append(outcome)
            histories[arm]=hist
            states[arm]={k:v.detach().cpu().clone() for k,v in net.state_dict().items()}
            for split in ('dev','test'):
                cases=_case_atlas(torch,net,spec,mu,sigma,args.device,split)
                groups=_group_atlas(cases)
                name='previously_seen_test' if split=='test' else split
                _write_csv(out/f'{arm}_cases_{name}.csv',cases)
                _write_csv(out/f'{arm}_groups_{name}.csv',groups)
            print('V7_REPAIR '+probe.canonical({'architecture':arm,
                'selected_step':best['step'],'dev_sign':outcome['dev']['all']['balanced_sign_accuracy'],
                'dev_six':outcome['dev']['cross_format']['complete_six_case_groups'],
                'previously_seen_test_sign':outcome['already_seen_test_diagnostic_only']['all']['balanced_sign_accuracy'],
                'test_six':outcome['already_seen_test_diagnostic_only']['cross_format']['complete_six_case_groups'],
                'retention':outcome['original_dev_retention']['status']}),flush=True)
    joint._verify_after(data)
    winner=max(outcomes,key=lambda x:joint._rank(x['original_dev_retention'],x['dev']))
    report={'schema':SCHEMA,'status':'EXPERIMENT_COMPLETE_NO_PROMOTION',
        'mode':args.mode,'v6_reference':str(Path(args.prior).resolve()),
        'reference_arm_verified':REQUIRED_PRIOR,
        'v6_reference_selected_step':cp['selected_step'],
        'selected_candidate':winner['id'], 'candidates':outcomes,
        'atlas_summary':atlas_summary,
        'training_ablation':{'arms':list(ARMS) if args.mode=='repair' else [],
            'same_initial_v6_weights':True,'same_optimizer_data_seed_steps':True,
            'uniform_to_zero_reversal_uniform_is_loss_only':True,
            'zero_reversal_uniform_to_focused_is_sampling_only':True,
            'training_atlas_from':'TRAIN_ONLY',
            'development_used_for':'CHECKPOINT_SELECTION_ONLY',
            'previously_seen_test':'DIAGNOSTIC_ONLY_NOT_VIRGIN'},
        'checkpoint_history':histories,
        'original_dev_used_for_fitting':False,'synthetic_test_used_for_fitting':False,
        'production_weights_modified':False,'lora_frozen':True,
        'qwen_frozen':True,'production_head_frozen':True,
        'general_production_router':'NOT_CERTIFIED',
        'original_task_protection':'EXACT_V6_HARD_BYPASS_UNCHANGED',
        'protection_gates_unchanged':True,'production_promotion':False,
        'config':{'steps':args.steps,'eval_every':args.eval_every,'lr':args.lr,
                  'seed':args.seed,'zero_weight':args.zero_weight,
                  'reversal_weight':args.reversal_weight},
        'lineage':data['probe_info']['lineage'],
        'synthetic_sha256':data['probe_info']['dataset_sha256']}
    (out/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True,
        allow_nan=False)+'\n',encoding='utf-8')
    _write_csv(out/'candidate_comparison.csv',[
        {'candidate':o['id'],'step':o['selected_step'],
         'dev_sign':o['dev']['all']['balanced_sign_accuracy'],
         'dev_zero_recall':o['dev']['all']['zero_recall'],
         'dev_triples':o['dev']['all']['triple_accuracy'],
         'dev_complete_six':o['dev']['cross_format']['complete_six_case_groups'],
         'test_sign_diagnostic_only':o['already_seen_test_diagnostic_only']['all']['balanced_sign_accuracy'],
         'test_triples_diagnostic_only':o['already_seen_test_diagnostic_only']['all']['triple_accuracy'],
         'test_complete_six_diagnostic_only':o['already_seen_test_diagnostic_only']['cross_format']['complete_six_case_groups'],
         'original_correct_retained':o['original_dev_retention']['original_correct_kept_fraction'],
         'all_gates':o['gates']['all_bounded_gates_pass']}
        for o in outcomes])
    if args.mode=='repair':
        rows=[]
        for arm,history in histories.items():
            for h in history:
                rows.append({'arm':arm,'step':h['step'],
                    'retention':h['source_retention'],
                    'dev_sign':h['synthetic_dev_balanced_sign'],
                    'dev_zero_recall':h['synthetic_dev_zero_recall'],
                    'dev_triples':h['synthetic_dev_triples'],
                    'dev_six':(h['synthetic_dev_cross_format'] or {}).get('complete_six_case_groups'),
                    'dev_both_correct':(h['synthetic_dev_cross_format'] or {}).get('both_correct_fraction')})
        _write_csv(out/'checkpoint_history.csv',rows)
        torch.save({'schema':SCHEMA,'status':'DISPOSABLE_NON_PRODUCTION',
            'arm':winner['id'],'weights':states[winner['id']],
            'mean':torch.as_tensor(mu),'std':torch.as_tensor(sigma),
            'prior_v6':str(Path(args.prior).resolve()),
            'source_lineage':data['probe_info']['lineage'],
            'selected_step':winner['selected_step'],
            'capture_bound_router_only':True,'protected_original_passthrough':True,
            'promotion_authorized':False},out/'disposable_candidate.pt')
    print(probe.canonical({'status':report['status'],'mode':args.mode,
       'selected':winner['id'],'promotion':False,'report':str(out/'report.json'),
       'original_dev_used_for_fitting':False,
       'production_weights_modified':False}),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('atlas','repair'),default='atlas')
    p.add_argument('--run',type=Path)
    p.add_argument('--expanded-run',type=Path)
    p.add_argument('--input',type=Path)
    p.add_argument('--source-features',type=Path)
    p.add_argument('--prior',type=Path,help='Selected v6 disposable_candidate.pt')
    p.add_argument('--output',type=Path)
    p.add_argument('--device',default='cuda')
    p.add_argument('--steps',type=int,default=300)
    p.add_argument('--eval-every',type=int,default=30)
    p.add_argument('--lr',type=float,default=0.001)
    p.add_argument('--seed',type=int,default=49)
    p.add_argument('--zero-weight',type=float,default=2.)
    p.add_argument('--reversal-weight',type=float,default=1.)
    return p


def main(argv=None):
    run(parser().parse_args(argv))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
