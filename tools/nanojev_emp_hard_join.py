#!/usr/bin/env python3
"""Opt-in, one-probe-lag Hard EMP and neural detector for the four-bank ladder.

The original outward ladder and its strict checkpoint SHA chain are unmodified.
Run THIS module instead of nanojev_emp_dual_bank_ladder to attach a specialist
at each committed normal accuracy probe. The original DEV acceptance gate still
uses the original evaluator. An independent TRAIN-family accuracy sample creates
hard/easy pairs without training on DEV or sealed TEST families.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
from pathlib import Path

import torch
from torch import nn
from torch.nn import functional as F

from tools import nanojev_emp_dual_bank as dual
from tools import nanojev_emp_dual_bank_ladder as ladder
from tools import nanojev_zero_line_outward_return as outward
from tools.nanojev_zero_line_zero_only import input_tensor
from tools.nanojev_zero_line_ladder import atomic_checkpoint, atomic_json
from tools.nanojev_zero_line_radius255_probe import sha256

SCHEMA = 'nanojev.emp-hard-join.v1'
MIN_SUPPLIER_ACCURACY = 0.60
DEFAULT_DETECTOR_GATE = 0.50
PRE_GATE_HARD_SHA256 = '4a80f056c36fbfc688d170933253b180785b1785066865f6d46692d3ffae75cb'
PRE_REVERSE_HARD_SHA256 = 'bea3bef3e77771fe68457341df6eeead8c12949e9b3ec4326f6d77479f5aa3f5'
SLOTS = ('hard.left', 'hard.right')


def is_correct(expected, observed):
    """Existing 5% nonzero rule; exact-zero rule, without oracle inputs."""
    expected, observed = int(expected), int(observed)
    return observed == 0 if expected == 0 else abs(observed / expected - 1.0) <= 0.05 + 1e-12


def qualifying_suppliers(predictions, rows, threshold=MIN_SUPPLIER_ACCURACY):
    """Strict >60%, measured on evaluation examples, never training losses."""
    if not (0.0 < threshold < 1.0) or not rows:
        raise ValueError('HARD_JOIN_INVALID_QUALIFICATION')
    if set(predictions) != set(dual.BANKS):
        raise ValueError('HARD_JOIN_MISSING_SUPPLIER')
    accuracies = {}
    for name in dual.BANKS:
        guesses = predictions[name]
        if len(guesses) != len(rows):
            raise ValueError('HARD_JOIN_PREDICTION_ALIGNMENT')
        accuracies[name] = sum(is_correct(r[2], p) for r, p in zip(rows, guesses)) / len(rows)
    return accuracies, tuple(name for name in dual.BANKS if accuracies[name] > threshold)


def assign_anchor(anchor, seed):
    # Stable across stages, separate complete operand-magnitude families.
    digest = hashlib.sha256(f'hard-join-holdout-v1:{seed}:{int(anchor)}'.encode()).digest()
    return 'validation' if int.from_bytes(digest[:8], 'big') % 5 == 0 else 'training'


def pick_sources(qualified, accuracy, standout_margin=0.15):
    """Two independent clones of best one if there is a clear joint standout."""
    if not qualified:
        return None
    order = sorted(qualified, key=lambda name: (-accuracy[name], dual.BANKS.index(name)))
    if len(order) == 1 or accuracy[order[0]] - accuracy[order[1]] >= standout_margin:
        return (order[0],)
    return tuple(order[:2])


def capture_bank(model, x):
    """Collect EXACT final norm state that sign/digit readouts see.

    Captured by a hook rather than a recreated forward implementation, so
    CompactEncoding and SeparateDigitEncoding models share this interface.
    """
    caught = []
    hook = model.norm.register_forward_hook(lambda module, inp, out: caught.append(out.detach()))
    try:
        output = model(x)
    finally:
        hook.remove()
    if not caught or caught[-1].shape[0] != x.shape[0]:
        raise RuntimeError('HARD_JOIN_BACKBONE_OUTPUT_MISSING')
    return dual.decoded(output), caught[-1]


class HardDetector(nn.Module):
    """A single detector consuming the *ordinary heads'* final backbone state."""
    def __init__(self, dimension):
        super().__init__()
        self.net = nn.Sequential(nn.LayerNorm(dimension), nn.Linear(dimension, dimension),
                                 nn.GELU(), nn.Linear(dimension, 1))

    def forward(self, latent):
        return self.net(latent).squeeze(-1)


