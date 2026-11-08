#!/usr/bin/env python3
"""GAMER V1 diagnostic: fixed-zero, held-out-profile polarity + magnitude repair.

Production LoRA checkpoint 3000 and C0 are frozen. Prior successful zero detector
is replayed WITHOUT retraining. Independently fit (a) a nonzero polarity specialist
and (b) a magnitude-only regressor using the final LoRA mean100 representation.
The 720-case cross-game control and the 792-case added-HERO game arm are both
predeclared; hawk profiles never enter TRAIN or DEV. No TEST-driven model choice.
Signed recombination is explicitly BLOCKED unless per-family sign/zero/triple
and magnitude tolerance gates independently pass. Do not promote artifacts.

python -m tools.nanojev_gamer_v1_polarity_magnitude_repair \\
 --joint runtime/reports/gamer-v1/c0-joint-sign-magnitude-0001 \\
 --output runtime/reports/gamer-v1/c0-polarity-magnitude-0001 \\
 --device cuda
"""
from __future__ import annotations

import argparse
from fractions import Fraction
import gc
import json
import os
from pathlib import Path
import numpy as np

from tools import nanojev_gamer_v1_joint_sign_magnitude_lab as joint
from tools import nanojev_gamer_v1_dual_sign_head as dual
from tools import nanojev_gamer_v1_c0_sign_head as three

SCHEMA='nanojev.gamer.v1.frozen-polarity-magnitude-independent-repair.v1'
EXTRA_GAME='hero'  # TSPR, NOT the held-out hawk TRSP payoff profile.
EXTRA_EPS=tuple(Fraction(1,d) for d in (2000,1600,1200,1000,800,700,600,500,400,300,200,100))
STEP=3000
REGS=(.001,.05)
KINDS=('linear','rbf_1.0','rbf_4.0')


def _json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _hero_deck(base):
    """Add 72 certified TRAIN cases; forbid hawk payoff order and test leakage."""
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    from tools import nanojev_gamer_v1_zero_sign_frob as original
    from tools import nanojev_two_game_spine_smoke as spine
    if spine.PAYOFF_ORDERS[EXTRA_GAME] == spine.PAYOFF_ORDERS['chicken_hawk_dove']:
        raise ValueError('GAMER_REPAIR_HAWK_PAYOFF_ORDER_EXPOSED')
    old=list(base);extra=[]
    for i,eps in enumerate(EXTRA_EPS):
        stage=spine.ordinal_stage(spine.PAYOFF_ORDERS[EXTRA_GAME],shift=3+2*i,scale=i+1)
        q0=spine.critical_q(stage)
        if q0 is None or stage.margin(q0)!=0 or (stage.R,stage.S,stage.T,stage.P)==(5,2,8,-3):
            raise ValueError('GAMER_REPAIR_BAD_EXTRA_PROFILE')
        group=f'train.belief.hero.{i:04d}'
        for tag,delta in (('negative',-eps),('zero',Fraction(0)),('positive',eps)):
            q=q0+delta
            if not 0<=q<=1:raise ValueError('GAMER_REPAIR_AUX_Q_OUT_OF_RANGE')
            for surface,renderer in (
                ('spine_belief',original._spine_belief_prompt),
                ('game_prose_belief',original._game_prose_belief_prompt)):
                extra.append(oracle.Case(
                    f'{group}.{tag}.{surface}','train',group,surface,
                    renderer(stage,q),stage.margin(q),'aux_hero',delta))
    original_ids={c.id for c in old}
    original_prompts={oracle.sha_text(c.prompt) for c in old}
    if (len(extra)!=72 or len({c.id for c in extra})!=72 or
        any(c.id in original_ids or oracle.sha_text(c.prompt) in original_prompts
            or c.split!='train' or c.family=='hawk_indifference' for c in extra)):
        raise ValueError('GAMER_REPAIR_TRAIN_HOLDOUT_COLLISION')
    oracle.validate_deck(old+extra)
    return old+extra,extra


