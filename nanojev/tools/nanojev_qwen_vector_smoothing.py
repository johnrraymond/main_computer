#!/usr/bin/env python3
"""Read-only, label-free vector smoothing tested with a frozen numerical readout.

Training labels fit a readout and score results, never select smoothing neighbors.
Model parameters selected on validation, untouched 900-vector holdout evaluated once.
"""
from __future__ import annotations
import argparse, csv, json
from pathlib import Path
import numpy as np

POPS=('C0_games','C1_shotgun_language','C2_fixed_payoff','C3_fixed_addition','C4_T00')

def metrics(y, pred):
    from scipy.stats import spearmanr
    e=np.abs(y-pred)
    r=spearmanr(y,pred).statistic if len(np.unique(pred))>1 and len(np.unique(y))>1 else np.nan
    return {'mae':float(e.mean()), 'median_ae':float(np.median(e)),
            'rmse':float(np.sqrt(np.mean((y-pred)**2))),
            'spearman':float(r) if np.isfinite(r) else None}

def smooth(Zquery, Zreference, k, alpha, temperature=1., *, batch=256):
    """Return smoothed query coordinates. Reference vectors only; no answer labels."""
    from sklearn.neighbors import NearestNeighbors
    nn=NearestNeighbors(n_neighbors=k, algorithm='auto').fit(Zreference)
    out=np.empty_like(Zquery, dtype=np.float64)
    for lo in range(0,len(Zquery),batch):
        hi=min(lo+batch,len(Zquery))
        d,ix=nn.kneighbors(Zquery[lo:hi])
        # Scale invariant kernel based on local kth-neighbor distance.
        bandwidth=np.maximum(d[:,-1:],1.e-9)*temperature
        w=np.exp(-0.5*(d/bandwidth)**2)
        w=w/np.maximum(w.sum(axis=1,keepdims=True),1.e-12)
        centroid=np.einsum('nk,nkd->nd',w,Zreference[ix], optimize=True)
        out[lo:hi]=(1.-alpha)*Zquery[lo:hi]+alpha*centroid
    return out

