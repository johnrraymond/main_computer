"""G2-0031: learn occurrence-conditioned relationship representations from token states.

Three trainable representation paths share a frozen Qwen backbone, frozen G1
predicted spans, and a label-blind top-24 G2 candidate inventory. TRAIN-only
fitting and calibration; DEV is used solely for final evaluation. No promotion.

These are bounded implementations motivated by PURE, DyGIE++, and OneIE, not
paper reproductions or a claim of fully fine-tuning the language backbone.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import gzip
import hashlib
import json
import math
import random

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from safetensors.torch import save_file

from tools import nanojev_gamer_v1_g2_graph_precision_selection as p30

m29=p30.m
base=p30.base
backbone=p30.backbone
rel=p30.rel
source13=p30.source13
density=p30.density
general16=p30.general16

SCHEMA='nanojev.gamer.graph.g2.occurrence-representation-bakeoff.v1'
REPORT='g2_representation_bakeoff_report.json'
LEDGER='g2_representation_bakeoff_per_case.jsonl.gz'
WEIGHTS='g2_representation_bakeoff_development.safetensors'
POLICIES='g2_representation_bakeoff_frozen_policies.json'
SEED=460331
SEEDS=(460331,460332,460333)
ARMS=('role_span_biaffine','cross_occurrence_attention','occurrence_graph')
STEPS=480
BATCH=3
LR=1.5e-4
HIDDEN=96
WINDOW=5
TRAIN_CAL_FIT=120
TRAIN_CAL_AUDIT=60
REPLAY=p30.NEW
NEW=('new_independent_dev_0031','new_topology_dev_0031')
REFERENCE=p30.REFERENCE
CHAMPION='predicate_argument_s460038'  # fixed from G2-0030, not selected on new DEV
MIN_F1_GAIN=.02
MIN_EXACT_RATE=0.0  # demand non-regression versus BOTH parent controls


def _sha_rows(rows):return density.sha_rows(rows)


def verify_parent30(directory, parent_hashes, training_sha, old_calibration, old_dev):
    """Verify immutable G2-0030 report/policies/1076 case rows before fitting."""
    d=Path(directory)
    rp,lp,pp=(d/p30.REPORT,d/p30.LEDGER,d/p30.POLICIES)
    report=json.loads(rp.read_text(encoding='utf-8'))
    if report.get('schema')!=p30.SCHEMA:
        raise ValueError('GAMER_G2_0031_PARENT_0030_CONTRACT:schema')
    policies=json.loads(pp.read_text(encoding='utf-8'))
    required={'schema':p30.SCHEMA,
              'status':'TRAIN_CALIBRATED_PRECISION_SELECTION_DEV_NOT_CERTIFIED',
              'parent_hashes':parent_hashes,'train_dataset_sha256':training_sha,
              'calibration_fit_sha256':_sha_rows(old_calibration[:p30.CAL_FIT]),
              'calibration_audit_sha256':_sha_rows(old_calibration[p30.CAL_FIT:]),
              'datasets_sha256':{k:_sha_rows(v) for k,v in old_dev.items()},
              'calibration_train_only':True,'frozen_backbone':True,
              'frozen_g1':True,'frozen_g2_0026':True,
              'frozen_g2_0029_challengers':True,
              'dev_labels_used_for_policy_selection':False,
              'dev_checkpoint_selection':False,'candidate_selection_uses_gold':False,
              'sealed_test_used':False,'production_promotion':False,'g2_certified':False}
    for k,v in required.items():
        if report.get(k)!=v:raise ValueError('GAMER_G2_0031_PARENT_0030_CONTRACT:'+k)
    if (report.get('ledger_sha256')!=backbone.sha256(lp) or
            report.get('policies_sha256')!=backbone.sha256(pp) or
            report.get('policies')!=policies):
        raise ValueError('GAMER_G2_0031_PARENT_0030_HASH_DRIFT')
    if report.get('gates')!=p30.development_gates(report['evaluation']['new_0030_dev'],
                                                      report['by_split'][p30.NEW[1]]):
        raise ValueError('GAMER_G2_0031_PARENT_0030_GATE_DRIFT')
    expected={
        'train_calibration_fit_0030':old_calibration[:p30.CAL_FIT],
        'train_calibration_audit_0030':old_calibration[p30.CAL_FIT:],
        **old_dev}
    recorded={k:[] for k in expected}
    names={REFERENCE,'calibrated_reference',
           *(f'{arm}_s{seed}' for arm in p30.ARMS for seed in p30.SEEDS)}
    with gzip.open(lp,'rt',encoding='utf-8') as reader:
        for line in reader:
            row=json.loads(line);group=row.get('group')
            if group not in recorded or len(recorded[group])>=len(expected[group]):
                raise ValueError('GAMER_G2_0031_PARENT_0030_EXTRA_ROW')
            i=len(recorded[group]);origin=expected[group][i]
            if (row.get('index')!=i+1 or
                    row.get('prompt_sha256')!=base._sha(origin['prompt']) or
                    row.get('family')!=origin['family'] or
                    row.get('gold_total')!=len(base.gold_binding_edges(origin)) or
                    set(row.get('metrics',{}))!=names):
                raise ValueError('GAMER_G2_0031_PARENT_0030_LEDGER_IDENTITY')
            recorded[group].append(row)
    if any(len(v)!=len(expected[k]) for k,v in recorded.items()):
        raise ValueError('GAMER_G2_0031_PARENT_0030_ROW_COUNT')
    for split in old_dev:
        if report['by_split'][split]!=p30.aggregate(recorded[split]):
            raise ValueError('GAMER_G2_0031_PARENT_0030_AGGREGATE:'+split)
    if (report['evaluation']['new_0030_dev']!=
            p30.aggregate([r for split in p30.NEW for r in recorded[split]])):
        raise ValueError('GAMER_G2_0031_PARENT_0030_DEV_METRICS')
    if CHAMPION not in policies or 'calibrated_reference' not in policies:
        raise ValueError('GAMER_G2_0031_PARENT_0030_MISSING_CONTROLS')
    return report,policies,recorded,{
        'g2_0030_report_sha256':backbone.sha256(rp),
        'g2_0030_ledger_sha256':backbone.sha256(lp),
        'g2_0030_policies_sha256':backbone.sha256(pp)}


def fresh_cohorts(state,old_calibration,old_dev):
    history=list({base._sha(r['prompt']):r for r in (
        *state['training'],*state['history29'],
        *(r for v in state['fresh29'].values() for r in v),
        *old_calibration,*(r for v in old_dev.values() for r in v))}.values())
    # Additional TRAIN examples are independent of all G2-0029 and -0030
    # training, calibration, and DEV. No DEV examples enter any optimizer.
    calibration=general16.curriculum_rows((history,),limit=TRAIN_CAL_FIT+TRAIN_CAL_AUDIT,seed=SEED)
    random.Random(SEED+3).shuffle(calibration)
    excluded=[*history,*calibration]
    independent=general16.independent_rows((excluded,),limit=320,seed=SEED+100)
    topology=general16.heldout_topology_rows((excluded,independent),limit=128,seed=SEED+101)
    combined=[*excluded,*independent,*topology]
    if (len(calibration)!=180 or len(independent)!=320 or len(topology)!=128 or
            len({base._sha(r['prompt']) for r in combined})!=len(combined) or
            any(r.get('split')!='train' for r in calibration) or
            any(r.get('split')!='dev' for r in (*independent,*topology)) or
            any(r.get('structural_topology') in {'2x4','4x2','4x4'} for r in calibration) or
            {r['structural_topology'] for r in topology}!={'2x4','4x2','4x4'}):
        raise ValueError('GAMER_G2_0031_COHORT_LEAKAGE')
    return calibration,dict(zip(NEW,(independent,topology)))


def occurrence_windows(hidden,prepared,anchors):
    """Predicted-span-aligned contextual token windows, no text/graph parsing.

    Five slots per occurrence: two preceding tokens, mean of the actual
    occurrence tokens, then two following tokens. Tokens outside the raw prompt
    are never exposed, and no author objects/relations are consulted.
    """
    offsets=prepared['offsets'];eligible=prepared['eligible'].tolist()
    start=int(prepared['prompt_start']);length=len(prepared['prompt'])
    if hidden.ndim!=2 or hidden.shape[0]!=len(offsets):
        raise ValueError('GAMER_G2_0031_HIDDEN_OFFSET_MISMATCH')
    mapping=[i for i,(a,b) in enumerate(offsets)
             if eligible[i] and b>a and a>=start and b<=start+length]
    windows=[];masks=[]
    for begin,end,kind in anchors:
        if not (0<=begin<end<=length):
            raise ValueError('GAMER_G2_0031_BAD_PREDICTED_ANCHOR')
        overlap=[j for j in mapping if offsets[j][1]>start+begin and
                 offsets[j][0]<start+end]
        if not overlap:
            raise ValueError('GAMER_G2_0031_UNMAPPABLE_OCCURRENCE')
        first,last=overlap[0],overlap[-1]
        seq=[];mask=[]
        candidates=(first-2,first-1,None,last+1,last+2)
        for idx in candidates:
            if idx is None:
                seq.append(hidden[overlap].float().mean(dim=0));mask.append(True)
            elif idx in mapping:
                seq.append(hidden[idx].float());mask.append(True)
            else:
                seq.append(torch.zeros_like(hidden[0],dtype=torch.float32));mask.append(False)
        windows.append(torch.stack(seq));masks.append(mask)
    w=torch.stack(windows).detach().to(device='cpu',dtype=torch.float16).contiguous()
    mask=torch.tensor(masks,dtype=torch.bool)
    if not torch.isfinite(w.float()).all() or not bool(mask[:,2].all()):
        raise ValueError('GAMER_G2_0031_BAD_TOKEN_WINDOWS')
    return w,mask


def capture_occurrences(head,model,tok,rows,*,device,max_length,group):
    """One frozen backbone pass supplies G1/G2 predictions AND raw token states."""
    output=[]
    if any(p.requires_grad for p in (*model.parameters(),*head.parameters())):
        raise ValueError('GAMER_G2_0031_UPSTREAM_NOT_FROZEN')
    for index,row in enumerate(rows,1):
        with torch.inference_mode():
            prepared=backbone.prepare_prompt(tok,row['prompt'],max_input_tokens=max_length)
            hidden=backbone.frozen_context(model,prepared,device=device)
            g1=head(hidden,eligible=prepared['eligible'].to(device),active_stage='G1')
            spans=base.predicted_spans(g1,prepared)
            g2=head(hidden,eligible=prepared['eligible'].to(device),
                    training_spans=spans,active_stage='G2')
            anchors=base.predicted_anchors(spans,prepared)
            n=len(anchors)
            if n<1 or n>128 or n!=len(spans):
                raise ValueError('GAMER_G2_0031_G1_ANCHOR_COUNT')
            vectors=g2['node_vectors'][:n].detach().cpu().contiguous()
            windows,mask=occurrence_windows(hidden,prepared,anchors)
        # Avoid passing inference tensors into layers needing backward storage.
        windows=windows.clone()
        mask=mask.clone()
        output.append({'index':index,'family':row['family'],
            'prompt_sha256':base._sha(row['prompt']),'anchors':anchors,
            'gold':base.gold_binding_edges(row),
            'vectors':vectors,'windows':windows,'window_mask':mask})
        if index==1 or index%40==0 or index==len(rows):
            print(f'GAMER_G2_0031_CAPTURE {group} {index}/{len(rows)}',flush=True)
    return output


def example_from_case(case,row,state,*,train=False,device='cpu',top_k=m29.TOP_K):
    # The full label-blind inventory defines graph topology even when TRAIN
    # hard-negative sampling restricts which edge labels enter the objective.
    full=m29.case_inventory(case,row,state['head'],state['reference'],
                             state['allowed'],device=device,train=False,top_k=top_k)
    if not len(full['edges']):raise ValueError('GAMER_G2_0031_EMPTY_CANDIDATES')
    windows=case['windows'];mask=case['window_mask']
    if windows.shape[0]!=len(case['anchors']) or mask.shape!=(len(windows),WINDOW):
        raise ValueError('GAMER_G2_0031_OCCURRENCE_ALIGNMENT')
    if train:
        groups=defaultdict(list)
        for i,key in enumerate(full['group'].tolist()):groups[key].append(i)
        gold=full['gold'];mixed=[];negative=[]
        for key,indices in groups.items():
            any_positive=any(full['edges'][i] in gold for i in indices)
            if any_positive and any(full['edges'][i] not in gold for i in indices):
                mixed.append(key)
            elif not any_positive:negative.append(key)
        rng=random.Random(m29.SEED+case['index'])
        rng.shuffle(mixed);rng.shuffle(negative)
        chosen=set(mixed[:m29.TRAIN_GROUPS_PER_CASE-2]+negative[:2])
        ids=[i for i,key in enumerate(full['group'].tolist()) if key in chosen]
        ex={**full,
            'features':full['features'][ids],
            'reference':full['reference'][ids],
            'src':full['src'][ids],
            'dst':full['dst'][ids],
            'group':full['group'][ids],
            'edges':[full['edges'][i] for i in ids],
            'labels':full['labels'][ids],
            # Full predicted graph for context propagation only, NO labels.
            'message_src':full['src'],
            'message_dst':full['dst'],
            'message_reference':full['reference']}
    else:ex=full
    width=case['vectors'].shape[-1]
    relation_idx=ex['features'][:,4*width:4*width+source13.REL_COUNT+1].argmax(-1)
    ex.update(windows=windows,window_mask=mask,relation=relation_idx)
    return ex


class TokenOccurrenceEncoder(nn.Module):
    """Trainable contextual representation, not a G2 frozen node embedding."""
    def __init__(self,input_width,hidden=HIDDEN):
        super().__init__()
        self.norm=nn.LayerNorm(input_width)
        self.token=nn.Linear(input_width,hidden)
        self.position=nn.Parameter(torch.zeros(WINDOW,hidden))
        self.attention=nn.Linear(hidden,1)
        self.mix=nn.Sequential(nn.Linear(hidden*2,hidden),nn.GELU(),nn.LayerNorm(hidden))

    def forward(self,windows,mask):
        if windows.ndim!=3 or windows.shape[1]!=WINDOW or mask.shape!=windows.shape[:2]:
            raise ValueError('GAMER_G2_0031_WINDOW_SHAPE')
        tok=F.gelu(self.token(self.norm(windows.float())))+self.position
        att=self.attention(tok).squeeze(-1).masked_fill(~mask,-1e4).softmax(-1)
        pooled=(tok*att[:,:,None]).sum(1)
        center=tok[:,2]
        return self.mix(torch.cat((pooled,center),-1)),tok


class RoleSpanBiaffine(nn.Module):
    """Occurrence-conditioned predicate/argument biaffine role learning."""
    def __init__(self,dim):
        super().__init__()
        self.encoder=TokenOccurrenceEncoder(dim)
        self.source=nn.Linear(HIDDEN,HIDDEN)
        self.target=nn.Linear(HIDDEN,HIDDEN)
        self.relations=nn.Embedding(source13.REL_COUNT+1,HIDDEN)
        self.bilinear=nn.Parameter(torch.empty(HIDDEN,HIDDEN))
        nn.init.normal_(self.bilinear,std=.002)
        self.readout=nn.Sequential(nn.Linear(HIDDEN*3+1,HIDDEN),nn.GELU(),nn.Linear(HIDDEN,1))
        nn.init.normal_(self.readout[-1].weight,std=.002);nn.init.zeros_(self.readout[-1].bias)

    def forward(self,ex):
        h,_=self.encoder(ex['windows'],ex['window_mask'])
        s=F.gelu(self.source(h[ex['src']])+self.relations(ex['relation']))
        d=F.gelu(self.target(h[ex['dst']]))
        b=((s @ self.bilinear)*d).sum(-1,keepdim=True)/math.sqrt(HIDDEN)
        delta=self.readout(torch.cat((s,d,s*d,b),-1)).squeeze(-1)
        return ex['reference']+delta


class CrossOccurrenceAttention(nn.Module):
    """Learn relations from cross-attention between actual token windows."""
    def __init__(self,dim):
        super().__init__()
        self.encoder=TokenOccurrenceEncoder(dim)
        self.query=nn.Linear(HIDDEN,HIDDEN)
        self.key=nn.Linear(HIDDEN,HIDDEN)
        self.value=nn.Linear(HIDDEN,HIDDEN)
        self.relations=nn.Embedding(source13.REL_COUNT+1,HIDDEN)
        self.readout=nn.Sequential(nn.Linear(HIDDEN*4,HIDDEN),nn.GELU(),nn.Linear(HIDDEN,1))
        nn.init.normal_(self.readout[-1].weight,std=.002);nn.init.zeros_(self.readout[-1].bias)

    def forward(self,ex):
        nodes,tok=self.encoder(ex['windows'],ex['window_mask'])
        pieces=[]
        for start in range(0,len(ex['src']),4096):
            sl=slice(start,start+4096);src=ex['src'][sl];dst=ex['dst'][sl]
            q=self.query(tok[src]);k=self.key(tok[dst]);v=self.value(tok[dst])
            logits=torch.matmul(q,k.transpose(1,2))/math.sqrt(HIDDEN)
            valid=ex['window_mask'][dst][:,None,:]
            att=logits.masked_fill(~valid,-1e4).softmax(-1)
            cross=torch.matmul(att,v).mean(1)
            a=nodes[src];b=nodes[dst]
            role=self.relations(ex['relation'][sl])
            pieces.append(self.readout(torch.cat((a,b,cross,role),-1)).squeeze(-1))
        return ex['reference']+torch.cat(pieces)


class OccurrenceGraph(nn.Module):
    """Contextual occurrence-message graph using only label-blind edges."""
    def __init__(self,dim):
        super().__init__()
        self.encoder=TokenOccurrenceEncoder(dim)
        self.update=nn.ModuleList(nn.Linear(HIDDEN*3,HIDDEN) for _ in range(2))
        self.norm=nn.ModuleList(nn.LayerNorm(HIDDEN) for _ in range(2))
        self.relations=nn.Embedding(source13.REL_COUNT+1,HIDDEN)
        self.readout=nn.Sequential(nn.Linear(HIDDEN*4,HIDDEN),nn.GELU(),nn.Linear(HIDDEN,1))
        nn.init.normal_(self.readout[-1].weight,std=.002);nn.init.zeros_(self.readout[-1].bias)

    def forward(self,ex):
        h,_=self.encoder(ex['windows'],ex['window_mask'])
        src=ex['src'];dst=ex['dst'];ref=ex['reference']
        message_src=ex.get('message_src',src)
        message_dst=ex.get('message_dst',dst)
        message_ref=ex.get('message_reference',ref)
        # Graph messages are confidence weighted from frozen reference logits;
        # no gold edges, labels or DEV targets are used in the forward path.
        weights=torch.sigmoid(message_ref.detach()-m29.LOGIT_CUTOFF)
        for linear,norm in zip(self.update,self.norm):
            incoming=torch.zeros_like(h).index_add(0,message_dst,h[message_src]*weights[:,None])
            outgoing=torch.zeros_like(h).index_add(0,message_src,h[message_dst]*weights[:,None])
            ci=torch.zeros(len(h),device=h.device).index_add(0,message_dst,weights)
            co=torch.zeros(len(h),device=h.device).index_add(0,message_src,weights)
            h=norm(h+F.gelu(linear(torch.cat((h,incoming/ci.clamp(min=.01)[:,None],
                                             outgoing/co.clamp(min=.01)[:,None]),-1))))
        a=h[src];b=h[dst];r=self.relations(ex['relation'])
        return ref+self.readout(torch.cat((a,b,a*b,r),-1)).squeeze(-1)


def models_for_seed(input_width,seed,device):
    torch.manual_seed(seed)
    return {'role_span_biaffine':RoleSpanBiaffine(input_width).to(device),
            'cross_occurrence_attention':CrossOccurrenceAttention(input_width).to(device),
            'occurrence_graph':OccurrenceGraph(input_width).to(device)}


def scorer_input(ex,device):
    allowed=('reference','src','dst','relation','windows','window_mask',
             'message_src','message_dst','message_reference')
    return {k:ex[k].to(device) for k in allowed if k in ex}  # never gold/labels/prompts


def fit_architectures(training,input_width,*,device='cpu',steps=STEPS,seeds=SEEDS):
    if len(training)<8 or steps<1 or steps>STEPS:
        raise ValueError('GAMER_G2_0031_TRAIN_PROTOCOL')
    models={};losses={};parameters={}
    for seed in seeds:
        nets=models_for_seed(input_width,seed,device)
        params={k:sum(p.numel() for p in net.parameters()) for k,net in nets.items()}
        opts={k:torch.optim.AdamW(v.parameters(),lr=LR,weight_decay=1e-4)
              for k,v in nets.items()}
        rng=random.Random(seed+72037)
        for step in range(1,steps+1):
            indices=rng.sample(range(len(training)),min(BATCH,len(training)))
            batch=[training[i] for i in indices]
            done={}
            for arm,net in nets.items():
                net.train();opts[arm].zero_grad(set_to_none=True)
                objectives=[]
                for ex in batch:
                    item=scorer_input(ex,device)
                    score=net(item)
                    objectives.append(m29.ranking_objective(score,
                        {'labels':ex['labels'].to(device),'group':ex['group'].to(device)}))
                loss=torch.stack(objectives).mean()
                if not torch.isfinite(loss):raise ValueError('GAMER_G2_0031_NONFINITE_LOSS')
                loss.backward();torch.nn.utils.clip_grad_norm_(net.parameters(),2.0)
                opts[arm].step();done[arm]=float(loss.detach())
            if step==1 or step%80==0 or step==steps:
                print(f'GAMER_G2_0031_TRAIN {seed} {step}/{steps} '+
                      ' '.join(f'{k}={done[k]:.5f}' for k in ARMS),flush=True)
        for net in nets.values():net.eval().requires_grad_(False)
        models[seed]=nets;losses[str(seed)]=done;parameters[str(seed)]=params
    return models,losses,parameters


def score_case(ex,state,models):
    """Scorers never receive gold, prompt, case labels, or author spans."""
    raw=p30.collect_scores(ex,state['models29'])['raw']
    with torch.inference_mode():
        for seed,nets in models.items():
            for arm,net in nets.items():
                data=scorer_input(ex,next(net.parameters()).device)
                scores=net(data).detach().cpu().numpy().astype('float32')
                if len(scores)!=len(ex['edges']) or not np.isfinite(scores).all():
                    raise ValueError('GAMER_G2_0031_NONFINITE_INFERENCE')
                raw[f'{arm}_s{seed}']=scores
    # Convert groups into contiguous IDs to preserve the G2-0030 decoder.
    _,inv=np.unique(ex['group'].numpy(),return_inverse=True)
    return {'raw':raw,'edges':ex['edges'],'groups':inv.astype('int32'),
            'gold':ex['gold'],'gold_total':len(ex['gold']),
            'candidate_gold':ex['all_candidate_gold'],
            'candidate_count':ex['all_inventory_edges']}


def calibrate(items):
    # TRAIN-only search includes reference threshold control. These challenger
    # calibration labels are never used in their training batches or DEV.
    policies,scores=p30.select_calibration_policies(items,seeds=SEEDS,arms=ARMS)
    return policies,scores


def evaluate_case(item,policies,parent_policies,row,split,index,anchors,prior=None):
    configs={'identity_peer_0026':dict(source=REFERENCE,alpha=0.,
                                        threshold=m29.THRESHOLD,margin=None,reference_gate=None),
             'calibrated_reference_0030':parent_policies['calibrated_reference'],
             'predicate_argument_0030':parent_policies[CHAMPION],
             **policies}
    metrics={}
    for name,policy in configs.items():
        graph=p30.select_graph(item,**policy)
        totals=p30.measure(graph,item)
        source=policy['source'];alpha=policy['alpha']
        scores=(item['raw'][REFERENCE] if source==REFERENCE else
                item['raw'][REFERENCE]+alpha*(item['raw'][source]-item['raw'][REFERENCE]))
        competing,failures=p30.ranking_counts(item,scores)
        metrics[name]={**totals,'competing_gold':competing,'ranking_failures':failures}
    if prior is not None:
        old=prior['metrics']
        for name,previous in ((REFERENCE,'identity_peer_0026'),
                              ('calibrated_reference','calibrated_reference_0030'),
                              (CHAMPION,'predicate_argument_0030')):
            for key in ('correct','false_positive','missing','competing_gold','ranking_failures'):
                if metrics[previous][key]!=old[name][key]:
                    raise ValueError('GAMER_G2_0031_PARENT_0030_REPLAY_DRIFT:'+previous+':'+key)
    return {'group':split,'index':index,'prompt_sha256':base._sha(row['prompt']),
        'family':row['family'],'density_bucket':density._bucket(anchors),
        'structural_topology':row.get('structural_topology','native'),
        'gold_total':item['gold_total'],'candidate_gold':item['candidate_gold'],
        'candidate_count':item['candidate_count'],'metrics':metrics}


def promotion_gates(fresh,ood):
    controls=fresh['models'];ood_models=ood['models']
    names=('identity_peer_0026','calibrated_reference_0030','calibrated_reference',
           'predicate_argument_0030')
    best_f1=max(controls[n]['f1'] for n in names)
    best_exact=max(controls[n]['exact_graph_rate'] for n in names)
    ranking=min(controls[n]['ranking_failures'] for n in names)
    ood_f1=max(ood_models[n]['f1'] for n in names)
    checks={}
    for arm in ARMS:
        seed_checks=[]
        for seed in SEEDS:
            key=f'{arm}_s{seed}';val=controls[key];ood_val=ood_models[key]
            ok=(val['f1']-best_f1>=MIN_F1_GAIN and
                val['exact_graph_rate']>=best_exact and
                val['ranking_failures']<=ranking and
                ood_val['f1']>=ood_f1 and
                ood_val['ranking_failures']<=min(ood_models[n]['ranking_failures'] for n in names))
            seed_checks.append({'seed':seed,'pass':bool(ok),
               'fresh_f1_delta_to_best_control':val['f1']-best_f1,
               'fresh_exact_graph_delta_to_best_control':val['exact_graph_rate']-best_exact,
               'fresh_ranking_delta_to_best_control':val['ranking_failures']-ranking,
               'ood_f1_delta_to_best_control':ood_val['f1']-ood_f1})
        checks[arm]={'all_seeds_pass':all(s['pass'] for s in seed_checks),'seeds':seed_checks}
    return {'required_f1_gain_over_best_control':MIN_F1_GAIN,
            'no_exact_graph_regression':True,'no_ranking_regression':True,
            'no_withheld_topology_regression':True,'all_three_seeds_required':True,
            'arms':checks,'development_success':any(v['all_seeds_pass'] for v in checks.values()),
            'production_promotion':False,'certification_authorized':False}


def _write_ledger(path,records):
    with open(path,'wb') as raw:
        with gzip.GzipFile(fileobj=raw,mode='wb',filename='',mtime=0) as zipped:
            for row in records:
                zipped.write((json.dumps(row,sort_keys=True,allow_nan=False,separators=(',',':'))+'\n').encode())


def run(*,precision30_run,output,device='cuda',max_length=2048,steps=STEPS,**kwargs):
    """Verify parent first; train occurrence encoders; freeze selection on TRAIN."""
    if steps!=STEPS or max_length!=2048:
        raise ValueError('GAMER_G2_0031_FIXED_PROTOCOL')
    # Reuse complete 0015...0029 verifier; no replacement, abbreviated ancestry.
    state=p30._prepare(output=output,device=device,max_length=max_length,**kwargs)
    old_cal,old_dev=p30.calibration_and_dev(state['history29'],state['fresh29'])
    report30,parent_policies,previous,h30=verify_parent30(
        precision30_run,state['ancestors'],_sha_rows(state['training']),old_cal,
        {**state['fresh29'],**old_dev})
    calibration,fresh=fresh_cohorts(state,old_cal,old_dev)
    parents={**state['ancestors'],**h30}
    # Training inputs come from the same original 640 TRAIN cases as G2-0029.
    train=[];input_width=None
    for start in range(0,len(state['training']),40):
        rows=state['training'][start:start+40]
        captured=capture_occurrences(state['head'],state['model'],state['tokenizer'],rows,
                        device=device,max_length=max_length,group='train_0031')
        for row,case in zip(rows,captured):
            ex=example_from_case(case,row,state,train=True,device=device)
            if not m29._mixed_groups(ex):continue
            if input_width is None:input_width=case['windows'].shape[-1]
            train.append(ex)
        print('GAMER_G2_0031_PREPARE '+str(min(start+40,len(state['training'])))+'/'+
              str(len(state['training'])),flush=True)
    if len(train)<100:raise ValueError('GAMER_G2_0031_TOO_FEW_TRAINABLE_CASES')
    trainable_count=len(train)
    models,losses,parameter_counts=fit_architectures(train,input_width,device=device,steps=steps)
    del train
    if any(p.requires_grad for p in state['model'].parameters()):
        raise ValueError('GAMER_G2_0031_BACKBONE_MUTATED')
    def capture_evaluate(rows,split,policies=None,prior=None):
        records=[]
        for start in range(0,len(rows),20):
            batch=rows[start:start+20]
            cases=capture_occurrences(state['head'],state['model'],state['tokenizer'],batch,
                             device=device,max_length=max_length,group=split)
            for ix,(row,case) in enumerate(zip(batch,cases),start+1):
                ex=example_from_case(case,row,state,train=False,device=device)
                item=score_case(ex,state,models)
                if policies is None:records.append((item,row,len(case['anchors']),ix))
                else:records.append(evaluate_case(item,policies,parent_policies,row,
                        split,ix,len(case['anchors']),prior=prior[ix-1] if prior else None))
            print('GAMER_G2_0031_EVAL '+split+' '+str(min(start+20,len(rows)))+'/'+str(len(rows)),flush=True)
        return records
    cal_raw=capture_evaluate(calibration,'train_calibration_0031')
    policies,fit_f1=calibrate([item for item,_,_,_ in cal_raw[:TRAIN_CAL_FIT]])
    fit=[evaluate_case(item,policies,parent_policies,row,'train_fit_0031',i+1,n)
         for i,(item,row,n,_) in enumerate(cal_raw[:TRAIN_CAL_FIT])]
    audit=[evaluate_case(item,policies,parent_policies,row,'train_audit_0031',i+1,n)
           for i,(item,row,n,_) in enumerate(cal_raw[TRAIN_CAL_FIT:])]
    del cal_raw
    evaluated={}
    for split,rows in {**old_dev,**fresh}.items():
        evaluated[split]=capture_evaluate(rows,split,policies,
            prior=previous.get(split))
    rel.validate_original_state(state['head'],state['head_before'])
    if (any(not torch.equal(v,state['reference'].state_dict()[k].detach().cpu())
            for k,v in state['reference_before'].items()) or
            any(p.requires_grad for nets in state['models29'].values()
                for net in nets.values() for p in net.parameters())):
        raise ValueError('GAMER_G2_0031_FROZEN_PARENT_MUTATION')
    new_cases=[r for split in NEW for r in evaluated[split]]
    replay_cases=[r for split in REPLAY for r in evaluated[split]]
    fresh_summary=p30.aggregate(new_cases)
    ood_summary=p30.aggregate(evaluated[NEW[1]])
    gates=promotion_gates(fresh_summary,ood_summary)
    report={'schema':SCHEMA,
        'status':'OCCURRENCE_REPRESENTATION_BAKEOFF_DEVELOPMENT_NOT_CERTIFIED',
        'parent_hashes':parents,
        'train_dataset_sha256':_sha_rows(state['training']),
        'calibration_fit_sha256':_sha_rows(calibration[:TRAIN_CAL_FIT]),
        'calibration_audit_sha256':_sha_rows(calibration[TRAIN_CAL_FIT:]),
        'datasets_sha256':{k:_sha_rows(v) for k,v in {**old_dev,**fresh}.items()},
        'predeclared':{'seeds':list(SEEDS),'steps_per_seed':STEPS,
          'arms':list(ARMS),'train_cases':len(state['training']),
          'calibration_fit_cases':TRAIN_CAL_FIT,'calibration_audit_cases':TRAIN_CAL_AUDIT,
          'candidate_top_k':m29.TOP_K,'frozen_parent_champion':CHAMPION,
          'fresh_independent_seed':SEED+100,'fresh_topology_seed':SEED+101,
          'f1_success_delta':MIN_F1_GAIN,'no_DEV_model_or_policy_selection':True},
        'frozen_backbone':True,'frozen_g1':True,'frozen_g2_rank_head':True,
        'frozen_g2_0026':True,'frozen_parent_0029_models':True,
        'trainable_token_occurrence_encoders':True,
        'frozen_upstream_node_vectors_not_used_by_new_relational_encoders':True,
        'candidate_selection_uses_gold':False,'dev_labels_used_for_training':False,
        'dev_labels_used_for_calibration':False,'dev_checkpoint_selection':False,
        'sealed_test_used':False,'challenge_or_test_split_used':False,
        'production_promotion':False,'g2_certified':False,
        'graph_execution_authorized':False,'certification_authorized':False,
        'parameters_by_seed':parameter_counts,'final_training_losses':losses,
        'trainable_case_count':trainable_count,
        'policies':policies,'policy_fit_graph_f1':fit_f1,
        'calibration_fit':p30.aggregate(fit),
        'calibration_audit':p30.aggregate(audit),
        'evaluation':{'replayed_0030_dev':p30.aggregate(replay_cases),
                      'new_0031_dev':fresh_summary},
        'by_split':{k:p30.aggregate(v) for k,v in evaluated.items()},
        'by_density':{k:{b:p30.aggregate([r for r in rows if r['density_bucket']==b])
                         for b in sorted({r['density_bucket'] for r in rows})}
                      for k,rows in {'new_0031_dev':new_cases,'replayed_0030_dev':replay_cases}.items()},
        'by_topology':{k:{b:p30.aggregate([r for r in rows if r['structural_topology']==b])
                         for b in sorted({r['structural_topology'] for r in rows})}
                      for k,rows in {'new_0031_dev':new_cases,'replayed_0030_dev':replay_cases}.items()},
        'gates':gates,
        'paired_intervals':{n:p30.paired_interval(new_cases,n,baseline='predicate_argument_0030',
                            seed=SEED+i) for i,n in enumerate(sorted(policies))},
        'limitations':{'frozen_language_backbone_not_end_to_end_fine_tuning':True,
           'predicted_G1_spans_and_top24_candidates_still_limit_ceiling':True,
           'synthetic_DEV_not_sealed_test':True,
           'gold_oracle_ceiling_not_deployable':True,
           'deterministic_executable_not_replayed':True}}
    folder=Path(output)
    folder.mkdir(parents=True,exist_ok=False)
    _write_ledger(folder/LEDGER,[*fit,*audit,*replay_cases,*new_cases])
    (folder/POLICIES).write_text(json.dumps(policies,sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    tensors={f'{arm}_s{seed}.{key}':value.detach().float().cpu().contiguous()
             for seed,nets in models.items() for arm,net in nets.items()
             for key,value in net.state_dict().items()}
    save_file(tensors,str(folder/WEIGHTS))
    report['ledger_sha256']=backbone.sha256(folder/LEDGER)
    report['policies_sha256']=backbone.sha256(folder/POLICIES)
    report['development_weights_sha256']=backbone.sha256(folder/WEIGHTS)
    (folder/REPORT).write_text(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    return report
