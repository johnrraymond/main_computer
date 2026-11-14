"""G2-0020: frozen G2-0019 precision-guard structural loss attribution.

Compare top-24 versus top-48 candidate inventories without retraining, then
run the *same predeclared* comparisons on newly authored prompt-disjoint DEV
cohorts. The 0019 choice is held fixed before these new prompts are scored.
Gold labels are used exclusively for retrospective metrics, never selection,
feature construction, ranking, thresholds, or predictions. This is NOT a
sealed test, certification, model selection, or production promotion.
"""
from __future__ import annotations

from collections import Counter
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
from tools import nanojev_gamer_v1_g2_source_conditioned_discrimination as source13
from tools import nanojev_gamer_v1_g2_general_relational_discrimination as general16
from tools import nanojev_gamer_v1_g2_architectural_ceiling_diagnosis as ceiling17
from tools import nanojev_gamer_v1_g2_competitor_context_intervention as context18
from tools import nanojev_gamer_v1_g2_inference_realistic_training as train19

SCHEMA = 'nanojev.gamer.graph.g2.structural-ceiling-validation.v1'
REPORT = 'g2_structural_ceiling_validation_report.json'
CASES = 'g2_structural_ceiling_validation_per_case.jsonl'
SEED = 460020
FRESH_INDEPENDENT = 320
FRESH_TOPOLOGY = 128
THRESHOLD = train19.THRESHOLD
TOP_KS = (24, 48)
REFERENCE = 'precision_guard'  # locked by G2-0019 before new prompts are generated
CONFIGS = ('baseline_0016_top24', 'precision_guard_top24',
           'precision_guard_top48_core', 'precision_guard_top48')
EXPOSED = ceiling17.SPLITS
FRESH = ('fresh_independent_dev', 'fresh_topology_dev')
SPLITS = (*EXPOSED, *FRESH)


def _read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines()
            if line.strip()]


