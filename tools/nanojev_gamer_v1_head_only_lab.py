#!/usr/bin/env python3
"""NanoJev Gamer disposable head-only repair experiment; NO promotion or LoRA training.

Uses a fingerprint-bound frozen feature archive made by
nanojev_gamer_v1_sign_information_probe, and independently captures source
retention controls with --mode source-capture. Fits only small disposable kernel
readouts (a head-only intervention), compares them to the EXACT original
production predictions, and selects candidates by development data only.

Example:
  python -m tools.nanojev_gamer_v1_head_only_lab --mode source-capture \
      --run runtime/reports/gamer-v1/transition-005 \
      --expanded-run runtime/reports/gamer-v1/transition-005-expanded-head-100-0001 \
      --output runtime/reports/gamer-v1/transition-005-head-retention-0001 --device cuda
  python -m tools.nanojev_gamer_v1_head_only_lab --mode evaluate \
      --input runtime/reports/gamer-v1/transition-005-sign-information-probe-0001 \
      --source-features runtime/reports/gamer-v1/transition-005-head-retention-0001 \
      --output runtime/reports/gamer-v1/transition-005-head-only-lab-0001

This laboratory never saves or promotes candidate parameters, does not change
production/model training state, and never declares its already-explored held-
out dataset a virgin blind acceptance test.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path

from tools import nanojev_gamer_v1_sign_information_probe as probe
from tools import nanojev_gamer_v1_zero_sign_frob as original

SCHEMA='nanojev.gamer.v1.head-only-lab.v1'
SOURCE_SCHEMA='nanojev.gamer.v1.head-only-source.v2'
SOURCE_ARCHIVE='source_features.npz'
FEATURE_POLICIES=('final_flat','final_mean100','final_last',
                  'layer_09_mean100','layer_18_mean100','layer_27_mean100')
DECODERS=('hurdle','signed_value','sign_guard')
ALPHAS=(0.01,0.1,1.0,10.0)


def _sha_array(x):
    import numpy as np
    return hashlib.sha256(np.ascontiguousarray(x).tobytes()).hexdigest()


def _output(path,protected=()):
    out=Path(path).resolve()
    for p in protected:
        p=Path(p).resolve()
        if out==p or p in out.parents or out in p.parents:
            raise ValueError('HEAD_LAB_OUTPUT_OVERLAPS_SOURCE: '+str(p))
    if out.exists():raise FileExistsError('HEAD_LAB_OUTPUT_ALREADY_EXISTS: '+str(out))
    return out


def _archive_path(path,name):
    p=Path(path)
    return p/name if p.is_dir() else p


def _verified_source_archive(path,expected_lineage=None,require_source=True):
    import numpy as np
    p=_archive_path(path,SOURCE_ARCHIVE)
    if not p.is_file():raise FileNotFoundError('HEAD_LAB_SOURCE_ARCHIVE_NOT_FOUND: '+str(p))
    with np.load(p,allow_pickle=False) as data:
        info=json.loads(str(data['metadata'].item()))
        arrays={k:data[k].copy() for k in data.files if k!='metadata'}
    if info.get('schema')!=SOURCE_SCHEMA:raise ValueError('HEAD_LAB_SOURCE_SCHEMA_MISMATCH')
    if info.get('dataset_sha256')!=probe.sha_text(probe.canonical(info.get('dataset'))):
        raise ValueError('HEAD_LAB_SOURCE_DATASET_FINGERPRINT_MISMATCH')
    if set(arrays)!=set(info.get('array_sha256',{})):
        raise ValueError('HEAD_LAB_SOURCE_ARRAY_KEYS_MISMATCH')
    n=len(info['dataset'])
    available=info.get('source_available')
    if not isinstance(available,int) or available<n or info.get('source_count')!=n or \
       info.get('source_complete')!=(n==available):
        raise ValueError('HEAD_LAB_SOURCE_COVERAGE_METADATA_MISMATCH')
    if n<100:raise ValueError('HEAD_LAB_RETENTION_REQUIRES_AT_LEAST_100_CERTIFIED_CASES')
    if len({r['source_index'] for r in info['dataset']})!=n or any(
        r.get('kind')!='certified_source' for r in info['dataset']):
        raise ValueError('HEAD_LAB_BAD_CERTIFIED_SOURCE_ROWS')
    for k,x in arrays.items():
        if len(x)!=n or not np.isfinite(x).all() or _sha_array(x)!=info['array_sha256'][k]:
            raise ValueError('HEAD_LAB_SOURCE_ARRAY_FINGERPRINT_OR_SHAPE_MISMATCH: '+k)
    if arrays['lora__final_flat'].shape!=(n,102400) or arrays['predictions'].shape!=(n,):
        raise ValueError('HEAD_LAB_SOURCE_CAPTURE_WRONG_SHAPE')
    if expected_lineage is not None:
        for key in ('checkpoint_sha256','checkpoint_step','adapter_sha256','model_id'):
            if info['lineage'].get(key)!=expected_lineage.get(key):
                raise ValueError('HEAD_LAB_RETENTION_LINEAGE_MISMATCH: '+key)
        if info['lineage'].get('configuration')!=expected_lineage.get('configuration'):
            raise ValueError('HEAD_LAB_RETENTION_HEAD_CONFIG_MISMATCH')
    if require_source:
        lineage=info['lineage']
        cp=Path(lineage['checkpoint_path']);adapter=Path(lineage['adapter_dir'])
        if not cp.is_file() or not adapter.is_dir():
            raise FileNotFoundError('HEAD_LAB_ORIGINAL_CHECKPOINT_OR_ADAPTER_NOT_FOUND')
        from tools import nanojev_two_game_spine_smoke as spine
        if spine.file_sha(cp)!=lineage['checkpoint_sha256'] or spine.adapter_identity(adapter)!=lineage['adapter_sha256']:
            raise ValueError('HEAD_LAB_ORIGINAL_MODEL_FINGERPRINT_CHANGED')
    if require_source:
        cacheinfo=info.get('source_feature_cache')
        if not cacheinfo or cacheinfo.get('feature_policy')!='EXACT_ORIGINAL_TRAINING_CACHE':
            raise ValueError('HEAD_LAB_SOURCE_REQUIRES_ORIGINAL_TRAINING_CACHE')
        cachepath=Path(cacheinfo['path'])
        if (cachepath.resolve()!=Path(info['lineage']['expanded_run']).resolve()/
                            'feature_cache'/'dev.npz' or
            not cachepath.is_file()):
            raise FileNotFoundError('HEAD_LAB_ORIGINAL_TRAINING_FEATURE_CACHE_NOT_FOUND')
        from tools import nanojev_two_game_spine_smoke as spine
        if spine.file_sha(cachepath)!=cacheinfo['sha256']:
            raise ValueError('HEAD_LAB_TRAINING_CACHE_FINGERPRINT_CHANGED')
    return info,arrays


def _verified_training_dev_cache(expanded, identity, dev):
    """Load EXACT original dev features, not a newly padded Qwen recapture.

    The original expanded-head trainer stored the dev raster in fp16 and
    evaluated best_model.pt directly on those features. Reference parity must
    use that archive, in its original development order, and its metadata must
    match the fingerprinted training provenance, split and prompt sequence.
    """
    import numpy as np
    from tools import nanojev_gamer_v1_expanded_head as expanded_head
    from tools import nanojev_gamer_v1_handoff as gamer
    from tools import nanojev_two_game_spine_smoke as spine
    expanded=Path(expanded)
    prov=expanded/'provenance.json'
    cache=expanded/'feature_cache'/'dev.npz'
    if not prov.is_file():
        raise FileNotFoundError('HEAD_LAB_TRAINING_PROVENANCE_NOT_FOUND: '+str(prov))
    if not cache.is_file():
        raise FileNotFoundError('HEAD_LAB_TRAINING_DEV_CACHE_NOT_FOUND: '+str(cache))
    provenance=json.loads(prov.read_text(encoding='utf-8'))
    if provenance!=identity:
        raise ValueError('HEAD_LAB_TRAINING_PROVENANCE_MISMATCH')
    prompts=[gamer.original_prompt(row) for row in dev]
    cache_id={**identity,'split':'dev',
        'prompt_sha256':hashlib.sha256(gamer.canonical(prompts).encode('utf-8')).hexdigest()}
    features,summary=expanded_head._read_cache(cache,cache_id,len(dev),
                                                identity['configuration']['input_width'])
    if features.shape!=(len(dev),102400) or not np.isfinite(features).all():
        raise ValueError('HEAD_LAB_TRAINING_DEV_CACHE_INVALID')
    return features, {'path':str(cache.resolve()),'sha256':spine.file_sha(cache),
                      'samples':len(dev),'capture_summary':summary,
                      'feature_policy':'EXACT_ORIGINAL_TRAINING_CACHE',
                      'fp16_round_trip':True}


def _original_head_predictions(info,full,*,device,head_batch):
    """Re-run the original head's forward and signed-log inverse on saved dev inputs."""
    import numpy as np
    import torch
    from tools.nanojev_gamer_v1_expanded_head import make_expanded_head
    from tools.nanojev_qwen_c0_compressive_train import inverse_signed_log
    cfg=info['configuration']
    checkpoint=torch.load(info['checkpoint_path'],map_location='cpu',weights_only=True)
    net=make_expanded_head(torch,102400,tuple(cfg['hidden']))
    net.load_state_dict(checkpoint['model'],strict=True)
    if cfg['backend']=='offload-cuda':
        dtype=torch.bfloat16 if cfg['precision']=='bfloat16' else torch.float16
    else:
        dtype=torch.float32
    net=net.to(device=device,dtype=dtype).eval()
    predictions=[]
    with torch.inference_mode():
        for start in range(0,len(full),head_batch):
            x=torch.as_tensor(full[start:start+head_batch],device=device,dtype=dtype)
            raw,_=net(x)
            predictions.extend(float(v) for v in inverse_signed_log(raw.float().cpu().numpy()))
    del net,checkpoint
    if not np.isfinite(predictions).all() or len(predictions)!=len(full):
        raise ValueError('HEAD_LAB_ORIGINAL_HEAD_NONFINITE_OR_MISSING_OUTPUT')
    return np.asarray(predictions,dtype=np.float64)


