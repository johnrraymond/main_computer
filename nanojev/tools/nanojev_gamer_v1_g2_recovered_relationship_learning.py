"""G2-0039: retrain occurrence-graph relationship scoring for recovered G1 spans.

TRAIN labels may shape the new relationship scorer and fit/audit a fixed blending
policy. G1, backbone, G2-0032, G2-0033, and the G2-0038 acceptor stay frozen.
No DEV labels participate in a training or model-selection decision.
"""
from __future__ import annotations

from collections import defaultdict
import copy
import gzip
import json
import math
import random
from pathlib import Path

import numpy as np
import torch
from torch.nn import functional as F
from safetensors import safe_open
from safetensors.torch import save_file

from tools import nanojev_gamer_v1_g2_contextual_occurrence as x38

x37=x38.x37
x36=x38.x36
x35=x38.x35
x34=x38.x34
x33=x38.x33
x32=x38.x32
x31=x38.x31
p30=x38.p30
base=x38.base
backbone=x38.backbone
general16=x38.general16
m29=x31.m29

SCHEMA='nanojev.gamer.graph.g2.recovered-relationship-learning.v1'
REPORT='g2_recovered_relationship_report.json'
LEDGER='g2_recovered_relationship_per_case.jsonl.gz'
WEIGHTS='g2_recovered_relationship_development.safetensors'
POLICIES='g2_recovered_relationship_frozen_policies.json'
SEED=460539
SEEDS=x38.SEEDS
REPLAY=x38.FRESH
FRESH=('new_independent_dev_0039','new_topology_dev_0039')
BLENDS=(0.0,.25,.5,.75,1.0)
TRAIN_STEPS=240
TRAIN_BATCH=2
TRAIN_LR=8e-5
TRAIN_FIT=60
TRAIN_AUDIT=40
MAX_LENGTH=x38.MAX_LENGTH


def sha_rows(rows):return x38.sha_rows(rows)


def verify_parent38(directory,*,ancestors,train_sha,pool38,dev38):
    """Verify saved 0038 lineage, weights, policies, case identity, and metrics."""
    root=Path(directory)
    rp=root/x38.REPORT
    report=json.loads(rp.read_text(encoding='utf-8'))
    checks={'schema':x38.SCHEMA,'status':'CONTEXTUAL_OCCURRENCE_DEV_NOT_CERTIFIED',
        'parent_hashes':ancestors,'train_dataset_sha256':train_sha,
        'train_pool_sha256':sha_rows([r for part in pool38 for r in part]),
        'development_evaluation_skipped':False,'production_promotion':False,
        'frozen_g1':True,'frozen_backbone':True,'no_dev_policy_selection':True,
        'dev_labels_used_for_training':False,'dev_labels_used_for_calibration':False,
        'sealed_test_used':False,'g2_certified':False,
        'train_cases':x37.TRAIN,'train_fit_cases':x37.CAL_FIT,
        'train_audit_cases':x37.CAL_AUDIT}
    for k,value in checks.items():
        if report.get(k)!=value:raise ValueError('GAMER_G2_0039_PARENT_0038_CONTRACT:'+k)
    for split,rows in dev38.items():
        if report['datasets_sha256'].get(split)!=sha_rows(rows):
            raise ValueError('GAMER_G2_0039_PARENT_0038_DATASET_DRIFT:'+split)
    for filename,key in ((x38.LEDGER,'ledger_sha256'),
                         (x38.WEIGHTS,'weights_sha256'),
                         (x38.POLICIES,'policies_sha256')):
        if backbone.sha256(root/filename)!=report.get(key):
            raise ValueError('GAMER_G2_0039_PARENT_0038_HASH:'+key)
    policies=json.loads((root/x38.POLICIES).read_text(encoding='utf-8'))
    if set(policies)!={f'{view}_s{seed}' for view in x38.VIEWS for seed in SEEDS}:
        raise ValueError('GAMER_G2_0039_PARENT_0038_POLICY_KEYS')
    for key,p in policies.items():
        if p!=report['train_policy_audit'][key]['policy']:
            raise ValueError('GAMER_G2_0039_PARENT_0038_POLICY_DRIFT:'+key)
    if not all(policies[f'contextual_s{seed}']['enabled'] for seed in SEEDS):
        raise ValueError('GAMER_G2_0039_PARENT_0038_CONTEXT_NOT_ENABLED')
    with safe_open(root/x38.WEIGHTS,framework='pt',device='cpu') as handle:
        names={f'{view}_s{seed}.{k}':v.shape for view in x38.VIEWS
               for seed in SEEDS
               for k,v in (x38.ContextualAcceptor() if view=='contextual'
                           else x36.OccurrenceAcceptor()).state_dict().items()}
        if set(handle.keys())!=set(names):
            raise ValueError('GAMER_G2_0039_PARENT_0038_WEIGHT_KEYS')
        if any(handle.get_tensor(k).shape!=shape or
               not bool(torch.isfinite(handle.get_tensor(k)).all())
               for k,shape in names.items()):
            raise ValueError('GAMER_G2_0039_PARENT_0038_NONFINITE_OR_SHAPE')
        nets={}
        for seed in SEEDS:
            prefix=f'contextual_s{seed}.'
            state={key:handle.get_tensor(prefix+key) for key in
                   x38.ContextualAcceptor().state_dict()}
            if any(state[key].shape!=names[prefix+key] or
                   not bool(torch.isfinite(state[key]).all()) for key in state):
                raise ValueError('GAMER_G2_0039_PARENT_0038_WEIGHT_SHAPE')
            net=x38.ContextualAcceptor()
            net.load_state_dict(state,strict=True)
            nets[seed]=net.eval().requires_grad_(False)
    entries={k:[] for k in dev38}
    all_groups=tuple(report['by_split'])
    with gzip.open(root/x38.LEDGER,'rt',encoding='utf-8') as stream:
        seen=[]
        for raw in stream:
            entry=json.loads(raw);split=entry.get('group')
            if split not in all_groups or split not in dev38:
                raise ValueError('GAMER_G2_0039_PARENT_0038_LEDGER_SPLIT')
            i=len(entries[split]);rows=dev38[split]
            if i>=len(rows) or any((entry.get('index')!=i+1,
                entry.get('prompt_sha256')!=base._sha(rows[i]['prompt']),
                entry.get('family')!=rows[i]['family'],
                entry.get('gold_total')!=len(base.gold_binding_edges(rows[i])))):
                raise ValueError('GAMER_G2_0039_PARENT_0038_CASE_IDENTITY')
            entries[split].append(entry);seen.append(split)
    if len(seen)!=sum(map(len,dev38.values())) or any(
            len(entries[k])!=len(v) for k,v in dev38.items()):
        raise ValueError('GAMER_G2_0039_PARENT_0038_CASE_COUNT')
    for split,items in entries.items():
        if report['by_split'][split]!=p30.aggregate(items) or\
           report['coverage_by_split'][split]!=x38.summarize_coverage(items):
            raise ValueError('GAMER_G2_0039_PARENT_0038_AGGREGATE:'+split)
    for label,splits in (('replayed_0037_dev',x38.REPLAY),('new_0038_dev',x38.FRESH)):
        merged=[r for s in splits for r in entries[s]]
        if report['evaluation'][label]!=p30.aggregate(merged) or\
           report['coverage_by_cohort'][label]!=x38.summarize_coverage(merged):
            raise ValueError('GAMER_G2_0039_PARENT_0038_COHORT:'+label)
    hashes={'g2_0038_report_sha256':backbone.sha256(rp),
        'g2_0038_ledger_sha256':backbone.sha256(root/x38.LEDGER),
        'g2_0038_weights_sha256':backbone.sha256(root/x38.WEIGHTS),
        'g2_0038_policies_sha256':backbone.sha256(root/x38.POLICIES)}
    return entries,policies,nets,hashes


