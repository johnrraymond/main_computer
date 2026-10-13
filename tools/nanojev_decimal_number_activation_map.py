#!/usr/bin/env python3
"""Map frozen language-model activations for a nonuniform decimal number grid.

Each number receives one prompt, "Number: <integer>". There is no arithmetic,
completion scoring, parameter update, or configuration search. Every hidden
layer is captured at the final numerical token and the mean of all numerical
tokens. Output stores raw vectors plus unsupervised geometry summaries.

The observations are descriptive: correlation or PCA alone does not establish
that any learned mechanism is performing arithmetic or representing magnitude.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import sys

import nanojev_numerate_spine_cassette_smoke as cassette

SCHEMA = 'nanojev.decimal-activation-map.v1'
PREFIX = 'Number: '
DEFAULT_ROOT = Path.home() / 'NanoJev' / 'runs' / 'decimal_number_activation_map_v1'
CHANNELS = ('numeric_last', 'numeric_mean')


def canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def digest(data):
    if isinstance(data, str):
        data = data.encode('utf-8')
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def write_csv(path, rows, columns):
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


def emit(event, **values):
    print(canonical({'event': event, **values}), flush=True)


def grid():
    """213 symmetric values, denser near zero; no stochastic selection."""
    positives = (list(range(1, 21)) + list(range(25, 201, 5))
                 + list(range(210, 501, 10)) + list(range(525, 1001, 25)))
    values = [-n for n in reversed(positives)] + [0] + positives
    if len(values) != 213 or len(set(values)) != len(values):
        raise AssertionError('DECIMAL_GRID_INVARIANT_FAILED')
    return values


def prompt_for(number):
    if not isinstance(number, int):
        raise ValueError('DECIMAL_NUMBER_MUST_BE_INTEGER')
    return f'{PREFIX}{number}'


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--self-test', action='store_true', help='offline tests of grid, pooling, and geometry; no model download')
    p.add_argument('--prepare-only', action='store_true', help='print the selected numbers and prompt examples; no model load')
    p.add_argument('--analyze-only', action='store_true', help='recompute geometry from stored records; no model load')
    p.add_argument('--model', choices=tuple(cassette.SOURCE), default='qwen')
    p.add_argument('--device', choices=('auto', 'cpu', 'cuda'), default='auto')
    p.add_argument('--dtype', choices=('auto', 'float32', 'float16', 'bfloat16'), default='auto')
    p.add_argument('--allow-model-download', action='store_true')
    p.add_argument('--output-root', type=Path, default=DEFAULT_ROOT)
    p.add_argument('--limit', type=int, default=0, help='for a short smoke: first N balanced/ordered values, 0=all 213')
    p.add_argument('--resume', action='store_true')
    p.add_argument('--report-every', type=int, default=10)
    p.add_argument('--max-prompt-tokens', type=int, default=128)
    return p


def manifest_data(args, numbers):
    return {'schema': SCHEMA, 'model': args.model,
            'model_source': cassette.SOURCE[args.model][0],
            'model_revision': cassette.SOURCE[args.model][1],
            'numbers': numbers, 'prompt_template': PREFIX + '{n}',
            'channels': list(CHANNELS), 'pooling': 'last numerical token; mean numerical tokens',
            'max_prompt_tokens': args.max_prompt_tokens, 'dtype': args.dtype,
            'device': args.device}


def numeric_indices(tokenizer, prompt):
    """Resolve actual numeric token overlap, including the minus sign."""
    encoded = tokenizer(prompt, add_special_tokens=False, return_offsets_mapping=True)
    ids = encoded['input_ids']
    offsets = encoded['offset_mapping']
    if ids and isinstance(ids[0], list):
        if len(ids) != 1:
            raise RuntimeError('DECIMAL_UNEXPECTED_BATCH')
        ids, offsets = ids[0], offsets[0]
    if len(ids) != len(offsets):
        raise RuntimeError('DECIMAL_OFFSET_LENGTH_MISMATCH')
    start, end = len(PREFIX), len(prompt)
    selected = [i for i, (s, e) in enumerate(offsets) if s < end and e > start]
    if not ids or not selected or selected[-1] != len(ids) - 1:
        raise RuntimeError('DECIMAL_NUMERIC_TOKEN_ALIGNMENT_FAILED')
    if not any(s <= start < e for s, e in (offsets[i] for i in selected)):
        raise RuntimeError('DECIMAL_NUMERIC_SPAN_START_MISSING')
    if not any(s < end <= e for s, e in (offsets[i] for i in selected)):
        raise RuntimeError('DECIMAL_NUMERIC_SPAN_END_MISSING')
    return list(map(int, ids)), selected


def capture(torch, model, tokenizer, number, device, max_prompt_tokens):
    import numpy as np
    prompt = prompt_for(number)
    ids, indices = numeric_indices(tokenizer, prompt)
    if len(ids) > max_prompt_tokens:
        raise RuntimeError('DECIMAL_PROMPT_EXCEEDS_TOKEN_BUDGET')
    tokens = torch.tensor([ids], dtype=torch.long, device=device)
    with torch.inference_mode():
        out = model(input_ids=tokens, attention_mask=torch.ones_like(tokens),
                    use_cache=False, output_hidden_states=True)
        if not out.hidden_states:
            raise RuntimeError('DECIMAL_MODEL_DID_NOT_RETURN_HIDDEN_STATES')
        vectors = torch.stack([
            torch.stack([state[0, indices[-1], :],
                         state[0, indices, :].mean(dim=0)], dim=0)
            for state in out.hidden_states
        ], dim=0).float().cpu().numpy()
    if vectors.ndim != 3 or vectors.shape[1] != len(CHANNELS) or not np.isfinite(vectors).all():
        raise RuntimeError('DECIMAL_BAD_ACTIVATIONS')
    metadata = {'number': number, 'prompt': prompt, 'prompt_sha256': digest(prompt),
                'input_ids': ids, 'input_ids_sha256': digest(canonical(ids)),
                'numeric_token_indices': indices, 'numeric_token_count': len(indices),
                'input_token_count': len(ids), 'shape': list(vectors.shape)}
    return metadata, vectors


def record_path(root, index):
    return root / 'records' / f'{index:04d}.npz'


def save_record(path, data, vectors):
    import numpy as np
    tmp = path.with_suffix('.tmp.npz')
    np.savez_compressed(tmp, metadata=canonical(data), vectors=vectors.astype(np.float32))
    os.replace(tmp, path)


def load_record(path, number, model_identity):
    import numpy as np
    with np.load(path, allow_pickle=False) as obj:
        meta = json.loads(str(obj['metadata'].item()))
        vectors = obj['vectors'].astype(np.float32)
    if meta.get('number') != number or meta.get('prompt') != prompt_for(number):
        raise RuntimeError(f'DECIMAL_RECORD_NUMBER_DRIFT:{path}')
    if meta.get('model_identity') != model_identity:
        raise RuntimeError(f'DECIMAL_RECORD_MODEL_DRIFT:{path}')
    if meta.get('prompt_sha256') != digest(prompt_for(number)):
        raise RuntimeError(f'DECIMAL_RECORD_PROMPT_DRIFT:{path}')
    if list(vectors.shape) != meta.get('shape') or not np.isfinite(vectors).all():
        raise RuntimeError(f'DECIMAL_RECORD_VECTOR_CORRUPTION:{path}')
    return meta, vectors


def pearson_features(x, y):
    """Return per-neuron Pearson correlations; avoid nonfinite constants."""
    import numpy as np
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    xc, yc = x - x.mean(0), y - y.mean()
    den = np.linalg.norm(xc, axis=0) * np.linalg.norm(yc)
    out = np.zeros(x.shape[1], dtype=np.float64)
    np.divide(yc @ xc, den, out=out, where=den > 1e-12)
    return out


def pca_two(x):
    """Two principal axes via sample-space Gram eigenvectors; no learned labels."""
    import numpy as np
    centered = np.asarray(x, dtype=np.float64) - np.asarray(x, dtype=np.float64).mean(axis=0)
    if len(centered) < 3 or not np.any(centered):
        return np.zeros((len(centered), 2)), [0.0, 0.0]
    gram = centered @ centered.T
    eigenvalues, eigenvectors = np.linalg.eigh(gram)
    top = np.argsort(eigenvalues)[-2:][::-1]
    val = np.maximum(0.0, eigenvalues[top])
    coords = eigenvectors[:, top] * np.sqrt(val)
    # Sign is arbitrary; fix it to make CSV outputs deterministic.
    for j in range(2):
        if coords[np.argmax(np.abs(coords[:, j])), j] < 0:
            coords[:, j] *= -1
    total = max(float(np.trace(gram)), 1e-20)
    return coords, [float(v / total) for v in val]


def neighborhood_metrics(x, values):
    """Centered-cosine nearest-neighbor distance, with numeric null baseline."""
    import numpy as np
    matrix = np.asarray(x, dtype=np.float64)
    matrix = matrix - matrix.mean(axis=0)
    unit = matrix / np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-12)
    sims = unit @ unit.T
    np.fill_diagonal(sims, -np.inf)
    neighbor = np.argmax(sims, axis=1)
    values = np.asarray(values, dtype=np.float64)
    gaps = np.abs(values - values[neighbor])
    pair_gaps = np.abs(values[:, None] - values[None, :])
    return float(np.median(gaps)), float(np.median(pair_gaps[np.triu_indices(len(values), 1)])), int(np.count_nonzero(gaps == 0))


def analyze(root, numbers, model_identity):
    import numpy as np
    metas = []
    arrays = []
    for index, number in enumerate(numbers):
        path = record_path(root, index)
        if not path.exists():
            raise RuntimeError(f'DECIMAL_RECORD_MISSING:{path}')
        meta, vectors = load_record(path, number, model_identity)
        metas.append(meta)
        arrays.append(vectors)
    raw = np.stack(arrays)
    if raw.ndim != 4 or raw.shape[2] != len(CHANNELS):
        raise RuntimeError('DECIMAL_INCONSISTENT_VECTOR_SHAPES')
    values = np.asarray(numbers, dtype=np.float64)
    signed_log = np.sign(values) * np.log1p(np.abs(values))
    magnitude = np.abs(values)
    numerics = []
    for idx, meta in enumerate(metas):
        numerics.append({'index': idx, 'number': numbers[idx],
                         'input_token_count': meta['input_token_count'],
                         'numeric_token_count': meta['numeric_token_count'],
                         'input_ids': canonical(meta['input_ids']),
                         'numeric_token_indices': canonical(meta['numeric_token_indices'])})
    write_csv(root / 'numbers.csv', numerics,
              ['index', 'number', 'input_token_count', 'numeric_token_count', 'input_ids', 'numeric_token_indices'])
    layer_rows, positions = [], []
    for layer in range(raw.shape[1]):
        for channel_i, channel in enumerate(CHANNELS):
            vectors = raw[:, layer, channel_i, :]
            coords, variance = pca_two(vectors)
            nr_med, null_med, _ = neighborhood_metrics(vectors, values)
            cv = pearson_features(vectors, values)
            cl = pearson_features(vectors, signed_log)
            cm = pearson_features(vectors, magnitude)
            row = {
                'layer': layer, 'channel': channel,
                'pc1_variance_fraction': variance[0],
                'pc2_variance_fraction': variance[1],
                'max_abs_neuron_corr_value': float(np.max(np.abs(cv))),
                'max_abs_neuron_corr_signed_log': float(np.max(np.abs(cl))),
                'max_abs_neuron_corr_magnitude': float(np.max(np.abs(cm))),
                'pc1_corr_value': float(pearson_features(coords[:, :1], values)[0]),
                'pc1_corr_signed_log': float(pearson_features(coords[:, :1], signed_log)[0]),
                'pc2_corr_value': float(pearson_features(coords[:, 1:2], values)[0]),
                'pc2_corr_signed_log': float(pearson_features(coords[:, 1:2], signed_log)[0]),
                'median_nearest_activation_neighbor_numeric_gap': nr_med,
                'median_all_pairs_numeric_gap': null_med,
            }
            layer_rows.append(row)
            for i, number in enumerate(numbers):
                positions.append({'number': number, 'layer': layer, 'channel': channel,
                                  'pc1': float(coords[i, 0]), 'pc2': float(coords[i, 1]),
                                  'numeric_token_count': metas[i]['numeric_token_count']})
    write_csv(root / 'layer_metrics.csv', layer_rows, list(layer_rows[0]))
    write_csv(root / 'pca_coordinates.csv', positions, list(positions[0]))
    report = {'schema': SCHEMA, 'number_count': len(numbers), 'min': min(numbers), 'max': max(numbers),
              'activation_shape': list(raw.shape), 'channels': list(CHANNELS),
              'model_identity': model_identity, 'layer_metrics_count': len(layer_rows),
              'raw_records': 'records/*.npz', 'numbers_csv': 'numbers.csv',
              'layer_metrics_csv': 'layer_metrics.csv', 'pca_coordinates_csv': 'pca_coordinates.csv',
              'interpretation': ('Correlations and PCA are descriptive and can reflect numeral-tokenization '
                                 'and prompt-format artifacts. They do not demonstrate arithmetic or '
                                 'model-independent numerical understanding.')}
    write_json(root / 'summary.json', report)
    emit('decimal_analysis_complete', root=str(root), numbers=len(numbers),
         layers=int(raw.shape[1]), channels=list(CHANNELS), shape=list(raw.shape))
    return report


def run(args, loader=cassette.load_source):
    values = grid()
    if args.limit:
        if not (1 <= args.limit <= len(values)):
            raise ValueError('DECIMAL_INVALID_LIMIT')
        # Preserve coverage and sign symmetry in smoke runs, rather than taking
        # just the most negative values from the ordered grid.
        values = sorted(values, key=lambda n: (abs(n), n))[:args.limit]
        values.sort()
    if args.max_prompt_tokens < 8 or args.report_every < 1:
        raise ValueError('DECIMAL_INVALID_ARGUMENT')
    settings = manifest_data(args, values)
    if args.prepare_only:
        emit('decimal_plan', sample_count=len(values), min=min(values), max=max(values),
             prompts=[prompt_for(n) for n in (values[:3] + [0] + values[-3:])],
             grid=values, model_loaded=False)
        return
    root = args.output_root.expanduser().resolve()
    settings_path = root / 'manifest.json'
    if settings_path.exists():
        existing = json.loads(settings_path.read_text(encoding='utf-8'))
        if existing != settings:
            raise RuntimeError('DECIMAL_MANIFEST_DRIFT')
        if not (args.resume or args.analyze_only):
            raise RuntimeError('DECIMAL_OUTPUT_EXISTS_USE_RESUME')
    elif args.analyze_only:
        raise RuntimeError('DECIMAL_ANALYZE_REQUIRES_EXISTING_MANIFEST')
    elif root.exists() and any(root.iterdir()):
        raise RuntimeError('DECIMAL_OUTPUT_DIRECTORY_NOT_EMPTY')
    if args.analyze_only:
        model_identity = json.loads((root / 'model_identity.json').read_text(encoding='utf-8'))
        return analyze(root, values, model_identity)
    torch, model, tokenizer, device, info = loader(args.model, args)
    try:
        model_identity = {'schema': SCHEMA, 'model': args.model, 'model_info': info}
        id_path = root / 'model_identity.json'
        if id_path.exists():
            if json.loads(id_path.read_text(encoding='utf-8')) != model_identity:
                raise RuntimeError('DECIMAL_MODEL_IDENTITY_DRIFT')
        else:
            root.mkdir(parents=True, exist_ok=True)
            (root / 'records').mkdir(exist_ok=True)
            write_json(id_path, model_identity)
        if not settings_path.exists():
            write_json(settings_path, settings)
        (root / 'records').mkdir(exist_ok=True)
        for index, n in enumerate(values):
            path = record_path(root, index)
            if path.exists():
                if not args.resume:
                    raise RuntimeError('DECIMAL_UNEXPECTED_EXISTING_RECORD')
                load_record(path, n, model_identity)
            else:
                metadata, vectors = capture(torch, model, tokenizer, n, device, args.max_prompt_tokens)
                save_record(path, {**metadata, 'model_identity': model_identity}, vectors)
            if (index + 1) % args.report_every == 0 or index + 1 == len(values):
                emit('decimal_capture_progress', completed=index + 1, total=len(values), number=n)
        return analyze(root, values, model_identity)
    finally:
        del model
        if device == 'cuda':
            torch.cuda.empty_cache()


def self_test():
    import numpy as np
    a = grid()
    assert len(a) == 213 and a[0] == -1000 and a[-1] == 1000
    assert all((-n in a) for n in a)
    assert a.count(0) == 1 and list(range(-20, 21)) == [n for n in a if -20 <= n <= 20]
    numbers = np.asarray([-3, -2, -1, 0, 1, 2, 3], dtype=np.float64)
    xyz = np.column_stack([numbers, numbers ** 2, np.ones_like(numbers)])
    assert abs(pearson_features(xyz, numbers)[0] - 1.0) < 1e-12
    assert pearson_features(xyz, numbers)[2] == 0
    pcs, explained = pca_two(xyz)
    assert pcs.shape == (7, 2) and sum(explained) > 0.99999
    neighbor, baseline, _ = neighborhood_metrics(xyz, numbers)
    assert neighbor >= 0 and baseline > 0
    emit('decimal_self_test_passed', cases=len(a))


def main(argv=None):
    args = parser().parse_args(argv)
    if args.self_test:
        self_test()
        return
    run(args)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError) as exc:
        emit('decimal_error', error=str(exc))
        sys.exit(1)
