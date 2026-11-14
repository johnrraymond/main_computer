"""G2-0011: multi-cohort robustness audit of the FROZEN G2-0010 selection.

The exact G2-0010 chosen rule is locked by its already-inspected, hash-checked
report before any new prompt is evaluated. Evaluate four independent balanced
DEV cohorts, including crowded-graph strata, plus a prompt-paired bootstrap.
No new weights, thresholds, model selection, challenge/test access, promotion,
or external effects. Weak or contradictory evidence fails closed.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
import json
import random

import torch

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
from tools import nanojev_gamer_v1_g2_crowded_recall_calibration as crowd10

SCHEMA='nanojev.gamer.graph.g2.robustness-validation.v1'
REPORT='g2_robustness_validation_report.json'
CASES='g2_robustness_validation_per_case.jsonl'
SEED=410011
COHORTS=4
COHORT_LIMIT=320
BOOTSTRAPS=2000
MIN_CROWDED_PER_COHORT=30
SELECTED='retain_w08_p40'
CONTROL='no_context_w08_p40'
PARENT=crowd10.PARENT
NAMES=(PARENT,SELECTED,CONTROL)


def new_cohorts(*predecessors, cohort_count=COHORTS, limit=COHORT_LIMIT, seed=SEED):
    """Predeclare disjoint dev prompts across all historical and new cohorts."""
    if (len(predecessors)!=11 or not all(predecessors) or type(cohort_count) is not int or
        cohort_count<2 or type(limit) is not int or limit<2 or limit%2 or
        type(seed) is not int):
        raise ValueError('GAMER_G2_0011_BAD_COHORT_CONFIG')
    previous=[row for cohort in predecessors for row in cohort]
    seen={base._sha(row['prompt']) for row in previous}
    cohorts=[]
    for i in range(cohort_count):
        fresh=base.novel_dev_rows(previous,limit=limit,seed=seed+104729*i)
        hashes=[base._sha(row['prompt']) for row in fresh]
        if (len(set(hashes))!=len(hashes) or set(hashes)&seen or
            Counter(row['family'] for row in fresh)!=
                {'encounter':limit//2,'allocation':limit//2}):
            raise ValueError('GAMER_G2_0011_COHORT_LEAKAGE')
        previous.extend(fresh);seen.update(hashes);cohorts.append(fresh)
    return cohorts


def _verify_records(rows,originals,expected_group,names):
    if len(rows)!=len(originals):
        raise ValueError('GAMER_G2_0011_PARENT_RECORD_COUNT')
    for i,(row,source) in enumerate(zip(rows,originals),1):
        if (row.get('group')!=expected_group or row.get('index')!=i or
            row.get('prompt_sha256')!=base._sha(source['prompt']) or
            row.get('family')!=source['family'] or
            row.get('gold_binding_edges')!=len(base.gold_binding_edges(source)) or
            row.get('density_bucket')!=density._bucket(row['g1_predicted_nodes']) or
            type(row.get('parent_edges')) is not int or row['parent_edges']<0 or
            set(row.get('configurations',{}))!=set(names)):
            raise ValueError('GAMER_G2_0011_PARENT_CASE_IDENTITY')
        for name,result in row['configurations'].items():
            if (any(type(result.get(k)) is not int or result[k]<0 for k in
                    ('correct','missing','false_positive')) or
                type(result.get('exact')) is not bool or
                result['correct']+result['missing']!=row['gold_binding_edges'] or
                result['correct']+result['false_positive']>row['parent_edges'] or
                result['exact']!=(result['missing']==result['false_positive']==0)):
                raise ValueError('GAMER_G2_0011_PARENT_CASE_COUNTS:'+name)
        if row['configurations'][PARENT]['correct']+row['configurations'][PARENT]['false_positive']!=row['parent_edges']:
            raise ValueError('GAMER_G2_0011_PARENT_EDGE_SET')


def verify_crowd10_parent(directory,ninth,tenth,expected_hashes):
    """Verify G2-0010 lineage, per-case counts, preselection, and screen."""
    directory=Path(directory)
    report_path=directory/crowd10.REPORT
    cases_path=directory/crowd10.CASES
    report=json.loads(report_path.read_text(encoding='utf-8'))
    required={
        'schema':crowd10.SCHEMA,'status':'G2_CROWDED_RECALL_CALIBRATION_DEV_NOT_CERTIFIED',
        'selected_rule':SELECTED,'selected_independent_screen_pass':True,
        'g1_frozen':True,'g2_0004_rank_weights_frozen':True,'g2_0005_rule_frozen':True,
        'g2_0008_context_weights_frozen':True,'g2_0009_retention_weights_frozen':True,
        'g2_0009_prediction_replay_pass':True,'g2_0009_independent_screen_pass':False,
        'new_trainable_parameters':0,'g2_certified':False,'g2_production_unlocked':False,
        'production_promotion':False,'certification_authorized':False,
        'graph_execution_authorized':False,'memory_writes':False,'sealed_test_used':False,
        'challenge_or_test_split_used':False,'inference_uses_predicted_g1_only':True,
        'no_gold_graph_at_inference':True,'no_new_edges_allowed':True,
        'no_fixed_cardinality':True,'independent_cases':len(tenth),
        'independent_seed':crowd10.SEED,'independent_dataset_sha256':density.sha_rows(tenth),
        'calibration_seed':recall9.SEED,'calibration_cases':len(ninth),
        'calibration_dataset_sha256':density.sha_rows(ninth),
        'calibration_source':'PREVIOUSLY_INSPECTED_G2_0009_INDEPENDENT_DEV',
        'g1_development_candidate':base.G1_ARM,'g1_head_sha256':base.G1_HEAD_SHA,
        'global_recall_loss_limit':crowd10.GLOBAL_RECALL_LOSS,
        'crowded_recall_loss_limit':crowd10.CROWDED_RECALL_LOSS,
    }
    for key,expected in required.items():
        if type(report.get(key)) is not type(expected) or report[key]!=expected:
            raise ValueError('GAMER_G2_0011_G2_0010_CONTRACT:'+key)
    if (report.get('parent_hashes')!=expected_hashes or
        report.get('frozen_retention_weights_sha256')!=expected_hashes['g2_0009_weights_sha256']):
        raise ValueError('GAMER_G2_0011_G2_0010_LINEAGE')
    prior_cal,source=report.get('calibration_metrics',{}),report.get('calibration_by_node_density',{})
    chosen,selection,gates=crowd10.choose_rule(prior_cal,source)
    if (chosen!=SELECTED or selection!=report.get('calibration_selection') or
        gates!=report.get('calibration_gates_by_rule')):
        raise ValueError('GAMER_G2_0011_G2_0010_PRESELECTION')
    records=[json.loads(line) for line in cases_path.read_text(encoding='utf-8').splitlines()
             if line.strip()]
    n=report.get('calibration_cases')
    if type(n) is not int or n<1 or len(records)!=n+len(tenth):
        raise ValueError('GAMER_G2_0011_G2_0010_CASE_COUNT')
    names=crowd10.all_rule_names()
    for group,rows,expected_metrics,expected_buckets in (
        ('previously_inspected_calibration',records[:n],report['calibration_metrics'],
         report['calibration_by_node_density']),
        ('new_independent_validation',records[n:],report['independent_metrics'],
         report['independent_by_node_density'])):
        if len(rows)!=(n if group=='previously_inspected_calibration' else len(tenth)):
            raise ValueError('GAMER_G2_0011_G2_0010_CASE_COUNT')
        for i,row in enumerate(rows,1):
            if (row.get('group')!=group or row.get('index')!=i or
                set(row.get('configurations',{}))!=set(names)):
                raise ValueError('GAMER_G2_0011_G2_0010_CASE_STRUCTURE')
        recomputed=crowd10._metrics_from_records(rows,names)
        if (recomputed!=expected_metrics or
            density.bucket_metrics(rows,tuple(recomputed))!=expected_buckets):
            raise ValueError('GAMER_G2_0011_G2_0010_CASE_METRICS')
    _verify_records(records[:n],ninth,'previously_inspected_calibration',names)
    _verify_records(records[n:],tenth,'new_independent_validation',names)
    selected_gates=crowd10._gates(report['independent_metrics'],
                                  report['independent_by_node_density'],SELECTED)
    if (selected_gates!=report.get('selected_independent_gates') or
        not all(selected_gates.values())):
        raise ValueError('GAMER_G2_0011_G2_0010_INDEPENDENT_SCREEN')
    return report,{
        'g2_0010_report_sha256':backbone.sha256(report_path),
        'g2_0010_cases_sha256':backbone.sha256(cases_path),
    }


def _case_f1(cases,name):
    correct=sum(row['configurations'][name]['correct'] for row in cases)
    missing=sum(row['configurations'][name]['missing'] for row in cases)
    extra=sum(row['configurations'][name]['false_positive'] for row in cases)
    return 2*correct/max(1,2*correct+missing+extra)


def paired_bootstrap(rows,*,draws=BOOTSTRAPS,seed=SEED):
    """Prompt-paired, cohort/family-stratified percentile interval for F1 gains."""
    if (not rows or type(draws) is not int or draws<50):
        raise ValueError('GAMER_G2_0011_BAD_BOOTSTRAP')
    buckets=defaultdict(list)
    for row in rows:
        buckets[(row['cohort'],row['family'])].append(row)
    if any(not group for group in buckets.values()):
        raise ValueError('GAMER_G2_0011_BOOTSTRAP_EMPTY_STRATUM')
    rng=random.Random(seed)
    differences=[]
    parts=[(group,len(group)) for group in (buckets[k] for k in sorted(buckets))]
    for _ in range(draws):
        sample=[group[rng.randrange(size)] for group,size in parts for _ in range(size)]
        differences.append(_case_f1(sample,SELECTED)-_case_f1(sample,PARENT))
    differences.sort()
    lo=differences[int(.025*(draws-1))]
    hi=differences[int(.975*(draws-1))]
    return {'method':'PAIRED_PROMPT_BOOTSTRAP_STRATIFIED_BY_COHORT_AND_FAMILY',
            'draws':draws,'seed':seed,'f1_delta':_case_f1(rows,SELECTED)-_case_f1(rows,PARENT),
            'lower_95':lo,'upper_95':hi,'fraction_positive':sum(d>0 for d in differences)/draws}


def screen(records,*,min_crowded=MIN_CROWDED_PER_COHORT,bootstrap_draws=BOOTSTRAPS):
    """Never select a new rule; audit the prior locked configuration only."""
    if not records or type(min_crowded) is not int or min_crowded<1:
        raise ValueError('GAMER_G2_0011_BAD_SCREEN')
    groups=defaultdict(list)
    for row in records:groups[row['cohort']].append(row)
    by_cohort={}
    for cohort,rows in sorted(groups.items()):
        metrics=crowd10._metrics_from_records(rows,NAMES)
        density_buckets=density.bucket_metrics(rows,NAMES)
        gates=crowd10._gates(metrics,density_buckets,SELECTED)
        count=density_buckets[crowd10.CROWDED].get(PARENT,{}).get('cases',0)
        gates['minimum_30_crowded_cases']=count>=min_crowded
        by_cohort[cohort]={'cases':len(rows),'metrics':metrics,'by_node_density':density_buckets,
                          'gates':gates,'pass':all(gates.values())}
    overall=crowd10._metrics_from_records(records,NAMES)
    overall_buckets=density.bucket_metrics(records,NAMES)
    overall_gates=crowd10._gates(overall,overall_buckets,SELECTED)
    uncertainty=paired_bootstrap(records,draws=bootstrap_draws)
    final_gates={
        'all_cohorts_pass_individually':all(data['pass'] for data in by_cohort.values()),
        'all_overall_gates':all(overall_gates.values()),
        'aggregate_paired_f1_ci_lower_above_zero':uncertainty['lower_95']>0,
        'minimum_four_cohorts':len(by_cohort)>=COHORTS,
    }
    return {'by_cohort':by_cohort,'aggregate_metrics':overall,
            'aggregate_by_node_density':overall_buckets,'aggregate_gates':overall_gates,
            'paired_bootstrap':uncertainty,'robustness_gates':final_gates,
            'robustness_pass':all(final_gates.values())}


def run(*,root,g1_validation_report,g2_run,selection_run,discrimination_run,relational_run,
        density_run,consistency_run,context_run,learned_run,recall_run,crowded_run,output,
        device='cuda',max_length=2048,cohort_limit=COHORT_LIMIT,cohort_count=COHORTS,
        bootstrap_draws=BOOTSTRAPS):
    root=Path(root);output=Path(output)
    if output.exists():raise FileExistsError('GAMER_G2_0011_IMMUTABLE_OUTPUT')
    head,manifest,dataset,bootstrap,_,_,hashes=sel.load_parents(root,g1_validation_report,g2_run)
    train=base.original_training_rows(dataset,rel.TRAIN_LIMIT)
    if base._sha('\n'.join(base._sha(item['prompt']) for item in train))!=bootstrap.get('train_prompt_hashes_sha256'):
        raise ValueError('GAMER_G2_0011_TRAIN_REPLAY')
    first=base.novel_dev_rows(train,limit=rel.CALIBRATION_LIMIT,seed=base.SEED)
    if density.sha_rows(first)!=bootstrap.get('dev_dataset_sha256'):
        raise ValueError('GAMER_G2_0011_BOOTSTRAP_DEV_REPLAY')
    second=sel.new_validation_rows(train,first,limit=sel.VALIDATION_LIMIT)
    third=disc.new_validation_rows(train,first,second,limit=disc.VALIDATION_LIMIT)
    fourth=rel.new_validation_rows(train,first,second,third,limit=rel.VALIDATION_LIMIT)
    fifth=density.new_validation_rows(train,first,second,third,fourth,limit=density.VALIDATION_LIMIT)
    sixth=graph6.new_validation_rows(train,first,second,third,fourth,fifth,limit=graph6.VALIDATION_LIMIT)
    seventh=context7.new_validation_rows(train,first,second,third,fourth,fifth,sixth,limit=context7.VALIDATION_LIMIT)
    eighth=learned8.new_validation_rows(train,first,second,third,fourth,fifth,sixth,seventh,limit=learned8.VALIDATION_LIMIT)
    ninth=recall9.new_validation_rows(train,first,second,third,fourth,fifth,sixth,seventh,eighth,limit=recall9.VALIDATION_LIMIT)
    tenth=crowd10.new_validation_rows(train,first,second,third,fourth,fifth,sixth,seventh,eighth,ninth,
                                     limit=crowd10.VALIDATION_LIMIT)
    allowed=sel.compatibility_from_train(train)
    motifs,motif_audit=context7.learn_motifs(train,allowed)
    functional,train_evidence=graph6.learn_constraints(train,allowed)
    selection=json.loads((Path(selection_run)/sel.REPORT).read_text(encoding='utf-8'))
    if (selection.get('schema')!=sel.SCHEMA or selection.get('g2_parent_hashes')!=hashes or
        selection.get('validation_dataset_sha256')!=density.sha_rows(second) or
        selection.get('compatibility_sha256')!=sel._hash_json([list(x) for x in sorted(allowed)]) or
        selection.get('production_promotion') is not False):
        raise ValueError('GAMER_G2_0011_G2_0002_DRIFT')
    early={**hashes,'g2_0002_report_sha256':backbone.sha256(Path(selection_run)/sel.REPORT),
           'g2_0002_cases_sha256':backbone.sha256(Path(selection_run)/sel.CASES)}
    _,disc_hashes=rel.verify_discrimination_parent(discrimination_run,third,early)
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
    ninth_report,_,ninth_hashes=crowd10.verify_recall9_parent(recall_run,eighth,ninth,prior_hashes)
    parent10,parent10_hashes=verify_crowd10_parent(crowded_run,ninth,tenth,{**prior_hashes,**ninth_hashes})
    # Freeze selection BEFORE exposing any fresh DEV prompts, and reject a
    # tampered result claiming success without independently reproduced gates.
    if parent10['selected_rule']!=SELECTED or not parent10['selected_independent_screen_pass']:
        raise ValueError('GAMER_G2_0011_PARENT_NOT_ELIGIBLE')
    cohorts=new_cohorts(train,first,second,third,fourth,fifth,sixth,seventh,eighth,ninth,tenth,
                        cohort_count=cohort_count,limit=cohort_limit)
    rule=fifth_report['fixed_decoder_rules'][graph6.PARENT_ARM]
    original={key:value.detach().cpu().clone() for key,value in head.state_dict().items()}
    head=head.to(device).eval().requires_grad_(False)
    bootstrap_head=deepcopy(head).to(device).eval().requires_grad_(False)
    # bootstrap_head must contain the frozen pre-reconstruction G1 head for
    # capture (separate identity is required even when weights are equal).
    model,tok=backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    if any(parameter.requires_grad for parameter in model.parameters()):
        raise ValueError('GAMER_G2_0011_BACKBONE_UNFROZEN')
    width=None;scorers=None;context=None;control=None
    case_records=[];cohort_meta=[]
    for i,fresh in enumerate(cohorts,1):
        label=f'robustness_{i:02d}'
        captured=rel.capture(bootstrap_head,model,tok,fresh,device=device,
                             max_length=max_length,group=label)
        with torch.inference_mode():
            encoded=learned8.encode_cases(captured,head,allowed,rule,device=device,group=label)
        this_width=encoded[0]['vectors'].shape[1]
        if width is None:
            width=this_width
            context,control=recall9.load_frozen_scorers(Path(learned_run)/learned8.WEIGHTS,
                                      width,eighth_report['context_weights_sha256'])
            scorers=crowd10.load_frozen_retention(Path(recall_run)/recall9.WEIGHTS,width,
                                      ninth_report['retention_weights_sha256'])
            scorers=tuple(net.to(device).eval().requires_grad_(False) for net in scorers)
        elif width!=this_width:
            raise ValueError('GAMER_G2_0011_FEATURE_WIDTH_CHANGED')
        cache=recall9.encode_retention(encoded,context,control,device=device,group=label)
        _,records=recall9.evaluate(cache,*scorers,device=device,group='new_independent_validation',
                                   thresholds=(.40,))
        for row in records:
            row['cohort']=i
            # The decoder identity was chosen on the G2-0010 cohort. All
            # nonselected score heads are ignored by this audit.
            row['configurations']={name:row['configurations'][name] for name in NAMES}
        _verify_records(records,fresh,'new_independent_validation',NAMES)
        case_records.extend(records)
        cohort_meta.append({'cohort':i,'seed':SEED+104729*(i-1),'cases':len(fresh),
                            'dataset_sha256':density.sha_rows(fresh)})
    rel.validate_original_state(head,original)
    scored=screen(case_records,bootstrap_draws=bootstrap_draws)
    report={
        'schema':SCHEMA,'status':'G2_ROBUSTNESS_DEV_NOT_CERTIFIED',
        'g1_development_candidate':base.G1_ARM,'g1_head_sha256':manifest['head_sha256'],
        'g1_frozen':True,'g2_0004_rank_weights_frozen':True,'g2_0005_rule_frozen':True,
        'g2_0008_context_weights_frozen':True,'g2_0009_retention_weights_frozen':True,
        'g2_0010_selected_rule_frozen':True,'g2_0010_parent_records_verified':True,
        'selected_rule':SELECTED,'matched_control':CONTROL,'new_trainable_parameters':0,
        'g2_certified':False,'g2_production_unlocked':False,'production_promotion':False,
        'certification_authorized':False,'graph_execution_authorized':False,
        'memory_writes':False,'sealed_test_used':False,'challenge_or_test_split_used':False,
        'inference_uses_predicted_g1_only':True,'no_gold_graph_at_inference':True,
        'gold_used_for_offline_scoring_only':True,'no_new_edges_allowed':True,
        'no_fixed_cardinality':True,'model_selection_on_new_cohorts':False,
        'cohorts':cohort_meta,'cohort_count':len(cohorts),'cohort_limit':cohort_limit,
        'total_cases':len(case_records),'crowded_minimum_per_cohort':MIN_CROWDED_PER_COHORT,
        'global_recall_loss_limit':crowd10.GLOBAL_RECALL_LOSS,
        'crowded_recall_loss_limit':crowd10.CROWDED_RECALL_LOSS,
        'parent_hashes':{**prior_hashes,**ninth_hashes,**parent10_hashes},
        'frozen_retention_weights_sha256':ninth_report['retention_weights_sha256'],
        **scored,
    }
    output.mkdir(parents=True,exist_ok=False)
    (output/REPORT).write_text(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    (output/CASES).write_bytes(base._jsonl(case_records))
    return report
