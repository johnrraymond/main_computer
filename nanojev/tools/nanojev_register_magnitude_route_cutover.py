#!/usr/bin/env python3
"""NanoJev v3: route-aware, zero-initialized continuous magnitude correction.

Uses 38-head source logits, twelve signed expert candidates and Qwen features.
Auxiliary supervision targets severe source bucket/sign/zero routing failures.
Independent matched direct/result branches; source and v2 remain immutable.
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

import nanojev_register_continuous_residual_cutover as v2
v1 = v2.v1

op, h, c, previous = v1.op, v1.h, v1.c, v1.previous
emit, atomic_json = v1.emit, v1.atomic_json
SCHEMA = 'nanojev.magnitude-route-cutover.v3'
DEFAULT_ROOT = Path.home() / 'NanoJev' / 'runs' / 'register_magnitude_route_cutover_v3'
DEFAULT_SOURCE = op.DEFAULT_ROOT / 'latest.pt'


class RouteAwareNumeracy(nn.Module):
    """Frozen v1.2 decoder plus trainable route posterior and signed-asinh jump.

    The correction is fine(h, raw) + gate(h, raw)*(E_route[asinh(candidate)]
    - asinh(source_number)). All three new heads have zero-initialized output
    projections. Consequently forward() equals source hard inference exactly at
    initialization, even for zero/negative values and misrouted magnitudes.
    """
    def __init__(self, stage='continuity', rank=8, residual_scale=.25,
                 op_width=96, route_width=128):
        super().__init__()
        if route_width < 1:
            raise ValueError('ROUTE_WIDTH_INVALID')
        self.source = op.ExecutableNumeracy(stage, rank, residual_scale, op_width)
        self.trunk = nn.Sequential(
            nn.LayerNorm(h.INPUT_WIDTH+2*h.HEADS_PER_REGISTER, elementwise_affine=False),
            nn.Linear(h.INPUT_WIDTH+2*h.HEADS_PER_REGISTER,route_width),
            nn.GELU(),
        )
        self.fine = nn.Linear(route_width,2)
        self.gate = nn.Linear(route_width,2)
        self.route = nn.Linear(route_width,2*13)
        for head in (self.fine,self.gate,self.route):
            nn.init.zeros_(head.weight)
            nn.init.zeros_(head.bias)
        self.freeze_source()

    def freeze_source(self):
        for p in self.source.parameters():
            p.requires_grad_(False)
        self.source.eval()

    def train(self,mode=True):
        super().train(mode)
        self.source.eval()
        return self

    @staticmethod
    def route_classes(values):
        """zero=0; nonzero classes 1+2*band+[0 negative,1 positive]."""
        vals = values.long()
        cuts=torch.tensor(h.CUTS,dtype=vals.dtype,device=vals.device)
        bands=torch.bucketize(vals.abs().contiguous(),cuts,right=False)
        classes=1+2*bands+(vals>0).long()
        return torch.where(vals==0,torch.zeros_like(classes),classes)

    @staticmethod
    def prior_route_logits(raw):
        """Categorical sign/zero/band prior from source's five survival gates."""
        z=raw.float().reshape(-1,2,h.HEADS_PER_REGISTER)
        logzero=F.logsigmoid(z[:,:,0:1])
        lognonzero=F.logsigmoid(-z[:,:,0:1])
        signs=torch.stack((F.logsigmoid(-z[:,:,1]),F.logsigmoid(z[:,:,1])),dim=-1)
        cont=F.logsigmoid(z[:,:,2:7])
        stop=F.logsigmoid(-z[:,:,2:7])
        start=torch.cat((torch.zeros_like(cont[:,:,:1]),cont.cumsum(-1)[:,:,:-1]),dim=-1)
        band=torch.cat((start+stop,cont.sum(-1,keepdim=True)),dim=-1)
        signed=(lognonzero[:,:,:,None]+band[:,:,:,None]+signs[:,:,None,:]).reshape(-1,2,12)
        return torch.cat((logzero,signed),dim=-1)

    @staticmethod
    def expert_candidates(raw):
        """Candidate signed-asinh values from EVERY frozen signed expert.

        Decodes using the source expert ranges, not an oracle band. Candidate
        values are detached (source stays frozen), and classification can select
        a different band without clipping the correction at its old boundary.
        """
        z=raw.double().reshape(-1,2,h.HEADS_PER_REGISTER)
        coords=z[:,:,7:].reshape(-1,2,6,2)
        fraction=((coords+1)*.5).clamp(0,1)
        lower=torch.tensor([h.band_limits(i)[0] for i in range(6)],device=z.device,dtype=z.dtype)
        upper=torch.tensor([h.band_limits(i)[1] for i in range(6)],device=z.device,dtype=z.dtype)
        scale=torch.log1p(lower)[None,None,:,None]+fraction*(torch.log1p(upper)-torch.log1p(lower))[None,None,:,None]
        magnitude=torch.expm1(scale)
        # Original regime 0 uses linear rather than logarithmic scaling.
        magnitude[:,:,0,:]=lower[0]+fraction[:,:,0,:]*(upper[0]-lower[0])
        signs=torch.tensor([-1.,1.],device=z.device,dtype=z.dtype)
        candidates=torch.asinh(magnitude)*signs[None,None,None,:]
        return torch.cat((torch.zeros_like(candidates[:,:,0,:1]),candidates.reshape(-1,2,12)),dim=-1)

    def forward_with_route(self,features):
        with torch.no_grad():
            raw, op_logits = self.source(features)
            old=c.decode(raw.float().detach().cpu().numpy())
            base=torch.as_tensor(old,dtype=torch.float64,device=features.device)
            prior=self.prior_route_logits(raw)
            candidates=self.expert_candidates(raw)
        signal=torch.cat((F.layer_norm(features.float(),(h.INPUT_WIDTH,)),
                          torch.tanh(raw.float().detach())),dim=-1)
        hidden=self.trunk(signal)
        fine=self.fine(hidden).double()
        gate_logits=self.gate(hidden).double()
        gate=torch.tanh(gate_logits)
        route_logits=prior.double()+self.route(hidden).reshape(-1,2,13).double()
        route_probs=route_logits.softmax(-1)
        expectation=(route_probs*candidates).sum(-1)
        baseline=torch.asinh(base)
        delta=fine+gate*(expectation-baseline)
        corrected=torch.sinh(baseline+delta)
        if not torch.isfinite(corrected).all():
            raise RuntimeError('ROUTE_NONFINITE_PREDICTION')
        aux={'route_logits':route_logits,'prior_logits':prior.double(),
             'route_probabilities':route_probs,'gate':gate,
             'gate_logits':gate_logits,'base_route_classes':self.route_classes(base),
             'candidate_asinh':candidates,'fine':fine}
        return corrected,base,delta,op_logits,aux

    def forward(self,features):
        corrected,base,delta,op_logits,_=self.forward_with_route(features)
        return corrected,base,delta,op_logits



