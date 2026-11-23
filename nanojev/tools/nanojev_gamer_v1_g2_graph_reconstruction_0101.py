"""G2-0101: verifier-guided graph reconstruction (research-only).

Reference: frozen, sealed G2-0100 fold models, which were NOT promoted because
all encounter graphs remained inexact. The study deliberately isolates graph
assembly from further router/expert training:

  python -u -m tools.nanojev_gamer_v1_g2_graph_reconstruction_0101 audit
  python -u -m tools.nanojev_gamer_v1_g2_graph_reconstruction_0101 selftest
  python -u -m tools.nanojev_gamer_v1_g2_graph_reconstruction_0101 run --device cuda

Within each of the SAME 3 G2-0100 fit-only base-disjoint folds, a fresh typed
predecoder is trained on inner TRAIN examples only. Validation chooses between
frozen post-veto, verifier-guided redecoding, predecoder-informed joint decoding
and their combinations before outer TEST data are scored. Gold is never passed
to model inference. No existing checkpoints/production code are changed.
"""
from __future__ import annotations
import argparse
from collections import Counter,defaultdict
from pathlib import Path
import hashlib
import json
import math
import random
import statistics
import tempfile

import torch
from torch import nn
from torch.nn import functional as F
from safetensors.torch import save_file,load_file

from tools import nanojev_gamer_v1_g2_encounter_bakeoff_0100 as b
from tools import nanojev_gamer_v1_g2_router_trainability_0098 as tr
from tools import nanojev_gamer_v1_g2_competence_routing_0098 as m
from tools import nanojev_gamer_v1_g2_fuzz_and_correct_0095 as old

TAG='G2_0101_RECONSTRUCTION'
SCHEMA='nanojev.g2.graph-level-reconstruction-0101.v1'
SEED=990099
MODES=('frozen_0100','redecode_veto','predecode_prior','prior_redecode')
PRIOR_BETAS=(.75,1.5,3.0)
PRIOR_CUTS=(.35,.50,.65)
VETO_THRESHOLDS=(.35,.50,.65,.80)
ITERATIONS=(1,2)

def emit(stage,payload):
    print(TAG+'_'+stage+' '+json.dumps(payload,sort_keys=True,allow_nan=False),flush=True)

def require(cond,reason):
    if not cond:raise ValueError(TAG+'_'+reason)

class TypedPredecoder(nn.Module):
    """One inference-time probability per *relation type* for every ordered pair.

    Unlike G2-0100's verifier, it is trained on both missing relationships and
    negative pairs BEFORE global decoding; can propose a positive edge or a
    different relation as well as veto one. No gold enters forward().
    """
    def __init__(self):
        super().__init__()
        self.net=nn.Sequential(nn.Linear(b.D+b.R+1,128),nn.Tanh(),
            nn.Linear(128,96),nn.Tanh(),nn.Linear(96,b.R))
    def forward(self,objects,margins,predictions,score):
        n=len(objects)
        require(score.shape==(n,n,b.R+1),'PRIOR_SCORE_DIMENSION_DRIFT')
        features=b._featurize(objects,margins,predictions)
        return self.net(torch.cat((features,score.softmax(-1)),dim=-1))


def _bundle_from_0100(fold,record,report99,experts,device):
    """Preserve the exact G2-0100 selection, training order and checkpoint hashes."""
    counts=record['base_counts']
    frozen,policy=b._load_fold(fold,report99,experts,device,counts)
    folder=Path(record['manifest']).parent
    require((folder/'manifest.json').is_file(),'MISSING_FOLD_0100_MANIFEST')
    models,manifest=b.load_candidate(folder,experts,device,report99['candidate']['sha256'])
    require(manifest['configuration']['variant']==record['selected_variant'],
            '0100_SELECTED_VARIANT_MANIFEST_MISMATCH')
    require(manifest['configuration']['base_counts']==counts,
            '0100_BASE_COUNTS_DRIFT')
    cfg=manifest['configuration']
    router,specialist,verifier=b._candidate_args(record['selected_variant'],frozen,
                    models['router'],models['specialist'],models['verifier'])
    if verifier is None:
        # The standalone 0100 verifier exists even when its arm was not selected.
        # It is a frozen counterfactual source, not a promoted component.
        verifier=models['verifier']
    return {'router':router,'specialist':specialist,'verifier':verifier,
            'policy':policy,'s_threshold':cfg['specialist_threshold'],
            'v_threshold':cfg['veto_threshold'],'frozen':frozen,
            'source_manifest':manifest,'manifest_path':str(folder/'manifest.json'),
            'selected_variant':record['selected_variant']}


