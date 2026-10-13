#!/usr/bin/env python3
"""Online constrained stage-5 register decoder; Qwen frozen, 38 head readout.

Ablations: bounded -> shared -> continuity.  Reuses the existing 38-head
benchmark/stream/activation capture without modifying its checkpoints.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import time

import numpy as np
import torch
import torch.nn.functional as F

import nanojev_register_online_hierarchy as h

previous = h.previous
v3 = h.v3
emit = h.emit
atomic_json = h.atomic_json
CUTS = h.CUTS
REGIMES = h.REGIMES
HEADS_PER_REGISTER = h.HEADS_PER_REGISTER
INPUT_WIDTH = h.INPUT_WIDTH
SCHEMA = 'nanojev.register-online-hierarchy-constrained.v1'
DEFAULT_ROOT = Path.home() / 'NanoJev' / 'runs' / 'register_online_hierarchy_constrained_v1'
STAGES = ('bounded', 'shared', 'continuity')


def log_encode(magnitudes, bands):
    """Exact logarithmic coordinate [-1,1] inside the designated integer band."""
    a = np.asarray(magnitudes, dtype=np.float64)
    b = np.asarray(bands, dtype=np.int64)
    if np.any(b < 0) or np.any(b >= REGIMES):
        raise ValueError('CONSTRAINED_BAD_BAND')
    out = np.zeros_like(a, dtype=np.float32)
    for k in range(REGIMES):
        use = b == k
        if not np.any(use):
            continue
        low, high = h.band_limits(k)
        if np.any(((a[use] < low) | (a[use] > high)) & (a[use] != 0)):
            # Exact zero has band 0 but is masked from expert regression.
            raise ValueError('CONSTRAINED_TARGET_OUTSIDE_BAND')
        valid = use & (a > 0)
        if valid.any():
            out[valid] = (2 * (np.log(a[valid]) - math.log(low)) /
                          (math.log(high) - math.log(low)) - 1).astype(np.float32)
    return out


def log_decode(coordinates, band):
    """Hard bounds at inference: output cannot escape the expert's band."""
    lo, hi = h.band_limits(band)
    z = np.asarray(coordinates, dtype=np.float64)
    u = np.clip((z + 1) / 2, 0, 1)
    estimated = np.exp(math.log(lo) + u * (math.log(hi) - math.log(lo)))
    estimated = np.clip(estimated, lo, hi)
    return np.where(u == 0, float(lo), np.where(u == 1, float(hi), estimated))


def torch_log_reconstruct(torch_coordinates, band, *, clamp=False):
    lo, hi = h.band_limits(band)
    z = torch_coordinates.clamp(-1, 1) if clamp else torch_coordinates
    return math.log(lo) + (z + 1) * (math.log(hi) - math.log(lo)) / 2


class CoupledMagnitudeHead(torch.nn.Module):
    """38 supervised outputs, with shared per-register magnitude slopes.

    The six bands share a core projection. A low-rank band adapter and an
    antisymmetric low-rank sign adapter provide limited specialized capacity.
    Unrelated classifier projections retain the independent stage-5 structure.
    """
    def __init__(self, rank=8, residual_scale=0.25):
        super().__init__()
        if rank < 1 or residual_scale <= 0:
            raise ValueError('CONSTRAINED_BAD_RANK_OR_SCALE')
        self.rank = int(rank)
        self.residual_scale = float(residual_scale)
        self.norm = torch.nn.LayerNorm(INPUT_WIDTH, elementwise_affine=False)
        self.classifiers = torch.nn.Linear(INPUT_WIDTH, 2 * 7)
        self.common_magnitude = torch.nn.Linear(INPUT_WIDTH, 2)
        self.band_features = torch.nn.Linear(INPUT_WIDTH, 2 * rank, bias=False)
        self.band_coefficients = torch.nn.Parameter(torch.zeros(2, REGIMES, rank))
        self.sign_coefficients = torch.nn.Parameter(torch.zeros(2, REGIMES, rank))
        self.band_bias = torch.nn.Parameter(torch.zeros(2, REGIMES))
        self.sign_bias = torch.nn.Parameter(torch.zeros(2, REGIMES))

    def forward(self, x):
        x = self.norm(x)
        n = len(x)
        cls = self.classifiers(x).view(n, 2, 7)
        common = self.common_magnitude(x).view(n, 2, 1)
        features = self.band_features(x).view(n, 2, self.rank)
        band_correction = torch.einsum('bpr,psr->bps', features, self.band_coefficients)
        sign_correction = torch.einsum('bpr,psr->bps', features, self.sign_coefficients)
        base = common + self.residual_scale * band_correction + self.band_bias
        delta = self.residual_scale * sign_correction + self.sign_bias
        # Expert ordering per bucket is (negative, positive).
        experts = torch.stack((base - delta, base + delta), dim=-1).reshape(n, 2, 12)
        return torch.cat((cls, experts), dim=-1).reshape(n, 38)

    def sharing_penalty(self):
        # Sign deviations must be learned rather than freely independent.
        return (self.sign_coefficients.square().mean() +
                self.sign_bias.square().mean() +
                0.25 * self.band_coefficients.square().mean())


