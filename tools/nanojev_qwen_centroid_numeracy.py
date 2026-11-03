#!/usr/bin/env python3
"""Read-only numeracy microscope for saved five-centroid Qwen activations.

No model loading, no changes to captured files. All fitted models train on training
rows only. Answer-value holdout tests interpolation/extrapolation without repeating
exact labels. Sample holdout tests generalization to unseen prompts, not unseen labels.
"""
from __future__ import annotations
import argparse, csv, json, math
from pathlib import Path
import numpy as np

POPS=('C0_games','C1_shotgun_language','C2_fixed_payoff','C3_fixed_addition','C4_T00')

def safe(v):
    try:
        f=float(v)
        return f if np.isfinite(f) else None
    except (TypeError, ValueError):return None

def summarize(y,p):
    from scipy.stats import spearmanr
    e=np.abs(p-y)
    rho=spearmanr(y,p).statistic if len(np.unique(y))>1 and len(np.unique(p))>1 else np.nan
    scale=np.std(y)
    return {'n':len(y),'mae':safe(np.mean(e)),'median_ae':safe(np.median(e)),
            'rmse':safe(np.sqrt(np.mean((p-y)**2))),
            'normalized_mae':safe(np.mean(e)/(scale+1e-12)),
            'rounded_exact':safe(np.mean(np.rint(p)==y)),
            'within_1':safe(np.mean(e<=1)), 'within_5':safe(np.mean(e<=5)),
            'within_10':safe(np.mean(e<=10)),
            'answer_spearman':safe(rho),
            'signed_bias':safe(np.mean(p-y))}