def _source(root,output):
    source,experts,report99,_=b._base_source(root,output)
    rootdir=source[8]
    reportfile=rootdir/'encounter-bakeoff-0100'/'result.json'
    require(reportfile.is_file(),'0100_RESEARCH_REPORT_MISSING')
    report=old.read(reportfile)
    require(report.get('schema')==b.SCHEMA,'0100_SCHEMA_MISMATCH')
    require(report.get('accepted_on_reused_fit_cv') is False,
            '0100_ACCEPTANCE_STATE_CHANGED')
    require(report.get('production_promoted') is False and
            report.get('g3_unlocked') is False and
            report.get('dev_used') is False and
            report.get('confirmation_used') is False,'0100_DATA_POLICY_DRIFT')
    require(len(report['folds'])==3 and len(report99['folds'])==3,
            '0100_FOLD_COUNT_DRIFT')
    require(report['g2_0099_control']['tp']==report99['oof_graph']['tp'] and
            report['g2_0099_control']['fp']==report99['oof_graph']['fp'] and
            report['g2_0099_control']['fn']==report99['oof_graph']['fn'],
            '0100_0099_CONTROL_DRIFT')
    return source,experts,report99,report,reportfile


def _base_scores(rec,experts,device,bundle):
    score,margins,pred,router_swaps=b._frozen_scores(bundle['router'],rec,experts,
                                                    device,bundle['policy'])
    sp=bundle['specialist']
    specialist_swaps=0
    if sp is not None:
        score,specialist_swaps=b._spec_override(score,rec['item']['objects'],
             margins,pred,sp,bundle['s_threshold'])
    return score,margins,pred,router_swaps,specialist_swaps


def _decode(score,objects,spec,device):
    decoded,_=old._decode_matrix({'objects':objects},score,spec)
    return decoded.to(device).long()


def _pre_scores(score,typed_logits,beta,cut):
    """Non-destructive typed evidence: permit replacing/adding missed relations.

    Compress pinning margins *only for this ablation*, then apply per-relation
    logit evidence relative to the validation-selected threshold. This is a
    new score surface for the original capacity-constrained decoder.
    """
    require(0<cut<1 and beta>0,'PREDECODER_PARAMETERS_INVALID')
    n=score.shape[0]
    baseline=score[...,1:]-score[...,0:1]
    # Prevent forced +/-12 pins from drowning out verified alternative edges.
    clamped=baseline.clamp(-8.,8.)/4.
    evidence=typed_logits.clamp(-10.,10.)-math.log(cut/(1-cut))
    newmargin=clamped+beta*evidence
    zeros=torch.zeros((n,n,1),dtype=score.dtype,device=score.device)
    return torch.cat((zeros,newmargin),dim=-1)


def _redecode_veto(score,objects,spec,verifier,margins,preds,threshold,rounds,device):
    """Reject low-confidence edges *in score space* and re-run joint decoding.

    Capacity freed by a rejection may be occupied by an alternative source,
    destination or relation. Rejections remain demoted on subsequent rounds.
    No forced removal of edges after the final decoding.
    """
    require(rounds in ITERATIONS and 0<threshold<1,'REDECODER_PARAMETERS_INVALID')
    new=score.clone()
    forbidden=torch.zeros_like(score,dtype=torch.bool)
    total=0
    for _ in range(rounds):
        decoded=_decode(new,objects,spec,device)
        with torch.no_grad():
            probs=verifier(objects,margins,preds,decoded,new).sigmoid()
            valid=~torch.eye(len(decoded),dtype=torch.bool,device=device)
            reject=valid&(decoded>0)&(probs<threshold)
            # Never redemote a previously demoted label.
            new_reject=reject & ~forbidden.gather(-1,decoded[...,None]).squeeze(-1)
            if not bool(new_reject.any()):break
            forbidden.scatter_(-1,decoded[...,None],
                  forbidden.gather(-1,decoded[...,None]) | new_reject[...,None])
            # Large negative per-label score; alternatives retain their margins.
            # Used only after choosing a nonzero decoded relation.
            replacement=new.gather(-1,decoded[...,None]).squeeze(-1)
            new.scatter_(-1,decoded[...,None],
               torch.where(new_reject,replacement-40.,replacement)[...,None])
            total+=int(new_reject.sum())
    final=_decode(new,objects,spec,device)
    return final,total


