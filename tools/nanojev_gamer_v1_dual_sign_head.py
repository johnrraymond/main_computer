#!/usr/bin/env python3
"""GAMER diagnostic: independent zero/nonzero and polarity (negative/positive).

Train BOTH binary heads on all 576 expanded original-LoRA TRAIN sign cases
(96 groups x 2 surfaces x 3 signs). The original 72 DEV and 108 TEST
(including the entire hawk_indifference family) are identical and held out.
No original LoRA, frozen production C0, or numerical-magnitude head is updated.

Paired within-context triplets supply a train-only ordering penalty; DEV
selects the binary hyperparameters and combined zero-precedence threshold;
TEST is evaluated only once per stage. Small standalone binary heads saved as
NPZ (no adapter, head, or oracle test labels in an artifact), with exact replay.

Example:
 python -m tools.nanojev_gamer_v1_dual_sign_head \
   --sign-head runtime/reports/gamer-v1/c0-sign-head-0001 \
   --output runtime/reports/gamer-v1/c0-dual-sign-0001 \
   --steps 0,500,1500,3000 --device cuda
"""
from __future__ import annotations

import argparse
import gc
import json
import os
from pathlib import Path
from fractions import Fraction
import numpy as np

from tools import nanojev_gamer_v1_c0_sign_head as three

SCHEMA='nanojev.gamer.v1.expanded-train-dual-binary-sign-head.v1'
STAGES=('lora_final_mean100','enc_gelu_3','mid_gelu')
KINDS=('linear','rbf_1.0','rbf_4.0')
REGS=(.001,.05)
ZERO_THRESHOLDS=(.20,.35,.50,.65,.80)


def _json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _source(prior_dir,steps):
    """Verify old sign audit and the immutable snapshot/expanded-deck lineage."""
    root=Path(prior_dir).resolve()
    prior_path=root/'final_report.json'
    prior=_json(prior_path)
    if (prior.get('schema')!=three.SCHEMA or
        prior.get('status')!='COMPLETE_SIGN_ONLY_DIAGNOSTIC_NO_PRODUCTION_UPDATES' or
        prior.get('no_lora_or_c0_training') is not True or
        prior.get('promotion')!='BLOCKED_DIAGNOSTIC_ONLY'):
        raise ValueError('GAMER_DUAL_SIGN_REQUIRES_COMPLETED_THREE_CLASS_AUDIT')
    from tools import nanojev_gamer_v1_lora_sign_stress as stress
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    causal_path=Path(prior['source_causal_report']).resolve()
    if three._sha(causal_path)!=prior['fingerprints']['causal_report']:
        raise ValueError('GAMER_DUAL_SIGN_CAUSAL_REPORT_CHANGED')
    source=three._source(causal_path.parent,steps)
    if (prior['source_audit_report']!=str(source['audit_path']) or
        prior['oracle_dataset_sha256']!=source['meta']['dataset_sha256'] or
        prior['fingerprints']!=three._protected_hashes(source) or
        any(prior['snapshots'].get(str(step))!=sha for step,(_,sha) in source['snapshots'].items())):
        raise ValueError('GAMER_DUAL_SIGN_PRIOR_ORACLE_OR_WEIGHTS_CHANGED')
    cfg=_json(source['training']/'experiment.json')
    train_groups=int(cfg['train_groups'])
    deck=stress._dataset(source['meta'],train_groups)
    if oracle.deck_hash(deck)!=cfg['training_deck_sha256']:
        raise ValueError('GAMER_DUAL_SIGN_EXPANDED_TRAIN_DECK_CHANGED')
    old={r['id']:r for r in source['rows']}
    expanded=[d.public() for d in deck]
    mapping={r['id']:i for i,r in enumerate(expanded)}
    if any(mapping.get(k) is None or expanded[mapping[k]]!=v for k,v in old.items()):
        raise ValueError('GAMER_DUAL_SIGN_ORIGINAL_HOLDOUT_MISMATCH')
    old_devtest={k:v for k,v in old.items() if v['split']!='train'}
    new_devtest={v['id']:v for v in expanded if v['split']!='train'}
    if new_devtest!=old_devtest:
        raise ValueError('GAMER_DUAL_SIGN_DEV_TEST_MUST_BE_IDENTICAL')
    split=three._partitions(expanded)
    if len(split['train'])!=576 or len(split['dev'])!=72 or len(split['test'])!=108:
        raise ValueError('GAMER_DUAL_SIGN_REQUIRES_576_72_108_ORACLE')
    if any(expanded[int(i)]['family']!='signed_offset' for i in split['train']) or \
       len([i for i in split['test'] if expanded[int(i)]['family']=='hawk_indifference'])!=36:
        raise ValueError('GAMER_DUAL_SIGN_HAWK_OR_SPLIT_LEAKAGE')
    return {'root':root,'prior':prior,'prior_path':prior_path,'source':source,
            'deck':deck,'rows':expanded,'split':split,'old_indices':np.asarray(
                [mapping[r['id']] for r in source['rows']],dtype=np.int64),
            'expanded_deck_sha256':cfg['training_deck_sha256']}