def _source(joint_dir):
    root=Path(joint_dir).resolve()
    report_path=root/'final_report.json'
    previous=_json(report_path)
    if (previous.get('schema')!=joint.SCHEMA or
        previous.get('status')!='COMPLETE_FROZEN_DUAL_PATH_AND_UNSEEN_PROFILE_TRANSFER' or
        previous.get('promotion')!='BLOCKED_DIAGNOSTIC_ONLY' or
        previous.get('no_production_lora_or_c0_training') is not True or
        previous.get('original_72_dev_and_108_test_unchanged') is not True or
        str(STEP) not in previous.get('steps',{})):
        raise ValueError('GAMER_REPAIR_REQUIRES_COMPLETED_FROZEN_JOINT_AUDIT')
    lineage=joint._source(Path(previous['source_dual_report']).parent,[STEP])
    # Verify every previously attested upstream fingerprint and the exact deck.
    current={'dual_report_sha256':three._sha(lineage['report_path']),
             **dual._fingerprints(lineage['origin'])}
    step=previous['steps'][str(STEP)]
    if (previous['fingerprints']!=current or
        previous['combined_deck_sha256']!=lineage['combined_deck_sha256'] or
        previous['additional_auxiliary_train_deck_sha256']!=lineage['additional_deck_sha256'] or
        step['snapshot_sha256']!=lineage['origin']['source']['snapshots'][STEP][1] or
        previous['hawk_test_profile_trained'] is not False):
        raise ValueError('GAMER_REPAIR_PRIOR_LINEAGE_OR_HOLDOUT_CHANGED')
    artifact=step['cross_game_training']['diagnostic_binary_head_artifact']
    if (artifact['type']!='DIAGNOSTIC_TWO_BINARY_SIGN_HEADS_NO_MAGNITUDE' or
        not Path(artifact['path']).is_file() or
        artifact['sha256']!=three._sha(artifact['path'])):
        raise ValueError('GAMER_REPAIR_FROZEN_ZERO_ARTIFACT_CHANGED')
    deck,extra=_hero_deck(lineage['deck'])
    rows=[c.public() for c in deck]
    if rows[:len(lineage['rows'])]!=lineage['rows']:
        raise ValueError('GAMER_REPAIR_CHANGED_ORIGINAL_900_CASES')
    split=three._partitions(rows)
    if (len(split['train'])!=792 or len(split['dev'])!=72 or len(split['test'])!=108 or
        any(rows[int(i)]['family']=='hawk_indifference' for i in split['train']) or
        sum(rows[int(i)]['family']=='hawk_indifference' for i in split['test'])!=36):
        raise ValueError('GAMER_REPAIR_CHANGED_ORACLE_SPLITS')
    return {'joint_dir':root,'prior_path':report_path,'prior':previous,
            'lineage':lineage,'deck':deck,'extra':extra,'rows':rows,
            'split':split,'zero_head':Path(artifact['path']),
            'fingerprints':current}


def _zero_proba(path,raw):
    """Replay only the PRIOR zero detector; independent of new polarity fitting."""
    with np.load(path,allow_pickle=False) as z:
        if str(z['schema'])!=dual.SCHEMA:
            raise ValueError('GAMER_REPAIR_ZERO_CHECKPOINT_SCHEMA_MISMATCH')
        spec=(z['mean'],z['std'],z['reference'],float(z['median_distance']))
        mapped=dual._map_from_spec(raw,str(z['zero_kind']),spec)
        pzero=dual._sigmoid(mapped.astype(np.float64)@z['zero_weight'].astype(np.float64)+float(z['zero_bias']))
        threshold=float(z['zero_threshold'])
    if not np.isfinite(pzero).all() or not 0<=threshold<=1:
        raise ValueError('GAMER_REPAIR_NONFINITE_FROZEN_ZERO_PROBABILITY')
    return pzero,threshold


