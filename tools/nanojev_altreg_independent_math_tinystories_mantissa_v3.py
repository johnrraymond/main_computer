#!/usr/bin/env python3
"""Read-only parent v2 -> separate mantissa/exponent precision v3 fork.

Frozen TinyStories backbone and v2 math head. Updates only a bounded numerical
reconstructor, with sign, exponent, mantissa and numerical-relative losses.
Parent files are never written; validation caches are reused byte-for-byte.
"""
from __future__ import annotations
import argparse
import hashlib
import math
from pathlib import Path
import torch
from torch import nn
from torch.nn import functional as F
import numpy as np
import nanojev_altreg_independent_math_tinystories_precision_v2 as base

SCHEMA='nanojev.independent-math.mantissa.v3'
DEFAULT_PARENT=base.DEFAULT_ROOT
DEFAULT_OUT=Path.home()/'NanoJev'/'runs'/'independent_altreg_math_tinystories_mantissa_v3'


def log_parts(numeric):
    """Return signs, decimal exponent, and fractional base-10 log magnitude."""
    a=torch.as_tensor(numeric,dtype=torch.float32)
    mag=a.abs().clamp_min(1.)
    logmag=mag.log10()
    exponent=torch.floor(logmag)
    mantissa=logmag-exponent
    sign=torch.sign(a)
    return sign,exponent,mantissa


class Reconstructor(nn.Module):
    def __init__(self,dim,width=128):
        super().__init__()
        self.net=nn.Sequential(nn.LayerNorm(dim+2),nn.Linear(dim+2,width),nn.GELU(),nn.Linear(width,width),nn.GELU())
        self.exponent=nn.Linear(width,2)
        self.mantissa=nn.Linear(width,2)
        self.sign=nn.Linear(width,2)
        self.zero=nn.Linear(width,2)
        # At fork, correction is exactly zero and parent numerical output unchanged.
        for layer in (self.exponent,self.mantissa):
            nn.init.zeros_(layer.weight);nn.init.zeros_(layer.bias)

    def forward(self,features,baseline_xy):
        h=self.net(torch.cat((features,baseline_xy),dim=-1))
        de=3*torch.tanh(self.exponent(h))
        dm=.75*torch.tanh(self.mantissa(h))
        return de,dm,self.sign(h),self.zero(h)


def decoded(parent,adapter,features,ops):
    with torch.no_grad():
        h=parent.extract(features)
        baseline=parent.xy_coord(h)
        result=parent.result_coord(h,baseline,ops)
        final=parent.final_coord(h,baseline,ops,result)
        baseline_numeric=torch.sinh(baseline*base.X_SCALE)
    de,dm,sign_logit,zero_logit=adapter(features,baseline)
    _,old_e,old_m=log_parts(baseline_numeric)
    new_logmag=(old_e+de+old_m+dm).clamp(0.,math.log10(float(base.registers.MAX_VALUE)))
    # Keep exceptionally accurate inherited sign pathway intact. Sign/zero
    # predictions are independently trained and reported, not silently applied.
    corrected_numeric=torch.sign(baseline_numeric)*torch.pow(10.,new_logmag)
    corrected_numeric=torch.where(baseline_numeric.abs()<1.,baseline_numeric,corrected_numeric)
    corrected_coord=torch.asinh(corrected_numeric)/base.X_SCALE
    return corrected_coord,result,final,(de,dm,sign_logit,zero_logit),baseline_numeric


class Combined(nn.Module):
    def __init__(self,parent,adapter):super().__init__();self.parent=parent;self.adapter=adapter
    def forward(self,z,ops):
        corrected,r,f,_,_=decoded(self.parent,self.adapter,z,ops)
        return corrected,r,f


def loss_terms(outputs,base_numeric,truth,truth_coord,pred_coord):
    de,dm,sign_logit,zero_logit=outputs
    ts,te,tm=log_parts(truth)
    _,be,bm=log_parts(base_numeric)
    # Targets are anchored on the parent estimate; small shifts of decimal
    # boundary are explained by both exponents and mantissas.
    exponent_target=(te-be).clamp(-3.,3.)
    mantissa_target=(tm-bm).clamp(-.75,.75)
    valid=(truth.abs()>=1.)&(base_numeric.abs()>=1.)
    def masked(pred,target):
        if not bool(valid.any()):return pred.square().mean()*0.
        return F.smooth_l1_loss(pred[valid],target[valid])
    le=masked(de,exponent_target)
    lm=masked(dm,mantissa_target)
    sign_target=(ts>0).float()
    ls=F.binary_cross_entropy_with_logits(sign_logit,sign_target)
    lz=F.binary_cross_entropy_with_logits(zero_logit,(truth.abs()<1.).float())
    # 5% relative precision evaluated in numeric space via a differentiable
    # log-ratio. No vast sinh gradient and no orientation-specific objectives.
    with torch.no_grad():
        truth_log=torch.log1p(truth.abs())
    pred_num=torch.sinh(pred_coord*base.X_SCALE)
    log_ratio=torch.log1p(pred_num.abs())-truth_log
    lp=F.smooth_l1_loss(log_ratio/.05,torch.zeros_like(log_ratio),beta=1.)
    lc=F.mse_loss(pred_coord,truth_coord)
    return {'exponent':le,'mantissa':lm,'sign':ls,'zero':lz,'relative':lp,'coordinate':lc}


