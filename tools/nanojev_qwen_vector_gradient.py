#!/usr/bin/env python3
"""Read-only numerical-gradient-guided correction on saved Qwen activations.

Correct answers supervise TRAINING neighborhood slopes, not held-out queries.
The frozen Ridge readout scores original and corrected vectors.  A local
prediction is also reported: a linear readout correction is mathematically
identical to interpolating toward that prediction, not independent evidence
of activation improvement.  Evaluation is diagnostic, not live-model editing.
"""
from __future__ import annotations
import argparse,csv,json
from pathlib import Path
import numpy as np

POPS=('C0_games','C1_shotgun_language','C2_fixed_payoff','C3_fixed_addition','C4_T00')

def scores(y,p):
    from scipy.stats import spearmanr
    e=np.abs(y-p)
    rho=spearmanr(y,p).statistic if np.unique(y).size>1 and np.unique(p).size>1 else np.nan
    return dict(mae=float(e.mean()),median_ae=float(np.median(e)),rmse=float(np.sqrt(np.mean((y-p)**2))),spearman=float(rho) if np.isfinite(rho) else None)

def local_targets(query,ref,targets,k,penalty=1.,batch=128):
    """Locally fitted first-order Taylor plane; returns value, gradient and fit diagnostics.

    Scale covariates by local mean squared radius; penalize gradients relative
    to this scale. Nearest neighbor targets come ONLY from the fitting subset.
    """
    from sklearn.neighbors import NearestNeighbors
    nn=NearestNeighbors(n_neighbors=k,algorithm='auto').fit(ref)
    dists,indices=nn.kneighbors(query)
    n,dim=query.shape
    prediction=np.empty(n);grad=np.empty((n,dim));spread=np.empty(n)
    for start in range(0,n,batch):
        end=min(n,start+batch)
        neighbors=ref[indices[start:end]].astype(np.float64,copy=False)
        labels=targets[indices[start:end]].astype(np.float64,copy=False)
        origin=neighbors.mean(axis=1)
        delta=neighbors-origin[:,None,:]
        # Dual ridge is cheap for k<=64 and avoids thousands of 32x32 solves.
        scale=np.sqrt(np.mean(np.sum(delta*delta,axis=2),axis=1))
        scale=np.maximum(scale,1e-8)
        a=delta/scale[:,None,None]
        yc=labels-labels.mean(axis=1,keepdims=True)
        gram=np.einsum('bkd,bjd->bkj',a,a,optimize=True)
        gram+=float(penalty)*np.eye(k)[None,:,:]
        dual=np.linalg.solve(gram,yc[...,None])[...,0]
        g=np.einsum('bkd,bk->bd',a,dual,optimize=True)/scale[:,None]
        prediction[start:end]=labels.mean(axis=1)+np.einsum('bd,bd->b',query[start:end]-origin,g)
        grad[start:end]=g;spread[start:end]=scale
    return prediction,grad,spread