def fresh_cohorts(prior,pool37,dev37,pool38,dev38):
    original=list({base._sha(r['prompt']):r for r in prior}.values())
    earlier=[*original,*(r for batch in pool37 for r in batch),
             *(r for batch in dev37.values() for r in batch),
             *(r for batch in pool38 for r in batch),
             *(r for batch in dev38.values() for r in batch)]
    if len({base._sha(r['prompt']) for r in earlier})!=len(earlier):
        raise ValueError('GAMER_G2_0039_PARENT_COHORT_OVERLAP')
    pool=x37.novel_train_pool(earlier,seed=SEED)
    withheld=[*earlier,*(r for batch in pool for r in batch)]
    independent=general16.independent_rows((withheld,),limit=320,seed=SEED+100)
    topology=general16.heldout_topology_rows((withheld,independent),limit=128,
                                              seed=SEED+101)
    combined=[*withheld,*independent,*topology]
    if len({base._sha(r['prompt']) for r in combined})!=len(combined) or\
       any(r['split']!='train' for batch in pool for r in batch) or\
       any(r['split']!='dev' for r in (*independent,*topology)) or\
       {r['structural_topology'] for r in topology}!={'4x4','4x2','2x4'}:
        raise ValueError('GAMER_G2_0039_COHORT_LEAKAGE')
    return pool,dict(zip(FRESH,(independent,topology)))


def chosen_spans(evidence,nets,policies,seed):
    return x38.choose_spans(evidence,nets[seed],policies[f'contextual_s{seed}'],
                            'contextual')


def frozen_train_example(row,spans,prepared,hidden,eligible,state,*,train=True,case_index=1):
    """TRAIN gold enters only the supervised example, not the frozen G1/G2 head."""
    case=x36.frozen_g2_case(row,spans,prepared,hidden,eligible,state['head'])
    case['index']=case_index
    case['gold']=base.gold_binding_edges(row) if train else set()
    return x31.example_from_case(case,row,state,train=train,device=hidden.device,
                                  top_k=24)