def _store_head(path,raw,train_idx,kind,weights,bias,epoch,*,head):
    """Store train-scaler, optional RBF reference and fitted weights; no TEST labels."""
    path=Path(path)
    if path.exists():raise FileExistsError('GAMER_REPAIR_CHECKPOINT_ALREADY_EXISTS')
    raw=np.asarray(raw,dtype=np.float32)
    train=raw[np.asarray(train_idx,dtype=int)].astype(np.float64)
    mean=train.mean(axis=0,keepdims=True)
    std=np.sqrt(np.maximum(np.mean((train-mean)**2,axis=0,keepdims=True),1e-6))
    ref=np.asarray((train-mean)/std,dtype=np.float32)
    aa=ref.astype(np.float64)@ref.astype(np.float64).T
    d=np.maximum(np.diag(aa)[:,None]+np.diag(aa)[None,:]-2*aa,0)/raw.shape[1]
    pos=d[np.triu_indices(len(ref),1)]
    pos=pos[pos>1e-12]
    median=float(np.median(pos)) if len(pos) else 0.
    payload={'schema':np.asarray(SCHEMA),'head':np.asarray(head),'kind':np.asarray(kind),
             'mean':mean,'std':std,
             'reference':ref if kind!='linear' else np.empty((0,raw.shape[1]),np.float32),
             'median_distance':np.asarray(median),
             'weights':np.asarray(weights,dtype=np.float32),
             'bias':np.asarray(bias,dtype=np.float64),'epoch':np.asarray(epoch)}
    tmp=path.with_suffix('.tmp.npz')
    try:
        np.savez_compressed(tmp,**payload)
        os.replace(tmp,path)
    finally:
        if tmp.exists():tmp.unlink()
    return {'path':str(path),'sha256':three._sha(path),'type':f'DIAGNOSTIC_{head.upper()}_ONLY'}


def _head_replay(path,raw,expected_head):
    with np.load(path,allow_pickle=False) as z:
        if str(z['schema'])!=SCHEMA or str(z['head'])!=expected_head:
            raise ValueError('GAMER_REPAIR_HEAD_CHECKPOINT_SCHEMA_MISMATCH')
        spec=(z['mean'],z['std'],z['reference'],float(z['median_distance']))
        mapped=dual._map_from_spec(raw,str(z['kind']),spec)
        weights=z['weights'].astype(np.float64)
        if mapped.shape[1]!=len(weights):
            raise ValueError('GAMER_REPAIR_HEAD_CHECKPOINT_WIDTH_MISMATCH')
        logit=mapped.astype(np.float64)@weights+float(z['bias'])
    return dual._sigmoid(logit) if expected_head=='polarity' else _decode_magnitude(logit)


def _fit_polarity(rows,raw,train,dev,*,triple_weight,epochs,output):
    """Train nonzero polarity only; the prior zero head is never touched."""
    y=dual._labels(rows)
    split={'train':np.asarray(train,dtype=int),'dev':np.asarray(dev,dtype=int),
           'test':three._partitions(rows)['test']}
    maps,feature_descriptor=three._train_only_maps(raw,split)
    pairs=dual._triples(rows,train)
    best=None;options=[]
    for kind in KINDS:
        for reg in REGS:
            fitted=dual._binary(maps[kind][train],y[train],maps[kind][dev],y[dev],pairs,
                task='polarity',regularization=reg,epochs=epochs,triple_weight=triple_weight)
            pdev=dual._score(fitted,maps[kind][dev])
            measured=dual._binary_diag(y[dev],pdev,task='polarity')
            rank=(min(measured['recall_negative_class'],measured['recall_positive_class']),
                  measured['balanced_accuracy'],-fitted['development']['dev_nll'],
                  int(kind=='linear'),-reg)
            options.append({'kind':kind,'regularization':reg,'dev':measured,
                            'epoch':fitted['development']['epoch']})
            if best is None or rank>best[0]:best=(rank,kind,reg,fitted,measured)
    _,kind,reg,fitted,dev_result=best
    chosen=dual._score(fitted,maps[kind][np.arange(len(rows))])
    artifact=_store_head(output,raw,train,kind,fitted['weights'],fitted['bias'],
                         fitted['development']['epoch'],head='polarity')
    replay=_head_replay(output,np.asarray(raw), 'polarity')
    if not np.allclose(chosen,replay,atol=1e-5):
        raise ValueError('GAMER_REPAIR_POLARITY_HEAD_REPLAY_FAILED')
    return chosen,{'training_cases':len(train),'pairs':len(pairs),
        'selection':'TRAIN_FIT_DEV_SELECTED_TEST_NOT_SELECTED',
        'triplet_weight':triple_weight,'feature_policy':feature_descriptor,
        'selected':{'kind':kind,'regularization':reg,**fitted['development']},
        'dev':dev_result,'candidates':options,'checkpoint':artifact}


