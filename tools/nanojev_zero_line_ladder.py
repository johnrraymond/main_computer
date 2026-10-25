#!/usr/bin/env python3
"""Zero-line bit-radius flash-card ladder for the existing NanoJev digit-token backbone.

Exactly enumerates (A,B) at each radius. Full-deck 100% stable mastery is
required before expanding. Evaluates the unseen *next shell* BEFORE teaching it.
No arithmetic is performed in the model; Python addition creates labels only.
No task router, expert, Z prediction, or external inference executor.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path

import torch
from torch import nn
from torch.nn import functional as F

from tools.nanojev_addition_curriculum import AdditionNet, POWERS, objective

SCHEMA = 'nanojev.zero-line-bit-radius.v1'
RADII = (1, 3, 7, 15, 31, 63, 127)
GROUPS = ('zero', 'negative_near', 'positive_near', 'negative_far', 'positive_far')


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as src:
        for chunk in iter(lambda: src.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def atomic_checkpoint(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    torch.save(obj, tmp)
    os.replace(tmp, path)


def group_for(a, b):
    s = a + b
    if s == 0:
        return 'zero'
    return ('positive_' if s > 0 else 'negative_') + ('near' if abs(s) <= 2 else 'far')


def deck(radius, previous=0, shell_only=False):
    if radius not in RADII or previous >= radius or previous < 0:
        raise ValueError('ZERO_LINE_INVALID_RADIUS')
    pairs = [(a, b) for a in range(-radius, radius + 1)
             for b in range(-radius, radius + 1)
             if not shell_only or previous == 0 or max(abs(a), abs(b)) > previous]
    if not pairs:
        raise RuntimeError('ZERO_LINE_EMPTY_DECK')
    return {'x': torch.tensor(pairs, dtype=torch.long),
            'y': torch.tensor([a + b for a, b in pairs], dtype=torch.long),
            'groups': [group_for(a, b) for a, b in pairs],
            'radius': radius, 'previous': previous,
            'kind': 'unseen_shell_pretraining' if shell_only else 'exposed_flashcard_mastery'}


def sample_deck(rows, maximum, seed):
    if maximum <= 0 or len(rows['y']) <= maximum:
        return rows
    gen = torch.Generator().manual_seed(seed)
    # Keep positive, negative, zero and small residuals represented.
    picks = []
    for group in GROUPS:
        idx = [i for i, g in enumerate(rows['groups']) if g == group]
        if idx:
            p = torch.tensor(idx)[torch.randperm(len(idx), generator=gen)[:maximum // 5]]
            picks += p.tolist()
    seen = set(picks)
    for j in torch.randperm(len(rows['y']), generator=gen).tolist():
        if len(picks) >= maximum:
            break
        if j not in seen:
            picks.append(j)
            seen.add(j)
    return {'x': rows['x'][picks], 'y': rows['y'][picks],
            'groups': [rows['groups'][i] for i in picks],
            'radius': rows['radius'], 'previous': rows['previous'], 'kind': rows['kind']}


def shifted(x, radius, generator):
    """Produce a different in-domain representation of the SAME sum."""
    out = x.clone()
    for i, (a, b) in enumerate(x.tolist()):
        low, high = max(-radius - a, b - radius), min(radius - a, b + radius)
        shifts = [k for k in range(low, high + 1) if k != 0]
        if shifts:
            k = shifts[int(torch.randint(len(shifts), (1,), generator=generator))]
            out[i, 0] += k
            out[i, 1] -= k
        else:
            out[i] = x[i].flip(0)
    return out


def make_augmentations(x, radius, gen):
    # Equal sums across different compositions. Identity probes use the same
    # neural model, NOT a precomputed residual or a hand-coded inference path.
    n = len(x)
    other = shifted(x, radius, gen)
    swap = torch.rand(n, generator=gen) < .5
    other[swap] = x[swap].flip(-1)
    half = max(1, n // 4)
    left = torch.stack((x[:half, 0], torch.zeros(half, dtype=torch.long)), -1)
    right = torch.stack((torch.zeros(half, dtype=torch.long), x[:half, 1]), -1)
    xx = torch.cat((x, other, left, right), dim=0)
    yy = torch.cat((x.sum(-1), x.sum(-1), x[:half, 0], x[:half, 1]), dim=0)
    return xx, yy, n


def prediction_values(pred):
    powers = torch.tensor(POWERS, dtype=torch.long, device=pred['digits'].device)
    digits = pred['digits'].argmax(-1).long()
    magnitudes = (digits * powers.unsqueeze(0)).sum(-1)  # CUDA-safe
    sign = pred['sign'].argmax(-1).long() - 1
    return sign * magnitudes, sign, digits


@torch.no_grad()
def evaluate(model, rows, device, batch_size=256, max_cards=0, seed=0):
    rows = sample_deck(rows, max_cards, seed)
    model.eval()
    correct = []
    wrong_zero = 0
    predicted_zero = 0
    mae = 0.
    for block in rows['x'].split(batch_size):
        pred = model(block.to(device))
        values, signs, digits = prediction_values(pred)
        truth = block.sum(-1).to(device)
        power = torch.tensor(POWERS, device=device, dtype=torch.long)
        expected_digits = (truth.abs().unsqueeze(-1) // power) % 10
        # Require sign, digits, and resulting value to agree, so a constant
        # zero predictor cannot exploit a nonzero sign and zero magnitudes.
        ok = ((values == truth) & (signs == truth.sign()) &
              (digits == expected_digits).all(-1))
        correct.extend(ok.cpu().tolist())
        predicted_zero += int((values == 0).sum())
        wrong_zero += int(((values == 0) & (truth != 0)).sum())
        mae += float((values - truth).abs().sum())
    groups = {}
    for g in ['all', *GROUPS]:
        inds = range(len(correct)) if g == 'all' else (i for i, v in enumerate(rows['groups']) if v == g)
        idx = list(inds)
        if idx:
            groups[g] = {'passed': sum(int(correct[i]) for i in idx),
                         'total': len(idx), 'accuracy': sum(int(correct[i]) for i in idx) / len(idx)}
    return {'kind': rows['kind'], 'radius': rows['radius'],
            'evaluated': len(correct), 'total_deck': (2 * rows['radius'] + 1) ** 2,
            'groups': groups, 'false_zero': wrong_zero,
            'predicted_zero': predicted_zero, 'mae': mae / len(correct)}


@torch.no_grad()
def write_error_map(model, rows, device, batch_size, path):
    """Write a 2D map containing one row per (A,B) flash card."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    model.eval()
    with temporary.open('w', encoding='utf-8', newline='') as fp:
        writer = csv.writer(fp)
        writer.writerow(('A', 'B', 'target_delta', 'predicted_delta',
                         'predicted_sign', 'exact', 'stratum'))
        for start in range(0, len(rows['y']), batch_size):
            block = rows['x'][start:start + batch_size]
            output = model(block.to(device))
            predictions, signs, digits = prediction_values(output)
            for (a, b), truth, result, sign, group in zip(
                    block.tolist(), rows['y'][start:start + batch_size].tolist(),
                    predictions.cpu().tolist(), signs.cpu().tolist(),
                    rows['groups'][start:start + batch_size]):
                writer.writerow((a, b, truth, result, sign,
                                 int(result == truth and sign == ((truth > 0) - (truth < 0))),
                                 group))
    os.replace(temporary, path)