def _fingerprints(src):
    return {'three_class_audit':three._sha(src['prior_path']),
            **three._protected_hashes(src['source'])}


def _triples(rows,train_indices):
    """TRAIN-only positions for (-,0,+) from the exact same group and surface."""
    locations={}
    for pos,idx in enumerate(np.asarray(train_indices,dtype=int)):
        r=rows[int(idx)]
        key=(r['group'],r['surface'])
        sign=int((Fraction(r['target'])>0)-(Fraction(r['target'])<0))
        group=locations.setdefault(key,{})
        if sign in group:raise ValueError('GAMER_DUAL_SIGN_DUPLICATE_TRIPLE_CLASS')
        group[sign]=pos
    if not locations or any(set(group)!=set((-1,0,1)) for group in locations.values()):
        raise ValueError('GAMER_DUAL_SIGN_TRAIN_TRIPLES_INCOMPLETE')
    return np.asarray([[v[-1],v[0],v[1]] for v in locations.values()],dtype=np.int64)


def _labels(rows):
    return np.asarray([int((Fraction(r['target'])>0)-(Fraction(r['target'])<0)) for r in rows],dtype=np.int8)


def _sigmoid(v):
    v=np.asarray(v,dtype=np.float64)
    return 1/(1+np.exp(-np.clip(v,-50,50)))