def _infer(rec,experts,spec,device,bundle,mode,typed=None,beta=1.5,cut=.5,
           veto=.50,rounds=2):
    require(mode in MODES,'UNKNOWN_MODE')
    for name in ('router','verifier'):
        require(not bundle[name].training,name.upper()+'_TRAINING_AT_INFERENCE')
    if bundle['specialist'] is not None:
        require(not bundle['specialist'].training,'SPECIALIST_TRAINING_AT_INFERENCE')
    if typed is not None:require(not typed.training,'PREDECODER_TRAINING_AT_INFERENCE')
    objects=rec['item']['objects']
    with torch.no_grad():
        score,margins,pred,swaps,sswaps=_base_scores(rec,experts,device,bundle)
        if mode in ('predecode_prior','prior_redecode'):
            require(typed is not None,'MISSING_TRAINED_PREDECODER')
            score=_pre_scores(score,typed(objects,margins,pred,score),beta,cut)
        pre=_decode(score,objects,spec,device)
        if mode=='frozen_0100':
            if bundle['selected_variant'] in ('assembly_only','router_assembly',
                          'specialist_assembly','all_three'):
                prob=bundle['verifier'](objects,margins,pred,pre,score).sigmoid()
                valid=~torch.eye(len(pre),dtype=torch.bool,device=device)
                removed=valid&(pre>0)&(prob<bundle['v_threshold'])
                final=pre.masked_fill(removed,0)
                rejected=int(removed.sum())
            else:
                final=pre;rejected=0
        elif mode in ('redecode_veto','prior_redecode'):
            final,rejected=_redecode_veto(score,objects,spec,bundle['verifier'],
                                          margins,pred,veto,rounds,device)
        else:
            final=pre;rejected=0
    require(tuple(final.shape)==(len(objects),len(objects)),'OUTPUT_SHAPE_DRIFT')
    return final.cpu(),{'router_swaps':swaps,'specialist_overrides':sswaps,
                        'rejected_proposals':rejected,
                        'changed_from_first_decode':int((final!=pre).sum()),
                        'added_after_reconstruction':int(((final>0)&(pre==0)).sum()),
                        'retargeted_after_reconstruction':int(((final>0)&(pre>0)&(final!=pre)).sum())}


def _typed_loss(typed,rec,experts,device,bundles):
    gold,valid=b._gold_mask(rec,device)
    idx=torch.arange(b.R,device=device)
    truth=(gold[...,None]==(idx+1)).float()
    losses=[]
    # Both the original 0099 route and the 0100 selected route are seen in TRAIN;
    # the model may learn to repair absent edges and to discriminate hard negatives.
    for bundle in bundles:
        with torch.no_grad():
            score,margins,pred,_,_=_base_scores(rec,experts,device,bundle)
        logits=typed(rec['item']['objects'],margins,pred,score)
        hard_wrong=(gold==0)&(score.argmax(-1)>0)
        # Positive edges get extra weight; teacher-forced labels never forwarded.
        weight=valid.float()*(1.+5.*(gold>0).float()+2.*hard_wrong.float())
        if rec['item']['row']['family']=='encounter':weight*=2.
        matrix=F.binary_cross_entropy_with_logits(logits,truth,reduction='none')
        # Greater supervision on false positives and hard-negative relation types.
        losses.append((matrix*weight[...,None]).sum()/
                      (weight.sum()*b.R).clamp_min(1.))
    return torch.stack(losses).mean()


def _train(records,experts,device,bundle,frozen_bundle,*,steps,lr,seed):
    torch.manual_seed(seed)
    typed=TypedPredecoder().to(device)
    optim=torch.optim.AdamW(typed.parameters(),lr=lr,weight_decay=.0001)
    randomizer=random.Random(seed)
    require(records and steps>0,'EMPTY_TRAINING_OR_STEPS')
    for step in range(1,steps+1):
        batch=randomizer.sample(records,min(4,len(records)))
        optim.zero_grad(set_to_none=True)
        loss=torch.stack([_typed_loss(typed,r,experts,device,
                           [bundle,frozen_bundle]) for r in batch]).mean()
        require(bool(torch.isfinite(loss)),'NONFINITE_TYPED_LOSS')
        loss.backward();torch.nn.utils.clip_grad_norm_(typed.parameters(),1.)
        optim.step()
        if step in (1,steps) or step%100==0:
            emit('TRAIN',{'step':step,'steps':steps,'loss':round(float(loss.detach()),6)})
    typed.eval()
    return typed