def verify_parent_0019(directory, rows_by_split, saved16, hashes, *,
                       expected_train_sha, expected_datasets):
    """Fail closed on G2-0019 weights, lineage, full exposed per-case replay.

    G2-0019 persists calibration FIRST, followed by both exposed DEV splits.
    Replay all three groups against G2-0016 identities and baseline scores.
    """
    directory = Path(directory)
    path = directory / train19.REPORT
    casepath = directory / train19.CASES
    weights = directory / train19.WEIGHTS
    report = json.loads(path.read_text(encoding='utf-8'))
    fixed = {
        'schema': train19.SCHEMA,
        'status': 'G2_INFERENCE_REALISTIC_TRAINING_DEV_NOT_CERTIFIED',
        'parent_0016_selected': ceiling17.GENERAL,
        'candidate_top_k': 24, 'fixed_threshold': THRESHOLD,
        'train_arms': list(train19.ARMS[1:]),
        'train_dataset_sha256': expected_train_sha,
        'parent_hashes': hashes,
        'baseline_0016_frozen': True, 'backbone_frozen': True,
        'g1_frozen': True, 'rank_head_frozen': True,
        'original_training_data_only': True,
        'train_inventory_gold_injection_disabled': True,
        'candidate_and_group_selection_label_blind': True,
        'selection_on_exposed_dev': False, 'checkpoint_selected': False,
        'no_density_specific_inference_router': True,
        'previously_exposed_development_only': True,
        'withheld_topologies_dev_only': True,
        'sealed_test_used': False, 'challenge_or_test_split_used': False,
        'g2_certified': False, 'g2_production_unlocked': False,
        'production_promotion': False, 'graph_execution_authorized': False,
        'certification_authorized': False, 'memory_writes': False,
    }
    for field, expected in fixed.items():
        if type(report.get(field)) is not type(expected) or report[field] != expected:
            raise ValueError('GAMER_G2_0020_PARENT_0019_CONTRACT:' + field)
    if report.get('new_weights_sha256') != backbone.sha256(weights):
        raise ValueError('GAMER_G2_0020_PARENT_0019_WEIGHT_DRIFT')
    if report.get('train', {}).get('candidate_top_k') != 24 or not all((
        report['train'].get('gold_in_candidate_construction') is False,
        report['train'].get('label_blind_full_inference_groups') is True,
        report['train'].get('same_initial_weights_and_training_batches') is True,
        report['train'].get('multi_positive_supervision') is True,
        report['train'].get('train_cases') == 640,
        report['train'].get('steps') == train19.STEPS,
    )):
        raise ValueError('GAMER_G2_0020_PARENT_0019_TRAIN_CONTRACT')
    if report.get('datasets_sha256') != expected_datasets:
        raise ValueError('GAMER_G2_0020_PARENT_0019_DATASET_DRIFT')
    if set(rows_by_split) != set(train19.SPLITS):
        raise ValueError('GAMER_G2_0020_PARENT_0019_SPLIT_CONTRACT')
    records = _read_jsonl(casepath)
    if len(records) != sum(len(rows_by_split[s]) for s in train19.SPLITS):
        raise ValueError('GAMER_G2_0020_PARENT_0019_COUNT')
    grouped = {}
    offset = 0
    for group in train19.SPLITS:
        rows = rows_by_split[group]
        part = records[offset:offset + len(rows)]
        offset += len(rows)
        if len(saved16.get(group, ())) != len(rows):
            raise ValueError('GAMER_G2_0020_PARENT_0016_CASE_COUNT')
        for index, (rec, row, prior) in enumerate(zip(part, rows, saved16[group]), 1):
            if (rec.get('group') != group or rec.get('index') != index or
                rec.get('family') != row['family'] or
                rec.get('structural_topology') != row.get('structural_topology', 'native') or
                rec.get('prompt_sha256') != base._sha(row['prompt']) or
                set(rec.get('configurations', {})) != set(train19.ARMS) or
                rec['configurations']['baseline_0016'] !=
                    prior['configurations'][ceiling17.GENERAL]):
                raise ValueError('GAMER_G2_0020_PARENT_0019_CASE_DRIFT')
        if report.get('evaluation', {}).get(group) != train19.aggregate(part):
            raise ValueError('GAMER_G2_0020_PARENT_0019_METRIC_DRIFT')
        by_family = {family:train19.aggregate([r for r in part if r['family'] == family])
                     for family in ('encounter', 'allocation')
                     if any(r['family'] == family for r in part)}
        by_density = {bucket:train19.aggregate([r for r in part if r['density_bucket'] == bucket])
                      for bucket in sorted({r['density_bucket'] for r in part})}
        if (report.get('by_family', {}).get(group) != by_family or
            report.get('by_density', {}).get(group) != by_density):
            raise ValueError('GAMER_G2_0020_PARENT_0019_SUBGROUP_DRIFT')
        grouped[group] = part
    topology = {top:train19.aggregate([r for r in grouped[EXPOSED[1]]
                                      if r['structural_topology'] == top])
                for top in ('2x4','4x2','4x4')}
    if (report.get('by_topology') != topology or
        report.get('diagnostic_gates') != train19.predeclared_gates(grouped)):
        raise ValueError('GAMER_G2_0020_PARENT_0019_GATE_DRIFT')
    return grouped, {
        'g2_0019_report_sha256': backbone.sha256(path),
        'g2_0019_cases_sha256': backbone.sha256(casepath),
        'g2_0019_weights_sha256': backbone.sha256(weights),
    }


def load_guarded(path, width, expected_sha):
    """Verify the *entire* two-arm checkpoint and load only prelocked guard."""
    path = Path(path)
    if backbone.sha256(path) != expected_sha:
        raise ValueError('GAMER_G2_0020_WEIGHT_HASH_DRIFT')
    state = load_file(str(path),device='cpu')
    scorer = source13.SourceConditionedScorer(width)
    keys = set(scorer.state_dict())
    if set(state) != {arm+'.'+k for arm in train19.ARMS[1:] for k in keys}:
        raise ValueError('GAMER_G2_0020_WEIGHT_KEY_DRIFT')
    for name, tensor in state.items():
        part = name.split('.',1)[1]
        if tensor.shape != scorer.state_dict()[part].shape or not torch.isfinite(tensor).all():
            raise ValueError('GAMER_G2_0020_WEIGHT_SHAPE_DRIFT')
    scorer.load_state_dict({key:state[REFERENCE+'.'+key] for key in keys})
    return scorer.eval().requires_grad_(False)


