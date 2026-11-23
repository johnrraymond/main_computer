"""G2-0100: frozen G2-0099 control versus continued router, edge verification,
and a newly trained encounter-error specialist, alone and together.

Execute from main_computer/nanojev:
  python -u -m tools.nanojev_gamer_v1_g2_encounter_bakeoff_0100 audit
  python -u -m tools.nanojev_gamer_v1_g2_encounter_bakeoff_0100 selftest
  python -u -m tools.nanojev_gamer_v1_g2_encounter_bakeoff_0100 run --device cuda

All selection uses inner validation on fit-only, base-disjoint folds identical
with G2-0099. Every previously saved G2-0099 fold router is a frozen control.
No G1/G2 expert updates, no original checkpoint updates, no dev/confirmation,
no production promotion, no G3 unlock. Final full-fit candidate is research-only.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import copy
import json
from pathlib import Path
import random
import statistics
import tempfile

import torch
from torch import nn
from torch.nn import functional as F
from safetensors.torch import save_file, load_file

from tools import nanojev_gamer_v1_g2_router_continuation_0099 as parent
from tools import nanojev_gamer_v1_g2_router_trainability_0098 as tr
from tools import nanojev_gamer_v1_g2_competence_routing_0098 as m
from tools import nanojev_gamer_v1_g2_router_forensics_0098 as fx
from tools import nanojev_gamer_v1_g2_fuzz_and_correct_0095 as old
from tools.nanojev_gamer_v1_graph_head import SPAN_KINDS, BIND_CLASSES

TAG='G2_0100_ENCOUNTER'
SCHEMA='nanojev.g2.encounter-graph-bakeoff-0100.v1'
E=7
R=len(BIND_CLASSES)-1
S=len(SPAN_KINDS)
VARIANTS=('frozen_0099','router_continued','assembly_only','specialist_only',
          'router_assembly','specialist_assembly','router_specialist','all_three')
SPECIALIST_THRESHOLDS=(.50,.70,.85)
VETO_THRESHOLDS=(.35,.50,.65,.80)

def emit(name,value):
    print(TAG+'_'+name+' '+json.dumps(value,sort_keys=True,allow_nan=False),flush=True)

def require(value,reason):
    if not value:raise ValueError(TAG+'_'+reason)

def _featurize(objects,margins,predictions):
    """Inference-only semantic/object and seven frozen expert signals; no gold."""
    e,n,n2,r=margins.shape
    require(e==E and n==n2 and r==R and len(objects)==n and
            tuple(predictions.shape)==(E,n,n),'FEATURE_SHAPE_DRIFT')
    device=margins.device
    dt=margins.dtype
    kind=torch.tensor([SPAN_KINDS.index(obj[2]) for obj in objects],device=device)
    onehot=F.one_hot(kind,S).to(dt)
    starts=torch.tensor([obj[0] for obj in objects],device=device,dtype=dt)
    ends=torch.tensor([obj[1] for obj in objects],device=device,dtype=dt)
    scale=(ends.max()-starts.min()).clamp_min(1.)
    width=(ends-starts).clamp_min(0.)
    left=onehot[:,None].expand(n,n,S)
    right=onehot[None,:].expand(n,n,S)
    diff=(starts[:,None]-starts[None,:])/scale
    local=torch.stack((diff,diff.abs(),(width[:,None]-width[None,:])/scale,
                       (width[:,None]/scale).expand(n,n),
                       (width[None,:]/scale).expand(n,n)),dim=-1)
    feats=torch.cat((left,right,local,
        margins.permute(1,2,0,3).reshape(n,n,E*R).tanh(),
        F.one_hot(predictions.long(),R+1).permute(1,2,0,3).reshape(n,n,E*(R+1)).to(dt)),dim=-1)
    return feats

D=2*S+5+E*R+E*(R+1)

class EncounterSpecialist(nn.Module):
    """Learns missing relationship evidence from inference-time expert disagreements."""
    def __init__(self):
        super().__init__()
        self.net=nn.Sequential(nn.Linear(D,96),nn.Tanh(),nn.Linear(96,96),nn.Tanh(),
                               nn.Linear(96,R+1))
    def forward(self,objects,margins,predictions):
        return self.net(_featurize(objects,margins,predictions))

class EdgeVerifier(nn.Module):
    """Learned false-edge veto. No graph gold supplied at inference."""
    def __init__(self):
        super().__init__()
        self.net=nn.Sequential(nn.Linear(D+R+1+3,64),nn.Tanh(),
                               nn.Linear(64,32),nn.Tanh(),nn.Linear(32,1))
    def forward(self,objects,margins,predictions,selected,score):
        feature=_featurize(objects,margins,predictions)
        label=F.one_hot(selected.long(),R+1).to(feature.dtype)
        probabilities=score.softmax(-1)
        sel_score=probabilities.gather(-1,selected[...,None].long()).squeeze(-1)
        support=(predictions==selected[None]).float().mean(0)
        disagreement=(predictions!=predictions[:1]).any(0).float()
        extra=torch.stack((sel_score,support,disagreement),dim=-1)
        return self.net(torch.cat((feature,label,extra),dim=-1)).squeeze(-1)


def _inputs(rec,experts,device):
    require(list(experts)==list(rec['predictions']),'PREDICTION_ORDER_DRIFT')
    return m._predict_inputs(rec,experts,device)


def _gold_mask(rec,device):
    gold=rec['item']['targets'].long().to(device)
    valid=~torch.eye(len(gold),dtype=torch.bool,device=device)
    return gold,valid


def _specialist_loss(model,rec,experts,device):
    margins,pred=_inputs(rec,experts,device)
    gold,valid=_gold_mask(rec,device)
    logits=model(rec['item']['objects'],margins,pred)
    relevant=valid&((gold>0)|(pred>0).any(0))
    correct=(pred==gold[None]).any(0)
    unknown=relevant&~correct
    # Train true edge types and hard negatives, with extra focus on relationships
    # that no frozen head knows. Training-family label is used for loss weight
    # only; it is NOT available to forward() or inference.
    weight=relevant.float()*(1.+2.*(gold>0).float()+4.*unknown.float())
    if rec['item']['row']['family']=='encounter':weight=weight*2.
    require(bool(weight.sum()>0),'EMPTY_SPECIALIST_SUPERVISION')
    loss=F.cross_entropy(logits.reshape(-1,R+1),gold.reshape(-1),reduction='none')
    return (loss.view_as(gold)*weight).sum()/weight.sum()


def _frozen_scores(router,rec,experts,device,policy):
    require(not router.training,'ROUTER_NOT_FROZEN')
    margins,pred=_inputs(rec,experts,device)
    with torch.no_grad():
        _,comp,meta=router(rec['item']['objects'],margins,pred)
        kind,threshold,gap=parent.POLICIES[policy]
        score,swaps,_=fx._route_scores(margins,pred,comp,
            meta['disagreement'],kind,threshold,gap,strength=12.)
    return score, margins, pred, int(swaps.sum())


def _spec_override(score,objects,margins,pred,specialist,threshold):
    with torch.no_grad():
        logits=specialist(objects,margins,pred)
        probs=logits.softmax(-1)
        winner=probs.argmax(-1)
        winprob=probs.max(-1).values
        # A confident specialist is allowed to veto *or* add an edge.
        # Its decision is expressed as a locked label, as in G2-0099.
        existing=score.argmax(-1)
        mask=(winner!=existing)&(winprob>=threshold)
        forced=torch.full_like(score,-12.)
        forced.scatter_(-1,winner[...,None],12.)
        return torch.where(mask[...,None],forced,score),int(mask.sum())


def _infer_models(rec,experts,spec,device,router,policy,*,specialist=None,
                  verifier=None,spec_threshold=.70,veto_threshold=.50):
    """No gold, row/family/ID, or authored edges are read here."""
    require(not router.training,'ROUTER_TRAINING_AT_INFERENCE')
    if specialist is not None:require(not specialist.training,'SPECIALIST_NOT_FROZEN')
    if verifier is not None:require(not verifier.training,'VERIFIER_NOT_FROZEN')
    score,margins,pred,swaps=_frozen_scores(router,rec,experts,device,policy)
    item={'objects':rec['item']['objects']}
    spec_swaps=0
    if specialist is not None:
        score,spec_swaps=_spec_override(score,item['objects'],margins,pred,
                                        specialist,spec_threshold)
    with torch.no_grad():
        decoded,_=old._decode_matrix(item,score,spec)
        decoded=decoded.to(device).long()
        veto=0
        if verifier is not None:
            probs=verifier(item['objects'],margins,pred,decoded,score).sigmoid()
            valid=~torch.eye(len(decoded),dtype=torch.bool,device=device)
            remove=valid&(decoded>0)&(probs<veto_threshold)
            decoded=decoded.masked_fill(remove,0)
            veto=int(remove.sum())
    return decoded.cpu(),{'router_swaps':swaps,'specialist_overrides':spec_swaps,
                          'vetoed_edges':veto}


def _verifier_examples(verifier,rec,experts,spec,device,routers,specialist):
    losses=[]
    gold,valid=_gold_mask(rec,device)
    for router,policy,use_specialist in routers:
        score,margins,pred,_=_frozen_scores(router,rec,experts,device,policy)
        if use_specialist:
            score,_=_spec_override(score,rec['item']['objects'],margins,pred,specialist,.70)
        with torch.no_grad():
            decoded,_=old._decode_matrix({'objects':rec['item']['objects']},score,spec)
        decoded=decoded.to(device).long()
        mask=valid&(decoded>0)
        if not bool(mask.any()):continue
        logit=verifier(rec['item']['objects'],margins,pred,decoded,score)
        y=(decoded==gold).float()
        # Correct and spurious predictions must both affect the verifier.
        weight=mask.float()*(1.+y*1.5)
        if rec['item']['row']['family']=='encounter':weight*=2.
        loss=F.binary_cross_entropy_with_logits(logit,y,reduction='none')
        losses.append((loss*weight).sum()/weight.sum().clamp_min(1.))
    require(bool(losses),'EMPTY_VERIFIER_SUPERVISION')
    return torch.stack(losses).mean()


def _selector_update(router,rec,experts,device):
    return parent._hard_correction_loss(router,rec,experts,device,
                                        ref_weight=2.,hard_weight=2.)


def _train_fold(train,experts,spec,device,starting_router,policy,*,seed,
                router_steps,specialist_steps,verifier_steps,lr):
    require(train,'EMPTY_TRAIN_FOLD')
    torch.manual_seed(seed)
    router=copy.deepcopy(starting_router).to(device)
    router.train()
    opt=torch.optim.AdamW(router.parameters(),lr=lr*.35,weight_decay=.0001)
    rng=random.Random(seed)
    for step in range(1,router_steps+1):
        batch=rng.sample(train,min(4,len(train)))
        opt.zero_grad(set_to_none=True)
        losses=[_selector_update(router,r,experts,device) for r in batch]
        loss=torch.stack([l for l in losses if l is not None]).mean()
        require(bool(torch.isfinite(loss)),'NONFINITE_ROUTER_LOSS')
        loss.backward();torch.nn.utils.clip_grad_norm_(router.parameters(),1.);opt.step()
    router.eval()
    torch.manual_seed(seed+31)
    specialist=EncounterSpecialist().to(device)
    opt_s=torch.optim.AdamW(specialist.parameters(),lr=lr,weight_decay=.0001)
    for step in range(1,specialist_steps+1):
        batch=rng.sample(train,min(4,len(train)))
        opt_s.zero_grad(set_to_none=True)
        loss=torch.stack([_specialist_loss(specialist,r,experts,device) for r in batch]).mean()
        require(bool(torch.isfinite(loss)),'NONFINITE_SPECIALIST_LOSS')
        loss.backward();torch.nn.utils.clip_grad_norm_(specialist.parameters(),1.);opt_s.step()
    specialist.eval()
    torch.manual_seed(seed+61)
    verifier=EdgeVerifier().to(device)
    opt_v=torch.optim.AdamW(verifier.parameters(),lr=lr,weight_decay=.0001)
    for step in range(1,verifier_steps+1):
        batch=rng.sample(train,min(3,len(train)))
        opt_v.zero_grad(set_to_none=True)
        loss=torch.stack([_verifier_examples(verifier,r,experts,spec,device,[
            (starting_router,policy,False),(router,policy,False),
            (starting_router,policy,True)],specialist) for r in batch]).mean()
        require(bool(torch.isfinite(loss)),'NONFINITE_VERIFIER_LOSS')
        loss.backward();torch.nn.utils.clip_grad_norm_(verifier.parameters(),1.);opt_v.step()
    verifier.eval()
    return router,specialist,verifier


def _policy_options():
    options=[]
    for variant in VARIANTS:
        for s_threshold in (SPECIALIST_THRESHOLDS if 'specialist' in variant or
                             variant=='all_three' else (.70,)):
            for v_threshold in (VETO_THRESHOLDS if 'assembly' in variant or
                                 variant=='all_three' else (.50,)):
                options.append((variant,s_threshold,v_threshold))
    return options


def _candidate_args(variant, frozen, continued, specialist, verifier):
    use_router=variant in ('router_continued','router_assembly',
                           'router_specialist','all_three')
    use_specialist=variant in ('specialist_only','specialist_assembly',
                               'router_specialist','all_three')
    use_verifier=variant in ('assembly_only','router_assembly',
                             'specialist_assembly','all_three')
    return continued if use_router else frozen, specialist if use_specialist else None, \
           verifier if use_verifier else None


def _evaluate(records,experts,spec,device,router,policy,*,specialist=None,
              verifier=None,spec_threshold=.70,veto_threshold=.50):
    by_fam=defaultdict(Counter)
    total=Counter()
    missing_expert=Counter()
    for rec in records:
        result,meta=_infer_models(rec,experts,spec,device,router,policy,
           specialist=specialist,verifier=verifier,spec_threshold=spec_threshold,
           veto_threshold=veto_threshold)
        count=m._count(rec,result)
        family=rec['item']['row']['family']
        baseline=rec['predictions'][m.ANCHOR].cpu()
        gold=rec['item']['targets'].cpu()
        valid=~torch.eye(len(gold),dtype=torch.bool)
        changed=(result!=baseline)&valid
        repaired=int((changed&(result==gold)).sum())
        harmed=int((changed&(baseline==gold)&(result!=gold)).sum())
        marg,preds=_inputs(rec,experts,device)
        allwrong=(preds!=gold.to(device)[None]).all(0).cpu()&valid
        newly_correct=int((allwrong&(result==gold)).sum())
        group=Counter(count)
        group.update(repaired=repaired,harmed=harmed,
            known_unrecoverable_corrected=newly_correct,
            router_swaps=meta['router_swaps'],
            specialist_overrides=meta['specialist_overrides'],
            vetoed_edges=meta['vetoed_edges'])
        by_fam[family].update(group);total.update(group)
    return {'overall':m._metric(total),'by_family':{k:m._metric(v) for k,v in by_fam.items()},
        'repaired':total['repaired'],'harmed':total['harmed'],
        'unrecoverable_corrected':total['known_unrecoverable_corrected'],
        'swaps':total['router_swaps'],'specialist_overrides':total['specialist_overrides'],
        'vetoed_edges':total['vetoed_edges']}


def _rank(result):
    graph=result['overall']
    enc=result['by_family'].get('encounter',{})
    allocation=result['by_family'].get('allocation',{})
    # Predeclared: exact encounter graphs first, then total exact, encounter F1,
    # allocation F1, then fewer harms. Do not select from outer-test results.
    return (enc.get('exact',0),graph['exact'],enc.get('edge_f1',0.),
            allocation.get('edge_f1',0.),-result['harmed'])


def _select_on_val(val,experts,spec,device,frozen,policy,continued,specialist,verifier):
    entries=[]
    for variant,s_thresh,v_thresh in _policy_options():
        router,sp,ve=_candidate_args(variant,frozen,continued,specialist,verifier)
        stat=_evaluate(val,experts,spec,device,router,policy,specialist=sp,
            verifier=ve,spec_threshold=s_thresh,veto_threshold=v_thresh)
        entries.append(((_rank(stat),-VARIANTS.index(variant),-s_thresh,-v_thresh),
                        variant,s_thresh,v_thresh,stat))
    _,variant,s_thresh,v_thresh,stats=max(entries,key=lambda z:z[0])
    return (variant,s_thresh,v_thresh,stats,
            [{'variant':v,'specialist_threshold':s,'veto_threshold':w,
              'rank':list(item[0]),'encounter_exact':stats['by_family'].get('encounter',{}).get('exact',0),
              'encounter_f1':stats['by_family'].get('encounter',{}).get('edge_f1',0.),
              'allocation_f1':stats['by_family'].get('allocation',{}).get('edge_f1',0.)}
              for item,v,s,w,stats in entries])


def _sum_graph(rows,key):
    counter=Counter()
    for row in rows:
        val=row[key]['overall']
        for f in ('tp','fp','fn','cases','exact','g1_exact'):
            counter[f]+=val[f]
    return m._metric(counter)


def _sum_family(rows,key,family):
    counter=Counter()
    for row in rows:
        val=row[key]['by_family'][family]
        for f in ('tp','fp','fn','cases','exact','g1_exact'):
            counter[f]+=val[f]
    return m._metric(counter)


def _base_source(root,output):
    source,experts=parent._source(root,output)
    dest=source[8]/'router-continuation-0099'
    report_file=dest/'joint-training-and-assembly.json'
    require(report_file.exists(),'MISSING_COMPLETED_0099_REPORT')
    report=old.read(report_file)
    require(report['schema']==parent.SCHEMA and report['acceptance_pass'] is True,
            '0099_SOURCE_NOT_ACCEPTED')
    require(len(report['folds'])==3,'0099_FOLD_COUNT_CHANGED')
    require(report['candidate']['arm'] in parent.ARMS, '0099_UNKNOWN_BASE_RECIPE')
    require(report['confirmation_used'] is False and report['dev_used'] is False,
            '0099_PRIOR_DATA_POLICY_DRIFT')
    return source,experts,report,dest


def _load_fold(fold,report,experts,device,expect_keys):
    record=report['folds'][fold]
    require(record['fold']==fold and record['base_counts']==expect_keys,
            '0099_BASE_SPLIT_DRIFT')
    path=Path(record['checkpoint'])
    require(old.digest(path)==record['checkpoint_sha256'],'0099_FOLD_HASH_DRIFT')
    model,policy=parent.load_frozen_adapter(path,experts,device)
    require(policy==record['policy'],'0099_POLICY_DRIFT')
    return model,policy


def _save_models(folder,models,source_report,experts,metadata,role):
    folder.mkdir(parents=True,exist_ok=True)
    records={}
    for name,model in models.items():
        path=folder/(name+'.safetensors')
        save_file({k:v.detach().contiguous().cpu() for k,v in model.state_dict().items()},str(path))
        records[name]={'path':str(path),'sha256':old.digest(path)}
    manifest={'schema':SCHEMA,'role':role,'research_only':True,
        'production_promoted':False,'g3_unlocked':False,'dev_used':False,
        'confirmation_used':False,'frozen_0099_sha256':source_report['candidate']['sha256'],
        'expert_order':list(experts),
        'expert_hashes':{n:entry['sha256'] for n,entry in experts.items()},
        'models':records,'configuration':metadata}
    old.write(folder/'manifest.json',manifest)
    return manifest


def load_candidate(folder,experts,device,source_hash):
    folder=Path(folder)
    manifest=old.read(folder/'manifest.json')
    require(manifest['schema']==SCHEMA and manifest['research_only'] and not
            manifest['production_promoted'] and not manifest['g3_unlocked'],
            'CANDIDATE_MANIFEST_DRIFT')
    require(manifest['frozen_0099_sha256']==source_hash,'0099_ANCESTOR_DRIFT')
    require(manifest['expert_order']==list(experts),'EXPERT_AXIS_DRIFT')
    require(all(manifest['expert_hashes'][name]==v['sha256'] and
            old.digest(Path(v['path']))==v['sha256'] for name,v in experts.items()),
            'FROZEN_EXPERT_MUTATED')
    constructors={'router':lambda:m.CompetenceRouter(len(experts)),
                  'specialist':EncounterSpecialist,'verifier':EdgeVerifier}
    loaded={}
    for name,item in manifest['models'].items():
        require(old.digest(Path(item['path']))==item['sha256'],
                'CANDIDATE_CHECKPOINT_MUTATED_'+name)
        model=constructors[name]().to(device)
        model.load_state_dict(load_file(item['path'],device='cpu'),strict=True)
        model.eval();loaded[name]=model
    return loaded,manifest


def audit(root=old.ROOT_DEFAULT,output=None):
    source,experts,report,dest=_base_source(root,output)
    folds=[]
    for fold,record in enumerate(report['folds']):
        require(record['fold']==fold,'0099_RECORD_FOLD_ORDER')
        require(old.digest(Path(record['checkpoint']))==record['checkpoint_sha256'],
                '0099_FOLD_FILE_HASH_DRIFT')
        folds.append({'fold':fold,'base_counts':record['base_counts'],
             'checkpoint_sha256':record['checkpoint_sha256']})
    emit('AUDIT',{'ready':True,'frozen_0099_expert_order':list(experts),
        'folds':folds,'arms':list(VARIANTS),'source_0099_acceptance':True,
        'new_trainable_modules':['router_continuation','encounter_specialist','edge_verifier'],
        'selection':'INNER_VAL_ONLY_EXACT_ENCOUNTER_FIRST',
        'new_dev_or_confirmation_used':False,'production_promotion':False})


def run(root=old.ROOT_DEFAULT,output=None,device='cuda',seed=990099,
        router_steps=250,specialist_steps=450,verifier_steps=300,
        lr=.002,force=False):
    require(device=='cpu' or (device.startswith('cuda') and torch.cuda.is_available()),
            'CUDA_NOT_AVAILABLE')
    require(20<=router_steps<=3000 and 20<=specialist_steps<=3000 and
            20<=verifier_steps<=3000 and 0<lr<.1,'TRAINING_PARAMETERS_INVALID')
    source,experts,report,prior_folder=_base_source(root,output)
    destination=source[8]/'encounter-bakeoff-0100'
    path=destination/'result.json'
    require(force or not path.exists(),'REPORT_EXISTS_USE_FORCE_TO_REPLACE_ONLY_0100')
    emit('START',{'device':device,'folds':3,'continued_router_steps':router_steps,
        'specialist_steps':specialist_steps,'verifier_steps':verifier_steps,
        'arms':list(VARIANTS),'outer_test_only_after_val_selection':True,
        'reference':'FROZEN_G2_0099_FOLD_CHECKPOINTS',
        'test_cohort_is_reused_fit_not_independent_certification':True})
    records,spec=parent._records(source,experts,device)
    all_rows=[]
    folds_iter=list(tr._split_by_base(records,3,seed))
    for fold,parts,counts in folds_iter:
        train,val,test=parts
        frozen,policy=_load_fold(fold,report,experts,device,counts)
        # Record frozen G2-0099 held-out fold results before any training.
        anchor=_evaluate(test,experts,spec,device,frozen,policy)
        prior_test=report['folds'][fold]['test_graph']['overall']
        require(all(anchor['overall'][k]==prior_test[k] for k in ('tp','fp','fn','exact')),
                '0099_FROZEN_CONTROL_REPRODUCTION_FAILED')
        continued,specialist,verifier=_train_fold(train,experts,spec,device,
            frozen,policy,seed=seed+fold*1009,
            router_steps=router_steps,specialist_steps=specialist_steps,
            verifier_steps=verifier_steps,lr=lr)
        variant,s_thresh,v_thresh,val_results,selection_table=_select_on_val(
            val,experts,spec,device,frozen,policy,continued,specialist,verifier)
        # Outer fold is not opened for candidate ranking.
        chosen_router,sp,ve=_candidate_args(variant,frozen,continued,specialist,verifier)
        test_results=_evaluate(test,experts,spec,device,chosen_router,policy,
            specialist=sp,verifier=ve,spec_threshold=s_thresh,
            veto_threshold=v_thresh)
        models={'router':continued,'specialist':specialist,'verifier':verifier}
        model_dir=destination/('fold-'+str(fold))
        mf=_save_models(model_dir,models,report,experts,
            {'variant':variant,'router_policy':policy,'specialist_threshold':s_thresh,
             'veto_threshold':v_thresh,'base_counts':counts},role='cross_validation')
        new_models,_=load_candidate(model_dir,experts,device,report['candidate']['sha256'])
        # Check reloaded candidate output and no wrong expert order.
        before,_=_infer_models(test[0],experts,spec,device,chosen_router,policy,
           specialist=sp,verifier=ve,spec_threshold=s_thresh,veto_threshold=v_thresh)
        frozen_copy=frozen if chosen_router is frozen else new_models['router']
        after,_=_infer_models(test[0],experts,spec,device,frozen_copy,policy,
           specialist=new_models['specialist'] if sp is not None else None,
           verifier=new_models['verifier'] if ve is not None else None,
           spec_threshold=s_thresh,veto_threshold=v_thresh)
        require(torch.equal(before,after),'FOLD_CHECKPOINT_RELOAD_DRIFT')
        row={'fold':fold,'base_counts':counts,'selected_on':'INNER_VALIDATION_ONLY',
             'selected_variant':variant,'specialist_threshold':s_thresh,
             'veto_threshold':v_thresh,'g2_0099_control':anchor,
             'validation':val_results,'outer_test':test_results,
             'validation_bakeoff':selection_table,'manifest':str(model_dir/'manifest.json')}
        all_rows.append(row)
        emit('FOLD',{'fold':fold,'variant':variant,
            'encounter_exact':[anchor['by_family']['encounter']['exact'],
                               test_results['by_family']['encounter']['exact']],
            'encounter_f1':[anchor['by_family']['encounter']['edge_f1'],
                            test_results['by_family']['encounter']['edge_f1']],
            'allocation_f1':[anchor['by_family']['allocation']['edge_f1'],
                             test_results['by_family']['allocation']['edge_f1']],
            'newly_recovered_all_experts_wrong':test_results['unrecoverable_corrected'],
            'graph_harm':test_results['harmed']})
    baseline=_sum_graph(all_rows,'g2_0099_control')
    result=_sum_graph(all_rows,'outer_test')
    byfamily={family:{'baseline':_sum_family(all_rows,'g2_0099_control',family),
                      'candidate':_sum_family(all_rows,'outer_test',family)}
              for family in ('encounter','allocation')}
    gates={
        'encounter_exact_progress':byfamily['encounter']['candidate']['exact']>
             byfamily['encounter']['baseline']['exact'],
        'encounter_f1_not_worse':byfamily['encounter']['candidate']['edge_f1']>=
             byfamily['encounter']['baseline']['edge_f1'],
        'allocation_f1_within_1pp':byfamily['allocation']['candidate']['edge_f1']>=
             byfamily['allocation']['baseline']['edge_f1']-.01,
        'overall_f1_not_worse':result['edge_f1']>=baseline['edge_f1'],
        'overall_exact_not_worse':result['exact']>=baseline['exact']}
    accepted=all(gates.values())
    selected_counts=Counter(row['selected_variant'] for row in all_rows)
    chosen=max(VARIANTS,key=lambda v:(selected_counts[v],-VARIANTS.index(v)))
    threshold_s=statistics.median(row['specialist_threshold'] for row in all_rows
                    if row['selected_variant']==chosen)
    threshold_v=statistics.median(row['veto_threshold'] for row in all_rows
                    if row['selected_variant']==chosen)
    # Final full-fit research checkpoint; not certified by fold test scores.
    final_frozen,final_policy=parent.load_frozen_adapter(
        Path(report['candidate']['path']),experts,device)
    models=_train_fold(records,experts,spec,device,final_frozen,final_policy,
        seed=seed+9100,router_steps=router_steps,
        specialist_steps=specialist_steps,verifier_steps=verifier_steps,lr=lr)
    final_dir=destination/'full-fit-research-only'
    full=_save_models(final_dir,dict(zip(('router','specialist','verifier'),models)),
        report,experts,{'variant':chosen,'router_policy':final_policy,
                        'specialist_threshold':threshold_s,
                        'veto_threshold':threshold_v,'base_count':68},
        role='full_fit_unvalidated_candidate')
    loaded,config=load_candidate(final_dir,experts,device,report['candidate']['sha256'])
    route,spec_m,verify=_candidate_args(chosen,final_frozen,*models)
    before,_=_infer_models(records[0],experts,spec,device,route,final_policy,
         specialist=spec_m,verifier=verify,spec_threshold=threshold_s,
         veto_threshold=threshold_v)
    new_route=final_frozen if route is final_frozen else loaded['router']
    after,_=_infer_models(records[0],experts,spec,device,new_route,final_policy,
         specialist=loaded['specialist'] if spec_m else None,
         verifier=loaded['verifier'] if verify else None,
         spec_threshold=threshold_s,veto_threshold=threshold_v)
    require(torch.equal(before,after),'FULL_FIT_RELOAD_DRIFT')
    summary={'schema':SCHEMA,'accepted_on_reused_fit_cv':accepted,'gates':gates,
        'g2_0099_control':baseline,'new_cv_result':result,'by_family':byfamily,
        'folds':all_rows,'selected_recipe_from_inner_val_majority':chosen,
        'full_fit_manifest':str(final_dir/'manifest.json'),
        'full_fit_checkpoint_not_certified':True,'dev_used':False,
        'confirmation_used':False,'production_promoted':False,
        'g3_unlocked':False,'reused_fit_data_not_independent_certification':True}
    destination.mkdir(parents=True,exist_ok=True)
    old.write(path,summary)
    emit('CROSS_VALIDATED',{'acceptance_pass':accepted,'gates':gates,
        'control_graph':baseline,'new_graph':result,'by_family':byfamily,
        'full_fit_recipe':chosen})
    emit('SAVED',{'path':str(path),'sha256':old.digest(path),
       'research_only':True,'candidate_manifest':str(final_dir/'manifest.json')})
    return summary


def selftest():
    torch.manual_seed(100)
    n=4
    objects=[[0,1,SPAN_KINDS[0]],[2,3,SPAN_KINDS[0]],
             [4,6,SPAN_KINDS[1]],[7,9,SPAN_KINDS[0]]]
    margins=torch.randn(E,n,n,R)
    labels=torch.randint(0,R+1,(E,n,n))
    model=m.CompetenceRouter(E);model.eval()
    specialist=EncounterSpecialist();verifier=EdgeVerifier()
    gold=torch.randint(0,R+1,(n,n));gold.fill_diagonal_(0)
    # Minimal independent frozen decoder fixture uses real structured joint path.
    from tools.nanojev_gamer_v1_g2_posttrain_graph_bakeoff_0088 import format_edge
    spec={'method':'joint','threshold':0.,'out_caps':{},'in_caps':{}}
    names=[m.ANCHOR]+['test_expert_'+str(i) for i in range(1,E)]
    experts={name:{'sha256':str(i)} for i,name in enumerate(names)}
    preds={name:labels[i] for i,name in enumerate(names)}
    edges={format_edge(objects,i,j,int(gold[i,j])) for i in range(n)
           for j in range(n) if i!=j and int(gold[i,j])>0}
    rec={'item':{'objects':objects,'targets':gold,'full_gold':edges,'g1_exact':True,
          'row':{'family':'encounter','base_id':'b1'}},
         'expert_margins':margins,'predictions':preds}
    # Gradients for all separately trainable components.
    spec_loss=_specialist_loss(specialist,rec,experts,'cpu')
    spec_loss.backward()
    require(bool(torch.isfinite(spec_loss)) and any(p.grad is not None
            for p in specialist.parameters()),'SPECIALIST_GRAD_FAILED')
    specialist.eval()
    edge_model=EdgeVerifier()
    loss_v=_verifier_examples(edge_model,rec,experts,spec,'cpu',
                 [(model,'pin_ungated',False),(model,'pin_ungated',True)],specialist)
    loss_v.backward()
    require(bool(torch.isfinite(loss_v)),'VERIFIER_GRAD_FAILED')
    edge_model.eval()
    for policy in VARIANTS:
        _,sp,ve=_candidate_args(policy,model,model,specialist,edge_model)
        output,meta=_infer_models(rec,experts,spec,'cpu',model,'pin_ungated',
                                  specialist=sp,verifier=ve)
        require(tuple(output.shape)==(n,n) and meta['vetoed_edges']>=0,
                'INFERENCE_RESULT_DRIFT')
    # Ensure the deployable adapter does not read target or family from rec.
    without_gold={'item':{'objects':objects},'expert_margins':margins,'predictions':preds}
    inferred,_=_infer_models(without_gold,experts,spec,'cpu',model,'pin_ungated',
                 specialist=specialist,verifier=edge_model)
    require(tuple(inferred.shape)==(n,n),'GOLD_FREE_INFERENCE_FAILED')
    selection=_select_on_val([rec],experts,spec,'cpu',model,'pin_ungated',
                             model,specialist,edge_model)
    require(selection[0] in VARIANTS and len(selection[-1])==len(_policy_options()),
            'VALIDATION_BAKEOFF_COUNT_OR_SELECTION_FAILED')
    with tempfile.TemporaryDirectory(prefix='nanojev_g2_0100_') as d:
        folder=Path(d)
        inventory={}
        for i,name in enumerate(names):
            file=folder/('ex'+str(i)+'.bin');file.write_bytes(str(i).encode())
            inventory[name]={'path':str(file),'sha256':old.digest(file)}
        mockreport={'candidate':{'sha256':'fake_source'}}
        _save_models(folder/'candidate',{'router':model,'specialist':specialist,
                     'verifier':edge_model},mockreport,inventory,
                     {'variant':'all_three'},'selftest')
        loaded,_=load_candidate(folder/'candidate',inventory,'cpu','fake_source')
        inferred2,_=_infer_models(without_gold,inventory,spec,'cpu',
                loaded['router'],'pin_ungated',specialist=loaded['specialist'],
                verifier=loaded['verifier'])
        require(torch.equal(inferred,inferred2),'RELOAD_INFERENCE_CHANGED')
        first=Path(next(iter(inventory.values()))['path']);first.write_bytes(b'tamper')
        try:load_candidate(folder/'candidate',inventory,'cpu','fake_source')
        except ValueError as exc:
            require('FROZEN_EXPERT_MUTATED' in str(exc),'UNEXPECTED_TAMPER_FAILURE')
        else:raise ValueError(TAG+'_TAMPER_UNDETECTED')
    emit('SELFTEST',{'specialist_training_gradient':True,
       'verifier_training_gradient':True,'all_arms_tested':list(VARIANTS),
       'real_structured_decoder':True,'gold_free_inference':True,
       'checkpoint_roundtrip':True,'expert_mutation_rejected':True,
       'no_production_promotion':True})


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',nargs='?',default='run',choices=('audit','selftest','run'))
    p.add_argument('--root',default=old.ROOT_DEFAULT)
    p.add_argument('--output')
    p.add_argument('--device',default='cuda')
    p.add_argument('--seed',type=int,default=990099)
    p.add_argument('--router-steps',type=int,default=250)
    p.add_argument('--specialist-steps',type=int,default=450)
    p.add_argument('--verifier-steps',type=int,default=300)
    p.add_argument('--lr',type=float,default=.002)
    p.add_argument('--force',action='store_true')
    a=p.parse_args(argv)
    if a.command=='selftest':return selftest()
    if a.command=='audit':return audit(root=a.root,output=a.output)
    return run(root=a.root,output=a.output,device=a.device,seed=a.seed,
               router_steps=a.router_steps,specialist_steps=a.specialist_steps,
               verifier_steps=a.verifier_steps,lr=a.lr,force=a.force)

if __name__=='__main__':main()
