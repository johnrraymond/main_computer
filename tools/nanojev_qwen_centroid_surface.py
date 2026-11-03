#!/usr/bin/env python3
"""Read-only, train-only landmark factorization and smooth numerical interpolation.

Exact 8100/900 random split at 9000 points. Test rows never select landmarks,
set normalization, tune hyperparameters, or fit a surface. No Qwen invocation.
"""
from __future__ import annotations
import argparse, csv, json
from pathlib import Path
import numpy as np

POPS=('C0_games','C1_shotgun_language','C2_fixed_payoff','C3_fixed_addition','C4_T00')

def distances(A,B,metric,mean=None,scale=None):
    from sklearn.metrics.pairwise import euclidean_distances, cosine_distances, manhattan_distances
    if metric=='whitened':
        A=(A-mean)/scale;B=(B-mean)/scale
    if metric=='cosine':return cosine_distances(A,B)
    if metric=='manhattan':return manhattan_distances(A,B)
    D=euclidean_distances(A,B)
    if metric=='complex_log':return .5*np.log1p(D*D)
    return D

def fit_landmark(X,train_idx,metric,rank,landmarks,seed):
    rng=np.random.default_rng(seed)
    ids=np.sort(rng.choice(train_idx,size=min(landmarks,len(train_idx)),replace=False))
    L=np.asarray(X[ids],np.float64)
    mean=L.mean(axis=0);scale=np.maximum(L.std(axis=0),1e-5)
    D=distances(L,L,metric,mean,scale)
    D2=D*D
    rowmean=D2.mean(axis=1); grand=float(D2.mean())
    B=-.5*(D2-rowmean[:,None]-rowmean[None,:]+grand)
    B=(B+B.T)*.5
    eig,U=np.linalg.eigh(B)
    chosen=np.flatnonzero(eig>max(float(eig[-1]),0)*1e-10)
    chosen=chosen[-min(rank,len(chosen)):][::-1]
    if not len(chosen):raise ValueError('no positive landmark eigenvalues')
    lam=eig[chosen];basis=U[:,chosen]/np.sqrt(lam)[None,:]
    return {'ids':ids,'L':L,'mean':mean,'scale':scale,'rowmean':rowmean,'grand':grand,'basis':basis,
            'positive':int(np.sum(eig>1e-10)),'negative':int(np.sum(eig< -1e-10)),
            'rank':int(len(chosen))}

def project(X,fit,metric,chunk=256):
    Z=np.empty((len(X),fit['rank']),np.float64)
    for i in range(0,len(X),chunk):
        D=distances(np.asarray(X[i:i+chunk],np.float64),fit['L'],metric,fit['mean'],fit['scale'])
        D2=D*D
        B=-.5*(D2-D2.mean(axis=1,keepdims=True)-fit['rowmean'][None,:]+fit['grand'])
        Z[i:i+chunk]=B@fit['basis']
    return Z

def score(y,p):
    from scipy.stats import spearmanr
    err=np.abs(y-p)
    rho=float(spearmanr(y,p).statistic) if np.unique(y).size>1 and np.unique(p).size>1 else None
    sd=float(np.std(y))
    return {'mae':float(err.mean()),'median_ae':float(np.median(err)),
            'rmse':float(np.sqrt(np.mean((y-p)**2))), 'normalized_mae':float(err.mean()/max(sd,1e-12)),
            'spearman':rho,'within_1':float(np.mean(err<=1)), 'within_10':float(np.mean(err<=10)),
            'bias':float(np.mean(p-y))}

def predict_knn(Ztrain,ytrain,Zquery,k,bandwidth):
    from sklearn.neighbors import NearestNeighbors
    nn=NearestNeighbors(n_neighbors=min(k,len(Ztrain))).fit(Ztrain)
    d,idx=nn.kneighbors(Zquery)
    if bandwidth=='inverse':w=1/np.maximum(d,1e-6)
    else:
        # Local Gaussian kernel scaled by distance to furthest selected neighbor.
        radius=np.maximum(d[:,-1:],1e-6)
        w=np.exp(-.5*(d/radius)**2)
    return np.sum(w*ytrain[idx],axis=1)/np.sum(w,axis=1)

