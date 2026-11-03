#!/usr/bin/env python3
"""Streaming, local-input-space C2 direct numerical head experiment.

Qwen is frozen; train a readout with optional low-rank residual on its final
prompt-token activations. No HybridLink, persisted activation bank, or backbone
LoRA update. Original five-centroid collector remains unchanged.
"""
from __future__ import annotations
import argparse, copy, csv, hashlib, json, math, random
from pathlib import Path
import numpy as np

DEFAULT_CENTERS = ((3, 4, 20, 10), (3, 4, 32, 16), (4, 5, 30, 20), (5, 6, 35, 25))

def answer(p, q, a, b):
    return int(p)*int(a)-int(q)*int(b)

def prompt(p, q, a, b):
    return f'PROBLEM: ({p}*{a})-({q}*{b})'

def parse_centers(value):
    centers=[]
    for item in value.split(';'):
        if not item.strip():continue
        parts=tuple(int(x.strip()) for x in item.split(','))
        if len(parts)!=4 or any(x<0 for x in parts):raise ValueError('centers must be p,q,a,b;... with nonnegative parameters')
        if parts[0]==0 or parts[1]==0:raise ValueError('coefficients must be positive')
        centers.append(parts)
    if not centers:raise ValueError('at least one center is required')
    return tuple(centers)

def local_points(center, radius, *, vary_coefficients=False, coefficient_radius=1):
    p,q,a,b=center
    ps=range(max(1,p-coefficient_radius),p+coefficient_radius+1) if vary_coefficients else (p,)
    qs=range(max(1,q-coefficient_radius),q+coefficient_radius+1) if vary_coefficients else (q,)
    return [(pp,qq,aa,bb) for pp in ps for qq in qs
            for aa in range(max(0,a-radius),a+radius+1)
            for bb in range(max(0,b-radius),b+radius+1)]

def deck(centers, radius, seed, *, vary_coefficients=False, coefficient_radius=1):
    # Unique parameter tuples ensure consistent labels even in overlapping neighborhoods.
    pts=set()
    for center in centers:
        pts.update(local_points(center,radius,vary_coefficients=vary_coefficients,coefficient_radius=coefficient_radius))
    pts=sorted(pts)
    random.Random(seed).shuffle(pts)
    return pts

def split_deck(points, seed, holdout_fraction=.15):
    """Deterministic disjoint partition; validation points stay excluded from gradients."""
    if len(points)<16:raise ValueError('neighborhood too small')
    pts=list(points);random.Random(seed).shuffle(pts)
    n=max(1,round(len(pts)*holdout_fraction))
    return pts[n:],pts[:n]

def deterministic_test_deck(centers, radius, seed, *, vary_coefficients=False, coefficient_radius=1):
    """Fixed disjoint neighboring input tuples, distinct from the local train region.

    Evaluate a ring outside the training neighborhood, plus its held-out interior.
    """
    outer=deck(centers,radius+2,seed+1009,vary_coefficients=vary_coefficients,coefficient_radius=coefficient_radius)
    inner=set(deck(centers,radius,seed,vary_coefficients=vary_coefficients,coefficient_radius=coefficient_radius))
    return [t for t in outer if t not in inner]

def batched(seq,n):
    for j in range(0,len(seq),n):yield seq[j:j+n]

def evaluate(torch,model,tokenizer,head,pts,device,batch_size,scale,max_length):
    if not pts:return {'n':0,'mae':None}
    errors=[]; truth=[]; estimated=[]
    model.eval();head.eval()
    with torch.inference_mode():
        for chunk in batched(pts,batch_size):
            z=features(torch,model,tokenizer,chunk,device,max_length)
            pred=head(z).flatten().float()*scale
            y=torch.tensor([answer(*t) for t in chunk],dtype=torch.float32,device=device)
            errors.extend(torch.abs(pred-y).cpu().tolist());truth.extend(y.cpu().tolist());estimated.extend(pred.cpu().tolist())
    e=np.asarray(errors)
    return {'n':len(errors),'mae':float(np.mean(e)),'median_ae':float(np.median(e)),
            'p90_ae':float(np.percentile(e,90)), 'within_1':float(np.mean(e<=1)),
            'within_5':float(np.mean(e<=5)), 'within_10':float(np.mean(e<=10)),
            'truth_min':float(min(truth)),'truth_max':float(max(truth)),
            'prediction_min':float(min(estimated)),'prediction_max':float(max(estimated))}

