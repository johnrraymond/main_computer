#!/usr/bin/env python3
"""Read-only-source first-failure experiment for NanoJev's cancellation invariant.

Memorize a finite TRAIN-family spiral shell with a *clone* of the trained Hard
EMP. Before training on the next shell, probe its DEV magnitude families,
archive the first incorrect answer and nearby successes, compare the actual
forward activations, and test candidate channels with controlled ablations.
No change is made to the original campaign, detector, or EMP heads.
"""
from __future__ import annotations

import argparse
import copy
import csv
import gzip
import os
import hashlib
import json
import math
import random
from pathlib import Path
from xml.sax.saxutils import escape

import torch
from torch import nn

from tools import nanojev_emp_dual_bank as dual
from tools import nanojev_emp_hard_join as hard
from tools import nanojev_emp_hard_lasso as lasso
from tools.nanojev_zero_line_zero_only import input_tensor, MAX_ABS
from tools.nanojev_zero_line_sampled_radius_ladder import stable_fold
from tools.nanojev_zero_line_ladder import atomic_checkpoint, atomic_json
from tools.nanojev_zero_line_radius255_probe import sha256

SCHEMA = 'nanojev.emp-first-failure-map.v1'


def coordinates(a, b):
    return max(abs(a), abs(b)), a + b


def card(a, b):
    return (int(a), int(b), int(a + b), 0, int(max(abs(a), abs(b))))


def split_card(c, seed):
    return stable_fold(int(c[4]), seed)


def delta_order(radius):
    return [0] + [d for n in range(1, radius + 1) for d in (n, -n)]


def variants(m, delta):
    seen = set()
    for sign in (1, -1):
        a, b = sign * m, -sign * m + delta
        for x, y in ((a, b), (b, a)):
            if (x, y) not in seen and max(abs(x), abs(y)) <= MAX_ABS:
                seen.add((x, y))
                yield card(x, y)


def schedule(args, ring):
    return min(args.max_magnitude, args.initial_magnitude * args.magnitude_growth ** ring), min(
        args.max_delta, args.initial_delta + args.delta_step * ring)


def training_deck(args, ring):
    max_m, max_d = schedule(args, ring)
    rng = random.Random(f'first-failure-train:{args.seed}:{ring}')
    candidates = {}
    attempts = 0
    # Finite memorization corpus, fixed before any training. No DEV/TEST cards.
    while len(candidates) < args.train_cards and attempts < args.train_cards * 1000:
        attempts += 1
        m = rng.randint(1, max_m)
        d = rng.randint(-max_d, max_d)
        rows = list(variants(m, d))
        rng.shuffle(rows)
        for c in rows[:2]:
            if split_card(c, args.seed) == 'train':
                candidates.setdefault(c[:2], c)
                if len(candidates) >= args.train_cards:
                    break
    if len(candidates) < args.train_cards:
        raise RuntimeError(f'FIRST_FAILURE_TRAIN_DECK_EXHAUSTED:{ring}:{len(candidates)}')
    return list(candidates.values())


def probe_anchors(max_m, n):
    # Scale-aware fixed ordering: small-to-large, with landmarks at powers of 2.
    if max_m <= n:
        return list(range(1, max_m + 1))
    values = {1, 2, max_m}
    for i in range(n):
        values.add(max(1, round(2 ** (math.log2(max_m) * i / (n - 1)))))
    for power in range(0, int(math.log2(max_m)) + 1):
        a = 2 ** power
        for v in (a - 1, a, a + 1):
            if 1 <= v <= max_m:
                values.add(v)
    return sorted(values)


def probe_deck(args, ring):
    max_m, max_d = schedule(args, ring)
    old_m, old_d = schedule(args, max(0,ring-1))
    cards = []
    seen = set()
    for m in probe_anchors(max_m, args.probe_anchors):
        for delta in delta_order(max_d):
            for c in variants(m, delta):
                if split_card(c, args.seed) != 'dev' or c[:2] in seen:
                    continue
                seen.add(c[:2])
                cards.append({'card': c, 'm': m, 'delta': delta, 'sign': 1 if c[0] >= 0 else -1,
                              'swap': bool(abs(c[0]) < abs(c[1])),
                              'new_shell': (m > old_m or abs(delta) > old_d)})
    if not cards:
        raise RuntimeError(f'FIRST_FAILURE_EMPTY_DEV_SHELL:{ring}')
    return cards


