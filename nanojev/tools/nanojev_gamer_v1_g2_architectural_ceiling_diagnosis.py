"""G2-0017: frozen architectural-ceiling and failure-attribution laboratory.

Diagnostic *only*, on previously exposed G2-0016 DEV and structural OOD
prompts. No training, new threshold selection, production promotion, or graph
execution. Stage-wise oracle reachability is an upper bound, not a decoder.
The competitor ablation is a fixed-weight intervention, not proof of an
information-theoretic limitation.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
import json
import math

import torch
from safetensors.torch import load_file

from tools import nanojev_gamer_v1_backbone_first as backbone
from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools import nanojev_gamer_v1_g2_binding_selection as sel
from tools import nanojev_gamer_v1_g2_relational_training as rel
from tools import nanojev_gamer_v1_g2_density_discrimination as density
from tools import nanojev_gamer_v1_g2_graph_consistency as graph6
from tools import nanojev_gamer_v1_g2_learned_graph_context as learned8
from tools import nanojev_gamer_v1_g2_recall_preserving_graph as recall9
from tools import nanojev_gamer_v1_g2_crowded_recall_calibration as crowd10
from tools import nanojev_gamer_v1_g2_source_conditioned_discrimination as source13
from tools import nanojev_gamer_v1_g2_density_specialization as specialized14
from tools import nanojev_gamer_v1_g2_density_routing_robustness as robust15
from tools import nanojev_gamer_v1_g2_general_relational_discrimination as general16
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES
from tools.nanojev_gamer_v1_graph_contract import MAX_EDGES

SCHEMA = 'nanojev.gamer.graph.g2.architectural-ceiling-diagnosis.v1'
REPORT = 'g2_architectural_ceiling_diagnosis_report.json'
CASES = 'g2_architectural_ceiling_diagnosis_per_case.jsonl'
TOP_KS = (2, 4, 8, 16, 24, 32, 48, 64, 128)
THRESHOLDS = (.60, .65, .70, .75, .80)
BASELINE = general16.PARENT
GENERAL = 'general_75'
SPLITS = ('new_independent_validation', 'withheld_topology_validation')
CONFIGS = (BASELINE, GENERAL, 'general_60', 'general_65', 'general_70',
           'general_80', 'pair_only_75')


def regeneration(original):
    """Recreate exact G2-0016 cohorts, including all prior prompt exclusions."""
    predecessors, cohorts15 = general16.reconstruct_predecessors(original)
    augmented = general16.curriculum_rows(predecessors)
    calibration = general16.independent_rows((*predecessors, augmented),
                                              limit=general16.CALIBRATION,
                                              seed=general16.SEED)
    independent = general16.independent_rows((*predecessors, augmented, calibration),
                                              limit=general16.INDEPENDENT,
                                              seed=general16.SEED + 1)
    withheld = general16.heldout_topology_rows((*predecessors, augmented,
                                                 calibration, independent),
                                                limit=general16.HELDOUT)
    hashes = [base._sha(r['prompt']) for batch in
              (*predecessors, augmented, calibration, independent, withheld) for r in batch]
    if len(hashes) != len(set(hashes)):
        raise ValueError('GAMER_G2_0017_COHORT_OVERLAP')
    return predecessors, cohorts15, augmented, calibration, independent, withheld


def verify_frozen_0016(directory, source_run, ancestor15, augmented, training,
                       calibration, independent, withheld):
    """Verify entire saved G2-0016 score ledger, selection and weight lineage."""
    directory = Path(directory)
    report_path, cases_path, weights_path = [directory / n for n in
             (general16.REPORT, general16.CASES, general16.WEIGHTS)]
    report = json.loads(report_path.read_text(encoding='utf-8'))
    fixed = {
        'schema': general16.SCHEMA,
        'status': 'G2_GENERAL_RELATIONAL_DISCRIMINATION_DEV_NOT_CERTIFIED',
        'parent_configuration': BASELINE,
        'calibration_only_model_selection': True,
        'independent_and_ood_not_used_for_selection': True,
        'challenge_or_test_split_used': False,
        'sealed_test_used': False,
        'g1_frozen': True, 'rank_head_frozen': True,
        'g2_0015_route_frozen': True,
        'no_density_specific_inference_router': True,
        'certification_authorized': False,
        'production_promotion': False,
        'graph_execution_authorized': False,
        'independent_acceptance_pass': False,
        'train_total_cases': len(training), 'train_extra_cases': len(augmented),
        'train_dataset_sha256': density.sha_rows(training),
    }
    for field, value in fixed.items():
        if type(report.get(field)) is not type(value) or report[field] != value:
            raise ValueError('GAMER_G2_0017_PARENT_CONTRACT:' + field)
    if report.get('calibration_selection', {}).get('name') != GENERAL:
        raise ValueError('GAMER_G2_0017_PARENT_SELECTION_DRIFT')
    if report.get('new_weights_sha256') != backbone.sha256(weights_path):
        raise ValueError('GAMER_G2_0017_PARENT_WEIGHT_DRIFT')
    p = report.get('parent_hashes', {})
    expected = {
        'g2_0015_report_sha256': backbone.sha256(Path(ancestor15) / robust15.REPORT),
        'g2_0015_cases_sha256': backbone.sha256(Path(ancestor15) / robust15.CASES),
        'g2_0013_weights_sha256': backbone.sha256(Path(source_run) / source13.WEIGHTS),
    }
    for field, value in expected.items():
        if p.get(field) != value:
            raise ValueError('GAMER_G2_0017_PARENT_LINEAGE:' + field)
    saved = [json.loads(line) for line in cases_path.read_text(encoding='utf-8').splitlines()
             if line.strip()]
    if len(saved) != general16.CALIBRATION + general16.INDEPENDENT + general16.HELDOUT:
        raise ValueError('GAMER_G2_0017_PARENT_CASE_COUNT')
    offsets = (('calibration', calibration), (SPLITS[0], independent), (SPLITS[1], withheld))
    pos = 0
    grouped = {}
    for name, rows in offsets:
        chunk = saved[pos:pos + len(rows)]
        general16.validate_records(chunk, rows, name)
        tab = report['evaluation'][name]
        if (tab.get('dataset_sha256') != density.sha_rows(rows)
                or tab.get('metrics') != general16.metrics(chunk)
                or tab.get('by_node_density') != general16.metric_groups(chunk)):
            raise ValueError('GAMER_G2_0017_PARENT_SCORE_DRIFT:' + name)
        grouped[name] = chunk
        pos += len(rows)
    if report.get('withheld_topology_metrics') != general16.topology_metrics(grouped[SPLITS[1]]):
        raise ValueError('GAMER_G2_0017_PARENT_TOPOLOGY_DRIFT')
    winner, decision, gates = general16.screen_calibration(
        report['evaluation']['calibration']['metrics'],
        report['evaluation']['calibration']['by_node_density'])
    if (winner != GENERAL or {**decision, 'name': winner} != report['calibration_selection']
            or gates != report['calibration_gates']):
        raise ValueError('GAMER_G2_0017_PARENT_CALIBRATION_DRIFT')
    iid = report['evaluation'][SPLITS[0]]
    ood = report['evaluation'][SPLITS[1]]
    expected_gates = general16.screen_independent(winner, iid['metrics'], iid['by_node_density'],
                                                  ood['metrics'], report['withheld_topology_metrics'])
    if expected_gates != report['independent_acceptance_gates']:
        raise ValueError('GAMER_G2_0017_PARENT_ACCEPTANCE_DRIFT')
    return report, grouped, {
        'g2_0016_report_sha256': backbone.sha256(report_path),
        'g2_0016_cases_sha256': backbone.sha256(cases_path),
        'g2_0016_weights_sha256': backbone.sha256(weights_path),
    }


def load_general_scorer(path, width, expected_sha):
    path = Path(path)
    if backbone.sha256(path) != expected_sha:
        raise ValueError('GAMER_G2_0017_WEIGHT_HASH_DRIFT')
    state = load_file(str(path), device='cpu')
    model = source13.SourceConditionedScorer(width)
    keys = set(model.state_dict())
    if (set(state) != {'general.' + k for k in keys} | {'crowd_only.' + k for k in keys}):
        raise ValueError('GAMER_G2_0017_WEIGHT_KEY_DRIFT')
    values = {key: state['general.' + key] for key in keys}
    if any(values[k].shape != model.state_dict()[k].shape or
           not torch.isfinite(values[k]).all() for k in keys):
        raise ValueError('GAMER_G2_0017_WEIGHT_SHAPE_DRIFT')
    model.load_state_dict(values)
    return model.eval().requires_grad_(False)


def candidate_ceiling(anchors, rank, gold, allowed, *, ks=TOP_KS):
    """Disjoint, gold-scored diagnostic after frozen inference (never fed back)."""
    n = len(anchors)
    if (len(set(anchors)) != n or n < 1 or n > 128 or rank.shape != (n, n, len(BIND_CLASSES))
            or not torch.isfinite(rank).all() or not ks or 24 not in ks):
        raise ValueError('GAMER_G2_0017_BAD_CEILING_INPUT')
    lookup = {anchor: i for i, anchor in enumerate(anchors)}
    margins = (rank.detach().float().cpu() - rank.detach().float().cpu()[..., :1])
    argmax = rank.detach().float().cpu().argmax(-1)
    eligible_ranks = {}
    # Rank inventory from the frozen scoring rule, independently of the
    # source13 inventory builder. No gold ever changes this ordering.
    for i, src in enumerate(anchors):
        for k in range(1, len(BIND_CLASSES)):
            relation = BIND_CLASSES[k]
            options = [j for j, dst in enumerate(anchors)
                       if i != j and (relation, src[2], dst[2]) in allowed]
            options.sort(key=lambda j: (-float(margins[i, j, k]), j))
            for place, j in enumerate(options, 1):
                eligible_ranks[(src, anchors[j], relation)] = (place, bool(argmax[i, j] == 0))
    out = Counter()
    top = {str(k): 0 for k in ks}
    out['gold_total'] = len(gold)
    for edge in gold:
        src, dst, relation = edge
        if src not in lookup and dst not in lookup:
            out['g1_missing_both'] += 1
        elif src not in lookup:
            out['g1_missing_source'] += 1
        elif dst not in lookup:
            out['g1_missing_destination'] += 1
        else:
            out['g1_endpoint_reachable'] += 1
            if edge not in eligible_ranks:
                out['compatibility_or_self_filtered'] += 1
            else:
                out['compatible_reachable'] += 1
                rank_at, null = eligible_ranks[edge]
                if null:
                    out['upstream_null_argmax_reachable'] += 1
                if rank_at > 24:
                    out['top24_excluded_reachable'] += 1
                for k in ks:
                    if rank_at <= k:
                        top[str(k)] += 1
    if (out['g1_endpoint_reachable'] + out['g1_missing_both'] +
            out['g1_missing_source'] + out['g1_missing_destination'] != len(gold)
            or out['compatible_reachable'] + out['compatibility_or_self_filtered'] !=
            out['g1_endpoint_reachable'] or top['24'] + out['top24_excluded_reachable'] !=
            out['compatible_reachable']):
        raise ValueError('GAMER_G2_0017_CEILING_ACCOUNTING')
    result = dict(out)
    for key in ('g1_missing_both', 'g1_missing_source', 'g1_missing_destination',
                'g1_endpoint_reachable', 'compatibility_or_self_filtered',
                'compatible_reachable', 'top24_excluded_reachable',
                'upstream_null_argmax_reachable'):
        result.setdefault(key, 0)
    result['topk_reachable'] = top
    return result


def competition_diagnostics(net, inventory):
    """Describe attention bottleneck; values are descriptive, NOT causal proof."""
    if not inventory['groups']:
        return {'groups': 0, 'multi_groups': 0, 'top_weight_gt_075': 0,
                'effective_competitors_le_2': 0, 'groups_at_top24': 0}
    with torch.inference_mode():
        h = net.pair(inventory['features'].to(next(net.parameters()).device))
        att = net.attention(h).squeeze(-1)
        groups = inventory['groups']
        counts = Counter()
        begin = 0
        while begin < len(groups):
            end = begin + 1
            while end < len(groups) and groups[end] == groups[begin]:
                end += 1
            count = end - begin
            counts['groups'] += 1
            counts['candidate_sum'] += count
            counts['max_group_size'] = max(count, counts.get('max_group_size', 0))
            if count >= source13.TOP_K:
                counts['groups_at_top24'] += 1
            if count > 1:
                counts['multi_groups'] += 1
                weights = torch.softmax(att[begin:end], dim=0)
                if float(weights.max()) > .75:
                    counts['top_weight_gt_075'] += 1
                if 1. / float((weights * weights).sum()) <= 2.0:
                    counts['effective_competitors_le_2'] += 1
            begin = end
    return dict(counts)


def _metrics(rows, name):
    return base.metrics([{**r['configurations'][name], 'family': r['family']} for r in rows])


def aggregate(rows):
    """Micro-aggregate mutually exclusive ceiling losses and fixed ablations."""
    if not rows:
        raise ValueError('GAMER_G2_0017_EMPTY_AGGREGATE')
    totals = Counter()
    ceiling_k = Counter()
    comp = Counter()
    for r in rows:
        totals.update({k: v for k, v in r['ceiling'].items() if k != 'topk_reachable'})
        ceiling_k.update(r['ceiling']['topk_reachable'])
        comp.update({k: v for k, v in r['competition'].items() if k != 'max_group_size'})
        comp['max_group_size'] = max(comp.get('max_group_size', 0),
                                     r['competition'].get('max_group_size', 0))
    gold = totals['gold_total']
    matched = totals['g1_endpoint_reachable']
    compatible = totals['compatible_reachable']
    reached = ceiling_k['24']
    result = {'cases': len(rows), 'gold_total': gold, 'stage_counts': dict(totals),
              'topk_reachable': dict(ceiling_k),
              'stage_oracle_recall': {'g1_endpoints': matched / max(1, gold),
                                      'compatible': compatible / max(1, gold),
                                      'top24': reached / max(1, gold)},
              'stage_losses': {
                  'g1_missing_endpoint': gold - matched,
                  'compatibility_or_self': matched - compatible,
                  'top24_truncation': compatible - reached,
                  'scorer_or_decoding_on_reachable': reached -
                         sum(r['configurations'][GENERAL]['correct'] for r in rows),
              }, 'competition': dict(comp),
              'fixed_weight_metrics': {name: _metrics(rows, name) for name in CONFIGS}}
    if (min(result['stage_losses'].values()) < 0 or
        sum(result['stage_losses'].values()) +
        result['fixed_weight_metrics'][GENERAL]['correct'] != gold):
        raise ValueError('GAMER_G2_0017_AGGREGATE_CEILING_IDENTITY')
    return result


def _parent_0015(case, rank, joint, context, retention, allowed, rule, device):
    old = density.candidates(case['anchors'], rank, allowed)
    baseline = density.select_edges(old, rule)
    f, odds, ordered = learned8.graph_features(case['anchors'], case['vectors'],
                                                old, baseline, rule)
    ids = [i for i, edge in enumerate(ordered) if edge in baseline]
    scores = retention(f[ids].to(device), odds[ids].to(device),
                       context(f[ids].to(device), odds[ids].to(device))).detach().cpu()
    predicted = {ordered[i] for i, score in zip(ids, scores)
                 if float(score) >= math.log(.4 / .6)}
    if density._bucket(len(case['anchors'])) == specialized14.BUCKET_SMALL:
        inv = source13.make_inventory(case, rank, allowed)
        logits = joint(inv['features'].to(device), inv['base'].to(device), inv['groups'],
                       with_competition=True).detach().cpu()
        predicted = source13.decode_scores(inv, logits, .60)
    return predicted


def diagnose_case(case, row, old, head, general, joint, context, retention, allowed,
                  rule, *, device, group, index):
    """All counterfactuals are evaluated without feeding gold to any decoder."""
    if old['prompt_sha256'] != case['prompt_sha256'] or case['prompt_sha256'] != base._sha(row['prompt']):
        raise ValueError('GAMER_G2_0017_CASE_PROMPT_DRIFT')
    with torch.inference_mode():
        rank = rel.full_logits(head, case['vectors'].to(device)).detach().cpu()
        ce = candidate_ceiling(case['anchors'], rank, case['gold'], allowed)
        inv = source13.make_inventory(case, rank, allowed)
        if len(inv['edges']) != len(set(inv['edges'])):
            raise ValueError('GAMER_G2_0017_DUPLICATE_INVENTORY')
        if sum(edge in case['gold'] for edge in inv['edges']) != ce['topk_reachable']['24']:
            raise ValueError('GAMER_G2_0017_INVENTORY_ORACLE_DISAGREES')
        x, b, groups = inv['features'].to(device), inv['base'].to(device), inv['groups']
        logits = general(x, b, groups, with_competition=True).detach().cpu()
        uncontextual = general(x, b, groups, with_competition=False).detach().cpu()
        configurations = {}
        predicted_75 = None
        for threshold in THRESHOLDS:
            name = f'general_{int(threshold*100):02d}'
            prediction = source13.decode_scores(inv, logits, threshold)
            configurations[name] = sel.score_set(prediction, case['gold'])
            if threshold == .75:
                predicted_75 = prediction
        configurations['pair_only_75'] = sel.score_set(
            source13.decode_scores(inv, uncontextual, .75), case['gold'])
        configurations[BASELINE] = sel.score_set(_parent_0015(case, rank, joint,
                        context, retention, allowed, rule, device), case['gold'])
        for name in (BASELINE, GENERAL, 'general_70', 'general_80'):
            if configurations[name] != old['configurations'][name]:
                raise ValueError('GAMER_G2_0017_FROZEN_REPLAY_DRIFT:' + name)
        if (len(case['anchors']) != old['g1_predicted_nodes'] or
                len(case['gold']) != old['gold_binding_edges'] or
                row.get('structural_topology', 'native') != old['structural_topology']):
            raise ValueError('GAMER_G2_0017_G1_REPLAY_DRIFT')
        hits = len(predicted_75 & case['gold'])
        if hits > ce['topk_reachable']['24']:
            raise ValueError('GAMER_G2_0017_ORACLE_VIOLATION')
        comp = competition_diagnostics(general, inv)
        return {'group': group, 'index': index,
                'prompt_sha256': case['prompt_sha256'], 'family': case['family'],
                'structural_topology': old['structural_topology'],
                'density_bucket': old['density_bucket'],
                'g1_predicted_nodes': len(case['anchors']),
                'gold_binding_edges': len(case['gold']),
                'ceiling': ce, 'competition': comp,
                'configurations': configurations}


def run(*, root, g1_validation_report, g2_run, selection_run, discrimination_run,
        relational_run, density_run, consistency_run, context_run, learned_run,
        recall_run, crowded_run, robustness_run, diagnosis_run, source_run,
        specialization_run, routing_robustness_run, general_run, output,
        device='cuda', max_length=2048):
    """Run a read-only architecture attribution on reused, exposed DEV cohorts."""
    root, output = Path(root), Path(output)
    if output.exists():
        raise FileExistsError('GAMER_G2_0017_IMMUTABLE_OUTPUT')
    head, manifest, dataset, bootstrap, _, _, hashes = sel.load_parents(
        root, g1_validation_report, g2_run)
    original = base.original_training_rows(dataset, source13.TRAIN_LIMIT)
    if base._sha('\n'.join(base._sha(r['prompt']) for r in original)) != bootstrap.get('train_prompt_hashes_sha256'):
        raise ValueError('GAMER_G2_0017_PARENT_TRAIN_DRIFT')
    history, prior15, augmented, calibration, independent, withheld = regeneration(original)
    if density.sha_rows(history[1]) != bootstrap.get('dev_dataset_sha256'):
        raise ValueError('GAMER_G2_0017_PARENT_BOOTSTRAP_DRIFT')
    parent15, _, parent13 = general16.verify_robust15_parent(
        routing_robustness_run, prior15, source_run, specialization_run)
    if parent15['parent_hashes']['g1_validation_report_sha256'] != hashes['g1_validation_report_sha256']:
        raise ValueError('GAMER_G2_0017_PARENT_G1_LINEAGE')
    general16.verify_frozen_ancestry15(parent15['parent_hashes'],
        g1_validation_report=g1_validation_report, g2_run=g2_run,
        discrimination_run=discrimination_run, relational_run=relational_run,
        density_run=density_run, consistency_run=consistency_run, context_run=context_run,
        learned_run=learned_run, recall_run=recall_run, crowded_run=crowded_run,
        robustness_run=robustness_run, diagnosis_run=diagnosis_run,
        source_run=source_run, specialization_run=specialization_run)
    training = original + augmented
    report16, records16, hashes16 = verify_frozen_0016(
        general_run, source_run, routing_robustness_run, augmented, training,
        calibration, independent, withheld)
    if (report16['parent_hashes']['g2_0014_report_sha256'] !=
            parent15['parent_hashes']['g2_0014_report_sha256'] or
            report16['frozen_source13_sha256'] != parent13['new_weights_sha256']):
        raise ValueError('GAMER_G2_0017_PARENT_0016_LINEAGE')
    allowed = sel.compatibility_from_train(original)
    upstream = parent15['parent_hashes']
    head = general16.load_frozen_rank4(
        head, Path(relational_run) / rel.WEIGHTS, upstream['g2_0004_weights_sha256'])
    original_state = {k: v.detach().cpu().clone() for k, v in head.state_dict().items()}
    head = head.to(device).eval().requires_grad_(False)
    model, tokenizer = backbone.model_components(manifest['model_id'],
                                                  manifest['source_adapter'], device)
    if any(p.requires_grad for p in model.parameters()):
        raise ValueError('GAMER_G2_0017_BACKBONE_UNFROZEN')
    captures = []
    for group, rows in ((SPLITS[0], independent), (SPLITS[1], withheld)):
        captured = rel.capture(head, model, tokenizer, rows, device=device,
                               max_length=max_length, group=group)
        captures.append(captured)
    width = captures[0][0]['vectors'].shape[1]
    if any(case['vectors'].shape[1] != width for batch in captures for case in batch):
        raise ValueError('GAMER_G2_0017_FEATURE_WIDTH_DRIFT')
    general = load_general_scorer(Path(general_run) / general16.WEIGHTS,
                                  width, report16['new_weights_sha256']).to(device)
    source_models = specialized14.load_source13_models(
        Path(source_run) / source13.WEIGHTS, width, parent13['new_weights_sha256'])
    joint = source_models['joint'].to(device).eval().requires_grad_(False)
    context, _ = recall9.load_frozen_scorers(
        Path(learned_run) / learned8.WEIGHTS, width,
        json.loads((Path(learned_run) / learned8.REPORT).read_text(encoding='utf-8'))['context_weights_sha256'])
    retention = crowd10.load_frozen_retention(
        Path(recall_run) / recall9.WEIGHTS, width,
        json.loads((Path(recall_run) / recall9.REPORT).read_text(encoding='utf-8'))['retention_weights_sha256'])[1]
    context = context.to(device).eval().requires_grad_(False)
    retention = retention.to(device).eval().requires_grad_(False)
    fixed5 = json.loads((Path(density_run) / density.REPORT).read_text(encoding='utf-8'))
    rule = fixed5['fixed_decoder_rules'][graph6.PARENT_ARM]
    saved = []
    for group, rows, cases in zip(SPLITS, (independent, withheld), captures):
        for i, (row, case, old) in enumerate(zip(rows, cases, records16[group]), 1):
            saved.append(diagnose_case(case, row, old, head, general, joint, context,
                        retention, allowed, rule, device=device, group=group, index=i))
            if i == 1 or i % 40 == 0 or i == len(rows):
                print(f'GAMER_G2_0017_DIAGNOSE {group} {i}/{len(rows)}', flush=True)
    general16.rel.validate_original_state(head, original_state)
    grouped = {group: [r for r in saved if r['group'] == group] for group in SPLITS}
    fresh = aggregate(grouped[SPLITS[0]])
    ood = aggregate(grouped[SPLITS[1]])
    by_topology = {top: aggregate([r for r in grouped[SPLITS[1]] if r['structural_topology'] == top])
                   for top in ('4x4', '4x2', '2x4')}
    if any(not value['cases'] for value in by_topology.values()):
        raise ValueError('GAMER_G2_0017_MISSING_OOD_TOPOLOGY')
    report = {
        'schema': SCHEMA,
        'status': 'G2_ARCHITECTURAL_CEILING_DIAGNOSIS_DEV_NOT_CERTIFIED',
        'mode': 'FROZEN_REPLAY_DIAGNOSTIC_ONLY',
        'parent_selected': GENERAL,
        'parent_0016_accepted': False,
        'frozen_robust_baseline': BASELINE,
        'parent_hashes': hashes16,
        'g1_frozen': True, 'g2_0004_rank_frozen': True,
        'g2_0013_joint_frozen': True, 'g2_0015_route_frozen': True,
        'g2_0016_general_frozen': True,
        'new_trainable_parameters': 0,
        'gold_used_only_for_offline_attribution': True,
        'candidate_gold_injection_disabled': True,
        'previously_exposed_development_only': True,
        'no_hindsight_threshold_selection': True,
        'threshold_sweep_diagnostic_only': [float(k) for k in THRESHOLDS],
        'topk_sweep_oracle_only': list(TOP_KS),
        'pair_only_fixed_weight_ablation_not_information_theoretic_proof': True,
        'cross_domain_generality_not_claimed': True,
        'source_training_vs_inference_competitor_set_mismatch':
             'TRAIN HARD-GROUP SAMPLING MAY RETAIN UP TO 21 DISTINCT NEGATIVES PLUS ALL POSITIVES; '
             'INFERENCE RANKS UP TO 24 COMPATIBLE DESTINATIONS PER SOURCE-RELATION',
        'independent_cases': len(independent),
        'withheld_topology_cases': len(withheld),
        'datasets_sha256': {SPLITS[0]: density.sha_rows(independent),
                            SPLITS[1]: density.sha_rows(withheld)},
        'diagnosis': {SPLITS[0]: fresh, SPLITS[1]: ood},
        'withheld_topologies': by_topology,
        'new_training': False, 'model_selection': False,
        'sealed_test_used': False, 'challenge_or_test_split_used': False,
        'certification_authorized': False, 'graph_execution_authorized': False,
        'production_promotion': False, 'g2_production_unlocked': False,
        'memory_writes': False,
    }
    output.mkdir(parents=True, exist_ok=False)
    (output / REPORT).write_text(json.dumps(report, sort_keys=True, indent=2,
                                            allow_nan=False) + '\n', encoding='utf-8')
    (output / CASES).write_bytes(base._jsonl(saved))
    return report