def _decode_magnitude(logit):
    x=np.asarray(logit,dtype=np.float64)
    return np.expm1(np.clip(x,0.,4.))


def _fit_magnitude_candidate(train_x,y_train,dev_x,y_dev,pairs,*,reg,epochs,paired_weight):
    """Independent absolute-value regression in log1p space (CPU, no LoRA updates)."""
    import torch
    import torch.nn.functional as F
    x=torch.from_numpy(np.asarray(train_x,dtype=np.float32))
    xd=torch.from_numpy(np.asarray(dev_x,dtype=np.float32))
    target=torch.from_numpy(np.log1p(np.asarray(y_train,dtype=np.float32)))
    if (x.ndim!=2 or xd.ndim!=2 or len(x)!=len(target) or len(pairs)==0 or
        not np.isfinite(x.numpy()).all() or not np.isfinite(xd.numpy()).all() or
        np.any(np.asarray(y_train)<0) or np.any(np.asarray(y_dev)<0)):
        raise ValueError('GAMER_REPAIR_BAD_MAGNITUDE_TRAINING_ARRAYS')
    torch.manual_seed(47)
    weight=torch.nn.Parameter(torch.zeros((x.shape[1],),dtype=torch.float32))
    bias=torch.nn.Parameter(torch.tensor(float(np.log1p(np.mean(y_train))),dtype=torch.float32))
    optimizer=torch.optim.Adam((weight,bias),lr=.035)
    tri=torch.from_numpy(np.asarray(pairs,dtype=np.int64))
    best=None
    for epoch in range(1,epochs+1):
        optimizer.zero_grad()
        z=x@weight+bias
        loss=F.smooth_l1_loss(z,target,beta=.1)
        if paired_weight:
            group=z[tri]
            # Equal absolute magnitude for true negative and positive of a
            # mathematical triple; zero is separately labeled exactly zero.
            loss=loss+paired_weight*((group[:,0]-group[:,2])**2).mean()
        loss=loss+(reg/max(1,x.shape[1]))*weight.square().sum()
        if not torch.isfinite(loss):raise ValueError('GAMER_REPAIR_NONFINITE_MAGNITUDE_LOSS')
        loss.backward();optimizer.step()
        if epoch%10 and epoch!=epochs:continue
        with torch.no_grad():
            pred=_decode_magnitude((xd@weight+bias).numpy())
            dev_err=np.abs(pred-np.asarray(y_dev,dtype=np.float64))
            mae=float(np.mean(dev_err));p90=float(np.quantile(dev_err,.9))
            rank=(-mae,-p90,-epoch)
            if best is None or rank>best[0]:
                best=(rank,weight.detach().numpy().copy(),float(bias.detach()),
                      epoch,mae,p90)
    return {'weights':best[1],'bias':best[2],'epoch':best[3],
            'dev_mae':best[4],'dev_p90':best[5]}


def _magnitude_metrics(rows,idx,pred,tol):
    ix=np.asarray(idx,dtype=int)
    mag=np.asarray(pred,dtype=np.float64)
    if mag.shape!=(len(rows),) or np.any(mag<0) or not np.isfinite(mag).all():
        raise ValueError('GAMER_REPAIR_BAD_MAGNITUDE_ARRAY')
    y=np.asarray([abs(float(Fraction(row['target']))) for row in rows])
    def stats(keep):
        a=np.asarray(keep,dtype=int)
        err=np.abs(mag[a]-y[a]);z=y[a]==0
        return {'cases':len(a),'mae':float(err.mean()),
                'p90_abs_error':float(np.quantile(err,.9)),
                'within_tolerance':float(np.mean(err<=tol)),
                'nonzero_within_tolerance':float(np.mean(err[~z]<=tol)) if np.any(~z) else None,
                'zero_mean_predicted_magnitude':float(np.mean(mag[a][z])) if np.any(z) else None,
                'zero_count':int(z.sum())}
    fam={name:stats([i for i in ix if rows[int(i)]['family']==name])
         for name in sorted({rows[int(i)]['family'] for i in ix})}
    return {'all':stats(ix),'by_family':fam,'tolerance':tol}


