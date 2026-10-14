#!/usr/bin/env python3
"""NanoJev parallel FINAL v3: backbone-conditioned selector of eleven RESULT candidates.

Forks median v2/latest.pt without altering v1/v2. One frozen Qwen capture per
fresh batch. Candidate results are detached, so FINAL cannot change them through
its gradients. Default training freezes v2 candidates while training the new
selector; --train-candidates optionally also continues v2 heads and median.
Oracle unions are evaluation-only; FINAL never receives labels at inference.
"""
from __future__ import annotations

import argparse
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

SCHEMA='nanojev.parallel-result-final.v3'
NAMES=median.ALL_NAMES
N=len(NAMES)
EXTRA_WIDTH=5*N+5

def final_design(features,ops,reference,proposals,raw_median,op_probe=None):
    """All results computed from predictions; no true operand/result enters x."""
    c=np.asarray(proposals,dtype=np.float64)
    raw=np.asarray(raw_median,dtype=np.float64)
    if c.ndim!=2 or c.shape[1]!=N or raw.shape!=(len(c),):
        raise ValueError('FINAL_RESULT_VECTOR_SHAPE')
    if not np.isfinite(c).all() or not np.isfinite(raw).all():
        raise ValueError('FINAL_NONFINITE_PROPOSALS')
    base_input=median.median_design(features,ops,reference,c[:,:len(median.CANDIDATES)],raw)
    z=base.signed_encode(c).astype(np.float32)
    med=base.signed_encode(raw).astype(np.float32)
    centered=z-med[:,None]
    distance=np.abs(z[:,:,None]-z[:,None,:])
    for j in range(N):distance[:,j,j]=np.inf
    nearest=distance.min(axis=2)
    sorted_z=np.sort(z,axis=1)
    spread=np.median(np.abs(z-med[:,None]),axis=1)
    if op_probe is None:
        op_conf=np.ones(len(c),dtype=np.float32)
    else:
        with torch.no_grad():
            op_conf=op_probe(features).softmax(-1).amax(-1).detach().cpu().numpy().astype(np.float32)
    extra=np.column_stack((z,z-z[:,0:1],centered,np.abs(centered),nearest,
                           spread,sorted_z[:,-1]-sorted_z[:,0],
                           np.std(z,axis=1),np.abs(z[:,0]-med),op_conf)).astype(np.float32)
    if extra.shape[1]!=EXTRA_WIDTH:raise AssertionError('FINAL_INPUT_WIDTH')
    return torch.cat((base_input,torch.as_tensor(extra,device=features.device,dtype=torch.float32)),dim=1)


class FinalHead(nn.Module):
    """Three independently supervised outputs: rank, correctness, and switch gate."""
    def __init__(self,width=128):
        super().__init__()
        size=base.INPUT_WIDTH+len(base.OP_CLASSES)+1+median.MEDIAN_FEATURES+EXTRA_WIDTH
        self.trunk=nn.Sequential(nn.LayerNorm(size,elementwise_affine=False),
                                 nn.Linear(size,width),nn.GELU(),
                                 nn.Linear(width,width),nn.GELU())
        self.rank=nn.Linear(width,N)
        self.correct=nn.Linear(width,N)
        self.gate=nn.Linear(width,1)
        with torch.no_grad():
            self.rank.bias.fill_(-1.0);self.rank.bias[0]=2.0
            self.correct.bias.fill_(-1.0)
            self.gate.bias.fill_(-4.0)

    def forward(self,x):
        y=self.trunk(x.float())
        return self.rank(y),self.correct(y),self.gate(y).flatten()


