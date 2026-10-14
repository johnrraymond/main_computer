#!/usr/bin/env python3
"""Separate continuous-log-magnitude experiment forked from operation/result v1.2.

No modifications or writes to the source checkpoint, legacy training run, or
frozen Qwen3-0.6B. The old operation classifier and 38-head decoder are frozen
for matched reference evaluation; new unbounded global magnitude predictions
receive operand and differentiable executed-result supervision.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import time

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

import nanojev_register_operation_result_cutover as op

b, h, c, previous = op.b, op.h, op.c, op.previous
emit, atomic_json = op.emit, op.atomic_json
SCHEMA = 'nanojev.continuous-magnitude-cutover.v1'
DEFAULT_ROOT = Path.home() / 'NanoJev' / 'runs' / 'register_continuous_magnitude_cutover_v1'
DEFAULT_SOURCE = op.DEFAULT_ROOT / 'latest.pt'


class ContinuousNumeracy(nn.Module):
    """Global log1p magnitude, with sign/zero heads initialized from the source.

    Magnitude is NOT selected by buckets or clipped at magnitude boundaries.
    Freezing the operation/classifier source means improvements or regressions
    can be assigned to the continuous decoder, not an operation-head update.
    """
    def __init__(self, stage='continuity', rank=8, residual_scale=.25,
                 op_width=96, width=128):
        super().__init__()
        if width < 1:
            raise ValueError('CONTINUOUS_WIDTH_INVALID')
        self.source = op.ExecutableNumeracy(stage, rank, residual_scale, op_width)
        self.magnitude = nn.Sequential(
            nn.LayerNorm(h.INPUT_WIDTH, elementwise_affine=False),
            nn.Linear(h.INPUT_WIDTH, width), nn.GELU(),
            nn.Linear(width, 2),
        )
        self.sign_zero = nn.Sequential(
            nn.LayerNorm(h.INPUT_WIDTH, elementwise_affine=False),
            nn.Linear(h.INPUT_WIDTH, 4),
        )
        self._initialize_magnitude()
        self.freeze_source()

    def _initialize_magnitude(self):
        # Neutral mid-range start; no random many-orders-of-magnitude spikes.
        nn.init.zeros_(self.magnitude[-1].weight)
        nn.init.constant_(self.magnitude[-1].bias, 8.)

    def initialize_sign_zero(self):
        """Copy the trained zero/sign linear heads from a continuity source."""
        classifiers = getattr(self.source.registers, 'classifiers', None)
        if classifiers is None:
            raise RuntimeError('CONTINUOUS_EXPECTS_CONTINUITY_SOURCE')
        with torch.no_grad():
            indexes = torch.tensor([0, 1, 7, 8], device=classifiers.weight.device)
            self.sign_zero[1].weight.copy_(classifiers.weight.index_select(0, indexes))
            self.sign_zero[1].bias.copy_(classifiers.bias.index_select(0, indexes))

    def freeze_source(self):
        for param in self.source.parameters():
            param.requires_grad_(False)
        self.source.eval()

    def train(self, mode=True):
        super().train(mode)
        self.source.eval()
        return self

    def forward(self, features):
        with torch.no_grad():
            legacy_raw, op_logits = self.source(features)
        magnitude_raw = self.magnitude(features.float())
        signs = self.sign_zero(features.float()).reshape(-1, 2, 2)
        return magnitude_raw, signs, op_logits, legacy_raw


def decode_continuous(magnitude_raw, signs, *, differentiable):
    """Return signed real values without any bucket routing or hard clipping.

    Magnitude is expm1(softplus(raw)), positive and continuous over the entire
    representable range. Float64 exponentiation avoids FP32 exponent overflow.
    Hard sign/zero/rounding is applied only for final discrete inference.
    """
    log_magnitudes = F.softplus(magnitude_raw.double())
    magnitude = torch.expm1(log_magnitudes)
    if differentiable:
        zero_probability = torch.sigmoid(signs[..., 0].double())
        sign_expectation = 2 * torch.sigmoid(signs[..., 1].double()) - 1
        return (1 - zero_probability) * sign_expectation * magnitude
    zero = signs[..., 0] > 0
    sign = torch.where(signs[..., 1] > 0, 1., -1.)
    integers = torch.round(magnitude) * sign
    return torch.where(zero, 0., integers)


def continuous_loss(magnitude_raw, signs, op_logits, labels, operations,
                    *, magnitude_weight=1., sign_weight=.1,
                    result_weight=.12, candidate_weight=.12,
                    temperature=1., result_ramp=1.):
    if any(name not in op.OP_ID for name in operations):
        raise ValueError('CONTINUOUS_HELDOUT_OPERATION_MUST_NOT_TRAIN')
    if temperature <= 0:
        raise ValueError('CONTINUOUS_TEMPERATURE_INVALID')
    target_log = labels.double().abs().log1p()
    predicted_log = F.softplus(magnitude_raw.double())
    # This objective is in global log units, not bucket-specific coordinates.
    direct = F.smooth_l1_loss(predicted_log, target_log, beta=.05)
    z_target = (labels == 0).double()
    sign_target = (labels > 0).double()
    zeros = F.binary_cross_entropy_with_logits(signs[..., 0].double(), z_target)
    nonzero = (labels != 0).double()
    signed = F.binary_cross_entropy_with_logits(signs[..., 1].double(),
                                                 sign_target, reduction='none')
    signed = (signed * nonzero).sum() / nonzero.sum().clamp_min(1.)
    predicted = decode_continuous(magnitude_raw, signs, differentiable=True)
    if not torch.isfinite(predicted).all():
        raise RuntimeError('CONTINUOUS_NONFINITE_MAGNITUDE')
    true_result = torch.as_tensor(
        [op.exact_execute(op.OP_TO_PROGRAM[name], int(a), int(b_))
         for name, (a,b_) in zip(operations, labels.tolist())],
        dtype=torch.float32, device=labels.device)
    losses = op._per_candidate_losses(predicted, labels, true_result,
                                       [op.OP_TO_PROGRAM[n] for n in operations])
    ids = torch.tensor([op.OP_ID[n] for n in operations],
                       dtype=torch.long, device=labels.device)
    oracle_result = losses.gather(1,ids[:,None]).mean()
    probs = F.softmax(op_logits.float()/temperature, dim=-1)
    candidate_result = (probs * losses).sum(-1).mean()
    result_part = result_ramp * (result_weight*oracle_result + candidate_weight*candidate_result)
    total = magnitude_weight*direct + sign_weight*(zeros+signed) + result_part
    if not torch.isfinite(total):
        raise RuntimeError('CONTINUOUS_NONFINITE_LOSS')
    return total, {'global_log_mae':(predicted_log-target_log).abs().mean().detach(),
                   'direct': direct.detach(), 'zero_bce':zeros.detach(),
                   'sign_bce':signed.detach(), 'oracle_result':oracle_result.detach(),
                   'candidate_result':candidate_result.detach(),
                   'result_ramp':result_ramp, 'total':total.detach()}


def predict_batch(network, features, device):
    was_training=network.training
    network.eval()
    with torch.inference_mode():
        tensors=torch.as_tensor(features,dtype=torch.float32,device=device)
        lm,sign,ops,_=network(tensors)
        nums=decode_continuous(lm,sign,differentiable=False).cpu().numpy()
        choices=ops.float().argmax(-1).cpu().numpy()
        probabilities=F.softmax(ops.float(),-1).cpu().numpy()
    network.train(was_training)
    outputs=[]
    for (x,y),idx,prob in zip(nums,choices,probabilities):
        name=op.TRAIN_OPS[int(idx)]; program=op.OP_TO_PROGRAM[name]
        x,y=int(x),int(y)
        outputs.append({'x':x,'y':y,'operation':name,'program':program,
                        'operation_probability':float(prob[idx]),
                        'result':op.exact_execute(program,x,y),
                        'result_kind':'boolean' if program in op.BOOL_PROGRAMS else 'number'})
    return outputs


def evaluate(network, evaluation, device, batch_size=256):
    features,numbers,ops,families=evaluation
    nums=[];old_nums=[];op_ids=[];log_errors=[]
    before=network.training
    network.eval()
    with torch.inference_mode():
        for i in range(0,len(features),batch_size):
            feature=torch.as_tensor(features[i:i+batch_size],dtype=torch.float32,device=device)
            lm,sign,logits,legacy=network(feature)
            nums.append(decode_continuous(lm,sign,differentiable=False).cpu().numpy())
            old_nums.append(c.decode(legacy.float().cpu().numpy()))
            op_ids.extend(logits.float().argmax(-1).cpu().numpy().tolist())
            labels=np.asarray(numbers[i:i+batch_size],dtype=np.float64)
            log_errors.append(np.abs(F.softplus(lm.double()).cpu().numpy() -
                                     np.log1p(np.abs(labels))))
    network.train(before)
    continuous_float=np.concatenate(nums)
    if not np.isfinite(continuous_float).all() or (np.abs(continuous_float)>9e18).any():
        raise RuntimeError('CONTINUOUS_INVALID_EVALUATION_RANGE')
    estimated=continuous_float.astype(np.int64)
    legacy=np.concatenate(old_nums).astype(np.int64)
    predictions=[op.TRAIN_OPS[int(k)] for k in op_ids]
    programs=[op.OP_TO_PROGRAM[n] for n in predictions]
    true_programs=[op.OP_TO_PROGRAM[n] for n in ops]
    exact_results=np.asarray([op.exact_execute(n,int(a),int(b_)) for n,(a,b_) in zip(true_programs,numbers)],dtype=np.float64)
    boolean=np.asarray([p in op.BOOL_PROGRAMS for p in true_programs])
    numerical=~boolean
    def score(values):
        result=[op.exact_execute(p,int(a),int(b_)) for p,(a,b_) in zip(programs,values)]
        pass_results=op.relative_pass(result, exact_results, boolean)
        operand_good=previous.correctness(values,numbers)
        return result, pass_results, operand_good
    results,passes,operand=score(estimated)
    legacy_results,legacy_passes,legacy_operand=score(legacy)
    op_true=[op.exact_execute(p,int(a),int(b_)) for p,(a,b_) in zip(true_programs,estimated)]
    correct_values=[op.exact_execute(p,int(a),int(b_)) for p,(a,b_) in zip(programs,numbers)]
    known=np.asarray([n in op.OP_ID for n in ops])
    operation_hits=np.asarray([a==b for a,b in zip(predictions,ops)])
    program_hits=np.asarray([a==b for a,b in zip(programs,true_programs)])
    log_err=np.concatenate(log_errors)
    within_boundary=np.isin(np.abs(legacy), np.asarray(h.CUTS))
    # Same examples, same operation predictions, same executor: matched control.
    data={
        'rows':len(numbers), 'operation_template_accuracy':float(operation_hits[known].mean()) if known.any() else None,
        'operation_program_accuracy':float(program_hits[known].mean()) if known.any() else None,
        'unsupported_heldout_operation_rows':int((~known).sum()),
        'result_5pct_pass_rate':float(passes.mean()),
        'numeric_result_5pct_pass_rate':float(passes[numerical].mean()) if numerical.any() else None,
        'boolean_result_pass_rate':float(passes[boolean].mean()) if boolean.any() else None,
        'result_given_true_operation_pass_rate':float(op.relative_pass(op_true,exact_results,boolean).mean()),
        'result_given_true_operands_pass_rate':float(op.relative_pass(correct_values,exact_results,boolean).mean()),
        'x_5pct_pass_rate':float(operand[:,0].mean()), 'y_5pct_pass_rate':float(operand[:,1].mean()),
        'joint_xy_5pct_pass_rate':float(operand.all(-1).mean()),
        'global_log_mae':float(log_err.mean()),'global_log_median_error':float(np.median(log_err)),
        'continuous_decoded_log_mae':float(np.abs(np.log1p(np.abs(estimated.astype(np.float64)))-np.log1p(np.abs(numbers.astype(np.float64)))).mean()),
        'source_result_5pct_pass_rate':float(legacy_passes.mean()),
        'source_numeric_result_5pct_pass_rate':float(legacy_passes[numerical].mean()) if numerical.any() else None,
        'source_joint_xy_5pct_pass_rate':float(legacy_operand.all(-1).mean()),
        'source_global_log_mae':float(np.abs(np.log1p(np.abs(legacy.astype(np.float64)))-np.log1p(np.abs(numbers.astype(np.float64)))).mean()),
        'source_boundary_snap_count':int(within_boundary.sum()),
        'continuous_boundary_snap_count':int(np.isin(np.abs(estimated),np.asarray(h.CUTS)).sum()),
        'families':{}, 'examples':[],
    }
    for family in sorted(set(families)):
        idx=np.asarray([f==family for f in families])
        data['families'][family]={'rows':int(idx.sum()),'result_pass':float(passes[idx].mean()),
                                 'source_result_pass':float(legacy_passes[idx].mean())}
    for j in range(min(8,len(numbers))):
        data['examples'].append({'true_x':int(numbers[j,0]),'true_y':int(numbers[j,1]),
                      'continuous_x':int(estimated[j,0]),'continuous_y':int(estimated[j,1]),
                      'source_x':int(legacy[j,0]),'source_y':int(legacy[j,1]),
                      'operation':predictions[j],'truth_result':float(exact_results[j]),
                      'continuous_result':float(results[j]),'source_result':float(legacy_results[j])})
    return data


def parser():
    p=op.parser()
    p.description=__doc__
    p.set_defaults(results_root=DEFAULT_ROOT, source_checkpoint=DEFAULT_SOURCE,
                   steps=3000, constraint_stage='continuity', continuity_weight=0)
    p.add_argument('--continuous-width',type=int,default=128)
    p.add_argument('--magnitude-weight',type=float,default=1.)
    p.add_argument('--sign-weight',type=float,default=.1)
    p.add_argument('--candidate-weight',type=float,default=.12)
    p.add_argument('--bootstrap-updates',type=int,default=200,
                   help='Direct magnitude/sign warm-start steps on copied replay before generating new examples')
    p.add_argument('--warmup-steps',type=int,default=200,
                   help='Ramp executed-result loss from zero to full weight')
    p.add_argument('--eval-checkpoint',choices=['latest','best','best-joint'],default='latest')
    return p


def config_for(args,manifest):
    config=op.make_config(args,manifest)
    config.update(schema=SCHEMA, objective='continuous_log1p_magnitude_result_cutover_v1',
                  continuous_width=args.continuous_width,
                  magnitude_weight=args.magnitude_weight, sign_weight=args.sign_weight,
                  candidate_weight=args.candidate_weight,warmup_steps=args.warmup_steps,
                  bootstrap_updates=args.bootstrap_updates,
                  result_weight=args.result_weight, source_schema=op.SCHEMA)
    return config


def save(path,network,optimizer,step,config,stream,replay,best,identity,source):
    state={'schema':SCHEMA,'step':step,'config':config,
           'network':network.state_dict(),'optimizer':optimizer.state_dict(),
           'random_stream':stream.state_dict(),'replay':replay.state_dict(),
           'best_validation':best,'backbone_identity':identity,'source_cutover':source,
           'torch_rng':torch.get_rng_state()}
    if torch.cuda.is_available():
        state['cuda_rng']=torch.cuda.get_rng_state_all()
    tmp=path.with_name(path.name+'.partial')
    torch.save(state,tmp);os.replace(tmp,path)


def restore(path,network,optimizer,stream,replay,config,identity):
    state=torch.load(path,map_location='cpu',weights_only=True)
    if state.get('schema')!=SCHEMA or state.get('config')!=config or state.get('backbone_identity')!=identity:
        raise RuntimeError('CONTINUOUS_RESUME_CONFIG_OR_IDENTITY_DRIFT')
    network.load_state_dict(state['network'])
    optimizer.load_state_dict(state['optimizer'])
    stream.load_state_dict(state['random_stream'])
    replay.load_state_dict(state['replay'])
    torch.set_rng_state(state['torch_rng'].cpu().to(torch.uint8))
    if torch.cuda.is_available() and 'cuda_rng' in state:
        torch.cuda.set_rng_state_all([x.cpu().to(torch.uint8) for x in state['cuda_rng']])
    return int(state['step']),dict(state['best_validation']),dict(state['source_cutover'])


def cutover(path,network,stream,replay,identity,args):
    raw=path.read_bytes()  # single read-only snapshot
    digest=hashlib.sha256(raw).hexdigest()
    state=torch.load(io.BytesIO(raw),map_location='cpu',weights_only=True)
    if state.get('schema')!=op.SCHEMA or state.get('backbone_identity')!=identity:
        raise RuntimeError('CONTINUOUS_BAD_SOURCE_OR_BACKBONE_IDENTITY')
    src=state.get('config',{})
    if src.get('constraint_stage')!=args.constraint_stage or src.get('rank')!=args.rank or \
       src.get('residual_scale')!=args.residual_scale or src.get('buffer_capacity')!=args.buffer_capacity or \
       src.get('op_width')!=args.op_width or src.get('operation_catalog')!=list(op.TRAIN_OPS) or \
       src.get('operation_programs')!=op.OP_TO_PROGRAM or \
       (src.get('sampler') is not None and src['sampler']!=args.sampler) or \
       (src.get('seed') is not None and src['seed']!=args.seed):
        raise RuntimeError('CONTINUOUS_CUTOVER_ARCHITECTURE_OR_CATALOG_DRIFT')
    network.source.load_state_dict(state['network'],strict=True)
    network.initialize_sign_zero()
    network.freeze_source()
    stream.load_state_dict(state['random_stream']);replay.load_state_dict(state['replay'])
    fork_seed=int(digest[:16],16)^int(args.seed)
    stream.rng.seed(fork_seed)
    if hasattr(stream,'bucket_pool'):stream.bucket_pool=[]
    return {'path':str(path),'sha256':digest,'source_step':int(state['step']),
            'source_seen_pairs':len(stream.seen),'source_best_validation':state.get('best_validation',{}),
            'fork_seed':fork_seed, 'source_schema':op.SCHEMA}


def main(argv=None):
    args=parser().parse_args(argv)
    if args.self_test:
        self_test();return 0
    if args.init_checkpoint:
        raise ValueError('CONTINUOUS_USE_--source-checkpoint')
    if (min(args.steps,args.batch_size,args.replay_updates,args.eval_every,args.checkpoint_every,
            args.report_every,args.continuous_width,args.threads,args.val_examples,args.buffer_capacity,
            args.per_expert_batch)<1 or args.warmup_steps<0 or args.bootstrap_updates<0 or
        min(args.magnitude_weight,args.sign_weight,args.result_weight,args.candidate_weight)<0 or
        args.magnitude_weight+args.sign_weight+args.result_weight+args.candidate_weight<=0):
        raise ValueError('CONTINUOUS_INVALID_ARGUMENTS')
    if args.constraint_stage!='continuity':
        raise ValueError('CONTINUOUS_EXPECTS_CONTINUITY_SOURCE')
    torch.set_num_threads(args.threads)
    if args.device=='cuda' and not torch.cuda.is_available():
        raise RuntimeError('CONTINUOUS_CUDA_UNAVAILABLE')
    device=torch.device(args.device)
    benchmark,tasks=previous.read_benchmark(args.v3_root)
    benchmark_root=args.v3_root.expanduser().resolve()
    identity=json.loads((benchmark_root/'model_identity.json').read_text(encoding='utf-8'))
    if identity.get('model')!='qwen':raise RuntimeError('CONTINUOUS_EXPECTS_QWEN')
    origin=args.source_checkpoint.expanduser().resolve()
    root=args.results_root.expanduser().resolve()
    if root==benchmark_root or benchmark_root in root.parents or root==origin.parent or \
        origin.parent in root.parents or root==origin or root in origin.parents:
        raise RuntimeError('CONTINUOUS_RESULTS_ROOT_COLLISION')
    config=config_for(args,benchmark)
    root.mkdir(parents=True,exist_ok=True)
    conf=root/'run_config.json'
    if conf.exists():
        if json.loads(conf.read_text(encoding='utf-8'))!=config:
            raise RuntimeError('CONTINUOUS_CONFIG_DRIFT')
    else:
        if any(root.iterdir()):
            raise RuntimeError('CONTINUOUS_NONEMPTY_RESULTS_ROOT')
        atomic_json(conf,config)
    torch.manual_seed(args.seed)
    net=ContinuousNumeracy(args.constraint_stage,args.rank,args.residual_scale,
                           args.op_width,args.continuous_width).to(device)
    optimizer=torch.optim.AdamW([v for v in net.parameters() if v.requires_grad],
                                lr=args.lr,weight_decay=args.weight_decay)
    stream=h.make_stream(args.seed,tasks,args.sampler)
    replay=op.OperationReplay(args.buffer_capacity,args.seed+17)
    latest=root/'latest.pt';best_result=root/'best.pt';best_joint=root/'best_joint.pt'
    best={'result':-1.,'joint':-1.,'result_step':None,'joint_step':None}
    step=0;source=None
    if args.resume or args.eval_only:
        check={'latest':latest,'best':best_result,'best-joint':best_joint}[args.eval_checkpoint] if args.eval_only else latest
        if not check.is_file():raise FileNotFoundError('CONTINUOUS_CHECKPOINT_MISSING')
        step,best,source=restore(check,net,optimizer,stream,replay,config,identity)
        emit('continuous_cutover_resumed',step=step,source=source,replay_records=len(replay.records))
    else:
        if latest.exists():raise RuntimeError('CONTINUOUS_USE_--resume')
        source=cutover(origin,net,stream,replay,identity,args)
        # Train only on copied TRAINING replay: no validation/test/control capture.
        for bootstrap_step in range(args.bootstrap_updates):
            feat,nums,operations=replay.sample_labeled(args.per_expert_batch)
            labels=torch.as_tensor(nums,dtype=torch.long,device=device)
            net.train()
            magnitude,signs,logits,_=net(feat.to(device=device,dtype=torch.float32))
            bootstrap_loss,_=continuous_loss(magnitude,signs,logits,labels,operations,
                magnitude_weight=args.magnitude_weight,sign_weight=args.sign_weight,
                result_weight=0.,candidate_weight=0.)
            optimizer.zero_grad(set_to_none=True); bootstrap_loss.backward()
            torch.nn.utils.clip_grad_norm_([p for p in net.parameters() if p.requires_grad],1.)
            optimizer.step()
        emit('continuous_cutover_bootstrap',updates=args.bootstrap_updates,
             training_records=len(replay.records),source_checkpoint_unchanged=True)
        emit('continuous_cutover_initialized',source=source,
             new_trainable_parameters=sum(p.numel() for p in net.parameters() if p.requires_grad),
             frozen_source_parameters=sum(p.numel() for p in net.source.parameters()),
             frozen_qwen_trainable_parameters=0, replay_records=len(replay.records),
             seen_pairs=len(stream.seen),source_unchanged=True)
        save(latest,net,optimizer,step,config,stream,replay,best,identity,source)
    if args.eval_only:
        for split in ('val','test','control'):
            ev=previous.load_eval(benchmark_root,benchmark,tasks,split)
            emit('continuous_cutover_final_evaluation',split=split,step=step,**evaluate(net,ev,device))
        return 0
    validation=previous.load_eval(benchmark_root,benchmark,tasks,'val',args.val_examples)
    if step==0:
        emit('continuous_cutover_bootstrap_validation',step=0,**evaluate(net,validation,device))
    import nanojev_numerate_spine_cassette_smoke as cassette
    _,frozen,tokenizer,source_device,info=cassette.load_source('qwen',args)
    if (info.get('resolved_commit')!=identity['model_info'].get('resolved_commit') or
        info.get('source')!=identity['model_info'].get('source') or
        str(info.get('dtype'))!=str(identity['model_info'].get('dtype'))):
        raise RuntimeError('CONTINUOUS_BACKBONE_IDENTITY_DRIFT')
    frozen.eval()
    previous.verify_capture_alignment(torch,frozen,tokenizer,source_device,benchmark_root,tasks,args.max_prompt_tokens)
    tic=time.monotonic()
    for k in range(step+1,args.steps+1):
        batch=stream.batch(args.batch_size)
        features=previous.extract_batched(torch,frozen,tokenizer,
                         [row['prompt'] for row in batch],source_device,args.max_prompt_tokens)
        if features.requires_grad:raise RuntimeError('CONTINUOUS_FROZEN_BACKBONE_HAS_GRAD')
        replay.add(features,batch)
        for _ in range(args.replay_updates):
            feature_cpu,numbers,operations=replay.sample_labeled(args.per_expert_batch)
            labels=torch.as_tensor(numbers,dtype=torch.long,device=device)
            net.train()
            magnitude,signs,logits,_=net(feature_cpu.to(device=device,dtype=torch.float32))
            ramp=min(1.,k/max(1,args.warmup_steps)) if args.warmup_steps>0 else 1.
            loss,components=continuous_loss(magnitude,signs,logits,labels,operations,
                          magnitude_weight=args.magnitude_weight,sign_weight=args.sign_weight,
                          result_weight=args.result_weight,candidate_weight=args.candidate_weight,
                          temperature=args.op_temperature,result_ramp=ramp)
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_([p for p in net.parameters() if p.requires_grad],1.)
            optimizer.step()
        if k%args.report_every==0 or k==args.steps:
            emit('continuous_cutover_train_progress',step=k,source_step=source['source_step'],
                 new_pairs=len(stream.seen)-source['source_seen_pairs'],seen_pairs=len(stream.seen),
                 replay_records=len(replay.records),loss=float(loss.detach()),
                 **{name:float(x) for name,x in components.items()},elapsed_seconds=round(time.monotonic()-tic,2))
        if k%args.eval_every==0 or k==args.steps:
            metrics=evaluate(net,validation,device)
            emit('continuous_cutover_validation',step=k,**metrics)
            result=metrics['numeric_result_5pct_pass_rate']
            joint=metrics['joint_xy_5pct_pass_rate']
            if result is not None and result>best['result']:
                best.update(result=result,result_step=k)
                save(best_result,net,optimizer,k,config,stream,replay,best,identity,source)
                emit('continuous_cutover_best_result',step=k,numeric_result=result,path=str(best_result))
            if joint>best['joint']:
                best.update(joint=joint,joint_step=k)
                save(best_joint,net,optimizer,k,config,stream,replay,best,identity,source)
                emit('continuous_cutover_best_joint',step=k,joint=joint,path=str(best_joint))
        if k%args.checkpoint_every==0 or k==args.steps:
            save(latest,net,optimizer,k,config,stream,replay,best,identity,source)
            emit('continuous_cutover_checkpoint',step=k,path=str(latest))
    emit('continuous_cutover_complete',step=args.steps,best=best,root=str(root))
    return 0


def self_test():
    net=ContinuousNumeracy(width=8,op_width=8)
    numbers=torch.tensor([[3847,2916],[-3,4],[7,-1],[0,0]])
    x=torch.randn(4,h.INPUT_WIDTH)
    raw,sign,ops,_=net(x)
    loss,_=continuous_loss(raw,sign,ops,numbers,
        ['sym_multiply','sym_sub','conditional_gt','sym_add'],result_weight=1.)
    loss.backward()
    assert torch.isfinite(loss)
    assert net.magnitude[1].weight.grad is not None
    assert net.magnitude[-1].bias.grad.abs().sum()>0
    assert all(v.grad is None for v in net.source.parameters())
    emit('continuous_cutover_self_test_pass',global_log_magnitude=True,
         no_bucket_clamps=True,result_gradients_to_continuous_decoder=True)


if __name__=='__main__':
    raise SystemExit(main())
