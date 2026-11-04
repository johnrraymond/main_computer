#!/usr/bin/env python3
"""C0 named-game payoff compression with independent deterministic comparison.

Each encoder trains on exact arithmetic labels only. The deterministic local
estimator independently fits training activations and reports matched validation
and outer results. It never supplies labels or gradients to the neural network.
"""
from __future__ import annotations
import argparse, copy, csv, hashlib, json, random
from pathlib import Path
import numpy as np
from tools.nanojev_qwen_c2_local_math import (fit_geometry, transform_geometry,
    local_predict, select_method, stats)
from tools.nanojev_qwen_c2_local_train import batched
from tools.nanojev_qwen_five_centroids import make_rows
from tools.nanojev_qwen_c0_balance import (make_balance_deck, save_balance_report,
    positive_ints, DEFAULT_DELTAS, DEFAULT_MAGNITUDES)



def signed_log(x):
    x=np.asarray(x,dtype=np.float64)
    return np.sign(x)*np.log1p(np.abs(x))

def inverse_signed_log(x):
    x=np.asarray(x,dtype=np.float64)
    return np.sign(x)*np.expm1(np.clip(np.abs(x),0,35.))

def c0_stage_rows(all_rows,scale,cap,seed,replay=(),fraction=.25):
    """Actual C0 centroid population: original named-game prompts and payoff labels."""
    group=[r for r in all_rows if int(r['scale'])==scale]
    rng=random.Random(seed+scale*983)
    rng.shuffle(group)
    n_old=min(len(replay),int(cap*fraction))
    old=rng.sample(list(replay),n_old) if n_old else []
    chosen={r['prompt']:r for r in old}
    for row in group:
        if len(chosen)>=cap:break
        chosen[row['prompt']]=row
    result=list(chosen.values())
    rng.shuffle(result)
    return result

def holdout_key(row,seed):
    return int(hashlib.sha256(f"{seed}:{row['prompt']}".encode()).hexdigest()[:12],16)%10==0

def split(rows,seed):
    return [r for r in rows if not holdout_key(r,seed)],[r for r in rows if holdout_key(r,seed)]

def capture_features(torch,model,tok,rows,args):
    chunks=[]
    for group in batched(rows,args.capture_batch):
        prompts=[r['prompt'] for r in group]
        encoded=tok(prompts,padding=True,truncation=True,max_length=args.max_length,return_tensors='pt')
        encoded={k:v.to(args.device) for k,v in encoded.items()}
        with torch.inference_mode():
            result=model(**encoded,output_hidden_states=True,return_dict=True)
            indices=encoded['attention_mask'].sum(dim=-1)-1
            hidden=result.hidden_states[-1]
            chunks.append(hidden[torch.arange(len(group),device=hidden.device),indices].float().cpu().numpy())
    return np.concatenate(chunks).astype('float32') if chunks else np.empty((0,int(model.config.hidden_size)),dtype='float32')

def retention_summary(torch, net, decks, scale, args):
    result={}
    for stage,(x,y,accepted) in decks.items():
        pred,_=eval_heads(torch,net,x,y,0.,scale,args.device,args.eval_batch)
        current=stats(y,pred)['mae']
        result[str(stage)]={'mae':current,'accepted_mae':accepted,
                            'retention_ratio':float(accepted/max(current,1e-12)),
                            'retained_95pct':bool(current<=accepted/.95)}
    return result

def teacher_fit(x,y,rank,seed,ks):
    geo=fit_geometry(x,rank=rank,seed=seed)
    config=select_method(geo['train'],y,seed=seed,ks=ks)
    return geo,config


def teacher_predict(geo,config,x,y):
    z=transform_geometry(geo,x)
    pred=local_predict(geo['train'],y,z,k=config['k'],penalty=config['penalty'],method=config['method'])
    return pred