def _fit_magnitude(rows,raw,train,dev,*,epochs,paired_weight,tolerance,output):
    split={'train':np.asarray(train,dtype=int),'dev':np.asarray(dev,dtype=int),
           'test':three._partitions(rows)['test']}
    maps,feature_descriptor=three._train_only_maps(raw,split)
    y=np.asarray([abs(float(Fraction(r['target']))) for r in rows],dtype=np.float64)
    triples=dual._triples(rows,train)
    best=None;options=[]
    for kind in KINDS:
        for reg in REGS:
            model=_fit_magnitude_candidate(maps[kind][train],y[train],maps[kind][dev],
                                           y[dev],triples,reg=reg,epochs=epochs,
                                           paired_weight=paired_weight)
            rank=(-model['dev_mae'],-model['dev_p90'],int(kind=='linear'),-reg)
            options.append({'kind':kind,'regularization':reg,'epoch':model['epoch'],
                            'dev_mae':model['dev_mae'],'dev_p90':model['dev_p90']})
            if best is None or rank>best[0]:best=(rank,kind,reg,model)
    _,kind,reg,model=best
    predicted=_decode_magnitude(maps[kind].astype(np.float64)@model['weights'].astype(np.float64)+model['bias'])
    checkpoint=_store_head(output,raw,train,kind,model['weights'],model['bias'],model['epoch'],
                           head='magnitude')
    replay=_head_replay(output,raw,'magnitude')
    if not np.allclose(predicted,replay,rtol=1e-5,atol=1e-5):
        raise ValueError('GAMER_REPAIR_MAGNITUDE_HEAD_REPLAY_FAILED')
    return predicted,{'training_cases':len(train),'training_triples':len(triples),
       'selection':'TRAIN_FIT_DEV_SELECTED_TEST_NOT_SELECTED',
       'pair_abs_symmetry_weight':paired_weight,'feature_policy':feature_descriptor,
       'selected':{'kind':kind,'regularization':reg,'epoch':model['epoch'],
                   'dev_mae':model['dev_mae'],'dev_p90':model['dev_p90']},
       'dev_metrics':_magnitude_metrics(rows,dev,predicted,tolerance),
       'candidates':options,'checkpoint':checkpoint}


def _hawk_polarity_geometry(rows,test_idx,p_pos):
    """Held-out-only diagnostic: did the scalar polarity direction reverse?"""
    from collections import defaultdict
    group=defaultdict(dict)
    for i in test_idx:
        r=rows[int(i)]
        if r['family']!='hawk_indifference':continue
        sign=int((Fraction(r['target'])>0)-(Fraction(r['target'])<0))
        group[(r['group'],r['surface'])][sign]=float(p_pos[int(i)])
    if len(group)!=12 or any(set(v)!={-1,0,1} for v in group.values()):
        raise ValueError('GAMER_REPAIR_HAWK_TRIPLES_CHANGED')
    diff=np.asarray([v[1]-v[-1] for v in group.values()],dtype=float)
    return {'pairs':len(diff),'positive_greater_than_negative_count':int(np.count_nonzero(diff>0)),
            'mean_polarity_probability_delta':float(np.mean(diff)),
            'median_polarity_probability_delta':float(np.median(diff)),
            'min_polarity_probability_delta':float(np.min(diff)),
            'max_polarity_probability_delta':float(np.max(diff)),
            'diagnostic_only_not_used_for_selection':True}


