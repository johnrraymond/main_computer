"""G2-0099: causal readout localization, strictly without weight editing.

The G2-0098 sparse applies_to edit improved a training ranking contrast but
lowered blind edge F1 and exact graphs. Here we keep the verified 0096 head
fixed, distinguish G1/candidate/threshold/decoder failure, and intervene in
individual readout paths at inference in an offline training-only replay.

A single-coordinate causal path test subtracts w[label, k]*feature[...,k]
from that label's logit, without changing model parameters. A full-feature
test subtracts (w[label,k]-w[none,k])*feature[...,k] from every margin.
These are deterministic *readout* counterfactuals, not claims of upstream
circuit discovery or generalization. Protected training examples are not an
independent challenge. This command never trains, saves edited weights,
reads challenge gold, or promotes a model. It makes diagnosis evidence only.

From main_computer/nanojev:
  python -u -m tools.nanojev_gamer_v1_g2_mit_mechanism_0099 run --device cuda
  python -m tools.nanojev_gamer_v1_g2_mit_mechanism_0099 find
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import sys
import torch
from safetensors.torch import load_file

from tools import nanojev_gamer_v1_g2_mit_weight_intervention_0097 as source_reader
from tools import nanojev_gamer_v1_g2_mit_directional_0098 as earlier
from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools import nanojev_gamer_v1_g2_structural_learning_0089 as structural
from tools import nanojev_gamer_v1_g2_posttrain_graph_bakeoff_0088 as bakeoff
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES

SCHEMA = 'nanojev.gamer.graph.g2.mit-mechanism-audit-0099.v1'
ROOT_DEFAULT = source_reader.ROOT_DEFAULT
NAME = 'oracle-fabric-g2-mit-mechanism-0099'
SEED = 990099
CASES = 120
FOCUS = 'applies_to'
TOP = 12
CONTROLS = 4
PREVIOUS_TRAIN = 80
GROUPS = ('discovery', 'replication', 'protection')
PRIOR_0098_COLS = (6, 62, 81, 161, 28, 14, 37, 55, 13, 119, 143, 169)
PRIOR_0098_DELTAS = (0.12, 0.11664323240555347, 0.11524253083888472,
                     0.11133954211435616, 0.1039328484722136,
                     0.09910691549507285, 0.09596212268982157,
                     0.08923587951481844, 0.08464238625371706,
                     -0.08310108436684567, 0.0829739933944207,
                     0.08186775179126737)
PRIOR_0098_PARENT_SHA = 'eb99869d7c851e13eb79859f545d35426072b2a55cdb004435a0539e5a8919a7'

def require(ok, reason):
    if not ok:
        raise ValueError('G2_0099_' + reason)
sha, digest, read, write = source_reader.sha, source_reader.digest, source_reader.load, source_reader.write


def location(root, output):
    return (Path(output) if output else Path(root) / NAME).resolve()


def _source(commit):
    candidate = source_reader._candidate_info(commit['source']['checkpoint'])
    require(candidate == commit['source'], 'SOURCE_CHECKPOINT_DRIFT')
    return candidate


def training_rows(paths, n):
    """Three independent TRAIN partitions; exclude all 0098 diagnostic rows."""
    require(n >= 60 and n <= 240 and n % 6 == 0, 'INVALID_CASE_COUNT')
    previous = earlier._train_selection(paths, PREVIOUS_TRAIN)
    prohibited = {digest(row['prompt']) for row in previous}
    families = {}
    for fam in ('allocation', 'encounter'):
        members = [r for r in cur.load_rows(paths['dataset'])
                   if r['family'] == fam and r['split'] == 'train'
                   and digest(r['prompt']) not in prohibited]
        members.sort(key=lambda r: hashlib.sha256(
            (str(SEED) + r['prompt']).encode('utf-8')).hexdigest())
        require(len(members) >= n // 2, 'INSUFFICIENT_NEW_TRAIN_' + fam)
        families[fam] = members[:n // 2]
    chosen = [families[('allocation', 'encounter')[k % 2]][k // 2]
              for k in range(n)]
    hashes = [digest(row['prompt']) for row in chosen]
    require(len(set(hashes)) == n and not set(hashes) & prohibited,
            'OVERLAPPING_DIAGNOSIS_COHORT')
    return chosen


def _selection_sha(rows):
    return hashlib.sha256(json.dumps(
        [(row['family'], digest(row['prompt'])) for row in rows],
        separators=(',', ':')).encode('utf-8')).hexdigest()


def group_of(i):
    # Alternates families each pair; 3 groups contain equal family counts.
    return GROUPS[(i // 2) % len(GROUPS)]


def prepare(*, root=ROOT_DEFAULT, output=None, checkpoint=None,
            arm=source_reader.PREFERRED_ARM, cases=CASES):
    dest = location(root, output)
    require(not dest.exists() and dest.parent.is_dir(), 'OUTPUT_EXISTS_OR_PARENT_MISSING')
    source = source_reader.select_checkpoint(root, checkpoint, arm)
    paths = structural.paths_for(root)
    _manifest, spec = structural.preflight(paths)
    require(spec.get('method') in ('joint', 'threshold', 'source_topk', 'type_aware'),
            'SCORE_DECODER_REQUIRED')
    selected = training_rows(paths, cases)
    dest.mkdir()
    (dest / 'capture').mkdir()
    (dest / 'capture' / 'shards').mkdir()
    commit = {'schema': SCHEMA, 'state': 'TRAIN_ONLY_DIAGNOSTIC_COMMITTED',
              'runner_sha256': sha(__file__), 'source': source,
              'cases': cases, 'dataset_sha256': sha(paths['dataset']),
              'selection_sha256': _selection_sha(selected), 'decoder_spec': spec,
              'focus_label': FOCUS, 'previous_training_excluded': PREVIOUS_TRAIN,
              'gradient_steps': 0, 'weight_edits': 0,
              'g3_unlocked': False, 'production_promotion': False}
    write(dest / 'commit.json', commit)
    print('G2_0099_PREPARED ' + json.dumps({
        'source': source['checkpoint'], 'head_sha256': source['head_sha256'],
        'cases': cases, 'exclude_0098_train': PREVIOUS_TRAIN,
        'groups': GROUPS, 'status': commit['state']}, sort_keys=True), flush=True)
    return commit


def verify(root, dest):
    commit = read(dest / 'commit.json')
    require(commit.get('schema') == SCHEMA and
            commit.get('state') == 'TRAIN_ONLY_DIAGNOSTIC_COMMITTED' and
            commit.get('runner_sha256') == sha(__file__) and
            commit.get('focus_label') == FOCUS and
            commit.get('gradient_steps') == 0 and
            commit.get('weight_edits') == 0 and
            commit.get('g3_unlocked') is False and
            commit.get('production_promotion') is False,
            'DIAGNOSTIC_COMMIT_DRIFT')
    _source(commit)
    paths = structural.paths_for(root)
    require(sha(paths['dataset']) == commit['dataset_sha256'],
            'TRAIN_DATASET_DRIFT')
    selected = training_rows(paths, commit['cases'])
    require(_selection_sha(selected) == commit['selection_sha256'],
            'TRAIN_SELECTION_DRIFT')
    return commit, selected, paths


def _torch_load(path):
    # These shards contain tensors and plain Python primitives only.
    return torch.load(str(path), map_location='cpu', weights_only=True)


def _capture_one(row, network, tok, composite, head, spec, device):
    prepared, hidden = bakeoff.previous._capture_inference(
        network, tok, row['prompt'], device, 2048)
    g1 = composite.predict(hidden, prepared,
                           family=row['family'], native_stage='G1')
    surf, feat = earlier._hook_features(head, lambda: bakeoff._infer(
        head, hidden, prepared['eligible'].to(device), g1['token_spans'],
        prepared, spec, with_margins=True))
    objs = surf['g1_objects']
    n = len(objs)
    require(feat.ndim == 3 and feat.shape[0] >= n and feat.shape[1] >= n and
            feat.shape[-1] == head.binding.weight.shape[-1],
            'FEATURE_GEOMETRY')
    chosen = bakeoff.decode(surf, spec)
    gold = bakeoff.truth(row['graph'])
    return {'family': row['family'], 'prompt_sha256': digest(row['prompt']),
            'surface': surf, 'features': feat[:n, :n].contiguous().float(),
            'baseline': [source_reader.json_edge(v) for v in sorted(chosen)],
            'gold': [source_reader.json_edge(v) for v in sorted(gold)]}


def capture(*, root=ROOT_DEFAULT, output=None, device='cuda'):
    dest = location(root, output)
    commit, selected, paths = verify(root, dest)
    lock = dest / 'capture' / 'lock.json'
    require(not lock.exists(), 'CAPTURE_ALREADY_LOCKED')
    shards = dest / 'capture' / 'shards'
    pending = [(i, row) for i, row in enumerate(selected)
               if not (shards / f'{i:04d}.pt').is_file()]
    if pending:
        network, tok, composite, ref, _ = structural._load_runtime(paths, device)
        head = source_reader._load_scientific_head(
            commit['source']['checkpoint'], ref, device=device)
        head.eval().requires_grad_(False)
        with torch.inference_mode():
            for step, (i, row) in enumerate(pending, 1):
                result = _capture_one(row, network, tok, composite,
                                      head, commit['decoder_spec'], device)
                target = shards / f'{i:04d}.pt'
                temp = shards / f'{i:04d}.tmp'
                torch.save(result, temp)
                os.replace(temp, target)
                if step == 1 or step % 20 == 0 or step == len(pending):
                    print(f'G2_0099_CAPTURE {len(selected)-len(pending)+step}/{len(selected)}',
                          flush=True)
    file_hashes = {}
    for i, row in enumerate(selected):
        file = shards / f'{i:04d}.pt'
        r = _torch_load(file)
        require(r['prompt_sha256'] == digest(row['prompt']) and
                r['family'] == row['family'], 'SHARD_PROMPT_DRIFT')
        bakeoff.validate_surface(r['surface'])
        n = len(r['surface']['g1_objects'])
        require(tuple(r['features'].shape) == (n, n, 192),
                'SHARD_FEATURE_GEOMETRY')
        file_hashes[file.name] = sha(file)
    record = {'schema': SCHEMA, 'status': 'FROZEN_0096_TRAIN_CAPTURE_LOCKED',
              'source_sha256': commit['source']['head_sha256'],
              'files': file_hashes, 'model_training': False,
              'weight_edits': 0, 'g3_unlocked': False,
              'production_promotion': False}
    write(lock, record)
    print('G2_0099_CAPTURE_LOCKED ' + json.dumps({
        'cases': len(selected), 'source_sha256': record['source_sha256'],
        'status': record['status']}, sort_keys=True), flush=True)


def _load_shards(root, dest):
    commit, selected, _ = verify(root, dest)
    lock = read(dest / 'capture' / 'lock.json')
    require(lock['schema'] == SCHEMA and
            lock['status'] == 'FROZEN_0096_TRAIN_CAPTURE_LOCKED' and
            lock['source_sha256'] == commit['source']['head_sha256'] and
            len(lock['files']) == len(selected) and
            lock['weight_edits'] == 0, 'CAPTURE_LOCK_DRIFT')
    result = []
    for i, row in enumerate(selected):
        filename = f'{i:04d}.pt'
        file = dest / 'capture' / 'shards' / filename
        require(filename in lock['files'] and sha(file) == lock['files'][filename],
                'CAPTURE_SHARD_HASH_DRIFT')
        r = _torch_load(file)
        require(r['prompt_sha256'] == digest(row['prompt']) and
                r['family'] == row['family'], 'SHARD_IDENTITY_DRIFT')
        result.append(r)
    return commit, selected, result


def _tp_counts(predicted, gold):
    return Counter(cases=1, exact=int(predicted == gold),
                   tp=len(predicted & gold), fp=len(predicted - gold),
                   fn=len(gold - predicted))


def _metrics(count):
    tp, fp, fn = count['tp'], count['fp'], count['fn']
    return {'cases': count['cases'], 'exact': count['exact'],
            'tp': tp, 'fp': fp, 'fn': fn,
            'edge_f1': 2*tp / max(1, 2*tp+fp+fn)}


def missing_attribution(surface, chosen, gold, spec):
    """Diagnostic on TRAIN gold; distinguish causes before changing any scores.

    Greedy global capacity matching may reject even a top-label pair. It must
    not be conflated with neural margin/label classification errors.
    """
    objs, pairs = bakeoff.validate_surface(surface)
    lookup = {(obj[2], (obj[0], obj[1])): i for i, obj in enumerate(objs)}
    stats = Counter()
    for edge in gold - chosen:
        source = (edge[0], tuple(edge[1]))
        target = (edge[2], tuple(edge[3]))
        if source not in lookup or target not in lookup:
            stats['g1_endpoint_unavailable'] += 1
            continue
        i, j = lookup[source], lookup[target]
        margins = pairs.get((i, j))
        if margins is None:
            stats['candidate_absent'] += 1
            continue
        class_idx = BIND_CLASSES.index(edge[4]) - 1
        if class_idx < 0:
            stats['unsupported_label'] += 1
            continue
        if max(range(len(margins)), key=lambda k: (margins[k], -k)) != class_idx:
            stats['wrong_label_top_scored'] += 1
            continue
        key = '|'.join((source[0], target[0], edge[4]))
        threshold = float(spec.get('kind_thresholds', {}).get(
            key, spec.get('threshold', 0.0)))
        if margins[class_idx] <= threshold:
            stats['below_admission_threshold'] += 1
        else:
            stats['decoder_capacity_or_global_competition'] += 1
    return stats


def endpoint_contrasts(r, label=FOCUS):
    selected = source_reader.as_set(r['baseline'])
    gold = source_reader.as_set(r['gold'])
    obs = r['surface']['g1_objects']
    n = len(obs)
    feat = r['features']
    result = []
    for kind, correct, wrong, mode in earlier.competing_pairs(selected, gold, obs):
        if kind != label:
            continue
        (i, j), (a, b) = correct, wrong
        require(all(0 <= z < n for z in (i, j, a, b)),
                'COMPETITION_COORDINATE_DRIFT')
        result.append((feat[a, b] - feat[i, j]).double())
    return result


def select_dimensions(records, binding_weight):
    """Select features ONLY from discovery group's wrong-endpoint contrasts."""
    idx = BIND_CLASSES.index(FOCUS)
    vectors = []
    for i, record in enumerate(records):
        if group_of(i) == 'discovery':
            vectors.extend(endpoint_contrasts(record))
    if not vectors:
        return {'signal': 'NO_APPLIES_TO_WRONG_ENDPOINT_COMPETITIONS',
                'n_competitions': 0, 'suspected_features': [],
                'negative_controls': []}
    matrix = torch.stack(vectors).double()
    require(matrix.ndim == 2 and matrix.shape[1] == binding_weight.shape[1] and
            torch.isfinite(matrix).all().item(), 'DISCOVERY_FEATURE_SHAPE')
    # Contribution to the wrong-minus-correct label logit. Positive means
    # the frozen learned readout *causally favors the wrong endpoint* on these
    # contrast pairs (the other terms are identical in the readout equation).
    coefficients = matrix.mean(0) * binding_weight[idx].double()
    ranking = sorted(range(len(coefficients)),
                     key=lambda k: (-float(coefficients[k]), k))
    suspected = [k for k in ranking if coefficients[k] > 0][:TOP]
    # Negative controls have signed contributions that *favor the correct*
    # endpoint, while retaining measurable magnitude.
    controls = [k for k in reversed(ranking)
                if coefficients[k] < 0 and k not in suspected][:CONTROLS]
    return {'signal': ('CANDIDATE_READOUT_HARM_FEATURES_FOUND' if suspected else
                       'NO_POSITIVE_READOUT_CONTRIBUTION'),
            'n_competitions': len(vectors),
            'suspected_features': suspected,
            'negative_controls': controls,
            'signed_mean_wrong_minus_correct_contributions': {
                str(k): float(coefficients[k]) for k in suspected + controls},
            'prior_0098_overlap': sorted(set(suspected) & set(PRIOR_0098_COLS))}


