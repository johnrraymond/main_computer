#!/usr/bin/env python3
"""Read-only GAMER exact-oracle C0-boundary and causal sign-transfer diagnostic.

Reuses the archived 324-question frozen Qwen/LoRA oracle capture.  NEVER
re-captures, trains/promotes a production head, updates LoRA, or changes inputs.
Measures the actual C0 LayerNorm -> encoder -> mid -> output pathway and
performs *synthetic activation* interventions at its exact 102400-d input.
The interpolated activations are not certified mathematical questions; a
response to a perturbation is evidence of head sensitivity, not computation.
"""
from __future__ import annotations

from collections import defaultdict
from fractions import Fraction
import json
from pathlib import Path

import numpy as np

from tools import nanojev_gamer_v1_sign_information_probe as oracle
from tools import nanojev_gamer_v1_upstream_path as upstream
from tools import nanojev_gamer_v1_sufficiency as suff
from tools import nanojev_gamer_v1_handoff as gamer

SCHEMA = 'nanojev.gamer.v1.c0-boundary-causal-sign.v1'


def _source(run):
    root = Path(run).resolve()
    report_path = root/'final_report.json'
    if not report_path.is_file():
        raise FileNotFoundError('GAMER_C0_UPSTREAM_REPORT_MISSING:'+str(report_path))
    report = json.loads(report_path.read_text(encoding='utf-8'))
    if report.get('schema') != upstream.SCHEMA or report.get('status') != 'COMPLETE_READ_ONLY_WITH_EXACT_ORACLE':
        raise ValueError('GAMER_C0_REQUIRES_COMPLETED_UPSTREAM_ORACLE')
    capture = Path(report['oracle_capture_dir']).resolve()
    if capture != (root/'oracle-capture').resolve():
        raise ValueError('GAMER_C0_ORACLE_CAPTURE_OUTSIDE_UPSTREAM_RUN')
    analysis = capture/'analysis.json'
    if suff._digest(analysis) != report['oracle_analysis_sha256']:
        raise ValueError('GAMER_C0_ORACLE_ANALYSIS_CHANGED')
    original_sign = Path(report['sign_only_source']).resolve()/'final_report.json'
    if suff._digest(original_sign) != report['source_sign_only_sha256']:
        raise ValueError('GAMER_C0_SIGN_SOURCE_CHANGED')
    meta, arrays = oracle._verify_archive(capture/oracle.CAPTURE_NAME,require_source=True)
    if meta['dataset_sha256'] != report['math_oracle']['oracle_dataset_sha256']:
        raise ValueError('GAMER_C0_ORACLE_DATASET_MISMATCH')
    if meta['lineage']['expanded_run'] != report['source_expanded']:
        raise ValueError('GAMER_C0_EXPANDED_LINEAGE_MISMATCH')
    # Traverse the complete authenticated sign-only -> balanced -> source chain;
    # the oracle's checkpoint must belong to that exact GAMER run.
    sign_record, sign_arms, exp, original_run, prior_capture, protected = upstream._source(report['sign_only_source'])
    if (str(exp) != report['source_expanded'] or
        meta['lineage']['source_run'] != str(original_run) or
        meta['lineage']['expanded_run'] != str(exp) or
        sign_record['arms'] != sign_arms):
        raise ValueError('GAMER_C0_ORACLE_SOURCE_LINEAGE_MISMATCH')
    if 'lora' not in meta['ablation'] or 'lora__final_flat' not in arrays:
        raise ValueError('GAMER_C0_LORA_BOUNDARY_MISSING')
    if meta['captures']['lora']['final_capture_parity_max_delta'] > 1e-4:
        raise ValueError('GAMER_C0_UNVERIFIED_FINAL_CAPTURE_PARITY')
    if not meta['production_head_frozen'] or meta['production_weights_modified']:
        raise ValueError('GAMER_C0_UNFROZEN_ORACLE_CAPTURE')
    return root,report,meta,arrays,capture


def _destination(root, output):
    root=Path(root).resolve()
    if output is not None:
        dest=Path(output).resolve()
        if dest.exists():
            raise FileExistsError('GAMER_C0_OUTPUT_ALREADY_EXISTS:'+str(dest))
    else:
        dest=next((root.with_name(root.name+'-c0-boundary-'+f'{i:04d}')
                   for i in range(1,10000)
                   if not root.with_name(root.name+'-c0-boundary-'+f'{i:04d}').exists()),None)
        if dest is None:raise RuntimeError('GAMER_C0_NO_NEW_REPORT_SLOT')
    if root==dest or root in dest.parents or dest in root.parents:
        raise ValueError('GAMER_C0_OUTPUT_OVERLAPS_SOURCE')
    return dest


