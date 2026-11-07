#!/usr/bin/env python3
"""Frozen GAMER C0 normalization/encoder information-preservation experiment.

Reads an authenticated, completed encoder-bypass run.  (1) Inverts C0's
LayerNorm using per-example *unlabeled* moments and the immutable affine
parameters, checking both activation and held-out trained-decoder parity.
(2) Matches probe dimension, algorithm, regularization and split across
stages using TRAIN-only PCA.  A single hyperparameter combination is chosen
from RAW C0 DEV data and locked for all other stages.  (3) Tests the frozen
encoder's actual output on exact oracle triples, separately for the unseen
family. No production updates, no interpretation of synthetic features as
mathematically certified prompts, no information-absence claims from probes.
"""
from __future__ import annotations

from fractions import Fraction
import os
import json
from pathlib import Path

import numpy as np

from tools import nanojev_gamer_v1_encoder_bypass as bypass
from tools import nanojev_gamer_v1_c0_boundary as c0
from tools import nanojev_gamer_v1_sign_information_probe as oracle
from tools import nanojev_gamer_v1_sufficiency as suff
from tools import nanojev_gamer_v1_handoff as gamer

SCHEMA = 'nanojev.gamer.v1.c0-information-preservation.v1'
RANKS = (16, 32, 64)


def _source(run):
    root=Path(run).resolve()
    f=root/'final_report.json'
    if not f.is_file():
        raise FileNotFoundError('GAMER_PRESERVATION_BYPASS_REPORT_MISSING:'+str(f))
    prior=json.loads(f.read_text(encoding='utf-8'))
    if (prior.get('schema')!=bypass.SCHEMA or
        prior.get('status')!='COMPLETE_READ_ONLY_BOUNDED_INFERENCE' or
        prior.get('baseline_parity',{}).get('max_abs_delta')!=0.0 or
        not prior.get('no_production_training_or_weight_update')):
        raise ValueError('GAMER_PRESERVATION_REQUIRES_COMPLETED_BYPASS')
    bp=Path(prior['source_boundary_report']).resolve()
    if bp.name!='final_report.json':
        raise ValueError('GAMER_PRESERVATION_INVALID_BOUNDARY_REFERENCE')
    boundary,boundary_report,upstream,up_report,meta,arrays,capture=bypass._source(bp.parent)
    from tools import nanojev_two_game_spine_smoke as spine
    lineage=meta['lineage']
    known={'c0_checkpoint_sha256':suff._digest(Path(lineage['checkpoint_path'])),
           'adapter_sha256':spine.adapter_identity(Path(lineage['adapter_dir'])),
           'oracle_capture_sha256':suff._digest(capture/oracle.CAPTURE_NAME),
           'oracle_analysis_sha256':suff._digest(capture/'analysis.json'),
           'upstream_report_sha256':suff._digest(upstream/'final_report.json'),
           'c0_boundary_report_sha256':suff._digest(boundary/'final_report.json')}
    if (prior['fingerprints']!=known or
        prior['dataset_sha256']!=meta['dataset_sha256'] or
        Path(prior['source_upstream_report']).resolve()!=upstream/'final_report.json' or
        not prior.get('readouts',{}).get('c0_raw_mean100') or
        not prior['readouts'].get('c0_raw_flat') or
        not prior['readouts'].get('enc_gelu_3')):
        raise ValueError('GAMER_PRESERVATION_BYPASS_LINEAGE_MISMATCH')
    c0._triples(meta['dataset'])
    return root,prior,meta,arrays,capture,known


def _destination(root,output):
    root=Path(root).resolve()
    if output is not None:
        dest=Path(output).resolve()
    else:
        sha=suff._digest(root/'final_report.json')[:12]
        dest=next((root.parent/('c0-preservation-'+sha+'-'+f'{i:04d}')
                   for i in range(1,10000) if not
                   (root.parent/('c0-preservation-'+sha+'-'+f'{i:04d}')).exists()),None)
    if dest is None:raise RuntimeError('GAMER_PRESERVATION_NO_OUTPUT_SLOT')
    if root==dest or root in dest.parents or dest in root.parents:
        raise ValueError('GAMER_PRESERVATION_OUTPUT_OVERLAPS_SOURCE')
    if dest.exists():raise FileExistsError('GAMER_PRESERVATION_OUTPUT_EXISTS')
    if len(dest.name)>240 or (os.name=='nt' and len(str(dest/'final_report.json'))>=240):
        raise ValueError('GAMER_PRESERVATION_OUTPUT_PATH_TOO_LONG')
    return dest