@torch.no_grad()
def invariance_audit(model, rows, radius, device, batch_size, seed, limit=256):
    """Behavioral tests of commutativity, A+0, B+0 and balanced translation."""
    rows = sample_deck(rows, limit, seed)
    x = rows['x']
    gen = torch.Generator().manual_seed(seed + 1)
    views = {'original': x, 'swapped': x.flip(-1), 'shifted': shifted(x, radius, gen),
             'identity_A': torch.stack((x[:, 0], torch.zeros_like(x[:, 0])), -1),
             'identity_B': torch.stack((torch.zeros_like(x[:, 1]), x[:, 1]), -1)}
    outputs = {}
    for name, values in views.items():
        res = []
        for chunk in values.split(batch_size):
            pred = model(chunk.to(device))
            res.append(prediction_values(pred)[0].cpu())
        outputs[name] = torch.cat(res)
    y = rows['y']
    success = {}
    for name, target in [('swapped', y), ('shifted', y),
                         ('identity_A', x[:, 0]), ('identity_B', x[:, 1])]:
        good = (outputs['original'] == y) & (outputs[name] == target)
        success[name] = {'passed': int(good.sum()), 'total': len(x),
                         'accuracy': float(good.double().mean())}
    return {'n': len(x), 'views': success}


class CompleteDeckSampler:
    def __init__(self, n, generator, restored=None):
        self.n, self.gen = n, generator
        if restored is None:
            self.perm = torch.randperm(n, generator=generator)
            self.offset = 0
            self.cards_seen = 0
        else:
            # Restoring must not consume generator state: otherwise the
            # subsequent flash-card and transformation stream diverges.
            self.restore(restored)

    def take(self, count):
        result = []
        while sum(len(t) for t in result) < count:
            need = count - sum(len(t) for t in result)
            take = min(need, self.n - self.offset)
            result.append(self.perm[self.offset:self.offset + take])
            self.offset += take
            self.cards_seen += take
            if self.offset == self.n:
                self.perm = torch.randperm(self.n, generator=self.gen)
                self.offset = 0
        return torch.cat(result)

    def state(self):
        return {'perm': self.perm, 'offset': self.offset, 'cards_seen': self.cards_seen}

    def restore(self, state):
        self.perm = state['perm']
        self.offset = state['offset']
        self.cards_seen = state['cards_seen']


