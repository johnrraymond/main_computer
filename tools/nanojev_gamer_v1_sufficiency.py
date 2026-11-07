#!/usr/bin/env python3
"""GAMER frozen-Qwen numerical sufficiency lab (disposable readouts only).

Fits small sign/zero/magnitude heads on immutable cached *certified* prompts.
Every reported test prompt is from a game absent from the head's training and
validation. Four input arms: localization-selected Qwen positions, nearby
positions, randomly selected positions, and the complete 100-vector input.

This lab tests whether information is *readable* under this head architecture.
It does not repair or update production C0/LoRA, prove a causal path, or certify
zero spelling on natural prompts (the additional spelling deck is synthetic).
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from tools import nanojev_gamer_v1_causal_path as causal
from tools import nanojev_gamer_v1_expanded_head as expanded_mod
from tools import nanojev_gamer_v1_handoff as gamer
from tools import nanojev_gamer_v1_number_distance as dist

SCHEMA = 'nanojev.gamer.v1.disposable-sufficiency.v1'


def _digest(path):
    return causal._hash(Path(path))


def _source(causal_run):
    root = Path(causal_run).resolve()
    cp = root/'final_report.json'
    if not cp.is_file():
        raise FileNotFoundError('GAMER_SUFFICIENCY_CAUSAL_REPORT_MISSING:'+str(cp))
    record = json.loads(cp.read_text(encoding='utf-8'))
    if record.get('schema') != causal.SCHEMA or record.get('status')!='COMPLETE_READ_ONLY':
        raise ValueError('GAMER_SUFFICIENCY_BAD_CAUSAL_LINEAGE')
    positions = list(map(int,record.get('candidate_positions_global',[])))
    if len(positions)<1 or len(set(positions))!=len(positions) or any(not 0<=p<100 for p in positions):
        raise ValueError('GAMER_SUFFICIENCY_BAD_LOCALIZED_POSITIONS')
    capture = Path(record['source_capture']).resolve()
    localization=Path(record['source_localization']).resolve()
    exp=Path(record['expanded_run']).resolve()
    cr=json.loads((capture/'final_report.json').read_text(encoding='utf-8'))
    if cr.get('schema')!=dist.SCHEMA or cr.get('checkpoint_step')!=record['checkpoint_step']:
        raise ValueError('GAMER_SUFFICIENCY_CAPTURE_LINEAGE_MISMATCH')
    if _digest(exp/'best_model.pt') != record['checkpoint_sha256']:
        raise ValueError('GAMER_SUFFICIENCY_HEAD_FINGERPRINT_CHANGED')
    for name,h in record['source_sha256'].items():
        if _digest(capture/name)!=h:
            raise ValueError('GAMER_SUFFICIENCY_CAPTURE_FINGERPRINT_CHANGED:'+name)
    loc_file=localization/'final_report.json'
    if not loc_file.is_file():raise FileNotFoundError(loc_file)
    lr=json.loads(loc_file.read_text(encoding='utf-8'))
    if str(Path(lr['source_capture']).resolve())!=str(capture):
        raise ValueError('GAMER_SUFFICIENCY_LOCALIZATION_CAPTURE_MISMATCH')
    return root,record,cr,lr,positions,exp,Path(cr['source_run']).resolve(),capture


def _cached_split(exp,source,identity,split):
    manifest=json.loads((source/'handoff_manifest.json').read_text(encoding='utf-8'))
    rows,_=gamer.load_cases(source/'certified_pairs.jsonl',
        allow_partial=bool(manifest.get('allow_partial_smoke')),
        include_catalog_extensions=bool(manifest.get('bounded_catalog_extensions')),
        include_transition_games=bool(manifest.get('bounded_transition_games')))
    filtered=gamer.by_split(rows,split)
    hash_of_prompts=hashlib.sha256(gamer.canonical(
        [gamer.original_prompt(r) for r in filtered]).encode('utf-8')).hexdigest()
    key={**identity,'split':split,'prompt_sha256':hash_of_prompts}
    path=exp/'feature_cache'/f'{split}.npz'
    if not path.is_file():raise FileNotFoundError('GAMER_SUFFICIENCY_CACHE_MISSING:'+str(path))
    cfg=identity['configuration'];features,_=expanded_mod._read_cache(path,key,len(filtered),cfg['input_width'])
    if features.shape!=(len(filtered),102400):
        raise ValueError('GAMER_SUFFICIENCY_FEATURE_WIDTH_MISMATCH')
    return filtered,features,path


def _reserved_numeric_range(value):
    """Pre-committed *magnitude interval* holdout, not eval-label-driven filtering.

    All nonzero targets in every fourth log2 magnitude interval are excluded
    from both readout fitting and early-stop calibration. Zero stays in training
    because the experiment specifically tests learning an actual zero class.
    """
    value=float(value)
    return value!=0. and math.floor(math.log2(abs(value))) % 4 == 0


def _split_groups(rows_train,rows_dev,calibration_games,*,folds,seed):
    """Split by game name, not row. Never use evaluation answers for selection."""
    known=set(r['game_id'] for r in rows_train)
    eligible=sorted((set(r['game_id'] for r in rows_dev)&known)-set(calibration_games))
    if len(eligible)<3*folds:
        raise ValueError('GAMER_SUFFICIENCY_TOO_FEW_UNSEEN_GAMES')
    rng=np.random.default_rng(seed);ordered=list(np.asarray(eligible)[rng.permutation(len(eligible))])
    chunks=[list(map(str,arr)) for arr in np.array_split(ordered,folds)]
    result=[]
    for fold,te in enumerate(chunks):
        test=set(te)
        remaining=sorted(known-test)
        # Validation is selected using game IDs, with no knowledge of dev labels.
        vrng=np.random.default_rng(seed+913*(fold+1))
        nval=max(4,round(len(remaining)*.12))
        val=set(map(str,vrng.choice(remaining,size=nval,replace=False)))
        fit=set(remaining)-val
        train_ids=np.asarray([i for i,r in enumerate(rows_train)
           if r['game_id'] in fit and not _reserved_numeric_range(r['answer'])],dtype=int)
        val_ids=np.asarray([i for i,r in enumerate(rows_train)
           if r['game_id'] in val and not _reserved_numeric_range(r['answer'])],dtype=int)
        test_ids=np.asarray([i for i,r in enumerate(rows_dev) if r['game_id'] in test],dtype=int)
        if min(len(train_ids),len(val_ids),len(test_ids))<1:
            raise ValueError('GAMER_SUFFICIENCY_EMPTY_GROUP_PARTITION')
        if set(r['game_id'] for i,r in enumerate(rows_train) if i in set(train_ids)) & test:
            raise ValueError('GAMER_SUFFICIENCY_GAME_LEAK')
        result.append({'fold':fold,'fit':train_ids,'val':val_ids,'test':test_ids,
             'test_games':te,'fit_games':sorted(fit),'val_games':sorted(val)})
    if len({i for f in result for i in f['test']})!=sum(len(f['test']) for f in result):
        raise ValueError('GAMER_SUFFICIENCY_TEST_ROW_REUSE')
    return result


def _arms(chosen,*,seed,total=100):
    chosen=sorted(map(int,chosen)); n=len(chosen)
    if n<1 or n>total//2:raise ValueError('GAMER_SUFFICIENCY_INVALID_ARM_SIZE')
    nearby=[p for p in range(total) if p not in chosen and min(abs(p-q) for q in chosen)<=8]
    if len(nearby)<n:raise ValueError('GAMER_SUFFICIENCY_NOT_ENOUGH_NEIGHBORS')
    nearby=sorted(nearby,key=lambda p:(min(abs(p-q) for q in chosen),p))[:n]
    eligible=[p for p in range(total) if p not in set(chosen+nearby)]
    if len(eligible)<n:raise ValueError('GAMER_SUFFICIENCY_NOT_ENOUGH_RANDOM_CONTROLS')
    rng=np.random.default_rng(seed)
    random=sorted(map(int,rng.choice(eligible,size=n,replace=False)))
    return {'localized':chosen,'neighbor_control':nearby,'random_control':random,'full100':None}


def _batch(features,indices,positions,*,vectors=100,width=1024):
    features=np.asarray(features)
    if features.ndim!=2 or features.shape[1]!=vectors*width:
        raise ValueError('GAMER_SUFFICIENCY_BAD_FEATURE_SHAPE')
    ids=np.asarray(indices,dtype=int)
    if positions is None:
        return np.asarray(features[ids],dtype=np.float32)
    positions=np.asarray(positions,dtype=int)
    if len(positions)<1 or (positions<0).any() or (positions>=vectors).any():
        raise ValueError('GAMER_SUFFICIENCY_BAD_POSITIONS')
    view=features.reshape(len(features),vectors,width)
    return np.asarray(view[ids[:,None],positions[None,:],:].reshape(len(ids),-1),dtype=np.float32)


def _classes(y):
    a=np.asarray(y,dtype=np.float64)
    return np.where(a<0,0,np.where(a>0,2,1)).astype(np.int64)


def _metrics(y,values,classes):
    y=np.asarray(y,dtype=np.float64);p=np.asarray(values,dtype=np.float64)
    c=np.asarray(classes,dtype=np.int64)
    if y.shape!=p.shape or len(y)!=len(c):raise ValueError('GAMER_SUFFICIENCY_METRIC_SHAPE')
    nz=y!=0;z=~nz;smallneg=(y<0)&(y>=-1);smallpos=(y>0)&(y<=1)
    def frac(mask,pred):return float(np.mean(pred[mask])) if mask.any() else None
    zero_pred=c==1
    return {'cases':int(len(y)),'mae':float(np.mean(np.abs(y-p))),
      'within_0_1':float(np.mean(np.abs(y-p)<=.1)),
      'exact_accuracy':float(np.mean(np.abs(y-p)<=1e-6)),
      'zero_cases':int(z.sum()),'zero_exact_rate':frac(z,np.abs(p-y)<=1e-6),
      'zero_within_0_1_rate':frac(z,np.abs(p-y)<=.1),
      'zero_recall':frac(z,zero_pred),
      'zero_precision':float(np.sum(z & zero_pred)/np.sum(zero_pred)) if zero_pred.any() else None,
      'zero_false_positive_rate':frac(nz,zero_pred),
      'nonzero_sign_rate':frac(nz,np.sign(p)==np.sign(y)),
      'small_negative_cases':int(smallneg.sum()),
      'small_negative_sign_rate':frac(smallneg,np.sign(p)==np.sign(y)),
      'small_positive_cases':int(smallpos.sum()),
      'small_positive_sign_rate':frac(smallpos,np.sign(p)==np.sign(y)),
      'predicted_zero_cases':int(zero_pred.sum()),
      'positive_cases':int((y>0).sum()),'negative_cases':int((y<0).sum())}


def _zero_gate(m):
    required={'zero_cases':8,'small_negative_cases':8,'small_positive_cases':8,
      'zero_exact_rate':.95,'zero_precision':.95,'zero_false_positive_rate_max':.05,
      'zero_within_0_1_rate':.95,'nonzero_sign_rate':.95,
      'small_negative_sign_rate':.95,'small_positive_sign_rate':.95}
    enough=all(m.get(k,0)>=v for k,v in (
      ('zero_cases',8),('small_negative_cases',8),('small_positive_cases',8)))
    if not enough:return {'status':'BLOCKED_INSUFFICIENT_CERTIFIED_CASES','passed':False,
                           'requires':required}
    rates=all(m.get(k) is not None and m[k]>=v for k,v in required.items()
              if k not in ('zero_cases','small_negative_cases','small_positive_cases','zero_false_positive_rate_max'))
    rates=rates and m.get('zero_false_positive_rate') is not None and m['zero_false_positive_rate']<=.05
    return {'status':'PASS_CERTIFIED_GAME_DISJOINT_ZERO_SIGN' if rates else 'BLOCKED_ZERO_SIGN_NOT_CERTIFIED',
      'passed':bool(rates),'requires':required,
      'note':'This gate uses naturally certified numeric answers. Zero spelling on natural prompts remains unverified.'}


def _model(torch,dim,hidden):
    class Readout(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.norm=torch.nn.LayerNorm(dim,elementwise_affine=False)
            self.body=torch.nn.Sequential(torch.nn.Linear(dim,hidden),torch.nn.GELU(),
                                           torch.nn.Linear(hidden,32),torch.nn.GELU())
            self.sign=torch.nn.Linear(32,3)
            self.log_magnitude=torch.nn.Linear(32,1)
        def forward(self,features):
            h=self.body(self.norm(features))
            return self.sign(h),self.log_magnitude(h).squeeze(-1)
    return Readout()


def _fit(torch,features,labels,fit_ids,val_ids,positions,*,device,epochs,
         batch_size,hidden,lr,seed,patience=9,vectors=100,width=1024):
    if not 0<lr<=.1 or epochs<1 or batch_size<1:raise ValueError('GAMER_SUFFICIENCY_BAD_TRAIN_BUDGET')
    if set(map(int,fit_ids))&set(map(int,val_ids)):
        raise ValueError('GAMER_SUFFICIENCY_FIT_VALIDATION_OVERLAP')
    dim=width*(vectors if positions is None else len(positions))
    torch.manual_seed(int(seed)); net=_model(torch,dim,hidden).to(device)
    optim=torch.optim.AdamW(net.parameters(),lr=lr,weight_decay=1e-4)
    fit_y=np.asarray(labels)[fit_ids]; cls=_classes(fit_y)
    counts=np.bincount(cls,minlength=3).astype(np.float64)
    if not (counts>0).all():
        raise ValueError('GAMER_SUFFICIENCY_TRAIN_MISSING_SIGN_OR_ZERO_CLASS')
    weights=np.sqrt(len(cls)/(3*counts));weights=np.clip(weights,.5,4.0)
    weight=torch.tensor(weights,dtype=torch.float32,device=device)
    def evaluate_loss(indices):
        net.eval();n=0;acc=0.0
        with torch.inference_mode():
            for start in range(0,len(indices),batch_size):
                ids=indices[start:start+batch_size]
                xx=torch.from_numpy(_batch(features,ids,positions,vectors=vectors,width=width)).to(device)
                yy=np.asarray(labels)[ids]
                t=torch.as_tensor(yy,device=device,dtype=torch.float32)
                c=torch.as_tensor(_classes(yy),device=device,dtype=torch.long)
                scores,mag=net(xx)
                cel=torch.nn.functional.cross_entropy(scores,c,weight=weight)
                nz=(c!=1)
                ml=torch.nn.functional.smooth_l1_loss(mag[nz],torch.log1p(t[nz].abs())) if nz.any() else 0.*cel
                loss=cel+.35*ml
                acc+=float(loss.detach().item())*len(ids);n+=len(ids)
        return acc/max(n,1)
    rng=np.random.default_rng(seed)
    best=float('inf');best_state=None;best_epoch=0;stale=0
    for ep in range(1,epochs+1):
        order=rng.permutation(np.asarray(fit_ids,dtype=int));net.train()
        for start in range(0,len(order),batch_size):
            ids=order[start:start+batch_size]
            xx=torch.from_numpy(_batch(features,ids,positions,vectors=vectors,width=width)).to(device)
            yy=np.asarray(labels)[ids]
            t=torch.as_tensor(yy,device=device,dtype=torch.float32)
            c=torch.as_tensor(_classes(yy),device=device,dtype=torch.long)
            scores,mag=net(xx)
            ce=torch.nn.functional.cross_entropy(scores,c,weight=weight)
            nz=c!=1
            ml=torch.nn.functional.smooth_l1_loss(mag[nz],torch.log1p(t[nz].abs())) if nz.any() else 0.*ce
            loss=ce+.35*ml
            if not torch.isfinite(loss):raise ValueError('GAMER_SUFFICIENCY_NONFINITE_LOSS')
            optim.zero_grad(set_to_none=True);loss.backward()
            torch.nn.utils.clip_grad_norm_(net.parameters(),1.0)
            optim.step()
        vl=evaluate_loss(np.asarray(val_ids,dtype=int))
        if vl<best-1e-5:
            best=vl;best_epoch=ep;best_state={k:v.detach().cpu().clone() for k,v in net.state_dict().items()};stale=0
        else:
            stale+=1
            if stale>=patience:break
    if best_state is None:raise RuntimeError('GAMER_SUFFICIENCY_NO_TRAINED_MODEL')
    net.load_state_dict(best_state);net.eval()
    return net,{'epochs_executed':ep,'best_epoch':best_epoch,'best_validation_loss':best,
                'parameters':sum(p.numel() for p in net.parameters()),
                'train_class_counts':counts.astype(int).tolist()},best_state


def _predict(torch,net,x,indices,positions,*,device,batch_size,vectors=100,width=1024):
    numeric=[];cs=[];prob=[]
    with torch.inference_mode():
        net.eval()
        for start in range(0,len(indices),batch_size):
            ids=indices[start:start+batch_size]
            f=torch.from_numpy(_batch(x,ids,positions,vectors=vectors,width=width)).to(device)
            logits,logmag=net(f);c=torch.argmax(logits,dim=1)
            size=torch.expm1(torch.clamp(logmag,min=0.,max=12.)).clamp(min=1e-6)
            p=torch.where(c==1,torch.zeros_like(size),torch.where(c==0,-size,size))
            numeric.extend(p.cpu().numpy().astype(float).tolist())
            cs.extend(c.cpu().numpy().astype(int).tolist())
            prob.extend(torch.softmax(logits,dim=1).cpu().numpy().astype(float).tolist())
    return np.asarray(numeric),np.asarray(cs),np.asarray(prob)


def _baseline_from_csv(source,dev,step):
    path=Path(source)/'dev_best.csv'
    if not path.is_file():return None,None
    pred=np.full(len(dev),np.nan,dtype=np.float64)
    with path.open(encoding='utf-8',newline='') as f:
        rd=csv.DictReader(f)
        for row in rd:
            i=int(row['case_index'])
            if not 0<=i<len(dev) or np.isfinite(pred[i]):raise ValueError('GAMER_SUFFICIENCY_BAD_BASELINE_INDEX')
            expected=hashlib.sha256(gamer.original_prompt(dev[i]).encode('utf-8')).hexdigest()
            if row['prompt_sha256']!=expected or int(row['step'])!=step or row['game_id']!=dev[i]['game_id']:
                raise ValueError('GAMER_SUFFICIENCY_BASELINE_LINEAGE_MISMATCH')
            if abs(float(row['target'])-float(dev[i]['answer']))>1e-8:
                raise ValueError('GAMER_SUFFICIENCY_BASELINE_TARGET_MISMATCH')
            pred[i]=float(row['prediction'])
    if not np.isfinite(pred).all():raise ValueError('GAMER_SUFFICIENCY_INCOMPLETE_BASELINE')
    return pred,path


def _spelling_deck(torch,net,zero_features,positions,*,device,batch_size):
    """Only literal-zero *representation invariance*, never certified answers."""
    out={};decisions=[];predictions=[]
    for name in ('0','+0','zero','-0'):
        if name not in zero_features:
            return {'status':'BLOCKED_INCOMPLETE_SYNTHETIC_SPELLINGS'}
        data=np.asarray(zero_features[name],dtype=np.float32)
        p,c,_=_predict(torch,net,data,np.arange(len(data)),positions,
              device=device,batch_size=batch_size)
        out[name]={'predicted_zero_fraction':float(np.mean(c==1)),
                   'mean_prediction':float(np.mean(p)),'cases':int(len(p))}
        decisions.append(c);predictions.append(p)
    if len({len(x) for x in decisions})!=1:
        raise ValueError('GAMER_SUFFICIENCY_ZERO_SPELLING_GAME_COUNT')
    joined=np.stack(decisions)
    agree=float(np.mean(np.all(joined==joined[0:1],axis=0)))
    for i,name in enumerate(('0','+0','zero','-0')):
        out[name]['median_abs_prediction_gap_from_0']=float(np.median(np.abs(predictions[i]-predictions[0])))
    return {'status':'DESCRIPTIVE_SYNTHETIC_NO_CERTIFIED_ANSWER',
            'classification_agreement_across_forms':agree,'forms':out,
            'note':'These are altered game registers, not certified numerical target-zero prompts.'}


def _load_synthetic_zero_deck(capture):
    """Load only the required zero entries; the capture NPZ is read-only."""
    with np.load(capture/'activations.npz',allow_pickle=False) as data:
        required=('qwen_final_100','values','minus_zero__qwen_final_100')
        if not all(key in data for key in required):
            return None
        values=np.asarray(data['values'],dtype=int)
        iz=np.flatnonzero(values==0)
        if len(iz)!=1:raise ValueError('GAMER_SUFFICIENCY_MISSING_SYNTHETIC_ZERO')
        # The 3 forms are digits, explicitly signed, and words.
        source=data['qwen_final_100']
        if source.ndim!=4 or source.shape[1]!=3 or source.shape[-1]!=102400:
            raise ValueError('GAMER_SUFFICIENCY_SYNTHETIC_DECK_SHAPE')
        zero={n:np.asarray(source[:,fi,int(iz[0]),:],dtype=np.float32).copy()
              for fi,n in enumerate(('0','+0','zero'))}
        zero['-0']=np.asarray(data['minus_zero__qwen_final_100'],dtype=np.float32).copy()
        return zero


def _save_state(torch,path,state,meta):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp')
    try:
        torch.save({'schema':SCHEMA,'state_dict':state,'metadata':meta},tmp)
        tmp.replace(path)
    finally:
        if tmp.exists():tmp.unlink()


def run_sufficiency(causal_run,*,output=None,device='cuda',folds=4,epochs=40,
                  batch_size=64,hidden=64,lr=.001,seed=47,patience=8):
    if not 2<=folds<=8 or not 5<=epochs<=200 or not 4<=batch_size<=256 or not 8<=hidden<=256 or patience<1:
        raise ValueError('GAMER_SUFFICIENCY_INVALID_BUDGET')
    if not (0<lr<=.01):raise ValueError('GAMER_SUFFICIENCY_INVALID_LR')
    import torch
    if device.startswith('cuda') and not torch.cuda.is_available():
        raise RuntimeError('GAMER_SUFFICIENCY_CUDA_UNAVAILABLE')
    root,causal_record,cr,loc,selected,exp,source,capture=_source(causal_run)
    out=dist._destination(root,output,'-sufficiency')
    protected=[root/'final_report.json',exp/'best_model.pt',exp/'provenance.json',
               capture/'final_report.json',capture/'activations.npz',
               Path(causal_record['source_localization'])/'final_report.json',
               source/'certified_pairs.jsonl']
    for parent in (root,exp,source,capture,Path(causal_record['source_localization']).resolve()):
        if out==parent or out in parent.parents or parent in out.parents:
            raise ValueError('GAMER_SUFFICIENCY_OUTPUT_OVERLAPS_SOURCE')
    identity=json.loads((exp/'provenance.json').read_text(encoding='utf-8'))
    if identity['configuration']['input_width']!=102400:
        raise ValueError('GAMER_SUFFICIENCY_REQUIRES_FULL_100_VECTORS')
    train,xtrain,ptrain=_cached_split(exp,source,identity,'train')
    dev,xdev,pdev=_cached_split(exp,source,identity,'dev')
    protected.extend([ptrain,pdev])
    calib=[str(r['game_id']) for r in cr['contexts']]
    partitions=_split_groups(train,dev,calib,folds=folds,seed=seed)
    original,pbaseline=_baseline_from_csv(exp,dev,int(cr['checkpoint_step']))
    if pbaseline:protected.append(pbaseline)
    arms=_arms(selected,seed=seed)
    fingerprints={str(p):_digest(p) for p in protected}
    ys=np.asarray([float(r['answer']) for r in train],dtype=np.float64)
    reserved_training_count=int(sum(_reserved_numeric_range(y) for y in ys))
    yd=np.asarray([float(r['answer']) for r in dev],dtype=np.float64)
    print('GAMER DISPOSABLE SUFFICIENCY: '+str(out),flush=True)
    print('  frozen Qwen / LoRA / production C0; certified train+dev caches; '
          f'{folds} game-disjoint folds; {epochs} max epochs; NO PRODUCTION TRAINING',flush=True)
    print('  localized positions='+str(selected)+'; calibration games excluded from test='+str(len(set(calib))),flush=True)
    outputs={name:[] for name in arms};train_summaries={name:[] for name in arms}
    checkpoints=[];source_tests=[]
    for fold in partitions:
        fi=fold['fold'];ids=fold['test']
        source_tests.extend(ids.tolist())
        print(f'  fold {fi+1}/{folds}: train={len(fold["fit"])} val={len(fold["val"])} '
              f'heldout={len(ids)} games={len(fold["test_games"])}',flush=True)
        for ai,(name,positions) in enumerate(arms.items()):
            h=min(hidden,16) if positions is None else hidden
            net,summary,state=_fit(torch,xtrain,ys,fold['fit'],fold['val'],positions,
                device=device,epochs=epochs,batch_size=batch_size,hidden=h,lr=lr,
                seed=seed+fi*193,patience=patience)
            pred,cls,prob=_predict(torch,net,xdev,ids,positions,device=device,batch_size=batch_size)
            summary.update({'fold':fi,'mode':name,'positions':positions,
                            'fit_games':len(fold['fit_games']),'validation_games':len(fold['val_games'])})
            train_summaries[name].append(summary)
            outputs[name].extend({'fold':fi,'index':int(i),'prediction':float(p),
                'class':int(c),'zero_probability':float(q[1])}
                for i,p,c,q in zip(ids,pred,cls,prob))
            path=out/'experimental_models'/f'fold{fi+1:02d}_{name}.pt'
            checkpoints.append((path,state,summary))
            m=_metrics(yd[ids],pred,cls)
            print(f'    {name}: MAE={m["mae"]:.4f} zeros={m["zero_exact_rate"]} '
                  f'small-neg-sign={m["small_negative_sign_rate"]}',flush=True)
            del net,state
            if device.startswith('cuda'):torch.cuda.empty_cache()
    expected=sorted(source_tests)
    if len(set(expected))!=len(expected):raise ValueError('GAMER_SUFFICIENCY_DUPLICATE_HELDOUT_ROW')
    scored={}; prediction_rows=[]
    for name,entries in outputs.items():
        byid=sorted(entries,key=lambda r:r['index'])
        if [r['index'] for r in byid]!=expected:
            raise ValueError('GAMER_SUFFICIENCY_OUTPUT_ALIGNMENT_FAILURE:'+name)
        indices=np.asarray(expected,dtype=int)
        m=_metrics(yd[indices],np.asarray([r['prediction'] for r in byid]),
                   np.asarray([r['class'] for r in byid]))
        reserved=np.asarray([_reserved_numeric_range(yd[i]) for i in expected],dtype=bool)
        reserved_metrics=(_metrics(yd[indices[reserved]],
            np.asarray([r['prediction'] for r in byid])[reserved],
            np.asarray([r['class'] for r in byid])[reserved]) if reserved.any() else None)
        scored[name]={'metrics':m,'reserved_magnitude_metrics':reserved_metrics,
                      'zero_gate':_zero_gate(m),
                      'fold_training':train_summaries[name],
                      'comparison':'all arms evaluated on the identical heldout dev rows'}
        for row in byid:
            i=row['index'];prediction_rows.append({'mode':name,'fold':row['fold'],
             'dev_case_index':i,'game_id':dev[i]['game_id'],
             'prompt_sha256':hashlib.sha256(gamer.original_prompt(dev[i]).encode('utf-8')).hexdigest(),
             'target':yd[i],'prediction':row['prediction'],'predicted_class':row['class'],
             'zero_probability':row['zero_probability']})
    if original is not None:
        indices=np.asarray(expected,dtype=int)
        scored['production_c0_baseline']={'metrics':_metrics(yd[indices],original[indices],
            _classes(original[indices])), 'zero_gate':'ORIGINAL_C0_NOT_DISPOSABLE'}
    diff=scored['localized']['metrics']['mae']-scored['random_control']['metrics']['mae']
    # Calibration games are excluded from labeled head acceptance. This deck
    # supplies only descriptive invariance and NEVER certified target answers.
    zero_deck=_load_synthetic_zero_deck(capture)
    if zero_deck is not None:
        row=next((row for row in checkpoints if row[2]['fold']==0
                  and row[2]['mode']=='localized'),None)
        if row is None:raise ValueError('GAMER_SUFFICIENCY_MISSING_LOCALIZED_HEAD')
        path,state,meta=row
        local_net=_model(torch,1024*len(arms['localized']),hidden).to(device)
        local_net.load_state_dict(state)
        local_net.eval()
        spelling=_spelling_deck(torch,local_net,zero_deck,arms['localized'],
                               device=device,batch_size=batch_size)
        del zero_deck,local_net
    else:
        spelling={'status':'BLOCKED_SYNTHETIC_ZERO_CAPTURE_MISSING'}
    for path,sha in fingerprints.items():
        if _digest(path)!=sha:
            raise ValueError('GAMER_SUFFICIENCY_SOURCE_MUTATED:'+path)
    out.mkdir(parents=True,exist_ok=False)
    for path,state,summary in checkpoints:
        _save_state(torch,path,state,{'positions':summary['positions'],'mode':summary['mode'],
            'fold':summary['fold'],'source_checkpoint_sha256':causal_record['checkpoint_sha256'],
            'train_games_count':summary['fit_games'],'validation_games_count':summary['validation_games']})
    predfile=out/'heldout_predictions.csv'
    with predfile.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=prediction_rows[0].keys());w.writeheader();w.writerows(prediction_rows)
    report={'schema':SCHEMA,'status':'COMPLETE_EXPERIMENTAL_NOT_PROMOTED',
      'promotion':'BLOCKED_FROZEN_PRODUCTION_C0_AND_UNCERTIFIED_ZERO_SPELLINGS',
      'source_causal':str(root),'source_expanded':str(exp),'selected_positions':selected,
      'arms':arms,'settings':{'folds':folds,'epochs':epochs,'batch_size':batch_size,'hidden':hidden,
                             'lr':lr,'seed':seed,'patience':patience,'device':device},
      'source_fingerprints':fingerprints,'certified_split_sizes':{'train':len(train),'dev':len(dev),
        'scored_dev':len(expected),'excluded_localization_game_count':len(set(calib)),
        'excluded_reserved_magnitude_train_cases':reserved_training_count},
      'holdout':{'all_test_games_disjoint_from_training':True,
                 'calibration_games_excluded_from_scoring':True,
                 'numeric_value_generalization':'All nonzero values with floor(log2(abs(target))) modulo 4 = 0 are excluded from fitting AND validation; reserved-magnitude test metrics reported separately. Zero remains in training.',
                 'folds':[{'fold':f['fold'],'heldout_games':f['test_games'],
                  'validation_games_count':len(f['val_games']),'training_games_count':len(f['fit_games']),
                  'test_cases':len(f['test'])} for f in partitions]},
      'results':scored,'localized_minus_random_mae':diff,'zero_spelling':spelling,
      'limitations':[
        'All Qwen and production C0 weights and cached features are immutable; only disposable readout weights are fitted.',
        'Readout sufficiency is predictive, not proof of a unique causal mechanism or abstract numeric semantics.',
        'Selected positions originated in a 24-game synthetic localization; all those games are excluded from scored evaluation.',
        'Game-disjoint tests include a separately reported precommitted held-out log2 magnitude range; zero is in train and test to assess learned zero detection.',
        'Full100 model has more parameters and a narrower first hidden layer; capacity is reported, not assumed matched.',
        'Certified natural zero re-spellings are not available in the cached data; zero spelling gate cannot be passed.',
        'The zero class produces exact zero by an explicit categorical decision, not a numeric tolerance snap.',
      ]}
    gamer.atomic_json(out/'final_report.json',report)
    print('  localized zero gate: '+scored['localized']['zero_gate']['status'],flush=True)
    print('  localized minus random MAE: '+str(diff),flush=True)
    print('GAMER DISPOSABLE SUFFICIENCY COMPLETE: '+str(out/'final_report.json'),flush=True)
    return report


if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--run',type=Path,required=True)
    ap.add_argument('--output',type=Path)
    ap.add_argument('--device',default='cuda')
    ap.add_argument('--folds',type=int,default=4)
    ap.add_argument('--epochs',type=int,default=40)
    ap.add_argument('--batch-size',type=int,default=64)
    ap.add_argument('--hidden',type=int,default=64)
    ap.add_argument('--lr',type=float,default=.001)
    ap.add_argument('--seed',type=int,default=47)
    ap.add_argument('--patience',type=int,default=8)
    a=ap.parse_args()
    run_sufficiency(a.run,output=a.output,device=a.device,folds=a.folds,
        epochs=a.epochs,batch_size=a.batch_size,hidden=a.hidden,
        lr=a.lr,seed=a.seed,patience=a.patience)
