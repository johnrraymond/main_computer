#!/usr/bin/env python3
"""Read-only compact smooth numerical surfaces over saved Qwen centroid activations.

Strict 8100/900 random holdout for 9000 vectors. All geometry, model selection,
centers, and hyperparameters are trained on the 8100 only. No model inference.
"""
from __future__ import annotations
import argparse, csv, json
from pathlib import Path
import numpy as np
from tools.nanojev_qwen_centroid_surface import (
    POPS, fit_landmark, project, predict_knn, score,
)

def sqdist(A, B):
    from sklearn.metrics.pairwise import euclidean_distances
    return np.maximum(euclidean_distances(A, B, squared=True), 0.)

def choose_centers(Z, count, seed):
    """Uniform random, reproducible unsupervised support centers."""
    return np.sort(np.random.default_rng(seed).choice(len(Z), min(count,len(Z)), replace=False))

def kernel(D2, scale):
    return np.exp(-D2 / (2.*scale*scale))

def experiment(directory, output, seed=47, landmarks=256, rank=32, metric='euclidean',
               center_counts=(32, 64, 128, 256), chunk=256):
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import Ridge
    src=Path(directory); out=Path(output)
    X=np.load(src/'activations.npy',mmap_mode='r')
    with (src/'probes.jsonl').open(encoding='utf8') as f:
        rows=[json.loads(s) for s in f if s.strip()]
    if len(rows)!=len(X) or X.ndim!=2 or not np.isfinite(X).all():
        raise ValueError('missing or invalid aligned activation/probe records')
    y=np.asarray([float(r['answer']) for r in rows]);
    if not np.isfinite(y).all(): raise ValueError('nonfinite answers')
    n=len(y)
    if n<100:raise ValueError('need >=100 observations')
    perm=np.random.default_rng(seed).permutation(n)
    n_test=900 if n==9000 else max(1,round(n*.1))
    tr,te=perm[:-n_test],perm[-n_test:]
    # All projection parameters, including whitening, are determined by training data.
    fit=fit_landmark(X,tr,metric,rank,landmarks,seed)
    Ztr=project(X[tr],fit,metric,chunk)
    Zte=project(X[te],fit,metric,chunk)
    scaler=StandardScaler().fit(Ztr)
    Ztr=scaler.transform(Ztr); Zte=scaler.transform(Zte)
    yi=y[tr]; yt=y[te]
    inner=np.random.default_rng(seed+1).permutation(len(tr))
    nv=max(10,len(tr)//10);va=inner[:nv];sub=inner[nv:]
    # Fixed baseline from the previous test; select its k and bandwidth inside training.
    baseline_candidates=[]
    for k in (8,32,128):
        for bw in ('inverse','gaussian'):
            pred=predict_knn(Ztr[sub],yi[sub],Ztr[va],k,bw)
            baseline_candidates.append((score(yi[va],pred)['mae'],k,bw))
    _,bk,bbw=min(baseline_candidates)
    knn=predict_knn(Ztr,yi,Zte,bk,bbw)
    # Smooth, low-rank RBF basis: fixed unsupervised training centers + ridge-readout.
    # Center selection and bandwidth selection never inspect the test labels or vectors.
    trials=[]
    for count in sorted(set(int(c) for c in center_counts if int(c)>0)):
        center_pos=choose_centers(Ztr[sub],count,seed+count)
        centers=Ztr[sub][center_pos]
        Dsub=sqdist(Ztr[sub],centers)
        Dval=sqdist(Ztr[va],centers)
        # Median nonzero within-training feature-space distances provide scale.
        from sklearn.metrics.pairwise import euclidean_distances
        pair=euclidean_distances(centers,centers)
        upper=pair[np.triu_indices(len(centers),1)]
        med=float(np.median(upper[upper>1e-12])) if (upper>1e-12).any() else 1.
        for multiplier in (.25, .5, 1., 2.):
            width=max(med*multiplier,1e-7)
            Fsub=kernel(Dsub,width);Fval=kernel(Dval,width)
            for alpha in (.01,1.,100.):
                reg=Ridge(alpha=alpha).fit(Fsub,yi[sub])
                pred=reg.predict(Fval)
                trials.append({'validation_mae':score(yi[va],pred)['mae'],
                               'count':count,'multiplier':multiplier,'alpha':alpha,
                               'width':width})
    winner=min(trials,key=lambda x:x['validation_mae'])
    # Refit on entire 8100 with a fresh deterministic center selection from 8100.
    count=winner['count']; center_pos=choose_centers(Ztr,count,seed+count)
    centers=Ztr[center_pos]
    pair=np.sqrt(sqdist(centers,centers))
    upper=pair[np.triu_indices(len(centers),1)]
    med=float(np.median(upper[upper>1e-12])) if (upper>1e-12).any() else 1.
    width=max(med*winner['multiplier'],1e-7)
    Ftr=kernel(sqdist(Ztr,centers),width)
    Fte=kernel(sqdist(Zte,centers),width)
    reg=Ridge(alpha=winner['alpha']).fit(Ftr,yi)
    smooth=reg.predict(Fte)
    constant=np.full(len(te),float(np.median(yi)))
    smooth_score=score(yt,smooth);knn_score=score(yt,knn);const_score=score(yt,constant)
    out.mkdir(parents=True,exist_ok=True)
    np.savez_compressed(out/'surface_model.npz',center_coordinates=centers.astype('float32'),
        readout_coefficients=reg.coef_,intercept=reg.intercept_,width=width,
        coordinate_mean=scaler.mean_,coordinate_scale=scaler.scale_,
        landmark_indices=fit['ids'],landmark_activations=fit['L'].astype('float32'),
        landmark_rowmean=fit['rowmean'],landmark_grand=fit['grand'],
        landmark_basis=fit['basis'],metric=np.asarray(metric),rank=fit['rank'],
        whitening_mean=fit['mean'],whitening_scale=fit['scale'])
    np.save(out/'train_indices.npy',tr);np.save(out/'test_indices.npy',te)
    with (out/'heldout_predictions.csv').open('w',encoding='utf8',newline='') as f:
        w=csv.writer(f);w.writerow(('index','true_y','smooth_yhat','knn_yhat','constant_yhat','smooth_abs_error','knn_abs_error'))
        for i,a,p,k,b in zip(te,yt,smooth,knn,constant):
            w.writerow((int(i),float(a),float(p),float(k),float(b),float(abs(a-p)),float(abs(a-k))))
    result={'schema':'nanojev.centroid-compact-surface.v1','population':src.name,
        'train':len(tr),'holdout':len(te),'seed':seed,'landmarks':len(fit['ids']),
        'rank':fit['rank'],'metric':metric,'surface_centers':len(centers),
        'surface_bandwidth':width,'surface_alpha':winner['alpha'],
        'surface_inner_validation_mae':winner['validation_mae'],
        'surface':smooth_score,'knn':knn_score,'constant':const_score,
        'knn_inner_selected':{'k':bk,'bandwidth':bbw},
        'smooth_minus_knn_mae':smooth_score['mae']-knn_score['mae'],
        'surface_relative_error_reduction_vs_constant':1.-smooth_score['mae']/max(const_score['mae'],1e-12),
        'trial_count':len(trials),'trial_validation':trials,
        'limits':'Random heldout prompts can share numerically similar templates; this is interpolation, not proven transfer. Center number compresses prediction surface, not the saved activation vectors.'}
    (out/'report.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',default='runtime/reports/qwen-five-centroids')
    p.add_argument('--population',choices=('all',)+POPS,default='all')
    p.add_argument('--output')
    p.add_argument('--seed',type=int,default=47)
    p.add_argument('--landmarks',type=int,default=256)
    p.add_argument('--rank',type=int,default=32)
    p.add_argument('--metric',choices=('euclidean','manhattan','whitened','cosine','complex_log'),default='euclidean')
    p.add_argument('--centers',default='32,64,128,256')
    p.add_argument('--chunk',type=int,default=256)
    a=p.parse_args();root=Path(a.input);dest=Path(a.output) if a.output else root/'compact-surface'
    counts=tuple(int(x.strip()) for x in a.centers.split(',') if x.strip());summary=[]
    for name in POPS if a.population=='all' else (a.population,):
        if not (root/name/'activations.npy').exists():
            print(f'{name}: no saved activations; skipping',flush=True);continue
        r=experiment(root/name,dest/name,a.seed,a.landmarks,a.rank,a.metric,counts,a.chunk)
        summary.append({k:r[k] for k in ('population','surface_centers','surface','knn','constant')})
        print(f"{name}: centers={r['surface_centers']} smooth_MAE={r['surface']['mae']:.6g} knn_MAE={r['knn']['mae']:.6g} constant_MAE={r['constant']['mae']:.6g} smooth_rho={r['surface']['spearman']}",flush=True)
    dest.mkdir(parents=True,exist_ok=True)
    (dest/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
    print(json.dumps({'output':str(dest),'completed':[x['population'] for x in summary]},indent=2))
if __name__=='__main__':main()