def model_factory(stage='continuity', rank=8, residual_scale=0.25):
    if stage not in STAGES:
        raise ValueError('CONSTRAINED_INVALID_STAGE')
    if stage == 'bounded':
        return h.model_factory(torch)
    return CoupledMagnitudeHead(rank, residual_scale)


def supervision(numbers, device):
    labels, bands, _ = h.supervision(torch, numbers, device)
    local = torch.as_tensor(log_encode(np.abs(numbers), bands.cpu().numpy()),
                            dtype=torch.float32, device=device)
    return labels, bands, local


def boundary_continuity(raw, labels, *, window=0.12):
    """Vectorized near-boundary supervision for both adjacent signed experts.

    Experts on either side predict the same logarithmic magnitude near a cut.
    The neighboring target is clamped to its legal interval. Distant samples
    contribute exactly zero gradient; no labels leak into inference routing.
    """
    if window <= 0:
        raise ValueError('CONSTRAINED_BAD_BOUNDARY_WINDOW')
    logits = raw.float().reshape(-1, 2, HEADS_PER_REGISTER)
    magnitude = labels.abs().float()
    target_log = magnitude.clamp_min(1).log()
    sign_offset = (labels > 0).long()
    total_loss = raw.float().sum() * 0
    total_examples = torch.zeros((), dtype=torch.float32, device=raw.device)
    for i, boundary in enumerate(CUTS):
        mask = (labels != 0) & ((target_log - math.log(boundary)).abs() <= window)
        offsets = sign_offset + (2 * i)
        lower_raw = torch.gather(logits[:, :, 7:], 2, offsets.unsqueeze(-1)).squeeze(-1)
        upper_raw = torch.gather(logits[:, :, 7:], 2, (offsets + 2).unsqueeze(-1)).squeeze(-1)
        lower_log = torch_log_reconstruct(lower_raw, i)
        upper_log = torch_log_reconstruct(upper_raw, i + 1)
        llo, lhi = h.band_limits(i)
        ulo, uhi = h.band_limits(i + 1)
        ltarget = target_log.clamp(math.log(llo), math.log(lhi))
        utarget = target_log.clamp(math.log(ulo), math.log(uhi))
        per_example = (F.smooth_l1_loss(lower_log, ltarget, beta=.05, reduction='none') +
                       F.smooth_l1_loss(upper_log, utarget, beta=.05, reduction='none') +
                       .5 * F.smooth_l1_loss(lower_log, upper_log, beta=.05, reduction='none'))
        total_loss = total_loss + (per_example * mask.float()).sum()
        total_examples = total_examples + mask.float().sum()
    return total_loss / total_examples.clamp_min(1), int(total_examples.detach().item())


