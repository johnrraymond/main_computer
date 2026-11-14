#!/usr/bin/env python3
"""Independent Pythia-70M precision continuation for alternating-register math.

No sibling trainer imports, no shared learned head, optimizer, replay or result files.
Frozen backbone captures the same sealed numeric task protocol. The math head
learns X/Y (80%), RESULT (15%), FINAL (5%) with two prompt orders.
Forks only this backbone's v1 best checkpoint; adds a robust numerical-precision
loss during X/Y updates. No v1 files are modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import random
import sys

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

import nanojev_numerate_spine_cassette_smoke as cassette
import nanojev_register_stability_v3_generate as registers

BACKBONE = 'pythia'
MODEL_ID = cassette.SOURCE[BACKBONE][0]
SCHEMA = 'nanojev.independent-three-backbone-math.precision.v2'
V1_SCHEMA = 'nanojev.independent-three-backbone-math.v1'
DEFAULT_ROOT = Path.home() / 'NanoJev' / 'runs' / 'independent_altreg_math_pythia_precision_v2'
DEFAULT_V1_ROOT = Path.home() / 'NanoJev' / 'runs' / 'independent_altreg_math_pythia_v1'
OPERATIONS = {item[0]:item for item in registers.OPERATIONS}
HELD_OUT = set(registers.HELD_OUT_OPERATIONS)
TRAIN_OPERATIONS = [item for item in registers.OPERATIONS if item[0] not in HELD_OUT]
BOOL_OPS = {'written_greater','conditional_gt','conditional_abs'}
X_SCALE = math.asinh(registers.MAX_VALUE)
R_SCALE = math.asinh(20_000_000_000_000)


def emit(name, **fields):
    print(json.dumps({'event':name,'backbone':BACKBONE,**fields},sort_keys=True,default=str),flush=True)


def digest(data):
    if isinstance(data,str): data=data.encode('utf-8')
    return hashlib.sha256(data).hexdigest()


def write_json(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    target=path.with_name(path.name+'.partial')
    target.write_text(json.dumps(obj,sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    os.replace(target,path)


def atomic_save(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    target=path.with_name(path.name+'.partial')
    torch.save(obj,target)
    os.replace(target,path)


def operation_truth(a,b,op):
    a=int(a);b=int(b)
    if op in ('sym_add','written_add','algebra_difference'):return a+b
    if op in ('sym_sub','written_sub','algebra_left','algebra_balance'):return a-b
    if op in ('sym_reverse_sub','written_difference','algebra_right'):return b-a
    if op in ('sym_multiply','written_product'):return a*b
    if op=='sym_absdiff':return abs(a-b)
    if op=='sym_max':return max(a,b)
    if op in ('written_greater','conditional_gt'):return int(a>b)
    if op=='written_average':return (a+b)/2
    if op=='conditional_min':return min(a,b)
    if op=='conditional_positive':return b if a>0 else a
    if op=='conditional_abs':return int(abs(a)>abs(b))
    if op in ('multistep_double','multistep_difference'):return 2*a-b
    if op=='multistep_combine':return 2*(a+b)
    if op=='multistep_square':return (a+b)**2
    raise ValueError('ALTREG_UNKNOWN_OPERATION:'+op)


def prompt(a,b,operation,reverse=False):
    p=registers.render(int(a),int(b),OPERATIONS[operation])
    if not reverse:return p
    lines=p.splitlines(keepends=True)
    assert len(lines)>=4 and lines[0].startswith(registers.FIRST+' = ') and lines[1].startswith(registers.SECOND+' = ')
    return ''.join((lines[1],lines[0],*lines[2:]))


def select_examples(tasks,split,count,seed):
    pool=[t for t in tasks if t['split']==split]
    if len(pool)<count:raise ValueError('ALTREG_INSUFFICIENT_HOLDOUT:'+split)
    indices=sorted(random.Random(seed).sample(range(len(pool)),count))
    return [{'a':int(pool[i]['a']),'b':int(pool[i]['b']),'operation':pool[i]['operation'],
             'family':pool[i]['family'],'pair_group':pool[i]['pair_group']} for i in indices]


class Stream:
    def __init__(self,seed,reserved):
        self.rng=random.Random(seed)
        self.reserved=set(reserved)
        self.grid=registers.numeric_grid()
        self.draws=0

    def get(self):
        for _ in range(20000):
            a=self.rng.choice(self.grid) if self.rng.random()<.75 else self.rng.randint(-registers.MAX_VALUE,registers.MAX_VALUE)
            b=self.rng.choice(self.grid) if self.rng.random()<.75 else self.rng.randint(-registers.MAX_VALUE,registers.MAX_VALUE)
            if (min(a,b),max(a,b)) not in self.reserved:
                op=self.rng.choice(TRAIN_OPERATIONS)[0]
                self.draws+=1
                return {'a':a,'b':b,'operation':op}
        raise RuntimeError('ALTREG_STREAM_EXHAUSTED')

    def state_dict(self):
        return {'rng':self.rng.getstate(),'draws':self.draws}

    def load_state_dict(self,state):
        self.rng.setstate(state['rng']);self.draws=int(state['draws'])


def capture(model,tokenizer,device,prompts,max_tokens):
    tokens=[]
    for p in prompts:
        ids=tokenizer(p,add_special_tokens=False)['input_ids']
        if len(ids)<4 or len(ids)>max_tokens:
            raise RuntimeError('ALTREG_INVALID_TOKEN_COUNT:'+str(len(ids)))
        tokens.append(ids)
    seq=max(map(len,tokens))
    pad=tokenizer.pad_token_id
    if pad is None:pad=tokenizer.eos_token_id
    if pad is None:raise RuntimeError('ALTREG_NO_PAD_TOKEN')
    inputs=torch.full((len(tokens),seq),int(pad),dtype=torch.long,device=device)
    attn=torch.zeros_like(inputs)
    for k,ids in enumerate(tokens):
        inputs[k,:len(ids)]=torch.tensor(ids,dtype=torch.long,device=device)
        attn[k,:len(ids)]=1
    with torch.inference_mode():
        output=model(input_ids=inputs,attention_mask=attn,output_hidden_states=True,use_cache=False)
        states=output.hidden_states
        if states is None or len(states)<3:raise RuntimeError('ALTREG_NO_HIDDEN_STATES')
        layers=(max(1,(len(states)-1)//2),len(states)-1)
        rows=torch.arange(len(tokens),device=device)[:,None]
        positions=torch.tensor([len(t) for t in tokens],device=device)[:,None]+torch.arange(-4,0,device=device)[None,:]
        matrix=torch.stack([states[j][rows,positions,:] for j in layers],dim=1)
        if not bool(torch.isfinite(matrix).all()):raise RuntimeError('ALTREG_NONFINITE_CAPTURE')
        result=matrix.flatten(1).float().cpu()
    # Clone OUTSIDE inference_mode: Linear backward cannot save inference tensors.
    return result.clone()


class MathHead(nn.Module):
    def __init__(self,input_dim,width,op_count):
        super().__init__()
        self.input_dim=input_dim
        self.feature=nn.Sequential(nn.LayerNorm(input_dim,elementwise_affine=False),
                  nn.Linear(input_dim,width),nn.GELU(),nn.Linear(width,width),nn.GELU())
        self.xy=nn.Linear(width,2)
        self.result=nn.Sequential(nn.Linear(width+2+op_count,width),nn.GELU(),nn.Linear(width,1))
        self.final=nn.Sequential(nn.Linear(width+2+op_count+1,width),nn.GELU(),nn.Linear(width,1))
        self.op_count=op_count

    def extract(self,z):
        if z.shape[-1]!=self.input_dim:raise RuntimeError('ALTREG_FEATURE_DIMENSION_DRIFT')
        return self.feature(z.float())

    def xy_coord(self,h):return torch.tanh(self.xy(h))

    def result_coord(self,h,xy,op_index):
        op=F.one_hot(op_index,num_classes=self.op_count).float()
        return torch.tanh(self.result(torch.cat((h,xy.detach(),op),dim=-1)))

    def final_coord(self,h,xy,op_index,r):
        op=F.one_hot(op_index,num_classes=self.op_count).float()
        correction=.25*torch.tanh(self.final(torch.cat((h,xy.detach(),op,r.detach()),dim=-1)))
        return (r+correction).clamp(-1.,1.)

    def forward(self,z,op_index):
        h=self.extract(z)
        xy=self.xy_coord(h)
        r=self.result_coord(h,xy,op_index)
        return xy,r,self.final_coord(h,xy,op_index,r)


def phase_at(tick):
    # Exact 16/3/1 updates per 20 ticks, no additional optimizer update.
    if tick<0:raise ValueError('ALTREG_NEGATIVE_TICK')
    k=tick%20
    return 'intermediate' if k<16 else 'result' if k<19 else 'final'


def asinh_truth(examples,device):
    xy=np.asarray([[p['a'],p['b']] for p in examples],dtype=np.float64)
    vals=np.asarray([operation_truth(p['a'],p['b'],p['operation']) for p in examples],dtype=np.float64)
    x=torch.tensor(np.arcsinh(xy)/X_SCALE,device=device,dtype=torch.float32)
    r=torch.tensor(np.arcsinh(vals)/R_SCALE,device=device,dtype=torch.float32)[:,None]
    return x,r,xy,vals



def numeric_coordinate_tolerance(numeric_xy, relative_tolerance, device):
    """Largest symmetric asinh-coordinate radius guaranteed inside numeric tolerance.

    For truth a, the tolerance in number space is t*max(abs(a),1).
    Asinh is monotonic, so take the nearer normalized transformed endpoint.
    This prevents the precision term from introducing sinh's huge gradients.
    """
    if not (0.0<relative_tolerance<1.0):
        raise ValueError('ALTREG_INVALID_PRECISION_TOLERANCE')
    values=np.asarray(numeric_xy,dtype=np.float64)
    if values.ndim!=2 or values.shape[1]!=2 or not np.all(np.isfinite(values)):
        raise ValueError('ALTREG_INVALID_NUMERIC_OPERANDS')
    radius=relative_tolerance*np.maximum(1.,np.abs(values))
    center=np.arcsinh(values)
    below=center-np.arcsinh(values-radius)
    above=np.arcsinh(values+radius)-center
    tolerance=np.minimum(below,above)/X_SCALE
    if not np.all(np.isfinite(tolerance)) or np.any(tolerance<=0):
        raise RuntimeError('ALTREG_INVALID_TRANSFORMED_TOLERANCE')
    return torch.tensor(tolerance,device=device,dtype=torch.float32)


def precision_aware_loss(predicted_xy,target_xy,numeric_xy,relative_tolerance=.05,huber_beta=1.0):
    """Robust 5%-tolerance-aware supervision in normalized asinh space.

    The residual is measured in units of the exact transformed tolerance.
    Smooth-L1 keeps the derivative finite for initially enormous mistakes.
    """
    if not (math.isfinite(huber_beta) and huber_beta>0):
        raise ValueError('ALTREG_INVALID_PRECISION_HUBER_BETA')
    tolerance=numeric_coordinate_tolerance(numeric_xy,relative_tolerance,predicted_xy.device)
    residual=(predicted_xy-target_xy)/tolerance
    return F.smooth_l1_loss(residual,torch.zeros_like(residual),beta=huber_beta)


def precision_rank(metrics):
    """Prioritize 5%, 1%, 0.1% decoded accuracy, then numerical error."""
    both=(metrics['original'],metrics['reversed'])
    def acc(key):return sum((row['x_'+key]+row['y_'+key])/4. for row in both)
    rel=sum(row['xy_relative_mse']/2. for row in both)
    return [acc('5pct'),acc('1pct'),acc('0p1pct'),-math.log1p(rel)]


def asinh_score(metrics):
    return (metrics['original']['x_asinh_mse']+metrics['original']['y_asinh_mse']+
            metrics['reversed']['x_asinh_mse']+metrics['reversed']['y_asinh_mse'])/2.


def assert_v1_compatible(old_config,expected):
    """The fork must be from exactly this model and the matching v1 protocol."""
    expected=dict(expected)
    expected['schema']=V1_SCHEMA
    expected['method']='frozen-last-4-tokens-mid-and-final-layer;normalized-asinh-loss'
    for name in ('precision_weight','precision_relative_tolerance','precision_huber_beta','parent_v1'):
        expected.pop(name,None)
    if old_config!=expected:
        raise RuntimeError('ALTREG_V1_CONFIG_OR_BACKBONE_DRIFT')


def load_v1_fork(source_root,output,net,optimizers,stream,expected_v2):
    source_root=source_root.expanduser().resolve()
    if source_root==output:
        raise RuntimeError('ALTREG_SOURCE_EQUALS_DESTINATION')
    checkpoint=source_root/'best.pt'
    cachefile=source_root/'validation-cache.pt'
    if not checkpoint.is_file() or not cachefile.is_file():
        raise FileNotFoundError('ALTREG_V1_BEST_OR_CACHE_MISSING:'+str(source_root))
    old=torch.load(checkpoint,map_location='cpu',weights_only=True)
    old_cache=torch.load(cachefile,map_location='cpu',weights_only=True)
    if old.get('schema')!=V1_SCHEMA or old.get('backbone')!=BACKBONE:
        raise RuntimeError('ALTREG_V1_WRONG_BACKBONE')
    assert_v1_compatible(old['config'],expected_v2)
    if old_cache.get('config')!=old['config']:
        raise RuntimeError('ALTREG_V1_CACHE_CONFIG_DRIFT')
    net.load_state_dict(old['head'],strict=True)
    for name,opt in optimizers.items():opt.load_state_dict(old['optimizer'][name])
    stream.load_state_dict(old['stream'])
    torch.set_rng_state(old['torch_rng'])
    if torch.cuda.is_available() and 'cuda_rng' in old:
        torch.cuda.set_rng_state_all(old['cuda_rng'])
    ancestry={'source_root':str(source_root),'source_checkpoint':'best.pt',
              'sha256':digest(checkpoint.read_bytes()),'step':int(old['step'])}
    emit('independent_math_precision_fork',v1_step=old['step'],source_root=str(source_root),
         results_root=str(output),source_sha256=ancestry['sha256'])
    return int(old['step']),dict(old['phase_counts']),old_cache,ancestry


def numeric_unscale(coords,scale):
    return np.sinh(np.clip(coords.astype(np.float64),-1.,1.)*scale)


def evaluate(model,pack,device,batch_size=128):
    # Same held-out examples on both orders. Includes label-aligned arithmetic mean.
    model.eval()
    examples=pack['examples']
    gold_xy=np.asarray([[row['a'],row['b']] for row in examples],dtype=np.float64)
    gold_r=np.asarray([operation_truth(row['a'],row['b'],row['operation']) for row in examples],dtype=np.float64)
    is_bool=np.asarray([row['operation'] in BOOL_OPS for row in examples],dtype=bool)
    ops=torch.as_tensor([OP_IDS[row['operation']] for row in examples],dtype=torch.long,device=device)
    decoded={}
    with torch.inference_mode():
        for order in ('original','reversed'):
            xy_chunks=[];r_chunks=[];f_chunks=[]
            for start in range(0,len(examples),batch_size):
                end=start+batch_size
                z=pack[order][start:end].to(device)
                xy,r,f=model(z,ops[start:end])
                xy_chunks.append(numeric_unscale(xy.cpu().numpy(),X_SCALE))
                r_chunks.append(numeric_unscale(r.cpu().numpy()[:,0],R_SCALE))
                f_chunks.append(numeric_unscale(f.cpu().numpy()[:,0],R_SCALE))
            decoded[order]={'xy':np.concatenate(xy_chunks),'result':np.concatenate(r_chunks),
                             'final':np.concatenate(f_chunks)}
    avg={k:(decoded['original'][k]+decoded['reversed'][k])/2. for k in ('xy','result','final')}
    decoded['averaged']=avg
    metrics={}
    for order,p in decoded.items():
        xy=p['xy'];result=p['result'];final=p['final']
        rel=(xy-gold_xy)/np.maximum(1.,np.abs(gold_xy))
        within=np.abs(rel)<=.05
        rs=(result-gold_r)/np.maximum(1.,np.abs(gold_r))
        fs=(final-gold_r)/np.maximum(1.,np.abs(gold_r))
        truth_scaled=np.arcsinh(gold_xy)/X_SCALE
        prediction_scaled=np.arcsinh(xy)/X_SCALE
        truth_result=np.arcsinh(gold_r)/R_SCALE
        result_scaled=np.arcsinh(result)/R_SCALE
        final_scaled=np.arcsinh(final)/R_SCALE
        result_hit=np.where(is_bool,np.rint(result)==gold_r,np.abs(rs)<=.05)
        final_hit=np.where(is_bool,np.rint(final)==gold_r,np.abs(fs)<=.05)
        vals={'x_relative_mse':float(np.mean(rel[:,0]**2)),
              'y_relative_mse':float(np.mean(rel[:,1]**2)),
              'xy_relative_mse':float(np.sum(np.mean(rel**2,axis=0))),
              'x_5pct':float(within[:,0].mean()),'y_5pct':float(within[:,1].mean()),
              'x_1pct':float((np.abs(rel[:,0])<=.01).mean()),
              'y_1pct':float((np.abs(rel[:,1])<=.01).mean()),
              'x_0p1pct':float((np.abs(rel[:,0])<=.001).mean()),
              'y_0p1pct':float((np.abs(rel[:,1])<=.001).mean()),
              'joint_5pct':float(np.all(within,axis=1).mean()),
              'x_asinh_mse':float(np.mean((prediction_scaled[:,0]-truth_scaled[:,0])**2)),
              'y_asinh_mse':float(np.mean((prediction_scaled[:,1]-truth_scaled[:,1])**2)),
              'result_asinh_mse':float(np.mean((result_scaled-truth_result)**2)),
              'final_asinh_mse':float(np.mean((final_scaled-truth_result)**2)),
              'result_5pct':float(result_hit.mean()),'final_5pct':float(final_hit.mean())}
        if not all(math.isfinite(x) for x in vals.values()):raise RuntimeError('ALTREG_NONFINITE_VALIDATION')
        metrics[order]=vals
    # Simple symmetric score, no intervention in training or checkpoint objective.
    metrics['rows']=len(examples)
    return metrics


def build_pack(frozen,tokenizer,device,examples,batch,max_tokens):
    pack={'examples':examples}
    for name,reverse in (('original',False),('reversed',True)):
        tensors=[]
        for start in range(0,len(examples),batch):
            prompts=[prompt(e['a'],e['b'],e['operation'],reverse) for e in examples[start:start+batch]]
            tensors.append(capture(frozen,tokenizer,device,prompts,max_tokens))
        pack[name]=torch.cat(tensors)
    return pack


def config_for(args,metadata,input_dim,plan_sha):
    return {'schema':SCHEMA,'backbone':BACKBONE,'model_id':MODEL_ID,'model_info':metadata,
            'input_dim':input_dim,'width':args.width,'lr':args.lr,'batch_size':args.batch_size,
            'seed':args.seed,'val_examples':args.val_examples,'test_examples':args.test_examples,
            'phase_schedule':'16:3:1','prompt_orders':'even-original-odd-reversed',
            'dataset_sha256':plan_sha,'max_prompt_tokens':args.max_prompt_tokens,
            'method':'frozen-last-4-tokens-mid-and-final-layer;precision-augmented-asinh',
            'precision_weight':args.precision_weight,
            'precision_relative_tolerance':args.precision_relative_tolerance,
            'precision_huber_beta':args.precision_huber_beta,
            'training_prompt_change':'intermediate-only-result-final-original'}


def state(step,net,optimizers,stream,phase_counts,best,cfg):
    saved={'schema':SCHEMA,'backbone':BACKBONE,'config':cfg,'step':step,
           'head':net.state_dict(),'optimizer':{n:o.state_dict() for n,o in optimizers.items()},
           'stream':stream.state_dict(),'phase_counts':phase_counts,'best':best,
           'torch_rng':torch.get_rng_state()}
    if torch.cuda.is_available():saved['cuda_rng']=torch.cuda.get_rng_state_all()
    return saved


def restore(file,net,optimizers,stream,cfg):
    saved=torch.load(file,map_location='cpu',weights_only=True)
    if saved.get('schema')!=SCHEMA or saved.get('backbone')!=BACKBONE or saved.get('config')!=cfg:
        raise RuntimeError('ALTREG_RESUME_IDENTITY_OR_CONFIG_DRIFT')
    net.load_state_dict(saved['head'])
    for name,opt in optimizers.items():
        opt.load_state_dict(saved['optimizer'][name])
    stream.load_state_dict(saved['stream'])
    torch.set_rng_state(saved['torch_rng'])
    if torch.cuda.is_available() and 'cuda_rng' in saved:torch.cuda.set_rng_state_all(saved['cuda_rng'])
    return int(saved['step']),dict(saved['phase_counts']),dict(saved['best'])


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--self-test',action='store_true')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--eval-only',action='store_true')
    p.add_argument('--eval-checkpoint',choices=('latest','fork','best-asinh','best-precision'),default='latest')
    p.add_argument('--results-root',type=Path,default=DEFAULT_ROOT)
    p.add_argument('--fork-v1-root',type=Path,default=DEFAULT_V1_ROOT,help='Read this backbone v1 best.pt and validation-cache.pt exactly once')
    p.add_argument('--precision-weight',type=float,default=.01)
    p.add_argument('--precision-relative-tolerance',type=float,default=.05)
    p.add_argument('--precision-huber-beta',type=float,default=1.0)
    p.add_argument('--steps',type=int,default=10000,help='Precision updates after v1 best fork')
    p.add_argument('--additional-steps',type=int,default=10000,help='Updates after v2 resume')
    p.add_argument('--device',choices=('cpu','cuda','auto'),default='cuda')
    p.add_argument('--dtype',choices=('float16','bfloat16','float32','auto'),default='float16')
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--seed',type=int,default=20261013)
    p.add_argument('--batch-size',type=int,default=16)
    p.add_argument('--width',type=int,default=128)
    p.add_argument('--lr',type=float,default=.0003)
    p.add_argument('--val-examples',type=int,default=256)
    p.add_argument('--test-examples',type=int,default=256)
    p.add_argument('--eval-every',type=int,default=100)
    p.add_argument('--report-every',type=int,default=50)
    p.add_argument('--max-prompt-tokens',type=int,default=256)
    return p


def self_test():
    for item in registers.OPERATIONS:
        p=prompt(17,-3,item[0]);r=prompt(17,-3,item[0],True)
        assert p.splitlines()[2:]==r.splitlines()[2:]
        assert p.splitlines()[:2]==r.splitlines()[:2][::-1]
        assert operation_truth(17,-3,item[0]) is not None
    assert [phase_at(i) for i in range(20)].count('intermediate')==16
    assert [phase_at(i) for i in range(20)].count('result')==3
    assert [phase_at(i) for i in range(20)].count('final')==1
    m=MathHead(32,16,len(OP_IDS))
    features=torch.randn(8,32)
    ops=torch.tensor([i%len(OP_IDS) for i in range(8)])
    xy,r,f=m(features,ops)
    assert xy.shape==(8,2) and r.shape==(8,1) and f.shape==(8,1)
    # Independent optimizer parameter sets, exact 80/15/5 update schedule.
    groups=optimizer_groups(m)
    ids=[[id(p) for p in group] for group in groups.values()]
    assert len(set(sum(ids,[])))==sum(map(len,ids))
    ex=[{'a':17,'b':-3,'operation':'sym_sub'}]*8
    x,y,_,_=asinh_truth(ex,'cpu')
    loss=(xy-x).square().mean()+.01*precision_aware_loss(xy,x,np.asarray([[17.,-3.]]*8))+ (r-y).square().mean()+(f-y).square().mean()
    loss.backward()
    assert torch.isfinite(loss)
    assert float(precision_aware_loss(x,x,np.asarray([[17.,-3.]]*8)))==0.
    # A zero-error paired metric should distinguish normal and exchanged labels.
    truth=np.array([[3.,7.],[1.,-1.]])
    assert np.sum(((truth-truth)/np.maximum(1,np.abs(truth)))**2)==0
    assert np.sum(((truth[:,::-1]-truth)/np.maximum(1,np.abs(truth)))**2)>0
    emit('independent_math_self_test_pass',frozen_model_loaded=False)
    return 0


def optimizer_groups(model):
    return {'intermediate':list(model.feature.parameters())+list(model.xy.parameters()),
            'result':list(model.result.parameters()),'final':list(model.final.parameters())}


def execute(args):
    if args.self_test:return self_test()
    if args.resume and args.eval_only:raise ValueError('ALTREG_RESUME_AND_EVAL_ONLY')
    if args.resume and args.eval_checkpoint!='latest':raise ValueError('ALTREG_RESUME_REQUIRES_LATEST')
    if not (math.isfinite(args.precision_weight) and 0.<=args.precision_weight<=1.):
        raise ValueError('ALTREG_INVALID_PRECISION_WEIGHT')
    if not (math.isfinite(args.precision_relative_tolerance) and 0.<args.precision_relative_tolerance<1.):
        raise ValueError('ALTREG_INVALID_PRECISION_TOLERANCE')
    if not (math.isfinite(args.precision_huber_beta) and args.precision_huber_beta>0.):
        raise ValueError('ALTREG_INVALID_PRECISION_HUBER_BETA')
    if any(getattr(args,k)<1 for k in ('batch_size','steps','additional_steps','width','val_examples','test_examples','eval_every','report_every')):
        raise ValueError('ALTREG_INVALID_ARGUMENT')
    output=args.results_root.expanduser().resolve()
    if args.resume or args.eval_only:
        if not (output/'latest.pt').is_file():raise FileNotFoundError('ALTREG_MISSING_INDEPENDENT_CHECKPOINT:'+str(output))
    elif output.exists() and any(output.iterdir()):
        raise RuntimeError('ALTREG_INDEPENDENT_RESULTS_ROOT_NOT_EMPTY:'+str(output))
    # Generate identical sealed task splits for each script, without importing
    # any other experiment or touching existing v3 captures.
    _,tasks,_,_=registers.build_plan(args.seed)
    plan_sha=digest(registers.serialized_jsonl(tasks))
    reserved={(min(int(t['a']),int(t['b'])),max(int(t['a']),int(t['b'])))
              for t in tasks if t['split']!='train'}
    validation=select_examples(tasks,'val',args.val_examples,args.seed+11)
    testing=select_examples(tasks,'test',args.test_examples,args.seed+19)
    stream=Stream(args.seed+101,reserved)
    dev='cuda' if args.device=='auto' and torch.cuda.is_available() else ('cpu' if args.device=='auto' else args.device)
    if dev=='cuda' and not torch.cuda.is_available():raise RuntimeError('ALTREG_CUDA_UNAVAILABLE')
    torch.manual_seed(args.seed)
    if dev=='cuda':torch.cuda.manual_seed_all(args.seed)
    backbone=None;tokenizer=None
    if args.eval_only:
        # Full checkpoint identity (including model_info) from this standalone
        # model only; no sibling artifacts or frozen model loading for eval.
        cfg=torch.load(output/'latest.pt',map_location='cpu',weights_only=True)['config']
        if cfg.get('backbone')!=BACKBONE or cfg.get('dataset_sha256')!=plan_sha or cfg.get('model_id')!=MODEL_ID:
            raise RuntimeError('ALTREG_WRONG_BACKBONE_OR_DATASET')
        dim=cfg['input_dim']
        for field in ('val_examples','test_examples'):
            if getattr(args,field)!=cfg[field]:raise RuntimeError('ALTREG_EVAL_EXAMPLE_COUNT_DRIFT')
    else:
        _,backbone,tokenizer,model_dev,info=cassette.load_source(BACKBONE,args)
        if str(model_dev)!=dev:raise RuntimeError('ALTREG_BACKBONE_DEVICE_DRIFT')
        if any(p.requires_grad for p in backbone.parameters()):raise RuntimeError('ALTREG_BACKBONE_NOT_FROZEN')
        dim=int(backbone.get_input_embeddings().weight.shape[-1])*8
        cfg=config_for(args,info,dim,plan_sha)
        if args.resume:
            previous=torch.load(output/'latest.pt',map_location='cpu',weights_only=True)
            if previous.get('schema')!=SCHEMA or previous.get('backbone')!=BACKBONE:
                raise RuntimeError('ALTREG_V2_WRONG_BACKBONE')
            cfg['parent_v1']=previous.get('config',{}).get('parent_v1')
    net=MathHead(dim,cfg['width'],len(OP_IDS)).to(dev)
    groups=optimizer_groups(net)
    opt={name:torch.optim.AdamW(parameters,lr=args.lr) for name,parameters in groups.items()}
    latest=output/'latest.pt'
    best={'asinh':{'step':0,'score':float('inf')},
          'precision':{'step':0,'rank':[-1.,-1.,-1.,float('-inf')]}}
    counts={'intermediate':0,'result':0,'final':0}
    initial=0
    old_cache=None
    if args.resume or args.eval_only:
        checkpoint=output/(args.eval_checkpoint+'.pt') if args.eval_only else latest
        if not checkpoint.is_file():raise FileNotFoundError('ALTREG_MISSING_V2_EVAL_CHECKPOINT:'+str(checkpoint))
        initial,counts,best=restore(checkpoint,net,opt,stream,cfg)
    else:
        initial,counts,old_cache,ancestry=load_v1_fork(args.fork_v1_root,output,net,opt,stream,cfg)
        ancestry['initial_intermediate_count']=counts['intermediate']
        cfg['parent_v1']=ancestry
    if args.resume and not args.eval_only and (output/'validation-cache.pt').is_file():
        cache=torch.load(output/'validation-cache.pt',map_location='cpu',weights_only=True)
        if cache.get('config')!=cfg:raise RuntimeError('ALTREG_VALIDATION_CACHE_DRIFT')
        val_pack=cache['validation'];test_pack=cache['test']
    elif args.eval_only:
        cache=torch.load(output/'validation-cache.pt',map_location='cpu',weights_only=True)
        if cache.get('config')!=cfg:raise RuntimeError('ALTREG_VALIDATION_CACHE_DRIFT')
        val_pack=cache['validation'];test_pack=cache['test']
    else:
        # Use the source checkpoint's exact held-out captures, then own the copy.
        # This permits genuine before/after comparisons and avoids recapturing
        # the frozen backbone in a fresh v2 baseline.
        assert old_cache is not None
        val_pack=old_cache['validation'];test_pack=old_cache['test']
        if val_pack['examples']!=validation or test_pack['examples']!=testing:
            raise RuntimeError('ALTREG_V1_HOLDOUT_DRIFT')
        output.mkdir(parents=True,exist_ok=True)
        atomic_save(output/'validation-cache.pt',{'config':cfg,'validation':val_pack,'test':test_pack})
    if args.eval_only:
        emit('independent_math_evaluation',step=initial,checkpoint=args.eval_checkpoint,
             validation=evaluate(net,val_pack,dev),test=evaluate(net,test_pack,dev))
        return 0
    # Fork baseline records the inherited v1 accuracy before changing its loss.
    if not args.resume:
        baseline=evaluate(net,val_pack,dev)
        best={'asinh':{'step':initial,'score':asinh_score(baseline)},
              'precision':{'step':initial,'rank':precision_rank(baseline)}}
        write_json(output/'baseline.json',{'step':initial,'backbone':BACKBONE,'validation':baseline,'config':cfg})
        write_json(output/'fork.json',{'parent_v1':cfg['parent_v1'],'v2_schema':SCHEMA,
                   'initial_step':initial,'precision_weight':args.precision_weight,
                   'precision_relative_tolerance':args.precision_relative_tolerance})
        emit('independent_math_precision_baseline',step=initial,paired_asinh_score=best['asinh']['score'],
             precision_rank=best['precision']['rank'],validation=baseline)
        snapshot=state(initial,net,opt,stream,counts,best,cfg)
        for fname in ('fork.pt','latest.pt','best-asinh.pt','best-precision.pt'):
            atomic_save(output/fname,snapshot)
    target=initial+(args.additional_steps if args.resume else args.steps)
    for step in range(initial+1,target+1):
        examples=[stream.get() for _ in range(args.batch_size)]
        # The prompt alternation follows global sample index, not random
        # draw order, and survives exact resume boundaries.
        stage=phase_at(step-1)
        # Only X/Y receives alternating order. RESULT and FINAL continue to
        # consume fixed-order features, exactly like v3.9.
        prompts=[prompt(e['a'],e['b'],e['operation'],stage=='intermediate' and
                        ((step-1)*args.batch_size+i)%2==1)
                 for i,e in enumerate(examples)]
        features=capture(backbone,tokenizer,dev,prompts,args.max_prompt_tokens).to(dev)
        ops=torch.as_tensor([OP_IDS[e['operation']] for e in examples],dtype=torch.long,device=dev)
        target_xy,target_r,numeric_xy,_=asinh_truth(examples,dev)
        for name in opt:opt[name].zero_grad(set_to_none=True)
        h=net.extract(features)
        xy=net.xy_coord(h)
        if stage=='intermediate':
            coordinate_loss=F.mse_loss(xy,target_xy)
            precision_loss=precision_aware_loss(xy,target_xy,numeric_xy,
                      args.precision_relative_tolerance,args.precision_huber_beta)
            loss=coordinate_loss+args.precision_weight*precision_loss
            params=groups['intermediate']
        elif stage=='result':
            result=net.result_coord(h.detach(),xy.detach(),ops)
            loss=F.mse_loss(result,target_r)
            params=groups['result']
        else:
            with torch.no_grad():result=net.result_coord(h,xy,ops)
            final=net.final_coord(h.detach(),xy.detach(),ops,result.detach())
            loss=F.mse_loss(final,target_r)
            params=groups['final']
        if not bool(torch.isfinite(loss)):
            raise RuntimeError('ALTREG_NONFINITE_TRAINING_LOSS:'+str(step))
        loss.backward()
        if any(p.grad is not None and not bool(torch.isfinite(p.grad).all()) for p in params):
            raise RuntimeError('ALTREG_NONFINITE_GRADIENT:'+str(step))
        nn.utils.clip_grad_norm_(params,1.)
        opt[stage].step()
        counts[stage]+=1
        if step%args.report_every==0:
            emit('independent_math_progress',step=step,target_step=target,phase=stage,
                 loss=float(loss.detach()),coordinate_loss=float(coordinate_loss.detach()) if stage=='intermediate' else None,
                 precision_loss=float(precision_loss.detach()) if stage=='intermediate' else None,
                 phase_counts=counts,examples_seen=stream.draws,
                 precision_updates=counts['intermediate']-cfg['parent_v1'].get('initial_intermediate_count',0))
        if step%args.eval_every==0 or step==target:
            metrics=evaluate(net,val_pack,dev)
            write_json(output/('validation-step-%08d.json'%step),
                       {'step':step,'backbone':BACKBONE,'validation':metrics,'phase_counts':counts})
            score=asinh_score(metrics)
            rank=precision_rank(metrics)
            asinh_improved=score<best['asinh']['score']
            precision_improved=rank>best['precision']['rank']
            if asinh_improved:best['asinh']={'step':step,'score':score}
            if precision_improved:best['precision']={'step':step,'rank':rank}
            snap=state(step,net,opt,stream,counts,best,cfg)
            if asinh_improved:
                atomic_save(output/'best-asinh.pt',snap)
                emit('independent_math_precision_new_best_asinh',step=step,score=score)
            if precision_improved:
                atomic_save(output/'best-precision.pt',snap)
                emit('independent_math_precision_new_best_numeric',step=step,rank=rank)
            atomic_save(latest,snap)
            emit('independent_math_precision_validation',step=step,paired_asinh_score=score,
                 numerical_precision_rank=rank,original=metrics['original'],
                 reversed=metrics['reversed'],averaged=metrics['averaged'])
    final_test=evaluate(net,test_pack,dev)
    write_json(output/'test-last.json',{'backbone':BACKBONE,'step':target,'test':final_test})
    emit('independent_math_precision_complete',step=target,results_root=str(output),best=best)
    return 0


OP_IDS={op[0]:i for i,op in enumerate(TRAIN_OPERATIONS)}

if __name__=='__main__':
    raise SystemExit(execute(parser().parse_args()))
