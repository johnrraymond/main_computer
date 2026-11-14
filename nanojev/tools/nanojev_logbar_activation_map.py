#!/usr/bin/env python3
"""Paired decimal/log-bar frozen activation map, -2000..2000; no arithmetic.

Log-bar encoding uses q=round(log1p(abs(n))*10**precision). The integer
part of q/scale is a run of bars, the fractional digits are separate runs.
Each repeated encoded prompt is evaluated only once; collisions are documented.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys

import nanojev_decimal_number_activation_map as dec
import nanojev_numerate_spine_cassette_smoke as cassette

SCHEMA = 'nanojev.logbar-activation-map.v1'
DEFAULT_ROOT = Path.home() / 'NanoJev' / 'runs' / 'logbar_activation_map_v1'
CHANNELS = dec.CHANNELS


def grid():
    pos = (list(range(1, 21)) + list(range(25, 201, 5))
           + list(range(210, 501, 10)) + list(range(525, 1001, 25))
           + list(range(1050, 2001, 50)))
    values = [-n for n in reversed(pos)] + [0] + pos
    if len(values) != 253 or len(set(values)) != len(values) or values != sorted(values):
        raise AssertionError('LOGBAR_GRID_INVARIANT_FAILED')
    return values


def quantize(number, fractional_digits=1):
    if type(number) is not int or not -2000 <= number <= 2000:
        raise ValueError('LOGBAR_INTEGER_OUT_OF_RANGE')
    if fractional_digits not in (1, 2):
        raise ValueError('LOGBAR_INVALID_PRECISION')
    scale = 10 ** fractional_digits
    q = int(math.floor(math.log1p(abs(number)) * scale + 0.5))
    return -q if number < 0 else q


def encode_quantized(signed_q, fractional_digits=1):
    if fractional_digits not in (1, 2):
        raise ValueError('LOGBAR_INVALID_PRECISION')
    q = abs(signed_q)
    scale = 10 ** fractional_digits
    major, remainder = divmod(q, scale)
    result = '|' * major + '.' + '|' * (remainder // (10 if fractional_digits == 2 else 1))
    if fractional_digits == 2:
        result += ':' + '|' * (remainder % 10)
    return ('-' if signed_q < 0 else '') + result


def encode(number, fractional_digits=1):
    return encode_quantized(quantize(number, fractional_digits), fractional_digits)


def recover_estimate(signed_q, fractional_digits=1):
    estimate = math.expm1(abs(signed_q) / (10 ** fractional_digits))
    return -estimate if signed_q < 0 else estimate


def number_bank(values, precision):
    """Return entries per decimal n and unique log-bar code bank."""
    groups = {}
    rows = []
    for n in values:
        q = quantize(n, precision)
        code = encode_quantized(q, precision)
        groups.setdefault(q, []).append(n)
        rows.append({'number': n, 'signed_quantized_log': q,
                     'logbar': code, 'decimal_prompt': dec.prompt_for(n),
                     'logbar_prompt': dec.PREFIX + code})
    for row in rows:
        q = row['signed_quantized_log']
        row['collision_group_size'] = len(groups[q])
        row['estimated_value'] = recover_estimate(q, precision)
        row['absolute_error'] = abs(row['number'] - row['estimated_value'])
    return rows, groups


def record_path(root, kind, key):
    if kind == 'decimal':
        return root / 'records' / f'decimal_{key:+06d}.npz'
    if kind == 'logbar':
        return root / 'records' / f'logbar_{key:+06d}.npz'
    raise ValueError('LOGBAR_BAD_KIND')


def capture_prompt(torch, model, tokenizer, prompt, device, max_prompt_tokens):
    import numpy as np
    ids, indices = dec.numeric_indices(tokenizer, prompt)
    if len(ids) > max_prompt_tokens:
        raise RuntimeError('LOGBAR_PROMPT_EXCEEDS_TOKEN_BUDGET')
    tokens = torch.tensor([ids], dtype=torch.long, device=device)
    with torch.inference_mode():
        output = model(input_ids=tokens, attention_mask=torch.ones_like(tokens),
                       use_cache=False, output_hidden_states=True)
        if not output.hidden_states:
            raise RuntimeError('LOGBAR_HIDDEN_STATES_MISSING')
        vectors = torch.stack([
            torch.stack([state[0, indices[-1], :],
                         state[0, indices, :].mean(dim=0)], dim=0)
            for state in output.hidden_states
        ], dim=0).float().cpu().numpy()
    if vectors.ndim != 3 or vectors.shape[1] != 2 or not np.isfinite(vectors).all():
        raise RuntimeError('LOGBAR_BAD_VECTORS')
    return {'prompt': prompt, 'prompt_sha256': dec.digest(prompt),
            'input_ids': ids, 'numeric_token_indices': indices,
            'numeric_token_count': len(indices), 'input_token_count': len(ids),
            'shape': list(vectors.shape)}, vectors


def read_record(path, expected_prompt, identity):
    import numpy as np
    if not path.exists():
        raise RuntimeError('LOGBAR_MISSING_RECORD:' + str(path))
    with np.load(path, allow_pickle=False) as archive:
        metadata = json.loads(str(archive['metadata'].item()))
        vectors = archive['vectors'].astype(np.float32)
    if (metadata.get('prompt') != expected_prompt
            or metadata.get('prompt_sha256') != dec.digest(expected_prompt)
            or metadata.get('model_identity') != identity):
        raise RuntimeError('LOGBAR_RECORD_DRIFT:' + str(path))
    if metadata.get('shape') != list(vectors.shape) or not np.isfinite(vectors).all():
        raise RuntimeError('LOGBAR_RECORD_CORRUPT:' + str(path))
    return metadata, vectors


def analyze(root, rows, groups, identity, precision):
    import numpy as np
    # Distinct log-bar codes are used once in geometry calculations. Duplicated
    # numbers are joined in the mapping CSV rather than overweighting clusters.
    bank = {
        'decimal': [(n['number'], n['decimal_prompt'], n['number']) for n in rows],
        'logbar': [(q, dec.PREFIX + encode_quantized(q, precision),
                    float(sum(members) / len(members)))
                   for q, members in sorted(groups.items())]
    }
    stats = []
    coord_rows = []
    for kind, entries in bank.items():
        arrays, metas = [], []
        for key, prompt, _ in entries:
            meta, vec = read_record(record_path(root, kind, key), prompt, identity)
            arrays.append(vec)
            metas.append(meta)
        tensor = np.stack(arrays, axis=0)
        real_values = np.asarray([item[2] for item in entries], dtype=np.float64)
        log_values = np.sign(real_values) * np.log1p(np.abs(real_values))
        for layer in range(tensor.shape[1]):
            for c, channel in enumerate(CHANNELS):
                vectors = tensor[:, layer, c]
                coords, variance = dec.pca_two(vectors)
                c1 = dec.pearson_features(coords[:, :1], log_values)[0]
                c2 = dec.pearson_features(coords[:, 1:2], log_values)[0]
                row = {'kind': kind, 'layer': layer, 'channel': channel,
                       'unique_prompts': len(entries),
                       'pc1_variance_fraction': variance[0],
                       'pc2_variance_fraction': variance[1],
                       'pc1_corr_signed_log': float(c1),
                       'pc2_corr_signed_log': float(c2),
                       'max_abs_neuron_corr_signed_log': float(np.max(np.abs(
                           dec.pearson_features(vectors, log_values))))}
                stats.append(row)
                for i, (key, prompt, representative) in enumerate(entries):
                    coord_rows.append({'kind': kind, 'key': key, 'representative_number': representative,
                                       'layer': layer, 'channel': channel,
                                       'pc1': float(coords[i, 0]), 'pc2': float(coords[i, 1]),
                                       'numeric_token_count': metas[i]['numeric_token_count']})
    for row in rows:
        meta, _ = read_record(record_path(root, 'logbar', row['signed_quantized_log']),
                              row['logbar_prompt'], identity)
        row['logbar_numeric_token_count'] = meta['numeric_token_count']
        row['logbar_input_token_count'] = meta['input_token_count']
    dec.write_csv(root / 'number_mapping.csv', rows, list(rows[0]))
    dec.write_csv(root / 'layer_metrics.csv', stats, list(stats[0]))
    dec.write_csv(root / 'pca_coordinates.csv', coord_rows, list(coord_rows[0]))
    summary = {'schema': SCHEMA, 'number_count': len(rows), 'min': min(r['number'] for r in rows),
               'max': max(r['number'] for r in rows), 'unique_logbar_codes': len(groups),
               'decimal_captures': len(rows), 'logbar_captures': len(groups),
               'collisions': len(rows) - len(groups), 'fractional_digits': precision,
               'model_identity': identity, 'channels': list(CHANNELS),
               'interpretation': ('Log-bar token geometry is geometry of an explicitly encoded logarithm, '
                                  'not evidence the model independently inferred numerical magnitude. '
                                  'Colliding codes cannot distinguish their original numbers.')}
    dec.write_json(root / 'summary.json', summary)
    dec.emit('logbar_analysis_complete', root=str(root), numbers=len(rows),
             unique_logbar_codes=len(groups), collisions=len(rows)-len(groups),
             layers=len(set(row['layer'] for row in stats)))
    return summary


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--self-test', action='store_true')
    p.add_argument('--prepare-only', action='store_true')
    p.add_argument('--analyze-only', action='store_true')
    p.add_argument('--resume', action='store_true')
    p.add_argument('--model', choices=tuple(cassette.SOURCE), default='qwen')
    p.add_argument('--device', choices=('auto', 'cpu', 'cuda'), default='auto')
    p.add_argument('--dtype', choices=('auto', 'float32', 'float16', 'bfloat16'), default='auto')
    p.add_argument('--allow-model-download', action='store_true')
    p.add_argument('--fractional-digits', choices=(1, 2), type=int, default=1)
    p.add_argument('--limit', type=int, default=0, help='balanced small-run limit; 0=253')
    p.add_argument('--max-prompt-tokens', type=int, default=128)
    p.add_argument('--report-every', type=int, default=20)
    p.add_argument('--output-root', type=Path, default=DEFAULT_ROOT)
    return p


def run(args, loader=cassette.load_source):
    values = grid()
    if args.limit:
        if not 1 <= args.limit <= len(values):
            raise ValueError('LOGBAR_BAD_LIMIT')
        values = sorted(sorted(values, key=lambda n: (abs(n), n))[:args.limit])
    if args.report_every < 1 or args.max_prompt_tokens < 8:
        raise ValueError('LOGBAR_BAD_ARGUMENT')
    rows, groups = number_bank(values, args.fractional_digits)
    settings = {'schema': SCHEMA, 'model': args.model,
                'model_source': cassette.SOURCE[args.model][0],
                'model_revision': cassette.SOURCE[args.model][1],
                'values': values, 'fractional_digits': args.fractional_digits,
                'channels': list(CHANNELS), 'device': args.device, 'dtype': args.dtype,
                'max_prompt_tokens': args.max_prompt_tokens}
    if args.prepare_only:
        dec.emit('logbar_plan', numbers=len(rows), unique_logbar_codes=len(groups),
                 duplicated_numbers=len(rows)-len(groups),
                 examples=[{'number': n, 'code': encode(n, args.fractional_digits)}
                           for n in (-2000, -1000, -100, -10, -1, 0, 1, 10, 100, 1000, 2000)],
                 model_loaded=False)
        return settings
    root = args.output_root.expanduser().resolve()
    manifest = root / 'manifest.json'
    if manifest.exists():
        if json.loads(manifest.read_text(encoding='utf-8')) != settings:
            raise RuntimeError('LOGBAR_MANIFEST_DRIFT')
        if not (args.resume or args.analyze_only):
            raise RuntimeError('LOGBAR_OUTPUT_EXISTS_USE_RESUME')
    elif args.analyze_only:
        raise RuntimeError('LOGBAR_ANALYZE_NO_MANIFEST')
    elif root.exists() and any(root.iterdir()):
        raise RuntimeError('LOGBAR_OUTPUT_DIR_NOT_EMPTY')
    if args.analyze_only:
        identity = json.loads((root / 'model_identity.json').read_text(encoding='utf-8'))
        return analyze(root, rows, groups, identity, args.fractional_digits)
    torch, model, tokenizer, device, model_info = loader(args.model, args)
    try:
        identity = {'schema': SCHEMA, 'model': args.model, 'model_info': model_info}
        if (root / 'model_identity.json').exists():
            if json.loads((root / 'model_identity.json').read_text(encoding='utf-8')) != identity:
                raise RuntimeError('LOGBAR_MODEL_DRIFT')
        root.mkdir(parents=True, exist_ok=True)
        (root / 'records').mkdir(exist_ok=True)
        if not manifest.exists():
            dec.write_json(manifest, settings)
        if not (root / 'model_identity.json').exists():
            dec.write_json(root / 'model_identity.json', identity)
        tasks = ([('decimal', row['number'], row['decimal_prompt']) for row in rows]
                 + [('logbar', q, dec.PREFIX + encode_quantized(q, args.fractional_digits))
                    for q in sorted(groups)])
        for i, (kind, key, prompt) in enumerate(tasks):
            path = record_path(root, kind, key)
            if path.exists():
                if not args.resume:
                    raise RuntimeError('LOGBAR_UNEXPECTED_RECORD')
                read_record(path, prompt, identity)
            else:
                metadata, vec = capture_prompt(torch, model, tokenizer, prompt,
                                               device, args.max_prompt_tokens)
                dec.save_record(path, {**metadata, 'model_identity': identity}, vec)
            if (i+1) % args.report_every == 0 or i+1 == len(tasks):
                dec.emit('logbar_capture_progress', completed=i+1, total=len(tasks), kind=kind)
        return analyze(root, rows, groups, identity, args.fractional_digits)
    finally:
        del model
        if device == 'cuda':
            torch.cuda.empty_cache()


def self_test():
    assert len(grid()) == 253 and grid()[0] == -2000 and grid()[-1] == 2000
    assert encode(2000) == '|||||||.||||||'
    assert encode(-2000) == '-|||||||.||||||'
    assert encode(0) == '.'
    assert encode(2000, 2) == '|||||||.||||||:'
    assert encode(-2) == '-|.|'
    rows, groups = number_bank(grid(), 1)
    assert len(rows) == 253 and len(groups) == 119
    assert encode(1900) == encode(2000)
    assert len(number_bank(grid(), 2)[1]) == 253
    dec.emit('logbar_self_test_passed', numbers=len(rows), distinct_codes=len(groups))


def main(argv=None):
    args = parser().parse_args(argv)
    if args.self_test:
        self_test()
    else:
        run(args)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError) as err:
        dec.emit('logbar_error', error=str(err))
        sys.exit(1)
