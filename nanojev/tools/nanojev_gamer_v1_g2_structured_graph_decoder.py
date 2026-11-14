"""G2-0033: TRAIN-supervised, structured complete-graph decoder.

Jointly selects per-source/relation edge subsets under a learned graph-count prior.
Can add OR remove edges. G2-0031, G2-0032 and earlier parents are frozen.
DEV cohorts are evaluated after all weights and policies are frozen.
"""
from __future__ import annotations

from collections import Counter
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

from tools import nanojev_gamer_v1_g2_graph_completeness as x32

x31=x32.x31
p30=x32.p30
base=x32.base
backbone=x32.backbone
density=x32.density
general16=x32.general16
source13=x32.source13

SCHEMA='nanojev.gamer.graph.g2.structured-graph-decoder.v1'
REPORT='g2_structured_graph_decoder_report.json'
LEDGER='g2_structured_graph_decoder_per_case.jsonl.gz'
WEIGHTS='g2_structured_graph_decoder_development.safetensors'
POLICIES='g2_structured_graph_decoder_frozen_policies.json'
SEEDS=x32.SEEDS
SEED=460333
TRAIN=300
CAL_FIT=120
CAL_AUDIT=60
STEPS=320
LR=4e-4
BATCH=3
EDGE_DIM=15
GROUP_DIM=20
HIDDEN=48
MAX_GROUP_EDGES=24
COUNT_CLASSES=MAX_GROUP_EDGES+1
MAX_LENGTH=2048
F1_SLACK=.005
F1_GAIN=.02
NEW=('new_independent_dev_0033','new_topology_dev_0033')
REPLAY=x32.NEW
POLICY_GRID=((0.,0.),(.7,0.),(1.4,0.),(.7,.30),(1.4,.30))


def _sha_rows(rows):
    return density.sha_rows(rows)


def verify_parent32(directory,*,train32,fit32,audit32,dev32,ancestors,training_sha):
    """Check unmodified parent lineage, hashes, row identities and aggregates."""
    folder=Path(directory)
    rp,lp,pp,wp=(folder/n for n in (x32.REPORT,x32.LEDGER,x32.POLICIES,x32.WEIGHTS))
    report=json.loads(rp.read_text(encoding='utf-8'))
    if report.get('schema')!=x32.SCHEMA:
        raise ValueError('GAMER_G2_0033_PARENT_0032_CONTRACT:schema')
    policies=json.loads(pp.read_text(encoding='utf-8'))
    expected={'schema':x32.SCHEMA,
        'status':'LEARNED_GRAPH_COMPLETENESS_DEV_NOT_CERTIFIED',
        'parent_hashes':ancestors,'train_dataset_sha256':training_sha,
        'completeness_train_sha256':_sha_rows(train32),
        'calibration_fit_sha256':_sha_rows(fit32),
        'calibration_audit_sha256':_sha_rows(audit32),
        'datasets_sha256':{k:_sha_rows(v) for k,v in dev32.items()},
        'frozen_backbone':True,'frozen_g1':True,'frozen_g2_rank_head':True,
        'frozen_g2_0031_occurrence_graph':True,
        'learned_group_and_graph_cardinality':True,
        'train_only_policy_selection':True,
        'candidate_selection_uses_gold':False,'dev_labels_used_for_training':False,
        'dev_labels_used_for_calibration':False,'dev_checkpoint_selection':False,
        'sealed_test_used':False,'production_promotion':False,'g2_certified':False}
    for k,v in expected.items():
        if report.get(k)!=v:
            raise ValueError('GAMER_G2_0033_PARENT_0032_CONTRACT:'+k)
    for key,file in (('ledger_sha256',lp),('policies_sha256',pp),
                     ('development_weights_sha256',wp)):
        if report.get(key)!=backbone.sha256(file):
            raise ValueError('GAMER_G2_0033_PARENT_0032_HASH:'+key)
    if policies!=report['policies'] or set(policies)!={
            f'{arm}_s{s}' for arm in x32.ARMS for s in SEEDS}:
        raise ValueError('GAMER_G2_0033_PARENT_0032_POLICIES')
    if report['gates']!=x32.gates(report['evaluation']['new_0032_dev'],
                                 report['by_split'][x32.NEW[1]]):
        raise ValueError('GAMER_G2_0033_PARENT_0032_GATES')
    cohorts={'train_calibration_fit_0032':fit32,
             'train_calibration_audit_0032':audit32,**dev32}
    found={k:[] for k in cohorts}
    with gzip.open(lp,'rt',encoding='utf-8') as stream:
        for line in stream:
            row=json.loads(line);split=row.get('group')
            if split not in found or len(found[split])>=len(cohorts[split]):
                raise ValueError('GAMER_G2_0033_PARENT_0032_ROW_COUNT')
            ordinal=len(found[split]);source=cohorts[split][ordinal]
            if (row.get('index')!=ordinal+1 or
                    row.get('prompt_sha256')!=base._sha(source['prompt']) or
                    row.get('family')!=source['family'] or
                    row.get('gold_total')!=len(base.gold_binding_edges(source))):
                raise ValueError('GAMER_G2_0033_PARENT_0032_ROW_IDENTITY')
            found[split].append(row)
    if any(len(found[k])!=len(v) for k,v in cohorts.items()):
        raise ValueError('GAMER_G2_0033_PARENT_0032_ROW_COUNT')
    for split in dev32:
        if report['by_split'][split]!=p30.aggregate(found[split]):
            raise ValueError('GAMER_G2_0033_PARENT_0032_AGGREGATE:'+split)
    for name,splits in (('new_0032_dev',x32.NEW),('replayed_0031_dev',x32.REPLAY)):
        if report['evaluation'][name]!=p30.aggregate([r for k in splits for r in found[k]]):
            raise ValueError('GAMER_G2_0033_PARENT_0032_EVALUATION:'+name)
    tensors=load_file(str(wp),device='cpu')
    expected_keys=set()
    for seed in SEEDS:
        for kind,net in (('group',x32.GroupCardinality()),
                         ('graph',x32.GraphCardinality())):
            expected_keys.update(f'{kind}_s{seed}.{k}' for k in net.state_dict())
            for k,v in net.state_dict().items():
                name=f'{kind}_s{seed}.{k}'
                if name not in tensors or tensors[name].shape!=v.shape or not torch.isfinite(tensors[name]).all():
                    raise ValueError('GAMER_G2_0033_PARENT_0032_WEIGHTS:'+name)
    if set(tensors)!=expected_keys:
        raise ValueError('GAMER_G2_0033_PARENT_0032_EXTRA_WEIGHTS')
    hashes={'g2_0032_report_sha256':backbone.sha256(rp),
            'g2_0032_ledger_sha256':backbone.sha256(lp),
            'g2_0032_policies_sha256':backbone.sha256(pp),
            'g2_0032_weights_sha256':backbone.sha256(wp)}
    return report,policies,found,tensors,hashes


