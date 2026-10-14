#!/usr/bin/env python3
"""FINAL v3.3: eleven independent candidate-error estimators and frozen v3.2 controls.

Each candidate model predicts log1p(relative error) and probability that its
RESULT is within 5%. Every numerical row trains *every* estimator, even if no
candidate is within 5%. Inference uses no truth and always selects one RESULT.
Calibration offsets use validation calibration rows only; held-out validation,
test, and control are never used to fit the offsets. All ancestor models are
frozen; a single frozen Qwen capture feeds all eleven heads and v3.2 controls.
"""
from __future__ import annotations

import hashlib
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

SCHEMA='nanojev.parallel-result-final-errors.v3.3'
NAMES=pair.NAMES
N=pair.N
LOCAL_WIDTH=5
IN_WIDTH=base.INPUT_WIDTH+pair.NUMERIC_WIDTH+len(base.OP_CLASSES)+LOCAL_WIDTH


def local_features(proposals):
    """Per-candidate data, independent of the true RESULT."""
    c=np.asarray(proposals,dtype=np.float64)
    if c.ndim!=2 or c.shape[1]!=N or not np.isfinite(c).all():
        raise ValueError('ERRORS_INVALID_PROPOSALS')
    z=base.signed_encode(c)
    median_z=np.median(z,axis=1,keepdims=True)
    dispersion=np.median(np.abs(z-median_z),axis=1,keepdims=True)
    stacked=np.stack((z,z-z[:,0:1],z-median_z,
                      np.abs(z-median_z)/(dispersion+1e-3),
                      np.broadcast_to(dispersion,z.shape)),axis=-1).astype(np.float32)
    if not np.isfinite(stacked).all():raise ValueError('ERRORS_NONFINITE_LOCAL_FEATURES')
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
    if len(indices)==0:raise ValueError('ERRORS_EMPTY_EVALUATION_SUBSET')
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


def parser():
    p=pair.parser();p.description=__doc__
    p.set_defaults(results_root=Path.home()/'NanoJev'/'runs'/'parallel_result_final_errors_v3_3')
    p.add_argument('--fork-v3-2-root',type=Path,default=None)
    p.add_argument('--error-width',type=int,default=96)
    p.add_argument('--within5-weight',type=float,default=.25)
    p.add_argument('--error-val-examples',type=int,default=2048,
                   help='Independent v3.3 validation size; does not change inherited v3.2 config')
    p.add_argument('--eval-best',action='store_true',help='With --eval-only, use v3.3 error_calibrated/best.pt')
    return p


def config_for(args,manifest,sha,identity):
    return {'schema':SCHEMA,'parent_config':pair.config_for(args,manifest,sha,identity),
            'error_width':args.error_width,'within5_weight':args.within5_weight,
            'error_val_examples':args.error_val_examples,
            'candidate_error_heads':list(NAMES),'candidates_frozen':True,
            'objective':'per_candidate_smooth_l1_log1p_rel_plus_within5_bce',
            'calibration':'validation_only_mean_residual_intercept_even_rows',
            'selection':'argmin_estimated_log_error_boolean_source_passthrough'}


def pack(step,cfg,networks,opts,op_probe,op_opt,stream,replay,best,median_net,median_opt,mb,rb,
         old_net,old_opt,old_best,old_threshold,closest_net,closest_opt,closest_best,
         comparators,pairwise,optimizers,experiment_best,estimators,error_opts,error_best):
    ck=pair.pack(step,cfg['parent_config'],networks,opts,op_probe,op_opt,stream,replay,best,
                 median_net,median_opt,mb,rb,old_net,old_opt,old_best,old_threshold,
                 closest_net,closest_opt,closest_best,comparators,pairwise,optimizers,experiment_best)
    ck.update({'schema':SCHEMA,'config':cfg,
               'error_estimators':{k:v.state_dict() for k,v in estimators.items()},
               'error_optimizers':{k:v.state_dict() for k,v in error_opts.items()},
               'error_best':error_best})
    return ck


def train_step(networks,op_probe,median_net,estimators,error_opts,rows,device,args):
    features=torch.stack([r[0] for r in rows]).to(device,dtype=torch.float32)
    xy=np.asarray([[r[4],r[5]] for r in rows],dtype=np.int64)
    truth,is_boolean=base.truth_for([{'a':r[1],'b':r[2],'operation':r[3]} for r in rows])
    proposals,_f,b,op=pair.model_inputs(networks,op_probe,median_net,features,xy)
    x=design(features,proposals,op)
    for model in estimators.values():model.train()
    loss,parts=errors_objective(estimators,x,proposals,truth,is_boolean,bce_weight=args.within5_weight)
    for opt in error_opts.values():opt.zero_grad(set_to_none=True)
    loss.backward()
    for model in estimators.values():nn.utils.clip_grad_norm_(model.parameters(),1.)
    for opt in error_opts.values():opt.step()
    return float(loss.detach()),parts


