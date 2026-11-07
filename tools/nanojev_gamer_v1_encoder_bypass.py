#!/usr/bin/env python3
"""Read-only GAMER C0 input-vs-encoder sign reconstruction audit.

This diagnostic uses the *same* 324 immutable exact-fraction oracle examples as
head-100-c0-boundary, the exact frozen 100-token inputs, and its frozen C0.
Diagnostic kernel readouts are fitted in memory to training groups only; their
family- and triple-level performance is scored on untouched held-out groups.
A bypass proves only tested decoder recoverability, not intrinsic arithmetic
competence or information-theoretic impossibility at unsuccessful stages.
"""
from __future__ import annotations

from fractions import Fraction
import os
import json
from pathlib import Path

import numpy as np

from tools import nanojev_gamer_v1_c0_boundary as c0
from tools import nanojev_gamer_v1_sign_information_probe as oracle
from tools import nanojev_gamer_v1_sufficiency as suff
from tools import nanojev_gamer_v1_handoff as gamer

SCHEMA='nanojev.gamer.v1.encoder-bypass-sign-reconstruction.v1'
ALPHAS=(.01,.1,1.,10.)
BANDWIDTHS=(.25,1.,4.)


def _source(run):
    boundary=Path(run).resolve()
    p=boundary/'final_report.json'
    if not p.is_file():raise FileNotFoundError('GAMER_BYPASS_BOUNDARY_REPORT_MISSING:'+str(p))
    prior=json.loads(p.read_text(encoding='utf-8'))
    if prior.get('schema')!=c0.SCHEMA or prior.get('status')!='COMPLETE_READ_ONLY_NO_CAUSAL_CERTIFICATION':
        raise ValueError('GAMER_BYPASS_REQUIRES_COMPLETED_BOUNDARY')
    if prior.get('baseline_replay_parity',{}).get('max_abs_delta')!=0.0:
        raise ValueError('GAMER_BYPASS_REQUIRES_EXACT_C0_REPLAY')
    upstream=Path(prior['source_upstream_report']).resolve().parent
    root,report,meta,arrays,capture=c0._source(upstream)
    from tools import nanojev_two_game_spine_smoke as spine
    if Path(prior['source_upstream_report']).resolve()!=root/'final_report.json' or \
       suff._digest(root/'final_report.json')!=prior['source_upstream_sha256'] or \
       Path(prior['source_oracle_capture']).resolve()!=capture/oracle.CAPTURE_NAME or \
       suff._digest(capture/oracle.CAPTURE_NAME)!=prior['source_oracle_capture_sha256'] or \
       meta['dataset_sha256']!=prior['dataset_sha256'] or \
       meta['lineage']['checkpoint_sha256']!=prior['frozen_head_sha256'] or \
       meta['lineage']!=prior['model_lineage']:
        raise ValueError('GAMER_BYPASS_UNVERIFIED_BOUNDARY_LINEAGE')
    if not (suff._digest(Path(meta['lineage']['checkpoint_path']))==prior['frozen_head_sha256'] and
            spine.adapter_identity(Path(meta['lineage']['adapter_dir']))==meta['lineage']['adapter_sha256']):
        raise ValueError('GAMER_BYPASS_FROZEN_WEIGHTS_CHANGED')
    c0._triples(meta['dataset'])
    return boundary,prior,root,report,meta,arrays,capture


