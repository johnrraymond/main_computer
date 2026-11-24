"""G2-0106: READ-ONLY forensic inspection of G2-0103 checkpoints.

  python -m tools.nanojev_gamer_v1_g2_checkpoint_forensics_0106 audit
  python -m tools.nanojev_gamer_v1_g2_checkpoint_forensics_0106 selftest
  python -u -m tools.nanojev_gamer_v1_g2_checkpoint_forensics_0106 run --device cuda

No optimization, checkpoint modification, policy promotion, or cutover. Replays
held-out predictions from frozen fold checkpoints, compares decision stages,
and measures score/feature interference. Gold is used *after* inference for
retrospective attribution. Feature ablations are out-of-distribution sensitivity
probes, not claimed deployment improvements or proof of causal benefit.
"""
from __future__ import annotations
import argparse
import copy
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import statistics

import torch
from torch.nn import functional as F
from tools import nanojev_gamer_v1_g2_raw_graph_reranking_0103 as d
from tools import nanojev_gamer_v1_g2_graph_reconstruction_0101 as r
from tools import nanojev_gamer_v1_g2_encounter_bakeoff_0100 as b
from tools import nanojev_gamer_v1_g2_router_trainability_0098 as tr
from tools import nanojev_gamer_v1_g2_competence_routing_0098 as m
from tools import nanojev_gamer_v1_g2_fuzz_and_correct_0095 as old

TAG='G2_0106_CHECKPOINT_FORENSICS'
SCHEMA='nanojev.g2.checkpoint-forensics-0106.v1'
REL=list(b.BIND_CLASSES)
BINS=[float('-inf'),-4.,-2.,-1.,0.,1.,2.,4.,float('inf')]
CONF_BINS=[0.,.25,.50,.65,.75,.85,.9,.95,.99,1.0000001]

def check(cond,code):
    if not cond:raise ValueError(TAG+'_'+code)

def emit(k,doc):
    print(TAG+'_'+k+' '+json.dumps(doc,sort_keys=True,allow_nan=False),flush=True)

def counter_dict(x):return {k:int(v) for k,v in sorted(x.items())}

def level(value,bins=BINS):
    v=float(value)
    for i in range(len(bins)-1):
        if bins[i]<=v<bins[i+1]:return f'{bins[i]:g}..{bins[i+1]:g}'
    return 'out-of-range'

def weight_groups(model):
    """First-layer coefficient diagnostics; not feature-attribution claims."""
    W=model.encoder[0].weight.detach().float().cpu().abs()
    sizes=[('span_geometry',2*b.S+5),('expert_margins',b.E*b.R),
           ('expert_discrete_labels',b.E*(b.R+1)),('raw_expert_max',b.R),
           ('raw_expert_mean',b.R),('raw_expert_std',b.R),
           ('frozen_router_surface',b.R+1)]
    offset=0;out={}
    for name,n in sizes:
        part=W[:,offset:offset+n]
        out[name]={'n':n,'mean_abs':round(float(part.mean()),6),
                   'sum_abs':round(float(part.sum()),6)}
        offset+=n
    check(offset==W.shape[1],'FEATURE_GROUP_WIDTH_MISMATCH')
    return out

def model_health(models):
    report={}
    for name,model in models.items():
        params=[p.detach().float().cpu() for p in model.parameters()]
        check(all(torch.isfinite(p).all().item() for p in params),'NONFINITE_MODEL_'+name)
        report[name]={'parameter_count':sum(p.numel() for p in params),
             'weight_l2':round(math.sqrt(sum(float((p*p).sum()) for p in params)),6),
             'first_layer':weight_groups(model)}
    return report

def _context_flatten(model,objects,margins,preds,base):
    """Replace node-specific pooled context by the graph-wide pool, retaining
    the trained local pair encoding. OOD sensitivity, NOT a deployable arm."""
    feat=d._raw_features(objects,margins,preds,base)
    x=model.encoder(feat);n=len(objects);mask=(~torch.eye(n,device=x.device,dtype=torch.bool))[...,None].to(x.dtype)
    g=(x*mask).sum((0,1))/max(1,n*(n-1))
    full=g.view(1,1,-1).expand_as(x)
    return model.classifier(torch.cat((x,full,full,full),-1))

