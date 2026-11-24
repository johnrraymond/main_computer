"""G2-0107: frozen checkpoint, pair-level competition and oracle-veto forensics.

    python -m tools.nanojev_gamer_v1_g2_pair_competition_forensics_0107 audit
    python -m tools.nanojev_gamer_v1_g2_pair_competition_forensics_0107 selftest
    python -u -m tools.nanojev_gamer_v1_g2_pair_competition_forensics_0107 run --device cuda

Research-only read-only investigation. Gold selects *retrospective diagnostic*
interventions and scores outcomes, and is NEVER supplied to model inference.
These interventions are neither trained nor deployable. The greedy decoder is
traced without changing the frozen implementation; every traced original
prediction is verified against the actual decoder. No checkpoint is modified.
"""
from __future__ import annotations
import argparse
from collections import Counter,defaultdict
import json
import math
from pathlib import Path
import torch
from tools import nanojev_gamer_v1_g2_checkpoint_forensics_0106 as f
from tools import nanojev_gamer_v1_g2_raw_graph_reranking_0103 as d
from tools import nanojev_gamer_v1_g2_graph_reconstruction_0101 as r
from tools import nanojev_gamer_v1_g2_encounter_bakeoff_0100 as b
from tools import nanojev_gamer_v1_g2_router_trainability_0098 as tr
from tools import nanojev_gamer_v1_g2_competence_routing_0098 as m
from tools import nanojev_gamer_v1_g2_fuzz_and_correct_0095 as old

TAG='G2_0107_PAIR_COMPETITION'
SCHEMA='nanojev.g2.pair-competition-forensics-0107.v1'
PRIMARY=('refers_to','applies_to')
NAMES=list(b.BIND_CLASSES)


def require(ok,reason):
    if not ok:raise ValueError(TAG+'_'+reason)


def emit(kind,payload):
    print(TAG+'_'+kind+' '+json.dumps(payload,sort_keys=True,allow_nan=False),flush=True)


def kind_name(v):return NAMES[int(v)]


def _metric(rec,graph):return m._count(rec,graph)


def _edge(i,j,t,objects):
    return {'pair':[i,j],'source':[int(objects[i][0]),int(objects[i][1]),str(objects[i][2])],
            'destination':[int(objects[j][0]),int(objects[j][1]),str(objects[j][2])],
            'relation':kind_name(t)}


def _keys(i,j,t,objects):
    return (i,objects[j][2],t),(j,objects[i][2],t)


def _gain(i,j,t,objects,score,spec):
    rel=kind_name(t)
    key='|'.join((str(objects[i][2]),str(objects[j][2]),rel))
    cut=float(spec.get('kind_thresholds',{}).get(key,spec.get('threshold',0.)))
    strength=float(score[i,j,t]-score[i,j,0])
    return strength-cut,key,strength,cut


def trace_decoder(score,objects,spec,device='cpu',verify=True):
    """Independent trace of the production greedy selector, asserted equivalent.

    Trace records the exact *candidate generation* (only one winning type per
    ordered pair) and source/destination cap rejection. It is NOT a replacement
    for the production decoder, and is never used to generate model predictions.
    """
    require(spec['method'] in ('joint','source_topk','type_aware','threshold'),
            'UNSUPPORTED_DECODER_METHOD_FOR_CAUSAL_TRACE')
    score=score.detach().cpu().float();n=len(objects)
    require(tuple(score.shape)==(n,n,b.R+1) and bool(torch.isfinite(score).all()),
            'INVALID_SCORE_SURFACE')
    candidates=[];events={};winner={};selected=torch.zeros((n,n),dtype=torch.long)
    for i in range(n):
        for j in range(n):
            if i==j:continue
            # Exactly the decoder's max(strength,-class_index) tie break.
            margins=(score[i,j,1:]-score[i,j,0]).tolist()
            t=max(range(b.R),key=lambda k:(margins[k],-k))+1
            gain,key,strength,cut=_gain(i,j,t,objects,score,spec)
            winner[(i,j)]=t
            if strength<=cut:
                events[(i,j)]={'status':'below_threshold','winning_type':kind_name(t),
                        'strength':round(strength,5),'cut':cut,'gain':round(gain,5)}
            else:
                candidates.append((gain,i,j,t,key))
    candidates.sort(key=lambda x:(-x[0],x[1],x[2],x[3]))
    out_count=Counter();in_count=Counter();out_selected=defaultdict(list);in_selected=defaultdict(list)
    for position,(gain,i,j,t,key) in enumerate(candidates):
        source_key,dest_key=_keys(i,j,t,objects)
        max_out=(int(spec.get('k',2)) if spec['method']=='source_topk' else
                 int(spec.get('out_caps',{}).get(key,2)))
        incoming='|'.join((objects[j][2],objects[i][2],kind_name(t)))
        max_in=int(spec.get('in_caps',{}).get(incoming,2))
        event={'winning_type':kind_name(t),'candidate_rank':position+1,
               'gain':round(float(gain),5),'source_count_before':out_count[source_key],
               'destination_count_before':in_count[dest_key],
               'source_cap':max_out if spec['method'] in ('joint','source_topk') else None,
               'destination_cap':max_in if spec['method']=='joint' else None}
        if spec['method'] in ('source_topk','joint') and out_count[source_key]>=max_out:
            event['status']='source_capacity';event['blockers']=[list(x) for x in out_selected[source_key]]
        elif spec['method']=='joint' and in_count[dest_key]>=max_in:
            event['status']='destination_capacity';event['blockers']=[list(x) for x in in_selected[dest_key]]
        else:
            event['status']='selected';selected[i,j]=t
            out_count[source_key]+=1;in_count[dest_key]+=1
            out_selected[source_key].append((i,j,t));in_selected[dest_key].append((i,j,t))
        events[(i,j)]=event
    if verify:
        actual=r._decode(score,objects,spec,device).cpu()
        require(torch.equal(actual,selected),'PRODUCTION_DECODER_TRACE_MISMATCH')
    return selected,events,winner