def _triples(rows):
    by_group=defaultdict(dict)
    group_splits=defaultdict(set)
    for i,row in enumerate(rows):
        value=Fraction(row['target'])
        sign=-1 if value<0 else 1 if value>0 else 0
        key=(row['split'],row['group'],row['surface'],row['family'])
        group_splits[row['group']].add(row['split'])
        if sign in by_group[key]:raise ValueError('GAMER_C0_DUPLICATE_SIGN')
        by_group[key][sign]=i
    if any(len(s)!=1 for s in group_splits.values()):
        raise ValueError('GAMER_C0_LEAKED_GROUP')
    if any(set(triple)!={-1,0,1} for triple in by_group.values()):
        raise ValueError('GAMER_C0_INCOMPLETE_ORACLE_TRIPLE')
    splits={s:sorted([k for k in by_group if k[0]==s]) for s in ('train','dev','test')}
    if min(len(x) for x in splits.values())<2:
        raise ValueError('GAMER_C0_TOO_FEW_GROUPED_TRIPLES')
    if not all(any(k[3]=='signed_offset' for k in splits[s]) for s in splits):
        raise ValueError('GAMER_C0_MISSING_SHARED_MATH_FAMILY')
    return by_group,splits


def _load_head(meta,device):
    import torch
    from tools.nanojev_gamer_v1_expanded_head import make_expanded_head
    lineage=meta['lineage']
    cfg=lineage['configuration']
    checkpoint=Path(lineage['checkpoint_path'])
    if device.startswith('cuda') and not torch.cuda.is_available():
        raise RuntimeError('GAMER_C0_CUDA_UNAVAILABLE')
    state=torch.load(checkpoint,map_location='cpu',weights_only=True)
    if state.get('schema')!=lineage['head_schema'] or not isinstance(state.get('model'),dict):
        raise ValueError('GAMER_C0_CHECKPOINT_SCHEMA_MISMATCH')
    net=make_expanded_head(torch,102400,tuple(cfg['hidden']))
    net.load_state_dict(state['model'],strict=True)
    del state
    dtype=torch.float16 if device.startswith('cuda') else torch.float32
    net=net.to(device=device,dtype=dtype).eval()
    for p in net.parameters():p.requires_grad_(False)
    return net


def _forward(net, features, *, device, batch_size=8, stages=False):
    """Direct C0 forward, capturing boundary internals without forward hooks."""
    import torch
    from tools.nanojev_qwen_c0_compressive_train import inverse_signed_log
    x=np.asarray(features,dtype=np.float32)
    if x.ndim!=2 or x.shape[1]!=net.norm.normalized_shape[0] or not np.isfinite(x).all():
        raise ValueError('GAMER_C0_BAD_BOUNDARY_FEATURES')
    dtype=next(net.parameters()).dtype
    pred=[];norm=[];enc=[];mid=[]
    with torch.inference_mode():
        for start in range(0,len(x),batch_size):
            inp=torch.as_tensor(x[start:start+batch_size],device=device,dtype=dtype)
            n=net.norm(inp)
            h=net.enc(n)
            m=net.mid(h)
            raw=net.corrected(m).flatten()
            result=inverse_signed_log(raw.float().cpu().numpy())
            pred.extend(np.asarray(result,dtype=np.float32).tolist())
            if stages:
                norm.append(n.float().cpu().numpy().astype(np.float16))
                enc.append(h.float().cpu().numpy().astype(np.float32))
                mid.append(m.float().cpu().numpy().astype(np.float32))
    outputs=np.asarray(pred,dtype=np.float32)
    if not np.isfinite(outputs).all():raise ValueError('GAMER_C0_NONFINITE_OUTPUT')
    if not stages:return outputs,None
    return outputs,{'c0_norm':np.concatenate(norm),
                    'c0_encoder':np.concatenate(enc),'c0_mid':np.concatenate(mid)}


