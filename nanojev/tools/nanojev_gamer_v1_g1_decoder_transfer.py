"""Read-only G1-0007/0008 decoder transfer audit on the same development split.

Runs the predeclared `length_linear` variant from G1-0009 across four immutable
checkpoint lineages. No training, stage unlock, promotion or challenge access.
A paired checkpoint's own diagnostic is rechecked against its frozen logits.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from tools.nanojev_gamer_v1_g1_decoder_frob import (
    EXPECTED_G1_0007_HEAD, SCHEMA as FROB_SCHEMA, run as frob_run,
)

SCHEMA = 'nanojev.gamer.graph.g1.frozen-decoder-transfer-dev.v1'
ARMS = (
    ('g1_0007', 'graph-g1-retention-retained-0007',
     'graph-g1-retention-diagnosis-0007', None),
    ('control', 'graph-g1-miss-control-0008',
     'graph-g1-miss-diagnosis-control-0008', (0.0, 0.0)),
    ('structured', 'graph-g1-miss-structured-0008',
     'graph-g1-miss-diagnosis-structured-0008', (0.4, 0.0)),
    ('null_focused', 'graph-g1-miss-null-focused-0008',
     'graph-g1-miss-diagnosis-null-focused-0008', (0.0, 1.0)),
)


def _manifest(checkpoint):
    path = checkpoint / 'manifest.json'
    if not path.is_file():
        raise FileNotFoundError(f'GAMER_G1_TRANSFER_MISSING_MANIFEST: {path}')
    value = json.loads(path.read_text(encoding='utf-8'))
    if (value.get('schema') != 'nanojev.gamer.graph.curriculum.v2.span'
            or value.get('stage') != 'G1'
            or value.get('status') != 'TRAINED_NOT_CERTIFIED'
            or value.get('production_promotion') is not False
            or value.get('train_limit') != 0):
        raise ValueError('GAMER_G1_TRANSFER_UNEXPECTED_CHECKPOINT_LINEAGE')
    return value


def _near(actual, expected):
    try:
        return math.isclose(float(actual), expected, rel_tol=0, abs_tol=1e-10)
    except (ValueError, TypeError):
        return False


def _preflight(root, output):
    if output.exists():
        raise FileExistsError('GAMER_G1_TRANSFER_IMMUTABLE_OUTPUT')
    dataset = root / 'graph-span-curriculum-0005.jsonl'
    if not dataset.is_file():
        raise FileNotFoundError(f'GAMER_G1_TRANSFER_MISSING_DATASET: {dataset}')
    from tools import nanojev_gamer_v1_backbone_first as backbone
    dataset_sha = backbone.sha256(dataset)
    entries = {}
    for name, checkpoint_name, diagnosis_name, objective in ARMS:
        checkpoint = root / checkpoint_name
        diagnosis = root / diagnosis_name
        manifest = _manifest(checkpoint)
        if not (diagnosis / 'summary.json').is_file() or not (diagnosis / 'misses.jsonl').is_file():
            raise FileNotFoundError(f'GAMER_G1_TRANSFER_MISSING_DIAGNOSIS: {diagnosis}')
        if (manifest.get('dataset_sha256') != dataset_sha
                or manifest.get('source_adapter_sha256') is None
                or manifest.get('head_sha256') is None):
            raise ValueError('GAMER_G1_TRANSFER_DATASET_OR_ADAPTER_LINEAGE')
        if name == 'g1_0007':
            if manifest['head_sha256'] != EXPECTED_G1_0007_HEAD:
                raise ValueError('GAMER_G1_TRANSFER_UNEXPECTED_PROTECTED_PARENT')
        else:
            expected_structured, expected_hard_null = objective
            if (manifest.get('g1_refined_head_sha256') != EXPECTED_G1_0007_HEAD
                    or not _near(manifest.get('structured_weight'), expected_structured)
                    or not _near(manifest.get('hard_null_weight'), expected_hard_null)
                    or not _near(manifest.get('parent_retention_weight'), 2.0)
                    or not _near(manifest.get('counterfactual_fraction'), .25)
                    or not _near(manifest.get('lr'), 3e-5)
                    or manifest.get('steps') != 100
                    or manifest.get('accum') != 4
                    or manifest.get('seed') != 47
                    or manifest.get('g1_family_balanced') is not True
                    or manifest.get('g1_controlled_exposure') is not True):
                raise ValueError(f'GAMER_G1_TRANSFER_TRAINING_MISMATCH: {name}')
        entries[name] = {'checkpoint': checkpoint, 'diagnosis': diagnosis,
                         'manifest': manifest}
    fingerprint = entries['g1_0007']['manifest']['source_adapter_sha256']
    if any(entry['manifest']['source_adapter_sha256'] != fingerprint
           for entry in entries.values()):
        raise ValueError('GAMER_G1_TRANSFER_SOURCE_ADAPTER_MISMATCH')
    return dataset, dataset_sha, fingerprint, entries


def _delta(current, reference):
    return {
        'exact_prompts': current['exact_prompts'] - reference['exact_prompts'],
        'correct_objects': current['correct'] - reference['correct'],
        'f1': round(current['f1'] - reference['f1'], 9),
        'precision': round(current['precision'] - reference['precision'], 9),
        'recall': round(current['recall'] - reference['recall'], 9),
        'correct_by_kind': {
            key: current['by_kind'][key]['correct'] - reference['by_kind'][key]['correct']
            for key in ('actor', 'action', 'quantity', 'outcome')
        },
    }


def _validate_reference(reference_report, report, manifest):
    reference = json.loads(Path(reference_report).read_text(encoding='utf-8'))
    if (reference.get('schema') != FROB_SCHEMA
            or reference.get('baseline_head_sha256') != EXPECTED_G1_0007_HEAD
            or reference.get('dataset_sha256') != manifest['dataset_sha256']
            or reference.get('source_adapter_sha256') != manifest['source_adapter_sha256']
            or reference.get('baseline_global') != report['baseline_global']
            or reference.get('variants', {}).get('length_linear', {}).get('metrics')
                != report['variants']['length_linear']['metrics']):
        raise ValueError('GAMER_G1_TRANSFER_0009_REFERENCE_DIVERGED')


def run(*, root, output, device='cuda', reference_report=None, max_length=2048,
        frob_executor=None):
    """Audit frozen 0009 linear decoder transfer; do not select for production."""
    root = Path(root)
    output = Path(output)
    dataset, dataset_sha, adapter_sha, entries = _preflight(root, output)
    executor = frob_executor or frob_run
    reports = {}
    for name, _, _, _ in ARMS:
        entry = entries[name]
        # Each individual experiment reopens the actual weights and fully
        # recomputes its own gold-relative diagnostic from the frozen scores.
        print(f'GAMER_G1_TRANSFER {name}: frozen decoder, dev only', flush=True)
        child_output = output / name
        executor(dataset=dataset, checkpoint=entry['checkpoint'],
                 baseline_diagnosis=entry['diagnosis'], output=child_output,
                 expected_head_sha256=entry['manifest']['head_sha256'],
                 device=device, max_length=max_length)
        report = json.loads((child_output / 'report.json').read_text(encoding='utf-8'))
        if (report.get('schema') != FROB_SCHEMA
                or report.get('baseline_head_sha256') != entry['manifest']['head_sha256']
                or report.get('dataset_sha256') != dataset_sha
                or report.get('source_adapter_sha256') != adapter_sha
                or report.get('cases', 0) < 1
                or report.get('no_training') is not True
                or report.get('original_decoder_unchanged') is not True
                or report.get('challenge_or_test_split_used') is not False
                or report.get('production_promotion') is not False
                or report.get('g2_unlocked') is not False):
            raise ValueError(f'GAMER_G1_TRANSFER_BAD_FROZEN_REPORT: {name}')
        reports[name] = report
        if name == 'g1_0007' and reference_report:
            _validate_reference(reference_report, report, entry['manifest'])
    cases = reports['g1_0007']['cases']
    if any(r['cases'] != cases for r in reports.values()):
        raise ValueError('GAMER_G1_TRANSFER_CASES_MISMATCH')
    protected = reports['g1_0007']['variants']['length_linear']['metrics']
    summary = {}
    for name, _, _, _ in ARMS:
        r = reports[name]
        baseline = r['baseline_global']
        linear = r['variants']['length_linear']
        m = linear['metrics']
        summary[name] = {
            'head_sha256': r['baseline_head_sha256'],
            'baseline_global': baseline,
            'linear': m,
            'decoder_gain_on_same_checkpoint': _delta(m, baseline),
            'difference_from_protected_g1_0007_linear': _delta(m, protected),
            'linear_recovered_overlap_misses': linear['recovered_original_overlap_misses'],
            'linear_lost_correct_objects': linear['lost_previously_correct_gold'],
            'linear_new_false_positives': linear['introduced_false_positives'],
            'linear_removed_false_positives': linear['removed_false_positives'],
            'improves_on_protected_g1_0007_linear': (
                name != 'g1_0007'
                and m['exact_prompts'] > protected['exact_prompts']
                and m['f1'] > protected['f1']
                and m['precision'] >= protected['precision'] - .02
                and all(m['by_kind'][k]['recall'] >= protected['by_kind'][k]['recall']
                        for k in ('actor', 'action', 'quantity', 'outcome'))
            ),
            'report_path': str(output / name / 'report.json'),
        }
    result = {
        'schema': SCHEMA,
        'status': 'DEVELOPMENT_FROZEN_DECODER_TRANSFER_ONLY_NOT_CERTIFICATION',
        'split': 'dev', 'cases': cases,
        'dataset_sha256': dataset_sha, 'source_adapter_sha256': adapter_sha,
        'decoder': 'length_linear', 'length_power': 1.0,
        'per_object_penalty': 0.0,
        'no_training': True, 'frozen_neural_scores': True,
        'same_decoder_for_all_checkpoints': True,
        'same_development_split_for_all_checkpoints': True,
        'reference_0009_checked': bool(reference_report),
        'reference_0009_report': str(reference_report) if reference_report else None,
        'arms': summary,
        'improved_development_arms': [name for name in ('control','structured','null_focused')
                                      if summary[name]['improves_on_protected_g1_0007_linear']],
        'selection_warning': 'Repeated development comparison; not independent generalization evidence',
        'challenge_or_test_split_used': False,
        'production_promotion': False, 'certification_authorized': False,
        'g2_unlocked': False,
    }
    (output / 'transfer_report.json').write_text(
        json.dumps(result, indent=2, sort_keys=True)+'\n', encoding='utf-8')
    return result