def counterfactual_pair_veto(score,objects,spec,device,i,j):
    """Oracle-selected *pair* exclusion in score space, then real re-decoding.

    Because a false positive is a gold-NONE pair, banning the entire pair is a
    meaningful diagnostic of whether freeing its capacity exposes a true edge.
    It is intentionally different from deleting a predicted edge *after* decode.
    """
    work=score.clone();work[i,j,1:]=-100000.
    result=r._decode(work,objects,spec,device).cpu()
    require(int(result[i,j])==0,'DIAGNOSTIC_VETO_NOT_HONORED')
    return result


def counterfactual_force_true(score,objects,spec,device,i,j,t):
    """Oracle forced-gold candidate, reveals which alternatives it displaces."""
    work=score.clone();work[i,j,t]=float(score.max().item())+100.
    result=r._decode(work,objects,spec,device).cpu()
    return result


def _snippet(prompt,span):
    if not isinstance(prompt,str) or not span:return None
    start,end=int(span[0]),int(span[1])
    if 0<=start<end<=len(prompt):return prompt[start:end]
    return None


def _distance_bucket(a,b):
    distance=max(0,max(int(a[0]),int(b[0]))-min(int(a[1]),int(b[1])))
    if distance==0:return 'overlap_or_adjacent'
    if distance<=16:return '1-16'
    if distance<=64:return '17-64'
    if distance<=160:return '65-160'
    return '>160'


def _score_bucket(score):
    score=float(score)
    if score<0:return '<0'
    if score<1:return '0..1'
    if score<2:return '1..2'
    if score<4:return '2..4'
    if score<6:return '4..6'
    if score<8:return '6..8'
    return '>=8'


def _evidence(i,j,t,objects,stage,score,trace_events,prompt=None):
    margins=stage['margins'].detach().cpu()
    preds=stage['preds'].detach().cpu()
    pair_score=score[i,j].detach().cpu()
    raw=margins[:,i,j,t-1]
    votes=preds[:,i,j]
    learned_margin=float(pair_score[t]-pair_score[0])
    winner=int(pair_score.argmax())
    # All class scores, including NONE, rather than a misleading probability.
    rel_ranking=sorted(range(b.R+1),key=lambda rel:(-float(pair_score[rel]),rel))
    event=dict(trace_events.get((i,j),{}))
    if event.get('winning_type')!=kind_name(t):
        event['gold_type_not_pair_winner']=True
    return {'edge':_edge(i,j,t,objects),
            'source_text':_snippet(prompt,objects[i]),
            'destination_text':_snippet(prompt,objects[j]),
            'endpoint_distance_bucket':_distance_bucket(objects[i],objects[j]),
            'raw_expert_margins':[round(float(x),5) for x in raw],
            'expert_decoded_labels':[kind_name(x) for x in votes],
            'expert_support_for_relation':int((votes==t).sum()),
            'raw_max':round(float(raw.max()),5),'raw_mean':round(float(raw.mean()),5),
            'learned_margin_vs_none':round(learned_margin,5),
            'label_score_rank':rel_ranking.index(t)+1,
            'winning_pair_label':kind_name(winner),
            'pair_label_scores':{kind_name(k):round(float(pair_score[k]-pair_score[0]),5)
                                 for k in range(b.R+1)},
            'decoder_event':event}