def _source_reference_parity(controls,predictions,reference,tolerance=0.01):
    """Strict absolute comparison; never mask drift with a relative tolerance."""
    import numpy as np
    diffs=np.asarray([abs(float(p)-float(reference[c.id]))
                      for c,p in zip(controls,predictions)],dtype=np.float64)
    if not len(diffs) or not np.isfinite(diffs).all():
        raise ValueError('HEAD_LAB_PARITY_NONFINITE_OR_EMPTY')
    ix=np.argsort(-diffs,kind='stable')[:10]
    audit={'max_absolute_delta':float(diffs.max()),
           'median_absolute_delta':float(np.median(diffs)),
           'over_tolerance':int(np.sum(diffs>tolerance)),
           'tolerance':tolerance,
           'worst':[{'id':controls[int(i)].id,
                     'source_index':controls[int(i)].source_index,
                     'saved_prediction':float(reference[controls[int(i)].id]),
                     'recaptured_prediction':float(predictions[int(i)]),
                     'absolute_delta':float(diffs[int(i)])} for i in ix]}
    if audit['over_tolerance']:
        raise ValueError('HEAD_LAB_SAVED_CHECKPOINT_PREDICTION_PARITY_FAILED: '+
                         json.dumps(audit,sort_keys=True))
    return audit


