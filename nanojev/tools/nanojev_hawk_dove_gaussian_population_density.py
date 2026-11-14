#!/usr/bin/env python3
"""Isolated shape-only experiment: additive Gaussian banks at configurable population sizes.

Run 200, 2,000, 20,000, 200,000 banks sequentially by default; --only selects
arbitrary positive populations. All banks contribute nonnegative probability
mass to the same physical grid. A sparse Gaussian stencil avoids N x grid
allocations, including for 200,000 banks. This tests reconstruction capacity
and aggregate-weight learning, NOT feature-conditioned Hawk-Dove inference.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import time
from pathlib import Path

import torch

SCHEMA = 'nanojev.hawk-dove.gaussian-population-density.v1'
DEFAULT_COUNTS = (200, 2_000, 20_000, 200_000)
DEFAULT_OUTPUT = Path.home() / 'NanoJev/runs/hawk_dove_gaussian_population_density_v1'
MAX_SIGMA_BINS = 3.5
STENCIL_RADIUS = math.ceil(5 * MAX_SIGMA_BINS)

# Predeclared density *shape*, not a training instance or true numerical answer.
# (mean on [0,1], standard deviation on [0,1], relative mass)
SHAPES = {
    'single': ((0.52, 0.085, 1.0),),
    'double': ((0.27, 0.055, 0.42), (0.74, 0.075, 0.58)),
    'triple': ((0.20, 0.047, 0.30), (0.51, 0.080, 0.44), (0.81, 0.034, 0.26)),
}


def parse_counts(value: str) -> tuple[int, ...]:
    parts = value.split(',')
    if not parts or any(not p.strip() or not p.strip().isascii() or not p.strip().isdigit()
                        for p in parts):
        raise argparse.ArgumentTypeError('--only requires positive comma-separated integers')
    nums = tuple(int(p.strip()) for p in parts)
    if any(n < 1 for n in nums) or len(nums) != len(set(nums)):
        raise argparse.ArgumentTypeError('--only requires unique positive bank counts')
    return nums


def target_mass(bins: int, shape: str, device: torch.device) -> torch.Tensor:
    positions = (torch.arange(bins, device=device, dtype=torch.float32) + 0.5) / bins
    mass = torch.zeros(bins, device=device)
    for mean, sigma, weight in SHAPES[shape]:
        z = (positions - mean) / sigma
        mass += (weight / sigma) * torch.exp(-0.5 * z.square())
    return mass / mass.sum()  # normalize ONCE, globally on the common grid


def bank_geometry(count: int, bins: int, seed: int, device: torch.device):
    """Fixed independent Gaussian experiments, nested across bank counts.

    Each count uses a prefix of the same reproducible (center,width) stream.
    Gaussian tails past five widths are neglected (< 6e-7 continuous mass).
    All 21/200/200000 banks use the same density *units* (probability per bin).
    """
    rng = torch.Generator(device='cpu').manual_seed(seed)
    uniform = torch.rand((count, 2), generator=rng, dtype=torch.float32)
    centers = (uniform[:, 0] * bins).to(device)
    sigmas = (1.5 + 2.0 * uniform[:, 1]).to(device)
    anchor = torch.floor(centers).long()
    offsets = torch.arange(-STENCIL_RADIUS, STENCIL_RADIUS + 1,
                           device=device, dtype=torch.long)
    grid_idx = anchor[:, None] + offsets[None, :]
    valid = (grid_idx >= 0) & (grid_idx < bins)
    delta = (grid_idx.to(torch.float32) + 0.5 - centers[:, None]) / sigmas[:, None]
    mass = torch.exp(-0.5 * delta.square()) * valid.to(torch.float32)
    mass /= mass.sum(dim=1, keepdim=True).clamp_min(1e-30)
    # Values and indices are fixed; only bank weights are learned.
    return grid_idx.clamp(0, bins - 1).contiguous(), mass.contiguous()


def accumulate(logits: torch.Tensor, indices: torch.Tensor, kernel: torch.Tensor,
               bins: int) -> torch.Tensor:
    """Literal addition of contributions from EVERY bank in shared bins.

    Compute O(count * 37) localized contributions, not count * bins.
    No per-bank LHS/RHS output and no gate/abstention; the final weights are
    softmaxed across banks to conserve total mass, NOT separately per bank.
    """
    weights = torch.softmax(logits, dim=0)
    contributions = (weights[:, None] * kernel).reshape(-1)
    result = torch.zeros(bins, dtype=contributions.dtype, device=contributions.device)
    return result.scatter_add(0, indices.reshape(-1), contributions)


def distribution_metrics(pred: torch.Tensor, truth: torch.Tensor) -> dict:
    # Return unscaled, comparable metrics of probability mass per common bin.
    eps = 1e-12
    error = pred - truth
    return {'rmse_mass_per_bin': math.sqrt(float(error.square().mean())),
            'total_variation': float(error.abs().sum()) * 0.5,
            'kl_target_to_reconstruction': float((truth * (truth.clamp_min(eps).log() -
                                                       pred.clamp_min(eps).log())).sum()),
            'max_abs_bin_residual': float(error.abs().max()),
            'total_probability_mass': float(pred.sum())}


def _atomic_bytes(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.pending')
    with open(temp, 'wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def _atomic_json(path: Path, value: dict):
    _atomic_bytes(path, (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n').encode())


def _write_curve(path: Path, truth: torch.Tensor, uniform: torch.Tensor, learned: torch.Tensor):
    truth = truth.detach().cpu().tolist()
    uniform = uniform.detach().cpu().tolist()
    learned = learned.detach().cpu().tolist()
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator='\n')
    writer.writerow(('x_fraction', 'target_mass', 'uniform_population_mass',
                     'learned_population_mass', 'residual_target_minus_learned'))
    for j, (t, u, p) in enumerate(zip(truth, uniform, learned)):
        writer.writerow((format((j + 0.5) / len(truth), '.10f'),
                         format(t, '.10g'), format(u, '.10g'), format(p, '.10g'),
                         format(t - p, '.10g')))
    _atomic_bytes(path, buf.getvalue().encode('utf-8'))


def _write_svg(path: Path, truth: torch.Tensor, uniform: torch.Tensor, learned: torch.Tensor,
               *, banks: int, step: int):
    """Dependency-free inspectable density plot (data still saved as CSV)."""
    sequences = [s.detach().cpu().tolist() for s in (truth, uniform, learned)]
    high = max(max(series) for series in sequences) * 1.1
    width, height, left, right, top, bottom = 900, 365, 55, 20, 35, 48
    def path_data(series):
        return ' '.join(('M' if i == 0 else 'L') +
                        f'{left+i*(width-left-right)/max(1,len(series)-1):.2f},'
                        f'{height-bottom-v/max(high,1e-10)*(height-top-bottom):.2f}'
                        for i, v in enumerate(series))
    paths = '\n'.join(f'<path d="{path_data(series)}" fill="none" stroke="{color}" '
                      f'stroke-width="{thickness}" {dash}/>'
                      for series,color,thickness,dash in zip(
                          sequences,('#888888','#c18338','#2770dd'),(2,1.5,3),
                          ('stroke-dasharray="8 5"','stroke-dasharray="3 5"','')))
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
           f'role="img" aria-label="{banks} bank reconstruction step {step}">\n'
           f'<rect width="100%" height="100%" fill="white"/>\n'
           f'<text x="55" y="24" font-family="sans-serif" font-size="16">'
           f'{banks:,} banks | step {step} | density mass by bin</text>\n'
           f'<line x1="{left}" x2="{width-right}" y1="{height-bottom}" '
           f'y2="{height-bottom}" stroke="#999"/>\n{paths}\n'
           f'<text x="55" y="{height-13}" font-family="sans-serif" font-size="12">'
           'Target (gray dashed) | Untrained (orange dotted) | Learned (blue)</text>\n'
           '</svg>\n')
    _atomic_bytes(path, svg.encode('utf-8'))


def _identity(args, count: int) -> dict:
    return {'schema':SCHEMA, 'bank_count':count, 'grid_bins':args.grid_bins,
            'seed':args.seed, 'shape':args.shape, 'device':args.device, 'steps':args.steps,
            'learning_rate':args.lr, 'probe_every':args.probe_every,
            'geometry':f'fixed_uniform_center_sigma_1.5_to_3.5_bins_5sigma_stencil_{STENCIL_RADIUS}',
            'supervision':'known_synthetic_density_shape_only',
            'objective':'mass_MSE_gradient_on_bank_log_amplitudes',
            'device_independent_source_seed':True}


def _memory_estimate_mb(count: int) -> float:
    # Conservative scratch + autograd budget; fail before attempting any CUDA allocations.
    taps = 2 * STENCIL_RADIUS + 1
    return (count * (taps * 48 + 128)) / 1_000_000


def train_population(args, count: int) -> dict:
    """One independent experiment, with an untrained/uniform control."""
    mem = _memory_estimate_mb(count)
    if mem > args.memory_limit_mb:
        raise ValueError(f'POPULATION_MEMORY_BUDGET_EXCEEDED:{count}:estimated={mem:.1f}MB:'
                         f'limit={args.memory_limit_mb:.1f}MB')
    device = torch.device(args.device)
    folder = args.output / f'banks-{count:09d}'
    ident = _identity(args, count)
    config = folder / 'config.json'
    if config.exists():
        if json.loads(config.read_text('utf-8')) != ident:
            raise ValueError(f'POPULATION_CONFIG_DRIFT:{folder}')
        summary_path = folder / 'summary.json'
        if summary_path.exists() and not args.rerun:
            old = json.loads(summary_path.read_text('utf-8'))
            if old.get('schema') == SCHEMA and old.get('status') == 'COMPLETE' and \
                    old.get('identity') == ident:
                print(f'population={count} existing COMPLETE (use --rerun to repeat)', flush=True)
                return old
    elif folder.exists() and any(folder.iterdir()):
        raise ValueError(f'POPULATION_PARTIAL_UNKNOWN_RUN:{folder}')
    folder.mkdir(parents=True, exist_ok=True)
    _atomic_json(config, ident)
    started = time.monotonic()
    indices, kernels = bank_geometry(count, args.grid_bins, args.seed, device)
    truth = target_mass(args.grid_bins, args.shape, device)
    logits = torch.nn.Parameter(torch.zeros(count, device=device, dtype=torch.float32))
    optimizer = torch.optim.Adam([logits], lr=args.lr)
    with torch.no_grad():
        control = accumulate(logits, indices, kernels, args.grid_bins).detach()
    uniform_metrics = distribution_metrics(control, truth)
    records = []
    def probe(step: int):
        with torch.no_grad():
            density = accumulate(logits, indices, kernels, args.grid_bins).detach()
            metrics = distribution_metrics(density, truth)
            weights = logits.softmax(dim=0)
            metrics['effective_weighted_banks'] = float(1.0 / weights.square().sum())
            metrics['step'] = step
            records.append(metrics)
            _write_curve(folder / f'curves-step-{step:06d}.csv', truth, control, density)
            _write_svg(folder / f'curves-step-{step:06d}.svg', truth, control, density,
                       banks=count, step=step)
            print(f'population={count} step={step}/{args.steps} '
                  f'tv={metrics["total_variation"]:.6f} '
                  f'kl={metrics["kl_target_to_reconstruction"]:.6f}', flush=True)
    probe(0)
    for step in range(1, args.steps + 1):
        optimizer.zero_grad(set_to_none=True)
        pred = accumulate(logits, indices, kernels, args.grid_bins)
        loss = args.grid_bins * (pred - truth).square().sum()
        if not bool(torch.isfinite(loss)):
            raise FloatingPointError(f'POPULATION_NONFINITE_LOSS:{count}:{step}')
        loss.backward()
        optimizer.step()
        if not bool(torch.isfinite(logits).all()):
            raise FloatingPointError(f'POPULATION_NONFINITE_WEIGHTS:{count}:{step}')
        if step % args.probe_every == 0 or step == args.steps:
            probe(step)
    output = {'schema':SCHEMA,'status':'COMPLETE','identity':ident,
              'trainable_parameters':count, 'geometry_parameters_trainable':0,
              'bank_parameterization':'fixed_random_center_width_learned_global_log_amplitude',
              'distribution_kind':'single_shared_synthetic_target_NOT_per_prompt_X_or_Y',
              'oracle_used_for_loss':True,'test_split_read':False,
              'fixed_step_budget':args.steps,'target_is_known':True,
              'memory_estimate_mb':round(mem,2),
              'uniform_population_baseline':uniform_metrics,
              'initial_reconstruction':records[0], 'final_reconstruction':records[-1],
              'history':records, 'elapsed_seconds':round(time.monotonic()-started,3),
              'interpretation_warning':'Averaging alone approaches the sampling prior. '
                   'This is a shape-reconstruction capacity/optimization pilot, NOT '
                   'Hawk-Dove conditional prediction or evidence of forward-training equivalence.'}
    _atomic_json(folder / 'summary.json', output)
    return output


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--only',type=parse_counts,default=DEFAULT_COUNTS,
                   help='Comma-separated positive bank counts; default 200,2000,20000,200000; '
                        'e.g. --only 5000,50000')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--steps',type=int,default=50)
    p.add_argument('--cpu-threads',type=int,default=4,
                   help='Cap CPU intra-op parallelism when --device cpu (default 4)')
    p.add_argument('--probe-every',type=int,default=10)
    p.add_argument('--grid-bins',type=int,default=256)
    p.add_argument('--shape',choices=tuple(SHAPES),default='triple')
    p.add_argument('--lr',type=float,default=0.2)
    p.add_argument('--seed',type=int,default=20261021)
    p.add_argument('--memory-limit-mb',type=float,default=1024.0,
                   help='Conservative estimated per-experiment scratch memory budget')
    p.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    p.add_argument('--rerun',action='store_true',
                   help='Repeat a completed run with identical saved identity')
    return p


def run(args) -> dict:
    if args.steps < 1 or args.probe_every < 1 or args.grid_bins < 32:
        raise ValueError('POPULATION_INVALID_STEPS_OR_GRID')
    if not math.isfinite(args.lr) or args.lr <= 0 or args.lr > 10:
        raise ValueError('POPULATION_INVALID_LEARNING_RATE')
    if not math.isfinite(args.memory_limit_mb) or args.memory_limit_mb <= 0:
        raise ValueError('POPULATION_INVALID_MEMORY_LIMIT')
    if args.cpu_threads < 1:
        raise ValueError('POPULATION_INVALID_CPU_THREADS')
    if args.device == 'cpu':
        torch.set_num_threads(args.cpu_threads)
    if args.device == 'cuda' and not torch.cuda.is_available():
        raise ValueError('POPULATION_CUDA_UNAVAILABLE_USE_DEVICE_CPU')
    # Preflight ALL counts before launching the first sequential run.
    for count in args.only:
        if _memory_estimate_mb(count) > args.memory_limit_mb:
            raise ValueError(f'POPULATION_MEMORY_BUDGET_EXCEEDED:{count}:estimated='
                             f'{_memory_estimate_mb(count):.1f}MB')
        folder = args.output / f'banks-{count:09d}'
        config = folder / 'config.json'
        if config.exists() and json.loads(config.read_text('utf-8')) != _identity(args, count):
            raise ValueError(f'POPULATION_CONFIG_DRIFT:{folder}')
        if folder.exists() and not config.exists() and any(folder.iterdir()):
            raise ValueError(f'POPULATION_PARTIAL_UNKNOWN_RUN:{folder}')
    reports = []
    for count in args.only:  # user order; experiments do not run concurrently
        reports.append(train_population(args, count))
    # Comparable, concise results at the top level, in requested order.
    comparison = {'schema':SCHEMA+'.comparison', 'order':list(args.only),
                  'comparison_kind':'shape_only_fixed_update_budget',
                  'records':[{'bank_count':r['identity']['bank_count'],
                    'uniform_total_variation':r['uniform_population_baseline']['total_variation'],
                    'final_total_variation':r['final_reconstruction']['total_variation'],
                    'uniform_kl':r['uniform_population_baseline']['kl_target_to_reconstruction'],
                    'final_kl':r['final_reconstruction']['kl_target_to_reconstruction'],
                    'elapsed_seconds':r['elapsed_seconds']}
                    for r in reports]}
    _atomic_json(args.output / 'comparison.json', comparison)
    print(json.dumps(comparison, indent=2, sort_keys=True, allow_nan=False), flush=True)
    return comparison


def main(argv=None):
    args=parser().parse_args(argv)
    return run(args)

if __name__ == '__main__':
    main()