def load_parent32_models(tensors,device='cpu'):
    result={}
    for seed in SEEDS:
        pair=[]
        for kind,net in (('group',x32.GroupCardinality()),
                         ('graph',x32.GraphCardinality())):
            prefix=f'{kind}_s{seed}.'
            weights={k[len(prefix):]:v for k,v in tensors.items() if k.startswith(prefix)}
            net.load_state_dict(weights,strict=True)
            pair.append(net.to(device).eval().requires_grad_(False))
        result[seed]=tuple(pair)
    return result


def fresh_cohorts(state,old_cal,dev30,cal31,dev31,train32,fit32,audit32,dev32):
    prior=[*state['training'],*state['history29'],
           *(r for group in state['fresh29'].values() for r in group),
           *old_cal,*(r for group in dev30.values() for r in group),
           *cal31,*(r for group in dev31.values() for r in group),
           *train32,*fit32,*audit32,
           *(r for group in dev32.values() for r in group)]
    prior=list({base._sha(r['prompt']):r for r in prior}.values())
    train=general16.curriculum_rows((prior,),limit=TRAIN+CAL_FIT+CAL_AUDIT,seed=SEED)
    random.Random(SEED+3).shuffle(train)
    excluded=[*prior,*train]
    independent=general16.independent_rows((excluded,),limit=320,seed=SEED+100)
    topology=general16.heldout_topology_rows((excluded,independent),limit=128,seed=SEED+101)
    combined=[*excluded,*independent,*topology]
    if (len(train)!=TRAIN+CAL_FIT+CAL_AUDIT or len(independent)!=320 or
            len(topology)!=128 or
            len({base._sha(r['prompt']) for r in combined})!=len(combined) or
            any(r.get('split')!='train' for r in train) or
            any(r.get('split')!='dev' for r in (*independent,*topology)) or
            {r['structural_topology'] for r in topology}!={'2x4','4x2','4x4'} or
            any(r.get('structural_topology') in {'2x4','4x2','4x4'} for r in train)):
        raise ValueError('GAMER_G2_0033_COHORT_LEAKAGE')
    return ((train[:TRAIN],train[TRAIN:TRAIN+CAL_FIT],train[TRAIN+CAL_FIT:]),
            dict(zip(NEW,(independent,topology))))