def constrained_loss(raw, labels, bands, local, network=None, *,
                     zero_positive_weight=2., range_weight=.05,
                     sharing_weight=.02, continuity_weight=.1,
                     boundary_window=.12, stage='continuity'):
    supervised, used = h.hierarchy_loss(torch, raw, labels, bands, local, zero_positive_weight)
    all_experts = raw.float().reshape(-1, 2, HEADS_PER_REGISTER)[:, :, 7:]
    range_loss = F.relu(all_experts.abs() - 1).square().mean()
    sharing = raw.sum() * 0
    if stage in ('shared', 'continuity'):
        if not hasattr(network, 'sharing_penalty'):
            raise ValueError('CONSTRAINED_SHARED_MODEL_REQUIRED')
        sharing = network.sharing_penalty()
    continuity = raw.sum() * 0
    touched = 0
    if stage == 'continuity' and continuity_weight:
        continuity, touched = boundary_continuity(raw, labels, window=boundary_window)
    total = (supervised + range_weight * range_loss +
             sharing_weight * sharing + continuity_weight * continuity)
    return total, {'active_heads': used, 'supervised': float(supervised.detach()),
                   'range': float(range_loss.detach()),
                   'sharing': float(sharing.detach()),
                   'continuity': float(continuity.detach()), 'boundary_rows': touched}


def decode(raw, oracle_numbers=None):
    logits = np.asarray(raw, dtype=np.float32).reshape(-1, 2, HEADS_PER_REGISTER)
    zeros, signs, bands = h.predicted_routing(logits)
    if oracle_numbers is not None:
        actual = np.asarray(oracle_numbers, dtype=np.int64)
        zeros = actual == 0
        signs = np.where(actual >= 0, 1, -1)
        bands = h.band_of(actual)
    result = np.zeros(logits.shape[:2], dtype=np.int64)
    for pos in range(2):
        for band in range(REGIMES):
            for sign in (-1, 1):
                which = (~zeros[:, pos]) & (bands[:, pos] == band) & (signs[:, pos] == sign)
                if which.any():
                    z = logits[which, pos, h.expert_index(band, sign)]
                    result[which, pos] = np.rint(sign * log_decode(z, band)).astype(np.int64)
    return result


def score(network, evaluation, device, *, stage='continuity', batch_size=256,
          range_weight=.05, sharing_weight=.02, continuity_weight=.1,
          boundary_window=.12):
    x, truth, ops, families = evaluation
    was_training = network.training
    network.eval()
    outputs = []
    with torch.inference_mode():
        for i in range(0, len(x), batch_size):
            data = torch.as_tensor(x[i:i + batch_size], dtype=torch.float32, device=device)
            outputs.append(network(data).float().cpu().numpy())
    network.train(was_training)
    if not outputs:
        raise ValueError('CONSTRAINED_EMPTY_EVAL')
    raw = np.concatenate(outputs)
    predicted, oracle = decode(raw), decode(raw, truth)
    result = previous.summarize_predictions(predicted, truth, ops, families)
    result['oracle_routing_joint_pass_rate'] = float(previous.correctness(oracle,truth).all(axis=1).mean())
    result['oracle_routing_x_pass_rate'] = float(previous.correctness(oracle,truth)[:, 0].mean())
    result['oracle_routing_y_pass_rate'] = float(previous.correctness(oracle,truth)[:, 1].mean())
    zeros, signs, regimes = h.predicted_routing(raw)
    true_bands = h.band_of(truth)
    true_local = log_encode(np.abs(truth), true_bands)
    experts = {}
    errors = []
    for pos, key in enumerate(('x','y')):
        nz = truth[:,pos] != 0
        true_zero = ~nz
        result[key+'_sign_accuracy'] = float((signs[nz,pos] == np.sign(truth[nz,pos])).mean()) if nz.any() else None
        result[key+'_regime_accuracy'] = float((regimes[nz,pos] == true_bands[nz,pos]).mean()) if nz.any() else None
        result[key+'_zero_recall'] = float(zeros[true_zero,pos].mean()) if true_zero.any() else None
        for band in range(REGIMES):
            for sign in (-1, 1):
                selected = nz & (true_bands[:,pos] == band) & ((truth[:,pos] > 0) if sign > 0 else (truth[:,pos] < 0))
                name = f'{key}_expert_{band}_{"pos" if sign>0 else "neg"}'
                if selected.any():
                    estimate = raw[selected,pos*HEADS_PER_REGISTER+h.expert_index(band,sign)]
                    err = np.abs(estimate - true_local[selected,pos])
                    errors.extend(err.tolist())
                    recovered = np.rint(sign*log_decode(estimate,band)).astype(np.int64)
                    success = previous.correctness(recovered[:,None], truth[selected,pos,None])
                    experts[name] = {'rows':int(selected.sum()), 'normalized_mae':float(err.mean()),
                                     'oracle_bucket_5pct_rate':float(success.mean())}
                else:
                    experts[name] = {'rows':0,'normalized_mae':None,'oracle_bucket_5pct_rate':None}
        for i, cut in enumerate(CUTS):
            low = CUTS[i-1] if i else 0
            selected = np.abs(truth[:,pos]) > low
            above = np.abs(truth[selected,pos]) > cut
            classified = raw[selected,pos*HEADS_PER_REGISTER+2+i] > 0
            experts[f'{key}_route_above_{cut}'] = {
                'rows':int(selected.sum()),
                'accuracy':float((classified == above).mean()) if selected.any() else None}
        experts[key+'_zero'] = {'rows':len(truth), 'accuracy':float((zeros[:,pos]==true_zero).mean()),
                                'recall':result[key+'_zero_recall']}
        experts[key+'_sign'] = {'rows':int(nz.sum()),'accuracy':result[key+'_sign_accuracy']}
    result['heads'] = experts
    result['true_bucket_expert_normalized_mae'] = float(np.mean(errors)) if errors else None
    # Full evaluation loss, including the same constraint terms as training.
    losses = []
    for i in range(0,len(raw),batch_size):
        labels, bands, local = supervision(truth[i:i+batch_size], device)
        r = torch.as_tensor(raw[i:i+batch_size],dtype=torch.float32,device=device)
        with torch.no_grad():
            val, extra = constrained_loss(r, labels, bands, local, network,
                range_weight=range_weight, sharing_weight=sharing_weight,
                continuity_weight=continuity_weight, boundary_window=boundary_window,
                stage=stage)
        losses.append((float(val),len(r)))
    result['head_validation_loss'] = sum(value*n for value,n in losses)/sum(n for _,n in losses)
    result['near_boundary_examples'] = int(sum((((truth != 0) &
        (np.abs(np.log(np.maximum(np.abs(truth), 1)) - math.log(cut)) <= boundary_window))).sum()
        for cut in CUTS))
    return result


