"""G2-0035: label-blind bounded G1 occurrence / G2 relationship inventory expansion.

Frozen G1/G2 scorers and preexisting policies; compare independent arms
(endpoint proposals, inventory widening, joint) against exact baseline replay.
No gold in proposals, scorers, policy selection, or decoding. All gold comparisons
happen after every arm's predictions are frozen. No deployment changes.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import gzip
import json
import math

import numpy as np
import torch

from tools import nanojev_gamer_v1_g2_causal_graph_diagnosis as x34
from tools import nanojev_gamer_v1_g1_component_diagnosis as g1diag
from tools.nanojev_gamer_v1_graph_head import token_span_char_bounds

x33=x34.x33
x32=x34.x32
x31=x34.x31
p30=x34.p30
base=x34.base
backbone=x34.backbone
density=x34.density
general16=x34.general16
source13=x34.source13

SCHEMA='nanojev.gamer.graph.g2.candidate-coverage-intervention.v1'
REPORT='g2_candidate_coverage_report.json'
LEDGER='g2_candidate_coverage_per_case.jsonl.gz'
SEED=460535
SEEDS=x34.SEEDS
MAX_LENGTH=x34.MAX_LENGTH
TOP_K_BASE=x31.m29.TOP_K
TOP_K_WIDE=48
MAX_EXTRA_SPANS=16
NEW=('new_independent_dev_0035','new_topology_dev_0035')
REPLAY=x34.NEW
ARMS={'baseline':(False,TOP_K_BASE),
      'endpoint_recovery':(True,TOP_K_BASE),
      'relationship_expansion':(False,TOP_K_WIDE),
      'joint_coverage':(True,TOP_K_WIDE)}


def sha_rows(rows):
    return density.sha_rows(rows)


def verify_parent34(directory, *, ancestors, train_sha, old_dev, fresh34):
    """Validate the immutable G2-0034 diagnosis, not merely its filenames."""
    d=Path(directory);rp=d/x34.REPORT;lp=d/x34.LEDGER
    report=json.loads(rp.read_text(encoding='utf-8'))
    for key,value in {
        'schema':x34.SCHEMA,
        'status':'FROZEN_CAUSAL_DIAGNOSIS_DEV_NOT_CERTIFIED',
        'parent_hashes':ancestors,'train_dataset_sha256':train_sha,
        'datasets_sha256':{k:sha_rows(v) for k,v in {**old_dev,**fresh34}.items()},
        'new_optimizer_steps':0,'new_train_cases':0,'replay_passed':True,
        'frozen_g1':True,'frozen_g2_0031_occurrence_graph':True,
        'frozen_g2_0032_completeness':True,'frozen_g2_0033_set_decoder':True,
        'dev_labels_used_for_training':False,'dev_labels_used_for_calibration':False,
        'sealed_test_used':False,'production_promotion':False,'g2_certified':False,
    }.items():
        if report.get(key)!=value:
            raise ValueError('GAMER_G2_0035_PARENT_0034_CONTRACT:'+key)
    if report.get('ledger_sha256')!=backbone.sha256(lp):
        raise ValueError('GAMER_G2_0035_PARENT_0034_LEDGER_HASH')
    cohorts={**old_dev,**fresh34};records={k:[] for k in cohorts}
    with gzip.open(lp,'rt',encoding='utf-8') as stream:
        for line in stream:
            row=json.loads(line);split=row.get('group')
            if split not in records or len(records[split])>=len(cohorts[split]):
                raise ValueError('GAMER_G2_0035_PARENT_0034_CASE_COUNT')
            origin=cohorts[split][len(records[split])]
            if (row.get('index')!=len(records[split])+1 or
                    row.get('prompt_sha256')!=base._sha(origin['prompt']) or
                    row.get('family')!=origin['family'] or
                    row.get('gold_total')!=len(base.gold_binding_edges(origin))):
                raise ValueError('GAMER_G2_0035_PARENT_0034_CASE_IDENTITY')
            records[split].append(row)
    if any(len(records[k])!=len(v) for k,v in cohorts.items()):
        raise ValueError('GAMER_G2_0035_PARENT_0034_CASE_COUNT')
    for split,rs in records.items():
        if (report['by_split'][split]!=p30.aggregate(rs) or
                report['causal_by_split'][split]!=x34.summarize_causality(rs)):
            raise ValueError('GAMER_G2_0035_PARENT_0034_AGGREGATE:'+split)
    for k,splits in (('replayed_0033_dev',x34.REPLAY),('new_0034_dev',x34.NEW)):
        if report['evaluation'][k]!=p30.aggregate([r for split in splits for r in records[split]]):
            raise ValueError('GAMER_G2_0035_PARENT_0034_EVALUATION:'+k)
    return records,{'g2_0034_report_sha256':backbone.sha256(rp),
                    'g2_0034_ledger_sha256':backbone.sha256(lp)}


def fresh_cohorts(state,cal30,dev30,cal31,dev31,train32,fit32,audit32,dev32,
                  train33,fit33,audit33,dev33,dev34):
    previous=[*state['training'],*state['history29'],
        *(r for v in state['fresh29'].values() for r in v),
        *cal30,*(r for v in dev30.values() for r in v),
        *cal31,*(r for v in dev31.values() for r in v),
        *train32,*fit32,*audit32,*(r for v in dev32.values() for r in v),
        *train33,*fit33,*audit33,*(r for v in dev33.values() for r in v),
        *(r for v in dev34.values() for r in v)]
    previous=list({base._sha(r['prompt']):r for r in previous}.values())
    independent=general16.independent_rows((previous,),limit=320,seed=SEED)
    topology=general16.heldout_topology_rows((previous,independent),limit=128,seed=SEED+1)
    all_rows=[*previous,*independent,*topology]
    if (len(independent)!=320 or len(topology)!=128 or
            len({base._sha(r['prompt']) for r in all_rows})!=len(all_rows) or
            any(r.get('split')!='dev' for r in (*independent,*topology)) or
            any(r.get('structural_topology') in {'2x4','4x2','4x4'} for r in independent) or
            {r['structural_topology'] for r in topology}!={'2x4','4x2','4x4'}):
        raise ValueError('GAMER_G2_0035_COHORT_LEAKAGE')
    return dict(zip(NEW,(independent,topology)))


def recover_predicted_spans(g1_output,prepared,baseline,*,max_extra=MAX_EXTRA_SPANS):
    """Add bounded neural G1 alternatives, without changing selected G1 spans.

    The earlier G1-0030 selector is the baseline. Candidates are actual frozen
    G1-0029 neural proposals (confidence >= .5), never strings or gold spans.
    Deliberately allow overlap: a missed correct span may compete with an
    already-selected incorrect span. G1 itself remains frozen and unchanged.
    """
    if type(max_extra) is not int or max_extra<0:
        raise ValueError('GAMER_G2_0035_BAD_ENDPOINT_BUDGET')
    stages=g1diag.frozen_stages(g1_output,prepared)
    existing=base.predicted_anchors(baseline,prepared)
    if len(set(existing))!=len(existing) or len(existing)>128:
        raise ValueError('GAMER_G2_0035_BASE_ANCHOR_DRIFT')
    candidates=[]
    for (first,last,kind),margin in stages['all_catalog'].items():
        if not math.isfinite(margin) or margin<=0:continue
        span={'token_first':int(first),'token_last':int(last),'kind':kind}
        bounds=token_span_char_bounds(span,prepared)
        if bounds is None:continue
        anchor=(*bounds,kind)
        if anchor in existing:continue
        # Canonical token-level evidence only. Favor high scores while avoiding
        # a fixed vocabulary or invented graph structure.
        candidates.append((float(margin)*(last-first+1)**.25,
                           int(first),int(last),kind,span,anchor))
    candidates.sort(key=lambda a:(-a[0],a[1],a[2],a[3]))
    selected=list(baseline);seen=set(existing)
    for _score,_first,_last,_kind,span,anchor in candidates:
        if len(selected)>=128 or len(selected)-len(baseline)>=max_extra:break
        if anchor in seen:continue
        selected.append(span);seen.add(anchor)
    selected.sort(key=lambda s:(s['token_first'],s['token_last'],s['kind']))
    if not set(existing)<=set(base.predicted_anchors(selected,prepared)):
        raise ValueError('GAMER_G2_0035_LOST_PARENT_ANCHOR')
    return selected


def capture_variants(head,model,tok,rows,*,device,max_length,group):
    """One frozen backbone/G1 pass; G2 node embeddings for each endpoint arm."""
    if any(p.requires_grad for p in (*model.parameters(),*head.parameters())):
        raise ValueError('GAMER_G2_0035_UPSTREAM_NOT_FROZEN')
    captured=[]
    for index,row in enumerate(rows,1):
        with torch.inference_mode():
            prepared=backbone.prepare_prompt(tok,row['prompt'],max_input_tokens=max_length)
            hidden=backbone.frozen_context(model,prepared,device=device)
            eligible=prepared['eligible'].to(device)
            g1=head(hidden,eligible=eligible,active_stage='G1')
            spans=base.predicted_spans(g1,prepared)
            extra=recover_predicted_spans(g1,prepared,spans)
            cases={}
            for label,proposal in (('baseline',spans),('expanded',extra)):
                anchors=base.predicted_anchors(proposal,prepared)
                if not 1<=len(anchors)<=128 or len(anchors)!=len(set(anchors)):
                    raise ValueError('GAMER_G2_0035_ANCHOR_COUNT')
                g2=head(hidden,eligible=eligible,training_spans=proposal,active_stage='G2')
                vectors=g2['node_vectors'][:len(anchors)].detach().cpu().contiguous()
                windows,mask=x31.occurrence_windows(hidden,prepared,anchors)
                cases[label]={'index':index,'family':row['family'],
                  'prompt_sha256':base._sha(row['prompt']),'anchors':anchors,
                  'gold':base.gold_binding_edges(row), 'vectors':vectors,
                  'windows':windows.clone(),'window_mask':mask.clone()}
            if not set(cases['baseline']['anchors'])<=set(cases['expanded']['anchors']):
                raise ValueError('GAMER_G2_0035_ENDPOINT_MONOTONICITY')
        captured.append(cases)
        if index==1 or index%20==0 or index==len(rows):
            print(f'GAMER_G2_0035_CAPTURE {group} {index}/{len(rows)}',flush=True)
    return captured



class _BoundedWideReference:
    """Apply the *frozen* top-24 reference to a wider, label-blind inventory.

    For each source/relation group, its original first 24 candidates are scored
    together, identically to the legacy scorer. The additional ranks are scored
    in deterministic windows with the group's strongest 12 original candidates
    as contextual competitors. Each scorer invocation sees <=24 per group;
    neither the network weights nor the legacy G2-0023 contract is modified.

    These are bounded-context scores for candidate re-ranking, not the output of
    a hypothetical reference network trained with all 48 competitors jointly.
    """
    def __init__(self, scorer):
        self.scorer=scorer

    def __call__(self, features, base_scores, groups, *, with_competition=True,
                 contextual=True):
        if (len(features)!=len(base_scores) or len(groups)!=len(features) or
                not with_competition or not contextual or
                any(groups[i]>groups[i+1] for i in range(len(groups)-1))):
            raise ValueError('GAMER_G2_0035_BAD_BOUNDED_REFERENCE')
        if not groups:
            return self.scorer(features,base_scores,groups,
                with_competition=with_competition,contextual=contextual)
        # Preserve the original scorer invocation (and exact scores) when there
        # are at most 24 destinations per source/relation group.
        starts=[];at=0
        while at<len(groups):
            end=at+1
            while end<len(groups) and groups[end]==groups[at]:end+=1
            if end-at>TOP_K_WIDE:
                raise ValueError('GAMER_G2_0035_GROUP_EXCEEDS_WIDE_PROTOCOL')
            starts.append((at,end));at=end
        if all(end-start<=TOP_K_BASE for start,end in starts):
            return self.scorer(features,base_scores,groups,
                with_competition=with_competition,contextual=contextual)
        original=[];orig_groups=[];supplement=[];supp_groups=[]
        supp_out=[];supp_pos=[]
        group_counter=0
        for start,end in starts:
            head=list(range(start,min(start+TOP_K_BASE,end)))
            original.extend(head)
            orig_groups.extend([groups[start]]*len(head))
            if end-start<=TOP_K_BASE:continue
            # The upper half of the established candidate group anchors each
            # new score to credible competitors; the remaining slots contain
            # previously excluded destinations. Scores of head entries never
            # depend on how many extra candidates are proposed.
            prefix=head[:TOP_K_BASE//2]
            for tail_at in range(start+TOP_K_BASE,end,TOP_K_BASE//2):
                tail=list(range(tail_at,min(tail_at+TOP_K_BASE//2,end)))
                window=prefix+tail
                offset=len(supplement)
                supplement.extend(window)
                supp_groups.extend([group_counter]*len(window))
                supp_out.extend(tail)
                supp_pos.extend(range(offset+len(prefix),offset+len(window)))
                group_counter+=1
        original_ids=torch.tensor(original,device=features.device,dtype=torch.long)
        output=features.new_empty((len(groups),))
        output[original_ids]=self.scorer(features[original_ids],
            base_scores[original_ids],orig_groups,
            with_competition=with_competition,contextual=contextual)
        if supplement:
            supp_ids=torch.tensor(supplement,device=features.device,dtype=torch.long)
            out_ids=torch.tensor(supp_out,device=features.device,dtype=torch.long)
            pos_ids=torch.tensor(supp_pos,device=features.device,dtype=torch.long)
            scored=self.scorer(features[supp_ids],base_scores[supp_ids],supp_groups,
                with_competition=with_competition,contextual=contextual)
            output[out_ids]=scored[pos_ids]
        if not torch.isfinite(output).all():
            raise ValueError('GAMER_G2_0035_NONFINITE_WIDE_REFERENCE')
        return output


def _scoring_state_for_topk(state,top_k):
    if top_k==TOP_K_BASE:
        return state
    if top_k!=TOP_K_WIDE:
        raise ValueError('GAMER_G2_0035_UNSUPPORTED_INVENTORY_WIDTH')
    return {**state,'reference':_BoundedWideReference(state['reference'])}


def shortlist_for_frozen_decoder(item, *, capacity=24):
    """Use frozen learned occurrence scores to re-rank top-48 to top-24.

    G2-0033's cardinality head has 25 output classes and a strict maximum of
    24 edges per group. Never change that head or silently truncate with raw
    frozen G2 pair rank; use the 3 learned occurrence scorers' CONSENSUS rank.
    No gold, authored text, or graph outcomes enter the shortlist.
    """
    groups=np.asarray(item['groups'],dtype=np.int32)
    edges=item['edges']
    if len(groups)!=len(edges) or type(capacity) is not int or capacity<1:
        raise ValueError('GAMER_G2_0035_BAD_SHORTLIST')
    sources=[f'occurrence_graph_s{seed}' for seed in SEEDS]
    if any(name not in item['raw'] or len(item['raw'][name])!=len(edges)
           for name in sources):
        raise ValueError('GAMER_G2_0035_MISSING_FROZEN_SCORES')
    keep=[]
    for group in sorted(set(groups.tolist())):
        ids=np.flatnonzero(groups==group).tolist()
        if len(ids)<=capacity:
            keep.extend(ids);continue
        rank_sum={i:0 for i in ids}
        for source in sources:
            scores=np.asarray(item['raw'][source],dtype=np.float64)
            if not np.isfinite(scores).all():
                raise ValueError('GAMER_G2_0035_NONFINITE_SHORTLIST_SCORE')
            order=sorted(ids,key=lambda i:(-float(scores[i]),edges[i]))
            for rank,i in enumerate(order):rank_sum[i]+=rank
        chosen=sorted(ids,key=lambda i:(rank_sum[i],edges[i]))[:capacity]
        keep.extend(chosen)
    keep=sorted(keep)
    if len(keep)==len(edges):
        return item
    out={**item,
        'edges':[edges[i] for i in keep],
        'groups':groups[keep],
        'raw':{name:np.asarray(value)[keep] for name,value in item['raw'].items()},
        'candidate_count':len(keep)}
    # Defer gold-dependent coverage accounting to the post-inference audit.
    return out


def _scores(item,arm,n_nodes,parent_policies,pol32,nets32,nets33,pol33):
    """No gold read. Apply preselected G2-0033 policies to an arm's inventory."""
    selected={};rank={}
    for seed in SEEDS:
        name=f'structured_set_s{seed}';source=f'occurrence_graph_s{seed}'
        cfg=pol33[name]
        edges,logits=x33.select_structured(item,n_nodes,nets33[seed],
            parent_policies[source],cfg,pol32,nets32[seed])
        selected[f'{arm}__structured_s{seed}']=edges
        if logits is None:
            ref=np.asarray(item['raw'][source],dtype=np.float64)
        else:ref=logits
        rank[f'{arm}__structured_s{seed}']=ref
        # Same preselected recovery policy, without re-fitting after widening.
        recovery=f'group_recovery_s{seed}'
        parent=x32.select_complete(item,source,parent_policies[source],
            n_nodes,nets32[seed],pol32[recovery])
        selected[f'{arm}__group_s{seed}']=parent
        rank[f'{arm}__group_s{seed}']=np.asarray(item['raw'][source],dtype=np.float64)
    return selected,rank