def _binary(train_x,train_y,dev_x,dev_y,triples,*,task,regularization,epochs,
            triple_weight=.25,margin=.5,lr=.04):
    """Independent binary BCE plus paired train-only ordering penalty.

    Zero head uses all cases. Polarity head uses NONZERO cases only for BCE;
    train-only triples constrain positive > negative in the same context.
    """
    import torch
    import torch.nn.functional as F
    x=np.asarray(train_x,dtype=np.float32)
    y=np.asarray(train_y,dtype=np.int8)
    dev_x=np.asarray(dev_x,dtype=np.float32)
    dev_y=np.asarray(dev_y,dtype=np.int8)
    ix=np.asarray(triples,dtype=np.int64)
    if (x.ndim!=2 or dev_x.ndim!=2 or dev_x.shape[1]!=x.shape[1] or
        len(x)!=len(y) or ix.ndim!=2 or ix.shape[1]!=3 or
        not np.isfinite(x).all() or not np.isfinite(dev_x).all() or
        task not in ('zero','polarity') or regularization<0 or epochs<1 or
        not set(y)=={-1,0,1} or np.any(ix<0) or np.any(ix>=len(y)) or
        np.any(y[ix]!=np.asarray([-1,0,1],dtype=np.int8))):
        raise ValueError('GAMER_DUAL_SIGN_BINARY_TRAIN_CONTRACT_VIOLATION')
    torch.manual_seed(47)
    a=torch.from_numpy(x)
    ad=torch.from_numpy(dev_x)
    triple=torch.from_numpy(ix)
    active=np.ones(len(y),dtype=bool) if task=='zero' else (y!=0)
    labels=(y==0).astype(np.float32) if task=='zero' else (y>0).astype(np.float32)
    yd=(dev_y==0).astype(np.float32) if task=='zero' else (dev_y>0).astype(np.float32)
    d_active=np.ones(len(dev_y),dtype=bool) if task=='zero' else (dev_y!=0)
    aa=a[torch.from_numpy(active)]
    yy=torch.from_numpy(labels[active]); dd=torch.from_numpy(yd[d_active])
    wd=torch.nn.Parameter(torch.zeros((x.shape[1],),dtype=torch.float32))
    bd=torch.nn.Parameter(torch.zeros((),dtype=torch.float32))
    optimizer=torch.optim.Adam((wd,bd),lr=lr)
    # DEV must have both conditional labels to select calibration fairly.
    if not set(yd[d_active])=={0.,1.}:raise ValueError('GAMER_DUAL_SIGN_MISSING_DEV_CLASS')
    best=None
    for epoch in range(1,epochs+1):
        optimizer.zero_grad()
        logits=aa@wd+bd
        loss=F.binary_cross_entropy_with_logits(logits,yy)
        if triple_weight>0:
            pair=a[triple].matmul(wd)+bd   # [n_group, negative, zero, positive]
            if task=='zero':
                order=F.softplus(margin-(pair[:,1]-pair[:,0])).mean()+\
                      F.softplus(margin-(pair[:,1]-pair[:,2])).mean()
                order=order/2
            else:
                order=F.softplus(margin-(pair[:,2]-pair[:,0])).mean()
            loss=loss+triple_weight*order
        loss=loss+(regularization/max(1,x.shape[1]))*wd.square().sum()
        if not torch.isfinite(loss):raise ValueError('GAMER_DUAL_SIGN_NONFINITE_OBJECTIVE')
        loss.backward();optimizer.step()
        if epoch%10 and epoch!=epochs:continue
        with torch.no_grad():
            prob=_sigmoid((ad@wd+bd).numpy())
            pred=prob[d_active]>=.5
            actual=yd[d_active].astype(bool)
            recall0=float(np.mean(~pred[~actual])); recall1=float(np.mean(pred[actual]))
            nll=float(-np.mean(actual*np.log(np.clip(prob[d_active],1e-12,1))+\
                        (~actual)*np.log(np.clip(1-prob[d_active],1e-12,1))))
            rank=(min(recall0,recall1),(recall0+recall1)/2,-nll,-epoch)
            if best is None or rank>best[0]:
                best=(rank,wd.detach().numpy().copy(),float(bd.detach().cpu()),
                      {'epoch':epoch,'dev_recall_0':recall0,'dev_recall_1':recall1,
                       'dev_min_binary_recall':min(recall0,recall1),
                       'dev_balanced_binary_accuracy':(recall0+recall1)/2,'dev_nll':nll})
    if best is None:raise ValueError('GAMER_DUAL_SIGN_EMPTY_TRAIN')
    return {'weights':best[1],'bias':best[2],'development':best[3]}


def _score(model,mapped):
    return _sigmoid(np.asarray(mapped,dtype=np.float64)@model['weights'].astype(np.float64)+float(model['bias']))


def _combine(p_zero,p_positive,threshold):
    pzero=np.asarray(p_zero,dtype=np.float64)
    ppos=np.asarray(p_positive,dtype=np.float64)
    if pzero.shape!=ppos.shape or not np.isfinite(pzero).all() or not np.isfinite(ppos).all():
        raise ValueError('GAMER_DUAL_SIGN_BAD_BINARY_OUTPUT')
    decision=np.where(pzero>=threshold,1,np.where(ppos>=.5,2,0)).astype(np.int64)
    prob=np.column_stack(((1-pzero)*(1-ppos),pzero,(1-pzero)*ppos))
    return decision,prob


def _binary_diag(y,prob,*,task,threshold=.5):
    y=np.asarray(y,dtype=np.int8);prob=np.asarray(prob,dtype=np.float64)
    mask=np.ones(len(y),dtype=bool) if task=='zero' else y!=0
    actual=(y[mask]==0) if task=='zero' else (y[mask]>0)
    pred=prob[mask]>=threshold
    return {'cases':int(mask.sum()),'decision_threshold':float(threshold),
            'balanced_accuracy':float((np.mean(~pred[~actual])+np.mean(pred[actual]))/2),
            'recall_negative_class':float(np.mean(~pred[~actual])),
            'recall_positive_class':float(np.mean(pred[actual]))}


