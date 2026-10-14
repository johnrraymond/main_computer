#!/usr/bin/env python3
"""NanoJev v2: zero-initialized continuous residual forked from v1.2.

Run --branch direct and --branch result separately under the same --results-root.
Both import the exact same immutable v1.2 checkpoint, replay and forked RNG.
The frozen legacy decoder and operation classifier are never updated.
"""
from __future__ import annotations

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

import nanojev_register_continuous_magnitude_cutover as v1

op, h, c, previous = v1.op, v1.h, v1.c, v1.previous
emit, atomic_json = v1.emit, v1.atomic_json
SCHEMA = 'nanojev.continuous-residual-magnitude.v2'
DEFAULT_ROOT = Path.home() / 'NanoJev' / 'runs' / 'register_continuous_residual_magnitude_v2'
DEFAULT_SOURCE = op.DEFAULT_ROOT / 'latest.pt'


class ResidualNumeracy(nn.Module):
    """Frozen v1.2 arithmetic + signed-asinh continuous correction.

    Unlike a log(abs(base)) correction gated on the legacy sign, signed-asinh
    works even where legacy predicts zero or gets the sign wrong. Its exact
    zero-residual reconstruction equals legacy inference (not a soft oracle
    route), and result gradients pass into BOTH trainable residual registers.
    """
    def __init__(self, stage='continuity', rank=8, residual_scale=.25,
                 op_width=96, residual_width=128):
        super().__init__()
        if residual_width < 1:
            raise ValueError('RESIDUAL_WIDTH_INVALID')
        self.source = op.ExecutableNumeracy(stage, rank, residual_scale, op_width)
        self.residual = nn.Sequential(
            nn.LayerNorm(h.INPUT_WIDTH, elementwise_affine=False),
            nn.Linear(h.INPUT_WIDTH, residual_width), nn.GELU(),
            nn.Linear(residual_width, 2),
        )
        nn.init.zeros_(self.residual[-1].weight)
        nn.init.zeros_(self.residual[-1].bias)
        self.freeze_source()

    def freeze_source(self):
        for p in self.source.parameters():
            p.requires_grad_(False)
        self.source.eval()

    def train(self, mode=True):
        super().train(mode)
        self.source.eval()
        return self

    def forward(self, features):
        with torch.no_grad():
            raw, op_logits = self.source(features)
            # Crucial: baseline values match the ACTUAL v1.2 hard inference,
            # not teacher-routed or soft-bucket surrogate operands.
            integers = c.decode(raw.float().detach().cpu().numpy())
            base = torch.as_tensor(integers, dtype=torch.float64, device=features.device)
        delta = self.residual(features.float()).double()
        corrected = torch.sinh(torch.asinh(base) + delta)
        if not torch.isfinite(corrected).all():
            raise RuntimeError('RESIDUAL_NONFINITE_PREDICTION')
        return corrected, base, delta, op_logits


def decode_int(predictions):
    if not torch.isfinite(predictions).all() or (predictions.abs() >= 9e18).any():
        raise RuntimeError('RESIDUAL_OUT_OF_INTEGER_RANGE')
    return torch.round(predictions).to(torch.int64)


def residual_loss(predicted, base, delta, op_logits, labels, operations, *,
                  branch, magnitude_weight=1., result_weight=.12,
                  candidate_weight=.12, anchor_weight=.01,
                  result_ramp=1., temperature=1.):
    if branch not in ('direct','result'):
        raise ValueError('RESIDUAL_BRANCH_INVALID')
    if any(name not in op.OP_ID for name in operations):
        raise ValueError('RESIDUAL_UNSUPPORTED_TRAIN_OPERATION')
    if temperature <= 0:
        raise ValueError('RESIDUAL_INVALID_TEMPERATURE')
    truth = labels.double()
    target_delta = (torch.asinh(truth) - torch.asinh(base)).detach()
    # Additive residual in continuous signed-log space, well-conditioned over
    # 0 to +/-2M; this also permits smooth crossing of zero and sign changes.
    direct = F.smooth_l1_loss(delta, target_delta, beta=.05)
    anchor = delta.square().mean()
    oracle = predicted.new_zeros(())
    candidate = predicted.new_zeros(())
    if branch == 'result':
        programs = [op.OP_TO_PROGRAM[name] for name in operations]
        true_results = torch.tensor(
            [op.exact_execute(program, int(x), int(y))
             for program,(x,y) in zip(programs,labels.tolist())],
            device=labels.device, dtype=torch.float32)
        losses = op._per_candidate_losses(predicted, labels, true_results, programs)
        ids = torch.tensor([op.OP_ID[name] for name in operations],
                           dtype=torch.long, device=labels.device)
        oracle = losses.gather(1, ids[:,None]).mean()
        weights = F.softmax(op_logits.float()/temperature, dim=-1).detach()
        candidate = (weights * losses).sum(-1).mean()
    extra = 0 if branch == 'direct' else result_ramp * (result_weight*oracle + candidate_weight*candidate)
    total = magnitude_weight*direct + anchor_weight*anchor + extra
    if not torch.isfinite(total):
        raise RuntimeError('RESIDUAL_NONFINITE_LOSS')
    return total, {'direct': direct.detach(), 'anchor':anchor.detach(),
                   'oracle_result':oracle.detach(),'candidate_result':candidate.detach(),
                   'signed_asinh_mae':(torch.asinh(predicted)-torch.asinh(truth)).abs().mean().detach(),
                   'result_ramp':result_ramp,'total':total.detach()}


