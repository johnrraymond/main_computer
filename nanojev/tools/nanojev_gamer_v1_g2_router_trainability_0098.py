"""G2-0098 isolated router trainability bakeoff; no graph fusion or G3 promotion.

The test asks whether G2 expert-correctness can be learned from inference-available
features. Historical experts and their predictions remain frozen. All evaluated
examples come from the 0098 FIT partition. Fit base graphs, not variants, are
assigned to outer held-out folds. Inner validation determines checkpoint choice.
The previously reused development and confirmation cohorts are not read.

    python -u -m tools.nanojev_gamer_v1_g2_router_trainability_0098 audit
    python -u -m tools.nanojev_gamer_v1_g2_router_trainability_0098 selftest
    python -u -m tools.nanojev_gamer_v1_g2_router_trainability_0098 run --device cuda
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path
import json
import random
import statistics

import torch
from torch.nn import functional as F
from safetensors.torch import load_file

from tools import nanojev_gamer_v1_g2_competence_routing_0098 as m
from tools import nanojev_gamer_v1_g2_router_forensics_0098 as forensics
from tools import nanojev_gamer_v1_g2_routing_diagnosis_0098 as diagnosis
from tools import nanojev_gamer_v1_g2_fuzz_and_correct_0095 as old
from tools import nanojev_gamer_v1_g2_hard_negative_learning_0087 as hard
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools import nanojev_gamer_v1_g1_native_composite_0082 as g1native
from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES

TAG='G2_0098_TRAINABILITY'
SCHEMA='nanojev.g2.router-trainability-0098.v1'
REL_REFERS_TO=BIND_CLASSES.index('refers_to')


def emit(kind, data):
    print(TAG+'_'+kind+' '+json.dumps(data,sort_keys=True,allow_nan=False),flush=True)


def check(cond, reason):
    if not cond:
        raise ValueError(TAG+'_'+reason)


def _record_tensors(rec, experts, device):
    margins,pred=m._predict_inputs(rec,experts,device)
    gold=rec['item']['targets'].long().to(device)
    n=gold.shape[0]
    valid=~torch.eye(n,dtype=torch.bool,device=device)
    relevant=valid&((gold>0)|(pred>0).any(0))
    correct=(pred==gold[None])
    recoverable=relevant&(pred[0]!=gold)&correct.any(0)
    protect=relevant&(pred[0]==gold)
    ref=relevant&(gold==REL_REFERS_TO)&recoverable
    return margins,pred,gold,relevant,correct,recoverable,protect,ref


def _objective(router,rec,experts,device,*,ref_weight=1.,shuffled=False,
               generator=None):
    margins,pred,gold,relevant,correct,recoverable,protect,ref=_record_tensors(
        rec,experts,device)
    n=len(gold)
    if not n or not bool(relevant.any()):
        return None
    _,logits,_=router(rec['item']['objects'],margins,pred)
    if shuffled:
        # Negative control: randomize gold assignments among OFF-DIAGONAL
        # positions within this example. Keep the label histogram unchanged,
        # but sever the relationship with candidate features and predictions.
        valid=(~torch.eye(n,dtype=torch.bool,device=device))
        shuffled_gold=gold.clone()
        positions=valid.nonzero(as_tuple=False)
        order=torch.randperm(len(positions),generator=generator).to(device)
        shuffled_gold[positions[:,0],positions[:,1]]=gold[
            positions[order,0],positions[order,1]]
        gold=shuffled_gold
        relevant=valid&((gold>0)|(pred>0).any(0))
        correct=(pred==gold[None])
        recoverable=relevant&(pred[0]!=gold)&correct.any(0)
        protect=relevant&(pred[0]==gold)
    correct_float=correct.float()
    weights=relevant.float()*(1.+2.*(gold>0).float())
    if ref_weight!=1.:
        weights=weights*(1.+(ref_weight-1.)*(gold==REL_REFERS_TO).float())
    denom=weights.sum().clamp_min(1.)
    bce=(F.binary_cross_entropy_with_logits(logits,correct_float,
                reduction='none')*weights[None]).sum()/(denom*len(experts))
    # Multi-correct target: maximize the TOTAL probability assigned to correct
    # experts. Do not demand choosing an arbitrary canonical 'best' expert.
    probability=logits.softmax(0)
    correct_mass=(probability*correct_float).sum(0).clamp_min(1e-7)
    available=relevant&correct.any(0)
    # Balance preserving known-correct anchor decisions with rescuing anchor
    # errors. Without the rescue term, simply choosing anchor dominates loss.
    teacher_weight=(2.*recoverable.float()+protect.float())*weights
    teacher_weight=teacher_weight*available.float()
    teach=-(correct_mass.log()*teacher_weight).sum()/teacher_weight.sum().clamp_min(1.)
    return .25*bce+teach


def _empty_metrics():
    return Counter(recoverable=0,rescue=0,protect=0,harm=0,
                   relevant=0,chosen_correct=0,ref_recoverable=0,
                   ref_rescued=0,group_cases=0)


def _metrics(records,experts,device,router=None,prior=None):
    global_stats=_empty_metrics()
    by_family=defaultdict(_empty_metrics)
    by_base=defaultdict(_empty_metrics)
    by_relation=defaultdict(_empty_metrics)
    with torch.no_grad():
        for rec in records:
            margins,pred,gold,relevant,correct,recoverable,protect,ref=_record_tensors(
                rec,experts,device)
            n=len(gold)
            if not n:continue
            if router is not None:
                _,comp,_=router(rec['item']['objects'],margins,pred)
                winner=comp.argmax(0)
            elif prior is not None:
                winner=prior(rec, pred, device)
            else:
                winner=torch.zeros((n,n),device=device,dtype=torch.long)
            i,j=torch.meshgrid(torch.arange(n,device=device),
                   torch.arange(n,device=device),indexing='ij')
            chosen=pred[winner,i,j]
            success=relevant&(chosen==gold)
            count=Counter(oracle_reachable=int((relevant&correct.any(0)).sum()),
                recoverable=int(recoverable.sum()),
                rescue=int((recoverable&success).sum()),
                protect=int(protect.sum()),harm=int((protect&~success).sum()),
                relevant=int(relevant.sum()),
                chosen_correct=int(success.sum()),
                ref_recoverable=int(ref.sum()),
                ref_rescued=int((ref&success).sum()),group_cases=1)
            global_stats.update(count)
            by_family[rec['item']['row']['family']].update(count)
            by_base[(rec['item']['row']['family'],
                     rec['item']['row']['base_id'])].update(count)
            # The relation-specific score is over gold-positive recoverable
            # errors, including the critical refers_to case.
            for r in range(1,len(BIND_CLASSES)):
                mask=recoverable&(gold==r)
                if bool(mask.any()):
                    by_relation[BIND_CLASSES[r]].update(
                        recoverable=int(mask.sum()),
                        rescue=int((mask&success).sum()))
    return global_stats,by_family,by_base,by_relation


def _rates(counter):
    return {'rescue':counter['rescue'],'recoverable':counter['recoverable'],
       'rescue_rate':round(counter['rescue']/max(1,counter['recoverable']),5),
       'harm':counter['harm'],'protected':counter['protect'],
       'harm_rate':round(counter['harm']/max(1,counter['protect']),5),
       'refers_to_rescued':counter['ref_rescued'],
       'refers_to_recoverable':counter['ref_recoverable'],
       'refers_to_rate':round(counter['ref_rescued']/max(1,counter['ref_recoverable']),5),
       'chosen_correct':counter['chosen_correct'],'relevant':counter['relevant'],
       'oracle_reachable':counter['oracle_reachable'],
       'oracle_fraction_recovered':round(counter['chosen_correct']/max(1,counter['oracle_reachable']),5),
       'chosen_accuracy':round(counter['chosen_correct']/max(1,counter['relevant']),5),
       'cases':counter['group_cases']}


def _score(counter):
    # Equal value to covering available rescues and preserving correct answers.
    # Always-anchor gets exactly 0.5 whenever both denominators are nonzero.
    rescue=counter['rescue']/max(1,counter['recoverable'])
    preserve=1.-counter['harm']/max(1,counter['protect'])
    return .5*(rescue+preserve)


def _split_by_base(records,folds,seed):
    grouped=defaultdict(list)
    for rec in records:
        key=(rec['item']['row']['family'],rec['item']['row']['base_id'])
        grouped[key].append(rec)
    families=defaultdict(list)
    for family,base in grouped:families[family].append((family,base))
    check(len(families)>=2 and all(len(v)>=folds*2 for v in families.values()),
         'INSUFFICIENT_INDEPENDENT_BASES_FOR_CV')
    bins=[[] for _ in range(folds)]
    for idx,family in enumerate(sorted(families)):
        keys=sorted(families[family]);random.Random(seed+idx).shuffle(keys)
        for i,key in enumerate(keys):bins[i%folds].append(key)
    for fold in range(folds):
        test_keys=set(bins[fold])
        remaining=[key for idx,b in enumerate(bins) if idx!=fold for key in b]
        train_keys=[];val_keys=[]
        for idx,family in enumerate(sorted(families)):
            own=sorted(k for k in remaining if k[0]==family)
            random.Random(seed+1000+fold*17+idx).shuffle(own)
            nval=max(2,round(.20*len(own)))
            val_keys.extend(own[:nval]);train_keys.extend(own[nval:])
        keysets=[set(train_keys),set(val_keys),test_keys]
        check(not any(keysets[i]&keysets[j] for i in range(3) for j in range(i+1,3)),
              'BASE_LEAKAGE')
        parts=[[rec for key in sorted(keys) for rec in grouped[key]]
               for keys in keysets]
        check(all(parts),'EMPTY_CV_PARTITION')
        yield fold,parts,{'train':len(keysets[0]),'val':len(keysets[1]),
                           'test':len(test_keys)}


def _train_router(train,val,experts,device,steps,seed,mode,lr,eval_every):
    torch.manual_seed(seed)
    model=m.CompetenceRouter(len(experts)).to(device)
    opt=torch.optim.AdamW(model.parameters(),lr=lr,weight_decay=0.0001)
    rng=random.Random(seed)
    generator=torch.Generator(device='cpu').manual_seed(seed+107)
    history=[]
    best=-1.
    state=None
    for step in range(1,steps+1):
        model.train(); opt.zero_grad(set_to_none=True)
        chosen=rng.sample(train,min(4,len(train)))
        losses=[_objective(model,rec,experts,device,
                ref_weight=8. if mode=='refers_to_weighted' else 1.,
                shuffled=mode=='shuffled_labels',generator=generator)
                for rec in chosen]
        losses=[loss for loss in losses if loss is not None]
        if not losses:continue
        loss=torch.stack(losses).mean()
        check(bool(torch.isfinite(loss)), 'NONFINITE_LOSS')
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(),1.)
        opt.step()
        if step==1 or step%eval_every==0 or step==steps:
            model.eval()
            train_stat,_,_,_=_metrics(train,experts,device,model)
            val_stat,_,_,_=_metrics(val,experts,device,model)
            value=_score(val_stat)
            row={'step':step,'loss':round(float(loss.detach()),6),
                 'fit_score':round(_score(train_stat),5),
                 'fit_rescue_rate':_rates(train_stat)['rescue_rate'],
                 'val_score':round(value,5),
                 'val_refers_to_rate':_rates(val_stat)['refers_to_rate']}
            history.append(row)
            if value>best:
                best=value
                state={k:v.detach().cpu().clone()
                       for k,v in model.state_dict().items()}
    check(state is not None,'NO_TRAINED_STATE')
    model.load_state_dict(state)
    model.eval()
    return model,history


def _prior_from_train(train, experts):
    # Supervised simple baseline: a single expert for each object-kind pair.
    # Target info is read only from the TRAINING split to construct the prior.
    counts=defaultdict(lambda:[0]*len(experts))
    for rec in train:
        objects=rec['item']['objects'];gold=rec['item']['targets']
        preds=torch.stack([rec['predictions'][k] for k in experts])
        for i in range(len(objects)):
            for j in range(len(objects)):
                if i==j:continue
                if gold[i,j]==0 and not bool((preds[:,i,j]>0).any()):continue
                key=(objects[i][2],objects[j][2])
                for k in range(len(experts)):
                    counts[key][k]+=int(preds[k,i,j]==gold[i,j])
    winners={key:max(range(len(experts)),
                     key=lambda k:(v[k],-k)) for key,v in counts.items()}
    def prior(rec,pred,device):
        n=len(rec['item']['objects'])
        obj=rec['item']['objects']
        return torch.tensor([[winners.get((obj[i][2],obj[j][2]),0)
                    for j in range(n)] for i in range(n)],device=device)
    return prior


def _synthetic():
    # Shape and label-loss sanity checks without backbone access.
    from tools.nanojev_gamer_v1_graph_head import SPAN_KINDS
    kinds=[SPAN_KINDS[0],SPAN_KINDS[1],SPAN_KINDS[0]]
    objects=[(i*5,i*5+2,kind) for i,kind in enumerate(kinds)]
    e=7;n=3
    gen=torch.Generator().manual_seed(981)
    margins=torch.randn(e,n,n,m.R,generator=gen)
    pred=torch.randint(0,m.R+1,(e,n,n),generator=gen)
    gold=torch.randint(0,m.R+1,(n,n),generator=gen)
    for i in range(n):gold[i,i]=0
    item={'objects':objects,'targets':gold,
          'row':{'family':'allocation','base_id':'synthetic-01'}}
    rec={'item':item,'expert_margins':margins,
         'predictions':{str(i):pred[i] for i in range(e)}}
    experts={str(i):{} for i in range(e)}
    router=m.CompetenceRouter(e)
    loss=_objective(router,rec,experts,'cpu')
    check(loss is not None and bool(torch.isfinite(loss)),'SYNTHETIC_LOSS')
    loss.backward()
    check(any(p.grad is not None and bool(torch.isfinite(p.grad).all())
              and bool((p.grad.abs()>0).any()) for p in router.parameters()),
          'SYNTHETIC_GRADIENT')
    metrics,_,_,_=_metrics([rec],experts,'cpu',router)
    check(metrics['relevant']>0,'SYNTHETIC_METRICS')
    return {'finite_loss':True,'nonzero_gradient':True,
            'selection_metrics':_rates(metrics)}


def audit(root=old.ROOT_DEFAULT,output=None):
    root,atlas_dir,commit,parent_dir,h95,h96,p97,previous,dest,provenance,report=(
        diagnosis._locate(root,output))
    experts=forensics._frozen_experts_in_training_order(previous,provenance,p97)
    check(next(iter(experts))==m.ANCHOR,'ANCHOR_ALIGNMENT')
    emit('AUDIT',{'ready':True,'expert_count':len(experts),
        'fit_cases':len(provenance['selection']['fit_case_ids']),
        'fit_bases':len(provenance['selection']['fit_bases']),
        'previous_dev_used_for_training':False,
        'previous_confirmation_used':False,
        'probes':['microset-memorization','base-disjoint cross-validation',
                  'expert-prior control','refers_to-weighted',
                  'shuffled-label negative control'],
        'expert_order_verified':True,'production_unchanged':True})


def run(root=old.ROOT_DEFAULT,output=None,device='cuda',folds=3,
        steps=500,eval_every=100,lr=0.002,seed=980189,
        negative_control=True):
    check(device=='cpu' or (device.startswith('cuda') and torch.cuda.is_available()),
          'DEVICE_UNAVAILABLE')
    check(2<=folds<=5 and 20<=steps<=3000 and eval_every>0 and
          0<lr<0.1,'INVALID_SETTINGS')
    root,atlas_dir,commit,parent_dir,h95,h96,p97,previous,dest,provenance,report=(
        diagnosis._locate(root,output))
    experts=forensics._frozen_experts_in_training_order(previous,provenance,p97)
    path=dest/'competence-router.safetensors'
    check(old.digest(path)==report['training']['router_sha256'], 'CHECKPOINT_HASH_DRIFT')
    target=dest/'router-trainability-0098'
    outfile=target/'base-disjoint-trainability.json'
    check(not outfile.exists(),'OUTPUT_ALREADY_EXISTS__USE_NEW_OUTPUT_LOCATION')
    selected=set(provenance['selection']['fit_case_ids'])
    check(bool(selected),'FIT_SPLIT_EMPTY')
    emit('START',{'fit_cases':len(selected),'folds':folds,
         'steps_per_arm':steps,'device':device,'expert_count':len(experts),
         'previous_dev_used':False,'previous_confirmation_used':False,
         'frozen_existing_weights':True})
    paths=m.atlas._roots(root)
    parent_record,_=hard._verify_trained(paths,paths['parent_0087'])
    network,tok=backbone.model_components(parent_record['model_id'],paths['adapter'],device)
    composite=g1native.load_composite(paths['composite'],device=device)
    head,_=cur._load_checkpoint(parent_dir,device=device)
    old._mutable(head)
    spec=commit['lineage']['decoder']
    rows=old._get_rows(atlas_dir,selected,split='discovery')
    items=old._capture(network,tok,composite,rows,device,must_exact=True)
    records=m._capture_experts(head,items,device,spec,experts,
                       '0098-router-trainability-fit',training=False)
    del items,network,composite,head
    historical=m.CompetenceRouter(len(experts)).to(device)
    historical.load_state_dict(load_file(str(path),device='cpu'),strict=True)
    historical.eval()
    # Explicit overfitting test: can this architecture learn a tiny set of
    # expert-correctness decisions when we remove fusion, decoding and fuzz?
    # This intentionally evaluates the SAME cases used in microset training.
    # It is a capacity/optimization probe, not generalization evidence.
    micro_bases=[]
    for family in sorted(set(rec['item']['row']['family'] for rec in records)):
        choices=sorted(set(rec['item']['row']['base_id'] for rec in records
                         if rec['item']['row']['family']==family))
        micro_bases.append((family,choices[0]))
    micro=[rec for rec in records if (rec['item']['row']['family'],
             rec['item']['row']['base_id']) in micro_bases]
    micro=micro[:8]
    torch.manual_seed(seed+999)
    micro_init=m.CompetenceRouter(len(experts)).to(device)
    before,_,_,_=_metrics(micro,experts,device,micro_init)
    micro_trained,micro_history=_train_router(micro,micro,experts,device,
               steps,seed+999,'competence_only',lr,eval_every)
    after,_,_,_=_metrics(micro,experts,device,micro_trained)
    micro_result={'train_cases':len(micro),'independent_bases':len(micro_bases),
        'before':_rates(before),'after':_rates(after),
        'best_training_score':round(max(row['fit_score'] for row in micro_history),5),
        'interpretation':'Same-case optimization/capacity only, not generalization'}
    emit('MICROSET_MEMORIZATION',micro_result)
    # Use only the previously designated FIT base groups; this is a new
    # scientific held-out split inside fit, not a development-score search.
    results=[]
    control_aggregate=defaultdict(Counter)
    heldout_by_base=defaultdict(dict)
    modes=['competence_only','refers_to_weighted']
    if negative_control:modes.append('shuffled_labels')
    for fold,(train,val,test),sizes in _split_by_base(records,folds,seed):
        baseline,_,baseline_bases,_=_metrics(test,experts,device)
        existing,_,existing_bases,_=_metrics(test,experts,device,historical)
        prior=_prior_from_train(train,experts)
        simple,_,prior_bases,_=_metrics(test,experts,device,prior=prior)
        for name,stats,bases in (('anchor',baseline,baseline_bases),
                    ('historical_router',existing,existing_bases),
                    ('kind_prior',simple,prior_bases)):
            control_aggregate[name].update(stats)
            for key,value in bases.items():
                heldout_by_base[name][key]=value
        emit('FOLD_CONTROL',{'fold':fold,'base_counts':sizes,
             'anchor':_rates(baseline),'historical_router':_rates(existing),
             'kind_prior':_rates(simple)})
        for idx,mode in enumerate(modes):
            model,history=_train_router(train,val,experts,device,steps,
                seed+fold*100+idx,mode,lr,eval_every)
            fit_stat,_,_,_=_metrics(train,experts,device,model)
            val_stat,_,_,_=_metrics(val,experts,device,model)
            test_stat,family,base,relations=_metrics(test,experts,device,model)
            row={'fold':fold,'mode':mode,'base_counts':sizes,
                 'fit':_rates(fit_stat),'validation':_rates(val_stat),
                 'test':_rates(test_stat),
                 'test_score':round(_score(test_stat),5),
                 'test_by_family':{k:_rates(v) for k,v in family.items()},
                 'test_gold_relation':{k:{'correctly_selected':v['rescue'],
                             'correctable':v['recoverable']}
                                     for k,v in relations.items()},
                 'best_step':max(history,key=lambda r:r['val_score'])['step'],
                 'learning_history':history}
            results.append(row)
            for key,value in base.items():
                heldout_by_base[mode][key]=value
            emit('FOLD_RESULT',{k:v for k,v in row.items() if k!='learning_history'})
    # Combine *held-out predictions*, not unweighted fold percentages.
    aggregate=defaultdict(Counter)
    for row in results:
        key=row['mode'];metric=row['test']
        aggregate[key].update({
            'rescue':metric['rescue'],'recoverable':metric['recoverable'],
            'harm':metric['harm'],'protect':metric['protected'],
            'ref_rescued':metric['refers_to_rescued'],
            'ref_recoverable':metric['refers_to_recoverable'],
            'chosen_correct':metric['chosen_correct'],'relevant':metric['relevant'],
            'group_cases':metric['cases'],
            'oracle_reachable':metric['oracle_reachable']})
    summary={name:_rates(count) for name,count in aggregate.items()}
    control_summary={name:_rates(count) for name,count in control_aggregate.items()}
    # Clustered bootstrap: sample independent base IDs with replacement.
    # All fuzz variants from one base travel together in each resample.
    bootstrap={}
    base_keys=sorted(heldout_by_base['kind_prior'])
    rand=random.Random(seed+54321)
    check(base_keys and all(set(heldout_by_base[k])==set(base_keys) for k in modes),
          'OUT_OF_FOLD_BASE_MISMATCH')
    for mode in modes:
        deltas=[]
        for _ in range(1000):
            sample=[rand.choice(base_keys) for _ in base_keys]
            model_stat=Counter();prior_stat=Counter()
            for key in sample:
                model_stat.update(heldout_by_base[mode][key])
                prior_stat.update(heldout_by_base['kind_prior'][key])
            deltas.append(_score(model_stat)-_score(prior_stat))
        deltas.sort()
        bootstrap[mode]={'difference_vs_kind_prior':round(
                    _score(aggregate[mode])-_score(control_aggregate['kind_prior']),5),
            'base_bootstrap_95pct_interval':[round(deltas[24],5),round(deltas[974],5)]}
    emit('CROSS_VALIDATED',{'folds':folds,'aggregate':summary,
       'controls':control_summary,'base_cluster_bootstrap':bootstrap,
       'diagnostic_rule':('Trainability requires a reproducible out-of-base '
          'selection advantage over the train-split kind prior and randomized '
          'target control, not merely lower training loss or decoded F1. '
          'Historical router trained on these fit examples and is NOT an '
          'independent comparator.'),
       'certification':False,'g3_unlocked':False})
    target.mkdir(exist_ok=True)
    old.write(outfile,{'schema':SCHEMA,'source_router_sha256':old.digest(path),
           'previous_dev_used':False,'confirmation_used':False,
           'microset':micro_result,'folds':results,'summary':summary,
           'controls':control_summary,'base_cluster_bootstrap':bootstrap,
           'report_not_certification':True,'production_promotion':False,
           'g3_unlocked':False})
    emit('SAVED',{'path':str(outfile),'sha256':old.digest(outfile)})
    return summary


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',nargs='?',default='run',choices=['audit','selftest','run'])
    p.add_argument('--root',default=old.ROOT_DEFAULT)
    p.add_argument('--output')
    p.add_argument('--device',default='cuda')
    p.add_argument('--folds',type=int,default=3)
    p.add_argument('--steps',type=int,default=500)
    p.add_argument('--eval-every',type=int,default=100)
    p.add_argument('--seed',type=int,default=980189)
    p.add_argument('--lr',type=float,default=.002)
    p.add_argument('--no-negative-control',action='store_true')
    a=p.parse_args(argv)
    if a.command=='selftest':emit('SELFTEST',_synthetic());return
    if a.command=='audit':return audit(root=a.root,output=a.output)
    return run(root=a.root,output=a.output,device=a.device,folds=a.folds,
       steps=a.steps,eval_every=a.eval_every,seed=a.seed,lr=a.lr,
       negative_control=not a.no_negative_control)


if __name__=='__main__':main()
