"""G2-0038: contextual G1 occurrence discrimination on TRAIN-only evidence.

G1/G2/backbone stay frozen. The only trainable components are independent
occurrence acceptors. All admissions are label-blind; gold is read to build
TRAIN supervision or to score already-frozen DEV decisions, never to choose.
"""
from __future__ import annotations

from collections import Counter
import gzip
import json
import math
import random
from pathlib import Path

import torch
from torch import nn
from torch.nn import functional as F
from safetensors import safe_open
from safetensors.torch import save_file

from tools import nanojev_gamer_v1_g2_curriculum_repair as x37

x36=x37.x36
x35=x37.x35
x34=x37.x34
x33=x37.x33
x32=x37.x32
x31=x37.x31
p30=x37.p30
base=x37.base
backbone=x37.backbone
general16=x37.general16

SCHEMA='nanojev.gamer.graph.g2.contextual-occurrence-discrimination.v1'
REPORT='g2_contextual_occurrence_report.json'
LEDGER='g2_contextual_occurrence_per_case.jsonl.gz'
WEIGHTS='g2_contextual_occurrence_development.safetensors'
POLICIES='g2_contextual_occurrence_frozen_policies.json'
SEED=460538
SEEDS=x36.SEEDS
MAX_LENGTH=x36.MAX_LENGTH
TRAIN_STEPS=320
PROJECTION_DIM=48
# Five frozen contextual token slots, one nearest selected competitor and one
# strongest nearby unselected competitor; plus structural comparison metadata.
CONTEXT_DIM=PROJECTION_DIM*7+8
MODEL_INPUT=x36.FEATURE_DIM+CONTEXT_DIM
FRESH=('new_independent_dev_0038','new_topology_dev_0038')
REPLAY=x37.FRESH
VIEWS=('feature_only','contextual')
MIN_PRECISION=.75


def sha_rows(rows):return x37.sha_rows(rows)


def verify_parent37(directory, *, ancestors, train_sha, fresh37, pool37):
    """Reject changed G2-0037 ancestry, policy, weights, cases or aggregates."""
    path=Path(directory)
    rp=path/x37.REPORT
    report=json.loads(rp.read_text(encoding='utf-8'))
    expected={'schema':x37.SCHEMA,
        'status':'TRAIN_ONLY_CURRICULUM_REPAIR_DEV_NOT_CERTIFIED',
        'parent_hashes':ancestors,'train_dataset_sha256':train_sha,
        'frozen_backbone':True,'frozen_g1':True,
        'frozen_g2_0031_occurrence_graph':True,
        'frozen_g2_0032_completeness':True,
        'frozen_g2_0033_set_decoder':True,
        'dev_labels_used_for_training':False,'dev_labels_used_for_calibration':False,
        'sealed_test_used':False,'production_promotion':False,
        'g2_certified':False,'graph_execution_authorized':False,
        'no_dev_policy_selection':True,'parent_live_replay_passed':True,
        'train_pool_sha256':sha_rows([r for part in pool37 for r in part]),
        'new_optimizer_steps':x37.TRAIN_STEPS*len(SEEDS)}
    for key,value in expected.items():
        if report.get(key)!=value:
            raise ValueError('GAMER_G2_0038_PARENT_0037_CONTRACT:'+key)
    for split,rows in fresh37.items():
        if report['datasets_sha256'].get(split)!=sha_rows(rows):
            raise ValueError('GAMER_G2_0038_PARENT_0037_COHORT_HASH:'+split)
    if report['train_cases']!=x37.TRAIN or report['train_fit_cases']!=x37.CAL_FIT or report['train_audit_cases']!=x37.CAL_AUDIT:
        raise ValueError('GAMER_G2_0038_PARENT_0037_TRAIN_SIZES')
    for name,key in ((x37.LEDGER,'ledger_sha256'),
                     (x37.WEIGHTS,'weights_sha256'),
                     (x37.POLICIES,'policies_sha256')):
        if backbone.sha256(path/name)!=report.get(key):
            raise ValueError('GAMER_G2_0038_PARENT_0037_FILE_HASH:'+name)
    policy=json.loads((path/x37.POLICIES).read_text(encoding='utf-8'))
    if (set(policy)!={f'acceptor_s{s}' for s in SEEDS} or
            any(report['train_policy_audit'][str(s)]['policy']!=policy[f'acceptor_s{s}']
                for s in SEEDS)):
        raise ValueError('GAMER_G2_0038_PARENT_0037_POLICIES')
    with safe_open(path/x37.WEIGHTS,framework='pt',device='cpu') as handle:
        ref={f'acceptor_s{s}.{name}':v.shape for s in SEEDS
             for name,v in x36.OccurrenceAcceptor().state_dict().items()}
        if set(handle.keys())!=set(ref) or any(
                handle.get_tensor(k).shape!=shape or
                not bool(torch.isfinite(handle.get_tensor(k)).all())
                for k,shape in ref.items()):
            raise ValueError('GAMER_G2_0038_PARENT_0037_WEIGHTS')
    rows={name:[] for name in fresh37}
    with gzip.open(path/x37.LEDGER,'rt',encoding='utf-8') as stream:
        for raw in stream:
            entry=json.loads(raw);group=entry.get('group')
            if group not in report['by_split']:
                raise ValueError('GAMER_G2_0038_PARENT_0037_UNKNOWN_SPLIT')
            if group not in rows:
                continue
            idx=len(rows[group]);cases=fresh37[group]
            if idx>=len(cases) or any((
                    entry.get('index')!=idx+1,
                    entry.get('family')!=cases[idx]['family'],
                    entry.get('prompt_sha256')!=base._sha(cases[idx]['prompt']),
                    entry.get('gold_total')!=len(base.gold_binding_edges(cases[idx])))):
                raise ValueError('GAMER_G2_0038_PARENT_0037_ROW_IDENTITY')
            rows[group].append(entry)
    if any(len(rows[k])!=len(v) for k,v in fresh37.items()):
        raise ValueError('GAMER_G2_0038_PARENT_0037_ROW_COUNT')
    for split,entries in rows.items():
        if (report['by_split'][split]!=p30.aggregate(entries) or
                report['coverage_by_split'][split]!=x36.summarize_coverage(entries)):
            raise ValueError('GAMER_G2_0038_PARENT_0037_AGGREGATES:'+split)
    fresh=[entry for split in x37.FRESH for entry in rows[split]]
    if (report['evaluation']['new_0037_dev']!=p30.aggregate(fresh) or
            report['coverage_by_cohort']['new_0037_dev']!=x36.summarize_coverage(fresh)):
        raise ValueError('GAMER_G2_0038_PARENT_0037_EVALUATION')
    return rows,{'g2_0037_report_sha256':backbone.sha256(rp),
                 'g2_0037_ledger_sha256':backbone.sha256(path/x37.LEDGER),
                 'g2_0037_weights_sha256':backbone.sha256(path/x37.WEIGHTS),
                 'g2_0037_policies_sha256':backbone.sha256(path/x37.POLICIES)}


