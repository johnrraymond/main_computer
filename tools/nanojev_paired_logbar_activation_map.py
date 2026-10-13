#!/usr/bin/env python3
"""Capture paired decimal+log-bar prompts on the prior -2000..2000 grid.

One forward pass per number: "Number: 2000, |||||||.||||||".
Compare against the prior *frozen* decimal and log-bar records rather than
rerunning either baseline. No arithmetic, generation, parameter updates or search.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import nanojev_decimal_number_activation_map as dec
import nanojev_logbar_activation_map as lb
import nanojev_numerate_spine_cassette_smoke as cassette

SCHEMA = 'nanojev.paired-logbar-activation-map.v1'
CHANNELS = ('numeric_last', 'numeric_mean', 'decimal_last', 'decimal_mean', 'bar_mean')
DEFAULT_ROOT = Path.home() / 'NanoJev' / 'runs' / 'paired_logbar_activation_map_v1'
DEFAULT_BASELINE = Path.home() / 'NanoJev' / 'runs' / 'logbar_activation_map_v1'


def paired_prompt(number, fractional_digits=1):
    if type(number) is not int:
        raise ValueError('PAIRED_INTEGER_REQUIRED')
    return f'{dec.PREFIX}{number}, {lb.encode(number, fractional_digits)}'


def record_path(root, number):
    return root / 'records' / f'paired_{number:+06d}.npz'


def token_spans(tokenizer, number, fractional_digits=1):
    prompt = paired_prompt(number, fractional_digits)
    encoded = tokenizer(prompt, add_special_tokens=False, return_offsets_mapping=True)
    ids, offsets = encoded['input_ids'], encoded['offset_mapping']
    if ids and isinstance(ids[0], list):
        if len(ids) != 1:
            raise RuntimeError('PAIRED_UNEXPECTED_BATCH')
        ids, offsets = ids[0], offsets[0]
    if not ids or len(ids) != len(offsets):
        raise RuntimeError('PAIRED_TOKEN_OFFSETS_MISSING')
    n_start = len(dec.PREFIX)
    n_end = n_start + len(str(number))
    b_start = n_end + 2  # comma, then space
    b_end = len(prompt)

    def select(start, end):
        indices = [i for i, (s, e) in enumerate(offsets) if s < end and e > start]
        if not indices or not any(offsets[i][0] <= start < offsets[i][1] for i in indices):
            raise RuntimeError('PAIRED_SPAN_START_MISSING')
        if not any(offsets[i][0] < end <= offsets[i][1] for i in indices):
            raise RuntimeError('PAIRED_SPAN_END_MISSING')
        return indices

    decimal_indices = select(n_start, n_end)
    bar_indices = select(b_start, b_end)
    if (set(decimal_indices) & set(bar_indices) or
            decimal_indices[-1] >= bar_indices[0] or bar_indices[-1] != len(ids) - 1):
        raise RuntimeError('PAIRED_TOKEN_SPANS_OVERLAP_OR_MISALIGN')
    both = decimal_indices + bar_indices
    return prompt, list(map(int, ids)), decimal_indices, bar_indices, both


def capture(torch, model, tokenizer, number, device, max_prompt_tokens, fractional_digits=1):
    import numpy as np
    prompt, ids, decimal_indices, bar_indices, both = token_spans(tokenizer, number, fractional_digits)
    if len(ids) > max_prompt_tokens:
        raise RuntimeError('PAIRED_TOKEN_BUDGET_EXCEEDED')
    tokens = torch.tensor([ids], dtype=torch.long, device=device)
    with torch.inference_mode():
        result = model(input_ids=tokens, attention_mask=torch.ones_like(tokens),
                       output_hidden_states=True, use_cache=False)
        if not result.hidden_states:
            raise RuntimeError('PAIRED_MISSING_HIDDEN_STATES')
        vectors = torch.stack([
            torch.stack([
                state[0, both[-1], :],
                state[0, both, :].mean(dim=0),
                state[0, decimal_indices[-1], :],
                state[0, decimal_indices, :].mean(dim=0),
                state[0, bar_indices, :].mean(dim=0),
            ], dim=0)
            for state in result.hidden_states
        ], dim=0).float().cpu().numpy()
    if vectors.ndim != 3 or vectors.shape[1] != len(CHANNELS) or not np.isfinite(vectors).all():
        raise RuntimeError('PAIRED_BAD_VECTOR_SHAPE')
    metadata = {'number': number, 'prompt': prompt, 'prompt_sha256': dec.digest(prompt),
                'input_ids': ids, 'decimal_token_indices': decimal_indices,
                'bar_token_indices': bar_indices, 'numeric_token_indices': both,
                'decimal_token_count': len(decimal_indices), 'bar_token_count': len(bar_indices),
                'input_token_count': len(ids), 'shape': list(vectors.shape)}
    return metadata, vectors


def read_paired(root, number, identity, fractional_digits=1):
    import numpy as np
    path = record_path(root, number)
    if not path.is_file():
        raise RuntimeError(f'PAIRED_RECORD_MISSING:{path}')
    with np.load(path, allow_pickle=False) as obj:
        meta = json.loads(str(obj['metadata'].item()))
        vec = obj['vectors'].astype(np.float32)
    prompt = paired_prompt(number, fractional_digits)
    if (meta.get('number') != number or meta.get('prompt') != prompt or
            meta.get('prompt_sha256') != dec.digest(prompt) or
            meta.get('model_identity') != identity):
        raise RuntimeError(f'PAIRED_RECORD_DRIFT:{path}')
    if meta.get('shape') != list(vec.shape) or vec.shape[1] != len(CHANNELS) or not np.isfinite(vec).all():
        raise RuntimeError(f'PAIRED_RECORD_BAD_VECTOR:{path}')
    return meta, vec


def baseline_identity(baseline_root, model_identity, values, fractional_digits):
    mp = baseline_root / 'manifest.json'
    ip = baseline_root / 'model_identity.json'
    if not mp.is_file() or not ip.is_file():
        raise RuntimeError(f'PAIRED_BASELINE_MISSING: {baseline_root} (need prior logbar run with raw records)')
    manifest = json.loads(mp.read_text(encoding='utf-8'))
    previous = json.loads(ip.read_text(encoding='utf-8'))
    if manifest.get('schema') != lb.SCHEMA or manifest.get('fractional_digits') != fractional_digits:
        raise RuntimeError('PAIRED_BASELINE_SCHEMA_OR_PRECISION_MISMATCH')
    if not set(values).issubset(set(manifest.get('values', []))):
        raise RuntimeError('PAIRED_BASELINE_GRID_MISMATCH')
    if previous.get('model') != model_identity.get('model') or previous.get('model_info') != model_identity.get('model_info'):
        raise RuntimeError('PAIRED_BASELINE_MODEL_MISMATCH')
    return previous


def magnitude_corr(coords, numbers, sign):
    import numpy as np
    n = np.asarray(numbers, dtype=np.float64)
    m = n > 0 if sign == 'positive' else n < 0
    if m.sum() < 3:
        return 0.0
    logabs = np.log1p(np.abs(n[m]))
    return float(np.max(np.abs(dec.pearson_features(coords[m, :2], logabs))))


def profile(vectors, numbers):
    import numpy as np
    vals = np.asarray(numbers, dtype=np.float64)
    log = np.sign(vals) * np.log1p(np.abs(vals))
    coords, variance = dec.pca_two(vectors)
    return {
        'pc1_variance_fraction': variance[0],
        'pc2_variance_fraction': variance[1],
        'abs_pc1_corr_signed_log': abs(float(dec.pearson_features(coords[:, :1], log)[0])),
        'abs_pc1_corr_sign': abs(float(dec.pearson_features(coords[:, :1], np.sign(vals))[0])),
        'max_abs_neuron_corr_signed_log': float(np.max(np.abs(dec.pearson_features(vectors, log)))),
        'max_abs_pc12_corr_logmag_positive': magnitude_corr(coords, vals, 'positive'),
        'max_abs_pc12_corr_logmag_negative': magnitude_corr(coords, vals, 'negative'),
    }


def ranked(data):
    import numpy as np
    data = np.asarray(data, dtype=np.float64)
    _, inverse, counts = np.unique(data, return_inverse=True, return_counts=True)
    ends = counts.cumsum()
    starts = ends - counts
    return ((starts + ends - 1) / 2.0)[inverse]


def spearman(a, b):
    import numpy as np
    if len(a) < 3:
        return 0.0
    x, y = ranked(a), ranked(b)
    x -= x.mean()
    y -= y.mean()
    den = np.linalg.norm(x) * np.linalg.norm(y)
    return float(x @ y / den) if den > 1e-12 else 0.0


def pairwise_cosine_distances(vectors):
    import numpy as np
    centered = vectors.astype(np.float64) - vectors.mean(axis=0)
    norm = np.maximum(np.linalg.norm(centered, axis=1, keepdims=True), 1e-12)
    unit = centered / norm
    return 1.0 - unit @ unit.T


def analyze(root, baseline_root, values, identity, fractional_digits):
    import numpy as np
    baseline_id = baseline_identity(baseline_root, identity, values, fractional_digits)
    meta = []
    paired = []
    decimal = []
    logbar = []
    for n in values:
        m, vectors = read_paired(root, n, identity, fractional_digits)
        meta.append(m)
        paired.append(vectors)
        _, d = lb.read_record(lb.record_path(baseline_root, 'decimal', n), dec.prompt_for(n), baseline_id)
        _, b = lb.read_record(lb.record_path(baseline_root, 'logbar', lb.quantize(n, fractional_digits)),
                              dec.PREFIX + lb.encode(n, fractional_digits), baseline_id)
        decimal.append(d)
        logbar.append(b)
    paired, decimal, logbar = map(np.stack, (paired, decimal, logbar))
    if paired.shape[:2] != decimal.shape[:2] or paired.shape[1] != logbar.shape[1] or paired.shape[-1] != decimal.shape[-1] or paired.shape[-1] != logbar.shape[-1]:
        raise RuntimeError('PAIRED_BASELINE_ACTIVATION_SHAPE_MISMATCH')

    rows, groups = lb.number_bank(values, fractional_digits)
    # One actual integer per unique Log-Bar code; never overweight collisions.
    representatives = []
    for q, members in sorted(groups.items()):
        mean = sum(members) / len(members)
        representative = min(members, key=lambda n: (abs(n-mean), n))
        representatives.append(values.index(representative))
    matched_n = [values[i] for i in representatives]
    source = {
        'decimal': (decimal, dec.CHANNELS),
        'logbar': (logbar, dec.CHANNELS),
        'paired': (paired, CHANNELS),
    }
    layer_rows = []
    for cohort, indices in [('all_numbers', list(range(len(values)))), ('unique_logbar', representatives)]:
        for kind in ('decimal', 'logbar', 'paired'):
            if kind == 'logbar' and cohort == 'all_numbers':
                continue  # avoid counting repeated log-bar prompts as independent observations
            block, channels = source[kind]
            numbers = [values[i] for i in indices]
            for layer in range(paired.shape[1]):
                for c, channel in enumerate(channels):
                    entry = {'cohort': cohort, 'kind': kind, 'channel': channel,
                             'layer': layer, 'sample_count': len(indices)}
                    entry.update(profile(block[indices, layer, c, :], numbers))
                    layer_rows.append(entry)

    align_rows = []
    matched = {
        'decimal': decimal[representatives],
        'logbar': logbar[representatives],
        'paired': paired[representatives],
    }
    numbers = np.asarray(matched_n)
    tri = np.triu_indices(len(numbers), 1)
    same_sign = np.sign(numbers[tri[0]]) == np.sign(numbers[tri[1]])
    for layer in range(paired.shape[1]):
        for c, channel in enumerate(dec.CHANNELS):
            distances = {kind: pairwise_cosine_distances(block[:, layer, c, :])[tri]
                         for kind, block in matched.items()}
            for first, second in (('decimal', 'paired'), ('logbar', 'paired'), ('decimal', 'logbar')):
                align_rows.append({'layer': layer, 'channel': channel,
                                   'first': first, 'second': second,
                                   'matched_numbers': len(numbers),
                                   'all_pair_count': len(distances[first]),
                                   'same_sign_pair_count': int(same_sign.sum()),
                                   'all_pairs_distance_spearman': spearman(distances[first], distances[second]),
                                   'same_sign_distance_spearman': spearman(distances[first][same_sign], distances[second][same_sign])})

    mapping = []
    for row, m in zip(rows, meta):
        mapping.append({'number': row['number'], 'logbar': row['logbar'],
                        'signed_quantized_log': row['signed_quantized_log'],
                        'collision_group_size': row['collision_group_size'],
                        'paired_prompt': m['prompt'],
                        'paired_input_token_count': m['input_token_count'],
                        'decimal_token_count': m['decimal_token_count'],
                        'bar_token_count': m['bar_token_count']})
    dec.write_csv(root / 'number_mapping.csv', mapping, list(mapping[0]))
    dec.write_csv(root / 'layer_metrics.csv', layer_rows, list(layer_rows[0]))
    dec.write_csv(root / 'geometry_alignment.csv', align_rows, list(align_rows[0]))
    selected_layers = sorted({min(16, paired.shape[1] - 1), paired.shape[1] - 1})
    highlights = {}
    for layer in selected_layers:
        matched_metrics = {
            row['kind']: {
                'pc1_signed_log_abs_corr': row['abs_pc1_corr_signed_log'],
                'pc1_sign_abs_corr': row['abs_pc1_corr_sign'],
                'max_neuron_signed_log_abs_corr': row['max_abs_neuron_corr_signed_log'],
                'pc12_positive_log_magnitude_abs_corr': row['max_abs_pc12_corr_logmag_positive'],
                'pc12_negative_log_magnitude_abs_corr': row['max_abs_pc12_corr_logmag_negative'],
            }
            for row in layer_rows
            if row['layer'] == layer and row['cohort'] == 'unique_logbar'
            and row['channel'] == 'numeric_mean'
        }
        within_sign_geometry = {
            f"{row['first']}_vs_{row['second']}": row['same_sign_distance_spearman']
            for row in align_rows if row['layer'] == layer and row['channel'] == 'numeric_mean'
        }
        highlights[str(layer)] = {'matched_numbers': len(representatives),
                                  'mean_pooled': matched_metrics,
                                  'within_sign_geometry': within_sign_geometry}
    dec.write_json(root / 'headline_comparison.json', highlights)
    summary = {'schema': SCHEMA, 'numbers': len(values),
               'model_identity': identity, 'baseline_root': str(baseline_root),
               'unique_logbar_codes': len(groups), 'collisions_in_bar_only': len(values)-len(groups),
               'unique_paired_prompts': len(values), 'precision_digits': fractional_digits,
               'channels': list(CHANNELS), 'activation_shape': list(paired.shape),
               'comparison': ('Layer metrics compare each representation; geometry_alignment.csv '
                              'correlates pairwise activation distances on one representative '
                              'per Log-Bar code, with within-sign controls.'),
               'caution': ('Paired prompts expose the answer in decimal and an explicitly encoded '
                           'logarithm. Stronger correlation alone cannot demonstrate a shared '
                           'numerical mechanism; tokenizer and surface-pattern controls remain necessary.')}
    dec.write_json(root / 'summary.json', summary)
    dec.emit('paired_analysis_complete', root=str(root), numbers=len(values),
             unique_paired_prompts=len(values), matched_unique_logbar=len(groups),
             layers=paired.shape[1], channels=list(CHANNELS), compared_against_prior_baseline=True)
    dec.emit('paired_comparison_summary', layers=highlights, filename='headline_comparison.json')
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
    p.add_argument('--limit', type=int, default=0)
    p.add_argument('--report-every', type=int, default=20)
    p.add_argument('--max-prompt-tokens', type=int, default=128)
    p.add_argument('--baseline-root', type=Path, default=DEFAULT_BASELINE)
    p.add_argument('--output-root', type=Path, default=DEFAULT_ROOT)
    return p


def run(args, loader=cassette.load_source):
    values = lb.grid()
    if args.limit:
        if not 1 <= args.limit <= len(values):
            raise ValueError('PAIRED_BAD_LIMIT')
        values = sorted(sorted(values, key=lambda n: (abs(n), n))[:args.limit])
    if args.report_every < 1 or args.max_prompt_tokens < 8:
        raise ValueError('PAIRED_BAD_ARGUMENT')
    baseline = args.baseline_root.expanduser().resolve()
    settings = {'schema': SCHEMA, 'model': args.model,
                'model_source': cassette.SOURCE[args.model][0],
                'model_revision': cassette.SOURCE[args.model][1],
                'values': values, 'fractional_digits': args.fractional_digits,
                'channels': list(CHANNELS), 'device': args.device,
                'dtype': args.dtype, 'max_prompt_tokens': args.max_prompt_tokens,
                'baseline_root': str(baseline), 'template': dec.PREFIX + '{decimal}, {logbar}'}
    if args.prepare_only:
        dec.emit('paired_plan', numbers=len(values), captures=len(values),
                 examples=[paired_prompt(n, args.fractional_digits)
                           for n in (-2000, -100, -1, 0, 1, 100, 2000)], model_loaded=False)
        return settings
    root = args.output_root.expanduser().resolve()
    mp = root / 'manifest.json'
    if mp.exists():
        if json.loads(mp.read_text(encoding='utf-8')) != settings:
            raise RuntimeError('PAIRED_MANIFEST_DRIFT')
        if not (args.resume or args.analyze_only):
            raise RuntimeError('PAIRED_OUTPUT_EXISTS_USE_RESUME')
    elif args.analyze_only:
        raise RuntimeError('PAIRED_ANALYZE_NO_MANIFEST')
    elif root.exists() and any(root.iterdir()):
        raise RuntimeError('PAIRED_OUTPUT_DIR_NOT_EMPTY')

    if args.analyze_only:
        identity = json.loads((root / 'model_identity.json').read_text(encoding='utf-8'))
        return analyze(root, baseline, values, identity, args.fractional_digits)

    torch, model, tokenizer, device, info = loader(args.model, args)
    try:
        identity = {'schema': SCHEMA, 'model': args.model, 'model_info': info}
        # Fail before capturing if the prior baseline is absent or a different model.
        baseline_identity(baseline, identity, values, args.fractional_digits)
        if (root / 'model_identity.json').exists():
            existing_id = json.loads((root / 'model_identity.json').read_text(encoding='utf-8'))
            if existing_id != identity:
                raise RuntimeError('PAIRED_MODEL_DRIFT')
        (root / 'records').mkdir(parents=True, exist_ok=True)
        if not mp.exists():
            dec.write_json(mp, settings)
        if not (root / 'model_identity.json').exists():
            dec.write_json(root / 'model_identity.json', identity)
        for i, n in enumerate(values):
            path = record_path(root, n)
            if path.exists():
                if not args.resume:
                    raise RuntimeError('PAIRED_UNEXPECTED_RECORD')
                read_paired(root, n, identity, args.fractional_digits)
            else:
                m, v = capture(torch, model, tokenizer, n, device, args.max_prompt_tokens, args.fractional_digits)
                dec.save_record(path, {**m, 'model_identity': identity}, v)
            if (i+1) % args.report_every == 0 or i+1 == len(values):
                dec.emit('paired_capture_progress', completed=i+1, total=len(values), number=n)
        return analyze(root, baseline, values, identity, args.fractional_digits)
    finally:
        del model
        if device == 'cuda':
            torch.cuda.empty_cache()


def self_test():
    assert len(lb.grid()) == 253
    assert paired_prompt(2000) == 'Number: 2000, |||||||.||||||'
    assert paired_prompt(-2000) == 'Number: -2000, -|||||||.||||||'
    assert paired_prompt(0) == 'Number: 0, .'
    assert len({paired_prompt(n) for n in lb.grid()}) == 253
    dec.emit('paired_self_test_passed', numbers=253)


def main(argv=None):
    args = parser().parse_args(argv)
    if args.self_test:
        self_test()
    else:
        run(args)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError) as e:
        dec.emit('paired_error', message=str(e))
        sys.exit(1)