def _evaluate(records,experts,spec,device,bundle,mode,typed=None,**params):
    counters=Counter()
    by_family=defaultdict(Counter)
    hist=defaultdict(Counter)
    by_rel=defaultdict(Counter)
    case_diagnostics=[]
    for rec in records:
        result,metadata=_infer(rec,experts,spec,device,bundle,mode,typed,**params)
        actual=m._count(rec,result)
        gold=rec['item']['targets'].cpu()
        valid=~torch.eye(len(gold),dtype=torch.bool)
        family=rec['item']['row']['family']
        reference,_=_infer(rec,experts,spec,device,bundle,'frozen_0100') if mode!='frozen_0100' else (result,{})
        changed=(result!=reference)&valid
        change_repaired=int((changed&(result==gold)).sum())
        change_harmed=int((changed&(reference==gold)&(result!=gold)).sum())
        counts=Counter(actual)
        counts.update(repaired_vs_0100=change_repaired,harmed_vs_0100=change_harmed,
                      **metadata)
        counters.update(counts);by_family[family].update(counts)
        hist[family][f"fp{min(actual['fp'],10)}_fn{min(actual['fn'],10)}"]+=1
        for k in range(1,b.R+1):
            name=b.BIND_CLASSES[k]
            mask=valid&(gold==k)
            by_rel[name]['gold']+=int(mask.sum())
            by_rel[name]['missed']+=int((mask&(result!=gold)).sum())
            by_rel[name]['true']+=int((mask&(result==gold)).sum())
            by_rel[name]['spurious']+=int((valid&(result==k)&(gold!=k)).sum())
        case_diagnostics.append({'family':family,'fp':actual['fp'],'fn':actual['fn'],
           'exact':actual['exact'],'added':metadata['added_after_reconstruction'],
           'retargeted':metadata['retargeted_after_reconstruction']})
    return {'overall':m._metric(counters),
        'by_family':{k:m._metric(v) for k,v in by_family.items()},
        'repairs_vs_0100':counters['repaired_vs_0100'],
        'harms_vs_0100':counters['harmed_vs_0100'],
        'rejected_proposals':counters['rejected_proposals'],
        'added_after_reconstruction':counters['added_after_reconstruction'],
        'retargeted_after_reconstruction':counters['retargeted_after_reconstruction'],
        'by_relation':dict(by_rel),
        'encounter_case_error_histogram':dict(hist.get('encounter',{})),
        'encounter_cases_with_no_false_positives':sum(v['fp']==0 for v in case_diagnostics if v['family']=='encounter'),
        'encounter_cases_with_no_missing_edges':sum(v['fn']==0 for v in case_diagnostics if v['family']=='encounter')}


def _candidate_grid():
    yield ('frozen_0100',{})
    for veto in VETO_THRESHOLDS:
        for rounds in ITERATIONS:
            yield ('redecode_veto',{'veto':veto,'rounds':rounds})
    for beta in PRIOR_BETAS:
        for cut in PRIOR_CUTS:
            yield ('predecode_prior',{'beta':beta,'cut':cut})
            # A few pre-declared joint models, not an unconstrained search.
            for veto in (.50,.65):
                yield ('prior_redecode',{'beta':beta,'cut':cut,'veto':veto,'rounds':2})


def _rank(stat,baseline):
    enc=stat['by_family'].get('encounter',{'exact':0,'edge_f1':0.})
    alloc=stat['by_family'].get('allocation',{'edge_f1':0.})
    # Reject regressions on allocation and overall edge F1 before ranking.
    admissible=(alloc['edge_f1']>=baseline['by_family'].get('allocation',{}).get('edge_f1',0.)-.01
       and stat['overall']['edge_f1']>=baseline['overall']['edge_f1']-.003)
    return (int(admissible),enc['exact'] if admissible else -1,
      stat['overall']['exact'] if admissible else -1,
      enc['edge_f1'] if admissible else -1.,
      stat['overall']['edge_f1'] if admissible else -1.,
      -stat['harms_vs_0100'])


def _select(val,experts,spec,device,bundle,typed):
    baseline=_evaluate(val,experts,spec,device,bundle,'frozen_0100')
    rows=[]
    for mode,params in _candidate_grid():
        stat=_evaluate(val,experts,spec,device,bundle,mode,typed,**params)
        rank=_rank(stat,baseline)
        rows.append({'mode':mode,'params':params,'rank':list(rank),
                     'encounter_exact':stat['by_family']['encounter']['exact'],
                     'encounter_f1':stat['by_family']['encounter']['edge_f1'],
                     'allocation_f1':stat['by_family']['allocation']['edge_f1'],
                     'overall_exact':stat['overall']['exact'],
                     'overall_f1':stat['overall']['edge_f1'],
                     'recon_added':stat['added_after_reconstruction'],
                     'recon_retargeted':stat['retargeted_after_reconstruction']})
    winner=max(enumerate(rows),key=lambda x:(tuple(x[1]['rank']),-x[0]))[1]
    return winner,rows,baseline


def _sum(rows,key,family=None):
    ct=Counter()
    for row in rows:
        source=row[key]['overall'] if family is None else row[key]['by_family'][family]
        for f in ('tp','fp','fn','cases','exact','g1_exact'):
            ct[f]+=source[f]
    return m._metric(ct)


