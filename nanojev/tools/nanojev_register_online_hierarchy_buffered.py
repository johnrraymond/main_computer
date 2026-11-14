#!/usr/bin/env python3
"""Efficient online constrained register training: GPU-vectorized loss and per-bucket replay.

The Qwen3-0.6B backbone is always frozen.  This is an additive experiment;
checkpoint initialization from an earlier constrained model copies ONLY its
weights, not its optimizer/stream step.  Its own checkpoints include replay
contents so --resume is an actual continuation.
"""
from __future__ import annotations

import argparse
from collections import deque
import json
import math
import os
from pathlib import Path
import random
import time

import numpy as np
import torch
import torch.nn.functional as F

import nanojev_register_online_hierarchy_constrained as c

h = c.h
previous = c.previous
emit = c.emit
atomic_json = c.atomic_json
SCHEMA = 'nanojev.register-online-hierarchy-buffered.v1'
DEFAULT_ROOT = Path.home() / 'NanoJev' / 'runs' / 'register_online_hierarchy_buffered_v1'
BUCKETS_PER_REGISTER = 1 + h.REGIMES * 2  # zero + twelve signed magnitudes


def classify_bucket(value: int) -> int:
    """Separate exact-zero bank, then negative/positive banks in each regime."""
    if value == 0:
        return 0
    band = int(h.band_of(np.asarray(value)))
    return 1 + band * 2 + (1 if value > 0 else 0)


class BucketReplay:
    """CPU float16 feature records indexed by TWO independent register banks.

    Refs avoid allocating the same 8192-dimensional vector twice. Replay is
    bounded to capacity per bank. Both bank index and sampling RNG survive
    checkpoints; all records live in the checkpoint so resumption is faithful.
    """
    def __init__(self, capacity: int, seed: int):
        if capacity < 1:
            raise ValueError('BUFFER_CAPACITY_MUST_BE_POSITIVE')
        self.capacity = int(capacity)
        self.rng = random.Random(int(seed))
        self.banks = [[deque() for _ in range(BUCKETS_PER_REGISTER)] for _ in range(2)]
        self.records = {}  # id -> (CPU float16 torch tensor, (X,Y), op, family)
        self.references = {}
        self.next_id = 0
        self.inserted = 0

    def add(self, features: torch.Tensor, samples):
        if features.shape != (len(samples), h.INPUT_WIDTH):
            raise ValueError('BUFFER_FEATURE_SHAPE')
        cpu = features.detach().to(device='cpu', dtype=torch.float16)
        for vec, sample in zip(cpu, samples):
            rid = self.next_id
            self.next_id += 1
            numbers = (int(sample['a']), int(sample['b']))
            self.records[rid] = (vec.clone().contiguous(), numbers, str(sample['operation']),
                                 str(sample['family']))
            self.references[rid] = 2
            for pos, value in enumerate(numbers):
                bank = self.banks[pos][classify_bucket(value)]
                if len(bank) >= self.capacity:
                    old = bank.popleft()
                    self.references[old] -= 1
                    if self.references[old] == 0:
                        del self.records[old]
                        del self.references[old]
                bank.append(rid)
            self.inserted += 1

    def counts(self):
        return [[len(bank) for bank in register] for register in self.banks]

    def sample(self, per_bank: int):
        """Stratified samples WITH replacement until banks are fully populated.

        Missing buckets are skipped during warmup; no synthetic activations.
        The returned batch supervises all heads of BOTH registers, not only
        the bank it was drawn from. No read of validation/control captures.
        """
        if per_bank < 1:
            raise ValueError('BUFFER_PER_BANK_MUST_BE_POSITIVE')
        ids = []
        for pos in range(2):
            for bank in self.banks[pos]:
                if bank:
                    choices = list(bank)
                    if len(choices) >= per_bank:
                        ids.extend(self.rng.sample(choices, per_bank))
                    else:
                        ids.extend(self.rng.choices(choices, k=per_bank))
        self.rng.shuffle(ids)
        if not ids:
            raise RuntimeError('BUFFER_EMPTY')
        rows = [self.records[rid] for rid in ids]
        data = torch.stack([r[0] for r in rows])
        truth = np.asarray([r[1] for r in rows], dtype=np.int64)
        return data, truth

    def probe(self, limit: int):
        """Deterministic recent stored training records, no duplicate IDs."""
        ids = sorted(self.records)[-limit:]
        if not ids:
            raise RuntimeError('BUFFER_EMPTY_PROBE')
        rows = [self.records[rid] for rid in ids]
        feats = np.stack([r[0].numpy().astype(np.float32) for r in rows])
        truth = np.asarray([r[1] for r in rows], dtype=np.int64)
        ops = [r[2] for r in rows]
        families = [r[3] for r in rows]
        return feats, truth, ops, families

    def state_dict(self):
        return {
            'version': 1, 'capacity': self.capacity, 'rng': self.rng.getstate(),
            'banks': [[list(q) for q in p] for p in self.banks],
            'records': self.records, 'next_id': self.next_id,
            'inserted': self.inserted,
        }

    def load_state_dict(self, state):
        if state['version'] != 1 or state['capacity'] != self.capacity:
            raise RuntimeError('BUFFER_RESUME_CAPACITY_DRIFT')
        banks = state['banks']
        if len(banks) != 2 or any(len(x) != BUCKETS_PER_REGISTER for x in banks):
            raise RuntimeError('BUFFER_RESUME_BANK_SHAPE')
        self.rng.setstate(state['rng'])
        self.records = dict(state['records'])
        self.banks = [[deque(bank) for bank in p] for p in banks]
        self.references = {rid: 0 for rid in self.records}
        for register in self.banks:
            for bank in register:
                if len(bank) > self.capacity:
                    raise RuntimeError('BUFFER_RESUME_OVER_CAPACITY')
                for rid in bank:
                    if rid not in self.references:
                        raise RuntimeError('BUFFER_RESUME_BROKEN_RECORD_REF')
                    self.references[rid] += 1
        if any(count != 2 and count != 1 for count in self.references.values()):
            raise RuntimeError('BUFFER_RESUME_INVALID_REFERENCES')
        self.next_id = int(state['next_id'])
        self.inserted = int(state['inserted'])
        if self.records and max(self.records) >= self.next_id:
            raise RuntimeError('BUFFER_RESUME_INVALID_ID')


