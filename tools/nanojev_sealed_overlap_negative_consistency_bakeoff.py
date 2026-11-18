#!/usr/bin/env python3
"""Frozen Qwen: diagnose residual negative duties and independently validate six slots.

Old 600 documents are a *previously inspected diagnostic* only. The fresh
17-kind, 24-group holdout uses new templates and semantic pairs; selection
never consults its labels. Baseline = scope + polarity grounding, unchanged.

The consensus arm abstains on unsupported/discordant observed slots. The
repair arm overwrites fields only from *unambiguous, complete literal text
anchors*. Its success measures restricted-language text grounding, NOT Qwen
language generalization. Gold, template IDs and acceptance labels are never
supplied to any inference function. No model weight/production mutations.

Run from repo root:
  & $py -m tools.nanojev_sealed_overlap_negative_consistency_bakeoff --device cuda \
    --output runtime/reports/sealed-overlap-negative-consistency-0001.json
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import itertools
import json
from pathlib import Path
import random
import re
from string import Formatter
from typing import Callable

from tools import nanojev_sealed_overlap_information_smoke as smoke
from tools import nanojev_sealed_overlap_polarity_layers_frob as layers
from tools import nanojev_sealed_overlap_relation_binding_bakeoff as binding
from tools import nanojev_sealed_overlap_language_stress_bakeoff as stress
from tools import nanojev_sealed_overlap_semantic_interface_bakeoff as interface
from tools import nanojev_sealed_overlap_semantic_interface_repair_bakeoff as repair
from tools import nanojev_sealed_overlap_lexicon_blind_bakeoff as blind
from tools import nanojev_sealed_overlap_scope_obligation_bakeoff as scope

SCHEMA = 'nanojev.sealed-overlap.negative-consistency-bakeoff.v1'
STATUS = 'COMPLETE_NEGATIVE_CONSISTENCY_DIAGNOSTIC_NO_SEAL_NO_PROMOTION'
FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'nanojev_overlap_negative_consistency_v1.json'
PRIOR_REPORT = Path('runtime/reports/sealed-overlap-scope-obligation-0001.json')
ARMS = ('scope_baseline', 'evidence_consensus', 'evidence_repair')
POLICIES = {'supported', 'abstain'}
TRUTHS = {'positive', 'negative', 'positive_scope_not', 'negative_scope_not', 'indeterminate'}


def load_fixture(path: Path, base: dict, st: dict, iface: dict, rf: dict, bf: dict, sf: dict) -> dict:
    f = json.loads(path.read_text(encoding='utf8'))
    ch = f.get('challenges')
    if f.get('schema') != 'nanojev.sealed-overlap.negative-consistency-fixture.v1' or f.get('document_delimiter') != ' || ' or not isinstance(ch, list) or len(ch) != 17:
        raise ValueError('NEGCONS_FIXTURE_SCHEMA_INVALID')
    prior = set(sum((base['templates'][k] for k in ('train', 'selection', 'final')), []))
    prior.update(st['selection_templates'] + st['final_templates'])
    prior.update(iface['augmentation_templates'] + iface['fresh_templates'])
    prior.update(rf['targeted_training_templates'] + rf['new_holdout_templates'])
    prior.update(x['template'] for fixture in (bf, sf) for x in fixture['challenges'])
    seen = set()
    fields_allowed = {'actor', 'recipient', 'asset', 'condition', 'consequence', 'other_asset', 'other_consequence', 'other_actor'}
    for c in ch:
        tpl = c.get('template')
        if not isinstance(tpl, str) or tpl in prior or tpl in seen or ' || ' in tpl:
            raise ValueError('NEGCONS_TEMPLATE_REUSE_OR_LEAK')
        seen.add(tpl)
        fields = {name for _, name, _, _ in Formatter().parse(tpl) if name is not None}
        if c.get('policy') not in POLICIES or c.get('truth') not in TRUTHS or not {'actor', 'recipient', 'asset', 'condition', 'consequence'}.issubset(fields) or fields - fields_allowed:
            raise ValueError('NEGCONS_FIELDS_OR_LABELS_INVALID')
        if (c['policy'] == 'supported') != (c['truth'] != 'indeterminate'):
            raise ValueError('NEGCONS_EXPECTATION_CONFLICT')
    if len({c['kind'] for c in ch}) != len(ch) or sum(c['policy'] == 'supported' for c in ch) != 9:
        raise ValueError('NEGCONS_CHALLENGE_SET_INVALID')
    return f


def validate_prior(path: Path, paths: dict[str, Path]) -> dict:
    p = json.loads(path.read_text(encoding='utf8'))
    ag = p.get('fresh', {}).get('scope_with_polarity_grounding', {}).get('aggregate', {})
    if (p.get('schema') != scope.SCHEMA or p.get('status') != scope.STATUS
        or p.get('acceptance', {}).get('no_model_weight_updates') is not True
        or p.get('acceptance', {}).get('no_final_based_model_selection') is not True
        or p.get('design', {}).get('view') != 'final:mean'
        or p.get('design', {}).get('frozen_training_examples') != 648
        or ag.get('documents') != 600 or ag.get('supported_accepted') != 216
        or ag.get('unsafe_accepted') != 0 or ag.get('false_confident_wrong') != 6
        or any(p.get('sources', {}).get(key) != sha256(value.read_bytes()).hexdigest()
               for key, value in paths.items())):
        raise ValueError('NEGCONS_PRIOR_REPORT_INVALID')
    return p


def make_fresh(data: dict, base: dict, st: dict, iface: dict, rf: dict, bf: dict, sf: dict,
               fixture: dict, *, seed: int, groups: int) -> list[dict]:
    if groups < 8 or groups % 8:
        raise ValueError('NEGCONS_GROUPS_INVALID')
    old_scope = scope.make_holdout(data, base, st, iface, rf, bf, sf, seed=seed, groups=24)
    old_blind = blind.make_holdout(data, base, st, iface, rf, bf, seed=seed, groups=24)
    used = {tuple(sorted(tuple(a) for a in row['atoms']))
            for ph in ('selection', 'final') for row in data[ph] if row['kind'] == 'base'}
    used.update(tuple(sorted(tuple(a) for a in r['atoms']))
                for r in repair.make_fresh(data, base, iface, st, rf, seed=seed, groups=24) if r['kind'] == 'base')
    used.update(tuple(sorted(tuple(a) for a in r['truth_atoms']))
                for r in old_scope if r['kind'] == 'operative_affirm')
    used.update(tuple(sorted(tuple(a) for a in r['truth_atoms']))
                for r in old_blind if r['kind'] == 'known_affirm')
    training = sorted({tuple(r['atom']) for r in data['train']})
    unseen = sorted(set(itertools.product(*binding.values(base))) - set(training))
    rng = random.Random(seed + 723107)
    out = []
    for g in range(groups):
        pool = training if g % 2 == 0 else unseen
        for _ in range(30000):
            a, b = rng.sample(pool, 2)
            pair = tuple(sorted((a, b)))
            if pair not in used and all(a[i] != b[i] for i in range(5)):
                used.add(pair)
                break
        else:
            raise ValueError('NEGCONS_PAIR_EXHAUSTED')
        fields = dict(zip(binding.FIELDS, a))
        for name, key in [('other_asset', 'assets'), ('other_consequence', 'consequences'), ('other_actor', 'actors')]:
            src = {'other_asset': 2, 'other_consequence': 4, 'other_actor': 0}[name]
            fields[name] = next(value for value in base[key] if value != a[src])
        second = repair.render(b, rf, 'new_holdout_templates', 0)
        for spec in fixture['challenges']:
            first = spec['template'].format(**fields)
            truth = spec['truth']
            atom = list(a)
            if truth != 'indeterminate':
                atom[-1] = 1 if truth.startswith('positive') else -1
                if truth.endswith('scope_not'):
                    atom[3] = f'(not {a[3]})'
                gold = [tuple(atom), b]
            else:
                gold = None
            out.append({'id': f'negcons:{g:03d}:{spec["kind"]}', 'kind': spec['kind'],
                        'group_id': g, 'policy': spec['policy'], 'truth_atoms': gold,
                        'text': binding.document([first, second])})
    if len({x['text'] for x in out}) != len(out):
        raise ValueError('NEGCONS_DUPLICATE_HOLDOUT')
    if {r['text'] for r in out} & {r['text'] for r in old_scope + old_blind}:
        raise ValueError('NEGCONS_PRIOR_HOLDOUT_OVERLAP')
    return out


def _literal_matches(text: str, values: list[str]) -> list[str]:
    return [x for x in values if re.search(r'(?<![A-Za-z])' + re.escape(x) + r'(?![A-Za-z])', text, re.I)]


def observe_clause_fields(clause: str, base: dict, refs: list[str], antecedent: str | None) -> tuple[dict, tuple[str, ...]]:
    """Text-only, role-anchored, complete six-field evidence. Never receives gold.

    Recognizes canonical literal slots in a restricted authored grammar. No
    semantic inference from absent or contradictory text is permitted.
    """
    fields = {}; issues = []
    roles, role_problem = repair.observed_roles(clause, refs, antecedent, base)
    if role_problem:
        issues.append('ROLES_' + role_problem)
    for key in ('actor', 'recipient'):
        if key in roles:
            fields[key] = roles[key]
        else:
            issues.append('ROLE_' + key.upper() + '_MISSING')
    for key in ('asset', 'consequence'):
        values = _literal_matches(clause, base[key + 's'])
        if len(values) == 1:
            fields[key] = values[0]
        else:
            issues.append(key.upper() + '_MISSING_OR_MULTIPLE')
    condition, condition_problem = repair.observed_condition(clause, base)
    if condition_problem:
        issues.append('CONDITION_' + condition_problem)
    if condition is None:
        found = _literal_matches(clause, base['conditions'])
        if len(found) == 1:
            condition = found[0]
        else:
            issues.append('CONDITION_MISSING_OR_MULTIPLE')
    # One parenthesized NOT must not be counted as an unnegated condition.
    if condition is not None:
        fields['condition'] = condition
    sign, sign_issues = scope.clause_scope_polarity(clause)
    if sign_issues or sign is None:
        issues.extend(sign_issues or ('POLARITY_UNGROUNDED',))
    else:
        fields['polarity'] = sign
    return fields, tuple(sorted(set(issues)))


def grounded_document(document: str, predictions: list[tuple], base: dict, refs: list[str], *, repair_mode: bool) -> tuple[bool, list[tuple], tuple[str, ...], dict]:
    """No label access. Consensus checks agreement; repair overlays all six slots.

    Abstains unless every slot has a unique text-evidence binding. The grounder
    is deterministic and independent of the Qwen head; this is intentionally
    measured as a separate, grammar-specific intervention.
    """
    ok, gate_reasons, signs = scope.scope_gate(document, base, refs)
    if not ok:
        return False, predictions, gate_reasons, {'observed': [], 'disagreements': []}
    clauses = binding.split_document(document)
    if len(clauses) != len(predictions):
        return False, predictions, ('CLAUSE_COUNT_MISMATCH',), {'observed': [], 'disagreements': []}
    observed = []; revised = []; issues = []; differences = []
    prior_actor = None
    for i, (clause, atom) in enumerate(zip(clauses, predictions)):
        fields, problems = observe_clause_fields(clause, base, refs, prior_actor)
        observed.append(fields)
        issues.extend('CLAUSE_%d_' % i + p for p in problems)
        if not problems:
            expected = tuple(fields[k] for k in binding.FIELDS)
            for k, model_value, text_value in zip(binding.FIELDS, atom, expected):
                if model_value != text_value:
                    differences.append({'clause': i, 'field': k, 'predicted': model_value, 'observed': text_value})
            revised.append(expected if repair_mode else atom)
            prior_actor = expected[0] if repair_mode else atom[0]
        else:
            revised.append(atom)
            prior_actor = None
    if not repair_mode and differences:
        issues.append('EVIDENCE_DISAGREES_WITH_MODEL')
    return not issues, revised if not issues else predictions, tuple(sorted(set(issues))), {'observed': observed, 'disagreements': differences}


def predict_baseline(rows: list[dict], model, encoder, base: dict, rf: dict) -> list[list[tuple]]:
    # Existing head and typed overlays are unchanged; the scope-only sign
    # overlay is the only previously accepted semantic intervention.
    raw = blind.predict_frozen(rows, model, encoder, base, rf)
    return scope.overlay_supported_signs(raw, [x['text'] for x in rows], base, rf['reference_markers'])


def evaluate(rows: list[dict], predictions: list[list[tuple]], base: dict, refs: list[str]) -> tuple[dict, dict, dict]:
    if len(rows) != len(predictions):
        raise ValueError('NEGCONS_ROWS_PREDICTIONS_MISMATCH')
    events = defaultdict(list)
    counts = Counter()
    field_counts = Counter()
    detail = []
    for row, atoms in zip(rows, predictions):
        plain_ok, _, _ = scope.scope_gate(row['text'], base, refs)
        arms = {'scope_baseline': (plain_ok, atoms, ())}
        provenance = {}
        for arm, do_repair in (('evidence_consensus', False), ('evidence_repair', True)):
            ok, pred, reasons, info = grounded_document(row['text'], atoms, base, refs, repair_mode=do_repair)
            arms[arm] = (ok, pred, reasons)
            provenance[arm] = info
            counts.update(reasons)
        gold = row['truth_atoms']
        for arm, (accepted, predicted, why) in arms.items():
            correct = None if gold is None else repair.graph(gold, base) == repair.graph(predicted, base)
            events[arm].append({'kind': row['kind'], 'accepted': accepted, 'correct': correct, 'supported': row['policy'] == 'supported'})
            if accepted and correct is False:
                field_counts[arm] += 1
        if gold is not None and plain_ok and repair.graph(gold, base) != repair.graph(atoms, base):
            mismatch = []
            for ci, (pred, truth) in enumerate(zip(atoms, gold)):
                mismatch.extend({'clause': ci, 'field': k, 'predicted': pv, 'expected': tv}
                                for k, pv, tv in zip(binding.FIELDS, pred, truth) if pv != tv)
            detail.append({'id': row['id'], 'kind': row['kind'], 'source_text': row['text'],
                           'predicted': [list(p) for p in atoms], 'expected': [list(p) for p in gold],
                           'mismatched_fields': mismatch,
                           'evidence': provenance['evidence_repair'],
                           'consensus_accepted': arms['evidence_consensus'][0],
                           'repair_accepted': arms['evidence_repair'][0],
                           'repair_correct': repair.graph(gold, base) == repair.graph(arms['evidence_repair'][1], base)})
    def summary(values):
        accepted = [d for d in values if d['accepted']]
        truth = [d for d in accepted if d['correct'] is not None]
        supported = [d for d in values if d['supported']]
        unsafe = [d for d in values if not d['supported']]
        total_labeled = [d for d in values if d['correct'] is not None]
        return {'documents': len(values), 'coverage': len(accepted) / len(values),
                'raw_graph_exact': (sum(d['correct'] for d in total_labeled)/len(total_labeled) if total_labeled else None),
                'selective_graph_exact': (sum(d['correct'] for d in truth)/len(truth) if truth else None),
                'accepted_labeled_count': len(truth),
                'false_confident_wrong': sum(d['accepted'] and d['correct'] is False for d in values),
                'accepted_indeterminate': sum(d['accepted'] and d['correct'] is None for d in values),
                'supported_accepted': sum(d['accepted'] for d in supported), 'supported_total': len(supported),
                'unsafe_accepted': sum(d['accepted'] for d in unsafe), 'unsafe_total': len(unsafe),
                'unsafe_rejected': sum(not d['accepted'] for d in unsafe)}
    return ({arm: {'aggregate': summary(vals), 'by_challenge': {k: summary([x for x in vals if x['kind'] == k])
                     for k in sorted({x['kind'] for x in vals})}} for arm, vals in events.items()},
            dict(sorted(counts.items())), detail)


def run(args: argparse.Namespace, *, encoder: Callable | None = None) -> dict:
    if args.output.exists():
        raise FileExistsError('OUTPUT_EXISTS_REFUSE_OVERWRITE')
    if (args.seed != 261117 or args.train_atoms != 216 or args.selection_groups != 16 or args.final_groups != 24
        or args.ridge != .1 or args.batch_size < 1 or args.max_length != 192 or args.groups < 8 or args.groups % 8):
        raise ValueError('NEGCONS_FROZEN_CONFIG_CHANGED')
    files = {'base_fixture_sha256': args.base_fixture, 'stress_fixture_sha256': args.stress_fixture,
             'interface_fixture_sha256': args.interface_fixture, 'repair_fixture_sha256': args.repair_fixture,
             'blind_fixture_sha256': args.blind_fixture, 'scope_fixture_sha256': args.scope_fixture}
    prior = validate_prior(args.prior_report, files)
    base = binding.load_fixture(args.base_fixture)
    st = stress.load_fixture(args.stress_fixture, base)
    iface = interface.load_fixture(args.interface_fixture, base, st)
    rf = repair.load_fixture(args.repair_fixture, base, iface, st)
    bf = blind.load_fixture(args.blind_fixture, base, st, iface, rf)
    sf = scope.load_fixture(args.scope_fixture, base, st, iface, rf, bf)
    f = load_fixture(args.fixture, base, st, iface, rf, bf, sf)
    data = stress.derive_data(base, st, seed=args.seed, train_atoms=args.train_atoms,
                              selection_groups=args.selection_groups, final_groups=args.final_groups)
    historic = scope.make_holdout(data, base, st, iface, rf, bf, sf, seed=args.seed, groups=24)
    fresh = make_fresh(data, base, st, iface, rf, bf, sf, f, seed=args.seed, groups=args.groups)
    if encoder is None:
        encoder = layers.FrozenLayerEncoder(smoke.MODEL_ID, args.device,
                        allow_download=args.allow_model_download, batch_size=args.batch_size, max_length=args.max_length)
    positions = getattr(encoder, 'positions', None)
    if positions and (positions.get('half') != 14 or positions.get('final') != 28):
        raise ValueError('NEGCONS_LAYER_POSITIONS_CHANGED')
    model = blind.train_frozen(encoder, base, st, iface, rf, data, ridge=args.ridge)
    predictions = predict_baseline(historic + fresh, model, encoder, base, rf)
    old_preds, new_preds = predictions[:len(historic)], predictions[len(historic):]
    old_scored, old_reasons, old_errors = evaluate(historic, old_preds, base, rf['reference_markers'])
    new_scored, new_reasons, new_errors = evaluate(fresh, new_preds, base, rf['reference_markers'])
    if getattr(encoder, 'stats', None) and encoder.stats.get('texts_truncated', 0):
        raise ValueError('NEGCONS_TEXT_TRUNCATION')
    report = {
        'schema': SCHEMA, 'status': STATUS,
        'acceptance': {'status': 'DIAGNOSTIC_ONLY_NOT_SEALABLE', 'model_frozen': True,
                       'no_model_weight_updates': True, 'no_contract_seal': True,
                       'no_production_mutation': True, 'no_final_based_model_selection': True},
        'sources': {**{k: sha256(p.read_bytes()).hexdigest() for k, p in files.items()},
                    'new_fixture_sha256': sha256(args.fixture.read_bytes()).hexdigest(),
                    'prior_report_sha256': sha256(args.prior_report.read_bytes()).hexdigest()},
        'design': {'model': smoke.MODEL_ID, 'view': 'final:mean', 'frozen_training_examples': 648,
                   'historical_inspected_rows': len(historic), 'fresh_holdout_rows': len(fresh),
                   'fresh_challenges': len(f['challenges']), 'fresh_groups': args.groups,
                   'all_inference_text_only': True, 'acceptance_independent_of_gold': True,
                   'evidence_repair_is_restricted_grammar_not_qwen_learning': True,
                   'prior_report_errors': prior['fresh']['scope_with_polarity_grounding']['aggregate']['false_confident_wrong'],
                   'token_stats': getattr(encoder, 'stats', None)},
        'historical_diagnostic': {'arms': old_scored, 'error_rows': old_errors, 'reason_counts': old_reasons,
                                  'not_used_for_training_or_method_selection': True},
        'new_holdout': {'arms': new_scored, 'error_rows': new_errors, 'reason_counts': new_reasons},
        'acceptance_targets': {
            'fresh_zero_confident_wrong': new_scored['evidence_repair']['aggregate']['false_confident_wrong'] == 0,
            'fresh_zero_unsafe_accepted': new_scored['evidence_repair']['aggregate']['unsafe_accepted'] == 0,
            'historical_supported_preserved': old_scored['evidence_repair']['aggregate']['supported_accepted'] == 216,
            'historical_six_errors_repaired': (len(old_errors) == 6 and all(r['repair_accepted'] and r['repair_correct'] for r in old_errors)),
            'fresh_supported_preserved': new_scored['evidence_repair']['aggregate']['supported_accepted'] == args.groups * 9},
        'interpretation': 'Evidence repair is a complete canonical-vocabulary text extractor with Qwen as monitored baseline. It does not establish open-language generalization. No acceptance threshold met on mock data certifies CUDA.'
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf8') as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, allow_nan=False)
        fh.write('\n')
    return report


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device', default='cuda')
    p.add_argument('--allow-model-download', action='store_true')
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--prior-report', default=PRIOR_REPORT, type=Path)
    p.add_argument('--fixture', default=FIXTURE, type=Path)
    p.add_argument('--base-fixture', default=binding.FIXTURE, type=Path)
    p.add_argument('--stress-fixture', default=stress.FIXTURE, type=Path)
    p.add_argument('--interface-fixture', default=interface.FIXTURE, type=Path)
    p.add_argument('--repair-fixture', default=repair.FIXTURE, type=Path)
    p.add_argument('--blind-fixture', default=blind.FIXTURE, type=Path)
    p.add_argument('--scope-fixture', default=scope.FIXTURE, type=Path)
    p.add_argument('--seed', default=261117, type=int)
    p.add_argument('--train-atoms', default=216, type=int)
    p.add_argument('--selection-groups', default=16, type=int)
    p.add_argument('--final-groups', default=24, type=int)
    p.add_argument('--groups', default=24, type=int)
    p.add_argument('--ridge', default=.1, type=float)
    p.add_argument('--batch-size', default=4, type=int)
    p.add_argument('--max-length', default=192, type=int)
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    report = run(args)
    scored = report['new_holdout']['arms']
    summary = {name: {k: (round(scored[name]['aggregate'][k], 6) if isinstance(scored[name]['aggregate'][k], float) else scored[name]['aggregate'][k])
                      for k in ('coverage', 'selective_graph_exact', 'supported_accepted', 'unsafe_accepted', 'false_confident_wrong', 'accepted_indeterminate')}
               for name in ARMS}
    print(smoke.canonical({'status': STATUS, 'acceptance': report['acceptance']['status'],
                            'historical_errors_regenerated': len(report['historical_diagnostic']['error_rows']),
                            'historical_supported': report['historical_diagnostic']['arms']['evidence_repair']['aggregate']['supported_accepted'],
                            'new_holdout_documents': report['design']['fresh_holdout_rows'],
                            'new_holdout': summary,
                            'targets': report['acceptance_targets'], 'report': str(args.output)}))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
