#!/usr/bin/env python3
"""NanoJev v3.5: continuous RESULT-first selection with bottom-heavy training.

A 20-phase cycle retains 16 phases for known intermediaries, 3 for RESULT
and 1 for FINAL. Base experts receive full-valued continuous log-magnitude
supervision; FINAL receives only numerical log-error and relative-improvement
regression, never within-5% classification gradients. The primary best is the
lowest continuous error of refined R0, including the inherited v3.4 fork
baseline as a candidate. Historic 5% hits remain diagnostics. Qwen is frozen,
and the original source and parent checkpoints are immutable. Calibrate only on validation.
"""
from __future__ import annotations

import hashlib
import copy
import math
import json
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

import nanojev_parallel_result_register_search as base
import nanojev_parallel_result_median_v2 as median
import nanojev_parallel_result_final_v3 as legacy
import nanojev_parallel_result_final_closest_v3_1 as closest
import nanojev_parallel_result_final_pairwise_v3_2 as pair
import nanojev_parallel_result_final_errors_v3_3 as parent33
import nanojev_parallel_result_bottom_heavy_v3_4 as parent34

SCHEMA='nanojev.parallel-result-continuous.v3.5'
NAMES=pair.NAMES
N=pair.N
LOCAL_WIDTH=5
IN_WIDTH=base.INPUT_WIDTH+pair.NUMERIC_WIDTH+len(base.OP_CLASSES)+LOCAL_WIDTH


PHASE_NAMES = ('intermediate',)*16 + ('result',)*3 + ('final',)
AUX_DIGITS = 7
AUX_SCALE = math.asinh(float(base.buffered.h.MAX_VALUE))


def phase_for_tick(tick):
    if tick < 0:
        raise ValueError('BOTTOM_INVALID_TICK')
    return PHASE_NAMES[tick % 20]


class OperandAux(nn.Module):
    """Distinct supervised operand coordinates, sign/zero, band and 7 digits.

    This is not a RESULT arbiter: all targets are known operand intermediaries.
    """
    def __init__(self, width=96):
        super().__init__()
        self.trunk=nn.Sequential(nn.LayerNorm(base.INPUT_WIDTH, elementwise_affine=False),
                                 nn.Linear(base.INPUT_WIDTH,width),nn.GELU(),
                                 nn.Linear(width,width),nn.GELU())
        self.coord=nn.Linear(width,2)
        self.sign=nn.Linear(width,2)
        self.zero=nn.Linear(width,2)
        self.band=nn.Linear(width,2*6)
        self.digits=nn.Linear(width,2*AUX_DIGITS*10)

    def forward(self, features):
        h=self.trunk(features.float())
        return (self.coord(h),self.sign(h),self.zero(h),
                self.band(h).view(-1,2,6),self.digits(h).view(-1,2,AUX_DIGITS,10))

    @torch.no_grad()
    def decode(self,features):
        coordinate,*_=self(features)
        u=np.clip(coordinate.cpu().numpy().astype(np.float64),-1.,1.)
        predicted=np.rint(np.sinh(u*AUX_SCALE))
        return np.clip(predicted,-base.buffered.h.MAX_VALUE,base.buffered.h.MAX_VALUE).astype(np.int64)