def final_objective(output,candidates,truth,is_bool):
    ranks,confidence,gate=output
    eligible=~np.asarray(is_bool,dtype=bool)
    if not eligible.any():return ranks.sum()*0,{'ranking':0.,'correctness':0.,'gate':0.}
    cand=np.asarray(candidates,dtype=np.float64)[eligible]
    targets=np.asarray(truth,dtype=np.float64)[eligible]
    flag=np.zeros(len(targets),dtype=bool)
    hits=base.exact_or_relative_hits(cand,targets[:,None],flag[:,None])
    # When R0 is correct, R0 is the preferred supervised action. Otherwise,
    # distribute the target over the correct minority candidates, if any.
    preference=np.zeros_like(hits,dtype=np.float32)
    prefer_source=hits[:,0] | ~hits.any(axis=1)
    preference[prefer_source,0]=1.
    alternatives=~prefer_source
    if alternatives.any():
        successes=hits[alternatives].astype(np.float32)
        successes[:,0]=0.
        preference[alternatives]=successes/successes.sum(axis=1,keepdims=True)
    t=torch.as_tensor(preference,device=ranks.device)
    y=torch.as_tensor(hits.astype(np.float32),device=ranks.device)
    idx=torch.as_tensor(eligible,device=ranks.device)
    ranking=-(t*F.log_softmax(ranks[idx],dim=1)).sum(axis=1)
    # Weight rescue opportunities (otherwise most examples prefer R0).
    weights=torch.as_tensor(np.where(prefer_source,1.,3.),device=ranks.device,dtype=torch.float32)
    ranking=(ranking*weights).sum()/weights.sum()
    correctness=F.binary_cross_entropy_with_logits(confidence[idx],y)
    switch=torch.as_tensor((~prefer_source).astype(np.float32),device=ranks.device)
    switching=F.binary_cross_entropy_with_logits(gate[idx],switch,pos_weight=torch.tensor(4.,device=ranks.device))
    loss=ranking+.5*correctness+.6*switching
    return loss,{'ranking':float(ranking.detach()),'correctness':float(correctness.detach()),'gate':float(switching.detach())}


@torch.inference_mode()
def infer_candidates(networks,op_probe,median_net,features,source_xy):
    ops,ref,first8,bool_mask=median.candidates_for_rows(networks,op_probe,features,source_xy)
    raw,signed=median.median_controls(first8,bool_mask)
    x_median=median.median_design(features,ops,ref,first8,raw)
    delta=median_net(x_median).detach().cpu().numpy()
    learned=median_net.decoded(delta,raw,bool_mask,first8[:,0])
    proposals=np.column_stack((first8,raw,signed,learned))
    x=final_design(features,ops,ref,proposals,raw,op_probe)
    return proposals,x,bool_mask,ops


def train_final(networks,op_probe,median_net,final_net,final_opt,rows,device):
    features=torch.stack([r[0] for r in rows]).to(device,dtype=torch.float32)
    xy=np.asarray([[r[4],r[5]] for r in rows],dtype=np.int64)
    truth,boolean=base.truth_for([{'a':r[1],'b':r[2],'operation':r[3]} for r in rows])
    proposals,x,_,_=infer_candidates(networks,op_probe,median_net,features,xy)
    final_net.train()
    loss,parts=final_objective(final_net(x),proposals,truth,boolean)
    final_opt.zero_grad(set_to_none=True)
    loss.backward()
    nn.utils.clip_grad_norm_(final_net.parameters(),1.)
    final_opt.step()
    return float(loss.detach()),parts


def selection_signals(output,boolean):
    rank,correct,gate=output
    r=rank.detach().cpu().numpy()
    p=correct.sigmoid().detach().cpu().numpy()
    g=gate.sigmoid().detach().cpu().numpy()
    winner=np.argmax(r,axis=1)
    win_conf=p[np.arange(len(p)),winner]
    signal=g*win_conf
    winner[np.asarray(boolean,dtype=bool)]=0
    signal[np.asarray(boolean,dtype=bool)]=0.
    return winner,signal,p


def choose(proposals,winner,signal,threshold,boolean):
    a=np.asarray(proposals,dtype=np.float64)
    use=(winner!=0)&(signal>=threshold)&(~np.asarray(boolean,dtype=bool))
    indices=np.where(use,winner,0)
    return a[np.arange(len(a)),indices],indices


