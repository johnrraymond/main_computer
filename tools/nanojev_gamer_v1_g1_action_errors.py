"""G1-0015: frozen null-focused +4.0 action-error attribution, dev only.

No tuning, training, decoder modification, test/challenge access, certification,
production promotion or G2 unlock. The selection comes from completed G1-0014.
Gold annotations enter ONLY diagnostics after fixed inference. All 320 winner
predictions must reproduce the existing frozen-calibration report case by case.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path

import torch

from tools.nanojev_gamer_v1_g1_calibration_validation import (
    CASES, CONFIGURATIONS, FAMILIES, SCHEMA as VALIDATION_SCHEMA,
    SEED, _jsonl_bytes, _load_inputs as _load_prior_inputs,
    _summarize, _verify_inputs_unchanged as _verify_prior_inputs,
    validation_rows,
)
from tools.nanojev_gamer_v1_g1_decoder_frob import _char_set
from tools.nanojev_gamer_v1_g1_null_boundary import (
    _decode_shift, _explain_gold, _verify_metrics,
)
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS
from tools.nanojev_gamer_v1_graph_head import token_span_char_bounds

SCHEMA = 'nanojev.gamer.graph.g1.frozen-action-error-diagnostic.v1'
WINNER_KEY = 'null_focused_4'
WINNER_HEAD = 'null_focused'
WINNER_SHIFT = 4.0
# These are the observed G1-0014 numbers, not post-0015 selection criteria.
PINNED_WINNER_F1 = .9731479930776532
PINNED_WINNER = {
    'cases': 320, 'expected': 8982, 'correct': 8716,
    'exact_prompts': 134, 'missing_objects': 266,
    'false_positive_objects': 215,
}
REASONS = (
    'null_wins', 'wrong_type', 'below_threshold',
    'linear_decoder_excluded', 'span_exceeds_window', 'invalid_token_span',
)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines()
            if line.strip()]


def _require_report(report, info, dataset_bytes, case_rows, original_bytes):
    """Revalidate selection, prompt lineage, and recorded per-case arithmetic."""
    expected_names = [name for name, _, _ in CONFIGURATIONS]
    if (report.get('schema') != VALIDATION_SCHEMA
            or report.get('status') != 'FROZEN_WITHIN_GENERATOR_DEVELOPMENT_REPLICATION_ONLY'
            or report.get('cases') != CASES or report.get('seed') != SEED
            or report.get('split') != 'new_dev'
            or report.get('families') != {'encounter':160,'allocation':160}
            or report.get('configurations_predeclared') != [
                {'key': name, 'checkpoint': arm, 'null_logit_shift': shift}
                for name, arm, shift in CONFIGURATIONS]
            or report.get('exploratory_ranking_not_certification') != [
                WINNER_KEY, 'structured_4', 'structured_2', 'structured_0']
            or report.get('ranking_rule_predeclared') !=
                'exact_prompts_desc,f1_desc,false_positives_asc,key_asc'
            or report.get('validation_dataset_sha256') != _sha(dataset_bytes)
            or report.get('original_dataset_sha256') != info['dataset_sha']
            or report.get('original_transfer_report_sha256') != info['transfer_sha']
            or report.get('prior_fresh_report_sha256') != info['fresh_report_sha']
            or report.get('prior_fresh_dataset_sha256') != info['fresh_dataset_sha']
            or report.get('prior_fresh_dataset_file_sha256') != info['fresh_dataset_file_sha']
            or report.get('null_boundary_report_sha256') != info['null_report_sha']
            or report.get('source_adapter_sha256') != info['adapter_sha']
            or report.get('head_sha256') != {
                name: info['entries'][name]['manifest']['head_sha256']
                for name in ('structured', 'null_focused')}
            or report.get('checkpoint_file_sha256') != info['checkpoint_files']
            or report.get('decoder') != 'length_linear'
            or report.get('length_power') != 1.0
            or report.get('per_object_penalty') != 0.0
            or report.get('same_prompts_for_all_configurations') is not True
            or report.get('one_feature_capture_per_prompt') is not True
            or report.get('prior_train_dev_and_fresh_prompt_overlap') != 0
            or report.get('no_training') is not True
            or report.get('heads_frozen') is not True
            or report.get('decoder_adjustments_inference_only') is not True
            or report.get('original_decoder_unchanged') is not True
            or report.get('challenge_or_test_split_used') is not False
            or report.get('certification_authorized') is not False
            or report.get('production_promotion') is not False
            or report.get('g2_unlocked') is not False
            or set(report.get('metrics', {})) != set(expected_names)
            or len(case_rows) != CASES):
        raise ValueError('GAMER_G1_0015_INVALID_VALIDATION_LINEAGE')

    winner = report['metrics'][WINNER_KEY]
    if any(winner.get(key) != value for key, value in PINNED_WINNER.items()):
        raise ValueError('GAMER_G1_0015_WINNER_NOT_PINNED')
    if not math.isclose(winner.get('f1', -1), PINNED_WINNER_F1, abs_tol=1e-9):
        raise ValueError('GAMER_G1_0015_WINNER_F1_DRIFT')
    for name in expected_names:
        m = report['metrics'][name]
        if (m.get('cases') != CASES or m.get('expected') != winner['expected']
                or m.get('missing_objects') != m.get('expected', -1)-m.get('correct', -2)
                or m.get('false_positive_objects') != m.get('predicted', -1)-m.get('correct', -2)):
            raise ValueError('GAMER_G1_0015_INVALID_SUMMARY')
    for name in expected_names:
        if name == WINNER_KEY:
            continue
        other = report['metrics'][name]
        if (winner['exact_prompts'] < other['exact_prompts']
                or (winner['exact_prompts'] == other['exact_prompts']
                    and winner['f1'] < other['f1'])):
            raise ValueError('GAMER_G1_0015_WINNER_RANKING_DIVERGED')
    if len(original_bytes) != len(dataset_bytes) or original_bytes != dataset_bytes:
        raise ValueError('GAMER_G1_0015_VALIDATION_DATASET_CHANGED')

    total = {key: Counter() for key in expected_names}
    for index, (row, case) in enumerate(zip(info['validation_rows'], case_rows), 1):
        expected_digest = _sha(row['prompt'].encode('utf-8'))
        if (case.get('index') != index or case.get('family') != row['family']
                or case.get('prompt_sha256') != expected_digest
                or case.get('gold_count') != len(row['object_spans'])
                or set(case.get('configurations', {})) != set(expected_names)):
            raise ValueError('GAMER_G1_0015_INVALID_VALIDATION_CASE')
        for name in expected_names:
            pred = case['configurations'][name]
            for key in ('correct', 'missing', 'false_positives'):
                if type(pred.get(key)) is not int or pred[key] < 0:
                    raise ValueError('GAMER_G1_0015_INVALID_CASE_COUNT')
            if (pred['correct']+pred['missing'] != case['gold_count']
                    or type(pred.get('exact')) is not bool
                    or pred['exact'] != (pred['missing'] == 0 and pred['false_positives'] == 0)):
                raise ValueError('GAMER_G1_0015_INVALID_CASE_ACCOUNTING')
            total[name].update({'correct':pred['correct'], 'missing':pred['missing'],
                'false_positives':pred['false_positives'], 'exact_prompts':int(pred['exact'])})
    for name in expected_names:
        m = report['metrics'][name]
        if (total[name]['correct'] != m['correct']
                or total[name]['missing'] != m['missing_objects']
                or total[name]['false_positives'] != m['false_positive_objects']
                or total[name]['exact_prompts'] != m['exact_prompts']):
            raise ValueError('GAMER_G1_0015_VALIDATION_CASES_DIVERGED')


def _load_inputs(root, transfer_report, fresh_run, null_run, validation_run, output):
    from tools import nanojev_gamer_v1_backbone_first as backbone
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    output = Path(output)
    if output.exists():
        raise FileExistsError('GAMER_G1_0015_IMMUTABLE_OUTPUT')
    info = _load_prior_inputs(root, transfer_report, fresh_run, null_run, output)
    rows = validation_rows(info['dataset'], info['fresh_rows'])
    info['validation_rows'] = rows
    validation_run = Path(validation_run)
    paths = {name: validation_run/name for name in (
        'validation_report.json', 'validation_dev.jsonl', 'validation_per_case.jsonl')}
    info['validation_paths'] = paths
    info['validation_file_sha256'] = {key: backbone.sha256(path) for key,path in paths.items()}
    data = paths['validation_dev.jsonl'].read_bytes()
    canonical = _jsonl_bytes(rows)
    # Input checks use byte hashes; no newline rewriting or files are modified.
    if data != canonical:
        raise ValueError('GAMER_G1_0015_VALIDATION_DATASET_CHANGED')
    case_rows = _read_jsonl(paths['validation_per_case.jsonl'])
    report = json.loads(paths['validation_report.json'].read_text(encoding='utf-8'))
    info['checkpoint_files'] = {name: {
        'manifest_sha256': backbone.sha256(Path(info['entries'][name]['checkpoint'])/'manifest.json'),
        'head_file_sha256': backbone.sha256(Path(info['entries'][name]['checkpoint'])/cur.HEAD_FILE),
    } for name in ('structured', 'null_focused')}
    _require_report(report, info, canonical, case_rows, data)
    info['validation_report'] = report
    info['validation_cases'] = case_rows
    return info


def _overlap(a, b):
    return a[0] < b[1] and b[0] < a[1]


def _bucket(n):
    return '1-4' if n <= 4 else '5-8' if n <= 8 else '9-16' if n <= 16 else '17+'


def _analyze_case(prediction, prepared, row, spans, guessed, *, index):
    """Read-only post-inference action errors, with calibrated and raw evidence."""
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    gold = {(s['start'], s['end'], s['kind']) for s in row['object_spans']}
    adjusted = prediction['span_logits'].detach().float().clone()
    adjusted[..., 0] -= WINNER_SHIFT
    misses, _, _ = _explain_gold(
        {'span_logits': adjusted, 'span_valid': prediction['span_valid']},
        prepared, row, spans, guessed, split='validation_dev',
        case_index=index, arm=WINNER_KEY)
    miss_map = {(m['span'][0],m['span'][1],m['kind']):m for m in misses}
    if set(miss_map) != gold-guessed:
        raise ValueError('GAMER_G1_0015_MISS_EXPLANATION_MISMATCH')
    aligned = cur.aligned_labels(prepared, row)['spans']
    aligned_map = {}
    for span in aligned:
        bounds = token_span_char_bounds(span, prepared)
        if bounds is None:
            raise ValueError('GAMER_G1_0015_UNALIGNED_GOLD')
        aligned_map[(*bounds,span['kind'])] = span
    if set(aligned_map) != gold:
        raise ValueError('GAMER_G1_0015_UNALIGNED_GOLD')
    logits = prediction['span_logits'].detach().float().cpu()
    prob = adjusted.detach().float().cpu().softmax(-1)
    action_misses = []
    for key, entry in sorted(miss_map.items()):
        if key[2] != 'action':
            continue
        item = dict(entry)
        item['surface'] = row['prompt'][key[0]:key[1]]
        item['characters'] = key[1]-key[0]
        item['span_length_bucket'] = _bucket(item['characters'])
        item['overlapping_predicted_action'] = sum(
            _overlap(key, obj) for obj in guessed-gold if obj[2]=='action')
        item['overlapping_predicted_other_kind'] = sum(
            _overlap(key, obj) for obj in guessed if obj[2]!='action')
        item['overlapping_correct_predicted_action'] = sum(
            _overlap(key, obj) for obj in guessed & gold if obj[2]=='action')
        first, last = aligned_map[key]['token_first'], aligned_map[key]['token_last']
        item['token_span_width'] = last-first+1
        if (first >= 0 and last >= first and first < logits.shape[0]
                and last-first < logits.shape[1]
                and bool(prediction['span_valid'][first,last-first])):
            original = logits[first,last-first]
            label = SPAN_KINDS.index('action')+1
            item['raw_action_vs_null_margin'] = round(float(original[label]-original[0]),6)
            item['calibrated_action_vs_null_margin'] = round(
                float(original[label]-original[0]+WINNER_SHIFT),6)
            item['calibrated_action_probability'] = round(float(prob[first,last-first,label]),6)
            item['best_nonnull_kind'] = SPAN_KINDS[int(original[1:].argmax())]
        action_misses.append(item)
    action_false_positives = []
    for key in sorted(guessed-gold):
        if key[2] != 'action':
            continue
        matches = [span for span in spans
                   if span['kind']=='action' and token_span_char_bounds(span,prepared)==key[:2]]
        if len(matches) != 1:
            raise ValueError('GAMER_G1_0015_ACTION_FALSE_POSITIVE_ALIGNMENT')
        span = matches[0]
        first, offset = span['token_first'], span['token_last']-span['token_first']
        scores = logits[first,offset]
        action_false_positives.append({
            'index': index, 'family': row['family'], 'kind':'action',
            'span':list(key[:2]), 'surface':row['prompt'][key[0]:key[1]],
            'characters':key[1]-key[0], 'span_length_bucket':_bucket(key[1]-key[0]),
            'raw_action_vs_null_margin':round(float(scores[SPAN_KINDS.index('action')+1]-scores[0]),6),
            'calibrated_action_probability':round(float(prob[first,offset,SPAN_KINDS.index('action')+1]),6),
            'overlaps_gold_action':any(_overlap(key, g) for g in gold if g[2]=='action'),
            'overlaps_gold_other_kind':any(_overlap(key, g) for g in gold if g[2]!='action'),
        })
    return action_misses, action_false_positives


def _summary(misses, false_positives, gold_action_count, predicted_action_count):
    if gold_action_count < 0 or predicted_action_count < 0:
        raise ValueError('GAMER_G1_0015_BAD_ACTION_COUNTS')
    by_family = {}
    for family in FAMILIES:
        sub = [m for m in misses if m['family'] == family]
        fp = [f for f in false_positives if f['family'] == family]
        by_family[family] = {
            'missing_actions':len(sub), 'false_positive_actions':len(fp),
            'miss_reasons':dict(sorted(Counter(m['reason'] for m in sub).items())),
            'boundary_overlap_misses':sum(m['overlapping_predicted_action']>0 for m in sub),
        }
    reasons = Counter(m['reason'] for m in misses)
    if set(reasons)-set(REASONS):
        raise ValueError('GAMER_G1_0015_UNKNOWN_REASON')
    return {
        'gold_actions':gold_action_count,
        'predicted_actions':predicted_action_count,
        'correct_actions':gold_action_count-len(misses),
        'missing_actions':len(misses),
        'false_positive_actions':len(false_positives),
        'miss_reasons':dict(sorted(reasons.items())),
        'miss_reasons_by_family':by_family,
        'misses_with_overlapping_incorrect_action':sum(
            m['overlapping_predicted_action']>0 for m in misses),
        'misses_with_overlapping_other_kind':sum(
            m['overlapping_predicted_other_kind']>0 for m in misses),
        'misses_with_overlapping_correct_action':sum(
            m['overlapping_correct_predicted_action']>0 for m in misses),
        'decoder_exclusions_conflicting_with_correct_gold':sum(
            m.get('conflicts_any_correct_gold', False) for m in misses
            if m['reason']=='linear_decoder_excluded'),
        'decoder_exclusions_conflicting_only_with_false_positives':sum(
            m.get('conflicts_only_false_positives', False) for m in misses
            if m['reason']=='linear_decoder_excluded'),
        'null_wins_with_action_best_nonnull':sum(
            m.get('best_nonnull_is_correct_kind',False) for m in misses
            if m['reason']=='null_wins'),
        'misses_by_character_span_width':dict(sorted(Counter(
            m['span_length_bucket'] for m in misses).items())),
        'false_positives_by_character_span_width':dict(sorted(Counter(
            m['span_length_bucket'] for m in false_positives).items())),
        'false_positives_overlapping_gold_action':sum(
            m['overlaps_gold_action'] for m in false_positives),
        'false_positives_overlapping_other_gold_kind':sum(
            m['overlaps_gold_other_kind'] for m in false_positives),
    }


def _verify_unchanged(info, transfer_report, fresh_run, source_adapter):
    from tools import nanojev_gamer_v1_backbone_first as backbone
    _verify_prior_inputs(info, transfer_report, fresh_run,
                         source_adapter, info['entries'])
    for name, path in info['validation_paths'].items():
        if backbone.sha256(path) != info['validation_file_sha256'][name]:
            raise ValueError('GAMER_G1_0015_VALIDATION_INPUT_CHANGED_DURING_RUN')


def run(*, root, transfer_report, fresh_run, null_run, validation_run, output,
        device='cuda', max_length=2048):
    """Trace exactly the 320 frozen G1-0014 winner predictions, no optimization."""
    from tools import nanojev_gamer_v1_backbone_first as backbone
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    if type(max_length) is not int or max_length < 1:
        raise ValueError('GAMER_G1_0015_INVALID_MAX_LENGTH')
    output = Path(output)
    if output.exists():
        raise FileExistsError('GAMER_G1_0015_IMMUTABLE_OUTPUT')
    info = _load_inputs(root, transfer_report, fresh_run, null_run, validation_run, output)
    entry = info['entries'][WINNER_HEAD]
    checkpoint = entry['checkpoint']
    manifest = entry['manifest']
    if backbone.adapter_fingerprint(manifest['source_adapter']) != info['adapter_sha']:
        raise ValueError('GAMER_G1_0015_ADAPTER_CHANGED')
    head, loaded = cur._load_checkpoint(checkpoint, device=device)
    if loaded.get('stage') != 'G1' or loaded.get('head_sha256') != manifest['head_sha256']:
        raise ValueError('GAMER_G1_0015_HEAD_CHANGED')
    head.eval()
    model, tokenizer = backbone.model_components(manifest['model_id'],
                                                  manifest['source_adapter'], device)
    records, miss_rows, fp_rows, by_case = [], [], [], []
    all_gold_actions = all_predicted_actions = 0
    with torch.inference_mode():
        for index, row in enumerate(info['validation_rows'],1):
            item = cur.capture(model,tokenizer,row,device,max_length)
            prepared = item['prepared']
            hidden = item['hidden'].to(device=device,dtype=torch.float32)
            prediction = head(hidden, eligible=prepared['eligible'].to(device),
                              active_stage='G1')
            spans = _decode_shift(prediction['span_logits'], prediction['span_valid'], WINNER_SHIFT)
            guess = _char_set(spans, prepared)
            gold = {(s['start'],s['end'],s['kind']) for s in row['object_spans']}
            current = {'correct':len(guess & gold), 'missing':len(gold-guess),
                       'false_positives':len(guess-gold), 'exact':guess==gold}
            if current != info['validation_cases'][index-1]['configurations'][WINNER_KEY]:
                raise ValueError('GAMER_G1_0015_WINNER_CASE_REPLAY_DIVERGED')
            missing, fps = _analyze_case(prediction,prepared,row,spans,guess,index=index)
            miss_rows.extend(missing)
            fp_rows.extend(fps)
            all_gold_actions += sum(s[2]=='action' for s in gold)
            all_predicted_actions += sum(s[2]=='action' for s in guess)
            records.append((guess,gold))
            if missing or fps:
                by_case.append({'index':index,'family':row['family'],
                    'prompt_sha256':_sha(row['prompt'].encode('utf-8')),
                    'prompt':row['prompt'],
                    'missing_actions':missing,'false_positive_actions':fps,
                    'winner_exact':guess==gold})
            if index == 1 or index%20==0 or index==CASES:
                print(f'GAMER_G1_ACTION_ERRORS {index}/{CASES}',flush=True)
    metric = _summarize(records, info['validation_rows'])
    winner_metric = info['validation_report']['metrics'][WINNER_KEY]
    _verify_metrics(metric, winner_metric, '0015_WINNER_REPLAY')
    if (metric['missing_objects'] != winner_metric['missing_objects']
            or metric['false_positive_objects'] != winner_metric['false_positive_objects']
            or metric['by_family'] != winner_metric['by_family']
            or metric['missing_by_kind'] != winner_metric['missing_by_kind']
            or metric['false_positives_by_kind'] != winner_metric['false_positives_by_kind']):
        raise ValueError('GAMER_G1_0015_WINNER_METRICS_DIVERGED')
    action_metric = _summary(miss_rows, fp_rows, all_gold_actions, all_predicted_actions)
    if (action_metric['missing_actions'] != metric['missing_by_kind'].get('action',0)
            or action_metric['false_positive_actions'] !=
                metric['false_positives_by_kind'].get('action',0)
            or action_metric['correct_actions'] != metric['by_kind']['action']['correct']):
        raise ValueError('GAMER_G1_0015_ACTION_ACCOUNTING_DIVERGED')
    _verify_unchanged(info, transfer_report, fresh_run, manifest['source_adapter'])
    selection = {
        'schema':'nanojev.gamer.graph.g1.development-leader-freeze.v1',
        'status':'FROZEN_DEVELOPMENT_REFERENCE_NOT_PRODUCTION',
        'key':WINNER_KEY, 'checkpoint':'null_focused',
        'checkpoint_path':str(Path(checkpoint)),
        'null_logit_shift':WINNER_SHIFT,
        'decoder':'length_linear', 'length_power':1.0, 'per_object_penalty':0.0,
        'head_sha256':manifest['head_sha256'],
        'checkpoint_file_sha256':info['checkpoint_files'][WINNER_HEAD],
        'source_adapter_sha256':info['adapter_sha'],
        'validation_dataset_sha256':info['validation_report']['validation_dataset_sha256'],
        'validation_report_file_sha256':info['validation_file_sha256']['validation_report.json'],
        'training_authorized':False, 'production_promotion':False,
        'certification_authorized':False, 'g2_unlocked':False,
    }
    report = {
        'schema':SCHEMA, 'status':'DIAGNOSTIC_ONLY_NOT_CERTIFICATION',
        'cases':CASES, 'families':{'encounter':160,'allocation':160},
        'winner':selection, 'baseline_metrics':metric,
        'action_errors':action_metric,
        'errored_prompts_with_action_errors':len(by_case),
        'diagnostic_example_count':len(miss_rows)+len(fp_rows),
        'interpretation':('Attributions are gold-informed diagnostics after frozen inference; '
            'no oracle annotations enter predictions or candidate choice. '
            'Within-generator dev replication only, no G2 stage gate.'),
        'no_training':True, 'heads_frozen':True, 'decoder_frozen':True,
        'challenge_or_test_split_used':False, 'production_promotion':False,
        'certification_authorized':False,'g2_unlocked':False,
    }
    output.mkdir(parents=True)
    (output/'frozen_development_candidate.json').write_bytes(
        (json.dumps(selection,indent=2,sort_keys=True)+'\n').encode('utf-8'))
    (output/'action_error_report.json').write_bytes(
        (json.dumps(report,indent=2,sort_keys=True)+'\n').encode('utf-8'))
    (output/'action_error_cases.jsonl').write_bytes(_jsonl_bytes(by_case))
    return report