def features(torch,model,tokenizer,pts,device,max_length):
    # Right padding with explicit last non-pad gather; disable position shift artifacts.
    prompts=[prompt(*t) for t in pts]
    tok=tokenizer(prompts,return_tensors='pt',padding=True,truncation=True,max_length=max_length)
    ids=tok['input_ids'].to(device);mask=tok['attention_mask'].to(device)
    with torch.no_grad():
        out=model(input_ids=ids,attention_mask=mask,output_hidden_states=True,return_dict=True)
        # Works for right and left padding: find final nonpad token position.
        last=(torch.arange(ids.shape[1],device=device)[None,:]*mask).amax(dim=1)
        h=out.hidden_states[-1][torch.arange(ids.shape[0],device=device),last]
    return h.float()

def collect_features(torch,model,tokenizer,pts,device,batch_size,max_length):
    """Transient CPU buffer for deterministic comparison; never persisted."""
    arrays=[]
    model.eval()
    for block in batched(pts,batch_size):
        arrays.append(features(torch,model,tokenizer,block,device,max_length).cpu().numpy())
    return np.concatenate(arrays,axis=0)


def evaluate_features(torch,head,X,pts,device,batch_size,scale):
    """Score the frozen head on precisely the same captured vectors as local math."""
    predicted=[]
    head.eval()
    with torch.inference_mode():
        for block in batched(X,batch_size):
            inp=torch.as_tensor(block,device=device,dtype=torch.float32)
            predicted.extend((head(inp).flatten().float()*scale).cpu().tolist())
    truth=np.asarray([answer(*t) for t in pts],dtype=np.float64)
    from tools.nanojev_qwen_c2_local_math import stats
    return np.asarray(predicted,dtype=np.float64),stats(truth,predicted)