def decode_int(predictions):
    if not torch.isfinite(predictions).all() or (predictions.abs() >= 9e18).any():
        raise RuntimeError('ROUTE_OUT_OF_INTEGER_RANGE')
    return torch.round(predictions).to(torch.int64)


def route_loss(predicted,base,delta,op_logits,aux,labels,operations,*,
               branch,magnitude_weight=1.,result_weight=.12,candidate_weight=.12,
               anchor_weight=.01,route_weight=.35,wrong_route_weight=5.,
               jump_weight=1.,gate_weight=.1,result_ramp=1.,temperature=1.):
    """v2 operand/executable losses + explicitly supervised wrong-route repair.

    No truth enters forward() or inference; true class enters training-only CE.
    'wrong route' uses frozen decoded source values, not learned residual values.
    """
    core,parts=v2.residual_loss(predicted,base,delta,op_logits,labels,operations,
                    branch=branch,magnitude_weight=magnitude_weight,
                    result_weight=result_weight,candidate_weight=candidate_weight,
                    anchor_weight=anchor_weight,result_ramp=result_ramp,
                    temperature=temperature)
    label_class=RouteAwareNumeracy.route_classes(labels)
    missed=label_class!=aux['base_route_classes']
    weights=torch.where(missed,torch.full_like(label_class,float(wrong_route_weight),dtype=torch.float64),
                        torch.ones_like(label_class,dtype=torch.float64))
    ce=F.cross_entropy(aux['route_logits'].reshape(-1,13),label_class.reshape(-1),reduction='none').reshape_as(weights)
    route_ce=(ce*weights).sum()/weights.sum().clamp_min(1)
    target_delta=torch.asinh(labels.double())-torch.asinh(base)
    jump_abs=F.smooth_l1_loss(delta,target_delta,reduction='none',beta=.05)
    jump=(jump_abs*missed.double()).sum()/missed.double().sum().clamp_min(1)
    gate=F.smooth_l1_loss(aux['gate'],missed.double(),reduction='none',beta=.1)
    gate_loss=(gate*weights).sum()/weights.sum().clamp_min(1)
    total=core+route_weight*route_ce+jump_weight*jump+gate_weight*gate_loss
    if not torch.isfinite(total):
        raise RuntimeError('ROUTE_NONFINITE_LOSS')
    parts.update(route_ce=route_ce.detach(),hard_jump=jump.detach(),gate_target=gate_loss.detach(),
                 source_wrong_route_rate=missed.float().mean().detach(),total=total.detach())
    return total,parts



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


