#!/usr/bin/env python3
"""Independent, from-scratch Gaussian heads for frozen TinyStories and Pythia.

Both heads train on identical oracle-blind visible numerical bindings plus their
own frozen LM activations. No Qwen, loaded Gaussian checkpoint, shared learned
weights, or residual correction. TRAIN optimizes, DEV selects, TEST audits once.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import shutil
from pathlib import Path
import subprocess
import sys
import time

import torch
from torch import nn

from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as reference
from tools import nanojev_hawk_dove_gaussian_population_conditional as base
from tools import nanojev_numerate_spine_cassette_smoke as cassette

SCHEMA = 'nanojev.hawk-dove.gaussian-two-backbone-independent.v1'
BACKBONES = ('tinystories', 'pythia')
DEFAULT_ROOT = Path.home() / 'NanoJev/runs/hawk_dove_gaussian_two_backbone_independent_v1'
MODULE = 'tools.nanojev_hawk_dove_gaussian_two_backbone'


def prompt_hash(rows):
    digest = hashlib.sha256()
    for row in rows:
        # Include ordered identity and raw prompt to detect split/content drift.
        record = (row['instance'], row['tier'], row['mask'], row['prompt'])
        digest.update(json.dumps(record, ensure_ascii=False).encode('utf8'))
        digest.update(b'\n')
    return digest.hexdigest()


def capture_hidden(model, tokenizer, device, prompts, max_tokens):
    """Same middle/final-layer, last-four-token contract as altreg Math capture.

    Capture returns ordinary CPU tensors, not inference tensors. It never reads
    numeric target labels, parsed numerical input features, or oracle results.
    """
    encoded = []
    for prompt in prompts:
        ids = tokenizer(prompt, add_special_tokens=False)['input_ids']
        if len(ids) < 4 or len(ids) > max_tokens:
            raise RuntimeError(f'DUO_INVALID_TOKEN_LENGTH:{len(ids)}')
        encoded.append(ids)
    if not encoded:
        raise ValueError('DUO_EMPTY_CAPTURE_BATCH')
    pad = tokenizer.pad_token_id
    if pad is None:
        pad = tokenizer.eos_token_id
    if pad is None:
        raise RuntimeError('DUO_NO_PAD_TOKEN')
    width = max(len(seq) for seq in encoded)
    ids = torch.full((len(encoded), width), int(pad), dtype=torch.long, device=device)
    mask = torch.zeros_like(ids)
    for k, seq in enumerate(encoded):
        ids[k, :len(seq)] = torch.tensor(seq, dtype=torch.long, device=device)
        mask[k, :len(seq)] = 1
    with torch.inference_mode():
        result = model(input_ids=ids, attention_mask=mask,
                       output_hidden_states=True, use_cache=False)
        states = result.hidden_states
        if states is None or len(states) < 3:
            raise RuntimeError('DUO_MISSING_HIDDEN_STATES')
        layers = (max(1, (len(states) - 1) // 2), len(states) - 1)
        rows = torch.arange(len(encoded), device=device)[:, None]
        positions = torch.tensor([len(seq) for seq in encoded], device=device)[:, None] + torch.arange(-4, 0, device=device)[None, :]
        vectors = torch.stack([states[layer][rows, positions, :] for layer in layers], dim=1)
        if not bool(torch.isfinite(vectors).all()):
            raise RuntimeError('DUO_NONFINITE_BACKBONE_ACTIVATIONS')
        return vectors.flatten(1).float().cpu().clone()


class IndependentGaussianHead(reference.BlindDensityNetwork):
    """Same fresh Gaussian bank/density encoder for both independent models.

    The original 14 oracle-blind prompt features enter the original numeric
    projection. A backbone-specific projection of frozen activations contributes
    at the SAME first hidden layer; subsequent encoder, Gaussian bank mixture,
    density decoder, losses and optimization are exactly the reference design.

    All parameters here are initialized afresh and trainable, including banks.
    Only the upstream LM itself remains frozen in the activation cache.
    """
    def __init__(self, input_dim, banks=2000, bins=256, seed=20261022, hidden=128):
        if not isinstance(input_dim, int) or input_dim < 1:
            raise ValueError('DUO_INVALID_BACKBONE_DIMENSION')
        # Restore identical numeric-Gaussian initialization independently for
        # each backbone, even if the runs occur in different processes.
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(seed + 17)
            super().__init__(banks, bins, seed, hidden)
            self.backbone_projection = nn.Sequential(
                nn.LayerNorm(input_dim, elementwise_affine=False),
                nn.Linear(input_dim, hidden))
        self.input_dim = input_dim
        self.backbone_scale = 0.1
        self.backbone_enabled = True  # diagnostic ablation only

    def locations(self, features):
        if (features.ndim != 2 or
                features.shape[1] != reference.INPUT_DIM + self.input_dim):
            raise ValueError('DUO_BOUND_INPUT_DIMENSION_DRIFT')
        numerical = features[:, :reference.INPUT_DIM]
        activation = self.encoder[0](numerical)
        if self.backbone_enabled:
            hidden = features[:, reference.INPUT_DIM:].float()
            activation = activation + self.backbone_scale * self.backbone_projection(hidden)
        # Preserve reference encoder layers 1–4 and the density network forward.
        return self.encoder[2:](self.encoder[1](activation)).sigmoid()


def _cache_content(backbone, data, frozen, tokenizer, device, args, meta, expected_dim):
    captured = {}
    for split in ('train', 'dev', 'test'):
        rows = data[split]['rows']
        blocks = []
        for start in range(0, len(rows), args.capture_batch_size):
            prompts = [row['prompt'] for row in rows[start:start + args.capture_batch_size]]
            block = capture_hidden(frozen, tokenizer, device, prompts, args.max_prompt_tokens)
            if block.shape != (len(prompts), expected_dim):
                raise RuntimeError('DUO_CAPTURE_DIMENSION_DRIFT')
            # Preserve the FP32 result of the frozen capture.  FP16 cache rounding
            # is not part of the already-tested Qwen capture/binding contract.
            blocks.append(block.float().clone())
        captured[split] = {'activations': torch.cat(blocks), 'prompt_sha256': prompt_hash(rows)}
        print(f'duo capture backbone={backbone} split={split} rows={len(rows)} dim={expected_dim}', flush=True)
    return {'schema': SCHEMA + '.activations', 'backbone': backbone,
            'model_id': cassette.SOURCE[backbone][0], 'source_info': meta,
            'capture': 'middle and last hidden layer, last four tokens, FP32, no labels',
            'input_dim': expected_dim, 'splits': captured}


def make_cache(backbone, data, args):
    """Exactly one frozen source model; caches separated by backbone and run."""
    if backbone not in BACKBONES:
        raise ValueError('DUO_UNKNOWN_BACKBONE')
    try:
        _, frozen, tokenizer, model_device, info = cassette.load_source(backbone, args)
    except ImportError as exc:
        if 'Application Control policy' in str(exc):
            raise RuntimeError('DUO_WINDOWS_APPLICATION_CONTROL_BLOCKED_DEPENDENCY: '
                               'Repair the blocked scikit-learn/transformers DLL or '
                               'use an approved Python environment; no policy bypass.') from exc
        raise
    try:
        if any(p.requires_grad for p in frozen.parameters()):
            raise RuntimeError('DUO_BACKBONE_MUST_BE_FROZEN')
        input_dim = int(frozen.get_input_embeddings().weight.shape[-1]) * 8
        return _cache_content(backbone, data, frozen, tokenizer, model_device, args, info, input_dim)
    finally:
        del frozen, tokenizer
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def validate_cache(cache, backbone, data, allow_previous=False):
    acceptable = {SCHEMA + '.activations'}
    if allow_previous:
        acceptable.update({
            'nanojev.hawk-dove.gaussian-frozen-three-backbone.v2-direct-input.activations',
            'nanojev.hawk-dove.gaussian-three-backbone-bound-residual.v3.activations'})
    if cache.get('schema') not in acceptable or cache.get('backbone') != backbone:
        raise RuntimeError('DUO_CACHE_IDENTITY_MISMATCH')
    if cache.get('model_id') != cassette.SOURCE[backbone][0]:
        raise RuntimeError('DUO_CACHE_SOURCE_MISMATCH')
    dim = int(cache['input_dim'])
    if dim < 1:
        raise RuntimeError('DUO_CACHE_INVALID_DIMENSION')
    for split in ('train', 'dev', 'test'):
        entry = cache['splits'][split]
        if entry['prompt_sha256'] != prompt_hash(data[split]['rows']):
            raise RuntimeError('DUO_CACHE_PROMPT_OR_SPLIT_DRIFT:' + split)
        features = entry['activations']
        if features.shape != (len(data[split]['rows']), dim):
            raise RuntimeError('DUO_CACHE_FEATURE_SHAPE_DRIFT:' + split)
        if features.dtype != torch.float32 or not bool(torch.isfinite(features).all()):
            raise RuntimeError('DUO_CACHE_REQUIRES_FINITE_FP32:' + split)
    return dim


def _dataset(args, device):
    # Original identical matrix/count/tier/mask generation, independent of LM.
    return reference._dataset(args, torch.device('cpu'))


def _install_activations(data, cache, device):
    for split in ('train', 'dev', 'test'):
        act = cache['splits'][split]['activations']
        parsed = data[split]['features']  # original parsed, oracle-blind features
        if parsed.shape != (len(data[split]['rows']), reference.INPUT_DIM):
            raise RuntimeError('DUO_REFERENCE_INPUT_DRIFT')
        features = torch.cat((parsed.cpu().float(), act.float()), dim=-1)
        data[split]['features'] = features if split == 'train' else features.to(device)
        data[split]['numeric'] = data[split]['numeric'].to(device)
        data[split]['tier_indices'] = {t: v.to(device) for t, v in data[split]['tier_indices'].items()}
    return data


def identity(args, backbone, manifest, domain, cache):
    return {
        'schema': SCHEMA, 'backbone': backbone,
        'baseline_identity': reference._identity(args, manifest, domain),
        'model_id': cassette.SOURCE[backbone][0],
        'source_info': cache['source_info'], 'capture': cache['capture'],
        'input_dim': cache['input_dim'],
        'prompt_hashes': {s: cache['splits'][s]['prompt_sha256']
                          for s in ('train', 'dev', 'test')},
        'head': 'fresh independently trained BlindDensityNetwork with backbone first-layer projection',
        'initial_weights': 'random-from-seed; no pretrained Gaussian/reference checkpoint',
        'binding': '14 oracle-blind parsed visible prompt features + frozen LM activations',
        'oracle_XY_in_input': False,
        'parsed_numeric_features_used_for_inference': True,
        'raw_text_only_understanding_certified': False}


def _load_cache_or_capture(backbone, args, data):
    root = args.reuse_cache_root
    if root is None:
        return make_cache(backbone, data, args)
    path = root / backbone / 'feature_cache.pt'
    if not path.is_file():
        print('DUO_CACHE_NOT_FOUND_RECAPTURE=' + str(path), flush=True)
        return make_cache(backbone, data, args)
    cache = torch.load(path, map_location='cpu', weights_only=True)
    validate_cache(cache, backbone, data, allow_previous=True)
    cache['schema'] = SCHEMA + '.activations'
    print('DUO_VERIFIED_REUSED_FEATURE_CACHE=' + str(path), flush=True)
    return cache

def _evaluate(model, part, domain, args):
    return base.evaluate(model, part, domain, args.target_sigma_bins,
                         batch_size=args.eval_batch_size, relative_tolerance=args.tolerance)


def _write_predictions(path, model, part, domain, args):
    """Save independently usable keyed X/Y/Z predictions for later ensembles."""
    model.eval()
    with path.open('w', encoding='utf8') as file:
        with torch.no_grad():
            for start in range(0, len(part['rows']), args.eval_batch_size):
                rows = part['rows'][start:start + args.eval_batch_size]
                density = model(part['features'][start:start + len(rows)])
                predictions = base.numeric_decode(density, domain).cpu().tolist()
                for row, (x, y) in zip(rows, predictions):
                    z = 'HAWK' if x > y else 'DOVE' if x < y else 'TIE'
                    file.write(json.dumps({'instance': row['instance'],
                                           'tier': row['tier'], 'mask': row['mask'],
                                           'predicted_X': x, 'predicted_Y': y,
                                           'predicted_Z': z}) + '\n')


def _verify_resume_identity(expected, observed):
    """Only the cumulative step target may change between training phases."""
    if expected.get('schema') != SCHEMA or observed.get('schema') != SCHEMA:
        raise RuntimeError('DUO_RESUME_SCHEMA_MISMATCH')
    left = json.loads(json.dumps(expected))
    right = json.loads(json.dumps(observed))
    right['baseline_identity']['steps'] = left['baseline_identity']['steps']
    if left != right:
        raise RuntimeError('DUO_RESUME_IDENTITY_DRIFT')


def _archive_prior_run(output):
    """Preserve the original completed run before legacy weights-only migration."""
    archive = output / 'before_weights_only_resume'
    if archive.exists():
        raise RuntimeError('DUO_LEGACY_RESUME_ARCHIVE_ALREADY_EXISTS')
    archive.mkdir()
    for name in ('best.pt', 'history.json', 'summary.json',
                 'predictions_dev.jsonl', 'predictions_test.jsonl'):
        source = output / name
        if source.is_file():
            shutil.copy2(source, archive / name)


def _save_progress(output, step, ident, model, opt, scheduler, generator,
                   best, best_step, history, phase_start, phase_end,
                   weights_only_origin):
    """Atomic, full training state at a completed optimizer/scheduler step."""
    state = {
        'schema': SCHEMA + '.training-progress.v1',
        'identity': ident,
        'step': step,
        'phase_start': phase_start,
        'phase_end': phase_end,
        'model': model.state_dict(),
        'optimizer': opt.state_dict(),
        'scheduler': scheduler.state_dict(),
        'sampler_rng': generator.get_state(),
        'torch_cpu_rng': torch.get_rng_state(),
        'torch_cuda_rng': torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
        'best_rank': tuple(best) if best is not None else None,
        'best_step': best_step,
        'history': history,
        'weights_only_origin': bool(weights_only_origin),
    }
    base._atomic_save(output / 'latest.pt', state)


def _restore_rng(progress, generator):
    generator.set_state(progress['sampler_rng'])
    torch.set_rng_state(progress['torch_cpu_rng'])
    cuda_state = progress['torch_cuda_rng']
    if cuda_state:
        if not torch.cuda.is_available() or len(cuda_state) != torch.cuda.device_count():
            raise RuntimeError('DUO_RESUME_CUDA_RNG_DEVICE_DRIFT')
        torch.cuda.set_rng_state_all(cuda_state)


def train(args):
    if args.backbone not in BACKBONES:
        raise ValueError('DUO_INVALID_BACKBONE')
    reference._validate(args)
    if args.capture_batch_size < 1 or args.max_prompt_tokens < 4 or args.checkpoint_every < 0:
        raise ValueError('DUO_BAD_CAPTURE_OR_CHECKPOINT_OPTIONS')
    if args.weights_only_resume and not args.resume:
        raise ValueError('DUO_WEIGHTS_ONLY_REQUIRES_RESUME')
    if args.device == 'cuda' and not torch.cuda.is_available():
        raise ValueError('DUO_CUDA_UNAVAILABLE')
    if args.device == 'cpu':
        torch.set_num_threads(args.cpu_threads)
    output = args.output.expanduser().resolve()
    if args.resume:
        if not (output / 'config.json').is_file() or not (output / 'feature_cache.pt').is_file():
            raise FileNotFoundError('DUO_RESUME_NEEDS_EXISTING_RUN:' + str(output))
    elif output.exists() and any(output.iterdir()):
        raise FileExistsError('DUO_NO_OVERWRITE:' + str(output))

    data, domain, manifest = _dataset(args, torch.device('cpu'))
    if args.resume:
        cache = torch.load(output / 'feature_cache.pt', map_location='cpu', weights_only=True)
        dim = validate_cache(cache, args.backbone, data)
        ident = json.loads((output / 'config.json').read_text(encoding='utf8'))
        _verify_resume_identity(ident, identity(args, args.backbone, manifest, domain, cache))
        previous_manifest = json.loads((output / 'matrix_split_manifest.json').read_text(encoding='utf8'))
        if previous_manifest != manifest:
            raise RuntimeError('DUO_RESUME_MANIFEST_DRIFT')
    else:
        cache = _load_cache_or_capture(args.backbone, args, data)
        dim = validate_cache(cache, args.backbone, data)
        output.mkdir(parents=True, exist_ok=True)
        base._atomic_save(output / 'feature_cache.pt', cache)
        ident = identity(args, args.backbone, manifest, domain, cache)
        base._atomic_json(output / 'config.json', ident)
        base._atomic_json(output / 'matrix_split_manifest.json', manifest)

    device = torch.device(args.device)
    _install_activations(data, cache, device)
    model = IndependentGaussianHead(dim, args.banks, args.grid_bins,
                                    args.seed, args.hidden).to(device)
    if not all(p.requires_grad for p in model.parameters()):
        raise RuntimeError('DUO_HEAD_NOT_FULLY_TRAINABLE')
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.)
    generator = torch.Generator(device='cpu').manual_seed(args.seed + 91)
    pools = data['train']['tier_indices']
    history = []; best = None; best_step = None
    step_start = 0
    phase_start = 0
    phase_end = args.steps
    weights_only_origin = False
    scheduler = None
    start_time = time.monotonic()

    if args.resume:
        saved_best = torch.load(output / 'best.pt', map_location='cpu', weights_only=True)
        if saved_best.get('schema') != SCHEMA or saved_best.get('identity') != ident:
            raise RuntimeError('DUO_RESUME_BEST_CHECKPOINT_IDENTITY_MISMATCH')
        latest_path = output / 'latest.pt'
        if latest_path.is_file():
            progress = torch.load(latest_path, map_location='cpu', weights_only=True)
            if progress.get('schema') != SCHEMA + '.training-progress.v1' or progress.get('identity') != ident:
                raise RuntimeError('DUO_RESUME_PROGRESS_IDENTITY_MISMATCH')
            step_start = int(progress['step'])
            phase_start, phase_end = int(progress['phase_start']), int(progress['phase_end'])
            if not (0 <= phase_start <= step_start <= phase_end):
                raise RuntimeError('DUO_RESUME_PROGRESS_STEP_INVALID')
            if not (0 <= int(progress['best_step']) <= step_start) or int(saved_best['step']) != progress['best_step']:
                raise RuntimeError('DUO_RESUME_BEST_STEP_CONFLICT')
            model.load_state_dict(progress['model'], strict=True)
            opt.load_state_dict(progress['optimizer'])
            scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
                opt, T_max=phase_end - phase_start, eta_min=args.min_lr)
            scheduler.load_state_dict(progress['scheduler'])
            best, best_step = tuple(progress['best_rank']), int(progress['best_step'])
            history = progress['history']
            weights_only_origin = bool(progress.get('weights_only_origin', False))
            if not history or history[-1]['step'] > step_start:
                raise RuntimeError('DUO_RESUME_HISTORY_INVALID')
            if args.steps < phase_end:
                raise RuntimeError('DUO_RESUME_TARGET_BEFORE_PHASE_END')
            if args.steps > phase_end:
                if step_start != phase_end:
                    raise RuntimeError('DUO_RESUME_COMPLETE_CURRENT_PHASE_FIRST')
                phase_start, phase_end = step_start, args.steps
                # New extension phase; preserve Adam moments and sampler RNG.
                # The prior cosine had reached min LR, so start a NEW cosine.
                for group in opt.param_groups:
                    group['lr'] = args.lr
                    group['initial_lr'] = args.lr
                scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
                    opt, T_max=phase_end-phase_start, eta_min=args.min_lr)
                print(f'DUO_RESUME_NEW_LR_PHASE={args.backbone} from={phase_start} to={phase_end}', flush=True)
            _restore_rng(progress, generator)
            print(f'DUO_EXACT_RESUME={args.backbone} step={step_start} target={args.steps} '
                  f'optimizer=RESTORED sampler_rng=RESTORED', flush=True)
        else:
            if not args.weights_only_resume:
                raise RuntimeError('DUO_LEGACY_CHECKPOINT_HAS_NO_OPTIMIZER_RNG: '
                                   'Use --resume --weights-only-resume to continue from best.pt '
                                   'with a NEW optimizer/scheduler/RNG, or keep the old run unchanged.')
            step_start = int(saved_best['step'])
            if step_start < 0:
                raise RuntimeError('DUO_LEGACY_INVALID_STEP')
            model.load_state_dict(saved_best['model'], strict=True)
            score = _evaluate(model, data['dev'], domain, args)
            best, best_step = reference._rank(score), step_start
            prior_history = json.loads((output / 'history.json').read_text(encoding='utf8'))['records']
            if not any(int(h['step']) == step_start and h['promoted'] for h in prior_history):
                raise RuntimeError('DUO_LEGACY_BEST_NOT_IN_HISTORY')
            history = [h for h in prior_history if int(h['step']) <= step_start]
            phase_start, phase_end = step_start, args.steps
            if phase_end <= phase_start:
                raise RuntimeError('DUO_RESUME_TARGET_NOT_AFTER_LEGACY_BEST')
            _archive_prior_run(output)
            weights_only_origin = True
            scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
                opt, T_max=phase_end-phase_start, eta_min=args.min_lr)
            print(f'DUO_WEIGHTS_ONLY_RESUME={args.backbone} step={step_start} target={args.steps} '
                  'optimizer=NEW scheduler=NEW sampler_rng=NEW previous_run=ARCHIVED', flush=True)
    else:
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
            opt, T_max=args.steps, eta_min=args.min_lr)

    if step_start > args.steps:
        raise RuntimeError('DUO_RESUME_TARGET_BEFORE_CHECKPOINT')
    if args.resume and step_start == args.steps:
        # Safe recovery from a crash after the last optimizer checkpoint but
        # before summary/TEST/prediction publication; no optimizer steps repeat.
        print(f'DUO_RESUME_FINALIZE_ONLY={args.backbone} step={step_start}', flush=True)

    def probe(step, loss=None):
        nonlocal best, best_step
        result = _evaluate(model, data['dev'], domain, args)
        rank = reference._rank(result)
        gate = reference._gate(result, args.min_rate)
        promote = best is None or rank > best
        if promote:
            best, best_step = rank, step
            base._atomic_save(output / 'best.pt',
                              {'schema': SCHEMA, 'identity': ident, 'step': step,
                               'model': model.state_dict()})
        history.append({'step': step, 'loss': loss, 'dev_gate': gate,
                        'dev': result['overall'], 'promoted': promote,
                        'lr': opt.param_groups[0]['lr']})
        base._atomic_json(output / 'history.json',
                          {'schema': SCHEMA, 'records': history})
        print(f'duo backbone={args.backbone} banks={args.banks} '
              f'step={step}/{args.steps} dev_min_joint5={rank[0]:.4f} '
              f'dev_tv={result["overall"]["mean_tv"]:.6f} '
              f'gate={gate["status"]}', flush=True)

    if not args.resume:
        print(f'DUO_FRESH_HEAD_INIT={args.backbone} '
              f'trainable_parameters={sum(p.numel() for p in model.parameters())} '
              f'reference_checkpoint=NONE', flush=True)
        probe(0)
        _save_progress(output, 0, ident, model, opt, scheduler, generator,
                       best, best_step, history, phase_start, phase_end, weights_only_origin)
    else:
        # Checkpoint the migration/new phase boundary before the first update.
        _save_progress(output, step_start, ident, model, opt, scheduler, generator,
                       best, best_step, history, phase_start, phase_end, weights_only_origin)

    checkpoint_every = args.checkpoint_every or args.probe_every
    for step in range(step_start + 1, args.steps + 1):
        model.train()
        selected = []
        for j, tier in enumerate(reference.TIERS):
            count = args.batch_size // len(reference.TIERS) + int(
                j < args.batch_size % len(reference.TIERS))
            pool = pools[tier]
            draws = torch.randint(len(pool), (count,), generator=generator)
            selected.append(pool[draws.to(device)])
        ids = torch.cat(selected)
        inp = data['train']['features'][ids.cpu()].to(device=device, dtype=torch.float32)
        actual = data['train']['numeric'][ids]
        target = base.normalized_target(actual, domain, args.grid_bins,
                                        args.target_sigma_bins)
        opt.zero_grad(set_to_none=True)
        density = model(inp)
        kl = (target * (target.clamp_min(1e-12).log() -
                        density.clamp_min(1e-12).log())).sum(-1).mean()
        mse = args.grid_bins * (density - target).square().mean()
        locations = torch.stack([
            (actual[:, j] - domain[j][0]) / (domain[j][1] - domain[j][0])
            for j in range(2)], dim=1)
        loss = kl + mse + 10. * (model.locations(inp) - locations).square().mean()
        if not bool(torch.isfinite(loss)):
            raise FloatingPointError('DUO_NONFINITE_LOSS')
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), 1., error_if_nonfinite=True)
        opt.step(); scheduler.step()
        probed = step % args.probe_every == 0 or step == args.steps
        if probed:
            probe(step, float(loss.detach()))
        if probed or step % checkpoint_every == 0:
            _save_progress(output, step, ident, model, opt, scheduler, generator,
                           best, best_step, history, phase_start, phase_end, weights_only_origin)

    state = torch.load(output / 'best.pt', map_location=device, weights_only=True)
    model.load_state_dict(state['model'], strict=True)
    dev = _evaluate(model, data['dev'], domain, args)
    model.backbone_enabled = False
    dev_without_backbone = _evaluate(model, data['dev'], domain, args)
    model.backbone_enabled = True
    test = _evaluate(model, data['test'], domain, args)
    _write_predictions(output / 'predictions_dev.jsonl', model, data['dev'], domain, args)
    _write_predictions(output / 'predictions_test.jsonl', model, data['test'], domain, args)
    dev_gate = reference._gate(dev, args.min_rate)
    test_gate = reference._gate(test, args.min_rate)
    report = {
        'schema': SCHEMA,
        'status': 'PASS' if dev_gate['status'] == test_gate['status'] == 'PASS' else 'FAIL',
        'backbone': args.backbone, 'best_step': best_step, 'identity': ident,
        'training_completed_step': args.steps,
        'weights_only_origin': weights_only_origin,
        'manifest': manifest,
        'dev': {'gate': dev_gate, 'overall': dev['overall'], 'by_tier': dev['by_tier']},
        'test': {'gate': test_gate, 'overall': test['overall'], 'by_tier': test['by_tier']},
        'dev_without_backbone_min_joint5': reference._rank(dev_without_backbone)[0],
        'uses_test_for_training_or_promotion': False,
        'oracle_XY_in_input': False,
        'parsed_numeric_features_used_for_inference': True,
        'fresh_head': True,
        'elapsed_seconds': round(time.monotonic() - start_time, 3)}
    base._atomic_json(output / 'summary.json', report)
    print(json.dumps({'backbone': args.backbone, 'status': report['status'],
                      'best_step': best_step, 'dev_gate': dev_gate, 'test_gate': test_gate,
                      'summary': str(output / 'summary.json')}, indent=2), flush=True)
    return report


def audit(args):
    device = torch.device(args.device)
    if args.device == 'cuda' and not torch.cuda.is_available():
        raise ValueError('DUO_CUDA_UNAVAILABLE')
    state = torch.load(args.checkpoint, map_location='cpu', weights_only=True)
    if state.get('schema') != SCHEMA:
        raise ValueError('DUO_CHECKPOINT_SCHEMA_MISMATCH')
    ident = state['identity']; backbone = ident['backbone']
    if backbone not in BACKBONES:
        raise ValueError('DUO_UNKNOWN_CHECKPOINT_BACKBONE')
    cfg = argparse.Namespace(**{**ident['baseline_identity'], 'device': args.device,
                                'eval_batch_size': args.eval_batch_size,
                                'cpu_threads': args.cpu_threads})
    reference._validate(cfg)
    if args.device == 'cpu':
        torch.set_num_threads(args.cpu_threads)
    data, domain, manifest = _dataset(cfg, torch.device('cpu'))
    cache = torch.load(args.checkpoint.parent / 'feature_cache.pt',
                       map_location='cpu', weights_only=True)
    dim = validate_cache(cache, backbone, data)
    if identity(cfg, backbone, manifest, domain, cache) != ident:
        raise ValueError('DUO_AUDIT_IDENTITY_DRIFT')
    _install_activations(data, cache, device)
    model = IndependentGaussianHead(dim, cfg.banks, cfg.grid_bins,
                                    cfg.seed, cfg.hidden).to(device)
    model.load_state_dict(state['model'], strict=True)
    model.eval()
    splits = args.splits.split(',')
    if (not splits or any(s not in ('dev', 'test') for s in splits)
            or len(splits) != len(set(splits))):
        raise ValueError('DUO_AUDIT_SPLITS_INVALID')
    selected = {}
    for split in splits:
        score = _evaluate(model, data[split], domain, cfg)
        selected[split] = {'gate': reference._gate(score, cfg.min_rate),
                           'overall': score['overall'], 'by_tier': score['by_tier']}
        print(f'{backbone} {split.upper()} weakest joint X/Y: '
              f'{selected[split]["gate"]["min_tier_joint_instance_rate"]:.2%}',
              flush=True)
    report = {'schema': SCHEMA + '.audit', 'backbone': backbone,
              'step': state['step'],
              'status': 'PASS' if all(x['gate']['status'] == 'PASS'
                                    for x in selected.values()) else 'FAIL',
              'splits': selected, 'matrix_hashes': manifest['matrix_hashes'],
              'oracle_XY_in_input': False}
    if args.output:
        base._atomic_json(args.output, report)
    return report


def train_all(args):
    root = args.output.expanduser().resolve()
    if args.resume:
        if not all((root / b / 'config.json').is_file() for b in BACKBONES):
            raise FileNotFoundError('DUO_RESUME_ALL_REQUIRES_BOTH_EXISTING_RUNS')
    elif root.exists() and any(root.iterdir()):
        raise FileExistsError('DUO_COMPARISON_OUTPUT_EXISTS:' + str(root))
    root.mkdir(parents=True, exist_ok=True)
    commands = {}
    for backbone in BACKBONES:
        argv = [sys.executable, '-m', MODULE, 'train',
                '--backbone', backbone, '--output', str(root / backbone)]
        for flag in ('device', 'banks', 'grid_bins', 'target_sigma_bins', 'hidden',
                     'steps', 'probe_every', 'batch_size', 'eval_batch_size',
                     'lr', 'min_lr', 'seed', 'train_matrices', 'dev_matrices',
                     'test_matrices', 'counts_per_matrix', 'masks', 'payoff_bound',
                     'tolerance', 'min_rate', 'cpu_threads', 'capture_batch_size',
                     'max_prompt_tokens', 'dtype'):
            argv += ['--' + flag.replace('_', '-'), str(getattr(args, flag))]
        if args.reuse_cache_root is not None and not args.resume:
            argv += ['--reuse-cache-root', str(args.reuse_cache_root)]
        if args.resume:
            argv.append('--resume')
        if args.weights_only_resume:
            argv.append('--weights-only-resume')
        if args.checkpoint_every:
            argv += ['--checkpoint-every', str(args.checkpoint_every)]
        if args.allow_model_download:
            argv.append('--allow-model-download')
        commands[backbone] = argv
    # Reject an unusable transformers import before investing thousands of steps
    # on the first model when the second model has no reusable activations.
    if not args.resume and any(args.reuse_cache_root is None or
           not (args.reuse_cache_root / b / 'feature_cache.pt').is_file()
           for b in BACKBONES):
        try:
            subprocess.run([sys.executable, '-c',
                            'from transformers import AutoModelForCausalLM, AutoTokenizer'],
                           check=True)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError('DUO_CAPTURE_DEPENDENCY_PREFLIGHT_FAILED: '
                               'An approved working transformers/sklearn environment '
                               'is required to capture missing backbone activations.') from exc
    if args.parallel:
        # Opt-in only: default sequential to respect a 6 GB GPU.
        processes = {}
        logs = {}
        try:
            for backbone, command in commands.items():
                log = (root / (backbone + '.log')).open('w', encoding='utf8')
                logs[backbone] = log
                processes[backbone] = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
                print('DUO_PARALLEL_START=' + backbone, flush=True)
            for backbone, process in processes.items():
                code = process.wait()
                if code != 0:
                    raise RuntimeError(f'DUO_BACKBONE_FAILED:{backbone}:exit={code}:log={root / (backbone + ".log")}')
        finally:
            for process in processes.values():
                if process.poll() is None:
                    process.terminate()
            for process in processes.values():
                process.wait()
            for log in logs.values():
                log.close()
    else:
        # One-command, independent serial experiments: no concurrent GPU OOM.
        for backbone, argv in commands.items():
            print('DUO_START_BACKBONE=' + backbone, flush=True)
            subprocess.run(argv, check=True)
    reports = {s: json.loads((root / s / 'summary.json').read_text()) for s in BACKBONES}
    comparison = {'schema': SCHEMA + '.comparison',
                  'backbones': {s: {'model_id': reports[s]['identity']['model_id'],
                                    'status': reports[s]['status'],
                                    'dev_min_joint5': reports[s]['dev']['gate']['min_tier_joint_instance_rate'],
                                    'test_min_joint5': reports[s]['test']['gate']['min_tier_joint_instance_rate'],
                                    'best_step': reports[s]['best_step']} for s in BACKBONES}}
    # Fail closed if supposedly shared matrix splits differ.
    hashes = [reports[s]['manifest']['matrix_hashes'] for s in BACKBONES]
    if hashes[0] != hashes[1]:
        raise RuntimeError('DUO_BACKBONE_MATRIX_SPLIT_MISMATCH')
    base._atomic_json(root / 'comparison.json', comparison)
    print(json.dumps(comparison, indent=2), flush=True)
    return comparison


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    for name in ('train', 'train-all'):
        t = sub.add_parser(name)
        t.add_argument('--output', type=Path, default=DEFAULT_ROOT)
        t.add_argument('--resume', action='store_true',
                       help='continue an existing independent run, preserving weights and best DEV checkpoint')
        t.add_argument('--weights-only-resume', action='store_true',
                       help='explicitly migrate legacy best.pt without optimizer/RNG state; archives original results')
        t.add_argument('--checkpoint-every', type=int, default=0,
                       help='full-state checkpoint interval; 0 = probe interval')
        if name == 'train':
            t.add_argument('--backbone', choices=BACKBONES, required=True)
        else:
            t.add_argument('--parallel', action='store_true', help='run both independent processes concurrently; requires enough GPU memory')
        t.add_argument('--reuse-cache-root', type=Path,
                       help='reuse verified FP32 activations from previous triad/duo directory')
        t.add_argument('--device', choices=('cpu', 'cuda'), default='cuda')
        t.add_argument('--banks', type=int, default=2000)
        t.add_argument('--grid-bins', type=int, default=256)
        t.add_argument('--target-sigma-bins', type=float, default=2.5)
        t.add_argument('--hidden', type=int, default=128)
        t.add_argument('--steps', type=int, default=15000)
        t.add_argument('--probe-every', type=int, default=500)
        t.add_argument('--batch-size', type=int, default=64)
        t.add_argument('--eval-batch-size', type=int, default=256)
        t.add_argument('--lr', type=float, default=.003)
        t.add_argument('--min-lr', type=float, default=.00003)
        t.add_argument('--seed', type=int, default=20261022)
        t.add_argument('--train-matrices', type=int, default=96)
        t.add_argument('--dev-matrices', type=int, default=24)
        t.add_argument('--test-matrices', type=int, default=24)
        t.add_argument('--counts-per-matrix', type=int, default=16)
        t.add_argument('--masks', type=int, choices=(1, 2), default=2)
        t.add_argument('--payoff-bound', type=int, default=4)
        t.add_argument('--tolerance', type=float, default=.05)
        t.add_argument('--min-rate', type=float, default=.8)
        t.add_argument('--cpu-threads', type=int, default=4)
        t.add_argument('--capture-batch-size', type=int, default=8)
        t.add_argument('--max-prompt-tokens', type=int, default=2048)
        t.add_argument('--dtype', choices=('auto', 'float16', 'float32', 'bfloat16'), default='auto')
        t.add_argument('--allow-model-download', action='store_true')
    a = sub.add_parser('audit')
    a.add_argument('--checkpoint', type=Path, required=True)
    a.add_argument('--device', choices=('cpu', 'cuda'), default='cpu')
    a.add_argument('--splits', default='dev,test')
    a.add_argument('--output', type=Path)
    a.add_argument('--cpu-threads', type=int, default=4)
    a.add_argument('--eval-batch-size', type=int, default=256)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    return train(args) if args.command == 'train' else train_all(args) if args.command == 'train-all' else audit(args)


if __name__ == '__main__':
    main()
