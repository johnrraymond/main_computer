#!/usr/bin/env python3
"""Analyze saved game-geometry smoke vectors without re-running Qwen.

Compares activation distances with independent arithmetic reference distances,
separately within exact game/level 3-offset neighborhoods and across larger
matched-scale samples. Outputs JSON and CSV. Never changes model weights.
"""
from __future__ import annotations
import argparse, csv, json, math
from pathlib import Path
import numpy as np
from scipy.stats import spearmanr
from scipy.spatial.distance import pdist, squareform

METRICS=('euclidean','manhattan','complex_log','whitened','cosine')

def load(root):
    root=Path(root)
    rows=[json.loads(line) for line in (root/'probes.jsonl').read_text(encoding='utf8').splitlines() if line.strip()]
    X=np.load(root/'activations.npy',mmap_mode='r')
    if len(rows)!=len(X):raise ValueError('vector count does not match probe count')
    mats={}
    for metric in METRICS:
        p=root/f'distance_{metric}.npy'
        if p.is_file():
            d=np.load(p,mmap_mode='r')
            if d.shape!=(len(rows),len(rows)):raise ValueError(f'{metric}: wrong shape')
            mats[metric]=d
    if not mats: raise ValueError('No distance_*.npy matrices found')
    return rows,X,mats

def rankcorr(x,y):
    x=np.asarray(x,float);y=np.asarray(y,float)
    good=np.isfinite(x)&np.isfinite(y)
    x=x[good];y=y[good]
    if len(x)<3 or np.ptp(x)==0 or np.ptp(y)==0:return None
    v=spearmanr(x,y).statistic
    return float(v) if np.isfinite(v) else None

def local_block(rows, d):
    """Fixed same-game same-level comparison, avoiding label confounds."""
    groups={}
    for i,r in enumerate(rows):groups.setdefault((r['game'],r['level']),[]).append(i)
    block_stats=[]; near=[];far=[];triples=0; inversions=0; ties=0
    for (game,level),ids in groups.items():
        ids=sorted(ids,key=lambda i:rows[i]['offset'])
        if len(ids)<3:continue
        mathdist=[]; actdist=[]
        for u in range(len(ids)):
            for v in range(u+1,len(ids)):
                i,j=ids[u],ids[v]
                mathdist.append(abs(rows[i]['answer']-rows[j]['answer']))
                actdist.append(float(d[i,j]))
                if abs(rows[i]['offset']-rows[j]['offset'])==1:near.append(float(d[i,j]))
                if abs(rows[i]['offset']-rows[j]['offset'])==2:far.append(float(d[i,j]))
        rho=rankcorr(mathdist,actdist)
        if rho is not None:block_stats.append(rho)
        if len(ids)==3 and [rows[i]['offset'] for i in ids]==[-1,0,1]:
            i,j,k=ids
            near_avg=.5*(float(d[i,j])+float(d[j,k])); far_d=float(d[i,k]);triples+=1
            if far_d<near_avg-1e-8:inversions+=1
            if abs(far_d-near_avg)<=1e-8:ties+=1
    return {'blocks':len(groups),'analyzable_blocks':len(block_stats),
            'mean_block_spearman':float(np.mean(block_stats)) if block_stats else None,
            'median_block_spearman':float(np.median(block_stats)) if block_stats else None,
            'near_offset1_mean_distance':float(np.mean(near)) if near else None,
            'far_offset2_mean_distance':float(np.mean(far)) if far else None,
            'far_to_near_ratio':float(np.mean(far)/np.mean(near)) if near and far and np.mean(near)>0 else None,
            'triples':triples,'far_closer_than_adjacent_rate':inversions/triples if triples else None,
            'ties':ties}