def exact_ok(expected, predicted):
    return int(expected) == int(predicted)


def is_hit(expected, predicted, mode):
    return exact_ok(expected, predicted) if mode == 'exact' else hard.is_correct(expected, predicted)


def score_cards(model, entries, args):
    model.eval()
    scored = []
    with torch.inference_mode():
        for start in range(0, len(entries), args.eval_batch):
            rows = entries[start:start + args.eval_batch]
            guesses = dual.decoded(model(input_tensor([r['card'] for r in rows], args.device)))
            for entry, prediction in zip(rows, guesses):
                gold = entry['card'][2]
                scored.append({**entry, 'prediction': prediction, 'exact': exact_ok(gold, prediction),
                               'within_5pct': hard.is_correct(gold, prediction),
                               'correct': is_hit(gold, prediction, args.failure_mode)})
    return scored


def deck_metrics(model, deck, args):
    model.eval()
    total_loss = 0.
    guesses = []
    with torch.inference_mode():
        for start in range(0, len(deck), args.eval_batch):
            chunk = deck[start:start + args.eval_batch]
            x = input_tensor(chunk, args.device)
            output = model(x)
            target = torch.tensor([row[2] for row in chunk], dtype=torch.long, device=args.device)
            loss = dual.loss_per_card(output, target)
            total_loss += float(loss.sum().item())
            guesses += dual.decoded(output)
    return {'mean_ce': total_loss / len(deck),
            'exact_accuracy': sum(int(g == c[2]) for c, g in zip(deck, guesses)) / len(deck)}


def memorize(model, opt, deck, args, ring):
    rng = random.Random(f'first-failure-optimizer:{args.seed}:{ring}')
    metrics = deck_metrics(model, deck, args)
    steps = 0
    while (metrics['mean_ce'] > args.loss_target or metrics['exact_accuracy'] != 1.0) and steps < args.max_steps:
        model.train()
        examples = [deck[rng.randrange(len(deck))] for _ in range(args.batch)]
        x = input_tensor(examples, args.device)
        y = torch.tensor([c[2] for c in examples], dtype=torch.long, device=args.device)
        opt.zero_grad(set_to_none=True)
        loss = dual.loss_per_card(model(x), y).mean()
        if not torch.isfinite(loss):
            raise FloatingPointError('FIRST_FAILURE_NONFINITE_LOSS')
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), 1.0, error_if_nonfinite=True)
        opt.step()
        steps += 1
        if steps % args.check_every == 0 or steps == args.max_steps:
            metrics = deck_metrics(model, deck, args)
    return {'steps': steps, 'deck': len(deck), **metrics,
            'memorized': metrics['mean_ce'] <= args.loss_target and metrics['exact_accuracy'] == 1.0}


def activation_modules(model):
    result = []
    # Last token of every Transformer layer is the latent computation slot;
    # record all rounds separately if a backbone layer runs multiple rounds.
    for name, layer in model.named_modules():
        if isinstance(layer, nn.TransformerEncoderLayer):
            result.append((name, layer))
    if not hasattr(model, 'norm'):
        raise RuntimeError('FIRST_FAILURE_NO_FINAL_NORM')
    result.append(('norm', model.norm))
    return result


def select_latent(output):
    if not isinstance(output, torch.Tensor):
        raise RuntimeError('FIRST_FAILURE_UNSUPPORTED_ACTIVATION')
    if output.ndim == 3:
        return output[:, -1, :]
    if output.ndim == 2:
        return output
    raise RuntimeError('FIRST_FAILURE_INVALID_LAYER_SHAPE')


