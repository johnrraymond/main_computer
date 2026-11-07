#!/usr/bin/env python3
"""Disposable certified sign-only GAMER probe on immutable Qwen feature caches.

Train binary NEGATIVE/POSITIVE heads on nonzero original certified TRAIN targets.
No zero examples, magnitude regression, reserved magnitude training, C0/LoRA update,
or held-out DEV prompt selection enter the optimizer. Game folds, feature arms,
and magnitude bands are exactly those of the preceding balanced-readout lab.
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import numpy as np

from tools import nanojev_gamer_v1_sufficiency as suff
from tools import nanojev_gamer_v1_balanced_repair as repair
from tools import nanojev_gamer_v1_threshold_calibration as cal
from tools import nanojev_gamer_v1_separability as sep
from tools import nanojev_gamer_v1_number_distance as distance
from tools import nanojev_gamer_v1_handoff as gamer

SCHEMA='nanojev.gamer.v1.disposable-sign-only.v1'
ARM_NAMES=('localized','neighbor_control','random_control','full100')


def _lineage(run):
    """Follow explicitly fingerprinted sep -> cal -> balanced -> suff -> source."""
    root=Path(run).resolve(); rp=root/'final_report.json'
    if not rp.is_file():raise FileNotFoundError('GAMER_SIGN_SOURCE_REPORT_MISSING:'+str(rp))
    record=json.loads(rp.read_text(encoding='utf-8'))
    protected=[rp]
    if record.get('schema')==sep.SCHEMA:
        if record.get('status')!='COMPLETE_READ_ONLY_NOT_PROMOTED':
            raise ValueError('GAMER_SIGN_BAD_SEPARABILITY_STATUS')
        for file,sha in record.get('source_fingerprints',{}).items():
            if suff._digest(file)!=sha:raise ValueError('GAMER_SIGN_SEPARABILITY_SOURCE_CHANGED:'+file)
        root=Path(record['source_calibration']).resolve()
        rp=root/'final_report.json';record=json.loads(rp.read_text(encoding='utf-8'));protected.append(rp)
    if record.get('schema')==cal.SCHEMA:
        if record.get('status')!='COMPLETE_READ_ONLY_NOT_PROMOTED':
            raise ValueError('GAMER_SIGN_BAD_CALIBRATION_STATUS')
        for file,sha in record.get('source_fingerprints',{}).items():
            if suff._digest(file)!=sha:raise ValueError('GAMER_SIGN_CALIBRATION_SOURCE_CHANGED:'+file)
        root=Path(record['source_balanced_repair']).resolve()
        rp=root/'final_report.json';record=json.loads(rp.read_text(encoding='utf-8'));protected.append(rp)
    if record.get('schema')!=repair.SCHEMA or record.get('status')!='COMPLETE_DISPOSABLE_NOT_PROMOTED':
        raise ValueError('GAMER_SIGN_REQUIRES_BALANCED_LINEAGE')
    for file,sha in record.get('source_fingerprints',{}).items():
        if suff._digest(file)!=sha:raise ValueError('GAMER_SIGN_BALANCED_SOURCE_CHANGED:'+file)
    origin=Path(record['source_generalization_or_sufficiency']).resolve()
    _,prior,causal_record,capture_record,positions,exp,source,capture,other=repair._lineage(origin)
    if Path(record['source_expanded']).resolve()!=exp or list(record['selected_positions'])!=positions:
        raise ValueError('GAMER_SIGN_BALANCED_LINEAGE_MISMATCH')
    return record,prior,capture_record,positions,exp,source,capture,protected+other+[rp]


def _nonzero_partition(fold,labels,game_ids):
    """Original fit/val games, only certified nonzero non-reserved training labels."""
    y=np.asarray(labels,dtype=np.float64);g=np.asarray(game_ids)
    result={}
    for key in ('fit','val'):
        ids=np.asarray(fold[key],dtype=np.int64)
        ids=ids[y[ids]!=0.]
        if len(ids)<2 or any(repair._reserved_for_fitting(v) for v in y[ids]):
            raise ValueError('GAMER_SIGN_RESERVED_OR_EMPTY_'+key.upper())
        counts=np.bincount((y[ids]>0).astype(int),minlength=2)
        if (counts==0).any():raise ValueError('GAMER_SIGN_MISSING_SIGN_IN_'+key.upper())
        result[key]=ids
    if set(result['fit'])&set(result['val']):raise ValueError('GAMER_SIGN_FIT_VAL_INDEX_LEAK')
    if set(g[result['fit']])&set(g[result['val']]):raise ValueError('GAMER_SIGN_FIT_VAL_GAME_LEAK')
    if set(g[result['fit']])&set(fold['test_games']) or set(g[result['val']])&set(fold['test_games']):
        raise ValueError('GAMER_SIGN_TEST_GAME_LEAK')
    return result


def _sample_sign_balanced(y,ids,*,steps,batch_size,seed,near_radius=1.,near_fraction=.25):
    """Two equal sign pools; near-zero boost is TRAIN-only, without magnitude loss."""
    ids=np.asarray(ids,dtype=np.int64);y=np.asarray(y,dtype=np.float64)
    if steps<1 or batch_size<4 or batch_size%2 or not 0<=near_fraction<=1 or near_radius<=0:
        raise ValueError('GAMER_SIGN_INVALID_BATCH_SAMPLER')
    if len(np.unique(ids))!=len(ids) or any(y[ids]==0):
        raise ValueError('GAMER_SIGN_ZERO_OR_DUPLICATE_IN_TRAIN')
    neg=ids[y[ids]<0];pos=ids[y[ids]>0]
    if not len(neg) or not len(pos):raise ValueError('GAMER_SIGN_MISSING_BINARY_CLASS')
    rng=np.random.default_rng(seed)
    for _ in range(steps):
        chosen=[]
        for pool in (neg,pos):
            half=batch_size//2;small=pool[np.abs(y[pool])<=near_radius]
            boosted=int(round(half*near_fraction)) if len(small) else 0
            if boosted:chosen.extend(rng.choice(small,boosted,replace=True))
            chosen.extend(rng.choice(pool,half-boosted,replace=True))
        rng.shuffle(chosen)
        yield np.asarray(chosen,dtype=np.int64)


def _model(torch,dim,hidden):
    class SignProbe(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.norm=torch.nn.LayerNorm(dim,elementwise_affine=False)
            self.layers=torch.nn.Sequential(torch.nn.Linear(dim,hidden),torch.nn.GELU(),
                 torch.nn.Linear(hidden,32),torch.nn.GELU(),torch.nn.Linear(32,2))
        def forward(self,x):return self.layers(self.norm(x))
    return SignProbe()


def _predict(torch,net,features,ids,positions,*,device,batch_size,width=1024,vectors=100):
    import torch.nn.functional as F
    logits=[]
    net.eval()
    with torch.inference_mode():
        for start in range(0,len(ids),batch_size):
            part=np.asarray(ids[start:start+batch_size],dtype=np.int64)
            xx=torch.from_numpy(suff._batch(features,part,positions,vectors=vectors,width=width)).to(device)
            score=net(xx)
            logits.append(F.softmax(score,dim=-1).detach().cpu().numpy().astype(np.float64))
    if not logits:return np.empty((0,2),dtype=np.float64)
    result=np.concatenate(logits)
    if result.shape!=(len(ids),2) or not np.isfinite(result).all():
        raise ValueError('GAMER_SIGN_NONFINITE_PREDICTIONS')
    return result


def _fit(torch,features,labels,fold,positions,*,device,epochs,batch_size,hidden,lr,patience,seed,
         vectors=100,width=1024):
    """Pure binary CE, macro validation CE; never uses TEST labels for fitting."""
    import torch.nn.functional as F
    y=np.asarray(labels,dtype=np.float64)
    fi=np.asarray(fold['fit'],dtype=np.int64);vi=np.asarray(fold['val'],dtype=np.int64)
    if epochs<1 or batch_size<4 or batch_size%2 or patience<1 or not 0<lr<=.01:
        raise ValueError('GAMER_SIGN_INVALID_FIT_BUDGET')
    for ids in (fi,vi):
        if len(ids)==0 or any(y[ids]==0) or any(repair._reserved_for_fitting(v) for v in y[ids]):
            raise ValueError('GAMER_SIGN_FIT_VAL_NUMERIC_LEAK')
        if (np.bincount((y[ids]>0).astype(int),minlength=2)==0).any():
            raise ValueError('GAMER_SIGN_FIT_VAL_MISSING_CLASS')
    dim=width*(vectors if positions is None else len(positions))
    torch.manual_seed(seed)
    net=_model(torch,dim,hidden).to(device)
    optim=torch.optim.AdamW(net.parameters(),lr=lr,weight_decay=1e-4)
    best=math.inf;best_state=None;best_epoch=0;stale=0
    history=[];steps=max(1,math.ceil(len(fi)/batch_size))
    for epoch in range(1,epochs+1):
        net.train()
        for ids in _sample_sign_balanced(y,fi,steps=steps,batch_size=batch_size,
                                         seed=seed+epoch*1009):
            xx=torch.from_numpy(suff._batch(features,ids,positions,vectors=vectors,width=width)).to(device)
            truth=torch.tensor((y[ids]>0).astype(np.int64),device=device,dtype=torch.long)
            loss=F.cross_entropy(net(xx),truth)
            if not torch.isfinite(loss):raise ValueError('GAMER_SIGN_NONFINITE_TRAINING')
            optim.zero_grad(set_to_none=True);loss.backward()
            torch.nn.utils.clip_grad_norm_(net.parameters(),1.0);optim.step()
        net.eval();perclass=[]
        with torch.inference_mode():
            scores=[]
            for start in range(0,len(vi),batch_size):
                ids=vi[start:start+batch_size]
                xx=torch.from_numpy(suff._batch(features,ids,positions,vectors=vectors,width=width)).to(device)
                truth=torch.tensor((y[ids]>0).astype(np.int64),device=device,dtype=torch.long)
                scores.extend(F.cross_entropy(net(xx),truth,reduction='none').cpu().tolist())
            ce=np.asarray(scores,dtype=np.float64)
            cl=(y[vi]>0).astype(np.int64)
            for klass in (0,1):perclass.append(float(np.mean(ce[cl==klass])))
            val=float(np.mean(perclass))
        if not math.isfinite(val):raise ValueError('GAMER_SIGN_NONFINITE_VALIDATION')
        history.append({'epoch':epoch,'validation_macro_ce':val,
                        'negative_ce':perclass[0],'positive_ce':perclass[1]})
        if val<best-1e-5:
            best=val;best_epoch=epoch;stale=0
            best_state={k:v.detach().cpu().clone() for k,v in net.state_dict().items()}
        else:
            stale+=1
            if stale>=patience:break
    if best_state is None:raise ValueError('GAMER_SIGN_NO_CHECKPOINT')
    net.load_state_dict(best_state);net.eval()
    return net,{'best_epoch':best_epoch,'epochs_executed':epoch,
          'best_validation_macro_ce':best,'parameters':sum(p.numel() for p in net.parameters()),
          'fit_nonzero_cases':len(fi),'val_nonzero_cases':len(vi),
          'fit_negative':int((y[fi]<0).sum()),'fit_positive':int((y[fi]>0).sum()),
          'validation_negative':int((y[vi]<0).sum()),'validation_positive':int((y[vi]>0).sum()),
          'history':history},best_state


def _metrics(rows):
    """Stand-alone sign task; true zeros are never scored or implied certified."""
    if not rows:return {'cases':0,'status':'NO_NONZERO_CASES','auc':None,'balanced_accuracy':None}
    y=np.asarray([r['target'] for r in rows],dtype=np.float64)
    probs=np.asarray([r['p_positive'] for r in rows],dtype=np.float64)
    if np.any(y==0) or not np.isfinite(y).all() or not np.isfinite(probs).all() or np.any((probs<0)|(probs>1)):
        raise ValueError('GAMER_SIGN_METRICS_MUST_BE_NONZERO')
    positive=y>0
    predicted=probs>=.5
    negatives=~positive
    nearneg=negatives&(y>=-1)
    nearpos=positive&(y<=1)
    from tools.nanojev_gamer_v1_separability import _auc, _average_precision
    return {'status':'COMPLETE' if positive.any() and negatives.any() else 'SINGLE_CLASS',
       'cases':len(y),'negative_cases':int(negatives.sum()),'positive_cases':int(positive.sum()),
       'auc':_auc(probs,positive),'average_precision':_average_precision(probs,positive),
       'balanced_accuracy':float((np.mean(predicted[positive]) + np.mean(~predicted[negatives]))/2) if positive.any() and negatives.any() else None,
       'accuracy':float(np.mean(predicted==positive)),
       'negative_recall':float(np.mean(~predicted[negatives])) if negatives.any() else None,
       'positive_recall':float(np.mean(predicted[positive])) if positive.any() else None,
       'small_negative_cases':int(nearneg.sum()),
       'small_negative_recall':float(np.mean(~predicted[nearneg])) if nearneg.any() else None,
       'small_positive_cases':int(nearpos.sum()),
       'small_positive_recall':float(np.mean(predicted[nearpos])) if nearpos.any() else None,
       'mean_probability_on_negative':float(np.mean(probs[negatives])) if negatives.any() else None,
       'mean_probability_on_positive':float(np.mean(probs[positive])) if positive.any() else None}


def _segment(rows):
    return {'all':_metrics(rows),
            'eligible_magnitudes':_metrics([r for r in rows if not suff._reserved_numeric_range(r['target'])]),
            'reserved_untrained_magnitudes':_metrics([r for r in rows if suff._reserved_numeric_range(r['target'])])}


def _sign_gate(metric):
    """Readout-only sign gate; passing never certifies zero or magnitude."""
    requires={'negative_cases':8,'positive_cases':8,'small_negative_cases':8,
              'small_positive_cases':8,'auc':.95,'balanced_accuracy':.95,
              'negative_recall':.95,'positive_recall':.95,
              'small_negative_recall':.95,'small_positive_recall':.95}
    coverage=all(metric.get(k,0)>=requires[k] for k in (
        'negative_cases','positive_cases','small_negative_cases','small_positive_cases'))
    metrics=coverage and all(metric.get(k) is not None and metric[k]>=requires[k]
           for k in ('auc','balanced_accuracy','negative_recall','positive_recall',
                     'small_negative_recall','small_positive_recall'))
    return {'passed':bool(metrics),'status':'PASS_SIGN_ONLY' if metrics else
       ('BLOCKED_INSUFFICIENT_SIGN_CASES' if not coverage else 'BLOCKED_SIGN_NOT_CERTIFIED'),
       'requirements':requires,'not_a_zero_or_magnitude_gate':True}


def _evaluate_predictions(rows,arms,expected):
    """The same dev indices in every arm, exactly once; no test-label selection."""
    results={};csvrows=[]
    for arm in arms:
        armrows=[r for r in rows if r['arm']==arm]
        keys=[r['index'] for r in armrows]
        if len(keys)!=len(set(keys)) or set(keys)!=set(expected):
            raise ValueError('GAMER_SIGN_UNPAIRED_HELDOUT_ROWS:'+arm)
        armrows.sort(key=lambda r:r['index'])
        signature={r['index']:(r['game_id'],r['target'],r['fold']) for r in armrows}
        if results and signature!=first_signature:
            raise ValueError('GAMER_SIGN_ARM_TARGET_OR_FOLD_ALIGNMENT')
        if not results:first_signature=signature
        result=_segment(armrows)
        result['sign_gate']={'all':_sign_gate(result['all']),
           'reserved_untrained_magnitudes':_sign_gate(result['reserved_untrained_magnitudes'])}
        result['by_fold']={str(fi):_segment([r for r in armrows if r['fold']==fi])
                      for fi in sorted({r['fold'] for r in armrows})}
        results[arm]=result
        csvrows.extend(armrows)
    return results,csvrows


def _save_checkpoint(torch,path,state,meta):
    """Never use the sufficiency/C0 checkpoint schema for a binary-only head."""
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp')
    try:
        torch.save({'schema':SCHEMA,'state_dict':state,'metadata':meta},tmp)
        tmp.replace(path)
    finally:
        if tmp.exists():tmp.unlink()


def run_sign_only(run,*,output=None,device='cuda',epochs=35,batch_size=64,hidden=64,
                  lr=.001,patience=8,seed=47):
    if not 5<=epochs<=200 or not 4<=batch_size<=256 or batch_size%2 or not 8<=hidden<=256 or not 0<lr<=.01 or patience<1:
        raise ValueError('GAMER_SIGN_INVALID_BUDGET')
    import torch
    if device.startswith('cuda') and not torch.cuda.is_available():
        raise RuntimeError('GAMER_SIGN_CUDA_UNAVAILABLE')
    parent=Path(run).resolve()
    balanced,prior,capture_record,chosen,exp,source,capture,protected=_lineage(parent)
    out=distance._destination(parent,output,'-sign-only')
    for item in [parent,exp,source,capture,Path(balanced['source_generalization_or_sufficiency']).resolve()]:
        if out==item or out in item.parents or item in out.parents:
            raise ValueError('GAMER_SIGN_OUTPUT_OVERLAPS_SOURCE')
    identity=json.loads((exp/'provenance.json').read_text(encoding='utf-8'))
    train,xtrain,pt=suff._cached_split(exp,source,identity,'train')
    dev,xdev,pd=suff._cached_split(exp,source,identity,'dev')
    folds=cal._folds(train,dev,capture_record,prior,balanced)
    arms=suff._arms(chosen,seed=int(balanced['settings']['original_split_seed']))
    if {k:v for k,v in arms.items()}!=balanced['arms']:
        raise ValueError('GAMER_SIGN_CONTROL_ARMS_CHANGED')
    ys=np.asarray([float(r['answer']) for r in train],dtype=np.float64)
    yd=np.asarray([float(r['answer']) for r in dev],dtype=np.float64)
    games=np.asarray([r['game_id'] for r in train])
    folds=[{**f,**_nonzero_partition(f,ys,games)} for f in folds]
    # We score ALL original dev cases in each test fold except true zeros; no
    # selection or filtering based on observed sign, errors, or probabilities.
    expected=sorted(int(i) for fold in folds for i in fold['test'] if yd[i]!=0.)
    if len(set(expected))!=len(expected):raise ValueError('GAMER_SIGN_TEST_REUSE')
    if sum(yd[i]==0 for fold in folds for i in fold['test'])<1:
        raise ValueError('GAMER_SIGN_EXPECTS_RESERVED_ZERO_CLASS_FOR_AUDIT')
    protected.extend([pt,pd,source/'certified_pairs.jsonl',exp/'best_model.pt'])
    fingerprints={str(p):suff._digest(p) for p in set(protected)}
    print('GAMER SIGN-ONLY PROBE: '+str(out),flush=True)
    print('  frozen Qwen / LoRA / production C0; certified NONZERO labels only; two-class CE; no magnitude loss',flush=True)
    print(f'  game folds={len(folds)}; trained-range FIT/VAL only; reserved magnitudes TEST only; heldout nonzero={len(expected)}',flush=True)
    predictions=[];snapshots=[];training={name:[] for name in arms}
    for fold in folds:
        fi=fold['fold']; ids=np.asarray([i for i in fold['test'] if yd[i]!=0.],dtype=np.int64)
        if len(ids)==0:raise ValueError('GAMER_SIGN_EMPTY_HELDOUT_FOLD')
        print(f'  fold {fi+1}/{len(folds)}: fit nonzero={len(fold["fit"])} val nonzero={len(fold["val"])} '
              f'heldout nonzero={len(ids)}',flush=True)
        for arm,p in arms.items():
            h=min(hidden,16) if p is None else hidden
            net,meta,state=_fit(torch,xtrain,ys,fold,p,device=device,
              epochs=epochs,batch_size=batch_size,hidden=h,lr=lr,patience=patience,
              seed=seed+193*fi)
            prob=_predict(torch,net,xdev,ids,p,device=device,batch_size=batch_size)
            for i,pair in zip(ids,prob):
                predictions.append({'arm':arm,'fold':fi,'index':int(i),
                  'game_id':dev[int(i)]['game_id'],'target':float(yd[i]),
                  'p_negative':float(pair[0]),'p_positive':float(pair[1]),
                  'predicted_sign':'positive' if pair[1]>=.5 else 'negative',
                  'magnitude_reserved':suff._reserved_numeric_range(yd[i])})
            metric=_metrics(predictions[-len(ids):]);
            print(f'    {arm}: sign-AUC={metric["auc"]}, balanced-acc={metric["balanced_accuracy"]}, '
                  f'negative-recall={metric["negative_recall"]}, best_epoch={meta["best_epoch"]}',flush=True)
            meta.update({'fold':fi,'arm':arm,'positions':p})
            training[arm].append(meta)
            snapshots.append((out/'experimental_models'/f'fold{fi+1:02d}_{arm}.pt',state,
              {'schema':SCHEMA,'fold':fi,'arm':arm,'positions':p,'source_expanded':str(exp)}))
            del net,state
            if device.startswith('cuda'):torch.cuda.empty_cache()
    results,rows=_evaluate_predictions(predictions,arms,expected)
    previous_auc={}
    parent_file=parent/'final_report.json'
    parent_record=json.loads(parent_file.read_text(encoding='utf-8')) if parent_file.is_file() else {}
    if parent_record.get('schema')==sep.SCHEMA:
        for arm in arms:
            previous_auc[arm]={band:parent_record['results'][arm][band]['separation'][
                  'negative_vs_positive_conditional_true_nonzero']['auc']
               for band in ('all','eligible_magnitudes','reserved_untrained_magnitudes')}
            for band,old_auc in previous_auc[arm].items():
                if old_auc is not None and results[arm][band]['cases']!=parent_record['results'][arm][band]['cases'] - (
                    parent_record['results'][arm][band]['true_class_counts'].get('zero',0)):
                    raise ValueError('GAMER_SIGN_COMPARISON_COHORT_CHANGED:'+arm+':'+band)
    for file,sha in fingerprints.items():
        if suff._digest(file)!=sha:raise ValueError('GAMER_SIGN_SOURCE_MUTATED:'+file)
    out.mkdir(parents=True,exist_ok=False)
    for file,state,meta in snapshots:_save_checkpoint(torch,file,state,meta)
    with (out/'heldout_predictions.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    report={'schema':SCHEMA,'status':'COMPLETE_DISPOSABLE_SIGN_ONLY_NOT_PROMOTED',
      'promotion':'BLOCKED_SIGN_ONLY_NOT_ZERO_OR_MAGNITUDE_CERTIFICATION',
      'source_run':str(parent),'source_expanded':str(exp),'arms':arms,
      'settings':{'device':device,'epochs':epochs,'batch_size':batch_size,'hidden':hidden,
                  'lr':lr,'patience':patience,'seed':seed,'original_fold_seed':int(balanced['settings']['original_split_seed']),
                  'loss':'BALANCED_BINARY_SIGN_CROSS_ENTROPY_ONLY','small_near_zero_fraction':.25,
                  'zero_training_examples':0,'magnitude_training_loss':0},
      'coverage':{'dev_nonzero_cases':len(expected),
                  'nonzero_reserved_cases':int(sum(bool(suff._reserved_numeric_range(yd[i])) for i in expected)),
                  'dev_zero_cases_excluded':int(sum(bool(yd[i]==0) for fold in folds for i in fold['test'])),
                  'game_fold_names':[f['test_games'] for f in folds],
                  'fit_counts':[len(f['fit']) for f in folds],
                  'validation_counts':[len(f['val']) for f in folds]},
      'source_fingerprints':fingerprints,'results':results,'training':training,
      'prior_three_class_sign_auc':previous_auc,
      'interpretation_constraints':[
          'Only original certified nonzero TRAIN labels influence fitting; no zero or magnitude regression is fitted.',
          'Original four game-disjoint folds and feature arms are reconstructed and checked against balanced predecessor.',
          'Reserved log2 magnitude bands and float32-boundary ambiguous values are excluded from FIT and validation.',
          'The same held-out certified nonzero DEV examples are scored in every arm; NO heldout labels tune optimizer or thresholds.',
          'Small-negative tests have limited support: show denominators and never infer population-wide certification.',
          'No true zero examples are scored by design: this does not pass or test zero detection or numerical magnitude.',
          'An AUC or sign accuracy gain is predictive only, not a causal guarantee or direct arithmetic correctness.',
          'The same DEV set informed earlier experiment design; results are exploratory and require independent confirmation.',
          'Qwen, LoRA, production C0, and prior head checkpoints remain immutable; only disposable sign-only models are trained.',
          'Full100 uses a narrower first hidden layer than small arms, following prior control budget; capacities differ.'
          ]}
    gamer.atomic_json(out/'final_report.json',report)
    l=results['localized'];r=results['random_control']
    print(f'  localized overall sign AUC={l["all"]["auc"]}; reserved sign AUC={l["reserved_untrained_magnitudes"]["auc"]}; '
          f'random-control reserved AUC={r["reserved_untrained_magnitudes"]["auc"]}',flush=True)
    if previous_auc:
        print(f'  prior 3-class localized sign AUC: all={previous_auc["localized"]["all"]} -> {l["all"]["auc"]}; '
              f'reserved={previous_auc["localized"]["reserved_untrained_magnitudes"]} -> '
              f'{l["reserved_untrained_magnitudes"]["auc"]}',flush=True)
    print(f'  sign gate: all={l["sign_gate"]["all"]["status"]}; '
          f'reserved={l["sign_gate"]["reserved_untrained_magnitudes"]["status"]}; '
          'zero and magnitude remain UNTESTED',flush=True)
    print('GAMER SIGN-ONLY COMPLETE: '+str(out/'final_report.json'),flush=True)
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
    a=ap.parse_args()
    run_sign_only(a.run,output=a.output,device=a.device,epochs=a.epochs,batch_size=a.batch_size,
                  hidden=a.hidden,lr=a.lr,patience=a.patience,seed=a.seed)
