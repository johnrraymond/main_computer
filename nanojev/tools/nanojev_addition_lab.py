#!/usr/bin/env python3
"""Standalone neural addition lab: continuous vs decimal-position thought encodings.

Neither inference path computes a+b or receives teacher intermediate arithmetic.
Both representations use the SAME recurrent causal Transformer and learned heads.
TRAIN is balanced across away, near-cancellation and exact-cancellation tasks.
DEV has in-distribution and high-magnitude OOD tasks; TEST is opt-in only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random

import torch
from torch import nn
from torch.nn import functional as F

SCHEMA = 'nanojev.addition-lab.v1'
ARMS = ('continuous', 'positional')
STRATA = ('away', 'near', 'zero')
BANDS = ('id', 'ood')
POWERS = tuple(10 ** i for i in range(7))
MAX_OUTPUT = 4_000_000


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1048576), b''):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def atomic_torch(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    torch.save(data, tmp)
    os.replace(tmp, path)


def bucket(magnitude, seed):
    """Pseudorandom *magnitude* partition; signed counterparts never cross splits.

    Unlike modulo-based partitions, close magnitudes are not assigned the same
    split predictably. Zero is excluded as an operand in the first laboratory.
    """
    if magnitude <= 0:
        raise ValueError('ADDITION_ZERO_OPERAND_EXCLUDED')
    x = (int(magnitude) ^ int(seed)) & ((1 << 64) - 1)
    x ^= x >> 30
    x = (x * 0xbf58476d1ce4e5b9) & ((1 << 64) - 1)
    x ^= x >> 27
    x = (x * 0x94d049bb133111eb) & ((1 << 64) - 1)
    x ^= x >> 31
    v = x % 100
    return 'train' if v < 80 else ('dev' if v < 90 else 'test')


def choose_magnitude(rng, lo, hi, logarithmic=True):
    if lo > hi:
        raise ValueError('ADDITION_BAD_RANGE')
    if not logarithmic or rng.random() < 0.5:
        return rng.randint(lo, hi)
    # Log-sampling gives smaller magnitudes meaningful coverage in TRAIN.
    import math
    return min(hi, max(lo, round(math.exp(rng.uniform(math.log(lo), math.log(hi))))))


def _case(rng, split, stratum, band, seed, train_bound, ood_start, operand_bound):
    lo, hi = (ood_start, operand_bound) if band == 'ood' else (1, train_bound)
    if stratum == 'near':
        lo = max(lo, 1000)
    for _ in range(20000):
        aa = choose_magnitude(rng, lo, hi, logarithmic=band == 'id')
        if bucket(aa, seed) != split:
            continue
        a = aa * (1 if rng.getrandbits(1) else -1)
        if stratum == 'zero':
            b = -a
        elif stratum == 'near':
            max_delta = max(1, aa // 100)  # |a+b| <= 1% of |a|
            delta = choose_magnitude(rng, 1, max_delta)
            delta *= (1 if rng.getrandbits(1) else -1)
            b = -a + delta
            if not lo <= abs(b) <= hi:
                continue
            if bucket(abs(b), seed) != split:
                continue
        else:
            bb = choose_magnitude(rng, lo, hi, logarithmic=band == 'id')
            if bucket(bb, seed) != split:
                continue
            b = bb * (1 if rng.getrandbits(1) else -1)
            if a + b == 0 or abs(a + b) <= 0.01 * max(abs(a), abs(b)):
                continue
        if b == 0 or abs(b) > operand_bound:
            continue
        s = a + b
        if (stratum == 'zero' and s == 0 or
            stratum == 'near' and 0 < abs(s) <= 0.01 * max(abs(a), abs(b)) or
            stratum == 'away' and abs(s) > 0.01 * max(abs(a), abs(b))):
            return (a, b, s, stratum, band)
    raise RuntimeError('ADDITION_SPLIT_GENERATION_EXHAUSTED')


def make_split(split, size, seed, train_bound=1_600_000,
               ood_start=1_800_000, operand_bound=2_000_000):
    if split not in ('train', 'dev', 'test') or size < 6 or size % (3 if split == 'train' else 6):
        raise ValueError('ADDITION_SPLIT_SIZE_INVALID')
    if not 1000 < train_bound < ood_start <= operand_bound or 2 * operand_bound >= 10 ** len(POWERS):
        raise ValueError('ADDITION_DOMAIN_INVALID')
    rng = random.Random(seed + {'train': 1301, 'dev': 9827, 'test': 23479}[split])
    groups = [(cat, band) for cat in STRATA for band in (('id',) if split == 'train' else BANDS)]
    rows = [_case(rng, split, cat, band, seed, train_bound, ood_start, operand_bound)
            for cat, band in groups for _ in range(size // len(groups))]
    rng.shuffle(rows)
    return {'operands': torch.tensor([(r[0], r[1]) for r in rows], dtype=torch.long),
            'targets': torch.tensor([r[2] for r in rows], dtype=torch.long),
            'strata': [r[3] for r in rows], 'bands': [r[4] for r in rows]}


class ContinuousThoughts(nn.Module):
    """Same [normalized value, sign, log-magnitude] convention as HybridLink."""
    def __init__(self, width, operand_bound):
        super().__init__()
        self.operand_bound = operand_bound
        self.encoder = nn.Sequential(nn.Linear(3, width), nn.Tanh(), nn.Linear(width, width))
        self.norm = nn.LayerNorm(width)
        self.roles = nn.Embedding(2, width)

    def forward(self, numbers):
        nums = numbers.float() / self.operand_bound
        stats = torch.stack((nums, torch.sign(nums), torch.log1p(nums.abs())), dim=-1)
        return self.norm(self.encoder(stats) + self.roles(torch.arange(2, device=numbers.device))[None])


class PositionThoughts(nn.Module):
    """Two numeric thoughts, each learned from sign + seven positioned digits.

    Digit extraction is INPUT representation only, not a carry routine or sum.
    """
    def __init__(self, width, operand_bound):
        super().__init__()
        self.digit = nn.Embedding(10, width)
        self.place = nn.Embedding(len(POWERS), width)
        self.sign = nn.Embedding(2, width)
        self.roles = nn.Embedding(2, width)
        self.transform = nn.Sequential(nn.Linear(width, width), nn.GELU(), nn.Linear(width, width))
        self.norm = nn.LayerNorm(width)

    def forward(self, numbers):
        powers = torch.tensor(POWERS, dtype=torch.long, device=numbers.device)
        digits = (numbers.abs().unsqueeze(-1) // powers) % 10
        positioned = self.digit(digits) + self.place(torch.arange(len(POWERS), device=numbers.device))
        value = self.transform(F.gelu(positioned).sum(dim=2) / len(POWERS))
        sign = self.sign((numbers < 0).long())
        roles = self.roles(torch.arange(2, device=numbers.device))[None]
        return self.norm(value + sign + roles)


class SharedCausalBackbone(nn.Module):
    """Architecture matched to NanoJev's shared causal Transformer backbone."""
    def __init__(self, width, heads, layers):
        super().__init__()
        self.positions = nn.Parameter(torch.zeros(3, width))
        layer = nn.TransformerEncoderLayer(width, heads, width * 2, dropout=0.,
                                           activation='gelu', batch_first=True, norm_first=True)
        self.layers = nn.TransformerEncoder(layer, num_layers=layers, enable_nested_tensor=False)
        self.norm = nn.LayerNorm(width)

    def forward(self, tokens):
        if tokens.shape[1] != 3:
            raise ValueError('ADDITION_BACKBONE_CONTEXT_INVALID')
        mask = torch.ones(3, 3, device=tokens.device, dtype=torch.bool).triu(1)
        return self.norm(self.layers(tokens + self.positions, mask=mask))