def analyze_variants(cases,row,state,models31,parent_policies,pol32,nets32,nets33,pol33,
                     *,device='cpu',split,index,parent34=None):
    """Complete inference before gold is used for diagnostics and metrics."""
    frozen={}
    for arm,(expanded,top_k) in ARMS.items():
        case=cases['expanded' if expanded else 'baseline']
        ex=x31.example_from_case(case,row,_scoring_state_for_topk(state,top_k),
                                 train=False,device=device,top_k=top_k)
        full=x31.score_case(ex,state,models31)
        item=shortlist_for_frozen_decoder(full)
        selected,rank=_scores(item,arm,len(case['anchors']),parent_policies,
                               pol32,nets32,nets33,pol33)
        frozen[arm]=(case,item,selected,rank,full)
    base_case,base_item,base_selected,_,base_full=frozen['baseline']
    if parent34 is not None:
        replay=x33.evaluate_case(base_item,len(base_case['anchors']),row,split,index,
            parent_policies,pol32,nets32,nets33,pol33)
        # Full G2-0033 frozen control must reproduce the G2-0034 case ledger.
        for k in ('group','index','prompt_sha256','family','structural_topology',
                  'candidate_gold','candidate_count','gold_total'):
            if replay.get(k)!=parent34.get(k):
                raise ValueError('GAMER_G2_0035_PARENT_0034_LIVE_REPLAY_DRIFT:'+split+':'+str(index)+':'+k)
        # G2-0034 added oracle and ablation metrics on top of the frozen
        # G2-0033 controls; compare only the common controls, not the larger
        # diagnostics-only dictionary.
        for name,metrics in replay['metrics'].items():
            if parent34.get('metrics',{}).get(name)!=metrics:
                raise ValueError('GAMER_G2_0035_PARENT_0034_LIVE_REPLAY_DRIFT:'+split+':'+str(index)+':'+name)
    # Diagnose and measure only AFTER all four variants have frozen predictions.
    metrics={};coverage={};changed={}
    base_anchors=set(base_case['anchors']);base_inventory=set(base_item['edges'])
    for arm,(case,item,selected,rank,full) in frozen.items():
        gold=item['gold'];inventory=set(item['edges']);anchors=set(case['anchors'])
        ceiling=x34.candidate_ceiling(item,anchors)
        ceiling.pop('candidate_oracle')
        broad=x34.candidate_ceiling(full,anchors)
        broad.pop('candidate_oracle')
        ceiling['pre_shortlist_candidate_gold']=broad['represented_gold']
        ceiling['pre_shortlist_candidate_count']=len(full['edges'])
        ceiling['lost_gold_during_shortlisting']=len((set(full['edges'])-inventory)&gold)
        ceiling['g1_anchor_count']=len(anchors)
        ceiling['added_anchors']=len(anchors-base_anchors)
        ceiling['new_candidates']=len(inventory-base_inventory)
        ceiling['displaced_candidates']=len(base_inventory-inventory)
        ceiling['new_gold_candidates']=len((inventory-base_inventory)&gold)
        ceiling['displaced_gold_candidates']=len((base_inventory-inventory)&gold)
        coverage[arm]=ceiling
        for name,edges in selected.items():
            competing,ranking=p30.ranking_counts(item,rank[name])
            metrics[name]={**p30.measure(edges,item),'competing_gold':competing,
                           'ranking_failures':ranking}
        if arm!='baseline':
            changed[arm]={str(s):{'added':len(selected[f'{arm}__structured_s{s}']-
                                          base_selected[f'baseline__structured_s{s}']),
                              'removed':len(base_selected[f'baseline__structured_s{s}']-
                                            selected[f'{arm}__structured_s{s}'])}
                           for s in SEEDS}
    return {'group':split,'index':index,'prompt_sha256':base._sha(row['prompt']),
        'family':row['family'],'structural_topology':row.get('structural_topology','native'),
        'gold_total':base_item['gold_total'], 'candidate_gold':base_item['candidate_gold'],
        'baseline_candidate_gold':base_item['candidate_gold'],
        'metrics':metrics,'coverage':coverage,'selection_changes':changed}