def operand_aux_objective(outputs,xy):
    """Known intermediary labels, not inferred RESULT or oracle gating."""
    coordinates,sign,zero,band,digits=outputs
    t=np.asarray(xy,dtype=np.int64)
    if t.ndim!=2 or t.shape[1]!=2 or np.any(np.abs(t)>base.buffered.h.MAX_VALUE):
        raise ValueError('BOTTOM_OPERAND_RANGE_OR_SHAPE')
    device=coordinates.device
    labels=torch.as_tensor(t,device=device,dtype=torch.long)
    coordinate_targets=torch.as_tensor(np.arcsinh(t.astype(np.float64))/AUX_SCALE,
                                      device=device,dtype=torch.float32)
    nonzero=labels!=0
    continuous=F.smooth_l1_loss(coordinates,coordinate_targets,beta=.04)
    sign_loss=(F.binary_cross_entropy_with_logits(sign,(labels>0).float(),reduction='none') *
               nonzero.float()).sum()/nonzero.float().sum().clamp_min(1.)
    zero_loss=F.binary_cross_entropy_with_logits(zero,(~nonzero).float())
    band_truth=torch.as_tensor(base.buffered.h.band_of(t),device=device,dtype=torch.long)
    route=F.cross_entropy(band.reshape(-1,6),band_truth.reshape(-1))
    absval=np.abs(t)
    digit_truth=np.stack([(absval // (10**k)) % 10 for k in range(AUX_DIGITS)],axis=-1)
    digit_target=torch.as_tensor(digit_truth,device=device,dtype=torch.long)
    place=F.cross_entropy(digits.reshape(-1,10),digit_target.reshape(-1))
    loss=3.*continuous + .5*sign_loss + .5*zero_loss + route + place
    return loss,{'coordinate':float(continuous.detach()),'sign':float(sign_loss.detach()),
                 'zero':float(zero_loss.detach()),'regime':float(route.detach()),
                 'digits':float(place.detach())}


def continuous_expert_loss(raw, xy):
    """Differentiable true-route log-magnitude supervision, balanced per operand.

    Truth determines the supervised expert, NEVER the inference route. The
    predicted log-magnitude is reconstructed from the expert coordinate using
    its real band limits, so all magnitude losses have the same physical units.
    """
    actual=np.asarray(xy,dtype=np.int64)
    if actual.ndim!=2 or actual.shape[1]!=2:
        raise ValueError('CONTINUOUS_OPERAND_SHAPE')
    bands=base.buffered.h.band_of(actual)
    experts=raw.float().reshape(-1,2,19)[:,:,7:]
    pos=torch.as_tensor((actual>0).astype(np.int64),device=raw.device)
    bucket=torch.as_tensor(bands*2,device=raw.device,dtype=torch.long)+pos
    selected=experts.gather(2,bucket.unsqueeze(-1)).squeeze(-1)
    lo=np.empty_like(actual,dtype=np.float64)
    hi=np.empty_like(actual,dtype=np.float64)
    for band in range(base.buffered.h.REGIMES):
        a,b=base.buffered.h.band_limits(band)
        lo[bands==band]=a;hi[bands==band]=b
    lo=torch.as_tensor(np.log(lo),device=raw.device,dtype=torch.float32)
    hi=torch.as_tensor(np.log(hi),device=raw.device,dtype=torch.float32)
    predicted=lo+(selected+1.)*.5*(hi-lo)
    gold=torch.as_tensor(np.log(np.maximum(1,np.abs(actual)).astype(np.float64)),
                         device=raw.device,dtype=torch.float32)
    mask=torch.as_tensor(actual!=0,device=raw.device)
    terms=[];per=[]
    for i in (0,1):
        if mask[:,i].any():
            # Smooth L1 protects the tails without reducing training to a
            # pass/fail 5-percent band. Both operands carry equal weight.
            term=F.smooth_l1_loss(predicted[:,i][mask[:,i]],gold[:,i][mask[:,i]],beta=.08)
        else:term=selected[:,i].sum()*0.
        terms.append(term);per.append(float(term.detach()))
    return .5*(terms[0]+terms[1]),{'x_log_magnitude':per[0],'y_log_magnitude':per[1]}


def hierarchy_objective(network,features,xy,continuous_weight=1.0):
    values,bands,local=base.buffered.supervision_fast(xy,features.device)
    raw=network(features.float())
    loss,stats=base.buffered.fast_constrained_loss(raw,values,bands,local,network,
               stage='continuity',continuity_weight=.03)
    full,details=continuous_expert_loss(raw,xy)
    measured=base.buffered.compact_stats(stats)
    measured.update(details)
    measured['continuous_weight']=float(continuous_weight)
    return loss+continuous_weight*full,measured


@torch.no_grad()
def current_xy(model,features):
    pred=base.constrained.decode(model(features.float()).cpu().numpy())
    return pred.astype(np.int64)


def update_intermediaries(operand_decoder,decoder_optimizer,aux,aux_optimizer,rows,device,
                           continuous_weight=1.0):
    features=torch.stack([r[0] for r in rows]).to(device,dtype=torch.float32)
    xy=np.asarray([[r[1],r[2]] for r in rows],dtype=np.int64)
    operand_decoder.train();aux.train()
    hierarchy_loss,h_stats=hierarchy_objective(operand_decoder,features,xy,continuous_weight)
    aux_loss,aux_stats=operand_aux_objective(aux(features),xy)
    decoder_optimizer.zero_grad(set_to_none=True)
    aux_optimizer.zero_grad(set_to_none=True)
    (hierarchy_loss+aux_loss).backward()
    nn.utils.clip_grad_norm_(operand_decoder.parameters(),1.)
    nn.utils.clip_grad_norm_(aux.parameters(),1.)
    decoder_optimizer.step();aux_optimizer.step()
    return float((hierarchy_loss+aux_loss).detach()),{'hierarchy':h_stats,'aux':aux_stats}


def update_result_heads(operand_decoder,networks,head_opts,op_probe,median_net,median_opt,rows,device):
    features=torch.stack([r[0] for r in rows]).to(device,dtype=torch.float32)
    xy=np.asarray([[r[1],r[2]] for r in rows],dtype=np.int64)
    truth,boolmask=base.truth_for([{'a':r[1],'b':r[2],'operation':r[3]} for r in rows])
    with torch.no_grad():
        derived=current_xy(operand_decoder,features)
        op_ids=op_probe(features).argmax(1)
    ops=[base.OP_CLASSES[int(k)] for k in op_ids.detach().cpu().numpy()]
    reference=torch.as_tensor(base.reference_results(derived,ops),device=device,dtype=torch.float32)
    design=base.features_with_op(features,op_ids,reference)
    bool_t=torch.as_tensor(boolmask,device=device,dtype=torch.bool)
    truth_t=torch.as_tensor(truth,device=device,dtype=torch.float32)
    losses={}
    for name,model in networks.items():
        model.train()
        output=model(design)
        loss,_=model.objective(output,truth_t,bool_t,reference,exact_values=truth)
        # This is differentiable in the two heads with continuous numerical
        # outputs. The other heads still receive their structural supervision.
        if name in ('continuous','residual') and (~bool_t).any():
            target=torch.as_tensor(base.signed_encode(truth[~boolmask]),device=device,dtype=torch.float32)
            raw=output[0][~bool_t,0]
            coordinate=raw if name=='continuous' else raw+torch.asinh(reference[~bool_t])/base.SCALE
            loss=loss+F.smooth_l1_loss(coordinate,target,beta=.03)
        head_opts[name].zero_grad(set_to_none=True)
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(),1.)
        head_opts[name].step()
        losses[name]=float(loss.detach())
    # After the RESULT heads have updated, supervise the learned median on the
    # current candidate vector. No gradient leaks into the operands here.
    with torch.no_grad():
        ids,ref,first8,inferred_bool=median.candidates_for_rows(networks,op_probe,features,derived)
        direct,_=median.median_controls(first8,inferred_bool)
        x=median.median_design(features,ids,ref,first8,direct)
    median_net.train()
    mloss=median_net.objective(median_net(x),direct,truth,boolmask)
    median_opt.zero_grad(set_to_none=True)
    mloss.backward()
    nn.utils.clip_grad_norm_(median_net.parameters(),1.)
    median_opt.step()
    losses['median_regressed']=float(mloss.detach())
    return float(np.mean(list(losses.values()))),losses


def continuous_final_objective(models,x,proposals,truth,boolean,relative_weight=.2):
    numeric,relative,logerr,_=error_targets(proposals,truth,boolean)
    if not numeric.any():
        return sum((net.net[-1].weight.sum()*0. for net in models.values()),x.sum()*0.),{'rows':0}
    valid=torch.as_tensor(numeric,device=x.device)
    target=torch.as_tensor(np.clip(logerr[numeric],0.,20.),device=x.device,dtype=torch.float32)
    predicted=torch.stack([models[name](x[valid,i])[0] for i,name in enumerate(NAMES)],dim=1)
    absolutes=F.smooth_l1_loss(predicted,target,beta=.25,reduction='none').mean(0)
    # Comparing absolute-error estimates needs their DIFFERENCE to be reliable.
    # This paired continuous target has no threshold, hard ranking or oracle
    # selection at inference.
    relative_pred=predicted[:,1:]-predicted[:,:1]
    relative_truth=target[:,1:]-target[:,:1]
    relative_loss=F.smooth_l1_loss(relative_pred,relative_truth,beta=.25)
    loss=absolutes.mean()+relative_weight*relative_loss
    return loss,{'rows':int(numeric.sum()),'regression':float(absolutes.mean().detach()),
                 'relative_regression':float(relative_loss.detach()),'within5_training_weight':0.}


def update_final(operand_decoder,networks,op_probe,median_net,estimators,error_opts,rows,device,args):
    features=torch.stack([r[0] for r in rows]).to(device,dtype=torch.float32)
    truth,boolean=base.truth_for([{'a':r[1],'b':r[2],'operation':r[3]} for r in rows])
    with torch.no_grad():
        xy=current_xy(operand_decoder,features)
        proposals,_,_,predops=pair.model_inputs(networks,op_probe,median_net,features,xy)
        x=design(features,proposals,predops)
    for net in estimators.values():net.train()
    loss,parts=continuous_final_objective(estimators,x,proposals,truth,boolean,
                                           relative_weight=args.relative_error_weight)
    for opt in error_opts.values():opt.zero_grad(set_to_none=True)
    loss.backward()
    for net in estimators.values():nn.utils.clip_grad_norm_(net.parameters(),1.)
    for opt in error_opts.values():opt.step()
    return float(loss.detach()),parts

def operand_metrics(operand_decoder,aux,frozen_source,op_probe,rows,device,batch_size=64):
    features,xy,ops,_=rows
    hierarchy=[];auxiliary=[];original=[];predops=[]
    with torch.inference_mode():
        for start in range(0,len(xy),batch_size):
            z=torch.as_tensor(features[start:start+batch_size],device=device,dtype=torch.float32)
            hierarchy.append(current_xy(operand_decoder,z))
            auxiliary.append(aux.decode(z))
            original.append(base.source_decode(frozen_source,z))
            predops.extend(base.OP_CLASSES[int(k)] for k in op_probe(z).argmax(1).cpu().numpy())
    actual=np.asarray(xy,dtype=np.int64)
    h=np.concatenate(hierarchy);a=np.concatenate(auxiliary);o=np.concatenate(original)
    def measures(pred):
        mask=base.previous.correctness(pred,actual)
        rel=np.abs(pred.astype(np.float64)-actual)/np.maximum(np.abs(actual),1.)
        signed=np.abs(np.arcsinh(pred.astype(np.float64))-np.arcsinh(actual.astype(np.float64)))
        return {'x_5pct':float(mask[:,0].mean()),'y_5pct':float(mask[:,1].mean()),
                'joint_5pct':float(mask.all(axis=1).mean()),
                'x_signed_asinh_mae':float(signed[:,0].mean()),
                'y_signed_asinh_mae':float(signed[:,1].mean()),
                'xy_relative_mae':float(rel.mean()),
                'sign_accuracy':float(np.mean((np.sign(pred)==np.sign(actual))))}
    truth,boolean=base.truth_for([{'a':int(x),'b':int(y),'operation':op} for (x,y),op in zip(actual,ops)])
    result={}
    for name,pred in (('original_frozen',o),('hierarchy_refined',h),('aux_continuous',a)):
        proposal=base.reference_results(pred,predops)
        record=measures(pred)
        record['executed_result_numeric_5pct']=base.diagnostic(proposal,truth,boolean)['numeric_5pct']
        record['executed_result_numeric_log_error']=float(np.log1p(np.abs(proposal[~boolean]-truth[~boolean])/
                       np.maximum(1.,np.abs(truth[~boolean]))).mean()) if (~boolean).any() else None
        result[name]=record
    return result


def local_features(proposals):
    """Per-candidate data, independent of the true RESULT."""
    c=np.asarray(proposals,dtype=np.float64)
    if c.ndim!=2 or c.shape[1]!=N or not np.isfinite(c).all():
        raise ValueError('BOTTOM_INVALID_PROPOSALS')
    z=base.signed_encode(c)
    median_z=np.median(z,axis=1,keepdims=True)
    dispersion=np.median(np.abs(z-median_z),axis=1,keepdims=True)
    stacked=np.stack((z,z-z[:,0:1],z-median_z,
                      np.abs(z-median_z)/(dispersion+1e-3),
                      np.broadcast_to(dispersion,z.shape)),axis=-1).astype(np.float32)
    if not np.isfinite(stacked).all():raise ValueError('BOTTOM_NONFINITE_LOCAL_FEATURES')
    return stacked


def design(features,proposals,pred_ops):
    shared=pair.feature_design(features,proposals,pred_ops)['combined']
    local=torch.as_tensor(local_features(proposals),device=shared.device)
    full=shared[:,None,:].expand(-1,N,-1)
    return torch.cat((full,local),dim=-1)


class ErrorEstimator(nn.Module):
    """Separate weights and optimizer for a single register; two output tasks."""
    def __init__(self,width=96):
        super().__init__()
        self.net=nn.Sequential(nn.LayerNorm(IN_WIDTH,elementwise_affine=False),
                               nn.Linear(IN_WIDTH,width),nn.GELU(),
                               nn.Linear(width,width),nn.GELU(),nn.Linear(width,2))
        nn.init.zeros_(self.net[-1].bias)

    def forward(self,features):
        raw=self.net(features.float())
        return F.softplus(raw[:,0]),raw[:,1]


def error_targets(proposals,truth,is_boolean):
    rel,logged=closest.numerical_errors(proposals,truth)
    numeric=~np.asarray(is_boolean,dtype=bool)
    hits=rel<=.05
    return numeric,rel,logged,hits


def errors_objective(models,x,proposals,truth,boolean,bce_weight=.25,clip_log=20.):
    numeric,relative,logerr,hits=error_targets(proposals,truth,boolean)
    if not numeric.any():
        zero=sum((model.net[-1].weight.sum()*0. for model in models.values()),
                 x.sum()*0.)
        return zero,{'rows':0,'per_head':{}}
    mask=torch.as_tensor(numeric,device=x.device)
    truth_log=torch.as_tensor(np.clip(logerr[numeric],0.,clip_log),dtype=torch.float32,device=x.device)
    truth_hit=torch.as_tensor(hits[numeric],dtype=torch.float32,device=x.device)
    losses=[];parts={}
    for i,name in enumerate(NAMES):
        prediction,correct_logits=models[name](x[mask,i])
        reg=F.smooth_l1_loss(prediction,truth_log[:,i],beta=.25)
        cls=F.binary_cross_entropy_with_logits(correct_logits,truth_hit[:,i],reduction='none')
        # Balanced positive/negative labels. If the minibatch has no positives,
        # keep the negative supervision rather than inventing positive labels.
        positive=truth_hit[:,i].sum().item();n=len(truth_hit)
        if 0<positive<n:
            weights=torch.where(truth_hit[:,i]>.5,
                torch.full_like(cls,min(8.,n/(2.*positive))),
                torch.full_like(cls,min(8.,n/(2.*(n-positive)))))
            bce=(cls*weights).sum()/weights.sum()
        else:bce=cls.mean()
        losses.append(reg+bce_weight*bce)
        parts[name]={'regression':round(float(reg.detach()),5),
                     'within5_bce':round(float(bce.detach()),5),
                     'within5_count':int(truth_hit[:,i].sum().item())}
    return torch.stack(losses).sum(),{'rows':int(numeric.sum()),'per_head':parts,
                                      'all_wrong_rows':int((~hits[numeric].any(axis=1)).sum())}


def predict_all(models,x):
    estimated=[];logits=[]
    for i,name in enumerate(NAMES):
        est,cls=models[name](x[:,i])
        estimated.append(est);logits.append(cls)
    return torch.stack(estimated,dim=1),torch.stack(logits,dim=1)


def select_min_error(estimated,bool_pred,offsets=None):
    predicted=np.asarray(estimated,dtype=np.float64)
    if offsets is not None:predicted=predicted+np.asarray(offsets,dtype=np.float64)[None,:]
    predicted=np.maximum(predicted,0.)
    indices=predicted.argmin(axis=1).astype(np.int64)
    indices[np.asarray(bool_pred,dtype=bool)]=0
    return indices,predicted


def calibrate_offsets(predicted,true_logged,true_boolean,indices=None):
    """Train only eleven candidate-wise intercepts, with a deterministic split.

    Offsets are signed mean residuals. They cannot inspect test or control.
    Validation calibration rows are disjoint from validation-selection rows.
    """
    p=np.asarray(predicted,dtype=np.float64)
    t=np.asarray(true_logged,dtype=np.float64)
    mask=~np.asarray(true_boolean,dtype=bool)
    if indices is not None:
        keep=np.zeros(len(mask),dtype=bool);keep[np.asarray(indices,dtype=np.int64)]=True
        mask &= keep
    if not mask.any():return np.zeros(N,dtype=np.float64)
    # Evaluation targets are genuine log errors, with no clipping for calibration.
    residual=t[mask]-p[mask]
    return np.clip(np.mean(residual,axis=0),-2.,2.)


def per_candidate_diagnostics(estimated,true_logged,true_relative,bool_true,hit_logits=None):
    valid=~np.asarray(bool_true,dtype=bool)
    if not valid.any():return {}
    report={}
    for i,name in enumerate(NAMES):
        p=np.asarray(estimated)[valid,i].astype(float)
        t=np.asarray(true_logged)[valid,i].astype(float)
        truth_hits=np.asarray(true_relative)[valid,i]<=.05
        predicted_hits=np.expm1(np.maximum(p,0.))<=.05
        delta=p-t
        r=float(np.corrcoef(p,t)[0,1]) if np.std(p)>1e-12 and np.std(t)>1e-12 else None
        item={'n':len(p),'mae_log_error':float(np.mean(np.abs(delta))),
                      'rmse_log_error':float(np.sqrt(np.mean(delta**2))),
                      'signed_bias':float(np.mean(delta)),
                      'estimated_mean_log_error':float(np.mean(p)),
                      'actual_mean_log_error':float(np.mean(t)),
                      'pearson':r,
                      'within5_true_rate':float(np.mean(truth_hits)),
                      'within5_predicted_rate':float(np.mean(predicted_hits))}
        if hit_logits is not None:
            cls=np.asarray(hit_logits)[valid,i].astype(float)
            probabilities=1./(1.+np.exp(-np.clip(cls,-50.,50.)))
            item['within5_classifier_brier']=float(np.mean((probabilities-truth_hits)**2))
            item['within5_classifier_mean_probability']=float(np.mean(probabilities))
        report[name]=item
    return report


@torch.inference_mode()
def data_capture(networks,op_probe,source,median_net,closest_net,comparators,pairwise,estimators,eval_rows,device,batch_size=64):
    features,xy,ops,_families=eval_rows
    proposals=[];is_bool_pred=[];predicted_ops=[];err=[];hit_logits=[];pair_logits=[];binary={k:[] for k in pair.ABLATIONS}
    old_ids=[]
    for start in range(0,len(xy),batch_size):
        end=min(start+batch_size,len(xy))
        f=torch.as_tensor(features[start:end],device=device,dtype=torch.float32)
        source_xy=base.source_decode(source,f)
        c,d,b,ops_idx=pair.model_inputs(networks,op_probe,median_net,f,source_xy)
        x=design(f,c,ops_idx)
        estimates,logits=predict_all(estimators,x)
        proposals.append(c);is_bool_pred.append(b);predicted_ops.extend(base.OP_CLASSES[int(i)] for i in ops_idx.detach().cpu().numpy())
        err.append(estimates.detach().cpu().numpy());hit_logits.append(logits.detach().cpu().numpy())
        pd=pair.feature_design(f,c,ops_idx)
        for name in pair.ABLATIONS:
            lg,_=comparators[name](pd[name]);binary[name].append(lg.detach().cpu().numpy())
        pair_logits.append(pairwise(pd['combined']).detach().cpu().numpy())
        old=legacy.final_design(f,ops_idx,torch.as_tensor(c[:,0],device=device,dtype=torch.float32),
                                c,np.median(c[:,:len(median.CANDIDATES)],axis=1),op_probe)
        old_id,_=closest.closest_predictions(closest_net(old),b)
        old_ids.extend(old_id.tolist())
    return {'proposals':np.concatenate(proposals),'inferred_bool':np.concatenate(is_bool_pred),
            'ops_pred':predicted_ops,'estimated':np.concatenate(err),
            'hit_logits':np.concatenate(hit_logits),
            'pair_logits':np.concatenate(pair_logits),
            'binary':{name:np.concatenate(items) for name,items in binary.items()},
            'old_ids':np.asarray(old_ids,dtype=np.int64)}


def assess(data,eval_rows,offsets=None,subset=None,frontiers=False,thresholds=None):
    _features,xy,operations,_families=eval_rows
    truth=np.asarray([base.result_value(int(a),int(b),op) for (a,b),op in zip(xy,operations)],dtype=np.float64)
    boolean=np.asarray([op in base.BOOL_OPS for op in operations],dtype=bool)
    c=data['proposals'];b=data['inferred_bool'];p=data['estimated']
    relative,logged=closest.numerical_errors(c,truth)
    if subset is None:indices=np.arange(len(truth))
    else:indices=np.asarray(subset,dtype=np.int64)
    if len(indices)==0:raise ValueError('BOTTOM_EMPTY_EVALUATION_SUBSET')
    source_ids=np.zeros(len(truth),dtype=np.int64)
    raw_ids,raw_error=select_min_error(p,b)
    cal_ids,cal_error=select_min_error(p,b,offsets)
    # Independent 5%-probability auxiliary policy: selects a candidate always.
    class_ids=np.argmax(data['hit_logits'],axis=1).astype(np.int64)
    class_ids[b]=0
    pair_scores=pair.pair_scores(torch.as_tensor(data['pair_logits'])).numpy()
    pair_ids=pair.choose_ids(pair_scores,b)
    binary_ids={name:np.where((v>0)&(~b),pair.RESIDUAL_INDEX,0).astype(np.int64)
                for name,v in data['binary'].items()}
    all_ids={'source':source_ids,'closest_v3_1':data['old_ids'],
             'pairwise_11':pair_ids,'error_raw':raw_ids,'error_calibrated':cal_ids,
             'within5_classifier':class_ids,**binary_ids}
    report={'numeric_rows':int((~boolean[indices]).sum()),
            'operation_accuracy':float(np.mean(np.asarray(data['ops_pred'])[indices]==np.asarray(operations)[indices])),
            'per_candidate':per_candidate_diagnostics(p[indices],logged[indices],relative[indices],boolean[indices],data['hit_logits'][indices]),
            'per_candidate_calibrated':per_candidate_diagnostics(cal_error[indices],logged[indices],relative[indices],boolean[indices],data['hit_logits'][indices]),
            'policy':{},'source_oracle':None}
    for name,ids in all_ids.items():
        report['policy'][name]=pair.measure(c[indices],truth[indices],boolean[indices],ids[indices])
    report['source_oracle']=report['policy']['source']['oracle_numeric_5pct']
    report['error_calibration']={'offsets':{name:float(v) for name,v in zip(NAMES,np.zeros(N) if offsets is None else offsets)},
                                 'mean_abs_offset':float(np.mean(np.abs(np.zeros(N) if offsets is None else offsets)))}
    raw_reference=report['policy']['source']
    report['delta_vs_source']={name:{'numeric_5pct':round(metrics['numeric_5pct']-raw_reference['numeric_5pct'],8),
                                     'mean_log1p_error':round(metrics['mean_log1p_relative_error']-raw_reference['mean_log1p_relative_error'],8)}
                               for name,metrics in report['policy'].items()}
    if frontiers:
        report['validation_frontiers']={}
        for name,ids,estimated in [('error_raw',raw_ids,raw_error),('error_calibrated',cal_ids,cal_error),
                                    ('pairwise_11',pair_ids,None)]:
            if estimated is None:signal=pair_scores[np.arange(len(pair_ids)),pair_ids]-pair_scores[:,0]
            else:signal=estimated[:,0]-estimated[np.arange(len(ids)),ids]
            report['validation_frontiers'][name]=pair.threshold_frontier(c[indices],truth[indices],boolean[indices],
                                                                         ids[indices],signal[indices])
    if thresholds:
        report['calibrated_policies_from_validation']={}
        for name,points in thresholds.items():
            original_id={'error_raw':raw_ids,'error_calibrated':cal_ids,'pairwise_11':pair_ids}[name]
            signal=(pair_scores[np.arange(len(pair_ids)),pair_ids]-pair_scores[:,0]
                    if name=='pairwise_11' else
                    (raw_error if name=='error_raw' else cal_error)[:,0] -
                    (raw_error if name=='error_raw' else cal_error)[np.arange(len(raw_ids)),original_id])
            nested={}
            for target,threshold in points.items():
                final_id=np.where((~b)&(original_id!=0)&(signal>=float(threshold)),original_id,0)
                nested[target]=pair.measure(c[indices],truth[indices],boolean[indices],final_id[indices])
            report['calibrated_policies_from_validation'][name]=nested
    return report


def continuous_quantile_report(data,rows,subset=None):
    _f,xy,operations,_families=rows
    y=np.asarray([base.result_value(int(a),int(b),op) for (a,b),op in zip(xy,operations)],dtype=np.float64)
    numeric=~np.asarray([op in base.BOOL_OPS for op in operations],dtype=bool)
    if subset is not None:
        include=np.zeros(len(y),dtype=bool)
        include[np.asarray(subset,dtype=np.int64)]=True
        numeric &= include
    if not numeric.any():return {}
    proposals=np.asarray(data['proposals'],dtype=np.float64)[numeric]
    gold=y[numeric]
    logged=np.log1p(np.abs(proposals-gold[:,None])/np.maximum(1.,np.abs(gold[:,None])))
    out={}
    for i,name in enumerate(NAMES):
        v=logged[:,i]
        out[name]={'mean':float(np.mean(v)),'median':float(np.median(v)),
                   'p90':float(np.quantile(v,.9)),'p99':float(np.quantile(v,.99))}
    return out


def parser():
    p=parent34.parser();p.description=__doc__
    p.set_defaults(results_root=Path.home()/'NanoJev'/'runs'/'parallel_result_continuous_v3_5')
    p.add_argument('--fork-v3-4-root',type=Path,default=None)
    p.add_argument('--continuous-operand-weight',type=float,default=1.0)
    p.add_argument('--relative-error-weight',type=float,default=.2)
    return p


def config_for(args,manifest,sha,identity):
    return {'schema':SCHEMA,'parent_config':parent34.config_for(args,manifest,sha,identity),
            'continuous_operand_weight':args.continuous_operand_weight,
            'relative_error_weight':args.relative_error_weight,
            'phase_cycle':list(PHASE_NAMES),
            'objective':'continuous_result_mean_log1p_relative_error',
            'final_loss':'continuous_absolute_and_source_relative_log_error',
            'within5_training_weight':0.,'within5_checkpoint_weight':0.,
            'val':'even_calibration_odd_checkpoint_selection',
            'backbone_frozen':True,'source_and_parent_frozen':True}


def pack_ancestor(step,cfg,networks,opts,op_probe,op_opt,stream,replay,best,median_net,median_opt,mb,rb,
         old_net,old_opt,old_best,old_threshold,closest_net,closest_opt,closest_best,
         comparators,pairwise,optimizers,experiment_best,estimators,error_opts,error_best):
    ck=pair.pack(step,cfg['parent_config']['parent_config'],networks,opts,op_probe,op_opt,stream,replay,best,
                 median_net,median_opt,mb,rb,old_net,old_opt,old_best,old_threshold,
                 closest_net,closest_opt,closest_best,comparators,pairwise,optimizers,experiment_best)
    ck.update({'schema':SCHEMA,'config':cfg,
               'error_estimators':{k:v.state_dict() for k,v in estimators.items()},
               'error_optimizers':{k:v.state_dict() for k,v in error_opts.items()},
               'error_best':error_best})
    return ck



def execute(args):
    if args.self_test:return self_test()
    if args.train_candidates:raise ValueError('CONTINUOUS_--train-candidates_UNSUPPORTED_USE_80_15_5_SCHEDULE')
    if any(x is not None for x in (args.fork_v1_root,args.fork_v2_root,args.fork_v3_root,args.fork_v3_1_root,args.fork_v3_2_root,args.fork_v3_3_root)):
        raise ValueError('CONTINUOUS_FORK_ONLY_FROM_V3_4')
    if args.fork_v3_4_root is not None and (args.resume or args.eval_only):raise ValueError('BOTTOM_FORK_RESUME_EXCLUSIVE')
    if args.fork_parent_best and args.fork_v3_4_root is None:raise ValueError('BOTTOM_FORK_PARENT_BEST_REQUIRES_FORK')
    if args.error_width<8 or args.within5_weight<0 or args.error_val_examples<2 or args.aux_width<8 or min(args.intermediate_lr,args.result_lr,args.final_lr)<=0 or args.continuous_operand_weight<0 or args.relative_error_weight<0:raise ValueError('BOTTOM_INVALID_HEAD_CONFIG')
    if args.eval_best and not args.eval_only:raise ValueError('BOTTOM_EVAL_BEST_ONLY_WITH_EVAL_ONLY')
    if args.additional_steps is not None and args.additional_steps<1:raise ValueError('BOTTOM_INVALID_ADDITIONAL_STEPS')
    if args.threads>0:torch.set_num_threads(args.threads)
    if args.device=='cuda' and not torch.cuda.is_available():raise RuntimeError('BOTTOM_CUDA_UNAVAILABLE')
    device=torch.device(args.device)
    benchmark=args.v3_root.expanduser().resolve()
    manifest,tasks=base.previous.read_benchmark(benchmark)
    identity=json.loads((benchmark/'model_identity.json').read_text(encoding='utf-8'))
    source,sha=base.source_factory(args.source_checkpoint,identity,device)
    cfg=config_for(args,manifest,sha,identity)
    root=args.results_root.expanduser().resolve()
    fork=args.fork_v3_4_root.expanduser().resolve() if args.fork_v3_4_root is not None else None
    source_root=args.source_checkpoint.expanduser().resolve().parent
    if (root==benchmark or benchmark in root.parents or root==source_root or source_root in root.parents or
        (fork is not None and (root==fork or fork in root.parents or root in fork.parents))):
        raise RuntimeError('BOTTOM_UNSAFE_RESULTS_ROOT')
    latest=root/'latest.pt'
    if args.resume or args.eval_only:
        if not latest.is_file():raise RuntimeError('CONTINUOUS_RESUME_REQUIRES_LATEST')
        source_file=(root/'continuous_result'/'best.pt') if args.eval_best else latest
        if not source_file.is_file():raise RuntimeError('CONTINUOUS_BEST_CHECKPOINT_MISSING')
        peek=torch.load(source_file,map_location='cpu',weights_only=True)
        if peek.get('schema')!=SCHEMA:raise RuntimeError('CONTINUOUS_SCHEMA_MISMATCH')
        expected_cfg=cfg
    else:
        if fork is None:raise RuntimeError('CONTINUOUS_REQUIRES_--fork-v3-4-root_OR_--resume')
        source_file=(fork/'intermediate_primary'/'best.pt') if args.fork_parent_best else (fork/'latest.pt')
        if not source_file.is_file():raise RuntimeError('CONTINUOUS_FORK_REQUIRES_V3_4_CHECKPOINT')
        if latest.exists():raise RuntimeError('CONTINUOUS_RUN_EXISTS_USE_--resume')
        peek=torch.load(source_file,map_location='cpu',weights_only=True)
        if peek.get('schema')!=parent34.SCHEMA:raise RuntimeError('CONTINUOUS_PARENT_SCHEMA_MISMATCH')
        expected_cfg=cfg['parent_config']
    inherited=int(peek['step'])
    target=inherited+args.additional_steps if args.additional_steps is not None else args.steps
    if not args.eval_only and target<=inherited:
        raise RuntimeError(f'CONTINUOUS_TARGET_NOT_ABOVE_CHECKPOINT:{inherited}; USE_--additional-steps 500')
    if peek.get('config')!=expected_cfg:raise RuntimeError('CONTINUOUS_CONFIG_DRIFT')
    if not (args.resume or args.eval_only) and root.exists() and any(root.iterdir()):
        raise RuntimeError('BOTTOM_NONEMPTY_RESULTS_ROOT')
    root.mkdir(parents=True,exist_ok=True)
    cfg_file=root/'run_config.json'
    if cfg_file.exists():
        if json.loads(cfg_file.read_text(encoding='utf-8'))!=cfg:raise RuntimeError('CONTINUOUS_CONFIG_FILE_DRIFT')
    else:base.atomic_json(cfg_file,cfg)
    torch.manual_seed(args.seed)
    networks={n:base.ResultHead(n,args.width).to(device) for n in base.NAMES}
    opts={n:torch.optim.AdamW(net.parameters(),lr=args.lr,weight_decay=args.weight_decay) for n,net in networks.items()}
    op_probe=base.SharedOperationProbe().to(device)
    op_opt=torch.optim.AdamW(op_probe.parameters(),lr=args.lr,weight_decay=args.weight_decay)
    median_net=median.MedianResidual(args.median_width).to(device)
    median_opt=torch.optim.AdamW(median_net.parameters(),lr=args.lr,weight_decay=args.weight_decay)
    old_net=legacy.FinalHead(args.final_width).to(device)
    old_opt=torch.optim.AdamW(old_net.parameters(),lr=args.lr,weight_decay=args.weight_decay)
    closest_net=closest.ClosestFinal(args.closest_width).to(device)
    closest_opt=torch.optim.AdamW(closest_net.parameters(),lr=args.lr,weight_decay=args.weight_decay)
    comparators={name:pair.BinaryComparator(pair.NUMERIC_WIDTH if name=='numeric_only' else
                    base.INPUT_WIDTH+len(base.OP_CLASSES) if name=='backbone_only' else
                    base.INPUT_WIDTH+pair.NUMERIC_WIDTH+len(base.OP_CLASSES),args.comparison_width).to(device)
                 for name in pair.ABLATIONS}
    pairwise=pair.PairwiseRanker(base.INPUT_WIDTH+pair.NUMERIC_WIDTH+len(base.OP_CLASSES),args.comparison_width).to(device)
    control_opts={name:torch.optim.AdamW(net.parameters(),lr=args.lr,weight_decay=args.weight_decay)
                  for name,net in {**comparators,'pairwise_11':pairwise}.items()}
    estimators={name:ErrorEstimator(args.error_width).to(device) for name in NAMES}
    error_opts={name:torch.optim.AdamW(model.parameters(),lr=args.lr,weight_decay=args.weight_decay)
                for name,model in estimators.items()}
    stream=base.previous.FreshPairs(args.seed,base.previous.reserved_pairs(tasks))
    replay=base.Replay(args.replay_capacity,args.seed+71)
    payload={**peek,'schema':closest.SCHEMA,'config':cfg['parent_config']['parent_config']['parent_config']['parent_config']}
    step,best,mb,rb,old_best,old_threshold,closest_best=closest.load_payload(
        payload,cfg['parent_config']['parent_config']['parent_config']['parent_config'],networks,opts,op_probe,op_opt,stream,replay,
        median_net,median_opt,old_net,old_opt,closest_net,closest_opt)
    for name,net in comparators.items():net.load_state_dict(peek['comparators'][name])
    pairwise.load_state_dict(peek['pairwise_11'])
    for name,opt in control_opts.items():opt.load_state_dict(peek['experiment_optimizers'][name])
    experiment_best=dict(peek['experiment_best'])
    error_best={'step':0,'log_error':float('inf'),'numeric_5pct':-1.}
    # v3.3 estimators are initialized from the inherited checkpoint, then trained
    # only on the FINAL phase of this new independent lineage.
    for name,net in estimators.items():net.load_state_dict(peek['error_estimators'][name])
    for name,opt in error_opts.items():opt.load_state_dict(peek['error_optimizers'][name])
    error_best=dict(peek['error_best'])
    for net in [op_probe,old_net,closest_net,*comparators.values(),pairwise]:
        net.eval()
        for q in net.parameters():q.requires_grad_(False)
    # Clone the original 38-head decoder: never write into source.pt or parent.
    operand_decoder=copy.deepcopy(source).to(device)
    for q in operand_decoder.parameters():q.requires_grad_(True)
    decoder_opt=torch.optim.AdamW(operand_decoder.parameters(),lr=args.intermediate_lr,
                                   weight_decay=args.weight_decay)
    aux=OperandAux(args.aux_width).to(device)
    aux_opt=torch.optim.AdamW(aux.parameters(),lr=args.intermediate_lr,weight_decay=args.weight_decay)
    # Replace optimizer hyperparameters without discarding inherited moments.
    for opt in list(opts.values())+[median_opt]:
        for group in opt.param_groups:group['lr']=args.result_lr
    for opt in error_opts.values():
        for group in opt.param_groups:group['lr']=args.final_lr
    phase_tick=0
    phase_counts={phase:0 for phase in ('intermediate','result','final')}
    primary_best={'step':0,'xy_joint_5pct':-1.,'xy_asinh_mae':float('inf'),
                  'result_numeric_5pct':-1.,'final_numeric_5pct':-1.}
    continuous_best={'step':0,'result_log_error':float('inf'),
                     'final_log_error':float('inf'),'xy_asinh_mae':float('inf')}
    pareto_best={'final':{'step':0,'error':float('inf')},
                 'intermediary':{'step':0,'error':float('inf')}}
    if peek.get('schema') in (SCHEMA,parent34.SCHEMA):
        operand_decoder.load_state_dict(peek['bottom_operand_decoder'])
        decoder_opt.load_state_dict(peek['bottom_operand_optimizer'])
        aux.load_state_dict(peek['bottom_operand_aux'])
        aux_opt.load_state_dict(peek['bottom_operand_aux_optimizer'])
        phase_tick=int(peek['bottom_phase_tick'])
        phase_counts=dict(peek['bottom_phase_counts'])
        primary_best=dict(peek['bottom_primary_best'])
        if peek.get('schema')==SCHEMA:
            continuous_best=dict(peek['continuous_primary_best'])
            pareto_best=dict(peek['continuous_pareto_best'])
        # Preserve new learning rates also after optimizer state restoration.
        for opt,lr in ((decoder_opt,args.intermediate_lr),(aux_opt,args.intermediate_lr)):
            for group in opt.param_groups:group['lr']=lr

    def make_state(*positional):
        args_parent=list(positional)
        args_parent[1]=cfg['parent_config']
        state=pack_ancestor(*args_parent)
        state.update({'schema':SCHEMA,'config':cfg,
            'bottom_operand_decoder':operand_decoder.state_dict(),
            'bottom_operand_optimizer':decoder_opt.state_dict(),
            'bottom_operand_aux':aux.state_dict(),
            'bottom_operand_aux_optimizer':aux_opt.state_dict(),
            'bottom_phase_tick':phase_tick,'bottom_phase_counts':phase_counts,
            'bottom_primary_best':primary_best,
            'continuous_primary_best':continuous_best,
            'continuous_pareto_best':pareto_best})
        return state

    if fork:
        base.atomic_checkpoint(latest,make_state(step,cfg,networks,opts,op_probe,op_opt,stream,replay,best,
            median_net,median_opt,mb,rb,old_net,old_opt,old_best,old_threshold,
            closest_net,closest_opt,closest_best,comparators,pairwise,control_opts,experiment_best,
            estimators,error_opts,error_best))
        base.emit('continuous_forked_v3_4',step=step,parent_checkpoint=str(source_file),
                  parent_sha256=hashlib.sha256(source_file.read_bytes()).hexdigest())
    else:base.emit('continuous_resumed',step=step,best=error_best)
    validation=base.previous.load_eval(benchmark,manifest,tasks,'val',args.error_val_examples)

    def validate(rows,stage):
        operand_decoder.eval();aux.eval();median_net.eval()
        for net in networks.values():net.eval()
        for net in estimators.values():net.eval()
        d=data_capture(networks,op_probe,operand_decoder,median_net,closest_net,comparators,pairwise,estimators,rows,device)
        x,y,ops,_=rows
        truth=np.asarray([base.result_value(int(a),int(b),op) for (a,b),op in zip(y,ops)],dtype=np.float64)
        boolean=np.asarray([op in base.BOOL_OPS for op in ops],dtype=bool)
        _,logged=closest.numerical_errors(d['proposals'],truth)
        # Even indexes calibrate candidate-specific offsets. Odd indexes are
        # the disjoint validation-selection half; both are numeric subsets.
        calibration=np.arange(0,len(truth),2,dtype=np.int64)
        holdout=np.arange(1,len(truth),2,dtype=np.int64)
        offsets=calibrate_offsets(d['estimated'],logged,boolean,calibration)
        calibration_report=assess(d,rows,offsets=offsets,subset=calibration,frontiers=True)
        threshold_grid={name:{str(point['retention_target']):point['threshold'] for point in points}
                        for name,points in calibration_report['validation_frontiers'].items()}
        scored=assess(d,rows,offsets=offsets,
                      subset=holdout if len(holdout) else calibration,
                      thresholds=threshold_grid)
        scored['calibration_rows']=len(calibration)
        scored['calibration_threshold_frontiers']=calibration_report['validation_frontiers']
        scored['selection_holdout_rows']=len(holdout)
        score_rows=(rows[0][holdout],rows[1][holdout],
                    [rows[2][int(j)] for j in holdout],
                    [rows[3][int(j)] for j in holdout])
        scored['intermediaries']=operand_metrics(operand_decoder,aux,source,op_probe,score_rows,device)
        scored['continuous_objective']='source.mean_log1p_relative_error'
        scored['continuous_quantiles']=continuous_quantile_report(d,rows,holdout if len(holdout) else calibration)
        return d,offsets,scored

    if fork:
        # The inherited step-5,000 state is a real competitor in the new
        # lineage. Preserve it as the initial best; never overwrite it merely
        # because the first NEW evaluation has a finite (but worse) score.
        _,_,baseline=validate(validation,'fork_baseline')
        hi=baseline['intermediaries']['hierarchy_refined']
        initial_result=float(baseline['policy']['source']['mean_log1p_relative_error'])
        initial_final=float(baseline['policy']['error_calibrated']['mean_log1p_relative_error'])
        initial_intermediary=float(hi['x_signed_asinh_mae']+hi['y_signed_asinh_mae'])
        continuous_best={'step':step,'result_log_error':initial_result,
                         'final_log_error':initial_final,'xy_asinh_mae':initial_intermediary}
        pareto_best={'final':{'step':step,'error':initial_final},
                     'intermediary':{'step':step,'error':initial_intermediary}}
        state=make_state(step,cfg,networks,opts,op_probe,op_opt,stream,replay,best,
                        median_net,median_opt,mb,rb,old_net,old_opt,old_best,old_threshold,
                        closest_net,closest_opt,closest_best,comparators,pairwise,control_opts,experiment_best,
                        estimators,error_opts,error_best)
        for directory in ('continuous_result','continuous_final','continuous_intermediary'):
            base.atomic_checkpoint(root/directory/'best.pt',state)
        base.atomic_checkpoint(latest,state)
        base.atomic_json(root/'validation-fork-baseline.json',{'step':step,**baseline})
        base.emit('continuous_parent_baseline',**continuous_best)

    if args.eval_only:
        _,offsets,v_report=validate(base.previous.load_eval(benchmark,manifest,tasks,'val'),'eval')
        frontiers=v_report['calibration_threshold_frontiers']
        thresholds={name:{str(point['retention_target']):point['threshold'] for point in points}
                    for name,points in frontiers.items()}
        tag='best-' if args.eval_best else ''
        base.atomic_json(root/f'evaluation-{tag}validation.json',v_report)
        base.emit('continuous_evaluation',split='val_holdout',step=step,models={
            name:{'accuracy':m['numeric_5pct'],'log_error':m['mean_log1p_relative_error']}
            for name,m in v_report['policy'].items()},calibration_only_validation=True)
        for split in ('test','control'):
            rows=base.previous.load_eval(benchmark,manifest,tasks,split)
            for net in estimators.values():net.eval()
            d=data_capture(networks,op_probe,operand_decoder,median_net,closest_net,comparators,pairwise,estimators,rows,device)
            report=assess(d,rows,offsets=offsets,thresholds=thresholds)
            report['intermediaries']=operand_metrics(operand_decoder,aux,source,op_probe,rows,device)
            report['continuous_objective']='source.mean_log1p_relative_error'
            report['continuous_quantiles']=continuous_quantile_report(d,rows)
            base.atomic_json(root/f'evaluation-{tag}{split}.json',report)
            base.emit('continuous_evaluation',split=split,step=step,models={
                name:{'accuracy':m['numeric_5pct'],'log_error':m['mean_log1p_relative_error']}
                for name,m in report['policy'].items()},calibration_only_validation=True)
        return 0

    import nanojev_numerate_spine_cassette_smoke as cassette
    _,frozen,tokenizer,model_device,model_info=cassette.load_source('qwen',args)
    if (model_info.get('resolved_commit')!=identity['model_info'].get('resolved_commit') or
        model_info.get('source')!=identity['model_info'].get('source') or
        str(model_info.get('dtype'))!=str(identity['model_info'].get('dtype'))):
        raise RuntimeError('BOTTOM_QWEN_IDENTITY_DRIFT')
    frozen.eval()
    base.previous.verify_capture_alignment(torch,frozen,tokenizer,model_device,benchmark,tasks,args.max_prompt_tokens)
    base.emit('continuous_initialized',step=step,target_step=target,candidate_heads_trainable=True,
              error_estimators=len(estimators),parameters={name:sum(q.numel() for q in net.parameters())
                  for name,net in estimators.items()},parent_controllers_frozen=True)
    started=time.monotonic();capture_time=update_time=0.;recent=[]
    for k in range(step+1,target+1):
        t0=time.monotonic()
        batch=stream.batch(args.batch_size)
        f=base.previous.extract_batched(torch,frozen,tokenizer,[r['prompt'] for r in batch],model_device,args.max_prompt_tokens)
        if f.requires_grad:raise RuntimeError('BOTTOM_BACKBONE_GRADIENT_DETECTED')
        xy=base.source_decode(source,f.to(device));replay.add(f,batch,xy)
        capture_time+=time.monotonic()-t0;t0=time.monotonic()
        phase=phase_for_tick(phase_tick)
        phase_tick+=1
        phase_counts[phase]+=1
        for _ in range(args.replay_updates):
            sample=replay.sample(args.replay_batch)
            if phase=='intermediate':
                loss,parts=update_intermediaries(operand_decoder,decoder_opt,aux,aux_opt,sample,device,args.continuous_operand_weight)
            elif phase=='result':
                operand_decoder.eval()
                loss,parts=update_result_heads(operand_decoder,networks,opts,op_probe,
                                               median_net,median_opt,sample,device)
            else:
                operand_decoder.eval();median_net.eval()
                for net in networks.values():net.eval()
                loss,parts=update_final(operand_decoder,networks,op_probe,median_net,
                                        estimators,error_opts,sample,device,args)
            recent.append(loss)
        update_time+=time.monotonic()-t0
        if k%args.report_every==0 or k==target:
            base.emit('continuous_train_progress',step=k,mean_loss=round(float(np.mean(recent)),6),
                      last_parts=parts,phase=phase,phase_counts=dict(phase_counts),elapsed_seconds=round(time.monotonic()-started,2),
                      replay_records=len(replay.rows),examples_seen=len(stream.seen),
                      shared_capture_seconds=round(capture_time,2),head_update_seconds=round(update_time,2))
            recent=[]
        if k%args.eval_every==0 or k==target:
            _,offsets,report=validate(validation,'train')
            base.atomic_json(root/f'validation-step-{k:06d}.json',{'step':k,**report})
            brief={name:{'accuracy':m['numeric_5pct'],'near':m['nearest_candidate_accuracy'],
                         'log_error':m['mean_log1p_relative_error'],'improved':m['improved'],'worsened':m['worsened']}
                   for name,m in report['policy'].items()}
            base.emit('continuous_validation',step=k,models=brief,
                      error_mae={name:round(q['mae_log_error'],5) for name,q in report['per_candidate'].items()},
                      intermediary=report['intermediaries'])
            hi=report['intermediaries']['hierarchy_refined']
            selected=report['policy']['source']['mean_log1p_relative_error']
            final_error=report['policy']['error_calibrated']['mean_log1p_relative_error']
            operand_error=hi['x_signed_asinh_mae']+hi['y_signed_asinh_mae']
            if selected < continuous_best['result_log_error']-1e-12:
                continuous_best={'step':k,'result_log_error':float(selected),
                                 'final_log_error':float(final_error),
                                 'xy_asinh_mae':float(operand_error)}
                base.atomic_checkpoint(root/'continuous_result'/'best.pt',make_state(k,cfg,networks,opts,op_probe,op_opt,stream,replay,best,
                    median_net,median_opt,mb,rb,old_net,old_opt,old_best,old_threshold,
                    closest_net,closest_opt,closest_best,comparators,pairwise,control_opts,experiment_best,
                    estimators,error_opts,error_best))
                base.emit('continuous_best_result',**continuous_best)
            if final_error < pareto_best['final']['error']-1e-12:
                pareto_best['final']={'step':k,'error':float(final_error)}
                base.atomic_checkpoint(root/'continuous_final'/'best.pt',make_state(k,cfg,networks,opts,op_probe,op_opt,stream,replay,best,
                    median_net,median_opt,mb,rb,old_net,old_opt,old_best,old_threshold,
                    closest_net,closest_opt,closest_best,comparators,pairwise,control_opts,experiment_best,
                    estimators,error_opts,error_best))
            if operand_error < pareto_best['intermediary']['error']-1e-12:
                pareto_best['intermediary']={'step':k,'error':float(operand_error)}
                base.atomic_checkpoint(root/'continuous_intermediary'/'best.pt',make_state(k,cfg,networks,opts,op_probe,op_opt,stream,replay,best,
                    median_net,median_opt,mb,rb,old_net,old_opt,old_best,old_threshold,
                    closest_net,closest_opt,closest_best,comparators,pairwise,control_opts,experiment_best,
                    estimators,error_opts,error_best))
        if k%args.checkpoint_every==0 or k==target:
            base.atomic_checkpoint(latest,make_state(k,cfg,networks,opts,op_probe,op_opt,stream,replay,best,
                median_net,median_opt,mb,rb,old_net,old_opt,old_best,old_threshold,
                closest_net,closest_opt,closest_best,comparators,pairwise,control_opts,experiment_best,
                estimators,error_opts,error_best))
            base.emit('continuous_checkpoint',step=k,path=str(latest))
    base.emit('continuous_complete',step=target,best=continuous_best,pareto=pareto_best,phase_counts=phase_counts,results_root=str(root))
    return 0


def self_test():
    torch.manual_seed(34)
    labels=np.array([[7,-12],[0,199],[1200345,-1000]],dtype=np.int64)
    f=torch.randn(3,base.INPUT_WIDTH)
    frozen=base.constrained.model_factory('continuity',8,.25)
    tuned=copy.deepcopy(frozen)
    aux=OperandAux(8)
    dopt=torch.optim.AdamW(tuned.parameters(),lr=.0001)
    aopt=torch.optim.AdamW(aux.parameters(),lr=.0001)
    rows=[(f[k].half(),int(pair[0]),int(pair[1]),'sym_add',int(pair[0]),int(pair[1])) for k,pair in enumerate(labels)]
    before={k:v.detach().clone() for k,v in frozen.state_dict().items()}
    loss,metrics=update_intermediaries(tuned,dopt,aux,aopt,rows,torch.device('cpu'))
    assert np.isfinite(loss) and metrics['aux']['digits']>0
    assert all(torch.equal(v,before[k]) for k,v in frozen.state_dict().items())
    assert any(not torch.equal(v,before[k]) for k,v in tuned.state_dict().items())
    assert aux.decode(f).shape==(3,2)
    assert [phase_for_tick(k) for k in range(20)].count('intermediate')==16
    assert [phase_for_tick(k) for k in range(20)].count('result')==3
    assert [phase_for_tick(k) for k in range(20)].count('final')==1
    assert torch.isfinite(continuous_expert_loss(tuned(f),labels)[0])
    assert continuous_quantile_report({'proposals':np.ones((2,N))},(None,np.array([[1,2],[3,4]]),['sym_add','sym_sub'],None))['source']['mean']>=0.
    base.emit('continuous_self_test_pass',phase_cycle='16:3:1',operand_supervision=True,
              original_decoder_unchanged=True)
    return 0


if __name__=='__main__':raise SystemExit(execute(parser().parse_args()))
