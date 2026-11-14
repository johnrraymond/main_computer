"""G2-0003: frozen G2 score discrimination by relation and source competition.

The previously inspected G2-0002 independent DEV cohort is CALIBRATION here.
A newly generated prompt-disjoint DEV cohort is held out for independent
validation; no challenge/test, gold at inference, parameter training or unlock.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import hashlib
import json
import math

import torch

from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools import nanojev_gamer_v1_g2_binding_selection as parent
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES
from tools.nanojev_gamer_v1_graph_contract import MAX_EDGES

SCHEMA = 'nanojev.gamer.graph.g2.frozen-binding-discrimination.v1'
REPORT = 'g2_binding_discrimination_report.json'
CASES = 'g2_binding_discrimination_per_case.jsonl'
AUDIT = 'g2_binding_score_audit.json'
SEED = 330003
VALIDATION_LIMIT = 320
CONF_THRESHOLDS = (0.50, 0.55, 0.60, 0.70, 0.80, 0.90, 0.95, 0.98, 0.995)
SHARE_THRESHOLDS = (0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.07, 0.10, 0.15, 0.20, 0.30, 0.40)
RELATIONS = BIND_CLASSES[1:]


def _sha_rows(rows):
    return hashlib.sha256(base._jsonl(rows)).hexdigest()


def new_validation_rows(train, old_calibration, old_validation, *, limit=VALIDATION_LIMIT, seed=SEED):
    if limit < 2 or limit % 2 or not train or not old_calibration or not old_validation:
        raise ValueError('GAMER_G2_0003_BAD_VALIDATION_COHORT')
    previous = train + old_calibration + old_validation
    fresh = base.novel_dev_rows(previous, limit=limit, seed=seed)
    known = {base._sha(r['prompt']) for r in previous}
    hashes = [base._sha(r['prompt']) for r in fresh]
    if len(set(hashes)) != len(hashes) or set(hashes) & known:
        raise ValueError('GAMER_G2_0003_PROMPT_LEAKAGE')
    if Counter(r['family'] for r in fresh) != {'encounter': limit//2, 'allocation': limit//2}:
        raise ValueError('GAMER_G2_0003_UNBALANCED_VALIDATION')
    return fresh


def verify_parent_report(report, saved, hashes, calibration, *, path):
    """Require the exact G2-0002 frozen run and its stored case/replay contract."""
    required = {
        'schema': parent.SCHEMA,
        'g1_frozen': True, 'g2_development_weights_frozen': True,
        'trainable_parameters': 0, 'g2_certified': False,
        'g2_production_unlocked': False, 'production_promotion': False,
        'certification_authorized': False, 'graph_execution_authorized': False,
        'memory_writes': False, 'sealed_test_used': False,
        'challenge_or_test_split_used': False, 'oracle_input_to_decoder': False,
        'compatibility_source': 'author_marked_train_only',
        'parent_bootstrap_replay_pass': True, 'calibration_only_rule_selection': True,
        'g1_development_candidate': base.G1_ARM,
        'validation_seed': parent.SEED, 'validation_cases': len(calibration),
        'validation_dataset_sha256': _sha_rows(calibration),
        'parent_bootstrap_dev_cases': parent.CALIBRATION_LIMIT,
        'g2_parent_hashes': hashes,
    }
    for key, expected in required.items():
        if type(report.get(key)) is not type(expected) or report[key] != expected:
            raise ValueError('GAMER_G2_0003_PARENT_CONTRACT:' + key)
    rules = {**parent.configurations()[0], **parent.configurations()[1]}
    if report.get('decoder_rules_predeclared') != rules:
        raise ValueError('GAMER_G2_0003_PARENT_RULES_CHANGED')
    if report.get('independent_development_screen_pass') is not False:
        raise ValueError('GAMER_G2_0003_PARENT_PROMOTION_STATE_CHANGED')
    if not isinstance(saved, list) or len(saved) != len(calibration):
        raise ValueError('GAMER_G2_0003_PARENT_CASE_COUNT')
    for i, (r, case) in enumerate(zip(calibration, saved), 1):
        if (case.get('group') != 'independent_validation' or case.get('index') != i or
                case.get('family') != r['family'] or
                case.get('prompt_sha256') != base._sha(r['prompt'])):
            raise ValueError('GAMER_G2_0003_PARENT_CASE_IDENTITY')
    return {'g2_0002_report_sha256': backbone.sha256(path / parent.REPORT),
            'g2_0002_cases_sha256': backbone.sha256(path / parent.CASES)}


def replay_0002(rows, saved, report, allowed):
    control, candidate = parent.configurations()
    expected = {**control, **({'selected_' + report['selected_rule']: candidate[report['selected_rule']]}
                              if report['selected_rule'] is not None else {})}
    for row, old in zip(rows, saved):
        if (row['index'] != old['index'] or row['family'] != old['family'] or
                row['prompt_sha256'] != old['prompt_sha256'] or
                len(row['anchors']) != old['g1_predicted_nodes'] or
                len(row['gold']) != old['gold_binding_edges']):
            raise ValueError('GAMER_G2_0003_PARENT_REPLAY_IDENTITY')
        if parent._case_scores(row, expected, allowed) != old.get('configurations'):
            raise ValueError('GAMER_G2_0003_PARENT_REPLAY_PREDICTIONS')
    metrics = parent._summarize(rows, expected, allowed)
    if metrics != report.get('independent_validation_metrics'):
        raise ValueError('GAMER_G2_0003_PARENT_REPLAY_METRICS')


def feature_edges(item, allowed):
    """Inference-only features: positive-class winner and its source/relation share.

    Normalization is over candidate destinations of one predicted source and
    one predicted relation, never over labeled edges. There is no fixed cap.
    """
    anchors, logits = item['anchors'], item['logits']
    if len(anchors) > 128 or logits.shape != (len(anchors), len(anchors), len(BIND_CLASSES)):
        raise ValueError('GAMER_G2_0003_PAIR_SHAPE')
    if not torch.isfinite(logits).all():
        raise ValueError('GAMER_G2_0003_NONFINITE_SCORES')
    probs = logits.detach().to(dtype=torch.float32, device='cpu').softmax(-1)
    sums = defaultdict(float)
    tmp = []
    for i, source in enumerate(anchors):
        for j, dest in enumerate(anchors):
            if i == j:
                continue
            k = int(probs[i, j].argmax())
            if k == 0:
                continue
            conf = float(probs[i, j, k])
            if conf < 0.5:
                continue
            relation = BIND_CLASSES[k]
            if (relation, source[2], dest[2]) not in allowed:
                continue
            edge = (source, dest, relation)
            tmp.append((edge, conf, i, relation, source[2]))
            sums[(i, relation)] += conf
    result = []
    for edge, conf, i, relation, source_kind in tmp:
        share = conf / sums[(i, relation)]
        result.append((edge, conf, share, relation, source_kind))
    return result


def _candidate_stats(cached):
    """Gold is consulted *only* for calibration statistics, not feature creation."""
    groups = defaultdict(list)
    gold_by_relation = Counter()
    for case in cached:
        for edge in case['gold']:
            gold_by_relation[edge[2]] += 1
        for edge, conf, share, rel, kind in case['features']:
            groups[rel].append((conf, share, edge in case['gold'], kind))
    return groups, gold_by_relation


def _f_beta(tp, fp, total, beta):
    b = beta * beta
    return (1+b)*tp / max(1, (1+b)*tp + b*(total-tp) + fp)


def fit_thresholds(calibration_cache, *, measure, beta):
    """Per-relation F-beta optimization from calibration only; fixed grid and ties."""
    if measure not in ('confidence', 'source_share') or beta not in (1, 2, 3):
        raise ValueError('GAMER_G2_0003_BAD_FIT_SPEC')
    groups, totals = _candidate_stats(calibration_cache)
    grid = CONF_THRESHOLDS if measure == 'confidence' else SHARE_THRESHOLDS
    index = 0 if measure == 'confidence' else 1
    thresholds = {}
    evidence = {}
    for rel in RELATIONS:
        rows = groups[rel]
        total = totals[rel]
        if total == 0 or not rows:
            thresholds[rel] = 1.000001  # Suppress a relation with no calibration support.
            evidence[rel] = {'gold': total, 'candidates': len(rows), 'fallback': True}
            continue
        scores = []
        for cut in grid:
            selected = [r for r in rows if r[index] >= cut]
            tp = sum(bool(r[2]) for r in selected)
            fp = len(selected) - tp
            scores.append((_f_beta(tp, fp, total, beta), tp, -fp, -cut, cut, tp, fp))
        choice = max(scores)
        thresholds[rel] = choice[4]
        evidence[rel] = {'gold': total, 'candidates': len(rows),
                         'calibration_correct': choice[5],
                         'calibration_false_positive': choice[6],
                         'calibration_f_beta': choice[0]}
    return thresholds, evidence


def fitted_rules(calibration_cache):
    result = {}
    notes = {}
    for beta in (1, 2, 3):
        for measure, prefix in (('confidence','relation_confidence'), ('source_share','source_competition')):
            cut, evidence = fit_thresholds(calibration_cache, measure=measure, beta=beta)
            name = f'{prefix}_f{beta}'
            result[name] = {'mode': measure, 'thresholds': cut, 'compatible': True}
            notes[name] = evidence
    # Both evidence-derived thresholds must pass: diagnostic interaction only.
    result['dual_relation_source_f2'] = {'mode':'dual', 'compatible':True,
        'confidence':result['relation_confidence_f2']['thresholds'],
        'source_share':result['source_competition_f2']['thresholds']}
    return result, notes


def edges_for_rule(features, rule):
    mode = rule['mode']
    if mode not in ('confidence','source_share','dual'):
        raise ValueError('GAMER_G2_0003_UNKNOWN_RULE')
    selected = set()
    for edge, conf, share, relation, _ in features:
        if mode == 'confidence':
            accept = conf >= rule['thresholds'][relation]
        elif mode == 'source_share':
            accept = share >= rule['thresholds'][relation]
        elif mode == 'dual':
            accept = (conf >= rule['confidence'][relation] and
                      share >= rule['source_share'][relation])
        else:
            raise ValueError('GAMER_G2_0003_UNKNOWN_RULE')
        if accept:
            selected.add(edge)
            if len(selected) > MAX_EDGES:
                raise ValueError('GAMER_G2_0003_EDGE_LIMIT')
    return selected


def score_cases(cached, rules, allowed, *, include_controls=True):
    outcomes = defaultdict(list)
    per_case = []
    controls = {'argmax_050':parent.configurations()[0]['argmax_050'],
                'argmax_080':parent.configurations()[0]['argmax_080'],
                'compatible_050':parent.configurations()[0]['compatible_050'],
                'top2_050':parent.configurations()[0]['top2_050'],
                'compatible_cap3_p50':parent.configurations()[1]['compatible_cap3_p50']}
    for case in cached:
        results = {}
        for name, rule in rules.items():
            results[name] = parent.score_set(edges_for_rule(case['features'],rule),case['gold'])
        if include_controls:
            for name, rule in controls.items():
                results[name] = parent.score_set(parent.choose_edges(case['anchors'],case['logits'],rule,allowed),case['gold'])
        for name, result in results.items():
            outcomes[name].append({**result,'family':case['family']})
        per_case.append({'index':case['index'],'family':case['family'],
                         'prompt_sha256':case['prompt_sha256'],
                         'g1_predicted_nodes':len(case['anchors']),
                         'gold_binding_edges':len(case['gold']),
                         'configurations':results})
    return {name:base.metrics(results) for name,results in outcomes.items()},per_case


def choose_calibration_winner(metrics, rules):
    baseline = metrics['argmax_050']
    recall_floor = max(parent.MIN_RECALL, baseline['recall'] - parent.RECALL_DROP_LIMIT)
    eligible = [name for name in rules if metrics[name]['recall'] >= recall_floor]
    if not eligible:
        return None, {'reason':'NO_RULE_MEETS_RECALL_FLOOR', 'recall_floor':recall_floor,
                      'eligible_count':0}
    winner = max(eligible,key=lambda name:(metrics[name]['f1'],metrics[name]['precision'],
                                             -metrics[name]['false_positive'],name))
    return winner, {'reason':'CALIBRATION_MAX_F1_WITH_RECALL_FLOOR',
                    'recall_floor':recall_floor, 'eligible_count':len(eligible)}


def _quantiles(values):
    if not values:
        return {'count':0,'p10':None,'median':None,'p90':None,'p99':None}
    values = sorted(values)
    def q(p):
        return values[int((len(values)-1)*p)]
    return {'count':len(values), 'p10':q(.10), 'median':q(.5),
            'p90':q(.9), 'p99':q(.99)}


def _auc(pos, neg):
    if not pos or not neg:
        return None
    # Area under ROC for discriminating a correct candidate from a false one.
    # Aggregate identical scores using exact tie half-credit.
    ranked = sorted([(v, 1) for v in pos] + [(v, 0) for v in neg])
    neg_so_far = 0
    wins = 0.0
    i = 0
    while i < len(ranked):
        j = i
        while j < len(ranked) and ranked[j][0] == ranked[i][0]:
            j += 1
        positives = sum(ranked[k][1] for k in range(i,j))
        negatives = j-i-positives
        wins += positives * (neg_so_far + negatives/2)
        neg_so_far += negatives
        i = j
    return wins/(len(pos)*len(neg))


def score_diagnostics(cached):
    # Operates on calibration only. All candidate features were computed blind.
    buckets = defaultdict(lambda:{'true_conf':[],'false_conf':[],
                                  'true_share':[],'false_share':[]})
    coverage = Counter()
    for case in cached:
        coverage['cases'] += 1
        coverage['gold_edges'] += len(case['gold'])
        for edge,conf,share,rel,kind in case['features']:
            correct = edge in case['gold']
            coverage['compatible_true' if correct else 'compatible_false'] += 1
            for label in ('overall', 'relation:'+rel, 'source:'+kind, 'relation_source:'+rel+'/'+kind):
                d = buckets[label]
                prefix = 'true_' if correct else 'false_'
                d[prefix+'conf'].append(conf)
                d[prefix+'share'].append(share)
    summary = {}
    for label, b in sorted(buckets.items()):
        summary[label] = {
            'correct_confidence':_quantiles(b['true_conf']),
            'false_confidence':_quantiles(b['false_conf']),
            'correct_source_share':_quantiles(b['true_share']),
            'false_source_share':_quantiles(b['false_share']),
            'confidence_auc':_auc(b['true_conf'],b['false_conf']),
            'source_share_auc':_auc(b['true_share'],b['false_share']),
        }
    return {'calibration_only':True,'candidate_definition':'top1_non_none_probability_gte_0.5_and_train_compatible',
            'coverage':dict(coverage),'score_separation':summary}


def _prepare_cache(captured,allowed):
    return [{**case,'features':feature_edges(case,allowed)} for case in captured]


def run(*, root, output, g1_validation_report, g2_run, selection_run,
        device='cuda', max_length=2048, validation_limit=VALIDATION_LIMIT):
    root=Path(root);output=Path(output);selection_run=Path(selection_run)
    if output.exists():
        raise FileExistsError('GAMER_G2_0003_IMMUTABLE_OUTPUT')
    head,manifest,dataset,bootstrap,bootstrap_cases,upstream,hashes = parent.load_parents(
        root,g1_validation_report,g2_run)
    train = base.original_training_rows(dataset,parent.TRAIN_LIMIT)
    if base._sha('\n'.join(base._sha(r['prompt']) for r in train)) != bootstrap.get('train_prompt_hashes_sha256'):
        raise ValueError('GAMER_G2_0003_TRAIN_REPLAY_CHANGED')
    old_calibration=base.novel_dev_rows(train,limit=parent.CALIBRATION_LIMIT,seed=base.SEED)
    if _sha_rows(old_calibration) != bootstrap.get('dev_dataset_sha256'):
        raise ValueError('GAMER_G2_0003_BOOTSTRAP_DEV_CHANGED')
    old_validation=parent.new_validation_rows(train,old_calibration,limit=parent.VALIDATION_LIMIT)
    fresh=new_validation_rows(train,old_calibration,old_validation,limit=validation_limit)
    report=json.loads((selection_run/parent.REPORT).read_text(encoding='utf-8'))
    rows=[json.loads(line) for line in (selection_run/parent.CASES).read_text(encoding='utf-8').splitlines() if line.strip()]
    saved=[row for row in rows if row.get('group') == 'independent_validation']
    if len(rows) != report.get('parent_bootstrap_dev_cases',-1) + len(saved):
        raise ValueError('GAMER_G2_0003_PARENT_UNEXPECTED_CASES')
    lineages=verify_parent_report(report,saved,hashes,old_validation,path=selection_run)
    allowed=parent.compatibility_from_train(train)
    if report.get('compatibility_sha256') != parent._hash_json([list(x) for x in sorted(allowed)]):
        raise ValueError('GAMER_G2_0003_COMPATIBILITY_CHANGED')
    head=head.to(device).eval().requires_grad_(False)
    if any(p.requires_grad for p in head.parameters()):
        raise ValueError('GAMER_G2_0003_HEAD_NOT_FROZEN')
    model,tok=backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    if any(p.requires_grad for p in model.parameters()):
        raise ValueError('GAMER_G2_0003_BACKBONE_UNFROZEN')
    original={k:v.detach().cpu().clone() for k,v in head.state_dict().items()}
    prior=parent.capture(head,manifest,model,tok,old_validation,
                         device=device,max_length=max_length,group='g2_0002_replay_calibration')
    replay_0002(prior,saved,report,allowed)
    calibration=_prepare_cache(prior,allowed)
    rules,evidence=fitted_rules(calibration)
    audit=score_diagnostics(calibration)
    calibration_metrics,_=score_cases(calibration,rules,allowed)
    selected,decision=choose_calibration_winner(calibration_metrics,rules)
    # Neither feature/rule fitting nor selection has ever accessed fresh data.
    independent=parent.capture(head,manifest,model,tok,fresh,device=device,
                               max_length=max_length,group='new_independent_validation')
    final_metrics,final_cases=score_cases(_prepare_cache(independent,allowed),rules,allowed)
    reference=final_metrics['argmax_050']
    gates={name:parent.validation_gate(final_metrics[name],reference) for name in rules}
    for name,value in original.items():
        if not torch.equal(value,head.state_dict()[name].detach().cpu()):
            raise ValueError('GAMER_G2_0003_FROZEN_PARAMETER_CHANGED:'+name)
    report_out={
        'schema':SCHEMA,'status':'G2_FROZEN_SCORE_DISCRIMINATION_DEVELOPMENT_ONLY',
        'g1_development_candidate':base.G1_ARM,'g1_frozen':True,
        'g2_development_weights_frozen':True,'trainable_parameters':0,
        'g2_certified':False,'g2_production_unlocked':False,'production_promotion':False,
        'certification_authorized':False,'graph_execution_authorized':False,
        'memory_writes':False,'sealed_test_used':False,'challenge_or_test_split_used':False,
        'oracle_input_to_decoder':False,'train_only_compatibility':True,
        'selection_split':'g2_0002_previously_inspected_dev_independent',
        'validation_split':'new_prompt_disjoint_dev',
        'g2_0002_replay_pass':True,'parent_hashes':{**hashes,**lineages},
        'calibration_cases':len(old_validation),'calibration_dataset_sha256':_sha_rows(old_validation),
        'independent_validation_cases':len(fresh),
        'independent_validation_dataset_sha256':_sha_rows(fresh), 'independent_seed':SEED,
        'decoder_rules_calibration_fitted':rules,'threshold_fit_evidence':evidence,
        'calibration_metrics':calibration_metrics, 'selected_rule':selected,
        'calibration_selection':decision,'independent_validation_metrics':final_metrics,
        'independent_screen_checks_by_rule':gates,
        'selected_rule_independent_screen_pass':bool(selected and all(gates[selected].values())),
        'candidate_discrimination_screen_pass':any(all(v.values()) for v in gates.values()),
        'g1_parent_head_sha256':manifest['head_sha256'],
    }
    output.mkdir(parents=True)
    (output/REPORT).write_text(json.dumps(report_out,sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    (output/CASES).write_bytes(base._jsonl([{'group':'new_independent_validation',**r} for r in final_cases]))
    (output/AUDIT).write_text(json.dumps(audit,sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    return report_out