def _conditional_test_metrics(rows,indices,p_zero,p_pos,zero_threshold):
    """Evaluate independent binary subproblems separately, including OOD."""
    ix=np.asarray(indices,dtype=int)
    y=_labels(rows)[ix]
    pz=np.asarray(p_zero,dtype=np.float64)
    pp=np.asarray(p_pos,dtype=np.float64)
    if len(ix)!=len(pz) or len(ix)!=len(pp):
        raise ValueError('GAMER_DUAL_SIGN_BAD_CONDITIONAL_TEST_LENGTH')
    def measured(keep):
        return {'zero_detection':_binary_diag(y[keep],pz[keep],task='zero',
                                              threshold=zero_threshold),
                'nonzero_polarity':_binary_diag(y[keep],pp[keep],task='polarity')}
    families={}
    for fam in sorted({rows[int(i)]['family'] for i in ix}):
        keep=np.asarray([j for j,i in enumerate(ix) if rows[int(i)]['family']==fam],dtype=int)
        families[fam]=measured(keep)
    return {'overall':measured(np.arange(len(ix))), 'by_family':families,
            'zero_threshold_chosen_on_dev':zero_threshold,
            'polarity_is_scored_only_on_actual_nonzero_cases':True}


def _raw_map_spec(raw,split):
    a=np.asarray(raw,dtype=np.float32)
    t=a[split['train']].astype(np.float64)
    mu=t.mean(axis=0,keepdims=True)
    std=np.sqrt(np.maximum(np.mean((t-mu)**2,axis=0,keepdims=True),1e-6))
    ref=np.asarray((t-mu)/std,dtype=np.float32)
    t=ref.astype(np.float64)
    gram=t@t.T
    dist=np.maximum(np.diag(gram)[:,None]+np.diag(gram)[None,:]-2*gram,0)/a.shape[1]
    upper=dist[np.triu_indices(len(t),1)]
    positive=upper[upper>1e-12]
    median=float(np.median(positive)) if len(positive) else 0.
    return mu,std,ref,median


def _map_from_spec(raw,kind,spec):
    mean,std,ref,median=spec
    a=np.asarray(raw,dtype=np.float32)
    if a.ndim!=2 or mean.shape!=(1,a.shape[1]) or not np.isfinite(a).all():
        raise ValueError('GAMER_DUAL_SIGN_SAVED_FEATURE_WIDTH_MISMATCH')
    z=np.asarray((a.astype(np.float64)-mean)/std,dtype=np.float32)
    if kind=='linear':return z
    if kind not in KINDS:raise ValueError('GAMER_DUAL_SIGN_UNRECOGNIZED_KIND')
    if median<=1e-12:return np.zeros((len(z),len(ref)),dtype=np.float32)
    x=z.astype(np.float64);t=ref.astype(np.float64)
    sq=np.maximum(np.sum(x*x,axis=1,keepdims=True)+np.sum(t*t,axis=1)[None,:]-2*x@t.T,0)/a.shape[1]
    return np.asarray(np.exp(-sq/(median*float(kind.split('_')[1]))),dtype=np.float32)


def _store(path,raw,split,chosen,threshold):
    """Diagnostic stand-alone heads with train-only scaler and ref; no labels."""
    path=Path(path)
    if path.exists():raise FileExistsError('GAMER_DUAL_SIGN_SAVED_ARTIFACT_EXISTS')
    mu,std,ref,median=_raw_map_spec(raw,split)
    payload={'schema':np.asarray(SCHEMA),'mean':mu,'std':std,
             'reference':ref,'median_distance':np.asarray(median),
             'zero_threshold':np.asarray(threshold)}
    for task in ('zero','polarity'):
        kind,reg,model=chosen[task]
        payload.update({f'{task}_kind':np.asarray(kind),
                        f'{task}_weight':np.asarray(model['weights'],dtype=np.float32),
                        f'{task}_bias':np.asarray(model['bias'],dtype=np.float64),
                        f'{task}_regularization':np.asarray(reg),
                        f'{task}_epoch':np.asarray(model['development']['epoch'])})
    tmp=path.with_suffix('.temp.npz')
    try:
        np.savez_compressed(tmp,**payload)
        os.replace(tmp,path)
    finally:
        if tmp.exists():tmp.unlink()
    return {'path':str(path),'sha256':three._sha(path),
            'type':'DIAGNOSTIC_TWO_BINARY_SIGN_HEADS_NO_MAGNITUDE'}