def _verify_baseline(pred,cached):
    a=np.asarray(pred,dtype=np.float32)
    b=np.asarray(cached,dtype=np.float32)
    if a.shape!=b.shape or not np.isfinite(b).all():
        raise ValueError('GAMER_C0_BASELINE_SHAPE_OR_VALUE_MISMATCH')
    abs_delta=np.abs(a-b)
    threshold=0.005 + 0.004*np.abs(b)
    if np.any(abs_delta>threshold):
        raise ValueError('GAMER_C0_FROZEN_HEAD_REPLAY_PARITY_FAILED:'+str(float(np.max(abs_delta))))
    return {'cases':len(a),'max_abs_delta':float(np.max(abs_delta)),
            'max_relative_to_tolerance':float(np.max(abs_delta/threshold)),
            'tolerance_abs':0.005,'tolerance_rel':0.004,'status':'EXACT_PRODUCTION_C0_BOUNDARY_REPLAY'}


def _mean_group_directions(x,triples,split_keys,positions):
    """Learn one contrast per TRAIN mathematical group, both surfaces equal-weighted."""
    by_group=defaultdict(list)
    for key in split_keys:
        pair=triples[key]
        difference=x[pair[1]]-x[pair[-1]]
        if positions is not None:
            difference=difference.reshape(100,1024)
            z=np.zeros_like(difference)
            z[positions]=difference[positions]
            difference=z.reshape(-1)
        norm=float(np.linalg.norm(difference))
        if norm>1e-10:by_group[key[1]].append(difference/norm)
    if len(by_group)<2:raise ValueError('GAMER_C0_INSUFFICIENT_INDEPENDENT_TRAIN_GROUPS')
    normalized=[]
    for vectors in by_group.values():
        direction=np.mean(vectors,axis=0)
        size=float(np.linalg.norm(direction))
        if size>1e-10:normalized.append(direction/size)
    if len(normalized)<2:raise ValueError('GAMER_C0_TRAIN_CONTRAST_DEGENERATE')
    return np.asarray(normalized,dtype=np.float32)


def _unit(v):
    z=np.asarray(v,dtype=np.float32)
    norm=float(np.linalg.norm(z))
    return z/norm if norm>1e-10 else None


def _axes(x,triples,groups,arms,*,seed=47,controls=3):
    """All axes, scales and nulls fixed by TRAIN groups; never tune on test."""
    if controls<1 or controls>32:raise ValueError('GAMER_C0_CONTROL_BUDGET_INVALID')
    rng=np.random.default_rng(seed)
    specs={}
    for arm,positions in arms.items():
        if arm!='full100' and (not isinstance(positions,(list,tuple)) or
                              not positions or any(not isinstance(i,int) or i<0 or i>=100 for i in positions)):
            raise ValueError('GAMER_C0_INVALID_ARM_POSITIONS:'+arm)
        if arm=='full100' and positions is not None:
            raise ValueError('GAMER_C0_INVALID_FULL100_POSITION_MASK')
        train_vectors=_mean_group_directions(x,triples,groups['train'],positions)
        axis=_unit(train_vectors.mean(axis=0))
        if axis is None:raise ValueError('GAMER_C0_TRAIN_SIGN_DIRECTION_DEGENERATE')
        # Norms are computed from training counterfactual pairs, independent of test.
        norms=[]
        for key in groups['train']:
            row=triples[key];difference=x[row[1]]-x[row[-1]]
            if positions is not None:
                difference=difference.reshape(100,1024)[positions].reshape(-1)
            norms.append(float(np.linalg.norm(difference)))
        step=float(np.median(norms))
        if step<=1e-10 or not np.isfinite(step):raise ValueError('GAMER_C0_DEGENERATE_TRAIN_STEP')
        nulls=[]
        for iteration in range(controls):
            candidate=None
            for attempt in range(30):
                signs=rng.choice([-1.,1.],size=(len(train_vectors),1))
                if len(set(signs.ravel()))==1:continue
                raw=(train_vectors*signs).mean(axis=0)
                # Remove the TRAIN-defined sign axis from this shuffled-control
                # direction; preserve the same feature mask and L2 stimulus.
                raw=raw-float(raw@axis)*axis
                candidate=_unit(raw)
                if candidate is not None:break
            if candidate is None:
                # Perfectly collinear training contrasts have no shuffled
                # independent null: sample in their *same masked subspace*.
                raw=np.zeros_like(axis)
                if positions is None:raw[:]=rng.standard_normal(len(raw)).astype(np.float32)
                else:raw.reshape(100,1024)[positions]=rng.standard_normal((len(positions),1024)).astype(np.float32)
                raw-=float(raw@axis)*axis
                candidate=_unit(raw)
            if candidate is None or abs(float(candidate@axis))>=1e-3:
                raise ValueError('GAMER_C0_NULL_AXIS_DEGENERATE')
            nulls.append(candidate)
        specs[arm]={'axis':axis,'nulls':nulls,'step':step,
                    'train_groups':len(train_vectors),'masked_token_positions':positions}
    return specs


