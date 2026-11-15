"""G2-0042: separate order from score calibration and bake off edge-head repairs.

TRAIN-only gold is used for counterfactual diagnosis, training and policy fit.
An independent TRAIN audit may veto (never choose) a fit winner. DEV is read
only after that gate, with immutable results and no promotion or sealed tests.
"""
from __future__ import annotations

import copy
import gzip
import json
import math
import random
from collections import Counter
from pathlib import Path

import numpy as np
import torch
from torch.nn import functional as F
from safetensors.torch import save_file

from tools import nanojev_gamer_v1_g2_causal_oracle_diagnosis as g41

g40=g41.g40
x33=g40.x33
base=g40.base
backbone=g40.backbone
SCHEMA='nanojev.gamer.graph.g2.rank-calibration-bakeoff.v1'
REPORT='g2_rank_calibration_bakeoff_report.json'
LEDGER='g2_rank_calibration_bakeoff_per_case.jsonl.gz'
WEIGHTS='g2_rank_calibration_bakeoff_development.safetensors'
POLICIES='g2_rank_calibration_bakeoff_frozen_policies.json'
STEPS=240
LR=1e-3
SEED=460542
# Policy space is fixed before inspecting TRAIN-fit or DEV data.
CAL_GRID=((.75,-1.5),(.75,0.),(.75,1.5),(1.25,-1.5),(1.25,0.),(1.25,1.5))
RANK_KINDS=('pairwise','hard_negative')
JOINT_BIASES=(-3.,-1.5,1.5,3.)
# Nondeployable and fixed before TRAIN labels are inspected.
ORACLE_SHIFTS=(-20.,-12.,-8.,-5.,-3.,-2.,-1.,0.,1.,2.,3.,5.,8.,12.,20.)
ORACLE_SCALES=(.5,1.,2.)


def verify_parent41(directory, parent40_hashes):
    folder=Path(directory)
    report=json.loads((folder/g41.REPORT).read_text(encoding='utf-8'))
    for key,expected in {'schema':g41.SCHEMA,
                         'status':'TRAIN_CAUSAL_ORACLES_COMPLETE',
                         'parent_hashes':parent40_hashes,
                         'dev_evaluation_skipped':True,
                         'dev_labels_used_for_training':False,
                         'dev_labels_used_for_calibration':False,
                         'sealed_test_used':False,
                         'production_promotion':False,
                         'no_training':True,
                         'g2_certified':False}.items():
        if report.get(key)!=expected:
            raise ValueError('GAMER_G2_0042_PARENT_0041_CONTRACT:'+key)
    if report['causal_counts']['train_fit']['counts'].get('reachable_cases')!=19:
        raise ValueError('GAMER_G2_0042_PARENT_CAUSAL_COHORT_DRIFT')
    if report['causal_counts']['train_fit']['counts'].get('edge_ranking_sufficient')!=19:
        raise ValueError('GAMER_G2_0042_PARENT_CAUSAL_DIAGNOSIS_DRIFT')
    if report.get('ledger_sha256')!=backbone.sha256(folder/g41.LEDGER):
        raise ValueError('GAMER_G2_0042_PARENT_0041_LEDGER_HASH')
    cohorts={k:[] for k in ('train','train_fit','natural_train_audit')}
    with gzip.open(folder/g41.LEDGER,'rt',encoding='utf-8') as fh:
        for line in fh:
            item=json.loads(line);cohort=item.get('cohort')
            if cohort not in cohorts or item['index']!=len(cohorts[cohort])+1:
                raise ValueError('GAMER_G2_0042_PARENT_0041_LEDGER_ORDER')
            cohorts[cohort].append(item)
    if {k:len(v) for k,v in cohorts.items()}!=report.get('cohorts'):
        raise ValueError('GAMER_G2_0042_PARENT_0041_LEDGER_COUNTS')
    for k,rows in cohorts.items():
        if (sum(bool(r['repairable']) for r in rows)!=
            report['causal_counts'][k]['counts']['reachable_cases']):
            raise ValueError('GAMER_G2_0042_PARENT_0041_LEDGER_FRONTIER')
    return report,cohorts,{'g2_0041_report_sha256':backbone.sha256(folder/g41.REPORT),
                           'g2_0041_ledger_sha256':backbone.sha256(folder/g41.LEDGER)}