def _reload(path,raw):
    with np.load(path,allow_pickle=False) as f:
        if str(f['schema'])!=SCHEMA:raise ValueError('GAMER_DUAL_SIGN_BAD_SAVED_SCHEMA')
        spec=(f['mean'],f['std'],f['reference'],float(f['median_distance']))
        probs={}
        for task in ('zero','polarity'):
            kind=str(f[f'{task}_kind'])
            x=_map_from_spec(raw,kind,spec)
            w=f[f'{task}_weight'];b=float(f[f'{task}_bias'])
            if x.shape[1]!=len(w):raise ValueError('GAMER_DUAL_SIGN_BAD_SAVED_WEIGHTS')
            probs[task]=_sigmoid(x.astype(np.float64)@w.astype(np.float64)+b)
        return _combine(probs['zero'],probs['polarity'],float(f['zero_threshold']))


def _fit_stage(rows,raw,*,epochs=120,nulls=2,seed=47,triple_weight=.25,
               output=None,train_subset=None):
    split=three._partitions(rows)
    if train_subset is not None:
        subset=np.asarray(train_subset,dtype=int)
        if (len(subset)!=144 or len(np.unique(subset))!=144 or
            not set(map(int,subset)).issubset(set(map(int,split['train'])))):
            raise ValueError('GAMER_DUAL_SIGN_CONTROL_MUST_HAVE_ORIGINAL_144_TRAIN_CASES')
        split={**split,'train':subset}
    y=_labels(rows)
    train=split['train'];dev=split['dev'];test=split['test']
    pairs=_triples(rows,train)
    maps,descriptor=three._train_only_maps(raw,split)
    candidate={};selected={}
    for task in ('zero','polarity'):
        best=None;all_options=[]
        for kind in KINDS:
            for reg in REGS:
                model=_binary(maps[kind][train],y[train],maps[kind][dev],y[dev],pairs,
                    task=task,regularization=reg,epochs=epochs,triple_weight=triple_weight)
                dev_prob=_score(model,maps[kind][dev]);metrics=_binary_diag(y[dev],dev_prob,task=task)
                rank=(metrics['balanced_accuracy'],min(metrics['recall_negative_class'],
                       metrics['recall_positive_class']),-model['development']['dev_nll'],
                       int(kind=='linear'),-reg)
                all_options.append({'kind':kind,'regularization':reg,'dev':metrics,
                                    **model['development']})
                if best is None or rank>best[0]:best=(rank,kind,reg,model,metrics)
        if best is None:raise ValueError('GAMER_DUAL_SIGN_NO_MODEL_CANDIDATE')
        _,kind,reg,model,metrics=best
        selected[task]=(kind,reg,model)
        candidate[task]={'kind':kind,'regularization':reg,'epoch':model['development']['epoch'],
                         'dev_binary':metrics,'candidate_development_scores':all_options}
    dev_zero=_score(selected['zero'][2],maps[selected['zero'][0]][dev])
    dev_pol=_score(selected['polarity'][2],maps[selected['polarity'][0]][dev])
    best=None
    for threshold in ZERO_THRESHOLDS:
        cls,probs=_combine(dev_zero,dev_pol,threshold)
        met=three._metrics(rows,dev,cls,probabilities=probs)
        rank=(met['min_class_recall'],met['triple_accuracy'],met['balanced_sign_accuracy'],
              -abs(threshold-.5))
        if best is None or rank>best[0]:best=(rank,threshold,met)
    _,threshold,dev_metrics=best
    train_zero=_score(selected['zero'][2],maps[selected['zero'][0]][train])
    train_pol=_score(selected['polarity'][2],maps[selected['polarity'][0]][train])
    train_cls,train_probs=_combine(train_zero,train_pol,threshold)
    test_zero=_score(selected['zero'][2],maps[selected['zero'][0]][test])
    test_pol=_score(selected['polarity'][2],maps[selected['polarity'][0]][test])
    test_cls,test_probs=_combine(test_zero,test_pol,threshold)
    test_metrics=three._metrics_by_family(rows,test,test_cls,test_probs)
    independent=_conditional_test_metrics(rows,test,test_zero,test_pol,threshold)
    artifact=None
    if output is not None:
        artifact=_store(output,raw,split,selected,threshold)
        replay_cls,replay_probs=_reload(output,np.asarray(raw)[test])
        if not np.array_equal(replay_cls,test_cls) or not np.allclose(replay_probs,test_probs,atol=1e-5):
            raise ValueError('GAMER_DUAL_SIGN_SAVED_HEAD_REPLAY_FAILED')
    null_scores=[]
    rng=np.random.default_rng(seed)
    for j in range(nulls):
        labels=y[train].copy()
        # Within EACH train triple permute sign labels. Original sign class
        # counts, paired group counts, and zero/nonzero ratio are maintained.
        for tri in pairs:labels[tri]=rng.permutation([-1,0,1])
        shuffled_pairs=_triples([{'group':rows[int(i)]['group'],
                                  'surface':rows[int(i)]['surface'],
                                  'target':str(int(labels[k]))} for k,i in enumerate(train)],
                                np.arange(len(train)))
        shuffled_models={}
        for task in ('zero','polarity'):
            kind,reg,model=selected[task]
            shuffled_models[task]=_binary(maps[kind][train],labels,maps[kind][dev],y[dev],
                shuffled_pairs,task=task,regularization=reg,
                epochs=model['development']['epoch'],triple_weight=triple_weight)
        pz=_score(shuffled_models['zero'],maps[selected['zero'][0]][test])
        pp=_score(shuffled_models['polarity'],maps[selected['polarity'][0]][test])
        pred,pr=_combine(pz,pp,threshold)
        null_scores.append(three._metrics(rows,test,pred,probabilities=pr)['balanced_sign_accuracy'])
    return {'feature_policy':descriptor,'training_cases':len(train),
            'train_groups':len(pairs),'dev_cases':len(dev),'test_cases':len(test),
            'train_only_paired_ordering_weight':triple_weight,
            'zero_threshold_selected_on_dev':threshold,'model_selection':'TRAIN_FIT_DEV_ONLY_TEST_NOT_SELECTED',
            'heads':candidate,'train':three._metrics(rows,train,train_cls,probabilities=train_probs),
            'dev':dev_metrics,'test':test_metrics,
            'independent_binary_heldout':independent,
            'null_shuffled_train_triples_test_accuracy':null_scores,
            'beats_all_shuffled_train_nulls':bool(null_scores and
                test_metrics['balanced_sign_accuracy']>max(null_scores)),
            'diagnostic_binary_head_artifact':artifact,'magnitude_fit':False,
            'production_c0_unchanged':True}