def accuracy_cards(radius, seed, serial, n):
    """Run a new head-accuracy evaluation in TRAIN magnitude families only.

    These cases are scored before use as specialist training data. We keep
    the ordinary ladder's DEV and TEST fixtures completely untouched.
    """
    rng = random.Random(f'hard-join-accuracy:v1:{seed}:{radius}:{serial}')
    weights = [1.0] * len(outward.BANDS)
    cursor, seen, cards = 0, set(), []
    attempts = 0
    while len(cards) < n and attempts < max(64, n * 12):
        size = min(256, n - len(cards) + 64)
        batch, cursor = outward.generate_batch(rng, radius, seed, size, weights, cursor,
                                                'outward', radius, radius)
        for item in batch:
            row = tuple(map(int, item))
            if (row[0], row[1]) in seen:
                continue
            if row[0] + row[1] != row[2]:
                raise RuntimeError('HARD_JOIN_INVALID_CARD')
            seen.add((row[0], row[1]))
            cards.append(row)
            if len(cards) >= n:
                break
        attempts += 1
    if len(cards) < n:
        raise RuntimeError('HARD_JOIN_INSUFFICIENT_DISTINCT_CARDS')
    return cards


def collect_accuracy(banks, cards, device, eval_batch):
    """One inference per original head; return each head's own latent and answer."""
    preds = {name: [] for name in dual.BANKS}
    latents = {name: [] for name in dual.BANKS}
    for bank in banks.values():
        bank.eval()
    with torch.no_grad():
        for start in range(0, len(cards), eval_batch):
            x = input_tensor(cards[start:start+eval_batch], device)
            for name, bank in banks.items():
                guesses, state = capture_bank(bank, x)
                preds[name].extend(guesses)
                latents[name].append(state.detach().cpu())
    return preds, {name: torch.cat(values, 0) for name, values in latents.items()}


def paired_cases(cards, preds, latents, qualified, seed, radius, step, accuracies=None):
    """Keep hard/easy pairs from SAME qualified supplier and split.

    No handcrafted arithmetic difficulty features or teacher predictions feed
    the detector: only the supplier's ordinary backbone representation.
    """
    result = {'training': [], 'validation': []}
    for name in qualified:
        for part in result:
            bad, good = [], []
            for i, card in enumerate(cards):
                if assign_anchor(card[4], seed) != part:
                    continue
                target, prediction = int(card[2]), int(preds[name][i])
                item = {'card': [int(x) for x in card], 'supplier': name,
                        'source_prediction': prediction, 'radius': radius, 'step': step,
                        'supplier_accuracy': (accuracies or {}).get(name),
                        'latent': latents[name][i].detach().cpu().clone()}
                (good if is_correct(target, prediction) else bad).append(item)
            # Preserve per-supplier hard/easy matching, with approximate
            # magnitude matching. Easy examples may be reused, never relabeled.
            easy_by_size = sorted(good, key=lambda x: (x['card'][4], abs(x['card'][2])))
            for j, hard in enumerate(bad):
                if not easy_by_size:
                    break
                easy = min(easy_by_size, key=lambda x: (abs(x['card'][4] - hard['card'][4]),
                                    abs(abs(x['card'][2]) - abs(hard['card'][2]))))
                result[part].append({'hard': hard, 'easy': easy})
    return result


def empty_metrics():
    return {'total': 0, 'hard': 0, 'easy': 0, 'tp': 0, 'fp': 0, 'tn': 0, 'fn': 0,
            'primary_hard_correct': 0, 'secondary_hard_correct': 0,
            'either_hard_correct_upper_bound': 0, 'detected_and_solved': 0,
            'primary_easy_correct': 0, 'combined_correct': 0}


