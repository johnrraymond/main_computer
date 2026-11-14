"""G2-0018: frozen, paired competitor-population intervention on exposed DEV only.

No gold enters candidate construction, context selection, features or scoring.
The train-geometry arm is explicitly a label-blind *proxy*: actual G2-0016
TRAIN sampler selects all positives using gold, which is forbidden at inference.
No model selection, threshold selection, training, certification or promotion.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
import math
import random

import torch

from tools import nanojev_gamer_v1_backbone_first as backbone
from tools import nanojev_gamer_v1_g2_architectural_ceiling_diagnosis as ceiling17
from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools import nanojev_gamer_v1_g2_binding_selection as sel
from tools import nanojev_gamer_v1_g2_relational_training as rel
from tools import nanojev_gamer_v1_g2_density_discrimination as density
from tools import nanojev_gamer_v1_g2_source_conditioned_discrimination as source13
from tools import nanojev_gamer_v1_g2_general_relational_discrimination as general16

SCHEMA = 'nanojev.gamer.graph.g2.competitor-context-intervention.v1'
REPORT = 'g2_competitor_context_intervention_report.json'
CASES = 'g2_competitor_context_intervention_per_case.jsonl'
SEED = 460018
THRESHOLD = .75  # selected by G2-0016 CALIBRATION, fixed before 0017/0018
ARMS = ('baseline_top24', 'train_geometry_proxy_top24',
        'expanded_top48_core', 'expanded_top48')
SPLITS = ceiling17.SPLITS


def verify_parent_0017(directory, rows_by_split, parent_hashes):
    """Check immutable 0017 provenance, all per-case records and aggregations."""
    directory = Path(directory)
    rp, cp = directory / ceiling17.REPORT, directory / ceiling17.CASES
    report = json.loads(rp.read_text(encoding='utf-8'))
    required = {
        'schema': ceiling17.SCHEMA,
        'status': 'G2_ARCHITECTURAL_CEILING_DIAGNOSIS_DEV_NOT_CERTIFIED',
        'mode': 'FROZEN_REPLAY_DIAGNOSTIC_ONLY',
        'parent_selected': ceiling17.GENERAL,
        'parent_0016_accepted': False,
        'parent_hashes': parent_hashes,
        'g1_frozen': True, 'g2_0004_rank_frozen': True,
        'g2_0016_general_frozen': True,
        'new_trainable_parameters': 0,
        'candidate_gold_injection_disabled': True,
        'gold_used_only_for_offline_attribution': True,
        'sealed_test_used': False,
        'challenge_or_test_split_used': False,
        'production_promotion': False,
        'certification_authorized': False,
        'g2_production_unlocked': False,
    }
    for field, expected in required.items():
        if type(report.get(field)) is not type(expected) or report[field] != expected:
            raise ValueError('GAMER_G2_0018_PARENT_0017_CONTRACT:' + field)
    records = [json.loads(s) for s in cp.read_text(encoding='utf-8').splitlines() if s.strip()]
    if len(records) != sum(map(len, rows_by_split.values())):
        raise ValueError('GAMER_G2_0018_PARENT_0017_CASE_COUNT')
    cursor, grouped = 0, {}
    for name in SPLITS:
        rows = rows_by_split[name]
        chunk = records[cursor:cursor + len(rows)]
        cursor += len(rows)
        for index, (row, rec) in enumerate(zip(rows, chunk), 1):
            if (rec.get('group') != name or rec.get('index') != index
                or rec.get('prompt_sha256') != base._sha(row['prompt'])
                or rec.get('family') != row['family']
                or rec.get('structural_topology') != row.get('structural_topology', 'native')
                or rec.get('configurations', {}).get(ceiling17.GENERAL) is None):
                raise ValueError('GAMER_G2_0018_PARENT_0017_CASE_DRIFT')
        if (report.get('datasets_sha256', {}).get(name) != density.sha_rows(rows)
            or report.get('diagnosis', {}).get(name) != ceiling17.aggregate(chunk)):
            raise ValueError('GAMER_G2_0018_PARENT_0017_METRIC_DRIFT:' + name)
        grouped[name] = chunk
    topologies = {top: ceiling17.aggregate([r for r in grouped[SPLITS[1]]
                                          if r['structural_topology'] == top])
                  for top in ('4x4', '4x2', '2x4')}
    if topologies != report.get('withheld_topologies'):
        raise ValueError('GAMER_G2_0018_PARENT_0017_TOPOLOGY_DRIFT')
    return grouped, {
        'g2_0017_report_sha256': backbone.sha256(rp),
        'g2_0017_cases_sha256': backbone.sha256(cp),
    }


def proxy_pool(inventory, begin, end, *, seed):
    """Label-blind approximate G2-0016 near/far/top/random geometry.

    Unlike TRAIN, cannot include all gold positives; use 6 top-ranked, 5 far,
    5 near and 5 sampled remaining (the TRAIN 16//3 quotas). A target missing
    from the pool is added *only for that target's forward*, so matched edges
    never disappear from the intervention or the evaluation universe.
    """
    if type(seed) is not int or not 0 <= begin < end <= len(inventory['edges']):
        raise ValueError('GAMER_G2_0018_BAD_POOL_INPUT')
    indices = list(range(begin, end))
    # Inventory is sorted by frozen margin within each source-relation group.
    top = indices[:min(6, len(indices))]
    rest = indices[len(top):]
    far = [i for i in rest if abs(inventory['edges'][i][0][0] -
                                  inventory['edges'][i][1][0]) > 128]
    far_set = set(far)
    near = [i for i in rest if i not in far_set]
    selected = set(top + far[:5] + near[:5])
    tail = [i for i in rest if i not in selected]
    selected.update(random.Random(seed).sample(tail, min(5, len(tail))))
    return sorted(selected)


def proxy_logits(net, inventory, *, seed):
    """Score every unchanged top-24 edge under its target-retaining proxy pool.

    The attention is exactly the existing frozen model's leave-one-out rule.
    For a target in the chosen pool, remove its weight; for a target outside,
    add it only to its individual forward and then remove it. Both leave the
    same context as the other chosen members. A singleton self has zero context.
    """
    if type(seed) is not int or not inventory['edges']:
        raise ValueError('GAMER_G2_0018_BAD_PROXY_INPUT')
    groups = inventory['groups']
    if len(groups) != len(inventory['edges']) or len(groups) != len(inventory['features']):
        raise ValueError('GAMER_G2_0018_BAD_PROXY_GROUPS')
    device = next(net.parameters()).device
    with torch.inference_mode():
        x = inventory['features'].to(device)
        b = inventory['base'].to(device)
        h = net.pair(x)
        attention = net.attention(h).squeeze(-1)
        context = torch.zeros_like(h)
        begin = 0
        pool_sizes = []
        while begin < len(groups):
            end = begin + 1
            while end < len(groups) and groups[end] == groups[begin]:
                end += 1
            if groups[begin:end] != [groups[begin]] * (end-begin):
                raise ValueError('GAMER_G2_0018_UNSORTED_GROUPS')
            chosen = proxy_pool(inventory, begin, end,
                                seed=seed + 7919 * groups[begin])
            pool_sizes.append(len(chosen))
            weights = torch.softmax(attention[chosen], dim=0)
            pool_h = h[chosen]
            total = (weights[:, None] * pool_h).sum(dim=0)
            context[begin:end] = total
            if len(chosen) == 1:
                # The sole retained item sees no other competitor.
                context[chosen[0]] = 0.
            else:
                context[chosen] = (total[None, :] - weights[:, None]*pool_h) / (
                    1. - weights[:, None]).clamp(min=1.e-6)
            begin = end
        logits = net.judge(torch.cat((h, context, h-context, b.unsqueeze(-1)),
                                     dim=-1)).squeeze(-1)
    return logits.detach().cpu(), pool_sizes


def paired_scores(edges, before, after, gold, *, threshold=THRESHOLD):
    """Per-edge counterfactual attribution, uncapped to isolate score drift."""
    if (before.shape != after.shape or before.ndim != 1 or
        len(edges) != len(before) or not torch.isfinite(before).all() or
        not torch.isfinite(after).all() or not 0 < threshold < 1):
        raise ValueError('GAMER_G2_0018_BAD_PAIRED_SCORES')
    cutoff = math.log(threshold/(1-threshold))
    deltas = (after.float() - before.float()).abs()
    counts = Counter()
    counts['candidates'] = len(edges)
    counts['gold_candidates'] = sum(edge in gold for edge in edges)
    counts['absolute_score_drift_sum'] = float(deltas.sum().item())
    counts['absolute_score_drift_max'] = float(deltas.max().item()) if len(deltas) else 0.
    for i, edge in enumerate(edges):
        a, b = float(before[i]) >= cutoff, float(after[i]) >= cutoff
        label = 'gold' if edge in gold else 'nongold'
        if a and not b:
            counts[label + '_positive_to_negative'] += 1
        if not a and b:
            counts[label + '_negative_to_positive'] += 1
    return dict(counts)


def intervention_case(case, row, parent, head, general, allowed, *, device, group, index):
    """Replay the exact 0017 baseline before applying either intervention."""
    if (case['prompt_sha256'] != parent['prompt_sha256'] or
        case['prompt_sha256'] != base._sha(row['prompt'])):
        raise ValueError('GAMER_G2_0018_PROMPT_DRIFT')
    with torch.inference_mode():
        rank = rel.full_logits(head, case['vectors'].to(device)).detach().cpu()
        inv24 = source13.make_inventory(case, rank, allowed, top_k=24)
        inv48 = source13.make_inventory(case, rank, allowed, top_k=48)
        if (len(inv24['edges']) != len(set(inv24['edges'])) or
            len(inv48['edges']) != len(set(inv48['edges']))):
            raise ValueError('GAMER_G2_0018_DUPLICATE_CANDIDATES')
        positions = {edge: i for i, edge in enumerate(inv48['edges'])}
        if not set(inv24['edges']) <= set(positions):
            raise ValueError('GAMER_G2_0018_NONNESTED_INVENTORY')
        loc = torch.tensor([positions[edge] for edge in inv24['edges']], dtype=torch.long)
        # Identity of frozen features is essential to claim a context-only effect.
        if (not torch.equal(inv24['features'], inv48['features'][loc])
            or not torch.equal(inv24['base'], inv48['base'][loc])):
            raise ValueError('GAMER_G2_0018_FEATURE_DRIFT')
        normal24 = general(inv24['features'].to(device), inv24['base'].to(device),
                           inv24['groups'], with_competition=True).detach().cpu()
        normal48 = general(inv48['features'].to(device), inv48['base'].to(device),
                           inv48['groups'], with_competition=True).detach().cpu()
        proxy, sizes = proxy_logits(general, inv24,
                                   seed=SEED + int(case['prompt_sha256'][:15], 16))
        core48 = normal48[loc]
        configurations = {
            'baseline_top24': sel.score_set(source13.decode_scores(inv24, normal24,
                                                                  THRESHOLD), case['gold']),
            'train_geometry_proxy_top24': sel.score_set(source13.decode_scores(inv24,
                                                proxy, THRESHOLD), case['gold']),
            'expanded_top48_core': sel.score_set(source13.decode_scores(inv24, core48,
                                                                      THRESHOLD), case['gold']),
            'expanded_top48': sel.score_set(source13.decode_scores(inv48, normal48,
                                                                 THRESHOLD), case['gold']),
        }
        if configurations['baseline_top24'] != parent['configurations'][ceiling17.GENERAL]:
            raise ValueError('GAMER_G2_0018_BASELINE_REPLAY_DRIFT')
        coverage = ceiling17.candidate_ceiling(case['anchors'], rank, case['gold'], allowed)
        if (coverage != parent['ceiling'] or
            len(inv24['edges']) != parent['competition']['candidate_sum'] or
            len(inv24['edges']) > len(inv48['edges'])):
            raise ValueError('GAMER_G2_0018_PARENT_INVENTORY_DRIFT')
        new_edges = set(inv48['edges']) - set(inv24['edges'])
        new_gold = len(new_edges & case['gold'])
        if new_gold != coverage['topk_reachable']['48'] - coverage['topk_reachable']['24']:
            raise ValueError('GAMER_G2_0018_COVERAGE_ACCOUNTING')
        cutoff = math.log(THRESHOLD/(1-THRESHOLD))
        expanded_predicted = source13.decode_scores(inv48, normal48, THRESHOLD)
        tail_predicted = expanded_predicted & new_edges
        return {
            'group': group, 'index': index, 'prompt_sha256': case['prompt_sha256'],
            'family': case['family'], 'density_bucket': parent['density_bucket'],
            'structural_topology': parent['structural_topology'],
            'gold_binding_edges': len(case['gold']),
            'candidate_inventory': {'top24': len(inv24['edges']), 'top48': len(inv48['edges']),
                                    'additional_candidates': len(new_edges),
                                    'additional_reachable_gold': new_gold,
                                    'additional_gold_recovered': len(tail_predicted & case['gold']),
                                    'additional_false_positives': len(tail_predicted-case['gold'])},
            'proxy_pool': {'groups': len(sizes), 'size_sum': sum(sizes),
                           'max_size': max(sizes, default=0)},
            'paired': {'train_geometry_proxy': paired_scores(inv24['edges'], normal24,
                                               proxy, case['gold']),
                       'expanded_context': paired_scores(inv24['edges'], normal24,
                                               core48, case['gold'])},
            'configurations': configurations,
        }


def aggregate(records):
    if not records:
        raise ValueError('GAMER_G2_0018_EMPTY_AGGREGATE')
    stats = Counter()
    proxy = Counter()
    paired = {'train_geometry_proxy': Counter(), 'expanded_context': Counter()}
    for r in records:
        stats.update(r['candidate_inventory'])
        proxy.update({key: value for key, value in r['proxy_pool'].items()
                      if key != 'max_size'})
        proxy['max_size'] = max(proxy['max_size'], r['proxy_pool']['max_size'])
        for arm, vals in r['paired'].items():
            paired[arm].update({key: value for key, value in vals.items()
                                if key != 'absolute_score_drift_max'})
            paired[arm]['absolute_score_drift_max'] = max(
                paired[arm]['absolute_score_drift_max'], vals.get('absolute_score_drift_max', 0.))
    return {'cases': len(records),
            'metrics': {arm: base.metrics([{**r['configurations'][arm], 'family': r['family']}
                                          for r in records]) for arm in ARMS},
            'candidate_inventory': dict(stats), 'proxy_pool': dict(proxy),
            'paired': {name: dict(values) for name, values in paired.items()}}


def run(*, root, g1_validation_report, g2_run, selection_run, discrimination_run,
        relational_run, density_run, consistency_run, context_run, learned_run,
        recall_run, crowded_run, robustness_run, diagnosis_run, source_run,
        specialization_run, routing_robustness_run, general_run, ceiling_run,
        output, device='cuda', max_length=2048):
    """Frozen replay of 0017 with two predeclared label-blind interventions."""
    root, output = Path(root), Path(output)
    if output.exists():
        raise FileExistsError('GAMER_G2_0018_IMMUTABLE_OUTPUT')
    head, manifest, dataset, bootstrap, _, _, hashes = sel.load_parents(
        root, g1_validation_report, g2_run)
    original = base.original_training_rows(dataset, source13.TRAIN_LIMIT)
    if base._sha('\n'.join(base._sha(r['prompt']) for r in original)) != bootstrap.get('train_prompt_hashes_sha256'):
        raise ValueError('GAMER_G2_0018_PARENT_TRAIN_DRIFT')
    history, prior15, augmented, calibration, independent, withheld = ceiling17.regeneration(original)
    if density.sha_rows(history[1]) != bootstrap.get('dev_dataset_sha256'):
        raise ValueError('GAMER_G2_0018_PARENT_BOOTSTRAP_DRIFT')
    parent15, _, parent13 = general16.verify_robust15_parent(
        routing_robustness_run, prior15, source_run, specialization_run)
    if parent15['parent_hashes']['g1_validation_report_sha256'] != hashes['g1_validation_report_sha256']:
        raise ValueError('GAMER_G2_0018_PARENT_G1_LINEAGE')
    general16.verify_frozen_ancestry15(parent15['parent_hashes'],
        g1_validation_report=g1_validation_report, g2_run=g2_run,
        discrimination_run=discrimination_run, relational_run=relational_run,
        density_run=density_run, consistency_run=consistency_run, context_run=context_run,
        learned_run=learned_run, recall_run=recall_run, crowded_run=crowded_run,
        robustness_run=robustness_run, diagnosis_run=diagnosis_run,
        source_run=source_run, specialization_run=specialization_run)
    report16, _, hashes16 = ceiling17.verify_frozen_0016(
        general_run, source_run, routing_robustness_run, augmented,
        original + augmented, calibration, independent, withheld)
    if (report16['parent_hashes']['g2_0014_report_sha256'] !=
            parent15['parent_hashes']['g2_0014_report_sha256'] or
            report16['frozen_source13_sha256'] != parent13['new_weights_sha256']):
        raise ValueError('GAMER_G2_0018_PARENT_0016_LINEAGE')
    rows_by_split = dict(zip(SPLITS, (independent, withheld)))
    parent_records, hashes17 = verify_parent_0017(ceiling_run, rows_by_split, hashes16)
    head = general16.load_frozen_rank4(head, Path(relational_run) / rel.WEIGHTS,
                                       parent15['parent_hashes']['g2_0004_weights_sha256'])
    original_state = {k: v.detach().cpu().clone() for k, v in head.state_dict().items()}
    head = head.to(device).eval().requires_grad_(False)
    model, tokenizer = backbone.model_components(manifest['model_id'],
                                                  manifest['source_adapter'], device)
    if any(p.requires_grad for p in model.parameters()):
        raise ValueError('GAMER_G2_0018_BACKBONE_UNFROZEN')
    captures = [rel.capture(head, model, tokenizer, rows, device=device,
                            max_length=max_length, group=name)
                for name, rows in rows_by_split.items()]
    width = captures[0][0]['vectors'].shape[1]
    if any(case['vectors'].shape[1] != width for batch in captures for case in batch):
        raise ValueError('GAMER_G2_0018_FEATURE_WIDTH_DRIFT')
    general = ceiling17.load_general_scorer(
        Path(general_run) / general16.WEIGHTS, width,
        report16['new_weights_sha256']).to(device)
    allowed = sel.compatibility_from_train(original)
    saved = []
    for name, rows, cases in zip(SPLITS, (independent, withheld), captures):
        for i, (row, case, parent) in enumerate(zip(rows, cases, parent_records[name]), 1):
            saved.append(intervention_case(case, row, parent, head, general, allowed,
                                           device=device, group=name, index=i))
            if i == 1 or i % 40 == 0 or i == len(rows):
                print(f'GAMER_G2_0018_CONTEXT {name} {i}/{len(rows)}', flush=True)
    rel.validate_original_state(head, original_state)
    grouped = {s: [r for r in saved if r['group'] == s] for s in SPLITS}
    report = {
        'schema': SCHEMA,
        'status': 'G2_COMPETITOR_CONTEXT_INTERVENTION_DEV_NOT_CERTIFIED',
        'mode': 'FROZEN_PAIRED_CONTEXT_INTERVENTION',
        'parent_selected': ceiling17.GENERAL,
        'fixed_threshold': THRESHOLD, 'fixed_seed': SEED,
        'frozen_0016_hashes': hashes16, 'parent_hashes': hashes17,
        'datasets_sha256': {name: density.sha_rows(rows) for name, rows in rows_by_split.items()},
        'diagnosis': {name: aggregate(grouped[name]) for name in SPLITS},
        'by_family': {name: {family: aggregate([r for r in grouped[name] if r['family'] == family])
                             for family in ('encounter', 'allocation')
                             if any(r['family'] == family for r in grouped[name])}
                      for name in SPLITS},
        'by_density': {name: {bucket: aggregate([r for r in grouped[name]
                                                 if r['density_bucket'] == bucket])
                              for bucket in sorted({r['density_bucket'] for r in grouped[name]})}
                       for name in SPLITS},
        'by_topology': {top: aggregate([r for r in grouped[SPLITS[1]]
                                      if r['structural_topology'] == top])
                        for top in ('2x4', '4x2', '4x4')},
        'intervention_contract': {
            'baseline': 'original G2-0016 general_75 top-24 replay',
            'proxy': 'target-retaining, label-blind 6 top / 5 far / 5 near / 5 random; NOT gold-conditioned TRAIN replay',
            'expanded': 'top-48 rank-only candidate set; core-only isolates context drift, full includes new coverage',
            'comparison': 'matched feature tensors, same 0.75 threshold, same frozen scorer and G1',
            'training_sampler_is_gold_conditioned': True,
            'exact_train_distribution_reconstruction_claimed': False,
            'paired_scoring_before_max_edges_cap': True,
        },
        'g1_frozen': True, 'g2_0004_rank_frozen': True,
        'g2_0016_general_frozen': True, 'new_trainable_parameters': 0,
        'new_training': False, 'model_selection': False,
        'gold_used_only_for_offline_attribution': True,
        'candidate_gold_injection_disabled': True,
        'previously_exposed_development_only': True,
        'withheld_topologies_dev_only': True,
        'no_hindsight_threshold_selection': True,
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
