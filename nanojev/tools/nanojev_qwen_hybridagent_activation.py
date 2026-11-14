#!/usr/bin/env python3
"""Bounded, resumable HybridSFT HybridLink-activation experiment for Qwen3-0.6B.

Replicates the released teacher-forced latent alignment loss, not HybridSFT
format activation or self-conditioned joint training. Backbone and embedding
rows are frozen. Small deterministic samples are a *training mechanics* probe.

Training checkpoints and optimizer state are separate from the base adapter;
no baseline or production artifacts are ever overwritten by this script.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys

import torch
import torch.nn.functional as F

from tools import nanojev_qwen_hybridagent as hybrid

SCHEMA = 'nanojev.qwen3-06b.hybridlink-activation-probe.v1'
DATA_VERSION = 'zero-cancellation-teacher-latent-v1'
DEFAULT_RUN = Path.home() / 'NanoJev' / 'runs' / 'qwen3_06b_base_hybridlink_activation_probe_v1'
BASELINE = hybrid.DEFAULT_OUTPUT
COS_WEIGHT = 1.0
MSE_WEIGHT = 0.2
EXIT_WEIGHT = 1.0


def examples():
    """Precommitted disjoint teacher-text trajectories; NOT oracle-blind evaluation."""
    a_train = [7, 11, 19, 27, 35, 43, 53, 65, 79, 91, 109, 127]
    b_train = [3, -4, 8, -9, 2, -6, 11, 5, -3, 7, -8, 4]
    a_dev = [137, 149, 163, 181]
    b_dev = [6, -7, 9, -5]
    def make(a, b):
        return {
            'question': f'Compute {a} + (-{a}) + ({b}).',
            'latent': f'Cancel {a} with -{a} to get zero. Add {b} to get {b}.',
            'answer': str(b),
            'a': a, 'b': b,
        }
    return [make(a, b) for a, b in zip(a_train, b_train)], [make(a, b) for a, b in zip(a_dev, b_dev)]


def data_fingerprint():
    train, dev = examples()
    return hashlib.sha256(json.dumps({'version': DATA_VERSION, 'train': train, 'dev': dev},
                                      sort_keys=True).encode('utf-8')).hexdigest()


def make_trajectory(tokenizer, sample, max_tokens=256):
    """Positionally correct latent source mask: entry and payload -> next token.

    Last payload token regresses to the </latent> exit token. Following text is
    present for teacher context but is not a training label in this phase.
    """
    def enc(text):
        return tokenizer(text, add_special_tokens=False)['input_ids']
    prefix = enc('Question: ' + sample['question'] + '\nReasoning: ')
    entry = enc(hybrid.START)
    thought = enc(sample['latent'])
    exit_ = enc(hybrid.END)
    suffix = enc(' Answer: ' + sample['answer'])
    assert len(entry) == len(exit_) == 1, 'ACTIVATION_MARKERS_NOT_ATOMIC'
    assert len(prefix) >= 1 and len(thought) >= 1 and len(suffix) >= 1, 'ACTIVATION_EMPTY_SPAN'
    assert entry[0] != exit_[0], 'ACTIVATION_MARKER_COLLISION'
    ids = prefix + entry + thought + exit_ + suffix
    assert len(ids) <= max_tokens, 'ACTIVATION_TRAJECTORY_TOO_LONG'
    mask = [0] * len(prefix) + [1] * (1 + len(thought)) + [0] * (1 + len(suffix))
    assert len(mask) == len(ids), 'ACTIVATION_MASK_LENGTH_ERROR'
    src = [i for i in range(len(ids) - 1) if mask[i]]
    exits = [int(ids[i + 1] == exit_[0]) for i in src]
    assert len(src) == 1 + len(thought) and sum(exits) == 1, 'ACTIVATION_EXIT_MASK_WRONG'
    assert all(ids[i + 1] == thought[0] if i == len(prefix) else True for i in src), (
        'ACTIVATION_FIRST_LATENT_TARGET_WRONG')
    assert src[-1] + 1 == len(prefix) + 1 + len(thought), 'ACTIVATION_EXIT_POSITION_WRONG'
    return ids, src, exits


def capture_pairs(model, tokenizer, samples, device, *, max_tokens=256):
    """Teacher-forced *actual Qwen* hidden states and next-token embedding rows.

    All Qwen computations run under no_grad; no training gradient graph lives in
    the backbone. Output stored on CPU to cap GPU allocation during training.
    """
    if not samples:
        raise AssertionError('ACTIVATION_EMPTY_DATASET')
    sources, targets, exit_masks = [], [], []
    assert not any(p.requires_grad for p in model.backbone.parameters()), (
        'ACTIVATION_BACKBONE_NOT_FROZEN')
    model.backbone.eval()
    with torch.no_grad():
        for sample in samples:
            ids, source_positions, exits = make_trajectory(tokenizer, sample, max_tokens)
            ids_t = torch.tensor([ids], device=device, dtype=torch.long)
            out = model.backbone(input_ids=ids_t, attention_mask=torch.ones_like(ids_t),
                                 use_cache=False, output_hidden_states=True, return_dict=True)
            hidden = out.hidden_states[-1][0]
            emb = model.backbone.get_input_embeddings()(ids_t)[0]
            positions = torch.tensor(source_positions, dtype=torch.long, device=device)
            sources.append(hidden.index_select(0, positions).to('cpu', dtype=torch.float32))
            targets.append(emb.index_select(0, positions + 1).to('cpu', dtype=torch.float32))
            exit_masks.extend(exits)
            del out, hidden, emb
    x = torch.cat(sources, 0).contiguous()
    y = torch.cat(targets, 0).contiguous()
    exit_t = torch.tensor(exit_masks, dtype=torch.bool)
    assert x.shape == y.shape and x.ndim == 2, 'ACTIVATION_PAIR_SHAPE_DRIFT'
    assert len(exit_t) == x.shape[0] and exit_t.any(), 'ACTIVATION_EXIT_LABELS_MISSING'
    assert torch.isfinite(x).all() and torch.isfinite(y).all(), 'ACTIVATION_TEACHER_NONFINITE'
    return x, y, exit_t


def loss_components(head, x, y, is_exit):
    """Official HybridSFT activation loss, including second exit-anchor weight.

    L = mean(1-cos) + .2*mean(MSE) + mean_exit(1-cos) + .2*mean_exit(MSE)
    """
    assert x.ndim == 2 and x.shape == y.shape and is_exit.shape == x.shape[:1], (
        'ACTIVATION_LOSS_INPUT_MISMATCH')
    if len(x) == 0:
        raise AssertionError('ACTIVATION_EMPTY_BATCH')
    pred = head(x).float()
    y = y.float()
    cos = 1.0 - F.cosine_similarity(pred, y, dim=-1)
    mse = (pred - y).square().mean(dim=-1)
    base = COS_WEIGHT * cos.mean() + MSE_WEIGHT * mse.mean()
    if is_exit.any():
        exit_loss = EXIT_WEIGHT * (COS_WEIGHT * cos[is_exit].mean() + MSE_WEIGHT * mse[is_exit].mean())
    else:
        exit_loss = base.new_zeros(())
    total = base + exit_loss
    assert bool(torch.isfinite(total)), 'ACTIVATION_LOSS_NONFINITE'
    return {'loss': total, 'cos': cos.mean(), 'mse': mse.mean(),
            'exit_loss': exit_loss, 'exit_count': int(is_exit.sum().item())}


def evaluate(head, cache, device, *, chunk_size=256):
    xs, ys, exits = cache
    weighted = {'loss': 0.0, 'cos': 0.0, 'mse': 0.0}
    sum_exit = 0.0
    count = 0
    exits_total = 0
    # Normalize exit penalty over all exit examples, not each chunk's local mean.
    head.eval()
    with torch.no_grad():
        for first in range(0, len(xs), chunk_size):
            stop = min(first + chunk_size, len(xs))
            part = loss_components(head, xs[first:stop].to(device), ys[first:stop].to(device),
                                   exits[first:stop].to(device))
            size = stop - first
            count += size
            for key in ('cos', 'mse'):
                weighted[key] += part[key].item() * size
            if part['exit_count']:
                sum_exit += float(part['exit_loss'].item()) * part['exit_count']
                exits_total += part['exit_count']
    assert exits_total > 0, 'ACTIVATION_NO_EXIT_TARGETS_IN_EVAL'
    result = {key: weighted[key] / count for key in ('cos', 'mse')}
    result['exit_loss'] = sum_exit / exits_total
    result['loss'] = result['cos'] * COS_WEIGHT + result['mse'] * MSE_WEIGHT + result['exit_loss']
    return result


def tensor_hash(module):
    """Whole-backbone integrity fingerprint with bounded per-parameter CPU copies."""
    h = hashlib.sha256()
    with torch.no_grad():
        for name, param in module.named_parameters():
            h.update(name.encode('utf-8'))
            h.update(str(tuple(param.shape)).encode('ascii'))
            h.update(str(param.dtype).encode('ascii'))
            arr = param.detach().contiguous().to('cpu').numpy()
            h.update(arr.tobytes())
    return h.hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for data in iter(lambda: f.read(1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def write_json_atomic(path: Path, obj):
    tmp = path.with_name(path.name + '.partial')
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def save_pt_atomic(path: Path, obj):
    tmp = path.with_name(path.name + '.partial')
    torch.save(obj, tmp)
    os.replace(tmp, path)


def cpu_state(module):
    return {key: value.detach().cpu().clone() for key, value in module.state_dict().items()}


def model_signature(details, baseline_dir: Path, *, steps, batch_size, lr, seed, max_tokens, eval_every):
    return {
        'schema': SCHEMA, 'data_version': DATA_VERSION, 'data_hash': data_fingerprint(),
        'baseline_dir': str(baseline_dir.resolve()),
        'baseline_adapter_hash': file_hash(baseline_dir / 'adapter.pt'),
        'baseline_config_hash': file_hash(baseline_dir / 'adapter_config.json'),
        'base_model': details['base_model'], 'revision': details['revision'],
        'adapter_schema': hybrid.SCHEMA, 'hidden_size': details['hidden_size'],
        'steps': int(steps), 'batch_size': int(batch_size), 'lr': float(lr),
        'seed': int(seed), 'max_tokens': int(max_tokens), 'eval_every': int(eval_every),
        'objective': 'cos(1.0)+mse(0.2)+exit[cos(1.0)+mse(0.2)]',
        'stage': 'teacher_forced_hybridlink_activation_only',
        'format_activation_preceded': False,
        'optimizer': 'AdamW betas=(0.9,0.95),weight_decay=0,grad_clip=1',
        'lr_schedule': 'constant for bounded smoke (published default LR 2e-4)',
    }


def verify_optimizer_scope(model, optimizer):
    assert all(not p.requires_grad for p in model.backbone.parameters()), 'ACTIVATION_BACKBONE_TRAINABLE'
    b_ids = {id(p) for p in model.backbone.parameters()}
    trainable_ids = {id(p) for p in model.head.parameters() if p.requires_grad}
    optim_ids = {id(p) for group in optimizer.param_groups for p in group['params']}
    assert trainable_ids and optim_ids == trainable_ids, 'ACTIVATION_OPTIMIZER_SCOPE_DRIFT'
    assert not (optim_ids & b_ids), 'ACTIVATION_OPTIMIZER_INCLUDES_BACKBONE'
    return len(trainable_ids)


def activation_run(*, device='cuda', baseline=BASELINE, output=DEFAULT_RUN, steps=80,
                   batch_size=32, lr=2e-4, seed=2604, eval_every=10,
                   max_tokens=256, stop_after=None, resume=False, allow_download=False,
                   model_loader=None):
    if steps < 2 or steps > 2000 or batch_size < 1 or batch_size > 512:
        raise ValueError('ACTIVATION_INVALID_STEP_OR_BATCH_SIZE')
    if not 0 < lr <= 1e-2 or eval_every < 1 or max_tokens < 32 or max_tokens > 1024:
        raise ValueError('ACTIVATION_INVALID_LEARNING_RATE_OR_EVALUATION')
    if stop_after is not None and (stop_after < 1 or stop_after > steps):
        raise ValueError('ACTIVATION_INVALID_STOP_AFTER')
    if baseline.resolve() == output.resolve():
        raise ValueError('ACTIVATION_OUTPUT_COLLIDES_WITH_BASELINE')
    if not resume and output.exists() and any(output.iterdir()):
        raise FileExistsError('ACTIVATION_REFUSES_EXISTING_OUTPUT:' + str(output))
    if resume and not (output / 'last.pt').is_file():
        raise FileNotFoundError('ACTIVATION_RESUME_CHECKPOINT_MISSING')
    torch.manual_seed(seed)
    if device == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('ACTIVATION_CUDA_UNAVAILABLE')
    if device == 'auto':
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
    if device.startswith('cuda'):
        torch.cuda.reset_peak_memory_stats()
    baseline_before = {name: file_hash(baseline / name) for name in ('adapter.pt','adapter_config.json')}
    loader = model_loader or hybrid.load_qwen
    # Never download except by explicit opt-in; matched to the pinned baseline metadata.
    model, tokenizer, details = loader(device=device, allow_download=allow_download)
    config = hybrid.load_adapter(baseline, model, details)
    assert config.get('schema') == hybrid.SCHEMA, 'ACTIVATION_REQUIRES_PAPER_HEAD_V2'
    assert config.get('base_model') == 'Qwen/Qwen3-0.6B-Base', 'ACTIVATION_REQUIRES_BASE_NOT_INSTRUCT'
    assert config.get('training_status') == 'untrained', 'ACTIVATION_REQUIRES_UNTRAINED_BASELINE'
    model.backbone.eval()
    model.backbone.requires_grad_(False)
    model.head.to(device=device, dtype=torch.float32)
    model.head.train()
    for p in model.head.parameters():
        p.requires_grad_(True)
    signature = model_signature(details, baseline, steps=steps, batch_size=batch_size, lr=lr,
                                seed=seed, max_tokens=max_tokens, eval_every=eval_every)
    if resume:
        previous = json.loads((output / 'run_config.json').read_text(encoding='utf-8'))
        assert previous == signature, 'ACTIVATION_RESUME_CONFIG_OR_BASELINE_DRIFT'
    backbone_before = tensor_hash(model.backbone)
    train_ex, dev_ex = examples()
    assert not ({(x['a'], x['b']) for x in train_ex} & {(x['a'], x['b']) for x in dev_ex}), (
        'ACTIVATION_TRAIN_DEV_OVERLAP')
    train = capture_pairs(model, tokenizer, train_ex, device, max_tokens=max_tokens)
    dev = capture_pairs(model, tokenizer, dev_ex, device, max_tokens=max_tokens)
    optimizer = torch.optim.AdamW(model.head.parameters(), lr=lr, weight_decay=0, betas=(0.9, 0.95))
    verify_optimizer_scope(model, optimizer)
    step = 0
    best_dev = float('inf')
    init_train = evaluate(model.head, train, device)
    init_dev = evaluate(model.head, dev, device)
    if resume:
        checkpoint = torch.load(output / 'last.pt', map_location='cpu', weights_only=True)
        assert checkpoint['schema'] == SCHEMA, 'ACTIVATION_RESUME_SCHEMA_DRIFT'
        assert checkpoint['config_sha256'] == hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest(), (
            'ACTIVATION_RESUME_SIGNATURE_DRIFT')
        model.head.load_state_dict(checkpoint['head'], strict=True)
        optimizer.load_state_dict(checkpoint['optimizer'])
        step = int(checkpoint['step'])
        assert 1 <= step <= steps, 'ACTIVATION_RESUME_BAD_STEP'
        best_dev = float(checkpoint['best_dev'])
        init_train = checkpoint['initial_train']
        init_dev = checkpoint['initial_dev']
    else:
        output.mkdir(parents=True, exist_ok=True)
        write_json_atomic(output / 'run_config.json', signature)
    head_before = cpu_state(model.head)
    report = {'schema': SCHEMA, 'status': 'TRAINING', 'initial_train': init_train,
              'initial_dev': init_dev, 'baseline_backbone_hash': backbone_before,
              'train_pairs': int(len(train[0])), 'dev_pairs': int(len(dev[0])),
              'train_exit_pairs': int(train[2].sum()), 'dev_exit_pairs': int(dev[2].sum()),
              'start_step': step, 'total_steps': steps, 'optimizer_steps_this_invocation': 0,
              'baseline_mutations': 0, 'frozen_backbone': True}
    horizon = min(stop_after, steps) if stop_after is not None else steps
    while step < horizon:
        offset = (step * batch_size) % len(train[0])
        ix = torch.tensor([(offset + i) % len(train[0]) for i in range(batch_size)], dtype=torch.long)
        x = train[0][ix].to(device)
        y = train[1][ix].to(device)
        e = train[2][ix].to(device)
        model.head.train()
        optimizer.zero_grad(set_to_none=True)
        loss = loss_components(model.head, x, y, e)
        loss['loss'].backward()
        grads = [p.grad for p in model.head.parameters() if p.requires_grad]
        assert all(g is not None and torch.isfinite(g).all() for g in grads), 'ACTIVATION_MISSING_OR_NONFINITE_HEAD_GRAD'
        assert any(float(g.float().abs().sum()) > 0.0 for g in grads), 'ACTIVATION_ZERO_GRADIENT'
        assert not any(p.grad is not None for p in model.backbone.parameters()), 'ACTIVATION_BACKBONE_RECEIVED_GRADIENT'
        torch.nn.utils.clip_grad_norm_(model.head.parameters(), 1.0)
        optimizer.step()
        step += 1
        report['optimizer_steps_this_invocation'] += 1
        if step % eval_every == 0 or step in (1, horizon, steps):
            now_train = evaluate(model.head, train, device)
            now_dev = evaluate(model.head, dev, device)
            assert math.isfinite(now_train['loss']) and math.isfinite(now_dev['loss']), (
                'ACTIVATION_EVAL_NONFINITE')
            if now_dev['loss'] < best_dev:
                best_dev = now_dev['loss']
                save_pt_atomic(output / 'best_head.pt', {
                    'schema': SCHEMA, 'step': step, 'head': cpu_state(model.head),
                    'dev': now_dev, 'training_stage': 'activation_only',
                    'adaptive_switching_certified': False,
                })
            checkpoint = {
                'schema': SCHEMA, 'step': step, 'head': cpu_state(model.head),
                'optimizer': optimizer.state_dict(), 'best_dev': best_dev,
                'initial_train': init_train, 'initial_dev': init_dev,
                'config_sha256': hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest(),
            }
            save_pt_atomic(output / 'last.pt', checkpoint)
            report.update(step=step, current_train=now_train, current_dev=now_dev,
                          best_dev_loss=best_dev, head_changed_from_call_start=any(
                              not torch.equal(v, model.head.state_dict()[k].detach().cpu())
                              for k, v in head_before.items()))
            print('HYBRID_ACTIVATION_STEP=' + json.dumps({
                'step': step, 'total': steps, 'train_loss': now_train['loss'],
                'dev_loss': now_dev['loss'], 'best_dev_loss': best_dev}, sort_keys=True), flush=True)
            write_json_atomic(output / 'result.json', report)
    backbone_after = tensor_hash(model.backbone)
    assert backbone_after == backbone_before, 'ACTIVATION_BACKBONE_WEIGHTS_CHANGED'
    assert all(not p.requires_grad and p.grad is None for p in model.backbone.parameters()), (
        'ACTIVATION_FROZEN_BACKBONE_STATE_BROKEN')
    assert baseline_before == {name: file_hash(baseline / name) for name in baseline_before}, (
        'ACTIVATION_BASELINE_CHECKPOINT_MUTATED')
    report['backbone_hash_preserved'] = True
    report['baseline_mutations'] = 0
    if device.startswith('cuda'):
        report['cuda_peak_allocated_mb'] = round(torch.cuda.max_memory_allocated() / 1024**2, 2)
        report['cuda_peak_reserved_mb'] = round(torch.cuda.max_memory_reserved() / 1024**2, 2)
        report['gpu_total_mb'] = round(torch.cuda.get_device_properties(device).total_memory / 1024**2, 2)
    if step == steps:
        full_train = evaluate(model.head, train, device)
        report['final_train'] = full_train
        assert report['optimizer_steps_this_invocation'] > 0 or resume, 'ACTIVATION_NO_OPTIMIZER_STEPS'
        assert report['head_changed_from_call_start'] or resume, 'ACTIVATION_HEAD_PARAMETERS_UNCHANGED'
        assert full_train['loss'] < init_train['loss'] - 1e-4, (
            'ACTIVATION_LOSS_DID_NOT_DECREASE: ' + str((init_train['loss'],full_train['loss'])))
        report['status'] = 'PASS_MECHANICS_ONLY'
    else:
        report['status'] = 'PAUSED_RESUMABLE'
    write_json_atomic(output / 'result.json', report)
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device', choices=['auto','cpu','cuda'], default='cuda')
    p.add_argument('--baseline', type=Path, default=BASELINE)
    p.add_argument('--output', type=Path, default=DEFAULT_RUN)
    p.add_argument('--steps', type=int, default=80)
    p.add_argument('--batch-size', type=int, default=32)
    p.add_argument('--lr', type=float, default=2e-4)
    p.add_argument('--seed', type=int, default=2604)
    p.add_argument('--eval-every', type=int, default=10)
    p.add_argument('--max-tokens', type=int, default=256)
    p.add_argument('--stop-after', type=int)
    p.add_argument('--resume', action='store_true')
    p.add_argument('--allow-model-download', action='store_true')
    args = p.parse_args(argv)
    try:
        report = activation_run(device=args.device, baseline=args.baseline, output=args.output,
                                steps=args.steps, batch_size=args.batch_size, lr=args.lr,
                                seed=args.seed, eval_every=args.eval_every,
                                max_tokens=args.max_tokens, stop_after=args.stop_after,
                                resume=args.resume, allow_download=args.allow_model_download)
        print('HYBRID_ACTIVATION_RESULT=' + json.dumps(report, sort_keys=True), flush=True)
        return 0 if report['status'] in ('PASS_MECHANICS_ONLY','PAUSED_RESUMABLE') else 2
    except (AssertionError, ValueError, FileNotFoundError, FileExistsError, RuntimeError, OSError) as exc:
        print('HYBRID_ACTIVATION_REFUSED=' + type(exc).__name__ + ': ' + str(exc), file=sys.stderr, flush=True)
        return 2


if __name__ == '__main__':
    sys.exit(main())
