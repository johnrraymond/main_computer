"""G2-0036: TRAIN-only selective G1 occurrence recovery, frozen G2 comparison.

Every candidate and neural feature is computed without an author graph. Gold is
used only to label fresh TRAIN examples, calibrate on disjoint TRAIN rows, and
score frozen DEV predictions. The frozen G1 decoder and G2 checkpoints remain
unchanged. A selector may abstain; there is no DEV policy search or promotion.
"""
from __future__ import annotations

from collections import Counter
import gzip
import json
import math
from pathlib import Path
import random

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from safetensors.torch import save_file

from tools import nanojev_gamer_v1_g2_candidate_coverage as x35
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS
from tools.nanojev_gamer_v1_graph_head import token_span_char_bounds

x34=x35.x34
x33=x35.x33
x32=x35.x32
x31=x35.x31
p30=x35.p30
base=x35.base
backbone=x35.backbone
general16=x35.general16

SCHEMA='nanojev.gamer.graph.g2.selective-occurrence.v1'
REPORT='g2_selective_occurrence_report.json'
LEDGER='g2_selective_occurrence_per_case.jsonl.gz'
WEIGHTS='g2_selective_occurrence_development.safetensors'
POLICIES='g2_selective_occurrence_frozen_policies.json'
SEED=460536
SEEDS=x35.SEEDS
MAX_LENGTH=x35.MAX_LENGTH
MAX_EXTRA=4
CATALOG_LIMIT=256
TRAIN=300
CAL_FIT=120
CAL_AUDIT=60
TRAIN_STEPS=320
FRESH=('new_independent_dev_0036','new_topology_dev_0036')
REPLAY=x35.NEW
KIND_COUNT=len(SPAN_KINDS)
FEATURE_DIM=17+KIND_COUNT
THRESHOLDS=(0.55,0.65,0.75,0.85,0.92,0.97)
BUDGETS=(1,2,4)


def sha_rows(rows):return x35.sha_rows(rows)


def verify_parent35(directory, *, ancestors, train_sha, old_dev, fresh35):
    """Check G2-0035's immutable experiment and all case/control aggregates."""
    d=Path(directory);rp=d/x35.REPORT;lp=d/x35.LEDGER
    report=json.loads(rp.read_text(encoding='utf-8'))
    cohort={**old_dev,**fresh35}
    expected={'schema':x35.SCHEMA,'status':'CANDIDATE_COVERAGE_DEV_NOT_CERTIFIED',
        'parent_hashes':ancestors,'train_dataset_sha256':train_sha,
        'datasets_sha256':{k:sha_rows(v) for k,v in cohort.items()},
        'no_training':True,'new_optimizer_steps':0,'frozen_backbone':True,
        'frozen_g1':True,'frozen_g2_0031_occurrence_graph':True,
        'frozen_g2_0032_completeness':True,'frozen_g2_0033_set_decoder':True,
        'all_candidate_proposals_label_blind':True,
        'dev_labels_used_for_training':False,'dev_labels_used_for_calibration':False,
        'sealed_test_used':False,'production_promotion':False,
        'g2_certified':False,'graph_execution_authorized':False,
        'parent_live_replay_passed':True}
    for k,v in expected.items():
        if report.get(k)!=v:
            raise ValueError('GAMER_G2_0036_PARENT_0035_CONTRACT:'+k)
    if report.get('ledger_sha256')!=backbone.sha256(lp):
        raise ValueError('GAMER_G2_0036_PARENT_0035_LEDGER_HASH')
    records={k:[] for k in cohort}
    with gzip.open(lp,'rt',encoding='utf-8') as stream:
        for line in stream:
            record=json.loads(line)
            split=record.get('group')
            if split not in cohort or len(records[split])>=len(cohort[split]):
                raise ValueError('GAMER_G2_0036_PARENT_0035_ROW_COUNT')
            original=cohort[split][len(records[split])]
            if (record.get('index')!=len(records[split])+1 or
                    record.get('prompt_sha256')!=base._sha(original['prompt']) or
                    record.get('family')!=original['family'] or
                    record.get('gold_total')!=len(base.gold_binding_edges(original))):
                raise ValueError('GAMER_G2_0036_PARENT_0035_ROW_IDENTITY')
            records[split].append(record)
    if any(len(records[k])!=len(v) for k,v in cohort.items()):
        raise ValueError('GAMER_G2_0036_PARENT_0035_ROW_COUNT')
    for split,entries in records.items():
        if (report['by_split'][split]!=p30.aggregate(entries) or
                report['coverage_by_split'][split]!=x35.summarize_coverage(entries)):
            raise ValueError('GAMER_G2_0036_PARENT_0035_AGGREGATE:'+split)
    for key,splits in (('new_0035_dev',x35.NEW),('replayed_0034_dev',x35.REPLAY)):
        collection=[e for s in splits for e in records[s]]
        if (report['evaluation'][key]!=p30.aggregate(collection) or
                report['coverage_by_cohort'][key]!=x35.summarize_coverage(collection)):
            raise ValueError('GAMER_G2_0036_PARENT_0035_EVALUATION:'+key)
    return records,{'g2_0035_report_sha256':backbone.sha256(rp),
                    'g2_0035_ledger_sha256':backbone.sha256(lp)}