def evaluate_specialist(detector, specialists, paired, device, batch_size=256, detector_gate=DEFAULT_DETECTOR_GATE):
    """Evaluate real detector -> PRIMARY specialist; no oracle head selection.

    Success by either specialist is a labeled upper bound only, never used as
    inference-time routing or counted as realized combined accuracy.
    """
    tally = empty_metrics()
    if detector is None or not paired:
        return {**tally, 'detector_precision': None, 'detector_recall': None,
                'hard_accuracy': None, 'joint_hard_recovery': None,
                'combined_accuracy': None}
    detector.eval()
    for m in specialists.values():
        m.eval()
    examples = [(label, pair[kind]) for pair in paired
                for label, kind in ((1, 'hard'), (0, 'easy'))]
    with torch.inference_mode():
        for start in range(0, len(examples), batch_size):
            batch = examples[start:start+batch_size]
            latent = torch.stack([entry['latent'] for _, entry in batch]).to(device)
            hits = (torch.sigmoid(detector(latent)) >= detector_gate).cpu().tolist()
            inputs = input_tensor([entry['card'] for _, entry in batch], device)
            outcomes = [dual.decoded(model(inputs)) for model in specialists.values()]
            for i, (label, entry) in enumerate(batch):
                detected = int(hits[i])
                target = int(entry['card'][2])
                first = is_correct(target, outcomes[0][i])
                second = is_correct(target, outcomes[1][i]) if len(outcomes) > 1 else first
                tally['total'] += 1
                tally['hard' if label else 'easy'] += 1
                tally['tp' if label and detected else 'fn' if label else 'fp' if detected else 'tn'] += 1
                # An easy case is correctly handled by its supplier if the
                # detector declines; a hard case is missed by definition.
                tally['combined_correct'] += int(first if detected else not label)
                if label:
                    tally['primary_hard_correct'] += int(first)
                    tally['secondary_hard_correct'] += int(second)
                    tally['either_hard_correct_upper_bound'] += int(first or second)
                    tally['detected_and_solved'] += int(detected and first)
                else:
                    tally['primary_easy_correct'] += int(first)
    return {**tally,
            'detector_precision': tally['tp'] / (tally['tp'] + tally['fp']) if tally['tp'] + tally['fp'] else None,
            'detector_recall': tally['tp'] / tally['hard'] if tally['hard'] else None,
            'hard_accuracy': tally['primary_hard_correct'] / tally['hard'] if tally['hard'] else None,
            'joint_hard_recovery': tally['detected_and_solved'] / tally['hard'] if tally['hard'] else None,
            'specialist_easy_accuracy': tally['primary_easy_correct'] / tally['easy'] if tally['easy'] else None,
            'combined_accuracy': tally['combined_correct'] / tally['total'] if tally['total'] else None,
            'baseline_accuracy': tally['easy'] / tally['total'] if tally['total'] else None,
            'either_hard_accuracy_upper_bound': tally['either_hard_correct_upper_bound'] / tally['hard'] if tally['hard'] else None}


def train_on_previous(specialists, detector, opts, queued, device, batch_size, updates, seed):
    """Only the PREVIOUS probe's pairs can update specialist parameters."""
    if not queued or not updates:
        return {'updates': 0, 'pairs': len(queued)}
    rng = random.Random(f'hard-join-update:{seed}:{len(queued)}:{updates}')
    for model in specialists.values():
        model.train()
    detector.train()
    for _ in range(updates):
        chosen = [queued[rng.randrange(len(queued))] for _ in range(batch_size)]
        # Labels and gold arithmetic are loss targets, never network inputs.
        hard_x = input_tensor([p['hard']['card'] for p in chosen], device)
        y = torch.tensor([p['hard']['card'][2] for p in chosen], device=device, dtype=torch.long)
        for name, model in specialists.items():
            optimizer = opts[name]
            optimizer.zero_grad(set_to_none=True)
            loss = dual.loss_per_card(model(hard_x), y).mean()
            if not torch.isfinite(loss):
                raise FloatingPointError('HARD_JOIN_ARITHMETIC_NONFINITE')
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0, error_if_nonfinite=True)
            optimizer.step()
        latent = torch.stack([p[which]['latent'] for p, which in
                              [(q, 'hard') for q in chosen] + [(q, 'easy') for q in chosen]]).to(device)
        gold = torch.tensor([1.0]*len(chosen) + [0.0]*len(chosen), device=device)
        opts['detector'].zero_grad(set_to_none=True)
        dloss = F.binary_cross_entropy_with_logits(detector(latent), gold)
        if not torch.isfinite(dloss):
            raise FloatingPointError('HARD_JOIN_DETECTOR_NONFINITE')
        dloss.backward()
        nn.utils.clip_grad_norm_(detector.parameters(), 1.0, error_if_nonfinite=True)
        opts['detector'].step()
    return {'updates': updates, 'pairs': len(queued)}


