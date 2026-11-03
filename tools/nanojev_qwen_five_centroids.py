#!/usr/bin/env python3
"""Five independently factorized 9k-probe Qwen populations, no model training.

C0 shared-payoff 100 named game contexts; C1 eight actual shotgun surface modes;
C2 fixed signed-product equation; C3 fixed addition; C4 source-grounded
T00 numeric-terminal ordered-pair reduction. Model held open for all captures.
"""
from __future__ import annotations
import argparse, hashlib, json, math, re
from pathlib import Path
import numpy as np
from tools.nanojev_qwen_game_geometry_smoke import FAMILIES
from tools.nanojev_qwen_game_geometry_dense_smoke import distances, classical_landmark, extend
from tools.nanojev_qwen_shotgun import MODES, case as shotgun_case

NAMES=tuple((family,name) for family,names in FAMILIES.items() for name in names)
POPS=('C0_games','C1_shotgun_language','C2_fixed_payoff','C3_fixed_addition','C4_T00')
METRICS=('euclidean','manhattan','complex_log','whitened','cosine')

def make_rows(population,n=9000,seed=47,limit=1024):
    if population not in POPS:raise ValueError('UNKNOWN_CENTROID')
    rng=np.random.default_rng(seed+POPS.index(population)*1009)
    rows=[]; seen=set(); i=0
    # Deterministic structured distribution: edge, cancellation, zero, and general values.
    while len(rows)<n:
        if population=='C0_games':
            family,game=NAMES[i%len(NAMES)]
            p=2+(i//100)%19;q=2+(i//1900)%17
            scale=(1,10,100,1000,10000)[(i//500)%5]
            offset=(i//100)%90-45
            a=(9+p)*scale+offset+46;b=(9+q)*scale-offset+46
            answer=p*a-q*b
            prompt=(f'In the {game} game ({family}), an agent receives {p} payoff units for each of {a} '
                    f'type-A encounters and loses {q} payoff units for each of {b} type-B encounters. '
                    'What is its exact total signed payoff? Answer with one integer.')
            meta={'game':game,'family':family,'p':p,'q':q,'a':a,'b':b,'offset':offset,'scale':scale}
        elif population=='C1_shotgun_language':
            mode=MODES[i%len(MODES)]
            shot=shotgun_case(mode,10000+i,seed=seed)
            prompt=shot['source']; answer=int(shot['metadata']['expected_balance'])
            meta={'mode':mode,**shot['metadata']}
        elif population=='C2_fixed_payoff':
            p=int(rng.integers(1,25));q=int(rng.integers(1,25))
            a=int(rng.integers(0,limit+1));b=int(rng.integers(0,limit+1))
            if i%4==0:b=int(np.clip(round(p*a/q)+int(rng.integers(-8,9)),0,limit))
            answer=p*a-q*b
            prompt=f'PROBLEM: ({p}*{a})-({q}*{b})'
            meta={'p':p,'q':q,'a':a,'b':b}
        elif population=='C3_fixed_addition':
            # Same operation and format, wider signed operands than T00.
            a=int(rng.integers(-limit,limit+1)); b=int(rng.integers(-limit,limit+1))
            if i%3==0:b=int(np.clip(-a+int(rng.integers(-32,33)),-limit,limit))
            answer=a+b;prompt=f'PROBLEM: {a} + {b}'
            meta={'a':a,'b':b}
        else:
            # Actual T00 numeric-terminal definition: ordered pairs in [-limit,limit]
            # with residual constrained to [-R,R]; preserve canonical ladder source.
            # Use deterministic enumeration across residual cells and operand bands.
            radius=min(16,limit)
            residual=(i% (2*radius+1))-radius
            # Modulo with coprime stride traverses operand values before repetition.
            a=-limit+((i//(2*radius+1)*577 + i//n + seed)% (2*limit+1))
            # Ensure b in range by clipping a into the feasible source interval.
            a=int(np.clip(a,max(-limit,residual-limit),min(limit,residual+limit)))
            b=residual-a
            # Exact T00 source grammar from nanojev_qwen_problem_answer_stage1._expr
            # and numeric ladder _case: PROBLEM: signed_leaf+signed_leaf.
            literal=lambda value: str(value) if value>=0 else f'({value})'
            prompt=f'PROBLEM: {literal(a)}+{literal(b)}';answer=int(a+b)
            meta={'a':int(a),'b':int(b),'residual':residual,'source_contract':'T00.numeric'}
        i+=1
        if prompt in seen: continue
        seen.add(prompt)
        rows.append({'id':f'{population}:{i-1:05d}','population':population,'index':i-1,
                     'prompt':prompt,'answer':int(answer),**meta})
    return rows

def capture(rows,args, model=None, tokenizer=None):
    if args.mode=='synthetic':
        rng=np.random.default_rng(args.seed)
        # Synthetic geometry intentionally carries the correct answer; never Qwen evidence.
        ans=np.asarray([r['answer'] for r in rows],np.float64)
        X=np.column_stack([np.sign(ans)*np.log1p(np.abs(ans)),
                           np.sin(ans*.03),np.cos(ans*.03),
                           rng.normal(size=(len(rows),29))*.1]).astype(np.float32)
        return X
    import torch
    chunks=[]
    with torch.inference_mode():
        for k,row in enumerate(rows):
            inp=tokenizer(row['prompt'],return_tensors='pt',truncation=True,max_length=args.max_length)
            inp={key:value.to(args.device) for key,value in inp.items()}
            out=model(**inp,output_hidden_states=True,return_dict=True)
            chunks.append(out.hidden_states[args.layer][0,-1].float().cpu().numpy())
            if (k+1)%500==0:print(f'  {row["population"]}: captured {k+1}/{len(rows)}',flush=True)
    return np.asarray(chunks,np.float32)

def geometry(X,rows,out,args):
    from scipy.stats import spearmanr
    landmarks=np.sort(np.random.default_rng(args.seed).choice(len(rows),size=min(len(rows),args.landmarks),replace=False))
    np.save(out/'landmark_indices.npy',landmarks)
    center=X[landmarks].mean(axis=0,dtype=np.float64)
    scale=np.maximum(X[landmarks].std(axis=0,dtype=np.float64),1e-6)
    results={}
    rng=np.random.default_rng(args.seed)
    pairs=rng.integers(0,len(rows),size=(min(args.pairs,len(rows)*10),2))
    pairs=pairs[pairs[:,0]!=pairs[:,1]]
    y=np.asarray([r['answer'] for r in rows],np.float64)
    math_dist=np.abs(y[pairs[:,0]]-y[pairs[:,1]])
    for metric in METRICS:
        D=distances(X[landmarks],X[landmarks],metric,center,scale)
        np.fill_diagonal(D,0)
        means,grand,U,lam,_,spectrum=classical_landmark(D)
        Z=np.empty((len(rows),2),np.float32)
        for first in range(0,len(rows),args.chunk):
            last=min(len(rows),first+args.chunk)
            Z[first:last]=extend(distances(X[first:last],X[landmarks],metric,center,scale),means,grand,U,lam)
        np.save(out/f'coordinates_{metric}.npy',Z)
        np.save(out/f'landmark_distance_{metric}.npy',D.astype(np.float32))
        # Pairwise distance sampled without allocating N x N.
        A=X[pairs[:,0]].astype(np.float64);B=X[pairs[:,1]].astype(np.float64)
        if metric=='whitened':A=(A-center)/scale;B=(B-center)/scale
        if metric=='manhattan':v=np.abs(A-B).sum(axis=1)
        elif metric=='cosine':v=1-(A*B).sum(axis=1)/(np.linalg.norm(A,axis=1)*np.linalg.norm(B,axis=1)+1e-12)
        elif metric=='complex_log':v=.5*np.log1p(np.square(A-B).sum(axis=1))
        else:v=np.linalg.norm(A-B,axis=1)
        rho=spearmanr(math_dist,v).statistic
        results[metric]={'answer_distance_rho':float(rho) if np.isfinite(rho) else None,
                         'sampled_pairs':len(pairs),**spectrum}
    # Independent geometry / exact-answer decoder diagnostic; no correction authority.
    # Split by correct ANSWER values so test results do not reuse memorized answers.
    from sklearn.decomposition import PCA
    from sklearn.linear_model import Ridge
    unique=np.unique(y.astype(np.int64));rng.shuffle(unique)
    held=set(unique[:max(1,len(unique)//5)].tolist())
    test=np.array([int(r['answer']) in held for r in rows],bool)
    train=~test
    decode={'train':int(train.sum()),'test':int(test.sum()),'disjoint_answer_values':True}
    if train.sum()>=20 and test.sum()>0:
        # Fit PCA ONLY on training activations; projected position relative to mean.
        pcs=min(args.decode_rank,X.shape[1],int(train.sum())-1)
        proj=PCA(n_components=pcs,svd_solver='randomized',random_state=args.seed)
        Ztr=proj.fit_transform(X[train]); Zte=proj.transform(X[test])
        # Raw exact integer regression: no hints from answers in test.
        reg=Ridge(alpha=10.).fit(Ztr,y[train]);pred=np.rint(reg.predict(Zte))
        decode.update({'pca_rank':pcs,'exact_accuracy':float(np.mean(pred==y[test])),
                       'mae':float(np.mean(np.abs(pred-y[test]))),
                       'note':'Diagnostic only; regression/rounding is not a certified exact correction rule.'})
    (out/'decode_report.json').write_text(json.dumps(decode,indent=2))
    return {'metrics':results,'decoder':decode}

def run(args):
    out=Path(args.output)
    out.mkdir(parents=True,exist_ok=True)
    selected=list(POPS) if args.population=='all' else [args.population]
    model=tokenizer=None
    if args.mode=='model':
        import torch
        from transformers import AutoTokenizer, AutoModelForCausalLM
        tokenizer=AutoTokenizer.from_pretrained(args.model,trust_remote_code=False)
        kw={'trust_remote_code':False,'dtype':torch.float16 if args.device.startswith('cuda') else torch.float32}
        model=AutoModelForCausalLM.from_pretrained(args.model,**kw)
        if args.adapter:
            from peft import PeftModel
            if not (Path(args.adapter)/'adapter_config.json').is_file():raise ValueError('adapter must be a PEFT folder')
            model=PeftModel.from_pretrained(model,args.adapter)
        model.to(args.device).eval()
    report={'schema':'nanojev.five-centroids.v1','mode':args.mode,'model':args.model if args.mode=='model' else None,
            'samples_per_population':args.samples,'seed':args.seed,'populations':{}}
    for name in selected:
        target=out/name
        if target.exists() and any(target.iterdir()):
            if not args.resume:raise ValueError(f'Existing population: {target}; supply --resume')
            # Existing capture may be reused even after a failed geometry analysis.
            if (target/'activations.npy').exists() and (target/'probes.jsonl').exists():
                rows=[json.loads(line) for line in (target/'probes.jsonl').read_text().splitlines()]
                X=np.load(target/'activations.npy')
            else:raise ValueError(f'Incomplete existing population: {target}; use a fresh output')
        else:
            target.mkdir(parents=True,exist_ok=True)
            rows=make_rows(name,args.samples,args.seed,args.operand_limit)
            X=capture(rows,args,model,tokenizer)
            np.save(target/'activations.npy',X)
            (target/'probes.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf8')
        diagnostic=geometry(X,rows,target,args)
        report['populations'][name]={'points':len(rows),**diagnostic}
        (target/'report.json').write_text(json.dumps(report['populations'][name],indent=2))
        (out/'report.json').write_text(json.dumps(report,indent=2))
        print(f'{name}: {len(rows)} points; decode_exact={diagnostic["decoder"].get("exact_accuracy")}',flush=True)
    return report

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--mode',choices=('model','synthetic'),default='synthetic')
    ap.add_argument('--population',choices=('all',)+POPS,default='all')
    ap.add_argument('--samples',type=int,default=9000)
    ap.add_argument('--model',default='Qwen/Qwen3-0.6B')
    ap.add_argument('--adapter');ap.add_argument('--device',default='cuda');ap.add_argument('--layer',type=int,default=-1)
    ap.add_argument('--max-length',type=int,default=256);ap.add_argument('--operand-limit',type=int,default=1024)
    ap.add_argument('--landmarks',type=int,default=256);ap.add_argument('--chunk',type=int,default=256)
    ap.add_argument('--pairs',type=int,default=20000);ap.add_argument('--decode-rank',type=int,default=32)
    ap.add_argument('--seed',type=int,default=47);ap.add_argument('--resume',action='store_true')
    ap.add_argument('--output',default='runtime/reports/qwen-five-centroids')
    args=ap.parse_args()
    if args.samples<20 or args.operand_limit<16 or args.landmarks<4:ap.error('invalid sample/limit/landmark counts')
    run(args)
if __name__=='__main__':main()