def _logits(model,objects,margins,preds,base,experiment=None):
    if experiment=='context_flat':
        check(model.contextual,'CONTEXT_PROBE_ON_LOCAL_MODEL')
        return _context_flatten(model,objects,margins,preds,base)
    if experiment=='drop_router_surface':
        x=d._raw_features(objects,margins,preds,base).clone()
        x[...,-(b.R+1):]=0.
        # This is a *feature sensitivity* test, not a consistent 0100 rerun.
        h=model.encoder(x)
        if model.contextual:
            n=len(objects);live=(~torch.eye(n,device=h.device,dtype=torch.bool))[...,None].to(h.dtype)
            row=(h*live).sum(1)/max(1,n-1)
            col=(h*live).sum(0)/max(1,n-1)
            full=(h*live).sum((0,1))/max(1,n*(n-1))
            h=torch.cat((h,row[:,None,:].expand_as(h),col[None,:,:].expand_as(h),
                         full[None,None,:].expand_as(h)),-1)
        return model.classifier(h)
    return model(objects,margins,preds,base)

def _stage(rec,experts,spec,device,bundle,models,chosen,probe=True):
    objects=rec['item']['objects'];n=len(objects)
    score,margins,preds,_,_=r._base_scores(rec,experts,device,bundle)
    raw_scores=d._candidate_scores(margins,offset=0.)
    reference_scores=score
    logits=None
    arm=chosen['arm'];cfg=chosen['params']
    if arm in d.MODELS:
        net=models[arm]
        logits=_logits(net,objects,margins,preds,score)
        selected_scores=d._candidate_scores(margins,logits,**cfg)
    elif arm=='raw_max':
        selected_scores=d._candidate_scores(margins,offset=cfg['offset'])
    else:
        selected_scores=None
    raw_pair=raw_scores.argmax(-1).long().cpu()
    raw_pair.fill_diagonal_(0)  # Unconstrained argmax must obey self-edge prohibition.
    raw_graph=r._decode(raw_scores,objects,spec,device).cpu()
    probe_extra={}
    if selected_scores is not None:
        pair=selected_scores.argmax(-1).long().cpu()
        pair.fill_diagonal_(0)
        graph=r._decode(selected_scores,objects,spec,device).cpu()
        if probe and logits is not None:
            for p in ('drop_router_surface','context_flat'):
                if p=='context_flat' and not models[arm].contextual:continue
                other=_logits(models[arm],objects,margins,preds,score,p)
                adjusted=d._candidate_scores(margins,other,**cfg)
                changed=(adjusted.argmax(-1).cpu()!=pair)
                mask=~torch.eye(n,dtype=torch.bool)
                probe_extra[p]={'pair_decisions_changed':int((changed&mask).sum()),
                         'decoder_edges_changed':int((r._decode(adjusted,objects,spec,device).cpu()!=graph).logical_and(mask).sum())}
            # Expert knockouts hold the frozen router surface fixed, so these
            # quantify learned-feature reliance, NOT a coherent model without
            # that expert. They are intentionally marked out-of-distribution.
            check(len(models)==len(d.MODELS),'FROZEN_MODEL_SET_INCOMPLETE')
            mask=~torch.eye(n,dtype=torch.bool)
            for expert_i in range(b.E):
                knockout_margins=margins.clone();knockout_preds=preds.clone()
                knockout_margins[expert_i]=0.;knockout_preds[expert_i]=0
                changed_logits=_logits(models[arm],objects,knockout_margins,
                                       knockout_preds,score)
                changed_scores=d._candidate_scores(margins,changed_logits,**cfg)
                new_graph=r._decode(changed_scores,objects,spec,device).cpu()
                changed_edges=(new_graph!=graph)&mask
                probe_extra[f'expert_{expert_i}_feature_knockout']={
                     'pair_decisions_changed':int(((changed_scores.argmax(-1).cpu()!=pair)&mask).sum()),
                     'decoder_edges_changed':int(changed_edges.sum())}
            # All fold models were trained on exactly the same training fold.
            # Fixed-offset comparison is descriptive, NOT recalibrated arm
            # selection or a valid post-hoc change to the accepted result.
            for alternative in d.MODELS:
                if alternative==arm:continue
                alternative_logits=models[alternative](objects,margins,preds,score)
                alternative_score=d._candidate_scores(margins,alternative_logits,
                                            blend=1.,offset=float(cfg.get('offset',0.)))
                alternative_graph=r._decode(alternative_score,objects,spec,device).cpu()
                probe_extra['alternative_'+alternative+'_fixed_offset']={
                    'pair_decisions_changed':int(((alternative_score.argmax(-1).cpu()!=pair)&mask).sum()),
                    'decoder_edges_changed':int(((alternative_graph!=graph)&mask).sum())}
    else:
        pair=None
        graph=d._infer(rec,experts,spec,device,bundle,arm)
    return {'raw':raw_graph,'raw_pair':raw_pair,'pair':pair,'graph':graph,
        'margins':margins,'preds':preds,'base':reference_scores,
        'raw_scores':raw_scores,'score':selected_scores,'logits':logits,
        'extra':probe_extra}