def predict_batch(network, features, device):
    old = network.training
    network.eval()
    with torch.inference_mode():
        tensor=torch.as_tensor(features,dtype=torch.float32,device=device)
        corrected,base,_,logits=network(tensor)
        nums=decode_int(corrected).cpu().numpy()
        probs=F.softmax(logits.float(),-1).cpu().numpy()
    network.train(old)
    outputs=[]
    for (x,y),p in zip(nums,probs):
        index=int(p.argmax());name=op.TRAIN_OPS[index]
        program=op.OP_TO_PROGRAM[name]
        outputs.append({'x':int(x),'y':int(y),'operation':name,
                        'program':program,'operation_probability':float(p[index]),
                        'result':op.exact_execute(program,x,y)})
    return outputs


def evaluate(network, evaluation, device, batch_size=256):
    features,numbers,operations,families=evaluation
    predicted=[];baseline=[];ids=[];signed_log_err=[]
    original=network.training
    network.eval()
    with torch.inference_mode():
        for i in range(0,len(features),batch_size):
            features_batch=torch.as_tensor(features[i:i+batch_size],dtype=torch.float32,device=device)
            corr,base,delta,logits=network(features_batch)
            predicted.append(decode_int(corr).cpu().numpy())
            baseline.append(base.to(torch.int64).cpu().numpy())
            ids.extend(logits.float().argmax(-1).cpu().numpy().tolist())
            truth=torch.as_tensor(numbers[i:i+batch_size],dtype=torch.float64,device=device)
            signed_log_err.append((torch.asinh(corr)-torch.asinh(truth)).abs().cpu().numpy())
    network.train(original)
    revised=np.concatenate(predicted)
    source=np.concatenate(baseline)
    labels=np.asarray(numbers,dtype=np.int64)
    chosen=[op.TRAIN_OPS[j] for j in ids]
    programs=[op.OP_TO_PROGRAM[k] for k in chosen]
    real_programs=[op.OP_TO_PROGRAM[k] for k in operations]
    target=np.array([op.exact_execute(p,int(x),int(y)) for p,(x,y) in zip(real_programs,labels)],dtype=np.float64)
    boolean=np.asarray([p in op.BOOL_PROGRAMS for p in real_programs],dtype=bool)
    numeric=~boolean
    def score(values):
        results=[op.exact_execute(p,int(x),int(y)) for p,(x,y) in zip(programs,values)]
        passed=op.relative_pass(results,target,boolean)
        correct=previous.correctness(values,labels)
        return results,passed,correct
    results,pass_rate,joint=score(revised)
    source_results,source_pass,source_joint=score(source)
    log_err=np.concatenate(signed_log_err)
    boundaries=np.asarray(h.CUTS)
    assert len(results)==len(operations)
    known=np.asarray([name in op.OP_ID for name in operations])
    metrics={
        'rows':len(labels),
        'operation_template_accuracy':float(np.mean(np.asarray(chosen)[known]==np.asarray(operations)[known])) if known.any() else None,
        'operation_program_accuracy':float(np.mean(np.asarray(programs)[known]==np.asarray(real_programs)[known])) if known.any() else None,
        'unsupported_heldout_operation_rows':int((~known).sum()),
        'numeric_result_rows':int(numeric.sum()),
        'result_5pct_pass_rate':float(pass_rate.mean()),
        'numeric_result_5pct_pass_rate':float(pass_rate[numeric].mean()) if numeric.any() else None,
        'boolean_result_pass_rate':float(pass_rate[boolean].mean()) if boolean.any() else None,
        'joint_xy_5pct_pass_rate':float(joint.all(-1).mean()),
        'x_5pct_pass_rate':float(joint[:,0].mean()),
        'y_5pct_pass_rate':float(joint[:,1].mean()),
        'signed_asinh_mae':float(log_err.mean()),
        'global_log_mae':float(np.abs(np.log1p(np.abs(revised.astype(np.float64)))-np.log1p(np.abs(labels.astype(np.float64)))).mean()),
        'boundary_snap_count':int(np.isin(np.abs(revised),boundaries).sum()),
        'source_result_5pct_pass_rate':float(source_pass.mean()),
        'source_numeric_result_5pct_pass_rate':float(source_pass[numeric].mean()) if numeric.any() else None,
        'source_joint_xy_5pct_pass_rate':float(source_joint.all(-1).mean()),
        'source_boundary_snap_count':int(np.isin(np.abs(source),boundaries).sum()),
        'source_global_log_mae':float(np.abs(np.log1p(np.abs(source.astype(np.float64)))-np.log1p(np.abs(labels.astype(np.float64)))).mean()),
        'families':{},'examples':[],
    }
    for family in sorted(set(families)):
        mask=np.asarray([f==family for f in families])
        metrics['families'][family]={
            'rows':int(mask.sum()),'numeric_result_pass':float(pass_rate[mask & numeric].mean()) if (mask & numeric).any() else None,
            'source_numeric_result_pass':float(source_pass[mask & numeric].mean()) if (mask & numeric).any() else None,
            'result_pass':float(pass_rate[mask].mean()),'source_result_pass':float(source_pass[mask].mean()),
        }
    for j in range(min(8,len(labels))):
        metrics['examples'].append({
            'true_x':int(labels[j,0]),'true_y':int(labels[j,1]),
            'residual_x':int(revised[j,0]),'residual_y':int(revised[j,1]),
            'source_x':int(source[j,0]),'source_y':int(source[j,1]),
            'operation':chosen[j], 'truth_result':float(target[j]),
            'residual_result':float(results[j]),'source_result':float(source_results[j]),
        })
    return metrics