def sample_balanced_flashcards(rows, sampler, batch_size, generator):
    """Cycle the entire deck while concentrating extra practice near zero.

    Half of every batch advances a shuffled exhaustive deck sweep. The rest
    samples uniformly by residual stratum, so exact and near-zero cases do
    not vanish statistically as the radius grows. No task labels are input
    to the model; groups are only TRAIN sampling metadata.
    """
    sweep_count = max(1, batch_size // 2)
    indices = [sampler.take(sweep_count)]
    remainder = batch_size - sweep_count
    pools = {g: torch.tensor([i for i, x in enumerate(rows['groups']) if x == g],
                             dtype=torch.long) for g in GROUPS}
    active = [pools[g] for g in GROUPS if len(pools[g])]
    picked = []
    # Rotate the starting stratum instead of always favoring the first group.
    offset = int(torch.randint(len(active), (1,), generator=generator))
    for i in range(remainder):
        pool = active[(offset + i) % len(active)]
        picked.append(pool[int(torch.randint(len(pool), (1,), generator=generator))].item())
    if picked:
        indices.append(torch.tensor(picked, dtype=torch.long))
    combined = torch.cat(indices)
    return combined[torch.randperm(len(combined), generator=generator)]


def train_loss(model, x, radius, gen, device):
    combined, targets, n = make_augmentations(x, radius, gen)
    combined, targets = combined.to(device), targets.to(device)
    pred = model(combined)
    core = objective(pred, combined, targets, len(POWERS))
    # Same answer distribution for a transformed equal-sum pair. Do not force
    # latent A-bar and B-bar vectors to be identical; demand same behavior.
    def agreement(logits):
        p = logits[:n].softmax(-1)
        q = logits[n:2*n].softmax(-1)
        return (p - q).square().mean()
    consistency = agreement(pred['sign']) + agreement(pred['digits'])
    return core + .2 * consistency


def configuration(args):
    from tools import nanojev_addition_curriculum as source
    keys = ('seed', 'width', 'heads', 'layers', 'rounds', 'batch_size',
            'eval_batch_size', 'lr', 'probe_every', 'stability_probes',
            'cpu_threads', 'max_preprobe_cards', 'invariance_cards')
    init_hash = digest(args.init_checkpoint) if args.init_checkpoint else None
    return {'schema': SCHEMA, 'source': digest(__file__), 'dependency': digest(source.__file__),
            'init_checkpoint_sha256': init_hash,
            'settings': {k: getattr(args, k) for k in keys},
            'contract': 'fully exposed rung mastery; independent unseen-shell preprobe; no test inference or external arithmetic'}


def capture(model, optimizer, gen, sampler, idx, step, streak, reports, best_rank):
    return {'schema': SCHEMA + '.checkpoint', 'model': model.state_dict(),
            'optimizer': optimizer.state_dict(), 'generator': gen.get_state(),
            'torch_rng': torch.get_rng_state(),
            'cuda_rng': torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None,
            'sampler': sampler.state() if sampler else None,
            'idx': idx, 'step': step, 'streak': streak,
            'reports': reports, 'best_rank': best_rank}


def run(args):
    if args.device == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('ZERO_LINE_CUDA_UNAVAILABLE')
    if (args.max_radius not in RADII or args.steps_per_rung < 1 or args.probe_every < 1 or
            args.batch_size < 1 or args.eval_batch_size < 1 or args.stability_probes < 1 or
            args.cpu_threads < 1 or args.width < 8 or args.width % args.heads or
            args.rounds < 1 or args.layers < 1 or not (0 < args.lr < 1) or
            args.max_preprobe_cards < 5 or args.invariance_cards < 1):
        raise ValueError('ZERO_LINE_INVALID_ARGUMENTS')
    torch.set_num_threads(args.cpu_threads)
    root = Path(args.output).expanduser().resolve()
    conf = configuration(args)
    cfg = root / 'config.json'
    if not args.resume:
        if root.exists() and any(root.iterdir()):
            raise FileExistsError('ZERO_LINE_OUTPUT_OVERWRITE_REFUSED')
        root.mkdir(parents=True, exist_ok=True)
        atomic_json(cfg, conf)
    elif not cfg.exists() or json.loads(cfg.read_text('utf-8')) != conf:
        raise RuntimeError('ZERO_LINE_CONFIG_DRIFT')
    torch.manual_seed(args.seed)
    model = AdditionNet('digit_tokens', args.width, args.heads, args.layers, args.rounds, args.seed).to(args.device)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.)
    gen = torch.Generator(device='cpu').manual_seed(args.seed + 3001)
    idx, step, streak, best_rank, reports, sampler_data = 0, 0, 0, (-1., -1.), {}, None
    latest = root / 'latest.pt'
    if args.resume:
        if not latest.is_file():
            raise FileNotFoundError('ZERO_LINE_RESUME_CHECKPOINT_MISSING')
        st = torch.load(latest, map_location='cpu', weights_only=True)
        if st.get('schema') != SCHEMA + '.checkpoint':
            raise RuntimeError('ZERO_LINE_BAD_CHECKPOINT')
        model.load_state_dict(st['model'], strict=True)
        opt.load_state_dict(st['optimizer'])
        gen.set_state(st['generator'])
        torch.set_rng_state(st['torch_rng'])
        if st['cuda_rng'] is not None and torch.cuda.is_available():
            torch.cuda.set_rng_state_all(st['cuda_rng'])
        idx, step, streak, reports, best_rank, sampler_data = (
            st['idx'], st['step'], st['streak'], st['reports'], tuple(st['best_rank']), st['sampler'])
    elif args.init_checkpoint:
        src = torch.load(args.init_checkpoint, map_location='cpu', weights_only=True)
        if not src.get('model') or src.get('arm') != 'digit_tokens' or src.get('stage') != 'signed':
            raise RuntimeError('ZERO_LINE_INIT_MUST_BE_SIGNED_DIGIT_CHECKPOINT')
        model.load_state_dict(src['model'], strict=True)
    stages = [r for r in RADII if r <= args.max_radius]
    if idx >= len(stages):
        print('ZERO_LINE_ALREADY_COMPLETED=TRUE')
        return reports
    for j in range(idx, len(stages)):
        radius = stages[j]
        rows = deck(radius)
        if j != idx:
            step, streak, best_rank, sampler_data = 0, 0, (-1., -1.), None
        sampler = CompleteDeckSampler(len(rows['y']), gen, sampler_data)
        prev = stages[j-1] if j else 0
        if radius > 1 and f'r{radius}' not in reports:
            before = evaluate(model, sample_deck(deck(radius, prev, True), args.max_preprobe_cards,
                            args.seed + radius), args.device, args.eval_batch_size)
            reports[f'r{radius}'] = {'before_training_shell': before}
            atomic_json(root / 'rungs' / f'radius_{radius}.json', reports[f'r{radius}'])
        if step >= args.steps_per_rung:
            raise RuntimeError('ZERO_LINE_RESUME_INCREASE_STEPS_PER_RUNG')
        for number in range(step+1, args.steps_per_rung+1):
            model.train()
            indices = sample_balanced_flashcards(rows, sampler, args.batch_size, gen)
            inputs = rows['x'].index_select(0, indices)
            loss = train_loss(model, inputs, radius, gen, args.device)
            if not bool(torch.isfinite(loss)):
                raise FloatingPointError('ZERO_LINE_NONFINITE_LOSS')
            opt.zero_grad(set_to_none=True)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1., error_if_nonfinite=True)
            opt.step()
            if number % args.probe_every and number != args.steps_per_rung:
                continue
            mastery = evaluate(model, rows, args.device, args.eval_batch_size)
            transformations = invariance_audit(model, rows, radius, args.device,
                                                 args.eval_batch_size, args.seed + radius,
                                                 args.invariance_cards)
            min_accuracy = min(g['accuracy'] for g in mastery['groups'].values())
            inv_min = min(g['accuracy'] for g in transformations['views'].values())
            # Exactly learned current world, all groups, plus behavioral
            # composition. Consecutive probes enforce persistence while training.
            pass_now = min_accuracy == 1. and inv_min == 1. and sampler.cards_seen >= len(rows['y'])
            streak = streak + 1 if pass_now else 0
            report = reports.setdefault(f'r{radius}', {})
            report.update({'radius': radius, 'step': number, 'deck_count': len(rows['y']),
                           'mastery': mastery, 'composition': transformations,
                           'streak': streak, 'stability_target': args.stability_probes,
                           'passed': streak >= args.stability_probes})
            rank = (min_accuracy, inv_min)
            improved = rank > best_rank
            if improved:
                best_rank = rank
                atomic_checkpoint(root / 'rungs' / f'radius_{radius}_best.pt',
                                  {'schema': SCHEMA+'.best', 'radius': radius,
                                   'step': number, 'model': model.state_dict(), 'report': report})
            atomic_json(root / 'rungs' / f'radius_{radius}.json', report)
            if improved:
                write_error_map(model, rows, args.device, args.eval_batch_size,
                                root / 'rungs' / f'radius_{radius}_best_map.csv')
            print(f'ZERO_LINE radius={radius} cards={len(rows["y"])} step={number}/{args.steps_per_rung} '
                  f'loss={float(loss.detach()):.5f} mastery={mastery["groups"]["all"]["accuracy"]:.4f} '
                  f'minGroup={min_accuracy:.4f} invariant={inv_min:.4f} '
                  f'stability={streak}/{args.stability_probes} '
                  f'gate={"PASS" if report["passed"] else "FAIL"}', flush=True)
            if report['passed']:
                write_error_map(model, rows, args.device, args.eval_batch_size,
                                root / 'rungs' / f'radius_{radius}_passed_map.csv')
                atomic_checkpoint(root / 'rungs' / f'radius_{radius}_passed.pt',
                                  {'schema': SCHEMA+'.passed', 'radius': radius,
                                   'step': number, 'model': model.state_dict(), 'report': report})
                # Record held-out shell *before* learning the next rung.
                if j + 1 < len(stages):
                    next_radius = stages[j+1]
                    shell = sample_deck(deck(next_radius, radius, True),
                                        args.max_preprobe_cards, args.seed + next_radius)
                    outcome = evaluate(model, shell, args.device, args.eval_batch_size)
                    next_report = reports.setdefault(f'r{next_radius}', {})
                    next_report['before_training_shell'] = outcome
                    atomic_json(root / 'rungs' / f'radius_{next_radius}.json', next_report)
                    print('ZERO_LINE_UNSEEN_SHELL=' + json.dumps({
                        'next_radius': next_radius, 'cards': outcome['evaluated'],
                        'exact_rate': outcome['groups']['all']['accuracy']}, sort_keys=True), flush=True)
                idx, step, streak, best_rank = j + 1, 0, 0, (-1., -1.)
                atomic_checkpoint(latest, capture(model, opt, gen, None, idx, step, streak, reports, best_rank))
                break
            step = number
            atomic_checkpoint(latest, capture(model, opt, gen, sampler, j, step, streak, reports, best_rank))
        else:
            atomic_json(root / 'summary.json', {'schema': SCHEMA, 'status': 'FAIL_AT_RADIUS',
                        'radius': radius, 'reports': reports, 'TEST': None,
                        'hybridlink_certified': False})
            print('ZERO_LINE_SUMMARY=' + json.dumps({'status': 'FAIL_AT_RADIUS', 'radius': radius,
                  'mastered': [k for k,v in reports.items() if v.get('passed')]}, sort_keys=True), flush=True)
            print('ZERO_LINE_TEST_UNTOUCHED=TRUE HYBRIDLINK_UNCHANGED=TRUE', flush=True)
            return reports
        sampler_data = None
    atomic_json(root / 'summary.json', {'schema': SCHEMA, 'status': 'MASTERED_REQUESTED_RADII',
                'reports': reports, 'TEST': None, 'hybridlink_certified': False})
    print('ZERO_LINE_SUMMARY=' + json.dumps({'status': 'MASTERED_REQUESTED_RADII',
          'mastered': [k for k,v in reports.items() if v.get('passed')]}, sort_keys=True), flush=True)
    print('ZERO_LINE_TEST_UNTOUCHED=TRUE HYBRIDLINK_UNCHANGED=TRUE', flush=True)
    return reports


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--device', choices=('cpu', 'cuda'), default='cuda')
    p.add_argument('--init-checkpoint', type=Path, default=None,
                   help='Optional transfer arm; otherwise start an untrained backbone.')
    p.add_argument('--max-radius', type=int, default=31, choices=RADII)
    p.add_argument('--steps-per-rung', type=int, default=3000)
    p.add_argument('--probe-every', type=int, default=100)
    p.add_argument('--stability-probes', type=int, default=3)
    p.add_argument('--batch-size', type=int, default=128)
    p.add_argument('--eval-batch-size', type=int, default=256)
    p.add_argument('--max-preprobe-cards', type=int, default=2048)
    p.add_argument('--invariance-cards', type=int, default=256)
    p.add_argument('--width', type=int, default=64)
    p.add_argument('--heads', type=int, default=4)
    p.add_argument('--layers', type=int, default=2)
    p.add_argument('--rounds', type=int, default=2)
    p.add_argument('--lr', type=float, default=.001)
    p.add_argument('--seed', type=int, default=20261024)
    p.add_argument('--cpu-threads', type=int, default=4)
    p.add_argument('--resume', action='store_true')
    return p


def main(argv=None):
    return run(parser().parse_args(argv))


if __name__ == '__main__':
    main()