def fresh_cohorts(prior, *, independent_limit=FRESH_INDEPENDENT,
                  topology_limit=FRESH_TOPOLOGY):
    """New DEV cohorts, disjoint from all G2-0001..0019 reconstructed prompts.

    Selection seed, case counts, topology mix and thresholds are hard-coded
    before any scores on these prompts can be examined. NOT a sealed holdout.
    """
    if independent_limit != FRESH_INDEPENDENT or topology_limit != FRESH_TOPOLOGY:
        raise ValueError('GAMER_G2_0020_FIXED_FRESH_PROTOCOL')
    old = tuple(prior)
    fresh = general16.independent_rows((old,),limit=independent_limit,seed=SEED)
    old_hashes = {base._sha(r['prompt']) for r in old}
    fresh_hashes = [base._sha(r['prompt']) for r in fresh]
    if len(fresh_hashes) != len(set(fresh_hashes)) or old_hashes.intersection(fresh_hashes):
        raise ValueError('GAMER_G2_0020_FRESH_PROMPT_OVERLAP')
    topology = general16.heldout_topology_rows((old,fresh),
                          limit=topology_limit,seed=SEED+1)
    batches = {'prior':old,FRESH[0]:fresh,FRESH[1]:topology}
    hashes = [base._sha(row['prompt']) for batch in batches.values() for row in batch]
    if len(hashes) != len(set(hashes)):
        raise ValueError('GAMER_G2_0020_FRESH_PROMPT_OVERLAP')
    if (len(fresh) != independent_limit or len(topology) != topology_limit or
        {r['structural_topology'] for r in topology} != {'4x4','4x2','2x4'} or
        any(r['split'] != 'dev' for r in (*fresh,*topology))):
        raise ValueError('GAMER_G2_0020_FRESH_AUTHOR_CONTRACT')
    return {FRESH[0]:fresh,FRESH[1]:topology}


def stage_attribution(gold, anchors, inv24, inv48, logits24, logits48,
                      predicted24, predicted48, *, cutoff):
    """Partition gold losses and FP sources, including the MAX_EDGES cap."""
    e24, e48 = set(inv24['edges']), set(inv48['edges'])
    if len(e24) != len(inv24['edges']) or len(e48) != len(inv48['edges']) or not e24 <= e48:
        raise ValueError('GAMER_G2_0020_BAD_CANDIDATE_NESTING')
    if logits24.shape != (len(e24),) or logits48.shape != (len(e48),):
        raise ValueError('GAMER_G2_0020_BAD_SCORE_SHAPE')
    above24 = {edge for edge,score in zip(inv24['edges'],logits24) if float(score) >= cutoff}
    above48 = {edge for edge,score in zip(inv48['edges'],logits48) if float(score) >= cutoff}
    if (not predicted24 <= above24 or not predicted48 <= above48 or
        not predicted24 <= e24 or not predicted48 <= e48):
        raise ValueError('GAMER_G2_0020_DECODER_NOT_SUBSET')
    endpoints = set(anchors)
    g1 = {edge for edge in gold if edge[0] in endpoints and edge[1] in endpoints}
    in24, in48 = gold & e24, gold & e48
    stages = {
        'gold_total':len(gold),
        'g1_missing_endpoint':len(gold-g1),
        'outside_top48_after_g1':len(g1-in48),
        'top24_excluded_but_top48_reachable':len(in48-in24),
        'top24_reachable_gold':len(in24),
        'top48_reachable_gold':len(in48),
        'top24_threshold_rejected_gold':len(in24-above24),
        'top24_decoder_cap_rejected_gold':len((in24&above24)-predicted24),
        'top48_threshold_rejected_gold':len(in48-above48),
        'top48_decoder_cap_rejected_gold':len((in48&above48)-predicted48),
        'top24_true_positive':len(predicted24&gold),
        'top48_true_positive':len(predicted48&gold),
        'top24_false_positive':len(predicted24-gold),
        'top48_false_positive':len(predicted48-gold),
        'new_top48_true_positive':len((predicted48-e24)&gold),
        'new_top48_false_positive':len((predicted48-e24)-gold),
        'core_true_positive_lost':len((predicted24-predicted48)&gold),
        'core_false_positive_lost':len((predicted24-predicted48)-gold),
    }
    # The total outside top48 includes G1-present incompatible edges, plus
    # candidate-rank exclusions; a separate ceiling function disambiguates.
    if (stages['g1_missing_endpoint']+stages['outside_top48_after_g1']+
        stages['top48_reachable_gold'] != stages['gold_total'] or
        stages['top24_threshold_rejected_gold']+stages['top24_decoder_cap_rejected_gold']+
        stages['top24_true_positive'] != stages['top24_reachable_gold'] or
        stages['top48_threshold_rejected_gold']+stages['top48_decoder_cap_rejected_gold']+
        stages['top48_true_positive'] != stages['top48_reachable_gold']):
        raise ValueError('GAMER_G2_0020_STAGE_ACCOUNTING')
    return stages