def parser():
    p=op.parser()
    p.description=__doc__
    p.set_defaults(results_root=DEFAULT_ROOT,source_checkpoint=DEFAULT_SOURCE,
                   constraint_stage='continuity',continuity_weight=0,
                   steps=3000)
    p.add_argument('--branch',choices=['direct','result'],required=False,default='direct')
    p.add_argument('--residual-width',type=int,default=128)
    p.add_argument('--magnitude-weight',type=float,default=1.)
    p.add_argument('--anchor-weight',type=float,default=.01)
    p.add_argument('--candidate-weight',type=float,default=.12)
    p.add_argument('--warmup-steps',type=int,default=200)
    p.add_argument('--eval-checkpoint',choices=['latest','best','best-joint'],default='latest')
    return p


def config_for(args,benchmark):
    config=op.make_config(args,benchmark)
    config.update(schema=SCHEMA,objective='zero-initialized-signed-asinh-residual',
                  residual_width=args.residual_width,branch=args.branch,
                  magnitude_weight=args.magnitude_weight,anchor_weight=args.anchor_weight,
                  candidate_weight=args.candidate_weight,warmup_steps=args.warmup_steps,
                  result_weight=args.result_weight,source_schema=op.SCHEMA)
    return config


def save(path,net,optimizer,step,config,stream,replay,best,identity,source):
    ck={'schema':SCHEMA,'step':step,'config':config,'network':net.state_dict(),
        'optimizer':optimizer.state_dict(),'random_stream':stream.state_dict(),
        'replay':replay.state_dict(),'best_validation':best,
        'backbone_identity':identity,'source_cutover':source,
        'torch_rng':torch.get_rng_state()}
    if torch.cuda.is_available():ck['cuda_rng']=torch.cuda.get_rng_state_all()
    tmp=path.with_name(path.name+'.partial')
    torch.save(ck,tmp);os.replace(tmp,path)