def analyze(rows,mats,seed=47,sample_pairs=100000):
    rng=np.random.default_rng(seed);N=len(rows)
    # Global pairs are deliberately *not* interpreted as a clean mathematical test.
    iu,ju=np.triu_indices(N,k=1)
    if len(iu)>sample_pairs:
        chosen=rng.choice(len(iu),sample_pairs,replace=False);iu=iu[chosen];ju=ju[chosen]
    vals=np.array([[r['x'],r['y'],r['p'],r['q'],r['answer']] for r in rows],float)
    # Dynamic range-aware answer geometry; also report exact answer-distance rank.
    ref_answer=np.abs(vals[iu,4]-vals[ju,4])
    ref_log_answer=np.abs(np.sign(vals[iu,4])*np.log1p(abs(vals[iu,4]))-
                          np.sign(vals[ju,4])*np.log1p(abs(vals[ju,4])))
    # Compare same level separately to reduce magnitude confounding.
    same_level=(np.array([r['level'] for r in rows])[iu]==np.array([r['level'] for r in rows])[ju])
    same_game=(np.array([r['game'] for r in rows])[iu]==np.array([r['game'] for r in rows])[ju])
    results={}
    for name,D in mats.items():
        d=np.asarray(D[iu,ju],float)
        if np.any(~np.isfinite(d)):raise ValueError(f'{name} nonfinite matrix distances')
        rec={'global_answer_spearman_DESCRIPTIVE':rankcorr(ref_answer,d),
             'global_signed_log_answer_spearman_DESCRIPTIVE':rankcorr(ref_log_answer,d),
             'same_level_answer_spearman':rankcorr(ref_answer[same_level],d[same_level]),
             'same_game_answer_spearman':rankcorr(ref_answer[same_game],d[same_game]),
             'controlled_offset':local_block(rows,D)}
        # Distances can be low for duplicated arithmetic but distinct named games.
        ix=np.where(same_level & ~same_game)[0]
        if len(ix)>0:
            order=np.argsort(ref_answer[ix])
            n=max(1,len(ix)//10)
            rec['cross_game_low_answer_delta_distance_mean']=float(np.mean(d[ix[order[:n]]]))
            rec['cross_game_high_answer_delta_distance_mean']=float(np.mean(d[ix[order[-n:]]]))
        results[name]=rec
    return {'schema':'nanojev.game-geometry-numeric-analyze.v1','points':N,
            'pairs_evaluated':len(iu),'method':'distance matrix correlations and matched game-level offset triples',
            'limitations':['Current probes are common payoff-template calculations with different game names, not 100 formal game algorithms.',
              'Only three offsets per game-level; a high correlation is not proof of smooth local geometry.',
              'Global correlations confound mathematical values with game wording and numerical scale.',
              'Some pairwise metrics are dissimilarities, not formal metrics.',
              'The analysis uses saved frozen distance matrices and does not measure Qwen answer accuracy.'],
            'metrics':results}

def run(root,output=None,seed=47,pairs=100000):
    root=Path(root);out=Path(output) if output else root
    rows,_,mats=load(root)
    result=analyze(rows,mats,seed,pairs)
    out.mkdir(parents=True,exist_ok=True)
    (out/'numeric_geometry_report.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    with (out/'numeric_geometry_scores.csv').open('w',newline='',encoding='utf8') as f:
        keys=['metric','global_answer_spearman_DESCRIPTIVE','same_level_answer_spearman','same_game_answer_spearman','mean_block_spearman','median_block_spearman','far_to_near_ratio','far_closer_than_adjacent_rate']
        wr=csv.DictWriter(f,fieldnames=keys);wr.writeheader()
        for name,rec in result['metrics'].items():
            local=rec['controlled_offset']
            wr.writerow({'metric':name,**{k:rec.get(k) for k in keys[1:4]},**{k:local.get(k) for k in keys[4:]}})
    print(json.dumps({'report':str(out/'numeric_geometry_report.json'), 'points':len(rows),'metrics':{name:{'local_median_rho':v['controlled_offset']['median_block_spearman'],'far_near_ratio':v['controlled_offset']['far_to_near_ratio'],'same_level_rho':v['same_level_answer_spearman']} for name,v in result['metrics'].items()}},indent=2))
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',default='runtime/reports/game-geometry-qwen')
    p.add_argument('--output',default=None)
    p.add_argument('--seed',type=int,default=47)
    p.add_argument('--pairs',type=int,default=100000)
    a=p.parse_args();run(a.input,a.output,a.seed,a.pairs)
if __name__=='__main__':main()
