#!/usr/bin/env python3
"""Isolated FORMAT teacher-path precision experiment (not autonomous inference).

Starts from an immutable HybridLink curriculum FORMAT checkpoint; tests
relative-precision supervision and a learned exact-zero decision. TEST is never
scored. Passing this experiment does not automatically approve alignment.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import torch
from torch import nn
from torch.nn import functional as F

from tools import nanojev_hawk_dove_hybridlink_curriculum as curriculum
from tools import nanojev_hawk_dove_hybridlink_format_diagnostic as diag
from tools import nanojev_hawk_dove_hybridlink_sequence as base
from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as blind
from tools import nanojev_hawk_dove_gaussian_population_conditional as gauss

SCHEMA = 'nanojev.hawk-dove.hybridlink-precision.v1'


class PrecisionAdapter(nn.Module):
    """Learned regression residual and zero classifier; neither reads targets at inference."""
    def __init__(self, backbone):
        super().__init__()
        self.base = backbone
        width = backbone.width
        inp = width + blind.INPUT_DIM
        self.residual = nn.Sequential(nn.LayerNorm(inp), nn.Linear(inp, width),
                                      nn.GELU(), nn.Linear(width, 2))
        self.zero_classifier = nn.Sequential(nn.LayerNorm(inp), nn.Linear(inp, width),
                                             nn.GELU(), nn.Linear(width, 2))
        # Identity-preserving regression initialization; cautious zero threshold.
        nn.init.zeros_(self.residual[-1].weight)
        nn.init.zeros_(self.residual[-1].bias)
        nn.init.zeros_(self.zero_classifier[-1].weight)
        nn.init.constant_(self.zero_classifier[-1].bias, -3.0)

    def raw_predictions(self, features):
        _, states = self.base.teacher_state(features)
        hidden = states[:, -1]
        context = torch.cat((hidden, features), dim=-1)
        regression = self.base.answer(hidden) + self.residual(context)
        zero_logits = self.zero_classifier(context)
        return regression, zero_logits

    def forward(self, features, threshold=.5):
        numeric, zero_logits = self.raw_predictions(features)
        is_zero = zero_logits.sigmoid() >= threshold
        # Exact zero requires a discrete decision, not approximate regression.
        return torch.where(is_zero, torch.zeros_like(numeric), numeric)


def configure_trainable(model, scope):
    model.base.requires_grad_(False)
    model.residual.requires_grad_(True)
    model.zero_classifier.requires_grad_(True)
    if scope == 'backbone':
        for name in ('backbone','answer','seed_numeric','seed_reference','roles',
                     'product_encoder','product_norm','value_probe','next_product'):
            getattr(model.base, name).requires_grad_(True)
    elif scope != 'head':
        raise ValueError('PRECISION_BAD_SCOPE')
    # The original Gaussian reference stays immutable in both arms.
    model.base.reference.requires_grad_(False)
    return [p for p in model.parameters() if p.requires_grad]


def population_indices(train, scale, near_fraction):
    truth = train['numeric']
    zero = (truth == 0).any(-1)
    near = (truth.abs() <= scale * near_fraction).any(-1) & ~zero
    away = ~zero & ~near
    return [torch.nonzero(mask, as_tuple=False).flatten().cpu() for mask in (away,near,zero)]


def balanced_sample(pools, count, rng, device):
    active = [p for p in pools if p.numel()]
    if not active:
        raise RuntimeError('PRECISION_NO_TRAIN_INSTANCES')
    parts = []
    for i, pool in enumerate(active):
        portion = count // len(active) + (i < count % len(active))
        picks = torch.randint(len(pool), (portion,), generator=rng)
        parts.append(pool.index_select(0, picks))
    return torch.cat(parts).index_select(0, torch.randperm(count, generator=rng)).to(device)


def precision_loss(pred, logits, truth, scale, *, zero_weight=2.0):
    """Train only on true X/Y, with stable nonzero relative error and zero BCE."""
    target = truth / scale
    nonzero = truth != 0
    # Nonzero relative scaling; a tiny floor controls gradient magnitude only.
    denom = target.abs().clamp_min(1e-7)
    relative = (pred - target) / denom
    if bool(nonzero.any()):
        rel = F.smooth_l1_loss(relative[nonzero], torch.zeros_like(relative[nonzero]),
                               beta=.10)
    else:
        rel = pred.new_zeros(())
    zero = (truth == 0).to(logits.dtype)
    positive_weight = torch.full((2,), float(zero_weight), device=logits.device,
                                 dtype=logits.dtype)
    classification = F.binary_cross_entropy_with_logits(logits, zero,
                                                         pos_weight=positive_weight)
    broad = base.numeric_loss(pred, target)
    return rel + classification + .1 * broad, {
        'relative': float(rel.detach()), 'zero_bce': float(classification.detach()),
        'broad': float(broad.detach())}


@torch.no_grad()
def evaluate(model, split, scale, args):
    model.eval()
    outputs, base_outputs, raw_outputs, flags = [], [], [], []
    for features in split['features'].split(args.eval_batch_size):
        regression, logits = model.raw_predictions(features)
        zero = logits.sigmoid() >= args.zero_threshold
        outputs.append(torch.where(zero, torch.zeros_like(regression), regression).cpu() * scale)
        raw_outputs.append((regression * scale).cpu())
        flags.append(zero.cpu())
        orig, _, _ = model.base.teacher_predictions(features)
        base_outputs.append((orig * scale).cpu())
    prediction = torch.cat(outputs)
    original = torch.cat(base_outputs)
    truth = split['numeric'].detach().cpu()
    zeros = truth == 0
    selected = torch.cat(flags)
    metrics = base.lab.strict_metrics(prediction, split, args.tolerance)
    strata = diag._xy_strata(prediction, split, scale, args.tolerance, args.near_fraction)
    return {'strict': metrics, 'strata': strata,
            'uncorrected_current': base.lab.strict_metrics(original, split, args.tolerance),
            'uncorrected_current_strata': diag._xy_strata(original, split, scale, args.tolerance,
                                                         args.near_fraction)['by_stratum'],
            'zero': {'true_coordinate_count': int(zeros.sum()),
                     'true_positive': int((selected & zeros).sum()),
                     'false_positive': int((selected & ~zeros).sum()),
                     'false_negative': int((~selected & zeros).sum()),
                     'predicted_zero_coordinates': int(selected.sum())},
            'raw_head_strict': base.lab.strict_metrics(torch.cat(raw_outputs), split,
                                                       args.tolerance)}


def identity_for(args, source, source_identity, old_checkpoint):
    return {'schema': SCHEMA,
            'reference_sha256': base.file_sha256(source),
            'format_checkpoint_sha256': base.file_sha256(old_checkpoint),
            'curriculum_identity': source_identity,
            'options': {key: getattr(args,key) for key in (
                'arm','scope','seed','lr','batch_size','eval_batch_size','probe_every',
                'near_fraction','zero_threshold','zero_weight','tolerance','min_teacher')},
            'data_policy': 'Train only for fitting; DEV for selection; TEST never evaluated',
            'inference_policy': 'FORMAT teacher path only; exact visible products, not autonomous'}


def _input_config(args):
    source = args.reference_checkpoint.expanduser().resolve()
    old_root = args.curriculum_output.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if not source.is_file() or not (old_root/'config.json').is_file():
        raise FileNotFoundError('PRECISION_REFERENCE_OR_CURRICULUM_CONFIG_MISSING')
    checkpoint = old_root/'arms'/args.arm/'format'/'latest.pt'
    if not checkpoint.is_file():
        raise FileNotFoundError('PRECISION_FORMAT_CHECKPOINT_MISSING')
    if (output == old_root or old_root in output.parents or
            output == checkpoint.parent or output == source.parent):
        raise ValueError('PRECISION_OUTPUT_NOT_ISOLATED')
    original_identity = json.loads((old_root/'config.json').read_text(encoding='utf8'))
    if original_identity.get('schema') != curriculum.SCHEMA:
        raise RuntimeError('PRECISION_CURRICULUM_SCHEMA_DRIFT')
    if base.file_sha256(source) != original_identity['source_sha256']:
        raise RuntimeError('PRECISION_REFERENCE_DRIFT')
    return source, checkpoint, output, original_identity


def run(args):
    if args.device == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('PRECISION_CUDA_UNAVAILABLE')
    if not (args.steps >= 1 and args.batch_size >= 1 and args.eval_batch_size >= 1 and
            args.probe_every >= 1 and args.cpu_threads >= 1 and 0 < args.lr < 1 and
            0 < args.near_fraction < 1 and 0 < args.zero_threshold < 1 and
            0 < args.tolerance < 1 and 0 < args.min_teacher <= 1 and
            args.zero_weight > 0):
        raise ValueError('PRECISION_BAD_OPTIONS')
    torch.set_num_threads(args.cpu_threads)
    torch.manual_seed(args.seed)
    source, old_checkpoint, output, original_identity = _input_config(args)
    original_sha, ck_sha = base.file_sha256(source), base.file_sha256(old_checkpoint)
    reference, splits, _, config = base.load_source(source, args.device)
    options = SimpleNamespace(**original_identity['options'])
    if curriculum.make_identity(options, source, config) != original_identity:
        raise RuntimeError('PRECISION_CURRICULUM_IDENTITY_DRIFT')
    base_model = curriculum.CurriculumModel(reference, options.heads, options.layers).to(args.device)
    curriculum._load_checkpoint(old_checkpoint, original_identity, args.arm, 'format', base_model)
    scale = base.xy_scale(config['source_identity']['payoff_bound'])
    adapter = PrecisionAdapter(base_model).to(args.device)
    original_baseline = evaluate(adapter, splits['dev'], scale, args)
    params = configure_trainable(adapter, args.scope)
    identity = identity_for(args, source, original_identity, old_checkpoint)
    latest = output/'latest.pt'
    best = output/'best.pt'
    if args.resume:
        if not (output/'config.json').is_file() or not latest.is_file():
            raise RuntimeError('PRECISION_RESUME_CHECKPOINT_MISSING')
        if json.loads((output/'config.json').read_text(encoding='utf8')) != identity:
            raise RuntimeError('PRECISION_CONFIG_DRIFT')
        state = torch.load(latest, map_location='cpu', weights_only=True)
        if state.get('schema') != SCHEMA+'.checkpoint' or state.get('identity') != identity:
            raise RuntimeError('PRECISION_CHECKPOINT_IDENTITY_DRIFT')
        adapter.load_state_dict(state['model'], strict=True)
        previous = int(state['step'])
        rng = torch.Generator(device='cpu');rng.set_state(state['sampling_rng'])
        torch.set_rng_state(state['torch_rng'])
        if state['cuda_rng'] is not None and torch.cuda.is_available():
            torch.cuda.set_rng_state_all(state['cuda_rng'])
        best_rank = tuple(state['best_rank'])
    else:
        if output.exists() and any(output.iterdir()):
            raise FileExistsError('PRECISION_OUTPUT_OVERWRITE_REFUSED')
        output.mkdir(parents=True,exist_ok=True)
        gauss._atomic_json(output/'config.json', identity)
        state = None; previous = 0; best_rank = (-1.,-1.,-1.)
        rng = torch.Generator(device='cpu').manual_seed(args.seed + 941)
    if args.steps < previous:
        raise ValueError('PRECISION_STEPS_BEHIND_CHECKPOINT')
    optimizer = torch.optim.AdamW(params, lr=args.lr, weight_decay=0.)
    if state is not None:
        optimizer.load_state_dict(state['optimizer'])
    train, dev = splits['train'],splits['dev']
    pools = population_indices(train, scale, args.near_fraction)
    print('PRECISION_TRAIN_POPULATIONS='+json.dumps({key:int(len(val)) for key,val in zip(
        ('away','near','zero'),pools)}), flush=True)
    for step in range(previous + 1, args.steps + 1):
        adapter.train()
        sample = balanced_sample(pools, args.batch_size, rng, args.device)
        x = train['features'].index_select(0, sample)
        y = train['numeric'].index_select(0, sample)
        prediction, logits = adapter.raw_predictions(x)
        loss, components = precision_loss(prediction, logits, y, scale,
                                          zero_weight=args.zero_weight)
        if args.scope == 'backbone':
            # Keep causal prefix/product comprehension from being destroyed
            # while the backbone learns a more precise final numerical readout.
            _, next_values, consumed = adapter.base.teacher_predictions(x)
            products = base.v4.stage_values(x)
            loss = (loss + .5 * F.mse_loss(next_values, products) +
                    .3 * F.mse_loss(consumed, products))
        if not bool(torch.isfinite(loss)):
            raise FloatingPointError('PRECISION_NONFINITE_LOSS')
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        nn.utils.clip_grad_norm_(params, 1., error_if_nonfinite=True)
        optimizer.step()
        if step % args.probe_every == 0 or step == args.steps:
            outcome = evaluate(adapter, dev, scale, args)
            accuracy = outcome['strict']['min_tier_joint5']
            overall = outcome['strata']['by_stratum']['all']['joint_all_masks_rate']
            near = outcome['strata']['by_stratum']['near_zero_nonzero']['joint_all_masks_rate'] or 0.
            rank = (accuracy, overall, near)
            snap = {'schema': SCHEMA+'.checkpoint','identity':identity,'step':step,
                    'model':adapter.state_dict(), 'optimizer':optimizer.state_dict(),
                    'sampling_rng':rng.get_state(),'torch_rng':torch.get_rng_state(),
                    'cuda_rng':torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None,
                    'best_rank':list(max(rank,best_rank))}
            if rank > best_rank:
                best_rank = rank
                gauss._atomic_save(best, {'schema': SCHEMA+'.best','identity':identity,
                                          'step':step,'model':adapter.state_dict(),
                                          'DEV':outcome})
            snap['best_rank'] = list(best_rank)
            gauss._atomic_save(latest,snap)
            print(f'PRECISION_TRAIN step={step} loss={float(loss.detach()):.6f} '
                  f'minTier={accuracy:.6f} overall={overall:.6f} near={near:.6f} '
                  f'zeroRecall={outcome["zero"]["true_positive"]}/'
                  f'{outcome["zero"]["true_coordinate_count"]} '
                  f'falseZero={outcome["zero"]["false_positive"]}',flush=True)
    if not best.is_file():
        raise RuntimeError('PRECISION_BEST_CHECKPOINT_MISSING')
    selected = torch.load(best, map_location='cpu', weights_only=True)
    if selected.get('identity') != identity or selected.get('schema') != SCHEMA+'.best':
        raise RuntimeError('PRECISION_BEST_IDENTITY_DRIFT')
    adapter.load_state_dict(selected['model'],strict=True)
    outcome = evaluate(adapter, dev, scale, args)
    status = 'PASS' if outcome['strict']['min_tier_joint5'] >= args.min_teacher else 'FAIL'
    result = {'schema':SCHEMA+'.result','identity':identity,
              'status':status,'best_step':selected['step'],
              'latest_step':args.steps, 'evaluation':outcome,
              'original_baseline': {
                  'strict': original_baseline['strict'],
                  'strata': original_baseline['strata']['by_stratum']},
              'best_checkpoint_sha256':base.file_sha256(best),
              'promotion_policy':'EXPERIMENT_ONLY; original format gate and alignment remain unchanged'}
    if base.file_sha256(source) != original_sha or base.file_sha256(old_checkpoint) != ck_sha:
        raise RuntimeError('PRECISION_INPUT_MUTATED')
    gauss._atomic_json(output/'result.json',result)
    print('PRECISION_DEV_GATE='+json.dumps({'status':status,'best_step':selected['step'],
          'min_tier':outcome['strict']['min_tier_joint5'],
          'baseline_min_tier':original_baseline['strict']['min_tier_joint5'],
          'strata':outcome['strata']['by_stratum'],'zero':outcome['zero'],
          'report':str(output/'result.json')},sort_keys=True),flush=True)
    print('PRECISION_REFERENCE_IMMUTABLE=TRUE FORMAT_CHECKPOINT_IMMUTABLE=TRUE '
          'TEST_UNTOUCHED=TRUE ORIGINAL_GATES_UNCHANGED=TRUE',flush=True)
    return result


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reference-checkpoint',type=Path,required=True)
    p.add_argument('--curriculum-output',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    p.add_argument('--arm',choices=curriculum.ARMS,default='recurrent')
    p.add_argument('--scope',choices=('head','backbone'),default='head')
    p.add_argument('--steps',type=int,default=2000)
    p.add_argument('--resume',action='store_true')
    p.add_argument('--probe-every',type=int,default=200)
    p.add_argument('--batch-size',type=int,default=128)
    p.add_argument('--eval-batch-size',type=int,default=256)
    p.add_argument('--seed',type=int,default=20261024)
    p.add_argument('--lr',type=float,default=.0005)
    p.add_argument('--near-fraction',type=float,default=.01)
    p.add_argument('--zero-threshold',type=float,default=.5)
    p.add_argument('--zero-weight',type=float,default=2.)
    p.add_argument('--min-teacher',type=float,default=.8)
    p.add_argument('--tolerance',type=float,default=.05)
    p.add_argument('--cpu-threads',type=int,default=4)
    return p


def main(argv=None):
    return run(parser().parse_args(argv))


if __name__ == '__main__':
    main()