def analyze_case(case, row, head, baseline, guard, allowed, *, split, index,
                 device, previous=None):
    """Paired evaluation; every prediction remains gold-blind and frozen."""
    if (case.get('prompt_sha256') != base._sha(row['prompt']) or
        case['family'] != row['family'] or
        (previous is not None and (previous.get('prompt_sha256') != case['prompt_sha256'] or
         previous.get('group') != split or previous.get('index') != index))):
        raise ValueError('GAMER_G2_0020_CASE_IDENTITY_DRIFT')
    with torch.inference_mode():
        rank = rel.full_logits(head,case['vectors'].to(device)).detach().cpu()
        inv24 = source13.make_inventory(case,rank,allowed,top_k=24,train_gold=False)
        inv48 = source13.make_inventory(case,rank,allowed,top_k=48,train_gold=False)
        idx48 = {edge:i for i,edge in enumerate(inv48['edges'])}
        if len(idx48) != len(inv48['edges']) or len(set(inv24['edges'])) != len(inv24['edges']):
            raise ValueError('GAMER_G2_0020_DUPLICATE_CANDIDATE')
        if not set(inv24['edges']) <= set(idx48):
            raise ValueError('GAMER_G2_0020_NONNESTED_INVENTORY')
        loc = [idx48[e] for e in inv24['edges']]
        a = baseline(inv24['features'].to(device),inv24['base'].to(device),
                     inv24['groups'],with_competition=True).detach().cpu()
        p24 = guard(inv24['features'].to(device),inv24['base'].to(device),
                    inv24['groups'],with_competition=True).detach().cpu()
        p48 = guard(inv48['features'].to(device),inv48['base'].to(device),
                    inv48['groups'],with_competition=True).detach().cpu()
        # Verify that the common candidate feature vectors are identical.
        if (not torch.equal(inv24['features'],inv48['features'][loc]) or
            not torch.equal(inv24['base'],inv48['base'][loc])):
            raise ValueError('GAMER_G2_0020_CORE_FEATURE_DRIFT')
        core = p48[loc]
        predicted = {
            CONFIGS[0]:source13.decode_scores(inv24,a,THRESHOLD),
            CONFIGS[1]:source13.decode_scores(inv24,p24,THRESHOLD),
            CONFIGS[2]:source13.decode_scores(inv24,core,THRESHOLD),
            CONFIGS[3]:source13.decode_scores(inv48,p48,THRESHOLD),
        }
        scores = {k:sel.score_set(edges,case['gold']) for k,edges in predicted.items()}
    if previous is not None and (scores[CONFIGS[0]] != previous['configurations']['baseline_0016']
                                  or scores[CONFIGS[1]] != previous['configurations'][REFERENCE]):
        raise ValueError('GAMER_G2_0020_PARENT_0019_REPLAY_DRIFT')
    stage = stage_attribution(case['gold'],case['anchors'],inv24,inv48,p24,p48,
                             predicted[CONFIGS[1]],predicted[CONFIGS[3]],
                             cutoff=math.log(THRESHOLD/(1-THRESHOLD)))
    oracle = ceiling17.candidate_ceiling(case['anchors'],rank,case['gold'],allowed)
    if (stage['g1_missing_endpoint'] != oracle['gold_total']-oracle['g1_endpoint_reachable'] or
        stage['top24_reachable_gold'] != oracle['topk_reachable']['24'] or
        stage['top48_reachable_gold'] != oracle['topk_reachable']['48']):
        raise ValueError('GAMER_G2_0020_CEILING_INCONSISTENCY')
    stage['g1_present_but_incompatible_or_self'] = oracle['compatibility_or_self_filtered']
    stage['compatible_rank_beyond_top48'] = oracle['compatible_reachable']-oracle['topk_reachable']['48']
    if stage['outside_top48_after_g1'] != (stage['g1_present_but_incompatible_or_self']+
                                         stage['compatible_rank_beyond_top48']):
        raise ValueError('GAMER_G2_0020_CEILING_PARTITION')
    drift = context18.paired_scores(inv24['edges'],p24,core,case['gold'],threshold=THRESHOLD)
    return {'group':split,'index':index,'family':case['family'],
            'structural_topology':row.get('structural_topology','native'),
            'density_bucket':density._bucket(len(case['anchors'])),
            'prompt_sha256':case['prompt_sha256'],
            'configurations':scores,
            'candidate_inventory':{'top24':len(inv24['edges']),'top48':len(inv48['edges']),
                                   'added':len(inv48['edges'])-len(inv24['edges'])},
            'stage_losses':stage, 'context_drift':drift}


