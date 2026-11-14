#!/usr/bin/env python3
"""Read-only GAMER radius recovery: within-context vs cross-context numerical coding.

Operates on the frozen activation captures produced by head-100-radius.
No Qwen/C0 forward passes, weights, or training artifacts are changed.

All radius probes are synthetic textual interventions, not valid certified
answers. Within-context decoders know the zero-intervention activation;
cross-context decoders hold out entire games and learn basis/scaling/readouts
using training games ONLY. The nonlinear probe is an anchored RBF kernel.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np

SCHEMA='nanojev.gamer.v1.radius-recovery.v1'


def _sha256(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()


def _score(y, prediction):
    truth=np.asarray(y,dtype=np.float64).reshape(-1)
    pred=np.asarray(prediction,dtype=np.float64).reshape(-1)
    if truth.size==0:return {'cases':0,'r2_vs_zero':None,'rmse':None,'mae':None,'correct_sign_fraction':None,'within_one_fraction':None}
    denom=float(truth@truth)
    err=truth-pred
    return {'cases':int(len(truth)),
            'r2_vs_zero':float(1-(err@err)/denom) if denom>1e-20 else None,
            'rmse':float(np.sqrt(np.mean(err*err))),
            'mae':float(np.mean(np.abs(err))),
            'correct_sign_fraction':float(np.mean(np.sign(truth)==np.sign(pred))),
            'within_one_fraction':float(np.mean(np.abs(err)<=1.0))}


def _distance_squared(x,z=None):
    x=np.asarray(x,dtype=np.float64)
    z=x if z is None else np.asarray(z,dtype=np.float64)
    squared=np.sum(x*x,axis=1)[:,None]+np.sum(z*z,axis=1)[None,:]-2*(x@z.T)
    return np.maximum(squared,0)


def _bandwidth(train):
    d2=_distance_squared(train)
    positive=d2[np.triu_indices(len(train),1)]
    positive=positive[positive>1e-12]
    return float(1/max(np.median(positive),1e-12)) if len(positive) else None


def _local_rbf_predict(x,y,heldout,alpha=0.05):
    # All operations use TRAIN values only for kernel scale and fitting.
    chosen=np.array([i for i in range(len(y)) if i!=heldout],dtype=int)
    training=x[chosen]
    gamma=_bandwidth(training)
    if gamma is None:return 0.0
    k=np.exp(-gamma*_distance_squared(training))
    k.flat[::len(chosen)+1]+=alpha
    # kernel ridge is a diagnostic decoder, not a neural-network update
    coefficients=np.linalg.solve(k,y[chosen].astype(np.float64))
    return float(np.exp(-gamma*_distance_squared(x[heldout:heldout+1],training))[0]@coefficients)


def _within_context(arr,values):
    c,n,w=arr.shape;z=int(np.flatnonzero(values==0)[0])
    y=[];linear=[];nonlinear=[];per_context=[]
    for i in range(c):
        delta=arr[i].astype(np.float64)-arr[i,z].astype(np.float64)
        p_linear=[];p_nonlinear=[];labels=[]
        for j,v in enumerate(values):
            if j==z:continue
            train=np.array([k for k in range(n) if k!=j and k!=z],dtype=int)
            train_y=values[train].astype(np.float64)
            slope=(train_y@delta[train])/float(train_y@train_y)
            norm=float(slope@slope)
            p=float(delta[j]@slope/norm) if norm>1e-20 else 0.0
            p_linear.append(p)
            p_nonlinear.append(_local_rbf_predict(delta,values,j))
            labels.append(float(v))
        y.extend(labels);linear.extend(p_linear);nonlinear.extend(p_nonlinear)
        per_context.append({'context':int(i),'linear':_score(labels,p_linear),
                            'nonlinear':_score(labels,p_nonlinear)})
    return {'linear':_score(y,linear),'nonlinear':_score(y,nonlinear),
            'per_context':per_context},(np.asarray(linear),np.asarray(nonlinear))


def _zero_crossing(arr,values):
    z=int(np.flatnonzero(values==0)[0]); c,n,w=arr.shape
    radius=set(map(int,values));items=[];adj=[]
    for a in sorted(x for x in radius if x>0 and -x in radius):
        positive=int(np.flatnonzero(values==a)[0]);negative=int(np.flatnonzero(values==-a)[0]);
        for i in range(c):
            left=np.asarray(arr[i,negative],dtype=np.float64)-arr[i,z]
            right=np.asarray(arr[i,positive],dtype=np.float64)-arr[i,z]
            ln=float(np.linalg.norm(left));rn=float(np.linalg.norm(right));denom=ln+rn
            midpoint=float(np.linalg.norm(left+right)/denom) if denom>1e-12 else None
            cosine=float(left@right/(ln*rn)) if ln*rn>1e-12 else None
            item={'context':i,'radius':int(a),'zero_midpoint_error_ratio':midpoint,
                  'opposite_direction_cosine':cosine,
                  'negative_radius_displacement':ln,'positive_radius_displacement':rn}
            items.append(item)
            if a==1:adj.append(item)
    def summarize(records):
        vals=[x['zero_midpoint_error_ratio'] for x in records if x['zero_midpoint_error_ratio'] is not None]
        cos=[x['opposite_direction_cosine'] for x in records if x['opposite_direction_cosine'] is not None]
        return {'matched_pairs':len(records),'nonconstant_pairs':len(vals),
                'mean_zero_midpoint_error_ratio':float(np.mean(vals)) if vals else None,
                'mean_signed_opposite_cosine':float(np.mean(cos)) if cos else None,
                'interpretation':'Linear signed numerical displacement has midpoint ratio 0 and opposite cosine -1; these measure geometry, not correct game zero predictions.'}
    return {'adjacent_radius_1':summarize(adj),'all_symmetric_radii':summarize(items)},items


def _anchors(arr,values,train_contexts):
    z=int(np.flatnonzero(values==0)[0]); delta=arr[train_contexts].astype(np.float32)-arr[train_contexts,z:z+1].astype(np.float32)
    y=np.asarray(values,dtype=np.float32)
    odd=np.einsum('n,cnd->cd',y,delta,optimize=True)/(y@y)
    # Even response around zero, a second basis for nonlinear curvature.
    positive=int(np.flatnonzero(values==1)[0]);negative=int(np.flatnonzero(values==-1)[0])
    even=0.5*(delta[:,positive,:]+delta[:,negative,:])
    result=np.concatenate((odd,even),axis=0)
    norm=np.linalg.norm(result,axis=1)
    result=result[norm>1e-10]
    return (result / np.linalg.norm(result,axis=1,keepdims=True)) if len(result) else result


def _train_basis(arr,values,train_contexts,rank):
    anchors=_anchors(arr,values,train_contexts)
    w=arr.shape[-1]
    if len(anchors)==0:return np.zeros((w,0),dtype=np.float32)
    gram=np.asarray(anchors@anchors.T,dtype=np.float64)
    eig,vec=np.linalg.eigh(gram)
    order=np.argsort(eig)[::-1]
    top=max(float(eig[order[0]]),1e-20)
    ids=[i for i in order if eig[i]>max(1e-9,top*1e-7)][:rank]
    if not ids:return np.zeros((w,0),dtype=np.float32)
    # right singular vectors (orthonormal) from TRAIN contexts only
    basis=(vec[:,ids].T@anchors)/np.sqrt(eig[ids])[:,None]
    return np.asarray(basis.T,dtype=np.float32)


def _pooled_examples(arr,values,contexts,basis):
    z=int(np.flatnonzero(values==0)[0]);n=len(values)
    centered=arr[contexts].astype(np.float32)-arr[contexts,z:z+1].astype(np.float32)
    matrix=centered.reshape(len(contexts)*n,-1)
    features=(matrix@basis).astype(np.float64)
    ys=np.tile(values,len(contexts)).astype(np.float64)
    return features,ys


def _cross_predict(train_x,train_y,test_x,*,label_shuffle=None,rng=None):
    n,d=train_x.shape
    if d==0:return np.zeros(len(test_x)),np.zeros(len(test_x))
    scale=np.std(train_x,axis=0)
    scale=np.maximum(scale,1e-7)
    x=train_x/scale;t=test_x/scale
    target=train_y.copy()
    if label_shuffle is not None:
        # Shuffle within each training context only, leave the zero anchor 0.
        n_values=label_shuffle
        for offset in range(0,len(target),n_values):
            nonzero=np.flatnonzero(target[offset:offset+n_values]!=0)+offset
            target[nonzero]=rng.permutation(target[nonzero])
    # Linear regularized projection. No intercept: zero intervention is anchor.
    coef=np.linalg.solve(x.T@x+np.eye(d),x.T@target)
    linear=t@coef
    gamma=_bandwidth(x)
    if gamma is None:return linear,np.zeros(len(t))
    # Anchored RBF kernel f(0)=0. All bandwidth choices use training cases.
    def anchored(a,b):
        base=np.exp(-gamma*_distance_squared(a,b))
        return base-np.exp(-gamma*np.sum(a*a,axis=1))[:,None] - np.exp(-gamma*np.sum(b*b,axis=1))[None,:] + 1.0
    gram=anchored(x,x)
    gram.flat[::n+1]+=0.20
    weights=np.linalg.solve(gram,target)
    return linear,anchored(t,x)@weights


def _cross_context(arr,values,*,folds=4,rank=12,controls=1,seed=47):
    c,n,w=arr.shape
    rng=np.random.default_rng(seed)
    shuffled=rng.permutation(c)
    group=np.array_split(shuffled,min(folds,c))
    pred_linear=np.zeros((c,n),dtype=np.float64)
    pred_rbf=np.zeros((c,n),dtype=np.float64)
    preds_null=[np.zeros((c,n),dtype=np.float64) for _ in range(controls)]
    fold_records=[]
    for k,holdout in enumerate(group):
        train=np.sort(np.setdiff1d(np.arange(c),holdout))
        if len(train)<3:raise ValueError('GAMER_RECOVERY_TOO_FEW_TRAIN_CONTEXTS')
        basis=_train_basis(arr,values,train,rank)
        train_x,train_y=_pooled_examples(arr,values,train,basis)
        test_x,test_y=_pooled_examples(arr,values,holdout,basis)
        a,b=_cross_predict(train_x,train_y,test_x)
        pred_linear[holdout]=a.reshape(len(holdout),n)
        pred_rbf[holdout]=b.reshape(len(holdout),n)
        for j in range(controls):
            random=np.random.default_rng(seed+1009*(j+1)+k)
            # This control keeps TRAIN-learned projection fixed but shuffles
            # TRAIN numeric labels for the readout. Held-out labels untouched.
            _,nul=_cross_predict(train_x,train_y,test_x,label_shuffle=n,rng=random)
            preds_null[j][holdout]=nul.reshape(len(holdout),n)
        fold_records.append({'fold':k,'train_contexts':train.tolist(),
                             'heldout_contexts':holdout.tolist(),
                             'basis_rank_effective':basis.shape[1]})
    nonzero=values!=0
    y=np.broadcast_to(values,(c,n))[:,nonzero].ravel()
    linear=pred_linear[:,nonzero].ravel()
    nonlinear=pred_rbf[:,nonzero].ravel()
    null=[_score(y,p[:,nonzero].ravel()) for p in preds_null]
    return {'linear':_score(y,linear),'nonlinear':_score(y,nonlinear),
            'shuffled_training_labels_nonlinear':null,
            'folds':fold_records,'numeric_zero_anchor_from_heldout_context':True,
            'basis_train_games_only':True,'note':'Training-game slope/curvature basis is fitted without the held-out game. Held-out zero intervention is supplied to center its activations. Shuffled control holds basis fixed; it tests decoder-label association, not basis selection.'},(pred_linear,pred_rbf)


def analyze_stage(arr,values,counts,*,folds=4,rank=12,controls=1,seed=47):
    a=np.asarray(arr)
    vals=np.asarray(values,dtype=np.int32)
    lengths=np.asarray(counts,dtype=np.int32)
    if a.ndim!=3 or a.shape[:2]!=lengths.shape or a.shape[1]!=len(vals) or len(set(vals))!=len(vals) or 0 not in vals:
        raise ValueError('GAMER_RECOVERY_SHAPE_OR_RADIUS_INVALID')
    if not np.isfinite(a).all():raise ValueError('GAMER_RECOVERY_NONFINITE')
    valid=np.flatnonzero(np.all(lengths==lengths[:,[int(np.flatnonzero(vals==0)[0])]],axis=1))
    if len(valid)<max(8,folds*2):
        return {'status':'INSUFFICIENT_TOKEN_MATCHED_CONTEXTS','total_contexts':len(a),
                'eligible_contexts':len(valid),'excluded_contexts':len(a)-len(valid)},None
    subset=a[valid]
    within,local_predictions=_within_context(subset,vals)
    near_zero,zero_detail=_zero_crossing(subset,vals)
    cross,cross_predictions=_cross_context(subset,vals,folds=folds,rank=rank,controls=controls,seed=seed)
    # Formally we measure recoverability, not information-theoretic loss.
    result={'status':'COMPLETE','total_contexts':len(a),'eligible_contexts':len(valid),
            'excluded_contexts':len(a)-len(valid),'eligible_indices':valid.tolist(),
            'width':int(a.shape[2]),'within_context_lovo':{k:v for k,v in within.items() if k!='per_context'},
            'per_context':within['per_context'],'heldout_game':cross,
            'zero_crossing':near_zero,
            'diagnostic_caveat':'Failed recovery by linear/RBF probes cannot prove numerical information is absent. Success can reflect signed-token lexical patterns; no game target labels were changed.'}
    return result,{'local_linear':local_predictions[0].astype(np.float32),
                   'local_nonlinear':local_predictions[1].astype(np.float32),
                   'cross_linear':cross_predictions[0].astype(np.float32),
                   'cross_nonlinear':cross_predictions[1].astype(np.float32),
                   'eligible_indices':valid.astype(np.int32)}


def _output_path(source,explicit):
    base=source.with_name(source.name+'-recovery')
    if explicit is not None:
        dest=Path(explicit).resolve()
        if dest==source or source in dest.parents or dest in source.parents:
            raise ValueError('GAMER_RECOVERY_OUTPUT_OVERLAPS_SOURCE')
        if dest.exists():raise FileExistsError(dest)
        return dest
    for n in range(1,10000):
        dest=base.with_name(base.name+f'-{n:04d}')
        if not dest.exists():return dest
    raise RuntimeError('GAMER_RECOVERY_OUTPUT_SLOTS_EXHAUSTED')


def run_recovery(radius_dir,*,output=None,folds=4,rank=12,controls=1,seed=47):
    source=Path(radius_dir).resolve()
    if folds<2 or folds>16 or rank<1 or rank>128 or controls<0 or controls>16:
        raise ValueError('GAMER_RECOVERY_INVALID_PROBE_BUDGET')
    report_path=source/'final_report.json';capture_path=source/'activations.npz';interventions=source/'interventions.jsonl'
    for p in (report_path,capture_path,interventions):
        if not p.is_file():raise FileNotFoundError(p)
    hashes={p.name:_sha256(p) for p in (report_path,capture_path,interventions)}
    original=json.loads(report_path.read_text(encoding='utf-8'))
    if original.get('schema')!='nanojev.gamer.v1.radius-activations.v1' or original.get('status')!='COMPLETE_READ_ONLY':
        raise ValueError('GAMER_RECOVERY_SOURCE_REPORT_INVALID')
    if len(original['contexts'])<8:raise ValueError('GAMER_RECOVERY_MIN_EIGHT_CONTEXTS')
    out=_output_path(source,output)
    # Explicit outputs must never be nested in the original GAMER training
    # run, either. Those directories contain the source checkpoints.
    for protected in (original.get('expanded_run'),original.get('source_run')):
        if not protected:continue
        root=Path(protected).resolve()
        if out==root or root in out.parents or out in root.parents:
            raise ValueError('GAMER_RECOVERY_OUTPUT_OVERLAPS_SOURCE')
    with np.load(capture_path,allow_pickle=False) as data:
        names=[x for x in data.files if x not in ('values','token_counts')]
        vals=data['values'].copy();counts=data['token_counts'].copy()
        if len(vals)<5 or len(vals)%2!=1 or not np.array_equal(vals,-vals[::-1]):
            raise ValueError('GAMER_RECOVERY_SYMMETRIC_RADIUS_REQUIRED')
        if len(original['contexts'])!=len(counts) or counts.shape!=(len(counts),len(vals)):
            raise ValueError('GAMER_RECOVERY_CONTEXT_DIMENSIONS_INVALID')
        per_stage={};preds={}
        for key in names:
            analysis,output_data=analyze_stage(data[key],vals,counts,folds=folds,rank=rank,controls=controls,seed=seed)
            per_stage[key]=analysis
            if output_data:
                for name,values_pred in output_data.items():preds[f'{key}__{name}']=values_pred
            if analysis['status']=='COMPLETE':
                within=analysis['within_context_lovo']['nonlinear']['r2_vs_zero']
                cross=analysis['heldout_game']['nonlinear']['r2_vs_zero']
                zero=analysis['zero_crossing']['adjacent_radius_1']['mean_zero_midpoint_error_ratio']
                print(f'  {key}: within_RBF_R2={within:.4g} heldout_RBF_R2={cross:.4g} zero_midpoint={zero}',flush=True)
            else:print(f'  {key}: {analysis["status"]}',flush=True)
    for p in (report_path,capture_path,interventions):
        if hashes[p.name]!=_sha256(p):raise ValueError('GAMER_RECOVERY_INPUT_CHANGED_DURING_ANALYSIS')
    out.mkdir(parents=True,exist_ok=False)
    np.savez_compressed(out/'recovery_predictions.npz',radius_values=vals,**preds)
    completed=[v for v in per_stage.values() if v['status']=='COMPLETE']
    comparison={}
    for stage,metrics in per_stage.items():
        if metrics['status']!='COMPLETE':
            comparison[stage]={'status':metrics['status']}
            continue
        within=metrics['within_context_lovo']
        cross=metrics['heldout_game']
        comparison[stage]={
            'status':'COMPLETE',
            'contexts':metrics['eligible_contexts'],
            'within_linear_r2':within['linear']['r2_vs_zero'],
            'within_nonlinear_r2':within['nonlinear']['r2_vs_zero'],
            'heldout_game_linear_r2':cross['linear']['r2_vs_zero'],
            'heldout_game_nonlinear_r2':cross['nonlinear']['r2_vs_zero'],
            'zero_midpoint_error_at_radius_1':metrics['zero_crossing']['adjacent_radius_1']['mean_zero_midpoint_error_ratio']}
    first_in=comparison.get('c0_input_norm',{})
    first_out=comparison.get('c0_hidden_1',{})
    def delta(key):
        a=first_in.get(key);b=first_out.get(key)
        return (b-a) if a is not None and b is not None else None
    summary={'stages_complete':len(completed),'stages_total':len(per_stage),
             'context_count':len(counts),'radius_width':int(np.max(np.abs(vals))),
             'layer_comparison':comparison,
             'input_to_hidden_1_deltas':{
                 'within_linear_r2':delta('within_linear_r2'),
                 'within_nonlinear_r2':delta('within_nonlinear_r2'),
                 'heldout_game_linear_r2':delta('heldout_game_linear_r2'),
                 'heldout_game_nonlinear_r2':delta('heldout_game_nonlinear_r2')},
             'interpretation':'The measured deltas compare decoder recoverability, NOT causal deletion of information.'}
    report={'schema':SCHEMA,'status':'COMPLETE_READ_ONLY','source_radius_run':str(source),
            'source_fingerprints_sha256':hashes,'expanded_run':original['expanded_run'],
            'checkpoint_step':original['checkpoint_step'],'folds':folds,
            'basis_rank':rank,'shuffled_training_label_controls':controls,
            'radius_values':vals.tolist(),'contexts':original['contexts'],
            'per_stage':per_stage,'summary':summary,
            'interpretation_constraints':['All source weights and captured activations are read-only',
              'Synthetic radius values are not certified game targets; zero-midpoint is geometry, not correct game zero outputs',
              'Within-context decoders know the zero intervention and train on other values of that SAME prompt',
              'Held-out decoders train on different GAMES, but require zero-intervention activation of the held-out game to center',
              'Nonlinear recovery uses a supervised training-game basis and anchored RBF readout; these probes test recoverability, not information-theoretic loss',
              'Tokenizer wordpiece identity/sign and invalid synthetic game states can explain part of the signal',
              'Shuffled-label null holds the projection basis fixed; it does not re-fit the basis under the null'],
            'artifacts':['final_report.json','recovery_predictions.npz']}
    (out/'final_report.json').write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf-8')
    print('GAMER RADIUS RECOVERY COMPLETE: '+str(out/'final_report.json'),flush=True)
    return report


def main(argv=None):
    import argparse
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--radius-run',type=Path,required=True)
    ap.add_argument('--output',type=Path)
    ap.add_argument('--folds',type=int,default=4)
    ap.add_argument('--rank',type=int,default=12)
    ap.add_argument('--controls',type=int,default=1)
    args=ap.parse_args(argv)
    return run_recovery(args.radius_run,output=args.output,folds=args.folds,rank=args.rank,controls=args.controls)

if __name__=='__main__':main()