def capture(model, c, device):
    recordings = {}
    counts = {}
    hooks = []
    def make_hook(name):
        def hook(_layer, _inputs, output):
            index = counts.get(name, 0)
            counts[name] = index + 1
            key = f'{name}@{index}'
            recordings[key] = select_latent(output).detach().cpu().flatten().clone()
        return hook
    for name, module in activation_modules(model):
        hooks.append(module.register_forward_hook(make_hook(name)))
    try:
        model.eval()
        with torch.inference_mode():
            prediction = dual.decoded(model(input_tensor([c], device)))[0]
    finally:
        for hook in hooks:
            hook.remove()
    return prediction, recordings


def neighbor_distance(failure, candidate):
    # Scale-normalized proximity; matching operand orientation is preferred.
    return (abs(math.log2(failure['m'] + 1) - math.log2(candidate['m'] + 1))
            + abs(failure['delta'] - candidate['delta']) / max(1, abs(failure['delta']) + 2)
            + .3 * (failure['sign'] != candidate['sign'])
            + .2 * (failure['swap'] != candidate['swap']))


def choose_neighbors(failure, scored, count):
    success = [r for r in scored if r['correct'] and r['card'] != failure['card']]
    failed = [r for r in scored if not r['correct'] and r['card'] != failure['card']]
    success.sort(key=lambda r: (neighbor_distance(failure, r), r['card'][:2]))
    failed.sort(key=lambda r: (neighbor_distance(failure, r), r['card'][:2]))
    return success[:count], failed[:count]


def rank_channels(failed_acts, control_acts):
    if not control_acts:
        return []
    common = sorted(set(failed_acts).intersection(*(set(a) for a in control_acts)))
    ranks = []
    for layer in common:
        values = torch.stack([a[layer].float() for a in control_acts])
        test = failed_acts[layer].float()
        mu = values.mean(0)
        std = values.std(0, unbiased=False)
        raw = (test - mu).abs()
        normed = raw / std.clamp_min(.05)
        for idx in range(test.numel()):
            ranks.append({'layer': layer, 'channel': idx, 'anomaly': float(normed[idx]),
                          'activation_failed': float(test[idx]), 'control_mean': float(mu[idx]),
                          'control_std': float(std[idx]), 'absolute_difference': float(raw[idx])})
    return sorted(ranks, key=lambda x: (-x['anomaly'], x['layer'], x['channel']))


def ablation_prediction(model, c, device, layer, channel):
    layername = layer.split('@')[0]
    modules = dict(activation_modules(model))
    if layername not in modules:
        raise RuntimeError('FIRST_FAILURE_MISSING_INTERVENTION_LAYER')
    index = int(layer.split('@')[-1])
    called = [0]
    def intervene(_module, _inp, output):
        current = called[0]
        called[0] += 1
        if current != index:
            return None
        edited = output.clone()
        if edited.ndim == 3:
            edited[:, -1, channel] = 0.
        else:
            edited[:, channel] = 0.
        return edited
    handle = modules[layername].register_forward_hook(intervene)
    try:
        model.eval()
        with torch.inference_mode():
            prediction = dual.decoded(model(input_tensor([c], device)))[0]
    finally:
        handle.remove()
    if called[0] <= index:
        raise RuntimeError('FIRST_FAILURE_ABLATION_NOT_INVOKED')
    return prediction


def interventions(model, failed, neighbors, ranking, args):
    results = []
    for entry in ranking[:args.ablate_top]:
        layer, channel = entry['layer'], entry['channel']
        after = ablation_prediction(model, failed['card'], args.device, layer, channel)
        losses = 0
        for control in neighbors:
            altered = ablation_prediction(model, control['card'], args.device, layer, channel)
            losses += int(not is_hit(control['card'][2], altered, args.failure_mode))
        results.append({'layer': layer, 'channel': channel,
                        'failed_before': failed['prediction'], 'failed_after': after,
                        'rescued': is_hit(failed['card'][2], after, args.failure_mode),
                        'correct_controls_lost': losses, 'correct_controls_total': len(neighbors),
                        'interpretation': 'single-channel zero intervention; not proof of unique arithmetic neuron'})
    return results