def fresh_cohorts(prior,pool37,dev37):
    """Create G2-0038 TRAIN and DEV partitions without prior prompt overlap."""
    prior_unique=list({base._sha(row['prompt']):row for row in prior}.values())
    already=[*prior_unique,*(r for part in pool37 for r in part),
             *(r for part in dev37.values() for r in part)]
    hashes={base._sha(row['prompt']) for row in already}
    if len(hashes)!=len(already):
        raise ValueError('GAMER_G2_0038_PARENT_DUPLICATE')
    pools=x37.novel_train_pool(already,seed=SEED)
    excluded=[*already,*(r for part in pools for r in part)]
    independent=general16.independent_rows((excluded,),limit=320,seed=SEED+100)
    topology=general16.heldout_topology_rows((excluded,independent),limit=128,
                                              seed=SEED+101)
    combined=[*excluded,*independent,*topology]
    if (len({base._sha(r['prompt']) for r in combined})!=len(combined) or
            any(r['split']!='train' for group in pools for r in group) or
            any(r['split']!='dev' for r in (*independent,*topology)) or
            {r['structural_topology'] for r in topology}!={'4x4','4x2','2x4'}):
        raise ValueError('GAMER_G2_0038_COHORT_LEAKAGE')
    return pools,dict(zip(FRESH,(independent,topology)))


def _normalise_view(x):
    if not bool(torch.isfinite(x).all()):
        raise ValueError('GAMER_G2_0038_NONFINITE_CONTEXT')
    return F.normalize(x.float(),dim=-1,eps=1e-6)


def _fixed_projection(width):
    """Public fixed seed; only frozen Qwen token states, no learned embedding."""
    generator=torch.Generator(device='cpu').manual_seed(460038)
    matrix=torch.randint(0,2,(width,PROJECTION_DIM),generator=generator,
                         dtype=torch.int8).float().mul_(2).sub_(1)
    return matrix*(1./math.sqrt(width))