def evaluate_population(folder,*,seed=47,rank=32,holdout=900,ks=(8,16,32,64),penalties=(.01,.1,1.,10.),alphas=(.25,.5,.75,1.),batch=128):
    from sklearn.decomposition import PCA
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import Ridge
    X=np.load(folder/'activations.npy',mmap_mode='r')
    with (folder/'probes.jsonl').open(encoding='utf-8') as f:rows=[json.loads(s) for s in f if s.strip()]
    if len(X)!=len(rows):raise ValueError('activation/probe count mismatch')
    y=np.asarray([float(r['answer']) for r in rows],dtype=np.float64)
    if not np.all(np.isfinite(y)) or len(y)<holdout+100:raise ValueError('invalid inputs')
    rng=np.random.default_rng(seed);perm=rng.permutation(len(y))
    test=perm[:holdout];remaining=perm[holdout:]
    nval=max(50,round(len(remaining)*.125));val=remaining[:nval];fit=remaining[nval:]
    dim=min(rank,X.shape[1],len(fit)-1)
    pca=PCA(n_components=dim,svd_solver='randomized',random_state=seed)
    zfit=pca.fit_transform(np.asarray(X[fit],dtype=np.float64))
    zval=pca.transform(np.asarray(X[val],dtype=np.float64));ztest=pca.transform(np.asarray(X[test],dtype=np.float64))
    norm=StandardScaler().fit(zfit)
    zfit=norm.transform(zfit);zval=norm.transform(zval);ztest=norm.transform(ztest)
    readout=Ridge(alpha=100.).fit(zfit,y[fit]);beta=np.asarray(readout.coef_)
    beta2=float(beta@beta)
    if beta2<1e-20:raise ValueError('frozen readout has zero gradient')
    rawval=readout.predict(zval)
    best={'k':None,'penalty':None,'alpha':0.,'method':'identity','validation_mae':scores(y[val],rawval)['mae']}
    trials=[]
    for k in sorted(set(ks)):
        if k<3 or k>len(fit):continue
        for penalty in penalties:
            local,grad,_=local_targets(zval,zfit,y[fit],k,penalty,batch)
            # The minimum-norm change needed to move the frozen readout toward
            # the local numerical estimate is along beta. The local gradient
            # provides the proposed target; report its alignment with beta.
            base=rawval
            cos=(grad@beta)/(np.linalg.norm(grad,axis=1)*np.sqrt(beta2)+1e-12)
            for alpha in alphas:
                p=(1-alpha)*base+alpha*local
                err=scores(y[val],p)['mae']
                trial={'method':'local_gradient','k':k,'penalty':penalty,'alpha':alpha,
                       'validation_mae':err,'mean_gradient_alignment':float(np.mean(cos))}
                trials.append(trial)
                if err<best['validation_mae']-1e-12:best=trial
    raw=readout.predict(ztest)
    if best['method']=='identity':
        updated=raw.copy();local=raw.copy();alignment=None;correction=np.zeros_like(ztest)
    else:
        local,grad,_=local_targets(ztest,zfit,y[fit],best['k'],best['penalty'],batch)
        correction=(best['alpha']*(local-raw)/beta2)[:,None]*beta[None,:]
        updated=readout.predict(ztest+correction)
        alignment=float(np.mean((grad@beta)/(np.linalg.norm(grad,axis=1)*np.sqrt(beta2)+1e-12)))
        if not np.allclose(updated,(1-best['alpha'])*raw+best['alpha']*local,atol=1e-6,rtol=1e-9):
            raise AssertionError('gradient correction failed frozen readout identity')
    orig=scores(y[test],raw);changed=scores(y[test],updated)
    result={'schema':'nanojev.centroid-vector-gradient.v1','population':folder.name,'points':len(y),
      'fit':len(fit),'validation':len(val),'holdout':len(test),'seed':seed,'projection_rank':dim,
      'selection':'training-only PCA/scaler, frozen ridge, local labels from fitting data; validation selects model',
      'correction':'minimum-norm vector step along frozen readout gradient to local first-order numerical estimate',
      'caveat':'frozen linear readout correction equals blending predictions; this does NOT demonstrate improved Qwen generation',
      'selected':best,'trials':trials,'holdout_raw':orig,'holdout_corrected':changed,
      'holdout_local_estimate':scores(y[test],local),'holdout_gradient_alignment':alignment,
      'mean_correction_norm':float(np.linalg.norm(correction,axis=1).mean()),
      'improvement_pct':float(100*(orig['mae']-changed['mae'])/max(orig['mae'],1e-12))}
    predictions=[{'index':int(i),'answer':float(y[i]),'raw':float(a),'local':float(b),'corrected':float(c),
                  'raw_abs_error':float(abs(y[i]-a)),'corrected_abs_error':float(abs(y[i]-c)),
                  'correction_norm':float(np.linalg.norm(v))} for i,a,b,c,v in zip(test,raw,local,updated,correction)]
    return result,predictions

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',default='runtime/reports/qwen-five-centroids')
    p.add_argument('--population',choices=('all',)+POPS,default='all')
    p.add_argument('--output');p.add_argument('--seed',type=int,default=47)
    p.add_argument('--rank',type=int,default=32);p.add_argument('--holdout',type=int,default=900)
    p.add_argument('--ks',default='8,16,32,64');p.add_argument('--penalties',default='0.01,0.1,1,10')
    p.add_argument('--alphas',default='0.25,0.5,0.75,1');p.add_argument('--batch',type=int,default=128)
    a=p.parse_args();ks=tuple(int(v) for v in a.ks.split(','));penalties=tuple(float(v) for v in a.penalties.split(','));alphas=tuple(float(v) for v in a.alphas.split(','))
    if not ks or min(ks)<3 or not penalties or min(penalties)<=0 or not alphas or min(alphas)<=0 or max(alphas)>1:raise ValueError('invalid grid')
    root=Path(a.input);output=Path(a.output) if a.output else root/'vector-gradient';output.mkdir(parents=True,exist_ok=True)
    summary=[]
    for pop in POPS if a.population=='all' else (a.population,):
        folder=root/pop
        if not (folder/'activations.npy').is_file() or not (folder/'probes.jsonl').is_file():
            print(f'{pop}: no saved activations; skipping',flush=True);continue
        report,pred=evaluate_population(folder,seed=a.seed,rank=a.rank,holdout=a.holdout,ks=ks,penalties=penalties,alphas=alphas,batch=a.batch)
        (output/f'{pop}.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        with (output/f'{pop}_holdout.csv').open('w',newline='',encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=list(pred[0]));w.writeheader();w.writerows(pred)
        s=report['selected'];summary.append({'population':pop,'k':s['k'],'penalty':s['penalty'],'alpha':s['alpha'],
                'raw_mae':report['holdout_raw']['mae'],'corrected_mae':report['holdout_corrected']['mae'],
                'improvement_pct':report['improvement_pct']})
        print(f'{pop}: k={s["k"]} penalty={s["penalty"]} alpha={s["alpha"]} '
              f'raw_MAE={report["holdout_raw"]["mae"]:.6g} corrected_MAE={report["holdout_corrected"]["mae"]:.6g} '
              f'improvement={report["improvement_pct"]:+.2f}%',flush=True)
    if summary:
        with (output/'summary.csv').open('w',newline='',encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=list(summary[0]));w.writeheader();w.writerows(summary)
    print(json.dumps({'output':str(output),'populations_analyzed':[s['population'] for s in summary]},indent=2))
if __name__=='__main__':main()
