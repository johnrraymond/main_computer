#!/usr/bin/env python3
"""Isolated aggressive signed-result frontier and paired LoRA error repair.

This is a Qwen q/v LoRA experiment; terminal numeric means textual PROBLEM
packets, NOT a HybridLink numerical head. Repairs update shared LoRA weights;
'locus' refers to a local *arithmetic input neighborhood*, not sparse weights.
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import random
import time
from pathlib import Path

from tools import nanojev_qwen_lm_radius_ladder as ladder

SCHEMA = 'nanojev.qwen.lora-frontier-stress-repair.v1'


def _emit(event, info):
    print(event + '=' + json.dumps(info, sort_keys=True), flush=True)


def _sha(path):
    d = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            d.update(block)
    return d.hexdigest()


def _out(args):
    p = Path(args.frontier_output) if args.frontier_output else Path(args.output)
    return p if args.frontier_output else p.with_name(p.name + '_aggressive_frontier_v1')


def _key(row):
    return ladder._hash([row['source'], row['target']])


def _append(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf8') as f:
        f.write(json.dumps(obj, sort_keys=True) + '\n')
        f.flush()
        os.fsync(f.fileno())


def _radii(args):
    start = ladder.parse_radii(args.radii, args.operand_limit)[-1]
    end = args.frontier_max_radius or min(args.operand_limit, max(32, start * 16))
    if end <= start or end > args.operand_limit:
        raise ValueError('FRONTIER_MAX_RADIUS_MUST_EXPAND_WITHIN_OPERAND_LIMIT')
    vals = [start]
    while vals[-1] < end:
        vals.append(min(end, vals[-1] * 2 if vals[-1] else 1))
    return vals


def _policy(args):
    if args.terminal_mode != 'numeric':
        raise ValueError('FRONTIER_REQUIRES_NUMERIC_TERMINAL')
    if (args.frontier_neighbor_probe_cap < 1 or args.frontier_repair_center_cap < 1 or args.frontier_steps < 1 or args.frontier_check_every < 1 or
        args.frontier_eval_cases < 2 or args.frontier_train_cases < args.batch_size or
        args.frontier_harvest_cases < 1 or args.frontier_repair_steps < 1 or
        args.local_radius < 1 or args.batch_size < 1 or args.max_rung < 0 or
        args.max_rung > 8):
        raise ValueError('FRONTIER_BAD_EXPERIMENT_ARGUMENTS')
    return {'schema': SCHEMA, 'radii': _radii(args), 'steps': args.frontier_steps,
            'check_every': args.frontier_check_every,
            'eval_cases': args.frontier_eval_cases,
            'train_cases': args.frontier_train_cases,
            'harvest_cases': args.frontier_harvest_cases, 'repair_center_cap': args.frontier_repair_center_cap,
            'neighbor_probe_cap': args.frontier_neighbor_probe_cap,
            'repair_steps': args.frontier_repair_steps,
            'local_radius': args.local_radius, 'operand_limit': args.operand_limit,
            'max_rung': args.max_rung, 'seed': args.seed, 'lr': args.lr,
            'batch_size': args.batch_size, 'max_seq': args.max_seq,
            'max_tokens': args.max_tokens, 'max_new_tokens': args.max_new_tokens,
            'terminal_mode': args.terminal_mode,
            'source': str(Path(args.output).resolve())}


def _deck(radius, lower, count, args, label):
    """Sample signed-result shell with predominantly large-magnitude operands.

    Keeps operand/partial sum bound independent of signed-result radius. No
    exhaustive T00 expansion; all generated cases carry exact target packets.
    """
    if not 0 <= lower <= radius <= args.operand_limit:
        raise ValueError('FRONTIER_BAD_SHELL')
    rng = random.Random(int(ladder._hash([label, radius, lower, args.seed])[:16], 16))
    choices = ladder.bands(args.operand_limit)
    choices = choices[-2:] if len(choices) > 1 else choices
    result, seen = [], set()
    for i in range(count * 30):
        if len(result) >= count:
            break
        rung = i % (args.max_rung + 1)
        d = rng.randint(lower, radius) * (-1 if rng.randrange(2) else 1)
        lo, hi, band = choices[rng.randrange(len(choices))]
        nums = ladder._bounded_numbers(rung, d, lo, hi, args.operand_limit,
                                       args.seed + int(ladder._hash(label)[:8], 16),
                                       i, i % 13)
        if nums is None:
            continue
        row = ladder._case(rung, nums, f'{label}-R{radius}-T{rung}-I{i}', 'numeric')
        row['radius'] = radius
        row['band'] = band
        k = _key(row)
        if k not in seen:
            seen.add(k)
            result.append(row)
    if len(result) != count:
        raise RuntimeError('FRONTIER_UNDERSAMPLED_SHELL:' + label)
    return result


def _evaluation_deck(args, radii):
    # Disjoint examples for scoring; never used in either repair curriculum.
    per = max(2, math.ceil(args.frontier_eval_cases / len(radii)))
    rows = []
    for idx, radius in enumerate(radii):
        lower = 0 if idx == 0 else radii[idx - 1] + 1
        rows += _deck(radius, lower, per, args, f'EVAL-{idx}')
    return rows


def _score(bb, tok, rows, args, *, greedy=None, progress=None):
    greedy = greedy or ladder._greedy
    results = {}
    for at in range(0, len(rows), args.eval_batch):
        chunk = rows[at:at + args.eval_batch]
        answer = greedy(bb, tok, [r['source'] for r in chunk],
                                args.device, args.max_tokens, args.max_new_tokens)
        for row, emitted in zip(chunk, answer):
            ok, parsed = ladder.correctness(row['source'], row['target'], emitted)
            results[_key(row)] = {'correct': bool(ok), 'emitted': emitted,
                                  'parsed': parsed}
        if progress is not None:
            progress(min(at + len(chunk), len(rows)), len(rows))
    return results


def _transitions(before, after, rows):
    gained = lost = retained = 0
    by_radius = {}
    by_rung = {}
    for r in rows:
        k = _key(r)
        a = before[k]['correct']; b = after[k]['correct']
        if not a and b: gained += 1
        if a and not b: lost += 1
        if a and b: retained += 1
        bucket = by_radius.setdefault(str(r['radius']), {'gained': 0, 'lost': 0, 'total': 0})
        bucket['total'] += 1
        bucket['gained'] += int(not a and b)
        bucket['lost'] += int(a and not b)
        rung = by_rung.setdefault(f'T{r["rung"]:02d}', {
            'tested': 0, 'correct_before': 0, 'correct_now': 0,
            'gained': 0, 'lost': 0, 'net': 0})
        rung['tested'] += 1
        rung['correct_before'] += int(a)
        rung['correct_now'] += int(b)
        rung['gained'] += int(not a and b)
        rung['lost'] += int(a and not b)
        rung['net'] = rung['gained'] - rung['lost']
    return {'tested': len(rows), 'gained': gained, 'lost': lost,
            'net': gained - lost, 'retained_correct': retained,
            'by_radius': by_radius, 'by_rung': by_rung}


def _chain_deck(evaluation, max_rung):
    """Fixed evaluation-only complete chains, anchored at maximum rung.

    The original evaluation deck is stable across checks and excludes training
    and harvested examples. Its top-rung members are full-chain probes, not
    new samples introduced later in training.
    """
    return [r for r in evaluation if r['rung'] == max_rung]


def _expression_operands(packet):
    """Validate generated arithmetic as a bounded, safe addition-only AST.

    This parser is *not* fed gold answers and does not correct Qwen's output.
    It is only a guard on which emitted strings may be sent back to Qwen.
    """
    if not packet.startswith('PROBLEM: '):
        return None
    expression = packet[len('PROBLEM: '):]
    if len(expression) > 512:
        return None
    try:
        tree = ast.parse(expression, mode='eval').body
    except (SyntaxError, ValueError, RecursionError):
        return None
    def walk(node):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = walk(node.left); right = walk(node.right)
            return left + right
        if isinstance(node, ast.Constant) and type(node.value) is int:
            return 1
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            if isinstance(node.operand, ast.Constant) and type(node.operand.value) is int:
                return 1
        raise ValueError('NON_INTEGER_ADDITION_EXPRESSION')
    try:
        return walk(tree)
    except (ValueError, RecursionError):
        return None


def _chain_score(bb, tok, rows, args, *, greedy=None, progress=None):
    """Unassisted, batched greedy rollouts from T(max_rung) to numeric terminal.

    Every next prompt is the MODEL'S emitted packet, never a teacher target.
    Track strict full-chain accuracy separately from final-integer accuracy;
    the first failing rung identifies the bottleneck. Stop malformed packets,
    non-progressing reductions, and any attempt beyond the exact hop budget.
    """
    greedy = greedy or ladder._greedy
    records = {}
    active = {}
    for row in rows:
        key = _key(row)
        active[key] = {'row': row, 'prompt': row['source'],
                       'remaining': row['rung'] + 1}
        records[key] = {'correct': False, 'terminal_correct': False,
                        'terminal_value': None, 'strict_chain': True,
                        'first_wrong_rung': None, 'failure_reason': None,
                        'completed_hops': 0, 'trace': []}
    # Each valid numeric reduction removes one addition operation.
    max_hops = max((r['rung'] + 1 for r in rows), default=0)
    for hop_index in range(max_hops):
        if not active:
            break
        group = list(active.items())
        for at in range(0, len(group), args.eval_batch):
            batch = group[at:at + args.eval_batch]
            emissions = greedy(bb, tok,
                [item['prompt'] for _, item in batch],
                args.device, args.max_tokens, args.max_new_tokens)
            if len(emissions) != len(batch):
                raise RuntimeError('CHAIN_GREEDY_OUTPUT_COUNT_MISMATCH')
            for (key, item), emission in zip(batch, emissions):
                row = item['row']; result = records[key]
                rung = item['remaining'] - 1
                hop = row['rung'] - rung
                expected = row['complete_chain'][hop + 1]
                got = ladder.parse_packet(emission)
                exact = got == expected
                result['trace'].append({'rung': f'T{rung:02d}',
                     'input': item['prompt'], 'expected': expected,
                     'emitted': emission, 'exact': exact})
                result['completed_hops'] += 1
                if not exact:
                    result['strict_chain'] = False
                    if result['first_wrong_rung'] is None:
                        result['first_wrong_rung'] = f'T{rung:02d}'
                terminal = ladder.numeric_terminal(got)
                if terminal is not None:
                    result['terminal_value'] = terminal
                    result['terminal_correct'] = terminal == row['residual']
                    # An early, mathematically correct terminal is not the
                    # demanded T08→...→T00 reduction trajectory.
                    result['correct'] = bool(result['strict_chain'] and
                        item['remaining'] == 1 and result['terminal_correct'])
                    result['failure_reason'] = (None if result['correct'] else
                        ('early_terminal' if item['remaining'] != 1 else
                         'wrong_terminal_or_intermediate'))
                    active.pop(key, None)
                    continue
                operand_count = _expression_operands(got) if got is not None else None
                if operand_count != item['remaining']:
                    result['failure_reason'] = ('invalid_or_nonprogressing_packet' if
                        operand_count is None or operand_count >= item['remaining'] + 1
                        else 'incorrect_reduction_depth')
                    active.pop(key, None)
                    continue
                # Expected after one reduction: remaining operands become
                # remaining-1, and a terminal is required at remaining=1.
                if item['remaining'] <= 1:
                    result['failure_reason'] = 'missing_numeric_terminal'
                    active.pop(key, None)
                    continue
                item['remaining'] -= 1
                item['prompt'] = got
        if progress is not None:
            progress(hop_index + 1, max_hops, len(active))
    for key in list(active):
        records[key]['failure_reason'] = 'chain_budget_exhausted'
    return records


def _chain_metrics(before, after, rows):
    gain = loss = term_gain = term_loss = 0
    strict = terminal = 0
    loci = {}
    by_radius = {}
    for row in rows:
        key = _key(row)
        old, new = before[key], after[key]
        a, b = bool(old['correct']), bool(new['correct'])
        ta, tb = bool(old['terminal_correct']), bool(new['terminal_correct'])
        gain += int(not a and b); loss += int(a and not b)
        term_gain += int(not ta and tb); term_loss += int(ta and not tb)
        strict += int(b); terminal += int(tb)
        if not b:
            locus = new['first_wrong_rung'] or new['failure_reason'] or 'unknown'
            loci[locus] = loci.get(locus, 0) + 1
        bucket = by_radius.setdefault(str(row['radius']),
            {'tested': 0, 'strict_correct': 0, 'terminal_correct': 0,
             'gained': 0, 'lost': 0, 'net': 0})
        bucket['tested'] += 1
        bucket['strict_correct'] += int(b)
        bucket['terminal_correct'] += int(tb)
        bucket['gained'] += int(not a and b)
        bucket['lost'] += int(a and not b)
        bucket['net'] = bucket['gained'] - bucket['lost']
    return {'tested': len(rows), 'strict_correct': strict,
            'terminal_correct': terminal, 'gained': gain, 'lost': loss,
            'net': gain - loss, 'terminal_gained': term_gain,
            'terminal_lost': term_loss, 'terminal_net': term_gain - term_loss,
            'first_failure_by_rung': loci, 'by_radius': by_radius}


def _record_chain(out, event, step, rows, score):
    for row in rows:
        key = _key(row)
        _append(out / 'chain_observations.jsonl', {
            'event': event, 'step': step, 'key': key,
            'radius': row['radius'], 'rung': row['rung'],
            'source': row['source'], 'target_final': row['residual'],
            **score[key]})


def _initialize_chain_baseline(state, bb, tok, zero, source, evaluation, args, out):
    """Backfill *initial source* baseline on an in-progress legacy run.

    Temporarily restore the parent LoRA, score the same fixed full-chain
    problems, then restore current working LoRA exactly. Nothing is trained.
    """
    deck = _chain_deck(evaluation, args.max_rung)
    deck_sha = ladder.deck_fingerprint(deck)
    if 'chain_baseline' in state:
        if state['chain_eval_sha256'] != deck_sha:
            raise RuntimeError('FRONTIER_CHAIN_DECK_DRIFT')
        return deck
    if not deck:
        raise RuntimeError('FRONTIER_NO_MAX_RUNG_EVALUATION_CASES')
    current = zero.lora_state(bb)
    try:
        zero.restore_lora(bb, source['lora'])
        bb.eval()
        baseline = _chain_score(bb, tok, deck, args)
    finally:
        zero.restore_lora(bb, current)
    bb.eval()
    state['chain_eval_sha256'] = deck_sha
    state['chain_baseline'] = baseline
    state['chain_last_score'] = baseline
    state['chain_last_step'] = 0
    ladder._atomic_json(out / 'chain_evaluation_deck.json', deck)
    _record_chain(out, 'baseline', 0, deck, baseline)
    _emit('QWEN_FRONTIER_CHAIN_BASELINE', {
        'source_step': 0, 'current_phase_step': state['phase_step'],
        'rung': f'T{args.max_rung:02d}', 'cases': len(deck),
        'metrics': _chain_metrics(baseline, baseline, deck),
        'backfilled_from_original_source_checkpoint': state['phase_step'] > 0})
    return deck


def _check_chain(state, bb, tok, deck, args, out):
    now = _chain_score(bb, tok, deck, args)
    step = state['phase_step']
    _record_chain(out, 'evaluation', step, deck, now)
    versus_start = _chain_metrics(state['chain_baseline'], now, deck)
    versus_prev = _chain_metrics(state['chain_last_score'], now, deck)
    state['chain_last_score'] = now
    state['chain_last_step'] = step
    state['chain_last_metrics'] = versus_start
    _emit('QWEN_FRONTIER_CHAIN_CHECK', {'step': step,
        'start_rung': f'T{args.max_rung:02d}',
        'versus_start': versus_start,
        'since_last_chain_check': versus_prev})


def _hard(rows, score):
    return [r for r in rows if not score[_key(r)]['correct']]


def _save(path, state, bb, zero, optimizer):
    import torch
    ladder._atomic_torch(path, {'schema': SCHEMA, 'state': state,
                               'lora': zero.lora_state(bb),
                               'optimizer': optimizer.state_dict()})
    ladder._atomic_json(path.with_suffix('.json'), state)


def _load(path, bb, zero, policy, source_hash):
    import torch
    obj = torch.load(path, map_location='cpu', weights_only=True)
    state = obj['state']
    if (obj.get('schema') != SCHEMA or state['policy'] != policy or
        state['source_sha256'] != source_hash):
        raise RuntimeError('FRONTIER_RESUME_POLICY_OR_SEED_DRIFT')
    zero.restore_lora(bb, obj['lora'])
    return state, obj['optimizer']


def _load_source(args):
    import torch
    source = Path(args.output).resolve() / 'working.pt'
    if not source.is_file():
        raise RuntimeError('FRONTIER_SOURCE_REQUIRES_EXISTING_LADDER_WORKING_PT')
    source_sha = _sha(source)
    ck = torch.load(source, map_location='cpu', weights_only=True)
    if ck.get('schema') != ladder.SCHEMA:
        raise RuntimeError('FRONTIER_SOURCE_NOT_LADDER_CHECKPOINT')
    cfg = ck['state']['config']
    if (cfg.get('terminal_mode') != 'numeric' or
        cfg.get('stage1_checkpoint') != str(Path(args.stage1_checkpoint).resolve()) or
        cfg.get('text_checkpoint') != str(Path(args.text_checkpoint).resolve())):
        raise RuntimeError('FRONTIER_SEED_CONTRACT_MISMATCH')
    return ck, source_sha


def _load_model(args, ck):
    from tools import nanojev_qwen_zero_line_backbone as zero
    bb, tok, sources, frozen, dirs, _ = ladder._fresh_parent(args)
    if (sources != ck['state']['source_sha256'] or frozen != ck['state']['frozen_sha256']):
        raise RuntimeError('FRONTIER_BACKBONE_LINEAGE_DRIFT')
    zero.restore_lora(bb, ck['lora'])
    ps = [p for _, p in zero.lora_named_parameters(bb)]
    if not ps:
        raise RuntimeError('FRONTIER_TRAINABLE_LORA_NOT_FOUND')
    for p in ps: p.requires_grad_(True)
    return bb, tok, zero, ps


def _optimizer(ps, args, prior=None):
    import torch
    opt = torch.optim.AdamW(ps, lr=args.lr, weight_decay=0)
    if prior is not None:
        opt.load_state_dict(prior)
    return opt


def _training_batch(rows, count, rng):
    return [rng.choice(rows) for _ in range(count)]


def _record_observation(out, event, step, rows, scored):
    # Exact case identifiers and gold text are durable, not just aggregate counts.
    for row in rows:
        k = _key(row)
        _append(out / 'observations.jsonl', {'event': event, 'step': step, 'key': k,
                'rung': row['rung'], 'radius': row['radius'], 'band': row['band'],
                'correct': scored[k]['correct'], 'emitted': scored[k]['emitted']})


def run_frontier(args):
    import torch
    policy = _policy(args)
    source, digest = _load_source(args)
    out = _out(args)
    ladder._outputs_safe(out, [Path(args.output), Path(args.stage1_checkpoint).parent,
                               Path(args.text_checkpoint).parent])
    out.mkdir(parents=True, exist_ok=True)
    checkpoint = out / 'working.pt'
    if not checkpoint.exists() and any(out.iterdir()):
        raise RuntimeError('FRONTIER_REFUSES_UNRECOGNIZED_OUTPUT')
    bb, tok, zero, ps = _load_model(args, source)
    evaluation = _evaluation_deck(args, policy['radii'])
    eval_hash = ladder.deck_fingerprint(evaluation)
    if checkpoint.exists():
        state, moments = _load(checkpoint, bb, zero, policy, digest)
        if state['eval_sha256'] != eval_hash:
            raise RuntimeError('FRONTIER_EVAL_DECK_DRIFT')
    else:
        baseline = _score(bb, tok, evaluation, args)
        state = {'policy': policy, 'source_sha256': digest, 'eval_sha256': eval_hash,
                 'phase_step': 0, 'status': 'TRAINING', 'baseline': baseline,
                 'last_score': baseline, 'last_check_step': 0,
                 'training_seconds': 0., 'best_net': 0, 'best_step': 0}
        ladder._atomic_json(out / 'evaluation_deck.json', evaluation)
        _record_observation(out, 'baseline', 0, evaluation, baseline)
        _emit('QWEN_FRONTIER_START', {'source_sha256': digest, 'radii': policy['radii'],
                                     'eval_cases': len(evaluation), 'source_output': args.output,
                                     'frontier_output': str(out)})
    chain_deck = _initialize_chain_baseline(
        state, bb, tok, zero, source, evaluation, args, out)
    # On a legacy in-progress frontier run, immediately measure the current
    # step as well, so first post-patch output doesn't pretend training starts
    # at the resume step or discard completed learning.
    if state['phase_step'] > 0 and state.get('chain_last_step', 0) == 0:
        _check_chain(state, bb, tok, chain_deck, args, out)
    opt = _optimizer(ps, args, source.get('optimizer') if not checkpoint.exists() else moments)
    _save(checkpoint, state, bb, zero, opt)
    # Keep a valid best checkpoint even if aggressive training never gains one
    # net evaluation competency (best_step=0 then represents the parent).
    if not (out / 'best.pt').exists() and state['phase_step'] == 0:
        _save(out / 'best.pt', state, bb, zero, opt)
    if state['status'] == 'STRESSED':
        _emit('QWEN_FRONTIER_DONE', {'already_complete': True, 'step': state['phase_step']})
        return state
    radii = policy['radii']
    pending = []
    previous_stage = -1
    for i in range(state['phase_step'], args.frontier_steps):
        stage = min(len(radii) - 1, i * len(radii) // args.frontier_steps)
        radius = radii[stage]
        if stage != previous_stage or not pending:
            low = 0 if stage == 0 else radii[stage - 1] + 1
            pending = _deck(radius, low, args.frontier_train_cases, args,
                            f'TRAIN-{stage}-C{i // args.frontier_check_every}')
            previous_stage = stage
        rng = random.Random(args.seed ^ (i * 104729))
        batch = _training_batch(pending, args.batch_size, rng)
        bb.train()
        start = time.perf_counter()
        loss = ladder._train_step(bb, tok, opt, batch, args)
        if args.device == 'cuda':torch.cuda.synchronize()
        state['training_seconds'] += time.perf_counter() - start
        bb.eval()
        state['phase_step'] = i + 1
        if ((i + 1) % args.frontier_check_every == 0 or i + 1 == args.frontier_steps
                or (i + 1) * len(radii) // args.frontier_steps != stage):
            # Harvest errors from a separate, deterministic, *non-evaluation*
            # shell. Both failed and corrected observations remain in history.
            harvest = _deck(radius, 0 if stage == 0 else radii[stage - 1] + 1,
                            args.frontier_harvest_cases, args,
                            f'HARVEST-{stage}-S{i+1}')
            found = _score(bb, tok, harvest, args)
            _record_observation(out, 'harvest', i + 1, harvest, found)
            for row in _hard(harvest, found):
                _append(out / 'discovered_errors.jsonl', {'step': i + 1,
                        'key': _key(row), 'case': row, 'emitted': found[_key(row)]['emitted']})
            now = _score(bb, tok, evaluation, args)
            _record_observation(out, 'evaluation', i + 1, evaluation, now)
            transitions = _transitions(state['baseline'], now, evaluation)
            prev = _transitions(state['last_score'], now, evaluation)
            state['last_score'] = now
            state['last_check_step'] = i + 1
            improved = transitions['net'] > state['best_net']
            if improved:
                state['best_net'] = transitions['net'];state['best_step'] = i + 1
            _emit('QWEN_FRONTIER_CHECK', {'step': i + 1, 'radius': radius,
                    'last_loss': round(loss, 8), 'versus_start': transitions,
                    'since_last_check': prev, 'harvest_errors': len(_hard(harvest, found)),
                    'training_seconds': round(state['training_seconds'], 3)})
            _check_chain(state, bb, tok, chain_deck, args, out)
            if improved:
                _save(out / 'best.pt', state, bb, zero, opt)
        # A report at a radius boundary must describe a durable checkpoint.
        # The pre-existing trainer emitted boundary checks without saving the
        # checkpoint; persist it here before any new diagnostic inference.
        at_check = (((i + 1) % args.frontier_check_every == 0) or
                    (i + 1 == args.frontier_steps) or
                    ((i + 1) * len(radii) // args.frontier_steps != stage))
        if at_check:
            _save(checkpoint, state, bb, zero, opt)
        every = getattr(args, 'frontier_competency_every', 0)
        if every > 0 and at_check:
            # Count actual emitted frontier checks, including extra radius
            # boundaries, rather than assuming every check is a round 100 steps.
            check_number = sum(1 for n in range(1,i + 2)
                if (n % args.frontier_check_every == 0 or
                    n == args.frontier_steps or
                    n * len(radii) // args.frontier_steps !=
                    min(len(radii) - 1, (n - 1) * len(radii) // args.frontier_steps)))
            if check_number % every == 0:
                from tools import nanojev_qwen_competency_diagnostic as competency
                competency.run_at_check(state, bb, tok, args, out, source, zero)
    # Repair centers come from accumulated historical failures and final fresh
    # harvest, NOT from the held-out evaluation. A+B use the same actual errors.
    harvest_rows = []
    for j, radius in enumerate(radii):
        low = 0 if j == 0 else radii[j - 1] + 1
        harvest_rows.extend(_deck(radius, low, args.frontier_harvest_cases, args,
                                 f'REPAIR-HARVEST-{j}'))
    # The repair corpus is based on the whole accumulated failure ledger, not
    # merely the last harvest. Recheck those cases against the actual stressed
    # checkpoint: a historical failure may now be a success.
    discovered = out / 'discovered_errors.jsonl'
    unique = {_key(r):r for r in harvest_rows}
    if discovered.exists():
        for line in discovered.read_text(encoding='utf8').splitlines():
            event = json.loads(line)
            row = event['case']
            unique.setdefault(_key(row), row)
    # Cap expensive greedy inference while retaining cross-radius coverage.
    grouped = {}
    for row in unique.values():
        grouped.setdefault(row['radius'], []).append(row)
    for rows in grouped.values():
        rows.sort(key=_key)
    selected = []
    while len(selected) < args.frontier_repair_center_cap and any(grouped.values()):
        for radius in sorted(grouped):
            if len(selected) == args.frontier_repair_center_cap: break
            if grouped[radius]: selected.append(grouped[radius].pop(0))
    harvest_score = _score(bb, tok, selected, args)
    _record_observation(out, 'stress_repair_recheck', state['phase_step'], selected, harvest_score)
    centers = _hard(selected, harvest_score)
    ladder._atomic_json(out / 'repair_centers.json', centers)
    ladder._atomic_json(out / 'repair_harvest_scores.json', harvest_score)
    state['repair_candidates_rechecked'] = len(selected)
    state['archive_candidate_count'] = len(unique)
    state['status'] = 'STRESSED'
    state['repair_center_count'] = len(centers)
    _save(out / 'stress.pt', state, bb, zero, opt)
    _save(checkpoint, state, bb, zero, opt)
    _emit('QWEN_FRONTIER_DONE', {'step': state['phase_step'], 'repair_centers': len(centers),
           'best_net': state['best_net'], 'best_step': state['best_step'],
           'stressed_checkpoint': str(out / 'stress.pt'),
           'next_stage': 'repair-ab'})
    return state


def _neighbors(center, radius, limit):
    """Perturb adjacent leaves +/-d, preserving signed result and bounds."""
    neighbors, seen = [], {_key(center)}
    leaves = center['leaves']
    for i in range(len(leaves) - 1):
        for d in range(-radius, radius + 1):
            if d == 0: continue
            vals = list(leaves); vals[i] += d; vals[i + 1] -= d
            if any(abs(x) > limit for x in vals): continue
            s = 0
            if any(abs((s := s + x)) > limit for x in vals): continue
            row = ladder._case(center['rung'], tuple(vals),
                               f'{center["id"]}-NEAR-{i}-{d:+d}', 'numeric')
            row['radius'] = center['radius'];row['band'] = center['band']
            if _key(row) not in seen:
                seen.add(_key(row));neighbors.append(row)
    return neighbors


def _repair_decks(centers, args):
    around, seen = [], {_key(c) for c in centers}
    for center in centers:
        for row in _neighbors(center, args.local_radius, args.operand_limit):
            if _key(row) not in seen:
                seen.add(_key(row));around.append(row)
    return {'A_direct': centers, 'B_neighborhood': around}


def _balanced_neighbors(centers, neighbors, cap):
    by_center = {c['id']: [] for c in centers}
    for row in neighbors:
        for c in centers:
            if row['id'].startswith(c['id'] + '-NEAR-'):
                by_center[c['id']].append(row)
                break
    selected=[]
    while len(selected)<cap and any(by_center.values()):
        for c in centers:
            if len(selected)==cap:break
            if by_center[c['id']]:selected.append(by_center[c['id']].pop(0))
    return selected


def status(args):
    out = _out(args)
    path = out / 'working.json'
    if not path.is_file():
        raise RuntimeError('FRONTIER_NO_STATUS_CHECKPOINT')
    state = json.loads(path.read_text(encoding='utf8'))
    short = {'phase_step': state['phase_step'], 'status': state['status'],
             'best_net': state['best_net'], 'best_step': state['best_step'],
             'output': str(out), 'repair_ready': (out / 'stress.pt').is_file(),
             'repair_done': (out / 'repair_ab_report.json').is_file(),
             'accumulated_candidates_rechecked': state.get('repair_candidates_rechecked'),
             'chain_last_step': state.get('chain_last_step'),
             'chain_metrics': state.get('chain_last_metrics')}
    _emit('QWEN_FRONTIER_STATUS', short)
    return short


def run_repair_ab(args):
    import torch
    policy = _policy(args)
    source, digest = _load_source(args)
    out = _out(args)
    stress_path = out / 'stress.pt'
    if not stress_path.is_file():
        raise RuntimeError('FRONTIER_REPAIR_REQUIRES_FINISHED_STRESS_PT')
    if not (out / 'evaluation_deck.json').is_file() or not (out / 'repair_centers.json').is_file():
        raise RuntimeError('FRONTIER_REPAIR_MISSING_FROZEN_DECKS')
    bb, tok, zero, ps = _load_model(args, source)
    stressed, moments = _load(stress_path, bb, zero, policy, digest)
    if stressed['status'] != 'STRESSED':
        raise RuntimeError('FRONTIER_REPAIR_SOURCE_INCOMPLETE')
    evaluation = json.loads((out / 'evaluation_deck.json').read_text(encoding='utf8'))
    if stressed['eval_sha256'] != ladder.deck_fingerprint(evaluation):
        raise RuntimeError('FRONTIER_REPAIR_DECK_DRIFT')
    centers = json.loads((out / 'repair_centers.json').read_text(encoding='utf8'))
    if not centers:
        raise RuntimeError('FRONTIER_REPAIR_NO_ERRORS_TO_REPAIR')
    decks = _repair_decks(centers, args)
    if not decks['B_neighborhood']:
        raise RuntimeError('FRONTIER_REPAIR_NO_FEASIBLE_NEIGHBORS')
    chain_deck = _initialize_chain_baseline(
        stressed, bb, tok, zero, source, evaluation, args, out)
    stress_lora = zero.lora_state(bb)
    stressed_eval = _score(bb, tok, evaluation, args)
    stress_chain = _chain_score(bb, tok, chain_deck, args)
    _record_chain(out, 'stress_before_repair', stressed['phase_step'], chain_deck, stress_chain)
    # Observe actual local failures at the stressed checkpoint, BEFORE either
    # repair branch starts. Keep correct neighbors for replay, incorrect ones
    # for targeted repair. Do not infer failure from proximity alone.
    neighbor_path=out / 'repair_neighbor_results.json'
    if neighbor_path.is_file():
        saved_neighbors=json.loads(neighbor_path.read_text(encoding='utf8'))
        if saved_neighbors['stress_sha256']!=_sha(stress_path):
            raise RuntimeError('FRONTIER_NEIGHBOR_DIAGNOSTIC_DRIFT')
        neighbors=saved_neighbors['rows']
        neighbor_scores=saved_neighbors['scores']
        neighbor_seconds=saved_neighbors['seconds']
    else:
        start=time.perf_counter()
        neighbors=_balanced_neighbors(centers, decks['B_neighborhood'],
                                      args.frontier_neighbor_probe_cap)
        neighbor_scores=_score(bb, tok, neighbors, args)
        if args.device=='cuda':torch.cuda.synchronize()
        neighbor_seconds=time.perf_counter()-start
        ladder._atomic_json(neighbor_path,{'stress_sha256':_sha(stress_path),
              'rows':neighbors,'scores':neighbor_scores,'seconds':neighbor_seconds})
    incorrect_neighbors=_hard(neighbors,neighbor_scores)
    correct_neighbors=[r for r in neighbors if neighbor_scores[_key(r)]['correct']]
    _emit('QWEN_REPAIR_NEIGHBOR_MAP', {'tested':len(neighbors),
          'incorrect':len(incorrect_neighbors),'correct':len(correct_neighbors),
          'diagnostic_seconds':round(neighbor_seconds,3)})
    # Full checkpoint identity ensures each branch always starts from stress.
    comparison = {}
    for branch, rows in decks.items():
        path = out / (branch + '.pt')
        if path.is_file():
            state, saved_opt = _load(path, bb, zero, policy, digest)
            if state.get('branch') != branch or state.get('stress_sha256') != _sha(stress_path):
                raise RuntimeError('FRONTIER_REPAIR_BRANCH_DRIFT')
        else:
            zero.restore_lora(bb, stress_lora)
            state = {'policy': policy, 'source_sha256': digest, 'branch': branch,
                     'stress_sha256': _sha(stress_path), 'steps': 0, 'training_seconds': 0.}
            saved_opt = moments
        opt = _optimizer(ps, args, saved_opt)
        _emit('QWEN_REPAIR_BRANCH_START', {'branch': branch, 'centers': len(centers),
              'neighbors': len(decks['B_neighborhood']), 'steps_done': state['steps']})
        # B mixes damaged centers and their exact-result neighborhoods.
        for step in range(state['steps'], args.frontier_repair_steps):
            rng = random.Random(args.seed + step * 131 + (17 if branch == 'B_neighborhood' else 0))
            if branch == 'A_direct':
                batch = _training_batch(rows, args.batch_size, rng)
            else:
                batch = [rng.choice(centers)]
                if correct_neighbors and len(batch)<args.batch_size:
                    batch.append(rng.choice(correct_neighbors))
                while len(batch)<args.batch_size:
                    candidates=incorrect_neighbors if incorrect_neighbors else rows
                    batch.append(rng.choice(candidates))
            bb.train();started = time.perf_counter()
            ladder._train_step(bb, tok, opt, batch, args)
            if args.device == 'cuda':torch.cuda.synchronize()
            state['training_seconds'] += time.perf_counter() - started
            bb.eval();state['steps'] = step + 1
            if state['steps'] % args.frontier_check_every == 0 or state['steps'] == args.frontier_repair_steps:
                _save(path, state, bb, zero, opt)
        eval_started = time.perf_counter()
        scored = _score(bb, tok, evaluation, args)
        harvest_score = _score(bb, tok, centers, args)
        chain_scored = _chain_score(bb, tok, chain_deck, args)
        _record_chain(out, 'repair_' + branch, stressed['phase_step'], chain_deck, chain_scored)
        if args.device == 'cuda': torch.cuda.synchronize()
        evaluation_seconds = time.perf_counter() - eval_started
        restored = sum(v['correct'] for v in harvest_score.values())
        metrics = _transitions(stressed_eval, scored, evaluation)
        from_baseline = _transitions(stressed['baseline'], scored, evaluation)
        t = state['training_seconds']
        comparison[branch] = {'from_stressed': metrics,
                               'from_initial': from_baseline,
                               'chain_from_stressed': _chain_metrics(stress_chain, chain_scored, chain_deck),
                               'chain_from_initial': _chain_metrics(stressed['chain_baseline'], chain_scored, chain_deck),
                               'evaluation_seconds': evaluation_seconds,
                               'total_gpu_seconds_including_evaluation': t + evaluation_seconds + (neighbor_seconds if branch == 'B_neighborhood' else 0),
                               'repaired_centers': restored,
                               'total_centers': len(centers),
                               'training_seconds': t,
                               'neighborhood_diagnostic_seconds': neighbor_seconds if branch == 'B_neighborhood' else 0,
                               'total_preparation_and_training_seconds': t + (neighbor_seconds if branch == 'B_neighborhood' else 0),
                               'net_repair_per_gpu_hour': 3600 * metrics['net'] / (t + (neighbor_seconds if branch == 'B_neighborhood' else 0)) if t else None,
                               'steps': state['steps']}
        _emit('QWEN_REPAIR_BRANCH_DONE', {'branch': branch, **comparison[branch]})
    report = {'schema': SCHEMA, 'source_step': stressed['phase_step'],
              'stress_checkpoint': str(stress_path),
              'eval_cases': len(evaluation),
              'repair_centers': len(centers),
              'neighbor_probe_cases': len(neighbors), 'neighbor_probe_wrong': len(incorrect_neighbors),
              'A_direct': comparison['A_direct'],
              'B_neighborhood': comparison['B_neighborhood'],
              'experimental_meaning': 'Shared-parameter LoRA changes; localized examples, not sparse parameters',
              'winner_by_net_delta': max(comparison, key=lambda x:comparison[x]['from_stressed']['net'])}
    ladder._atomic_json(out / 'repair_ab_report.json', report)
    _emit('QWEN_REPAIR_AB_DONE', report)
    return report