def calibrate(proposals,winner,signal,truth,boolean,min_retention=.99):
    """Fit only on the validation split. No switch is the fail-closed baseline."""
    a=np.asarray(proposals); t=np.asarray(truth)
    b=np.asarray(boolean,dtype=bool); number=~b
    source_hit=base.exact_or_relative_hits(a[:,0],t,b)
    src_n=int((source_hit&number).sum())
    best={'threshold':1.01,'numeric_5pct':float(source_hit[number].mean()) if number.any() else 0.,
          'rescued':0,'damaged':0,'source_retention':1.,'switched':0}
    for threshold in np.linspace(1.,0.,101):
        prediction,selected=choose(a,winner,signal,float(threshold),b)
        hits=base.exact_or_relative_hits(prediction,t,b)
        damaged=int(((~hits)&source_hit&number).sum())
        retention=1.-damaged/max(1,src_n)
        if retention+1e-12<min_retention:continue
        rescued=int((hits&~source_hit&number).sum())
        accuracy=float(hits[number].mean()) if number.any() else 0.
        if accuracy>best['numeric_5pct']+1e-12 or (abs(accuracy-best['numeric_5pct'])<=1e-12 and rescued>best['rescued']):
            best={'threshold':float(threshold),'numeric_5pct':accuracy,
                  'rescued':rescued,'damaged':damaged,'source_retention':retention,'switched':int((selected!=0).sum())}
    return best


@torch.inference_mode()
def evaluate(networks,op_probe,source,median_net,final_net,eval_rows,device,threshold=1.01,calibrate_on_validation=False,min_retention=.99,batch_size=64):
    features,xy,ops,families=eval_rows
    truth=np.asarray([base.result_value(int(a),int(b),op) for (a,b),op in zip(xy,ops)],dtype=np.float64)
    boolean=np.asarray([op in base.BOOL_OPS for op in ops],dtype=bool)
    prediction_chunks=[];winner_chunks=[];signal_chunks=[];conf_chunks=[];ops_correct=[]
    final_net.eval();median_net.eval()
    for start in range(0,len(xy),batch_size):
        end=min(start+batch_size,len(xy))
        feats=torch.as_tensor(features[start:end],dtype=torch.float32,device=device)
        decoded=base.source_decode(source,feats)
        candidate,x,pred_bool,pred_ops=infer_candidates(networks,op_probe,median_net,feats,decoded)
        output=final_net(x)
        winner,signal,p=selection_signals(output,pred_bool)
        prediction_chunks.append(candidate);winner_chunks.append(winner)
        signal_chunks.append(signal);conf_chunks.append(p)
        op_names=[base.OP_CLASSES[int(i)] for i in pred_ops.cpu().numpy()]
        ops_correct.extend(a==b for a,b in zip(op_names,ops[start:end]))
    candidates=np.concatenate(prediction_chunks)
    winner=np.concatenate(winner_chunks)
    signal=np.concatenate(signal_chunks)
    confidence=np.concatenate(conf_chunks)
    if calibrate_on_validation:
        calib=calibrate(candidates,winner,signal,truth,boolean,min_retention)
        threshold=calib['threshold']
    else:
        calib=None
    selected,selected_ids=choose(candidates,winner,signal,threshold,boolean)
    ungated=candidates[np.arange(len(candidates)),winner]
    source=candidates[:,0]
    source_hits=base.exact_or_relative_hits(source,truth,boolean)
    final_hits=base.exact_or_relative_hits(selected,truth,boolean)
    matrix=np.column_stack([base.exact_or_relative_hits(candidates[:,j],truth,boolean) for j in range(N)])
    numeric=~boolean
    report={'rows':len(truth),'step_source':'aligned_v2_snapshot',
            'operation_accuracy':float(np.mean(ops_correct)),
            'source':base.diagnostic(source,truth,boolean),
            'final':base.diagnostic(selected,truth,boolean),
            'ungated':base.diagnostic(ungated,truth,boolean),
            'oracle_numeric_5pct':float(matrix[numeric].any(axis=1).mean()) if numeric.any() else None,
            'oracle_original_numeric_5pct':float(matrix[numeric,:len(median.CANDIDATES)].any(axis=1).mean()) if numeric.any() else None,
            'oracle_with_final_numeric_5pct':float(np.column_stack((matrix,final_hits))[numeric].any(axis=1).mean()) if numeric.any() else None,
            'threshold':float(threshold),'calibration':calib,
            'selected_count':{name:int((selected_ids==j).sum()) for j,name in enumerate(NAMES)},
            'winner_before_gate_count':{name:int((winner==j).sum()) for j,name in enumerate(NAMES)},
            'switch_signal_p50':float(np.quantile(signal,.5)),
            'switch_signal_p90':float(np.quantile(signal,.9)),
            'rescued_source_errors':int((final_hits&(~source_hits)&numeric).sum()),
            'damaged_source_successes':int(((~final_hits)&source_hits&numeric).sum()),
            'source_retention':float(1.-((~final_hits)&source_hits&numeric).sum()/max(1,(source_hits&numeric).sum())),
            'numeric_candidates_5pct':{name:float(matrix[numeric,j].mean()) for j,name in enumerate(NAMES)} if numeric.any() else {},
            'confidence_mean':{name:float(confidence[:,j].mean()) for j,name in enumerate(NAMES)},
            'examples':[{'xy':[int(v) for v in xy[i]],'operation':ops[i],
                         'truth':float(truth[i]),'selected_name':NAMES[int(selected_ids[i])],
                         'selected':float(selected[i]),
                         'candidates':{name:float(candidates[i,j]) for j,name in enumerate(NAMES)}} for i in range(min(12,len(truth)))]}
    return report,np.column_stack((matrix,final_hits))


