"""G1-0014: one-shot frozen calibration comparison on 320 new dev examples.

The four configurations, generator seed, prompt count, and ranking rule are
locked in source before inspecting validation labels. Original/fresh dev data
were used to nominate these candidates and are NOT reused for scoring.
This is within-generator development replication, not a blind challenge/test,
G1 certification, decoder deployment, G2 unlock, or authorization to train.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import itertools
import json
import math
from pathlib import Path
import random

import torch

from tools.nanojev_gamer_v1_g1_decoder_frob import _char_set
from tools.nanojev_gamer_v1_g1_fresh_dev import _original_prompts
from tools.nanojev_gamer_v1_g1_null_boundary import (
    SCHEMA as NULL_SCHEMA, NULL_LOGIT_SHIFTS, _decode_shift,
    _load_reference, _verify_unchanged_inputs, _verify_metrics,
)
from tools.nanojev_gamer_v1_graph_comparison import _stats
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS

SCHEMA = 'nanojev.gamer.graph.g1.frozen-calibration-validation.v1'
SEED = 174321
CASES = 320
CONFIGURATIONS = (
    ('structured_0', 'structured', 0.0),
    ('structured_2', 'structured', 2.0),
    ('structured_4', 'structured', 4.0),
    ('null_focused_4', 'null_focused', 4.0),
)
FAMILIES = ('encounter', 'allocation')


def _jsonl_bytes(rows):
    return ''.join(json.dumps(row, sort_keys=True, ensure_ascii=False) + '\n'
                   for row in rows).encode('utf-8')


def validation_rows(dataset, prior_fresh_rows, *, seed=SEED, cases=CASES):
    """Seed-disjoint, prompt-disjoint, balanced, newly generated dev-only rows.

    Existing train/dev prompts and the previous 80 fresh prompts are excluded.
    Deliberately never opens challenge/test labels or their sealed surfaces.
    """
    from tools.nanojev_gamer_v1_graph_corpus import encounter, allocation
    if (type(seed) is not int or seed != SEED or type(cases) is not int
            or cases != CASES):
        raise ValueError('GAMER_G1_0014_LOCKED_GENERATION_PARAMETERS')
    used = _original_prompts(dataset)
    previous = set()
    for row in prior_fresh_rows:
        if (not isinstance(row, dict) or row.get('split') != 'dev'
                or row.get('family') not in FAMILIES
                or not isinstance(row.get('prompt'), str)
                or row['prompt'] in used or row['prompt'] in previous):
            raise ValueError('GAMER_G1_0014_INVALID_PRIOR_FRESH_PROMPTS')
        previous.add(row['prompt'])
    used.update(previous)
    rng = random.Random(seed)
    rows = []
    for index in range(cases):
        generator = encounter if index % 2 == 0 else allocation
        for attempt in range(250):
            row = generator(rng, 'dev', index)
            if (row.get('split') == 'dev' and row.get('family') == FAMILIES[index % 2]
                    and isinstance(row.get('prompt'), str)
                    and row['prompt'] not in used):
                break
        else:
            raise ValueError('GAMER_G1_0014_PROMPT_EXCLUSION_EXHAUSTED')
        rows.append(row)
        used.add(row['prompt'])
    if (len(rows) != cases or len({r['prompt'] for r in rows}) != cases
            or any(sum(r['family'] == f for r in rows) != cases//2 for f in FAMILIES)):
        raise ValueError('GAMER_G1_0014_GENERATION_INVARIANT')
    return rows


def _validate_diagnostic(report, info):
    """Reject altered, incomplete, or unrelated G1-0012 selection evidence."""
    selected = info['selected']
    if (tuple(selected) != ('structured', 'null_focused')
            or report.get('schema') != NULL_SCHEMA
            or report.get('status') != 'DIAGNOSTIC_ONLY_NOT_CERTIFICATION'
            or report.get('arms') != list(selected)
            or report.get('splits') != {
                'dev': len(info['old_rows']), 'fresh_dev': len(info['fresh_rows'])}
            or report.get('null_logit_shifts_predeclared') != list(NULL_LOGIT_SHIFTS)
            or report.get('decoder') != 'length_linear'
            or report.get('length_power') != 1.0
            or report.get('per_object_penalty') != 0.0
            or report.get('dataset_sha256') != info['dataset_sha']
            or report.get('source_adapter_sha256') != info['adapter_sha']
            or report.get('transfer_report_sha256') != info['transfer_sha']
            or report.get('fresh_report_sha256') != info['fresh_report_sha']
            or report.get('fresh_dataset_sha256') != info['fresh_dataset_sha']
            or report.get('fresh_dataset_file_sha256') != info['fresh_dataset_file_sha']
            or report.get('head_sha256') != {
                name: info['entries'][name]['manifest']['head_sha256'] for name in selected}
            or report.get('no_training') is not True
            or report.get('heads_frozen') is not True
            or report.get('original_decoder_unchanged') is not True
            or report.get('challenge_or_test_split_used') is not False
            or report.get('production_promotion') is not False
            or report.get('certification_authorized') is not False
            or report.get('g2_unlocked') is not False):
        raise ValueError('GAMER_G1_0014_UNTRUSTED_NULL_DIAGNOSTIC')
    for split in ('dev', 'fresh_dev'):
        for name in selected:
            try:
                arm = report['metrics'][split][name]
                ref = (info['transfer']['arms'][name]['linear'] if split == 'dev'
                       else info['fresh_report']['fresh_metrics'][name])
                _verify_metrics(arm['baseline'], ref, '0014_REFERENCE')
                _verify_metrics(arm['shift_sweep']['0.0']['metrics'], ref,
                                '0014_ZERO_SHIFT')
                for _, config_arm, shift in CONFIGURATIONS:
                    if name == config_arm:
                        sweep = arm['shift_sweep'][str(shift)]
                        if (sweep['complete'] is not True
                                or sweep['overflow_cases'] != 0
                                or sweep['cases'] != len(info['old_rows'] if split == 'dev'
                                                         else info['fresh_rows'])):
                            raise ValueError('GAMER_G1_0014_INCOMPLETE_REFERENCE_SWEEP')
            except (KeyError, TypeError, IndexError) as exc:
                raise ValueError('GAMER_G1_0014_INCOMPLETE_NULL_DIAGNOSTIC') from exc


def _load_inputs(root, transfer_report, fresh_run, null_run, output):
    from tools import nanojev_gamer_v1_backbone_first as backbone
    root, transfer_report, fresh_run, null_run, output = map(Path,
        (root, transfer_report, fresh_run, null_run, output))
    if output.exists():
        raise FileExistsError('GAMER_G1_0014_IMMUTABLE_OUTPUT')
    # Reuse 0013's canonical-LF vs. physical-CRLF hash and prompt lineage checks.
    info = _load_reference(root, transfer_report, fresh_run, output)
    report_path = null_run / 'null_boundary_report.json'
    report_sha = backbone.sha256(report_path)
    report = json.loads(report_path.read_text(encoding='utf-8'))
    _validate_diagnostic(report, info)
    info['null_report_sha'] = report_sha
    info['null_report_path'] = report_path
    return info


def _summarize(records, rows):
    if len(records) != len(rows):
        raise ValueError('GAMER_G1_0014_UNPAIRED_SUMMARY')
    result = _stats(records)
    result['missing_objects'] = result['expected'] - result['correct']
    result['false_positive_objects'] = result['predicted'] - result['correct']
    result['by_family'] = {}
    for family in FAMILIES:
        subset = [item for item, row in zip(records, rows) if row['family'] == family]
        result['by_family'][family] = _stats(subset)
    result['false_positives_by_kind'] = dict(sorted(Counter(
        obj[2] for predicted, gold in records for obj in predicted-gold).items()))
    result['missing_by_kind'] = dict(sorted(Counter(
        obj[2] for predicted, gold in records for obj in gold-predicted).items()))
    return result


def _paired(a, b):
    """B compared to A on identical prompts and exact gold span sets."""
    if len(a) != len(b) or len(a) != CASES:
        raise ValueError('GAMER_G1_0014_UNPAIRED_CANDIDATES')
    result = Counter()
    for (pred_a, gold_a), (pred_b, gold_b) in zip(a, b):
        if gold_a != gold_b:
            raise ValueError('GAMER_G1_0014_GOLD_DRIFT')
        result['b_only_exact'] += int(pred_b == gold_b and pred_a != gold_a)
        result['a_only_exact'] += int(pred_a == gold_a and pred_b != gold_b)
        result['b_newly_recovered_gold'] += len((pred_b & gold_b) - pred_a)
        result['b_previously_correct_gold_lost'] += len((pred_a & gold_a) - pred_b)
        result['b_new_false_positives'] += len((pred_b - gold_b) - pred_a)
        result['b_old_false_positives_removed'] += len((pred_a - gold_a) - pred_b)
    discordant = result['b_only_exact'] + result['a_only_exact']
    if discordant:
        tail = min(result['b_only_exact'], result['a_only_exact'])
        p = min(1.0, 2.0 * math.fsum(math.comb(discordant, i) * (0.5 ** discordant)
                                      for i in range(tail + 1)))
    else:
        p = 1.0
    return {**{k: int(result[k]) for k in (
                'b_only_exact','a_only_exact','b_newly_recovered_gold',
                'b_previously_correct_gold_lost','b_new_false_positives',
                'b_old_false_positives_removed')},
            'exact_discordant_pairs': discordant,
            'mcnemar_exact_two_sided_p': p}


def _verify_inputs_unchanged(info, transfer_report, fresh_run, source_adapter, entries):
    from tools import nanojev_gamer_v1_backbone_first as backbone
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    _verify_unchanged_inputs(info, transfer_report, fresh_run, source_adapter)
    if backbone.sha256(info['null_report_path']) != info['null_report_sha']:
        raise ValueError('GAMER_G1_0014_NULL_REPORT_CHANGED_DURING_RUN')
    for name in ('structured', 'null_focused'):
        entry = entries[name]
        cp = Path(entry['checkpoint'])
        if (backbone.sha256(cp/'manifest.json') != info['checkpoint_files'][name]['manifest_sha256']
                or backbone.sha256(cp/cur.HEAD_FILE) != info['checkpoint_files'][name]['head_file_sha256']):
            raise ValueError('GAMER_G1_0014_CHECKPOINT_CHANGED_DURING_RUN')


def run(*, root, transfer_report, fresh_run, null_run, output,
        device='cuda', max_length=2048):
    """Capture 320 times, decode four predeclared configurations, write once."""
    from tools import nanojev_gamer_v1_backbone_first as backbone
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    if type(max_length) is not int or max_length < 1:
        raise ValueError('GAMER_G1_0014_INVALID_MAX_LENGTH')
    output = Path(output)
    if output.exists():
        raise FileExistsError('GAMER_G1_0014_IMMUTABLE_OUTPUT')
    info = _load_inputs(root, transfer_report, fresh_run, null_run, output)
    rows = validation_rows(info['dataset'], info['fresh_rows'])
    dataset_bytes = _jsonl_bytes(rows)
    dataset_sha = hashlib.sha256(dataset_bytes).hexdigest()
    selected = info['selected']
    first = info['entries']['structured']['manifest']
    if (backbone.adapter_fingerprint(first['source_adapter']) != info['adapter_sha']
            or any(info['entries'][name]['manifest']['model_id'] != first['model_id']
                   or info['entries'][name]['manifest']['source_adapter'] != first['source_adapter']
                   for name in selected)):
        raise ValueError('GAMER_G1_0014_MODEL_LINEAGE')
    heads = {}
    checkpoint_files = {}
    for name in selected:
        entry = info['entries'][name]
        head, manifest = cur._load_checkpoint(entry['checkpoint'], device=device)
        if (manifest.get('stage') != 'G1'
                or manifest['head_sha256'] != entry['manifest']['head_sha256']):
            raise ValueError('GAMER_G1_0014_HEAD_CHANGED')
        head.eval()
        heads[name] = head
        cp = Path(entry['checkpoint'])
        checkpoint_files[name] = {
            'manifest_sha256': backbone.sha256(cp/'manifest.json'),
            'head_file_sha256': backbone.sha256(cp/cur.HEAD_FILE)}
    info['checkpoint_files'] = checkpoint_files
    model, tokenizer = backbone.model_components(
        first['model_id'], first['source_adapter'], device)
    records = {key: [] for key, _, _ in CONFIGURATIONS}
    per_case = []
    with torch.inference_mode():
        for index, row in enumerate(rows, 1):
            item = cur.capture(model, tokenizer, row, device, max_length)
            prepared = item['prepared']
            hidden = item['hidden'].to(device=device, dtype=torch.float32)
            gold = {(obj['start'], obj['end'], obj['kind'])
                    for obj in row['object_spans']}
            predictions = {}
            for name in selected:
                prediction = heads[name](hidden, eligible=prepared['eligible'].to(device),
                                         active_stage='G1')
                if name == 'structured':
                    shifts = (0.0, 2.0, 4.0)
                else:
                    shifts = (4.0,)
                for shift in shifts:
                    key = next(k for k, arm, s in CONFIGURATIONS
                               if arm == name and s == shift)
                    # Decoder receives only logits, eligibility, fixed shift;
                    # never the prompt, gold spans, or prior results.
                    spans = _decode_shift(prediction['span_logits'],
                                          prediction['span_valid'], shift)
                    guess = _char_set(spans, prepared)
                    predictions[key] = guess
                    records[key].append((guess, gold))
            per_case.append({
                'index': index, 'family': row['family'],
                'prompt_sha256': hashlib.sha256(row['prompt'].encode('utf-8')).hexdigest(),
                'gold_count': len(gold),
                'configurations': {key: {
                    'correct': len(guess & gold),
                    'missing': len(gold - guess),
                    'false_positives': len(guess - gold),
                    'exact': guess == gold,
                } for key, guess in predictions.items()},
            })
            if index == 1 or index % 20 == 0 or index == CASES:
                print(f'GAMER_G1_CALIBRATION_VALIDATION {index}/{CASES}', flush=True)
    metrics = {key: _summarize(records[key], rows) for key, _, _ in CONFIGURATIONS}
    pairings = {f'{a}__vs__{b}': _paired(records[a], records[b])
                for a, b in itertools.combinations(records, 2)}
    # Ranking fixed before first validation example: exact prompts first,
    # object F1 second, fewer false positives third. No automatic promotion.
    ranking = sorted(records, key=lambda key: (
        -metrics[key]['exact_prompts'], -metrics[key]['f1'],
        metrics[key]['false_positive_objects'], key))
    _verify_inputs_unchanged(info, transfer_report, fresh_run,
                             first['source_adapter'], info['entries'])
    report = {
        'schema': SCHEMA,
        'status': 'FROZEN_WITHIN_GENERATOR_DEVELOPMENT_REPLICATION_ONLY',
        'seed': SEED, 'cases': CASES,
        'families': {family: sum(row['family'] == family for row in rows)
                     for family in FAMILIES},
        'split': 'new_dev', 'configurations_predeclared': [
            {'key': key, 'checkpoint': arm, 'null_logit_shift': shift}
            for key, arm, shift in CONFIGURATIONS],
        'ranking_rule_predeclared': 'exact_prompts_desc,f1_desc,false_positives_asc,key_asc',
        'exploratory_ranking_not_certification': ranking,
        'development_leader_preserved': 'structured',
        'metrics': metrics, 'paired_comparisons': pairings,
        'validation_dataset_sha256': dataset_sha,
        'original_dataset_sha256': info['dataset_sha'],
        'original_transfer_report_sha256': info['transfer_sha'],
        'prior_fresh_report_sha256': info['fresh_report_sha'],
        'prior_fresh_dataset_sha256': info['fresh_dataset_sha'],
        'prior_fresh_dataset_file_sha256': info['fresh_dataset_file_sha'],
        'null_boundary_report_sha256': info['null_report_sha'],
        'source_adapter_sha256': info['adapter_sha'],
        'head_sha256': {name: info['entries'][name]['manifest']['head_sha256']
                        for name in selected},
        'checkpoint_file_sha256': checkpoint_files,
        'decoder': 'length_linear', 'length_power': 1.0,
        'per_object_penalty': 0.0, 'same_prompts_for_all_configurations': True,
        'one_feature_capture_per_prompt': True,
        'prior_train_dev_and_fresh_prompt_overlap': 0,
        'no_training': True, 'heads_frozen': True, 'decoder_adjustments_inference_only': True,
        'original_decoder_unchanged': True, 'challenge_or_test_split_used': False,
        'certification_authorized': False, 'production_promotion': False,
        'g2_unlocked': False,
        'interpretation': ('A one-shot replication on new within-generator development data. '
                           'The four candidates were fixed from earlier development work. '
                           'This is not an out-of-distribution or sealed test result; '
                           'no choice here unlocks G2 or promotes a decoder.'),
    }
    output.mkdir(parents=True)
    (output/'validation_dev.jsonl').write_bytes(dataset_bytes)
    (output/'validation_per_case.jsonl').write_bytes(_jsonl_bytes(per_case))
    (output/'validation_report.json').write_bytes(
        (json.dumps(report, sort_keys=True, indent=2) + '\n').encode('utf-8'))
    return report