def _norm_inverse(net,raw,normalized,*,dtype):
    """Inverse of exact frozen affine LN, conditioned on individual input moments.

    C0 does not receive the removed mean/variance, so reconstruction with side
    statistics tests only whether normalization has *otherwise* damaged the
    representation.  Finite precision can prevent exact reconstruction.
    """
    a=np.asarray(raw,dtype=np.float32)
    n=np.asarray(normalized,dtype=np.float32)
    if a.ndim!=2 or a.shape!=n.shape or not np.isfinite(a).all() or not np.isfinite(n).all():
        raise ValueError('GAMER_PRESERVATION_BAD_LAYERNORM_ARRAYS')
    # The C0 forward casts the cached FP32 input to the frozen head's dtype.
    import torch
    quantized=torch.as_tensor(a,dtype=dtype).float().cpu().numpy()
    gamma=net.norm.weight.detach().float().cpu().numpy().reshape(-1)
    beta=net.norm.bias.detach().float().cpu().numpy().reshape(-1)
    if gamma.shape!=(a.shape[1],) or beta.shape!=gamma.shape:
        raise ValueError('GAMER_PRESERVATION_UNEXPECTED_NORM_AFFINE')
    if not np.isfinite(gamma).all() or not np.isfinite(beta).all():
        raise ValueError('GAMER_PRESERVATION_NONFINITE_NORM_AFFINE')
    valid=np.abs(gamma)>1e-10
    mean=np.mean(quantized,dtype=np.float64,axis=1,keepdims=True)
    variance=np.mean((quantized.astype(np.float64)-mean)**2,axis=1,keepdims=True)
    std=np.sqrt(variance+float(net.norm.eps))
    reconstructed=np.empty_like(a,dtype=np.float32)
    # Never smuggle the original input back into the inverse for zero gamma.
    reconstructed[:,~valid]=mean.astype(np.float32)
    reconstructed[:,valid]=((n[:,valid].astype(np.float64)-beta[valid]) /
        gamma[valid] * std + mean).astype(np.float32)
    error=reconstructed.astype(np.float64)-quantized.astype(np.float64)
    relrmse=float(np.sqrt(np.mean(error**2))/max(float(np.std(quantized)),1e-12))
    metrics={
        'affine_zero_or_effectively_zero_gamma_coordinates':int((~valid).sum()),
        'gamma_coordinate_count':len(valid),
        'input_forward_dtype':str(dtype),
        'per_example_statistics_injected':True,
        'c0_itself_receives_these_statistics':False,
        'max_abs_vs_forward_quantized_input':float(np.max(np.abs(error))),
        'p95_abs_vs_forward_quantized_input':float(np.percentile(np.abs(error),95)),
        'relative_rmse_vs_forward_quantized_input':relrmse,
        'max_abs_input_cast_error':float(np.max(np.abs(a-quantized))),
        'max_abs_input_mean':float(np.max(np.abs(mean))),
        'marginal_mean_std':float(np.std(mean)),
        'marginal_scale_std':float(np.std(std)),
    }
    return reconstructed,metrics


