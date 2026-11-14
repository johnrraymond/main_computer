#!/usr/bin/env python3
"""Numerate Spine Cassette v1: record a frozen LM's unary-swap trajectories.

This is an observational smoke test. It does not train, optimize, inject an
activation, or claim to have found a mathematical circuit. Mathematical truth
is supplied by an exact integer oracle, never by the model.

Default: 5-unit complete trajectory, pipe unary, both '+' and matched '-'
controls, Qwen3-0.6B, 12 forward passes. Model downloads require opt-in.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import random
import sys

SCHEMA = 'nanojev.numerate-spine-cassette.v1'
SOURCE = {
    'qwen': ('Qwen/Qwen3-0.6B', 'c1899de289a04d12100db370d81485cdf75e47ca'),
    'pythia': ('EleutherAI/pythia-70m', None),
    'tinystories': ('roneneldan/TinyStories-33M', None),
}
ENCODINGS = {'pipe': '|', 'zero': '0', 'one': '1', 'alternating': '01'}
NOISE = {
    'clean': '',
    'human': 'A traveler watched clouds gathering beyond a quiet bridge.',
    'code': 'function probe(){ return cache.ready ? state : fallback; }',
}
DEFAULT_OUTPUT = Path(r'C:\Users\subsi\NanoJev\runs\numerate_spine_cassette_v1')


def digest(data):
    if not isinstance(data, bytes):
        data = data.encode('utf-8')
    return hashlib.sha256(data).hexdigest()


def emit(event, **data):
    print(json.dumps({'event': event, **data}, sort_keys=True), flush=True)


def parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--self-test', action='store_true', help='test cassette, tokenizer alignment and capture with a toy model; no Hugging Face downloads')
    p.add_argument('--prepare-only', action='store_true', help='generate sealed prompts without loading any model')
    p.add_argument('--output-root', type=Path, default=DEFAULT_OUTPUT)
    p.add_argument('--models', nargs='+', choices=tuple(SOURCE), default=['qwen'])
    p.add_argument('--totals', nargs='+', type=int, default=[5])
    p.add_argument('--encodings', nargs='+', choices=tuple(ENCODINGS), default=['pipe'])
    p.add_argument('--widths', nargs='+', type=int, default=[1], help='repeat factor for each unit symbol; NOT model token count')
    p.add_argument('--noises', nargs='+', choices=tuple(NOISE), default=['clean'])
    p.add_argument('--directions', nargs='+', choices=('forward', 'backward'), default=['forward'])
    p.add_argument('--orders', nargs='+', choices=('rule-first', 'expression-first'), default=['rule-first'])
    p.add_argument('--seed', type=int, default=17415)
    p.add_argument('--device', choices=('auto', 'cpu', 'cuda'), default='auto')
    p.add_argument('--dtype', choices=('auto', 'float32', 'float16', 'bfloat16'), default='auto')
    p.add_argument('--allow-model-download', action='store_true')
    p.add_argument('--max-prompt-tokens', type=int, default=2048)
    p.add_argument('--max-cases', type=int, default=5000, help='hard guard against combinatorial explosion')
    p.add_argument('--resume', action='store_true', help='resume only with matching cassette and model identity')
    p.add_argument('--analyze-only', action='store_true', help='rebuild reports from complete saved .npz records; never load a backbone')
    p.add_argument('--report-every', type=int, default=10)
    return p


def options(args):
    for key in ('models', 'totals', 'encodings', 'widths', 'noises', 'directions', 'orders'):
        vals = getattr(args, key)
        if len(vals) != len(set(vals)):
            raise ValueError(f'CASSETTE_DUPLICATE_OPTION:{key}')
    if any(n < 2 or n > 256 for n in args.totals):
        raise ValueError('CASSETTE_TOTAL_OUT_OF_RANGE: expected [2,256]')
    if any(w < 1 or w > 64 for w in args.widths):
        raise ValueError('CASSETTE_WIDTH_OUT_OF_RANGE: expected [1,64]')
    if args.report_every < 1 or args.max_cases < 1 or args.max_prompt_tokens < 8:
        raise ValueError('CASSETTE_INVALID_LIMIT')


def tally(number, encoding, width):
    """Exact quantity, with an explicit empty-operand sentinel."""
    if number < 0:
        raise ValueError('CASSETTE_NEGATIVE_TALLY')
    return ENCODINGS[encoding] * (number * width) if number else 'EMPTY'


def build_prompt(case):
    symbol = ENCODINGS[case['encoding']] * case['width']
    direction = case['direction']
    rule = (f'RULE: One repeated block [{symbol}] means one unit. '
            'The word EMPTY means zero units. Evaluate + as addition and - as subtraction. '
            'Return the numerical answer in ordinary decimal notation.')
    expression = f"EXPR: {tally(case['left'],case['encoding'],case['width'])}{case['operator']}{tally(case['right'],case['encoding'],case['width'])}"
    meta = f'DIRECTION: {direction} progression of the addition boundary.'
    fields = [rule, meta, expression] if case['order'] == 'rule-first' else [expression, meta, rule]
    preface = 'CASSETTE: conserved-unit transformation.'
    if case['noise'] != 'clean':
        preface += '\nUNRELATED CONTEXT: ' + NOISE[case['noise']]
    return preface + '\n' + '\n'.join(fields) + '\nAnswer:'


def generate(args):
    """The plus and minus controls have exactly the same symbol quantities.

    Minus has a different mathematical meaning, and is *not* a valid unary
    swap; it is a matched surface control. Candidate answer set is identical
    across + and - and fixed for each conserved-total trajectory.
    """
    options(args)
    cases = []
    for total in args.totals:
        for encoding in args.encodings:
            for width in args.widths:
                for noise in args.noises:
                    for direction in args.directions:
                        for order in args.orders:
                            steps = list(range(total + 1))
                            if direction == 'backward':
                                steps.reverse()
                            trajectory = f'N{total}.{encoding}.w{width}.{noise}.{direction}.{order}'
                            for index, left in enumerate(steps):
                                right = total - left
                                for op in ('+', '-'):
                                    case = {
                                        'id': f'{trajectory}.s{index:03d}.{("plus" if op == "+" else "minus")}',
                                        'trajectory': trajectory,
                                        'step': index,
                                        'total': total,
                                        'left': left,
                                        'right': right,
                                        'operator': op,
                                        'true_result': left + right if op == '+' else left - right,
                                        'encoding': encoding,
                                        'width': width,
                                        'noise': noise,
                                        'direction': direction,
                                        'order': order,
                                        'answer_candidates': [total - 1, total, total + 1],
                                    }
                                    case['prompt'] = build_prompt(case)
                                    case['prompt_sha256'] = digest(case['prompt'])
                                    cases.append(case)
                                    if len(cases) > args.max_cases:
                                        raise ValueError('CASSETTE_CASE_CAP_EXCEEDED: narrow the sweep or explicitly raise --max-cases')
    if len({x['id'] for x in cases}) != len(cases):
        raise AssertionError('CASSETTE_NONUNIQUE_IDS')
    for c in cases:
        if c['operator'] == '+':
            assert c['true_result'] == c['total'] == c['left'] + c['right']
        assert len(c['answer_candidates']) == 3
    return cases


def canonical(data):
    return json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def write_json(path, data):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + '\n', encoding='utf-8')
    os.replace(temp, path)


def write_cases(root, cases, args):
    root.mkdir(parents=True, exist_ok=True)
    case_lines = ''.join(canonical(c) + '\n' for c in cases)
    bank_sha = digest(case_lines)
    expected = {
        'schema': SCHEMA,
        'bank_sha256': bank_sha,
        'case_count': len(cases),
        'settings': {k: getattr(args, k) for k in ('totals', 'encodings', 'widths', 'noises', 'directions', 'orders', 'seed')},
        'mathematics': 'Exact tally; + preserves total under boundary swap; - is a paired negative control; candidates [total-1,total,total+1].',
        'claim': 'Stimulus and observation only; no learning, correction, or causal interpretation.',
    }
    bank_path = root / 'cases.jsonl'
    meta_path = root / 'cassette_manifest.json'
    # Python text-mode writes CRLF on Windows. V1 hashed LF before writing,
    # then verified raw CRLF bytes on resume. Accept only these two exact
    # deterministic byte representations; never ignore actual bank drift.
    bank_lf = case_lines.encode('utf-8')
    bank_crlf = case_lines.replace('\n', '\r\n').encode('utf-8')
    if bank_path.exists() or meta_path.exists():
        if not args.resume:
            raise RuntimeError('CASSETTE_OUTPUT_EXISTS: use --resume or another --output-root')
        if not bank_path.exists() or not meta_path.exists():
            raise RuntimeError('CASSETTE_INCOMPLETE_BANK_PAIR')
        persisted = bank_path.read_bytes()
        if persisted not in (bank_lf, bank_crlf) or json.loads(meta_path.read_text(encoding='utf-8')) != expected:
            raise RuntimeError('CASSETTE_BANK_IDENTITY_DRIFT')
    else:
        # Explicit binary write prevents Windows newline translation in V2.
        temp_bank = bank_path.with_suffix('.jsonl.tmp')
        temp_bank.write_bytes(bank_lf)
        os.replace(temp_bank, bank_path)
        write_json(meta_path, expected)
    return expected


def locate_operator(tokenizer, prompt):
    """Offsets ensure a merged tokenizer token can still be mapped to '+/-'."""
    marker = prompt.index('EXPR: ') + len('EXPR: ')
    end = prompt.index('\n', marker)
    expression = prompt[marker:end]
    operator_char = marker + next(i for i, c in enumerate(expression) if c in '+-')
    enc = tokenizer(prompt, add_special_tokens=True, return_offsets_mapping=True)
    ids = list(enc['input_ids'])
    offsets = enc['offset_mapping']
    locations = [i for i, (a, b) in enumerate(offsets) if a <= operator_char < b and a != b]
    if len(locations) != 1 or not ids:
        raise RuntimeError('CASSETTE_OPERATOR_TOKEN_ALIGNMENT_FAILED: require a fast tokenizer')
    return ids, int(locations[0]), operator_char


def scores_and_activations(*, torch, model, tokenizer, prompt, answers, device, max_prompt_tokens):
    import numpy as np
    prefix, op_token, op_character = locate_operator(tokenizer, prompt)
    suffixes = [tokenizer.encode(' ' + str(x), add_special_tokens=False) for x in answers]
    if any(not v for v in suffixes):
        raise RuntimeError('CASSETTE_EMPTY_ANSWER_TOKENIZATION')
    if max(len(prefix) + len(s) for s in suffixes) > max_prompt_tokens:
        raise RuntimeError('CASSETTE_TOKEN_BUDGET_EXCEEDED')
    padding = tokenizer.pad_token_id
    if padding is None:
        padding = tokenizer.eos_token_id
    if padding is None:
        padding = 0
    length = max(len(prefix) + len(s) for s in suffixes)
    tokens = torch.full((3, length), int(padding), dtype=torch.long, device=device)
    mask = torch.zeros((3, length), dtype=torch.long, device=device)
    for i, suffix in enumerate(suffixes):
        seq = prefix + suffix
        tokens[i, :len(seq)] = torch.tensor(seq, dtype=torch.long, device=device)
        mask[i, :len(seq)] = 1
    with torch.inference_mode():
        out = model(input_ids=tokens, attention_mask=mask, use_cache=False, output_hidden_states=True)
        logps = []
        for i, suffix in enumerate(suffixes):
            first = len(prefix) - 1
            relevant = out.logits[i, first:first + len(suffix)].float()
            targets = torch.tensor(suffix, dtype=torch.long, device=device)
            val = (relevant.gather(-1, targets.unsqueeze(-1)).squeeze(-1)
                   - torch.logsumexp(relevant, dim=-1)).sum()
            logps.append(float(val.item()))
        # [embedding and each transformer block, operator/last-prompt, hidden size]
        vectors = torch.stack([
            torch.stack((state[0, op_token], state[0, len(prefix) - 1]), dim=0)
            for state in out.hidden_states
        ], dim=0).float().cpu().numpy()
    if not np.isfinite(vectors).all() or not all(math.isfinite(x) for x in logps):
        raise RuntimeError('CASSETTE_NONFINITE_DATA')
    top = max(logps)
    w = [math.exp(v - top) for v in logps]
    probs = [float(z / sum(w)) for z in w]
    gold = 1  # center candidate is the trajectory's invariant
    return {
        'candidate_logp': logps,
        'candidate_probs': probs,
        'predicted_candidate': answers[max(range(3), key=lambda j: logps[j])],
        'invariant_margin': logps[gold] - max(logps[0], logps[2]),
        'invariant_probability': probs[gold],
        'prefix_tokens': len(prefix),
        'operator_token_index': op_token,
        'operator_character_index': op_character,
        'answer_token_counts': [len(s) for s in suffixes],
        'input_ids_sha256': digest(canonical(prefix)),
        'input_ids': prefix,
    }, vectors


def load_source(model_name, args):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    source, revision = SOURCE[model_name]
    device = ('cuda' if torch.cuda.is_available() else 'cpu') if args.device == 'auto' else args.device
    if device == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('CASSETTE_CUDA_UNAVAILABLE')
    dtype = (torch.float32 if device == 'cpu' else torch.float16) if args.dtype == 'auto' else getattr(torch, args.dtype)
    if device == 'cpu' and dtype == torch.float16:
        raise RuntimeError('CASSETTE_CPU_FLOAT16_NOT_SUPPORTED: use --dtype float32')
    kwargs = {'local_files_only': not args.allow_model_download, 'trust_remote_code': False}
    if revision:
        kwargs['revision'] = revision
    emit('cassette_model_loading', model=model_name, source=source, device=device, dtype=str(dtype))
    tokenizer = AutoTokenizer.from_pretrained(source, use_fast=True, **kwargs)
    if not getattr(tokenizer, 'is_fast', False):
        raise RuntimeError('CASSETTE_FAST_TOKENIZER_REQUIRED')
    model = AutoModelForCausalLM.from_pretrained(source, dtype=dtype, **kwargs).eval().to(device)
    for param in model.parameters():
        param.requires_grad_(False)
    info = {'source': source, 'requested_revision': revision,
            'resolved_commit': getattr(model.config, '_commit_hash', None),
            'device': device, 'dtype': str(dtype), 'model_type': getattr(model.config, 'model_type', None),
            'layer_count_including_embedding': getattr(model.config, 'num_hidden_layers', None)}
    emit('cassette_model_ready', model=model_name, model_info=info,
         trainable_parameters=sum(p.numel() for p in model.parameters() if p.requires_grad))
    return torch, model, tokenizer, device, info


def read_existing(path, expected):
    import numpy as np
    if not path.exists():
        return None
    with np.load(path, allow_pickle=False) as record:
        if set(record.files) != {'metadata', 'vectors'}:
            raise RuntimeError('CASSETTE_BAD_RECORD_SHAPE')
        metadata = json.loads(str(record['metadata'].item()))
        vectors = record['vectors'].astype(np.float32)
    for key in ('id', 'prompt_sha256', 'bank_sha256', 'model_info'):
        if metadata.get(key) != expected.get(key):
            raise RuntimeError('CASSETTE_RESUME_RECORD_IDENTITY_DRIFT:' + path.name)
    if vectors.ndim != 3 or vectors.shape[1] != 2 or not np.isfinite(vectors).all():
        raise RuntimeError('CASSETTE_BAD_VECTOR_DATA')
    return metadata, vectors


def measurement_row(case, record):
    fields = (
        'candidate_logp', 'candidate_probs', 'predicted_candidate',
        'invariant_margin', 'invariant_probability', 'prefix_tokens',
        'operator_token_index', 'operator_character_index',
        'answer_token_counts', 'input_ids_sha256',
    )
    return {**case, **{key: record[key] for key in fields}}


def analyze_saved(model_name, cases, bank, root):
    """Rebuild plots/tables from sealed activation files without loading HF or CUDA.

    The bank, model manifest and EVERY per-state record are identity-checked
    before publishing reports. Incomplete/foreign captures always fail closed.
    """
    import numpy as np
    result_dir = root / model_name
    manifest_path = result_dir / 'model_manifest.json'
    if not manifest_path.is_file():
        raise RuntimeError('CASSETTE_NO_SAVED_MODEL_MANIFEST:' + model_name)
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('schema') != SCHEMA or manifest.get('bank_sha256') != bank['bank_sha256']:
        raise RuntimeError('CASSETTE_MODEL_RUN_IDENTITY_DRIFT')
    model_info = manifest.get('model_info')
    if not isinstance(model_info, dict) or model_info.get('source') != SOURCE[model_name][0]:
        raise RuntimeError('CASSETTE_MODEL_RUN_IDENTITY_DRIFT')
    if model_info.get('requested_revision') != SOURCE[model_name][1]:
        raise RuntimeError('CASSETTE_MODEL_RUN_IDENTITY_DRIFT')
    vector_dir = result_dir / 'vectors'
    rows, vectors = [], []
    for i, case in enumerate(cases):
        path = vector_dir / f'{i:06d}.npz'
        if not path.is_file():
            raise RuntimeError('CASSETTE_MISSING_SAVED_RECORD:' + path.name)
        identity = {'id': case['id'], 'prompt_sha256': case['prompt_sha256'],
                    'bank_sha256': bank['bank_sha256'], 'model_info': model_info}
        record, vec = read_existing(path, identity)
        rows.append(measurement_row(case, record))
        vectors.append(vec)
    recorded_paths = set(vector_dir.glob('*.npz'))
    if len(recorded_paths) != len(cases):
        raise RuntimeError('CASSETTE_UNEXPECTED_SAVED_RECORD_COUNT')
    emit('cassette_saved_records_verified', model=model_name, records=len(rows),
         model_loaded=False, bank_sha256=bank['bank_sha256'])
    return analyze(result_dir, rows, np.stack(vectors, axis=0), model_info)


def run_model(model_name, cases, bank, root, args, source_loader=load_source):
    import numpy as np
    torch, model, tokenizer, device, model_info = source_loader(model_name, args)
    result_dir = root / model_name
    vector_dir = result_dir / 'vectors'
    vector_dir.mkdir(parents=True, exist_ok=True)
    result_meta = result_dir / 'model_manifest.json'
    expected = {'schema': SCHEMA, 'bank_sha256': bank['bank_sha256'], 'model_info': model_info,
                'max_prompt_tokens': args.max_prompt_tokens}
    if result_meta.exists():
        if not args.resume or json.loads(result_meta.read_text(encoding='utf-8')) != expected:
            raise RuntimeError('CASSETTE_MODEL_RUN_IDENTITY_DRIFT')
    else:
        if list(vector_dir.iterdir()):
            raise RuntimeError('CASSETTE_ORPHAN_RECORDS')
        write_json(result_meta, expected)
    rows = []
    vectors = []
    try:
        for i, case in enumerate(cases):
            path = vector_dir / f'{i:06d}.npz'
            identity = {'id': case['id'], 'prompt_sha256': case['prompt_sha256'],
                        'bank_sha256': bank['bank_sha256'], 'model_info': model_info}
            if path.exists():
                if not args.resume:
                    raise RuntimeError('CASSETTE_RESULT_EXISTS_USE_RESUME')
                rec, vec = read_existing(path, identity)
            else:
                measurement, vec = scores_and_activations(
                    torch=torch, model=model, tokenizer=tokenizer, prompt=case['prompt'],
                    answers=case['answer_candidates'], device=device,
                    max_prompt_tokens=args.max_prompt_tokens)
                rec = {**identity, **measurement}
                tmp = path.with_suffix('.tmp.npz')
                np.savez_compressed(tmp, metadata=canonical(rec), vectors=vec.astype(np.float16))
                os.replace(tmp, path)
            rows.append(measurement_row(case, rec))
            vectors.append(vec.astype(np.float32))
            if (i + 1) % args.report_every == 0 or i + 1 == len(cases):
                emit('cassette_progress', model=model_name, done=i + 1, total=len(cases))
        array = np.stack(vectors, axis=0)
        analyze(result_dir, rows, array, model_info)
    finally:
        del model
        if device == 'cuda':
            torch.cuda.empty_cache()


def analyze(root, rows, array, info):
    import numpy as np
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    if array.ndim != 4 or array.shape[0] != len(rows) or array.shape[2] != 2:
        raise RuntimeError('CASSETTE_ACTIVATION_SHAPE_INVALID')
    n_layers = array.shape[1]
    layer_names = ['embedding'] + [f'block_{i}' for i in range(1, n_layers)]
    # Per-state metadata is complete enough to reconstruct the plots independently.
    data_fields = [
        'id', 'trajectory', 'step', 'total', 'left', 'right', 'operator', 'encoding',
        'width', 'noise', 'direction', 'order', 'true_result', 'prompt_sha256',
        'prefix_tokens', 'operator_token_index', 'operator_character_index',
        'predicted_candidate', 'invariant_margin', 'invariant_probability',
        'candidate_logp', 'candidate_probs', 'answer_token_counts', 'input_ids_sha256',
    ]
    with (root / 'per_state.csv').open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=data_fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: canonical(row[k]) if isinstance(row[k], list) else row[k] for k in data_fields})
    # Trajectory metrics: plus-only, adjacent states, same total and surface setting.
    by_traj = defaultdict(list)
    index = {row['id']: i for i, row in enumerate(rows)}
    for row in rows:
        if row['operator'] == '+':
            by_traj[row['trajectory']].append(row)
    step_data = []
    summaries = []
    for trajectory, entries in by_traj.items():
        entries.sort(key=lambda r: r['step'])
        margins = [x['invariant_margin'] for x in entries]
        good = [x['predicted_candidate'] == x['total'] for x in entries]
        summaries.append({'trajectory': trajectory, 'states': len(entries),
                          'accuracy': sum(good)/len(good),
                          'mean_invariant_margin': float(np.mean(margins)),
                          'invariant_margin_std': float(np.std(margins)),
                          'mean_invariant_probability': float(np.mean([r['invariant_probability'] for r in entries])),
                          'prefix_token_min': min(r['prefix_tokens'] for r in entries),
                          'prefix_token_max': max(r['prefix_tokens'] for r in entries)})
        for previous, current in zip(entries, entries[1:]):
            a, b = array[index[previous['id']]], array[index[current['id']]]
            for layer in range(n_layers):
                for coord, name in enumerate(('operator', 'answer')):
                    diff = float(np.linalg.norm(a[layer, coord] - b[layer, coord]) / math.sqrt(array.shape[-1]))
                    step_data.append({'trajectory': trajectory, 'step_from': previous['step'],
                                      'step_to': current['step'], 'layer': layer_names[layer],
                                      'coordinate': name, 'rms_activation_displacement': diff})
    with (root / 'trajectory_summary.csv').open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(summaries[0]));writer.writeheader();writer.writerows(summaries)
    with (root / 'step_distances.csv').open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(step_data[0]));writer.writeheader();writer.writerows(step_data)
    # Matched + versus - operator controls, identical operand counts and context.
    controls = []
    for row in rows:
        if row['operator'] != '+':
            continue
        counterpart = row['id'].replace('.plus', '.minus')
        neg = next((r for r in rows if r['id'] == counterpart), None)
        if neg is None:
            raise RuntimeError('CASSETTE_MISSING_OPERATOR_CONTROL')
        a, b = array[index[row['id']]], array[index[neg['id']]]
        for layer in range(n_layers):
            controls.append({'trajectory': row['trajectory'], 'step': row['step'],
                             'discriminative_control': row['right'] > 0,
                             'layer': layer_names[layer],
                             'answer_rms_plus_minus': float(np.linalg.norm(a[layer, 1] - b[layer, 1]) / math.sqrt(array.shape[-1])),
                             'operator_rms_plus_minus': float(np.linalg.norm(a[layer, 0] - b[layer, 0]) / math.sqrt(array.shape[-1])),
                             'margin_plus': row['invariant_margin'], 'margin_minus': neg['invariant_margin']})
    with (root / 'operator_controls.csv').open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(controls[0]));writer.writeheader();writer.writerows(controls)
    # Plots show recorded trajectories ONLY; no fitted corrections or causal claims.
    fig, ax = plt.subplots(figsize=(10, 5))
    for tr, seq in by_traj.items():
        seq.sort(key=lambda r: r['step'])
        ax.plot([r['step'] for r in seq], [r['invariant_margin'] for r in seq], marker='.', label=tr)
    ax.axhline(0.0, linestyle='--', linewidth=1)
    ax.set(xlabel='Cassette step', ylabel='logP(gold) - max logP(neighbor)',
           title='Native generator: invariant answer margin (no correction)')
    if len(by_traj) <= 12:
        ax.legend(fontsize=7, loc='best')
    fig.tight_layout();fig.savefig(root / 'answer_margin.png', dpi=140);plt.close(fig)
    fig, ax = plt.subplots(figsize=(10, 5))
    for coordinate in ('answer', 'operator'):
        x, y = [], []
        for layer in layer_names:
            vals = [r['rms_activation_displacement'] for r in step_data
                    if r['coordinate'] == coordinate and r['layer'] == layer]
            x.append(layer_names.index(layer));y.append(float(np.mean(vals)))
        ax.plot(x, y, marker='.', label=coordinate)
    ax.set(xlabel='Hidden-state index (0 = embedding)', ylabel='Mean adjacent-step RMS activation change',
           title='Raw swap trajectories: displacement by layer')
    ax.legend();fig.tight_layout();fig.savefig(root / 'layer_displacement.png', dpi=140);plt.close(fig)
    # PCA fitted once on all plus states, solely descriptive. Not an invariant decoder.
    plus_rows = [r for r in rows if r['operator'] == '+']
    matrix = np.stack([array[index[r['id']], -1, 1] for r in plus_rows]).astype(np.float64)
    center = matrix.mean(axis=0)
    _, _, vh = np.linalg.svd(matrix-center, full_matrices=False)
    coords = (matrix-center) @ vh[:2].T
    coord_index = {r['id']: coords[i] for i, r in enumerate(plus_rows)}
    fig, ax = plt.subplots(figsize=(8, 6))
    for tr, seq in by_traj.items():
        xy = np.array([coord_index[r['id']] for r in sorted(seq, key=lambda r: r['step'])])
        ax.plot(xy[:, 0], xy[:, 1], marker='.', label=tr)
    ax.set(xlabel='PCA 1 (arbitrary scale)', ylabel='PCA 2 (arbitrary scale)',
           title='Final-layer answer-position trajectory; descriptive PCA only')
    if len(by_traj) <= 12:
        ax.legend(fontsize=7, loc='best')
    fig.tight_layout();fig.savefig(root / 'activation_pca.png', dpi=140);plt.close(fig)
    report = {
        'schema': SCHEMA, 'model_info': info,
        'case_count': len(rows), 'trajectories': len(by_traj),
        'layer_count_including_embedding': n_layers, 'hidden_size': array.shape[-1],
        'plus_accuracy': float(sum(r['predicted_candidate'] == r['total'] for r in plus_rows) / len(plus_rows)),
        'trajectories_summary': summaries,
        'interpretation': ('Plots show observational activation geometry, not causal numeracy. '
                           'Smoothness alone is not evidence of quantity coding; compare different totals and held-out controls.'),
        'files': ['per_state.csv', 'trajectory_summary.csv', 'step_distances.csv', 'operator_controls.csv',
                  'answer_margin.png', 'layer_displacement.png', 'activation_pca.png', 'vectors/*.npz'],
    }
    write_json(root / 'report.json', report)
    emit('cassette_model_complete', root=str(root), summary={k: report[k] for k in
          ('case_count', 'trajectories', 'plus_accuracy', 'layer_count_including_embedding')})
    return report


def self_test():
    """End-to-end shape, score, resume, plot test with a toy frozen LM."""
    import tempfile
    import torch
    import numpy as np

    class ToyTokenizer:
        is_fast = True
        pad_token_id = 0
        eos_token_id = 2
        def encode(self, text, add_special_tokens=True):
            return ([1] if add_special_tokens else []) + [ord(c) + 5 for c in text]
        def __call__(self, text, add_special_tokens=True, return_offsets_mapping=False):
            ids = self.encode(text, add_special_tokens=add_special_tokens)
            offs = ([(0,0)] if add_special_tokens else []) + [(i,i+1) for i in range(len(text))]
            return {'input_ids': ids, 'offset_mapping': offs}

    class ToyModel(torch.nn.Module):
        def forward(self, input_ids, attention_mask, use_cache=False, output_hidden_states=False):
            b, length = input_ids.shape
            x = input_ids.float()
            components = [torch.sin(x/31),torch.cos(x/23),x/1000,x/2000,torch.sin(x/7),torch.cos(x/5),x*0+1,x*0]
            hidden = torch.stack(components, dim=-1)
            hidden2 = hidden + torch.cumsum(hidden,dim=1)/100
            logits = torch.zeros((b,length,1100), dtype=torch.float32)
            for i in range(b):
                # Explicitly exercise the answer-scoring contract; toy model deliberately non-numerate.
                logits[i,:,ord('5')+5] = .2
            return type('Out',(),{'logits':logits,'hidden_states':(hidden,hidden2)})()

    from argparse import Namespace
    args = Namespace(models=['qwen'],totals=[3,5],encodings=['pipe','zero'],widths=[1,2],
                     noises=['clean','code'],directions=['forward'],orders=['rule-first'],seed=4,
                     max_cases=999,max_prompt_tokens=1000,report_every=500,
                     resume=False,device='cpu',dtype='float32')
    cases = generate(args)
    assert len(cases) == 2*(4+6)*2*2*2
    assert all(c['true_result'] == c['total'] for c in cases if c['operator'] == '+')
    assert len({c['prompt_sha256'] for c in cases}) == len(cases)
    c = cases[0]
    tok = ToyTokenizer()
    ids, op_tok, op_ch = locate_operator(tok, c['prompt'])
    assert ids[op_tok] == ord(c['operator'])+5 and c['prompt'][op_ch] == c['operator']
    r, a = scores_and_activations(torch=torch,model=ToyModel(),tokenizer=tok,
                                  prompt=c['prompt'], answers=c['answer_candidates'],
                                  device='cpu',max_prompt_tokens=1000)
    assert a.shape == (2,2,8) and math.isclose(sum(r['candidate_probs']),1.0,abs_tol=1e-6)
    assert r['input_ids'] == ids and r['prefix_tokens'] == len(ids)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bank = write_cases(root,cases,args)
        args.resume = True
        assert write_cases(root,cases,args) == bank
        def fake_loader(model_name,args):
            return torch,ToyModel(),ToyTokenizer(),'cpu',{'source':SOURCE['qwen'][0], 'requested_revision':SOURCE['qwen'][1], 'resolved_commit':'toy','device':'cpu'}
        run_model('qwen',cases,bank,root,args,source_loader=fake_loader)
        expected = root/'qwen'
        for filename in ('report.json','per_state.csv','operator_controls.csv',
                         'step_distances.csv','answer_margin.png','layer_displacement.png','activation_pca.png'):
            assert (expected/filename).exists(), filename
        assert len(list((expected/'vectors').glob('*.npz'))) == len(cases)
        # Emulate the existing V1 Windows CRLF bank, then resume safely.
        bank_path = root / 'cases.jsonl'
        bank_path.write_bytes(bank_path.read_bytes().replace(b'\n', b'\r\n'))
        assert write_cases(root, cases, args) == bank
        run_model('qwen',cases,bank,root,args,source_loader=fake_loader)
        assert json.loads((expected/'report.json').read_text())['case_count'] == len(cases)
        original_vector = (expected/'vectors'/'000000.npz').read_bytes()
        assert analyze_saved('qwen',cases,bank,root)['case_count'] == len(cases)
        assert (expected/'vectors'/'000000.npz').read_bytes() == original_vector
    emit('cassette_self_test_pass', test_cases=len(cases), frozen_toy_model=True,
         live_backbone_loaded=False, mathematical_labels_verified=True, saved_plots=True,
         resume_verified=True)


def main(argv=None):
    args = parser().parse_args(argv)
    if args.self_test:
        self_test()
        return
    cases = generate(args)
    root = args.output_root.expanduser().resolve()
    if args.analyze_only and args.prepare_only:
        raise ValueError('CASSETTE_CONFLICTING_MODES')
    if args.analyze_only:
        args.resume = True
        if not (root / 'cases.jsonl').is_file() or not (root / 'cassette_manifest.json').is_file():
            raise RuntimeError('CASSETTE_NO_SAVED_BANK_FOR_ANALYSIS')
    if not args.prepare_only:
        # Check BEFORE creating a bank or loading expensive HF models.
        try:
            import matplotlib
        except ImportError as exc:
            raise RuntimeError(f'CASSETTE_PLOTTING_DEPENDENCY_MISSING: run \"{sys.executable}\" -m pip install matplotlib') from exc
    bank = write_cases(root, cases, args)
    emit('cassette_bank_verified', output_root=str(root), bank_sha256=bank['bank_sha256'],
         cases=len(cases), trajectories=len({x['trajectory'] for x in cases}), trainable_parameters=0,
         first_plus=next(c['prompt'] for c in cases if c['operator'] == '+'))
    if args.prepare_only:
        emit('cassette_prepared_only', model_loaded=False)
        return
    if args.analyze_only:
        for model_name in args.models:
            analyze_saved(model_name, cases, bank, root)
        emit('cassette_analysis_complete', output_root=str(root), models=args.models, model_loaded=False)
        return
    for model_name in args.models:
        run_model(model_name,cases,bank,root,args)
    emit('cassette_smoke_complete', output_root=str(root), models=args.models)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        emit('cassette_smoke_failure', error=str(exc), type=type(exc).__name__)
        raise