def summarize_cells(scored):
    cells = {}
    for row in scored:
        key = (row['m'], row['delta'])
        entry = cells.setdefault(key, {'m': row['m'], 'delta': row['delta'], 'total': 0,
                                       'wrong': 0, 'exact_wrong': 0, 'within_5pct_wrong': 0})
        entry['total'] += 1
        entry['wrong'] += int(not row['correct'])
        entry['exact_wrong'] += int(not row['exact'])
        entry['within_5pct_wrong'] += int(not row['within_5pct'])
    return list(cells.values())


def write_probe_archive(path, scored):
    temp = path.with_suffix(path.suffix + '.tmp')
    with gzip.open(temp, 'wt', encoding='utf-8') as fp:
        for row in scored:
            fp.write(json.dumps(clean_row(row), sort_keys=True) + '\n')
    os.replace(temp, path)


def activation_heatmap_svg(path, ranking):
    if not ranking:
        path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="550" height="55">'
                        '<rect width="100%" height="100%" fill="#ffffff"/>'
                        '<text x="10" y="30" font-family="sans-serif" font-size="13">'
                        'No nearby correct controls; activation comparison unavailable'
                        '</text></svg>', encoding='utf-8')
        return
    layers = sorted(set(r['layer'] for r in ranking))
    width = max(r['channel'] for r in ranking) + 1
    scores = {(r['layer'],r['channel']):r['anomaly'] for r in ranking}
    cell = 11
    lines = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{230+width*cell}" height="{80+len(layers)*19}">',
             '<rect width="100%" height="100%" fill="#ffffff"/>',
             '<text x="10" y="16" font-family="sans-serif" font-size="12">Neuron activation anomaly (failure vs nearby correct)</text>']
    for y, layer in enumerate(layers):
        lines.append(f'<text x="220" y="{39+y*19}" text-anchor="end" font-family="monospace" font-size="10">{escape(layer)}</text>')
        for i in range(width):
            z = scores.get((layer,i),0.)
            strength = min(1., math.log1p(z)/math.log1p(10))
            r = int(240-55*strength)
            g = int(243-195*strength)
            b = int(247-210*strength)
            lines.append(f'<rect x="{225+i*cell}" y="{29+y*19}" width="10" height="15" fill="#{r:02x}{g:02x}{b:02x}"/>')
    lines.append('</svg>')
    path.write_text('\n'.join(lines),encoding='utf-8')


def heatmap_svg(path, cells, first):
    magnitudes = sorted(set(c['m'] for c in cells))
    deltas = sorted(set(c['delta'] for c in cells), reverse=True)
    if not magnitudes or not deltas:
        return
    xidx = {x: i for i, x in enumerate(magnitudes)}
    yidx = {y: i for i, y in enumerate(deltas)}
    w, h = 14, 14
    lines = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{max(500,100+len(magnitudes)*w)}" height="{max(130,70+len(deltas)*h)}">',
             '<rect width="100%" height="100%" fill="#fafafa"/>',
             '<text x="10" y="16" font-family="sans-serif" font-size="13">Fraction wrong: magnitude vs signed residual</text>']
    for c in cells:
        proportion = c['wrong'] / c['total']
        red = int(70 + 170 * proportion)
        green = int(170 - 125 * proportion)
        fill = f'#{red:02x}{green:02x}78'
        x, y = 65 + xidx[c['m']]*w, 25 + yidx[c['delta']]*h
        lines.append(f'<rect x="{x}" y="{y}" width="13" height="13" fill="{fill}"/>')
    if first is not None:
        x, y = 65 + xidx[first['m']]*w, 25 + yidx[first['delta']]*h
        lines.append(f'<rect x="{x-.5}" y="{y-.5}" width="14" height="14" fill="none" stroke="#1b1b1b" stroke-width="2"/>')
    for i, delta in enumerate(deltas):
        if i % max(1, math.ceil(len(deltas) / 16)) == 0:
            lines.append(f'<text x="57" y="{36+i*h}" font-family="monospace" font-size="9" text-anchor="end">{delta}</text>')
    for i, m in enumerate(magnitudes):
        if i % max(1, math.ceil(len(magnitudes) / 15)) == 0:
            lines.append(f'<text x="{65+i*w}" y="{45+len(deltas)*h}" font-family="monospace" font-size="8" transform="rotate(45 {65+i*w} {45+len(deltas)*h})">{m}</text>')
    lines.append('</svg>')
    path.write_text('\n'.join(lines), encoding='utf-8')