class AdditionModel(nn.Module):
    def __init__(self, arm, width=64, heads=4, layers=2, rounds=2,
                 operand_bound=2_000_000, init_seed=123):
        super().__init__()
        if arm not in ARMS or width < 8 or width % heads or rounds < 1:
            raise ValueError('ADDITION_MODEL_OPTIONS_INVALID')
        self.arm, self.rounds = arm, rounds
        # Both input arms receive identical shared backbone and readout init.
        torch.manual_seed(init_seed + 17)
        self.input = (ContinuousThoughts(width, operand_bound) if arm == 'continuous'
                      else PositionThoughts(width, operand_bound))
        torch.manual_seed(init_seed + 29)
        self.query = nn.Parameter(torch.zeros(width))
        self.backbone = SharedCausalBackbone(width, heads, layers)
        self.sign_head = nn.Linear(width, 3)
        self.digit_head = nn.Linear(width, len(POWERS) * 10)
        self.regression_head = nn.Linear(width, 1)

    def forward(self, operands):
        if operands.ndim != 2 or operands.shape[1] != 2:
            raise ValueError('ADDITION_EXPECTS_TWO_OPERANDS')
        thoughts = self.input(operands)
        state = self.query.expand(operands.shape[0], -1)
        for _ in range(self.rounds):
            state = self.backbone(torch.cat((thoughts, state.unsqueeze(1)), dim=1))[:, -1, :]
        return {'sign': self.sign_head(state),
                'digits': self.digit_head(state).reshape(-1, len(POWERS), 10),
                'continuous': self.regression_head(state).squeeze(-1)}