def _destination(boundary, output):
    """Choose a short, source-bound sibling directory.

    Report lineages accumulate suffixes until the *component* exceeds the
    common 255-character filesystem limit (and the Windows path can exceed
    MAX_PATH). Never append another suffix to the upstream directory name.
    """
    boundary=Path(boundary).resolve()
    if output is not None:
        dest=c0._destination(boundary,output)
    else:
        source_digest=suff._digest(boundary/'final_report.json')[:12]
        stem='c0-encoder-bypass-'+source_digest
        dest=next((boundary.parent/(stem+'-'+f'{i:04d}')
                   for i in range(1,10000) if not (
                       boundary.parent/(stem+'-'+f'{i:04d}')).exists()),None)
    if dest is None:raise RuntimeError('GAMER_BYPASS_NO_OUTPUT_SLOT')
    if dest.exists():raise FileExistsError('GAMER_BYPASS_OUTPUT_EXISTS')
    protected=boundary
    dest=dest.resolve()
    if dest==protected or dest in protected.parents or protected in dest.parents:
        raise ValueError('GAMER_BYPASS_OUTPUT_OVERLAPS_SOURCE')
    # Fail BEFORE doing CUDA/probe work if a user-selected Windows path is too
    # long; the output report must itself be creatable.
    if (len(dest.name)>240 or (
        os.name=='nt' and len(str(dest/'final_report.json'))>=240)):
        raise ValueError('GAMER_BYPASS_OUTPUT_PATH_TOO_LONG_USE_BYPASS_OUTPUT')
    return dest


def _features_at_every_stage(net, input_flat, *, device, batch_size=8):
    """Capture pre-LN, LN, every encoder projection and GELU, mid and output.

    No mutable forward hooks; calls the exact sequential modules in the same
    order as production.  Float16 is converted to float32 for probe fitting.
    """
    import torch
    from tools.nanojev_qwen_c0_compressive_train import inverse_signed_log
    raw=np.asarray(input_flat,dtype=np.float32)
    if raw.ndim!=2 or raw.shape[1]!=net.norm.normalized_shape[0] or \
       raw.shape[1]!=102400 or not np.isfinite(raw).all():
        raise ValueError('GAMER_BYPASS_INVALID_C0_RAW_INPUT')
    if len(net.enc)!=6 or len(net.mid)!=2:
        raise ValueError('GAMER_BYPASS_UNEXPECTED_C0_ENCODER_ARCHITECTURE')
    names=['c0_norm','enc_linear_1','enc_gelu_1','enc_linear_2','enc_gelu_2',
           'enc_linear_3','enc_gelu_3','mid_linear','mid_gelu']
    chunks={name:[] for name in names};outputs=[]
    dtype=next(net.parameters()).dtype
    with torch.inference_mode():
        for start in range(0,len(raw),batch_size):
            inp=torch.as_tensor(raw[start:start+batch_size],device=device,dtype=dtype)
            h=net.norm(inp)
            chunks['c0_norm'].append(h.float().cpu().numpy())
            for i,layer in enumerate(net.enc):
                h=layer(h)
                name=f'enc_{"linear" if i%2==0 else "gelu"}_{i//2+1}'
                chunks[name].append(h.float().cpu().numpy())
            for i,layer in enumerate(net.mid):
                h=layer(h)
                chunks['mid_linear' if i==0 else 'mid_gelu'].append(h.float().cpu().numpy())
            final=net.corrected(h).flatten()
            outputs.append(np.asarray(inverse_signed_log(final.float().cpu().numpy()),dtype=np.float32))
    stages={key:np.concatenate(parts,axis=0) for key,parts in chunks.items()}
    for key,value in stages.items():
        if value.shape[0]!=len(raw) or not np.isfinite(value).all():
            raise ValueError('GAMER_BYPASS_INVALID_STAGE:'+key)
    return np.concatenate(outputs),stages


def _distance2(a,b):
    # Float64 keeps squared distances nonnegative in wide standardized spaces.
    aa=np.sum(a*a,axis=1,keepdims=True)
    bb=np.sum(b*b,axis=1,keepdims=True).T
    out=np.maximum(aa+bb-2*a@b.T,0.)
    return out