def oracle_order_only(record,gold):
    """Within each group, permute the EXISTING score multiset: no rescaling.

    This changes ordering/edge identity, but cannot change the group's sorted
    top-k energy sequence. Hence group-size optimization is held fixed.
    """
    old=np.asarray(record['structured_pred'][0],dtype=np.float64)
    edges=record['shortlisted']['edges']
    ids=np.asarray(record['features']['index'],dtype=np.int64)
    result=old.copy()
    for g in range(len(record['features']['groups'])):
        indices=np.flatnonzero(ids==g).tolist()
        order=sorted(indices,key=lambda i:(edges[i] not in gold,-old[i],edges[i]))
        scores=sorted((float(old[i]) for i in indices),reverse=True)
        for i,value in zip(order,scores):result[i]=value
    return result


def decode_logits(record,scores,policy):
    parent=record['structured_pred']
    assert len(scores)==len(parent[0])
    return x33.decode(record['shortlisted'],record['features'],
                      (np.asarray(scores,dtype=np.float64),parent[1],parent[2]),policy)


def causal_separation(record,policy):
    row=record['row']
    if row.get('split')!='train':
        raise ValueError('GAMER_G2_0042_DEV_ORACLE_FORBIDDEN')
    gold=base.gold_binding_edges(row)
    info=g40._opportunity_from_gold(record,gold)
    parent=g40.baseline_selection(record,'constrained')
    summary={'admitted':info['admitted'],'reachable':info['shortlist_representable'],
             'repairable':info['repairable'],'parent_exact':parent==gold,
             'order_only_exact':None,'calibration_only_exact':None,
             'order_and_calibration_exact':None,'fixed_magnitude_oracle_exact':None,
             'calibration_best':None,'order_calibration_best':None,
             'ranking_inversions':None}
    if not info['repairable']:return summary
    scores=np.asarray(record['structured_pred'][0],dtype=np.float64)
    reordered=oracle_order_only(record,gold)
    summary['order_only_exact']=decode_logits(record,reordered,policy)==gold
    summary['fixed_magnitude_oracle_exact']=summary['order_only_exact']
    ids=np.asarray(record['features']['index'],dtype=np.int64)
    edges=record['shortlisted']['edges']
    summary['ranking_inversions']=sum(
        float(scores[j])>=float(scores[i])
        for g in range(len(record['features']['groups']))
        for i in np.flatnonzero(ids==g) if edges[i] in gold
        for j in np.flatnonzero(ids==g) if edges[j] not in gold)
    for scale in ORACLE_SCALES:
        for bias in ORACLE_SHIFTS:
            for key,raw in (('calibration_only',scores),('order_and_calibration',reordered)):
                if summary[key+'_exact']:continue
                if decode_logits(record,scale*raw+bias,policy)==gold:
                    summary[key+'_exact']=True
                    summary['calibration_best' if key=='calibration_only' else 'order_calibration_best']=[scale,bias]
    for key in ('calibration_only_exact','order_and_calibration_exact'):
        if summary[key] is None:summary[key]=False
    return summary