def targets_as_digits(values):
    powers = torch.tensor(POWERS, dtype=torch.long, device=values.device)
    return (values.abs().unsqueeze(1) // powers) % 10


def decode(prediction):
    """Convert MODEL-PREDICTED digits to an integer, never calculate a+b."""
    powers = torch.tensor(POWERS, dtype=torch.long, device=prediction['digits'].device)
    # CUDA does not implement int64 matrix-vector matmul (addmv_impl_cuda).
    # Elementwise int64 multiplication and reduction work on both CPU and CUDA;
    # keep decoding exact, including negative values and exact zero.
    digits = prediction['digits'].argmax(-1).long()
    numbers = (digits * powers.unsqueeze(0)).sum(dim=-1)
    signs = prediction['sign'].argmax(-1).long() - 1
    return signs * numbers


def objective(pred, target):
    sign_target = torch.sign(target).long() + 1
    digits_target = targets_as_digits(target)
    sign_ce = F.cross_entropy(pred['sign'], sign_target)
    digit_ce = F.cross_entropy(pred['digits'].transpose(1, 2), digits_target)
    regression = F.smooth_l1_loss(pred['continuous'], target.float() / MAX_OUTPUT, beta=.05)
    return sign_ce + 2. * digit_ce + .5 * regression


def _one_score(pred, truth, strata, bands, tolerance):
    error = (pred - truth).abs()
    strict = error <= tolerance * truth.abs()
    # The zero target has zero tolerance; exact integer zero is required.
    results = {'all': {'instances': len(truth), 'passed': int(strict.sum()),
                       'rate': float(strict.float().mean()),
                       'mae': float(error.float().mean())}}
    for label in list(STRATA) + list(BANDS) + [f'{cat}_{band}' for cat in STRATA for band in BANDS]:
        selected = [i for i in range(len(truth)) if
                    (label == strata[i] or label == bands[i] or label == strata[i] + '_' + bands[i])]
        if selected:
            success = strict[selected]
            results[label] = {'instances': len(selected), 'passed': int(success.sum()),
                              'rate': float(success.float().mean()),
                              'mae': float(error[selected].float().mean())}
    return results


@torch.no_grad()
def evaluate(model, split, device, batch_size=256, tolerance=.05):
    model.eval()
    decoded, continuous, predicted_digits = [], [], []
    swapped, inverted, cancelled = [], [], []
    x = split['operands']
    for batch in x.split(batch_size):
        prediction = model(batch.to(device))
        decoded.append(decode(prediction).cpu())
        continuous.append((prediction['continuous'] * MAX_OUTPUT).cpu())
        predicted_digits.append(prediction['digits'].argmax(-1).cpu())
        # Causal identity probes receive new inputs, not correct answers.
        swapped.append(decode(model(batch[:, [1, 0]].to(device))).cpu())
        inverted.append(decode(model((-batch).to(device))).cpu())
        cancelled_input = torch.stack((batch[:, 0], -batch[:, 0]), dim=1)
        cancelled.append(decode(model(cancelled_input.to(device))).cpu())
    integer = torch.cat(decoded)
    analog = torch.cat(continuous)
    truth = split['targets']
    discrete = _one_score(integer, truth, split['strata'], split['bands'], tolerance)
    raw = _one_score(analog, truth, split['strata'], split['bands'], tolerance)
    expected_digits = targets_as_digits(truth)
    predicted_digits = torch.cat(predicted_digits)
    result = {'digit_output': discrete, 'continuous_output': raw,
              'arithmetic_identity_probes': {
                  'commutativity_exact': float((torch.cat(swapped) == integer).float().mean()),
                  'sign_reversal_exact': float((torch.cat(inverted) == -integer).float().mean()),
                  'self_cancellation_exact': float((torch.cat(cancelled) == 0).float().mean()),
                  'caveat': 'An always-zero network can pass these consistency tests; strict accuracy remains primary'},
              'digit_position_accuracy': [float(x) for x in
                                           (predicted_digits == expected_digits).float().mean(0)],
              'exact_integer_accuracy': float((integer == truth).float().mean()),
              'inference_uses_oracle': False}
    available = [discrete[f'{c}_{b}']['rate'] for c in STRATA for b in BANDS
                 if f'{c}_{b}' in discrete]
    result['min_group_strict5'] = min(available)
    result['gate'] = 'PASS_DEV_ONLY' if min(available) >= .8 else 'FAIL'
    return result


def identity(args):
    keys = ('seed', 'width', 'heads', 'layers', 'rounds', 'lr', 'batch_size',
            'eval_batch_size', 'train_size', 'dev_size', 'test_size', 'tolerance',
            'train_bound', 'ood_start', 'operand_bound', 'cpu_threads')
    return {'schema': SCHEMA, 'source_sha256': sha256(__file__),
            'options': {k: getattr(args, k) for k in keys},
            'policy': 'Training on TRAIN only; DEV selection; no TEST evaluation without explicit phase',
            'representation_policy': 'input values/sign/digits only; no oracle sums or arithmetic executor at inference'}


def checkpoint(model, optim, step, generator, best_rank, best_step, ident, arm):
    return {'schema': SCHEMA + '.checkpoint', 'arm': arm, 'identity': ident,
            'step': step, 'model': model.state_dict(), 'optimizer': optim.state_dict(),
            'sample_rng': generator.get_state(), 'torch_rng': torch.get_rng_state(),
            'cuda_rng': torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None,
            'best_rank': list(best_rank), 'best_step': best_step}


def load_state(path, ident, arm):
    state = torch.load(path, map_location='cpu', weights_only=True)
    if state.get('schema') != SCHEMA + '.checkpoint' or state.get('identity') != ident or state.get('arm') != arm:
        raise RuntimeError('ADDITION_CHECKPOINT_IDENTITY_DRIFT')
    return state


def train_arm(arm, args, ident, train, dev, root):
    directory = root / 'arms' / arm
    latest, best, report = (directory / 'latest.pt', directory / 'best.pt', directory / 'result.json')
    torch.manual_seed(args.seed + 101)
    model = AdditionModel(arm, args.width, args.heads, args.layers, args.rounds,
                          args.operand_bound, args.seed).to(args.device)
    optim = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.)
    sample_rng = torch.Generator(device='cpu').manual_seed(args.seed + 709)
    step0, best_rank, best_step = 0, (-1., -1.), 0
    if args.resume and latest.exists():
        state = load_state(latest, ident, arm)
        model.load_state_dict(state['model'], strict=True)
        optim.load_state_dict(state['optimizer'])
        sample_rng.set_state(state['sample_rng'])
        torch.set_rng_state(state['torch_rng'])
        if state['cuda_rng'] is not None and torch.cuda.is_available():
            torch.cuda.set_rng_state_all(state['cuda_rng'])
        step0, best_rank, best_step = state['step'], tuple(state['best_rank']), state['best_step']
    elif latest.exists() or directory.exists() and any(directory.iterdir()):
        raise FileExistsError('ADDITION_ARM_OVERWRITE_REFUSED')
    directory.mkdir(parents=True, exist_ok=True)
    if step0 > args.steps:
        raise ValueError('ADDITION_STEPS_BEHIND_CHECKPOINT')
    n = len(train['targets'])
    for step in range(step0 + 1, args.steps + 1):
        model.train()
        indices = torch.randint(n, (args.batch_size,), generator=sample_rng)
        inputs = train['operands'].index_select(0, indices).to(args.device)
        truth = train['targets'].index_select(0, indices).to(args.device)
        prediction = model(inputs)
        loss = objective(prediction, truth)
        if not bool(torch.isfinite(loss)):
            raise FloatingPointError('ADDITION_NONFINITE_LOSS')
        optim.zero_grad(set_to_none=True)
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), 1., error_if_nonfinite=True)
        optim.step()
        if step % args.probe_every == 0 or step == args.steps:
            metrics = evaluate(model, dev, args.device, args.eval_batch_size, args.tolerance)
            rank = (metrics['min_group_strict5'], metrics['digit_output']['all']['rate'])
            if rank > best_rank:
                best_rank, best_step = rank, step
                atomic_torch(best, {'schema': SCHEMA + '.best', 'arm': arm, 'identity': ident,
                                    'step': step, 'model': model.state_dict(), 'DEV': metrics})
                atomic_json(report, {'schema': SCHEMA + '.result', 'arm': arm, 'best_step': step,
                                     'DEV': metrics, 'checkpoint': str(best), 'TEST': None})
            atomic_torch(latest, checkpoint(model, optim, step, sample_rng,
                                            best_rank, best_step, ident, arm))
            print(f'ADDITION_TRAIN arm={arm} step={step}/{args.steps} loss={float(loss.detach()):.6f} '
                  f'DEV_minGroup={metrics["min_group_strict5"]:.4f} '
                  f'DEV_overall={metrics["digit_output"]["all"]["rate"]:.4f} '
                  f'DEV_nearID={metrics["digit_output"]["near_id"]["rate"]:.4f} '
                  f'DEV_zeroID={metrics["digit_output"]["zero_id"]["rate"]:.4f} '
                  f'bestStep={best_step}', flush=True)
    if not best.exists():
        raise RuntimeError('ADDITION_NO_DEV_BEST_CHECKPOINT')
    result = json.loads(report.read_text(encoding='utf-8'))
    result['latest_step'] = args.steps
    result['parameter_count'] = sum(p.numel() for p in model.parameters())
    atomic_json(report, result)
    return result


