"""G2-0014: TRAIN-only crowded specialist and calibration-frozen density routing.

A frozen G2-0013 joint scorer supplies small-graph candidates; the crowded
specialist is fine-tuned only on TRAIN graph examples, with source/relation
hard negatives and distant wrong destinations. Medium graphs use the robust
G2-0010 parent unchanged. All routing and cutoffs are selected using G2-0013's
already inspected independent development cohort, before a fresh disjoint DEV
cohort is scored. Nothing is certified or used for graph execution.
"""
from __future__ import annotations

from collections import Counter,defaultdict
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import math
import random

import torch
from torch.nn import functional as F
from safetensors.torch import load_file,save_file

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
from tools.nanojev_gamer_v1_graph_contract import MAX_EDGES

SCHEMA='nanojev.gamer.graph.g2.density-specialization.v1'
REPORT='g2_density_specialization_report.json'
CASES='g2_density_specialization_per_case.jsonl'
WEIGHTS='g2_density_specialization_development.safetensors'
SEED=440014
CALIBRATION_LIMIT=source13.VALIDATION_LIMIT
VALIDATION_LIMIT=320
TRAIN_STEPS=200
TRAIN_LIMIT=source13.TRAIN_LIMIT
LR=1.e-4
PARENT=robust11.SELECTED
BUCKET_SMALL='small_1_24'
BUCKET_MEDIUM='medium_25_48'
BUCKET_CROWDED='crowded_49_128'
SMALL_POLICIES=('fallback','joint_50','joint_60','joint_70')
CROWD_POLICIES=('fallback','prune_50','prune_60','prune_70','prune_80',
                'rescue_90','rescue_95')
MIN_CROWDED=30
GLOBAL_RECALL_LOSS=.01
CROWDED_RECALL_LOSS=.02


