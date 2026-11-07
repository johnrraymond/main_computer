#!/usr/bin/env python3
"""Disposable GAMER sign/zero/magnitude repair on certified frozen Qwen features.

A/B comparison against the original sufficiency lab: class-balanced sampling,
near-zero sign/zero margin, and cross-game magnitude ordering. All game-disjoint
and precommitted log2-magnitude holdouts are retained. Models/checkpoints live
ONLY in a separate lab folder and can never replace production C0 or LoRA.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from tools import nanojev_gamer_v1_generalization as gen
from tools import nanojev_gamer_v1_sufficiency as suff
from tools import nanojev_gamer_v1_number_distance as distance
from tools import nanojev_gamer_v1_handoff as gamer

SCHEMA='nanojev.gamer.v1.balanced-readout-repair.v1'


def _lineage(path):
    """Accept the completed generalization run or its exact sufficiency parent."""
    root=Path(path).resolve(); report_path=root/'final_report.json'
    if not report_path.is_file():
        raise FileNotFoundError('GAMER_BALANCED_REPAIR_REPORT_MISSING:'+str(report_path))
    report=json.loads(report_path.read_text(encoding='utf-8'))
    protected=[report_path]
    if report.get('schema')==gen.SCHEMA:
        if report.get('status')!='COMPLETE_READ_ONLY_NOT_PROMOTED':
            raise ValueError('GAMER_BALANCED_REPAIR_INVALID_GENERALIZATION_STATUS')
        for name,expect in report.get('source_sha256',{}).items():
            if suff._digest(name)!=expect:
                raise ValueError('GAMER_BALANCED_REPAIR_GENERALIZATION_SOURCE_CHANGED:'+name)
        suff_root=Path(report['source_sufficiency']).resolve()
    elif report.get('schema')==suff.SCHEMA:
        suff_root=root
    else:
        raise ValueError('GAMER_BALANCED_REPAIR_WRONG_PARENT_SCHEMA')
    verified_suff_root,prior=gen._verify_source_report(suff_root)
    causal,causal_record,cr,_,positions,exp,source,capture=suff._source(prior['source_causal'])
    if (str(exp)!=str(Path(prior['source_expanded']).resolve()) or
        list(positions)!=list(prior['selected_positions'])):
        raise ValueError('GAMER_BALANCED_REPAIR_PRIOR_LINEAGE_MISMATCH')
    protected.extend([suff_root/'final_report.json',causal/'final_report.json',
                      exp/'provenance.json',exp/'best_model.pt',
                      exp/'feature_cache'/'train.npz',exp/'feature_cache'/'dev.npz',
                      source/'certified_pairs.jsonl',capture/'activations.npz'])
    return root,prior,causal_record,cr,positions,exp,source,capture,protected



def _reserved_for_fitting(value):
    """Guard the precommitted range against rounding when labels reach float32.

    The original sufficiency split applies its holdout predicate to Python
    float64 answers, but training uses float32 targets. A result one ULP below
    e.g. 1.0 can round *into* the reserved 1..2 magnitude band. Neither
    spelling of that value may enter fitting or early stopping.
    """
    exact=float(value)
    rounded=float(np.float32(exact))
    if not math.isfinite(exact) or not math.isfinite(rounded):
        raise ValueError('GAMER_BALANCED_REPAIR_NONFINITE_TRAIN_TARGET')
    if exact!=0. and rounded==0.:
        return True  # Do not silently train a nonzero as the zero class.
    return suff._reserved_numeric_range(exact) or suff._reserved_numeric_range(rounded)


def _filter_rounded_magnitude_boundaries(fold,labels):
    """Tighten FIT/VAL only; do not reassign or inspect heldout dev cases."""
    result=dict(fold)
    excluded={}
    for cohort in ('fit','val'):
        original=np.asarray(fold[cohort],dtype=np.int64)
        reject=np.asarray([_reserved_for_fitting(labels[int(i)]) for i in original],dtype=bool)
        result[cohort]=original[~reject]
        excluded[cohort]=[int(i) for i in original[reject]]
        if len(result[cohort])==0:
            raise ValueError('GAMER_BALANCED_REPAIR_EMPTY_AFTER_PRECISION_GUARD:'+cohort)
    result['float32_boundary_exclusions']=excluded
    return result


def _balanced_batches(labels,fit_ids,*,batch_size,seed,steps,near_radius=1.,near_fraction=.5):
    """Return deterministic class-balanced draws from FIT labels alone.

    Exactly three sign classes are drawn each batch; half of each nonzero class
    is devoted to values near the zero line when that pool exists. No test or
    validation row, label, or game is consulted by this sampler.
    """
    if steps<1 or batch_size<6 or not 0<=near_fraction<=1 or near_radius<=0:
        raise ValueError('GAMER_BALANCED_REPAIR_INVALID_SAMPLER')
    ids=np.asarray(fit_ids,dtype=np.int64)
    if len(np.unique(ids))!=len(ids):
        raise ValueError('GAMER_BALANCED_REPAIR_DUPLICATE_FIT_INDEX')
    y=np.asarray(labels,dtype=np.float64)
    classes=suff._classes(y[ids]);rng=np.random.default_rng(seed)
    pools=[ids[classes==i] for i in range(3)]
    if any(len(x)==0 for x in pools):
        raise ValueError('GAMER_BALANCED_REPAIR_MISSING_SIGN_OR_ZERO_CLASS')
    near=[x[np.abs(y[x])<=near_radius] for x in pools]
    for _ in range(steps):
        counts=np.asarray([batch_size//3]*3)
        counts[:batch_size%3]+=1
        batch=[]
        for klass,n in enumerate(counts):
            nn=int(round(n*near_fraction)) if klass!=1 and len(near[klass]) else 0
            if nn:batch.extend(rng.choice(near[klass],size=nn,replace=True))
            batch.extend(rng.choice(pools[klass],size=int(n)-nn,replace=True))
        rng.shuffle(batch)
        yield np.asarray(batch,dtype=np.int64)



def _ensure_validation_classes(fold,labels,game_ids):
    """Deterministic TRAIN-only game swap if validation lacks a sign class.

    Does not touch the precommitted game-disjoint TEST fold or reserved bands.
    Every swapped game enters just one of fitting or early stopping, never both.
    """
    values=np.asarray(labels)
    games=np.asarray(game_ids)
    fit=set(map(str,fold['fit_games']));val=set(map(str,fold['val_games']))
    original=set(fit)|set(val)
    ids=np.concatenate([np.asarray(fold['fit'],dtype=int),np.asarray(fold['val'],dtype=int)])
    by_game={name:ids[games[ids]==name] for name in original}
    def counts(group):
        chosen=np.concatenate([by_game[g] for g in sorted(group)]) if group else np.array([],dtype=int)
        return np.bincount(suff._classes(values[chosen]),minlength=3),chosen
    swaps=[]
    for _ in range(4):
        vc,vi=counts(val)
        fc,fi=counts(fit)
        if (vc>0).all() and (fc>0).all():
            result=dict(fold)
            result.update({'fit':fi,'val':vi,'fit_games':sorted(fit),
                           'val_games':sorted(val),'validation_class_swaps':swaps})
            if set(result['fit'])&set(result['val']):
                raise ValueError('GAMER_BALANCED_REPAIR_STRATIFICATION_INDEX_LEAK')
            if set(result['fit_games'])&set(result['val_games']):
                raise ValueError('GAMER_BALANCED_REPAIR_STRATIFICATION_GAME_LEAK')
            return result
        missing=set(np.flatnonzero(vc==0))
        changed=False
        for add_game in sorted(fit):
            add_count=np.bincount(suff._classes(values[by_game[add_game]]),minlength=3)
            if not any(add_count[k]>0 for k in missing):continue
            for take_game in sorted(val):
                new_fit=(fit-{add_game})|{take_game}
                new_val=(val-{take_game})|{add_game}
                fc2,_=counts(new_fit);vc2,_=counts(new_val)
                if (fc2>0).all() and int((vc2==0).sum())<len(missing):
                    swaps.append({'into_validation':add_game,'into_fitting':take_game})
                    fit,val=new_fit,new_val;changed=True;break
            if changed:break
        if not changed:break
    raise ValueError('GAMER_BALANCED_REPAIR_NO_TRAIN_ONLY_THREE_CLASS_VALIDATION')


def _model(torch,dim,hidden):
    # Identical capacity to the previous sufficiency model in each arm: the
    # controlled change is training objective and sampling, not extra features.
    return suff._model(torch,dim,hidden)


def _objective(torch,scores,logmag,targets,games,*,ranking_weight=.25,
               zero_weight=.30,near_weight=.35):
    import torch.nn.functional as F
    truth=torch.as_tensor(targets,device=scores.device,dtype=torch.float32)
    classes=torch.as_tensor(suff._classes(targets),device=scores.device,dtype=torch.long)
    if not torch.isfinite(scores).all() or not torch.isfinite(logmag).all():
        raise ValueError('GAMER_BALANCED_REPAIR_NONFINITE_FORWARD')
    ce=F.cross_entropy(scores,classes)
    nz=classes!=1
    mag=(F.smooth_l1_loss(logmag[nz],torch.log1p(truth[nz].abs()))
         if bool(nz.any()) else ce*0.)
    # Penalize false zero and missed zero separately, without using a tolerance
    # snapping rule. This is a train-time margin, not a test-time label gate.
    if bool(nz.any()):
        false_zero=F.softplus(scores[nz,1]-torch.logsumexp(scores[nz][:,[0,2]],dim=1)+.35).mean()
    else:false_zero=ce*0.
    z=classes==1
    if bool(z.any()):
        missed_zero=F.softplus(torch.logsumexp(scores[z][:,[0,2]],dim=1)-scores[z,1]+.35).mean()
    else:missed_zero=ce*0.
    zero_loss=.5*(false_zero+missed_zero)
    # Target the sign discontinuity just outside zero. Near-zero negative
    # examples must beat the positive logit by a margin (and vice versa).
    smallneg=(truth<0)&(truth>=-1)
    smallpos=(truth>0)&(truth<=1)
    near_terms=[]
    if bool(smallneg.any()):
        near_terms.append(F.softplus(.5+scores[smallneg,2]-scores[smallneg,0]).mean())
    if bool(smallpos.any()):
        near_terms.append(F.softplus(.5+scores[smallpos,0]-scores[smallpos,2]).mean())
    near_loss=torch.stack(near_terms).mean() if near_terms else ce*0.
    # Magnitude order *across different games* must survive, regardless of sign.
    # The criterion uses differences in log(1+|target|), not a numeric-output
    # regression or test-label-derived pairs.
    ylog=torch.log1p(truth.abs());where=np.flatnonzero(np.asarray(targets)!=0)
    ordering=ce*0.;num_pairs=0
    if len(where)>=4:
        # Deterministic pair pattern in the shuffled balanced batch.
        left=where
        right=np.roll(where,1)
        g=np.asarray(games)
        gap=(np.asarray(targets)[left]!=np.asarray(targets)[right]) & (g[left]!=g[right])
        mag_gaps=np.abs(np.log1p(np.abs(np.asarray(targets)[left]))-
                        np.log1p(np.abs(np.asarray(targets)[right])))
        use=gap&(mag_gaps>=.15)
        if use.any():
            a=torch.as_tensor(left[use],device=scores.device,dtype=torch.long)
            b=torch.as_tensor(right[use],device=scores.device,dtype=torch.long)
            sign=torch.sign(ylog[a]-ylog[b])
            ordering=F.softplus(-2.*sign*(logmag[a]-logmag[b])).mean()
            num_pairs=int(use.sum())
    total=ce+.40*mag+ranking_weight*ordering+zero_weight*zero_loss+near_weight*near_loss
    return total,{'ce':float(ce.detach()),'magnitude':float(mag.detach()),
                  'ordering':float(ordering.detach()),'zero_margin':float(zero_loss.detach()),
                  'near_sign_margin':float(near_loss.detach()),'ranking_pairs':num_pairs}


def _fit(torch,features,labels,train_games,fit_ids,val_ids,positions,*,device,
         epochs,batch_size,hidden,lr,seed,patience=8,vectors=100,width=1024):
    if epochs<1 or patience<1 or not 0<lr<=.01 or batch_size<6:
        raise ValueError('GAMER_BALANCED_REPAIR_BAD_TRAIN_BUDGET')
    fit_ids=np.asarray(fit_ids,dtype=int);val_ids=np.asarray(val_ids,dtype=int)
    if set(fit_ids)&set(val_ids):
        raise ValueError('GAMER_BALANCED_REPAIR_FIT_VALIDATION_LEAK')
    g=np.asarray(train_games);y=np.asarray(labels,dtype=np.float64)
    if len(g)!=len(y) or len(features)!=len(y):
        raise ValueError('GAMER_BALANCED_REPAIR_TRAIN_ALIGNMENT')
    if set(g[fit_ids])&set(g[val_ids]):
        raise ValueError('GAMER_BALANCED_REPAIR_VALIDATION_GAME_LEAK')
    for subset in (fit_ids,val_ids):
        if min(np.bincount(suff._classes(y[subset]),minlength=3))==0:
            raise ValueError('GAMER_BALANCED_REPAIR_MISSING_CLASS_FIT_OR_VALIDATION')
        if any(_reserved_for_fitting(v) for v in y[subset]):
            raise ValueError('GAMER_BALANCED_REPAIR_RESERVED_MAGNITUDE_LEAK')
    dim=width*(vectors if positions is None else len(positions))
    torch.manual_seed(seed)
    net=_model(torch,dim,hidden).to(device)
    optimizer=torch.optim.AdamW(net.parameters(),lr=lr,weight_decay=1e-4)
    steps=max(1,math.ceil(len(fit_ids)/batch_size))
    train_log=[]
    best=float('inf');best_state=None;best_ep=0;stale=0
    for epoch in range(1,epochs+1):
        net.train();epoch_classes=np.zeros(3,dtype=np.int64);epoch_near=np.zeros(3,dtype=np.int64)
        pairs_seen=0
        for ids in _balanced_batches(y,fit_ids,batch_size=batch_size,
                                      seed=seed+1009*epoch,steps=steps):
            c=suff._classes(y[ids]);epoch_classes+=np.bincount(c,minlength=3)
            epoch_near+=np.bincount(c[np.abs(y[ids])<=1.],minlength=3)
            xx=torch.from_numpy(suff._batch(features,ids,positions,
                         vectors=vectors,width=width)).to(device)
            scores,logmag=net(xx)
            loss,parts=_objective(torch,scores,logmag,y[ids],g[ids])
            pairs_seen+=parts['ranking_pairs']
            if not torch.isfinite(loss):raise ValueError('GAMER_BALANCED_REPAIR_NONFINITE_LOSS')
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(net.parameters(),1.)
            optimizer.step()
        # Early stopping uses training-derived validation GAMES, never dev
        # targets, heldout games or reserved magnitude bands. Macro CE avoids
        # a positive-heavy validation game determining checkpoint selection.
        net.eval();val_losses=[];val_count=0;vce=[];vmag=[]
        with torch.inference_mode():
            for i in range(0,len(val_ids),batch_size):
                indices=val_ids[i:i+batch_size]
                xx=torch.from_numpy(suff._batch(features,indices,positions,
                            vectors=vectors,width=width)).to(device)
                scores,logmag=net(xx)
                va=torch.tensor(y[indices],device=device,dtype=torch.float32)
                vclass=torch.tensor(suff._classes(y[indices]),device=device,dtype=torch.long)
                # Accumulate per-case rather than per-batch means.
                import torch.nn.functional as F
                vce.extend(F.cross_entropy(scores,vclass,reduction='none').cpu().tolist())
                vmag.extend(F.smooth_l1_loss(logmag,torch.log1p(va.abs()),reduction='none').cpu().tolist())
        vc=suff._classes(y[val_ids]);vce=np.asarray(vce);vmag=np.asarray(vmag)
        macro_ce=float(np.mean([vce[vc==k].mean() for k in range(3)]))
        nonzero=vc!=1
        vl=macro_ce+.40*float(vmag[nonzero].mean())
        if not np.isfinite(vl):raise ValueError('GAMER_BALANCED_REPAIR_NONFINITE_VALIDATION')
        train_log.append({'epoch':epoch,'val_macro_ce':macro_ce,
              'val_nonzero_logmag_l1':float(vmag[nonzero].mean()),
              'val_loss':vl,'class_draws':epoch_classes.tolist(),
              'near_zero_draws':epoch_near.tolist(),
              'ranking_pairs':pairs_seen})
        if vl<best-1e-5:
            best=vl;best_ep=epoch;stale=0
            best_state={k:v.detach().cpu().clone() for k,v in net.state_dict().items()}
        else:
            stale+=1
            if stale>=patience:break
    if best_state is None:raise RuntimeError('GAMER_BALANCED_REPAIR_NO_MODEL')
    net.load_state_dict(best_state);net.eval()
    return net,{'epochs_executed':epoch,'best_epoch':best_ep,
         'best_validation_loss':best,'parameters':sum(p.numel() for p in net.parameters()),
         'fit_class_counts':np.bincount(suff._classes(y[fit_ids]),minlength=3).tolist(),
         'validation_class_counts':np.bincount(suff._classes(y[val_ids]),minlength=3).tolist(),
         'sampling_last_epoch':train_log[-1],'ranking_pairs_total':sum(v['ranking_pairs'] for v in train_log)},best_state


def _cell_metrics(rows):
    if not rows:return None
    yy=np.asarray([r['target'] for r in rows],dtype=float)
    pp=np.asarray([r['prediction'] for r in rows],dtype=float)
    cc=np.asarray([r['class'] for r in rows],dtype=int)
    m=suff._metrics(yy,pp,cc)
    for k in ('negative','zero','positive'):
        ix=(suff._classes(yy)=={'negative':0,'zero':1,'positive':2}[k])
        m[k+'_class_recall']=float(np.mean(cc[ix]=={'negative':0,'zero':1,'positive':2}[k])) if ix.any() else None
    m['three_class_accuracy']=float(np.mean(cc==suff._classes(yy)))
    m['always_positive_nonzero_sign_rate']=float(np.mean(yy[yy!=0]>0)) if (yy!=0).any() else None
    return m


def _paired_audit(predictions,dev,expected,arm):
    """Same dev prompt under a fit-game model and a game-disjoint model.

    A validation-game prediction NEVER substitutes for a familiar prediction.
    Fold choice is deterministic before inspecting any predictions or targets.
    """
    rows=[];unmatched=[];seen=set()
    for idx in expected:
        key=(arm,int(idx),'unseen')
        ut=[r for r in predictions if (r['arm'],r['index'],r['cohort'])==key]
        if len(ut)!=1:raise ValueError('GAMER_BALANCED_REPAIR_UNSEEN_COVERAGE_ERROR')
        unseen=ut[0]
        familiar=sorted((r for r in predictions if r['arm']==arm and r['index']==idx
             and r['cohort']=='familiar'),key=lambda r:r['fold'])
        if not familiar:
            unmatched.append(int(idx));continue
        f=familiar[0]
        for kind,r in (('familiar',f),('unseen',unseen)):
            rows.append({**r,'paired_kind':kind})
        seen.add(int(idx))
    return rows,unmatched


def _score_rows(predictions,expected,arms,dev,partitions):
    results={};rows_for_csv=[];test_set=set(map(int,expected))
    if len(test_set)!=len(expected):raise ValueError('GAMER_BALANCED_REPAIR_DUPLICATE_UNSEEN_CASE')
    for arm in arms:
        pairs,unmatched=_paired_audit(predictions,dev,expected,arm)
        # Preserve all game-unseen cases, including those without a fit-game peer.
        total=[r for r in predictions if r['arm']==arm and r['cohort']=='unseen']
        if {r['index'] for r in total}!=test_set or len(total)!=len(test_set):
            raise ValueError('GAMER_BALANCED_REPAIR_INCOMPLETE_UNSEEN_COVERAGE')
        paired_cases=set(map(int,expected))-set(unmatched)
        subset={'paired':{},'unseen_all':{},'counts':{
               'paired_cases':len(paired_cases),'unmatched_cases':len(unmatched),
               'unmatched_indices':unmatched,'all_unseen_cases':len(total)}}
        for scope in ('paired','unseen_all'):
            collection=(pairs if scope=='paired' else total)
            groups=(('familiar','unseen') if scope=='paired' else ('unseen',))
            for cohort in groups:
                group_rows=[r for r in collection if (r.get('paired_kind',r['cohort'])==cohort)]
                group_scores={}
                for band in ('eligible_magnitudes','reserved_untrained_magnitudes','all'):
                    selected=[r for r in group_rows if band=='all' or
                        bool(suff._reserved_numeric_range(r['target']))==(band=='reserved_untrained_magnitudes')]
                    group_scores[band]=_cell_metrics(selected)
                subset[scope][cohort]=group_scores
        # Single blinded heldout gate: signs/zeros across all previously unseen
        # games plus no false zeros on reserved magnitude band. Passing doesn't
        # automatically authorize modifying production C0.
        unseen=_cell_metrics(total)
        r=[r for r in total if suff._reserved_numeric_range(r['target'])]
        reserved=_cell_metrics(r)
        zero=suff._zero_gate(unseen)
        reserved_ok=bool(reserved and reserved['cases']>=8 and
            reserved['nonzero_sign_rate'] is not None and reserved['nonzero_sign_rate']>=.95 and
            reserved['zero_false_positive_rate'] is not None and reserved['zero_false_positive_rate']<=.05 and
            reserved['within_0_1']>=.95)
        subset['zero_gate']=zero
        subset['reserved_magnitude_gate']={
            'passed':reserved_ok,'status':'PASS' if reserved_ok else 'BLOCKED_MAGNITUDE_EXTRAPOLATION',
            'requirements':{'cases_min':8,'nonzero_sign_rate_min':.95,'false_zero_rate_max':.05,
                            'within_0_1_min':.95},
            'zero_cases':reserved['zero_cases'] if reserved else 0,
            'note':'Reserved bands contain no true zero targets; zero accuracy must be assessed elsewhere.'}
        results[arm]=subset
        rows_for_csv.extend(total)
        rows_for_csv.extend(r for r in pairs if r.get('paired_kind')=='familiar')
    return results,rows_for_csv


def run_balanced_repair(run,*,output=None,device='cuda',epochs=35,batch_size=64,
            hidden=64,lr=.001,patience=8,seed=47,include_full100=True):
    if not 5<=epochs<=200 or not 12<=batch_size<=256 or not 8<=hidden<=256 or \
            patience<1 or not 0<lr<=.01:
        raise ValueError('GAMER_BALANCED_REPAIR_INVALID_BUDGET')
    import torch
    if device.startswith('cuda') and not torch.cuda.is_available():
        raise RuntimeError('GAMER_BALANCED_REPAIR_CUDA_UNAVAILABLE')
    root,prior,causal_record,cr,positions,exp,source,capture,protected=_lineage(run)
    out=distance._destination(root,output,'-balanced-repair')
    for parent in (root,exp,source,capture,Path(prior['source_causal']).resolve()):
        if out==parent or out in parent.parents or parent in out.parents:
            raise ValueError('GAMER_BALANCED_REPAIR_OUTPUT_OVERLAPS_SOURCE')
    identity=json.loads((exp/'provenance.json').read_text(encoding='utf-8'))
    if identity['configuration']['input_width']!=102400:
        raise ValueError('GAMER_BALANCED_REPAIR_REQUIRES_FULL_100_VECTORS')
    train,xtrain,pt=suff._cached_split(exp,source,identity,'train')
    dev,xdev,pd=suff._cached_split(exp,source,identity,'dev')
    old_settings=prior['settings']
    if int(old_settings['folds'])!=4:
        raise ValueError('GAMER_BALANCED_REPAIR_EXPECTS_ORIGINAL_FOUR_FOLD_HOLDOUT')
    split_seed=int(old_settings['seed'])
    partitions=suff._split_groups(train,dev,[x['game_id'] for x in cr['contexts']],
                                  folds=4,seed=split_seed)
    old_folds=prior['holdout']['folds']
    if len(old_folds)!=4 or any(
            f['fold']!=o['fold'] or f['test_games']!=o['heldout_games'] or
            len(f['test'])!=o['test_cases'] for f,o in zip(partitions,old_folds)):
        raise ValueError('GAMER_BALANCED_REPAIR_ORIGINAL_TEST_GAMES_CHANGED')
    train_games_for_split=np.asarray([r['game_id'] for r in train])
    split_labels=np.asarray([float(r['answer']) for r in train])
    # Preserve the original game folds and reserved-range definition. Remove
    # only training/validation rows whose exact target becomes reserved after
    # float32 conversion; the earlier sufficiency split used float64 targets.
    partitions=[_filter_rounded_magnitude_boundaries(f,split_labels) for f in partitions]
    partitions=[_ensure_validation_classes(f,split_labels,train_games_for_split) for f in partitions]
    arms=suff._arms(positions,seed=split_seed)
    if arms!=prior['arms']:
        raise ValueError('GAMER_BALANCED_REPAIR_ORIGINAL_CONTROL_ARMS_CHANGED')
    if not include_full100:arms.pop('full100')
    fingerprints={str(p):suff._digest(p) for p in set(protected+[pt,pd])}
    ytrain=np.asarray([r['answer'] for r in train],dtype=float)
    ydev=np.asarray([r['answer'] for r in dev],dtype=float)
    games=np.asarray([r['game_id'] for r in train])
    expected=sorted(set(int(i) for fold in partitions for i in fold['test']))
    if len(expected)!=sum(len(fold['test']) for fold in partitions):
        raise ValueError('GAMER_BALANCED_REPAIR_OVERLAPPING_TEST_FOLDS')
    print('GAMER BALANCED READOUT REPAIR: '+str(out),flush=True)
    print('  frozen Qwen / LoRA / production C0; balanced sign/zero sampling; '
          'cross-game magnitude ordering; no production updates',flush=True)
    print(f'  game-disjoint folds=4, certified dev={len(expected)}, '
          f'fit+validation reserved magnitude bands excluded; epochs max={epochs}',flush=True)
    removed=sum(len(f['float32_boundary_exclusions'][k])
                for f in partitions for k in ('fit','val'))
    print(f'  float32 magnitude-boundary exclusions (train/validation only)={removed}',flush=True)
    predictions=[];histories={arm:[] for arm in arms};checkpoints=[]
    eligible_games=set(r['game_id'] for r in dev if r['game_id'] not in
         {ctx['game_id'] for ctx in cr['contexts']})
    for fold in partitions:
        fi=fold['fold']
        # The full pool of cached DEV prompts is never trained on; score a
        # familiar-game dev prompt only if its game was in training, never val.
        fit_games=set(fold['fit_games']);test_games=set(fold['test_games'])
        all_dev_ids=np.asarray([i for i,r in enumerate(dev)
                  if r['game_id'] in eligible_games and
                  (r['game_id'] in fit_games or r['game_id'] in test_games)],dtype=int)
        if not set(games[fold['fit']]).isdisjoint(test_games):
            raise ValueError('GAMER_BALANCED_REPAIR_TEST_GAME_IN_TRAIN')
        if set(games[fold['val']])&test_games:
            raise ValueError('GAMER_BALANCED_REPAIR_TEST_GAME_IN_VALIDATION')
        print(f'  fold {fi+1}/4: fit={len(fold["fit"])} val={len(fold["val"])} '
              f'unseen={len(fold["test"])}',flush=True)
        for arm,p in arms.items():
            h=min(hidden,16) if p is None else hidden
            net,meta,state=_fit(torch,xtrain,ytrain,games,fold['fit'],fold['val'],p,
                        device=device,epochs=epochs,batch_size=batch_size,hidden=h,
                        lr=lr,seed=seed+fi*193,patience=patience)
            pp,cc,probs=suff._predict(torch,net,xdev,all_dev_ids,p,
                            device=device,batch_size=batch_size)
            for i,v,c,q in zip(all_dev_ids,pp,cc,probs):
                name=dev[int(i)]['game_id']
                cohort='unseen' if name in test_games else 'familiar'
                predictions.append({'arm':arm,'fold':fi,'index':int(i),
                   'game_id':name,'cohort':cohort,'target':float(ydev[i]),
                   'prediction':float(v),'class':int(c),
                   'zero_probability':float(q[1])})
            meta.update({'fold':fi,'mode':arm,'positions':p,
                         'fit_games':len(fit_games),'validation_games':len(fold['val_games'])})
            histories[arm].append(meta)
            checkpoints.append((out/'experimental_models'/f'fold{fi+1:02d}_{arm}.pt',state,meta))
            held=[r for r in predictions if r['arm']==arm and r['fold']==fi and r['cohort']=='unseen']
            m=_cell_metrics(held)
            print(f'    {arm}: heldout MAE={m["mae"]:.4f}, zero={m["zero_exact_rate"]}, '
                  f'small-neg={m["small_negative_sign_rate"]}, best_epoch={meta["best_epoch"]}',flush=True)
            del net,state
            if device.startswith('cuda'):torch.cuda.empty_cache()
    scored,rows=_score_rows(predictions,expected,arms,dev,partitions)
    # Only choose a candidate from precommitted training/evaluation protocol;
    # report contrasts, NEVER promote a head based on this same evaluation.
    def metric(arm,cohort,band,key):
        s=scored[arm]['paired'].get(cohort,{}).get(band)
        return None if s is None else s.get(key)
    differences={}
    for arm in arms:
        differences[arm]={
          'familiar_minus_unseen_mae_eligible':None if metric(arm,'familiar','eligible_magnitudes','mae') is None else
             metric(arm,'unseen','eligible_magnitudes','mae')-metric(arm,'familiar','eligible_magnitudes','mae'),
          'familiar_minus_unseen_mae_reserved':None if metric(arm,'familiar','reserved_untrained_magnitudes','mae') is None else
             metric(arm,'unseen','reserved_untrained_magnitudes','mae')-metric(arm,'familiar','reserved_untrained_magnitudes','mae')}
    all_unseen={arm:scored[arm]['unseen_all']['unseen']['all'] for arm in arms}
    relative_mae=all_unseen['localized']['mae']-all_unseen['random_control']['mae']
    for p,sha in fingerprints.items():
        if suff._digest(p)!=sha:raise ValueError('GAMER_BALANCED_REPAIR_SOURCE_CHANGED:'+p)
    out.mkdir(parents=True,exist_ok=False)
    for file,state,meta in checkpoints:
        suff._save_state(torch,file,state,{'mode':meta['mode'],'fold':meta['fold'],
                  'positions':meta['positions'],'experiment_schema':SCHEMA,
                  'source_checkpoint_sha256':causal_record['checkpoint_sha256']})
    with (out/'predictions.csv').open('w',newline='',encoding='utf-8') as f:
        if rows:
            writer=csv.DictWriter(f,fieldnames=['arm','fold','index','game_id','cohort',
                      'target','prediction','class','zero_probability'])
            writer.writeheader();writer.writerows({k:r[k] for k in writer.fieldnames} for r in rows)
    report={'schema':SCHEMA,'status':'COMPLETE_DISPOSABLE_NOT_PROMOTED',
       'promotion':'BLOCKED_EXPERIMENT_ONLY_FROZEN_PRODUCTION_C0',
       'source_generalization_or_sufficiency':str(root),
       'source_causal':str(prior['source_causal']),'source_expanded':str(exp),
       'selected_positions':positions,'arms':arms,
       'settings':{'epochs':epochs,'batch_size':batch_size,'hidden':hidden,
                   'lr':lr,'patience':patience,'seed':seed,'folds':4,'original_split_seed':split_seed,'device':device,
                   'sampling':'balanced_neg_zero_pos_with_near_zero_emphasis',
                   'validation_coverage':'train_only_game_level_swaps_if_class_missing',
                   'objective':'3class_CE + .40 log_magnitude_L1 + .25 cross_game_order + .30 zero_margin + .35 near_sign_margin',
                   'same_as_sufficiency_capacity':True},
       'validation_stratification':[{'fold':f['fold'],'swaps':f['validation_class_swaps']} for f in partitions],
       'float32_boundary_exclusions':[{'fold':f['fold'],
          'fit_indices':f['float32_boundary_exclusions']['fit'],
          'validation_indices':f['float32_boundary_exclusions']['val'],
          'fit_count':len(f['float32_boundary_exclusions']['fit']),
          'validation_count':len(f['float32_boundary_exclusions']['val'])} for f in partitions],
       'coverage':{'scored_games':len(set(dev[i]['game_id'] for i in expected)),
            'certified_dev_cases':len(expected),
            'excluded_calibration_games':len(set(ctx['game_id'] for ctx in cr['contexts'])),
            'reserved_magnitude_definition':'nonzero floor(log2(abs(target))) modulo 4 equals 0; held out from fitting and all validation'},
       'source_fingerprints':fingerprints,'results':scored,
       'training':histories,'paired_differences':differences,
       'localized_minus_random_unseen_mae':relative_mae,
       'limitations':[
          'The experimental heads see immutable saved Qwen features, not a Qwen/LoRA update.',
          'Familiar-game evaluation uses different fit-fold models on the same DEV prompts as unseen-game evaluation; validation-only games are excluded from pairing.',
          'Numerical-range holdout excludes all nonzero values in predetermined log2 bands from training and early stopping; zero stays present in training.',
          'A reserved-band test contains no true zeros; it can only test false-zero and sign/magnitude generalization.',
          'No natural certified zero re-spelling oracle exists in these feature caches. Zero spelling invariance is NOT certified.',
          'Prior observed class imbalance and game failures motivated the precommitted objective; improvement is empirical and must be established by this new test.',
          'Fit and validation additionally exclude rows whose float64 numeric value rounds into a reserved log2 band at float32; heldout dev cases and games remain unchanged.',
          'Four-fold evaluation does not provide an untouched independent confirmation set. No head can be promoted on this report alone.',
          'Full100 arm uses a narrower first hidden layer and a different parameter count, as in the source sufficiency lab.',
          'No source files or production checkpoints are modified.'
       ]}
    gamer.atomic_json(out/'final_report.json',report)
    for arm,entry in scored.items():
        m=entry['unseen_all']['unseen']['all']
        print(f'  {arm}: unseen MAE={m["mae"]:.4f}; '
              f'zero_gate={entry["zero_gate"]["status"]}; '
              f'reserved_gate={entry["reserved_magnitude_gate"]["status"]}; '
              f'paired={entry["counts"]["paired_cases"]}/{entry["counts"]["all_unseen_cases"]}',flush=True)
    print(f'  localized minus random unseen MAE={relative_mae:.6f} (negative is better)',flush=True)
    print('GAMER BALANCED READOUT REPAIR COMPLETE: '+str(out/'final_report.json'),flush=True)
    return report


if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--run',type=Path,required=True)
    ap.add_argument('--output',type=Path)
    ap.add_argument('--device',default='cuda')
    ap.add_argument('--epochs',type=int,default=35)
    ap.add_argument('--batch-size',type=int,default=64)
    ap.add_argument('--hidden',type=int,default=64)
    ap.add_argument('--lr',type=float,default=.001)
    ap.add_argument('--patience',type=int,default=8)
    ap.add_argument('--seed',type=int,default=47)
    ap.add_argument('--no-full100',action='store_true')
    a=ap.parse_args()
    run_balanced_repair(a.run,output=a.output,device=a.device,epochs=a.epochs,
          batch_size=a.batch_size,hidden=a.hidden,lr=a.lr,patience=a.patience,
          seed=a.seed,include_full100=not a.no_full100)