def _fixed_kernel(a,b,c,*,kind):
    """Use one TRAIN-anchored kernel for untouched train, raw test, inverse test."""
    a=np.asarray(a,dtype=np.float64)
    b=np.asarray(b,dtype=np.float64)
    c=np.asarray(c,dtype=np.float64)
    avg=np.mean(a,axis=0,keepdims=True)
    scale=np.sqrt(np.maximum(np.mean((a-avg)**2,axis=0,keepdims=True),1e-6))
    a=(a-avg)/scale; b=(b-avg)/scale; c=(c-avg)/scale
    width=a.shape[1]
    kt=a@a.T/width
    kr=b@a.T/width
    ki=c@a.T/width
    if kind=='linear':return kt,kr,ki
    if kind not in ('rbf_0.25','rbf_1.0','rbf_4.0'):
        raise ValueError('GAMER_PRESERVATION_UNRECOGNIZED_ORIGINAL_DECODER')
    dt=bypass._distance2(a,a)/width
    positive=dt[np.triu_indices(len(a),1)]
    positive=positive[positive>1e-10]
    if not len(positive):
        raise ValueError('GAMER_PRESERVATION_RBF_ON_DEGENERATE_INPUT')
    divisor=float(np.median(positive))*float(kind.split('_')[1])
    return tuple(np.exp(-v/divisor) for v in (
        dt,bypass._distance2(b,a)/width,bypass._distance2(c,a)/width))


def _decode(gram,weights):
    pred=gram@weights
    cls=np.asarray((-1,0,1),dtype=np.int8)[np.argmax(pred[:,:3],axis=1)]
    value=np.sign(pred[:,3])*np.expm1(np.minimum(np.abs(pred[:,3]),20.))
    return cls,value


def _ridge_train(kt,train_y,alpha):
    sign=np.sign(train_y)
    encoded=sign*np.log1p(np.abs(train_y))
    rhs=np.column_stack([(sign==s).astype(float) for s in (-1,0,1)]+[encoded])
    return np.linalg.solve(kt+float(alpha)*np.eye(len(kt)),rhs)


def _inverse_readout(rows,raw,rec,source_readout):
    """Frozen TRAIN-fitted original raw-mean decoder on original and inverse TEST.

    Never refit on reconstructed or test cases.  Source kind/alpha were chosen
    on the prior independent DEV split.
    """
    if raw.shape!=rec.shape or raw.shape[1]!=102400:
        raise ValueError('GAMER_PRESERVATION_INVERSE_C0_WIDTH_MISMATCH')
    split=oracle._test_partitions(rows)
    train,test=(split[s] for s in ('train','test'))
    y=np.asarray([float(Fraction(r['target'])) for r in rows],dtype=np.float64)
    rm=np.asarray(raw,dtype=np.float32).reshape(len(raw),100,1024).mean(axis=1)
    im=np.asarray(rec,dtype=np.float32).reshape(len(rec),100,1024).mean(axis=1)
    kind=source_readout['decoder']
    alpha=source_readout['development_selection']['alpha']
    kt,kr,ki=_fixed_kernel(rm[train],rm[test],im[test],kind=kind)
    weights=_ridge_train(kt,y[train],alpha)
    classes,values=_decode(kr,weights)
    inverse_classes,inverse_values=_decode(ki,weights)
    original=oracle._metrics(y[test],values,classes,rows,test)
    inverse=oracle._metrics(y[test],inverse_values,inverse_classes,rows,test)
    by_family={}
    for family in sorted({rows[int(i)]['family'] for i in test}):
        j=np.asarray([k for k,i in enumerate(test) if rows[int(i)]['family']==family],dtype=int)
        by_family[family]={
           'raw':oracle._metrics(y[test[j]],values[j],classes[j],rows,test[j]),
           'inverse':oracle._metrics(y[test[j]],inverse_values[j],inverse_classes[j],rows,test[j])}
    return {
        'raw_decoder_source':'prior-bypass-DEV-selected, refitted TRAIN only',
        'raw_decoder_kind':kind,'raw_decoder_alpha':alpha,
        'heldout_prediction_sign_agreement':float(np.mean(classes==inverse_classes)),
        'heldout_max_abs_prediction_change':float(np.max(np.abs(values-inverse_values))),
        'raw':original,'inverse_with_input_moments':inverse,'test_by_family':by_family,
        'preserves_all_test_sign_predictions':bool(np.array_equal(classes,inverse_classes))}


