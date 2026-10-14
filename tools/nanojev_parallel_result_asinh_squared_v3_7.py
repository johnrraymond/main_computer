#!/usr/bin/env python3
"""NanoJev v3.7: fixed-scale signed-asinh squared-error, bottom-heavy.

Fork ONLY from stable v3.5; keep original Qwen and v3.5 immutable.
Cycle: 16 known-intermediary, 3 RESULT, 1 FINAL update per 20 steps.
Numeric operand experts, auxiliary coordinates, continuous/residual RESULT,
and learned median use true squared differences in fixed-scale signed-asinh
coordinates, with structural classification retained for discrete targets.
FINAL learns a bounded signed improvement against R0, not squared-square
regression. The primary checkpoint minimizes mean RESULT signed-asinh MSE.
The historical 5% and log-relative metrics are diagnostics only.
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
import nanojev_parallel_result_continuous_v3_5 as parent35

SCHEMA='nanojev.parallel-result-asinh-squared.v3.7'
NAMES=pair.NAMES
N=pair.N
LOCAL_WIDTH=5
IN_WIDTH=base.INPUT_WIDTH+pair.NUMERIC_WIDTH+len(base.OP_CLASSES)+LOCAL_WIDTH


PHASE_NAMES = ('intermediate',)*16 + ('result',)*3 + ('final',)
AUX_DIGITS = 7
AUX_SCALE = math.asinh(float(base.buffered.h.MAX_VALUE))
OPERAND_SCALE = 1.0
RESULT_SCALE = 1.0
FINAL_TEMPERATURE = 2.0
SWITCH_MARGIN = 0.02



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
    # The actual X/Y regression is in physical units, not log coordinates.
    # The inverse-asinh is bounded exactly as it is at inference.
    coord=coordinates.double()
    coord=coord+(coord.clamp(-1.,1.)-coord).detach()
    recovered=torch.sinh(coord*AUX_SCALE)
    continuous=asinh_squared_torch(recovered,labels.double(),OPERAND_SCALE)
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


def continuous_expert_loss(raw,xy):
    """True-route fixed-scale signed-asinh squared error for X and Y.

    The categorical route remains supervised separately; no gold route is
    passed into inference. The expert's true-route numerical prediction is
    reconstructed in physical magnitude units, using actual band limits.
    """
    actual=np.asarray(xy,dtype=np.int64)
    if actual.ndim!=2 or actual.shape[1]!=2:
        raise ValueError('ASINH_OPERAND_SHAPE')
    bands=base.buffered.h.band_of(actual)
    experts=raw.float().reshape(-1,2,19)[:,:,7:]
    positive=torch.as_tensor((actual>0).astype(np.int64),device=raw.device)
    bucket=torch.as_tensor(bands*2,device=raw.device,dtype=torch.long)+positive
    selected=experts.gather(2,bucket.unsqueeze(-1)).squeeze(-1)
    low=np.empty_like(actual,dtype=np.float64)
    high=np.empty_like(actual,dtype=np.float64)
    for band in range(base.buffered.h.REGIMES):
        a,b=base.buffered.h.band_limits(band)
        low[bands==band]=a;high[bands==band]=b
    lo=torch.as_tensor(np.log(low),device=raw.device,dtype=torch.float64)
    hi=torch.as_tensor(np.log(high),device=raw.device,dtype=torch.float64)
    # Match the physical clipping of the expert's inference coordinate;
    # use a straight-through clamp to retain gradients for overshoot.
    selected=selected.double()
    selected=selected+(selected.clamp(-1.,1.)-selected).detach()
    physical=torch.exp(lo+(selected+1.)*.5*(hi-lo))
    truth=torch.as_tensor(np.abs(actual).astype(np.float64),device=raw.device,dtype=torch.float64)
    valid=torch.as_tensor(actual!=0,device=raw.device,dtype=torch.bool)
    terms=[];per=[]
    for i in (0,1):
        if bool(valid[:,i].any()):
            squared=(torch.asinh(physical[:,i][valid[:,i]] / OPERAND_SCALE)
                     -torch.asinh(truth[:,i][valid[:,i]] / OPERAND_SCALE)).square()
            term=squared.mean()
        else:term=selected[:,i].sum()*0.
        if not bool(torch.isfinite(term)):raise FloatingPointError('ASINH_NONFINITE_OPERAND')
        terms.append(term);per.append(float(term.detach()))
    return .5*(terms[0]+terms[1]),{'x_asinh_mse':per[0],'y_asinh_mse':per[1]}


def hierarchy_objective(network,features,xy,continuous_weight=1.0):
    """Structural CE and numerical signed-asinh squared loss; no numeric smooth-L1."""
    labels,bands,local=base.buffered.supervision_fast(xy,features.device)
    raw=network(features.float())
    # The inherited loss mixed expert smooth-L1 with classifications. Explicitly
    # disable that expert loss here, then add squared physical-value regression.
    structural,stats=base.buffered.vectorized_hierarchy_loss(
        raw,labels,bands,local,expert_weight=0.)
    range_loss=F.relu(raw.float().reshape(-1,2,19)[:,:,7:].abs()-1.).square().mean()
    sharing=network.sharing_penalty()
    squared,details=continuous_expert_loss(raw,xy)
    loss=structural+.05*range_loss+.02*sharing+continuous_weight*squared
    measured={'total':float(loss.detach()),'supervised':float(structural.detach()),
              'range':float(range_loss.detach()),'sharing':float(sharing.detach()),
              'continuity':0.,'boundary_rows':0.,'expert':float(squared.detach()),
              'route':float(stats['route'].detach()),'sign':float(stats['sign'].detach()),
              'zero':float(stats['zero'].detach()),
              'active_classifiers':float(stats['active_classifiers'].detach()),
              'active_experts':float(stats['active_experts'].detach()),
              'continuous_weight':float(continuous_weight)}
    measured.update(details)
    return loss,measured


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
    safe_clip_gradients(operand_decoder.parameters(),1.)
    safe_clip_gradients(aux.parameters(),1.)
    decoder_optimizer.step();aux_optimizer.step()
    return float((hierarchy_loss+aux_loss).detach()),{'hierarchy':h_stats,'aux':aux_stats}


def asinh_squared_np(proposals,truth,scale=None):
    """Unclipped fixed-scale signed-asinh squared residual, float64."""
    scale=RESULT_SCALE if scale is None else float(scale)
    if not (math.isfinite(scale) and scale>0):raise ValueError('ASINH_INVALID_SCALE')
    y=np.asarray(truth,dtype=np.float64)
    p=np.asarray(proposals,dtype=np.float64)
    delta=(np.arcsinh(p/scale)
           -np.arcsinh(y.reshape(-1,*([1]*(p.ndim-1)))/scale))
    squared=np.square(delta)
    if not np.isfinite(squared).all():raise FloatingPointError('ASINH_NONFINITE_MSE')
    return squared


def asinh_squared_torch(predictions,truth,scale=None):
    scale=RESULT_SCALE if scale is None else float(scale)
    if not (math.isfinite(scale) and scale>0):raise ValueError('ASINH_INVALID_SCALE')
    p=predictions.to(torch.float64)
    t=truth.to(torch.float64)
    value=(torch.asinh(p/scale)-torch.asinh(t/scale)).square()
    if not bool(torch.isfinite(value).all()):raise FloatingPointError('ASINH_NONFINITE_TORCH_MSE')
    return value.mean()


def safe_clip_gradients(parameters,max_norm=1.):
    """Compute gradient norm in float64; float32 squared norms can overflow."""
    grads=[p.grad for p in parameters if p.grad is not None]
    if not grads:return 0.
    if any(not bool(torch.isfinite(g).all()) for g in grads):
        raise FloatingPointError('ASINH_NONFINITE_GRADIENT')
    norm=torch.stack([g.detach().double().norm(2) for g in grads]).norm(2)
    if not bool(torch.isfinite(norm)):raise FloatingPointError('ASINH_NONFINITE_GRADIENT_NORM')
    ratio=min(1.,float(max_norm/(float(norm)+1e-12)))
    if ratio<1.:
        for grad in grads:grad.mul_(ratio)
    return float(norm)


def bounded_physical_result(coordinate):
    """Differentiable surrogate of signed-asinh RESULT decode (no rounding)."""
    raw=coordinate.to(torch.float64)
    clipped=raw.clamp(-1.,1.)
    # Straight-through clamp: the forward matches the existing bounded decoder,
    # while gradients can still correct a saturated coordinate.
    bounded=raw+(clipped-raw).detach()
    return torch.sinh(bounded*base.SCALE)


def update_result_heads(operand_decoder,networks,head_opts,op_probe,median_net,median_opt,rows,device):
    features=torch.stack([r[0] for r in rows]).to(device,dtype=torch.float32)
    truth,boolmask=base.truth_for([{'a':r[1],'b':r[2],'operation':r[3]} for r in rows])
    with torch.no_grad():
        derived=current_xy(operand_decoder,features)
        op_ids=op_probe(features).argmax(1)
    ops=[base.OP_CLASSES[int(k)] for k in op_ids.detach().cpu().numpy()]
    reference=torch.as_tensor(base.reference_results(derived,ops),device=device,dtype=torch.float32)
    design=base.features_with_op(features,op_ids,reference)
    numeric=torch.as_tensor(~boolmask,device=device,dtype=torch.bool)
    truth_t=torch.as_tensor(truth,device=device,dtype=torch.float32)
    losses={}
    for name,model in networks.items():
        model.train()
        output=model(design)
        if name in ('continuous','residual'):
            raw,sign,zero,boolean_logits=output
            bool_mask=~numeric
            bool_loss=(F.binary_cross_entropy_with_logits(boolean_logits[bool_mask],truth_t[bool_mask])
                       if bool(bool_mask.any()) else raw.sum()*0.)
            if bool(numeric.any()):
                values=truth_t[numeric]
                nz=values!=0
                sign_loss=(F.binary_cross_entropy_with_logits(sign[numeric][nz],(values[nz]>0).float())
                           if bool(nz.any()) else raw.sum()*0.)
                zero_loss=F.binary_cross_entropy_with_logits(zero[numeric],(~nz).float())
                coord=raw[numeric,0]
                if name=='residual':coord=coord+torch.asinh(reference[numeric].double())/base.SCALE
                physical=bounded_physical_result(coord)
                gold=torch.as_tensor(truth[~boolmask],device=device,dtype=torch.float64)
                squared=asinh_squared_torch(physical,gold,RESULT_SCALE)
                loss=squared+.12*sign_loss+.12*zero_loss+.2*bool_loss
                losses[name+'_asinh_mse']=float(squared.detach())
            else:loss=bool_loss
        else:
            # Discrete decoders (digits, bits, exponent, etc.) have no
            # differentiable physical-value decode; categorical supervision remains.
            loss,_=model.objective(output,truth_t,~numeric,reference,exact_values=truth)
            # Discrete heads retain CE/BCE. Scientific/ordinal's scalar
            # mantissa fraction historically used smooth-L1: replace only
            # that differentiable scalar loss by squared error in [0,1].
            if name in ('scientific','ordinal') and bool(numeric.any()):
                values=np.asarray(truth,dtype=np.float64)[~boolmask]
                _,fraction=base.scientific_targets(values)
                present=np.asarray(np.abs(values)>0)
                if present.any():
                    raw=output[0][numeric,14][torch.as_tensor(present,device=device)]
                    tgt=torch.as_tensor(fraction[present],device=device,dtype=torch.float32)
                    old_huber=F.smooth_l1_loss(raw,tgt,beta=.06)
                    loss=loss-old_huber+F.mse_loss(raw,tgt)
        head_opts[name].zero_grad(set_to_none=True)
        if not bool(torch.isfinite(loss)):raise FloatingPointError('ASINH_NONFINITE_RESULT_LOSS:'+name)
        loss.backward()
        safe_clip_gradients(model.parameters(),1.)
        head_opts[name].step()
        losses[name]=float(loss.detach())
    with torch.no_grad():
        ids,ref,first8,inferred_bool=median.candidates_for_rows(networks,op_probe,features,derived)
        direct,_=median.median_controls(first8,inferred_bool)
        x=median.median_design(features,ids,ref,first8,direct)
    median_net.train()
    predicted_delta=median_net(x)
    numeric_median=~np.asarray(boolmask,dtype=bool)
    if numeric_median.any():
        mask=torch.as_tensor(numeric_median,device=device,dtype=torch.bool)
        med=torch.as_tensor(base.signed_encode(direct[numeric_median]),device=device,dtype=torch.float64)
        estimate=bounded_physical_result(med+predicted_delta[mask])
        gold=torch.as_tensor(truth[numeric_median],device=device,dtype=torch.float64)
        mloss=asinh_squared_torch(estimate,gold,RESULT_SCALE)
    else:mloss=predicted_delta.sum()*0.
    median_opt.zero_grad(set_to_none=True)
    mloss.backward()
    safe_clip_gradients(median_net.parameters(),1.)
    median_opt.step()
    losses['median_regressed']=float(mloss.detach())
    return float(np.mean([losses[n] for n in list(networks)+['median_regressed']])),losses


def improvement_targets(proposals,truth,temperature=None):
    """Bounded signed improvement relative to R0. Lower is better."""
    temperature=FINAL_TEMPERATURE if temperature is None else float(temperature)
    if not (math.isfinite(temperature) and temperature>0):raise ValueError('ASINH_INVALID_TEMPERATURE')
    errors=asinh_squared_np(proposals,truth)
    relative=errors-errors[:,:1]
    bounded=np.tanh(relative/temperature)
    bounded[:,0]=0.
    return bounded


def asinh_final_objective(models,x,proposals,truth,boolean,relative_weight=.2):
    numeric=~np.asarray(boolean,dtype=bool)
    if not numeric.any():
        return sum((net.net[-1].weight.sum()*0. for net in models.values()),x.sum()*0.),{'rows':0}
    target=improvement_targets(np.asarray(proposals)[numeric],np.asarray(truth)[numeric])
    true=torch.as_tensor(target,device=x.device,dtype=torch.float64)
    mask=torch.as_tensor(numeric,device=x.device)
    estimated=torch.stack([models[name](x[mask,i])[0].double() for i,name in enumerate(NAMES)],dim=1)
    # The first channel predicts a signed bounded advantage. Anchoring its
    # source value to zero prevents arbitrary cross-head offsets at inference.
    absolute=(estimated-true).square().mean()
    relative=((estimated[:,1:]-estimated[:,:1])-true[:,1:]).square().mean()
    loss=absolute+relative_weight*relative
    if not bool(torch.isfinite(loss)):raise FloatingPointError('ASINH_NONFINITE_FINAL_LOSS')
    return loss,{'rows':int(numeric.sum()),'bounded_improvement_regression':float(absolute.detach()),
                 'source_relative_regression':float(relative.detach()),
                 'within5_training_weight':0.,'target_abs_max':float(np.max(np.abs(target)))}


def update_final(operand_decoder,networks,op_probe,median_net,estimators,error_opts,rows,device,args):
    features=torch.stack([r[0] for r in rows]).to(device,dtype=torch.float32)
    truth,boolean=base.truth_for([{'a':r[1],'b':r[2],'operation':r[3]} for r in rows])
    with torch.no_grad():
        xy=current_xy(operand_decoder,features)
        proposals,_,_,predops=pair.model_inputs(networks,op_probe,median_net,features,xy)
        x=design(features,proposals,predops)
    for net in estimators.values():net.train()
    loss,parts=asinh_final_objective(estimators,x,proposals,truth,boolean,
                                       relative_weight=args.relative_error_weight)
    for opt in error_opts.values():opt.zero_grad(set_to_none=True)
    loss.backward()
    for net in estimators.values():safe_clip_gradients(net.parameters(),1.)
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
        return torch.tanh(raw[:,0]),raw[:,1]


def predict_all(models,x):
    estimated=[];logits=[]
    for i,name in enumerate(NAMES):
        est,cls=models[name](x[:,i])
        estimated.append(est);logits.append(cls)
    return torch.stack(estimated,dim=1),torch.stack(logits,dim=1)


def select_min_error(estimated,bool_pred,offsets=None):
    """Source-anchored bounded improvement: switch only beyond fixed margin."""
    p=np.asarray(estimated,dtype=np.float64)
    if p.ndim!=2 or p.shape[1]!=N or not np.isfinite(p).all():
        raise ValueError('ASINH_INVALID_PREDICTIONS')
    predicted=p.copy()
    if offsets is not None:predicted+=np.asarray(offsets,dtype=np.float64)[None,:]
    predicted[:,0]=0.  # R0 is always the explicit zero-regret competitor.
    predicted=np.clip(predicted,-1.,1.)
    indices=predicted.argmin(axis=1).astype(np.int64)
    indices[predicted[np.arange(len(indices)),indices]>=-SWITCH_MARGIN]=0
    indices[np.asarray(bool_pred,dtype=bool)]=0
    return indices,predicted


def asinh_calibrate_offsets(predicted,true_bounded,true_boolean,indices=None):
    p=np.asarray(predicted,dtype=np.float64)
    target=np.asarray(true_bounded,dtype=np.float64)
    mask=~np.asarray(true_boolean,dtype=bool)
    if indices is not None:
        keep=np.zeros(len(mask),dtype=bool)
        keep[np.asarray(indices,dtype=np.int64)]=True
        mask &= keep
    if not mask.any():return np.zeros(N,dtype=np.float64)
    offsets=np.clip(np.mean(target[mask]-p[mask],axis=0),-.25,.25)
    offsets[0]=0.
    if not np.isfinite(offsets).all():raise FloatingPointError('ASINH_NONFINITE_CALIBRATION')
    return offsets


def per_candidate_diagnostics(estimated,true_bounded,true_asinh_sq,bool_true,hit_logits=None):
    valid=~np.asarray(bool_true,dtype=bool)
    if not valid.any():return {}
    results={}
    for i,name in enumerate(NAMES):
        p=np.asarray(estimated,dtype=np.float64)[valid,i]
        y=np.asarray(true_bounded,dtype=np.float64)[valid,i]
        delta=p-y
        results[name]={'n':len(p),'predicted_mean_bounded_improvement':float(p.mean()),
                       'actual_mean_bounded_improvement':float(y.mean()),
                       'mean_absolute_improvement_prediction':float(np.abs(delta).mean()),
                       'rmse_improvement_prediction':float(np.sqrt(np.square(delta).mean())),
                       'signed_bias':float(delta.mean()),
                       'candidate_mean_asinh_squared_error':float(np.asarray(true_asinh_sq)[valid,i].mean())}
    return results


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
    truth_sq=asinh_squared_np(c,truth)
    true_improvement=improvement_targets(c,truth)
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
            'per_candidate':per_candidate_diagnostics(p[indices],true_improvement[indices],truth_sq[indices],boolean[indices],data['hit_logits'][indices]),
            'per_candidate_calibrated':per_candidate_diagnostics(cal_error[indices],true_improvement[indices],truth_sq[indices],boolean[indices],data['hit_logits'][indices]),
            'policy':{},'source_oracle':None}
    for name,ids in all_ids.items():
        policy=pair.measure(c[indices],truth[indices],boolean[indices],ids[indices])
        chosen=truth_sq[indices,ids[indices]]
        numeric=~boolean[indices]
        if numeric.any():
            errors=chosen[numeric]
            policy['mean_asinh_squared_error']=float(errors.mean())
            policy['rmse_asinh_error']=float(np.sqrt(errors.mean()))
            policy['median_asinh_squared_error']=float(np.median(errors))
            policy['p90_asinh_squared_error']=float(np.quantile(errors,.90))
            policy['p99_asinh_squared_error']=float(np.quantile(errors,.99))
            policy['max_asinh_squared_error']=float(np.max(errors))
        else:policy['mean_asinh_squared_error']=None
        if numeric.any():
            chosen_values=c[indices,ids[indices]][numeric]
            ref_values=truth[indices][numeric]
            near_zero=np.abs(chosen_values)<=np.maximum(1.,np.abs(ref_values))*.001
            policy['near_zero_fraction']=float(np.mean(near_zero))
            policy['source_fraction']=float(np.mean(ids[indices][numeric]==0))
        report['policy'][name]=policy
    report['source_oracle']=report['policy']['source']['oracle_numeric_5pct']
    report['error_calibration']={'offsets':{name:float(v) for name,v in zip(NAMES,np.zeros(N) if offsets is None else offsets)},
                                 'mean_abs_offset':float(np.mean(np.abs(np.zeros(N) if offsets is None else offsets)))}
    raw_reference=report['policy']['source']
    report['delta_vs_source']={name:{'numeric_5pct':round(metrics['numeric_5pct']-raw_reference['numeric_5pct'],8),
                                     'mean_asinh_squared_error':float(metrics['mean_asinh_squared_error']-raw_reference['mean_asinh_squared_error'])}
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


def asinh_quantile_report(data,rows,subset=None):
    _f,xy,operations,_families=rows
    truth=np.asarray([base.result_value(int(a),int(b),op) for (a,b),op in zip(xy,operations)],dtype=np.float64)
    mask=~np.asarray([op in base.BOOL_OPS for op in operations],dtype=bool)
    if subset is not None:
        keep=np.zeros(len(mask),dtype=bool)
        keep[np.asarray(subset,dtype=np.int64)]=True
        mask &= keep
    if not mask.any():return {}
    sq=asinh_squared_np(data['proposals'][mask],truth[mask])
    return {name:{'mean':float(np.mean(sq[:,i])),
                  'rmse':float(np.sqrt(np.mean(sq[:,i]))),
                  'median':float(np.median(sq[:,i])),
                  'p90':float(np.quantile(sq[:,i],.90)),
                  'p99':float(np.quantile(sq[:,i],.99)),
                  'maximum':float(np.max(sq[:,i]))} for i,name in enumerate(NAMES)}


def operand_metrics(operand_decoder,aux,frozen_source,op_probe,rows,device,batch_size=64):
    old=parent35.operand_metrics(operand_decoder,aux,frozen_source,op_probe,rows,device,batch_size)
    features,xy,ops,_=rows
    actual=np.asarray(xy,dtype=np.int64)
    predicts={'original_frozen':[],'hierarchy_refined':[],'aux_continuous':[]}
    predops=[]
    with torch.inference_mode():
        for start in range(0,len(actual),batch_size):
            z=torch.as_tensor(features[start:start+batch_size],device=device,dtype=torch.float32)
            predicts['original_frozen'].append(base.source_decode(frozen_source,z))
            predicts['hierarchy_refined'].append(current_xy(operand_decoder,z))
            predicts['aux_continuous'].append(aux.decode(z))
            predops.extend(base.OP_CLASSES[int(k)] for k in op_probe(z).argmax(1).cpu().numpy())
    gold,booleans=base.truth_for([{'a':int(x),'b':int(y),'operation':op} for (x,y),op in zip(actual,ops)])
    for name,parts in predicts.items():
        pred=np.concatenate(parts)
        operand_sq=np.square((pred.astype(np.float64)-actual)/np.maximum(np.abs(actual),1.))
        old[name]['x_asinh_mse']=float(operand_sq[:,0].mean())
        old[name]['y_asinh_mse']=float(operand_sq[:,1].mean())
        computed=base.reference_results(pred,predops)
        squared=asinh_squared_np(computed[~booleans],gold[~booleans]) if (~booleans).any() else None
        old[name]['executed_result_numeric_asinh_mse']=float(np.mean(squared)) if squared is not None else None
    return old


def parser():
    p=parent34.parser();p.description=__doc__
    p.set_defaults(results_root=Path.home()/'NanoJev'/'runs'/'parallel_result_asinh_squared_v3_7')
    p.add_argument('--fork-v3-4-root',type=Path,default=None)
    p.add_argument('--fork-v3-5-root',type=Path,default=None)
    p.add_argument('--continuous-operand-weight',type=float,default=1.0)
    p.add_argument('--relative-error-weight',type=float,default=.2)
    p.add_argument('--operand-asinh-scale',type=float,default=1.0)
    p.add_argument('--result-asinh-scale',type=float,default=1.0)
    p.add_argument('--final-target-temperature',type=float,default=2.0)
    p.add_argument('--switch-margin',type=float,default=.02)
    return p


def config_for(args,manifest,sha,identity):
    return {'schema':SCHEMA,'parent_config':parent35.config_for(args,manifest,sha,identity),
            'continuous_operand_weight':args.continuous_operand_weight,
            'relative_error_weight':args.relative_error_weight,
            'operand_asinh_scale':args.operand_asinh_scale,
            'result_asinh_scale':args.result_asinh_scale,
            'final_target_temperature':args.final_target_temperature,
            'switch_margin':args.switch_margin,
            'phase_cycle':list(PHASE_NAMES),
            'objective':'mean_fixed_scale_signed_asinh_squared_result_error',
            'final_loss':'bounded_source_relative_asinh_squared_improvement',
            'within5_training_weight':0.,'within5_checkpoint_weight':0.,
            'asinh_scales':'fixed positive per experiment; independent of each target',
            'uncapped_numerical_asinh_squared_error':True,
            'optimizer_moments':'reset_on_fork_new_squared_objective',
            'validation':'even_calibration_odd_checkpoint_selection',
            'backbone_frozen':True,'source_and_parent_frozen':True}


def pack_ancestor(step,cfg,networks,opts,op_probe,op_opt,stream,replay,best,median_net,median_opt,mb,rb,
         old_net,old_opt,old_best,old_threshold,closest_net,closest_opt,closest_best,
         comparators,pairwise,optimizers,experiment_best,estimators,error_opts,error_best):
    ck=pair.pack(step,cfg['parent_config']['parent_config']['parent_config'],networks,opts,op_probe,op_opt,stream,replay,best,
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
    if args.fork_v3_4_root is not None:raise ValueError('ASINH_FORK_ONLY_FROM_V3_5')
    if any(x is not None for x in (args.fork_v1_root,args.fork_v2_root,args.fork_v3_root,args.fork_v3_1_root,args.fork_v3_2_root,args.fork_v3_3_root)):
        raise ValueError('ASINH_FORK_ONLY_FROM_V3_5')
    if args.fork_v3_5_root is not None and (args.resume or args.eval_only):raise ValueError('BOTTOM_FORK_RESUME_EXCLUSIVE')
    if args.fork_parent_best and args.fork_v3_5_root is None:raise ValueError('BOTTOM_FORK_PARENT_BEST_REQUIRES_FORK')
    if args.error_width<8 or args.within5_weight<0 or args.error_val_examples<2 or args.aux_width<8 or min(args.intermediate_lr,args.result_lr,args.final_lr)<=0 or args.continuous_operand_weight<0 or args.relative_error_weight<0:raise ValueError('BOTTOM_INVALID_HEAD_CONFIG')
    if args.eval_best and not args.eval_only:raise ValueError('BOTTOM_EVAL_BEST_ONLY_WITH_EVAL_ONLY')
    if args.additional_steps is not None and args.additional_steps<1:raise ValueError('BOTTOM_INVALID_ADDITIONAL_STEPS')
    if not all(math.isfinite(v) and v>0 for v in (args.operand_asinh_scale,args.result_asinh_scale,args.final_target_temperature)):
        raise ValueError('ASINH_INVALID_SCALES')
    if not (0<=args.switch_margin<1):raise ValueError('ASINH_INVALID_SWITCH_MARGIN')
    global OPERAND_SCALE,RESULT_SCALE,FINAL_TEMPERATURE,SWITCH_MARGIN
    OPERAND_SCALE=float(args.operand_asinh_scale)
    RESULT_SCALE=float(args.result_asinh_scale)
    FINAL_TEMPERATURE=float(args.final_target_temperature)
    SWITCH_MARGIN=float(args.switch_margin)
    if args.threads>0:torch.set_num_threads(args.threads)
    if args.device=='cuda' and not torch.cuda.is_available():raise RuntimeError('BOTTOM_CUDA_UNAVAILABLE')
    device=torch.device(args.device)
    benchmark=args.v3_root.expanduser().resolve()
    manifest,tasks=base.previous.read_benchmark(benchmark)
    identity=json.loads((benchmark/'model_identity.json').read_text(encoding='utf-8'))
    source,sha=base.source_factory(args.source_checkpoint,identity,device)
    cfg=config_for(args,manifest,sha,identity)
    root=args.results_root.expanduser().resolve()
    fork=args.fork_v3_5_root.expanduser().resolve() if args.fork_v3_5_root is not None else None
    source_root=args.source_checkpoint.expanduser().resolve().parent
    if (root==benchmark or benchmark in root.parents or root==source_root or source_root in root.parents or
        (fork is not None and (root==fork or fork in root.parents or root in fork.parents))):
        raise RuntimeError('BOTTOM_UNSAFE_RESULTS_ROOT')
    latest=root/'latest.pt'
    if args.resume or args.eval_only:
        if not latest.is_file():raise RuntimeError('ASINH_RESUME_REQUIRES_LATEST')
        source_file=(root/'asinh_result'/'best.pt') if args.eval_best else latest
        if not source_file.is_file():raise RuntimeError('ASINH_BEST_CHECKPOINT_MISSING')
        peek=torch.load(source_file,map_location='cpu',weights_only=True)
        if peek.get('schema')!=SCHEMA:raise RuntimeError('ASINH_SCHEMA_MISMATCH')
        expected_cfg=cfg
    else:
        if fork is None:raise RuntimeError('ASINH_REQUIRES_--fork-v3-5-root_OR_--resume')
        source_file=(fork/'continuous_result'/'best.pt') if args.fork_parent_best else (fork/'latest.pt')
        if not source_file.is_file():raise RuntimeError('ASINH_FORK_REQUIRES_V3_5_CHECKPOINT')
        if latest.exists():raise RuntimeError('ASINH_RUN_EXISTS_USE_--resume')
        peek=torch.load(source_file,map_location='cpu',weights_only=True)
        if peek.get('schema')!=parent35.SCHEMA:raise RuntimeError('ASINH_PARENT_SCHEMA_MISMATCH')
        expected_cfg=cfg['parent_config']
    inherited=int(peek['step'])
    target=inherited+args.additional_steps if args.additional_steps is not None else args.steps
    if not args.eval_only and target<=inherited:
        raise RuntimeError(f'ASINH_TARGET_NOT_ABOVE_CHECKPOINT:{inherited}; USE_--additional-steps 500')
    if peek.get('config')!=expected_cfg:raise RuntimeError('ASINH_CONFIG_DRIFT')
    if not (args.resume or args.eval_only) and root.exists() and any(root.iterdir()):
        raise RuntimeError('BOTTOM_NONEMPTY_RESULTS_ROOT')
    root.mkdir(parents=True,exist_ok=True)
    cfg_file=root/'run_config.json'
    if cfg_file.exists():
        if json.loads(cfg_file.read_text(encoding='utf-8'))!=cfg:raise RuntimeError('ASINH_CONFIG_FILE_DRIFT')
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
    payload={**peek,'schema':closest.SCHEMA,'config':cfg['parent_config']['parent_config']['parent_config']['parent_config']['parent_config']}
    step,best,mb,rb,old_best,old_threshold,closest_best=closest.load_payload(
        payload,cfg['parent_config']['parent_config']['parent_config']['parent_config']['parent_config'],networks,opts,op_probe,op_opt,stream,replay,
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
    primary_best={'step':0,'xy_joint_5pct':-1.,'xy_asinh_mse':float('inf'),
                  'result_numeric_5pct':-1.,'final_numeric_5pct':-1.}
    continuous_best={'step':0,'result_mse':float('inf'),
                     'final_mse':float('inf'),'xy_asinh_mse':float('inf')}
    pareto_best={'final':{'step':0,'error':float('inf')},
                 'intermediary':{'step':0,'error':float('inf')}}
    if peek.get('schema') in (SCHEMA,parent35.SCHEMA):
        operand_decoder.load_state_dict(peek['bottom_operand_decoder'])
        decoder_opt.load_state_dict(peek['bottom_operand_optimizer'])
        aux.load_state_dict(peek['bottom_operand_aux'])
        aux_opt.load_state_dict(peek['bottom_operand_aux_optimizer'])
        phase_tick=int(peek['bottom_phase_tick'])
        phase_counts=dict(peek['bottom_phase_counts'])
        primary_best=dict(peek['bottom_primary_best'])
        if peek.get('schema')==SCHEMA:
            continuous_best=dict(peek['asinh_primary_best'])
            pareto_best=dict(peek['asinh_pareto_best'])
        # Preserve new learning rates also after optimizer state restoration.
        for opt,lr in ((decoder_opt,args.intermediate_lr),(aux_opt,args.intermediate_lr)):
            for group in opt.param_groups:group['lr']=lr

    if fork:
        # Signed-asinh MSE is not the inherited log/smooth-L1 objective. Keep every
        # learned parameter, but do not reuse Adam moments trained on the
        # previous loss geometry. Reset only trainable optimizer state.
        for opt in [decoder_opt,aux_opt,median_opt,*opts.values(),*error_opts.values()]:
            opt.state.clear()
        for net in estimators.values():
            nn.init.normal_(net.net[-1].weight,mean=0.,std=1e-3)
            nn.init.zeros_(net.net[-1].bias)

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
            'asinh_primary_best':continuous_best,
            'asinh_pareto_best':pareto_best})
        return state

    if fork:
        base.atomic_checkpoint(latest,make_state(step,cfg,networks,opts,op_probe,op_opt,stream,replay,best,
            median_net,median_opt,mb,rb,old_net,old_opt,old_best,old_threshold,
            closest_net,closest_opt,closest_best,comparators,pairwise,control_opts,experiment_best,
            estimators,error_opts,error_best))
        base.emit('asinh_forked_v3_5',step=step,parent_checkpoint=str(source_file),
                  parent_sha256=hashlib.sha256(source_file.read_bytes()).hexdigest())
    else:base.emit('asinh_resumed',step=step,best=error_best)
    validation=base.previous.load_eval(benchmark,manifest,tasks,'val',args.error_val_examples)

    def validate(rows,stage):
        operand_decoder.eval();aux.eval();median_net.eval()
        for net in networks.values():net.eval()
        for net in estimators.values():net.eval()
        d=data_capture(networks,op_probe,operand_decoder,median_net,closest_net,comparators,pairwise,estimators,rows,device)
        x,y,ops,_=rows
        truth=np.asarray([base.result_value(int(a),int(b),op) for (a,b),op in zip(y,ops)],dtype=np.float64)
        boolean=np.asarray([op in base.BOOL_OPS for op in ops],dtype=bool)
        logged=improvement_targets(d['proposals'],truth)
        # Even indexes calibrate candidate-specific offsets. Odd indexes are
        # the disjoint validation-selection half; both are numeric subsets.
        calibration=np.arange(0,len(truth),2,dtype=np.int64)
        holdout=np.arange(1,len(truth),2,dtype=np.int64)
        offsets=asinh_calibrate_offsets(d['estimated'],logged,boolean,calibration)
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
        scored['asinh_objective']='source.mean_asinh_squared_error'
        scored['asinh_quantiles']=asinh_quantile_report(d,rows,holdout if len(holdout) else calibration)
        return d,offsets,scored

    if fork:
        # The inherited step-5,000 state is a real competitor in the new
        # lineage. Preserve it as the initial best; never overwrite it merely
        # because the first NEW evaluation has a finite (but worse) score.
        _,_,baseline=validate(validation,'fork_baseline')
        hi=baseline['intermediaries']['hierarchy_refined']
        initial_result=float(baseline['policy']['source']['mean_asinh_squared_error'])
        initial_final=float(baseline['policy']['error_calibrated']['mean_asinh_squared_error'])
        initial_intermediary=float(hi['x_asinh_mse']+hi['y_asinh_mse'])
        continuous_best={'step':step,'result_mse':initial_result,
                         'final_mse':initial_final,'xy_asinh_mse':initial_intermediary}
        pareto_best={'final':{'step':step,'error':initial_final},
                     'intermediary':{'step':step,'error':initial_intermediary}}
        state=make_state(step,cfg,networks,opts,op_probe,op_opt,stream,replay,best,
                        median_net,median_opt,mb,rb,old_net,old_opt,old_best,old_threshold,
                        closest_net,closest_opt,closest_best,comparators,pairwise,control_opts,experiment_best,
                        estimators,error_opts,error_best)
        for directory in ('asinh_result','asinh_final','asinh_intermediary'):
            base.atomic_checkpoint(root/directory/'best.pt',state)
        base.atomic_checkpoint(latest,state)
        base.atomic_json(root/'validation-fork-baseline.json',{'step':step,**baseline})
        base.emit('asinh_parent_baseline',**continuous_best)

    if args.eval_only:
        _,offsets,v_report=validate(base.previous.load_eval(benchmark,manifest,tasks,'val'),'eval')
        frontiers=v_report['calibration_threshold_frontiers']
        thresholds={name:{str(point['retention_target']):point['threshold'] for point in points}
                    for name,points in frontiers.items()}
        tag='best-' if args.eval_best else ''
        base.atomic_json(root/f'evaluation-{tag}validation.json',v_report)
        base.emit('asinh_evaluation',split='val_holdout',step=step,models={
            name:{'accuracy':m['numeric_5pct'],'mse':m['mean_asinh_squared_error'],'log_error':m['mean_log1p_relative_error']}
            for name,m in v_report['policy'].items()},calibration_only_validation=True)
        for split in ('test','control'):
            rows=base.previous.load_eval(benchmark,manifest,tasks,split)
            for net in estimators.values():net.eval()
            d=data_capture(networks,op_probe,operand_decoder,median_net,closest_net,comparators,pairwise,estimators,rows,device)
            report=assess(d,rows,offsets=offsets,thresholds=thresholds)
            report['intermediaries']=operand_metrics(operand_decoder,aux,source,op_probe,rows,device)
            report['asinh_objective']='source.mean_asinh_squared_error'
            report['asinh_quantiles']=asinh_quantile_report(d,rows)
            base.atomic_json(root/f'evaluation-{tag}{split}.json',report)
            base.emit('asinh_evaluation',split=split,step=step,models={
                name:{'accuracy':m['numeric_5pct'],'mse':m['mean_asinh_squared_error'],'log_error':m['mean_log1p_relative_error']}
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
    base.emit('asinh_initialized',step=step,target_step=target,candidate_heads_trainable=True,
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
            base.emit('asinh_train_progress',step=k,mean_loss=round(float(np.mean(recent)),6),
                      last_parts=parts,phase=phase,phase_counts=dict(phase_counts),elapsed_seconds=round(time.monotonic()-started,2),
                      replay_records=len(replay.rows),examples_seen=len(stream.seen),
                      shared_capture_seconds=round(capture_time,2),head_update_seconds=round(update_time,2))
            recent=[]
        if k%args.eval_every==0 or k==target:
            _,offsets,report=validate(validation,'train')
            base.atomic_json(root/f'validation-step-{k:06d}.json',{'step':k,**report})
            brief={name:{'accuracy':m['numeric_5pct'],'near':m['nearest_candidate_accuracy'],
                         'mse':m['mean_asinh_squared_error'],'log_error':m['mean_log1p_relative_error'],'improved':m['improved'],'worsened':m['worsened']}
                   for name,m in report['policy'].items()}
            base.emit('asinh_validation',step=k,models=brief,
                      error_mae={name:round(q['mean_absolute_improvement_prediction'],5) for name,q in report['per_candidate'].items()},
                      intermediary=report['intermediaries'])
            hi=report['intermediaries']['hierarchy_refined']
            selected=report['policy']['source']['mean_asinh_squared_error']
            final_error=report['policy']['error_calibrated']['mean_asinh_squared_error']
            operand_error=hi['x_asinh_mse']+hi['y_asinh_mse']
            if selected < continuous_best['result_mse']-1e-12:
                continuous_best={'step':k,'result_mse':float(selected),
                                 'final_mse':float(final_error),
                                 'xy_asinh_mse':float(operand_error)}
                base.atomic_checkpoint(root/'asinh_result'/'best.pt',make_state(k,cfg,networks,opts,op_probe,op_opt,stream,replay,best,
                    median_net,median_opt,mb,rb,old_net,old_opt,old_best,old_threshold,
                    closest_net,closest_opt,closest_best,comparators,pairwise,control_opts,experiment_best,
                    estimators,error_opts,error_best))
                base.emit('asinh_best_result',**continuous_best)
            if final_error < pareto_best['final']['error']-1e-12:
                pareto_best['final']={'step':k,'error':float(final_error)}
                base.atomic_checkpoint(root/'asinh_final'/'best.pt',make_state(k,cfg,networks,opts,op_probe,op_opt,stream,replay,best,
                    median_net,median_opt,mb,rb,old_net,old_opt,old_best,old_threshold,
                    closest_net,closest_opt,closest_best,comparators,pairwise,control_opts,experiment_best,
                    estimators,error_opts,error_best))
            if operand_error < pareto_best['intermediary']['error']-1e-12:
                pareto_best['intermediary']={'step':k,'error':float(operand_error)}
                base.atomic_checkpoint(root/'asinh_intermediary'/'best.pt',make_state(k,cfg,networks,opts,op_probe,op_opt,stream,replay,best,
                    median_net,median_opt,mb,rb,old_net,old_opt,old_best,old_threshold,
                    closest_net,closest_opt,closest_best,comparators,pairwise,control_opts,experiment_best,
                    estimators,error_opts,error_best))
        if k%args.checkpoint_every==0 or k==target:
            base.atomic_checkpoint(latest,make_state(k,cfg,networks,opts,op_probe,op_opt,stream,replay,best,
                median_net,median_opt,mb,rb,old_net,old_opt,old_best,old_threshold,
                closest_net,closest_opt,closest_best,comparators,pairwise,control_opts,experiment_best,
                estimators,error_opts,error_best))
            base.emit('asinh_checkpoint',step=k,path=str(latest))
    base.emit('asinh_complete',step=target,best=continuous_best,pareto=pareto_best,phase_counts=phase_counts,results_root=str(root))
    return 0


def self_test():
    torch.manual_seed(37)
    assert np.allclose(asinh_squared_np([1.,2.],[1.,2.]),0.)
    assert asinh_squared_np([0.],[1000000.])[0]>asinh_squared_np([0.],[100.])[0]>0.
    a=torch.tensor([1.,2.],requires_grad=True)
    loss=asinh_squared_torch(a,torch.tensor([2.,1.]))
    loss.backward()
    assert torch.isfinite(loss) and a.grad is not None and bool(torch.isfinite(a.grad).all())
    assert [phase_for_tick(i) for i in range(20)].count('intermediate')==16
    assert [phase_for_tick(i) for i in range(20)].count('result')==3
    assert [phase_for_tick(i) for i in range(20)].count('final')==1
    model=base.constrained.model_factory('continuity',8,.25)
    inputs=torch.randn(3,base.INPUT_WIDTH)
    xy=np.array([[7,19],[-200,0],[150000,-700]],dtype=np.int64)
    l,detail=continuous_expert_loss(model(inputs),xy)
    l.backward()
    assert torch.isfinite(l) and detail['x_asinh_mse']>=0
    assert any(p.grad is not None and p.grad.abs().sum()>0 for p in model.parameters())
    f=torch.randn(3,N,IN_WIDTH)
    nets={n:ErrorEstimator(8) for n in NAMES}
    truth=np.array([200.,30.,10.])
    proposals=np.repeat(truth[:,None],N,axis=1)
    proposals[:,1]+=np.array([200.,20.,3.])
    loss,parts=asinh_final_objective(nets,f,proposals,truth,np.zeros(3,dtype=bool))
    loss.backward()
    assert parts['within5_training_weight']==0 and parts['target_abs_max']<=1.
    assert all(net.net[-1].weight.grad is not None and
               torch.all(net.net[-1].weight.grad[1]==0) for net in nets.values())
    direct=np.zeros((2,N));direct[:,1]=[-.5,-.001]
    selected,_=select_min_error(direct,np.array([False,False]))
    assert selected.tolist()==[1,0]
    assert np.allclose(improvement_targets(proposals,truth)[:,0],0.)
    base.emit('asinh_self_test_pass',phase_cycle='16:3:1',squared_signed_asinh=True,
              bounded_relative_final=True,no_five_percent_gradient=True)
    return 0

if __name__=='__main__':raise SystemExit(execute(parser().parse_args()))