def make_specialist(banks, selected, device, lr, init_seed=20261026):
    specialists = nn.ModuleDict({name.replace('.', '__'): copy.deepcopy(banks[src]).to(device)
                                 for name, src in zip(SLOTS, selected)})
    models = {name: specialists[name.replace('.', '__')] for name in SLOTS[:len(selected)]}
    dimensions = [model.sign_head.in_features for model in models.values()]
    if len(set(dimensions)) != 1:
        raise RuntimeError('HARD_JOIN_UNEQUAL_LATENT_WIDTH')
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(int(init_seed))
        detector = HardDetector(dimensions[0]).to(device)
    optimizers = {name: torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=0.)
                  for name, model in models.items()}
    optimizers['detector'] = torch.optim.AdamW(detector.parameters(), lr=lr, weight_decay=0.)
    return specialists, detector, optimizers


def archive_probe(folder, serial, cards, preds, accuracies, qualified, samples, source_sha):
    """Immutable audit of ALL tested heads and qualified hard/easy pairs.

    The queue is bounded but the underlying per-probe research data is not
    discarded. Validation pairs are archived separately and never queued.
    """
    path = folder / 'evidence' / f'{serial}.pt'
    evidence = {'schema': SCHEMA + '.evidence', 'source_sha256': source_sha,
                'cards': cards, 'predictions': preds,
                'accuracy': accuracies, 'qualified': list(qualified),
                'training_pairs': samples['training'],
                'validation_pairs': samples['validation']}
    if path.exists():
        old = torch.load(path, map_location='cpu', weights_only=True)
        for key in ('schema', 'source_sha256', 'cards', 'predictions', 'accuracy', 'qualified'):
            if old.get(key) != evidence[key]:
                raise RuntimeError('HARD_JOIN_EVIDENCE_REPLAY_CHANGED')
        for category in ('training_pairs', 'validation_pairs'):
            if len(old.get(category, [])) != len(evidence[category]):
                raise RuntimeError('HARD_JOIN_EVIDENCE_REPLAY_CHANGED')
            for recorded, fresh in zip(old[category], evidence[category]):
                for kind in ('hard', 'easy'):
                    x, y = recorded[kind], fresh[kind]
                    if any(x.get(field) != y.get(field) for field in
                           ('card', 'supplier', 'source_prediction', 'radius', 'step', 'supplier_accuracy')):
                        raise RuntimeError('HARD_JOIN_EVIDENCE_REPLAY_CHANGED')
                    if not torch.equal(x['latent'], y['latent']):
                        raise RuntimeError('HARD_JOIN_EVIDENCE_REPLAY_CHANGED')
        return  # A committed, identical evidence archive is safe to reuse.
    atomic_checkpoint(path, evidence)


def selected_gate_values(args):
    policy = {'left_gate': float(getattr(args, 'left_gate', .80)),
              'right_gate': float(getattr(args, 'right_gate', .50)),
              'supplier_gate': float(getattr(args, 'supplier_gate', MIN_SUPPLIER_ACCURACY)),
              'detector_gate': float(getattr(args, 'detector_gate', DEFAULT_DETECTOR_GATE))}
    if any(not 0 < value < 1 for value in policy.values()):
        raise ValueError('HARD_JOIN_INVALID_GATE')
    return policy


def gate_revision(root):
    policy = ladder.gate_tuning(root)
    return policy['revisions'][-1]['revision'] if policy else 0


def _prior_hard_configuration(folder, args, expected):
    path = folder / 'config.json'
    if not path.is_file():
        return None
    old = json.loads(path.read_text('utf-8'))
    for key, value in expected.items():
        if key in ('implementation_sha256', 'qualify', 'detector_gate'):
            continue
        if old.get(key) != value:
            raise RuntimeError('HARD_JOIN_CONFIG_DRIFT')
    if old.get('implementation_sha256') not in (PRE_GATE_HARD_SHA256, PRE_REVERSE_HARD_SHA256, sha256(__file__)):
        raise RuntimeError('HARD_JOIN_UNSUPPORTED_CODE_MIGRATION')
    return old