def set_features(item,source,policy,n_nodes):
    """Label-blind candidate and group features; no gold, prompt or author graph."""
    group_features,groups,ordered,counts,chosen,scores=x32.candidate_features(
        item,source,policy,n_nodes)
    if len(groups)==0 or len(scores)==0:
        raise ValueError('GAMER_G2_0033_EMPTY_INVENTORY')
    refs=np.asarray(item['raw'][x31.REFERENCE],dtype='float64')
    alt=np.asarray(item['raw'][source],dtype='float64')
    edges=np.zeros((len(scores),EDGE_DIM),dtype=np.float32)
    indices=np.zeros(len(scores),dtype=np.int64)
    for j,ranked in enumerate(ordered):
        if len(ranked)>MAX_GROUP_EDGES:
            raise ValueError('GAMER_G2_0033_GROUP_EXCEEDS_DECLARED_CAPACITY')
        local=np.asarray(scores[ranked],dtype=np.float64)
        for rank,i in enumerate(ranked):
            indices[i]=j
            f=[float(np.clip(refs[i],-20,20)/10),
               float(np.clip(alt[i],-20,20)/10),
               float(np.clip(scores[i],-20,20)/10),
               float(x32._sigmoid([refs[i]])[0]),
               float(x32._sigmoid([alt[i]])[0]),
               float(item['edges'][i] in chosen),
               rank/max(1,len(ranked)-1),len(ranked)/MAX_GROUP_EDGES,
               float(np.clip(local[0]-scores[i],0,30)/10),
               float(np.clip(local[0],-20,20)/10),
               float(x32._sigmoid(local).mean()),
               counts[j]/MAX_GROUP_EDGES,n_nodes/128.,
               min(1.,len(scores)/2048.),min(1.,len(chosen)/2048.)]
            edges[i]=f
    if not np.isfinite(edges).all() or not np.isfinite(group_features).all():
        raise ValueError('GAMER_G2_0033_NONFINITE_FEATURES')
    return {'edges':edges,'groups':group_features,'index':indices,
            'chosen':chosen,'source':source}


class GraphSetEnergy(nn.Module):
    """Permutation-equivariant group/graph interactions and set-cardinality energy."""
    def __init__(self):
        super().__init__()
        self.edge_embed=nn.Sequential(nn.Linear(EDGE_DIM,HIDDEN),nn.GELU(),nn.LayerNorm(HIDDEN))
        self.group_embed=nn.Linear(GROUP_DIM,HIDDEN)
        self.group_norm=nn.LayerNorm(HIDDEN)
        self.edge_update=nn.Sequential(nn.Linear(3*HIDDEN,HIDDEN),nn.GELU(),
                                       nn.LayerNorm(HIDDEN))
        self.edge_readout=nn.Linear(HIDDEN,1)
        self.group_readout=nn.Sequential(nn.Linear(2*HIDDEN,HIDDEN),nn.GELU(),
                                         nn.Linear(HIDDEN,COUNT_CLASSES))
        self.graph_readout=nn.Sequential(nn.Linear(HIDDEN,HIDDEN),nn.GELU(),
                                         nn.Linear(HIDDEN,1))
        nn.init.zeros_(self.edge_readout.weight)
        nn.init.zeros_(self.edge_readout.bias)

    def forward(self,edges,groups,index):
        if (edges.ndim!=2 or edges.shape[-1]!=EDGE_DIM or
                groups.ndim!=2 or groups.shape[-1]!=GROUP_DIM or
                index.ndim!=1 or len(index)!=len(edges) or
                not len(edges) or not len(groups)):
            raise ValueError('GAMER_G2_0033_INPUT_SHAPE')
        e=self.edge_embed(edges)
        pooled=torch.zeros((len(groups),HIDDEN),dtype=e.dtype,device=e.device)
        pooled=pooled.index_add(0,index,e)
        size=torch.bincount(index,minlength=len(groups)).clamp(min=1)
        pooled=pooled/size[:,None]
        g=self.group_norm(self.group_embed(groups)+pooled)
        global_g=g.mean(dim=0)
        h=self.edge_update(torch.cat((e,g[index],global_g.expand_as(e)),-1))
        # Pre-existing scorer is a frozen prior, not a learned G2 node vector.
        edge_logits=edges[:,1]*10-math.log(.93/.07)+self.edge_readout(h).squeeze(-1)
        count_logits=self.group_readout(torch.cat((g,global_g.expand_as(g)),-1))
        total_count_log=self.graph_readout(global_g).squeeze(-1)
        return edge_logits,count_logits,total_count_log


def _tensor_features(features,device):
    return (torch.as_tensor(features['edges'],device=device),
            torch.as_tensor(features['groups'],device=device),
            torch.as_tensor(features['index'],device=device,dtype=torch.long))


def supervised(item,source,policy,n_nodes):
    features=set_features(item,source,policy,n_nodes)
    labels=np.asarray([e in item['gold'] for e in item['edges']],dtype=np.float32)
    return features,labels