def _train_pca(x,train,*,max_rank=64):
    """Train-only dual PCA; returned features have exactly max_rank columns.

    Every stage receives the same feature count, including the 102400-wide
    stages. Heldout rows only enter via the frozen train-derived projection.
    """
    x=np.asarray(x,dtype=np.float32)
    a=x[train]
    if x.ndim!=2 or a.shape[0]<max_rank+1 or not np.isfinite(x).all():
        raise ValueError('GAMER_PRESERVATION_INVALID_PCA_INPUT')
    mu=a.mean(axis=0,keepdims=True)
    std=np.sqrt(np.maximum(np.mean((a-mu)**2,axis=0,keepdims=True),1e-6))
    ztrain=(a-mu)/std
    z=(x-mu)/std
    gram=np.asarray(ztrain@ztrain.T/x.shape[1],dtype=np.float64)
    gram=(gram+gram.T)/2.
    eigenvalues,unit=np.linalg.eigh(gram)
    order=np.argsort(eigenvalues)[::-1]
    keep=[i for i in order if eigenvalues[i]>max(1e-9,float(eigenvalues[order[0]])*1e-8)][:max_rank]
    out=np.zeros((len(x),max_rank),dtype=np.float32)
    if keep:
        basis=unit[:,keep]/np.sqrt(eigenvalues[keep])[None,:]
        all_gram=np.asarray(z@ztrain.T/x.shape[1],dtype=np.float64)
        out[:,:len(keep)]=np.asarray(all_gram@basis,dtype=np.float32)
    return out,{'requested_rank':max_rank,'effective_rank':len(keep),
                'selection_uses':'TRAIN_UNLABELED_ONLY','source_width':int(x.shape[1])}


def _select_matched(rows,stage_projections):
    """Choose rank/kernel/alpha on raw input DEV only; freeze for all stages."""
    split=oracle._test_partitions(rows)
    train,dev,test=(split[k] for k in ('train','dev','test'))
    y=np.asarray([float(Fraction(r['target'])) for r in rows],dtype=np.float64)
    raw=stage_projections['c0_raw_flat']
    best=None
    for rank in RANKS:
        kernels=bypass._kernels(raw[:,:rank],train,dev,test)
        for kind,(kt,kd,_) in kernels.items():
            for alpha in bypass.ALPHAS:
                selection,_,_=oracle._solve_probe(kt,kd,kd,y[train],y[dev],alphas=(alpha,))
                ranking=(selection['dev_balanced_sign_accuracy'],
                         -selection['dev_signed_mae'],-rank,
                         int(kind=='linear'),-alpha)
                if best is None or ranking>best[0]:
                    best=(ranking,rank,kind,alpha,selection)
    _,rank,kind,alpha,selection=best
    return {'feature_count':rank,'decoder':kind,'alpha':alpha,
            'selection':selection,'selected_on':'RAW_C0_TRAIN_AND_DEV_ONLY'}


def _matched(rows,projections,selection,*,seed=47):
    split=oracle._test_partitions(rows)
    train,dev,test=(split[k] for k in ('train','dev','test'))
    y=np.asarray([float(Fraction(r['target'])) for r in rows],dtype=np.float64)
    rank=selection['feature_count'];kind=selection['decoder'];alpha=selection['alpha']
    out={}
    for name,(x,pca_info) in projections.items():
        kernels=bypass._kernels(x[:,:rank],train,dev,test)
        if kind not in kernels:
            # A perfectly constant stage cannot support the original RBF
            # decoder; this is evidence of collapse, not a reason to tune.
            cls=np.zeros(len(test),dtype=np.int8)
            values=np.zeros(len(test),dtype=np.float64)
            nulls=[]
            decoder_usable=False
        else:
            kt,kd,ke=kernels[kind]
            weights=_ridge_train(kt,y[train],alpha)
            cls,values=_decode(ke,weights)
            decoder_usable=True
            nulls=[]
            for i in range(3):
                shuffled=y[train].copy()
                np.random.default_rng(seed+i).shuffle(shuffled)
                shuffled_weight=_ridge_train(kt,shuffled,alpha)
                nc,nv=_decode(ke,shuffled_weight)
                nulls.append(oracle._metrics(y[test],nv,nc,rows,test)['balanced_sign_accuracy'])
        metrics=oracle._metrics(y[test],values,cls,rows,test)
        families={}
        for family in sorted({rows[int(j)]['family'] for j in test}):
            ix=np.asarray([k for k,j in enumerate(test) if rows[int(j)]['family']==family],dtype=int)
            families[family]=oracle._metrics(y[test[ix]],values[ix],cls[ix],rows,test[ix])
        out[name]={'pca':pca_info,'feature_count':rank,'decoder_usable':decoder_usable,
                   'test':metrics,'test_by_family':families,
                   'shuffled_label_nulls':nulls,
                   'beats_all_shuffled_nulls':bool(nulls and
                       metrics['balanced_sign_accuracy']>max(nulls))}
    return out