def fresh_cohorts(state,cal30,dev30,cal31,dev31,train32,fit32,audit32,dev32,
                  train33,fit33,audit33,dev33,dev34,dev35):
    previous=[*state['training'],*state['history29'],
        *(r for batch in state['fresh29'].values() for r in batch),
        *cal30,*(r for batch in dev30.values() for r in batch),
        *cal31,*(r for batch in dev31.values() for r in batch),
        *train32,*fit32,*audit32,*(r for batch in dev32.values() for r in batch),
        *train33,*fit33,*audit33,*(r for batch in dev33.values() for r in batch),
        *(r for batch in dev34.values() for r in batch),
        *(r for batch in dev35.values() for r in batch)]
    prior=list({base._sha(r['prompt']):r for r in previous}.values())
    train=general16.curriculum_rows((prior,),limit=TRAIN+CAL_FIT+CAL_AUDIT,seed=SEED)
    random.Random(SEED+3).shuffle(train)
    excluded=[*prior,*train]
    fresh_independent=general16.independent_rows((excluded,),limit=320,seed=SEED+100)
    fresh_topology=general16.heldout_topology_rows((excluded,fresh_independent),
                                                   limit=128,seed=SEED+101)
    combined=[*excluded,*fresh_independent,*fresh_topology]
    if (len(train)!=TRAIN+CAL_FIT+CAL_AUDIT or
            len(fresh_independent)!=320 or len(fresh_topology)!=128 or
            len({base._sha(r['prompt']) for r in combined})!=len(combined) or
            any(r.get('split')!='train' for r in train) or
            any(r.get('split')!='dev' for r in (*fresh_independent,*fresh_topology)) or
            any(r.get('structural_topology') in {'2x4','4x2','4x4'} for r in train) or
            {r['structural_topology'] for r in fresh_topology}!={'2x4','4x2','4x4'}):
        raise ValueError('GAMER_G2_0036_COHORT_LEAKAGE')
    return ((train[:TRAIN],train[TRAIN:TRAIN+CAL_FIT],train[TRAIN+CAL_FIT:]),
            dict(zip(FRESH,(fresh_independent,fresh_topology))))


def _iou(a,b):
    common=max(0,min(a[1],b[1])-max(a[0],b[0]))
    return common/max(1,a[1]-a[0]+b[1]-b[0]-common)


