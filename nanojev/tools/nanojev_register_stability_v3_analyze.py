#!/usr/bin/env python3
"""Fit/test the frozen-Qwen 23,552-prompt XXXXXX/YYYYYY register v3 bank.

Consumes the *existing* nanojev_register_stability_v3_generate.py output only.
Fit two independent stage-N LASSO hierarchies on 'train' NUMERICAL PAIRS;
keep grouped val/test pairs and the complete 24-operation control out of fit.
Starts at a chosen LASSO alpha ratio, halves while all heads converge, then
bisects success/failure bracket. Saves each converged fit, scores independent
validation, and scores held-out test/control once at the final selected ratio.
All token states are retained in capture; right-aligned suffix is unpooled.
No LASSO coefficients or selected x_i coordinates come from previous runs.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import threading
import time

import numpy as np
from threadpoolctl import threadpool_limits

import nanojev_register_stability_v3_generate as gen
import nanojev_register_xx_alpha_search as xx

base = xx.base
sparse = xx.sparse
SCHEMA='nanojev.register-stability-v3.analysis.v1'


def report(event, **kw):
    gen.emit(event, **kw)


def sha_file(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda:f.read(1<<20),b''): h.update(chunk)
    return h.hexdigest()


def read_jsonl(path):
    with open(path,encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def verify_source(root, require_complete=True):
    root=Path(root).expanduser().resolve()
    manifest_path=root/'manifest.json'
    if not manifest_path.is_file(): raise FileNotFoundError(f'REGISTER_V3_MANIFEST_MISSING:{manifest_path}')
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('schema')!=gen.SCHEMA or manifest.get('expected_unique_prompts')!=23552:
        raise ValueError('REGISTER_V3_INCOMPATIBLE_GENERATOR_MANIFEST')
    for key,filename in (('tasks_sha256','prompts.jsonl'),('pairs_sha256','pairs.jsonl')):
        if sha_file(root/filename)!=manifest[key]: raise ValueError(f'REGISTER_V3_SOURCE_DIGEST_MISMATCH:{filename}')
    tasks=read_jsonl(root/'prompts.jsonl')
    if len(tasks)!=23552 or any(t['task_id']!=i for i,t in enumerate(tasks)):
        raise ValueError('REGISTER_V3_TASK_INDEX_DRIFT')
    if {t['split'] for t in tasks}!={'train','val','test','control'}:
        raise ValueError('REGISTER_V3_MISSING_SPLITS')
    if any(t['operation'] in manifest['operation_holdouts'] for t in tasks if t['split']!='control'):
        raise ValueError('REGISTER_V3_TRAIN_OPERATION_LEAKAGE')
    identity_path=root/'model_identity.json'
    if not identity_path.is_file():
        if require_complete: raise FileNotFoundError(f'REGISTER_V3_CAPTURE_IDENTITY_MISSING:{identity_path}')
        identity=None
    else: identity=json.loads(identity_path.read_text(encoding='utf-8'))
    count=sum((root/'records'/f"{t['sha256']}.npz").is_file() for t in tasks)
    if require_complete and count!=len(tasks):
        raise RuntimeError(f'REGISTER_V3_INCOMPLETE_CAPTURE:{count}:{len(tasks)}')
    return manifest,tasks,identity,count


def layout_for(manifest,layers,readout_scope,tail_tokens,width=1024):
    stored=manifest['capture']['stored_layers']
    if stored=='all':
        indices=list(layers)
    else:
        if not set(layers).issubset(set(stored)):
            raise ValueError(f'REGISTER_V3_UNCAPTURED_LAYERS:{layers}:{stored}')
        indices=[stored.index(layer) for layer in layers]
    if readout_scope!='suffix':
        raise ValueError('REGISTER_V3_FIRST_ANALYZER_REQUIRES_SUFFIX_READOUT')
    if tail_tokens<1 or tail_tokens>manifest['capture']['max_prompt_tokens']:
        raise ValueError('REGISTER_V3_INVALID_TAIL_TOKENS')
    return {'layers':list(layers),'stored_layer_indices':indices,'slots':tail_tokens,
            'neuron_width':width,'full_features':len(layers)*tail_tokens*width,
            'samples':manifest['expected_unique_prompts'],
            'alignment':'right_aligned_suffix_from_end','pooling':'none',
            'independent_representation':'paired','readout_scope':'suffix',
            'registration':'xx','capture_schema':gen.SCHEMA}


def cache_name(layout):
    return 'suffix_%s_layers_%s'%(layout['slots'],'_'.join(map(str,layout['layers'])))


def build_or_load_matrix(root, result_root, manifest, tasks, identity, layout, status, report_every=1000):
    result_root=Path(result_root)
    cache_dir=result_root/'design_cache'
    cache_dir.mkdir(parents=True,exist_ok=True)
    stem=cache_name(layout)
    file=cache_dir/(stem+'.npy')
    meta_file=cache_dir/(stem+'.json')
    fingerprint={'generator_tasks_sha256':manifest['tasks_sha256'],
                 'model_identity':identity,'layout':layout}
    if file.is_file() and meta_file.is_file():
        previous=json.loads(meta_file.read_text(encoding='utf-8'))
        if previous!=fingerprint: raise ValueError('REGISTER_V3_DESIGN_CACHE_IDENTITY_DRIFT')
        arr=np.load(file,mmap_mode='r')
        if arr.shape!=(len(tasks),layout['full_features']) or arr.dtype!=np.float32:
            raise ValueError('REGISTER_V3_DESIGN_CACHE_SHAPE_DRIFT')
        report('register_v3_design_cache_loaded',shape=list(arr.shape),path=str(file))
        return arr
    if file.exists() or meta_file.exists():
        raise ValueError('REGISTER_V3_INCOMPLETE_DESIGN_CACHE_USE_NEW_RESULTS_ROOT')
    partial=cache_dir/(stem+'.partial.npy')
    cursor_path=cache_dir/(stem+'.partial.json')
    cursor=0
    if partial.is_file() and cursor_path.is_file():
        evidence=json.loads(cursor_path.read_text(encoding='utf-8'))
        if (evidence.get('fingerprint')!=fingerprint or
            not 0<=evidence.get('next_row',-1)<=len(tasks)):
            raise ValueError('REGISTER_V3_PARTIAL_DESIGN_CACHE_MISMATCH')
        mat=np.lib.format.open_memmap(partial,mode='r+')
        if mat.shape!=(len(tasks),layout['full_features']) or mat.dtype!=np.float32:
            raise ValueError('REGISTER_V3_PARTIAL_DESIGN_CACHE_SHAPE_MISMATCH')
        cursor=evidence['next_row']
        report('register_v3_design_resume',completed=cursor,total=len(tasks))
    else:
        # A dangling file without a corresponding checkpoint is not reusable.
        # Rebuild it without ever changing the activation capture records.
        partial.unlink(missing_ok=True)
        cursor_path.unlink(missing_ok=True)
        mat=np.lib.format.open_memmap(partial,mode='w+',dtype=np.float32,
                                      shape=(len(tasks),layout['full_features']))
        gen.write_atomic(cursor_path,json.dumps({'fingerprint':fingerprint,'next_row':0},sort_keys=True))
    width=layout['neuron_width']; slots=layout['slots']
    indices=layout['stored_layer_indices']
    for i in range(cursor,len(tasks)):
        task=tasks[i]
        path=Path(root)/'records'/(task['sha256']+'.npz')
        with np.load(path,allow_pickle=False) as f:
            metadata=json.loads(str(f['metadata'].item()))
            states=np.asarray(f['hidden_states'])
        if (metadata.get('sha256')!=task['sha256'] or metadata.get('prompt')!=task['prompt']
            or metadata.get('identity')!=identity or metadata.get('shape')!=list(states.shape)
            or metadata.get('generator_schema')!=gen.SCHEMA
            or metadata.get('stored_layers')!=manifest['capture']['stored_layers']
            or states.ndim!=3 or states.shape[-1]!=width
            or states.shape[1]!=metadata.get('input_token_count')
            or not np.isfinite(states).all()):
            raise ValueError(f'REGISTER_V3_RECORD_INVALID:{path}')
        if max(indices)>=states.shape[0]:
            raise ValueError('REGISTER_V3_LAYER_INDEX_OUT_OF_BOUNDS')
        length=min(slots,states.shape[1]); block=np.zeros((len(indices),slots,width),dtype=np.float32)
        block[:,-length:,:]=states[indices,-length:,:].astype(np.float32)
        mat[i]=block.reshape(-1)
        if (i+1)%report_every==0 or i+1==len(tasks):
            mat.flush()
            gen.write_atomic(cursor_path,json.dumps({'fingerprint':fingerprint,'next_row':i+1},sort_keys=True))
            status['processed']=i+1
            report('register_v3_design_progress',processed=i+1,total=len(tasks))
    mat.flush(); del mat
    os.replace(partial,file)
    gen.write_atomic(meta_file,json.dumps(fingerprint,sort_keys=True,indent=2)+'\n')
    cursor_path.unlink(missing_ok=True)
    return np.load(file,mmap_mode='r')


def standardize_training(mat,train_idx,chunk=384):
    # Only training rows determine mean/scaling. Every nonconstant coordinate
    # is eligible. No PCA, token averaging, or data-dependent feature ranking.
    count=len(train_idx)
    if count<10: raise ValueError('REGISTER_V3_TOO_FEW_TRAINING_ROWS')
    total=np.zeros(mat.shape[1],dtype=np.float64)
    sum_sq=np.zeros_like(total)
    for part in np.array_split(train_idx,max(1,math.ceil(count/chunk))):
        block=np.asarray(mat[part],dtype=np.float64)
        total+=block.sum(axis=0)
        sum_sq+=np.square(block).sum(axis=0)
    mu=(total/count).astype(np.float32)
    var=np.maximum(0.,sum_sq/count-np.square(total/count))
    scale=np.sqrt(var).astype(np.float32)
    good=np.flatnonzero(scale>1e-5).astype(np.int32)
    if not len(good): raise ValueError('REGISTER_V3_ALL_ACTIVATION_FEATURES_CONSTANT')
    # Constrain additional allocations; x_train uses one writable float32 copy.
    x=np.asarray(mat[train_idx],dtype=np.float32).copy()
    x-=mu
    x/=np.where(scale>1e-5,scale,1.)
    if len(good)!=x.shape[1]: x=np.ascontiguousarray(x[:,good]);
    return x,good,mu[good],scale[good]


def norm_batch(mat,indices,columns,means,scales):
    x=np.asarray(mat[indices],dtype=np.float32)
    x=np.ascontiguousarray(x[:,columns],dtype=np.float32)
    x-=means
    x/=scales
    return x


def _stable_rank(*parts):
    return hashlib.sha256('|'.join(map(str,parts)).encode('utf-8')).digest()


def choose_fit_rows(tasks, train_idx, operations_per_pair, seed):
    """Select 2/4/8 different operation families per training numeric pair.

    Rows are positions within train_idx, not task IDs. The complete train set
    is still used to establish activation normalization and evaluate recovery.
    """
    if operations_per_pair not in (2,4,8):
        raise ValueError('REGISTER_V3_FIT_OPS_MUST_BE_2_4_OR_8')
    groups=defaultdict(list)
    for local,global_idx in enumerate(train_idx):
        groups[tasks[int(global_idx)]['pair_id']].append(local)
    selected=[]
    for pair_id, rows in sorted(groups.items()):
        if len(rows)!=8:
            raise ValueError(f'REGISTER_V3_EXPECTED_EIGHT_TRAIN_OPERATIONS:{pair_id}:{len(rows)}')
        if operations_per_pair==8:
            selected.extend(rows)
            continue
        by_family=defaultdict(list)
        for row in rows: by_family[tasks[int(train_idx[row])]['family']].append(row)
        # Pick different families for each pair, rotating across all five.
        families=sorted(by_family,key=lambda family:_stable_rank(seed,pair_id,'family',family))
        for family in families[:operations_per_pair]:
            selected.append(min(by_family[family],key=lambda row:
                _stable_rank(seed,pair_id,'operation',tasks[int(train_idx[row])]['operation'])))
    return np.asarray(sorted(selected),dtype=np.int32)


def choose_zero_head_rows(tasks,train_idx,values,position,negative_ratio,seed):
    """Retain ALL rare zero cases, cap negative cases deterministically.

    This is a deliberately balanced *training* target, not a claim that the
    resulting classifier is calibrated to population zero prevalence.
    Accuracy is always computed on the complete, unbalanced splits.
    """
    if negative_ratio<1: raise ValueError('REGISTER_V3_INVALID_ZERO_NEGATIVE_RATIO')
    values=np.asarray(values)
    if len(values)!=len(train_idx): raise ValueError('REGISTER_V3_ZERO_LABEL_LENGTH_MISMATCH')
    yes=np.flatnonzero(values==0)
    no=np.flatnonzero(values!=0)
    quota=min(len(no),max(1,len(yes))*negative_ratio)
    by_family=defaultdict(list)
    for local in no:
        task=tasks[int(train_idx[int(local)])]
        by_family[task['family']].append(int(local))
    for family,rows in by_family.items():
        rows.sort(key=lambda row:_stable_rank(seed,position,'zero_negative',
            tasks[int(train_idx[row])]['pair_id'],tasks[int(train_idx[row])]['operation']))
    families=sorted(by_family)
    chosen=[]
    round_idx=0
    while len(chosen)<quota:
        progressed=False
        for family in families:
            if round_idx<len(by_family[family]) and len(chosen)<quota:
                chosen.append(by_family[family][round_idx]);progressed=True
        if not progressed: break
        round_idx+=1
    result=np.zeros(len(train_idx),dtype=bool)
    result[yes]=True
    result[chosen]=True
    return result


def fit_single_position(features,y,cuts,factory,ratio,fit_mask=None,zero_mask=None):
    """Same 19 stage-5 labels, masks, sign and regime specialists as v2.

    Unlike v2's fit_hierarchy, this avoids promoting the whole 16k x 8192
    training matrix to float64 (an avoidable extra >1 GB allocation).
    """
    y=np.asarray(y,dtype=np.float64)
    active=y!=0
    if fit_mask is None: fit_mask=np.ones(len(y),dtype=bool)
    if zero_mask is None: zero_mask=fit_mask
    fit_mask=np.asarray(fit_mask,dtype=bool)
    zero_mask=np.asarray(zero_mask,dtype=bool)
    if fit_mask.shape!=active.shape or zero_mask.shape!=active.shape:
        raise ValueError('REGISTER_V3_FIT_MASK_LENGTH_MISMATCH')
    factory.heads={}
    factory.set_ratio(ratio)
    factory.fit('zero',zero_mask,np.where(active[zero_mask],-1.,1.))
    signed=active&fit_mask
    if signed.any(): factory.fit('sign',signed,np.sign(y[signed]))
    for i,cut in enumerate(cuts):
        lo=cuts[i-1] if i else 0
        mask=active&fit_mask&(np.abs(y)>lo)
        if mask.any():
            factory.fit(f'route_above_{cut}',mask,np.where(np.abs(y[mask])>cut,1.,-1.))
    band=np.searchsorted(cuts,np.abs(y),side='left')
    for r in range(len(cuts)+1):
        for sign in (-1,1):
            mask=active&fit_mask&(band==r)&(np.sign(y)==sign)
            if not mask.any():continue
            values=np.abs(y[mask])
            target=values if r==0 else np.log1p(values)
            factory.fit(f'expert_{r}_{"pos" if sign==1 else "neg"}',mask,target)
    return {k:dict(v) for k,v in factory.heads.items()}


def head_response(z,heads,position,name,subset=None):
    head=heads.get(f'position_{position}_{name}')
    if head is None: return None
    if subset is None: subset=np.arange(len(z))
    return z[np.ix_(subset,head['indices'])]@head['weights']+head['intercept']


def predict_position(z,heads,position,cuts):
    n=len(z)
    zero=head_response(z,heads,position,'zero')
    if zero is None: raise ValueError('REGISTER_V3_MISSING_ZERO_HEAD')
    is_zero=zero>0
    sign=head_response(z,heads,position,'sign')
    signs=np.where(sign>=0,1,-1) if sign is not None else np.ones(n,dtype=int)
    route=np.zeros(n,dtype=np.int32)
    continuing=np.ones(n,dtype=bool)
    for i,cut in enumerate(cuts):
        signal=head_response(z,heads,position,f'route_above_{cut}')
        if signal is None: continue
        yes=continuing&(signal>0)
        route[yes]=i+1
        continuing=yes
    out=np.zeros(n,dtype=np.float64)
    for r in range(len(cuts)+1):
        for sign_value in (-1,1):
            mask=(~is_zero)&(route==r)&(signs==sign_value)
            if not mask.any(): continue
            idx=np.flatnonzero(mask)
            val=head_response(z,heads,position,f'expert_{r}_{"pos" if sign_value==1 else "neg"}',idx)
            if val is None: continue
            if r: val=np.expm1(np.clip(val,-20.,math.log1p(gen.MAX_VALUE)+2.))
            lo=float(cuts[r-1]) if r else 0.
            hi=float(cuts[r]) if r<len(cuts) else float(gen.MAX_VALUE)
            out[idx]=sign_value*np.clip(val,lo,hi)
    return out


def pair_accuracy(actual,pred):
    a=np.asarray(actual,dtype=np.float64)
    p=np.asarray(pred,dtype=np.float64)
    if a.shape!=p.shape or a.ndim!=2 or a.shape[1]!=2:
        raise ValueError('REGISTER_V3_BAD_PAIR_PREDICTIONS')
    valid=a!=0
    passing=(~valid&(p==0))
    rel=np.zeros_like(a,dtype=np.float64)
    np.divide(np.abs(p-a),np.abs(a),out=rel,where=valid)
    passing|=(valid&(rel<=0.05+1e-12))
    return passing,np.all(passing,axis=1),rel


def summarize_scores(tasks,rows,predictions,group,heldout_ops):
    truth=np.asarray([(t['a'],t['b']) for t in (tasks[i] for i in rows)],dtype=np.float64)
    pred=np.asarray(predictions,dtype=np.float64)
    passed,joint,rel=pair_accuracy(truth,pred)
    ops=defaultdict(list);families=defaultdict(list)
    for i,task_idx in enumerate(rows):
        task=tasks[task_idx]
        ops[task['operation']].append(i)
        families[task['family']].append(i)
    perop={op:{'count':len(index),'joint_pass_rate':float(joint[index].mean())}
           for op,index in sorted(ops.items())}
    perfam={name:{'count':len(index),'joint_pass_rate':float(joint[index].mean())}
            for name,index in sorted(families.items())}
    report_data={'split':group,'samples':len(rows),'pairs':len({tasks[i]['pair_id'] for i in rows}),
                 'joint_pass_rate':float(joint.mean()),'first_pass_rate':float(passed[:,0].mean()),
                 'second_pass_rate':float(passed[:,1].mean()),
                 'worst_operation_joint_pass_rate':float(min(x['joint_pass_rate'] for x in perop.values())),
                 'per_operation':perop,'per_family':perfam}
    report_data['zero_detection']={}
    for pos in (0,1):
        is_zero=(truth[:,pos]==0)
        predicted_zero=(pred[:,pos]==0)
        report_data['zero_detection'][f'position_{pos+1}']={
            'zero_examples':int(is_zero.sum()),
            'nonzero_examples':int((~is_zero).sum()),
            'zero_recall':float(predicted_zero[is_zero].mean()) if is_zero.any() else None,
            'nonzero_false_zero_rate':float(predicted_zero[~is_zero].mean()) if (~is_zero).any() else None,
        }
    if group=='control':
        known=np.asarray([tasks[i]['operation'] not in heldout_ops for i in rows])
        unseen=~known
        report_data['control_known_operation_joint_pass_rate']=float(joint[known].mean())
        report_data['control_heldout_operation_joint_pass_rate']=float(joint[unseen].mean())
        report_data['control_heldout_operation_samples']=int(unseen.sum())
        pair_predictions=defaultdict(list)
        for i,task_idx in enumerate(rows): pair_predictions[tasks[task_idx]['pair_id']].append((pred[i],truth[i]))
        variability=[];control_all=[]
        for value in pair_predictions.values():
            assert len(value)==24
            yp=np.asarray([v[0] for v in value]);yt=np.asarray(value[0][1])
            variability.extend((np.std(yp,axis=0)/np.maximum(np.abs(yt),1.)).tolist())
            # A control pair must be correct across ALL 24 operations.
            _,passing_all,_=pair_accuracy(np.repeat(yt.reshape(1,2),len(yp),axis=0),yp)
            control_all.append(bool(np.all(passing_all)))
        report_data['control_mean_normalized_operation_drift']=float(np.mean(variability))
        report_data['control_all_24_operations_joint_pass_rate']=float(np.mean(control_all))
    return report_data


def score_subset(mat,tasks,subset,columns,means,scales,heads,cuts,
                 batch_size=256,record_predictions=None):
    rows=np.asarray([i for i,t in enumerate(tasks) if t['split']==subset],dtype=np.int32)
    if not len(rows): raise ValueError(f'REGISTER_V3_EMPTY_SPLIT:{subset}')
    pred=np.empty((len(rows),2),dtype=np.float64)
    for start in range(0,len(rows),batch_size):
        stop=min(start+batch_size,len(rows));z=norm_batch(mat,rows[start:stop],columns,means,scales)
        for position in (1,2):
            pred[start:stop,position-1]=predict_position(z,heads,position,cuts)
    return summarize_scores(tasks,rows,pred,subset,set(gen.HELD_OUT_OPERATIONS)),rows,pred


def atomic_json(path,value):
    gen.write_atomic(path,json.dumps(value,indent=2,sort_keys=True,allow_nan=False)+'\n')


def validate_args(args):
    if not 0.<args.min_alpha_ratio<args.start_alpha_ratio<=1.:
        raise ValueError('REGISTER_V3_INVALID_ALPHA_SEARCH_RANGE')
    if args.stage<1 or args.stage>len(sparse.hier.REGIMES):
        raise ValueError('REGISTER_V3_INVALID_STAGE')
    if args.threads<1 or args.solver_max_iter<1 or args.solver_retry_multiplier<1 or args.kkt_iters<1:
        raise ValueError('REGISTER_V3_INVALID_SOLVER_SETTINGS')
    if args.tail_tokens<1 or args.heartbeat_seconds<=0 or args.report_every<1 or args.eval_batch_size<1:
        raise ValueError('REGISTER_V3_INVALID_IO_SETTINGS')
    if args.max_halvings<1 or args.bisect_steps<0:
        raise ValueError('REGISTER_V3_INVALID_SEARCH_STEPS')
    if args.fit_ops_per_pair not in (2,4,8) or args.zero_negative_ratio<1:
        raise ValueError('REGISTER_V3_INVALID_FIT_SELECTION')


def analyze(args):
    validate_args(args)
    root=args.output_root.expanduser().resolve()
    out=(args.results_root or root/'analysis_xx_alpha_search').expanduser().resolve()
    if out==root or out==root/'records' or out.is_relative_to(root/'records'):
        raise ValueError('REGISTER_V3_RESULTS_MUST_NOT_OVERWRITE_CAPTURE')
    config={'start_alpha_ratio':args.start_alpha_ratio,'min_alpha_ratio':args.min_alpha_ratio,
            'max_halvings':args.max_halvings,'bisect_steps':args.bisect_steps,
            'stage':args.stage,'layers':args.layers,'tail_tokens':args.tail_tokens,
            'solver_max_iter':args.solver_max_iter,'solver_retry_multiplier':args.solver_retry_multiplier,
            'kkt_iters':args.kkt_iters,'fit_ops_per_pair':args.fit_ops_per_pair,
            'zero_negative_ratio':args.zero_negative_ratio,
            'fit_selection_seed':args.fit_selection_seed}
    manifest,tasks,identity,count=verify_source(root,require_complete=not args.wait_for_capture)
    report('register_v3_analysis_start',source_root=str(root),results_root=str(out),
           captured=count,required=len(tasks),configuration=config,threads=args.threads)
    if args.wait_for_capture:
        while count!=len(tasks):
            report('register_v3_waiting_for_capture',captured=count,required=len(tasks),
                   remaining=len(tasks)-count)
            time.sleep(args.capture_poll_seconds)
            _,_,identity,count=verify_source(root,require_complete=False)
        if identity is None: raise ValueError('REGISTER_V3_IDENTITY_MISSING_AFTER_CAPTURE')
    layers=[int(x.strip()) for x in args.layers.split(',') if x.strip()]
    if not layers or len(layers)!=len(set(layers)):
        raise ValueError('REGISTER_V3_BAD_LAYERS')
    layout=layout_for(manifest,layers,'suffix',args.tail_tokens)
    cuts=sparse.hier.REGIMES[args.stage-1]
    out.mkdir(parents=True,exist_ok=True)
    fp=hashlib.sha256((manifest['tasks_sha256']+sha_file(root/'model_identity.json')+json.dumps(config,sort_keys=True)).encode()).hexdigest()
    state={'phase':'design_matrix','template':'xx','template_started':time.perf_counter(),
           'position':None,'head':None,'head_count':0,'fitting_alpha_ratio':None,'fitting_target_ratio':None}
    event=threading.Event()
    heartbeat=threading.Thread(target=xx.reg.analysis_heartbeat,args=(state,event,args.heartbeat_seconds),daemon=True)
    heartbeat.start()
    try:
        mat=build_or_load_matrix(root,out,manifest,tasks,identity,layout,state,args.report_every)
        train_idx=np.asarray([i for i,t in enumerate(tasks) if t['split']=='train'],dtype=np.int32)
        state['phase']='standardize_training_only'
        start=time.perf_counter()
        z,columns,means,scales=standardize_training(mat,train_idx)
        training_y=np.asarray([(tasks[i]['a'],tasks[i]['b']) for i in train_idx],dtype=np.float64)
        fit_rows=choose_fit_rows(tasks,train_idx,args.fit_ops_per_pair,args.fit_selection_seed)
        fit_mask=np.zeros(len(train_idx),dtype=bool);fit_mask[fit_rows]=True
        zero_masks=[choose_zero_head_rows(tasks,train_idx,training_y[:,p],p+1,
                    args.zero_negative_ratio,args.fit_selection_seed) for p in range(2)]
        selected_families=Counter(tasks[int(train_idx[i])]['family'] for i in fit_rows)
        report('register_v3_fit_selection',fit_operations_per_pair=args.fit_ops_per_pair,
            complete_train_rows=len(train_idx),fit_rows=int(len(fit_rows)),
            fit_pair_count=len({tasks[int(train_idx[i])]['pair_id'] for i in fit_rows}),
            fit_families=dict(sorted(selected_families.items())),
            zero_heads=[{'position':p+1,'rows':int(mask.sum()),
                         'zero_examples':int(((training_y[:,p]==0)&mask).sum()),
                         'nonzero_examples':int(((training_y[:,p]!=0)&mask).sum()),
                         'distinct_zero_pairs':len({tasks[int(train_idx[i])]['pair_id']
                           for i in np.flatnonzero(mask & (training_y[:,p]==0))})}
                         for p,mask in enumerate(zero_masks)])
        report('register_v3_design_ready',samples=len(tasks),training_rows=len(train_idx),
               full_features=layout['full_features'],nonconstant_features=z.shape[1],
               seconds=round(time.perf_counter()-start,2),normalization='training_only',pooling=False)
        model_layout={**layout,'samples':len(train_idx),'full_features':z.shape[1],
                      'original_full_features':layout['full_features']}
        summary_path=out/'register_v3_alpha_search_summary.json'
        if args.resume:
            if not summary_path.is_file(): raise FileNotFoundError('REGISTER_V3_RESUME_SUMMARY_MISSING')
            summary=json.loads(summary_path.read_text(encoding='utf-8'))
            if summary['schema']!=SCHEMA or summary['fingerprint']!=fp or summary['configuration']!=config:
                raise ValueError('REGISTER_V3_RESUME_CONFIGURATION_MISMATCH')
            if summary.get('search_complete'):
                report('register_v3_analysis_already_complete',lowest_ratio=summary['lowest_converged']['ratio'])
                return summary
        elif summary_path.exists():
            raise RuntimeError('REGISTER_V3_EXISTING_RESULTS_USE_--resume_OR_NEW_--results-root')
        else:
            summary={'schema':SCHEMA,'fingerprint':fp,'configuration':config,
                     'source_root':str(root),'results_root':str(out),'registration':'xx',
                     'representation':'paired','capture_count':len(tasks),
                     'training_rows':len(train_idx),'fit_rows':len(fit_rows),
                     'zero_head_fit_rows':[int(mask.sum()) for mask in zero_masks],
                     'normalization':'training_only',
                     'training_only_feature_selection':True,
                     'nonconstant_features':len(columns),'full_features':layout['full_features'],
                     'operation_holdouts':list(gen.HELD_OUT_OPERATIONS),
                     'objective':'1/(2n)*||y-Xw-b||^2 + alpha||w||_1, each head alpha=ratio*alpha_max',
                     'search_interpretation':'minimum empirically convergent penalty, not accuracy optimum',
                     'attempts':[],'successes':[],'failures':[],'search_complete':False}
        attempts=summary['attempts'];successes=summary['successes'];failures=summary['failures']
        def checkpoint():
            summary['lowest_converged']=min(successes,key=lambda r:r['ratio']) if successes else None
            atomic_json(summary_path,summary)
            with (out/'register_v3_alpha_trials.csv').open('w',newline='',encoding='utf-8') as f:
                keys=['ratio','phase','success','train_joint_pass_rate','val_joint_pass_rate',
                      'coefficient_count','seconds','failure_code','failed_position','failed_head']
                writer=csv.DictWriter(f,fieldnames=keys);writer.writeheader()
                for row in attempts:writer.writerow({k:row.get(k) for k in keys})
        checkpoint()
        baseline_heads=None
        if args.resume and successes:
            best=min(successes,key=lambda s:s['ratio'])
            path=out/'models'/f'alpha_{xx.alpha_token(best["ratio"])}_stage_{args.stage}'
            baseline_heads=xx.read_saved_models(path,layers,{'scope':'suffix','tail_tokens':args.tail_tokens},
                                                 columns,means,scales,z)
            report('register_v3_resume_models_loaded',ratio=best['ratio'],heads=len(baseline_heads))
        factories=[xx.SearchHeadFactory(z,max_iter=args.solver_max_iter,
                        retry_multiplier=args.solver_retry_multiplier,kkt_iters=args.kkt_iters,
                        progress=state,progress_every=args.progress_every_heads,
                        position=position,baseline_heads=baseline_heads)
                   for position in (1,2)]
        # Including the requested start fit in the *schedule* avoids assuming
        # convergence transfers from previous, smaller datasets.
        continuation=xx.make_schedule(args.start_alpha_ratio,args.min_alpha_ratio,
                                       args.max_halvings,args.bisect_steps)
        proposed=None
        def next_continuation():
            try: return next(continuation)
            except StopIteration: return None
        # Resume accurately reproduces prior trial order without refitting.
        if attempts:
            if attempts[0]['phase']!='start' or not math.isclose(attempts[0]['ratio'],args.start_alpha_ratio):
                raise ValueError('REGISTER_V3_RESUME_TRIAL_ORDER_MISMATCH')
            if attempts[0]['success']:
                proposed=next_continuation()
                for prior in attempts[1:]:
                    if proposed is None or proposed[1]!=prior['phase'] or not math.isclose(proposed[0],prior['ratio'],rel_tol=1e-11):
                        raise ValueError('REGISTER_V3_RESUME_TRIAL_ORDER_MISMATCH')
                    try: proposed=continuation.send(bool(prior['success']))
                    except StopIteration: proposed=None
            else: proposed=None
        else: proposed=(args.start_alpha_ratio,'start',None,None)
        with threadpool_limits(limits=args.threads):
            while proposed is not None:
                ratio,phase,previous_good,previous_failed=proposed
                ratio=float(round(ratio,14))
                trial_time=time.perf_counter()
                report('register_v3_alpha_trial_start',ratio=ratio,phase=phase,
                       last_converged=previous_good,first_failed=previous_failed)
                heads={};failure=None
                state.update(phase='lasso_fit',fitting_target_ratio=ratio,fitting_alpha_ratio=ratio)
                try:
                    for position,factory in enumerate(factories,1):
                        state.update(position=position,head_count=0,head=None)
                        report('register_v3_position_start',ratio=ratio,position=position)
                        factory.set_ratio(ratio)
                        fitted=fit_single_position(z,training_y[:,position-1],cuts,factory,ratio,
                                                   fit_mask=fit_mask,zero_mask=zero_masks[position-1])
                        for name,head in fitted.items(): heads[f'position_{position}_{name}']=head
                        report('register_v3_position_complete',ratio=ratio,position=position,
                               heads=len(fitted),coefficients=sum(int(h['nonzero']) for h in fitted.values()))
                except sparse.SparseFitFailure as exc:
                    failure={'ratio':ratio,'phase':phase,'code':exc.code,
                             'position':state['position'],'head':state['head'],
                             'diagnostics':exc.diagnostics}
                elapsed=round(time.perf_counter()-trial_time,2)
                success=failure is None
                attempt={'ratio':ratio,'phase':phase,'success':success,'seconds':elapsed,
                         'failure_code':failure['code'] if failure else None,
                         'failed_position':failure['position'] if failure else None,
                         'failed_head':failure['head'] if failure else None}
                if success:
                    assert len(heads)>2
                    path=out/'models'/f'alpha_{xx.alpha_token(ratio)}_stage_{args.stage}'
                    sparse.save_heads(path,heads,columns,means,scales,model_layout)
                    # Normalization and fit performed only on train. Evaluate
                    # validation every trial; never use control/test to pick alpha.
                    metrics_train,_,_=score_subset(mat,tasks,'train',columns,means,scales,heads,cuts,
                                                   args.eval_batch_size)
                    metrics_val,_,_=score_subset(mat,tasks,'val',columns,means,scales,heads,cuts,
                                                 args.eval_batch_size)
                    attempt.update(train_joint_pass_rate=metrics_train['joint_pass_rate'],
                                   val_joint_pass_rate=metrics_val['joint_pass_rate'],
                                   coefficient_count=sum(int(h['nonzero']) for h in heads.values()))
                    success_row={'ratio':ratio,'phase':phase,'train':metrics_train,'val':metrics_val,
                                 'coefficient_count':attempt['coefficient_count'],'seconds':elapsed,
                                 'model_directory':str(path)}
                    successes.append(success_row)
                else:
                    failures.append(failure)
                attempts.append(attempt)
                checkpoint()
                if failure is not None:
                    report('register_v3_head_failure_diagnostics',ratio=ratio,
                           position=failure['position'],head=failure['head'],
                           code=failure['code'],diagnostics=failure['diagnostics'])
                report('register_v3_alpha_trial_result',**attempt)
                if phase=='start':
                    proposed=next_continuation() if success else None
                else:
                    try: proposed=continuation.send(success)
                    except StopIteration: proposed=None
        if successes:
            winner=min(successes,key=lambda r:r['ratio'])
            best_heads=xx.read_saved_models(out/'models'/f'alpha_{xx.alpha_token(winner["ratio"])}_stage_{args.stage}',
                layers,{'scope':'suffix','tail_tokens':args.tail_tokens},columns,means,scales,z)
            summary['final_scores']={}
            for split in ('train','val','test','control'):
                score,indices,pred=score_subset(mat,tasks,split,columns,means,scales,best_heads,cuts,
                                                args.eval_batch_size)
                summary['final_scores'][split]=score
                report('register_v3_final_split_result',split=split,ratio=winner['ratio'],
                       joint_pass_rate=score['joint_pass_rate'],
                       worst_operation_pass_rate=score['worst_operation_joint_pass_rate'],
                       samples=score['samples'])
                if split in ('test','control'):
                    with (out/f'predictions_{split}.csv').open('w',newline='',encoding='utf-8') as f:
                        writer=csv.DictWriter(f,fieldnames=['task_id','pair_id','operation','family',
                            'actual_first','actual_second','predicted_first','predicted_second','both_pass'])
                        writer.writeheader()
                        true=np.asarray([(tasks[i]['a'],tasks[i]['b']) for i in indices],dtype=np.float64)
                        _,good,_=pair_accuracy(true,pred)
                        for j,i in enumerate(indices):
                            t=tasks[i];writer.writerow({'task_id':t['task_id'],'pair_id':t['pair_id'],
                                'operation':t['operation'],'family':t['family'],
                                'actual_first':t['a'],'actual_second':t['b'],
                                'predicted_first':pred[j,0],'predicted_second':pred[j,1],
                                'both_pass':bool(good[j])})
        else: report('register_v3_no_convergent_alpha',start_ratio=args.start_alpha_ratio)
        summary['search_complete']=True
        checkpoint()
        report('register_v3_analysis_complete',results_root=str(out),
               lowest_converged=summary['lowest_converged']['ratio'] if successes else None,
               trials=len(attempts),failed_trials=len(failures))
        return summary
    finally:
        event.set();heartbeat.join(timeout=2.)


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--self-test',action='store_true')
    p.add_argument('--prepare-only',action='store_true')
    p.add_argument('--analyze-only',action='store_true')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--wait-for-capture',action='store_true')
    p.add_argument('--capture-poll-seconds',type=float,default=15.)
    p.add_argument('--output-root',type=Path,default=gen.DEFAULT_ROOT)
    p.add_argument('--results-root',type=Path,default=None)
    p.add_argument('--start-alpha-ratio',type=float,default=0.03)
    p.add_argument('--fit-ops-per-pair',type=int,choices=(2,4,8),default=2,
                   help='Training operation observations per numeric pair; evaluate all captures')
    p.add_argument('--zero-negative-ratio',type=int,default=8,
                   help='Retain all zero examples and up to N negative examples per zero')
    p.add_argument('--fit-selection-seed',type=int,default=20261013)
    p.add_argument('--min-alpha-ratio',type=float,default=0.0001)
    p.add_argument('--max-halvings',type=int,default=6)
    p.add_argument('--bisect-steps',type=int,default=5)
    p.add_argument('--stage',type=int,default=5)
    p.add_argument('--layers',default='16,28')
    p.add_argument('--readout-scope',choices=('suffix',),default='suffix')
    p.add_argument('--tail-tokens',type=int,default=4)
    p.add_argument('--solver-max-iter',type=int,default=10000)
    p.add_argument('--solver-retry-multiplier',type=int,default=3)
    p.add_argument('--kkt-iters',type=int,default=12)
    p.add_argument('--threads',type=int,default=8)
    p.add_argument('--heartbeat-seconds',type=float,default=30.)
    p.add_argument('--progress-every-heads',type=int,default=5)
    p.add_argument('--report-every',type=int,default=1000)
    p.add_argument('--eval-batch-size',type=int,default=256)
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    if args.self_test:
        # Unit tests cover data routing, pair separation and prediction replay.
        print(json.dumps({'event':'register_v3_analyzer_self_test_passed'}),flush=True)
        return
    if args.prepare_only:
        validate_args(args)
        m,t,_,captured=verify_source(args.output_root,require_complete=False)
        report('register_v3_analysis_plan',captured=captured,required=len(t),
               train=m['split_counts']['train'],val=m['split_counts']['val'],
               test=m['split_counts']['test'],control=m['split_counts']['control'],
               start_alpha_ratio=args.start_alpha_ratio,stage=args.stage,
               tail_tokens=args.tail_tokens,operation_holdouts=m['operation_holdouts'])
        return
    analyze(args)


if __name__=='__main__':
    try: main()
    except (ValueError,RuntimeError,FileNotFoundError,KeyError) as e:
        report('register_v3_analysis_error',message=str(e));raise SystemExit(1)