def _decision(matched,inverse_report,production,normalization):
    raw=matched['c0_raw_flat'];norm=matched['c0_norm'];enc=matched['enc_gelu_3']
    early=raw['test']['balanced_sign_accuracy']
    late=enc['test']['balanced_sign_accuracy']
    raw_family=raw['test_by_family'].get('hawk_indifference',{})
    diff=early-late
    inversion_good=(normalization['affine_zero_or_effectively_zero_gamma_coordinates']==0 and
                    normalization['relative_rmse_vs_forward_quantized_input']<.01 and
                    inverse_report['heldout_prediction_sign_agreement']>=.99)
    if early>=.95 and raw['test']['triple_accuracy']>=.95 and late<.95:
        verdict='MATCHED_RAW_INPUT_RECOVERABLE_ENCODER_NOT_CERTIFIED'
    elif early>=.95 and late>=.95 and enc['test']['triple_accuracy']>=.95:
        verdict='MATCHED_ENCODER_SIGN_RECOVERABLE_OUTPUT_FAILURE'
    elif diff>.10 and raw['beats_all_shuffled_nulls'] and not enc['beats_all_shuffled_nulls']:
        verdict='MATCHED_ENCODER_READOUT_DEGRADATION_WITH_UPSTREAM_LIMIT'
    else:
        verdict='INSUFFICIENT_CAUSAL_SEPARATION'
    return {'bounded_answer':verdict,
            'raw_to_encoder_matched_accuracy_delta':diff,
            'raw_to_layernorm_matched_accuracy_delta':early-norm['test']['balanced_sign_accuracy'],
            'inverse_normalization_parity_demonstrated':inversion_good,
            'unseen_hawk_raw_test_accuracy':raw_family.get('balanced_sign_accuracy'),
            'production_negative_recall':production['recall']['-1'],
            'production_triple_accuracy':production['triple_accuracy'],
            'training_or_checkpoint_changes':False,
            'causal_information_destruction_certified':False,
            'interpretation':('Matched readout degradation measures accessibility under '+
                'one fixed diagnostic capacity; it does not prove destruction of '+
                'all sign information. LayerNorm inversion needs per-example '+
                'moments unavailable to downstream C0. All heldout oracle triples '+
                'and the unseen family are scored; no test-driven model selection.')}


