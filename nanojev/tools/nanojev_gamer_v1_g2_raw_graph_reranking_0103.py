"""G2-0103: raw-expert-score graph reranking with graph-level supervision.

  python -u -m tools.nanojev_gamer_v1_g2_raw_graph_reranking_0103 audit
  python -u -m tools.nanojev_gamer_v1_g2_raw_graph_reranking_0103 selftest
  python -u -m tools.nanojev_gamer_v1_g2_raw_graph_reranking_0103 run --device cuda

Research-only. Preserve frozen G2-0100/G2-0101 lineage. Three base-disjoint
folds of previously used fit data; not independent certification. Outer tests
are scored only after selecting each fold's intervention on inner validation.
Gold is used only for training and retrospective metrics, never inference.
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
from tools import nanojev_gamer_v1_g2_reconstructability_0102 as c
from tools import nanojev_gamer_v1_g2_graph_reconstruction_0101 as r
from tools import nanojev_gamer_v1_g2_encounter_bakeoff_0100 as b
from tools import nanojev_gamer_v1_g2_router_trainability_0098 as tr
from tools import nanojev_gamer_v1_g2_competence_routing_0098 as m
from tools import nanojev_gamer_v1_g2_fuzz_and_correct_0095 as old

TAG = 'G2_0103_RAW_RERANK'
SCHEMA = 'nanojev.g2.raw-graph-reranking-0103.v1'
SEED = r.SEED
MODELS = ('raw_local', 'raw_graph_pair', 'raw_graph_joint')
BASES = ('frozen_0100', 'frozen_0101', 'raw_max')
INTERVENTIONS = BASES + MODELS
BLENDS = (.7, 1.)
OFFSETS = (-2., -1., 0., .75, 1.5)
RAW_OFFSETS = (-2., -1., 0., 1., 2.)
H = 80
D = b.D + 3*b.R + (b.R+1)


def emit(stage, data):
    print(TAG+'_'+stage+' '+json.dumps(data, sort_keys=True, allow_nan=False), flush=True)


def require(condition, label):
    if not condition:
        raise ValueError(TAG+'_'+label)


def _source(root, output):
    source, experts, r99, r100, r101, p101 = c._source(root, output)
    reportfile = source[8]/'reconstructability-0102'/'result.json'
    require(reportfile.is_file(), '0102_REPORT_MISSING')
    report = old.read(reportfile)
    require(report.get('schema') == c.SCHEMA and report.get('accepted_on_reused_fit_cv') is False,
            '0102_SCHEMA_OR_ACCEPTANCE_DRIFT')
    require(report.get('source_0101_sha256') == old.digest(p101),
            '0102_0101_SOURCE_HASH_CHANGED')
    require(report.get('production_promoted') is False and
            report.get('g3_unlocked') is False and
            report.get('confirmation_used') is False and report.get('dev_used') is False,
            '0102_SAFETY_POLICY_CHANGED')
    ceiling = report['upper_bounds']['by_family']['encounter']
    require(ceiling['cases'] == 138 and ceiling['structural_oracle_exact'] == 138 and
            ceiling['raw_top2_unthresholded_oracle_exact'] == 138,
            '0102_ORACLE_CEILING_CHANGED')
    require(len(report['folds']) == 3, '0102_FOLD_COUNT_DRIFT')
    return source, experts, r99, r100, r101, report, reportfile


def _raw_features(objects, margins, predictions, base_score):
    # Inputs are derived exclusively from the seven frozen expert outputs and
    # the current graph's object spans. No family, ID, target, or fold enters.
    require(margins.shape[0] == b.E and margins.shape[-1] == b.R,
            'RAW_EXPERT_AXIS_DRIFT')
    n = len(objects)
    require(tuple(base_score.shape) == (n,n,b.R+1), 'SCORE_SHAPE_DRIFT')
    require(tuple(predictions.shape) == (b.E,n,n), 'PREDICTION_SHAPE_DRIFT')
    rawmax = margins.max(0).values
    rawmean = margins.mean(0)
    rawstd = margins.std(0,unbiased=False)
    baseline = (base_score-base_score[:,:,:1]).clamp(-12.,12.)/6.
    return torch.cat((b._featurize(objects,margins,predictions),
                      rawmax.tanh(),rawmean.tanh(),rawstd.tanh(),baseline),-1)


class RawGraphReranker(nn.Module):
    """Local relation classification or contextual graph-wide reranking.

    Contextual variant pools pair representations over each source, destination,
    and the whole graph; each edge is then classified conditional on other
    edges competing for the same objects. Full raw scores remain accessible.
    """
    def __init__(self, contextual=False):
        super().__init__()
        self.contextual = bool(contextual)
        self.encoder = nn.Sequential(nn.Linear(D,H),nn.Tanh(),
                                     nn.Linear(H,H),nn.Tanh())
        self.classifier = nn.Sequential(
            nn.Linear(H*(4 if self.contextual else 1),H),nn.Tanh(),
            nn.Linear(H,b.R+1))

    def forward(self,objects,margins,predictions,base_score):
        x = self.encoder(_raw_features(objects,margins,predictions,base_score))
        if self.contextual:
            n = len(objects)
            mask = ~torch.eye(n,dtype=torch.bool,device=x.device)
            live = mask[...,None].to(x.dtype)
            denom = max(1,n-1)
            row = (x*live).sum(1)/denom
            col = (x*live).sum(0)/denom
            full = (x*live).sum((0,1))/max(1,n*(n-1))
            x = torch.cat((x,
                           row[:,None,:].expand(n,n,H),
                           col[None,:,:].expand(n,n,H),
                           full[None,None,:].expand(n,n,H)),dim=-1)
        return self.classifier(x)


def _loss(net, rec, experts, device, bundle, graph_objective):
    gold,valid = b._gold_mask(rec,device)
    with torch.no_grad():
        score,margins,preds,_,_ = r._base_scores(rec,experts,device,bundle)
    logits = net(rec['item']['objects'],margins,preds,score)
    # Train both labels and NONE. Incorrect edges and omitted correct edges
    # cannot disappear from the loss by being suppressed by expert decoding.
    hard_negative = valid & (gold==0) & ((preds>0).any(0)| (margins.max(0).values.max(-1).values>0))
    weights = valid.float()*(1.+3.*(gold>0).float()+2.*hard_negative.float())
    # Encounter labels drive the trained objective, but cannot be supplied to
    # the model at inference. Allocation remains in training for preservation.
    if rec['item']['row']['family']=='encounter': weights = weights*1.75
    ce = F.cross_entropy(logits.reshape(-1,b.R+1), gold.reshape(-1),
                         reduction='none').view_as(gold)
    pair_loss=(ce*weights).sum()/weights.sum().clamp_min(1.)
    if not graph_objective:
        return pair_loss
    prob = logits.softmax(-1)
    p_gold = prob.gather(-1,gold[...,None]).squeeze(-1)
    actual_positive=(gold>0)&valid
    predicted_mass=(1.-prob[...,0])*valid.float()
    correct_mass=(p_gold*actual_positive.float()).sum()
    true_count=actual_positive.float().sum()
    # A smooth, graph-scoped exactness proxy: penalize both false-positive
    # mass and any missing/incorrect typed relation, not only mean pair CE.
    soft_dice = 1.-(2.*correct_mass+1.)/(predicted_mass.sum()+true_count+1.)
    neg_wrong = ((1.-prob[...,0])*(~actual_positive & valid).float()).sum()
    pos_wrong = ((1.-p_gold)*actual_positive.float()).sum()
    # Smooth worst-edge error emphasizes the last errors that prevent a graph
    # from being exact. Not an oracle at inference; training gold only.
    errors=(1.-p_gold)[valid]
    worst=torch.logsumexp(8.*errors,0)/8. - math.log(max(1,errors.numel()))/8.
    return pair_loss + .8*soft_dice + .35*(neg_wrong+pos_wrong)/valid.sum().clamp_min(1.) + .3*worst


def _train(records,experts,device,bundle,kind,*,steps,lr,seed):
    require(kind in MODELS and records and steps>0 and 0<lr<1.,'TRAINING_ARGS_INVALID')
    torch.manual_seed(seed)
    net = RawGraphReranker(contextual=kind!='raw_local').to(device)
    optimizer = torch.optim.AdamW(net.parameters(),lr=lr,weight_decay=.0001)
    rng=random.Random(seed)
    for step in range(1,steps+1):
        batch=rng.sample(records,min(4,len(records)))
        optimizer.zero_grad(set_to_none=True)
        loss=torch.stack([_loss(net,rec,experts,device,bundle,
                        graph_objective=kind=='raw_graph_joint') for rec in batch]).mean()
        require(bool(torch.isfinite(loss)),'NONFINITE_LOSS')
        loss.backward()
        torch.nn.utils.clip_grad_norm_(net.parameters(),1.)
        optimizer.step()
        if step in (1,steps) or step%100==0:
            emit('TRAIN',{'arm':kind,'step':step,'steps':steps,
                          'loss':round(float(loss.detach()),6)})
    net.eval()
    return net


def _candidate_scores(raw,logits=None,*,blend=1.,offset=0.):
    require(0<=blend<=1 and -5.<=offset<=5.,'INVALID_CALIBRATION')
    # Explicit NONE class is the common zero reference for raw and learned
    # scores. Unlike previous pinning, the full relation set can be reranked.
    raw=raw.max(0).values.clamp(-8.,8.)
    if logits is None:
        margin=raw-offset
    else:
        require(logits.shape[-1]==b.R+1,'LOGIT_RELATION_DIMENSION_DRIFT')
        learned=(logits[...,1:]-logits[...,:1]).clamp(-12.,12.)
        margin=blend*learned+(1.-blend)*raw-offset
    zero=torch.zeros((*margin.shape[:-1],1),device=margin.device,dtype=margin.dtype)
    return torch.cat((zero,margin),dim=-1)


def _infer(rec,experts,spec,device,bundle,arm,model=None,*,blend=1.,offset=0.,reference101=None):
    require(arm in INTERVENTIONS,'UNKNOWN_ARM')
    for label in ('router','verifier'):
        require(not bundle[label].training,'UNFROZEN_'+label.upper())
    require(bundle['specialist'] is None or not bundle['specialist'].training,
            'UNFROZEN_SPECIALIST')
    if model is not None: require(not model.training,'RERANK_MODEL_NOT_FROZEN')
    objects=rec['item']['objects']
    with torch.no_grad():
        if arm=='frozen_0100':
            graph,_=r._infer(rec,experts,spec,device,bundle,'frozen_0100')
            return graph.cpu()
        if arm=='frozen_0101':
            require(reference101 is not None,'0101_REFERENCE_UNAVAILABLE')
            typed,chosen=reference101
            graph,_=r._infer(rec,experts,spec,device,bundle,chosen['mode'],typed,
                             **chosen['params'])
            return graph.cpu()
        _,margins,pred,_,_=r._base_scores(rec,experts,device,bundle)
        if arm in MODELS:
            require(model is not None,'RERANK_MODEL_MISSING')
            score,_,_,_,_=r._base_scores(rec,experts,device,bundle)
            logits=model(objects,margins,pred,score)
            calibrated=_candidate_scores(margins,logits,blend=blend,offset=offset)
        else:
            calibrated=_candidate_scores(margins,offset=offset)
        graph=r._decode(calibrated,objects,spec,device)
    return graph.cpu()


def _evaluate(records,experts,spec,device,bundle,arm,model=None,
              reference101=None,**params):
    overall=Counter();families=defaultdict(Counter)
    errors=defaultdict(Counter);pairs=defaultdict(Counter)
    for rec in records:
        result=_infer(rec,experts,spec,device,bundle,arm,model,
                      reference101=reference101,**params)
        count=m._count(rec,result)
        family=rec['item']['row']['family']
        overall.update(count);families[family].update(count)
        errors[family][f"fp{min(10,count['fp'])}_fn{min(10,count['fn'])}"]+=1
        gold=rec['item']['targets'].cpu()
        for rel in range(1,b.R+1):
            mask=~torch.eye(len(gold),dtype=torch.bool)
            ty=b.BIND_CLASSES[rel]
            pairs[family+'|'+ty]['missed']+=int((mask&(gold==rel)&(result!=rel)).sum())
            pairs[family+'|'+ty]['spurious']+=int((mask&(gold!=rel)&(result==rel)).sum())
    return {'overall':m._metric(overall),
            'by_family':{key:m._metric(value) for key,value in families.items()},
            'by_relation':{key:dict(value) for key,value in pairs.items()},
            'case_errors':{key:dict(value) for key,value in errors.items()}}


def _choices():
    yield 'frozen_0100',{}
    yield 'frozen_0101',{}
    for off in RAW_OFFSETS: yield 'raw_max',{'offset':off}
    for kind in MODELS:
        for blend in BLENDS:
            for off in OFFSETS:
                yield kind,{'blend':blend,'offset':off}


def _rank(result,control,reference):
    enc=result['by_family']['encounter']
    allocation=result['by_family']['allocation']
    adm=(result['overall']['edge_f1']>=control['overall']['edge_f1']-.003
         and allocation['edge_f1']>=control['by_family']['allocation']['edge_f1']-.01)
    return (int(adm), enc['exact'] if adm else -1,
            result['overall']['exact'] if adm else -1,
            enc['edge_f1'] if adm else -1.,
            result['overall']['edge_f1'] if adm else -1.,
            # Predictive stability against more recent, unaccepted research.
            result['overall']['edge_f1']-reference['overall']['edge_f1'] if adm else -1.)


def _select(val,experts,spec,device,bundle,models,reference101):
    control=_evaluate(val,experts,spec,device,bundle,'frozen_0100')
    more_recent=_evaluate(val,experts,spec,device,bundle,'frozen_0101',reference101=reference101)
    options=[]
    for arm,params in _choices():
        stat=(control if arm=='frozen_0100' else more_recent if arm=='frozen_0101'
             else _evaluate(val,experts,spec,device,bundle,arm,models.get(arm),
                            reference101=reference101,**params))
        options.append({'arm':arm,'params':params,
                        'rank':list(_rank(stat,control,more_recent)),
                        'encounter_exact':stat['by_family']['encounter']['exact'],
                        'encounter_f1':stat['by_family']['encounter']['edge_f1'],
                        'allocation_f1':stat['by_family']['allocation']['edge_f1'],
                        'overall_exact':stat['overall']['exact'],
                        'overall_f1':stat['overall']['edge_f1']})
    selected=max(enumerate(options),key=lambda pair:(tuple(pair[1]['rank']),-pair[0]))[1]
    return selected,options,control,more_recent


def _sum(folds,key,family=None):
    ct=Counter()
    for item in folds:
        stat=item[key]['by_family'][family] if family else item[key]['overall']
        for fld in ('tp','fp','fn','cases','exact','g1_exact'):
            ct[fld]+=stat[fld]
    return m._metric(ct)


def _save(folder,models,experts,source_hash,choice,role):
    folder.mkdir(parents=True,exist_ok=True)
    weights={}
    for name,model in models.items():
        p=folder/(name+'.safetensors')
        save_file({k:v.detach().cpu().contiguous() for k,v in model.state_dict().items()},str(p))
        weights[name]={'path':str(p),'sha256':old.digest(p)}
    manifest={'schema':SCHEMA,'role':role,'research_only':True,
       'production_promoted':False,'g3_unlocked':False,
       'dev_used':False,'confirmation_used':False,
       'source_0102_sha256':source_hash,'expert_order':list(experts),
       'expert_hashes':{key:val['sha256'] for key,val in experts.items()},
       'models':weights,'selected':choice}
    old.write(folder/'manifest.json',manifest)
    return manifest


def load_candidate(folder,experts,device,source_hash):
    manifest=old.read(Path(folder)/'manifest.json')
    require(manifest.get('schema')==SCHEMA and manifest.get('research_only') is True and
            manifest.get('production_promoted') is False and manifest.get('g3_unlocked') is False and
            manifest.get('dev_used') is False and manifest.get('confirmation_used') is False,
            'RESEARCH_POLICY_DRIFT')
    require(manifest['source_0102_sha256']==source_hash,'0102_SOURCE_HASH_CHANGED')
    require(manifest['expert_order']==list(experts),'EXPERT_ORDER_CHANGED')
    require(all(manifest['expert_hashes'][key]==item['sha256'] and
                old.digest(item['path'])==item['sha256'] for key,item in experts.items()),
            'FROZEN_EXPERT_MUTATED')
    loaded={}
    for name,entry in manifest['models'].items():
        require(name in MODELS and old.digest(entry['path'])==entry['sha256'],
                'RERANK_CHECKPOINT_MUTATED')
        net=RawGraphReranker(contextual=name!='raw_local').to(device)
        net.load_state_dict(load_file(entry['path'],device='cpu'),strict=True)
        net.eval();loaded[name]=net
    return loaded,manifest


def _full_fit_bundle(source,experts,r99,r100,device):
    """Construct the full-fit 0100 research adapter, never a fold-0 surrogate."""
    folder=Path(r100['full_fit_manifest']).parent
    models,manifest=b.load_candidate(folder,experts,device,r99['candidate']['sha256'])
    cfg=manifest['configuration']
    require(cfg['variant']==r100['selected_recipe_from_inner_val_majority'],
            '0100_FULL_FIT_RECIPE_CHANGED')
    # Full-fit 0099 router is frozen; 0100 full-fit intervention is also frozen.
    router99,policy=b.parent.load_frozen_adapter(Path(r99['candidate']['path']),
                                                 experts,device)
    require(cfg['router_policy']==policy,'0100_FULL_FIT_ROUTER_POLICY_CHANGED')
    router,specialist,verifier=b._candidate_args(cfg['variant'],router99,
             models['router'],models['specialist'],models['verifier'])
    if verifier is None: verifier=models['verifier']
    return {'router':router,'specialist':specialist,'verifier':verifier,
       'policy':policy,'s_threshold':cfg['specialist_threshold'],
       'v_threshold':cfg['veto_threshold'],
       'selected_variant':cfg['variant'],
       'source_manifest':manifest}


def audit(root=old.ROOT_DEFAULT,output=None):
    source,experts,r99,r100,r101,r102,p102=_source(root,output)
    for i in range(3):
        count=r102['folds'][i]['base_counts']
        require(count==r100['folds'][i]['base_counts'] and
                count==r101['folds'][i]['base_counts'], 'FOLD_PARTITION_DRIFT')
        _=r._bundle_from_0100(i,r100['folds'][i],r99,experts,'cpu')
        _,manifest=r.load_candidate(Path(r101['folds'][i]['manifest']).parent,experts,
                 'cpu',old.digest(source[8]/'encounter-bakeoff-0100'/'result.json'))
        require(manifest['configuration']['mode']==r101['folds'][i]['selected']['mode'],
                '0101_CHECKPOINT_POLICY_DRIFT')
    emit('AUDIT',{'ready':True,'expert_count':len(experts),'source_0102_sha256':old.digest(p102),
           'inference_uses':['raw_unthresholded_all_relation_margins','frozen_expert_predictions',
                             'typed_object_spans','frozen_0100_router_surface'],
           'learning_arms':list(MODELS),'controls':list(BASES),
           'candidate_grid':len(list(_choices())),
           'base_disjoint_reused_fit':True,'inner_validation_selection_only':True,
           'no_dev_or_confirmation':True,'production_promotion':False,'g3_unlocked':False})


def run(root=old.ROOT_DEFAULT,output=None,device='cuda',seed=SEED,steps=450,
        lr=.0015,force=False):
    require(device=='cpu' or (device.startswith('cuda') and torch.cuda.is_available()),
            'CUDA_UNAVAILABLE')
    require(1<=steps<=3000 and 0<lr<.1,'TRAIN_ARGS_OUT_OF_RANGE')
    source,experts,r99,r100,r101,r102,p102=_source(root,output)
    folder=source[8]/'raw-graph-reranking-0103'
    resultpath=folder/'result.json'
    require(force or not resultpath.exists(),'REPORT_EXISTS_USE_FORCE')
    emit('START',{'device':device,'folds':3,'steps_per_arm':steps,
        'controls':['frozen_0100','frozen_0101'],
        'training_arms':list(MODELS),'candidate_grid':len(list(_choices())),
        'objective':'PAIR_CLASSIFICATION_VS_GRAPH_CONTEXT_AND_EXACTNESS_SURROGATE',
        'gold_free_inference':True,'outer_test_after_validation_selection':True,
        'reused_fit_not_independent_certification':True})
    records,spec=b.parent._records(source,experts,device)
    folds=[];case_evidence=[]
    for fold,(idx,(train,val,test),counts) in enumerate(tr._split_by_base(records,3,seed)):
        require(idx==fold and counts==r100['folds'][fold]['base_counts'] and
                counts==r101['folds'][fold]['base_counts'] and
                counts==r102['folds'][fold]['base_counts'], 'BASE_DISJOINT_SPLIT_CHANGED')
        bundle=r._bundle_from_0100(fold,r100['folds'][fold],r99,experts,device)
        c101=r101['folds'][fold]
        typed,_=r.load_candidate(Path(c101['manifest']).parent,experts,device,
                                old.digest(source[8]/'encounter-bakeoff-0100'/'result.json'))
        ref101=(typed,c101['selected'])
        models={name:_train(train,experts,device,bundle,name,steps=steps,lr=lr,
                     seed=seed+fold*701+i*101) for i,name in enumerate(MODELS)}
        chosen,options,_,_=_select(val,experts,spec,device,bundle,models,ref101)
        # No outer-test result participates in selecting arm or its thresholds.
        control=_evaluate(test,experts,spec,device,bundle,'frozen_0100')
        baseline0100=r100['folds'][fold]['outer_test']['overall']
        require(all(control['overall'][k]==baseline0100[k] for k in ('tp','fp','fn','exact')),
                '0100_CONTROL_NOT_REPRODUCIBLE')
        frozen101=_evaluate(test,experts,spec,device,bundle,'frozen_0101',reference101=ref101)
        baseline0101=c101['outer_test']['overall']
        require(all(frozen101['overall'][k]==baseline0101[k] for k in ('tp','fp','fn','exact')),
                '0101_CONTROL_NOT_REPRODUCIBLE')
        selected=_evaluate(test,experts,spec,device,bundle,chosen['arm'],
                  models.get(chosen['arm']),reference101=ref101,**chosen['params'])
        # Retrospective stratification, deliberately AFTER selection/test scoring.
        priorcases={(x['fold'],x['test_index']):x for x in r102['case_diagnostics']}
        bycause=defaultdict(Counter)
        for j,rec in enumerate(test):
            row=priorcases[(fold,j)]
            require(row['base_id']==str(rec['item']['row']['base_id']) and
                    row['family']==rec['item']['row']['family'],
                    '0102_CASE_ORDER_DRIFT')
            got=_infer(rec,experts,spec,device,bundle,chosen['arm'],
                      models.get(chosen['arm']),reference101=ref101,**chosen['params'])
            outcome=m._count(rec,got)
            if row['family']=='encounter':
                cause=row['decoding_obstacle']
                bycause[cause].update({k:outcome[k] for k in ('tp','fp','fn','exact')})
                case_evidence.append({'fold':fold,'index':j,'base_id':row['base_id'],
                   'case_id':row['case_id'],'cause':cause,
                   'tp':outcome['tp'],'fp':outcome['fp'],
                   'fn':outcome['fn'],'exact':outcome['exact']})
        path=folder/('fold-'+str(fold))
        _save(path,models,experts,old.digest(p102),chosen,'base-disjoint-fold')
        reload_models,_=load_candidate(path,experts,device,old.digest(p102))
        for rec in test[:1]:
            a=_infer(rec,experts,spec,device,bundle,chosen['arm'],
                     models.get(chosen['arm']),reference101=ref101,**chosen['params'])
            d=_infer(rec,experts,spec,device,bundle,chosen['arm'],
                     reload_models.get(chosen['arm']),reference101=ref101,**chosen['params'])
            require(torch.equal(a,d),'RELOADED_INFERENCE_CHANGED')
        folds.append({'fold':fold,'base_counts':counts,'selected_on':'INNER_VALIDATION_ONLY',
           'chosen':chosen,'validation_bakeoff':options,'control_0100':control,
           'control_0101':frozen101,'selected':selected,
           'encounter_by_0102_cause':{key:dict(val) for key,val in bycause.items()},
           'manifest':str(path/'manifest.json')})
        emit('FOLD',{'fold':fold,'selected':chosen['arm'],
            'params':chosen['params'],'encounter_exact':[
                    control['by_family']['encounter']['exact'],
                    selected['by_family']['encounter']['exact']],
            'encounter_f1':[
                    control['by_family']['encounter']['edge_f1'],
                    selected['by_family']['encounter']['edge_f1']],
            'overall_exact':[control['overall']['exact'],selected['overall']['exact']]})
    baseline=_sum(folds,'control_0100');previous=_sum(folds,'control_0101')
    new=_sum(folds,'selected')
    byfamily={f:{'control_0100':_sum(folds,'control_0100',f),
                 'control_0101':_sum(folds,'control_0101',f),
                 'selected':_sum(folds,'selected',f)} for f in ('allocation','encounter')}
    gates={'encounter_exact_progress':byfamily['encounter']['selected']['exact']>0,
       'encounter_f1_not_worse_than_0100':byfamily['encounter']['selected']['edge_f1']>=byfamily['encounter']['control_0100']['edge_f1'],
       'allocation_f1_within_1pp':byfamily['allocation']['selected']['edge_f1']>=byfamily['allocation']['control_0100']['edge_f1']-.01,
       'overall_f1_not_worse_than_0101':new['edge_f1']>=previous['edge_f1'],
       'overall_exact_not_worse_than_0101':new['exact']>=previous['exact']}
    # Full-fit recipe is selected only from inner-validation choices, not the
    # outer-test winner; no certification or promotion is possible here.
    selected_arms=Counter(x['chosen']['arm'] for x in folds)
    final_arm=sorted(selected_arms,key=lambda a:(-selected_arms[a],
                   min(i for i,x in enumerate(folds) if x['chosen']['arm']==a)))[0]
    final_params=next(x['chosen']['params'] for x in folds if x['chosen']['arm']==final_arm)
    finalbundle=_full_fit_bundle(source,experts,r99,r100,device)
    fullmodels=({final_arm:_train(records,experts,device,finalbundle,final_arm,
           steps=steps,lr=lr,seed=seed+9999)} if final_arm in MODELS else {})
    # Full-fit candidate remains unvalidated; no outer fold was evaluated with
    # this final model, even though the research fold models were tested.
    _save(folder/'full-fit-research-only',fullmodels,experts,old.digest(p102),
          {'arm':final_arm,'params':final_params,'note':'NO_INDEPENDENT_CERTIFICATION','source_0100_full_fit_manifest':r100['full_fit_manifest']},
          'research-only')
    report={'schema':SCHEMA,'accepted_on_reused_fit_cv':all(gates.values()),
      'gates':gates,'control_0100':baseline,'control_0101':previous,
      'candidate':new,'by_family':byfamily,'folds':folds,
      'case_level_encounter':case_evidence,
      'source_0102_sha256':old.digest(p102),'oracle_capability_0102':{
            'structural':138,'expert_decoded':46,'raw_positive':95,
            'raw_unthresholded':138,'observed_prior':0},
      'full_fit_recipe':{'arm':final_arm,'params':final_params,
                         'warning':'FULL_FIT_RESEARCH_ONLY_UNVALIDATED'},
      'data_is_reused_fit_not_independent_certification':True,
      'gold_used_only_for_training_and_evaluation':True,
      'research_only':True,'production_promoted':False,'g3_unlocked':False,
      'dev_used':False,'confirmation_used':False}
    folder.mkdir(parents=True,exist_ok=True)
    old.write(resultpath,report)
    emit('CROSS_VALIDATED',{'accepted':all(gates.values()),'gates':gates,
       'control_0100':baseline,'control_0101':previous,'candidate':new,
       'by_family':byfamily,'full_fit_recipe':final_arm})
    emit('SAVED',{'path':str(resultpath),'sha256':old.digest(resultpath),
       'production_promoted':False,'g3_unlocked':False})


def selftest():
    from tools.nanojev_gamer_v1_g2_posttrain_graph_bakeoff_0088 import format_edge
    torch.manual_seed(103)
    kinds=b.SPAN_KINDS
    objects=[[3*i,3*i+2,kinds[i%len(kinds)]] for i in range(7)]
    gold=torch.zeros((7,7),dtype=torch.long)
    gold[0,1]=1;gold[1,2]=2;gold[3,4]=3;gold[5,6]=min(b.R,4)
    margins=torch.randn(b.E,7,7,b.R)*.25
    preds=torch.zeros((b.E,7,7),dtype=torch.long)
    # A correct relation is negative from every expert: unthresholded evidence
    # must permit the reranker to represent it even when all decoded as NONE.
    margins[:,0,1,0]=-2.
    names=[b.m.ANCHOR]+['synthetic_'+str(i) for i in range(1,b.E)]
    experts={key:{'sha256':'','path':''} for key in names}
    record={'item':{'objects':objects,'targets':gold,'full_gold':{
        format_edge(objects,i,j,int(gold[i,j])) for i,j in (gold>0).nonzero().tolist()},
        'row':{'family':'encounter','base_id':'toy-1'},'g1_exact':True},
        'expert_margins':margins,'predictions':{key:preds[i] for i,key in enumerate(names)}}
    router=b.m.CompetenceRouter(b.E).eval()
    bundle={'router':router,'specialist':None,'verifier':b.EdgeVerifier().eval(),
       'policy':'pin_ungated','s_threshold':.7,'v_threshold':.5,
       'selected_variant':'router_assembly'}
    spec={'method':'joint','threshold':0.,'out_caps':{},'in_caps':{}}
    for kind in MODELS:
        model=RawGraphReranker(kind!='raw_local')
        loss=_loss(model,record,experts,'cpu',bundle,kind=='raw_graph_joint')
        loss.backward()
        require(bool(torch.isfinite(loss)) and all(p.grad is not None for p in model.parameters()),
                'TRAINING_GRADIENT_MISSING_'+kind)
        model.eval()
        no_gold={'item':{'objects':objects},'expert_margins':margins,
                 'predictions':record['predictions']}
        output=_infer(no_gold,experts,spec,'cpu',bundle,kind,model)
        require(torch.equal(output,_infer(record,experts,spec,'cpu',bundle,kind,model)),
                'GOLD_LEAK_'+kind)
    # Force an arbitrary expert-negative gold edge from learned logits in score
    # space: the candidate cannot disappear because all experts said NONE.
    logits=torch.full((7,7,b.R+1),-10.)
    logits[:,:,0]=4.;logits[0,1,0]=-8.;logits[0,1,1]=9.
    adjusted=_candidate_scores(margins,logits,blend=1.,offset=0.)
    found=r._decode(adjusted,objects,spec,'cpu')
    require(int(found[0,1])==1,'UNTHRESHOLDED_NEGATIVE_CANDIDATE_NOT_REACHABLE')
    # Entire synthetic cycle: train, evaluate two families, compare frozen 0100
    # and multiple fresh candidates, then roundtrip persisted weights.
    alloc={**record,'item':{**record['item'],'row':{'family':'allocation','base_id':'toy-2'}}}
    trained={kind:_train([record,alloc],experts,'cpu',bundle,kind,
                       steps=2,lr=.002,seed=101+i) for i,kind in enumerate(MODELS)}
    sample=_evaluate([record,alloc],experts,spec,'cpu',bundle,'raw_graph_joint',
                     trained['raw_graph_joint'],blend=1.,offset=0.)
    require(sample['overall']['cases']==2,'EVALUATION_CASE_COUNT_CHANGED')
    # Inner selection cannot use an outer fold and must rank a frozen control.
    ref101=(r.TypedPredecoder().eval(),{'mode':'predecode_prior',
               'params':{'beta':1.5,'cut':.5}})
    choice,table,_,_=_select([record,alloc],experts,spec,'cpu',bundle,trained,ref101)
    require(choice['arm'] in INTERVENTIONS and len(table)==len(list(_choices())),
            'BAKEOFF_SELECTION_INVALID')
    with tempfile.TemporaryDirectory(prefix='g2_0103_') as tmp:
        folder=Path(tmp)
        for i,entry in enumerate(experts.values()):
            p=folder/f'expert_{i}.bin';p.write_bytes(str(i).encode())
            entry['path']=str(p);entry['sha256']=old.digest(p)
        _save(folder/'candidate',trained,experts,'mock-0102','raw_graph_joint','test')
        loaded,_=load_candidate(folder/'candidate',experts,'cpu','mock-0102')
        for kind in MODELS:
            expected=_infer(no_gold,experts,spec,'cpu',bundle,kind,trained[kind])
            actual=_infer(no_gold,experts,spec,'cpu',bundle,kind,loaded[kind])
            require(torch.equal(expected,actual),'CHECKPOINT_ROUNDTRIP_FAILED_'+kind)
        Path(next(iter(experts.values()))['path']).write_bytes(b'mutated')
        try:load_candidate(folder/'candidate',experts,'cpu','mock-0102')
        except ValueError as ex:
            require('FROZEN_EXPERT_MUTATED' in str(ex),'TAMPER_WRONG_EXCEPTION')
        else:raise ValueError(TAG+'_TAMPER_NOT_REJECTED')
    emit('SELFTEST',{'real_decoder':True,'negative_expert_edge_recoverable':True,
         'all_training_arms_finite_gradients':True,'graph_context_training':True,
         'graph_level_loss_training':True,'no_gold_at_inference':True,
         'validation_only_selection':True,'checkpoint_roundtrip':True,
         'expert_tamper_rejected':True,'production_unchanged':True})


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',nargs='?',choices=('audit','selftest','run'),default='run')
    parser.add_argument('--root',default=old.ROOT_DEFAULT)
    parser.add_argument('--output')
    parser.add_argument('--device',default='cuda')
    parser.add_argument('--seed',type=int,default=SEED)
    parser.add_argument('--steps',type=int,default=450)
    parser.add_argument('--lr',type=float,default=.0015)
    parser.add_argument('--force',action='store_true')
    args=parser.parse_args(argv)
    if args.command=='audit':return audit(args.root,args.output)
    if args.command=='selftest':return selftest()
    return run(args.root,args.output,args.device,args.seed,args.steps,args.lr,args.force)

if __name__=='__main__':main()
