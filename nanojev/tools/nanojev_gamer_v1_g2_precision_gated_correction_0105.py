"""G2-0105: precision-gated, graph-preserving specialist corrections.

  python -u -m tools.nanojev_gamer_v1_g2_precision_gated_correction_0105 audit
  python -u -m tools.nanojev_gamer_v1_g2_precision_gated_correction_0105 selftest
  python -u -m tools.nanojev_gamer_v1_g2_precision_gated_correction_0105 run --device cuda

G2-0103's fold-selected raw rerankers and G2-0104 specialist heads are frozen.
Train new binary acceptance gates on TRAIN only, calibrate policies on inner VAL,
then evaluate on the outer base-disjoint TEST. Inference never reads gold, family,
case ID, or G2-0102 retrospective cause. Existing graph edges are protected in
additive modes. Swap modes require extra evidence and respect frozen capacities.
The reused 275-case FIT cohort is NOT independent certification. No promotion.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import random
import tempfile

import torch
from torch import nn
from torch.nn import functional as F
from safetensors.torch import load_file, save_file

from tools import nanojev_gamer_v1_g2_failure_specialists_0104 as s
from tools import nanojev_gamer_v1_g2_raw_graph_reranking_0103 as q
from tools import nanojev_gamer_v1_g2_graph_reconstruction_0101 as r
from tools import nanojev_gamer_v1_g2_encounter_bakeoff_0100 as b
from tools import nanojev_gamer_v1_g2_router_trainability_0098 as tr
from tools import nanojev_gamer_v1_g2_competence_routing_0098 as m
from tools import nanojev_gamer_v1_g2_fuzz_and_correct_0095 as old

TAG='G2_0105_PRECISION_GATE'
SCHEMA='nanojev.g2.precision-gated-specialists-0105.v1'
SEED=q.SEED
HEADS=s.HEADS
ARMS=('frozen_0103','specialist_score_add','suppressed_add','weak_add','dual_add',
      'suppressed_swap','dual_swap')
THRESHOLDS=(.65,.80,.90,.97,.995)
SPECIALIST_FLOORS=(0.,1.,2.)
SWAP_GAPS=(.5,2.)
DIM=20
HIDDEN=64
MAX_PROPOSALS_PER_HEAD=384


def emit(event,data):
    print(TAG+'_'+event+' '+json.dumps(data,sort_keys=True,allow_nan=False),flush=True)


def require(condition,reason):
    if not condition:raise ValueError(TAG+'_'+reason)


def _source(root,output):
    source,experts,r99,r100,r101,r102,r103,p103,p102=s._source(root,output)
    path=source[8]/'failure-specialists-0104'/'result.json'
    require(path.is_file(),'0104_RESULT_MISSING')
    report=old.read(path)
    require(report.get('schema')==s.SCHEMA and
            report.get('accepted_on_reused_fit_cv') is False and
            report.get('source_0103_sha256')==old.digest(p103) and
            len(report.get('folds',[]))==3,
            '0104_FROZEN_LINEAGE_CHANGED')
    require(all(report.get(k) is False for k in ('production_promoted',
            'g3_unlocked','dev_used','confirmation_used')) and
            report.get('research_only') is True,'0104_RESEARCH_POLICY_CHANGED')
    for fold in range(3):
        require(report['folds'][fold]['fold']==fold and
                report['folds'][fold]['base_counts']==r103['folds'][fold]['base_counts'] and
                report['folds'][fold]['selected_on']=='INNER_VALIDATION_ONLY',
                '0104_SPLIT_OR_SELECTION_CHANGED')
    return (source,experts,r99,r100,r101,r102,r103,report,p103,path,p102)


def _fold_reference(index,source,experts,r99,r100,r103,r104,p102,p103,device):
    bundle,models,choice=s._fold_reference(index,source,experts,r99,r100,r103,p102,device)
    entry=r104['folds'][index]
    heads,manifest=s.load_candidate(Path(entry['manifest']).parent,experts,
                                     device,old.digest(p103))
    require(set(heads)==set(HEADS) and manifest['selected']==entry['chosen']
            and manifest['role']=='base-disjoint-fold',
            '0104_FOLD_SPECIALIST_CHECKPOINT_CHANGED')
    return bundle,models,choice,heads


class PrecisionGate(nn.Module):
    """Learns P(correct missing typed edge | frozen model evidence)."""
    def __init__(self):
        super().__init__()
        self.net=nn.Sequential(nn.Linear(DIM,HIDDEN),nn.Tanh(),
                  nn.Linear(HIDDEN,HIDDEN//2),nn.Tanh(),nn.Linear(HIDDEN//2,1))

    def forward(self,x):
        require(x.shape[-1]==DIM,'GATE_FEATURE_DIM_DRIFT')
        return self.net(x).squeeze(-1)


def _group_usage(graph,objects):
    out=Counter();inc=Counter()
    for i,j in (graph>0).nonzero(as_tuple=False).tolist():
        rel=int(graph[i,j]);src=objects[i][2];dst=objects[j][2]
        out[(i,dst,rel)]+=1;inc[(j,src,rel)]+=1
    return out,inc


def _cap(spec,objects,i,j,rel):
    typ=b.BIND_CLASSES[rel]
    src=objects[i][2];dst=objects[j][2]
    outkey='|'.join((src,dst,typ))
    inkey='|'.join((dst,src,typ))
    method=spec['method']
    return (int(spec.get('k',2)) if method=='source_topk' else
            int(spec.get('out_caps',{}).get(outkey,2)) if method=='joint' else 10**8,
            int(spec.get('in_caps',{}).get(inkey,2)) if method=='joint' else 10**8)


def _capacity_ok(graph,objects,spec,i,j,new_rel,usage=None):
    if new_rel==0 or i==j:return False
    old_rel=int(graph[i,j]);src=objects[i][2];dst=objects[j][2]
    out,inc=usage if usage is not None else _group_usage(graph,objects)
    outcap,incap=_cap(spec,objects,i,j,new_rel)
    extra=0 if old_rel==new_rel else 1
    return (out[(i,dst,new_rel)]+extra<=outcap and
            inc[(j,src,new_rel)]+extra<=incap)


def _set_edge(graph,objects,usage,i,j,rel):
    out,inc=usage
    previous=int(graph[i,j]);src=objects[i][2];dst=objects[j][2]
    if previous:
        out[(i,dst,previous)]-=1;inc[(j,src,previous)]-=1
    graph[i,j]=rel
    if rel:
        out[(i,dst,rel)]+=1;inc[(j,src,rel)]+=1


def _features(rec,experts,device,bundle,base_models,choice,heads,spec):
    """No gold or case metadata read. Build frozen evidence and typed proposals."""
    with torch.no_grad():
        score,features,gates=s._base_inputs(rec,experts,device,bundle,base_models,choice)
        margins,preds=b._inputs(rec,experts,device)
        objects=rec['item']['objects'];n=len(objects)
        baseline=r._decode(score,objects,spec,device).cpu()
        baseline_dev=baseline.to(device)
        rawmax=margins.max(0).values
        rawmean=margins.mean(0)
        rawstd=margins.std(0,unbiased=False)
        typed=torch.arange(1,b.R+1,device=device).view(1,1,b.R)
        votes=(preds[...,None]==typed).sum(0).float()/b.E
        positive=(margins>0).sum(0).float()/b.E
        base=score[...,1:]-score[...,:1]
        best=base.max(-1,keepdim=True).values.expand_as(base)
        chosen_margin=score.gather(-1,baseline_dev[...,None]).expand(-1,-1,b.R)
        selected=(baseline_dev[...,None]==typed).float()
        occupied=(baseline_dev>0).float()[...,None].expand_as(base)
        srcdegree=(baseline_dev>0).float().sum(1)/max(1,n-1)
        dstdegree=(baseline_dev>0).float().sum(0)/max(1,n-1)
        span_dist=torch.tensor([[abs(objects[i][0]-objects[j][0]) for j in range(n)]
                       for i in range(n)],device=device,dtype=base.dtype)
        span_dist=span_dist.clamp(max=400.)/400.
        i=torch.arange(n,device=device)
        relation=typed.float().expand(n,n,-1)/b.R
        ones=torch.ones_like(base)
        srcd=srcdegree[:,None,None].expand_as(base)
        dstd=dstdegree[None,:,None].expand_as(base)
        dist=span_dist[...,None].expand_as(base)
        raw_rank=(rawmax>=rawmax.max(-1,keepdim=True).values).float()
        out={}
        for kind in HEADS:
            require(kind in heads and not heads[kind].training,
                    'UNFROZEN_0104_'+kind.upper())
            logit=heads[kind](features).detach().clamp(-12.,12.)
            # The candidate specialist is *evidence*, not an instruction to
            # overwrite the graph. The gate learns whether that edit is safe.
            data=torch.stack((base.clamp(-12.,12.)/6.,best.clamp(-12.,12.)/6.,
                (base-best).clamp(-12.,12.)/6.,
                chosen_margin.clamp(-12.,12.)/6.,
                rawmax.clamp(-12.,12.)/6.,rawmean.clamp(-12.,12.)/6.,
                rawstd.clamp(0.,12.)/6.,votes,positive,
                logit/6.,logit.sigmoid(),
                (logit-base).clamp(-12.,12.)/6.,selected,occupied,
                srcd,dstd,dist,relation,raw_rank,
                ones*(1. if kind=='suppressed' else 0.)),dim=-1)
            eligible=gates[kind]&(baseline_dev[...,None]!=typed)
            require(data.shape==(n,n,b.R,DIM),'FEATURE_GRID_DRIFT')
            out[kind]={'x':data.detach(),'eligible':eligible.detach(),
                       'specialist':logit.detach()}
    return {'rec':rec,'objects':objects,'baseline':baseline,'kinds':out}


def _prepare(records,experts,device,bundle,models,choice,heads,spec,*,supervised):
    prepared=[];stats=Counter()
    for rec in records:
        item=_features(rec,experts,device,bundle,models,choice,heads,spec)
        if supervised:
            gold,valid=b._gold_mask(rec,device)
            for kind in HEADS:
                eligible=item['kinds'][kind]['eligible']
                positive=eligible&(gold[...,None]==torch.arange(1,b.R+1,device=device))
                item['kinds'][kind]['positive']=positive
                item['kinds'][kind]['negative']=eligible&~positive
                stats[kind+'_positive']+=int(positive.sum())
                stats[kind+'_negative']+=int((eligible&~positive).sum())
        prepared.append(item)
    return prepared,dict(stats)


def _train(prepared,kind,device,*,steps=240,lr=.0015,seed=SEED):
    require(kind in HEADS and 1<=steps<=3000 and 0<lr<.1,'BAD_TRAIN_ARGS')
    pos=[];neg=[]
    for sample in prepared:
        part=sample['kinds'][kind];x=part['x'].reshape(-1,DIM)
        pos_ids=part['positive'].reshape(-1).nonzero().squeeze(-1)
        neg_ids=part['negative'].reshape(-1).nonzero().squeeze(-1)
        if len(pos_ids):pos.append((x,pos_ids))
        if len(neg_ids):
            # Hard negatives have high specialist confidence. Also preserve
            # randomly sampled negatives across the same eligibility domain.
            sp=part['specialist'].reshape(-1)[neg_ids]
            hard=neg_ids[torch.topk(sp,min(128,len(sp))).indices]
            neg.append((x,neg_ids,hard))
    require(pos and neg,'POSITIVE_OR_NEGATIVE_SUPERVISION_ABSENT_'+kind)
    torch.manual_seed(seed)
    net=PrecisionGate().to(device)
    opt=torch.optim.AdamW(net.parameters(),lr=lr,weight_decay=.0001)
    rng=random.Random(seed)
    for step in range(1,steps+1):
        loss_terms=[]
        for _ in range(3):
            xp,ip=rng.choice(pos)
            xn,ineg,hard=rng.choice(neg)
            ip=ip[torch.as_tensor(rng.sample(range(len(ip)),min(len(ip),16)),device=device)]
            # Hard + population negatives: calibrate false additions, not only
            # balanced training loss. Selection threshold is tuned on VAL.
            ih=hard[torch.as_tensor(rng.sample(range(len(hard)),min(len(hard),32)),device=device)]
            ir=ineg[torch.as_tensor(rng.sample(range(len(ineg)),min(len(ineg),32)),device=device)]
            lp=net(xp[ip]);ln=net(xn[torch.cat((ih,ir))])
            loss_terms.append(.5*F.softplus(-lp).mean()+.5*F.softplus(ln).mean())
        loss=torch.stack(loss_terms).mean()
        require(bool(torch.isfinite(loss)),'NONFINITE_GATE_LOSS')
        opt.zero_grad(set_to_none=True);loss.backward()
        torch.nn.utils.clip_grad_norm_(net.parameters(),1.)
        opt.step()
        if step in (1,steps) or step%100==0:
            emit('TRAIN',{'head':kind,'step':step,'steps':steps,
                          'loss':round(float(loss.detach()),6)})
    net.eval().requires_grad_(False)
    return net


def _score_proposals(prepared,gates):
    """Cache sorted candidates once, so validation grid never retrains/relogs."""
    output=[]
    with torch.no_grad():
        for item in prepared:
            new={**item,'proposals':{},'truncated':{}}
            for kind in HEADS:
                part=item['kinds'][kind]
                mask=part['eligible']
                loc=mask.nonzero(as_tuple=False)
                count=len(loc)
                if not count:
                    new['proposals'][kind]=[];new['truncated'][kind]=0;continue
                x=part['x'][mask]
                # Convert once, not once per proposal: individual GPU->CPU
                # conversions in this loop would serialize thousands of CUDA
                # operations on every graph.
                conf=gates[kind](x).sigmoid().detach().cpu().tolist()
                strength=part['specialist'][mask].detach().cpu().tolist()
                coords=loc.detach().cpu().tolist()
                entries=[(conf[z],strength[z],coords[z][0],coords[z][1],
                          coords[z][2]+1,kind,
                          float(1./(1.+math.exp(-strength[z]))))
                         for z in range(count) if strength[z]>=0.]
                entries.sort(key=lambda e:(-e[0],-e[1],e[2],e[3],e[4]))
                new['truncated'][kind]=max(0,len(entries)-MAX_PROPOSALS_PER_HEAD)
                new['proposals'][kind]=entries[:MAX_PROPOSALS_PER_HEAD]
            output.append(new)
    return output


def _mode_heads(arm):
    if arm=='specialist_score_add':return HEADS
    if arm.startswith('suppressed_'):return ('suppressed',)
    if arm.startswith('weak_'):return ('weak',)
    if arm.startswith('dual_'):return HEADS
    require(arm=='frozen_0103','UNKNOWN_POLICY')
    return ()


def _predict(item,spec,arm,*,threshold=.90,floor=1.,swap_gap=2.,verify=False):
    graph=item['baseline'].clone()
    stats=Counter();stats['proposals_available']=sum(len(item['proposals'][x])
                                                 for x in _mode_heads(arm))
    if arm=='frozen_0103':return graph,dict(stats)
    require(arm in ARMS and 0<threshold<1 and floor>=0 and swap_gap>=0,
            'BAD_POLICY_PARAMETERS')
    allow_swap=arm.endswith('_swap')
    # A deterministic nonlearned specialist-score gate is retained as an
    # ablation; no learned acceptance model is needed for that policy.
    heuristic=arm=='specialist_score_add'
    all_candidates=[(e[6] if heuristic else e[0],*e[1:])
                    for k in _mode_heads(arm) for e in item['proposals'][k]
                    if (e[6] if heuristic else e[0])>=threshold and e[1]>=floor]
    all_candidates.sort(key=lambda e:(-e[0],-e[1],e[2],e[3],e[4]))
    stats['gated']=len(all_candidates)
    objects=item['objects'];use=_group_usage(graph,objects)
    for _,_,i,j,rel,kind,_ in all_candidates:
        oldrel=int(graph[i,j])
        if oldrel==rel:continue
        if oldrel:
            if not allow_swap:
                stats['protected_baseline']+=1;continue
            # Swapping a frozen edge requires both a high calibrated gate and
            # an independently favorable specialist margin gap.
            oldx=item['kinds'][kind]['x'][i,j,rel-1]
            # Feature[3] is the original chosen margin; feature[9] is
            # specialist margin. Their scales both use /6.
            margin_gap=float((oldx[9]-oldx[3])*6.)
            if margin_gap<swap_gap:
                stats['swap_evidence_insufficient']+=1;continue
        if not _capacity_ok(graph,objects,spec,i,j,rel,use):
            stats['capacity_blocked']+=1;continue
        _set_edge(graph,objects,use,i,j,rel)
        stats['swap' if oldrel else 'added']+=1
    if verify:
        n=len(graph)
        forced=torch.full((n,n,b.R+1),-100.,dtype=torch.float32)
        forced[...,0]=0.
        for i,j in (graph>0).nonzero(as_tuple=False).tolist():
            forced[i,j,int(graph[i,j])]=100.
        replay=r._decode(forced,objects,spec,'cpu').cpu()
        require(torch.equal(replay,graph),'CAPACITY_OR_STRUCTURAL_REPLAY_CHANGED')
    return graph,dict(stats)


def _evaluate(items,spec,arm,**params):
    counts=Counter();fam=defaultdict(Counter);corrections=Counter()
    family_changes=defaultdict(Counter)
    for item in items:
        rec=item['rec'];family=rec['item']['row']['family']
        graph,events=_predict(item,spec,arm,**params)
        met=m._count(rec,graph)
        counts.update(met);fam[family].update(met)
        corrections.update(events);family_changes[family].update(events)
        base=item['baseline']; gold=rec['item']['targets'].cpu()
        changed=(graph!=base)
        correct=changed&(graph==gold)&(graph>0)
        wrong=changed&~correct
        corrections.update({'correct_edit':int(correct.sum()),'wrong_edit':int(wrong.sum()),
                            'corrected_case':int(bool((changed& (graph==gold)).any()))})
        family_changes[family].update({'correct_edit':int(correct.sum()),
               'wrong_edit':int(wrong.sum()),'changed_cases':int(bool(changed.any()))})
        # A correct addition differs from an accurate whole graph: both are reported.
        corrections['new_exact']+=int(met['exact'] and not m._count(rec,base)['exact'])
        corrections['lost_exact']+=int(not met['exact'] and m._count(rec,base)['exact'])
    stat={'overall':m._metric(counts),
          'by_family':{k:m._metric(v) for k,v in fam.items()},
          'edit':dict(corrections),'by_family_edit':{k:dict(v) for k,v in family_changes.items()}}
    accepted=stat['edit'].get('correct_edit',0)+stat['edit'].get('wrong_edit',0)
    stat['edit']['precision']=stat['edit'].get('correct_edit',0)/accepted if accepted else 0.
    return stat


def _options():
    yield 'frozen_0103',{}
    for arm in ARMS[1:]:
        for t in THRESHOLDS:
            for floor in SPECIALIST_FLOORS:
                if arm.endswith('_swap'):
                    for gap in SWAP_GAPS:
                        yield arm,{'threshold':t,'floor':floor,'swap_gap':gap}
                else:
                    yield arm,{'threshold':t,'floor':floor}


def _rank(stat,baseline):
    encounter=stat['by_family']['encounter'];allocation=stat['by_family']['allocation']
    ctrl=baseline['by_family']
    feasible=(encounter['edge_f1']>=ctrl['encounter']['edge_f1'] and
              allocation['edge_f1']>=ctrl['allocation']['edge_f1']-.01 and
              stat['overall']['edge_f1']>=baseline['overall']['edge_f1'] and
              stat['overall']['exact']>=baseline['overall']['exact'])
    return (int(feasible),encounter['exact'] if feasible else -1,
            stat['overall']['exact'] if feasible else -1,
            encounter['edge_f1'] if feasible else -1.,
            stat['overall']['edge_f1'] if feasible else -1.,
            stat['edit']['precision'] if feasible else -1.)


def _select(items,spec):
    baseline=_evaluate(items,spec,'frozen_0103')
    entries=[]
    for arm,params in _options():
        stat=baseline if arm=='frozen_0103' else _evaluate(items,spec,arm,**params)
        entries.append({'arm':arm,'params':params,'rank':list(_rank(stat,baseline)),
            'encounter_exact':stat['by_family']['encounter']['exact'],
            'encounter_f1':stat['by_family']['encounter']['edge_f1'],
            'overall_exact':stat['overall']['exact'],
            'overall_f1':stat['overall']['edge_f1'],
            'edit_precision':stat['edit']['precision'],
            'correct_edits':stat['edit'].get('correct_edit',0),
            'wrong_edits':stat['edit'].get('wrong_edit',0)})
    selected=max(enumerate(entries),key=lambda p:(tuple(p[1]['rank']),-p[0]))[1]
    return selected,entries,baseline


def _metric_sum(folds,key,family=None):
    counts=Counter()
    for fold in folds:
        part=fold[key]['by_family'][family] if family else fold[key]['overall']
        for name in ('tp','fp','fn','exact','cases','g1_exact'):
            counts[name]+=part[name]
    return m._metric(counts)


def _save(path,models,experts,p103hash,p104hash,baseline,selection,role):
    path.mkdir(parents=True,exist_ok=True)
    checkpoint={}
    for kind,net in models.items():
        require(kind in HEADS and not net.training,'GATE_STILL_TRAINING')
        file=path/(kind+'-gate.safetensors')
        save_file({k:v.detach().cpu().contiguous() for k,v in net.state_dict().items()},str(file))
        checkpoint[kind]={'path':str(file),'sha256':old.digest(file)}
    manifest={'schema':SCHEMA,'role':role,'research_only':True,
        'production_promoted':False,'g3_unlocked':False,'dev_used':False,
        'confirmation_used':False,'reused_fit_not_independent_certification':True,
        'source_0103_sha256':p103hash,'source_0104_sha256':p104hash,
        'expert_order':list(experts),
        'expert_hashes':{k:v['sha256'] for k,v in experts.items()},
        'frozen_base_0103':baseline,'selected':selection,'gates':checkpoint}
    old.write(path/'manifest.json',manifest)
    return manifest


def load_candidate(folder,experts,device,p103hash,p104hash):
    manifest=old.read(Path(folder)/'manifest.json')
    require(manifest.get('schema')==SCHEMA and manifest.get('research_only') is True
       and all(manifest.get(k) is False for k in ('production_promoted','g3_unlocked',
            'dev_used','confirmation_used'))
       and manifest.get('source_0103_sha256')==p103hash
       and manifest.get('source_0104_sha256')==p104hash,'CHECKPOINT_LINEAGE_CHANGED')
    require(manifest['expert_order']==list(experts),'EXPERT_ORDER_DRIFT')
    require(all(manifest['expert_hashes'].get(k)==v['sha256'] and
            old.digest(v['path'])==v['sha256'] for k,v in experts.items()),
            'EXPERT_CHECKPOINT_MUTATED')
    gates={}
    for kind,entry in manifest['gates'].items():
        require(kind in HEADS and old.digest(entry['path'])==entry['sha256'],
                'PRECISION_GATE_TAMPER')
        net=PrecisionGate().to(device)
        net.load_state_dict(load_file(entry['path'],device='cpu'),strict=True)
        net.eval().requires_grad_(False);gates[kind]=net
    return gates,manifest


def audit(root=old.ROOT_DEFAULT,output=None):
    source,experts,r99,r100,r101,r102,r103,r104,p103,p104,p102=_source(root,output)
    for index in range(3):
        _,_,_,heads=_fold_reference(index,source,experts,r99,r100,r103,r104,
                                   p102,p103,'cpu')
        require(set(heads)==set(HEADS),'0104_FOLD_HEADS_INCOMPLETE')
    emit('AUDIT',{'ready':True,'source_0104_sha256':old.digest(p104),
       'source_0103_sha256':old.digest(p103),'folds':3,'expert_count':len(experts),
       'frozen_controls':['G2_0103_SELECTED','G2_0104_SPECIALIST_HEADS'],
       'new_trainable_heads':['suppressed_acceptance','weak_acceptance'],
       'methods':list(ARMS),'validation_options':len(list(_options())),
       'gold_free_inference':True,'inner_validation_only':True,
       'reused_fit_not_independent_certification':True,
       'production_promotion':False,'g3_unlocked':False})


def run(root=old.ROOT_DEFAULT,output=None,device='cuda',seed=SEED,
        steps=240,lr=.0015,force=False):
    require(device=='cpu' or (device.startswith('cuda') and torch.cuda.is_available()),
            'CUDA_UNAVAILABLE')
    require(1<=steps<=3000 and 0<lr<.1,'TRAINING_ARGUMENTS_INVALID')
    source,experts,r99,r100,r101,r102,r103,r104,p103,p104,p102=_source(root,output)
    outdir=source[8]/'precision-gated-specialists-0105'
    report_path=outdir/'result.json'
    require(force or not report_path.exists(),'REPORT_EXISTS_USE_FORCE')
    emit('START',{'device':device,'folds':3,'steps_per_gate':steps,
          'policies':list(ARMS),'validation_options':len(list(_options())),
          'frozen_benchmark':'G2_0103_SELECTED_FOLD_REPLAY',
          'training':'BINARY_EDIT_ACCEPTANCE_ON_FIT_TRAIN_ONLY',
          'outer_test_after_validation_selection':True,
          'reused_fit_not_independent_certification':True})
    records,spec=b.parent._records(source,experts,device)
    folds=[];allcase=[]
    for fold,(index,(train,val,test),counts) in enumerate(tr._split_by_base(records,3,seed)):
        require(index==fold and counts==r103['folds'][fold]['base_counts'] and
                counts==r104['folds'][fold]['base_counts'],'BASE_FOLDS_DRIFT')
        bundle,basemodels,choice,heads=_fold_reference(fold,source,experts,r99,r100,
                                         r103,r104,p102,p103,device)
        samples,elig=_prepare(train,experts,device,bundle,basemodels,choice,heads,
                              spec,supervised=True)
        require(all(elig.get(k+'_positive',0)>0 and elig.get(k+'_negative',0)>0
                    for k in HEADS),'NO_TRAINABLE_POSITIVE_IN_FOLD')
        gates={kind:_train(samples,kind,device,steps=steps,lr=lr,
                           seed=seed+fold*1073+j*167)
               for j,kind in enumerate(HEADS)}
        val_data,_=_prepare(val,experts,device,bundle,basemodels,choice,heads,
                            spec,supervised=False)
        val_data=_score_proposals(val_data,gates)
        selected,options,_=_select(val_data,spec)
        test_data,_=_prepare(test,experts,device,bundle,basemodels,choice,heads,
                             spec,supervised=False)
        test_data=_score_proposals(test_data,gates)
        control=_evaluate(test_data,spec,'frozen_0103')
        ref=r103['folds'][fold]['selected']
        require(all(control['overall'][k]==ref['overall'][k] for k in ('tp','fp','fn','exact'))
             and all(control['by_family'][f][k]==ref['by_family'][f][k]
                     for f in ('encounter','allocation')
                     for k in ('tp','fp','fn','exact')),
                'FROZEN_0103_REPLAY_MISMATCH')
        result=_evaluate(test_data,spec,selected['arm'],**selected['params'])
        for datum in test_data:
            graph,counts_case=_predict(datum,spec,selected['arm'],
                                    verify=True,**selected['params'])
            if datum['rec']['item']['row']['family']=='encounter':
                ct=m._count(datum['rec'],graph)
                allcase.append({'fold':fold,'case_id':str(datum['rec']['item']['row']['case_id']),
                     'base_id':str(datum['rec']['item']['row']['base_id']),
                     'tp':ct['tp'],'fp':ct['fp'],'fn':ct['fn'],'exact':ct['exact'],
                     'edits':counts_case})
        path=outdir/('fold-'+str(fold))
        _save(path,gates,experts,old.digest(p103),old.digest(p104),choice,
              selected,'base-disjoint-fold')
        loaded,manifest=load_candidate(path,experts,device,old.digest(p103),old.digest(p104))
        require(manifest['selected']==selected,'SELECTED_GATE_MANIFEST_DRIFT')
        again=_score_proposals(test_data[:1],loaded)
        before=_predict(test_data[0],spec,selected['arm'],**selected['params'])[0]
        after=_predict(again[0],spec,selected['arm'],**selected['params'])[0]
        require(torch.equal(before,after),'RELOADED_GATE_CHANGED_PREDICTION')
        folds.append({'fold':fold,'base_counts':counts,'training_eligibility':elig,
            'reference_0104':r104['folds'][fold]['selected'],
            'selected_on':'INNER_VALIDATION_ONLY','frozen_0103_recipe':choice,
            'chosen':selected,'validation_bakeoff':options,
            'control_0103':control,'selected':result,'manifest':str(path/'manifest.json'),
            'proposal_truncation':{k:sum(d['truncated'][k] for d in test_data)
                                   for k in HEADS}})
        emit('FOLD',{'fold':fold,'chosen':selected['arm'],'params':selected['params'],
             'encounter_exact':[control['by_family']['encounter']['exact'],
                                result['by_family']['encounter']['exact']],
             'encounter_f1':[control['by_family']['encounter']['edge_f1'],
                             result['by_family']['encounter']['edge_f1']],
             'overall_exact':[control['overall']['exact'],result['overall']['exact']],
             'edit_precision':result['edit']['precision'],
             'correct_edits':result['edit'].get('correct_edit',0),
             'wrong_edits':result['edit'].get('wrong_edit',0)})
    control=_metric_sum(folds,'control_0103');candidate=_metric_sum(folds,'selected')
    require(all(control[k]==r103['candidate'][k] for k in ('tp','fp','fn','exact')),
            'G2_0103_AGGREGATE_REPLAY_CHANGED')
    families={f:{'control':_metric_sum(folds,'control_0103',f),
                 'candidate':_metric_sum(folds,'selected',f)}
              for f in ('allocation','encounter')}
    gates_pass={
      'encounter_exact_progress':families['encounter']['candidate']['exact']>
                                 families['encounter']['control']['exact'],
      'encounter_f1_not_worse':families['encounter']['candidate']['edge_f1']>=
                              families['encounter']['control']['edge_f1'],
      'allocation_f1_within_1pp':families['allocation']['candidate']['edge_f1']>=
                                 families['allocation']['control']['edge_f1']-.01,
      'overall_f1_not_worse':candidate['edge_f1']>=control['edge_f1'],
      'overall_exact_not_worse':candidate['exact']>=control['exact']}
    aggregate_edits=Counter()
    for fold in folds:
        for name in ('correct_edit','wrong_edit','added','swap','capacity_blocked',
                     'protected_baseline','new_exact','lost_exact','gated'):
            aggregate_edits[name]+=fold['selected']['edit'].get(name,0)
    total=aggregate_edits['correct_edit']+aggregate_edits['wrong_edit']
    edit_precision=aggregate_edits['correct_edit']/total if total else 0.
    votes=Counter(fold['chosen']['arm'] for fold in folds)
    final_arm=sorted(votes,key=lambda a:(-votes[a],
                 min(i for i,f in enumerate(folds) if f['chosen']['arm']==a)))[0]
    final_params=next(f['chosen']['params'] for f in folds if f['chosen']['arm']==final_arm)
    # The full-fit model remains research-only. Never evaluate it on this
    # reused cross-validation fold; selection is strictly from inner VAL.
    full_bundle=q._full_fit_bundle(source,experts,r99,r100,device)
    full_base,base_manifest=q.load_candidate(source[8]/'raw-graph-reranking-0103'/
           'full-fit-research-only',experts,device,old.digest(p102))
    fullheads,head_manifest=s.load_candidate(source[8]/'failure-specialists-0104'/
           'full-fit-research-only',experts,device,old.digest(p103))
    require(set(fullheads)==set(HEADS) and
            head_manifest['baseline_0103']==base_manifest['selected'],
            'FULL_FIT_SPECIALIST_BASE_MISMATCH')
    fullchoice=base_manifest['selected']
    fullgates={}
    if final_arm!='frozen_0103':
        prepared,_=_prepare(records,experts,device,full_bundle,full_base,
                            fullchoice,fullheads,spec,supervised=True)
        for j,kind in enumerate(HEADS):
            fullgates[kind]=_train(prepared,kind,device,steps=steps,lr=lr,
                                    seed=seed+10305+j*131)
    _save(outdir/'full-fit-research-only',fullgates,experts,old.digest(p103),
         old.digest(p104),fullchoice,
         {'arm':final_arm,'params':final_params,
          'warning':'FULL_FIT_UNTESTED_ON_INDEPENDENT_DATA'},'research-only')
    report={'schema':SCHEMA,'accepted_on_reused_fit_cv':all(gates_pass.values()),
      'gates':gates_pass,'control_0103':control,'candidate':candidate,'by_family':families,
      'edit':{**dict(aggregate_edits),'precision':edit_precision},
      'folds':folds,'case_level_encounter':allcase,
      'source_0103_sha256':old.digest(p103),
      'source_0104_sha256':old.digest(p104),
      'reference_0104':r104['candidate'],
      'full_fit_recipe_from_inner_validation_majority':{'arm':final_arm,'params':final_params},
      'gold_used_only_for_training_validation_and_retrospective_scoring':True,
      'data_is_reused_fit_not_independent_certification':True,
      'research_only':True,'production_promoted':False,'g3_unlocked':False,
      'dev_used':False,'confirmation_used':False}
    outdir.mkdir(parents=True,exist_ok=True);old.write(report_path,report)
    emit('CROSS_VALIDATED',{'accepted':all(gates_pass.values()),'gates':gates_pass,
           'control_0103':control,'candidate':candidate,'by_family':families,
           'edits':report['edit'],'full_fit_recipe':final_arm})
    emit('SAVED',{'path':str(report_path),'sha256':old.digest(report_path),
                  'production_promoted':False,'g3_unlocked':False})


def selftest():
    from tools.nanojev_gamer_v1_g2_posttrain_graph_bakeoff_0088 import format_edge
    torch.manual_seed(105)
    n=8;objects=[[3*i,3*i+2,b.SPAN_KINDS[i%len(b.SPAN_KINDS)]] for i in range(n)]
    targets=torch.zeros(n,n,dtype=torch.long)
    targets[0,1]=1;targets[2,3]=2;targets[4,5]=1
    margins=torch.full((b.E,n,n,b.R),-1.)
    preds=torch.zeros((b.E,n,n),dtype=torch.long)
    margins[:,0,1,0]=1.4;margins[:,2,3,1]=-1.8
    margins[:,1,0,0]=2.4
    names=[b.m.ANCHOR]+['toy-expert-'+str(i) for i in range(1,b.E)]
    experts={name:{'sha256':'','path':''} for name in names}
    full_gold={format_edge(objects,i,j,int(targets[i,j]))
               for i,j in (targets>0).nonzero().tolist()}
    rec={'item':{'objects':objects,'targets':targets,'full_gold':full_gold,
                 'row':{'family':'encounter','base_id':'synthetic-105',
                        'case_id':'synthetic-105-base'},'g1_exact':True},
         'expert_margins':margins,
         'predictions':{name:preds[i] for i,name in enumerate(names)}}
    bundle={'router':m.CompetenceRouter(b.E).eval(),'specialist':None,
           'verifier':b.EdgeVerifier().eval(),'policy':'pin_ungated',
           's_threshold':.7,'v_threshold':.5,'selected_variant':'router_assembly'}
    base_models={'raw_local':q.RawGraphReranker(False).eval()}
    choice={'arm':'raw_local','params':{'blend':1.,'offset':0.}}
    spec={'method':'joint','threshold':0.,'out_caps':{},'in_caps':{}}
    heads={kind:s.FailureSpecialist().eval() for kind in HEADS}
    prepared,stats=_prepare([rec],experts,'cpu',bundle,base_models,choice,heads,
                            spec,supervised=True)
    require(stats['suppressed_positive']>0 and stats['weak_positive']>0,
            'SELFTEST_MISSING_SUPERVISION')
    gates={kind:_train(prepared,kind,'cpu',steps=2,lr=.002,seed=105+j)
           for j,kind in enumerate(HEADS)}
    cached=_score_proposals(prepared,gates)
    for arm in ARMS:
        pred,_=_predict(cached[0],spec,arm,threshold=.65,floor=0.,swap_gap=.5,
                       verify=True)
        require(pred.shape==targets.shape,'SELFTEST_DECODE_SHAPE')
    # Explicit invariants: no unaccepted score changes can alter existing
    # edges, even when a specialist strongly prefers an alternative.
    fake={**cached[0], 'baseline':torch.zeros_like(targets)}
    fake['baseline'][0,1]=1
    fake['proposals']={'suppressed':[(.999,10.,4,5,1,'suppressed',.999),
                                    (.999,10.,6,7,1,'suppressed',.999)],
                       'weak':[(.999,10.,0,1,2,'weak',.999)]}
    graph,st=_predict(fake,spec,'dual_add',threshold=.9,floor=0.,verify=True)
    require(graph[0,1]==1 and graph[4,5]==1 and graph[6,7]==1,
            'ADDITIVE_PROTECTION_OR_NEW_EDGE_FAILED')
    require(st['protected_baseline']>0 and st['added']==2,
            'CORRECTION_ACCOUNTING_FAILED')
    # A source already using its two-edge cap to the same destination kind
    # must reject a third. Separate homogeneous kinds isolate this rule.
    homog=[[3*i,3*i+2,"action"] for i in range(n)]
    capped=graph.clone();capped[4,5]=0;capped[4,6]=1;capped[4,7]=1
    require(not _capacity_ok(capped,homog,spec,4,5,1),
            'CAPACITY_GATE_FAILED_TO_PROTECT')
    # Gold-blind inference: remove all training labels and case metadata.
    blind={'item':{'objects':objects},'expert_margins':margins,
           'predictions':rec['predictions']}
    earlier=_features(rec,experts,'cpu',bundle,base_models,choice,heads,spec)
    later=_features(blind,experts,'cpu',bundle,base_models,choice,heads,spec)
    require(torch.equal(earlier['baseline'],later['baseline']) and
        all(torch.equal(earlier['kinds'][k]['x'],later['kinds'][k]['x'])
            for k in HEADS),'GOLD_LEAK_IN_FEATURES')
    allocations=[]
    for family in ('allocation','encounter'):
        for group in range(6):
            row={'family':family,'base_id':f'toy-{family}-{group}',
                 'case_id':f'toy-{family}-{group}-base'}
            allocations.append({**rec,'item':{**rec['item'],'row':row}})
    synthetic=[]
    for fold,(index,(train,val,test),counts) in enumerate(
            tr._split_by_base(allocations,3,SEED)):
        require(index==fold and all(counts.values()),'THREE_FOLD_SYNTHETIC_SPLIT')
        train_cache,_=_prepare(train,experts,'cpu',bundle,base_models,choice,heads,
                               spec,supervised=True)
        learned={kind:_train(train_cache,kind,'cpu',steps=2,lr=.002,
                            seed=105+fold*11+j) for j,kind in enumerate(HEADS)}
        val_cache=_score_proposals(_prepare(val,experts,'cpu',bundle,base_models,choice,
                                            heads,spec,supervised=False)[0],learned)
        chosen,options,_=_select(val_cache,spec)
        require(len(options)==len(list(_options())),'BAKEOFF_GRID_MISMATCH')
        test_cache=_score_proposals(_prepare(test,experts,'cpu',bundle,base_models,choice,
                                             heads,spec,supervised=False)[0],learned)
        synthetic.append({'control':_evaluate(test_cache,spec,'frozen_0103'),
                          'selected':_evaluate(test_cache,spec,
                                  chosen['arm'],**chosen['params'])})
    require(_metric_sum(synthetic,'selected')['cases']==len(allocations),
            'SYNTHETIC_THREE_FOLD_AGGREGATION_FAILED')
    with tempfile.TemporaryDirectory(prefix='g2_0105_') as temp:
        for i,entry in enumerate(experts.values()):
            path=Path(temp)/('expert-'+str(i)+'.bin')
            path.write_bytes(str(i).encode())
            entry['path']=str(path);entry['sha256']=old.digest(path)
        folder=Path(temp)/'candidate'
        _save(folder,gates,experts,'toy-0103','toy-0104',choice,
              {'arm':'dual_add','params':{}},'selftest')
        loaded,_=load_candidate(folder,experts,'cpu','toy-0103','toy-0104')
        oldc=_score_proposals(prepared,gates)[0]
        newc=_score_proposals(prepared,loaded)[0]
        for arm in ARMS:
            require(torch.equal(_predict(oldc,spec,arm)[0],
                                      _predict(newc,spec,arm)[0]),
                    'CHECKPOINT_ROUNDTRIP_'+arm)
        first=next(iter(experts.values()))['path']
        Path(first).write_bytes(b'tampered')
        try:load_candidate(folder,experts,'cpu','toy-0103','toy-0104')
        except ValueError as err:
            require('EXPERT_CHECKPOINT_MUTATED' in str(err),'WRONG_TAMPER_REJECTION')
        else:raise ValueError(TAG+'_TAMPER_UNDETECTED')
    emit('SELFTEST',{'both_gates_have_finite_gradients':True,
       'suppressed_and_weak_positive_supervision':True,
       'baseline_edges_preserved_by_additive_policy':True,
       'graph_capacities_enforced':True,'real_structured_decoder_replay':True,
       'gold_free_inference':True,'candidate_edit_precision_reported':True,
       'validation_only_selection':True,'three_fold_pipeline':True,
       'checkpoint_roundtrip':True,'expert_tamper_rejected':True,
       'production_unchanged':True})


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',nargs='?',choices=('audit','selftest','run'),
                        default='run')
    parser.add_argument('--root',default=old.ROOT_DEFAULT)
    parser.add_argument('--output')
    parser.add_argument('--device',default='cuda')
    parser.add_argument('--steps',type=int,default=240)
    parser.add_argument('--lr',type=float,default=.0015)
    parser.add_argument('--seed',type=int,default=SEED)
    parser.add_argument('--force',action='store_true')
    args=parser.parse_args(argv)
    if args.command=='audit':return audit(args.root,args.output)
    if args.command=='selftest':return selftest()
    return run(args.root,args.output,args.device,args.seed,args.steps,args.lr,args.force)


if __name__=='__main__':main()