def run_information_preservation(run,*,output=None,device='cuda',head_batch=8,seed=47):
    if head_batch<1:
        raise ValueError('GAMER_PRESERVATION_INVALID_BATCH')
    root,prior,meta,arrays,capture,known=_source(run)
    dest=_destination(root,output)
    x=np.asarray(arrays['lora__final_flat'],dtype=np.float32)
    rows=meta['dataset']
    print('GAMER C0 MATCHED INFORMATION PRESERVATION: '+str(dest),flush=True)
    print('  frozen Qwen/LoRA/C0, exact oracle, no production updates',flush=True)
    head=c0._load_head(meta,device)
    baseline,stage=bypass._features_at_every_stage(head,x,device=device,batch_size=head_batch)
    replay=c0._verify_baseline(baseline,arrays['production_head_predictions'])
    if replay['max_abs_delta']!=0.0:
        raise ValueError('GAMER_PRESERVATION_PARITY_NOT_BIT_EXACT')
    rec,norm_diag=_norm_inverse(head,x,stage['c0_norm'],dtype=next(head.parameters()).dtype)
    inverse_readout=_inverse_readout(rows,x,rec,prior['readouts']['c0_raw_mean100'])
    print('  LayerNorm inverse relative_rmse='+str(round(norm_diag['relative_rmse_vs_forward_quantized_input'],7))+
          ' heldout_sign_agreement='+str(round(inverse_readout['heldout_prediction_sign_agreement'],4)),flush=True)
    # Must compare equivalent decoder capacities, not raw mean against the
    # independently tuned full flatten-normalized representation.
    source={
      'lora_layer09_mean100':np.asarray(arrays['lora__layer_09_mean100'],dtype=np.float32),
      'lora_final_mean100':np.asarray(arrays['lora__final_mean100'],dtype=np.float32),
      'c0_raw_mean100':x.reshape(len(x),100,1024).mean(axis=1),
      'c0_raw_flat':x,
      'c0_norm':stage['c0_norm'],
      'enc_linear_1':stage['enc_linear_1'],
      'enc_gelu_1':stage['enc_gelu_1'],
      'enc_linear_2':stage['enc_linear_2'],
      'enc_gelu_2':stage['enc_gelu_2'],
      'enc_linear_3':stage['enc_linear_3'],
      'enc_gelu_3':stage['enc_gelu_3'],
      'mid_gelu':stage['mid_gelu']}
    split=oracle._test_partitions(rows)
    projection={}
    for name,features in source.items():
        z,info=_train_pca(features,split['train'],max_rank=max(RANKS))
        projection[name]=(z,info)
        print('  train-only matched PCA: '+name+' rank='+str(info['effective_rank']),flush=True)
    selected=_select_matched(rows,{k:v[0] for k,v in projection.items()})
    print('  RAW DEV locked decoder: '+str({k:selected[k] for k in ('feature_count','decoder','alpha')}),flush=True)
    matched=_matched(rows,projection,selected,seed=seed)
    for name,r in matched.items():
        print('    '+name+' sign='+str(round(r['test']['balanced_sign_accuracy'],4))+
            ' triples='+str(round(r['test']['triple_accuracy'],4))+
            ' unseen='+str(round(r['test_by_family']['hawk_indifference']['balanced_sign_accuracy'],4)),flush=True)
    # Prior bypass did not store the frozen production metric; bind to the
    # audited boundary's exact baseline readout instead.
    boundary_path=Path(prior['source_boundary_report']).resolve()
    production=json.loads(boundary_path.read_text(encoding='utf-8'))['prior_frozen_production_c0_test']
    decision=_decision(matched,inverse_readout,production,norm_diag)
    # Revalidate the full signed lineage after CUDA and fitting work.
    root2,prior2,meta2,arrays2,capture2,known2=_source(root)
    if known2!=known or prior2!=prior or meta2['dataset_sha256']!=meta['dataset_sha256']:
        raise ValueError('GAMER_PRESERVATION_SOURCE_CHANGED_DURING_RUN')
    outcome={'schema':SCHEMA,'status':'COMPLETE_READ_ONLY_BOUNDED_INFERENCE',
             'source_encoder_bypass_report':str(root/'final_report.json'),
             'source_boundary_report':str(boundary_path),
             'fingerprints':{**known,'encoder_bypass_report_sha256':suff._digest(root/'final_report.json')},
             'dataset_sha256':meta['dataset_sha256'],'cases':len(rows),
             'baseline_replay_parity':replay,
             'layernorm_with_side_statistics':norm_diag,
             'inverse_normalization_fixed_raw_decoder':inverse_readout,
             'matched_decoder_dev_selection':selected,
             'matched_stage_readouts':matched,'decision':decision,
             'no_production_training_or_weight_update':True,
             'promotion':'BLOCKED_DIAGNOSTIC_ONLY','seed':seed}
    dest.mkdir(parents=True,exist_ok=False)
    gamer.atomic_json(dest/'final_report.json',outcome)
    print('GAMER INFORMATION PRESERVATION RESULT: '+decision['bounded_answer'],flush=True)
    print('GAMER INFORMATION PRESERVATION COMPLETE: '+str(dest/'final_report.json'),flush=True)
    return outcome