def analyze(directory,out,seed=47,landmarks=256,rank=32,metric='euclidean',chunk=256):
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import Ridge
    src=Path(directory);out=Path(out)
    X=np.load(src/'activations.npy',mmap_mode='r')
    rows=[json.loads(s) for s in (src/'probes.jsonl').read_text(encoding='utf8').splitlines() if s.strip()]
    if len(X)!=len(rows):raise ValueError('activation/probe length mismatch')
    if X.ndim!=2 or not np.isfinite(X).all():raise ValueError('invalid activations')
    y=np.array([float(r['answer']) for r in rows]);
    if not np.isfinite(y).all():raise ValueError('invalid numerical labels')
    n=len(y);rng=np.random.default_rng(seed); perm=rng.permutation(n)
    if n==9000:tr=perm[:8100];te=perm[8100:]
    else:
        if n<100:raise ValueError('minimum 100 rows for diagnostic')
        ntest=max(1,int(round(.1*n)));tr=perm[:-ntest];te=perm[-ntest:]
    fit=fit_landmark(X,tr,metric,rank,landmarks,seed)
    # Only training points influence the eigensystem, standardization and surfaces.
    Ztr=project(X[tr],fit,metric,chunk);Zte=project(X[te],fit,metric,chunk)
    scaler=StandardScaler().fit(Ztr)
    Ztr=scaler.transform(Ztr);Zte=scaler.transform(Zte)
    # Hyperparameter choice uses an inner 10% TRAIN validation split.
    shuffled=np.random.default_rng(seed+1).permutation(len(tr));nv=max(5,len(tr)//10)
    va=shuffled[:nv];sub=shuffled[nv:]
    choices=[]
    for k in (8,32,128):
        for bw in ('inverse','gaussian'):
            p=predict_knn(Ztr[sub],y[tr][sub],Ztr[va],k,bw)
            choices.append((score(y[tr][va],p)['mae'],f'knn_{k}_{bw}',(k,bw)))
    for alpha in (1.,100.,10000.):
        reg=Ridge(alpha=alpha).fit(Ztr[sub],y[tr][sub]);p=reg.predict(Ztr[va])
        choices.append((score(y[tr][va],p)['mae'],f'ridge_{alpha:g}',alpha))
    _,name,param=min(choices,key=lambda v:v[0])
    if isinstance(param,tuple):pred=predict_knn(Ztr,y[tr],Zte,*param)
    else:pred=Ridge(alpha=param).fit(Ztr,y[tr]).predict(Zte)
    baseline=np.full(len(te),np.median(y[tr]))
    # Also preserve per-estimator test measurements for post-hoc exploration,
    # without selecting the reported winner on the held-out observations.
    candidates={}
    for k in (8,32,128):
        for bw in ('inverse','gaussian'):
            candidates[f'knn_{k}_{bw}']=score(y[te],predict_knn(Ztr,y[tr],Zte,k,bw))
    for alpha in (1.,100.,10000.):
        candidates[f'ridge_{alpha:g}']=score(y[te],Ridge(alpha=alpha).fit(Ztr,y[tr]).predict(Zte))
    out.mkdir(parents=True,exist_ok=True)
    np.save(out/'train_indices.npy',tr);np.save(out/'test_indices.npy',te)
    np.save(out/'train_coordinates.npy',Ztr.astype(np.float32));np.save(out/'test_coordinates.npy',Zte.astype(np.float32))
    np.save(out/'landmark_indices.npy',fit['ids'])
    with (out/'heldout_predictions.csv').open('w',newline='',encoding='utf8') as f:
        w=csv.writer(f);w.writerow(('probe_index','probe_id','answer','predicted','absolute_error','baseline'))
        for i,actual,p,b in zip(te,y[te],pred,baseline):
            w.writerow((int(i),rows[i].get('id',str(i)),actual,float(p),abs(float(p-actual)),float(b)))
    result={'schema':'nanojev.centroid-surface.v1','population':src.name,'points':n,
            'training':len(tr),'test_n':len(te),'seed':seed,'metric':metric,
            'landmark_count':len(fit['ids']),'rank':fit['rank'],'positive_eigenvalues':fit['positive'],
            'negative_eigenvalues':fit['negative'],'selection':'lowest inner-validation MAE, no test tuning',
            'chosen_model':name,'inner_validation_mae':float(min(choices,key=lambda x:x[0])[0]),
            'heldout':score(y[te],pred),'constant_median':score(y[te],baseline),'candidate_heldout_diagnostics':candidates,
            'limitation':'Random prompt holdout may share answer values and template structure; this tests interpolation, not transfer to unseen games.'}
    (out/'report.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    return result

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input',default='runtime/reports/qwen-five-centroids')
    ap.add_argument('--population',choices=('all',)+POPS,default='all')
    ap.add_argument('--output',default=None)
    ap.add_argument('--seed',type=int,default=47)
    ap.add_argument('--landmarks',type=int,default=256)
    ap.add_argument('--rank',type=int,default=32)
    ap.add_argument('--metric',choices=('euclidean','whitened','manhattan','complex_log','cosine'),default='euclidean')
    ap.add_argument('--chunk',type=int,default=256)
    args=ap.parse_args()
    root=Path(args.input);out=Path(args.output) if args.output else root/'surface-interpolation'
    summary=[]
    for pop in POPS if args.population=='all' else (args.population,):
        if not (root/pop/'activations.npy').exists():
            print(f'{pop}: no saved activations; skipping',flush=True);continue
        r=analyze(root/pop,out/pop,args.seed,args.landmarks,args.rank,args.metric,args.chunk)
        summary.append({'population':pop,'selected':r['chosen_model'], 'mae':r['heldout']['mae'],
                        'baseline_mae':r['constant_median']['mae'],'spearman':r['heldout']['spearman']})
        print(f"{pop}: {r['training']}/{r['test_n']} split; {r['chosen_model']}; MAE={r['heldout']['mae']:.5g}; baseline={r['constant_median']['mae']:.5g}; rho={r['heldout']['spearman']}",flush=True)
    out.mkdir(parents=True,exist_ok=True)
    (out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
    print(json.dumps({'output':str(out),'analyzed':[r['population'] for r in summary]},indent=2))
if __name__=='__main__':main()