def _count(rec,graph):return m._count(rec,graph)

def _stats(records,experts,spec,device,bundle,models,choice):
    ct=Counter()
    with torch.no_grad():
        for rec in records:
            graph=d._infer(rec,experts,spec,device,bundle,choice['arm'],
                models.get(choice['arm']),**choice['params'])
            ct.update(_count(rec,graph))
    return m._metric(ct)

class Collector:
    def __init__(self):
        self.report=defaultdict(Counter)
        self.family=defaultdict(Counter)
        self.rel=defaultdict(Counter)
        self.comparisons=defaultdict(Counter)
        self.feature=defaultdict(Counter)
        self.hist=defaultdict(Counter)
        self.conf=defaultdict(Counter)
        self.types=defaultdict(Counter)
        self.examples=defaultdict(list)
        self.cases=[]
        self.near=defaultdict(Counter)
        self.fold=defaultdict(Counter)
        self.variant=defaultdict(Counter)
        self.change_rel=defaultdict(Counter)
    def add(self,rec, fold, phase,oldgraph,stage):
        fam=rec['item']['row']['family'];objects=rec['item']['objects']
        gold=rec['item']['targets'].long().cpu()
        pred=stage['graph'];n=len(objects);valid=~torch.eye(n,dtype=torch.bool)
        check(pred.shape==gold.shape,'PRED_GOLD_DIMENSION')
        ct=_count(rec,pred)
        self.family[fam].update(ct)
        self.fold[str(fold)].update(ct)
        case_id=str(rec['item']['row'].get('case_id',''))
        variant=(next((x for x in ('action_removal','role_swap','renaming','added_distractor','base')
             if case_id.endswith('-'+x)), 'unclassified'))
        self.variant[fam+'|'+variant].update(ct)
        local=stage['pair'];raw=stage['raw'];raw_local=stage['raw_pair']
        if local is not None:
            self.comparisons['pairwise_direct'].update(_count(rec,local))
            self.comparisons['raw_max_graph'].update(_count(rec,raw))
            self.comparisons['raw_max_pair'].update(_count(rec,raw_local))
            for k,mask in {
                'decoder_changed_label':valid&(pred!=local),
                'decoder_destroyed_pair_correct':valid&(local==gold)&(pred!=gold),
                'decoder_repaired_pair_error':valid&(local!=gold)&(pred==gold),
                'reranker_fixed_raw_label':valid&(raw_local!=gold)&(local==gold),
                'reranker_damaged_raw_label':valid&(raw_local==gold)&(local!=gold),
                'reranker_fixed_raw_graph':valid&(raw!=gold)&(pred==gold),
                'reranker_damaged_raw_graph':valid&(raw==gold)&(pred!=gold),
                'correct_gold_not_pair_top1':valid&(gold>0)&(local!=gold),
                'correct_gold_pair_top1_but_not_decoded':valid&(gold>0)&(local==gold)&(pred!=gold),
            }.items():self.comparisons['stage_changes'][k]+=int(mask.sum())
        delta_old={
            'baseline_tp_removed':valid&(oldgraph==gold)&(gold>0)&(pred!=gold),
            'baseline_fp_removed':valid&(oldgraph>0)&(oldgraph!=gold)&(pred!=oldgraph),
            'baseline_fp_created':valid&(pred>0)&(pred!=gold)&(oldgraph!=pred),
            'baseline_tp_created':valid&(pred==gold)&(gold>0)&(oldgraph!=gold),
            'baseline_correct_none_destroyed':valid&(oldgraph==0)&(gold==0)&(pred>0),
            'baseline_missing_gold_recovered':valid&(gold>0)&(oldgraph!=gold)&(pred==gold),
        }
        for k,mask in delta_old.items():
            self.comparisons['vs_frozen_0100'][k]+=int(mask.sum())
            if k in ('baseline_tp_created','baseline_tp_removed'):
                ref=gold
            elif k=='baseline_fp_removed':ref=oldgraph
            else:ref=pred
            for rid in range(1,b.R+1):
                self.change_rel[REL[rid]][k]+=int((mask&(ref==rid)).sum())
        score=stage['score']
        if score is not None:
            prob=score.softmax(-1).cpu()
        else:prob=None
        margins=stage['margins'].detach().cpu()
        preds=stage['preds'].detach().cpu()
        for i,j in valid.nonzero().tolist():
            true=int(gold[i,j]);got=int(pred[i,j])
            if true==got and true==0:continue
            typ=REL[true] if true else 'none'
            if true>0:
                self.rel[typ]['gold']+=1
                if got==true:self.rel[typ]['true_positive']+=1
            if got>0:self.rel[REL[got]]['predicted']+=1
            if true>0 and got!=true:
                mx=float(margins[:,i,j,true-1].max())
                support=int((preds[:,i,j]==true).sum())
                cls='gold_error_'+typ
                self.rel[typ]['missed']+=1
                self.rel[typ]['miss_expert_decoded_supported' if support else 'miss_expert_not_decoded']+=1
                self.rel[typ]['miss_expert_raw_positive' if mx>0 else 'miss_expert_raw_nonpositive']+=1
                self.hist['miss_gold_raw_max'][level(mx)]+=1
                if score is not None:
                    sg=float(score[i,j,true]-score[i,j,0]);self.hist['miss_gold_learned_margin'][level(sg)]+=1
                    rank=1+int((score[i,j]>score[i,j,true]).sum())
                    self.rel[typ]['miss_correct_rank1' if rank==1 else 'miss_correct_rank2' if rank==2 else 'miss_correct_rank3plus']+=1
                self.types[f'{objects[i][2]}->{objects[j][2]}|{typ}']['missed']+=1
                self.examples['FN'].append((float(score[i,j,true]-score[i,j,0]) if score is not None else mx,
                     {'fold':fold,'family':fam,'case':str(rec['item']['row'].get('case_id',rec['item']['row'].get('base_id','?'))),
                      'pair':[i,j],'source_type':objects[i][2],'dest_type':objects[j][2],
                      'gold':typ,'got':REL[got], 'raw_gold_max':round(mx,4),'expert_votes':support,
                      'learned_gold_margin':round(sg,4) if score is not None else None,
                      'pair_was_correct':bool(local is not None and int(local[i,j])==true)}))
            if got>0 and got!=true:
                rel=REL[got];self.rel[rel]['spurious']+=1
                self.rel[rel]['spurious_wrong_type' if true>0 else 'spurious_none_pair']+=1
                self.rel[rel]['spurious_expert_voted' if (preds[:,i,j]==got).any() else 'spurious_not_expert_voted']+=1
                self.types[f'{objects[i][2]}->{objects[j][2]}|{rel}']['spurious']+=1
                mx=float(margins[:,i,j,got-1].max())
                self.hist['spurious_raw_max'][level(mx)]+=1
                if score is not None:
                    sg=float(score[i,j,got]-score[i,j,0]);self.hist['spurious_learned_margin'][level(sg)]+=1
                self.examples['FP'].append((float(score[i,j,got]-score[i,j,0]) if score is not None else mx,
                     {'fold':fold,'family':fam,'case':str(rec['item']['row'].get('case_id',rec['item']['row'].get('base_id','?'))),
                      'pair':[i,j],'source_type':objects[i][2],'dest_type':objects[j][2],
                      'gold':typ,'got':rel, 'raw_proposed_max':round(mx,4),
                      'expert_votes':int((preds[:,i,j]==got).sum()),
                      'learned_selected_margin':round(sg,4) if score is not None else None,
                      'pair_was_correct':bool(local is not None and int(local[i,j])==true)}))
            if prob is not None and got>0:
                p=float(prob[i,j,got]);key=level(p,CONF_BINS)
                self.conf[key]['predicted']+=1
                self.conf[key]['correct']+=int(got==true)
                self.conf[key]['probability_sum_milli']+=int(round(p*1000))
        for name,stats in stage['extra'].items():self.feature[name].update(stats)
        if fam=='encounter':
            k='exact' if ct['exact'] else 'only_FP' if ct['fp'] and not ct['fn'] else 'only_FN' if ct['fn'] and not ct['fp'] else 'FP_and_FN'
            self.near['encounter'][k]+=1
            self.near['encounter']['fp0']+=int(ct['fp']==0)
            self.near['encounter']['fn0']+=int(ct['fn']==0)
            self.near['encounter']['at_most_1_error']+=int(ct['fp']+ct['fn']<=1)
            self.near['encounter']['at_most_2_errors']+=int(ct['fp']+ct['fn']<=2)
            self.near['encounter']['at_most_3_errors']+=int(ct['fp']+ct['fn']<=3)
            self.near['encounter']['at_most_5_errors']+=int(ct['fp']+ct['fn']<=5)
        self.cases.append({'fold':fold,'family':fam,'base_id':str(rec['item']['row']['base_id']),
            'case_id':str(rec['item']['row'].get('case_id',rec['item']['row'].get('id',''))),
            'tp':ct['tp'],'fp':ct['fp'],'fn':ct['fn'],'exact':ct['exact'],
            'old_fp':_count(rec,oldgraph)['fp'],'old_fn':_count(rec,oldgraph)['fn']})
    def result(self):
        sample={}
        for typ,arr in self.examples.items():
            # Positive scores on wrong edges and negative scores on required
            # edges are useful investigations, never case-level model inputs.
            arr=sorted(arr,key=lambda x:x[0],reverse=(typ=='FP'))
            sample[typ]=[v for _,v in arr[:25]]
        calib=[]
        for k,v in sorted(self.conf.items()):
            n=v['predicted']
            calib.append({'score_bucket':k,'n':n,'precision':v['correct']/n,
                          'mean_score':v['probability_sum_milli']/1000./n})
        ece=(sum(abs(x['precision']-x['mean_score'])*x['n'] for x in calib)/
             max(1,sum(x['n'] for x in calib)))
        return {'by_family':{k:m._metric(v) for k,v in self.family.items()},
           'by_fold':{k:m._metric(v) for k,v in self.fold.items()},
           'by_relation':{k:counter_dict(v) for k,v in self.rel.items()},
           'by_case_variant':{k:m._metric(v) for k,v in self.variant.items()},
           'baseline_change_by_relation':{k:counter_dict(v) for k,v in self.change_rel.items()},
           'score_histograms':{k:counter_dict(v) for k,v in self.hist.items()},
           'object_type_pairs':{k:counter_dict(v) for k,v in self.types.items()},
           'stage_comparisons':{k:counter_dict(v) for k,v in self.comparisons.items()},
           'feature_sensitivity_OOD':{k:counter_dict(v) for k,v in self.feature.items()},
           'predicted_positive_score_reliability':calib,
           'positive_selection_score_ECE_not_probability_calibration':ece,
           'near_exact':{k:counter_dict(v) for k,v in self.near.items()},
           'worst_examples':sample,'case_level':self.cases}


