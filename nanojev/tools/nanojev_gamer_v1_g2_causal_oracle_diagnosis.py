"""G2-0041: TRAIN-only causal oracle interventions on the recovered-graph frontier.

No training, checkpoint mutation, DEV scoring, policy selection, or promotion.
Oracle supervision is permitted exclusively to diagnose TRAIN graphs after frozen
candidate generation and ranking have been completed.
"""
from __future__ import annotations

import gzip
import json
import math
from collections import Counter
from pathlib import Path

import numpy as np
from safetensors.torch import load_file

from tools import nanojev_gamer_v1_g2_completion_bakeoff as g40

base=g40.base
x33=g40.x33
p30=g40.p30
backbone=g40.backbone
SCHEMA='nanojev.gamer.graph.g2.causal-oracle-diagnosis.v1'
REPORT='g2_causal_oracle_diagnosis_report.json'
LEDGER='g2_causal_oracle_diagnosis_per_case.jsonl.gz'
PARENT=g40.REPORT
# The diagnostic does not optimize these controls; they are fixed factor interventions.
ARMS=('routed','model_structured','oracle_total_count','oracle_group_counts',
      'oracle_edge_ranking','oracle_total_and_group_counts',
      'oracle_ranking_and_total','oracle_ranking_and_group_counts',
      'oracle_all_factors','direct_gold_control')


def verify_parent40(directory):
    folder=Path(directory)
    report=json.loads((folder/g40.REPORT).read_text(encoding='utf-8'))
    expected={
      'schema':g40.SCHEMA,'status':'TRAIN_BAKEOFF_NO_SAFE_WINNER',
      'development_evaluation_skipped':True,'production_promotion':False,
      'sealed_test_used':False,'dev_labels_used_for_calibration':False,
      'dev_labels_used_for_training':False,'g2_certified':False,
      'by_split':{},'evaluation':{},'frozen_g1':True,
      'frozen_g2_0033_parent':True,'frozen_g2_0039_scorer':True,
    }
    for k,v in expected.items():
        if report.get(k)!=v:raise ValueError('GAMER_G2_0041_PARENT_0040_CONTRACT:'+k)
    if report.get('train_policy',{}).get('enabled') or report.get('train_policy',{}).get('arm') is not None:
        raise ValueError('GAMER_G2_0041_PARENT_POLICY_UNSAFE')
    if not (report.get('opportunity_frontier',{}).get('fit',{}).get('expanded_reachable',{}).get('cases',0)>0
            and report.get('train_records',{}).get('expanded_fit',0)>=g40.TARGET_EXPANDED_FIT):
        raise ValueError('GAMER_G2_0041_PARENT_NO_OPPORTUNITY_PROOF')
    hashes={}
    for name,key in ((g40.REPORT,'g2_0040_report_sha256'),
                     (g40.LEDGER,'g2_0040_ledger_sha256'),
                     (g40.POLICIES,'g2_0040_policies_sha256'),
                     (g40.WEIGHTS,'g2_0040_weights_sha256')):
        path=folder/name
        hashes[key]=backbone.sha256(path)
        if name!=g40.REPORT and report.get(key.removeprefix('g2_0040_'))!=hashes[key]:
            raise ValueError('GAMER_G2_0041_PARENT_HASH:'+name)
    if json.loads((folder/g40.POLICIES).read_text(encoding='utf-8'))!=report['train_policy']:
        raise ValueError('GAMER_G2_0041_PARENT_POLICY_HASH_DRIFT')
    if any(True for _ in gzip.open(folder/g40.LEDGER,'rt',encoding='utf-8')):
        raise ValueError('GAMER_G2_0041_PARENT_DEV_LEDGER_NOT_EMPTY')
    tensors=load_file(str(folder/g40.WEIGHTS),device='cpu')
    parent=g40.x33.GraphSetEnergy().state_dict()
    if set(tensors)!={f'adapted_set.{k}' for k in parent} or any(
        not np.isfinite(tensors['adapted_set.'+k].numpy()).all() or
        tensors['adapted_set.'+k].shape!=v.shape for k,v in parent.items()):
        raise ValueError('GAMER_G2_0041_PARENT_WEIGHT_SHAPES')
    return report,hashes


