#!/usr/bin/env python3
"""Independent cumulative-magnitude + operation-result cutover from frozen buffered hierarchy.

CUTOVER, not an upgrade-in-place. Read an existing buffered checkpoint ONCE,
clone its decoder/replay/stream into a different root, add a learned operation
classifier, and use executed result loss to train operation and numeric heads.
Original checkpoints, config and training process are never written to.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import time

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

import nanojev_register_online_hierarchy_buffered as b

c = b.c
h = b.h
previous = b.previous
v3 = previous.v3
emit = b.emit
atomic_json = b.atomic_json
SCHEMA = 'nanojev.register-cumulative-magnitude-guarded-cutover.v2'
DEFAULT_ROOT = Path.home() / 'NanoJev' / 'runs' / 'register_cumulative_magnitude_guarded_v2'
SOURCE_DEFAULT = b.DEFAULT_ROOT / 'best.pt'

# The benchmark owns the definitive surface operation catalog. The following
# gives every template a numerical semantics; aliases deliberately share code.
# Comparisons are represented as 0/1; conditional_abs: 1 means X, 0 means Y.
OP_TO_PROGRAM = {
    'sym_add':'ADD', 'sym_sub':'SUB', 'sym_reverse_sub':'RSUB',
    'sym_multiply':'MUL', 'sym_absdiff':'ABSDIFF', 'sym_max':'MAX',
    'written_add':'ADD', 'written_sub':'SUB', 'written_product':'MUL',
    'written_difference':'RSUB', 'written_greater':'GT',
    'written_average':'AVG', 'algebra_left':'SUB',
    'algebra_right':'RSUB', 'algebra_difference':'ADD',
    'algebra_balance':'SUB', 'conditional_gt':'GT',
    'conditional_min':'MIN', 'conditional_positive':'IF_X_POS_Y_ELSE_X',
    'conditional_abs':'ABS_X_GE_Y', 'multistep_double':'TWICE_X_MINUS_Y',
    'multistep_combine':'DOUBLE_SUM', 'multistep_square':'SQUARE_SUM',
    'multistep_difference':'TWICE_X_MINUS_Y',
}
CATALOG = tuple(item[0] for item in v3.OPERATIONS)
TRAIN_OPS = tuple(op for op in CATALOG if op not in v3.HELD_OUT_OPERATIONS)
OP_ID = {name: idx for idx, name in enumerate(TRAIN_OPS)}
PROGRAM_ID = {program: idx for idx, program in enumerate(sorted(set(OP_TO_PROGRAM.values())))}
BOOL_PROGRAMS = {'GT', 'ABS_X_GE_Y'}
if set(CATALOG) != set(OP_TO_PROGRAM) or len(OP_ID) != len(TRAIN_OPS):
    raise RuntimeError('OP_CATALOG_DRIFT')


def exact_execute(program, a, b_):
    """Reference executor: Python integers prevent overflow for this benchmark."""
    a, b_ = int(a), int(b_)
    return {
        'ADD':lambda:a+b_, 'SUB':lambda:a-b_, 'RSUB':lambda:b_-a,
        'MUL':lambda:a*b_, 'ABSDIFF':lambda:abs(a-b_),
        'MAX':lambda:max(a,b_), 'MIN':lambda:min(a,b_),
        'GT':lambda:int(a>b_), 'ABS_X_GE_Y':lambda:int(abs(a)>=abs(b_)),
        'AVG':lambda:(a+b_)/2.,
        'IF_X_POS_Y_ELSE_X':lambda:b_ if a>0 else a,
        'TWICE_X_MINUS_Y':lambda:2*a-b_, 'DOUBLE_SUM':lambda:2*(a+b_),
        'SQUARE_SUM':lambda:(a+b_)**2,
    }[program]()


def predicate_margin(program, x, y, scale):
    """Signed comparison margin with scale anchored to the TRUE operands.

    Using predicted magnitudes or dividing by an enormous result scale would
    make large-number comparison gradients vanish.  A fixed true-operand scale
    produces a non-saturated margin even for reversed million-scale operands.
    """
    if program == 'GT':
        difference = x - y
    elif program == 'ABS_X_GE_Y':
        difference = x.abs() - y.abs()
    else:
        raise ValueError('OP_NOT_A_PREDICATE:' + program)
    return difference / scale


def execute_tensor(program, x, y, *, smooth=True, scale=None):
    """Physical-unit execution, with smooth predicate/selector training paths."""
    if program == 'ADD': return x+y
    if program == 'SUB': return x-y
    if program == 'RSUB': return y-x
    if program == 'MUL': return x*y
    if program == 'ABSDIFF': return (x-y).abs()
    if program == 'MAX': return torch.maximum(x,y)
    if program == 'MIN': return torch.minimum(x,y)
    if program == 'AVG': return (x+y)*.5
    if program == 'TWICE_X_MINUS_Y': return 2*x-y
    if program == 'DOUBLE_SUM': return 2*(x+y)
    if program == 'SQUARE_SUM': return (x+y).square()
    if program in BOOL_PROGRAMS:
        if not smooth:
            if program == 'GT': return (x>y).to(x.dtype)
            return (x.abs()>=y.abs()).to(x.dtype)
        denom = (1 + .01*torch.maximum(x.abs(),y.abs())).detach() if scale is None else scale
        return torch.sigmoid(predicate_margin(program,x,y,denom))
    if program == 'IF_X_POS_Y_ELSE_X':
        if not smooth: return torch.where(x>0,y,x)
        denom = (1 + .01*x.abs()).detach() if scale is None else scale
        gate=torch.sigmoid(x/denom)
        return gate*y+(1-gate)*x
    raise ValueError('OP_UNKNOWN_PROGRAM:'+program)


def _per_candidate_losses(numbers, labels, true_results, programs):
    """B x K non-cancelling errors, independently evaluating every program.

    Numeric losses operate in signed-asinh space and use a scale derived ONLY
    from targets. Boolean predicates instead use BCE-with-logits on an
    oracle-normalized signed margin (no sigmoid saturation or giant scaling).
    """
    xx,yy=numbers[:,0].float(),numbers[:,1].float()
    true_x,true_y=labels[:,0].float(),labels[:,1].float()
    magnitude_scale=torch.maximum(true_x.abs(),true_y.abs()).clamp_min(1).detach()
    boolean_target=torch.as_tensor([p in BOOL_PROGRAMS for p in programs],
                        device=numbers.device,dtype=torch.bool)
    numeric_scale=torch.maximum(true_results.abs(),magnitude_scale).clamp_min(1).detach()
    scale=torch.where(boolean_target,torch.ones_like(numeric_scale),numeric_scale)
    norm_truth=torch.asinh(true_results/scale)
    losses=[]
    for name in TRAIN_OPS:
        program=OP_TO_PROGRAM[name]
        if program in BOOL_PROGRAMS:
            logits=predicate_margin(program,xx,yy,magnitude_scale)
            output=torch.sigmoid(logits)
            # Comparison targets cannot be inferred from a non-boolean target.
            # For a numeric target, compare its numerical result with 0/1.
            numeric_error=F.smooth_l1_loss(torch.asinh(output/scale),norm_truth,
                                            beta=.25,reduction='none')
            bool_error=F.binary_cross_entropy_with_logits(logits,
                    true_results.clamp(0,1),reduction='none')
            loss=torch.where(boolean_target,bool_error,numeric_error)
        else:
            selector_scale=true_x.abs().clamp_min(1).detach() if program=='IF_X_POS_Y_ELSE_X' else None
            output=execute_tensor(program,xx,yy,scale=selector_scale)
            loss=F.smooth_l1_loss(torch.asinh(output/scale),norm_truth,
                                   beta=.25,reduction='none')
        losses.append(loss)
    result=torch.stack(losses,dim=-1)
    if not torch.isfinite(result).all():
        raise RuntimeError('OP_NONFINITE_EXECUTION_LOSS')
    return result

def signed_log(x):
    """Signed, smooth log-like compression with nonzero derivative at zero."""
    return torch.asinh(x)


def relative_pass(guess, target, is_boolean):
    guess=np.asarray(guess, dtype=np.float64)
    target=np.asarray(target, dtype=np.float64)
    is_boolean=np.asarray(is_boolean, dtype=bool)
    good=(np.abs(guess-target) <= np.maximum(1., .05*np.abs(target)))
    return np.where(is_boolean, np.abs(guess-target)<.5, good)


class OperationReplay(b.BucketReplay):
    """Reuses the source buffer schema; samples operation labels with activations."""
    def sample_labeled(self, per_bank):
        ids=[]
        for register in self.banks:
            for bank in register:
                if bank:
                    choices=list(bank)
                    if len(choices)>=per_bank: ids.extend(self.rng.sample(choices,per_bank))
                    else: ids.extend(self.rng.choices(choices,k=per_bank))
        self.rng.shuffle(ids)
        if not ids: raise RuntimeError('OP_EMPTY_REPLAY')
        rows=[self.records[rid] for rid in ids]
        return (torch.stack([r[0] for r in rows]),
                np.asarray([r[1] for r in rows],dtype=np.int64),
                [r[2] for r in rows])


class CumulativeMagnitude(nn.Module):
    """Five monotonically nested magnitude gates plus six additive corrections.

    Gates are a learned thermometer code. Each activated level contributes a
    small correction in asinh coordinates; contributions are NEVER summed as
    raw physical-unit expert outputs (which would double-count magnitudes).
    The initialized residual is tiny, preserving the frozen source decoder.
    """
    def __init__(self, width=h.INPUT_WIDTH, correction_limit=.5):
        super().__init__()
        self.threshold_logits=nn.Linear(width, 2*5)
        self.delta=nn.Linear(width, 2*6)
        self.correction_limit=float(correction_limit)
        nn.init.zeros_(self.threshold_logits.weight)
        nn.init.constant_(self.threshold_logits.bias,2.)
        nn.init.zeros_(self.delta.weight)
        nn.init.zeros_(self.delta.bias)

    def forward(self, features):
        batch=features.size(0)
        thresholds=self.threshold_logits(features).reshape(batch,2,5)
        gates=torch.cumprod(torch.sigmoid(thresholds),dim=-1)
        activations=torch.cat((torch.ones_like(gates[:,:,:1]),gates),dim=-1)
        deltas=torch.tanh(self.delta(features).reshape(batch,2,6))
        correction=(activations*deltas).sum(-1)/6.0**.5
        return gates, correction*self.correction_limit


def cumulative_target(bands):
    """Band 0 -> [0,0,0,0,0]; band 3 -> [1,1,1,0,0]."""
    return (bands.unsqueeze(-1) >= torch.arange(1,6,device=bands.device)).float()


def source_decoder_straight_through(raw):
    """Exact original decoder in the forward pass, soft surrogate gradients.

    Intentionally exposes the original hard routing forward semantics while
    carrying a differentiable gradient estimator during training. Inference
    agrees with c.decode for every finite input (apart from float32 rounding).
    """
    if raw.ndim != 2 or raw.shape[1] != 38:
        raise ValueError('GUARDED_EXPECTS_38_REGISTER_OUTPUTS')
    decoded=c.decode(raw.detach().float().cpu().numpy())
    base=torch.as_tensor(decoded,device=raw.device,dtype=torch.float32)
    if torch.is_grad_enabled() and raw.requires_grad:
        soft=soft_numbers(raw)
        # Preserve exact decoded forward value while exposing surrogate gradient.
        base=base+(soft-soft.detach())
    return base


def cumulative_numbers(raw, gates, correction, *, scale=None):
    """Sign-preserving bounded ratio applied to the original decoder.

    Gates are reflected in correction from CumulativeMagnitude. Zero remains
    zero; initial correction is exactly zero so predictions match c.decode.
    """
    base=source_decoder_straight_through(raw)
    return base * torch.exp(correction.float())


class ExecutableNumeracy(nn.Module):
    def __init__(self, stage='continuity', rank=8, residual_scale=.25, op_width=96,
                 cumulative_limit=.5):
        super().__init__()
        self.registers=c.model_factory(stage,rank,residual_scale)
        self.operations=nn.Sequential(
            nn.LayerNorm(h.INPUT_WIDTH,elementwise_affine=False),
            nn.Linear(h.INPUT_WIDTH,op_width),nn.GELU(),
            nn.Linear(op_width,len(TRAIN_OPS)))
        self.cumulative=CumulativeMagnitude(correction_limit=cumulative_limit)

    def forward(self, features):
        gates,correction=self.cumulative(features)
        return self.registers(features),self.operations(features),gates,correction

def teacher_numbers(raw, labels, bands):
    """Differentiable oracle-bucket reconstruction used ONLY for loss."""
    z=raw.double().reshape(-1,2,19)
    indexes=7+2*bands+(labels>0).long()
    chosen=z.gather(2,indexes.unsqueeze(-1)).squeeze(-1).clamp(-1,1)
    lower=torch.tensor([h.band_limits(i)[0] for i in range(6)],device=raw.device,dtype=torch.float64)
    upper=torch.tensor([h.band_limits(i)[1] for i in range(6)],device=raw.device,dtype=torch.float64)
    lo=torch.log(lower[bands]);hi=torch.log(upper[bands])
    mag=torch.exp(lo+.5*(chosen+1)*(hi-lo))
    return torch.where(labels==0,torch.zeros_like(mag),torch.where(labels>0,mag,-mag))


def soft_numbers(raw):
    """Differentiable inference-routing surrogate; no labels enter this path."""
    z=raw.float().reshape(-1,2,19)
    zero=torch.sigmoid(z[:,:,0]);sign=torch.sigmoid(z[:,:,1]);p=torch.sigmoid(z[:,:,2:7])
    survival=torch.cumprod(p,dim=-1)
    weights=torch.cat(((1-p[:,:,:1]),survival[:,:,:-1]*(1-p[:,:,1:]),survival[:,:,-1:]),dim=-1)
    coords=z[:,:,7:].reshape(-1,2,6,2).clamp(-1,1)
    lo=torch.tensor([h.band_limits(i)[0] for i in range(6)],device=raw.device,dtype=torch.float32).log()
    hi=torch.tensor([h.band_limits(i)[1] for i in range(6)],device=raw.device,dtype=torch.float32).log()
    mag=torch.exp(lo[None,None,:,None]+.5*(coords+1)*(hi-lo)[None,None,:,None])
    positive=(weights*mag[:,:,:,1]).sum(-1)
    negative=(weights*mag[:,:,:,0]).sum(-1)
    return (1-zero)*(sign*positive-(1-sign)*negative)


def execution_loss(raw, op_logits, labels, bands, names, *, gates=None, correction=None,
                   threshold_weight=.12, op_weight=.3, result_weight=.12,
                   routed_result_weight=.02, temperature=1.):
    """Supervised template classification plus non-cancelling executed results.

    Three separate result gradients:
      - the oracle OP's executed error propagates directly into X and Y;
      - softmax-weighted *candidate errors* train the operation classifier;
      - the optional soft-route term also supervises numeric sign/bucket logits.
    No ground-truth operation, operand or bucket is used at inference.
    """
    if temperature <= 0: raise ValueError('OP_TEMPERATURE_MUST_BE_POSITIVE')
    if any(name not in OP_ID for name in names):
        raise ValueError('OP_HELDOUT_OPERATION_MUST_NOT_TRAIN')
    ids=torch.as_tensor([OP_ID[name] for name in names],dtype=torch.long,device=raw.device)
    op_ce=F.cross_entropy(op_logits.float(),ids)
    results=[exact_execute(OP_TO_PROGRAM[name],int(x),int(y)) for name,(x,y) in
             zip(names,labels.tolist())]
    true_results=torch.as_tensor(results,device=raw.device,dtype=torch.float32)
    op_probs=F.softmax(op_logits.float()/temperature,dim=-1)
    def per_branch(numbers):
        candidates=_per_candidate_losses(numbers,labels,true_results,
                                          [OP_TO_PROGRAM[name] for name in names])
        chosen=candidates.gather(1,ids[:,None]).squeeze(-1).mean()
        weighted=(candidates*op_probs).sum(-1).mean()
        return chosen,weighted
    oracle,oracle_candidates=per_branch(teacher_numbers(raw,labels,bands))
    numbers=(cumulative_numbers(raw,gates,correction) if gates is not None
             else soft_numbers(raw))
    routed,routed_candidates=per_branch(numbers)
    threshold_loss=(F.binary_cross_entropy(gates.clamp(1e-6,1-1e-6),
                    cumulative_target(bands)) if gates is not None else raw.new_zeros(()))
    combined=(threshold_weight*threshold_loss+op_weight*op_ce+
              result_weight*(oracle+oracle_candidates)+
              routed_result_weight*(routed+routed_candidates))
    if not torch.isfinite(combined): raise RuntimeError('OP_NONFINITE_TOTAL')
    return combined,{'threshold_bce':threshold_loss.detach(), 'op_ce':op_ce.detach(),
                     'oracle_executed_result':oracle.detach(),
                     'oracle_candidate_result':oracle_candidates.detach(),
                     'soft_routed_result':routed.detach(),
                     'soft_routed_candidate_result':routed_candidates.detach(),
                     'combined':combined.detach()}

def predict_batch(network, features, device):
    """Structured output contract for a future language-model integration."""
    was_training=network.training
    network.eval()
    with torch.inference_mode():
        data=torch.as_tensor(features,dtype=torch.float32,device=device)
        raw,op_logits,gates,correction=network(data)
        corrected=cumulative_numbers(raw,gates,correction).cpu().numpy()
        raw=raw.float().cpu().numpy()
        probs=F.softmax(op_logits.float(),dim=-1).cpu().numpy()
    network.train(was_training)
    decoded=np.rint(corrected).astype(np.int64)
    values=[]
    for (x,y),p in zip(decoded,probs):
        oid=int(p.argmax());op=TRAIN_OPS[oid];program=OP_TO_PROGRAM[op]
        result=exact_execute(program,int(x),int(y))
        # Float values are JSON-serializable; boolean programs are semantic 0/1.
        values.append({'x':int(x),'y':int(y),'operation':op,
                       'program':program,'operation_probability':float(p[oid]),
                       'result':result,'result_kind':'boolean' if program in BOOL_PROGRAMS else 'number'})
    return values


def evaluate(network, evaluation, device, batch_size=256):
    features,numbers,ops,families=evaluation
    records=[];all_logits=[];all_raw=[];all_corrected=[];all_gates=[]
    old=network.training;network.eval()
    with torch.inference_mode():
        for i in range(0,len(features),batch_size):
            tensor=torch.as_tensor(features[i:i+batch_size],dtype=torch.float32,device=device)
            r,o,g,delta=network(tensor)
            all_raw.append(r.float().cpu().numpy());all_logits.append(o.float().cpu().numpy())
            all_corrected.append(cumulative_numbers(r,g,delta).cpu().numpy())
            all_gates.append(g.cpu().numpy())
    network.train(old)
    raw=np.concatenate(all_raw);logits=np.concatenate(all_logits)
    baseline_predicted=c.decode(raw)
    predicted=np.rint(np.concatenate(all_corrected)).astype(np.int64)
    cumulative_gates=np.concatenate(all_gates)
    selected=np.argmax(logits,axis=1)
    pred_names=[TRAIN_OPS[int(k)] for k in selected]
    pred_programs=[OP_TO_PROGRAM[n] for n in pred_names]
    true_programs=[OP_TO_PROGRAM[n] for n in ops]
    predictions=[exact_execute(p,int(x),int(y)) for p,(x,y) in zip(pred_programs,predicted)]
    truth=[exact_execute(p,int(x),int(y)) for p,(x,y) in zip(true_programs,numbers)]
    true_ops=[exact_execute(p,int(x),int(y)) for p,(x,y) in zip(true_programs,predicted)]
    true_numbers=[exact_execute(p,int(x),int(y)) for p,(x,y) in zip(pred_programs,numbers)]
    boolean=[p in BOOL_PROGRAMS for p in true_programs]
    predicted_pass=relative_pass(predictions,truth,boolean)
    oracle_op_pass=relative_pass(true_ops,truth,boolean)
    oracle_xy_pass=relative_pass(true_numbers,truth,boolean)
    known=np.asarray([name in OP_ID for name in ops],dtype=bool)
    template_hit=np.asarray([guess==true for guess,true in zip(pred_names,ops)])
    program_hit=np.asarray([a==b for a,b in zip(pred_programs,true_programs)])
    xy_pass=previous.correctness(predicted,numbers)
    bands=h.band_of(numbers)
    local=c.log_encode(np.abs(numbers),bands)
    oracle_indices=7+2*bands+(numbers>0).astype(np.int64)
    per_reg_raw=raw.reshape(-1,2,19)
    regression=np.take_along_axis(per_reg_raw,oracle_indices[:,:,None],axis=2)[:,:,0]
    nz=(numbers!=0)
    magnitude_mae=float(np.abs(regression[nz]-local[nz]).mean()) if nz.any() else None
    _,_,pred_bands=h.predicted_routing(raw)
    per_family={}
    for fam in sorted(set(families)):
        idx=np.asarray([x==fam for x in families])
        per_family[fam]={'rows':int(idx.sum()),'result_pass':float(predicted_pass[idx].mean()),
                         'operation_accuracy':float(template_hit[idx&known].mean()) if (idx&known).any() else None}
    return {'rows':len(truth),'trainable_operation_classes':len(TRAIN_OPS),
            'unsupported_heldout_operation_rows':int((~known).sum()),
            'op_template_accuracy':float(template_hit[known].mean()) if known.any() else None,
            'op_program_accuracy':float(program_hit[known].mean()) if known.any() else None,
            'result_5pct_pass_rate':float(predicted_pass.mean()),
            'result_given_true_operation_pass_rate':float(oracle_op_pass.mean()),
            'result_given_true_operands_pass_rate':float(oracle_xy_pass.mean()),
            'x_5pct_pass_rate':float(xy_pass[:,0].mean()),
            'y_5pct_pass_rate':float(xy_pass[:,1].mean()),
            'joint_xy_5pct_pass_rate':float(xy_pass.all(axis=1).mean()),
            'source_decoder_joint_xy_pass_rate':float(previous.correctness(baseline_predicted,numbers).all(axis=1).mean()),
            'source_decoder_x_pass_rate':float(previous.correctness(baseline_predicted,numbers)[:,0].mean()),
            'source_decoder_y_pass_rate':float(previous.correctness(baseline_predicted,numbers)[:,1].mean()),
            'corrected_vs_source_changed_fraction':float((predicted!=baseline_predicted).any(axis=1).mean()),
            'cumulative_threshold_binary_accuracy':float(((cumulative_gates>=.5)==(cumulative_target(torch.as_tensor(h.band_of(numbers))).numpy()>=.5)).mean()),
            'true_bucket_expert_normalized_mae':magnitude_mae,
            'x_regime_accuracy':float((pred_bands[:,0][nz[:,0]]==bands[:,0][nz[:,0]]).mean()) if nz[:,0].any() else None,
            'y_regime_accuracy':float((pred_bands[:,1][nz[:,1]]==bands[:,1][nz[:,1]]).mean()) if nz[:,1].any() else None,
            'numeric_result_rows':int((~np.asarray(boolean)).sum()),
            'boolean_result_rows':int(np.asarray(boolean).sum()),
            'numeric_result_pass_rate':float(predicted_pass[~np.asarray(boolean)].mean()) if (~np.asarray(boolean)).any() else None,
            'boolean_result_pass_rate':float(predicted_pass[np.asarray(boolean)].mean()) if np.asarray(boolean).any() else None,
            'families':per_family,
            'examples':[{'true_x':int(numbers[i,0]),'true_y':int(numbers[i,1]),
                         'pred_x':int(predicted[i,0]),'pred_y':int(predicted[i,1]),
                         'true_operation':ops[i],'pred_operation':pred_names[i],
                         'true_result':truth[i],'pred_result':predictions[i]}
                         for i in range(min(8,len(truth)))]}


def make_config(args,manifest):
    config=b.config_for(args,manifest)
    config.update(schema=SCHEMA, objective='cumulative_magnitude_guarded_operation_result_v2',
                  threshold_weight=args.threshold_weight,cumulative_limit=args.cumulative_limit,
                  source_checkpoint=str(args.source_checkpoint.expanduser().resolve()),
                  op_width=args.op_width,op_weight=args.op_weight,
                  result_weight=args.result_weight,routed_result_weight=args.routed_result_weight,
                  op_temperature=args.op_temperature,operation_catalog=list(TRAIN_OPS),
                  operation_programs=OP_TO_PROGRAM,train_registers=args.train_registers)
    return config


def save(path,network,optimizer,step,config,stream,replay,best,identity,source):
    value={'schema':SCHEMA,'step':step,'config':config,'network':network.state_dict(),
           'optimizer':optimizer.state_dict(),'random_stream':stream.state_dict(),
           'replay':replay.state_dict(),'best_validation':best,
           'backbone_identity':identity,'source_cutover':source,'torch_rng':torch.get_rng_state()}
    if torch.cuda.is_available():value['cuda_rng']=torch.cuda.get_rng_state_all()
    tmp=path.with_name(path.name+'.partial')
    torch.save(value,tmp);os.replace(tmp,path)


def restore(path,network,optimizer,stream,replay,config,identity):
    ck=torch.load(path,map_location='cpu',weights_only=True)
    if ck.get('schema')!=SCHEMA or ck.get('config')!=config or ck.get('backbone_identity')!=identity:
        raise RuntimeError('OP_RESUME_CONFIG_OR_IDENTITY_DRIFT')
    network.load_state_dict(ck['network']);optimizer.load_state_dict(ck['optimizer'])
    stream.load_state_dict(ck['random_stream']);replay.load_state_dict(ck['replay'])
    torch.set_rng_state(ck['torch_rng'].cpu().to(torch.uint8))
    if torch.cuda.is_available() and 'cuda_rng' in ck:
        torch.cuda.set_rng_state_all([q.cpu().to(torch.uint8) for q in ck['cuda_rng']])
    return int(ck['step']),dict(ck['best_validation']),dict(ck['source_cutover'])


def cutover(path,network,stream,replay,identity,args):
    """Do not reuse old optimizer state. Preserve fresh-pair and replay histories."""
    data=path.read_bytes()  # immutable snapshot even while source trainer runs
    digest=hashlib.sha256(data).hexdigest()
    import io
    ck=torch.load(io.BytesIO(data),map_location='cpu',weights_only=True)
    if ck.get('schema')!=b.SCHEMA or ck.get('backbone_identity')!=identity:
        raise RuntimeError('OP_CUTOVER_BAD_SOURCE_OR_IDENTITY')
    old=ck.get('config',{})
    if (old.get('constraint_stage')!=args.constraint_stage or old.get('rank')!=args.rank
        or old.get('residual_scale')!=args.residual_scale or old.get('buffer_capacity')!=args.buffer_capacity
        or (old.get('sampler') is not None and old['sampler']!=args.sampler)
        or (old.get('seed') is not None and old['seed']!=args.seed)):
        raise RuntimeError('OP_CUTOVER_ARCHITECTURE_OR_BUFFER_DRIFT')
    network.registers.load_state_dict(ck['model'],strict=True)
    stream.load_state_dict(ck['random_stream']);replay.load_state_dict(ck['replay'])
    # Branch the *new* stream after importing its full seen-pair history.
    # Without this fork, two concurrently running experiments would generate
    # identical post-cutover numerical problems from the same RNG state.
    fork_seed=int(digest[:16],16)^int(args.seed)
    stream.rng.seed(fork_seed)
    if hasattr(stream,'bucket_pool'):stream.bucket_pool=[]
    return {'path':str(path),'sha256':digest,'source_step':int(ck['step']),
            'source_seen_pairs':len(stream.seen),'stream_fork_seed':fork_seed,
            'source_best_validation':ck.get('best_validation',{})}


def parser():
    p=b.parser()
    p.description=__doc__
    p.set_defaults(results_root=DEFAULT_ROOT,steps=3000,batch_size=16)
    p.add_argument('--source-checkpoint',type=Path,default=SOURCE_DEFAULT,
                   help='Read-only buffered best.pt or latest.pt, copied once at cutover')
    p.add_argument('--op-width',type=int,default=96)
    p.add_argument('--train-registers',action='store_true',help='Opt in to modifying inherited register weights')
    p.add_argument('--threshold-weight',type=float,default=.12)
    p.add_argument('--cumulative-limit',type=float,default=.5)
    p.add_argument('--op-weight',type=float,default=.3)
    p.add_argument('--result-weight',type=float,default=.12)
    p.add_argument('--routed-result-weight',type=float,default=.02)
    p.add_argument('--op-temperature',type=float,default=1.)
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    if args.self_test: self_test();return 0
    if args.init_checkpoint:
        raise ValueError('OP_USE_--source-checkpoint_NOT_--init-checkpoint')
    if min(args.op_width,args.batch_size,args.replay_updates,args.steps,args.eval_every,
           args.checkpoint_every,args.report_every,args.buffer_capacity,args.per_expert_batch,
           args.val_examples,args.threads)<1 or args.op_temperature<=0 or min(args.op_weight,
           args.result_weight,args.routed_result_weight,args.threshold_weight)<0 or args.cumulative_limit<=0:
        raise ValueError('OP_INVALID_CONFIGURATION')
    if args.op_weight==args.result_weight==args.routed_result_weight==0:
        raise ValueError('OP_OBJECTIVE_DISABLED')
    torch.set_num_threads(args.threads)
    if args.device=='cuda' and not torch.cuda.is_available():
        raise RuntimeError('OP_CUDA_UNAVAILABLE')
    device=torch.device(args.device)
    benchmark, tasks=previous.read_benchmark(args.v3_root)
    source=args.v3_root.expanduser().resolve()
    identity=json.loads((source/'model_identity.json').read_text(encoding='utf-8'))
    if identity.get('model')!='qwen':raise RuntimeError('OP_EXPECTS_QWEN')
    origin=args.source_checkpoint.expanduser().resolve()
    root=args.results_root.expanduser().resolve()
    if root==source or source in root.parents or root==origin.parent or origin.parent in root.parents or root in origin.parents:
        raise RuntimeError('OP_RESULTS_ROOT_COLLIDES_WITH_SOURCE')
    config=make_config(args,benchmark)
    root.mkdir(parents=True,exist_ok=True)
    conf=root/'run_config.json'
    if conf.is_file():
        if json.loads(conf.read_text(encoding='utf-8'))!=config:raise RuntimeError('OP_CONFIG_DRIFT')
    else:
        if any(root.iterdir()):raise RuntimeError('OP_NONEMPTY_RESULTS_ROOT')
        atomic_json(conf,config)
    torch.manual_seed(args.seed)
    network=ExecutableNumeracy(args.constraint_stage,args.rank,args.residual_scale,args.op_width,args.cumulative_limit).to(device)
    if not args.train_registers:
        network.registers.requires_grad_(False)
    optimizer=torch.optim.AdamW((p for p in network.parameters() if p.requires_grad),
                                lr=args.lr,weight_decay=args.weight_decay)
    stream=h.make_stream(args.seed,tasks,args.sampler)
    replay=OperationReplay(args.buffer_capacity,args.seed+17)
    latest,best_path=root/'latest.pt',root/'best.pt'
    step=0;best={'joint_xy':-1.,'numeric_result':-1.,'result':-1.,'op_accuracy':-1.,'loss':float('inf')}
    source_details=None
    if args.resume or args.eval_only:
        if not latest.is_file():raise FileNotFoundError('OP_OWN_LATEST_MISSING')
        step,best,source_details=restore(latest,network,optimizer,stream,replay,config,identity)
        emit('op_cutover_resumed',step=step,source=source_details,record_count=len(replay.records))
    else:
        if latest.exists():raise RuntimeError('OP_USE_--resume_FOR_EXISTING_RUN')
        source_details=cutover(origin,network,stream,replay,identity,args)
        emit('op_cutover_initialized',source=source_details,
             parameters=sum(p.numel() for p in network.parameters()),
             register_parameters=sum(p.numel() for p in network.registers.parameters()),
             frozen_backbone_parameters_trainable=0,operation_classes=list(TRAIN_OPS),
             source_unchanged=True,replay_records=len(replay.records),
             seen_pairs=len(stream.seen))
        save(latest,network,optimizer,step,config,stream,replay,best,identity,source_details)
    validation=previous.load_eval(source,benchmark,tasks,'val',args.val_examples)
    if args.eval_only:
        for split in ('val','test','control'):
            eval_data=previous.load_eval(source,benchmark,tasks,split)
            emit('op_cutover_final_evaluation',split=split,step=step,**evaluate(network,eval_data,device))
        return 0
    import nanojev_numerate_spine_cassette_smoke as cassette
    _,frozen,tokenizer,source_device,model_info=cassette.load_source('qwen',args)
    if (model_info.get('resolved_commit')!=identity['model_info'].get('resolved_commit') or
        model_info.get('source')!=identity['model_info'].get('source') or
        str(model_info.get('dtype'))!=str(identity['model_info'].get('dtype'))):
        raise RuntimeError('OP_BACKBONE_IDENTITY_DRIFT')
    frozen.eval()
    previous.verify_capture_alignment(torch,frozen,tokenizer,source_device,source,tasks,args.max_prompt_tokens)
    tic=time.monotonic();start_step=step
    for k in range(step+1,args.steps+1):
        batch=stream.batch(args.batch_size)
        features=previous.extract_batched(torch,frozen,tokenizer,
                          [row['prompt'] for row in batch],source_device,args.max_prompt_tokens)
        if features.requires_grad:raise RuntimeError('OP_FROZEN_BACKBONE_REQUIRES_GRAD')
        replay.add(features,batch)
        for _ in range(args.replay_updates):
            feat,nums,operations=replay.sample_labeled(args.per_expert_batch)
            labels,bands,local=b.supervision_fast(nums,device)
            network.train()
            raw,op_logits,gates,correction=network(feat.to(device=device,dtype=torch.float32))
            registration,reg_stats=b.fast_constrained_loss(raw,labels,bands,local,network.registers,
                stage=args.constraint_stage,zero_positive_weight=args.zero_positive_weight,
                expert_weight=args.expert_weight,range_weight=args.range_weight,
                sharing_weight=args.sharing_weight,continuity_weight=args.continuity_weight,
                boundary_window=args.boundary_window)
            math_loss,math_stats=execution_loss(raw,op_logits,labels,bands,operations,
                gates=gates,correction=correction,threshold_weight=args.threshold_weight,
                op_weight=args.op_weight,result_weight=args.result_weight,
                routed_result_weight=args.routed_result_weight,temperature=args.op_temperature)
            loss=(registration if args.train_registers else registration.detach())+math_loss
            optimizer.zero_grad(set_to_none=True);loss.backward()
            torch.nn.utils.clip_grad_norm_(network.parameters(),1.)
            optimizer.step()
        if k%args.report_every==0 or k==args.steps:
            emit('op_cutover_train_progress',step=k,source_step=source_details['source_step'],
                 new_generated_pairs=len(stream.seen)-source_details['source_seen_pairs'],
                 total_seen_pairs=len(stream.seen),replay_records=len(replay.records),
                 training_loss=float(loss.detach()),register_loss=float(registration.detach()),
                 **{name:float(value) for name,value in math_stats.items()},
                 elapsed_seconds=round(time.monotonic()-tic,2))
        if k%args.eval_every==0 or k==args.steps:
            val=evaluate(network,validation,device)
            emit('op_cutover_validation',step=k,**val)
            objective=val['result_5pct_pass_rate'];op_acc=val['op_template_accuracy'] or 0.
            joint=val['joint_xy_5pct_pass_rate']
            numeric=val['numeric_result_pass_rate'] or 0.
            if ((joint,numeric,objective,op_acc) >
                (best['joint_xy'],best['numeric_result'],best['result'],best['op_accuracy'])):
                best={'joint_xy':joint,'numeric_result':numeric,'result':objective,
                      'op_accuracy':op_acc,'loss':float(loss.detach())}
                save(best_path,network,optimizer,k,config,stream,replay,best,identity,source_details)
                emit('op_cutover_best_checkpoint',step=k,**best,path=str(best_path))
        if k%args.checkpoint_every==0 or k==args.steps:
            save(latest,network,optimizer,k,config,stream,replay,best,identity,source_details)
            emit('op_cutover_checkpoint',step=k,path=str(latest))
    emit('op_cutover_complete',step=args.steps,best=best,results_root=str(root))
    return 0


def self_test():
    for name in TRAIN_OPS:
        assert name in OP_TO_PROGRAM
        values=[(3847,2916),(-12,4),(0,0),(2000000,-2000000)]
        for x,y in values:
            hard=exact_execute(OP_TO_PROGRAM[name],x,y)
            soft=execute_tensor(OP_TO_PROGRAM[name],torch.tensor([float(x)]),torch.tensor([float(y)]),smooth=False)
            assert abs(float(soft[0])-hard) <= max(.1, abs(hard)*1e-6)
    probe=torch.randn(7,38,requires_grad=True)
    untouched=c.decode(probe.detach().numpy())
    gates0,correction0=CumulativeMagnitude()(torch.randn(7,h.INPUT_WIDTH))
    corrected=cumulative_numbers(probe,gates0,correction0)
    assert torch.equal(corrected.detach(),torch.tensor(untouched,dtype=torch.float32))
    net=ExecutableNumeracy(op_width=8)
    raw,ops,gates,correction=net(torch.randn(4,h.INPUT_WIDTH))
    nums=torch.tensor([[3847,2916],[-3,4],[7,-1],[0,0]])
    labs,bands,local=b.supervision_fast(nums.numpy(),'cpu')
    op_names=['sym_multiply','sym_sub','conditional_gt','sym_add']
    loss,stats=execution_loss(raw,ops,labs,bands,op_names,gates=gates,correction=correction)
    loss.backward()
    assert torch.isfinite(loss)
    assert net.registers.common_magnitude.weight.grad.abs().sum()>0
    assert net.operations[1].weight.grad.abs().sum()>0
    assert net.cumulative.delta.weight.grad.abs().sum()>0
    assert net.cumulative.threshold_logits.weight.grad.abs().sum()>0
    assert torch.equal(cumulative_target(torch.tensor([[3,0]]))[0,0],torch.tensor([1.,1.,1.,0.,0.]))
    assert torch.all(gates[:,:,1:]<=gates[:,:,:-1]+1e-7)
    emit('cumulative_cutover_self_test_pass',operation_classes=len(TRAIN_OPS),
         hard_arithmetic_verified=True,identity_initialization_verified=True,
         result_backward_to_registers_and_operation=True)


if __name__=='__main__':
    raise SystemExit(main())
