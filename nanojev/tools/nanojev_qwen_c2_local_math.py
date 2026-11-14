#!/usr/bin/env python3
"""Deterministic, train-only numerical-local baseline for C2 streaming training.

No Qwen dependency: accepts activation arrays and true training labels. Labels
from evaluation examples are never used to fit geometry or select parameters.
"""
from __future__ import annotations
import numpy as np


def fit_geometry(x, rank=32, seed=47):
    from sklearn.decomposition import PCA
    x = np.asarray(x, dtype=np.float32)
    if x.ndim != 2 or len(x) < 10 or not np.isfinite(x).all():
        raise ValueError('expected at least 10 finite training activation vectors')
    rank = min(int(rank), x.shape[1], len(x)-1)
    if rank < 1: raise ValueError('rank must be positive')
    pca = PCA(n_components=rank, svd_solver='randomized', random_state=seed)
    z = pca.fit_transform(x).astype(np.float64)
    scales = np.maximum(z.std(axis=0), 1e-6)
    z /= scales
    return {'pca':pca, 'scales':scales, 'train':z}


def transform_geometry(geo, x):
    return geo['pca'].transform(np.asarray(x,dtype=np.float32)).astype(np.float64)/geo['scales']


def local_predict(ztrain, ytrain, zquery, k=32, penalty=1.0, method='linear', chunk=256):
    """Predict using weighted nearby labels, or regularized local linear fits.

    The local linear model centers coordinates at the query vector, and fits a
    weighted intercept plus gradient. The intercept is the query prediction.
    """
    from sklearn.neighbors import NearestNeighbors
    ztrain=np.asarray(ztrain,np.float64);zquery=np.asarray(zquery,np.float64)
    ytrain=np.asarray(ytrain,np.float64)
    k=min(int(k),len(ytrain))
    if k<2 or penalty<0:raise ValueError('invalid local interpolation parameters')
    nn=NearestNeighbors(n_neighbors=k,algorithm='auto').fit(ztrain)
    result=np.empty(len(zquery),np.float64)
    for start in range(0,len(zquery),chunk):
        q=zquery[start:start+chunk]
        dist, ids=nn.kneighbors(q)
        for i,(di,inds) in enumerate(zip(dist,ids)):
            yi=ytrain[inds]
            if di[0]<=1e-8:
                # Exact geometric match: use the average of equal-distance matches.
                result[start+i]=float(np.mean(yi[di<=1e-8]));continue
            weights=1./np.maximum(di,1e-6)
            if method=='knn':
                result[start+i]=np.dot(weights,yi)/weights.sum();continue
            if method!='linear':raise ValueError('unknown local model')
            d=ztrain[inds]-q[i]
            # Effective rank <=k. Penalize slopes, never the intercept.
            a=np.column_stack([np.ones(k),d]);wa=a*weights[:,None]
            reg=np.eye(a.shape[1],dtype=np.float64)*float(penalty)
            reg[0,0]=1e-10
            coef=np.linalg.solve(a.T@wa+reg,a.T@(weights*yi))
            result[start+i]=coef[0]
    return result


def stats(y, prediction):
    y=np.asarray(y,np.float64);p=np.asarray(prediction,np.float64)
    e=np.abs(y-p)
    return {'n':int(len(y)),'mae':float(np.mean(e)),'median_ae':float(np.median(e)),
            'p90_ae':float(np.percentile(e,90)),
            'within_1':float(np.mean(e<=1)),'within_5':float(np.mean(e<=5)),
            'within_10':float(np.mean(e<=10)),
            'bias':float(np.mean(p-y)),
            'truth_min':float(min(y)),'truth_max':float(max(y)),
            'prediction_min':float(min(p)),'prediction_max':float(max(p))}


def select_method(z, y, seed=47, ks=(8,16,32,64), penalties=(.1,1.,10.), validation_cap=384):
    """Select by an inner partition drawn *only* from training examples."""
    y=np.asarray(y,np.float64)
    rng=np.random.default_rng(seed);order=rng.permutation(len(y))
    nval=min(int(validation_cap),max(8,len(y)//5))
    inner_val,inner_train=order[:nval],order[nval:]
    if len(inner_train)<10:raise ValueError('too few points')
    candidates=[]
    for k in ks:
        if k<2:continue
        for method,penalty in [('knn',0.)]+[('linear',p) for p in penalties]:
            pred=local_predict(z[inner_train],y[inner_train],z[inner_val],k,penalty,method)
            candidates.append((stats(y[inner_val],pred)['mae'],method,int(k),float(penalty)))
    if not candidates:raise ValueError('no candidate methods')
    candidates.sort(key=lambda a:a[0])
    m,method,k,p=candidates[0]
    return {'method':method,'k':k,'penalty':p,'inner_validation_mae':m,
            'candidates':[{'mae':v,'method':t,'k':kk,'penalty':pp} for v,t,kk,pp in candidates]}


def compare(xtrain,ytrain, eval_sets, *, rank=32,seed=47,ks=(8,16,32,64),penalties=(.1,1.,10.)):
    """Return local predictions and diagnostics, keyed by evaluation deck.

    eval_sets: {name: (X, y)}. Eval labels are only used by stats(), after
    geometry and hyperparameters are finalized from training data alone.
    """
    geo=fit_geometry(xtrain,rank,seed)
    ytrain=np.asarray(ytrain,np.float64)
    config=select_method(geo['train'],ytrain,seed,ks,penalties)
    answer={'config':config,'effective_rank':int(geo['train'].shape[1]),'sets':{}}
    const=float(np.median(ytrain))
    for name,(x,y) in eval_sets.items():
        z=transform_geometry(geo,x)
        p=local_predict(geo['train'],ytrain,z,config['k'],config['penalty'],config['method'])
        b=np.full(len(p),const)
        answer['sets'][name]={'local':stats(y,p),'constant':stats(y,b),
                              'local_predictions':p.tolist(),'baseline_predictions':b.tolist()}
    return answer
