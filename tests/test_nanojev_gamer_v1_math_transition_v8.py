"""V8 mathematical retention audit: exact case, pair, and group transitions."""
from __future__ import annotations

import copy
import csv
import json
from pathlib import Path

import pytest

from tools import nanojev_gamer_v1_math_transition_v8 as v8


def _write_csv(path, rows):
    with Path(path).open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def _make_cases(split, arm):
    rows = []
    for group in (0, 1):
        for surface in ('source_native', 'spine_difference'):
            for sign in (-1, 0, 1):
                # On the test split, v6 has one fully solved group, which
                # v7 loses after a single previously correct zero changes.
                if split == 'test':
                    if group == 0:
                        pred = sign if arm == 'v6_reference' else (-1 if surface == 'spine_difference' and sign == 0 else sign)
                    else:
                        pred = 0 if arm == 'v6_reference' else (sign if sign == 1 else 0)
                else:
                    if group == 0:
                        pred = (0 if sign else 1) if arm == 'v6_reference' else sign
                    else:
                        pred = 0 if arm == 'v6_reference' else (sign if sign < 0 else 0)
                key = f'{split}.offset.{group}/{surface}/{sign}'
                correct = int(pred == sign)
                base = 0
                rows.append({
                    'operand_center': str(group * 100), 'operand_max_abs': '101',
                    'operand_band': '100_to_1000',
                    'id': key, 'group': f'{split}.offset.{group}', 'split': split,
                    'family': 'signed_offset', 'surface': surface,
                    'prompt_sha256': f'{group+1:064x}' if surface == 'source_native' else f'{group+3:064x}',
                    'target_fraction': str(sign), 'target_sign': sign,
                    'target_sign_name': {-1:'negative',0:'zero',1:'positive'}[sign],
                    'magnitude_band': 'exact_zero' if sign == 0 else '0.05_to_1',
                    'baseline_sign': base, 'predicted_sign': pred,
                    'baseline_value': 0., 'predicted_value': pred * .4,
                    'correct': correct, 'original_correct': int(base == sign),
                    'zero_miss': int(sign == 0 and pred != 0),
                    'nonzero_as_zero': int(sign != 0 and pred == 0),
                    'wrong_direction': int(sign != 0 and pred == -sign),
                    'correction_admitted': 1, 'correction_applied': 1,
                    'decision_margin': 1.0 + .1 * pred,
                    'residual_signed_log': pred * .4,
                    'residual_class_max': abs(pred) * .3,
                })
    return rows


def _make_groups(case_rows):
    groups = v8._group_index({r['id']: {k: str(v) for k,v in r.items()} for r in case_rows})
    output=[]
    for key, g in sorted(groups.items()):
        members=g['members']
        output.append({'group': key, 'split': members[0]['split'], 'family': 'signed_offset',
            'surfaces': 'source_native+spine_difference', 'magnitude_band':'0.05_to_1',
            'positive_offset_fraction': '1',
            'correct_cases': g['correct_count'], 'zero_misses': g['zero_misses'],
            'nonzero_misses': g['nonzero_misses'],
            'reversal_miss': int(g['nonzero_misses'] > 0),
            'surface_disagreement_pairs':g['surface_disagreements'],
            'both_correct_pairs':g['both_correct_pairs'],
            'left_complete_triple':g['triples']['source_native'],
            'right_complete_triple':g['triples']['spine_difference'],
            'complete_six':g['complete'], 'all_corrections_denied':0})
    return output


def _fixture(tmp_path):
    run=tmp_path/'v7'
    run.mkdir()
    candidates=[]
    for arm in ('v6_reference','uniform_continuation'):
        candidate={'id':arm,'selected_step': 180 if arm == 'v6_reference' else 210,
            'original_dev_retention':{'status':'PASS'}, 'promotion_authorized':False}
        for label, split in [('dev', 'dev'), ('previously_seen_test', 'test')]:
            cases=_make_cases(split,arm)
            groups=_make_groups(cases)
            _write_csv(run/f'{arm}_cases_{label}.csv',cases)
            _write_csv(run/f'{arm}_groups_{label}.csv',groups)
            expected={'all':{'cases':len(cases),'correct_class':sum(c['correct'] for c in cases)},
                      'cross_format':{'groups':len(groups),'paired_cases':3*len(groups),
                          'complete_six_case_groups':sum(g['complete_six'] for g in groups)}}
            candidate['dev' if label == 'dev' else 'already_seen_test_diagnostic_only']=expected
        candidates.append(candidate)
    report={'schema':v8.V7_SCHEMA,'mode':'repair',
        'status':'EXPERIMENT_COMPLETE_NO_PROMOTION',
        'reference_arm_verified':'dual_protected_unaligned',
        'selected_candidate':'uniform_continuation',
        'original_task_protection':'EXACT_V6_HARD_BYPASS_UNCHANGED',
        'production_weights_modified':False, 'production_promotion':False,
        'original_dev_used_for_fitting':False, 'synthetic_test_used_for_fitting':False,
        'v6_reference_selected_step':180,
        'synthetic_sha256':'a'*64,'lineage':{'source':'fixture'},'candidates':candidates}
    (run/'report.json').write_text(json.dumps(report),encoding='utf-8')
    return run