def _kernels(x,train,dev,test):
    """Preprocessing and RBF bandwidth derived exclusively from train rows."""
    a=np.asarray(x[train],dtype=np.float64)
    b=np.asarray(x[dev],dtype=np.float64)
    c=np.asarray(x[test],dtype=np.float64)
    if a.ndim!=2 or not all(np.isfinite(z).all() for z in (a,b,c)):
        raise ValueError('GAMER_BYPASS_NONFINITE_PROBE_FEATURE')
    avg=a.mean(axis=0,keepdims=True)
    std=np.sqrt(np.maximum(np.mean((a-avg)**2,axis=0,keepdims=True),1e-6))
    a=(a-avg)/std;b=(b-avg)/std;c=(c-avg)/std
    width=a.shape[1]
    if width==0:raise ValueError('GAMER_BYPASS_EMPTY_FEATURE_WIDTH')
    # Linear ridge exactly matches the preceding oracle policy's kernel.
    scale=float(width)
    linear=(a@a.T/scale,b@a.T/scale,c@a.T/scale)
    dtrain=_distance2(a,a)/scale
    ddev=_distance2(b,a)/scale
    dtest=_distance2(c,a)/scale
    offdiag=dtrain[np.triu_indices(len(a),1)]
    median=float(np.median(offdiag[offdiag>1e-10])) if np.any(offdiag>1e-10) else 0.
    out={'linear':linear}
    if median>1e-10:
        for multiplier in BANDWIDTHS:
            divisor=median*multiplier
            out['rbf_'+str(multiplier)]=tuple(np.exp(-distance/divisor)
                for distance in (dtrain,ddev,dtest))
    return out


def _reconstruct(rows,x,*,seed=47):
    """Select kernel/bandwidth/alpha on DEV; report exactly one TEST result.

    Never rank or choose probe candidates using test accuracy. Nulls retain the
    selected kernel while shuffling training labels, not split identities.
    """
    split=oracle._test_partitions(rows)
    train,dev,test=[split[name] for name in ('train','dev','test')]
    y=np.asarray([float(Fraction(row['target'])) for row in rows],dtype=np.float64)
    choices=_kernels(x,train,dev,test)
    best=None
    for name,(kt,kd,ke) in choices.items():
        selection,cls,values=oracle._solve_probe(kt,kd,ke,y[train],y[dev],alphas=ALPHAS)
        rank=(selection['dev_balanced_sign_accuracy'],-selection['dev_signed_mae'],int(name=='linear'))
        if best is None or rank>best[0]:
            best=(rank,name,selection,cls,values)
    _,kind,selected,classes,values=best
    metric=oracle._metrics(y[test],values,classes,rows,test)
    family={}
    for name in sorted(set(rows[int(i)]['family'] for i in test)):
        selected_local=np.asarray([j for j,i in enumerate(test) if rows[int(i)]['family']==name],dtype=int)
        idx=test[selected_local]
        family[name]=oracle._metrics(y[idx],values[selected_local],classes[selected_local],rows,idx)
    nulls=[]
    kt,kd,ke=choices[kind]
    for i in range(3):
        shuffled=y[train].copy()
        np.random.default_rng(seed+i).shuffle(shuffled)
        _,nclasses,nvalues=oracle._solve_probe(kt,kd,ke,shuffled,y[dev],alphas=ALPHAS)
        nulls.append(oracle._metrics(y[test],nvalues,nclasses,rows,test)['balanced_sign_accuracy'])
    return {'feature_width':int(x.shape[1]),'train_cases':len(train),'dev_cases':len(dev),
            'test_cases':len(test),'model_selection':'DEV_ONLY',
            'decoder':kind,'development_selection':selected,'test':metric,
            'test_by_family':family,'shuffled_train_label_nulls':nulls,
            'beats_all_shuffled_nulls':bool(metric['balanced_sign_accuracy']>max(nulls)),
            'passes_95pct_all_classes_and_triples':bool(
                metric['balanced_sign_accuracy']>=.95 and metric['triple_accuracy']>=.95 and
                metric['small_nonzero_correct_sign'] is not None and
                metric['small_nonzero_correct_sign']>=.95 and
                all(m['balanced_sign_accuracy']>=.95 and m['triple_accuracy']>=.95
                    for m in family.values()))}