def summarize_coverage(rows):
    out={name:dict(cases=len(rows),fully_representable=0,candidate_complete=0,
            gold_total=0,represented_gold=0,missing_gold=0,missing_g1_endpoint=0,
            endpoints_present_but_not_in_inventory=0,added_anchors=0,
            new_candidates=0,displaced_candidates=0,
            new_gold_candidates=0,displaced_gold_candidates=0,
            pre_shortlist_candidate_gold=0,pre_shortlist_candidate_count=0,
            lost_gold_during_shortlisting=0,output_budget_limited=0) for name in ARMS}
    for row in rows:
        for name,part in row['coverage'].items():
            dst=out[name]
            dst['fully_representable']+=int(part['fully_representable'])
            dst['candidate_complete']+=int(part['candidate_complete'])
            dst['gold_total']+=part['gold_edges']
            dst['represented_gold']+=part['represented_gold']
            dst['missing_gold']+=part['missing_candidate_gold']
            dst['missing_g1_endpoint']+=part['missing_g1_endpoint']
            dst['endpoints_present_but_not_in_inventory']+=part['endpoints_present_but_not_in_inventory']
            dst['added_anchors']+=part['added_anchors']
            dst['new_candidates']+=part['new_candidates']
            dst['displaced_candidates']+=part['displaced_candidates']
            dst['new_gold_candidates']+=part['new_gold_candidates']
            dst['displaced_gold_candidates']+=part['displaced_gold_candidates']
            dst['pre_shortlist_candidate_gold']+=part['pre_shortlist_candidate_gold']
            dst['pre_shortlist_candidate_count']+=part['pre_shortlist_candidate_count']
            dst['lost_gold_during_shortlisting']+=part['lost_gold_during_shortlisting']
            dst['output_budget_limited']+=int(not part['output_budget_sufficient'])
    return out