def check_snapshot(snapshot,model,config):
    if snapshot.get('schema')!=SCHEMA or snapshot.get('config')!=config:
        raise RuntimeError('MANTISSA_V3_CHECKPOINT_IDENTITY_DRIFT')
    model.load_state_dict(snapshot['adapter'])


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--self-test',action='store_true')
    p.add_argument('--parent-root',type=Path,default=DEFAULT_PARENT)
    p.add_argument('--parent-checkpoint',choices=['latest','best-precision','best-asinh'],default='latest')
    p.add_argument('--results-root',type=Path,default=DEFAULT_OUT)
    p.add_argument('--steps',type=int,default=2000)
    p.add_argument('--batch-size',type=int,default=16)
    p.add_argument('--eval-every',type=int,default=100)
    p.add_argument('--report-every',type=int,default=50)
    p.add_argument('--lr',type=float,default=0.0001)
    p.add_argument('--device',choices=['auto','cuda','cpu'],default='cuda')
    p.add_argument('--dtype',choices=['float16','float32','bfloat16'],default='float16')
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--eval-only',action='store_true')
    return p


def run(args):
    if args.self_test:return self_test()
    device=('cuda' if torch.cuda.is_available() else 'cpu') if args.device=='auto' else args.device
    if device=='cuda' and not torch.cuda.is_available():raise RuntimeError('MANTISSA_CUDA_UNAVAILABLE')
    if args.steps<0 or args.eval_every<1 or args.batch_size<1:raise ValueError('MANTISSA_BAD_PARAMETERS')
    checkpoint=args.parent_root/(args.parent_checkpoint+'.pt')
    parent_saved=torch.load(checkpoint,map_location='cpu',weights_only=True)
    if parent_saved.get('schema')!=base.SCHEMA or parent_saved.get('backbone')!='tinystories':
        raise RuntimeError('MANTISSA_INCOMPATIBLE_PARENT')
    cfg=parent_saved['config'];dim=cfg['input_dim']
    parent=base.MathHead(dim,cfg['width'],len(base.OP_IDS)).to(device)
    parent.load_state_dict(parent_saved['head']);parent.eval()
    for p in parent.parameters():p.requires_grad_(False)
    cache=torch.load(args.parent_root/'validation-cache.pt',map_location='cpu',weights_only=True)
    if cache['config']!=cfg:raise RuntimeError('MANTISSA_PARENT_CACHE_DRIFT')
    val_pack=cache['validation'];test_pack=cache['test']
    adapter=Reconstructor(dim).to(device);combined=Combined(parent,adapter)
    opt=torch.optim.AdamW(adapter.parameters(),lr=args.lr)
    sha=hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    identity={'schema':SCHEMA,'parent_sha256':sha,'parent_step':int(parent_saved['step']),
              'parent_config':cfg,'dim':dim,'lr':args.lr,'batch_size':args.batch_size}
    out=args.results_root;latest=out/'latest.pt';start=0
    if args.resume or args.eval_only:
        saved=torch.load(latest,map_location='cpu',weights_only=True)
        check_snapshot(saved,adapter,identity);start=int(saved['step'])
        if args.resume:opt.load_state_dict(saved['optimizer'])
    if args.eval_only:
        base.emit('mantissa_v3_evaluation',step=start,validation=base.evaluate(combined,val_pack,device),test=base.evaluate(combined,test_pack,device));return 0
    if not args.resume:
        baseline=base.evaluate(combined,val_pack,device)
        base.write_json(out/'parent-baseline.json',{'step':int(parent_saved['step']),'validation':baseline,'identity':identity})
    else:baseline=None
    if args.steps==0:
        base.emit('mantissa_v3_baseline',validation=baseline);return 0
    # Training alone loads frozen TinyStories. Evaluation above uses existing
    # paired held-out captures without loading the transformer.
    backbone,tokenizer=None,None
    class Config:pass
    ca=Config();ca.device=device;ca.dtype=args.dtype;ca.allow_model_download=args.allow_model_download
    _,backbone,tokenizer,model_device,info=base.cassette.load_source('tinystories',ca)
    if str(model_device)!=device:raise RuntimeError('MANTISSA_BACKBONE_DEVICE_DRIFT')
    tasks=base.registers.build_plan(cfg['seed'])[1]
    reserved={(min(int(t['a']),int(t['b'])),max(int(t['a']),int(t['b']))) for t in tasks if t['split']!='train'}
    stream=base.Stream(cfg['seed']+103,reserved)
    # Deterministically fast-forward the v3 stream upon resume. No parent
    # optimizer, data stream or checkpoint is modified.
    for _ in range(start*args.batch_size):stream.get()
    best=dict(saved['best']) if args.resume else {'joint':-1.,'operand':-1.,'step':start}
    for step in range(start+1,start+args.steps+1):
        examples=[stream.get() for _ in range(args.batch_size)]
        # Alternating orientation; semantic targets never swapped.
        prompts=[base.prompt(e['a'],e['b'],e['operation'],((step-1)*args.batch_size+i)%2==1)
                 for i,e in enumerate(examples)]
        features=base.capture(backbone,tokenizer,device,prompts,cfg['max_prompt_tokens']).to(device)
        ops=torch.as_tensor([base.OP_IDS[e['operation']] for e in examples],device=device,dtype=torch.long)
        truth_coord,_,gold,_=base.asinh_truth(examples,device)
        truth=torch.as_tensor(gold,device=device,dtype=torch.float32)
        adapter.train();opt.zero_grad(set_to_none=True)
        corrected,_,_,outputs,old=decoded(parent,adapter,features,ops)
        parts=loss_terms(outputs,old,truth,truth_coord,corrected)
        loss=parts['coordinate']+0.002*parts['relative']+0.1*(parts['exponent']+parts['mantissa'])+0.01*(parts['sign']+parts['zero'])
        if not bool(torch.isfinite(loss)):raise RuntimeError('MANTISSA_NONFINITE_LOSS')
        loss.backward()
        if any(p.grad is not None and not bool(torch.isfinite(p.grad).all()) for p in adapter.parameters()):
            raise RuntimeError('MANTISSA_NONFINITE_GRADIENT')
        nn.utils.clip_grad_norm_(adapter.parameters(),1.)
        opt.step()
        if step%args.report_every==0:
            base.emit('mantissa_v3_progress',step=step,loss=float(loss.detach()),parts={k:float(v.detach()) for k,v in parts.items()})
        if step%args.eval_every==0 or step==start+args.steps:
            adapter.eval();metrics=base.evaluate(combined,val_pack,device)
            order=(metrics['original'],metrics['reversed'])
            joint=min(row['joint_5pct'] for row in order)
            operand=sum(row['x_5pct']+row['y_5pct'] for row in order)/4
            improved=(joint,operand)>(best['joint'],best['operand'])
            if improved:best={'joint':joint,'operand':operand,'step':step}
            snapshot={'schema':SCHEMA,'config':identity,'adapter':adapter.state_dict(),
                      'optimizer':opt.state_dict(),'step':step,'best':best}
            base.atomic_save(latest,snapshot)
            if improved:base.atomic_save(out/'best-joint.pt',snapshot)
            base.write_json(out/f'validation-step-{step:08d}.json',{'step':step,'validation':metrics})
            base.emit('mantissa_v3_validation',step=step,joint_worst=joint,operand=operand,
                      original=metrics['original'],reversed=metrics['reversed'],averaged=metrics['averaged'])
    base.write_json(out/'test-last.json',{'step':start+args.steps,'test':base.evaluate(combined,test_pack,device)})
    base.emit('mantissa_v3_complete',step=start+args.steps,best=best,root=str(out));return 0