def compare_stage(torch,model,tokenizer,head,train,val,outer,args,scale,output,radius):
    from tools.nanojev_qwen_c2_local_math import compare,stats
    # Train-only geometry, fit and hyperparameter selection; held-out answers
    # are used only to score the completed methods.
    Xtrain=collect_features(torch,model,tokenizer,train,args.device,args.batch_size,args.max_length)
    Xval=collect_features(torch,model,tokenizer,val,args.device,args.batch_size,args.max_length)
    Xouter=collect_features(torch,model,tokenizer,outer,args.device,args.batch_size,args.max_length) if outer else None
    ytrain=np.asarray([answer(*t) for t in train],np.float64)
    yval=np.asarray([answer(*t) for t in val],np.float64)
    cases={'validation':(Xval,yval)}
    if outer:cases['outer']=(Xouter,np.asarray([answer(*t) for t in outer],np.float64))
    results=compare(Xtrain,ytrain,cases,rank=args.compare_rank,seed=args.seed+radius,
                    ks=tuple(int(k) for k in args.compare_ks.split(',')))
    out=output/'comparisons'/f'radius_{radius}'
    out.mkdir(parents=True,exist_ok=True)
    summary={'radius':radius,'training_count':len(train),'train_unique':len(set(train)),
             'validation_count':len(val),'outer_count':len(outer),
             'geometry':'training-only randomized PCA; train-only inner model selection',
             'local':{'config':results['config'],'effective_rank':results['effective_rank']},'sets':{}}
    for name,(x,y) in cases.items():
        pts=val if name=='validation' else outer
        raw,rawstats=evaluate_features(torch,head,x,pts,args.device,args.batch_size,scale)
        local=np.asarray(results['sets'][name]['local_predictions']);constant=np.asarray(results['sets'][name]['baseline_predictions'])
        summary['sets'][name]={'neural':rawstats,'local':results['sets'][name]['local'],
                               'constant':results['sets'][name]['constant']}
        with (out/f'{name}_predictions.csv').open('w',newline='',encoding='utf-8') as f:
            w=csv.writer(f);w.writerow(('p','q','a','b','true_answer','neural_prediction','local_prediction','constant_prediction',
                                        'neural_abs_error','local_abs_error'))
            for params,t,r,l,c in zip(pts,y,raw,local,constant):
                w.writerow((*params,float(t),float(r),float(l),float(c),abs(r-t),abs(l-t)))
    (out/'report.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    return summary


def build_head(torch,hidden,rank,residual):
    from torch import nn
    class Head(nn.Module):
        def __init__(self):
            super().__init__()
            self.readout=nn.Linear(hidden,1)
            self.down=nn.Linear(hidden,rank,bias=False) if residual else None
            self.up=nn.Linear(rank,hidden,bias=False) if residual else None
            if residual:
                nn.init.normal_(self.down.weight,std=.01)
                nn.init.zeros_(self.up.weight)
        def forward(self,h):
            if self.down is not None:h=h+self.up(self.down(h))
            return self.readout(h)
    return Head()

def run(args):
    import torch
    from transformers import AutoTokenizer,AutoModelForCausalLM
    if args.device.startswith('cuda') and not torch.cuda.is_available():raise RuntimeError('CUDA requested but unavailable')
    torch.manual_seed(args.seed);np.random.seed(args.seed);random.seed(args.seed)
    centers=parse_centers(args.centers)
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    tokenizer=AutoTokenizer.from_pretrained(args.model,trust_remote_code=False)
    if tokenizer.pad_token_id is None: tokenizer.pad_token=tokenizer.eos_token
    tokenizer.padding_side='right'
    dtype=torch.float16 if args.device.startswith('cuda') else torch.float32
    model=AutoModelForCausalLM.from_pretrained(args.model,torch_dtype=dtype,trust_remote_code=False).to(args.device).eval()
    if args.adapter:
        from peft import PeftModel
        if not (Path(args.adapter)/'adapter_config.json').is_file():raise ValueError('adapter must be PEFT folder')
        model=PeftModel.from_pretrained(model,args.adapter).eval()
    for p in model.parameters():p.requires_grad_(False)
    hidden=int(model.config.hidden_size)
    head=build_head(torch,hidden,args.rank,args.residual).to(args.device)
    opt=torch.optim.AdamW(head.parameters(),lr=args.lr,weight_decay=args.weight_decay)
    radii=[int(x) for x in args.radii.split(',')]
    if not radii or min(radii)<1 or radii!=sorted(set(radii)):raise ValueError('radii must be increasing unique positive integers')
    # Fixed output normalization across all radii; never use holdout answers for scaling.
    max_p=max(c[0] for c in centers)+(args.coefficient_radius if args.vary_coefficients else 0)
    max_q=max(c[1] for c in centers)+(args.coefficient_radius if args.vary_coefficients else 0)
    max_a=max(c[2] for c in centers)+max(radii)
    max_b=max(c[3] for c in centers)+max(radii)
    scale=float(max(1,max_p*max_a+max_q*max_b))
    report={'schema':'nanojev.c2-local-direct.v1','model':args.model,'adapter':args.adapter,
            'centers':centers,'radii':radii,'scale':scale,'residual':args.residual,
            'rank':args.rank,'seed':args.seed,'compare_local':args.compare_local,'stages':[]}
    # Stage-specific validation sets. Training split never includes stage heldouts.
    used_train=set();used_val=set(); original_validation={}
    for radius in radii:
        pts=deck(centers,radius,args.seed+radius,vary_coefficients=args.vary_coefficients,coefficient_radius=args.coefficient_radius)
        # Permanent, radius-independent hash assignment: an example is never
        # allowed in training after appearing in validation at an earlier radius.
        def in_validation(x):
            return int(hashlib.sha256(f'{args.seed}:{x}'.encode()).hexdigest()[:8],16)%10==0
        train=[x for x in pts if not in_validation(x)]
        val=[x for x in pts if in_validation(x)]
        used_train.update(train);used_val.update(val)
        outer=deterministic_test_deck(centers,radius,args.seed,vary_coefficients=args.vary_coefficients,coefficient_radius=args.coefficient_radius)
        outer=[x for x in outer if x not in used_train and x not in used_val]
        if not train or not val:raise ValueError('not enough examples for training and validation')
        if radius==radii[0]:
            original_validation={'radius':radius,'pts':list(val)}
        stage={'radius':radius,'train_unique':len(train),'validation_unique':len(val),'outer_unique':len(outer),'checkpoints':[]}
        print(f'R={radius}: train={len(train)} val={len(val)} outer={len(outer)}',flush=True)
        best=float('inf');best_state=None;best_opt=None;stall=0
        for step in range(1,args.steps_per_radius+1):
            # deterministic stream: batches sampled with replacement from current radius,
            # plus replay from earlier acquired train-only points.
            batch=[]
            old=list(used_train-set(train)); rng=random.Random(args.seed+radius*1000003+step)
            for _ in range(args.batch_size):
                if old and rng.random()<args.replay_fraction:batch.append(old[rng.randrange(len(old))])
                else:batch.append(train[rng.randrange(len(train))])
            h=features(torch,model,tokenizer,batch,args.device,args.max_length)
            pred=head(h).flatten().float()
            labels=torch.tensor([answer(*t)/scale for t in batch],dtype=torch.float32,device=args.device)
            loss=torch.nn.functional.smooth_l1_loss(pred,labels,beta=args.huber_beta)
            opt.zero_grad(set_to_none=True);loss.backward();torch.nn.utils.clip_grad_norm_(head.parameters(),args.grad_clip);opt.step()
            if step%args.eval_every==0 or step==args.steps_per_radius:
                stats=evaluate(torch,model,tokenizer,head,val,args.device,args.batch_size,scale,args.max_length)
                row={'step':step,'loss':float(loss.detach().cpu()),'validation':stats}
                stage['checkpoints'].append(row)
                if stats['mae']<best-1e-8:
                    best=stats['mae'];best_state=copy.deepcopy(head.state_dict());best_opt=copy.deepcopy(opt.state_dict());stall=0
                else:stall+=1
                print(f'  step={step} val_MAE={stats["mae"]:.5g} best={best:.5g}',flush=True)
                if stall>=args.patience:break
        head.load_state_dict(best_state)
        opt.load_state_dict(best_opt)
        stage['best_validation_mae']=best
        stage['outer']=evaluate(torch,model,tokenizer,head,outer,args.device,args.batch_size,scale,args.max_length)
        stage['retention_initial']=evaluate(torch,model,tokenizer,head,original_validation['pts'],args.device,args.batch_size,scale,args.max_length)
        if args.compare_local:
            stage['local_comparison']=compare_stage(torch,model,tokenizer,head,train,val,outer,args,scale,out,radius)
            measured=stage['local_comparison']['sets']['validation']
            print(f"  matched validation: neural_MAE={measured['neural']['mae']:.5g} local_MAE={measured['local']['mae']:.5g} constant_MAE={measured['constant']['mae']:.5g}",flush=True)
        report['stages'].append(stage)
        torch.save({'head':head.state_dict(),'optimizer':opt.state_dict(),'radius':radius,'scale':scale,
                    'centers':centers,'model':args.model,'seed':args.seed},out/f'head_radius_{radius}.pt')
        (out/'report.json').write_text(json.dumps(report,indent=2))
        print(f'R={radius}: best_val_MAE={best:.5g}, outer_MAE={stage["outer"]["mae"]}',flush=True)
    return report

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--model',default='Qwen/Qwen3-0.6B');ap.add_argument('--adapter')
    ap.add_argument('--device',default='cuda');ap.add_argument('--centers',default='3,4,20,10;3,4,32,16;4,5,30,20;5,6,35,25')
    ap.add_argument('--radii',default='4,8,16,32');ap.add_argument('--vary-coefficients',action='store_true');ap.add_argument('--coefficient-radius',type=int,default=1)
    ap.add_argument('--rank',type=int,default=8);ap.add_argument('--residual',action='store_true')
    ap.add_argument('--batch-size',type=int,default=16);ap.add_argument('--steps-per-radius',type=int,default=500)
    ap.add_argument('--eval-every',type=int,default=100);ap.add_argument('--patience',type=int,default=5)
    ap.add_argument('--lr',type=float,default=1e-3);ap.add_argument('--weight-decay',type=float,default=1e-4)
    ap.add_argument('--huber-beta',type=float,default=.05);ap.add_argument('--grad-clip',type=float,default=1.)
    ap.add_argument('--replay-fraction',type=float,default=.25);ap.add_argument('--max-length',type=int,default=128)
    ap.add_argument('--seed',type=int,default=47);ap.add_argument('--output',default='runtime/reports/qwen-c2-local-direct')
    ap.add_argument('--compare-local',action='store_true',help='Compare frozen neural head vs train-only local interpolation on identical C2 decks')
    ap.add_argument('--compare-rank',type=int,default=32,help='Train-only PCA dimension for mathematical local baseline')
    ap.add_argument('--compare-ks',default='8,16,32,64',help='Neighborhood sizes selected by train-only inner validation')
    args=ap.parse_args()
    if args.batch_size<1 or args.steps_per_radius<1 or args.eval_every<1 or args.rank<1 or not 0<=args.replay_fraction<=1:ap.error('invalid training parameters')
    run(args)
if __name__=='__main__':main()
