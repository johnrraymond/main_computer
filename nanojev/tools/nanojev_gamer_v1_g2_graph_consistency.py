"""G2-0006: frozen graph-wide consistency and evidence-bounded completion.

G1-1.25 and G2-0004 weights remain frozen. The G2-0005 selected density
rule is immutable; TRAIN-only evidence establishes *directional* functional
binding constraints. New edges must come from frozen positive-class neural
scores and must have existing, non-oracle graph support. No transitivity,
forced completion, game-specific rules, production promotion, or gold input to
the decoder. G2-0005's already inspected holdout calibrates the variants;
a fresh, prompt-disjoint DEV cohort measures the fixed decision.
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
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools.nanojev_gamer_v1_graph_contract import BINDINGS, MAX_EDGES

SCHEMA='nanojev.gamer.graph.g2.graph-consistency-and-completion.v1'
REPORT='g2_graph_consistency_report.json'
CASES='g2_graph_consistency_per_case.jsonl'
CONSTRAINTS='g2_graph_consistency_train_constraints.json'
SEED=360006
VALIDATION_LIMIT=320
PARENT_ARM='density_p50_d15_c35'
MIN_SUPPORT=32

# Variant names are fixed before inspecting the new holdout. Recovery thresholds
# are offsets from the frozen G2-0005 adjusted log-odds, not oracle labels.
VARIANTS={
    'functional_only':(0.,'none'),
    'functional_one_end_015':(.15,'one'),
    'functional_both_015':(.15,'both'),
    'functional_both_030':(.30,'both'),
    'functional_one_end_030':(.30,'one'),
}


def sha_rows(rows):
    return hashlib.sha256(base._jsonl(rows)).hexdigest()


def new_validation_rows(train,first,second,third,fourth,fifth,*,limit=VALIDATION_LIMIT,seed=SEED):
    previous=train+first+second+third+fourth+fifth
    if limit<2 or limit%2 or not all((train,first,second,third,fourth,fifth)):
        raise ValueError('GAMER_G2_0006_BAD_COHORT')
    rows=base.novel_dev_rows(previous,limit=limit,seed=seed)
    prior_hash={base._sha(r['prompt']) for r in previous}
    hashes=[base._sha(r['prompt']) for r in rows]
    if len(hashes)!=len(set(hashes)) or prior_hash.intersection(hashes):
        raise ValueError('GAMER_G2_0006_PROMPT_LEAKAGE')
    if Counter(r['family'] for r in rows)!={'encounter':limit//2,'allocation':limit//2}:
        raise ValueError('GAMER_G2_0006_UNBALANCED_HOLDOUT')
    return rows


def learn_constraints(train,allowed,*,min_support=MIN_SUPPORT):
    """Only observed TRAIN functional directionality, never a universal cap.

    A direction is functional only if at least min_support distinct endpoint
    observations for a typed relation exist, *none* have degree >1, and no
    train row violates it. A nonfunctional direction is entirely unconstrained.
    """
    if not train or any(r.get('split')!='train' for r in train):
        raise ValueError('GAMER_G2_0006_CONSTRAINTS_TRAIN_ONLY')
    if type(min_support) is not int or min_support<1:
        raise ValueError('GAMER_G2_0006_BAD_SUPPORT')
    samples=defaultdict(lambda:{'src':[],'dst':[]})
    for row in train:
        nodes={n['id']:n for n in row['graph']['nodes']}
        outgoing=defaultdict(set);incoming=defaultdict(set)
        for e in row['graph']['edges']:
            if e['type'] not in BINDINGS:continue
            key=(e['type'],nodes[e['src']]['kind'],nodes[e['dst']]['kind'])
            if key not in allowed:
                raise ValueError('GAMER_G2_0006_UNEXPECTED_TRAIN_RELATION')
            outgoing[(key,e['src'])].add(e['dst'])
            incoming[(key,e['dst'])].add(e['src'])
        for (key,_),dest in outgoing.items():samples[key]['src'].append(len(dest))
        for (key,_),sources in incoming.items():samples[key]['dst'].append(len(sources))
    functional=set();evidence={}
    for key in sorted(allowed):
        kinds=samples[key];data={}
        for direction in ('src','dst'):
            degrees=kinds[direction]
            data[direction]={'observations':len(degrees),'maximum':max(degrees,default=0),
                             'functional':len(degrees)>=min_support and max(degrees,default=0)==1}
            if data[direction]['functional']:functional.add((key,direction))
        evidence['|'.join(key)]=data
    return frozenset(functional),evidence


def _adjusted(feature,rule):
    src,dst,rel,conf,odds,count,delta=feature
    return odds-rule['density_penalty']*math.log1p(count-1)-rule['competition_penalty']*delta


def graph_decode(features,rule,functional,*,margin=0.,support='none'):
    """Globally resolve demonstrated functional conflicts, then optional repair.

    A repair proposal must have positive-class frozen neural support, be close
    to the existing acceptance threshold, and connect to established graph
    evidence. Every addition remains subject to functional compatibility.
    """
    if support not in ('none','one','both') or type(margin) not in (int,float) or not math.isfinite(margin) or margin<0 or margin>.5:
        raise ValueError('GAMER_G2_0006_BAD_REPAIR_RULE')
    if set(rule)!={'minimum_confidence','density_penalty','competition_penalty'}:
        raise ValueError('GAMER_G2_0006_BAD_PARENT_RULE')
    density.select_edges([],rule)  # validates the frozen parent decoder rule
    threshold=math.log(rule['minimum_confidence']/(1-rule['minimum_confidence']))
    options=[]
    for row in features:
        if len(row)!=7 or row[2] not in BINDINGS or row[5]<1:
            raise ValueError('GAMER_G2_0006_BAD_CANDIDATE')
        score=_adjusted(row,rule)
        if not math.isfinite(score):raise ValueError('GAMER_G2_0006_NONFINITE_CANDIDATE')
        edge=(row[0],row[1],row[2]);key=(row[2],row[0][2],row[1][2])
        options.append((score,edge,key))
    options.sort(key=lambda x:(-x[0],x[1]))
    accepted=set();used_src=set();used_dst=set();pair_used=set();occupied=Counter()
    audit={'parent_candidates':sum(v[0]>=threshold for v in options),
           'functional_conflicts_removed':0,'near_threshold_considered':0,
           'near_threshold_added':0}

    def admissible(edge,key):
        src,dst,rel=edge
        return ((src,dst) not in pair_used and
                ((key,'src') not in functional or (key,src) not in used_src) and
                ((key,'dst') not in functional or (key,dst) not in used_dst))

    def accept(edge,key):
        accepted.add(edge);pair_used.add((edge[0],edge[1]))
        used_src.add((key,edge[0]));used_dst.add((key,edge[1]))
        occupied[edge[0]]+=1;occupied[edge[1]]+=1
        if len(accepted)>MAX_EDGES:raise ValueError('GAMER_G2_0006_EDGE_LIMIT')

    for score,edge,key in options:
        if score<threshold:continue
        if admissible(edge,key):accept(edge,key)
        else:audit['functional_conflicts_removed']+=1
    if support!='none' and margin>0:
        # One fixed pass: an added edge cannot become evidence authorizing the
        # next addition. This prevents runaway self-reinforcing completions.
        supported=frozenset(occupied)
        for score,edge,key in options:
            if score>=threshold or score<threshold-margin:continue
            audit['near_threshold_considered']+=1
            endpoints=int(edge[0] in supported)+int(edge[1] in supported)
            if endpoints < (2 if support=='both' else 1):continue
            if not admissible(edge,key):continue
            accept(edge,key);audit['near_threshold_added']+=1
    return accepted,audit


def verify_density_parent(parent_dir,fifth,expected_hashes):
    """Verify all 640 G2-0005 cases, every metric, and selected decoder."""
    directory=Path(parent_dir);report_path=directory/density.REPORT;cases_path=directory/density.CASES
    report=json.loads(report_path.read_text(encoding='utf-8'))
    required={'schema':density.SCHEMA,'g1_frozen':True,
              'g2_0004_rank_weights_frozen':True,'g2_certified':False,
              'g2_production_unlocked':False,'certification_authorized':False,
              'production_promotion':False,'graph_execution_authorized':False,
              'memory_writes':False,'sealed_test_used':False,
              'challenge_or_test_split_used':False,'inference_uses_predicted_g1_only':True,
              'gold_used_for_calibration_and_offline_scoring_only':True,
              'trainable_parameters':0,'no_fixed_edge_cap':True,
              'parent_g2_0004_replay_pass':True,'parent_g2_0004_screen_pass':False,
              'selected_independent_screen_pass':True,'selected_rule':PARENT_ARM,
              'independent_seed':density.SEED,'independent_cases':len(fifth),
              'independent_dataset_sha256':sha_rows(fifth),
              'g1_development_candidate':base.G1_ARM,
              'g1_head_sha256':base.G1_HEAD_SHA}
    for name,value in required.items():
        if type(report.get(name)) is not type(value) or report[name]!=value:
            raise ValueError('GAMER_G2_0006_PARENT_CONTRACT:'+name)
    if report.get('parent_hashes')!=expected_hashes:
        raise ValueError('GAMER_G2_0006_PARENT_LINEAGE')
    rules=density.decoder_rules()
    if report.get('fixed_decoder_rules')!=rules:
        raise ValueError('GAMER_G2_0006_PARENT_RULES')
    saved=[json.loads(x) for x in cases_path.read_text(encoding='utf-8').splitlines() if x.strip()]
    calibration_size=report.get('calibration_cases')
    if type(calibration_size) is not int or calibration_size<1 or len(saved)!=calibration_size+len(fifth):
        raise ValueError('GAMER_G2_0006_PARENT_CASE_COUNT')
    expected_names=set(report.get('independent_metrics',{}))
    if expected_names!=set(report.get('calibration_metrics',{})) or expected_names!=set(rules)|set(density.BASE_RULES)|{'g2_0001_argmax_050','g2_0001_compatible_050'}:
        raise ValueError('GAMER_G2_0006_PARENT_NAMES')
    for i,r in enumerate(saved):
        group='previously_inspected_calibration' if i<calibration_size else 'new_independent_validation'
        index=i+1 if i<calibration_size else i-calibration_size+1
        if r.get('group')!=group or r.get('index')!=index or set(r.get('configurations',{}))!=expected_names:
            raise ValueError('GAMER_G2_0006_PARENT_CASE_SCHEMA')
        if i>=calibration_size:
            row=fifth[i-calibration_size]
            if (r.get('family')!=row['family'] or
                r.get('prompt_sha256')!=base._sha(row['prompt']) or
                r.get('gold_binding_edges')!=len(base.gold_binding_edges(row))):
                raise ValueError('GAMER_G2_0006_PARENT_CASE_IDENTITY')
    for key,group in (('calibration_metrics',saved[:calibration_size]),('independent_metrics',saved[calibration_size:])):
        calculated={name:base.metrics([{**r['configurations'][name],'family':r['family']} for r in group])
                    for name in expected_names}
        if calculated!=report[key]:raise ValueError('GAMER_G2_0006_PARENT_METRICS:'+key)
    chosen,why=density.choose_rule(report['calibration_metrics'],report['calibration_metrics']['g2_0001_argmax_050'])
    if chosen!=PARENT_ARM or why!=report.get('calibration_selection'):
        raise ValueError('GAMER_G2_0006_PARENT_SELECTION')
    computed_gates={name:sel.validation_gate(report['independent_metrics'][name],report['independent_metrics']['g2_0001_argmax_050']) for name in rules}
    if computed_gates!=report.get('independent_gates_by_rule') or not all(computed_gates[PARENT_ARM].values()):
        raise ValueError('GAMER_G2_0006_PARENT_GATES')
    return report,saved[calibration_size:],{
        'g2_0005_report_sha256':backbone.sha256(report_path),
        'g2_0005_cases_sha256':backbone.sha256(cases_path)}


def evaluate(cached,head,allowed,functional,*,device,group,parent_rule):
    results=defaultdict(list);saved=[]
    for item in cached:
        logits=relational.full_logits(head,item['vectors'].to(device)).detach().cpu()
        features=density.candidates(item['anchors'],logits,allowed)
        parent_edges=density.select_edges(features,parent_rule)
        scored={'g2_0005_frozen_selected':sel.score_set(parent_edges,item['gold'])}
        audits={};deltas={}
        for name,(margin,support) in VARIANTS.items():
            edges,diag=graph_decode(features,parent_rule,functional,margin=margin,support=support)
            scored[name]=sel.score_set(edges,item['gold']);audits[name]=diag
            deltas[name]={'recovered_gold':len((edges-parent_edges)&item['gold']),
                          'lost_gold':len((parent_edges-edges)&item['gold']),
                          'false_edges_added':len((edges-parent_edges)-item['gold']),
                          'false_edges_removed':len((parent_edges-edges)-item['gold'])}
        for name,row in scored.items():results[name].append({**row,'family':item['family']})
        saved.append({'group':group,'index':item['index'],'family':item['family'],
                      'prompt_sha256':item['prompt_sha256'],'g1_predicted_nodes':len(item['anchors']),
                      'gold_binding_edges':len(item['gold']),'density_bucket':density._bucket(len(item['anchors'])),
                      'configurations':scored,'consistency_audit':audits,'comparison_to_parent':deltas})
    return {name:base.metrics(rows) for name,rows in results.items()},saved


def choose_rule(calibration):
    parent=calibration['g2_0005_frozen_selected']
    floor=max(.60,parent['recall']-.02)
    eligible=[name for name in VARIANTS
              if calibration[name]['recall']>=floor and
              calibration[name]['false_positive']<=parent['false_positive'] and
              calibration[name]['f1']>parent['f1']]
    if not eligible:return None,{'recall_floor':floor,'eligible_count':0,
                                  'reason':'NO_GRAPH_REPAIR_BEATS_FROZEN_PARENT'}
    best=max(eligible,key=lambda name:(calibration[name]['f1'],calibration[name]['recall'],
                                       -calibration[name]['false_positive'],name))
    return best,{'recall_floor':floor,'eligible_count':len(eligible),
                 'reason':'MAX_CALIBRATION_F1_WITH_PARENT_NONREGRESSION'}


def aggregate_deltas(cases):
    return {name:{field:sum(c['comparison_to_parent'][name][field] for c in cases)
                  for field in ('recovered_gold','lost_gold','false_edges_added','false_edges_removed')}
            for name in VARIANTS}


def run(*,root,g1_validation_report,g2_run,selection_run,discrimination_run,
        relational_run,density_run,output,device='cuda',max_length=2048,validation_limit=VALIDATION_LIMIT):
    root=Path(root);output=Path(output)
    if output.exists():raise FileExistsError('GAMER_G2_0006_IMMUTABLE_OUTPUT')
    head,manifest,dataset,bootstrap,_,_,hashes=sel.load_parents(root,g1_validation_report,g2_run)
    train=base.original_training_rows(dataset,relational.TRAIN_LIMIT)
    if base._sha('\n'.join(base._sha(r['prompt']) for r in train))!=bootstrap.get('train_prompt_hashes_sha256'):
        raise ValueError('GAMER_G2_0006_TRAIN_REPLAY')
    first=base.novel_dev_rows(train,limit=relational.CALIBRATION_LIMIT,seed=base.SEED)
    if density.sha_rows(first)!=bootstrap.get('dev_dataset_sha256'):
        raise ValueError('GAMER_G2_0006_BOOTSTRAP_DEV_REPLAY')
    second=sel.new_validation_rows(train,first,limit=sel.VALIDATION_LIMIT)
    third=disc.new_validation_rows(train,first,second,limit=disc.VALIDATION_LIMIT)
    fourth=relational.new_validation_rows(train,first,second,third,limit=relational.VALIDATION_LIMIT)
    fifth=density.new_validation_rows(train,first,second,third,fourth,limit=density.VALIDATION_LIMIT)
    allowed=sel.compatibility_from_train(train)
    functional,train_evidence=learn_constraints(train,allowed)
    selection_dir=Path(selection_run)
    selection=json.loads((selection_dir/sel.REPORT).read_text(encoding='utf-8'))
    if (selection.get('schema')!=sel.SCHEMA or selection.get('g2_parent_hashes')!=hashes or
        selection.get('validation_dataset_sha256')!=density.sha_rows(second) or
        selection.get('compatibility_sha256')!=sel._hash_json([list(x) for x in sorted(allowed)]) or
        selection.get('production_promotion') is not False):
        raise ValueError('GAMER_G2_0006_G2_0002_DRIFT')
    old_hashes={**hashes,'g2_0002_report_sha256':backbone.sha256(selection_dir/sel.REPORT),
                      'g2_0002_cases_sha256':backbone.sha256(selection_dir/sel.CASES)}
    _,disc_hashes=relational.verify_discrimination_parent(discrimination_run,third,old_hashes)
    bootstrap_head=deepcopy(head)
    _,fourth_saved,fourth_hashes=density.verify_parent(relational_run,fourth,head,
                                                       {**hashes,**disc_hashes},allowed)
    fifth_report,fifth_saved,fifth_hashes=verify_density_parent(density_run,fifth,
                                                        {**hashes,**disc_hashes,**fourth_hashes})
    fresh=new_validation_rows(train,first,second,third,fourth,fifth,limit=validation_limit)
    parent_rule=fifth_report['fixed_decoder_rules'][PARENT_ARM]
    head=head.to(device).eval().requires_grad_(False)
    bootstrap_head=bootstrap_head.to(device).eval().requires_grad_(False)
    before={key:v.detach().cpu().clone() for key,v in head.state_dict().items()}
    model,tok=backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    if any(p.requires_grad for p in model.parameters()):
        raise ValueError('GAMER_G2_0006_BACKBONE_UNFROZEN')
    calibration=relational.capture(bootstrap_head,model,tok,fifth,device=device,
                                   max_length=max_length,group='g2_0005_replay')
    validation=relational.capture(bootstrap_head,model,tok,fresh,device=device,
                                  max_length=max_length,group='new_independent')
    with torch.inference_mode():
        if len(calibration)!=len(fifth_saved):raise ValueError('GAMER_G2_0006_REPLAY_COUNT')
        for item,old in zip(calibration,fifth_saved):
            if (item['index']!=old['index'] or item['family']!=old['family'] or
                item['prompt_sha256']!=old['prompt_sha256'] or
                len(item['anchors'])!=old['g1_predicted_nodes'] or
                len(item['gold'])!=old['gold_binding_edges']):
                raise ValueError('GAMER_G2_0006_REPLAY_IDENTITY')
            logits=relational.full_logits(head,item['vectors'].to(device)).detach().cpu()
            features=density.candidates(item['anchors'],logits,allowed)
            for name,rule in fifth_report['fixed_decoder_rules'].items():
                actual=sel.score_set(density.select_edges(features,rule),item['gold'])
                if old['configurations'][name]!=actual:
                    raise ValueError('GAMER_G2_0006_REPLAY_PREDICTIONS:'+name)
            for name in density.BASE_RULES:
                actual=sel.score_set(sel.choose_edges(item['anchors'],logits,
                                             relational.candidate_rules()[name],allowed),item['gold'])
                if old['configurations'][name]!=actual:
                    raise ValueError('GAMER_G2_0006_REPLAY_RANK_CONTROL:'+name)
            for name in ('argmax_050','compatible_050'):
                actual=sel.score_set(sel.choose_edges(item['anchors'],item['logits'],
                                            sel.configurations()[0][name],allowed),item['gold'])
                if old['configurations']['g2_0001_'+name]!=actual:
                    raise ValueError('GAMER_G2_0006_REPLAY_BOOTSTRAP:'+name)
        calibration_metrics,cal_saved=evaluate(calibration,head,allowed,functional,device=device,
                                               group='previously_inspected_calibration',parent_rule=parent_rule)
        selected,select_info=choose_rule(calibration_metrics)
        independent_metrics,independent_saved=evaluate(validation,head,allowed,functional,device=device,
                                               group='new_independent_validation',parent_rule=parent_rule)
    relational.validate_original_state(head,before)
    parent_independent=independent_metrics['g2_0005_frozen_selected']
    gates={name:{'f1_at_least_parent':independent_metrics[name]['f1']>=parent_independent['f1'],
                 'false_positives_at_most_parent':independent_metrics[name]['false_positive']<=parent_independent['false_positive'],
                 'recall_at_least_parent_minus_002':independent_metrics[name]['recall']>=parent_independent['recall']-.02,
                 'recall_at_least_060':independent_metrics[name]['recall']>=.60,
                 'no_gold_labels_as_inference_inputs':True}
           for name in VARIANTS}
    chosen_gate=gates.get(selected)
    report={'schema':SCHEMA,'status':'G2_GRAPH_CONSISTENCY_DEVELOPMENT_NOT_CERTIFIED',
            'g1_frozen':True,'g1_development_candidate':base.G1_ARM,
            'g2_0004_rank_weights_frozen':True,'g2_0005_rule_frozen':True,'trainable_parameters':0,
            'g2_certified':False,'g2_production_unlocked':False,'certification_authorized':False,
            'production_promotion':False,'graph_execution_authorized':False,'memory_writes':False,
            'sealed_test_used':False,'challenge_or_test_split_used':False,
            'inference_uses_predicted_g1_only':True,
            'gold_used_for_training_constraints_calibration_and_offline_scoring_only':True,
            'parent_g2_0005_screen_pass':True,'parent_g2_0005_replay_pass':True,
            'parent_hashes':{**hashes,**disc_hashes,**fourth_hashes,**fifth_hashes},
            'g1_head_sha256':manifest['head_sha256'],
            'train_functional_constraints':sorted([list(k)+[direction] for k,direction in functional]),
            'functional_train_evidence_sha256':sel._hash_json(train_evidence),
            'parent_selected_rule':PARENT_ARM,'repair_variants':{name:{'margin':x,'support':y} for name,(x,y) in VARIANTS.items()},
            'calibration_cases':len(fifth),'calibration_seed':density.SEED,
            'calibration_dataset_sha256':density.sha_rows(fifth),
            'independent_cases':len(fresh),'independent_seed':SEED,
            'independent_dataset_sha256':sha_rows(fresh),
            'selected_rule':selected,'calibration_selection':select_info,
            'calibration_metrics':calibration_metrics,'independent_metrics':independent_metrics,
            'independent_gates_by_rule':gates,
            'selected_independent_screen_pass':bool(chosen_gate and all(chosen_gate.values())),
            'calibration_delta_vs_parent':aggregate_deltas(cal_saved),
            'independent_delta_vs_parent':aggregate_deltas(independent_saved),
            'independent_by_node_density':density.bucket_metrics(independent_saved,
                                  ('g2_0005_frozen_selected',*VARIANTS)),
            'no_arbitrary_binding_caps':True,'no_forced_completion':True}
    output.mkdir(parents=True)
    (output/REPORT).write_text(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    (output/CASES).write_bytes(base._jsonl(cal_saved+independent_saved))
    (output/CONSTRAINTS).write_text(json.dumps(train_evidence,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    return report