def ablate_readout_path(surface, features, binding_weight, *, dimension, mode):
    """Exact algebraic counterfactual for the final linear binding readout.

    mode='path': remove feature k's influence on applies_to *only*, matching
    the mechanism targeted by 0098. mode='full': set feature k to zero across
    ALL binding classes, including none, and recompute each margin delta.
    Neither changes the in-memory module or any checkpoint tensor.
    """
    objs, _ = bakeoff.validate_surface(surface)
    n = len(objs)
    require(tuple(features.shape[:2]) == (n, n) and
            dimension in range(binding_weight.shape[1]) and
            mode in ('path', 'full'), 'ABLATION_SHAPE')
    weight = binding_weight.double()
    k = BIND_CLASSES.index(FOCUS)-1
    modified = []
    for i, j, margins in surface['pair_margins']:
        x = float(features[i, j, dimension])
        new = list(margins)
        if mode == 'path':
            new[k] = float(new[k] - float(weight[k+1, dimension])*x)
        else:
            for a in range(len(new)):
                new[a] = float(new[a] - float(
                    weight[a+1, dimension]-weight[0, dimension])*x)
        modified.append([i, j, new])
    return {**surface, 'pair_margins': modified}



def replay_0098_score_edit(surface, features):
    """Reproduce the observed 0098 weight edit in pair margins, without editing.

    This is the exact linear-layer readout delta for applies_to, before the
    7-decimal source-surface rounding. Valid only for 0098's parent SHA.
    """
    objects, _ = bakeoff.validate_surface(surface)
    require(features.shape[0] == len(objects) and
            features.shape[1] == len(objects) and
            len(PRIOR_0098_COLS) == len(PRIOR_0098_DELTAS),
            '0098_REPLAY_GEOMETRY')
    k = BIND_CLASSES.index(FOCUS) - 1
    altered = []
    for i, j, margins in surface['pair_margins']:
        new = list(margins)
        gain = sum(float(features[i, j, col]) * delta for col, delta in
                   zip(PRIOR_0098_COLS, PRIOR_0098_DELTAS))
        new[k] += gain
        altered.append([i, j, new])
    return {**surface, 'pair_margins': altered}


