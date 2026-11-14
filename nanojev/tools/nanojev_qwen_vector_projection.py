#!/usr/bin/env python3
"""Read-only, label-free local affine-subspace denoising on saved Qwen vectors.

Select rank, neighbors, and projection fraction using validation labels ONLY;
score original, averaging and local projection on an untouched 900-vector holdout.
Projection and frozen readout are fitted only on fitting vectors.
"""
from __future__ import annotations
import argparse, csv, json
from pathlib import Path
import numpy as np

POPS=('C0_games','C1_shotgun_language','C2_fixed_payoff','C3_fixed_addition','C4_T00')

def metrics(y,p):
    from scipy.stats import spearmanr
    e=np.abs(y-p)
    rho=spearmanr(y,p).statistic if len(np.unique(y))>1 and len(np.unique(p))>1 else np.nan
    return {'mae':float(e.mean()),'median_ae':float(np.median(e)),
            'rmse':float(np.sqrt(np.mean((y-p)**2))),
            'spearman':float(rho) if np.isfinite(rho) else None}

def neighbors(query,ref,k):
    from sklearn.neighbors import NearestNeighbors
    nn=NearestNeighbors(n_neighbors=k).fit(ref)
    return nn.kneighbors(query)

def local_affine(query,ref,ix,rank,alpha,*,batch=128):
    """Project onto local PCA plane about neighbor mean; no labels used.

    Each neighborhood defines its OWN plane, not the overall centroid plane.
    Vectorized symmetric eigh in small batches. Rank 0 is mean-only.
    """
    if alpha==0: return np.asarray(query,dtype=np.float64).copy()
    if not (0 <= rank < ix.shape[1]):raise ValueError('rank must be less than k')
    out=np.empty_like(query,dtype=np.float64)
    for lo in range(0,len(query),batch):
        hi=min(len(query),lo+batch)
        nearby=np.asarray(ref[ix[lo:hi]],dtype=np.float64)
        mean=nearby.mean(axis=1)
        delta=np.asarray(query[lo:hi],dtype=np.float64)-mean
        if rank:
            centered=nearby-mean[:,None,:]
            cov=np.einsum('nkd,nke->nde',centered,centered,optimize=True)
            _,vecs=np.linalg.eigh(cov)
            basis=vecs[:,:,-rank:]
            tangent=np.einsum('ndr,nr->nd',basis,np.einsum('ndr,nd->nr',basis,delta,optimize=True),optimize=True)
            target=mean+tangent
        else:target=mean
        out[lo:hi]=(1-alpha)*query[lo:hi]+alpha*target
    return out

