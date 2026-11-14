"""G2-0029: three distinct graph-construction schemes, frozen-reference bake-off.

Only TRAIN supervision; DEV is used for *evaluation*, never model/threshold tuning.
All architectures operate on the same label-blind G1/G2 candidate inventory.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
import gzip
import hashlib
import json
import math
import random

import torch
from torch import nn
from torch.nn import functional as F
from safetensors.torch import save_file, load_file

from tools import nanojev_gamer_v1_g2_occurrence_identity_evidence_probe as parent28
from tools import nanojev_gamer_v1_g2_architectural_ceiling_diagnosis as ceiling17
from tools import nanojev_gamer_v1_g2_competitor_context_intervention as context18
from tools import nanojev_gamer_v1_g2_competition_aware_learning as parent22
from tools import nanojev_gamer_v1_g2_contextual_relationship_representation as parent23
from tools import nanojev_gamer_v1_g2_context_utilization_intervention as parent25
from tools import nanojev_gamer_v1_g2_relationship_evidence_sufficiency_audit as parent27
from tools import nanojev_gamer_v1_g2_competitor_identity_discrimination as parent26
from tools import nanojev_gamer_v1_g2_context_utilization_audit as audit24
from tools import nanojev_gamer_v1_g2_source_conditioned_discrimination as source13
from tools import nanojev_gamer_v1_g2_general_relational_discrimination as general16
from tools import nanojev_gamer_v1_g2_structural_ceiling_validation as ceiling20
from tools import nanojev_gamer_v1_g2_relationship_discrimination_ceiling as ceiling21
from tools import nanojev_gamer_v1_g2_density_discrimination as density
from tools import nanojev_gamer_v1_g2_relational_training as rel
from tools import nanojev_gamer_v1_g2_binding_selection as sel
from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools import nanojev_gamer_v1_g2_inference_realistic_training as train19

SCHEMA = 'nanojev.gamer.graph.g2.architecture-bakeoff.v1'
REPORT = 'g2_architecture_bakeoff_report.json'
LEDGER = 'g2_architecture_bakeoff_per_case.jsonl.gz'
WEIGHTS = 'g2_architecture_bakeoff_development.safetensors'
SEED = 460038
SEEDS = (460038, 460039, 460040)
STEPS = 480
LEARNING_RATE = 8e-5
THRESHOLD = parent26.THRESHOLD
LOGIT_CUTOFF = math.log(THRESHOLD / (1. - THRESHOLD))
TOP_K = 24
HIDDEN = 96
TRAIN_CASES_PER_STEP = 3
TRAIN_GROUPS_PER_CASE = 6
MAX_GROUP_COUNT = 6  # bin 6 includes six or more; not a hard output cap
ARMS = ('predicate_argument', 'dynamic_span_graph', 'joint_graph_decoder')
REFERENCE = 'identity_peer_0026'
REPLAY = parent28.NEW
NEW = ('new_independent_dev_0029', 'new_topology_dev_0029')
SPLITS = (*REPLAY, *NEW)


def _sha(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def fresh_cohorts(history, training):
    """Brand-new DEV, distinct from every historical TRAIN/DEV prompt."""
    independent = general16.independent_rows((history,), limit=320, seed=SEED)
    topology = general16.heldout_topology_rows((history, independent), limit=128, seed=SEED + 1)
    joined = [*history, *independent, *topology]
    if (len(independent) != 320 or len(topology) != 128 or
            len({_sha(r['prompt']) for r in joined}) != len(joined) or
            any(r.get('split') != 'dev' for r in (*independent, *topology)) or
            {r.get('structural_topology') for r in topology} != {'2x4', '4x2', '4x4'}):
        raise ValueError('GAMER_G2_0029_FRESH_COHORT_CONTRACT')
    return dict(zip(NEW, (independent, topology)))


def verify_parent28(directory, rows, ancestors, train_sha):
    """Check full saved parent ledger and weights before any new training."""
    p = Path(directory)
    rp, cp, wp = (p / parent28.REPORT, p / parent28.CASES, p / parent28.WEIGHTS)
    report = json.loads(rp.read_text(encoding='utf-8'))
    if report.get('schema') != parent28.SCHEMA:
        raise ValueError('GAMER_G2_0029_PARENT_0028_CONTRACT:schema')
    if set(rows) != set(parent28.SPLITS):
        raise ValueError('GAMER_G2_0029_PARENT_0028_CONTRACT:cohorts')
    required = {'schema':parent28.SCHEMA,
                'status':'G2_OCCURRENCE_PROBES_DIAGNOSTIC_TRAIN_ONLY_NOT_CERTIFIED',
                'train_dataset_sha256':train_sha,
                'datasets_sha256':{k:density.sha_rows(rows[k]) for k in parent28.SPLITS},
                'parent_hashes':ancestors,
                'frozen_backbone':True,'frozen_g1':True,
                'frozen_rank_head':True,'frozen_parent_weights':True,
                'candidate_selection_uses_gold':False,'dev_labels_used_for_training':False,
                'sealed_test_used':False,'production_promotion':False,
                'g2_certified':False}
    for key, value in required.items():
        if report.get(key) != value:
            raise ValueError('GAMER_G2_0029_PARENT_0028_CONTRACT:'+key)
    if (report.get('cases_sha256') != backbone.sha256(cp) or
            report.get('probe_weights_sha256') != backbone.sha256(wp)):
        raise ValueError('GAMER_G2_0029_PARENT_0028_HASH_DRIFT')
    predeclared = report.get('predeclared', {})
    if (predeclared.get('new_dev_seeds') != [parent28.SEED, parent28.SEED+1] or
            predeclared.get('steps') != parent28.STEPS or
            predeclared.get('arms') != list(parent28.ARMS)):
        raise ValueError('GAMER_G2_0029_PARENT_0028_PROTOCOL_DRIFT')
    found = {k:[] for k in parent28.SPLITS}
    with gzip.open(cp, 'rt', encoding='utf-8') as f:
        for line in f:
            entry = json.loads(line)
            group = entry.get('group')
            if group not in found or len(found[group]) >= len(rows[group]):
                raise ValueError('GAMER_G2_0029_PARENT_0028_UNEXPECTED_ROW')
            i = len(found[group]); source_row = rows[group][i]
            if (entry.get('index') != i+1 or
                    entry.get('prompt_sha256') != base._sha(source_row['prompt']) or
                    entry.get('family') != source_row['family'] or
                    entry.get('gold_total') != len(base.gold_binding_edges(source_row)) or
                    set(entry.get('probes', {})) != set((*parent28.ARMS, parent28.REFERENCE))):
                raise ValueError('GAMER_G2_0029_PARENT_0028_ROW_DRIFT')
            found[group].append(entry)
    if any(len(found[k]) != len(rows[k]) for k in parent28.SPLITS):
        raise ValueError('GAMER_G2_0029_PARENT_0028_COUNT_DRIFT')
    for split, records in found.items():
        if report['by_split'][split] != parent28.aggregate(records):
            raise ValueError('GAMER_G2_0029_PARENT_0028_AGGREGATE_DRIFT:'+split)
    # Validate the parent checkpoint is a genuine complete ensemble.
    tensors = load_file(str(wp), device='cpu')
    prefixes = set(k.split('.',1)[0] for k in tensors)
    if prefixes != set(parent28.ARMS) or any(not torch.isfinite(v).all() for v in tensors.values()):
        raise ValueError('GAMER_G2_0029_PARENT_0028_TENSOR_DRIFT')
    return report, found, {'g2_0028_report_sha256':backbone.sha256(rp),
                           'g2_0028_cases_sha256':backbone.sha256(cp),
                           'g2_0028_weights_sha256':backbone.sha256(wp)}


def case_inventory(case, row, head, reference, allowed, *, device='cpu', train=False, top_k=TOP_K):
    """Build identical, label-blind input to every architecture.

    Gold is consulted only *after* inventory construction, for TRAIN sampling
    or offline evaluation. No graph oracle can add candidates.
    """
    if case['prompt_sha256'] != base._sha(row['prompt']):
        raise ValueError('GAMER_G2_0029_CASE_IDENTITY')
    with torch.inference_mode():
        rank = rel.full_logits(head,case['vectors'].to(device)).detach().cpu()
        inv = source13.make_inventory(case, rank, allowed, top_k=top_k, train_gold=False)
        x = inv['features'].detach().cpu().float()
        core, position, structure = parent28.evidence_features(case,row['prompt'],inv,
                                                                 case['vectors'].shape[-1])
        ref = reference(x.to(device),inv['base'].to(device),inv['groups'],
                        with_competition=True,contextual=True).detach().cpu().float()
    if ref.shape != (len(inv['edges']),) or not torch.isfinite(ref).all():
        raise ValueError('GAMER_G2_0029_REFERENCE_SHAPE')
    # All arms use the same extra structural information: architecture, not
    # evidence availability, is the independent variable.
    features = torch.cat((core,position,structure),dim=-1).contiguous()
    anchor_index = {node:i for i,node in enumerate(case['anchors'])}
    src = torch.tensor([anchor_index[edge[0]] for edge in inv['edges']], dtype=torch.long)
    dst = torch.tensor([anchor_index[edge[1]] for edge in inv['edges']], dtype=torch.long)
    group = torch.tensor(inv['groups'], dtype=torch.long)
    gold = case['gold']
    # Inventory already excludes label-based candidates; supervision is only
    # used to choose balanced groups on TRAIN.
    indices = list(range(len(inv['edges'])))
    if train:
        by_group = defaultdict(list)
        for idx, key in enumerate(inv['groups']):by_group[key].append(idx)
        mixed = [key for key, ids in by_group.items()
                 if any(inv['edges'][i] in gold for i in ids) and
                    any(inv['edges'][i] not in gold for i in ids)]
        all_negative = [key for key, ids in by_group.items()
                        if not any(inv['edges'][i] in gold for i in ids)]
        rng = random.Random(SEED + case['index'])
        rng.shuffle(mixed);rng.shuffle(all_negative)
        # Include negative-only groups: otherwise the trained heads never
        # learn to suppress entire spurious relation/source groups.
        chosen = set(mixed[:TRAIN_GROUPS_PER_CASE-2] + all_negative[:2])
        indices = [i for i,key in enumerate(inv['groups']) if key in chosen]
    return {'features':features[indices], 'reference':ref[indices],
            'nodes':case['vectors'].detach().float().cpu(),
            'src':src[indices], 'dst':dst[indices], 'group':group[indices],
            'edges':[inv['edges'][i] for i in indices],
            'gold':gold, 'labels':torch.tensor([float(inv['edges'][i] in gold)
                                              for i in indices]),
            'all_inventory_edges':len(inv['edges']),
            'all_candidate_gold':sum(e in gold for e in inv['edges']),
            'prompt':row['prompt'],
            'frozen_width':int(case['vectors'].shape[-1])}


class PredicateArgument(nn.Module):
    """Explicit role-conditioned biaffine predicate/argument compatibility."""
    def __init__(self,width,dim,hidden=HIDDEN):
        super().__init__()
        self.width=width
        self.src=nn.Linear(width,hidden)
        self.dst=nn.Linear(width,hidden)
        self.relation=nn.Embedding(source13.REL_COUNT+1,hidden)
        self.biaffine=nn.Parameter(torch.randn(hidden,hidden)*0.01)
        self.aux=nn.Sequential(nn.Linear(20,hidden),nn.GELU())
        self.readout=nn.Linear(hidden*3+1,1)
        nn.init.zeros_(self.readout.weight);nn.init.zeros_(self.readout.bias)

    def forward(self,example):
        x=example['features'];ref=example['reference']
        if not len(x):return ref
        w=self.width
        relation_index=x[:,4*w:4*w+source13.REL_COUNT+1].argmax(-1)
        s=F.gelu(self.src(x[:,:w])+self.relation(relation_index))
        d=F.gelu(self.dst(x[:,w:2*w]))
        bilinear=(s @ self.biaffine * d).sum(-1,keepdim=True)/math.sqrt(len(s[0]))
        extra=self.aux(x[:,-20:])
        delta=self.readout(torch.cat((s,d,extra,bilinear),-1)).squeeze(-1)
        return ref+delta


class DynamicSpanGraph(nn.Module):
    """Two-round candidate-graph message passing between predicted spans."""
    def __init__(self,width,dim,hidden=HIDDEN):
        super().__init__()
        self.node=nn.Sequential(nn.Linear(width,hidden),nn.GELU())
        self.update=nn.ModuleList([nn.Linear(hidden*3,hidden) for _ in range(2)])
        self.norm=nn.ModuleList([nn.LayerNorm(hidden) for _ in range(2)])
        self.edge=nn.Sequential(nn.Linear(hidden*3+20,hidden),nn.GELU(),nn.Linear(hidden,1))
        nn.init.zeros_(self.edge[-1].weight);nn.init.zeros_(self.edge[-1].bias)

    def forward(self,example):
        src=example['src'];dst=example['dst'];ref=example['reference']
        if not len(src):return ref
        h=self.node(example['nodes']);n=len(h)
        # Graph topology is the label-blind candidate inventory. Avoid
        # probabilities here so messages cannot implicitly incorporate gold.
        for update,norm in zip(self.update,self.norm):
            incoming=torch.zeros_like(h).index_add(0,dst,h[src])
            outgoing=torch.zeros_like(h).index_add(0,src,h[dst])
            ni=torch.zeros(n,device=h.device).index_add(0,dst,torch.ones(len(dst),device=h.device))
            no=torch.zeros(n,device=h.device).index_add(0,src,torch.ones(len(src),device=h.device))
            joined=torch.cat((h,incoming/ni.clamp(min=1)[:,None],
                                 outgoing/no.clamp(min=1)[:,None]),-1)
            h=norm(h+F.gelu(update(joined)))
        hs=h[src];hd=h[dst]
        pair=torch.cat((hs,hd,hs*hd,example['features'][:,-20:]),-1)
        return ref+self.edge(pair).squeeze(-1)


class JointGraphDecoder(nn.Module):
    """Trainable graph-global destination factor + multi-positive set decoder.

    Inspired by joint structured IE, not an implementation of OneIE. The
    decoder searches variable-cardinality destination subsets per source/role;
    no hard-coded one-destination rule is applied.
    """
    def __init__(self,width,dim,hidden=HIDDEN):
        super().__init__()
        self.local=nn.Sequential(nn.Linear(dim,hidden),nn.GELU(),nn.Linear(hidden,1))
        nn.init.zeros_(self.local[-1].weight);nn.init.zeros_(self.local[-1].bias)
        self.global_weight=nn.Parameter(torch.zeros(()))
        self.cardinality=nn.Sequential(nn.Linear(6,hidden//2),nn.GELU(),
                                       nn.Linear(hidden//2,MAX_GROUP_COUNT+1))
        nn.init.zeros_(self.cardinality[-1].weight)
        nn.init.zeros_(self.cardinality[-1].bias)

    def scores(self,example):
        ref=example['reference'];n=len(ref)
        if not n:return ref
        local=ref+self.local(example['features']).squeeze(-1)
        dst=example['dst']; node_count=len(example['nodes'])
        prob=torch.sigmoid((ref-LOGIT_CUTOFF).detach())
        density=torch.zeros(node_count,device=ref.device).index_add(0,dst,prob)
        count=torch.zeros(node_count,device=ref.device).index_add(0,dst,torch.ones_like(prob))
        # Other source/role groups that point to the same occurrence.
        peers=(density[dst]-prob)/(count[dst]-1).clamp(min=1)
        return local+self.global_weight*(peers-0.5)

    def groups(self,example,scores):
        by_group=defaultdict(list)
        for i,key in enumerate(example['group'].tolist()):by_group[key].append(i)
        for key,ids in by_group.items():
            v=scores[ids];n=len(ids)
            st=example['features'][ids,-20:]
            feat=torch.stack((torch.tensor(math.log1p(n)/8,device=v.device),
                v.mean().detach()/8,v.max().detach()/8,
                torch.sigmoid(v-LOGIT_CUTOFF).mean().detach(),
                st[:,-10:].mean(), st[:,:10].mean()))
            yield ids, self.cardinality(feat), v

    def forward(self,example):return self.scores(example)

    def decode(self,example,scores):
        # Exact best top-k subset for each group given its cardinality factor.
        # Global destination evidence already affects scores across groups.
        selected=set()
        for ids,counts,v in self.groups(example,scores):
            ordered=sorted(range(len(ids)),key=lambda j:(-float(v[j]),j))
            margins=(v[ordered]-LOGIT_CUTOFF).detach()
            prefix=torch.cat((margins.new_zeros(1),margins.cumsum(0)))
            vals=[]
            for k in range(len(ids)+1):
                vals.append(float((prefix[k]+counts[min(k,MAX_GROUP_COUNT)]).detach()))
            chosen=max(range(len(vals)),key=lambda k:(vals[k],-k))
            selected.update(ids[ordered[j]] for j in range(chosen))
        # Mirror G2's graph cap; do not admit extra edges merely because
        # a different architectural branch performed decoding.
        if len(selected)>source13.MAX_EDGES:
            selected=set(sorted(selected,key=lambda i:(-float(scores[i]),i))[:source13.MAX_EDGES])
        return selected


def _mixed_groups(example):
    buckets=defaultdict(list)
    for i,g in enumerate(example['group'].tolist()):buckets[g].append(i)
    return [(ids,example['labels'][ids]) for ids in buckets.values()
            if 0<sum(example['labels'][ids])<len(ids)]


def ranking_objective(scores,example):
    """Equal BCE + multi-positive hard-negative loss for all neural arms."""
    if not _mixed_groups(example):
        raise ValueError('GAMER_G2_0029_UNMIXED_TRAIN')
    by_group=defaultdict(list)
    for i,key in enumerate(example['group'].tolist()):by_group[key].append(i)
    result=[]
    for ids in by_group.values():
        labels=example['labels'][ids]
        pos=scores[ids][labels>0.5]-LOGIT_CUTOFF
        neg=scores[ids][labels<0.5]-LOGIT_CUTOFF
        if len(pos) and len(neg):
            bce=(F.binary_cross_entropy_with_logits(pos,torch.ones_like(pos))+
                 F.binary_cross_entropy_with_logits(neg,torch.zeros_like(neg)))/2
            rank=F.softplus(.35-(pos[:,None]-neg[None,:])).mean()
            result.append(bce+.5*rank)
        elif len(neg):
            result.append(F.binary_cross_entropy_with_logits(neg,torch.zeros_like(neg)))
        elif len(pos):
            result.append(F.binary_cross_entropy_with_logits(pos,torch.ones_like(pos)))
    return torch.stack(result).mean()


def joint_cardinality_loss(model,example,scores):
    result=[]
    for ids,logits,_ in model.groups(example,scores):
        # TRAIN labels only; multiple correct destinations remain possible.
        count=min(int(example['labels'][ids].sum()),MAX_GROUP_COUNT)
        result.append(F.cross_entropy(logits[None,:],torch.tensor([count],device=scores.device)))
    return .15*torch.stack(result).mean() if result else scores.sum()*0


def as_device(example,device):
    return {k:(value.to(device) if isinstance(value,torch.Tensor) else value)
            for k,value in example.items()}


def fit_architectures(training,width,dim,*,device='cpu',steps=STEPS,seeds=SEEDS):
    """Same TRAIN examples, order, optimizer steps and loss for each seed."""
    if steps!=STEPS and steps>10:raise ValueError('GAMER_G2_0029_FIXED_STEPS')
    if len(training)<8:raise ValueError('GAMER_G2_0029_INSUFFICIENT_TRAIN')
    rng_check={}
    models={};training_log={}
    for seed in seeds:
        torch.manual_seed(seed)
        nets={'predicate_argument':PredicateArgument(width,dim),
              'dynamic_span_graph':DynamicSpanGraph(width,dim),
              'joint_graph_decoder':JointGraphDecoder(width,dim)}
        nets={k:v.to(device).train() for k,v in nets.items()}
        optimizers={k:torch.optim.AdamW(v.parameters(),lr=LEARNING_RATE,weight_decay=1e-4)
                    for k,v in nets.items()}
        sampler=random.Random(seed+65537);done={}
        for step in range(1,steps+1):
            indices=sampler.sample(range(len(training)),min(TRAIN_CASES_PER_STEP,len(training)))
            batch=[as_device(training[i],device) for i in indices]
            for name,net in nets.items():
                optimizers[name].zero_grad(set_to_none=True)
                losses=[]
                for item in batch:
                    score=net(item)
                    objective=ranking_objective(score,item)
                    if name=='joint_graph_decoder':
                        objective=objective+joint_cardinality_loss(net,item,score)
                    losses.append(objective)
                loss=torch.stack(losses).mean()
                if not torch.isfinite(loss):raise ValueError('GAMER_G2_0029_NONFINITE_TRAIN')
                loss.backward()
                torch.nn.utils.clip_grad_norm_(net.parameters(),2.)
                optimizers[name].step();done[name]=float(loss.detach())
            if step in (1,steps) or step%80==0:
                print('GAMER_G2_0029_TRAIN '+str(seed)+' '+str(step)+'/'+str(steps)+' '+
                      ' '.join(f'{a}={done[a]:.5f}' for a in ARMS),flush=True)
        for net in nets.values():net.eval().requires_grad_(False)
        models[seed]=nets;training_log[str(seed)]=done
        rng_check[str(seed)]=True
    return models,training_log


def decode_independent(example,scores):
    # The frozen G2-0026 baseline uses this exact threshold and graph cap.
    fake={'edges':example['edges'],'groups':example['group'].tolist(),
          'null_argmax':[False]*len(example['edges'])}
    return source13.decode_scores(fake,scores,THRESHOLD)


def analyze_case(case,row,ex,models,*,split,index,prior=None):
    if prior is not None and (prior.get('index')!=index or prior.get('group')!=split or
            prior.get('prompt_sha256')!=case['prompt_sha256'] or
            prior.get('candidate_count')!=ex['all_inventory_edges'] or
            prior.get('candidate_gold')!=ex['all_candidate_gold']):
        raise ValueError('GAMER_G2_0029_PARENT_0028_REPLAY_DRIFT')
    if len(ex['edges'])!=ex['all_inventory_edges']:
        raise ValueError('GAMER_G2_0029_EVAL_PRUNED')
    raw={REFERENCE:ex['reference'].cpu()}
    decoded={REFERENCE:decode_independent(ex,raw[REFERENCE])}
    for seed,nets in models.items():
        for name,net in nets.items():
            with torch.inference_mode():
                item=as_device(ex,next(net.parameters()).device)
                score=net(item).detach().cpu()
            k=f'{name}_s{seed}';raw[k]=score
            if name=='joint_graph_decoder':
                with torch.inference_mode():
                    ids=net.decode(item,score.to(item['reference'].device))
                decoded[k]={ex['edges'][i] for i in ids}
            else:decoded[k]=decode_independent(ex,score)
    ref=case['gold'];counts={};subgroups=defaultdict(lambda:defaultdict(Counter))
    buckets=defaultdict(list)
    for i,g in enumerate(ex['group'].tolist()):buckets[g].append(i)
    w=ex['frozen_width']
    norm=F.normalize(ex['features'][:,:4*w],dim=-1)
    for key,score in raw.items():
        predicted=decoded[key]
        counts[key]=dict(correct=len(predicted&ref),missing=len(ref-predicted),
                         false_positive=len(predicted-ref),exact_cases=int(predicted==ref),
                         competing_gold=0,
                         ranking_failures=0)
    for ids in buckets.values():
        pos=[i for i in ids if ex['edges'][i] in ref]
        neg=[i for i in ids if ex['edges'][i] not in ref]
        if not pos or not neg:continue
        for i in pos:
            dst=ex['edges'][i][1]
            surface=ex['prompt'][dst[0]:dst[1]].strip().casefold()
            shared=any(ex['prompt'][ex['edges'][j][1][0]:ex['edges'][j][1][1]].strip().casefold()==surface
                       for j in neg)
            close=bool((norm[neg]@norm[i]).max()>=.99)
            for name,score in raw.items():
                fail=int(float(score[i])<=float(score[neg].max()))
                counts[name]['competing_gold']+=1
                counts[name]['ranking_failures']+=fail
                for category in ('same_surface' if shared else 'different_surface',
                                 'high_pair_cosine' if close else 'lower_pair_cosine'):
                    subgroups[name][category]['count']+=1
                    subgroups[name][category]['failures']+=fail
    if prior is not None:
        expected=prior['probes'][REFERENCE]
        for field in ('correct','false_positive','missing','competing_gold','ranking_failures'):
            if counts[REFERENCE][field]!=expected[field]:
                raise ValueError('GAMER_G2_0029_FROZEN_REFERENCE_REPLAY_DRIFT:'+field)
    return {'group':split,'index':index,'prompt_sha256':case['prompt_sha256'],
            'family':row['family'],'structural_topology':row.get('structural_topology','native'),
            'density_bucket':density._bucket(len(case['anchors'])),
            'gold_total':len(ref),'candidate_gold':ex['all_candidate_gold'],
            'candidate_count':ex['all_inventory_edges'],'metrics':counts,
            'subgroups':{k:{g:dict(cnt) for g,cnt in groups.items()}
                         for k,groups in subgroups.items()}}


def aggregate(rows):
    if not rows:raise ValueError('GAMER_G2_0029_EMPTY_AGGREGATE')
    names=sorted(rows[0]['metrics'])
    totals={a:Counter() for a in names};sub={a:defaultdict(Counter) for a in names}
    for row in rows:
        if set(row['metrics'])!=set(names):raise ValueError('GAMER_G2_0029_METRIC_KEYS')
        for name in names:
            totals[name].update(row['metrics'][name])
            for tag,value in row['subgroups'].get(name,{}).items():sub[name][tag].update(value)
    results={}
    for name,c in totals.items():
        tp,fp,fn=c['correct'],c['false_positive'],c['missing']
        results[name]={'correct':tp,'false_positive':fp,'missing':fn,
                       'f1':2*tp/max(1,2*tp+fp+fn),'exact_cases':c['exact_cases'],
                       'exact_graph_rate':c['exact_cases']/len(rows),
                       'precision':tp/max(1,tp+fp),
                       'recall':tp/max(1,tp+fn),
                       'ranking_failures':c['ranking_failures'],
                       'competing_gold':c['competing_gold'],
                       'ranking_failure_rate':c['ranking_failures']/max(1,c['competing_gold']),
                       'subgroups':{tag:{'count':t['count'],'failures':t['failures']}
                                    for tag,t in sub[name].items()}}
    n=sum(r['candidate_gold'] for r in rows);g=sum(r['gold_total'] for r in rows)
    return {'cases':len(rows),'gold_total':g,'candidate_gold':n,
            'offline_deterministic_candidate_oracle_ceiling_f1':2*n/max(1,n+g),
            'models':results}


def paired_ci(rows,arm,baseline=REFERENCE,draws=500,seed=SEED):
    rng=random.Random(seed);differences=[];rank=[]
    for _ in range(draws):
        sample=[rows[rng.randrange(len(rows))] for _ in rows]
        results=aggregate(sample)['models']
        differences.append(results[arm]['f1']-results[baseline]['f1'])
        rank.append(results[arm]['ranking_failure_rate']-results[baseline]['ranking_failure_rate'])
    differences.sort();rank.sort()
    at=lambda x:[x[int(.025*(draws-1))],x[int(.975*(draws-1))]]
    return {'f1_delta_CI95':at(differences),'ranking_failure_rate_delta_CI95':at(rank),
            'draws':draws,'method':'paired_case_bootstrap'}


def promotion_gates(new,withheld,seeds=SEEDS):
    """Predeclared, non-promoting development gates. No seed cherry-picking."""
    base=new['models'][REFERENCE]
    per_arm={}
    for arm in ARMS:
        tests=[]
        for seed in seeds:
            key=f'{arm}_s{seed}';m=new['models'][key];o=withheld['models'][key]
            passes=(m['f1']-base['f1']>=.02 and
                    m['ranking_failures']<=base['ranking_failures'] and
                    o['f1']>=withheld['models'][REFERENCE]['f1'] and
                    o['ranking_failures']<=withheld['models'][REFERENCE]['ranking_failures'])
            tests.append({'seed':seed,'f1_delta':m['f1']-base['f1'],
                          'ranking_failure_delta':m['ranking_failures']-base['ranking_failures'],
                          'out_of_distribution_f1_delta':o['f1']-withheld['models'][REFERENCE]['f1'],
                          'pass':passes})
        per_arm[arm]={'all_seeds_pass':all(x['pass'] for x in tests), 'seeds':tests}
    return {'threshold_graph_f1_absolute':.02,'all_seeds_required':True,
            'must_not_worsen_ranking':True,'must_not_worsen_ood':True,
            'arms':per_arm,'promotion_authorized':False,'certification_authorized':False}


def write_cases(path,records):
    with open(path,'wb') as f:
        with gzip.GzipFile(filename='',mode='wb',fileobj=f,mtime=0) as gz:
            for row in records:
                gz.write((json.dumps(row,sort_keys=True,allow_nan=False,separators=(',',':'))+'\n').encode())


def run(*,root,g1_validation_report,g2_run,selection_run,discrimination_run,
        relational_run,density_run,consistency_run,context_run,learned_run,
        recall_run,crowded_run,robustness_run,diagnosis_run,source_run,
        specialization_run,routing_robustness_run,general_run,ceiling_run,
        context18_run,train19_run,ceiling20_run,ceiling21_run,competition22_run,
        context23_run,audit24_run,intervention25_run,identity26_run,evidence27_run,
        probe28_run,output,device='cuda',max_length=2048,steps=STEPS):
    root,output=Path(root),Path(output)
    if output.exists():raise FileExistsError('GAMER_G2_0029_IMMUTABLE_OUTPUT')
    if max_length!=2048 or steps!=STEPS:
        raise ValueError('GAMER_G2_0029_FIXED_PROTOCOL')
    head,manifest,dataset,bootstrap,_,_,hashes=sel.load_parents(root,g1_validation_report,g2_run)
    original=base.original_training_rows(dataset,source13.TRAIN_LIMIT)
    if base._sha('\n'.join(base._sha(r['prompt']) for r in original))!=bootstrap.get('train_prompt_hashes_sha256'):
        raise ValueError('GAMER_G2_0028_TRAIN_LINEAGE')
    history,prior15,augmented,calibration,independent,withheld=ceiling17.regeneration(original)
    if density.sha_rows(history[1])!=bootstrap.get('dev_dataset_sha256'):
        raise ValueError('GAMER_G2_0028_BOOTSTRAP_LINEAGE')
    parent15,_,parent13=general16.verify_robust15_parent(
        routing_robustness_run,prior15,source_run,specialization_run)
    if parent15['parent_hashes']['g1_validation_report_sha256']!=hashes['g1_validation_report_sha256']:
        raise ValueError('GAMER_G2_0028_G1_LINEAGE')
    general16.verify_frozen_ancestry15(parent15['parent_hashes'],
        g1_validation_report=g1_validation_report,g2_run=g2_run,
        discrimination_run=discrimination_run,relational_run=relational_run,
        density_run=density_run,consistency_run=consistency_run,
        context_run=context_run,learned_run=learned_run,recall_run=recall_run,
        crowded_run=crowded_run,robustness_run=robustness_run,
        diagnosis_run=diagnosis_run,source_run=source_run,
        specialization_run=specialization_run)
    training=original+augmented
    report16,rec16,h16=ceiling17.verify_frozen_0016(general_run,source_run,
        routing_robustness_run,augmented,training,calibration,independent,withheld)
    if (report16['parent_hashes']['g2_0014_report_sha256']!=
        parent15['parent_hashes']['g2_0014_report_sha256'] or
        report16['frozen_source13_sha256']!=parent13['new_weights_sha256']):
        raise ValueError('GAMER_G2_0028_PARENT_0016_LINEAGE')
    exposed=dict(zip(ceiling20.EXPOSED,(independent,withheld)))
    rec17,h17=context18.verify_parent_0017(ceiling_run,exposed,h16)
    h18=train19.verify_parent_0018(context18_run,exposed,h16,h17,rec17)
    _,h19=ceiling20.verify_parent_0019(train19_run,{'calibration':calibration,**exposed},rec16,
        {**h16,**h17,**h18},expected_train_sha=density.sha_rows(training),
        expected_datasets={s:density.sha_rows(rows) for s,rows in
                           (('calibration',calibration),*exposed.items())})
    prior=[r for batch in (*history,augmented,calibration,independent,withheld) for r in batch]
    fresh20=ceiling20.fresh_cohorts(prior)
    _,h20=ceiling21.verify_parent_0020(ceiling20_run,{**exposed,**fresh20},
                                       {**h16,**h17,**h18,**h19})
    fresh21=ceiling21._cohort_rows(prior,fresh20)
    _,_,h21=parent22._read_parent21(ceiling21_run,{**fresh20,**fresh21},
                                   {**h16,**h17,**h18,**h19,**h20})
    fresh22=parent22.fresh_cohorts(prior,fresh20,fresh21)
    _,_,h22=parent23._verify_parent22(competition22_run,{**fresh21,**fresh22},
        {**h16,**h17,**h18,**h19,**h20,**h21},density.sha_rows(training))
    fresh23=parent23.fresh_cohorts(prior,fresh20,fresh21,fresh22)
    previous={**fresh22,**fresh23}
    if len(training)!=640:raise ValueError('GAMER_G2_0028_TRAIN_COUNT')
    all_previous=[training,calibration,independent,withheld,*fresh20.values(),
                  *fresh21.values(),*fresh22.values(),*fresh23.values()]
    if len({base._sha(r['prompt']) for batch in all_previous for r in batch})!=sum(map(len,all_previous)):
        raise ValueError('GAMER_G2_0028_PARENT_DEV_OVERLAP')
    parent23_report,old23,h23=audit24.verify_parent23(context23_run,previous,
        {**h16,**h17,**h18,**h19,**h20,**h21,**h22},density.sha_rows(training))
    parent24_report,old24,h24=parent26.verify_parent24(audit24_run,fresh23,
        {**h16,**h17,**h18,**h19,**h20,**h21,**h22,**h23})
    history25=[r for batch in all_previous for r in batch]
    fresh25=parent25.fresh_cohorts(history25,training)
    old25_parent_rows={**fresh23,**fresh25}
    parent25_report,old25,h25=parent26.verify_parent25(intervention25_run,old25_parent_rows,
        {**h16,**h17,**h18,**h19,**h20,**h21,**h22,**h23,**h24},
        density.sha_rows(training))
    history26=[*history25,*(r for batch in fresh25.values() for r in batch)]
    fresh26=parent26.fresh_cohorts(history26,training)
    all_new=[*history26,*fresh26[parent26.NEW[0]],*fresh26[parent26.NEW[1]]]
    if len({base._sha(r['prompt']) for r in all_new})!=len(all_new):
        raise ValueError('GAMER_G2_0028_NEW_DEV_OVERLAP')
    parent26_report,old26,h26=parent27.verify_parent26(identity26_run,{**fresh25,**fresh26},
        {**h16,**h17,**h18,**h19,**h20,**h21,**h22,**h23,**h24,**h25},
        density.sha_rows(training))
    history27=[*history26,*(r for batch in fresh26.values() for r in batch)]
    fresh27=parent27.fresh_cohorts(history27,training)
    if len({base._sha(r['prompt']) for r in (*history27,*fresh27[parent27.NEW[0]],*fresh27[parent27.NEW[1]])})!=len(history27)+448:
        raise ValueError('GAMER_G2_0028_FRESH_PROMPT_OVERLAP')

    report27,old27,h27=parent28.verify_parent27(evidence27_run,{**fresh26,**fresh27},
        {**h16,**h17,**h18,**h19,**h20,**h21,**h22,**h23,**h24,**h25,**h26},
        density.sha_rows(training))
    history28=[*history27,*(r for batch in fresh27.values() for r in batch)]
    fresh28=parent28.fresh_cohorts(history28,training)
    evaluate={**fresh27,**fresh28}



    # Parent G2-0028 must match all reconstructed historical cohorts and
    # its saved tensor and per-case ledgers before a new optimizer is created.
    ancestors={**h16,**h17,**h18,**h19,**h20,**h21,**h22,**h23,**h24,**h25,**h26,**h27}
    parent28_report,old28,h28=verify_parent28(probe28_run,{**fresh27,**fresh28},
        ancestors,density.sha_rows(training))
    history29=[*history28,*(row for batch in fresh28.values() for row in batch)]
    fresh29=fresh_cohorts(history29,training)
    evaluation={**fresh28,**fresh29}
    assert len(evaluation)==4

    head=general16.load_frozen_rank4(head,Path(relational_run)/rel.WEIGHTS,
         parent15['parent_hashes']['g2_0004_weights_sha256'])
    rank_before={k:v.detach().cpu().clone() for k,v in head.state_dict().items()}
    head=head.to(device).eval().requires_grad_(False)
    model,tokenizer=backbone.model_components(manifest['model_id'],
        manifest['source_adapter'],device)
    if any(p.requires_grad for p in model.parameters()):
        raise ValueError('GAMER_G2_0029_BACKBONE_UNFROZEN')
    old19=json.loads((Path(train19_run)/train19.REPORT).read_text(encoding='utf-8'))
    allowed=sel.compatibility_from_train(original)
    width=parent28.rel.capture(head,model,tokenizer,training[:1],device=device,
        max_length=max_length,group='train_0029_width')[0]['vectors'].shape[-1]
    guard=ceiling20.load_guarded(Path(train19_run)/train19.WEIGHTS,width,
                                 old19['new_weights_sha256'])
    frozen23=audit24.load_parent23_models(context23_run,guard,
               parent23_report['new_weights_sha256'])['relational_context_0023']
    reference=parent27.load_parent26_models(identity26_run,frozen23,
               parent26_report['new_weights_sha256'])[REFERENCE]
    original_reference={k:v.detach().cpu().clone() for k,v in reference.state_dict().items()}
    reference=reference.to(device).eval().requires_grad_(False)

    train_data=[]
    for group in range(0,len(training),40):
        batch=training[group:group+40]
        captured=rel.capture(head,model,tokenizer,batch,device=device,
                             max_length=max_length,group='train_0029')
        for row,case in zip(batch,captured):
            ex=case_inventory(case,row,head,reference,allowed,device=device,train=True)
            if _mixed_groups(ex):train_data.append(ex)
        print(f'GAMER_G2_0029_PREPARE {min(group+40,len(training))}/{len(training)}',flush=True)
    if len(train_data)<128:
        raise ValueError('GAMER_G2_0029_TOO_FEW_TRAIN_CASES')
    example_dim=train_data[0]['features'].shape[-1]
    if any(item['features'].shape[-1]!=example_dim or item['frozen_width']!=width
           for item in train_data):
        raise ValueError('GAMER_G2_0029_TRAIN_DIM_DRIFT')
    nets,train_loss=fit_architectures(train_data,width,example_dim,device=device,steps=steps)
    weights={f'{arm}_s{seed}.{key}':v.detach().cpu().contiguous()
             for seed,models in nets.items() for arm,network in models.items()
             for key,v in network.state_dict().items()}

    evaluated={}
    for split,rows in evaluation.items():
        outcomes=[]
        for start in range(0,len(rows),40):
            batch=rows[start:start+40]
            captured=rel.capture(head,model,tokenizer,batch,device=device,
                                 max_length=max_length,group=split)
            for index,(row,case) in enumerate(zip(batch,captured),start+1):
                ex=case_inventory(case,row,head,reference,allowed,device=device,train=False)
                prior=old28[split][index-1] if split in REPLAY else None
                outcomes.append(analyze_case(case,row,ex,nets,split=split,index=index,prior=prior))
            print(f'GAMER_G2_0029_EVAL {split} {min(start+40,len(rows))}/{len(rows)}',flush=True)
        evaluated[split]=outcomes
    rel.validate_original_state(head,rank_before)
    if any(not torch.equal(v,reference.state_dict()[k].detach().cpu())
           for k,v in original_reference.items()):
        raise ValueError('GAMER_G2_0029_REFERENCE_MUTATED')
    if any(p.requires_grad for p in model.parameters()):
        raise ValueError('GAMER_G2_0029_BACKBONE_TRAINED')

    combined={'replayed_0028_dev':[r for split in REPLAY for r in evaluated[split]],
              'new_0029_dev':[r for split in NEW for r in evaluated[split]]}
    fresh=combined['new_0029_dev'];ood=evaluated[NEW[1]]
    fresh_summary=aggregate(fresh);ood_summary=aggregate(ood)
    gates=promotion_gates(fresh_summary,ood_summary)
    confidence={f'{arm}_s{seed}':paired_ci(fresh,f'{arm}_s{seed}',draws=500,seed=seed+99)
                for seed in SEEDS for arm in ARMS}
    report={'schema':SCHEMA,'status':'ARCHITECTURE_BAKEOFF_DEVELOPMENT_NOT_CERTIFIED',
        'parent_hashes':{**ancestors,**h28},
        'train_dataset_sha256':density.sha_rows(training),
        'datasets_sha256':{k:density.sha_rows(v) for k,v in evaluation.items()},
        'new_prompt_sha256':{k:[base._sha(r['prompt']) for r in fresh29[k]] for k in NEW},
        'predeclared':{'seed':SEED,'training_seeds':list(SEEDS),'steps_per_seed':STEPS,
            'lr':LEARNING_RATE,'candidate_top_k':TOP_K,'shared_threshold':THRESHOLD,
            'training_cases_per_step':TRAIN_CASES_PER_STEP,
            'training_groups_per_case':TRAIN_GROUPS_PER_CASE,
            'arms':list(ARMS),'reference':REFERENCE,'replay':list(REPLAY),
            'fresh':list(NEW),'bootstrap_draws':500,
            'no_DEV_selection_or_threshold_tuning':True},
        'training':{'cases_in_corpus':len(training),'usable_cases':len(train_data),
            'loss_per_seed':train_loss,'same_training_cases_and_batches':True,
            'train_labels_after_inventory_only':True,
            'different_parameter_counts_are_reported_not_claimed_matched':True,
            'parameter_counts':{a:sum(p.numel() for p in nets[SEEDS[0]][a].parameters())
                                for a in ARMS}},
        'evaluation':{k:aggregate(v) for k,v in combined.items()},
        'by_split':{k:aggregate(v) for k,v in evaluated.items()},
        'by_family':{k:{name:aggregate([r for r in v if r['family']==name])
                        for name in sorted({r['family'] for r in v})}
                     for k,v in combined.items()},
        'by_density':{k:{name:aggregate([r for r in v if r['density_bucket']==name])
                         for name in sorted({r['density_bucket'] for r in v})}
                      for k,v in combined.items()},
        'by_topology':{k:{name:aggregate([r for r in v if r['structural_topology']==name])
                          for name in sorted({r['structural_topology'] for r in v})}
                       for k,v in combined.items()},
        'paired_intervals':confidence,'gates':gates,
        'literature':{
            'predicate_argument':'https://aclanthology.org/C18-1233/',
            'dynamic_span_graph':'https://aclanthology.org/D19-1585/',
            'joint_graph_decoder':'https://aclanthology.org/2020.acl-main.713/',
            'compositional_generalization':'https://aclanthology.org/2020.emnlp-main.731/'},
        'limits':{'graph_oracle_is_authored_synthetic_gold_not_the_original_deterministic_executable':True,
            'all_arms_share_frozen_G1_candidates_and_may_inherit_its_coverage_ceiling':True,
            'joint_decoder_uses_bounded_global_factors_not_full_OneIE':True,
            'architecture_comparison_is_not_equal_parameter_count':True,
            'source_text_is_not_used_as_direct_autoregressive_graph_input':True,
            'full_text_to_graph_generation_is_a_separate_unimplemented_architecture_track':True,
            'dev_results_do_not_certify_test_generalization':True},
        'frozen_backbone':True,'frozen_g1':True,'frozen_g2_rank_head':True,
        'frozen_parent_weights':True,'candidate_selection_uses_gold':False,
        'dev_labels_used_for_training':False,'dev_checkpoint_selection':False,
        'sealed_test_used':False,'challenge_or_test_split_used':False,
        'production_promotion':False,'g2_certified':False,
        'certification_authorized':False,'graph_execution_authorized':False,
        'memory_writes':False}
    output.mkdir(parents=True,exist_ok=False)
    write_cases(output/LEDGER,[row for split in SPLITS for row in evaluated[split]])
    save_file(weights,str(output/WEIGHTS))
    report['cases_sha256']=backbone.sha256(output/LEDGER)
    report['development_weights_sha256']=backbone.sha256(output/WEIGHTS)
    (output/REPORT).write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf-8')
    return report