def aggregate(records):
    if not records:
        raise ValueError('GAMER_G2_0020_EMPTY_AGGREGATION')
    metrics = {k:base.metrics([{**r['configurations'][k],'family':r['family']} for r in records])
               for k in CONFIGS}
    stage = Counter()
    drift = Counter()
    inventories = Counter()
    for row in records:
        stage.update(row['stage_losses'])
        inventories.update(row['candidate_inventory'])
        for key,val in row['context_drift'].items():
            if key != 'absolute_score_drift_max':drift[key] += val
            else:drift[key] = max(drift[key],val)
    return {'cases':len(records),'metrics':metrics,'stage_losses':dict(stage),
            'candidate_inventory':dict(inventories),'paired_score_drift':dict(drift)}


def run(*,root,g1_validation_report,g2_run,selection_run,discrimination_run,
        relational_run,density_run,consistency_run,context_run,learned_run,
        recall_run,crowded_run,robustness_run,diagnosis_run,source_run,
        specialization_run,routing_robustness_run,general_run,ceiling_run,
        context18_run,train19_run,output,device='cuda',max_length=2048):
    root,output = Path(root),Path(output)
    if output.exists():raise FileExistsError('GAMER_G2_0020_IMMUTABLE_OUTPUT')
    head,manifest,dataset,bootstrap,_,_,hashes = sel.load_parents(root,g1_validation_report,g2_run)
    original = base.original_training_rows(dataset,source13.TRAIN_LIMIT)
    if base._sha('\n'.join(base._sha(r['prompt']) for r in original)) != bootstrap.get('train_prompt_hashes_sha256'):
        raise ValueError('GAMER_G2_0020_PARENT_TRAIN_DRIFT')
    history,prior15,augmented,calibration,independent,withheld = ceiling17.regeneration(original)
    if density.sha_rows(history[1]) != bootstrap.get('dev_dataset_sha256'):
        raise ValueError('GAMER_G2_0020_PARENT_BOOTSTRAP_DRIFT')
    parent15,_,parent13 = general16.verify_robust15_parent(
        routing_robustness_run,prior15,source_run,specialization_run)
    if parent15['parent_hashes']['g1_validation_report_sha256'] != hashes['g1_validation_report_sha256']:
        raise ValueError('GAMER_G2_0020_PARENT_G1_LINEAGE')
    general16.verify_frozen_ancestry15(parent15['parent_hashes'],
        g1_validation_report=g1_validation_report,g2_run=g2_run,
        discrimination_run=discrimination_run,relational_run=relational_run,
        density_run=density_run,consistency_run=consistency_run,context_run=context_run,
        learned_run=learned_run,recall_run=recall_run,crowded_run=crowded_run,
        robustness_run=robustness_run,diagnosis_run=diagnosis_run,
        source_run=source_run,specialization_run=specialization_run)
    training = original+augmented
    report16,records16,hashes16 = ceiling17.verify_frozen_0016(
        general_run,source_run,routing_robustness_run,augmented,training,
        calibration,independent,withheld)
    if (report16['parent_hashes']['g2_0014_report_sha256'] !=
        parent15['parent_hashes']['g2_0014_report_sha256'] or
        report16['frozen_source13_sha256'] != parent13['new_weights_sha256']):
        raise ValueError('GAMER_G2_0020_PARENT_0016_LINEAGE')
    by_exposed = dict(zip(EXPOSED,(independent,withheld)))
    rec17,hash17 = context18.verify_parent_0017(ceiling_run,by_exposed,hashes16)
    hash18 = train19.verify_parent_0018(context18_run,by_exposed,hashes16,hash17,rec17)
    records19,hash19 = verify_parent_0019(
        train19_run,{'calibration':calibration,**by_exposed},records16,
        {**hashes16,**hash17,**hash18},
        expected_train_sha=density.sha_rows(training),
        expected_datasets={group:density.sha_rows(rows) for group,rows in (
            ('calibration',calibration),*by_exposed.items())})
    # PRELOCK: no decisions informed by either new cohort. Previously exposed
    # G2-0019 data selected the experimental reference; fresh data cannot.
    prior = [row for batch in (*history,augmented,calibration,independent,withheld)
             for row in batch]
    fresh = fresh_cohorts(prior)
    by_split = {**by_exposed,**fresh}
    all_hashes = [base._sha(row['prompt']) for row in (*prior,*fresh[FRESH[0]],*fresh[FRESH[1]])]
    if len(all_hashes) != len(set(all_hashes)):
        raise ValueError('GAMER_G2_0020_FRESH_OVERLAP')
    head = general16.load_frozen_rank4(head,Path(relational_run)/rel.WEIGHTS,
                                      parent15['parent_hashes']['g2_0004_weights_sha256'])
    head_before = {k:v.detach().cpu().clone() for k,v in head.state_dict().items()}
    head = head.to(device).eval().requires_grad_(False)
    model,tokenizer = backbone.model_components(manifest['model_id'],
                                               manifest['source_adapter'],device)
    if any(p.requires_grad for p in model.parameters()):
        raise ValueError('GAMER_G2_0020_BACKBONE_UNFROZEN')
    baseline = None
    guard = None
    allowed = sel.compatibility_from_train(original)
    grouped = {}
    for split, rows in by_split.items():
        cases = rel.capture(head,model,tokenizer,rows,device=device,
                            max_length=max_length,group=split)
        if not cases:raise ValueError('GAMER_G2_0020_EMPTY_CAPTURE')
        if baseline is None:
            width=cases[0]['vectors'].shape[1]
            baseline = ceiling17.load_general_scorer(Path(general_run)/general16.WEIGHTS,
                                      width,report16['new_weights_sha256']).to(device)
            guard = load_guarded(Path(train19_run)/train19.WEIGHTS,width,
                                 json.loads((Path(train19_run)/train19.REPORT).read_text(encoding='utf-8'))['new_weights_sha256']).to(device)
            baseline_before={k:v.detach().cpu().clone() for k,v in baseline.state_dict().items()}
            guard_before={k:v.detach().cpu().clone() for k,v in guard.state_dict().items()}
        saved=[]
        prior_records=records19.get(split)
        for index,(case,row) in enumerate(zip(cases,rows),1):
            if case['vectors'].shape[1]!=width:raise ValueError('GAMER_G2_0020_WIDTH_DRIFT')
            saved.append(analyze_case(case,row,head,baseline,guard,allowed,split=split,
                                      index=index,device=device,
                                      previous=prior_records[index-1] if prior_records else None))
            if index==1 or index%40==0 or index==len(rows):
                print(f'GAMER_G2_0020_DIAGNOSE {split} {index}/{len(rows)}',flush=True)
        if len(saved)!=len(rows):raise ValueError('GAMER_G2_0020_CASE_COUNT')
        grouped[split]=saved
    rel.validate_original_state(head,head_before)
    for net,before in ((baseline,baseline_before),(guard,guard_before)):
        if any(not torch.equal(net.state_dict()[k].detach().cpu(),val) for k,val in before.items()):
            raise ValueError('GAMER_G2_0020_FROZEN_WEIGHTS_CHANGED')
    report = {
        'schema':SCHEMA,'status':'G2_STRUCTURAL_CEILING_DEV_NOT_CERTIFIED',
        'reference':REFERENCE,'reference_prelocked_before_fresh_evaluation':True,
        'reference_selection_basis':'G2-0019 previously exposed development',
        'fixed_threshold':THRESHOLD,'top_k_inventory':[24,48],
        'seed':SEED,'fresh_counts':{k:len(fresh[k]) for k in FRESH},
        'parent_hashes':{**hashes16,**hash17,**hash18,**hash19},
        'datasets_sha256':{group:density.sha_rows(rows) for group,rows in by_split.items()},
        'fresh_prompt_sha256':{group:[base._sha(row['prompt']) for row in fresh[group]]
                               for group in FRESH},
        'evaluation':{group:aggregate(rows) for group,rows in grouped.items()},
        'by_family':{group:{family:aggregate([r for r in rows if r['family']==family])
                            for family in ('encounter','allocation')
                            if any(r['family']==family for r in rows)}
                     for group,rows in grouped.items()},
        'by_density':{group:{bucket:aggregate([r for r in rows if r['density_bucket']==bucket])
                             for bucket in sorted({r['density_bucket'] for r in rows})}
                      for group,rows in grouped.items()},
        'by_topology':{group:{top:aggregate([r for r in grouped[group]
                                           if r['structural_topology']==top])
                               for top in ('2x4','4x2','4x4')}
                       for group in (EXPOSED[1],FRESH[1])},
        'frozen_g1':True,'frozen_rank_head':True,'frozen_baseline_0016':True,
        'frozen_precision_guard_0019':True,'new_training':False,
        'new_trainable_parameters':0,'model_selection':False,
        'threshold_selection':False,'candidate_selection_uses_gold':False,
        'gold_used_offline_only':True,'fresh_splits_are_dev_not_sealed':True,
        'fresh_prompts_disjoint_from_reconstructed_history':True,
        'fresh_results_not_used_for_selection':True,
        'previously_exposed_comparisons_dev_only':True,
        'cross_domain_generality_not_claimed':True,
        'sealed_test_used':False,'challenge_or_test_split_used':False,
        'certification_authorized':False,'g2_certified':False,
        'g2_production_unlocked':False,'production_promotion':False,
        'graph_execution_authorized':False,'memory_writes':False,
    }
    output.mkdir(parents=True,exist_ok=False)
    (output/CASES).write_bytes(base._jsonl([r for split in SPLITS for r in grouped[split]]))
    report['cases_sha256']=backbone.sha256(output/CASES)
    (output/REPORT).write_text(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    return report