def config_for(args, manifest):
    config = h.config_for(args, manifest)
    config.update(schema=SCHEMA,
                  objective='stage5_38_bounded_coupled_continuous_v1',
                  constraint_stage=args.constraint_stage, rank=args.rank,
                  residual_scale=args.residual_scale, range_weight=args.range_weight,
                  sharing_weight=args.sharing_weight,
                  continuity_weight=args.continuity_weight,
                  boundary_window=args.boundary_window)
    return config


def load_checkpoint(path, model, optimizer, stream, config, identity):
    # h.save/load validates h.SCHEMA. We use a separate schema so neither
    # experiment can accidentally resume the other's incompatible weights.
    ck = torch.load(path,map_location='cpu',weights_only=True)
    if ck.get('schema') != SCHEMA or ck.get('config') != config or ck.get('backbone_identity') != identity:
        raise RuntimeError('CONSTRAINED_RESUME_CONFIG_OR_BACKBONE_DRIFT')
    model.load_state_dict(ck['model'])
    optimizer.load_state_dict(ck['optimizer'])
    stream.load_state_dict(ck['random_stream'])
    torch.set_rng_state(ck['torch_rng'].cpu().to(dtype=torch.uint8))
    if 'cuda_rng' in ck and torch.cuda.is_available():
        torch.cuda.set_rng_state_all([x.cpu().to(dtype=torch.uint8) for x in ck['cuda_rng']])
    return int(ck['step']),dict(ck['best_validation'])


def atomic_checkpoint(path, model, optimizer, step, config, stream, best, identity):
    obj = {'schema':SCHEMA,'config':config,'step':step,
           'model':model.state_dict(),'optimizer':optimizer.state_dict(),
           'random_stream':stream.state_dict(),'torch_rng':torch.get_rng_state(),
           'best_validation':best,'backbone_identity':identity}
    if torch.cuda.is_available():
        obj['cuda_rng'] = torch.cuda.get_rng_state_all()
    tmp = path.with_name(path.name+'.partial')
    torch.save(obj,tmp)
    os.replace(tmp,path)


