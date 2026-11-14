"""G2-0032: TRAIN-supervised graph completeness over frozen G2-0031 occurrences.

A learned source/relation-group count head and a learned whole-graph count head
propose missing edges. Selection policies are fitted only on novel TRAIN cases.
DEV and replay are strictly evaluation-only. This is an experiment, not release.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import gzip
import json
import math
import random

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from safetensors.torch import load_file, save_file

from tools import nanojev_gamer_v1_g2_representation_bakeoff as x31

p30=x31.p30
m29=x31.m29
base=x31.base
backbone=x31.backbone
general16=x31.general16
density=x31.density
source13=x31.source13

SCHEMA='nanojev.gamer.graph.g2.learned-graph-completeness.v1'
REPORT='g2_graph_completeness_report.json'
LEDGER='g2_graph_completeness_per_case.jsonl.gz'
WEIGHTS='g2_graph_completeness_development.safetensors'
POLICIES='g2_graph_completeness_frozen_policies.json'
PARENT=x31.SCHEMA
SEED=460332
SEEDS=x31.SEEDS
SOURCE_NAMES=tuple(f'occurrence_graph_s{s}' for s in SEEDS)
ARMS=('group_recovery','graph_recovery','joint_recovery')
TRAIN_FIT=240
CAL_FIT=120
CAL_AUDIT=60
COUNT_CLASSES=9  # count 8 means >=8; no single-destination assumption
TRAIN_STEPS=320
LR=5e-4
MAX_LENGTH=2048
NEW=('new_independent_dev_0032','new_topology_dev_0032')
REPLAY=x31.NEW
F1_SLACK=0.005
REQUIRED_GAIN=0.02
MIN_EDGE_PROBS=(.70,.80,.88)
CONFIDENCES=(.55,.70,.85)
MAX_ADDITIONS=(1,3,6)
GROUP_GAPS=(2.,4.)
GRAPH_OFFSETS=(-1.,0.,1.)


def _sha_rows(rows):
    return density.sha_rows(rows)


def verify_parent31(directory,*,old_cal,old_dev,cal31,dev31,ancestors,training_sha):
    """Check immutable parent ledger, all declared cohorts, policies and weights."""
    folder=Path(directory)
    rp,lp,pp,wp=(folder/name for name in (x31.REPORT,x31.LEDGER,x31.POLICIES,x31.WEIGHTS))
    report=json.loads(rp.read_text(encoding='utf-8'))
    policies=json.loads(pp.read_text(encoding='utf-8'))
    expected={'schema':PARENT,
        'status':'OCCURRENCE_REPRESENTATION_BAKEOFF_DEVELOPMENT_NOT_CERTIFIED',
        'parent_hashes':ancestors,'train_dataset_sha256':training_sha,
        'calibration_fit_sha256':_sha_rows(cal31[:x31.TRAIN_CAL_FIT]),
        'calibration_audit_sha256':_sha_rows(cal31[x31.TRAIN_CAL_FIT:]),
        'datasets_sha256':{k:_sha_rows(v) for k,v in {**old_dev,**dev31}.items()},
        'frozen_backbone':True,'frozen_g1':True,'frozen_g2_rank_head':True,
        'frozen_g2_0026':True,'frozen_parent_0029_models':True,
        'trainable_token_occurrence_encoders':True,
        'frozen_upstream_node_vectors_not_used_by_new_relational_encoders':True,
        'candidate_selection_uses_gold':False,'dev_labels_used_for_training':False,
        'dev_labels_used_for_calibration':False,'dev_checkpoint_selection':False,
        'sealed_test_used':False,'production_promotion':False,
        'g2_certified':False,'certification_authorized':False}
    for k,v in expected.items():
        if report.get(k)!=v:
            raise ValueError('GAMER_G2_0032_PARENT_0031_CONTRACT:'+k)
    for field,path in (('ledger_sha256',lp),('policies_sha256',pp),
                       ('development_weights_sha256',wp)):
        if report.get(field)!=backbone.sha256(path):
            raise ValueError('GAMER_G2_0032_PARENT_0031_HASH:'+field)
    if policies!=report['policies'] or set(policies)!={f'{arm}_s{seed}'
                                            for arm in x31.ARMS for seed in SEEDS} | {'calibrated_reference'}:
        raise ValueError('GAMER_G2_0032_PARENT_0031_POLICY_DRIFT')
    if report['gates']!=x31.promotion_gates(report['evaluation']['new_0031_dev'],
                                             report['by_split'][x31.NEW[1]]):
        raise ValueError('GAMER_G2_0032_PARENT_0031_GATE_DRIFT')
    sources={'train_fit_0031':cal31[:x31.TRAIN_CAL_FIT],
             'train_audit_0031':cal31[x31.TRAIN_CAL_FIT:],**old_dev,**dev31}
    found={k:[] for k in sources}
    with gzip.open(lp,'rt',encoding='utf-8') as reader:
        for line in reader:
            record=json.loads(line)
            split=record.get('group')
            if split not in found or len(found[split])>=len(sources[split]):
                raise ValueError('GAMER_G2_0032_PARENT_0031_ROW_COUNT')
            index=len(found[split])
            row=sources[split][index]
            if (record.get('index')!=index+1 or
                    record.get('prompt_sha256')!=base._sha(row['prompt']) or
                    record.get('gold_total')!=len(base.gold_binding_edges(row)) or
                    record.get('family')!=row['family']):
                raise ValueError('GAMER_G2_0032_PARENT_0031_ROW_IDENTITY')
            found[split].append(record)
    if any(len(found[k])!=len(v) for k,v in sources.items()):
        raise ValueError('GAMER_G2_0032_PARENT_0031_ROW_COUNT')
    for split in {**old_dev,**dev31}:
        if report['by_split'][split]!=p30.aggregate(found[split]):
            raise ValueError('GAMER_G2_0032_PARENT_0031_AGGREGATE:'+split)
    for name,splits in (('new_0031_dev',x31.NEW),('replayed_0030_dev',x31.REPLAY)):
        if report['evaluation'][name]!=p30.aggregate([r for s in splits for r in found[s]]):
            raise ValueError('GAMER_G2_0032_PARENT_0031_EVALUATION:'+name)
    tensors=load_file(str(wp),device='cpu')
    required={f'{arm}_s{s}' for arm in x31.ARMS for s in SEEDS}
    if {k.split('.',1)[0] for k in tensors}!=required or not all(
            torch.isfinite(t).all() for t in tensors.values()):
        raise ValueError('GAMER_G2_0032_PARENT_0031_TENSORS')
    return report,policies,found,tensors,{
        'g2_0031_report_sha256':backbone.sha256(rp),
        'g2_0031_ledger_sha256':backbone.sha256(lp),
        'g2_0031_policies_sha256':backbone.sha256(pp),
        'g2_0031_weights_sha256':backbone.sha256(wp)}


def load_parent_models(tensors,*,device='cpu'):
    """Strictly reconstruct every arm and seed, no partial checkpoint loads."""
    dims={tensors[f'occurrence_graph_s{s}.encoder.norm.weight'].numel() for s in SEEDS}
    if len(dims)!=1:raise ValueError('GAMER_G2_0032_PARENT_0031_INPUT_WIDTH')
    width=dims.pop()
    classes={'role_span_biaffine':x31.RoleSpanBiaffine,
             'cross_occurrence_attention':x31.CrossOccurrenceAttention,
             'occurrence_graph':x31.OccurrenceGraph}
    used=set();models={}
    for seed in SEEDS:
        models[seed]={}
        for arm,cls in classes.items():
            prefix=f'{arm}_s{seed}.'
            state={k[len(prefix):]:v for k,v in tensors.items() if k.startswith(prefix)}
            net=cls(width)
            if set(state)!=set(net.state_dict()) or any(state[k].shape!=t.shape
                        for k,t in net.state_dict().items()):
                raise ValueError('GAMER_G2_0032_PARENT_0031_WEIGHT_SHAPE:'+prefix)
            net.load_state_dict(state,strict=True)
            models[seed][arm]=net.to(device).eval().requires_grad_(False)
            used.update(prefix+k for k in state)
    if used!=set(tensors):raise ValueError('GAMER_G2_0032_PARENT_0031_UNUSED_WEIGHTS')
    return models


def fresh_cohorts(state,old_cal,old_dev,cal31,dev31):
    prior=[*state['training'],*state['history29'],
           *(r for batch in state['fresh29'].values() for r in batch),
           *old_cal,*(r for batch in old_dev.values() for r in batch),
           *cal31,*(r for batch in dev31.values() for r in batch)]
    # Parent histories intentionally overlap; preserve all unique prompts while
    # treating every earlier TRAIN/DEV prompt as excluded for this experiment.
    prior=list({base._sha(r['prompt']):r for r in prior}.values())
    train=general16.curriculum_rows((prior,),limit=TRAIN_FIT+CAL_FIT+CAL_AUDIT,seed=SEED)
    random.Random(SEED+3).shuffle(train)
    excluded=[*prior,*train]
    independent=general16.independent_rows((excluded,),limit=320,seed=SEED+100)
    topology=general16.heldout_topology_rows((excluded,independent),limit=128,seed=SEED+101)
    allrows=[*excluded,*independent,*topology]
    if (len(train)!=TRAIN_FIT+CAL_FIT+CAL_AUDIT or len(independent)!=320 or len(topology)!=128 or
            len({base._sha(r['prompt']) for r in allrows})!=len(allrows) or
            any(r.get('split')!='train' for r in train) or
            any(r.get('split')!='dev' for r in (*independent,*topology)) or
            {r['structural_topology'] for r in topology}!={'2x4','4x2','4x4'} or
            any(r.get('structural_topology') in {'2x4','4x2','4x4'} for r in train)):
        raise ValueError('GAMER_G2_0032_COHORT_LEAKAGE')
    return (train[:TRAIN_FIT],train[TRAIN_FIT:TRAIN_FIT+CAL_FIT],train[TRAIN_FIT+CAL_FIT:]),\
            dict(zip(NEW,(independent,topology)))


def _sigmoid(scores):
    return 1/(1+np.exp(-np.clip(np.asarray(scores,dtype=np.float64),-30,30)))


def candidate_features(item,source,policy,n_nodes):
    """Inference features are strictly scores, label-blind groups and graph size.

    Neither the gold set, labels, prompt, nor authored graph enters this function.
    """
    raw=item['raw']
    if source not in raw:raise ValueError('GAMER_G2_0032_UNKNOWN_SOURCE')
    ref=np.asarray(raw[x31.REFERENCE],dtype=np.float64)
    alt=np.asarray(raw[source],dtype=np.float64)
    scores=ref+policy['alpha']*(alt-ref)
    if len(scores)!=len(item['edges']) or not np.isfinite(scores).all():
        raise ValueError('GAMER_G2_0032_INFERENCE_SCORE_SHAPE')
    chosen=p30.select_graph(item,**policy)
    buckets=defaultdict(list)
    for i,g in enumerate(item['groups']):buckets[int(g)].append(i)
    feats=[];groups=[];indices=[];base_counts=[]
    global_f=[np.log1p(len(buckets))/6,np.log1p(n_nodes)/6,
              np.log1p(len(scores))/10,np.log1p(len(chosen))/6]
    for key,ids in sorted(buckets.items()):
        order=sorted(ids,key=lambda i:(-float(scores[i]),item['edges'][i]))
        sc=_sigmoid(scores[order]);sr=_sigmoid(ref[order]);
        selected=sum(item['edges'][i] in chosen for i in order)
        first=list(sc[:5])+[0.]*max(0,5-len(sc))
        firstref=list(sr[:3])+[0.]*max(0,3-len(sr))
        gaps=[float(np.clip(scores[order[0]]-scores[order[j]],0,20)/10)
              if len(order)>j else 2. for j in (1,2,3)]
        # 20 dimensional, all from predicted model outputs and candidate counts
        f=[*first,*firstref,*gaps,float(sc.mean()),float(sc.std()),
           np.log1p(len(order))/5,np.log1p(selected)/3,
           float(np.clip(scores[order[0]],-10,10)/10),*global_f]
        assert len(f)==20
        feats.append(f);groups.append(key);indices.append(order)
        base_counts.append(selected)
    return np.asarray(feats,dtype='float32'),groups,indices,base_counts,chosen,scores


def graph_features(group_features,base_counts,indices,scores):
    # 20 group-pooled features + 4 global graph quantities
    f=np.asarray(group_features,dtype=np.float32)
    top=f.mean(axis=0)
    return np.concatenate((top,np.asarray([
        float(np.log1p(len(f))/6),float(np.log1p(sum(base_counts))/6),
        float(np.mean([len(x) for x in indices])/24),
        float(_sigmoid(scores).mean()),],dtype=np.float32)))


class GroupCardinality(nn.Module):
    def __init__(self):
        super().__init__()
        self.net=nn.Sequential(nn.Linear(20,48),nn.GELU(),nn.LayerNorm(48),
                               nn.Linear(48,COUNT_CLASSES))
    def forward(self,x):return self.net(x)


class GraphCardinality(nn.Module):
    def __init__(self):
        super().__init__()
        self.net=nn.Sequential(nn.Linear(24,48),nn.GELU(),nn.LayerNorm(48),nn.Linear(48,1))
    def forward(self,x):return self.net(x).squeeze(-1)


def supervised_case(item,source,policy,n_nodes):
    features,groups,ids,counts,chosen,scores=candidate_features(item,source,policy,n_nodes)
    # Only TRAIN target generation may examine gold, never a prediction function.
    targets=[min(COUNT_CLASSES-1,sum(item['edges'][i] in item['gold'] for i in order))
             for order in ids]
    full_count=sum(item['edges'][i] in item['gold'] for order in ids for i in order)
    return dict(features=features,graph=graph_features(features,counts,ids,scores),
                targets=np.asarray(targets,dtype=np.int64),
                graph_count=float(full_count),num_groups=len(groups))


def fit_heads(data,*,seeds=SEEDS,steps=TRAIN_STEPS,device='cpu'):
    if len(data)<8 or not 1<=steps<=TRAIN_STEPS:
        raise ValueError('GAMER_G2_0032_TRAIN_PROTOCOL')
    models={};losses={}
    for seed in seeds:
        torch.manual_seed(seed+1000)
        group=GroupCardinality().to(device)
        graph=GraphCardinality().to(device)
        optimizer=torch.optim.AdamW([*group.parameters(),*graph.parameters()],lr=LR)
        rng=random.Random(seed+1001)
        for step in range(1,steps+1):
            examples=[data[i] for i in rng.sample(range(len(data)),min(12,len(data)))]
            gf=torch.cat([torch.as_tensor(ex['features'],device=device) for ex in examples]);
            targets=torch.cat([torch.as_tensor(ex['targets'],device=device) for ex in examples]);
            graph_inputs=torch.as_tensor(np.stack([ex['graph'] for ex in examples]),device=device)
            graph_targets=torch.tensor([math.log1p(ex['graph_count']) for ex in examples],
                                       dtype=torch.float32,device=device)
            # Weighted class loss limits domination by absent relationships.
            class_freq=torch.bincount(targets,minlength=COUNT_CLASSES).float().clamp(min=1)
            weight=(class_freq.sum()/class_freq).sqrt().clamp(max=4.)
            loss_group=F.cross_entropy(group(gf),targets,weight=weight)
            loss_graph=F.smooth_l1_loss(graph(graph_inputs),graph_targets)
            loss=loss_group+0.30*loss_graph
            if not torch.isfinite(loss):raise ValueError('GAMER_G2_0032_NONFINITE_LOSS')
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_([*group.parameters(),*graph.parameters()],2.)
            optimizer.step()
            if step==1 or step%80==0 or step==steps:
                print(f'GAMER_G2_0032_TRAIN {seed} {step}/{steps} '
                      f'group={float(loss_group):.5f} graph={float(loss_graph):.5f}',flush=True)
        group.eval().requires_grad_(False);graph.eval().requires_grad_(False)
        models[seed]=(group,graph)
        losses[str(seed)]={'group':float(loss_group.detach()),
                           'graph':float(loss_graph.detach())}
    return models,losses


def probabilities(item,source,policy,n_nodes,models):
    feats,groups,ids,base_counts,selected,scores=candidate_features(item,source,policy,n_nodes)
    gf=graph_features(feats,base_counts,ids,scores)
    group,graph=models
    device=next(group.parameters(),torch.empty(0)).device
    with torch.inference_mode():
        class_p=group(torch.as_tensor(feats,device=device)).softmax(-1).cpu().numpy()
        count=float(np.expm1(graph(torch.as_tensor(gf[None,:],device=device)).item()))
    if not np.isfinite(class_p).all() or not math.isfinite(count):
        raise ValueError('GAMER_G2_0032_NONFINITE_CARDINALITY')
    return (groups,ids,base_counts,selected,scores,class_p,max(0.,count))


def select_complete(item,source,policy,n_nodes,nets,recovery):
    """Label-blind selection; never removes an existing parent relationship."""
    prediction=probabilities(item,source,policy,n_nodes,nets)
    return _select_with_prediction(item,policy,prediction,recovery)


def fit_policies(cases,models):
    """Select on calibration-fit TRAIN only, with an explicit micro-F1 floor."""
    policies={};audits={}
    for seed in SEEDS:
        name=f'occurrence_graph_s{seed}'
        baseline=Counter()
        for item,n_nodes in cases:
            baseline.update(p30.measure(p30.select_graph(item,**item['parent_policies'][name]),item))
        baseline_f1=p30._totals_f1(baseline)
        records=[]
        for item,n_nodes in cases:
            policy=item['parent_policies'][name]
            # Prediction is precomputed once per graph; calibration never refits a head.
            pred=probabilities(item,name,policy,n_nodes,models[seed])
            records.append((item,n_nodes,policy,pred))
        disabled={'enabled':False,'mode':'disabled','edge_prob':1.,'confidence':1.,
                  'group_gap':0.,'max_additions':0,'offset':0.}
        for arm in ARMS:
            best=(baseline['exact_cases'],baseline_f1,disabled)
            # The union is fixed independently of calibration outcomes.
            for cutoff in MIN_EDGE_PROBS:
                for conf in CONFIDENCES if arm!='graph_recovery' else (0.,):
                    for budget in MAX_ADDITIONS:
                        for gap in GROUP_GAPS:
                            for offset in GRAPH_OFFSETS if arm!='group_recovery' else (0.,):
                                proposal={'enabled':True,'mode':arm,'edge_prob':cutoff,
                                    'confidence':conf,'group_gap':gap,
                                    'max_additions':budget,'offset':offset}
                                total=Counter()
                                for item,n_nodes,policy,pred in records:
                                    edges=_select_with_prediction(item,policy,pred,proposal)
                                    total.update(p30.measure(edges,item))
                                f1=p30._totals_f1(total)
                                # Do not trade away the very F1 advantage we achieved.
                                if f1<baseline_f1-F1_SLACK:continue
                                quality=(total['exact_cases'],f1)
                                if quality>(best[0],best[1]):
                                    best=(quality[0],quality[1],proposal)
            policies[f'{arm}_s{seed}']=dict(best[2],source=name)
            audits[f'{arm}_s{seed}']={'training_exact_cases':best[0],
                'training_f1':best[1],'parent_training_f1':baseline_f1,
                'parent_training_exact_cases':baseline['exact_cases']}
            print(f'GAMER_G2_0032_SELECT {arm}_s{seed} exact={best[0]} '
                  f'f1={best[1]:.5f} enabled={best[2]["enabled"]}',flush=True)
    return policies,audits


def _select_with_prediction(item,parent_policy,pred,rec):
    groups,ids,base_counts,selected,scores,probs,pred_count=pred
    if not rec['enabled']:return selected
    edge_cutoff=math.log(rec['edge_prob']/(1-rec['edge_prob']))
    proposals=[]
    for j,order in enumerate(ids):
        n_present=base_counts[j]
        votes=float(probs[j,min(n_present+1,COUNT_CLASSES):].sum())
        if rec['mode']!='graph_recovery' and votes<rec['confidence']:continue
        top=float(scores[order[0]])
        rank=[i for i in order if item['edges'][i] not in selected and
              scores[i]>=edge_cutoff and top-scores[i]<=rec['group_gap']]
        if rec['mode']!='graph_recovery':
            rank=rank[:max(0,int(np.argmax(probs[j]))-n_present)]
        for i in rank:proposals.append((float(scores[i]),float(votes),item['edges'][i]))
    desired=(max(0,int(round(pred_count+rec['offset']))-len(selected))
             if rec['mode']!='group_recovery' else rec['max_additions'])
    budget=min(desired,rec['max_additions'],max(0,source13.MAX_EDGES-len(selected)))
    if budget:
        proposals.sort(key=lambda triple:(-triple[0],-triple[1],triple[2]))
        selected=set(selected)
        selected.update(edge for _,_,edge in proposals[:budget])
    return selected


def evaluate_case(item,n_nodes,row,split,index,parent_policies,policy32,nets,
                  *,prior=None):
    metrics={}
    for name,source in (('identity_peer_0026',x31.REFERENCE),
                        ('calibrated_reference_0030','calibrated_reference_0030'),
                        ('calibrated_reference_0031','calibrated_reference_0031'),
                        ('predicate_argument_0030','predicate_argument_0030'),
                        *[(f'occurrence_graph_s{s}',f'occurrence_graph_s{s}') for s in SEEDS]):
        if name=='identity_peer_0026':
            policy={'source':x31.REFERENCE,'alpha':0.,'threshold':m29.THRESHOLD,
                    'margin':None,'reference_gate':None}
        else:policy=parent_policies[name]
        selected=p30.select_graph(item,**policy)
        result=p30.measure(selected,item)
        scores=(item['raw'][x31.REFERENCE] if policy['source']==x31.REFERENCE else
                item['raw'][x31.REFERENCE]+policy['alpha']*(
                    item['raw'][policy['source']]-item['raw'][x31.REFERENCE]))
        competing,failures=p30.ranking_counts(item,scores)
        metrics[name]={**result,'competing_gold':competing,'ranking_failures':failures}
    for key,policy in policy32.items():
        source=policy['source'];seed=int(source.rsplit('s',1)[-1])
        pred=probabilities(item,source,parent_policies[source],n_nodes,nets[seed])
        edges=_select_with_prediction(item,parent_policies[source],pred,policy)
        parent=metrics[source]
        metrics[key]={**p30.measure(edges,item),
                      'competing_gold':parent['competing_gold'],
                      'ranking_failures':parent['ranking_failures']}
        if not p30.select_graph(item,**parent_policies[source]).issubset(edges):
            raise ValueError('GAMER_G2_0032_DELETED_PARENT_EDGE')
    if prior is not None:
        for key,oldkey in (('identity_peer_0026','identity_peer_0026'),
                           ('calibrated_reference_0030','calibrated_reference_0030'),
                           ('calibrated_reference_0031','calibrated_reference'),
                           ('predicate_argument_0030','predicate_argument_0030'),
                           *((s,s) for s in SOURCE_NAMES)):
            for metric in ('correct','false_positive','missing','competing_gold','ranking_failures'):
                if metrics[key][metric]!=prior['metrics'][oldkey][metric]:
                    raise ValueError('GAMER_G2_0032_PARENT_0031_REPLAY_DRIFT:'+key+':'+metric)
    return {'group':split,'index':index,'prompt_sha256':base._sha(row['prompt']),
            'family':row['family'],'density_bucket':density._bucket(n_nodes),
            'structural_topology':row.get('structural_topology','native'),
            'gold_total':item['gold_total'],'candidate_gold':item['candidate_gold'],
            'candidate_count':item['candidate_count'],'metrics':metrics}


def gates(fresh,ood):
    f=fresh['models'];o=ood['models']
    best_control_exact=max(f['identity_peer_0026']['exact_graph_rate'],
                           f['calibrated_reference_0030']['exact_graph_rate'],
                           f['calibrated_reference_0031']['exact_graph_rate'],
                           f['predicate_argument_0030']['exact_graph_rate'])
    checks={}
    for arm in ARMS:
        entries=[]
        for seed in SEEDS:
            name=f'{arm}_s{seed}';parent=f'occurrence_graph_s{seed}'
            actual=f[name];base=f[parent];ood_actual=o[name];ood_base=o[parent]
            pass_=(actual['f1']>=base['f1']-F1_SLACK and
                actual['f1']>=max(f[c]['f1'] for c in (
                    'identity_peer_0026','calibrated_reference_0030',
                    'calibrated_reference_0031','predicate_argument_0030'))+REQUIRED_GAIN and
                actual['exact_graph_rate']>=best_control_exact and
                actual['exact_graph_rate']>=base['exact_graph_rate'] and
                actual['ranking_failures']<=base['ranking_failures'] and
                ood_actual['f1']>=ood_base['f1']-F1_SLACK and
                ood_actual['ranking_failures']<=ood_base['ranking_failures'])
            entries.append({'seed':seed,'pass':bool(pass_),
                'f1_delta_to_occurrence_graph':actual['f1']-base['f1'],
                'exact_graph_delta_to_original_control':actual['exact_graph_rate']-best_control_exact,
                'exact_graph_delta_to_occurrence_graph':actual['exact_graph_rate']-base['exact_graph_rate'],
                'ood_f1_delta_to_occurrence_graph':ood_actual['f1']-ood_base['f1']})
        checks[arm]={'all_seeds_pass':all(r['pass'] for r in entries),'seeds':entries}
    return {'status':'DEVELOPMENT_ONLY','f1_tolerance':F1_SLACK,
        'required_f1_gain_over_best_prior_control':REQUIRED_GAIN,
        'no_ranking_regression':True,'no_withheld_topology_regression':True,
        'exact_graph_at_least_original_control':True,'all_three_seeds_required':True,
        'arms':checks,'development_success':any(v['all_seeds_pass'] for v in checks.values()),
        'production_promotion':False,'certification_authorized':False}


def _write_ledger(path,rows):
    with open(path,'wb') as raw:
        with gzip.GzipFile(fileobj=raw,mode='wb',filename='',mtime=0) as zipped:
            for row in rows:
                zipped.write((json.dumps(row,sort_keys=True,allow_nan=False,separators=(',',':'))+'\n').encode())


def run(*,precision30_run,representation31_run,output,device='cuda',
        max_length=MAX_LENGTH,steps=TRAIN_STEPS,**kwargs):
    if steps!=TRAIN_STEPS or max_length!=MAX_LENGTH:
        raise ValueError('GAMER_G2_0032_FIXED_PROTOCOL')
    if Path(output).exists():raise FileExistsError('GAMER_G2_0032_IMMUTABLE_OUTPUT')
    state=p30._prepare(output=output,device=device,max_length=max_length,**kwargs)
    old_cal,dev30=p30.calibration_and_dev(state['history29'],state['fresh29'])
    # Parent 0030 verifies both its replayed 0029 cohorts and its own DEV.
    # Parent 0031, however, records ONLY the two 0030 DEV cohorts as replay;
    # including 0029 cohorts in its expected ledger would wrongly reject it.
    report30,pol30,_,h30=x31.verify_parent30(precision30_run,state['ancestors'],
                        _sha_rows(state['training']),old_cal,{**state['fresh29'],**dev30})
    cal31,dev31=x31.fresh_cohorts(state,old_cal,dev30)
    parents={**state['ancestors'],**h30}
    report31,pol31,parent_rows,tensors31,h31=verify_parent31(representation31_run,
        old_cal=old_cal,old_dev=dev30,cal31=cal31,dev31=dev31,
        ancestors=parents,training_sha=_sha_rows(state['training']))
    models31=load_parent_models(tensors31,device=device)
    parent_policies={
        'calibrated_reference_0030':pol30['calibrated_reference'],
        'calibrated_reference_0031':pol31['calibrated_reference'],
        'predicate_argument_0030':pol30[x31.CHAMPION],
        **{name:pol31[name] for name in SOURCE_NAMES}}
    (train,fit,audit),fresh=fresh_cohorts(state,old_cal,dev30,cal31,dev31)
    def capture(rows,split,*,prior=None):
        cases=[]
        for start in range(0,len(rows),20):
            batch=rows[start:start+20]
            predictions=x31.capture_occurrences(state['head'],state['model'],
                state['tokenizer'],batch,device=device,max_length=max_length,group=split)
            for offset,(row,case) in enumerate(zip(batch,predictions),start+1):
                ex=x31.example_from_case(case,row,state,train=False,device=device)
                item=x31.score_case(ex,state,models31)
                if split in parent_rows:
                    frozen=x31.evaluate_case(item,pol31,pol30,row,split,offset,
                                             len(case['anchors']))
                    expected=parent_rows[split][offset-1]
                    if frozen['metrics']!=expected['metrics'] or frozen['prompt_sha256']!=expected['prompt_sha256']:
                        raise ValueError('GAMER_G2_0032_PARENT_0031_LIVE_REPLAY_DRIFT:'+split+':'+str(offset))
                item['parent_policies']=parent_policies
                cases.append((item,len(case['anchors']),row,offset))
            print(f'GAMER_G2_0032_CAPTURE {split} '
                  f'{min(start+20,len(rows))}/{len(rows)}',flush=True)
        return cases
    train_raw=capture(train,'train_completeness_0032')
    training={}
    for seed in SEEDS:
        source=f'occurrence_graph_s{seed}'
        training[seed]=[supervised_case(item,source,parent_policies[source],n)
                        for item,n,_,_ in train_raw]
    nets={};losses={}
    for seed in SEEDS:
        model,log=fit_heads(training[seed],seeds=(seed,),steps=steps,device=device)
        nets.update(model);losses.update(log)
    del train_raw,training
    fit_raw=capture(fit,'train_calibration_fit_0032')
    policy32,training_fit=fit_policies([(item,n) for item,n,_,_ in fit_raw],nets)
    audit_raw=capture(audit,'train_calibration_audit_0032')
    def score_entries(raw,split,prior=None):
        return [evaluate_case(item,n,row,split,i,parent_policies,policy32,nets,
             prior=prior[i-1] if prior is not None else None)
             for item,n,row,i in raw]
    fit_records=score_entries(fit_raw,'train_calibration_fit_0032')
    audit_records=score_entries(audit_raw,'train_calibration_audit_0032')
    del fit_raw,audit_raw
    evaluated={}
    # Parent replay is for drift detection only. It cannot alter policies.
    for split,rows in {**dev31,**fresh}.items():
        evaluated[split]=score_entries(capture(rows,split),split,
                             prior=parent_rows.get(split))
    x31.rel.validate_original_state(state['head'],state['head_before'])
    if any(not torch.equal(v,state['reference'].state_dict()[k].detach().cpu())
           for k,v in state['reference_before'].items()):
        raise ValueError('GAMER_G2_0032_FROZEN_PARENT_MUTATION')
    if any(p.requires_grad for p in state['model'].parameters()) or any(
        p.requires_grad for bundle in models31.values() for net in bundle.values()
        for p in net.parameters()):
        raise ValueError('GAMER_G2_0032_FROZEN_UPSTREAM_MUTATION')
    new=[r for k in NEW for r in evaluated[k]]
    replay=[r for k in REPLAY for r in evaluated[k]]
    fresh_summary=p30.aggregate(new)
    ood_summary=p30.aggregate(evaluated[NEW[1]])
    decisions=gates(fresh_summary,ood_summary)
    report={'schema':SCHEMA,'status':'LEARNED_GRAPH_COMPLETENESS_DEV_NOT_CERTIFIED',
        'parent_hashes':{**parents,**h31},
        'train_dataset_sha256':_sha_rows(state['training']),
        'completeness_train_sha256':_sha_rows(train),
        'calibration_fit_sha256':_sha_rows(fit),
        'calibration_audit_sha256':_sha_rows(audit),
        'datasets_sha256':{k:_sha_rows(v) for k,v in {**dev31,**fresh}.items()},
        'predeclared':{'seeds':list(SEEDS),'steps':steps,'train_cases':len(train),
          'calibration_fit_cases':len(fit),'calibration_audit_cases':len(audit),
          'arms':list(ARMS),'graph_f1_slack':F1_SLACK,
          'exact_graph_min_control':'identity_peer_0026_or_better',
          'train_only_cardinality_supervision':True},
        'frozen_backbone':True,'frozen_g1':True,'frozen_g2_rank_head':True,
        'frozen_g2_0031_occurrence_graph':True,
        'learned_group_and_graph_cardinality':True,
        'train_only_policy_selection':True,'candidate_selection_uses_gold':False,
        'dev_labels_used_for_training':False,'dev_labels_used_for_calibration':False,
        'dev_checkpoint_selection':False,'sealed_test_used':False,
        'production_promotion':False,'g2_certified':False,
        'policies':policy32,'calibration_fit':p30.aggregate(fit_records),
        'calibration_audit':p30.aggregate(audit_records),
        'policy_training_fit':training_fit,'final_training_losses':losses,
        'evaluation':{'new_0032_dev':fresh_summary,'replayed_0031_dev':p30.aggregate(replay)},
        'by_split':{k:p30.aggregate(v) for k,v in evaluated.items()},
        'by_density':{k:{b:p30.aggregate([r for r in data if r['density_bucket']==b])
                         for b in sorted({r['density_bucket'] for r in data})}
            for k,data in {'new_0032_dev':new}.items()},
        'gates':decisions,
        'paired_intervals':{name:p30.paired_interval(new,name,
            baseline=f'occurrence_graph_s{seed}',seed=SEED+i)
            for i,(name,seed) in enumerate((
                (f'{arm}_s{s}',s) for arm in ARMS for s in SEEDS))},
        'limitations':{'frozen_Qwen_backbone_and_G1':True,
           'candidate_inventory_top24_unchanged':True,
           'graph_completeness_is_post_scoring':True,
           'deterministic_executable_not_replayed':True}}
    output=Path(output)
    output.mkdir(parents=True,exist_ok=False)
    _write_ledger(output/LEDGER,[*fit_records,*audit_records,*replay,*new])
    (output/POLICIES).write_text(json.dumps(policy32,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    weights={f'{kind}_s{seed}.{k}':v.detach().float().cpu().contiguous()
             for seed,(group,graph) in nets.items()
             for kind,net in (('group',group),('graph',graph))
             for k,v in net.state_dict().items()}
    save_file(weights,str(output/WEIGHTS))
    report['ledger_sha256']=backbone.sha256(output/LEDGER)
    report['policies_sha256']=backbone.sha256(output/POLICIES)
    report['development_weights_sha256']=backbone.sha256(output/WEIGHTS)
    (output/REPORT).write_text(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n',
                               encoding='utf-8')
    return report