def mine_relation_training(pool,state,acceptors,policies,*,device,max_length):
    """Mining uses TRAIN supervision; selector admissions are always label blind."""
    triples,diag=x37.mine_train_curriculum(pool,state['head'],state['model'],
        state['tokenizer'],device=device,max_length=max_length,
        freeze=x38.freeze_context_case)
    out=[];counts={}
    for name,cases in zip(('train_0039','train_fit_0039','train_audit_0039'),triples):
        records=[];statistics=defaultdict(int)
        for i,(evidence,row) in enumerate(cases,1):
            # Recompute frozen token states for the chosen TRAIN sample only.
            current,(prepared,hidden,eligible)=x38.freeze_context_case(
                row,state['head'],state['model'],state['tokenizer'],
                device=device,max_length=max_length)
            if current['baseline_anchors']!=evidence['baseline_anchors'] or\
               current['anchors']!=evidence['anchors']:
                raise ValueError('GAMER_G2_0039_TRAIN_CAPTURE_DRIFT')
            original=frozen_train_example(row,current['baseline_spans'],prepared,
                hidden,eligible,state,case_index=i)
            original['original_anchors']=tuple(current['baseline_anchors'])
            records.append(('original',row,original))
            seen_expansions={tuple(current['baseline_anchors'])}
            for seed in SEEDS:
                spans=chosen_spans(current,acceptors,policies,seed)
                if spans==current['baseline_spans']:
                    statistics['selector_abstentions']+=1
                    continue
                anchor_key=tuple(base.predicted_anchors(spans,prepared))
                if anchor_key in seen_expansions:
                    statistics['duplicate_expansions_skipped']+=1
                    continue
                seen_expansions.add(anchor_key)
                ex=frozen_train_example(row,spans,prepared,hidden,eligible,state,case_index=i)
                ex['original_anchors']=tuple(current['baseline_anchors'])
                records.append((f'recovered_s{seed}',row,ex))
                original_set=set(current['baseline_anchors'])
                selected=set(base.predicted_anchors(spans,prepared))
                gold_end=x36.positive_endpoints(row)
                statistics['admitted_gold_endpoints']+=len((selected-original_set)&gold_end)
                statistics['admitted_false_endpoints']+=len((selected-original_set)-gold_end)
                statistics['recovered_examples']+=1
            if i==1 or i%40==0 or i==len(cases):
                print(f'GAMER_G2_0039_CAPTURE {name} {i}/{len(cases)}',flush=True)
        if not records or not statistics['recovered_examples']:
            raise ValueError('GAMER_G2_0039_NO_EXPANDED_TRAIN_EXAMPLES:'+name)
        hashes=sha_rows([row for _,row,_ in records])
        counts[name]={**statistics,'graphs':len(cases),'examples':len(records),
                      'records_sha256':hashes,'gold_edges':sum(int(ex['labels'].sum())
                                                             for _,_,ex in records)}
        out.append(records)
    return out,counts,diag


def hard_negative_loss(scores,ex,original_anchors):
    """Penalize false candidate relations touching newly admitted occurrences."""
    labels=ex['labels'].to(scores.device)
    edge_keys=ex['edges']
    if len(edge_keys)!=len(scores):
        raise ValueError('GAMER_G2_0039_RELATION_ALIGNMENT')
    targets=[i for i,e in enumerate(edge_keys)
             if labels[i]<.5 and (e[0] not in original_anchors or e[1] not in original_anchors)]
    if not targets:return scores.sum()*0
    neg=scores[torch.tensor(targets,dtype=torch.long,device=scores.device)]
    return F.softplus(neg-m29.LOGIT_CUTOFF).mean()


def train_relationships(cases,parent,*,steps=TRAIN_STEPS,batch_size=TRAIN_BATCH):
    """Fine-tune three original G2 occurrence-graph scorers, not a new classifier."""
    if steps<1 or steps>TRAIN_STEPS or not cases:raise ValueError('GAMER_G2_0039_TRAIN_PROTOCOL')
    mixed=[];original=[];recovered=[]
    for arm,row,ex in cases:
        if not len(ex['labels']) or not any(float(v)>0 for v in ex['labels']):continue
        groups=defaultdict(list)
        for i,g in enumerate(ex['group'].tolist()):groups[g].append(i)
        if not any(any(ex['labels'][i]>.5 for i in ids) and
                   any(ex['labels'][i]<.5 for i in ids) for ids in groups.values()):continue
        (original if arm=='original' else recovered).append((arm,row,ex))
    if len(original)<8 or len(recovered)<8:
        raise ValueError('GAMER_G2_0039_INSUFFICIENT_HARD_NEGATIVE_EXAMPLES')
    output={};loss_audit={}
    for seed in SEEDS:
        parent_model=parent[seed]['occurrence_graph']
        model=copy.deepcopy(parent_model).train().requires_grad_(True)
        frozen_before={k:v.detach().clone() for k,v in parent_model.state_dict().items()}
        optimizer=torch.optim.AdamW(model.parameters(),lr=TRAIN_LR,weight_decay=1e-4)
        rng=random.Random(seed+SEED)
        for step in range(1,steps+1):
            selected=[original[rng.randrange(len(original))],
                      recovered[rng.randrange(len(recovered))]]
            optimizer.zero_grad(set_to_none=True);batch_loss=[]
            for arm,row,ex in selected:
                data=x31.scorer_input(ex,'cpu')
                score=model(data)
                loss=m29.ranking_objective(score,
                    {'labels':ex['labels'],'group':ex['group']})
                if arm!='original':
                    # Original nodes are a subset of the expanded node graph.
                    originals=set(ex.get('original_anchors',()))
                    if not originals:
                        raise ValueError('GAMER_G2_0039_MISSING_ORIGINAL_ANCHORS')
                    loss=loss+.30*hard_negative_loss(score,ex,originals)
                batch_loss.append(loss)
            objective=torch.stack(batch_loss).mean()
            if not bool(torch.isfinite(objective)):
                raise ValueError('GAMER_G2_0039_NONFINITE_RELATION_LOSS')
            objective.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(),2.0)
            optimizer.step()
            if step==1 or step%60==0 or step==steps:
                print(f'GAMER_G2_0039_TRAIN {seed} {step}/{steps} '
                      f'loss={float(objective.detach()):.5f}',flush=True)
        if any(not torch.equal(parent_model.state_dict()[k],v) for k,v in frozen_before.items()):
            raise ValueError('GAMER_G2_0039_PARENT_G2_MUTATED')
        output[seed]=model.eval().requires_grad_(False)
        loss_audit[str(seed)]={'steps':steps,'original_hard_examples':len(original),
                               'expanded_hard_examples':len(recovered),
                               'last_loss':float(objective.detach()),
                               'trainable_parameters':sum(p.numel() for p in model.parameters())}
    return output,loss_audit