def source_capture(args):
    import gc
    import numpy as np
    import torch
    from tools import nanojev_gamer_v1_expanded_head as expanded_head
    from tools import nanojev_gamer_v1_handoff as gamer
    from tools import nanojev_two_game_spine_smoke as spine
    if not args.run or not args.expanded_run or not args.output:
        raise ValueError('HEAD_LAB_SOURCE_CAPTURE_REQUIRES_RUN_EXPANDED_RUN_OUTPUT')
    if args.source_cases<100:raise ValueError('HEAD_LAB_SOURCE_REQUIRES_100_OR_MORE_EXAMPLES')
    if args.capture_batch<1 or args.head_batch<1:raise ValueError('HEAD_LAB_BAD_BATCH')
    info=probe._verified_lineage(args.run,args.expanded_run,args.model_id)
    root=Path(args.run).resolve();expanded=Path(args.expanded_run).resolve()
    out=_output(args.output,(root,expanded))
    if args.max_input_tokens>info['configuration']['max_length']:
        raise ValueError('HEAD_LAB_CAPTURE_TOKENS_EXCEED_TRAINED_LIMIT')
    expanded_identity=spine.expanded_metadata(expanded,info['adapter_sha256'],args.model_id)
    dev=original._manifest_verified_dev(root,expanded_identity)
    if args.source_cases>len(dev):
        raise ValueError('HEAD_LAB_SOURCE_CASES_EXCEED_VERIFIED_DEVELOPMENT_SET')
    original_controls,selection=original.select_source_controls(dev,args.source_cases)
    reference=original._check_original_evaluation(root,expanded,
        info['checkpoint_step'],original_controls,None)
    if (reference['status']!='CHECKPOINT_MATCHED' or
            len(reference['validated_reference_predictions'])!=len(original_controls)):
        raise ValueError('HEAD_LAB_STEP_MATCHED_SOURCE_PREDICTIONS_REQUIRED')

    # The original trainer evaluated the head on dev features loaded from its
    # fp16 cache, in original dev order. Recapturing selected examples changes
    # padding companions and CUDA kernels and is NOT an identical baseline.
    full,cache_info=_verified_training_dev_cache(expanded,expanded_identity,dev)
    ix=np.asarray([p.source_index for p in original_controls],dtype=np.int64)
    if len(set(int(i) for i in ix))!=len(ix) or (ix<0).any() or (ix>=len(dev)).any():
        raise ValueError('HEAD_LAB_SOURCE_INDEX_OUT_OF_BOUNDS')
    x=full[ix].copy()
    # Match training inference's batch size and feature precision. Arbitrary
    # user-specified head batching must not redefine the parity experiment.
    training_batch=int(info['configuration']['batch_size'])
    cfg=info['configuration']
    print(f'HEAD_LAB source cache: {len(original_controls)}/{len(dev)} original examples; '+
          f'best_step={info["checkpoint_step"]}; adapter={info["adapter_sha256"][:16]}; '+
          f'training_head_batch={training_batch}',flush=True)
    # Preserve original source ordering for the CUDA head matmul as well.
    # Only then select the diagnostic's possibly reordered source controls.
    all_head=_original_head_predictions(info,full,device=args.device,
                                        head_batch=training_batch)
    head=all_head[ix].copy()
    parity=_source_reference_parity(original_controls,head,
                                   reference['validated_reference_predictions'])
    details={'case_count':len(original_controls),'source':'TRAINING_FEATURE_CACHE',
             'head_batch':training_batch,'capture_repeated':False,
             'head_inference_order':'ORIGINAL_FULL_DEV_ORDER'}
    vector=x.reshape(len(x),100,1024)
    features={'final_flat':x,'final_last':vector[:,-1,:].copy(),
              'final_mean100':vector.mean(axis=1,dtype=np.float32)}
    # Optional earlier-layer captures are auxiliary only. They use original
    # complete development ordering to reproduce the original padding groups;
    # the final input and head baseline ALWAYS come from the verified cache.
    if getattr(args,'include_intermediate_layers',False):
        spine.cache_policy(False)
        _,tok,model=gamer.model_components(args.model_id,args.device,
                                          adapter_dir=Path(info['adapter_dir']))
        from types import SimpleNamespace
        full_deck=[SimpleNamespace(prompt=gamer.original_prompt(r)) for r in dev]
        live,live_info=probe._capture_profiles(torch,model,tok,full_deck,
            device=args.device,max_length=args.max_input_tokens,
            batch_size=int(cfg['capture_batch']),curvature=cfg['curvature'])
        del model,tok
        gc.collect()
        if torch.cuda.is_available():torch.cuda.empty_cache()
        details['live_intermediate_capture']=live_info
        details['max_live_final_vs_cache_feature_delta']=float(np.max(
            np.abs(live['final_flat']-full)))
        for name,values in live.items():
            if name.startswith('layer_'):
                features[name]=values[ix].copy()
        del live
    if spine.file_sha(Path(info['checkpoint_path']))!=info['checkpoint_sha256'] or \
       spine.adapter_identity(Path(info['adapter_dir']))!=info['adapter_sha256']:
        raise ValueError('HEAD_LAB_SOURCE_WEIGHTS_CHANGED_DURING_CAPTURE')
    if spine.file_sha(Path(cache_info['path']))!=cache_info['sha256']:
        raise ValueError('HEAD_LAB_TRAINING_FEATURE_CACHE_CHANGED_DURING_CAPTURE')
    rows=[{**p.public(),'kind':'certified_source'} for p in original_controls]
    arrays={'predictions':head.astype(np.float32)}
    arrays.update({'lora__'+k:value.astype(np.float16) for k,value in features.items()})
    metadata={'schema':SOURCE_SCHEMA,'lineage':info,'dataset':rows,
              'dataset_sha256':probe.sha_text(probe.canonical(rows)),
              'array_sha256':{k:_sha_array(v) for k,v in arrays.items()},
              'available_profiles':sorted(features),
              'source_available':len(dev),'source_count':len(rows),
              'source_complete':len(rows)==len(dev),
              'checkpoint_matched_reference_max_delta':parity['max_absolute_delta'],
              'checkpoint_matched_reference_parity':parity,
              'source_feature_cache':cache_info,
              'source_selection':selection,'capture':details,
              'frozen_lora':True,'original_head_unchanged':True}
    out.mkdir(parents=True,exist_ok=False)
    with (out/SOURCE_ARCHIVE).open('wb') as f:
        np.savez_compressed(f,metadata=probe.canonical(metadata),**arrays)
    (out/'source_manifest.json').write_text(json.dumps(
        {k:v for k,v in metadata.items() if k!='dataset'},indent=2,sort_keys=True,allow_nan=False)+'\n',
        encoding='utf-8')
    print(probe.canonical({'status':'SOURCE_CAPTURE_COMPLETE','directory':str(out),
        'source_count':len(rows),'source_available':len(dev),
        'checkpoint_step':info['checkpoint_step'],
        'checkpoint_parity_max_delta':parity['max_absolute_delta'],
        'cache_fingerprint':cache_info['sha256'],
        'intermediate_layer_capture':getattr(args,'include_intermediate_layers',False),
        'head_and_lora_unchanged':True}),flush=True)
    return out


