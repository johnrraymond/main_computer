#!/usr/bin/env python3
"""GAMER: three-class NEGATIVE/ZERO/POSITIVE sign head, diagnostics ONLY.

Exactly the old, fingerprinted oracle train/dev/test splits. Frozen Qwen + LoRA
snapshots + frozen production C0. Fit only small CPU torch softmax classifiers:
  final LoRA mean-pooled -> 1024 dimensions
  frozen C0 final encoder GELU -> 512 dimensions
  frozen C0 mid GELU -> 128 dimensions

DEV chooses model class, regularization and early stopping. TEST is scored
only afterward, with exact same-context triples and held-out hawk family.
No magnitude prediction, no production C0 update, no LoRA update or promotion.

  python -m tools.nanojev_gamer_v1_c0_sign_head \
      --causal runtime/reports/gamer-v1/c0-sign-causality-0001 \
      --output runtime/reports/gamer-v1/c0-sign-head-0001 \
      --steps 0,500,1500,3000 --device cuda
"""
from __future__ import annotations

import argparse
import gc
import json
import os
from fractions import Fraction
from pathlib import Path

import numpy as np

SCHEMA='nanojev.gamer.v1.frozen-representation-three-class-sign-head.v1'
STAGES=('lora_final_mean100','enc_gelu_3','mid_gelu')
LABELS=(-1,0,1)
KINDS=('linear','rbf_1.0','rbf_4.0')
REGULARIZATIONS=(0.001,0.05)


def _sha(path):
    from tools import nanojev_gamer_v1_sufficiency as suff
    return suff._digest(Path(path))


def _source(causal_dir,steps):
    from tools import nanojev_gamer_v1_c0_sign_causality as causal
    causal_dir=Path(causal_dir).resolve()
    causal_path=causal_dir/'final_report.json'
    r=json.loads(causal_path.read_text(encoding='utf-8'))
    if (r.get('schema')!=causal.SCHEMA or
        r.get('status')!='COMPLETE_READ_ONLY_NO_CAUSAL_DESTRUCTION_CERTIFICATION' or
        r.get('promotion')!='BLOCKED_DIAGNOSTIC_ONLY' or
        r.get('no_training_or_model_mutation') is not True):
        raise ValueError('GAMER_SIGN_HEAD_REQUIRES_COMPLETE_FROZEN_CAUSAL_AUDIT')
    from tools import nanojev_gamer_v1_lora_sign_stress as stress
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    requested=causal._select_steps(r,steps)
    audit_path=Path(r['audit_source']).resolve()
    audit=json.loads(audit_path.read_text(encoding='utf-8'))
    if (audit.get('schema')!=stress.AUDIT_SCHEMA or
        audit.get('status')!='AUDIT_COMPLETE_NO_PRODUCTION_UPDATES' or
        audit.get('no_c0_weight_update') is not True or
        _sha(audit_path)!=r['frozen_fingerprints']['audit_report']):
        raise ValueError('GAMER_SIGN_HEAD_AUDIT_LINEAGE_MISMATCH')
    source,source_report,meta,arrays,capture=stress._source(audit['source'])
    training=Path(audit['training_run']).resolve()
    exp=json.loads((training/'experiment.json').read_text(encoding='utf-8'))
    lineage=meta['lineage']
    if (r['oracle_dataset_sha256']!=meta['dataset_sha256'] or
        audit['oracle_dataset_sha256']!=meta['dataset_sha256'] or
        r['frozen_fingerprints']['preservation_report']!=_sha(source/'final_report.json') or
        r['frozen_fingerprints']['training_plan']!=_sha(training/'experiment.json') or
        r['frozen_fingerprints']['original_adapter']!=lineage['adapter_sha256'] or
        r['frozen_fingerprints']['c0_checkpoint']!=lineage['checkpoint_sha256'] or
        audit['training_deck_sha256']!=exp['training_deck_sha256'] or
        exp['source_preservation']!=str(source) or
        any(str(s) not in audit['steps'] for s in requested)):
        raise ValueError('GAMER_SIGN_HEAD_PROTECTED_ORACLE_OR_MODEL_MISMATCH')
    rows=meta['dataset']
    deck=oracle.make_deck(
        sum(x['split']=='train' for x in rows)//6,
        sum(x['split']=='dev' for x in rows)//6,
        sum(x['split']=='test' and x['family']=='signed_offset' for x in rows)//6,
        include_ood=True)
    if [x.public() for x in deck]!=rows:
        raise ValueError('GAMER_SIGN_HEAD_ORACLE_DECK_CHANGED')
    from tools import nanojev_gamer_v1_c0_boundary as c0
    from tools import nanojev_gamer_v1_sufficiency as suff
    ckpt=Path(lineage['checkpoint_path'])
    adapter=Path(lineage['adapter_dir'])
    if (_sha(ckpt)!=lineage['checkpoint_sha256'] or
        stress._weights_hash(adapter)!=lineage['adapter_sha256']):
        raise ValueError('GAMER_SIGN_HEAD_FROZEN_WEIGHTS_CHANGED')
    snapshots={}
    for step in requested:
        snap=training/'snapshots'/f'step-{step:07d}'
        actual=stress._verify_snapshot(snap,step)
        if actual!=audit['steps'][str(step)]['snapshot_sha256'] or actual!=r['steps'][str(step)]['snapshot_adapter_sha256']:
            raise ValueError('GAMER_SIGN_HEAD_SNAPSHOT_CHANGED')
        snapshots[step]=(snap,actual)
    return {'causal':r,'audit':audit,'causal_path':causal_path,'audit_path':audit_path,
            'source':source,'training':training,'source_report':source_report,
            'meta':meta,'archive':arrays,'capture':capture,'rows':rows,'deck':deck,
            'ckpt':ckpt,'adapter':adapter,'snapshots':snapshots,'requested':requested}