def load_best(arm, args, ident, root):
    path = root / 'arms' / arm / 'best.pt'
    if not path.is_file():
        raise FileNotFoundError(f'ADDITION_BEST_MISSING {arm}')
    state = torch.load(path, map_location='cpu', weights_only=True)
    if state.get('schema') != SCHEMA + '.best' or state.get('arm') != arm or state.get('identity') != ident:
        raise RuntimeError('ADDITION_BEST_IDENTITY_DRIFT')
    model = AdditionModel(arm, args.width, args.heads, args.layers,
                          args.rounds, args.operand_bound, args.seed).to(args.device)
    model.load_state_dict(state['model'], strict=True)
    return model, state, path


def run(args):
    if args.device == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('ADDITION_CUDA_UNAVAILABLE')
    if (not 0 < args.lr < 1 or not 0 < args.tolerance < 1 or args.steps <= 0 or
        args.batch_size <= 0 or args.eval_batch_size <= 0 or args.probe_every <= 0 or
        args.width < 8 or args.heads <= 0 or args.width % args.heads or args.layers < 1 or
        args.rounds < 1 or args.cpu_threads < 1 or
        args.train_size < 6 or args.train_size % 3 or args.dev_size < 6 or args.dev_size % 6 or
        args.test_size < 6 or args.test_size % 6):
        raise ValueError('ADDITION_OPTIONS_INVALID')
    if not 1000 < args.train_bound < args.ood_start <= args.operand_bound <= 2_000_000:
        raise ValueError('ADDITION_DOMAIN_INVALID')
    arms = args.arms.split(',')
    if not arms or len(set(arms)) != len(arms) or any(a not in ARMS for a in arms):
        raise ValueError('ADDITION_BAD_ARMS')
    torch.set_num_threads(args.cpu_threads)
    root = Path(args.output).expanduser().resolve()
    ident = identity(args)
    config = root / 'config.json'
    if args.phase == 'train' and not args.resume:
        if root.exists() and any(root.iterdir()):
            raise FileExistsError('ADDITION_OUTPUT_OVERWRITE_REFUSED')
        root.mkdir(parents=True, exist_ok=True)
        atomic_json(config, ident)
    elif not config.exists() or json.loads(config.read_text(encoding='utf-8')) != ident:
        raise RuntimeError('ADDITION_CONFIG_DRIFT_OR_MISSING')
    if args.phase == 'test' and not args.authorize_test:
        raise RuntimeError('ADDITION_TEST_REQUIRES_EXPLICIT_AUTHORIZATION')
    if args.phase == 'test' and (root / 'final_test.json').exists():
        raise RuntimeError('ADDITION_TEST_ALREADY_RECORDED')
    if args.phase == 'train':
        train = make_split('train', args.train_size, args.seed, args.train_bound,
                           args.ood_start, args.operand_bound)
        dev = make_split('dev', args.dev_size, args.seed, args.train_bound,
                         args.ood_start, args.operand_bound)
        result = {a: train_arm(a, args, ident, train, dev, root) for a in arms}
        atomic_json(root / 'comparison.json', {'schema': SCHEMA + '.comparison', 'arms': result,
                                               'split_counts': {'TRAIN': args.train_size,
                                                                'DEV': args.dev_size},
                                               'TEST': None, 'hybridlink_certified': False})
        print('ADDITION_LAB_COMPARISON=' + json.dumps({a: {'best_step': x['best_step'],
            'min_group': x['DEV']['min_group_strict5'],
            'overall': x['DEV']['digit_output']['all']['rate'],
            'gate': x['DEV']['gate']} for a, x in result.items()}, sort_keys=True), flush=True)
        print('ADDITION_TEST_UNTOUCHED=TRUE HYBRIDLINK_UNCHANGED=TRUE', flush=True)
        return result
    # Enforce the DEV gate before constructing or examining any TEST records.
    if args.phase == 'test':
        for arm in arms:
            _model, state, _path = load_best(arm, args, ident, root)
            if state['DEV']['gate'] != 'PASS_DEV_ONLY':
                raise RuntimeError('ADDITION_TEST_BLOCKED_BY_DEV_GATE')
    split = make_split('dev' if args.phase == 'dev' else 'test',
                       args.dev_size if args.phase == 'dev' else args.test_size,
                       args.seed, args.train_bound, args.ood_start, args.operand_bound)
    result = {}
    for arm in arms:
        model, state, path = load_best(arm, args, ident, root)
        if args.phase == 'test' and state['DEV']['gate'] != 'PASS_DEV_ONLY':
            raise RuntimeError('ADDITION_TEST_BLOCKED_BY_DEV_GATE')
        result[arm] = evaluate(model, split, args.device, args.eval_batch_size, args.tolerance)
    if args.phase == 'test':
        atomic_json(root / 'final_test.json', {'schema': SCHEMA + '.test', 'result': result,
                                               'DEV_gate_required': True,
                                               'best_sha256': {a: sha256(root / 'arms' / a / 'best.pt') for a in arms}})
    print('ADDITION_' + args.phase.upper() + '=' + json.dumps(result, sort_keys=True), flush=True)
    return result


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--phase', choices=('train', 'dev', 'test'), default='train')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--device', choices=('cpu', 'cuda'), default='cuda')
    p.add_argument('--arms', default='continuous,positional')
    p.add_argument('--steps', type=int, default=3000, help='Total steps PER arm')
    p.add_argument('--resume', action='store_true')
    p.add_argument('--probe-every', type=int, default=250)
    p.add_argument('--batch-size', type=int, default=256)
    p.add_argument('--eval-batch-size', type=int, default=512)
    p.add_argument('--width', type=int, default=64)
    p.add_argument('--heads', type=int, default=4)
    p.add_argument('--layers', type=int, default=2)
    p.add_argument('--rounds', type=int, default=2)
    p.add_argument('--lr', type=float, default=.001)
    p.add_argument('--seed', type=int, default=20261024)
    p.add_argument('--cpu-threads', type=int, default=4)
    p.add_argument('--train-size', type=int, default=24000)
    p.add_argument('--dev-size', type=int, default=3072)
    p.add_argument('--test-size', type=int, default=3072)
    p.add_argument('--train-bound', type=int, default=1600000)
    p.add_argument('--ood-start', type=int, default=1800000)
    p.add_argument('--operand-bound', type=int, default=2000000)
    p.add_argument('--tolerance', type=float, default=.05)
    p.add_argument('--authorize-test', action='store_true')
    return p


def main(argv=None):
    return run(parser().parse_args(argv))


if __name__ == '__main__':
    main()