def restore(path,net,optimizer,stream,replay,config,identity):
    state=torch.load(path,map_location='cpu',weights_only=True)
    if state.get('schema')!=SCHEMA or state.get('config')!=config or state.get('backbone_identity')!=identity:
        raise RuntimeError('RESIDUAL_RESUME_CONFIG_OR_IDENTITY_DRIFT')
    net.load_state_dict(state['network'],strict=True)
    optimizer.load_state_dict(state['optimizer'])
    stream.load_state_dict(state['random_stream'])
    replay.load_state_dict(state['replay'])
    torch.set_rng_state(state['torch_rng'].cpu().to(torch.uint8))
    if torch.cuda.is_available() and 'cuda_rng' in state:
        torch.cuda.set_rng_state_all([x.cpu().to(torch.uint8) for x in state['cuda_rng']])
    return int(state['step']),dict(state['best_validation']),dict(state['source_cutover'])


def cutover(path,net,stream,replay,identity,args):
    """Read a v1.2 checkpoint once; keep every source byte untouched."""
    data=path.read_bytes()
    digest=hashlib.sha256(data).hexdigest()
    state=torch.load(io.BytesIO(data),map_location='cpu',weights_only=True)
    if state.get('schema')!=op.SCHEMA or state.get('backbone_identity')!=identity:
        raise RuntimeError('RESIDUAL_BAD_SOURCE_OR_BACKBONE_IDENTITY')
    src=state.get('config',{})
    if (src.get('constraint_stage')!=args.constraint_stage or
        src.get('rank')!=args.rank or
        src.get('residual_scale')!=args.residual_scale or
        src.get('buffer_capacity')!=args.buffer_capacity or
        src.get('op_width')!=args.op_width or
        src.get('operation_catalog')!=list(op.TRAIN_OPS) or
        src.get('operation_programs')!=op.OP_TO_PROGRAM or
        (src.get('sampler') is not None and src['sampler']!=args.sampler) or
        (src.get('seed') is not None and src['seed']!=args.seed)):
        raise RuntimeError('RESIDUAL_SOURCE_ARCHITECTURE_OR_CATALOG_DRIFT')
    net.source.load_state_dict(state['network'],strict=True)
    net.freeze_source()
    stream.load_state_dict(state['random_stream'])
    replay.load_state_dict(state['replay'])
    fork_seed=int(digest[:16],16)^int(args.seed)
    stream.rng.seed(fork_seed)
    if hasattr(stream,'bucket_pool'):stream.bucket_pool=[]
    return {'path':str(path),'sha256':digest,'source_step':int(state['step']),
            'source_seen_pairs':len(stream.seen),
            'source_best_validation':state.get('best_validation',{}),
            'fork_seed':fork_seed,'source_schema':op.SCHEMA}