def _protected_hashes(source):
    from tools import nanojev_gamer_v1_lora_sign_stress as stress
    return {'causal_report':_sha(source['causal_path']),
            'audit_report':_sha(source['audit_path']),
            'preservation_report':_sha(source['source']/'final_report.json'),
            'training_plan':_sha(source['training']/'experiment.json'),
            'production_c0':_sha(source['ckpt']),
            'original_lora':stress._weights_hash(source['adapter'])}


def _output(path,protected):
    dest=Path(path).resolve()
    if len(dest.name)>110 or (os.name=='nt' and len(str(dest/'final_report.json'))>=240):
        raise ValueError('GAMER_SIGN_HEAD_OUTPUT_PATH_TOO_LONG')
    for src in protected:
        src=Path(src).resolve()
        if dest==src or src in dest.parents or dest in src.parents:
            raise ValueError('GAMER_SIGN_HEAD_OUTPUT_OVERLAPS_SOURCE')
    if dest.exists():raise FileExistsError('GAMER_SIGN_HEAD_OUTPUT_ALREADY_EXISTS')
    return dest


def _partitions(rows):
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    split=oracle._test_partitions(rows)
    if len(rows)!=sum(len(v) for v in split.values()):
        raise ValueError('GAMER_SIGN_HEAD_INCOMPLETE_PARTITIONS')
    return split


def _labels(rows):
    return np.asarray([int((Fraction(r['target'])>0)-(Fraction(r['target'])<0))+1 for r in rows],dtype=np.int64)


def _confusion(rows,idx,cls):
    idx=np.asarray(idx,dtype=int)
    target=_labels(rows)[idx]
    pred=np.asarray(cls,dtype=np.int64)
    if len(pred)!=len(idx) or (len(pred) and ((pred<0).any() or (pred>2).any())):
        raise ValueError('GAMER_SIGN_HEAD_INVALID_CLASS_OUTPUT')
    return [[int(np.sum((target==i)&(pred==j))) for j in range(3)] for i in range(3)]


