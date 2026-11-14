#!/usr/bin/env python3
"""Parallel RESULT lab v2: median controls and a learned median residual.

Depends on nanojev_parallel_result_register_search.py (v1); does not modify it.
Fork v1/latest.pt with --fork-v1-root into a NEW v2 results directory,
thereby preserving all seven existing weights and their optimizer states.
The frozen Qwen capture remains once per newly generated training minibatch.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import time

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

import nanojev_parallel_result_register_search as base

SCHEMA = 'nanojev.parallel-result-median-search.v2'
HEAD = 'median_regressed'
RAW = 'median_raw'
SIGNED = 'median_signed'
CANDIDATES = ('source',) + base.NAMES
ALL_NAMES = CANDIDATES + (RAW, SIGNED, HEAD)
MEDIAN_FEATURES = 19  # 8 candidate values + 8 centered + median + MAD + source delta


def median_controls(values: np.ndarray, bool_mask: np.ndarray):
    """Robust, entirely unsupervised medians of R0..R7, in float64.

    R0 is the Boolean answer for Boolean operation rows: no averaging Boolean
    probabilities or changing the established Boolean reference baseline.
    """
    a = np.asarray(values, dtype=np.float64)
    if a.ndim != 2 or a.shape[1] != len(CANDIDATES):
        raise ValueError('MEDIAN_REQUIRES_ALL_EIGHT_RESULTS')
    if not np.isfinite(a).all():
        raise ValueError('MEDIAN_NONFINITE_CANDIDATE')
    b = np.asarray(bool_mask, dtype=bool)
    if b.shape != (len(a),): raise ValueError('MEDIAN_BOOLEAN_MASK_SHAPE')
    direct = np.median(a, axis=1)
    transformed = np.median(base.signed_encode(a), axis=1)
    signed = np.sinh(transformed * base.SCALE)
    direct = np.where(b, a[:,0], direct)
    signed = np.where(b, a[:,0], signed)
    return direct, signed


def median_design(features: torch.Tensor, predicted_ops: torch.Tensor,
                  reference: torch.Tensor, candidates: np.ndarray,
                  direct_median: np.ndarray):
    """Same frozen features used by other heads plus detached RESULT vector."""
    design = base.features_with_op(features,predicted_ops,reference)
    c = base.signed_encode(candidates).astype(np.float32)
    med = base.signed_encode(direct_median).astype(np.float32)
    dispersion = np.median(np.abs(c - med[:,None]),axis=1).astype(np.float32)
    source_delta = (c[:,0] - med).astype(np.float32)
    extras = np.concatenate((c, c-med[:,None], med[:,None],
                             dispersion[:,None], source_delta[:,None]),axis=1)
    if extras.shape[1] != MEDIAN_FEATURES: raise AssertionError('MEDIAN_INPUT_DIMENSION')
    extra_tensor = torch.as_tensor(extras,device=features.device,dtype=torch.float32)
    return torch.cat((design,extra_tensor),dim=1)


class MedianResidual(nn.Module):
    """Trains in signed-asinh RESULT space; zero-init is exactly the raw median."""
    def __init__(self,width=128):
        super().__init__()
        size = base.INPUT_WIDTH+len(base.OP_CLASSES)+1+MEDIAN_FEATURES
        self.net=nn.Sequential(nn.LayerNorm(size,elementwise_affine=False),
                 nn.Linear(size,width),nn.GELU(),nn.Linear(width,width),nn.GELU(),
                 nn.Linear(width,1))
        nn.init.zeros_(self.net[-1].weight)
        nn.init.zeros_(self.net[-1].bias)

    def forward(self,x): return self.net(x.float()).flatten()

    def decoded(self,delta,direct_median,bool_mask,source):
        b=np.asarray(bool_mask,dtype=bool)
        z=base.signed_encode(direct_median) + np.asarray(delta,dtype=np.float64)
        out=base.signed_decode(z)
        # Numeric average results (e.g. written_average) may have .5 targets.
        # The pre-existing readout also rounds numerical results to integer.
        return np.where(b,np.asarray(source,dtype=np.float64),out)

    def objective(self,delta,direct_median,true_values,bool_mask):
        n=~np.asarray(bool_mask,dtype=bool)
        if not n.any():return delta.sum()*0.
        med=torch.as_tensor(base.signed_encode(np.asarray(direct_median)[n]),
                            device=delta.device,dtype=torch.float32)
        y=torch.as_tensor(base.signed_encode(np.asarray(true_values)[n]),
                          device=delta.device,dtype=torch.float32)
        return F.smooth_l1_loss(med+delta[torch.as_tensor(n,device=delta.device)],y,beta=.03)


@torch.no_grad()
def candidates_for_rows(networks,op_probe,features,source_xy):
    """One detached forward of each head; uses inferred operation, never oracle."""
    ops=op_probe(features).argmax(1)
    op_names=[base.OP_CLASSES[int(j)] for j in ops.detach().cpu().numpy()]
    inferred_bool=np.asarray([name in base.BOOL_OPS for name in op_names],dtype=bool)
    source_results=base.reference_results(source_xy,op_names)
    ref=torch.as_tensor(source_results,device=features.device,dtype=torch.float32)
    design=base.features_with_op(features,ops,ref)
    proposals=[source_results]
    for name in base.NAMES:
        networks[name].eval()
        proposals.append(networks[name].infer(networks[name](design),ref,inferred_bool))
    values=np.stack(proposals,axis=1)
    return ops,ref,values,inferred_bool


def train_median(networks,op_probe,median_net,median_opt,rows,device):
    feats=torch.stack([r[0] for r in rows]).to(device,dtype=torch.float32)
    frozen_xy=np.asarray([[r[4],r[5]] for r in rows],dtype=np.int64)
    truth,bool_mask=base.truth_for([{'a':r[1],'b':r[2],'operation':r[3]} for r in rows])
    ops,ref,candidates,inferred_bool=candidates_for_rows(networks,op_probe,feats,frozen_xy)
    raw,_=median_controls(candidates,inferred_bool)
    design=median_design(feats,ops,ref,candidates,raw)
    median_net.train()
    delta=median_net(design)
    loss=median_net.objective(delta,raw,truth,bool_mask)
    median_opt.zero_grad(set_to_none=True)
    loss.backward()
    nn.utils.clip_grad_norm_(median_net.parameters(),1.)
    median_opt.step()
    return float(loss.detach())


@torch.inference_mode()
def evaluate(networks,op_probe,source,median_net,eval_rows,device,batch_size=64):
    features,xy,ops,families=eval_rows
    truth=np.asarray([base.result_value(int(a),int(b),op) for (a,b),op in zip(xy,ops)],dtype=np.float64)
    bool_mask=np.asarray([op in base.BOOL_OPS for op in ops],dtype=bool)
    predictions={name:[] for name in ALL_NAMES}
    op_hits=[];source_xy_hits=[]
    mad=[]
    median_net.eval()
    for start in range(0,len(features),batch_size):
        sl=slice(start,start+batch_size)
        z=torch.as_tensor(features[sl],device=device,dtype=torch.float32)
        frozen_xy=base.source_decode(source,z)
        source_xy_hits.extend(base.previous.correctness(frozen_xy,xy[sl]).all(axis=1).tolist())
        pred_ops,ref,candidates,inferred_bool=candidates_for_rows(networks,op_probe,z,frozen_xy)
        pred_names=[base.OP_CLASSES[int(j)] for j in pred_ops.cpu().numpy()]
        op_hits.extend(p==t for p,t in zip(pred_names,ops[sl]))
        raw,signed=median_controls(candidates,inferred_bool)
        design=median_design(z,pred_ops,ref,candidates,raw)
        delta=median_net(design).cpu().numpy()
        regressed=median_net.decoded(delta,raw,inferred_bool,candidates[:,0])
        for idx,name in enumerate(CANDIDATES):predictions[name].extend(candidates[:,idx].tolist())
        for name,values in ((RAW,raw),(SIGNED,signed),(HEAD,regressed)):
            predictions[name].extend(values.tolist())
        enc=base.signed_encode(candidates)
        mad.extend(np.median(np.abs(enc-np.median(enc,axis=1,keepdims=True)),axis=1))
    pred={k:np.asarray(v,dtype=np.float64) for k,v in predictions.items()}
    hits={k:base.exact_or_relative_hits(v,truth,bool_mask) for k,v in pred.items()}
    reports={k:base.diagnostic(v,truth,bool_mask) for k,v in pred.items()}
    correctness=np.stack([hits[k] for k in ALL_NAMES],axis=1)
    n=~bool_mask
    source_wrong=~hits['source']
    rescued={k:int((hits[k]&source_wrong).sum()) for k in ALL_NAMES if k!='source'}
    damaged={k:int((~hits[k]&hits['source']).sum()) for k in ALL_NAMES if k!='source'}
    overlap={k:{other:int((hits[k]&hits[other]).sum()) for other in ALL_NAMES} for k in ALL_NAMES}
    by_family={}
    for family in sorted(set(families)):
        idx=np.asarray([f==family for f in families])
        by_family[family]={k:float(hits[k][idx].mean()) for k in ALL_NAMES}
    examples=[]
    for i in range(min(12,len(xy))):
        examples.append({'xy':[int(v) for v in xy[i]],'operation':ops[i],
                         'true_result':float(truth[i]),
                         'candidates':{k:float(pred[k][i]) for k in ALL_NAMES}})
    median_report={'raw_vs_source_numeric_5pct':reports[RAW]['numeric_5pct']-reports['source']['numeric_5pct'] if n.any() else None,
                   'median_regression_vs_raw_numeric_5pct':reports[HEAD]['numeric_5pct']-reports[RAW]['numeric_5pct'] if n.any() else None,
                   'signed_vs_raw_numeric_5pct':reports[SIGNED]['numeric_5pct']-reports[RAW]['numeric_5pct'] if n.any() else None,
                   'candidate_asinh_mad_mean':float(np.mean(np.asarray(mad)[n])) if n.any() else None,
                   'candidate_asinh_mad_p90':float(np.quantile(np.asarray(mad)[n],.9)) if n.any() else None}
    # Direct stability comparison: errors on the SAME numeric examples.
    for name in ('source',RAW,SIGNED,HEAD):
        if n.any():
            delta=np.abs(base.signed_encode(pred[name][n])-base.signed_encode(truth[n]))
            median_report[f'{name}_asinh_error_p50']=float(np.quantile(delta,.5))
            median_report[f'{name}_asinh_error_p90']=float(np.quantile(delta,.9))
            median_report[f'{name}_asinh_error_p99']=float(np.quantile(delta,.99))
    median_report['raw_vs_source_relative_5pct_agreement']=float(base.exact_or_relative_hits(
        pred[RAW][n],pred['source'][n],np.zeros(int(n.sum()),dtype=bool)).mean()) if n.any() else None
    return {'rows':len(xy),'operation_accuracy':float(np.mean(op_hits)),
            'source_xy_joint_5pct':float(np.mean(source_xy_hits)),
            'registers':reports,'oracle_any_5pct':float(correctness.any(axis=1).mean()),
            'oracle_numeric_5pct':float(correctness[n].any(axis=1).mean()) if n.any() else None,
            'rescued_source_errors':rescued,'damaged_source_successes':damaged,
            'overlap_correct_counts':overlap,'by_family':by_family,
            'median_stability':median_report,'examples':examples},correctness


def parser():
    p=base.parser()
    p.description=__doc__
    p.set_defaults(results_root=Path.home()/'NanoJev'/'runs'/'parallel_result_median_search_v2')
    p.add_argument('--fork-v1-root',type=Path,default=None,
                   help='Read v1/latest.pt exactly once, into new v2 output root')
    p.add_argument('--median-width',type=int,default=128)
    p.add_argument('--additional-steps',type=int,default=None,
                   help='Run this many additional steps after the fork/resume checkpoint, instead of interpreting --steps as an absolute total')
    return p


def config_for(args,manifest,source_hash,identity):
    original=base.config_for(args,manifest,source_hash,identity)
    return {'schema':SCHEMA,'v1_config':original,'median_width':args.median_width,
            'median_targets':'numeric_asinh_smooth_l1',
            'median_members':list(CANDIDATES),'boolean_policy':'source_passthrough'}


def pack_state(step,cfg,networks,opts,op_probe,op_opt,stream,replay,best,
               median_net,median_opt,median_best,median_raw_best):
    ck=base.pack_state(step,cfg['v1_config'],networks,opts,op_probe,op_opt,stream,replay,best)
    ck.update({'schema':SCHEMA,'config':cfg,'median':median_net.state_dict(),
               'median_optimizer':median_opt.state_dict(),
               'median_best':median_best,'median_raw_best':median_raw_best})
    return ck


def _load_base_payload(ck,cfg,networks,opts,op_probe,op_opt,stream,replay):
    # No tensors are converted to JSON or lossy representations in this path.
    if ck.get('config')!=cfg:raise RuntimeError('MEDIAN_BASE_CONFIG_OR_SOURCE_DRIFT')
    for name in base.NAMES:
        networks[name].load_state_dict(ck['models'][name]);opts[name].load_state_dict(ck['optimizers'][name])
    op_probe.load_state_dict(ck['operation_probe']);op_opt.load_state_dict(ck['operation_optimizer'])
    stream.load_state_dict(ck['stream']);replay.load_state_dict(ck['replay'])
    torch.set_rng_state(ck['torch_rng'])
    if torch.cuda.is_available() and 'cuda_rng' in ck:torch.cuda.set_rng_state_all(ck['cuda_rng'])
    return int(ck['step']),dict(ck['best'])


def restore_v2(path,cfg,networks,opts,op_probe,op_opt,stream,replay,median_net,median_opt):
    ck=torch.load(path,map_location='cpu',weights_only=True)
    if ck.get('schema')!=SCHEMA or ck.get('config')!=cfg:
        raise RuntimeError('MEDIAN_RESUME_CONFIG_OR_SOURCE_DRIFT')
    old={**ck,'config':cfg['v1_config']}
    step,best=_load_base_payload(old,cfg['v1_config'],networks,opts,op_probe,op_opt,stream,replay)
    median_net.load_state_dict(ck['median']);median_opt.load_state_dict(ck['median_optimizer'])
    return step,best,dict(ck['median_best']),dict(ck['median_raw_best'])


def fork_v1(path,cfg,networks,opts,op_probe,op_opt,stream,replay):
    ck=torch.load(path,map_location='cpu',weights_only=True)
    if ck.get('schema')!=base.SCHEMA or ck.get('config')!=cfg['v1_config']:
        raise RuntimeError('MEDIAN_V1_FORK_CONFIG_OR_SOURCE_DRIFT')
    return _load_base_payload(ck,cfg['v1_config'],networks,opts,op_probe,op_opt,stream,replay)


def preflight_checkpoint_target(args, root, cfg, original_root):
    """Reject impossible targets before producing any v2 checkpoint or config."""
    if args.additional_steps is not None and args.additional_steps<1:
        raise ValueError('MEDIAN_ADDITIONAL_STEPS_MUST_BE_POSITIVE')
    latest=root/'latest.pt'
    if args.resume or args.eval_only:
        if not latest.is_file():
            raise RuntimeError('MEDIAN_RESUME_REQUIRES_LATEST')
        ck=torch.load(latest,map_location='cpu',weights_only=True)
        if ck.get('schema')!=SCHEMA or ck.get('config')!=cfg:
            raise RuntimeError('MEDIAN_RESUME_CONFIG_OR_SOURCE_DRIFT')
        step=int(ck['step'])
    elif original_root is not None:
        source_file=original_root/'latest.pt'
        if not source_file.is_file():
            raise RuntimeError('MEDIAN_FORK_REQUIRES_V1_LATEST')
        if latest.exists():
            raise RuntimeError('MEDIAN_V2_RUN_EXISTS_REMOVE_FORK_AND_USE_--resume')
        ck=torch.load(source_file,map_location='cpu',weights_only=True)
        if ck.get('schema')!=base.SCHEMA or ck.get('config')!=cfg['v1_config']:
            raise RuntimeError('MEDIAN_V1_FORK_CONFIG_OR_SOURCE_DRIFT')
        step=int(ck['step'])
    else:
        if latest.exists():
            raise RuntimeError('MEDIAN_V2_RUN_EXISTS_USE_--resume')
        step=0
    if args.additional_steps is not None:
        args.steps=step+args.additional_steps
    if not args.eval_only and args.steps<=step:
        raise RuntimeError(f'MEDIAN_TARGET_STEPS_NOT_ABOVE_CHECKPOINT:{step}; '
                           f'USE_--additional-steps 500 OR_--steps {step+500}')
    return step


def execute(args):
    if args.self_test:return self_test()
    if args.fork_v1_root is not None and (args.resume or args.eval_only):
        raise ValueError('MEDIAN_FORK_INCOMPATIBLE_WITH_RESUME_OR_EVAL_ONLY')
    if args.median_width<8:raise ValueError('MEDIAN_WIDTH_TOO_SMALL')
    if args.steps<1 or args.batch_size<1 or args.replay_batch<1 or args.replay_capacity<1 or args.replay_updates<1 or args.width<8:
        raise ValueError('MEDIAN_INVALID_TRAINING_ARGUMENTS')
    if any(q<1 for q in (args.eval_every,args.report_every,args.checkpoint_every,args.val_examples)):
        raise ValueError('MEDIAN_INVALID_REPORT_FREQUENCY')
    if args.threads>0:torch.set_num_threads(args.threads)
    if args.device=='cuda' and not torch.cuda.is_available():raise RuntimeError('MEDIAN_CUDA_UNAVAILABLE')
    dev=torch.device(args.device)
    source_root=args.v3_root.expanduser().resolve()
    manifest,tasks=base.previous.read_benchmark(source_root)
    identity=json.loads((source_root/'model_identity.json').read_text(encoding='utf-8'))
    source,source_sha=base.source_factory(args.source_checkpoint,identity,dev)
    cfg=config_for(args,manifest,source_sha,identity)
    root=args.results_root.expanduser().resolve()
    original_root=args.fork_v1_root.expanduser().resolve() if args.fork_v1_root is not None else None
    if root==source_root or source_root in root.parents:raise RuntimeError('MEDIAN_CANNOT_WRITE_INTO_BENCHMARK')
    if root==args.source_checkpoint.expanduser().resolve().parent:raise RuntimeError('MEDIAN_CANNOT_WRITE_INTO_SOURCE')
    if original_root is not None and (root==original_root or original_root in root.parents or root in original_root.parents):
        raise RuntimeError('MEDIAN_CANNOT_WRITE_INTO_V1_LINEAGE')
    preflight_checkpoint_target(args,root,cfg,original_root)
    root.mkdir(parents=True,exist_ok=True)
    config_path=root/'run_config.json'
    if config_path.exists():
        if json.loads(config_path.read_text(encoding='utf-8'))!=cfg:raise RuntimeError('MEDIAN_EXISTING_CONFIG_DRIFT')
    else:
        if any(root.iterdir()):raise RuntimeError('MEDIAN_NONEMPTY_RESULTS_ROOT')
        base.atomic_json(config_path,cfg)
    torch.manual_seed(args.seed)
    networks={k:base.ResultHead(k,args.width).to(dev) for k in base.NAMES}
    opts={k:torch.optim.AdamW(v.parameters(),lr=args.lr,weight_decay=args.weight_decay) for k,v in networks.items()}
    op_probe=base.SharedOperationProbe().to(dev)
    op_opt=torch.optim.AdamW(op_probe.parameters(),lr=args.lr,weight_decay=args.weight_decay)
    median_net=MedianResidual(args.median_width).to(dev)
    median_opt=torch.optim.AdamW(median_net.parameters(),lr=args.lr,weight_decay=args.weight_decay)
    stream=base.previous.FreshPairs(args.seed,base.previous.reserved_pairs(tasks))
    replay=base.Replay(args.replay_capacity,args.seed+71)
    step=0
    best={k:{'step':0,'numeric_5pct':-1.,'overall_5pct':-1.} for k in base.NAMES}
    median_best={'step':0,'numeric_5pct':-1.,'overall_5pct':-1.}
    median_raw_best={'step':0,'numeric_5pct':-1.,'overall_5pct':-1.}
    latest=root/'latest.pt'
    if args.resume or args.eval_only:
        if not latest.is_file():raise RuntimeError('MEDIAN_RESUME_REQUIRES_LATEST')
        step,best,median_best,median_raw_best=restore_v2(latest,cfg,networks,opts,op_probe,op_opt,stream,replay,median_net,median_opt)
        base.emit('median_resumed',step=step,median_best=median_best)
    elif original_root is not None:
        source_file=original_root/'latest.pt'
        if not source_file.is_file():raise RuntimeError('MEDIAN_FORK_REQUIRES_V1_LATEST')
        if latest.exists():raise RuntimeError('MEDIAN_V2_RUN_EXISTS_USE_--resume')
        step,best=fork_v1(source_file,cfg,networks,opts,op_probe,op_opt,stream,replay)
        base.emit('median_forked_v1',step=step,v1_checkpoint=str(source_file),
                  v1_sha256=hashlib.sha256(source_file.read_bytes()).hexdigest())
        base.atomic_checkpoint(latest,pack_state(step,cfg,networks,opts,op_probe,op_opt,stream,replay,best,
                               median_net,median_opt,median_best,median_raw_best))
    elif latest.exists():raise RuntimeError('MEDIAN_V2_RUN_EXISTS_USE_--resume')
    validation=base.previous.load_eval(source_root,manifest,tasks,'val',args.val_examples)
    if args.eval_only:
        base.emit('median_evaluation_checkpoint',step=step,selection='aligned_latest')
        for split in ('val','test','control'):
            ev=base.previous.load_eval(source_root,manifest,tasks,split)
            report,_=evaluate(networks,op_probe,source,median_net,ev,dev)
            base.emit('median_final_evaluation',split=split,step=step,**report)
        return 0
    if args.steps<=step:raise RuntimeError(f'MEDIAN_TARGET_STEPS_NOT_ABOVE_CHECKPOINT:{step}')
    import nanojev_numerate_spine_cassette_smoke as cassette
    _,frozen,tokenizer,model_device,model_info=cassette.load_source('qwen',args)
    if (model_info.get('resolved_commit')!=identity['model_info'].get('resolved_commit') or
        model_info.get('source')!=identity['model_info'].get('source') or
        str(model_info.get('dtype'))!=str(identity['model_info'].get('dtype'))):
        raise RuntimeError('MEDIAN_QWEN_BACKBONE_IDENTITY_DRIFT')
    frozen.eval()
    base.previous.verify_capture_alignment(torch,frozen,tokenizer,model_device,source_root,tasks,args.max_prompt_tokens)
    base.emit('median_initialized',step=step,source_sha256=source_sha,
              candidate_registers=list(CANDIDATES),median_registers=[RAW,SIGNED,HEAD],
              median_head_parameters=sum(p.numel() for p in median_net.parameters()),
              frozen_qwen_trainable_parameters=0,boolean_policy='source_passthrough')
    started=time.monotonic();capture_time=update_time=0.;losses={k:[] for k in (*base.NAMES,HEAD)}
    for k in range(step+1,args.steps+1):
        t=time.monotonic()
        fresh=stream.batch(args.batch_size)
        feat=base.previous.extract_batched(torch,frozen,tokenizer,[r['prompt'] for r in fresh],
                                           model_device,args.max_prompt_tokens)
        if feat.requires_grad:raise RuntimeError('MEDIAN_BACKBONE_GRADIENT_DETECTED')
        xy=base.source_decode(source,feat.to(dev));replay.add(feat,fresh,xy)
        capture_time+=time.monotonic()-t
        t=time.monotonic()
        for _ in range(args.replay_updates):
            sampled=replay.sample(args.replay_batch)
            op_loss,head_losses=base.train_step(networks,opts,op_probe,op_opt,sampled,dev)
            for n,x in head_losses.items():losses[n].append(x)
            losses[HEAD].append(train_median(networks,op_probe,median_net,median_opt,sampled,dev))
        update_time+=time.monotonic()-t
        if k%args.report_every==0 or k==args.steps:
            base.emit('median_train_progress',step=k,examples_seen=len(stream.seen),
                 replay_records=len(replay.rows),op_loss=op_loss,
                 losses={n:round(float(np.mean(a)),6) for n,a in losses.items()},
                 elapsed_seconds=round(time.monotonic()-started,2),
                 shared_capture_seconds=round(capture_time,2),head_update_seconds=round(update_time,2))
            losses={name:[] for name in (*base.NAMES,HEAD)}
        if k%args.eval_every==0 or k==args.steps:
            report,matrix=evaluate(networks,op_probe,source,median_net,validation,dev)
            summary={n:{s:report['registers'][n][s] for s in ('numeric_5pct','overall_5pct','numeric_asinh_mae')}
                     for n in ALL_NAMES}
            base.emit('median_validation',step=k,operation_accuracy=report['operation_accuracy'],
                      source_xy_joint_5pct=report['source_xy_joint_5pct'],registers=summary,
                      median_stability=report['median_stability'],oracle_numeric_5pct=report['oracle_numeric_5pct'],
                      rescued_source_errors=report['rescued_source_errors'],
                      damaged_source_successes=report['damaged_source_successes'])
            base.atomic_json(root/f'validation-step-{k:06d}.json',{'step':k,**report})
            np.save(root/f'correctness-step-{k:06d}.npy',matrix)
            for name in base.NAMES:
                m=report['registers'][name];candidate=(m['numeric_5pct'],m['overall_5pct'])
                old=(best[name]['numeric_5pct'],best[name]['overall_5pct'])
                if candidate>old:
                    best[name]={'step':k,'numeric_5pct':candidate[0],'overall_5pct':candidate[1]}
                    base.atomic_checkpoint(root/name/'best.pt',{'schema':base.SCHEMA,'config':cfg['v1_config'],
                                          'step':k,'model':networks[name].state_dict(),
                                          'operation_probe':op_probe.state_dict(),'score':best[name]})
                    base.emit('parallel_best',register=name,**best[name])
            m=report['registers'][HEAD];cand=(m['numeric_5pct'],m['overall_5pct'])
            if cand>(median_best['numeric_5pct'],median_best['overall_5pct']):
                median_best={'step':k,'numeric_5pct':cand[0],'overall_5pct':cand[1]}
                base.atomic_checkpoint(root/HEAD/'best.pt',{'schema':SCHEMA,'config':cfg,'step':k,
                                      'median':median_net.state_dict(),
                                      'models':{x:networks[x].state_dict() for x in base.NAMES},
                                      'operation_probe':op_probe.state_dict(),'score':median_best})
                base.emit('median_best',register=HEAD,**median_best)
            m=report['registers'][RAW];cand=(m['numeric_5pct'],m['overall_5pct'])
            if cand>(median_raw_best['numeric_5pct'],median_raw_best['overall_5pct']):
                median_raw_best={'step':k,'numeric_5pct':cand[0],'overall_5pct':cand[1]}
                base.emit('median_raw_best',register=RAW,**median_raw_best)
        if k%args.checkpoint_every==0 or k==args.steps:
            base.atomic_checkpoint(latest,pack_state(k,cfg,networks,opts,op_probe,op_opt,stream,replay,best,
                                   median_net,median_opt,median_best,median_raw_best))
            for name in base.NAMES:
                base.atomic_checkpoint(root/name/'latest.pt',{'schema':base.SCHEMA,'config':cfg['v1_config'],
                                     'step':k,'model':networks[name].state_dict(),
                                     'optimizer':opts[name].state_dict(),
                                     'operation_probe':op_probe.state_dict()})
            base.atomic_checkpoint(root/HEAD/'latest.pt',{'schema':SCHEMA,'config':cfg,'step':k,
                                  'median':median_net.state_dict(),'optimizer':median_opt.state_dict(),
                                  'models':{x:networks[x].state_dict() for x in base.NAMES},
                                  'operation_probe':op_probe.state_dict()})
            base.emit('median_checkpoint',step=k,path=str(latest))
    base.emit('median_complete',step=args.steps,best=best,median_best=median_best,
              median_raw_best=median_raw_best,results_root=str(root))
    return 0


def self_test():
    torch.set_num_threads(2);torch.manual_seed(67)
    candidates=np.array([[100,100,101,102,103,104,105,1000000],
                         [-300,-299,-302,-301,500000,9,10,11],
                         [1,0,1,1,0,0,1,0]],dtype=np.float64)
    bool_mask=np.array([False,False,True])
    raw,signed=median_controls(candidates,bool_mask)
    assert raw[0]==102.5 and raw[2]==1.
    assert signed[0]<raw[0] and np.isfinite(signed).all()
    feats=torch.randn(3,base.INPUT_WIDTH)
    op=base.SharedOperationProbe();ops=op(feats).argmax(1)
    ref=torch.tensor(candidates[:,0].copy())
    med=MedianResidual(16)
    inp=median_design(feats,ops,ref,candidates,raw)
    assert inp.shape[1]==base.INPUT_WIDTH+len(base.OP_CLASSES)+1+MEDIAN_FEATURES
    before=med(inp).detach().clone()
    assert torch.equal(before,torch.zeros_like(before))
    predicts=med.decoded(before.numpy(),raw,bool_mask,candidates[:,0])
    assert np.array_equal(predicts[:2],np.rint(raw[:2])) and predicts[2]==1.
    opt=torch.optim.AdamW(med.parameters(),lr=.001)
    loss=med.objective(med(inp),raw,[102.0,100.0,1.0],bool_mask)
    opt.zero_grad();loss.backward();opt.step()
    assert torch.isfinite(loss)
    assert any(p.grad is not None for p in med.parameters())
    base.emit('median_self_test_pass',raw_median=True,signed_median=True,
              regressed_median=True,frozen_backbone_reused=True,boolean_source_passthrough=True)
    return 0


if __name__=='__main__':
    raise SystemExit(execute(parser().parse_args()))
