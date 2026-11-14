#!/usr/bin/env python3
"""507 signed probes to +/-2,000,000, three encodings, 117-effective-DF decoding.

One frozen model loads and captures decimal, unique natural-log bar, and paired
prompts. Save all raw hidden states for reuse; decode actual signed integers on
the SAME observations with a fixed-capacity linear ridge decoder. This is an
in-sample information-accessibility measurement, NOT a generalization test.

Requires the prior numerate scripts in the same tools directory; it does not
modify or depend on the old experiment outputs.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

import numpy as np

import nanojev_decimal_number_activation_map as dec
import nanojev_logbar_activation_map as lb
import nanojev_numerate_spine_cassette_smoke as cassette

SCHEMA = 'nanojev.multiscale-activation-decoder.v1'
DEFAULT_ROOT = Path.home() / 'NanoJev' / 'runs' / 'multiscale_activation_decoder_v1'
CHANNEL = 'numeric_mean'
PAIRED_CHANNELS = ('numeric_last', 'numeric_mean', 'decimal_last', 'decimal_mean', 'bar_mean')
KINDS = ('decimal', 'logbar', 'paired')
MAX_VALUE = 2_000_000
DEFAULT_DF = 117.0


def positives():
    # Preserve dense probes near zero, extend by successively wider intervals.
    # Final band contains 89 evenly spaced *integer* points, endpoints included.
    # No random selection, and symmetry is exact across sign.
    bands = [list(range(1, 21)), list(range(25, 201, 5)),
             list(range(250, 2001, 50)), list(range(2500, 20001, 500)),
             list(range(25000, 200001, 5000))]
    tail = [(220_000 * (88 - i) + MAX_VALUE * i + 44) // 88 for i in range(89)]
    vals = [number for band in bands for number in band] + tail
    if len(vals) != 253 or vals != sorted(set(vals)) or vals[-1] != MAX_VALUE:
        raise AssertionError('MULTISCALE_GRID_INVARIANT_FAILED')
    return vals


def grid(limit=0):
    pos = positives()
    values = [-number for number in reversed(pos)] + [0] + pos
    if limit:
        if not 3 <= limit <= 507:
            raise ValueError('MULTISCALE_BAD_LIMIT')
        values = sorted(sorted(values, key=lambda n: (abs(n), n))[:limit])
    if len(set(values)) != len(values):
        raise AssertionError('MULTISCALE_DUPLICATED_VALUES')
    return values


def quantize(number, precision=1):
    if type(number) is not int or abs(number) > MAX_VALUE:
        raise ValueError('MULTISCALE_BAD_NUMBER')
    if precision != 1:
        raise ValueError('MULTISCALE_BAD_PRECISION')
    q = int(math.floor(math.log1p(abs(number)) * 10 + .5))
    return -q if number < 0 else q


def encode(number, precision=1):
    return lb.encode_quantized(quantize(number, precision), precision)


def prompt(kind, value, precision=1):
    if kind == 'decimal':
        return dec.prompt_for(value)
    if kind == 'logbar':
        return dec.PREFIX + lb.encode_quantized(value, precision)
    if kind == 'paired':
        return f'{dec.PREFIX}{value}, {encode(value, precision)}'
    raise ValueError('MULTISCALE_BAD_KIND')


def task_bank(values, precision):
    groups = {}
    for n in values:
        groups.setdefault(quantize(n, precision), []).append(n)
    tasks = ([('decimal', n, prompt('decimal', n, precision)) for n in values]
             + [('logbar', q, prompt('logbar', q, precision)) for q in sorted(groups)]
             + [('paired', n, prompt('paired', n, precision)) for n in values])
    return tasks, groups


def record_path(root, kind, key):
    return root / 'records' / f'{kind}_{key:+09d}.npz'


def token_spans(tokenizer, number, precision=1):
    """Paired numeric spans exclude the literal comma/space separator."""
    text = prompt('paired', number, precision)
    coded = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)
    ids, offsets = coded['input_ids'], coded['offset_mapping']
    if ids and isinstance(ids[0], list):
        if len(ids) != 1:
            raise RuntimeError('MULTISCALE_BAD_TOKEN_BATCH')
        ids, offsets = ids[0], offsets[0]
    if not ids or len(ids) != len(offsets):
        raise RuntimeError('MULTISCALE_TOKENIZER_OFFSETS_REQUIRED')
    d_start = len(dec.PREFIX)
    d_end = d_start + len(str(number))
    b_start = d_end + 2

    def subset(start, end):
        found = [i for i, (s, e) in enumerate(offsets) if s < end and e > start]
        if not found or not any(offsets[i][0] <= start < offsets[i][1] for i in found):
            raise RuntimeError('MULTISCALE_TOKEN_SPAN_START_MISSING')
        if not any(offsets[i][0] < end <= offsets[i][1] for i in found):
            raise RuntimeError('MULTISCALE_TOKEN_SPAN_END_MISSING')
        return found

    d = subset(d_start, d_end)
    b = subset(b_start, len(text))
    if set(d) & set(b) or d[-1] >= b[0] or b[-1] != len(ids) - 1:
        raise RuntimeError('MULTISCALE_TOKEN_SPANS_OVERLAP')
    return text, list(map(int, ids)), d, b


def capture_paired(torch, model, tokenizer, number, device, max_tokens, precision):
    text, ids, d, b = token_spans(tokenizer, number, precision)
    if len(ids) > max_tokens:
        raise RuntimeError('MULTISCALE_PROMPT_EXCEEDS_TOKEN_LIMIT')
    tokens = torch.tensor([ids], dtype=torch.long, device=device)
    with torch.inference_mode():
        res = model(input_ids=tokens, attention_mask=torch.ones_like(tokens),
                    output_hidden_states=True, use_cache=False)
        if not res.hidden_states:
            raise RuntimeError('MULTISCALE_NO_HIDDEN_STATES')
        joined = d + b
        vectors = torch.stack([
            torch.stack([layer[0, joined[-1]], layer[0, joined].mean(0),
                         layer[0, d[-1]], layer[0, d].mean(0), layer[0, b].mean(0)], 0)
            for layer in res.hidden_states], 0).float().cpu().numpy()
    if vectors.ndim != 3 or vectors.shape[1] != len(PAIRED_CHANNELS) or not np.isfinite(vectors).all():
        raise RuntimeError('MULTISCALE_INVALID_ACTIVATIONS')
    meta = {'prompt': text, 'prompt_sha256': dec.digest(text), 'number': number,
            'input_ids': ids, 'decimal_token_indices': d, 'bar_token_indices': b,
            'decimal_token_count': len(d), 'bar_token_count': len(b),
            'input_token_count': len(ids), 'shape': list(vectors.shape)}
    return meta, vectors


def read_record(root, kind, key, expected_prompt, identity):
    path = record_path(root, kind, key)
    if not path.is_file():
        raise RuntimeError(f'MULTISCALE_RECORD_MISSING:{path}')
    with np.load(path, allow_pickle=False) as archive:
        meta = json.loads(str(archive['metadata'].item()))
        vectors = archive['vectors'].astype(np.float32)
    if (meta.get('prompt') != expected_prompt
            or meta.get('prompt_sha256') != dec.digest(expected_prompt)
            or meta.get('model_identity') != identity
            or meta.get('shape') != list(vectors.shape)
            or vectors.ndim != 3
            or vectors.shape[1] != (5 if kind == 'paired' else 2)
            or not np.isfinite(vectors).all()):
        raise RuntimeError(f'MULTISCALE_RECORD_DRIFT:{path}')
    return meta, vectors


def matrix_bank(root, values, precision, identity, layers):
    arrays = {kind: [] for kind in KINDS}
    for n in values:
        q = quantize(n, precision)
        for kind, key in (('decimal', n), ('logbar', q), ('paired', n)):
            _, vec = read_record(root, kind, key, prompt(kind, key, precision), identity)
            if max(layers) >= vec.shape[0]:
                raise RuntimeError('MULTISCALE_LAYER_OUT_OF_RANGE')
            arrays[kind].append(vec[layers, 1, :])  # numeric_mean for all three representations
    arrays = {kind: np.stack(chunks, 0).astype(np.float64) for kind, chunks in arrays.items()}
    if len({item.shape for item in arrays.values()}) != 1:
        raise RuntimeError('MULTISCALE_ACTIVATION_SHAPE_MISMATCH')
    return arrays


def ridge_fit(x, y, df):
    """Affine ridge with a fixed effective-DF budget, using smaller Gram matrix."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    if x.ndim != 2 or y.ndim != 1 or len(x) != len(y) or len(y) < 3:
        raise ValueError('MULTISCALE_BAD_DECODER_DATA')
    if not np.isfinite(x).all() or not np.isfinite(y).all() or not 0 < df < len(y)-1:
        raise ValueError('MULTISCALE_BAD_DECODER_BUDGET')
    xm, ym = x.mean(0), y.mean()
    xc, yc = x-xm, y-ym
    by_rows = xc.shape[0] <= xc.shape[1]
    gram = (xc @ xc.T) if by_rows else (xc.T @ xc)
    s, v = np.linalg.eigh(gram)
    s = np.maximum(s, 0)
    tol = max(float(s[-1]) * 1e-11, 1e-12)
    good = s > tol
    rank = int(good.sum())
    if rank == 0:
        coeffs = np.zeros(x.shape[1], dtype=float)
        return np.full_like(y, ym), {'effective_df':0., 'rank':0, 'capacity_capped':True,
                                     'ridge_lambda':None}, {'weights':coeffs,'intercept':float(ym)}
    evals = s[good]
    target = min(df, rank * .99)
    lo, hi = 0., float(evals[-1])
    while float(np.sum(evals / (evals + hi))) > target:
        hi *= 2.
    for _ in range(64):
        mid = (lo+hi)/2
        if float(np.sum(evals/(evals+mid))) > target:
            lo = mid
        else:
            hi = mid
    lam = hi
    if by_rows:
        projected = v[:, good].T @ yc
        coeffs = xc.T @ (v[:, good] @ (projected / (evals+lam)))
    else:
        projected = v[:, good].T @ (xc.T @ yc)
        coeffs = v[:, good] @ (projected / (evals+lam))
    intercept = float(ym - xm @ coeffs)
    predictions = x @ coeffs + intercept
    return predictions, {'effective_df':float(np.sum(evals/(evals+lam))),
                         'rank':rank, 'capacity_capped':bool(df > .99*rank + 1e-9),
                         'ridge_lambda':float(lam)}, {'weights':coeffs, 'intercept':intercept}