def _metrics(rows,idx,pred,*,probabilities=None):
    idx=np.asarray(idx,dtype=np.int64)
    cls=np.asarray(pred,dtype=np.int64)
    confusion=_confusion(rows,idx,cls)
    actual=_labels(rows)[idx]
    recalls={str(label):float(np.mean(cls[actual==i]==i)) if np.any(actual==i) else None
             for i,label in enumerate(LABELS)}
    triples={}
    # Count only COMPLETE sign-reversal triples with a single prompt surface.
    groups={}
    for local,n in enumerate(idx):
        r=rows[int(n)]
        groups.setdefault((r['group'],r['surface']),[]).append(local)
    for group,loc in groups.items():
        if len(loc)!=3 or set(actual[loc])!={0,1,2}:
            raise ValueError('GAMER_SIGN_HEAD_NONTRIPLE_EVALUATION_GROUP')
        triples[group]=bool(np.all(cls[loc]==actual[loc]))
    both=sum(bool((cls[loc]==actual[loc])[actual[loc]!=1].all()) for loc in groups.values())
    if len(triples)==0:raise ValueError('GAMER_SIGN_HEAD_NO_COMPLETE_TRIPLES')
    nll=None
    if probabilities is not None:
        probs=np.asarray(probabilities,dtype=np.float64)
        if probs.shape!=(len(idx),3) or not np.isfinite(probs).all():
            raise ValueError('GAMER_SIGN_HEAD_INVALID_PROBABILITIES')
        nll=float(-np.mean(np.log(np.clip(probs[np.arange(len(idx)),actual],1e-12,1.))))
    return {'cases':len(idx),'correct':int((cls==actual).sum()),
            'balanced_sign_accuracy':float(np.mean(list(recalls.values()))),
            'recall':recalls,'min_class_recall':float(min(recalls.values())),
            'confusion_rows_true_cols_pred_negative_zero_positive':confusion,
            'complete_triples':int(sum(triples.values())),
            'triples':len(triples),'triple_accuracy':float(np.mean(list(triples.values()))),
            'sign_reversal_both_correct':int(both),
            'zero_class_would_force_exact_zero_if_combined':True,
            'nll':nll}


def _metrics_by_family(rows,idx,pred,probs):
    idx=np.asarray(idx,dtype=int)
    pred=np.asarray(pred,dtype=np.int64)
    probs=np.asarray(probs,dtype=np.float64)
    all_metrics=_metrics(rows,idx,pred,probabilities=probs)
    family_metrics={}
    for family in sorted({rows[int(i)]['family'] for i in idx}):
        keep=np.asarray([j for j,k in enumerate(idx) if rows[int(k)]['family']==family],dtype=int)
        family_metrics[family]=_metrics(rows,idx[keep],pred[keep],probabilities=probs[keep])
    all_metrics['by_family']=family_metrics
    good=lambda m: m['min_class_recall']>=.95 and m['triple_accuracy']>=.95
    all_metrics['passes_sign_zero_and_triples_gate']=bool(good(all_metrics) and all(good(m) for m in family_metrics.values()))
    return all_metrics


def _train_only_maps(features,split):
    """Prepare full linear and nonlinear RBF feature maps using TRAIN only."""
    x=np.asarray(features,dtype=np.float32)
    if x.ndim!=2 or x.shape[1]<2 or not np.isfinite(x).all():
        raise ValueError('GAMER_SIGN_HEAD_BAD_STAGE_ACTIVATIONS')
    train=split['train']
    a=x[train].astype(np.float64)
    mu=a.mean(axis=0,keepdims=True)
    scale=np.sqrt(np.maximum(np.mean((a-mu)**2,axis=0,keepdims=True),1e-6))
    z=((x.astype(np.float64)-mu)/scale)
    if not np.isfinite(z).all():raise ValueError('GAMER_SIGN_HEAD_INVALID_TRAIN_SCALING')
    # Keep the native dimensions for linear sign regression; no sign-bearing
    # direction is silently removed by a PCA dimensionality bottleneck.
    z=np.asarray(z,dtype=np.float32)
    maps={'linear':z}
    at=z[train].astype(np.float64)
    aa=at@at.T
    dtrain=np.maximum(np.diag(aa)[:,None]+np.diag(aa)[None,:]-2*aa,0)/z.shape[1]
    diag=dtrain[np.triu_indices(len(at),1)]
    median=float(np.median(diag[diag>1e-12])) if np.any(diag>1e-12) else 0.
    if median>1e-12:
        other=z.astype(np.float64)@at.T
        squared=np.sum(z.astype(np.float64)**2,axis=1,keepdims=True)
        ds=np.maximum(squared+np.diag(aa)[None,:]-2*other,0)/z.shape[1]
        for width in (1.,4.):
            maps[f'rbf_{width}']=np.asarray(np.exp(-ds/(median*width)),dtype=np.float32)
    else:
        # Degenerate features cannot support nonlinear sign classification.
        maps['rbf_1.0']=np.zeros((len(x),len(train)),dtype=np.float32)
        maps['rbf_4.0']=np.zeros((len(x),len(train)),dtype=np.float32)
    return maps,{'original_dim':int(x.shape[1]),'train_cases':len(train),
                 'scaling':'TRAIN_ONLY_PER_COORDINATE','rbf_median_train_distance':median}