def _observe(records, spec, binding_weight, dimension, mode):
    result = {group: {fam: Counter() for fam in ('allocation', 'encounter', 'overall')}
              for group in GROUPS}
    collateral = {group: Counter() for group in GROUPS}
    for i, r in enumerate(records):
        bucket = group_of(i)
        gold = source_reader.as_set(r['gold'])
        old = source_reader.as_set(r['baseline'])
        transformed = (replay_0098_score_edit(r['surface'], r['features'])
                       if mode == 'prior_0098' else ablate_readout_path(
                           r['surface'], r['features'], binding_weight,
                           dimension=dimension, mode=mode))
        new = bakeoff.decode(transformed, spec)
        for key in (r['family'], 'overall'):
            result[bucket][key].update(_tp_counts(new, gold))
        collateral[bucket].update({
            'correct_edges_lost': len((old & gold) - (new & gold)),
            'correct_edges_rescued': len((new & gold) - (old & gold)),
            'incorrect_edges_introduced': len((new - gold) - (old - gold)),
            'incorrect_edges_removed': len((old - gold) - (new - gold)),
            'exact_graphs_lost': int(old == gold and new != gold),
            'exact_graphs_gained': int(new == gold and old != gold)})
    return {'metrics': {g: {f: _metrics(cnt) for f, cnt in fams.items()}
                        for g, fams in result.items()},
            'collateral': {g: dict(collateral[g]) for g in GROUPS}}


