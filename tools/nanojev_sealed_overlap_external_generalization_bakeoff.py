#!/usr/bin/env python3
"""Frozen Qwen evidence-repair candidate versus genuinely new contract language.

No mutation, fitting to eval, or candidate changes. Source-fingerprint preflight
pins the exact previously GPU-tested decoder/gate/grounding modules. Runs:
  * 600 earlier authored contract probes as a REGRESSION CANARY (inspected);
  * 24 groups x 18 newly authored lexical/syntactic/status probes;
  * 10 unchanged excerpts from two externally authored legal texts.

External samples are REAL legal prose but lack a full annotation in this six-slot
schema. Their target is ABSTAIN from authoritative six-field extraction; this
DOES NOT assert the quoted clause is non-operative or invalid. New authored
probes are separately labeled and are NOT independent human legal documents.

Use from main_computer repository root:
  & $py -m tools.nanojev_sealed_overlap_external_generalization_bakeoff \
      --device cuda --output runtime/reports/sealed-overlap-external-generalization-0001.json
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import itertools
import json
from pathlib import Path
import random
from string import Formatter
from typing import Callable

from tools import nanojev_sealed_overlap_information_smoke as smoke
from tools import nanojev_sealed_overlap_polarity_layers_frob as layers
from tools import nanojev_sealed_overlap_composition_bakeoff as composition
from tools import nanojev_sealed_overlap_relation_binding_bakeoff as binding
from tools import nanojev_sealed_overlap_language_stress_bakeoff as stress
from tools import nanojev_sealed_overlap_semantic_interface_bakeoff as interface
from tools import nanojev_sealed_overlap_semantic_interface_repair_bakeoff as repair
from tools import nanojev_sealed_overlap_lexicon_blind_bakeoff as blind
from tools import nanojev_sealed_overlap_scope_obligation_bakeoff as scope
from tools import nanojev_sealed_overlap_negative_consistency_bakeoff as neg

SCHEMA = 'nanojev.sealed-overlap.external-generalization-bakeoff.v1'
STATUS = 'COMPLETE_EXTERNAL_GENERALIZATION_DIAGNOSTIC_NO_SEAL_NO_PROMOTION'
FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'nanojev_overlap_external_generalization_v1.json'
PRIOR_REPORT = Path('runtime/reports/sealed-overlap-negative-consistency-0001.json')
FROZEN_HOLDOUT_SHA256 = '9758bb77ff96f1ef7f647c9d43d4b49e36bf2d4c2629fde9205624c098ea2145'
FROZEN_MODULES = {
    'nanojev_sealed_overlap_negative_consistency_bakeoff': '2650864770c56118b19f272b83d6be4f3429cf7052cb377b610c30b324571e43',
    'nanojev_sealed_overlap_scope_obligation_bakeoff': '08428738666ce2a524a3a2aef41930a8152ae9a05e2a8fd2d6393870be4ce729',
    'nanojev_sealed_overlap_lexicon_blind_bakeoff': '301eda34a468ded8237688e256bdcde6409b1de412061434232d0ddf61e34112',
    'nanojev_sealed_overlap_semantic_interface_repair_bakeoff': '2d683b04d731fa9ff204d98cfedd5118906693e9720c6c92b3f49d3b369e4ca4',
    'nanojev_sealed_overlap_semantic_interface_bakeoff': 'dd3e1c32d4aaa0469c885a13e1bf62e7eb12b196f5055aa158f948aa2184b0c1',
    'nanojev_sealed_overlap_language_stress_bakeoff': 'ff845030cf4e5a461ab560ff5f793a43ef32250d51459dda574af184e46f9226',
    'nanojev_sealed_overlap_relation_binding_bakeoff': '01f019c8bcd8ce0e652171f4a5eb79fea06e89a9624d8d0a05ccddd26474940f',
    'nanojev_sealed_overlap_composition_bakeoff': 'af7e9f9990afb75898b638c2ee4ffec40c96a33cb14730402415eab380f2b1f9',
    'nanojev_sealed_overlap_polarity_layers_frob': 'd744ae351576d7a9714f0f70a71624165fb421d35c314512bd341641aa67b29b',
    'nanojev_sealed_overlap_information_smoke': '71b1e659a9fc1a2847f0a1be9b4758acf7ee05a7e84bb832d349e4008c9fd119',
}
MODULES = (neg, scope, blind, repair, interface, stress, binding, composition, layers, smoke)
SLOTS = binding.FIELDS


def fingerprint_preflight() -> dict[str, str]:
    """Freezes actual imported code, not just parameter names in the report."""
    observed = {Path(mod.__file__).stem: sha256(Path(mod.__file__).read_bytes()).hexdigest() for mod in MODULES}
    if observed != FROZEN_MODULES:
        changed = {k: {'expected': FROZEN_MODULES.get(k), 'actual': observed.get(k)}
                   for k in set(observed) | set(FROZEN_MODULES) if observed.get(k) != FROZEN_MODULES.get(k)}
        raise ValueError('CANDIDATE_SOURCE_DRIFT_REFUSED: ' + json.dumps(changed, sort_keys=True))
    return observed


def load_fixture(path: Path, base: dict, old_fixtures: tuple[dict, ...]) -> dict:
    f = json.loads(path.read_text(encoding='utf8'))
    if f.get('schema') != 'nanojev.sealed-overlap.external-generalization-fixture.v1' or f.get('document_delimiter') != ' || ':
        raise ValueError('EXTGEN_FIXTURE_SCHEMA_INVALID')
    ch = f.get('authored_challenges')
    sources = f.get('external_legal_excerpts')
    if not isinstance(ch, list) or len(ch) != 18 or not isinstance(sources, list) or len(sources) != 10:
        raise ValueError('EXTGEN_COUNTS_CHANGED')
    past = set(sum((base['templates'][k] for k in ('train', 'selection', 'final')), []))
    st, iface, rf, bf, sf, nf = old_fixtures
    past.update(st['selection_templates'] + st['final_templates'])
    past.update(iface['augmentation_templates'] + iface['fresh_templates'])
    past.update(rf['targeted_training_templates'] + rf['new_holdout_templates'])
    for old in (bf, sf, nf):
        past.update(x['template'] for x in old['challenges'])
    fields = {'actor', 'recipient', 'asset', 'condition', 'consequence',
              'new_actor', 'new_recipient', 'new_asset', 'new_condition', 'new_consequence', 'other_consequence'}
    seen = set()
    for c in ch:
        tpl = c.get('template')
        variables = {x for _, x, _, _ in Formatter().parse(tpl) if x is not None} if isinstance(tpl, str) else set()
        if (not isinstance(tpl, str) or not tpl or tpl in seen or tpl in past or ' || ' in tpl
            or not variables.issubset(fields) or c.get('family') not in ('known_control', 'new_vocabulary', 'new_grammar', 'nonoperative')
            or c.get('policy') not in ('supported', 'abstain')
            or c.get('truth') not in ('positive', 'negative', 'indeterminate')
            or (c['policy'] == 'supported') != (c['truth'] != 'indeterminate')):
            raise ValueError('EXTGEN_CHALLENGE_INVALID_OR_OVERLAP')
        seen.add(tpl)
    if len({c['kind'] for c in ch}) != len(ch):
        raise ValueError('EXTGEN_DUPLICATE_KINDS')
    new = f.get('new_entities')
    if set(new or ()) != {'actors', 'recipients', 'assets', 'conditions', 'consequences'}:
        raise ValueError('EXTGEN_NEW_LEXICON_MISSING')
    for key, vals in new.items():
        if len(vals) != 8 or len(set(vals)) != 8 or any(not isinstance(x, str) or not x.strip() for x in vals):
            raise ValueError('EXTGEN_NEW_LEXICON_INVALID')
        if set(vals) & set(base[key]):
            raise ValueError('EXTGEN_LEXICON_NOT_UNSEEN')
    if len({x['id'] for x in sources}) != len(sources) or len({x['text'] for x in sources}) != len(sources):
        raise ValueError('EXTGEN_EXTERNAL_DUPLICATES')
    for row in sources:
        if (row.get('target') != 'abstain_full_six_field'
            or row.get('source_url') not in ('https://www.acquisition.gov/far/52.212-4', 'https://creativecommons.org/licenses/by/4.0/legalcode')
            or ' || ' in row.get('text', '') or len(row.get('text', '').split()) < 10):
            raise ValueError('EXTGEN_EXTERNAL_PROVENANCE_INVALID')
    return f


def validate_prior(path: Path, paths: dict[str, Path]) -> dict:
    prior = json.loads(path.read_text(encoding='utf8'))
    a = prior.get('new_holdout', {}).get('arms', {}).get('evidence_repair', {}).get('aggregate', {})
    h = prior.get('historical_diagnostic', {}).get('arms', {}).get('evidence_repair', {}).get('aggregate', {})
    if (prior.get('schema') != neg.SCHEMA or prior.get('status') != neg.STATUS
        or prior.get('acceptance', {}).get('no_model_weight_updates') is not True
        or prior.get('acceptance', {}).get('no_final_based_model_selection') is not True
        or prior.get('design', {}).get('view') != 'final:mean'
        or prior.get('design', {}).get('frozen_training_examples') != 648
        or a.get('documents') != 408 or a.get('supported_accepted') != 216
        or a.get('unsafe_accepted') != 0 or a.get('false_confident_wrong') != 0
        or h.get('supported_accepted') != 216 or h.get('false_confident_wrong') != 0
        or any(prior.get('sources', {}).get(key) != sha256(p.read_bytes()).hexdigest() for key, p in paths.items())):
        raise ValueError('EXTGEN_PRIOR_REPORT_NOT_ACCEPTED_OR_DIFFERENT')
    return prior


def make_holdout(data: dict, base: dict, st: dict, iface: dict, rf: dict, bf: dict, sf: dict,
                 nf: dict, f: dict, *, seed: int, groups: int) -> list[dict]:
    if groups < 8 or groups % 8:
        raise ValueError('EXTGEN_GROUP_COUNT_INVALID')
    # Ensure new semantic pairs are neither training/review pairs nor previous
    # holdouts. No selection on evaluated target responses.
    prior_neg = neg.make_fresh(data, base, st, iface, rf, bf, sf, nf, seed=seed, groups=24)
    prior_scope = scope.make_holdout(data, base, st, iface, rf, bf, sf, seed=seed, groups=24)
    prior_blind = blind.make_holdout(data, base, st, iface, rf, bf, seed=seed, groups=24)
    prior_rep = repair.make_fresh(data, base, iface, st, rf, seed=seed, groups=24)
    used = {tuple(sorted(tuple(a) for a in r['atoms'])) for phase in ('selection', 'final') for r in data[phase] if r['kind'] == 'base'}
    for prior in (prior_neg, prior_scope, prior_blind, prior_rep):
        for row in prior:
            atoms = row.get('truth_atoms') or row.get('atoms')
            if atoms and len(atoms) == 2:
                used.add(tuple(sorted(tuple(a[:5]) for a in atoms)))
    training = sorted({tuple(r['atom']) for r in data['train']})
    unseen = sorted(set(itertools.product(*binding.values(base))) - set(training))
    rng = random.Random(seed + 998207)
    rows = []
    entities = f['new_entities']
    for g in range(groups):
        pool = training if g % 2 == 0 else unseen
        for _ in range(60000):
            a, b = rng.sample(pool, 2)
            key = tuple(sorted((tuple(a[:5]), tuple(b[:5]))))
            if key not in used and all(a[i] != b[i] for i in range(5)):
                used.add(key)
                break
        else:
            raise ValueError('EXTGEN_SEMANTIC_PAIR_EXHAUSTED')
        fields = dict(zip(SLOTS, a))
        fields.update({'new_actor': entities['actors'][g % 8],
                       'new_recipient': entities['recipients'][(g + 3) % 8],
                       'new_asset': entities['assets'][(g + 5) % 8],
                       'new_condition': entities['conditions'][(g + 2) % 8],
                       'new_consequence': entities['consequences'][(g + 6) % 8],
                       'other_consequence': next(x for x in base['consequences'] if x != a[4])})
        # The unmodified second clause is a fixed context control and is NOT
        # independently authored. Only the first clause is a novelty probe.
        second = repair.render(b, rf, 'new_holdout_templates', 0)
        for spec in f['authored_challenges']:
            text = binding.document([spec['template'].format(**fields).removesuffix('.'), second])
            atom = list(a)
            atom[5] = 1 if spec['truth'] == 'positive' else -1
            substitutions = {'unknown_actor': (0, 'new_actor'), 'unknown_recipient': (1, 'new_recipient'),
                             'unknown_asset': (2, 'new_asset'), 'unknown_condition': (3, 'new_condition'),
                             'unknown_consequence': (4, 'new_consequence')}
            if spec['kind'] in substitutions:
                pos, name = substitutions[spec['kind']]
                atom[pos] = fields[name]
            if spec['kind'] == 'all_five_new':
                atom[:5] = [fields['new_' + k] for k in SLOTS[:5]]
            gold = [tuple(atom), tuple(b)] if spec['policy'] == 'supported' else None
            rows.append({'id': f'extgen:{g:03d}:{spec["kind"]}', 'family': spec['family'],
                         'kind': spec['kind'], 'origin': 'authored_probe', 'target': spec['policy'],
                         'truth_atoms': gold, 'text': text, 'group_id': g})
    for src in f['external_legal_excerpts']:
        rows.append({'id': 'external:' + src['id'], 'family': 'external_legal',
                     'kind': src['id'], 'origin': 'third_party_legal_excerpt', 'target': 'abstain',
                     'truth_atoms': None, 'text': src['text'], 'source_url': src['source_url'],
                     'source_section': src['section'], 'annotation_scope': src['annotation_scope']})
    if len({r['text'] for r in rows}) != len(rows):
        raise ValueError('EXTGEN_DUPLICATE_DOCUMENT_TEXT')
    prior_texts = {r['text'] for rows_old in (prior_neg, prior_scope, prior_blind, prior_rep) for r in rows_old}
    if {r['text'] for r in rows} & prior_texts:
        raise ValueError('EXTGEN_REUSES_PREVIOUS_DOCUMENT')
    return rows


def exact_graph(atoms: list[tuple]) -> tuple:
    # Unlike the earlier closed-class graph() this comparison permits NEW
    # condition, asset, actor, recipient, and consequence literal strings.
    return tuple(sorted(tuple(a) for a in atoms))


def evaluate(rows: list[dict], predictions: list[list[tuple]], base: dict, refs: list[str]) -> dict:
    if len(rows) != len(predictions):
        raise ValueError('EXTGEN_INFERENCE_LENGTH_MISMATCH')
    items = {'scope_baseline': [], 'frozen_evidence_repair': []}
    errors = []
    for r, pred in zip(rows, predictions):
        gate_ok, gate_reasons, _ = scope.scope_gate(r['text'], base, refs)
        grounded_ok, grounded_pred, grounded_reasons, provenance = neg.grounded_document(
            r['text'], pred, base, refs, repair_mode=True)
        for arm, accepted, atom, reasons in (
                ('scope_baseline', gate_ok, pred, gate_reasons),
                ('frozen_evidence_repair', grounded_ok, grounded_pred, grounded_reasons)):
            gold = r['truth_atoms']
            exact = exact_graph(gold) == exact_graph(atom) if gold is not None else None
            entry = {'id': r['id'], 'kind': r['kind'], 'family': r['family'], 'origin': r['origin'],
                     'target': r['target'], 'accepted': accepted, 'exact': exact,
                     'reasons': list(reasons)}
            items[arm].append(entry)
            if accepted and (exact is False or gold is None):
                missing = []
                if gold is not None:
                    for ci, (a, b) in enumerate(zip(atom, gold)):
                        missing.extend({'clause': ci, 'field': key, 'predicted': av, 'truth': bv}
                                       for key, av, bv in zip(SLOTS, a, b) if av != bv)
                errors.append({'arm': arm, 'id': r['id'], 'family': r['family'], 'text': r['text'],
                               'source_url': r.get('source_url'), 'raw_prediction': [list(x) for x in pred],
                               'accepted_prediction': [list(x) for x in atom], 'target': r['target'],
                               'mismatched_fields': missing,
                               'text_evidence': provenance if arm == 'frozen_evidence_repair' else None})
    def metrics(entries):
        accepted = [x for x in entries if x['accepted']]
        labeled = [x for x in entries if x['exact'] is not None]
        accepted_labeled = [x for x in accepted if x['exact'] is not None]
        eligible = [x for x in entries if x['target'] == 'supported']
        abstain = [x for x in entries if x['target'] == 'abstain']
        counts = Counter(reason for x in entries for reason in x['reasons'])
        return {'documents': len(entries), 'labeled_documents': len(labeled),
                'raw_graph_exact': (sum(x['exact'] for x in labeled) / len(labeled)) if labeled else None,
                'accepted_count': len(accepted), 'coverage': len(accepted) / len(entries) if entries else None,
                'accepted_labeled_count': len(accepted_labeled),
                'selective_graph_exact': (sum(x['exact'] for x in accepted_labeled) / len(accepted_labeled)) if accepted_labeled else None,
                'supported_accepted': sum(x['accepted'] for x in eligible), 'supported_total': len(eligible),
                'false_confident_wrong': sum(x['accepted'] and x['exact'] is False for x in entries),
                'abstention_required_accepted': sum(x['accepted'] for x in abstain),
                'abstention_required_total': len(abstain), 'correctly_abstained': sum(not x['accepted'] for x in abstain),
                'reason_counts': dict(sorted(counts.items()))}
    return {arm: {'aggregate': metrics(entries),
                  'by_family': {k: metrics([x for x in entries if x['family'] == k]) for k in sorted({x['family'] for x in entries})},
                  'by_challenge': {k: metrics([x for x in entries if x['kind'] == k]) for k in sorted({x['kind'] for x in entries})},
                  'accepted_examples': [x['id'] for x in entries if x['accepted']]}
            for arm, entries in items.items()} | {'errors': errors}


def run(args: argparse.Namespace, *, encoder: Callable | None = None) -> dict:
    if args.output.exists():
        raise FileExistsError('OUTPUT_EXISTS_REFUSE_OVERWRITE')
    if (args.seed != 261117 or args.train_atoms != 216 or args.selection_groups != 16
        or args.final_groups != 24 or args.ridge != .1 or args.groups != 24
        or args.batch_size < 1 or args.max_length != 192):
        raise ValueError('EXTGEN_FROZEN_CONFIG_CHANGED')
    checked = fingerprint_preflight()
    if sha256(args.fixture.read_bytes()).hexdigest() != FROZEN_HOLDOUT_SHA256:
        raise ValueError('EXTGEN_HOLDOUT_CHANGED_REFUSED')
    paths = {'base_fixture_sha256': args.base_fixture, 'stress_fixture_sha256': args.stress_fixture,
             'interface_fixture_sha256': args.interface_fixture, 'repair_fixture_sha256': args.repair_fixture,
             'blind_fixture_sha256': args.blind_fixture, 'scope_fixture_sha256': args.scope_fixture,
             'new_fixture_sha256': args.negative_fixture}
    prior = validate_prior(args.prior_report, paths)
    base = binding.load_fixture(args.base_fixture)
    st = stress.load_fixture(args.stress_fixture, base)
    iface = interface.load_fixture(args.interface_fixture, base, st)
    rf = repair.load_fixture(args.repair_fixture, base, iface, st)
    bf = blind.load_fixture(args.blind_fixture, base, st, iface, rf)
    sf = scope.load_fixture(args.scope_fixture, base, st, iface, rf, bf)
    nf = neg.load_fixture(args.negative_fixture, base, st, iface, rf, bf, sf)
    f = load_fixture(args.fixture, base, (st, iface, rf, bf, sf, nf))
    data = stress.derive_data(base, st, seed=args.seed, train_atoms=args.train_atoms,
                              selection_groups=args.selection_groups, final_groups=args.final_groups)
    canary = scope.make_holdout(data, base, st, iface, rf, bf, sf, seed=args.seed, groups=24)
    fresh = make_holdout(data, base, st, iface, rf, bf, sf, nf, f, seed=args.seed, groups=args.groups)
    if encoder is None:
        encoder = layers.FrozenLayerEncoder(smoke.MODEL_ID, args.device,
                                          allow_download=args.allow_model_download,
                                          batch_size=args.batch_size, max_length=args.max_length)
    positions = getattr(encoder, 'positions', None)
    if positions and (positions.get('half') != 14 or positions.get('final') != 28):
        raise ValueError('EXTGEN_MODEL_LAYER_POSITIONS_CHANGED')
    model = blind.train_frozen(encoder, base, st, iface, rf, data, ridge=args.ridge)
    predictions = neg.predict_baseline(canary + fresh, model, encoder, base, rf)
    old_p, new_p = predictions[:len(canary)], predictions[len(canary):]
    # Historical canary uses its existing six-field graph and scope labels;
    # completely new vocabulary is evaluated separately in evaluate().
    canary_metrics, _, canary_errors = neg.evaluate(canary, old_p, base, rf['reference_markers'])
    new_metrics = evaluate(fresh, new_p, base, rf['reference_markers'])
    stat = getattr(encoder, 'stats', None)
    if stat and stat.get('texts_truncated', 0):
        raise ValueError('EXTGEN_EMBEDDING_TRUNCATION')
    h = canary_metrics['evidence_repair']['aggregate']
    a = new_metrics['frozen_evidence_repair']['aggregate']
    assert len(canary) == 600 and len(fresh) == args.groups * 18 + 10
    result = {'schema': SCHEMA, 'status': STATUS,
              'acceptance': {'status': 'DIAGNOSTIC_ONLY_NOT_SEALABLE',
                             'no_model_weight_updates': True, 'no_contract_seal': True,
                             'no_production_mutation': True, 'no_final_based_model_selection': True},
              'source_pins': {'module_sha256': checked,
                              'prior_report_sha256': sha256(args.prior_report.read_bytes()).hexdigest(),
                              'fixture_sha256': sha256(args.fixture.read_bytes()).hexdigest(),
                              **{k: sha256(v.read_bytes()).hexdigest() for k, v in paths.items()}},
              'design': {'model': smoke.MODEL_ID, 'view': 'final:mean', 'ridge': args.ridge,
                         'training_examples': 648, 'new_authored_probes': args.groups * 18,
                         'source_verified_external_legal_excerpts': 10,
                         'historical_canary_documents': len(canary), 'source_urls': sorted({r['source_url'] for r in fresh if r['origin'] == 'third_party_legal_excerpt'}),
                         'third_party_gold_limitation': 'Only abstention from a full six-field authoritative graph is annotated; no human expert gold graph, not a legal-validity determination',
                         'novelty_limitation': 'The 432 authored probes are model-developer-generated; not an independent human-authored legal corpus. Two separate third-party legal sources supply 10 authentic excerpts.',
                         'code_fingerprint_enforced': True, 'token_stats': stat},
              'historical_canary': {'aggregate': h, 'error_count': len(canary_errors),
                                    'matches_prior_216_of_216': h['supported_accepted'] == 216 and h['false_confident_wrong'] == 0 and h['unsafe_accepted'] == 0,
                                    'previous_aggregate': prior['historical_diagnostic']['arms']['evidence_repair']['aggregate']},
              'new_holdout': new_metrics,
              'outcome': {'historical_regression_pass': h['supported_accepted'] == 216 and h['false_confident_wrong'] == 0 and h['unsafe_accepted'] == 0,
                          'fresh_zero_confident_wrong': a['false_confident_wrong'] == 0,
                          'fresh_zero_unsupported_acceptances': a['abstention_required_accepted'] == 0,
                          'fresh_supported_coverage': a['supported_accepted'] / a['supported_total'],
                          'fresh_valid_generalization_certified': False},
              'interpretation': 'Do not equate abstention with comprehension. Novel words and externally authored language require independently labeled full semantic annotations before claiming accuracy. The frozen evidence repair is a closed-vocabulary text parser and is expected to abstain on most out-of-domain constructions.'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf8') as fp:
        json.dump(result, fp, ensure_ascii=False, indent=2, allow_nan=False)
        fp.write('\n')
    return result


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device', default='cuda')
    p.add_argument('--allow-model-download', action='store_true')
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--prior-report', type=Path, default=PRIOR_REPORT)
    p.add_argument('--fixture', type=Path, default=FIXTURE)
    p.add_argument('--base-fixture', type=Path, default=binding.FIXTURE)
    p.add_argument('--stress-fixture', type=Path, default=stress.FIXTURE)
    p.add_argument('--interface-fixture', type=Path, default=interface.FIXTURE)
    p.add_argument('--repair-fixture', type=Path, default=repair.FIXTURE)
    p.add_argument('--blind-fixture', type=Path, default=blind.FIXTURE)
    p.add_argument('--scope-fixture', type=Path, default=scope.FIXTURE)
    p.add_argument('--negative-fixture', type=Path, default=neg.FIXTURE)
    p.add_argument('--train-atoms', type=int, default=216)
    p.add_argument('--selection-groups', type=int, default=16)
    p.add_argument('--final-groups', type=int, default=24)
    p.add_argument('--groups', type=int, default=24)
    p.add_argument('--seed', type=int, default=261117)
    p.add_argument('--ridge', type=float, default=.1)
    p.add_argument('--batch-size', type=int, default=4)
    p.add_argument('--max-length', type=int, default=192)
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    report = run(args)
    a = report['new_holdout']['frozen_evidence_repair']['aggregate']
    families = report['new_holdout']['frozen_evidence_repair']['by_family']
    print(smoke.canonical({'status': STATUS, 'acceptance': report['acceptance']['status'],
                           'fingerprint_locked': True, 'historical_regression_pass': report['outcome']['historical_regression_pass'],
                           'fresh_documents': a['documents'], 'fresh_supported_coverage': round(report['outcome']['fresh_supported_coverage'], 6),
                           'fresh_false_confident_wrong': a['false_confident_wrong'],
                           'fresh_unsupported_accepted': a['abstention_required_accepted'],
                           'fresh_selective_graph_exact': a['selective_graph_exact'],
                           'external_legal_abstained': families['external_legal']['correctly_abstained'],
                           'external_legal_total': families['external_legal']['documents'],
                           'by_family': {k: {'coverage': round(v['coverage'], 6), 'supported_accepted': v['supported_accepted'],
                                            'supported_total': v['supported_total'], 'wrong_accepted': v['false_confident_wrong'],
                                            'unsupported_accepted': v['abstention_required_accepted']}
                                         for k, v in families.items()}, 'report': str(args.output)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