def _softmax(scores):
    logits=np.asarray(scores,dtype=np.float64)
    logits-=logits.max(axis=1,keepdims=True)
    y=np.exp(logits)
    return y/y.sum(axis=1,keepdims=True)


def _train_classifier(features,train_y,dev_x,dev_y,*,regularization,epochs,lr=.08,select_dev=True):
    """Only three-class cross-entropy; CPU; no pretrained model parameters."""
    import torch
    import torch.nn.functional as F
    data=np.asarray(features,dtype=np.float32)
    valid=np.asarray(dev_x,dtype=np.float32)
    if data.ndim!=2 or valid.shape[1]!=data.shape[1] or not np.isfinite(data).all() or not np.isfinite(valid).all():
        raise ValueError('GAMER_SIGN_HEAD_BAD_TRAINING_FEATURES')
    y=np.asarray(train_y,dtype=np.int64)
    ydev=np.asarray(dev_y,dtype=np.int64)
    if set(y)!={0,1,2} or np.min(np.bincount(y,minlength=3))<3:
        raise ValueError('GAMER_SIGN_HEAD_MISSING_BALANCED_TRAIN_CLASSES')
    xt=torch.from_numpy(data)
    yt=torch.from_numpy(y)
    xd=torch.from_numpy(valid)
    yd=torch.from_numpy(ydev)
    # Softmax symmetry is broken by labeled gradients from zero initialization.
    w=torch.nn.Parameter(torch.zeros(data.shape[1],3,dtype=torch.float32))
    b=torch.nn.Parameter(torch.zeros(3,dtype=torch.float32))
    optimizer=torch.optim.Adam((w,b),lr=lr)
    best=None
    for epoch in range(1,epochs+1):
        optimizer.zero_grad()
        loss=F.cross_entropy(xt@w+b,yt) + regularization*(w.square().sum()/max(1,data.shape[1]))
        if not torch.isfinite(loss):raise ValueError('GAMER_SIGN_HEAD_NONFINITE_TRAIN_LOSS')
        loss.backward();optimizer.step()
        if epoch % 10 != 0 and epoch!=epochs:continue
        with torch.no_grad():
            dev_scores=(xd@w+b).numpy().copy()
            prob=_softmax(dev_scores)
            pred=np.argmax(prob,axis=1)
            recall=[float(np.mean(pred[ydev==k]==k)) if np.any(ydev==k) else 0. for k in (0,1,2)]
            balanced=float(np.mean(recall))
            dev_ce=float(-np.mean(np.log(np.clip(prob[np.arange(len(ydev)),ydev],1e-12,1.))))
            # The triple metric gets evaluated by caller (requires group ids),
            # here prioritize all three classes rather than majority accuracy.
            rank=(min(recall),balanced,-dev_ce,-epoch)
            if not select_dev or best is None or rank>best[0]:
                best=(rank,w.detach().numpy().copy(),b.detach().numpy().copy(),
                      {'epoch':epoch,'dev_min_class_recall':min(recall),
                       'dev_balanced_sign_accuracy':balanced,'dev_nll':dev_ce,
                       'dev_recall':{str(k-1):recall[k] for k in range(3)}})
    if best is None:raise ValueError('GAMER_SIGN_HEAD_NO_TRAINED_CHECKPOINT')
    return {'weights':best[1],'bias':best[2],'dev':best[3]}


def _evaluate_head(head,features,indices):
    data=np.asarray(features[indices],dtype=np.float32)
    logits=data.astype(np.float64)@head['weights'].astype(np.float64)+head['bias'].astype(np.float64)
    probability=_softmax(logits)
    return np.argmax(probability,axis=1),probability


