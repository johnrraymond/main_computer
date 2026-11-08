#!/usr/bin/env python3
"""GAMER V1: frozen causal counterfactual representation-transfer audit.

No LoRA/C0/zero-head training or promotion. Captures exactly the frozen LoRA
step-3000 representation and the frozen C0 encoder/bottleneck on exact-
Fraction counterfactual triples. A fixed payoff/prompt surface changes only
its belief q, with -/0/+ signs at three distance scales. Compare upstream
and post-encoder recoverability using train-only linear and RBF diagnostic
readouts for zero, conditional polarity, and nonzero magnitude.

Model selection is DEV-only. Previously inspected hawk/V2 fresh cases are
regression evidence; newly generated heldout payoff profiles are excluded
from all fitting and selection. Geometry is descriptive and can reflect
lexical differences, not proof that the backbone computes the arithmetic.

python -m tools.nanojev_gamer_v1_representation_transfer_audit \\
  --v2 runtime/reports/gamer-v1/c0-polarity-magnitude-v2-0001 \\
  --output runtime/reports/gamer-v1/c0-representation-transfer-0001 \\
  --device cuda
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from fractions import Fraction
import gc
import json
import os
from pathlib import Path

import numpy as np

from tools import nanojev_gamer_v1_polarity_magnitude_v2 as v2
from tools import nanojev_gamer_v1_c0_sign_head as three
from tools import nanojev_gamer_v1_dual_sign_head as dual
from tools import nanojev_gamer_v1_joint_sign_magnitude_lab as joint

SCHEMA = 'nanojev.gamer.v1.frozen-counterfactual-representation-transfer.v1'
STEP = 3000
NEW_TRAIN_PROFILES = 12
NEW_DEV_PROFILES = 4
NEW_HOLDOUT_PROFILES = 4
RADII = (Fraction(1, 200), Fraction(1, 90), Fraction(1, 40))
STAGES = ('lora_final_mean100', 'enc_gelu_3', 'mid_gelu')
KINDS = ('linear', 'rbf_1.0')
TASKS = ('zero', 'polarity', 'magnitude')
THRESHOLDS = (.35, .50, .65)
TOLERANCE = .05


def _sha(path):
    return three._sha(Path(path))


def _profile(i):
    """Never reuse a V2 payoff tuple; exact mathematical crossing."""
    from tools import nanojev_two_game_spine_smoke as spine
    if i not in range(NEW_TRAIN_PROFILES + NEW_DEV_PROFILES + NEW_HOLDOUT_PROFILES):
        raise ValueError('GAMER_TRANSFER_PROFILE_INDEX_OUT_OF_RANGE')
    s = 1 if i % 2 == 0 else -1
    a = s * (13 + (i % 6) * 4 + i // 6)
    b = -s * (17 + (i % 5) * 3 + i // 5)
    r, reward_s = Fraction(10000 + 37*i), Fraction(6000 + 19*i)
    stage = spine.Stage(r, reward_s, r + a, reward_s + b)
    q0 = spine.critical_q(stage)
    if q0 is None or not Fraction(0)<q0<Fraction(1) or stage.margin(q0) != 0:
        raise ValueError('GAMER_TRANSFER_INVALID_ZERO_LINE')
    return stage, q0


def _new_deck(existing):
    """One-variable-at-a-time q changes, disjoint payoff roles and exact signs."""
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    from tools import nanojev_gamer_v1_zero_sign_frob as prompts
    seen_payoffs = set()
    # Recompute the V2 payoff tuples, not just its prompt hashes.
    for i in range(v2.TRAIN_COUNT + v2.DEV_COUNT + v2.FRESH_COUNT):
        st, _ = v2._profile(i)
        seen_payoffs.add(tuple(map(str, (st.R, st.S, st.T, st.P))))
    old_ids = {c.id for c in existing}
    old_hashes = {oracle.sha_text(c.prompt) for c in existing}
    roles = {'counterfactual_train': [], 'counterfactual_dev': [], 'counterfactual_holdout': []}
    metadata = {}
    for i in range(NEW_TRAIN_PROFILES+NEW_DEV_PROFILES+NEW_HOLDOUT_PROFILES):
        role = ('counterfactual_train' if i < NEW_TRAIN_PROFILES else
                'counterfactual_dev' if i < NEW_TRAIN_PROFILES+NEW_DEV_PROFILES else
                'counterfactual_holdout')
        stage, q0 = _profile(i)
        payoff = tuple(map(str,(stage.R,stage.S,stage.T,stage.P)))
        if payoff in seen_payoffs:
            raise ValueError('GAMER_TRANSFER_PAYOFF_REUSE')
        seen_payoffs.add(payoff)
        key=f'{role}.payoff.{i:04d}'
        metadata[key]={'role': role, 'payoff': payoff, 'exact_zero_q':str(q0),
                       'profile_index':i, 'radii':[str(r) for r in RADII]}
        slope_sign = 1 if stage.gaps()[0]>stage.gaps()[1] else -1
        for radius_index, radius in enumerate(RADII):
            group=f'{key}.radius.{radius_index:02d}'
            for tag, expected_sign in (('negative',-1), ('zero',0), ('positive',1)):
                delta=radius*expected_sign*slope_sign
                q=q0+delta
                if not 0<=q<=1:
                    raise ValueError('GAMER_TRANSFER_BELIEF_OUT_OF_RANGE')
                result=stage.margin(q)
                if (result>0)-(result<0)!=expected_sign:
                    raise ValueError('GAMER_TRANSFER_BAD_ORACLE_SIGN')
                if expected_sign and abs(result)!=abs(stage.gaps()[0]-stage.gaps()[1])*radius:
                    raise ValueError('GAMER_TRANSFER_BAD_ORACLE_MAGNITUDE')
                for surface, render in (('spine_belief',prompts._spine_belief_prompt),
                                        ('game_prose_belief',prompts._game_prose_belief_prompt)):
                    prompt=render(stage,q)
                    case_id=f'{group}.{tag}.{surface}'
                    if case_id in old_ids or oracle.sha_text(prompt) in old_hashes:
                        raise ValueError('GAMER_TRANSFER_PRIOR_PROMPT_OVERLAP')
                    case=oracle.Case(case_id,
                         'train' if role=='counterfactual_train' else
                         'dev' if role=='counterfactual_dev' else 'test',
                         group,surface,prompt,result,role,delta)
                    roles[role].append(case)
                    old_ids.add(case.id)
                    # Do not add new hashes: same exact zero prompt repeats at
                    # different radii, but ONLY inside the same payoff/role.
    count = len(RADII)*3*2
    if {k:len(v) for k,v in roles.items()} != {
        'counterfactual_train':NEW_TRAIN_PROFILES*count,
        'counterfactual_dev':NEW_DEV_PROFILES*count,
        'counterfactual_holdout':NEW_HOLDOUT_PROFILES*count}:
        raise ValueError('GAMER_TRANSFER_ROLE_COUNTS_CHANGED')
    extra=sum((roles[k] for k in roles),[])
    oracle.validate_deck(list(existing)+extra)
    return list(existing)+extra,roles,metadata


def _source(v2_dir):
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    root=Path(v2_dir).resolve()
    report_path=root/'final_report.json'
    report=json.loads(report_path.read_text(encoding='utf-8'))
    if (report.get('schema')!=v2.SCHEMA or
        report.get('status')!='COMPLETE_FROZEN_DIAGNOSTICS_NO_PROMOTION' or
        report.get('all_arms_promotion_blocked') is not True or
        report.get('zero_head_unchanged') is not True or
        report.get('no_production_model_training') is not True or
        report.get('frozen_lora_step')!=STEP or
        report.get('original_72_dev_108_test_unchanged') is not True):
        raise ValueError('GAMER_TRANSFER_REQUIRES_COMPLETED_FROZEN_V2')
    original=v2._source(Path(report['source_repair_report']).parent)
    if (_sha(original['report_path'])!=report.get('source_repair_report_sha256') or
        oracle.deck_hash(original['deck'])!=report.get('combined_deck_sha256') or
        _sha(original['prior']['zero_head'])!=report.get('zero_head_sha256') or
        original['prior']['lineage']['origin']['source']['snapshots'][STEP][1]!=report.get('frozen_lora_sha256')):
        raise ValueError('GAMER_TRANSFER_FROZEN_LINEAGE_CHANGED')
    deck,extra,profiles=_new_deck(original['deck'])
    rows=[c.public() for c in deck]
    assert rows[:len(original['rows'])] == original['rows']
    index={r['id']:i for i,r in enumerate(rows)}
    ix={role:np.asarray([index[c.id] for c in cases],dtype=int)
        for role,cases in extra.items()}
    original_ix=original['indices']
    ix.update({
        'base_train':original_ix['train936'],
        'base_dev':original_ix['dev120'],
        'original_test':original_ix['old_test'],
        'v2_fresh':original_ix['fresh'],
    })
    ix.update({'train_extended':np.concatenate((ix['base_train'],ix['counterfactual_train'])),
               'dev_extended':np.concatenate((ix['base_dev'],ix['counterfactual_dev']))})
    if (len(ix['base_train'])!=936 or len(ix['base_dev'])!=120 or
        len(ix['original_test'])!=108 or len(ix['v2_fresh'])!=48 or
        len(ix['counterfactual_holdout'])!=NEW_HOLDOUT_PROFILES*len(RADII)*6):
        raise ValueError('GAMER_TRANSFER_BASE_SPLIT_CHANGED')
    for train, dev in ((ix['base_train'],ix['base_dev']),
                       (ix['train_extended'],ix['dev_extended'])):
        evaluation=np.concatenate((ix['original_test'],ix['v2_fresh'],ix['counterfactual_holdout']))
        if (len(set(train)&set(dev)) or len(set(train)&set(evaluation)) or
            len(set(dev)&set(evaluation))):
            raise ValueError('GAMER_TRANSFER_TRAIN_DEV_EVAL_LEAK')
        trp={rows[int(i)]['group'].split('.radius.')[0] for i in train if 'counterfactual_' in rows[int(i)]['family']}
        dep={rows[int(i)]['group'].split('.radius.')[0] for i in dev if 'counterfactual_' in rows[int(i)]['family']}
        tep={rows[int(i)]['group'].split('.radius.')[0] for i in evaluation if 'counterfactual_' in rows[int(i)]['family']}
        if trp&dep or trp&tep or dep&tep:
            raise ValueError('GAMER_TRANSFER_PAYOFF_SPLIT_LEAK')
    return {'v2_report':report,'v2_report_path':report_path,'original':original,
            'deck':deck,'rows':rows,'indices':ix,'profiles':profiles,'role_cases':extra}


def _train_only_map(raw,train,kind):
    """Linear standardized or small train-landmark RBF features; no heldout fit."""
    x=np.asarray(raw,dtype=np.float32)
    train=np.asarray(train,dtype=int)
    if x.ndim!=2 or x.shape[1]<2 or not np.isfinite(x).all() or len(train)<6:
        raise ValueError('GAMER_TRANSFER_BAD_FEATURES')
    mu=x[train].astype(np.float64).mean(axis=0)
    std=np.sqrt(np.maximum(np.mean((x[train].astype(np.float64)-mu)**2,axis=0),1e-6))
    z=((x.astype(np.float64)-mu)/std).astype(np.float32)
    ref=np.empty((0,z.shape[1]),dtype=np.float32); width=1.
    if kind=='linear':return z,(mu,std,ref,width)
    if kind!='rbf_1.0':raise ValueError('GAMER_TRANSFER_UNKNOWN_MAP')
    # Landmarks are selected from TRAIN only and span the group ordering.
    n=min(96,len(train))
    ref=z[train[np.linspace(0,len(train)-1,n,dtype=int)]].copy()
    aa=np.sum(ref.astype(np.float64)**2,axis=1)
    d=np.maximum(aa[:,None]+aa[None,:]-2*ref.astype(np.float64)@ref.astype(np.float64).T,0)/x.shape[1]
    valid=d[np.triu_indices(n,1)]
    valid=valid[valid>1e-12]
    width=float(np.median(valid)) if len(valid) else 1.
    return _apply_map(x,kind,(mu,std,ref,width)),(mu,std,ref,width)


def _apply_map(raw,kind,spec):
    mu,std,ref,width=spec
    x=np.asarray(raw,dtype=np.float64)
    z=((x-mu)/std).astype(np.float32)
    if kind=='linear':return z
    if kind!='rbf_1.0':raise ValueError('GAMER_TRANSFER_UNKNOWN_MAP')
    if width<=0:raise ValueError('GAMER_TRANSFER_INVALID_RBF_WIDTH')
    a=z.astype(np.float64); b=ref.astype(np.float64)
    dist=np.maximum(np.sum(a*a,axis=1,keepdims=True)+np.sum(b*b,axis=1)[None,:]-2*a@b.T,0)/x.shape[1]
    return np.asarray(np.exp(-dist/width),dtype=np.float32)


def _targets(rows):
    exact=[Fraction(r['target']) for r in rows]
    sign=np.asarray([(v>0)-(v<0) for v in exact],dtype=np.int8)
    mag=np.asarray([float(abs(v)) for v in exact],dtype=np.float64)
    if not np.isfinite(mag).all():raise ValueError('GAMER_TRANSFER_NONFINITE_ORACLE')
    return sign,mag


def _fit_probe(x,sign,mag,train,dev,task,*,epochs=80,reg=.001):
    """Small diagnostic readout only; TRAIN optimizes, DEV picks epoch."""
    import torch
    import torch.nn.functional as F
    x=np.asarray(x,dtype=np.float32)
    tr=np.asarray(train,dtype=int);de=np.asarray(dev,dtype=int)
    y=np.asarray(sign,dtype=np.int8);m=np.asarray(mag,dtype=np.float64)
    if task not in TASKS or len(x)!=len(y) or len(y)!=len(m) or epochs<10 or reg<0:
        raise ValueError('GAMER_TRANSFER_PROBE_TRAIN_CONTRACT')
    if not np.isfinite(x).all() or not np.isfinite(m).all():
        raise ValueError('GAMER_TRANSFER_NONFINITE_PROBE')
    active=(y!=0) if task!='zero' else np.ones(len(y),dtype=bool)
    ti=tr[active[tr]]; di=de[active[de]]
    if not len(ti) or not len(di):raise ValueError('GAMER_TRANSFER_EMPTY_PROBE_TASK')
    if task!='magnitude':
        if task=='zero':tt=(y[ti]==0).astype(np.float32); dd=(y[di]==0).astype(np.float32)
        else:tt=(y[ti]>0).astype(np.float32);dd=(y[di]>0).astype(np.float32)
        if set(tt)!={0.,1.} or set(dd)!={0.,1.}:
            raise ValueError('GAMER_TRANSFER_PROBE_CLASS_MISSING')
    else:
        if np.any(m[ti]<=0) or np.any(m[di]<=0):raise ValueError('GAMER_TRANSFER_MAGNITUDE_HAS_ZERO')
        tt=np.log(m[ti]).astype(np.float32);dd=np.log(m[di]).astype(np.float32)
    torch.manual_seed(47)
    xt=torch.from_numpy(x[ti]); yt=torch.from_numpy(tt)
    xd=torch.from_numpy(x[di]);
    w=torch.nn.Parameter(torch.zeros(x.shape[1],dtype=torch.float32))
    b=torch.nn.Parameter(torch.tensor(float(np.median(tt)) if task=='magnitude' else 0.,dtype=torch.float32))
    opt=torch.optim.Adam((w,b),lr=.025)
    best=None
    for epoch in range(1,epochs+1):
        opt.zero_grad();pred=xt@w+b
        if task=='magnitude':loss=F.smooth_l1_loss(pred,yt,beta=.35)
        else:loss=F.binary_cross_entropy_with_logits(pred,yt)
        loss=loss + reg/max(1,len(w))*w.square().sum()
        if not torch.isfinite(loss):raise ValueError('GAMER_TRANSFER_NONFINITE_PROBE_LOSS')
        loss.backward();opt.step()
        if epoch%10!=0 and epoch!=epochs:continue
        with torch.no_grad():dlog=(xd@w+b).numpy().astype(np.float64)
        if task=='magnitude':
            p=np.exp(np.clip(dlog,-18,5)); truth=m[di]
            err=np.abs(p-truth)
            rank=(-float(np.mean(err)),-float(np.quantile(err,.9)),-epoch)
        else:
            p=dual._sigmoid(dlog)
            truth=dd.astype(bool); pred=p>=.5
            a=float(np.mean(~pred[~truth]));c=float(np.mean(pred[truth]))
            rank=(min(a,c),(a+c)/2,-epoch)
        if best is None or rank>best[0]:
            best=(rank,w.detach().numpy().copy(),float(b.detach()),epoch)
    if best is None:raise ValueError('GAMER_TRANSFER_NO_PROBE_EPOCH')
    return {'weights':best[1],'bias':best[2],'epoch':best[3]}


def _score_probe(model,mapped,task):
    logit=np.asarray(mapped,dtype=np.float64)@np.asarray(model['weights'],dtype=np.float64)+float(model['bias'])
    return np.exp(np.clip(logit,-18,5)) if task=='magnitude' else dual._sigmoid(logit)


def _binary_metrics(sign,prob,task,threshold=.5):
    y=np.asarray(sign,dtype=np.int8);p=np.asarray(prob,dtype=np.float64)
    active=y!=0 if task=='polarity' else np.ones(len(y),dtype=bool)
    a=y[active];pr=p[active]>=threshold
    truth=a==0 if task=='zero' else a>0
    if not np.any(truth) or not np.any(~truth):
        raise ValueError('GAMER_TRANSFER_BINARY_EVAL_CLASS_MISSING')
    neg=float(np.mean(~pr[~truth]));pos=float(np.mean(pr[truth]))
    return {'cases':int(active.sum()),'threshold':float(threshold),
            'balanced_accuracy':(neg+pos)/2,
            'negative_or_nonzero_recall':neg,'positive_or_zero_recall':pos,
            'min_recall':min(neg,pos)}


def _magnitude_metrics(mag,pred):
    a=np.asarray(mag,dtype=np.float64);p=np.asarray(pred,dtype=np.float64)
    nz=a>0
    if not nz.any() or not np.isfinite(p).all() or np.any(p<0):
        raise ValueError('GAMER_TRANSFER_BAD_MAGNITUDE_EVALUATION')
    err=np.abs(p[nz]-a[nz]);t=a[nz]
    tight=np.minimum(.05,np.maximum(.001,.1*t))
    # Stable finite covariance rank diagnostic: slope on absolute magnitudes.
    corr=float(np.corrcoef(t,p[nz])[0,1]) if np.std(p[nz])>1e-12 and np.std(t)>1e-12 else 0.
    return {'nonzero_cases':int(nz.sum()), 'nonzero_mae':float(np.mean(err)),
            'nonzero_p90_abs_error':float(np.quantile(err,.9)),
            'within_0_05':float(np.mean(err<=.05)),
            'within_tight_tolerance':float(np.mean(err<=tight)),
            'under_tenth_true_magnitude_count':int(np.count_nonzero(p[nz]<.1*t)),
            'magnitude_pearson':corr,
            'mean_predicted_nonzero_magnitude':float(np.mean(p[nz]))}


def _by_family(rows,idx,sign,mag,prob,task,threshold):
    ix=np.asarray(idx,dtype=int)
    def calculate(p):
        if task=='magnitude':return _magnitude_metrics(mag[p],prob[p])
        return _binary_metrics(sign[p],prob[p],task,threshold)
    families={name:calculate(np.asarray([i for i in ix if rows[int(i)]['family']==name],dtype=int))
              for name in sorted({rows[int(i)]['family'] for i in ix})}
    return {'overall':calculate(ix),'by_family':families}


def _geometry(rows,idx,features,train_axis=None):
    """Pairwise contrast; descriptive, not an information-existence proof."""
    x=np.asarray(features,dtype=np.float64)
    grouped=defaultdict(dict)
    for i in idx:
        r=rows[int(i)];sign=int((Fraction(r['target'])>0)-(Fraction(r['target'])<0))
        key=(r['group'],r['surface'])
        if sign in grouped[key]:raise ValueError('GAMER_TRANSFER_DUPLICATE_PAIR_SIGN')
        grouped[key][sign]=int(i)
    if not grouped or any(set(d)!=set((-1,0,1)) for d in grouped.values()):
        raise ValueError('GAMER_TRANSFER_INCOMPLETE_GEOMETRY_TRIPLE')
    contrasts=[];residual=[];spans=[]
    for d in grouped.values():
        a=x[d[-1]];z=x[d[0]];b=x[d[1]]
        delta=b-a
        norm=float(np.linalg.norm(delta))
        contrasts.append(delta)
        spans.append(norm)
        residual.append(float(np.linalg.norm(z-(a+b)/2))/max(norm,1e-10))
    paired=np.stack(contrasts)
    if train_axis is None:
        axis=paired.mean(axis=0)
        axis=axis/max(float(np.linalg.norm(axis)),1e-12)
    else:
        axis=np.asarray(train_axis,dtype=np.float64)
        if not np.isfinite(axis).all() or len(axis)!=x.shape[1]:
            raise ValueError('GAMER_TRANSFER_BAD_GEOMETRY_AXIS')
    projections=paired@axis
    return {'triples':len(grouped),'median_negative_positive_l2':float(np.median(spans)),
            'median_zero_from_midpoint_over_span':float(np.median(residual)),
            'train_axis_positive_pair_order_fraction':float(np.mean(projections>0)),
            'train_axis_median_positive_minus_negative':float(np.median(projections))},axis


def _radius_order(rows,idx,features):
    """Does activation distance from zero rise as exact |payoff margin| rises?"""
    x=np.asarray(features,dtype=np.float64)
    by=defaultdict(lambda:defaultdict(dict))
    for i in idx:
        r=rows[int(i)]
        if 'counterfactual_' not in r['family']:continue
        pre,rad=r['group'].rsplit('.radius.',1)
        sign=int((Fraction(r['target'])>0)-(Fraction(r['target'])<0))
        by[(pre,r['surface'])][int(rad)][sign]=int(i)
    results=[]
    for radius_map in by.values():
        if len(radius_map)!=len(RADII) or any(set(part)!=set((-1,0,1)) for part in radius_map.values()):
            raise ValueError('GAMER_TRANSFER_INCOMPLETE_RADIUS_SET')
        for sign in (-1,1):
            seq=[]
            for j in range(len(RADII)):
                group=radius_map[j]
                seq.append((abs(float(Fraction(rows[group[sign]]['target']))),
                            float(np.linalg.norm(x[group[sign]]-x[group[0]]))))
            seq.sort()
            results.extend((seq[k+1][1]>seq[k][1]) for k in range(len(seq)-1))
    return {'adjacent_tests':len(results),
            'increasing_activation_distance_fraction':float(np.mean(results)) if results else None,
            'interpretation':'Within fixed payoff and surface, only belief q changes. Activation distances can still reflect lexical changes.'}


def _capture(source,device,capture_batch,head_batch):
    import torch
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    from tools import nanojev_gamer_v1_c0_sign_causality as causal
    from tools import nanojev_gamer_v1_c0_boundary as c0
    from tools import nanojev_gamer_v1_lora_sign_stress as stress
    from tools import nanojev_gamer_v1_handoff as gamer
    origin=source['original']['prior']['lineage']['origin']['source']
    meta=origin['meta'];snapshot,sha=origin['snapshots'][STEP]
    if stress._verify_snapshot(snapshot,STEP)!=sha:
        raise ValueError('GAMER_TRANSFER_LORA_CHANGED')
    net=c0._load_head(meta,device)
    original=np.asarray(origin['archive']['lora__final_flat'],dtype=np.float32)
    _,_,_,old=causal._read_stages(net,original,device=device,batch=head_batch)
    c0._verify_baseline(old,origin['archive']['production_head_predictions'])
    del net,original,old;gc.collect()
    if torch.cuda.is_available():torch.cuda.empty_cache()
    _,tok,model=gamer.model_components(meta['lineage']['model_id'],device,adapter_dir=snapshot)
    features,_=oracle._capture_profiles(torch,model,tok,source['deck'],device=device,
         max_length=meta['lineage']['configuration']['max_length'],batch_size=capture_batch,
         curvature=meta['lineage']['configuration']['curvature'],verify_exact_final=True)
    del model,tok;gc.collect()
    if torch.cuda.is_available():torch.cuda.empty_cache()
    final=np.asarray(features['final_mean100'],dtype=np.float32)
    flat=np.asarray(features['final_flat'],dtype=np.float16).astype(np.float32)
    del features;gc.collect()
    net=c0._load_head(meta,device)
    stages,_,_,prod=causal._read_stages(net,flat,device=device,batch=head_batch)
    del net,flat;gc.collect()
    if torch.cuda.is_available():torch.cuda.empty_cache()
    return {STAGES[0]:final,STAGES[1]:stages['enc_gelu_3'],
            STAGES[2]:stages['mid_gelu']},prod


def _save_probe(path,task,kind,spec,model,threshold,train,src_hash):
    path=Path(path)
    if path.exists():raise FileExistsError('GAMER_TRANSFER_SAVED_PROBE_EXISTS')
    mu,std,ref,width=spec
    payload={'schema':np.asarray(SCHEMA),'task':np.asarray(task),'kind':np.asarray(kind),
        'mean':np.asarray(mu),'std':np.asarray(std),'reference':np.asarray(ref),
        'width':np.asarray(width),'weights':np.asarray(model['weights']),
        'bias':np.asarray(model['bias']),'epoch':np.asarray(model['epoch']),
        'threshold':np.asarray(threshold),'training_case_count':np.asarray(len(train)),
        'source_v2_sha256':np.asarray(src_hash)}
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp.npz')
    try:np.savez_compressed(tmp,**payload);os.replace(tmp,path)
    finally:
        if tmp.exists():tmp.unlink()
    return {'path':str(path),'sha256':_sha(path),'type':'DIAGNOSTIC_PROBE_NO_PRODUCTION_UPDATE'}


def _replay(path,raw,task):
    with np.load(path,allow_pickle=False) as z:
        if str(z['schema'])!=SCHEMA or str(z['task'])!=task:
            raise ValueError('GAMER_TRANSFER_SAVED_PROBE_SCHEMA_MISMATCH')
        kind=str(z['kind']);spec=(z['mean'],z['std'],z['reference'],float(z['width']))
        model={'weights':z['weights'],'bias':float(z['bias'])}
        threshold=float(z['threshold'])
    return _score_probe(model,_apply_map(raw,kind,spec),task),threshold


def _fit_stage(source,raw,sign,mag,train,dev,eval_splits,*,epochs,output,stage,arm,src_hash):
    selected={}
    for task in TASKS:
        candidates=[];best=None
        for kind in KINDS:
            mapped,spec=_train_only_map(raw,train,kind)
            model=_fit_probe(mapped,sign,mag,train,dev,task,epochs=epochs)
            p=_score_probe(model,mapped,task)
            for threshold in ((.5,) if task=='magnitude' else THRESHOLDS):
                dm=_by_family(source['rows'],dev,sign,mag,p,task,threshold)
                if task=='magnitude':
                    criterion=(-max(m['nonzero_mae'] for m in dm['by_family'].values()),
                               -dm['overall']['nonzero_mae'],
                               -dm['overall']['nonzero_p90_abs_error'])
                else:
                    criterion=(min(m['min_recall'] for m in dm['by_family'].values()),
                               dm['overall']['min_recall'],dm['overall']['balanced_accuracy'])
                candidate={'kind':kind,'threshold':threshold,'epoch':model['epoch'],
                           'development':dm,'score':list(criterion)}
                candidates.append(candidate)
                rank=(*criterion,int(kind=='linear'),-abs(threshold-.5))
                if best is None or rank>best[0]:
                    best=(rank,kind,spec,model,threshold,dm)
        _,kind,spec,model,threshold,dev_report=best
        checkpoint=_save_probe(output/f'{arm}-{stage}-{task}.npz',task,kind,spec,model,threshold,train,src_hash)
        predicted,used_threshold=_replay(checkpoint['path'],raw,task)
        reference=_score_probe(model,_apply_map(raw,kind,spec),task)
        if used_threshold!=threshold or not np.allclose(predicted,reference,atol=1e-7,rtol=1e-7):
            raise ValueError('GAMER_TRANSFER_PROBE_REPLAY_FAILURE')
        reports={name:_by_family(source['rows'],idx,sign,mag,predicted,task,threshold)
                 for name,idx in eval_splits.items()}
        selected[task]={'selected':{'kind':kind,'threshold':threshold,'epoch':model['epoch'],
                                 'development':dev_report,'training_cases':len(train)},
                        'candidates':candidates,'checkpoint':checkpoint,
                        'evaluation':reports}
    return selected


def _gate(summary):
    """Readout passes only if every family obeys all three invariants."""
    errors=[]
    for stage,arms in summary.items():
        for arm,info in arms.items():
            for partition in ('original_test','v2_fresh','counterfactual_holdout'):
                z=info['tasks']['zero']['evaluation'][partition]['by_family']
                p=info['tasks']['polarity']['evaluation'][partition]['by_family']
                m=info['tasks']['magnitude']['evaluation'][partition]['by_family']
                for family in z:
                    if z[family]['min_recall']<.95:errors.append(f'{stage}/{arm}/{partition}/{family}/zero')
                    if p[family]['min_recall']<.95:errors.append(f'{stage}/{arm}/{partition}/{family}/polarity')
                    if m[family]['within_tight_tolerance']<.95:errors.append(f'{stage}/{arm}/{partition}/{family}/magnitude')
    return {'passes_all_components':not errors,'failures':errors,
            'required':.95,'promotion':'BLOCKED_DIAGNOSTIC_ONLY'}


def _joint_sign_baseline(original):
    """Resolve JOINT report through the V1 repair descriptor, not as its own steps.

    V2 _source returns {prior: V1 repair _source result}; that result's
    {prior: <JOINT report>} contains steps. Validate this before CUDA capture.
    """
    try:
        joint_report = original['prior']['prior']
        if (joint_report.get('schema') != joint.SCHEMA or
            joint_report.get('status') != 'COMPLETE_FROZEN_DUAL_PATH_AND_UNSEEN_PROFILE_TRANSFER'):
            raise ValueError('GAMER_TRANSFER_JOINT_REPORT_LINEAGE_MISMATCH')
        record = joint_report['steps'][str(STEP)]['cross_game_training']['test']
        if (record.get('cases') != 108 or
            'complete_triples' not in record or
            'balanced_sign_accuracy' not in record):
            raise ValueError('GAMER_TRANSFER_JOINT_REFERENCE_INCOMPLETE')
        return record
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError('GAMER_TRANSFER_JOINT_REPORT_LINEAGE_MISMATCH') from exc


def run(args):
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    from tools import nanojev_gamer_v1_handoff as gamer
    from tools import nanojev_gamer_v1_polarity_magnitude_repair as repair
    if args.epochs<10 or args.capture_batch<1 or args.head_batch<1:
        raise ValueError('GAMER_TRANSFER_INVALID_ARGUMENTS')
    source=_source(args.v2)
    original=source['original'];o=original['prior']['lineage']['origin']['source']
    expected=_joint_sign_baseline(original)  # Fail before the expensive capture.
    protect=[source['v2_report_path'].parent,original['report_path'].parent,
         original['prior']['joint_dir'],original['prior']['lineage']['report_path'].parent,
         original['prior']['lineage']['origin']['root'],o['training'],o['source'],o['capture'],
         o['ckpt'].parent,o['adapter'],o['audit_path'].parent,o['causal_path'].parent]
    out=three._output(args.output,protect)
    src_hash=_sha(source['v2_report_path'])
    zero_hash=_sha(original['prior']['zero_head'])
    rows=source['rows'];ix=source['indices'];sign,mag=_targets(rows)
    print(f'GAMER TRANSFER AUDIT: frozen LoRA {STEP}; {len(rows)} cases; '
          f'{len(ix["train_extended"])} extended train / {len(ix["dev_extended"])} extended dev / '
          f'{len(ix["counterfactual_holdout"])} new heldout',flush=True)
    features,production=_capture(source,args.device,args.capture_batch,args.head_batch)
    if any(len(v)!=len(rows) or not np.isfinite(v).all() for v in features.values()) or len(production)!=len(rows):
        raise ValueError('GAMER_TRANSFER_CAPTURE_INVALID')
    if _sha(source['v2_report_path'])!=src_hash or _sha(original['prior']['zero_head'])!=zero_hash:
        raise ValueError('GAMER_TRANSFER_SOURCE_CHANGED_DURING_CAPTURE')
    out.mkdir(parents=True,exist_ok=False)
    # Save compact stages for exact, offline followup analysis (no final_flat or prompt text).
    stage_file=out/'frozen_stage_features.npz'
    np.savez_compressed(stage_file,**features)
    baseline_zero,baseline_threshold=repair._zero_proba(original['prior']['zero_head'],features[STAGES[0]])
    existing=dual._reload(original['prior']['zero_head'],features[STAGES[0]][ix['original_test']])
    replay=three._metrics_by_family(rows,ix['original_test'],*existing)
    if (replay['complete_triples']!=expected['complete_triples'] or
        abs(replay['balanced_sign_accuracy']-expected['balanced_sign_accuracy'])>1e-9):
        raise ValueError('GAMER_TRANSFER_FROZEN_ZERO_HEAD_PARITY_FAILED')
    ev={name:ix[name] for name in ('original_test','v2_fresh','counterfactual_holdout')}
    base_zero={name:_by_family(rows,ids,sign,mag,baseline_zero,'zero',baseline_threshold)
               for name,ids in ev.items()}
    report={'schema':SCHEMA,'status':'INCOMPLETE','promotion':'BLOCKED_DIAGNOSTIC_ONLY',
      'frozen_lora_step':STEP,'frozen_lora_sha256':source['v2_report']['frozen_lora_sha256'],
      'v2_report_path':str(source['v2_report_path']),'v2_report_sha256':src_hash,
      'zero_head_sha256':zero_hash,'production_c0_unchanged':True,'lora_unchanged':True,
      'no_production_training_or_promotion':True,'original_v2_deck_sha256':source['v2_report']['combined_deck_sha256'],
      'counterfactual_deck_sha256':oracle.deck_hash(source['deck']),
      'role_hashes':{k:oracle.deck_hash(cases) for k,cases in source['role_cases'].items()},
      'counterfactual_roles':{k:len(v) for k,v in source['role_cases'].items()},
      'profiles':source['profiles'],
      'feature_archive':{'path':str(stage_file),'sha256':_sha(stage_file),
                         'stages':{k:list(v.shape) for k,v in features.items()}},
      'baseline_frozen_zero_head':{'threshold':baseline_threshold,'evaluations':base_zero},
      'settings':{'epochs':args.epochs,'probe_kinds':KINDS,'tasks':TASKS,
                  'label_selection':'TRAIN_FIT_DEV_SELECT_NEVER_HELDOUT',
                  'counterfactual':'payoffs + surface fixed, belief q altered ± three radii',
                  'lexical_confound':'Numerical characters change with belief q; not proof of abstract arithmetic.',
                  'stage_comparability':'LoRA final_mean100 is a summary of final token states, whereas the C0 encoder consumes their 100-position flattened form. Lower readout accuracy downstream alone does not prove the encoder irreversibly erased information.',
                  'new_profile_roles':'12 train / 4 development / 4 heldout profiles; each 3 radii and two surfaces'},
      'stages':{},
      'frozen_c0_original_output':{
         part:{'cases':len(ids),'signed_mae':float(np.mean(np.abs(production[ids]-np.asarray([float(Fraction(rows[int(i)]['target'])) for i in ids])))),
               'zero_within_0_05':float(np.mean(np.abs(production[ids][sign[ids]==0])<=.05))}
         for part,ids in ev.items()}}
    for stage in STAGES:
        print('  stage:',stage,flush=True)
        x=features[stage];arms={}
        for arm,train,dev in (('v2_train_control',ix['base_train'],ix['base_dev']),
                              ('counterfactual_train_extension',ix['train_extended'],ix['dev_extended'])):
            print('    readouts:',arm,flush=True)
            tasks=_fit_stage(source,x,sign,mag,train,dev,ev,epochs=args.epochs,
                             output=out,stage=stage,arm=arm,src_hash=src_hash)
            geom,axis=_geometry(rows,train,x)
            heldout={name:{'paired_geometry':_geometry(rows,ids,x,axis)[0],
                             'magnitude_radius_ordering':_radius_order(rows,ids,x)}
                      for name,ids in ev.items()}
            arms[arm]={'training_cases':len(train),'development_cases':len(dev),
                       'tasks':tasks,'train_geometry':geom,'heldout_geometry':heldout}
        report['stages'][stage]=arms
        gamer.atomic_json(out/f'{stage}.json',arms)
    report['component_gate']=_gate(report['stages'])
    report['status']='COMPLETE_FROZEN_REPRESENTATION_AUDIT_NO_PROMOTION'
    report['interpretation']='Probe success demonstrates recoverability by tested limited readouts, not numerical understanding; probe failure does not prove information absent. Frozen C0 stage changes are causal along the actual forward path but lexical changes remain a confound. Existing hawk/V2 fresh were inspected before; new heldout payoff profiles were generated without test selection. LoRA mean100 and C0 encoder are derived from different representations of the same token states, so this cannot solely attribute a drop to encoder information destruction.'
    if _sha(source['v2_report_path'])!=src_hash or _sha(original['prior']['zero_head'])!=zero_hash:
        raise ValueError('GAMER_TRANSFER_SOURCE_CHANGED_DURING_RUN')
    gamer.atomic_json(out/'final_report.json',report)
    print('GAMER TRANSFER AUDIT COMPLETE:',out/'final_report.json',flush=True)
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--v2',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--device',choices=('cpu','cuda','directml'),default='cuda')
    p.add_argument('--capture-batch',type=int,default=2)
    p.add_argument('--head-batch',type=int,default=12)
    p.add_argument('--epochs',type=int,default=80)
    return run(p.parse_args(argv))

if __name__=='__main__':main()
