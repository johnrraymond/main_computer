#!/usr/bin/env python3
"""V8: training-free, capture-bound mathematical competence transition audit.

Compare exactly the V6 protected, unaligned reference and the V7 selected
uniform-continuation atlases emitted by the *same* audited V7 run. Track
correct->wrong, wrong->correct, and per-six-case group losses on DEV and the
previously examined TEST separately. Never train, select, or promote weights.

The input is the V7 report directory, not the original model or an unchecked
pair of arbitrary CSV files. The test deck is diagnostic-only; its results must
not become a fitting or promotion gate. No change to capture-bound routing.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path

SCHEMA = 'nanojev.gamer.v1.mathematical-transition.v8'
V7_SCHEMA = 'nanojev.gamer.v1.protected-math-repair.v7'
REFERENCE = 'v6_reference'
CANDIDATE = 'uniform_continuation'
SPLITS = (('dev', 'dev'), ('previously_seen_test', 'test'))
SURFACES = ('source_native', 'spine_difference', 'spine_belief', 'game_prose_belief')
REQUIRED_CASE_FIELDS = {
    'id', 'group', 'split', 'family', 'surface', 'prompt_sha256',
    'target_fraction', 'target_sign', 'target_sign_name', 'magnitude_band',
    'operand_band', 'operand_center', 'baseline_sign', 'baseline_value',
    'predicted_sign', 'predicted_value', 'correct', 'original_correct',
    'zero_miss', 'nonzero_as_zero', 'wrong_direction', 'correction_admitted',
    'correction_applied', 'decision_margin', 'residual_signed_log',
    'residual_class_max'
}
REQUIRED_GROUP_FIELDS = {
    'group', 'split', 'family', 'surfaces', 'correct_cases', 'zero_misses',
    'nonzero_misses', 'reversal_miss', 'surface_disagreement_pairs',
    'both_correct_pairs', 'left_complete_triple', 'right_complete_triple',
    'complete_six', 'all_corrections_denied'
}


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _csv_read(path, required):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError('V8_REQUIRED_ATLAS_MISSING: ' + str(path))
    with path.open(newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError('V8_ATLAS_COLUMNS_MISSING: ' + str(path))
        rows = list(reader)
    if not rows:
        raise ValueError('V8_EMPTY_ATLAS: ' + str(path))
    return rows


def _as_int(value, field):
    try:
        n = int(value)
    except (ValueError, TypeError):
        raise ValueError('V8_INVALID_INTEGER_' + field) from None
    return n


def _as_bit(value, field):
    n = _as_int(value, field)
    if n not in (0, 1):
        raise ValueError('V8_INVALID_BOOLEAN_' + field)
    return n


def _as_float(value, field):
    try:
        v = float(value)
    except (ValueError, TypeError):
        raise ValueError('V8_INVALID_FLOAT_' + field) from None
    if not math.isfinite(v):
        raise ValueError('V8_NONFINITE_' + field)
    return v


def _sign(target):
    return (target > 0) - (target < 0)


def _check_report(run):
    path = run / 'report.json'
    if not path.is_file():
        raise FileNotFoundError('V8_V7_REPORT_MISSING')
    report = json.loads(path.read_text(encoding='utf-8'))
    if (report.get('schema') != V7_SCHEMA or
            report.get('mode') != 'repair' or
            report.get('status') != 'EXPERIMENT_COMPLETE_NO_PROMOTION' or
            report.get('reference_arm_verified') != 'dual_protected_unaligned' or
            report.get('selected_candidate') != CANDIDATE or
            report.get('original_task_protection') != 'EXACT_V6_HARD_BYPASS_UNCHANGED' or
            report.get('production_weights_modified') is not False or
            report.get('production_promotion') is not False or
            report.get('original_dev_used_for_fitting') is not False or
            report.get('synthetic_test_used_for_fitting') is not False):
        raise ValueError('V8_V7_LINEAGE_OR_SELECTION_NOT_VERIFIED')
    if (not isinstance(report.get('synthetic_sha256'), str) or
            len(report['synthetic_sha256']) != 64 or
            not isinstance(report.get('lineage'), dict)):
        raise ValueError('V8_V7_CAPTURE_LINEAGE_MISSING')
    candidates = {r.get('id'): r for r in report.get('candidates', [])}
    if not all(name in candidates for name in (REFERENCE, CANDIDATE)):
        raise ValueError('V8_V7_CANDIDATES_MISSING')
    if (candidates[REFERENCE]['selected_step'] != report['v6_reference_selected_step'] or
            candidates[CANDIDATE]['selected_step'] <= 0 or
            candidates[REFERENCE]['original_dev_retention']['status'] != 'PASS' or
            candidates[CANDIDATE]['original_dev_retention']['status'] != 'PASS' or
            candidates[REFERENCE].get('promotion_authorized') is not False or
            candidates[CANDIDATE].get('promotion_authorized') is not False):
        raise ValueError('V8_REFERENCE_OR_CONTINUATION_NOT_VERIFIED')
    return report, candidates


def _load_cases(path, split):
    rows = _csv_read(path, REQUIRED_CASE_FIELDS)
    by_id = {}
    for row in rows:
        if not row['id'] or not row['group'] or row['split'] != split or not row['prompt_sha256']:
            raise ValueError('V8_CASE_ID_GROUP_SPLIT_INVALID')
        if row['id'] in by_id:
            raise ValueError('V8_DUPLICATE_CASE_ID')
        exact = Fraction(row['target_fraction'])
        expected_sign = _sign(exact)
        if (_as_int(row['target_sign'], 'target_sign') != expected_sign or
                row['target_sign_name'] != {-1: 'negative', 0: 'zero', 1: 'positive'}[expected_sign]):
            raise ValueError('V8_TARGET_SIGN_INCONSISTENT')
        if row['surface'] not in SURFACES:
            raise ValueError('V8_UNKNOWN_CAPTURED_SURFACE')
        actual_sign = _as_int(row['predicted_sign'], 'predicted_sign')
        baseline_sign = _as_int(row['baseline_sign'], 'baseline_sign')
        if actual_sign not in (-1, 0, 1) or baseline_sign not in (-1, 0, 1):
            raise ValueError('V8_PREDICTION_SIGN_INVALID')
        b = _as_float(row['baseline_value'], 'baseline_value')
        v = _as_float(row['predicted_value'], 'predicted_value')
        admitted = _as_bit(row['correction_admitted'], 'correction_admitted')
        applied = _as_bit(row['correction_applied'], 'correction_applied')
        for key, flag in (
            ('correct', int(actual_sign == expected_sign)),
            ('original_correct', int(baseline_sign == expected_sign)),
            ('zero_miss', int(expected_sign == 0 and actual_sign != 0)),
            ('nonzero_as_zero', int(expected_sign != 0 and actual_sign == 0)),
            ('wrong_direction', int(expected_sign != 0 and actual_sign == -expected_sign)),
        ):
            if _as_bit(row[key], key) != flag:
                raise ValueError('V8_INTERNAL_CASE_CLASSIFICATION_INCONSISTENT_' + key)
        if admitted != int(row['surface'] in ('source_native', 'spine_difference')):
            raise ValueError('V8_ROUTE_SURFACE_MISMATCH')
        if not admitted and (applied or v != b or actual_sign != baseline_sign):
            raise ValueError('V8_UNCERTIFIED_CAPTURE_CHANGED')
        for key in ('decision_margin', 'residual_signed_log', 'residual_class_max'):
            _as_float(row[key], key)
        if len(row['prompt_sha256']) != 64:
            raise ValueError('V8_PROMPT_SHA_INVALID')
        by_id[row['id']] = row
    return by_id


def _verify_alignment(reference, candidate):
    if set(reference) != set(candidate):
        raise ValueError('V8_CASE_ID_SET_CHANGED')
    immutable = ('group', 'split', 'family', 'surface', 'prompt_sha256',
                 'target_fraction', 'target_sign', 'target_sign_name',
                 'magnitude_band', 'operand_band', 'operand_center',
                 'operand_max_abs', 'baseline_sign', 'baseline_value',
                 'original_correct', 'correction_admitted')
    for key in reference:
        a, b = reference[key], candidate[key]
        for field in immutable:
            if a.get(field, '') != b.get(field, ''):
                raise ValueError('V8_UNMATCHED_CAPTURE_OR_BASELINE_' + field)


def _group_index(rows):
    groups = defaultdict(list)
    for r in rows.values():
        groups[r['group']].append(r)
    results = {}
    for group_id, members in groups.items():
        if len(members) != 6:
            raise ValueError('V8_INCOMPLETE_SIX_CASE_GROUP')
        surfaces = defaultdict(dict)
        for r in members:
            s = r['surface']
            k = int(r['target_sign'])
            if k in surfaces[s]:
                raise ValueError('V8_DUPLICATE_GROUP_SIGN')
            surfaces[s][k] = r
        if len(surfaces) != 2 or any(set(m) != {-1, 0, 1} for m in surfaces.values()):
            raise ValueError('V8_INVALID_GROUP_STRUCTURE')
        left, right = [surfaces[k] for k in sorted(surfaces)]
        if (len({r['family'] for r in members}) != 1 or
                len({r['split'] for r in members}) != 1 or
                any(Fraction(left[k]['target_fraction']) != Fraction(right[k]['target_fraction'])
                    for k in (-1, 0, 1))):
            raise ValueError('V8_CROSS_SURFACE_MATHEMATICAL_GROUP_MISMATCH')
        results[group_id] = {'members': sorted(members,
            key=lambda x: (x['surface'], int(x['target_sign']))),
            'by_surface': surfaces,
            'complete': int(all(int(r['correct']) for r in members)),
            'triples': {name: int(all(int(r['correct']) for r in smap.values()))
                        for name, smap in surfaces.items()},
            'both_correct_pairs': sum(int(left[k]['correct']) and int(right[k]['correct'])
                                      for k in (-1, 0, 1)),
            'surface_disagreements': sum(int(left[k]['predicted_sign'] != right[k]['predicted_sign'])
                                         for k in (-1, 0, 1)),
            'correct_count': sum(int(r['correct']) for r in members),
            'zero_misses': sum(int(r['zero_miss']) for r in members),
            'nonzero_misses': sum(int(r['target_sign']) != 0 and not int(r['correct'])
                                  for r in members)}
    return results


def _verify_group_csv(path, groups):
    rows = _csv_read(path, REQUIRED_GROUP_FIELDS)
    if len(rows) != len(groups) or len({r['group'] for r in rows}) != len(rows):
        raise ValueError('V8_GROUP_ATLAS_ID_SET_CHANGED')
    for r in rows:
        if r['group'] not in groups:
            raise ValueError('V8_GROUP_ATLAS_ID_SET_CHANGED')
        g = groups[r['group']]
        checks = {'complete_six': g['complete'], 'correct_cases': g['correct_count'],
                  'zero_misses': g['zero_misses'], 'nonzero_misses': g['nonzero_misses'],
                  'both_correct_pairs': g['both_correct_pairs'],
                  'surface_disagreement_pairs': g['surface_disagreements'],
                  'left_complete_triple': g['triples'][sorted(g['triples'])[0]],
                  'right_complete_triple': g['triples'][sorted(g['triples'])[1]]}
        for field, expected in checks.items():
            if _as_int(r[field], field) != expected:
                raise ValueError('V8_GROUP_ATLAS_MISMATCH_' + field)


def _transition(a, b):
    ca, cb = int(a['correct']), int(b['correct'])
    return ('retained_correct' if ca and cb else
            'lost_correct' if ca and not cb else
            'newly_correct' if not ca and cb else 'still_wrong')


def _intervention(a, b):
    prev, now = int(a['correction_applied']), int(b['correction_applied'])
    if prev == 0 and now == 1:
        return 'correction_newly_applied'
    if prev == 1 and now == 0:
        return 'correction_withdrawn'
    if prev and now:
        return 'both_corrected_different_decisions' if a['predicted_sign'] != b['predicted_sign'] else 'both_corrected_same_sign'
    return 'neither_correction_applied'


def _loss_type(a, b):
    if int(a['correct']) and not int(b['correct']):
        s = int(a['target_sign'])
        result = int(b['predicted_sign'])
        if s == 0:
            return 'exact_zero_became_nonzero'
        if result == 0:
            return 'nonzero_became_zero'
        if result == -s:
            return 'nonzero_sign_reversed'
        return 'other_nonzero_regression'
    return ''


def _make_case_transitions(ref, new, split):
    records = []
    for key in sorted(ref):
        a, b = ref[key], new[key]
        transition = _transition(a, b)
        records.append({
            'split': split, 'id': key, 'group': a['group'],
            'family': a['family'], 'surface': a['surface'],
            'target_fraction': a['target_fraction'], 'target_sign': a['target_sign'],
            'target_sign_name': a['target_sign_name'],
            'magnitude_band': a['magnitude_band'], 'operand_band': a['operand_band'],
            'operand_center': a['operand_center'],
            'transition': transition, 'regression_kind': _loss_type(a, b),
            'reference_correct': int(a['correct']), 'continuation_correct': int(b['correct']),
            'reference_sign': a['predicted_sign'], 'continuation_sign': b['predicted_sign'],
            'reference_value': a['predicted_value'], 'continuation_value': b['predicted_value'],
            'value_shift': _as_float(b['predicted_value'], 'value') - _as_float(a['predicted_value'], 'value'),
            'reference_zero_miss': a['zero_miss'], 'continuation_zero_miss': b['zero_miss'],
            'reference_wrong_direction': a['wrong_direction'],
            'continuation_wrong_direction': b['wrong_direction'],
            'reference_applied': a['correction_applied'], 'continuation_applied': b['correction_applied'],
            'correction_transition': _intervention(a, b),
            'reference_margin': a['decision_margin'], 'continuation_margin': b['decision_margin'],
            'margin_shift': _as_float(b['decision_margin'], 'margin') - _as_float(a['decision_margin'], 'margin'),
            'reference_residual_signed_log': a['residual_signed_log'],
            'continuation_residual_signed_log': b['residual_signed_log'],
            'residual_signed_log_shift': _as_float(b['residual_signed_log'], 'residual') -
                                         _as_float(a['residual_signed_log'], 'residual'),
            'reference_residual_class_max': a['residual_class_max'],
            'continuation_residual_class_max': b['residual_class_max'],
            'routing_unchanged': int(a['correction_admitted'] == b['correction_admitted']),
            'baseline_sign': a['baseline_sign'], 'baseline_value': a['baseline_value']
        })
    return records


def _make_group_transitions(ref, new, cases, split):
    if set(ref) != set(new):
        raise ValueError('V8_MATH_GROUP_SET_CHANGED')
    cases_by_group = defaultdict(list)
    for row in cases:
        cases_by_group[row['group']].append(row)
    records = []
    for key in sorted(ref):
        a, b = ref[key], new[key]
        transition = ('retained_complete' if a['complete'] and b['complete'] else
                      'lost_complete' if a['complete'] and not b['complete'] else
                      'new_complete' if not a['complete'] and b['complete'] else 'still_incomplete')
        lost = [c for c in cases_by_group[key] if c['transition'] == 'lost_correct']
        gained = [c for c in cases_by_group[key] if c['transition'] == 'newly_correct']
        records.append({
            'split': split, 'group': key, 'family': a['members'][0]['family'],
            'magnitude_band': a['members'][0]['magnitude_band'],
            'operand_band': a['members'][0]['operand_band'],
            'transition': transition,
            'reference_complete': a['complete'], 'continuation_complete': b['complete'],
            'reference_correct_count': a['correct_count'],
            'continuation_correct_count': b['correct_count'],
            'reference_triples': sum(a['triples'].values()),
            'continuation_triples': sum(b['triples'].values()),
            'reference_both_correct_pairs': a['both_correct_pairs'],
            'continuation_both_correct_pairs': b['both_correct_pairs'],
            'reference_disagreement_pairs': a['surface_disagreements'],
            'continuation_disagreement_pairs': b['surface_disagreements'],
            'reference_zero_misses': a['zero_misses'],
            'continuation_zero_misses': b['zero_misses'],
            'reference_nonzero_misses': a['nonzero_misses'],
            'continuation_nonzero_misses': b['nonzero_misses'],
            'lost_case_count': len(lost), 'new_case_count': len(gained),
            'lost_case_ids': ';'.join(c['id'] for c in lost),
            'lost_case_surfaces_and_signs': ';'.join(c['surface'] + ':' + c['target_sign_name'] for c in lost),
            'lost_case_causes': ';'.join(c['regression_kind'] for c in lost),
            'gained_case_ids': ';'.join(c['id'] for c in gained),
            'gained_case_surfaces_and_signs': ';'.join(c['surface'] + ':' + c['target_sign_name'] for c in gained),
        })
    return records


def _pair_transitions(ref, new, split):
    result = []
    for key in sorted(ref):
        aa, bb = ref[key]['by_surface'], new[key]['by_surface']
        surfaces = sorted(aa)
        for sign in (-1, 0, 1):
            r1, r2 = aa[surfaces[0]][sign], aa[surfaces[1]][sign]
            n1, n2 = bb[surfaces[0]][sign], bb[surfaces[1]][sign]
            previous = int(r1['correct']) and int(r2['correct'])
            present = int(n1['correct']) and int(n2['correct'])
            result.append({'split': split, 'group': key, 'target_sign': sign,
                'target_fraction': r1['target_fraction'],
                'surfaces': '+'.join(surfaces),
                'reference_pair_correct': int(previous), 'continuation_pair_correct': int(present),
                'transition': ('retained_pair' if previous and present else
                               'lost_pair' if previous and not present else
                               'gained_pair' if not previous and present else 'still_wrong_pair'),
                'reference_agreement': int(r1['predicted_sign'] == r2['predicted_sign']),
                'continuation_agreement': int(n1['predicted_sign'] == n2['predicted_sign']),
                'surface_1_sign_change': int(r1['predicted_sign'] != n1['predicted_sign']),
                'surface_2_sign_change': int(r2['predicted_sign'] != n2['predicted_sign'])})
    return result


def _triple_transitions(ref, new, split):
    records = []
    for key in sorted(ref):
        for surface in sorted(ref[key]['by_surface']):
            old = ref[key]['by_surface'][surface]
            now = new[key]['by_surface'][surface]
            old_ok = int(all(int(r['correct']) for r in old.values()))
            new_ok = int(all(int(r['correct']) for r in now.values()))
            lost = [sign for sign in (-1,0,1)
                    if int(old[sign]['correct']) and not int(now[sign]['correct'])]
            gained = [sign for sign in (-1,0,1)
                      if not int(old[sign]['correct']) and int(now[sign]['correct'])]
            records.append({'split': split, 'group': key, 'surface': surface,
                'reference_complete_triple': old_ok,
                'continuation_complete_triple': new_ok,
                'transition': ('retained_triple' if old_ok and new_ok else
                               'lost_triple' if old_ok and not new_ok else
                               'gained_triple' if not old_ok and new_ok else 'still_incomplete'),
                'reference_correct_signs': ';'.join(str(sign) for sign in (-1,0,1)
                                                     if int(old[sign]['correct'])),
                'continuation_correct_signs': ';'.join(str(sign) for sign in (-1,0,1)
                                                        if int(now[sign]['correct'])),
                'lost_signs': ';'.join(str(sign) for sign in lost),
                'gained_signs': ';'.join(str(sign) for sign in gained),
                'reference_zero_correct': int(old[0]['correct']),
                'continuation_zero_correct': int(now[0]['correct'])})
    return records


def _aggregate(cases, groups, pairs, triples):
    c = Counter(r['transition'] for r in cases)
    g = Counter(r['transition'] for r in groups)
    p = Counter(r['transition'] for r in pairs)
    tr = Counter(r['transition'] for r in triples)
    lost = [r for r in cases if r['transition'] == 'lost_correct']
    return {
        'cases': len(cases), 'groups': len(groups), 'matched_pairs': len(pairs),
        'case_transitions': dict(c), 'group_transitions': dict(g), 'matched_pair_transitions': dict(p), 'triple_transitions': dict(tr),
        'reference_correct': sum(r['reference_correct'] for r in cases),
        'continuation_correct': sum(r['continuation_correct'] for r in cases),
        'gross_lost_correct_cases': c['lost_correct'],
        'gross_gained_correct_cases': c['newly_correct'],
        'net_correct_case_change': c['newly_correct'] - c['lost_correct'],
        'reference_complete_triples': sum(r['reference_complete_triple'] for r in triples),
        'continuation_complete_triples': sum(r['continuation_complete_triple'] for r in triples),
        'reference_complete_six': sum(r['reference_complete'] for r in groups),
        'continuation_complete_six': sum(r['continuation_complete'] for r in groups),
        'reference_zero_misses': sum(int(r['reference_zero_miss']) for r in cases),
        'continuation_zero_misses': sum(int(r['continuation_zero_miss']) for r in cases),
        'lost_case_types': dict(Counter(r['regression_kind'] for r in lost)),
        'lost_case_interventions': dict(Counter(r['correction_transition'] for r in lost)),
        'lost_cases_with_unchanged_route': sum(r['routing_unchanged'] for r in lost),
        'lost_groups': [r['group'] for r in groups if r['transition'] == 'lost_complete'],
        'new_groups': [r['group'] for r in groups if r['transition'] == 'new_complete'],
    }


def _slices(cases):
    records = []
    for kind in ('surface', 'target_sign_name', 'family', 'magnitude_band',
                 'operand_band', 'surface_and_sign', 'surface_and_magnitude',
                 'surface_and_operand_band'):
        grouped = defaultdict(list)
        for r in cases:
            key = {'surface': r['surface'],
                'target_sign_name': r['target_sign_name'],
                'family': r['family'],
                'magnitude_band': r['magnitude_band'],
                'operand_band': r['operand_band'],
                'surface_and_sign': r['surface'] + '/' + r['target_sign_name'],
                'surface_and_magnitude': r['surface'] + '/' + r['magnitude_band'],
                'surface_and_operand_band': r['surface'] + '/' + r['operand_band']}[kind]
            grouped[key].append(r)
        for key in sorted(grouped):
            a = grouped[key]
            lost = sum(r['transition'] == 'lost_correct' for r in a)
            gained = sum(r['transition'] == 'newly_correct' for r in a)
            records.append({'split': a[0]['split'], 'dimension': kind, 'bucket': key,
                'total': len(a), 'reference_correct': sum(r['reference_correct'] for r in a),
                'continuation_correct': sum(r['continuation_correct'] for r in a),
                'lost_correct': lost, 'gained_correct': gained,
                'net_change': gained - lost,
                'gross_churn': gained + lost})
    return records


def _write_csv(path, rows):
    if not rows:
        raise ValueError('V8_EMPTY_OUTPUT_TABLE: ' + str(path))
    with Path(path).open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _observation(split, summary, cases, groups):
    observations = []
    lost_groups = [g for g in groups if g['transition'] == 'lost_complete']
    if lost_groups:
        observations.append({
            'finding': 'COMPLETE_GROUP_REGRESSION', 'epistemic_status': 'observed',
            'detail': f'{len(lost_groups)} previously complete six-case groups became incomplete; '
                      f'{sum(g["lost_case_count"] for g in lost_groups)} correct member answers were lost within them.'})
    if summary['gross_lost_correct_cases'] and summary['gross_gained_correct_cases']:
        observations.append({
            'finding': 'COMPETENCE_CHURN', 'epistemic_status': 'observed',
            'detail': f'{summary["gross_gained_correct_cases"]} wrong-to-correct and '
                      f'{summary["gross_lost_correct_cases"]} correct-to-wrong transitions; '
                      'net accuracy alone hides these opposing movements.'})
    if summary['reference_zero_misses'] != summary['continuation_zero_misses']:
        observations.append({
            'finding': 'ZERO_LINE_DRIFT', 'epistemic_status': 'observed',
            'detail': f'Exact-zero errors changed from {summary["reference_zero_misses"]} '
                      f'to {summary["continuation_zero_misses"]}.'})
    if summary['gross_lost_correct_cases'] and summary['lost_cases_with_unchanged_route'] == summary['gross_lost_correct_cases']:
        observations.append({
            'finding': 'ROUTER_UNCHANGED', 'epistemic_status': 'observed',
            'detail': 'All correct-to-wrong cases kept the identical verified routing admission; '
                      'the output decision changed within the specialist, not because the input switched routes.'})
    if split == 'previously_seen_test':
        observations.append({'finding': 'TEST_IS_PREVIOUSLY_EXAMINED',
            'epistemic_status': 'policy',
            'detail': 'These held-out observations have been examined before; they are diagnostic '
                      'and must not be used for fitting, checkpoint selection, or promotion.'})
    if not observations:
        observations.append({'finding': 'NO_OBSERVED_REGRESSION', 'epistemic_status': 'observed',
            'detail': 'No tracked competence regression occurred in this split.'})
    return observations


def _markdown(report):
    d = report['splits']['dev']['summary']
    t = report['splits']['previously_seen_test']['summary']
    def row(label, m):
        return (f'| {label} | {m["reference_correct"]}/{m["cases"]} | '
                f'{m["continuation_correct"]}/{m["cases"]} | '
                f'{m["gross_gained_correct_cases"]} | {m["gross_lost_correct_cases"]} | '
                f'{m["reference_complete_six"]} → {m["continuation_complete_six"]} | '
                f'{m["reference_zero_misses"]} → {m["continuation_zero_misses"]} |')
    lines = ['# NanoJev v8 — Mathematical competence transition audit', '',
        'Training-free comparison: v6 `dual_protected_unaligned` reference against '
        'v7 selected `uniform_continuation`.', '',
        '| Population | V6 correct | V7 correct | Gained cases | Lost cases | Complete six-case groups | Zero misses |',
        '|---|---:|---:|---:|---:|---:|---:|', row('DEV', d),
        row('Previously examined TEST (diagnostic only)', t), '',
        '## What changed', '']
    for split, label in [('dev', 'DEV'), ('previously_seen_test', 'Previously examined TEST')]:
        lines.append(f'### {label}')
        for obs in report['splits'][split]['observations']:
            lines.append(f'- **{obs["finding"]}** ({obs["epistemic_status"]}): {obs["detail"]}')
        lines.append('')
    lines += ['## Interpretation boundary', '',
        'The audit proves which predictions and complete groups were gained or lost, '
        'and correlates losses with zero/sign targets, prompt format, operand bands, '
        'classification margins, residual shifts, and admission decisions. It cannot '
        'establish which weights, gradients, or hidden features caused the shifts, '
        'because v7 recorded endpoint predictions rather than per-step activations.', '',
        'The original-task hard bypass remains unchanged. This tool does not train, '
        'edit model weights, change routing, or promote a checkpoint. Previously '
        'examined TEST is never used for fitting or selection.', '',
        'For exact failed group members, read `lost_groups_previously_seen_test.csv` '
        'and `case_transitions_previously_seen_test.csv`.', '']
    return '\n'.join(lines)


def analyze(v7_run, output):
    run = Path(v7_run).expanduser().resolve()
    dest = Path(output).expanduser().resolve()
    if not run.is_dir():
        raise FileNotFoundError('V8_V7_DIRECTORY_NOT_FOUND')
    if dest.exists():
        raise FileExistsError('V8_OUTPUT_ALREADY_EXISTS')
    if run == dest or run in dest.parents:
        raise ValueError('V8_OUTPUT_MUST_NOT_OVERWRITE_SOURCE_TREE')
    source_report, candidates = _check_report(run)
    inputs = [run / 'report.json']
    payload = {}
    for prefix, split in SPLITS:
        cases_by_arm = {}
        group_paths = {}
        for arm in (REFERENCE, CANDIDATE):
            cases_path = run / f'{arm}_cases_{prefix}.csv'
            groups_path = run / f'{arm}_groups_{prefix}.csv'
            inputs.extend([cases_path, groups_path])
            cases_by_arm[arm] = _load_cases(cases_path, split)
            group_paths[arm] = groups_path
        ref, new = cases_by_arm[REFERENCE], cases_by_arm[CANDIDATE]
        # Verify the two captures *before* constructing group aggregates.
        _verify_alignment(ref, new)
        atlas = {}
        for arm in (REFERENCE, CANDIDATE):
            group_map = _group_index(cases_by_arm[arm])
            _verify_group_csv(group_paths[arm], group_map)
            atlas[arm] = group_map
        old_groups, new_groups = atlas[REFERENCE], atlas[CANDIDATE]
        cases = _make_case_transitions(ref, new, prefix)
        groups = _make_group_transitions(old_groups, new_groups, cases, prefix)
        pairs = _pair_transitions(old_groups, new_groups, prefix)
        triples = _triple_transitions(old_groups, new_groups, prefix)
        summary = _aggregate(cases, groups, pairs, triples)
        # Reject incomplete or altered CSVs using the already-authored V7 report.
        for arm, map_ in [(REFERENCE, ref), (CANDIDATE, new)]:
            expected = candidates[arm]['dev' if prefix == 'dev' else
                'already_seen_test_diagnostic_only']
            predicted = sum(int(r['correct']) for r in map_.values())
            if (len(map_) != int(expected['all']['cases']) or
                    predicted != int(expected['all']['correct_class'])):
                raise ValueError('V8_CANDIDATE_REPORT_CASE_COUNT_MISMATCH')
            selected_groups = old_groups if arm == REFERENCE else new_groups
            complete = sum(g['complete'] for g in selected_groups.values())
            if (len(selected_groups) != int(expected['cross_format']['groups']) or
                    3 * len(selected_groups) !=
                    int(expected['cross_format']['paired_cases']) or
                    complete != int(expected['cross_format']['complete_six_case_groups'])):
                raise ValueError('V8_CANDIDATE_REPORT_GROUP_COUNT_MISMATCH')
        payload[prefix] = {'cases': cases, 'groups': groups, 'pairs': pairs, 'triples': triples,
                           'slices': _slices(cases), 'summary': summary,
                           'observations': _observation(prefix, summary, cases, groups)}
    input_hashes = [{'path': p.name, 'sha256': _sha256(p)} for p in inputs]
    report = {'schema': SCHEMA, 'status': 'DIAGNOSTIC_COMPLETE_NO_TRAINING',
        'v7_input': str(run), 'v7_schema': V7_SCHEMA,
        'v7_selected_candidate': source_report['selected_candidate'],
        'v6_selected_step': candidates[REFERENCE]['selected_step'],
        'v7_selected_step': candidates[CANDIDATE]['selected_step'],
        'synthetic_sha256': source_report['synthetic_sha256'],
        'frozen_lineage': source_report['lineage'],
        'input_sha256': input_hashes,
        'splits': {k: {'summary': v['summary'], 'observations': v['observations']}
                   for k, v in payload.items()},
        'limitations': {'previously_seen_test': 'DIAGNOSTIC_ONLY_NOT_VIRGIN',
            'causal_claim': 'OBSERVED_TRANSITIONS_NOT_GRADIENT_CAUSAL_PROOF',
            'case_oracle_source': 'VERIFIED_MATCHED_V7_CAPTURED_CASES',
            'training': False, 'checkpoint_selection': False,
            'promotion': False, 'new_model_weights': False,
            'original_protection': 'UNCHANGED_CAPTURE_BOUND_V6_HARD_BYPASS'}}
    dest.mkdir(parents=True, exist_ok=False)
    for prefix, value in payload.items():
        _write_csv(dest / f'case_transitions_{prefix}.csv', value['cases'])
        _write_csv(dest / f'group_transitions_{prefix}.csv', value['groups'])
        _write_csv(dest / f'pair_transitions_{prefix}.csv', value['pairs'])
        _write_csv(dest / f'triple_transitions_{prefix}.csv', value['triples'])
        _write_csv(dest / f'slice_transitions_{prefix}.csv', value['slices'])
        lost = [r for r in value['groups'] if r['transition'] == 'lost_complete']
        if lost:
            _write_csv(dest / f'lost_groups_{prefix}.csv', lost)
        # Even when there are no complete group losses, surface group cases remain readable.
    (dest / 'report.json').write_text(json.dumps(report, indent=2,
        sort_keys=True, allow_nan=False) + '\n', encoding='utf-8')
    (dest / 'summary.md').write_text(_markdown(report), encoding='utf-8')
    print('V8_TRANSITIONS ' + json.dumps({
        'status': report['status'], 'v6_step': report['v6_selected_step'],
        'v7_step': report['v7_selected_step'], 'promotion': False,
        'dev': payload['dev']['summary'],
        'previously_seen_test_diagnostic_only': payload['previously_seen_test']['summary'],
        'report': str(dest / 'report.json')}, sort_keys=True), flush=True)
    return report


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=('compare',), default='compare')
    p.add_argument('--v7-run', type=Path, required=True,
                   help='V7 repair report directory containing all generated case/group atlases')
    p.add_argument('--output', type=Path, required=True,
                   help='New output directory; source files are read-only')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    analyze(args.v7_run, args.output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