def score_pair(case,row,state,parent31,new_nets,*,device):
    """No gold in either score path; all decoders use the original 0033 contract."""
    ex=x31.example_from_case(case,row,state,train=False,device=device,top_k=24)
    parent_item=x31.score_case(ex,state,parent31)
    updated={name:np.asarray(value).copy() for name,value in parent_item['raw'].items()}
    for seed,net in new_nets.items():
        with torch.inference_mode():
            logits=net(x31.scorer_input(ex,'cpu')).detach().cpu().numpy().astype('float32')
        if len(logits)!=len(ex['edges']) or not np.isfinite(logits).all():
            raise ValueError('GAMER_G2_0039_BAD_RETRAINED_SCORES')
        updated[f'occurrence_graph_s{seed}']=logits
    return parent_item,{**parent_item,'raw':updated}


def interpolate(original,finetuned,alpha,seed=None):
    """Weights selected using TRAIN-fit/audit only; never DEV labels."""
    if alpha not in BLENDS:raise ValueError('GAMER_G2_0039_UNCALIBRATED_BLEND')
    raw={name:np.asarray(v).copy() for name,v in original['raw'].items()}
    if seed is not None and seed not in SEEDS:
        raise ValueError('GAMER_G2_0039_UNKNOWN_RELATIONSHIP_SEED')
    for selected in (SEEDS if seed is None else (seed,)):
        key=f'occurrence_graph_s{selected}'
        raw[key]=((1-alpha)*raw[key]+alpha*np.asarray(finetuned['raw'][key])).astype('float32')
    return {**original,'raw':raw}


def decode_scores(item,n_nodes,parent_policies,pol32,nets32,nets33,pol33):
    shortlisted=x35.shortlist_for_frozen_decoder(item)
    return (*x35._scores(shortlisted,'candidate',n_nodes,parent_policies,
                         pol32,nets32,nets33,pol33),shortlisted)


def measure_mode(item,pred,rank,mode,seed,gold):
    name=f'candidate__{mode}_s{seed}'
    labeled={**item,'gold':gold,'gold_total':len(gold),
             'candidate_gold':sum(e in gold for e in item['edges'])}
    competing,failures=p30.ranking_counts(labeled,rank[name])
    return {**p30.measure(pred[name],labeled),
            'competing_gold':competing,'ranking_failures':failures}


def calibration_cases(cases,state,parent31,new_nets,acceptors,policies,*,device):
    records=[]
    for arm,row,_ in cases:
        # G2 examples were retained with labels for TRAIN fitting. For policy
        # calibration rebuild the paired full *unlabeled* inventories from
        # the chosen frozen TRAIN inference spans.
        if arm!='original':continue
        evidence,(prepared,hidden,eligible)=x38.freeze_context_case(
            row,state['head'],state['model'],state['tokenizer'],
            device=device,max_length=MAX_LENGTH)
        original=x36.frozen_g2_case(row,evidence['baseline_spans'],prepared,
                                   hidden,eligible,state['head'])
        arms={'original':original}
        for seed in SEEDS:
            spans=chosen_spans(evidence,acceptors,policies,seed)
            arms[str(seed)]=(original if spans==evidence['baseline_spans'] else
                x36.frozen_g2_case(row,spans,prepared,hidden,eligible,state['head']))
        scores={name:score_pair(case,row,state,parent31,new_nets,device=device)
                for name,case in arms.items()}
        records.append((row,arms,scores))
    return records