def _group_order(record,pred):
    edges=record['shortlisted']['edges']
    ids=np.asarray(record['features']['index'],dtype=np.int64)
    logits,logcounts,total=pred
    if len(edges)!=len(ids) or len(edges)!=len(logits) or len(logcounts)!=len(record['features']['groups']):
        raise ValueError('GAMER_G2_0041_MISALIGNED_CANDIDATES')
    if len(edges)!=len(set(edges)) or not all(np.isfinite(np.asarray(a)).all() for a in (logits,logcounts)):
        raise ValueError('GAMER_G2_0041_BAD_CANDIDATE_DATA')
    groups=[]
    for g in range(len(logcounts)):
        ordered=np.flatnonzero(ids==g).tolist()
        ordered.sort(key=lambda i:(-float(logits[i]),edges[i]))
        if not ordered or len(ordered)>x33.MAX_GROUP_EDGES:
            raise ValueError('GAMER_G2_0041_BAD_GROUP')
        groups.append(ordered)
    if sum(map(len,groups))!=len(edges):raise ValueError('GAMER_G2_0041_GROUP_COVERAGE')
    return groups


def oracle_decode(record,gold,*,ranking=False,total_count=False,group_counts=False):
    """Viterbi-style counterfactual. Gold controls only the selected factor.

    When total_count=True, the graph cardinality is an *exact constraint*, not
    a soft penalty. When group_counts=True, counts per relationship group are
    exact constraints. These are interventions, never deployable predictions.
    """
    edges=record['shortlisted']['edges']
    logits,logcounts,_=record['structured_pred']
    scores=np.asarray(logits,dtype=np.float64)
    groupids=np.asarray(record['features']['index'],dtype=np.int64)
    groups=_group_order(record,record['structured_pred'])
    if ranking:
        scores=np.array([24. if edge in gold else -24. for edge in edges],dtype=np.float64)
    n=len(groups);limit=min(x33.source13.MAX_EDGES,len(edges))
    if len(gold)>limit:raise ValueError('GAMER_G2_0041_GOLD_CAPACITY_EXCEEDED')
    weight=0.7  # actual frozen structured-set policy, verified at runtime
    policy=record.get('oracle_policy')
    if policy is not None:
        weight=float(policy['group_weight'])
    dp=np.full(limit+1,-np.inf,dtype=np.float64)
    dp[0]=0.
    traceback=[];orders=[]
    for g in range(n):
        ordered=groups[g] if not ranking else sorted(
            groups[g],key=lambda i:(-float(scores[i]),edges[i]))
        orders.append(ordered)
        k_gold=sum(edges[i] in gold for i in ordered)
        sizes=[k_gold] if group_counts else range(len(ordered)+1)
        prefix=np.r_[0.,np.cumsum(scores[ordered])]
        next_dp=np.full_like(dp,-np.inf)
        chosen=np.zeros(limit+1,dtype=np.uint8)
        for k in sizes:
            if k>limit:continue
            utility=float(prefix[k]+weight*logcounts[g,k])
            possibilities=dp[:limit+1-k]+utility
            improve=possibilities>next_dp[k:]
            next_dp[k:][improve]=possibilities[improve]
            chosen[k:][improve]=k
        dp=next_dp;traceback.append(chosen)
    if total_count:
        count=len(gold)
        if not np.isfinite(dp[count]):raise ValueError('GAMER_G2_0041_ORACLE_TOTAL_UNREACHABLE')
    else:
        global_weight=float(policy['global_weight']) if policy is not None else 0.
        predicted=float(record['structured_pred'][2])
        target=max(0.,min(x33.source13.MAX_EDGES,predicted))
        nums=np.arange(limit+1,dtype=np.float64)
        energy=dp-global_weight*(np.log1p(nums)-math.log1p(target))**2
        count=int(np.argmax(energy))
    result=set()
    for g in range(n-1,-1,-1):
        k=int(traceback[g][count])
        result.update(edges[i] for i in orders[g][:k]);count-=k
    if count:raise ValueError('GAMER_G2_0041_ORACLE_DP_BACKTRACK')
    return result


def _base_structured(record):
    return g40.baseline_selection(record,'constrained')