def evaluate_population(folder, *, seed=47, rank=32, holdout=900, ks=(8,16,32,64), alphas=(.1,.25,.5,.75), ridge_alpha=100., batch=256):
    from sklearn.decomposition import PCA
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import Ridge
    X=np.load(folder/'activations.npy',mmap_mode='r')
    with (folder/'probes.jsonl').open(encoding='utf-8') as f:
        rows=[json.loads(l) for l in f if l.strip()]
    if len(X)!=len(rows): raise ValueError('activation/probe misalignment')
    y=np.asarray([float(r['answer']) for r in rows])
    if not np.isfinite(y).all():raise ValueError('nonfinite labels')
    n=len(y)
    if n<holdout+100:raise ValueError('insufficient points')
    rng=np.random.default_rng(seed)
    perm=rng.permutation(n); test=perm[:holdout]; remaining=perm[holdout:]
    val_count=max(50,int(round(len(remaining)*.125)))
    val=remaining[:val_count]; fit=remaining[val_count:]
    d=min(rank,X.shape[1],len(fit)-1)
    # All projections, normalization and readout frozen before validation and holdout.
    pca=PCA(n_components=d,svd_solver='randomized',random_state=seed)
    z_fit=pca.fit_transform(np.asarray(X[fit],dtype=np.float64))
    z_val=pca.transform(np.asarray(X[val],dtype=np.float64))
    z_test=pca.transform(np.asarray(X[test],dtype=np.float64))
    scaler=StandardScaler().fit(z_fit)
    z_fit=scaler.transform(z_fit); z_val=scaler.transform(z_val); z_test=scaler.transform(z_test)
    readout=Ridge(alpha=ridge_alpha).fit(z_fit,y[fit]);raw_val=readout.predict(z_val)
    best=None; trials=[]
    for k in ks:
        if k>len(fit):continue
        for alpha in alphas:
            zz=smooth(z_val,z_fit,k,alpha,batch=batch)
            score=metrics(y[val],readout.predict(zz))
            trial={'k':k,'alpha':alpha,**score};trials.append(trial)
            if best is None or score['mae']<best['mae']:
                best=trial
    baseline_val=metrics(y[val],raw_val)
    original=readout.predict(z_test)
    smoothed=readout.predict(smooth(z_test,z_fit,best['k'],best['alpha'],batch=batch))
    m_original=metrics(y[test],original);m_smoothed=metrics(y[test],smoothed)
    # This comparator is meaningful because all three predictions use exactly same readout.
    # A second comparator trained on smoothed inputs is deliberately NOT included.
    result={'schema':'nanojev.vector-smoothing.v1','population':folder.name,
        'n':n,'fit':len(fit),'validation':len(val),'holdout':len(test), 'seed':seed,
        'projection_rank':d,'readout':'frozen ridge on unsmoothed fit vectors',
        'smoothing':'gaussian local weighted centroid of FIT vectors only',
        'selection':'validation MAE','selection_baseline':baseline_val,
        'selected':best,'trials':trials,'holdout_original':m_original,
        'holdout_smoothed':m_smoothed,
        'mae_improvement_pct':float(100*(m_original['mae']-m_smoothed['mae'])/max(m_original['mae'],1.e-12)),
        'per_example_columns':['index','answer','original_yhat','smoothed_yhat','original_error','smoothed_error']}
    predictions=[{'index':int(i),'answer':float(y[i]),'original_yhat':float(a),'smoothed_yhat':float(b),
      'original_error':float(abs(y[i]-a)),'smoothed_error':float(abs(y[i]-b))} for i,a,b in zip(test,original,smoothed)]
    return result,predictions

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',default='runtime/reports/qwen-five-centroids')
    p.add_argument('--population',choices=('all',)+POPS,default='all')
    p.add_argument('--output')
    p.add_argument('--seed',type=int,default=47)
    p.add_argument('--rank',type=int,default=32)
    p.add_argument('--holdout',type=int,default=900)
    p.add_argument('--ks',default='8,16,32,64')
    p.add_argument('--alphas',default='0.1,0.25,0.5,0.75')
    p.add_argument('--batch',type=int,default=256)
    args=p.parse_args()
    root=Path(args.input);out=Path(args.output) if args.output else root/'vector-smoothing'
    out.mkdir(parents=True,exist_ok=True)
    ks=tuple(int(s) for s in args.ks.split(',')); alphas=tuple(float(s) for s in args.alphas.split(','))
    if not ks or min(ks)<1 or not alphas or min(alphas)<0 or max(alphas)>1:raise ValueError('invalid smoothing grid')
    summary=[]
    for pop in POPS if args.population=='all' else (args.population,):
        folder=root/pop
        if not (folder/'activations.npy').exists() or not (folder/'probes.jsonl').exists():
            print(f'{pop}: no saved vectors and probes; skipping',flush=True);continue
        result,rows=evaluate_population(folder,seed=args.seed,rank=args.rank,holdout=args.holdout,ks=ks,alphas=alphas,batch=args.batch)
        (out/f'{pop}.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        with (out/f'{pop}_holdout.csv').open('w',newline='',encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
        summary.append({'population':pop,'k':result['selected']['k'],'alpha':result['selected']['alpha'],
          'raw_mae':result['holdout_original']['mae'],'smoothed_mae':result['holdout_smoothed']['mae'],
          'improvement_pct':result['mae_improvement_pct']})
        print(f'{pop}: k={result["selected"]["k"]} alpha={result["selected"]["alpha"]} '
              f'raw_MAE={result["holdout_original"]["mae"]:.6g} '
              f'smoothed_MAE={result["holdout_smoothed"]["mae"]:.6g} '
              f'improvement={result["mae_improvement_pct"]:+.2f}%',flush=True)
    if summary:
        with (out/'summary.csv').open('w',newline='',encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=list(summary[0]));w.writeheader();w.writerows(summary)
    print(json.dumps({'output':str(out),'populations_analyzed':[r['population'] for r in summary]},indent=2))
if __name__=='__main__':main()