def _aggregate(rows):
    # p30.aggregate expects metrics plus one common gold_total per case.
    return p30.aggregate(rows)



def development_gates(fresh,ood,coverage):
    """Predeclared comparisons. No DEV-driven selection or promotion."""
    result={}
    for arm in ARMS:
        if arm=='baseline':continue
        gain=coverage[arm]['represented_gold']-coverage['baseline']['represented_gold']
        new_exactly_representable=(coverage[arm]['fully_representable']-
                                   coverage['baseline']['fully_representable'])
        per_seed=[]
        for seed in SEEDS:
            variants={}
            for mode in ('structured','group'):
                before=fresh['models'][f'baseline__{mode}_s{seed}']
                after=fresh['models'][f'{arm}__{mode}_s{seed}']
                ood_old=ood['models'][f'baseline__{mode}_s{seed}']
                ood_new=ood['models'][f'{arm}__{mode}_s{seed}']
                passed=(after['f1']>=before['f1'] and
                        after['exact_cases']>=before['exact_cases'] and
                        after['ranking_failure_rate']<=before['ranking_failure_rate'] and
                        ood_new['f1']>=ood_old['f1'] and
                        ood_new['exact_cases']>=ood_old['exact_cases'])
                variants[mode]={'f1_delta':after['f1']-before['f1'],
                    'exact_graph_delta':after['exact_cases']-before['exact_cases'],
                    'ood_f1_delta':ood_new['f1']-ood_old['f1'],
                    'ranking_failure_rate_delta':after['ranking_failure_rate']-
                                                   before['ranking_failure_rate'],
                    'passes':bool(passed)}
            per_seed.append({'seed':seed,'models':variants})
        success=(gain>0 and new_exactly_representable>0 and
                 all(all(v['passes'] for v in r['models'].values()) for r in per_seed))
        result[arm]={'new_candidate_gold_delta':gain,
            'fully_representable_graph_delta':new_exactly_representable,
            'all_seeds_nonregressing':bool(success),'seeds':per_seed}
    return {'arms':result,'no_dev_policy_selection':True,
        'diagnostic_success':any(v['all_seeds_nonregressing'] for v in result.values()),
        'production_promotion':False,'g2_certified':False}