def one_case(record,*,tag,index):
    row=record['row']
    if row['split']!='train':raise ValueError('GAMER_G2_0041_DEV_GOLD_FORBIDDEN')
    # Compute every label-blind decoder decision before TRAIN gold is read.
    controls={'routed':_base_structured(record),
              'model_structured':x33.decode(record['shortlisted'],record['features'],
                  record['structured_pred'],record['oracle_policy'])}
    gold=base.gold_binding_edges(row)
    info=g40._opportunity_from_gold(record,gold)
    shortlist=set(record['shortlisted']['edges'])
    if info['shortlist_representable'] and info['admitted']:
        controls.update({
          'oracle_total_count':oracle_decode(record,gold,total_count=True),
          'oracle_group_counts':oracle_decode(record,gold,group_counts=True),
          'oracle_edge_ranking':oracle_decode(record,gold,ranking=True),
          'oracle_total_and_group_counts':oracle_decode(record,gold,total_count=True,group_counts=True),
          'oracle_ranking_and_total':oracle_decode(record,gold,ranking=True,total_count=True),
          'oracle_ranking_and_group_counts':oracle_decode(record,gold,ranking=True,group_counts=True),
          'oracle_all_factors':oracle_decode(record,gold,ranking=True,total_count=True,group_counts=True),
          'direct_gold_control':set(gold),
        })
        if controls['oracle_all_factors']!=gold:
            raise ValueError('GAMER_G2_0041_FULL_ORACLE_NOT_EXACT')
        if controls['direct_gold_control']!=gold:
            raise ValueError('GAMER_G2_0041_GOLD_CONTROL_MISALIGNED')
    logits,logcounts,pred_total=record['structured_pred']
    groups=_group_order(record,record['structured_pred'])
    edges=record['shortlisted']['edges']
    inversions=0;missed_best=0;count_l1=0
    prior_inversions=0;prior_ranking_available=False
    local=[];witnesses=[]
    prior=record['shortlisted'].get('raw',{}).get(g40.SOURCE)
    if prior is not None:
        prior=np.asarray(prior,dtype=np.float64)
        if len(prior)!=len(edges) or not np.isfinite(prior).all():
            raise ValueError('GAMER_G2_0041_PARENT_RANK_ALIGNMENT')
        prior_ranking_available=True
    g32=record.get('count_prediction')
    g32_counts=g32[5] if g32 is not None else None
    g32_total=float(g32[6]) if g32 is not None else None
    g32_group_count_l1=0
    for g,indices in enumerate(groups):
        positive=[i for i in indices if edges[i] in gold]
        negative=[i for i in indices if edges[i] not in gold]
        inversions+=sum(float(logits[j])>=float(logits[i]) for i in positive for j in negative)
        if prior_ranking_available:
            prior_inversions+=sum(float(prior[j])>=float(prior[i])
                                  for i in positive for j in negative)
        missed_best+=int(bool(positive and negative and max(float(logits[j]) for j in negative)>=min(float(logits[i]) for i in positive)))
        actual=len(positive);predicted=int(np.argmax(logcounts[g]))
        count_l1+=abs(predicted-actual)
        if g32_counts is not None:
            g32_group_count_l1+=abs(int(np.argmax(g32_counts[g]))-actual)
        inversion_pairs=[(i,j) for i in positive for j in negative
                         if float(logits[j])>=float(logits[i])]
        witnesses.extend({'gold_edge':list(edges[i]),'gold_score':float(logits[i]),
                          'negative_edge':list(edges[j]),'negative_score':float(logits[j]),
                          'group':g} for i,j in inversion_pairs[:max(0,24-len(witnesses))])
        local.append({'group':g,'candidates':len(indices),'gold':actual,
                      'predicted_count':predicted,
                      'top_gold_count':sum(edges[i] in gold for i in indices[:actual]),
                      'inversion_pairs':len(inversion_pairs),
                      'representative_source':edges[indices[0]][0],
                      'representative_relation':edges[indices[0]][2]})
    measured={}
    for name,pred in controls.items():
        missing=gold-pred;extra=pred-gold
        measured[name]={'exact':not (missing or extra),'missing':len(missing),'extra':len(extra),
                        'predicted_count':len(pred),'gold_count':len(gold)}
    parent=_base_structured(record)
    return {'cohort':tag,'index':index,'prompt_sha256':base._sha(row['prompt']),
      'family':row.get('family'),'structural_topology':row.get('structural_topology'),
      'admitted':info['admitted'],'anchor_representable':info['anchor_representable'],
      'shortlist_representable':info['shortlist_representable'],
      'newly_anchor_representable':info['newly_anchor_representable'],
      'repairable':info['repairable'],'parent_exact':parent==gold,
      'candidate_gold':len(gold&shortlist),'shortlist_size':len(shortlist),
      'gold_count':len(gold),'model_total_count':float(pred_total),
      'model_total_count_error':float(pred_total-len(gold)),
      'learned_group_count_l1':count_l1,'misranked_groups':missed_best,
      'pairwise_rank_inversions':inversions,
      'source_rank_inversions':prior_inversions if prior_ranking_available else None,
      'g2_0032_predicted_total':g32_total,
      'g2_0032_total_count_error':(g32_total-len(gold)) if g32_total is not None else None,
      'g2_0032_group_count_l1':g32_group_count_l1 if g32_counts is not None else None,
      'group_details':local,
      'ranking_inversion_witnesses':witnesses,
      'routed_missing_edges':[list(e) for e in sorted(gold-controls['routed'])],
      'routed_false_edges':[list(e) for e in sorted(controls['routed']-gold)],
      'unrepresented_gold_edges':[list(e) for e in sorted(gold-shortlist)],
      'outcomes':measured,
      'oracle_diagnostic_only':True}