def _signs(y):
    import numpy as np
    return np.sign(np.asarray(y,dtype=np.float64)).astype(np.int8)


def _classify_continuous(values,threshold=0.05):
    import numpy as np
    z=np.asarray(values,dtype=np.float64)
    return np.where(z>threshold,1,np.where(z< -threshold,-1,0)).astype(np.int8)


def _decode(raw,production,decoder):
    import numpy as np
    classes=np.argmax(raw[:,:3],axis=1).astype(np.int8)-1
    signed_log=np.clip(raw[:,3],-math.log1p(128),math.log1p(128))
    values=np.sign(signed_log)*np.expm1(np.abs(signed_log))
    if decoder=='signed_value':
        return _classify_continuous(values),values
    if decoder=='hurdle':
        v=np.where(classes==0,0.0,classes*np.abs(values))
        return classes,v
    if decoder=='sign_guard':
        if production is None:raise ValueError('HEAD_LAB_GUARD_REQUIRES_PRODUCTION_PREDICTIONS')
        v=np.where(classes==0,0.0,classes*np.abs(production))
        return classes,v
    raise ValueError('HEAD_LAB_UNKNOWN_DECODER')


def _grams(train,dev,test,source=None):
    """Train-only centering/scaling; source has no influence on candidate fitting."""
    import numpy as np
    x=np.asarray(train,dtype=np.float32)
    mu=x.mean(axis=0,keepdims=True)
    std=np.sqrt(np.maximum(np.mean((x-mu)**2,axis=0,keepdims=True),1e-6))
    x=(x-mu)/std
    scale=max(x.shape[1],1)
    kernels=[]
    for z in (x,dev,test,source):
        if z is None: kernels.append(None);continue
        mat=x if z is x else (np.asarray(z,dtype=np.float32)-mu)/std
        kernels.append(np.asarray(mat@x.T/scale,dtype=np.float64))
    kernels[0]=(kernels[0]+kernels[0].T)*0.5
    if any(not np.isfinite(k).all() for k in kernels if k is not None):
        raise ValueError('HEAD_LAB_NONFINITE_KERNEL')
    return kernels