def train_edge_head(records,parent,kind,*,steps=STEPS,seed=SEED):
    """Fine-tune only the G2-0033 edge readout; count heads are frozen.

    Pairwise ranks compete within the same source/relation group. The harder
    arm emphasizes highest-scoring negatives and lowest-scoring positives.
    """
    if kind not in RANK_KINDS or not 1<=steps<=STEPS:
        raise ValueError('GAMER_G2_0042_BAD_TRAIN_KIND')
    expanded=[r for r in records if r['admitted']]
    original=[r for r in records if not r['admitted']]
    if len(expanded)<8 or len(original)<8:
        raise ValueError('GAMER_G2_0042_TRAIN_BALANCE')
    net=copy.deepcopy(parent).to('cpu').eval().requires_grad_(False)
    net.edge_readout.requires_grad_(True)
    before={k:v.detach().clone() for k,v in parent.state_dict().items()}
    optimizer=torch.optim.AdamW(net.edge_readout.parameters(),lr=LR,weight_decay=0.)
    rng=random.Random(seed+(0 if kind=='pairwise' else 17))
    last={}
    for step in range(1,steps+1):
        optimizer.zero_grad(set_to_none=True)
        pieces=[];counts=Counter()
        for rec in (expanded[rng.randrange(len(expanded))],
                    original[rng.randrange(len(original))]):
            if rec['row'].get('split')!='train':
                raise ValueError('GAMER_G2_0042_DEV_TRAIN_FORBIDDEN')
            features=rec['features'];edges=rec['shortlisted']['edges']
            gold=base.gold_binding_edges(rec['row'])
            tensors=x33._tensor_features(features,'cpu')
            logits,_,_=net(*tensors)
            truth=torch.tensor([e in gold for e in edges],dtype=logits.dtype)
            group_ids=features['index']
            losses=[]
            for g in range(len(features['groups'])):
                inds=np.flatnonzero(group_ids==g)
                yes=[i for i in inds if edges[i] in gold]
                no=[i for i in inds if edges[i] not in gold]
                if yes and no:
                    p=logits[yes];n=logits[no]
                    if kind=='hard_negative':
                        p=torch.topk(p,min(2,len(p)),largest=False).values
                        n=torch.topk(n,min(3,len(n)),largest=True).values
                    losses.append(F.softplus(.75+n[:,None]-p[None,:]).mean())
                    counts['groups']+=1
            if losses:
                margin=torch.stack(losses).mean()
            else:
                margin=logits.sum()*0
            bce=F.binary_cross_entropy_with_logits(logits,truth,
                pos_weight=torch.tensor(1.5 if kind=='pairwise' else 2.))
            pieces.append(margin+(0.15 if kind=='pairwise' else .30)*bce)
        loss=torch.stack(pieces).mean()
        if not torch.isfinite(loss):raise ValueError('GAMER_G2_0042_NONFINITE_LOSS')
        loss.backward();torch.nn.utils.clip_grad_norm_(net.edge_readout.parameters(),3.)
        optimizer.step()
        last={'step':step,'loss':float(loss.detach()),'pair_groups':counts['groups']}
        if step==1 or step%60==0 or step==steps:
            print(f'GAMER_G2_0042_TRAIN {kind} {step}/{steps} '
                  f'loss={last["loss"]:.5f} pairs={counts["groups"]}',flush=True)
    if any(not torch.equal(v,parent.state_dict()[k]) for k,v in before.items()):
        raise ValueError('GAMER_G2_0042_PARENT_MUTATED')
    for k,v in net.state_dict().items():
        if not k.startswith('edge_readout.') and not torch.equal(v,before[k]):
            raise ValueError('GAMER_G2_0042_COUNT_HEAD_MUTATED:'+k)
    net.eval().requires_grad_(False)
    return net,last


def candidate_configs():
    configs=[{'kind':'calibration','scale':scale,'bias':bias} for scale,bias in CAL_GRID]
    for name in RANK_KINDS:
        configs.append({'kind':name,'scale':1.,'bias':0.})
        for bias in JOINT_BIASES:
            configs.append({'kind':name,'scale':1.,'bias':bias})
    return configs


def learned_order_preserving_magnitudes(record,learned):
    """Learned rank reassignment holds each group's original score multiset fixed.

    The ranking-only arm therefore cannot change the group's top-k energy or
    chosen cardinality. Joint arms add an explicit affine calibration afterward.
    """
    parent=np.asarray(record['structured_pred'][0],dtype=np.float64)
    learned=np.asarray(learned,dtype=np.float64)
    if len(parent)!=len(learned) or not np.isfinite(learned).all():
        raise ValueError('GAMER_G2_0042_RANK_SHAPE')
    result=parent.copy()
    groupids=np.asarray(record['features']['index'],dtype=np.int64)
    edges=record['shortlisted']['edges']
    for group in range(len(record['features']['groups'])):
        indices=np.flatnonzero(groupids==group).tolist()
        order=sorted(indices,key=lambda i:(-float(learned[i]),edges[i]))
        original_sorted=sorted((float(parent[i]) for i in indices),reverse=True)
        for i,value in zip(order,original_sorted):result[i]=value
    return result


def augmented_score(record,cfg,nets):
    parent=record['structured_pred']
    if cfg['kind']=='calibration':logits=parent[0]
    else:
        predicted=x33.predictions(record['features'],nets[cfg['kind']])
        # A learned ranking head must leave both count heads unchanged.
        if not (np.array_equal(predicted[1],parent[1]) and predicted[2]==parent[2]):
            raise ValueError('GAMER_G2_0042_COUNT_MODEL_CHANGED')
        logits=learned_order_preserving_magnitudes(record,predicted[0])
    logits=cfg['scale']*np.asarray(logits,dtype=np.float64)+cfg['bias']
    return (logits,parent[1],parent[2])