def evaluate(network,evaluation,device,batch_size=256):
    metrics=v2.evaluate(network,evaluation,device,batch_size)
    features,numbers,operations,families=evaluation
    corrected=[];source=[];route_ids=[];prior_ids=[];trueclass=[];gates=[]
    trained=network.training
    network.eval()
    with torch.inference_mode():
        for start in range(0,len(features),batch_size):
            x=torch.as_tensor(features[start:start+batch_size],dtype=torch.float32,device=device)
            pred,base,_,_,aux=network.forward_with_route(x)
            corrected.append(decode_int(pred).cpu().numpy())
            source.append(base.to(torch.int64).cpu().numpy())
            route_ids.append(aux['route_logits'].argmax(-1).cpu().numpy())
            prior_ids.append(aux['prior_logits'].argmax(-1).cpu().numpy())
            gates.append(aux['gate'].cpu().numpy())
    network.train(trained)
    pred=np.concatenate(corrected);src=np.concatenate(source)
    truth=np.asarray(numbers,dtype=np.int64)
    classes=RouteAwareNumeracy.route_classes(torch.from_numpy(truth)).numpy()
    base_classes=RouteAwareNumeracy.route_classes(torch.from_numpy(src)).numpy()
    learned=np.concatenate(route_ids);prior=np.concatenate(prior_ids)
    gate=np.concatenate(gates)
    wrong=base_classes!=classes
    correct=previous.correctness(pred,truth)
    source_correct=previous.correctness(src,truth)
    severe=(np.abs(np.arcsinh(truth.astype(np.float64))-np.arcsinh(src.astype(np.float64)))>=np.log(2))
    metrics.update(
        source_wrong_route_operands=int(wrong.sum()),
        source_wrong_route_rows=int(wrong.any(-1).sum()),
        corrected_route_top1_accuracy=float((learned==classes).mean()),
        frozen_route_top1_accuracy=float((prior==classes).mean()),
        corrected_route_accuracy_on_source_wrong=float((learned[wrong]==classes[wrong]).mean()) if wrong.any() else None,
        source_wrong_route_operand_5pct_recovery=float(correct[wrong].mean()) if wrong.any() else None,
        source_wrong_route_operand_5pct_baseline=float(source_correct[wrong].mean()) if wrong.any() else None,
        source_correct_route_operand_5pct_recovery=float(correct[~wrong].mean()) if (~wrong).any() else None,
        source_correct_route_operand_5pct_baseline=float(source_correct[~wrong].mean()) if (~wrong).any() else None,
        severe_source_error_operands=int(severe.sum()),
        severe_source_error_operand_5pct_recovery=float(correct[severe].mean()) if severe.any() else None,
        severe_source_error_operand_5pct_baseline=float(source_correct[severe].mean()) if severe.any() else None,
        mean_gate_source_wrong=float(gate[wrong].mean()) if wrong.any() else None,
        mean_gate_source_correct=float(gate[~wrong].mean()) if (~wrong).any() else None,
    )
    for j,example in enumerate(metrics['examples']):
        example.update(source_x_route_wrong=bool(wrong[j,0]),source_y_route_wrong=bool(wrong[j,1]),
                       corrected_x_route_class=int(learned[j,0]),corrected_y_route_class=int(learned[j,1]),
                       target_x_route_class=int(classes[j,0]),target_y_route_class=int(classes[j,1]))
    return metrics



