#!/usr/bin/env python3
"""Game-atlas activation geometry smoke (measurement only; never trains Qwen).

100 distinct named game scenarios across 10 mathematical game families; three
complexity levels and controlled numerical perturbations per game. Extract the
last prompt-token residual-stream output from a real Qwen HF model or run a
clearly labelled synthetic pipeline check. Evaluate several pseudometric/
metric variants, full distance matrices, numeric 2D MDS, held-out landmark
placement, and replay centroid summaries. No history is required.

Examples:
  python -m tools.nanojev_qwen_game_geometry_smoke --mode synthetic --output runtime/reports/game-geometry-smoke
  python -m tools.nanojev_qwen_game_geometry_smoke --mode model --model Qwen/Qwen3-0.6B --device cuda --output runtime/reports/game-geometry-qwen
  python -m tools.nanojev_qwen_game_geometry_smoke --mode model --model C:/models/qwen --adapter C:/runs/adapter --device cuda

--adapter accepts a PEFT adapter directory only, not the joint trainer's
working.pt without an explicit adapter export. Actual weights provenance is
recorded in the report. Model mode requires transformers and torch.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np

FAMILIES = {
 'normal_form': ('Prisoners Dilemma','Stag Hunt','Chicken','Matching Pennies','Battle of the Sexes','Assurance','Deadlock','Harmony','Coordination','Volunteer Dilemma'),
 'evolutionary': ('Hawk Dove','Snowdrift','Evolutionary Prisoners Dilemma','Replicator Competition','Evolutionary RPS','Invasion Fitness','ESS Challenge','Public Goods Evolution','Mutualism Invasion','Evolutionary Coordination'),
 'cooperation': ('Public Goods','Common Pool Resource','Tragedy of Commons','Trust','Ultimatum','Dictator','Centipede','Threshold Contribution','Club Good','Volunteer Provision'),
 'signaling': ('Spence Signaling','Cheap Talk','Sender Receiver','Bayesian Persuasion','Market for Lemons','Screening','Inspection','Reputation Signaling','Costly Signaling','Entry Deterrence'),
 'sequential': ('Stackelberg','Sequential Bargaining','Sequential Entry','Leader Follower','Sequential Coordination','Commitment','Take or Pass','Sequential Matching','Threat Bargaining','Sequential Assurance'),
 'stochastic': ('Repeated Prisoners Dilemma','Repeated Stag Hunt','Markov Competition','Stochastic Resource','Repeated Trust','Repeated Public Goods','Discounted Cooperation','Stochastic Switching','Repeated Inspection','Finite Horizon Bargaining'),
 'ecological': ('Lotka Volterra Competition','Predator Prey Strategic Harvest','Host Parasite','Mutualistic Investment','Territory Defense','Patch Foraging','Producer Scrounger','Interference Competition','Resource Defense','Ecological Invasion'),
 'auction': ('First Price Auction','Second Price Auction','All Pay Auction','English Auction','Dutch Auction','Double Auction','Cournot Competition','Bertrand Competition','Nash Bargaining','Rent Seeking'),
 'network': ('Network Coordination','Network Public Goods','Spatial Prisoners Dilemma','Network Vaccination','Network Security','Routing Congestion','Local Imitation','Diffusion Competition','Network Formation','Graphical Coordination'),
 'control': ('Pursuit Evasion','Differential Pursuit','Target Defense','Patrolling','Search Evasion','Resource Allocation Blotto','Continuous Contest','Optimal Harvest Contest','Time to Capture','Dynamic Interception'),
}
assert sum(map(len,FAMILIES.values())) == 100


def probes(seed=47, levels=3, offsets=(-1,0,1)):
    """Exact integer/payoff prompts; labels are game contexts, not full solvers."""
    rng=np.random.default_rng(seed)
    out=[]
    for family,names in FAMILIES.items():
        for name in names:
            # Stable per-name base values: all questions are deterministic.
            base=int(rng.integers(5,25)); rate=int(rng.integers(2,13))
            for level in range(levels):
                scale=(1, 100, 10000, 1000000)[min(level,3)]
                for offset in offsets:
                    x=base*scale+offset
                    y=(base+rate)*scale-offset
                    p=rate+level; q=rate+level+3
                    # Mathematically transparent payoff structure, shared across genres.
                    # This is an arithmetic game probe, not an exhaustive game-theory solution.
                    value=p*x-q*y
                    prompt=(f'In the {name} game ({family}), an agent receives {p} payoff units '
                            f'for each of {x} type-A encounters and loses {q} payoff units '
                            f'for each of {y} type-B encounters. '
                            'What is its exact total signed payoff? Answer with one integer.')
                    out.append(dict(id=f'{family}:{name}:L{level}:O{offset}', family=family,
                                    game=name, level=level, offset=offset,
                                    x=x,y=y,p=p,q=q,answer=int(value),prompt=prompt,
                                    math_key=[int(x),int(y),int(p),int(q),int(value)]))
    return out


def capture_model(rows, model_name, adapter, device, layer, max_length):
    import torch
    from transformers import AutoTokenizer, AutoModelForCausalLM
    tok=AutoTokenizer.from_pretrained(model_name,trust_remote_code=False)
    kwargs={'trust_remote_code':False,'output_hidden_states':True}
    if device.startswith('cuda'):
        kwargs['torch_dtype']=torch.float16
    model=AutoModelForCausalLM.from_pretrained(model_name,**kwargs)
    if adapter:
        try: from peft import PeftModel
        except ImportError as ex: raise RuntimeError('PEFT is required for --adapter') from ex
        if not (Path(adapter)/'adapter_config.json').is_file():
            raise ValueError('--adapter must be a PEFT adapter directory, not working.pt')
        model=PeftModel.from_pretrained(model,adapter)
    model.to(device).eval()
    vectors=[]; predictions=[]
    with torch.inference_mode():
        for i,row in enumerate(rows):
            tokens=tok(row['prompt'],return_tensors='pt',truncation=True,max_length=max_length)
            tokens={k:v.to(device) for k,v in tokens.items()}
            result=model(**tokens,output_hidden_states=True,return_dict=True)
            hs=result.hidden_states[layer][0,-1].detach().float().cpu().numpy()
            vectors.append(hs)
            # This is a next-token prediction, NOT the complete arithmetic answer.
            predictions.append(int(torch.argmax(result.logits[0,-1]).item()))
            if (i+1)%100==0: print(f'captured {i+1}/{len(rows)}',flush=True)
    return np.asarray(vectors,np.float32), predictions


def synthetic_vectors(rows,dim=64,seed=47):
    """Deterministic pipeline-only data; NEVER evidence of real Qwen geometry."""
    rng=np.random.default_rng(seed)
    family_index={name:i for i,name in enumerate(FAMILIES)}
    vec=[]
    for row in rows:
        v=np.zeros(dim,dtype=np.float32)
        v[family_index[row['family']]]=3
        v[10+row['level']]=2
        v[16]=np.sign(row['answer'])*math.log1p(abs(row['answer']))
        v[17]=math.log1p(row['x']);v[18]=math.log1p(row['y'])
        v[19]=row['offset']*.2
        v+=rng.normal(0,.1,size=dim).astype(np.float32)
        vec.append(v)
    return np.array(vec,np.float32)


def pairwise(X,kind):
    from scipy.spatial.distance import pdist, squareform
    if kind=='manhattan': d=squareform(pdist(X,metric='cityblock'))
    elif kind=='euclidean': d=squareform(pdist(X,metric='euclidean'))
    elif kind=='cosine': d=squareform(pdist(X,metric='cosine'))
    elif kind=='whitened':
        from sklearn.preprocessing import StandardScaler
        Z=StandardScaler().fit_transform(X)
        d=squareform(pdist(Z,metric='euclidean'))
    elif kind=='complex_log':
        # Paired-coordinate squared amplitude; normalized log-amplitude dissimilarity.
        # Undo log before MDS only when squared-Euclidean structure is required.
        sq=squareform(pdist(X,metric='sqeuclidean'))
        d=0.5*np.log1p(np.maximum(sq,0))
    else: raise ValueError(kind)
    np.fill_diagonal(d,0)
    return np.asarray(d,dtype=np.float32)


def embed(D,kind,seed):
    # Classical metric MDS for Euclidean variants; stress-MDS for Manhattan/cosine.
    from sklearn.manifold import MDS
    if kind in ('manhattan','cosine','complex_log'):
        try: obj=MDS(n_components=2,dissimilarity='precomputed',random_state=seed,
                     n_init=1,max_iter=100,normalized_stress='auto')
        except TypeError: obj=MDS(n_components=2,dissimilarity='precomputed',random_state=seed,n_init=1,max_iter=100)
        Z=obj.fit_transform(D.astype(float)); stress=float(obj.stress_)
    else:
        from scipy.linalg import eigh
        n=len(D); S=D.astype(np.float64)**2
        K=-.5*(S-S.mean(axis=0)[None,:]-S.mean(axis=1)[:,None]+S.mean())
        vals,vec=eigh(K,subset_by_index=[max(0,n-2),n-1]) if n>=2 else (np.array([0.]),np.zeros((n,1)))
        order=np.argsort(vals)[::-1];vals=vals[order];vec=vec[:,order]
        Z=vec*np.sqrt(np.maximum(vals,0))[None,:]
        if Z.shape[1]<2:Z=np.pad(Z,((0,0),(0,2-Z.shape[1])))
        stress=float(np.linalg.norm(square_dist(Z)-D.astype(float))/(np.linalg.norm(D)+1e-12))
    return Z.astype(np.float32),stress


def square_dist(Z):
    from scipy.spatial.distance import pdist,squareform
    return squareform(pdist(Z))


def placement_test(X,Z,rows,seed):
    # Held-out placement using frozen geometry anchored in training points.
    from sklearn.linear_model import Ridge
    from sklearn.metrics import pairwise_distances
    rng=np.random.default_rng(seed);idx=rng.permutation(len(rows))
    nval=max(1,len(rows)//5);val=idx[:nval];train=idx[nval:]
    reg=Ridge(alpha=10.0).fit(X[train],Z[train]);pred=reg.predict(X[val])
    # This tests mapping to the *existing* factorization, not out-of-sample MDS truth.
    rmse=float(np.sqrt(np.mean((pred-Z[val])**2)))
    neighbor=pairwise_distances(X[val],X[train]).argmin(axis=1)
    nearest_ref=np.mean(np.linalg.norm(Z[val]-Z[train][neighbor],axis=1))
    return {'heldout':len(val),'placement_rmse':rmse,'input_nn_2d_distance':float(nearest_ref),
            'note':'Coordinates were fitted on all points; use disjoint matrix fitting for unbiased geometry generalization.'}


def save_plot(path,Z,rows,title):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,ax=plt.subplots(figsize=(8,6))
    for family in FAMILIES:
        ix=[i for i,r in enumerate(rows) if r['family']==family]
        if not ix: continue
        coords=Z[ix]
        ax.scatter(coords[:,0],coords[:,1],s=7,alpha=.45,label=family)
        mu=coords.mean(axis=0)
        ax.scatter(mu[0],mu[1],s=70,marker='x',color='black')
        ax.text(mu[0],mu[1],family,fontsize=7)
    ax.set_title(title);ax.legend(fontsize=6,ncol=2);ax.set_xlabel('MDS 1');ax.set_ylabel('MDS 2')
    fig.tight_layout();fig.savefig(path,dpi=135);plt.close(fig)


def run(args):
    from sklearn.metrics import silhouette_score
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    rows=probes(seed=args.seed,levels=args.levels)
    if args.max_prompts: rows=rows[:args.max_prompts]
    if args.mode=='model':
        if not args.model: raise ValueError('Real model mode requires --model')
        X,tokens=capture_model(rows,args.model,args.adapter,args.device,args.layer,args.max_length)
        provenance={'mode':'model','model':args.model,'adapter':args.adapter,'layer':args.layer,
                    'token':'last_prompt_token','output':'residual_hidden_state','next_token_ids':tokens}
    else:
        X=synthetic_vectors(rows,seed=args.seed)
        provenance={'mode':'synthetic','warning':'PIPELINE CHECK ONLY: NOT QWEN ACTIVATIONS'}
    np.save(output/'activations.npy',X)
    (output/'probes.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
    report={'schema':'nanojev.game-geometry-smoke.v1','provenance':provenance,'points':len(rows),
            'games':len({r['game'] for r in rows}),'families':len({r['family'] for r in rows}),
            'dimensions':X.shape[1],'variants':{}}
    for kind in ('euclidean','manhattan','complex_log','whitened','cosine'):
        D=pairwise(X,kind)
        Z,stress=embed(D,kind,args.seed)
        np.save(output/f'distance_{kind}.npy',D)
        np.save(output/f'coordinates_{kind}.npy',Z)
        centroid_history={}
        for family in FAMILIES:
            indices=[i for i,r in enumerate(rows) if r['family']==family]
            centroid_history[family]=np.cumsum(Z[indices],axis=0)/(np.arange(len(indices))[:,None]+1) if indices else np.empty((0,2))
        (output/f'replay_centroids_{kind}.json').write_text(json.dumps({k:v.tolist() for k,v in centroid_history.items()}))
        if args.plots: save_plot(output/f'mds_{kind}.png',Z,rows,f'{kind} — {args.mode} — {len(rows)} probes')
        family_labels=[r['family'] for r in rows]
        try: separation=float(silhouette_score(Z,family_labels)) if len(set(family_labels))>1 else None
        except ValueError: separation=None
        report['variants'][kind]={'mds_stress':stress,'family_silhouette_2d':separation,
                                   'placement':placement_test(X,Z,rows,args.seed)}
        print(f'{kind}: stress={stress:.4g}; family_silhouette={separation}',flush=True)
    (output/'report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'report':str(output/'report.json'),'points':len(rows),'mode':args.mode},indent=2))
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('synthetic','model'),default='synthetic')
    p.add_argument('--model',help='HF name or local model directory')
    p.add_argument('--adapter',help='PEFT adapter directory (not working.pt)')
    p.add_argument('--device',default='cpu');p.add_argument('--layer',type=int,default=-1)
    p.add_argument('--max-length',type=int,default=256)
    p.add_argument('--levels',type=int,default=3)
    p.add_argument('--max-prompts',type=int,default=0)
    p.add_argument('--seed',type=int,default=47)
    p.add_argument('--output',default='runtime/reports/nanojev-game-geometry-smoke')
    p.add_argument('--plots',action='store_true')
    args=p.parse_args()
    if args.levels<1 or args.levels>4: p.error('--levels must be in [1,4]')
    run(args)
if __name__=='__main__':main()