def contextual_features(evidence,prepared,hidden):
    """Five frozen token slots plus competing G1 spans, no text/gold access."""
    n=len(evidence['spans'])
    if n==0:return torch.empty(0,CONTEXT_DIM)
    spans=[*evidence['baseline_spans'],*evidence['spans']]
    keys=[(s['token_first'],s['token_last'],s['kind']) for s in spans]
    if len(set(keys))!=len(keys):
        raise ValueError('GAMER_G2_0038_DUPLICATE_CONTEXT_SPAN')
    anchors=base.predicted_anchors(spans,prepared)
    if anchors!=[*evidence['baseline_anchors'],*evidence['anchors']]:
        raise ValueError('GAMER_G2_0038_CONTEXT_ANCHOR_DRIFT')
    windows,masks=x31.occurrence_windows(hidden,prepared,anchors)
    w=windows.float();nbase=len(evidence['baseline_spans'])
    # A deterministic, shared-sign projection permits bounded CPU TRAIN
    # storage and ensures that novel names use the same feature basis.
    projection=_fixed_projection(w.shape[-1])
    projected=_normalise_view(_normalise_view(w)@projection)
    projected=projected*masks[:,:,None].float()
    centres=projected[:,2]
    result=[]
    for idx,span in enumerate(evidence['spans']):
        position=nbase+idx
        first,last=span['token_first'],span['token_last']
        competitors=[]
        for other in range(len(spans)):
            if other==position:continue
            candidate=spans[other]
            gap=max(0,candidate['token_first']-last,
                    first-candidate['token_last'])
            overlap=x36._iou((first,last+1),
                (candidate['token_first'],candidate['token_last']+1))
            same=int(candidate['kind']==span['kind'])
            # Prefer same-kind overlapping competitors, then nearby spans.
            priority=(-overlap,gap,-same,other)
            competitors.append((priority,other,overlap,gap,same))
        selected=[q for q in competitors if q[1]<nbase]
        alternatives=[q for q in competitors if q[1]>=nbase]
        nearest_sel=min(selected,key=lambda q:q[0]) if selected else None
        nearest_alt=min(alternatives,key=lambda q:q[0]) if alternatives else None
        zeros=torch.zeros(PROJECTION_DIM)
        selected_vector=centres[nearest_sel[1]] if nearest_sel else zeros
        alternate_vector=centres[nearest_alt[1]] if nearest_alt else zeros
        views=torch.cat((projected[position].reshape(-1),
                         centres[position]-selected_vector,
                         centres[position]-alternate_vector))
        extra=torch.tensor([
            float(nearest_sel is not None),
            float(nearest_alt is not None),
            (nearest_sel[2] if nearest_sel else 0.),
            (nearest_alt[2] if nearest_alt else 0.),
            1./(1.+(nearest_sel[3] if nearest_sel else 100)),
            1./(1.+(nearest_alt[3] if nearest_alt else 100)),
            float(nearest_sel[4]) if nearest_sel else 0.,
            float(nearest_alt[4]) if nearest_alt else 0.],dtype=torch.float32)
        result.append(torch.cat((views,extra)))
    features=torch.stack(result).contiguous()
    if features.shape!=(n,CONTEXT_DIM) or not bool(torch.isfinite(features).all()):
        raise ValueError('GAMER_G2_0038_CONTEXT_SHAPE')
    return features


def freeze_context_case(row,head,model,tok,*,device,max_length):
    evidence,hidden_bundle=x36.freeze_case(row,head,model,tok,
                                            device=device,max_length=max_length)
    prepared,hidden,eligible=hidden_bundle
    evidence['context_features']=contextual_features(evidence,prepared,hidden)
    # Freeze-case output is only an inference representation. Never include
    # author graph labels/relations/ground-truth spans here.
    return evidence,(prepared,hidden,eligible)


class ContextualAcceptor(nn.Module):
    def __init__(self):
        super().__init__()
        self.context=nn.Sequential(nn.LayerNorm(CONTEXT_DIM),
            nn.Linear(CONTEXT_DIM,96),nn.GELU(),nn.Linear(96,48),nn.GELU())
        self.head=nn.Sequential(nn.LayerNorm(x36.FEATURE_DIM+48),
            nn.Linear(x36.FEATURE_DIM+48,64),nn.GELU(),nn.Linear(64,1))
    def forward(self,features):
        if features.ndim!=2 or features.shape[1]!=MODEL_INPUT:
            raise ValueError('GAMER_G2_0038_CONTEXT_INPUT_SHAPE')
        prior=features[:,:x36.FEATURE_DIM]
        representation=self.context(features[:,x36.FEATURE_DIM:])
        return self.head(torch.cat((prior,representation),dim=-1)).flatten()


def scoring_features(evidence,view):
    original=evidence['features']
    if view=='feature_only':return original
    if view=='contextual':
        ctx=evidence.get('context_features')
        if ctx is None or ctx.shape!=(len(original),CONTEXT_DIM):
            raise ValueError('GAMER_G2_0038_CONTEXT_MISSING')
        return torch.cat((original,ctx),dim=1)
    raise ValueError('GAMER_G2_0038_UNKNOWN_VIEW')


def choose_spans(evidence,model,policy,view):
    return x36.choose_spans({**evidence,'features':scoring_features(evidence,view)},
                            model,policy)


def calibration_metrics(cases,model,cfg,view):
    tp=fp=added=0;reachable=0
    for evidence,row in cases:
        selected=choose_spans(evidence,model,cfg,view)
        original=set(evidence['baseline_anchors'])
        chosen_keys={(s['token_first'],s['token_last'],s['kind']) for s in selected}
        new={a for s,a in zip(evidence['spans'],evidence['anchors'])
             if (s['token_first'],s['token_last'],s['kind']) in chosen_keys}-original
        gold=base.gold_binding_edges(row)
        gold_end={endpoint for src,dst,_ in gold for endpoint in (src,dst)}
        tp+=len(new&gold_end);fp+=len(new-gold_end);added+=len(new)
        reachable+=sum(a in original|new and b in original|new and
                       not (a in original and b in original) for a,b,_ in gold)
    return {'true_new_endpoints':tp,'false_new_endpoints':fp,
            'added_endpoints':added,'newly_reachable_gold_edges':reachable,
            'admission_precision':tp/max(1,tp+fp)}


