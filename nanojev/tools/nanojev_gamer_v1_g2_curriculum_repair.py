"""G2-0037: TRAIN-only selective G1 occurrence recovery, frozen G2 comparison.

Every candidate and neural feature is computed without an author graph. Gold is
used only to label fresh TRAIN examples, calibrate on disjoint TRAIN rows, and
score frozen DEV predictions. The frozen G1 decoder and G2 checkpoints remain
unchanged. A selector may abstain; there is no DEV policy search or promotion.
"""
from __future__ import annotations

from collections import Counter
import gzip
import json
import random
from pathlib import Path

import torch
from safetensors import safe_open
from safetensors.torch import save_file

from tools import nanojev_gamer_v1_g2_selective_occurrence as x36
from tools import nanojev_gamer_v1_graph_corpus as corpus

x35=x36.x35
x34=x36.x34
x33=x36.x33
x32=x36.x32
x31=x36.x31
p30=x36.p30
base=x36.base
backbone=x36.backbone
general16=x36.general16

SCHEMA='nanojev.gamer.graph.g2.targeted-occurrence-curriculum.v1'
REPORT='g2_occurrence_curriculum_report.json'
LEDGER='g2_occurrence_curriculum_per_case.jsonl.gz'
WEIGHTS='g2_occurrence_curriculum_development.safetensors'
POLICIES='g2_occurrence_curriculum_frozen_policies.json'
SEED=460537
SEEDS=x36.SEEDS
MAX_LENGTH=x36.MAX_LENGTH
TRAIN=x36.TRAIN
CAL_FIT=x36.CAL_FIT
CAL_AUDIT=x36.CAL_AUDIT
TRAIN_STEPS=x36.TRAIN_STEPS
FRESH=('new_independent_dev_0037','new_topology_dev_0037')
REPLAY=x36.FRESH
# The three pools are nonoverlapping by construction before frozen-G1 mining.
POOL_LIMITS=(1700,700,400)
# Fixed TRAIN-only positive-case quotas, not adapted to DEV results.
RICH_QUOTAS=(96,36,18)
MIN_USEFUL=(12,4,2)
NOVEL_POOL_SEED=902137


def sha_rows(rows):return x36.sha_rows(rows)