def _axis_test_geometry(x,triples,groups,axis):
    family=defaultdict(list)
    for key in groups['test']:
        p=triples[key]
        delta=x[p[1]]-x[p[-1]]
        family[key[3]].append(float(delta@axis))
    return {name:{'triples':len(v),'positive_minus_negative_projection_positive':float(np.mean(np.asarray(v)>0)),
                  'median_signed_projection':float(np.median(v))}
            for name,v in sorted(family.items())}


def _response_metrics(rows,triples,groups,baseline,plus,minus):
    """Synthetic stimulus metrics, NOT accuracy on valid mathematical prompts."""
    out={}
    for fam in sorted({k[3] for k in groups['test']}):
        pairs=[triples[k] for k in groups['test'] if k[3]==fam]
        lo=np.asarray([pair[-1] for pair in pairs],dtype=int)
        zz=np.asarray([pair[0] for pair in pairs],dtype=int)
        hi=np.asarray([pair[1] for pair in pairs],dtype=int)
        plus_neg=plus[lo]-baseline[lo]
        minus_pos=baseline[hi]-minus[hi]
        zero_symmetric=(plus[zz]-baseline[zz])-(baseline[zz]-minus[zz])
        result={
            'triples':len(pairs),
            'negative_plus_increases_output':float(np.mean(plus_neg>1e-5)),
            'positive_minus_decreases_output':float(np.mean(minus_pos>1e-5)),
            'both_signed_interventions_expected_direction':float(np.mean((plus_neg>1e-5)&(minus_pos>1e-5))),
            'median_negative_plus_delta':float(np.median(plus_neg)),
            'median_positive_minus_decrease':float(np.median(minus_pos)),
            'median_zero_plus_delta':float(np.median(plus[zz]-baseline[zz])),
            'median_zero_minus_delta':float(np.median(minus[zz]-baseline[zz])),
            'median_zero_antisymmetry_error':float(np.median(np.abs(zero_symmetric))),
            'negative_plus_crosses_positive':float(np.mean(plus[lo]>0)),
            'positive_minus_crosses_negative':float(np.mean(minus[hi]<0)),
            'baseline_negative_correct':float(np.mean(baseline[lo]<0)),
            'baseline_positive_correct':float(np.mean(baseline[hi]>0)),
        }
        out[fam]=result
    return out



def _exact_endpoint_interpolations(model,x,triples,groups,baseline,*,device,head_batch):
    """Interpolate between actual +/- oracle endpoints, retaining actual zero.

    Both endpoints are genuine oracle prompts, but interiors are *not* valid
    math prompts.  No target label is assigned to an interpolated feature.
    """
    keys=groups['test']
    neg=np.asarray([triples[k][-1] for k in keys],dtype=int)
    zero=np.asarray([triples[k][0] for k in keys],dtype=int)
    pos=np.asarray([triples[k][1] for k in keys],dtype=int)
    delta=x[pos]-x[neg]
    inside=[]
    for alpha in (.25,.5,.75):
        pred,_=_forward(model,x[neg]+np.float32(alpha)*delta,
                        device=device,batch_size=head_batch)
        inside.append(pred)
    chain=np.column_stack((baseline[neg],inside[0],inside[1],inside[2],baseline[pos]))
    # Actual zero is a real independently certified case. Its displacement
    # from the synthetic midpoint prevents treating interpolation as arithmetic.
    width=np.linalg.norm(delta,axis=1)
    distance=np.linalg.norm(x[zero]-(x[neg]+x[pos])*.5,axis=1)
    normalized=np.divide(distance,width,out=np.full_like(distance,np.nan),where=width>1e-10)
    result={}
    for family in sorted({k[3] for k in keys}):
        idx=np.asarray([i for i,k in enumerate(keys) if k[3]==family],dtype=int)
        seg=chain[idx]
        geom=normalized[idx]
        result[family]={
          'triples':len(idx),
          'real_negative_endpoint_prediction_negative':float(np.mean(seg[:,0]<0)),
          'real_positive_endpoint_prediction_positive':float(np.mean(seg[:,-1]>0)),
          'real_positive_greater_than_real_negative':float(np.mean(seg[:,-1]>seg[:,0])),
          'synthetic_path_monotone_nondecreasing':float(np.mean(np.all(np.diff(seg,axis=1)>=-1e-5,axis=1))),
          'synthetic_midpoint_prediction_median':float(np.median(seg[:,2])),
          'real_zero_prediction_within_0_05':float(np.mean(np.abs(baseline[zero[idx]])<=.05)),
          'actual_zero_distance_to_synthetic_midpoint_over_endpoint_span_median':
             float(np.nanmedian(geom)) if np.isfinite(geom).any() else None,
          'note':'Only endpoints and actual zero are oracle cases. Interior interpolations have no certified mathematical target.'}
    return result