def error_metrics(real, predicted):
    y, p = np.asarray(real, dtype=float), np.asarray(predicted, dtype=float)
    delta = p-y
    denominator = np.sum((y-y.mean())**2)
    sign_mask = y != 0
    return {'mae':float(np.mean(np.abs(delta))),
            'rmse':float(np.sqrt(np.mean(delta*delta))),
            'r2':float(1-np.sum(delta*delta)/denominator) if denominator else 0.,
            'max_abs_error':float(np.max(np.abs(delta))),
            'wape':float(np.sum(np.abs(delta))/np.sum(np.abs(y))) if np.any(y) else 0.,
            'median_abs_percentage_error':float(np.median(np.abs(delta[sign_mask])/np.abs(y[sign_mask]))) if np.any(sign_mask) else 0.,
            'sign_accuracy_nonzero':float(np.mean(np.sign(y[sign_mask]) == np.sign(p[sign_mask]))) if np.any(sign_mask) else 0.}


def collision_floor(values, precision):
    groups = {}
    for value in values:
        groups.setdefault(quantize(int(value), precision), []).append(float(value))
    mae_fit = {q: float(np.median(v)) for q,v in groups.items()}
    rmse_fit = {q: float(np.mean(v)) for q,v in groups.items()}
    return {'unique_codes': len(groups), 'collisions': len(values)-len(groups),
            'mae_floor':float(np.mean([abs(x-mae_fit[quantize(int(x), precision)]) for x in values])),
            'rmse_floor':float(np.sqrt(np.mean([(x-rmse_fit[quantize(int(x), precision)])**2 for x in values])))}