def calibration_grid(records,policies,pol32,nets32,nets33,pol33):
    grid={}
    for seed in SEEDS:
        for alpha in BLENDS:
            sums={'f1_base':0.,'f1_new':0.,'exact_base':0,'exact_new':0,
                  'ranking_base':0,'ranking_new':0,'cases':0,'fp_base':0,'fp_new':0,
                  'tp_base':0,'tp_new':0,'fn_base':0,'fn_new':0}
            for row,arms,scores in records:
                original,retrained=scores[str(seed)]
                item=interpolate(original,retrained,alpha,seed=seed)
                parent_sel,parent_rank,parent_item=decode_scores(original,
                    len(arms[str(seed)]['anchors']),policies,pol32,nets32,nets33,pol33)
                selected,ranks,new_item=decode_scores(item,len(arms[str(seed)]['anchors']),
                    policies,pol32,nets32,nets33,pol33)
                gold=base.gold_binding_edges(row)  # TRAIN calibration only
                for mode in ('group','structured'):
                    baseline=measure_mode(parent_item,parent_sel,parent_rank,mode,seed,gold)
                    updated=measure_mode(new_item,selected,ranks,mode,seed,gold)
                    for suffix,metrics in (('base',baseline),('new',updated)):
                        sums['tp_'+suffix]+=metrics['correct']
                        sums['fn_'+suffix]+=metrics['missing']
                        sums['exact_'+suffix]+=metrics['exact_cases']
                        sums['ranking_'+suffix]+=metrics['ranking_failures']
                        sums['fp_'+suffix]+=metrics['false_positive']
                sums['cases']+=1
            for label in ('base','new'):
                sums['f1_'+label]=2*sums['tp_'+label]/max(1,
                    2*sums['tp_'+label]+sums['fp_'+label]+sums['fn_'+label])
            grid[f'{seed}:{alpha:.2f}']=sums
    return grid


def fit_policies(fit,audit,parent_policies,pol32,nets32,nets33,pol33):
    fit_grid=calibration_grid(fit,parent_policies,pol32,nets32,nets33,pol33)
    chosen={};diagnosis={}
    for seed in SEEDS:
        ranked=[]
        for alpha in BLENDS[1:]:
            metrics=fit_grid[f'{seed}:{alpha:.2f}']
            # Fit must improve graph F1 without sacrificing exact graphs.
            # Ranking/fp are recorded as additional constraints; do not
            # demand an exact gain from a small TRAIN fit where exact=0.
            eligible=(metrics['f1_new']>metrics['f1_base']+1e-6 and
                metrics['exact_new']>=metrics['exact_base'] and
                metrics['ranking_new']<=metrics['ranking_base'] and
                metrics['fp_new']<=metrics['fp_base'])
            if eligible:
                ranked.append((metrics['exact_new']-metrics['exact_base'],
                               metrics['f1_new']-metrics['f1_base'],
                               -metrics['fp_new'], -alpha, alpha))
        alpha=max(ranked)[-1] if ranked else 0.
        chosen[seed]={'enabled':bool(alpha),'blend':alpha,
                      **({} if alpha else {'disabled_reason':'no_train_fit_exact_graph_gain'})}
    audit_grid=calibration_grid(audit,parent_policies,pol32,nets32,nets33,pol33)
    for seed in SEEDS:
        alpha=chosen[seed]['blend']
        fit_stat=fit_grid[f'{seed}:{alpha:.2f}']
        audit_stat=audit_grid[f'{seed}:{alpha:.2f}']
        if alpha and not (audit_stat['exact_new']>=audit_stat['exact_base'] and
            audit_stat['f1_new']>=audit_stat['f1_base']-1e-9 and
            audit_stat['ranking_new']<=audit_stat['ranking_base'] and
            audit_stat['fp_new']<=audit_stat['fp_base']):
            chosen[seed]={'enabled':False,'blend':0.,
                          'disabled_reason':'failed_independent_train_audit'}
        diagnosis[str(seed)]={'fit':fit_stat,'audit':audit_stat,
            'fit_grid':{str(a):fit_grid[f'{seed}:{a:.2f}'] for a in BLENDS},
            'audit_grid':{str(a):audit_grid[f'{seed}:{a:.2f}'] for a in BLENDS},
            'policy':chosen[seed]}
        print(f'GAMER_G2_0039_SELECT {seed} enabled={chosen[seed]["enabled"]} '
              f'blend={chosen[seed]["blend"]}',flush=True)
    return chosen,diagnosis


