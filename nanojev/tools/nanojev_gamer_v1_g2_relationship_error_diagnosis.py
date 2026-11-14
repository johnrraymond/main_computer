"""G2-0012: frozen G2 relationship-error attribution on fresh, disjoint DEV.

Diagnoses where correct bindings disappear and where wrong ones survive, using
G1-predicted nodes and frozen G2 scores. Gold annotations are used *only* after
inference for offline attribution, never as model or decoder inputs. Analysis
cannot establish that a representation is absent: it identifies observable
failure boundaries and testable hypotheses. No training or decoder changes.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
import json
import math

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
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES

SCHEMA='nanojev.gamer.graph.g2.relationship-error-diagnosis.v1'
REPORT='g2_relationship_error_diagnosis_report.json'
CASES='g2_relationship_error_diagnosis_per_case.jsonl'
SEED=420012
LIMIT=320
MIN_CROWDED=30
NAMES=robust11.NAMES


def fresh_rows(predecessors,*,limit=LIMIT,seed=SEED):
    if (not predecessors or not all(predecessors) or type(limit) is not int or
        limit<2 or limit%2 or type(seed) is not int):
        raise ValueError('GAMER_G2_0012_BAD_COHORT')
    earlier=[row for cohort in predecessors for row in cohort]
    fresh=base.novel_dev_rows(earlier,limit=limit,seed=seed)
    old_hashes={base._sha(row['prompt']) for row in earlier}
    hashes=[base._sha(row['prompt']) for row in fresh]
    if (len(hashes)!=len(set(hashes)) or set(hashes)&old_hashes or
        Counter(r['family'] for r in fresh)!= {'encounter':limit//2,'allocation':limit//2}):
        raise ValueError('GAMER_G2_0012_PROMPT_LEAKAGE')
    return fresh


def verify_robust11_parent(directory,cohorts,expected_hashes,*,bootstrap_draws=None):
    """Recompute saved 0011 cases, all gates and paired interval, not mere flags."""
    directory=Path(directory);path=directory/robust11.REPORT;casepath=directory/robust11.CASES
    report=json.loads(path.read_text(encoding='utf-8'))
    required={
        'schema':robust11.SCHEMA,'status':'G2_ROBUSTNESS_DEV_NOT_CERTIFIED',
        'robustness_pass':True,'selected_rule':robust11.SELECTED,
        'matched_control':robust11.CONTROL,'g1_frozen':True,
        'g2_0004_rank_weights_frozen':True,'g2_0005_rule_frozen':True,
        'g2_0008_context_weights_frozen':True,'g2_0009_retention_weights_frozen':True,
        'g2_0010_selected_rule_frozen':True,'g2_0010_parent_records_verified':True,
        'new_trainable_parameters':0,'g2_certified':False,
        'g2_production_unlocked':False,'production_promotion':False,
        'certification_authorized':False,'graph_execution_authorized':False,
        'memory_writes':False,'sealed_test_used':False,'challenge_or_test_split_used':False,
        'no_gold_graph_at_inference':True,'inference_uses_predicted_g1_only':True,
        'model_selection_on_new_cohorts':False,'no_new_edges_allowed':True,
        'cohort_count':len(cohorts),'total_cases':sum(map(len,cohorts)),
        'g1_development_candidate':base.G1_ARM,'g1_head_sha256':base.G1_HEAD_SHA,
    }
    for key,value in required.items():
        if type(report.get(key)) is not type(value) or report[key]!=value:
            raise ValueError('GAMER_G2_0012_PARENT_CONTRACT:'+key)
    if (report.get('parent_hashes')!=expected_hashes or
        report.get('frozen_retention_weights_sha256')!=expected_hashes['g2_0009_weights_sha256']):
        raise ValueError('GAMER_G2_0012_PARENT_LINEAGE')
    expected_cohorts=[{'cohort':i,'seed':robust11.SEED+104729*(i-1),
                       'cases':len(rows),'dataset_sha256':density.sha_rows(rows)}
                      for i,rows in enumerate(cohorts,1)]
    if report.get('cohorts')!=expected_cohorts:
        raise ValueError('GAMER_G2_0012_PARENT_COHORTS')
    records=[json.loads(line) for line in casepath.read_text(encoding='utf-8').splitlines() if line.strip()]
    if len(records)!=sum(map(len,cohorts)):
        raise ValueError('GAMER_G2_0012_PARENT_COUNT')
    i=0
    for group,original in enumerate(cohorts,1):
        part=records[i:i+len(original)];i+=len(original)
        if any(type(row.get('cohort')) is not int or row['cohort']!=group for row in part):
            raise ValueError('GAMER_G2_0012_PARENT_COHORT_ID')
        robust11._verify_records(part,original,'new_independent_validation',NAMES)
    # Audit the exact original statistical test, not a changed retrospective one.
    draws=report.get('paired_bootstrap',{}).get('draws') if bootstrap_draws is None else bootstrap_draws
    checked=robust11.screen(records,bootstrap_draws=draws)
    normalized=lambda value:json.loads(json.dumps(value,sort_keys=True,allow_nan=False))
    for field,value in checked.items():
        if normalized(value)!=report.get(field):
            raise ValueError('GAMER_G2_0012_PARENT_AUDIT:'+field)
    return report,{
        'g2_0011_report_sha256':backbone.sha256(path),
        'g2_0011_cases_sha256':backbone.sha256(casepath),
    }


def _rank_auc(pairs):
    """Exact pairwise probability of ranking a gold candidate above a false one."""
    positive=sorted(s for s,y in pairs if y)
    negative=sorted(s for s,y in pairs if not y)
    if not positive or not negative:return None
    import bisect
    wins=0.
    for p in positive:
        wins+=bisect.bisect_left(negative,p)+.5*(bisect.bisect_right(negative,p)-bisect.bisect_left(negative,p))
    return wins/(len(positive)*len(negative))


def classify_gold(edge,anchors,allowed,prob,parent,selected,candidate_set):
    """Exclusive first failure boundary for every gold relationship."""
    src,dst,relation=edge;lookup={a:i for i,a in enumerate(anchors)}
    if src not in lookup and dst not in lookup:return 'g1_both_endpoints_missing'
    if src not in lookup:return 'g1_source_missing'
    if dst not in lookup:return 'g1_destination_missing'
    if (relation,src[2],dst[2]) not in allowed:return 'train_compatibility_exclusion'
    p=prob[lookup[src],lookup[dst]]
    k=int(p.argmax())
    if BIND_CLASSES[k]!=relation:
        return 'rank_argmax_null' if k==0 else 'rank_argmax_wrong_relation'
    if edge not in candidate_set:return 'candidate_inventory_mismatch'
    if edge not in parent:return 'g2_0005_density_competition_filter'
    if edge not in selected:return 'g2_0010_retention_filter'
    return 'retained_correct'


def classify_false(edge,gold,gold_nodes):
    """Exclusive association category; shared endpoints are NOT contradictions."""
    src,dst,rel=edge
    if src not in gold_nodes or dst not in gold_nodes:
        if src not in gold_nodes and dst not in gold_nodes:return 'both_endpoints_not_gold_objects'
        return 'source_not_gold_object' if src not in gold_nodes else 'destination_not_gold_object'
    if any(s==src and d==dst for s,d,_ in gold):return 'wrong_relation_on_true_pair'
    if any(s==src and r==rel for s,_,r in gold):return 'wrong_destination_same_source_relation'
    if any(d==dst and r==rel for _,d,r in gold):return 'wrong_source_same_destination_relation'
    return 'gold_nodes_without_that_relation'


def _gap_class(edge):
    src,dst,_=edge
    gap=max(0,max(src[0]-dst[1],dst[0]-src[1]))
    if gap==0:return 'overlap_or_touch'
    if gap<=32:return 'gap_1_32'
    if gap<=128:return 'gap_33_128'
    return 'gap_gt_128'


def diagnose_case(item,row,allowed,rank,selected,*,parent=None):
    """Offline label-based error attribution. Requires frozen predictions only."""
    anchors=item['anchors'];gold=item['gold'];parent=item['parent'] if parent is None else parent
    if set(selected)-set(parent):raise ValueError('GAMER_G2_0012_NEW_EDGE')
    if rank.shape!=(len(anchors),len(anchors),len(BIND_CLASSES)) or not torch.isfinite(rank).all():
        raise ValueError('GAMER_G2_0012_BAD_RANK')
    if len(anchors)!=len(set(anchors)) or not gold:
        raise ValueError('GAMER_G2_0012_BAD_GRAPH')
    probs=rank.detach().float().cpu().softmax(-1)
    known={(s['start'],s['end'],s['kind']) for s in row['object_spans']}
    if not any(known):raise ValueError('GAMER_G2_0012_BAD_GOLD_NODES')
    if any(src not in known or dst not in known for src,dst,_ in gold):
        raise ValueError('GAMER_G2_0012_BAD_GOLD_EDGES')
    candidates=set(item['edges'])
    failure=Counter(classify_gold(edge,anchors,allowed,probs,parent,selected,candidates) for edge in gold)
    false_selected=set(selected)-gold
    false_parent=set(parent)-gold
    false_removed=false_parent-false_selected
    lost_correct=(parent&gold)-set(selected)
    if (sum(failure.values())!=len(gold) or
        failure['retained_correct']!=len(set(selected)&gold) or
        len(false_parent)!=len(false_selected)+len(false_removed)):
        raise ValueError('GAMER_G2_0012_ATTRIBUTION_MISMATCH')
    by_class=Counter(classify_false(edge,gold,known) for edge in false_selected)
    by_relation=Counter(edge[2] for edge in false_selected)
    by_kind=Counter(f'{src[2]}->{dst[2]}:{rel}' for src,dst,rel in false_selected)
    by_gap=Counter(_gap_class(edge) for edge in false_selected)
    edge_inventory={(s,d,r):(c,o,n,g) for s,d,r,c,o,n,g in item['candidate_features']}
    # Compare candidate strengths using FROZEN scores only (no oracle decoder).
    rank_pairs=[(math.log(max(1.e-8,conf)/(1.-min(.99999999,conf))),edge in gold)
                for edge,(conf,_,_,_) in edge_inventory.items()]
    # This AUC is for generated candidates, not all possible pairs. It cannot
    # prove the absence of a relation representation in earlier neural states.
    competing=0;groups=defaultdict(list)
    for edge,(conf,odds,count,delta) in edge_inventory.items():
        groups[(edge[0],edge[2])].append((float(odds),edge in gold))
    for group in groups.values():
        positives=[s for s,truth in group if truth]
        negatives=[s for s,truth in group if not truth]
        if positives and negatives and max(negatives)>=max(positives):competing+=1
    examples=[]
    for edge in sorted(false_selected, key=lambda e:(-edge_inventory[e][0],e))[:5]:
        src,dst,relation=edge
        confidence,odds,count,delta=edge_inventory[edge]
        examples.append({'source':list(src),'destination':list(dst),
                         'relation':relation,'rank_confidence':confidence,
                         'competition_count':count,'competition_gap':delta,
                         'association_class':classify_false(edge,gold,known)})
    missed=[]
    lookup={anchor:i for i,anchor in enumerate(anchors)}
    for edge in sorted(gold-set(selected))[:5]:
        src,dst,relation=edge
        stage=classify_gold(edge,anchors,allowed,probs,parent,selected,candidates)
        evidence={'source':list(src),'destination':list(dst),
                  'relation':relation,'first_failure':stage}
        if src in lookup and dst in lookup:
            prediction=probs[lookup[src],lookup[dst]]
            evidence['gold_relation_probability']=float(prediction[BIND_CLASSES.index(relation)])
            evidence['predicted_relation']=BIND_CLASSES[int(prediction.argmax())]
        missed.append(evidence)
    result={
        'selected_false_examples':examples,'missing_gold_examples':missed,
        'index':item['index'],'prompt_sha256':item['prompt_sha256'],
        'family':item['family'],'density_bucket':density._bucket(len(anchors)),
        'g1_predicted_nodes':len(anchors),'gold_nodes':len(known),
        'gold_relationships':len(gold),'candidate_relationships':len(candidates),
        'g1_matched_gold_nodes':len(known & set(anchors)),
        'gold_failure_boundary':dict(sorted(failure.items())),
        'selected_false_analyses':{
            'association':dict(sorted(by_class.items())),
            'relation':dict(sorted(by_relation.items())),
            'endpoint_relation':dict(sorted(by_kind.items())),
            'span_separation':dict(sorted(by_gap.items())),
        },
        'candidate_rank_auc':_rank_auc(rank_pairs),
        'candidate_true_count':sum(edge in gold for edge in candidates),
        'candidate_false_count':sum(edge not in gold for edge in candidates),
        'source_relation_groups_false_outscores_true':competing,
        'selected_correct':len(set(selected)&gold),
        'selected_false':len(false_selected),
        'parent_correct':len(parent&gold),
        'parent_false':len(false_parent),
        'retention_false_removed':len(false_removed),
        'retention_correct_removed':len(lost_correct),
    }
    return result


def aggregate_diagnosis(rows):
    if not rows:raise ValueError('GAMER_G2_0012_EMPTY_DIAGNOSIS')
    result={}
    for bucket,subset in [('all',rows),*[(b,[r for r in rows if r['density_bucket']==b])
                                for b in ('small_1_24','medium_25_48','crowded_49_128')]]:
        if not subset:
            result[bucket]={'cases':0};continue
        failure=Counter();association=Counter();relation=Counter();kinds=Counter();gap=Counter()
        totals=Counter();aucs=[]
        for row in subset:
            failure.update(row['gold_failure_boundary'])
            block=row['selected_false_analyses']
            association.update(block['association']);relation.update(block['relation'])
            kinds.update(block['endpoint_relation']);gap.update(block['span_separation'])
            totals.update({k:row[k] for k in (
                'gold_relationships','candidate_relationships','candidate_true_count',
                'candidate_false_count','selected_correct','selected_false',
                'parent_correct','parent_false','retention_false_removed',
                'retention_correct_removed','source_relation_groups_false_outscores_true')})
            if row['candidate_rank_auc'] is not None:aucs.append(row['candidate_rank_auc'])
        if (sum(failure.values())!=totals['gold_relationships'] or
            sum(association.values())!=totals['selected_false'] or
            totals['parent_correct']-totals['selected_correct']!=totals['retention_correct_removed'] or
            totals['parent_false']-totals['selected_false']!=totals['retention_false_removed']):
            raise ValueError('GAMER_G2_0012_AGGREGATION_DRIFT')
        result[bucket]={'cases':len(subset),'totals':dict(totals),
            'gold_failure_boundary':dict(sorted(failure.items())),
            'selected_false_association':dict(sorted(association.items())),
            'selected_false_by_relation':dict(sorted(relation.items())),
            'selected_false_by_endpoint_relation':dict(sorted(kinds.items())),
            'selected_false_by_span_separation':dict(sorted(gap.items())),
            'candidate_rank_auc_mean_of_cases':(sum(aucs)/len(aucs) if aucs else None),
            'cases_with_both_candidate_classes':len(aucs),
            'gold_reachable_by_g1_count':sum(v for k,v in failure.items() if not k.startswith('g1_')),
            'gold_in_candidate_inventory_count':totals['candidate_true_count'],
            'gold_in_parent_count':totals['parent_correct'],
            'gold_in_selected_count':totals['selected_correct'],
            'recall':totals['selected_correct']/totals['gold_relationships'],
            'candidate_oracle_recall_upper_bound':totals['candidate_true_count']/totals['gold_relationships']}
    return result


def run(*,root,g1_validation_report,g2_run,selection_run,discrimination_run,relational_run,
        density_run,consistency_run,context_run,learned_run,recall_run,crowded_run,
        robustness_run,output,device='cuda',max_length=2048,limit=LIMIT):
    root=Path(root);output=Path(output)
    if output.exists():raise FileExistsError('GAMER_G2_0012_IMMUTABLE_OUTPUT')
    head,manifest,dataset,bootstrap,_,_,hashes=sel.load_parents(root,g1_validation_report,g2_run)
    train=base.original_training_rows(dataset,rel.TRAIN_LIMIT)
    if base._sha('\n'.join(base._sha(row['prompt']) for row in train))!=bootstrap.get('train_prompt_hashes_sha256'):
        raise ValueError('GAMER_G2_0012_TRAIN_REPLAY')
    first=base.novel_dev_rows(train,limit=rel.CALIBRATION_LIMIT,seed=base.SEED)
    if density.sha_rows(first)!=bootstrap.get('dev_dataset_sha256'):
        raise ValueError('GAMER_G2_0012_G2_0001_REPLAY')
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
        raise ValueError('GAMER_G2_0012_G2_0002_DRIFT')
    early={**hashes,'g2_0002_report_sha256':backbone.sha256(selection_dir/sel.REPORT),
           'g2_0002_cases_sha256':backbone.sha256(selection_dir/sel.CASES)}
    _,disc_hashes=rel.verify_discrimination_parent(discrimination_run,third,early)
    _,_,fourth_hashes=density.verify_parent(relational_run,fourth,head,{**hashes,**disc_hashes},allowed)
    fifth_report,_,fifth_hashes=graph6.verify_density_parent(density_run,fifth,
                               {**hashes,**disc_hashes,**fourth_hashes})
    _,_,sixth_hashes=context7.verify_graph6_parent(consistency_run,sixth,
                               {**hashes,**disc_hashes,**fourth_hashes,**fifth_hashes},
                               sel._hash_json(train_evidence))
    _,_,seventh_hashes=learned8.verify_context7_parent(context_run,seventh,
                               {**hashes,**disc_hashes,**fourth_hashes,**fifth_hashes,**sixth_hashes},
                               sel._hash_json(motif_audit))
    eighth_report,_,eighth_hashes=recall9.verify_learned8_parent(learned_run,eighth,
                   {**hashes,**disc_hashes,**fourth_hashes,**fifth_hashes,**sixth_hashes,**seventh_hashes})
    prior_hashes={**hashes,**disc_hashes,**fourth_hashes,**fifth_hashes,**sixth_hashes,
                  **seventh_hashes,**eighth_hashes}
    ninth_report,_,ninth_hashes=crowd10.verify_recall9_parent(recall_run,eighth,ninth,prior_hashes)
    parent10,parent10_hashes=robust11.verify_crowd10_parent(crowded_run,ninth,tenth,
                                                            {**prior_hashes,**ninth_hashes})
    if not parent10['selected_independent_screen_pass']:
        raise ValueError('GAMER_G2_0012_G2_0010_NOT_VALIDATED')
    parent_cohort_limit=json.loads((Path(robustness_run)/robust11.REPORT).read_text(encoding='utf-8')).get('cohort_limit')
    if (type(parent_cohort_limit) is not int or parent_cohort_limit<2 or
            parent_cohort_limit>robust11.COHORT_LIMIT or parent_cohort_limit%2):
        raise ValueError('GAMER_G2_0012_PARENT_COHORT_LIMIT')
    parent_cohorts=robust11.new_cohorts(train,first,second,third,fourth,fifth,sixth,seventh,eighth,ninth,tenth,
                                        cohort_count=4,limit=parent_cohort_limit)
    parent11,parent11_hashes=verify_robust11_parent(robustness_run,parent_cohorts,
                                                    {**prior_hashes,**ninth_hashes,**parent10_hashes})
    fresh=fresh_rows((train,first,second,third,fourth,fifth,sixth,seventh,eighth,ninth,tenth,*parent_cohorts),limit=limit)
    rule=fifth_report['fixed_decoder_rules'][graph6.PARENT_ARM]
    original={k:v.detach().cpu().clone() for k,v in head.state_dict().items()}
    head=head.to(device).eval().requires_grad_(False)
    frozen_g1=deepcopy(head).to(device).eval().requires_grad_(False)
    model,tok=backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    if any(p.requires_grad for p in model.parameters()):
        raise ValueError('GAMER_G2_0012_UNFROZEN_BACKBONE')
    captured=rel.capture(frozen_g1,model,tok,fresh,device=device,max_length=max_length,group='diagnosis_new_independent')
    with torch.inference_mode():
        encoded=learned8.encode_cases(captured,head,allowed,rule,device=device,group='diagnosis_new_independent')
    width=encoded[0]['vectors'].shape[1]
    context,_=recall9.load_frozen_scorers(Path(learned_run)/learned8.WEIGHTS,width,
                                        eighth_report['context_weights_sha256'])
    context=context.to(device).eval().requires_grad_(False)
    retention=crowd10.load_frozen_retention(Path(recall_run)/recall9.WEIGHTS,width,
                                          ninth_report['retention_weights_sha256'])[1]
    retention=retention.to(device).eval().requires_grad_(False)
    diagnostics=[]
    with torch.inference_mode():
        for i,(row,item) in enumerate(zip(fresh,encoded),1):
            selected_indices=[j for j,edge in enumerate(item['edges']) if edge in item['parent']]
            feats=item['features'][selected_indices].to(device)
            odds=item['base_scores'][selected_indices].to(device)
            priors=context(feats,odds)
            retention_scores=retention(feats,odds,priors).detach().cpu()
            if not torch.isfinite(retention_scores).all():raise ValueError('GAMER_G2_0012_NONFINITE_RETENTION')
            selected={item['edges'][j] for j,score in zip(selected_indices,retention_scores)
                      if float(score)>=math.log(.40/.60)}
            rank=rel.full_logits(head,item['vectors'].to(device)).detach().cpu()
            item['candidate_features']=density.candidates(item['anchors'],rank,allowed)
            diagnosis=diagnose_case(item,row,allowed,rank,selected)
            parent_score=sel.score_set(item['parent'],item['gold'])
            selected_score=sel.score_set(selected,item['gold'])
            diagnosis['configurations']={robust11.PARENT:parent_score,robust11.SELECTED:selected_score}
            diagnostics.append(diagnosis)
            if i==1 or i%40==0 or i==len(encoded):
                print(f'GAMER_G2_0012_DIAGNOSE {i}/{len(encoded)}',flush=True)
    rel.validate_original_state(head,original)
    distribution=aggregate_diagnosis(diagnostics)
    metrics={name:base.metrics([{**r['configurations'][name],'family':r['family']}
                                for r in diagnostics]) for name in (robust11.PARENT,robust11.SELECTED)}
    crowded_count=distribution['crowded_49_128']['cases']
    # Diagnostic findings are observations about the pipeline, not claims
    # that a representational feature is absent from the neural backbone.
    crowded=distribution['crowded_49_128']
    crowded_misses={k:v for k,v in crowded.get('gold_failure_boundary',{}).items()
                    if k!='retained_correct'}
    crowded_false=crowded.get('selected_false_association',{})
    leading_missing=max(crowded_misses,key=crowded_misses.get) if crowded_misses else None
    leading_false=max(crowded_false,key=crowded_false.get) if crowded_false else None
    report={
        'schema':SCHEMA,'status':'G2_RELATIONSHIP_ERROR_DIAGNOSIS_DEV_NOT_CERTIFIED',
        'g1_frozen':True,'g2_0004_rank_weights_frozen':True,'g2_0005_rule_frozen':True,
        'g2_0008_context_weights_frozen':True,'g2_0009_retention_weights_frozen':True,
        'g2_0010_rule_frozen':True,'g2_0011_robustness_verified':True,
        'selected_rule':robust11.SELECTED,'new_trainable_parameters':0,
        'dataset_seed':SEED,'independent_cases':len(fresh),'independent_dataset_sha256':density.sha_rows(fresh),
        'g1_development_candidate':base.G1_ARM,'g1_head_sha256':manifest['head_sha256'],
        'parent_hashes':{**prior_hashes,**ninth_hashes,**parent10_hashes,**parent11_hashes},
        'frozen_retention_weights_sha256':ninth_report['retention_weights_sha256'],
        'analysis_by_density':distribution,'independent_metrics':metrics,
        'leading_crowded_missing_relationship_boundary':leading_missing,
        'leading_crowded_false_relationship_class':leading_false,
        'interpretation_limit':'FIRST_OBSERVABLE_FAILURE_STAGE_NOT_PROOF_OF_ABSENT_NEURAL_INFORMATION',
        'minimum_crowded_cases':MIN_CROWDED,'crowded_case_count':crowded_count,
        'crowded_diagnosis_sufficient_cases':crowded_count>=MIN_CROWDED,
        'attribution_uses_gold_offline_only':True,
        'failure_boundary_is_not_proof_of_missing_neural_representation':True,
        'no_training':True,'no_model_selection':True,'no_decoder_change':True,
        'inference_uses_predicted_g1_only':True,'no_gold_graph_at_inference':True,
        'sealed_test_used':False,'challenge_or_test_split_used':False,
        'g2_certified':False,'g2_production_unlocked':False,'production_promotion':False,
        'certification_authorized':False,'graph_execution_authorized':False,'memory_writes':False,
    }
    output.mkdir(parents=True,exist_ok=False)
    (output/REPORT).write_text(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    (output/CASES).write_bytes(base._jsonl(diagnostics))
    return report