def _load(root,output,device,include_models=True):
    src,ex,r99,r100,r101,r102,p102=d._source(root,output)
    path=src[8]/'raw-graph-reranking-0103'/'result.json'
    check(path.is_file(),'0103_RESULT_NOT_FOUND')
    report=old.read(path)
    check(report.get('schema')==d.SCHEMA and report.get('accepted_on_reused_fit_cv') is True,
          '0103_SCHEMA_OR_ACCEPTANCE_CHANGED')
    check(not report.get('production_promoted') and not report.get('g3_unlocked') and
          not report.get('dev_used') and not report.get('confirmation_used'),
          '0103_SAFETY_LINEAGE_CHANGED')
    check(report['source_0102_sha256']==old.digest(p102),'0103_SOURCE_0102_CHANGED')
    check(len(report['folds'])==3,'0103_FOLD_DRIFT')
    return src,ex,r99,r100,r101,r102,p102,report,path


def audit(root=old.ROOT_DEFAULT,output=None):
    src,ex,r99,r100,r101,r102,p102,report,path=_load(root,output,'cpu')
    snapshots=[]
    for i,fold in enumerate(report['folds']):
        models,manifest=d.load_candidate(Path(fold['manifest']).parent,ex,'cpu',old.digest(p102))
        check(manifest['selected']==fold['chosen'],'0103_SELECTED_POLICY_CHANGED')
        check(set(models)==set(d.MODELS),'0103_FOLD_MODEL_ARM_DRIFT')
        snapshots.append({'fold':i,'chosen':fold['chosen']['arm'],
            'checkpoint_sha256':{k:v['sha256'] for k,v in manifest['models'].items()},
            'chosen_parameters':fold['chosen']['params']})
    # Full-fit model is NOT interchangeable with fold-tested models. Inspect
    # lineage only; deliberately do not reuse its weights for a fold replay.
    full_folder=src[8]/'raw-graph-reranking-0103'/'full-fit-research-only'
    full_models,full_manifest=d.load_candidate(full_folder,ex,'cpu',old.digest(p102))
    check(full_manifest['role']=='research-only' and not full_manifest['production_promoted'],
          'FULL_FIT_POLICY_CHANGED')
    emit('AUDIT',{'ready':True,'frozen_fold_checkpoints':snapshots,
        'full_fit_checkpoint':{'arm':full_manifest['selected']['arm'],
            'path':str(full_folder/'manifest.json'),'research_only_unvalidated':True,
            'models':list(full_models)},'heldout_case_count':275,
        'report_sha256':old.digest(path),'checkpoint_modification':False,
        'new_training':False,'production_promotion':False,'confirmation_used':False})