def baseline_report(root, config, identity, evaluation, device):
    """Optional read-only comparison against the original independent experts."""
    root = Path(root).expanduser().resolve()
    path = root/'best.pt'
    if not path.is_file():
        raise FileNotFoundError('CONSTRAINED_BASELINE_CHECKPOINT_MISSING')
    baseline_config = json.loads((root/'run_config.json').read_text(encoding='utf-8'))
    if baseline_config['benchmark_sha256'] != config['benchmark_sha256']:
        raise RuntimeError('CONSTRAINED_BASELINE_BENCHMARK_DRIFT')
    baseline = h.model_factory(torch).to(device)
    optim = torch.optim.AdamW(baseline.parameters(),lr=baseline_config['lr'],
                              weight_decay=baseline_config['weight_decay'])
    # Inspect original checkpoint read-only; its saved generator is untouched.
    ck = torch.load(path,map_location='cpu',weights_only=True)
    if ck.get('schema') != h.SCHEMA or ck.get('config') != baseline_config or ck.get('backbone_identity') != identity:
        raise RuntimeError('CONSTRAINED_BASELINE_BACKBONE_DRIFT')
    baseline.load_state_dict(ck['model'])
    result = h.score(torch,baseline,evaluation,device)
    emit('constrained_baseline_comparison', baseline_checkpoint=str(path),
         baseline_step=int(ck['step']), baseline_joint=result['joint_pass_rate'],
         baseline_x=result['x_pass_rate'], baseline_y=result['y_pass_rate'],
         baseline_oracle_joint=result['oracle_routing_joint_pass_rate'])
    return result


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--self-test',action='store_true')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--eval-only',action='store_true')
    p.add_argument('--v3-root',type=Path,default=previous.DEFAULT_V3)
    p.add_argument('--results-root',type=Path,default=DEFAULT_ROOT)
    p.add_argument('--baseline-results-root',type=Path,default=None)
    p.add_argument('--constraint-stage',choices=STAGES,default='continuity')
    p.add_argument('--rank',type=int,default=8)
    p.add_argument('--residual-scale',type=float,default=.25)
    p.add_argument('--range-weight',type=float,default=.05)
    p.add_argument('--sharing-weight',type=float,default=.02)
    p.add_argument('--continuity-weight',type=float,default=.1)
    p.add_argument('--boundary-window',type=float,default=.12)
    p.add_argument('--steps',type=int,default=5000)
    p.add_argument('--batch-size',type=int,default=32)
    p.add_argument('--lr',type=float,default=.0003)
    p.add_argument('--weight-decay',type=float,default=.01)
    p.add_argument('--replay-updates',type=int,default=2)
    p.add_argument('--zero-positive-weight',type=float,default=2.)
    p.add_argument('--sampler',choices=('balanced','legacy'),default='balanced')
    p.add_argument('--seed',type=int,default=20261015)
    p.add_argument('--eval-every',type=int,default=100)
    p.add_argument('--checkpoint-every',type=int,default=100)
    p.add_argument('--report-every',type=int,default=50)
    p.add_argument('--val-examples',type=int,default=512)
    p.add_argument('--max-prompt-tokens',type=int,default=256)
    p.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    p.add_argument('--dtype',choices=('float16','float32','bfloat16'),default='float16')
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--threads',type=int,default=2)
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    if args.self_test:
        self_test();return 0
    if (args.batch_size<1 or args.steps<1 or args.replay_updates<1 or args.eval_every<1 or
        args.checkpoint_every<1 or args.report_every<1 or args.threads<1 or
        args.val_examples<1 or args.max_prompt_tokens<h.SUFFIX or args.rank<1 or
        args.residual_scale<=0 or args.zero_positive_weight<=0 or
        args.range_weight<0 or args.sharing_weight<0 or args.continuity_weight<0 or
        args.boundary_window<=0):
        raise ValueError('CONSTRAINED_INVALID_ARGUMENTS')
    torch.set_num_threads(args.threads)
    if args.device=='cuda' and not torch.cuda.is_available():
        raise RuntimeError('CONSTRAINED_CUDA_UNAVAILABLE')
    device=torch.device(args.device)
    manifest,tasks=previous.read_benchmark(args.v3_root)
    identity=json.loads((args.v3_root/'model_identity.json').read_text(encoding='utf-8'))
    if identity.get('model')!='qwen':
        raise RuntimeError('CONSTRAINED_EXPECTS_QWEN')
    config=config_for(args,manifest)
    root=args.results_root.expanduser().resolve()
    source=args.v3_root.expanduser().resolve()
    if root==source or source in root.parents:
        raise RuntimeError('CONSTRAINED_ROOT_OVERWRITES_BENCHMARK')
    root.mkdir(parents=True,exist_ok=True)
    path=root/'run_config.json'
    if path.exists():
        if json.loads(path.read_text(encoding='utf-8'))!=config:
            raise RuntimeError('CONSTRAINED_EXISTING_CONFIG_DRIFT')
    else:
        if any(root.iterdir()):
            raise RuntimeError('CONSTRAINED_EXISTING_NONEMPTY_RESULTS_ROOT')
        atomic_json(path,config)
    torch.manual_seed(args.seed)
    model=model_factory(args.constraint_stage,args.rank,args.residual_scale).to(device)
    optim=torch.optim.AdamW(model.parameters(),lr=args.lr,weight_decay=args.weight_decay)
    stream=h.make_stream(args.seed,tasks,args.sampler)
    emit('constrained_decoder_ready', stage=args.constraint_stage,
         parameter_count=sum(p.numel() for p in model.parameters()), head_count=38,
         frozen_backbone='Qwen3-0.6B', trainable_backbone_parameters=0,
         benchmark_prompts=len(tasks),heldout_operations=list(v3.HELD_OUT_OPERATIONS))
    latest,best_path=root/'latest.pt',root/'best.pt'
    best={'joint':-1.,'loss':float('inf')}
    kw=dict(stage=args.constraint_stage,range_weight=args.range_weight,
            sharing_weight=args.sharing_weight,continuity_weight=args.continuity_weight,
            boundary_window=args.boundary_window)
    if args.eval_only:
        if not best_path.is_file():
            raise FileNotFoundError('CONSTRAINED_BEST_CHECKPOINT_NOT_FOUND')
        step,_=load_checkpoint(best_path,model,optim,stream,config,identity)
        for split in ('val','test','control'):
            evaluation=previous.load_eval(source,manifest,tasks,split)
            report=score(model,evaluation,device,**kw)
            if args.baseline_results_root is not None:
                base=baseline_report(args.baseline_results_root,config,identity,evaluation,device)
                report['baseline_joint_pass_rate']=base['joint_pass_rate']
                report['joint_delta_vs_baseline']=report['joint_pass_rate']-base['joint_pass_rate']
                report['oracle_joint_delta_vs_baseline']=(report['oracle_routing_joint_pass_rate'] -
                                                         base['oracle_routing_joint_pass_rate'])
            emit('constrained_final_evaluation',split=split,step=step,**report)
        return 0
    if latest.is_file() and not args.resume:
        raise RuntimeError('CONSTRAINED_USE_--resume_FOR_EXISTING_CHECKPOINT')
    step=0
    if args.resume:
        if not latest.is_file():
            raise FileNotFoundError('CONSTRAINED_RESUME_CHECKPOINT_MISSING')
        step,best=load_checkpoint(latest,model,optim,stream,config,identity)
        emit('constrained_resumed',step=step,best_validation=best)
    val=previous.load_eval(source,manifest,tasks,'val',args.val_examples)
    baseline_val=None
    if args.baseline_results_root is not None:
        baseline_val=baseline_report(args.baseline_results_root,config,identity,val,device)
    import nanojev_numerate_spine_cassette_smoke as cassette
    _,frozen,tokenizer,source_device,model_info=cassette.load_source('qwen',args)
    if (model_info.get('resolved_commit')!=identity['model_info'].get('resolved_commit') or
        model_info.get('source')!=identity['model_info'].get('source') or
        str(model_info.get('dtype'))!=str(identity['model_info'].get('dtype'))):
        raise RuntimeError('CONSTRAINED_BACKBONE_IDENTITY_DRIFT')
    frozen.eval()
    previous.verify_capture_alignment(torch,frozen,tokenizer,source_device,source,tasks,args.max_prompt_tokens)
    started=time.monotonic()
    cumulative_capture=cumulative_update=0.
    interval_loss=[]
    for k in range(step+1,args.steps+1):
        t0=time.monotonic()
        batch=stream.batch(args.batch_size)
        features=previous.extract_batched(torch,frozen,tokenizer,
                   [row['prompt'] for row in batch],source_device,args.max_prompt_tokens)
        if features.requires_grad:
            raise RuntimeError('CONSTRAINED_BACKBONE_HAS_GRADIENTS')
        labels,bands,local=supervision([[row['a'],row['b']] for row in batch],device)
        if device.type=='cuda':torch.cuda.synchronize()
        cumulative_capture+=time.monotonic()-t0
        t1=time.monotonic()
        for _ in range(args.replay_updates):
            model.train()
            raw=model(features)
            L,diagnostics=constrained_loss(raw,labels,bands,local,model,
                zero_positive_weight=args.zero_positive_weight,**kw)
            optim.zero_grad(set_to_none=True)
            L.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(),1.)
            optim.step()
        interval_loss.append(float(L.detach()))
        if device.type=='cuda':torch.cuda.synchronize()
        cumulative_update+=time.monotonic()-t1
        if k%args.report_every==0 or k==args.steps:
            elapsed=time.monotonic()-started
            emit('constrained_train_progress',step=k,
                 unique_generated_pairs=len(stream.seen),
                 mean_head_loss=float(np.mean(interval_loss)),
                 active_supervised_heads=diagnostics['active_heads'],
                 boundary_rows_in_last_batch=diagnostics['boundary_rows'],
                 frozen_qwen_seconds=round(cumulative_capture,2),
                 decoder_update_seconds=round(cumulative_update,2),
                 elapsed_seconds=round(elapsed,2),
                 new_pairs_per_second=round((k-step)*args.batch_size/max(elapsed,1e-9),2))
            interval_loss.clear()
        if k%args.eval_every==0 or k==args.steps:
            validation=score(model,val,device,**kw)
            if baseline_val is not None:
                validation['baseline_joint_pass_rate']=baseline_val['joint_pass_rate']
                validation['joint_delta_vs_baseline']=(validation['joint_pass_rate'] -
                                                      baseline_val['joint_pass_rate'])
                validation['oracle_joint_delta_vs_baseline']=(validation['oracle_routing_joint_pass_rate'] -
                                                      baseline_val['oracle_routing_joint_pass_rate'])
            emit('constrained_validation',step=k,**validation)
            if h.better_checkpoint(validation,best):
                best={'joint':validation['joint_pass_rate'],
                      'loss':validation['head_validation_loss']}
                atomic_checkpoint(best_path,model,optim,k,config,stream,best,identity)
                emit('constrained_best_checkpoint',step=k,
                     validation_joint=best['joint'],validation_loss=best['loss'],
                     path=str(best_path))
        if k%args.checkpoint_every==0 or k==args.steps:
            atomic_checkpoint(latest,model,optim,k,config,stream,best,identity)
            emit('constrained_checkpoint',step=k,path=str(latest))
    emit('constrained_training_complete',step=args.steps,
         generated_pairs=len(stream.seen),best_validation=best,
         results_root=str(root))
    return 0