def _shared(fp,fn,objects):
    i,j,t=fp;u,v,g=fn
    same_source=i==u;same_destination=j==v
    fk=_keys(i,j,t,objects);gk=_keys(u,v,g,objects)
    return {'same_source':same_source,'same_destination':same_destination,
            'same_ordered_pair':i==u and j==v,
            'same_source_capacity_slot':fk[0]==gk[0],
            'same_destination_capacity_slot':fk[1]==gk[1]}


def _typed_summary(edges):
    x=defaultdict(Counter)
    for edge in edges:
        rel=edge['relation'];x[rel]['count']+=1
    return {k:dict(v) for k,v in sorted(x.items())}


def _case(rec,fold,stage,spec,device,primary=PRIMARY,run_oracles=True):
    """Pure posterior scoring and counterfactuals: no retraining or updates."""
    objects=rec['item']['objects'];gold=rec['item']['targets'].long().cpu()
    graph=stage['graph'].long().cpu();pair=stage['pair'].long().cpu()
    score=stage['score'];n=len(objects)
    require(score is not None and pair.shape==gold.shape==graph.shape==(n,n),
            'FROZEN_MODEL_STAGE_SHAPE_DRIFT')
    observed,events,winner=trace_decoder(score,objects,spec,device,verify=True)
    require(torch.equal(observed,graph),'FROZEN_STAGE_NOT_REPRODUCED')
    valid=~torch.eye(n,dtype=torch.bool)
    fplist=[(int(i),int(j),int(graph[i,j])) for i,j in (valid&(graph>0)&(gold==0)).nonzero().tolist()]
    fnlist=[(int(i),int(j),int(gold[i,j])) for i,j in (valid&(gold>0)&(graph==0)).nonzero().tolist()]
    # Existing experiments measured zero wrong-type positive pairs, but fail
    # closed instead of silently omitting them if a new source changes.
    mismatches=(valid&(gold>0)&(graph>0)&(gold!=graph)).nonzero().tolist()
    require(not mismatches,'UNEXPECTED_WRONG_TYPE_POSITIVE_EDGE')
    fps=[];fns=[];stats=Counter();impacts=defaultdict(Counter)
    # Direct positive-pair discrimination: same evidence bins for correct and
    # incorrect selected edges, so the report diagnoses precision rather than
    # merely displaying a catalogue of mistakes.
    quality=defaultdict(Counter)
    margins=stage['margins'].detach().cpu();votes=stage['preds'].detach().cpu()
    score_cpu=score.detach().cpu()
    cpu_stage={**stage,'margins':margins,'preds':votes}
    for u,v in (valid&(graph>0)).nonzero().tolist():
        rel=int(graph[u,v]);label=kind_name(rel)
        outcome='TP' if int(gold[u,v])==rel else 'FP'
        count=int((votes[:,u,v]==rel).sum())
        learned=float(score_cpu[u,v,rel]-score_cpu[u,v,0])
        rawmax=float(margins[:,u,v,rel-1].max())
        for feature,value in (('relation',label),('votes',str(count)),
             ('learned_margin',_score_bucket(learned)),
             ('raw_margin',_score_bucket(rawmax)),
             ('endpoint_distance',_distance_bucket(objects[u],objects[v])),
             ('relation_x_votes',label+'|'+str(count)),
             ('relation_x_distance',label+'|'+_distance_bucket(objects[u],objects[v]))):
            quality[feature+'|'+value][outcome]+=1
    original=_metric(rec,graph)
    prompt=rec['item']['row'].get('prompt')
    for i,j,t in fplist:
        e=_evidence(i,j,t,objects,cpu_stage,score_cpu,events,prompt)
        rel=kind_name(t)
        competitor=[]
        for u,v,g in fnlist:
            c=_shared((i,j,t),(u,v,g),objects)
            if any(c.values()):
                g_score=float(score_cpu[u,v,g]-score_cpu[u,v,0])
                competitor.append({'edge':_edge(u,v,g,objects),
                                   'shared':c,'gold_margin_vs_none':round(g_score,5),
                                   'gap_fp_minus_gold':round(float(score_cpu[i,j,t]-score_cpu[i,j,0])-g_score,5),
                                   'gold_decoder_reason':events[(u,v)].get('status')})
        e['gold_missing_competitors']=competitor
        # Even already-correct gold edges can share a source or destination
        # slot with the FP. This distinguishes oversubscribed *false* edges
        # from true FN/FP competition where the gold candidate was excluded.
        true_selected=[]
        for u,v in (valid&(gold>0)&(graph==gold)).nonzero().tolist():
            g=int(gold[u,v])
            shared=_shared((i,j,t),(u,v,g),objects)
            if shared['same_source_capacity_slot'] or shared['same_destination_capacity_slot']:
                true_selected.append({'edge':_edge(u,v,g,objects),'shared':shared,
                   'gold_margin_vs_none':round(float(score_cpu[u,v,g]-score_cpu[u,v,0]),5)})
        e['already_correct_shared_slot_edges']=true_selected
        e['relation']=rel
        if run_oracles and rel in primary:
            new=counterfactual_pair_veto(score_cpu,objects,spec,'cpu',i,j)
            # Direct deletion is a trivial one-FP improvement; isolate the
            # extra changes that arise ONLY from rerunning greedy assembly.
            post=graph.clone();post[i,j]=0
            direct=_metric(rec,post);post_metric=_metric(rec,new)
            added=(valid&(new>0)&(graph==0)).nonzero().tolist()
            removed=(valid&(new==0)&(graph>0)).nonzero().tolist()
            retyped=(valid&(new>0)&(graph>0)&(new!=graph)).nonzero().tolist()
            recover=[(u,v,g) for u,v,g in fnlist if int(new[u,v])==g]
            new_fp=[(u,v,int(new[u,v])) for u,v in added if int(gold[u,v])==0]
            lost_tp=[(u,v,int(graph[u,v])) for u,v in removed if int(gold[u,v])>0]
            # Gold-labelled analysis is a diagnostic *after* two real decodes.
            causal={'posthoc_delete':{'fp':direct['fp'],'fn':direct['fn']},
                    'redecoded':{'fp':post_metric['fp'],'fn':post_metric['fn'],
                                 'tp':post_metric['tp'],'exact':post_metric['exact']},
                    'new_edges_after_freeing_slot':[_edge(u,v,int(new[u,v]),objects) for u,v in added],
                    'removed_edges':[_edge(u,v,int(graph[u,v]),objects) for u,v in removed],
                    'retyped_pairs':[{'pair':[u,v],'before':kind_name(graph[u,v]),
                                       'after':kind_name(new[u,v])} for u,v in retyped],
                    'new_correct_edges':[_edge(u,v,g,objects) for u,v,g in recover],
                    'new_wrong_edges':[_edge(u,v,t2,objects) for u,v,t2 in new_fp],
                    'lost_correct_edges':[_edge(u,v,t2,objects) for u,v,t2 in lost_tp],
                    'secondary_true_rescues':len(recover),
                    'additional_spurious':len(new_fp),
                    'newly_exact':bool(post_metric['exact'] and not original['exact'])}
            e['oracle_pair_veto']=causal
            stats['veto_probes']+=1
            stats['rescued_true_edges']+=len(recover)
            stats['additional_spurious_edges']+=len(new_fp)
            stats['lost_true_edges']+=len(lost_tp)
            stats['vetoes_with_rescue']+=int(len(recover)>0)
            stats['vetoes_with_no_rescue']+=int(len(recover)==0)
            stats['new_exact_from_single_veto']+=int(causal['newly_exact'])
            impacts[rel]['veto_count']+=1
            impacts[rel]['rescues']+=len(recover)
            impacts[rel]['new_false_positives']+=len(new_fp)
            for u,v,g in recover:
                impacts[rel]['rescue_shared_source_capacity']+=int(_shared((i,j,t),(u,v,g),objects)['same_source_capacity_slot'])
                impacts[rel]['rescue_shared_destination_capacity']+=int(_shared((i,j,t),(u,v,g),objects)['same_destination_capacity_slot'])
                impacts[rel]['rescue_no_shared_capacity_slot']+=int(not (_shared((i,j,t),(u,v,g),objects)['same_source_capacity_slot'] or _shared((i,j,t),(u,v,g),objects)['same_destination_capacity_slot']))
        fps.append(e)
    for i,j,t in fnlist:
        e=_evidence(i,j,t,objects,cpu_stage,score_cpu,events,prompt)
        e['pairwise_direct_correct']=int(pair[i,j])==t
        e['pairwise_direct_none']=int(pair[i,j])==0
        related=[_shared((u,v,g),(i,j,t),objects) for u,v,g in fplist]
        e['competing_incorrect_edges']=[
            {'edge':_edge(u,v,g,objects),'shared':shared,
             'margin_gap_wrong_minus_gold':round(float(score_cpu[u,v,g]-score_cpu[u,v,0]-score_cpu[i,j,t]+score_cpu[i,j,0]),5)}
            for (u,v,g),shared in zip(fplist,related) if any(shared.values())]
        cause=('DECODER_LOST_CORRECT_PAIR' if int(pair[i,j])==t else
               'PAIR_RANKING_REJECTED_TRUE_EDGE')
        # Attribute only the causal obstacle for the GOLD label. A capacity
        # rejection of some other winning label is not evidence that capacity
        # prevented the true edge: type ranking failed first.
        if events[(i,j)].get('winning_type')!=kind_name(t):
            cause='WRONG_PAIR_TOP_LABEL'
        elif events[(i,j)]['status'] in ('source_capacity','destination_capacity'):
            cause='CAPACITY_REJECTED_TRUE_EDGE'
        elif events[(i,j)]['status']=='below_threshold':
            cause='SCORE_BELOW_DECODER_THRESHOLD'
        e['diagnostic_class']=cause
        stats['missing_'+cause]+=1
        if run_oracles and kind_name(t) in primary:
            result=counterfactual_force_true(score_cpu,objects,spec,'cpu',i,j,t)
            after=_metric(rec,result)
            lost=(valid&(graph==gold)&(gold>0)&(result!=gold)).nonzero().tolist()
            removed_false=(valid&(graph>0)&(gold==0)&(result==0)).nonzero().tolist()
            e['oracle_forced_true_edge']={'appeared':int(result[i,j])==t,
                'new_exact':bool(after['exact'] and not original['exact']),
                'displaced_correct_edges':[_edge(u,v,int(graph[u,v]),objects) for u,v in lost],
                'removed_false_edges':[_edge(u,v,int(graph[u,v]),objects) for u,v in removed_false],
                'net_fp':after['fp']-original['fp'],'net_fn':after['fn']-original['fn']}
            stats['forced_true_probes']+=1
            stats['forced_true_appeared']+=int(result[i,j])==t
            stats['forced_true_displaced_tp']+=len(lost)
            stats['forced_true_removed_fp']+=len(removed_false)
        fns.append(e)
    family=rec['item']['row']['family']
    obj={'fold':int(fold),'family':family,
         'base_id':str(rec['item']['row'].get('base_id','')),
         'case_id':str(rec['item']['row'].get('case_id','')),
         'graph':{k:int(original[k]) for k in ('tp','fp','fn','exact')},
         'pairwise_graph':{k:int(_metric(rec,pair)[k]) for k in ('tp','fp','fn','exact')},
         'wrong_positive_edges':fps,'missing_true_edges':fns,
         'diagnostic_counts':dict(stats),'positive_pair_discrimination':{key:dict(ct) for key,ct in quality.items()}}
    return obj,stats,impacts