def magnitude_band(n):
    val = abs(n)
    if val <= 20: return '0-20'
    if val <= 200: return '25-200'
    if val <= 2000: return '250-2000'
    if val <= 20000: return '2500-20000'
    if val <= 200000: return '25000-200000'
    return '220000-2000000'


def analyze(root, values, precision, identity, layers, df):
    if not 0 < df < len(values)-1:
        raise ValueError('MULTISCALE_DF_EXCEEDS_SAMPLE_COUNT')
    arrays = matrix_bank(root, values, precision, identity, layers)
    y = np.asarray(values, dtype=float)
    metrics_rows, prediction_rows, models = [], [], {}
    for idx, layer in enumerate(layers):
        matrix = {kind: arrays[kind][:, idx, :] for kind in KINDS}
        matrix['shared_all'] = np.concatenate([matrix[k] for k in KINDS], axis=0)
        targets = {kind: y for kind in KINDS}
        targets['shared_all'] = np.tile(y, 3)
        for kind in (*KINDS, 'shared_all'):
            predicts, info, coefs = ridge_fit(matrix[kind], targets[kind], df)
            models[f'layer_{layer}_{kind}_weights'] = coefs['weights']
            models[f'layer_{layer}_{kind}_intercept'] = np.float64(coefs['intercept'])
            groups = ((kind, y, predicts),) if kind != 'shared_all' else (
                ('shared_all', targets[kind], predicts),
                *((f'shared_on_{r}', y, predicts[k*len(y):(k+1)*len(y)]) for k, r in enumerate(KINDS)))
            for name, actual, got in groups:
                metrics_rows.append({'layer': layer, 'representation':name, 'channel': CHANNEL,
                                     'samples':len(actual), 'df_budget': df, **info,
                                     **error_metrics(actual, got)})
                if name == 'shared_all':
                    continue
                for n, prediction in zip(y, got):
                    prediction_rows.append({'layer':layer, 'representation':name,
                                            'number':int(n), 'magnitude_band':magnitude_band(n),
                                            'prediction':float(prediction), 'error':float(prediction-n),
                                            'abs_error':float(abs(prediction-n)),
                                            'relative_error':float(abs(prediction-n)/max(1., abs(n)))})
    mapping = []
    _, groups = task_bank(values, precision)
    for n in values:
        q = quantize(n, precision)
        mapping.append({'number': n, 'magnitude_band':magnitude_band(n),
                        'quantized_log':q, 'logbar':encode(n, precision),
                        'collision_group_size':len(groups[q]),
                        'decimal_prompt':prompt('decimal', n, precision),
                        'logbar_prompt':prompt('logbar', q, precision),
                        'paired_prompt':prompt('paired', n, precision)})
    dec.write_csv(root / 'number_mapping.csv', mapping, list(mapping[0]))
    dec.write_csv(root / 'fit_metrics.csv', metrics_rows, list(metrics_rows[0]))
    dec.write_csv(root / 'predictions.csv', prediction_rows, list(prediction_rows[0]))
    band_rows = []
    for layer in layers:
        for kind in (*KINDS, *(f'shared_on_{name}' for name in KINDS)):
            subset = [r for r in prediction_rows if r['layer']==layer and r['representation']==kind]
            for band in dict.fromkeys(r['magnitude_band'] for r in subset):
                b = [r for r in subset if r['magnitude_band']==band]
                band_rows.append({'layer':layer, 'representation':kind, 'magnitude_band':band,
                                  'samples':len(b), 'mae':float(np.mean([r['abs_error'] for r in b])),
                                  'median_relative_error':float(np.median([r['relative_error'] for r in b]))})
    dec.write_csv(root / 'band_metrics.csv', band_rows, list(band_rows[0]))
    np.savez_compressed(root/'decoder_models.npz', **models)
    output = {'schema':SCHEMA, 'metric_scope':'in_sample_only',
              'objective':'reconstruct actual signed integer x from frozen-model activations',
              'number_count':len(values), 'value_min':int(y.min()), 'value_max':int(y.max()),
              'model_identity':identity, 'effective_df_requested':df, 'layers':layers,
              'channels':{'decimal':CHANNEL, 'logbar':CHANNEL, 'paired':CHANNEL},
              'collision_floor':collision_floor(values, precision),
              'results':{str(layer):{r['representation']:{k:r[k] for k in
                           ('mae','rmse','r2','wape','median_abs_percentage_error','sign_accuracy_nonzero','effective_df','rank','capacity_capped')}
                           for r in metrics_rows if r['layer']==layer} for layer in layers},
              'interpretation':'Same activations used for training and evaluation; no generalization claim. '
                               'MAE/RMSE measure signed integers in original units. WAPE and per-band metrics '
                               'permit comparison across 6 orders of magnitude. Log-Bar is quantized and collides.'}
    dec.write_json(root/'summary.json', output)
    dec.emit('multiscale_decoder_complete', root=str(root), numbers=len(values),
             effective_df=df, unique_logbar_codes=output['collision_floor']['unique_codes'],
             layers=layers, results=output['results'])
    return output


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--self-test', action='store_true')
    p.add_argument('--prepare-only', action='store_true', help='Print grid plan; no model loaded')
    p.add_argument('--analyze-only', action='store_true', help='Decode saved records; no model loaded')
    p.add_argument('--resume', action='store_true')
    p.add_argument('--model', choices=tuple(cassette.SOURCE), default='qwen')
    p.add_argument('--device', choices=('auto','cpu','cuda'), default='auto')
    p.add_argument('--dtype', choices=('auto','float32','float16','bfloat16'), default='auto')
    p.add_argument('--allow-model-download', action='store_true')
    p.add_argument('--fractional-digits', type=int, choices=(1,), default=1)
    p.add_argument('--effective-df', type=float, default=DEFAULT_DF)
    p.add_argument('--layers', default='16,28')
    p.add_argument('--report-every', type=int, default=20)
    p.add_argument('--max-prompt-tokens', type=int, default=128)
    p.add_argument('--output-root', type=Path, default=DEFAULT_ROOT)
    p.add_argument('--limit', type=int, default=0, help='Small symmetric smoke; default 507')
    return p