def save_event(folder, ring, failure, scored, model, args):
    successes, failures = choose_neighbors(failure, scored, args.neighbors)
    prediction, failed_acts = capture(model, failure['card'], args.device)
    if prediction != failure['prediction']:
        raise RuntimeError('FIRST_FAILURE_REPLAY_CHANGED')
    controls = []
    for row in successes:
        observed, acts = capture(model, row['card'], args.device)
        if observed != row['prediction']:
            raise RuntimeError('FIRST_FAILURE_CONTROL_CHANGED')
        controls.append(acts)
    failure_neighbors = []
    for row in failures:
        _, acts = capture(model, row['card'], args.device)
        failure_neighbors.append(acts)
    ranking = rank_channels(failed_acts, controls)
    interventions_result = interventions(model, failure, successes, ranking, args)
    local = folder / f'ring_{ring:02d}'
    local.mkdir(parents=True, exist_ok=True)
    cells = summarize_cells(scored)
    heatmap_svg(local/'error_heatmap.svg', cells, failure)
    activation_heatmap_svg(local/'neuron_heatmap.svg', ranking)
    atomic_json(local/'error_grid.json', {'schema': SCHEMA+'.grid', 'cells': cells,
                'first_failure_card': list(failure['card']), 'failure_mode': args.failure_mode})
    # Full activation vector for *each* comparison, not just the ranked subset.
    atomic_checkpoint(local/'activations.pt', {'schema': SCHEMA+'.activations',
                      'failure': failed_acts, 'controls': controls,
                      'nearby_failures': failure_neighbors,
                      'failure_card': list(failure['card']),
                      'control_cards': [list(row['card']) for row in successes]})
    report = {'schema': SCHEMA+'.event', 'ring': ring, 'first_failure': clean_row(failure),
              'attribution_valid': bool(successes),
              'neighboring_successes': [clean_row(r) for r in successes],
              'neighboring_failures': [clean_row(r) for r in failures],
              'top_activation_differences': ranking[:args.activation_top],
              'all_channel_ranks': len(ranking), 'interventions': interventions_result,
              'grid': str(local/'error_grid.json'), 'heatmap': str(local/'error_heatmap.svg'),
              'neuron_heatmap': str(local/'neuron_heatmap.svg'),
              'activations': str(local/'activations.pt')}
    atomic_json(local/'event.json', report)
    return report


def clean_row(row):
    return {k: list(v) if k == 'card' else v for k, v in row.items()}


def validated_config(args, digest, reverse_digest):
    return {'schema': SCHEMA, 'source_hard_sha256': digest, 'source_reverse_sha256': reverse_digest,
            'parameters': {name: getattr(args, name) for name in (
                'seed','initial_magnitude','initial_delta','magnitude_growth','delta_step',
                'max_delta','max_magnitude','rings','train_cards','probe_anchors','loss_target',
                'max_steps','check_every','batch','eval_batch','lr','neighbors','activation_top',
                'ablate_top','failure_mode')}}


