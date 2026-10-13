#!/usr/bin/env python3
"""Online stage-5 numerical register decoder: 38 separately supervised heads.

Fresh XX register prompts -> frozen Qwen3-0.6B -> 4-token suffix at layers
16,28 -> independent zero/sign/routing/expert heads for EACH register.

No backbone training, no application integrations, no LASSO. Reuses the
existing online generator/capture/benchmark helpers without altering them.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import random
import time

import numpy as np

import nanojev_register_online_neural as previous

v3 = previous.v3
emit = previous.emit
atomic_json = previous.atomic_json
LAYER_IDS = previous.LAYER_IDS
SUFFIX = previous.SUFFIX
INPUT_WIDTH = len(LAYER_IDS) * SUFFIX * 1024
MAX_VALUE = v3.MAX_VALUE
CUTS = (20, 200, 2_000, 20_000, 200_000)
REGIMES = len(CUTS) + 1
HEADS_PER_REGISTER = 2 + len(CUTS) + REGIMES * 2
SCHEMA = 'nanojev.register-online-hierarchy.v1'
DEFAULT_ROOT = Path.home() / 'NanoJev' / 'runs' / 'register_online_hierarchy_v1'


def band_of(n):
    """Regime 0: 1..20, ..., regime 5: 200001..2000000."""
    return np.searchsorted(CUTS, np.abs(n), side='left').astype(np.int64)


def band_limits(band):
    if band < 0 or band >= REGIMES:
        raise ValueError('REGISTER_HIERARCHY_BAD_BAND')
    lower = 1 if band == 0 else CUTS[band - 1] + 1
    upper = CUTS[band] if band < len(CUTS) else MAX_VALUE
    return lower, upper


def expert_encode(magnitudes, bands):
    """Each expert learns its OWN unit-scaled local target, not global asinh."""
    values = np.asarray(magnitudes, dtype=np.float64)
    regimes = np.asarray(bands, dtype=np.int64)
    t = np.empty(values.shape, dtype=np.float32)
    for band in range(REGIMES):
        mask = regimes == band
        if not mask.any():
            continue
        lo, hi = band_limits(band)
        if band == 0:
            scaled = (values[mask] - lo) / (hi - lo)
        else:
            scaled = (np.log1p(values[mask]) - math.log1p(lo)) / (
                math.log1p(hi) - math.log1p(lo))
        t[mask] = (2.0 * scaled - 1.0).astype(np.float32)
    return t


def expert_decode(targets, band):
    vals = np.asarray(targets, dtype=np.float64)
    scaled = np.clip((vals + 1) / 2, 0, 1)
    lo, hi = band_limits(band)
    if band == 0:
        return lo + scaled * (hi - lo)
    return np.expm1(math.log1p(lo) + scaled * (math.log1p(hi) - math.log1p(lo)))


def expert_index(band, sign):
    """Indices 7..18; even expert negative, odd expert positive."""
    return 7 + band * 2 + (1 if sign > 0 else 0)


def model_factory(torch):
    # Exactly 38 unrelated linear projections, no shared 32-dimension bottleneck.
    return torch.nn.Sequential(
        torch.nn.LayerNorm(INPUT_WIDTH, elementwise_affine=False),
        torch.nn.Linear(INPUT_WIDTH, 2 * HEADS_PER_REGISTER),
    )


class BalancedFreshPairs(previous.FreshPairs):
    """Balance all 12 signed experts plus exact zero in the infinite stream."""
    def __init__(self, seed, reserved):
        super().__init__(seed, reserved)
        self.bucket_pool = []
        self.value_draws = 0

    def integer(self):
        if not self.bucket_pool:
            self.bucket_pool = list(range(2 * REGIMES + 1))
            self.rng.shuffle(self.bucket_pool)
        choice = self.bucket_pool.pop()
        self.value_draws += 1
        if choice == 0:
            return 0
        sign = -1 if choice % 2 == 1 else 1
        band = (choice - 1) // 2
        lo, hi = band_limits(band)
        # Log-uniform for larger regimes; randomized exact boundaries too.
        if self.rng.random() < .10:
            mag = self.rng.choice([lo, hi])
        elif band == 0:
            mag = self.rng.randint(lo, hi)
        else:
            mag = int(round(math.exp(self.rng.uniform(math.log(lo), math.log(hi)))))
            mag = min(hi, max(lo, mag))
        return sign * mag

    def state_dict(self):
        state = super().state_dict()
        state['bucket_pool'] = list(self.bucket_pool)
        state['value_draws'] = self.value_draws
        return state

    def load_state_dict(self, obj):
        super().load_state_dict(obj)
        pool = obj['bucket_pool']
        if len(pool) != len(set(pool)) or any(v not in range(2 * REGIMES + 1) for v in pool):
            raise RuntimeError('HIERARCHY_BAD_STREAM_BUCKET_STATE')
        self.bucket_pool = list(pool)
        self.value_draws = int(obj['value_draws'])


def make_stream(seed, tasks, sampler):
    reserved = previous.reserved_pairs(tasks)
    return (BalancedFreshPairs(seed, reserved) if sampler == 'balanced'
            else previous.FreshPairs(seed, reserved))


def supervision(torch, numbers, device):
    """True zero, sign, regime, and local expert targets; no learned routing."""
    arr = np.asarray(numbers, dtype=np.int64)
    if arr.ndim != 2 or arr.shape[1] != 2 or np.any(np.abs(arr) > MAX_VALUE):
        raise ValueError('HIERARCHY_TARGET_SHAPE_OR_RANGE')
    magnitude = np.abs(arr)
    bands = band_of(arr)
    local = expert_encode(magnitude, bands)
    return (torch.as_tensor(arr, dtype=torch.int64, device=device),
            torch.as_tensor(bands, dtype=torch.long, device=device),
            torch.as_tensor(local, dtype=torch.float32, device=device))


def hierarchy_loss(torch, raw, label, bands, local, zero_positive_weight=2.0):
    """Supervise each active head independently, then average its loss.

    For route above CUTS[i], only magnitudes > previous cut receive labels;
    for each expert, only numbers of its *true* signed regime receive labels.
    Correct targets are used ONLY for training, never for inference routing.
    """
    import torch.nn.functional as F
    logits = raw.reshape(-1, 2, HEADS_PER_REGISTER).float()
    pieces = []
    active_heads = 0
    for position in range(2):
        n = label[:, position]
        magnitude = n.abs()
        nz = magnitude > 0
        zero = (n == 0).float()
        zero_logits = logits[:, position, 0]
        zero_losses = F.binary_cross_entropy_with_logits(zero_logits, zero, reduction='none')
        zero_weights = torch.where(n == 0, zero_positive_weight, 1.0)
        pieces.append((zero_losses * zero_weights).sum() / zero_weights.sum().clamp_min(1.0))
        active_heads += 1
        if nz.any():
            pieces.append(F.binary_cross_entropy_with_logits(
                logits[nz, position, 1], (n[nz] > 0).float()))
            active_heads += 1
        for i, cut in enumerate(CUTS):
            lo = CUTS[i - 1] if i else 0
            mask = magnitude > lo
            if mask.any():
                pieces.append(F.binary_cross_entropy_with_logits(
                    logits[mask, position, 2 + i], (magnitude[mask] > cut).float()))
                active_heads += 1
        for r in range(REGIMES):
            for sign in (-1, 1):
                mask = nz & (bands[:, position] == r) & ((n > 0) if sign > 0 else (n < 0))
                if mask.any():
                    pred = logits[mask, position, expert_index(r, sign)]
                    target = local[mask, position]
                    pieces.append(F.smooth_l1_loss(pred, target, beta=.15))
                    active_heads += 1
    if not pieces:
        raise RuntimeError('HIERARCHY_EMPTY_HEAD_LOSS')
    return torch.stack(pieces).mean(), active_heads


def predicted_routing(raw):
    """Predicted zero/sign/band without any true-target influence."""
    logits = np.asarray(raw, dtype=np.float32).reshape(-1, 2, HEADS_PER_REGISTER)
    zero = logits[:, :, 0] > 0
    sign = np.where(logits[:, :, 1] >= 0, 1, -1)
    band = np.zeros(logits.shape[:2], dtype=np.int64)
    cont = np.ones_like(zero, dtype=bool)
    for r in range(len(CUTS)):
        yes = cont & (logits[:, :, r + 2] > 0)
        band[yes] = r + 1
        cont = yes
    return zero, sign, band


def decode(raw, oracle_numbers=None):
    """Reconstruct values. Oracle option is diagnostic, never the main score."""
    logits = np.asarray(raw, dtype=np.float32).reshape(-1, 2, HEADS_PER_REGISTER)
    zeros, signs, bands = predicted_routing(logits)
    if oracle_numbers is not None:
        actual = np.asarray(oracle_numbers, dtype=np.int64)
        zeros = actual == 0
        signs = np.where(actual >= 0, 1, -1)
        bands = band_of(actual)
    decoded = np.zeros(logits.shape[:2], dtype=np.int64)
    for pos in range(2):
        for r in range(REGIMES):
            for sign in (-1, 1):
                mask = (~zeros[:, pos]) & (bands[:, pos] == r) & (signs[:, pos] == sign)
                if not mask.any():
                    continue
                z = logits[mask, pos, expert_index(r, sign)]
                estimates = expert_decode(z, r)
                decoded[mask, pos] = np.rint(sign * estimates).astype(np.int64)
    return decoded


def score(torch, network, evaluation, device, batch_size=256):
    x, truth, ops, families = evaluation
    prev_training = network.training
    network.eval()
    outputs = []
    with torch.inference_mode():
        for i in range(0, len(x), batch_size):
            batch = torch.as_tensor(x[i:i + batch_size], dtype=torch.float32, device=device)
            outputs.append(network(batch).float().cpu().numpy())
    network.train(prev_training)
    raw = np.concatenate(outputs, axis=0)
    predicted = decode(raw)
    oracle = decode(raw, oracle_numbers=truth)
    report = previous.summarize_predictions(predicted, truth, ops, families)
    report['oracle_routing_joint_pass_rate'] = float(
        previous.correctness(oracle, truth).all(axis=1).mean())
    report['oracle_routing_x_pass_rate'] = float(previous.correctness(oracle, truth)[:, 0].mean())
    report['oracle_routing_y_pass_rate'] = float(previous.correctness(oracle, truth)[:, 1].mean())
    zeros, signs, regimes = predicted_routing(raw)
    true_bands = band_of(truth)
    nz = truth != 0
    local_tgt = expert_encode(np.abs(truth), true_bands)
    expert_err = []
    per_head = {}
    for pos in range(2):
        mask_nz = nz[:, pos]
        suffix = ('x', 'y')[pos]
        report[suffix + '_sign_accuracy'] = (float((signs[mask_nz, pos] ==
            np.sign(truth[mask_nz, pos])).mean()) if mask_nz.any() else None)
        report[suffix + '_regime_accuracy'] = (float((regimes[mask_nz, pos] ==
            true_bands[mask_nz, pos]).mean()) if mask_nz.any() else None)
        mask_zero = ~mask_nz
        per_head[suffix + '_zero'] = {
            'rows': len(truth),
            'accuracy': float((zeros[:, pos] == mask_zero).mean()),
            'recall': float(zeros[mask_zero, pos].mean()) if mask_zero.any() else None,
        }
        per_head[suffix + '_sign'] = {
            'rows': int(mask_nz.sum()),
            'accuracy': report[suffix + '_sign_accuracy'],
        }
        for cut_idx, cut in enumerate(CUTS):
            lower = CUTS[cut_idx - 1] if cut_idx else 0
            eligible = np.abs(truth[:, pos]) > lower
            truth_above = np.abs(truth[eligible, pos]) > cut
            predicted_above = raw[eligible, pos * HEADS_PER_REGISTER + 2 + cut_idx] > 0
            per_head[f'{suffix}_route_above_{cut}'] = {
                'rows': int(eligible.sum()),
                'accuracy': float(np.mean(predicted_above == truth_above)) if eligible.any() else None,
            }
        report[suffix + '_zero_precision'] = (float(mask_zero[zeros[:, pos]].mean())
            if zeros[:, pos].any() else None)
        report[suffix + '_zero_false_positive_rate'] = (float(zeros[mask_nz, pos].mean())
            if mask_nz.any() else None)
        for r in range(REGIMES):
            for sign in (-1, 1):
                mask = mask_nz & (true_bands[:, pos] == r) & (
                    (truth[:, pos] > 0) if sign > 0 else (truth[:, pos] < 0))
                key = f'{suffix}_expert_{r}_{"pos" if sign > 0 else "neg"}'
                if mask.any():
                    fitted = raw[mask, pos * HEADS_PER_REGISTER + expert_index(r, sign)]
                    absolute_errors = np.abs(fitted - local_tgt[mask, pos])
                    expert_err.extend(absolute_errors.tolist())
                    oracle_value = np.rint(sign * expert_decode(fitted, r)).astype(np.int64)
                    ok = previous.correctness(oracle_value.reshape(-1, 1),
                                              truth[mask, pos].reshape(-1, 1))
                    per_head[key] = {
                        'rows': int(mask.sum()),
                        'normalized_mae': float(np.mean(absolute_errors)),
                        'oracle_bucket_5pct_rate': float(ok.mean()),
                    }
                else:
                    per_head[key] = {'rows': 0, 'normalized_mae': None,
                                     'oracle_bucket_5pct_rate': None}
    report['heads'] = per_head
    report['true_bucket_expert_normalized_mae'] = (float(np.mean(expert_err)) if expert_err else None)
    # Continuous loss gives useful checkpoint ranking before any joint 5% hits.
    losses = []
    with torch.inference_mode():
        for i in range(0, len(raw), batch_size):
            r = torch.as_tensor(raw[i:i + batch_size], dtype=torch.float32)
            labels, bands, local = supervision(torch, truth[i:i + batch_size], 'cpu')
            L, _ = hierarchy_loss(torch, r, labels, bands, local)
            losses.append((float(L), len(r)))
    report['head_validation_loss'] = float(sum(a * n for a, n in losses) / sum(n for _, n in losses))
    return report


def save_checkpoint(torch, path, net, optimizer, step, config, stream, best, identity):
    payload = {'schema': SCHEMA, 'config': config, 'step': step,
               'model': net.state_dict(), 'optimizer': optimizer.state_dict(),
               'random_stream': stream.state_dict(), 'torch_rng': torch.get_rng_state(),
               'best_validation': best, 'backbone_identity': identity}
    if torch.cuda.is_available():
        payload['cuda_rng'] = torch.cuda.get_rng_state_all()
    tmp = path.with_name(path.name + '.partial')
    torch.save(payload, tmp)
    os.replace(tmp, path)


def load_checkpoint(torch, path, net, optimizer, stream, config, identity):
    ck = torch.load(path, map_location='cpu', weights_only=True)
    if ck.get('schema') != SCHEMA or ck.get('config') != config or ck.get('backbone_identity') != identity:
        raise RuntimeError('HIERARCHY_RESUME_CONFIG_OR_BACKBONE_DRIFT')
    net.load_state_dict(ck['model'])
    optimizer.load_state_dict(ck['optimizer'])
    stream.load_state_dict(ck['random_stream'])
    torch.set_rng_state(ck['torch_rng'].cpu().to(dtype=torch.uint8))
    if 'cuda_rng' in ck and torch.cuda.is_available():
        torch.cuda.set_rng_state_all([x.cpu().to(dtype=torch.uint8) for x in ck['cuda_rng']])
    return int(ck['step']), dict(ck['best_validation'])


def config_for(args, manifest):
    return {'objective': 'stage5_38_independently_supervised_heads_v1',
            'input_width': INPUT_WIDTH, 'cuts': list(CUTS), 'heads_per_register': HEADS_PER_REGISTER,
            'lr': args.lr, 'weight_decay': args.weight_decay,
            'batch_size': args.batch_size, 'replay_updates': args.replay_updates,
            'zero_positive_weight': args.zero_positive_weight,
            'sampler': args.sampler, 'seed': args.seed, 'val_examples': args.val_examples,
            'max_prompt_tokens': args.max_prompt_tokens,
            'benchmark_sha256': manifest['tasks_sha256'], 'layers': list(LAYER_IDS),
            'suffix': SUFFIX, 'train_operations': [op[0] for op in v3.OPERATIONS
                     if op[0] not in v3.HELD_OUT_OPERATIONS]}


def better_checkpoint(scores, best):
    """Accuracy primary; continuous supervised loss breaks zero-accuracy ties."""
    return (scores['joint_pass_rate'] > best['joint'] or
            (scores['joint_pass_rate'] == best['joint'] and
             scores['head_validation_loss'] < best['loss']))


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--self-test', action='store_true')
    p.add_argument('--eval-only', action='store_true')
    p.add_argument('--resume', action='store_true')
    p.add_argument('--v3-root', type=Path, default=previous.DEFAULT_V3)
    p.add_argument('--results-root', type=Path, default=DEFAULT_ROOT)
    p.add_argument('--steps', type=int, default=5000)
    p.add_argument('--batch-size', type=int, default=32)
    p.add_argument('--lr', type=float, default=.0003)
    p.add_argument('--weight-decay', type=float, default=.01)
    p.add_argument('--replay-updates', type=int, default=2)
    p.add_argument('--zero-positive-weight', type=float, default=2.)
    p.add_argument('--sampler', choices=('balanced', 'legacy'), default='balanced')
    p.add_argument('--seed', type=int, default=20261015)
    p.add_argument('--eval-every', type=int, default=250)
    p.add_argument('--checkpoint-every', type=int, default=250)
    p.add_argument('--report-every', type=int, default=100)
    p.add_argument('--val-examples', type=int, default=512)
    p.add_argument('--max-prompt-tokens', type=int, default=256)
    p.add_argument('--device', choices=('cuda', 'cpu'), default='cuda')
    p.add_argument('--dtype', choices=('float16', 'float32', 'bfloat16'), default='float16')
    p.add_argument('--allow-model-download', action='store_true')
    p.add_argument('--threads', type=int, default=2)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    if args.self_test:
        self_test()
        return 0
    if (args.batch_size < 1 or args.steps < 1 or args.replay_updates < 1 or
        args.eval_every < 1 or args.checkpoint_every < 1 or args.report_every < 1 or
        args.threads < 1 or args.val_examples < 1 or args.max_prompt_tokens < SUFFIX or
        args.zero_positive_weight <= 0):
        raise ValueError('HIERARCHY_BAD_ARGUMENTS')
    import torch
    torch.set_num_threads(args.threads)
    if args.device == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('HIERARCHY_CUDA_UNAVAILABLE')
    device = torch.device(args.device)
    manifest, tasks = previous.read_benchmark(args.v3_root)
    identity = json.loads((args.v3_root / 'model_identity.json').read_text(encoding='utf-8'))
    if identity.get('model') != 'qwen':
        raise RuntimeError('HIERARCHY_EXPECTS_QWEN')
    config = config_for(args, manifest)
    results = args.results_root.expanduser().resolve()
    source_root = args.v3_root.expanduser().resolve()
    if results == source_root or source_root / 'records' == results or source_root / 'records' in results.parents:
        raise RuntimeError('HIERARCHY_RESULT_ROOT_OVERWRITES_BENCHMARK')
    results.mkdir(parents=True, exist_ok=True)
    cfg_path = results / 'run_config.json'
    if cfg_path.is_file():
        if json.loads(cfg_path.read_text(encoding='utf-8')) != config:
            raise RuntimeError('HIERARCHY_EXISTING_CONFIG_DRIFT')
    else:
        if any(results.iterdir()):
            raise RuntimeError('HIERARCHY_EXISTING_NONEMPTY_RESULTS_ROOT')
        atomic_json(cfg_path, config)
    torch.manual_seed(args.seed)
    network = model_factory(torch).to(device)
    optimizer = torch.optim.AdamW(network.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    stream = make_stream(args.seed, tasks, args.sampler)
    params = sum(p.numel() for p in network.parameters())
    emit('hierarchy_decoder_ready', parameters=params, heads_per_register=HEADS_PER_REGISTER,
         head_count=2 * HEADS_PER_REGISTER, frozen_backbone='Qwen3-0.6B',
         trainable_backbone_parameters=0, heldout_operations=list(v3.HELD_OUT_OPERATIONS),
         benchmark_prompts=len(tasks), sampler=args.sampler)
    latest, best_path = results / 'latest.pt', results / 'best.pt'
    best = {'joint': -1., 'loss': float('inf')}
    if args.eval_only:
        if not best_path.is_file():
            raise FileNotFoundError('HIERARCHY_BEST_CHECKPOINT_NOT_FOUND')
        step, _ = load_checkpoint(torch, best_path, network, optimizer, stream, config, identity)
        for split in ('val', 'test', 'control'):
            evaluation = previous.load_eval(source_root, manifest, tasks, split)
            emit('hierarchy_final_evaluation', split=split, step=step,
                 **score(torch, network, evaluation, device))
        return 0
    if latest.is_file() and not args.resume:
        raise RuntimeError('HIERARCHY_USE_--resume_FOR_EXISTING_CHECKPOINT')
    step = 0
    if args.resume:
        if not latest.is_file():
            raise FileNotFoundError('HIERARCHY_RESUME_CHECKPOINT_MISSING')
        step, best = load_checkpoint(torch, latest, network, optimizer, stream, config, identity)
        emit('hierarchy_resumed', step=step, best_validation=best)
    val = previous.load_eval(source_root, manifest, tasks, 'val', args.val_examples)
    # Only now load the expensive frozen model. Evaluation-only never loads it.
    import nanojev_numerate_spine_cassette_smoke as cassette
    _, frozen, tokenizer, source_device, model_info = cassette.load_source('qwen', args)
    if (model_info.get('resolved_commit') != identity['model_info'].get('resolved_commit') or
        model_info.get('source') != identity['model_info'].get('source') or
        str(model_info.get('dtype')) != str(identity['model_info'].get('dtype'))):
        raise RuntimeError('HIERARCHY_QWEN_BACKBONE_IDENTITY_DRIFT')
    frozen.eval()
    previous.verify_capture_alignment(torch, frozen, tokenizer, source_device,
                                      source_root, tasks, args.max_prompt_tokens)
    started = time.monotonic()
    cumulative_qwen = cumulative_head = 0.
    losses, active = [], []
    for k in range(step + 1, args.steps + 1):
        t0 = time.monotonic()
        rows = stream.batch(args.batch_size)
        features = previous.extract_batched(torch, frozen, tokenizer,
                   [r['prompt'] for r in rows], source_device, args.max_prompt_tokens)
        if features.requires_grad:
            raise RuntimeError('HIERARCHY_FROZEN_BACKBONE_HAS_GRADIENTS')
        numbers, bands, local = supervision(torch, [[r['a'], r['b']] for r in rows], device)
        if device.type == 'cuda':
            torch.cuda.synchronize()
        cumulative_qwen += time.monotonic() - t0
        t1 = time.monotonic()
        for _ in range(args.replay_updates):
            network.train()
            raw = network(features)
            loss, heads_used = hierarchy_loss(torch, raw, numbers, bands, local,
                                             args.zero_positive_weight)
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(network.parameters(), 1.)
            optimizer.step()
        losses.append(float(loss.detach().cpu()))
        active.append(heads_used)
        if device.type == 'cuda':
            torch.cuda.synchronize()
        cumulative_head += time.monotonic() - t1
        if k % args.report_every == 0 or k == args.steps:
            elapsed = time.monotonic() - started
            emit('hierarchy_train_progress', step=k, unique_generated_pairs=len(stream.seen),
                 mean_head_loss=round(float(np.mean(losses)), 6),
                 mean_supervised_heads=round(float(np.mean(active)), 2),
                 elapsed_seconds=round(elapsed, 2),
                 new_pairs_per_second=round((k-step) * args.batch_size/max(elapsed, 1e-9), 2),
                 frozen_qwen_seconds=round(cumulative_qwen, 2),
                 decoder_update_seconds=round(cumulative_head, 2))
            losses.clear(); active.clear()
        if k % args.eval_every == 0 or k == args.steps:
            validation = score(torch, network, val, device)
            emit('hierarchy_validation', step=k, **validation)
            if better_checkpoint(validation, best):
                best = {'joint': validation['joint_pass_rate'],
                        'loss': validation['head_validation_loss']}
                save_checkpoint(torch, best_path, network, optimizer, k, config, stream, best, identity)
                emit('hierarchy_best_checkpoint', step=k, validation_joint=best['joint'],
                     validation_head_loss=best['loss'], path=str(best_path))
        if k % args.checkpoint_every == 0 or k == args.steps:
            save_checkpoint(torch, latest, network, optimizer, k, config, stream, best, identity)
            emit('hierarchy_checkpoint', step=k, path=str(latest))
    emit('hierarchy_training_complete', step=args.steps,
         generated_pairs=len(stream.seen), best_validation=best,
         results_root=str(results))
    return 0


def self_test():
    import torch
    assert HEADS_PER_REGISTER == 19
    net = model_factory(torch)
    assert sum(p.numel() for p in net.parameters()) == 311334
    true = np.array([[0, -2_000_000], [-5, 16], [20, 200], [2000, -200_001]])
    labels, bands, local = supervision(torch, true, 'cpu')
    assert np.array_equal(bands.numpy(), band_of(true))
    assert np.all(np.isfinite(local.numpy()))
    out = net(torch.randn(4, INPUT_WIDTH))
    loss, count = hierarchy_loss(torch, out, labels, bands, local)
    loss.backward()
    assert count > 10 and np.isfinite(float(loss.detach())) and net[1].weight.grad is not None
    # Regression experts reproduce their exact supervised values with oracle routing.
    raw = np.full((len(true), 2, HEADS_PER_REGISTER), -10., dtype=np.float32)
    for i in range(len(true)):
        for p in range(2):
            n = true[i,p]; r = int(band_of(np.array([n]))[0])
            raw[i,p,expert_index(r, int(np.sign(n)) if n else 1)] = float(local[i,p])
    assert np.array_equal(decode(raw, oracle_numbers=true), true)
    emit('hierarchy_self_test_pass', parameter_count=311334, head_count=38,
         gradient_heads=int(torch.count_nonzero(net[1].weight.grad.abs().sum(dim=1))),
         active_supervised_heads=count)


if __name__ == '__main__':
    raise SystemExit(main())