def analyze(*, root=ROOT_DEFAULT, output=None):
    dest = location(root, output)
    require(not (dest / 'report.json').exists(), 'REPORT_ALREADY_EXISTS')
    commit, selection, data = _load_shards(root, dest)
    source = Path(commit['source']['checkpoint'])
    original_hash = commit['source']['head_sha256']
    tensor = load_file(str(source / cur.HEAD_FILE), device='cpu')
    weight = tensor['binding.weight'].clone().double()
    require(weight.ndim == 2 and
            weight.shape[0] == len(BIND_CLASSES) and
            weight.shape[1] == 192, 'BINDING_WEIGHT_GEOMETRY')
    raw = {g: {fam: Counter() for fam in ('allocation', 'encounter', 'overall')}
           for g in GROUPS}
    errors = {g: Counter() for g in GROUPS}
    by_label = {g: Counter() for g in GROUPS}
    endpoint_counts = {g: Counter() for g in GROUPS}
    for i, record in enumerate(data):
        g = group_of(i)
        selected = source_reader.as_set(record['baseline'])
        gold = source_reader.as_set(record['gold'])
        for fam in (record['family'], 'overall'):
            raw[g][fam].update(_tp_counts(selected, gold))
        errors[g].update(missing_attribution(
            record['surface'], selected, gold, commit['decoder_spec']))
        for edge in gold - selected:
            by_label[g][edge[4]] += 1
        endpoint_counts[g][FOCUS] += len(endpoint_contrasts(record))
    candidate = select_dimensions(data, weight)
    candidates = candidate['suspected_features'] + candidate['negative_controls']
    response = {}
    for index, dim in enumerate(candidates, 1):
        response[str(dim)] = {
            mode: _observe(data, commit['decoder_spec'], weight, dim, mode)
            for mode in ('path', 'full')}
        print(f'G2_0099_ABLATION {index}/{len(candidates)} feature={dim}',
              flush=True)
    baseline = {g: {f: _metrics(v) for f, v in fams.items()}
                for g, fams in raw.items()}
    # Only replay the specific 0098 intervention on its identical 0096 parent.
    replay = (_observe(data, commit['decoder_spec'], weight, None, 'prior_0098')
              if original_hash == PRIOR_0098_PARENT_SHA else None)
    localized = []
    for dim in candidate['suspected_features']:
        track = response[str(dim)]['path']
        rep = track['metrics']['replication']['overall']
        hold = track['metrics']['protection']['overall']
        rep_old = baseline['replication']['overall']
        hold_old = baseline['protection']['overall']
        change = {
            'feature': dim,
            'replication_f1_delta': rep['edge_f1']-rep_old['edge_f1'],
            'protection_f1_delta': hold['edge_f1']-hold_old['edge_f1'],
            'replication_exact_delta': rep['exact']-rep_old['exact'],
            'protection_exact_delta': hold['exact']-hold_old['exact'],
            'replication_correct_edges_lost': track['collateral']['replication'].get(
                'correct_edges_lost', 0),
            'protection_correct_edges_lost': track['collateral']['protection'].get(
                'correct_edges_lost', 0)}
        change['protected_localization'] = (
            change['replication_f1_delta'] > 0 and
            change['protection_f1_delta'] >= 0 and
            change['replication_exact_delta'] >= 0 and
            change['protection_exact_delta'] >= 0 and
            change['replication_correct_edges_lost'] == 0 and
            change['protection_correct_edges_lost'] == 0)
        localized.append(change)
    has_local = any(v['protected_localization'] for v in localized)
    # 0098 failure strongly predicts these negatives are nonzero; report them
    # as diagnostics rather than reclassifying them as reasons for promotion.
    report = {
        'schema': SCHEMA,
        'status': ('TRAIN_READOUT_PATH_LOCALIZATION_CANDIDATE' if has_local else
                   'NO_PROTECTED_TRAIN_READOUT_PATH_LOCALIZATION'),
        'checkpoint': commit['source'],
        'baseline': baseline,
        'missing_gold_by_cause': {g: dict(errors[g]) for g in GROUPS},
        'missing_gold_by_label': {g: dict(by_label[g]) for g in GROUPS},
        'endpoint_competitions': {g: dict(endpoint_counts[g]) for g in GROUPS},
        'discovery_feature_selection': candidate,
        'counterfactuals': response,
        'prior_0098_score_level_replay': replay,
        'localized_candidates': localized,
        'source_unchanged': source_reader._candidate_info(source) == commit['source']
                            and sha(source/cur.HEAD_FILE) == original_hash,
        'training_cases': len(selection),
        'weight_edits': 0, 'gradient_steps': 0, 'g3_unlocked': False,
        'production_promotion': False,
        'no_new_checkpoint': True,
        'limits': ('All cohorts are training examples, including the protected '
                   'cohort. No independent generalization claim or safe edit '
                   'authorization. Readout algebra isolates a final score '
                   'path only; it does not identify upstream neural circuits. '
                   'Full-feature ablation is a simulated in-memory '
                   'counterfactual and does not modify checkpoint weights. '
                   'The 0098 replay, when available, matches only its final '
                   'linear score change and is evaluated on TRAIN examples.')}

    require(report['source_unchanged'], 'SOURCE_MUTATED_DURING_AUDIT')
    write(dest / 'report.json', report)
    print('G2_0099_RESULT ' + json.dumps({
        'status': report['status'], 'weight_edits': 0,
        'source_unchanged': True,
        'missing_gold_by_cause': report['missing_gold_by_cause'],
        'discovery_features': candidate['suspected_features'],
        'negative_controls': candidate['negative_controls'],
        'prior_0098_replay_performed': replay is not None,
        'localized_candidates': localized}, sort_keys=True), flush=True)
    return report