def proposal_features(g1_output,prepared,baseline):
    """Inference-only catalog evidence. NO gold, prompt text, or graph labels."""
    stages=x35.g1diag.frozen_stages(g1_output,prepared)
    selected=base.predicted_anchors(baseline,prepared)
    if len(selected)!=len(baseline) or len(set(selected))!=len(selected):
        raise ValueError('GAMER_G2_0036_PARENT_ANCHOR_DRIFT')
    chosen=set(selected)
    catalog=stages['all_catalog'];clean=stages['filtered_catalog']
    max_token=max(1,max((key[1] for key in catalog),default=1))
    scored=[];covered=set(chosen)
    selected_tokens={ (s['token_first'],s['token_last'],s['kind'])
        for s in baseline }
    guard={(s['token_first'],s['token_last'],s['kind'])
        for s in stages['selected']['guard']}
    filtered={(s['token_first'],s['token_last'],s['kind'])
        for s in stages['selected']['filtered']}
    for (start,end,kind),margin in catalog.items():
        if kind not in SPAN_KINDS or not math.isfinite(margin) or margin<=0:continue
        span={'token_first':int(start),'token_last':int(end),'kind':kind}
        bounds=token_span_char_bounds(span,prepared)
        if bounds is None:continue
        anchor=(*bounds,kind)
        covered.add(anchor)
        if anchor in chosen:continue
        overlaps=[_iou(anchor,a) for a in selected]
        same=[_iou(anchor,a) for a in selected if a[2]==kind]
        width=end-start+1
        score=float(margin)*width**0.25
        features=[math.log1p(max(0.,margin)),min(margin,10.)/10.,
            math.log1p(width)/4.,min(width,12)/12.,
            start/max_token,end/max_token,
            float((start,end,kind) in clean),
            float((start,end,kind) in guard),
            float((start,end,kind) in filtered),
            max(overlaps,default=0.),max(same,default=0.),
            min(sum(x>0 for x in overlaps),12)/12.,
            min(sum(x>0 for x in same),12)/12.,
            min(len(selected),128)/128.,
            math.log1p(len(catalog))/8.,
            min(score,10.)/10.,
            float((start,end,kind) in selected_tokens)]
        features.extend(float(kind==k) for k in SPAN_KINDS)
        scored.append((score,span,anchor,features))
    # Deterministic candidate cap prevents runaway inference and keeps labels
    # out of proposal shortlist selection. Retain every proposal separately in
    # the catalog ceiling, even those excluded by the safety budget.
    scored.sort(key=lambda x:(-x[0],x[1]['token_first'],x[1]['token_last'],x[1]['kind']))
    scored=scored[:CATALOG_LIMIT]
    for _,span,anchor,feats in scored:
        if len(feats)!=FEATURE_DIM:raise ValueError('GAMER_G2_0036_FEATURE_WIDTH')
    return {'baseline_spans':list(baseline),'baseline_anchors':selected,
        'catalog_anchors':covered,
        'spans':[s for _,s,_,_ in scored],
        'anchors':[a for _,_,a,_ in scored],
        'features':torch.tensor([f for *_,f in scored],dtype=torch.float32).reshape(-1,FEATURE_DIM)}


def positive_endpoints(row):
    return {end for src,dst,_ in base.gold_binding_edges(row) for end in (src,dst)}


def label_train_case(evidence,row):
    gold=positive_endpoints(row)
    return torch.tensor([float(anchor in gold) for anchor in evidence['anchors']],
                        dtype=torch.float32)


class OccurrenceAcceptor(nn.Module):
    def __init__(self):
        super().__init__()
        self.layers=nn.Sequential(nn.LayerNorm(FEATURE_DIM),
            nn.Linear(FEATURE_DIM,64),nn.GELU(),nn.Linear(64,32),nn.GELU(),
            nn.Linear(32,1))
    def forward(self,features):
        if features.ndim!=2 or features.shape[1]!=FEATURE_DIM:
            raise ValueError('GAMER_G2_0036_FEATURE_SHAPE')
        return self.layers(features).squeeze(-1)


