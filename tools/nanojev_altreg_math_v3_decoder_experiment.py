#!/usr/bin/env python3
"""Frozen-backbone numerical-decoder experiments: residual, factorized, attention.

Every arm starts from a fixed v2 parent and trains ONLY a new decoder. The exact
same stream seed/prompt alternation is used across arms; no source state changes.
Each output directory is independent and resume restores RNG plus stream state.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import random

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

SCHEMA='nanojev.altreg.math.decoder-experiment.v3'
BACKBONES=('tinystories','qwen','pythia')
ARMS=('residual','factorized','attention')

def sha256(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for c in iter(lambda:stream.read(2**20),b''):digest.update(c)
    return digest.hexdigest()

def emit(event,**data):print(json.dumps({'event':event,**data},sort_keys=True,allow_nan=False),flush=True)

def atomic_save(base,path,obj):base.atomic_save(path,obj)

def inputs(base,examples,device):
    return torch.tensor([base.OP_IDS[e['operation']] for e in examples],device=device,dtype=torch.long)

def target_parts(xy,max_exponent,device):
    """Absolute integer operands, including explicit zero; fractional log mantissa."""
    x=torch.as_tensor(xy,dtype=torch.float32,device=device)
    mag=x.abs()
    nonzero=mag>=1.
    sign=torch.where(x<0,torch.zeros_like(x),torch.where(x>0,torch.full_like(x,2.),torch.ones_like(x))).long()
    logmag=torch.log10(mag.clamp_min(1.))
    exponent=torch.floor(logmag).long().clamp(0,max_exponent)
    fraction=(logmag-exponent.float()).clamp(0.,1.)
    return sign,exponent,fraction,nonzero

def compose_logits(sign_logits,exponent_logits,fraction,max_value):
    label=sign_logits.argmax(dim=-1)
    sg=torch.where(label==0,-1.,torch.where(label==2,1.,0.))
    exponent=exponent_logits.argmax(dim=-1).float()
    logmag=(exponent+fraction.clamp(0.,1.)).clamp(0.,math.log10(float(max_value)))
    return sg*torch.pow(10.,logmag)

class V3Decoder(nn.Module):
    def __init__(self,parent_width,op_count,arm,max_exponent,hidden=192,token_dim=None):
        super().__init__()
        if arm not in ARMS:raise ValueError('V3_INVALID_ARM')
        self.arm=arm;self.max_exponent=max_exponent;self.op_count=op_count
        self.slot=nn.Embedding(2,16)
        self.queries=None
        if arm=='attention':
            if token_dim is None:raise ValueError('V3_ATTENTION_NEEDS_TOKEN_DIM')
            self.key=nn.Linear(token_dim,hidden,bias=False)
            self.value=nn.Linear(token_dim,hidden,bias=False)
            self.queries=nn.Parameter(torch.randn(2,hidden)*.03)
        d=parent_width+1+op_count+16+(hidden if arm=='attention' else 0)
        self.body=nn.Sequential(nn.LayerNorm(d),nn.Linear(d,hidden),nn.GELU(),nn.Linear(hidden,hidden),nn.GELU())
        if arm=='residual':
            self.delta=nn.Linear(hidden,1)
            nn.init.zeros_(self.delta.weight);nn.init.zeros_(self.delta.bias)
        else:
            self.sign=nn.Linear(hidden,3)
            self.exponent=nn.Linear(hidden,max_exponent+1)
            self.mantissa=nn.Linear(hidden,1)

    def forward(self,h,parent_xy,operations,tokens=None,mask=None):
        b=len(h)
        if parent_xy.shape!=(b,2):raise RuntimeError('V3_PARENT_SHAPE_DRIFT')
        slots=torch.arange(2,device=h.device)
        parts=[h[:,None,:].expand(-1,2,-1),parent_xy[:,:,None].detach(),
               F.one_hot(operations,num_classes=self.op_count).float()[:,None,:].expand(-1,2,-1),
               self.slot(slots)[None,:,:].expand(b,-1,-1)]
        if self.arm=='attention':
            if tokens is None or mask is None:raise RuntimeError('V3_ATTENTION_CAPTURE_MISSING')
            keys=self.key(tokens.float());vals=self.value(tokens.float())
            scores=torch.einsum('bld,rd->brl',keys,self.queries)/math.sqrt(keys.shape[-1])
            scores=scores.masked_fill(~mask.bool()[:,None,:],-1e4)
            weights=F.softmax(scores,dim=-1)
            parts.append(torch.einsum('brl,bld->brd',weights,vals))
        features=self.body(torch.cat(parts,dim=-1))
        if self.arm=='residual':
            # Parent XY is already tanh-bounded by the frozen v2 head.  A second
            # tanh would distort the parent even when the residual is exactly 0.
            # Keep raw coordinates trainable; clamp only for numeric decoding.
            xy=parent_xy+self.delta(features).squeeze(-1)
            return {'xy':xy}
        return {'sign':self.sign(features),'exponent':self.exponent(features),
                'fraction':torch.sigmoid(self.mantissa(features).squeeze(-1))}

def objective(out,true_xy,base,arm,precision_weight=.01):
    if arm=='residual':
        target=torch.asinh(true_xy)/base.X_SCALE
        mse=F.mse_loss(out['xy'],target)
        if precision_weight:
            precision=base.precision_aware_loss(out['xy'],target,true_xy.detach().cpu().numpy())
        else:precision=mse.detach()*0.
        return mse+precision_weight*precision,{'asinh':float(mse.detach()),'precision':float(precision.detach())}
    s,e,m,nonzero=target_parts(true_xy,math.floor(math.log10(base.registers.MAX_VALUE)),true_xy.device)
    s_loss=F.cross_entropy(out['sign'].reshape(-1,3),s.reshape(-1))
    if bool(nonzero.any()):
        e_loss=F.cross_entropy(out['exponent'][nonzero],e[nonzero])
        m_loss=F.smooth_l1_loss(out['fraction'][nonzero],m[nonzero],beta=.05)
    else:
        e_loss=out['exponent'].sum()*0.
        m_loss=out['fraction'].sum()*0.
    loss=s_loss+e_loss+m_loss
    return loss,{'sign':float(s_loss.detach()),'exponent':float(e_loss.detach()),'mantissa':float(m_loss.detach())}

def predict(out,base,arm):
    if arm=='residual':return torch.sinh(out['xy'].clamp(-1.,1.)*base.X_SCALE)
    return compose_logits(out['sign'],out['exponent'],out['fraction'],base.registers.MAX_VALUE)

def full_capture(model,tokenizer,device,prompts,max_tokens):
    """Capture mid+last layer at ALL real tokens, plus bit-identical last-four.

    Capture outputs are detached clones and cannot backpropagate to frozen model.
    """
    encoded=[]
    for p in prompts:
        ids=tokenizer(p,add_special_tokens=False)['input_ids']
        if not 4<=len(ids)<=max_tokens:raise RuntimeError('V3_BAD_TOKEN_COUNT:'+str(len(ids)))
        encoded.append(ids)
    b=len(encoded);length=max(map(len,encoded))
    pad=tokenizer.pad_token_id
    if pad is None:pad=tokenizer.eos_token_id
    if pad is None:raise RuntimeError('V3_NO_PAD_ID')
    ids=torch.full((b,length),int(pad),device=device,dtype=torch.long)
    mask=torch.zeros((b,length),device=device,dtype=torch.bool)
    for j,row in enumerate(encoded):ids[j,:len(row)]=torch.tensor(row,device=device);mask[j,:len(row)]=True
    with torch.inference_mode():
        output=model(input_ids=ids,attention_mask=mask.long(),output_hidden_states=True,use_cache=False)
        hs=output.hidden_states;layer_ids=(max(1,(len(hs)-1)//2),len(hs)-1)
        seq=torch.cat([hs[l] for l in layer_ids],dim=-1)
        loc=torch.tensor([len(x) for x in encoded],device=device)[:,None]+torch.arange(-4,0,device=device)
        idx=torch.arange(b,device=device)[:,None]
        flat=torch.stack([hs[l][idx,loc,:] for l in layer_ids],dim=1).flatten(1)
        if not bool(torch.isfinite(seq).all()) or not bool(torch.isfinite(flat).all()):
            raise RuntimeError('V3_NONFINITE_CAPTURE')
    # Inference-mode tensors cannot be saved by trainable attention's Linear.
    # Clone them AFTER leaving inference mode.
    return flat.float().cpu().clone(),seq.float().cpu().clone(),mask.cpu().clone()

def batch_capture(base,arm,model,tok,device,prompts,max_tokens):
    if arm=='attention':return full_capture(model,tok,device,prompts,max_tokens)
    return base.capture(model,tok,device,prompts,max_tokens),None,None

def pack_attention_holdouts(base,model,tok,device,parent_cache,batch_size,max_tokens):
    result={}
    for split in ('validation','test'):
        pack=parent_cache[split]
        result[split]={}
        for order,reverse in (('original',False),('reversed',True)):
            seqs=[];masks=[];flats=[]
            examples=pack['examples']
            for i in range(0,len(examples),batch_size):
                prompts=[base.prompt(e['a'],e['b'],e['operation'],reverse) for e in examples[i:i+batch_size]]
                flat,seq,mask=full_capture(model,tok,device,prompts,max_tokens)
                original=pack[order][i:i+batch_size]
                if not torch.allclose(flat,original,atol=2e-4,rtol=2e-4):
                    raise RuntimeError('V3_TOKEN_FEATURE_NOT_MATCHING_PARENT_CACHE')
                seqs.append(seq);masks.append(mask);flats.append(flat)
            width=max(t.shape[1] for t in seqs)
            result[split][order]={
                'sequence':torch.cat([F.pad(v,(0,0,0,width-v.shape[1])) for v in seqs]),
                'mask':torch.cat([F.pad(v,(0,width-v.shape[1]),value=False) for v in masks])}
    return result

def evaluate(base,parent,decoder,cache,device,arm,batch_size,attention_cache=None,details=False):
    decoder.eval();parent.eval()
    all_metrics={};rows=[]
    pack=cache
    truth=np.asarray([[e['a'],e['b']] for e in pack['examples']],dtype=np.float64)
    ops=inputs(base,pack['examples'],device)
    decoded={}
    with torch.inference_mode():
        for order in ('original','reversed'):
            guesses=[];parents=[]
            for i in range(0,len(truth),batch_size):
                z=pack[order][i:i+batch_size].to(device)
                op=ops[i:i+batch_size]
                h=parent.extract(z);p_xy=parent.xy_coord(h)
                seq=mask=None
                if arm=='attention':
                    if attention_cache is None:raise RuntimeError('V3_MISSING_HOLDOUT_TOKENS')
                    seq=attention_cache[order]['sequence'][i:i+batch_size].to(device)
                    mask=attention_cache[order]['mask'][i:i+batch_size].to(device)
                output=decoder(h,p_xy,op,seq,mask)
                guess=predict(output,base,arm).cpu().numpy().astype(np.float64)
                guesses.append(guess)
                parents.append(np.sinh(p_xy.detach().cpu().numpy().astype(np.float64)*base.X_SCALE))
            decoded[order]={'candidate':np.concatenate(guesses),'parent':np.concatenate(parents)}
        decoded['averaged']={kind:(decoded['original'][kind]+decoded['reversed'][kind])/2
                            for kind in ('candidate','parent')}
    for order in ('original','reversed','averaged'):
        for kind in ('candidate','parent'):
            pred=decoded[order][kind]
            relative=np.abs(pred-truth)/np.maximum(1.,np.abs(truth))
            hit5=relative<=.05;hit1=relative<=.01;hit01=relative<=.001
            key=order+'/'+kind
            all_metrics[key]={'operand_5pct':float(hit5.mean()),'operand_1pct':float(hit1.mean()),
                              'operand_0p1pct':float(hit01.mean()),
                              'joint_5pct':float(hit5.all(axis=1).mean()),
                              'operand_5pct_hits':int(hit5.sum()),'joint_5pct_hits':int(hit5.all(axis=1).sum()),
                              'mean_abs_relative_error':float(relative.mean()),
                              'asinh_mse':float(np.mean(((np.arcsinh(pred)-np.arcsinh(truth))/base.X_SCALE)**2))}
            if details:
                for j,ex in enumerate(pack['examples']):
                    rows.append({'orientation':order,'decoder':kind,'example_id':j,'operation':ex['operation'],
                                 'true_x':float(truth[j,0]),'true_y':float(truth[j,1]),
                                 'pred_x':float(pred[j,0]),'pred_y':float(pred[j,1]),
                                 'x_5pct':bool(hit5[j,0]),'y_5pct':bool(hit5[j,1]),
                                 'joint_5pct':bool(hit5[j].all())})
    score=all_metrics['original/candidate']['operand_5pct']+all_metrics['reversed/candidate']['operand_5pct']
    score1=all_metrics['original/candidate']['operand_1pct']+all_metrics['reversed/candidate']['operand_1pct']
    joint=all_metrics['original/candidate']['joint_5pct']+all_metrics['reversed/candidate']['joint_5pct']
    return {'metrics':all_metrics,'rank':[joint,score,score1,
            -all_metrics['original/candidate']['asinh_mse']-all_metrics['reversed/candidate']['asinh_mse']],
            'rows':rows}

def get_parent(base,args):
    root=args.parent_root.expanduser().resolve();f=root/(args.parent_checkpoint+'.pt')
    if not f.is_file():raise FileNotFoundError('V3_PARENT_NOT_FOUND:'+str(f))
    cp=torch.load(f,map_location='cpu',weights_only=True)
    cfg=cp['config']
    if cp.get('schema')!=base.SCHEMA or cp.get('backbone')!=args.backbone or cfg['model_id']!=base.MODEL_ID:
        raise RuntimeError('V3_PARENT_IDENTITY_MISMATCH')
    cache=torch.load(root/'validation-cache.pt',map_location='cpu',weights_only=True)
    if cache.get('config')!=cfg:raise RuntimeError('V3_PARENT_HOLDOUT_DRIFT')
    model=base.MathHead(cfg['input_dim'],cfg['width'],len(base.OP_IDS)).to(args.device)
    model.load_state_dict(cp['head'],strict=True)
    model.eval()
    for p in model.parameters():p.requires_grad_(False)
    return cp,cache,model,sha256(f)

def config(args,parent_config,parent_hash):
    cfg={'schema':SCHEMA,'backbone':args.backbone,'arm':args.arm,
            'parent_root':str(args.parent_root.expanduser().resolve()),
            'parent_checkpoint':args.parent_checkpoint,'parent_sha256':parent_hash,
            'parent_step':int(args._parent_step),'dataset_sha256':parent_config['dataset_sha256'],
            'model_id':parent_config['model_id'],'seed':args.seed,'batch_size':args.batch_size,
            'lr':args.lr,'hidden':args.hidden,'precision_weight':args.precision_weight,
            'max_prompt_tokens':parent_config['max_prompt_tokens'],
            'holdout_val':parent_config['val_examples'],'holdout_test':parent_config['test_examples']}
    if args.arm=='residual':
        # Binds corrected semantics to checkpoint identity. Old residual weights
        # must never be interpreted as if their parent-forward was identity.
        cfg['residual_semantics']='identity-plus-delta-v1'
    return cfg

def dump_checkpoint(base,dest,head,opt,stream,step,best,cfg):
    data={'schema':SCHEMA,'config':cfg,'head':head.state_dict(),'optimizer':opt.state_dict(),
          'stream':stream.state_dict(),'step':step,'best':best,'rng_cpu':torch.get_rng_state()}
    if torch.cuda.is_available():data['rng_cuda']=torch.cuda.get_rng_state_all()
    atomic_save(base,dest,data)

def restore(head,opt,stream,path,cfg):
    data=torch.load(path,map_location='cpu',weights_only=True)
    if data.get('schema')!=SCHEMA or data.get('config')!=cfg:raise RuntimeError('V3_RESUME_IDENTITY_MISMATCH')
    head.load_state_dict(data['head'],strict=True)
    opt.load_state_dict(data['optimizer']);stream.load_state_dict(data['stream'])
    torch.set_rng_state(data['rng_cpu'])
    if torch.cuda.is_available() and data.get('rng_cuda') is not None:torch.cuda.set_rng_state_all(data['rng_cuda'])
    return int(data['step']),data['best']

def self_test():
    torch.manual_seed(11)
    for arm in ARMS:
        model=V3Decoder(32,7,arm,6,hidden=24,token_dim=16 if arm=='attention' else None)
        h=torch.randn(10,32);xy=torch.randn(10,2).clamp(-1,1);ops=torch.arange(10)%7
        token=torch.randn(10,6,16);mask=torch.ones(10,6,dtype=torch.bool)
        pred=model(h,xy,ops,token if arm=='attention' else None,mask if arm=='attention' else None)
        class B:
            X_SCALE=14.
            class registers:MAX_VALUE=2000000
            @staticmethod
            def precision_aware_loss(x,t,n):return F.mse_loss(x,t)
        truth=torch.tensor([[135.,-263.],[1.,0.],[900.,6.],[0.,-2.],[2000000.,500.]]).repeat(2,1)
        loss,_=objective(pred,truth,B,arm)
        assert torch.isfinite(loss)
        loss.backward()
        assert all(p.grad is not None and torch.isfinite(p.grad).all() for p in model.parameters())
        values=predict(pred,B,arm)
        assert values.shape==(10,2) and torch.isfinite(values).all()
    s,e,m,_=target_parts(torch.tensor([[-123.,0.],[1.,2000000.]]),6,'cpu')
    assert s.tolist()==[[0,1],[2,2]]
    assert e.tolist()==[[2,0],[0,6]]
    assert abs(float(m[0,0])-.089905)<.002
    assert torch.equal(compose_logits(torch.tensor([[[0.,0.,10.],[10.,0.,0.]]]),
                                      torch.tensor([[[0.,10.,0.],[0.,10.,0.]]]),
                                      torch.zeros(1,2),2000000),torch.tensor([[10.,-10.]]))
    emit('altreg_decoder_v3_self_test_pass',arms=list(ARMS),backbone_loaded=False)
    return 0

def run(args):
    if args.self_test:return self_test()
    if args.arm not in ARMS:raise ValueError('V3_BAD_ARM')
    if args.steps<0 or args.additional_steps<0 or args.batch_size<1 or args.eval_every<1 or args.report_every<1:
        raise ValueError('V3_BAD_ARGUMENT')
    if args.device=='auto':args.device='cuda' if torch.cuda.is_available() else 'cpu'
    if args.device=='cuda' and not torch.cuda.is_available():raise RuntimeError('V3_CUDA_UNAVAILABLE')
    base=importlib.import_module('nanojev_altreg_independent_math_'+args.backbone+'_precision_v2')
    args.parent_root=args.parent_root or base.DEFAULT_ROOT
    args.results_root=args.results_root or (Path.home()/'NanoJev'/'runs'/f'independent_altreg_math_{args.backbone}_v3_{args.arm}')
    output=args.results_root.expanduser().resolve()
    parent_root=args.parent_root.expanduser().resolve()
    if parent_root==output or parent_root in output.parents:raise RuntimeError('V3_OUTPUT_INSIDE_PARENT')
    cp,cache,parent,parent_sha=get_parent(base,args)
    args._parent_step=int(cp['step'])
    cfg=config(args,cp['config'],parent_sha)
    torch.manual_seed(args.seed)
    if args.device=='cuda':torch.cuda.manual_seed_all(args.seed)
    token_dim=(cp['config']['input_dim']//8)*2 if args.arm=='attention' else None
    decoder=V3Decoder(cp['config']['width'],len(base.OP_IDS),args.arm,
                      math.floor(math.log10(base.registers.MAX_VALUE)),args.hidden,token_dim).to(args.device)
    optimizer=torch.optim.AdamW(decoder.parameters(),lr=args.lr)
    tasks=base.registers.build_plan(cp['config']['seed'])[1]
    reserved={(min(int(t['a']),int(t['b'])),max(int(t['a']),int(t['b'])))
              for t in tasks if t['split']!='train'}
    stream=base.Stream(args.seed+303,reserved)
    initial=0;best={'step':0,'rank':[-1.,-1.,-1.,float('-inf')]}
    if args.resume or args.eval_only:
        name='best.pt' if args.eval_only and args.eval_checkpoint=='best' else 'latest.pt'
        initial,best=restore(decoder,optimizer,stream,output/name,cfg)
    elif output.exists() and any(output.iterdir()):
        raise RuntimeError('V3_OUTPUT_NOT_EMPTY_USE_RESUME')
    attention_cache=None;backbone=None;tokenizer=None
    if args.arm=='attention' and (output/'attention-cache.pt').is_file():
        pack=torch.load(output/'attention-cache.pt',map_location='cpu',weights_only=True)
        if pack['config']!=cfg:raise RuntimeError('V3_ATTENTION_CACHE_IDENTITY_DRIFT')
        attention_cache=pack['captures']
    if not args.eval_only:
        class C:pass
        c=C();c.device=args.device;c.dtype=args.dtype;c.allow_model_download=args.allow_model_download
        _,backbone,tokenizer,model_device,_=base.cassette.load_source(args.backbone,c)
        if str(model_device)!=args.device:raise RuntimeError('V3_FROZEN_DEVICE_DRIFT')
        if any(p.requires_grad for p in backbone.parameters()):raise RuntimeError('V3_BACKBONE_NOT_FROZEN')
        if args.arm=='attention' and attention_cache is None:
            attention_cache=pack_attention_holdouts(base,backbone,tokenizer,args.device,cache,args.batch_size,
                                                    cp['config']['max_prompt_tokens'])
            output.mkdir(parents=True,exist_ok=True)
            atomic_save(base,output/'attention-cache.pt',{'config':cfg,'captures':attention_cache})
    if args.arm=='attention' and attention_cache is None:raise RuntimeError('V3_ATTENTION_CACHE_MISSING')
    # Loading the frozen HF model may consume RNG, so restore AFTER loading it.
    # Resume therefore recreates the exact next training minibatch and dropout RNG.
    if args.resume and not args.eval_only:
        initial,best=restore(decoder,optimizer,stream,output/'latest.pt',cfg)
    if args.eval_only:
        for split in ('validation','test') if args.eval_split=='both' else (args.eval_split,):
            stats=evaluate(base,parent,decoder,cache[split],args.device,args.arm,args.batch_size,
                           attention_cache[split] if attention_cache else None,details=True)
            emit('altreg_decoder_v3_evaluation',backbone=args.backbone,arm=args.arm,
                 split=split,step=initial,metrics=stats['metrics'])
            if args.save_eval:
                import csv
                output.mkdir(parents=True,exist_ok=True)
                with (output/f'evaluation-{args.eval_checkpoint}-{split}.csv').open('w',newline='',encoding='utf-8') as fh:
                    writer=csv.DictWriter(fh,fieldnames=list(stats['rows'][0]));writer.writeheader();writer.writerows(stats['rows'])
        return 0
    target=initial+(args.additional_steps if args.resume else args.steps)
    if not args.resume:
        initial_metric=evaluate(base,parent,decoder,cache['validation'],args.device,args.arm,args.batch_size,
                                attention_cache['validation'] if attention_cache else None)
        output.mkdir(parents=True,exist_ok=True)
        base.write_json(output/'fork.json',{'config':cfg,'parent_sha256':parent_sha,
                    'note':'New decoder only; original Qwen/Pythia/TinyStories and parent weights remain frozen.'})
        dump_checkpoint(base,output/'latest.pt',decoder,optimizer,stream,initial,best,cfg)
        emit('altreg_decoder_v3_baseline',backbone=args.backbone,arm=args.arm,validation=initial_metric['metrics'])
    for step in range(initial+1,target+1):
        examples=[stream.get() for _ in range(args.batch_size)]
        prompts=[base.prompt(e['a'],e['b'],e['operation'],((step-1)*args.batch_size+j)%2==1)
                 for j,e in enumerate(examples)]
        z,seq,mask=batch_capture(base,args.arm,backbone,tokenizer,args.device,prompts,cfg['max_prompt_tokens'])
        z=z.to(args.device)
        op=inputs(base,examples,args.device)
        gold=torch.tensor([[ex['a'],ex['b']] for ex in examples],dtype=torch.float32,device=args.device)
        with torch.no_grad():
            h=parent.extract(z);pxy=parent.xy_coord(h)
        prediction=decoder(h,pxy,op,seq.to(args.device) if seq is not None else None,
                           mask.to(args.device) if mask is not None else None)
        loss,parts=objective(prediction,gold,base,args.arm,args.precision_weight)
        if not torch.isfinite(loss):raise RuntimeError('V3_NONFINITE_LOSS')
        optimizer.zero_grad(set_to_none=True);loss.backward()
        if any(p.grad is not None and not bool(torch.isfinite(p.grad).all()) for p in decoder.parameters()):
            raise RuntimeError('V3_NONFINITE_GRADIENT')
        nn.utils.clip_grad_norm_(decoder.parameters(),1.)
        optimizer.step()
        if step%args.report_every==0:
            emit('altreg_decoder_v3_progress',backbone=args.backbone,arm=args.arm,
                 step=step,target_step=target,loss=float(loss.detach()),loss_components=parts,
                 examples_seen=stream.draws)
        if step%args.eval_every==0 or step==target:
            stats=evaluate(base,parent,decoder,cache['validation'],args.device,args.arm,args.batch_size,
                           attention_cache['validation'] if attention_cache else None)
            score=stats['rank'];improved=score>best['rank']
            if improved:best={'step':step,'rank':score}
            snapshot={'step':step,'rank':score,'validation':stats['metrics'],'best':best}
            base.write_json(output/('validation-step-%08d.json'%step),snapshot)
            dump_checkpoint(base,output/'latest.pt',decoder,optimizer,stream,step,best,cfg)
            if improved:atomic_save(base,output/'best.pt',torch.load(output/'latest.pt',map_location='cpu',weights_only=True))
            emit('altreg_decoder_v3_validation',backbone=args.backbone,arm=args.arm,
                 step=step,rank=score,best=best,metrics=stats['metrics'])
    # The untouched test split is not used for checkpoint selection.
    if args.evaluate_test_at_end:
        result=evaluate(base,parent,decoder,cache['test'],args.device,args.arm,args.batch_size,
                        attention_cache['test'] if attention_cache else None)
        base.write_json(output/'test-last.json',{'step':target,'test':result['metrics']})
    emit('altreg_decoder_v3_complete',backbone=args.backbone,arm=args.arm,
         step=target,best=best,output=str(output))
    return 0

def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--self-test',action='store_true')
    p.add_argument('--backbone',choices=BACKBONES,default='qwen')
    p.add_argument('--arm',choices=ARMS,default='factorized')
    p.add_argument('--parent-root',type=Path)
    p.add_argument('--parent-checkpoint',choices=('best-precision','best-asinh','latest','fork'),default='best-precision')
    p.add_argument('--results-root',type=Path)
    p.add_argument('--steps',type=int,default=2000)
    p.add_argument('--additional-steps',type=int,default=1000)
    p.add_argument('--batch-size',type=int,default=16)
    p.add_argument('--hidden',type=int,default=192)
    p.add_argument('--lr',type=float,default=1e-4)
    p.add_argument('--precision-weight',type=float,default=.01)
    p.add_argument('--eval-every',type=int,default=100)
    p.add_argument('--report-every',type=int,default=50)
    p.add_argument('--seed',type=int,default=20261014)
    p.add_argument('--device',choices=('cpu','cuda','auto'),default='cuda')
    p.add_argument('--dtype',choices=('float16','float32','bfloat16'),default='float16')
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--eval-only',action='store_true')
    p.add_argument('--eval-checkpoint',choices=('best','latest'),default='best')
    p.add_argument('--eval-split',choices=('validation','test','both'),default='validation')
    p.add_argument('--save-eval',action='store_true')
    p.add_argument('--evaluate-test-at-end',action='store_true',help='Use only for final evaluation, after selection.')
    return p

if __name__=='__main__':raise SystemExit(run(parser().parse_args()))
