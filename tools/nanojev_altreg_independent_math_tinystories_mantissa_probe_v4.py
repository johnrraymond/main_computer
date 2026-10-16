#!/usr/bin/env python3
"""Direct mantissa recovery probe; parent backbone, decoder and exponent stay frozen.

This is a NEW, isolated fork. Uses the same split/reservation rules as precision v2.
Only the new per-operand mantissa head receives gradient updates.
"""
from __future__ import annotations
import argparse, copy, hashlib, json, math
from pathlib import Path
import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
import nanojev_altreg_independent_math_tinystories_precision_v2 as base

SCHEMA='nanojev.independent-math.mantissa-probe.v4'
DEFAULT_OUT=Path.home()/'NanoJev'/'runs'/'independent_altreg_math_tinystories_mantissa_probe_v4'

class MantissaHead(nn.Module):
    """Predict log10 mantissa fractions directly, not residuals of parent's mantissa."""
    def __init__(self, feature_dim, width=192):
        super().__init__()
        self.net=nn.Sequential(nn.LayerNorm(feature_dim+2+len(base.OP_IDS)),nn.Linear(feature_dim+2+len(base.OP_IDS),width),
                               nn.GELU(),nn.Linear(width,width),nn.GELU(),nn.Linear(width,2))
    def forward(self, features, parent_coord, ops):
        # ops is the semantic operation identifier, represented as a normalized one-hot.
        # Register slots always represent X,Y, independent of presentation order.
        op=F.one_hot(ops,num_classes=len(base.OP_IDS)).to(dtype=features.dtype)
        return torch.sigmoid(self.net(torch.cat([features,parent_coord,op],dim=-1)))


def componentize(values):
    v=torch.as_tensor(values,dtype=torch.float32,device=values.device if isinstance(values,torch.Tensor) else None)
    log=torch.log10(v.abs().clamp_min(1.))
    e=torch.floor(log)
    m=log-e
    return torch.sign(v),e,m


def reconstruct(parent_num, fraction, exponent=None):
    sign,e,_=componentize(parent_num)
    if exponent is not None:e=exponent
    logmag=(e+fraction).clamp(0.,math.log10(float(base.registers.MAX_VALUE)))
    out=sign*torch.pow(10.,logmag)
    return torch.where(parent_num.abs()<1.,parent_num,out)


def get_parent_numbers(parent, features, ops):
    with torch.no_grad():
        hidden=parent.extract(features)
        coord=parent.xy_coord(hidden)
        result=parent.result_coord(hidden,coord,ops)
        final=parent.final_coord(hidden,coord,ops,result)
        parent_num=torch.sinh(coord.float()*base.X_SCALE)
    return coord,parent_num,result,final


def forward_values(parent, head, features, ops, truth=None):
    coord,p_num,result,final=get_parent_numbers(parent,features,ops)
    frac=head(features.float(),coord.float(),ops)
    predicted=reconstruct(p_num,frac)
    values={'parent':p_num,'predicted':predicted}
    if truth is not None:
        _,oracle_e,_=componentize(truth)
        values['oracle_exponent']=reconstruct(p_num,frac,oracle_e)
    return values,frac


def loss_function(frac,truth,predicted):
    _,_,target=componentize(truth)
    valid=truth.abs()>=1.
    if not bool(valid.any()):return frac.sum()*0.,{'mantissa':0.,'log_ratio':0.}
    mant=F.smooth_l1_loss(frac[valid],target[valid],beta=.05)
    # Parent exponent is fixed: log-ratio also trains the mantissa toward the
    # closest possible number under that fixed exponent (without changing exponent).
    tlog=torch.log10(truth.abs().clamp_min(1.))
    plog=torch.log10(predicted.abs().clamp_min(1.))
    ratio=F.smooth_l1_loss(plog[valid],tlog[valid],beta=.05)
    return mant + .2*ratio,{'mantissa':float(mant.detach()),'log_ratio':float(ratio.detach())}


def score(pred,truth):
    correct=(pred-truth).abs()<=.05*truth.abs().clamp_min(1.)
    return {'operand_hits':int(correct.sum()),'operand_5pct':float(correct.float().mean()),
            'joint_hits':int(correct.all(dim=-1).sum()),'joint_5pct':float(correct.all(dim=-1).float().mean()),
            'mean_abs_relative_error':float(((pred-truth).abs()/truth.abs().clamp_min(1.)).mean())}