def ranking_audit(cases,model,view):
    """TRAIN-only discrimination evidence independent of threshold selection."""
    tp_at1=eligible=pair_wins=pair_total=positives=negatives=0
    for evidence,row in cases:
        labels=x36.label_train_case(evidence,row)
        positives+=int(labels.sum());negatives+=len(labels)-int(labels.sum())
        if not len(labels) or not bool((labels>0).any()):continue
        eligible+=1
        with torch.inference_mode():
            score=model(scoring_features(evidence,view)).detach().cpu()
        highest=int(torch.argmax(score))
        tp_at1+=int(labels[highest]>0)
        for p in torch.nonzero(labels>0).flatten().tolist():
            # Nearest hard same-kind negative without looking at DEV gold.
            neg=[i for i in range(len(labels)) if labels[i]<.5]
            if not neg:continue
            rival=min(neg,key=lambda i:(
                evidence['spans'][i]['kind']!=evidence['spans'][p]['kind'],
                abs(evidence['spans'][i]['token_first']-evidence['spans'][p]['token_first']),i))
            pair_wins+=int(score[p]>score[rival]);pair_total+=1
    return {'positive_rows':eligible,'positive_proposals':positives,
            'negative_proposals':negatives,'top1_correct':tp_at1,
            'top1_precision':tp_at1/max(1,eligible),
            'hard_pair_correct':pair_wins,'hard_pair_total':pair_total,
            'hard_pair_accuracy':pair_wins/max(1,pair_total)}


def train_models(cases,*,steps=TRAIN_STEPS):
    """Balanced BCE and within-case hard-negative ranking, TRAIN only."""
    feature_models,feature_audit=x36.train_acceptors(
        [(e,x36.label_train_case(e,row)) for e,row in cases],steps=steps)
    all_x=[];all_y=[];pairs=[];offset=0
    for evidence,row in cases:
        labels=x36.label_train_case(evidence,row)
        features=scoring_features(evidence,'contextual')
        all_x.append(features);all_y.append(labels)
        positive=torch.nonzero(labels>.5).flatten().tolist()
        negative=torch.nonzero(labels<.5).flatten().tolist()
        for p in positive:
            if not negative:continue
            # A local hard negative is the same-kind competing span closest
            # to the gold occurrence, not a random easy negative.
            n=min(negative,key=lambda i:(
                evidence['spans'][i]['kind']!=evidence['spans'][p]['kind'],
                -x36._iou((evidence['spans'][i]['token_first'],evidence['spans'][i]['token_last']+1),
                          (evidence['spans'][p]['token_first'],evidence['spans'][p]['token_last']+1)),
                abs(evidence['spans'][i]['token_first']-evidence['spans'][p]['token_first']),i))
            pairs.append((offset+p,offset+n))
        offset+=len(labels)
    x=torch.cat(all_x);y=torch.cat(all_y)
    pos=torch.nonzero(y>.5).flatten();neg=torch.nonzero(y<.5).flatten()
    if len(pos)<1 or len(neg)<1 or len(pairs)<1:
        raise ValueError('GAMER_G2_0038_INSUFFICIENT_PAIR_SUPERVISION')
    pair_idx=torch.tensor(pairs,dtype=torch.int64)
    nets={};audits={}
    for seed in SEEDS:
        torch.manual_seed(seed)
        net=ContextualAcceptor().train()
        opt=torch.optim.AdamW(net.parameters(),lr=.001,weight_decay=.01)
        generator=torch.Generator().manual_seed(seed)
        for step in range(steps):
            pos_ids=pos[torch.randint(len(pos),(48,),generator=generator)]
            neg_ids=neg[torch.randint(len(neg),(48,),generator=generator)]
            idx=torch.cat((pos_ids,neg_ids))
            logits=net(x[idx]);bce=F.binary_cross_entropy_with_logits(logits,y[idx])
            sample=pair_idx[torch.randint(len(pair_idx),(32,),generator=generator)]
            paired=net(x[sample.reshape(-1)]).reshape(-1,2)
            contrast=F.softplus(0.5-(paired[:,0]-paired[:,1])).mean()
            loss=bce+.4*contrast
            opt.zero_grad(set_to_none=True);loss.backward();opt.step()
            if step==0 or (step+1)%80==0:
                print(f'GAMER_G2_0038_TRAIN_CONTEXT {seed} {step+1}/{steps} '
                      f'loss={float(loss.detach()):.5f}',flush=True)
        nets[seed]=net.eval().requires_grad_(False)
        audits[str(seed)]={'train_examples':len(y),'train_positive':len(pos),
            'train_negative':len(neg),'hard_negative_pairs':len(pairs),
            'optimizer_steps':steps,'last_train_loss':float(loss.detach())}
    return ({'feature_only':feature_models,'contextual':nets},
            {'feature_only':feature_audit,'contextual':audits})