def _targets(rows):
    import numpy as np
    y=np.asarray([float(Fraction(r['target'])) for r in rows],dtype=np.float64)
    if not np.isfinite(y).all():raise ValueError('HEAD_LAB_NONFINITE_TARGETS')
    return y


def _fit(K,y,alpha):
    import numpy as np
    sign=_signs(y)
    Y=np.column_stack([(sign==v).astype(np.float64) for v in (-1,0,1)]+
                      [np.sign(y)*np.log1p(np.abs(y))])
    return np.linalg.solve(K+float(alpha)*np.eye(len(K)),Y)


def _summary(y,pred,cls,rows,idx):
    import numpy as np
    result=probe._metrics(y,pred,cls,rows,idx)
    # A continuous near-zero head cannot get free credit for classifying
    # +epsilon/-epsilon as zero. Enforce sign AND calibrated magnitude.
    positive=np.asarray(y)>0;negative=np.asarray(y)<0;zero=np.asarray(y)==0
    small=(np.abs(y)>0)&(np.abs(y)<=0.05)
    result.update({
        'exact_zero_within_0_05':float(np.mean(np.abs(pred[zero])<=0.05)) if zero.any() else None,
        'near_nonzero_strict_sign':float(np.mean(_signs(pred[small])==_signs(y[small]))) if small.any() else None,
        'negative_strict_sign':float(np.mean(pred[negative]<0)) if negative.any() else None,
        'positive_strict_sign':float(np.mean(pred[positive]>0)) if positive.any() else None,
        'worst_absolute_error':float(np.max(np.abs(y-pred))),
        'signed_rmse':float(np.sqrt(np.mean((y-pred)**2)))})
    return result