def parser():
    p=median.parser()
    p.description=__doc__
    p.set_defaults(results_root=Path.home()/'NanoJev'/'runs'/'parallel_result_final_search_v3')
    p.add_argument('--fork-v2-root',type=Path,default=None,help='Import v2/latest.pt into a new v3 lineage')
    p.add_argument('--final-width',type=int,default=128)
    p.add_argument('--min-source-retention',type=float,default=.99)
    p.add_argument('--train-candidates',action='store_true',help='Also train the seven candidate heads, operation probe, and median; default freezes them')
    return p


def cfg_for(args,manifest,source_sha,identity):
    return {'schema':SCHEMA,'v2_config':median.config_for(args,manifest,source_sha,identity),
            'final_width':args.final_width,'min_source_retention':args.min_source_retention,
            'train_candidates':bool(args.train_candidates),'final_members':list(NAMES),
            'boolean_policy':'source_passthrough','selector_target':'source_preferred_multi_correct'}


def pack_state(step,cfg,networks,opts,op_probe,op_opt,stream,replay,best,median_net,median_opt,median_best,raw_best,final_net,final_opt,final_best,threshold):
    ck=median.pack_state(step,cfg['v2_config'],networks,opts,op_probe,op_opt,stream,replay,best,median_net,median_opt,median_best,raw_best)
    ck.update({'schema':SCHEMA,'config':cfg,'final':final_net.state_dict(),
               'final_optimizer':final_opt.state_dict(),'final_best':final_best,
               'final_threshold':float(threshold)})
    return ck


def restore_v3(path,cfg,networks,opts,op_probe,op_opt,stream,replay,median_net,median_opt,final_net,final_opt):
    ck=torch.load(path,map_location='cpu',weights_only=True)
    if ck.get('schema')!=SCHEMA or ck.get('config')!=cfg:raise RuntimeError('FINAL_RESUME_CONFIG_OR_SOURCE_DRIFT')
    old={**ck,'schema':median.SCHEMA,'config':cfg['v2_config']}
    step,best,mb,rb=_load_v2_payload(old,cfg['v2_config'],networks,opts,op_probe,op_opt,stream,replay,median_net,median_opt)
    final_net.load_state_dict(ck['final']);final_opt.load_state_dict(ck['final_optimizer'])
    return step,best,mb,rb,dict(ck['final_best']),float(ck['final_threshold'])


def _load_v2_payload(ck,cfg,networks,opts,op_probe,op_opt,stream,replay,median_net,median_opt):
    if ck.get('schema')!=median.SCHEMA or ck.get('config')!=cfg:
        raise RuntimeError('FINAL_V2_CHECKPOINT_CONFIG_DRIFT')
    base_ck={**ck,'config':cfg['v1_config']}
    step,best=median._load_base_payload(base_ck,cfg['v1_config'],networks,opts,op_probe,op_opt,stream,replay)
    median_net.load_state_dict(ck['median']);median_opt.load_state_dict(ck['median_optimizer'])
    return step,best,dict(ck['median_best']),dict(ck['median_raw_best'])


