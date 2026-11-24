"""G2-0104: causal failure-specialized raw-edge recovery bakeoff.

  python -u -m tools.nanojev_gamer_v1_g2_failure_specialists_0104 audit
  python -u -m tools.nanojev_gamer_v1_g2_failure_specialists_0104 selftest
  python -u -m tools.nanojev_gamer_v1_g2_failure_specialists_0104 run --device cuda

The frozen G2-0103 *fold-selected* rerankers are controls. A suppression
specialist trains only on relations proposed with positive raw margins but
omitted by every expert's decoded graph. A weak-signal specialist learns
from relations assigned nonpositive raw margins by all experts. Both have
negative examples drawn from the SAME eligible inference-time domain.
Neither uses the G2-0102 gold-derived case diagnosis at inference time.
Selection is restricted to the original base-disjoint inner-validation split;
outer tests are used once per fold, after selection. The fit cohort has been
reused across G2 studies: passing this gate is RESEARCH ONLY, never G3 or
production certification. Existing checkpoints are read, never written.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import random
import tempfile

import torch
from torch import nn
from torch.nn import functional as F
from safetensors.torch import load_file, save_file

from tools import nanojev_gamer_v1_g2_raw_graph_reranking_0103 as q
from tools import nanojev_gamer_v1_g2_graph_reconstruction_0101 as r
from tools import nanojev_gamer_v1_g2_encounter_bakeoff_0100 as b
from tools import nanojev_gamer_v1_g2_router_trainability_0098 as tr
from tools import nanojev_gamer_v1_g2_competence_routing_0098 as m
from tools import nanojev_gamer_v1_g2_fuzz_and_correct_0095 as old

TAG = 'G2_0104_FAILURE_SPECIALISTS'
SCHEMA = 'nanojev.g2.failure-specialists-0104.v1'
SEED = q.SEED
HEADS = ('suppressed', 'weak')
ARMS = ('frozen_0103', 'suppressed_only', 'weak_only', 'dual', 'dual_guarded')
ALPHAS = (.35, .65, 1.0)
OFFSETS = (0., .75, 1.5)
H = 96
INPUT_DIM = q.D + b.R


def emit(stage, data):
    print(TAG+'_'+stage+' '+json.dumps(data,sort_keys=True,allow_nan=False),flush=True)


def require(ok,reason):
    if not ok:raise ValueError(TAG+'_'+reason)


def _source(root,output):
    source,experts,r99,r100,r101,r102,p102=q._source(root,output)
    p103=source[8]/'raw-graph-reranking-0103'/'result.json'
    require(p103.is_file(),'0103_REPORT_MISSING')
    r103=old.read(p103)
    require(r103.get('schema')==q.SCHEMA and
            r103.get('accepted_on_reused_fit_cv') is True and
            all(r103.get('gates',{}).values()) and
            r103.get('source_0102_sha256')==old.digest(p102),
            '0103_ACCEPTANCE_OR_LINEAGE_CHANGED')
    require(r103.get('production_promoted') is False and
            r103.get('g3_unlocked') is False and
            r103.get('dev_used') is False and
            r103.get('confirmation_used') is False and
            r103.get('research_only') is True,
            '0103_RESEARCH_POLICY_CHANGED')
    require(len(r103.get('folds',[]))==3 and
            len(r103.get('case_level_encounter',[]))==138,
            '0103_FOLD_OR_CASE_INVENTORY_CHANGED')
    for i in range(3):
        fold=r103['folds'][i]
        require(fold['fold']==i and fold['base_counts']==r100['folds'][i]['base_counts'] and
                fold['base_counts']==r102['folds'][i]['base_counts'] and
                fold.get('selected_on')=='INNER_VALIDATION_ONLY',
                '0103_SPLIT_OR_SELECTION_CHANGED')
        require(fold['chosen']['arm'] in q.MODELS+('raw_max',),
                '0103_SELECTED_ARM_CANNOT_BE_REPLAYED_AS_SCORE_SURFACE')
    return source,experts,r99,r100,r101,r102,r103,p103,p102


def _fold_reference(i,source,experts,r99,r100,r103,p102,device):
    bundle=r._bundle_from_0100(i,r100['folds'][i],r99,experts,device)
    ref=r103['folds'][i]
    models,manifest=q.load_candidate(Path(ref['manifest']).parent,experts,device,
                                     old.digest(p102))
    require(manifest['selected']==ref['chosen'] and
            manifest['role']=='base-disjoint-fold',
            '0103_FOLD_SELECTED_CHECKPOINT_CHANGED')
    return bundle,models,ref['chosen']


def _masks(margins,predictions):
    """Typed eligibility from inference-time evidence; NO gold or diagnosis.

    Positive-but-not-decoded and nonpositive raw evidence are disjoint for
    each relation type. A relationship may qualify as suppressed while a
    different type at the same (source,destination) qualifies as weak.
    """
    raw=margins.max(0).values
    require(raw.shape[-1]==b.R and predictions.shape==margins.shape[:3],
            'EXPERT_SHAPE_CHANGED')
    typed=torch.arange(1,b.R+1,device=raw.device)
    decoded=(predictions[...,None]==typed).any(0)
    valid=~torch.eye(raw.shape[0],dtype=torch.bool,device=raw.device)
    suppressed=(raw>0)&(~decoded)&valid[...,None]
    weak=(raw<=0)&valid[...,None]
    require(not bool((suppressed&weak).any()),'GATES_OVERLAP')
    return {'suppressed':suppressed,'weak':weak}


class FailureSpecialist(nn.Module):
    """Graph-conditioned, relation-typed scores; no gold or case family input."""
    def __init__(self):
        super().__init__()
        self.encoder=nn.Sequential(nn.Linear(INPUT_DIM,H),nn.Tanh(),
                                   nn.Linear(H,H),nn.Tanh())
        self.head=nn.Sequential(nn.Linear(4*H,H),nn.Tanh(),
                                nn.Linear(H,b.R))

    def forward(self,features):
        n=features.shape[0]
        require(features.shape==(n,n,INPUT_DIM),'SPECIALIST_FEATURE_SHAPE')
        x=self.encoder(features)
        valid=(~torch.eye(n,dtype=torch.bool,device=x.device))[...,None].to(x.dtype)
        xlive=x*valid
        row=xlive.sum(1)/max(1,n-1)
        col=xlive.sum(0)/max(1,n-1)
        full=xlive.sum((0,1))/max(1,n*(n-1))
        context=torch.cat((x,row[:,None,:].expand(-1,n,-1),
                           col[None,:,:].expand(n,-1,-1),
                           full[None,None,:].expand(n,n,-1)),dim=-1)
        return self.head(context)


def _base_inputs(rec,experts,device,bundle,base_models,choice):
    """One score space exactly reproducing selected G2-0103 raw reranking."""
    require(choice['arm'] in q.MODELS+('raw_max',),'INVALID_BASELINE_ARM')
    objects=rec['item']['objects']
    for name in ('router','verifier'):
        require(not bundle[name].training,'FROZEN_'+name.upper()+'_TRAINING')
    if bundle['specialist'] is not None:
        require(not bundle['specialist'].training,'FROZEN_SPECIALIST_TRAINING')
    with torch.no_grad():
        base,margins,preds,_,_=r._base_scores(rec,experts,device,bundle)
        if choice['arm'] in q.MODELS:
            refmodel=base_models[choice['arm']]
            require(not refmodel.training,'0103_BASELINE_MODEL_TRAINING')
            logits=refmodel(objects,margins,preds,base)
            scores=q._candidate_scores(margins,logits,**choice['params'])
        else:
            scores=q._candidate_scores(margins,**choice['params'])
        # The raw-score surface itself is unchanged for the frozen arm.
        features=torch.cat((q._raw_features(objects,margins,preds,base),
                            (scores[...,1:]-scores[...,:1]).clamp(-12.,12.)/6.),-1)
        gates=_masks(margins,preds)
    require(scores.shape==(len(objects),len(objects),b.R+1),'SCORE_SHAPE_DRIFT')
    return scores,features,gates


def _prepare(records,experts,device,bundle,models,choice):
    """Cache frozen inference features once; labels remain training-only."""
    prepared=[];stats=Counter()
    for rec in records:
        score,features,gates=_base_inputs(rec,experts,device,bundle,models,choice)
        gold,valid=b._gold_mask(rec,device)
        labels=(gold[...,None]==torch.arange(1,b.R+1,device=device))
        for kind in HEADS:
            eligible=gates[kind]
            stats[kind+'_positive']+=int((eligible&labels).sum())
            stats[kind+'_negative']+=int((eligible&(~labels)).sum())
        prepared.append({'features':features.detach(),'scores':score.detach(),
           'gates':gates,'gold':gold,'labels':labels,'valid':valid,
           'family':rec['item']['row']['family']})
    require(prepared,'EMPTY_TRAINING_DATA')
    return prepared,dict(stats)


def _balanced_loss(net,sample,kind):
    scores=net(sample['features'])
    eligible=sample['gates'][kind]
    labels=sample['labels']
    pos=eligible&labels
    neg=eligible&(~labels)
    require(bool(eligible.any()),'ELIGIBILITY_EMPTY_IN_TRAINING_SAMPLE')
    # Equal positive/negative loss pressure prevents huge numbers of NONE
    # candidates from drowning out rare missing edges. Explicit negatives
    # from the SAME eligibility domain prevent always-adding an edge.
    neg_loss=F.softplus(scores[neg]).mean() if bool(neg.any()) else scores.sum()*0.
    if bool(pos.any()):
        loss=.6*F.softplus(-scores[pos]).mean()+.4*neg_loss
    else:
        loss=neg_loss
    # Family is a TRAINING weight only, never an inference feature.
    return loss*(1.3 if sample['family']=='encounter' else .7)


def _train(records,experts,device,bundle,base_models,choice,kind,*,steps,lr,seed):
    require(kind in HEADS and steps>=1 and 0<lr<.1,'TRAINING_ARGS')
    samples,stats=_prepare(records,experts,device,bundle,base_models,choice)
    require(stats[kind+'_positive']>0 and stats[kind+'_negative']>0,
            'NO_SUPERVISION_FOR_'+kind.upper())
    torch.manual_seed(seed)
    net=FailureSpecialist().to(device)
    optimizer=torch.optim.AdamW(net.parameters(),lr=lr,weight_decay=.0001)
    rng=random.Random(seed)
    pos_samples=[s for s in samples if bool((s['gates'][kind]&s['labels']).any())]
    for step in range(1,steps+1):
        # At least one eligible *positive* each step, with independently
        # sampled negatives/other cases, to prevent an empty-class gradient.
        batch=[rng.choice(pos_samples)]+rng.sample(samples,min(3,len(samples)))
        optimizer.zero_grad(set_to_none=True)
        loss=torch.stack([_balanced_loss(net,x,kind) for x in batch
                          if bool(x['gates'][kind].any())]).mean()
        require(bool(torch.isfinite(loss)),'NONFINITE_LOSS')
        loss.backward()
        torch.nn.utils.clip_grad_norm_(net.parameters(),1.)
        optimizer.step()
        if step in (1,steps) or step%100==0:
            emit('TRAIN',{'head':kind,'step':step,'steps':steps,
                          'loss':round(float(loss.detach()),6)})
    net.eval()
    return net,stats


def _correct_scores(scores,features,gates,heads,arm,*,alpha,offset):
    require(arm in ARMS and 0<=alpha<=1 and 0<=offset<=3,'INVALID_POLICY')
    if arm=='frozen_0103':return scores.clone()
    names={'suppressed_only':('suppressed',),'weak_only':('weak',),
           'dual':HEADS,'dual_guarded':HEADS}[arm]
    base=scores[...,1:]-scores[...,:1]
    margins=base.clone()
    for name in names:
        require(name in heads and not heads[name].training,'MISSING_FROZEN_'+name.upper())
        mask=gates[name]
        logits=heads[name](features).clamp(-12.,12.)
        correction=(1.-alpha)*base+alpha*logits-offset
        if arm=='dual_guarded':
            # Protect existing high-margin edges from a weak/uncertain veto;
            # positive or absent alternatives can still compete normally.
            mask=mask&(base<2.)
        margins=torch.where(mask,correction,margins)
    return torch.cat((torch.zeros_like(scores[...,:1]),margins),dim=-1)


def _infer(rec,experts,spec,device,bundle,base_models,choice,heads,arm,
           *,alpha=.65,offset=0.):
    require(arm in ARMS,'UNKNOWN_ARM')
    if arm=='frozen_0103':
        return q._infer(rec,experts,spec,device,bundle,choice['arm'],
                 base_models.get(choice['arm']),**choice['params'])
    with torch.no_grad():
        scores,features,gates=_base_inputs(rec,experts,device,bundle,base_models,choice)
        corrected=_correct_scores(scores,features,gates,heads,arm,
                                  alpha=alpha,offset=offset)
        return r._decode(corrected,rec['item']['objects'],spec,device).cpu()


def _evaluate(records,experts,spec,device,bundle,base_models,choice,heads,arm,**params):
    if arm=='frozen_0103':
        return q._evaluate(records,experts,spec,device,bundle,choice['arm'],
                    base_models.get(choice['arm']),**choice['params'])
    overall=Counter();byfam=defaultdict(Counter);byrel=defaultdict(Counter)
    for rec in records:
        predicted=_infer(rec,experts,spec,device,bundle,base_models,choice,heads,arm,**params)
        ct=m._count(rec,predicted)
        family=rec['item']['row']['family']
        overall.update(ct);byfam[family].update(ct)
        gold=rec['item']['targets'].cpu()
        valid=~torch.eye(len(gold),dtype=torch.bool)
        for rel in range(1,b.R+1):
            key=family+'|'+b.BIND_CLASSES[rel]
            byrel[key]['missed']+=int((valid&(gold==rel)&(predicted!=rel)).sum())
            byrel[key]['spurious']+=int((valid&(gold!=rel)&(predicted==rel)).sum())
    return {'overall':m._metric(overall),
            'by_family':{k:m._metric(v) for k,v in byfam.items()},
            'by_relation':{k:dict(v) for k,v in byrel.items()}}


def _choices():
    yield 'frozen_0103',{}
    for arm in ARMS[1:]:
        for alpha in ALPHAS:
            for offset in OFFSETS:
                yield arm,{'alpha':alpha,'offset':offset}


def _rank(stat,baseline):
    fam=stat['by_family'];orig=baseline['by_family']
    feasible=(fam['allocation']['edge_f1']>=orig['allocation']['edge_f1']-.01 and
              stat['overall']['edge_f1']>=baseline['overall']['edge_f1']-.005)
    return (int(feasible),
       fam['encounter']['exact'] if feasible else -1,
       stat['overall']['exact'] if feasible else -1,
       fam['encounter']['edge_f1'] if feasible else -1.,
       stat['overall']['edge_f1'] if feasible else -1.)


def _select(val,experts,spec,device,bundle,base_models,choice,heads):
    baseline=_evaluate(val,experts,spec,device,bundle,base_models,choice,heads,
                       'frozen_0103')
    options=[]
    for arm,params in _choices():
        measured=baseline if arm=='frozen_0103' else _evaluate(val,experts,spec,device,
                     bundle,base_models,choice,heads,arm,**params)
        options.append({'arm':arm,'params':params,
                        'rank':list(_rank(measured,baseline)),
                        'encounter_exact':measured['by_family']['encounter']['exact'],
                        'encounter_f1':measured['by_family']['encounter']['edge_f1'],
                        'allocation_f1':measured['by_family']['allocation']['edge_f1'],
                        'overall_exact':measured['overall']['exact'],
                        'overall_f1':measured['overall']['edge_f1']})
    chosen=max(enumerate(options),key=lambda x:(tuple(x[1]['rank']),-x[0]))[1]
    return chosen,options,baseline


def _metric_sum(folds,key,family=None):
    counts=Counter()
    for fold in folds:
        stat=fold[key]['by_family'][family] if family else fold[key]['overall']
        for name in ('tp','fp','fn','exact','cases','g1_exact'):
            counts[name]+=stat[name]
    return m._metric(counts)


def _save(folder,heads,experts,source_hash,baseline_choice,selection,role):
    folder.mkdir(parents=True,exist_ok=True)
    weights={}
    for name,net in heads.items():
        require(name in HEADS and not net.training,'UNFROZEN_SPECIALIST')
        file=folder/(name+'.safetensors')
        save_file({k:v.detach().cpu().contiguous() for k,v in net.state_dict().items()},str(file))
        weights[name]={'path':str(file),'sha256':old.digest(file)}
    manifest={'schema':SCHEMA,'role':role,'research_only':True,
       'production_promoted':False,'g3_unlocked':False,
       'dev_used':False,'confirmation_used':False,
       'reused_fit_not_independent_confirmation':True,
       'source_0103_sha256':source_hash,
       'expert_order':list(experts),
       'expert_hashes':{k:v['sha256'] for k,v in experts.items()},
       'baseline_0103':baseline_choice,'selected':selection,'heads':weights}
    old.write(folder/'manifest.json',manifest)
    return manifest


def load_candidate(folder,experts,device,source_hash):
    manifest=old.read(Path(folder)/'manifest.json')
    require(manifest.get('schema')==SCHEMA and manifest.get('research_only') is True and
            manifest.get('production_promoted') is False and
            manifest.get('g3_unlocked') is False and
            manifest.get('dev_used') is False and
            manifest.get('confirmation_used') is False and
            manifest.get('source_0103_sha256')==source_hash,
            'POLICY_OR_SOURCE_CHANGED')
    require(manifest['expert_order']==list(experts),'EXPERT_ORDER_CHANGED')
    require(all(manifest['expert_hashes'].get(k)==v['sha256'] and
            old.digest(v['path'])==v['sha256'] for k,v in experts.items()),
            'FROZEN_EXPERT_MUTATED')
    heads={}
    for name,entry in manifest['heads'].items():
        require(name in HEADS and old.digest(entry['path'])==entry['sha256'],
                'SPECIALIST_CHECKPOINT_MUTATED')
        model=FailureSpecialist().to(device)
        model.load_state_dict(load_file(entry['path'],device='cpu'),strict=True)
        model.eval();heads[name]=model
    return heads,manifest


def _cause_metrics(test,fold,priorcases,experts,spec,device,bundle,models,choice,
                   heads,arm,params):
    cause=defaultdict(Counter);rawcase=[]
    for i,rec in enumerate(test):
        prior=priorcases[(fold,i)]
        require(prior['base_id']==str(rec['item']['row']['base_id']) and
                prior['family']==rec['item']['row']['family'] and
                prior['case_id']==str(rec['item']['row']['case_id']),
                '0102_DIAGNOSTIC_CASE_ORDER_CHANGED')
        if prior['family']!='encounter':continue
        got=_infer(rec,experts,spec,device,bundle,models,choice,heads,arm,**params)
        ct=m._count(rec,got)
        name=prior['decoding_obstacle']
        cause[name].update({k:ct[k] for k in ('exact','tp','fp','fn')})
        rawcase.append({'fold':fold,'index':i,'case_id':prior['case_id'],
            'base_id':prior['base_id'],'cause':name,
            'tp':ct['tp'],'fp':ct['fp'],'fn':ct['fn'],'exact':ct['exact']})
    return {k:dict(v) for k,v in cause.items()},rawcase


def audit(root=old.ROOT_DEFAULT,output=None):
    source,experts,r99,r100,r101,r102,r103,p103,p102=_source(root,output)
    for fold in range(3):
        _,_,choice=_fold_reference(fold,source,experts,r99,r100,r103,p102,'cpu')
        require(choice==r103['folds'][fold]['chosen'],'0103_CHECKPOINT_SELECTION_CHANGED')
    emit('AUDIT',{'ready':True,'source_0103_sha256':old.digest(p103),
      'frozen_0103_research_cv_accepted':True,
      'folds':3,'expert_count':len(experts),'new_trainable_heads':list(HEADS),
      'arms':list(ARMS),'selection_grid':len(list(_choices())),
      'inference_domains':['raw_positive_undecoded_typed_relation',
                           'raw_nonpositive_typed_relation'],
      'retrospective_causes_only':True,
      'base_disjoint_reused_fit':True,'inner_validation_only':True,
      'production_promotion':False,'g3_unlocked':False})


def run(root=old.ROOT_DEFAULT,output=None,device='cuda',seed=SEED,
        steps=350,lr=.0015,force=False):
    require(device=='cpu' or (device.startswith('cuda') and torch.cuda.is_available()),
            'CUDA_UNAVAILABLE')
    require(1<=steps<=3000 and 0<lr<.1,'INVALID_TRAINING_ARGS')
    source,experts,r99,r100,r101,r102,r103,p103,p102=_source(root,output)
    outputdir=source[8]/'failure-specialists-0104'
    reportfile=outputdir/'result.json'
    require(force or not reportfile.exists(),'REPORT_EXISTS_USE_FORCE')
    emit('START',{'device':device,'folds':3,'steps_per_head':steps,
       'trainable_heads':list(HEADS),'arms':list(ARMS),
       'selection_grid':len(list(_choices())),
       'frozen_benchmark':'G2_0103_FOLD_SELECTED_RESEARCH_CV',
       'outer_test_after_inner_validation':True,
       'gold_free_inference':True,
       'reused_fit_not_independent_certification':True})
    records,spec=b.parent._records(source,experts,device)
    folds=[];evidence=[]
    priorcases={(x['fold'],x['test_index']):x for x in r102['case_diagnostics']}
    require(len(priorcases)==275,'0102_CASE_DIAGNOSTICS_INCOMPLETE')
    for fold,(index,(train,val,test),counts) in enumerate(tr._split_by_base(records,3,seed)):
        require(index==fold and counts==r103['folds'][fold]['base_counts'],
                'BASE_DISJOINT_FOLDS_CHANGED')
        bundle,models,choice=_fold_reference(fold,source,experts,r99,r100,r103,p102,device)
        heads={};training_evidence={}
        for j,kind in enumerate(HEADS):
            model,stats=_train(train,experts,device,bundle,models,choice,kind,
                steps=steps,lr=lr,seed=seed+fold*701+j*137)
            heads[kind]=model;training_evidence[kind]=stats
        chosen,options,_=_select(val,experts,spec,device,bundle,models,choice,heads)
        # Frozen G2-0103 selection and prediction are replayed on the SAME
        # outer fold; any deviation fails closed before new results are claimed.
        control=_evaluate(test,experts,spec,device,bundle,models,choice,heads,'frozen_0103')
        ref=r103['folds'][fold]['selected']
        require(all(control['overall'][k]==ref['overall'][k] for k in ('tp','fp','fn','exact')) and
                all(control['by_family'][f][k]==ref['by_family'][f][k]
                    for f in ('encounter','allocation') for k in ('tp','fp','fn','exact')),
                'FROZEN_0103_CONTROL_NOT_REPRODUCIBLE')
        selected=_evaluate(test,experts,spec,device,bundle,models,choice,heads,
                           chosen['arm'],**chosen['params'])
        causes,rows=_cause_metrics(test,fold,priorcases,experts,spec,device,bundle,
                                  models,choice,heads,chosen['arm'],chosen['params'])
        evidence.extend(rows)
        path=outputdir/('fold-'+str(fold))
        _save(path,heads,experts,old.digest(p103),choice,chosen,'base-disjoint-fold')
        loaded,manifest=load_candidate(path,experts,device,old.digest(p103))
        require(manifest['selected']==chosen,'SPECIALIST_SELECTION_CHANGED')
        for rec in test[:1]:
            before=_infer(rec,experts,spec,device,bundle,models,choice,heads,
                          chosen['arm'],**chosen['params'])
            after=_infer(rec,experts,spec,device,bundle,models,choice,loaded,
                         chosen['arm'],**chosen['params'])
            require(torch.equal(before,after),'RELOADED_INFERENCE_CHANGED')
        folds.append({'fold':fold,'base_counts':counts,
           'selected_on':'INNER_VALIDATION_ONLY','frozen_0103_recipe':choice,
           'chosen':chosen,'validation_bakeoff':options,'control_0103':control,
           'selected':selected,'encounter_by_0102_cause':causes,
           'training_eligibility_counts':training_evidence,
           'manifest':str(path/'manifest.json')})
        emit('FOLD',{'fold':fold,'chosen':chosen['arm'],'params':chosen['params'],
            'encounter_exact':[control['by_family']['encounter']['exact'],
                                selected['by_family']['encounter']['exact']],
            'encounter_f1':[control['by_family']['encounter']['edge_f1'],
                             selected['by_family']['encounter']['edge_f1']],
            'overall_exact':[control['overall']['exact'],selected['overall']['exact']]})
    prior=_metric_sum(folds,'control_0103');candidate=_metric_sum(folds,'selected')
    families={f:{'control_0103':_metric_sum(folds,'control_0103',f),
                 'selected':_metric_sum(folds,'selected',f)}
              for f in ('allocation','encounter')}
    require(all(prior[k]==r103['candidate'][k] for k in ('tp','fp','fn','exact')),
            '0103_AGGREGATE_CONTROL_CHANGED')
    gates={'encounter_exact_progress_over_0103':families['encounter']['selected']['exact']>
                                              families['encounter']['control_0103']['exact'],
           'encounter_f1_not_worse':families['encounter']['selected']['edge_f1']>=
                                    families['encounter']['control_0103']['edge_f1'],
           'allocation_f1_within_1pp':families['allocation']['selected']['edge_f1']>=
                                      families['allocation']['control_0103']['edge_f1']-.01,
           'overall_f1_not_worse':candidate['edge_f1']>=prior['edge_f1'],
           'overall_exact_not_worse':candidate['exact']>=prior['exact']}
    # Final recipe is chosen from INNER VALIDATION ONLY, not best outer test.
    votes=Counter(f['chosen']['arm'] for f in folds)
    final_arm=sorted(votes,key=lambda arm:(-votes[arm],
             min(i for i,x in enumerate(folds) if x['chosen']['arm']==arm)))[0]
    final_params=next(x['chosen']['params'] for x in folds if x['chosen']['arm']==final_arm)
    fullbundle=q._full_fit_bundle(source,experts,r99,r100,device)
    fullmodels,fullmanifest=q.load_candidate(
           source[8]/'raw-graph-reranking-0103'/'full-fit-research-only',
           experts,device,old.digest(p102))
    fullchoice=fullmanifest['selected']
    require(fullchoice['arm']==r103['full_fit_recipe']['arm'] and
            fullchoice['params']==r103['full_fit_recipe']['params'] and
            fullchoice['arm'] in q.MODELS+('raw_max',),
            '0103_FULL_FIT_RECIPE_CHANGED')
    # Final checkpoint is separately trained on all previously used FIT data.
    # It has NEVER been measured on untouched confirmation/development data.
    fullheads={}
    if final_arm!='frozen_0103':
        for j,kind in enumerate(HEADS):
            fullheads[kind],_=_train(records,experts,device,fullbundle,fullmodels,
                       fullchoice,kind,steps=steps,lr=lr,seed=seed+10001+j*173)
    _save(outputdir/'full-fit-research-only',fullheads,experts,old.digest(p103),
       fullchoice,{'arm':final_arm,'params':final_params,
                   'warning':'FULL_FIT_NOT_INDEPENDENTLY_EVALUATED'},
       'research-only')
    report={'schema':SCHEMA,'accepted_on_reused_fit_cv':all(gates.values()),
      'gates':gates,'control_0103':prior,'candidate':candidate,
      'by_family':families,'folds':folds,'case_level_encounter':evidence,
      'source_0103_sha256':old.digest(p103),
      'final_recipe_from_inner_validation_majority':{'arm':final_arm,'params':final_params},
      'full_fit_0103_reference':fullchoice,
      'data_is_reused_fit_not_independent_certification':True,
      'gold_used_only_for_training_and_retrospective_evaluation':True,
      'research_only':True,'production_promoted':False,'g3_unlocked':False,
      'dev_used':False,'confirmation_used':False}
    outputdir.mkdir(parents=True,exist_ok=True)
    old.write(reportfile,report)
    emit('CROSS_VALIDATED',{'accepted':all(gates.values()),'gates':gates,
      'control_0103':prior,'candidate':candidate,'by_family':families,
      'full_fit_recipe':final_arm})
    emit('SAVED',{'path':str(reportfile),'sha256':old.digest(reportfile),
                  'production_promoted':False,'g3_unlocked':False})


def selftest():
    from tools.nanojev_gamer_v1_g2_posttrain_graph_bakeoff_0088 import format_edge
    torch.manual_seed(104)
    n=8
    objects=[[3*i,3*i+2,b.SPAN_KINDS[i%len(b.SPAN_KINDS)]] for i in range(n)]
    gold=torch.zeros((n,n),dtype=torch.long)
    gold[0,1]=1;gold[2,3]=min(b.R,2);gold[4,5]=1
    margins=torch.full((b.E,n,n,b.R),-.8)
    preds=torch.zeros((b.E,n,n),dtype=torch.long)
    # gold[0,1] is suppressed: positive raw proposal, no expert decodes it.
    margins[:,0,1,0]=1.2
    # gold[2,3] is weak: all experts have negative raw margin for its type.
    margins[:,2,3,1]=-1.8
    # True negative in suppression domain, to prevent an always-insert fix.
    margins[:,1,0,0]=2.
    names=[b.m.ANCHOR]+['synthetic_'+str(i) for i in range(1,b.E)]
    experts={name:{'sha256':'','path':''} for name in names}
    full_gold={format_edge(objects,i,j,int(gold[i,j]))
               for i,j in (gold>0).nonzero().tolist()}
    rec={'item':{'objects':objects,'targets':gold,'full_gold':full_gold,
                 'row':{'family':'encounter','base_id':'toy-104','case_id':'toy-104-base'},
                 'g1_exact':True},
         'expert_margins':margins,'predictions':{k:preds[i] for i,k in enumerate(names)}}
    bundle={'router':b.m.CompetenceRouter(b.E).eval(),'specialist':None,
           'verifier':b.EdgeVerifier().eval(),'policy':'pin_ungated',
           's_threshold':.7,'v_threshold':.5,'selected_variant':'router_assembly'}
    baseline=q.RawGraphReranker(False).eval()
    models={'raw_local':baseline};choice={'arm':'raw_local','params':{'blend':1.,'offset':0.}}
    spec={'method':'joint','threshold':0.,'out_caps':{},'in_caps':{}}
    scores,features,gates=_base_inputs(rec,experts,'cpu',bundle,models,choice)
    require(bool(gates['suppressed'][0,1,0]) and
            bool(gates['weak'][2,3,1]) and
            bool(gates['suppressed'][1,0,0]) and
            not bool((gates['weak']&gates['suppressed']).any()),
            'CAUSAL_ELIGIBILITY_INVALID')
    heads={}
    for kind in HEADS:
        head,stats=_train([rec],experts,'cpu',bundle,models,choice,kind,
                         steps=2,lr=.002,seed=104)
        require(stats[kind+'_positive']>0 and stats[kind+'_negative']>0,
                'MISSING_CLASS_EXAMPLE_'+kind)
        heads[kind]=head
    for arm in ARMS:
        expected=_infer(rec,experts,spec,'cpu',bundle,models,choice,heads,arm)
        no_gold={'item':{'objects':objects},'expert_margins':margins,
                 'predictions':rec['predictions']}
        actual=_infer(no_gold,experts,spec,'cpu',bundle,models,choice,heads,arm)
        require(torch.equal(expected,actual),'GOLD_LEAK_'+arm)
    # Scores must remain byte-exact outside eligible relation-type cells.
    for arm in ARMS[1:]:
        improved=_correct_scores(scores,features,gates,heads,arm,alpha=.65,offset=.75)
        eligible=(gates['suppressed'] if arm=='suppressed_only' else
                  gates['weak'] if arm=='weak_only' else
                  gates['suppressed']|gates['weak'])
        require(torch.equal(improved[...,1:][~eligible],scores[...,1:][~eligible]),
                'CHANGED_UNRELATED_RELATION_'+arm)
    # Check both missing classes can be expressed, with learned logits set
    # explicitly high only for the relevant eligible relations.
    class OracleFixture(nn.Module):
        def __init__(self,kind):super().__init__();self.kind=kind
        def forward(self,feats):
            out=feats.new_full((*feats.shape[:2],b.R),-10.)
            if self.kind=='suppressed':out[0,1,0]=10.
            else:out[2,3,1]=10.
            return out
    synthetic_heads={k:OracleFixture(k).eval() for k in HEADS}
    corrected=_correct_scores(scores,features,gates,synthetic_heads,'dual',alpha=1.,offset=0.)
    require(corrected[0,1,1]>corrected[0,1,0] and
            corrected[2,3,2]>corrected[2,3,0],
            'CANNOT_RECOVER_SUPPRESSED_OR_WEAK_LABEL')
    alloc={**rec,'item':{**rec['item'],'row':{'family':'allocation',
                  'base_id':'toy-105','case_id':'toy-105-base'}}}
    selected,table,_=_select([rec,alloc],experts,spec,'cpu',bundle,models,choice,heads)
    require(len(table)==len(list(_choices())) and selected['arm'] in ARMS,
            'BAKEOFF_SELECTION_INVALID')
    # Full miniature base-disjoint three-fold pipeline: train both heads on
    # TRAIN, rank on VAL, score only the selected policy on TEST. This catches
    # wiring/aggregation errors that single-case smoke fixtures cannot.
    miniature=[]
    for family in ('allocation','encounter'):
        for group in range(6):
            row={'family':family,'base_id':f'toy-{family}-{group}',
                 'case_id':f'toy-{family}-{group}-base'}
            miniature.append({**rec,'item':{**rec['item'],'row':row}})
    synthetic_folds=[]
    for fi,(index,(train,val,test),counts) in enumerate(
            tr._split_by_base(miniature,3,SEED)):
        require(index==fi and all(counts.values()),'SYNTHETIC_FOLD_EMPTY')
        specialist_heads={}
        for ki,kind in enumerate(HEADS):
            specialist_heads[kind],_= _train(train,experts,'cpu',bundle,models,choice,
                      kind,steps=2,lr=.002,seed=104+fi*31+ki)
        selected,_,_= _select(val,experts,spec,'cpu',bundle,models,choice,
                              specialist_heads)
        before=_evaluate(test,experts,spec,'cpu',bundle,models,choice,
                         specialist_heads,'frozen_0103')
        after=_evaluate(test,experts,spec,'cpu',bundle,models,choice,
                        specialist_heads,selected['arm'],**selected['params'])
        synthetic_folds.append({'control_0103':before,'selected':after})
    require(_metric_sum(synthetic_folds,'selected')['cases']==len(miniature),
            'SYNTHETIC_CV_AGGREGATION_CHANGED')
    with tempfile.TemporaryDirectory(prefix='g2_0104_') as temp:
        for i,entry in enumerate(experts.values()):
            p=Path(temp)/('expert_'+str(i)+'.bin')
            p.write_bytes(str(i).encode());entry['path']=str(p);entry['sha256']=old.digest(p)
        path=Path(temp)/'candidate'
        _save(path,heads,experts,'mock_0103',choice,selected,'selftest')
        loaded,_=load_candidate(path,experts,'cpu','mock_0103')
        for arm in ARMS:
            a=_infer(rec,experts,spec,'cpu',bundle,models,choice,heads,arm)
            z=_infer(rec,experts,spec,'cpu',bundle,models,choice,loaded,arm)
            require(torch.equal(a,z),'CHECKPOINT_ROUNDTRIP_FAILED_'+arm)
        next(iter(experts.values()))['path']
        Path(next(iter(experts.values()))['path']).write_bytes(b'corrupt')
        try:load_candidate(path,experts,'cpu','mock_0103')
        except ValueError as e:
            require('FROZEN_EXPERT_MUTATED' in str(e),'WRONG_TAMPER_EXCEPTION')
        else:raise ValueError(TAG+'_FAILED_TO_DETECT_TAMPER')
    emit('SELFTEST',{'suppressed_positive_candidate_recoverable':True,
      'negative_margin_weak_candidate_recoverable':True,
      'specialized_positive_and_negative_gradients':True,
      'unrelated_relations_untouched':True,
      'both_heads_and_combinations_tested':list(ARMS),
      'real_structured_decoder':True,'gold_free_inference':True,
      'validation_selection':True,'base_disjoint_three_fold_pipeline':True,
      'checkpoint_roundtrip':True,
      'expert_tamper_rejected':True,'production_unchanged':True})


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',nargs='?',choices=('audit','selftest','run'),default='run')
    parser.add_argument('--root',default=old.ROOT_DEFAULT)
    parser.add_argument('--output')
    parser.add_argument('--device',default='cuda')
    parser.add_argument('--seed',type=int,default=SEED)
    parser.add_argument('--steps',type=int,default=350)
    parser.add_argument('--lr',type=float,default=.0015)
    parser.add_argument('--force',action='store_true')
    args=parser.parse_args(argv)
    if args.command=='audit':return audit(args.root,args.output)
    if args.command=='selftest':return selftest()
    return run(args.root,args.output,args.device,args.seed,args.steps,args.lr,args.force)

if __name__=='__main__':main()