def evaluate(parent,head,pack,device,batch_size):
    truth=torch.tensor([[e['a'],e['b']] for e in pack['examples']],device=device,dtype=torch.float32)
    ops=torch.tensor([base.OP_IDS[e['operation']] for e in pack['examples']],device=device,dtype=torch.long)
    output={}
    with torch.inference_mode():
        for orient in ('original','reversed'):
            collected={k:[] for k in ('parent','predicted','oracle_exponent')}
            fracs=[]
            for i in range(0,len(truth),batch_size):
                v,f=forward_values(parent,head,pack[orient][i:i+batch_size].to(device),ops[i:i+batch_size],truth[i:i+batch_size])
                for k in collected:collected[k].append(v[k])
                fracs.append(f)
            output[orient]={k:torch.cat(v,dim=0) for k,v in collected.items()}
            output[orient]['fraction']=torch.cat(fracs,dim=0)
        output['averaged']={k:(output['original'][k]+output['reversed'][k])/2
                            for k in ('parent','predicted','oracle_exponent')}
        metrics={orient:{k:score(output[orient][k],truth) for k in ('parent','predicted','oracle_exponent')}
                 for orient in ('original','reversed','averaged')}
        _,_,true_m=componentize(truth)
        for orient in ('original','reversed'):
            metrics[orient]['mantissa_fraction_mae']=float((output[orient]['fraction']-true_m).abs().mean())
    return metrics


def save_checkpoint(path,payload):
    base.atomic_save(path,payload)


def run(args):
    if args.self_test:return self_test()
    if args.device=='cuda' and not torch.cuda.is_available():raise RuntimeError('MANTISSA_V4_CUDA_UNAVAILABLE')
    if args.batch_size<1 or args.eval_every<1 or args.steps<0:raise ValueError('MANTISSA_V4_BAD_ARGS')
    parent_file=args.parent_root/(args.parent_checkpoint+'.pt')
    original=torch.load(parent_file,map_location='cpu',weights_only=True)
    if original.get('schema')!=base.SCHEMA or original.get('backbone')!='tinystories':raise RuntimeError('MANTISSA_V4_PARENT_MISMATCH')
    cfg=original['config'];parent=base.MathHead(cfg['input_dim'],cfg['width'],len(base.OP_IDS)).to(args.device)
    parent.load_state_dict(original['head'],strict=True);parent.eval()
    for p in parent.parameters():p.requires_grad_(False)
    cache=torch.load(args.parent_root/'validation-cache.pt',map_location='cpu',weights_only=True)
    if cache['config']!=cfg:raise RuntimeError('MANTISSA_V4_CACHE_MISMATCH')
    head=MantissaHead(cfg['input_dim']).to(args.device)
    optimizer=torch.optim.AdamW(head.parameters(),lr=args.lr)
    identity={'schema':SCHEMA,'parent_sha256':hashlib.sha256(parent_file.read_bytes()).hexdigest(),
              'parent_step':int(original['step']),'parent_config':cfg,'lr':args.lr,'batch_size':args.batch_size}
    out=args.results_root;start=0;best=(-1.,-1.,-1.)
    if args.resume or args.eval_only:
        saved=torch.load(out/'latest.pt',map_location='cpu',weights_only=True)
        if saved.get('schema')!=SCHEMA or saved.get('config')!=identity:raise RuntimeError('MANTISSA_V4_RESUME_DRIFT')
        head.load_state_dict(saved['head'],strict=True);start=int(saved['step']);best=tuple(saved['best'])
        if args.resume:optimizer.load_state_dict(saved['optimizer'])
    if args.eval_only or args.steps==0:
        metrics=evaluate(parent,head,cache['validation'],args.device,args.batch_size)
        base.emit('mantissa_v4_evaluation',step=start,validation=metrics)
        return 0
    # Only training requires TinyStories; held-out evaluations use precomputed captures.
    class C:pass
    c=C();c.device=args.device;c.dtype=args.dtype;c.allow_model_download=args.allow_model_download
    _,backbone,tokenizer,model_device,_=base.cassette.load_source('tinystories',c)
    if str(model_device)!=args.device:raise RuntimeError('MANTISSA_V4_DEVICE_DRIFT')
    tasks=base.registers.build_plan(cfg['seed'])[1]
    reserved={(min(int(t['a']),int(t['b'])),max(int(t['a']),int(t['b']))) for t in tasks if t['split']!='train'}
    stream=base.Stream(cfg['seed']+403,reserved)
    for _ in range(start*args.batch_size):stream.get()
    for step in range(start+1,start+args.steps+1):
        examples=[stream.get() for _ in range(args.batch_size)]
        prompts=[base.prompt(e['a'],e['b'],e['operation'],((step-1)*args.batch_size+i)%2==1)
                 for i,e in enumerate(examples)]
        z=base.capture(backbone,tokenizer,args.device,prompts,cfg['max_prompt_tokens']).to(args.device)
        ops=torch.tensor([base.OP_IDS[e['operation']] for e in examples],dtype=torch.long,device=args.device)
        truth=torch.tensor([[e['a'],e['b']] for e in examples],dtype=torch.float32,device=args.device)
        head.train();optimizer.zero_grad(set_to_none=True)
        values,frac=forward_values(parent,head,z,ops,truth)
        loss,parts=loss_function(frac,truth,values['predicted'])
        if not bool(torch.isfinite(loss)):raise RuntimeError('MANTISSA_V4_NONFINITE_LOSS')
        loss.backward();nn.utils.clip_grad_norm_(head.parameters(),1.);optimizer.step()
        if step%args.report_every==0:base.emit('mantissa_v4_progress',step=step,loss=float(loss.detach()),parts=parts)
        if step%args.eval_every==0 or step==start+args.steps:
            head.eval();metrics=evaluate(parent,head,cache['validation'],args.device,args.batch_size)
            o,r=metrics['original'],metrics['reversed']
            rank=(min(o['predicted']['joint_5pct'],r['predicted']['joint_5pct']),
                  min(o['predicted']['operand_5pct'],r['predicted']['operand_5pct']),
                  metrics['averaged']['predicted']['operand_5pct'])
            improved=rank>best
            if improved:best=rank
            snapshot={'schema':SCHEMA,'config':identity,'head':copy.deepcopy(head.state_dict()),
                      'optimizer':optimizer.state_dict(),'step':step,'best':list(best)}
            save_checkpoint(out/'latest.pt',snapshot)
            if improved:save_checkpoint(out/'best-joint.pt',snapshot)
            base.write_json(out/f'validation-step-{step:08d}.json',{'step':step,'metrics':metrics,'rank':rank})
            base.emit('mantissa_v4_validation',step=step,rank=rank,metrics=metrics)
    head.eval()
    base.write_json(out/'test-last.json',{'step':start+args.steps,
                    'metrics':evaluate(parent,head,cache['test'],args.device,args.batch_size)})
    base.emit('mantissa_v4_complete',step=start+args.steps,best=best,root=str(out))
    return 0