def verify_parent36(directory, *, ancestors, train_sha, train36, fit36, audit36,
                    old_dev, fresh36):
    """Verify G2-0036 lineage, full ledger, hashes, and published aggregates."""
    d=Path(directory); rp=d/x36.REPORT; lp=d/x36.LEDGER
    report=json.loads(rp.read_text(encoding='utf-8'))
    cohort={**old_dev,**fresh36}
    datasets={**{n:sha_rows(r) for n,r in (
        ('train_0036',train36),('train_fit_0036',fit36),
        ('train_audit_0036',audit36))},
        **{n:sha_rows(r) for n,r in cohort.items()}}
    contract={'schema':x36.SCHEMA,
        'status':'TRAIN_ONLY_SELECTIVE_G1_DEV_NOT_CERTIFIED',
        'parent_hashes':ancestors,'train_dataset_sha256':train_sha,
        'datasets_sha256':datasets,'train_cases':len(train36),
        'train_fit_cases':len(fit36),'train_audit_cases':len(audit36),
        'new_optimizer_steps':x36.TRAIN_STEPS*len(SEEDS),
        'frozen_backbone':True,'frozen_g1':True,
        'frozen_g2_0031_occurrence_graph':True,
        'frozen_g2_0032_completeness':True,
        'frozen_g2_0033_set_decoder':True,
        'dev_labels_used_for_training':False,
        'dev_labels_used_for_calibration':False,
        'sealed_test_used':False,'production_promotion':False,
        'g2_certified':False,'graph_execution_authorized':False,
        'parent_live_replay_passed':True,'no_dev_policy_selection':True}
    for k,v in contract.items():
        if report.get(k)!=v:
            raise ValueError('GAMER_G2_0037_PARENT_0036_CONTRACT:'+k)
    for name,key in ((x36.LEDGER,'ledger_sha256'),
                     (x36.WEIGHTS,'weights_sha256'),
                     (x36.POLICIES,'policies_sha256')):
        if backbone.sha256(d/name)!=report.get(key):
            raise ValueError('GAMER_G2_0037_PARENT_0036_FILE_HASH:'+name)
    policy=json.loads((d/x36.POLICIES).read_text(encoding='utf-8'))
    if (set(policy)!={f'acceptor_s{s}' for s in SEEDS} or
        any(report['train_policy_audit'][str(s)]['policy']!=policy[f'acceptor_s{s}']
            for s in SEEDS)):
        raise ValueError('GAMER_G2_0037_PARENT_0036_POLICY')
    with safe_open(d/x36.WEIGHTS,framework='pt',device='cpu') as handle:
        actual=set(handle.keys())
        expected={f'acceptor_s{seed}.{k}' for seed in SEEDS
                 for k in x36.OccurrenceAcceptor().state_dict()}
        reference={f'acceptor_s{seed}.{k}':v for seed in SEEDS
                   for k,v in x36.OccurrenceAcceptor().state_dict().items()}
        if actual!=expected or any(
                handle.get_tensor(k).shape!=reference[k].shape or
                not torch.isfinite(handle.get_tensor(k)).all() for k in actual):
            raise ValueError('GAMER_G2_0037_PARENT_0036_WEIGHTS')
    cases={split:[] for split in cohort}
    with gzip.open(lp,'rt',encoding='utf-8') as stream:
        for line in stream:
            item=json.loads(line);split=item.get('group')
            if split not in cases or len(cases[split])>=len(cohort[split]):
                raise ValueError('GAMER_G2_0037_PARENT_0036_ROW_COUNT')
            i=len(cases[split]);r=cohort[split][i]
            if (item.get('index')!=i+1 or item.get('family')!=r['family'] or
                    item.get('prompt_sha256')!=base._sha(r['prompt']) or
                    item.get('gold_total')!=len(base.gold_binding_edges(r))):
                raise ValueError('GAMER_G2_0037_PARENT_0036_ROW_IDENTITY')
            cases[split].append(item)
    if any(len(cases[k])!=len(cohort[k]) for k in cohort):
        raise ValueError('GAMER_G2_0037_PARENT_0036_ROW_COUNT')
    for split,items in cases.items():
        if (report['by_split'][split]!=p30.aggregate(items) or
                report['coverage_by_split'][split]!=x36.summarize_coverage(items)):
            raise ValueError('GAMER_G2_0037_PARENT_0036_AGGREGATES:'+split)
    for name,splits in (('replayed_0035_dev',x36.REPLAY),
                        ('new_0036_dev',x36.FRESH)):
        group=[r for split in splits for r in cases[split]]
        if (report['evaluation'][name]!=p30.aggregate(group) or
                report['coverage_by_cohort'][name]!=x36.summarize_coverage(group)):
            raise ValueError('GAMER_G2_0037_PARENT_0036_EVAL:'+name)
    fresh=[r for split in x36.FRESH for r in cases[split]]
    if (report['development_gates']!=x36.development_gates(
            p30.aggregate(fresh),p30.aggregate(cases[x36.FRESH[1]]),
            x36.summarize_coverage(fresh))):
        raise ValueError('GAMER_G2_0037_PARENT_0036_GATES')
    return cases,{'g2_0036_report_sha256':backbone.sha256(rp),
                  'g2_0036_ledger_sha256':backbone.sha256(lp),
                  'g2_0036_weights_sha256':backbone.sha256(d/x36.WEIGHTS),
                  'g2_0036_policies_sha256':backbone.sha256(d/x36.POLICIES)}