def _save(folder,typed,experts,source_hash,metadata):
    folder.mkdir(parents=True,exist_ok=True)
    weights=folder/'typed-predecoder.safetensors'
    save_file({k:v.detach().contiguous().cpu() for k,v in typed.state_dict().items()},str(weights))
    manifest={'schema':SCHEMA,'research_only':True,'production_promoted':False,
         'dev_used':False,'confirmation_used':False,'g3_unlocked':False,
         '0100_source_report_sha256':source_hash,
         'expert_order':list(experts),
         'expert_hashes':{key:entry['sha256'] for key,entry in experts.items()},
         'typed_predecoder':{'path':str(weights),'sha256':old.digest(weights)},
         'configuration':metadata}
    old.write(folder/'manifest.json',manifest)
    return manifest


def load_candidate(folder,experts,device,source_hash):
    manifest=old.read(Path(folder)/'manifest.json')
    require(manifest['schema']==SCHEMA and manifest['research_only'] and
            manifest['production_promoted'] is False and
            manifest['confirmation_used'] is False and
            manifest['g3_unlocked'] is False,'CANDIDATE_SECURITY_DRIFT')
    require(manifest['0100_source_report_sha256']==source_hash,'0100_LINEAGE_DRIFT')
    require(manifest['expert_order']==list(experts),'EXPERT_ORDER_DRIFT')
    require(all(manifest['expert_hashes'][k]==v['sha256'] and
            old.digest(Path(v['path']))==v['sha256'] for k,v in experts.items()),
            'FROZEN_EXPERT_MUTATED')
    entry=manifest['typed_predecoder']
    require(old.digest(entry['path'])==entry['sha256'],'TYPED_CHECKPOINT_MUTATED')
    model=TypedPredecoder().to(device)
    model.load_state_dict(load_file(entry['path'],device='cpu'),strict=True)
    model.eval()
    return model,manifest


def audit(root=old.ROOT_DEFAULT,output=None):
    _,experts,r99,r100,reportfile=_source(root,output)
    for i,row in enumerate(r100['folds']):
        require(row['fold']==i,'0100_FOLD_ORDER_DRIFT')
        bundle=_bundle_from_0100(i,row,r99,experts,'cpu')
        require(bundle['selected_variant']==row['selected_variant'],'0100_ORDER_DRIFT')
    emit('AUDIT',{'ready':True,'0100_accepted':False,'source_sha256':old.digest(reportfile),
        'source_folds':3,'expert_count':len(experts),'policies':list(MODES),
        'selected_on':'FIT_INNER_VALIDATION_ONLY',
        'new_trainable_modules':['typed_predecoder'],
        'frozen_0100_control':True,'production_promotion':False,
        'dev_confirmation_used':False})