def self_test():
    torch.manual_seed(10)
    s,e,m=log_parts(torch.tensor([[135.,-27.],[0.,10.]]))
    assert torch.allclose(e,torch.tensor([[2.,1.],[0.,1.]]))
    assert torch.allclose(m[0,0],torch.tensor(math.log10(1.35)),atol=1e-6)
    a=Reconstructor(32);features=torch.randn(8,32);coord=torch.randn(8,2)*.1
    de,dm,sl,z=a(features,coord)
    assert torch.all(de==0) and torch.all(dm==0)
    assert sl.shape==(8,2) and z.shape==(8,2)
    truth=torch.tensor([[135.,27.]]*8);base_num=torch.tensor([[13.5,27.]]*8)
    target=torch.asinh(truth)/base.X_SCALE
    pred=torch.asinh(base_num)/base.X_SCALE
    outputs=a(features,coord)
    parts=loss_terms(outputs,base_num,truth,target,pred)
    assert all(bool(torch.isfinite(x)) for x in parts.values())
    loss=sum(parts.values());loss.backward()
    assert a.exponent.weight.grad is not None and a.mantissa.weight.grad is not None
    assert float(parts['relative'])>0.
    base.emit('mantissa_v3_self_test_pass',parent_loaded=False,frozen_backbone_loaded=False)
    return 0

if __name__=='__main__':raise SystemExit(run(parser().parse_args()))