def supervision_fast(numbers, device):
    """Construct targets on CPU before upload: no round-trip from GPU bands to numpy."""
    array = np.asarray(numbers, dtype=np.int64)
    if array.ndim != 2 or array.shape[1] != 2 or np.any(np.abs(array) > h.MAX_VALUE):
        raise ValueError('BUFFER_TARGET_SHAPE_OR_RANGE')
    bucket = h.band_of(array)
    local = c.log_encode(np.abs(array), bucket)
    return (torch.as_tensor(array, dtype=torch.long, device=device),
            torch.as_tensor(bucket, dtype=torch.long, device=device),
            torch.as_tensor(local, dtype=torch.float32, device=device))


def vectorized_hierarchy_loss(raw, labels, bands, local, * ,
                              zero_positive_weight=2.0, expert_weight=1.0):
    """38 masked head means, with NO Python branching on CUDA tensors.

    Equivalent to h.hierarchy_loss at expert_weight=1 for the same targets,
    including its conditional threshold eligibility and active-head averaging.
    Returns per-component differentiable scalar losses and a GPU count vector.
    """
    if raw.ndim != 2 or raw.shape[1] != 38 or labels.ndim != 2 or labels.shape[1] != 2:
        raise ValueError('BUFFER_BAD_SUPERVISION_SHAPE')
    logits = raw.float().reshape(-1, 2, h.HEADS_PER_REGISTER)
    labels = labels.to(dtype=torch.long)
    nonzero = labels != 0
    absnum = labels.abs()
    zlabel = (~nonzero).float()
    zweights = torch.where(nonzero, 1., zero_positive_weight)
    zeros = F.binary_cross_entropy_with_logits(logits[:, :, 0], zlabel, reduction='none')
    zero_loss = (zeros * zweights).sum(0) / zweights.sum(0).clamp_min(1)

    sign_loss_elements = F.binary_cross_entropy_with_logits(
        logits[:, :, 1], (labels > 0).float(), reduction='none')
    nzmask = nonzero.float()
    sign_count = nzmask.sum(0)
    sign_loss = (sign_loss_elements * nzmask).sum(0) / sign_count.clamp_min(1)

    cuts = torch.as_tensor(h.CUTS, dtype=absnum.dtype, device=raw.device)
    preceding = torch.cat((cuts.new_zeros(1), cuts[:-1]))
    route_mask = (absnum.unsqueeze(-1) > preceding).float()  # B x 2 x 5
    route_target = (absnum.unsqueeze(-1) > cuts).float()
    route_bce = F.binary_cross_entropy_with_logits(
        logits[:, :, 2:7], route_target, reduction='none')
    route_count = route_mask.sum(0)
    route_loss = (route_bce * route_mask).sum(0) / route_count.clamp_min(1)

    expert_label_index = 2 * bands + (labels > 0).long()
    expert_mask = F.one_hot(expert_label_index, num_classes=12).float() * nzmask.unsqueeze(-1)
    expert_l1 = F.smooth_l1_loss(logits[:, :, 7:],
                                local.unsqueeze(-1).expand(-1, -1, 12),
                                reduction='none', beta=.15)
    expert_count = expert_mask.sum(0)
    expert_loss = (expert_l1 * expert_mask).sum(0) / expert_count.clamp_min(1)

    # Inactive heads are exactly zero, without CPU .any() decisions.
    zmean = zero_loss.sum()
    smean = (sign_loss * (sign_count > 0)).sum()
    rmean = (route_loss * (route_count > 0)).sum()
    emean = (expert_loss * (expert_count > 0)).sum()
    n_cls = 2 + (sign_count > 0).float().sum() + (route_count > 0).float().sum()
    n_exp = (expert_count > 0).float().sum()
    weighted = (zmean + smean + rmean + expert_weight * emean) / (n_cls + expert_weight*n_exp).clamp_min(1)
    stats = {'zero': zmean / 2, 'sign': smean / (sign_count > 0).float().sum().clamp_min(1),
             'route': rmean / (route_count > 0).float().sum().clamp_min(1),
             'expert': emean / n_exp.clamp_min(1),
             'active_classifiers': n_cls, 'active_experts': n_exp,
             'expert_counts': expert_count, 'route_counts': route_count}
    return weighted, stats


