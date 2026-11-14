#!/usr/bin/env python3
"""C2 multi-architecture compressive numerical encoding with matched local-math teacher.

Default trains every registered architecture on the same data and teacher,
with independent seeds, weights, optimizers, checkpoints, and matched evaluation.
Exact answers are authoritative. Does not reconstruct activations or use HybridLink.
"""
from __future__ import annotations
import argparse, copy, csv, hashlib, json, random
from pathlib import Path
import numpy as np
from tools.nanojev_qwen_c2_local_math import (fit_geometry, transform_geometry,
    local_predict, select_method, stats)
from tools.nanojev_qwen_c2_local_train import (answer, prompt, features, batched, parse_centers)


def signed_deck(centers, radius, seed):
    points=set()
    for p,q,a,b in centers:
        for x in range(a-radius,a+radius+1):
            for y in range(b-radius,b+radius+1):
                points.add((p,q,x,y))
    out=sorted(points)
    random.Random(seed).shuffle(out)
    return out


def frontier_deck(centers, radius, seed, cap=8192, replay=(), replay_fraction=.25,
                  vary_coefficients=False, coefficient_radius=1):
    """Bounded, repeatable input-space frontier; never enumerate huge R512 grids.

    A fixed portion replays earlier-radius training points. New points are
    drawn within the current input-radius, including boundary shells and
    cancellation bands. Validation is assigned by immutable tuple hash.
    """
    if cap < 32 or not 0 <= replay_fraction < 1:
        raise ValueError('cap >=32 and 0<=replay_fraction<1 required')
    rng=random.Random(seed + radius*983)
    replay=sorted(set(replay))
    keep=min(len(replay),int(cap*replay_fraction))
    old=rng.sample(replay,keep) if keep<len(replay) else replay[:]
    result=set(old)
    # At R32 a complete grid is feasible and reproduces the established test.
    if not vary_coefficients and len(centers)*(2*radius+1)**2 <=cap:
        return signed_deck(centers,radius,seed)
    attempts=0
    while len(result)<cap and attempts<cap*100:
        attempts+=1
        p,q,a,b=rng.choice(centers)
        if vary_coefficients:
            p=max(1,p+rng.randint(-coefficient_radius,coefficient_radius))
            q=max(1,q+rng.randint(-coefficient_radius,coefficient_radius))
        mode=attempts%5
        if mode==0:  # points concentrated near radius boundary
            da=rng.choice((-1,1))*rng.randint(max(0,3*radius//4),radius)
            db=rng.randint(-radius,radius)
        elif mode==1:
            da=rng.randint(-radius,radius)
            db=rng.choice((-1,1))*rng.randint(max(0,3*radius//4),radius)
        elif mode==2:  # interior / small-offset numerical structure
            da=rng.randint(-max(1,radius//4),max(1,radius//4))
            db=rng.randint(-max(1,radius//4),max(1,radius//4))
        elif mode==3:  # near cancellation, mathematically defined
            da=rng.randint(-radius,radius)
            target=round((p*(a+da)-q*b)/q)
            db=max(-radius,min(radius,target+rng.randint(-3,3)))
        else:
            da=rng.randint(-radius,radius)
            db=rng.randint(-radius,radius)
        result.add((p,q,a+da,b+db))
    if len(result)<cap:
        raise RuntimeError('unable to fill requested frontier sample')
    output=sorted(result)
    random.Random(seed+radius).shuffle(output)
    return output


def retention_summary(torch, net, decks, scale, args):
    """Report numeric retention against held-out earlier-region examples."""
    result={}
    for r,(x,y,accepted) in decks.items():
        pred,_=eval_heads(torch,net,x,y,0.,scale,args.device,args.eval_batch)
        current=stats(y,pred)['mae']
        result[str(r)]={'mae':current,'accepted_mae':accepted,
                        'retention_ratio':float(accepted/max(current,1e-12)),
                        'retained_95pct':bool(current<=accepted/.95)}
    return result


def outer_shell(centers, radius, seed, count=256, vary_coefficients=False,
                coefficient_radius=1):
    """Select unseen radius+1/+2 boundary points without full-grid enumeration."""
    rng=random.Random(seed+radius*1789)
    points=set()
    attempts=0
    while len(points)<count and attempts<count*100:
        attempts+=1
        p,q,a,b=rng.choice(centers)
        if vary_coefficients:
            p=max(1,p+rng.randint(-coefficient_radius,coefficient_radius))
            q=max(1,q+rng.randint(-coefficient_radius,coefficient_radius))
        delta=rng.choice((radius+1,radius+2))*rng.choice((-1,1))
        other=rng.randint(-radius-2,radius+2)
        da,db=(delta,other) if rng.randrange(2)==0 else (other,delta)
        point=(p,q,a+da,b+db)
        if not any(abs(point[2]-ca)<=radius and abs(point[3]-cb)<=radius and
                   (not vary_coefficients or
                    (abs(point[0]-cp)<=coefficient_radius and abs(point[1]-cq)<=coefficient_radius))
                   for cp,cq,ca,cb in centers):
            points.add(point)
    if len(points)<count:
        raise ValueError('could not generate requested outside-radius test deck')
    return sorted(points)


def holdout_key(t,seed):
    return int(hashlib.sha256(f'{seed}:{t}'.encode()).hexdigest()[:12],16)%10==0


def split(pts,seed):
    return [p for p in pts if not holdout_key(p,seed)], [p for p in pts if holdout_key(p,seed)]


def collect(torch, model, tok, tuples, args):
    chunks=[]
    for group in batched(tuples,args.capture_batch):
        chunks.append(features(torch,model,tok,group,args.device,args.max_length).cpu().numpy())
    return np.concatenate(chunks).astype('float32') if chunks else np.empty((0,model.config.hidden_size),dtype='float32')


def teacher_fit(x,y,rank,seed,ks):
    geo=fit_geometry(x,rank=rank,seed=seed)
    config=select_method(geo['train'],y,seed=seed,ks=ks)
    return geo,config


def teacher_predict(geo,config,x,y):
    z=transform_geometry(geo,x)
    pred=local_predict(geo['train'],y,z,k=config['k'],penalty=config['penalty'],method=config['method'])
    return pred


def crossfit_targets(x,y,rank,seed,ks,folds=4):
    """Out-of-fold teacher predictions. No point can retrieve its own label."""
    rng=np.random.default_rng(seed)
    order=rng.permutation(len(y))
    out=np.empty(len(y),np.float64)
    for i,idx in enumerate(np.array_split(order,folds)):
        train=np.setdiff1d(np.arange(len(y)),idx,assume_unique=True)
        geo,cfg=teacher_fit(x[train],y[train],rank,seed+i,ks)
        out[idx]=teacher_predict(geo,cfg,x[idx],y[train])
    return out


# Registered architectures are defined by hidden-layer widths. All are compressed
# down to a final scalar output; the penultimate hidden layer is the z' probe.
# Keep the original architecture as "reference" for comparable continuation.
VARIANTS = {
    'reference': (512, 256, 64, 16),
    'narrow': (256, 128, 32, 8),
    'wide': (1024, 512, 128, 32),
    'deep': (768, 512, 256, 128, 32, 8),
    'balanced': (512, 256, 128, 64, 32),
}


def resolve_variants(spec):
    names = tuple(VARIANTS) if spec.strip().lower() == 'all' else tuple(v.strip() for v in spec.split(','))
    if not names or any(not v or v not in VARIANTS for v in names) or len(set(names)) != len(names):
        raise ValueError('variants must be all or distinct comma-separated names from: '+','.join(VARIANTS))
    return names


def make_network(torch, input_width, hidden=(512, 256, 64, 16)):
    from torch import nn
    if len(hidden) < 2 or any(d < 1 for d in hidden):
        raise ValueError('hidden must contain at least two positive widths')
    class Encoder(nn.Module):
        def __init__(self):
            super().__init__()
            self.norm=nn.LayerNorm(input_width)
            layers=[]; width=input_width
            for next_width in hidden[:-1]:
                layers.extend((nn.Linear(width,next_width),nn.GELU()))
                width=next_width
            self.enc=nn.Sequential(*layers)
            self.mid=nn.Sequential(nn.Linear(width,hidden[-1]),nn.GELU())
            self.corrected=nn.Linear(hidden[-1],1)
            self.uncorrected=nn.Linear(hidden[-2],1)
        def forward(self,x):
            zp=self.enc(self.norm(x))
            zz=self.mid(zp)
            return self.corrected(zz).flatten(),self.uncorrected(zp).flatten()
    return Encoder()


def eval_heads(torch,network,x,y,mean,scale,device,batch):
    network.eval(); p=[]; q=[]
    with torch.inference_mode():
        for chunk in batched(x,batch):
            a,b=network(torch.as_tensor(chunk,dtype=torch.float32,device=device))
            p.extend((a*scale+mean).cpu().tolist())
            q.extend((b*scale+mean).cpu().tolist())
    return np.asarray(p,dtype=np.float64),np.asarray(q,dtype=np.float64)


def run(args):
    import torch
    from transformers import AutoTokenizer,AutoModelForCausalLM
    if args.device.startswith('cuda') and not torch.cuda.is_available():
        raise RuntimeError('CUDA requested but unavailable')
    torch.manual_seed(args.seed);np.random.seed(args.seed);random.seed(args.seed)
    centers=parse_centers(args.centers)
    radii=[int(x) for x in args.radii.split(',')]
    if not radii or min(radii)<1 or radii!=sorted(set(radii)):
        raise ValueError('radii must be positive, unique, increasing')
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    tok=AutoTokenizer.from_pretrained(args.model,trust_remote_code=False)
    if tok.pad_token_id is None:tok.pad_token=tok.eos_token
    tok.padding_side='right'
    model=AutoModelForCausalLM.from_pretrained(args.model,dtype=torch.float16 if args.device.startswith('cuda') else torch.float32,trust_remote_code=False).to(args.device).eval()
    if args.adapter:
        from peft import PeftModel
        model=PeftModel.from_pretrained(model,args.adapter).eval()
    for p in model.parameters():p.requires_grad_(False)
    ks=tuple(int(k) for k in args.teacher_ks.split(','))
    # One fixed, label-free output scale across all stages; checkpoint semantics do not drift.
    global_radius=max(radii)
    global_scale=float(max([1]+[abs(answer(p,q,a+da,b+db)) for p,q,a,b in centers for da in (-global_radius,global_radius) for db in (-global_radius,global_radius)]))
    variant_names=resolve_variants(args.variants)
    print('Architectures (all selected models train by default):', ', '.join(variant_names), flush=True)
    suite={}
    for name in variant_names:
        # Reset RNG per architecture; each gets deterministic, independent initialization.
        torch.manual_seed(args.seed + list(VARIANTS).index(name)*100003)
        net=make_network(torch,int(model.config.hidden_size),VARIANTS[name]).to(args.device)
        opt=torch.optim.AdamW(net.parameters(),lr=args.lr,weight_decay=args.weight_decay)
        suite[name]=(net,opt)
    stages=[]
    prior_train=[]
    prior_evals={name:{} for name in variant_names}
    # Holdout assignment is invariant to radius. Previous training vectors may be replayed.
    for radius in radii:
        allpts=frontier_deck(centers,radius,args.seed,cap=args.max_stage_points,
                replay=prior_train,replay_fraction=args.replay_fraction,
                vary_coefficients=args.vary_coefficients,
                coefficient_radius=args.coefficient_radius)
        tr,va=split(allpts,args.seed)
        # Never replay a held-out tuple into the student's gradient data.
        prior_train=tr
        if len(tr)<24 or len(va)<4: raise ValueError('insufficient examples for matched teacher evaluation')
        print(f'R={radius}: capture train={len(tr)} val={len(va)} (signed a,b grid)',flush=True)
        xt=collect(torch,model,tok,tr,args);xv=collect(torch,model,tok,va,args)
        outer=outer_shell(centers,radius,args.seed+101,args.outer_points,
             vary_coefficients=args.vary_coefficients,
             coefficient_radius=args.coefficient_radius)
        xo=collect(torch,model,tok,outer,args)
        yt=np.asarray([answer(*t) for t in tr],np.float64)
        yv=np.asarray([answer(*t) for t in va],np.float64)
        mean=0.0;scale=global_scale
        print(f'R={radius}: fitting deterministic teacher (train-only, rank={args.teacher_rank})',flush=True)
        geo,config=teacher_fit(xt,yt,args.teacher_rank,args.seed+radius,ks)
        teacher_val=teacher_predict(geo,config,xv,yt)
        teacher_outer=teacher_predict(geo,config,xo,yt)
        yo=np.asarray([answer(*t) for t in outer],np.float64)
        # OOF teacher targets prevent training-set self-label lookup.
        teacher_tr=crossfit_targets(xt,yt,args.teacher_rank,args.seed+radius,ks,args.teacher_folds)
        teacher_mae=stats(yv,teacher_val)['mae']
        constant_mae=stats(yv,np.full_like(yv,np.median(yt)))['mae']
        print(f'R={radius}: deterministic_MAE={teacher_mae:.5g} constant_MAE={constant_mae:.5g} teacher={config["method"]}/k{config["k"]}',flush=True)
        stage_models={}
        # Fit teacher and capture Qwen features ONCE per radius; all model
        # variants see identical examples, teacher predictions, and holdouts.
        for name in variant_names:
            net,opt=suite[name]
            print(f'R={radius}: TRAIN variant={name} layers={VARIANTS[name]}',flush=True)
            # Cache activation vectors in CPU RAM, minibatches migrate to GPU.
            # GPU Qwen forward is done once for each distinct input per radius.
            best=float('inf');best_state=None;best_opt=None;stalled=0;checks=[]
            rng=np.random.default_rng(args.seed+radius*1009+list(VARIANTS).index(name)*100003)
            for step in range(1,args.steps_per_radius+1):
                ind=rng.integers(0,len(tr),size=args.batch_size)
                x=torch.as_tensor(xt[ind].copy(),device=args.device)
                y=torch.as_tensor(((yt[ind]-mean)/scale).astype('float32'),device=args.device)
                t=torch.as_tensor(((teacher_tr[ind]-mean)/scale).astype('float32'),device=args.device)
                net.train();pred,baseline=net(x)
                # True answer is authoritative; teacher is auxiliary guidance.
                exact=torch.nn.functional.smooth_l1_loss(pred,y,beta=args.huber_beta)
                imitation=torch.nn.functional.smooth_l1_loss(pred,t,beta=args.huber_beta)
                auxiliary=torch.nn.functional.smooth_l1_loss(baseline,y,beta=args.huber_beta)
                loss=exact+args.teacher_weight*imitation+args.aux_weight*auxiliary
                opt.zero_grad(set_to_none=True);loss.backward()
                torch.nn.utils.clip_grad_norm_(net.parameters(),args.grad_clip);opt.step()
                if step%args.eval_every==0 or step==args.steps_per_radius:
                    pred_val,base_val=eval_heads(torch,net,xv,yv,mean,scale,args.device,args.eval_batch)
                    pm=stats(yv,pred_val)['mae'];bm=stats(yv,base_val)['mae']
                    if pm<best-1e-8:
                        best=pm;best_state=copy.deepcopy(net.state_dict());best_opt=copy.deepcopy(opt.state_dict());stalled=0
                    else:stalled+=1
                    checks.append({'step':step,'corrected_mae':pm,'uncorrected_mae':bm,'teacher_mae':teacher_mae,'best_corrected_mae':best})
                    print(f'  {name} step={step} zprime_MAE={bm:.5g} zdoubleprime_MAE={pm:.5g} local_MAE={teacher_mae:.5g} best={best:.5g}',flush=True)
                    if stalled>=args.patience:break
            net.load_state_dict(best_state);opt.load_state_dict(best_opt)
            final,raw=eval_heads(torch,net,xv,yv,mean,scale,args.device,args.eval_batch)
            opred,oraw=eval_heads(torch,net,xo,yo,mean,scale,args.device,args.eval_batch)
            path=out/f'radius_{radius}'/name;path.mkdir(parents=True,exist_ok=True)
            with (path/'validation_predictions.csv').open('w',newline='',encoding='utf-8') as file:
                writer=csv.writer(file);writer.writerow(['p','q','a','b','true','deterministic','zprime','zdoubleprime'])
                for row,y,t,r,s in zip(va,yv,teacher_val,raw,final): writer.writerow([*row,y,t,r,s])
            with (path/'outer_predictions.csv').open('w',newline='',encoding='utf-8') as file:
                writer=csv.writer(file);writer.writerow(['p','q','a','b','true','deterministic','zprime','zdoubleprime'])
                for row,y,t,r,s in zip(outer,yo,teacher_outer,oraw,opred): writer.writerow([*row,y,t,r,s])
            summary={'radius':radius,'variant':name,'hidden':list(VARIANTS[name]),'train_count':len(tr),'validation_count':len(va),'outer_count':len(outer),'teacher_config':config,
                     'best_corrected_mae':best,'validation':{'local':stats(yv,teacher_val),'zprime':stats(yv,raw),
                      'zdoubleprime':stats(yv,final),'constant':stats(yv,np.full_like(yv,np.median(yt)))},
                     'outer':{'local':stats(yo,teacher_outer),'zprime':stats(yo,oraw),
                              'zdoubleprime':stats(yo,opred),'constant':stats(yo,np.full_like(yo,np.median(yt)))},'checks':checks}
            (path/'report.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
            torch.save({'model':net.state_dict(),'optimizer':opt.state_dict(),'input_width':int(xt.shape[1]),
                        'radius':radius,'variant':name,'hidden':list(VARIANTS[name]),'centers':centers,'seed':args.seed,'normalization':{'mean':mean,'scale':scale}},path/'encoder.pt')
            summary['retention']=retention_summary(torch,net,prior_evals[name],scale,args)
            summary['retention_gate_pass']=all(v['retained_95pct'] for v in summary['retention'].values())
            (path/'report.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
            prior_evals[name][radius]=(xv,yv,summary['validation']['zdoubleprime']['mae'])
            stage_models[name]=summary
            print(f'R={radius}: variant={name} FINAL local={teacher_mae:.5g} zprime={summary["validation"]["zprime"]["mae"]:.5g} zdoubleprime={best:.5g}',flush=True)
        ranking=sorted(stage_models, key=lambda n: stage_models[n]['validation']['zdoubleprime']['mae'])
        stage={'radius':radius,'teacher_mae':teacher_mae,'constant_mae':constant_mae,
               'models':stage_models,'ranking':ranking,'winner':ranking[0],
               'train_count':len(tr),'validation_count':len(va),
               'retention_failures':[n for n in variant_names if not stage_models[n]['retention_gate_pass']]}
        stages.append(stage)
        (out/f'radius_{radius}'/'report.json').write_text(json.dumps(stage,indent=2),encoding='utf-8')
        (out/'report.json').write_text(json.dumps({'schema':'nanojev.c2-compressive-sweep.v2',
            'model':args.model,'variants':{n:list(VARIANTS[n]) for n in variant_names},
            'stages':stages},indent=2),encoding='utf-8')
        print('R=%s: RANKING %s'%(radius, ' | '.join(f'{n}={stage_models[n]["validation"]["zdoubleprime"]["mae"]:.5g}' for n in ranking)),flush=True)
    return stages


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--model',default='Qwen/Qwen3-0.6B');ap.add_argument('--adapter');ap.add_argument('--device',default='cuda')
    ap.add_argument('--centers',default='3,4,20,10');ap.add_argument('--radii',default='32,64,128,256,512')
    ap.add_argument('--batch-size',type=int,default=64);ap.add_argument('--capture-batch',type=int,default=16)
    ap.add_argument('--eval-batch',type=int,default=256);ap.add_argument('--steps-per-radius',type=int,default=9000)
    ap.add_argument('--eval-every',type=int,default=100);ap.add_argument('--patience',type=int,default=90)
    ap.add_argument('--teacher-rank',type=int,default=32);ap.add_argument('--teacher-ks',default='8,16,32,64')
    ap.add_argument('--teacher-folds',type=int,default=4);ap.add_argument('--teacher-weight',type=float,default=.25)
    ap.add_argument('--aux-weight',type=float,default=.25);ap.add_argument('--lr',type=float,default=2e-4)
    ap.add_argument('--weight-decay',type=float,default=1e-4);ap.add_argument('--grad-clip',type=float,default=1.)
    ap.add_argument('--huber-beta',type=float,default=.1);ap.add_argument('--max-length',type=int,default=128)
    ap.add_argument('--max-stage-points',type=int,default=8192,
                    help='max mathematical tuples at each frontier stage')
    ap.add_argument('--replay-fraction',type=float,default=.25)
    ap.add_argument('--outer-points',type=int,default=256)
    ap.add_argument('--vary-coefficients',action='store_true',
                    help='vary p,q around centers, in addition to a,b')
    ap.add_argument('--coefficient-radius',type=int,default=1)
    ap.add_argument('--variants',default='all',help='all (default), or comma-separated: '+','.join(VARIANTS))
    ap.add_argument('--seed',type=int,default=47);ap.add_argument('--output',default='runtime/reports/qwen-c2-compressive')
    args=ap.parse_args()
    if any((args.batch_size<1,args.capture_batch<1,args.steps_per_radius<1,args.eval_every<1,
            args.max_stage_points<32,args.outer_points<1,
            not 0<=args.replay_fraction<1,args.coefficient_radius<0,
            args.teacher_folds<2,args.teacher_rank<1,args.teacher_weight<0,args.aux_weight<0,args.patience<1)):
        ap.error('invalid training arguments')
    try: resolve_variants(args.variants)
    except ValueError as error: ap.error(str(error))
    run(args)

if __name__=='__main__':main()
