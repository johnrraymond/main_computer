"""Gamer MIT-SCOPE-0003: sealed, frozen-weight scope-generalization experiment.

Run from main_computer/nanojev:
    python -m tools.nanojev_gamer_v1_mit_scope_0003 run --device cuda

The experiment constructs paired, graph-equivalent prompts from fresh author-marked
examples and evaluates exactly the same frozen MIT-WSP head on all prompts.
No manual JSON input, training, gradients, tuning on held-out answers or G3 gates.

Variants: original, period-separated records, newline-separated records,
reordered records, irrelevant record, valid intra-record semicolon, and a
mixed reordered/period/distractor stress case.

Compare the 0002 semicolon-only decoder with a generic action-anchored scope
hypothesis. A punctuation fragment lacking any predicted 'action' continues
its preceding scope; one with an action starts a new scope. This is a
precommitted, fallible hypothesis, not a learned syntactic parser.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import re

import torch

from tools import nanojev_gamer_v1_mit_binding_0002 as old
from tools import nanojev_gamer_v1_graph_corpus as corpus
from tools import nanojev_gamer_v1_graph_contract as contract
from tools import nanojev_gamer_v1_mit as mit
from tools import nanojev_gamer_v1_g2_posttrain_graph_bakeoff_0088 as legacy
from tools import nanojev_gamer_v1_g2_structural_learning_0089 as structural
from tools import nanojev_gamer_v1_mit_wsp as wsp
from tools import nanojev_gamer_v1_mit_decoder as projector
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES

SCHEMA = 'nanojev.gamer.graph.g2.mit-scope-0003.v1'
CAMPAIGN = 'oracle-fabric-g2-mit-scope-0003'
ROOT_DEFAULT = '../runtime/reports/gamer-v1'
SEED = 261123
DEV_BASES = 16
CHALLENGE_BASES = 16
VARIANTS = ('original', 'periods', 'newlines', 'reordered', 'distractor',
            'intra_semicolon', 'mixed')
ARMS = ('mit_wsp_baseline', 'identity', 'identity_clause',
        'identity_gap45_clause', 'identity_scope')
BLOCKED_MARGIN = old.BLOCKED_MARGIN


def require(ok, code):
    if not ok:
        raise ValueError('MIT_SCOPE_' + code)


def path_for(root, output):
    return (Path(output) if output else Path(root) / CAMPAIGN).resolve()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(s):
    return hashlib.sha256(s.encode('utf-8')).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def rows(path):
    return list(mit.rows(path))


def write(path, obj):
    mit.write(path, obj)


def group_regions(prompt, family):
    """Identify author-controlled record *regions* in preparation only.

    No corpus template knowledge is used by the scope decoder at inference.
    These ranges are strictly for generating paired challenge examples.
    """
    if family == 'allocation':
        starts = [('Each has a benefit and cost: ', '. Choose the plan')]
    elif family == 'encounter':
        starts = [('Observed response counts: ', '. Consequences per pairing:'),
                  ('Consequences per pairing: ', '. Select a single choice')]
    else:
        raise ValueError('MIT_SCOPE_UNKNOWN_FAMILY')
    regions = []
    for start_marker, end_marker in starts:
        start_idx = prompt.find(start_marker)
        require(start_idx >= 0, 'RECORD_START_NOT_FOUND')
        start = start_idx + len(start_marker)
        end = prompt.find(end_marker, start)
        require(end > start, 'RECORD_END_NOT_FOUND')
        regions.append((start, end))
    return regions


def _pieces(prompt, family, variant, ordinal):
    """A sequence of unchanged source slices or new text, with full span maps.

    Original annotations never need text-matching guesses: every object must
    lie wholly inside exactly one copied slice. Node IDs and edges persist.
    """
    require(variant in VARIANTS, 'BAD_VARIANT')
    regions = group_regions(prompt, family)
    pieces = []
    cursor = 0

    def copied(a, b):
        require(0 <= a <= b <= len(prompt), 'BAD_COPY_RANGE')
        if a != b:
            pieces.append(('source', a, b, prompt[a:b]))

    def literal(text):
        pieces.append(('literal', None, None, text))

    def record(start, end, split_inside):
        if not split_inside:
            copied(start, end)
            return
        mark = ' cost ' if family == 'allocation' else ' yields '
        spot = prompt.find(mark, start, end)
        require(spot >= 0, 'INTRA_RECORD_INSERTION_NOT_FOUND')
        # Replace only the whitespace before cost/yields; never split a node.
        copied(start, spot)
        literal('; ')
        copied(spot + 1, end)

    for region_num, (start, end) in enumerate(regions):
        copied(cursor, start)
        raw = prompt[start:end]
        cuts = list(re.finditer(r';\s*', raw))
        bounds = []
        start_piece = start
        for m in cuts:
            bounds.append((start_piece, start + m.start()))
            start_piece = start + m.end()
        bounds.append((start_piece, end))
        require(all(b > a for a, b in bounds), 'EMPTY_RECORD')
        if variant in ('reordered', 'mixed'):
            bounds = list(reversed(bounds))
        separator = { 'periods': '. ', 'newlines': '\n', 'mixed': '. ' }.get(variant, '; ')
        for idx, (a, b) in enumerate(bounds):
            if idx:
                literal(separator)
            # For a realistic cross-boundary case, each independent record
            # is split into two fragments that still describe the SAME event.
            record(a, b, variant == 'intra_semicolon' and region_num == len(regions) - 1)
        if variant in ('distractor', 'mixed') and region_num == len(regions) - 1:
            literal(separator)
            literal(f'Unrelated archive note (not a game option): the lobby has {17 + ordinal % 11} chairs')
        cursor = end
    copied(cursor, len(prompt))
    return pieces


def transform(base, variant, ordinal):
    """Keep graph edges/values/operations fixed; remap only textual node spans."""
    prompt = base['prompt']
    pieces = _pieces(prompt, base['family'], variant, ordinal)
    out_text = []
    copied_offsets = []
    pos = 0
    for origin, a, b, text in pieces:
        out_text.append(text)
        if origin == 'source':
            copied_offsets.append((a, b, pos))
        pos += len(text)
    rewritten = ''.join(out_text)
    graph = json.loads(json.dumps(base['graph']))
    for node in graph['nodes']:
        if 'span' not in node:
            continue
        s, e = node['span']
        hits = [(a, b, offset) for a, b, offset in copied_offsets if a <= s < e <= b]
        require(len(hits) == 1, 'SOURCE_ANNOTATION_SPLIT_OR_DUPLICATED')
        a, b, offset = hits[0]
        new_span = [offset + s - a, offset + e - a]
        require(rewritten[new_span[0]:new_span[1]] == prompt[s:e],
                'ANNOTATION_TEXT_CHANGED')
        node['span'] = new_span
    contract.validate_graph(graph, complete=True)
    require(contract.execute_graph(graph)['decisions'] == base['expected_decisions'],
            'GAME_SEMANTICS_CHANGED')
    if variant == 'intra_semicolon':
        require(any(crosses_separator(rewritten, graph, edge)
                    for edge in graph['edges'] if edge['type'] in ('measures', 'applies_to')),
                'INTRA_RECORD_CROSSING_NOT_CREATED')
    return rewritten, graph


def crosses_separator(prompt, graph, edge):
    nodes = {n['id']: n for n in graph['nodes']}
    left = nodes.get(edge['src'], {}).get('span')
    right = nodes.get(edge['dst'], {}).get('span')
    if not left or not right:
        return False
    a, b = sorted((left, right), key=lambda x: x[0])
    return ';' in prompt[a[1]:b[0]]


def scopes(prompt, objects):
    """Generic punctuation scopes with an action-anchored continuation rule.

    Does not look at gold, game family, generator templates, entity names or
    target answers. An unanchored fragment inherits the previous segment's
    scope; this makes an intra-record semicolon non-fatal.
    """
    points = [0]
    for m in re.finditer(r'[;.\n]', prompt):
        i = m.start()
        # A decimal point is not a sentence boundary.
        if prompt[i] == '.' and i > 0 and i + 1 < len(prompt) and \
                prompt[i-1].isdigit() and prompt[i+1].isdigit():
            continue
        points.append(i + 1)
    points.append(len(prompt) + 1)
    parts = [(a, b) for a, b in zip(points, points[1:]) if b > a]
    def segment_of(span):
        require(0 <= span[0] < span[1] <= len(prompt), 'OBJECT_OUT_OF_BOUNDS')
        # G1 may propose a malformed object crossing punctuation. Anchor
        # that proposal to its first character instead of aborting an
        # entire blind cohort because of a G1 prediction error.
        matches = [n for n, (a, b) in enumerate(parts) if a <= span[0] < b]
        require(len(matches) == 1, 'NO_SCOPE_FOR_OBJECT')
        return matches[0]
    per_object = [segment_of(obj) for obj in objects]
    anchored = {s for obj, s in zip(objects, per_object) if obj[2] == 'action'}
    scope_id = []
    current = -1
    for index in range(len(parts)):
        if current < 0 or index in anchored:
            current += 1
        scope_id.append(current)
    return [scope_id[s] for s in per_object]


def allowed(prompt, source, target, label, arm, *, source_scope=None,
            target_scope=None):
    if arm in old.ARMS:
        return old.allowed(prompt, source, target, label, arm)
    require(arm == 'identity_scope', 'UNKNOWN_ARM')
    if label == 'refers_to':
        return (prompt[source[0]:source[1]].strip().casefold() ==
                prompt[target[0]:target[1]].strip().casefold())
    if label in ('measures', 'applies_to'):
        require(source_scope is not None and target_scope is not None,
                'SCOPE_REQUIRED')
        return source_scope == target_scope
    return True


def restrict_surface(surface, prompt, arm):
    if arm in old.ARMS:
        return old.restrict_surface(surface, prompt, arm)
    require(arm == 'identity_scope', 'UNKNOWN_ARM')
    objs, pairs = legacy.validate_surface(surface)
    IDs = scopes(prompt, objs)
    rejected = 0
    updated_pairs = []
    for i, j, margins in surface['pair_margins']:
        changes = list(margins)
        for k, label in enumerate(BIND_CLASSES[1:]):
            if not allowed(prompt, objs[i], objs[j], label, arm,
                           source_scope=IDs[i], target_scope=IDs[j]):
                changes[k] = BLOCKED_MARGIN
                rejected += 1
        updated_pairs.append([i, j, changes])
    return {**surface, 'pair_margins': updated_pairs}, rejected


def decode(surface, prompt, spec, arm):
    constrained, rejected = restrict_surface(surface, prompt, arm)
    predicted, status = projector.decode(constrained, spec, return_diagnostics=True)
    return predicted, {'blocked_pair_labels': rejected, **status}


def source_lineage(root):
    source = old.source_lineage(root)
    return source


def new_cases(paths, root, wsp_dest):
    rng = random.Random(SEED)
    seen = mit.known_hashes(paths, root)
    for campaign in (Path(root) / mit.CAMPAIGN, wsp_dest,
                     Path(root) / old.CAMPAIGN):
        prompt_file = campaign / 'seal' / 'prompts.jsonl'
        if prompt_file.is_file():
            for r in rows(prompt_file):
                seen.add(r['prompt_sha256'])
    cases = []
    serial = 261_123_000
    for split, count in (('dev', DEV_BASES), ('challenge', CHALLENGE_BASES)):
        for i in range(count):
            factory = corpus.encounter if i % 2 == 0 else corpus.allocation
            for _ in range(4096):
                base = factory(rng, 'test', serial)
                serial += 1
                if digest(base['prompt']) not in seen:
                    break
            else:
                raise ValueError('MIT_SCOPE_FRESH_BASE_EXHAUSTED')
            require(base['annotation_provenance'] ==
                    'AUTHOR_MARKED_IN_SOURCE_GENERATOR', 'SOURCE_GOLD_REQUIRED')
            transformed = []
            for variant in VARIANTS:
                text, graph = transform(base, variant, serial)
                fp = digest(text)
                require(fp not in seen, 'PROMPT_COLLISION')
                seen.add(fp)
                transformed.append({
                    'case_id': f'mit-scope-{split}-{i+1:04}-{variant}',
                    'base_id': f'{split}-{i+1:04}', 'variant': variant,
                    'split': split, 'family': base['family'],
                    'prompt': text, 'prompt_sha256': fp, 'graph': graph})
            cases.extend(transformed)
    require(len(cases) == (DEV_BASES + CHALLENGE_BASES) * len(VARIANTS),
            'CASE_COUNT')
    return cases


def prepare(*, root=ROOT_DEFAULT, output=None):
    dest = path_for(root, output)
    require(not dest.exists() and dest.parent.is_dir(),
            'CAMPAIGN_EXISTS_OR_PARENT_MISSING')
    source = source_lineage(root)
    cases = new_cases(source['paths'], root, source['wsp_dest'])
    dest.mkdir(exist_ok=False)
    (dest / 'seal').mkdir(exist_ok=False)
    fields = ('case_id', 'base_id', 'variant', 'split', 'family',
              'prompt', 'prompt_sha256')
    mit.write_jsonl(dest / 'seal' / 'prompts.jsonl', [
        {k: r[k] for k in fields} for r in cases])
    mit.write_jsonl(dest / 'seal' / 'answers.SECRET.jsonl', [
        {k: r[k] for k in fields if k != 'prompt'} | {'graph': r['graph']}
        for r in cases])
    selection = {
        'schema': SCHEMA, 'arms': list(ARMS), 'variants': list(VARIANTS),
        'dev_bases': DEV_BASES, 'challenge_bases': CHALLENGE_BASES,
        'hypothesis': ('A punctuation fragment with no predicted action is a '
                       'continuation of the previous action-anchored scope'),
        'no_gold_during_prediction': True, 'trained': False,
        'scope_uses_only_g1_spans_and_raw_prompt': True,
        'model_promotion': False, 'g3_unlocked': False,
        'precommitted_decision': (
            'Compare scope to semicolon in each split on periods, newlines, '
            'and intra_semicolon; report any original-format regression. '
            'No promotion based on this experiment.')}
    write(dest / 'selection.json', selection)
    spec_sha = digest(json.dumps(source['spec'], sort_keys=True))
    commit = {'schema': SCHEMA, 'state': 'SEALED_BEFORE_BLIND_INFERENCE',
              'runner_sha256': sha(__file__),
              'parent_0002_sha256': sha(old.__file__),
              'source_wsp_sha256': sha(source['wsp_dest'] / 'seal' / 'commit.json'),
              'frozen_head_sha256': source['edit']['edited_head_sha256'],
              'spec_sha256': spec_sha,
              'selection_sha256': sha(dest / 'selection.json'),
              'prompts_sha256': sha(dest / 'seal' / 'prompts.jsonl'),
              'answers_sha256': sha(dest / 'seal' / 'answers.SECRET.jsonl'),
              'cases': len(cases), 'variant_count': len(VARIANTS),
              'dev_bases': DEV_BASES, 'challenge_bases': CHALLENGE_BASES,
              'model_training': False, 'gold_during_prediction': False,
              'g3_unlocked': False, 'production_promotion': False}
    write(dest / 'seal' / 'commit.json', commit)
    print('MIT_SCOPE_PREPARED ' + json.dumps({
        'cases': len(cases), 'base_games': DEV_BASES + CHALLENGE_BASES,
        'variants': list(VARIANTS), 'frozen_head_sha256': commit['frozen_head_sha256'],
        'status': commit['state']}, sort_keys=True), flush=True)
    return commit


def verify(dest, root, *, gold=False):
    source = source_lineage(root)
    commit = read(dest / 'seal' / 'commit.json')
    selection = read(dest / 'selection.json')
    required = {
        'schema': SCHEMA, 'state': 'SEALED_BEFORE_BLIND_INFERENCE',
        'runner_sha256': sha(__file__), 'parent_0002_sha256': sha(old.__file__),
        'source_wsp_sha256': sha(source['wsp_dest'] / 'seal' / 'commit.json'),
        'frozen_head_sha256': source['edit']['edited_head_sha256'],
        'spec_sha256': digest(json.dumps(source['spec'], sort_keys=True)),
        'selection_sha256': sha(dest / 'selection.json'),
        'prompts_sha256': sha(dest / 'seal' / 'prompts.jsonl'),
        'cases': (DEV_BASES + CHALLENGE_BASES) * len(VARIANTS),
        'variant_count': len(VARIANTS),
        'dev_bases': DEV_BASES, 'challenge_bases': CHALLENGE_BASES,
        'model_training': False, 'gold_during_prediction': False,
        'g3_unlocked': False, 'production_promotion': False}
    require(all(commit.get(k) == v for k, v in required.items()) and
            selection.get('arms') == list(ARMS) and
            selection.get('variants') == list(VARIANTS) and
            selection.get('trained') is False and
            selection.get('no_gold_during_prediction') is True,
            'SEAL_DRIFT')
    if gold:
        require(commit.get('answers_sha256') == sha(dest / 'seal' / 'answers.SECRET.jsonl'),
                'ANSWER_DRIFT')
    return commit, selection, source


def predict(*, root=ROOT_DEFAULT, output=None, device='cuda'):
    dest = path_for(root, output)
    commit, selection, source = verify(dest, root, gold=False)
    require(not (dest / 'blind').exists(), 'BLIND_ALREADY_EXISTS')
    paths = source['paths']
    net, tok, composite, _, _ = structural._load_runtime(paths, device)
    head = wsp.load_edited_head(source['mit_dest'] / 'checkpoint',
                                source['checkpoint'], source['edit'], device=device)
    head.eval().requires_grad_(False)
    questions = rows(dest / 'seal' / 'prompts.jsonl')
    require(len(questions) == commit['cases'] and
            len({q['case_id'] for q in questions}) == len(questions),
            'PROMPT_COUNT_DRIFT')
    outputs = []
    with torch.no_grad():
        for k, row in enumerate(questions, 1):
            prompt = row['prompt']
            require(digest(prompt) == row['prompt_sha256'], 'PROMPT_HASH_DRIFT')
            prepared, hidden = legacy.previous._capture_inference(net, tok, prompt, device, 2048)
            g1 = composite.predict(hidden, prepared,
                                   family=row['family'], native_stage='G1')
            surface = legacy._infer(head, hidden, prepared['eligible'].to(device),
                                    g1['token_spans'], prepared, source['spec'],
                                    with_margins=True)
            chosen, solvers = {}, {}
            for arm in ARMS:
                predicted, diag = decode(surface, prompt, source['spec'], arm)
                chosen[arm] = old.edges_to_json(predicted)
                solvers[arm] = diag
            outputs.append({key: row[key] for key in (
                'case_id', 'base_id', 'variant', 'split', 'family', 'prompt_sha256')} |
                {'g1_objects': surface['g1_objects'],
                 'pair_margins': surface['pair_margins'],
                 'arms': chosen, 'solver': solvers})
            if k == 1 or k % 16 == 0 or k == len(questions):
                print(f'MIT_SCOPE_BLIND {k}/{len(questions)}', flush=True)
    blind = dest / 'blind'
    blind.mkdir(exist_ok=False)
    mit.write_jsonl(blind / 'predictions.jsonl', outputs)
    lock = {'schema': SCHEMA, 'state': 'BLIND_PREDICTIONS_LOCKED',
            'predictions_sha256': sha(blind / 'predictions.jsonl'),
            'prompts_sha256': commit['prompts_sha256'],
            'frozen_head_sha256': commit['frozen_head_sha256'],
            'gold_accessed': False, 'g3_unlocked': False,
            'production_promotion': False}
    write(blind / 'lock.json', lock)
    print('MIT_SCOPE_BLIND_LOCKED ' + json.dumps({
        'cases': len(outputs), 'predictions_sha256': lock['predictions_sha256']},
        sort_keys=True), flush=True)
    return lock


def missing_attribution(prompt, surface, spec, correct, chosen, arm, group_ids=None):
    """Partition actual missing gold edges without double counting.

    Only score() calls this method. The original frozen candidate universe
    determines reachability. A rule-blocked candidate is *not* incorrectly
    reported as scorer failure or solver competition.
    """
    objects, _ = legacy.validate_surface(surface)
    positions = {(kind, (a, b)): i for i, (a, b, kind) in enumerate(objects)}
    if arm == 'identity_scope' and group_ids is None:
        group_ids = scopes(prompt, objects)
    candidate_set = {(i, j, t): gain for i, j, t, _, gain
                     in projector.candidate_edges(surface, spec)[1]}
    stats = Counter()
    for edge in correct - chosen:
        src, ss, dst, ds, label = edge
        i, j = positions.get((src, ss)), positions.get((dst, ds))
        if i is None or j is None:
            stats['g1_unreachable'] += 1
            continue
        t = BIND_CLASSES.index(label)
        if (i, j, t) not in candidate_set:
            stats['candidate_below_threshold'] += 1
            continue
        if arm == 'identity_scope':
            valid = allowed(prompt, objects[i], objects[j], label, arm,
                            source_scope=group_ids[i], target_scope=group_ids[j])
        else:
            valid = allowed(prompt, objects[i], objects[j], label, arm)
        if not valid:
            stats['gold_blocked_by_rule'] += 1
            continue
        stats['solver_competition'] += 1
        gain = candidate_set[(i, j, t)]
        if any(ii == i and jj != j and tt == t and rival > gain
               for (ii, jj, tt), rival in candidate_set.items()):
            stats['stronger_same_source_rival'] += 1
    require(sum(stats[k] for k in (
        'g1_unreachable', 'candidate_below_threshold',
        'gold_blocked_by_rule', 'solver_competition')) == len(correct - chosen),
        'ATTRIBUTION_MUST_PARTITION_MISSING')
    return stats


def score(*, root=ROOT_DEFAULT, output=None):
    dest = path_for(root, output)
    commit, selection, source = verify(dest, root, gold=True)
    require(not (dest / 'scored').exists(), 'ALREADY_SCORED')
    lock = read(dest / 'blind' / 'lock.json')
    require(lock.get('schema') == SCHEMA and
            lock.get('state') == 'BLIND_PREDICTIONS_LOCKED' and
            lock.get('predictions_sha256') == sha(dest / 'blind' / 'predictions.jsonl') and
            lock.get('prompts_sha256') == commit['prompts_sha256'] and
            lock.get('frozen_head_sha256') == commit['frozen_head_sha256'] and
            lock.get('gold_accessed') is False and
            lock.get('production_promotion') is False,
            'BLIND_LOCK_DRIFT')
    # Only after the prediction hash has been locked may these labels be read.
    truth_rows = rows(dest / 'seal' / 'answers.SECRET.jsonl')
    prediction_rows = rows(dest / 'blind' / 'predictions.jsonl')
    prompts = {r['case_id']: r for r in rows(dest / 'seal' / 'prompts.jsonl')}
    gold = {r['case_id']: r for r in truth_rows}
    require(len(prompts) == len(gold) == len(prediction_rows) == commit['cases'] and
            len({r['case_id'] for r in prediction_rows}) == len(prediction_rows),
            'SCORE_CASE_COUNT')
    counts = {split: {variant: {arm: Counter() for arm in ARMS}
                      for variant in VARIANTS}
              for split in ('dev', 'challenge')}
    family_counts = {split: {variant: {family: {arm: Counter() for arm in ARMS}
                                         for family in ('allocation', 'encounter')}
                             for variant in VARIANTS}
                     for split in ('dev', 'challenge')}
    attribution = {split: {variant: {arm: Counter() for arm in ARMS}
                           for variant in VARIANTS}
                   for split in ('dev', 'challenge')}
    impact = {split: {variant: {arm: Counter() for arm in ARMS if arm != 'mit_wsp_baseline'}
                      for variant in VARIANTS}
              for split in ('dev', 'challenge')}
    cross_edges = Counter()
    per_case = []
    for record in prediction_rows:
        key = record['case_id']
        q, answer = prompts.get(key), gold.get(key)
        require(q is not None and answer is not None and
                all(record.get(k) == q.get(k) == answer.get(k) for k in (
                    'case_id', 'base_id', 'variant', 'split', 'family', 'prompt_sha256')) and
                digest(q['prompt']) == record['prompt_sha256'],
                'CASE_ALIGNMENT_DRIFT')
        correct = legacy.truth(answer['graph'])
        require(all(arm in record['arms'] for arm in ARMS), 'ARM_MISSING')
        surface = {'g1_objects': record['g1_objects'],
                   'binding_edges': [], 'pair_margins': record['pair_margins']}
        obj, _ = legacy.validate_surface(surface)
        group_ids = scopes(q['prompt'], obj)
        obj_lookup = {(kind, (a, b)): n for n, (a, b, kind) in enumerate(obj)}
        original = old.edges_from_json(record['arms']['mit_wsp_baseline'])
        details = {'case_id': key, 'base_id': record['base_id'],
                   'variant': record['variant'], 'split': record['split'],
                   'family': record['family'], 'arms': {}}
        for edge in correct:
            if (edge[4] in ('measures', 'applies_to') and
                    ';' in q['prompt'][min(edge[1][1], edge[3][1]):max(edge[1][0], edge[3][0])]):
                cross_edges[(record['split'], record['variant'])] += 1
        for arm in ARMS:
            chosen = old.edges_from_json(record['arms'][arm])
            c = counts[record['split']][record['variant']][arm]
            metrics = {'tp': len(chosen & correct), 'fp': len(chosen - correct),
                       'fn': len(correct - chosen), 'exact': int(chosen == correct)}
            c.update({'cases': 1, **metrics})
            family_counts[record['split']][record['variant']][record['family']][arm].update(
                {'cases': 1, **metrics})
            diag = missing_attribution(q['prompt'], surface, source['spec'],
                                       correct, chosen, arm, group_ids)
            attribution[record['split']][record['variant']][arm].update(diag)
            details['arms'][arm] = {**metrics, 'missing': dict(diag)}
            if arm != 'mit_wsp_baseline':
                impact[record['split']][record['variant']][arm].update({
                    'rescued_true_edges': len((chosen & correct) - (original & correct)),
                    'lost_true_edges': len((original & correct) - (chosen & correct)),
                    'removed_false_edges': len((original - correct) - (chosen - correct)),
                    'introduced_false_edges': len((chosen - correct) - (original - correct))})
        per_case.append(details)
    summary = {split: {variant: {arm: old._summary(c) for arm, c in arms.items()}
                       for variant, arms in variants.items()}
               for split, variants in counts.items()}
    family_summary = {split: {variant: {family: {arm: old._summary(c)
                                               for arm, c in arms.items()}
                                        for family, arms in families.items()}
                              for variant, families in variants.items()}
                      for split, variants in family_counts.items()}
    delta = {split: {variant: {arm: {
        'edge_f1_delta': record['edge_f1'] - arms['mit_wsp_baseline']['edge_f1'],
        'exact_delta': record['exact'] - arms['mit_wsp_baseline']['exact']}
        for arm, record in arms.items() if arm != 'mit_wsp_baseline'}
        for variant, arms in variants.items()}
        for split, variants in summary.items()}
    robustness = {split: {arm: {
        'lowest_variant_edge_f1': min(summary[split][v][arm]['edge_f1'] for v in VARIANTS),
        'original_edge_f1': summary[split]['original'][arm]['edge_f1'],
        'worst_delta_vs_original': min(summary[split][v][arm]['edge_f1'] -
                                       summary[split]['original'][arm]['edge_f1']
                                       for v in VARIANTS if v != 'original'),
        'intra_semicolon_gold_blocked': attribution[split]['intra_semicolon'][arm].get(
            'gold_blocked_by_rule', 0)}
        for arm in ARMS} for split in ('dev', 'challenge')}
    # Paired case-level effects use each game's unchanged ground-truth graph;
    # compare the *same prompt* under both decoders, never different games.
    paired = {split: {variant: Counter() for variant in VARIANTS}
              for split in ('dev', 'challenge')}
    for case in per_case:
        previous = case['arms']['identity_clause']
        current = case['arms']['identity_scope']
        def f1(m):
            return 2 * m['tp'] / max(1, 2 * m['tp'] + m['fp'] + m['fn'])
        difference = f1(current) - f1(previous)
        c = paired[case['split']][case['variant']]
        c['cases'] += 1
        c['improved_f1'] += int(difference > 1e-12)
        c['regressed_f1'] += int(difference < -1e-12)
        c['tied_f1'] += int(abs(difference) <= 1e-12)
        c['exact_rescued'] += int(current['exact'] > previous['exact'])
        c['exact_lost'] += int(current['exact'] < previous['exact'])
        # Edge count, unlike per-case F1, is additive.
        c['correct_edge_gain'] += current['tp'] - previous['tp']
        c['false_edge_reduction'] += previous['fp'] - current['fp']
    checks = {split: {
        'scope_f1_exceeds_semicolon_' + variant: (
            summary[split][variant]['identity_scope']['edge_f1'] >
            summary[split][variant]['identity_clause']['edge_f1'])
        for variant in ('periods', 'newlines', 'intra_semicolon')}
        for split in ('dev', 'challenge')}
    for split in ('dev', 'challenge'):
        checks[split]['scope_original_f1_delta'] = (
            summary[split]['original']['identity_scope']['edge_f1'] -
            summary[split]['original']['identity_clause']['edge_f1'])
        checks[split]['scope_original_exact_delta'] = (
            summary[split]['original']['identity_scope']['exact'] -
            summary[split]['original']['identity_clause']['exact'])
    hypothesis_support = all(
        value is True for split in checks.values() for key, value in split.items()
        if key.startswith('scope_f1_exceeds_semicolon_'))
    report = {'schema': SCHEMA,
              'status': 'MIT_SCOPE_BLIND_ROBUSTNESS_MEASURED_NOT_CERTIFIED',
              'summary': summary, 'families': family_summary,
              'paired_comparison': {split: {variant: dict(count)
                                            for variant, count in variants.items()}
                                    for split, variants in paired.items()},
              'precommitted_checks': checks,
              'scope_robustness_hypothesis_supported': hypothesis_support,
              'deltas_vs_baseline': delta,
              'robustness': robustness,
              'missing_attribution': {s: {v: {a: dict(c) for a, c in arms.items()}
                                          for v, arms in variants.items()}
                                      for s, variants in attribution.items()},
              'intervention_impact': {s: {v: {a: dict(c) for a, c in arms.items()}
                                          for v, arms in variants.items()}
                                      for s, variants in impact.items()},
              'gold_edges_crossing_semicolons': {f'{s}/{v}': n
                                                for (s, v), n in cross_edges.items()},
              'prediction_sha256': lock['predictions_sha256'],
              'frozen_head_sha256': commit['frozen_head_sha256'],
              'model_training': False, 'production_promotion': False,
              'limitations': ('Paired perturbations of the same synthetic families, not '
                              'independent natural-language games. G1 predictions can vary '
                              'with formatting. Action-anchored punctuation scopes may '
                              'merge unrelated records without recognized actions.')}
    scored = dest / 'scored'
    scored.mkdir(exist_ok=False)
    write(scored / 'report.json', report)
    mit.write_jsonl(scored / 'cases.jsonl', per_case)
    for split in ('dev', 'challenge'):
        for variant in VARIANTS:
            metrics = report['summary'][split][variant]
            print('MIT_SCOPE_VARIANT ' + json.dumps({
                'split': split, 'variant': variant,
                'baseline_f1': metrics['mit_wsp_baseline']['edge_f1'],
                'semicolon_f1': metrics['identity_clause']['edge_f1'],
                'scope_f1': metrics['identity_scope']['edge_f1'],
                'semicolon_exact': metrics['identity_clause']['exact'],
                'scope_exact': metrics['identity_scope']['exact']},
                sort_keys=True), flush=True)
    print('MIT_SCOPE_SCORED ' + json.dumps({
        'status': report['status'],
        'robustness': report['robustness'],
        'intra_semicolon_crossing_gold': {
            s: cross_edges.get((s, 'intra_semicolon'), 0)
            for s in ('dev', 'challenge')}}, sort_keys=True), flush=True)
    return report


def run(*, root=ROOT_DEFAULT, output=None, device='cuda'):
    dest = path_for(root, output)
    if not (dest / 'seal' / 'commit.json').is_file():
        require(not dest.exists(), 'PARTIAL_SEAL')
        prepare(root=root, output=output)
    if not (dest / 'blind' / 'lock.json').is_file():
        predict(root=root, output=output, device=device)
    if not (dest / 'scored' / 'report.json').is_file():
        return score(root=root, output=output)
    report = read(dest / 'scored' / 'report.json')
    print('MIT_SCOPE_ALREADY_SCORED ' + json.dumps({
        'status': report['status'], 'robustness': report['robustness']},
        sort_keys=True), flush=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('run', 'prepare', 'predict', 'score'):
        p = sub.add_parser(command)
        p.add_argument('--root', default=ROOT_DEFAULT)
        p.add_argument('--output')
        if command in ('run', 'predict'):
            p.add_argument('--device', default='cuda')
    args = parser.parse_args(argv)
    kwargs = {'root': args.root, 'output': args.output}
    if args.command in ('run', 'predict'):
        kwargs['device'] = args.device
    return globals()[args.command](**kwargs)


if __name__ == '__main__':
    main()