def _store_head(path,raw,split,*,kind,model,regularization):
    """Persist exactly the disposable classifier and its train-only feature map.

    Contains neither original model weights nor any oracle test labels. RBF
    references are frozen TRAIN embeddings, not a refit on unseen examples.
    """
    dest=Path(path)
    if dest.exists():raise FileExistsError('GAMER_SIGN_HEAD_ARTIFACT_EXISTS')
    a=np.asarray(raw,dtype=np.float32)
    train=a[split['train']].astype(np.float64)
    mu=train.mean(axis=0,keepdims=True)
    std=np.sqrt(np.maximum(np.mean((train-mu)**2,axis=0,keepdims=True),1e-6))
    normalized=np.asarray((train-mu)/std,dtype=np.float32)
    reference=normalized if kind.startswith('rbf_') else np.empty((0,a.shape[1]),dtype=np.float32)
    bandwidth=0.
    if kind.startswith('rbf_'):
        at=normalized.astype(np.float64)
        products=at@at.T
        dist=np.maximum(np.diag(products)[:,None]+np.diag(products)[None,:]-2*products,0)/a.shape[1]
        upper=dist[np.triu_indices(len(dist),1)]
        upper=upper[upper>1e-12]
        if len(upper):bandwidth=float(np.median(upper))*float(kind.split('_')[1])
    payload={'schema':np.asarray(SCHEMA),
             'kind':np.asarray(kind),
             'mean':np.asarray(mu,dtype=np.float64),
             'std':np.asarray(std,dtype=np.float64),
             'train_reference':reference,
             'bandwidth':np.asarray(bandwidth,dtype=np.float64),
             'weights':np.asarray(model['weights'],dtype=np.float32),
             'bias':np.asarray(model['bias'],dtype=np.float32),
             'regularization':np.asarray(regularization,dtype=np.float32),
             'epoch':np.asarray(model['dev']['epoch'],dtype=np.int64)}
    temp=dest.with_suffix('.tmp.npz')
    try:
        np.savez_compressed(temp,**payload)
        os.replace(temp,dest)
    finally:
        if temp.exists():temp.unlink()
    return {'path':str(dest),'sha256':_sha(dest),'type':'DIAGNOSTIC_SIGN_HEAD_ONLY_NO_MAGNITUDE'}


def _replay_head(path,raw):
    """Reproduce signed class predictions from an isolated NPZ checkpoint."""
    a=np.asarray(raw,dtype=np.float32)
    with np.load(path,allow_pickle=False) as z:
        if str(z['schema'])!=SCHEMA:raise ValueError('GAMER_SIGN_HEAD_BAD_ARTIFACT_SCHEMA')
        kind=str(z['kind']);mean=z['mean'];std=z['std']
        if a.ndim!=2 or mean.shape!=(1,a.shape[1]) or std.shape!=mean.shape:
            raise ValueError('GAMER_SIGN_HEAD_ARTIFACT_INPUT_WIDTH_MISMATCH')
        if not np.isfinite(a).all() or not np.isfinite(std).all() or (std<=0).any():
            raise ValueError('GAMER_SIGN_HEAD_BAD_REPLAY_FEATURES')
        x=np.asarray((a.astype(np.float64)-mean.astype(np.float64))/std.astype(np.float64),dtype=np.float32)
        if kind.startswith('rbf_'):
            ref=z['train_reference'].astype(np.float64)
            band=float(z['bandwidth'])
            if band<=0:feat=np.zeros((len(a),len(ref)),dtype=np.float32)
            else:
                prod=x@ref.T
                distance=np.maximum(np.sum(x*x,axis=1,keepdims=True)+
                         np.sum(ref*ref,axis=1,keepdims=True).T-2*prod,0)/a.shape[1]
                feat=np.exp(-distance/band).astype(np.float32)
        elif kind=='linear':
            feat=x.astype(np.float32)
        else:raise ValueError('GAMER_SIGN_HEAD_UNKNOWN_SAVED_DECODER')
        if feat.shape[1]!=z['weights'].shape[0] or z['weights'].shape[1]!=3:
            raise ValueError('GAMER_SIGN_HEAD_ARTIFACT_WEIGHT_MISMATCH')
        probs=_softmax(feat.astype(np.float64)@z['weights'].astype(np.float64)+z['bias'].astype(np.float64))
    return np.argmax(probs,axis=1),probs