def execute(args):
    if args.self_test:return self_test()
    if args.train_candidates:raise ValueError('ERRORS_REQUIRES_FROZEN_CANDIDATES')
    if args.fork_v1_root is not None or args.fork_v2_root is not None or args.fork_v3_root is not None or args.fork_v3_1_root is not None:
        raise ValueError('ERRORS_FORK_ONLY_FROM_V3_2')
    if args.fork_v3_2_root is not None and (args.resume or args.eval_only):raise ValueError('ERRORS_FORK_RESUME_EXCLUSIVE')
    if args.error_width<8 or args.within5_weight<0 or args.error_val_examples<2:raise ValueError('ERRORS_INVALID_HEAD_CONFIG')
    if args.eval_best and not args.eval_only:raise ValueError('ERRORS_EVAL_BEST_ONLY_WITH_EVAL_ONLY')
    if args.additional_steps is not None and args.additional_steps<1:raise ValueError('ERRORS_INVALID_ADDITIONAL_STEPS')
    if args.threads>0:torch.set_num_threads(args.threads)
    if args.device=='cuda' and not torch.cuda.is_available():raise RuntimeError('ERRORS_CUDA_UNAVAILABLE')
    device=torch.device(args.device)
    benchmark=args.v3_root.expanduser().resolve()
    manifest,tasks=base.previous.read_benchmark(benchmark)
    identity=json.loads((benchmark/'model_identity.json').read_text(encoding='utf-8'))
    source,sha=base.source_factory(args.source_checkpoint,identity,device)
    cfg=config_for(args,manifest,sha,identity)
    root=args.results_root.expanduser().resolve()
    fork=args.fork_v3_2_root.expanduser().resolve() if args.fork_v3_2_root is not None else None
    source_root=args.source_checkpoint.expanduser().resolve().parent
    if (root==benchmark or benchmark in root.parents or root==source_root or source_root in root.parents or
        (fork is not None and (root==fork or fork in root.parents or root in fork.parents))):
        raise RuntimeError('ERRORS_UNSAFE_RESULTS_ROOT')
    latest=root/'latest.pt'
    if args.resume or args.eval_only:
        if not latest.is_file():raise RuntimeError('ERRORS_RESUME_REQUIRES_LATEST')
        source_file=(root/'error_calibrated'/'best.pt') if args.eval_best else latest
        if not source_file.is_file():raise RuntimeError('ERRORS_BEST_CHECKPOINT_MISSING')
        peek=torch.load(source_file,map_location='cpu',weights_only=True)
        if peek.get('schema')!=SCHEMA:raise RuntimeError('ERRORS_SCHEMA_MISMATCH')
        expected_cfg=cfg
    else:
        if fork is None:raise RuntimeError('ERRORS_REQUIRES_--fork-v3-2-root_OR_--resume')
        source_file=fork/'latest.pt'
        if not source_file.is_file():raise RuntimeError('ERRORS_FORK_REQUIRES_V3_2_LATEST')
        if latest.exists():raise RuntimeError('ERRORS_RUN_EXISTS_USE_--resume')
        peek=torch.load(source_file,map_location='cpu',weights_only=True)
        if peek.get('schema')!=pair.SCHEMA:raise RuntimeError('ERRORS_PARENT_SCHEMA_MISMATCH')
        expected_cfg=cfg['parent_config']
    inherited=int(peek['step'])
    target=inherited+args.additional_steps if args.additional_steps is not None else args.steps
    if not args.eval_only and target<=inherited:
        raise RuntimeError(f'ERRORS_TARGET_NOT_ABOVE_CHECKPOINT:{inherited}; USE_--additional-steps 500')
    if peek.get('config')!=expected_cfg:raise RuntimeError('ERRORS_CONFIG_DRIFT')
    if not (args.resume or args.eval_only) and root.exists() and any(root.iterdir()):
        raise RuntimeError('ERRORS_NONEMPTY_RESULTS_ROOT')
    root.mkdir(parents=True,exist_ok=True)
    cfg_file=root/'run_config.json'
    if cfg_file.exists():
        if json.loads(cfg_file.read_text(encoding='utf-8'))!=cfg:raise RuntimeError('ERRORS_CONFIG_FILE_DRIFT')
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
    payload={**peek,'schema':closest.SCHEMA,'config':cfg['parent_config']['parent_config']}
    step,best,mb,rb,old_best,old_threshold,closest_best=closest.load_payload(
        payload,cfg['parent_config']['parent_config'],networks,opts,op_probe,op_opt,stream,replay,
        median_net,median_opt,old_net,old_opt,closest_net,closest_opt)
    for name,net in comparators.items():net.load_state_dict(peek['comparators'][name])
    pairwise.load_state_dict(peek['pairwise_11'])
    for name,opt in control_opts.items():opt.load_state_dict(peek['experiment_optimizers'][name])
    experiment_best=dict(peek['experiment_best'])
    error_best={'step':0,'log_error':float('inf'),'numeric_5pct':-1.}
    if peek.get('schema')==SCHEMA:
        for name,net in estimators.items():net.load_state_dict(peek['error_estimators'][name])
        for name,opt in error_opts.items():opt.load_state_dict(peek['error_optimizers'][name])
        error_best=dict(peek['error_best'])
    for net in list(networks.values())+[op_probe,median_net,old_net,closest_net,*comparators.values(),pairwise]:
        net.eval()
        for q in net.parameters():q.requires_grad_(False)
    if fork:
        base.atomic_checkpoint(latest,pack(step,cfg,networks,opts,op_probe,op_opt,stream,replay,best,
            median_net,median_opt,mb,rb,old_net,old_opt,old_best,old_threshold,
            closest_net,closest_opt,closest_best,comparators,pairwise,control_opts,experiment_best,
            estimators,error_opts,error_best))
        base.emit('errors_forked_v3_2',step=step,parent_checkpoint=str(source_file),
                  parent_sha256=hashlib.sha256(source_file.read_bytes()).hexdigest())
    else:base.emit('errors_resumed',step=step,best=error_best)
    validation=base.previous.load_eval(benchmark,manifest,tasks,'val',args.error_val_examples)

    def validate(rows,stage):
        for net in estimators.values():net.eval()
        d=data_capture(networks,op_probe,source,median_net,closest_net,comparators,pairwise,estimators,rows,device)
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
        return d,offsets,scored

    if args.eval_only:
        _,offsets,v_report=validate(base.previous.load_eval(benchmark,manifest,tasks,'val'),'eval')
        frontiers=v_report['calibration_threshold_frontiers']
        thresholds={name:{str(point['retention_target']):point['threshold'] for point in points}
                    for name,points in frontiers.items()}
        tag='best-' if args.eval_best else ''
        base.atomic_json(root/f'evaluation-{tag}validation.json',v_report)
        base.emit('errors_evaluation',split='val_holdout',step=step,models={
            name:{'accuracy':m['numeric_5pct'],'log_error':m['mean_log1p_relative_error']}
            for name,m in v_report['policy'].items()},calibration_only_validation=True)
        for split in ('test','control'):
            rows=base.previous.load_eval(benchmark,manifest,tasks,split)
            for net in estimators.values():net.eval()
            d=data_capture(networks,op_probe,source,median_net,closest_net,comparators,pairwise,estimators,rows,device)
            report=assess(d,rows,offsets=offsets,thresholds=thresholds)
            base.atomic_json(root/f'evaluation-{tag}{split}.json',report)
            base.emit('errors_evaluation',split=split,step=step,models={
                name:{'accuracy':m['numeric_5pct'],'log_error':m['mean_log1p_relative_error']}
                for name,m in report['policy'].items()},calibration_only_validation=True)
        return 0

    import nanojev_numerate_spine_cassette_smoke as cassette
    _,frozen,tokenizer,model_device,model_info=cassette.load_source('qwen',args)
    if (model_info.get('resolved_commit')!=identity['model_info'].get('resolved_commit') or
        model_info.get('source')!=identity['model_info'].get('source') or
        str(model_info.get('dtype'))!=str(identity['model_info'].get('dtype'))):
        raise RuntimeError('ERRORS_QWEN_IDENTITY_DRIFT')
    frozen.eval()
    base.previous.verify_capture_alignment(torch,frozen,tokenizer,model_device,benchmark,tasks,args.max_prompt_tokens)
    base.emit('errors_initialized',step=step,target_step=target,candidate_heads_trainable=False,
              error_estimators=len(estimators),parameters={name:sum(q.numel() for q in net.parameters())
                  for name,net in estimators.items()},parent_controllers_frozen=True)
    started=time.monotonic();capture_time=update_time=0.;recent=[]
    for k in range(step+1,target+1):
        t0=time.monotonic()
        batch=stream.batch(args.batch_size)
        f=base.previous.extract_batched(torch,frozen,tokenizer,[r['prompt'] for r in batch],model_device,args.max_prompt_tokens)
        if f.requires_grad:raise RuntimeError('ERRORS_BACKBONE_GRADIENT_DETECTED')
        xy=base.source_decode(source,f.to(device));replay.add(f,batch,xy)
        capture_time+=time.monotonic()-t0;t0=time.monotonic()
        for _ in range(args.replay_updates):
            sample=replay.sample(args.replay_batch)
            loss,parts=train_step(networks,op_probe,median_net,estimators,error_opts,sample,device,args)
            recent.append(loss)
        update_time+=time.monotonic()-t0
        if k%args.report_every==0 or k==target:
            base.emit('errors_train_progress',step=k,mean_loss=round(float(np.mean(recent)),6),
                      last_parts=parts,elapsed_seconds=round(time.monotonic()-started,2),
                      replay_records=len(replay.rows),examples_seen=len(stream.seen),
                      shared_capture_seconds=round(capture_time,2),head_update_seconds=round(update_time,2))
            recent=[]
        if k%args.eval_every==0 or k==target:
            _,offsets,report=validate(validation,'train')
            base.atomic_json(root/f'validation-step-{k:06d}.json',{'step':k,**report})
            brief={name:{'accuracy':m['numeric_5pct'],'near':m['nearest_candidate_accuracy'],
                         'log_error':m['mean_log1p_relative_error'],'improved':m['improved'],'worsened':m['worsened']}
                   for name,m in report['policy'].items()}
            base.emit('errors_validation',step=k,models=brief,
                      error_mae={name:round(q['mae_log_error'],5) for name,q in report['per_candidate'].items()})
            current=(report['policy']['error_calibrated']['mean_log1p_relative_error'],
                     -report['policy']['error_calibrated']['numeric_5pct'])
            if current<(error_best['log_error'],-error_best['numeric_5pct']):
                error_best={'step':k,'log_error':float(current[0]),
                            'numeric_5pct':float(report['policy']['error_calibrated']['numeric_5pct'])}
                base.atomic_checkpoint(root/'error_calibrated'/'best.pt',pack(k,cfg,networks,opts,op_probe,op_opt,stream,replay,best,
                    median_net,median_opt,mb,rb,old_net,old_opt,old_best,old_threshold,
                    closest_net,closest_opt,closest_best,comparators,pairwise,control_opts,experiment_best,
                    estimators,error_opts,error_best))
                base.emit('errors_best',**error_best)
        if k%args.checkpoint_every==0 or k==target:
            base.atomic_checkpoint(latest,pack(k,cfg,networks,opts,op_probe,op_opt,stream,replay,best,
                median_net,median_opt,mb,rb,old_net,old_opt,old_best,old_threshold,
                closest_net,closest_opt,closest_best,comparators,pairwise,control_opts,experiment_best,
                estimators,error_opts,error_best))
            base.emit('errors_checkpoint',step=k,path=str(latest))
    base.emit('errors_complete',step=target,best=error_best,results_root=str(root))
    return 0