def train_acceptors(cases,*,steps=TRAIN_STEPS):
    """TRAIN examples only. Frozen checkpoint parents never enter optimizer."""
    pairs=[(e['features'],y) for e,y in cases if len(y)]
    if not pairs:
        raise ValueError('GAMER_G2_0036_EMPTY_TRAIN_CATALOG')
    features=torch.cat([p[0] for p in pairs]);labels=torch.cat([p[1] for p in pairs])
    positives=int(labels.sum().item());negatives=len(labels)-positives
    if positives==0 or negatives==0:
        raise ValueError('GAMER_G2_0036_ONE_CLASS_TRAIN_CATALOG')
    nets={};audit={}
    for seed in SEEDS:
        torch.manual_seed(seed)
        net=OccurrenceAcceptor().train()
        optimizer=torch.optim.AdamW(net.parameters(),lr=0.002,weight_decay=0.01)
        rng=torch.Generator().manual_seed(seed)
        positive_ids=torch.nonzero(labels>0.5).flatten()
        negative_ids=torch.nonzero(labels<0.5).flatten()
        for step in range(steps):
            # Balanced mini-batches prevent the sparse positive class from
            # disappearing; final admission remains TRAIN-calibrated.
            pos=positive_ids[torch.randint(len(positive_ids),(48,),generator=rng)]
            neg=negative_ids[torch.randint(len(negative_ids),(48,),generator=rng)]
            ids=torch.cat((pos,neg))
            logits=net(features[ids]);loss=F.binary_cross_entropy_with_logits(logits,labels[ids])
            optimizer.zero_grad(set_to_none=True);loss.backward();optimizer.step()
            if step==0 or (step+1)%80==0:
                print(f'GAMER_G2_0036_TRAIN {seed} {step+1}/{steps} '
                      f'loss={loss.detach().item():.5f}',flush=True)
        nets[seed]=net.eval().requires_grad_(False)
        audit[str(seed)]={'train_examples':int(len(labels)),
            'train_positive':positives,'train_negative':negatives,
            'optimizer_steps':steps,'last_train_loss':float(loss.detach())}
    return nets,audit


def choose_spans(evidence,net,policy):
    """Label-blind conservative admission; base G1 selections are immutable."""
    baseline=evidence['baseline_spans']
    if not policy.get('enabled',False) or not len(evidence['spans']):return list(baseline)
    with torch.inference_mode():
        prob=torch.sigmoid(net(evidence['features'])).detach().cpu().tolist()
    indices=[i for i,p in enumerate(prob) if p>=policy['threshold']]
    indices.sort(key=lambda i:(-prob[i],evidence['anchors'][i]))
    chosen=list(baseline);seen=set(evidence['baseline_anchors'])
    for i in indices:
        anchor=evidence['anchors'][i]
        if anchor in seen:continue
        if len(chosen)>=128 or len(chosen)-len(baseline)>=policy['max_extra']:break
        chosen.append(evidence['spans'][i]);seen.add(anchor)
    chosen.sort(key=lambda s:(s['token_first'],s['token_last'],s['kind']))
    if len(chosen)!=len(set((s['token_first'],s['token_last'],s['kind']) for s in chosen)):
        raise ValueError('GAMER_G2_0036_DUPLICATE_ADMISSION')
    return chosen


def calibration_metrics(cases,net,cfg):
    tp=fp=added=0;new_gold_edges=0
    for evidence,row in cases:
        chosen=choose_spans(evidence,net,cfg)
        anchors=set(evidence['baseline_anchors'])
        gold_edges=base.gold_binding_edges(row)
        added_set=set()
        # All admitted token spans are resolved through the same canonical
        # token-to-character mapping used when constructing their evidence.
        chosen_tokens={(s['token_first'],s['token_last'],s['kind']) for s in chosen}
        for span,anchor in zip(evidence['spans'],evidence['anchors']):
            if (span['token_first'],span['token_last'],span['kind']) in chosen_tokens:
                added_set.add(anchor)
        added_set.difference_update(anchors)
        gold={a for src,dst,_ in gold_edges for a in (src,dst)}
        tp+=len(added_set&gold);fp+=len(added_set-gold)
        added+=len(added_set)
        new_gold_edges+=sum(a in anchors|added_set and b in anchors|added_set and
                            not(a in anchors and b in anchors) for a,b,_ in gold_edges)
    return {'true_new_endpoints':tp,'false_new_endpoints':fp,
            'added_endpoints':added,'newly_reachable_gold_edges':new_gold_edges,
            'admission_precision':tp/max(1,tp+fp)}