def run(args, loader=cassette.load_source):
    values = grid(args.limit)
    if not math.isfinite(args.effective_df) or not 0 < args.effective_df < len(values)-1:
        raise ValueError('MULTISCALE_INVALID_EFFECTIVE_DF')
    if args.report_every < 1 or args.max_prompt_tokens < 8:
        raise ValueError('MULTISCALE_BAD_ARGUMENT')
    try:
        layers = sorted({int(item.strip()) for item in args.layers.split(',') if item.strip()})
    except ValueError as exc:
        raise ValueError('MULTISCALE_BAD_LAYERS') from exc
    if not layers or min(layers)<0:
        raise ValueError('MULTISCALE_BAD_LAYERS')
    tasks, groups = task_bank(values, args.fractional_digits)
    settings = {'schema': SCHEMA, 'model': args.model,
                'model_source':cassette.SOURCE[args.model][0],
                'model_revision':cassette.SOURCE[args.model][1],
                'values':values, 'fractional_digits':args.fractional_digits,
                'df_default':DEFAULT_DF, 'device':args.device, 'dtype':args.dtype,
                'max_prompt_tokens':args.max_prompt_tokens,
                'templates':{'decimal':dec.PREFIX+'{n}',
                             'logbar':dec.PREFIX+'{encoded_log}',
                             'paired':dec.PREFIX+'{n}, {encoded_log}'}}
    if args.prepare_only:
        dec.emit('multiscale_plan', numbers=len(values), range=[values[0], values[-1]],
                 decimal=len(values), unique_logbar=len(groups), paired=len(values),
                 total_evaluations=len(tasks), effective_df=args.effective_df,
                 example_prompts=[prompt('paired', n) for n in (-MAX_VALUE,-2000,-1,0,1,2000,MAX_VALUE)])
        return settings
    root = args.output_root.expanduser().resolve()
    mp = root / 'manifest.json'
    if mp.is_file():
        if json.loads(mp.read_text(encoding='utf-8')) != settings:
            raise RuntimeError('MULTISCALE_MANIFEST_DRIFT')
        if not (args.resume or args.analyze_only):
            raise RuntimeError('MULTISCALE_OUTPUT_EXISTS_USE_RESUME')
    elif args.analyze_only:
        raise RuntimeError('MULTISCALE_MISSING_MANIFEST')
    elif root.exists() and any(root.iterdir()):
        raise RuntimeError('MULTISCALE_OUTPUT_DIR_NOT_EMPTY')
    if args.analyze_only or (args.resume and mp.is_file() and all(
            record_path(root, kind, key).is_file() for kind,key,_ in tasks)):
        identity = json.loads((root/'model_identity.json').read_text(encoding='utf-8'))
        return analyze(root, values, args.fractional_digits, identity, layers, args.effective_df)
    torch, model, tokenizer, device, info = loader(args.model, args)
    try:
        identity = {'schema':SCHEMA,'model':args.model, 'model_info':info}
        if (root/'model_identity.json').is_file() and json.loads(
                (root/'model_identity.json').read_text(encoding='utf-8')) != identity:
            raise RuntimeError('MULTISCALE_MODEL_DRIFT')
        (root/'records').mkdir(parents=True, exist_ok=True)
        if not mp.is_file():
            dec.write_json(mp, settings)
        if not (root/'model_identity.json').is_file():
            dec.write_json(root/'model_identity.json', identity)
        for index, (kind, key, text) in enumerate(tasks, 1):
            path = record_path(root, kind, key)
            if path.is_file():
                if not args.resume:
                    raise RuntimeError('MULTISCALE_UNEXPECTED_RECORD')
                read_record(root, kind, key, text, identity)
            elif kind == 'paired':
                m, v = capture_paired(torch, model, tokenizer, key, device,
                                      args.max_prompt_tokens, args.fractional_digits)
                dec.save_record(path, {**m, 'model_identity':identity}, v)
            else:
                m, v = lb.capture_prompt(torch, model, tokenizer, text, device,
                                         args.max_prompt_tokens)
                dec.save_record(path, {**m, 'model_identity':identity}, v)
            if index % args.report_every == 0 or index == len(tasks):
                dec.emit('multiscale_capture_progress', completed=index,
                         total=len(tasks), kind=kind)
        return analyze(root, values, args.fractional_digits, identity, layers, args.effective_df)
    finally:
        del model
        if device == 'cuda':
            torch.cuda.empty_cache()


def self_test():
    assert len(grid()) == 507 and grid()[0] == -MAX_VALUE and grid()[-1] == MAX_VALUE
    assert len(positives()) == 253 and len(set(positives())) == 253
    assert encode(2000) == '|||||||.||||||'
    assert encode(2_000_000) == '||||||||||||||.|||||'
    assert encode(0) == '.'
    assert encode(-2_000_000) == '-||||||||||||||.|||||'
    assert len(task_bank(grid(), 1)[1]) == 239
    dec.emit('multiscale_self_test_passed', numbers=507, effective_df=DEFAULT_DF)


def main(argv=None):
    args = parser().parse_args(argv)
    if args.self_test:
        self_test()
    else:
        run(args)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, FileNotFoundError, KeyError) as exc:
        dec.emit('multiscale_error', message=str(exc))
        sys.exit(1)