def choose(record,cfg,nets,policy):
    old=g40.baseline_selection(record,'constrained')
    if not record['admitted']:return old
    score=augmented_score(record,cfg,nets)
    return x33.decode(record['shortlisted'],record['features'],score,policy)


def measure(records,cfg,nets,policy,*,allow_dev=False):
    counts=Counter();base_counts=Counter()
    cases=[]
    for rec in records:
        row=rec['row']
        if row['split']!='train' and not allow_dev:
            raise ValueError('GAMER_G2_0042_DEV_TRAIN_METRICS_FORBIDDEN')
        gold=base.gold_binding_edges(row)
        before=g40.baseline_selection(rec,'constrained')
        after=choose(rec,cfg,nets,policy)
        false=after-gold;missed=gold-after
        old_false=before-gold;old_missed=gold-before
        new=not false and not missed
        prior=not old_false and not old_missed
        counts.update({'correct':len(gold&after),'false_positive':len(false),
            'missing':len(missed),'exact_cases':int(new)})
        base_counts.update({'correct':len(gold&before),'false_positive':len(old_false),
            'missing':len(old_missed),'exact_cases':int(prior)})
        if rec['admitted']:
            # Rank errors are evaluated with the actual score source, not the
            # reference scorer used by the earlier completion bake-off.
            s=augmented_score(rec,cfg,nets)[0]
            old=rec['structured_pred'][0]
            ids=rec['features']['index'];edges=rec['shortlisted']['edges']
            inv=lambda z:sum(float(z[j])>=float(z[i])
                for g in range(len(rec['features']['groups']))
                for i in np.flatnonzero(ids==g) if edges[i] in gold
                for j in np.flatnonzero(ids==g) if edges[j] not in gold)
            counts['rank_inversions']+=inv(s)
            base_counts['rank_inversions']+=inv(old)
        cases.append({'prompt_sha256':base._sha(row['prompt']),
          'admitted':bool(rec['admitted']),
          'reachable':bool(rec.get('opportunity',{}).get('shortlist_representable',gold<=set(rec['shortlisted']['edges']))),
          'new_exact':bool(new and not prior),'lost_exact':bool(prior and not new),
          'exact':bool(new),'parent_exact':bool(prior),
          'missing':len(missed),'false_positive':len(false)})
    f1=lambda v:(2*v['correct']/max(1,2*v['correct']+v['false_positive']+v['missing']))
    return {'cases':len(records),'f1':f1(counts),'parent_f1':f1(base_counts),
        'exact':counts['exact_cases'],'parent_exact':base_counts['exact_cases'],
        'new_exact':sum(r['new_exact'] for r in cases),
        'lost_exact':sum(r['lost_exact'] for r in cases),
        'fp':counts['false_positive'],'parent_fp':base_counts['false_positive'],
        'rank_inversions':counts['rank_inversions'],
        'parent_rank_inversions':base_counts['rank_inversions'],
        'reachable_expanded':sum(r['admitted'] and r['reachable'] for r in cases),
        'new_exact_reachable':sum(r['new_exact'] and r['reachable'] for r in cases)},cases


def admissible(stat,*,require_gain):
    return (stat['new_exact']>0 or not require_gain) and stat['lost_exact']==0 and \
           stat['f1']>=stat['parent_f1'] and stat['fp']<=stat['parent_fp'] and \
           stat['rank_inversions']<=stat['parent_rank_inversions']


def select(fit,audit,nets,policy):
    ledgers=[];choices=[]
    for idx,cfg in enumerate(candidate_configs()):
        metrics,_=measure(fit,cfg,nets,policy)
        eligible=admissible(metrics,require_gain=True)
        item={'config':cfg,'fit':metrics,'eligible_fit':eligible,
              'audit':None,'eligible_audit':False}
        ledgers.append(item)
        if eligible:
            choices.append(((metrics['new_exact'],metrics['f1']-metrics['parent_f1'],
                             -metrics['fp'],-idx),item))
    if not choices:
        return {'enabled':False,'config':None,'disabled_reason':'no_safe_train_fit_winner'},ledgers
    winner=max(choices,key=lambda x:x[0])[1]
    audit_metrics,_=measure(audit,winner['config'],nets,policy)
    winner['audit']=audit_metrics
    winner['eligible_audit']=admissible(audit_metrics,require_gain=False)
    if not winner['eligible_audit']:
        return {'enabled':False,'config':None,
                'disabled_reason':'winner_failed_independent_natural_train_audit'},ledgers
    return {'enabled':True,'config':winner['config']},ledgers