def set_objective(outputs,features,labels):
    """Edge F1, per-group set cardinality and exact graph energy supervision.

    Gold is read ONLY here on TRAIN cases; no gold is passed into the scorer.
    """
    logits,counts,graph_count=outputs
    idx=features['index']
    target=torch.as_tensor(labels,dtype=logits.dtype,device=logits.device)
    G=counts.shape[0]
    group_true=torch.zeros(G,device=logits.device).index_add(0,idx,target).long()
    if bool((group_true>MAX_GROUP_EDGES).any()):
        raise ValueError('GAMER_G2_0033_UNREPRESENTABLE_TRAIN_GRAPH')
    pos=float(target.sum().item());n=float(len(target))
    pos_weight=torch.tensor(min(3.,math.sqrt(max(1.,n-pos)/max(1.,pos))),device=logits.device)
    bce=F.binary_cross_entropy_with_logits(logits,target,pos_weight=pos_weight)
    p=torch.sigmoid(logits)
    soft_f1=1-(2*(p*target).sum()+1.)/(p.sum()+target.sum()+1.)
    group_loss=F.cross_entropy(counts,group_true)
    graph_loss=F.smooth_l1_loss(graph_count,torch.log1p(target.sum()))
    # The exact candidate graph competes against the frozen parent set and the
    # current independently chosen graph; large Hamming-distance errors cost more.
    gold_energy=(logits*target).sum()+counts.gather(1,group_true[:,None]).sum()
    def alt_energy(choice):
        n_groups=torch.zeros(G,device=logits.device).index_add(0,idx,choice).long()
        n_groups=n_groups.clamp(max=MAX_GROUP_EDGES)
        return (logits*choice).sum()+counts.gather(1,n_groups[:,None]).sum()
    parent=torch.as_tensor(features['edges'][:,5],device=logits.device,dtype=logits.dtype)
    greedy=(logits.detach()>0).to(logits.dtype)
    hinge=[]
    for alt in (parent,greedy):
        distance=(alt-target).abs().sum().detach()
        if distance.item():
            hinge.append(F.relu(.5*distance+alt_energy(alt)-gold_energy)/distance)
    structured=torch.stack(hinge).mean() if hinge else logits.sum()*0
    loss=bce+.55*group_loss+.22*graph_loss+.18*soft_f1+.45*structured
    if not torch.isfinite(loss):raise ValueError('GAMER_G2_0033_NONFINITE_TRAIN_LOSS')
    return loss,{'edge':float(bce.detach()),'group':float(group_loss.detach()),
                 'graph':float(graph_loss.detach()),'exact_set_margin':float(structured.detach())}


def fit_models(training,*,seeds=SEEDS,steps=STEPS,device='cpu'):
    if len(training)<8 or not 1<=steps<=STEPS:
        raise ValueError('GAMER_G2_0033_TRAIN_PROTOCOL')
    nets={};losses={}
    for seed in seeds:
        torch.manual_seed(seed+371)
        net=GraphSetEnergy().to(device)
        optimizer=torch.optim.AdamW(net.parameters(),lr=LR,weight_decay=1e-4)
        rng=random.Random(seed+108)
        for step in range(1,steps+1):
            net.train();optimizer.zero_grad(set_to_none=True)
            sampled=rng.sample(range(len(training)),min(BATCH,len(training)))
            losses_step=[]
            for i in sampled:
                f,labels=training[i]
                logits=net(*_tensor_features(f,device))
                loss,last=set_objective(logits,
                    {'index':_tensor_features(f,device)[2],'edges':f['edges']},labels)
                losses_step.append(loss)
            loss=torch.stack(losses_step).mean()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(net.parameters(),2.)
            optimizer.step()
            if step==1 or step%80==0 or step==steps:
                print(f'GAMER_G2_0033_TRAIN {seed} {step}/{steps} '
                      f'edge={last["edge"]:.5f} group={last["group"]:.5f} '
                      f'set={last["exact_set_margin"]:.5f}',flush=True)
        net.eval().requires_grad_(False)
        nets[seed]=net;losses[str(seed)]=last
    return nets,losses


def predictions(features,net):
    device=next(net.parameters()).device
    with torch.inference_mode():
        edge,counts,total=net(*_tensor_features(features,device))
        result=(edge.detach().cpu().double().numpy(),
                F.log_softmax(counts,-1).detach().cpu().double().numpy(),
                float(torch.expm1(total.detach()).item()))
    if not all(np.isfinite(v).all() for v in result[:2]) or not math.isfinite(result[2]):
        raise ValueError('GAMER_G2_0033_NONFINITE_INFERENCE')
    return result


