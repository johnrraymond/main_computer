"""G2-0010: prospective crowded-recall-safe calibration of FROZEN G2-0009.

G2-0009's already-inspected independent DEV cohort becomes calibration. Its
trained retention and control weights are frozen; no retraining or new decoding
rules occur. Select using overall + crowded recall constraints BEFORE scoring
an additional prompt-disjoint DEV cohort. Never inspect challenge/test labels,
promote to production, or generate edges outside frozen G2-0005.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from pathlib import Path
import json

import torch
from safetensors.torch import load_file

from tools import nanojev_gamer_v1_backbone_first as backbone
from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools import nanojev_gamer_v1_g2_binding_selection as sel
from tools import nanojev_gamer_v1_g2_binding_discrimination as disc
from tools import nanojev_gamer_v1_g2_relational_training as rel
from tools import nanojev_gamer_v1_g2_density_discrimination as density
from tools import nanojev_gamer_v1_g2_graph_consistency as graph6
from tools import nanojev_gamer_v1_g2_contextual_graph as context7
from tools import nanojev_gamer_v1_g2_learned_graph_context as learned8
from tools import nanojev_gamer_v1_g2_recall_preserving_graph as recall9

SCHEMA='nanojev.gamer.graph.g2.crowded-recall-calibration.v1'
REPORT='g2_crowded_recall_calibration_report.json'
CASES='g2_crowded_recall_calibration_per_case.jsonl'
SEED=400010
VALIDATION_LIMIT=320
PARENT=recall9.PARENT
CROWDED='crowded_49_128'
GLOBAL_RECALL_LOSS=.01
CROWDED_RECALL_LOSS=.02


def new_validation_rows(train,first,second,third,fourth,fifth,sixth,seventh,eighth,ninth,
                        *,limit=VALIDATION_LIMIT,seed=SEED):
    cohorts=(train,first,second,third,fourth,fifth,sixth,seventh,eighth,ninth)
    if type(limit) is not int or limit<2 or limit%2 or not all(cohorts):
        raise ValueError('GAMER_G2_0010_BAD_COHORT')
    prior=[row for cohort in cohorts for row in cohort]
    new=base.novel_dev_rows(prior,limit=limit,seed=seed)
    hashes=[base._sha(row['prompt']) for row in new]
    if (len(hashes)!=len(set(hashes)) or
        set(hashes)&{base._sha(row['prompt']) for row in prior} or
        Counter(row['family'] for row in new)!={'encounter':limit//2,'allocation':limit//2}):
        raise ValueError('GAMER_G2_0010_PROMPT_LEAKAGE_OR_IMBALANCE')
    return new


def all_rule_names():
    return [PARENT,*recall9._rule_names(),
            *(f'no_context_w{w:02d}_p{round(t*100):02d}'
              for w in (4,8) for t in recall9.THRESHOLDS)]


def _metrics_from_records(rows,names):
    return recall9._metrics_from_records(rows,names)


def _gates(metrics,buckets,name):
    parent=metrics[PARENT]
    crowd=buckets[CROWDED]
    baseline=crowd.get(PARENT,{})
    crowded_available=baseline.get('cases',0)>0
    matched=name.replace('retain_','no_context_',1)
    m=metrics[name]
    return {
        'f1_improved':m['f1']>parent['f1'],
        'false_positives_reduced':m['false_positive']<parent['false_positive'],
        'global_recall_at_least_060':m['recall']>=.60,
        'global_recall_at_least_parent_minus_001':m['recall']>=parent['recall']-GLOBAL_RECALL_LOSS,
        'crowded_cases_present':crowded_available,
        'crowded_f1_improved':bool(crowded_available and crowd[name]['f1']>baseline['f1']),
        'crowded_recall_at_least_parent_minus_002':bool(crowded_available and
                                    crowd[name]['recall']>=baseline['recall']-CROWDED_RECALL_LOSS),
        'beats_matched_no_context_f1':bool(matched in metrics and m['f1']>metrics[matched]['f1']),
        'no_new_edges_generated':True,
    }


def choose_rule(calibration,buckets):
    """Predeclared gate and deterministic choice; no access to independent cohort."""
    parent=calibration[PARENT]
    crowded=buckets.get(CROWDED,{})
    crowd_parent=crowded.get(PARENT,{})
    floor=max(.60,parent['recall']-GLOBAL_RECALL_LOSS)
    crowd_floor=(crowd_parent['recall']-CROWDED_RECALL_LOSS
                 if crowd_parent.get('cases',0)>0 else None)
    candidates={name:_gates(calibration,buckets,name) for name in recall9._rule_names()}
    eligible=[name for name,gates in candidates.items() if all(gates.values())]
    chosen=(max(eligible,key=lambda name:(calibration[name]['f1'],
            crowded[name]['f1'],calibration[name]['recall'],
            -calibration[name]['false_positive'],name)) if eligible else None)
    return chosen,{'reason':('MAX_CALIBRATION_F1_WITH_GLOBAL_AND_CROWDED_RECALL_BOUNDS'
                            if chosen else 'NO_CROWDED_RECALL_SAFE_RULE'),
                   'eligible_count':len(eligible),
                   'global_recall_floor':floor,'crowded_recall_floor':crowd_floor,
                   'crowded_cases':crowd_parent.get('cases',0)},candidates


def verify_recall9_parent(directory,eighth,ninth,expected_hashes):
    """Recompute G2-0009's saved metrics, density buckets, selection, and lineage."""
    directory=Path(directory)
    report_path=directory/recall9.REPORT
    cases_path=directory/recall9.CASES
    weight_path=directory/recall9.WEIGHTS
    report=json.loads(report_path.read_text(encoding='utf-8'))
    required={
        'schema':recall9.SCHEMA,'g1_development_candidate':base.G1_ARM,
        'g1_head_sha256':base.G1_HEAD_SHA,'g1_frozen':True,
        'g2_0004_rank_weights_frozen':True,'g2_0005_rule_frozen':True,
        'g2_0008_context_weights_frozen':True,'g2_0008_replay_pass':True,
        'g2_0008_calibration_pass':False,'g2_certified':False,
        'g2_production_unlocked':False,'production_promotion':False,
        'certification_authorized':False,'graph_execution_authorized':False,
        'memory_writes':False,'sealed_test_used':False,'challenge_or_test_split_used':False,
        'inference_uses_predicted_g1_only':True,'no_gold_graph_at_inference':True,
        'gold_used_for_training_and_offline_scoring_only':True,
        'no_new_edges_allowed':True,'no_fixed_cardinality':True,
        'selected_independent_screen_pass':False,
        'independent_cases':len(ninth),'independent_seed':recall9.SEED,
        'independent_dataset_sha256':density.sha_rows(ninth),
        'calibration_cases':len(eighth),'calibration_seed':learned8.SEED,
        'calibration_dataset_sha256':density.sha_rows(eighth),
        'thresholds_predeclared':list(recall9.THRESHOLDS),
        'positive_weights_predeclared':list(recall9.TRAIN_WEIGHTS),
    }
    for key,value in required.items():
        if type(report.get(key)) is not type(value) or report[key]!=value:
            raise ValueError('GAMER_G2_0010_G2_0009_CONTRACT:'+key)
    if report.get('parent_hashes')!=expected_hashes:
        raise ValueError('GAMER_G2_0010_G2_0009_LINEAGE')
    if report.get('retention_weights_sha256')!=backbone.sha256(weight_path):
        raise ValueError('GAMER_G2_0010_G2_0009_WEIGHT_HASH')
    weights=load_file(str(weight_path),device='cpu')
    prefixes=('w04.','w08.','no_context_w04.','no_context_w08.')
    if not weights or any(not key.startswith(prefixes) for key in weights):
        raise ValueError('GAMER_G2_0010_G2_0009_WEIGHTS_KEYS')
    for prefix in prefixes:
        selected={key[len(prefix):]:tensor for key,tensor in weights.items()
                  if key.startswith(prefix)}
        if not selected or sum(t.numel() for t in selected.values())!=report.get('trainable_parameters_per_arm'):
            raise ValueError('GAMER_G2_0010_G2_0009_WEIGHTS_COUNT')
        if any(not torch.isfinite(t).all() for t in selected.values()):
            raise ValueError('GAMER_G2_0010_G2_0009_NONFINITE_WEIGHTS')
    rows=[json.loads(line) for line in cases_path.read_text(encoding='utf-8').splitlines() if line.strip()]
    names=all_rule_names()
    if len(rows)!=len(eighth)+len(ninth):
        raise ValueError('GAMER_G2_0010_G2_0009_CASE_COUNT')
    for index,(row,original) in enumerate(zip(rows,eighth+ninth)):
        prior=index<len(eighth)
        if (row.get('group')!=('previously_inspected_calibration' if prior else 'new_independent_validation') or
            row.get('index')!=(index+1 if prior else index-len(eighth)+1) or
            row.get('prompt_sha256')!=base._sha(original['prompt']) or
            row.get('family')!=original['family'] or
            row.get('gold_binding_edges')!=len(base.gold_binding_edges(original)) or
            row.get('density_bucket')!=density._bucket(row['g1_predicted_nodes']) or
            set(row.get('configurations',{}))!=set(names)):
            raise ValueError('GAMER_G2_0010_G2_0009_CASE_IDENTITY')
    for label,subset in (('calibration',rows[:len(eighth)]),('independent',rows[len(eighth):])):
        metrics=_metrics_from_records(subset,names)
        if metrics!=report.get(label+'_metrics'):
            raise ValueError('GAMER_G2_0010_G2_0009_METRICS:'+label)
    chosen,selection=recall9.choose_rule(report['calibration_metrics'])
    if chosen!=report.get('selected_rule') or selection!=report.get('calibration_selection'):
        raise ValueError('GAMER_G2_0010_G2_0009_SELECTION')
    bucket=density.bucket_metrics(rows[len(eighth):],tuple(report['independent_metrics']))
    if bucket!=report.get('independent_by_node_density'):
        raise ValueError('GAMER_G2_0010_G2_0009_BUCKET_METRICS')
    if chosen is not None:
        crowd=bucket[CROWDED]
        parent=report['independent_metrics'][PARENT]
        m=report['independent_metrics'][chosen]
        selected_gates=report['independent_gates_by_rule'][chosen]
        if (selected_gates.get('f1_improved')!=(m['f1']>parent['f1']) or
            selected_gates.get('crowded_recall_at_least_parent_minus_002')!=
              (crowd[chosen]['recall']>=crowd[PARENT]['recall']-.02) or
            report.get('selected_independent_screen_pass')!=bool(
              all(selected_gates.values()) and m['f1']>
              report['independent_metrics'][chosen.replace('retain_','no_context_',1)]['f1'])):
            raise ValueError('GAMER_G2_0010_G2_0009_SCREEN')
    return report,rows[len(eighth):],{
        'g2_0009_report_sha256':backbone.sha256(report_path),
        'g2_0009_cases_sha256':backbone.sha256(cases_path),
        'g2_0009_weights_sha256':backbone.sha256(weight_path),
    }