def self_test():
    assert len(CUTS)==5 and HEADS_PER_REGISTER==19
    samples=np.array([[0,1],[20,21],[-200,201],[-2_000_000,2_000_000]])
    labels,bands,local=supervision(samples,'cpu')
    raw=np.zeros((len(samples),2,19),dtype=np.float32)
    for i in range(len(samples)):
        for p in range(2):
            n=int(samples[i,p])
            if not n:continue
            b=int(bands[i,p]); raw[i,p,h.expert_index(b,1 if n>0 else -1)]=local[i,p]
    assert np.array_equal(decode(raw,oracle_numbers=samples),samples)
    for stage in STAGES:
        net=model_factory(stage)
        x=torch.randn(8,INPUT_WIDTH)
        nums=np.array([[20,21],[-200,201],[2000,-2001],[-200000,200001]]*2)
        lbl,band,loc=supervision(nums,'cpu')
        out=net(x)
        assert out.shape==(8,38)
        loss,diagnostics=constrained_loss(out,lbl,band,loc,net,stage=stage)
        loss.backward()
        assert math.isfinite(float(loss.detach()))
        assert diagnostics['active_heads']>10
    emit('constrained_self_test_pass',stages=list(STAGES),head_count=38)


if __name__=='__main__':
    raise SystemExit(main())