def fit_policies(fit,audit,nets):
    """Preserve 75% precision and independent TRAIN audit for *both* arms."""
    policies={};summary={}
    for view in VIEWS:
        for seed,net in nets[view].items():
            grid=[];eligible=[]
            for threshold in x36.THRESHOLDS:
                for budget in x36.BUDGETS:
                    cfg={'enabled':True,'threshold':threshold,'max_extra':budget}
                    stats=calibration_metrics(fit,net,cfg,view)
                    passed=(stats['true_new_endpoints']>=3 and
                        stats['admission_precision']>=MIN_PRECISION and
                        stats['newly_reachable_gold_edges']>0)
                    grid.append({'threshold':threshold,'max_extra':budget,
                                 'fit':stats,'fit_eligible':passed})
                    if passed:
                        eligible.append((stats['newly_reachable_gold_edges'],
                                         -stats['false_new_endpoints'],-budget,
                                         threshold,cfg,stats))
            if eligible:
                choice=max(eligible,key=lambda a:a[:4]);cfg=choice[4]
                train_fit=choice[5]
                train_audit=calibration_metrics(audit,net,cfg,view)
                if (train_audit['true_new_endpoints']<2 or
                    train_audit['admission_precision']<MIN_PRECISION or
                    train_audit['newly_reachable_gold_edges']<=0):
                    cfg={**cfg,'enabled':False,
                         'disabled_reason':'failed_independent_train_audit'}
            else:
                cfg={'enabled':False,'threshold':.97,'max_extra':0,
                     'disabled_reason':'no_precision_safe_train_policy'}
                train_fit=None;train_audit=None
            key=f'{view}_s{seed}'
            policies[key]=cfg
            summary[key]={'fit':train_fit,'audit':train_audit,
                          'fit_grid':grid,'eligible_fit_policies':len(eligible),
                          'fit_rank':ranking_audit(fit,net,view),
                          'audit_rank':ranking_audit(audit,net,view),
                          'policy':cfg}
            print(f'GAMER_G2_0038_SELECT {key} enabled={cfg["enabled"]} '
                  f'eligible={len(eligible)} '
                  f'fit_tp={train_fit["true_new_endpoints"] if train_fit else 0}',flush=True)
    return policies,summary


def analyze_case(row,evidence,state,models31,parent_policies,pol32,nets32,nets33,
                 pol33,networks,policies,prepared,hidden,eligible,*,split,index,
                 parent37=None):
    """Predict ALL models with gold withheld, then perform scoring."""
    baseline=x36.frozen_g2_case(row,evidence['baseline_spans'],
                                prepared,hidden,eligible,state['head'])
    base_predictions,base_ranks,base_item=x36.score_decoders(
        baseline,row,state,models31,parent_policies,pol32,nets32,nets33,
        pol33,device=hidden.device)
    frozen={'baseline':(baseline,base_item,base_predictions,base_ranks)}
    for view in VIEWS:
        for seed,net in networks[view].items():
            spans=choose_spans(evidence,net,policies[f'{view}_s{seed}'],view)
            if spans==evidence['baseline_spans']:
                case,item,preds,ranks=baseline,base_item,base_predictions,base_ranks
            else:
                case=x36.frozen_g2_case(row,spans,prepared,hidden,eligible,state['head'])
                preds,ranks,item=x36.score_decoders(case,row,state,models31,
                    parent_policies,pol32,nets32,nets33,pol33,device=hidden.device)
            frozen[f'{view}:{seed}']=(case,item,preds,ranks)
    # Author graph enters only AFTER frozen admissions, relations and ranks.
    gold=base.gold_binding_edges(row)
    endpoints={a for left,right,_ in gold for a in (left,right)}
    original=set(evidence['baseline_anchors'])
    catalog=evidence['catalog_anchors']
    actionable=original|set(evidence['anchors'])
    coverage={'catalog_gold_endpoint_count':len(catalog&endpoints),
        'actionable_catalog_gold_endpoint_count':len(actionable&endpoints),
        'lost_gold_endpoints_to_catalog_cap':len((catalog-actionable)&endpoints),
        'gold_endpoint_count':len(endpoints),
        'baseline_gold_endpoint_count':len(original&endpoints),
        'unrecoverable_gold_endpoints':len(endpoints-catalog),
        'catalog_candidate_count':len(evidence['anchors']),
        'catalog_limit':x36.CATALOG_LIMIT}
    metrics={}
    for view,(case,item,predictions,ranks) in frozen.items():
        supplied={**item,'gold':gold,'gold_total':len(gold),
            'candidate_gold':sum(edge in gold for edge in item['edges'])}
        for seed in SEEDS:
            for mode in ('group','structured'):
                key=f'{mode}_s{seed}'
                # Case is shared among 3 seeds within each view/arm.
                if view!='baseline' and view.split(':')[1]!=str(seed):continue
                name=f'{view.split(":")[0]}__{key}'
                competing,ranking=p30.ranking_counts(supplied,ranks[key])
                metrics[name]={**p30.measure(predictions[key],supplied),
                               'competing_gold':competing,'ranking_failures':ranking}
        anchors=set(case['anchors'])
        ceiling=x34.candidate_ceiling(supplied,anchors)
        ceiling.pop('candidate_oracle')
        coverage[view]={'selected_anchors':len(anchors),
            'new_gold_anchors':len((anchors-original)&endpoints),
            'new_false_anchors':len((anchors-original)-endpoints),
            'gold_edges_reachable_by_endpoints':sum(a in anchors and b in anchors
                for a,b,_ in gold),
            'represented_gold_edges':ceiling['represented_gold'],
            'fully_representable':ceiling['fully_representable']}
    if parent37 is not None:
        expected={'group':split,'index':index,
            'prompt_sha256':base._sha(row['prompt']),
            'family':row['family'],'gold_total':len(gold)}
        for key,value in expected.items():
            if parent37.get(key)!=value:
                raise ValueError('GAMER_G2_0038_PARENT_0037_CASE_DRIFT:'+key)
        for name,value in metrics.items():
            if name.startswith('baseline__') and parent37['metrics'].get(name)!=value:
                raise ValueError('GAMER_G2_0038_PARENT_0037_LIVE_REPLAY_DRIFT:'+
                                 split+':'+str(index)+':'+name)
    return {'group':split,'index':index,'prompt_sha256':base._sha(row['prompt']),
        'family':row['family'],
        'structural_topology':row.get('structural_topology','native'),
        'gold_total':len(gold),
        'candidate_gold':sum(edge in gold for edge in base_item['edges']),
        'metrics':metrics,'coverage':coverage}