def fit_policies(fit,audit,nets):
    """Fit on TRAIN, confirm on disjoint TRAIN audit; never inspect DEV labels."""
    result={};records={}
    for seed,net in nets.items():
        eligible=[]
        for threshold in THRESHOLDS:
            for max_extra in BUDGETS:
                cfg={'enabled':True,'threshold':threshold,'max_extra':max_extra}
                measured=calibration_metrics(fit,net,cfg)
                if (measured['true_new_endpoints']>=3 and
                        measured['admission_precision']>=0.75 and
                        measured['newly_reachable_gold_edges']>0):
                    eligible.append((measured['newly_reachable_gold_edges'],
                        -measured['false_new_endpoints'],
                        -max_extra,threshold,cfg,measured))
        if eligible:
            chosen=max(eligible,key=lambda x:x[:4]);cfg=chosen[4]
            fit_stats=chosen[5];audit_stats=calibration_metrics(audit,net,cfg)
            if (audit_stats['true_new_endpoints']<2 or
                    audit_stats['admission_precision']<0.75 or
                    audit_stats['newly_reachable_gold_edges']<=0):
                cfg={**cfg,'enabled':False,'disabled_reason':'failed_train_audit'}
        else:
            cfg={'enabled':False,'threshold':0.97,'max_extra':0,
                 'disabled_reason':'no_precision_safe_train_policy'}
            fit_stats=calibration_metrics(fit,net,cfg)
            audit_stats=calibration_metrics(audit,net,cfg)
        result[f'acceptor_s{seed}']=cfg
        records[str(seed)]={'fit':fit_stats,'audit':audit_stats,'policy':cfg}
        print(f'GAMER_G2_0036_SELECT {seed} enabled={cfg["enabled"]} '
              f'fit_tp={fit_stats["true_new_endpoints"]} '
              f'audit_precision={audit_stats["admission_precision"]:.3f}',flush=True)
    return result,records


def freeze_case(row,head,model,tok,*,device,max_length):
    if any(p.requires_grad for p in (*model.parameters(),*head.parameters())):
        raise ValueError('GAMER_G2_0036_UPSTREAM_NOT_FROZEN')
    with torch.inference_mode():
        prepared=backbone.prepare_prompt(tok,row['prompt'],max_input_tokens=max_length)
        hidden=backbone.frozen_context(model,prepared,device=device)
        eligible=prepared['eligible'].to(device)
        pred=head(hidden,eligible=eligible,active_stage='G1')
        baseline=base.predicted_spans(pred,prepared)
        evidence=proposal_features(pred,prepared,baseline)
    return evidence,(prepared,hidden,eligible)


def frozen_g2_case(row,spans,prepared,hidden,eligible,head):
    with torch.inference_mode():
        anchors=base.predicted_anchors(spans,prepared)
        if not 1<=len(anchors)<=128 or len(set(anchors))!=len(anchors):
            raise ValueError('GAMER_G2_0036_BAD_SELECTED_ANCHORS')
        pred=head(hidden,eligible=eligible,training_spans=spans,active_stage='G2')
        vectors=pred['node_vectors'][:len(anchors)].detach().cpu().contiguous()
        windows,mask=x31.occurrence_windows(hidden,prepared,anchors)
    return {'index':1,'family':row['family'],
        'prompt_sha256':base._sha(row['prompt']),'anchors':anchors,
        'gold':set(),'vectors':vectors,
        'windows':windows,'window_mask':mask}


def score_decoders(case,row,state,models31,parent_policies,pol32,nets32,nets33,pol33,*,device):
    with torch.inference_mode():
        example=x31.example_from_case(case,row,state,device=device,top_k=24)
        item=x31.score_case(example,state,models31)
        item=x35.shortlist_for_frozen_decoder(item)
        selected,rank=x35._scores(item,'temporary',len(case['anchors']),
                                  parent_policies,pol32,nets32,nets33,pol33)
    # No metrics are computed here: item['gold'] is empty and remains so
    # until all decisions have been returned by the frozen networks.
    return ({k.removeprefix('temporary__'):v for k,v in selected.items()},
            {k.removeprefix('temporary__'):v for k,v in rank.items()},item)