def _load(root=old.ROOT_DEFAULT,output=None):
    src,ex,r99,r100,r101,r102,p102,report,original=f._load(root,output,'cpu')
    path=src[8]/'checkpoint-forensics-0106'/'result.json'
    require(path.is_file(),'0106_DIAGNOSTIC_REPORT_MISSING_RUN_0106_FIRST')
    prior=old.read(path)
    require(prior.get('schema')==f.SCHEMA and prior.get('checkpoint_modification') is False
            and prior.get('frozen_0103_exact_replay',{}).get('exact')==report['candidate']['exact']
            and prior.get('source_0103_report_sha256')==old.digest(original)
            and prior.get('production_promoted') is False,'0106_LINEAGE_OR_REPLAY_DRIFT')
    return src,ex,r99,r100,r101,r102,p102,report,original,path,prior


def audit(root=old.ROOT_DEFAULT,output=None):
    src,ex,r99,r100,r101,r102,p102,report,original,priorpath,prior=_load(root,output)
    entries=[]
    for fold in report['folds']:
        models,manifest=d.load_candidate(Path(fold['manifest']).parent,ex,'cpu',old.digest(p102))
        require(manifest['selected']==fold['chosen'],'0103_FOLD_SELECTION_DRIFT')
        entries.append({'arm':fold['chosen']['arm'],'params':fold['chosen']['params'],
                        'sha256':{k:v['sha256'] for k,v in manifest['models'].items()}})
    emit('AUDIT',{'ready':True,'frozen_0103_checkpoint_folds':entries,
         'prior_0106_sha256':old.digest(priorpath),'reference_0103_sha256':old.digest(original),
         'target_relations':list(PRIMARY),'new_training':False,'gold_used_in_diagnostic_only':True,
         'checkpoint_modified':False,'production_promotion':False,'confirmation_used':False,
         'g3_unlocked':False,'reused_fit_not_independent_certification':True})