def evaluate_row(row,evidence,bundle,state,parent31,new_nets,acceptors,acceptor_policies,
                 rel_policies,parent_policies,pol32,nets32,nets33,pol33,*,group,index,
                 parent38=None):
    """All span, score, and edge choices are complete before gold is read."""
    prepared,hidden,eligible=bundle
    original=x36.frozen_g2_case(row,evidence['baseline_spans'],prepared,
                                hidden,eligible,state['head'])
    cases={'baseline':original}
    for seed in SEEDS:
        spans=chosen_spans(evidence,acceptors,acceptor_policies,seed)
        cases[str(seed)]=(original if spans==evidence['baseline_spans'] else
            x36.frozen_g2_case(row,spans,prepared,hidden,eligible,state['head']))
    outcomes={}
    for name,case in cases.items():
        frozen,trained=score_pair(case,row,state,parent31,new_nets,device=hidden.device)
        if name=='baseline':
            selected,rank,item=decode_scores(frozen,len(case['anchors']),parent_policies,
                                             pol32,nets32,nets33,pol33)
            outcomes['baseline']=(case,item,selected,rank)
        else:
            selected,rank,item=decode_scores(frozen,len(case['anchors']),parent_policies,
                                             pol32,nets32,nets33,pol33)
            outcomes[f'contextual:{name}']=(case,item,selected,rank)
            alpha=rel_policies[int(name)]['blend']
            new_item=interpolate(frozen,trained,alpha,seed=int(name))
            newsel,newrank,newshort=decode_scores(new_item,len(case['anchors']),
                parent_policies,pol32,nets32,nets33,pol33)
            outcomes[f'retrained:{name}']=(case,newshort,newsel,newrank)
    gold=base.gold_binding_edges(row)
    endpoints={v for a,b,_ in gold for v in (a,b)}
    original_anchors=set(original['anchors'])
    metrics={};coverage={}
    for arm,(case,item,selected,ranks) in outcomes.items():
        allowed=SEEDS if arm=='baseline' else (int(arm.split(':')[1]),)
        for seed in allowed:
            for mode in ('group','structured'):
                named=f'{arm.split(":")[0]}__{mode}_s{seed}'
                metrics[named]=measure_mode(item,selected,ranks,mode,seed,gold)
        anchors=set(case['anchors']);labeled={**item,'gold':gold,
            'gold_total':len(gold),'candidate_gold':sum(e in gold for e in item['edges'])}
        ceiling=x34.candidate_ceiling(labeled,anchors)
        coverage[arm]={'new_gold_anchors':len((anchors-original_anchors)&endpoints),
            'new_false_anchors':len((anchors-original_anchors)-endpoints),
            'represented_gold_edges':ceiling['represented_gold'],
            'fully_representable':ceiling['fully_representable']}
    if parent38 is not None:
        identity={'group':group,'index':index,'prompt_sha256':base._sha(row['prompt']),
                  'family':row['family'],'gold_total':len(gold)}
        if any(parent38.get(k)!=v for k,v in identity.items()):
            raise ValueError('GAMER_G2_0039_PARENT_0038_CASE_DRIFT')
        for k,val in metrics.items():
            former=k.replace('contextual__','contextual__',1)
            if (k.startswith('baseline__') or k.startswith('contextual__')) and\
               parent38['metrics'].get(former)!=val:
                raise ValueError('GAMER_G2_0039_PARENT_0038_LIVE_REPLAY:'+group+':'+str(index)+':'+k)
    return {'group':group,'index':index,'prompt_sha256':base._sha(row['prompt']),
        'family':row['family'],'structural_topology':row.get('structural_topology','native'),
        'gold_total':len(gold),'candidate_gold':sum(e in gold for e in outcomes['baseline'][1]['edges']),
        'metrics':metrics,'coverage':coverage}


def summarize_coverage(rows):
    arms=('baseline',*(f'{kind}:{seed}' for kind in ('contextual','retrained') for seed in SEEDS))
    fields=('new_gold_anchors','new_false_anchors','represented_gold_edges','fully_representable')
    return {'cases':len(rows),'variants':{a:{f:sum(r['coverage'][a][f] for r in rows)
                                            for f in fields} for a in arms}}


def exact_transitions(rows):
    """Count individual graphs won/lost, not only the net exact delta."""
    result={}
    for seed in SEEDS:
        for mode in ('group','structured'):
            old=f'contextual__{mode}_s{seed}'
            new=f'retrained__{mode}_s{seed}'
            win=loss=retained=0
            for row in rows:
                before=bool(row['metrics'][old]['exact_cases'])
                after=bool(row['metrics'][new]['exact_cases'])
                win+=int(after and not before)
                loss+=int(before and not after)
                retained+=int(before and after)
            result[f'{mode}_s{seed}']={'newly_exact':win,'lost_exact':loss,
                                       'retained_exact':retained}
    return result


def development_gates(full,topology,coverage,policies,transitions=None):
    decisions={}
    transitions=transitions or {}
    for seed in SEEDS:
        a=f'contextual__';b='retrained__'
        checks={}
        for mode in ('group','structured'):
            old=full['models'][f'{a}{mode}_s{seed}']
            new=full['models'][f'{b}{mode}_s{seed}']
            ood_old=topology['models'][f'{a}{mode}_s{seed}']
            ood_new=topology['models'][f'{b}{mode}_s{seed}']
            swapped=transitions.get(f'{mode}_s{seed}',{})
            checks[mode]={'f1_delta':new['f1']-old['f1'],
                'exact_graph_delta':new['exact_cases']-old['exact_cases'],
                'ranking_failure_delta':new['ranking_failures']-old['ranking_failures'],
                'topology_f1_delta':ood_new['f1']-ood_old['f1'],
                'topology_exact_delta':ood_new['exact_cases']-ood_old['exact_cases'],
                'newly_exact':swapped.get('newly_exact'),
                'lost_exact':swapped.get('lost_exact'),
                'passed':(swapped.get('lost_exact',0)==0) and new['f1']>=old['f1'] and
                    new['exact_cases']>old['exact_cases'] and
                    new['ranking_failures']<=old['ranking_failures'] and
                    ood_new['f1']>=ood_old['f1'] and
                    ood_new['exact_cases']>=ood_old['exact_cases']}
        decisions[str(seed)]={'train_enabled':policies[seed]['enabled'],
            'blend':policies[seed]['blend'],'by_decoder':checks,
            'passed':policies[seed]['enabled'] and all(x['passed'] for x in checks.values())}
    return {'by_seed':decisions,'exact_graph_improved_without_regressions':any(
        d['passed'] for d in decisions.values()),'production_promotion':False,
        'sealed_test_used':False}