def run(root=old.ROOT_DEFAULT,output=None,device='cuda',seed=SEED,steps=350,
        lr=.002,force=False):
    require(device=='cpu' or (device.startswith('cuda') and torch.cuda.is_available()),
            'CUDA_UNAVAILABLE')
    require(25<=steps<=3000 and 0<lr<.1,'TRAINING_PARAMS_OUT_OF_RANGE')
    source,experts,r99,r100,reportfile=_source(root,output)
    destination=source[8]/'graph-reconstruction-0101'
    target=destination/'result.json'
    require(force or not target.exists(),'REPORT_EXISTS_REFUSE_REPLACE_UNLESS_FORCE')
    emit('START',{'folds':3,'device':device,'new_typed_predecoder_steps':steps,
          'reference':'FROZEN_G2_0100_SELECTED_FOLD_CANDIDATES',
          'methods':list(MODES),'gold_free_inference':True,
          'outer_test_sealed_until_inner_validation_selection':True,
          'reused_fit_not_independent_certification':True})
    records,spec=b.parent._records(source,experts,device)
    rows=[]
    for fold,(fi,(train,val,test),counts) in enumerate(tr._split_by_base(records,3,seed)):
        require(fold==fi and r100['folds'][fold]['base_counts']==counts,
                '0100_BASE_SPLIT_CHANGED')
        record=r100['folds'][fold]
        bundle=_bundle_from_0100(fold,record,r99,experts,device)
        frozen,policy=b._load_fold(fold,r99,experts,device,counts)
        base_bundle={**bundle,'router':frozen,'specialist':None,
                     'selected_variant':'frozen_0099'}
        typed=_train(train,experts,device,bundle,base_bundle,steps=steps,lr=lr,
                     seed=seed+fold*701)
        selected,table,inner_control=_select(val,experts,spec,device,bundle,typed)
        # Do not open outer test examples until the inner validation choice is sealed.
        control=_evaluate(test,experts,spec,device,bundle,'frozen_0100')
        prior=record['outer_test']['overall']
        require(all(control['overall'][k]==prior[k] for k in ('tp','fp','fn','exact')),
                '0100_CONTROL_REPRODUCTION_FAILED')
        candidate=_evaluate(test,experts,spec,device,bundle,selected['mode'],typed,
                             **selected['params'])
        path=destination/('fold-'+str(fold))
        _save(path,typed,experts,old.digest(reportfile),
              {'mode':selected['mode'],'params':selected['params'],
               'base_counts':counts,'0100_source_manifest':bundle['manifest_path']})
        reloaded,_=load_candidate(path,experts,device,old.digest(reportfile))
        first,_=_infer(test[0],experts,spec,device,bundle,selected['mode'],typed,
                       **selected['params'])
        second,_=_infer(test[0],experts,spec,device,bundle,selected['mode'],reloaded,
                        **selected['params'])
        require(torch.equal(first,second),'CHECKPOINT_RELOAD_PREDICTION_DRIFT')
        rows.append({'fold':fold,'base_counts':counts,
           'selection_on':'INNER_VALIDATION_ONLY','selected':selected,
           '0100_control':control,'inner_control':inner_control,
           'outer_test':candidate,'inner_bakeoff':table,
           'manifest':str(path/'manifest.json')})
        emit('FOLD',{'fold':fold,'mode':selected['mode'],'params':selected['params'],
          'exact_encounter':[control['by_family']['encounter']['exact'],
                             candidate['by_family']['encounter']['exact']],
          'f1_encounter':[control['by_family']['encounter']['edge_f1'],
                          candidate['by_family']['encounter']['edge_f1']],
          'overall_exact':[control['overall']['exact'],candidate['overall']['exact']],
          'reconstructed_added':candidate['added_after_reconstruction'],
          'reconstructed_retargeted':candidate['retargeted_after_reconstruction']})
    control=_sum(rows,'0100_control');new=_sum(rows,'outer_test')
    byfam={f:{'control':_sum(rows,'0100_control',f),
               'candidate':_sum(rows,'outer_test',f)} for f in ('encounter','allocation')}
    gates={
      'encounter_exact_progress':byfam['encounter']['candidate']['exact']>
          byfam['encounter']['control']['exact'],
      'encounter_f1_not_worse':byfam['encounter']['candidate']['edge_f1']>=
          byfam['encounter']['control']['edge_f1'],
      'allocation_f1_within_1pp':byfam['allocation']['candidate']['edge_f1']>=
          byfam['allocation']['control']['edge_f1']-.01,
      'overall_f1_not_worse':new['edge_f1']>=control['edge_f1'],
      'overall_exact_not_worse':new['exact']>=control['exact']}
    accepted=all(gates.values())
    majority=Counter(r['selected']['mode'] for r in rows)
    chosen=max(MODES,key=lambda k:(majority[k],-MODES.index(k)))
    # Select one already-validated configuration; never synthesize a new
    # threshold combination from medians of unrelated fold winners.
    chosen_record=max((r for r in rows if r['selected']['mode']==chosen),
                      key=lambda r:tuple(r['selected']['rank']))
    params=dict(chosen_record['selected']['params'])
    # Final full-fit model: the reused-fold scores NEVER certify this checkpoint.
    final_manifest=Path(r100['full_fit_manifest'])
    final_models,_=b.load_candidate(final_manifest.parent,experts,device,
                                    r99['candidate']['sha256'])
    frozen_router,policy=b.parent.load_frozen_adapter(
                    Path(r99['candidate']['path']),experts,device)
    chosen_variant=r100['selected_recipe_from_inner_val_majority']
    route,specialist,verifier=b._candidate_args(chosen_variant,frozen_router,
        final_models['router'],final_models['specialist'],final_models['verifier'])
    full_bundle={'router':route,'specialist':specialist,
       'verifier':final_models['verifier'],'policy':policy,
       's_threshold':old.read(final_manifest)['configuration']['specialist_threshold'],
       'v_threshold':old.read(final_manifest)['configuration']['veto_threshold'],
       'selected_variant':chosen_variant}
    full_base={**full_bundle,'router':frozen_router,'specialist':None,
               'selected_variant':'frozen_0099'}
    full_typed=_train(records,experts,device,full_bundle,full_base,steps=steps,
                      lr=lr,seed=seed+9101)
    final_dir=destination/'full-fit-research-only'
    _save(final_dir,full_typed,experts,old.digest(reportfile),
          {'mode':chosen,'params':params,'source_0100':str(final_manifest),
           'full_fit_checkpoint_not_independently_validated':True})
    reloaded,_=load_candidate(final_dir,experts,device,old.digest(reportfile))
    a,_=_infer(records[0],experts,spec,device,full_bundle,chosen,full_typed,**params)
    c,_=_infer(records[0],experts,spec,device,full_bundle,chosen,reloaded,**params)
    require(torch.equal(a,c),'FINAL_FULL_FIT_RELOAD_DRIFT')
    report={'schema':SCHEMA,'accepted_on_reused_fit_cv':accepted,'gates':gates,
       'control_graph':control,'new_graph':new,'by_family':byfam,
       'folds':rows,'source_0100_sha256':old.digest(reportfile),
       'final_recipe_from_inner_validation_majority':{'mode':chosen,'params':params},
       'full_fit_manifest':str(final_dir/'manifest.json'),
       'full_fit_checkpoint_not_independently_certified':True,
       'research_only':True,'production_promoted':False,'g3_unlocked':False,
       'dev_used':False,'confirmation_used':False,
       'reused_fit_not_independent_certification':True}
    destination.mkdir(parents=True,exist_ok=True)
    old.write(target,report)
    emit('CROSS_VALIDATED',{'accepted':accepted,'gates':gates,'control':control,
        'candidate':new,'by_family':byfam,'full_fit_recipe':chosen})
    emit('SAVED',{'path':str(target),'sha256':old.digest(target),
        'full_fit_manifest':str(final_dir/'manifest.json'),'production_promoted':False})
    return report


