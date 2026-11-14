#!/usr/bin/env python3
"""Three independent frozen-backbone residual tests anchored to Gaussian reference.

The reference numeric features are parsed from visible oracle-blind prompts,
never from X/Y labels.  A verified 60%-class original Gaussian checkpoint is
frozen and each frozen LM supplies a separately trained residual correction.
Ablation reports the added LM effect independently of the structured baseline.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

import torch
from torch import nn
from torch.nn import functional as F

from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as reference
from tools import nanojev_hawk_dove_gaussian_population_conditional as base
from tools import nanojev_numerate_spine_cassette_smoke as cassette

SCHEMA = 'nanojev.hawk-dove.gaussian-three-backbone-bound-residual.v3'
BACKBONES = ('qwen', 'tinystories', 'pythia')
DEFAULT_ROOT = Path.home() / 'NanoJev/runs/hawk_dove_gaussian_frozen_backbone_comparison_v1'


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
            raise RuntimeError(f'TRIAD_INVALID_TOKEN_LENGTH:{len(ids)}')
        encoded.append(ids)
    if not encoded:
        raise ValueError('TRIAD_EMPTY_CAPTURE_BATCH')
    pad = tokenizer.pad_token_id
    if pad is None:
        pad = tokenizer.eos_token_id
    if pad is None:
        raise RuntimeError('TRIAD_NO_PAD_TOKEN')
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
            raise RuntimeError('TRIAD_MISSING_HIDDEN_STATES')
        layers = (max(1, (len(states) - 1) // 2), len(states) - 1)
        rows = torch.arange(len(encoded), device=device)[:, None]
        positions = torch.tensor([len(seq) for seq in encoded], device=device)[:, None] + torch.arange(-4, 0, device=device)[None, :]
        vectors = torch.stack([states[layer][rows, positions, :] for layer in layers], dim=1)
        if not bool(torch.isfinite(vectors).all()):
            raise RuntimeError('TRIAD_NONFINITE_BACKBONE_ACTIVATIONS')
        return vectors.flatten(1).float().cpu().clone()


class FrozenBackboneGaussianHead(reference.BlindDensityNetwork):
    """Unmodified reference Gaussian head plus independently learned LM residual.

    A required original checkpoint supplies all Gaussian-head weights. The
    original numeric-feature channel stays intact; the LM only contributes a
    bounded additive change to the predicted locations. With residual logits
    initialized at zero, the complete density matches the reference *exactly*.
    """
    def __init__(self, input_dim, banks=2000, bins=256, seed=20261022,
                 hidden=128, reference_state=None):
        super().__init__(banks, bins, seed, hidden)
        if not isinstance(input_dim, int) or input_dim < 1:
            raise ValueError('TRIAD_INVALID_BACKBONE_FEATURE_SIZE')
        if reference_state is None:
            raise ValueError('TRIAD_REFERENCE_CHECKPOINT_REQUIRED')
        self.input_dim = input_dim
        self.load_state_dict(reference_state, strict=True)
        for parameter in self.encoder.parameters():
            parameter.requires_grad_(False)
        self.bank_log_gains.requires_grad_(False)
        self.log_proposal_sigma.requires_grad_(False)
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(seed + 1)
            self.lm_residual = nn.Sequential(
                nn.LayerNorm(input_dim, elementwise_affine=False),
                nn.Linear(input_dim, hidden), nn.GELU(),
                nn.Linear(hidden, hidden), nn.GELU(),
                nn.Linear(hidden, 2))
        nn.init.zeros_(self.lm_residual[-1].weight)
        nn.init.zeros_(self.lm_residual[-1].bias)
        self.backbone_enabled = True

    def locations(self, features):
        if features.ndim != 2 or features.shape[1] != reference.INPUT_DIM + self.input_dim:
            raise ValueError('TRIAD_BOUND_INPUT_DIMENSION_DRIFT')
        # These fourteen numbers come only from the canonical visible prompt.
        anchor = super().locations(features[:, :reference.INPUT_DIM])
        if not self.backbone_enabled:
            return anchor
        residual = .05 * torch.tanh(self.lm_residual(features[:, reference.INPUT_DIM:].float()))
        return (anchor + residual).clamp(0., 1.)


def _reference_state_from_model_state(model_state, args):
    expected = reference.BlindDensityNetwork(args.banks, args.grid_bins,
                                               args.seed, args.hidden).state_dict()
    return {k: model_state[k] for k in expected}


def verified_reference(args, manifest, domain, data):
    """Refuse an absent, mismatched or nonfunctional original checkpoint."""
    path = args.reference_checkpoint
    if path is None or not path.is_file():
        raise FileNotFoundError('TRIAD_REFERENCE_CHECKPOINT_REQUIRED:' + str(path))
    state = torch.load(path, map_location='cpu', weights_only=True)
    if state.get('schema') != reference.SCHEMA or state.get('identity') != reference._identity(args, manifest, domain):
        raise RuntimeError('TRIAD_REFERENCE_IDENTITY_MISMATCH')
    model = reference.BlindDensityNetwork(args.banks, args.grid_bins,
                                           args.seed, args.hidden)
    model.load_state_dict(state['model'], strict=True)
    # Preflight is DEV-only. TEST is not used for initialization or selection.
    score = reference._evaluate(model, data['dev'], domain, args)
    rate = reference._rank(score)[0]
    print(f'TRIAD_REFERENCE_PREFLIGHT_DEV={rate:.4f}', flush=True)
    if (args.banks == 2000 and args.grid_bins == 256 and
            args.steps == 15000 and rate < .5):
        raise RuntimeError('TRIAD_REFERENCE_ACCURACY_BELOW_BASELINE_FLOOR')
    return state['model'], hashlib.sha256(path.read_bytes()).hexdigest(), rate

def _cache_content(backbone, data, frozen, tokenizer, device, args, meta, expected_dim):
    captured = {}
    for split in ('train', 'dev', 'test'):
        rows = data[split]['rows']
        blocks = []
        for start in range(0, len(rows), args.capture_batch_size):
            prompts = [row['prompt'] for row in rows[start:start + args.capture_batch_size]]
            block = capture_hidden(frozen, tokenizer, device, prompts, args.max_prompt_tokens)
            if block.shape != (len(prompts), expected_dim):
                raise RuntimeError('TRIAD_CAPTURE_DIMENSION_DRIFT')
            # Preserve the FP32 result of the frozen capture.  FP16 cache rounding
            # is not part of the already-tested Qwen capture/binding contract.
            blocks.append(block.float().clone())
        captured[split] = {'activations': torch.cat(blocks), 'prompt_sha256': prompt_hash(rows)}
        print(f'triad capture backbone={backbone} split={split} rows={len(rows)} dim={expected_dim}', flush=True)
    return {'schema': SCHEMA + '.activations', 'backbone': backbone,
            'model_id': cassette.SOURCE[backbone][0], 'source_info': meta,
            'capture': 'middle and last hidden layer, last four tokens, FP32, no labels',
            'input_dim': expected_dim, 'splits': captured}


def make_cache(backbone, data, args):
    """Exactly one frozen source model; caches separated by backbone and run."""
    if backbone not in BACKBONES:
        raise ValueError('TRIAD_UNKNOWN_BACKBONE')
    _, frozen, tokenizer, model_device, info = cassette.load_source(backbone, args)
    try:
        if any(p.requires_grad for p in frozen.parameters()):
            raise RuntimeError('TRIAD_BACKBONE_MUST_BE_FROZEN')
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
        acceptable.add('nanojev.hawk-dove.gaussian-frozen-three-backbone.v2-direct-input.activations')
    if cache.get('schema') not in acceptable or cache.get('backbone') != backbone:
        raise RuntimeError('TRIAD_CACHE_IDENTITY_MISMATCH')
    if cache.get('model_id') != cassette.SOURCE[backbone][0]:
        raise RuntimeError('TRIAD_CACHE_MODEL_MISMATCH')
    dim = int(cache['input_dim'])
    for split in ('train', 'dev', 'test'):
        entry = cache['splits'][split]
        if entry['prompt_sha256'] != prompt_hash(data[split]['rows']):
            raise RuntimeError('TRIAD_CACHE_PROMPT_OR_SPLIT_DRIFT:' + split)
        features = entry['activations']
        if features.shape != (len(data[split]['rows']), dim):
            raise RuntimeError('TRIAD_CACHE_FEATURE_SHAPE_DRIFT')
        if features.dtype != torch.float32:
            raise RuntimeError('TRIAD_CACHE_REQUIRES_FP32')
        if not bool(torch.isfinite(features).all()):
            raise RuntimeError('TRIAD_CACHE_NONFINITE')
    return dim


def _dataset(args, device):
    # Original identical matrix/count/tier/mask generation, independent of LM.
    return reference._dataset(args, torch.device('cpu'))


def _install_activations(data, cache, device):
    for split in ('train', 'dev', 'test'):
        act = cache['splits'][split]['activations']
        parsed = data[split]['features']  # original parsed, oracle-blind features
        if parsed.shape != (len(data[split]['rows']), reference.INPUT_DIM):
            raise RuntimeError('TRIAD_REFERENCE_INPUT_DRIFT')
        features = torch.cat((parsed.cpu().float(), act.float()), dim=-1)
        data[split]['features'] = features if split == 'train' else features.to(device)
        data[split]['numeric'] = data[split]['numeric'].to(device)
        data[split]['tier_indices'] = {t: v.to(device) for t, v in data[split]['tier_indices'].items()}
    return data


def identity(args, backbone, manifest, domain, cache, reference_sha256, reference_dev):
    baseline_id = reference._identity(args, manifest, domain)
    return {'schema': SCHEMA, 'backbone': backbone, 'baseline_identity': baseline_id,
            'model_id': cassette.SOURCE[backbone][0],
            'backbone_source_info': cache['source_info'],
            'capture': cache['capture'], 'input_dim': cache['input_dim'],
            'prompt_hashes': {s: cache['splits'][s]['prompt_sha256'] for s in ('train', 'dev', 'test')},
            'reference_checkpoint_sha256': reference_sha256,
            'reference_dev_min_joint5': reference_dev,
            'reference_head': 'unchanged verified original BlindDensityNetwork (frozen)',
            'binding': '14 original oracle-blind parsed visible numeric/tier features + independently learned frozen-backbone residual',
            'oracle_XY_in_input': False,
            'parsed_numeric_features_used_for_inference': True,
            'backbone_only_text_comprehension_certified': False}


def _load_cache_or_capture(backbone, args, data):
    root = args.reuse_cache_root
    if root is None:
        return make_cache(backbone, data, args)
    path = root / backbone / 'feature_cache.pt'
    if not path.is_file():
        print('TRIAD_CACHE_NOT_FOUND_RECAPTURE=' + str(path), flush=True)
        return make_cache(backbone, data, args)
    cache = torch.load(path, map_location='cpu', weights_only=True)
    validate_cache(cache, backbone, data, allow_previous=True)
    cache['schema'] = SCHEMA + '.activations'
    print('TRIAD_VERIFIED_REUSED_FEATURE_CACHE=' + str(path), flush=True)
    return cache

def _evaluate(model, part, domain, args):
    return base.evaluate(model, part, domain, args.target_sigma_bins,
                         batch_size=args.eval_batch_size, relative_tolerance=args.tolerance)


def train(args):
    if args.backbone not in BACKBONES:
        raise ValueError('TRIAD_INVALID_BACKBONE')
    reference._validate(args)
    if args.capture_batch_size < 1 or args.max_prompt_tokens < 4:
        raise ValueError('TRIAD_BAD_CAPTURE_OPTIONS')
    if args.device == 'cpu':
        torch.set_num_threads(args.cpu_threads)
    output = args.output.expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError('TRIAD_NO_OVERWRITE:' + str(output))
    data, domain, manifest = _dataset(args, torch.device('cpu'))
    anchor, anchor_sha, anchor_dev = verified_reference(args, manifest, domain, data)
    output.mkdir(parents=True, exist_ok=True)
    cache = _load_cache_or_capture(args.backbone, args, data)
    dim = validate_cache(cache, args.backbone, data)
    base._atomic_save(output / 'feature_cache.pt', cache)
    ident = identity(args, args.backbone, manifest, domain, cache, anchor_sha, anchor_dev)
    base._atomic_json(output / 'config.json', ident)
    base._atomic_json(output / 'matrix_split_manifest.json', manifest)
    device = torch.device(args.device)
    data = _install_activations(data, cache, device)
    model = FrozenBackboneGaussianHead(dim, args.banks, args.grid_bins, args.seed, args.hidden, anchor).to(device)
    opt = torch.optim.AdamW((p for p in model.parameters() if p.requires_grad), lr=args.lr, weight_decay=0.)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.steps, eta_min=args.min_lr)
    generator = torch.Generator(device='cpu').manual_seed(args.seed + 91)
    pools = data['train']['tier_indices']
    history = []; best = None; best_step = None
    start_time = time.monotonic()

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
        base._atomic_json(output / 'history.json', {'schema': SCHEMA, 'records': history})
        print(f'triad backbone={args.backbone} banks={args.banks} step={step}/{args.steps} '
              f'dev_min_joint5={rank[0]:.4f} dev_tv={result["overall"]["mean_tv"]:.6f} '
              f'gate={gate["status"]}', flush=True)

    # Compare the exact frozen baseline and augmented model on the SAME device.
    # The CPU preflight score can differ by one borderline case on CUDA.
    plain_reference = reference.BlindDensityNetwork(args.banks, args.grid_bins,
                                                    args.seed, args.hidden).to(device)
    plain_reference.load_state_dict(anchor, strict=True)
    reference_dev_part = {**data['dev'],
                          'features': data['dev']['features'][:, :reference.INPUT_DIM]}
    device_reference_rank = reference._rank(_evaluate(plain_reference, reference_dev_part,
                                                      domain, args))[0]
    del plain_reference
    probe(0)
    if best[0] != device_reference_rank:
        raise RuntimeError('TRIAD_INITIAL_GAUSSIAN_REFERENCE_PARITY_FAILED')
    print(f'TRIAD_INITIAL_REFERENCE_PARITY=PASS dev_min_joint5={best[0]:.4f}', flush=True)
    for step in range(1, args.steps + 1):
        model.train()
        selected = []
        for j, tier in enumerate(reference.TIERS):
            count = args.batch_size // len(reference.TIERS) + int(j < args.batch_size % len(reference.TIERS))
            pool = pools[tier]
            draws = torch.randint(len(pool), (count,), generator=generator)
            selected.append(pool[draws.to(device)])
        ids = torch.cat(selected)
        inp = data['train']['features'][ids.cpu()].to(device=device, dtype=torch.float32)
        actual = data['train']['numeric'][ids]
        target = base.normalized_target(actual, domain, args.grid_bins, args.target_sigma_bins)
        opt.zero_grad(set_to_none=True)
        density = model(inp)
        kl = (target * (target.clamp_min(1e-12).log() - density.clamp_min(1e-12).log())).sum(-1).mean()
        mse = args.grid_bins * (density - target).square().mean()
        locations = torch.stack([(actual[:, j] - domain[j][0]) / (domain[j][1] - domain[j][0])
                                 for j in range(2)], dim=1)
        loss = kl + mse + 10. * (model.locations(inp) - locations).square().mean()
        if not bool(torch.isfinite(loss)):
            raise FloatingPointError('TRIAD_NONFINITE_LOSS')
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), 1., error_if_nonfinite=True)
        opt.step(); scheduler.step()
        if step % args.probe_every == 0 or step == args.steps:
            probe(step, float(loss.detach()))

    state = torch.load(output / 'best.pt', map_location=device, weights_only=True)
    model.load_state_dict(state['model'])
    dev = _evaluate(model, data['dev'], domain, args)
    # Ablation makes clear whether any improvement is due to the backbone.
    model.backbone_enabled = False
    baseline_dev = _evaluate(model, data['dev'], domain, args)
    model.backbone_enabled = True
    test = _evaluate(model, data['test'], domain, args)  # TEST once, after freeze.
    dev_gate = reference._gate(dev, args.min_rate)
    test_gate = reference._gate(test, args.min_rate)
    report = {'schema': SCHEMA, 'status': 'PASS' if dev_gate['status'] == test_gate['status'] == 'PASS' else 'FAIL',
              'backbone': args.backbone, 'best_step': best_step, 'identity': ident,
              'manifest': manifest,
              'dev': {'gate': dev_gate, 'overall': dev['overall'], 'by_tier': dev['by_tier']},
              'test': {'gate': test_gate, 'overall': test['overall'], 'by_tier': test['by_tier']},
              'uses_test_for_training_or_promotion': False,
              'oracle_XY_in_input': False,
              'parsed_numeric_features_used_for_inference': True,
              'dev_no_backbone_min_joint5': reference._rank(baseline_dev)[0],
              'dev_backbone_increment': reference._rank(dev)[0] - reference._rank(baseline_dev)[0],
              'raw_text_understanding_certified': False,
              'elapsed_seconds': round(time.monotonic() - start_time, 3)}
    base._atomic_json(output / 'summary.json', report)
    print(json.dumps({'backbone': args.backbone, 'status': report['status'],
                      'best_step': best_step, 'dev_gate': dev_gate, 'test_gate': test_gate,
                      'summary': str(output / 'summary.json')}, indent=2), flush=True)
    return report


def audit(args):
    device = torch.device(args.device)
    if args.device == 'cuda' and not torch.cuda.is_available():
        raise ValueError('TRIAD_CUDA_UNAVAILABLE')
    state = torch.load(args.checkpoint, map_location='cpu', weights_only=True)
    if state.get('schema') != SCHEMA:
        raise ValueError('TRIAD_CHECKPOINT_SCHEMA_MISMATCH')
    ident = state['identity']; backbone = ident['backbone']
    if backbone not in BACKBONES:
        raise ValueError('TRIAD_UNKNOWN_CHECKPOINT_BACKBONE')
    baseline = ident['baseline_identity']
    cfg = argparse.Namespace(**{**baseline, 'device': args.device,
                                'eval_batch_size': args.eval_batch_size,
                                'cpu_threads': args.cpu_threads})
    reference._validate(cfg)
    if args.device == 'cpu':
        torch.set_num_threads(args.cpu_threads)
    data, domain, manifest = _dataset(cfg, torch.device('cpu'))
    cache = torch.load(args.checkpoint.parent / 'feature_cache.pt', map_location='cpu', weights_only=True)
    dim = validate_cache(cache, backbone, data)
    if identity(cfg, backbone, manifest, domain, cache, ident['reference_checkpoint_sha256'], ident['reference_dev_min_joint5']) != ident:
        raise ValueError('TRIAD_AUDIT_IDENTITY_DRIFT')
    _install_activations(data, cache, device)
    model = FrozenBackboneGaussianHead(dim, cfg.banks, cfg.grid_bins, cfg.seed, cfg.hidden,
                                       _reference_state_from_model_state(state['model'], cfg)).to(device)
    model.load_state_dict(state['model']); model.eval()
    splits = args.splits.split(',')
    if not splits or any(s not in ('dev', 'test') for s in splits) or len(splits) != len(set(splits)):
        raise ValueError('TRIAD_AUDIT_SPLITS_INVALID')
    selected = {}
    for split in splits:
        score = _evaluate(model, data[split], domain, cfg)
        selected[split] = {'gate': reference._gate(score, cfg.min_rate), 'overall': score['overall'],
                           'by_tier': score['by_tier']}
        print(f'{backbone} {split.upper()} weakest joint X/Y: '
              f'{selected[split]["gate"]["min_tier_joint_instance_rate"]:.2%}', flush=True)
    report = {'schema': SCHEMA + '.audit', 'backbone': backbone, 'step': state['step'],
              'status': 'PASS' if all(x['gate']['status'] == 'PASS' for x in selected.values()) else 'FAIL',
              'splits': selected, 'matrix_hashes': manifest['matrix_hashes'],
              'oracle_XY_in_input': False}
    if args.output:
        base._atomic_json(args.output, report)
    return report


def train_all(args):
    root = args.output.expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise FileExistsError('TRIAD_COMPARISON_OUTPUT_EXISTS:' + str(root))
    root.mkdir(parents=True, exist_ok=True)
    commands = {}
    for backbone in BACKBONES:
        argv = [sys.executable, '-m', 'tools.nanojev_hawk_dove_gaussian_three_backbone', 'train',
                '--backbone', backbone, '--output', str(root / backbone)]
        for flag in ('device', 'banks', 'grid_bins', 'target_sigma_bins', 'hidden',
                     'steps', 'probe_every', 'batch_size', 'eval_batch_size',
                     'lr', 'min_lr', 'seed', 'train_matrices', 'dev_matrices',
                     'test_matrices', 'counts_per_matrix', 'masks', 'payoff_bound',
                     'tolerance', 'min_rate', 'cpu_threads', 'capture_batch_size',
                     'max_prompt_tokens', 'dtype'):
            argv += ['--' + flag.replace('_', '-'), str(getattr(args, flag))]
        argv += ['--reference-checkpoint', str(args.reference_checkpoint)]
        if args.reuse_cache_root is not None:
            argv += ['--reuse-cache-root', str(args.reuse_cache_root)]
        if args.allow_model_download:
            argv.append('--allow-model-download')
        commands[backbone] = argv
    if args.parallel:
        # True independent/concurrent processes. Opt-in because smaller GPUs
        # may not fit three CUDA contexts and the Qwen weights simultaneously.
        processes = {}
        logs = {}
        try:
            for backbone, command in commands.items():
                log = (root / (backbone + '.log')).open('w', encoding='utf8')
                logs[backbone] = log
                processes[backbone] = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
                print('TRIAD_PARALLEL_START=' + backbone, flush=True)
            for backbone, process in processes.items():
                code = process.wait()
                if code != 0:
                    raise RuntimeError(f'TRIAD_BACKBONE_FAILED:{backbone}:exit={code}:log={root / (backbone + ".log")}')
        finally:
            for process in processes.values():
                if process.poll() is None:
                    process.terminate()
            for process in processes.values():
                process.wait()
            for log in logs.values():
                log.close()
    else:
        # One-command, independent serial experiments: no three-way GPU OOM.
        for backbone, argv in commands.items():
            print('TRIAD_START_BACKBONE=' + backbone, flush=True)
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
    if hashes[0] != hashes[1] or hashes[0] != hashes[2]:
        raise RuntimeError('TRIAD_BACKBONE_MATRIX_SPLIT_MISMATCH')
    base._atomic_json(root / 'comparison.json', comparison)
    print(json.dumps(comparison, indent=2), flush=True)
    return comparison


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    for name in ('train', 'train-all'):
        t = sub.add_parser(name)
        t.add_argument('--output', type=Path, default=DEFAULT_ROOT)
        if name == 'train':
            t.add_argument('--backbone', choices=BACKBONES, required=True)
        else:
            t.add_argument('--parallel', action='store_true', help='run all three independent processes concurrently; requires enough GPU memory')
        t.add_argument('--reference-checkpoint', type=Path, required=True,
                       help='verified original 2000-bank, 15000-step Gaussian baseline best.pt')
        t.add_argument('--reuse-cache-root', type=Path,
                       help='reuse verified FP32 activations from previous triad directory')
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