def self_test():
    torch.manual_seed(123)
    x=torch.tensor([[135.,-27.],[25.,1000.]])
    s,e,m=componentize(x)
    assert torch.allclose(reconstruct(x,m),x,atol=2e-4)
    h=MantissaHead(16);z=torch.randn(4,16);coord=torch.randn(4,2);ops=torch.tensor([i%len(base.OP_IDS) for i in range(4)])
    frac=h(z,coord,ops)
    assert frac.shape==(4,2) and bool(((frac>0)&(frac<1)).all())
    parent=torch.tensor([[130.,-29.],[30.,900.],[4.,7.],[60.,8.]])
    truth=torch.tensor([[135.,-27.],[25.,1000.],[4.,7.],[60.,8.]])
    guess=reconstruct(parent,frac)
    loss,_=loss_function(frac,truth,guess);loss.backward()
    assert all(p.grad is not None for p in h.parameters())
    assert torch.equal(componentize(guess)[1],componentize(parent)[1])
    assert score(truth,truth)['joint_hits']==4
    assert score(parent,truth)['operand_hits']<8
    print(json.dumps({'event':'mantissa_v4_self_test_pass','parent_loaded':False,'backbone_loaded':False}))
    return 0


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--self-test',action='store_true')
    p.add_argument('--parent-root',type=Path,default=base.DEFAULT_ROOT)
    p.add_argument('--parent-checkpoint',choices=['latest','best-precision','best-asinh'],default='latest')
    p.add_argument('--results-root',type=Path,default=DEFAULT_OUT)
    p.add_argument('--steps',type=int,default=2000)
    p.add_argument('--batch-size',type=int,default=16)
    p.add_argument('--lr',type=float,default=1e-4)
    p.add_argument('--eval-every',type=int,default=100)
    p.add_argument('--report-every',type=int,default=50)
    p.add_argument('--device',choices=['cpu','cuda'],default='cuda')
    p.add_argument('--dtype',choices=['float16','float32','bfloat16'],default='float16')
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--eval-only',action='store_true')
    return p

if __name__=='__main__':raise SystemExit(run(parser().parse_args()))
