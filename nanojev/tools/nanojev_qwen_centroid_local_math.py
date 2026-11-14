"""Local smooth numerical regression on saved centroid data; no model inference.

Fits landmark coordinates from training activations only. Hyperparameter choice is
made within 8100 training rows, before projecting/scoring the 900-row test set.
"""
from __future__ import annotations
import numpy as np
from pathlib import Path
from tools.nanojev_qwen_centroid_surface import fit_landmark, project, predict_knn, score


def local_predict(train, y, query, k=32, degree=1, alpha=1.0):
    """Distance-weighted local ridge; quadratic uses diagonal second-order terms.

    The fitted intercept at query location is the prediction. Target centering
    and regularized local differences avoid unstable absolute-value regression.
    """
    from scipy.linalg import solve
    from sklearn.neighbors import NearestNeighbors
    from sklearn.preprocessing import PolynomialFeatures
    train=np.asarray(train, dtype=np.float64)
    query=np.asarray(query,dtype=np.float64)
    y=np.asarray(y,dtype=np.float64)
    nn=NearestNeighbors(n_neighbors=min(int(k),len(train))).fit(train)
    distances, indices=nn.kneighbors(query)
    predictions=np.empty(len(query),dtype=np.float64)
    for i,(d,ids) in enumerate(zip(distances,indices)):
        delta=train[ids]-query[i]
        r=max(float(d[-1]),1.e-8)
        local=delta/r
        if degree==2:
            # Full quadratic interactions (32D -> 560 terms) require many more
            # neighbors than we have; diagonal quadratic is explicit/controlled.
            local=np.concatenate((local,local*local),axis=1)
        w=np.exp(-.5*(d/r)**2)
        y0=float(np.sum(w*y[ids])/np.sum(w))
        # Dual ridge solves k x k rather than feature-by-feature for quadratic.
        F=np.column_stack((np.ones(len(ids)),local))
        A=F.T@(w[:,None]*F)
        # Intercept weakly regularized for robustness, while local slopes get alpha.
        regularizer=np.full(F.shape[1],max(alpha,1.e-10),np.float64)
        regularizer[0]=1.e-8
        A.flat[::len(A)+1]+=regularizer
        b=F.T@(w*(y[ids]-y0))
        coef=solve(A,b,assume_a='pos',check_finite=False)
        predictions[i]=y0+coef[0]
    return predictions


def run(directory, output, seed=47, landmarks=256, rank=32, metric='euclidean',
        chunk=256, ks=(16,32,64), alphas=(.1,1.,10.)):
    from sklearn.preprocessing import StandardScaler
    import json, csv
    src=Path(directory);out=Path(output)
    X=np.load(src/'activations.npy',mmap_mode='r')
    rows=[json.loads(line) for line in (src/'probes.jsonl').read_text(encoding='utf8').splitlines() if line.strip()]
    if len(rows)!=len(X) or X.ndim!=2 or not np.isfinite(X).all():
        raise ValueError('invalid or misaligned activation/probe data')
    y=np.array([float(row['answer']) for row in rows],dtype=np.float64)
    if not np.isfinite(y).all(): raise ValueError('invalid numerical answers')
    n=len(y)
    if n<100: raise ValueError('need >= 100 saved vectors')
    permutation=np.random.default_rng(seed).permutation(n)
    count=900 if n==9000 else max(1,int(round(n*.1)))
    tr,te=permutation[:-count],permutation[-count:]
    fit=fit_landmark(X,tr,metric,rank,landmarks,seed)
    Ztr=project(X[tr],fit,metric,chunk)
    Zte=project(X[te],fit,metric,chunk)
    scaler=StandardScaler().fit(Ztr)
    Ztr=scaler.transform(Ztr);Zte=scaler.transform(Zte)
    yt=y[tr];holdout=y[te]
    inner=np.random.default_rng(seed+1).permutation(len(tr))
    nv=max(10,int(len(tr)//10));validation=inner[:nv];sub=inner[nv:]
    trials=[]
    for degree in (1,2):
        for k in ks:
            for alpha in alphas:
                predicted=local_predict(Ztr[sub],yt[sub],Ztr[validation],k,degree,alpha)
                trials.append({'degree':degree,'k':int(k),'alpha':float(alpha),
                    'validation_mae':score(yt[validation],predicted)['mae']})
    best=min(trials,key=lambda d:d['validation_mae'])
    yh=local_predict(Ztr,yt,Zte,best['k'],best['degree'],best['alpha'])
    knn=predict_knn(Ztr,yt,Zte,8,'inverse')
    constant=np.full(len(te),np.median(yt),dtype=np.float64)
    reports={'local':score(holdout,yh),'knn_8_inverse':score(holdout,knn),
             'constant':score(holdout,constant)}
    out.mkdir(parents=True,exist_ok=True)
    np.save(out/'train_indices.npy',tr);np.save(out/'test_indices.npy',te)
    with (out/'heldout_predictions.csv').open('w',newline='',encoding='utf8') as file:
        w=csv.writer(file)
        w.writerow(('original_index','actual','local_yhat','knn_yhat','constant_yhat'))
        for i,a,p,k,b in zip(te,holdout,yh,knn,constant):
            w.writerow((int(i),float(a),float(p),float(k),float(b)))
    report={'schema':'nanojev.centroid-local-math.v1','population':src.name,
        'train':len(tr),'holdout':len(te),'seed':seed,'rank':fit['rank'],
        'landmarks':len(fit['ids']),'metric':metric,'selected':best,
        'scores':reports,'validation_trials':trials,
        'note':'Same seeded split as surface experiment. Model selected on inner training validation. Test withheld from fitting and model selection. Quadratic means diagonal second-order terms.'}
    (out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    return report