def analyze_case(row,evidence,state,models31,parent_policies,pol32,nets32,nets33,pol33,
                 acceptors,policies,prepared,hidden,eligible,*,split,index,parent35=None):
    """Make ALL decisions before reading any gold or parent development metrics."""
    baseline=frozen_g2_case(row,evidence['baseline_spans'],prepared,hidden,eligible,state['head'])
    base_decisions,base_ranks,base_item=score_decoders(baseline,row,state,models31,parent_policies,
        pol32,nets32,nets33,pol33,device=hidden.device)
    frozen={'baseline':(baseline,base_item,base_decisions,base_ranks)}
    for seed,net in acceptors.items():
        spans=choose_spans(evidence,net,policies[f'acceptor_s{seed}'])
        if spans==evidence['baseline_spans']:
            this=baseline;item=base_item;decisions=base_decisions;ranks=base_ranks
        else:
            this=frozen_g2_case(row,spans,prepared,hidden,eligible,state['head'])
            decisions,ranks,item=score_decoders(this,row,state,models31,parent_policies,
                pol32,nets32,nets33,pol33,device=hidden.device)
        frozen[str(seed)]=(this,item,decisions,ranks)
    # All predictions are now frozen. Gold first becomes available at this point.
    gold=base.gold_binding_edges(row)
    gold_endpoints={a for src,dst,_ in gold for a in (src,dst)}
    original=set(evidence['baseline_anchors']);catalog=evidence['catalog_anchors']
    actionable=original|set(evidence['anchors'])
    coverage={'catalog_gold_endpoint_count':len(catalog&gold_endpoints),
        'actionable_catalog_gold_endpoint_count':len(actionable&gold_endpoints),
        'lost_gold_endpoints_to_catalog_cap':len((catalog-actionable)&gold_endpoints),
        'gold_endpoint_count':len(gold_endpoints),
        'baseline_gold_endpoint_count':len(original&gold_endpoints),
        'unrecoverable_gold_endpoints':len(gold_endpoints-catalog),
        'catalog_candidate_count':len(evidence['anchors']),
        'catalog_limit':CATALOG_LIMIT}
    all_metrics={}
    for arm,(case,item,decisions,ranks) in frozen.items():
        evaluated={**item,'gold':gold,'gold_total':len(gold),
            'candidate_gold':sum(edge in gold for edge in item['edges'])}
        if arm=='baseline':
            model_names=[f'{mode}_s{seed}' for seed in SEEDS for mode in ('group','structured')]
        else:
            model_names=[f'{mode}_s{arm}' for mode in ('group','structured')]
        for name in model_names:
            competing,ranking=p30.ranking_counts(evaluated,ranks[name])
            all_metrics[f'{"baseline" if arm=="baseline" else "selective"}__{name}']={
                **p30.measure(decisions[name],evaluated),
                'competing_gold':competing,'ranking_failures':ranking}
        anchors=set(case['anchors'])
        ceiling=x34.candidate_ceiling(evaluated,anchors)
        ceiling.pop('candidate_oracle')
        coverage[arm]={'selected_anchors':len(anchors),
            'new_gold_anchors':len((anchors-original)&gold_endpoints),
            'new_false_anchors':len((anchors-original)-gold_endpoints),
            'gold_edges_reachable_by_endpoints':sum(a in anchors and b in anchors
                for a,b,_ in gold),
            'represented_gold_edges':ceiling['represented_gold'],
            'fully_representable':ceiling['fully_representable']}
    if parent35 is not None:
        for key in ('group','index','prompt_sha256','family','gold_total'):
            expected={'group':split,'index':index,'prompt_sha256':base._sha(row['prompt']),
                      'family':row['family'],'gold_total':len(gold)}[key]
            if parent35.get(key)!=expected:
                raise ValueError('GAMER_G2_0036_PARENT_0035_CASE_DRIFT:'+key)
        for name,value in all_metrics.items():
            if name.startswith('baseline__') and parent35['metrics'].get(name)!=value:
                raise ValueError('GAMER_G2_0036_PARENT_0035_LIVE_REPLAY_DRIFT:'+split+':'+str(index)+':'+name)
    return {'group':split,'index':index,'prompt_sha256':base._sha(row['prompt']),
        'family':row['family'],'structural_topology':row.get('structural_topology','native'),
        'gold_total':len(gold),'candidate_gold':sum(edge in gold for edge in base_item['edges']),
        'metrics':all_metrics,'coverage':coverage}