def run(*, root=ROOT_DEFAULT, output=None, device='cuda', checkpoint=None,
        arm=source_reader.PREFERRED_ARM, cases=CASES):
    dest = location(root, output)
    if not (dest / 'commit.json').exists():
        prepare(root=root, output=output, checkpoint=checkpoint, arm=arm,
                cases=cases)
    if not (dest / 'capture' / 'lock.json').exists():
        capture(root=root, output=output, device=device)
    if not (dest / 'report.json').exists():
        return analyze(root=root, output=output)
    commit, _, _ = _load_shards(root, dest)
    report = read(dest / 'report.json')
    require(report['source_unchanged'] is True and
            report['checkpoint'] == commit['source'] and
            report['weight_edits'] == 0 and
            report['production_promotion'] is False, 'REPORT_DRIFT')
    print('G2_0099_RESULT ' + json.dumps({
        'status': report['status'], 'source_unchanged': True,
        'weight_edits': 0, 'localized_candidates': report['localized_candidates']},
        sort_keys=True), flush=True)
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    s = p.add_subparsers(dest='cmd', required=True)
    for cmd in ('find', 'run', 'prepare', 'capture', 'analyze'):
        q = s.add_parser(cmd)
        q.add_argument('--root', default=ROOT_DEFAULT)
        if cmd != 'find': q.add_argument('--output')
        if cmd in ('find', 'prepare', 'run'):
            q.add_argument('--arm', default=source_reader.PREFERRED_ARM)
        if cmd in ('prepare', 'run'): q.add_argument('--checkpoint')
        if cmd in ('prepare', 'run'): q.add_argument('--cases', type=int, default=CASES)
        if cmd in ('capture', 'run'): q.add_argument('--device', default='cuda')
    a = p.parse_args(argv)
    if a.cmd == 'find':
        print('G2_0099_SOURCE_CANDIDATES ' + json.dumps(
            source_reader.discover(a.root, a.arm), sort_keys=True), flush=True)
        return
    kwargs = {'root': a.root, 'output': a.output}
    if a.cmd in ('prepare', 'run'):
        kwargs.update(arm=a.arm, checkpoint=a.checkpoint, cases=a.cases)
    if a.cmd in ('capture', 'run'): kwargs['device'] = a.device
    globals()[a.cmd](**kwargs)


if __name__ == '__main__':
    main()
