"""G2-0101: protected, relationship-specific admission calibration (no weight changes).

Use the frozen 0099 120-case TRAIN capture. Select one margin offset per
relationship label on DISCOVERY only; REPLICATION and PROTECTION may veto, but
never retune. The joint combination must also pass both audits. Then seal a
fresh, non-0100 holdout and lock predictions before opening gold. The 0100
+1.0 global threshold is retained only as a predeclared diagnostic comparator.

  python -u -m tools.nanojev_gamer_v1_g2_relationship_admission_0101 run --device cuda
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import random

import torch
from tools import nanojev_gamer_v1_g2_admission_constraints_0100 as prior
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES

SCHEMA = 'nanojev.gamer.graph.g2.relationship-admission-0101.v1'
NAME = 'oracle-fabric-g2-relationship-admission-0101'
ROOT_DEFAULT = prior.ROOT_DEFAULT
SEED = 1_000_101
CASES = 240
LABELS = tuple(BIND_CLASSES[1:])
LEVELS = (-1.0, -0.5, -0.25, 0.25, 0.5, 1.0, 1.5)
ARMS = ('baseline', 'class_calibrated', 'global_0100')
FAMILIES = prior.FAMILIES
GROUPS = prior.GROUPS
source, previous, structural = prior.source, prior.previous, prior.structural
bakeoff, corpus, contract = prior.bakeoff, prior.corpus, prior.contract
sha, digest, read, write, rows, write_rows = (prior.sha, prior.digest,
                                               prior.read, prior.write,
                                               prior.rows, prior.write_rows)


def require(ok, reason):
    if not ok:
        raise ValueError('G2_0101_' + reason)


def location(root, output=None):
    return (Path(output) if output else Path(root) / NAME).resolve()


def _offsets(value):
    require(isinstance(value, dict) and set(value) == set(LABELS),
            'OFFSETS_LABEL_SET')
    require(all(type(value[k]) in (int, float) and math.isfinite(value[k]) and
                float(value[k]) in (0.0, *LEVELS) for k in LABELS),
            'OFFSETS_INVALID_LEVEL')
    return {k: float(value[k]) for k in LABELS}


def decode_labels(surface, original, offsets):
    """A gold-blind decoder with class-specific *admission* offsets only.

    Ranking within the same class and all original graph capacities remain
    exactly unchanged; no gold-aware selection, weight edit, or family routing.
    """
    offsets = _offsets(offsets)
    require(original.get('method') == 'joint', 'EXPECTED_JOINT_DECODER')
    if not any(offsets.values()):
        return bakeoff.decode(surface, original)
    objects, pairs = bakeoff.validate_surface(surface)
    spec = deepcopy(original)
    kind = dict(original.get('kind_thresholds', {}))
    base_threshold = float(original.get('threshold', 0.0))
    for (i, j), margins in pairs.items():
        chosen = max(range(len(margins)), key=lambda k: (margins[k], -k))
        label = BIND_CLASSES[chosen + 1]
        offset = offsets[label]
        if offset:
            key = '|'.join((objects[i][2], objects[j][2], label))
            kind[key] = float(original.get('kind_thresholds', {}).get(
                key, base_threshold)) + offset
    spec['kind_thresholds'] = kind
    return bakeoff.decode(surface, spec)


def decoder(surface, spec, arm, offsets):
    if arm == 'baseline':
        return bakeoff.decode(surface, spec)
    if arm == 'class_calibrated':
        return decode_labels(surface, spec, offsets)
    require(arm == 'global_0100', 'UNKNOWN_ARM')
    return prior.decode_variant(surface, spec, offset=1.0, capacity=0)


def _metric_counts(records, spec, offsets, groups):
    """Gold-aware TRAIN evaluation only. Never invoked on sealed holdout."""
    metrics = {g: {fam: Counter() for fam in FAMILIES} for g in groups}
    for i, r in enumerate(records):
        group = previous.group_of(i)
        if group not in metrics:
            continue
        gold = source.as_set(r['gold'])
        pred = decode_labels(r['surface'], spec, offsets)
        if not any(offsets.values()):
            require(pred == source.as_set(r['baseline']),
                    '0099_BASELINE_REPLAY_DISAGREEMENT')
        for family in ('overall', r['family']):
            metrics[group][family].update(prior._count(pred, gold))
    return {g: {fam: prior._score(counter) for fam, counter in v.items()}
            for g, v in metrics.items()}


def _delta(current, baseline, group, fam='overall'):
    a, b = current[group][fam], baseline[group][fam]
    return {'f1': a['edge_f1']-b['edge_f1'],
            'exact': a['exact']-b['exact'],
            'fp': a['fp']-b['fp'], 'fn': a['fn']-b['fn']}


def _audit(scores, baseline):
    """Independent train audits can reject, not retune, a discovery choice."""
    reason = []
    for group in ('replication', 'protection'):
        d = _delta(scores, baseline, group)
        if d['f1'] < -1e-12 or d['exact'] < 0:
            reason.append(group+'_overall_regression')
        for fam in ('allocation', 'encounter'):
            df = _delta(scores, baseline, group, fam)
            if df['exact'] < 0 or df['f1'] < -0.002:
                reason.append(group+'_'+fam+'_regression')
    return {'protected': not reason, 'reasons': reason,
            'replication': {f: _delta(scores,baseline,'replication',f) for f in FAMILIES},
            'protection': {f: _delta(scores,baseline,'protection',f) for f in FAMILIES}}


def calibrate(records, spec):
    """Every class choice uses DISCOVERY only. A veto never causes a retry."""
    zero = {k: 0.0 for k in LABELS}
    baseline = _metric_counts(records, spec, zero, GROUPS)
    decisions = {}
    accepted = dict(zero)
    for label in LABELS:
        trials = []
        for level in LEVELS:
            variant = dict(zero); variant[label] = level
            scores = _metric_counts(records, spec, variant, ('discovery',))
            d = _delta(scores, baseline, 'discovery')
            trials.append({'level': level, 'discovery': d})
        # F1 is the prespecified discovery objective, exact graph losses veto.
        ranked = [t for t in trials if t['discovery']['exact'] >= 0 and
                  t['discovery']['f1'] > 0.001]
        chosen = max(ranked, key=lambda t: (t['discovery']['f1'],
                     t['discovery']['exact'], -abs(t['level']))) if ranked else None
        audit = None
        if chosen is not None:
            attempt = dict(zero); attempt[label] = chosen['level']
            audit_scores = _metric_counts(records, spec, attempt,
                                         ('replication', 'protection'))
            audit = _audit(audit_scores, baseline)
            if audit['protected']:
                accepted[label] = chosen['level']
        decisions[label] = {'trials': trials,
                            'discovery_selected': chosen['level'] if chosen else 0.0,
                            'audit': audit,
                            'accepted': accepted[label]}
    joint = _metric_counts(records, spec, accepted, GROUPS)
    combined_audit = _audit(joint, baseline)
    # Reject unsafe interactions rather than retuning on protected cohorts.
    joint_safe = combined_audit['protected'] and (
        _delta(joint, baseline, 'discovery')['f1'] > 0.001 and
        _delta(joint, baseline, 'discovery')['exact'] >= 0)
    frozen = accepted if joint_safe else zero
    final_train = _metric_counts(records, spec, frozen, GROUPS)
    return {'baseline': baseline, 'class_decisions': decisions,
            'individually_accepted': accepted,
            'joint_training_audit': combined_audit,
            'joint_safe': joint_safe,
            'frozen_offsets': frozen,
            'frozen_training_scores': final_train,
            'selection_rule': ('Discovery-only F1 selection, no discovery exact losses; '
                               'replication and protection can veto, never retune; '
                               'joint composition must pass both audits.')}


def _used_prompt_hashes(root, dataset):
    used = {digest(r['prompt']) for r in previous.cur.load_rows(dataset)}
    campaigns = (prior.NAME, source.OUTPUT_NAME, previous.earlier.NAME)
    evidence = {}
    for campaign in campaigns:
        p = Path(root) / campaign / 'seal' / 'prompts.jsonl'
        if p.is_file():
            provenance = list(rows(p))
            used.update(r.get('prompt_sha256') or digest(r['prompt']) for r in provenance)
            evidence[campaign] = sha(p)
    return used, evidence


def _fresh_cohort(root, dataset, number):
    require(40 <= number <= 2000 and number % 2 == 0,
            'INVALID_HOLDOUT_CASES')
    used, evidence = _used_prompt_hashes(root, dataset)
    rng = random.Random(SEED)
    public, secret = [], []
    serial = 101_010_000
    for i in range(number):
        family = 'allocation' if i % 2 == 0 else 'encounter'
        generator = corpus.allocation if family == 'allocation' else corpus.encounter
        for _ in range(4096):
            record = generator(rng, 'test', serial)
            serial += 1
            key = digest(record['prompt'])
            if key not in used:
                break
        else:
            require(False, 'FRESH_COHORT_EXHAUSTED')
        require(record.get('annotation_provenance') ==
                'AUTHOR_MARKED_IN_SOURCE_GENERATOR', 'AUTHOR_GOLD_UNAVAILABLE')
        contract.validate_graph(record['graph'], complete=True)
        used.add(key)
        name = f'g2-0101-{i:04d}'
        public.append({'case_id': name, 'family': family,
                       'prompt': record['prompt'], 'prompt_sha256': key})
        secret.append({'case_id': name, 'family': family,
                       'prompt_sha256': key, 'graph': record['graph']})
    return public, secret, evidence


def prepare(*, root=ROOT_DEFAULT, output=None, cases=CASES):
    dest = location(root, output)
    require(not dest.exists() and dest.parent.is_dir(),
            'OUTPUT_EXISTS_OR_PARENT_MISSING')
    previous_commit, training, prior_dir = prior._selected_source(root)
    spec = previous_commit['decoder_spec']
    require(spec.get('method') == 'joint', 'JOINT_DECODER_REQUIRED')
    train = calibrate(training, spec)
    paths = structural.paths_for(root)
    prompts, answers, excluded = _fresh_cohort(root, Path(paths['dataset']), cases)
    dest.mkdir(); (dest/'seal').mkdir()
    write_rows(dest/'seal'/'prompts.jsonl', prompts)
    write_rows(dest/'seal'/'answers.SECRET.jsonl', answers)
    write(dest/'train_evidence.json', train)
    commit = {'schema': SCHEMA, 'status': 'CLASS_FACTORS_FROZEN_BEFORE_BLIND',
              'runner_sha256': sha(__file__), 'source': previous_commit['source'],
              'decoder_spec': spec, 'offsets': _offsets(train['frozen_offsets']),
              'training_0099_lock_sha256': sha(prior_dir/'capture'/'lock.json'),
              'training_0099_commit_sha256': sha(prior_dir/'commit.json'),
              'training_evidence_sha256': sha(dest/'train_evidence.json'),
              'excluded_prior_holdout_sha256': excluded,
              'cases': cases, 'prompts_sha256': sha(dest/'seal'/'prompts.jsonl'),
              'answers_sha256': sha(dest/'seal'/'answers.SECRET.jsonl'),
              'gradient_steps': 0, 'weight_edits': 0, 'g3_unlocked': False,
              'production_promotion': False}
    write(dest/'seal'/'commit.json', commit)
    print('G2_0101_PREPARED ' + json.dumps({
        'checkpoint': commit['source']['checkpoint'],
        'source_sha256': commit['source']['head_sha256'],
        'train_cases': len(training), 'sealed_holdout_cases': cases,
        'class_offsets': commit['offsets'], 'joint_training_protected': train['joint_safe'],
        'excluded_prior_campaigns': sorted(excluded), 'status': commit['status']},
        sort_keys=True), flush=True)
    return commit


def verify(root, dest, *, gold=False):
    c = read(dest/'seal'/'commit.json')
    require(c.get('schema') == SCHEMA and c.get('runner_sha256') == sha(__file__) and
            c.get('status') == 'CLASS_FACTORS_FROZEN_BEFORE_BLIND' and
            c.get('weight_edits') == c.get('gradient_steps') == 0 and
            c.get('g3_unlocked') is False and c.get('production_promotion') is False and
            c['prompts_sha256'] == sha(dest/'seal'/'prompts.jsonl') and
            c['training_evidence_sha256'] == sha(dest/'train_evidence.json'),
            'PRECOMMITMENT_DRIFT')
    evidence = read(dest/'train_evidence.json')
    require(c['offsets'] == _offsets(evidence['frozen_offsets']) and
            evidence['joint_safe'] == any(c['offsets'].values()),
            'FROZEN_CLASS_SETTINGS_DRIFT')
    former, _, prior_dir = prior._selected_source(root)
    require(former['source'] == c['source'] and former['decoder_spec'] == c['decoder_spec'] and
            sha(prior_dir/'capture'/'lock.json') == c['training_0099_lock_sha256'] and
            sha(prior_dir/'commit.json') == c['training_0099_commit_sha256'],
            'TRAIN_OR_SOURCE_DRIFT')
    _, prior_evidence = _used_prompt_hashes(root, Path(structural.paths_for(root)['dataset']))
    require(prior_evidence == c['excluded_prior_holdout_sha256'],
            'PRIOR_HOLDOUT_DRIFT')
    if gold:
        require(sha(dest/'seal'/'answers.SECRET.jsonl') == c['answers_sha256'],
                'ANSWERS_DRIFT')
    return c


def _validated_shard(path, row, c):
    obj = prior._gzip_read(path)
    require(obj.get('case_id') == row['case_id'] and
            obj.get('prompt_sha256') == row['prompt_sha256'] and
            obj.get('family') == row['family'] and
            set(obj['predictions']) == set(ARMS),
            'BLIND_SHARD_IDENTITY_DRIFT')
    bakeoff.validate_surface(obj['surface'])
    for arm in ARMS:
        require(decoder(obj['surface'], c['decoder_spec'], arm, c['offsets']) ==
                source.as_set(obj['predictions'][arm]),
                'BLIND_PREDICTION_REPLAY_DISAGREEMENT')
    return obj


def predict(*, root=ROOT_DEFAULT, output=None, device='cuda'):
    dest = location(root, output)
    c = verify(root, dest)
    blind = dest/'blind'
    require(not (blind/'lock.json').exists(), 'ALREADY_LOCKED')
    blind.mkdir(exist_ok=True)
    shards = blind/'shards'; shards.mkdir(exist_ok=True)
    prompts = list(rows(dest/'seal'/'prompts.jsonl'))
    require(len(prompts) == c['cases'] and len({r['case_id'] for r in prompts}) == len(prompts),
            'PROMPT_SET_DRIFT')
    pending = [r for r in prompts if not (shards/(r['case_id']+'.json.gz')).exists()]
    if pending:
        paths = structural.paths_for(root)
        net, tok, composite, reference, _ = structural._load_runtime(paths, device)
        head = source._load_scientific_head(c['source']['checkpoint'], reference, device=device)
        head.eval().requires_grad_(False)
        with torch.inference_mode():
            for step, r in enumerate(pending, 1):
                require(r['prompt_sha256'] == digest(r['prompt']), 'PROMPT_DRIFT')
                prepared, hidden = bakeoff.previous._capture_inference(
                    net, tok, r['prompt'], device, 2048)
                g1 = composite.predict(hidden, prepared,
                                       family=r['family'], native_stage='G1')
                surface = bakeoff._infer(head, hidden, prepared['eligible'].to(device),
                                         g1['token_spans'], prepared, c['decoder_spec'],
                                         with_margins=True)
                guesses = {a: [source.json_edge(e) for e in sorted(decoder(
                    surface, c['decoder_spec'], a, c['offsets']))] for a in ARMS}
                prior._gzip_write(shards/(r['case_id']+'.json.gz'), {
                    'case_id': r['case_id'], 'family': r['family'],
                    'prompt_sha256': r['prompt_sha256'], 'surface': surface,
                    'predictions': guesses})
                if step == 1 or step % 25 == 0 or step == len(pending):
                    print(f'G2_0101_BLIND {len(prompts)-len(pending)+step}/{len(prompts)}',
                          flush=True)
    shas = {}; compact = []
    for r in prompts:
        file = shards/(r['case_id']+'.json.gz')
        obj = _validated_shard(file, r, c)
        shas[file.name] = sha(file)
        compact.append({k: obj[k] for k in
                        ('case_id', 'family', 'prompt_sha256', 'predictions')})
    write_rows(blind/'predictions.jsonl', compact)
    lock = {'schema': SCHEMA, 'status': 'BLIND_PREDICTIONS_LOCKED_UNSCORED',
            'predictions_sha256': sha(blind/'predictions.jsonl'),
            'shard_sha256': shas, 'precommit_sha256': sha(dest/'seal'/'commit.json'),
            'source_head_sha256': c['source']['head_sha256'],
            'gold_accessed': False, 'gradient_steps': 0, 'weight_edits': 0,
            'production_promotion': False, 'g3_unlocked': False}
    write(blind/'lock.json', lock)
    print('G2_0101_BLIND_LOCKED ' + json.dumps({
        'cases': len(compact), 'gold_accessed': False,
        'predictions_sha256': lock['predictions_sha256']}, sort_keys=True), flush=True)
    return lock


def _per_label(pred, gold, counter):
    for e in pred & gold:
        counter[e[4]]['tp'] += 1
    for e in pred - gold:
        counter[e[4]]['fp'] += 1
    for e in gold - pred:
        counter[e[4]]['fn'] += 1


def score(*, root=ROOT_DEFAULT, output=None):
    dest = location(root, output)
    c = verify(root, dest, gold=True)
    require(not (dest/'scored').exists(), 'ALREADY_SCORED')
    lock = read(dest/'blind'/'lock.json')
    require(lock.get('schema') == SCHEMA and
            lock.get('status') == 'BLIND_PREDICTIONS_LOCKED_UNSCORED' and
            lock.get('precommit_sha256') == sha(dest/'seal'/'commit.json') and
            lock.get('predictions_sha256') == sha(dest/'blind'/'predictions.jsonl') and
            lock.get('source_head_sha256') == c['source']['head_sha256'] and
            lock.get('gold_accessed') is False and
            lock.get('weight_edits') == 0 and lock.get('production_promotion') is False,
            'BLIND_LOCK_DRIFT')
    prompts = list(rows(dest/'seal'/'prompts.jsonl'))
    answers = {r['case_id']: r for r in rows(dest/'seal'/'answers.SECRET.jsonl')}
    require(len(answers) == len(prompts), 'ANSWERS_COUNT_DRIFT')
    totals = {fam:{arm:Counter() for arm in ARMS} for fam in FAMILIES}
    impact = {fam:{arm:Counter() for arm in ARMS[1:]} for fam in FAMILIES}
    labels = {fam:{arm:{kind:Counter() for kind in LABELS} for arm in ARMS}
              for fam in FAMILIES}
    for r in prompts:
        ans = answers.get(r['case_id'])
        require(ans is not None and ans['prompt_sha256'] == r['prompt_sha256'] and
                ans['family'] == r['family'], 'ANSWERS_BINDING_DRIFT')
        file = dest/'blind'/'shards'/(r['case_id']+'.json.gz')
        require(sha(file) == lock['shard_sha256'].get(file.name),
                'BLIND_SHARD_SHA256_DRIFT')
        obj = _validated_shard(file, r, c)
        gold = bakeoff.truth(ans['graph'])
        preds = {arm: source.as_set(obj['predictions'][arm]) for arm in ARMS}
        for fam in (r['family'], 'overall'):
            for arm in ARMS:
                totals[fam][arm].update(prior._count(preds[arm], gold))
                _per_label(preds[arm], gold, labels[fam][arm])
        before = preds['baseline']
        for arm in ARMS[1:]:
            after = preds[arm]
            change = {'exact_gained': int(after == gold and before != gold),
                      'exact_lost': int(before == gold and after != gold),
                      'rescued_tp': len((after & gold) - (before & gold)),
                      'lost_tp': len((before & gold) - (after & gold)),
                      'introduced_fp': len((after - gold) - (before - gold)),
                      'removed_fp': len((before - gold) - (after - gold))}
            for fam in (r['family'], 'overall'):
                impact[fam][arm].update(change)
    metrics = {f: {a: prior._score(ct) for a, ct in by.items()}
               for f,by in totals.items()}
    effects = {f: {a: {'f1': metrics[f][a]['edge_f1']-metrics[f]['baseline']['edge_f1'],
                       'exact':metrics[f][a]['exact']-metrics[f]['baseline']['exact'],
                       'fp':metrics[f][a]['fp']-metrics[f]['baseline']['fp'],
                       'fn':metrics[f][a]['fn']-metrics[f]['baseline']['fn']}
                   for a in ARMS[1:]} for f in FAMILIES}
    training = read(dest/'train_evidence.json')
    safe = training['joint_safe'] and any(c['offsets'].values())
    gate = (safe and effects['overall']['class_calibrated']['f1'] >= .005 and
            effects['overall']['class_calibrated']['exact'] >= 0 and
            impact['overall']['class_calibrated']['exact_lost'] == 0 and
            all(effects[f]['class_calibrated']['f1'] >= -0.001
                for f in ('allocation','encounter')))
    require(source._candidate_info(c['source']['checkpoint']) == c['source'],
            'ORIGINAL_CHECKPOINT_MUTATED')
    report = {'schema':SCHEMA,
              'status':('CLASS_ADMISSION_BLIND_CANDIDATE_NOT_PROMOTED' if gate else
                        'CLASS_ADMISSION_NO_CERTIFIED_GAIN'),
              'source':c['source'], 'cases':len(prompts),
              'class_offsets':c['offsets'], 'training_joint_protected':safe,
              'metrics':metrics, 'effects':effects,
              'paired':{f:{a:dict(v) for a,v in by.items()} for f,by in impact.items()},
              'per_label': {f:{a:{kind:dict(v) for kind,v in classes.items()}
                             for a,classes in by.items()} for f,by in labels.items()},
              'gate':bool(gate), 'source_unchanged':True, 'weight_edits':0,
              'gradient_steps':0, 'production_promotion':False, 'g3_unlocked':False,
              'limitations': ('All calibration uses only 0099 TRAIN capture. '
                              '0100 +1 threshold is a predefined comparator, not '
                              'certified or used for 0101 selection. Fresh holdout '
                              'uses synthetic author-marked examples; no automatic '
                              'promotion, no weight modifications.')}
    (dest/'scored').mkdir()
    write(dest/'scored'/'report.json', report)
    print('G2_0101_SCORED ' + json.dumps({
        'class_offsets':c['offsets'], 'training_joint_protected':safe,
        'effects':effects, 'paired':report['paired'], 'gate':gate,
        'source_unchanged':True, 'status':report['status']},sort_keys=True),flush=True)
    return report


def run(*, root=ROOT_DEFAULT, output=None, cases=CASES, device='cuda'):
    dest = location(root, output)
    if not (dest/'seal'/'commit.json').exists():
        prepare(root=root, output=output, cases=cases)
    if not (dest/'blind'/'lock.json').exists():
        predict(root=root, output=output, device=device)
    if not (dest/'scored'/'report.json').exists():
        return score(root=root, output=output)
    c = verify(root, dest, gold=True)
    result = read(dest/'scored'/'report.json')
    require(result.get('schema') == SCHEMA and result.get('source') == c['source'] and
            result.get('source_unchanged') is True and result.get('weight_edits') == 0 and
            result.get('production_promotion') is False,
            'FINISHED_REPORT_DRIFT')
    print('G2_0101_RESULT ' + json.dumps({
        'status': result['status'], 'effects': result['effects'],
        'gate':result['gate'], 'source_unchanged':True},sort_keys=True),flush=True)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    commands=p.add_subparsers(dest='command',required=True)
    for cmd in ('run','prepare','predict','score','find'):
        q=commands.add_parser(cmd)
        q.add_argument('--root',default=ROOT_DEFAULT)
        if cmd != 'find': q.add_argument('--output')
        if cmd in ('run','prepare'):q.add_argument('--cases',type=int,default=CASES)
        if cmd in ('run','predict'):q.add_argument('--device',default='cuda')
    a=p.parse_args(argv)
    if a.command == 'find':
        print('G2_0101_REQUIREMENTS '+json.dumps({
            'g2_0099_training':str(Path(a.root)/prior.TRAIN_CAMPAIGN/'capture'/'lock.json'),
            'g2_0100_prior':str(Path(a.root)/prior.NAME/'seal'/'prompts.jsonl'),
            'g2_0096_source':source.discover(a.root,source.PREFERRED_ARM)},sort_keys=True),
            flush=True)
        return
    params={'root':a.root,'output':a.output}
    if a.command in ('run','prepare'):params['cases']=a.cases
    if a.command in ('run','predict'):params['device']=a.device
    globals()[a.command](**params)

if __name__=='__main__':
    main()