def summarize_coverage(rows):
    keys=('gold_endpoint_count','catalog_gold_endpoint_count',
          'actionable_catalog_gold_endpoint_count','lost_gold_endpoints_to_catalog_cap',
          'baseline_gold_endpoint_count','unrecoverable_gold_endpoints',
          'catalog_candidate_count')
    out={'cases':len(rows),**{k:sum(r['coverage'][k] for r in rows) for k in keys}}
    out['variants']={}
    for arm in ('baseline',*[str(s) for s in SEEDS]):
        variant_keys=('selected_anchors','new_gold_anchors','new_false_anchors',
            'gold_edges_reachable_by_endpoints','represented_gold_edges',
            'fully_representable')
        out['variants'][arm]={k:sum(r['coverage'][arm][k] for r in rows)
                              for k in variant_keys}
    return out


def development_gates(fresh,ood,coverage):
    outcomes={}
    for seed in SEEDS:
        checks={}
        for mode in ('group','structured'):
            key=f'{mode}_s{seed}'
            before=fresh['models'][f'baseline__{key}']
            after=fresh['models'][f'selective__{key}']
            ood_old=ood['models'][f'baseline__{key}']
            ood_new=ood['models'][f'selective__{key}']
            checks[mode]={'f1_delta':after['f1']-before['f1'],
                'exact_delta':after['exact_cases']-before['exact_cases'],
                'ranking_failure_rate_delta':after['ranking_failure_rate']-before['ranking_failure_rate'],
                'ood_f1_delta':ood_new['f1']-ood_old['f1'],
                'passes':bool(after['f1']>=before['f1'] and
                    after['exact_cases']>=before['exact_cases'] and
                    after['ranking_failure_rate']<=before['ranking_failure_rate'] and
                    ood_new['f1']>=ood_old['f1'] and
                    ood_new['exact_cases']>=ood_old['exact_cases'])}
        coverage_seed=coverage['variants'][str(seed)]
        baseline=coverage['variants']['baseline']
        gain=coverage_seed['represented_gold_edges']-baseline['represented_gold_edges']
        outcomes[str(seed)]={'new_gold_edges_represented':gain,
            'selected_new_gold_anchors':coverage_seed['new_gold_anchors'],
            'selected_false_anchors':coverage_seed['new_false_anchors'],
            'models':checks,'passes':bool(gain>0 and
                   all(v['passes'] for v in checks.values()))}
    return {'seeds':outcomes,'all_seeds_pass':all(v['passes'] for v in outcomes.values()),
            'no_dev_policy_selection':True,'production_promotion':False,'g2_certified':False}