def persist(output,report,models,policies,rows):
    dest=Path(output)
    dest.mkdir(parents=True,exist_ok=False)
    x32._write_ledger(dest/LEDGER,rows)
    report['ledger_sha256']=backbone.sha256(dest/LEDGER)
    weights={f'fine_tuned_s{seed}.{key}':value.detach().cpu().contiguous()
             for seed,net in models.items() for key,value in net.state_dict().items()}
    save_file(weights,str(dest/WEIGHTS))
    report['weights_sha256']=backbone.sha256(dest/WEIGHTS)
    payload={f'relationship_s{seed}':policies[seed] for seed in SEEDS}
    (dest/POLICIES).write_text(json.dumps(payload,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    report['policies_sha256']=backbone.sha256(dest/POLICIES)
    (dest/REPORT).write_text(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n',
                             encoding='utf-8')
    return report


def assert_frozen(state,parent31,acceptors,parent_snap,acceptor_snap):
    x31.rel.validate_original_state(state['head'],state['head_before'])
    if any(not torch.equal(v,state['reference'].state_dict()[k].detach().cpu())
           for k,v in state['reference_before'].items()):
        raise ValueError('GAMER_G2_0039_FROZEN_REFERENCE_MUTATION')
    if any(p.requires_grad for p in state['model'].parameters()):
        raise ValueError('GAMER_G2_0039_FROZEN_BACKBONE_MUTATION')
    for seed in SEEDS:
        for key,t in acceptors[seed].state_dict().items():
            if not torch.equal(t,acceptor_snap[seed][key]):
                raise ValueError('GAMER_G2_0039_FROZEN_ACCEPTOR_MUTATION')
        for key,t in parent31[seed]['occurrence_graph'].state_dict().items():
            if not torch.equal(t,parent_snap[seed][key]):
                raise ValueError('GAMER_G2_0039_FROZEN_G2_PARENT_MUTATION')


def run(*,precision30_run,representation31_run,completeness32_run,structured33_run,
        diagnosis34_run,coverage35_run,selective36_run,curriculum37_run,context38_run,
        output,device='cuda',max_length=MAX_LENGTH,**kwargs):
    if max_length!=MAX_LENGTH:
        raise ValueError('GAMER_G2_0039_FIXED_PROTOCOL')
    if Path(output).exists():
        raise FileExistsError('GAMER_G2_0039_IMMUTABLE_OUTPUT')
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
    _,hash35=x36.verify_parent35(coverage35_run,ancestors=ancestors35,
        train_sha=sha_rows(state['training']),old_dev=dev34,fresh35=dev35)
    (train36,fit36,audit36),dev36=x36.fresh_cohorts(state,cal30,dev30,cal31,
        dev31,train32,fit32,audit32,dev32,train33,fit33,audit33,dev33,dev34,dev35)
    ancestors36={**ancestors35,**hash35}
    _,hash36=x37.verify_parent36(selective36_run,ancestors=ancestors36,
        train_sha=sha_rows(state['training']),train36=train36,fit36=fit36,
        audit36=audit36,old_dev=dev35,fresh36=dev36)
    pool37,dev37=x37.fresh_cohorts(state,cal30,dev30,cal31,dev31,train32,fit32,
        audit32,dev32,train33,fit33,audit33,dev33,dev34,dev35,
        train36,fit36,audit36,dev36)
    ancestors37={**ancestors36,**hash36}
    _,hash37=x38.verify_parent37(curriculum37_run,ancestors=ancestors37,
        train_sha=sha_rows(state['training']),fresh37=dev37,pool37=pool37)
    prior=x37._previous_rows(state,cal30,dev30,cal31,dev31,train32,fit32,audit32,
        dev32,train33,fit33,audit33,dev33,dev34,dev35,
        train36,fit36,audit36,dev36)
    pool38,dev38=x38.fresh_cohorts(prior,pool37,dev37)
    ancestors38={**ancestors37,**hash37}
    parent38,acceptor_policies,acceptors,hash38=verify_parent38(
        context38_run,ancestors=ancestors38,train_sha=sha_rows(state['training']),
        pool38=pool38,dev38={**dev37,**dev38})
    train_pool,fresh=fresh_cohorts(prior,pool37,dev37,pool38,dev38)
    parent31=x32.load_parent_models(tensors31,device='cpu')
    nets32=x33.load_parent32_models(tensors32,device='cpu')
    nets33=x34.load_parent33_models(tensors33,device='cpu')
    parent_policies={'calibrated_reference_0030':pol30['calibrated_reference'],
        'calibrated_reference_0031':pol31['calibrated_reference'],
        'predicate_argument_0030':pol30[x31.CHAMPION],
        **{k:pol31[k] for k in x32.SOURCE_NAMES}}
    upstream_snap={seed:{k:v.detach().clone()
        for k,v in parent31[seed]['occurrence_graph'].state_dict().items()} for seed in SEEDS}
    acceptor_snap={seed:{k:v.detach().clone() for k,v in acceptors[seed].state_dict().items()}
        for seed in SEEDS}
    (train,fit,audit),mining,screening=mine_relation_training(
        train_pool,state,acceptors,acceptor_policies,
        device=device,max_length=max_length)
    models,train_audit=train_relationships(train,parent31)
    fit_cases=calibration_cases([r for r in fit if r[0]=='original'][:TRAIN_FIT],state,parent31,models,acceptors,
        acceptor_policies,device=device)
    audit_cases=calibration_cases([r for r in audit if r[0]=='original'][:TRAIN_AUDIT],state,parent31,models,acceptors,
        acceptor_policies,device=device)
    policies,calibration=fit_policies(fit_cases,audit_cases,parent_policies,
                                     pol32,nets32,nets33,pol33)
    assert_frozen(state,parent31,acceptors,upstream_snap,acceptor_snap)
    training_only=not any(p['enabled'] for p in policies.values())
    report={'schema':SCHEMA,'status':('TRAIN_CALIBRATION_UNSAFE_NO_DEV_EVALUATION'
                if training_only else 'RETRAINED_RELATIONSHIP_DEV_NOT_CERTIFIED'),
        'parent_hashes':{**ancestors38,**hash38},
        'train_dataset_sha256':sha_rows(state['training']),
        'datasets_sha256':{**{label:sha_rows([r for _,r,_ in cases])
            for label,cases in zip(('train_0039','train_fit_0039','train_audit_0039'),
                                   (train,fit,audit))},
            **{k:sha_rows(v) for k,v in {**dev38,**fresh}.items()}},
        'train_pool_sha256':sha_rows([r for batch in train_pool for r in batch]),
        'protocol':{'seed':SEED,'seeds':list(SEEDS),'train_steps':TRAIN_STEPS,
            'train_lr':TRAIN_LR,'train_batch':TRAIN_BATCH,'blends':list(BLENDS),
            'train_fit_rows':TRAIN_FIT,'train_audit_rows':TRAIN_AUDIT,
            'original_and_contextual_recovery_train_inputs':True,
            'new_relationship_architecture':False,
            'fine_tuned_pretrained_occurrence_graph':True,
            'explicit_recovered_endpoint_hard_negative_loss':True,
            'G2_0038_admission_policies_frozen':True,
            'G2_0032_and_G2_0033_decoder_policies_frozen':True,
            'no_dev_policy_selection':True},
        'frozen_g1':True,'frozen_backbone':True,
        'frozen_g2_0031_parent':True,'frozen_g2_0032_completeness':True,
        'frozen_g2_0033_set_decoder':True,'frozen_g2_0038_acceptor':True,
        'dev_labels_used_for_training':False,'dev_labels_used_for_calibration':False,
        'sealed_test_used':False,'production_promotion':False,
        'g2_certified':False,'graph_execution_authorized':False,
        'parent_live_replay_passed':False,'no_dev_policy_selection':True,
        'train_relation_mining':mining,'train_curriculum_screening':screening,
        'train_optimization':train_audit,'train_policy_audit':calibration,
        'development_evaluation_skipped':training_only,
        'limitations':{'does_not_train_upstream_g1':True,
            'G2_0033_group_counts_remain_frozen':True,
            'no_sealed_test_or_promotion':True}}
    if training_only:
        report['development_gates']={'exact_graph_improved_without_regressions':False,
            'reason':'no_safe_train_calibrated_relationship_policy'}
        report['by_split']={};report['evaluation']={};report['coverage_by_split']={}
        report['coverage_by_cohort']={}
        return persist(output,report,models,policies,[])
    evaluated={}
    for split,rows in {**dev38,**fresh}.items():
        results=[]
        for index,row in enumerate(rows,1):
            evidence,bundle=x38.freeze_context_case(row,state['head'],state['model'],
                state['tokenizer'],device=device,max_length=max_length)
            results.append(evaluate_row(row,evidence,bundle,state,parent31,models,
                acceptors,acceptor_policies,policies,parent_policies,
                pol32,nets32,nets33,pol33,group=split,index=index,
                parent38=parent38[split][index-1] if split in dev38 else None))
            if index==1 or index%20==0 or index==len(rows):
                print(f'GAMER_G2_0039_EVAL {split} {index}/{len(rows)}',flush=True)
        evaluated[split]=results
    assert_frozen(state,parent31,acceptors,upstream_snap,acceptor_snap)
    old=[r for split in REPLAY for r in evaluated[split]]
    new=[r for split in FRESH for r in evaluated[split]]
    summary=p30.aggregate(new)
    top=p30.aggregate(evaluated[FRESH[1]])
    cov=summarize_coverage(new)
    report['by_split']={k:p30.aggregate(v) for k,v in evaluated.items()}
    report['evaluation']={'replayed_0038_dev':p30.aggregate(old),
                          'new_0039_dev':summary}
    report['coverage_by_split']={k:summarize_coverage(v) for k,v in evaluated.items()}
    report['coverage_by_cohort']={'replayed_0038_dev':summarize_coverage(old),
                                  'new_0039_dev':cov}
    report['parent_live_replay_passed']=True
    transitions=exact_transitions(new)
    report['exact_graph_transitions']=transitions
    report['development_gates']=development_gates(summary,top,cov,policies,transitions)
    return persist(output,report,models,policies,[*old,*new])
