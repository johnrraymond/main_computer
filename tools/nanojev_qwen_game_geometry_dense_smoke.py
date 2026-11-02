#!/usr/bin/env python3
"""Dense (100 games x 3 levels x 33 offsets) Qwen geometry smoke.

Source-only additive module: does not overwrite earlier 900-point results, alter
model weights, or create full quadratic matrices. Saves all capture vectors,
landmark dissimilarities, fixed-frame 2D coordinates, replay centroids and
local numerical geometry diagnostics. --mode synthetic is pipeline-only.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path
import numpy as np
from tools.nanojev_qwen_game_geometry_smoke import probes, capture_model, synthetic_vectors, FAMILIES

METRICS=('euclidean','manhattan','complex_log','whitened','cosine')

def distances(A,B,kind,center=None,scale=None):
    from scipy.spatial.distance import cdist
    A=np.asarray(A,dtype=np.float64); B=np.asarray(B,dtype=np.float64)
    if kind=='whitened':
        A=(A-center)/scale; B=(B-center)/scale
        return cdist(A,B,'euclidean')
    if kind=='manhattan': return cdist(A,B,'cityblock')
    if kind=='cosine': return cdist(A,B,'cosine')
    if kind=='complex_log':
        return 0.5*np.log1p(cdist(A,B,'sqeuclidean'))
    return cdist(A,B,'euclidean')

def choose_landmarks(rows,cap,seed):
    rng=np.random.default_rng(seed)
    groups={}
    for i,row in enumerate(rows): groups.setdefault((row['game'],row['level']),[]).append(i)
    # One representative per game-level first, then randomized additional samples.
    chosen=[int(rng.choice(ids)) for ids in groups.values()]
    if cap<len(chosen):
        # Choose balanced subset across game-level groups rather than first families.
        chosen=list(rng.choice(chosen,size=cap,replace=False))
    elif cap>len(chosen):
        rest=np.setdiff1d(np.arange(len(rows)),np.array(chosen),assume_unique=False)
        chosen+=list(map(int,rng.choice(rest,size=min(cap-len(chosen),len(rest)),replace=False)))
    return np.array(sorted(chosen),dtype=int)

def classical_landmark(D):
    # Non-Euclidean metrics get an explicit positive-eigenvalue projection;
    # negative spectrum is reported rather than interpreted as Euclidean.
    S=D**2; n=len(D)
    means=S.mean(axis=0); grand=means.mean()
    K=-0.5*(S-means[None,:]-means[:,None]+grand)
    vals,vec=np.linalg.eigh(K)
    ix=np.argsort(vals)[::-1]; vals=vals[ix]; vec=vec[:,ix]
    positive=vals>max(1e-10,abs(vals[0])*1e-10)
    if not positive.any(): raise ValueError('No positive MDS eigenvalues')
    rank=min(2,int(positive.sum()))
    lam=vals[:rank]; U=vec[:,:rank]
    Y=U*np.sqrt(lam)[None,:]
    # Embed additional points by Gower out-of-sample extension.
    # For landmark points it reproduces the same embedding (up to precision).
    return means,grand,U,lam,Y,{'positive_eigenvalues':int(positive.sum()),'negative_eigenvalue_count':int((vals < -1e-8).sum()),'positive_energy_fraction':float(np.maximum(vals[:rank],0).sum()/np.maximum(vals,0).sum())}

def extend(D_cross,means,grand,U,lam):
    sq=D_cross**2
    centered=-0.5*(sq-sq.mean(axis=1,keepdims=True)-means[None,:]+grand)
    out=centered @ (U/np.sqrt(lam)[None,:])
    if out.shape[1]==1: out=np.pad(out,((0,0),(0,1)))
    return out

def local_diagnostics(X,rows,kind,center,scale):
    from scipy.stats import spearmanr
    groups={}
    for i,row in enumerate(rows): groups.setdefault((row['game'],row['level']),[]).append(i)
    values=[]; near=[]; far=[]; locality=[]
    for group,idx in groups.items():
        if len(idx)<4: continue
        idx=np.array(sorted(idx,key=lambda i:rows[i]['offset']))
        oo=np.array([rows[i]['offset'] for i in idx],float)
        D=distances(X[idx],X[idx],kind,center,scale)
        up=np.triu_indices(len(idx),1)
        nd=np.abs(oo[:,None]-oo[None,:])[up]
        ad=D[up]
        rho=spearmanr(nd,ad).statistic
        if np.isfinite(rho):values.append(float(rho))
        near.extend(ad[nd==1].tolist());far.extend(ad[nd>=max(4,(oo.max()-oo.min())/2)].tolist())
        # Local nearest mathematical neighbors versus activation nearest neighbor.
        for j in range(len(idx)):
            dd=D[j].copy();dd[j]=np.inf
            mind=np.abs(oo-oo[j]);mind[j]=np.inf
            minval=np.min(mind)
            locality.append(float(mind[np.argmin(dd)]==minval))
    return {'groups':len(groups),'eligible_groups':len(values),'median_spearman_rho':float(np.median(values)) if values else None,
        'mean_spearman_rho':float(np.mean(values)) if values else None,
        'far_near_ratio':float(np.median(far)/np.median(near)) if near and far and np.median(near)>0 else None,
        'nearest_offset_neighbor_rate':float(np.mean(locality)) if locality else None,
        'near_pairs':len(near),'far_pairs':len(far)}

def run(args):
    out=Path(args.output)
    if out.exists() and any(out.iterdir()) and not args.overwrite:
        raise ValueError(f'Output not empty: {out}; use a new --output (or --overwrite)')
    out.mkdir(parents=True,exist_ok=True)
    offsets=tuple(range(-args.radius,args.radius+1))
    rows=probes(seed=args.seed,levels=args.levels,offsets=offsets)
    # The original generator uses small level-zero encounter counts; +/-16
    # otherwise creates negative encounters. Apply the same positive shift
    # within each game-level neighborhood and rebuild exact answers/prompts.
    for row in rows:
        row['x']+=args.radius+1; row['y']+=args.radius+1
        row['answer']=int(row['p']*row['x']-row['q']*row['y'])
        row['math_key']=[row['x'],row['y'],row['p'],row['q'],row['answer']]
        row['prompt']=(f"In the {row['game']} game ({row['family']}), an agent receives {row['p']} payoff units "
            f"for each of {row['x']} type-A encounters and loses {row['q']} payoff units "
            f"for each of {row['y']} type-B encounters. "
            "What is its exact total signed payoff? Answer with one integer.")
    if args.max_prompts:rows=rows[:args.max_prompts]
    if args.mode=='model':
        if not args.model:raise ValueError('--model is required in model mode')
        X,tokens=capture_model(rows,args.model,args.adapter,args.device,args.layer,args.max_length)
        provenance={'mode':'model','model':args.model,'adapter':args.adapter,'layer':args.layer,
            'token':'last_prompt_token','next_token_ids':tokens}
    else:
        X=synthetic_vectors(rows,seed=args.seed)
        provenance={'mode':'synthetic','warning':'NOT QWEN DATA'}
    np.save(out/'activations.npy',X)
    (out/'probes.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf8')
    landmarks=choose_landmarks(rows,min(args.landmarks,len(rows)),args.seed)
    np.save(out/'landmark_indices.npy',landmarks)
    center=np.mean(X[landmarks],axis=0,dtype=np.float64)
    scale=np.std(X[landmarks],axis=0,dtype=np.float64);scale=np.maximum(scale,1e-6)
    report={'schema':'nanojev.game-geometry-dense.v1','points':len(rows),'radius':args.radius,'levels':args.levels,
        'games':len(set(r['game'] for r in rows)),'landmarks':len(landmarks), 'provenance':provenance,'metrics':{},
        'note':'Local tests use all within-game-level offsets; 2D coordinates use a frozen landmark frame, not full 9900-point MDS.'}
    for kind in METRICS:
        DL=distances(X[landmarks],X[landmarks],kind,center,scale)
        np.fill_diagonal(DL,0)
        np.save(out/f'landmark_distance_{kind}.npy',DL.astype(np.float32))
        means,grand,U,lam,Y,spectrum=classical_landmark(DL)
        Z=np.empty((len(rows),2),np.float32)
        for start in range(0,len(rows),args.chunk):
            end=min(len(rows),start+args.chunk)
            Dc=distances(X[start:end],X[landmarks],kind,center,scale)
            Z[start:end]=extend(Dc,means,grand,U,lam).astype(np.float32)
        np.save(out/f'coordinates_{kind}.npy',Z)
        replay={};
        for family in FAMILIES:
            idx=[i for i,r in enumerate(rows) if r['family']==family]
            points=Z[idx].astype(np.float64)
            if len(points):replay[family]=(np.cumsum(points,axis=0)/np.arange(1,len(points)+1)[:,None]).tolist()
        (out/f'replay_centroids_{kind}.json').write_text(json.dumps(replay),encoding='utf8')
        diag=local_diagnostics(X,rows,kind,center,scale)
        diag.update(spectrum)
        report['metrics'][kind]=diag
        print(f'{kind}: local rho={diag["median_spearman_rho"]}, far/near={diag["far_near_ratio"]}',flush=True)
        if args.plots:
            from tools.nanojev_qwen_game_geometry_smoke import save_plot
            save_plot(out/f'mds_{kind}.png',Z,rows,f'{kind} — {args.mode} — {len(rows)} points (landmarks={len(landmarks)})')
    (out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps({'report':str(out/'report.json'),'points':len(rows),'landmarks':len(landmarks)},indent=2))
    return report

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('model','synthetic'),default='synthetic')
    p.add_argument('--model',default=None);p.add_argument('--adapter',default=None)
    p.add_argument('--device',default='cpu');p.add_argument('--layer',type=int,default=-1)
    p.add_argument('--max-length',type=int,default=256)
    p.add_argument('--levels',type=int,default=3);p.add_argument('--radius',type=int,default=16)
    p.add_argument('--landmarks',type=int,default=256);p.add_argument('--chunk',type=int,default=256)
    p.add_argument('--seed',type=int,default=47);p.add_argument('--max-prompts',type=int,default=0)
    p.add_argument('--output',default='runtime/reports/game-geometry-qwen-dense')
    p.add_argument('--plots',action='store_true');p.add_argument('--overwrite',action='store_true')
    args=p.parse_args()
    if not 1<=args.levels<=4:p.error('--levels must be 1..4')
    if not 1<=args.radius<=1000:p.error('--radius must be 1..1000')
    if not 4<=args.landmarks<=4096:p.error('--landmarks must be 4..4096')
    run(args)
if __name__=='__main__':main()
