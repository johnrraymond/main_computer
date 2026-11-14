"""G2-0007: frozen, context-dependent graph binding discrimination.

Instead of imposing structural cardinality constraints, learn two-hop relational
motif evidence from TRAIN graphs. At inference the only witness edges are those
selected by the frozen G2-0005 decoder. Apply bounded, non-cascading contextual
score adjustments to frozen candidate edges; neither G1 nor G2 weights change.
Previously inspected G2-0006 DEV is calibration; a new prompt-disjoint DEV
cohort is reserved for post-selection validation. No graph execution or promotion.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import math

import torch

from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools import nanojev_gamer_v1_g2_binding_selection as sel
from tools import nanojev_gamer_v1_g2_binding_discrimination as disc
from tools import nanojev_gamer_v1_g2_relational_training as relational
from tools import nanojev_gamer_v1_g2_density_discrimination as density
from tools import nanojev_gamer_v1_g2_graph_consistency as graph6
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools.nanojev_gamer_v1_graph_contract import BINDINGS, MAX_EDGES

SCHEMA='nanojev.gamer.graph.g2.context-dependent-graph-discrimination.v1'
REPORT='g2_contextual_graph_report.json'
CASES='g2_contextual_graph_per_case.jsonl'
MOTIFS='g2_contextual_graph_train_motifs.json'
SEED=370007
VALIDATION_LIMIT=320
PARENT_ARM=graph6.PARENT_ARM
# Frozen, declared prior to examining the new validation prompts.
VARIANTS={
    'motif_bonus_025':(.25,0.),
    'motif_bonus_050':(.50,0.),
    'motif_bonus_075':(.75,0.),
    'motif_signed_050_025':(.50,.25),
    'motif_signed_075_050':(.75,.50),
    'motif_signed_100_050':(1.,.50),
}
MIN_PATTERN_OBSERVATIONS=6
MIN_ENRICHMENT=math.log(1.5)


def sha_rows(rows):
    return hashlib.sha256(base._jsonl(rows)).hexdigest()


def new_validation_rows(train,first,second,third,fourth,fifth,sixth,*,limit=VALIDATION_LIMIT,seed=SEED):
    earlier=train+first+second+third+fourth+fifth+sixth
    if limit<2 or limit%2 or not all((train,first,second,third,fourth,fifth,sixth)):
        raise ValueError('GAMER_G2_0007_BAD_COHORT')
    fresh=base.novel_dev_rows(earlier,limit=limit,seed=seed)
    old_hash={base._sha(x['prompt']) for x in earlier}
    hashes=[base._sha(x['prompt']) for x in fresh]
    if len(set(hashes))!=len(hashes) or set(hashes)&old_hash:
        raise ValueError('GAMER_G2_0007_PROMPT_LEAKAGE')
    if Counter(x['family'] for x in fresh)!={'encounter':limit//2,'allocation':limit//2}:
        raise ValueError('GAMER_G2_0007_UNBALANCED_HOLDOUT')
    return fresh


def two_hop_patterns(edges):
    """Return (source,target)->set[(rel,direction,rel,direction,middle_kind)].

    The two witness edges must be different, and the middle vertex may not be
    either endpoint. Direction is relative to the source->middle->destination
    walk. Node identifiers are predicted span anchors at inference, never gold.
    """
    incident=defaultdict(list)
    for src,dst,rel in edges:
        if src==dst or rel not in BINDINGS or len(src)!=3 or len(dst)!=3:
            raise ValueError('GAMER_G2_0007_BAD_GRAPH_EDGE')
        incident[src].append((dst,rel,'>'))
        incident[dst].append((src,rel,'<'))
    patterns=defaultdict(set)
    for mid,links in incident.items():
        for src,first,first_mid_direction in links:
            for dst,second,second_mid_direction in links:
                if src==dst or src==mid or dst==mid:
                    continue
                src_to_mid='<' if first_mid_direction=='>' else '>'
                patterns[(src,dst)].add((first,src_to_mid,second,second_mid_direction,mid[2]))
    return patterns


def context_patterns(edges,candidates):
    """Endpoint neighborhoods plus optional two-hop witnesses.

    A candidate edge is explicitly excluded from its own endpoint context.
    Thus even a true gold training edge does not provide its own evidence.
    """
    paths=two_hop_patterns(edges)
    incident=defaultdict(list)
    for src,dst,rel in edges:
        incident[src].append((dst,rel,'>',(src,dst,rel)))
        incident[dst].append((src,rel,'<',(src,dst,rel)))
    result={}
    for src,dst,rel in candidates:
        own=(src,dst,rel)
        observed=set()
        for side,node in (('src',src),('dst',dst)):
            for neighbor,other_rel,direction,existing in incident.get(node,()):
                if existing!=own:
                    observed.add(('endpoint',side,direction,other_rel,neighbor[2]))
        observed.update(('twohop',)+pattern for pattern in paths.get((src,dst),()))
        result[own]=observed
    return result

def _training_edges(row,allowed):
    nodes={n['id']:n for n in row['graph']['nodes'] if 'span' in n}
    anchors={name:(n['span'][0],n['span'][1],n['kind']) for name,n in nodes.items()}
    edges=set()
    for item in row['graph']['edges']:
        if item['type'] not in BINDINGS or item['src'] not in anchors or item['dst'] not in anchors:
            continue
        src,dst=anchors[item['src']],anchors[item['dst']]
        if (item['type'],src[2],dst[2]) not in allowed:
            raise ValueError('GAMER_G2_0007_TRAIN_RELATION_DRIFT')
        edges.add((src,dst,item['type']))
    return anchors,edges


def learn_motifs(train,allowed,*,min_observations=MIN_PATTERN_OBSERVATIONS):
    """Estimate relation-conditioned motif likelihood ratios from TRAIN only.

    Negative samples share the positive relation and endpoint kinds but are
    not annotated edges. The sampling is deterministic and fully auditable.
    Only statistically enriched/anti-enriched patterns are retained. Neither
    test nor development answers are available to this function.
    """
    if not train or any(row.get('split')!='train' for row in train):
        raise ValueError('GAMER_G2_0007_MOTIFS_TRAIN_ONLY')
    if type(min_observations) is not int or min_observations<1:
        raise ValueError('GAMER_G2_0007_BAD_MIN_OBSERVATIONS')
    counts=defaultdict(lambda:{'positive':Counter(),'negative':Counter(),
                               'positive_total':0,'negative_total':0})
    for row in train:
        anchors,positive=_training_edges(row,allowed)
        # Compute the candidate set first, then remove each positive edge
        # from its own context during feature extraction.
        bykind=defaultdict(list)
        for anchor in anchors.values():
            bykind[anchor[2]].append(anchor)
        for kind in bykind:
            bykind[kind].sort()
        negatives=set()
        for src,dst,rel in sorted(positive):
            # Same type, same source, distinct plausible destination. Also
            # substitute the source so a model cannot rely on target-only cues.
            dests=[other for other in bykind[dst[2]]
                   if other!=src and (src,other,rel) not in positive]
            sources=[other for other in bykind[src[2]]
                     if other!=dst and (other,dst,rel) not in positive]
            negatives.update((src,other,rel) for other in dests[:3])
            negatives.update((other,dst,rel) for other in sources[:3])
        motifs=context_patterns(positive,positive|negatives)
        for label,group in ((True,positive),(False,negatives)):
            for src,dst,rel in group:
                key=(rel,src[2],dst[2]);bucket=counts[key]
                side='positive' if label else 'negative'
                bucket[side+'_total']+=1
                bucket[side].update(motifs.get((src,dst,rel),()))
    evidence={};weights={}
    for key in sorted(counts):
        group=counts[key];p=group['positive_total'];n=group['negative_total']
        learned={};details={}
        for pattern in sorted(set(group['positive'])|set(group['negative'])):
            pos=group['positive'][pattern];neg=group['negative'][pattern]
            # Smoothed relative occurrence rates; both denominators are
            # necessary because false-edge samples outnumber true edges.
            strength=math.log(((pos+1)/(p+2))/((neg+1)/(n+2)))
            observed=(pos>=min_observations and strength>=MIN_ENRICHMENT or
                      neg>=min_observations and strength<=-MIN_ENRICHMENT)
            if observed:
                learned[pattern]=round(max(-2.,min(2.,strength)),6)
            details['|'.join(pattern)]={'positive':pos,'negative':neg,
                                       'log_enrichment':round(strength,6),'retained':observed}
        evidence['|'.join(key)]={'positive_samples':p,'negative_samples':n,
                                'retained_patterns':len(learned),'patterns':details}
        weights[key]=learned
    return weights,evidence


def context_signals(features,parent_edges,weights):
    """Frozen one-pass witnesses: base G2-0005 edges, never repaired edges."""
    motifs=context_patterns(parent_edges,[(src,dst,rel) for src,dst,rel,*_ in features])
    signals={}
    for src,dst,rel,*_ in features:
        edge=(src,dst,rel)
        learned=weights.get((rel,src[2],dst[2]),{})
        scores=[learned[p] for p in motifs.get(edge,()) if p in learned]
        signals[edge]=(max((x for x in scores if x>0),default=0.),
                       max((-x for x in scores if x<0),default=0.))
    return signals


def select_contextual(features,parent_rule,weights,*,positive_bonus,negative_penalty,precomputed=None):
    """No functional cardinality caps, forced edges, or iterative closure."""
    density.select_edges([],parent_rule) # strict validation of frozen rule
    for param in (positive_bonus,negative_penalty):
        if type(param) not in (int,float) or not math.isfinite(param) or param<0 or param>2:
            raise ValueError('GAMER_G2_0007_BAD_CONTEXT_RULE')
    if precomputed is None:
        parent=density.select_edges(features,parent_rule)
        motif=context_signals(features,parent,weights)
    else:
        parent,motif=precomputed
    threshold=math.log(parent_rule['minimum_confidence']/(1-parent_rule['minimum_confidence']))
    accepted=set();changes=Counter()
    for feature in features:
        if len(feature)!=7 or feature[2] not in BINDINGS:
            raise ValueError('GAMER_G2_0007_BAD_CANDIDATE')
        src,dst,rel,conf,odds,count,delta=feature
        if type(count) is not int or count<1 or not all(math.isfinite(v) for v in (conf,odds,delta)):
            raise ValueError('GAMER_G2_0007_BAD_CANDIDATE_VALUE')
        edge=(src,dst,rel)
        support,contradiction=motif[edge]
        prior=odds-parent_rule['density_penalty']*math.log1p(count-1)-parent_rule['competition_penalty']*delta
        score=prior+positive_bonus*support-negative_penalty*contradiction
        if not math.isfinite(score):raise ValueError('GAMER_G2_0007_NONFINITE_SCORE')
        if support:changes['supported_candidates']+=1
        if contradiction:changes['contradicted_candidates']+=1
        if score>=threshold:
            accepted.add(edge)
            if len(accepted)>MAX_EDGES:raise ValueError('GAMER_G2_0007_EDGE_LIMIT')
    changes['edges_added']=len(accepted-parent)
    changes['edges_removed']=len(parent-accepted)
    changes['parent_edges']=len(parent)
    return accepted,dict(changes)


def verify_graph6_parent(parent_dir,sixth,expected_hashes,train_constraints_hash):
    """Recompute every G2-0006 saved metric and verify no repair was promoted."""
    folder=Path(parent_dir);report_path=folder/graph6.REPORT;cases_path=folder/graph6.CASES
    report=json.loads(report_path.read_text(encoding='utf-8'))
    required={'schema':graph6.SCHEMA,'parent_selected_rule':PARENT_ARM,
              'g1_frozen':True,'g2_0004_rank_weights_frozen':True,'g2_0005_rule_frozen':True,
              'trainable_parameters':0,'production_promotion':False,'graph_execution_authorized':False,
              'certification_authorized':False,'g2_certified':False,'g2_production_unlocked':False,
              'challenge_or_test_split_used':False,'sealed_test_used':False,
              'inference_uses_predicted_g1_only':True,'parent_g2_0005_replay_pass':True,
              'selected_rule':None,'selected_independent_screen_pass':False,
              'independent_seed':graph6.SEED,'independent_cases':len(sixth),
              'independent_dataset_sha256':sha_rows(sixth),'g1_development_candidate':base.G1_ARM,
              'g1_head_sha256':base.G1_HEAD_SHA,
              'functional_train_evidence_sha256':train_constraints_hash}
    for name,value in required.items():
        if type(report.get(name)) is not type(value) or report[name]!=value:
            raise ValueError('GAMER_G2_0007_G2_0006_CONTRACT:'+name)
    if report.get('parent_hashes')!=expected_hashes:
        raise ValueError('GAMER_G2_0007_G2_0006_LINEAGE')
    if report.get('repair_variants')!={name:{'margin':margin,'support':support}
                                      for name,(margin,support) in graph6.VARIANTS.items()}:
        raise ValueError('GAMER_G2_0007_G2_0006_RULES')
    cases=[json.loads(line) for line in cases_path.read_text(encoding='utf-8').splitlines() if line.strip()]
    cal=report.get('calibration_cases')
    if type(cal) is not int or cal<1 or len(cases)!=cal+len(sixth):
        raise ValueError('GAMER_G2_0007_G2_0006_CASE_COUNT')
    names={'g2_0005_frozen_selected',*graph6.VARIANTS}
    for i,row in enumerate(cases):
        group='previously_inspected_calibration' if i<cal else 'new_independent_validation'
        index=i+1 if i<cal else i-cal+1
        if row.get('group')!=group or row.get('index')!=index or set(row.get('configurations',{}))!=names:
            raise ValueError('GAMER_G2_0007_G2_0006_CASE_SCHEMA')
        if i>=cal:
            old=sixth[i-cal]
            if (row.get('family')!=old['family'] or row.get('prompt_sha256')!=base._sha(old['prompt']) or
                row.get('gold_binding_edges')!=len(base.gold_binding_edges(old))):
                raise ValueError('GAMER_G2_0007_G2_0006_CASE_IDENTITY')
    for field,subset in (('calibration_metrics',cases[:cal]),('independent_metrics',cases[cal:])):
        computed={name:base.metrics([{**r['configurations'][name],'family':r['family']} for r in subset])
                  for name in names}
        if computed!=report.get(field):raise ValueError('GAMER_G2_0007_G2_0006_METRICS:'+field)
    selected,selection=graph6.choose_rule(report['calibration_metrics'])
    if selected is not None or report.get('calibration_selection')!=selection:
        raise ValueError('GAMER_G2_0007_G2_0006_SELECTION')
    return report,cases[cal:],{'g2_0006_report_sha256':backbone.sha256(report_path),
                              'g2_0006_cases_sha256':backbone.sha256(cases_path)}


def evaluate(cached,head,allowed,weights,*,device,group,parent_rule):
    metrics=defaultdict(list);saved=[]
    for item in cached:
        logits=relational.full_logits(head,item['vectors'].to(device)).detach().cpu()
        features=density.candidates(item['anchors'],logits,allowed)
        parent=density.select_edges(features,parent_rule)
        shared_context=(parent,context_signals(features,parent,weights))
        scored={'g2_0005_frozen_selected':sel.score_set(parent,item['gold'])}
        diagnostics={};deltas={}
        for name,(pos,neg) in VARIANTS.items():
            edges,diag=select_contextual(features,parent_rule,weights,
                                         positive_bonus=pos,negative_penalty=neg,precomputed=shared_context)
            scored[name]=sel.score_set(edges,item['gold']);diagnostics[name]=diag
            deltas[name]={'recovered_gold':len((edges-parent)&item['gold']),
                          'lost_gold':len((parent-edges)&item['gold']),
                          'false_edges_added':len((edges-parent)-item['gold']),
                          'false_edges_removed':len((parent-edges)-item['gold'])}
        for name,row in scored.items():metrics[name].append({**row,'family':item['family']})
        saved.append({'group':group,'index':item['index'],'family':item['family'],
                      'prompt_sha256':item['prompt_sha256'],'g1_predicted_nodes':len(item['anchors']),
                      'gold_binding_edges':len(item['gold']),'density_bucket':density._bucket(len(item['anchors'])),
                      'configurations':scored,'context_audit':diagnostics,'comparison_to_parent':deltas})
    return {name:base.metrics(rows) for name,rows in metrics.items()},saved


def choose_rule(calibration):
    parent=calibration['g2_0005_frozen_selected'];floor=max(.60,parent['recall']-.02)
    eligible=[name for name in VARIANTS
              if calibration[name]['recall']>=floor and
              calibration[name]['false_positive']<=parent['false_positive'] and
              calibration[name]['f1']>parent['f1']]
    if not eligible:
        return None,{'recall_floor':floor,'eligible_count':0,
                     'reason':'NO_CONTEXT_RULE_BEATS_FROZEN_PARENT'}
    best=max(eligible,key=lambda name:(calibration[name]['f1'],calibration[name]['recall'],
                                       -calibration[name]['false_positive'],name))
    return best,{'recall_floor':floor,'eligible_count':len(eligible),
                 'reason':'MAX_CALIBRATION_F1_WITH_PARENT_NONREGRESSION'}


def aggregate_deltas(rows):
    return {name:{key:sum(row['comparison_to_parent'][name][key] for row in rows)
                  for key in ('recovered_gold','lost_gold','false_edges_added','false_edges_removed')}
            for name in VARIANTS}


def run(*,root,g1_validation_report,g2_run,selection_run,discrimination_run,
        relational_run,density_run,consistency_run,output,device='cuda',
        max_length=2048,validation_limit=VALIDATION_LIMIT):
    root=Path(root);output=Path(output)
    if output.exists():raise FileExistsError('GAMER_G2_0007_IMMUTABLE_OUTPUT')
    head,manifest,dataset,bootstrap,_,_,hashes=sel.load_parents(root,g1_validation_report,g2_run)
    train=base.original_training_rows(dataset,relational.TRAIN_LIMIT)
    if base._sha('\n'.join(base._sha(x['prompt']) for x in train))!=bootstrap.get('train_prompt_hashes_sha256'):
        raise ValueError('GAMER_G2_0007_TRAIN_REPLAY')
    first=base.novel_dev_rows(train,limit=relational.CALIBRATION_LIMIT,seed=base.SEED)
    if density.sha_rows(first)!=bootstrap.get('dev_dataset_sha256'):
        raise ValueError('GAMER_G2_0007_BOOTSTRAP_DEV_REPLAY')
    second=sel.new_validation_rows(train,first,limit=sel.VALIDATION_LIMIT)
    third=disc.new_validation_rows(train,first,second,limit=disc.VALIDATION_LIMIT)
    fourth=relational.new_validation_rows(train,first,second,third,limit=relational.VALIDATION_LIMIT)
    fifth=density.new_validation_rows(train,first,second,third,fourth,limit=density.VALIDATION_LIMIT)
    sixth=graph6.new_validation_rows(train,first,second,third,fourth,fifth,limit=graph6.VALIDATION_LIMIT)
    allowed=sel.compatibility_from_train(train)
    learned,motif_audit=learn_motifs(train,allowed)
    functional,train_evidence=graph6.learn_constraints(train,allowed)
    selection_dir=Path(selection_run)
    selection=json.loads((selection_dir/sel.REPORT).read_text(encoding='utf-8'))
    if (selection.get('schema')!=sel.SCHEMA or selection.get('g2_parent_hashes')!=hashes or
        selection.get('validation_dataset_sha256')!=density.sha_rows(second) or
        selection.get('compatibility_sha256')!=sel._hash_json([list(x) for x in sorted(allowed)]) or
        selection.get('production_promotion') is not False):
        raise ValueError('GAMER_G2_0007_G2_0002_DRIFT')
    early={**hashes,'g2_0002_report_sha256':backbone.sha256(selection_dir/sel.REPORT),
              'g2_0002_cases_sha256':backbone.sha256(selection_dir/sel.CASES)}
    _,disc_hashes=relational.verify_discrimination_parent(discrimination_run,third,early)
    bootstrap_head=deepcopy(head)
    _,fourth_saved,fourth_hashes=density.verify_parent(relational_run,fourth,head,
                                                      {**hashes,**disc_hashes},allowed)
    fifth_report,_,fifth_hashes=graph6.verify_density_parent(density_run,fifth,
                                                  {**hashes,**disc_hashes,**fourth_hashes})
    sixth_report,sixth_saved,sixth_hashes=verify_graph6_parent(consistency_run,sixth,
                       {**hashes,**disc_hashes,**fourth_hashes,**fifth_hashes},
                       sel._hash_json(train_evidence))
    fresh=new_validation_rows(train,first,second,third,fourth,fifth,sixth,limit=validation_limit)
    parent_rule=fifth_report['fixed_decoder_rules'][PARENT_ARM]
    head=head.to(device).eval().requires_grad_(False)
    bootstrap_head=bootstrap_head.to(device).eval().requires_grad_(False)
    before={key:v.detach().cpu().clone() for key,v in head.state_dict().items()}
    model,tok=backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    if any(p.requires_grad for p in model.parameters()):
        raise ValueError('GAMER_G2_0007_BACKBONE_UNFROZEN')
    calibration=relational.capture(bootstrap_head,model,tok,sixth,device=device,
                                   max_length=max_length,group='g2_0006_replay')
    validation=relational.capture(bootstrap_head,model,tok,fresh,device=device,
                                  max_length=max_length,group='new_independent')
    with torch.inference_mode():
        if len(calibration)!=len(sixth_saved):raise ValueError('GAMER_G2_0007_REPLAY_COUNT')
        for item,old in zip(calibration,sixth_saved):
            if (item['index']!=old['index'] or item['family']!=old['family'] or
                item['prompt_sha256']!=old['prompt_sha256'] or
                len(item['anchors'])!=old['g1_predicted_nodes'] or
                len(item['gold'])!=old['gold_binding_edges']):
                raise ValueError('GAMER_G2_0007_REPLAY_IDENTITY')
            logits=relational.full_logits(head,item['vectors'].to(device)).detach().cpu()
            features=density.candidates(item['anchors'],logits,allowed)
            if sel.score_set(density.select_edges(features,parent_rule),item['gold'])!=old['configurations']['g2_0005_frozen_selected']:
                raise ValueError('GAMER_G2_0007_REPLAY_PARENT_PREDICTIONS')
            for name,(margin,support) in graph6.VARIANTS.items():
                edges,_=graph6.graph_decode(features,parent_rule,functional,margin=margin,support=support)
                if sel.score_set(edges,item['gold'])!=old['configurations'][name]:
                    raise ValueError('GAMER_G2_0007_REPLAY_GRAPH6_PREDICTIONS:'+name)
        calibration_metrics,cal_saved=evaluate(calibration,head,allowed,learned,device=device,
                                              group='previously_inspected_calibration',parent_rule=parent_rule)
        selected,select_info=choose_rule(calibration_metrics)
        independent_metrics,ind_saved=evaluate(validation,head,allowed,learned,device=device,
                                              group='new_independent_validation',parent_rule=parent_rule)
    relational.validate_original_state(head,before)
    parent=independent_metrics['g2_0005_frozen_selected']
    gates={name:{'f1_at_least_parent':independent_metrics[name]['f1']>=parent['f1'],
                 'false_positives_at_most_parent':independent_metrics[name]['false_positive']<=parent['false_positive'],
                 'recall_at_least_parent_minus_002':independent_metrics[name]['recall']>=parent['recall']-.02,
                 'recall_at_least_060':independent_metrics[name]['recall']>=.60,
                 'no_gold_labels_as_inference_inputs':True}
           for name in VARIANTS}
    chosen=gates.get(selected)
    report={'schema':SCHEMA,'status':'G2_CONTEXTUAL_GRAPH_DEV_NOT_CERTIFIED',
            'g1_frozen':True,'g1_development_candidate':base.G1_ARM,
            'g2_0004_rank_weights_frozen':True,'g2_0005_rule_frozen':True,'trainable_parameters':0,
            'g2_certified':False,'g2_production_unlocked':False,'certification_authorized':False,
            'production_promotion':False,'graph_execution_authorized':False,'memory_writes':False,
            'sealed_test_used':False,'challenge_or_test_split_used':False,
            'inference_uses_predicted_g1_only':True,
            'gold_used_for_train_motif_learning_calibration_and_offline_scoring_only':True,
            'parent_g2_0005_screen_pass':True,'parent_g2_0006_replay_pass':True,
            'parent_g2_0006_screen_pass':False,'g1_head_sha256':manifest['head_sha256'],
            'parent_hashes':{**hashes,**disc_hashes,**fourth_hashes,**fifth_hashes,**sixth_hashes},
            'train_motif_audit_sha256':sel._hash_json(motif_audit),
            'train_motif_rules':{'minimum_observations':MIN_PATTERN_OBSERVATIONS,
                                 'minimum_abs_enrichment':MIN_ENRICHMENT},
            'fixed_decoder_variants':{name:{'positive_bonus':p,'negative_penalty':n}
                                      for name,(p,n) in VARIANTS.items()},
            'calibration_cases':len(sixth),'calibration_seed':graph6.SEED,
            'calibration_dataset_sha256':sha_rows(sixth),
            'independent_cases':len(fresh),'independent_seed':SEED,
            'independent_dataset_sha256':sha_rows(fresh),
            'selected_rule':selected,'calibration_selection':select_info,
            'calibration_metrics':calibration_metrics,'independent_metrics':independent_metrics,
            'independent_gates_by_rule':gates,
            'selected_independent_screen_pass':bool(chosen and all(chosen.values())),
            'calibration_delta_vs_parent':aggregate_deltas(cal_saved),
            'independent_delta_vs_parent':aggregate_deltas(ind_saved),
            'independent_by_node_density':density.bucket_metrics(ind_saved,
                                     ('g2_0005_frozen_selected',*VARIANTS)),
            'no_fixed_cardinality':True,'no_iterative_completion':True,
            'no_oracle_graph_context':True}
    output.mkdir(parents=True)
    (output/REPORT).write_text(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    (output/CASES).write_bytes(base._jsonl(cal_saved+ind_saved))
    (output/MOTIFS).write_text(json.dumps(motif_audit,sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    return report