def _summary(report):
    steps=sorted(map(int,report['steps']))
    last=report['steps'][str(steps[-1])]
    stages={}
    for stage in STAGES:
        expanded=last['stages'][stage]['test']
        baseline=last['previous_three_class_144'][stage]
        control=last['control_144'].get(stage)
        control_test=control['test'] if control is not None else None
        stages[stage]={
            'dual_576_sign':expanded['balanced_sign_accuracy'],
            'dual_576_negative_recall':expanded['recall']['-1'],
            'dual_576_zero_recall':expanded['recall']['0'],
            'dual_576_positive_recall':expanded['recall']['1'],
            'dual_576_triples':expanded['complete_triples'],
            'dual_576_unseen_hawk_sign':expanded['by_family']['hawk_indifference']['balanced_sign_accuracy'],
            'dual_576_all_gates':expanded['passes_sign_zero_and_triples_gate'],
            'three_class_144_sign':baseline['balanced_sign_accuracy'],
            'three_class_144_triples':baseline['complete_triples'],
            'dual_144_sign':control_test['balanced_sign_accuracy'] if control_test is not None else None,
            'dual_144_triples':control_test['complete_triples'] if control_test is not None else None,
            'dual_objective_gain_at_144_cases':(
                control_test['balanced_sign_accuracy']-baseline['balanced_sign_accuracy'])
                if control_test is not None else None,
            'dual_sample_size_gain_576_minus_144':(
                expanded['balanced_sign_accuracy']-control_test['balanced_sign_accuracy'])
                if control_test is not None else None}
    return {'first':steps[0],'last':steps[-1], 'stages':stages,
            'interpretation':('These controlled readout differences distinguish '
                'objective and sample-size contributions under tested features; '
                'interaction effects remain untested. A failed dual head is not '
                'an information-theoretic proof; no production promotion.')}


