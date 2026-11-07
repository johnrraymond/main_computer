#!/usr/bin/env python3
"""Read-only post-hoc GAMER decision thresholds, using FIT-SIDE VALIDATION ONLY.

Replays the saved balanced-repair checkpoints; no optimizer, no training or
production writes. Reconstructs the *original* train-only fold validation game
assignments including their numerical-precision exclusions. Each head learns
zero-vs-nonzero and negative-vs-positive thresholds exclusively on its own
validation rows. Reports independent unseen-game / reserved-magnitude results
and original softmax confidence diagnostics without treating softmax as truth.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from tools import nanojev_gamer_v1_balanced_repair as repair
from tools import nanojev_gamer_v1_sufficiency as suff
from tools import nanojev_gamer_v1_number_distance as distance
from tools import nanojev_gamer_v1_handoff as gamer

SCHEMA = 'nanojev.gamer.v1.validation-threshold-calibration.v1'
CLASSES = ('negative', 'zero', 'positive')


def _softmax(logits):
    x=np.asarray(logits,dtype=np.float64)
    if x.ndim!=2 or x.shape[1]!=3 or not np.isfinite(x).all():
        raise ValueError('GAMER_CALIBRATION_INVALID_LOGITS')
    s=x-np.max(x,axis=1,keepdims=True)
    exp=np.exp(s)
    return exp/exp.sum(axis=1,keepdims=True)


def _scores(logits):
    a=np.asarray(logits,dtype=np.float64)
    if a.ndim!=2 or a.shape[1]!=3:raise ValueError('GAMER_CALIBRATION_INVALID_SCORES')
    return a[:,1]-np.maximum(a[:,0],a[:,2]), a[:,0]-a[:,2]


def _decide(logits,zero_threshold,negative_threshold):
    if not np.isfinite([zero_threshold,negative_threshold]).all():
        raise ValueError('GAMER_CALIBRATION_INVALID_THRESHOLD')
    zg,sg=_scores(logits)
    # Preserve the original argmax tie convention: negative (index 0)
    # beats zero (index 1), and zero beats positive (index 2).
    zero_wins=(zg>zero_threshold)|((zg==zero_threshold)&(sg<0))
    return np.where(zero_wins,1,np.where(sg>=negative_threshold,0,2)).astype(np.int64)


def _predictions(classes,logmag):
    cc=np.asarray(classes,dtype=int)
    mm=np.asarray(logmag,dtype=np.float64)
    if cc.shape!=mm.shape or not np.isfinite(mm).all():
        raise ValueError('GAMER_CALIBRATION_INVALID_MAGNITUDES')
    size=np.maximum(np.expm1(np.clip(mm,0.,12.)),1e-6)
    return np.where(cc==1,0.,np.where(cc==0,-size,size))


def _candidate_thresholds(values):
    """Precommitted quantile grid, using only validation-side model scores."""
    x=np.asarray(values,dtype=float)
    if x.ndim!=1 or not np.isfinite(x).all() or len(x)<1:
        raise ValueError('GAMER_CALIBRATION_INVALID_VALIDATION_SCORES')
    q=np.quantile(x,[0.,.01,.05,.1,.2,.3,.4,.5,.6,.7,.8,.9,.95,.99,1.])
    fixed=np.asarray([-8.,-4.,-2.,-1.,-.5,-.2,-.05,0.,.05,.2,.5,1.,2.,4.,8.])
    # nextafter permits selecting the exact observed boundary, including ties.
    c=np.r_[fixed,q,np.nextafter(q,np.inf),np.nextafter(q,-np.inf),
             np.min(x)-1.,np.max(x)+1.]
    return np.unique(c[np.isfinite(c)])


def _rates(classes,truth):
    c=np.asarray(classes,dtype=np.int64);t=np.asarray(truth,dtype=np.int64)
    if c.shape!=t.shape or len(c)==0:raise ValueError('GAMER_CALIBRATION_BAD_RATE_INPUT')
    def recall(k):
        ix=t==k
        return float(np.mean(c[ix]==k)) if ix.any() else None
    nz=t!=1;z=t==1;predz=c==1
    return {'negative_recall':recall(0),'zero_recall':recall(1),'positive_recall':recall(2),
            'zero_precision':float(np.mean(z[predz])) if predz.any() else 0.,
            'false_zero_rate':float(np.mean(predz[nz])) if nz.any() else 0.,
            'three_class_accuracy':float(np.mean(c==t))}


def _loss_priority(m):
    """Minimax worst failing sign/zero constraint; deterministic tie breaking."""
    # Positive / negative class recall, true zero recall, true zero precision,
    # false-zero rate. Constraint values are FIXED, never inferred from DEV.
    deficits=[max(0.,.95-m['negative_recall']),
              max(0.,.95-m['positive_recall']),
              max(0.,.95-m['zero_recall']),
              max(0.,.95-m['zero_precision']),
              max(0.,(m['false_zero_rate']-.05)*3.)]
    return max(deficits),float(np.mean(deficits))


def _calibrate(logits,targets):
    """Returns selected thresholds and audited alternatives using VAL ONLY."""
    x=np.asarray(logits,dtype=np.float64)
    y=np.asarray(targets,dtype=np.float64)
    if x.shape!=(len(y),3) or not np.isfinite(y).all():
        raise ValueError('GAMER_CALIBRATION_VAL_MISMATCH')
    truth=suff._classes(y)
    counts=np.bincount(truth,minlength=3)
    if np.min(counts)==0:
        raise ValueError('GAMER_CALIBRATION_VAL_CLASS_MISSING')
    zg,sg=_scores(x)
    zc=_candidate_thresholds(zg);nc=_candidate_thresholds(sg)
    options=[]
    best=None
    for z in zc:
        for n in nc:
            cc=_decide(x,float(z),float(n))
            metrics=_rates(cc,truth)
            worst,mean=_loss_priority(metrics)
            # Prefer the small change among exactly tied validation objectives.
            key=(round(worst,12),round(mean,12),-metrics['three_class_accuracy'],
                 abs(float(z))+abs(float(n)),float(z),float(n))
            if best is None or key<best[0]:
                best=(key,float(z),float(n),metrics)
    _,zt,nt,chosen=best
    baseline=_rates(np.argmax(x,axis=1),truth)
    return {'zero_threshold':zt,'negative_threshold':nt,
       'selection':'validation_minimax_zero_precision_zero_recall_sign_recall_false_zero',
       'validation':{'cases':len(y),'class_counts':counts.tolist(),
         'raw_argmax':baseline,'selected':chosen,
         'raw_objective':_loss_priority(baseline),
         'selected_objective':_loss_priority(chosen),
         'candidate_grid':{'zero_thresholds':len(zc),'negative_thresholds':len(nc),
                           'total':len(zc)*len(nc)},
         'zero_criteria_all_pass':bool(chosen['zero_recall']>=.95 and
                   chosen['zero_precision']>=.95 and chosen['false_zero_rate']<=.05),
         'sign_criteria_all_pass':bool(chosen['negative_recall']>=.95 and chosen['positive_recall']>=.95)}}


def _reliability(logits,truth,*,bins=10):
    """Descriptive multiclass probability audit; not used for threshold fitting."""
    probs=_softmax(logits)
    t=suff._classes(truth)
    labels=np.eye(3,dtype=np.float64)[t]
    confidence=probs.max(axis=1);predicted=probs.argmax(axis=1)
    table=[];weighted=0.
    for i in range(bins):
        low=i/bins;high=(i+1)/bins
        ix=(confidence>=low)&((confidence<high) if i<bins-1 else (confidence<=high))
        if not ix.any():continue
        confidence_mean=float(np.mean(confidence[ix]));observed=float(np.mean(predicted[ix]==t[ix]))
        table.append({'low':low,'high':high,'cases':int(ix.sum()),
           'mean_max_probability':confidence_mean,'observed_accuracy':observed})
        weighted+=len(confidence[ix])/len(t)*abs(confidence_mean-observed)
    return {'cases':int(len(t)),'class_counts':np.bincount(t,minlength=3).tolist(),
      'probability_mean_by_true_class':{
         CLASSES[k]:[float(v) for v in probs[t==k].mean(axis=0)] if (t==k).any() else None
         for k in range(3)},
      'mean_softmax_probabilities':[float(x) for x in probs.mean(axis=0)],
      'brier_three_class':float(np.mean(np.sum((probs-labels)**2,axis=1))),
      'mean_negative_log_likelihood':float(-np.mean(np.log(np.maximum(probs[np.arange(len(t)),t],1e-15)))),
      'expected_calibration_error_top_class':float(weighted),
      'confidence_bins':table}


def _replay(torch,net,features,indices,positions,*,device,batch_size,vectors=100,width=1024):
    all_logits=[];all_mag=[]
    net.eval()
    with torch.inference_mode():
        for start in range(0,len(indices),batch_size):
            ids=np.asarray(indices[start:start+batch_size],dtype=int)
            xx=torch.from_numpy(suff._batch(features,ids,positions,vectors=vectors,width=width)).to(device)
            logits,mag=net(xx)
            all_logits.append(logits.detach().cpu().numpy().astype(np.float64))
            all_mag.append(mag.detach().cpu().numpy().astype(np.float64))
    if not all_logits:raise ValueError('GAMER_CALIBRATION_EMPTY_REPLAY')
    x=np.concatenate(all_logits);m=np.concatenate(all_mag)
    if not np.isfinite(x).all() or not np.isfinite(m).all():
        raise ValueError('GAMER_CALIBRATION_NONFINITE_REPLAY')
    return x,m


def _load_checkpoint(torch,path,arm,fi,positions,settings,source_checkpoint_sha,*,device,width=1024,vectors=100):
    ck=torch.load(path,map_location='cpu',weights_only=True)
    if ck.get('schema')!=suff.SCHEMA or not isinstance(ck.get('state_dict'),dict):
        raise ValueError('GAMER_CALIBRATION_BAD_DISPOSABLE_CHECKPOINT')
    meta=ck.get('metadata',{})
    if (meta.get('experiment_schema')!=repair.SCHEMA or meta.get('fold')!=fi or
        meta.get('mode')!=arm or meta.get('positions')!=positions or
        meta.get('source_checkpoint_sha256')!=source_checkpoint_sha):
        raise ValueError('GAMER_CALIBRATION_CHECKPOINT_PROVENANCE_MISMATCH')
    h=min(int(settings['hidden']),16) if positions is None else int(settings['hidden'])
    dim=width*(vectors if positions is None else len(positions))
    net=suff._model(torch,dim,h)
    net.load_state_dict(ck['state_dict'],strict=True)
    net.to(device).eval()
    return net


def _folds(train,dev,cr,prior,balanced):
    old=prior['settings'];n=int(old['folds']);seed=int(old['seed'])
    folds=suff._split_groups(train,dev,[r['game_id'] for r in cr['contexts']],folds=n,seed=seed)
    if n!=int(balanced['settings']['folds']) or seed!=int(balanced['settings']['original_split_seed']):
        raise ValueError('GAMER_CALIBRATION_PRIOR_SPLIT_MISMATCH')
    for f,ref in zip(folds,prior['holdout']['folds']):
        if f['test_games']!=ref['heldout_games'] or len(f['test'])!=ref['test_cases']:
            raise ValueError('GAMER_CALIBRATION_PRIOR_TEST_FOLD_MISMATCH')
    y=np.asarray([float(r['answer']) for r in train],dtype=np.float64)
    g=np.asarray([r['game_id'] for r in train])
    folds=[repair._filter_rounded_magnitude_boundaries(f,y) for f in folds]
    folds=[repair._ensure_validation_classes(f,y,g) for f in folds]
    for f,removed,swapped in zip(folds,balanced['float32_boundary_exclusions'],
                                 balanced['validation_stratification']):
        if (f['fold']!=removed['fold'] or f['fold']!=swapped['fold'] or
          f['float32_boundary_exclusions']['fit']!=removed['fit_indices'] or
          f['float32_boundary_exclusions']['val']!=removed['validation_indices'] or
          f['validation_class_swaps']!=swapped['swaps']):
            raise ValueError('GAMER_CALIBRATION_VALIDATION_PARTITION_CHANGED')
        if set(g[f['val']])&set(f['test_games']):
            raise ValueError('GAMER_CALIBRATION_VALIDATION_TEST_GAME_LEAK')
        if any(repair._reserved_for_fitting(v) for v in y[f['val']]):
            raise ValueError('GAMER_CALIBRATION_RESERVED_VALUE_IN_VALIDATION')
    return folds


def _score(rows):
    if not rows:return None
    y=np.asarray([r['target'] for r in rows],dtype=float)
    raw=np.asarray([r['raw_prediction'] for r in rows],dtype=float)
    after=np.asarray([r['calibrated_prediction'] for r in rows],dtype=float)
    rc=np.asarray([r['raw_class'] for r in rows],dtype=int)
    cc=np.asarray([r['calibrated_class'] for r in rows],dtype=int)
    a=repair._cell_metrics([{'target':float(v),'prediction':float(p),'class':int(c)}
                            for v,p,c in zip(y,raw,rc)])
    b=repair._cell_metrics([{'target':float(v),'prediction':float(p),'class':int(c)}
                            for v,p,c in zip(y,after,cc)])
    prob=np.asarray([[r['negative_probability'],r['zero_probability'],r['positive_probability']]
                    for r in rows],dtype=float)
    return {'raw':a,'threshold_calibrated':b,
      'delta':{'mae':b['mae']-a['mae'],
          'zero_false_positive_rate':None if a['zero_false_positive_rate'] is None else
                   b['zero_false_positive_rate']-a['zero_false_positive_rate'],
          'small_negative_sign_rate':None if a['small_negative_sign_rate'] is None else
                   b['small_negative_sign_rate']-a['small_negative_sign_rate'],
          'zero_precision':None if a['zero_precision'] is None or b['zero_precision'] is None else
                   b['zero_precision']-a['zero_precision']},
      'original_probability_audit':_reliability(np.log(np.maximum(prob,1e-15)),y)}


def _aggregate(rows,arms):
    result={}
    for arm in arms:
        armrows=[r for r in rows if r['arm']==arm]
        by_band={'all':_score(armrows)}
        for band,flag in (('eligible_magnitudes',False),('reserved_untrained_magnitudes',True)):
            by_band[band]=_score([r for r in armrows if bool(suff._reserved_numeric_range(r['target']))==flag])
        before=by_band['all']['raw'];after=by_band['all']['threshold_calibrated']
        reserve=(by_band['reserved_untrained_magnitudes'] or {}).get('threshold_calibrated')
        by_band['zero_gate_raw']=suff._zero_gate(before)
        by_band['zero_gate_after']=suff._zero_gate(after)
        ok=bool(reserve and reserve['cases']>=8 and
             reserve['zero_false_positive_rate'] is not None and reserve['zero_false_positive_rate']<=.05 and
             reserve['nonzero_sign_rate'] is not None and reserve['nonzero_sign_rate']>=.95 and reserve['within_0_1']>=.95)
        by_band['reserved_gate_after']={'passed':ok,'status':'PASS' if ok else 'BLOCKED_MAGNITUDE_EXTRAPOLATION',
            'note':'No true zeros are present in reserved magnitudes. This gate cannot certify zero recall.'}
        result[arm]=by_band
    return result


def run_calibration(run,*,output=None,device='cuda',batch_size=64):
    if not 1<=batch_size<=1024:raise ValueError('GAMER_CALIBRATION_INVALID_BATCH')
    import torch
    if device.startswith('cuda') and not torch.cuda.is_available():
        raise RuntimeError('GAMER_CALIBRATION_CUDA_UNAVAILABLE')
    root=Path(run).resolve();rp=root/'final_report.json'
    if not rp.is_file():raise FileNotFoundError('GAMER_CALIBRATION_SOURCE_REPORT_MISSING:'+str(rp))
    balanced=json.loads(rp.read_text(encoding='utf-8'))
    if balanced.get('schema')!=repair.SCHEMA or balanced.get('status')!='COMPLETE_DISPOSABLE_NOT_PROMOTED':
        raise ValueError('GAMER_CALIBRATION_WRONG_SOURCE_REPORT')
    out=distance._destination(root,output,'-calibration')
    parent=Path(balanced['source_generalization_or_sufficiency']).resolve()
    _,prior,causal_record,cr,positions,exp,source,capture,protected=repair._lineage(parent)
    if Path(balanced['source_expanded']).resolve()!=exp or Path(balanced['source_causal']).resolve()!=Path(prior['source_causal']).resolve():
        raise ValueError('GAMER_CALIBRATION_SOURCE_LINEAGE_CHANGED')
    arms=balanced['arms']; settings=balanced['settings']
    if arms!=dict((k,v) for k,v in suff._arms(positions,seed=int(settings['original_split_seed'])).items() if k in arms):
        raise ValueError('GAMER_CALIBRATION_SELECTED_ARMS_CHANGED')
    identity=json.loads((exp/'provenance.json').read_text(encoding='utf-8'))
    train,xtrain,pt=suff._cached_split(exp,source,identity,'train')
    dev,xdev,pd=suff._cached_split(exp,source,identity,'dev')
    folds=_folds(train,dev,cr,prior,balanced)
    fingerprints={**balanced['source_fingerprints'],str(rp):suff._digest(rp)}
    model_paths={}
    for fold in folds:
        for arm in arms:
            p=root/'experimental_models'/f'fold{fold["fold"]+1:02d}_{arm}.pt'
            if not p.is_file():raise FileNotFoundError('GAMER_CALIBRATION_MISSING_CHECKPOINT:'+str(p))
            fingerprints[str(p)]=suff._digest(p)
            model_paths[(fold['fold'],arm)]=p
    fingerprints[str(root/'predictions.csv')]=suff._digest(root/'predictions.csv')
    for p,digest in balanced['source_fingerprints'].items():
        if suff._digest(p)!=digest:raise ValueError('GAMER_CALIBRATION_SOURCE_FINGERPRINT_CHANGED:'+p)
    for path in [root,parent,exp,source,capture]:
        if out==path or out in path.parents or path in out.parents:
            raise ValueError('GAMER_CALIBRATION_OUTPUT_OVERLAPS_SOURCE')
    raw_original={}
    with (root/'predictions.csv').open(encoding='utf-8',newline='') as f:
        for r in csv.DictReader(f):
            if r['cohort']!='unseen':continue
            key=(r['arm'],int(r['index']))
            if key in raw_original:raise ValueError('GAMER_CALIBRATION_DUPLICATE_PRIOR_PREDICTION')
            raw_original[key]=r
    ytrain=np.asarray([float(r['answer']) for r in train],dtype=np.float64)
    ydev=np.asarray([float(r['answer']) for r in dev],dtype=np.float64)
    rows=[];calibrations={arm:[] for arm in arms};expected={arm:set() for arm in arms}
    print('GAMER VALIDATION THRESHOLD CALIBRATION: '+str(out),flush=True)
    print('  existing balanced checkpoints only; validation-game thresholds; NO TRAINING',flush=True)
    for fold in folds:
        fi=fold['fold'];test=np.asarray(fold['test'],dtype=np.int64)
        v=np.asarray(fold['val'],dtype=np.int64)
        print(f'  fold {fi+1}/{len(folds)}: validation={len(v)}, unseen={len(test)}',flush=True)
        for arm,positions in arms.items():
            net=_load_checkpoint(torch,model_paths[(fi,arm)],arm,fi,positions,settings,
               causal_record['checkpoint_sha256'],device=device)
            xval,magval=_replay(torch,net,xtrain,v,positions,device=device,batch_size=batch_size)
            calibrator=_calibrate(xval,ytrain[v])
            calibrator.update({'fold':fi,'arm':arm,'fit_side_validation_games':len(fold['val_games']),
               'validation_probability_audit':_reliability(xval,ytrain[v])})
            calibrations[arm].append(calibrator)
            xtest,magtest=_replay(torch,net,xdev,test,positions,device=device,batch_size=batch_size)
            raw=np.argmax(xtest,axis=1).astype(int)
            adjusted=_decide(xtest,calibrator['zero_threshold'],calibrator['negative_threshold'])
            pred=_predictions(raw,magtest);newpred=_predictions(adjusted,magtest)
            probs=_softmax(xtest)
            for j,i in enumerate(test):
                key=(arm,int(i))
                if key not in raw_original:raise ValueError('GAMER_CALIBRATION_MISSING_PRIOR_UNSEEN')
                original=raw_original[key]
                if (int(original['fold'])!=fi or int(original['class'])!=int(raw[j]) or
                    not math.isclose(float(original['prediction']),float(pred[j]),rel_tol=1e-4,abs_tol=5e-4)):
                    raise ValueError('GAMER_CALIBRATION_REPLAY_MISMATCH:'+str(key))
                rows.append({'arm':arm,'fold':fi,'index':int(i),'game_id':dev[int(i)]['game_id'],
                   'target':float(ydev[i]),'raw_class':int(raw[j]),
                   'calibrated_class':int(adjusted[j]),
                   'raw_prediction':float(pred[j]),'calibrated_prediction':float(newpred[j]),
                   'negative_probability':float(probs[j,0]),
                   'zero_probability':float(probs[j,1]),
                   'positive_probability':float(probs[j,2]),
                   'zero_score':float(_scores(xtest[j:j+1])[0][0]),
                   'negative_score':float(_scores(xtest[j:j+1])[1][0])})
                if int(i) in expected[arm]:raise ValueError('GAMER_CALIBRATION_DUPLICATE_TEST_CASE')
                expected[arm].add(int(i))
            print(f'    {arm}: zero threshold={calibrator["zero_threshold"]:.4f}, '
                  f'negative threshold={calibrator["negative_threshold"]:.4f}, '
                  f'validation worst deficit={calibrator["validation"]["selected_objective"][0]:.3f}',flush=True)
            del net
            if device.startswith('cuda'):torch.cuda.empty_cache()
    for arm in arms:
        if len(expected[arm])!=int(balanced['coverage']['certified_dev_cases']):
            raise ValueError('GAMER_CALIBRATION_UNSEEN_COVERAGE_CHANGED:'+arm)
    scored=_aggregate(rows,arms)
    for arm in arms:
        a=scored[arm]['all']['raw'];ref=balanced['results'][arm]['unseen_all']['unseen']['all']
        for k in ('mae','zero_exact_rate','zero_false_positive_rate','small_negative_sign_rate'):
            if not math.isclose(a[k],ref[k],rel_tol=1e-4,abs_tol=5e-4):
                raise ValueError('GAMER_CALIBRATION_PRIOR_METRIC_MISMATCH:'+arm+':'+k)
    for path,digest in fingerprints.items():
        if suff._digest(path)!=digest:raise ValueError('GAMER_CALIBRATION_SOURCE_CHANGED:'+path)
    out.mkdir(parents=True,exist_ok=False)
    with (out/'predictions.csv').open('w',newline='',encoding='utf-8') as f:
        names=list(rows[0])
        writer=csv.DictWriter(f,fieldnames=names);writer.writeheader();writer.writerows(rows)
    report={'schema':SCHEMA,'status':'COMPLETE_READ_ONLY_NOT_PROMOTED',
        'promotion':'BLOCKED_PRODUCTION_FROZEN_AND_ZERO_GATE_NOT_AUTOMATIC',
        'source_balanced_repair':str(root),'source_expanded':str(exp),
        'source_checkpoints':{f'fold{fi+1:02d}_{arm}':str(p) for (fi,arm),p in model_paths.items()},
        'source_fingerprints':fingerprints,
        'arms':arms,'settings':{'device':device,'batch_size':batch_size,
            'threshold_selection':'fixed validation-only minimax zero/sign deficit; prior fold selection fixed',
            'early_stopping_validation_reused':True,'trainable_parameters':0},
        'coverage':{'heldout_cases':len(expected[next(iter(arms))]),'folds':len(folds),
                    'reserved_magnitude_test_cases':sum(suff._reserved_numeric_range(v) for v in ydev)},
        'thresholds':calibrations,'results':scored,
        'limitations':[
         'Thresholds selected separately per fold from original FIT-SIDE VALIDATION GAMES and applied unchanged to heldout-game DEV prompts.',
         'These same validation games influenced early stopping. Thresholds are independent of DEV labels but not independent of checkpoint selection.',
         'Class probabilities are the frozen model softmax, not proven calibrated probabilities; Brier/NLL and reliability bins are descriptive.',
         'Only sign/zero decisions change. Magnitude regression values and feature representations remain exactly those of the frozen checkpoints.',
         'Reserved magnitude ranges remain completely unseen in fitting, early stopping and calibration; the reserved test has no zero answers.',
         'Fold thresholds are different operating points, not a new learned head or proof of semantic sufficiency.',
         'The heldout-game data are used ONLY for scoring after threshold selection; no model, trial or threshold is selected using heldout performance.',
         'The same fourfold dev cohort was used in prior experiments, so improvements are exploratory and not independent confirmation.',
         'No source checkpoints, source features, production C0, Qwen or LoRA files were updated.',
        ]}
    gamer.atomic_json(out/'final_report.json',report)
    for arm in arms:
        b=scored[arm]['all'];p=b['raw'];q=b['threshold_calibrated']
        print(f'  {arm}: heldout MAE={p["mae"]:.4f}->{q["mae"]:.4f}; '
              f'zero precision={p["zero_precision"]:.3f}->{q["zero_precision"]}; '
              f'false-zero={p["zero_false_positive_rate"]:.3f}->{q["zero_false_positive_rate"]:.3f}; '
              f'small-neg={p["small_negative_sign_rate"]:.3f}->{q["small_negative_sign_rate"]:.3f}; '
              f'gate={scored[arm]["zero_gate_after"]["status"]}',flush=True)
    print('GAMER VALIDATION CALIBRATION COMPLETE: '+str(out/'final_report.json'),flush=True)
    return report


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',required=True,type=Path)
    p.add_argument('--output',type=Path)
    p.add_argument('--device',default='cuda')
    p.add_argument('--batch-size',type=int,default=64)
    args=p.parse_args()
    run_calibration(args.run,output=args.output,device=args.device,batch_size=args.batch_size)