def run(*,precision30_run,representation31_run,completeness32_run,structured33_run,
        diagnosis34_run,coverage35_run,output,device='cuda',max_length=MAX_LENGTH,**kwargs):
    if max_length!=MAX_LENGTH:
        raise ValueError('GAMER_G2_0036_FIXED_PROTOCOL')
    if Path(output).exists():raise FileExistsError('GAMER_G2_0036_IMMUTABLE_OUTPUT')
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
    parent35,hash35=verify_parent35(coverage35_run,ancestors=ancestors35,
        train_sha=sha_rows(state['training']),old_dev=dev34,fresh35=dev35)
    (train_rows,fit_rows,audit_rows),fresh=fresh_cohorts(state,cal30,dev30,cal31,
        dev31,train32,fit32,audit32,dev32,train33,fit33,audit33,dev33,dev34,dev35)
    models31=x32.load_parent_models(tensors31,device='cpu')
    nets32=x33.load_parent32_models(tensors32,device='cpu')
    nets33=x34.load_parent33_models(tensors33,device='cpu')
    parent_policies={'calibrated_reference_0030':pol30['calibrated_reference'],
        'calibrated_reference_0031':pol31['calibrated_reference'],
        'predicate_argument_0030':pol30[x31.CHAMPION],
        **{k:pol31[k] for k in x32.SOURCE_NAMES}}
    def collect(rows,group):
        collected=[]
        for idx,row in enumerate(rows,1):
            evidence,_=freeze_case(row,state['head'],state['model'],state['tokenizer'],
                device=device,max_length=max_length)
            collected.append((evidence,row))
            if idx==1 or idx%40==0 or idx==len(rows):
                print(f'GAMER_G2_0036_CAPTURE {group} {idx}/{len(rows)}',flush=True)
        return collected
    train_cases=[(e,label_train_case(e,row)) for e,row in collect(train_rows,'train_0036')]
    acceptors,train_audit=train_acceptors(train_cases)
    policy,cal_audit=fit_policies(collect(fit_rows,'train_fit_0036'),
                                  collect(audit_rows,'train_audit_0036'),acceptors)
    evaluated={}
    for split,rows in {**dev35,**fresh}.items():
        results=[]
        for index,row in enumerate(rows,1):
            evidence,(prepared,hidden,eligible)=freeze_case(row,state['head'],
                state['model'],state['tokenizer'],device=device,max_length=max_length)
            results.append(analyze_case(row,evidence,state,models31,parent_policies,
                pol32,nets32,nets33,pol33,acceptors,policy,prepared,hidden,eligible,
                split=split,index=index,
                parent35=(parent35[split][index-1] if split in dev35 else None)))
            if index==1 or index%20==0 or index==len(rows):
                print(f'GAMER_G2_0036_EVAL {split} {index}/{len(rows)}',flush=True)
        evaluated[split]=results
    x31.rel.validate_original_state(state['head'],state['head_before'])
    if any(not torch.equal(v,state['reference'].state_dict()[k].detach().cpu())
           for k,v in state['reference_before'].items()):
        raise ValueError('GAMER_G2_0036_FROZEN_REFERENCE_MUTATION')
    if any(p.requires_grad for p in state['model'].parameters()):
        raise ValueError('GAMER_G2_0036_FROZEN_BACKBONE_MUTATION')
    replay=[r for k in REPLAY for r in evaluated[k]]
    new=[r for k in FRESH for r in evaluated[k]]
    fresh_metrics=p30.aggregate(new);ood=p30.aggregate(evaluated[FRESH[1]])
    fresh_coverage=summarize_coverage(new)
    report={'schema':SCHEMA,'status':'TRAIN_ONLY_SELECTIVE_G1_DEV_NOT_CERTIFIED',
        'parent_hashes':{**ancestors35,**hash35},
        'train_dataset_sha256':sha_rows(state['training']),
        'datasets_sha256':{**{k:sha_rows(v) for k,v in
            {'train_0036':train_rows,'train_fit_0036':fit_rows,
             'train_audit_0036':audit_rows}.items()},
            **{k:sha_rows(v) for k,v in {**dev35,**fresh}.items()}},
        'train_cases':len(train_rows),'train_fit_cases':len(fit_rows),
        'train_audit_cases':len(audit_rows),
        'new_optimizer_steps':TRAIN_STEPS*len(SEEDS),
        'protocol':{'selector_budget':MAX_EXTRA,'candidate_catalog_limit':CATALOG_LIMIT,
            'fixed_train_policy_grid':{'thresholds':list(THRESHOLDS),'budgets':list(BUDGETS)},
            'seeds':list(SEEDS),'new_independent_dev':320,'new_topology_dev':128,
            'replayed_dev':448},
        'frozen_backbone':True,'frozen_g1':True,
        'frozen_g2_0031_occurrence_graph':True,
        'frozen_g2_0032_completeness':True,'frozen_g2_0033_set_decoder':True,
        'dev_labels_used_for_training':False,'dev_labels_used_for_calibration':False,
        'sealed_test_used':False,'production_promotion':False,
        'g2_certified':False,'graph_execution_authorized':False,
        'parent_live_replay_passed':True,'no_dev_policy_selection':True,
        'train_audit':train_audit,'train_policy_audit':cal_audit,
        'evaluation':{'new_0036_dev':fresh_metrics,
                      'replayed_0035_dev':p30.aggregate(replay)},
        'coverage_by_cohort':{'new_0036_dev':fresh_coverage,
                              'replayed_0035_dev':summarize_coverage(replay)},
        'development_gates':development_gates(fresh_metrics,ood,fresh_coverage),
        'by_split':{k:p30.aggregate(v) for k,v in evaluated.items()},
        'coverage_by_split':{k:summarize_coverage(v) for k,v in evaluated.items()},
        'limitations':{'frozen_catalog_cannot_recover_absent_proposals':True,
            'selector_is_feature_only_not_context_retrained':True,
            'no_g2_discriminator_retraining':True,
            'g2_0036_not_production_certified':True}}
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
