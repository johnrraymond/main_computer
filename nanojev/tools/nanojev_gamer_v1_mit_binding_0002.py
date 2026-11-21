"""Gamer MIT-BINDING-0002: sealed, no-training relationship-grounding experiment.

Run from main_computer/nanojev:
    python -m tools.nanojev_gamer_v1_mit_binding_0002 run --device cuda

Compares exact MILP decoding using the *same frozen MIT-WSP checkpoint*,
with zero additional weight updates, under independently ablated lexical
identity, character-gap, and semicolon-clause constraints. Re-solves the MILP
for each intervention; it never just removes edges after decoding.

A new prompt cohort is sealed before blind prediction. No gold is opened by
predict(). score() reads it only after the prediction hash is locked.
All configuration is Python source; JSON/JSONL reports are written internally.
This does not promote the G2 model or unlock G3+.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random

import torch

from tools import nanojev_gamer_v1_graph_corpus as corpus
from tools import nanojev_gamer_v1_g2_posttrain_graph_bakeoff_0088 as legacy
from tools import nanojev_gamer_v1_g2_structural_learning_0089 as structural
from tools import nanojev_gamer_v1_mit as mit
from tools import nanojev_gamer_v1_mit_decoder as projector
from tools import nanojev_gamer_v1_mit_wsp as wsp
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES

SCHEMA = 'nanojev.gamer.graph.g2.mit-binding-0002.v1'
CAMPAIGN = 'oracle-fabric-g2-mit-binding-0002'
ROOT_DEFAULT = '../runtime/reports/gamer-v1'
SEED = 261122
DEV = 48
CHALLENGE = 32
GAP_LIMIT = 45              # Precommitted TRAIN-derived hypothesis; not fit on held-out gold.
BLOCKED_MARGIN = -1000.0    # Far below all authorized frozen decoder thresholds.
ARMS = ('mit_wsp_baseline', 'identity', 'identity_gap45',
        'identity_clause', 'identity_gap45_clause')


def require(ok, code):
    if not ok:
        raise ValueError('MIT_BINDING_' + code)


def path_for(root, output):
    return (Path(output) if output else Path(root) / CAMPAIGN).resolve()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, content):
    mit.write(path, content)


def rows(path):
    return list(mit.rows(path))


def edges_to_json(edges):
    return [[src, list(ss), dst, list(ds), kind]
            for src, ss, dst, ds, kind in sorted(edges)]


def edges_from_json(items):
    return {(src, tuple(ss), dst, tuple(ds), kind)
            for src, ss, dst, ds, kind in items}


def source_lineage(root):
    """Validate frozen parent, WSP edit and selection without reading WSP gold."""
    wsp_dest = wsp.location(root, None)
    commit, mit_dest, base_file, paths, parent, cfg, edit = wsp.check_edit(wsp_dest, root)
    require(cfg['winner']['method'] == 'joint', 'JOINT_SPEC_REQUIRED')
    return {'wsp_dest': wsp_dest, 'mit_dest': mit_dest, 'paths': paths,
            'spec': cfg['winner'], 'wsp_commit': commit, 'edit': edit,
            'checkpoint': wsp_dest / 'checkpoint', 'base_file': base_file}


def _anchor(prompt, obj):
    a, b, kind = obj
    require(type(a) is int and type(b) is int and
            0 <= a < b <= len(prompt), 'BAD_CHARACTER_ANCHOR')
    return (a, b, kind, prompt[a:b])


def _gap(first, second):
    """Count characters *between* two nonoverlapping spans; zero if overlapping."""
    return max(0, first[0] - second[1], second[0] - first[1])


def _same_semicolon_clause(prompt, first, second):
    """A structural ablation: no ';' between the two endpoint spans."""
    left, right = sorted((first, second), key=lambda s: (s[0], s[1]))
    return ';' not in prompt[left[1]:right[0]]


def allowed(prompt, source, target, label, arm):
    """Fixed rules on predicted G1 objects and raw text; never read gold."""
    require(arm in ARMS, 'UNKNOWN_ARM')
    if arm == 'mit_wsp_baseline':
        return True
    if label == 'refers_to':
        # This is an explicit lexical-identity hypothesis. Distinct occurrences
        # of the same text are allowed; different names are excluded.
        a = _anchor(prompt, source)[3]
        b = _anchor(prompt, target)[3]
        if a.strip().casefold() != b.strip().casefold():
            return False
    if arm in ('identity_gap45', 'identity_gap45_clause'):
        if label == 'measures' and _gap(source, target) > GAP_LIMIT:
            return False
    if arm in ('identity_clause', 'identity_gap45_clause'):
        if label in ('measures', 'applies_to') and not _same_semicolon_clause(
                prompt, source, target):
            return False
    return True


def restrict_surface(surface, prompt, arm):
    """Remove ineligible label-candidates BEFORE the exact global MILP.

    Keep all original margins/other metadata intact and construct a new surface.
    Setting a blocked margin to -1000 excludes it from candidate_edges(); the
    frozen projector and optimizer see an ordinary reduced candidate universe.
    """
    objs, _ = legacy.validate_surface(surface)
    require(isinstance(prompt, str), 'PROMPT_REQUIRED')
    for obj in objs:
        _anchor(prompt, obj)
    if arm == 'mit_wsp_baseline':
        return surface, 0
    rejected = 0
    out = []
    for i, j, margins in surface['pair_margins']:
        updated = list(margins)
        for t, label in enumerate(BIND_CLASSES[1:]):
            if not allowed(prompt, objs[i], objs[j], label, arm):
                updated[t] = BLOCKED_MARGIN
                rejected += 1
        out.append([i, j, updated])
    return {**surface, 'pair_margins': out}, rejected


def decode(surface, prompt, spec, arm):
    constrained, blocked = restrict_surface(surface, prompt, arm)
    chosen, details = projector.decode(constrained, spec, return_diagnostics=True)
    return chosen, {'blocked_pair_labels': blocked, **details}


def new_cases(paths, root, wsp_dest):
    rng = random.Random(SEED)
    seen = mit.known_hashes(paths, root)
    # Exclude both already-evaluated MIT cohorts explicitly.
    for cohort in (Path(root) / mit.CAMPAIGN, wsp_dest):
        for r in rows(cohort / 'seal' / 'prompts.jsonl'):
            seen.add(r['prompt_sha256'])
    cases = []
    serial = 261_122_000
    for split, size in (('dev', DEV), ('challenge', CHALLENGE)):
        for i in range(size):
            factory = corpus.encounter if i % 2 == 0 else corpus.allocation
            for _ in range(4096):
                example = factory(rng, 'test', serial)
                serial += 1
                digest = hashlib.sha256(example['prompt'].encode('utf-8')).hexdigest()
                if digest not in seen:
                    break
            else:
                raise ValueError('MIT_BINDING_FRESH_COHORT_EXHAUSTED')
            require(example.get('annotation_provenance') ==
                    'AUTHOR_MARKED_IN_SOURCE_GENERATOR', 'INVALID_SOURCE_LABELS')
            seen.add(digest)
            cases.append({'case_id': f'mit-binding-{split}-{i+1:04}',
                          'split': split, 'family': example['family'],
                          'prompt': example['prompt'], 'prompt_sha256': digest,
                          'graph': example['graph']})
    require(len(cases) == DEV + CHALLENGE, 'CASE_COUNT')
    return cases


def prepare(*, root=ROOT_DEFAULT, output=None):
    dest = path_for(root, output)
    require(not dest.exists() and dest.parent.is_dir(), 'CAMPAIGN_EXISTS_OR_PARENT_MISSING')
    source = source_lineage(root)
    cases = new_cases(source['paths'], root, source['wsp_dest'])
    dest.mkdir(exist_ok=False)
    (dest / 'seal').mkdir(exist_ok=False)
    mit.write_jsonl(dest / 'seal' / 'prompts.jsonl', [
        {k: r[k] for k in ('case_id', 'split', 'family', 'prompt', 'prompt_sha256')}
        for r in cases])
    mit.write_jsonl(dest / 'seal' / 'answers.SECRET.jsonl', [
        {k: r[k] for k in ('case_id', 'split', 'family', 'prompt_sha256', 'graph')}
        for r in cases])
    selection = {'schema': SCHEMA, 'method': 'precommitted-gold-blind-structural-ablation',
                 'arms': list(ARMS), 'gap_limit_chars': GAP_LIMIT,
                 'gap_definition': 'characters between nonoverlapping G1 spans',
                 'clause_delimiter': ';', 'weight_edit': 'existing-frozen-MIT-WSP',
                 'model_training': False, 'g3_unlocked': False}
    write(dest / 'selection.json', selection)
    commit = {'schema': SCHEMA, 'state': 'SEALED_BEFORE_BLIND_INFERENCE',
              'runner_sha256': sha(__file__),
              'wsp_commit_sha256': sha(source['wsp_dest'] / 'seal' / 'commit.json'),
              'wsp_edit_head_sha256': source['edit']['edited_head_sha256'],
              'frozen_spec_sha256': hashlib.sha256(json.dumps(
                  source['spec'], sort_keys=True).encode('utf-8')).hexdigest(),
              'selection_sha256': sha(dest / 'selection.json'),
              'prompts_sha256': sha(dest / 'seal' / 'prompts.jsonl'),
              'answers_sha256': sha(dest / 'seal' / 'answers.SECRET.jsonl'),
              'dev': DEV, 'challenge': CHALLENGE,
              'answers_accessed_by_prediction': False,
              'independent_distribution': False,
              'model_training': False, 'g3_unlocked': False,
              'production_promotion': False}
    write(dest / 'seal' / 'commit.json', commit)
    print('MIT_BINDING_PREPARED ' + json.dumps({
        'cases': len(cases), 'frozen_head_sha256': commit['wsp_edit_head_sha256'],
        'status': 'SEALED_BEFORE_BLIND_INFERENCE'}, sort_keys=True), flush=True)
    return commit


def verify_seal(dest, root, *, gold=False):
    source = source_lineage(root)
    commit = read(dest / 'seal' / 'commit.json')
    selection = read(dest / 'selection.json')
    spec_sha = hashlib.sha256(json.dumps(
        source['spec'], sort_keys=True).encode('utf-8')).hexdigest()
    require(commit.get('schema') == SCHEMA and
            commit.get('state') == 'SEALED_BEFORE_BLIND_INFERENCE' and
            commit.get('runner_sha256') == sha(__file__) and
            commit.get('wsp_commit_sha256') == sha(source['wsp_dest'] / 'seal' / 'commit.json') and
            commit.get('wsp_edit_head_sha256') == source['edit']['edited_head_sha256'] and
            commit.get('frozen_spec_sha256') == spec_sha and
            commit.get('selection_sha256') == sha(dest / 'selection.json') and
            commit.get('prompts_sha256') == sha(dest / 'seal' / 'prompts.jsonl') and
            commit.get('dev') == DEV and commit.get('challenge') == CHALLENGE and
            commit.get('answers_accessed_by_prediction') is False and
            commit.get('model_training') is False and
            commit.get('g3_unlocked') is False and
            commit.get('production_promotion') is False and
            selection.get('arms') == list(ARMS) and
            selection.get('gap_limit_chars') == GAP_LIMIT and
            selection.get('model_training') is False,
            'SEALED_PROVENANCE_DRIFT')
    if gold:
        require(commit.get('answers_sha256') == sha(dest / 'seal' / 'answers.SECRET.jsonl'),
                'SEALED_ANSWERS_DRIFT')
    return commit, selection, source


def predict(*, root=ROOT_DEFAULT, output=None, device='cuda'):
    dest = path_for(root, output)
    commit, selection, source = verify_seal(dest, root, gold=False)
    require(not (dest / 'blind').exists(), 'BLIND_ALREADY_PREDICTED')
    paths = source['paths']
    network, tok, composite, _, _ = structural._load_runtime(paths, device)
    head = wsp.load_edited_head(source['mit_dest'] / 'checkpoint',
                                 source['checkpoint'], source['edit'], device=device)
    head.eval().requires_grad_(False)
    prompts = rows(dest / 'seal' / 'prompts.jsonl')
    require(len(prompts) == DEV + CHALLENGE and
            len({r['case_id'] for r in prompts}) == len(prompts), 'PROMPT_COUNT_DRIFT')
    out = []
    with torch.no_grad():
        for k, row in enumerate(prompts, 1):
            prompt = row['prompt']
            require(hashlib.sha256(prompt.encode('utf-8')).hexdigest() ==
                    row['prompt_sha256'], 'PROMPT_TEXT_DRIFT')
            prepared, hidden = legacy.previous._capture_inference(
                network, tok, prompt, device, 2048)
            g1 = composite.predict(hidden, prepared,
                family=row['family'], native_stage='G1')
            surface = legacy._infer(head, hidden, prepared['eligible'].to(device),
                g1['token_spans'], prepared, source['spec'], with_margins=True)
            arms, diagnostics = {}, {}
            for arm in ARMS:
                chosen, details = decode(surface, prompt, source['spec'], arm)
                arms[arm] = edges_to_json(chosen)
                diagnostics[arm] = details
            out.append({k: row[k] for k in (
                'case_id', 'split', 'family', 'prompt_sha256')} |
                {'g1_objects': surface['g1_objects'],
                 'pair_margins': surface['pair_margins'],
                 'arms': arms, 'solver': diagnostics})
            if k == 1 or k % 10 == 0 or k == len(prompts):
                print(f'MIT_BINDING_BLIND {k}/{len(prompts)}', flush=True)
    blind = dest / 'blind'
    blind.mkdir(exist_ok=False)
    mit.write_jsonl(blind / 'predictions.jsonl', out)
    lock = {'schema': SCHEMA, 'state': 'BLIND_PREDICTIONS_LOCKED',
            'predictions_sha256': sha(blind / 'predictions.jsonl'),
            'prompts_sha256': commit['prompts_sha256'],
            'wsp_edit_head_sha256': commit['wsp_edit_head_sha256'],
            'gold_accessed': False, 'g3_unlocked': False,
            'production_promotion': False}
    write(blind / 'lock.json', lock)
    print('MIT_BINDING_BLIND_LOCKED ' + json.dumps({
        'cases': len(out), 'predictions_sha256': lock['predictions_sha256']},
        sort_keys=True), flush=True)
    return lock


def _summary(count):
    return {'cases': count['cases'], 'exact': count['exact'],
            'exact_rate': count['exact'] / max(1, count['cases']),
            'tp': count['tp'], 'fp': count['fp'], 'fn': count['fn'],
            'edge_f1': 2 * count['tp'] /
            max(1, 2 * count['tp'] + count['fp'] + count['fn'])}


def missing_attribution(prompt, surface, spec, gold, predicted, arm):
    """Attribute each missing gold edge to a distinct pipeline stage.

    Only score() may call this: gold never crosses the prediction boundary.
    The categories are exhaustive and mutually exclusive.
    """
    objects, all_pairs = legacy.validate_surface(surface)
    lookup = {(kind, (a, b)): i for i, (a, b, kind) in enumerate(objects)}
    source_candidates = {(i, j, t): gain
                         for i, j, t, _, gain in projector.candidate_edges(surface, spec)[1]}
    counts = Counter()
    for edge in gold - predicted:
        src, ss, dst, ds, label = edge
        i = lookup.get((src, ss))
        j = lookup.get((dst, ds))
        if i is None or j is None:
            counts['g1_unreachable'] += 1
            continue
        t = BIND_CLASSES.index(label)
        if (i, j, t) not in source_candidates:
            counts['candidate_below_threshold'] += 1
            # Candidate is present in the pair matrix but has insufficient
            # margin to be included by the unmodified exact MILP.
            continue
        if not allowed(prompt, objects[i], objects[j], label, arm):
            counts['gold_blocked_by_rule'] += 1
            continue
        counts['solver_competition'] += 1
        own_gain = source_candidates[(i, j, t)]
        if any(ii == i and tt == t and jj != j and gain > own_gain
               for (ii, jj, tt), gain in source_candidates.items()):
            counts['stronger_same_source_rival'] += 1
    require(sum(counts[k] for k in (
        'g1_unreachable', 'candidate_below_threshold',
        'gold_blocked_by_rule', 'solver_competition')) == len(gold - predicted),
        'MISSED_GOLD_ATTRIBUTION_INCOMPLETE')
    return counts


def score(*, root=ROOT_DEFAULT, output=None):
    dest = path_for(root, output)
    commit, selection, source = verify_seal(dest, root, gold=True)
    require(not (dest / 'scored').exists(), 'ALREADY_SCORED')
    lock = read(dest / 'blind' / 'lock.json')
    require(lock.get('schema') == SCHEMA and
            lock.get('state') == 'BLIND_PREDICTIONS_LOCKED' and
            lock.get('predictions_sha256') == sha(dest / 'blind' / 'predictions.jsonl') and
            lock.get('prompts_sha256') == commit['prompts_sha256'] and
            lock.get('wsp_edit_head_sha256') == commit['wsp_edit_head_sha256'] and
            lock.get('gold_accessed') is False and
            lock.get('g3_unlocked') is False, 'BLIND_LOCK_INVALID')
    # This is the FIRST read of secret gold since prepare sealed it.
    gold_rows = rows(dest / 'seal' / 'answers.SECRET.jsonl')
    predicted_rows = rows(dest / 'blind' / 'predictions.jsonl')
    questions = {r['case_id']: r for r in rows(dest / 'seal' / 'prompts.jsonl')}
    answers = {r['case_id']: r for r in gold_rows}
    require(len(predicted_rows) == len(answers) == len(questions) == DEV + CHALLENGE and
            len(answers) == len({r['case_id'] for r in predicted_rows}), 'CASE_ALIGNMENT')
    aggregate = {split: {arm: Counter() for arm in ARMS}
                 for split in ('dev', 'challenge')}
    attribution = {split: {arm: Counter() for arm in ARMS}
                   for split in ('dev', 'challenge')}
    family = {split: {fam: {arm: Counter() for arm in ARMS}
                      for fam in ('encounter', 'allocation')}
              for split in ('dev', 'challenge')}
    label_errors = {split: {arm: {} for arm in ARMS}
                    for split in ('dev', 'challenge')}
    intervention = {split: {arm: Counter() for arm in ARMS if arm != 'mit_wsp_baseline'}
                    for split in ('dev', 'challenge')}
    per_case = []
    for record in predicted_rows:
        key = record['case_id']; truth_row = answers.get(key); question = questions.get(key)
        require(truth_row is not None and question is not None and
                all(record.get(k) == truth_row.get(k) == question.get(k)
                    for k in ('case_id', 'split', 'family', 'prompt_sha256')) and
                hashlib.sha256(question['prompt'].encode('utf-8')).hexdigest() ==
                    record['prompt_sha256'], 'MISALIGNED_CASE')
        gold = legacy.truth(truth_row['graph'])
        surface = {'g1_objects': record['g1_objects'],
                   'binding_edges': [], 'pair_margins': record['pair_margins']}
        record_metrics = {'case_id': key, 'split': record['split'],
                          'family': record['family'], 'arms': {}}
        baseline_edges = edges_from_json(record['arms']['mit_wsp_baseline'])
        for arm in ARMS:
            chosen = edges_from_json(record['arms'][arm])
            tp, fp, fn = len(chosen & gold), len(chosen - gold), len(gold - chosen)
            metrics = {'exact': int(chosen == gold), 'tp': tp, 'fp': fp, 'fn': fn}
            diagnostics = missing_attribution(question['prompt'], surface,
                                               source['spec'], gold, chosen, arm)
            record_metrics['arms'][arm] = {**metrics, 'missing': dict(diagnostics)}
            for count in (aggregate[record['split']][arm],
                          family[record['split']][record['family']][arm]):
                count.update({'cases': 1, 'exact': metrics['exact'],
                              'tp': tp, 'fp': fp, 'fn': fn})
            attribution[record['split']][arm].update(diagnostics)
            by_label = label_errors[record['split']][arm]
            for label in set(e[4] for e in gold | chosen):
                label_counter = by_label.setdefault(label, Counter())
                good = {e for e in gold if e[4] == label}
                predicted_of_type = {e for e in chosen if e[4] == label}
                label_counter.update({'tp': len(good & predicted_of_type),
                                      'fp': len(predicted_of_type - good),
                                      'fn': len(good - predicted_of_type)})
            if arm != 'mit_wsp_baseline':
                impact = intervention[record['split']][arm]
                impact.update({
                    'rescued_true_edges': len((chosen & gold) - (baseline_edges & gold)),
                    'lost_true_edges': len((baseline_edges & gold) - (chosen & gold)),
                    'removed_false_edges': len((baseline_edges - gold) - (chosen - gold)),
                    'new_false_edges': len((chosen - gold) - (baseline_edges - gold))})
        per_case.append(record_metrics)
    summary = {split: {arm: _summary(c) for arm, c in arms.items()}
               for split, arms in aggregate.items()}
    family_summary = {split: {fam: {arm: _summary(c) for arm, c in arms.items()}
                              for fam, arms in by_family.items()}
                      for split, by_family in family.items()}
    report = {'schema': SCHEMA, 'status': 'MIT_BINDING_FRESH_GENERALIZATION_MEASURED_NOT_CERTIFIED',
              'splits': summary, 'families': family_summary,
              'missing_attribution': {split: {arm: dict(c) for arm, c in arms.items()}
                                      for split, arms in attribution.items()},
              'errors_by_relation': {split: {arm: {label: dict(c) for label, c in labels.items()}
                                             for arm, labels in arms.items()}
                                     for split, arms in label_errors.items()},
              'intervention_impact': {split: {arm: dict(c) for arm, c in arms.items()}
                                      for split, arms in intervention.items()},
              'deltas_vs_baseline': {
                  split: {arm: {
                      'edge_f1_delta': score['edge_f1'] - by_arm['mit_wsp_baseline']['edge_f1'],
                      'exact_delta': score['exact'] - by_arm['mit_wsp_baseline']['exact']}
                  for arm, score in by_arm.items() if arm != 'mit_wsp_baseline'}
                  for split, by_arm in summary.items()},
              'frozen_head_sha256': commit['wsp_edit_head_sha256'],
              'answers_sha256': commit['answers_sha256'],
              'predictions_sha256': lock['predictions_sha256'],
              'model_training': False, 'g3_unlocked': False,
              'production_promotion': False,
              'limitations': 'Same synthetic generators, fresh prompts. 45-character gap and semicolon scope are untrained hypotheses, not universal grammar.'}
    scored = dest / 'scored'
    scored.mkdir(exist_ok=False)
    write(scored / 'report.json', report)
    mit.write_jsonl(scored / 'cases.jsonl', per_case)
    print('MIT_BINDING_SCORED ' + json.dumps({
        'status': report['status'], 'deltas': report['deltas_vs_baseline'],
        'baseline_missing_attribution': report['missing_attribution']['challenge']['mit_wsp_baseline']},
        sort_keys=True), flush=True)
    return report


def run(*, root=ROOT_DEFAULT, output=None, device='cuda'):
    dest = path_for(root, output)
    if not (dest / 'seal' / 'commit.json').is_file():
        require(not dest.exists(), 'INCOMPLETE_SEAL')
        prepare(root=root, output=output)
    if not (dest / 'blind' / 'lock.json').is_file():
        predict(root=root, output=output, device=device)
    if not (dest / 'scored' / 'report.json').is_file():
        return score(root=root, output=output)
    result = read(dest / 'scored' / 'report.json')
    print('MIT_BINDING_ALREADY_SCORED ' + json.dumps({
        'status': result['status'], 'deltas': result['deltas_vs_baseline']},
        sort_keys=True), flush=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for cmd in ('run', 'prepare', 'predict', 'score'):
        arg = sub.add_parser(cmd)
        arg.add_argument('--root', default=ROOT_DEFAULT)
        arg.add_argument('--output')
        if cmd in ('run', 'predict'):
            arg.add_argument('--device', default='cuda')
    args = parser.parse_args(argv)
    kw = {'root': args.root, 'output': args.output}
    if args.command in ('run', 'predict'):
        kw['device'] = args.device
    return globals()[args.command](**kw)


if __name__ == '__main__':
    main()