def summarize(records):
    buckets={key:[] for key in ('all','expanded','expanded_reachable','expanded_unreachable',
                                   'unchanged','repairable','rescued_repairable')}
    for record in records:
        labels=['all','expanded' if record['admitted'] else 'unchanged']
        if record['admitted']:
            labels.append('expanded_reachable' if record['shortlist_representable'] else 'expanded_unreachable')
        if record['repairable']:
            labels.append('repairable')
            if record['newly_anchor_representable']:labels.append('rescued_repairable')
        for key in labels:buckets[key].append(record)
    result={}
    for key,group in buckets.items():
        outcome={name:{'evaluated':0,'exact':0,'new_exact':0,'lost_exact':0,
                      'missing':0,'extra':0} for name in ARMS}
        for r in group:
            old=r['outcomes']['routed']['exact']
            for name,metrics in r['outcomes'].items():
                t=outcome[name];t['evaluated']+=1;t['exact']+=int(metrics['exact'])
                t['new_exact']+=int(metrics['exact'] and not old)
                t['lost_exact']+=int(old and not metrics['exact'])
                t['missing']+=metrics['missing'];t['extra']+=metrics['extra']
        result[key]={'cases':len(group),'parent_exact':sum(r['parent_exact'] for r in group),
            'shortlist_representable':sum(r['shortlist_representable'] for r in group),
            'rank_inversions':sum(r['pairwise_rank_inversions'] for r in group),
            'group_count_l1':sum(r['learned_group_count_l1'] for r in group),
            'source_rank_inversions':sum(r['source_rank_inversions'] or 0 for r in group),
            'g2_0032_group_count_l1':sum(r['g2_0032_group_count_l1'] or 0 for r in group),
            'g2_0032_abs_total_count_error':sum(abs(r['g2_0032_total_count_error'] or 0) for r in group),
            'abs_total_count_error':sum(abs(r['model_total_count_error']) for r in group),
            'outcomes':outcome}
    return result


def root_cause_counts(records):
    causes=Counter();patterns=Counter()
    for r in records:
        if not r['repairable']:continue
        z=r['outcomes'];success={k for k,v in z.items() if v['exact']}
        patterns['|'.join(sorted(success)) or 'none']+=1
        if 'oracle_total_count' in success:causes['total_count_sufficient']+=1
        if 'oracle_group_counts' in success:causes['group_counts_sufficient']+=1
        if 'oracle_edge_ranking' in success:causes['edge_ranking_sufficient']+=1
        if 'oracle_total_and_group_counts' in success:causes['count_combination_sufficient']+=1
        if 'oracle_ranking_and_total' in success:causes['ranking_and_total_sufficient']+=1
        if 'oracle_ranking_and_group_counts' in success:causes['ranking_and_group_sufficient']+=1
        if 'oracle_all_factors' not in success:causes['full_factor_oracle_failed']+=1
        if not (success & set(ARMS[2:-2])) and 'oracle_all_factors' in success:
            causes['requires_all_factors_or_unmodeled_interaction']+=1
    causes['reachable_cases']=sum(r['repairable'] for r in records)
    return {'counts':dict(causes),'success_patterns':dict(patterns)}