def _pending_probe(root, folder):
    candidates = []
    for stage in (root / 'stages').glob('outward_*'):
        checkpoint = stage / 'latest.pt'
        if checkpoint.is_file():
            candidates.append((int(stage.name.removeprefix('outward_')), checkpoint))
    if not candidates:
        return False
    radius, checkpoint = max(candidates)
    saved = torch.load(checkpoint, map_location='cpu', weights_only=True)
    hard_checkpoint = folder / 'latest.pt'
    if not hard_checkpoint.is_file():
        return True
    hard_state = torch.load(hard_checkpoint, map_location='cpu', weights_only=True)
    serial = f"{radius}-{saved['step']}"
    record = hard_state.get('processed', {}).get(serial)
    return record is None or record.get('source_sha256') != sha256(checkpoint)


def prepare_gate_tuning(args):
    """Authorize forward-only changes without altering either original config."""
    requested = selected_gate_values(args)
    root = Path(args.output).expanduser().resolve()
    cfg_path = root / 'config.json'
    folder = root / getattr(args, 'hard_state_dir', 'hard_emp')
    if not getattr(args, 'resume', False) or not cfg_path.is_file():
        return
    cfg = json.loads(cfg_path.read_text('utf-8'))
    hard_expected = {'schema': SCHEMA, 'seed': args.seed, 'lr': args.hard_lr,
                     'cards': args.hard_cards, 'batch': args.hard_batch,
                     'updates': args.hard_updates, 'eval_batch': args.eval_batch_size,
                     'max_queue': args.hard_max_queue}
    old_hard = _prior_hard_configuration(folder, args, hard_expected)
    if old_hard is None:
        # A fresh named specialist uses the requested gates and new immutable config.
        if cfg.get('code_sha256') != sha256(ladder.__file__):
            raise RuntimeError('HARD_JOIN_NEW_STATE_REQUIRES_OLD_LADDER_CODE')
        return
    tuning = ladder.gate_tuning(root)
    original = {'left_gate': cfg['left_gate'], 'right_gate': cfg['right_gate'],
                'supplier_gate': old_hard['qualify'],
                'detector_gate': old_hard.get('detector_gate', DEFAULT_DETECTOR_GATE)}
    current = tuning['revisions'][-1] if tuning else {'revision': 0, **original}
    if tuning:
        if (tuning.get('hard_state_dir') != folder.name
                or tuning.get('hard_config_sha256') != sha256(folder / 'config.json')
                or tuning.get('original_hard_code_sha256') != old_hard['implementation_sha256']):
            raise RuntimeError('HARD_JOIN_GATE_TUNING_DRIFT')
    elif cfg.get('code_sha256') not in (ladder.PRE_GATE_LADDER_SHA256, ladder.LEGACY_CODE_SHA256, sha256(ladder.__file__)):
        raise RuntimeError('HARD_JOIN_UNSUPPORTED_CODE_MIGRATION')
    changed = any(requested[k] != current[k] for k in requested)
    if changed and not getattr(args, 'allow_gate_change', False):
        raise RuntimeError('HARD_JOIN_GATE_CHANGE_REQUIRES_AUTHORIZATION')
    if changed and _pending_probe(root, folder):
        raise RuntimeError('HARD_JOIN_PENDING_PROBE_BEFORE_GATE_CHANGE')
    if tuning is None:
        tuning = {'schema': ladder.SCHEMA + '.gate-tuning.v1',
                  'campaign_config_sha256': sha256(cfg_path),
                  'original_ladder_code_sha256': cfg['code_sha256'],
                  'patched_ladder_code_sha256': sha256(ladder.__file__),
                  'hard_state_dir': folder.name,
                  'hard_config_sha256': sha256(folder / 'config.json'),
                  'original_hard_code_sha256': old_hard['implementation_sha256'],
                  'revisions': [{'revision': 0, **original}]}
    if changed:
        # Append an auditable policy revision effective only on subsequent probes.
        tuning['revisions'].append({'revision': current['revision'] + 1, **requested})
    atomic_json(root / ladder.GATE_TUNING_FILE, tuning)
    if changed:
        print('EMP_HARD_GATE_CHANGED=' + json.dumps(tuning['revisions'][-1], sort_keys=True), flush=True)