def decode(item,features,pred,policy):
    """Joint dynamic-programming decode across all source/relation groups.

    A group's options are top-k selected candidates for k=0..24. A global
    count potential couples choices across groups. This can add AND remove
    frozen parent edges; it is never given gold labels or any source text.
    """
    if not policy['enabled']:
        raise ValueError('GAMER_G2_0033_DISABLED_DECODER_USE_WRAPPER')
    logits,logcounts,pred_total=pred
    if len(logits)!=len(item['edges']) or len(logcounts)!=len(features['groups']):
        raise ValueError('GAMER_G2_0033_DECODE_SHAPE')
    groups=features['index'];n_groups=len(features['groups'])
    limit=min(source13.MAX_EDGES,len(item['edges']))
    # Exact Viterbi dynamic program over total graph cardinality, implemented
    # using contiguous NumPy slices. No Python tuple-path beam/truncation bias.
    dp=np.full(limit+1,-np.inf,dtype=np.float64)
    dp[0]=0.
    back=[];sorted_groups=[]
    for g in range(n_groups):
        ids=np.flatnonzero(groups==g).tolist()
        ids.sort(key=lambda i:(-float(logits[i]),item['edges'][i]))
        if not ids or len(ids)>MAX_GROUP_EDGES:
            raise ValueError('GAMER_G2_0033_BAD_GROUP_CAPACITY')
        sorted_groups.append(ids)
        utilities=np.r_[0.,np.cumsum(logits[ids])]+policy['group_weight']*logcounts[g,:len(ids)+1]
        next_dp=np.full(limit+1,-np.inf,dtype=np.float64)
        chosen_k=np.zeros(limit+1,dtype=np.uint8)
        for k,value in enumerate(utilities):
            alternative=dp[:limit+1-k]+float(value)
            improve=alternative>next_dp[k:]
            next_dp[k:][improve]=alternative[improve]
            chosen_k[k:][improve]=k
        dp=next_dp;back.append(chosen_k)
    target=max(0.,min(source13.MAX_EDGES,pred_total))
    cardinality=np.arange(limit+1,dtype=np.float64)
    energy=dp-policy['global_weight']*(np.log1p(cardinality)-math.log1p(target))**2
    total=int(np.argmax(energy))  # lowest total wins an exact energy tie
    selected=set()
    for g in range(n_groups-1,-1,-1):
        count=int(back[g][total]);total-=count
        selected.update(item['edges'][i] for i in sorted_groups[g][:count])
    if total!=0:raise ValueError('GAMER_G2_0033_DECODE_BACKTRACK')
    return selected


def fit_policies(cases,nets,parent_policies,policy32,nets32):
    """TRAIN calibration only; maximize exact graphs subject to edge-F1 floor."""
    result={};audit={}
    for seed in SEEDS:
        source=f'occurrence_graph_s{seed}'
        controls={}
        for arm in x32.ARMS:
            name=f'{arm}_s{seed}'
            rows=[]
            for item,n in cases:
                edges=x32.select_complete(item,source,parent_policies[source],n,
                                         nets32[seed],policy32[name])
                rows.append(p30.measure(edges,item))
            total=Counter()
            for record in rows:total.update(record)
            controls[name]=total
        # A previous policy remains the fallback if the new trained decoder
        # cannot preserve at least the TRAIN F1 of the strongest prior policy.
        eligible=max(controls,key=lambda n:(controls[n]['exact_cases'],p30._totals_f1(controls[n])))
        floor=max(p30._totals_f1(c) for c in controls.values())-F1_SLACK
        prepared=[]
        for item,n in cases:
            features=set_features(item,source,parent_policies[source],n)
            prepared.append((item,features,predictions(features,nets[seed])))
        best=None
        for weight,global_weight in POLICY_GRID:
            cfg={'enabled':True,'source':source,'group_weight':weight,
                 'global_weight':global_weight,'fallback':eligible}
            total=Counter()
            for item,features,pred in prepared:
                total.update(p30.measure(decode(item,features,pred,cfg),item))
            f1=p30._totals_f1(total)
            if f1<floor:continue
            quality=(total['exact_cases'],f1)
            if best is None or quality>best[0]:best=(quality,cfg)
        if best is None:
            cfg={'enabled':False,'source':source,'group_weight':0.,
                 'global_weight':0.,'fallback':eligible}
        else:cfg=best[1]
        result[f'structured_set_s{seed}']=cfg
        audit[str(seed)]={'eligible_floor':floor,'selected_enabled':cfg['enabled'],
                          'selected_fallback':eligible,
                          'selected_train_exact':best[0][0] if best else None,
                          'selected_train_f1':best[0][1] if best else None,
                          'parent_train_scores':{key:{'exact':v['exact_cases'],
                            'f1':p30._totals_f1(v)} for key,v in controls.items()}}
        print(f'GAMER_G2_0033_SELECT {seed} enabled={cfg["enabled"]} '
              f'calibration_exact={audit[str(seed)]["selected_train_exact"]} '
              f'calibration_f1={audit[str(seed)]["selected_train_f1"]}',flush=True)
    return result,audit