def run(root=old.ROOT_DEFAULT,output=None,device='cuda',force=False,all_relations=False):
    require(device=='cpu' or (device.startswith('cuda') and torch.cuda.is_available()),'CUDA_UNAVAILABLE')
    src,ex,r99,r100,r101,r102,p102,report,original,priorpath,prior=_load(root,output)
    target=src[8]/'pair-competition-forensics-0107'/'result.json'
    require(force or not target.exists(),'REPORT_EXISTS_USE_FORCE')
    primary=tuple(NAMES[1:]) if all_relations else PRIMARY
    emit('START',{'device':device,'target_relations':list(primary),'folds':3,
         'model_training':False,'gold_only_for_posthoc_oracle_probes':True,
         'frozen_0103_replay_required':True,'checkpoint_write':False,
         'real_decoder_greedy_trace_required':True,'data_reused_fit':True})
    records,spec=b.parent._records(src,ex,device)
    results=[];stats=Counter();impacts=defaultdict(Counter);replay=Counter()
    with torch.inference_mode():
        for fold,(idx,(train,val,test),counts) in enumerate(tr._split_by_base(records,3,d.SEED)):
            require(fold==idx and counts==report['folds'][fold]['base_counts'],
                    '0103_BASE_SPLIT_DRIFT')
            bundle=r._bundle_from_0100(fold,r100['folds'][fold],r99,ex,device)
            models,manifest=d.load_candidate(Path(report['folds'][fold]['manifest']).parent,
                                           ex,device,old.digest(p102))
            choice=report['folds'][fold]['chosen']
            require(manifest['selected']==choice,'SELECTED_CHECKPOINT_DRIFT')
            local=Counter()
            for k,rec in enumerate(test):
                stage=f._stage(rec,ex,spec,device,bundle,models,choice,probe=False)
                actual=d._infer(rec,ex,spec,device,bundle,choice['arm'],models.get(choice['arm']),
                                **choice['params'])
                require(torch.equal(stage['graph'],actual),'FROZEN_INFERENCE_MISMATCH')
                counts_rec=_metric(rec,stage['graph']);local.update(counts_rec);replay.update(counts_rec)
                case,case_stats,case_impacts=_case(rec,fold,stage,spec,device,primary,True)
                results.append(case);stats.update(case_stats)
                for rel,datum in case_impacts.items():impacts[rel].update(datum)
            expect=report['folds'][fold]['selected']['overall']
            require(all(int(local[key])==int(expect[key]) for key in ('tp','fp','fn','exact','cases')),
                    'FROZEN_0103_FOLD_REPLAY_MISMATCH_'+str(fold))
            emit('FOLD',{'fold':fold,'chosen':choice['arm'],'heldout_cases':len(test),
                 'heldout_exact':local['exact'],'wrong_positive_veto_probes':
                  sum(1 for case in results if case['fold']==fold for edge in case['wrong_positive_edges']
                      if 'oracle_pair_veto' in edge),
                 'frozen_replay':True})
    require(all(int(replay[key])==int(report['candidate'][key])
                for key in ('tp','fp','fn','exact','cases')),'GLOBAL_REPLAY_MISMATCH')
    # Classifier is diagnostic only: no claims of independent confirmation.
    by_relation=defaultdict(Counter)
    reason=defaultdict(Counter);competition=Counter();by_variant=defaultdict(Counter)
    discrim=defaultdict(Counter)
    for case in results:
        for key,ct in case['positive_pair_discrimination'].items():discrim[key].update(ct)
        family=case['family'];caseid=case['case_id']
        variant=next((s for s in ('action_removal','role_swap','renaming','added_distractor','base')
                     if caseid.endswith('-'+s)),'unknown')
        by_variant[family+'|'+variant].update({k:case['graph'][k] for k in ('tp','fp','fn','exact')})
        for edge in case['wrong_positive_edges']:
            rel=edge['relation'];by_relation[rel]['false_edges']+=1
            by_relation[rel]['false_with_expert_vote']+=int(edge['expert_support_for_relation']>0)
            by_relation[rel]['false_pairwise_correct_none']+=int(edge['winning_pair_label']=='none')
            candidates=edge['gold_missing_competitors']
            by_relation[rel]['false_with_competing_missing_true']+=int(bool(candidates))
            if candidates:
                competition['source_slot']+=int(any(c['shared']['same_source_capacity_slot'] for c in candidates))
                competition['destination_slot']+=int(any(c['shared']['same_destination_capacity_slot'] for c in candidates))
                competition['different_relation_or_slot']+=int(not any(c['shared']['same_source_capacity_slot'] or
                    c['shared']['same_destination_capacity_slot'] for c in candidates))
            if 'oracle_pair_veto' in edge:
                o=edge['oracle_pair_veto'];by_relation[rel]['rescued_true_edges']+=o['secondary_true_rescues']
                by_relation[rel]['oracle_vetoes_tested']+=1
                by_relation[rel]['vetoes_triggering_repair']+=int(o['secondary_true_rescues']>0)
        for edge in case['missing_true_edges']:
            rel=edge['edge']['relation'];by_relation[rel]['missed_true_edges']+=1
            reason[rel][edge['diagnostic_class']]+=1
            by_relation[rel]['missed_pairwise_correct']+=int(edge['pairwise_direct_correct'])
            by_relation[rel]['missed_raw_positive']+=int(edge['raw_max']>0)
            by_relation[rel]['missed_with_competing_wrong_positive']+=int(bool(edge['competing_incorrect_edges']))
    doc={'schema':SCHEMA,'source_0103_report_sha256':old.digest(original),
         'source_0106_report_sha256':old.digest(priorpath),
         'reference_0103_checkpoint_hashes':prior['source_0103_checkpoint_fold_manifests'],
         'frozen_0103_replay':m._metric(replay),'selected_relations_for_intervention':list(primary),
         'case_level':results,'by_relation':{x:dict(y) for x,y in sorted(by_relation.items())},
         'missing_edge_decoder_reasons':{x:dict(y) for x,y in sorted(reason.items())},
         'false_edge_competition':dict(competition),
         'by_case_variant':{x:dict(y) for x,y in sorted(by_variant.items())},
         'oracle_veto_diagnostic':dict(stats),
         'oracle_veto_by_false_relation':{x:dict(y) for x,y in sorted(impacts.items())},
         'positive_pair_discrimination':{key:{'tp':int(v['TP']),'fp':int(v['FP']),
             'precision':v['TP']/max(1,v['TP']+v['FP'])} for key,v in sorted(discrim.items())},
         'interpretation_limits':[
             'Every vetoed pair and forced true label was selected by gold; these are retrospective oracle interventions, NOT gold-free inference gains',
             'Single-edge counterfactual deltas do not add linearly across probes and must never be treated as deployable accuracy',
             'Decoder is a greedy selector; candidate scores may have insufficient evidence for true endpoint identity even when gold class is representable',
             'The decoder trace is verified against the production greedy decoder for every observed case',
             'Only one-edge vetoes and one-edge forced truths are tested; multi-edge interference can remain',
             'Fold partitions reuse the experimental fit cohort; no independent certification or confirmation'],
         'new_training':False,'checkpoint_modified':False,'production_promoted':False,
         'g3_unlocked':False,'dev_used':False,'confirmation_used':False,
         'research_only':True,'data_reused_fit_not_independent_certification':True}
    target.parent.mkdir(parents=True,exist_ok=True)
    old.write(target,doc)
    emit('DIAGNOSIS',{'by_relation':doc['by_relation'],
         'missing_decoder_causes':doc['missing_edge_decoder_reasons'],
         'false_edge_competition':doc['false_edge_competition'],
         'oracle_veto':doc['oracle_veto_diagnostic'],
         'impact_by_relation':doc['oracle_veto_by_false_relation'],
         'positive_pair_precision_by_votes':{k:v for k,v in doc['positive_pair_discrimination'].items()
                                             if k.startswith('relation_x_votes|')},
         'frozen_replay':doc['frozen_0103_replay']})
    emit('SAVED',{'path':str(target),'sha256':old.digest(target),
          'checkpoints_unchanged':True,'trained':False,'production_promoted':False})