def run_probe(root, banks, args, radius, step, phase='outward', source_checkpoint=None, training_radius=None):
    """Called only after the ordinary stage checkpoint has been committed."""
    folder = root / getattr(args, 'hard_state_dir', 'hard_emp')
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'latest.pt'
    config_path = folder / 'config.json'
    configuration = {'schema': SCHEMA, 'seed': args.seed, 'qualify': selected_gate_values(args)['supplier_gate'],
                     'lr': args.hard_lr, 'cards': args.hard_cards, 'batch': args.hard_batch,
                     'updates': args.hard_updates, 'eval_batch': args.eval_batch_size,
                     'max_queue': args.hard_max_queue,
                     'detector_gate': selected_gate_values(args)['detector_gate'],
                     'implementation_sha256': sha256(__file__)}
    if config_path.is_file():
        old_config = _prior_hard_configuration(folder, args, configuration)
        if old_config != configuration:
            revision = ladder.gate_tuning(root)
            if (revision is None
                    or revision.get('hard_config_sha256') != sha256(config_path)
                    or revision.get('original_hard_code_sha256') != old_config['implementation_sha256']
                    or any(selected_gate_values(args)[key] != revision['revisions'][-1][key]
                           for key in selected_gate_values(args))):
                raise RuntimeError('HARD_JOIN_CONFIG_DRIFT')
    else:
        if path.exists():
            raise RuntimeError('HARD_JOIN_MISSING_CONFIG')
        atomic_json(config_path, configuration)
    serial = f'{radius}-{step}' if phase == 'outward' else f'reverse-{radius}-{step}'
    prior = torch.load(path, map_location='cpu', weights_only=True) if path.exists() else None
    stage_path = (Path(source_checkpoint) if source_checkpoint is not None else
                  root / 'stages' / f'outward_{radius}' / 'latest.pt')
    source_sha = sha256(stage_path) if stage_path.is_file() else None
    if prior is not None:
        if prior.get('schema') != SCHEMA + '.checkpoint':
            raise RuntimeError('HARD_JOIN_CHECKPOINT_SCHEMA')
        if serial in prior['processed']:
            old = prior['processed'][serial]
            if old.get('source_sha256') != source_sha:
                raise RuntimeError('HARD_JOIN_PROBE_SOURCE_CHANGED')
            return old
    # Seeds separate this train-only evaluation from normal DEV gate fixture.
    if phase == 'reverse':
        from tools import nanojev_emp_hard_reverse as reverse
        cards = reverse.reverse_accuracy_cards(radius, training_radius, args.seed, serial, args.hard_cards)
    else:
        cards = accuracy_cards(radius, args.seed, serial, args.hard_cards)
    preds, latents = collect_accuracy(banks, cards, args.device, args.eval_batch_size)
    accuracies, qualified = qualifying_suppliers(preds, cards, selected_gate_values(args)['supplier_gate'])
    samples = paired_cases(cards, preds, latents, qualified, args.seed, radius, step, accuracies)
    selected = pick_sources(qualified, accuracies)
    if (prior is None or prior['selected'] is None) and selected is None:
        report = {'radius': radius, 'step': step, 'source_sha256': source_sha,
                  'status': 'no_qualified_heads',
                  'accuracy': accuracies, 'qualified': list(qualified),
                  'gate_revision': gate_revision(root), **selected_gate_values(args),
                  'training_pairs': 0,
                  'validation_pairs': 0}
        # Save a marker even without initialized networks; otherwise probes
        # with all heads below threshold would be replayed indefinitely.
        archive_probe(folder, serial, cards, preds, accuracies, qualified, samples, source_sha)
        processed = prior['processed'] if prior is not None else {}
        atomic_checkpoint(path, {'schema': SCHEMA+'.checkpoint', 'processed': {**processed, serial: report},
                                 'selected': None, 'queue': [], 'specialists': None,
                                 'detector': None, 'optimizers': None})
        atomic_json(folder/'latest_report.json', report)
        print('EMP_HARD_JOIN_PROBE=' + json.dumps(report, sort_keys=True), flush=True)
        return report
    if prior is None or prior['selected'] is None:
        if selected is None:
            raise RuntimeError('HARD_JOIN_INITIALIZATION_WITHOUT_SUPPLIER')
        units, detector, opts = make_specialist(banks, selected, args.device, args.hard_lr, args.seed + 41741)
        previous = []
        processed = prior['processed'] if prior else {}
    else:
        selected = tuple(prior['selected'])
        units, detector, opts = make_specialist(banks, selected, args.device, args.hard_lr, args.seed + 41741)
        units.load_state_dict(prior['specialists'], strict=True)
        detector.load_state_dict(prior['detector'], strict=True)
        for name, opt in opts.items():
            opt.load_state_dict(prior['optimizers'][name])
        previous = prior['queue']
        processed = prior['processed']
    specialists = {name: units[name.replace('.', '__')] for name in SLOTS[:len(selected)]}
    training = train_on_previous(specialists, detector, opts, previous, args.device,
                                 args.hard_batch, args.hard_updates, args.seed + radius * 10000 + step)
    # Test on held-out anchor families before placing this probe's training
    # cases into the next-cycle queue. No new validation pair is ever enqueued.
    validation = evaluate_specialist(detector, specialists, samples['validation'], args.device,
                                     detector_gate=selected_gate_values(args)['detector_gate'])
    bounded = samples['training'][:args.hard_max_queue]
    report = {'radius': radius, 'step': step, 'source_sha256': source_sha,
              'gate_revision': gate_revision(root), **selected_gate_values(args),
              'status': 'evaluated' if qualified else 'no_qualified_heads',
              'accuracy': {key: round(value, 6) for key, value in accuracies.items()},
              'qualified': list(qualified), 'source_clones': list(selected),
              'train_on_previous': training, 'training_pairs': len(bounded),
              'validation_pairs': len(samples['validation']), 'validation': validation}
    updated = {'schema': SCHEMA+'.checkpoint', 'processed': {**processed, serial: report},
               'selected': list(selected), 'queue': bounded,
               'specialists': units.state_dict(), 'detector': detector.state_dict(),
               'optimizers': {name: opt.state_dict() for name, opt in opts.items()}}
    archive_probe(folder, serial, cards, preds, accuracies, qualified, samples, source_sha)
    atomic_checkpoint(path, updated)
    atomic_json(folder/'latest_report.json', report)
    print('EMP_HARD_JOIN_PROBE='+json.dumps(report, sort_keys=True), flush=True)
    return report