def select_structured(item,n,net,parent_policy,config,policy32,nets32):
    source=config['source']
    if not config['enabled']:
        return x32.select_complete(item,source,parent_policy,n,nets32,
                                   policy32[config['fallback']]),None
    feats=set_features(item,source,parent_policy,n)
    pred=predictions(feats,net)
    return decode(item,feats,pred,config),pred[0]


def evaluate_case(item,n,row,split,index,parent_policies,policy32,nets32,
                  nets33,policy33,*,prior32=None,prior31=None):
    # Parent 0032 computes and freezes ALL earlier controls for fair comparison.
    record=x32.evaluate_case(item,n,row,split,index,parent_policies,policy32,nets32,
                             prior=prior31)
    if prior32 is not None:
        previous=prior32['metrics']
        if record['metrics']!=previous or record['prompt_sha256']!=prior32['prompt_sha256']:
            raise ValueError('GAMER_G2_0033_PARENT_0032_LIVE_REPLAY_DRIFT:'+split+':'+str(index))
    for seed in SEEDS:
        source=f'occurrence_graph_s{seed}'
        name=f'structured_set_s{seed}'
        edges,new_logits=select_structured(item,n,nets33[seed],parent_policies[source],
                                           policy33[name],policy32,nets32[seed])
        if new_logits is None:
            ranking=record['metrics'][source]['ranking_failures']
            competing=record['metrics'][source]['competing_gold']
        else:
            competing,ranking=p30.ranking_counts(item,new_logits)
        record['metrics'][name]={**p30.measure(edges,item),
                                  'competing_gold':competing,
                                  'ranking_failures':ranking}
    return record


def development_diagnostics(item,n,parent_policies,policy32,nets32,nets33,policy33):
    """Offline DEV-only error evidence, computed strictly AFTER prediction.

    Gold labels annotate logged errors; no diagnostic is returned to inference,
    calibration or the trainable scorer. No authored prompt text is serialized.
    """
    result={}
    all_edges=set(item['edges'])
    for seed in SEEDS:
        source=f'occurrence_graph_s{seed}'
        parent=p30.select_graph(item,**parent_policies[source])
        previous=x32.select_complete(item,source,parent_policies[source],n,
            nets32[seed],policy32[f'group_recovery_s{seed}'])
        name=f'structured_set_s{seed}'
        selected,scores=select_structured(item,n,nets33[seed],
            parent_policies[source],policy33[name],policy32,nets32[seed])
        flagged=(selected^parent)|(selected^previous)|(item['gold']^selected)
        flagged &= all_edges
        records=[]
        for i,edge in enumerate(item['edges']):
            if edge not in flagged:continue
            records.append({'edge':edge,'group':int(item['groups'][i]),
                'reference_score':float(item['raw'][x31.REFERENCE][i]),
                'occurrence_score':float(item['raw'][source][i]),
                'structured_score':float(scores[i]) if scores is not None else None,
                'parent_selected':edge in parent,
                'group_recovery_selected':edge in previous,
                'structured_selected':edge in selected,
                'gold':edge in item['gold']})
        unreachable=sorted(item['gold']-all_edges)
        result[name]={'changed_from_parent':len(selected^parent),
            'changed_from_group_recovery':len(selected^previous),
            'missing_candidate_gold':len(unreachable),
            'unreachable_gold_edges':unreachable,
            'flagged_candidate_edges':records}
    return result


def gates(fresh,ood,policies):
    models=fresh['models'];out=ood['models'];results=[]
    control_exact=models['identity_peer_0026']['exact_graph_rate']
    for seed in SEEDS:
        name=f'structured_set_s{seed}';parent=f'occurrence_graph_s{seed}'
        old=[f'{arm}_s{seed}' for arm in x32.ARMS]
        prior_f1=max(models[k]['f1'] for k in old)
        prior_exact=max(models[k]['exact_graph_rate'] for k in old)
        prior_ood=max(out[k]['f1'] for k in old)
        val=models[name];passed=bool(
            policies[name]['enabled'] and
            val['f1']>=prior_f1-F1_SLACK and
            val['f1']>=max(models[k]['f1'] for k in (
                'identity_peer_0026','calibrated_reference_0030',
                'calibrated_reference_0031','predicate_argument_0030'))+F1_GAIN and
            val['exact_graph_rate']>=max(control_exact,prior_exact) and
            val['ranking_failures']<=models[parent]['ranking_failures'] and
            out[name]['f1']>=prior_ood-F1_SLACK and
            out[name]['ranking_failures']<=out[parent]['ranking_failures'])
        results.append({'seed':seed,'pass':passed,'enabled':policies[name]['enabled'],
           'f1_gain_over_best_0032':val['f1']-prior_f1,
           'exact_rate_delta_to_original':val['exact_graph_rate']-control_exact,
           'exact_rate_delta_to_best_0032':val['exact_graph_rate']-prior_exact,
           'ood_f1_gain_over_best_0032':out[name]['f1']-prior_ood})
    return {'status':'DEVELOPMENT_ONLY','all_seeds_required':True,
        'exact_graph_at_least_original_and_best_0032':True,
        'graph_f1_no_regression_slack':F1_SLACK,'required_f1_gain_over_prior_controls':F1_GAIN,
        'no_ranking_regression':True,'no_withheld_topology_regression':True,
        'seeds':results,'development_success':all(v['pass'] for v in results),
        'production_promotion':False,'certification_authorized':False}


