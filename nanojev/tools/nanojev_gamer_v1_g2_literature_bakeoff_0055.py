"""G2-0055: frozen-evidence neural graph-parser bake-off with mandatory memorization gate.

Research only. Three genuinely different graph models: directed biaffine parser,
second-order message passing over predicted edge neighborhoods, and DETR-style
unordered triple-set prediction with Hungarian supervision. Labels NEVER enter
feature construction, candidate generation, inference or probe-driven selection.
No arm is evaluated on either probe unless it first reconstructs every small
memorization-gate training graph exactly. All weights remain non-production.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import torch
from torch import nn
from torch.nn import functional as F
from safetensors.torch import load_file

from tools import nanojev_gamer_v1_g2_architectural_bakeoff_0054 as parent54
from tools import nanojev_gamer_v1_g2_repaired_graph_learning as g53
from tools import nanojev_gamer_v1_g1_g2_endpoint_repair as p52
from tools import nanojev_gamer_v1_g2_occurrence_evidence_repair as d48

SCHEMA='nanojev.gamer.graph.g2.literature-bakeoff-0055.v1'
STATUS='G2_0055_MEMORIZATION_GATED_RESEARCH_NOT_CERTIFIED'
REPORT='g2_literature_bakeoff_report.json'
LEDGER='g2_literature_bakeoff_per_case.jsonl.gz'
WEIGHTS='g2_0055_development_models.safetensors'
POLICY='g2_0055_frozen_policy.json'
ARMS=('biaffine','higher_order','set_prediction')
SEED=460555
GATE_STEPS=240
FULL_STEPS=320
MAX_STEPS=1200
BATCH=2
SLOTS=64
RANK_BUDGET=512
MAX_EDGE=96
EVAL_CHUNK=2048
LR=5e-4


def refuse(reason):
    raise ValueError('GAMER_G2_0055_'+reason)


def verify_parent54(folder, parent53_run, parent52_run):
    folder=Path(folder)
    record=json.loads((folder/parent54.REPORT).read_text(encoding='utf-8'))
    exp=record.get('scoring_experiment',{})
    h=g53.p52.p51.SOURCE.backbone.sha256
    if (record.get('schema')!=parent54.SCHEMA or record.get('status')!=parent54.STATUS
        or record.get('production_promotion') is not False
        or record.get('sealed_test_used') is not False
        or exp.get('design',{}).get('oracle_accesses_gold_only_for_ceiling_audit') is not True
        or exp.get('results',{}).get('counterfactual_probe',{}).get('Oracle_coverage_ceiling',{}).get('exact')!=31
        or exp.get('results',{}).get('fresh_counterfactual_probe',{}).get('Oracle_coverage_ceiling',{}).get('exact')!=32
        or record.get('scoring_weights_sha256')!=h(folder/parent54.WEIGHTS)
        or record.get('ledger_sha256')!=h(folder/parent54.LEDGER)
        or exp.get('parent53_hashes',{}).get('g2_0053_report_sha256')!=h(Path(parent53_run)/g53.REPORT)
        or exp.get('parent53_hashes',{}).get('g2_0053_weights_sha256')!=h(Path(parent53_run)/g53.WEIGHTS)):
        refuse('PARENT54_CONTRACT_OR_HASH')
    return record,{'g2_0054_report_sha256':h(folder/parent54.REPORT),
                   'g2_0054_weights_sha256':h(folder/parent54.WEIGHTS),
                   'g2_0054_ledger_sha256':h(folder/parent54.LEDGER)}


def feature_inputs(sample, device):
    """No ground-truth keys are accessed; CPU capture -> model device explicitly."""
    return (sample['node'].to(device=device,dtype=torch.float32),
            sample['kinds'].to(device=device,dtype=torch.long),
            sample['margin'].to(device=device,dtype=torch.float32))


class BiaffineParser(nn.Module):
    """Directed low-rank biaffine edge existence plus relation-conditioned score.

    Separate source and destination projections avoid the symmetric-source
    assumption. A learned cardinality head determines decoded graph size.
    """
    def __init__(self):
        super().__init__()
        d=64
        self.src=nn.Sequential(nn.LayerNorm(96),nn.Linear(96,d),nn.Tanh())
        self.dst=nn.Sequential(nn.LayerNorm(96),nn.Linear(96,d),nn.Tanh())
        self.rel_src=nn.Embedding(len(g53.RELATIONS),d)
        self.rel_dst=nn.Embedding(len(g53.RELATIONS),d)
        self.bias=nn.Embedding(len(g53.RELATIONS),1)
        self.geom=nn.Linear(5,1)
        self.margins=nn.Linear(2,1)
        self.count=nn.Sequential(nn.LayerNorm(96),nn.Linear(96,48),nn.GELU(),nn.Linear(48,1))

    def score(self,sample,idx=None):
        dev=next(self.parameters()).device
        n,_,m=feature_inputs(sample,dev)
        if idx is None:idx=torch.arange(sample['edge_count'])
        ix=idx.to(device='cpu',dtype=torch.long)
        src=sample['src'].index_select(0,ix).to(dev)
        dst=sample['dst'].index_select(0,ix).to(dev)
        rel=sample['relation'].index_select(0,ix).to(dev)
        x=self.src(n)[src];y=self.dst(n)[dst]
        logits=((x+self.rel_src(rel))*(y+self.rel_dst(rel))).sum(-1)/math.sqrt(x.shape[-1])
        logits=logits+self.bias(rel).squeeze(-1)
        logits=logits+self.geom(sample['geometry'].index_select(0,ix).to(dev)).squeeze(-1)
        logits=logits+self.margins(torch.stack((m[src],m[dst]),-1)).squeeze(-1)
        return logits

    def predict_count(self,sample):
        dev=next(self.parameters()).device
        node,_,_=feature_inputs(sample,dev)
        return F.softplus(self.count(node.mean(0)).squeeze())


class HigherOrderParser(BiaffineParser):
    """Second-order edge interaction via directed incident-edge messages.

    Gold-free top-k preliminary edges transmit differentiable messages through
    shared source and destination nodes. The updated edge scores depend on the
    predicted surrounding graph rather than independent triples alone.
    """
    def __init__(self):
        super().__init__()
        self.up=nn.Sequential(nn.Linear(96+2,64),nn.GELU(),nn.Linear(64,2))
        self.second=nn.Sequential(nn.Linear(4,32),nn.GELU(),nn.Linear(32,1))

    def score(self,sample,idx=None):
        dev=next(self.parameters()).device
        full=[]
        for start in range(0,sample['edge_count'],EVAL_CHUNK):
            ids=torch.arange(start,min(start+EVAL_CHUNK,sample['edge_count']))
            full.append(super().score(sample,ids))
        base=torch.cat(full)
        k=min(RANK_BUDGET,base.numel())
        # Membership is determined solely by learned features, never gold.
        order=torch.topk(base.detach(),k=k,sorted=False).indices.to('cpu')
        src=sample['src'].index_select(0,order).to(dev)
        dst=sample['dst'].index_select(0,order).to(dev)
        weight=torch.sigmoid(base.index_select(0,order.to(dev)))
        n=sample['node'].shape[0]
        outgoing=torch.zeros(n,device=dev).index_add(0,src,weight)
        incoming=torch.zeros(n,device=dev).index_add(0,dst,weight)
        cnt_src=torch.zeros(n,device=dev).index_add(0,src,torch.ones_like(weight))
        cnt_dst=torch.zeros(n,device=dev).index_add(0,dst,torch.ones_like(weight))
        node=sample['node'].to(device=dev,dtype=torch.float32)
        update=self.up(torch.cat((node,(outgoing/(cnt_src+1))[:,None],
                                     (incoming/(cnt_dst+1))[:,None]),dim=-1))
        all_src=sample['src'].to(dev);all_dst=sample['dst'].to(dev)
        interaction=torch.cat((update[all_src],update[all_dst]),dim=1)
        adjusted=base+self.second(interaction).squeeze(-1)
        if idx is None:return adjusted
        return adjusted.index_select(0,idx.to(dev))


class TripleSetPredictor(nn.Module):
    """Permutation-invariant relation-set prediction with Hungarian matching.

    Fixed learned slots attend to the frozen G1 nodes and predict null-or-label,
    directed source and destination jointly. At inference each slot is mapped
    only onto an admissible G2-0052 typed edge, with no label access.
    """
    def __init__(self):
        super().__init__()
        d=64
        self.nodes=nn.Sequential(nn.LayerNorm(96),nn.Linear(96,d))
        self.kind=nn.Embedding(len(g53.KIND_INDEX),d)
        self.queries=nn.Parameter(torch.randn(SLOTS,d)*.03)
        self.cross=nn.MultiheadAttention(d,4,batch_first=True)
        self.self_attn=nn.MultiheadAttention(d,4,batch_first=True)
        self.norm=nn.LayerNorm(d)
        self.rel=nn.Linear(d,len(g53.RELATIONS)+1)
        self.source=nn.Linear(d,d)
        self.target=nn.Linear(d,d)

    def forward(self,sample):
        dev=next(self.parameters()).device
        node,kinds,_=feature_inputs(sample,dev)
        context=self.nodes(node)+self.kind(kinds)
        q=self.queries.unsqueeze(0)
        a,_=self.cross(q,context.unsqueeze(0),context.unsqueeze(0),need_weights=False)
        q=self.norm(q+a)
        b,_=self.self_attn(q,q,q,need_weights=False)
        q=self.norm(q+b).squeeze(0)
        return (self.rel(q),self.source(q)@context.T/math.sqrt(q.shape[-1]),
                self.target(q)@context.T/math.sqrt(q.shape[-1]))


def model_for(arm):
    if arm=='biaffine':return BiaffineParser()
    if arm=='higher_order':return HigherOrderParser()
    if arm=='set_prediction':return TripleSetPredictor()
    refuse('UNKNOWN_ARCHITECTURE')


def edge_objective(model,sample):
    dev=next(model.parameters()).device
    logit=model.score(sample)
    truth=torch.zeros(sample['edge_count'],dtype=torch.bool,device=dev)
    truth[sample['positive'].to(dev)]=True
    if not bool(truth.any()) or not bool((~truth).any()):refuse('TRAIN_LABEL_DIVERSITY')
    pos=logit[truth];neg=logit[~truth]
    # Exact-graph oriented: in addition to per-edge signal, maximize separation
    # of the weakest correct edge from the strongest false competitor.
    hard=torch.topk(neg,k=min(64,neg.numel())).values
    loss=F.softplus(-pos).mean()+.20*F.softplus(neg).mean()
    loss=loss+.5*F.softplus(hard.max()-pos.min()+.5)
    target=torch.tensor(float(sample['truth_count']),device=dev)
    count_loss=F.smooth_l1_loss(model.predict_count(sample),target)
    return loss+.04*count_loss



def rectangular_assignment(cost):
    """Rectangular minimum-cost Hungarian assignment; no new dependency.

    All costs are detached CPU arrays. Returns matched indices into the
    original slots × true-triples matrix; complexity O(n²m), n<=m.
    """
    import numpy as np
    matrix=np.asarray(cost,dtype=np.float64)
    if matrix.ndim!=2 or not min(matrix.shape) or not np.isfinite(matrix).all():
        refuse('HUNGARIAN_BAD_COSTS')
    transposed=matrix.shape[0]>matrix.shape[1]
    if transposed:matrix=matrix.T
    n,m=matrix.shape
    u=[0.0]*(n+1);v=[0.0]*(m+1)
    p=[0]*(m+1);way=[0]*(m+1)
    for i in range(1,n+1):
        p[0]=i
        j0=0;minv=[float('inf')]*(m+1);used=[False]*(m+1)
        while True:
            used[j0]=True;i0=p[j0];delta=float('inf');j1=0
            for j in range(1,m+1):
                if used[j]:continue
                cur=float(matrix[i0-1,j-1])-u[i0]-v[j]
                if cur<minv[j]:minv[j]=cur;way[j]=j0
                if minv[j]<delta:delta=minv[j];j1=j
            for j in range(m+1):
                if used[j]:u[p[j]]+=delta;v[j]-=delta
                else:minv[j]-=delta
            j0=j1
            if p[j0]==0:break
        while True:
            j1=way[j0];p[j0]=p[j1];j0=j1
            if j0==0:break
    rows=[];cols=[]
    for j in range(1,m+1):
        if p[j]:rows.append(p[j]-1);cols.append(j-1)
    if transposed:return cols,rows
    return rows,cols


def set_objective(model,sample):
    dev=next(model.parameters()).device
    rel,src,dst=model(sample)
    gold=sample['positive']
    if not len(gold) or len(gold)>SLOTS:refuse('SET_GOLD_CAPACITY')
    gold=gold.to('cpu')
    src_gold=sample['src'].index_select(0,gold).to(dev)
    dst_gold=sample['dst'].index_select(0,gold).to(dev)
    rel_gold=sample['relation'].index_select(0,gold).to(dev)+1 # 0 is null
    lr=F.log_softmax(rel,-1);ls=F.log_softmax(src,-1);ld=F.log_softmax(dst,-1)
    # Match entire unordered triples, not independent edge permutations.
    cost=-(lr[:,rel_gold]+ls[:,src_gold]+ld[:,dst_gold]).detach().cpu().numpy()
    slots,targets=rectangular_assignment(cost)
    a=torch.as_tensor(slots,dtype=torch.long,device=dev)
    b=torch.as_tensor(targets,dtype=torch.long,device=dev)
    desired=torch.zeros(SLOTS,dtype=torch.long,device=dev)
    desired[a]=rel_gold[b]
    cls=F.cross_entropy(rel,desired,reduction='none')
    mask=torch.ones(SLOTS,dtype=torch.bool,device=dev);mask[a]=False
    # Equal graph-level weight, with a weaker but essential no-edge loss.
    loss=cls[a].mean()+.2*cls[mask].mean() if bool(mask.any()) else cls[a].mean()
    loss=loss+F.cross_entropy(src[a],src_gold[b])+F.cross_entropy(dst[a],dst_gold[b])
    return loss


def objective(model,sample,arm):
    return set_objective(model,sample) if arm=='set_prediction' else edge_objective(model,sample)


@torch.no_grad()
def infer_edges(model,sample,arm):
    """Ground-truth-free inference, always returning indices of typed G2 edges."""
    if arm=='set_prediction':
        rel,src,dst=model(sample)
        rel_prob=F.log_softmax(rel,-1)
        sp=F.log_softmax(src,-1);dp=F.log_softmax(dst,-1)
        device=rel.device
        ids=sample['src'].to(device);ide=sample['dst'].to(device)
        labels=sample['relation'].to(device)+1
        # Each slot chooses its best *admissible* typed edge, not an arbitrary
        # source/destination/relation combination outside the G1/G2 inventory.
        selected={}
        for q in range(SLOTS):
            if int(rel[q].argmax())==0:continue
            confidence=sp[q,ids]+dp[q,ide]+rel_prob[q,labels]
            best=int(confidence.argmax())
            if float(rel[q,labels[best]])<=float(rel[q,0]):continue
            score=float(confidence[best])
            if best not in selected or score>selected[best]:selected[best]=score
        return set(sorted(selected,key=lambda x:-selected[x])[:MAX_EDGE])
    score=model.score(sample).detach().cpu()
    count=max(0,min(MAX_EDGE,int(round(float(model.predict_count(sample).detach().cpu())))))
    if count==0:return set()
    return set(torch.topk(score,k=min(count,len(score))).indices.tolist())


def audit_arm(model,samples,arm,pair_list,*,section):
    rows=[]
    for sample in samples:
        chosen=infer_edges(model,sample,arm)
        gold=set(sample['positive'].tolist())
        row={'section':section,'arm':arm,'prompt_sha256':sample['prompt_sha256'],
             'pair':sample['pair'],'family':sample['family'],'mode':sample['mode'],
             'gold_edges':sample['truth_count'],'reachable_gold_edges':len(gold),
             'candidate_edges':sample['edge_count'],
             'ranked_edges':sample['edge_count'],
             'selected_edges':len(chosen),
             'missing':sample['truth_count']-len(chosen&gold),'extra':len(chosen-gold),
             'rank_missing':sample['truth_count']-len(gold),
             'reachable':sample['truth_count']==len(gold),
             'exact':chosen==gold and sample['truth_count']==len(gold),
             'selected_indices':sorted(chosen),
             'false_negative_indices':sorted(gold-chosen),
             'false_positive_indices':sorted(chosen-gold)}
        rows.append(row)
    counts=g53.aggregate(rows,pair_list)
    return rows,counts


def memorization_subset(cohorts,pair_sets):
    """One pair in each of four family/mode cells, strictly TRAINING data."""
    chosen={}
    for pair in pair_sets['train']:
        cell=(pair['family'],pair['mode'])
        if cell not in chosen:chosen[cell]=pair
    if len(chosen)!=4:refuse('MEMORIZATION_CELL_BALANCE')
    shas={sha for pair in chosen.values() for sha in pair['sha256']}
    subset=[x for x in cohorts['train'] if x['prompt_sha256'] in shas]
    if len(subset)!=8 or any(x['truth_count']!=len(x['positive']) for x in subset):
        refuse('MEMORIZATION_REQUIRES_REACHABLE_TRAIN_GRAPHS')
    return subset,list(chosen.values())


def train_model(model,samples,arm,*,steps,device,batch,gate_subset=None):
    if not 1<=steps<=MAX_STEPS or not 1<=batch<=8 or device not in ('cpu','cuda') or not samples:
        refuse('TRAIN_BUDGET')
    model=model.to(device).train()
    opt=torch.optim.AdamW(model.parameters(),lr=LR)
    gen=torch.Generator().manual_seed(SEED)
    history=[]
    for step in range(1,steps+1):
        pick=torch.randperm(len(samples),generator=gen)[:batch].tolist()
        opt.zero_grad(set_to_none=True)
        loss=torch.stack([objective(model,samples[i],arm) for i in pick]).mean()
        if not bool(torch.isfinite(loss)):refuse('NONFINITE_LOSS_'+arm)
        loss.backward()
        norm=torch.nn.utils.clip_grad_norm_(model.parameters(),5.)
        if not bool(torch.isfinite(norm)):refuse('NONFINITE_GRADIENT_'+arm)
        opt.step()
        if step==1 or step%40==0 or step==steps:
            value={'step':step,'loss':round(float(loss.detach().cpu()),6)}
            history.append(value)
            print('GAMER_G2_0055_TRAIN '+json.dumps({'arm':arm,**value},sort_keys=True),flush=True)
        if gate_subset is not None and (step%40==0 or step==steps):
            model.eval()
            exact=sum(infer_edges(model,s,arm)==set(s['positive'].tolist()) for s in gate_subset)
            model.train()
            if exact==len(gate_subset):
                return model,history,step,exact
    if gate_subset is None:return model,history,steps,None
    model.eval()
    exact=sum(infer_edges(model,s,arm)==set(s['positive'].tolist()) for s in gate_subset)
    return model,history,steps,exact


def training_device_preflight(device):
    """Actually backpropagates each architecture on the requested CPU/CUDA device."""
    sample={'node':torch.randn(5,96,dtype=torch.float16),
        'kinds':torch.zeros(5,dtype=torch.long),'margin':torch.zeros(5),
        'src':torch.tensor([0,0,1,1,2,2,3,3,4,4],dtype=torch.long),
        'dst':torch.tensor([1,2,2,3,3,4,4,0,0,1],dtype=torch.long),
        'relation':torch.tensor([0,1,0,1,0,1,0,1,0,1],dtype=torch.long),
        'geometry':torch.zeros(10,5), 'positive':torch.tensor([0,4],dtype=torch.long),
        'truth_count':2,'edge_count':10}
    receipt={}
    for arm in ARMS:
        model=model_for(arm).to(device)
        opt=torch.optim.AdamW(model.parameters(),lr=LR)
        opt.zero_grad()
        loss=objective(model,sample,arm)
        if not bool(torch.isfinite(loss)):refuse('DEVICE_PREFLIGHT_LOSS_'+arm)
        loss.backward()
        if any(p.grad is not None and not bool(torch.isfinite(p.grad).all()) for p in model.parameters()):
            refuse('DEVICE_PREFLIGHT_GRADIENT_'+arm)
        opt.step()
        result=infer_edges(model.eval(),sample,arm)
        if not isinstance(result,set) or any(not 0<=idx<10 for idx in result):
            refuse('DEVICE_PREFLIGHT_INFERENCE_'+arm)
        receipt[arm]={'loss':round(float(loss.detach().cpu()),6),'pass':True}
    print('GAMER_G2_0055_DEVICE_PREFLIGHT '+json.dumps({'device':device,'arms':receipt,'status':'PASS'},sort_keys=True),flush=True)


def finalize(samples,pairs,replay,report,*,device,gate_steps,full_steps,batch,
             parent54_report,parent54_hashes,parent48_sha):
    if replay.get('exact_parent50_legacy_replay') is not True or \
       report.get('interface_repair',{}).get('decision',{}).get('coverage_targets_met') is not True:
        refuse('PARENT52_REPLAY')
    cohorts,pair_sets=parent54.split_cohorts(samples,pairs)
    small,small_pairs=memorization_subset(cohorts,pair_sets)
    results={};gate={};ledger=[];weights={};models={};history={};optimizer_total=0
    # Architecture seed resets for reproducibility and no accidental shared states.
    for arm in ARMS:
        torch.manual_seed(SEED)
        model=model_for(arm)
        model,rec,steps_used,correct=train_model(model,small,arm,steps=gate_steps,
            device=device,batch=batch,gate_subset=small)
        optimizer_total+=steps_used
        history[arm]={'memorization':rec}
        entry,stats=audit_arm(model.eval(),small,arm,small_pairs,section='memorization')
        ledger+=entry
        passed=correct==len(small) and stats['exact']==len(small)
        gate[arm]={'pass':passed,'exact':stats['exact'],'required':len(small),
            'steps':steps_used,'false_positive':stats['extra'],'false_negative':stats['missing']}
        print('GAMER_G2_0055_MEMORIZATION_GATE '+json.dumps({'arm':arm,**gate[arm]},sort_keys=True),flush=True)
        if passed:
            model,rec,steps_used,_=train_model(model,cohorts['train'],arm,steps=full_steps,
                 device=device,batch=batch)
            optimizer_total+=steps_used
            history[arm]['full_train']=rec
            # A model that memorizes and then forgets its examples still fails
            # the pre-generalization exact-reconstruction contract.
            retained_rows,retained_stats=audit_arm(model.eval(),small,arm,small_pairs,
                                                    section='memorization_retention')
            ledger+=retained_rows
            gate[arm]['post_full_exact']=retained_stats['exact']
            gate[arm]['retained_after_full']=retained_stats['exact']==len(small)
            if gate[arm]['retained_after_full']:
                models[arm]=model.eval()
        for name,tensor in model.state_dict().items():
            weights[arm+'.'+name]=tensor.detach().cpu().clone()
    # The mandatory gate is causal: if no architecture reconstructs its own
    # memorization examples, NO generalization cohorts are ever scored.
    for arm,model in models.items():
        results[arm]={}
        for section in ('train','train_fit','counterfactual_probe','fresh_counterfactual_probe'):
            rows,aggregate=audit_arm(model,cohorts[section],arm,pair_sets[section],section=section)
            results[arm][section]=aggregate;ledger+=rows
    # A selected research arm must have passed memorization. Train-fit is used
    # only to choose between passing architectures; probes never select.
    chosen=None
    if models:
        def key(arm):
            stats=results[arm]['train_fit']
            return (stats['exact'],-(stats['missing']+stats['extra']),-stats['extra'])
        chosen=max(models,key=lambda name:(key(name),-ARMS.index(name)))
    experiment={
      'schema':SCHEMA,'status':STATUS,'production_promotion':False,
      'parent54_hashes':parent54_hashes,'frozen_encoder_parent48_sha256':parent48_sha,
      'design':{'architectures':{
          'biaffine':'directed low-rank biaffine semantic graph parser with learned cardinality',
          'higher_order':'biaffine parser with second-order predicted-edge message passing',
          'set_prediction':'unordered triple queries with Hungarian matching and no-edge class'},
          'memorization_cohort':'8 TRAIN graphs, one counterfactual pair per family/mode cell',
          'gate_requires_exact':8,'gate_precedes_generalization':True,
          'parent54_historical_control_only':True,'frozen_g1_and_0048_encoder':True,
          'g1_g2_0052_repaired_inventory':True,'sealed_test_used':False,
          'probes_used_for_model_selection':False,'no_gold_at_inference':True},
      'memorization_gate':gate,'results':results,'train_fit_selection':chosen,
      'historical_g2_0054':{
          'counterfactual_probe':parent54_report['scoring_experiment']['results']['counterfactual_probe']['D_competition_calibrated'],
          'fresh_counterfactual_probe':parent54_report['scoring_experiment']['results']['fresh_counterfactual_probe']['D_competition_calibrated']},
      'training':{'steps':optimizer_total,'optimizer_steps_total':optimizer_total,
          'memorization_max_steps_per_arm':gate_steps,'full_steps_per_passing_arm':full_steps,
          'history':history},
      'gates':{'any_architecture_memorized':bool(models),
          'architectures_allowed_to_generalize':sorted(models),
          'generalization_skipped_for_failed_arms':True,
          'next_step':('DIAGNOSE_MEMORIZATION_FAILURE' if not models
               else 'EVALUATE_PASSING_MODELS_WITH_INDEPENDENT_SEALED_HOLDOUT'),
          'production_promotion':False,'g2_certified':False,'sealed_test_used':False}}
    policy={'enabled':False,'research_only':True,'selected_development_arm':chosen,
        'architectures_passed_gate':sorted(models),'production_promotion':False,
        'sealed_test_used':False}
    return experiment,weights,policy


def run(*,candidate54_run,candidate53_run,candidate52_run,candidate51_run,
        occurrence48_run,counterfactual50_run,output,device='cuda',
        memorization_steps=GATE_STEPS,full_steps=FULL_STEPS,batch_prompts=BATCH,**kwargs):
    if kwargs.get('train_pairs',48)!=48 or kwargs.get('probe_pairs',16)!=16:
        refuse('COHORT_COUNTS_FIXED')
    if not 1<=memorization_steps<=MAX_STEPS or not 1<=full_steps<=MAX_STEPS:
        refuse('INVALID_STEP_BUDGET')
    g53.verify_parent52(candidate52_run)
    parent53_report,parent53_state,_=parent54.verify_parent53(candidate53_run,{},candidate52_run)
    del parent53_report,parent53_state
    parent54_report,hashes=verify_parent54(candidate54_run,candidate53_run,candidate52_run)
    if Path(output).exists():raise FileExistsError('GAMER_G2_0055_IMMUTABLE_OUTPUT')
    # Fail fast: every model and its loss, gradient, optimizer and inference
    # are executed on the requested device before the expensive frozen capture.
    training_device_preflight(device)
    weight_path=Path(occurrence48_run)/d48.WEIGHTS
    parent48=json.loads((Path(occurrence48_run)/d48.REPORT).read_text(encoding='utf-8'))
    sha=d48.backbone.sha256(weight_path)
    if parent48.get('schema')!=d48.SCHEMA or sha!=parent48.get('weights_sha256'):
        refuse('PARENT48_HASH')
    checkpoint=load_file(str(weight_path),device='cpu')
    width=checkpoint['encoder.norm.weight'].shape[0]
    frozen=d48.OccurrenceEvidenceScorer(width).eval().requires_grad_(False).to(device)
    frozen.load_state_dict(checkpoint,strict=True)
    snapshot={k:v.detach().clone() for k,v in frozen.state_dict().items()}
    def features(raw,expanded,section):
        return g53.frozen_features(raw,expanded,frozen,section)
    def finish(cases,pairs,replay,report):
        answer=finalize(cases,pairs,replay,report,device=device,
            gate_steps=memorization_steps,full_steps=full_steps,batch=batch_prompts,
            parent54_report=parent54_report,parent54_hashes=hashes,parent48_sha=sha)
        if any(not torch.equal(v,frozen.state_dict()[name]) for name,v in snapshot.items()):
            refuse('FROZEN_0048_MUTATION')
        return answer
    return p52.run(candidate51_run=candidate51_run,output=output,
        scoring_case_features=features,scoring_finalize=finish,
        scoring_schema=SCHEMA,scoring_status=STATUS,
        scoring_report_name=REPORT,scoring_ledger_name=LEDGER,
        alternate_weights_filename=WEIGHTS,alternate_policy_filename=POLICY,
        occurrence48_run=occurrence48_run,counterfactual50_run=counterfactual50_run,
        device=device,fresh_probe_pairs=16,**kwargs)
