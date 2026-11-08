#!/usr/bin/env python3
"""GAMER V1 V2: frozen-zero, independent paired polarity + nonzero magnitude.

Lineage: completed polarity_magnitude_repair V1 report, frozen LoRA step 3000,
production C0, and exactly the saved cross-game zero head. V2 constructs
mathematically certified, disjoint auxiliary payoff profiles for TRAIN, DEV,
and prospective (not previously inspected) transfer evaluation. Previously
inspected hawk examples remain diagnostic only; neither hawk nor prospective
transfer examples select hyperparameters.

V2 compares identical model candidate families on 720, 792 and 936 training
cases. Magnitude fits only on nonzero targets, exp(log magnitude) is strictly
positive (and saturation is reported). Strict trivial-zero and constant
baselines, per-target-level and relative near-zero diagnostics are compulsory.

Every arm reports independent component gates and a diagnostic reconstruction,
regardless of gates; promotion always BLOCKED, even for a passing arm.

python -m tools.nanojev_gamer_v1_polarity_magnitude_v2 \\
  --repair runtime/reports/gamer-v1/c0-polarity-magnitude-0001 \\
  --output runtime/reports/gamer-v1/c0-polarity-magnitude-v2-0001 --device cuda
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

from tools import nanojev_gamer_v1_polarity_magnitude_repair as v1
from tools import nanojev_gamer_v1_dual_sign_head as dual
from tools import nanojev_gamer_v1_c0_sign_head as three
from tools import nanojev_gamer_v1_joint_sign_magnitude_lab as joint

SCHEMA = 'nanojev.gamer.v1.frozen-polarity-magnitude-v2.v1'
STEP = 3000
# Matched model families and regularization across ALL three training arms.
KINDS = ('linear', 'rbf_1.0')
REGS = (.001, .05)
PAIR_WEIGHTS = (.25, 2.0)
POL_THRESHOLDS = (.30, .40, .50, .60, .70)
TRAIN_COUNT, DEV_COUNT, FRESH_COUNT = 24, 8, 8  # payoff profiles, 6 cases each
MIN_LOG = -18.0
MAX_LOG = 5.0
MAG_TOL = .05
REL_TOL = .10
MIN_SMALL_ABS_TOL = .001


def _sha(path):
    return three._sha(path)


def _profile(index):
    """A distinct exact-rational margin crossing, with varying slope and q0.

    Payoff configurations are never copied from the ordinal catalogs. We
    separate + and - slope games and avoid duplicates in (R,S,T,P).
    """
    from tools import nanojev_two_game_spine_smoke as spine
    if not 0 <= index < TRAIN_COUNT + DEV_COUNT + FRESH_COUNT:
        raise ValueError('GAMER_V2_PROFILE_INDEX_OUT_OF_RANGE')
    k = index // 2
    s = 1 if index % 2 == 0 else -1
    a = s * (5 + (k % 7) * 3 + k // 7)
    b = -s * (7 + (k % 5) * 4 + 2 * (k // 5))
    r = Fraction(70 + 13 * index)
    reward_s = Fraction(19 + 7 * index)
    stage = spine.Stage(r, reward_s, r + a, reward_s + b)
    q0 = spine.critical_q(stage)
    if q0 is None or not 0 < q0 < 1 or stage.margin(q0) != 0:
        raise ValueError('GAMER_V2_INVALID_MARGIN_CROSSING')
    return stage, q0


def _extension_deck(base):
    """No TRAIN/DEV/FRESH/Hawk overlaps, exact triple + both surfaces."""
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    from tools import nanojev_gamer_v1_zero_sign_frob as prompts
    source = list(base)
    old_ids = {v.id for v in source}
    old_prompts = {oracle.sha_text(v.prompt) for v in source}
    extra = []
    partitions = {'transfer_train': [], 'transfer_dev': [], 'fresh_profile': []}
    profiles = {}
    seen_stage = set()
    bounds = (TRAIN_COUNT, TRAIN_COUNT + DEV_COUNT, TRAIN_COUNT + DEV_COUNT + FRESH_COUNT)
    for i in range(bounds[-1]):
        role = ('transfer_train' if i < bounds[0] else
                'transfer_dev' if i < bounds[1] else 'fresh_profile')
        stage, q0 = _profile(i)
        identity = tuple(str(v) for v in (stage.R, stage.S, stage.T, stage.P))
        if identity in seen_stage:
            raise ValueError('GAMER_V2_REUSED_PAYOFF_PROFILE')
        seen_stage.add(identity)
        eps = Fraction(1, (80, 120, 200, 300, 500, 800, 1200, 1600)[i % 8])
        group = f'{role}.payoff.{i:04d}'
        profiles[group] = {'role': role, 'payoff': identity, 'q0': str(q0),
                           'slope': str(stage.gaps()[0]-stage.gaps()[1]),
                           'profile_index': i}
        for tag, sign in (('negative', -1), ('zero', 0), ('positive', 1)):
            # For negative-slope configurations, swap the side of q0 so
            # the label is the mathematical sign, never lexical position.
            delta = (eps * sign * (1 if stage.gaps()[0] > stage.gaps()[1] else -1))
            q = q0 + delta
            if not 0 <= q <= 1:
                raise ValueError('GAMER_V2_BELIEF_OUTSIDE_UNIT_INTERVAL')
            target = stage.margin(q)
            if (target > 0) - (target < 0) != sign:
                raise ValueError('GAMER_V2_MATHEMATICAL_SIGN_CERTIFICATION_FAILED')
            for surface, render in (
                ('spine_belief', prompts._spine_belief_prompt),
                ('game_prose_belief', prompts._game_prose_belief_prompt),
            ):
                row = oracle.Case(f'{group}.{tag}.{surface}',
                                  'train' if role == 'transfer_train' else
                                  ('dev' if role == 'transfer_dev' else 'test'),
                                  group, surface, render(stage, q), target, role, delta)
                if row.id in old_ids or oracle.sha_text(row.prompt) in old_prompts:
                    raise ValueError('GAMER_V2_PRIOR_CASE_OVERLAP')
                old_ids.add(row.id)
                old_prompts.add(oracle.sha_text(row.prompt))
                extra.append(row)
                partitions[role].append(row)
    if [len(partitions[k]) for k in partitions] != [144, 48, 48]:
        raise ValueError('GAMER_V2_PROFILE_ROLE_COUNTS_CHANGED')
    oracle.validate_deck(source + extra)
    return source + extra, partitions, profiles


def _source(repair_dir):
    """Reuse the V1 verified lineage, and attest its own finished report."""
    root = Path(repair_dir).resolve()
    report_path = root / 'final_report.json'
    report = json.loads(report_path.read_text(encoding='utf-8'))
    if (report.get('schema') != v1.SCHEMA or
        report.get('status') != 'COMPLETE_INDEPENDENT_DIAGNOSTICS_NO_PRODUCTION_PROMOTION' or
        report.get('promotion') != 'BLOCKED_DIAGNOSTIC_ONLY' or
        report.get('frozen_lora_step') != STEP or
        report.get('no_production_weights_changed') is not True or
        report.get('zero_detector_unchanged') is not True or
        report.get('original_test_unchanged') is not True):
        raise ValueError('GAMER_V2_REQUIRES_COMPLETED_FROZEN_REPAIR_REPORT')
    prior = v1._source(Path(report['source_joint_report']).parent)
    if (report.get('frozen_fingerprints') != {
            'joint_report_sha256': _sha(prior['prior_path']),
            'zero_head_sha256': _sha(prior['zero_head']),
            **prior['fingerprints']} or
        report.get('frozen_lora_sha256') != prior['lineage']['origin']['source']['snapshots'][STEP][1] or
        report.get('extra_deck_sha256') != __import__(
            'tools.nanojev_gamer_v1_sign_information_probe', fromlist=['deck_hash']).deck_hash(prior['extra']) or
        report.get('hawk_profiles_in_train') != 0 or report.get('hawk_profiles_in_dev') != 0):
        raise ValueError('GAMER_V2_PRIOR_LINEAGE_OR_ZERO_CHANGED')
    deck, partitions, profiles = _extension_deck(prior['deck'])
    rows = [v.public() for v in deck]
    if rows[:len(prior['rows'])] != prior['rows']:
        raise ValueError('GAMER_V2_792_ORIGINAL_CASES_CHANGED')
    index = {r['id']: i for i, r in enumerate(rows)}
    orig_test = np.asarray([index[prior['rows'][int(i)]['id']] for i in prior['split']['test']], dtype=int)
    orig_dev = np.asarray([index[prior['rows'][int(i)]['id']] for i in prior['split']['dev']], dtype=int)
    role_idx = {role: np.asarray([index[c.id] for c in cases], dtype=int)
                for role, cases in partitions.items()}
    train720 = np.asarray([index[prior['rows'][int(i)]['id']] for i in prior['split']['train']
                           if prior['rows'][int(i)]['family'] != 'aux_hero'], dtype=int)
    train792 = np.asarray([index[prior['rows'][int(i)]['id']] for i in prior['split']['train']], dtype=int)
    if (len(train720) != 720 or len(train792) != 792 or
        any(rows[int(i)]['family']=='hawk_indifference' for i in
            np.concatenate([train792,orig_dev,role_idx['transfer_train'],role_idx['transfer_dev']])) or
        sum(rows[int(i)]['family']=='hawk_indifference' for i in orig_test) != 36 or
        len(np.unique(np.concatenate([train792,orig_dev,orig_test,*role_idx.values()]))) != len(rows)):
        raise ValueError('GAMER_V2_SPLIT_ISOLATION_FAILED')
    return {'prior':prior, 'repair':report, 'report_path':report_path,
            'rows':rows, 'deck':deck, 'profiles':profiles,
            'indices':{'train720':train720, 'train792':train792,
                       'train936':np.concatenate([train792,role_idx['transfer_train']]),
                       'dev120':np.concatenate([orig_dev,role_idx['transfer_dev']]),
                       'old_dev':orig_dev, 'old_test':orig_test,
                       'fresh':role_idx['fresh_profile'],
                       'transfer_dev':role_idx['transfer_dev'],
                       'transfer_train':role_idx['transfer_train']},
            'role_cases':partitions}


def _decode_log_magnitude(logit):
    a = np.asarray(logit, dtype=np.float64)
    if not np.isfinite(a).all():
        raise ValueError('GAMER_V2_NONFINITE_MAGNITUDE_LOGIT')
    return np.exp(np.clip(a, MIN_LOG, MAX_LOG))  # strictly > 0


def _logit_saturation(logit):
    x = np.asarray(logit, dtype=np.float64)
    return {'low': int(np.count_nonzero(x <= MIN_LOG)),
            'high': int(np.count_nonzero(x >= MAX_LOG)),
            'cases': len(x)}


def _binary_stats(y, p, threshold=.5):
    y = np.asarray(y, dtype=int); p = np.asarray(p, dtype=float)
    neg = y == -1; pos = y == 1
    if not np.any(neg) or not np.any(pos):
        raise ValueError('GAMER_V2_MISSING_POLARITY_CLASS')
    return {'negative_recall': float(np.mean(p[neg] < threshold)),
            'positive_recall': float(np.mean(p[pos] >= threshold)),
            'balanced_accuracy': float((np.mean(p[neg] < threshold) + np.mean(p[pos] >= threshold))/2),
            'threshold': float(threshold)}


def _sign_decision(p_zero, p_pos, zero_thresh, polarity_thresh):
    p_zero = np.asarray(p_zero, dtype=float)
    p_pos = np.asarray(p_pos, dtype=float)
    if p_zero.shape != p_pos.shape or not np.isfinite(p_zero).all() or not np.isfinite(p_pos).all():
        raise ValueError('GAMER_V2_BAD_SIGN_PROBABILITIES')
    klass = np.where(p_zero >= zero_thresh, 1, np.where(p_pos >= polarity_thresh, 2, 0))
    probabilities = np.column_stack(((1-p_zero)*(1-p_pos), p_zero, (1-p_zero)*p_pos))
    return klass.astype(int), probabilities


def _saved_head(path, raw, train_indices, kind, weights, bias, *, name, epoch, threshold=None):
    """Checkpoint is self-contained; scaler is calculated on TRAIN only."""
    path = Path(path)
    if path.exists():
        raise FileExistsError('GAMER_V2_HEAD_ALREADY_EXISTS')
    x = np.asarray(raw, dtype=np.float32)
    mu, std, ref, median = dual._raw_map_spec(x, {'train':np.asarray(train_indices,dtype=int)})
    payload = {'schema':np.asarray(SCHEMA), 'name':np.asarray(name),
        'kind':np.asarray(kind), 'mean':mu, 'std':std,
        'reference':ref if kind != 'linear' else np.empty((0,x.shape[1]),dtype=np.float32),
        'median_distance':np.asarray(median),
        'weights':np.asarray(weights,dtype=np.float32), 'bias':np.asarray(float(bias)),
        'epoch':np.asarray(epoch),
        'polarity_threshold':np.asarray(float(threshold) if threshold is not None else np.nan),
        'decoder':np.asarray('exp_clipped_log_magnitude_v2' if name=='magnitude' else 'sigmoid_v2')}
    path.parent.mkdir(parents=True,exist_ok=True)
    temp = path.with_suffix('.tmp.npz')
    try:
        np.savez_compressed(temp, **payload)
        os.replace(temp,path)
    finally:
        if temp.exists():temp.unlink()
    return {'path':str(path), 'sha256':_sha(path), 'schema':SCHEMA,
            'type':'DIAGNOSTIC_FROZEN_'+name.upper()+'_ONLY'}


def _replay(path, raw, name):
    with np.load(path,allow_pickle=False) as z:
        if str(z['schema']) != SCHEMA or str(z['name']) != name:
            raise ValueError('GAMER_V2_SAVED_HEAD_MISMATCH')
        spec = z['mean'],z['std'],z['reference'],float(z['median_distance'])
        mapping = dual._map_from_spec(raw,str(z['kind']),spec)
        if mapping.shape[1] != len(z['weights']):
            raise ValueError('GAMER_V2_SAVED_HEAD_SHAPE_MISMATCH')
        logit = mapping.astype(np.float64)@z['weights'].astype(np.float64)+float(z['bias'])
    return dual._sigmoid(logit) if name=='polarity' else _decode_log_magnitude(logit)


def _profile_recall(rows, idx, labels, predictions):
    ix = np.asarray(idx,dtype=int)
    y = labels[ix]
    p = np.asarray(predictions,dtype=float)[ix]
    grouped = defaultdict(list)
    for j,i in enumerate(ix):
        grouped[rows[int(i)]['group']].append(j)
    scores=[]
    for ids in grouped.values():
        indices=np.asarray(ids,dtype=int)
        if sum(y[indices]!=0)!=4:
            raise ValueError('GAMER_V2_INCOMPLETE_PROFILE_PAIR')
        scores.append(_binary_stats(y[indices],p[indices]))
    return {'profiles':len(scores), 'minimum_polarity_accuracy':min(s['balanced_accuracy'] for s in scores),
            'mean_polarity_accuracy':float(np.mean([s['balanced_accuracy'] for s in scores]))}


def _pair_order(rows,idx,p):
    ix=np.asarray(idx,dtype=int);group=defaultdict(dict)
    for i in ix:
        r=rows[int(i)]
        sign=int((Fraction(r['target'])>0)-(Fraction(r['target'])<0))
        group[(r['group'],r['surface'])][sign]=float(p[int(i)])
    if any(set(v)!=set((-1,0,1)) for v in group.values()):
        raise ValueError('GAMER_V2_INCOMPLETE_ORDERING_PAIRS')
    diffs=[v[1]-v[-1] for v in group.values()]
    return {'pairs':len(diffs), 'positive_order_fraction':float(np.mean(np.asarray(diffs)>0)),
            'mean_positive_minus_negative':float(np.mean(diffs)),
            'median_positive_minus_negative':float(np.median(diffs))}


def _fit_polarity(rows, raw, train, dev, pzero, zero_threshold, *, epochs, out, kind_reg=None):
    """Cross-profile DEV chooses pair loss, model family and global threshold."""
    y = dual._labels(rows)
    train=np.asarray(train,dtype=int);dev=np.asarray(dev,dtype=int)
    maps, descriptor = three._train_only_maps(raw,{'train':train,'dev':dev,'test':np.array([],dtype=int)})
    pairs=dual._triples(rows,train)
    best=None; options=[]
    choices=kind_reg or tuple((k,r) for k in KINDS for r in REGS)
    for kind, reg in choices:
        for weight in PAIR_WEIGHTS:
            fit=dual._binary(maps[kind][train],y[train],maps[kind][dev],y[dev],pairs,
                             task='polarity',regularization=reg,epochs=epochs,triple_weight=weight)
            pp=dual._score(fit,maps[kind][dev])
            for threshold in POL_THRESHOLDS:
                label,_=_sign_decision(pzero[dev],pp,zero_threshold,threshold)
                scored=three._metrics_by_family(rows,dev,label,np.eye(3)[label])
                fam=scored['by_family']
                min_recall=min(min(v['recall']['-1'],v['recall']['1']) for v in fam.values())
                min_triples=min(v['triple_accuracy'] for v in fam.values())
                stat=_binary_stats(y[dev],pp,threshold)
                rank=(min_recall,min_triples,stat['balanced_accuracy'],
                      scored['balanced_sign_accuracy'],-float(fit['development']['dev_nll']),
                      int(kind=='linear'),-reg,-abs(threshold-.5))
                options.append({'kind':kind,'regularization':reg,'pair_weight':weight,
                    'threshold':threshold,'dev_min_per_family_polarity_recall':min_recall,
                    'dev_min_per_family_triple_accuracy':min_triples,
                    'dev_sign':scored['balanced_sign_accuracy'],
                    'dev_polarity':stat['balanced_accuracy'],
                    'epoch':fit['development']['epoch']})
                if best is None or rank>best[0]:
                    best=(rank,kind,reg,weight,threshold,fit,scored)
    if best is None:raise ValueError('GAMER_V2_NO_POLARITY_CANDIDATE')
    _,kind,reg,weight,threshold,fit,dev_score=best
    p_all=dual._score(fit,maps[kind])
    checkpoint=_saved_head(out,raw,train,kind,fit['weights'],fit['bias'],
                           name='polarity',epoch=fit['development']['epoch'],threshold=threshold)
    if not np.allclose(p_all,_replay(out,raw,'polarity'),atol=1e-5):
        raise ValueError('GAMER_V2_POLARITY_REPLAY_FAILURE')
    return p_all,threshold,{'selected':{'kind':kind,'reg':reg,'pair_weight':weight,
        'threshold':threshold,**fit['development']},'dev':dev_score,
        'candidates':options,'checkpoint':checkpoint,
        'feature_policy':descriptor,'training_cases':len(train),
        'training_pairs':len(pairs),'selection':'TRAIN_FIT_MULTI_PROFILE_DEV_SELECTED'}


def _fit_magnitude_candidate(train_x, y_train, dev_x, y_dev, pairs, *, regularization, epochs, pair_weight):
    """Only nonzero examples contribute regression loss; paired log ratios agree."""
    import torch
    import torch.nn.functional as F
    x=np.asarray(train_x,dtype=np.float32);xd=np.asarray(dev_x,dtype=np.float32)
    y=np.asarray(y_train,dtype=np.float64);yd=np.asarray(y_dev,dtype=np.float64)
    tri=np.asarray(pairs,dtype=int)
    if (x.ndim!=2 or xd.ndim!=2 or x.shape[1]!=xd.shape[1] or
        len(x)!=len(y) or len(xd)!=len(yd) or
        np.any(y<0) or np.any(yd<0) or not np.any(y>0) or not np.any(yd>0) or
        not np.isfinite(x).all() or not np.isfinite(xd).all() or
        np.any(y[tri[:,[0,2]]]<=0) or not np.all(y[tri[:,0]]==y[tri[:,2]])):
        raise ValueError('GAMER_V2_MAGNITUDE_TRAIN_CONTRACT')
    torch.manual_seed(47)
    xx=torch.from_numpy(x);dd=torch.from_numpy(xd)
    active=torch.from_numpy(y>0)
    target=torch.from_numpy(np.log(y[y>0]).astype(np.float32))
    pair=torch.from_numpy(tri.astype(np.int64))
    w=torch.nn.Parameter(torch.zeros(x.shape[1],dtype=torch.float32))
    b=torch.nn.Parameter(torch.tensor(float(np.median(np.log(y[y>0]))),dtype=torch.float32))
    opt=torch.optim.Adam((w,b),lr=.02)
    best=None
    for epoch in range(1,epochs+1):
        opt.zero_grad();z=xx@w+b
        loss=F.smooth_l1_loss(z[active],target,beta=.35)
        if pair_weight:
            pairs_z=z[pair]
            loss=loss+pair_weight*(pairs_z[:,0]-pairs_z[:,2]).square().mean()
        loss=loss+(regularization/max(1,x.shape[1]))*w.square().sum()
        if not torch.isfinite(loss):
            raise ValueError('GAMER_V2_NONFINITE_MAGNITUDE_TRAIN_LOSS')
        loss.backward();opt.step()
        if epoch%10!=0 and epoch!=epochs:continue
        with torch.no_grad():
            pred=_decode_log_magnitude((dd@w+b).detach().numpy())
        mae=float(np.mean(np.abs(pred[yd>0]-yd[yd>0])))
        p90=float(np.quantile(np.abs(pred[yd>0]-yd[yd>0]),.9))
        tight=float(np.mean(np.abs(pred[yd>0]-yd[yd>0]) <=
                            np.minimum(MAG_TOL,np.maximum(MIN_SMALL_ABS_TOL,REL_TOL*yd[yd>0]))))
        # Nonzero-only DEV is used for magnitude selection.
        rank=(-mae,-p90,tight,-epoch)
        if best is None or rank>best[0]:
            best=(rank,w.detach().numpy().copy(),float(b.detach()),epoch,mae,p90,tight)
    return {'weights':best[1],'bias':best[2],'epoch':best[3],
            'dev_nonzero_mae':best[4],'dev_nonzero_p90':best[5],
            'dev_nonzero_tight_fraction':best[6]}


def _magnitude_stats(rows, idx, predictions, *, tolerance=MAG_TOL):
    ix=np.asarray(idx,dtype=int)
    p=np.asarray(predictions,dtype=np.float64)
    target=np.asarray([abs(float(Fraction(r['target']))) for r in rows],dtype=np.float64)
    if p.shape!=target.shape or not np.isfinite(p).all() or np.any(p<0):
        raise ValueError('GAMER_V2_BAD_MAGNITUDE_PREDICTIONS')
    def scored(indices):
        z=np.asarray(indices,dtype=int); y=target[z];pr=p[z]
        active=y>0
        if not active.any():raise ValueError('GAMER_V2_MAGNITUDE_NO_NONZERO_TARGETS')
        err=np.abs(pr[active]-y[active]);truth=y[active]
        tight=np.minimum(tolerance,np.maximum(MIN_SMALL_ABS_TOL,REL_TOL*truth))
        large=(pr[active]>=truth*.1)
        levels={}
        for v in sorted(set(float(z) for z in truth)):
            mask=np.isclose(truth,v,rtol=0,atol=1e-12)
            levels[format(v,'.9g')]={'cases':int(mask.sum()),
                'mae':float(err[mask].mean()),
                'mean_prediction':float(pr[active][mask].mean()),
                'within_0_05':float(np.mean(err[mask]<=tolerance)),
                'within_relative_tolerance':float(np.mean(err[mask]<=tight[mask]))}
        return {'cases':len(z),'nonzero_cases':int(active.sum()),
            'nonzero_mae':float(err.mean()),'nonzero_p90_abs_error':float(np.quantile(err,.9)),
            'nonzero_within_0_05':float(np.mean(err<=tolerance)),
            'nonzero_within_tight_tolerance':float(np.mean(err<=tight)),
            'nonzero_at_least_one_tenth_true_magnitude':float(large.mean()),
            'zero_case_mean_magnitude':float(pr[~active].mean()) if (~active).any() else None,
            'zero_case_count':int((~active).sum()), 'by_nonzero_magnitude_level':levels}
    families={fam:scored([i for i in ix if rows[int(i)]['family']==fam])
              for fam in sorted({rows[int(i)]['family'] for i in ix})}
    return {'overall':scored(ix), 'by_family':families,
            'small_magnitude_tolerance':'min(0.05,max(0.001,0.10*abs(target)))'}


def _baselines(rows,train,dev,eval_idx):
    y=np.asarray([abs(float(Fraction(r['target']))) for r in rows])
    candidate=np.asarray([0.,np.median(y[np.asarray(train)[y[np.asarray(train)]>0]])])
    result={}
    for name,constant in (('always_zero',candidate[0]),('train_median_constant',candidate[1])):
        pred=np.full(len(rows),float(constant),dtype=float)
        result[name]={'constant_magnitude':float(constant),
                      'dev':_magnitude_stats(rows,dev,pred),
                      'evaluation':_magnitude_stats(rows,eval_idx,pred)}
    return result


def _fit_magnitude(rows,raw,train,dev,*,epochs,out):
    train=np.asarray(train,dtype=int);dev=np.asarray(dev,dtype=int)
    targets=np.asarray([abs(float(Fraction(r['target']))) for r in rows],dtype=float)
    pairs=dual._triples(rows,train)
    maps,descriptor=three._train_only_maps(raw,{'train':train,'dev':dev,'test':np.empty(0,dtype=int)})
    best=None;options=[]
    for kind in KINDS:
        for reg in REGS:
            fit=_fit_magnitude_candidate(maps[kind][train],targets[train],maps[kind][dev],targets[dev],pairs,
                      regularization=reg,epochs=epochs,pair_weight=.25)
            mapped=maps[kind][dev].astype(np.float64)@fit['weights'].astype(np.float64)+fit['bias']
            pred=_decode_log_magnitude(mapped)
            # Both game development profile and original signed-offset dev MUST
            # be respected in the criterion. No hawk or new fresh profile labels.
            groups={fam:np.asarray([j for j,i in enumerate(dev) if rows[int(i)]['family']==fam],dtype=int)
                    for fam in sorted({rows[int(i)]['family'] for i in dev})}
            family_errors=[]
            for group in groups.values():
                active_group=group[targets[dev[group]]>0]
                if not len(active_group):
                    raise ValueError('GAMER_V2_DEV_FAMILY_MISSING_NONZERO')
                family_errors.append(float(np.mean(np.abs(pred[active_group]-targets[dev[active_group]]))))
            worst=max(family_errors)
            nz=targets[dev]>0
            mae=float(np.mean(np.abs(pred[nz]-targets[dev][nz])))
            p90=float(np.quantile(np.abs(pred[nz]-targets[dev][nz]),.9))
            rank=(-worst,-mae,-p90,int(kind=='linear'),-reg)
            options.append({'kind':kind,'regularization':reg,'epoch':fit['epoch'],
                            'dev_worst_family_nonzero_mae':worst,
                            'dev_nonzero_mae':mae,'dev_nonzero_p90':p90})
            if best is None or rank>best[0]: best=(rank,kind,reg,fit)
    _,kind,reg,fit=best
    raw_logits=maps[kind].astype(np.float64)@fit['weights'].astype(np.float64)+fit['bias']
    full=_decode_log_magnitude(raw_logits)
    checkpoint=_saved_head(out,raw,train,kind,fit['weights'],fit['bias'],
                           name='magnitude',epoch=fit['epoch'])
    if not np.allclose(full,_replay(out,raw,'magnitude'),rtol=1e-5,atol=1e-7):
        raise ValueError('GAMER_V2_MAGNITUDE_REPLAY_FAILURE')
    return full,{'selected':{'kind':kind,'reg':reg,'epoch':fit['epoch'],
        'dev_nonzero_mae':fit['dev_nonzero_mae'],
        'dev_nonzero_p90':fit['dev_nonzero_p90'],
        'dev_nonzero_tight_fraction':fit['dev_nonzero_tight_fraction']},
        'dev':_magnitude_stats(rows,dev,full),
        'candidates':options,'checkpoint':checkpoint,
        'saturation':_logit_saturation(raw_logits),
        'training_cases':len(train),'training_nonzero_cases':int(np.sum(targets[train]>0)),
        'training_pairs':len(pairs),'feature_policy':descriptor,
        'selection':'TRAIN_FIT_MULTI_PROFILE_DEV_SELECTED_NONZERO_ONLY'}



def _payoff_profile_gates(rows, idx, klass_all, magnitude, *, required=.95):
    """Prevent a good aggregate from hiding a failed fresh payoff profile."""
    ix=np.asarray(idx,dtype=int)
    cls=np.asarray(klass_all,dtype=int)
    mag=np.asarray(magnitude,dtype=float)
    actual=np.asarray([float(Fraction(r['target'])) for r in rows],dtype=float)
    groups=defaultdict(list)
    for i in ix:
        groups[rows[int(i)]['group']].append(int(i))
    entries={}
    for key,group in sorted(groups.items()):
        g=np.asarray(group,dtype=int)
        target=actual[g]
        truth=np.sign(target).astype(int)+1
        nonzero=target!=0
        if (len(g)!=6 or np.count_nonzero(nonzero)!=4 or
            np.count_nonzero(~nonzero)!=2 or
            not set(truth)=={0,1,2}):
            raise ValueError('GAMER_V2_PROFILE_GROUP_NOT_COMPLETE')
        correct=int(np.count_nonzero(cls[g]==truth))
        error=np.abs(mag[g][nonzero]-np.abs(target[nonzero]))
        tight=np.minimum(MAG_TOL,np.maximum(MIN_SMALL_ABS_TOL,REL_TOL*np.abs(target[nonzero])))
        sign_rate=correct/len(g)
        mag_rate=float(np.mean(error<=tight))
        passed=sign_rate>=required and mag_rate>=required
        entries[key]={'cases':len(g),'correct_sign':correct,
                      'sign_accuracy':sign_rate,'nonzero_tight_magnitude_fraction':mag_rate,
                      'passed':passed}
    return {'profiles':len(entries),'passed_profiles':sum(x['passed'] for x in entries.values()),
            'passed_all':all(x['passed'] for x in entries.values()),'by_profile':entries}


def _accept(sign_metrics,mag_metrics,frozen_metrics,trivial_metrics,*,constant_metrics=None,required=.95):
    reasons=[]
    for fam,metrics in sign_metrics['by_family'].items():
        for klass in ('-1','0','1'):
            if metrics['recall'][klass]<required-1e-12:
                reasons.append(f'{fam}:sign_recall_{klass}')
        if metrics['triple_accuracy']<required-1e-12:
            reasons.append(f'{fam}:complete_triples')
    for fam,m in mag_metrics['by_family'].items():
        b=frozen_metrics['by_family'][fam];c=trivial_metrics['by_family'][fam]
        for field in ('nonzero_within_0_05','nonzero_within_tight_tolerance',
                      'nonzero_at_least_one_tenth_true_magnitude'):
            if m[field]<required-1e-12:
                reasons.append(f'{fam}:{field}')
        for field in ('nonzero_mae','nonzero_p90_abs_error'):
            if not (m[field]<b[field]-1e-12 and m[field]<c[field]-1e-12):
                reasons.append(f'{fam}:{field}_not_better_than_c0_and_zero')
            if constant_metrics is not None and not m[field]<constant_metrics['by_family'][fam][field]-1e-12:
                reasons.append(f'{fam}:{field}_not_better_than_constant')
    return {'passed':not reasons,'reasons':reasons,'required':required,
            'tolerance':MAG_TOL,'promotion':'BLOCKED_DIAGNOSTIC_ONLY'}


def _diagnostic_recombine(rows,idx,klass,magnitude,frozen):
    ix=np.asarray(idx,dtype=int)
    labels=np.asarray(klass,dtype=int)
    mag=np.asarray(magnitude,dtype=float)
    original=np.asarray(frozen,dtype=float)
    true=np.asarray([float(Fraction(row['target'])) for row in rows])
    if len(labels)!=len(rows) or len(mag)!=len(rows) or len(original)!=len(rows):
        raise ValueError('GAMER_V2_RECOMBINATION_SHAPE_MISMATCH')
    reconstructed=(labels-1)*mag
    def metrics(z):
        z=np.asarray(z,dtype=int);actual=true[z];prediction=reconstructed[z]
        return {'cases':len(z),'signed_mae':float(np.mean(np.abs(actual-prediction))),
            'frozen_c0_signed_mae':float(np.mean(np.abs(actual-original[z]))),
            'oracle_sign_magnitude_mae_reference':float(np.mean(np.abs(actual-np.sign(actual)*mag[z]))),
            'complete_exact_zero':int(np.count_nonzero(prediction[actual==0]==0)),
            'true_zero_cases':int(np.count_nonzero(actual==0)),
            'false_exact_zero_on_nonzero':int(np.count_nonzero((actual!=0)&(prediction==0)))}
    fam={key:metrics([i for i in ix if rows[int(i)]['family']==key])
         for key in sorted({rows[int(i)]['family'] for i in ix})}
    return {'overall':metrics(ix),'by_family':fam,'diagnostic_only':True,
            'production_reconstruction_forbidden':True,
            'exact_zero_class_has_absolute_precedence':True}


def _capture(src,device,capture_batch,head_batch):
    """Frozen source parity + one capture of all 1,212 oracle cases."""
    import torch
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    from tools import nanojev_gamer_v1_c0_sign_causality as causal
    from tools import nanojev_gamer_v1_c0_boundary as c0
    from tools import nanojev_gamer_v1_lora_sign_stress as stress
    from tools import nanojev_gamer_v1_handoff as gamer
    origin=src['prior']['lineage']['origin']['source']
    meta=origin['meta'];snapshot,sha=origin['snapshots'][STEP]
    if stress._verify_snapshot(snapshot,STEP)!=sha:
        raise ValueError('GAMER_V2_SNAPSHOT_MUTATED_BEFORE_CAPTURE')
    net=c0._load_head(meta,device)
    original=np.asarray(origin['archive']['lora__final_flat'],dtype=np.float32)
    _,_,_,old=causal._read_stages(net,original,device=device,batch=head_batch)
    c0._verify_baseline(old,origin['archive']['production_head_predictions'])
    del net,original,old
    gc.collect()
    if torch.cuda.is_available():torch.cuda.empty_cache()
    _,tok,model=gamer.model_components(meta['lineage']['model_id'],device,adapter_dir=snapshot)
    features,_=oracle._capture_profiles(torch,model,tok,src['deck'],device=device,
        max_length=meta['lineage']['configuration']['max_length'],batch_size=capture_batch,
        curvature=meta['lineage']['configuration']['curvature'],verify_exact_final=True)
    del tok,model
    gc.collect()
    if torch.cuda.is_available():torch.cuda.empty_cache()
    final=np.asarray(features['final_mean100'],dtype=np.float32)
    flat=np.asarray(features['final_flat'],dtype=np.float16).astype(np.float32)
    del features
    net=c0._load_head(meta,device)
    _,_,_,prod=causal._read_stages(net,flat,device=device,batch=head_batch)
    del net,flat
    gc.collect()
    if torch.cuda.is_available():torch.cuda.empty_cache()
    return final,prod


def run(args):
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    from tools import nanojev_gamer_v1_lora_sign_stress as stress
    from tools import nanojev_gamer_v1_handoff as gamer
    if args.epochs<10 or args.capture_batch<1 or args.head_batch<1:
        raise ValueError('GAMER_V2_INVALID_ARGUMENTS')
    src=_source(args.repair)
    old=src['prior'];origin=old['lineage']['origin']['source'];snapshot,sha=origin['snapshots'][STEP]
    protect=[src['report_path'].parent,old['joint_dir'],old['lineage']['report_path'].parent,
        old['lineage']['origin']['root'],origin['training'],origin['source'],origin['capture'],
        origin['ckpt'].parent,origin['adapter'],origin['audit_path'].parent,
        origin['causal_path'].parent]
    out=three._output(args.output,protect)
    indices=src['indices'];rows=src['rows']
    report_frozen=_sha(src['report_path']);zero_frozen=_sha(old['zero_head'])
    hash_deck=oracle.deck_hash(src['deck'])
    print('GAMER V2: frozen LoRA step 3000; TRAIN 720/792/936, DEV 120, original TEST 108, new fresh 48',flush=True)
    print('  captured new payoff profiles: 24 train, 8 development, 8 fresh (all disjoint)',flush=True)
    final,production=_capture(src,args.device,args.capture_batch,args.head_batch)
    if len(final)!=len(rows) or len(production)!=len(rows):
        raise ValueError('GAMER_V2_CAPTURE_ROW_COUNT_MISMATCH')
    pzero,zero_threshold=v1._zero_proba(old['zero_head'],final)
    # Retain exact original-test zero head provenance. No refitting permitted.
    prior_cls,prior_prob=dual._reload(old['zero_head'],final[indices['old_test']])
    known=old['prior']['steps'][str(STEP)]['cross_game_training']['test']
    replay=three._metrics_by_family(rows,indices['old_test'],prior_cls,prior_prob)
    if (replay['complete_triples']!=known['complete_triples'] or
        abs(replay['balanced_sign_accuracy']-known['balanced_sign_accuracy'])>1e-10 or
        not np.allclose(pzero[indices['old_test']],prior_prob[:,1],atol=1e-8)):
        raise ValueError('GAMER_V2_UNCHANGED_SIGN_HEAD_PARITY_FAILURE')
    out.mkdir(parents=True,exist_ok=False)
    result={'schema':SCHEMA,'status':'INCOMPLETE','promotion':'BLOCKED_DIAGNOSTIC_ONLY',
        'frozen_lora_step':STEP,'frozen_lora_sha256':sha,
        'frozen_c0_checkpoint_sha256':old['prior']['fingerprints']['production_c0'],
        'source_repair_report':str(src['report_path']),
        'source_repair_report_sha256':report_frozen,'zero_head_sha256':zero_frozen,
        'combined_deck_sha256':hash_deck,
        'role_deck_hashes':{k:oracle.deck_hash(v) for k,v in src['role_cases'].items()},
        'original_72_dev_108_test_unchanged':True,'fresh_holdout_not_used_in_selection':True,
        'hawk_reused_for_diagnostic_only':True,
        'fresh_blind_is_prospective_within_generated_profile_class_not_external_proof':True,
        'no_production_model_training':True,'zero_head_unchanged':True,
        'settings':{'epochs':args.epochs,'model_kinds':KINDS,'regularization':REGS,
            'paired_polarity_weights':PAIR_WEIGHTS,'polarity_threshold_grid':POL_THRESHOLDS,
            'magnitude_decoder':'exp_clipped_log_magnitude_v2',
            'training_magnitude_only_nonzero':True,'test_tolerance':MAG_TOL},
        'profiles':src['profiles'], 'arms':{}}
    baseline=np.abs(production)
    old_test=indices['old_test'];fresh=indices['fresh'];dev=indices['dev120']
    test_baseline=_magnitude_stats(rows,old_test,baseline)
    fresh_baseline=_magnitude_stats(rows,fresh,baseline)
    result['frozen_c0_magnitude']={'original_test':test_baseline,'fresh':fresh_baseline}
    for name,train in (('original_720_control',indices['train720']),
                       ('hero_792_control',indices['train792']),
                       ('diverse_936_extension',indices['train936'])):
        print(f'  V2 arm {name}: {len(train)} TRAIN, frozen zero',flush=True)
        pp,threshold,pol=_fit_polarity(rows,final,train,dev,pzero,zero_threshold,
                                      epochs=args.epochs,out=out/f'{name}-polarity.npz')
        magnitude,mag=_fit_magnitude(rows,final,train,dev,epochs=args.epochs,
                                     out=out/f'{name}-magnitude.npz')
        scores={}
        for partition,eval_idx in (('original_test',old_test),('fresh',fresh)):
            sign,prob=_sign_decision(pzero[eval_idx],pp[eval_idx],zero_threshold,threshold)
            all_classes=np.ones(len(rows),dtype=int);all_classes[eval_idx]=sign
            sign_test=three._metrics_by_family(rows,eval_idx,sign,prob)
            mag_test=_magnitude_stats(rows,eval_idx,magnitude)
            references=test_baseline if partition=='original_test' else fresh_baseline
            trivial=_magnitude_stats(rows,eval_idx,np.zeros(len(rows)))
            baselines=_baselines(rows,train,dev,eval_idx)
            acceptance=_accept(sign_test,mag_test,references,trivial,
                constant_metrics=baselines['train_median_constant']['evaluation'])
            individual=_payoff_profile_gates(rows,eval_idx,all_classes,magnitude)
            if partition=='fresh' and not individual['passed_all']:
                acceptance['passed']=False
                acceptance['reasons'].append('fresh_profile:individual_payoff_profile_gates')
            scores[partition]={'sign':sign_test,'individual_payoff_profiles':individual,
                'independent_binary':dual._conditional_test_metrics(
                rows,eval_idx,pzero[eval_idx],pp[eval_idx],zero_threshold),
                'polarity':{'overall':_binary_stats(dual._labels(rows)[eval_idx],pp[eval_idx],threshold),
                            'paired_order':_pair_order(rows,eval_idx,pp)},
                'magnitude':mag_test,'trivial_zero_magnitude':trivial,
                'constant_baselines':baselines,
                'acceptance':acceptance,
                'diagnostic_reconstruction':_diagnostic_recombine(rows,eval_idx,all_classes,magnitude,production)}
        result['arms'][name]={'training_cases':len(train),'polarity_head':pol,
                             'magnitude_head':mag,'scores':scores}
        gamer.atomic_json(out/f'{name}.json',result['arms'][name])
        hawk=scores['original_test']['sign']['by_family']['hawk_indifference']
        print('    hawk - recall %.3f, 0 recall %.3f, triples %d/12; mag hawk nonzero MAE %.4f; fresh sign %.3f' % (
            hawk['recall']['-1'],hawk['recall']['0'],hawk['complete_triples'],
            scores['original_test']['magnitude']['by_family']['hawk_indifference']['nonzero_mae'],
            scores['fresh']['sign']['balanced_sign_accuracy']),flush=True)
    if (stress._verify_snapshot(snapshot,STEP)!=sha or _sha(src['report_path'])!=report_frozen or
        _sha(old['zero_head'])!=zero_frozen or
        _sha(origin['ckpt'])!=old['prior']['fingerprints']['production_c0']):
        raise ValueError('GAMER_V2_SOURCE_OR_FROZEN_WEIGHT_CHANGED_DURING_RUN')
    result['status']='COMPLETE_FROZEN_DIAGNOSTICS_NO_PROMOTION'
    result['all_arms_promotion_blocked']=True
    result['interpretation']='Fresh profiles are newly generated and excluded from training/selection but share the programmatic payoff construction; no external blind certification. Hawk has been repeatedly inspected and is a regression diagnostic. Zero detector, LoRA, and production C0 are frozen. No test-based candidate selection and no promotion.'
    gamer.atomic_json(out/'final_report.json',result)
    print('GAMER V2 COMPLETE:',out/'final_report.json',flush=True)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repair',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--device',choices=('cpu','cuda','directml'),default='cuda')
    p.add_argument('--capture-batch',type=int,default=2)
    p.add_argument('--head-batch',type=int,default=12)
    p.add_argument('--epochs',type=int,default=120)
    return run(p.parse_args(argv))

if __name__=='__main__': main()