def run(args):
    check_args(args)
    root = Path(args.campaign).expanduser().resolve()
    out = Path(args.output).expanduser().resolve()
    sidecar = root/args.hard_state_dir/'latest.pt'
    model, _detector, state_opt, digest, reverse_digest = lasso.initialize(root, args.ladder_output, sidecar, args.device)
    model.train().requires_grad_(True)
    config = validated_config(args, digest, reverse_digest)
    if (out/'config.json').exists():
        if json.loads((out/'config.json').read_text(encoding='utf-8')) != config:
            raise RuntimeError('FIRST_FAILURE_CONFIG_DRIFT')
    else:
        out.mkdir(parents=True, exist_ok=True)
        atomic_json(out/'config.json', config)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.)
    optimizer.load_state_dict(state_opt)
    for group in optimizer.param_groups:
        group['lr'] = args.lr
    history = []
    if (out/'history.json').is_file():
        history = json.loads((out/'history.json').read_text(encoding='utf-8'))
    checkpoint = out/'latest.pt'
    if checkpoint.exists():
        last = torch.load(checkpoint, map_location='cpu', weights_only=True)
        if last['schema'] != SCHEMA+'.checkpoint' or last['config'] != config:
            raise RuntimeError('FIRST_FAILURE_CHECKPOINT_DRIFT')
        model.load_state_dict(last['model'])
        optimizer.load_state_dict(last['optimizer'])
        for group in optimizer.param_groups:
            group['lr'] = args.lr
        if len(history) != last['next_ring']:
            archived = last.get('history')
            if (archived is None or len(archived) != last['next_ring'] or
                    history != archived[:len(history)]):
                raise RuntimeError('FIRST_FAILURE_INCONSISTENT_HISTORY')
            history = archived
            atomic_json(out/'history.json', history)
    start = len(history)
    for ring in range(start, args.rings):
        deck = training_deck(args, ring)
        result = memorize(model, optimizer, deck, args, ring)
        entry = {'ring': ring, 'train_coordinates': schedule(args, ring), 'memorization': result}
        if not result['memorized']:
            entry['status'] = 'not_memorized'
            # No checkpoint is committed for an unmastered ring. A resumed
            # run restarts that ring; it cannot accidentally skip the gate.
            atomic_json(out/'result.json', {'schema':SCHEMA, 'status':'not_memorized', 'ring':ring,
                        'loss':result['mean_ce'], 'exact_accuracy':result['exact_accuracy']})
            # Unlike a solved ring, do not mark it complete in latest.pt.
            print('EMP_FIRST_FAILURE_MEMORIZATION_LIMIT='+json.dumps(entry,sort_keys=True),flush=True)
            return entry
        target_ring = ring + 1
        shell = probe_deck(args, target_ring)
        scored = score_cards(model, shell, args)
        probe_folder = out/f'ring_{target_ring:02d}'
        probe_folder.mkdir(parents=True, exist_ok=True)
        # This is the *event-time* state: after memorizing ring N, before
        # fitting ring N+1. A final latest.pt cannot recreate this state.
        # Per-ring snapshots make neighborhood replay independently auditable.
        snapshot_path = probe_folder/'probe_snapshot.pt'
        # Always atomically replace a snapshot for an uncommitted ring:
        # after an interrupted run the same ring may have been retrained.
        # Completed rings are skipped by the historical resume logic.
        atomic_checkpoint(snapshot_path, {
            'schema': SCHEMA+'.probe-snapshot.v1',
            'ring': target_ring,
            'train_coordinates': schedule(args, ring),
            'probe_coordinates': schedule(args, target_ring),
            'model': model.state_dict(),
            'optimizer': optimizer.state_dict(),
            'config': config,
        })
        entry['probe_snapshot'] = str(snapshot_path)
        write_probe_archive(probe_folder/'all_predictions.jsonl.gz', scored)
        first = next((r for r in scored if r['new_shell'] and not r['correct']), None)
        entry['probe_coordinates'] = schedule(args, target_ring)
        entry['probe_cases'] = len(scored)
        entry['probe_archive'] = str(probe_folder/'all_predictions.jsonl.gz')
        entry['probe_exact_accuracy'] = sum(r['exact'] for r in scored)/len(scored)
        entry['probe_tolerant_accuracy'] = sum(r['within_5pct'] for r in scored)/len(scored)
        entry['status'] = 'first_failure' if first else 'all_correct'
        if first:
            # Archive layer snapshots and controls BEFORE any training on this shell.
            event = save_event(out,target_ring,first,scored,model,args)
            entry['event'] = str(out/f'ring_{target_ring:02d}'/'event.json')
            entry['first_failure'] = event['first_failure']
        else:
            stage = out/f'ring_{target_ring:02d}'
            stage.mkdir(parents=True, exist_ok=True)
            cells = summarize_cells(scored)
            atomic_json(stage/'error_grid.json', {'schema':SCHEMA+'.grid','cells':cells,'failure_mode':args.failure_mode})
            heatmap_svg(stage/'error_heatmap.svg',cells,None)
        history.append(entry)
        # Persist checkpoint before proceeding to the next training ring.
        atomic_checkpoint(checkpoint, {'schema':SCHEMA+'.checkpoint','config':config,
                          'next_ring':ring+1,'history':history,
                          'model':model.state_dict(),'optimizer':optimizer.state_dict()})
        atomic_json(out/'history.json', history)
        print('EMP_FIRST_FAILURE_RING='+json.dumps({k:v for k,v in entry.items() if k not in ('first_failure',)},sort_keys=True),flush=True)
    conclusion = {'schema':SCHEMA,'status':'complete','rings':len(history),
                  'failure_events':sum(row['status']=='first_failure' for row in history),
                  'history':str(out/'history.json'),'checkpoint':str(checkpoint)}
    atomic_json(out/'result.json', conclusion)
    print('EMP_FIRST_FAILURE_RESULT='+json.dumps(conclusion,sort_keys=True),flush=True)
    return conclusion