def selftest():
    torch.manual_seed(10101)
    from tools.nanojev_gamer_v1_graph_head import SPAN_KINDS
    from tools.nanojev_gamer_v1_g2_posttrain_graph_bakeoff_0088 import format_edge
    n=6
    kinds=(SPAN_KINDS[0],SPAN_KINDS[1],SPAN_KINDS[0],SPAN_KINDS[1],SPAN_KINDS[0],SPAN_KINDS[1])
    objects=[[i*3,i*3+2,kinds[i]] for i in range(n)]
    margins=torch.randn(b.E,n,n,b.R)
    predictions=torch.randint(0,b.R+1,(b.E,n,n))
    gold=torch.zeros(n,n,dtype=torch.long)
    gold[0,1]=1;gold[2,3]=2;gold[4,5]=min(3,b.R)
    links={format_edge(objects,i,j,int(gold[i,j]))
           for i in range(n) for j in range(n) if int(gold[i,j])>0}
    experts={b.m.ANCHOR:{'sha256':'0','path':''}}
    for i in range(1,b.E):experts['synth_'+str(i)]={'sha256':str(i),'path':''}
    record={'item':{'objects':objects,'targets':gold,'full_gold':links,
           'row':{'family':'encounter','base_id':'synthetic-1'},'g1_exact':True},
           'expert_margins':margins,
           'predictions':{key:predictions[i] for i,key in enumerate(experts)}}
    router=b.m.CompetenceRouter(b.E).eval()
    verifier=b.EdgeVerifier().eval()
    config={'router':router,'specialist':None,'verifier':verifier,
            'policy':'pin_ungated','v_threshold':.50,'s_threshold':.70,
            'selected_variant':'router_assembly'}
    spec={'method':'joint','threshold':0.,'out_caps':{},'in_caps':{}}
    typed=TypedPredecoder()
    los=_typed_loss(typed,record,experts,'cpu',[config])
    los.backward()
    require(bool(torch.isfinite(los)) and
            all(p.grad is not None for p in typed.parameters()),'TYPED_GRAD_MISSING')
    typed.eval()
    no_gold={'item':{'objects':objects},'expert_margins':margins,
             'predictions':record['predictions']}
    for mode in MODES:
        result,meta=_infer(no_gold,experts,spec,'cpu',config,mode,typed)
        require(result.shape==(n,n) and not bool(result.diag().any()),
                'GOLD_FREE_INFERENCE_FAILED_'+mode)
    r1,_=_infer(no_gold,experts,spec,'cpu',config,'prior_redecode',typed)
    original,_=_infer(record,experts,spec,'cpu',config,'prior_redecode',typed)
    require(torch.equal(r1,original),'INFERENCE_GOLD_LEAK')
    # The real fold contains both families; include an allocation fixture too.
    allocation={**record,'item':{**record['item'],'row':
                 {'family':'allocation','base_id':'synthetic-2'}}}
    rank,table,base=_select([record,allocation],experts,spec,'cpu',config,typed)
    require(len(table)==len(list(_candidate_grid())) and rank['mode'] in MODES,
            'SELECTION_POLICY_DRIFT')
    trained=_train([record,allocation],experts,'cpu',config,config,
                   steps=2,lr=.001,seed=10101)
    stats=_evaluate([record,allocation],experts,spec,'cpu',config,
                    rank['mode'],trained,**rank['params'])
    require(stats['overall']['cases']==2,'TRAIN_EVALUATE_PIPELINE_DRIFT')
    # Reconstruction must reconsider alternatives rather than only erase edges.
    # Test an artificially high confidence-veto that demotes a chosen label.
    score,ms,ps,_,_=_base_scores(record,experts,'cpu',config)
    prior=torch.zeros_like(score)
    prior[...,1:]=1.
    _decode(prior,objects,spec,'cpu')
    # Causal reconstruction fixture: a top-scoring edge consumes an out-cap;
    # when rejected, a second lower-scoring edge must occupy that capacity.
    same_kind=[[i*3,i*3+2,SPAN_KINDS[0]] for i in range(3)]
    scores=torch.zeros(3,3,b.R+1)
    scores[0,1,1]=6.;scores[0,2,1]=5.
    capkey='|'.join((same_kind[0][2],same_kind[1][2],b.BIND_CLASSES[1]))
    capped={'method':'joint','threshold':0.,'out_caps':{capkey:1},'in_caps':{}}
    first=_decode(scores,same_kind,capped,'cpu')
    require(int(first[0,1])==1 and int(first[0,2])==0,
            'RECONSTRUCTION_FIXTURE_NOT_CAPACITY_LIMITED')
    class OneEdgeRejector(nn.Module):
        def forward(self,objects,margins,predictions,selected,scores):
            values=torch.full_like(selected,-10.,dtype=torch.float)
            values[0,2]=10.
            return values
    replacement_graph,rejections=_redecode_veto(scores,same_kind,capped,
             OneEdgeRejector().eval(),torch.zeros(b.E,3,3,b.R),
             torch.zeros(b.E,3,3,dtype=torch.long),.50,2,'cpu')
    require(rejections>=1 and int(replacement_graph[0,1])==0 and
            int(replacement_graph[0,2])==1,'REDECODE_FAILED_TO_REPLACE_EDGE')
    # Independently verify that a typed prior can propose a missing edge.
    blank=torch.zeros_like(scores)
    typed_evidence=torch.full((3,3,b.R),-8.)
    typed_evidence[1,0,0]=8.
    introduced=_decode(_pre_scores(blank,typed_evidence,1.5,.5),
                       same_kind,capped,'cpu')
    require(int(introduced[1,0])==1,'PREDECODE_FAILED_TO_ADD_EDGE')
    with tempfile.TemporaryDirectory(prefix='g2_0101_') as tmp:
        folder=Path(tmp)
        for i,entry in enumerate(experts.values()):
            p=folder/('ex'+str(i)+'.bin');p.write_bytes(str(i).encode());
            entry['path']=str(p);entry['sha256']=old.digest(p)
        _save(folder/'ckpt',typed,experts,'mock-lineage',{'mode':'prior_redecode'})
        restored,_=load_candidate(folder/'ckpt',experts,'cpu','mock-lineage')
        expected,_=_infer(no_gold,experts,spec,'cpu',config,'prior_redecode',typed)
        observed,_=_infer(no_gold,experts,spec,'cpu',config,'prior_redecode',restored)
        require(torch.equal(expected,observed),'TYPED_RELOAD_DRIFT')
        p=Path(next(iter(experts.values()))['path']);p.write_bytes(b'altered')
        try:load_candidate(folder/'ckpt',experts,'cpu','mock-lineage')
        except ValueError as error:
            require('FROZEN_EXPERT_MUTATED' in str(error),'TAMPER_ERROR_MISLABELED')
        else:raise ValueError(TAG+'_FROZEN_TAMPER_ACCEPTED')
    emit('SELFTEST',{'typed_predecoder_trainable':True,'gold_free_inference':True,
          'structured_decoder_used':True,'redecode_after_veto':True,
          'all_modes_tested':list(MODES),'checkpoint_roundtrip':True,
          'expert_mutation_rejected':True,'production_unchanged':True})


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',nargs='?',default='run',choices=('audit','selftest','run'))
    parser.add_argument('--root',default=old.ROOT_DEFAULT)
    parser.add_argument('--output')
    parser.add_argument('--device',default='cuda')
    parser.add_argument('--seed',type=int,default=SEED)
    parser.add_argument('--steps',type=int,default=350)
    parser.add_argument('--lr',type=float,default=.002)
    parser.add_argument('--force',action='store_true')
    args=parser.parse_args(argv)
    if args.command=='audit':return audit(root=args.root,output=args.output)
    if args.command=='selftest':return selftest()
    return run(root=args.root,output=args.output,device=args.device,
               seed=args.seed,steps=args.steps,lr=args.lr,force=args.force)

if __name__=='__main__':main()