def recover_pending(args):
    """Attach to the CURRENT ladder frontier, not old completed stages.

    A new specialist has no legitimate prior probe history. Recovery must
    therefore begin at the highest instantiated radius, rather than replaying
    completed stage snapshots out of sequence. Historical stage snapshots are
    not a chronological stream of accuracy probes.

    When recovering an interrupted live probe, only the frontier's most recent
    checkpoint can be incomplete. Both cases are handled with the same path.
    """
    root = Path(args.output).expanduser().resolve()
    stages_dir = root / 'stages'
    candidates = []
    if stages_dir.exists():
        for stage in stages_dir.glob('outward_*'):
            path = stage / 'latest.pt'
            if not path.is_file():
                continue
            try:
                expected_radius = int(stage.name.removeprefix('outward_'))
            except ValueError:
                raise RuntimeError('HARD_JOIN_MALFORMED_STAGE') from None
            candidates.append((expected_radius, path))
    if not candidates:
        return
    # A four-head campaign advances through increasing radius; only its
    # highest instantiated stage may be active or have an unhandled latest
    # checkpoint. Do not feed the specialist stale prior-stage state.
    radius, path = max(candidates, key=lambda item: item[0])
    cp = torch.load(path, map_location='cpu', weights_only=True)
    if cp.get('radius') != radius:
        raise RuntimeError('HARD_JOIN_STAGE_RADIUS_MISMATCH')
    step = cp['step']
    serial = f'{radius}-{step}'
    sidecar = root / getattr(args, 'hard_state_dir', 'hard_emp') / 'latest.pt'
    if sidecar.exists():
        saved = torch.load(sidecar, map_location='cpu', weights_only=True)
        if serial in saved.get('processed', {}):
            if saved['processed'][serial].get('source_sha256') != sha256(path):
                raise RuntimeError('HARD_JOIN_PROBE_SOURCE_CHANGED')
            return
    # Validate the original source and reconstruct exact ordinary heads.
    _, _, _, left, right = ladder.source_config(args)
    parent, _ = ladder.load_certified_model(args.ladder_output, 'cpu')
    banks = ladder.from_source(parent, left, right, cp['banks'], args.device)
    run_probe(root, banks, args, radius, step)