def _decision(readouts,prod):
    raw=readouts['c0_raw_flat'];norm=readouts['c0_norm'];encoded=readouts['enc_gelu_3']
    last=readouts['mid_gelu'];earlier=readouts['lora_layer09_mean100']
    def passed(r):return r['passes_95pct_all_classes_and_triples'] and r['beats_all_shuffled_nulls']
    if passed(raw) and not passed(encoded):
        answer='RECOVERABLE_AT_RAW_C0_INPUT_NOT_AFTER_ENCODER'
    elif not passed(raw) and passed(encoded):
        answer='ENCODER_MAKES_SIGN_RECOVERABLE_INPUT_BYPASS_NOT_CERTIFIED'
    elif passed(raw) and passed(encoded):
        answer='SIGN_RECOVERABLE_ON_BOTH_SIDES_C0_OUTPUT_READOUT_FAILURE'
    else:
        answer='RAW_INPUT_AND_ENCODER_FAIL_SUFFICIENCY_GATE'
    raw_score=raw['test']['balanced_sign_accuracy']
    enc_score=encoded['test']['balanced_sign_accuracy']
    detail={'upstream_early_layer_test_accuracy':earlier['test']['balanced_sign_accuracy'],
            'raw_c0_input_test_accuracy':raw_score,
            'layernorm_test_accuracy':norm['test']['balanced_sign_accuracy'],
            'encoder_output_test_accuracy':enc_score,
            'bottleneck_test_accuracy':last['test']['balanced_sign_accuracy'],
            'production_output_test_accuracy':prod['balanced_sign_accuracy'],
            'raw_to_encoder_accuracy_delta':raw_score-enc_score,
            'raw_input_sign_sufficiency_demonstrated':passed(raw),
            'encoder_sign_sufficiency_demonstrated':passed(encoded),
            'production_negative_recall':prod['recall']['-1'],
            'production_triple_accuracy':prod['triple_accuracy']}
    # The sign could remain in a representation that neither tested decoder
    # extracts. A negative diagnostic is NEVER a proof of information absence.
    return {'bounded_answer':answer,'findings':detail,
      'causal_certification':False,
      'interpretation':(
        'Successful raw-input bypass on held-out complete oracle triples would '
        'demonstrate sign is recoverable before C0. Failure establishes only '
        'that the tested diagnostic readouts do not recover it. Comparison '
        'across stages measures readout accessibility, not guaranteed '
        'information-theoretic loss. Exact examples include an unseen family '
        'that must not be silently omitted. No production weights changed.')}