def _select_head(rows,features,*,epochs=160,nulls=3,seed=47,export_path=None):
    """Select entirely on DEV then expose one held-out TEST result per stage."""
    split=_partitions(rows)
    y=_labels(rows)
    maps,descriptor=_train_only_maps(features,split)
    best=None
    options=[]
    for kind in KINDS:
        data=maps[kind]
        for reg in REGULARIZATIONS:
            model=_train_classifier(data[split['train']],y[split['train']],
                                    data[split['dev']],y[split['dev']],
                                    regularization=reg,epochs=epochs)
            dev_pred,dev_probs=_evaluate_head(model,data,split['dev'])
            dev=_metrics(rows,split['dev'],dev_pred,probabilities=dev_probs)
            rank=(dev['min_class_recall'],dev['triple_accuracy'],
                  dev['balanced_sign_accuracy'],-dev['nll'],
                  int(kind=='linear'),-reg)
            options.append({'kind':kind,'regularization':reg,'dev':dev,
                            'epoch':model['dev']['epoch']})
            if best is None or rank>best[0]:
                best=(rank,kind,reg,model,dev)
    if best is None:raise ValueError('GAMER_SIGN_HEAD_NO_CANDIDATES')
    _,kind,reg,model,dev=best
    data=maps[kind]
    test_pred,test_probs=_evaluate_head(model,data,split['test'])
    artifact=None
    if export_path is not None:
        artifact=_store_head(export_path,features,split,kind=kind,model=model,regularization=reg)
        restored_pred,restored_probs=_replay_head(export_path,np.asarray(features)[split['test']])
        if not np.array_equal(restored_pred,test_pred) or not np.allclose(restored_probs,test_probs,atol=1e-5):
            raise ValueError('GAMER_SIGN_HEAD_SAVED_MODEL_REPLAY_PARITY_FAILED')
    test=_metrics_by_family(rows,split['test'],test_pred,test_probs)
    train_pred,train_probs=_evaluate_head(model,data,split['train'])
    trained=_metrics(rows,split['train'],train_pred,probabilities=train_probs)
    null_metrics=[]
    for i in range(nulls):
        shuffled=y[split['train']].copy()
        np.random.default_rng(seed+i).shuffle(shuffled)
        null_head=_train_classifier(data[split['train']],shuffled,
                                    data[split['dev']],y[split['dev']],
                                    regularization=reg,epochs=model['dev']['epoch'],
                                    select_dev=False)
        pred,prob=_evaluate_head(null_head,data,split['test'])
        null_metrics.append(_metrics(rows,split['test'],pred,probabilities=prob)['balanced_sign_accuracy'])
    return {'feature_policy':descriptor,
            'train_cases':len(split['train']),'dev_cases':len(split['dev']),
            'test_cases':len(split['test']),
            'model':'3-way softmax cross-entropy (trained diagnostic sign only)',
            'model_selection':'TRAIN_FIT_DEV_ONLY_TEST_UNTOUCHED',
            'selected':{'kind':kind,'regularization':reg,**model['dev']},
            'candidate_development_scores':options,
            'train':trained,'dev':dev,'test':test,
            'shuffled_train_label_test_sign_accuracy_nulls':null_metrics,
            'beats_all_shuffled_train_label_controls':bool(null_metrics and
                test['balanced_sign_accuracy']>max(null_metrics)),
            'any_magnitude_fit':False,'production_head_unchanged':True,
            'diagnostic_sign_head_artifact':artifact}


