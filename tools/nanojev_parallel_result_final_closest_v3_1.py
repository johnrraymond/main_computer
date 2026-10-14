#!/usr/bin/env python3
"""NanoJev FINAL v3.1: regress closeness to each RESULT and pick closest estimate.

Fork from final-v3/latest.pt. Qwen and R0-R10 are frozen by default; only the
new closest-estimator learns. Every numerical training example contributes a
supervised nearest-candidate target, including those where all candidates are
outside the 5% accuracy window. FINAL always predicts a candidate (no NONE).

Requires the existing v1, v2 and v3 scripts alongside this file. Checkpoint and
output directory are independent of v3; neither v3 nor earlier runs are changed.
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
import nanojev_parallel_result_final_v3 as old

SCHEMA = 'nanojev.parallel-result-final-closest.v3.1'
NAMES = old.NAMES
N = len(NAMES)


def numerical_errors(proposals, truth):
    """Unclipped relative errors. Same denominator per example preserves nearest.

    Return log1p(relative error) as regression target. Clipping is applied only
    inside the loss, never for oracle winner computation. Input must be finite.
    """
    a = np.asarray(proposals, dtype=np.float64)
    t = np.asarray(truth, dtype=np.float64).reshape(-1)
    if a.ndim != 2 or a.shape != (len(t), N) or not np.isfinite(a).all() or not np.isfinite(t).all():
        raise ValueError('CLOSEST_INVALID_NUMERICAL_TARGETS')
    relative = np.abs(a - t[:, None]) / np.maximum(np.abs(t[:, None]), 1.)
    return relative, np.log1p(relative)


def nearest_target(proposals, truth):
    """Deterministic argmin: exact ties go to the lowest index (R0 first)."""
    relative, _ = numerical_errors(proposals, truth)
    return relative.argmin(axis=1)


class ClosestFinal(nn.Module):
    """One estimated nonnegative log-relative-error per candidate."""
    def __init__(self, width=128):
        super().__init__()
        size = base.INPUT_WIDTH + len(base.OP_CLASSES) + 1 + median.MEDIAN_FEATURES + old.EXTRA_WIDTH
        self.net = nn.Sequential(nn.LayerNorm(size, elementwise_affine=False),
                                 nn.Linear(size, width), nn.GELU(),
                                 nn.Linear(width, width), nn.GELU(),
                                 nn.Linear(width, N))
        # All heads start equal; unlike v3, there is NO source-rank bias.
        nn.init.zeros_(self.net[-1].bias)

    def forward(self, design):
        return F.softplus(self.net(design.float()))


def closest_objective(predicted, proposals, truth, boolean,
                      minority_weight=3., regression_weight=1., rank_weight=1., temperature=.25):
    """Regression + ranking, with minority winners upweighted against collapse."""
    mask = ~np.asarray(boolean, dtype=bool)
    if not mask.any():
        return predicted.sum()*0., {'regression':0., 'ranking':0., 'minority_examples':0}
    relative, targets = numerical_errors(np.asarray(proposals)[mask], np.asarray(truth)[mask])
    winner = relative.argmin(axis=1)
    weights_np = np.where(winner == 0, 1., float(minority_weight)).astype(np.float32)
    weights = torch.as_tensor(weights_np, device=predicted.device)
    objective = torch.as_tensor(np.clip(targets, 0., 20.), dtype=torch.float32, device=predicted.device)
    pred = predicted[torch.as_tensor(mask, device=predicted.device)]
    # Regress across ALL candidates, including when every candidate is wrong.
    reg_rows = F.smooth_l1_loss(pred, objective, beta=.25, reduction='none').mean(dim=1)
    reg = (reg_rows*weights).sum()/weights.sum()
    labels = torch.as_tensor(winner, dtype=torch.long, device=predicted.device)
    # Low predicted error is high ranking logit; no privileged source class.
    ranking = F.cross_entropy(-pred / temperature, labels, reduction='none')
    ranking = (ranking*weights).sum()/weights.sum()
    loss = regression_weight*reg + rank_weight*ranking
    return loss, {'regression':float(reg.detach()), 'ranking':float(ranking.detach()),
                  'minority_examples':int((winner!=0).sum()),
                  'source_examples':int((winner==0).sum())}


def closest_predictions(predicted, boolean):
    """Always choose a candidate on numeric rows, source for Boolean rows."""
    error = predicted.detach().float().cpu().numpy()
    winner = error.argmin(axis=1)
    winner[np.asarray(boolean,dtype=bool)] = 0
    return winner, error


def select(proposals, winner):
    arr = np.asarray(proposals, dtype=np.float64)
    ids = np.asarray(winner, dtype=np.int64)
    return arr[np.arange(len(arr)), ids]


def retention_frontier(proposals, winner, estimated_errors, truth, boolean,
                       retention_targets=(1., .99, .98, .95, 0.)):
    """Research-only threshold frontier. FINAL selection remains ungated.

    Threshold on expected improvement score (R0 predicted error minus selected
    predicted error). Calibrate on validation only; never leak to training or
    report test accuracy as validation. Each point may fall back to R0.
    """
    truth = np.asarray(truth)
    boolmask = np.asarray(boolean, dtype=bool)
    eligible = ~boolmask
    src_hits = base.exact_or_relative_hits(np.asarray(proposals)[:,0], truth, boolmask)
    source_correct = int((src_hits&eligible).sum())
    delta = estimated_errors[:,0]-estimated_errors[np.arange(len(winner)),winner]
    values = np.unique(np.concatenate(([np.inf],np.maximum(delta,0.),[0.])))
    # Avoid large frontier loops; it has at most one per evaluated row.
    frontier=[]
    for retention_target in retention_targets:
        optimal=None
        for threshold in values:
            ids=np.where((winner!=0)&(delta>=threshold)&eligible,winner,0)
            proposed=select(proposals,ids)
            hits=base.exact_or_relative_hits(proposed,truth,boolmask)
            damaged=int(((~hits)&src_hits&eligible).sum())
            retained=1.-damaged/max(1,source_correct)
            if retained+1e-12<retention_target:continue
            rescued=int((hits&~src_hits&eligible).sum())
            numeric_score=float(hits[eligible].mean()) if eligible.any() else 0.
            item={'required_retention':float(retention_target),'threshold':float(threshold),
                  'retention':float(retained),'numeric_5pct':numeric_score,
                  'rescued':rescued,'damaged':damaged,'switched':int((ids!=0).sum())}
            if (optimal is None or (numeric_score, rescued, -damaged) >
                (optimal['numeric_5pct'],optimal['rescued'],-optimal['damaged'])):
                optimal=item
        frontier.append(optimal)
    return frontier


def train_closest(networks,op_probe,median_net,model,optimizer,rows,device,args):
    features=torch.stack([r[0] for r in rows]).to(device,dtype=torch.float32)
    xy=np.asarray([[r[4],r[5]] for r in rows],dtype=np.int64)
    truth,boolean=base.truth_for([{'a':r[1],'b':r[2],'operation':r[3]} for r in rows])
    proposals,design,_,_=old.infer_candidates(networks,op_probe,median_net,features,xy)
    model.train()
    pred=model(design)
    loss,parts=closest_objective(pred,proposals,truth,boolean,
              minority_weight=args.minority_weight,regression_weight=args.regression_weight,
              rank_weight=args.rank_weight,temperature=args.rank_temperature)
    optimizer.zero_grad(set_to_none=True)
    loss.backward()
    nn.utils.clip_grad_norm_(model.parameters(),1.)
    optimizer.step()
    return float(loss.detach()),parts


@torch.inference_mode()
def evaluate(networks,op_probe,source,median_net,model,eval_rows,device,batch_size=64,include_frontier=True):
    features,xy,ops,families=eval_rows
    truth=np.asarray([base.result_value(int(a),int(b),op) for (a,b),op in zip(xy,ops)],dtype=np.float64)
    boolean=np.asarray([op in base.BOOL_OPS for op in ops],dtype=bool)
    proposals=[]; estimated=[];winners=[]; op_correct=[]
    model.eval(); median_net.eval()
    for start in range(0,len(xy),batch_size):
        end=min(start+batch_size,len(xy))
        f=torch.as_tensor(features[start:end],device=device,dtype=torch.float32)
        decoded=base.source_decode(source,f)
        candidate,design,inferred_bool,pred_ops=old.infer_candidates(networks,op_probe,median_net,f,decoded)
        winner,error=closest_predictions(model(design),inferred_bool)
        proposals.append(candidate);winners.append(winner);estimated.append(error)
        op_correct.extend(base.OP_CLASSES[int(i)]==t for i,t in zip(pred_ops.cpu().numpy(),ops[start:end]))
    cand=np.concatenate(proposals); win=np.concatenate(winners); pred_error=np.concatenate(estimated)
    chosen=select(cand,win)
    true_relative, true_logged=numerical_errors(cand,truth)
    true_winner=true_relative.argmin(axis=1)
    numeric=~boolean
    hits=np.column_stack([base.exact_or_relative_hits(cand[:,i],truth,boolean) for i in range(N)])
    src=hits[:,0]
    final_hits=base.exact_or_relative_hits(chosen,truth,boolean)
    closest_oracle=select(cand,np.where(numeric,true_winner,0))
    closeness=np.abs(chosen-truth)/np.maximum(np.abs(truth),1.)
    source_error=true_relative[:,0]
    oracle_error=np.min(true_relative,axis=1)
    def mean(arr):return float(np.mean(arr[numeric])) if numeric.any() else None
    def median_val(arr):return float(np.median(arr[numeric])) if numeric.any() else None
    by_name={name:int((win[numeric]==i).sum()) for i,name in enumerate(NAMES)}
    closest_report={
        'numeric_rows':int(numeric.sum()),'nearest_candidate_accuracy':mean((win==true_winner).astype(float)),
        'nearest_candidate_accuracy_all_wrong':float(np.mean((win==true_winner)[numeric&(~hits.any(axis=1))])) if (numeric&(~hits.any(axis=1))).any() else None,
        'all_candidates_wrong':int((numeric&(~hits.any(axis=1))).sum()),
        'minority_nearest_opportunities':int((numeric&(true_winner!=0)).sum()),
        'actual_minority_nearest_selected':int((numeric&(true_winner!=0)&(win==true_winner)).sum()),
        'numeric_mean_relative_error':mean(closeness),'source_mean_relative_error':mean(source_error),
        'improved_over_source_count':int((numeric&(closeness<source_error)).sum()),
        'worsened_vs_source_count':int((numeric&(closeness>source_error)).sum()),
        'tied_with_source_count':int((numeric&(closeness==source_error)).sum()),
        'oracle_mean_relative_error':mean(oracle_error),
        'numeric_median_relative_error':median_val(closeness),
        'source_median_relative_error':median_val(source_error),
        'numeric_mean_log1p_relative_error':mean(np.log1p(closeness)),
        'source_mean_log1p_relative_error':mean(np.log1p(source_error)),
        'numeric_error_regression_mae':mean(np.mean(np.abs(pred_error-true_logged),axis=1)),
        'numeric_best_available_5pct':mean(hits.any(axis=1).astype(float)),
        'nearest_oracle_5pct':base.diagnostic(closest_oracle,truth,boolean)['numeric_5pct'],
        'selection_by_name_numeric':by_name,
    }
    report={
        'rows':len(truth),'operation_accuracy':float(np.mean(op_correct)),
        'source':base.diagnostic(cand[:,0],truth,boolean),
        'final':base.diagnostic(chosen,truth,boolean),
        'oracle_numeric_5pct':closest_report['numeric_best_available_5pct'],
        'rescued_source_errors':int((numeric&final_hits&(~src)).sum()),
        'damaged_source_successes':int((numeric&src&(~final_hits)).sum()),
        'source_retention':float(1.-(numeric&src&(~final_hits)).sum()/max(1,(numeric&src).sum())),
        'nearest':closest_report,
        'retention_frontier_validation_only':(retention_frontier(cand,win,pred_error,truth,boolean)
                                              if include_frontier else None),
        'examples':[{'xy':[int(x) for x in xy[i]],'operation':ops[i],
                     'truth':float(truth[i]),'final_candidate':NAMES[int(win[i])],
                     'oracle_nearest':NAMES[int(true_winner[i])],
                     'final_result':float(chosen[i]),'relative_error':float(closeness[i]),
                     'all_candidates_wrong_5pct':bool(~hits[i].any()),
                     'predicted_log_error':{name:float(pred_error[i,j]) for j,name in enumerate(NAMES)},
                     'candidate_result':{name:float(cand[i,j]) for j,name in enumerate(NAMES)}}
                    for i in range(min(12,len(truth)))]
    }
    return report,np.column_stack((hits,final_hits))


def parser():
    p=old.parser();p.description=__doc__
    p.set_defaults(results_root=Path.home()/'NanoJev'/'runs'/'parallel_result_final_closest_v3_1')
    p.add_argument('--fork-v3-root',type=Path,default=None)
    p.add_argument('--closest-width',type=int,default=128)
    p.add_argument('--minority-weight',type=float,default=3.)
    p.add_argument('--regression-weight',type=float,default=1.)
    p.add_argument('--rank-weight',type=float,default=1.)
    p.add_argument('--rank-temperature',type=float,default=.25)
    return p


def cfg_for(args,manifest,sha,identity):
    return {'schema':SCHEMA,'v3_config':old.cfg_for(args,manifest,sha,identity),
            'closest_width':args.closest_width,'minority_weight':args.minority_weight,
            'regression_weight':args.regression_weight,'rank_weight':args.rank_weight,
            'rank_temperature':args.rank_temperature,
            'target':'nearest_relative_error_all_numeric_including_all_wrong',
            'selection':'unconditional_argmin_predicted_log1p_relative_error',
            'boolean_policy':'source_passthrough','candidate_names':list(NAMES),
            'train_candidates':bool(args.train_candidates)}


def pack(step,cfg,networks,opts,op_probe,op_opt,stream,replay,best,median_net,median_opt,
         mb,rb,old_net,old_opt,old_best,old_threshold,model,optimizer,closest_best):
    ck=old.pack_state(step,cfg['v3_config'],networks,opts,op_probe,op_opt,
         stream,replay,best,median_net,median_opt,mb,rb,old_net,old_opt,old_best,old_threshold)
    ck.update({'schema':SCHEMA,'config':cfg,'closest':model.state_dict(),
               'closest_optimizer':optimizer.state_dict(),'closest_best':closest_best})
    return ck


def load_payload(ck,cfg,networks,opts,op_probe,op_opt,stream,replay,median_net,median_opt,
                 old_net,old_opt,model=None,optimizer=None):
    if ck.get('schema') not in (old.SCHEMA,SCHEMA):
        raise RuntimeError('CLOSEST_UNSUPPORTED_CHECKPOINT_SCHEMA')
    if ck['config'] != (cfg['v3_config'] if ck['schema']==old.SCHEMA else cfg):
        raise RuntimeError('CLOSEST_CONFIG_OR_SOURCE_DRIFT')
    old_payload={**ck,'schema':old.SCHEMA,'config':cfg['v3_config']}
    step,best,mb,rb=old._load_v2_payload({**old_payload,'schema':median.SCHEMA,
                                         'config':cfg['v3_config']['v2_config']},
       cfg['v3_config']['v2_config'],networks,opts,op_probe,op_opt,stream,replay,median_net,median_opt)
    old_net.load_state_dict(ck['final']);old_opt.load_state_dict(ck['final_optimizer'])
    old_best=dict(ck['final_best']);old_threshold=float(ck['final_threshold'])
    if ck['schema']==SCHEMA:
        model.load_state_dict(ck['closest']);optimizer.load_state_dict(ck['closest_optimizer'])
        closest_best=dict(ck['closest_best'])
    else:
        closest_best={'step':0,'numeric_5pct':-1.,'nearest_accuracy':-1.,'mean_log_error':float('inf')}
    return step,best,mb,rb,old_best,old_threshold,closest_best


def execute(args):
    if args.self_test:return self_test()
    if args.fork_v1_root is not None or args.fork_v2_root is not None:
        raise ValueError('CLOSEST_FORK_ONLY_FROM_V3')
    if args.fork_v3_root is not None and (args.resume or args.eval_only):
        raise ValueError('CLOSEST_FORK_INCOMPATIBLE_WITH_RESUME_OR_EVAL_ONLY')
    if (args.closest_width<8 or args.minority_weight<=0 or args.regression_weight<0 or
        args.rank_weight<=0 or args.rank_temperature<=0):
        raise ValueError('CLOSEST_INVALID_HEAD_CONFIG')
    if args.additional_steps is not None and args.additional_steps<1:
        raise ValueError('CLOSEST_ADDITIONAL_STEPS_MUST_BE_POSITIVE')
    if args.threads>0:torch.set_num_threads(args.threads)
    if args.device=='cuda' and not torch.cuda.is_available():
        raise RuntimeError('CLOSEST_CUDA_UNAVAILABLE')
    device=torch.device(args.device)
    benchmark=args.v3_root.expanduser().resolve()
    manifest,tasks=base.previous.read_benchmark(benchmark)
    identity=json.loads((benchmark/'model_identity.json').read_text(encoding='utf-8'))
    source,sha=base.source_factory(args.source_checkpoint,identity,device)
    cfg=cfg_for(args,manifest,sha,identity)
    root=args.results_root.expanduser().resolve()
    fork=args.fork_v3_root.expanduser().resolve() if args.fork_v3_root is not None else None
    unsafe=(root==benchmark or benchmark in root.parents or
            root==args.source_checkpoint.expanduser().resolve().parent or
            (fork is not None and (root==fork or fork in root.parents or root in fork.parents)))
    if unsafe:raise RuntimeError('CLOSEST_UNSAFE_RESULTS_ROOT')
    latest=root/'latest.pt'
    if args.resume or args.eval_only:
        if not latest.is_file():raise RuntimeError('CLOSEST_RESUME_REQUIRES_LATEST')
        source_file=latest
        peek=torch.load(source_file,map_location='cpu',weights_only=True)
        if peek.get('schema')!=SCHEMA:raise RuntimeError('CLOSEST_RESUME_WRONG_SCHEMA')
    else:
        if fork is None:raise RuntimeError('CLOSEST_REQUIRES_--fork-v3-root_OR_--resume')
        source_file=fork/'latest.pt'
        if not source_file.is_file():raise RuntimeError('CLOSEST_FORK_REQUIRES_V3_LATEST')
        if latest.exists():raise RuntimeError('CLOSEST_ALREADY_EXISTS_USE_--resume')
        peek=torch.load(source_file,map_location='cpu',weights_only=True)
        if peek.get('schema')!=old.SCHEMA:raise RuntimeError('CLOSEST_FORK_REQUIRES_V3_SCHEMA')
    inherited=int(peek['step'])
    target=(inherited+args.additional_steps) if args.additional_steps is not None else args.steps
    if not args.eval_only and target<=inherited:
        raise RuntimeError(f'CLOSEST_TARGET_NOT_ABOVE_CHECKPOINT:{inherited}; USE_--additional-steps 500')
    if peek.get('config')!=(cfg['v3_config'] if fork else cfg):
        raise RuntimeError('CLOSEST_INHERITED_CONFIG_DRIFT')
    # Complete all preflight checks before touching output directory.
    if not (args.resume or args.eval_only) and root.exists() and any(root.iterdir()):
        raise RuntimeError('CLOSEST_NONEMPTY_RESULTS_ROOT')
    root.mkdir(parents=True,exist_ok=True)
    cfg_file=root/'run_config.json'
    if cfg_file.is_file():
        if json.loads(cfg_file.read_text(encoding='utf-8'))!=cfg:raise RuntimeError('CLOSEST_EXISTING_CONFIG_DRIFT')
    else:
        if any(root.iterdir()):raise RuntimeError('CLOSEST_NONEMPTY_RESULTS_ROOT')
        base.atomic_json(cfg_file,cfg)
    torch.manual_seed(args.seed)
    networks={n:base.ResultHead(n,args.width).to(device) for n in base.NAMES}
    opts={n:torch.optim.AdamW(model.parameters(),lr=args.lr,weight_decay=args.weight_decay) for n,model in networks.items()}
    op_probe=base.SharedOperationProbe().to(device)
    op_opt=torch.optim.AdamW(op_probe.parameters(),lr=args.lr,weight_decay=args.weight_decay)
    median_net=median.MedianResidual(args.median_width).to(device)
    median_opt=torch.optim.AdamW(median_net.parameters(),lr=args.lr,weight_decay=args.weight_decay)
    old_net=old.FinalHead(args.final_width).to(device)
    old_opt=torch.optim.AdamW(old_net.parameters(),lr=args.lr,weight_decay=args.weight_decay)
    model=ClosestFinal(args.closest_width).to(device)
    optimizer=torch.optim.AdamW(model.parameters(),lr=args.lr,weight_decay=args.weight_decay)
    stream=base.previous.FreshPairs(args.seed,base.previous.reserved_pairs(tasks))
    replay=base.Replay(args.replay_capacity,args.seed+71)
    payload=load_payload(peek,cfg,networks,opts,op_probe,op_opt,stream,replay,median_net,median_opt,old_net,old_opt,model,optimizer)
    step,best,mb,rb,old_best,old_threshold,closest_best=payload
    if fork:
        base.atomic_checkpoint(latest,pack(step,cfg,networks,opts,op_probe,op_opt,stream,replay,best,
               median_net,median_opt,mb,rb,old_net,old_opt,old_best,old_threshold,model,optimizer,closest_best))
        base.emit('closest_forked_v3',step=step,v3_checkpoint=str(source_file),
                  v3_sha256=hashlib.sha256(source_file.read_bytes()).hexdigest())
    else:
        base.emit('closest_resumed',step=step,best=closest_best)
    validation=base.previous.load_eval(benchmark,manifest,tasks,'val',args.val_examples)
    if args.eval_only:
        for split in ('val','test','control'):
            rows=base.previous.load_eval(benchmark,manifest,tasks,split)
            report,_=evaluate(networks,op_probe,source,median_net,model,rows,device,
                              include_frontier=(split=='val'))
            # Calibrated threshold sweeps on validation only; test/control remain untouched.
            base.emit('closest_evaluation',split=split,step=step,**report)
        return 0
    import nanojev_numerate_spine_cassette_smoke as cassette
    _,frozen,tokenizer,model_device,model_info=cassette.load_source('qwen',args)
    if (model_info.get('resolved_commit')!=identity['model_info'].get('resolved_commit') or
        model_info.get('source')!=identity['model_info'].get('source') or
        str(model_info.get('dtype'))!=str(identity['model_info'].get('dtype'))):
        raise RuntimeError('CLOSEST_QWEN_IDENTITY_DRIFT')
    frozen.eval()
    base.previous.verify_capture_alignment(torch,frozen,tokenizer,model_device,benchmark,tasks,args.max_prompt_tokens)
    base.emit('closest_initialized',step=step,target_step=target,
              frozen_qwen_trainable_parameters=0,closest_parameters=sum(p.numel() for p in model.parameters()),
              candidate_heads_trainable=bool(args.train_candidates),candidates=list(NAMES))
    start=time.monotonic();capture_time=update_time=0.;recent=[]
    for k in range(step+1,target+1):
        t0=time.monotonic()
        rows=stream.batch(args.batch_size)
        f=base.previous.extract_batched(torch,frozen,tokenizer,[r['prompt'] for r in rows],model_device,args.max_prompt_tokens)
        if f.requires_grad:raise RuntimeError('CLOSEST_BACKBONE_GRADIENT_DETECTED')
        decoded=base.source_decode(source,f.to(device));replay.add(f,rows,decoded)
        capture_time+=time.monotonic()-t0
        t0=time.monotonic()
        for _ in range(args.replay_updates):
            sampled=replay.sample(args.replay_batch)
            if args.train_candidates:
                base.train_step(networks,opts,op_probe,op_opt,sampled,device)
                median.train_median(networks,op_probe,median_net,median_opt,sampled,device)
            loss,parts=train_closest(networks,op_probe,median_net,model,optimizer,sampled,device,args)
            recent.append(loss)
        update_time+=time.monotonic()-t0
        if k%args.report_every==0 or k==target:
            base.emit('closest_train_progress',step=k,mean_loss=round(float(np.mean(recent)),6),
                      last_regression_loss=parts['regression'],last_ranking_loss=parts['ranking'],
                      minority_examples_in_last_batch=parts['minority_examples'],
                      examples_seen=len(stream.seen),replay_records=len(replay.rows),
                      elapsed_seconds=round(time.monotonic()-start,2),
                      shared_capture_seconds=round(capture_time,2),head_update_seconds=round(update_time,2))
            recent=[]
        if k%args.eval_every==0 or k==target:
            report,matrix=evaluate(networks,op_probe,source,median_net,model,validation,device)
            base.atomic_json(root/f'validation-step-{k:06d}.json',{'step':k,**report})
            np.save(root/f'correctness-step-{k:06d}.npy',matrix)
            nearest=report['nearest']
            base.emit('closest_validation',step=k,source_numeric_5pct=report['source']['numeric_5pct'],
                      final_numeric_5pct=report['final']['numeric_5pct'],
                      nearest_candidate_accuracy=nearest['nearest_candidate_accuracy'],
                      nearest_candidate_accuracy_all_wrong=nearest['nearest_candidate_accuracy_all_wrong'],
                      minority_nearest_selected=nearest['actual_minority_nearest_selected'],
                      minority_nearest_opportunities=nearest['minority_nearest_opportunities'],
                      selected_count=nearest['selection_by_name_numeric'],
                      rescued=report['rescued_source_errors'],damaged=report['damaged_source_successes'],
                      mean_log1p_relative_error=nearest['numeric_mean_log1p_relative_error'],
                      oracle_numeric_5pct=report['oracle_numeric_5pct'])
            accuracy=report['final']['numeric_5pct']
            near_score=nearest['nearest_candidate_accuracy'] or 0.
            error=nearest['numeric_mean_log1p_relative_error']
            if error is None: error=float('inf')
            # Optimize the user's nearest-candidate objective before 5% reporting.
            candidate_key=(near_score,-error,accuracy)
            old_key=(closest_best['nearest_accuracy'],-closest_best['mean_log_error'],closest_best['numeric_5pct'])
            if candidate_key>old_key:
                closest_best={'step':k,'numeric_5pct':accuracy,'nearest_accuracy':near_score,'mean_log_error':error}
                base.atomic_checkpoint(root/'closest'/'best.pt',
                    {'schema':SCHEMA,'config':cfg,'step':k,'closest':model.state_dict(),
                     'models':{n:m.state_dict() for n,m in networks.items()},
                     'median':median_net.state_dict(),'operation_probe':op_probe.state_dict(),
                     'score':closest_best})
                base.emit('closest_best',**closest_best)
        if k%args.checkpoint_every==0 or k==target:
            base.atomic_checkpoint(latest,pack(k,cfg,networks,opts,op_probe,op_opt,stream,replay,best,
                   median_net,median_opt,mb,rb,old_net,old_opt,old_best,old_threshold,model,optimizer,closest_best))
            base.emit('closest_checkpoint',step=k,path=str(latest))
    base.emit('closest_complete',step=target,best=closest_best,results_root=str(root))
    return 0


def self_test():
    torch.manual_seed(31)
    values=np.asarray([[100.,103.,300.,700.,900.,1000.,1200.,1300.,100.,1300.,1350.],
                       [500.,300.,600.,200.,100.,1000.,99.,1002.,620.,200.,700.]],dtype=np.float64)
    truths=np.asarray([250.,800.]) # all candidates outside 5% for row 1
    truth_best=nearest_target(values,truths)
    assert truth_best.tolist()==[2,10], truth_best
    f=torch.randn(2,base.INPUT_WIDTH)
    ops=torch.tensor([base.OP_IDS['written_product']]*2)
    design=old.final_design(f,ops,torch.as_tensor(values[:,0],dtype=torch.float32),
                            values,np.median(values[:,:8],axis=1))
    net=ClosestFinal(16)
    output=net(design)
    loss,parts=closest_objective(output,values,truths,np.asarray([False,False]))
    loss.backward()
    assert np.isfinite(float(loss.detach()))
    assert all(p.grad is not None and torch.isfinite(p.grad).all() for p in net.parameters())
    winner,errors=closest_predictions(output,np.asarray([False,True]))
    assert winner[1]==0 and np.isfinite(errors).all()
    base.emit('closest_self_test_pass',candidates=N,all_wrong_target_supported=True,
              no_none_class=True,unbiased_initial_rank=True,boolean_source_passthrough=True)
    return 0


if __name__=='__main__':raise SystemExit(execute(parser().parse_args()))
