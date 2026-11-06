#!/usr/bin/env python3
"""Train-only, closed-form flat register readouts on frozen GAMER features.

Two explicitly different choices are fitted per register: the original
per-game/role ridge probe and a pooled same-role ridge. A two-fold, train-only
comparison selects the pooled readout only where it demonstrably improves
regression; no dev features or labels participate in fitting or selection.

The coefficients are independent readouts; there are no masked AdamW moments,
no shared trainable encoder, and no oracle routing at inference.
"""
from __future__ import annotations

from collections import defaultdict
import math
import numpy as np


def _signed(x):
    x=np.asarray(x,dtype=np.float64)
    return np.sign(x)*np.log1p(np.abs(x))


def _inverse(x):
    x=np.asarray(x,dtype=np.float64)
    return np.sign(x)*np.expm1(np.clip(np.abs(x),0,25.))


def _ridge_fit(features,targets,alpha=.1):
    """Algebraically identical to register-oracle's bounded dual ridge probe."""
    x=np.asarray(features,dtype=np.float64)
    y=_signed(targets)
    if x.ndim!=2 or not len(y) or len(x)!=len(y) or not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError('GAMER_FLAT_RIDGE_NONFINITE_OR_EMPTY')
    center=x.mean(axis=0)
    centered=x-center
    scale=max(float(np.sqrt(np.mean(centered*centered))),1e-6)*math.sqrt(x.shape[1])
    normalized=centered/scale
    kernel=normalized@normalized.T
    lam=max(alpha*float(np.trace(kernel))/len(x),1e-6)
    dual=np.linalg.solve(kernel+np.eye(len(x))*lam,y-y.mean())
    coef=(normalized.T@dual)/scale
    intercept=float(y.mean()-center@coef)
    if not np.isfinite(coef).all() or not math.isfinite(intercept):
        raise ValueError('GAMER_FLAT_RIDGE_NONFINITE_WEIGHTS')
    return coef,intercept


def _predict(x,coef,bias):
    return np.asarray(x,dtype=np.float64)@coef+bias


def _groups(docs,slots):
    per_slot=defaultdict(list)
    per_role=defaultdict(list)
    for i,doc in enumerate(docs):
        game=doc['game_id']
        for reg in doc['registers']:
            key=(game,reg['id'])
            if key not in slots or reg['visibility']!='PROMPT_LITERAL_VERIFIED':
                raise ValueError('GAMER_FLAT_RIDGE_UNCERTIFIED_LABEL')
            entry=(i,float(reg['value']['float']))
            per_slot[slots[key]].append(entry)
            per_role[reg['id']].append(entry)
    return per_slot,per_role


def _fit_group(x,entries):
    indices=np.array([i for i,_ in entries],dtype=int)
    values=np.array([v for _,v in entries],dtype=np.float64)
    return _ridge_fit(x[indices],values)