def persist(output,report,ledger,weights,policy):
    directory=Path(output)
    if directory.exists():raise FileExistsError('GAMER_G2_0042_IMMUTABLE_OUTPUT')
    directory.mkdir(parents=True)
    with gzip.open(directory/LEDGER,'wt',encoding='utf-8',compresslevel=6) as fh:
        for item in ledger:fh.write(json.dumps(item,sort_keys=True,allow_nan=False)+'\n')
    save_file({f'{kind}.{k}':v.detach().clone().contiguous()
        for kind,net in weights.items() for k,v in net.state_dict().items()},str(directory/WEIGHTS))
    (directory/POLICIES).write_text(json.dumps(policy,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    report.update({'ledger_sha256':backbone.sha256(directory/LEDGER),
                   'weights_sha256':backbone.sha256(directory/WEIGHTS),
                   'policies_sha256':backbone.sha256(directory/POLICIES)})
    (directory/REPORT).write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf-8')
    return report


def run(*,bakeoff40_run,oracle41_run,output,**kwargs):
    parent40,hash40=g41.verify_parent40(bakeoff40_run)
    parent41,ledger41,hash41=verify_parent41(oracle41_run,hash40)
    if Path(output).exists():raise FileExistsError('GAMER_G2_0042_IMMUTABLE_OUTPUT')
    def callback(*,training,fitting,auditing,train_rows,fit_rows,audit_rows,
                 pol33,parent33_models,post_selection_dev_capture,assert_all_frozen,**ignored):
        if any(parent40['training_sha256'][name]!=g40.sha_rows(rows) for name,rows in
               (('train_0040',train_rows),('train_fit_0040',fit_rows),
                ('train_audit_0040',audit_rows))):
            raise ValueError('GAMER_G2_0042_PARENT_TRAIN_COHORT_DRIFT')
        if parent40['training_sha256']!=parent41['training_sha256']:
            raise ValueError('GAMER_G2_0042_PARENT_TRAIN_HASH_DRIFT')
        groups=(('train',training),('train_fit',fitting),('natural_train_audit',auditing))
        for cohort,records in groups:
            compare=ledger41[cohort]
            if len(records)!=len(compare):raise ValueError('GAMER_G2_0042_PARENT_CASE_COUNT')
            for ordinal,(case,control) in enumerate(zip(records,compare),1):
                info=case.get('opportunity') or g40.reachable_opportunity(case)
                if any((control['index']!=ordinal,
                    control['prompt_sha256']!=base._sha(case['row']['prompt']),
                    control['admitted']!=info['admitted'],
                    control['repairable']!=info['repairable'],
                    control['shortlist_representable']!=info['shortlist_representable'])):
                    raise ValueError('GAMER_G2_0042_PARENT_CASE_IDENTITY')
        policy=pol33[f'structured_set_s{g40.SOURCE_SEED}']
        if policy!=parent41['protocol']['config']:
            raise ValueError('GAMER_G2_0042_PARENT_DECODER_POLICY_DRIFT')
        # Stage 1: interventions on captured TRAIN data only.
        diagnostic=[]
        for cohort,records in groups:
            for ix,record in enumerate(records,1):
                finding=causal_separation(record,policy)
                diagnostic.append({'cohort':cohort,'index':ix,
                    'prompt_sha256':base._sha(record['row']['prompt']),**finding})
            print(f'GAMER_G2_0042_FACTOR_DIAG {cohort} {len(records)}',flush=True)
        by_diag={k:{'cases':len(records),
            'repairable':sum(r['repairable'] for r in diagnostic if r['cohort']==k),
            'order_only':sum(r['order_only_exact'] is True for r in diagnostic if r['cohort']==k),
            'calibration_only':sum(r['calibration_only_exact'] is True for r in diagnostic if r['cohort']==k),
            'order_and_calibration':sum(r['order_and_calibration_exact'] is True for r in diagnostic if r['cohort']==k)}
            for k,records in groups}
        # Stage 2: TRAIN-only fixed-head hard-negative learning.
        return stage2(training,fitting,auditing,policy,parent33_models[g40.SOURCE_SEED],
                      by_diag,diagnostic,post_selection_dev_capture,assert_all_frozen,
                      output,hash40,hash41,parent41)
    return g40.run(output=output,diagnostic_callback=callback,**kwargs)


def stage2(training,fitting,auditing,policy,parent_net,diagnosis,diagnostic,
           dev_capture,assert_frozen,output,hash40,hash41,parent41):
    parent_copy={k:v.detach().clone() for k,v in parent_net.state_dict().items()}
    nets={};losses={}
    for kind in RANK_KINDS:
        nets[kind],losses[kind]=train_edge_head(training,parent_net,kind)
    if any(not torch.equal(parent_net.state_dict()[k],v)
           for k,v in parent_copy.items()):
        raise ValueError('GAMER_G2_0042_FROZEN_SET_PARENT_MUTATED')
    assert_frozen()
    winner,calibration=select(fitting,auditing,nets,policy)
    print('GAMER_G2_0042_SELECT '+json.dumps(winner,sort_keys=True),flush=True)
    details={'calibration_grid':CAL_GRID,'joint_biases':JOINT_BIASES,
             'rank_objectives':RANK_KINDS,'train_steps':STEPS,
             'oracle_scales':ORACLE_SCALES,'oracle_shifts':ORACLE_SHIFTS,
             'independent_train_audit_can_only_veto':True,
             'no_dev_train_or_policy_selection':True,
             'parent_count_model_frozen':True,
             'unchanged_graphs_preserved':True}
    report={'schema':SCHEMA,'status':'TRAIN_SCORE_BAKEOFF_NO_SAFE_WINNER',
       'parent_hashes':{**hash40,**hash41},
       'source_parent_schema':g41.SCHEMA,
       'train_dataset_sha256':parent41['train_dataset_sha256'],
       'training_sha256':parent41['training_sha256'],
       'factor_separation_by_cohort':diagnosis,
       'opportunity_frontier':parent41['opportunity_frontier'],
       'train_bakeoff':calibration,'train_policy':winner,
       'training_losses':losses,'protocol':details,
       'by_split':{},'development_gates':{'passed':False,'reason':'no_train_winner'},
       'development_evaluation_skipped':not winner['enabled'],
       'dev_labels_used_for_training':False,'dev_labels_used_for_calibration':False,
       'sealed_test_used':False,'production_promotion':False,'g2_certified':False,
       'frozen_g1':True,'frozen_g2_0031_parent':True,'frozen_g2_0032':True,
       'frozen_g2_0033_parent':True,'frozen_g2_0038_acceptor':True,
       'frozen_g2_0039_scorer':True}
    ledger=[{'section':'causal_factor_separation',**row} for row in diagnostic]
    if winner['enabled']:
        assert_frozen()
        dev=dev_capture()  # Only permitted after TRAIN-fit and independent TRAIN-audit.
        summaries={}
        for split,examples in dev.items():
            summary,rows=measure(examples,winner['config'],nets,policy,allow_dev=True)
            summaries[split]=summary
            ledger.extend({'section':'development','split':split,'index':i,**row}
                          for i,row in enumerate(rows,1))
        report['by_split']=summaries
        independent=summaries['new_independent_dev_0040']
        topology=summaries['new_topology_dev_0040']
        replay=[v for k,v in summaries.items() if k not in
                ('new_independent_dev_0040','new_topology_dev_0040')]
        passed=(admissible(independent,require_gain=True) and
                admissible(topology,require_gain=False) and
                all(admissible(v,require_gain=False) for v in replay))
        report['development_gates']={'passed':passed,'new_exact':independent['new_exact'],
           'lost_exact':independent['lost_exact'],
           'topology_new_exact':topology['new_exact'],
           'topology_lost_exact':topology['lost_exact'],
           'topology_rank_delta':topology['rank_inversions']-topology['parent_rank_inversions'],
           'replay_lost_exact':sum(v['lost_exact'] for v in replay),
           'replay_rank_delta':sum(v['rank_inversions']-v['parent_rank_inversions'] for v in replay)}
        report['status']=('SCORE_BAKEOFF_DEV_GATES_PASSED_UNPROMOTED' if passed else
                          'SCORE_BAKEOFF_DEV_NOT_CERTIFIED')
    assert_frozen()
    return persist(output,report,ledger,nets,winner)