def fresh_rows(predecessors,*,limit=VALIDATION_LIMIT,seed=SEED):
    if (not predecessors or not all(predecessors) or type(limit) is not int or
            limit<2 or limit%2 or type(seed) is not int):
        raise ValueError('GAMER_G2_0014_BAD_HOLDOUT')
    rows=base.novel_dev_rows([row for cohort in predecessors for row in cohort],
                             limit=limit,seed=seed)
    old={base._sha(row['prompt']) for cohort in predecessors for row in cohort}
    hashes=[base._sha(row['prompt']) for row in rows]
    if (len(set(hashes))!=limit or set(hashes)&old or
        Counter(row['family'] for row in rows)!={'encounter':limit//2,'allocation':limit//2}):
        raise ValueError('GAMER_G2_0014_HOLDOUT_LEAKAGE')
    return rows


def verify_source13_parent(directory,calibration,expected_hashes):
    """Verify immutable weights and ALL saved case-level development metrics."""
    directory=Path(directory)
    rp=directory/source13.REPORT;cp=directory/source13.CASES;wp=directory/source13.WEIGHTS
    report=json.loads(rp.read_text(encoding='utf-8'))
    required={'schema':source13.SCHEMA,'status':'G2_SOURCE_CONDITIONED_DEV_NOT_CERTIFIED',
              'selected_rule':None,'g1_frozen':True,'g2_0004_rank_weights_frozen':True,
              'g2_0010_rule_frozen':True,'g2_0011_robustness_verified':True,
              'g2_0012_diagnosis_verified':True,'g2_certified':False,
              'g2_production_unlocked':False,'production_promotion':False,
              'certification_authorized':False,'graph_execution_authorized':False,
              'sealed_test_used':False,'challenge_or_test_split_used':False,
              'no_gold_in_inference':True,'source_conditioned_multi_positive':True,
              'hard_negative_same_source_relation':True,'train_only_gold_candidate_injection':True,
              'calibration_selection':{'eligible_count':0,
               'reason':'NO_SOURCE_CONDITIONED_RULE_PASSES_CALIBRATION'},
              'candidate_top_k':source13.TOP_K,'g1_head_sha256':base.G1_HEAD_SHA,
              'independent_cases':len(calibration),
              'independent_dataset_sha256':density.sha_rows(calibration),
              'independent_screen_pass':False}
    for key,value in required.items():
        if type(report.get(key)) is not type(value) or report[key]!=value:
            raise ValueError('GAMER_G2_0014_PARENT_CONTRACT:'+key)
    if report.get('parent_hashes')!=expected_hashes:
        raise ValueError('GAMER_G2_0014_PARENT_LINEAGE')
    if report.get('new_weights_sha256')!=backbone.sha256(wp):
        raise ValueError('GAMER_G2_0014_PARENT_WEIGHTS')
    records=[json.loads(line) for line in cp.read_text(encoding='utf-8').splitlines() if line.strip()]
    if len(records)!=report['calibration_cases']+len(calibration):
        raise ValueError('GAMER_G2_0014_PARENT_CASE_COUNT')
    names=set(report['independent_metrics'])
    previous=records[:report['calibration_cases']]
    for index,(row,old) in enumerate(zip(calibration,records[len(previous):]),1):
        if (old.get('group')!='new_independent_validation' or old.get('index')!=index or
            old.get('family')!=row['family'] or old.get('prompt_sha256')!=base._sha(row['prompt']) or
            old.get('gold_binding_edges')!=len(base.gold_binding_edges(row)) or
            set(old.get('configurations',{}))!=names):
            raise ValueError('GAMER_G2_0014_PARENT_CASE_IDENTITY')
    if (len(previous)!=report['calibration_cases'] or
        any(rec.get('group')!='previously_inspected_calibration' for rec in previous)):
        raise ValueError('GAMER_G2_0014_PARENT_CALIBRATION_RECORDS')
    for subset,key,bucket_key in ((previous,'calibration_metrics','calibration_by_node_density'),
                                  (records[len(previous):],'independent_metrics','independent_by_node_density')):
        recalculated={name:base.metrics([{**r['configurations'][name],'family':r['family']} for r in subset])
                      for name in names}
        if recalculated!=report[key] or source13.bucket_metrics(subset)!=report[bucket_key]:
            raise ValueError('GAMER_G2_0014_PARENT_METRICS')
    return report,records[len(previous):],{
        'g2_0013_report_sha256':backbone.sha256(rp),
        'g2_0013_cases_sha256':backbone.sha256(cp),
        'g2_0013_weights_sha256':backbone.sha256(wp)}


def load_source13_models(path,width,expected_sha):
    path=Path(path)
    if backbone.sha256(path)!=expected_sha:
        raise ValueError('GAMER_G2_0014_PARENT_WEIGHT_HASH')
    data=load_file(str(path),device='cpu')
    models={}
    for prefix in ('joint','control'):
        model=source13.SourceConditionedScorer(width)
        weights={k[len(prefix)+1:]:v for k,v in data.items() if k.startswith(prefix+'.')}
        if (set(weights)!=set(model.state_dict()) or
            any(weights[k].shape!=v.shape for k,v in model.state_dict().items())):
            raise ValueError('GAMER_G2_0014_PARENT_WEIGHT_SHAPE:'+prefix)
        model.load_state_dict(weights,strict=True)
        models[prefix]=model.eval().requires_grad_(False)
    return models


def hard_crowded_groups(inv,gold,*,seed,max_groups=8,max_negatives=18):
    """TRAIN labels only. Sample multi-positive hard competitors and distant FPs."""
    grouped=defaultdict(list)
    for i,key in enumerate(inv['groups']):grouped[key].append(i)
    eligible=[]
    for key,indices in grouped.items():
        positive=[i for i in indices if inv['edges'][i] in gold]
        wrong=sorted((i for i in indices if inv['edges'][i] not in gold),
                     key=lambda i:(-float(inv['base'][i]),i))
        if not positive or not wrong:continue
        distant=[i for i in wrong if abs(inv['edges'][i][0][0]-inv['edges'][i][1][0])>128]
        near=[i for i in wrong if i not in set(distant)]
        # The strongest false destination in each source/relation group is
        # always included, even if several destinations are actually correct.
        chosen=set(positive);chosen.add(wrong[0])
        chosen.update(distant[:max_negatives//2]);chosen.update(near[:max_negatives//2])
        chosen.update(wrong[:max_negatives])
        eligible.append((key,sorted(chosen)))
    rng=random.Random(seed)
    return rng.sample(eligible,min(max_groups,len(eligible)))


def train_specialists(specialist,matched,items,*,steps=TRAIN_STEPS,lr=LR,device='cpu',seed=SEED):
    """Matched fine-tuning; only difference is distant-negative weighting."""
    if type(steps) is not int or steps<1 or not math.isfinite(lr) or lr<=0:
        raise ValueError('GAMER_G2_0014_BAD_TRAIN')
    crowded=[row for row in items if density._bucket(len(row['anchors']))==BUCKET_CROWDED]
    if len(crowded)<4 or not all(row['family']=='encounter' for row in crowded):
        # The corpus currently has crowded encounter examples; if it changes,
        # refuse to silently train a specialist on sparse graphs.
        raise ValueError('GAMER_G2_0014_INSUFFICIENT_TRAIN_CROWDED')
    prepared=[]
    for row in crowded:
        inv=source13.make_inventory(row,row['rank'],row['allowed'],train_gold=True)
        prepared.append((row,inv))
    if not any(hard_crowded_groups(inv,row['gold'],seed=seed) for row,inv in prepared):
        raise ValueError('GAMER_G2_0014_NO_TRAIN_HARD_NEGATIVES')
    nets=(specialist.to(device).train(),matched.to(device).train())
    opts=[torch.optim.AdamW(net.parameters(),lr=lr) for net in nets]
    rng=random.Random(seed);count=Counter();last={}
    for step in range(steps):
        batches=[[],[]]
        for _ in range(3):
            row,inv=rng.choice(prepared)
            sampled=hard_crowded_groups(inv,row['gold'],seed=seed+step,max_groups=7)
            if not sampled:continue
            ids=sorted({i for _,group in sampled for i in group},key=lambda i:(inv['groups'][i],i))
            edges=[inv['edges'][i] for i in ids]
            x=inv['features'][ids].to(device);b=inv['base'][ids].to(device)
            gs=[inv['groups'][i] for i in ids]
            y=torch.tensor([float(edge in row['gold']) for edge in edges],device=device)
            far=torch.tensor([float(abs(e[0][0]-e[1][0])>128) for e in edges],device=device)
            null=torch.tensor([float(inv['null_argmax'][i]) for i in ids],device=device)
            count['positive']+=int(y.sum());count['negative']+=len(ids)-int(y.sum())
            count['distant_negative']+=int(((y<.5)&(far>.5)).sum())
            count['null_positive']+=int(((y>.5)&(null>.5)).sum())
            for k,net in enumerate(nets):
                logits=net(x,b,gs,with_competition=True)
                weight=torch.where(y>.5,8.*(1.+null),
                      (1.+2.*far) if k==0 else torch.ones_like(y))
                bce=(F.binary_cross_entropy_with_logits(logits,y,reduction='none')*weight).mean()
                rank_loss=[]
                for g in set(gs):
                    indices=[j for j,key in enumerate(gs) if key==g]
                    pos=logits[indices][y[indices]>.5]
                    neg=logits[indices][y[indices]<.5]
                    if len(pos) and len(neg):
                        rank_loss.append(F.softplus(.75-pos[:,None]+neg[None,:]).mean())
                batches[k].append(bce+(.6*torch.stack(rank_loss).mean() if rank_loss else 0.))
        if not all(batches):raise ValueError('GAMER_G2_0014_EMPTY_TRAIN_STEP')
        for k,(net,opt,parts) in enumerate(zip(nets,opts,batches)):
            opt.zero_grad(set_to_none=True)
            loss=torch.stack(parts).mean()
            if not torch.isfinite(loss):raise ValueError('GAMER_G2_0014_NONFINITE_TRAIN')
            loss.backward();torch.nn.utils.clip_grad_norm_(net.parameters(),1.);opt.step()
            last['distance_specialist' if k==0 else 'matched_control']=float(loss.detach())
        if step==0 or (step+1)%40==0 or step+1==steps:
            print(f'GAMER_G2_0014_TRAIN {step+1}/{steps} '
                  f'specialist={last["distance_specialist"]:.6f} control={last["matched_control"]:.6f}',flush=True)
    for net in nets:net.eval().requires_grad_(False)
    return {'steps':steps,'crowded_train_cases':len(crowded),'samples':dict(count),
            'last_loss':last,'training_gold_only':True,'matched_optimization':True}


def specialist_edges(parent,inventory,logits,policy):
    """Only predicted G1 nodes. Prune or conservatively rescue, never cardinality-cap sources."""
    if policy=='fallback':return set(parent)
    if logits.shape!=(len(inventory['edges']),):
        raise ValueError('GAMER_G2_0014_BAD_LOGIT_SHAPE')
    mode,threshold_s=policy.split('_')
    threshold=int(threshold_s)/100
    if mode not in ('prune','rescue') or not 0<threshold<1:
        raise ValueError('GAMER_G2_0014_BAD_CROWD_POLICY')
    cutoff=math.log(threshold/(1.-threshold))
    scores={edge:float(s) for edge,s in zip(inventory['edges'],logits)}
    if mode=='prune':
        return {edge for edge in parent if edge not in scores or scores[edge]>=cutoff}
    # Conservative additions are from predicted object pairs, never gold labels.
    candidates=[(s,e) for e,s in scores.items() if e not in parent and s>=cutoff]
    candidates.sort(key=lambda p:(-p[0],p[1]))
    return set(parent)|{edge for _,edge in candidates[:max(0,MAX_EDGES-len(parent))]}


def route_name(small,crowd):
    if small not in SMALL_POLICIES or crowd not in CROWD_POLICIES:
        raise ValueError('GAMER_G2_0014_BAD_ROUTING_CHOICE')
    return 'route__'+small+'__'+crowd


def route_edges(bucket,base_edges,inventory,joint_logits,control_logits,specialist_logits,
                matched_logits,small,crowd,*,ablation=False):
    if bucket==BUCKET_MEDIUM:return set(base_edges)
    if bucket==BUCKET_SMALL:
        if small=='fallback':return set(base_edges)
        mode,cutoff=small.split('_')
        if mode!='joint':raise ValueError('GAMER_G2_0014_BAD_SMALL_POLICY')
        return source13.decode_scores(inventory,control_logits if ablation else joint_logits,int(cutoff)/100)
    if bucket==BUCKET_CROWDED:
        return specialist_edges(base_edges,inventory,matched_logits if ablation else specialist_logits,crowd)
    raise ValueError('GAMER_G2_0014_BAD_DENSITY_BUCKET')


def metrics_by_density(records):
    names=records[0]['configurations']
    return {bucket:{name:base.metrics([{**r['configurations'][name],'family':r['family']}
                             for r in records if r['density_bucket']==bucket]) for name in names}
            for bucket in (BUCKET_SMALL,BUCKET_MEDIUM,BUCKET_CROWDED)}


def route_gates(metrics,buckets,name,control):
    parent=metrics[PARENT];cand=metrics[name];crowd=buckets[BUCKET_CROWDED]
    crowd_parent=crowd[PARENT];crowd_cand=crowd[name]
    # The overall graph must improve; the crowded specialist must add value,
    # not hide behind improvements on easy small graphs.
    return {'global_f1_improved':cand['f1']>parent['f1'],
            'false_positives_reduced':cand['false_positive']<parent['false_positive'],
            'global_recall_at_least_060':cand['recall']>=.60,
            'global_recall_loss_at_most_001':cand['recall']>=parent['recall']-GLOBAL_RECALL_LOSS,
            'crowded_cases_at_least_30':crowd_parent['cases']>=MIN_CROWDED,
            'crowded_f1_preserved_or_improved':(crowd_cand['f1']>=crowd_parent['f1'] if name.endswith('__fallback')
                                               else crowd_cand['f1']>crowd_parent['f1']),
            'crowded_recall_loss_at_most_002':crowd_cand['recall']>=crowd_parent['recall']-CROWDED_RECALL_LOSS,
            'beats_matched_control':cand['f1']>metrics[control]['f1'],
            'small_f1_not_worse':buckets[BUCKET_SMALL][name]['f1']>=buckets[BUCKET_SMALL][PARENT]['f1'],
            'medium_route_exact_fallback':True}


def select_route(metrics,buckets):
    gates={};eligible=[]
    for small in SMALL_POLICIES:
        for crowd in CROWD_POLICIES:
            if small=='fallback' and crowd=='fallback':continue
            name=route_name(small,crowd);control='control__'+name
            g=route_gates(metrics,buckets,name,control);gates[name]=g
            if all(g.values()):eligible.append(name)
    chosen=max(eligible,key=lambda name:(metrics[name]['f1'],
            buckets[BUCKET_CROWDED][name]['f1'],metrics[name]['recall'],name)) if eligible else None
    return chosen,{'eligible_count':len(eligible),
        'reason':'MAX_CALIBRATION_F1_DENSITY_ROUTING' if chosen else 'NO_DENSITY_ROUTE_PASSES_GATES',
        'global_recall_floor':max(.60,metrics[PARENT]['recall']-.01),
        'crowded_recall_floor':buckets[BUCKET_CROWDED][PARENT]['recall']-.02},gates


def eval_cases(cases,rank_head,old_joint,old_control,specialist,matched,allowed,rule,context,
               retention,*,device,group,chosen_only=None):
    """No labels supplied to any inference stage; labels used for offline scoring only."""
    names=[PARENT,robust11.PARENT,'source13_joint_50','source13_joint_60','source13_joint_70',
           'source13_joint_80','source13_control_50','source13_control_60',
           'source13_control_70','source13_control_80']
    choices=[(s,c) for s in SMALL_POLICIES for c in CROWD_POLICIES]
    if chosen_only is None:
        choices=[(s,c) for s,c in choices if s!='fallback' or c!='fallback']
    else:
        _,small,crowd=chosen_only.split('__') if chosen_only else ('route','fallback','fallback')
        choices=[(small,crowd)] if chosen_only else []
    names += [route_name(s,c) for s,c in choices]+['control__'+route_name(s,c) for s,c in choices]
    score_rows=defaultdict(list);saved=[]
    for net in (old_joint,old_control,specialist,matched,context,retention):
        net.to(device).eval().requires_grad_(False)
    with torch.inference_mode():
        for ix,case in enumerate(cases,1):
            rank=rel.full_logits(rank_head,case['vectors'].to(device)).detach().cpu()
            old=density.candidates(case['anchors'],rank,allowed)
            ancestor=density.select_edges(old,rule)
            feats,odds,edge_order=learned8.graph_features(case['anchors'],case['vectors'],old,ancestor,rule)
            ids=[i for i,e in enumerate(edge_order) if e in ancestor]
            scores=retention(feats[ids].to(device),odds[ids].to(device),
                 context(feats[ids].to(device),odds[ids].to(device))).detach().cpu()
            parent={edge_order[i] for i,v in zip(ids,scores) if float(v)>=math.log(.4/.6)}
            bucket=density._bucket(len(case['anchors']))
            inv=source13.make_inventory(case,rank,allowed)
            x=inv['features'].to(device);b=inv['base'].to(device);groups=inv['groups']
            pred_j=old_joint(x,b,groups,with_competition=True).cpu()
            pred_c=old_control(x,b,groups,with_competition=False).cpu()
            pred_s=specialist(x,b,groups,with_competition=True).cpu() if bucket==BUCKET_CROWDED else torch.empty(0)
            pred_m=matched(x,b,groups,with_competition=True).cpu() if bucket==BUCKET_CROWDED else torch.empty(0)
            variants={PARENT:parent,robust11.PARENT:ancestor}
            for t in source13.THRESHOLDS:
                variants[f'source13_joint_{int(t*100):02d}']=source13.decode_scores(inv,pred_j,t)
                variants[f'source13_control_{int(t*100):02d}']=source13.decode_scores(inv,pred_c,t)
            for s,c in choices:
                name=route_name(s,c)
                variants[name]=route_edges(bucket,parent,inv,pred_j,pred_c,pred_s,pred_m,s,c)
                variants['control__'+name]=route_edges(bucket,parent,inv,pred_j,pred_c,pred_s,pred_m,s,c,ablation=True)
            gold=case['gold'];computed={n:sel.score_set(v,gold) for n,v in variants.items()}
            for name,score in computed.items():score_rows[name].append({**score,'family':case['family']})
            saved.append({'group':group,'index':ix,'family':case['family'],
                          'prompt_sha256':case['prompt_sha256'],'density_bucket':bucket,
                          'g1_predicted_nodes':len(case['anchors']),
                          'gold_binding_edges':len(gold),
                          'configurations':computed})
            if ix==1 or ix%40==0 or ix==len(cases):
                print(f'GAMER_G2_0014_EVALUATE {group} {ix}/{len(cases)}',flush=True)
    return {n:base.metrics(v) for n,v in score_rows.items()},saved

def run(*,root,g1_validation_report,g2_run,selection_run,discrimination_run,relational_run,
        density_run,consistency_run,context_run,learned_run,recall_run,crowded_run,
        robustness_run,diagnosis_run,source_run,output,device='cuda',max_length=2048,
        validation_limit=VALIDATION_LIMIT,steps=TRAIN_STEPS):
    root=Path(root);output=Path(output)
    if output.exists():raise FileExistsError('GAMER_G2_0014_IMMUTABLE_OUTPUT')
    head,manifest,dataset,bootstrap,_,_,hashes=sel.load_parents(root,g1_validation_report,g2_run)
    train=base.original_training_rows(dataset,TRAIN_LIMIT)
    if base._sha('\n'.join(base._sha(row['prompt']) for row in train))!=bootstrap.get('train_prompt_hashes_sha256'):
        raise ValueError('GAMER_G2_0014_TRAIN_DRIFT')
    first=base.novel_dev_rows(train,limit=rel.CALIBRATION_LIMIT,seed=base.SEED)
    if density.sha_rows(first)!=bootstrap.get('dev_dataset_sha256'):
        raise ValueError('GAMER_G2_0014_BOOTSTRAP_DRIFT')
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
    selection_dir=Path(selection_run);selection=json.loads((selection_dir/sel.REPORT).read_text())
    if (selection.get('schema')!=sel.SCHEMA or selection.get('g2_parent_hashes')!=hashes or
        selection.get('validation_dataset_sha256')!=density.sha_rows(second) or
        selection.get('compatibility_sha256')!=sel._hash_json([list(x) for x in sorted(allowed)]) or
        selection.get('production_promotion') is not False):
        raise ValueError('GAMER_G2_0014_G2_0002_DRIFT')
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
    parent10,parent10_hashes=robust11.verify_crowd10_parent(crowded_run,ninth,tenth,{**prior_hashes,**ninth_hashes})
    if not parent10['selected_independent_screen_pass']:
        raise ValueError('GAMER_G2_0014_UNVALIDATED_G2_0010')
    parent_cohort_limit=json.loads((Path(robustness_run)/robust11.REPORT).read_text()).get('cohort_limit')
    if type(parent_cohort_limit) is not int or parent_cohort_limit!=robust11.COHORT_LIMIT:
        raise ValueError('GAMER_G2_0014_UNSUPPORTED_PARENT_COHORT_SIZE')
    parent_cohorts=robust11.new_cohorts(train,first,second,third,fourth,fifth,sixth,seventh,eighth,ninth,tenth,
                                       cohort_count=4,limit=parent_cohort_limit)
    _,parent11_hashes=diagnosis12.verify_robust11_parent(robustness_run,parent_cohorts,
                        {**prior_hashes,**ninth_hashes,**parent10_hashes})
    predecessors=(train,first,second,third,fourth,fifth,sixth,seventh,eighth,ninth,tenth,*parent_cohorts)
    twelfth=diagnosis12.fresh_rows(predecessors,limit=CALIBRATION_LIMIT)
    parent12,parent12_records,parent12_hashes=source13.verify_diagnosis_parent(diagnosis_run,twelfth,
                        {**prior_hashes,**ninth_hashes,**parent10_hashes,**parent11_hashes})
    thirteenth=source13.new_validation_rows((*predecessors,twelfth),limit=CALIBRATION_LIMIT)
    fresh=fresh_rows((*predecessors,twelfth,thirteenth),limit=validation_limit)
    expected_parent_hashes={**prior_hashes,**ninth_hashes,**parent10_hashes,**parent11_hashes,**parent12_hashes}
    parent13,parent13_records,parent13_hashes=verify_source13_parent(source_run,thirteenth,expected_parent_hashes)
    rule=fifth_report['fixed_decoder_rules'][graph6.PARENT_ARM]
    original={name:value.detach().cpu().clone() for name,value in head.state_dict().items()}
    head=head.to(device).eval().requires_grad_(False)
    g1=deepcopy(head).eval().requires_grad_(False)
    model,tok=backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    if any(p.requires_grad for p in model.parameters()):
        raise ValueError('GAMER_G2_0014_BACKBONE_UNFROZEN')
    train_cache=rel.capture(g1,model,tok,train,device=device,max_length=max_length,group='train')
    calibration=rel.capture(g1,model,tok,thirteenth,device=device,max_length=max_length,group='g2_0013_calibration')
    independent=rel.capture(g1,model,tok,fresh,device=device,max_length=max_length,group='new_independent')
    rel.validate_original_state(head,original)
    width=train_cache[0]['vectors'].shape[1]
    old_models=load_source13_models(Path(source_run)/source13.WEIGHTS,width,parent13['new_weights_sha256'])
    context,_=recall9.load_frozen_scorers(Path(learned_run)/learned8.WEIGHTS,width,
                                          eighth_report['context_weights_sha256'])
    retention=crowd10.load_frozen_retention(Path(recall_run)/recall9.WEIGHTS,width,
                                       ninth_report['retention_weights_sha256'])[1]
    for item in train_cache:
        item['rank']=rel.full_logits(head,item['vectors'].to(device)).detach().cpu()
        item['allowed']=allowed
    torch.manual_seed(SEED)
    specialist=deepcopy(old_models['joint']).requires_grad_(True)
    matched=deepcopy(old_models['joint']).requires_grad_(True)
    training=train_specialists(specialist,matched,train_cache,steps=steps,device=device)
    calibration_metrics,calibration_cases=eval_cases(
        calibration,head,old_models['joint'],old_models['control'],specialist,matched,
        allowed,rule,context,retention,device=device,group='previously_inspected_calibration')
    for old,new in zip(parent13_records,calibration_cases):
        for name in parent13['independent_metrics']:
            selected=(name if name in (PARENT,robust11.PARENT) else 'source13_'+name)
            if new['configurations'].get(selected)!=old['configurations'][name]:
                raise ValueError('GAMER_G2_0014_SOURCE13_REPLAY_DRIFT:'+name)
    buckets=metrics_by_density(calibration_cases)
    chosen,selection,gates=select_route(calibration_metrics,buckets)
    independent_metrics,independent_cases=eval_cases(
        independent,head,old_models['joint'],old_models['control'],specialist,matched,
        allowed,rule,context,retention,device=device,group='new_independent_validation',
        chosen_only=chosen)
    fresh_buckets=metrics_by_density(independent_cases)
    selected_screen=(route_gates(independent_metrics,fresh_buckets,chosen,'control__'+chosen)
                     if chosen else None)
    passed=bool(chosen and all(selected_screen.values()))
    rel.validate_original_state(head,original)
    checkpoint={**{'specialist.'+k:v.detach().cpu().contiguous() for k,v in specialist.state_dict().items()},
                **{'matched.'+k:v.detach().cpu().contiguous() for k,v in matched.state_dict().items()}}
    report={'schema':SCHEMA,'status':'G2_DENSITY_SPECIALIZATION_DEV_NOT_CERTIFIED',
            'selected_route':chosen,'calibration_selection':selection,'calibration_gates':gates,
            'calibration_metrics':calibration_metrics,'calibration_by_node_density':buckets,
            'calibration_cases':len(calibration),'calibration_dataset_sha256':density.sha_rows(thirteenth),
            'independent_metrics':independent_metrics,'independent_by_node_density':fresh_buckets,
            'independent_cases':len(independent),'independent_dataset_sha256':density.sha_rows(fresh),
            'independent_selected_gates':selected_screen,'independent_screen_pass':passed,
            'train_info':training,'fallback_rule':PARENT,'fallback_medium_unconditional':True,
            'selected_route_uses_crowded_specialist':bool(chosen and not chosen.endswith('__fallback')),
            'small_fixed_policies_predeclared':list(SMALL_POLICIES),
            'crowded_fixed_policies_predeclared':list(CROWD_POLICIES),
            'crowded_specialist_hard_same_source_negatives':True,
            'crowded_specialist_distant_negatives':True,
            'training_only_gold_supervision':True,'no_gold_in_inference':True,
            'no_single_destination_cardinality':True,'matched_distant_negative_control':True,
            'g1_frozen':True,'g2_0004_rank_weights_frozen':True,'g2_0005_rule_frozen':True,
            'g2_0010_rule_frozen':True,'g2_0013_parent_weights_frozen':True,
            'g2_0011_robustness_verified':True,'g2_0012_diagnosis_verified':True,
            'g2_0013_parent_replay_verified':True,
            'parent_hashes':{**expected_parent_hashes,**parent13_hashes},
            'g1_development_candidate':base.G1_ARM,'g1_head_sha256':base.G1_HEAD_SHA,
            'g2_certified':False,'g2_production_unlocked':False,'production_promotion':False,
            'certification_authorized':False,'graph_execution_authorized':False,'memory_writes':False,
            'sealed_test_used':False,'challenge_or_test_split_used':False}
    output.mkdir(parents=True,exist_ok=False)
    save_file(checkpoint,str(output/WEIGHTS))
    report['new_weights_sha256']=backbone.sha256(output/WEIGHTS)
    (output/REPORT).write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf-8')
    (output/CASES).write_bytes(base._jsonl(calibration_cases+independent_cases))
    return report