def fit_register_readouts(x_train,train_docs,names,slots,*,alpha=.1):
    """Pool identical register roles, retain per-game ridge, choose via train-only CV.

    Folds are assigned within each game by sorted case position, so each game
    contributes to both folds and no development row participates in selection.
    """
    x=np.asarray(x_train,dtype=np.float64)
    if x.ndim!=2 or x.shape[0]!=len(train_docs) or not np.isfinite(x).all():
        raise ValueError('GAMER_FLAT_RIDGE_FEATURE_MISMATCH')
    if not (0<alpha<=10):
        raise ValueError('GAMER_FLAT_RIDGE_INVALID_ALPHA')
    per_slot,per_role=_groups(train_docs,slots)
    roles=sorted(per_role)
    role_ids={r:i for i,r in enumerate(roles)}
    dimension=x.shape[1]
    n=len(names)
    slot_coef=np.empty((n,dimension),dtype=np.float64)
    slot_bias=np.empty(n,dtype=np.float64)
    role_coef=np.empty((len(roles),dimension),dtype=np.float64)
    role_bias=np.empty(len(roles),dtype=np.float64)
    slot_role=np.empty(n,dtype=np.int64)
    slot_support=np.empty(n,dtype=np.int64)
    role_support=np.empty(len(roles),dtype=np.int64)
    selection=[]

    # Fit each same-role pool just once. All 7,116 available labels are used;
    # no silent per-role cap or replacement with zero-filled target rows.
    for role,j in role_ids.items():
        entries=per_role[role]
        role_coef[j],role_bias[j]=_fit_group(x,entries)
        role_support[j]=len(entries)

    # Cross-validation is strictly within TRAIN and is used only for deciding
    # whether same-role pooling is safer than the independent game/role probe.
    # Fold assignment is by position within each game (not global row index).
    per_game=defaultdict(list)
    for i,doc in enumerate(train_docs):per_game[doc['game_id']].append(i)
    folds={}
    for game,indices in per_game.items():
        for j,i in enumerate(sorted(indices)):
            folds[i]=j%2
    cv={s:{'game':[],'role':[]} for s in range(n)}
    for fold in (0,1):
        role_models={}
        for role,entries in per_role.items():
            train=[e for e in entries if folds[e[0]]!=fold]
            if len(train)>=2:
                role_models[role]=_fit_group(x,train)
        for j,(game,role) in enumerate(names):
            entries=per_slot[j]
            hold=[e for e in entries if folds[e[0]]==fold]
            keep=[e for e in entries if folds[e[0]]!=fold]
            if not hold or len(keep)<2 or role not in role_models:
                continue
            game_coef,game_bias=_fit_group(x,keep)
            role_coef_cv,role_bias_cv=role_models[role]
            ii=np.array([i for i,_ in hold],dtype=int)
            truth=np.array([v for _,v in hold],dtype=np.float64)
            gp=_inverse(_predict(x[ii],game_coef,game_bias))
            rp=_inverse(_predict(x[ii],role_coef_cv,role_bias_cv))
            cv[j]['game'].extend(np.abs(gp-truth).tolist())
            cv[j]['role'].extend(np.abs(rp-truth).tolist())
    use_role=np.zeros(n,dtype=np.bool_)
    for j,(game,role) in enumerate(names):
        entries=per_slot[j]
        slot_coef[j],slot_bias[j]=_fit_group(x,entries)
        slot_support[j]=len(entries)
        slot_role[j]=role_ids[role]
        game_cv=float(np.mean(cv[j]['game'])) if cv[j]['game'] else None
        role_cv=float(np.mean(cv[j]['role'])) if cv[j]['role'] else None
        # Pool only a genuinely shared role, and only when train-only CV
        # improves raw MAE by at least three percent. Ties keep proven ridge.
        pool=(len({train_docs[i]['game_id'] for i,_ in per_role[role]})>1
              and game_cv is not None and role_cv is not None
              and role_cv<.97*game_cv-1e-10)
        use_role[j]=pool
        selection.append({'game_id':game,'register_id':role,
                          'training_examples_for_game_register':int(slot_support[j]),
                          'training_examples_for_shared_role':int(role_support[slot_role[j]]),
                          'train_cv_per_game_raw_mae':game_cv,
                          'train_cv_pooled_role_raw_mae':role_cv,
                          'selected':'pooled_role' if pool else 'per_game_ridge'})
    return {'game_coef':slot_coef,'game_bias':slot_bias,
            'role_coef':role_coef,'role_bias':role_bias,
            'slot_role':slot_role,'use_role':use_role,
            'roles':roles,'selection':selection,
            'slot_support':slot_support,'role_support':role_support,
            'alpha':alpha,'training_labels':sum(len(v) for v in per_slot.values())}


def predict_register_readouts(model,features):
    x=np.asarray(features,dtype=np.float64)
    if x.ndim!=2 or x.shape[1]!=model['game_coef'].shape[1] or not np.isfinite(x).all():
        raise ValueError('GAMER_FLAT_RIDGE_EVALUATION_FEATURE_INVALID')
    game=_predict(x,model['game_coef'].T,model['game_bias'])
    pooled=_predict(x,model['role_coef'].T,model['role_bias'])[:,model['slot_role']]
    selected=np.where(model['use_role'][None,:],pooled,game)
    if not np.isfinite(selected).all():
        raise ValueError('GAMER_FLAT_RIDGE_NONFINITE_PREDICTION')
    return selected,game,pooled