def vectorized_boundary_loss(raw, labels, window=.12):
    """Five boundaries in one gather; no GPU .any() or .item()."""
    if window <= 0:
        raise ValueError('BUFFER_BAD_BOUNDARY_WINDOW')
    logits = raw.float().reshape(-1, 2, h.HEADS_PER_REGISTER)[:, :, 7:]
    magnitude = labels.abs().float()
    target_log = magnitude.clamp_min(1).log().unsqueeze(-1)
    cuts = torch.as_tensor(h.CUTS, dtype=torch.float32,device=raw.device).log()
    near = (labels != 0).unsqueeze(-1) & ((target_log - cuts).abs() <= window)
    sign = (labels > 0).long().unsqueeze(-1)
    band = torch.arange(5, device=raw.device).reshape(1, 1, 5)
    idx = 2 * band + sign
    idx = idx.expand(len(raw), 2, 5)
    lower = logits.gather(2,idx)
    upper = logits.gather(2,idx+2)
    log_lo = torch.tensor([math.log(h.band_limits(i)[0]) for i in range(6)],
                          dtype=torch.float32,device=raw.device)
    log_hi = torch.tensor([math.log(h.band_limits(i)[1]) for i in range(6)],
                          dtype=torch.float32,device=raw.device)
    lower_log = log_lo[:-1] + (lower+1)*.5*(log_hi[:-1]-log_lo[:-1])
    upper_log = log_lo[1:] + (upper+1)*.5*(log_hi[1:]-log_lo[1:])
    lower_target=target_log.clamp(min=log_lo[:-1],max=log_hi[:-1])
    upper_target=target_log.clamp(min=log_lo[1:],max=log_hi[1:])
    terms=(F.smooth_l1_loss(lower_log,lower_target,beta=.05,reduction='none')+
           F.smooth_l1_loss(upper_log,upper_target,beta=.05,reduction='none')+
           .5*F.smooth_l1_loss(lower_log,upper_log,beta=.05,reduction='none'))
    count=near.float().sum()
    return (terms*near.float()).sum()/count.clamp_min(1), count