def summarize_coverage(rows):
    keys=('gold_endpoint_count','catalog_gold_endpoint_count',
          'actionable_catalog_gold_endpoint_count','lost_gold_endpoints_to_catalog_cap',
          'baseline_gold_endpoint_count','unrecoverable_gold_endpoints',
          'catalog_candidate_count')
    out={'cases':len(rows),**{k:sum(r['coverage'][k] for r in rows) for k in keys}}
    out['variants']={}
    for arm in ('baseline',*(f'{view}:{seed}' for view in VIEWS for seed in SEEDS)):
        names=('selected_anchors','new_gold_anchors','new_false_anchors',
            'gold_edges_reachable_by_endpoints','represented_gold_edges',
            'fully_representable')
        out['variants'][arm]={k:sum(r['coverage'][arm][k] for r in rows) for k in names}
    return out


def development_gates(fresh,ood,coverage,policies):
    gates={}
    for view in VIEWS:
        for seed in SEEDS:
            arm=f'{view}:{seed}'
            admitted=coverage['variants'][arm]
            checks={}
            for mode in ('group','structured'):
                base_key=f'baseline__{mode}_s{seed}'
                changed=f'{view}__{mode}_s{seed}'
                b=fresh['models'][base_key];c=fresh['models'][changed]
                bo=ood['models'][base_key];co=ood['models'][changed]
                checks[mode]={'f1_delta':c['f1']-b['f1'],
                    'exact_graph_delta':c['exact_cases']-b['exact_cases'],
                    'ranking_failure_delta':c['ranking_failure_rate']-b['ranking_failure_rate'],
                    'topology_f1_delta':co['f1']-bo['f1'],
                    'passed':c['f1']>=b['f1'] and
                             c['exact_cases']>=b['exact_cases'] and
                             c['ranking_failure_rate']<=b['ranking_failure_rate'] and
                             co['f1']>=bo['f1']}
            gates[arm]={'enabled':policies[f'{view}_s{seed}']['enabled'],
                'new_gold_anchors':admitted['new_gold_anchors'],
                'new_false_anchors':admitted['new_false_anchors'],
                'development_admission_precision':admitted['new_gold_anchors']/max(1,
                        admitted['new_gold_anchors']+admitted['new_false_anchors']),
                'decoder_checks':checks,
                'passed':policies[f'{view}_s{seed}']['enabled'] and
                    admitted['new_gold_anchors']>=2 and
                    admitted['new_gold_anchors']/max(1,
                        admitted['new_gold_anchors']+admitted['new_false_anchors'])>=MIN_PRECISION and
                    admitted['represented_gold_edges']>
                        coverage['variants']['baseline']['represented_gold_edges'] and
                    all(c['passed'] for c in checks.values())}
    return {'by_arm':gates,
        'contextual_improved_and_safe':any(
            gates[f'contextual:{s}']['passed'] for s in SEEDS),
        'production_promotion':False,'sealed_test_used':False}