def _previous_rows(state,cal30,dev30,cal31,dev31,train32,fit32,audit32,dev32,
                   train33,fit33,audit33,dev33,dev34,dev35,train36,fit36,audit36,dev36):
    return [*state['training'],*state['history29'],
        *(r for batch in state['fresh29'].values() for r in batch),
        *cal30,*(r for batch in dev30.values() for r in batch),
        *cal31,*(r for batch in dev31.values() for r in batch),
        *train32,*fit32,*audit32,*(r for batch in dev32.values() for r in batch),
        *train33,*fit33,*audit33,*(r for batch in dev33.values() for r in batch),
        *(r for batch in dev34.values() for r in batch),
        *(r for batch in dev35.values() for r in batch),
        *train36,*fit36,*audit36,*(r for batch in dev36.values() for r in batch)]


def novel_train_pool(prior, *, seed=SEED, limits=POOL_LIMITS):
    """Only TRAIN lexicon plus a new disjoint TRAIN-only synthetic-name pool.

    Gold-bearing rows are generated BEFORE neural screening. Neither DEV text
    nor DEV labels are read to construct candidates or choose training cases.
    """
    if len(limits)!=3 or min(limits)<1:raise ValueError('GAMER_G2_0037_POOL_PLAN')
    preexisting=set().union(*(set(pool) for pool in corpus.NAME_POOLS.values()))
    names=tuple(n for n in corpus._synthetic_pool(NOVEL_POOL_SEED,4608)
                if n not in preexisting)
    if len(names)<4096:
        raise ValueError('GAMER_G2_0037_TRAIN_NAME_POOL_EXHAUSTED')
    rng=random.Random(seed);seen={base._sha(row['prompt']) for row in prior}
    result=[]
    sizes=[(2,2),(2,3),(3,2),(3,3),(3,4),(4,3)]
    for i in range(sum(limits)):
        family='encounter' if i%2==0 else 'allocation'
        for attempt in range(256):
            # Vary novelty. Some examples retain familiar TRAIN tokens as
            # hard negative controls; none introduces a DEV vocabulary entry.
            mix=(.25,.5,.75,1.)[(i//2)%4]
            nplayer,nopp=sizes[(i//2)%len(sizes)]
            n=nplayer+nopp if family=='encounter' else (2,3,4)[(i//2)%3]
            new_count=max(1,min(n,round(n*mix)))
            chosen=rng.sample(names,new_count)+rng.sample(
                corpus.NAME_POOLS['train'],n-new_count)
            rng.shuffle(chosen)
            if family=='encounter':
                actor,rival=rng.sample(corpus.ACTORS_TRAIN,2)
                row=corpus.encounter(rng,'train',400000+i+attempt,
                    player_override=chosen[:nplayer],
                    opponent_override=chosen[nplayer:],
                    actor_override=actor,rival_override=rival)
                row['structural_topology']=f'{nplayer}x{nopp}'
            else:
                row=corpus.allocation(rng,'train',400000+i+attempt,
                    names_override=chosen,
                    actor_override=rng.choice(corpus.ACTORS_TRAIN))
                row['structural_topology']='allocation'
            digest=base._sha(row['prompt'])
            if digest not in seen:break
        else:raise ValueError('GAMER_G2_0037_POOL_DUPLICATE_EXHAUSTED')
        seen.add(digest);result.append(row)
    chunks=[];start=0
    for limit in limits:
        chunks.append(result[start:start+limit]);start+=limit
    return chunks


def fresh_cohorts(state,cal30,dev30,cal31,dev31,train32,fit32,audit32,dev32,
                  train33,fit33,audit33,dev33,dev34,dev35,
                  train36,fit36,audit36,dev36):
    previous=_previous_rows(state,cal30,dev30,cal31,dev31,train32,fit32,audit32,
        dev32,train33,fit33,audit33,dev33,dev34,dev35,train36,fit36,audit36,dev36)
    prior=list({base._sha(r['prompt']):r for r in previous}.values())
    pool=novel_train_pool(prior)
    excluded=[*prior,*(r for part in pool for r in part)]
    fresh_independent=general16.independent_rows((excluded,),limit=320,seed=SEED+100)
    fresh_topology=general16.heldout_topology_rows((excluded,fresh_independent),
                                                   limit=128,seed=SEED+101)
    all_rows=[*excluded,*fresh_independent,*fresh_topology]
    if (len({base._sha(r['prompt']) for r in all_rows})!=len(all_rows) or
            any(r['split']!='train' or
                r['structural_topology'] in ('4x4','4x2','2x4')
                for part in pool for r in part) or
            any(r['split']!='dev' for r in (*fresh_independent,*fresh_topology)) or
            {r['structural_topology'] for r in fresh_topology}!=
                {'2x4','4x2','4x4'}):
        raise ValueError('GAMER_G2_0037_COHORT_LEAKAGE')
    return pool,dict(zip(FRESH,(fresh_independent,fresh_topology)))


def useful_new_endpoints(evidence,row):
    gold_edges=base.gold_binding_edges(row)
    current=set(evidence['baseline_anchors']);candidate=set(evidence['anchors'])
    endpoint_gold={a for left,right,_ in gold_edges for a in (left,right)}
    useful={a for left,right,_ in gold_edges for a,b in ((left,right),(right,left))
            if a in candidate and a not in current and b in current}
    return {'positive':len(candidate&endpoint_gold),
            'useful':len(useful),'false':len(candidate-endpoint_gold),
            'catalog_additional_gold':len((evidence['catalog_anchors']-current)&endpoint_gold)}


def mine_train_curriculum(pool,head,model,tokenizer,*,device,max_length,
                          sizes=(TRAIN,CAL_FIT,CAL_AUDIT),
                          rich_quotas=RICH_QUOTAS, min_useful=MIN_USEFUL,
                          freeze=None):
    """Screen TRAIN-only rows, select positive-rich + negative controls.

    Independent segments for train/fit/audit are fixed BEFORE using labels;
    selection is based exclusively on these segments' TRAIN gold annotations.
    """
    freeze=freeze or x36.freeze_case
    selected=[];audits=[]
    if not (len(pool)==len(sizes)==len(rich_quotas)==len(min_useful)==3):
        raise ValueError('GAMER_G2_0037_MINE_CONFIG')
    for cohort,rows,size,rich_goal,useful_goal in zip(
            ('train_0037','train_fit_0037','train_audit_0037'),
            pool,sizes,rich_quotas,min_useful):
        positives=[];negatives=[];screened=Counter();useful_cases=0
        for row in rows:
            evidence,hidden=freeze(row,head,model,tokenizer,
                device=device,max_length=max_length)
            del hidden
            stats=useful_new_endpoints(evidence,row)
            screened.update({'rows':1,'positive_proposals':stats['positive'],
                'false_proposals':stats['false'],
                'catalog_additional_gold':stats['catalog_additional_gold']})
            if stats['positive']:
                screened.update({'positive_rows':1})
                if stats['useful']:screened.update({'useful_rows':1})
                if len(positives)<rich_goal:
                    positives.append((evidence,row))
                    useful_cases+=int(stats['useful']>0)
                elif stats['useful'] and useful_cases<useful_goal:
                    # Replace one previously selected positive without an
                    # actionable restored edge; never change the disjoint
                    # TRAIN-only partition.
                    for j,(prior_e,prior_r) in enumerate(positives):
                        if not useful_new_endpoints(prior_e,prior_r)['useful']:
                            positives[j]=(evidence,row);useful_cases+=1
                            break
            elif len(negatives)<size-rich_goal:
                negatives.append((evidence,row))
            if len(positives)==rich_goal and len(negatives)==size-rich_goal and                     useful_cases>=useful_goal:
                break
        if len(positives)!=rich_goal or len(negatives)!=size-rich_goal or                 useful_cases<useful_goal:
            raise ValueError('GAMER_G2_0037_INSUFFICIENT_TRAIN_POSITIVES:'+
                cohort+':'+str(dict(screened))+':selected='+str(len(positives)))
        entries=positives+negatives
        random.Random(SEED+len(cohort)).shuffle(entries)
        chosen=[row for evidence,row in entries]
        proof={'selected_cases':len(chosen),'selected_rich_cases':len(positives),
            'selected_negative_control_cases':len(negatives),
            'selected_useful_cases':useful_cases,
            'selected_positive_proposals':sum(useful_new_endpoints(e,r)['positive']
                                              for e,r in entries),
            'selected_positive_families':dict(Counter(r['family'] for e,r in positives)),
            'screened':dict(screened),'max_screened':len(rows),
            'cohort_sha256':sha_rows(chosen),
            'training_examples_only':True}
        print(f'GAMER_G2_0037_MINE {cohort} '+
              f'screened={screened["rows"]}/{len(rows)} '
              f'rich={len(positives)}/{rich_goal} '+
              f'useful={useful_cases} negatives={len(negatives)}',flush=True)
        selected.append(entries);audits.append(proof)
    return selected,dict(zip(('train_0037','train_fit_0037','train_audit_0037'),audits))


def fit_policies(fit,audit,nets):
    """Disclose the FULL fixed TRAIN-only threshold grid, even on abstention."""
    policies={};summary={}
    for seed,net in nets.items():
        grid=[];candidates=[]
        for threshold in x36.THRESHOLDS:
            for budget in x36.BUDGETS:
                cfg={'enabled':True,'threshold':threshold,'max_extra':budget}
                stats=x36.calibration_metrics(fit,net,cfg)
                eligible=(stats['true_new_endpoints']>=3 and
                    stats['admission_precision']>=.75 and
                    stats['newly_reachable_gold_edges']>0)
                record={'threshold':threshold,'max_extra':budget,
                        'fit':stats,'fit_eligible':eligible}
                grid.append(record)
                if eligible:
                    candidates.append((stats['newly_reachable_gold_edges'],
                        -stats['false_new_endpoints'],-budget,threshold,cfg,stats))
        if candidates:
            chosen=max(candidates,key=lambda p:p[:4]);cfg=chosen[4];fit_stats=chosen[5]
            audit_stats=x36.calibration_metrics(audit,net,cfg)
            if (audit_stats['true_new_endpoints']<2 or
                    audit_stats['admission_precision']<.75 or
                    audit_stats['newly_reachable_gold_edges']<=0):
                cfg={**cfg,'enabled':False,'disabled_reason':'failed_train_audit'}
        else:
            cfg={'enabled':False,'threshold':.97,'max_extra':0,
                 'disabled_reason':'no_precision_safe_train_policy'}
            fit_stats=None;audit_stats=None
        policies[f'acceptor_s{seed}']=cfg
        summary[str(seed)]={'fit':fit_stats,'audit':audit_stats,
            'policy':cfg,'fit_grid':grid,
            'eligible_fit_policies':len(candidates),
            'audit_evaluated':audit_stats is not None}
        print(f'GAMER_G2_0037_SELECT {seed} enabled={cfg["enabled"]} '
              f'fit_eligible={len(candidates)} '
              f'fit_tp={fit_stats["true_new_endpoints"] if fit_stats else 0} '
              f'audit_tp={audit_stats["true_new_endpoints"] if audit_stats else "not-tested"}',
              flush=True)
    return policies,summary


def run(*,precision30_run,representation31_run,completeness32_run,structured33_run,
        diagnosis34_run,coverage35_run,selective36_run,output,device='cuda',max_length=MAX_LENGTH,**kwargs):
    if max_length!=MAX_LENGTH:
        raise ValueError('GAMER_G2_0037_FIXED_PROTOCOL')
    if Path(output).exists():raise FileExistsError('GAMER_G2_0037_IMMUTABLE_OUTPUT')
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
    _,hash34=x35.verify_parent34(diagnosis34_run,ancestors=ancestors34,
        train_sha=sha_rows(state['training']),old_dev=dev33,fresh34=dev34)
    dev35=x35.fresh_cohorts(state,cal30,dev30,cal31,dev31,train32,fit32,
        audit32,dev32,train33,fit33,audit33,dev33,dev34)
    ancestors35={**ancestors34,**hash34}
    parent35,hash35=x36.verify_parent35(coverage35_run,ancestors=ancestors35,
        train_sha=sha_rows(state['training']),old_dev=dev34,fresh35=dev35)
    (train36,fit36,audit36),dev36=x36.fresh_cohorts(state,cal30,dev30,cal31,
        dev31,train32,fit32,audit32,dev32,train33,fit33,audit33,dev33,dev34,dev35)
    ancestors36={**ancestors35,**hash35}
    parent36,hash36=verify_parent36(selective36_run,ancestors=ancestors36,
        train_sha=sha_rows(state['training']),train36=train36,fit36=fit36,
        audit36=audit36,old_dev=dev35,fresh36=dev36)
    pool,fresh=fresh_cohorts(state,cal30,dev30,cal31,dev31,train32,fit32,audit32,
        dev32,train33,fit33,audit33,dev33,dev34,dev35,
        train36,fit36,audit36,dev36)
    models31=x32.load_parent_models(tensors31,device='cpu')
    nets32=x33.load_parent32_models(tensors32,device='cpu')
    nets33=x34.load_parent33_models(tensors33,device='cpu')
    parent_policies={'calibrated_reference_0030':pol30['calibrated_reference'],
        'calibrated_reference_0031':pol31['calibrated_reference'],
        'predicate_argument_0030':pol30[x31.CHAMPION],
        **{k:pol31[k] for k in x32.SOURCE_NAMES}}
    mined,mining_audit=mine_train_curriculum(pool,state['head'],state['model'],
        state['tokenizer'],device=device,max_length=max_length)
    (train_cases,fit_cases,audit_cases)=mined
    train_rows=[r for e,r in train_cases]
    fit_rows=[r for e,r in fit_cases]
    audit_rows=[r for e,r in audit_cases]
    acceptors,train_audit=x36.train_acceptors(
        [(e,x36.label_train_case(e,row)) for e,row in train_cases])
    policy,cal_audit=fit_policies(fit_cases,audit_cases,acceptors)
    evaluated={}
    for split,rows in {**dev36,**fresh}.items():
        results=[]
        for index,row in enumerate(rows,1):
            evidence,(prepared,hidden,eligible)=x36.freeze_case(row,state['head'],
                state['model'],state['tokenizer'],device=device,max_length=max_length)
            results.append(x36.analyze_case(row,evidence,state,models31,parent_policies,
                pol32,nets32,nets33,pol33,acceptors,policy,prepared,hidden,eligible,
                split=split,index=index,
                parent35=(parent36[split][index-1] if split in dev36 else None)))
            if index==1 or index%20==0 or index==len(rows):
                print(f'GAMER_G2_0037_EVAL {split} {index}/{len(rows)}',flush=True)
        evaluated[split]=results
    x31.rel.validate_original_state(state['head'],state['head_before'])
    if any(not torch.equal(v,state['reference'].state_dict()[k].detach().cpu())
           for k,v in state['reference_before'].items()):
        raise ValueError('GAMER_G2_0037_FROZEN_REFERENCE_MUTATION')
    if any(p.requires_grad for p in state['model'].parameters()):
        raise ValueError('GAMER_G2_0037_FROZEN_BACKBONE_MUTATION')
    replay=[r for k in REPLAY for r in evaluated[k]]
    new=[r for k in FRESH for r in evaluated[k]]
    fresh_metrics=p30.aggregate(new);ood=p30.aggregate(evaluated[FRESH[1]])
    fresh_coverage=x36.summarize_coverage(new)
    report={'schema':SCHEMA,'status':'TRAIN_ONLY_CURRICULUM_REPAIR_DEV_NOT_CERTIFIED',
        'parent_hashes':{**ancestors36,**hash36},
        'train_dataset_sha256':sha_rows(state['training']),
        'datasets_sha256':{**{k:sha_rows(v) for k,v in
            {'train_0037':train_rows,'train_fit_0037':fit_rows,
             'train_audit_0037':audit_rows}.items()},
            **{k:sha_rows(v) for k,v in {**dev36,**fresh}.items()}},
        'train_cases':len(train_rows),'train_fit_cases':len(fit_rows),
        'train_audit_cases':len(audit_rows),
        'new_optimizer_steps':TRAIN_STEPS*len(SEEDS),
        'protocol':{'selector_budget':x36.MAX_EXTRA,'candidate_catalog_limit':x36.CATALOG_LIMIT,
            'train_pool_limits':list(POOL_LIMITS),'train_rich_case_quotas':list(RICH_QUOTAS),
            'train_min_useful_case_quotas':list(MIN_USEFUL),
            'novel_name_pool_seed':NOVEL_POOL_SEED,
            'fixed_train_policy_grid':{'thresholds':list(x36.THRESHOLDS),'budgets':list(x36.BUDGETS)},
            'seeds':list(SEEDS),'new_independent_dev':320,'new_topology_dev':128,
            'replayed_dev':448,'train_curriculum_mined':True},
        'frozen_backbone':True,'frozen_g1':True,
        'frozen_g2_0031_occurrence_graph':True,
        'frozen_g2_0032_completeness':True,'frozen_g2_0033_set_decoder':True,
        'dev_labels_used_for_training':False,'dev_labels_used_for_calibration':False,
        'sealed_test_used':False,'production_promotion':False,
        'g2_certified':False,'graph_execution_authorized':False,
        'parent_live_replay_passed':True,'no_dev_policy_selection':True,
        'train_audit':train_audit,'train_policy_audit':cal_audit,
        'train_curriculum_mining':mining_audit,
        'train_pool_sha256':sha_rows([r for part in pool for r in part]),
        'curriculum_name_source':'synthetic TRAIN-only names, disjoint from all split lexicons',
        'evaluation':{'new_0037_dev':fresh_metrics,
                      'replayed_0036_dev':p30.aggregate(replay)},
        'coverage_by_cohort':{'new_0037_dev':fresh_coverage,
                              'replayed_0036_dev':x36.summarize_coverage(replay)},
        'development_gates':x36.development_gates(fresh_metrics,ood,fresh_coverage),
        'by_split':{k:p30.aggregate(v) for k,v in evaluated.items()},
        'coverage_by_split':{k:x36.summarize_coverage(v) for k,v in evaluated.items()},
        'limitations':{'frozen_catalog_cannot_recover_absent_proposals':True,
            'selector_is_feature_only_not_context_retrained':True,
            'train_only_hard_positive_mining':True,
            'no_g2_discriminator_retraining':True,
            'g2_0037_not_production_certified':True}}
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    x32._write_ledger(output/LEDGER,[*replay,*new])
    report['ledger_sha256']=backbone.sha256(output/LEDGER)
    tensors={f'acceptor_s{seed}.{k}':v.detach().cpu().contiguous()
             for seed,net in acceptors.items() for k,v in net.state_dict().items()}
    save_file(tensors,str(output/WEIGHTS));report['weights_sha256']=backbone.sha256(output/WEIGHTS)
    (output/POLICIES).write_text(json.dumps(policy,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    report['policies_sha256']=backbone.sha256(output/POLICIES)
    (output/REPORT).write_text(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    return report
