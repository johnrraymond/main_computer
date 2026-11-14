#!/usr/bin/env python3
"""Staged neural addition: compact value tokens vs separate positioned digit tokens.

No arithmetic oracle in model inference. TRAIN labels use Python addition; DEV
is held-out. Exact-zero answers require predicted sign=zero and/or zero digits.
A failed DEV stage blocks later stages by default. TEST requires explicit opt-in.
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

SCHEMA = 'nanojev.progressive-addition.v2'
ARMS = ('compact', 'digit_tokens')
STAGES = ('atomic', 'no_carry', 'carry', 'signed', 'cancellation', 'generalization')
STAGE_DIGITS = (2, 2, 3, 3, 5, 7)
POWERS = tuple(10 ** i for i in range(7))
CATEGORIES = {
    'atomic': ('no_carry', 'carry'),
    'no_carry': ('no_carry',),
    'carry': ('units_only', 'hundreds_only', 'both'),
    'signed': ('negative_plus_negative', 'opposite_sign', 'zero'),
    'cancellation': ('away', 'near', 'zero'),
    'generalization': ('id_away', 'id_near', 'id_zero', 'ood_away', 'ood_near', 'ood_zero'),
}
ATOMIC_MASTERY_THRESHOLD = 1.0


def sha256(path):
    d = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1048576), b''):
            d.update(chunk)
    return d.hexdigest()


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(payload, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    os.replace(temp, path)


def write_torch(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    torch.save(payload, temp)
    os.replace(temp, path)


def fold(value, seed):
    h = hashlib.blake2b(f'{seed}:{value}'.encode(), digest_size=8).digest()
    x = int.from_bytes(h, 'little') % 100
    return 'train' if x < 76 else 'dev' if x < 88 else 'test'


def magnitude_fold(value, seed):
    return fold(abs(value), seed)


def pair_fold(a, b, seed):
    aa, bb = sorted((a, b))
    return fold(f'{aa},{bb}', seed)


def _stage_group(stage, a, b):
    """Classify operand pairs; does not participate in model inference."""
    if stage == 'atomic':
        return 'no_carry' if a + b < 10 else 'carry'
    if stage in ('no_carry', 'carry'):
        units = a % 10 + b % 10 >= 10
        hundreds = a // 10 + b // 10 + int(units) >= 10
        if not units and not hundreds:
            return 'no_carry'
        if stage == 'no_carry':
            return None
        return ('both' if units and hundreds else
                'units_only' if units else 'hundreds_only')
    if stage == 'signed':
        if a < 0 and b < 0:
            return 'negative_plus_negative'
        if a * b < 0:
            return 'zero' if a == -b else 'opposite_sign'
        return None
    raise ValueError('ADDITION_STAGE_CLASSIFICATION_UNSUPPORTED')


def _candidate_pair(rng, stage, category):
    band, kind = ('id', category) if stage == 'cancellation' else category.split('_', 1)
    lo, hi = ((100, 9999) if stage == 'cancellation' else
              (1000, 99999) if band == 'id' else (100000, 999999))
    mag = rng.randint(lo, hi)
    a = mag * (1 if rng.getrandbits(1) else -1)
    if kind == 'zero':
        return a, -a
    if kind == 'near':
        delta = rng.randint(1, max(1, mag // 100))
        delta *= 1 if rng.getrandbits(1) else -1
        b = -a + delta
        return (a, b) if lo <= abs(b) <= hi else None
    b = rng.randint(lo, hi) * (1 if rng.getrandbits(1) else -1)
    if abs(a + b) <= max(abs(a), abs(b)) / 100:
        return None
    return a, b


def make_stage(stage, split, seed, per_group=384):
    """TRAIN labels are exact; DEV/TEST partition full operand pairs.

    The *atomic* domain contains exactly 100 ordered examples and is deliberately
    fully exposed at training. Its DEV view is a mastery diagnostic over TRAIN,
    NOT a held-out score. Every subsequent stage uses order-invariant pair
    partitions. Signed/magnitude sign mirrors partition together. There is
    no TEST split for atomic because all 100 combinations are trained.
    """
    if stage not in STAGES or split not in ('train', 'dev', 'test') or per_group < 1:
        raise ValueError('ADDITION_BAD_DATASET_REQUEST')
    if stage == 'atomic' and split == 'test':
        raise ValueError('ADDITION_ATOMIC_NO_UNSEEN_TEST_DOMAIN')
    groups = CATEGORIES[stage]
    if stage == 'generalization' and split == 'train':
        groups = tuple(g for g in groups if g.startswith('id_'))
    rows = []
    if stage == 'atomic':
        rows = [(a, b, a + b, _stage_group('atomic', a, b))
                for a in range(10) for b in range(10)]
    elif stage in ('no_carry', 'carry', 'signed'):
        # Enumerate finite domains. Both operand orders and sign mirrors
        # must have one split; do not sample duplicates to reach a quota.
        domain = range(10, 100) if stage != 'signed' else range(-99, 100)
        pools = {g: [] for g in groups}
        for a in domain:
            if stage == 'signed' and a == 0:
                continue
            for b in domain:
                if stage == 'signed' and b == 0:
                    continue
                group = _stage_group(stage, a, b)
                if group not in pools:
                    continue
                # The sign-invariant signed partition prevents learning a
                # magnitude pair's positive example and evaluating its
                # negative mirror as though it were independent.
                key_a, key_b = (abs(a), abs(b)) if stage == 'signed' else (a, b)
                if pair_fold(key_a, key_b, seed) == split:
                    pools[group].append((a, b, a + b, group))
        for group in groups:
            pool = pools[group]
            if not pool:
                raise RuntimeError(f'ADDITION_EMPTY_FINITE_GROUP {stage}:{split}:{group}')
            random.Random(f'{seed}:{stage}:{split}:{group}').shuffle(pool)
            rows.extend(pool[:per_group])
    else:
        for group in groups:
            rng = random.Random(f'{seed}:{stage}:{split}:{group}')
            used = set()
            for _ in range(per_group * 1500):
                candidate = _candidate_pair(rng, stage, group)
                if candidate is None:
                    continue
                a, b = candidate
                # Pair-held-out split: individual digits and operands remain
                # reusable; the *computation* pair must be novel. The signed
                # mirror shares its split to prevent sign-based leakage.
                if pair_fold(abs(a), abs(b), seed) != split:
                    continue
                key = (a, b)
                if key in used:
                    continue
                used.add(key)
                rows.append((a, b, a + b, group))
                if len(used) == per_group:
                    break
            else:
                raise RuntimeError(f'ADDITION_DATA_EXHAUSTED stage={stage} split={split} group={group}')
    if not rows:
        raise RuntimeError('ADDITION_EMPTY_DATASET')
    random.Random(f'{seed}:shuffle:{stage}:{split}').shuffle(rows)
    return {'x': torch.tensor([(a, b) for a, b, _, _ in rows], dtype=torch.long),
            'y': torch.tensor([s for _, _, s, _ in rows], dtype=torch.long),
            'groups': [g for _, _, _, g in rows], 'stage': stage, 'split': split,
            'evaluation_kind': 'exposed_atomic_mastery' if stage == 'atomic' else 'pair_held_out'}


class CompactEncoding(nn.Module):
    """Prior 3-feature numerical representation: value, sign, log magnitude."""
    def __init__(self, width):
        super().__init__()
        self.encoder = nn.Sequential(nn.Linear(3, width), nn.Tanh(), nn.Linear(width, width))
        self.norm = nn.LayerNorm(width)
        self.role = nn.Embedding(2, width)

    def forward(self, x):
        v = x.float() / 1_000_000.
        # The old lab took log1p *after* dividing by 1e6; single-digit
        # numbers then became almost indistinguishable. Preserve a log feature
        # at their native magnitude while keeping the linear feature bounded.
        log_mag = torch.log1p(x.abs().float()) / 14.0
        features = torch.stack((v, v.sign(), log_mag), dim=-1)
        return self.norm(self.encoder(features) + self.role(torch.arange(2, device=x.device))[None])


class SeparateDigitEncoding(nn.Module):
    """Interleaved least-significant-first digit tokens: A0 B0 A1 B1 ...

    Extraction uses the value of each input; no carries or sums are supplied.
    This leaves the Transformer positions available for learned interactions.
    """
    def __init__(self, width):
        super().__init__()
        self.digit = nn.Embedding(10, width)
        self.place = nn.Embedding(len(POWERS), width)
        self.sign = nn.Embedding(2, width)
        self.role = nn.Embedding(2, width)
        self.norm = nn.LayerNorm(width)

    def forward(self, x):
        power = torch.tensor(POWERS, dtype=torch.long, device=x.device)
        d = (x.abs().unsqueeze(-1) // power) % 10  # [B,2,7]
        p = torch.arange(len(POWERS), device=x.device)
        role = torch.arange(2, device=x.device)
        tok = (self.digit(d) + self.place(p)[None, None] +
               self.sign((x < 0).long())[:, :, None] + self.role(role)[None, :, None])
        tok = tok.permute(0, 2, 1, 3).reshape(x.shape[0], 14, -1)
        return self.norm(tok)


class AdditionNet(nn.Module):
    def __init__(self, arm, width=64, heads=4, layers=2, rounds=2, seed=42):
        super().__init__()
        if arm not in ARMS or width % heads or rounds < 1 or layers < 1:
            raise ValueError('ADDITION_INVALID_MODEL')
        self.arm = arm
        self.rounds = rounds
        torch.manual_seed(seed + 17)
        self.input = CompactEncoding(width) if arm == 'compact' else SeparateDigitEncoding(width)
        torch.manual_seed(seed + 29)  # shared backbone / heads identical at initialization
        self.query = nn.Parameter(torch.zeros(width))
        self.pos = nn.Parameter(torch.zeros(15, width))
        encoder = nn.TransformerEncoderLayer(width, heads, width * 2, 0.,
                                             activation='gelu', batch_first=True, norm_first=True)
        self.backbone = nn.TransformerEncoder(encoder, num_layers=layers, enable_nested_tensor=False)
        self.norm = nn.LayerNorm(width)
        self.sign_head = nn.Linear(width, 3)
        self.digit_head = nn.Linear(width, 7 * 10)
        self.regression_head = nn.Linear(width, 1)

    def forward(self, x):
        if x.ndim != 2 or x.shape[1] != 2:
            raise ValueError('ADDITION_EXPECTS_TWO_INPUTS')
        tokens = self.input(x)
        state = self.query.expand(len(x), -1)
        n = tokens.shape[1] + 1
        mask = torch.ones(n, n, device=x.device, dtype=torch.bool).triu(1)
        for _ in range(self.rounds):
            context = torch.cat((tokens, state[:, None]), dim=1) + self.pos[:n]
            state = self.norm(self.backbone(context, mask=mask)[:, -1])
        return {'sign': self.sign_head(state),
                'digits': self.digit_head(state).reshape(-1, 7, 10),
                'ratio': self.regression_head(state).squeeze(-1)}


def target_digits(y):
    p = torch.tensor(POWERS, device=y.device, dtype=torch.long)
    return (y.abs().unsqueeze(-1) // p) % 10


def decode(pred, n_digits):
    """Only decodes predicted digits; never adds the two input operands."""
    power = torch.tensor(POWERS[:n_digits], device=pred['digits'].device, dtype=torch.long)
    digs = pred['digits'][:, :n_digits].argmax(-1).long()
    value = (digs * power.unsqueeze(0)).sum(-1)
    return (pred['sign'].argmax(-1).long() - 1) * value


def objective(pred, x, y, n_digits):
    sign = F.cross_entropy(pred['sign'], y.sign().long() + 1)
    digit = F.cross_entropy(pred['digits'][:, :n_digits].transpose(1, 2), target_digits(y)[:, :n_digits])
    # Dimensionless target allows retention across increasing number widths.
    scale = x.abs().amax(-1).clamp(min=1).float()
    ratio = F.smooth_l1_loss(pred['ratio'], y.float() / scale, beta=.1)
    return sign + 2. * digit + ratio


def score(prediction, truth, groups, tolerance=.05):
    errors = (prediction.double() - truth.double()).abs()
    success = errors <= truth.double().abs() * tolerance
    result = {}
    for group in ['all', *sorted(set(groups))]:
        indices = (torch.ones(len(truth), dtype=torch.bool) if group == 'all' else
                   torch.tensor([g == group for g in groups], dtype=torch.bool))
        result[group] = {'passed': int(success[indices].sum()),
                         'n': int(indices.sum()),
                         'rate': float(success[indices].double().mean()),
                         'mae': float(errors[indices].mean())}
    return result


@torch.no_grad()
def evaluate(model, dataset, device, batch_size, tolerance):
    model.eval()
    n = STAGE_DIGITS[STAGES.index(dataset['stage'])]
    dig, reg = [], []
    for chunk in dataset['x'].split(batch_size):
        inp = chunk.to(device)
        pred = model(inp)
        dig.append(decode(pred, n).cpu())
        reg.append((pred['ratio'] * inp.abs().amax(-1).clamp(min=1).float()).cpu())
    truth = dataset['y']
    return {'digit': score(torch.cat(dig), truth, dataset['groups'], tolerance),
            'regression': score(torch.cat(reg), truth, dataset['groups'], tolerance),
            'digit_exact': float((torch.cat(dig) == truth).float().mean()),
            'stage': dataset['stage'], 'split': dataset['split'],
            'evaluation_kind': dataset['evaluation_kind']}


def gate_metrics(stage, dev_metrics, retained_metrics, threshold):
    # Atomic addition is a fully exposed 100-pair mastery test, not a DEV
    # generalization claim. Require all pairs correct before composition.
    requirement = ATOMIC_MASTERY_THRESHOLD if stage == 'atomic' else threshold
    current = min(v['rate'] for k, v in dev_metrics['digit'].items() if k != 'all')
    retained_scores = {
        old: min(v['rate'] for k, v in item['digit'].items() if k != 'all')
        for old, item in retained_metrics.items()
    }
    retained = min(retained_scores.values()) if retained_scores else 1.
    retention_ok = all(rate >= (ATOMIC_MASTERY_THRESHOLD if old == 'atomic' else threshold)
                       for old, rate in retained_scores.items())
    return {'min_current': current, 'min_retained': retained,
            'pass': current >= requirement and retention_ok,
            'threshold': requirement, 'stage': stage,
            'evaluation_kind': dev_metrics.get('evaluation_kind',
                'exposed_atomic_mastery' if stage == 'atomic' else 'pair_held_out'),
            'retention_scores': retained_scores}


def identity(args):
    fields = ('seed', 'width', 'heads', 'layers', 'rounds', 'lr', 'batch_size',
              'eval_batch_size', 'train_per_group', 'dev_per_group', 'test_per_group',
              'replay_fraction', 'tolerance', 'threshold', 'probe_every', 'cpu_threads')
    return {'schema': SCHEMA, 'source_sha256': sha256(__file__),
            'options': {k: getattr(args, k) for k in fields},
            'policy': '100 ordered atomic TRAIN pairs as exposed mastery; novel-pair composition DEV and TEST; atomic 100 percent gate; TEST opt-in only; no oracle in inference'}


def capture(model, optimizer, generator, stage_idx, stage_step, best_rank, best_step, records, ident, arm):
    return {'schema': SCHEMA + '.state', 'identity': ident, 'arm': arm,
            'model': model.state_dict(), 'optimizer': optimizer.state_dict(),
            'sample_rng': generator.get_state(), 'torch_rng': torch.get_rng_state(),
            'cuda_rng': torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None,
            'stage_idx': stage_idx, 'stage_step': stage_step,
            'best_rank': list(best_rank), 'best_step': best_step, 'records': records}


def restore(path, model, optimizer, gen, ident, arm):
    state = torch.load(path, map_location='cpu', weights_only=True)
    if state.get('schema') != SCHEMA + '.state' or state.get('identity') != ident or state.get('arm') != arm:
        raise RuntimeError('ADDITION_CURRICULUM_IDENTITY_MISMATCH')
    model.load_state_dict(state['model'], strict=True)
    optimizer.load_state_dict(state['optimizer'])
    gen.set_state(state['sample_rng'])
    torch.set_rng_state(state['torch_rng'])
    if state['cuda_rng'] is not None and torch.cuda.is_available():
        torch.cuda.set_rng_state_all(state['cuda_rng'])
    return state


def data_cache(args):
    return {stage: {'train': make_stage(stage, 'train', args.seed, args.train_per_group),
                    'dev': make_stage(stage, 'dev', args.seed, args.dev_per_group)}
            for stage in STAGES}


def run_arm(arm, args, root, ident, datasets):
    directory = root / 'arms' / arm
    latest = directory / 'latest.pt'
    if not args.resume and directory.exists() and any(directory.iterdir()):
        raise FileExistsError('ADDITION_CURRICULUM_OVERWRITE_REFUSED')
    directory.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(args.seed + 101)
    model = AdditionNet(arm, args.width, args.heads, args.layers, args.rounds, args.seed).to(args.device)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.)
    rng = torch.Generator(device='cpu').manual_seed(args.seed + 709)
    stage_idx, stage_step, best_rank, best_step, records = 0, 0, (-1., -1., -1.), 0, {}
    if args.resume:
        if not latest.is_file():
            raise FileNotFoundError(f'ADDITION_CURRICULUM_RESUME_MISSING arm={arm}')
        state = restore(latest, model, opt, rng, ident, arm)
        stage_idx, stage_step = state['stage_idx'], state['stage_step']
        best_rank, best_step = tuple(state['best_rank']), state['best_step']
        records = state['records']
    if stage_idx >= len(STAGES):
        return {'status': 'PASS_ALL_DEV', 'records': records}
    for si in range(stage_idx, min(len(STAGES), args.stop_after_stage)):
        stage = STAGES[si]
        current = datasets[stage]['train']
        prev = [datasets[s]['train'] for s in STAGES[:si]]
        retained_dev = {s: datasets[s]['dev'] for s in STAGES[:si]}
        if si != stage_idx:
            stage_step, best_rank, best_step = 0, (-1., -1., -1.), 0
        if args.steps_per_stage <= stage_step:
            raise ValueError('ADDITION_CURRICULUM_INCREASE_STEPS_PER_STAGE_TO_RESUME')
        for step in range(stage_step + 1, args.steps_per_stage + 1):
            model.train()
            ids = torch.randint(len(current['y']), (args.batch_size,), generator=rng)
            xx = current['x'].index_select(0, ids)
            yy = current['y'].index_select(0, ids)
            if prev and args.replay_fraction:
                count = round(args.batch_size * args.replay_fraction)
                old = prev[(step - 1) % len(prev)]
                old_ids = torch.randint(len(old['y']), (count,), generator=rng)
                xx[:count] = old['x'].index_select(0, old_ids)
                yy[:count] = old['y'].index_select(0, old_ids)
            x, y = xx.to(args.device), yy.to(args.device)
            pred = model(x)
            # Replay digit widths vary: use max stage width, which covers
            # every old target; leading zeros remain supervised.
            loss = objective(pred, x, y, STAGE_DIGITS[si])
            if not torch.isfinite(loss):
                raise FloatingPointError('ADDITION_CURRICULUM_NONFINITE_LOSS')
            opt.zero_grad(set_to_none=True)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1., error_if_nonfinite=True)
            opt.step()
            if step % args.probe_every and step != args.steps_per_stage:
                continue
            train_m = evaluate(model, current, args.device, args.eval_batch_size, args.tolerance)
            dev_m = evaluate(model, datasets[stage]['dev'], args.device, args.eval_batch_size, args.tolerance)
            retention = {s: evaluate(model, dataset, args.device, args.eval_batch_size, args.tolerance)
                         for s, dataset in retained_dev.items()}
            gate = gate_metrics(stage, dev_m, retention, args.threshold)
            rank = (gate['min_current'], gate['min_retained'], dev_m['digit']['all']['rate'])
            if rank > best_rank:
                best_rank, best_step = rank, step
                record = {'stage': stage, 'step': step, 'train': train_m,
                          'dev': dev_m, 'retention': retention, 'gate': gate}
                records[stage] = record
                write_torch(directory / f'{stage}_best.pt',
                            {'schema': SCHEMA + '.best', 'identity': ident, 'arm': arm,
                             'stage': stage, 'step': step, 'model': model.state_dict(), 'result': record})
                write_json(directory / f'{stage}.json', record)
            label = 'MASTERY_min' if stage == 'atomic' else 'DEV_min'
            print(f'ADDITION_STAGE arm={arm} stage={stage} step={step}/{args.steps_per_stage} '
                  f'loss={float(loss.detach()):.5f} TRAIN={train_m["digit"]["all"]["rate"]:.4f} '
                  f'{label}={gate["min_current"]:.4f} retention_min={gate["min_retained"]:.4f} '
                  f'DEV_reg={dev_m["regression"]["all"]["rate"]:.4f} '
                  f'best_step={best_step} gate={"PASS" if gate["pass"] else "FAIL"}', flush=True)
            if gate['pass']:
                # Save passing weights, rather than a potentially earlier
                # best checkpoint selected with an uneven ranking.
                record = {'stage': stage, 'step': step, 'train': train_m,
                          'dev': dev_m, 'retention': retention, 'gate': gate}
                records[stage] = record
                write_torch(directory / f'{stage}_passed.pt',
                            {'schema': SCHEMA + '.passed', 'identity': ident, 'arm': arm,
                             'stage': stage, 'step': step, 'model': model.state_dict(), 'result': record})
                write_json(directory / f'{stage}.json', record)
                stage_idx, stage_step, best_rank, best_step = si + 1, 0, (-1., -1., -1.), 0
                write_torch(latest, capture(model, opt, rng, stage_idx, stage_step,
                                            best_rank, best_step, records, ident, arm))
                break
            stage_step = step
            write_torch(latest, capture(model, opt, rng, si, stage_step,
                                        best_rank, best_step, records, ident, arm))
        else:
            return {'status': f'FAIL_AT_{stage}', 'stage': stage, 'records': records}
    return {'status': 'PASS_ALL_DEV' if stage_idx == len(STAGES) else 'PAUSED',
            'next_stage': STAGES[stage_idx] if stage_idx < len(STAGES) else None,
            'records': records}


def run(args):
    if args.device == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('ADDITION_CURRICULUM_CUDA_UNAVAILABLE')
    if not (0 < args.lr < 1 and 0 < args.threshold <= 1 and 0 < args.tolerance < 1 and
            0 <= args.replay_fraction < 1 and args.width >= 8 and
            args.width % args.heads == 0 and args.layers > 0 and args.rounds > 0 and
            args.steps_per_stage > 0 and args.probe_every > 0 and args.batch_size > 0 and
            args.eval_batch_size > 0 and args.train_per_group > 0 and args.dev_per_group > 0 and
            args.test_per_group > 0 and args.cpu_threads > 0 and
            1 <= args.stop_after_stage <= len(STAGES)):
        raise ValueError('ADDITION_CURRICULUM_INVALID_OPTIONS')
    torch.set_num_threads(args.cpu_threads)
    selected = args.arms.split(',')
    if len(selected) != len(set(selected)) or not selected or any(a not in ARMS for a in selected):
        raise ValueError('ADDITION_CURRICULUM_BAD_ARMS')
    root = Path(args.output).expanduser().resolve()
    ident = identity(args)
    cfg = root / 'config.json'
    if args.phase == 'train' and not args.resume:
        if root.exists() and any(root.iterdir()):
            raise FileExistsError('ADDITION_CURRICULUM_OUTPUT_OVERWRITE_REFUSED')
        root.mkdir(parents=True, exist_ok=True)
        write_json(cfg, ident)
    elif not cfg.is_file() or json.loads(cfg.read_text(encoding='utf-8')) != ident:
        raise RuntimeError('ADDITION_CURRICULUM_CONFIG_DRIFT')
    if args.phase == 'test':
        if not args.authorize_test:
            raise RuntimeError('ADDITION_CURRICULUM_TEST_REQUIRES_AUTHORIZATION')
        if (root / 'final_test.json').exists():
            raise RuntimeError('ADDITION_CURRICULUM_TEST_ALREADY_RUN')
        # Authorization alone is insufficient: every stage for every arm
        # must have passed its DEV gate before any TEST dataset is built.
        for arm in selected:
            statefile = root / 'arms' / arm / 'latest.pt'
            if not statefile.is_file():
                raise RuntimeError('ADDITION_CURRICULUM_TEST_MISSING_STATE')
            st = torch.load(statefile, map_location='cpu', weights_only=True)
            if st.get('identity') != ident or st.get('stage_idx') != len(STAGES):
                raise RuntimeError('ADDITION_CURRICULUM_TEST_BLOCKED_BY_DEV')
        outcome = {}
        for arm in selected:
            st = torch.load(root / 'arms' / arm / 'latest.pt', map_location='cpu', weights_only=True)
            model = AdditionNet(arm, args.width, args.heads, args.layers, args.rounds, args.seed).to(args.device)
            model.load_state_dict(st['model'])
            # The 100 ordered atomic cases were all in TRAIN. Reporting them
            # under TEST would invent independent evidence that cannot exist.
            outcome[arm] = {stage: evaluate(model, make_stage(stage, 'test', args.seed,
                                       args.test_per_group), args.device,
                                       args.eval_batch_size, args.tolerance)
                            for stage in STAGES if stage != 'atomic'}
        write_json(root / 'final_test.json', {'schema': SCHEMA + '.test', 'result': outcome})
        print('ADDITION_CURRICULUM_TEST=' + json.dumps(outcome, sort_keys=True), flush=True)
        return outcome
    datasets = data_cache(args)
    result = {arm: run_arm(arm, args, root, ident, datasets) for arm in selected}
    write_json(root / 'summary.json', {'schema': SCHEMA + '.summary', 'arms': result,
                                      'TEST': None, 'hybridlink_certified': False})
    print('ADDITION_CURRICULUM_SUMMARY=' + json.dumps({a: {'status': r['status'],
          'passed_stages': [s for s in STAGES if s in r['records'] and r['records'][s]['gate']['pass']]}
          for a, r in result.items()}, sort_keys=True), flush=True)
    print('ADDITION_TEST_UNTOUCHED=TRUE HYBRIDLINK_UNCHANGED=TRUE', flush=True)
    return result


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--phase', choices=('train', 'test'), default='train')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--device', choices=('cpu', 'cuda'), default='cuda')
    p.add_argument('--arms', default='compact,digit_tokens')
    p.add_argument('--steps-per-stage', type=int, default=3000)
    p.add_argument('--stop-after-stage', type=int, default=6)
    p.add_argument('--probe-every', type=int, default=250)
    p.add_argument('--batch-size', type=int, default=128)
    p.add_argument('--eval-batch-size', type=int, default=256)
    p.add_argument('--train-per-group', type=int, default=1024)
    p.add_argument('--dev-per-group', type=int, default=128)
    p.add_argument('--test-per-group', type=int, default=128)
    p.add_argument('--width', type=int, default=64)
    p.add_argument('--heads', type=int, default=4)
    p.add_argument('--layers', type=int, default=2)
    p.add_argument('--rounds', type=int, default=2)
    p.add_argument('--lr', type=float, default=.001)
    p.add_argument('--seed', type=int, default=20261024)
    p.add_argument('--cpu-threads', type=int, default=4)
    p.add_argument('--replay-fraction', type=float, default=.25)
    p.add_argument('--tolerance', type=float, default=.05)
    p.add_argument('--threshold', type=float, default=.8)
    p.add_argument('--resume', action='store_true')
    p.add_argument('--authorize-test', action='store_true')
    return p


def main(argv=None):
    return run(parser().parse_args(argv))


if __name__ == '__main__':
    main()