def selftest():
    # Two competing refers_to edges share a source cap; an oracle veto of the
    # incorrect winner permits the lower-scored, genuinely correct edge.
    objects=[[0,1,'action'],[2,3,'action'],[4,5,'action']]
    label=NAMES.index('refers_to')
    spec={'method':'joint','threshold':0.,'out_caps':{'action|action|refers_to':1},
          'in_caps':{'action|action|refers_to':2}}
    scores=torch.zeros((3,3,b.R+1),dtype=torch.float32)
    scores[:,:,1:]=-2.
    scores[0,1,label]=5.;scores[0,2,label]=4.
    predicted,events,_=trace_decoder(scores,objects,spec,'cpu')
    require(int(predicted[0,1])==label and int(predicted[0,2])==0 and
            events[(0,2)]['status']=='source_capacity','SYNTHETIC_CAP_COMPETITION_MISSING')
    rescued=counterfactual_pair_veto(scores,objects,spec,'cpu',0,1)
    require(int(rescued[0,1])==0 and int(rescued[0,2])==label,
            'SINGLE_VETO_FAILED_TO_RECOVER_GOLD_COMPETITOR')
    # If unrelated slots are present, a veto must not invent a relation there.
    require(int(rescued[1,2])==0,'UNRELATED_SPURIOUS_ADDED')
    # A missing true edge below threshold should be forced as a separate oracle;
    # forcing it may displace an otherwise valid positive via capacity.
    forced=counterfactual_force_true(scores,objects,spec,'cpu',0,2,label)
    require(int(forced[0,2])==label and int(forced[0,1])==0,
            'FORCED_GOLD_CAPACITY_DISPLACEMENT_FAILED')
    n=3; gold=torch.zeros((n,n),dtype=torch.long);gold[0,2]=label
    from tools.nanojev_gamer_v1_g2_posttrain_graph_bakeoff_0088 import format_edge
    rec={'item':{'objects':objects,'targets':gold,
          'row':{'family':'encounter','base_id':'synthetic','case_id':'synthetic-base'},
          'full_gold':{format_edge(objects,0,2,label)},'g1_exact':True}}
    stage={'graph':predicted,'pair':scores.argmax(-1),'score':scores,
           'margins':scores[...,1:].unsqueeze(0).repeat(b.E,1,1,1),
           'preds':predicted.unsqueeze(0).repeat(b.E,1,1)}
    case,stats,impacts=_case(rec,0,stage,spec,'cpu',PRIMARY,True)
    require(case['graph']['exact']==0 and stats['vetoes_with_rescue']==1 and
            stats['rescued_true_edges']==1 and
            case['wrong_positive_edges'][0]['oracle_pair_veto']['newly_exact'] is True,
            'ORACLE_RESCUE_CASE_ACCOUNTING_INVALID')
    # A false positive without a competing true edge has no secondary rescue.
    gold0=torch.zeros_like(gold);rec['item']['targets']=gold0;rec['item']['full_gold']=set()
    case2,stat2,_=_case(rec,0,stage,spec,'cpu',PRIMARY,True)
    require(stat2['vetoes_with_no_rescue']==1,'FALSE_RECOVERY_IN_NO_COMPETITOR_CASE')
    # Veto that is already a valid edge would destroy a TP. This proves oracle
    # selection matters and should not be portrayed as a deployment policy.
    require(int(predicted[0,1])==label and int(rescued[0,1])==0,'ORACLE_GOLD_DEPENDENCE_MISSING')
    emit('SELFTEST',{'production_decoder_trace_exact':True,
          'source_capacity_competition_verified':True,
          'oracle_pair_veto_recovers_gold_without_boost':True,
          'oracle_forced_gold_can_displace_candidate':True,
          'no_competitor_no_rescue':True,'gold_only_for_diagnostic':True,
          'no_training':True,'checkpoint_modification':False})


def main(argv=None):
    a=argparse.ArgumentParser(description=__doc__)
    a.add_argument('command',nargs='?',choices=('audit','selftest','run'),default='audit')
    a.add_argument('--root',default=old.ROOT_DEFAULT)
    a.add_argument('--output')
    a.add_argument('--device',default='cuda')
    a.add_argument('--force',action='store_true',help='Replace only this diagnostic result.json')
    a.add_argument('--all-relations',action='store_true',help='Include owns and measures in oracle probes')
    args=a.parse_args(argv)
    if args.command=='audit':return audit(args.root,args.output)
    if args.command=='selftest':return selftest()
    return run(args.root,args.output,args.device,args.force,args.all_relations)

if __name__=='__main__':main()
