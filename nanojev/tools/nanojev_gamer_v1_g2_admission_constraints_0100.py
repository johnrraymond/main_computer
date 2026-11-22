"""G2-0100: read-only threshold/capacity causal analysis on frozen G2-0096.

Uses the *existing*, hash-locked G2-0099 120-case TRAIN capture for
exploratory selection. The 2x2 factorial arms are frozen before new gold-blind
inference: baseline, admission-only, capacity-only, and combined. Gold-aware
oracle ceilings are calculated ONLY at scoring and are never used to rank,
predict, or change a model. This creates no neural checkpoint, no gradient
steps, no production cutover, and no G3 unlock.

Run from main_computer/nanojev:
  python -u -m tools.nanojev_gamer_v1_g2_admission_constraints_0100 run --device cuda

The results diagnose decoder causality, not MIT weight-space success.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import random

import torch

from tools import nanojev_gamer_v1_g2_mit_mechanism_0099 as previous
from tools import nanojev_gamer_v1_g2_mit_weight_intervention_0097 as source
from tools import nanojev_gamer_v1_g2_structural_learning_0089 as structural
from tools import nanojev_gamer_v1_g2_posttrain_graph_bakeoff_0088 as bakeoff
from tools import nanojev_gamer_v1_graph_corpus as corpus
from tools import nanojev_gamer_v1_graph_contract as contract
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES

SCHEMA = 'nanojev.gamer.graph.g2.admission-constraint-causality-0100.v1'
ROOT_DEFAULT = source.ROOT_DEFAULT
NAME = 'oracle-fabric-g2-admission-constraint-causality-0100'
TRAIN_CAMPAIGN = previous.NAME
SEED = 1000100
CASES = 240
ADMISSION_OFFSETS = (-2.0, -1.0, -0.5, -0.25, 0.25, 0.5, 1.0, 2.0)
CAP_INCREMENTS = (1, 2, 4, 'unlimited')
ARMS = ('baseline', 'admission_only', 'capacity_only', 'combined')
FAMILIES = ('allocation', 'encounter', 'overall')
GROUPS = previous.GROUPS
sha, digest, read, write, rows, write_rows = (source.sha, source.digest,
    source.load, source.write, source.rows, source.write_rows)


def require(ok, reason):
    if not ok:
        raise ValueError('G2_0100_' + reason)


def location(root, output=None):
    return (Path(output) if output else Path(root) / NAME).resolve()


def _json_bytes(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False,
                       separators=(',', ':')) + '\n').encode('utf-8')


def _gzip_write(path, obj):
    path = Path(path)
    temp = path.with_name(path.name + '.tmp')
    with temp.open('wb') as handle:
        # Deterministic gzip header for reproducible shard hashes.
        with gzip.GzipFile(filename='', mode='wb', fileobj=handle,
                           mtime=0, compresslevel=6) as compressed:
            compressed.write(_json_bytes(obj))
    os.replace(temp, path)


def _gzip_read(path):
    with gzip.open(path, 'rt', encoding='utf-8') as handle:
        return json.load(handle)


def _selected_source(root):
    dest = Path(root) / TRAIN_CAMPAIGN
    require((dest / 'capture' / 'lock.json').is_file(),
            'REQUIRES_COMPLETED_0099_TRAIN_CAPTURE')
    commit, chosen, records = previous._load_shards(root, dest)
    require(len(records) == len(chosen) == 120 and
            commit['cases'] == 120 and
            commit['gradient_steps'] == commit['weight_edits'] == 0 and
            commit['production_promotion'] is False,
            '0099_TRAIN_CAPTURE_CONTRACT')
    actual = source._candidate_info(commit['source']['checkpoint'])
    require(actual == commit['source'], '0096_CHECKPOINT_DRIFT')
    return commit, records, dest


def variant_spec(original, *, offset=0.0, capacity=0):
    """Change ONLY the two decoder mechanisms, with no model surgery.

    'unlimited' removes all joint capacity checks but keeps the same
    label argmax, source/target kinds, and admission thresholds.
    """
    require(original.get('method') == 'joint', 'EXPECTED_JOINT_DECODER')
    require(type(offset) in (int, float) and math.isfinite(offset) and
            -5 <= offset <= 5 and
            (capacity in (0, 1, 2, 4, 'unlimited')),
            'INVALID_FACTOR_LEVEL')
    new = deepcopy(original)
    new['threshold'] = float(original.get('threshold', 0.0)) + float(offset)
    new['kind_thresholds'] = {key: float(value) + float(offset)
                              for key, value in original.get('kind_thresholds', {}).items()}
    if capacity == 'unlimited':
        new['method'] = 'type_aware' if new['kind_thresholds'] else 'threshold'
        new.pop('out_caps', None)
        new.pop('in_caps', None)
    elif capacity:
        for side in ('out_caps', 'in_caps'):
            new[side] = {key: int(value) + int(capacity)
                         for key, value in original.get(side, {}).items()}
        # Decoder defaults to 2 for kinds absent from explicit cap maps.
        # Joint caps that were originally implicit must be relaxed too:
        # populate all encountered kinds at call time using keys from the
        # frozen surface's objects (implemented in decode_variant).
    return new


def decode_variant(surface, original, *, offset=0.0, capacity=0):
    spec = variant_spec(original, offset=offset, capacity=capacity)
    if capacity not in (0, 'unlimited'):
        objects, _ = bakeoff.validate_surface(surface)
        # Include all possible type keys; absent caps otherwise use default 2.
        all_keys = {'|'.join((a[2], b[2], kind))
                    for a in objects for b in objects if a != b
                    for kind in BIND_CLASSES[1:]}
        for key in all_keys:
            # in_caps are keyed by *destination,source,label*.
            src, dst, kind = key.split('|')
            inward = '|'.join((dst, src, kind))
            spec['out_caps'][key] = int(original.get('out_caps', {}).get(key, 2)) + capacity
            spec['in_caps'][inward] = int(original.get('in_caps', {}).get(inward, 2)) + capacity
    return bakeoff.decode(surface, spec)


def _count(predicted, gold):
    return Counter(cases=1, exact=int(predicted == gold),
                   tp=len(predicted & gold), fp=len(predicted - gold),
                   fn=len(gold - predicted))


def _score(counter):
    tp, fp, fn = (counter[k] for k in ('tp', 'fp', 'fn'))
    return {'cases': counter['cases'], 'exact': counter['exact'],
            'tp': tp, 'fp': fp, 'fn': fn,
            'edge_f1': 2 * tp / max(1, 2 * tp + fp + fn),
            'exact_rate': counter['exact'] / max(1, counter['cases'])}


def _partition_scores(records, original, *, offset=0.0, capacity=0):
    result = {group: {f: Counter() for f in FAMILIES} for group in GROUPS}
    for i, record in enumerate(records):
        group = previous.group_of(i)
        gold = source.as_set(record['gold'])
        decoded = decode_variant(record['surface'], original,
                                 offset=offset, capacity=capacity)
        # Fail if 0100's baseline is not the original 0099 decoder result.
        if offset == 0 and capacity == 0:
            require(decoded == source.as_set(record['baseline']),
                    '0099_BASELINE_REPLAY_DISAGREEMENT')
        for family in ('overall', record['family']):
            result[group][family].update(_count(decoded, gold))
    return {g: {fam: _score(v) for fam, v in fams.items()}
            for g, fams in result.items()}


def _axis_selection(records, original, axis, baseline):
    require(axis in ('admission', 'capacity'), 'UNKNOWN_AXIS')
    candidates = ADMISSION_OFFSETS if axis == 'admission' else CAP_INCREMENTS
    trials = []
    for level in candidates:
        settings = {'offset': level, 'capacity': 0} if axis == 'admission' else {
            'offset': 0, 'capacity': level}
        scores = _partition_scores(records, original, **settings)
        differences = {group: {
            fam: {'f1': scores[group][fam]['edge_f1']-baseline[group][fam]['edge_f1'],
                  'exact': scores[group][fam]['exact']-baseline[group][fam]['exact']}
            for fam in FAMILIES} for group in GROUPS}
        trials.append({'level': level, 'scores': scores, 'deltas': differences})
    # Selection uses discovery ONLY. Replication and protection are reported
    # separately, not fitted or used to select a hyperparameter.
    def priority(t):
        obs = t['deltas']['discovery']['overall']
        return (obs['exact'], obs['f1'],
                -abs(t['level']) if isinstance(t['level'], (int, float)) else -100)
    ranked = sorted(trials, key=priority, reverse=True)
    chosen = ranked[0]
    # Choose nonzero exploratory factor even if none outperforms baseline,
    # to measure its mechanism; NEVER infer an acceptable treatment from that.
    verified = all(
        chosen['deltas'][g]['overall']['f1'] >= 0 and
        chosen['deltas'][g]['overall']['exact'] >= 0
        for g in ('replication', 'protection')) and (
        chosen['deltas']['discovery']['overall']['f1'] > 0 or
        chosen['deltas']['discovery']['overall']['exact'] > 0)
    return {'axis': axis, 'selected': chosen['level'],
            'replicated_and_protected': verified,
            'selection_basis': 'discovery-only; replication/protection are audits',
            'candidates': trials}


def _fresh_cohort(root, dataset, number):
    """Bounded discovery: never recursively scan runtime/ or the shard forest."""
    require(40 <= number <= 2000 and number % 2 == 0, 'INVALID_HOLDOUT_CASES')
    used = {digest(r['prompt']) for r in previous.cur.load_rows(dataset)}
    for campaign in (source.OUTPUT_NAME, previous.earlier.NAME):
        p = Path(root) / campaign / 'seal' / 'prompts.jsonl'
        if p.is_file():
            used.update(r['prompt_sha256'] for r in rows(p))
    rng = random.Random(SEED)
    public, secret = [], []
    serial = 100_010_000
    for i in range(number):
        family = 'allocation' if i % 2 == 0 else 'encounter'
        generator = corpus.allocation if family == 'allocation' else corpus.encounter
        for attempt in range(4096):
            record = generator(rng, 'test', serial)
            serial += 1
            key = digest(record['prompt'])
            if key not in used:
                break
        else:
            require(False, 'FRESH_PROMPT_EXHAUSTION')
        require(record.get('annotation_provenance') == 'AUTHOR_MARKED_IN_SOURCE_GENERATOR',
                'AUTHOR_GOLD_UNAVAILABLE')
        contract.validate_graph(record['graph'], complete=True)
        used.add(key)
        identifier = f'g2-0100-{i:04d}'
        public.append({'case_id': identifier, 'family': family,
                       'prompt': record['prompt'], 'prompt_sha256': key})
        secret.append({'case_id': identifier, 'family': family,
                       'prompt_sha256': key, 'graph': record['graph']})
    return public, secret


def _oracle(snapshot, gold, original):
    """Gold-aware IDEAL-PRECISION opportunity ceilings, *not* executable arms.

    Every ceiling suppresses false positives using answers. Current-admitted
    counts gold edges with G1 endpoints, top neural class and above-threshold
    margins. Threshold-released ignores only threshold; G1-reachable ignores
    both label and threshold/capacity. Caps are ignored in all three. These
    are nested optimistic ceilings, NOT performance claims or deployable fixes.
    """
    objects, pairs = bakeoff.validate_surface(snapshot)
    lookup = {(obj[2], (obj[0], obj[1])): i for i, obj in enumerate(objects)}
    categories = Counter()
    subsets = {name: set() for name in ('admitted_oracle',
                                        'threshold_released_oracle',
                                        'g1_reachable_oracle')}
    for edge in gold:
        src = (edge[0], tuple(edge[1]))
        dst = (edge[2], tuple(edge[3]))
        if src not in lookup or dst not in lookup:
            categories['g1_endpoint_unavailable'] += 1
            continue
        subsets['g1_reachable_oracle'].add(edge)
        coord = (lookup[src], lookup[dst])
        if coord not in pairs:
            categories['candidate_absent'] += 1
            continue
        margins = pairs[coord]
        class_id = max(range(len(margins)), key=lambda a: (margins[a], -a)) + 1
        if BIND_CLASSES[class_id] != edge[4]:
            categories['wrong_neural_top_label'] += 1
            continue
        subsets['threshold_released_oracle'].add(edge)
        key = '|'.join((edge[0], edge[2], edge[4]))
        cutoff = original.get('kind_thresholds', {}).get(key, original.get('threshold', 0.0))
        if margins[class_id-1] <= cutoff:
            categories['below_admission_threshold'] += 1
            continue
        subsets['admitted_oracle'].add(edge)
        categories['admitted_but_may_be_capacity_rejected'] += 1
    require(sum(categories.values()) == len(gold), 'ORACLE_CATEGORIES_INCOMPLETE')
    return subsets, categories


def prepare(*, root=ROOT_DEFAULT, output=None, cases=CASES):
    dest = location(root, output)
    require(not dest.exists() and dest.parent.is_dir(), 'OUTPUT_EXISTS_OR_PARENT_MISSING')
    previous_commit, train_records, prior = _selected_source(root)
    spec = previous_commit['decoder_spec']
    require(spec.get('method') == 'joint', 'EXPECTED_JOINT_DECODER')
    baseline = _partition_scores(train_records, spec)
    admission = _axis_selection(train_records, spec, 'admission', baseline)
    capacity = _axis_selection(train_records, spec, 'capacity', baseline)
    # The factor levels are frozen here; no challenge gold is inspected.
    offsets = {'admission': admission['selected'], 'capacity': capacity['selected']}
    factorial = {
        'baseline': {'offset': 0.0, 'capacity': 0},
        'admission_only': {'offset': offsets['admission'], 'capacity': 0},
        'capacity_only': {'offset': 0.0, 'capacity': offsets['capacity']},
        'combined': {'offset': offsets['admission'], 'capacity': offsets['capacity']}}
    # Training oracle ceilings are diagnostic and do NOT select the axes.
    train_oracle = {g: {f+'::'+oracle: Counter() for f in FAMILIES
                       for oracle in ('admitted_oracle', 'threshold_released_oracle',
                                      'g1_reachable_oracle')} for g in GROUPS}
    train_categories = {g: Counter() for g in GROUPS}
    for i, r in enumerate(train_records):
        g = previous.group_of(i)
        gold = source.as_set(r['gold'])
        sets, causes = _oracle(r['surface'], gold, spec)
        train_categories[g].update(causes)
        for family in ('overall', r['family']):
            for name, predicted in sets.items():
                train_oracle[g][family+'::'+name].update(_count(predicted, gold))
    paths = structural.paths_for(root)
    prompts, answers = _fresh_cohort(root, Path(paths['dataset']), cases)
    dest.mkdir()
    (dest / 'seal').mkdir()
    write_rows(dest / 'seal' / 'prompts.jsonl', prompts)
    write_rows(dest / 'seal' / 'answers.SECRET.jsonl', answers)
    source_identity = previous_commit['source']
    evidence = {'baseline': baseline, 'admission': admission,
                'capacity': capacity, 'train_oracle': {
                    g: {f: _score(v) for f, v in families.items()}
                    for g, families in train_oracle.items()},
                'gold_edge_opportunity_categories': {g: dict(c) for g, c in train_categories.items()},
                'warning': 'Train oracle used only for opportunity diagnosis, never for selection.'}
    write(dest / 'train_evidence.json', evidence)
    commitment = {'schema': SCHEMA, 'status': 'FACTORS_FROZEN_BEFORE_BLIND_INFERENCE',
                  'runner_sha256': sha(__file__), 'source': source_identity,
                  'decoder_spec': spec, 'factors': factorial,
                  'train_capture_lock_sha256': sha(prior/'capture'/'lock.json'),
                  'train_capture_commit_sha256': sha(prior/'commit.json'),
                  'training_evidence_sha256': sha(dest/'train_evidence.json'),
                  'cases': cases, 'prompt_sha256': sha(dest/'seal'/'prompts.jsonl'),
                  'answers_sha256': sha(dest/'seal'/'answers.SECRET.jsonl'),
                  'gradient_steps': 0, 'weight_edits': 0, 'g3_unlocked': False,
                  'production_promotion': False}
    write(dest / 'seal' / 'commit.json', commitment)
    print('G2_0100_PREPARED ' + json.dumps({
        'checkpoint': source_identity['checkpoint'], 'source_sha256': source_identity['head_sha256'],
        'train_cases_reused': len(train_records), 'sealed_holdout_cases': cases,
        'factors': factorial, 'admission_protected': admission['replicated_and_protected'],
        'capacity_protected': capacity['replicated_and_protected'],
        'status': commitment['status']}, sort_keys=True), flush=True)
    return commitment


def verify(root, dest, *, with_gold=False):
    commit = read(dest/'seal'/'commit.json')
    require(commit.get('schema') == SCHEMA and
            commit.get('runner_sha256') == sha(__file__) and
            commit.get('status') == 'FACTORS_FROZEN_BEFORE_BLIND_INFERENCE' and
            commit.get('gradient_steps') == 0 and commit.get('weight_edits') == 0 and
            commit.get('g3_unlocked') is False and
            commit.get('production_promotion') is False and
            commit['prompt_sha256'] == sha(dest/'seal'/'prompts.jsonl') and
            commit['training_evidence_sha256'] == sha(dest/'train_evidence.json'),
            'PRECOMMITMENT_DRIFT')
    training = read(dest/'train_evidence.json')
    admission = training['admission']['selected']
    capacity = training['capacity']['selected']
    expected_factors = {
        'baseline': {'offset': 0.0, 'capacity': 0},
        'admission_only': {'offset': admission, 'capacity': 0},
        'capacity_only': {'offset': 0.0, 'capacity': capacity},
        'combined': {'offset': admission, 'capacity': capacity}}
    require(admission in ADMISSION_OFFSETS and capacity in CAP_INCREMENTS and
            commit.get('factors') == expected_factors, 'FROZEN_FACTORS_DRIFT')
    actual, _, prior = _selected_source(root)
    require(actual['source'] == commit['source'] and
            actual['decoder_spec'] == commit['decoder_spec'] and
            sha(prior/'capture'/'lock.json') == commit['train_capture_lock_sha256'] and
            sha(prior/'commit.json') == commit['train_capture_commit_sha256'] and
            set(commit['factors']) == set(ARMS) and
            commit['factors']['baseline'] == {'offset': 0.0, 'capacity': 0},
            'TRAIN_EVIDENCE_OR_SOURCE_DRIFT')
    if with_gold:
        require(commit['answers_sha256'] == sha(dest/'seal'/'answers.SECRET.jsonl'),
                'SEALED_ANSWERS_CHANGED')
    return commit


def _prediction_rows(dest, commit):
    prompts = list(rows(dest/'seal'/'prompts.jsonl'))
    require(len(prompts) == commit['cases'] and
            len({r['case_id'] for r in prompts}) == len(prompts),
            'HOLDOUT_CASE_IDENTITY_DRIFT')
    return prompts


def _validated_shard(path, row, commit):
    obj = _gzip_read(path)
    require(obj['case_id'] == row['case_id'] and
            obj['prompt_sha256'] == row['prompt_sha256'] and
            obj['family'] == row['family'] and
            set(obj['predictions']) == set(ARMS), 'BLIND_SHARD_IDENTITY_DRIFT')
    bakeoff.validate_surface(obj['surface'])
    # Recompute predictions from surface and precommitted factor levels to
    # detect altered shards before they can influence outcome scoring.
    for arm in ARMS:
        expected = decode_variant(obj['surface'], commit['decoder_spec'],
                                  **commit['factors'][arm])
        require(expected == source.as_set(obj['predictions'][arm]),
                'BLIND_PREDICTION_REPLAY_DISAGREEMENT')
    return obj


def predict(*, root=ROOT_DEFAULT, output=None, device='cuda'):
    dest = location(root, output)
    commit = verify(root, dest)
    blind = dest/'blind'
    require(not (blind/'lock.json').exists(), 'PREDICTIONS_ALREADY_LOCKED')
    blind.mkdir(exist_ok=True)
    shards = blind/'shards'
    shards.mkdir(exist_ok=True)
    prompts = _prediction_rows(dest, commit)
    pending = [r for r in prompts if not (shards/(r['case_id']+'.json.gz')).exists()]
    if pending:
        paths = structural.paths_for(root)
        network, tok, composite, reference, _manifest = structural._load_runtime(paths, device)
        head = source._load_scientific_head(commit['source']['checkpoint'], reference, device=device)
        head.eval().requires_grad_(False)
        with torch.inference_mode():
            for step, row in enumerate(pending, 1):
                require(row['prompt_sha256'] == digest(row['prompt']), 'PROMPT_DRIFT')
                prepared, hidden = bakeoff.previous._capture_inference(
                    network, tok, row['prompt'], device, 2048)
                g1 = composite.predict(hidden, prepared,
                                       family=row['family'], native_stage='G1')
                surf = bakeoff._infer(head, hidden, prepared['eligible'].to(device),
                                     g1['token_spans'], prepared,
                                     commit['decoder_spec'], with_margins=True)
                preds = {arm: [source.json_edge(e) for e in sorted(decode_variant(
                    surf, commit['decoder_spec'], **commit['factors'][arm]))]
                         for arm in ARMS}
                result = {'case_id': row['case_id'], 'family': row['family'],
                          'prompt_sha256': row['prompt_sha256'],
                          'surface': surf, 'predictions': preds}
                _gzip_write(shards/(row['case_id']+'.json.gz'), result)
                if step == 1 or step % 25 == 0 or step == len(pending):
                    print(f'G2_0100_BLIND {len(prompts)-len(pending)+step}/{len(prompts)}',
                          flush=True)
    shas = {}
    compact = []
    for row in prompts:
        file = shards/(row['case_id']+'.json.gz')
        obj = _validated_shard(file, row, commit)
        shas[file.name] = sha(file)
        compact.append({key: obj[key] for key in
                        ('case_id','family','prompt_sha256','predictions')})
    temp = blind/'predictions.jsonl.tmp'
    with temp.open('w', encoding='utf-8') as handle:
        for row in compact:
            handle.write(_json_bytes(row).decode('utf-8'))
    os.replace(temp, blind/'predictions.jsonl')
    lock = {'schema': SCHEMA, 'status': 'BLIND_PREDICTIONS_LOCKED_UNSCORED',
            'predictions_sha256': sha(blind/'predictions.jsonl'),
            'shard_sha256': shas, 'precommit_sha256': sha(dest/'seal'/'commit.json'),
            'source_head_sha256': commit['source']['head_sha256'],
            'gold_accessed': False, 'gradient_steps': 0, 'weight_edits': 0,
            'production_promotion': False, 'g3_unlocked': False}
    write(blind/'lock.json', lock)
    print('G2_0100_BLIND_LOCKED ' + json.dumps({
        'cases': len(compact), 'predictions_sha256': lock['predictions_sha256'],
        'gold_accessed': False}, sort_keys=True), flush=True)
    return lock


def score(*, root=ROOT_DEFAULT, output=None):
    dest = location(root, output)
    commit = verify(root, dest, with_gold=True)
    require(not (dest/'scored').exists(), 'ALREADY_SCORED')
    lock = read(dest/'blind'/'lock.json')
    require(lock.get('schema') == SCHEMA and
            lock.get('status') == 'BLIND_PREDICTIONS_LOCKED_UNSCORED' and
            lock.get('source_head_sha256') == commit['source']['head_sha256'] and
            lock.get('precommit_sha256') == sha(dest/'seal'/'commit.json') and
            lock.get('predictions_sha256') == sha(dest/'blind'/'predictions.jsonl') and
            lock.get('gold_accessed') is False and
            lock.get('weight_edits') == 0 and
            lock.get('production_promotion') is False,
            'BLIND_LOCK_DRIFT')
    prompts = _prediction_rows(dest, commit)
    answers = {r['case_id']: r for r in rows(dest/'seal'/'answers.SECRET.jsonl')}
    require(len(answers) == len(prompts), 'ANSWER_COUNT_DRIFT')
    by_arm = {family: {a: Counter() for a in ARMS} for family in FAMILIES}
    by_oracle = {family: {a: Counter() for a in ('admitted_oracle',
                  'threshold_released_oracle', 'g1_reachable_oracle')}
                 for family in FAMILIES}
    category = {family: Counter() for family in FAMILIES}
    missing_causes = {family: Counter() for family in FAMILIES}
    paired = {f: {a: Counter() for a in ARMS if a != 'baseline'}
              for f in FAMILIES}
    rescues_by_cause = {f: {a: Counter() for a in ARMS if a != 'baseline'}
                        for f in FAMILIES}
    for row in prompts:
        require(row['case_id'] in answers and
                answers[row['case_id']]['prompt_sha256'] == row['prompt_sha256'] and
                answers[row['case_id']]['family'] == row['family'], 'ANSWER_BINDING_DRIFT')
        file = dest/'blind'/'shards'/(row['case_id']+'.json.gz')
        require(sha(file) == lock['shard_sha256'].get(file.name),
                'BLIND_SHARD_CHECKSUM_DRIFT')
        record = _validated_shard(file, row, commit)
        gold = bakeoff.truth(answers[row['case_id']]['graph'])
        preds = {arm: source.as_set(record['predictions'][arm]) for arm in ARMS}
        ceiling, causes = _oracle(record['surface'], gold, commit['decoder_spec'])
        missing = previous.missing_attribution(record['surface'],
                       preds['baseline'], gold, commit['decoder_spec'])
        for family in (row['family'],'overall'):
            for arm in ARMS:
                by_arm[family][arm].update(_count(preds[arm], gold))
            for oracle, feasible in ceiling.items():
                by_oracle[family][oracle].update(_count(feasible, gold))
            category[family].update(causes)
            missing_causes[family].update(missing)
        before = preds['baseline']
        for arm in ARMS:
            if arm == 'baseline':
                continue
            after = preds[arm]
            transitions = {
                'rescued_tp': len((after & gold) - (before & gold)),
                'lost_tp': len((before & gold) - (after & gold)),
                'removed_fp': len((before - gold) - (after - gold)),
                'introduced_fp': len((after - gold) - (before - gold)),
                'exact_gained': int(after == gold and before != gold),
                'exact_lost': int(before == gold and after != gold)}
            recovered = (after & gold) - (before & gold)
            causes = Counter()
            for e in recovered:
                if e in ceiling['admitted_oracle']:
                    causes['admitted_but_rejected'] += 1
                elif e in ceiling['threshold_released_oracle']:
                    causes['below_threshold'] += 1
                elif e in ceiling['g1_reachable_oracle']:
                    causes['wrong_neural_label_or_candidate'] += 1
                else:
                    causes['g1_missing'] += 1
            for family in (row['family'], 'overall'):
                paired[family][arm].update(transitions)
                rescues_by_cause[family][arm].update(causes)
    metrics = {f: {arm: _score(c) for arm,c in by_arm[f].items()} for f in FAMILIES}
    oracles = {f: {a: _score(c) for a,c in by_oracle[f].items()} for f in FAMILIES}
    effects = {f: {a: {'f1': metrics[f][a]['edge_f1']-metrics[f]['baseline']['edge_f1'],
                       'exact': metrics[f][a]['exact']-metrics[f]['baseline']['exact'],
                       'fp': metrics[f][a]['fp']-metrics[f]['baseline']['fp'],
                       'fn': metrics[f][a]['fn']-metrics[f]['baseline']['fn']}
                   for a in ARMS if a != 'baseline'} for f in FAMILIES}
    interaction = {f: metrics[f]['combined']['edge_f1'] -
                      metrics[f]['admission_only']['edge_f1'] -
                      metrics[f]['capacity_only']['edge_f1'] +
                      metrics[f]['baseline']['edge_f1'] for f in FAMILIES}
    evidence = read(dest/'train_evidence.json')
    gate = (effects['overall']['combined']['f1'] > .005 and
            effects['overall']['combined']['exact'] >= 0 and
            all(effects[f]['combined']['f1'] >= -.01
                for f in ('allocation','encounter')) and
            evidence['admission']['replicated_and_protected'] and
            evidence['capacity']['replicated_and_protected'])
    unchanged = source._candidate_info(commit['source']['checkpoint']) == commit['source']
    require(unchanged, '0096_SOURCE_MUTATED')
    report = {'schema': SCHEMA,
              'status': 'CAUSAL_DECODER_CANDIDATE_NOT_CERTIFIED' if gate else
                        'ADMISSION_CONSTRAINT_ANALYSIS_NO_PROMOTION',
              'cases': len(prompts), 'source': commit['source'],
              'factorial_levels': commit['factors'],
              'metrics': metrics, 'effects': effects,
              'factorial_f1_interaction': interaction,
              'paired_edge_impact': {f: {a: dict(v) for a,v in by.items()}
                                     for f, by in paired.items()},
              'recovered_gold_by_original_cause': {
                  f: {a: dict(v) for a,v in by.items()} for f,by in rescues_by_cause.items()},
              'gold_informed_ideal_precision_ceilings': oracles,
              'gold_edge_opportunity_categories': {f: dict(cnt) for f,cnt in category.items()},
              'gold_missing_causes': {f: dict(cnt) for f,cnt in missing_causes.items()},
              'gate': gate, 'source_unchanged': unchanged,
              'weight_edits': 0, 'gradient_steps': 0,
              'g3_unlocked': False, 'production_promotion': False,
              'limits': ('Factor levels chosen on 0099 discovery training only; '
                  '0099 replication/protection used for diagnostics. Fresh holdout '
                  'synthetic/author-annotated; no automatic decoder promotion. '
                  'Oracle ceilings use holdout gold ONLY AFTER blind lock and '
                  'assume perfect precision and no capacity constraints; not '
                  'attainable performance or proof of sufficiency. Interactions '
                  'are on F1, a nonlinear metric. Frozen G2/G1 unchanged.')}
    (dest/'scored').mkdir()
    write(dest/'scored'/'report.json', report)
    print('G2_0100_SCORED ' + json.dumps({
        'status': report['status'], 'gate': gate,
        'factors': commit['factors'], 'effects': effects,
        'factorial_f1_interaction': interaction,
        'paired': report['paired_edge_impact'],
        'recovered_gold_by_original_cause': report['recovered_gold_by_original_cause'],
        'gold_missing_causes': report['gold_missing_causes'],
        'oracle_ceilings': {f: {k:{'edge_f1':v['edge_f1'],'exact':v['exact']}
                        for k,v in by.items()} for f,by in oracles.items()},
        'source_unchanged': unchanged}, sort_keys=True), flush=True)
    return report


def run(*, root=ROOT_DEFAULT, output=None, device='cuda', cases=CASES):
    dest = location(root, output)
    if not (dest/'seal'/'commit.json').exists():
        prepare(root=root, output=output, cases=cases)
    if not (dest/'blind'/'lock.json').exists():
        predict(root=root, output=output, device=device)
    if not (dest/'scored'/'report.json').exists():
        return score(root=root, output=output)
    commit = verify(root, dest, with_gold=True)
    report = read(dest/'scored'/'report.json')
    require(report.get('schema')==SCHEMA and
            report.get('source')==commit['source'] and
            report.get('weight_edits')==0 and
            report.get('production_promotion') is False and
            report.get('source_unchanged') is True, 'FINISHED_REPORT_DRIFT')
    print('G2_0100_RESULT '+json.dumps({
        'status': report['status'], 'gate': report['gate'],
        'effects': report['effects'], 'source_unchanged': True},
        sort_keys=True), flush=True)
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    commands=p.add_subparsers(dest='command', required=True)
    for name in ('run','prepare','predict','score','find'):
        q=commands.add_parser(name)
        q.add_argument('--root', default=ROOT_DEFAULT)
        if name != 'find': q.add_argument('--output')
        if name in ('run','prepare'): q.add_argument('--cases',type=int,default=CASES)
        if name in ('run','predict'): q.add_argument('--device',default='cuda')
    a=p.parse_args(argv)
    if a.command=='find':
        print('G2_0100_REQUIREMENTS '+json.dumps({
            'g2_0099_capture': str(Path(a.root)/TRAIN_CAMPAIGN/'capture'/'lock.json'),
            'g2_0099_report': str(Path(a.root)/TRAIN_CAMPAIGN/'report.json'),
            'g2_0096_source': source.discover(a.root, source.PREFERRED_ARM)},
            sort_keys=True), flush=True)
        return
    kwargs={'root':a.root,'output':a.output}
    if a.command in ('run','prepare'):kwargs['cases']=a.cases
    if a.command in ('run','predict'):kwargs['device']=a.device
    globals()[a.command](**kwargs)

if __name__=='__main__':
    main()