def split_indices(y,seed,mode):
    rng=np.random.default_rng(seed)
    if mode=='answers':
        u=np.unique(y);rng.shuffle(u)
        hold=set(u[:max(1,len(u)//5)].tolist())
        te=np.isin(y,list(hold));tr=~te
    else:
        idx=rng.permutation(len(y));te=np.zeros(len(y),bool);te[idx[:max(1,len(y)//5)]]=True;tr=~te
    if tr.sum()<20 or te.sum()<5:raise ValueError(f'insufficient train/test observations for {mode}')
    return tr,te

def pair_geometry(X,y,rng,n_pairs=18000):
    from scipy.stats import spearmanr
    n=len(y)
    i=rng.integers(0,n,n_pairs*2);j=rng.integers(0,n,n_pairs*2)
    ok=(i!=j);i=i[ok][:n_pairs];j=j[ok][:n_pairs]
    a=X[i].astype(np.float64);b=X[j].astype(np.float64)
    d=np.linalg.norm(a-b,axis=1)
    dy=np.abs(y[i]-y[j]);valid=dy>0
    rho=spearmanr(d,dy).statistic if len(np.unique(d))>1 and len(np.unique(dy))>1 else np.nan
    positive=dy[valid]
    if len(positive):
        near=positive<=np.quantile(positive,.2)
        far=positive>=np.quantile(positive,.8)
        values=d[valid]
        ratio=float(np.median(values[far])/(np.median(values[near])+1e-12))
    else:ratio=None
    equal=(dy==0);diff=(dy>0)
    return {'pairs':len(d),'distance_answer_spearman':safe(rho),
            'far_near_median_ratio':safe(ratio),
            'same_answer_distance_median':safe(np.median(d[equal])) if equal.any() else None,
            'different_answer_distance_median':safe(np.median(d[diff])) if diff.any() else None,
            'same_answer_pair_count':int(equal.sum())}

def feature_controls(rows):
    # Only controls available consistently from the records, not target-derived values.
    columns=('a','b','p','q','offset','scale','residual','x','y')
    out=[]; selected=[]
    for key in columns:
        vals=[]
        for r in rows:
            value=r.get(key)
            if isinstance(value,(int,float)) and np.isfinite(value): vals.append(float(value))
            else:vals.append(np.nan)
        v=np.asarray(vals)
        if np.isfinite(v).mean()>.95 and np.nanstd(v)>0:
            out.append(v);selected.append(key)
    return (np.column_stack(out) if out else None),selected

def evaluate(X,y,rows,seed,rank,knn_k):
    from sklearn.decomposition import PCA
    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import StandardScaler
    from sklearn.neighbors import NearestNeighbors
    results={}
    for split in ('samples','answers'):
        tr,te=split_indices(y,seed,split)
        # Fit projection and standardization only on training activations.
        dims=max(1,min(rank,X.shape[1],int(tr.sum())-1))
        pca=PCA(n_components=dims,random_state=seed,svd_solver='randomized')
        ztr=pca.fit_transform(X[tr]);zte=pca.transform(X[te])
        scaler=StandardScaler().fit(ztr)
        ztr=scaler.transform(ztr);zte=scaler.transform(zte)
        baseline=np.full(int(te.sum()),np.median(y[tr]),dtype=np.float64)
        block={'train_n':int(tr.sum()),'test_n':int(te.sum()),'unique_train_answers':int(len(np.unique(y[tr]))),
               'unique_test_answers':int(len(np.unique(y[te]))),'pca_rank':dims,
               'pca_explained_variance':safe(sum(pca.explained_variance_ratio_)),
               'constant_median':summarize(y[te],baseline)}
        for alpha in (1.,100.,10000.):
            reg=Ridge(alpha=alpha).fit(ztr,y[tr])
            block[f'ridge_{int(alpha)}']=summarize(y[te],reg.predict(zte))
        # Robust monotonic compression handles very large, cancellation-rich result ranges.
        sy=np.sign(y[tr])*np.log1p(np.abs(y[tr]))
        reg=Ridge(alpha=100.).fit(ztr,sy)
        pred=reg.predict(zte)
        pred=np.sign(pred)*np.expm1(np.minimum(np.abs(pred),25.))
        block['signed_log_ridge']=summarize(y[te],pred)
        nbrs=NearestNeighbors(n_neighbors=min(knn_k,int(tr.sum())),algorithm='auto').fit(ztr)
        d,ind=nbrs.kneighbors(zte)
        weights=1./np.maximum(d,1e-5)
        pred=(y[tr][ind]*weights).sum(axis=1)/weights.sum(axis=1)
        block['knn_weighted']=summarize(y[te],pred)
        # Comparison to a simple transparent numerical-control model to identify
        # whether geometry has added information, not a causal attribution.
        c,keys=feature_controls(rows)
        if c is not None:
            csc=StandardScaler().fit(c[tr]);ct=csc.transform(c[tr]);cv=csc.transform(c[te])
            reg=Ridge(alpha=100.).fit(ct,y[tr]);block['metadata_control']={'features':keys,**summarize(y[te],reg.predict(cv))}
        results[split]=block
    return results

def analyze(directory,seed=47,rank=32,pairs=18000,knn_k=9):
    src=Path(directory)
    xfile=src/'activations.npy';rfile=src/'probes.jsonl'
    if not xfile.exists() or not rfile.exists():raise FileNotFoundError(f'missing activations.npy or probes.jsonl in {src}')
    X=np.load(xfile,mmap_mode='r')
    with rfile.open(encoding='utf8') as f:
        rows=[json.loads(line) for line in f if line.strip()]
    if len(rows)!=len(X):raise ValueError('probe-to-activation row mismatch')
    if not np.isfinite(X).all():raise ValueError('nonfinite activations')
    y=np.asarray([float(r['answer']) for r in rows],np.float64)
    if not np.isfinite(y).all():raise ValueError('nonfinite answers')
    # Data and logic checks. Labels are already supplied by capture; no hidden
    # correctness assertion is made for unfamiliar formats.
    checks={}
    for key in ('C2_fixed_payoff','C3_fixed_addition','C4_T00'):
        if src.name==key:
            fn=(lambda r:r['p']*r['a']-r['q']*r['b']) if key=='C2_fixed_payoff' else (lambda r:r['a']+r['b'])
            checks['answer_arithmetic_disagreements']=sum(fn(r)!=r['answer'] for r in rows)
            if checks['answer_arithmetic_disagreements']:raise ValueError('arithmetic label mismatch')
    rng=np.random.default_rng(seed)
    return {'schema':'nanojev.centroid-numeracy.v1','population':src.name,'points':len(y),
            'dimensions':int(X.shape[1]),'unique_answers':int(len(np.unique(y))),
            'answer_min':safe(np.min(y)),'answer_max':safe(np.max(y)),
            'seed':seed,'checks':checks,'pair_geometry':pair_geometry(X,y,rng,pairs),
            'decoders':evaluate(X,y,rows,seed,rank,knn_k),
            'qualification':'Associational diagnostic; no certified correction or model-weight update.'}

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input',default='runtime/reports/qwen-five-centroids')
    ap.add_argument('--population',choices=('all',)+POPS,default='all')
    ap.add_argument('--output',default=None)
    ap.add_argument('--seed',type=int,default=47)
    ap.add_argument('--rank',type=int,default=32)
    ap.add_argument('--pairs',type=int,default=18000)
    ap.add_argument('--knn-k',type=int,default=9)
    ap.add_argument('--local-surfaces',action='store_true',help='run 8100/900 train-only factorization and local linear/quadratic interpolation on saved vectors')
    ap.add_argument('--landmarks',type=int,default=256)
    ap.add_argument('--local-ks',default='16,32,64')
    args=ap.parse_args();root=Path(args.input)
    dest=Path(args.output) if args.output else root/'numeracy-diagnostic'
    dest.mkdir(parents=True,exist_ok=True)
    selected=POPS if args.population=='all' else (args.population,)
    summary=[]
    for pop in selected:
        directory=root/pop
        if not (directory/'activations.npy').exists():
            print(f'{pop}: no saved activations; skipping',flush=True);continue
        result=analyze(directory,args.seed,args.rank,args.pairs,args.knn_k)
        if args.local_surfaces:
            from tools.nanojev_qwen_centroid_local_math import run as local_run
            ks=tuple(int(v) for v in args.local_ks.split(',') if v.strip())
            local=local_run(directory,dest/'local-surfaces'/pop,seed=args.seed,rank=args.rank,landmarks=args.landmarks,ks=ks)
            result['local_surfaces']={'report':str(dest/'local-surfaces'/pop/'report.json'),
                'selected':local['selected'],'scores':local['scores']}
            print(f"  {pop}: local={local['scores']['local']['mae']:.6g} knn={local['scores']['knn_8_inverse']['mae']:.6g} constant={local['scores']['constant']['mae']:.6g}",flush=True)
        (dest/f'{pop}.json').write_text(json.dumps(result,indent=2),encoding='utf8')
        for split,block in result['decoders'].items():
            for name,values in block.items():
                if isinstance(values,dict) and 'mae' in values:
                    summary.append({'population':pop,'split':split,'decoder':name,'n':values['n'],
                                   'mae':values['mae'],'normalized_mae':values['normalized_mae'],
                                   'spearman':values['answer_spearman'],'within_10':values['within_10'],
                                   'rounded_exact':values['rounded_exact'],
                                   'geometry_rho':result['pair_geometry']['distance_answer_spearman']})
        metric=result['decoders']['samples']['ridge_100']
        print(f'{pop}: pairs_rho={result["pair_geometry"]["distance_answer_spearman"]}; '
              f'sample_ridge_spearman={metric["answer_spearman"]}; '
              f'normalized_MAE={metric["normalized_mae"]}',flush=True)
    if summary:
        with (dest/'summary.csv').open('w',newline='',encoding='utf8') as file:
            writer=csv.DictWriter(file,fieldnames=list(summary[0]));writer.writeheader();writer.writerows(summary)
    print(json.dumps({'output':str(dest),'populations_analyzed':sorted(set(r['population'] for r in summary))},indent=2))
if __name__=='__main__':main()