def run(args):
    import torch
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    from tools import nanojev_gamer_v1_c0_sign_causality as causal
    from tools import nanojev_gamer_v1_c0_boundary as c0
    from tools import nanojev_gamer_v1_lora_sign_stress as stress
    from tools import nanojev_gamer_v1_handoff as gamer
    src=_source(args.sign_head,args.steps)
    origin=src['source'];meta=origin['meta'];old=origin['rows']
    protected=[src['root'],origin['training'],origin['source'],origin['capture'],
               origin['ckpt'].parent,origin['adapter'],origin['audit_path'].parent,
               origin['causal_path'].parent]
    out=three._output(args.output,protected)
    baseline=_fingerprints(src)
    rows=src['rows'];deck=src['deck'];idx=src['old_indices']
    # Baseline immutable model must replay before Qwen re-capture.
    net=c0._load_head(meta,args.device)
    x0=np.asarray(origin['archive']['lora__final_flat'],dtype=np.float32)
    _,_,_,pred0=causal._read_stages(net,x0,device=args.device,batch=args.head_batch)
    c0._verify_baseline(pred0,origin['archive']['production_head_predictions'])
    del net,x0,pred0
    gc.collect()
    if torch.cuda.is_available():torch.cuda.empty_cache()
    control_train=src['old_indices'][np.asarray([i for i,r in enumerate(old)
                                                 if r['split']=='train'],dtype=int)]
    if len(control_train)!=144:
        raise ValueError('GAMER_DUAL_SIGN_INVALID_ORIGINAL_144_CONTROL')
    result={'schema':SCHEMA,'status':'INCOMPLETE',
        'source_three_class_report':str(src['prior_path']),
        'original_oracle_dataset_sha256':meta['dataset_sha256'],
        'expanded_training_deck_sha256':src['expanded_deck_sha256'],
        'fingerprints':baseline,
        'steps':{},'snapshots':{str(k):v[1] for k,v in origin['snapshots'].items()},
        'architecture':'zero/nonzero binary + conditional negative/positive binary; zero precedence',
        'training_restricted_to_expanded_576_train_cases':True,
        'original_144_train_control_enabled':args.control_144,
        'dev_and_test_unchanged':True,'unseen_hawk_entire_family_test_only':True,
        'no_original_lora_or_production_c0_training':True,
        'no_magnitude_regression':True,'promotion':'BLOCKED_DIAGNOSTIC_ONLY',
        'settings':{'epochs':args.epochs,'nulls':args.nulls,'seed':args.seed,
                    'triplet_weight':args.triplet_weight,
                    'kinds':list(KINDS),'regularizations':list(REGS)}}
    out.mkdir(parents=True,exist_ok=False)
    for step,(snapshot,sha) in origin['snapshots'].items():
        if stress._verify_snapshot(snapshot,step)!=sha:
            raise ValueError('GAMER_DUAL_SIGN_SNAPSHOT_CHANGED')
        print(f'GAMER DUAL SIGN: snapshot {step}; 576 train / 72 dev / 108 test',flush=True)
        _,tok,model=gamer.model_components(meta['lineage']['model_id'],args.device,adapter_dir=snapshot)
        features,_=oracle._capture_profiles(torch,model,tok,deck,
           device=args.device,max_length=meta['lineage']['configuration']['max_length'],
           batch_size=args.capture_batch,curvature=meta['lineage']['configuration']['curvature'],
           verify_exact_final=True)
        del model,tok
        gc.collect()
        if torch.cuda.is_available():torch.cuda.empty_cache()
        x=np.asarray(features['final_flat'],dtype=np.float16).astype(np.float32)
        original=np.asarray(origin['archive']['lora__final_flat'],dtype=np.float16).astype(np.float32)
        if step==0 and (original.shape!=x[idx].shape or
                        float(np.max(np.abs(original-x[idx])))>.01):
            raise ValueError('GAMER_DUAL_SIGN_ZERO_STEP_CAPTURE_REPLAY_FAILED')
        net=c0._load_head(meta,args.device)
        stages,_,_,pred=causal._read_stages(net,x,device=args.device,batch=args.head_batch)
        prior_metrics=origin['audit']['steps'][str(step)]['metrics']['production_c0']['test']
        actual=stress._head_metrics(old,np.asarray(pred)[idx])['test']
        if (abs(actual['balanced_sign_accuracy']-prior_metrics['balanced_sign_accuracy'])>1e-10 or
            actual['triple_all_correct']!=prior_metrics['triple_all_correct'] or
            abs(actual['signed_mae']-prior_metrics['signed_mae'])>.001):
            raise ValueError('GAMER_DUAL_SIGN_FROZEN_C0_REPLAY_FAILED')
        input_stages={'lora_final_mean100':np.asarray(features['final_mean100'],dtype=np.float32),
                      'enc_gelu_3':stages['enc_gelu_3'],
                      'mid_gelu':stages['mid_gelu']}
        del net,features,x,pred,stages
        gc.collect()
        if torch.cuda.is_available():torch.cuda.empty_cache()
        stages_result={}
        controls={}
        for stage in STAGES:
            print('  dual zero/polarity fit: '+stage,flush=True)
            stages_result[stage]=_fit_stage(rows,input_stages[stage],epochs=args.epochs,
                nulls=args.nulls,seed=args.seed,triple_weight=args.triplet_weight,
                output=out/f'step-{step:07d}-{stage}-dual-sign.npz')
            if args.control_144:
                controls[stage]=_fit_stage(rows,input_stages[stage],epochs=args.epochs,
                    nulls=args.nulls,seed=args.seed,triple_weight=args.triplet_weight,
                    train_subset=control_train,
                    output=out/f'step-{step:07d}-{stage}-dual-sign-144-control.npz')
                print(f'    144-case control sign={controls[stage]["test"]["balanced_sign_accuracy"]:.4f} '
                      f'triples={controls[stage]["test"]["complete_triples"]}/36',flush=True)
            t=stages_result[stage]['test']
            print(f'    sign={t["balanced_sign_accuracy"]:.4f} -={t["recall"]["-1"]:.3f} '
                  f'0={t["recall"]["0"]:.3f} +={t["recall"]["1"]:.3f} '
                  f'triples={t["complete_triples"]}/{t["triples"]} '
                  f'hawk={t["by_family"]["hawk_indifference"]["balanced_sign_accuracy"]:.3f}',flush=True)
        result['steps'][str(step)]={'snapshot_sha256':sha,
            'stages':stages_result,'control_144':controls,
            'previous_three_class_144':{stage:src['prior']['steps'][str(step)]['stages'][stage]['test']
                                        for stage in STAGES},
            'frozen_production_c0':{'test':actual}}
        gamer.atomic_json(out/f'step-{step:07d}.json',result['steps'][str(step)])
        gamer.atomic_json(out/'progress.json',{'status':'IN_PROGRESS','steps':sorted(result['steps'])})
        del input_stages,stages_result,controls
        gc.collect()
    if _fingerprints(src)!=baseline:
        raise ValueError('GAMER_DUAL_SIGN_PROTECTED_WEIGHTS_CHANGED_DURING_RUN')
    result['trajectory_summary']=_summary(result)
    result['status']='COMPLETE_TWO_BINARY_HEADS_NO_PRODUCTION_UPDATES'
    result['interpretation']='Two independent binary readouts trained on 576 train cases, DEV-selected and TEST-scored once. Zero-class precedence is diagnostic only; magnitude correctness has not been tested.'
    gamer.atomic_json(out/'final_report.json',result)
    print('GAMER DUAL SIGN COMPLETE: '+str(out/'final_report.json'),flush=True)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sign-head',required=True,type=Path,
                   help='Completed three-class diagnostic directory')
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--steps',default='0,500,1500,3000')
    p.add_argument('--device',default='cuda')
    p.add_argument('--capture-batch',type=int,default=2)
    p.add_argument('--head-batch',type=int,default=8)
    p.add_argument('--epochs',type=int,default=120)
    p.add_argument('--nulls',type=int,default=2)
    p.add_argument('--triplet-weight',type=float,default=.25)
    p.add_argument('--no-144-control',dest='control_144',action='store_false',
                   help='Skip original 144-case dual-head control (enabled by default)')
    p.set_defaults(control_144=True)
    p.add_argument('--seed',type=int,default=47)
    args=p.parse_args(argv)
    if (args.capture_batch<1 or args.head_batch<1 or args.epochs<10 or
        args.epochs>1000 or args.nulls<1 or args.nulls>10 or
        not 0<=args.triplet_weight<=10):
        raise ValueError('GAMER_DUAL_SIGN_INVALID_FLAGS')
    return run(args)

if __name__=='__main__': main()
