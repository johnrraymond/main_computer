#!/usr/bin/env python3
"""Seven independent RESULT representations trained on one frozen-Qwen capture.

Additive experiment: never changes the source checkpoint, benchmark, or deployed
RESULT. Every head sees the same pairs, replay samples, captured features and
operation-probe prediction. The reference R0 executes on frozen decoded X/Y.

Source checkpoint: nanojev.register-online-hierarchy-buffered.v1 best.pt,
trained with --constraint-stage continuity. This version intentionally does
not attempt to deserialize the differently structured v1.2 result checkpoint.
"""
from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
import math
import os
from pathlib import Path
import random
import time

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

import nanojev_register_online_neural as previous
import nanojev_register_online_hierarchy_buffered as buffered

v3 = previous.v3
constrained = buffered.c
emit = previous.emit
atomic_json = previous.atomic_json
SCHEMA = 'nanojev.parallel-result-register-search.v1'
NAMES = ('continuous', 'scientific', 'digits', 'place', 'ordinal', 'bits', 'residual')
OP_CLASSES = tuple(t[0] for t in v3.OPERATIONS if t[0] not in v3.HELD_OUT_OPERATIONS)
ALL_OP_CLASSES = tuple(t[0] for t in v3.OPERATIONS)
OP_IDS = {n: k for k, n in enumerate(OP_CLASSES)}
BOOL_OPS = frozenset(('written_greater', 'conditional_gt', 'conditional_abs'))
N_DIGITS = 15
N_BITS = 48
MAX_RESULT = 20_000_000_000_000  # max from 2M+2M squared is 16e12
SCALE = math.asinh(MAX_RESULT)
INPUT_WIDTH = buffered.h.INPUT_WIDTH
DEFAULT_RESULT_ROOT = Path.home()/'NanoJev'/'runs'/'parallel_result_register_search_v1'
DEFAULT_SOURCE = Path.home()/'NanoJev'/'runs'/'register_online_hierarchy_buffered_v1'/'best.pt'


def result_value(a: int, b: int, op: str):
    """Exact execution semantics for the existing v3 prompt operation catalog."""
    if op in ('sym_add', 'written_add'): return a+b
    if op in ('sym_sub', 'written_sub', 'algebra_left', 'algebra_balance'): return a-b
    if op in ('sym_reverse_sub', 'written_difference', 'algebra_right'): return b-a
    if op in ('sym_multiply', 'written_product'): return a*b
    if op == 'sym_absdiff': return abs(a-b)
    if op == 'sym_max': return max(a,b)
    if op in ('written_greater', 'conditional_gt'): return int(a>b)
    if op == 'written_average': return (a+b)/2
    if op == 'algebra_difference': return a+b
    if op == 'conditional_min': return min(a,b)
    if op == 'conditional_positive': return b if a>0 else a
    if op == 'conditional_abs': return int(abs(a)>abs(b))
    if op == 'multistep_double': return 2*a-b
    if op == 'multistep_combine': return 2*(a+b)
    if op == 'multistep_square': return (a+b)**2
    if op == 'multistep_difference': return 2*a-b
    raise ValueError(f'PARALLEL_UNSUPPORTED_OPERATION:{op}')


def truth_for(rows):
    values = [result_value(int(row['a']), int(row['b']), str(row['operation'])) for row in rows]
    is_bool = [str(row['operation']) in BOOL_OPS for row in rows]
    if any(abs(float(v)) > MAX_RESULT for v in values):
        raise ValueError('PARALLEL_RESULT_EXCEEDS_SUPPORTED_RANGE')
    return np.asarray(values, dtype=np.float64), np.asarray(is_bool, dtype=bool)


def signed_encode(values):
    return np.arcsinh(np.asarray(values, dtype=np.float64)) / SCALE


def signed_decode(values):
    a = np.clip(np.asarray(values, dtype=np.float64), -1, 1)
    return np.rint(np.sinh(a*SCALE))


def scientific_targets(values):
    v = np.abs(np.asarray(values, dtype=np.float64))
    nonzero = v > 0
    exp = np.zeros(len(v), dtype=np.int64)
    exp[nonzero] = np.floor(np.log10(v[nonzero])+1e-10).astype(np.int64)
    exp = np.clip(exp, 0, 13)
    fraction = np.zeros(len(v), dtype=np.float32)
    fraction[nonzero] = np.log10(v[nonzero])-exp[nonzero]
    return exp, fraction