def _retention(y,pred,cls,baseline,rows):
    import numpy as np
    y=np.asarray(y,dtype=np.float64)
    baseline=np.asarray(baseline,dtype=np.float64)
    pred=np.asarray(pred,dtype=np.float64)
    original_class=_classify_continuous(baseline)
    actual=_signs(y)
    source_idx=np.arange(len(y),dtype=np.int64)
    candidate=_summary(y,pred,cls,rows,source_idx)
    original_metrics=_summary(y,baseline,original_class,rows,source_idx)
    correct=(original_class==actual)
    kept=np.mean(cls[correct]==actual[correct]) if correct.any() else None
    by_class={}
    for sign,label in ((-1,'negative'),(0,'zero'),(1,'positive')):
        indices=correct&(actual==sign)
        by_class[label]={'original_correct':int(indices.sum()),
                         'preserved_fraction':float(np.mean(cls[indices]==sign)) if indices.any() else None}
    condition=(kept is not None and kept>=0.95 and
               candidate['signed_mae']<=original_metrics['signed_mae']*1.05 and
               all(x['preserved_fraction'] is None or x['preserved_fraction']>=0.95
                   for x in by_class.values()))
    return {'status':'PASS' if condition else 'FAIL',
            'original':original_metrics,'candidate':candidate,
            'original_correct_kept_fraction':float(kept) if kept is not None else None,
            'by_class':by_class,'max_mae_regression_fraction':0.05,
            'minimum_retention_fraction':0.95}


def _gates(test,retention,baseline):
    # A pre-exposed synthetic test split can assess repair direction; it must
    # never serve as a virgin promotion test.
    family=test.get('by_family',{})
    all_metrics=test['all']
    required=('balanced_sign_accuracy','triple_accuracy','zero_recall',
              'exact_zero_within_0_05','near_nonzero_strict_sign')
    accuracy=all(all_metrics.get(k) is not None and all_metrics[k]>=0.95 for k in required)
    family_ok=bool(family) and all(v['balanced_sign_accuracy']>=0.95 and
                v.get('triple_accuracy',0)>=0.95 for v in family.values())
    # In addition to classification and zero tolerance, the signed numeric
    # MAE must not worsen from the existing head.
    magnitude=all_metrics['signed_mae']<=baseline['signed_mae']
    return {'strict_sign_and_zero':accuracy,'family_transfer':family_ok,
            'magnitude_no_regression':magnitude,
            'source_retention':retention is not None and retention['status']=='PASS',
            'all_bounded_gates_pass':bool(accuracy and family_ok and magnitude and
                                      retention is not None and retention['status']=='PASS'),
            'virgin_test_unavailable':True,
            'promotion_authorized':False}


def _group_scores(rows,y,pred,cls,indices):
    import numpy as np
    all_result=_summary(y[indices],pred[indices],cls[indices],rows,indices)
    by_surface={};by_family={}
    for field,into in (('surface',by_surface),('family',by_family)):
        for name in sorted({rows[int(i)][field] for i in indices}):
            ix=np.asarray([i for i in indices if rows[int(i)][field]==name],dtype=np.int64)
            into[name]=_summary(y[ix],pred[ix],cls[ix],rows,ix)
    return {'all':all_result,'by_surface':by_surface,'by_family':by_family}