def run(*,precision30_run,representation31_run,completeness32_run,output,
        device='cuda',max_length=MAX_LENGTH,steps=STEPS,**kwargs):
    if max_length!=MAX_LENGTH or steps!=STEPS:
        raise ValueError('GAMER_G2_0033_FIXED_PROTOCOL')
    if Path(output).exists():raise FileExistsError('GAMER_G2_0033_IMMUTABLE_OUTPUT')
    state=p30._prepare(output=output,device=device,max_length=max_length,**kwargs)
    cal30,dev30=p30.calibration_and_dev(state['history29'],state['fresh29'])
    report30,pol30,_,hashes30=x31.verify_parent30(precision30_run,state['ancestors'],
                   _sha_rows(state['training']),cal30,{**state['fresh29'],**dev30})
    cal31,dev31=x31.fresh_cohorts(state,cal30,dev30)
    ancestry31={**state['ancestors'],**hashes30}
    report31,pol31,prior31,tensors31,hashes31=x32.verify_parent31(
        representation31_run,old_cal=cal30,old_dev=dev30,cal31=cal31,
        dev31=dev31,ancestors=ancestry31,training_sha=_sha_rows(state['training']))
    (train32,fit32,audit32),dev32=x32.fresh_cohorts(state,cal30,dev30,cal31,dev31)
    ancestry32={**ancestry31,**hashes31}
    report32,policy32,prior32,tensors32,hashes32=verify_parent32(
        completeness32_run,train32=train32,fit32=fit32,audit32=audit32,
        dev32={**dev31,**dev32},ancestors=ancestry32,
        training_sha=_sha_rows(state['training']))
    nets31=x32.load_parent_models(tensors31,device=device)
    nets32=load_parent32_models(tensors32,device=device)
    parent_policies={'calibrated_reference_0030':pol30['calibrated_reference'],
                     'calibrated_reference_0031':pol31['calibrated_reference'],
                     'predicate_argument_0030':pol30[x31.CHAMPION],
                     **{k:pol31[k] for k in x32.SOURCE_NAMES}}
    (train,fit,audit),fresh=fresh_cohorts(state,cal30,dev30,cal31,dev31,
                                            train32,fit32,audit32,dev32)
    def capture(rows,split):
        cases=[]
        for start in range(0,len(rows),20):
            batch=rows[start:start+20]
            predictions31=x31.capture_occurrences(state['head'],state['model'],
                state['tokenizer'],batch,device=device,max_length=max_length,group=split)
            for idx,(row,case) in enumerate(zip(batch,predictions31),start+1):
                ex=x31.example_from_case(case,row,state,train=False,device=device)
                item=x31.score_case(ex,state,nets31)
                item['parent_policies']=parent_policies
                cases.append((item,len(case['anchors']),row,idx))
            print(f'GAMER_G2_0033_CAPTURE {split} '
                  f'{min(start+20,len(rows))}/{len(rows)}',flush=True)
        return cases
    train_raw=capture(train,'train_structured_set_0033')
    nets33={};training_log={}
    for seed in SEEDS:
        source=f'occurrence_graph_s{seed}'
        examples=[supervised(item,source,parent_policies[source],n)
                  for item,n,_,_ in train_raw]
        fitted,losses=fit_models(examples,seeds=(seed,),steps=steps,device=device)
        nets33.update(fitted);training_log.update(losses)
    del train_raw
    fit_raw=capture(fit,'train_calibration_fit_0033')
    policy33,policy_audit=fit_policies([(item,n) for item,n,_,_ in fit_raw],
                                      nets33,parent_policies,policy32,nets32)
    def score_entries(records,split):
        result=[]
        for item,n,row,idx in records:
            entry=evaluate_case(item,n,row,split,idx,parent_policies,policy32,nets32,
                nets33,policy33,prior32=prior32.get(split,[None]*len(records))[idx-1],
                prior31=prior31.get(split,[None]*len(records))[idx-1])
            if split in NEW:
                entry['edge_diagnostics']=development_diagnostics(item,n,parent_policies,
                                    policy32,nets32,nets33,policy33)
            result.append(entry)
        return result
    fit_records=score_entries(fit_raw,'train_calibration_fit_0033');del fit_raw
    audit_records=score_entries(capture(audit,'train_calibration_audit_0033'),
                                  'train_calibration_audit_0033')
    evaluated={}
    for split,rows in {**dev32,**fresh}.items():
        evaluated[split]=score_entries(capture(rows,split),split)
    x31.rel.validate_original_state(state['head'],state['head_before'])
    if any(not torch.equal(v,state['reference'].state_dict()[k].detach().cpu())
           for k,v in state['reference_before'].items()):
        raise ValueError('GAMER_G2_0033_FROZEN_PARENT_MUTATION')
    if any(p.requires_grad for p in state['model'].parameters()) or any(
            p.requires_grad for bundle in nets31.values() for net in bundle.values()
            for p in net.parameters()) or any(
            p.requires_grad for pair in nets32.values() for net in pair
            for p in net.parameters()):
        raise ValueError('GAMER_G2_0033_UPSTREAM_NOT_FROZEN')
    new=[r for split in NEW for r in evaluated[split]]
    replay=[r for split in REPLAY for r in evaluated[split]]
    fresh_summary=p30.aggregate(new)
    ood_summary=p30.aggregate(evaluated[NEW[1]])
    decisions=gates(fresh_summary,ood_summary,policy33)
    report={'schema':SCHEMA,'status':'STRUCTURED_GRAPH_DECODER_DEV_NOT_CERTIFIED',
        'parent_hashes':{**ancestry32,**hashes32},
        'train_dataset_sha256':_sha_rows(state['training']),
        'set_train_sha256':_sha_rows(train),
        'calibration_fit_sha256':_sha_rows(fit),
        'calibration_audit_sha256':_sha_rows(audit),
        'datasets_sha256':{k:_sha_rows(v) for k,v in {**dev32,**fresh}.items()},
        'predeclared':{'seeds':list(SEEDS),'steps':steps,'train_cases':len(train),
            'calibration_fit_cases':len(fit),'calibration_audit_cases':len(audit),
            'grid':list(map(list,POLICY_GRID)),'global_graph_set_objective':True,
            'inference_can_add_and_remove_edges':True},
        'frozen_backbone':True,'frozen_g1':True,'frozen_g2_rank_head':True,
        'frozen_g2_0031_occurrence_graph':True,'frozen_g2_0032_completeness':True,
        'train_only_set_supervision':True,'train_only_policy_selection':True,
        'candidate_selection_uses_gold':False,'dev_labels_used_for_training':False,
        'dev_labels_used_for_calibration':False,'dev_checkpoint_selection':False,
        'sealed_test_used':False,'production_promotion':False,'g2_certified':False,
        'development_edge_diagnostics_only':True,
        'policies':policy33,'policy_training_fit':policy_audit,
        'calibration_fit':p30.aggregate(fit_records),
        'calibration_audit':p30.aggregate(audit_records),
        'final_training_losses':training_log,
        'evaluation':{'new_0033_dev':fresh_summary,
                      'replayed_0032_dev':p30.aggregate(replay)},
        'by_split':{k:p30.aggregate(rows) for k,rows in evaluated.items()},
        'gates':decisions,
        'paired_intervals':{f'structured_set_s{s}':p30.paired_interval(
              new,f'structured_set_s{s}',baseline=f'group_recovery_s{s}',seed=SEED+i)
              for i,s in enumerate(SEEDS)},
        'limitations':{'candidate_inventory_top24_unchanged':True,
           'frozen_Qwen_backbone_and_G1':True,'no_joint_G1_G2_training':True,
           'evaluation_only_on_development':True}}
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    x32._write_ledger(output/LEDGER,[*fit_records,*audit_records,*replay,*new])
    (output/POLICIES).write_text(json.dumps(policy33,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    tensors={f'structured_set_s{seed}.{k}':v.detach().float().cpu().contiguous()
             for seed,net in nets33.items() for k,v in net.state_dict().items()}
    save_file(tensors,str(output/WEIGHTS))
    report['ledger_sha256']=backbone.sha256(output/LEDGER)
    report['policies_sha256']=backbone.sha256(output/POLICIES)
    report['development_weights_sha256']=backbone.sha256(output/WEIGHTS)
    (output/REPORT).write_text(json.dumps(report,sort_keys=True,indent=2,
                 allow_nan=False)+'\n',encoding='utf-8')
    return report
