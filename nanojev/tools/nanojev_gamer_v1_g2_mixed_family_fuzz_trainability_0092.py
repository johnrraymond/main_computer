"""G2-0092: bounded mixed-family structural trainability with LIVE fuzz/causal diagnostics.

    python -m tools.nanojev_gamer_v1_g2_mixed_family_fuzz_trainability_0092 run --device cuda

Two frozen parent initializations. First memorize 2 encounter + 2 allocation
graphs, then 4+4. A separate, excluded fuzz battery is executed DURING
optimization, not only after it. Failed transformations trigger gradient and
activation diagnostics, with per-family heldout measurements. Experimental
checkpoints are NOT certifications and can never unlock G3.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random

import torch

from tools import nanojev_gamer_v1_g2_structural_learning_0089 as structural
from tools import nanojev_gamer_v1_g2_trainability_diagnostic_0090 as trainability
from tools import nanojev_gamer_v1_g2_frozen_generalization_0091 as frozen
from tools import nanojev_gamer_v1_g2_causal_activation_fuzz_0085 as fuzz
from tools import nanojev_gamer_v1_graph_corpus as corpus
from tools import nanojev_gamer_v1_graph_curriculum as cur

SCHEMA='nanojev.gamer.graph.g2.mixed-family-causal-fuzz-0092.v1'
ROOT_DEFAULT='../runtime/reports/gamer-v1'
SEED=920092
FAMILIES=('allocation','encounter')
STAGES=(4,8)
DEFAULT_STEPS=(650,1300)
EVAL_INTERVAL=25
FUZZ_INTERVAL=50
GRADIENT_INTERVAL=100
CONSISTENCY_WEIGHT=0.10
FUZZ_PAIRS=8
MAX_EVENTS=16


def require(ok,code):
    if not ok:raise ValueError('G2_0092_'+code)


def sha(path):return structural.sha(path)
def read(path):return structural.js(path)
def write(path,obj):return structural.write_json(path,obj)
def digest(row):return hashlib.sha256(row['prompt'].encode()).hexdigest()


def stage_steps(value):
    if value is None:return DEFAULT_STEPS
    pieces=str(value).split(',')
    require(len(pieces)==2 and all(x.strip().isdigit() for x in pieces),'STEP_FORMAT')
    numbers=tuple(int(x) for x in pieces)
    require(all(1<=x<=5000 for x in numbers),'STEP_RANGE')
    return numbers


def balanced_prefix(rows,per_family):
    """Deterministic family balance; do not silently choose all one family."""
    require(per_family in (2,4),'BAD_FAMILY_BUDGET')
    selected=[]
    for fam in FAMILIES:
        matching=[r for r in rows if r['row']['family']==fam]
        require(len(matching)>=per_family,'INSUFFICIENT_'+fam.upper())
        selected.extend(matching[:per_family])
    require(len({digest(r['row']) for r in selected})==len(selected),'DUPLICATE_TRAIN_CASE')
    return selected


def family_summary(metrics):
    by={}
    for family in FAMILIES:
        records=[r for r in metrics['records'] if r['family']==family]
        gold=sum(r['gold_edges'] for r in records)
        pred=sum(r['predicted_edges'] for r in records)
        tp=sum(r['true_positive'] for r in records)
        by[family]={'cases':len(records),'exact':sum(r['exact'] for r in records),
             'fp':sum(r['false_positive'] for r in records),
             'fn':sum(r['false_negative'] for r in records),
             'edge_f1':2*tp/max(gold+pred,1),
             'within_kind_inversions':sum(r['within_kind_inversions'] for r in records),
             'within_kind_groups':sum(r['within_kind_groups'] for r in records)}
    return by


def balanced_quality(metrics):
    groups=family_summary(metrics)
    rates=[groups[f]['exact']/max(groups[f]['cases'],1) for f in FAMILIES]
    errs=[groups[f]['fp']+groups[f]['fn'] for f in FAMILIES]
    return (min(rates),sum(groups[f]['exact'] for f in FAMILIES),
            -max(errs),-sum(errs),-metrics['mean_loss'])


def compare_family(current,previous,baseline):
    """No aggregate progress may conceal either family regression."""
    reasons=[]
    for fam in FAMILIES:
        c=current[fam];p=previous[fam];b=baseline[fam]
        if c['exact']<p['exact']:reasons.append(fam+':lost_exact')
        if c['fp']+c['fn']>p['fp']+p['fn']:reasons.append(fam+':more_edge_errors')
        if c['edge_f1'] < b['edge_f1']-0.03:
            reasons.append(fam+':below_initial_f1_by_3pp')
    return reasons


def _eligible_training(network,tok,composite,head,paths,spec,device):
    found={fam:[] for fam in FAMILIES};rejected=Counter();used=set()
    for row in cur.load_rows(paths['dataset']):
        if row['split']!='train':continue
        fam=row['family']
        if fam not in found or len(found[fam])>=4:continue
        h=digest(row)
        if h in used:continue
        used.add(h)
        item=structural._item_capture(network,tok,composite,row,device)
        full=structural.parent.previous._gold_edges(row['graph'])
        if not full or len(item['spans'])<2 or item['reachable']!=len(full):
            rejected[fam+':g1_unreachable']+=1;continue
        record=structural._surface(head,item,device)
        obj=record['g1_objects']
        gold=structural._gold_keys(record,item['targets'])
        require(gold==full,'TRAIN_AUTHOR_TARGET_MISALIGNMENT')
        oracle=structural._oracle_surface(record,item['targets'])
        if structural.previous.decode(oracle,spec)!=full:
            rejected[fam+':decoder_not_recoverable']+=1;continue
        item['objects']=obj
        found[fam].append(item)
        if all(len(items)>=4 for items in found.values()):break
    require(all(len(v)==4 for v in found.values()),'NOT_ENOUGH_FULLY_REACHABLE_BOTH_FAMILIES')
    rows=[item for fam in FAMILIES for item in found[fam]]
    return rows,dict(rejected)


def _heldout_items(network,tok,composite,head,paths,device):
    """Previously exposed original dev/challenge: diagnosis ONLY, never optimization."""
    found={(split,fam):[] for split in ('dev','challenge') for fam in FAMILIES}
    for row in cur.load_rows(paths['dataset']):
        key=(row['split'],row['family'])
        if key not in found or len(found[key])>=3:continue
        item=structural._item_capture(network,tok,composite,row,device)
        item['objects']=(structural._surface(head,item,device)['g1_objects']
                          if len(item['spans'])>=2 else [])
        item['full_gold_count']=len(structural.parent.previous._gold_edges(row['graph']))
        found[key].append(item)
        if all(len(v)>=3 for v in found.values()):break
    require(all(len(v)==3 for v in found.values()),'INCOMPLETE_HELDOUT_FAMILY_COVERAGE')
    result=[it for key in sorted(found) for it in found[key]]
    require(len({digest(x['row']) for x in result})==len(result),'DUPLICATE_HELDOUT')
    return result


def _diagnostic_fuzz(network,tok,composite,head,device,training_digests):
    """Author-annotated test-name perturbations, excluded from ALL updates."""
    pairs=fuzz.make_pairs(seed=SEED+500,pairs=FUZZ_PAIRS)
    result=[];used=set()
    for pair in pairs:
        sides={}
        for side in ('base','fuzz'):
            row=pair[side];key=digest(row)
            require(key not in training_digests and key not in used,'FUZZ_TRAIN_LEAKAGE')
            used.add(key)
            item=structural._item_capture(network,tok,composite,row,device)
            if len(item['spans'])>=2:
                item['objects']=structural._surface(head,item,device)['g1_objects']
            sides[side]=item
        result.append((pair['id'],pair['mode'],pair['family'],sides))
    require(len(result)==FUZZ_PAIRS,'FUZZ_COUNT_CHANGED')
    return result


def _training_fuzz(network,tok,composite,head,device,excluded):
    """Separate train-name counterfactuals used ONLY for consistency learning."""
    rng=random.Random(SEED+333);result=[]
    for family in FAMILIES:
        for mode in ('renaming','role_swap'):
            for attempt in range(20):
                base,changed=corpus._counterfactual_pair(rng,family,mode,
                                                        SEED*100+len(result)*100+attempt)
                if digest(base) in excluded or digest(changed) in excluded:continue
                a=structural._item_capture(network,tok,composite,base,device)
                b=structural._item_capture(network,tok,composite,changed,device)
                if len(a['spans'])<2 or len(b['spans'])<2:continue
                a['objects']=structural._surface(head,a,device)['g1_objects']
                b['objects']=structural._surface(head,b,device)['g1_objects']
                # Enforce exact G1 alignment so counterfactual supervision cannot
                # accidentally train G2 to match non-corresponding objects.
                exact=lambda it: {(k,(s,e)) for s,e,k in it['objects']}=={
                    (n['kind'],tuple(n['span'])) for n in it['row']['graph']['nodes']
                    if 'span' in n}
                if not exact(a) or not exact(b):continue
                require(base['graph']['edges']==changed['graph']['edges'],
                        'TRAIN_COUNTERFACTUAL_GOLD_DRIFT')
                result.append((mode,a,b))
                excluded.update((digest(base),digest(changed)))
                break
    return result


def fuzz_probe(head,fuzz_items,spec,device):
    """Diagnostic inference; author gold is NEVER supplied to head/decoder."""
    totals=Counter();details=[]
    for key,mode,family,sides in fuzz_items:
        decisions={};golds={};g1_exact={};edge_errors={}
        for side,item in sides.items():
            gold=structural.parent.previous._gold_edges(item['row']['graph'])
            golds[side]=frozen._normalized_edges(gold,item['row']['graph'])
            if len(item['spans'])<2:
                decisions[side]=set();g1_exact[side]=False
                edge_errors[side]=len(gold);continue
            logits=structural._forward(head,item,device)
            surface=structural._surface_from_logits(item['objects'],logits)
            predicted=structural.previous.decode(surface,spec)
            decisions[side]=frozen._normalized_edges(predicted,item['row']['graph'])
            g1_exact[side]={ (k,(s,e)) for s,e,k in item['objects']}=={
                (n['kind'],tuple(n['span'])) for n in item['row']['graph']['nodes']
                if 'span' in n}
            edge_errors[side]=len(decisions[side]^golds[side])
        correct_both=all(decisions[s]==golds[s] for s in ('base','fuzz'))
        delta_correct=(decisions['base']^decisions['fuzz'])==(
                       golds['base']^golds['fuzz'])
        gold_invariant=golds['base']==golds['fuzz']
        if mode in ('renaming','role_swap','added_distractor'):
            require(gold_invariant,'FUZZ_GOLD_INVARIANT_DRIFT')
        if mode=='action_removal':require(not gold_invariant,'FUZZ_REMOVAL_GOLD_UNCHANGED')
        row={'pair_id':key,'mode':mode,'family':family,
             'g1_exact_both':all(g1_exact.values()),'gold_invariant':gold_invariant,
             'predicted_invariant':decisions['base']==decisions['fuzz'],
             'correct_both':correct_both,'delta_correct':delta_correct,
             'gold_delta':len(golds['base']^golds['fuzz']),
             'predicted_delta':len(decisions['base']^decisions['fuzz']),
             'base_errors':edge_errors['base'],'fuzz_errors':edge_errors['fuzz']}
        totals.update({'pairs':1,'both_exact':int(correct_both),
                       'delta_correct':int(delta_correct),
                       'g1_exact_both':int(row['g1_exact_both']),
                       'counterfactual_errors':int(not delta_correct),
                       'graph_errors':sum(edge_errors.values())})
        details.append(row)
    return {'summary':dict(totals),'pairs':details}


def _activation_attribution(head,fuzz_items,spec,device,max_cases=2):
    """Trace a high-error fuzz input from each family, not merely first two."""
    candidates={}
    for _,mode,family,sides in fuzz_items:
        for side,item in sides.items():
            if len(item['spans'])<2:continue
            if len(item['spans'])<2:
                pred=set()
            else:
                logits=structural._forward(head,item,device)
                surface=structural._surface_from_logits(item['objects'],logits)
                pred=structural.previous.decode(surface,spec)
            full=structural.parent.previous._gold_edges(item['row']['graph'])
            err=len(pred^full)
            if family not in candidates or err>candidates[family][0]:
                candidates[family]=(err,mode,side,item)
    result=[]
    for family in FAMILIES:
        if family not in candidates:continue
        _,mode,side,item=candidates[family]
        h=item['hidden'].to(device);e=item['eligible'].to(device)
        raw,captured=fuzz.trace_head(head,h,e,item['spans'])
        rows=fuzz._pair_stats(raw['binding_logits'],item['spans'],
                              item['prepared'],item['row']['graph'])
        result.append({'family':family,'mode':mode,'side':side,
                 'prompt_sha256':digest(item['row']),
                 'pair_stats':fuzz.summarize_pairs(rows),
                 'stage_gold_conditioned_separation':
                    fuzz._representation_probe(captured,item['spans'],rows)})
    return {'scope':'observational_gold_conditioned; no unique layer attribution',
            'traces':result}


def _cross_family_gradients(head,items,spec,device,include_transfer=False):
    """Cross-family loss gradients by module and reversible causal probes.

    A negative cosine is a first-order interference hypothesis. The finite
    weight-space transfer is the actual causal check, with restored weights.
    """
    chosen=[next(it for it in items if it['row']['family']==f) for f in FAMILIES]
    evidence=trainability.gradient_diagnosis(head,chosen,spec,device,
                                            include_transfer=include_transfer)
    require(evidence['cases']==2 and evidence['pair_count']==1,
            'GRADIENT_EVIDENCE_MISSING')
    params=trainability._trainable(head)
    vectors,_=trainability._per_case_gradients(head,chosen,spec,device,params)
    offset=0
    group_segments={name:[] for name in ('binding','pair_projection','g1_g2_coupler')}
    for name,param in head.named_parameters():
        if not param.requires_grad:continue
        n=param.numel()
        category=('binding' if name.startswith('binding.') else
                  'pair_projection' if name.startswith('pair_projection.') else
                  'g1_g2_coupler' if name.startswith('interface_couplers.G1_G2.')
                  else None)
        if category:group_segments[category].append((offset,offset+n))
        offset+=n
    require(offset==vectors.shape[1],'GRADIENT_VECTOR_OFFSET_DRIFT')
    by_module={}
    for name,segments in group_segments.items():
        if not segments:
            by_module[name]={'cosine':None,'reason':'NO_MUTABLE_PARAMETERS'}
            continue
        left=torch.cat([vectors[0,start:stop] for start,stop in segments])
        right=torch.cat([vectors[1,start:stop] for start,stop in segments])
        denom=float(torch.linalg.vector_norm(left)*torch.linalg.vector_norm(right))
        by_module[name]={'cosine':float(torch.dot(left,right))/denom if denom>1e-20 else None,
             'allocation_gradient_l2':float(torch.linalg.vector_norm(left)),
             'encounter_gradient_l2':float(torch.linalg.vector_norm(right))}
    evidence['module_cosines']=by_module
    return evidence


def _status_label(train_by,held_by,prev_by,baseline_by,fuzz_report):
    reasons=compare_family(held_by,prev_by,baseline_by)
    if any(x['g1_exact_both'] is False for x in fuzz_report['pairs']):
        reasons.append('counterfactual:G1_mismatch_in_some_pairs')
    if any(not r['delta_correct'] and r['g1_exact_both'] for r in fuzz_report['pairs']):
        reasons.append('counterfactual:G2_transform_mismatch_with_exact_G1')
    if any(train_by[f]['exact']<train_by[f]['cases'] for f in FAMILIES):
        reasons.append('trainability:not_yet_complete')
    return reasons


def _diagnosis_label(reasons,grad,probe):
    """Hypotheses only; no fictitious causal proof from correlation."""
    labels=[]
    if any('G1_mismatch' in s for s in reasons):labels.append('UPSTREAM_G1_SENSITIVITY')
    if any('G2_transform' in s for s in reasons):labels.append('COUNTERFACTUAL_BINDING_INSTABILITY')
    if any('more_edge_errors' in s or 'below_initial' in s for s in reasons):
        labels.append('HELDOUT_FAMILY_REGRESSION')
    if grad['minimum_pair_cosine'] is not None and grad['minimum_pair_cosine']<0:
        labels.append('NEGATIVE_CROSS_FAMILY_GRADIENT_ALIGNMENT')
    if any(t['pair_stats'].get('margin_auc') is not None and
           t['pair_stats']['margin_auc']<0.75 for t in probe['traces']):
        labels.append('WEAK_EDGE_MARGIN_SEPARATION_ON_PROBED_CASES')
    return {'hypotheses':labels,'localized_layer_proven':False,
            'requires_intervention_or_new_evidence':True}


def _compact_snapshot(head,items,spec,device):
    """Unconditional heldout graph accuracy, including unreachable G1 edges.

    The standard trainability snapshot evaluates only the gold edges reachable
    from predicted G1 spans. This explicit recomputation prevents silently
    counting an unreachable G2 graph as exact during monitoring.
    """
    records=[]
    with torch.no_grad():
        for item in items:
            if len(item['spans'])<2:
                pred=set()
            else:
                logits=structural._forward(head,item,device)
                surface=structural._surface_from_logits(item['objects'],logits)
                pred=structural.previous.decode(surface,spec)
            gold=structural.parent.previous._gold_edges(item['row']['graph'])
            reachable={edge for edge in gold if
                       (edge[0],edge[1]) in {(o[2],(o[0],o[1])) for o in item['objects']}
                       and (edge[2],edge[3]) in {(o[2],(o[0],o[1])) for o in item['objects']}}
            require(len(reachable)==item['reachable'],'HELDOUT_REACHABILITY_DRIFT')
            tp=len(gold&pred)
            records.append({'prompt_sha256':digest(item['row']),
               'family':item['row']['family'], 'split':item['row']['split'],
               'exact':int(pred==gold),'true_positive':tp,
               'false_positive':len(pred-gold),'false_negative':len(gold-pred),
               'predicted_edges':len(pred),'gold_edges':len(gold),
               'g1_reachable':int(len(reachable)==len(gold)),
               'g1_unreachable_gold_edges':len(gold)-len(reachable),
               'within_kind_inversions':0,'within_kind_groups':0})
    families=family_summary({'records':records})
    return {'metrics':{'cases':len(items),'exact':sum(x['exact'] for x in records),
             'false_positive':sum(x['false_positive'] for x in records),
             'false_negative':sum(x['false_negative'] for x in records),
             'g1_reachable_cases':sum(x['g1_reachable'] for x in records)},
            'families':families,'cases':records}


def _save_experimental(head,manifest,paths,dest,arm,stage,report):
    path=dest/arm/f'stage-{stage}'/'checkpoint'
    structural._save_head(head,manifest,path,paths,
            arm='MIXED_FAMILY_FUZZ_DIAGNOSTIC_NOT_DEPLOYABLE',detail={
                'stage':stage,'arm':arm,'best_exact':report['best_exact'],
                'g3_unlocked':False})
    return sha(path/cur.HEAD_FILE)


def _train_stage(head,optimizer,train_items,heldout,fuzz_items,train_fuzz,
                 spec,device,*,limit,arm,stage,dest,manifest,paths):
    require(len(train_items)==stage and stage in STAGES,'BAD_STAGE')
    require(all(sum(x['row']['family']==f for x in train_items)==stage//2
                for f in FAMILIES),'UNBALANCED_STAGE')
    arm_path=dest/arm/f'stage-{stage}'
    arm_path.mkdir(parents=True,exist_ok=False)
    params=trainability._trainable(head)
    head.eval()
    first=trainability.snapshot(head,train_items,spec,device)
    best=first;best_step=0;best_weights=trainability._copy_head(head)
    best_optimizer=deepcopy(optimizer.state_dict())
    baseline_hold=_compact_snapshot(head,heldout,spec,device)
    prev_hold=baseline_hold['families']
    base_hold=deepcopy(prev_hold)
    history=[];event_files=[];prior_fuzz=None
    journal=arm_path/'monitor.jsonl'  # durable even on interrupted stages
    stable=0;stable_success=False
    for step in range(1,limit+1):
        optimizer.zero_grad(set_to_none=True)
        for item in train_items:
            loss,_=structural._train_case(head,item,device,spec)
            require(torch.isfinite(loss).item(),'TRAIN_LOSS_NONFINITE')
            (loss/len(train_items)).backward()
            train_loss=float(loss.detach())
        # Counterfactual supervision uses disjoint TRAIN-name pairs; the test-name
        # battery below is completely excluded from learning and selection.
        if train_fuzz and step%4==0:
            mode,a,b=train_fuzz[((step//4)-1)%len(train_fuzz)]
            consistency=structural._consistency(head,a,b,mode,device)
            if consistency is not None:
                require(torch.isfinite(consistency).item(),'CONSISTENCY_NONFINITE')
                (CONSISTENCY_WEIGHT*consistency).backward()
        norm=torch.nn.utils.clip_grad_norm_(params,1.)
        require(torch.isfinite(norm).item(),'GRADIENT_NONFINITE')
        optimizer.step()
        if step!=1 and step%EVAL_INTERVAL!=0 and step!=limit:continue
        current=trainability.snapshot(head,train_items,spec,device)
        if balanced_quality(current)>balanced_quality(best):
            best=current;best_step=step
            best_weights=trainability._copy_head(head)
            best_optimizer=deepcopy(optimizer.state_dict())
        held=_compact_snapshot(head,heldout,spec,device)
        run_fuzz=step==1 or step%FUZZ_INTERVAL==0 or step==limit
        fuzz_now=fuzz_probe(head,fuzz_items,spec,device) if run_fuzz else None
        if fuzz_now is None:
            # No hidden assumption about unmeasured counterfactual correctness.
            reason=compare_family(held['families'],prev_hold,base_hold)
        else:
            reason=_status_label(family_summary(current),held['families'],
                                 prev_hold,base_hold,fuzz_now)
        if prior_fuzz is not None and fuzz_now is not None and (
                fuzz_now['summary']['counterfactual_errors']>
                prior_fuzz['summary']['counterfactual_errors']):
            reason.append('counterfactual:new_transform_regressions')
        if fuzz_now is not None:prior_fuzz=fuzz_now
        prev_hold=held['families']
        rec={'step':step,'training':{k:v for k,v in current.items() if k!='records'},
             'training_by_family':family_summary(current),
             'heldout_by_family':held['families'],
             'heldout_reachable':{
                f:sum(it['reachable']==it['full_gold_count'] for it in heldout
                      if it['row']['family']==f) for f in FAMILIES},
             'fuzz':fuzz_now,'flags':reason}
        history.append(rec)
        with journal.open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(rec,sort_keys=True,allow_nan=False)+'\n')
            stream.flush()
        trigger=bool(reason) and (
            step==1 or step%GRADIENT_INTERVAL==0 or
            any('lost_exact' in r or 'more_edge_errors' in r or
                'new_transform_regressions' in r for r in reason))
        if trigger and len(event_files)<MAX_EVENTS:
            gradient=_cross_family_gradients(head,train_items,spec,device,
                                              include_transfer=step%GRADIENT_INTERVAL==0)
            activation=_activation_attribution(head,fuzz_items,spec,device)
            event={'schema':SCHEMA,'arm':arm,'stage':stage,'step':step,'flags':reason,
                'gradient':gradient,'activation':activation,
                'interpretation':_diagnosis_label(reason,gradient,activation),
                'g3_unlocked':False}
            file=arm_path/f'diagnostic-{step:05}.json'
            write(file,event)
            event_files.append({'file':file.name,'sha256':sha(file)})
        print('G2_0092_MONITOR '+json.dumps({'arm':arm,'stage':stage,'step':step,
              'exact':current['exact'],'best_exact':best['exact'],
              'by_family':{f:{'exact':family_summary(current)[f]['exact'],
                'fp':family_summary(current)[f]['fp'],
                'fn':family_summary(current)[f]['fn'],
                'heldout_f1':round(held['families'][f]['edge_f1'],4)} for f in FAMILIES},
              'fuzz_delta_correct':fuzz_now['summary']['delta_correct'] if fuzz_now else None,
              'diagnostics':len(event_files)},sort_keys=True),flush=True)
        stable=stable+1 if current['exact']==stage else 0
        if stable>=2:
            stable_success=True
            break
    head.load_state_dict(best_weights,strict=True)
    optimizer.load_state_dict(best_optimizer)
    confirmed=trainability.snapshot(head,train_items,spec,device)
    require(confirmed['exact']==best['exact'] and
            confirmed['graph_errors']==best['graph_errors'],'RESTORE_BEST_MISMATCH')
    final_hold=_compact_snapshot(head,heldout,spec,device)
    final_fuzz=fuzz_probe(head,fuzz_items,spec,device)
    report={'schema':SCHEMA,'arm':arm,'stage':stage,'steps_executed':step,
         'best_step':best_step,'best_exact':best['exact'],
         'exact_passed':best['exact']==stage and stable_success,
         'two_consecutive_exact_checkpoints':stable_success,
         'final_fuzz':final_fuzz,'final_heldout_by_family':final_hold['families'],
         'heldout_baseline_by_family':baseline_hold['families'],
         'training_by_family':family_summary(confirmed),
         'diagnostic_files':event_files,'checkpoint_sha256':None,
         'g2_certified':False,'g3_unlocked':False}
    write(arm_path/'history.json',history)
    report['history_sha256']=sha(arm_path/'history.json')
    report['journal_sha256']=sha(journal)
    report['failure_attribution']=failure_attribution(report,arm_path)
    report['checkpoint_sha256']=_save_experimental(head,manifest,paths,dest,arm,stage,report)
    write(arm_path/'report.json',report)
    write(arm_path/'lock.json',{'schema':SCHEMA,'report_sha256':sha(arm_path/'report.json'),
        'history_sha256':report['history_sha256'],
        'journal_sha256':report['journal_sha256'],
        'checkpoint_sha256':report['checkpoint_sha256'],'diagnostics':event_files,
        'g3_unlocked':False})
    return report


def failure_attribution(stage,diagnostic_root):
    """Evidence-linked possible failure causes; not a claim of layer causality."""
    flags=[]
    training=stage['training_by_family']
    held=stage['final_heldout_by_family']
    base=stage['heldout_baseline_by_family']
    for family in FAMILIES:
        f=training[family]
        if f['exact']<f['cases']:
            flags.append(family+':MEMORIZATION_INCOMPLETE')
        if f['fp']>f['fn']:
            flags.append(family+':FALSE_EDGE_ADMISSION_DOMINATES_TRAIN_ERRORS')
        elif f['fn']>f['fp']:
            flags.append(family+':MISSING_EDGE_RECALL_DOMINATES_TRAIN_ERRORS')
        if held[family]['edge_f1']<base[family]['edge_f1']-0.03:
            flags.append(family+':HELDOUT_F1_REGRESSED_GT_3PP')
        if held[family]['exact']<base[family]['exact']:
            flags.append(family+':HELDOUT_EXACT_REGRESSION')
    fuzz_summary=stage['final_fuzz']['summary']
    if fuzz_summary['counterfactual_errors']:
        flags.append('FUZZ:SEMANTIC_TRANSFORMATIONS_INCORRECT')
    if fuzz_summary['g1_exact_both']<fuzz_summary['pairs']:
        flags.append('FUZZ:G1_EXTRACTION_INSTABILITY')
    negative_evidence=[]
    for event in stage['diagnostic_files']:
        source=diagnostic_root/event['file']
        require(sha(source)==event['sha256'],'DIAGNOSTIC_TAMPERED')
        data=read(source)
        if data['gradient']['negative_pair_count']:
            negative_evidence.append({'step':data['step'],
                       'whole_model_cosine':data['gradient']['minimum_pair_cosine'],
                       'module_cosines':data['gradient'].get('module_cosines',{}),
                       'source_sha256':event['sha256']})
    if negative_evidence:
        flags.append('NEGATIVE_CROSS_FAMILY_GRADIENT_ALIGNMENT_OBSERVED')
    return {'flags':flags,'gradient_conflicts':negative_evidence,
       'fuzz_exact_pairs':fuzz_summary['both_exact'],
       'fuzz_correct_transforms':fuzz_summary['delta_correct'],
       'fuzz_pairs':fuzz_summary['pairs'],
       'training_family_errors':{f:{'false_positive':training[f]['fp'],
                                   'false_negative':training[f]['fn']}
                                   for f in FAMILIES},
       'heldout_f1_delta':{f:held[f]['edge_f1']-base[f]['edge_f1'] for f in FAMILIES},
       'explanation':'conditional hypotheses from observed gradients, graph errors, '
                     'counterfactual violations, and traced stages; unique neural '
                     'layer not identified without selective intervention'}


def run(*,root=ROOT_DEFAULT,output=None,device='cuda',steps=None):
    limits=stage_steps(steps)
    root=Path(root).resolve()
    dest=(Path(output) if output else root/'oracle-fabric-g2-mixed-family-fuzz-0092').resolve()
    require(root.is_dir() and not dest.exists(),'OUTPUT_ALREADY_EXISTS_OR_ROOT_MISSING')
    paths=frozen._roots(root)
    parents=frozen.verify_parent(paths)
    require((paths['parent_0090']/'stage-8'/'checkpoint'/cur.HEAD_FILE).is_file(),
            '0090_MEMORIZATION_MISSING')
    network,tok,composite,parent_head,parent_manifest=structural._load_runtime(paths,device)
    spec=parents['decoder']
    selected,rejections=_eligible_training(network,tok,composite,parent_head,
                                          paths,spec,device)
    chosen=balanced_prefix(selected,4)
    train_hashes={digest(it['row']) for it in chosen}
    heldout=_heldout_items(network,tok,composite,parent_head,paths,device)
    require(train_hashes.isdisjoint(digest(it['row']) for it in heldout),
            'TRAIN_HELDOUT_OVERLAP')
    fuzz_items=_diagnostic_fuzz(network,tok,composite,parent_head,device,train_hashes)
    excluded=set(train_hashes)
    excluded.update(digest(it['row']) for it in heldout)
    for _,_,_,sides in fuzz_items:
        excluded.update(digest(item['row']) for item in sides.values())
    train_fuzz=_training_fuzz(network,tok,composite,parent_head,device,excluded)
    require({a['row']['family'] for _,a,b in train_fuzz}==set(FAMILIES),
            'MISSING_G1_EXACT_TRAIN_COUNTERFACTUAL_FAMILY')
    require(all(digest(x['row']) not in train_hashes for _,a,b in train_fuzz for x in (a,b)),
            'COUNTERFACTUAL_TRAIN_CASE_OVERLAP')
    parent_87=paths['parent_0087']/'training'
    parent_90=paths['parent_0090']/'stage-8'/'checkpoint'
    parent_checksums={'from_0087':sha(parent_87/cur.HEAD_FILE),
                      'from_0090':sha(parent_90/cur.HEAD_FILE)}
    dest.mkdir(parents=False,exist_ok=False)
    provenance={'schema':SCHEMA,'runner_sha256':sha(__file__),
        'source_0091_sha256':sha(Path(frozen.__file__)),
        'source_0090_sha256':sha(Path(trainability.__file__)),
        'parent_0090_lock_sha256':sha(paths['parent_0090']/'stage-8'/'lock.json'),
        'g1_certificate_sha256':parents['g1_certificate_sha256'],
        'dataset_sha256':parents['dataset_sha256'],
        'parent_checkpoints':parent_checksums,'decoder':spec,
        'selection':[{'family':it['row']['family'],'prompt_sha256':digest(it['row'])}
                     for it in chosen],
        'heldout':[{'family':it['row']['family'],'split':it['row']['split'],
                    'prompt_sha256':digest(it['row'])} for it in heldout],
        'diagnostic_fuzz':[{'pair_id':k,'mode':m,'family':fam,
                    'base_sha256':digest(s['base']['row']),
                    'fuzz_sha256':digest(s['fuzz']['row'])}
                    for k,m,fam,s in fuzz_items],
        'training_counterfactuals':[
            {'mode':m,'base_sha256':digest(a['row']),
             'fuzz_sha256':digest(b['row'])} for m,a,b in train_fuzz],
        'excluded_selection':rejections,
        'selection_bias':'fully_G1_reachable_and_oracle_decodable_train_only',
        'heldout_use':'exposed_original_dev_challenge_diagnostics_only',
        'diagnostic_fuzz_use':'test_pool_diagnostics_only_never_optimized',
        'steps':limits,'seed':SEED,'g2_certified':False,'g3_unlocked':False,
        'independent_certification':False}
    write(dest/'provenance.json',provenance)
    torch.manual_seed(SEED)
    arms={}
    for name,source in (('A_from_0087',parent_87),('B_from_0090',parent_90)):
        head,manifest=cur._load_checkpoint(source,device=device)
        structural._mutable(head)
        head.eval()
        optimizer=torch.optim.AdamW([p for p in head.parameters() if p.requires_grad],
                                    lr=structural.LR,weight_decay=0.)
        outcomes=[]
        for stage,limit in zip(STAGES,limits):
            subset=balanced_prefix(chosen,stage//2)
            result=_train_stage(head,optimizer,subset,heldout,fuzz_items,train_fuzz,
                     spec,device,limit=limit,arm=name,stage=stage,dest=dest,
                     manifest=parent_manifest,paths=paths)
            outcomes.append(result)
            print('G2_0092_STAGE '+json.dumps({'arm':name,'stage':stage,
                  'exact':result['best_exact'],'passed':result['exact_passed'],
                  'diagnostics':len(result['diagnostic_files'])},sort_keys=True),flush=True)
            if not result['exact_passed']:break
        arms[name]={'stages':[{'stage':r['stage'],'best_exact':r['best_exact'],
                     'passed':r['exact_passed'],
                     'checkpoint_sha256':r['checkpoint_sha256'],
                     'heldout_by_family':r['final_heldout_by_family'],
                     'fuzz':r['final_fuzz']['summary'],
                     'failure_attribution':r['failure_attribution']}
                     for r in outcomes],
                    'eight_pass':bool(outcomes and outcomes[-1]['stage']==8 and
                                       outcomes[-1]['exact_passed'])}
    require(all(sha(path/cur.HEAD_FILE)==parent_checksums[name]
               for name,path in (('from_0087',parent_87),('from_0090',parent_90))),
            'PARENT_CHECKPOINT_MUTATED')
    any_eight=any(x['eight_pass'] for x in arms.values())
    report={'schema':SCHEMA,'status':(
       'MIXED_FAMILY_EIGHT_GRAPH_TRAINABILITY_PASSED_NOT_CERTIFIED'
       if any_eight else 'MIXED_FAMILY_TRAINABILITY_BLOCKED'),
       'arms':arms,'provenance_sha256':sha(dest/'provenance.json'),
       'parent_checkpoints_unchanged':True,
       'family_generalization_gate':'UNDETERMINED_DIAGNOSTICS_ONLY',
       'next_action':'independently sealed mixed-family continuation evaluation'
                     if any_eight else 'inspect triggered gradients and fuzz diagnostics',
       'g2_certified':False,'g3_unlocked':False,'no_promotion':True,
       'independent_certification':False}
    write(dest/'report.json',report)
    print('G2_0092_RESULT '+json.dumps({'status':report['status'],
        'arms':{k:{'stage_exact':[(s['stage'],s['best_exact']) for s in v['stages']],
                   'eight_pass':v['eight_pass']} for k,v in arms.items()},
        'g3_unlocked':False},sort_keys=True),flush=True)
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',nargs='?',default='run',choices=('run',))
    p.add_argument('--root',default=ROOT_DEFAULT)
    p.add_argument('--output')
    p.add_argument('--device',default='cuda')
    p.add_argument('--steps',help='4-case,8-case maximum updates; default 650,1300')
    args=p.parse_args(argv)
    return run(root=args.root,output=args.output,device=args.device,steps=args.steps)

if __name__=='__main__':main()