def test_group_and_case_attribution_recovers_lost_test_group_and_new_dev_group(tmp_path):
    run=_fixture(tmp_path)
    report=v8.analyze(run,tmp_path/'audit')
    dev=report['splits']['dev']['summary']
    test=report['splits']['previously_seen_test']['summary']
    assert dev['reference_complete_six']==0 and dev['continuation_complete_six']==1
    assert test['reference_complete_six']==1 and test['continuation_complete_six']==0
    assert test['gross_lost_correct_cases']==1
    assert test['gross_gained_correct_cases']==2
    assert test['net_correct_case_change']==1
    assert test['lost_case_types']=={'exact_zero_became_nonzero':1}
    assert test['lost_cases_with_unchanged_route']==1
    assert test['lost_groups']==['test.offset.0']
    assert test['reference_complete_triples'] > test['continuation_complete_triples']
    assert (tmp_path/'audit/triple_transitions_previously_seen_test.csv').exists()
    assert report['limitations']['training'] is False
    assert report['limitations']['promotion'] is False
    assert report['limitations']['previously_seen_test']=='DIAGNOSTIC_ONLY_NOT_VIRGIN'
    assert (tmp_path/'audit/lost_groups_previously_seen_test.csv').exists()
    with (tmp_path/'audit/lost_groups_previously_seen_test.csv').open(newline='') as f:
        lost=list(csv.DictReader(f))
    assert len(lost)==1
    assert lost[0]['lost_case_surfaces_and_signs']=='spine_difference:zero'
    assert lost[0]['lost_case_causes']=='exact_zero_became_nonzero'
    assert 'Zero misses' in (tmp_path/'audit/summary.md').read_text()
    assert len(report['input_sha256'])==9
    with pytest.raises(FileExistsError):
        v8.analyze(run,tmp_path/'audit')


def test_swapped_target_and_changed_router_refused(tmp_path):
    run=_fixture(tmp_path)
    path=run/'uniform_continuation_cases_dev.csv'
    with path.open(newline='') as f: rows=list(csv.DictReader(f))
    rows[0]['target_fraction']='-2'
    _write_csv(path,rows)
    with pytest.raises(ValueError,match='UNMATCHED_CAPTURE'):
        v8.analyze(run,tmp_path/'bad1')
    assert not (tmp_path/'bad1').exists()
    rows[0]['target_fraction']='-1'
    rows[0]['correction_admitted']='0'
    _write_csv(path,rows)
    with pytest.raises(ValueError,match='ROUTE_SURFACE_MISMATCH'):
        v8.analyze(run,tmp_path/'bad2')


def test_corrupted_aggregate_and_missing_case_refused(tmp_path):
    run=_fixture(tmp_path)
    groupfile=run/'uniform_continuation_groups_previously_seen_test.csv'
    with groupfile.open(newline='') as f: rows=list(csv.DictReader(f))
    rows[0]['complete_six']='1'
    _write_csv(groupfile,rows)
    with pytest.raises(ValueError,match='GROUP_ATLAS_MISMATCH'):
        v8.analyze(run,tmp_path/'bad1')
    rows[0]['complete_six']='0'
    _write_csv(groupfile,rows)
    casefile=run/'uniform_continuation_cases_previously_seen_test.csv'
    with casefile.open(newline='') as f: cases=list(csv.DictReader(f))
    _write_csv(casefile,cases[:-1])
    with pytest.raises(ValueError,match='CASE_ID_SET_CHANGED'):
        v8.analyze(run,tmp_path/'bad2')


def test_no_promotion_or_unverified_selection_and_invalid_claim_refused(tmp_path):
    run=_fixture(tmp_path)
    path=run/'report.json'
    for field, value, error in [('selected_candidate','zero_reversal_focused','LINEAGE_OR_SELECTION'),
                                ('production_promotion',True,'LINEAGE_OR_SELECTION'),
                                ('original_dev_used_for_fitting',True,'LINEAGE_OR_SELECTION')]:
        report=json.loads(path.read_text())
        modified=copy.deepcopy(report)
        modified[field]=value
        path.write_text(json.dumps(modified))
        with pytest.raises(ValueError,match=error):
            v8.analyze(run,tmp_path/('bad'+field))
        path.write_text(json.dumps(report))


def test_exact_sign_validation_and_fail_closed_protection(tmp_path):
    run=_fixture(tmp_path)
    source=run/'v6_reference_cases_dev.csv'
    with source.open(newline='') as f: rows=list(csv.DictReader(f))
    rows[0]['correct']='0' if rows[0]['correct']=='1' else '1'
    _write_csv(source,rows)
    with pytest.raises(ValueError,match='INTERNAL_CASE_CLASSIFICATION'):
        v8.analyze(run,tmp_path/'bad1')
    rows[0]['correct']='1' if rows[0]['correct']=='0' else '0'
    rows[0]['correction_admitted']='0'
    _write_csv(source,rows)
    with pytest.raises(ValueError,match='ROUTE_SURFACE_MISMATCH'):
        v8.analyze(run,tmp_path/'bad2')


def test_parser_requires_source_and_output():
    with pytest.raises(SystemExit):
        v8.parser().parse_args([])