def load_frozen_retention(path,width,expected_sha):
    if backbone.sha256(path)!=expected_sha:
        raise ValueError('GAMER_G2_0010_G2_0009_WEIGHT_HASH')
    all_weights=load_file(str(path),device='cpu')
    prefixes=('w04.','w08.','no_context_w04.','no_context_w08.')
    nets=[]
    for prefix in prefixes:
        state={key[len(prefix):]:value for key,value in all_weights.items() if key.startswith(prefix)}
        model=recall9.RetentionScorer(width)
        model.load_state_dict(state,strict=True)
        model.eval().requires_grad_(False)
        nets.append(model)
    if set(all_weights)!={prefix+key for prefix,net in zip(prefixes,nets) for key in net.state_dict()}:
        raise ValueError('GAMER_G2_0010_G2_0009_UNEXPECTED_TENSORS')
    return nets


def run(*,root,g1_validation_report,g2_run,selection_run,discrimination_run,relational_run,
        density_run,consistency_run,context_run,learned_run,recall_run,output,
        device='cuda',max_length=2048,validation_limit=VALIDATION_LIMIT):
    root=Path(root);output=Path(output)
    if output.exists():raise FileExistsError('GAMER_G2_0010_IMMUTABLE_OUTPUT')
    head,manifest,dataset,bootstrap,_,_,hashes=sel.load_parents(root,g1_validation_report,g2_run)
    train=base.original_training_rows(dataset,rel.TRAIN_LIMIT)
    if base._sha('\n'.join(base._sha(item['prompt']) for item in train))!=bootstrap.get('train_prompt_hashes_sha256'):
        raise ValueError('GAMER_G2_0010_TRAIN_REPLAY')
    first=base.novel_dev_rows(train,limit=rel.CALIBRATION_LIMIT,seed=base.SEED)
    if density.sha_rows(first)!=bootstrap.get('dev_dataset_sha256'):
        raise ValueError('GAMER_G2_0010_BOOTSTRAP_DEV_REPLAY')
    second=sel.new_validation_rows(train,first,limit=sel.VALIDATION_LIMIT)
    third=disc.new_validation_rows(train,first,second,limit=disc.VALIDATION_LIMIT)
    fourth=rel.new_validation_rows(train,first,second,third,limit=rel.VALIDATION_LIMIT)
    fifth=density.new_validation_rows(train,first,second,third,fourth,limit=density.VALIDATION_LIMIT)
    sixth=graph6.new_validation_rows(train,first,second,third,fourth,fifth,limit=graph6.VALIDATION_LIMIT)
    seventh=context7.new_validation_rows(train,first,second,third,fourth,fifth,sixth,limit=context7.VALIDATION_LIMIT)
    eighth=learned8.new_validation_rows(train,first,second,third,fourth,fifth,sixth,seventh,
                                      limit=learned8.VALIDATION_LIMIT)
    ninth=recall9.new_validation_rows(train,first,second,third,fourth,fifth,sixth,seventh,eighth,
                                    limit=recall9.VALIDATION_LIMIT)
    allowed=sel.compatibility_from_train(train)
    motifs,motif_audit=context7.learn_motifs(train,allowed)
    functional,train_evidence=graph6.learn_constraints(train,allowed)
    selection_dir=Path(selection_run)
    selection=json.loads((selection_dir/sel.REPORT).read_text(encoding='utf-8'))
    if (selection.get('schema')!=sel.SCHEMA or selection.get('g2_parent_hashes')!=hashes or
        selection.get('validation_dataset_sha256')!=density.sha_rows(second) or
        selection.get('compatibility_sha256')!=sel._hash_json([list(x) for x in sorted(allowed)]) or
        selection.get('production_promotion') is not False):
        raise ValueError('GAMER_G2_0010_G2_0002_DRIFT')
    early={**hashes,'g2_0002_report_sha256':backbone.sha256(selection_dir/sel.REPORT),
           'g2_0002_cases_sha256':backbone.sha256(selection_dir/sel.CASES)}
    _,disc_hashes=rel.verify_discrimination_parent(discrimination_run,third,early)
    bootstrap_head=deepcopy(head)
    _,_,fourth_hashes=density.verify_parent(relational_run,fourth,head,
                                            {**hashes,**disc_hashes},allowed)
    fifth_report,_,fifth_hashes=graph6.verify_density_parent(density_run,fifth,
                                           {**hashes,**disc_hashes,**fourth_hashes})
    _,_,sixth_hashes=context7.verify_graph6_parent(consistency_run,sixth,
                       {**hashes,**disc_hashes,**fourth_hashes,**fifth_hashes},
                       sel._hash_json(train_evidence))
    _,_,seventh_hashes=learned8.verify_context7_parent(context_run,seventh,
                       {**hashes,**disc_hashes,**fourth_hashes,**fifth_hashes,**sixth_hashes},
                       sel._hash_json(motif_audit))
    eighth_report,_,eighth_hashes=recall9.verify_learned8_parent(learned_run,eighth,
                       {**hashes,**disc_hashes,**fourth_hashes,**fifth_hashes,**sixth_hashes,
                        **seventh_hashes})
    prior_hashes={**hashes,**disc_hashes,**fourth_hashes,**fifth_hashes,
                  **sixth_hashes,**seventh_hashes,**eighth_hashes}
    parent_report,parent_cases,parent_hashes=verify_recall9_parent(recall_run,eighth,ninth,prior_hashes)
    fresh=new_validation_rows(train,first,second,third,fourth,fifth,sixth,seventh,eighth,ninth,
                              limit=validation_limit)
    rule=fifth_report['fixed_decoder_rules'][graph6.PARENT_ARM]
    original={key:value.detach().cpu().clone() for key,value in head.state_dict().items()}
    head=head.to(device).eval().requires_grad_(False)
    bootstrap_head=bootstrap_head.to(device).eval().requires_grad_(False)
    model,tok=backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    if any(parameter.requires_grad for parameter in model.parameters()):
        raise ValueError('GAMER_G2_0010_BACKBONE_UNFROZEN')
    captured_cal=rel.capture(bootstrap_head,model,tok,ninth,device=device,
                             max_length=max_length,group='g2_0009_replay')
    with torch.inference_mode():
        encoded_cal=learned8.encode_cases(captured_cal,head,allowed,rule,device=device,
                                          group='calibration')
    width=encoded_cal[0]['vectors'].shape[1]
    context,control=recall9.load_frozen_scorers(Path(learned_run)/learned8.WEIGHTS,
                                                 width,eighth_report['context_weights_sha256'])
    calibrated=recall9.encode_retention(encoded_cal,context,control,device=device,
                                       group='calibration')
    w4,w8,nc4,nc8=load_frozen_retention(Path(recall_run)/recall9.WEIGHTS,width,
                                       parent_report['retention_weights_sha256'])
    # Evaluation tensors are placed on --device; frozen scoring networks must match.
    w4,w8,nc4,nc8=(net.to(device).eval().requires_grad_(False) for net in (w4,w8,nc4,nc8))
    replay_metrics,replay_cases=recall9.evaluate(calibrated,w4,w8,nc4,nc8,device=device,
                                                group='new_independent_validation')
    if replay_metrics!=parent_report['independent_metrics'] or len(replay_cases)!=len(parent_cases):
        raise ValueError('GAMER_G2_0010_G2_0009_PREDICTION_REPLAY')
    for current,prior in zip(replay_cases,parent_cases):
        if (current['configurations']!=prior['configurations'] or
            current['prompt_sha256']!=prior['prompt_sha256'] or
            current['g1_predicted_nodes']!=prior['g1_predicted_nodes']):
            raise ValueError('GAMER_G2_0010_G2_0009_CASE_REPLAY')
    buckets=density.bucket_metrics(replay_cases,tuple(replay_metrics))
    selected,selection_info,calibration_gates=choose_rule(replay_metrics,buckets)
    # Only after calibration is irrevocably selected do we touch the fresh cohort.
    captured_fresh=rel.capture(bootstrap_head,model,tok,fresh,device=device,
                               max_length=max_length,group='new_independent')
    with torch.inference_mode():
        encoded_fresh=learned8.encode_cases(captured_fresh,head,allowed,rule,device=device,
                                            group='new_independent')
    fresh_cache=recall9.encode_retention(encoded_fresh,context,control,device=device,
                                        group='new_independent')
    independent,new_cases=recall9.evaluate(fresh_cache,w4,w8,nc4,nc8,device=device,
                                          group='new_independent_validation')
    rel.validate_original_state(head,original)
    independent_buckets=density.bucket_metrics(new_cases,tuple(independent))
    independent_gates=(_gates(independent,independent_buckets,selected) if selected else None)
    screen=bool(independent_gates and all(independent_gates.values()))
    report={
        'schema':SCHEMA,'status':'G2_CROWDED_RECALL_CALIBRATION_DEV_NOT_CERTIFIED',
        'g1_development_candidate':base.G1_ARM,'g1_head_sha256':manifest['head_sha256'],
        'g1_frozen':True,'g2_0004_rank_weights_frozen':True,'g2_0005_rule_frozen':True,
        'g2_0008_context_weights_frozen':True,'g2_0009_retention_weights_frozen':True,
        'g2_0009_prediction_replay_pass':True,'g2_0009_independent_screen_pass':False,
        'new_trainable_parameters':0,'g2_certified':False,'g2_production_unlocked':False,
        'production_promotion':False,'certification_authorized':False,
        'graph_execution_authorized':False,'memory_writes':False,'sealed_test_used':False,
        'challenge_or_test_split_used':False,'inference_uses_predicted_g1_only':True,
        'no_gold_graph_at_inference':True,'gold_used_for_previous_training_and_offline_scoring_only':True,
        'no_new_edges_allowed':True,'no_fixed_cardinality':True,
        'calibration_source':'PREVIOUSLY_INSPECTED_G2_0009_INDEPENDENT_DEV',
        'calibration_cases':len(ninth),'calibration_seed':recall9.SEED,
        'calibration_dataset_sha256':density.sha_rows(ninth),
        'calibration_metrics':replay_metrics,'calibration_by_node_density':buckets,
        'calibration_gates_by_rule':calibration_gates,'calibration_selection':selection_info,
        'selected_rule':selected,
        'independent_cases':len(fresh),'independent_seed':SEED,
        'independent_dataset_sha256':density.sha_rows(fresh),
        'independent_metrics':independent,'independent_by_node_density':independent_buckets,
        'selected_independent_gates':independent_gates,
        'selected_independent_screen_pass':screen,
        'retention_rules_predeclared':recall9._rule_names(),
        'global_recall_loss_limit':GLOBAL_RECALL_LOSS,
        'crowded_recall_loss_limit':CROWDED_RECALL_LOSS,
        'parent_hashes':{**prior_hashes,**parent_hashes},
        'frozen_retention_weights_sha256':parent_report['retention_weights_sha256'],
    }
    output.mkdir(parents=True,exist_ok=False)
    for row in replay_cases:row['group']='previously_inspected_calibration'
    (output/REPORT).write_text(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    (output/CASES).write_bytes(base._jsonl(replay_cases+new_cases))
    return report