def run(*,precision30_run,representation31_run,completeness32_run,structured33_run,
        diagnosis34_run,output,device='cuda',max_length=MAX_LENGTH,**kwargs):
    if max_length!=MAX_LENGTH:
        raise ValueError('GAMER_G2_0035_FIXED_PROTOCOL')
    if Path(output).exists():
        raise FileExistsError('GAMER_G2_0035_IMMUTABLE_OUTPUT')
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
    _,pol33,_,tensors33,hash33=x34.verify_parent33(structured33_run,
        train33=train33,fit33=fit33,audit33=audit33,
        old_dev=dev32,new_dev=dev33,ancestors=ancestors33,
        training_sha=sha_rows(state['training']))
    dev34=x34.fresh_cohorts(state,cal30,dev30,cal31,dev31,train32,fit32,
        audit32,dev32,train33,fit33,audit33,dev33)
    ancestors34={**ancestors33,**hash33}
    parent34,hash34=verify_parent34(diagnosis34_run,ancestors=ancestors34,
        train_sha=sha_rows(state['training']),old_dev=dev33,fresh34=dev34)
    fresh=fresh_cohorts(state,cal30,dev30,cal31,dev31,train32,fit32,audit32,
        dev32,train33,fit33,audit33,dev33,dev34)
    models31=x32.load_parent_models(tensors31,device=device)
    nets32=x33.load_parent32_models(tensors32,device=device)
    nets33=x34.load_parent33_models(tensors33,device=device)
    parent_policies={'calibrated_reference_0030':pol30['calibrated_reference'],
        'calibrated_reference_0031':pol31['calibrated_reference'],
        'predicate_argument_0030':pol30[x31.CHAMPION],
        **{k:pol31[k] for k in x32.SOURCE_NAMES}}
    evaluated={}
    for split,rows in {**dev34,**fresh}.items():
        records=[]
        for start in range(0,len(rows),20):
            block=rows[start:start+20]
            captured=capture_variants(state['head'],state['model'],state['tokenizer'],
                block,device=device,max_length=max_length,group=split)
            for offset,(row,cases) in enumerate(zip(block,captured),start+1):
                # Original indexes matter only for traceability, never input evidence.
                record=analyze_variants(cases,row,state,models31,parent_policies,
                    pol32,nets32,nets33,pol33,device=device,split=split,index=offset,
                    parent34=(parent34[split][offset-1] if split in dev34 else None))
                records.append(record)
        evaluated[split]=records
    x31.rel.validate_original_state(state['head'],state['head_before'])
    if any(not torch.equal(v,state['reference'].state_dict()[k].detach().cpu())
           for k,v in state['reference_before'].items()):
        raise ValueError('GAMER_G2_0035_FROZEN_REFERENCE_MUTATION')
    if any(p.requires_grad for p in state['model'].parameters()) or any(
        p.requires_grad for nets in models31.values() for net in nets.values()
        for p in net.parameters()) or any(
        p.requires_grad for pair in nets32.values() for net in pair
        for p in net.parameters()) or any(
        p.requires_grad for net in nets33.values() for p in net.parameters()):
        raise ValueError('GAMER_G2_0035_FROZEN_MODEL_MUTATION')
    replay=[r for k in REPLAY for r in evaluated[k]]
    new=[r for k in NEW for r in evaluated[k]]
    fresh_metrics=_aggregate(new)
    topology_metrics=_aggregate(evaluated[NEW[1]])
    fresh_coverage=summarize_coverage(new)
    report={'schema':SCHEMA,'status':'CANDIDATE_COVERAGE_DEV_NOT_CERTIFIED',
        'parent_hashes':{**ancestors34,**hash34},
        'train_dataset_sha256':sha_rows(state['training']),
        'datasets_sha256':{k:sha_rows(v) for k,v in {**dev34,**fresh}.items()},
        'protocol':{'arms':{k:{'g1_expanded':e,'top_k':top}
            for k,(e,top) in ARMS.items()},'extra_predicted_g1_spans':MAX_EXTRA_SPANS,
            'seeds':list(SEEDS),
            'wide_reference_scoring':'original-top24-plus-12-anchor-bounded-tail-windows',
            'replayed_dev':448,'new_independent_dev':320,
            'new_withheld_topology_dev':128,'no_policy_search':True},
        'no_training':True,'new_optimizer_steps':0,'frozen_backbone':True,
        'frozen_g1':True,'frozen_g2_0031_occurrence_graph':True,
        'frozen_g2_0032_completeness':True,'frozen_g2_0033_set_decoder':True,
        'all_candidate_proposals_label_blind':True,
        'dev_labels_used_for_training':False,'dev_labels_used_for_calibration':False,
        'sealed_test_used':False,'production_promotion':False,'g2_certified':False,
        'graph_execution_authorized':False,'parent_live_replay_passed':True,
        'evaluation':{'new_0035_dev':fresh_metrics,
                      'replayed_0034_dev':_aggregate(replay)},
        'coverage_by_cohort':{'new_0035_dev':fresh_coverage,
                              'replayed_0034_dev':summarize_coverage(replay)},
        'development_gates':development_gates(fresh_metrics,topology_metrics,fresh_coverage),
        'by_split':{k:_aggregate(v) for k,v in evaluated.items()},
        'coverage_by_split':{k:summarize_coverage(v) for k,v in evaluated.items()},
        'limitations':{'frozen_g1_extra_spans_are_not_trained':True,
          'increased_candidates_may_reduce_precision':True,
          'no_dev_selected_champion':True,
          'exact_span_matching_for_endpoint_coverage':True,
          'no_trainable_relationship_discriminator_changed':True,
          'g2_0035_is_candidate_coverage_only':True}}
    out=Path(output);out.mkdir(parents=True,exist_ok=False)
    x32._write_ledger(out/LEDGER,[*replay,*new])
    report['ledger_sha256']=backbone.sha256(out/LEDGER)
    (out/REPORT).write_text(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n',
                            encoding='utf-8')
    return report