def decimal_targets(values):
    integers = np.rint(np.abs(np.asarray(values, dtype=np.float64))).astype(np.int64)
    # Class 10 is PAD, classes 0..9 are decimal digits.
    left = np.full((len(integers), N_DIGITS), 10, dtype=np.int64)
    place = np.zeros((len(integers), N_DIGITS), dtype=np.int64)
    length = np.zeros(len(integers), dtype=np.int64)
    for i, n in enumerate(integers):
        digits = [int(c) for c in str(int(n))]
        if len(digits)>N_DIGITS: raise ValueError('PARALLEL_TOO_MANY_DIGITS')
        left[i,:len(digits)] = digits
        length[i] = len(digits)-1
        for p in range(N_DIGITS): place[i,p] = int((int(n)//(10**p))%10)
    return left, place, length


def ordinal_targets(values):
    # Ordered logarithmic boundaries: 10^0 ... 10^13, 14 supervised bits.
    a = np.abs(np.asarray(values, dtype=np.float64))
    exps, frac = scientific_targets(values)
    cuts = np.power(10.0, np.arange(14,dtype=np.float64))
    return (a[:,None] >= cuts[None,:]).astype(np.float32), frac


def bit_targets(values):
    x = np.rint(np.abs(np.asarray(values, dtype=np.float64))).astype(np.int64)
    return ((x[:,None] >> np.arange(N_BITS,dtype=np.int64)) & 1).astype(np.float32)


def features_with_op(features, op_ids, reference):
    # Discrete operation predictions are detached; no gold operation is passed.
    one_hot = F.one_hot(op_ids.detach(),len(OP_CLASSES)).float()
    ref = torch.asinh(reference.detach().float())/SCALE
    return torch.cat((features.float().detach(),one_hot,ref.unsqueeze(-1)),dim=1)


class ResultHead(nn.Module):
    """Independent common-size hidden readout; representation-specific output."""
    def __init__(self, name, width):
        super().__init__()
        if name not in NAMES: raise ValueError(name)
        self.name=name
        dim={'continuous':1,'scientific':16,'digits':N_DIGITS*11,
             'place':N_DIGITS*10+N_DIGITS,'ordinal':15,'bits':N_BITS,
             'residual':1}[name]
        self.body=nn.Sequential(nn.LayerNorm(INPUT_WIDTH+len(OP_CLASSES)+1,elementwise_affine=False),
                                nn.Linear(INPUT_WIDTH+len(OP_CLASSES)+1,width),nn.GELU(),
                                nn.Linear(width,width),nn.GELU())
        self.num=nn.Linear(width,dim)
        self.sign=nn.Linear(width,1)
        self.zero=nn.Linear(width,1)
        self.boolean=nn.Linear(width,1)

    def forward(self,x):
        latent=self.body(x)
        return self.num(latent).float(), self.sign(latent).flatten().float(), \
            self.zero(latent).flatten().float(), self.boolean(latent).flatten().float()

    def objective(self, output, values, bool_mask, reference, exact_values=None):
        raw,sign,zero,boolean=output
        numbers=~bool_mask
        true_float=values.float()
        bool_loss=F.binary_cross_entropy_with_logits(boolean[bool_mask],true_float[bool_mask]) if bool_mask.any() else raw.sum()*0
        if not numbers.any(): return bool_loss,{'numeric':0.,'boolean':float(bool_loss.detach())}
        v=true_float[numbers]
        exact=np.asarray(exact_values,dtype=np.float64)[numbers.detach().cpu().numpy()] if exact_values is not None else v.detach().cpu().numpy().astype(np.float64)
        magnitude=v.abs()
        nonzero=magnitude>0
        sign_target=(v>0).float()
        sign_loss=F.binary_cross_entropy_with_logits(sign[numbers][nonzero],sign_target[nonzero]) if nonzero.any() else raw.sum()*0
        zero_loss=F.binary_cross_entropy_with_logits(zero[numbers],(~nonzero).float())
        y=raw[numbers]
        if self.name=='continuous':
            numeric=F.smooth_l1_loss(y[:,0], torch.as_tensor(signed_encode(exact),dtype=torch.float32,device=v.device),beta=.03)
        elif self.name=='residual':
            tgt=torch.as_tensor(signed_encode(exact),dtype=torch.float32,device=v.device)
            ref=torch.asinh(reference[numbers].detach().float())/SCALE
            numeric=F.smooth_l1_loss(ref+y[:,0],tgt,beta=.03)
        elif self.name=='scientific':
            exp,frac=scientific_targets(exact)
            e=torch.as_tensor(exp,device=v.device,dtype=torch.long)
            u=torch.as_tensor(frac,device=v.device,dtype=torch.float32)
            numeric=F.cross_entropy(y[:,:14],e)+F.smooth_l1_loss(y[:,14][nonzero],u[nonzero],beta=.06) if nonzero.any() else F.cross_entropy(y[:,:14],e)
        elif self.name=='digits':
            left,_,_=decimal_targets(exact)
            target=torch.as_tensor(left,device=v.device,dtype=torch.long)
            numeric=F.cross_entropy(y.reshape(-1,11),target.reshape(-1))
        elif self.name=='place':
            _,place,length=decimal_targets(exact)
            p=torch.as_tensor(place,device=v.device,dtype=torch.long)
            l=torch.as_tensor(length,device=v.device,dtype=torch.long)
            numeric=F.cross_entropy(y[:,:N_DIGITS*10].reshape(-1,10),p.reshape(-1))+F.cross_entropy(y[:,N_DIGITS*10:],l)
        elif self.name=='ordinal':
            flags,frac=ordinal_targets(exact)
            b=torch.as_tensor(flags,device=v.device,dtype=torch.float32)
            u=torch.as_tensor(frac,device=v.device,dtype=torch.float32)
            numeric=F.binary_cross_entropy_with_logits(y[:,:14],b)+F.smooth_l1_loss(y[:,14][nonzero],u[nonzero],beta=.06) if nonzero.any() else F.binary_cross_entropy_with_logits(y[:,:14],b)
        elif self.name=='bits':
            bit=torch.as_tensor(bit_targets(exact),device=v.device,dtype=torch.float32)
            numeric=F.binary_cross_entropy_with_logits(y,bit)
        else: raise AssertionError(self.name)
        loss=numeric+.12*sign_loss+.12*zero_loss+.2*bool_loss
        return loss,{'numeric':float(numeric.detach()),'boolean':float(bool_loss.detach())}

    @torch.no_grad()
    def infer(self, output, reference, bool_mask):
        raw,sign,zero,boolean=output
        y=raw.detach().cpu().numpy()
        sign_pred=np.where(sign.detach().cpu().numpy()>=0,1.,-1.)
        zero_pred=zero.detach().cpu().numpy()>=0
        if self.name=='continuous':
            numbers=signed_decode(y[:,0])
        elif self.name=='residual':
            source=np.asarray(reference.detach().cpu().numpy(),dtype=np.float64)
            numbers=signed_decode(signed_encode(source)+y[:,0])
        elif self.name=='scientific':
            exps=y[:,:14].argmax(1)
            numbers=sign_pred*np.rint(10.**np.clip(exps+np.clip(y[:,14],0.,.999999),0,13.99))
        elif self.name=='digits':
            digits=y.reshape(-1,N_DIGITS,11).argmax(2)
            nums=[]
            for row in digits:
                seq=[]
                for digit in row:
                    if digit==10: break
                    seq.append(int(digit))
                nums.append(int(''.join(map(str,seq))) if seq else 0)
            numbers=sign_pred*np.asarray(nums,dtype=np.float64)
        elif self.name=='place':
            digits=y[:,:N_DIGITS*10].reshape(-1,N_DIGITS,10).argmax(2)
            length=y[:,N_DIGITS*10:].argmax(1)+1
            numbers=[]
            for i in range(len(y)):
                numbers.append(sum(int(digits[i,k])*10**k for k in range(int(length[i]))))
            numbers=sign_pred*np.asarray(numbers,dtype=np.float64)
        elif self.name=='ordinal':
            # Monotone prefix: stop at the first boundary with logit <= 0.
            flag=y[:,:14]>0
            exp=np.cumprod(flag.astype(np.int64),axis=1).sum(axis=1)-1
            exp=np.maximum(exp,0)
            numbers=sign_pred*np.rint(10.**np.clip(exp+np.clip(y[:,14],0.,.999999),0,13.99))
        elif self.name=='bits':
            digits=(y>0).astype(np.int64)
            numbers=sign_pred*(digits*np.power(2.,np.arange(N_BITS))).sum(axis=1)
        else: raise AssertionError(self.name)
        numbers=np.asarray(numbers,dtype=np.float64)
        numbers=np.where(zero_pred,0.,numbers)
        numbers=np.clip(numbers,-MAX_RESULT,MAX_RESULT)
        bb=(boolean.detach().cpu().numpy()>=0).astype(np.float64)
        return np.where(np.asarray(bool_mask,dtype=bool),bb,numbers)


class SharedOperationProbe(nn.Module):
    def __init__(self):
        super().__init__()
        self.net=nn.Sequential(nn.LayerNorm(INPUT_WIDTH,elementwise_affine=False),nn.Linear(INPUT_WIDTH,len(OP_CLASSES)))
    def forward(self,x): return self.net(x.float().detach())


class Replay:
    def __init__(self,capacity,seed):
        self.capacity=int(capacity);self.rng=random.Random(seed)
        self.rows=deque(maxlen=self.capacity)
    def add(self,features,rows,source_xy):
        for feature,row,pair in zip(features.detach().to('cpu',dtype=torch.float16),rows,source_xy):
            self.rows.append((feature.clone(),int(row['a']),int(row['b']),str(row['operation']),
                              int(pair[0]),int(pair[1])))
    def sample(self,n):
        if not self.rows:raise RuntimeError('PARALLEL_EMPTY_REPLAY')
        return [self.rows[i] for i in self.rng.choices(range(len(self.rows)),k=n)]
    def state_dict(self): return {'capacity':self.capacity,'rng':self.rng.getstate(),'rows':list(self.rows)}
    def load_state_dict(self,s):
        if s['capacity']!=self.capacity:raise RuntimeError('PARALLEL_REPLAY_CAPACITY_DRIFT')
        self.rng.setstate(s['rng']);self.rows=deque(s['rows'],maxlen=self.capacity)


def source_factory(ckpt,identity,device):
    path=Path(ckpt).expanduser().resolve()
    ck=torch.load(path,map_location='cpu',weights_only=True)
    if ck.get('schema')!=buffered.SCHEMA:
        raise RuntimeError('PARALLEL_SOURCE_REQUIRES_BUFFERED_V1_CHECKPOINT')
    if ck.get('backbone_identity')!=identity:
        raise RuntimeError('PARALLEL_SOURCE_BACKBONE_IDENTITY_DRIFT')
    cfg=ck['config']
    net=constrained.model_factory(cfg.get('constraint_stage','continuity'),
                                  cfg.get('rank',8),cfg.get('residual_scale',.25))
    net.load_state_dict(ck['model'],strict=True)
    net.to(device).eval()
    for p in net.parameters():p.requires_grad_(False)
    return net,hashlib.sha256(path.read_bytes()).hexdigest()


@torch.inference_mode()
def source_decode(source,features):
    raw=source(features.float()).detach().cpu().numpy()
    return constrained.decode(raw).astype(np.int64)


def reference_results(pairs,operations):
    return np.asarray([result_value(int(pair[0]),int(pair[1]),op)
                      for pair,op in zip(pairs,operations)],dtype=np.float64)


def exact_or_relative_hits(pred,true,is_bool,tolerance=.05):
    pred=np.asarray(pred,dtype=np.float64)
    true=np.asarray(true,dtype=np.float64)
    mask=np.asarray(is_bool,dtype=bool)
    return np.where(mask| (true==0), pred==true, np.abs(pred-true) <= tolerance*np.abs(true))


def diagnostic(pred,true,is_bool):
    p=np.asarray(pred,dtype=np.float64);t=np.asarray(true,dtype=np.float64)
    b=np.asarray(is_bool,dtype=bool);n=~b
    hit=exact_or_relative_hits(p,t,b)
    out={'rows':len(t),'overall_5pct':float(hit.mean()),
         'numeric_rows':int(n.sum()),'numeric_5pct':float(hit[n].mean()) if n.any() else None,
         'boolean_rows':int(b.sum()),'boolean_exact':float(hit[b].mean()) if b.any() else None,
         'exact':float((p==t).mean()),
         'numeric_asinh_mae':float(np.abs(signed_encode(p[n])-signed_encode(t[n])).mean()) if n.any() else None,
         'numeric_relative_mae':float((np.abs(p[n]-t[n])/np.maximum(1.,np.abs(t[n]))).mean()) if n.any() else None}
    for threshold in (.01,.02,.10,.20,.50):
        out[f'numeric_{int(threshold*100)}pct']=float(exact_or_relative_hits(p[n],t[n],np.zeros(int(n.sum()),dtype=bool),threshold).mean()) if n.any() else None
    return out


@torch.inference_mode()
def evaluate(models,op_probe,source,eval_rows,device,batch_size=64):
    features,xy,ops,families=eval_rows
    correct_truth=np.asarray([result_value(int(a),int(b),op) for (a,b),op in zip(xy,ops)],dtype=np.float64)
    bool_mask=np.asarray([op in BOOL_OPS for op in ops],dtype=bool)
    names=('source',)+NAMES
    preds={k:[] for k in names}; op_hits=[]; source_operand_hits=[]
    for start in range(0,len(features),batch_size):
        sl=slice(start,start+batch_size)
        z=torch.as_tensor(features[sl],dtype=torch.float32,device=device)
        pred_ops=op_probe(z).argmax(1)
        op_strs=[OP_CLASSES[int(i)] for i in pred_ops.cpu().numpy()]
        op_hits.extend(a==b for a,b in zip(op_strs,ops[sl]))
        operands=source_decode(source,z)
        source_operand_hits.extend(previous.correctness(operands,xy[sl]).all(axis=1).tolist())
        reference=reference_results(operands,op_strs)
        preds['source'].extend(reference.tolist())
        ref=torch.as_tensor(reference,dtype=torch.float32,device=device)
        design=features_with_op(z,pred_ops,ref)
        for name in NAMES:
            models[name].eval()
            outputs=models[name](design)
            preds[name].extend(models[name].infer(outputs,ref,bool_mask[sl]).tolist())
    predict={k:np.asarray(v,dtype=np.float64) for k,v in preds.items()}
    hits={k:exact_or_relative_hits(v,correct_truth,bool_mask) for k,v in predict.items()}
    reports={k:diagnostic(v,correct_truth,bool_mask) for k,v in predict.items()}
    result_matrix=np.stack([hits[k] for k in names],axis=1)
    oracle=np.any(result_matrix,axis=1)
    n=~bool_mask
    opportunity=~hits['source']
    rescued={k:int((hits[k]&opportunity).sum()) for k in NAMES}
    damaged={k:int((~hits[k]&hits['source']).sum()) for k in NAMES}
    overlap={k:{other:int((hits[k]&hits[other]).sum()) for other in names} for k in names}
    by_family={}
    for family in sorted(set(families)):
        ix=np.asarray([x==family for x in families])
        by_family[family]={k:float(hits[k][ix].mean()) for k in names}
    examples=[]
    for i in range(min(12,len(xy))):
        examples.append({'xy':[int(v) for v in xy[i]],'operation':ops[i],
                         'true_result':float(correct_truth[i]),
                         'candidates':{k:float(predict[k][i]) for k in names}})
    return {'rows':len(xy),'operation_accuracy':float(np.mean(op_hits)),
            'source_xy_joint_5pct':float(np.mean(source_operand_hits)),
            'registers':reports,'oracle_any_5pct':float(oracle.mean()),
            'oracle_numeric_5pct':float(oracle[n].mean()) if n.any() else None,
            'overlap_correct_counts':overlap,'rescued_source_errors':rescued,
            'damaged_source_successes':damaged,'by_family':by_family,
            'examples':examples},result_matrix


def config_for(args,manifest,source_hash,identity):
    return {'schema':SCHEMA,'benchmark_sha256':manifest['tasks_sha256'],
            'source_sha256':source_hash,'backbone_identity':identity,
            'width':args.width,'lr':args.lr,'weight_decay':args.weight_decay,
            'batch_size':args.batch_size,'replay_batch':args.replay_batch,
            'replay_capacity':args.replay_capacity,'replay_updates':args.replay_updates,
            'seed':args.seed,'val_examples':args.val_examples,
            'max_prompt_tokens':args.max_prompt_tokens,'heads':list(NAMES)}


def atomic_checkpoint(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.partial')
    torch.save(obj,tmp)
    os.replace(tmp,path)


def pack_state(step,cfg,networks,optimizers,op_probe,op_opt,stream,replay,best):
    state={'schema':SCHEMA,'step':step,'config':cfg,
           'models':{k:net.state_dict() for k,net in networks.items()},
           'optimizers':{k:o.state_dict() for k,o in optimizers.items()},
           'operation_probe':op_probe.state_dict(),'operation_optimizer':op_opt.state_dict(),
           'stream':stream.state_dict(),'replay':replay.state_dict(),
           'best':best,'torch_rng':torch.get_rng_state()}
    if torch.cuda.is_available():state['cuda_rng']=torch.cuda.get_rng_state_all()
    return state


def restore_state(path,cfg,networks,optimizers,op_probe,op_opt,stream,replay):
    ck=torch.load(path,map_location='cpu',weights_only=True)
    if ck.get('schema')!=SCHEMA or ck.get('config')!=cfg:
        raise RuntimeError('PARALLEL_RESUME_CONFIG_OR_SOURCE_DRIFT')
    for k in NAMES:
        networks[k].load_state_dict(ck['models'][k]);optimizers[k].load_state_dict(ck['optimizers'][k])
    op_probe.load_state_dict(ck['operation_probe']);op_opt.load_state_dict(ck['operation_optimizer'])
    stream.load_state_dict(ck['stream']);replay.load_state_dict(ck['replay'])
    torch.set_rng_state(ck['torch_rng'])
    if torch.cuda.is_available() and 'cuda_rng' in ck:torch.cuda.set_rng_state_all(ck['cuda_rng'])
    return int(ck['step']),dict(ck['best'])


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--self-test',action='store_true')
    p.add_argument('--eval-only',action='store_true')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--v3-root',type=Path,default=previous.DEFAULT_V3)
    p.add_argument('--source-checkpoint',type=Path,default=DEFAULT_SOURCE)
    p.add_argument('--results-root',type=Path,default=DEFAULT_RESULT_ROOT)
    p.add_argument('--steps',type=int,default=3000)
    p.add_argument('--batch-size',type=int,default=16)
    p.add_argument('--width',type=int,default=128)
    p.add_argument('--replay-batch',type=int,default=32)
    p.add_argument('--replay-capacity',type=int,default=4096)
    p.add_argument('--replay-updates',type=int,default=2)
    p.add_argument('--lr',type=float,default=.0003)
    p.add_argument('--weight-decay',type=float,default=.01)
    p.add_argument('--seed',type=int,default=20261013)
    p.add_argument('--val-examples',type=int,default=512)
    p.add_argument('--eval-every',type=int,default=100)
    p.add_argument('--checkpoint-every',type=int,default=100)
    p.add_argument('--report-every',type=int,default=50)
    p.add_argument('--max-prompt-tokens',type=int,default=256)
    p.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    p.add_argument('--dtype',choices=('float16','bfloat16','float32'),default='float16')
    p.add_argument('--threads',type=int,default=2)
    p.add_argument('--allow-model-download',action='store_true')
    return p


def train_step(networks,optimizers,op_probe,op_opt,rows,device):
    features=torch.stack([r[0] for r in rows]).to(device,dtype=torch.float32)
    xy=np.asarray([[r[1],r[2]] for r in rows],dtype=np.int64)
    ops=[r[3] for r in rows]
    frozen_xy=np.asarray([[r[4],r[5]] for r in rows],dtype=np.int64)
    truth, bool_mask=truth_for([{'a':r[1],'b':r[2],'operation':r[3]} for r in rows])
    target=torch.as_tensor(truth,device=device,dtype=torch.float32)
    flags=torch.as_tensor(bool_mask,device=device,dtype=torch.bool)
    op_gold=torch.as_tensor([OP_IDS[op] for op in ops],device=device,dtype=torch.long)
    op_loss=F.cross_entropy(op_probe(features),op_gold)
    op_opt.zero_grad(set_to_none=True);op_loss.backward()
    nn.utils.clip_grad_norm_(op_probe.parameters(),1.);op_opt.step()
    with torch.no_grad(): predicted_ops=op_probe(features).argmax(1)
    predicted_names=[OP_CLASSES[int(i)] for i in predicted_ops.cpu().numpy()]
    reference=torch.as_tensor(reference_results(frozen_xy,predicted_names),device=device,dtype=torch.float32)
    design=features_with_op(features,predicted_ops,reference)
    losses={}
    for k,head in networks.items():
        head.train()
        out=head(design)
        loss,_=head.objective(out,target,flags,reference,exact_values=truth)
        optimizers[k].zero_grad(set_to_none=True)
        loss.backward()
        nn.utils.clip_grad_norm_(head.parameters(),1.)
        optimizers[k].step()
        losses[k]=float(loss.detach())
    return float(op_loss.detach()),losses


def execute(args):
    if args.self_test:return self_test()
    if args.steps<1 or args.batch_size<1 or args.replay_batch<1 or args.replay_capacity<1 or args.replay_updates<1 or args.width<8:
        raise ValueError('PARALLEL_INVALID_TRAINING_ARGUMENTS')
    if any(x<1 for x in (args.eval_every,args.report_every,args.checkpoint_every,args.val_examples)):
        raise ValueError('PARALLEL_INVALID_FREQUENCY_OR_VALIDATION_SIZE')
    if args.threads>0:torch.set_num_threads(args.threads)
    if args.device=='cuda' and not torch.cuda.is_available():raise RuntimeError('PARALLEL_CUDA_UNAVAILABLE')
    dev=torch.device(args.device)
    source_root=args.v3_root.expanduser().resolve()
    manifest,tasks=previous.read_benchmark(source_root)
    identity=json.loads((source_root/'model_identity.json').read_text(encoding='utf-8'))
    source,source_sha=source_factory(args.source_checkpoint,identity,dev)
    cfg=config_for(args,manifest,source_sha,identity)
    root=args.results_root.expanduser().resolve()
    if root==source_root or source_root in root.parents:raise RuntimeError('PARALLEL_CANNOT_WRITE_INTO_BENCHMARK')
    if root==args.source_checkpoint.expanduser().resolve().parent:raise RuntimeError('PARALLEL_CANNOT_WRITE_INTO_SOURCE')
    root.mkdir(parents=True,exist_ok=True)
    config_path=root/'run_config.json'
    if config_path.is_file():
        if json.loads(config_path.read_text(encoding='utf-8'))!=cfg:raise RuntimeError('PARALLEL_EXISTING_RUN_CONFIG_DRIFT')
    else:
        if any(root.iterdir()):raise RuntimeError('PARALLEL_NONEMPTY_RESULTS_ROOT')
        atomic_json(config_path,cfg)
    torch.manual_seed(args.seed)
    networks={k:ResultHead(k,args.width).to(dev) for k in NAMES}
    optimizers={k:torch.optim.AdamW(v.parameters(),lr=args.lr,weight_decay=args.weight_decay) for k,v in networks.items()}
    op_probe=SharedOperationProbe().to(dev)
    op_opt=torch.optim.AdamW(op_probe.parameters(),lr=args.lr,weight_decay=args.weight_decay)
    stream=previous.FreshPairs(args.seed,previous.reserved_pairs(tasks))
    replay=Replay(args.replay_capacity,args.seed+71)
    step=0;best={k:{'step':0,'numeric_5pct':-1.,'overall_5pct':-1.} for k in NAMES}
    global_latest=root/'latest.pt'
    if args.resume:
        if not global_latest.is_file():raise RuntimeError('PARALLEL_RESUME_REQUIRES_LATEST_CHECKPOINT')
        step,best=restore_state(global_latest,cfg,networks,optimizers,op_probe,op_opt,stream,replay)
        emit('parallel_resumed',step=step)
    elif global_latest.exists() and not args.eval_only:
        raise RuntimeError('PARALLEL_RUN_EXISTS_USE_--resume')
    validation=previous.load_eval(source_root,manifest,tasks,'val',args.val_examples)
    if args.eval_only:
        if not global_latest.exists():raise RuntimeError('PARALLEL_EVAL_REQUIRES_LATEST')
        # Strictly matched step across all seven readouts and operation probe.
        step,best=restore_state(global_latest,cfg,networks,optimizers,op_probe,op_opt,stream,replay)
        emit('parallel_evaluation_checkpoint',step=step,selection='aligned_latest',best_head_steps=best)
        for split in ('val','test','control'):
            ev=previous.load_eval(source_root,manifest,tasks,split)
            report,_=evaluate(networks,op_probe,source,ev,dev)
            emit('parallel_final_evaluation',split=split,step=step,**report)
        return 0
    import nanojev_numerate_spine_cassette_smoke as cassette
    _,frozen,tokenizer,model_device,model_info=cassette.load_source('qwen',args)
    if (model_info.get('resolved_commit')!=identity['model_info'].get('resolved_commit') or
        model_info.get('source')!=identity['model_info'].get('source') or
        str(model_info.get('dtype'))!=str(identity['model_info'].get('dtype'))):
        raise RuntimeError('PARALLEL_QWEN_BACKBONE_IDENTITY_DRIFT')
    frozen.eval()
    previous.verify_capture_alignment(torch,frozen,tokenizer,model_device,source_root,tasks,args.max_prompt_tokens)
    emit('parallel_initialized',step=step,source_sha256=source_sha,heads=list(NAMES),
         head_parameters={k:sum(p.numel() for p in v.parameters()) for k,v in networks.items()},
         frozen_qwen_trainable_parameters=0,operation_probe_classes=len(OP_CLASSES),
         reference_source_schema=buffered.SCHEMA,reference_baseline='buffered hierarchy + operation probe')
    start=time.monotonic();capture_time=update_time=0.;losses={k:[] for k in NAMES}
    for k in range(step+1,args.steps+1):
        t=time.monotonic();fresh=stream.batch(args.batch_size)
        features=previous.extract_batched(torch,frozen,tokenizer,[x['prompt'] for x in fresh],
                                          model_device,args.max_prompt_tokens)
        if features.requires_grad:raise RuntimeError('PARALLEL_BACKBONE_GRADIENT_DETECTED')
        source_xy=source_decode(source,features.to(dev))
        replay.add(features,fresh,source_xy)
        capture_time+=time.monotonic()-t
        t=time.monotonic()
        for _ in range(args.replay_updates):
            sampled=replay.sample(args.replay_batch)
            op_loss,step_losses=train_step(networks,optimizers,op_probe,op_opt,sampled,dev)
            for name,value in step_losses.items():losses[name].append(value)
        update_time+=time.monotonic()-t
        if k%args.report_every==0 or k==args.steps:
            emit('parallel_train_progress',step=k,examples_seen=len(stream.seen),
                 replay_records=len(replay.rows),op_loss=op_loss,
                 losses={name:round(float(np.mean(values)),6) for name,values in losses.items()},
                 elapsed_seconds=round(time.monotonic()-start,2),
                 shared_capture_seconds=round(capture_time,2),head_update_seconds=round(update_time,2))
            losses={name:[] for name in NAMES}
        if k%args.eval_every==0 or k==args.steps:
            report,matrix=evaluate(networks,op_probe,source,validation,dev)
            summary={a:{key:report['registers'][a][key] for key in ('numeric_5pct','overall_5pct','numeric_asinh_mae')} for a in ('source',)+NAMES}
            emit('parallel_validation',step=k,operation_accuracy=report['operation_accuracy'],
                 source_xy_joint_5pct=report['source_xy_joint_5pct'],registers=summary,
                 oracle_numeric_5pct=report['oracle_numeric_5pct'],
                 rescued_source_errors=report['rescued_source_errors'],
                 damaged_source_successes=report['damaged_source_successes'])
            detailed_path=root/f'validation-step-{k:06d}.json'
            atomic_json(detailed_path,{'step':k,**report})
            np.save(root/f'correctness-step-{k:06d}.npy',matrix)
            for name in NAMES:
                m=report['registers'][name]
                candidate=(m['numeric_5pct'],m['overall_5pct'])
                old=(best[name]['numeric_5pct'],best[name]['overall_5pct'])
                if candidate>old:
                    best[name]={'step':k,'numeric_5pct':candidate[0],'overall_5pct':candidate[1]}
                    atomic_checkpoint(root/name/'best.pt',{'schema':SCHEMA,'config':cfg,'step':k,
                         'model':networks[name].state_dict(),'operation_probe':op_probe.state_dict(),
                         'score':best[name]})
                    emit('parallel_best',register=name,**best[name])
        if k%args.checkpoint_every==0 or k==args.steps:
            state=pack_state(k,cfg,networks,optimizers,op_probe,op_opt,stream,replay,best)
            atomic_checkpoint(global_latest,state)
            for name in NAMES:
                atomic_checkpoint(root/name/'latest.pt',{'schema':SCHEMA,'config':cfg,'step':k,
                    'model':networks[name].state_dict(),'optimizer':optimizers[name].state_dict(),
                    'operation_probe':op_probe.state_dict()})
            emit('parallel_checkpoint',step=k,path=str(global_latest))
    emit('parallel_complete',step=args.steps,best=best,results_root=str(root))
    return 0


def self_test():
    torch.set_num_threads(2);torch.manual_seed(19)
    example=[{'a':135000,'b':30,'operation':'written_product'},
             {'a':-550,'b':-12000,'operation':'written_sub'},
             {'a':3,'b':2,'operation':'written_greater'},
             {'a':0,'b':-2000000,'operation':'sym_multiply'},
             {'a':2000000,'b':2000000,'operation':'sym_multiply'}]
    tgt,bm=truth_for(example)
    assert tgt.tolist()==[4050000.,11450.,1.,0.,4000000000000.]
    assert bm.tolist()==[False,False,True,False,False]
    assert result_value(7,5,'multistep_double')==9
    assert result_value(7,5,'multistep_difference')==9
    assert result_value(7,5,'algebra_right')==-2
    assert result_value(7,5,'algebra_left')==2
    x=torch.randn(len(example),INPUT_WIDTH)
    op=SharedOperationProbe(); ids=op(x).argmax(1)
    ref=torch.tensor([4050000.,11450.,1.,0.,4e12])
    design=features_with_op(x,ids,ref)
    assert design.shape==(len(example),INPUT_WIDTH+len(OP_CLASSES)+1)
    numeric=torch.tensor(tgt,dtype=torch.float32)
    mask=torch.tensor(bm)
    summary={}
    for name in NAMES:
        head=ResultHead(name,16)
        opt=torch.optim.AdamW(head.parameters(),lr=.003)
        out=head(design)
        loss,meta=head.objective(out,numeric,mask,ref,exact_values=tgt)
        assert torch.isfinite(loss),name
        opt.zero_grad();loss.backward()
        assert any(p.grad is not None and torch.isfinite(p.grad).all() for p in head.parameters()),name
        opt.step()
        pred=head.infer(head(design),ref,bm)
        assert pred.shape==(len(example),) and np.isfinite(pred).all(),name
        summary[name]={'loss':float(loss.detach()),'parameters':sum(p.numel() for p in head.parameters())}
    left,place,length=decimal_targets(tgt)
    assert left[0,:7].tolist()==[4,0,5,0,0,0,0]
    assert place[0,:7].tolist()==[0,0,0,0,5,0,4]
    assert length[0]==6
    assert (bit_targets([5])[0,:4]==[1,0,1,0]).all()
    assert exact_or_relative_hits([105,0,1],[100,0,1],[False,False,True]).tolist()==[True,True,True]
    assert not exact_or_relative_hits([106],[100],[False])[0]
    rr=Replay(3,11)
    rr.add(x[:3],example[:3],[[1,2],[3,4],[5,6]]);snapshot=rr.state_dict()
    original=rr.sample(5)
    rr2=Replay(3,11);rr2.load_state_dict(snapshot);recovered=rr2.sample(5)
    assert [(r[1],r[2]) for r in original]==[(r[1],r[2]) for r in recovered]
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        ck=Path(directory)/'state.pt';atomic_checkpoint(ck,{'schema':SCHEMA,'config':{'a':3},'test':x[:1]})
        loaded=torch.load(ck,weights_only=True)
        assert loaded['config']=={'a':3}
    emit('parallel_self_test_pass',heads=summary,shared_forward_only=True,
         independent_optimizers=True,checkpoint_atomic=True,source_not_mutated=True)
    return 0


if __name__=='__main__':
    raise SystemExit(execute(parser().parse_args()))