def execute(args):
    if args.self_test:return self_test()
    if args.fork_v1_root is not None:raise ValueError('FINAL_DO_NOT_USE_FORK_V1_ROOT_USE_FORK_V2_ROOT')
    if args.fork_v2_root is not None and (args.resume or args.eval_only):
        raise ValueError('FINAL_FORK_INCOMPATIBLE_WITH_RESUME_OR_EVAL_ONLY')
    if args.final_width<8 or not (0<=args.min_source_retention<=1):
        raise ValueError('FINAL_INVALID_HEAD_PARAMETERS')
    if args.additional_steps is not None and args.additional_steps<1:raise ValueError('FINAL_ADDITIONAL_STEPS_MUST_BE_POSITIVE')
    if args.threads>0:torch.set_num_threads(args.threads)
    if args.device=='cuda' and not torch.cuda.is_available():raise RuntimeError('FINAL_CUDA_UNAVAILABLE')
    device=torch.device(args.device)
    benchmark=args.v3_root.expanduser().resolve();manifest,tasks=base.previous.read_benchmark(benchmark)
    identity=json.loads((benchmark/'model_identity.json').read_text(encoding='utf-8'))
    source,sha=base.source_factory(args.source_checkpoint,identity,device)
    cfg=cfg_for(args,manifest,sha,identity)
    root=args.results_root.expanduser().resolve()
    if root==benchmark or benchmark in root.parents or root==args.source_checkpoint.expanduser().resolve().parent:
        raise RuntimeError('FINAL_UNSAFE_RESULTS_ROOT')
    fork=args.fork_v2_root.expanduser().resolve() if args.fork_v2_root else None
    if fork is not None and (root==fork or fork in root.parents or root in fork.parents):
        raise RuntimeError('FINAL_CANNOT_WRITE_TO_V2_LINEAGE')
    latest=root/'latest.pt'
    # Preflight target BEFORE writing any configuration or fork checkpoint.
    if args.resume or args.eval_only:
        if not latest.is_file():raise RuntimeError('FINAL_RESUME_REQUIRES_LATEST')
        peek=torch.load(latest,map_location='cpu',weights_only=True)
        if peek.get('schema')!=SCHEMA:raise RuntimeError('FINAL_RESUME_WRONG_SCHEMA')
        inherited=int(peek['step'])
    elif fork is not None:
        source_file=fork/'latest.pt'
        if not source_file.is_file():raise RuntimeError('FINAL_FORK_REQUIRES_V2_LATEST')
        if latest.exists():raise RuntimeError('FINAL_RUN_EXISTS_REMOVE_FORK_AND_USE_--resume')
        peek=torch.load(source_file,map_location='cpu',weights_only=True)
        if peek.get('schema')!=median.SCHEMA:raise RuntimeError('FINAL_FORK_REQUIRES_V2_SCHEMA')
        inherited=int(peek['step'])
    else:
        raise RuntimeError('FINAL_REQUIRES_--fork-v2-root_OR_--resume')
    target=(inherited+args.additional_steps) if args.additional_steps is not None else args.steps
    if not args.eval_only and target<=inherited:
        raise RuntimeError(f'FINAL_TARGET_NOT_ABOVE_CHECKPOINT:{inherited}; USE_--additional-steps 500 OR_--steps {inherited+500}')
    root.mkdir(parents=True,exist_ok=True)
    config_path=root/'run_config.json'
    if config_path.exists():
        if json.loads(config_path.read_text(encoding='utf-8'))!=cfg:raise RuntimeError('FINAL_EXISTING_CONFIG_DRIFT')
    else:
        if any(root.iterdir()):raise RuntimeError('FINAL_NONEMPTY_RESULTS_ROOT')
        base.atomic_json(config_path,cfg)
    torch.manual_seed(args.seed)
    networks={n:base.ResultHead(n,args.width).to(device) for n in base.NAMES}
    opts={n:torch.optim.AdamW(model.parameters(),lr=args.lr,weight_decay=args.weight_decay) for n,model in networks.items()}
    op_probe=base.SharedOperationProbe().to(device)
    op_opt=torch.optim.AdamW(op_probe.parameters(),lr=args.lr,weight_decay=args.weight_decay)
    median_net=median.MedianResidual(args.median_width).to(device)
    median_opt=torch.optim.AdamW(median_net.parameters(),lr=args.lr,weight_decay=args.weight_decay)
    final_net=FinalHead(args.final_width).to(device)
    final_opt=torch.optim.AdamW(final_net.parameters(),lr=args.lr,weight_decay=args.weight_decay)
    stream=base.previous.FreshPairs(args.seed,base.previous.reserved_pairs(tasks))
    replay=base.Replay(args.replay_capacity,args.seed+71)
    best={n:{'step':0,'numeric_5pct':-1.,'overall_5pct':-1.} for n in base.NAMES}
    median_best={'step':0,'numeric_5pct':-1.,'overall_5pct':-1.}
    raw_best={'step':0,'numeric_5pct':-1.,'overall_5pct':-1.}
    final_best={'step':0,'numeric_5pct':-1.,'overall_5pct':-1.}
    threshold=1.01
    if args.resume or args.eval_only:
        step,best,median_best,raw_best,final_best,threshold=restore_v3(latest,cfg,networks,opts,op_probe,op_opt,stream,replay,median_net,median_opt,final_net,final_opt)
        base.emit('final_resumed',step=step,threshold=threshold,final_best=final_best)
    else:
        ck=torch.load(source_file,map_location='cpu',weights_only=True)
        step,best,median_best,raw_best=_load_v2_payload(ck,cfg['v2_config'],networks,opts,op_probe,op_opt,stream,replay,median_net,median_opt)
        # Preflight validated target, schema, config and source identity before save.
        base.atomic_checkpoint(latest,pack_state(step,cfg,networks,opts,op_probe,op_opt,stream,replay,best,median_net,median_opt,median_best,raw_best,final_net,final_opt,final_best,threshold))
        base.emit('final_forked_v2',step=step,v2_checkpoint=str(source_file),v2_sha256=hashlib.sha256(source_file.read_bytes()).hexdigest())
    validation=base.previous.load_eval(benchmark,manifest,tasks,'val',args.val_examples)
    if args.eval_only:
        for split in ('val','test','control'):
            rows=base.previous.load_eval(benchmark,manifest,tasks,split)
            report,_=evaluate(networks,op_probe,source,median_net,final_net,rows,device,threshold)
            base.emit('final_evaluation',split=split,step=step,**report)
        return 0
    import nanojev_numerate_spine_cassette_smoke as cassette
    _,frozen,tokenizer,model_device,model_info=cassette.load_source('qwen',args)
    if (model_info.get('resolved_commit')!=identity['model_info'].get('resolved_commit') or
        model_info.get('source')!=identity['model_info'].get('source') or
        str(model_info.get('dtype'))!=str(identity['model_info'].get('dtype'))):
        raise RuntimeError('FINAL_QWEN_BACKBONE_IDENTITY_DRIFT')
    frozen.eval()
    base.previous.verify_capture_alignment(torch,frozen,tokenizer,model_device,benchmark,tasks,args.max_prompt_tokens)
    base.emit('final_initialized',step=step,target_step=target,train_candidates=args.train_candidates,
              candidate_registers=list(NAMES),final_parameters=sum(p.numel() for p in final_net.parameters()),
              backbone_parameters_trainable=0,min_source_retention=args.min_source_retention)
    start=time.monotonic();capture_seconds=update_seconds=0.;acc=[]
    for k in range(step+1,target+1):
        begin=time.monotonic()
        rows=stream.batch(args.batch_size)
        f=base.previous.extract_batched(torch,frozen,tokenizer,[r['prompt'] for r in rows],model_device,args.max_prompt_tokens)
        if f.requires_grad:raise RuntimeError('FINAL_BACKBONE_GRADIENT_DETECTED')
        decoded=base.source_decode(source,f.to(device));replay.add(f,rows,decoded)
        capture_seconds+=time.monotonic()-begin
        begin=time.monotonic()
        for _ in range(args.replay_updates):
            sampled=replay.sample(args.replay_batch)
            if args.train_candidates:
                base.train_step(networks,opts,op_probe,op_opt,sampled,device)
                median.train_median(networks,op_probe,median_net,median_opt,sampled,device)
            loss,parts=train_final(networks,op_probe,median_net,final_net,final_opt,sampled,device)
            acc.append(loss)
        update_seconds+=time.monotonic()-begin
        if k%args.report_every==0 or k==target:
            base.emit('final_train_progress',step=k,mean_loss=round(float(np.mean(acc)),6),
                      replay_records=len(replay.rows),examples_seen=len(stream.seen),
                      elapsed_seconds=round(time.monotonic()-start,2),
                      shared_capture_seconds=round(capture_seconds,2),head_update_seconds=round(update_seconds,2))
            acc=[]
        if k%args.eval_every==0 or k==target:
            report,matrix=evaluate(networks,op_probe,source,median_net,final_net,validation,device,
                                    threshold=threshold,calibrate_on_validation=True,
                                    min_retention=args.min_source_retention)
            threshold=report['threshold']
            base.emit('final_validation',step=k,source_numeric_5pct=report['source']['numeric_5pct'],
                      final_numeric_5pct=report['final']['numeric_5pct'],
                      ungated_numeric_5pct=report['ungated']['numeric_5pct'],
                      oracle_numeric_5pct=report['oracle_numeric_5pct'],
                      oracle_original_numeric_5pct=report['oracle_original_numeric_5pct'],
                      threshold=threshold,selected_count=report['selected_count'],
                      rescued=report['rescued_source_errors'],damaged=report['damaged_source_successes'],
                      source_retention=report['source_retention'])
            base.atomic_json(root/f'validation-step-{k:06d}.json',{'step':k,**report})
            np.save(root/f'correctness-step-{k:06d}.npy',matrix)
            accuracy=(report['final']['numeric_5pct'],report['final']['overall_5pct'])
            old=(final_best['numeric_5pct'],final_best['overall_5pct'])
            if accuracy>old:
                final_best={'step':k,'numeric_5pct':accuracy[0],'overall_5pct':accuracy[1]}
                base.atomic_checkpoint(root/'final'/'best.pt',{'schema':SCHEMA,'config':cfg,
                       'step':k,'final':final_net.state_dict(),'models':{n:m.state_dict() for n,m in networks.items()},
                       'median':median_net.state_dict(),'operation_probe':op_probe.state_dict(),
                       'threshold':threshold,'score':final_best})
                base.emit('final_best',**final_best,threshold=threshold)
        if k%args.checkpoint_every==0 or k==target:
            base.atomic_checkpoint(latest,pack_state(k,cfg,networks,opts,op_probe,op_opt,stream,replay,best,
                  median_net,median_opt,median_best,raw_best,final_net,final_opt,final_best,threshold))
            base.emit('final_checkpoint',step=k,path=str(latest),threshold=threshold)
    base.emit('final_complete',step=target,best=final_best,threshold=threshold,results_root=str(root))
    return 0