def run(args):
    """Use original SHA-pinned trainer; append specialist after committed probes."""
    if (args.hard_cards < 64 or args.hard_batch < 2 or args.hard_updates < 0 or
        args.hard_max_queue < 1 or args.hard_lr <= 0):
        raise ValueError('HARD_JOIN_INVALID_ARGS')
    state_dir = Path(getattr(args, 'hard_state_dir', 'hard_emp'))
    if (state_dir.is_absolute() or len(state_dir.parts) != 1 or
        state_dir.name in ('', '.', '..') or state_dir.name.startswith('.')):
        raise ValueError('HARD_JOIN_INVALID_STATE_DIR')
    original = ladder.save_stage
    campaign = Path(args.output).expanduser().resolve()
    prepare_gate_tuning(args)
    reverse_marker = campaign / 'hard_emp_reverse' / 'plan.json'
    if reverse_marker.is_file() and not getattr(args, 'reverse', False):
        raise RuntimeError('HARD_JOIN_REVERSE_COMMITTED: resume with --reverse')
    if getattr(args, 'reverse', False):
        from tools import nanojev_emp_hard_reverse as reverse
        if not args.resume:
            raise RuntimeError('HARD_JOIN_REVERSE_REQUIRES_RESUME')
        # Consume a potentially committed forward probe before leaving its frontier.
        if not reverse_marker.is_file():
            recover_pending(args)
        return reverse.run_reverse(args)
    if getattr(args, 'resume', False):
        recover_pending(args)
    def attached(path, banks, opt, controls, events, rng, step, cursor, weights, streak, radius):
        original(path, banks, opt, controls, events, rng, step, cursor, weights, streak, radius)
        # Preserve normal trainer torch RNG even when detector initialization
        # and specialist optimization consume random numbers.
        state = torch.get_rng_state()
        cuda_state = torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None
        try:
            run_probe(campaign, banks, args, radius, step)
        finally:
            torch.set_rng_state(state)
            if cuda_state is not None:
                torch.cuda.set_rng_state_all(cuda_state)
    ladder.save_stage = attached
    try:
        return ladder.run(args)
    finally:
        ladder.save_stage = original


def parser():
    p = ladder.parser()
    p.description = __doc__
    p.add_argument('--supplier-gate', type=float, default=MIN_SUPPLIER_ACCURACY,
                   help='Strict > accuracy threshold for ordinary heads supplying pairs')
    p.add_argument('--detector-gate', type=float, default=DEFAULT_DETECTOR_GATE,
                   help='Minimum detector hard probability before specialist routing')
    p.add_argument('--allow-gate-change', action='store_true',
                   help='Authorize an auditable forward-only threshold revision during --resume')
    p.add_argument('--reverse', action='store_true',
                   help='Persistently abandon outward scheduling; descend with live states to exact zero-line')
    p.add_argument('--hard-state-dir', default='hard_emp',
                   help='Separate specialist state directory, under --output; use a new name to start fresh')
    p.add_argument('--hard-cards', type=int, default=2048)
    p.add_argument('--hard-batch', type=int, default=64)
    p.add_argument('--hard-updates', type=int, default=24)
    p.add_argument('--hard-lr', type=float, default=1e-4)
    p.add_argument('--hard-max-queue', type=int, default=2048)
    return p


if __name__ == '__main__':
    args = parser().parse_args()
    if args.hard_cards < 64 or args.hard_batch < 2 or args.hard_updates < 0 or args.hard_max_queue < 1 or args.hard_lr <= 0:
        raise ValueError('HARD_JOIN_INVALID_ARGS')
    run(args)
