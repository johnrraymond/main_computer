"""G2-0015: frozen G2-0014 density-routing robustness on four independent DEV cohorts.

The calibrated choice is fixed to route__joint_60__fallback. G2-0013's
source-conditioned scorer runs only on small predicted-G1 graphs; G2-0010
continues to handle medium/crowded graphs without modification. No training,
threshold selection, gold-assisted inference, sealed data, or production effects.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
import json
import math
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
from tools import nanojev_gamer_v1_g2_robustness_validation as robust11
from tools import nanojev_gamer_v1_g2_relationship_error_diagnosis as diagnosis12
from tools import nanojev_gamer_v1_g2_source_conditioned_discrimination as source13
from tools import nanojev_gamer_v1_g2_density_specialization as specialization14

SCHEMA='nanojev.gamer.graph.g2.density-routing-robustness.v1'
REPORT='g2_density_routing_robustness_report.json'
CASES='g2_density_routing_robustness_per_case.jsonl'
SELECTED='route__joint_60__fallback'
CONTROL='control__'+SELECTED
PARENT=robust11.SELECTED
NAMES=(PARENT,SELECTED,CONTROL)
SEED=450015
COHORTS=4
COHORT_LIMIT=320
BOOTSTRAPS=2000
MIN_CROWDED=30
MIN_SMALL=30


def new_cohorts(predecessors,*,count=COHORTS,limit=COHORT_LIMIT,seed=SEED):
    """Disjoint prompts from every prior TRAIN/DEV split and between cohorts."""
    if (not predecessors or not all(predecessors) or type(count) is not int or
            count!=COHORTS or type(limit) is not int or limit<2 or limit%2 or
            type(seed) is not int):
        raise ValueError('GAMER_G2_0015_BAD_COHORT_CONFIG')
    old=[row for cohort in predecessors for row in cohort]
    seen={base._sha(row['prompt']) for row in old}
    cohorts=[]
    for i in range(count):
        rows=base.novel_dev_rows(old,limit=limit,seed=seed+104729*i)
        hashes=[base._sha(row['prompt']) for row in rows]
        if (len(rows)!=limit or len(set(hashes))!=limit or set(hashes)&seen or
            Counter(row['family'] for row in rows)!=
                {'encounter':limit//2,'allocation':limit//2}):
            raise ValueError('GAMER_G2_0015_COHORT_OVERLAP')
        cohorts.append(rows);old.extend(rows);seen.update(hashes)
    return cohorts


def _verify_saved_rows(rows,originals,group,names):
    if len(rows)!=len(originals):
        raise ValueError('GAMER_G2_0015_PARENT_CASE_COUNT')
    for i,(row,source) in enumerate(zip(rows,originals),1):
        if (row.get('group')!=group or row.get('index')!=i or
            row.get('family')!=source['family'] or
            row.get('prompt_sha256')!=base._sha(source['prompt']) or
            row.get('gold_binding_edges')!=len(base.gold_binding_edges(source)) or
            row.get('density_bucket')!=density._bucket(row.get('g1_predicted_nodes',0)) or
            set(row.get('configurations',{}))!=set(names)):
            raise ValueError('GAMER_G2_0015_PARENT_CASE_IDENTITY')
        for name,result in row['configurations'].items():
            if (any(type(result.get(field)) is not int or result[field]<0
                    for field in ('correct','missing','false_positive')) or
                type(result.get('exact')) is not bool or
                result['correct']+result['missing']!=row['gold_binding_edges'] or
                result['exact']!=(result['missing']==result['false_positive']==0)):
                raise ValueError('GAMER_G2_0015_PARENT_CASE_COUNTS:'+name)
        # These equality checks only concern *scores* for the same policy,
        # not an assertion that identical scores imply identical edge sets.
        bucket=row['density_bucket'];scores=row['configurations']
        if bucket in (specialization14.BUCKET_MEDIUM,specialization14.BUCKET_CROWDED):
            if scores[SELECTED]!=scores[PARENT] or scores[CONTROL]!=scores[PARENT]:
                raise ValueError('GAMER_G2_0015_PARENT_FALLBACK_DRIFT')
        elif bucket==specialization14.BUCKET_SMALL:
            if {'source13_joint_60','source13_control_60'} <= set(scores):
                if (scores[SELECTED]!=scores['source13_joint_60'] or
                    scores[CONTROL]!=scores['source13_control_60']):
                    raise ValueError('GAMER_G2_0015_PARENT_SMALL_ROUTE_DRIFT')
        else:
            raise ValueError('GAMER_G2_0015_PARENT_UNKNOWN_BUCKET')


def verify_specialization14_parent(directory,calibration,independent,expected_hashes):
    """Recompute rule selection, gate outcomes, case-level metrics and lineage."""
    directory=Path(directory)
    rp=directory/specialization14.REPORT
    cp=directory/specialization14.CASES
    wp=directory/specialization14.WEIGHTS
    report=json.loads(rp.read_text(encoding='utf-8'))
    required={
        'schema':specialization14.SCHEMA,
        'status':'G2_DENSITY_SPECIALIZATION_DEV_NOT_CERTIFIED',
        'selected_route':SELECTED,'independent_screen_pass':True,
        'fallback_rule':PARENT,'fallback_medium_unconditional':True,
        'selected_route_uses_crowded_specialist':False,
        'small_fixed_policies_predeclared':list(specialization14.SMALL_POLICIES),
        'crowded_fixed_policies_predeclared':list(specialization14.CROWD_POLICIES),
        'g1_frozen':True,'g2_0004_rank_weights_frozen':True,
        'g2_0005_rule_frozen':True,'g2_0010_rule_frozen':True,
        'g2_0013_parent_weights_frozen':True,
        'g2_0011_robustness_verified':True,'g2_0012_diagnosis_verified':True,
        'g2_0013_parent_replay_verified':True,
        'no_gold_in_inference':True,'training_only_gold_supervision':True,
        'matched_distant_negative_control':True,
        'calibration_cases':len(calibration),
        'calibration_dataset_sha256':density.sha_rows(calibration),
        'independent_cases':len(independent),
        'independent_dataset_sha256':density.sha_rows(independent),
        'g1_head_sha256':base.G1_HEAD_SHA,
        'g2_certified':False,'g2_production_unlocked':False,
        'production_promotion':False,'certification_authorized':False,
        'graph_execution_authorized':False,'memory_writes':False,
        'sealed_test_used':False,'challenge_or_test_split_used':False,
    }
    for key,value in required.items():
        if type(report.get(key)) is not type(value) or report[key]!=value:
            raise ValueError('GAMER_G2_0015_PARENT_CONTRACT:'+key)
    if report.get('parent_hashes')!=expected_hashes:
        raise ValueError('GAMER_G2_0015_PARENT_LINEAGE')
    if report.get('new_weights_sha256')!=backbone.sha256(wp):
        raise ValueError('GAMER_G2_0015_PARENT_WEIGHTS')
    chosen,selection,gates=specialization14.select_route(
        report['calibration_metrics'],report['calibration_by_node_density'])
    if (chosen!=SELECTED or selection!=report.get('calibration_selection') or
        gates!=report.get('calibration_gates')):
        raise ValueError('GAMER_G2_0015_PARENT_SELECTION_DRIFT')
    names=set(report['independent_metrics'])
    if names!={PARENT,robust11.PARENT,SELECTED,CONTROL,
              'source13_joint_50','source13_joint_60','source13_joint_70',
              'source13_joint_80','source13_control_50','source13_control_60',
              'source13_control_70','source13_control_80'}:
        raise ValueError('GAMER_G2_0015_PARENT_INDEPENDENT_RULES')
    checked=specialization14.route_gates(
        report['independent_metrics'],report['independent_by_node_density'],
        SELECTED,CONTROL)
    if checked!=report.get('independent_selected_gates') or not all(checked.values()):
        raise ValueError('GAMER_G2_0015_PARENT_SCREEN_DRIFT')
    records=[json.loads(line) for line in cp.read_text(encoding='utf-8').splitlines()
             if line.strip()]
    if len(records)!=len(calibration)+len(independent):
        raise ValueError('GAMER_G2_0015_PARENT_RECORD_COUNT')
    cal=records[:len(calibration)];val=records[len(calibration):]
    _verify_saved_rows(cal,calibration,'previously_inspected_calibration',
                       report['calibration_metrics'])
    _verify_saved_rows(val,independent,'new_independent_validation',names)
    for part,met_key,dens_key in (
        (cal,'calibration_metrics','calibration_by_node_density'),
        (val,'independent_metrics','independent_by_node_density')):
        recalculated={name:base.metrics([
            {**row['configurations'][name],'family':row['family']} for row in part])
                      for name in report[met_key]}
        if (recalculated!=report[met_key] or
            specialization14.metrics_by_density(part)!=report[dens_key]):
            raise ValueError('GAMER_G2_0015_PARENT_METRIC_DRIFT')
    return report,{
        'g2_0014_report_sha256':backbone.sha256(rp),
        'g2_0014_cases_sha256':backbone.sha256(cp),
        'g2_0014_weights_sha256':backbone.sha256(wp),
    }


def _case_f1(rows,name):
    tp=sum(row['configurations'][name]['correct'] for row in rows)
    fp=sum(row['configurations'][name]['false_positive'] for row in rows)
    fn=sum(row['configurations'][name]['missing'] for row in rows)
    return 2*tp/max(1,2*tp+fp+fn)


def paired_bootstrap(records,*,density_bucket=None,opponent=PARENT,draws=BOOTSTRAPS,seed=SEED):
    """Paired, cohort/family-stratified bootstrap; identical sampled prompts."""
    if (not records or opponent not in (PARENT,CONTROL) or
            type(draws) is not int or draws<50):
        raise ValueError('GAMER_G2_0015_BAD_BOOTSTRAP')
    rows=[r for r in records if density_bucket is None or r['density_bucket']==density_bucket]
    if not rows:raise ValueError('GAMER_G2_0015_EMPTY_BOOTSTRAP')
    strata=defaultdict(list)
    for row in rows:strata[(row['cohort'],row['family'])].append(row)
    if len({r['cohort'] for r in rows})!=COHORTS:
        raise ValueError('GAMER_G2_0015_INCOMPLETE_BOOTSTRAP_COHORTS')
    parts=[(strata[k],len(strata[k])) for k in sorted(strata)]
    rng=random.Random(seed);differences=[]
    for _ in range(draws):
        sample=[part[rng.randrange(n)] for part,n in parts for _ in range(n)]
        differences.append(_case_f1(sample,SELECTED)-_case_f1(sample,opponent))
    differences.sort()
    return {'method':'PAIRED_BOOTSTRAP_STRATIFIED_BY_COHORT_AND_FAMILY',
            'opponent':opponent,'density_bucket':density_bucket,
            'draws':draws,'seed':seed,'f1_delta':_case_f1(rows,SELECTED)-_case_f1(rows,opponent),
            'lower_95':differences[int(.025*(draws-1))],
            'upper_95':differences[int(.975*(draws-1))]}


def screen(records,*,min_crowded=MIN_CROWDED,min_small=MIN_SMALL,bootstrap_draws=BOOTSTRAPS):
    if not records or min_crowded<1 or min_small<1:
        raise ValueError('GAMER_G2_0015_BAD_SCREEN')
    groups=defaultdict(list)
    for row in records:groups[row['cohort']].append(row)
    if set(groups)!=set(range(1,COHORTS+1)):
        raise ValueError('GAMER_G2_0015_COHORT_IDS')
    by_cohort={}
    for cohort,rows in sorted(groups.items()):
        metrics={name:base.metrics([{**r['configurations'][name],'family':r['family']}
                                    for r in rows]) for name in NAMES}
        buckets=specialization14.metrics_by_density(rows)
        gates=specialization14.route_gates(metrics,buckets,SELECTED,CONTROL)
        gates['crowded_cases_at_least_30']=buckets[specialization14.BUCKET_CROWDED][PARENT]['cases']>=min_crowded
        gates['minimum_crowded_examples']=gates['crowded_cases_at_least_30']
        gates['minimum_small_examples']=buckets[specialization14.BUCKET_SMALL][PARENT]['cases']>=min_small
        gates['small_f1_improved']=buckets[specialization14.BUCKET_SMALL][SELECTED]['f1']>buckets[specialization14.BUCKET_SMALL][PARENT]['f1']
        gates['medium_fallback_casewise']=all(r['configurations'][SELECTED]==r['configurations'][PARENT]
            for r in rows if r['density_bucket']==specialization14.BUCKET_MEDIUM)
        gates['crowded_fallback_casewise']=all(r['configurations'][SELECTED]==r['configurations'][PARENT]
            for r in rows if r['density_bucket']==specialization14.BUCKET_CROWDED)
        by_cohort[cohort]={'cases':len(rows),'metrics':metrics,'by_node_density':buckets,
                           'gates':gates,'pass':all(gates.values())}
    aggregate_metrics={name:base.metrics([{**r['configurations'][name],'family':r['family']}
                                         for r in records]) for name in NAMES}
    buckets=specialization14.metrics_by_density(records)
    aggregate_gates=specialization14.route_gates(aggregate_metrics,buckets,SELECTED,CONTROL)
    aggregate_gates['small_f1_improved']=buckets[specialization14.BUCKET_SMALL][SELECTED]['f1']>buckets[specialization14.BUCKET_SMALL][PARENT]['f1']
    overall_ci=paired_bootstrap(records,draws=bootstrap_draws)
    try:
        small_ci=paired_bootstrap(records,density_bucket=specialization14.BUCKET_SMALL,
                                  draws=bootstrap_draws,seed=SEED+1)
    except ValueError as exc:
        if str(exc) not in ('GAMER_G2_0015_INCOMPLETE_BOOTSTRAP_COHORTS',
                            'GAMER_G2_0015_EMPTY_BOOTSTRAP'):
            raise
        # An insufficient density stratum is evidence of a failed audit,
        # not a reason to discard all already-computed cohort results.
        small_ci={'method':'PAIRED_BOOTSTRAP_STRATIFIED_BY_COHORT_AND_FAMILY',
                  'density_bucket':specialization14.BUCKET_SMALL,'opponent':PARENT,
                  'draws':bootstrap_draws,'seed':SEED+1,'f1_delta':None,
                  'lower_95':None,'upper_95':None,'failure':str(exc)}
    control_ci=paired_bootstrap(records,opponent=CONTROL,draws=bootstrap_draws,seed=SEED+2)
    final_gates={
        'four_cohorts_pass_individually':all(v['pass'] for v in by_cohort.values()),
        'aggregate_all_gates':all(aggregate_gates.values()),
        'overall_f1_paired_ci_lower_above_zero':overall_ci['lower_95']>0,
        'small_f1_paired_ci_lower_above_zero':(small_ci['lower_95'] is not None and small_ci['lower_95']>0),
        'matched_control_paired_ci_lower_above_zero':control_ci['lower_95']>0,
        'four_cohorts_evaluated':len(by_cohort)==COHORTS,
    }
    return {'by_cohort':by_cohort,'aggregate_metrics':aggregate_metrics,
            'aggregate_by_node_density':buckets,'aggregate_gates':aggregate_gates,
            'paired_bootstrap':{'overall_vs_parent':overall_ci,
                               'small_vs_parent':small_ci,
                               'overall_vs_control':control_ci},
            'robustness_gates':final_gates,'robustness_pass':all(final_gates.values())}


def evaluate_frozen(captured,rank_head,joint,control,allowed,rule,context,retention,
                    *,cohort,device):
    """Only frozen selected and matched-control routes; never use gold at inference."""
    rows=[]
    for net in (joint,control,context,retention):
        net.to(device).eval().requires_grad_(False)
    with torch.inference_mode():
        for i,case in enumerate(captured,1):
            rank=rel.full_logits(rank_head,case['vectors'].to(device)).detach().cpu()
            old=density.candidates(case['anchors'],rank,allowed)
            parent5=density.select_edges(old,rule)
            features,odds,ordered=learned8.graph_features(case['anchors'],case['vectors'],old,parent5,rule)
            indices=[j for j,e in enumerate(ordered) if e in parent5]
            logits=retention(features[indices].to(device),odds[indices].to(device),
                     context(features[indices].to(device),odds[indices].to(device))).detach().cpu()
            baseline={ordered[j] for j,v in zip(indices,logits) if float(v)>=math.log(.4/.6)}
            bucket=density._bucket(len(case['anchors']))
            selected=set(baseline);matched=set(baseline)
            if bucket==specialization14.BUCKET_SMALL:
                inv=source13.make_inventory(case,rank,allowed)
                x=inv['features'].to(device);b=inv['base'].to(device);groups=inv['groups']
                selected=source13.decode_scores(inv,joint(x,b,groups,with_competition=True).cpu(),.60)
                matched=source13.decode_scores(inv,control(x,b,groups,with_competition=False).cpu(),.60)
            gold=case['gold']
            scores={PARENT:sel.score_set(baseline,gold),
                    SELECTED:sel.score_set(selected,gold),
                    CONTROL:sel.score_set(matched,gold)}
            rows.append({'cohort':cohort,'group':'new_independent_validation',
                         'index':i,'family':case['family'],
                         'prompt_sha256':case['prompt_sha256'],
                         'gold_binding_edges':len(gold),
                         'g1_predicted_nodes':len(case['anchors']),
                         'density_bucket':bucket,'configurations':scores})
            if i==1 or i%40==0 or i==len(captured):
                print(f'GAMER_G2_0015_EVALUATE robustness_{cohort:02d} {i}/{len(captured)}',flush=True)
    return rows


def run(*,root,g1_validation_report,g2_run,selection_run,discrimination_run,relational_run,
        density_run,consistency_run,context_run,learned_run,recall_run,crowded_run,
        robustness_run,diagnosis_run,source_run,specialization_run,output,
        device='cuda',max_length=2048,cohort_limit=COHORT_LIMIT,
        cohort_count=COHORTS,bootstrap_draws=BOOTSTRAPS):
    root=Path(root);output=Path(output)
    if output.exists():raise FileExistsError('GAMER_G2_0015_IMMUTABLE_OUTPUT')
    head,manifest,dataset,bootstrap,_,_,hashes=sel.load_parents(root,g1_validation_report,g2_run)
    train=base.original_training_rows(dataset,rel.TRAIN_LIMIT)
    if base._sha('\n'.join(base._sha(r['prompt']) for r in train))!=bootstrap.get('train_prompt_hashes_sha256'):
        raise ValueError('GAMER_G2_0015_TRAIN_DRIFT')
    first=base.novel_dev_rows(train,limit=rel.CALIBRATION_LIMIT,seed=base.SEED)
    if density.sha_rows(first)!=bootstrap.get('dev_dataset_sha256'):
        raise ValueError('GAMER_G2_0015_BOOTSTRAP_DRIFT')
    second=sel.new_validation_rows(train,first,limit=sel.VALIDATION_LIMIT)
    third=disc.new_validation_rows(train,first,second,limit=disc.VALIDATION_LIMIT)
    fourth=rel.new_validation_rows(train,first,second,third,limit=rel.VALIDATION_LIMIT)
    fifth=density.new_validation_rows(train,first,second,third,fourth,limit=density.VALIDATION_LIMIT)
    sixth=graph6.new_validation_rows(train,first,second,third,fourth,fifth,limit=graph6.VALIDATION_LIMIT)
    seventh=context7.new_validation_rows(train,first,second,third,fourth,fifth,sixth,limit=context7.VALIDATION_LIMIT)
    eighth=learned8.new_validation_rows(train,first,second,third,fourth,fifth,sixth,seventh,limit=learned8.VALIDATION_LIMIT)
    ninth=recall9.new_validation_rows(train,first,second,third,fourth,fifth,sixth,seventh,eighth,limit=recall9.VALIDATION_LIMIT)
    tenth=crowd10.new_validation_rows(train,first,second,third,fourth,fifth,sixth,seventh,eighth,ninth,limit=crowd10.VALIDATION_LIMIT)
    allowed=sel.compatibility_from_train(train)
    motifs,motif_audit=context7.learn_motifs(train,allowed)
    functional,train_evidence=graph6.learn_constraints(train,allowed)
    selection_dir=Path(selection_run)
    selection=json.loads((selection_dir/sel.REPORT).read_text(encoding='utf-8'))
    if (selection.get('schema')!=sel.SCHEMA or selection.get('g2_parent_hashes')!=hashes or
        selection.get('validation_dataset_sha256')!=density.sha_rows(second) or
        selection.get('compatibility_sha256')!=sel._hash_json([list(x) for x in sorted(allowed)]) or
        selection.get('production_promotion') is not False):
        raise ValueError('GAMER_G2_0015_G2_0002_DRIFT')
    early={**hashes,'g2_0002_report_sha256':backbone.sha256(selection_dir/sel.REPORT),
           'g2_0002_cases_sha256':backbone.sha256(selection_dir/sel.CASES)}
    _,disc_hashes=rel.verify_discrimination_parent(discrimination_run,third,early)
    _,_,fourth_hashes=density.verify_parent(relational_run,fourth,head,{**hashes,**disc_hashes},allowed)
    fifth_report,_,fifth_hashes=graph6.verify_density_parent(density_run,fifth,{**hashes,**disc_hashes,**fourth_hashes})
    _,_,sixth_hashes=context7.verify_graph6_parent(consistency_run,sixth,
              {**hashes,**disc_hashes,**fourth_hashes,**fifth_hashes},sel._hash_json(train_evidence))
    _,_,seventh_hashes=learned8.verify_context7_parent(context_run,seventh,
              {**hashes,**disc_hashes,**fourth_hashes,**fifth_hashes,**sixth_hashes},sel._hash_json(motif_audit))
    eighth_report,_,eighth_hashes=recall9.verify_learned8_parent(learned_run,eighth,
              {**hashes,**disc_hashes,**fourth_hashes,**fifth_hashes,**sixth_hashes,**seventh_hashes})
    prior_hashes={**hashes,**disc_hashes,**fourth_hashes,**fifth_hashes,**sixth_hashes,**seventh_hashes,**eighth_hashes}
    ninth_report,_,ninth_hashes=crowd10.verify_recall9_parent(recall_run,eighth,ninth,prior_hashes)
    parent10,parent10_hashes=robust11.verify_crowd10_parent(crowded_run,ninth,tenth,
                             {**prior_hashes,**ninth_hashes})
    if not parent10['selected_independent_screen_pass']:
        raise ValueError('GAMER_G2_0015_G2_0010_NOT_ELIGIBLE')
    prior_report=json.loads((Path(robustness_run)/robust11.REPORT).read_text(encoding='utf-8'))
    if prior_report.get('cohort_limit')!=robust11.COHORT_LIMIT or prior_report.get('cohort_count')!=4:
        raise ValueError('GAMER_G2_0015_PARENT_ROBUSTNESS_SIZE')
    prior_cohorts=robust11.new_cohorts(train,first,second,third,fourth,fifth,sixth,seventh,eighth,ninth,tenth,
                                      cohort_count=4,limit=robust11.COHORT_LIMIT)
    _,prior11_hashes=diagnosis12.verify_robust11_parent(robustness_run,prior_cohorts,
                             {**prior_hashes,**ninth_hashes,**parent10_hashes})
    predecessors=(train,first,second,third,fourth,fifth,sixth,seventh,eighth,ninth,tenth,*prior_cohorts)
    twelfth=diagnosis12.fresh_rows(predecessors,limit=diagnosis12.LIMIT)
    _,_,prior12_hashes=source13.verify_diagnosis_parent(diagnosis_run,twelfth,
                     {**prior_hashes,**ninth_hashes,**parent10_hashes,**prior11_hashes})
    thirteenth=source13.new_validation_rows((*predecessors,twelfth),limit=source13.VALIDATION_LIMIT)
    fourteenth=specialization14.fresh_rows((*predecessors,twelfth,thirteenth),
                                            limit=specialization14.VALIDATION_LIMIT)
    old_hashes={**prior_hashes,**ninth_hashes,**parent10_hashes,**prior11_hashes,**prior12_hashes}
    parent13,_,prior13_hashes=specialization14.verify_source13_parent(source_run,thirteenth,old_hashes)
    parent14,prior14_hashes=verify_specialization14_parent(specialization_run,thirteenth,fourteenth,
                           {**old_hashes,**prior13_hashes})
    if parent14['selected_route']!=SELECTED or not parent14['independent_screen_pass']:
        raise ValueError('GAMER_G2_0015_NO_LOCKED_ROUTE')
    predecessors=(*predecessors,twelfth,thirteenth,fourteenth)
    cohorts=new_cohorts(predecessors,count=cohort_count,limit=cohort_limit)
    rule=fifth_report['fixed_decoder_rules'][graph6.PARENT_ARM]
    original={key:value.detach().cpu().clone() for key,value in head.state_dict().items()}
    head=head.to(device).eval().requires_grad_(False)
    capture_head=deepcopy(head).to(device).eval().requires_grad_(False)
    model,tok=backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    if any(p.requires_grad for p in model.parameters()):
        raise ValueError('GAMER_G2_0015_BACKBONE_UNFROZEN')
    width=None;old_models=None;context=None;retention=None
    records=[];meta=[]
    for i,cohort in enumerate(cohorts,1):
        label=f'robustness_{i:02d}'
        captured=rel.capture(capture_head,model,tok,cohort,device=device,
                             max_length=max_length,group=label)
        w=captured[0]['vectors'].shape[1]
        if width is None:
            width=w
            old_models=specialization14.load_source13_models(
                Path(source_run)/source13.WEIGHTS,width,parent13['new_weights_sha256'])
            context,_=recall9.load_frozen_scorers(Path(learned_run)/learned8.WEIGHTS,width,
                                                 eighth_report['context_weights_sha256'])
            retention=crowd10.load_frozen_retention(Path(recall_run)/recall9.WEIGHTS,width,
                                               ninth_report['retention_weights_sha256'])[1]
        elif w!=width:raise ValueError('GAMER_G2_0015_FEATURE_WIDTH_DRIFT')
        part=evaluate_frozen(captured,head,old_models['joint'],old_models['control'],
                   allowed,rule,context,retention,cohort=i,device=device)
        _verify_saved_rows(part,cohort,'new_independent_validation',NAMES)
        records.extend(part)
        meta.append({'cohort':i,'seed':SEED+104729*(i-1),'cases':len(cohort),
                     'dataset_sha256':density.sha_rows(cohort)})
    rel.validate_original_state(head,original)
    scored=screen(records,bootstrap_draws=bootstrap_draws)
    report={'schema':SCHEMA,'status':'G2_DENSITY_ROUTING_ROBUSTNESS_DEV_NOT_CERTIFIED',
            'selected_route':SELECTED,'matched_control':CONTROL,'fallback_rule':PARENT,
            'parent_selected_route_replayed':True,
            'g1_development_candidate':base.G1_ARM,'g1_head_sha256':base.G1_HEAD_SHA,
            'g1_frozen':True,'g2_0004_rank_weights_frozen':True,
            'g2_0010_parent_frozen':True,'g2_0013_joint_weights_frozen':True,
            'g2_0014_selection_frozen':True,'unused_crowded_specialist_not_loaded':True,
            'new_trainable_parameters':0,'model_selection_on_new_cohorts':False,
            'small_policy':'joint_60','medium_policy':'fallback','crowded_policy':'fallback',
            'inference_uses_predicted_g1_only':True,'gold_used_for_offline_scoring_only':True,
            'cohorts':meta,'cohort_count':len(cohorts),'cohort_limit':cohort_limit,
            'total_cases':len(records),'crowded_minimum_per_cohort':MIN_CROWDED,
            'small_minimum_per_cohort':MIN_SMALL,
            'parent_hashes':{**old_hashes,**prior13_hashes,**prior14_hashes},
            'frozen_joint_weights_sha256':parent13['new_weights_sha256'],
            'g2_certified':False,'g2_production_unlocked':False,
            'production_promotion':False,'certification_authorized':False,
            'graph_execution_authorized':False,'memory_writes':False,
            'sealed_test_used':False,'challenge_or_test_split_used':False,**scored}
    output.mkdir(parents=True,exist_ok=False)
    (output/REPORT).write_text(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    (output/CASES).write_bytes(base._jsonl(records))
    return report
