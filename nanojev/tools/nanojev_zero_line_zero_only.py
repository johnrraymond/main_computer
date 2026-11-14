#!/usr/bin/env python3
"""Train the *backbone* to recognize exact cancellation across +/-2,000,000.

Gold arithmetic is used only in constructing training/evaluation cards and in
legacy rehearsal; never as a model input or an inference-time executor.  This
phase teaches 'zero' versus 'not-zero', NOT the value of a nonzero residual.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
from pathlib import Path

import torch
from torch import nn
from torch.nn import functional as F

from tools.nanojev_addition_curriculum import target_digits
from tools.nanojev_zero_line_ladder import atomic_checkpoint, atomic_json, deck, sample_deck, evaluate as legacy_evaluate
from tools.nanojev_zero_line_radius255_probe import load_certified_model, sha256
from tools.nanojev_zero_line_scale_invariance import BANDS, MAX_ABS

SCHEMA = 'nanojev.zero-line-zero-only.v1'
NONZERO_DELTAS = (-5, -2, -1, 1, 2, 5)
SPLITS = ('train', 'dev', 'test')


def fold(m: int, seed: int = 20261025) -> str:
    """Family = maximum absolute operand magnitude; deterministic and disjoint."""
    if not 0 <= m <= MAX_ABS:
        raise ValueError('ZERO_ONLY_INVALID_MAGNITUDE')
    if m == MAX_ABS:
        return 'test'  # a permanently withheld extreme magnitude
    # Preserve the original certified small-number domain for rehearsal;
    # it is NOT presented as unseen validation regardless of hash partition.
    h = hashlib.sha256(f'zero-only:{seed}:{m}'.encode()).digest()[0] % 10
    return 'train' if h < 6 else 'dev' if h < 8 else 'test'


def magnitude_band(m):
    for idx, (lo, hi) in enumerate(BANDS):
        if lo <= m <= hi:
            return idx
    raise ValueError('ZERO_ONLY_OUTSIDE_BANDS')


def make_pair(m, orientation, delta):
    """No target or metadata becomes a network input."""
    a = orientation * m
    b = -a + delta
    if abs(a) > MAX_ABS or abs(b) > MAX_ABS:
        return None
    return a, b


def draw_card(rng, band_index, wanted_split, seed, zero=None):
    lo, hi = BANDS[band_index]
    for _ in range(4000):
        m = rng.randint(lo, hi)
        orientation = -1 if rng.randrange(2) else 1
        iszero = (rng.randrange(2) == 0) if zero is None else zero
        delta = 0 if iszero else NONZERO_DELTAS[rng.randrange(len(NONZERO_DELTAS))]
        pair = make_pair(m, orientation, delta)
        if pair is None:
            continue
        if rng.randrange(2):
            pair = pair[::-1]
        actual_m = max(abs(pair[0]), abs(pair[1]))
        if actual_m < lo or actual_m > hi:
            continue
        if wanted_split != 'train' or actual_m > 127:
            if fold(actual_m, seed) != wanted_split:
                continue
        return (pair[0], pair[1], iszero, band_index, actual_m)
    raise RuntimeError(f'ZERO_ONLY_SPLIT_EXHAUSTED band={band_index} split={wanted_split}')


def anchors_for_split(lo, hi, wanted_split, count, seed):
    """No large-range enumeration or allocation; anchors fixed independently of model."""
    rng = random.Random(f'{seed}:{lo}:{hi}:{wanted_split}:anchors')
    proposed = [lo, hi, lo + 1, hi - 1]
    for p in (10, 100, 1000, 10000, 100000, 1000000, MAX_ABS):
        proposed.extend([p - 1, p, p + 1])
    seen = set(); out = []
    for m in proposed:
        if lo <= m <= hi and fold(m, seed) == wanted_split and m not in seen:
            seen.add(m); out.append(m)
    attempts = 0
    while len(out) < count and attempts < count * 400:
        attempts += 1
        m = rng.randint(lo, hi)
        if m not in seen and fold(m, seed) == wanted_split:
            seen.add(m); out.append(m)
    return sorted(out[:count])


def build_probe(wanted_split, seed, anchors_per_band=24, deltas=NONZERO_DELTAS):
    if wanted_split not in SPLITS or anchors_per_band < 1:
        raise ValueError('ZERO_ONLY_INVALID_PROBE')
    cards = []
    for idx, (lo, hi) in enumerate(BANDS):
        for m in anchors_for_split(lo, hi, wanted_split, anchors_per_band, seed):
            # Pair reversal and both operand signs are mandatory for zeros.
            for direction in (-1, 1):
                for delta in (0, *deltas):
                    pair = make_pair(m, direction, delta)
                    if pair is None:
                        continue
                    for a, b in (pair, pair[::-1]):
                        actual = max(abs(a), abs(b))
                        if not (lo <= actual <= hi):
                            continue
                        if fold(actual, seed) != wanted_split:
                            continue
                        cards.append((a, b, delta == 0, idx, actual))
    # No duplicate cards; a permutation cannot provide independent evidence.
    return list({(a, b): (a, b, z, band, m) for a, b, z, band, m in cards}.values())


def train_cards(rng, batch, seed, weights=None):
    """Balanced zeros/near-misses; train-only magnitude families."""
    weights = weights or [1.] * len(BANDS)
    result = []
    for i in range(batch):
        k = rng.choices(range(len(BANDS)), weights=weights, k=1)[0]
        result.append(draw_card(rng, k, 'train', seed, zero=i % 2 == 0))
    rng.shuffle(result)
    return result


def input_tensor(cards, device):
    return torch.tensor([(a, b) for a, b, *_ in cards], dtype=torch.long, device=device)


def zero_logits_loss(out, iszero):
    """No nonzero numerical target or signed residual classification in phase 1."""
    signs = out['sign']
    digits = out['digits']
    yes = iszero.bool()
    terms = []
    if bool(yes.any()):
        sign_ce = F.cross_entropy(signs[yes], torch.ones(int(yes.sum()), dtype=torch.long, device=signs.device))
        digit_ce = F.cross_entropy(digits[yes].transpose(1, 2),
                                   torch.zeros((int(yes.sum()), 7), dtype=torch.long, device=signs.device))
        terms.append(sign_ce + digit_ce)
    if bool((~yes).any()):
        sn = signs[~yes]
        # Zero sign versus either nonzero sign; no + versus - supervision.
        wrong_zero_margin = sn[:, 1] - torch.logsumexp(sn[:, [0, 2]], dim=-1)
        sign_loss = F.softplus(wrong_zero_margin).mean()
        # Stable multi-instance nonzero-digit objective. A hard-zero output
        # (all zero logits dominant) must *still* receive a gradient; an
        # expm1/log probability-of-any approach can saturate at p(all-zero)=1.
        d = digits[~yes]
        nonzero_vs_zero = torch.logsumexp(d[..., 1:], -1) - d[..., 0]
        any_digit_log_odds = torch.logsumexp(nonzero_vs_zero, -1)
        digit_loss = F.softplus(-any_digit_log_odds).mean()
        terms.append(sign_loss + digit_loss)
    return sum(terms)


@torch.inference_mode()
def classify(model, cards, device, batch_size=256):
    """Only the two operands are supplied to the model. No x+y in inference."""
    model.eval()
    counts = {(i, z): [0, 0] for i in range(len(BANDS)) for z in (True, False)}
    examples = []
    for start in range(0, len(cards), batch_size):
        subset = cards[start:start + batch_size]
        outputs = model(input_tensor(subset, device))
        signs = outputs['sign'].argmax(-1) - 1
        digits = outputs['digits'].argmax(-1)
        canonical = (signs == 0) & (digits == 0).all(-1)
        rejected = (signs != 0) & (digits != 0).any(-1)
        for (a, b, true_zero, band, m), canonical_zero, nonzero in zip(
                subset, canonical.tolist(), rejected.tolist()):
            passed = bool(canonical_zero if true_zero else nonzero)
            ctr = counts[(band, true_zero)]
            ctr[0] += int(passed)
            ctr[1] += 1
            if not passed and len(examples) < 100:
                examples.append({'a': a, 'b': b, 'is_zero': true_zero,
                                 'band': f'{BANDS[band][0]}-{BANDS[band][1]}',
                                 'magnitude': m, 'predicted_canonical_zero': bool(canonical_zero),
                                 'predicted_nonzero': bool(nonzero)})
    matrix = {}
    for i, (lo, hi) in enumerate(BANDS):
        zcorrect, ztotal = counts[(i, True)]
        ncorrect, ntotal = counts[(i, False)]
        matrix[f'{lo}-{hi}'] = {
            'zero_correct': zcorrect, 'zero_total': ztotal,
            'zero_rate': zcorrect / ztotal if ztotal else None,
            'nonzero_correct': ncorrect, 'nonzero_total': ntotal,
            'nonzero_rate': ncorrect / ntotal if ntotal else None}
    populated = [v for v in matrix.values() if v['zero_total'] and v['nonzero_total']]
    min_zero = min((v['zero_rate'] for v in populated), default=0.)
    min_nonzero = min((v['nonzero_rate'] for v in populated), default=0.)
    return {'cards': len(cards), 'by_band': matrix, 'min_zero': min_zero,
            'min_nonzero_rejection': min_nonzero, 'passes': bool(populated) and
            len(populated) == len(BANDS) and min_zero == 1. and min_nonzero == 1.,
            'failures': examples}


def legacy_loss(model, teacher, x):
    y = x.sum(-1)  # supervised rehearsal ONLY; never inference
    out = model(x)
    ce = F.cross_entropy(out['sign'], y.sign() + 1) + F.cross_entropy(
        out['digits'].transpose(1, 2), target_digits(y))
    with torch.no_grad():
        target = teacher(x)
    # Preserve previously certified distributions as well as correct labels.
    kd_sign = F.kl_div(F.log_softmax(out['sign'], -1),
                       F.softmax(target['sign'], -1), reduction='batchmean')
    kd_digit = F.kl_div(F.log_softmax(out['digits'], -1),
                        F.softmax(target['digits'], -1), reduction='none').sum(-1).mean()
    return ce + .5 * (kd_sign + kd_digit)


def train_step(model, teacher, optimizer, rng, args, weights):
    cards = train_cards(rng, args.batch_size, args.seed, weights)
    a = input_tensor(cards, args.device)
    iszero = torch.tensor([z for _, _, z, _, _ in cards], dtype=torch.bool, device=args.device)
    # Rehearsal is deliberately drawn over the *whole* certified deck.
    legacy = torch.randint(-127, 128, (args.legacy_batch_size, 2), device='cpu').to(args.device)
    model.train()
    near = zero_logits_loss(model(a), iszero)
    old = legacy_loss(model, teacher, legacy)
    total = near + args.legacy_weight * old
    if not bool(torch.isfinite(total)):
        raise FloatingPointError('ZERO_ONLY_NONFINITE_LOSS')
    optimizer.zero_grad(set_to_none=True)
    total.backward()
    nn.utils.clip_grad_norm_((p for p in model.parameters() if p.requires_grad), 1., error_if_nonfinite=True)
    optimizer.step()
    return float(total.detach()), float(near.detach()), float(old.detach())


def configuration(args, provenance):
    from tools import nanojev_addition_curriculum, nanojev_zero_line_ladder
    keys = ('seed', 'batch_size', 'legacy_batch_size', 'legacy_weight',
            'lr', 'probe_every', 'eval_batch_size', 'legacy_probe_cards', 'anchors_per_band',
            'train_probe_anchors', 'stability_probes', 'cpu_threads')
    return {'schema': SCHEMA, 'source_sha256': sha256(__file__),
            'backbone_code_sha256': sha256(nanojev_addition_curriculum.__file__),
            'ladder_code_sha256': sha256(nanojev_zero_line_ladder.__file__),
            'certified_source': provenance,
            'settings': {k: getattr(args, k) for k in keys},
            'scope': 'true backbone finetune; exact canonical zero vs nonzero; zero-only phase',
            'holdout_policy': 'test never used in training, dev selection or mining'}


def checkpoint(model, optimizer, rng, step, streak, best, weights):
    return {'schema': SCHEMA + '.checkpoint', 'step': step,
            'model': model.state_dict(), 'optimizer': optimizer.state_dict(),
            'python_rng': rng.getstate(), 'torch_rng': torch.get_rng_state(),
            'cuda_rng': torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None,
            'streak': streak, 'best': best, 'weights': weights}


def restore(st, model, optimizer, rng):
    if st.get('schema') != SCHEMA + '.checkpoint':
        raise RuntimeError('ZERO_ONLY_BAD_RESUME_CHECKPOINT')
    model.load_state_dict(st['model'], strict=True)
    optimizer.load_state_dict(st['optimizer'])
    rng.setstate(st['python_rng']); torch.set_rng_state(st['torch_rng'])
    if st['cuda_rng'] is not None and torch.cuda.is_available():
        torch.cuda.set_rng_state_all(st['cuda_rng'])
    return st['step'], st['streak'], st['best'], st['weights']


def check_args(args):
    if (args.max_steps < 1 or args.probe_every < 1 or args.batch_size < 2 or
            args.batch_size % 2 or args.legacy_batch_size < 1 or
            args.eval_batch_size < 1 or args.legacy_probe_cards < 1 or args.anchors_per_band < 4 or
            args.train_probe_anchors < 1 or args.stability_probes < 2 or
            args.cpu_threads < 1 or not (0 < args.lr < 1) or
            not (0 < args.legacy_weight < 100)):
        raise ValueError('ZERO_ONLY_INVALID_ARGUMENTS')
    if args.phase != 'plan' and args.device == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('ZERO_ONLY_CUDA_UNAVAILABLE')


def plan(args):
    counts = {s: {f'{lo}-{hi}': len(anchors_for_split(lo, hi, s, args.anchors_per_band, args.seed))
                  for lo, hi in BANDS} for s in ('dev', 'test')}
    return {'schema': SCHEMA, 'max_magnitude': MAX_ABS, 'zero_targets': [0],
            'nonzero_controls': list(NONZERO_DELTAS), 'bands': [list(v) for v in BANDS],
            'withheld_anchor_counts': counts, 'historical_full_deck': 65025,
            'required_dev_streak': args.stability_probes, 'promotion': '100% on all populated cells + 65,025 historical exact + sealed test pass'}


def run(args):
    check_args(args)
    if args.phase == 'plan':
        result = plan(args)
        print('ZERO_ONLY_PLAN=' + json.dumps(result, sort_keys=True))
        return result
    torch.set_num_threads(args.cpu_threads)
    root = Path(args.output).resolve()
    teacher, origin = load_certified_model(args.ladder_output, args.device)
    for p in teacher.parameters(): p.requires_grad_(False)
    teacher.eval()
    config = configuration(args, origin)
    conf_path = root / 'config.json'
    if args.resume:
        if not conf_path.is_file() or json.loads(conf_path.read_text('utf-8')) != config:
            raise RuntimeError('ZERO_ONLY_CONFIG_OR_CHECKPOINT_DRIFT')
    else:
        if root.exists() and any(root.iterdir()):
            raise FileExistsError('ZERO_ONLY_OUTPUT_OVERWRITE_REFUSED')
        root.mkdir(parents=True, exist_ok=True)
        atomic_json(conf_path, config)
    model, _ = load_certified_model(args.ladder_output, args.device)
    # Genuine recurrent-backbone finetuning; frozen inputs/decoders protect the
    # coordinate system of original arithmetic while the backbone adapts.
    for name, param in model.named_parameters():
        param.requires_grad_(name.startswith('backbone.') or name.startswith('norm.')
                             or name in ('query', 'pos'))
    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad],
                                  lr=args.lr, weight_decay=0.)
    rng = random.Random(args.seed + 479)
    torch.manual_seed(args.seed + 181)
    step, streak, best = 0, 0, -1.
    weights = [1.] * len(BANDS)
    latest = root / 'latest.pt'
    if args.resume:
        if (root / 'result.json').is_file() and 'holdout_once' in json.loads((root / 'result.json').read_text('utf-8')):
            raise RuntimeError('ZERO_ONLY_SEALED_TEST_ALREADY_OPENED_START_NEW_SCIENTIFIC_CAMPAIGN')
        if not latest.is_file(): raise FileNotFoundError('ZERO_ONLY_RESUME_MISSING')
        st = torch.load(latest, map_location='cpu', weights_only=True)
        step, streak, best, weights = restore(st, model, optimizer, rng)
        if (root / 'promoted.pt').exists():
            raise RuntimeError('ZERO_ONLY_ALREADY_PROMOTED')
    dev = build_probe('dev', args.seed, args.anchors_per_band)
    test = build_probe('test', args.seed, args.anchors_per_band)
    if not dev or not test: raise RuntimeError('ZERO_ONLY_EMPTY_DEV_OR_TEST')
    train_probe = build_probe('train', args.seed, args.train_probe_anchors)
    legacy_rows = deck(127)
    legacy_probe = sample_deck(legacy_rows, args.legacy_probe_cards, args.seed + 121)
    baseline_path = root / 'source_baseline.json'
    if args.resume:
        if not baseline_path.is_file():
            raise RuntimeError('ZERO_ONLY_BASELINE_MISSING_ON_RESUME')
        baseline = json.loads(baseline_path.read_text('utf-8'))
    else:
        baseline = classify(teacher, dev, args.device, args.eval_batch_size)
        atomic_json(baseline_path, baseline)
        print('ZERO_ONLY_BASELINE=' + json.dumps({'dev_min_zero': baseline['min_zero'],
              'dev_min_nonzero_rejection': baseline['min_nonzero_rejection'],
              'cards': baseline['cards']}, sort_keys=True), flush=True)
    report = {'status': 'not_certified', 'source': origin, 'last_step': step,
              'baseline_dev': {'min_zero': baseline['min_zero'],
                               'min_nonzero_rejection': baseline['min_nonzero_rejection']}}
    for step in range(step + 1, args.max_steps + 1):
        loss, zl, legacy = train_step(model, teacher, optimizer, rng, args, weights)
        if step % args.probe_every and step != args.max_steps: continue
        train_diag = classify(model, train_probe, args.device, args.eval_batch_size)
        # Difficulty mining is strictly TRAIN-only, not DEV or held-out TEST.
        weights = [1. + 4.*(2. - train_diag['by_band'][f'{lo}-{hi}']['zero_rate'] -
                             train_diag['by_band'][f'{lo}-{hi}']['nonzero_rate'])
                   if train_diag['by_band'][f'{lo}-{hi}']['zero_rate'] is not None and
                   train_diag['by_band'][f'{lo}-{hi}']['nonzero_rate'] is not None else 1.
                   for lo, hi in BANDS]
        dev_result = classify(model, dev, args.device, args.eval_batch_size)
        history_probe = legacy_evaluate(model, legacy_probe, args.device, args.eval_batch_size)
        metric = min(dev_result['min_zero'], dev_result['min_nonzero_rejection'])
        if metric > best:
            best = metric
            atomic_checkpoint(root / 'best_dev.pt',
                              {'schema': SCHEMA + '.best', 'model': model.state_dict(),
                               'step': step, 'metric': metric, 'source_sha256': origin['checkpoint_sha256']})
        streak = streak + 1 if dev_result['passes'] else 0
        report.update({'last_step': step, 'loss': loss, 'zero_loss': zl, 'legacy_loss': legacy,
                       'best_dev_min': best, 'dev': dev_result, 'train_probe': train_diag,
                       'historical_probe': history_probe,
                       'streak': streak, 'target_streak': args.stability_probes,
                       'status': 'training'})
        atomic_checkpoint(latest, checkpoint(model, optimizer, rng, step, streak, best, weights))
        atomic_json(root / 'result.json', report)
        print('ZERO_ONLY_PROBE=' + json.dumps({'step': step, 'loss': round(loss,5),
              'dev_min_zero': dev_result['min_zero'],
              'dev_min_reject': dev_result['min_nonzero_rejection'],
              'worst_train_band': min(range(len(weights)),key=lambda i: -weights[i]),
              'streak': streak, 'target': args.stability_probes,
              'historical_probe_exact': history_probe['groups']['all']['accuracy']}, sort_keys=True), flush=True)
        if streak < args.stability_probes: continue
        legacy_full = legacy_evaluate(model, legacy_rows, args.device, args.eval_batch_size)
        historical_pass = (legacy_full['groups']['all']['passed'] == 65025 and
                           legacy_full['false_zero'] == 0)
        report['historical_full'] = legacy_full
        if not historical_pass:
            streak = 0
            report['status'] = 'retention_refused'
            report['reason'] = 'original_65025_card_regression'
            atomic_checkpoint(latest, checkpoint(model, optimizer, rng, step, streak, best, weights))
            atomic_json(root / 'result.json', report)
            print('ZERO_ONLY_RETENTION_REFUSED=' + json.dumps({'step':step,
                  'exact':legacy_full['groups']['all']['passed'],'required':65025}),flush=True)
            continue
        # Test is sealed: open it just once AFTER DEV streak and full history;
        # if it fails, STOP, never use it to select another checkpoint.
        holdout = classify(model, test, args.device, args.eval_batch_size)
        report['holdout_once'] = holdout
        report['status'] = 'promoted' if holdout['passes'] else 'heldout_refused'
        report['reason'] = None if holdout['passes'] else 'unseen_magnitude_instability'
        if holdout['passes']:
            atomic_checkpoint(root / 'promoted.pt', {'schema': SCHEMA + '.promoted',
                              'model': model.state_dict(), 'step': step,
                              'source_checkpoint_sha256': origin['checkpoint_sha256'],
                              'config_sha256': sha256(conf_path), 'historical_exact': 65025,
                              'dev': dev_result, 'heldout': holdout})
        atomic_json(root / 'result.json', report)
        print('ZERO_ONLY_CERTIFICATION=' + json.dumps({'status':report['status'],
              'step':step, 'legacy':65025, 'holdout_min_zero':holdout['min_zero'],
              'holdout_min_reject':holdout['min_nonzero_rejection']},sort_keys=True))
        return report
    report['status'] = 'budget_exhausted_no_promotion'
    atomic_json(root / 'result.json', report)
    print('ZERO_ONLY_SUMMARY=' + json.dumps({'status': report['status'],
          'step': args.max_steps, 'best_dev_min': best, 'promoted':False}, sort_keys=True))
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--phase', choices=('plan','train'), default='train')
    p.add_argument('--ladder-output', default='')
    p.add_argument('--output', default='')
    p.add_argument('--device', choices=('cpu','cuda'), default='cuda')
    p.add_argument('--max-steps',type=int,default=30000)
    p.add_argument('--probe-every',type=int,default=250)
    p.add_argument('--batch-size',type=int,default=128)
    p.add_argument('--legacy-batch-size',type=int,default=128)
    p.add_argument('--legacy-weight',type=float,default=2.)
    p.add_argument('--lr',type=float,default=0.00005)
    p.add_argument('--eval-batch-size',type=int,default=256)
    p.add_argument('--legacy-probe-cards',type=int,default=2048)
    p.add_argument('--anchors-per-band',type=int,default=32)
    p.add_argument('--train-probe-anchors',type=int,default=12)
    p.add_argument('--stability-probes',type=int,default=3)
    p.add_argument('--cpu-threads',type=int,default=4)
    p.add_argument('--seed',type=int,default=20261025)
    p.add_argument('--resume',action='store_true')
    return p


def main(argv=None):
    return run(parser().parse_args(argv))

if __name__=='__main__': main()