def check_args(a):
    if (a.initial_magnitude < 2 or a.initial_delta < 0 or a.magnitude_growth < 2 or
        a.delta_step < 0 or a.max_delta < a.initial_delta or a.max_magnitude < a.initial_magnitude or
        a.max_magnitude > MAX_ABS // 2 or a.rings < 1 or a.train_cards < 12 or
        a.probe_anchors < 3 or a.max_steps < 1 or a.check_every < 1 or a.batch < 2 or
        a.eval_batch < 1 or not 0 < a.loss_target < 1 or a.lr <= 0 or
        a.neighbors < 1 or a.activation_top < 1 or a.ablate_top < 0 or
        a.failure_mode not in ('exact','tolerance')):
        raise ValueError('FIRST_FAILURE_INVALID_ARGUMENTS')


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--campaign',required=True)
    p.add_argument('--ladder-output',required=True)
    p.add_argument('--hard-state-dir',default='hard_emp_frontier_v2')
    p.add_argument('--output',required=True)
    p.add_argument('--device',default='cuda' if torch.cuda.is_available() else 'cpu')
    p.add_argument('--seed',type=int,default=20261025)
    p.add_argument('--initial-magnitude',type=int,default=32)
    p.add_argument('--initial-delta',type=int,default=2)
    p.add_argument('--magnitude-growth',type=int,default=2)
    p.add_argument('--delta-step',type=int,default=2)
    p.add_argument('--max-delta',type=int,default=32)
    p.add_argument('--max-magnitude',type=int,default=131072)
    p.add_argument('--rings',type=int,default=9)
    p.add_argument('--train-cards',type=int,default=128)
    p.add_argument('--probe-anchors',type=int,default=24)
    p.add_argument('--loss-target',type=float,default=.005)
    p.add_argument('--max-steps',type=int,default=1200)
    p.add_argument('--check-every',type=int,default=50)
    p.add_argument('--batch',type=int,default=64)
    p.add_argument('--eval-batch',type=int,default=256)
    p.add_argument('--lr',type=float,default=.0001)
    p.add_argument('--neighbors',type=int,default=8)
    p.add_argument('--activation-top',type=int,default=32)
    p.add_argument('--ablate-top',type=int,default=8)
    p.add_argument('--failure-mode',choices=('exact','tolerance'),default='exact')
    return p


if __name__ == '__main__':
    run(parser().parse_args())