def run_encoder_bypass(run,*,output=None,device='cuda',head_batch=8,seed=47):
    if head_batch<1:raise ValueError('GAMER_BYPASS_INVALID_BATCH')
    boundary,prior,root,report,meta,arrays,capture=_source(run)
    dest=_destination(boundary,output)
    original_boundary_digest=suff._digest(boundary/'final_report.json')
    original_sign_digest=suff._digest(Path(report['sign_only_source'])/'final_report.json')
    protected=(boundary,root,Path(meta['lineage']['checkpoint_path']),capture,
               Path(report['source_expanded']),Path(report['sign_only_source']))
    if any(dest==p or dest in p.parents or p in dest.parents for p in protected):
        raise ValueError('GAMER_BYPASS_OUTPUT_OVERLAPS_PROTECTED_SOURCE')
    rows=meta['dataset']
    required=('lora__layer_09_mean100','lora__final_mean100','lora__final_last','lora__final_flat')
    if any(name not in arrays for name in required):
        raise ValueError('GAMER_BYPASS_MISSING_FINAL_OR_EARLY_LAYER_CAPTURE')
    net=c0._load_head(meta,device)
    x=np.asarray(arrays['lora__final_flat'],dtype=np.float32)
    print('GAMER RAW-C0/ENCODER SIGN BYPASS: '+str(dest),flush=True)
    print('  EXACT oracle / immutable Qwen-LoRA and C0 / no production training',flush=True)
    baseline,stages=_features_at_every_stage(net,x,device=device,batch_size=head_batch)
    parity=c0._verify_baseline(baseline,arrays['production_head_predictions'])
    # The names below differentiate PRE-LayerNorm from post-normalization and
    # expose all three encoder projections instead of conflating them.
    xs=x.reshape(len(x),100,1024)
    moment_mean=x.mean(axis=1,keepdims=True)
    moment_std=x.std(axis=1,keepdims=True)
    pool={
       'lora_layer09_mean100':np.asarray(arrays['lora__layer_09_mean100'],dtype=np.float32),
       'lora_final_mean100':np.asarray(arrays['lora__final_mean100'],dtype=np.float32),
       'lora_final_last':np.asarray(arrays['lora__final_last'],dtype=np.float32),
       'c0_raw_mean100':xs.mean(axis=1),
       'c0_raw_flat':x,
       'c0_raw_moments':np.concatenate((moment_mean,moment_std),axis=1),
       'c0_norm':stages['c0_norm'],
       'c0_norm_with_moments':np.concatenate((stages['c0_norm'],moment_mean,moment_std),axis=1),
       **{k:v for k,v in stages.items() if k!='c0_norm'}}
    # Do not store raw activations: source capture already has SHA256-bound
    # tensors. Train in memory; only small aggregate reports go to new dir.
    readouts={}
    for name,features in pool.items():
        print('  matched decoder: '+name+' / width='+str(features.shape[1]),flush=True)
        readouts[name]=_reconstruct(rows,features,seed=seed)
        report_test=readouts[name]['test']
        print('    test_sign='+str(round(report_test['balanced_sign_accuracy'],4))+
              ' test_triples='+str(round(report_test['triple_accuracy'],4))+
              ' unseen_hawk='+str(round(readouts[name]['test_by_family']['hawk_indifference']['balanced_sign_accuracy'],4)),
              flush=True)
    prior_prod=prior['prior_frozen_production_c0_test']
    conclusion=_decision(readouts,prior_prod)
    from tools import nanojev_two_game_spine_smoke as spine
    lineage=meta['lineage']
    fingerprints={
      'c0_checkpoint_sha256':suff._digest(Path(lineage['checkpoint_path'])),
      'adapter_sha256':spine.adapter_identity(Path(lineage['adapter_dir'])),
      'oracle_capture_sha256':suff._digest(capture/oracle.CAPTURE_NAME),
      'oracle_analysis_sha256':suff._digest(capture/'analysis.json'),
      'upstream_report_sha256':suff._digest(root/'final_report.json'),
      'c0_boundary_report_sha256':suff._digest(boundary/'final_report.json')}
    if (fingerprints['c0_checkpoint_sha256']!=lineage['checkpoint_sha256'] or
        fingerprints['adapter_sha256']!=lineage['adapter_sha256'] or
        fingerprints['oracle_capture_sha256']!=prior['source_oracle_capture_sha256'] or
        fingerprints['oracle_analysis_sha256']!=report['oracle_analysis_sha256'] or
        fingerprints['upstream_report_sha256']!=prior['source_upstream_sha256'] or
        fingerprints['c0_boundary_report_sha256']!=original_boundary_digest or
        suff._digest(Path(report['sign_only_source'])/'final_report.json')!=original_sign_digest):
        raise ValueError('GAMER_BYPASS_SOURCE_MUTATED_DURING_RUN')
    result={'schema':SCHEMA,'status':'COMPLETE_READ_ONLY_BOUNDED_INFERENCE',
            'source_boundary_report':str(boundary/'final_report.json'),
            'source_upstream_report':str(root/'final_report.json'),
            'fingerprints':fingerprints,'dataset_sha256':meta['dataset_sha256'],
            'baseline_parity':parity,'readouts':readouts,'decision':conclusion,
            'no_production_training_or_weight_update':True,
            'selection':'train-only fitting, dev-only model/kernel/alpha selection, test-only scoring',
            'promotion':'BLOCKED_DIAGNOSTIC_ONLY','seed':seed}
    dest.mkdir(parents=True,exist_ok=False)
    gamer.atomic_json(dest/'final_report.json',result)
    print('GAMER ENCODER BYPASS RESULT: '+conclusion['bounded_answer'],flush=True)
    print('GAMER ENCODER BYPASS COMPLETE: '+str(dest/'final_report.json'),flush=True)
    return result