def fast_constrained_loss(raw, labels, bands, local, model, *,
                          stage='continuity', zero_positive_weight=2.,
                          expert_weight=1., range_weight=.05,
                          sharing_weight=.02, continuity_weight=0.,
                          boundary_window=.12):
    basic, stats = vectorized_hierarchy_loss(raw, labels, bands, local,
                                zero_positive_weight=zero_positive_weight,
                                expert_weight=expert_weight)
    range_loss = F.relu(raw.float().reshape(-1, 2, 19)[:,:,7:].abs() - 1).square().mean()
    sharing_loss = model.sharing_penalty() if stage in ('shared','continuity') else raw.float().sum()*0
    if stage == 'continuity' and continuity_weight:
        boundary_loss, boundary_count = vectorized_boundary_loss(raw,labels,boundary_window)
    else:
        boundary_loss, boundary_count = raw.float().sum()*0, torch.zeros((),device=raw.device)
    loss = basic + range_weight*range_loss + sharing_weight*sharing_loss + continuity_weight*boundary_loss
    stats.update({'total':loss.detach(),'supervised':basic.detach(),
                  'range':range_loss.detach(),'sharing':sharing_loss.detach(),
                  'continuity':boundary_loss.detach(),'boundary_rows':boundary_count.detach()})
    return loss, stats


def compact_stats(stats):
    """Call only at report intervals; performs the necessary CPU synchronization."""
    return {key: float(stats[key].detach().cpu()) for key in ('total','supervised','zero',
                   'sign','route','expert','range','sharing','continuity','boundary_rows',
                   'active_classifiers','active_experts')}


def config_for(args, manifest):
    config = c.config_for(args, manifest)
    config.update(schema=SCHEMA, objective='stage5_38_constrained_buffered_v1',
                  buffer_capacity=args.buffer_capacity, per_expert_batch=args.per_expert_batch,
                  expert_weight=args.expert_weight, replay_probe=args.replay_probe,
                  init_checkpoint=str(args.init_checkpoint.expanduser().resolve()) if args.init_checkpoint else None)
    return config


def save_checkpoint(path, model, optimizer, step, config, stream, replay, best, identity):
    state = {'schema': SCHEMA, 'step': int(step), 'config': config,
             'model': model.state_dict(), 'optimizer': optimizer.state_dict(),
             'random_stream': stream.state_dict(), 'replay': replay.state_dict(),
             'best_validation':best,'backbone_identity':identity,
             'torch_rng':torch.get_rng_state()}
    if torch.cuda.is_available():
        state['cuda_rng']=torch.cuda.get_rng_state_all()
    tmp=path.with_name(path.name+'.partial')
    torch.save(state,tmp)
    os.replace(tmp,path)


def load_checkpoint(path, model, optimizer, stream, replay, config, identity):
    ck=torch.load(path,map_location='cpu',weights_only=True)
    if ck.get('schema')!=SCHEMA or ck.get('config')!=config or ck.get('backbone_identity')!=identity:
        raise RuntimeError('BUFFER_RESUME_CONFIG_OR_BACKBONE_DRIFT')
    model.load_state_dict(ck['model'])
    optimizer.load_state_dict(ck['optimizer'])
    stream.load_state_dict(ck['random_stream'])
    replay.load_state_dict(ck['replay'])
    torch.set_rng_state(ck['torch_rng'].cpu().to(torch.uint8))
    if torch.cuda.is_available() and 'cuda_rng' in ck:
        torch.cuda.set_rng_state_all([item.cpu().to(torch.uint8) for item in ck['cuda_rng']])
    return int(ck['step']), dict(ck['best_validation'])