def _check_archive_groups(rows):
    groups=defaultdict(list)
    for r in rows:groups[r['group']].append(r)
    for members in groups.values():
        splits={r['split'] for r in members}
        if len(splits)!=1 or len(members)!=6:
            raise ValueError('HEAD_LAB_TRAIN_TEST_GROUP_LEAKAGE_OR_INCOMPLETE')
        classes={-1 if Fraction(r['target'])<0 else 1 if Fraction(r['target'])>0 else 0
                 for r in members}
        if classes!={-1,0,1}:raise ValueError('HEAD_LAB_INCOMPLETE_SIGN_TRIPLE')
    return len(groups)


def _evaluate_head(input_metadata,input_arrays,source=None,selected_features=None):
    import numpy as np
    rows=input_metadata['dataset'];_check_archive_groups(rows)
    partitions=probe._test_partitions(rows)
    y=_targets(rows)
    old=np.asarray(input_arrays['production_head_predictions'],dtype=np.float64)
    baseline=_group_scores(rows,y,old,_classify_continuous(old),partitions['test'])
    source_meta,source_arrays=source if source is not None else (None,None)
    candidates=[];selected=[]
    for feature in (selected_features or FEATURE_POLICIES):
        key='lora__'+feature
        if key not in input_arrays:continue
        train,dev,test=(partitions[k] for k in ('train','dev','test'))
        source_x=(source_arrays.get(key) if source_arrays is not None else None)
        K,Kdev,Ktest,Ksource=_grams(input_arrays[key][train],input_arrays[key][dev],
                                   input_arrays[key][test],source_x)
        prod_dev=old[dev];prod_test=old[test]
        y_source=(_targets(source_meta['dataset']) if source_meta is not None else None)
        old_source=(np.asarray(source_arrays['predictions'],dtype=np.float64)
                    if source_arrays is not None else None)
        solved={alpha:_fit(K,y[train],alpha) for alpha in ALPHAS}
        for decoder in DECODERS:
            chosen=None
            for alpha in ALPHAS:
                weights=solved[alpha]
                dc,dv=_decode(Kdev@weights,prod_dev,decoder)
                m=_summary(y[dev],dv,dc,rows,dev)
                # Rank by development only; no source/test data is involved.
                rank=(m['balanced_sign_accuracy'],m['triple_accuracy'] or 0,
                      m['near_nonzero_strict_sign'] or 0,-m['signed_mae'],-alpha)
                if chosen is None or rank>chosen['rank']:
                    chosen={'rank':rank,'alpha':alpha,'weights':weights,
                            'development':m}
            tc,tv=_decode(Ktest@chosen['weights'],prod_test,decoder)
            predictions=old.copy();predictions[test]=tv
            classes=_classify_continuous(old);classes[test]=tc
            metrics=_group_scores(rows,y,predictions,classes,test)
            retention=None
            if source_arrays is not None and Ksource is not None:
                sc,sv=_decode(Ksource@chosen['weights'],old_source,decoder)
                retention=_retention(y_source,sv,sc,old_source,source_meta['dataset'])
            gates=_gates(metrics,retention,baseline['all'])
            if source_meta is not None and not source_meta.get('source_complete',False):
                gates['source_retention']=False
                gates['all_bounded_gates_pass']=False
                gates['incomplete_source_coverage']=True
            result={'id':feature+'.'+decoder,'feature':feature,'decoder':decoder,
                    'alpha':chosen['alpha'],'development':chosen['development'],
                    'test':metrics,'source_retention':retention,'gates':gates,
                    'head_only_temporary':True,'checkpoint_written':False}
            candidates.append(result)
            selected.append((chosen['rank'],result['id']))
    if not candidates:raise ValueError('HEAD_LAB_NO_COMPATIBLE_CANDIDATE_FEATURES')
    bestid=max(selected)[1]
    return {'production_baseline':baseline,'candidates':candidates,
            'dev_selected_candidate_id':bestid,
            'recommended_for_production':'NONE',
            'overall_status':'BOUNDED_HEAD_ONLY_EXPERIMENT_NO_PROMOTION',
            'source_retention_status':('COMPLETE_ORIGINAL_DEV' if source_meta is not None and source_meta.get('source_complete',False) else
                 'PARTIAL_ORIGINAL_DEV' if source is not None else 'UNVERIFIED_NO_ORIGINAL_FEATURES'),
            'selection_policy':'development_only (balanced sign, triples, near-nonzero, negative MAE)',
            'held_out_exposure':'PREVIOUSLY_ANALYZED_SYNTHETIC_SET_NOT_VIRGIN',
            'training_state_modified':False,'production_weights_modified':False}