def run(root=old.ROOT_DEFAULT,output=None,device='cuda',force=False):
    check(device=='cpu' or (device.startswith('cuda') and torch.cuda.is_available()),'CUDA_UNAVAILABLE')
    src,ex,r99,r100,r101,r102,p102,report,original=_load(root,output,device)
    dest=src[8]/'checkpoint-forensics-0106'/'result.json'
    check(force or not dest.exists(),'FORENSIC_REPORT_EXISTS_USE_FORCE')
    emit('START',{'device':device,'read_only_checkpoints':True,'steps':0,
        'heldout_selection_frozen':True,'reused_fit_cv':True,'full_fit_not_evaluated':True,
        'base_disjoint':True,'calibration_is_diagnostic_only':True})
    records,spec=b.parent._records(src,ex,device)
    collector=Collector();fold_evidence=[];total=0
    with torch.inference_mode():
        for fold,(idx,(train,val,test),counts) in enumerate(tr._split_by_base(records,3,d.SEED)):
            check(idx==fold and counts==r100['folds'][fold]['base_counts'] and
                counts==report['folds'][fold]['base_counts'],'SPLIT_OR_COUNTS_CHANGED')
            bundle=r._bundle_from_0100(fold,r100['folds'][fold],r99,ex,device)
            models,manifest=d.load_candidate(Path(report['folds'][fold]['manifest']).parent,
                                            ex,device,old.digest(p102))
            chosen=report['folds'][fold]['chosen']
            check(manifest['selected']==chosen,'FOLD_CHECKPOINT_SELECTED_POLICY_CHANGED')
            health=model_health(models)
            fit=_stats(train,ex,spec,device,bundle,models,chosen)
            inner=_stats(val,ex,spec,device,bundle,models,chosen)
            # Strong replay checks against exact frozen heldout benchmark for
            # ALL aggregate counts (including family and exact graph counts).
            replay=_stats(test,ex,spec,device,bundle,models,chosen)
            expected=report['folds'][fold]['selected']['overall']
            check(all(replay[k]==expected[k] for k in ('tp','fp','fn','exact','cases')),
                  'FROZEN_0103_REPLAY_MISMATCH_FOLD_'+str(fold))
            for rec in test:
                chosen_stage=_stage(rec,ex,spec,device,bundle,models,chosen,probe=True)
                actual=d._infer(rec,ex,spec,device,bundle,chosen['arm'],
                    models.get(chosen['arm']),**chosen['params'])
                check(torch.equal(actual,chosen_stage['graph']),
                      'FROZEN_0103_CASE_REPLAY_MISMATCH_FOLD_'+str(fold))
                frozen=d._infer(rec,ex,spec,device,bundle,'frozen_0100')
                collector.add(rec,fold,'test',frozen,chosen_stage)
                total+=1
            fold_evidence.append({'fold':fold,'arm':chosen['arm'],
                'params':chosen['params'],'train_in_sample':fit,
                'inner_validation_selected_on':inner,'outer_heldout':replay,
                'weights':health,'checkpoint_hashes':{k:v['sha256'] for k,v in manifest['models'].items()}})
            emit('FOLD',{'fold':fold,'chosen':chosen['arm'],'test':replay,
              'train_f1':fit['edge_f1'],'validation_f1':inner['edge_f1'],
              'test_f1':replay['edge_f1'],'model_parameters':health[chosen['arm']]['parameter_count'] if chosen['arm'] in health else 0})
    observed=collector.result()
    cv=Counter()
    for stats in observed['by_family'].values():
        for k in ('tp','fp','fn','cases','exact','g1_exact'):cv[k]+=stats[k]
    metric=m._metric(cv)
    check(all(metric[k]==report['candidate'][k] for k in ('tp','fp','fn','exact','cases')),
          'GLOBAL_0103_REPLAY_MISMATCH')
    check(total==report['candidate']['cases'] and
          sum(v['cases'] for v in observed['by_family'].values())==report['candidate']['cases'],
          'CASE_COVERAGE_INCOMPLETE')
    full_folder=src[8]/'raw-graph-reranking-0103'/'full-fit-research-only'
    full_models,full_manifest=d.load_candidate(full_folder,ex,'cpu',old.digest(p102))
    doc={'schema':SCHEMA,'source_0103_report_sha256':old.digest(original),
        'source_0102_report_sha256':old.digest(p102),
        'source_0103_checkpoint_fold_manifests':[x['checkpoint_hashes'] for x in fold_evidence],
        'research_only':True,'production_promoted':False,'g3_unlocked':False,
        'dev_used':False,'confirmation_used':False,'model_training':False,
        'checkpoint_modification':False,'data_reused_fit_not_independent_certification':True,
        'full_fit_research_candidate_not_fold_tested':{
            'recipe':full_manifest['selected'],
            'checkpoint_hashes':{k:v['sha256'] for k,v in full_manifest['models'].items()},
            'weights':model_health(full_models)},
        'frozen_0103_exact_replay':metric,'fold_diagnostics':fold_evidence,
        'diagnostic':observed,
        'interpretation_limits':[
          'Training-set metrics are in-sample; validation selected policy; outer folds reused by earlier experiments',
          'Class-weighted CE and calibrated offset do not guarantee probabilistic calibration',
          'Context flattening, expert knockouts, and router-feature dropout are out-of-distribution sensitivity tests, not causal upgrades',
          'The alternative-arm probe uses the chosen arm offset on all arms, not separately validated arm calibrations',
          'Gold used only to score the already-frozen predictions; no gold at inference',
          'This is a checkpoint analysis, not a new training intervention']}
    dest.parent.mkdir(parents=True,exist_ok=True)
    old.write(dest,doc)
    emit('DIAGNOSIS',{'by_relation':observed['by_relation'],
        'near_exact':observed['near_exact'],
        'stage_changes':observed['stage_comparisons'],
        'feature_sensitivity_OOD':observed['feature_sensitivity_OOD'],
        'positive_score_ECE':observed['positive_selection_score_ECE_not_probability_calibration']})
    emit('SAVED',{'path':str(dest),'sha256':old.digest(dest),
        'baseline_reproduced':True,'checkpoints_unchanged':True,
        'production_promoted':False})