def _measured_readouts(rows,layer_features):
    results={}
    for name,x in layer_features.items():
        # Probe fitting is disposable and lives only in memory.  Train groups
        # fit; development chooses ridge; held-out mathematical groups score.
        arrays={'lora__'+name:x}
        r=oracle._evaluate_policy(rows,arrays,'lora',name,seed=47)
        results[name]={'feature_width':int(x.shape[1]),'train_groups_only':True,
                       'development_selection':r['selection'], 'test':r['test'],
                       'test_by_family':r['test_by_family'],
                       'shuffled_train_label_null_max':r['shuffled_null_max'],
                       '95pct_all_sign_and_triple_gate':r['passes_95pct_sign_and_triple_gate']}
    return results


def run_boundary(run,*,output=None,device='cuda',head_batch=8,controls=3,seed=47):
    if head_batch<1 or controls<1 or controls>32:
        raise ValueError('GAMER_C0_INVALID_EXPERIMENT_BUDGET')
    root,source,meta,arrays,capture=_source(run)
    dest=_destination(root,output)
    for folder in (Path(source['source_expanded']),Path(source['sign_only_source']),capture):
        folder=folder.resolve()
        if dest==folder or folder in dest.parents or dest in folder.parents:
            raise ValueError('GAMER_C0_OUTPUT_OVERLAPS_PROTECTED_SOURCE')
    rows=meta['dataset']
    triples,groups=_triples(rows)
    x=np.asarray(arrays['lora__final_flat'],dtype=np.float32)
    arms=json.loads((Path(source['sign_only_source'])/'final_report.json').read_text(encoding='utf-8'))['arms']
    if set(arms)!={'localized','neighbor_control','random_control','full100'}:
        raise ValueError('GAMER_C0_SIGN_CONTROL_ARMS_MISMATCH')
    model=_load_head(meta,device)
    import torch
    lineage=meta['lineage']
    ckpt=Path(lineage['checkpoint_path'])
    original_sha=lineage['checkpoint_sha256']
    upstream_sha=suff._digest(root/'final_report.json')
    capture_sha=suff._digest(capture/oracle.CAPTURE_NAME)
    print('GAMER C0 BOUNDARY / CAUSAL SIGN TRANSFER: '+str(dest),flush=True)
    print('  reuse exact-oracle '+str(len(rows))+' cached questions; frozen LoRA and C0; NO PRODUCTION TRAINING',flush=True)
    baseline,stages=_forward(model,x,device=device,batch_size=head_batch,stages=True)
    parity=_verify_baseline(baseline,arrays['production_head_predictions'])
    print('  C0 input replay parity: max_abs_delta='+str(parity['max_abs_delta']),flush=True)
    layer_readouts=_measured_readouts(rows,stages)
    for name,r in layer_readouts.items():
        print('  '+name+' heldout_sign='+str(r['test']['balanced_sign_accuracy'])+
              ' whole_triples='+str(r['test']['triple_accuracy']),flush=True)
    analyses=json.loads((capture/'analysis.json').read_text(encoding='utf-8'))
    interpolation=_exact_endpoint_interpolations(model,x,triples,groups,baseline,
                device=device,head_batch=head_batch)
    axes=_axes(x,triples,groups,arms,seed=seed,controls=controls)
    test_indices=np.asarray(sorted({i for k in groups['test'] for i in triples[k].values()}),dtype=int)
    if len(test_indices)!=len([r for r in rows if r['split']=='test']):
        raise ValueError('GAMER_C0_INCOMPLETE_TEST_INTERVENTION_COVERAGE')
    families={}
    for arm,spec in axes.items():
        print('  activation intervention '+arm+'; frozen train-defined axes='+str(controls+1),flush=True)
        geom=_axis_test_geometry(x,triples,groups,spec['axis'])
        outputs=[]
        for label,u in [('sign_direction',spec['axis'])]+[(f'null_{i:02d}',v) for i,v in enumerate(spec['nulls'])]:
            adjustment=(u*spec['step']).reshape(1,-1)
            xp=x[test_indices]+adjustment
            xm=x[test_indices]-adjustment
            plus,_=_forward(model,xp,device=device,batch_size=head_batch)
            minus,_=_forward(model,xm,device=device,batch_size=head_batch)
            joined_plus=baseline.copy();joined_minus=baseline.copy()
            joined_plus[test_indices]=plus;joined_minus[test_indices]=minus
            metric=_response_metrics(rows,triples,groups,baseline,joined_plus,joined_minus)
            outputs.append((label,metric))
        primary=outputs[0][1]
        nulls=[v for _,v in outputs[1:]]
        comparisons={}
        for family,p in primary.items():
            if not all(family in n for n in nulls):raise ValueError('GAMER_C0_CONTROL_FAMILY_MISMATCH')
            key='both_signed_interventions_expected_direction'
            control_values=[n[family][key] for n in nulls]
            comparisons[family]={'primary_fraction_both_expected':p[key],
                'null_fractions_both_expected':control_values,
                'null_max':max(control_values),
                'above_every_matched_null':bool(p[key]>max(control_values))}
        families[arm]={'train_groups':spec['train_groups'],
            'null_axis_policy':'train-group-sign-flip, orthogonalized to sign direction, same token mask and L2 step',
            'train_only_median_paired_difference_l2_step':spec['step'],
            'intervention_is_synthetic_not_mathematical_counterfactual':True,
            'test_exact_oracle_direction_projection':geom,
            'sign_direction_response':primary,'null_responses':{k:v for k,v in outputs[1:]},
            'comparisons':comparisons,'positions':spec['masked_token_positions']}
    # Replay end gate; test and training never write their original data.
    from tools import nanojev_two_game_spine_smoke as spine
    if suff._digest(ckpt)!=original_sha or \
       suff._digest(root/'final_report.json')!=upstream_sha or \
       suff._digest(capture/oracle.CAPTURE_NAME)!=capture_sha or \
       spine.adapter_identity(Path(lineage['adapter_dir']))!=lineage['adapter_sha256'] or \
       suff._digest(capture/'analysis.json')!=source['oracle_analysis_sha256'] or \
       suff._digest(Path(source['sign_only_source'])/'final_report.json')!=source['source_sign_only_sha256']:
        raise ValueError('GAMER_C0_SOURCE_CHANGED_DURING_DIAGNOSTIC')
    report={'schema':SCHEMA,'status':'COMPLETE_READ_ONLY_NO_CAUSAL_CERTIFICATION',
       'source_upstream_report':str(root/'final_report.json'),
       'source_upstream_sha256':upstream_sha,
       'source_oracle_capture':str(capture/oracle.CAPTURE_NAME),
       'source_oracle_capture_sha256':capture_sha,
       'dataset_sha256':meta['dataset_sha256'], 'frozen_head_sha256':original_sha,
       'model_lineage':lineage,
       'no_production_training_or_weight_update':True,
       'baseline_replay_parity':parity,
       'prior_frozen_production_c0_test':analyses['production_head_test'],
       'prior_layer9_probe_test':{
          k:{'feature':v['feature'],'test':v['test']} for k,v in analyses['development_selected_readouts'].items()},
       'c0_boundary_and_internal_readouts':layer_readouts,
       'real_oracle_endpoints_and_synthetic_interpolations':interpolation,
       'causal_boundary_interventions':families,
       'train_groups':len({k[1] for k in groups['train']}),
       'dev_groups':len({k[1] for k in groups['dev']}),
       'test_groups':len({k[1] for k in groups['test']}),
       'seed':seed,'null_axes_per_arm':controls,
       'interpretation':(
           'In-layer held-out linear probes measure recoverability, not model competence. '
           'Synthetic activation intervention changes do not have independently verified '
           'arithmetic targets; even if they move the frozen C0 output, they establish '
           'sensitivity to the intervention, not correct mathematical sign computation. '
           'Training/validation/test group disjointness is preserved. No repair was attempted.'),
       'promotion':'BLOCKED_DIAGNOSTIC_ONLY'}
    dest.mkdir(parents=True,exist_ok=False)
    gamer.atomic_json(dest/'final_report.json',report)
    print('GAMER C0 BOUNDARY DIAGNOSTIC COMPLETE: '+str(dest/'final_report.json'),flush=True)
    return report