def evaluate(args):
    if not args.input or not args.output:raise ValueError('HEAD_LAB_REQUIRES_INPUT_AND_OUTPUT')
    import numpy as np
    path=_archive_path(args.input,probe.CAPTURE_NAME)
    info,arrays=probe._verify_archive(path,require_source=not args.offline)
    source=None
    if args.source_features:
        source=_verified_source_archive(args.source_features,info['lineage'],not args.offline)
    out=_output(args.output,(Path(args.input).resolve(),
                  Path(info['lineage']['source_run']).resolve(),
                  Path(info['lineage']['expanded_run']).resolve(),
                  Path(args.source_features).resolve() if args.source_features else Path(args.input).resolve()))
    report=_evaluate_head(info,arrays,source=source)
    report.update({'schema':SCHEMA,'status':'INCOMPLETE_SOURCE_RETENTION' if source is None else
                   'EXPERIMENT_COMPLETE_NO_PROMOTION',
                   'dataset_sha256':info['dataset_sha256'],
                   'source_dataset_sha256':source[0]['dataset_sha256'] if source else None,
                   'lineage':info['lineage'],
                   'captures_verified':not args.offline,
                   'parameters_saved':False,
                   'fitting_scope':'temporary ridge heads trained on synthetic TRAIN groups only',
                   'test_scope':'synthetic TEST groups and separately captured original certified examples',
                   'note':'Candidate selection used development only; synthetic test was exposed in earlier probe work. No promotion or component-causation certification.'})
    out.mkdir(parents=True,exist_ok=False)
    (out/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf-8')
    with (out/'candidate_scores.csv').open('w',encoding='utf-8',newline='') as f:
        columns=['id','alpha','dev_sign','test_sign','test_triples','test_zero',
                 'test_near_nonzero','test_signed_mae','source_retention','bounded_gates']
        w=csv.DictWriter(f,fieldnames=columns);w.writeheader()
        for c in report['candidates']:
            m=c['test']['all'];r=c['source_retention']
            w.writerow({'id':c['id'],'alpha':c['alpha'],
                'dev_sign':c['development']['balanced_sign_accuracy'],
                'test_sign':m['balanced_sign_accuracy'],
                'test_triples':m['triple_accuracy'],
                'test_zero':m['exact_zero_within_0_05'],
                'test_near_nonzero':m['near_nonzero_strict_sign'],
                'test_signed_mae':m['signed_mae'],
                'source_retention':r['status'] if r else 'UNVERIFIED',
                'bounded_gates':c['gates']['all_bounded_gates_pass']})
    print(probe.canonical({'status':report['status'],
        'production':baseline_summary(report['production_baseline']),
        'dev_selected':report['dev_selected_candidate_id'],
        'candidate_count':len(report['candidates']),
        'source_retention':report['source_retention_status'],
        'report':str(out/'report.json'),
        'promotion_authorized':False}),flush=True)
    return report


def baseline_summary(data):
    m=data['all']
    return {k:m[k] for k in ('cases','balanced_sign_accuracy','triple_accuracy',
                              'zero_within_0_05','signed_mae')}


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('oracle','source-capture','evaluate'),default='oracle')
    p.add_argument('--input',type=Path)
    p.add_argument('--source-features',type=Path)
    p.add_argument('--output',type=Path)
    p.add_argument('--run',type=Path)
    p.add_argument('--expanded-run',type=Path)
    p.add_argument('--model-id',default='Qwen/Qwen3-0.6B')
    p.add_argument('--source-cases',type=int,default=438)
    p.add_argument('--device',default='cuda')
    p.add_argument('--capture-batch',type=int,default=2)
    p.add_argument('--head-batch',type=int,default=4)
    p.add_argument('--include-intermediate-layers',action='store_true',
                   help='Optional Qwen recapture for earlier-layer retention; original head always uses verified training dev cache')
    p.add_argument('--max-input-tokens',type=int,default=1024)
    p.add_argument('--offline',action='store_true',help='Analysis only. Source fingerprints unverified; no acceptance claimed')
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    if args.mode=='oracle':
        deck=probe.make_deck()
        print(probe.canonical({'status':'ORACLE_ONLY','model_evaluated':False,
                               'deck':probe.validate_deck(deck),
                               'head_candidate_count':len(FEATURE_POLICIES)*len(DECODERS)}))
    elif args.mode=='source-capture':source_capture(args)
    else:evaluate(args)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