def import_older_checkpoint(path, model, args, identity):
    ck=torch.load(path,map_location='cpu',weights_only=True)
    config=ck.get('config',{})
    if ck.get('schema')!=c.SCHEMA or ck.get('backbone_identity')!=identity:
        raise RuntimeError('BUFFER_INIT_INCOMPATIBLE_BASELINE')
    if (config.get('constraint_stage')!=args.constraint_stage or
        config.get('rank')!=args.rank or
        config.get('residual_scale')!=args.residual_scale):
        raise RuntimeError('BUFFER_INIT_ARCHITECTURE_DRIFT')
    model.load_state_dict(ck['model'],strict=True)
    return int(ck['step'])


def parser():
    p=c.parser()
    p.description=__doc__
    p.set_defaults(results_root=DEFAULT_ROOT,continuity_weight=0., steps=500)
    p.add_argument('--buffer-capacity',type=int,default=256,
                   help='Max CPU-stored activation records per register/bucket (13 banks/register)')
    p.add_argument('--per-expert-batch',type=int,default=16,
                   help='Replay examples sampled per register/bucket per optimizer update')
    p.add_argument('--expert-weight',type=float,default=2.,
                   help='Relative weight on supervised magnitude regression vs classifiers')
    p.add_argument('--replay-probe',type=int,default=512,
                   help='How many recent cached unique training records to evaluate')
    p.add_argument('--init-checkpoint',type=Path,default=None,
                   help='Copy compatible constrained v2 weights; starts a NEW optimizer/run')
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    if args.self_test:
        self_test();return 0
    if (args.buffer_capacity<1 or args.per_expert_batch<1 or args.expert_weight<=0 or
        args.replay_probe<1 or args.batch_size<1 or args.steps<1 or args.eval_every<1 or
        args.replay_updates<1 or args.checkpoint_every<1 or args.report_every<1):
        raise ValueError('BUFFER_INVALID_ARGUMENTS')
    if args.resume and args.init_checkpoint:
        # --init-checkpoint is part of config and needed for compatible resume;
        # permitted here. The checkpoint is not reimported on resumed runs.
        pass
    torch.set_num_threads(args.threads)
    if args.device=='cuda' and not torch.cuda.is_available():
        raise RuntimeError('BUFFER_CUDA_UNAVAILABLE')
    device=torch.device(args.device)
    manifest,tasks=previous.read_benchmark(args.v3_root)
    source=args.v3_root.expanduser().resolve()
    identity=json.loads((source/'model_identity.json').read_text(encoding='utf-8'))
    if identity.get('model')!='qwen':
        raise RuntimeError('BUFFER_EXPECTS_QWEN')
    config=config_for(args,manifest)
    root=args.results_root.expanduser().resolve()
    if root==source or source in root.parents:
        raise RuntimeError('BUFFER_ROOT_OVERWRITES_BENCHMARK')
    root.mkdir(parents=True,exist_ok=True)
    conf_path=root/'run_config.json'
    if conf_path.exists():
        if json.loads(conf_path.read_text(encoding='utf-8')) != config:
            raise RuntimeError('BUFFER_EXISTING_CONFIG_DRIFT')
    else:
        if any(root.iterdir()):
            raise RuntimeError('BUFFER_EXISTING_NONEMPTY_RESULTS_ROOT')
        atomic_json(conf_path, config)
    torch.manual_seed(args.seed)
    model=c.model_factory(args.constraint_stage,args.rank,args.residual_scale).to(device)
    optimizer=torch.optim.AdamW(model.parameters(),lr=args.lr,weight_decay=args.weight_decay)
    stream=h.make_stream(args.seed,tasks,args.sampler)
    replay=BucketReplay(args.buffer_capacity,args.seed+17)
    latest,best_path=root/'latest.pt',root/'best.pt'
    best={'joint':-1.,'loss':float('inf')}
    step=0
    eval_kw=dict(stage=args.constraint_stage,range_weight=args.range_weight,
                 sharing_weight=args.sharing_weight,continuity_weight=args.continuity_weight,
                 boundary_window=args.boundary_window)
    emit('buffered_decoder_ready', stage=args.constraint_stage,
         parameters=sum(p.numel() for p in model.parameters()),head_count=38,
         frozen_backbone='Qwen3-0.6B',trainable_backbone_parameters=0,
         benchmark_prompts=len(tasks),buffer_capacity=args.buffer_capacity,
         per_expert_batch=args.per_expert_batch,continuity_weight=args.continuity_weight)
    if args.eval_only:
        if not best_path.is_file():
            raise FileNotFoundError('BUFFER_BEST_CHECKPOINT_MISSING')
        step,_=load_checkpoint(best_path,model,optimizer,stream,replay,config,identity)
        for split in ('val','test','control'):
            e=previous.load_eval(source,manifest,tasks,split)
            res=c.score(model,e,device,**eval_kw)
            emit('buffered_final_evaluation',split=split,step=step,**res)
        return 0
    if latest.is_file() and not args.resume:
        raise RuntimeError('BUFFER_USE_--resume_FOR_EXISTING_CHECKPOINT')
    if args.resume:
        if not latest.is_file():
            raise FileNotFoundError('BUFFER_RESUME_CHECKPOINT_MISSING')
        step,best=load_checkpoint(latest,model,optimizer,stream,replay,config,identity)
        emit('buffered_resumed',step=step,replay_records=len(replay.records),best_validation=best)
    elif args.init_checkpoint:
        oldstep=import_older_checkpoint(args.init_checkpoint.expanduser().resolve(),model,args,identity)
        emit('buffered_initialized_from_constrained',old_step=oldstep,
             checkpoint=str(args.init_checkpoint),optimizer_reset=True,replay_reset=True)
    validation=previous.load_eval(source,manifest,tasks,'val',args.val_examples)
    baseline=None
    if args.baseline_results_root is not None:
        baseline=c.baseline_report(args.baseline_results_root,config,identity,validation,device)
    import nanojev_numerate_spine_cassette_smoke as cassette
    _,frozen,tokenizer,source_device,model_info=cassette.load_source('qwen',args)
    if (model_info.get('resolved_commit')!=identity['model_info'].get('resolved_commit') or
        model_info.get('source')!=identity['model_info'].get('source') or
        str(model_info.get('dtype'))!=str(identity['model_info'].get('dtype'))):
        raise RuntimeError('BUFFER_BACKBONE_IDENTITY_DRIFT')
    frozen.eval()
    previous.verify_capture_alignment(torch,frozen,tokenizer,source_device,source,tasks,args.max_prompt_tokens)
    start=time.monotonic()
    capture_sec=update_sec=0.
    rolling_losses=[]
    last_stats=None
    for k in range(step+1,args.steps+1):
        tic=time.monotonic()
        incoming=stream.batch(args.batch_size)
        features=previous.extract_batched(torch,frozen,tokenizer,
                     [r['prompt'] for r in incoming],source_device,args.max_prompt_tokens)
        if features.requires_grad:
            raise RuntimeError('BUFFER_BACKBONE_HAS_GRADIENTS')
        replay.add(features,incoming)
        capture_sec+=time.monotonic()-tic
        tic=time.monotonic()
        for _ in range(args.replay_updates):
            feature_cpu, numbers=replay.sample(args.per_expert_batch)
            x=feature_cpu.to(device=device,dtype=torch.float32)
            labels,bands,local=supervision_fast(numbers,device)
            model.train()
            predictions=model(x)
            loss,last_stats=fast_constrained_loss(predictions,labels,bands,local,model,
                stage=args.constraint_stage,zero_positive_weight=args.zero_positive_weight,
                expert_weight=args.expert_weight,range_weight=args.range_weight,
                sharing_weight=args.sharing_weight,continuity_weight=args.continuity_weight,
                boundary_window=args.boundary_window)
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(),1.)
            optimizer.step()
        # No per-head scalar readback or GPU mask->Python branches in updates.
        rolling_losses.append(loss.detach())
        update_sec+=time.monotonic()-tic
        if k%args.report_every==0 or k==args.steps:
            if device.type=='cuda':torch.cuda.synchronize()
            report_loss=float(torch.stack(rolling_losses).mean().cpu())
            rolling_losses.clear()
            report_stats=compact_stats(last_stats)
            emit('buffered_train_progress',step=k,unique_generated_pairs=len(stream.seen),
                 replay_records=len(replay.records),bank_counts=replay.counts(),
                 active_supervised_heads=int(report_stats['active_classifiers']+report_stats['active_experts']),
                 train_components=report_stats,mean_train_loss=report_loss,
                 frozen_qwen_seconds=round(capture_sec,2),decoder_update_seconds=round(update_sec,2),
                 elapsed_seconds=round(time.monotonic()-start,2),
                 fresh_pairs_per_second=round((k-step)*args.batch_size/max(time.monotonic()-start,1e-9),2))
        if k%args.eval_every==0 or k==args.steps:
            probe=c.score(model,replay.probe(args.replay_probe),device,**eval_kw)
            v=c.score(model,validation,device,**eval_kw)
            v['replay_probe_rows']=probe['rows']
            v['replay_probe_joint_pass_rate']=probe['joint_pass_rate']
            v['replay_probe_expert_normalized_mae']=probe['true_bucket_expert_normalized_mae']
            v['replay_probe_head_loss']=probe['head_validation_loss']
            v['replay_probe_per_expert']={key:val for key,val in probe['heads'].items()
                                            if '_expert_' in key}
            if baseline is not None:
                v['baseline_joint_pass_rate']=baseline['joint_pass_rate']
                v['joint_delta_vs_baseline']=v['joint_pass_rate']-baseline['joint_pass_rate']
                v['oracle_joint_delta_vs_baseline']=v['oracle_routing_joint_pass_rate']-baseline['oracle_routing_joint_pass_rate']
            emit('buffered_validation',step=k,**v)
            if h.better_checkpoint(v,best):
                best={'joint':v['joint_pass_rate'],'loss':v['head_validation_loss']}
                save_checkpoint(best_path,model,optimizer,k,config,stream,replay,best,identity)
                emit('buffered_best_checkpoint',step=k,validation_joint=best['joint'],
                     validation_loss=best['loss'],path=str(best_path))
        if k%args.checkpoint_every==0 or k==args.steps:
            save_checkpoint(latest,model,optimizer,k,config,stream,replay,best,identity)
            emit('buffered_checkpoint',step=k,path=str(latest))
    emit('buffered_training_complete',step=args.steps,generated_pairs=len(stream.seen),
         best_validation=best,replay_records=len(replay.records),results_root=str(root))
    return 0


def self_test():
    from itertools import product
    for pos,band,sign in product(range(2),range(6),(-1,1)):
        lo,hi=h.band_limits(band)
        assert classify_bucket(sign*lo)==1+2*band+(sign>0)
        assert classify_bucket(sign*hi)==1+2*band+(sign>0)
    numbers=np.array([[0,10],[20,-200],[2001,-200001],[2,-19]])
    labs,bands,loc=c.supervision(numbers,'cpu')
    raw=torch.randn(4,38,requires_grad=True)
    loss,info=vectorized_hierarchy_loss(raw,labs,bands,loc)
    ref,_=h.hierarchy_loss(torch,raw,labs,bands,loc)
    torch.testing.assert_close(loss,ref,rtol=1e-5,atol=1e-6)
    loss.backward()
    emit('buffered_self_test_pass',head_count=38,supervised_bank_count=26,
         vectorized_loss_equivalent=True,active_experts=int(info['active_experts']))


if __name__=='__main__':
    raise SystemExit(main())