def _comparison(report):
    steps=sorted(map(int,report['steps']))
    first=report['steps'][str(steps[0])]
    last=report['steps'][str(steps[-1])]
    trajectory={}
    for stage in STAGES:
        a=first['stages'][stage]['test']
        b=last['stages'][stage]['test']
        trajectory[stage]={'baseline_sign':a['balanced_sign_accuracy'],
                           'last_sign':b['balanced_sign_accuracy'],
                           'baseline_triples':a['complete_triples'],
                           'last_triples':b['complete_triples'],
                           'last_zero_recall':b['recall']['0'],
                           'last_negative_recall':b['recall']['-1'],
                           'last_positive_recall':b['recall']['1'],
                           'last_unseen_hawk_sign':b['by_family']['hawk_indifference']['balanced_sign_accuracy'],
                           'last_passed_all_family_gates':b['passes_sign_zero_and_triples_gate']}
    return {'first_step':steps[0],'last_step':steps[-1],
            'stages':trajectory,
            'conclusion_policy':'A successful mid_gelu sign head isolates a trainable sign readout; failure does not certify absence of sign information; no magnitude repair is inferred.'}


def run(args):
    import torch
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    from tools import nanojev_gamer_v1_c0_sign_causality as causal
    from tools import nanojev_gamer_v1_c0_boundary as c0
    from tools import nanojev_gamer_v1_lora_sign_stress as stress
    from tools import nanojev_gamer_v1_handoff as gamer
    source=_source(args.causal,args.steps)
    out=_output(args.output,(Path(args.causal),source['audit_path'].parent,
        source['training'],source['source'],source['ckpt'].parent,
        source['adapter'],source['capture']))
    baseline=_protected_hashes(source)
    if baseline['production_c0']!=source['meta']['lineage']['checkpoint_sha256'] or \
       baseline['original_lora']!=source['meta']['lineage']['adapter_sha256']:
        raise ValueError('GAMER_SIGN_HEAD_FROZEN_SOURCE_FINGERPRINT_MISMATCH')
    meta=source['meta'];rows=source['rows'];deck=source['deck']
    # The original frozen C0 still reproduces the production boundary.
    net=c0._load_head(meta,args.device)
    net.eval()
    original=np.asarray(source['archive']['lora__final_flat'],dtype=np.float32)
    _,_,_,pred=causal._read_stages(net,original,device=args.device,batch=args.head_batch)
    c0._verify_baseline(pred,source['archive']['production_head_predictions'])
    del net,original,pred
    gc.collect()
    if torch.cuda.is_available():torch.cuda.empty_cache()
    outcome={'schema':SCHEMA,'status':'INCOMPLETE_READ_ONLY',
             'source_causal_report':str(source['causal_path']),
             'source_audit_report':str(source['audit_path']),
             'fingerprints':baseline,'oracle_dataset_sha256':meta['dataset_sha256'],
             'snapshots':{str(k):v[1] for k,v in source['snapshots'].items()},
             'head_config':{'epochs':args.epochs,'nulls':args.nulls,'seed':args.seed,
                            'algorithms':list(KINDS),'regularization':list(REGULARIZATIONS)},
             'train_split_only_sign_fit':True,'unseen_hawk_test_only':True,
             'no_lora_or_c0_training':True,
             'magnitude_regression_not_tested':True,
             'promotion':'BLOCKED_DIAGNOSTIC_ONLY',
             'steps':{}}
    out.mkdir(parents=True,exist_ok=False)
    for step,(snap,stored_sha) in source['snapshots'].items():
        if stress._verify_snapshot(snap,step)!=stored_sha:
            raise ValueError('GAMER_SIGN_HEAD_SNAPSHOT_CHANGED_DURING_RUN')
        print(f'GAMER SIGN-ONLY HEAD: snapshot {step}, frozen LoRA/C0',flush=True)
        _,tok,model=gamer.model_components(meta['lineage']['model_id'],args.device,adapter_dir=snap)
        features,_=oracle._capture_profiles(torch,model,tok,deck,
            device=args.device,max_length=meta['lineage']['configuration']['max_length'],
            batch_size=args.capture_batch,curvature=meta['lineage']['configuration']['curvature'],
            verify_exact_final=True)
        del model,tok
        gc.collect()
        if torch.cuda.is_available():torch.cuda.empty_cache()
        x=np.asarray(features['final_flat'],dtype=np.float16).astype(np.float32)
        if step==0:
            expected=np.asarray(source['archive']['lora__final_flat'],dtype=np.float16).astype(np.float32)
            if expected.shape!=x.shape or float(np.max(np.abs(expected-x)))>.01:
                raise ValueError('GAMER_SIGN_HEAD_ZERO_STEP_CAPTURE_MISMATCH')
        net=c0._load_head(meta,args.device)
        net.eval()
        stages,_,_,pred=causal._read_stages(net,x,device=args.device,batch=args.head_batch)
        production=stress._head_metrics(rows,pred)
        expected=source['audit']['steps'][str(step)]['metrics']['production_c0']['test']
        got=production['test']
        if (abs(expected['balanced_sign_accuracy']-got['balanced_sign_accuracy'])>1e-10 or
            expected['triple_all_correct']!=got['triple_all_correct'] or
            abs(expected['signed_mae']-got['signed_mae'])>.001):
            raise ValueError('GAMER_SIGN_HEAD_PRODUCTION_PARITY_MISMATCH')
        stage_inputs={'lora_final_mean100':np.asarray(features['final_mean100'],dtype=np.float32),
                      'enc_gelu_3':stages['enc_gelu_3'],
                      'mid_gelu':stages['mid_gelu']}
        del net,stages,features,x,pred
        gc.collect()
        if torch.cuda.is_available():torch.cuda.empty_cache()
        reports={}
        for name in STAGES:
            print('  fitting diagnostic sign classifier: '+name,flush=True)
            artifact=out/f'step-{step:07d}-{name}-sign-only.npz'
            reports[name]=_select_head(rows,stage_inputs[name],epochs=args.epochs,
                                       nulls=args.nulls,seed=args.seed,export_path=artifact)
            t=reports[name]['test']
            print(f'    test sign={t["balanced_sign_accuracy"]:.4f}, -={t["recall"]["-1"]:.3f}, '
                  f'0={t["recall"]["0"]:.3f}, +={t["recall"]["1"]:.3f}, '
                  f'triples={t["complete_triples"]}/{t["triples"]}, '
                  f'hawk={t["by_family"]["hawk_indifference"]["balanced_sign_accuracy"]:.3f}',
                  flush=True)
        outcome['steps'][str(step)]={'snapshot_sha256':stored_sha,'stages':reports,
                                    'frozen_production_c0':production}
        gamer.atomic_json(out/f'step-{step:07d}.json',outcome['steps'][str(step)])
        gamer.atomic_json(out/'progress.json',{'status':'IN_PROGRESS','complete_steps':list(outcome['steps'])})
        del stage_inputs,reports
        gc.collect()
    if _protected_hashes(source)!=baseline:
        raise ValueError('GAMER_SIGN_HEAD_PROTECTED_SOURCE_CHANGED_DURING_RUN')
    outcome['trajectory_summary']=_comparison(outcome)
    outcome['status']='COMPLETE_SIGN_ONLY_DIAGNOSTIC_NO_PRODUCTION_UPDATES'
    outcome['interpretation']='Train-only sign classifier; DEV hyperparameter/early-stop selection, TEST scored once per stage and LoRA snapshot. Success demonstrates recoverability under tested readouts, not magnitude correctness. Failure does not prove information absence. No production promotion.'
    gamer.atomic_json(out/'final_report.json',outcome)
    print('GAMER SIGN-ONLY HEAD COMPLETE: '+str(out/'final_report.json'),flush=True)
    return outcome


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--causal',type=Path,required=True,help='Completed frozen C0 sign-causality diagnostic directory')
    p.add_argument('--output',type=Path,required=True,help='New, short, isolated report directory')
    p.add_argument('--steps',default='0,500,1500,3000')
    p.add_argument('--device',default='cuda')
    p.add_argument('--capture-batch',type=int,default=2)
    p.add_argument('--head-batch',type=int,default=8)
    p.add_argument('--epochs',type=int,default=160)
    p.add_argument('--nulls',type=int,default=3)
    p.add_argument('--seed',type=int,default=47)
    args=p.parse_args(argv)
    if args.capture_batch<1 or args.head_batch<1 or args.epochs<10 or args.epochs>2000 or args.nulls<1 or args.nulls>20:
        raise ValueError('GAMER_SIGN_HEAD_INVALID_DIAGNOSTIC_FLAGS')
    return run(args)


if __name__=='__main__':main()