def self_test():
    torch.manual_seed(33)
    c=np.array([[100,150,120,140,130,110,125,190,180,170,160],
                [500,600,700,800,900,1000,1100,1200,1300,1400,1500]],dtype=float)
    y=np.array([115.,200.])
    f=torch.randn(2,base.INPUT_WIDTH)
    op=torch.tensor([base.OP_IDS['written_product']]*2)
    x=design(f,c,op)
    models={name:ErrorEstimator(8) for name in NAMES}
    loss,parts=errors_objective(models,x,c,y,[False,False])
    loss.backward()
    assert parts['all_wrong_rows']>=1
    assert all(torch.isfinite(z.grad).all() for m in models.values() for z in m.parameters() if z.grad is not None)
    predicted,logits=predict_all(models,x)
    indices,_=select_min_error(predicted.detach().numpy(),[False,True])
    assert indices[1]==0
    offs=calibrate_offsets(predicted.detach().numpy(),closest.numerical_errors(c,y)[1],[False,False],[0])
    assert len(offs)==N and np.isfinite(offs).all()
    base.emit('errors_self_test_pass',candidate_count=N,independent_estimators=N,
              all_wrong_supervision=True,parent_frozen=True)
    return 0


if __name__=='__main__':raise SystemExit(execute(parser().parse_args()))
