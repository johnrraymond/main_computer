"""G2-0034: frozen-model, label-separated causal graph failure diagnosis.

No training, calibration, policy search or graph promotion. Gold is read only
AFTER every frozen prediction for candidate and diagnostic oracle measurements.
The inventory oracle is an upper bound; ranking-count tests are diagnostic
counterfactuals, not deployable models.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import gzip
import json
import math

import numpy as np
import torch
from safetensors.torch import load_file

from tools import nanojev_gamer_v1_g2_structured_graph_decoder as x33

x32=x33.x32
x31=x33.x31
p30=x33.p30
base=x33.base
backbone=x33.backbone
density=x33.density
general16=x33.general16
source13=x33.source13

SCHEMA='nanojev.gamer.graph.g2.causal-graph-diagnosis.v1'
REPORT='g2_causal_graph_diagnosis_report.json'
LEDGER='g2_causal_graph_diagnosis_per_case.jsonl.gz'
SEEDS=x33.SEEDS
SEED=460434
MAX_LENGTH=x33.MAX_LENGTH
NEW=('new_independent_dev_0034','new_topology_dev_0034')
REPLAY=x33.NEW
# Fixed ablations, never selected on development labels. All use the SAME
# trained G2-0033 contextual edge logits and candidate inventory.
ABLATIONS=(('edge_only',0.,0.),('group_only',.7,0.),
           ('global_only',0.,.30),('joint',.7,.30))


def sha_rows(rows):
    return density.sha_rows(rows)


def verify_parent33(directory,*,train33,fit33,audit33,old_dev,new_dev,ancestors,
                    training_sha):
    """Verify actual G2-0033 artifacts and development case identities.

    Caller must reconstruct cohorts independently from the original lineage.
    File hashes alone are insufficient: check all saved graph metrics as well.
    """
    d=Path(directory)
    rp,lp,pp,wp=(d/name for name in (x33.REPORT,x33.LEDGER,x33.POLICIES,x33.WEIGHTS))
    report=json.loads(rp.read_text(encoding='utf-8'))
    policy=json.loads(pp.read_text(encoding='utf-8'))
    required={'schema':x33.SCHEMA,
        'status':'STRUCTURED_GRAPH_DECODER_DEV_NOT_CERTIFIED',
        'parent_hashes':ancestors,'train_dataset_sha256':training_sha,
        'set_train_sha256':sha_rows(train33),
        'calibration_fit_sha256':sha_rows(fit33),
        'calibration_audit_sha256':sha_rows(audit33),
        'datasets_sha256':{k:sha_rows(v) for k,v in {**old_dev,**new_dev}.items()},
        'frozen_backbone':True,'frozen_g1':True,'frozen_g2_rank_head':True,
        'frozen_g2_0031_occurrence_graph':True,
        'frozen_g2_0032_completeness':True,
        'train_only_set_supervision':True,'train_only_policy_selection':True,
        'candidate_selection_uses_gold':False,
        'dev_labels_used_for_training':False,
        'dev_labels_used_for_calibration':False,
        'dev_checkpoint_selection':False,'sealed_test_used':False,
        'production_promotion':False,'g2_certified':False,
        'development_edge_diagnostics_only':True}
    for k,v in required.items():
        if report.get(k)!=v:
            raise ValueError('GAMER_G2_0034_PARENT_0033_CONTRACT:'+k)
    for field,path in (('ledger_sha256',lp),('policies_sha256',pp),
                       ('development_weights_sha256',wp)):
        if report.get(field)!=backbone.sha256(path):
            raise ValueError('GAMER_G2_0034_PARENT_0033_HASH:'+field)
    if (report.get('policies')!=policy or
            set(policy)!={f'structured_set_s{s}' for s in SEEDS} or
            any(v['source']!=f'occurrence_graph_s{s}' or
                v.get('fallback') not in (f'{arm}_s{s}' for arm in x32.ARMS) or
                v['group_weight']<0 or v['global_weight']<0
                for s in SEEDS for v in [policy[f'structured_set_s{s}']])):
        raise ValueError('GAMER_G2_0034_PARENT_0033_POLICIES')
    if report['gates']!=x33.gates(report['evaluation']['new_0033_dev'],
                                  report['by_split'][x33.NEW[1]],policy):
        raise ValueError('GAMER_G2_0034_PARENT_0033_GATES')
    cohorts={'train_calibration_fit_0033':fit33,
             'train_calibration_audit_0033':audit33,**old_dev,**new_dev}
    found={k:[] for k in cohorts}
    with gzip.open(lp,'rt',encoding='utf-8') as inp:
        for line in inp:
            row=json.loads(line);split=row.get('group')
            if split not in found or len(found[split])>=len(cohorts[split]):
                raise ValueError('GAMER_G2_0034_PARENT_0033_ROW_COUNT')
            origin=cohorts[split][len(found[split])]
            if (row.get('index')!=len(found[split])+1 or
                    row.get('prompt_sha256')!=base._sha(origin['prompt']) or
                    row.get('family')!=origin['family'] or
                    row.get('gold_total')!=len(base.gold_binding_edges(origin)) or
                    row.get('structural_topology')!=origin.get('structural_topology','native')):
                raise ValueError('GAMER_G2_0034_PARENT_0033_ROW_IDENTITY')
            if (split in new_dev) != ('edge_diagnostics' in row):
                raise ValueError('GAMER_G2_0034_PARENT_0033_DIAGNOSTICS_SCOPE')
            found[split].append(row)
    if any(len(found[k])!=len(v) for k,v in cohorts.items()):
        raise ValueError('GAMER_G2_0034_PARENT_0033_ROW_COUNT')
    if report['calibration_fit']!=p30.aggregate(found['train_calibration_fit_0033']):
        raise ValueError('GAMER_G2_0034_PARENT_0033_FIT_AGGREGATE')
    if report['calibration_audit']!=p30.aggregate(found['train_calibration_audit_0033']):
        raise ValueError('GAMER_G2_0034_PARENT_0033_AUDIT_AGGREGATE')
    for split in {**old_dev,**new_dev}:
        if report['by_split'][split]!=p30.aggregate(found[split]):
            raise ValueError('GAMER_G2_0034_PARENT_0033_SPLIT_AGGREGATE:'+split)
    for name,splits in (('new_0033_dev',x33.NEW),('replayed_0032_dev',x33.REPLAY)):
        if report['evaluation'][name]!=p30.aggregate([r for k in splits for r in found[k]]):
            raise ValueError('GAMER_G2_0034_PARENT_0033_EVALUATION:'+name)
    tensors=load_file(str(wp),device='cpu')
    expected=set()
    for seed in SEEDS:
        template=x33.GraphSetEnergy()
        for name,v in template.state_dict().items():
            key=f'structured_set_s{seed}.{name}'
            expected.add(key)
            if (key not in tensors or tensors[key].shape!=v.shape or
                    not torch.isfinite(tensors[key]).all()):
                raise ValueError('GAMER_G2_0034_PARENT_0033_WEIGHTS:'+key)
    if set(tensors)!=expected:
        raise ValueError('GAMER_G2_0034_PARENT_0033_EXTRA_WEIGHTS')
    hashes={f'g2_0033_{name}_sha256':backbone.sha256(p) for name,p in
            (('report',rp),('ledger',lp),('policies',pp),('weights',wp))}
    return report,policy,found,tensors,hashes


def load_parent33_models(tensors,device='cpu'):
    models={}
    for seed in SEEDS:
        net=x33.GraphSetEnergy()
        prefix=f'structured_set_s{seed}.'
        net.load_state_dict({k[len(prefix):]:v for k,v in tensors.items()
                             if k.startswith(prefix)},strict=True)
        models[seed]=net.to(device).eval().requires_grad_(False)
    return models


def fresh_cohorts(state,cal30,dev30,cal31,dev31,train32,fit32,audit32,dev32,
                  train33,fit33,audit33,dev33):
    """Two independent, prompt-disjoint DEV cohorts; no TRAIN or policy fitting."""
    previous=[*state['training'],*state['history29'],
              *(r for v in state['fresh29'].values() for r in v),
              *cal30,*(r for v in dev30.values() for r in v),
              *cal31,*(r for v in dev31.values() for r in v),
              *train32,*fit32,*audit32,*(r for v in dev32.values() for r in v),
              *train33,*fit33,*audit33,*(r for v in dev33.values() for r in v)]
    previous=list({base._sha(r['prompt']):r for r in previous}.values())
    independent=general16.independent_rows((previous,),limit=320,seed=SEED)
    topology=general16.heldout_topology_rows((previous,independent),limit=128,
                                             seed=SEED+1)
    combined=[*previous,*independent,*topology]
    if (len(independent)!=320 or len(topology)!=128 or
            len({base._sha(r['prompt']) for r in combined})!=len(combined) or
            any(r.get('split')!='dev' for r in (*independent,*topology)) or
            any(r.get('structural_topology') in {'2x4','4x2','4x4'}
                for r in independent) or
            {r['structural_topology'] for r in topology}!={'2x4','4x2','4x4'}):
        raise ValueError('GAMER_G2_0034_COHORT_LEAKAGE')
    return dict(zip(NEW,(independent,topology)))


def score_rank_oracles(item, scores):
    """DIAGNOSTIC ONLY: rank given oracle counts of represented gold edges.

    The oracle knows the number of gold *candidate* edges globally or in every
    source/relation group. This is label-dependent and MUST NOT be a policy.
    """
    edges=item['edges'];gold=item['gold'];available=set(edges)
    if len(available)!=len(edges) or len(scores)!=len(edges) or not np.isfinite(scores).all():
        raise ValueError('GAMER_G2_0034_RANK_ORACLE_INPUT')
    k=len(available & gold)
    ranked=sorted(range(len(edges)),key=lambda i:(-float(scores[i]),edges[i]))
    global_set={edges[i] for i in ranked[:min(k,source13.MAX_EDGES)]}
    buckets=defaultdict(list)
    for i,g in enumerate(item['groups']):buckets[int(g)].append(i)
    group_set=set()
    for ids in buckets.values():
        n=sum(edges[i] in gold for i in ids)
        ids.sort(key=lambda i:(-float(scores[i]),edges[i]))
        group_set.update(edges[i] for i in ids[:n])
    # Source13's output cap is an independent constraint: if the counterfactual
    # requires more edges than allowed, this graph cannot be emitted exactly.
    if len(group_set)>source13.MAX_EDGES:
        group_set=set(sorted(group_set,key=lambda e:
            (-float(scores[edges.index(e)]),e))[:source13.MAX_EDGES])
    return {'global_gold_candidate_count':global_set,
            'group_gold_candidate_counts':group_set}


def candidate_ceiling(item,anchors):
    """Separate unmatched G1 endpoints from inventory omissions after G1."""
    available=set(item['edges']);gold=item['gold'];nodes=set(anchors)
    missing=gold-available
    absent=[e for e in missing if e[0] not in nodes or e[1] not in nodes]
    present=[e for e in missing if e[0] in nodes and e[1] in nodes]
    represented=gold & available
    can_emit=len(gold)<=source13.MAX_EDGES
    return {'candidate_complete':not missing,
            'output_budget_sufficient':can_emit,
            'fully_representable':not missing and can_emit,
            'gold_edges':len(gold), 'represented_gold':len(represented),
            'missing_candidate_gold':len(missing),
            'missing_g1_endpoint':len(absent),
            'endpoints_present_but_not_in_inventory':len(present),
            'candidate_oracle':represented if can_emit else set(sorted(represented)[:source13.MAX_EDGES])}


def fixed_ablations(item,features,pred,source):
    """Evaluate predeclared count-potential settings without choosing a winner."""
    outcomes={}
    for label,group_weight,global_weight in ABLATIONS:
        config={'enabled':True,'source':source,'group_weight':group_weight,
                'global_weight':global_weight}
        outcomes[label]=x33.decode(item,features,pred,config)
    return outcomes


def detail_case(item,anchors,models,pol33,parent_policies,policy32,nets32):
    """One frozen forward pass per seed; gold consulted for diagnostics AFTER.

    Calls to x33.predictions and x33.decode are label-blind. The only oracle
    is score_rank_oracles, whose results are never fed back into prediction.
    """
    # Inference first. No gold label, authored text, oracle count, or correct
    # answer reaches any scorer, ablation decoder or frozen policy.
    frozen={}
    for seed in SEEDS:
        name=f'structured_set_s{seed}';source=f'occurrence_graph_s{seed}'
        feats=x33.set_features(item,source,parent_policies[source],len(anchors))
        pred=x33.predictions(feats,models[seed])
        ablations=fixed_ablations(item,feats,pred,source)
        if pol33[name]['enabled']:
            selected=x33.decode(item,feats,pred,pol33[name])
        else:
            selected=x32.select_complete(item,source,parent_policies[source],
                          len(anchors),nets32[seed],policy32[pol33[name]['fallback']])
        frozen[seed]=(pred[0],selected,ablations)
    # Gold is consulted exclusively below, after ALL predictions are frozen.
    ceiling=candidate_ceiling(item,anchors)
    output={};models_metrics={}
    inventory=ceiling.pop('candidate_oracle')
    competing=ceiling['represented_gold']
    # Ranking-oracle success means score ordering suffices if exact represented
    # edge cardinalities are supplied by an impossible (gold-informed) oracle.
    for seed in SEEDS:
        name=f'structured_set_s{seed}';source=f'occurrence_graph_s{seed}'
        logits,selected,ablations=frozen[seed]
        oracle=score_rank_oracles(item,logits)
        # Even for disabled policies, scorer-only and oracle diagnoses inspect
        # the trained decoder; the inherited fallback stays the measured control.
        old_source_scores=np.asarray(item['raw'][source],dtype=np.float64)
        old_oracle=score_rank_oracles(item,old_source_scores)
        model_selections={name:selected,
            f'oracle_global_count_s{seed}':oracle['global_gold_candidate_count'],
            f'oracle_group_counts_s{seed}':oracle['group_gold_candidate_counts'],
            f'oracle_parent_group_counts_s{seed}':old_oracle['group_gold_candidate_counts'],
            **{f'ablation_{k}_s{seed}':v for k,v in ablations.items()}}
        for model,edges in model_selections.items():
            score=(old_source_scores if (model==f'oracle_parent_group_counts_s{seed}' or
                    (model==name and not pol33[name]['enabled'])) else logits)
            n,failures=p30.ranking_counts(item,score)
            models_metrics[model]={**p30.measure(edges,item),'competing_gold':n,
                                   'ranking_failures':failures}
        if not ceiling['output_budget_sufficient']:
            attribution='output_budget_limit'
        elif not ceiling['candidate_complete']:
            attribution='candidate_inventory_limit'
        elif oracle['group_gold_candidate_counts']!=item['gold']:
            attribution='relationship_ranking_limit'
        elif selected!=item['gold']:
            attribution='decoder_selection_limit'
        else:
            attribution='exact_graph'
        # Bound logging to important failed positives and competing negatives,
        # not the entire candidate inventory; retain the complete source scores
        # in memory for all oracle computations above.
        diagnostics=[]
        in_group=defaultdict(list)
        for i,g in enumerate(item['groups']):in_group[int(g)].append(i)
        for i,edge in enumerate(item['edges']):
            if edge not in item['gold'] or edge in selected:continue
            indices=in_group[int(item['groups'][i])]
            toughest=sorted((j for j in indices if item['edges'][j] not in item['gold']),
                           key=lambda j:(-float(logits[j]),item['edges'][j]))[:2]
            diagnostics.append({'edge':edge,'score':float(logits[i]),
                'rank_in_group':1+sum(float(logits[j])>float(logits[i]) for j in indices),
                'selected_by_frozen':False,
                'highest_false_competitors':[
                    {'edge':item['edges'][j],'score':float(logits[j])} for j in toughest]})
        diagnostics.sort(key=lambda v:(-v['score'],v['edge']))
        output[name]={'attribution':attribution,
            'structured_policy_enabled':bool(pol33[name]['enabled']),
            'oracle_global_exact':int(oracle['global_gold_candidate_count']==item['gold']),
            'oracle_group_exact':int(oracle['group_gold_candidate_counts']==item['gold']),
            'parent_rank_group_exact':int(old_oracle['group_gold_candidate_counts']==item['gold']),
            'selected_exact':int(selected==item['gold']),
            'would_repair_with_gold_group_counts':bool(
                oracle['group_gold_candidate_counts']==item['gold'] and selected!=item['gold']),
            'selected_false_positive':len(selected-item['gold']),
            'selected_missing':len(item['gold']-selected),
            'top_missing_gold':diagnostics[:12]}
    models_metrics['candidate_inventory_oracle']={**p30.measure(inventory,item),
                    'competing_gold':competing,'ranking_failures':0}
    return ceiling,output,models_metrics


def summarize_causality(rows):
    """Fixed, independent attribution counts; no winner or policy selection."""
    result={'cases':len(rows),'fully_representable':0,'candidate_complete':0,
            'missing_g1_endpoint':0,'endpoints_present_but_not_in_inventory':0,
            'output_budget_limited':0,'models':{}}
    for seed in SEEDS:
        result['models'][f'structured_set_s{seed}']={
            'attribution':{},'fully_representable':0,'exact_on_fully_representable':0,
            'gold_group_counts_oracle_exact_on_fully_representable':0,
            'gold_global_count_oracle_exact_on_fully_representable':0,
            'old_source_group_count_oracle_exact_on_fully_representable':0,
            'correctable_decoder_failures':0}
    for row in rows:
        ceiling=row['candidate_diagnosis']
        result['fully_representable']+=int(ceiling['fully_representable'])
        result['candidate_complete']+=int(ceiling['candidate_complete'])
        result['missing_g1_endpoint']+=ceiling['missing_g1_endpoint']
        result['endpoints_present_but_not_in_inventory']+=ceiling['endpoints_present_but_not_in_inventory']
        result['output_budget_limited']+=int(not ceiling['output_budget_sufficient'])
        for name,part in row['causal_diagnosis'].items():
            stats=result['models'][name]
            category=part['attribution']
            stats['attribution'][category]=stats['attribution'].get(category,0)+1
            if ceiling['fully_representable']:
                stats['fully_representable']+=1
                stats['exact_on_fully_representable']+=part['selected_exact']
                stats['gold_group_counts_oracle_exact_on_fully_representable']+=part['oracle_group_exact']
                stats['gold_global_count_oracle_exact_on_fully_representable']+=part['oracle_global_exact']
                stats['old_source_group_count_oracle_exact_on_fully_representable']+=part['parent_rank_group_exact']
                stats['correctable_decoder_failures']+=int(part['would_repair_with_gold_group_counts'])
    return result


def run(*,precision30_run,representation31_run,completeness32_run,
        structured33_run,output,device='cuda',max_length=MAX_LENGTH,**kwargs):
    if max_length!=MAX_LENGTH:
        raise ValueError('GAMER_G2_0034_FIXED_PROTOCOL')
    if Path(output).exists():
        raise FileExistsError('GAMER_G2_0034_IMMUTABLE_OUTPUT')
    # Unchanged ancestry setup. The earlier fixed train-step argument is needed
    # solely to pass the frozen G2-0030 preparation contract; NO optimizer runs.
    state=p30._prepare(output=output,device=device,max_length=max_length,
                       steps=p30.STEPS,**kwargs)
    cal30,dev30=p30.calibration_and_dev(state['history29'],state['fresh29'])
    _,pol30,_,hash30=x31.verify_parent30(precision30_run,state['ancestors'],
        sha_rows(state['training']),cal30,{**state['fresh29'],**dev30})
    cal31,dev31=x31.fresh_cohorts(state,cal30,dev30)
    ancestors31={**state['ancestors'],**hash30}
    _,pol31,_,tensors31,hash31=x32.verify_parent31(
        representation31_run,old_cal=cal30,old_dev=dev30,cal31=cal31,
        dev31=dev31,ancestors=ancestors31,training_sha=sha_rows(state['training']))
    (train32,fit32,audit32),dev32=x32.fresh_cohorts(state,cal30,dev30,cal31,dev31)
    ancestors32={**ancestors31,**hash31}
    _,pol32,_,tensors32,hash32=x33.verify_parent32(completeness32_run,
        train32=train32,fit32=fit32,audit32=audit32,
        dev32={**dev31,**dev32},ancestors=ancestors32,
        training_sha=sha_rows(state['training']))
    (train33,fit33,audit33),dev33=x33.fresh_cohorts(state,cal30,dev30,cal31,
        dev31,train32,fit32,audit32,dev32)
    ancestors33={**ancestors32,**hash32}
    report33,pol33,prior33,tensors33,hash33=verify_parent33(
        structured33_run,train33=train33,fit33=fit33,audit33=audit33,
        old_dev=dev32,new_dev=dev33,ancestors=ancestors33,
        training_sha=sha_rows(state['training']))
    models31=x32.load_parent_models(tensors31,device=device)
    models32=x33.load_parent32_models(tensors32,device=device)
    models33=load_parent33_models(tensors33,device=device)
    parent_policies={'calibrated_reference_0030':pol30['calibrated_reference'],
        'calibrated_reference_0031':pol31['calibrated_reference'],
        'predicate_argument_0030':pol30[x31.CHAMPION],
        **{k:pol31[k] for k in x32.SOURCE_NAMES}}
    fresh=fresh_cohorts(state,cal30,dev30,cal31,dev31,train32,fit32,audit32,
                        dev32,train33,fit33,audit33,dev33)
    evaluated={}
    for split,rows in {**dev33,**fresh}.items():
        collected=[]
        for start in range(0,len(rows),20):
            block=rows[start:start+20]
            captured=x31.capture_occurrences(state['head'],state['model'],
                state['tokenizer'],block,device=device,max_length=max_length,group=split)
            if len(captured)!=len(block):
                raise ValueError('GAMER_G2_0034_CAPTURE_COUNT')
            for offset,(row,case) in enumerate(zip(block,captured),start+1):
                ex=x31.example_from_case(case,row,state,train=False,device=device)
                item=x31.score_case(ex,state,models31)
                item['parent_policies']=parent_policies
                # Verify the full frozen 0033 prediction before any oracle.
                control=x33.evaluate_case(item,len(case['anchors']),row,split,offset,
                    parent_policies,pol32,models32,models33,pol33)
                if split in dev33:
                    expected=prior33[split][offset-1]
                    for k in ('group','index','prompt_sha256','family',
                              'structural_topology','gold_total','candidate_gold',
                              'candidate_count','metrics'):
                        if control.get(k)!=expected.get(k):
                            raise ValueError('GAMER_G2_0034_PARENT_0033_LIVE_REPLAY_DRIFT:'+split+':'+str(offset)+':'+k)
                ceiling,diagnostics,metrics=detail_case(item,case['anchors'],models33,
                    pol33,parent_policies,pol32,models32)
                # Structured prediction is run twice independently here to
                # detect non-deterministic or gold-influenced diagnostic paths.
                for seed in SEEDS:
                    key=f'structured_set_s{seed}'
                    if metrics[key]!=control['metrics'][key]:
                        raise ValueError('GAMER_G2_0034_STRUCTURED_REPLAY_DRIFT:'+key)
                collected.append({**control,'metrics':{**control['metrics'],**metrics},
                    'candidate_diagnosis':ceiling,'causal_diagnosis':diagnostics})
            print(f'GAMER_G2_0034_CAPTURE {split} '
                  f'{min(start+20,len(rows))}/{len(rows)}',flush=True)
        evaluated[split]=collected
    x31.rel.validate_original_state(state['head'],state['head_before'])
    if any(not torch.equal(v,state['reference'].state_dict()[k].detach().cpu())
           for k,v in state['reference_before'].items()):
        raise ValueError('GAMER_G2_0034_FROZEN_REFERENCE_MUTATION')
    if any(p.requires_grad for p in state['model'].parameters()) or any(
            p.requires_grad for model in models31.values() for net in model.values()
            for p in net.parameters()) or any(
            p.requires_grad for pair in models32.values() for net in pair
            for p in net.parameters()) or any(
            p.requires_grad for net in models33.values() for p in net.parameters()):
        raise ValueError('GAMER_G2_0034_FROZEN_MODEL_MUTATION')
    replay=[r for split in REPLAY for r in evaluated[split]]
    new=[r for split in NEW for r in evaluated[split]]
    fresh_metrics=p30.aggregate(new)
    ood_metrics=p30.aggregate(evaluated[NEW[1]])
    report={'schema':SCHEMA,'status':'FROZEN_CAUSAL_DIAGNOSIS_DEV_NOT_CERTIFIED',
        'parent_hashes':{**ancestors33,**hash33},
        'train_dataset_sha256':sha_rows(state['training']),
        'datasets_sha256':{k:sha_rows(v) for k,v in {**dev33,**fresh}.items()},
        'predeclared':{'seeds':list(SEEDS),'new_independent_dev':320,
            'new_withheld_topology_dev':128,
            'fixed_decoder_ablations':[{ 'name':label,'group_weight':group,
                'global_weight':glob} for label,group,glob in ABLATIONS],
            'diagnostic_rank_oracles_use_gold_counts':True,
            'fixed_candidates_top24':True,'no_policy_selection':True},
        'frozen_backbone':True,'frozen_g1':True,
        'frozen_g2_0031_occurrence_graph':True,
        'frozen_g2_0032_completeness':True,'frozen_g2_0033_set_decoder':True,
        'new_optimizer_steps':0,'new_train_cases':0,
        'dev_labels_used_for_training':False,'dev_labels_used_for_calibration':False,
        'dev_checkpoint_selection':False,'diagnostic_oracles_non_deployable':True,
        'sealed_test_used':False,'production_promotion':False,
        'g2_certified':False,'graph_execution_authorized':False,
        'replay_passed':True,
        'evaluation':{'new_0034_dev':fresh_metrics,
                      'replayed_0033_dev':p30.aggregate(replay)},
        'by_split':{k:p30.aggregate(v) for k,v in evaluated.items()},
        'causal_by_cohort':{'new_0034_dev':summarize_causality(new),
            'replayed_0033_dev':summarize_causality(replay)},
        'causal_by_split':{k:summarize_causality(v) for k,v in evaluated.items()},
        'limitations':{'rank_oracles_require_gold_cardinality':True,
            'candidate_inventory_oracle_is_upper_bound':True,
            'ranking_count_oracles_not_general_upper_bounds':True,
            'missing_endpoint_classification_requires_exact_span_equality':True,
            'endpoints_present_candidate_loss_not_uniquely_top24':True,
            'unchanged_G1_and_candidate_inventory':True,
            'no_production_model_or_policy_selected':True,
            'fixed_decoder_ablation_weights_not_calibrated':True}}
    out=Path(output);out.mkdir(parents=True,exist_ok=False)
    x32._write_ledger(out/LEDGER,[*replay,*new])
    report['ledger_sha256']=backbone.sha256(out/LEDGER)
    (out/REPORT).write_text(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n',
                            encoding='utf-8')
    return report