def persist(directory,report,ledger):
    output=Path(directory);output.mkdir(parents=True,exist_ok=False)
    with gzip.open(output/LEDGER,'wt',encoding='utf-8',compresslevel=6) as stream:
        for record in ledger:stream.write(json.dumps(record,sort_keys=True,allow_nan=False)+'\n')
    report['ledger_sha256']=backbone.sha256(output/LEDGER)
    (output/REPORT).write_text(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    return report


def run(*,bakeoff40_run,output,**kwargs):
    parent,parent_hashes=verify_parent40(bakeoff40_run)
    if Path(output).exists():raise FileExistsError('GAMER_G2_0041_IMMUTABLE_OUTPUT')
    def callback(*,training,fitting,auditing,training_frontier,fit_frontier,
                 natural_audit_frontier,train_rows,fit_rows,audit_rows,meta,pol33,**unused):
        if any(parent['training_sha256'].get(key)!=g40.sha_rows(rows) for key,rows in
            (('train_0040',train_rows),('train_fit_0040',fit_rows),('train_audit_0040',audit_rows))):
            raise ValueError('GAMER_G2_0041_PARENT_COHORT_DRIFT')
        if parent['opportunity_frontier']!={'train':training_frontier,'fit':fit_frontier,
                                            'natural_audit':natural_audit_frontier}:
            raise ValueError('GAMER_G2_0041_PARENT_OPPORTUNITY_DRIFT')
        policy=pol33[f'structured_set_s{g40.SOURCE_SEED}']
        if not policy['enabled']:
            raise ValueError('GAMER_G2_0041_STRUCTURED_PARENT_DISABLED')
        proof={'seed':g40.SOURCE_SEED,'config':policy,
               'gold_labels_used_only_in_train_diagnostics':True}
        records=[]
        for cohort,examples in (('train',training),('train_fit',fitting),('natural_train_audit',auditing)):
            for i,example in enumerate(examples,1):
                # All G1, candidate and scorer inference was completed upstream.
                record=one_case({**example,'oracle_policy':policy},tag=cohort,index=i)
                records.append(record)
            print(f'GAMER_G2_0041_ORACLE {cohort} {len(examples)} cases',flush=True)
        by_cohort={k:summarize([r for r in records if r['cohort']==k])
                   for k in ('train','train_fit','natural_train_audit')}
        frontier={k:root_cause_counts([r for r in records if r['cohort']==k]) for k in by_cohort}
        if by_cohort['train_fit']['repairable']['cases']<=0:
            raise ValueError('GAMER_G2_0041_NO_REACHABLE_FIT_PROBLEM')
        report={'schema':SCHEMA,'status':'TRAIN_CAUSAL_ORACLES_COMPLETE',
          'parent_hashes':parent_hashes,'source_parent_schema':g40.SCHEMA,
          'train_dataset_sha256':parent['train_dataset_sha256'],
          'training_sha256':parent['training_sha256'],
          'cohorts':{k:len(v) for k,v in (('train',training),('train_fit',fitting),
                                           ('natural_train_audit',auditing))},
          'opportunity_frontier':parent['opportunity_frontier'],
          'by_cohort':by_cohort,'causal_counts':frontier,'protocol':proof,
          'frozen_g1':True,'frozen_g2_0031_parent':True,'frozen_g2_0032':True,
          'frozen_g2_0033_parent':True,'frozen_g2_0038_acceptor':True,
          'frozen_g2_0039_scorer':True,'no_training':True,
          'dev_labels_used_for_training':False,'dev_labels_used_for_calibration':False,
          'dev_evaluation_skipped':True,'sealed_test_used':False,
          'production_promotion':False,'g2_certified':False}
        return persist(output,report,records)
    return g40.run(output=output,diagnostic_callback=callback,**kwargs)
