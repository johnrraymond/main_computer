#!/usr/bin/env python3
"""Offline, matched dense vs progressively group-LASSO/sparsified Hard EMP.

Never updates the original four-bank run, hard specialist, or detector. Both
arms start at the same saved specialist weights and AdamW state. Only shared
sign/digit READOUT INPUT CHANNELS are constrained; backbone neurons are not
claimed to have been pruned. Test families are never optimized upon.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import random
from pathlib import Path

import torch
from torch import nn

from tools import nanojev_emp_dual_bank as dual
from tools import nanojev_emp_dual_bank_ladder as ladder
from tools import nanojev_emp_hard_join as hard
from tools.nanojev_zero_line_zero_only import input_tensor
from tools.nanojev_zero_line_ladder import atomic_checkpoint, atomic_json
from tools.nanojev_zero_line_radius255_probe import sha256
from tools.nanojev_zero_line_sampled_radius_ladder import stable_fold

SCHEMA = 'nanojev.emp-hard-lasso.v1'


class ReadoutMask:
    """Shared deterministic gate on the final representation seen by sign/digit.

    No extra trainable model parameters and no modification to serialized
    AdditionNet state_dict. A zero mask disables one readout input channel
    in BOTH arithmetic heads; original network and optimizer remain intact.
    """
    def __init__(self, model, mask=None):
        if not isinstance(model.sign_head, nn.Linear) or not isinstance(model.digit_head, nn.Linear):
            raise RuntimeError('HARD_LASSO_UNSUPPORTED_READOUT')
        if model.sign_head.in_features != model.digit_head.in_features:
            raise RuntimeError('HARD_LASSO_READOUT_WIDTH_MISMATCH')
        width = model.sign_head.in_features
        self.model = model
        self.mask = (torch.ones(width, dtype=torch.float32, device=model.sign_head.weight.device)
                     if mask is None else mask.detach().clone().to(model.sign_head.weight.device))
        if self.mask.shape != (width,) or not bool(torch.all((self.mask == 0) | (self.mask == 1))):
            raise ValueError('HARD_LASSO_INVALID_MASK')
        self.hooks = [layer.register_forward_pre_hook(self._gate) for layer in
                      (model.sign_head, model.digit_head)]

    def _gate(self, _layer, inputs):
        return (inputs[0] * self.mask.to(dtype=inputs[0].dtype),)

    def close(self):
        for hook in self.hooks:
            hook.remove()
        self.hooks = []

    def groups(self):
        a = self.model.sign_head.weight
        b = self.model.digit_head.weight
        return torch.sqrt((a.square().sum(0) + b.square().sum(0)).clamp_min(1e-18))

    def penalty(self):
        scores = self.groups()
        return (scores * self.mask).sum() / self.mask.sum().clamp_min(1)

    def prune_to(self, fraction):
        if not 0 <= fraction < 1:
            raise ValueError('HARD_LASSO_INVALID_FRACTION')
        width = self.mask.numel()
        target = min(width - 1, round(width * fraction))
        count = int((self.mask == 0).sum().item())
        if target < count:
            raise RuntimeError('HARD_LASSO_CANNOT_REGROW_MASK')
        if target > count:
            scores = self.groups().detach().cpu().tolist()
            available = [i for i in range(width) if self.mask[i].item() == 1]
            for idx in sorted(available, key=lambda i: (scores[i], i))[:target - count]:
                self.mask[idx] = 0
        return {'removed': int((self.mask == 0).sum().item()),
                'active': int(self.mask.sum().item()),
                'active_fraction': self.mask.float().mean().item(),
                'removed_indices': (self.mask == 0).nonzero().flatten().cpu().tolist()}


def load_pairs(evidence_dir, max_train, max_dev, seed):
    """Preserve old accuracy-test train/validation split; never mix families."""
    files = sorted(evidence_dir.glob('*.pt'))
    if not files:
        raise FileNotFoundError('HARD_LASSO_NO_EVIDENCE')
    train, dev = {}, {}
    used_files = []
    for file in files:
        record = torch.load(file, map_location='cpu', weights_only=True)
        if record.get('schema') != hard.SCHEMA + '.evidence':
            raise RuntimeError('HARD_LASSO_INVALID_EVIDENCE')
        for source, dest in (('training_pairs', train), ('validation_pairs', dev)):
            for pair in record[source]:
                for kind in ('hard', 'easy'):
                    card = pair[kind]['card']
                    if len(card) < 5 or int(card[0]) + int(card[1]) != int(card[2]):
                        raise RuntimeError('HARD_LASSO_BAD_ARITHMETIC_CARD')
                h = pair['hard']
                if h['supplier_accuracy'] is None or h['supplier_accuracy'] <= .60:
                    raise RuntimeError('HARD_LASSO_UNQUALIFIED_SUPPLIER')
                key = (h['supplier'], int(h['card'][0]), int(h['card'][1]))
                dest.setdefault(key, pair)
        used_files.append([file.name, sha256(file)])
    # No exact operand may be trained in one supplier's training pair and
    # evaluated in any supplier's validation pair; enforce across suppliers.
    train_cards = {(int(e['card'][0]), int(e['card'][1])) for p in train.values()
                   for e in (p['hard'], p['easy'])}
    dev_cards = {(int(e['card'][0]), int(e['card'][1])) for p in dev.values()
                 for e in (p['hard'], p['easy'])}
    if train_cards & dev_cards:
        raise RuntimeError('HARD_LASSO_TRAIN_DEV_OVERLAP')
    rng = random.Random(f'lasso-evidence:{seed}')
    train_rows, dev_rows = list(train.values()), list(dev.values())
    rng.shuffle(train_rows)
    rng.shuffle(dev_rows)
    if not train_rows or not dev_rows:
        raise RuntimeError('HARD_LASSO_INSUFFICIENT_SPLITS')
    return train_rows[:max_train], dev_rows[:max_dev], used_files


def ood_cards(seed, count, minimum, maximum, train_pairs, dev_pairs):
    """Fixed held-out DEV magnitude families, signed and zero residuals."""
    if minimum < 1 or maximum <= minimum or maximum >= 1_000_000 or count < 8:
        raise ValueError('HARD_LASSO_INVALID_OOD_RANGE')
    seen = {(int(entry['card'][0]), int(entry['card'][1]))
            for pairs in (train_pairs, dev_pairs) for pair in pairs
            for entry in (pair['hard'], pair['easy'])}
    rng = random.Random(f'lasso-ood:{seed}:{minimum}:{maximum}')
    rows, attempts = [], 0
    while len(rows) < count and attempts < count * 1000:
        attempts += 1
        m = rng.randint(minimum, maximum)
        if stable_fold(m, seed) != 'dev':
            continue
        delta = (0, 1, -1, 2, -2, 3, -3, 8, -8, 16, -16, 32, -32)[len(rows) % 13]
        sign = -1 if len(rows) % 2 else 1
        a = sign * m
        b = -a + delta
        if (a, b) in seen:
            continue
        seen.add((a, b))
        rows.append((a, b, delta, 0, m))
    if len(rows) != count:
        raise RuntimeError('HARD_LASSO_OOD_EXHAUSTED')
    return rows


def evaluate_arithmetic(model, rows, device, batch_size):
    model.eval()
    correct = 0
    n = 0
    with torch.inference_mode():
        for start in range(0, len(rows), batch_size):
            cards = rows[start:start+batch_size]
            guesses = dual.decoded(model(input_tensor(cards, device)))
            correct += sum(hard.is_correct(r[2], p) for r, p in zip(cards, guesses))
            n += len(cards)
    return {'correct': correct, 'total': n, 'accuracy': correct / n if n else None}


def score(model, detector, dev, ood, device, batch_size, detector_gate):
    paired = hard.evaluate_specialist(detector, {'hard.left': model}, dev, device,
                                      batch_size=batch_size, detector_gate=detector_gate)
    test = evaluate_arithmetic(model, ood, device, batch_size)
    return {'paired': paired, 'heldout_magnitude': test}


def neuron_ablation(model, mask, dev, device, batch_size, count=6):
    """Intervention on top retained readout channels, not mere correlations."""
    if count <= 0:
        return []
    hard_rows = [p['hard']['card'] for p in dev]
    if not hard_rows:
        return []
    baseline = evaluate_arithmetic(model, hard_rows, device, batch_size)
    groups = mask.groups().detach().cpu().tolist()
    active = [i for i in range(mask.mask.numel()) if mask.mask[i].item() == 1]
    candidates = sorted(active, key=lambda i: (-groups[i], i))[:count]
    results = []
    for idx in candidates:
        mask.mask[idx] = 0
        try:
            tested = evaluate_arithmetic(model, hard_rows, device, batch_size)
        finally:
            mask.mask[idx] = 1
        results.append({'channel': idx, 'readout_group_norm': groups[idx],
                        'baseline_hard_accuracy': baseline['accuracy'],
                        'ablated_hard_accuracy': tested['accuracy'],
                        'accuracy_change': tested['accuracy'] - baseline['accuracy']})
    return results


def initialize(campaign, ladder_output, sidecar, device):
    cfg = json.loads((campaign/'config.json').read_text('utf-8'))
    # This is an OFFLINE read-only experiment. The ladder's source_config()
    # is a campaign-resume/migration interface and requires runtime gate and
    # trainer parameters. Do not call it to read the immutable source heads.
    # Verify all four source snapshots against the campaign's original hashes
    # before reconstructing the accepted reverse-trained population.
    for filename, key in (('source_dual.pt', 'source_checkpoint_sha256'),
                          ('source_config.json', 'source_config_sha256'),
                          ('source_left.pt', 'left_source_sha256'),
                          ('source_right.pt', 'right_source_sha256')):
        if sha256(campaign/filename) != cfg.get(key):
            raise RuntimeError(f'HARD_LASSO_SOURCE_SNAPSHOT_DRIFT:{filename}')
    left = dual.checked_source(campaign/'source_left.pt')
    right = dual.checked_source(campaign/'source_right.pt')
    base, _ = ladder.load_certified_model(str(ladder_output), 'cpu')
    reverse_cp = campaign/'hard_emp_reverse'/'stages'/'zero_line'/'accepted.pt'
    if not reverse_cp.is_file():
        raise FileNotFoundError('HARD_LASSO_REQUIRES_COMPLETED_REVERSE')
    saved = torch.load(reverse_cp, map_location='cpu', weights_only=True)
    if saved.get('radius') != 0:
        raise RuntimeError('HARD_LASSO_WRONG_REVERSE_CHECKPOINT')
    banks = ladder.from_source(base, left, right, saved['banks'], 'cpu')
    state = torch.load(sidecar, map_location='cpu', weights_only=True)
    selected = state.get('selected')
    if not selected or 'hard.left' not in hard.SLOTS:
        raise RuntimeError('HARD_LASSO_NO_TRAINED_SPECIALIST')
    units, detector, opts = hard.make_specialist(banks, tuple(selected), 'cpu', .0001)
    units.load_state_dict(state['specialists'], strict=True)
    detector.load_state_dict(state['detector'], strict=True)
    primary = units['hard__left'].to(device)
    detector.to(device).eval().requires_grad_(False)
    return primary, detector, state['optimizers']['hard.left'], sha256(sidecar), sha256(reverse_cp)


def train_step(model, opt, mask, sampled, device, regularizer):
    model.train()
    x = input_tensor([p['hard']['card'] for p in sampled], device)
    gold = torch.tensor([p['hard']['card'][2] for p in sampled], device=device, dtype=torch.long)
    opt.zero_grad(set_to_none=True)
    ce = dual.loss_per_card(model(x), gold).mean()
    loss = ce + regularizer * mask.penalty()
    if not bool(torch.isfinite(loss)):
        raise FloatingPointError('HARD_LASSO_NONFINITE_LOSS')
    loss.backward()
    nn.utils.clip_grad_norm_(model.parameters(), 1.0, error_if_nonfinite=True)
    opt.step()
    return float(ce.detach().item())


def parse_schedule(s):
    values = [float(item) for item in s.split(',')]
    if len(values) < 2 or abs(values[0]) > 1e-12 or any(not math.isfinite(x) or x < 0 or x >= 1 for x in values):
        raise ValueError('HARD_LASSO_BAD_SCHEDULE')
    if any(a >= b for a, b in zip(values, values[1:])):
        raise ValueError('HARD_LASSO_NOT_INCREASING')
    return values


def validate_args(args):
    schedule = parse_schedule(args.sparsity)
    if (args.steps_per_stage < 1 or args.batch < 2 or args.max_train < 4 or
        args.max_dev < 4 or args.lr <= 0 or args.lambda_max < 0 or
        args.eval_batch < 1 or args.ablate_top < 0 or not 0 < args.detector_gate < 1):
        raise ValueError('HARD_LASSO_BAD_PARAMETERS')
    return schedule


def experiment(args):
    schedule = validate_args(args)
    campaign = Path(args.campaign).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    if output == campaign or campaign in output.parents or output in campaign.parents:
        raise RuntimeError('HARD_LASSO_OUTPUT_INSIDE_CAMPAIGN')
    sidecar = campaign/args.hard_state_dir/'latest.pt'
    evidence = campaign/args.hard_state_dir/'evidence'
    if not sidecar.is_file():
        raise FileNotFoundError('HARD_LASSO_NO_SPECIALIST_CHECKPOINT')
    train, dev, evidence_hashes = load_pairs(evidence, args.max_train, args.max_dev, args.seed)
    heldout = ood_cards(args.seed, args.ood_cards, args.ood_min, args.ood_max, train, dev)
    config = {'schema': SCHEMA, 'campaign': str(campaign), 'sidecar_sha256': sha256(sidecar),
              'ladder_output': str(Path(args.ladder_output).expanduser().resolve()),
              'reverse_sha256': sha256(campaign/'hard_emp_reverse'/'stages'/'zero_line'/'accepted.pt'),
              'evidence_sha256': evidence_hashes, 'seed': args.seed, 'schedule': schedule,
              'steps_per_stage': args.steps_per_stage, 'lr': args.lr,
              'lambda_max': args.lambda_max, 'batch': args.batch,
              'max_train': args.max_train, 'max_dev': args.max_dev,
              'ood_cards': args.ood_cards, 'ood_min': args.ood_min,
              'ood_max': args.ood_max, 'detector_gate': args.detector_gate,
              'ablate_top': args.ablate_top, 'train_pairs': len(train), 'dev_pairs': len(dev)}
    config_file = output/'config.json'
    if config_file.is_file():
        if json.loads(config_file.read_text('utf-8')) != config:
            raise RuntimeError('HARD_LASSO_CONFIG_DRIFT')
    elif output.exists() and any(output.iterdir()):
        raise FileExistsError('HARD_LASSO_OUTPUT_NOT_EMPTY')
    else:
        output.mkdir(parents=True, exist_ok=True)
        atomic_json(config_file, config)
    source, detector, original_opt, source_sha, _ = initialize(campaign, args.ladder_output, sidecar, args.device)
    if source_sha != config['sidecar_sha256']:
        raise RuntimeError('HARD_LASSO_SOURCE_DRIFT')
    control = copy.deepcopy(source).to(args.device)
    sparse = copy.deepcopy(source).to(args.device)
    del source
    control_mask = ReadoutMask(control)
    sparse_mask = ReadoutMask(sparse)
    a = torch.optim.AdamW(control.parameters(), lr=args.lr, weight_decay=0.)
    b = torch.optim.AdamW(sparse.parameters(), lr=args.lr, weight_decay=0.)
    a.load_state_dict(copy.deepcopy(original_opt))
    b.load_state_dict(copy.deepcopy(original_opt))
    for opt in (a, b):
        for group in opt.param_groups:
            group['lr'] = args.lr
    rng = random.Random(f'hard-lasso-batches:{args.seed}')
    start = 0
    path = output/'latest.pt'
    if path.is_file():
        state = torch.load(path, map_location='cpu', weights_only=True)
        if state.get('schema') != SCHEMA+'.checkpoint' or state['stage'] >= len(schedule):
            raise RuntimeError('HARD_LASSO_CHECKPOINT_DRIFT')
        control.load_state_dict(state['control'], strict=True)
        sparse.load_state_dict(state['sparse'], strict=True)
        control_mask.mask.copy_(state['mask'].to(args.device))
        sparse_mask.mask.copy_(state['sparse_mask'].to(args.device))
        a.load_state_dict(state['control_opt'])
        b.load_state_dict(state['sparse_opt'])
        rng.setstate(state['rng'])
        torch.set_rng_state(state['torch_rng'])
        if torch.cuda.is_available() and state['cuda_rng'] is not None:
            torch.cuda.set_rng_state_all(state['cuda_rng'])
        start = state['stage'] + 1
    history_file = output/'history.json'
    # The atomic checkpoint is authoritative: recover after a crash between
    # saving weights and updating the human-readable JSON history.
    history = (state['history'] if path.is_file() else [])
    if len(history) != start:
        raise RuntimeError('HARD_LASSO_HISTORY_DRIFT')
    if history_file.is_file() and json.loads(history_file.read_text('utf-8')) != history:
        atomic_json(history_file, history)
    try:
        for stage in range(start, len(schedule)):
            fraction = schedule[stage]
            pruning = sparse_mask.prune_to(fraction)
            penalty = args.lambda_max * (fraction / schedule[-1]) if fraction else 0.
            loss_a = loss_b = None
            for _ in range(args.steps_per_stage):
                sample = [train[rng.randrange(len(train))] for _ in range(args.batch)]
                loss_a = train_step(control, a, control_mask, sample, args.device, 0.)
                loss_b = train_step(sparse, b, sparse_mask, sample, args.device, penalty)
            result_a = score(control, detector, dev, heldout, args.device, args.eval_batch, args.detector_gate)
            result_b = score(sparse, detector, dev, heldout, args.device, args.eval_batch, args.detector_gate)
            interventions = neuron_ablation(sparse, sparse_mask, dev, args.device,
                                            args.eval_batch, args.ablate_top)
            entry = {'stage': stage, 'sparsity_target': fraction, 'regularizer': penalty,
                     'pruning': pruning, 'control_train_ce': loss_a, 'sparse_train_ce': loss_b,
                     'control': result_a, 'sparse': result_b, 'ablations': interventions}
            snapshot = {'schema': SCHEMA+'.checkpoint', 'stage': stage,
                        'control': control.state_dict(), 'sparse': sparse.state_dict(),
                        'mask': control_mask.mask.cpu(), 'sparse_mask': sparse_mask.mask.cpu(),
                        'control_opt': a.state_dict(), 'sparse_opt': b.state_dict(),
                        'history': history + [entry],
                        'rng': rng.getstate(), 'torch_rng': torch.get_rng_state(),
                        'cuda_rng': torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None}
            atomic_checkpoint(path, snapshot)
            history.append(entry)
            atomic_json(history_file, history)
            print('EMP_HARD_LASSO_STAGE='+json.dumps(entry, sort_keys=True), flush=True)
    finally:
        control_mask.close()
        sparse_mask.close()
    report = {'status':'complete', 'stages':len(history), 'output': str(output),
              'control_final': history[-1]['control'], 'sparse_final': history[-1]['sparse'],
              'active_channels':history[-1]['pruning']['active']}
    atomic_json(output/'result.json', report)
    print('EMP_HARD_LASSO_RESULT='+json.dumps(report, sort_keys=True), flush=True)
    return report


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--campaign', required=True, help='Original four-bank outward + reverse run')
    p.add_argument('--ladder-output', required=True, help='Original zero-line certified base model')
    p.add_argument('--hard-state-dir', default='hard_emp_frontier_v2')
    p.add_argument('--output', required=True, help='New independent experiment directory')
    p.add_argument('--device', choices=('cpu','cuda'), default='cuda')
    p.add_argument('--seed', type=int, default=20261026)
    p.add_argument('--sparsity', default='0,0.02,0.05,0.10,0.20,0.35')
    p.add_argument('--steps-per-stage', type=int, default=250)
    p.add_argument('--batch', type=int, default=64)
    p.add_argument('--eval-batch', type=int, default=128)
    p.add_argument('--lr', type=float, default=1e-4)
    p.add_argument('--lambda-max', type=float, default=1e-3)
    p.add_argument('--max-train', type=int, default=8192)
    p.add_argument('--max-dev', type=int, default=1024)
    p.add_argument('--ood-cards', type=int, default=260)
    p.add_argument('--ood-min', type=int, default=65536)
    p.add_argument('--ood-max', type=int, default=131072)
    p.add_argument('--ablate-top', type=int, default=6)
    p.add_argument('--detector-gate', type=float, default=.55)
    return p


if __name__ == '__main__':
    experiment(parser().parse_args())