def main(argv=None):
    args=parser().parse_args(argv)
    if args.self_test:
        self_test();return 0
    if args.init_checkpoint:
        raise ValueError('RESIDUAL_USE_SOURCE_CHECKPOINT')
    if args.constraint_stage!='continuity':
        raise ValueError('RESIDUAL_REQUIRES_CONTINUITY_CHECKPOINT')
    if (min(args.steps,args.batch_size,args.replay_updates,args.eval_every,args.checkpoint_every,
            args.report_every,args.residual_width,args.threads,args.val_examples,args.buffer_capacity,
            args.per_expert_batch)<1 or args.warmup_steps<0 or
        min(args.magnitude_weight,args.anchor_weight,args.result_weight,args.candidate_weight)<0 or
        args.magnitude_weight+args.anchor_weight<=0 or args.op_temperature<=0):
        raise ValueError('RESIDUAL_INVALID_ARGUMENTS')
    torch.set_num_threads(args.threads)
    if args.device=='cuda' and not torch.cuda.is_available():
        raise RuntimeError('RESIDUAL_CUDA_UNAVAILABLE')
    device=torch.device(args.device)
    benchmark,tasks=previous.read_benchmark(args.v3_root)
    benchmark_root=args.v3_root.expanduser().resolve()
    identity=json.loads((benchmark_root/'model_identity.json').read_text(encoding='utf-8'))
    if identity.get('model')!='qwen':
        raise RuntimeError('RESIDUAL_EXPECTS_FROZEN_QWEN')
    origin=args.source_checkpoint.expanduser().resolve()
    root=(args.results_root.expanduser().resolve()/args.branch)
    if (root==benchmark_root or benchmark_root in root.parents or
        root==origin.parent or origin.parent in root.parents or
        root==origin or root in origin.parents):
        raise RuntimeError('RESIDUAL_OUTPUT_COLLIDES_WITH_SOURCE')
    config=config_for(args,benchmark)
    root.mkdir(parents=True,exist_ok=True)
    conf=root/'run_config.json'
    if conf.exists():
        if json.loads(conf.read_text(encoding='utf-8'))!=config:
            raise RuntimeError('RESIDUAL_CONFIG_DRIFT')
    else:
        if any(root.iterdir()):
            raise RuntimeError('RESIDUAL_NONEMPTY_RESULTS_ROOT')
        atomic_json(conf,config)
    torch.manual_seed(args.seed)
    net=ResidualNumeracy(args.constraint_stage,args.rank,args.residual_scale,
                        args.op_width,args.residual_width).to(device)
    optimizer=torch.optim.AdamW(net.residual.parameters(),lr=args.lr,weight_decay=args.weight_decay)
    stream=h.make_stream(args.seed,tasks,args.sampler)
    replay=op.OperationReplay(args.buffer_capacity,args.seed+17)
    latest=root/'latest.pt';best_result=root/'best.pt';best_joint=root/'best_joint.pt'
    best={'result':-1.,'joint':-1.,'result_step':None,'joint_step':None}
    step=0
    if args.resume or args.eval_only:
        check={'latest':latest,'best':best_result,'best-joint':best_joint}[args.eval_checkpoint] if args.eval_only else latest
        if not check.exists():
            raise FileNotFoundError('RESIDUAL_CHECKPOINT_MISSING')
        step,best,source=restore(check,net,optimizer,stream,replay,config,identity)
        emit('residual_cutover_resumed',branch=args.branch,step=step,source=source)
    else:
        if latest.exists():
            raise RuntimeError('RESIDUAL_USE_--resume')
        source=cutover(origin,net,stream,replay,identity,args)
        net.freeze_source()
        emit('residual_cutover_initialized',branch=args.branch,source=source,
             trainable_parameters=sum(p.numel() for p in net.parameters() if p.requires_grad),
             source_parameters=sum(p.numel() for p in net.source.parameters()),
             frozen_qwen_trainable_parameters=0,replay_records=len(replay.records),
             seen_pairs=len(stream.seen),source_unchanged=True)
        save(latest,net,optimizer,0,config,stream,replay,best,identity,source)
    if args.eval_only:
        for split in ('val','test','control'):
            data=previous.load_eval(benchmark_root,benchmark,tasks,split)
            emit('residual_cutover_final_evaluation',branch=args.branch,split=split,
                 step=step,**evaluate(net,data,device))
        return 0
    validation=previous.load_eval(benchmark_root,benchmark,tasks,'val',args.val_examples)
    if step==0:
        baseline=evaluate(net,validation,device)
        if (baseline['result_5pct_pass_rate']!=baseline['source_result_5pct_pass_rate'] or
            baseline['joint_xy_5pct_pass_rate']!=baseline['source_joint_xy_5pct_pass_rate'] or
            baseline['boundary_snap_count']!=baseline['source_boundary_snap_count'] or
            baseline['numeric_result_5pct_pass_rate']!=baseline['source_numeric_result_5pct_pass_rate']):
            raise RuntimeError('RESIDUAL_STEP_ZERO_MISMATCH')
        # The inherited model is a real checkpoint candidate, not a disposable
        # warm-start. Keep it as both bests until genuinely surpassed.
        best.update(result=baseline['numeric_result_5pct_pass_rate'],
                    joint=baseline['joint_xy_5pct_pass_rate'],
                    result_step=0,joint_step=0)
        save(best_result,net,optimizer,0,config,stream,replay,best,identity,source)
        save(best_joint,net,optimizer,0,config,stream,replay,best,identity,source)
        save(latest,net,optimizer,0,config,stream,replay,best,identity,source)
        emit('residual_cutover_step_zero',branch=args.branch,step=0,**baseline)
    import nanojev_numerate_spine_cassette_smoke as cassette
    _,frozen,tokenizer,source_device,info=cassette.load_source('qwen',args)
    if (info.get('resolved_commit')!=identity['model_info'].get('resolved_commit') or
        info.get('source')!=identity['model_info'].get('source') or
        str(info.get('dtype'))!=str(identity['model_info'].get('dtype'))):
        raise RuntimeError('RESIDUAL_BACKBONE_IDENTITY_DRIFT')
    frozen.eval()
    previous.verify_capture_alignment(torch,frozen,tokenizer,source_device,benchmark_root,tasks,args.max_prompt_tokens)
    tic=time.monotonic()
    for k in range(step+1,args.steps+1):
        batch=stream.batch(args.batch_size)
        features=previous.extract_batched(torch,frozen,tokenizer,[row['prompt'] for row in batch],
                                          source_device,args.max_prompt_tokens)
        if features.requires_grad:
            raise RuntimeError('RESIDUAL_FROZEN_BACKBONE_HAS_GRAD')
        replay.add(features,batch)
        for _ in range(args.replay_updates):
            feats,nums,ops_list=replay.sample_labeled(args.per_expert_batch)
            labels=torch.as_tensor(nums,dtype=torch.long,device=device)
            net.train()
            pred,base,delta,op_logits=net(feats.to(device=device,dtype=torch.float32))
            ramp=min(1.,k/max(args.warmup_steps,1)) if args.warmup_steps else 1.
            loss,parts=residual_loss(pred,base,delta,op_logits,labels,ops_list,branch=args.branch,
                  magnitude_weight=args.magnitude_weight,result_weight=args.result_weight,
                  candidate_weight=args.candidate_weight,anchor_weight=args.anchor_weight,
                  result_ramp=ramp,temperature=args.op_temperature)
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(net.residual.parameters(),1.)
            optimizer.step()
        if k%args.report_every==0 or k==args.steps:
            emit('residual_cutover_train_progress',branch=args.branch,step=k,
                 source_step=source['source_step'],new_pairs=len(stream.seen)-source['source_seen_pairs'],
                 seen_pairs=len(stream.seen),replay_records=len(replay.records),
                 loss=float(loss.detach()),**{key:float(val) for key,val in parts.items()},
                 elapsed_seconds=round(time.monotonic()-tic,2))
        if k%args.eval_every==0 or k==args.steps:
            metrics=evaluate(net,validation,device)
            emit('residual_cutover_validation',branch=args.branch,step=k,**metrics)
            result=metrics['numeric_result_5pct_pass_rate']
            joint=metrics['joint_xy_5pct_pass_rate']
            if result is not None and result>best['result']:
                best.update(result=result,result_step=k)
                save(best_result,net,optimizer,k,config,stream,replay,best,identity,source)
                emit('residual_cutover_best_result',branch=args.branch,step=k,value=result,path=str(best_result))
            if joint>best['joint']:
                best.update(joint=joint,joint_step=k)
                save(best_joint,net,optimizer,k,config,stream,replay,best,identity,source)
                emit('residual_cutover_best_joint',branch=args.branch,step=k,value=joint,path=str(best_joint))
        if k%args.checkpoint_every==0 or k==args.steps:
            save(latest,net,optimizer,k,config,stream,replay,best,identity,source)
            emit('residual_cutover_checkpoint',branch=args.branch,step=k,path=str(latest))
    emit('residual_cutover_complete',branch=args.branch,step=args.steps,best=best,root=str(root))
    return 0


def self_test():
    torch.manual_seed(12)
    net=ResidualNumeracy(op_width=8,residual_width=8)
    x=torch.randn(3,h.INPUT_WIDTH)
    pred,base,delta,op_logits=net(x)
    assert torch.equal(decode_int(pred),base.to(torch.int64))
    assert torch.all(delta==0)
    # Exact arithmetic result gradient, including the zero/sign crossing.
    pred,base,delta,ops=net(x)
    labels=torch.tensor([[125000,30],[-10,20],[0,-15]],dtype=torch.long)
    loss,terms=residual_loss(pred,base,delta,ops,labels,
          ['written_product','written_sub','conditional_min'],branch='result',
          magnitude_weight=0,anchor_weight=0,result_weight=1,candidate_weight=1)
    loss.backward()
    assert torch.isfinite(loss)
    assert net.residual[-1].bias.grad.abs().sum()>0
    assert all(p.grad is None for p in net.source.parameters())
    emit('residual_cutover_self_test_pass',zero_step_exact=True,
         source_frozen=True,executable_result_backprop=True)


if __name__=='__main__':
    raise SystemExit(main())