def neural_loss(torch, corrected, uncorrected, exact_targets, *, beta, aux_weight):
    """Exact-answer supervision only; no deterministic-teacher input or loss."""
    exact=torch.nn.functional.smooth_l1_loss(corrected,exact_targets,beta=beta)
    auxiliary=torch.nn.functional.smooth_l1_loss(uncorrected,exact_targets,beta=beta)
    return exact+aux_weight*auxiliary


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
            p.extend(inverse_signed_log(a.cpu().numpy()).tolist())
            q.extend(inverse_signed_log(b.cpu().numpy()).tolist())
    return np.asarray(p,dtype=np.float64),np.asarray(q,dtype=np.float64)


def run(args):
    if args.zero_line_hard_train:
        from tools.nanojev_qwen_c0_zero_line import run_zero_line
        return run_zero_line(args)
    import torch
    from transformers import AutoTokenizer,AutoModelForCausalLM
    if args.device.startswith('cuda') and not torch.cuda.is_available():
        raise RuntimeError('CUDA requested but unavailable')
    torch.manual_seed(args.seed);np.random.seed(args.seed);random.seed(args.seed)
    scales=[int(x) for x in args.scales.split(',')]
    if not scales or scales!=sorted(set(scales)) or any(x not in (1,10,100,1000,10000) for x in scales):
        raise ValueError('scales must be ordered unique values from 1,10,100,1000,10000')
    if args.source_samples < 1000:raise ValueError('source_samples must be >=1000')
    # Use the original C0 generator, including its 100 nominal game names and
    # five magnitude bands. Unlike C2, prompts are natural-language payoff text.
    all_rows=make_rows('C0_games',n=args.source_samples,seed=args.seed)
    balance_rows=[]; balance_x=None
    if args.balance_diagnostic:
        balance_rows=make_balance_deck(
            deltas=positive_ints(args.balance_deltas,include_zero=True),
            magnitudes=positive_ints(args.balance_magnitudes),
            samples_per_cell=args.balance_samples_per_cell,seed=args.seed,
            excluded_prompts=(r['prompt'] for r in all_rows))
        print(f'Balance audit: {len(balance_rows)} fixed held-out prompts (exact signed power-of-two deltas)',flush=True)
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    tok=AutoTokenizer.from_pretrained(args.model,trust_remote_code=False)
    if tok.pad_token_id is None:tok.pad_token=tok.eos_token
    tok.padding_side='right'
    model=AutoModelForCausalLM.from_pretrained(args.model,dtype=torch.float16 if args.device.startswith('cuda') else torch.float32,trust_remote_code=False).to(args.device).eval()
    if args.adapter:
        from peft import PeftModel
        model=PeftModel.from_pretrained(model,args.adapter).eval()
    for p in model.parameters():p.requires_grad_(False)
    if balance_rows:
        balance_x=capture_features(torch,model,tok,balance_rows,args)
    ks=tuple(int(k) for k in args.teacher_ks.split(','))
    # Signed-log outputs avoid catastrophic loss scales across C0's five bands.
    global_scale=1.
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
    # Holdout assignment is invariant to scale. Only earlier training examples are replayed.
    for radius in scales:
        allpts=c0_stage_rows(all_rows,radius,args.max_stage_points,args.seed,
                             replay=prior_train,fraction=args.replay_fraction)
        tr,va=split(allpts,args.seed)
        # Never replay a held-out tuple into the student's gradient data.
        prior_train=tr
        if len(tr)<24 or len(va)<4: raise ValueError('insufficient examples for matched teacher evaluation')
        print(f'C0 scale={radius}: capture train={len(tr)} val={len(va)} (named-game payoffs)',flush=True)
        xt=capture_features(torch,model,tok,tr,args);xv=capture_features(torch,model,tok,va,args)
        # Outside-scale evaluation uses the next larger C0 magnitude band;
        # the model cannot train on its held-out examples.
        larger=[r for r in all_rows if int(r['scale'])>radius and holdout_key(r,args.seed)]
        outer=larger[:args.outer_points]
        if not outer: outer=[r for r in all_rows if int(r['scale'])==radius and r['prompt'] not in {q['prompt'] for q in allpts}][:args.outer_points]
        if not outer:outer=va[:min(len(va),args.outer_points)]  # no higher scale exists; same-band heldout fallback
        xo=capture_features(torch,model,tok,outer,args)
        yt=np.asarray([r['answer'] for r in tr],np.float64)
        yv=np.asarray([r['answer'] for r in va],np.float64)
        mean=0.0;scale=global_scale
        print(f'C0 scale={radius}: fitting deterministic teacher (train-only, rank={args.teacher_rank})',flush=True)
        geo,config=teacher_fit(xt,signed_log(yt),args.teacher_rank,args.seed+radius,ks)
        # Fit the local teacher in symlog space; evaluate every method in
        # original payoff units. OOF targets cannot retrieve their own labels.
        teacher_val=inverse_signed_log(teacher_predict(geo,config,xv,signed_log(yt)))
        teacher_outer=inverse_signed_log(teacher_predict(geo,config,xo,signed_log(yt)))
        balance_predictions={}
        if balance_rows:
            balance_predictions['deterministic']=inverse_signed_log(teacher_predict(geo,config,balance_x,signed_log(yt)))
            balance_predictions['constant']=np.full(len(balance_rows),np.median(yt),dtype=np.float64)
        yo=np.asarray([r['answer'] for r in outer],np.float64)
        teacher_mae=stats(yv,teacher_val)['mae']
        constant_mae=stats(yv,np.full_like(yv,np.median(yt)))['mae']
        print(f'C0 scale={radius}: deterministic_MAE={teacher_mae:.5g} constant_MAE={constant_mae:.5g} teacher={config["method"]}/k{config["k"]}',flush=True)
        stage_models={}
        # Fit independent deterministic comparator and capture Qwen features once.
        # Every neural variant uses exact-answer labels only, never teacher outputs.
        for name in variant_names:
            net,opt=suite[name]
            print(f'C0 scale={radius}: TRAIN variant={name} layers={VARIANTS[name]}',flush=True)
            # Cache activation vectors in CPU RAM, minibatches migrate to GPU.
            # GPU Qwen forward is done once for each distinct input per scale.
            best=float('inf');best_state=None;best_opt=None;stalled=0;checks=[]
            rng=np.random.default_rng(args.seed+radius*1009+list(VARIANTS).index(name)*100003)
            for step in range(1,args.steps_per_radius+1):
                ind=rng.integers(0,len(tr),size=args.batch_size)
                x=torch.as_tensor(xt[ind].copy(),device=args.device)
                y=torch.as_tensor(signed_log(yt[ind]).astype('float32'),device=args.device)
                net.train();pred,baseline=net(x)
                # The deterministic comparator never affects neural gradients.
                loss=neural_loss(torch,pred,baseline,y,beta=args.huber_beta,aux_weight=args.aux_weight)
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
            if balance_rows:
                true_balance=np.asarray([r['answer'] for r in balance_rows],np.float64)
                balance_zz,balance_zp=eval_heads(torch,net,balance_x,true_balance,mean,scale,args.device,args.eval_batch)
                balance_predictions[f'{name}.zprime']=balance_zp
                balance_predictions[f'{name}.zdoubleprime']=balance_zz
            opred,oraw=eval_heads(torch,net,xo,yo,mean,scale,args.device,args.eval_batch)
            path=out/f'scale_{radius}'/name;path.mkdir(parents=True,exist_ok=True)
            with (path/'validation_predictions.csv').open('w',newline='',encoding='utf-8') as file:
                writer=csv.writer(file);writer.writerow(['game','family','p','q','a','b','scale','true','deterministic','zprime','zdoubleprime'])
                for row,y,t,r,s in zip(va,yv,teacher_val,raw,final): writer.writerow([row['game'],row['family'],row['p'],row['q'],row['a'],row['b'],row['scale'],y,t,r,s])
            with (path/'outer_predictions.csv').open('w',newline='',encoding='utf-8') as file:
                writer=csv.writer(file);writer.writerow(['game','family','p','q','a','b','scale','true','deterministic','zprime','zdoubleprime'])
                for row,y,t,r,s in zip(outer,yo,teacher_outer,oraw,opred): writer.writerow([row['game'],row['family'],row['p'],row['q'],row['a'],row['b'],row['scale'],y,t,r,s])
            summary={'scale_band':radius,'variant':name,'hidden':list(VARIANTS[name]),'train_count':len(tr),'validation_count':len(va),'outer_count':len(outer),'teacher_config':config,
                     'best_corrected_mae':best,'neural_supervision':'exact_answers_only',
                     'deterministic_role':'independent_evaluation_only','validation':{'local':stats(yv,teacher_val),'zprime':stats(yv,raw),
                      'zdoubleprime':stats(yv,final),'constant':stats(yv,np.full_like(yv,np.median(yt)))},
                     'outer':{'local':stats(yo,teacher_outer),'zprime':stats(yo,oraw),
                              'zdoubleprime':stats(yo,opred),'constant':stats(yo,np.full_like(yo,np.median(yt)))},'checks':checks}
            (path/'report.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
            torch.save({'model':net.state_dict(),'optimizer':opt.state_dict(),'input_width':int(xt.shape[1]),
                        'scale_band':radius,'variant':name,'hidden':list(VARIANTS[name]),'population':'C0_games','scales':scales,'seed':args.seed,'normalization':{'mean':mean,'scale':scale}},path/'encoder.pt')
            summary['retention']=retention_summary(torch,net,prior_evals[name],scale,args)
            summary['retention_gate_pass']=all(v['retained_95pct'] for v in summary['retention'].values())
            (path/'report.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
            prior_evals[name][radius]=(xv,yv,summary['validation']['zdoubleprime']['mae'])
            stage_models[name]=summary
            print(f'C0 scale={radius}: variant={name} FINAL local={teacher_mae:.5g} zprime={summary["validation"]["zprime"]["mae"]:.5g} zdoubleprime={best:.5g}',flush=True)
        balance_report=None
        if balance_rows:
            balance_dir=out/f'scale_{radius}'/'balance_audit'
            balance_report=save_balance_report(balance_dir,radius,balance_rows,balance_predictions,{
                'deltas':positive_ints(args.balance_deltas,include_zero=True),
                'magnitudes':positive_ints(args.balance_magnitudes),
                'samples_per_cell':args.balance_samples_per_cell,'seed':args.seed})
            print(f'C0 scale={radius}: BALANCE AUDIT (per-delta/magnitude matrix: {balance_dir}/balance_cells.csv)',flush=True)
            for name in variant_names:
                model_audit=balance_report['models'][f'{name}.zdoubleprime']
                by_delta={x['delta']:x for x in model_audit['by_delta']}
                def fmt(d):
                    item=by_delta.get(d)
                    return f'{item["mae"]:.4g}' if item else 'n/a'
                print(f'  {name}: balance_MAE zero={fmt(0)} d1={fmt(1)} d2={fmt(2)} d4={fmt(4)} d8={fmt(8)} d16={fmt(16)} d32={fmt(32)} overall={model_audit["overall"]["mae"]:.5g}',flush=True)
        ranking=sorted(stage_models, key=lambda n: stage_models[n]['validation']['zdoubleprime']['mae'])
        stage={'scale_band':radius,'teacher_mae':teacher_mae,'constant_mae':constant_mae,
               'models':stage_models,'ranking':ranking,'winner':ranking[0],
               'train_count':len(tr),'validation_count':len(va),
               'balance_audit':str(out/f'scale_{radius}'/'balance_audit'/'balance_report.json') if balance_rows else None,
               'retention_failures':[n for n in variant_names if not stage_models[n]['retention_gate_pass']]}
        stages.append(stage)
        (out/f'scale_{radius}'/'report.json').write_text(json.dumps(stage,indent=2),encoding='utf-8')
        (out/'report.json').write_text(json.dumps({'schema':'nanojev.c0-compressive-scale-sweep.v1',
            'model':args.model,'population':'C0_games','output_target':'signed_log_payoff',
            'neural_supervision':'exact_answers_only','deterministic_role':'independent_evaluation_only','variants':{n:list(VARIANTS[n]) for n in variant_names},
            'stages':stages},indent=2),encoding='utf-8')
        print('C0 scale=%s: RANKING %s'%(radius, ' | '.join(f'{n}={stage_models[n]["validation"]["zdoubleprime"]["mae"]:.5g}' for n in ranking)),flush=True)
    return stages


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--model',default='Qwen/Qwen3-0.6B');ap.add_argument('--adapter');ap.add_argument('--device',default='cuda')
    ap.add_argument('--scales',default='1,10,100,1000,10000');ap.add_argument('--source-samples',type=int,default=40000)
    ap.add_argument('--batch-size',type=int,default=64);ap.add_argument('--capture-batch',type=int,default=16)
    ap.add_argument('--eval-batch',type=int,default=256);ap.add_argument('--steps-per-radius',type=int,default=9000)
    ap.add_argument('--eval-every',type=int,default=100);ap.add_argument('--patience',type=int,default=90)
    ap.add_argument('--teacher-rank',type=int,default=32);ap.add_argument('--teacher-ks',default='8,16,32,64')
    ap.add_argument('--teacher-weight',type=float,default=0.,
                    help='legacy compatibility; must be 0 (deterministic comparison never trains neural encoders)')
    ap.add_argument('--aux-weight',type=float,default=.25);ap.add_argument('--lr',type=float,default=2e-4)
    ap.add_argument('--weight-decay',type=float,default=1e-4);ap.add_argument('--grad-clip',type=float,default=1.)
    ap.add_argument('--huber-beta',type=float,default=.1);ap.add_argument('--max-length',type=int,default=256)
    ap.add_argument('--max-stage-points',type=int,default=8192,
                    help='max C0 prompts per scale band, including earlier-band replay')
    ap.add_argument('--replay-fraction',type=float,default=.25)
    ap.add_argument('--outer-points',type=int,default=256)
    ap.add_argument('--variants',default='all',help='all (default), or comma-separated: '+','.join(VARIANTS))
    ap.add_argument('--balance-diagnostic',action=argparse.BooleanOptionalAction,default=True,
                    help='run held-out power-of-two cancellation audit at every completed scale (default on)')
    ap.add_argument('--balance-deltas',default=','.join(map(str,DEFAULT_DELTAS)))
    ap.add_argument('--balance-magnitudes',default=','.join(map(str,DEFAULT_MAGNITUDES)))
    ap.add_argument('--balance-samples-per-cell',type=int,default=8)
    ap.add_argument('--zero-line-hard-train',action='store_true',
                    help='opt-in hard acquisition: zero, +/-1, +/-2, +/-4... with gated radius and retention')
    ap.add_argument('--zero-line-max-radius',type=int,default=1024)
    ap.add_argument('--zero-line-init-dir',default='',
                    help='optional previous C0 scale directory holding <variant>/encoder.pt')
    ap.add_argument('--zero-line-steps',type=int,default=3000,
                    help='steps per zero-line radius and per additional repair attempt')
    ap.add_argument('--zero-line-max-repairs',type=int,default=2)
    ap.add_argument('--zero-line-exact-threshold',type=float,default=.95,
                    help='minimum exact rounded fraction in EACH delta/magnitude development cell')
    ap.add_argument('--zero-line-train-samples-per-cell',type=int,default=32)
    ap.add_argument('--zero-line-gate-samples-per-cell',type=int,default=8)
    ap.add_argument('--zero-line-audit-samples-per-cell',type=int,default=8)
    ap.add_argument('--zero-line-replay-per-shell',type=int,default=64)
    ap.add_argument('--zero-line-ordinary-fraction',type=float,default=.20)
    ap.add_argument('--zero-line-ordinary-pool',type=int,default=4096)
    ap.add_argument('--zero-line-mixture',action='store_true',
                    help='opt-in paired P0-P7 arithmetic/natural-language training mixture with explicit weighted sampling')
    ap.add_argument('--zero-line-program-weights',default='',
                    help='overrides P0:weight,...,P7:weight (all default 1); 0 disables a rung')
    ap.add_argument('--zero-line-form-weights',default='',
                    help='arithmetic:weight,natural:weight (both default 1)')
    ap.add_argument('--zero-line-delta-weights',default='',
                    help='zero:weight,near:weight,outer:weight (all default 1)')
    ap.add_argument('--zero-line-magnitude-weights',default='',
                    help='operand magnitude:weight, e.g. 100:1,1000000:4 (all default 1)')
    ap.add_argument('--zero-line-nonzero-lookahead',type=int,default=2,
                    help='train from this many future signed delta shells without advancing acceptance gates')
    ap.add_argument('--zero-line-mixture-per-cell',type=int,default=4,
                    help='max source prompts per (delta/sign,magnitude,shell) for paired prompt capture')
    ap.add_argument('--zero-line-adaptive',action='store_true',
                    help='opt-in failure-driven mixture and independently gated magnitude/delta acquisition')
    ap.add_argument('--zero-line-repair-boost',type=float,default=4.,
                    help='maximum extra sampling multiplier for failing strata')
    ap.add_argument('--zero-line-repair-fraction',type=float,default=.40,
                    help='fraction of focus samples preferentially drawn from failing strata')
    ap.add_argument('--zero-line-retention-fraction',type=float,default=.30,
                    help='fraction of focus samples replaying previously acquired cells')
    ap.add_argument('--zero-line-repair-lr-factor',type=float,default=.25,
                    help='learning rate factor for subsequent repair rounds')
    ap.add_argument('--zero-line-report-worst',type=int,default=10,
                    help='number of failing program/form/delta/magnitude cells printed')
    ap.add_argument('--zero-line-lazy-capture',action=argparse.BooleanOptionalAction,default=None,
                    help='capture features on demand (automatic in adaptive mode)')
    ap.add_argument('--zero-line-backbone-phase',action='store_true',
                    help='opt-in LoRA arithmetic adaptation before frozen C0 capture/training')
    ap.add_argument('--zero-line-backbone-steps',type=int,default=2000)
    ap.add_argument('--zero-line-backbone-rank',type=int,default=16)
    ap.add_argument('--zero-line-backbone-lr',type=float,default=1e-4)
    ap.add_argument('--zero-line-backbone-batch',type=int,default=2)
    ap.add_argument('--zero-line-backbone-accum',type=int,default=4)
    ap.add_argument('--zero-line-backbone-eval-every',type=int,default=200)
    ap.add_argument('--zero-line-backbone-eval-cases',type=int,default=64)
    ap.add_argument('--zero-line-backbone-train-cap',type=int,default=4096)
    ap.add_argument('--zero-line-backbone-min-improvement',type=float,default=.02)
    ap.add_argument('--zero-line-advance-on-fail',action='store_true',
                    help='research only: continue expanding even if exactness/retention gate fails')

    ap.add_argument('--seed',type=int,default=47);ap.add_argument('--output',default='runtime/reports/qwen-c0-multi-frontier')
    args=ap.parse_args()
    if any((args.batch_size<1,args.capture_batch<1,args.steps_per_radius<1,args.eval_every<1,
            args.max_stage_points<32,args.outer_points<1,
            not 0<=args.replay_fraction<1,args.source_samples<1000,
            args.teacher_rank<1,args.teacher_weight!=0,args.aux_weight<0,args.patience<1,
            args.balance_samples_per_cell<1)):
        ap.error('invalid training arguments')
    try:
        deltas=positive_ints(args.balance_deltas,include_zero=True)
        magnitudes=positive_ints(args.balance_magnitudes)
        if 0 not in deltas or any(d and (d&(d-1)) for d in deltas):
            raise ValueError('balance deltas must include zero and only powers of two')
        if any(m<8 for m in magnitudes):raise ValueError('balance magnitudes must be >= 8')
    except ValueError as error: ap.error(str(error))
    try: resolve_variants(args.variants)
    except ValueError as error: ap.error(str(error))
    if args.zero_line_hard_train:
        from tools.nanojev_qwen_c0_zero_line import validate_stage_args
        try:validate_stage_args(args)
        except ValueError as error:ap.error(str(error))
    from tools.nanojev_qwen_c0_backbone_phase import validate_backbone_args
    try:validate_backbone_args(args)
    except ValueError as error:ap.error(str(error))
    run(args)

if __name__=='__main__':main()
