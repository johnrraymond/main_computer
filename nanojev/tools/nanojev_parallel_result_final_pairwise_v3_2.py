#!/usr/bin/env python3
"""NanoJev FINAL v3.2: one-capture comparator/ablation/pairwise search.

Fork from FINAL closest v3.1 latest.pt. Immutable R0-R10 candidate heads and
frozen Qwen; train three independent R0-vs-residual comparators (candidate
numeric only, backbone only, combined) plus an all-55-pairs eleven-way ranker
using identical replay batches. ALWAYS select a candidate on numerical rows,
even if every RESULT is incorrect. No ground truth enters inference features.
Validation only calibrates threshold/retention frontiers; test is never used to
fit thresholds or choose checkpoints.
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
import nanojev_parallel_result_final_closest_v3_1 as parent

SCHEMA='nanojev.parallel-result-final-pairwise.v3.2'
NAMES=parent.NAMES
N=parent.N
RESIDUAL_INDEX=NAMES.index('residual')
ABLATIONS=('numeric_only','backbone_only','combined')
PAIRS=tuple((i,j) for i in range(N) for j in range(i+1,N))
NUMERIC_WIDTH=4*N+7


def numeric_features(proposals):
    """Inference-only, scale-compressed candidate numerical and disagreement data."""
    c=np.asarray(proposals,dtype=np.float64)
    if c.ndim!=2 or c.shape[1]!=N or not np.isfinite(c).all():
        raise ValueError('PAIRWISE_INVALID_CANDIDATES')
    z=base.signed_encode(c)
    med=np.median(z,axis=1,keepdims=True)
    dif=z-z[:,0:1]
    centroid=z-med
    distances=np.abs(z[:,:,None]-z[:,None,:])
    for i in range(N): distances[:,i,i]=np.inf
    nearest=distances.min(axis=2)
    spread=np.median(np.abs(centroid),axis=1)
    stats=np.column_stack((med[:,0],spread,z.max(axis=1)-z.min(axis=1),
                           np.std(z,axis=1),np.abs(dif[:,RESIDUAL_INDEX]),
                           np.abs(z[:,0]-med[:,0]),
                           (np.abs(dif)<.005).sum(axis=1)/N))
    out=np.column_stack((z,dif,centroid,nearest,stats)).astype(np.float32)
    if out.shape[1]!=NUMERIC_WIDTH or not np.isfinite(out).all():
        raise RuntimeError('PAIRWISE_FEATURE_DIMENSION_OR_NONFINITE')
    return out


def feature_design(features,proposals,predicted_ops):
    n=torch.as_tensor(numeric_features(proposals),device=features.device)
    f=features.detach().float()
    op=F.one_hot(predicted_ops.long(),num_classes=len(base.OP_CLASSES)).float()
    # None of the inputs depend on truth or training targets.
    return {'numeric_only':n,
            'backbone_only':torch.cat((f,op),dim=1),
            'combined':torch.cat((f,n,op),dim=1)}


class BinaryComparator(nn.Module):
    """R7 vs R0; two outputs: win logit and predicted signed log-error margin."""
    def __init__(self,input_width,width=128):
        super().__init__()
        self.trunk=nn.Sequential(nn.LayerNorm(input_width,elementwise_affine=False),
                                 nn.Linear(input_width,width),nn.GELU(),
                                 nn.Linear(width,width),nn.GELU())
        self.out=nn.Linear(width,2)
        nn.init.zeros_(self.out.bias)

    def forward(self,x):
        y=self.out(self.trunk(x.float()))
        return y[:,0],y[:,1]


class PairwiseRanker(nn.Module):
    """55 simultaneous comparisons, scores from antisymmetric pairwise votes."""
    def __init__(self,input_width,width=128):
        super().__init__()
        self.net=nn.Sequential(nn.LayerNorm(input_width,elementwise_affine=False),
                               nn.Linear(input_width,width),nn.GELU(),
                               nn.Linear(width,width),nn.GELU(),
                               nn.Linear(width,len(PAIRS)))
        nn.init.zeros_(self.net[-1].bias)

    def forward(self,x):return self.net(x.float())


def pair_scores(logits):
    # Positive logit means i is closer than j. Sum zero across classes.
    score=logits.new_zeros(logits.shape[0],N)
    for k,(i,j) in enumerate(PAIRS):
        score[:,i] += logits[:,k]
        score[:,j] -= logits[:,k]
    return score


def closest_targets(proposals,truth):
    rel,log=parent.numerical_errors(proposals,truth)
    return rel,log


def balanced_bce(logits,targets,mask=None):
    """Within-minibatch symmetric class weighting, bounded for rare outcomes."""
    targets=targets.float()
    if mask is None:mask=torch.ones_like(targets,dtype=torch.bool)
    active=mask.float()
    count=active.sum(dim=0).clamp_min(1.)
    pos=(targets*active).sum(dim=0)
    neg=count-pos
    # Avoid divisions by 0 and extreme weights on singleton classes.
    wp=(count/(2*pos.clamp_min(1.))).clamp(max=8.)
    wn=(count/(2*neg.clamp_min(1.))).clamp(max=8.)
    w=torch.where(targets>.5,wp,wn).detach()*active
    return (F.binary_cross_entropy_with_logits(logits,targets,reduction='none')*w).sum()/w.sum().clamp_min(1.)


def objectives(comparators,pairwise,design,proposals,truth,is_boolean,regression_weight=.4,rank_weight=.4):
    numeric=~np.asarray(is_boolean,dtype=bool)
    if not numeric.any():
        z=sum((m.out.weight.sum()*0. for m in comparators.values()),pairwise.net[-1].weight.sum()*0.)
        return z,{'binary':{},'pairwise':0.,'minority_nearest':0}
    relative,logged=closest_targets(np.asarray(proposals)[numeric],np.asarray(truth)[numeric])
    mask=torch.as_tensor(numeric,device=design['combined'].device)
    logged_t=torch.as_tensor(np.clip(logged,0.,20.),device=mask.device,dtype=torch.float32)
    binary_label=torch.as_tensor(relative[:,RESIDUAL_INDEX]<relative[:,0]-1e-12,device=mask.device,dtype=torch.float32)
    # Continuous margin supports classification even when both predictions exceed 5%.
    delta=(logged_t[:,0]-logged_t[:,RESIDUAL_INDEX]).clamp(-10.,10.)
    total=design['combined'].sum()*0.
    parts={}
    for name in ABLATIONS:
        logits,margins=comparators[name](design[name][mask])
        classification=balanced_bce(logits,binary_label)
        regression=F.smooth_l1_loss(margins,delta,beta=.2)
        l=classification+regression_weight*regression
        total=total+l
        parts[name]={'bce':float(classification.detach()),'margin':float(regression.detach())}
    pairlogits=pairwise(design['combined'][mask])
    pair_label=np.stack([(relative[:,i]<relative[:,j]) for i,j in PAIRS],axis=1)
    ties=np.stack([np.isclose(relative[:,i],relative[:,j],rtol=0,atol=1e-10) for i,j in PAIRS],axis=1)
    ptarget=torch.as_tensor(pair_label,dtype=torch.float32,device=mask.device)
    pmask=torch.as_tensor(~ties,dtype=torch.bool,device=mask.device)
    pair_loss=balanced_bce(pairlogits,ptarget,pmask)
    scores=pair_scores(pairlogits)
    winners=np.argmin(relative,axis=1)
    winners_t=torch.as_tensor(winners,dtype=torch.long,device=mask.device)
    # Explicit winner supervision so the Borda aggregation is useful at inference.
    winner_loss=F.cross_entropy(scores/(N*1.5),winners_t)
    total=total+pair_loss+rank_weight*winner_loss
    return total,{'binary':parts,'pairwise':float(pair_loss.detach()),
                  'rank':float(winner_loss.detach()),'minority_nearest':int((winners!=0).sum()),
                  'r7_preferred':int(binary_label.sum().item())}


def choose_ids(scores,boolean):
    ids=np.asarray(scores).argmax(axis=1).astype(np.int64)
    ids[np.asarray(boolean,dtype=bool)]=0
    return ids


def result_from_ids(proposals,ids):
    return parent.select(proposals,ids)


def measure(proposals,truth,true_boolean,selected,oracle=None):
    c=np.asarray(proposals); t=np.asarray(truth);b=np.asarray(true_boolean,dtype=bool)
    numeric=~b
    err,logged=closest_targets(c,t)
    oracle_ids=err.argmin(axis=1) if oracle is None else np.asarray(oracle)
    ids=np.asarray(selected)
    pred=result_from_ids(c,ids)
    src=result_from_ids(c,np.zeros(len(t),dtype=np.int64))
    final_hits=base.exact_or_relative_hits(pred,t,b)
    src_hits=base.exact_or_relative_hits(src,t,b)
    oracle_hits=np.column_stack([base.exact_or_relative_hits(c[:,j],t,b) for j in range(N)]).any(axis=1)
    all_wrong=numeric & ~oracle_hits
    minority_opportunity=numeric & (oracle_ids!=0)
    def avg(a):return float(np.mean(np.asarray(a)[numeric])) if numeric.any() else 0.
    chosen_err=err[np.arange(len(t)),ids]
    chosen_logged=logged[np.arange(len(t)),ids]
    nearest=chosen_err <= err.min(axis=1)+1e-12
    return {'numeric_rows':int(numeric.sum()),'numeric_5pct':avg(final_hits),
            'source_numeric_5pct':avg(src_hits),'oracle_numeric_5pct':avg(oracle_hits),
            'nearest_candidate_accuracy':avg(nearest),
            'nearest_accuracy_all_wrong':float(nearest[all_wrong].mean()) if all_wrong.any() else None,
            'mean_log_regret_all_wrong':float((chosen_logged-logged.min(axis=1))[all_wrong].mean()) if all_wrong.any() else None,
            'all_candidates_wrong_count':int(all_wrong.sum()),
            'nearest_accuracy_minority_opportunities':float(nearest[minority_opportunity].mean()) if minority_opportunity.any() else None,
            'source_nearest_accuracy':avg(err[:,0]<=err.min(axis=1)+1e-12),
            'mean_relative_error':avg(chosen_err),
            'source_mean_relative_error':avg(err[:,0]),
            'mean_log1p_relative_error':avg(chosen_logged),
            'source_mean_log1p_relative_error':avg(logged[:,0]),
            'oracle_mean_log1p_relative_error':avg(logged.min(axis=1)),
            'mean_log_regret':avg(chosen_logged-logged.min(axis=1)),
            'improved':int((numeric&(chosen_err<err[:,0]-1e-12)).sum()),
            'worsened':int((numeric&(chosen_err>err[:,0]+1e-12)).sum()),
            'tied':int((numeric&(np.abs(chosen_err-err[:,0])<=1e-12)).sum()),
            'rescued_5pct':int((numeric&final_hits&~src_hits).sum()),
            'damaged_5pct':int((numeric&src_hits&~final_hits).sum()),
            'source_retention':float(1-(numeric&src_hits&~final_hits).sum()/max(1,int((numeric&src_hits).sum()))),
            'minority_nearest_opportunities':int((numeric&(oracle_ids!=0)).sum()),
            'minority_nearest_found':int((numeric&(ids==oracle_ids)&(oracle_ids!=0)).sum()),
            'selected_count':{name:int((numeric&(ids==j)).sum()) for j,name in enumerate(NAMES)}}


def binary_diagnostics(proposals,truth,boolean,logits,selected):
    rel,_=closest_targets(proposals,truth)
    numeric=~np.asarray(boolean,dtype=bool)
    y=(rel[:,RESIDUAL_INDEX]<rel[:,0]-1e-12)&numeric
    p=(np.asarray(logits)>0)&numeric
    tp=int((p&y).sum());tn=int((~p&~y&numeric).sum())
    fp=int((p&~y&numeric).sum());fn=int((~p&y).sum())
    precision=tp/max(1,tp+fp);recall=tp/max(1,tp+fn)
    specificity=tn/max(1,tn+fp)
    metrics=measure(proposals,truth,boolean,selected)
    metrics.update({'comparator':{'tp':tp,'tn':tn,'fp':fp,'fn':fn,
                  'precision':precision,'recall':recall,
                  'balanced_accuracy':(recall+specificity)/2,
                  'always_source_balanced_accuracy':.5,
                  'r7_closer_count':int(y.sum()),
                  'r7_switch_count':int(p.sum())}})
    return metrics


def threshold_frontier(proposals,truth,boolean,ids,score,
                       targets=(1.,.99,.98,.95,0.)):
    """Research-only calibration; never changes ungated experimental predictions."""
    c=np.asarray(proposals);t=np.asarray(truth);b=np.asarray(boolean,dtype=bool)
    ids=np.asarray(ids,dtype=np.int64);score=np.asarray(score,dtype=np.float64)
    assert len(ids)==len(t)==len(score)
    numeric=~b
    src_hits=base.exact_or_relative_hits(c[:,0],t,b)
    src_count=int((src_hits&numeric).sum())
    if (~np.isfinite(score)).any():raise ValueError('NONFINITE_SWITCH_SCORE')
    # Thresholds include no-switch, all-switch, and every observed score.
    active_scores=score[(ids!=0)&numeric]
    if active_scores.size:
        thresholds=np.r_[np.nextafter(active_scores.max(),np.inf),
                          np.unique(active_scores),np.nextafter(active_scores.min(),-np.inf)]
    else:
        thresholds=np.asarray([0.])
    out=[]
    for retention in targets:
        best=None
        for threshold in thresholds:
            choice=np.where(numeric & (ids!=0) & (score>=threshold),ids,0)
            hit=base.exact_or_relative_hits(result_from_ids(c,choice),t,b)
            damaged=int((numeric&src_hits&~hit).sum())
            retained=1-damaged/max(1,src_count)
            if retained+1e-12<retention:continue
            rescued=int((numeric&~src_hits&hit).sum())
            accuracy=float(hit[numeric].mean()) if numeric.any() else 0.
            point={'retention_target':float(retention),'threshold':float(threshold),
                   'source_retention':float(retained),'numeric_5pct':accuracy,
                   'rescued':rescued,'damaged':damaged,'switches':int((choice!=0).sum())}
            if best is None or (accuracy,rescued,-damaged)>(best['numeric_5pct'],best['rescued'],-best['damaged']):
                best=point
        out.append(best)
    return out


def model_inputs(networks,op_probe,median_net,features,xy):
    proposals,_,inferred_bool,pred_ops=legacy.infer_candidates(networks,op_probe,median_net,features,xy)
    return proposals,feature_design(features,proposals,pred_ops),inferred_bool,pred_ops


def train_step(networks,op_probe,median_net,comparators,pairwise,optimizers,rows,device,args):
    features=torch.stack([r[0] for r in rows]).to(device,dtype=torch.float32)
    xy=np.asarray([[r[4],r[5]] for r in rows],dtype=np.int64)
    truth,boolean=base.truth_for([{'a':r[1],'b':r[2],'operation':r[3]} for r in rows])
    proposals,design,_,_=model_inputs(networks,op_probe,median_net,features,xy)
    for model in list(comparators.values())+[pairwise]:model.train()
    loss,parts=objectives(comparators,pairwise,design,proposals,truth,boolean,
                         regression_weight=args.margin_weight,rank_weight=args.pair_rank_weight)
    for opt in optimizers.values():opt.zero_grad(set_to_none=True)
    loss.backward()
    for model in list(comparators.values())+[pairwise]:nn.utils.clip_grad_norm_(model.parameters(),1.)
    for opt in optimizers.values():opt.step()
    return float(loss.detach()),parts


@torch.inference_mode()
def evaluate(networks,op_probe,source,median_net,closest_net,comparators,pairwise,eval_rows,device,
             batch_size=64,with_frontier=True,calibration_thresholds=None):
    features,xy,ops,families=eval_rows
    truth=np.asarray([base.result_value(int(a),int(b),op) for (a,b),op in zip(xy,ops)],dtype=np.float64)
    boolean=np.asarray([op in base.BOOL_OPS for op in ops],dtype=bool)
    proposals_l=[];bool_l=[];logits_l={k:[] for k in ABLATIONS};margins_l={k:[] for k in ABLATIONS}
    pair_l=[]; old_l=[];pred_ops_l=[];calibration_candidates={};calibration_scores={}
    for start in range(0,len(xy),batch_size):
        end=min(start+batch_size,len(xy));f=torch.as_tensor(features[start:end],device=device,dtype=torch.float32)
        source_xy=base.source_decode(source,f)
        proposals,design,inferred_bool,pred_ops=model_inputs(networks,op_probe,median_net,f,source_xy)
        proposals_l.append(proposals);bool_l.append(inferred_bool)
        pred_ops_l.extend(base.OP_CLASSES[int(x)] for x in pred_ops.detach().cpu().numpy())
        for key in ABLATIONS:
            logits,margins=comparators[key](design[key]);logits_l[key].append(logits.detach().cpu().numpy())
            margins_l[key].append(margins.detach().cpu().numpy())
        pair_l.append(pairwise(design['combined']).detach().cpu().numpy())
        # Historical v3.1 reference: no retraining or modification.
        original=legacy.final_design(f,pred_ops,torch.as_tensor(proposals[:,0],device=device,dtype=torch.float32),
                       proposals,np.median(proposals[:,:len(median.CANDIDATES)],axis=1),op_probe)
        old_ids,_=parent.closest_predictions(closest_net(original),inferred_bool)
        old_l.append(old_ids)
    c=np.concatenate(proposals_l);bpred=np.concatenate(bool_l)
    chosen_ids={}
    result={'rows':len(truth),'operation_accuracy':float(np.mean(np.asarray(pred_ops_l)==np.asarray(ops))),
            'candidate_snapshot':'frozen_imported_v3.1','reference':{},'models':{},'frontiers_validation_only':{}}
    relative,_=closest_targets(c,truth)
    oracle_ids=relative.argmin(axis=1)
    zero=np.zeros(len(truth),dtype=np.int64)
    result['reference']['source']=measure(c,truth,boolean,zero)
    result['reference']['always_residual']=measure(c,truth,boolean,np.where(bpred,0,RESIDUAL_INDEX))
    result['reference']['closest_v3_1']=measure(c,truth,boolean,np.concatenate(old_l))
    for key in ABLATIONS:
        logits=np.concatenate(logits_l[key]);margin=np.concatenate(margins_l[key])
        indices=np.where((logits>0)&(~bpred),RESIDUAL_INDEX,0)
        result['models'][key]=binary_diagnostics(c,truth,boolean,logits,indices)
        chosen_ids[key]=indices
        result['models'][key]['margin_mae']=float(np.abs(margin[~boolean]-(np.log1p(relative[~boolean,0])-np.log1p(relative[~boolean,RESIDUAL_INDEX]))).mean()) if (~boolean).any() else None
        proposed=np.where(bpred,0,RESIDUAL_INDEX)
        calibration_candidates[key]=proposed
        calibration_scores[key]=logits
        if with_frontier:
            result['frontiers_validation_only'][key]=threshold_frontier(c,truth,boolean,proposed,logits)
    pair_logits=np.concatenate(pair_l)
    scores=pair_scores(torch.as_tensor(pair_logits)).numpy()
    ids=choose_ids(scores,bpred)
    result['models']['pairwise_11']=measure(c,truth,boolean,ids,oracle_ids)
    chosen_ids['pairwise_11']=ids
    result['models']['pairwise_11']['mean_pairwise_margin']=float(np.abs(pair_logits[~boolean]).mean()) if (~boolean).any() else None
    # Ranking gain over source: full-11 class has nonzero odds when score gap favors alternative.
    gap=scores[np.arange(len(ids)),ids]-scores[:,0]
    calibration_candidates['pairwise_11']=ids
    calibration_scores['pairwise_11']=gap
    if with_frontier:
        result['frontiers_validation_only']['pairwise_11']=threshold_frontier(c,truth,boolean,ids,gap)
    if calibration_thresholds is not None:
        result['calibrated_using_validation_thresholds']={}
        for name, thresholds in calibration_thresholds.items():
            results={}
            for retention, threshold in thresholds.items():
                proposed=calibration_candidates[name]
                ids_policy=np.where((proposed!=0)&(~bpred)&
                             (calibration_scores[name]>=float(threshold)),proposed,0)
                results[retention]=measure(c,truth,boolean,ids_policy)
            result['calibrated_using_validation_thresholds'][name]=results
    result['best_possible']=measure(c,truth,boolean,np.where(bpred,0,oracle_ids))
    result['best_possible']['note']='oracle_only_not_available_at_inference'
    # Slice-by-operation diagnostics use ground truth solely for evaluation.
    result['by_operation']={}
    operation_array=np.asarray(ops)
    for op in sorted(set(ops)):
        eligible=(operation_array==op)&(~boolean)
        if not eligible.any():continue
        source_hit=base.exact_or_relative_hits(c[eligible,0],truth[eligible],boolean[eligible])
        item={'numeric_rows':int(eligible.sum()),'source_numeric_5pct':float(source_hit.mean()),'models':{}}
        for name,indices in chosen_ids.items():
            prediction=result_from_ids(c[eligible],indices[eligible])
            hit=base.exact_or_relative_hits(prediction,truth[eligible],boolean[eligible])
            rel=np.abs(prediction-truth[eligible])/np.maximum(np.abs(truth[eligible]),1.)
            item['models'][name]={'numeric_5pct':float(hit.mean()),
                                  'mean_log1p_relative_error':float(np.log1p(rel).mean())}
        result['by_operation'][op]=item
    return result


def parser():
    p=parent.parser();p.description=__doc__
    p.set_defaults(results_root=Path.home()/'NanoJev'/'runs'/'parallel_result_final_pairwise_v3_2')
    p.add_argument('--fork-v3-1-root',type=Path,default=None)
    p.add_argument('--comparison-width',type=int,default=128)
    p.add_argument('--margin-weight',type=float,default=.4)
    p.add_argument('--pair-rank-weight',type=float,default=.4)
    return p


def config_for(args,manifest,sha,identity):
    return {'schema':SCHEMA,'parent_config':parent.cfg_for(args,manifest,sha,identity),
            'comparison_width':args.comparison_width,'margin_weight':args.margin_weight,
            'pair_rank_weight':args.pair_rank_weight,'head_names':list(ABLATIONS)+['pairwise_11'],
            'binary_indices':[0,RESIDUAL_INDEX],
            'objective':'binary_balanced_margin_and_55_pair_balanced_bce_plus_rank',
            'ablation_operation_control':'backbone_only_and_combined_both_include_predicted_operation',
            'candidates_frozen':True}


def pack(step,cfg,networks,opts,op_probe,op_opt,stream,replay,best,median_net,median_opt,mb,rb,
         old_net,old_opt,old_best,old_threshold,closest_net,closest_opt,closest_best,
         comparators,pairwise,optimizers,experiment_best):
    ck=parent.pack(step,cfg['parent_config'],networks,opts,op_probe,op_opt,stream,replay,best,
                   median_net,median_opt,mb,rb,old_net,old_opt,old_best,old_threshold,
                   closest_net,closest_opt,closest_best)
    ck.update({'schema':SCHEMA,'config':cfg,
               'comparators':{k:m.state_dict() for k,m in comparators.items()},
               'pairwise_11':pairwise.state_dict(),
               'experiment_optimizers':{k:o.state_dict() for k,o in optimizers.items()},
               'experiment_best':experiment_best})
    return ck


def execute(args):
    if args.self_test:return self_test()
    if args.train_candidates:raise ValueError('PAIRWISE_REQUIRES_FROZEN_CANDIDATES')
    if args.fork_v1_root is not None or args.fork_v2_root is not None or args.fork_v3_root is not None:
        raise ValueError('PAIRWISE_FORK_ONLY_FROM_V3_1')
    if args.fork_v3_1_root is not None and (args.resume or args.eval_only):
        raise ValueError('PAIRWISE_FORK_AND_RESUME_EXCLUSIVE')
    if args.comparison_width<8 or args.margin_weight<0 or args.pair_rank_weight<0:
        raise ValueError('PAIRWISE_INVALID_HEAD_CONFIG')
    if args.additional_steps is not None and args.additional_steps<1:
        raise ValueError('PAIRWISE_INVALID_ADDITIONAL_STEPS')
    if args.threads>0:torch.set_num_threads(args.threads)
    if args.device=='cuda' and not torch.cuda.is_available():raise RuntimeError('PAIRWISE_CUDA_UNAVAILABLE')
    device=torch.device(args.device)
    benchmark=args.v3_root.expanduser().resolve()
    manifest,tasks=base.previous.read_benchmark(benchmark)
    identity=json.loads((benchmark/'model_identity.json').read_text(encoding='utf-8'))
    source,sha=base.source_factory(args.source_checkpoint,identity,device)
    cfg=config_for(args,manifest,sha,identity)
    root=args.results_root.expanduser().resolve()
    fork=args.fork_v3_1_root.expanduser().resolve() if args.fork_v3_1_root is not None else None
    src_root=args.source_checkpoint.expanduser().resolve().parent
    if (root==benchmark or benchmark in root.parents or root==src_root or src_root in root.parents or
        (fork is not None and (root==fork or fork in root.parents or root in fork.parents))):
        raise RuntimeError('PAIRWISE_UNSAFE_RESULTS_ROOT')
    latest=root/'latest.pt'
    if args.resume or args.eval_only:
        if not latest.is_file():raise RuntimeError('PAIRWISE_RESUME_REQUIRES_LATEST')
        source_file=latest;peek=torch.load(latest,map_location='cpu',weights_only=True)
        if peek.get('schema')!=SCHEMA:raise RuntimeError('PAIRWISE_SCHEMA_MISMATCH')
        expected_cfg=cfg
    else:
        if fork is None:raise RuntimeError('PAIRWISE_REQUIRES_--fork-v3-1-root_OR_--resume')
        source_file=fork/'latest.pt'
        if not source_file.is_file():raise RuntimeError('PAIRWISE_FORK_REQUIRES_V3_1_LATEST')
        if latest.exists():raise RuntimeError('PAIRWISE_RUN_EXISTS_USE_--resume')
        peek=torch.load(source_file,map_location='cpu',weights_only=True)
        if peek.get('schema')!=parent.SCHEMA:raise RuntimeError('PAIRWISE_PARENT_SCHEMA_MISMATCH')
        expected_cfg=cfg['parent_config']
    inherited=int(peek['step'])
    target=inherited+args.additional_steps if args.additional_steps is not None else args.steps
    if not args.eval_only and target<=inherited:
        raise RuntimeError(f'PAIRWISE_TARGET_NOT_ABOVE_CHECKPOINT:{inherited}; USE_--additional-steps 500')
    if peek.get('config')!=expected_cfg:raise RuntimeError('PAIRWISE_CONFIG_DRIFT')
    if not (args.resume or args.eval_only) and root.exists() and any(root.iterdir()):
        raise RuntimeError('PAIRWISE_NONEMPTY_RESULTS_ROOT')
    root.mkdir(parents=True,exist_ok=True)
    cfg_file=root/'run_config.json'
    if cfg_file.exists():
        if json.loads(cfg_file.read_text(encoding='utf-8'))!=cfg:raise RuntimeError('PAIRWISE_CONFIG_FILE_DRIFT')
    else:
        if any(root.iterdir()):raise RuntimeError('PAIRWISE_NONEMPTY_RESULTS_ROOT')
        base.atomic_json(cfg_file,cfg)
    torch.manual_seed(args.seed)
    networks={n:base.ResultHead(n,args.width).to(device) for n in base.NAMES}
    opts={n:torch.optim.AdamW(net.parameters(),lr=args.lr,weight_decay=args.weight_decay) for n,net in networks.items()}
    op_probe=base.SharedOperationProbe().to(device)
    op_opt=torch.optim.AdamW(op_probe.parameters(),lr=args.lr,weight_decay=args.weight_decay)
    median_net=median.MedianResidual(args.median_width).to(device)
    median_opt=torch.optim.AdamW(median_net.parameters(),lr=args.lr,weight_decay=args.weight_decay)
    old_net=legacy.FinalHead(args.final_width).to(device)
    old_opt=torch.optim.AdamW(old_net.parameters(),lr=args.lr,weight_decay=args.weight_decay)
    closest_net=parent.ClosestFinal(args.closest_width).to(device)
    closest_opt=torch.optim.AdamW(closest_net.parameters(),lr=args.lr,weight_decay=args.weight_decay)
    comparators={name:BinaryComparator(NUMERIC_WIDTH if name=='numeric_only' else
                    base.INPUT_WIDTH+len(base.OP_CLASSES) if name=='backbone_only' else
                    base.INPUT_WIDTH+NUMERIC_WIDTH+len(base.OP_CLASSES),args.comparison_width).to(device)
                 for name in ABLATIONS}
    pairwise=PairwiseRanker(base.INPUT_WIDTH+NUMERIC_WIDTH+len(base.OP_CLASSES),args.comparison_width).to(device)
    optimizers={name:torch.optim.AdamW(net.parameters(),lr=args.lr,weight_decay=args.weight_decay)
                for name,net in {**comparators,'pairwise_11':pairwise}.items()}
    stream=base.previous.FreshPairs(args.seed,base.previous.reserved_pairs(tasks))
    replay=base.Replay(args.replay_capacity,args.seed+71)
    payload={**peek,'schema':parent.SCHEMA,'config':cfg['parent_config']}
    step,best,mb,rb,old_best,old_threshold,closest_best=parent.load_payload(
        payload,cfg['parent_config'],networks,opts,op_probe,op_opt,stream,replay,
        median_net,median_opt,old_net,old_opt,closest_net,closest_opt)
    experiment_best={name:{'step':0,'log_error':float('inf'),'numeric_5pct':-1.}
                     for name in (*ABLATIONS,'pairwise_11')}
    if peek.get('schema')==SCHEMA:
        for name,net in comparators.items():net.load_state_dict(peek['comparators'][name])
        pairwise.load_state_dict(peek['pairwise_11'])
        for name,opt in optimizers.items():opt.load_state_dict(peek['experiment_optimizers'][name])
        experiment_best=dict(peek['experiment_best'])
    # Keep all parents and candidate head weights fixed throughout this lineage.
    for net in list(networks.values())+[op_probe,median_net,old_net,closest_net]:
        net.eval()
        for p in net.parameters():p.requires_grad_(False)
    if fork:
        base.atomic_checkpoint(latest,pack(step,cfg,networks,opts,op_probe,op_opt,stream,replay,best,
             median_net,median_opt,mb,rb,old_net,old_opt,old_best,old_threshold,
             closest_net,closest_opt,closest_best,comparators,pairwise,optimizers,experiment_best))
        base.emit('pairwise_forked_v3_1',step=step,parent_checkpoint=str(source_file),
                  parent_sha256=hashlib.sha256(source_file.read_bytes()).hexdigest())
    else:base.emit('pairwise_resumed',step=step,best=experiment_best)
    validation=base.previous.load_eval(benchmark,manifest,tasks,'val',args.val_examples)
    if args.eval_only:
        # Only validation receives diagnostics with optimized thresholds.
        thresholds_from_val=None
        for split in ('val','test','control'):
            eval_rows=base.previous.load_eval(benchmark,manifest,tasks,split)
            report=evaluate(networks,op_probe,source,median_net,closest_net,comparators,pairwise,
                            eval_rows,device,with_frontier=(split=='val'),
                            calibration_thresholds=thresholds_from_val if split!='val' else None)
            if split=='val':
                thresholds_from_val={name:{str(q['retention_target']):q['threshold'] for q in values}
                                     for name,values in report['frontiers_validation_only'].items()}
            base.emit('pairwise_evaluation',split=split,step=step,**report)
        return 0
    import nanojev_numerate_spine_cassette_smoke as cassette
    _,frozen,tokenizer,model_device,model_info=cassette.load_source('qwen',args)
    if (model_info.get('resolved_commit')!=identity['model_info'].get('resolved_commit') or
        model_info.get('source')!=identity['model_info'].get('source') or
        str(model_info.get('dtype'))!=str(identity['model_info'].get('dtype'))):
        raise RuntimeError('PAIRWISE_QWEN_IDENTITY_DRIFT')
    frozen.eval()
    base.previous.verify_capture_alignment(torch,frozen,tokenizer,model_device,benchmark,tasks,args.max_prompt_tokens)
    base.emit('pairwise_initialized',step=step,target_step=target,candidate_heads_trainable=False,
              backbone_trainable_parameters=0,comparator_variants=list(ABLATIONS),
              candidate_count=N,pair_count=len(PAIRS),
              head_parameters={n:sum(p.numel() for p in m.parameters()) for n,m in
                               {**comparators,'pairwise_11':pairwise}.items()})
    started=time.monotonic();capture_time=update_time=0.;recent=[]
    for k in range(step+1,target+1):
        t0=time.monotonic()
        batch=stream.batch(args.batch_size)
        f=base.previous.extract_batched(torch,frozen,tokenizer,
                    [r['prompt'] for r in batch],model_device,args.max_prompt_tokens)
        if f.requires_grad:raise RuntimeError('PAIRWISE_BACKBONE_GRADIENT_DETECTED')
        xy=base.source_decode(source,f.to(device));replay.add(f,batch,xy)
        capture_time+=time.monotonic()-t0;t0=time.monotonic()
        for _ in range(args.replay_updates):
            sample=replay.sample(args.replay_batch)
            loss,parts=train_step(networks,op_probe,median_net,comparators,pairwise,optimizers,sample,device,args)
            recent.append(loss)
        update_time+=time.monotonic()-t0
        if k%args.report_every==0 or k==target:
            base.emit('pairwise_train_progress',step=k,mean_loss=round(float(np.mean(recent)),6),
                      last_parts=parts,replay_records=len(replay.rows),examples_seen=len(stream.seen),
                      elapsed_seconds=round(time.monotonic()-started,2),
                      shared_capture_seconds=round(capture_time,2),head_update_seconds=round(update_time,2))
            recent=[]
        if k%args.eval_every==0 or k==target:
            report=evaluate(networks,op_probe,source,median_net,closest_net,comparators,pairwise,validation,device)
            base.atomic_json(root/f'validation-step-{k:06d}.json',{'step':k,**report})
            brief={name:{'accuracy':x['numeric_5pct'],'near':x['nearest_candidate_accuracy'],
                         'mean_log_error':x['mean_log1p_relative_error'],'improved':x['improved'],
                         'worsened':x['worsened']} for name,x in report['models'].items()}
            base.emit('pairwise_validation',step=k,
                      baseline=report['reference']['source']['numeric_5pct'],
                      oracle=report['reference']['source']['oracle_numeric_5pct'],models=brief)
            for name,item in report['models'].items():
                current=(item['mean_log1p_relative_error'], -item['numeric_5pct'])
                best_item=experiment_best[name]
                if current<(best_item['log_error'],-best_item['numeric_5pct']):
                    experiment_best[name]={'step':k,'log_error':float(current[0]),
                                           'numeric_5pct':float(item['numeric_5pct'])}
                    # Snapshot entire aligned experiment for apples-to-apples candidate comparisons.
                    base.atomic_checkpoint(root/name/'best.pt',
                        pack(k,cfg,networks,opts,op_probe,op_opt,stream,replay,best,
                             median_net,median_opt,mb,rb,old_net,old_opt,old_best,old_threshold,
                             closest_net,closest_opt,closest_best,comparators,pairwise,optimizers,experiment_best))
                    base.emit('pairwise_best',model=name,**experiment_best[name])
        if k%args.checkpoint_every==0 or k==target:
            base.atomic_checkpoint(latest,pack(k,cfg,networks,opts,op_probe,op_opt,stream,replay,best,
                 median_net,median_opt,mb,rb,old_net,old_opt,old_best,old_threshold,
                 closest_net,closest_opt,closest_best,comparators,pairwise,optimizers,experiment_best))
            base.emit('pairwise_checkpoint',step=k,path=str(latest))
    base.emit('pairwise_complete',step=target,best=experiment_best,results_root=str(root))
    return 0


def self_test():
    torch.manual_seed(32)
    p=np.array([[100,108,120,200,140,90,91,98,99,104,115],
                [100,150,130,160,110,140,90,120,160,180,125]],dtype=np.float64)
    t=np.array([116.,300.])
    f=torch.randn(2,base.INPUT_WIDTH)
    ops=torch.tensor([base.OP_IDS['written_product']]*2)
    d=feature_design(f,p,ops)
    models={name:BinaryComparator(d[name].shape[-1],16) for name in ABLATIONS}
    pair=PairwiseRanker(d['combined'].shape[-1],16)
    loss,parts=objectives(models,pair,d,p,t,np.array([False,False]))
    loss.backward()
    assert np.isfinite(loss.item()) and all(torch.isfinite(q.grad).all()
        for model in list(models.values())+[pair] for q in model.parameters() if q.grad is not None)
    ids=choose_ids(pair_scores(pair(d['combined'])).detach().numpy(),[False,True])
    assert ids[1]==0 and len(PAIRS)==N*(N-1)//2
    assert parent.nearest_target(p,t).tolist()==[10,9]
    base.emit('pairwise_self_test_pass',candidate_count=N,pair_count=len(PAIRS),
              ablations=list(ABLATIONS),all_wrong_training=True,frozen_candidate_contract=True)
    return 0

if __name__=='__main__':raise SystemExit(execute(parser().parse_args()))