def _acceptance(sign_metrics,magnitude_metrics,baseline_magnitude,*,tolerance,required=.95):
    reasons=[]
    for family,s in sign_metrics['by_family'].items():
        for cls,score in s['recall'].items():
            if score+1e-12<required:reasons.append(f'{family}:sign_recall_{cls}')
        if s['triple_accuracy']+1e-12<required:reasons.append(f'{family}:complete_triples')
    for family,m in magnitude_metrics['by_family'].items():
        b=baseline_magnitude['by_family'][family]
        if m['within_tolerance']+1e-12<required:
            reasons.append(f'{family}:magnitude_within_{tolerance}')
        if m['nonzero_within_tolerance']+1e-12<required:
            reasons.append(f'{family}:nonzero_magnitude_within_{tolerance}')
        if not m['mae']<b['mae']:
            reasons.append(f'{family}:magnitude_mae_not_improved')
        if not m['p90_abs_error']<b['p90_abs_error']:
            reasons.append(f'{family}:magnitude_p90_not_improved')
    return {'passed':not reasons,'reasons':reasons,'per_family_required':required,
            'magnitude_abs_tolerance':tolerance}


def run(args):
    import torch
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    from tools import nanojev_gamer_v1_c0_sign_causality as causal
    from tools import nanojev_gamer_v1_c0_boundary as c0
    from tools import nanojev_gamer_v1_lora_sign_stress as stress
    from tools import nanojev_gamer_v1_handoff as gamer

    if args.epochs<10 or args.polarity_triplet_weight<0 or args.magnitude_pair_weight<0 or \
       not 0<args.magnitude_tolerance<=1:
        raise ValueError('GAMER_REPAIR_BAD_EXPERIMENT_SETTINGS')
    src=_source(args.joint)
    origin=src['lineage']['origin']['source']
    meta=origin['meta'];root=src['lineage'];rows=src['rows'];split=src['split']
    step=src['prior']['steps'][str(STEP)]
    protect=[src['joint_dir'],src['lineage']['report_path'].parent,
             root['origin']['root'],origin['training'],origin['source'],origin['capture'],
             origin['ckpt'].parent,origin['adapter'],origin['audit_path'].parent,
             origin['causal_path'].parent]
    out=three._output(args.output,protect)
    frozen_hashes={'joint_report_sha256':three._sha(src['prior_path']),
                   'zero_head_sha256':three._sha(src['zero_head']),
                   **src['fingerprints']}
    snapshot,sha=origin['snapshots'][STEP]
    if stress._verify_snapshot(snapshot,STEP)!=sha:
        raise ValueError('GAMER_REPAIR_FROZEN_LORA_SNAPSHOT_CHANGED')
    # Verify C0 before Qwen capture; their model weights never require gradients.
    net=c0._load_head(meta,args.device)
    original=np.asarray(origin['archive']['lora__final_flat'],dtype=np.float32)
    _,_,_,prod_original=causal._read_stages(net,original,device=args.device,batch=args.head_batch)
    c0._verify_baseline(prod_original,origin['archive']['production_head_predictions'])
    del net,original,prod_original
    gc.collect()
    if torch.cuda.is_available():torch.cuda.empty_cache()
    print('GAMER POLARITY+MAGNITUDE: frozen LoRA step 3000, 792 TRAIN / 72 DEV / 108 TEST',flush=True)
    _,tok,model=gamer.model_components(meta['lineage']['model_id'],args.device,adapter_dir=snapshot)
    feature,_=oracle._capture_profiles(torch,model,tok,src['deck'],device=args.device,
        max_length=meta['lineage']['configuration']['max_length'],
        batch_size=args.capture_batch,curvature=meta['lineage']['configuration']['curvature'],
        verify_exact_final=True)
    del tok,model
    gc.collect()
    if torch.cuda.is_available():torch.cuda.empty_cache()
    final=np.asarray(feature['final_mean100'],dtype=np.float32)
    flat=np.asarray(feature['final_flat'],dtype=np.float16).astype(np.float32)
    del feature
    net=c0._load_head(meta,args.device)
    _,_,_,production=causal._read_stages(net,flat,device=args.device,batch=args.head_batch)
    del net,flat
    gc.collect()
    if torch.cuda.is_available():torch.cuda.empty_cache()
    # All original 324-case C0 predictions must be unchanged (indices correspond
    # to the original audit order, not a subset selected by numeric value).
    old=stress._head_metrics(origin['rows'],production[root['origin']['old_indices']])['test']
    known=origin['audit']['steps'][str(STEP)]['metrics']['production_c0']['test']
    if (old['triple_all_correct']!=known['triple_all_correct'] or
        abs(old['signed_mae']-known['signed_mae'])>.001):
        raise ValueError('GAMER_REPAIR_C0_PARITY_FAILED')
    pzero,zero_threshold=_zero_proba(src['zero_head'],final)
    # Attest the unchanged old sign-head decisions on the exact original TEST.
    cls_prior,prob_prior=dual._reload(src['zero_head'],final[split['test']])
    prior_sign=step['cross_game_training']['test']
    replay=three._metrics_by_family(rows,split['test'],cls_prior,prob_prior)
    if (replay['complete_triples']!=prior_sign['complete_triples'] or
        abs(replay['balanced_sign_accuracy']-prior_sign['balanced_sign_accuracy'])>1e-10):
        raise ValueError('GAMER_REPAIR_PRIOR_SIGN_REPLAY_FAILED')
    # Consistency: zero probabilities from the saved head must match the original
    # complete softmax decomposition, not a retrained replacement.
    if not np.allclose(pzero[split['test']],prob_prior[:,1],atol=1e-8):
        raise ValueError('GAMER_REPAIR_ZERO_HEAD_REPLAY_CHANGED')
    print('  frozen cross-game zero detector replayed unchanged',flush=True)
    out.mkdir(parents=True,exist_ok=False)
    y=dual._labels(rows)
    train720=np.asarray([i for i in split['train'] if rows[int(i)]['family']!='aux_hero'],dtype=int)
    train792=np.asarray(split['train'],dtype=int)
    if len(train720)!=720:raise ValueError('GAMER_REPAIR_720_CONTROL_CHANGED')
    baseline_mag=_magnitude_metrics(rows,split['test'],np.abs(production),args.magnitude_tolerance)
    result={'schema':SCHEMA,'status':'INCOMPLETE','promotion':'BLOCKED_DIAGNOSTIC_ONLY',
            'source_joint_report':str(src['prior_path']),
            'original_test_unchanged':True,'hawk_profiles_in_train':0,
            'hawk_profiles_in_dev':0,
            'hawk_profile_reused_across_diagnostic_iterations':True,
            'fresh_blind_profile_certification':False,
            'new_train_game':'hero',
            'new_train_cases':len(src['extra']),
            'extra_deck_sha256':oracle.deck_hash(src['extra']),
            'frozen_lora_step':STEP,'frozen_lora_sha256':sha,
            'no_production_weights_changed':True,
            'zero_detector_unchanged':True,'frozen_fingerprints':frozen_hashes,
            'settings':{'epochs':args.epochs,'polarity_triplet_weight':args.polarity_triplet_weight,
                        'magnitude_pair_weight':args.magnitude_pair_weight,
                        'magnitude_tolerance':args.magnitude_tolerance,
                        'min_class_and_triple_recall':.95},
            'frozen_production_c0':old,
            'frozen_c0_magnitude':baseline_mag,
            'prior_cross_game_sign_replayed':replay,
            'arms':{}}
    for name,train in (('original_720_control',train720),('hero_792_extension',train792)):
        print(f'  fitting separate polarity + magnitude: {name}',flush=True)
        ppol,pol_diag=_fit_polarity(rows,final,train,split['dev'],
            triple_weight=args.polarity_triplet_weight,epochs=args.epochs,
            output=out/f'{name}-polarity.npz')
        signs,probs=dual._combine(pzero[split['test']],ppol[split['test']],zero_threshold)
        test_sign=three._metrics_by_family(rows,split['test'],signs,probs)
        binary=dual._conditional_test_metrics(rows,split['test'],pzero[split['test']],
                                               ppol[split['test']],zero_threshold)
        geometry=_hawk_polarity_geometry(rows,split['test'],ppol)
        pred_mag,mag_diag=_fit_magnitude(rows,final,train,split['dev'],
            epochs=args.epochs,paired_weight=args.magnitude_pair_weight,
            tolerance=args.magnitude_tolerance,output=out/f'{name}-magnitude.npz')
        mag_test=_magnitude_metrics(rows,split['test'],pred_mag,args.magnitude_tolerance)
        magnitude_accept=_acceptance(test_sign,mag_test,baseline_mag,
            tolerance=args.magnitude_tolerance)
        # Do NOT score signed reconstruction unless BOTH components reach
        # predeclared held-out competence in EACH family.
        if magnitude_accept['passed']:
            assembled=np.ones(len(rows),dtype=np.int64)
            assembled[split['test']]=signs
            combined={'status':'EVALUATED_AFTER_INDEPENDENT_COMPONENT_GATES',
                      'scores':joint._reconstruction(rows,split['test'],assembled,pred_mag,production)}
        else:
            combined={'status':'BLOCKED_COMPONENT_ACCEPTANCE_FAILED',
                      'reasons':magnitude_accept['reasons']}
        item={'sign':{'test':test_sign,'independent_binary_heldout':binary,
                      'hawk_polarity_geometry':geometry,'polarity_head':pol_diag,
                      'zero_head_source_sha256':frozen_hashes['zero_head_sha256'],
                      'zero_threshold_preserved':zero_threshold},
              'magnitude':{'test':mag_test,'head':mag_diag,
                           'frozen_c0_reference':baseline_mag},
              'component_acceptance':magnitude_accept,
              'reconstruction':combined}
        result['arms'][name]=item
        gamer.atomic_json(out/f'{name}.json',item)
        hawk=test_sign['by_family']['hawk_indifference']
        print('    hawk -recall=%.3f zero=%.3f triples=%d/12, magnitude MAE=%.4f; combination=%s' % (
            hawk['recall']['-1'],hawk['recall']['0'],hawk['complete_triples'],
            mag_test['by_family']['hawk_indifference']['mae'],combined['status']),flush=True)
    if stress._verify_snapshot(snapshot,STEP)!=sha:
        raise ValueError('GAMER_REPAIR_FROZEN_LORA_MUTATED_DURING_DIAGNOSTIC')
    if ({'joint_report_sha256':three._sha(src['prior_path']),
         'zero_head_sha256':three._sha(src['zero_head']),
         **{'dual_report_sha256':three._sha(src['lineage']['report_path']),
            **dual._fingerprints(src['lineage']['origin'])}}!=frozen_hashes):
        raise ValueError('GAMER_REPAIR_FROZEN_SOURCE_MUTATION_DETECTED')
    result['status']='COMPLETE_INDEPENDENT_DIAGNOSTICS_NO_PRODUCTION_PROMOTION'
    result['interpretation']=(
        'Frozen LoRA step 3000, frozen C0, and prior zero detector. Both polarity '
        'specialist and magnitude regressor fit on TRAIN with DEV selection only. '
        'Hawk is held out in both arms. Combining sign and magnitude is forbidden '
        'unless every family passes sign, zero, complete triples, magnitude '
        'within-tolerance and improvement gates. No TEST selection. Hawk is '
        'absent from training, but its metrics have been inspected across prior '
        'experiments, so this is not fresh blind generalization certification.')
    gamer.atomic_json(out/'final_report.json',result)
    print('GAMER INDEPENDENT REPAIR COMPLETE:',out/'final_report.json',flush=True)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--joint',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--device',choices=('cuda','cpu','directml'),default='cuda')
    parser.add_argument('--capture-batch',type=int,default=2)
    parser.add_argument('--head-batch',type=int,default=12)
    parser.add_argument('--epochs',type=int,default=160)
    parser.add_argument('--polarity-triplet-weight',type=float,default=1.0)
    parser.add_argument('--magnitude-pair-weight',type=float,default=.25)
    parser.add_argument('--magnitude-tolerance',type=float,default=.05)
    return run(parser.parse_args(argv))


if __name__=='__main__':main()
