#!/usr/bin/env python3
"""V31 full fresh-forward GAMER numerical regression; no training or promotion.

Regenerate the exact 624-case V27 captured evaluation corpus (288 TRAIN,
144 DEV, 192 DEV stress) from the original fingerprint-verified source.
The expected answer is derived independently from the source's certified
operand oracle and is used ONLY for scoring, never as a prediction input.
V29 selection and V30 token centroids operate on FRESH base Qwen activations.

This command does not confer production authority on either the Wide head or
this diagnostic pipeline. Strict pass requires every one of the 624 cases.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from fractions import Fraction
import json
from pathlib import Path

from tools import nanojev_gamer_v1_candidate_span_causality_v27 as v27
from tools import nanojev_gamer_v1_v30_live_inference as live
from tools import nanojev_gamer_v1_sign_information_probe as probe
from tools import nanojev_gamer_v1_operand_causality_v23 as v23
from tools import nanojev_gamer_v1_operand_token_causality_v24 as v24
from tools import nanojev_gamer_v1_handoff as gamer
from tools import nanojev_gamer_v1_precision_continuation_v19 as v19

SCHEMA = 'nanojev.gamer.v1.full-live-regression.v31'
EXPECTED = {'protected_train': 288, 'original_dev': 144,
            'stress_altered_wording': 48, 'stress_distractor_prefix': 48,
            'stress_novel_operands': 48, 'stress_role_reordered': 48}
SIGN_COUNTS = {-1: 208, 0: 208, 1: 208}


def _require(condition, code):
    if not condition:
        raise ValueError('V31_REGRESSION_' + code)


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _scope_kind(e):
    if e['scope'] == 'train' and e['kind'] == 'original':
        return 'protected_train'
    if e['scope'] == 'dev' and e['kind'] == 'original':
        return 'original_dev'
    if e['scope'] == 'dev_stress' and e['kind'] in ('altered_wording',
                  'distractor_prefix', 'novel_operands', 'role_reordered'):
        return 'stress_' + e['kind']
    raise ValueError('V31_REGRESSION_UNKNOWN_SCOPE_OR_KIND')


def regenerate(input_dir, v27_run, v29_run, v30_run):
    """Independently recover the corpus, prohibit a TEST case or DEV fitting."""
    r27 = Path(v27_run) / 'report.json'
    r29 = Path(v29_run) / 'report.json'
    r30 = Path(v30_run) / 'report.json'
    for path in (r27, r29, r30):
        _require(path.is_file(), 'SOURCE_REPORT_MISSING_' + path.name)
    a, b, c = map(_read, (r27, r29, r30))
    _require(c.get('v27_report_sha256') == v19._sha(r27)
             and c.get('v29_report_sha256') == v19._sha(r29)
             and a.get('frozen_lineage') == b.get('frozen_lineage'),
             'V27_V29_V30_LINEAGE')
    _require(a.get('schema') == v27.SCHEMA and
             a.get('status') == 'READ_ONLY_ALL_CANDIDATES_NO_PROMOTION' and
             a.get('test_cases_forwarded') == 0 and
             a.get('readout_fit_on_dev_or_test') is False and
             b.get('test_used_for_fitting') is False and
             c.get('no_dev_fitting_or_checkpoint_selection') is True and
             c.get('no_test_used') is True and
             c.get('all_gates_met') is True,
             'REQUIRED_SOURCE_CERTIFICATION_MISSING')
    info, _ = probe._verify_archive(Path(input_dir) / probe.CAPTURE_NAME,
                                    require_source=True)
    _require(info['lineage'] == a['frozen_lineage'], 'DATASET_FROZEN_LINEAGE')
    train = v23._operand_rows(info, 'train')
    dev = v23._operand_rows(info, 'dev')
    rows = train + dev
    prompts = v24._indexed_prompts(info)
    _require(len(train) == 288 and len(dev) == 144 and
             len(rows) == 432, 'CERTIFIED_SOURCE_CASE_COUNTS')
    hold = set(v24._group_fit_split(train)[1])
    stress_limit = a.get('stress_limit_per_condition')
    _require(type(stress_limit) is int and stress_limit == 48,
             'STRESS_LIMIT_CHANGED')
    entries = v27._entries(rows, prompts, len(train), stress_limit)
    _require(a.get('capture_cases') == len(entries) ==
             c.get('v29_replayed_cases') == 624,
             'CAPTURE_REPLAY_CASE_COUNT_MISMATCH')
    deck = []
    for i, e in enumerate(entries):
        _require(e['scope'] in ('train', 'dev', 'dev_stress'),
                 'UNSUPPORTED_OR_TEST_SCOPE')
        surface = e['surface']
        prompt = e['prompt']
        first, second = v27._truth_spans(prompt, surface)
        truth = Fraction(prompt[slice(*first)]) - Fraction(prompt[slice(*second)])
        _require(truth == e['target'], 'SOURCE_ORACLE_DISAGREES_WITH_TEXT')
        live._validate_prompt(prompt)  # checks the actual inference contract
        deck.append({'id': e['id'], 'prompt': prompt, 'expected': str(truth),
                     'scope_kind': _scope_kind(e), 'surface': surface,
                     'sign': int(truth > 0) - int(truth < 0),
                     'heldout_train': bool(i in hold) if i < len(train) else False,
                     'prompt_sha256': probe.sha_text(prompt)})
    _require(len({e['id'] for e in deck}) == 624, 'DUPLICATE_CASE_IDS')
    counts = Counter(x['scope_kind'] for x in deck)
    signs = Counter(x['sign'] for x in deck)
    _require(dict(counts) == EXPECTED and dict(signs) == SIGN_COUNTS,
             'SOURCE_CASE_OR_SIGN_BALANCE_CHANGED')
    _require(sum(x['heldout_train'] for x in deck) == 54,
             'HELDOUT_TRAIN_GROUPS_CHANGED')
    return deck, {'source_dataset_sha256': info['dataset_sha256'],
                  'v27_report_sha256': v19._sha(r27),
                  'v29_report_sha256': v19._sha(r29),
                  'v30_report_sha256': v19._sha(r30),
                  'corpus_counts': dict(counts),
                  'sign_counts': {str(k): signs[k] for k in (-1, 0, 1)},
                  'heldout_train_cases': 54}


def summarize(deck, live_report, provenance):
    """All scores from LIVE outputs, never from predictions copied from deck."""
    actual = live_report.get('cases', [])
    _require(len(actual) == len(deck) == 624 and
             [x['id'] for x in actual] == [x['id'] for x in deck],
             'LIVE_REPORT_MISSING_REORDERED_OR_EXTRA_CASES')
    by_kind = defaultdict(lambda: {'exact': 0, 'total': 0})
    by_sign = defaultdict(lambda: {'exact': 0, 'total': 0})
    by_surface = defaultdict(lambda: {'exact': 0, 'total': 0})
    failed = []
    for source, result in zip(deck, actual):
        _require('exact' in result and 'expected' in result and
                 str(Fraction(result['expected'])) == source['expected'] and
                 isinstance(result.get('oracle_used_for_prediction'), bool) and
                 result['oracle_used_for_prediction'] is False and
                 result.get('calculation_authority') == 'exact_fraction_postprocess',
                 'SCORING_INTEGRITY')
        truth = Fraction(source['expected'])
        scored = (Fraction(result['difference']) == truth) and result['exact'] is True
        for aggregate, key in ((by_kind, source['scope_kind']),
                               (by_sign, str(source['sign'])),
                               (by_surface, source['surface'])):
            aggregate[key]['total'] += 1
            aggregate[key]['exact'] += int(scored)
        if not scored:
            failed.append({'id': source['id'], 'scope_kind': source['scope_kind'],
                           'sign': source['sign'], 'surface': source['surface'],
                           'expected': source['expected'],
                           'observed': result['difference'],
                           'selected_spans': result.get('selected_spans'),
                           'predicted_token_ids': result.get('predicted_token_ids')})
    gates = {'live_report_completed':
                 live_report.get('status') == 'V31_READ_ONLY_LIVE_INFERENCE_COMPLETE',
             'all_624_exact': len(failed) == 0,
             'all_192_stress_exact': all(by_kind[k]['exact'] == by_kind[k]['total'] == 48
                        for k in EXPECTED if k.startswith('stress_')),
             'all_144_original_dev_exact': by_kind['original_dev'] ==
                                             {'exact': 144, 'total': 144},
             'all_288_protected_train_exact': by_kind['protected_train'] ==
                                             {'exact': 288, 'total': 288},
             'all_208_zero_exact': by_sign['0'] == {'exact': 208, 'total': 208},
             'all_208_positive_exact': by_sign['1'] == {'exact': 208, 'total': 208},
             'all_208_negative_exact': by_sign['-1'] == {'exact': 208, 'total': 208},
             'frozen_production_unchanged': live_report.get('production_modified') is False
                       and live_report.get('promotion_authorized') is False
                       and live_report.get('wide_head_untouched') is True,
             'base_model_activations': live_report.get('activation_source') ==
                       'frozen_base_qwen_layer18_no_lora_adapter'}
    passed = all(gates.values())
    return {'schema': SCHEMA,
            'status': 'PASS_LIVE_NUMERICAL_REGRESSION_NO_PROMOTION'
                      if passed else 'FAIL_LIVE_NUMERICAL_REGRESSION_NO_PROMOTION',
            'all_gates_met': passed, 'acceptance_gates': gates,
            'exact': 624 - len(failed), 'total': 624,
            'by_scope_kind': dict(sorted(by_kind.items())),
            'by_sign': dict(sorted(by_sign.items())),
            'by_surface': dict(sorted(by_surface.items())),
            'failed_cases': failed, 'provenance': provenance,
            'live_report_sha256': None,
            'production_modified': False, 'promotion_authorized': False,
            'scope': 'V27/V29_CATALOG_SIGNED_DIFFERENCE_ONLY',
            'limits': ['No TEST cases, no Space Captain end-to-end decision proof',
                       'Exact subtraction is a deterministic calculator, not a learned head',
                       'The Wide head remains operational authority']}


def run(args):
    output = Path(args.output)
    _require(not output.exists(), 'OUTPUT_ALREADY_EXISTS')
    # Complete upstream verification before creating any artifact.
    deck, provenance = regenerate(args.input, args.v27_run, args.v29_run, args.v30_run)
    output.mkdir(parents=True, exist_ok=False)
    deck_path = output / 'full_live_regression_cases.jsonl'
    deck_path.write_text(''.join(json.dumps(x, separators=(',', ':'),
        ensure_ascii=False) + '\n' for x in deck), encoding='utf-8')
    live_path = output / 'live_inference_report.json'
    flags = ['v30-live', '--output', args.run,
             '--v27-run', args.v27_run, '--v29-run', args.v29_run,
             '--v30-run', args.v30_run, '--v30-cases-jsonl', str(deck_path),
             '--v30-report', str(live_path), '--device', args.device,
             '--max-length', str(args.max_length)]
    options = gamer.parser().parse_args(flags)
    gamer.validate_flags(options)
    # The actual full CUDA inference takes place here. No mock is installed.
    report = live.run(options)
    summary = summarize(deck, report, provenance)
    summary['live_report_sha256'] = v19._sha(live_path)
    summary_path = output / 'acceptance_report.json'
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n',
                            encoding='utf-8')
    print('GAMER_V31_FULL_LIVE_REGRESSION ' + json.dumps({
        'status': summary['status'], 'exact': summary['exact'],
        'total': summary['total'], 'failed': len(summary['failed_cases']),
        'acceptance': str(summary_path), 'production_modified': False},
        sort_keys=True), flush=True)
    if not summary['all_gates_met']:
        raise SystemExit(1)
    return summary


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', required=True, help='Existing frozen Gamer run')
    p.add_argument('--input', required=True, help='Existing sign-probe directory')
    p.add_argument('--v27-run', required=True)
    p.add_argument('--v29-run', required=True)
    p.add_argument('--v30-run', required=True)
    p.add_argument('--output', required=True, help='A NEW output directory')
    p.add_argument('--device', default='cuda')
    p.add_argument('--max-length', type=int, default=1024)
    return p


def main(argv=None):
    run(parser().parse_args(argv))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