def evaluate_population(folder,*,seed=47,rank=32,holdout=900,ks=(8,16,32,64),
                        local_ranks=(1,2,4,8,16),alphas=(.1,.25,.5,.75,1.),
                        ridge_alpha=100.,batch=128):
    from sklearn.decomposition import PCA
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import Ridge
    X=np.load(folder/'activations.npy',mmap_mode='r')
    with (folder/'probes.jsonl').open(encoding='utf-8') as f:
        rows=[json.loads(s) for s in f if s.strip()]
    if len(X)!=len(rows):raise ValueError('activation/probe mismatch')
    y=np.asarray([float(r['answer']) for r in rows]);n=len(y)
    if not np.isfinite(y).all() or n<holdout+100:raise ValueError('invalid population')
    rng=np.random.default_rng(seed);perm=rng.permutation(n)
    test=perm[:holdout];remaining=perm[holdout:]
    nval=max(50,int(round(len(remaining)*.125)));val=remaining[:nval];fit=remaining[nval:]
    d=min(rank,X.shape[1],len(fit)-1)
    pca=PCA(n_components=d,svd_solver='randomized',random_state=seed)
    zfit=pca.fit_transform(np.asarray(X[fit],dtype=np.float64))
    zval=pca.transform(np.asarray(X[val],dtype=np.float64))
    ztest=pca.transform(np.asarray(X[test],dtype=np.float64))
    norm=StandardScaler().fit(zfit)
    zfit=norm.transform(zfit);zval=norm.transform(zval);ztest=norm.transform(ztest)
    readout=Ridge(alpha=ridge_alpha).fit(zfit,y[fit])
    rawval=metrics(y[val],readout.predict(zval));trials=[]
    best={'method':'identity','k':None,'rank':None,'alpha':0.,**rawval}
    valid_ks=sorted(set(int(k) for k in ks if 2<=k<=len(fit)))
    for k in valid_ks:
        _,idx=neighbors(zval,zfit,k)
        for r in (0,)+tuple(sorted(set(r for r in local_ranks if 0<r<min(k,d+1)))):
            for alpha in alphas:
                projected=local_affine(zval,zfit,idx,r,alpha,batch=batch)
                met=metrics(y[val],readout.predict(projected))
                record={'method':'mean' if r==0 else 'local_pca','k':k,'rank':r,'alpha':alpha,**met}
                trials.append(record)
                if met['mae']<best['mae']:best=record
    pred_raw=readout.predict(ztest)
    if best['method']=='identity':pred_changed=pred_raw.copy()
    else:
        _,idx=neighbors(ztest,zfit,best['k'])
        pred_changed=readout.predict(local_affine(ztest,zfit,idx,best['rank'],best['alpha'],batch=batch))
    a=metrics(y[test],pred_raw);b=metrics(y[test],pred_changed)
    result={'schema':'nanojev.vector-local-projection.v1','population':folder.name,
            'n':n,'fit':len(fit),'validation':len(val),'holdout':len(test),'seed':seed,
            'projection_rank':d,'readout':'ridge, trained on unmodified fitting vectors',
            'transform':'per-query neighbor-centered affine PCA; selected on validation',
            'identity_eligible':True,'validation_identity':rawval,
            'selected':best,'trials':trials,'holdout_original':a,'holdout_modified':b,
            'improvement_pct':float(100*(a['mae']-b['mae'])/max(a['mae'],1e-12))}
    predictions=[{'index':int(i),'answer':float(y[i]),'raw_yhat':float(p),
                  'modified_yhat':float(q),'raw_abs_error':float(abs(y[i]-p)),
                  'modified_abs_error':float(abs(y[i]-q))}
                 for i,p,q in zip(test,pred_raw,pred_changed)]
    return result,predictions

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',default='runtime/reports/qwen-five-centroids')
    p.add_argument('--population',choices=('all',)+POPS,default='all')
    p.add_argument('--output');p.add_argument('--seed',type=int,default=47)
    p.add_argument('--rank',type=int,default=32);p.add_argument('--holdout',type=int,default=900)
    p.add_argument('--ks',default='8,16,32,64');p.add_argument('--local-ranks',default='1,2,4,8,16')
    p.add_argument('--alphas',default='0.1,0.25,0.5,0.75,1.0');p.add_argument('--batch',type=int,default=128)
    args=p.parse_args()
    ks=tuple(int(x) for x in args.ks.split(','));ranks=tuple(int(x) for x in args.local_ranks.split(','))
    alphas=tuple(float(x) for x in args.alphas.split(','))
    if not ks or min(ks)<2 or not ranks or min(ranks)<1 or not alphas or min(alphas)<=0 or max(alphas)>1:
        raise ValueError('bad parameter grid')
    root=Path(args.input);out=Path(args.output) if args.output else root/'local-vector-projection'
    out.mkdir(parents=True,exist_ok=True);summary=[]
    for pop in POPS if args.population=='all' else (args.population,):
        folder=root/pop
        if not (folder/'activations.npy').exists() or not (folder/'probes.jsonl').exists():
            print(f'{pop}: no saved activations; skipping',flush=True);continue
        report,rows=evaluate_population(folder,seed=args.seed,rank=args.rank,holdout=args.holdout,
                        ks=ks,local_ranks=ranks,alphas=alphas,batch=args.batch)
        (out/f'{pop}.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        with (out/f'{pop}_holdout.csv').open('w',newline='',encoding='utf-8') as f:
            writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
        m=report['selected'];summary.append({'population':pop,'method':m['method'],'k':m['k'],
            'local_rank':m['rank'],'alpha':m['alpha'],'raw_mae':report['holdout_original']['mae'],
            'projected_mae':report['holdout_modified']['mae'],'improvement_pct':report['improvement_pct']})
        print(f'{pop}: {m["method"]} k={m["k"]} rank={m["rank"]} alpha={m["alpha"]} '
              f'raw_MAE={report["holdout_original"]["mae"]:.6g} '
              f'projected_MAE={report["holdout_modified"]["mae"]:.6g} '
              f'improvement={report["improvement_pct"]:+.2f}%',flush=True)
    if summary:
        with (out/'summary.csv').open('w',newline='',encoding='utf-8') as f:
            writer=csv.DictWriter(f,fieldnames=list(summary[0]));writer.writeheader();writer.writerows(summary)
    print(json.dumps({'output':str(out),'populations_analyzed':[s['population'] for s in summary]},indent=2))
if __name__=='__main__':main()
