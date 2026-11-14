#!/usr/bin/env python3
"""Train a restartable 4th X/Y head on three frozen Hawk-Dove Gaussian heads.

EXPLORATORY stacking on existing TRAIN/DEV/TEST caches. The original predictors
were trained on TRAIN and selected using DEV; TEST has already been inspected.
No claim of a new blind certification or out-of-fold meta-training is made.
No backbone/head parameters are updated. Z is derived from fused X/Y.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import torch
from torch import nn
from torch.nn import functional as F

from tools import nanojev_hawk_dove_gaussian_minority_report as mr
from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as ref
from tools import nanojev_hawk_dove_gaussian_population_conditional as base
from tools import nanojev_hawk_dove_gaussian_two_backbone as duo

SCHEMA = 'nanojev.hawk-dove.frozen-three-head-fusion.v1'
ORDER = mr.MODELS  # reference, pythia, tinystories, fixed and certified
SPLITS = ('train', 'dev', 'test')


def _atomic_save(path: Path, obj):
    base._atomic_save(path, obj)


def _atomic_json(path: Path, obj):
    base._atomic_json(path, obj)


def _fingerprint(reference_path, duo_root):
    paths = {'reference': Path(reference_path)}
    for b in ('pythia', 'tinystories'):
        paths[b + '_best'] = Path(duo_root) / b / 'best.pt'
        paths[b + '_cache'] = Path(duo_root) / b / 'feature_cache.pt'
        paths[b + '_config'] = Path(duo_root) / b / 'config.json'
        paths[b + '_manifest'] = Path(duo_root) / b / 'matrix_split_manifest.json'
    for p in paths.values():
        if not p.is_file():
            raise FileNotFoundError('FUSION_INPUT_MISSING:' + str(p))
    return {k: mr._sha256(p) for k, p in paths.items()}


def _validated_sources(reference_checkpoint, duo_root, eval_batch_size):
    state = mr._load_checkpoint(Path(reference_checkpoint), ref.SCHEMA)
    ident = state['identity']
    data, domain, manifest, args = mr._dataset_verified(ident, torch.device('cpu'), eval_batch_size)
    sources = {}
    model = ref.BlindDensityNetwork(ident['banks'], ident['grid_bins'], ident['seed'], ident['hidden'])
    model.load_state_dict(state['model'], strict=True)
    model.eval().requires_grad_(False)
    sources['reference'] = (model, None)
    for b in ('pythia', 'tinystories'):
        path = Path(duo_root) / b
        checkpoint = mr._load_checkpoint(path / 'best.pt', duo.SCHEMA)
        cache, dim = mr._verify_duo(checkpoint, b, path, ident, data, manifest)
        model = duo.IndependentGaussianHead(dim, ident['banks'], ident['grid_bins'], ident['seed'], ident['hidden'])
        model.load_state_dict(checkpoint['model'], strict=True)
        model.eval().requires_grad_(False)
        sources[b] = (model, cache)
    return sources, data, tuple(tuple(d) for d in domain), manifest, ident


@torch.inference_mode()
def extract_frozen_features(sources, data, domain, device, batch_size):
    """Each head contributes second hidden layer and Gaussian location/mean/variance.

    Do not include oracle X/Y labels, target densities, or correctness flags.
    Source heads are frozen and inputs originate from the visible prompts/caches.
    """
    output = {}
    positions = None
    if tuple(sources) != ORDER:
        raise RuntimeError('FUSION_HEAD_ORDER_DRIFT')
    for b, (model, _) in sources.items():
        model.to(device)
        model.eval()
    for split in SPLITS:
        all_features = []
        rows = data[split]['rows']
        for first in range(0, len(rows), batch_size):
            last = min(first + batch_size, len(rows))
            numeric = data[split]['features'][first:last].to(device)
            hiddens = []
            statistics = []
            for b in ORDER:
                model, cache = sources[b]
                if cache is None:
                    inp = numeric
                    first_hidden = model.encoder[0](inp)
                else:
                    activation = cache['splits'][split]['activations'][first:last].to(device)
                    inp = torch.cat((numeric, activation), dim=-1)
                    first_hidden = (model.encoder[0](numeric) + model.backbone_scale *
                                    model.backbone_projection(activation))
                hidden = model.encoder[3](model.encoder[2](model.encoder[1](first_hidden)))
                proposal = model.encoder[4](hidden).sigmoid()
                density = model(inp)
                if positions is None or positions.shape[-1] != density.shape[-1]:
                    positions = ((torch.arange(density.shape[-1], device=device, dtype=torch.float32)
                                  + .5) / density.shape[-1])
                expectation = (density * positions.view(1, 1, -1)).sum(-1)
                variance = (density * (positions.view(1, 1, -1) - expectation.unsqueeze(-1)).square()).sum(-1)
                hiddens.append(hidden)
                statistics.append(torch.cat((proposal, expectation, variance), -1))
            feature = torch.cat((*hiddens, *statistics), -1).float().cpu()
            if not bool(torch.isfinite(feature).all()):
                raise RuntimeError('FUSION_NONFINITE_FROZEN_FEATURES')
            all_features.append(feature)
        output[split] = torch.cat(all_features)
        if output[split].shape[0] != len(rows):
            raise RuntimeError('FUSION_FROZEN_FEATURE_COUNT_DRIFT:' + split)
        print(f'FUSION_FEATURES split={split} rows={len(rows)} dim={output[split].shape[-1]}', flush=True)
    return output


class FusionHead(nn.Module):
    """Learned unrestricted correction to reference Gaussian decoded X/Y.

    Zero final-layer initialization reproduces the reference decoded X/Y at step 0,
    but unlike the earlier capped 0.05 residual, corrections are not amplitude capped.
    All 3 independently learned hidden features and density statistics feed the MLP.
    """
    def __init__(self, feature_dim, head_hidden, width=128):
        super().__init__()
        if feature_dim != 3*head_hidden + 18:
            raise ValueError('FUSION_FEATURE_DIMENSION_INVALID')
        self.head_hidden = head_hidden
        self.hidden_projection = nn.LayerNorm(3*head_hidden)
        self.mlp = nn.Sequential(nn.Linear(3*head_hidden + 18, width), nn.GELU(),
                                 nn.Linear(width, width), nn.GELU(), nn.Linear(width, 2))
        nn.init.zeros_(self.mlp[-1].weight)
        nn.init.zeros_(self.mlp[-1].bias)

    def forward(self, features):
        n = self.head_hidden * 3
        hidden = self.hidden_projection(features[:, :n])
        summary = features[:, n:]
        anchor = summary[:, 2:4]  # reference density decoded in normalized domain
        delta = self.mlp(torch.cat((hidden, summary), -1))
        return (anchor + delta).clamp(0.0, 1.0)


def _to_number(locations, domain):
    bounds = torch.tensor(domain, device=locations.device, dtype=locations.dtype)
    return locations * (bounds[:, 1] - bounds[:, 0]) + bounds[:, 0]


def _to_unit(numeric, domain):
    bounds = torch.tensor(domain, device=numeric.device, dtype=numeric.dtype)
    return (numeric - bounds[:, 0]) / (bounds[:, 1] - bounds[:, 0])


def loss_fn(pred_unit, truth, domain):
    target = _to_unit(truth, domain)
    magnitude = truth.abs().clamp_min(10_000.0)
    in_real = (_to_number(pred_unit, domain) - truth).abs()
    return (20 * F.smooth_l1_loss(pred_unit, target, beta=.002) +
            .03 * torch.log1p(in_real / magnitude).mean())


@torch.no_grad()
def score(model, feature, data_split, domain, device, batch_size, tolerance):
    model.eval()
    numeric = []
    for first in range(0, len(feature), batch_size):
        p = model(feature[first:first+batch_size].to(device))
        numeric.extend(_to_number(p, domain).cpu().tolist())
    result = mr._score(data_split['rows'], numeric, tolerance)
    return result, numeric


def _rank(s):
    return (s['min_tier_joint_all_masks_rate'],
            s['mean_tier_joint_all_masks_rate'])


def reference_only(features, head_hidden):
    """Ablation: identical trainable fusion network, but no Pythia/TinyStories.

    Keep reference learned-hidden block and density summary; set the other two
    heads to zero. Labels, numeric bindings, and DEV/TEST evaluation are shared.
    """
    view = features.clone()
    view[:, head_hidden:3*head_hidden] = 0.0
    view[:, 3*head_hidden+6:] = 0.0
    return view


def _source_scores(features, data, domain, tolerance):
    hidden = (features['train'].shape[1] - 18)//3
    result = {}
    for split in ('dev', 'test'):
        summary = features[split][:, hidden*3:]
        result[split] = {}
        for index, name in enumerate(ORDER):
            loc = summary[:, index*6 + 2:index*6 + 4]
            p = _to_number(loc, domain).tolist()
            result[split][name] = mr._score(data[split]['rows'], p, tolerance)
    return result


def _save_checkpoint(path, *, model, optimizer, control_model, control_optimizer,
                     rng, step, best_step, best_rank, control_best_step,
                     control_best_rank, identity, history):
    _atomic_save(path, {
        'schema': SCHEMA + '.progress.v1', 'identity': identity, 'step': step,
        'best_step': best_step, 'best_rank': best_rank, 'model': model.state_dict(),
        'optimizer': optimizer.state_dict(),
        'control_model': control_model.state_dict(),
        'control_optimizer': control_optimizer.state_dict(),
        'control_best_step': control_best_step,
        'control_best_rank': control_best_rank,
        'sampler_rng': rng.get_state(),
        'cpu_rng': torch.get_rng_state(),
        'cuda_rng': torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
        'history': history,
    })


def _restore_rng(checkpoint, rng):
    rng.set_state(checkpoint['sampler_rng'])
    torch.set_rng_state(checkpoint['cpu_rng'])
    saved = checkpoint['cuda_rng']
    if saved:
        if not torch.cuda.is_available() or len(saved) != torch.cuda.device_count():
            raise RuntimeError('FUSION_CUDA_RNG_DRIFT')
        torch.cuda.set_rng_state_all(saved)


def train(args):
    if args.device == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('FUSION_CUDA_UNAVAILABLE')
    if any(v < 1 for v in (args.steps, args.probe_every, args.batch_size,
                           args.eval_batch_size, args.cpu_threads, args.width)):
        raise ValueError('FUSION_INVALID_POSITIVE_OPTION')
    if args.tolerance <= 0 or args.tolerance >= 1 or args.lr <= 0:
        raise ValueError('FUSION_INVALID_TOLERANCE_OR_LR')
    torch.set_num_threads(args.cpu_threads)
    output = args.output.expanduser().resolve()
    current_fingerprints = _fingerprint(args.reference_checkpoint, args.duo_root)
    if args.resume:
        if not (output / 'latest.pt').is_file():
            raise FileNotFoundError('FUSION_RESUME_NEEDS_LATEST_CHECKPOINT')
        if not (output / 'feature_cache.pt').is_file():
            raise FileNotFoundError('FUSION_RESUME_NEEDS_FROZEN_FEATURE_CACHE')
    elif output.exists() and any(output.iterdir()):
        raise FileExistsError('FUSION_REFUSES_OVERWRITE:' + str(output))
    # Fast, fail-closed provenance check before loading large source checkpoints.
    if args.resume:
        previous = torch.load(output/'latest.pt', map_location='cpu', weights_only=True)
        if previous.get('identity', {}).get('frozen_sha256') != current_fingerprints:
            raise RuntimeError('FUSION_RESUME_SOURCE_FINGERPRINT_DRIFT')
    # Full checkpoint, cache, and manifest validation runs before any output mutation.
    sources, data, domain, manifest, ref_ident = _validated_sources(
        args.reference_checkpoint, args.duo_root, args.eval_batch_size)
    checkpoint_id = {'schema': SCHEMA, 'frozen_sha256': current_fingerprints,
                     'matrix_hashes': manifest['matrix_hashes'],
                     'head_hidden': ref_ident['hidden'], 'width': args.width,
                     'seed': args.seed, 'lr': args.lr, 'batch_size': args.batch_size,
                     'tolerance': args.tolerance, 'domain': [list(x) for x in domain],
                     'data_policy': 'EXPLORATORY: original pretrained heads saw TRAIN; selected using DEV; original TEST previously inspected',
                     'trainable': 'fusion and reference-only control heads',
                     'controls': ['full_three_heads','reference_only_fusion'],
                     'source_models': list(ORDER)}
    if args.resume:
        progress = torch.load(output/'latest.pt', map_location='cpu', weights_only=True)
        if progress.get('schema') != SCHEMA+'.progress.v1' or progress.get('identity') != checkpoint_id:
            raise RuntimeError('FUSION_RESUME_CHECKPOINT_IDENTITY_DRIFT')
        cache = torch.load(output/'feature_cache.pt', map_location='cpu', weights_only=True)
        if cache.get('identity') != checkpoint_id or cache.get('schema') != SCHEMA+'.frozen-features.v1':
            raise RuntimeError('FUSION_RESUME_CACHE_IDENTITY_DRIFT')
        features = cache['features']
        for s in SPLITS:
            if (features[s].shape != (len(data[s]['rows']), 3*ref_ident['hidden'] + 18) or
                features[s].dtype != torch.float32 or not bool(torch.isfinite(features[s]).all())):
                raise RuntimeError('FUSION_RESUME_FEATURE_SHAPE_OR_VALUES_DRIFT:' + s)
    else:
        features = extract_frozen_features(sources, data, domain,
                                          torch.device(args.device), args.eval_batch_size)
        output.mkdir(parents=True, exist_ok=False)
        _atomic_save(output/'feature_cache.pt', {'schema': SCHEMA+'.frozen-features.v1',
                    'identity': checkpoint_id, 'features': features})
        _atomic_json(output/'config.json', checkpoint_id)
        _atomic_json(output/'matrix_split_manifest.json', manifest)
    # The three source models are never in the optimizer, and may be released now.
    del sources
    device = torch.device(args.device)
    torch.manual_seed(args.seed)
    model = FusionHead(features['train'].shape[-1], ref_ident['hidden'], args.width).to(device)
    # Start from identical weights for a controlled comparison.
    control_model = FusionHead(features['train'].shape[-1], ref_ident['hidden'], args.width).to(device)
    control_model.load_state_dict(model.state_dict(), strict=True)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.0)
    control_optimizer = torch.optim.AdamW(control_model.parameters(), lr=args.lr, weight_decay=0.0)
    control_features = {k: reference_only(v, ref_ident['hidden']) for k,v in features.items()}
    rng = torch.Generator(device='cpu').manual_seed(args.seed+199)
    start = 0
    best_step = 0
    best_rank = None
    control_best_step = 0
    control_best_rank = None
    history = []
    if args.resume:
        start = int(progress['step'])
        if args.steps < start:
            raise RuntimeError('FUSION_CANNOT_REWIND_TRAINING')
        best_step = int(progress['best_step'])
        best_rank = tuple(progress['best_rank']) if progress['best_rank'] is not None else None
        control_best_step = int(progress['control_best_step'])
        control_best_rank = (tuple(progress['control_best_rank'])
                             if progress['control_best_rank'] is not None else None)
        history = progress['history']
        # v1 selected the zero-initialized, untrained head as the best model.
        # Keep its DEV score as a baseline, never as the trained winner.
        if best_step == 0:
            best_rank = None
        if control_best_step == 0:
            control_best_rank = None
        model.load_state_dict(progress['model'], strict=True)
        optimizer.load_state_dict(progress['optimizer'])
        control_model.load_state_dict(progress['control_model'], strict=True)
        control_optimizer.load_state_dict(progress['control_optimizer'])
        _restore_rng(progress, rng)
        print(f'FUSION_EXACT_RESUME step={start} target={args.steps} optimizer=RESTORED rng=RESTORED', flush=True)
    train_x = features['train']
    train_y = data['train']['numeric']
    tier_pools = data['train']['tier_indices']

    # The reference is a meaningful step-zero *baseline*, never a trained model.
    # Record an explicit non-zero training checkpoint even when DEV gate accuracy
    # fails to improve on that baseline.
    def probe(step, batch_losses=None):
        nonlocal best_rank, best_step, control_best_rank, control_best_step
        dev_score, dev_preds = score(model, features['dev'], data['dev'], domain,
                                     device, args.eval_batch_size, args.tolerance)
        rank = _rank(dev_score)
        improved = step > 0 and (best_rank is None or rank > best_rank)
        if improved:
            best_rank = rank
            best_step = step
            _atomic_save(output/'best.pt', {'schema': SCHEMA, 'identity': checkpoint_id,
                                            'step': step, 'model': model.state_dict()})
        control_score, control_preds = score(control_model, control_features['dev'], data['dev'], domain,
                                           device, args.eval_batch_size, args.tolerance)
        control_rank = _rank(control_score)
        control_improved = step > 0 and (control_best_rank is None or control_rank > control_best_rank)
        if control_improved:
            control_best_rank = control_rank
            control_best_step = step
            _atomic_save(output/'best_reference_only.pt',
                         {'schema':SCHEMA, 'identity':checkpoint_id,
                          'step':step,'model':control_model.state_dict()})
        anchors = _to_number(features['dev'][:, 3*ref_ident['hidden']+2:
                                             3*ref_ident['hidden']+4], domain)
        anchor_delta = float((torch.tensor(dev_preds) - anchors).abs().mean())
        control_delta = float((torch.tensor(control_preds) - anchors).abs().mean())
        history.append({'step':step,
                        'stage': 'untrained_baseline' if step == 0 else 'trained',
                        'updates_completed': step,
                        'dev':{'min_joint5':rank[0],'mean_joint5':rank[1]},
                        'control_dev':{'min_joint5':control_rank[0],
                                       'mean_joint5':control_rank[1]},
                        'mean_absolute_change_from_reference': anchor_delta,
                        'control_mean_absolute_change_from_reference': control_delta,
                        'batch_train_loss': (None if batch_losses is None else batch_losses[0]),
                        'batch_control_loss': (None if batch_losses is None else batch_losses[1]),
                        'promoted':improved, 'control_promoted':control_improved})
        _save_checkpoint(output/'latest.pt', model=model, optimizer=optimizer,
                         control_model=control_model, control_optimizer=control_optimizer,
                         rng=rng, step=step, best_step=best_step, best_rank=best_rank,
                         control_best_step=control_best_step,
                         control_best_rank=control_best_rank,
                         identity=checkpoint_id, history=history)
        _atomic_json(output/'history.json', {'schema': SCHEMA, 'probes': history})
        label = 'BASELINE_ONLY_UNTRAINED' if step == 0 else 'TRAINED'
        print(f'fusion step={step}/{args.steps} status={label} '
              f'DEV_min_joint5={rank[0]:.4f} '
              f'control_DEV_min_joint5={control_rank[0]:.4f} '
              f'best_trained_step={best_step} '
              f'mean_abs_XY_delta={anchor_delta:.5g} '
              f'train_loss={(None if batch_losses is None else batch_losses[0])}', flush=True)

    if not args.resume:
        probe(0)
    elif start > 0 and (best_rank is None or control_best_rank is None):
        # Backward-compatible migration for a completed v1 run whose best.pt
        # was saved at step zero. The last trained state is available, but
        # historical trained candidates were not retained; don't invent them.
        print('FUSION_MIGRATE_V1_UNTRAINED_BEST '
              f'latest_trained_step={start} historical_trained_best=UNKNOWN', flush=True)
        probe(start)
    for step in range(start+1, args.steps+1):
        selected = []
        for index, tier in enumerate(ref.TIERS):
            n = args.batch_size//len(ref.TIERS) + (index < args.batch_size%len(ref.TIERS))
            if n:
                pool = tier_pools[tier]
                selected.append(pool[torch.randint(len(pool), (n,), generator=rng)])
        idx = torch.cat(selected)
        f = train_x[idx].to(device)
        y = train_y[idx].to(device)
        model.train()
        optimizer.zero_grad(set_to_none=True)
        loss = loss_fn(model(f), y, domain)
        if not bool(torch.isfinite(loss)):
            raise FloatingPointError('FUSION_NONFINITE_TRAIN_LOSS')
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), 1.0, error_if_nonfinite=True)
        optimizer.step()
        control_model.train()
        control_optimizer.zero_grad(set_to_none=True)
        control_loss = loss_fn(control_model(control_features['train'][idx].to(device)),
                               y, domain)
        if not bool(torch.isfinite(control_loss)):
            raise FloatingPointError('FUSION_NONFINITE_CONTROL_LOSS')
        control_loss.backward()
        nn.utils.clip_grad_norm_(control_model.parameters(),1.,error_if_nonfinite=True)
        control_optimizer.step()
        if step%args.probe_every==0 or step==args.steps:
            probe(step, (float(loss.detach()), float(control_loss.detach())))

    if best_step <= 0 or control_best_step <= 0:
        raise RuntimeError('FUSION_TRAINED_BEST_MISSING')
    best = torch.load(output/'best.pt', map_location='cpu', weights_only=True)
    if best.get('identity') != checkpoint_id:
        raise RuntimeError('FUSION_BEST_IDENTITY_DRIFT')
    model.load_state_dict(best['model'], strict=True)
    dev_score, dev_preds = score(model, features['dev'], data['dev'], domain,
                                  device, args.eval_batch_size, args.tolerance)
    test_score, test_preds = score(model, features['test'], data['test'], domain,
                                    device, args.eval_batch_size, args.tolerance)
    control_best = torch.load(output/'best_reference_only.pt', map_location='cpu', weights_only=True)
    if control_best.get('identity') != checkpoint_id:
        raise RuntimeError('FUSION_CONTROL_BEST_IDENTITY_DRIFT')
    control_model.load_state_dict(control_best['model'], strict=True)
    control_dev, control_dev_preds = score(control_model, control_features['dev'], data['dev'],
                                            domain, device,args.eval_batch_size,args.tolerance)
    control_test, control_test_preds = score(control_model, control_features['test'], data['test'],
                                              domain,device,args.eval_batch_size,args.tolerance)
    sources_scores = _source_scores(features, data, domain, args.tolerance)
    for split, preds in (('dev',dev_preds),('test',test_preds)):
        path = output/f'predictions_{split}.jsonl'
        text = ''.join(json.dumps({'instance':r['instance'],'tier':r['tier'],
            'mask':r['mask'],'X':float(p[0]),'Y':float(p[1]),'Z':mr._z(p)})+'\n'
            for r,p in zip(data[split]['rows'],preds))
        path.write_text(text,encoding='utf8')
    for split, preds in (('dev',control_dev_preds),('test',control_test_preds)):
        path = output/f'predictions_reference_only_{split}.jsonl'
        text = ''.join(json.dumps({'instance':r['instance'],'tier':r['tier'],
            'mask':r['mask'],'X':float(p[0]),'Y':float(p[1]),'Z':mr._z(p)})+'\n'
            for r,p in zip(data[split]['rows'],preds))
        path.write_text(text,encoding='utf8')
    report = {'schema': SCHEMA+'.report.v1', 'identity': checkpoint_id,
        'best_step': best_step, 'reference_only_best_step':control_best_step,
        'best_model_has_completed_optimizer_updates': True,
        'optimizer_step': args.steps,
        'training_updates_completed': args.steps,
        'initial_untrained_baseline': next((h for h in history if h['step']==0), None),
        'best_trained_probe': next((h for h in history if h['step']==best_step), None),
        'evaluation_status': 'EXPLORATORY_NOT_NEW_BLIND_CERTIFICATION',
        'warning': 'Base models trained on these TRAIN matrices and selected on original DEV; original TEST was previously inspected. New held-out matrices and recaptured LM activations are needed for confirmatory certification.',
        'DEV': {'fusion':dev_score,'reference_only_fusion':control_dev,
                'sources':sources_scores['dev']},
        'TEST': {'fusion':test_score,'reference_only_fusion':control_test,
                 'sources':sources_scores['test']},
        'fusion_gain_over_reference_only_DEV': (dev_score['min_tier_joint_all_masks_rate']-
            control_dev['min_tier_joint_all_masks_rate']),
        'fusion_gain_over_reference_only_TEST': (test_score['min_tier_joint_all_masks_rate']-
            control_test['min_tier_joint_all_masks_rate']),
        'test_used_to_select_fusion': False,
        'reference_is_qwen': False}
    _atomic_json(output/'report.json',report)
    print(json.dumps({'report':str(output/'report.json'),
          'training_updates_completed':args.steps,
          'best_trained_step':best_step,
          'DEV_fusion':dev_score['min_tier_joint_all_masks_rate'],
          'TEST_fusion':test_score['min_tier_joint_all_masks_rate'],
          'TEST_reference_only_fusion':control_test['min_tier_joint_all_masks_rate'],
          'TEST_reference':sources_scores['test']['reference']['min_tier_joint_all_masks_rate'],
          'exploratory':True},indent=2),flush=True)
    return report


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reference-checkpoint',type=Path,required=True)
    p.add_argument('--duo-root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--device', choices=('cpu','cuda'),default='cpu')
    p.add_argument('--cpu-threads',type=int,default=4)
    p.add_argument('--eval-batch-size',type=int,default=128)
    p.add_argument('--steps',type=int,default=6000)
    p.add_argument('--probe-every',type=int,default=250)
    p.add_argument('--batch-size',type=int,default=128)
    p.add_argument('--width',type=int,default=128)
    p.add_argument('--lr',type=float,default=.0003)
    p.add_argument('--tolerance',type=float,default=.05)
    p.add_argument('--seed',type=int,default=20261022)
    p.add_argument('--resume',action='store_true')
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    return train(args)


if __name__=='__main__':
    main()