def parser():
    p=op.parser()
    p.description=__doc__
    p.set_defaults(results_root=DEFAULT_ROOT,source_checkpoint=DEFAULT_SOURCE,
                   constraint_stage='continuity',continuity_weight=0,
                   steps=3000)
    p.add_argument('--branch',choices=['direct','result'],required=False,default='direct')
    p.add_argument('--route-width',type=int,default=128)
    p.add_argument('--route-weight',type=float,default=.35)
    p.add_argument('--wrong-route-weight',type=float,default=5.)
    p.add_argument('--jump-weight',type=float,default=1.)
    p.add_argument('--gate-weight',type=float,default=.1)
    p.add_argument('--magnitude-weight',type=float,default=1.)
    p.add_argument('--anchor-weight',type=float,default=.01)
    p.add_argument('--candidate-weight',type=float,default=.12)
    p.add_argument('--warmup-steps',type=int,default=200)
    p.add_argument('--eval-checkpoint',choices=['latest','best','best-joint','best-route'],default='latest')
    return p


def config_for(args,benchmark):
    config=op.make_config(args,benchmark)
    config.update(schema=SCHEMA,objective='zero-initialized-route-aware-asinh-residual',
                  route_width=args.route_width,route_weight=args.route_weight,
                  wrong_route_weight=args.wrong_route_weight,jump_weight=args.jump_weight,
                  gate_weight=args.gate_weight,branch=args.branch,
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
        raise RuntimeError('ROUTE_RESUME_CONFIG_OR_IDENTITY_DRIFT')
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
        raise RuntimeError('ROUTE_BAD_SOURCE_OR_BACKBONE_IDENTITY')
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
        raise RuntimeError('ROUTE_SOURCE_ARCHITECTURE_OR_CATALOG_DRIFT')
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
        raise ValueError('ROUTE_USE_SOURCE_CHECKPOINT')
    if args.constraint_stage!='continuity':
        raise ValueError('ROUTE_REQUIRES_CONTINUITY_CHECKPOINT')
    if (min(args.steps,args.batch_size,args.replay_updates,args.eval_every,args.checkpoint_every,
            args.report_every,args.route_width,args.threads,args.val_examples,args.buffer_capacity,
            args.per_expert_batch)<1 or args.warmup_steps<0 or
        min(args.magnitude_weight,args.anchor_weight,args.result_weight,args.candidate_weight)<0 or
        args.magnitude_weight+args.anchor_weight<=0 or args.op_temperature<=0 or
        min(args.route_weight,args.jump_weight,args.gate_weight)<0 or args.wrong_route_weight<1):
        raise ValueError('ROUTE_INVALID_ARGUMENTS')
    torch.set_num_threads(args.threads)
    if args.device=='cuda' and not torch.cuda.is_available():
        raise RuntimeError('ROUTE_CUDA_UNAVAILABLE')
    device=torch.device(args.device)
    benchmark,tasks=previous.read_benchmark(args.v3_root)
    benchmark_root=args.v3_root.expanduser().resolve()
    identity=json.loads((benchmark_root/'model_identity.json').read_text(encoding='utf-8'))
    if identity.get('model')!='qwen':
        raise RuntimeError('ROUTE_EXPECTS_FROZEN_QWEN')
    origin=args.source_checkpoint.expanduser().resolve()
    root=(args.results_root.expanduser().resolve()/args.branch)
    if (root==benchmark_root or benchmark_root in root.parents or
        root==origin.parent or origin.parent in root.parents or
        root==origin or root in origin.parents):
        raise RuntimeError('ROUTE_OUTPUT_COLLIDES_WITH_SOURCE')
    config=config_for(args,benchmark)
    root.mkdir(parents=True,exist_ok=True)
    conf=root/'run_config.json'
    if conf.exists():
        if json.loads(conf.read_text(encoding='utf-8'))!=config:
            raise RuntimeError('ROUTE_CONFIG_DRIFT')
    else:
        if any(root.iterdir()):
            raise RuntimeError('ROUTE_NONEMPTY_RESULTS_ROOT')
        atomic_json(conf,config)
    torch.manual_seed(args.seed)
    net=RouteAwareNumeracy(args.constraint_stage,args.rank,args.residual_scale,
                        args.op_width,args.route_width).to(device)
    optimizer=torch.optim.AdamW([p for p in net.parameters() if p.requires_grad],lr=args.lr,weight_decay=args.weight_decay)
    stream=h.make_stream(args.seed,tasks,args.sampler)
    replay=op.OperationReplay(args.buffer_capacity,args.seed+17)
    latest=root/'latest.pt';best_result=root/'best.pt';best_joint=root/'best_joint.pt';best_route=root/'best_route.pt'
    best={'result':-1.,'joint':-1.,'route':-1.,'result_step':None,'joint_step':None,'route_step':None}
    step=0
    if args.resume or args.eval_only:
        check={'latest':latest,'best':best_result,'best-joint':best_joint,'best-route':best_route}[args.eval_checkpoint] if args.eval_only else latest
        if not check.exists():
            raise FileNotFoundError('ROUTE_CHECKPOINT_MISSING')
        step,best,source=restore(check,net,optimizer,stream,replay,config,identity)
        emit('route_cutover_resumed',branch=args.branch,step=step,source=source)
    else:
        if latest.exists():
            raise RuntimeError('ROUTE_USE_--resume')
        source=cutover(origin,net,stream,replay,identity,args)
        net.freeze_source()
        emit('route_cutover_initialized',branch=args.branch,source=source,
             trainable_parameters=sum(p.numel() for p in net.parameters() if p.requires_grad),
             source_parameters=sum(p.numel() for p in net.source.parameters()),
             frozen_qwen_trainable_parameters=0,replay_records=len(replay.records),
             seen_pairs=len(stream.seen),source_unchanged=True)
        save(latest,net,optimizer,0,config,stream,replay,best,identity,source)
    if args.eval_only:
        for split in ('val','test','control'):
            data=previous.load_eval(benchmark_root,benchmark,tasks,split)
            emit('route_cutover_final_evaluation',branch=args.branch,split=split,
                 step=step,**evaluate(net,data,device))
        return 0
    validation=previous.load_eval(benchmark_root,benchmark,tasks,'val',args.val_examples)
    if step==0:
        baseline=evaluate(net,validation,device)
        if (baseline['result_5pct_pass_rate']!=baseline['source_result_5pct_pass_rate'] or
            baseline['joint_xy_5pct_pass_rate']!=baseline['source_joint_xy_5pct_pass_rate'] or
            baseline['boundary_snap_count']!=baseline['source_boundary_snap_count'] or
            baseline['numeric_result_5pct_pass_rate']!=baseline['source_numeric_result_5pct_pass_rate']):
            raise RuntimeError('ROUTE_STEP_ZERO_MISMATCH')
        # The inherited model is a real checkpoint candidate, not a disposable
        # warm-start. Keep it as both bests until genuinely surpassed.
        best.update(result=baseline['numeric_result_5pct_pass_rate'],
                    joint=baseline['joint_xy_5pct_pass_rate'],
                    route=baseline['source_wrong_route_operand_5pct_recovery'] if baseline['source_wrong_route_operand_5pct_recovery'] is not None else -1.,
                    result_step=0,joint_step=0,route_step=0)
        save(best_result,net,optimizer,0,config,stream,replay,best,identity,source)
        save(best_joint,net,optimizer,0,config,stream,replay,best,identity,source)
        save(best_route,net,optimizer,0,config,stream,replay,best,identity,source)
        save(latest,net,optimizer,0,config,stream,replay,best,identity,source)
        emit('route_cutover_step_zero',branch=args.branch,step=0,**baseline)
    import nanojev_numerate_spine_cassette_smoke as cassette
    _,frozen,tokenizer,source_device,info=cassette.load_source('qwen',args)
    if (info.get('resolved_commit')!=identity['model_info'].get('resolved_commit') or
        info.get('source')!=identity['model_info'].get('source') or
        str(info.get('dtype'))!=str(identity['model_info'].get('dtype'))):
        raise RuntimeError('ROUTE_BACKBONE_IDENTITY_DRIFT')
    frozen.eval()
    previous.verify_capture_alignment(torch,frozen,tokenizer,source_device,benchmark_root,tasks,args.max_prompt_tokens)
    tic=time.monotonic()
    for k in range(step+1,args.steps+1):
        batch=stream.batch(args.batch_size)
        features=previous.extract_batched(torch,frozen,tokenizer,[row['prompt'] for row in batch],
                                          source_device,args.max_prompt_tokens)
        if features.requires_grad:
            raise RuntimeError('ROUTE_FROZEN_BACKBONE_HAS_GRAD')
        replay.add(features,batch)
        for _ in range(args.replay_updates):
            feats,nums,ops_list=replay.sample_labeled(args.per_expert_batch)
            labels=torch.as_tensor(nums,dtype=torch.long,device=device)
            net.train()
            pred,base,delta,op_logits,aux=net.forward_with_route(feats.to(device=device,dtype=torch.float32))
            ramp=min(1.,k/max(args.warmup_steps,1)) if args.warmup_steps else 1.
            loss,parts=route_loss(pred,base,delta,op_logits,aux,labels,ops_list,branch=args.branch,
                  magnitude_weight=args.magnitude_weight,result_weight=args.result_weight,
                  candidate_weight=args.candidate_weight,anchor_weight=args.anchor_weight,
                  result_ramp=ramp,temperature=args.op_temperature,route_weight=args.route_weight,
                  wrong_route_weight=args.wrong_route_weight,jump_weight=args.jump_weight,
                  gate_weight=args.gate_weight)
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_([p for p in net.parameters() if p.requires_grad],1.)
            optimizer.step()
        if k%args.report_every==0 or k==args.steps:
            emit('route_cutover_train_progress',branch=args.branch,step=k,
                 source_step=source['source_step'],new_pairs=len(stream.seen)-source['source_seen_pairs'],
                 seen_pairs=len(stream.seen),replay_records=len(replay.records),
                 loss=float(loss.detach()),**{key:float(val) for key,val in parts.items()},
                 elapsed_seconds=round(time.monotonic()-tic,2))
        if k%args.eval_every==0 or k==args.steps:
            metrics=evaluate(net,validation,device)
            emit('route_cutover_validation',branch=args.branch,step=k,**metrics)
            result=metrics['numeric_result_5pct_pass_rate']
            joint=metrics['joint_xy_5pct_pass_rate']
            if result is not None and result>best['result']:
                best.update(result=result,result_step=k)
                save(best_result,net,optimizer,k,config,stream,replay,best,identity,source)
                emit('route_cutover_best_result',branch=args.branch,step=k,value=result,path=str(best_result))
            if joint>best['joint']:
                best.update(joint=joint,joint_step=k)
                save(best_joint,net,optimizer,k,config,stream,replay,best,identity,source)
                emit('route_cutover_best_joint',branch=args.branch,step=k,value=joint,path=str(best_joint))
        route=metrics['source_wrong_route_operand_5pct_recovery'] if (k%args.eval_every==0 or k==args.steps) else None
        if route is not None and route>best['route']:
            best.update(route=route,route_step=k)
            save(best_route,net,optimizer,k,config,stream,replay,best,identity,source)
            emit('route_cutover_best_route',branch=args.branch,step=k,value=route,path=str(best_route))
        if k%args.checkpoint_every==0 or k==args.steps:
            save(latest,net,optimizer,k,config,stream,replay,best,identity,source)
            emit('route_cutover_checkpoint',branch=args.branch,step=k,path=str(latest))
    emit('route_cutover_complete',branch=args.branch,step=args.steps,best=best,root=str(root))
    return 0


def self_test():
    torch.manual_seed(12)
    net=RouteAwareNumeracy(op_width=8,route_width=8)
    x=torch.randn(3,h.INPUT_WIDTH)
    pred,base,delta,op_logits=net(x)
    assert torch.equal(decode_int(pred),base.to(torch.int64))
    assert torch.all(delta==0)
    # Exact arithmetic result gradient, including the zero/sign crossing.
    pred,base,delta,ops,aux=net.forward_with_route(x)
    labels=torch.tensor([[125000,30],[-10,20],[0,-15]],dtype=torch.long)
    loss,terms=route_loss(pred,base,delta,ops,aux,labels,
          ['written_product','written_sub','conditional_min'],branch='result',
          magnitude_weight=0,anchor_weight=0,result_weight=1,candidate_weight=1)
    loss.backward()
    assert torch.isfinite(loss)
    assert net.fine.bias.grad.abs().sum()>0
    assert net.route.bias.grad.abs().sum()>0
    assert net.gate.bias.grad.abs().sum()>0
    assert all(p.grad is None for p in net.source.parameters())
    emit('route_cutover_self_test_pass',zero_step_exact=True,
         source_frozen=True,executable_result_backprop=True)


if __name__=='__main__':
    raise SystemExit(main())