def self_test():
    torch.manual_seed(54)
    v=np.asarray([[600000,100,200,300,400,500,600,700,800,900,1000],
                  [4050000,4000000,4040000,4100000,4200000,4300000,4400000,4500000,4600000,4700000,4800000]],dtype=np.float64)
    feat=torch.randn(2,base.INPUT_WIDTH)
    ops=torch.tensor([base.OP_IDS['written_product']]*2)
    ref=torch.as_tensor(v[:,0],dtype=torch.float32)
    x=final_design(feat,ops,ref,v,np.median(v[:,:8],axis=1))
    model=FinalHead(16);out=model(x)
    loss,parts=final_objective(out,v,np.asarray([500.,4050000.]),np.asarray([False,False]))
    loss.backward()
    assert np.isfinite(float(loss.detach())) and all(torch.isfinite(p.grad).all() for p in model.parameters() if p.grad is not None)
    winner,signals,_=selection_signals(out,np.zeros(2,dtype=bool))
    selected,ids=choose(v,winner,signals,1.01,np.zeros(2,dtype=bool))
    assert np.array_equal(selected,v[:,0]) and not ids.any()
    best=calibrate(v,np.array([5,2]),np.array([.98,.98]),np.asarray([500.,4050000.]),np.array([False,False]),1.)
    assert best['damaged']==0
    base.emit('final_self_test_pass',candidates=N,backbone_attached=True,source_fallback=True,
              independent_rank_correctness_gate=True,retention_calibration=True)
    return 0

if __name__=='__main__':raise SystemExit(execute(parser().parse_args()))
