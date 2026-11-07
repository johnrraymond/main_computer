#!/usr/bin/env python3
"""Read-only probability separability and numerical-error attribution for GAMER.

Consumes the *saved*, independently scored balanced-head calibration CSV.  No
checkpoint replay, CUDA, gradients, fitting, or heldout-label threshold search.
Reports rank separability independent of the decision threshold, class-conditional
probability distributions, clustered uncertainty, and exact MAE attribution.
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import numpy as np

from tools import nanojev_gamer_v1_threshold_calibration as cal
from tools import nanojev_gamer_v1_sufficiency as suff
from tools import nanojev_gamer_v1_number_distance as distance
from tools import nanojev_gamer_v1_handoff as gamer

SCHEMA='nanojev.gamer.v1.class-probability-separability.v1'
CLASSES=('negative','zero','positive')
RANGES=('all','eligible_magnitudes','reserved_untrained_magnitudes')


def _truth(y):
    v=float(y)
    if not math.isfinite(v):raise ValueError('GAMER_SEPARABILITY_INVALID_TARGET')
    return 0 if v<0 else (2 if v>0 else 1)


def _distribution(v):
    x=np.asarray(v,dtype=np.float64)
    if not len(x):return None
    if not np.isfinite(x).all():raise ValueError('GAMER_SEPARABILITY_NONFINITE_DISTRIBUTION')
    q=np.quantile(x,[0,.05,.1,.25,.5,.75,.9,.95,1])
    return {'cases':int(len(x)),'mean':float(x.mean()),'std':float(x.std()),
       'minimum':float(q[0]),'p05':float(q[1]),'p10':float(q[2]),
       'p25':float(q[3]),'median':float(q[4]),'p75':float(q[5]),
       'p90':float(q[6]),'p95':float(q[7]),'maximum':float(q[8])}


def _auc(scores,positive):
    """Mann-Whitney AUROC with exact tie averaging; no fitted cutoffs."""
    x=np.asarray(scores,dtype=np.float64)
    mask=np.asarray(positive,dtype=bool)
    if x.ndim!=1 or x.shape!=mask.shape or not np.isfinite(x).all():
        raise ValueError('GAMER_SEPARABILITY_INVALID_AUC_INPUT')
    n1=int(mask.sum());n0=len(mask)-n1
    if n1==0 or n0==0:return None
    order=np.argsort(x,kind='stable');sorted_x=x[order]
    ranks=np.empty(len(x),dtype=np.float64)
    start=0
    while start<len(x):
        end=start+1
        while end<len(x) and sorted_x[end]==sorted_x[start]:end+=1
        ranks[order[start:end]]=(start+1+end)/2.
        start=end
    return float((ranks[mask].sum()-n1*(n1+1)/2)/(n1*n0))


def _average_precision(scores,positive):
    x=np.asarray(scores,dtype=np.float64)
    mask=np.asarray(positive,dtype=bool)
    if x.shape!=mask.shape or not np.isfinite(x).all():
        raise ValueError('GAMER_SEPARABILITY_INVALID_AP_INPUT')
    positives=int(mask.sum())
    if not positives or positives==len(mask):return None
    order=np.argsort(-x,kind='stable')
    ranked=x[order];y=mask[order]
    total=0;tp=0;ap=0.;i=0
    while i<len(x):
        j=i+1
        while j<len(x) and ranked[j]==ranked[i]:j+=1
        tp+=int(y[i:j].sum());total=j
        ap+=float(y[i:j].sum())/positives * tp/total
        i=j
    return float(ap)


def _separation(scores,positives):
    x=np.asarray(scores,dtype=np.float64);positive=np.asarray(positives,dtype=bool)
    if len(x)!=len(positive):raise ValueError('GAMER_SEPARABILITY_LENGTH_MISMATCH')
    p=int(positive.sum());n=len(positive)-p
    return {'cases':int(len(x)),'positive_cases':p,'negative_cases':n,
      'positive_prevalence':float(p/len(x)) if len(x) else None,
      'status':'COMPLETE' if p and n else 'UNIDENTIFIABLE_SINGLE_CLASS',
      'auc':_auc(x,positive) if len(x) else None,
      'average_precision':_average_precision(x,positive) if len(x) else None,
      'positive_scores':_distribution(x[positive]),
      'negative_scores':_distribution(x[~positive])}


def _bucket(y,p,c):
    """Disjoint exact decomposition of absolute numerical error."""
    if y==0 and c==1:return 'correct_zero'
    if y==0:return 'missed_zero_nonzero_prediction'
    if c==1:return 'false_zero_nonzero_target'
    if (y<0)==(c==0):return 'correct_sign_magnitude_error'
    return 'wrong_sign_nonzero_prediction'


def _attribution(rows,kind):
    groups={k:{'cases':0,'total_absolute_error':0.,'mean_absolute_error':None}
       for k in ('correct_zero','missed_zero_nonzero_prediction',
                'false_zero_nonzero_target','correct_sign_magnitude_error',
                'wrong_sign_nonzero_prediction')}
    if not rows:return {'cases':0,'mae':None,'groups':groups}
    total=0.
    for row in rows:
        y=row['target'];p=row[kind+'_prediction'];c=row[kind+'_class']
        bucket=_bucket(y,p,c)
        error=abs(y-p)
        # Wrong-sign error = |target|+|prediction|, and correct-sign error
        # = ||target|-|prediction||, by construction.
        if bucket=='wrong_sign_nonzero_prediction' and not math.isclose(error,abs(y)+abs(p),abs_tol=1e-7):
            raise ValueError('GAMER_SEPARABILITY_SIGN_ERROR_DECOMPOSITION')
        if bucket=='correct_sign_magnitude_error' and not math.isclose(error,abs(abs(y)-abs(p)),abs_tol=1e-7):
            raise ValueError('GAMER_SEPARABILITY_MAG_ERROR_DECOMPOSITION')
        groups[bucket]['cases']+=1
        groups[bucket]['total_absolute_error']+=error
        total+=error
    for g in groups.values():
        g['mean_absolute_error']=g['total_absolute_error']/g['cases'] if g['cases'] else None
        g['contribution_to_overall_mae']=g['total_absolute_error']/len(rows)
        g['fraction_total_absolute_error']=g['total_absolute_error']/total if total else None
    if not math.isclose(sum(g['total_absolute_error'] for g in groups.values()),total,abs_tol=1e-6):
        raise ValueError('GAMER_SEPARABILITY_ERROR_BUDGET_MISMATCH')
    return {'cases':len(rows),'mae':float(total/len(rows)),'groups':groups}


def _features(rows):
    prob=np.asarray([[r['negative_probability'],r['zero_probability'],r['positive_probability']]
                     for r in rows],dtype=np.float64)
    truth=np.asarray([_truth(r['target']) for r in rows],dtype=np.int64)
    return prob,truth


def _one_segment(rows):
    if not rows:return {'cases':0,'status':'NO_CASES'}
    probs,t=_features(rows)
    raw=np.asarray([r['raw_class'] for r in rows],dtype=np.int64)
    adjusted=np.asarray([r['calibrated_class'] for r in rows],dtype=np.int64)
    by_class={}
    for k,name in enumerate(CLASSES):
        ix=t==k
        by_class[name]={'cases':int(ix.sum()),'probabilities':{
              CLASSES[j]:_distribution(probs[ix,j]) for j in range(3)},
          'raw_predictions':{CLASSES[j]:int(np.sum(raw[ix]==j)) for j in range(3)},
          'calibrated_predictions':{CLASSES[j]:int(np.sum(adjusted[ix]==j)) for j in range(3)}}
    zero=(t==1);nz=t!=1
    # Conditional sign score is a monotonic logistic transform of the saved
    # negative-versus-positive logit margin, robust to softmax underflow.
    sm=np.asarray([r['negative_score'] for r in rows],dtype=float)
    sign=1./(1.+np.exp(-np.clip(sm[nz],-700.,700.))) if np.any(nz) else np.asarray([])
    pair={
      'zero_vs_nonzero':_separation(probs[:,1],zero),
      'zero_decision_margin_vs_nonzero':_separation(
          np.asarray([r['zero_score'] for r in rows],dtype=float),zero),
      'negative_vs_positive_conditional_true_nonzero':_separation(sign,t[nz]==0),
      'negative_decision_margin_vs_positive_conditional_true_nonzero':_separation(
          np.asarray([r['negative_score'] for r in rows if _truth(r['target'])!=1],dtype=float),t[nz]==0),
    }
    for k,name in enumerate(CLASSES):
        pair[name+'_vs_rest']=_separation(probs[:,k],t==k)
    for one,two in ((0,1),(2,1)):
        mask=(t==one)|(t==two)
        s=probs[mask,one]/np.maximum(probs[mask,one]+probs[mask,two],1e-300)
        pair[CLASSES[one]+'_vs_'+CLASSES[two]]=_separation(s,t[mask]==one)
    r0=cal._rates(raw,t);r1=cal._rates(adjusted,t)
    return {'cases':len(rows),'status':'COMPLETE','true_class_counts':{
             name:int((t==k).sum()) for k,name in enumerate(CLASSES)},
      'by_true_class':by_class,'separation':pair,
      'decisions':{'raw':r0,'calibrated':r1},
      'error_attribution':{'raw':_attribution(rows,'raw'),
                           'calibrated':_attribution(rows,'calibrated')}}


def _subsets(rows):
    return {'all':rows,
       'eligible_magnitudes':[r for r in rows if not r['reserved']],
       'reserved_untrained_magnitudes':[r for r in rows if r['reserved']]}


def _bootstrap_auc(rows,task,*,reps,seed):
    if reps<0 or reps>10000:raise ValueError('GAMER_SEPARABILITY_INVALID_REPS')
    if not rows:return None
    games=sorted({r['game_id'] for r in rows})
    if len(games)<2:return None
    by_game={g:[r for r in rows if r['game_id']==g] for g in games}
    rng=np.random.default_rng(seed)
    au=[]
    for _ in range(reps):
        selected=rng.integers(0,len(games),size=len(games))
        sample=[r for i in selected for r in by_game[games[int(i)]]]
        v=_auc_task(sample,task)
        if v is not None:au.append(v)
    if not au:return {'status':'UNIDENTIFIABLE_BOOTSTRAP','replicates_valid':0}
    return {'status':'COMPLETE' if len(au)>=max(1,int(.8*reps)) else 'LIMITED_CLASS_COVERAGE',
       'replicates_requested':reps,'replicates_valid':len(au),
       'p025':float(np.quantile(au,.025)),'median':float(np.median(au)),
       'p975':float(np.quantile(au,.975)),
       'note':'Game-cluster resampling; exploratory confidence interval, not a class-sufficiency proof.'}


def _auc_task(rows,task):
    if not rows:return None
    p,t=_features(rows)
    if task=='zero_vs_nonzero':return _auc(p[:,1],t==1)
    if task=='negative_vs_positive_conditional_true_nonzero':
        nz=t!=1
        return _auc(p[nz,0]/(p[nz,0]+p[nz,2]),t[nz]==0)
    raise ValueError('GAMER_SEPARABILITY_INVALID_TASK')


def _read_source(root,report):
    if report.get('schema')!=cal.SCHEMA or report.get('status')!='COMPLETE_READ_ONLY_NOT_PROMOTED':
        raise ValueError('GAMER_SEPARABILITY_WRONG_SOURCE_REPORT')
    filepath=root/'predictions.csv'
    if not filepath.is_file():raise FileNotFoundError('GAMER_SEPARABILITY_CALIBRATION_CSV_MISSING:'+str(filepath))
    names={'arm','fold','index','game_id','target','raw_class','calibrated_class',
           'raw_prediction','calibrated_prediction','negative_probability','zero_probability',
           'positive_probability','zero_score','negative_score'}
    arms=report['arms'];thresholds={}
    for arm in arms:
        fold_data=report['thresholds'][arm]
        for record in fold_data:
            key=(arm,int(record['fold']))
            if key in thresholds:raise ValueError('GAMER_SEPARABILITY_DUPLICATE_THRESHOLD')
            thresholds[key]=(float(record['zero_threshold']),float(record['negative_threshold']))
    expected_arm_cases=int(report['coverage']['heldout_cases'])
    seen=set();rows=[];by_arm={a:set() for a in arms}
    with filepath.open('r',newline='',encoding='utf-8') as stream:
        reader=csv.DictReader(stream)
        if not names.issubset(set(reader.fieldnames or [])):
            raise ValueError('GAMER_SEPARABILITY_SOURCE_COLUMNS_MISSING')
        for raw in reader:
            arm=raw['arm'];fi=int(raw['fold']);idx=int(raw['index'])
            if arm not in arms or (arm,fi) not in thresholds:
                raise ValueError('GAMER_SEPARABILITY_UNKNOWN_ARM_OR_FOLD')
            if (arm,idx) in seen:raise ValueError('GAMER_SEPARABILITY_DUPLICATE_CASE')
            seen.add((arm,idx));by_arm[arm].add(idx)
            row={'arm':arm,'fold':fi,'index':idx,'game_id':raw['game_id'],
                 'target':float(raw['target']),
                 'raw_class':int(raw['raw_class']),
                 'calibrated_class':int(raw['calibrated_class']),
                 'raw_prediction':float(raw['raw_prediction']),
                 'calibrated_prediction':float(raw['calibrated_prediction']),
                 'negative_probability':float(raw['negative_probability']),
                 'zero_probability':float(raw['zero_probability']),
                 'positive_probability':float(raw['positive_probability']),
                 'zero_score':float(raw['zero_score']),
                 'negative_score':float(raw['negative_score'])}
            if not row['game_id']:raise ValueError('GAMER_SEPARABILITY_MISSING_GAME')
            numeric=[v for k,v in row.items() if isinstance(v,float)]
            if not np.isfinite(numeric).all():raise ValueError('GAMER_SEPARABILITY_NONFINITE_ROW')
            p=np.asarray([row[x+'_probability'] for x in CLASSES])
            if np.any(p<0) or np.any(p>1) or not np.isclose(p.sum(),1,atol=1e-7):
                raise ValueError('GAMER_SEPARABILITY_BAD_PROBABILITIES')
            z=row['zero_score'];s=row['negative_score'];zt,nt=thresholds[(arm,fi)]
            def predicted(zthr,sthr):
                iszero=z>zthr or (z==zthr and s<0.)
                return 1 if iszero else (0 if s>=sthr else 2)
            if row['raw_class']!=predicted(0.,0.) or row['calibrated_class']!=predicted(zt,nt):
                raise ValueError('GAMER_SEPARABILITY_PREDICTION_THRESHOLD_MISMATCH')
            for label in ('raw','calibrated'):
                c=row[label+'_class'];v=row[label+'_prediction']
                if c not in (0,1,2) or (c==1 and v!=0) or (c==0 and not v<0) or (c==2 and not v>0):
                    raise ValueError('GAMER_SEPARABILITY_INVALID_NUMERICAL_PREDICTION')
            if (row['raw_class']!=1 and row['calibrated_class']!=1 and
                not math.isclose(abs(row['raw_prediction']),abs(row['calibrated_prediction']),abs_tol=5e-4)):
                raise ValueError('GAMER_SEPARABILITY_CHANGED_MAGNITUDE_BRANCH')
            row['reserved']=bool(suff._reserved_numeric_range(row['target']))
            rows.append(row)
    for arm in arms:
        if len(by_arm[arm])!=expected_arm_cases:
            raise ValueError('GAMER_SEPARABILITY_ARM_COVERAGE_CHANGED')
    anchor=next(iter(arms))
    cases_by_arm={a:{r['index']:(r['target'],r['game_id'],r['fold']) for r in rows if r['arm']==a} for a in arms}
    for arm in arms:
        if cases_by_arm[arm]!=cases_by_arm[anchor]:
            raise ValueError('GAMER_SEPARABILITY_ARMS_NOT_PAIRED')
    # Validate that the loaded CSV reproduces the released calibration aggregate.
    prior=cal._aggregate(rows,arms)
    for arm in arms:
        for decision in ('raw','threshold_calibrated'):
            a=prior[arm]['all'][decision]
            b=report['results'][arm]['all'][decision]
            for metric in ('mae','zero_precision','zero_exact_rate','zero_false_positive_rate',
                           'small_negative_sign_rate'):
                if a[metric] is None or b[metric] is None:
                    if a[metric]!=b[metric]:raise ValueError('GAMER_SEPARABILITY_PRIOR_METRIC_MISMATCH')
                elif not math.isclose(a[metric],b[metric],rel_tol=1e-5,abs_tol=1e-4):
                    raise ValueError('GAMER_SEPARABILITY_PRIOR_METRIC_MISMATCH:'+arm+':'+metric)
    return rows


def run_separability(run,*,output=None,bootstraps=199,seed=47):
    if not 0<=bootstraps<=10000:raise ValueError('GAMER_SEPARABILITY_INVALID_REPS')
    root=Path(run).resolve();rp=root/'final_report.json';csv_path=root/'predictions.csv'
    if not rp.is_file():raise FileNotFoundError('GAMER_SEPARABILITY_REPORT_MISSING:'+str(rp))
    report=json.loads(rp.read_text(encoding='utf-8'))
    if report.get('schema')!=cal.SCHEMA or report.get('status')!='COMPLETE_READ_ONLY_NOT_PROMOTED':
        raise ValueError('GAMER_SEPARABILITY_WRONG_SOURCE_REPORT')
    out=distance._destination(root,output,'-separability')
    protected=[root]
    for key in ('source_balanced_repair','source_expanded'):
        if report.get(key):protected.append(Path(report[key]).resolve())
    for path in protected:
        if out==path or out in path.parents or path in out.parents:
            raise ValueError('GAMER_SEPARABILITY_OUTPUT_OVERLAPS_SOURCE')
    fingerprints={str(p):suff._digest(p) for p in (rp,csv_path)}
    rows=_read_source(root,report);arms=list(report['arms'])
    print('GAMER CLASS PROBABILITY SEPARABILITY: '+str(out),flush=True)
    print('  saved calibration CSV only; AUC distributions; exact numerical-error decomposition; NO TRAINING',flush=True)
    evaluations={};distributions=[];attributions=[];histograms=[];worst=[]
    for arm in arms:
        armrows=[r for r in rows if r['arm']==arm]
        cohorts=_subsets(armrows)
        evaluations[arm]={}
        evaluations[arm]['by_fold']={}
        for fi in sorted({r['fold'] for r in armrows}):
            subset=[r for r in armrows if r['fold']==fi]
            part=_one_segment(subset)
            evaluations[arm]['by_fold'][str(fi)]={
                'cases':len(subset),
                'zero_auc':part['separation']['zero_vs_nonzero']['auc'],
                'zero_margin_auc':part['separation']['zero_decision_margin_vs_nonzero']['auc'],
                'sign_auc':part['separation']['negative_vs_positive_conditional_true_nonzero']['auc'],
                'negative_recall_raw':part['decisions']['raw']['negative_recall'],
                'negative_recall_calibrated':part['decisions']['calibrated']['negative_recall'],
                'zero_precision_raw':part['decisions']['raw']['zero_precision'],
                'zero_precision_calibrated':part['decisions']['calibrated']['zero_precision'],
                'raw_mae':part['error_attribution']['raw']['mae'],
                'calibrated_mae':part['error_attribution']['calibrated']['mae'],
            }
        for name,subset in cohorts.items():
            result=_one_segment(subset)
            if name=='all' and subset:
                result['game_cluster_bootstrap']={task:_bootstrap_auc(subset,task,
                       reps=bootstraps,seed=seed+i) for i,task in enumerate((
                      'zero_vs_nonzero','negative_vs_positive_conditional_true_nonzero'))}
            evaluations[arm][name]=result
            if not subset:continue
            for trueclass,entry in result['by_true_class'].items():
                for probability,metrics in entry['probabilities'].items():
                    if metrics:
                        distributions.append({'arm':arm,'cohort':name,'true_class':trueclass,
                          'probability':probability,**metrics})
                        values=[r[probability+'_probability'] for r in subset
                            if CLASSES[_truth(r['target'])]==trueclass]
                        counts,_=np.histogram(values,bins=np.linspace(0.,1.,11))
                        for bucket,n in enumerate(counts):
                            histograms.append({'arm':arm,'cohort':name,'true_class':trueclass,
                              'probability':probability,'bin_low':bucket/10.,
                              'bin_high':(bucket+1)/10.,'cases':int(n)})
            for decision,data in result['error_attribution'].items():
                for k,g in data['groups'].items():
                    attributions.append({'arm':arm,'cohort':name,'decision':decision,
                                         'error_type':k,**g})
        worst.extend(sorted([{'arm':arm,'fold':r['fold'],'game_id':r['game_id'],
             'index':r['index'],'target':r['target'],
             'raw_prediction':r['raw_prediction'],
             'calibrated_prediction':r['calibrated_prediction'],
             'raw_absolute_error':abs(r['target']-r['raw_prediction']),
             'calibrated_absolute_error':abs(r['target']-r['calibrated_prediction']),
             'raw_failure_kind':_bucket(r['target'],r['raw_prediction'],r['raw_class']),
             'calibrated_failure_kind':_bucket(r['target'],r['calibrated_prediction'],r['calibrated_class']),
             'negative_probability':r['negative_probability'],
             'zero_probability':r['zero_probability'],
             'positive_probability':r['positive_probability']} for r in armrows],
             key=lambda r:(-r['raw_absolute_error'],r['fold'],r['index']))[:60])
        x=evaluations[arm]['all'];z=x['separation']['zero_vs_nonzero'];s=x['separation']['negative_vs_positive_conditional_true_nonzero']
        er=x['error_attribution']['raw']
        print(f'  {arm}: zero AUC={z["auc"]:.3f}, sign AUC={s["auc"]:.3f}, '
              f'raw MAE={er["mae"]:.4f}, calibrated MAE={x["error_attribution"]["calibrated"]["mae"]:.4f}',flush=True)
    # The held-out labels appear in scoring only, never in threshold fitting.
    # AUC is threshold-free; these are retrospective descriptive diagnostics.
    for path,digest in fingerprints.items():
        if suff._digest(path)!=digest:raise ValueError('GAMER_SEPARABILITY_CALIBRATION_SOURCE_CHANGED:'+path)
    out.mkdir(parents=True,exist_ok=False)
    def writecsv(path,records):
        with path.open('w',encoding='utf-8',newline='') as f:
            if not records:return
            writer=csv.DictWriter(f,fieldnames=list(records[0]));writer.writeheader();writer.writerows(records)
    writecsv(out/'probability_distributions.csv',distributions)
    writecsv(out/'probability_histograms.csv',histograms)
    writecsv(out/'numerical_error_attribution.csv',attributions)
    writecsv(out/'worst_numerical_errors.csv',worst)
    result={'schema':SCHEMA,'status':'COMPLETE_READ_ONLY_NOT_PROMOTED',
        'promotion':'BLOCKED_NO_HEAD_OR_THRESHOLD_PROMOTION',
        'source_calibration':str(root),'source_fingerprints':fingerprints,
        'coverage':{'cases_per_arm':int(report['coverage']['heldout_cases']),
            'arms':arms,'bootstraps_per_primary_auc':bootstraps,'bootstrap_seed':seed,
            'game_holdout':'original calibrated unseen-game folds',
            'magnitude_holdout':'exact prior predetermined reserved-magnitude bands'},
        'results':evaluations,
        'interpretation_constraints':[
            'Class probabilities are original frozen-head softmax scores, not certified calibrated probabilities.',
            'Only original certified DEV labels are used for retrospective evaluation, never for head or threshold selection.',
            'AUROC and average precision measure ranking/separability independent of a classification threshold, not mathematical correctness.',
            'Probability AUC is compared with the actual zero decision-margin AUC, which need not have the same ranking.',
            'Negative-vs-positive sign AUC is conditioned on TRUE nonzero labels solely for analysis; this is not an operational oracle.',
            'No threshold is fitted, searched, or chosen from heldout DEV predictions.',
            'Missing classes (for example true zero in reserved magnitude bands) yield null/UNIDENTIFIABLE rather than fabricated scores.',
            'The error attribution is an exact disjoint MAE decomposition; it does not recover hidden magnitude outputs on predicted-zero cases.',
            'Game-cluster bootstrap intervals are exploratory; the same DEV examples have influenced prior experiment design.',
            'Balanced and control heads are not capacity-matched in the full100 arm.',
            'No inference, CUDA, checkpoint replay, optimization, gradients, model weight changes or source writes occur.',
            'The zero-line certification and future independent confirmation remain blocked.'
        ]}
    gamer.atomic_json(out/'final_report.json',result)
    print('GAMER SEPARABILITY COMPLETE: '+str(out/'final_report.json'),flush=True)
    return result
