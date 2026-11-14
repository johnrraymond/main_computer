"""Read-only G1 null-boundary diagnosis on original and fresh development splits.

This is an exploratory diagnostic, NOT a calibration selection procedure, a
stage gate, or evidence of independent out-of-distribution generalization.
All biases are declared in advance and evaluated with frozen neural weights.
No gold spans enter inference or candidate selection; gold is used only for
post-decoding attribution and recovery/false-positive accounting.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path

import torch

from tools.nanojev_gamer_v1_g1_decoder_frob import (
    _candidate_scores, _char_set, decode_whole_span_scores,
)
from tools.nanojev_gamer_v1_g1_fresh_dev import (
    CASES, SEED, DECODER, SCHEMA as FRESH_SCHEMA, _original_prompts,
    _select_arms, fresh_rows as regenerate_fresh_rows,
)
from tools.nanojev_gamer_v1_g1_decoder_transfer import _preflight
from tools.nanojev_gamer_v1_graph_comparison import _stats
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS
from tools.nanojev_gamer_v1_graph_head import token_span_char_bounds

SCHEMA = 'nanojev.gamer.graph.g1.null-boundary-diagnostic.v1'
# Predeclared, NOT optimized on labels. Each lowers the null logit by this
# constant. The 0.0 arm must exactly reproduce the frozen linear decoder.
NULL_LOGIT_SHIFTS = (0.0, 0.5, 1.0, 2.0, 4.0, 8.0)
DECODER_LENGTH_POWER = 1.0
DECODER_PER_OBJECT_PENALTY = 0.0


def _sha(path):
    from tools import nanojev_gamer_v1_backbone_first as backbone
    return backbone.sha256(Path(path))


def _load_reference(root, transfer_report, fresh_run, output):
    """Bind fresh prompts, prior ranking, adapter and both heads before capture."""
    root, transfer_report, fresh_run, output = map(Path,
        (root, transfer_report, fresh_run, output))
    if output.exists():
        raise FileExistsError('GAMER_G1_NULL_IMMUTABLE_OUTPUT')
    dataset, dataset_sha, adapter_sha, entries = _preflight(root, output)
    transfer_sha = _sha(transfer_report)
    transfer = json.loads(transfer_report.read_text(encoding='utf-8'))
    selected, _ = _select_arms(transfer, entries, dataset_sha, adapter_sha)
    fresh_path = fresh_run / 'fresh_dev.jsonl'
    report_path = fresh_run / 'fresh_report.json'
    case_path = fresh_run / 'per_case.jsonl'
    report = json.loads(report_path.read_text(encoding='utf-8'))
    # Fresh report lineage hashes canonical LF text, but write_text() on
    # Windows may have serialized CRLF bytes. Capture BOTH representations
    # from one byte snapshot to avoid confusing newline translation with
    # an input modification during inference.
    fresh_bytes = fresh_path.read_bytes()
    text = fresh_bytes.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')
    fresh_sha = hashlib.sha256(text.encode('utf-8')).hexdigest()
    fresh_file_sha = hashlib.sha256(fresh_bytes).hexdigest()
    if (report.get('schema') != FRESH_SCHEMA
            or report.get('status') != 'FRESH_WITHIN_GENERATOR_DEVELOPMENT_ONLY_NOT_CERTIFICATION'
            or report.get('original_dataset_sha256') != dataset_sha
            or report.get('source_adapter_sha256') != adapter_sha
            or report.get('transfer_report_sha256') != transfer_sha
            or report.get('fresh_dataset_sha256') != fresh_sha
            or report.get('seed') != SEED or report.get('cases') != CASES
            or report.get('selected_before_fresh_capture') != list(selected)
            or report.get('head_sha256') != {
                name: entries[name]['manifest']['head_sha256'] for name in selected}
            or report.get('decoder') != DECODER
            or report.get('length_power') != DECODER_LENGTH_POWER
            or report.get('per_object_penalty') != DECODER_PER_OBJECT_PENALTY
            or report.get('no_training') is not True
            or report.get('heads_frozen') is not True
            or report.get('decoder_frozen') is not True
            or report.get('challenge_or_test_split_used') is not False
            or report.get('production_promotion') is not False
            or report.get('g2_unlocked') is not False):
        raise ValueError('GAMER_G1_NULL_FRESH_LINEAGE')
    # Regenerate from the *original* train/dev split, not from the archived
    # report alone. This prevents altered/colliding fresh labels or prompts.
    original_fresh = regenerate_fresh_rows(dataset, seed=SEED, cases=CASES)
    canonical_text = ''.join(json.dumps(row, sort_keys=True, ensure_ascii=False) + '\n'
                             for row in original_fresh)
    if text != canonical_text:
        raise ValueError('GAMER_G1_NULL_FRESH_GENERATION_DIVERGED')
    fresh_rows = original_fresh
    case_rows = [json.loads(line) for line in case_path.read_text(encoding='utf-8').splitlines()
                 if line.strip()]
    if (not fresh_rows or len(fresh_rows) != report.get('cases')
            or len(case_rows) != len(fresh_rows)
            or report.get('families') != {
                'encounter': sum(r.get('family') == 'encounter' for r in fresh_rows),
                'allocation': sum(r.get('family') == 'allocation' for r in fresh_rows)}):
        raise ValueError('GAMER_G1_NULL_FRESH_ROW_COUNT')
    old_prompts = _original_prompts(dataset)
    prompt_seen = set()
    for i, (row, case) in enumerate(zip(fresh_rows, case_rows), 1):
        prompt = row.get('prompt')
        digest = hashlib.sha256(prompt.encode('utf-8')).hexdigest() if isinstance(prompt, str) else ''
        if (row.get('split') != 'dev'
                or row.get('family') not in ('encounter', 'allocation')
                or prompt in old_prompts or prompt in prompt_seen
                or case.get('index') != i or case.get('prompt_sha256') != digest
                or case.get('family') != row['family']
                or case.get('gold_count') != len(row.get('object_spans', []))):
            raise ValueError('GAMER_G1_NULL_FRESH_PROMPT_LINEAGE')
        prompt_seen.add(prompt)
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    dev_rows = [r for r in cur.load_rows(dataset) if r['split'] == 'dev']
    if len(dev_rows) != transfer.get('cases') or not dev_rows:
        raise ValueError('GAMER_G1_NULL_ORIGINAL_DEV_COUNT')
    return {
        'dataset': dataset, 'dataset_sha': dataset_sha, 'adapter_sha': adapter_sha,
        'entries': entries, 'selected': selected,
        'transfer': transfer, 'transfer_sha': transfer_sha,
        'fresh_report': report, 'fresh_report_sha': _sha(report_path),
        'fresh_dataset_sha': fresh_sha, 'fresh_dataset_file_sha': fresh_file_sha,
        'fresh_case_sha': _sha(case_path),
        'old_rows': dev_rows, 'fresh_rows': fresh_rows,
    }


def _decode_shift(logits, valid, shift):
    """Inference-only constant bias, no gold-informed filtering."""
    biased = logits.detach().float().clone()
    biased[..., 0] -= shift
    candidates = _candidate_scores(biased, valid, confidence=0.50)
    return decode_whole_span_scores(candidates,
        length_power=DECODER_LENGTH_POWER,
        per_object_penalty=DECODER_PER_OBJECT_PENALTY)


def _binned(value):
    if value < -8: return 'lt_-8'
    if value < -4: return '-8_to_-4'
    if value < -2: return '-4_to_-2'
    if value < -1: return '-2_to_-1'
    if value < 0: return '-1_to_0'
    if value == 0: return 'zero'
    if value <= 1: return '0_to_1'
    if value <= 2: return '1_to_2'
    if value <= 4: return '2_to_4'
    if value <= 8: return '4_to_8'
    return 'gt_8'


def _explain_gold(prediction, prepared, row, base_spans, base_predicted, *, split, case_index, arm):
    """Describe raw logit evidence and decoder competition AFTER decoding.

    The attributions do not change predictions and do not assume that forcing
    an oracle gold span could be done in deployment.
    """
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    aligned = cur.aligned_labels(prepared, row)['spans']
    gold = {(s['start'], s['end'], s['kind']) for s in row['object_spans']}
    if len(aligned) != len(gold):
        raise ValueError('GAMER_G1_NULL_GOLD_ALIGNMENT')
    logits = prediction['span_logits'].detach().float().cpu()
    valid = prediction['span_valid'].detach().bool().cpu()
    probs = logits.softmax(-1)
    selected = []
    for span in base_spans:
        cb = token_span_char_bounds(span, prepared)
        selected.append((span, (*cb, span['kind']) if cb is not None else None))
    misses = []
    evidence = Counter()
    all_gold = Counter()
    for span in aligned:
        bounds = token_span_char_bounds(span, prepared)
        kind = span['kind']
        if bounds is None or (*bounds, kind) not in gold:
            raise ValueError('GAMER_G1_NULL_UNMAPPABLE_GOLD')
        first, last = span['token_first'], span['token_last']
        all_gold[kind] += 1
        reason = None
        if first < 0 or last < first or first >= valid.shape[0] or last-first >= valid.shape[1]:
            reason = 'span_exceeds_window'
        elif not bool(valid[first, last-first]):
            reason = 'invalid_token_span'
        else:
            scores = logits[first, last-first]
            probability = probs[first, last-first]
            label = SPAN_KINDS.index(kind) + 1
            best_label = int(scores[1:].argmax())+1
            top_label = int(scores.argmax())
            gold_margin = float((scores[label] - scores[0]).item())
            best_margin = float((scores[best_label] - scores[0]).item())
            same_kind = best_label == label
            evidence['gold_margin_'+_binned(gold_margin)] += 1
            evidence['best_margin_'+_binned(best_margin)] += 1
            evidence['best_nonnull_kind_correct' if same_kind else 'best_nonnull_kind_wrong'] += 1
            if (*bounds, kind) not in base_predicted:
                if top_label == 0: reason = 'null_wins'
                elif top_label != label: reason = 'wrong_type'
                elif float(probability[label]) < 0.50: reason = 'below_threshold'
                else: reason = 'linear_decoder_excluded'
        if (*bounds, kind) in base_predicted:
            continue
        record = {'split': split, 'case_index': case_index, 'arm': arm,
                  'family': row['family'], 'kind': kind,
                  'span': list(bounds), 'reason': reason,
                  'prompt_sha256': hashlib.sha256(row['prompt'].encode('utf-8')).hexdigest()}
        if reason not in ('span_exceeds_window','invalid_token_span'):
            record.update({
                'gold_vs_null_margin': round(gold_margin, 6),
                'best_nonnull_vs_null_margin': round(best_margin, 6),
                'best_nonnull_is_correct_kind': same_kind,
                'gold_probability': round(float(probability[label]), 6),
                'null_probability': round(float(probability[0]), 6),
                'required_null_shift_for_positive_gold_margin': round(max(0., -gold_margin), 6),
                'required_null_shift_for_positive_best_margin': round(max(0., -best_margin), 6),
            })
        if reason == 'linear_decoder_excluded':
            conflicts = []
            for selected_span, mapped in selected:
                if (selected_span['token_first'] <= last
                        and selected_span['token_last'] >= first):
                    conflicts.append({'kind': selected_span['kind'],
                        'correct_gold': mapped in gold})
            if not conflicts:
                raise ValueError('GAMER_G1_NULL_EXCLUSION_WITHOUT_CONFLICT')
            record['selected_conflicts'] = conflicts
            record['conflicts_only_false_positives'] = all(not c['correct_gold'] for c in conflicts)
            record['conflicts_any_correct_gold'] = any(c['correct_gold'] for c in conflicts)
        misses.append(record)
    if len(misses) != len(gold - base_predicted):
        raise ValueError('GAMER_G1_NULL_MISS_ACCOUNTING')
    return misses, evidence, all_gold


def _metric(records):
    metric = _stats(records)
    metric['missing_objects'] = metric['expected'] - metric['correct']
    metric['false_positive_objects'] = metric['predicted'] - metric['correct']
    return metric


def _paired_metrics(candidate_records, baseline_records):
    """Compute paired recovery using the exact same subset as the candidate."""
    if len(candidate_records) != len(baseline_records):
        raise ValueError('GAMER_G1_NULL_UNPAIRED_SWEEP')
    recovered = lost = added_fp = removed_fp = 0
    for (pred, gold), (base, prior_gold) in zip(candidate_records, baseline_records):
        if gold != prior_gold:
            raise ValueError('GAMER_G1_NULL_GOLD_DRIFT')
        recovered += len((pred & gold) - base)
        lost += len((base & gold) - pred)
        added_fp += len((pred - gold) - base)
        removed_fp += len((base - gold) - pred)
    return {'newly_recovered_gold': recovered, 'previously_correct_gold_lost': lost,
            'new_false_positives': added_fp, 'old_false_positives_removed': removed_fp}


def _summarize_split(cases, arm):
    base = [case['arms'][arm]['baseline'] for case in cases]
    misses = [miss for case in cases for miss in case['arms'][arm]['misses']]
    evidence = Counter()
    gold = Counter()
    for case in cases:
        evidence.update(case['arms'][arm]['evidence'])
        gold.update(case['arms'][arm]['gold_counts'])
    summary = {'baseline': _metric(base),
               'miss_reasons': dict(sorted(Counter(m['reason'] for m in misses).items())),
               'by_kind_miss_reasons': {k: dict(sorted(Counter(m['reason'] for m in misses if m['kind']==k).items()))
                                        for k in SPAN_KINDS},
               'gold_kind_counts': dict(sorted(gold.items())),
               'raw_evidence': dict(sorted(evidence.items())),
               'null_miss_best_nonnull_kind_correct': sum(
                    m['reason']=='null_wins' and m.get('best_nonnull_is_correct_kind') for m in misses),
               'excluded_only_competing_false_positives': sum(
                    m['reason']=='linear_decoder_excluded' and m['conflicts_only_false_positives'] for m in misses),
               'excluded_conflicts_with_correct_gold': sum(
                    m['reason']=='linear_decoder_excluded' and m['conflicts_any_correct_gold'] for m in misses),
               'shift_sweep': {}}
    if sum(summary['miss_reasons'].values()) != summary['baseline']['missing_objects']:
        raise ValueError('GAMER_G1_NULL_SUMMARY_ACCOUNTING')
    for shift in NULL_LOGIT_SHIFTS:
        included = [case for case in cases if case['arms'][arm]['sweeps'][shift] is not None]
        baseline_subset = [case['arms'][arm]['baseline'] for case in included]
        candidate = [case['arms'][arm]['sweeps'][shift] for case in included]
        comparable = _paired_metrics(candidate, baseline_subset)
        trial = _metric(candidate)
        subset = _metric(baseline_subset)
        summary['shift_sweep'][str(shift)] = {
            'cases': len(included), 'overflow_cases': len(cases)-len(included),
            'complete': len(included) == len(cases),
            'metrics': trial, 'paired_change': comparable,
            'delta_exact_prompts': trial['exact_prompts']-subset['exact_prompts'],
            'delta_correct_objects': trial['correct']-subset['correct'],
            'delta_false_positive_objects': trial['false_positive_objects']-subset['false_positive_objects'],
            'delta_f1': round(trial['f1']-subset['f1'], 9),
            'comparison_scope': 'only cases without decoder overflow; incomplete if overflow_cases > 0',
        }
        if shift == 0.0 and (summary['shift_sweep'][str(shift)]['overflow_cases']
                              or trial != summary['baseline']
                              or any(comparable.values())):
            raise ValueError('GAMER_G1_NULL_ZERO_SHIFT_DIVERGED')
    return summary


def _verify_metrics(actual, expected, label):
    """Fail closed if same frozen decoder no longer reproduces prior result."""
    for key in ('cases', 'expected', 'predicted', 'correct', 'exact_prompts'):
        if key in expected and actual[key] != expected[key]:
            raise ValueError(f'GAMER_G1_NULL_{label}_BASELINE_DIVERGED_{key}')
    for key in ('f1', 'precision', 'recall'):
        if key in expected and not math.isclose(actual[key], expected[key], abs_tol=1e-8):
            raise ValueError(f'GAMER_G1_NULL_{label}_BASELINE_DIVERGED_{key}')


def _verify_unchanged_inputs(info, transfer_report, fresh_run, source_adapter):
    """Fail closed on physical byte changes, regardless of newline convention."""
    from tools import nanojev_gamer_v1_backbone_first as backbone
    if (_sha(transfer_report) != info['transfer_sha']
            or _sha(Path(fresh_run)/'fresh_report.json') != info['fresh_report_sha']
            or _sha(Path(fresh_run)/'fresh_dev.jsonl') != info['fresh_dataset_file_sha']
            or _sha(Path(fresh_run)/'per_case.jsonl') != info['fresh_case_sha']
            or _sha(info['dataset']) != info['dataset_sha']
            or backbone.adapter_fingerprint(source_adapter) != info['adapter_sha']):
        raise ValueError('GAMER_G1_NULL_INPUT_CHANGED_DURING_RUN')


def run(*, root, transfer_report, fresh_run, output, device='cuda', max_length=2048):
    """Read-only two-arm/two-split experiment, emitting one immutable report."""
    from tools import nanojev_gamer_v1_backbone_first as backbone
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    if max_length < 1:
        raise ValueError('GAMER_G1_NULL_INVALID_MAX_LENGTH')
    output = Path(output)
    if output.exists():
        raise FileExistsError('GAMER_G1_NULL_IMMUTABLE_OUTPUT')
    info = _load_reference(root, transfer_report, fresh_run, output)
    selected = info['selected']
    first = info['entries'][selected[0]]['manifest']
    if any(info['entries'][name]['manifest']['source_adapter'] != first['source_adapter']
           or info['entries'][name]['manifest']['model_id'] != first['model_id']
           for name in selected):
        raise ValueError('GAMER_G1_NULL_SOURCE_MODEL_DIVERGED')
    if backbone.adapter_fingerprint(first['source_adapter']) != info['adapter_sha']:
        raise ValueError('GAMER_G1_NULL_ADAPTER_CHANGED')
    heads = {}
    for name in selected:
        head, manifest = cur._load_checkpoint(info['entries'][name]['checkpoint'], device=device)
        if manifest.get('stage') != 'G1' or manifest['head_sha256'] != info['entries'][name]['manifest']['head_sha256']:
            raise ValueError('GAMER_G1_NULL_HEAD_CHANGED')
        head.eval()
        heads[name] = head
    model, tokenizer = backbone.model_components(first['model_id'], first['source_adapter'], device)
    rows_by_split = {'dev': info['old_rows'], 'fresh_dev': info['fresh_rows']}
    summaries = {}
    miss_rows = []
    for split, rows in rows_by_split.items():
        processed = []
        for i, row in enumerate(rows, 1):
            item = cur.capture(model, tokenizer, row, device, max_length)
            prepared = item['prepared']
            hidden = item['hidden'].to(device=device, dtype=torch.float32)
            gold = {(s['start'],s['end'],s['kind']) for s in row['object_spans']}
            case = {'arms': {}}
            for name in selected:
                with torch.inference_mode():
                    prediction = heads[name](hidden, eligible=prepared['eligible'].to(device),
                                              active_stage='G1')
                logits, valid = prediction['span_logits'], prediction['span_valid']
                spans = _decode_shift(logits, valid, 0.0)
                pred = _char_set(spans, prepared)
                misses, evidence, gold_counts = _explain_gold(
                    prediction, prepared, row, spans, pred, split=split, case_index=i, arm=name)
                miss_rows.extend(misses)
                shifts = {0.0: (pred, gold)}
                for shift in NULL_LOGIT_SHIFTS[1:]:
                    try:
                        shifted = _decode_shift(logits, valid, shift)
                        shifts[shift] = (_char_set(shifted, prepared), gold)
                    except ValueError as exc:
                        if str(exc) != 'GAMER_GRAPH_TOO_MANY_PREDICTED_OBJECTS':
                            raise
                        shifts[shift] = None
                case['arms'][name] = {'baseline': (pred, gold), 'misses': misses,
                                     'evidence': evidence, 'gold_counts': gold_counts,
                                     'sweeps': shifts}
            processed.append(case)
            if i == 1 or i % 10 == 0 or i == len(rows):
                print(f'GAMER_G1_NULL_BOUNDARY {split} {i}/{len(rows)}', flush=True)
        summaries[split] = {name: _summarize_split(processed, name) for name in selected}
        for name in selected:
            previous = (info['transfer']['arms'][name]['linear'] if split == 'dev'
                        else info['fresh_report']['fresh_metrics'][name])
            _verify_metrics(summaries[split][name]['baseline'], previous,
                            'DEV' if split == 'dev' else 'FRESH')
    _verify_unchanged_inputs(info, transfer_report, fresh_run, first['source_adapter'])
    report = {
        'schema': SCHEMA, 'status': 'DIAGNOSTIC_ONLY_NOT_CERTIFICATION',
        'arms': list(selected), 'splits': {'dev':len(info['old_rows']), 'fresh_dev':len(info['fresh_rows'])},
        'null_logit_shifts_predeclared': list(NULL_LOGIT_SHIFTS),
        'decoder': DECODER, 'length_power': DECODER_LENGTH_POWER,
        'per_object_penalty': DECODER_PER_OBJECT_PENALTY,
        'dataset_sha256': info['dataset_sha'], 'source_adapter_sha256': info['adapter_sha'],
        'transfer_report_sha256': info['transfer_sha'],
        'fresh_report_sha256': info['fresh_report_sha'],
        'fresh_dataset_sha256': info['fresh_dataset_sha'],
        'fresh_dataset_file_sha256': info['fresh_dataset_file_sha'],
        'head_sha256': {name: info['entries'][name]['manifest']['head_sha256'] for name in selected},
        'metrics': summaries,
        'interpretation': ('A sensitivity analysis on existing and within-generator fresh dev data; '
                           'shifts are uncalibrated probes, not a recommended decoder; '
                           'gold-informed diagnostics are never used in inference'),
        'no_training': True, 'heads_frozen': True, 'original_decoder_unchanged': True,
        'challenge_or_test_split_used': False, 'production_promotion': False,
        'certification_authorized': False, 'g2_unlocked': False,
    }
    output.mkdir(parents=True)
    (output/'null_boundary_report.json').write_text(json.dumps(report, indent=2, sort_keys=True)+'\n', encoding='utf-8')
    (output/'null_boundary_misses.jsonl').write_text(''.join(
        json.dumps(r,sort_keys=True)+'\n' for r in miss_rows), encoding='utf-8')
    return report