def persist(output,report,network,policies,rows):
    dest=Path(output)
    dest.mkdir(parents=True,exist_ok=False)
    x32._write_ledger(dest/LEDGER,rows)
    report['ledger_sha256']=backbone.sha256(dest/LEDGER)
    tensors={f'{view}_s{seed}.{key}':value.detach().cpu().contiguous()
        for view in VIEWS for seed,model in network[view].items()
        for key,value in model.state_dict().items()}
    save_file(tensors,str(dest/WEIGHTS))
    report['weights_sha256']=backbone.sha256(dest/WEIGHTS)
    (dest/POLICIES).write_text(json.dumps(policies,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    report['policies_sha256']=backbone.sha256(dest/POLICIES)
    (dest/REPORT).write_text(json.dumps(report,sort_keys=True,indent=2,
        allow_nan=False)+'\n',encoding='utf-8')
    return report


def run(*,precision30_run,representation31_run,completeness32_run,structured33_run,
        diagnosis34_run,coverage35_run,selective36_run,curriculum37_run,output,
        device='cuda',max_length=MAX_LENGTH,**kwargs):
    if max_length!=MAX_LENGTH:raise ValueError('GAMER_G2_0038_FIXED_PROTOCOL')
    if Path(output).exists():raise FileExistsError('GAMER_G2_0038_IMMUTABLE_OUTPUT')
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
    parent36,hash36=x37.verify_parent36(selective36_run,ancestors=ancestors36,
        train_sha=sha_rows(state['training']),train36=train36,fit36=fit36,
        audit36=audit36,old_dev=dev35,fresh36=dev36)
    pool37,dev37=x37.fresh_cohorts(state,cal30,dev30,cal31,dev31,train32,fit32,
        audit32,dev32,train33,fit33,audit33,dev33,dev34,dev35,
        train36,fit36,audit36,dev36)
    ancestors37={**ancestors36,**hash36}
    parent37,hash37=verify_parent37(curriculum37_run,ancestors=ancestors37,
        train_sha=sha_rows(state['training']),fresh37=dev37,pool37=pool37)
    prior=x37._previous_rows(state,cal30,dev30,cal31,dev31,train32,fit32,audit32,
        dev32,train33,fit33,audit33,dev33,dev34,dev35,
        train36,fit36,audit36,dev36)
    pool,fresh=fresh_cohorts(prior,pool37,dev37)
    models31=x32.load_parent_models(tensors31,device='cpu')
    nets32=x33.load_parent32_models(tensors32,device='cpu')
    nets33=x34.load_parent33_models(tensors33,device='cpu')
    parent_policies={'calibrated_reference_0030':pol30['calibrated_reference'],
        'calibrated_reference_0031':pol31['calibrated_reference'],
        'predicate_argument_0030':pol30[x31.CHAMPION],
        **{k:pol31[k] for k in x32.SOURCE_NAMES}}
    mined,mining_audit=x37.mine_train_curriculum(pool,state['head'],state['model'],
        state['tokenizer'],device=device,max_length=max_length,
        freeze=freeze_context_case)
    train,fit,audit=mined
    networks,train_audit=train_models(train)
    policies,policy_audit=fit_policies(fit,audit,networks)
    checks={'feature_only':sum(int(policies[f'feature_only_s{s}']['enabled']) for s in SEEDS),
            'contextual':sum(int(policies[f'contextual_s{s}']['enabled']) for s in SEEDS)}
    rank_delta={str(seed):{
        'fit_top1_delta':policy_audit[f'contextual_s{seed}']['fit_rank']['top1_precision']-
            policy_audit[f'feature_only_s{seed}']['fit_rank']['top1_precision'],
        'audit_top1_delta':policy_audit[f'contextual_s{seed}']['audit_rank']['top1_precision']-
            policy_audit[f'feature_only_s{seed}']['audit_rank']['top1_precision']}
        for seed in SEEDS}
    training_only=not bool(checks['contextual'])
    report={'schema':SCHEMA,
        'status':('TRAIN_CALIBRATION_UNSAFE_NO_DEV_EVALUATION' if training_only else
                  'CONTEXTUAL_OCCURRENCE_DEV_NOT_CERTIFIED'),
        'parent_hashes':{**ancestors37,**hash37},
        'train_dataset_sha256':sha_rows(state['training']),
        'datasets_sha256':{**{k:sha_rows([r for _,r in v]) for k,v in
            zip(('train_0038','train_fit_0038','train_audit_0038'),mined)},
            **{k:sha_rows(v) for k,v in {**dev37,**fresh}.items()}},
        'train_pool_sha256':sha_rows([row for group in pool for row in group]),
        'train_cases':len(train),'train_fit_cases':len(fit),'train_audit_cases':len(audit),
        'new_optimizer_steps':TRAIN_STEPS*2*len(SEEDS),
        'protocol':{'model_views':list(VIEWS),'context_dimension':CONTEXT_DIM,
            'context_projection_dimension':PROJECTION_DIM,
            'projection_seed':460038,'hard_negative_pairs':True,
            'contextual_pair_loss_weight':.4,'contextual_pair_margin':.5,
            'train_pool_limits':list(x37.POOL_LIMITS),
            'train_rich_case_quotas':list(x37.RICH_QUOTAS),
            'train_min_useful_case_quotas':list(x37.MIN_USEFUL),
            'train_only_novel_names':True,'seed':SEED,'seeds':list(SEEDS),
            'fixed_thresholds':list(x36.THRESHOLDS),
            'fixed_budgets':list(x36.BUDGETS),
            'max_admitted_occurrences':x36.MAX_EXTRA,
            'minimum_train_admission_precision':MIN_PRECISION,
            'new_independent_dev':320,'new_topology_dev':128,
            'replayed_dev':448,'early_stop_if_all_contextual_unsafe':True},
        'frozen_backbone':True,'frozen_g1':True,
        'frozen_g2_0031_occurrence_graph':True,
        'frozen_g2_0032_completeness':True,'frozen_g2_0033_set_decoder':True,
        'dev_labels_used_for_training':False,'dev_labels_used_for_calibration':False,
        'sealed_test_used':False,'production_promotion':False,
        'g2_certified':False,'graph_execution_authorized':False,
        'parent_live_replay_passed':True,'no_dev_policy_selection':True,
        'train_curriculum_mining':mining_audit,
        'train_audit':train_audit,'train_policy_audit':policy_audit,
        'enabled_policies':checks,'context_vs_feature_rank_delta':rank_delta,
        'development_evaluation_skipped':training_only,
        'limitations':{'frozen_g1_catalog_not_expanded':True,
            'context_is_frozen_token_states_not_backbone_retraining':True,
            'random_projection_is_fixed_label_blind':True,
            'train_mining_enriches_positives_vs_natural_dev':True,
            'no_g2_discriminator_retraining':True,
            'not_certified':True}}
    # Even early rejection must prove the frozen upstream checkpoints were not
    # modified by training the independently allocated acceptor models.
    x31.rel.validate_original_state(state['head'],state['head_before'])
    if any(not torch.equal(v,state['reference'].state_dict()[k].detach().cpu())
           for k,v in state['reference_before'].items()):
        raise ValueError('GAMER_G2_0038_FROZEN_REFERENCE_MUTATION')
    if any(p.requires_grad for p in state['model'].parameters()):
        raise ValueError('GAMER_G2_0038_FROZEN_BACKBONE_MUTATION')
    if training_only:
        # The full graph evaluation is pointless when every contextual model
        # would abstain. Save complete TRAIN diagnostics and weights instead.
        report['development_gates']={'contextual_improved_and_safe':False,
            'reason':'no_contextual_acceptor_passed_train_fit_and_audit'}
        report['evaluation']={}
        report['coverage_by_cohort']={}
        report['by_split']={}
        report['coverage_by_split']={}
        return persist(output,report,networks,policies,[])
    evaluated={}
    for split,rows in {**dev37,**fresh}.items():
        results=[]
        for index,row in enumerate(rows,1):
            evidence,(prepared,hidden,eligible)=freeze_context_case(row,
                state['head'],state['model'],state['tokenizer'],
                device=device,max_length=max_length)
            results.append(analyze_case(row,evidence,state,models31,parent_policies,
                pol32,nets32,nets33,pol33,networks,policies,prepared,hidden,eligible,
                split=split,index=index,
                parent37=parent37[split][index-1] if split in dev37 else None))
            if index==1 or index%20==0 or index==len(rows):
                print(f'GAMER_G2_0038_EVAL {split} {index}/{len(rows)}',flush=True)
        evaluated[split]=results
    x31.rel.validate_original_state(state['head'],state['head_before'])
    if any(not torch.equal(v,state['reference'].state_dict()[k].detach().cpu())
           for k,v in state['reference_before'].items()):
        raise ValueError('GAMER_G2_0038_FROZEN_REFERENCE_MUTATION')
    if any(p.requires_grad for p in state['model'].parameters()):
        raise ValueError('GAMER_G2_0038_FROZEN_BACKBONE_MUTATION')
    replay=[r for k in REPLAY for r in evaluated[k]]
    new=[r for k in FRESH for r in evaluated[k]]
    fresh_summary=p30.aggregate(new)
    ood=p30.aggregate(evaluated[FRESH[1]])
    coverage=summarize_coverage(new)
    report['evaluation']={'replayed_0037_dev':p30.aggregate(replay),
                          'new_0038_dev':fresh_summary}
    report['coverage_by_cohort']={
        'replayed_0037_dev':summarize_coverage(replay),
        'new_0038_dev':coverage}
    report['by_split']={k:p30.aggregate(v) for k,v in evaluated.items()}
    report['coverage_by_split']={k:summarize_coverage(v) for k,v in evaluated.items()}
    report['development_gates']=development_gates(fresh_summary,ood,coverage,policies)
    return persist(output,report,networks,policies,[*replay,*new])