def selftest():
    torch.manual_seed(106)
    objects=[[2*i,2*i+1,b.SPAN_KINDS[i%len(b.SPAN_KINDS)]] for i in range(8)]
    margins=torch.randn(b.E,8,8,b.R)*.5
    preds=torch.zeros((b.E,8,8),dtype=torch.long)
    base=torch.zeros((8,8,b.R+1))
    local=d.RawGraphReranker(False).eval()
    context=d.RawGraphReranker(True).eval()
    with torch.inference_mode():
        expected=context(objects,margins,preds,base)
        actual=_logits(context,objects,margins,preds,base)
        check(torch.allclose(expected,actual),'REFERENCE_LOGITS_CHANGED')
        flat=_logits(context,objects,margins,preds,base,'context_flat')
        dropped=_logits(context,objects,margins,preds,base,'drop_router_surface')
        check(flat.shape==dropped.shape==expected.shape,'CONTEXT_FEATURE_SHAPE_CHANGED')
        unchanged=_logits(local,objects,margins,preds,base)
        check(torch.equal(unchanged,local(objects,margins,preds,base)),'LOCAL_REFERENCE_CHANGED')
        check(sum(x['n'] for x in weight_groups(context).values())==d.D,'FEATURE_LAYOUT_DRIFT')
        gold=torch.zeros((8,8),dtype=torch.long);gold[1,2]=1;gold[2,3]=2
        from tools.nanojev_gamer_v1_g2_posttrain_graph_bakeoff_0088 import format_edge
        record={'item':{'objects':objects,'targets':gold,
          'full_gold':{format_edge(objects,i,j,int(gold[i,j])) for i,j in (gold>0).nonzero().tolist()},
          'row':{'family':'encounter','base_id':'test-106','case_id':'test-106-c'},
          'g1_exact':True}}
        mask=torch.zeros((8,8),dtype=torch.long);mask[1,2]=1;mask[4,5]=1
        scores=torch.randn(8,8,b.R+1)
        col=Collector()
        col.add(record,0,'test',mask,{'graph':mask,'pair':scores.argmax(-1),
                'raw':mask,'raw_pair':scores.argmax(-1),
                'score':scores,'margins':margins,'preds':preds,
                'extra':{'context_flat':{'pair_decisions_changed':2,'decoder_edges_changed':1}}})
        report=col.result()
        check(report['by_family']['encounter']['exact']==0 and
              report['by_relation'][REL[1]]['spurious']==1 and
              report['by_relation'][REL[2]]['missed']==1,'ERROR_ACCOUNTING_INVALID')
        check(report['feature_sensitivity_OOD']['context_flat']['pair_decisions_changed']==2,
              'ABLATION_ACCOUNTING_INVALID')
    emit('SELFTEST',{'weight_feature_group_layout':True,'graph_context_sensitivity':True,
           'router_surface_sensitivity':True,'frozen_reference_logits_exact':True,
           'typed_error_attribution':True,'graph_exact_accounting':True,
           'checkpoint_modification':False,'new_training':False})


def main(argv=None):
    a=argparse.ArgumentParser(description=__doc__)
    a.add_argument('command',nargs='?',choices=['audit','selftest','run'],default='audit')
    a.add_argument('--root',default=old.ROOT_DEFAULT)
    a.add_argument('--output')
    a.add_argument('--device',default='cuda')
    a.add_argument('--force',action='store_true')
    args=a.parse_args(argv)
    if args.command=='audit':return audit(args.root,args.output)
    if args.command=='selftest':return selftest()
    return run(args.root,args.output,args.device,args.force)

if __name__=='__main__':main()
