"""G1-0070: frozen Oracle-Fabric versus legacy candidate-admission bake-off.

Explicit comparison to the dev-selected ``whole_linear`` decoder from the
prior 0069-fabric experiment. No training, parameter changes, promotion, gate
advancement, sealed tests, or gold-assisted decoding. The development cohort
alone selects a research arm; the previously exposed challenge cohort is
reported only after that choice is frozen and is NOT certification evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path

import torch

from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS
from tools.nanojev_gamer_v1_g1_decoder_frob import (
    _candidate_scores, _char_set, decode_whole_span_scores,
)
from tools.nanojev_gamer_v1_g1_oracle_decoder_cutover_frob import (
    _checked_preflight, _oracle_ceilings, _summarize, sha256,
)

SCHEMA = 'nanojev.gamer.graph.g1.oracle-fabric-frozen-admission-0070.v1'
CONTROL = 'whole_linear'
REQUIRED_KINDS = ('actor', 'action', 'quantity', 'outcome')


@dataclass(frozen=True)
class Arm:
    name: str
    # All constants are PREDECLARED, not fitted from development gold.
    kind: str = 'none'                 # none / actor / action / actor_action / all
    deficit: float = 0.0              # admitted null-versus-kind logit deficit
    rescue: str = 'none'              # none / light / offset
    rank_bonus: float = 0.0           # adjusts only baseline-eligible scores
    runnerup: bool = False            # optional near-tied actor type
    runnerup_actor_bonus: float = 0.0  # asymmetric actor prior for competing types
    conditional_min: float = 0.60     # softmax within the non-null classes


ARMS = (
    Arm(CONTROL),
    Arm('rank_actor_action_0p5',kind='actor_action',rank_bonus=0.5),
    Arm('rescue_actor_0p5',kind='actor',deficit=0.5,rescue='light'),
    Arm('rescue_action_0p5',kind='action',deficit=0.5,rescue='light'),
    Arm('rescue_actor_action_0p5',kind='actor_action',deficit=0.5,rescue='light'),
    Arm('rescue_actor_action_1p0',kind='actor_action',deficit=1.0,rescue='light'),
    Arm('rescue_all_0p5',kind='all',deficit=0.5,rescue='light'),
    Arm('joint_actor_action_0p5',kind='actor_action',deficit=0.5,rescue='offset',rank_bonus=0.25),
    Arm('joint_actor_action_1p0',kind='actor_action',deficit=1.0,rescue='offset',rank_bonus=0.25),
    Arm('runnerup_actor_prior_0p75',kind='actor_action',runnerup=True,runnerup_actor_bonus=0.75),
    Arm('joint_runnerup_actor_prior_0p75',kind='actor_action',deficit=0.5,
        rescue='offset',rank_bonus=0.25,runnerup=True,runnerup_actor_bonus=0.75),
)
ARM_NAMES = tuple(x.name for x in ARMS)


def _kinds(spec):
    return {'none':frozenset(),'actor':frozenset(('actor',)),
            'action':frozenset(('action',)),
            'actor_action':frozenset(('actor','action')),
            'all':frozenset(SPAN_KINDS)}[spec.kind]


def _check_specs():
    if len(ARM_NAMES)!=len(set(ARM_NAMES)) or ARMS[0]!=Arm(CONTROL):
        raise ValueError('G1_ADMISSION_ARM_CATALOGUE_INVALID')
    for s in ARMS:
        if (s.rescue not in ('none','light','offset') or
                not 0<=s.deficit<=2 or not 0<=s.rank_bonus<=2 or
                not 0<=s.runnerup_actor_bonus<=2 or
                not .5<=s.conditional_min<=1 or
                s.kind not in ('none','actor','action','actor_action','all')):
            raise ValueError('G1_ADMISSION_ARM_PARAMETERS_INVALID')


def _pool(logits, valid):
    """Represent all valid frozen neural scores once, without any label input."""
    if (logits.ndim!=3 or valid.shape!=logits.shape[:2] or
            logits.shape[-1]!=1+len(SPAN_KINDS)):
        raise ValueError('G1_ADMISSION_LOGIT_SHAPE')
    with torch.no_grad():
        values=logits.detach().float().cpu()
        mask=valid.detach().bool().cpu()
        if not torch.isfinite(values[mask]).all():
            raise ValueError('G1_ADMISSION_NONFINITE_SCORES')
        probs=values.softmax(-1)
        conditionals=values[...,1:].softmax(-1)
        size,width=mask.shape
        pool=[]
        for start,length_index in mask.nonzero(as_tuple=False).tolist():
            if start+length_index>=size:
                raise ValueError('G1_ADMISSION_INVALID_SPAN_MASK')
            active=values[start,length_index]
            sorted_classes=torch.argsort(active[1:],descending=True,stable=True).tolist()
            k=sorted_classes[0]+1
            alternative=sorted_classes[1]+1 if len(sorted_classes)>1 else k
            margin=float((active[k]-active[0]).item())
            alt_margin=float((active[alternative]-active[0]).item())
            pool.append((start,length_index,k,margin,
                         float(probs[start,length_index,k].item()),
                         float(conditionals[start,length_index,k-1].item()),
                         alternative,alt_margin))
    return size,pool


def _score_candidates(size,pool,spec):
    """Build label-blind options; no prompt, oracle or target is accessible."""
    allowed=_kinds(spec)
    by_end=[[] for _ in range(size+1)]
    seen=set()
    for start,offset,label,margin,p_raw,p_cond,runnerup,alt_margin in pool:
        end=start+offset+1
        kind=SPAN_KINDS[label-1]
        baseline=margin>0 and p_raw>=.5
        rescued=(not baseline and spec.rescue!='none' and kind in allowed
                 and margin>=-spec.deficit and p_cond>=spec.conditional_min)
        if baseline or rescued:
            if baseline:
                weight=margin+(spec.rank_bonus if kind in allowed else 0)
            elif spec.rescue=='light':
                # Small positive admission, minimally competitive with eligible
                # candidates, so the ablation mostly tests empty-space recovery.
                weight=0.20*p_cond
            else:
                # Reweight null-vs-kind information by a deterministic prior.
                weight=margin+spec.deficit+(spec.rank_bonus if kind in allowed else 0)
            if weight>0:
                by_end[end].append((start,offset,label,float(weight)))
                seen.add((start,offset,label))
        if spec.runnerup and baseline and runnerup!=label:
            alternate_kind=SPAN_KINDS[runnerup-1]
            if (alternate_kind=='actor' and kind=='action' and
                    0<=margin-alt_margin<=0.75 and
                    alt_margin+spec.rank_bonus+spec.runnerup_actor_bonus>0 and
                    (start,offset,runnerup) not in seen):
                by_end[end].append((start,offset,runnerup,float(
                    alt_margin+spec.rank_bonus+spec.runnerup_actor_bonus)))
                seen.add((start,offset,runnerup))
    return by_end


def _decode_all(logits,valid):
    from tools.nanojev_gamer_v1_graph_head import decode_neural_spans
    size,pool=_pool(logits,valid)
    predictions={}
    old_candidates=_candidate_scores(logits,valid,confidence=.5)
    # Ensure the pool reproduces the production eligibility function exactly.
    ours=_score_candidates(size,pool,ARMS[0])
    if ours!=old_candidates:
        # Candidate iteration order may differ, but ties must remain equivalent.
        canon=lambda seq:[sorted(v) for v in seq]
        if canon(ours)!=canon(old_candidates):
            raise ValueError('G1_ADMISSION_BASELINE_POOL_DIVERGED')
        ours=old_candidates
    baseline=decode_whole_span_scores(ours,length_power=1,per_object_penalty=0)
    production=decode_neural_spans(logits,valid,confidence=.5)
    old_global=decode_whole_span_scores(ours,length_power=0,per_object_penalty=0)
    if production!=old_global:
        raise ValueError('G1_ADMISSION_ORIGINAL_DECODER_PARITY')
    predictions[CONTROL]=baseline
    for s in ARMS[1:]:
        candidates=_score_candidates(size,pool,s)
        try:
            predictions[s.name]=decode_whole_span_scores(candidates,length_power=1,
                                                          per_object_penalty=0)
        except ValueError as exc:
            if str(exc)!='GAMER_GRAPH_TOO_MANY_PREDICTED_OBJECTS':
                raise
            # Explicit failed arm, not a silently dropped easy case.
            predictions[s.name]=None
    return predictions,old_candidates


def _paired_exact_test(base, changed):
    """Exact two-sided discordance test, not a sealed certification p-value."""
    from math import comb
    if len(base)!=len(changed) or any(g!=h for (_,g),(_,h) in zip(base,changed)):
        raise ValueError('G1_ADMISSION_UNPAIRED_EXACT_TEST')
    wins=sum(p!=g and q==g for (p,g),(q,h) in zip(base,changed))
    losses=sum(p==g and q!=g for (p,g),(q,h) in zip(base,changed))
    n=wins+losses
    p=min(1.0,2*sum(comb(n,k) for k in range(min(wins,losses)+1))/2**n) if n else 1.0
    return {'gained_exact_cases':wins,'lost_exact_cases':losses,
            'paired_discordant_cases':n,'two_sided_exact_p':p,
            'historical_exploratory_only':True}


def _changes(original,variant):
    counts={'recovered_correct':0,'lost_correct':0,
            'introduced_false_objects':0,'removed_false_objects':0,
            'by_kind':{kind:Counter() for kind in SPAN_KINDS}}
    for (before,gold),(after,confirmed) in zip(original,variant):
        if gold!=confirmed:raise ValueError('G1_ADMISSION_UNPAIRED_GOLD')
        terms={'recovered_correct':(after&gold)-(before&gold),
               'lost_correct':(before&gold)-(after&gold),
               'introduced_false_objects':(after-gold)-(before-gold),
               'removed_false_objects':(before-gold)-(after-gold)}
        for name,objects in terms.items():
            counts[name]+=len(objects)
            for *_,kind in objects:counts['by_kind'][kind][name]+=1
    counts['by_kind']={k:dict(v) for k,v in counts['by_kind'].items() if v}
    counts['net_correct']=counts['recovered_correct']-counts['lost_correct']
    counts['net_false']=counts['introduced_false_objects']-counts['removed_false_objects']
    counts['exact_pairing']=_paired_exact_test(original,variant)
    return counts


def _dev_select(metrics, overflow=None):
    """Lock a research choice using development measurements ONLY.

    Reject if actor/action/quantity/outcome recall, object F1 or exact sets
    regresses relative to the proven whole_linear control. Require positive
    improvement in either exact sets or F1. Do not require a winner.
    """
    base=metrics[CONTROL]
    viable=[CONTROL]
    for name in ARM_NAMES[1:]:
        m=metrics[name]
        if overflow and overflow.get(name,0):
            continue
        if (m['exact_prompts']>=base['exact_prompts'] and
                m['f1']>=base['f1'] and
                m['precision']>=base['precision']-.01 and
                all(m['by_kind'].get(k,{}).get('recall',0)>=
                    base['by_kind'].get(k,{}).get('recall',0) for k in REQUIRED_KINDS) and
                (m['exact_prompts']>base['exact_prompts'] or m['f1']>base['f1']+1e-10)):
            viable.append(name)
    return max(viable,key=lambda n:(metrics[n]['exact_prompts'],metrics[n]['f1'],
                                  metrics[n]['precision'],-ARM_NAMES.index(n)))


def _oracle_deficits(summary):
    eligible=summary['oracle_eligible']['correct']
    nonnull=summary['oracle_nonnull_type']['correct']
    forced=summary['oracle_forced_type']['correct']
    total=summary['oracle_forced_type']['expected']
    if not 0<=eligible<=nonnull<=forced<=total:
        raise ValueError('G1_ADMISSION_ORACLE_DEFICIT_INCONSISTENT')
    return {'eligible_correct':eligible,
            'nonnull_correct_but_not_eligible':nonnull-eligible,
            'typed_gold_span_not_top_nonnull':forced-nonnull,
            'unsupported_or_unaligned_gold':total-forced,
            'total_gold_objects':total}


def _verify_decoder_reference(location,entries,dataset_sha,case_counts):
    """Tie the new bake-off to the recorded whole_linear predecessor."""
    from tools import nanojev_gamer_v1_g1_oracle_decoder_cutover_frob as prior
    path=Path(location)
    doc=json.loads((path/'report.json').read_text(encoding='utf-8'))
    if (doc.get('schema')!=prior.SCHEMA or
            doc.get('lineage',{}).get('dataset_sha256')!=dataset_sha or
            doc.get('lineage',{}).get('checkpoints')!={
                k:v['manifest']['head_sha256'] for k,v in entries.items()} or
            doc.get('training_performed') is not False or
            doc.get('production_promotion') is not False or
            doc.get('g2_unlocked') is not False or
            doc.get('sealed_test_used') is not False or
            doc.get('design',{}).get('dev_cases')!=case_counts['dev'] or
            doc.get('design',{}).get('historical_challenge_cases')!=case_counts['challenge'] or
            any(doc.get('arms',{}).get(k,{}).get('selected_decoder')!=CONTROL
                for k in entries)):
        raise ValueError('G1_ADMISSION_PRIOR_DECODER_REFERENCE_MISMATCH')
    return doc,sha256(path/'report.json')


def _prior_parity(prior_report,our_metrics,split):
    source='dev' if split=='dev' else 'historical_challenge'
    for model in ('legacy','cutover'):
        expected=prior_report['arms'][model][source]['models'][CONTROL]
        observed=our_metrics[model][CONTROL]
        for key in ('correct','predicted','expected','exact_prompts'):
            if expected[key]!=observed[key]:
                raise ValueError('G1_ADMISSION_WHOLE_LINEAR_REFERENCE_DRIFT:'+model+':'+key)


def _evaluate(*,split,rows,heads,model,tok,device,max_length):
    from tools import nanojev_gamer_v1_backbone_first as backbone
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    records={name:{arm:[] for arm in ARM_NAMES} for name in heads}
    oracle={name:{k:[] for k in ('oracle_eligible','oracle_nonnull_type','oracle_forced_type')}
            for name in heads}
    details=[]
    overflow={name:Counter() for name in heads}
    for idx,row in enumerate(rows,1):
        prepared=backbone.prepare_prompt(tok,row['prompt'],max_input_tokens=max_length)
        with torch.inference_mode():
            hidden=backbone.frozen_context(model,prepared,device=device)
            predictions={name:head(hidden.to(device=device,dtype=torch.float32),
                                   eligible=prepared['eligible'].to(device),
                                   active_stage='G1') for name,head in heads.items()}
            decoded={}
            eligible={}
            for name,pred in predictions.items():
                decoded[name],eligible[name]=_decode_all(pred['span_logits'],pred['span_valid'])
        # Only now read expected spans; never pass these to _decode_all.
        aligned=cur.aligned_labels(prepared,row)
        gold={(s['start'],s['end'],s['kind']) for s in row['object_spans']}
        for name,pred in predictions.items():
            char_predictions={arm:(_char_set(v,prepared) if v is not None else set())
                              for arm,v in decoded[name].items()}
            for arm,v in decoded[name].items():
                if v is None:overflow[name][arm]+=1
            oracle_sets=_oracle_ceilings(pred['span_logits'],pred['span_valid'],
                                       prepared,aligned,eligible[name])
            if not (oracle_sets['oracle_eligible']<=oracle_sets['oracle_nonnull_type']<=
                    oracle_sets['oracle_forced_type']<=gold):
                raise ValueError('G1_ADMISSION_ORACLE_CEILING_ALIGNMENT')
            for arm,out in char_predictions.items():records[name][arm].append((out,gold))
            for key,subset in oracle_sets.items():oracle[name][key].append((subset,gold))
            base=char_predictions[CONTROL]
            changes={}
            for arm,v in char_predictions.items():
                if arm==CONTROL:continue
                changes[arm]={
                    'recovered':[list(x) for x in sorted((v&gold)-(base&gold))],
                    'lost':[list(x) for x in sorted((base&gold)-(v&gold))],
                    'new_false':[list(x) for x in sorted((v-gold)-(base-gold))],
                    'removed_false':[list(x) for x in sorted((base-gold)-(v-gold))],
                    'exact':v==gold,
                }
            details.append({'split':split,'model':name,'index':idx,'family':row['family'],
                'prompt_sha256':hashlib.sha256(row['prompt'].encode('utf-8')).hexdigest(),
                'gold_count':len(gold),'control_exact':base==gold,
                'control_correct':len(base&gold),'changes':changes})
        if idx==1 or idx%10==0 or idx==len(rows):
            print(f'G1_ADMISSION_{split.upper()} {idx}/{len(rows)} '
                  'legacy+cutover frozen',flush=True)
    return (records,{name:{k:_summarize(v) for k,v in metrics.items()}
                      for name,metrics in records.items()},
            {name:{k:_summarize(v) for k,v in metrics.items()}
             for name,metrics in oracle.items()},details,overflow)


def run(*,dataset,legacy,cutover,decoder_reference,output,device='cuda',max_length=2048):
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    from tools import nanojev_gamer_v1_backbone_first as backbone
    _check_specs()
    if max_length<1:raise ValueError('G1_ADMISSION_BAD_MAX_LENGTH')
    entries,dataset_sha,cohorts=_checked_preflight(dataset,legacy,cutover,output)
    ref_path=Path(decoder_reference).resolve()
    output_path=Path(output).resolve()
    if output_path==ref_path or ref_path in output_path.parents:
        raise PermissionError('G1_ADMISSION_OUTPUT_INSIDE_DECODER_REFERENCE')
    prior,prior_hash=_verify_decoder_reference(decoder_reference,entries,dataset_sha,
                               {k:len(v) for k,v in cohorts.items()})
    heads={name:cur._load_checkpoint(info['path'],device=device)[0].eval()
           for name,info in entries.items()}
    ref=entries['legacy']['manifest']
    model,tok=backbone.model_components(ref['model_id'],ref['source_adapter'],device)
    dev_records,dev,dev_oracle,dev_details,dev_overflow=_evaluate(
        split='dev',rows=cohorts['dev'],heads=heads,model=model,tok=tok,
        device=device,max_length=max_length)
    _prior_parity(prior,dev,'dev')
    decisions={name:_dev_select(metrics,dev_overflow[name]) for name,metrics in dev.items()}
    print('G1_ADMISSION_DEV_SELECTION '+json.dumps(decisions,sort_keys=True),flush=True)
    # Decisions are frozen. Historical challenge cannot modify them.
    challenge_records,challenge,challenge_oracle,challenge_details,challenge_overflow=_evaluate(
        split='challenge',rows=cohorts['challenge'],heads=heads,model=model,tok=tok,
        device=device,max_length=max_length)
    _prior_parity(prior,challenge,'challenge')
    if sha256(dataset)!=dataset_sha or sha256(Path(decoder_reference)/'report.json')!=prior_hash:
        raise ValueError('G1_ADMISSION_REFERENCE_CHANGED_DURING_RUN')
    if backbone.adapter_fingerprint(ref['source_adapter'])!=ref['source_adapter_sha256']:
        raise ValueError('G1_ADMISSION_SOURCE_CHANGED_DURING_RUN')
    for name,info in entries.items():
        if sha256(Path(info['path'])/cur.HEAD_FILE)!=info['manifest']['head_sha256']:
            raise ValueError('G1_ADMISSION_CHECKPOINT_CHANGED_DURING_RUN')
    results={}
    for name in entries:
        selected=decisions[name]
        results[name]={
            'checkpoint':entries[name]['path'],
            'head_sha256':entries[name]['manifest']['head_sha256'],
            'dev':{'arms':dev[name], 'oracle_ceilings':dev_oracle[name],
                   'paired_vs_whole_linear':{
                       arm:_changes(dev_records[name][CONTROL],dev_records[name][arm])
                       for arm in ARM_NAMES if arm!=CONTROL},
                   'oracle_deficits':_oracle_deficits(dev_oracle[name]),
                   'published_dev_thresholds_observed_only':{
                       'exact_sets_at_least_80pct':dev[name][selected]['object_exact_rate']>=.80,
                       'each_required_kind_recall_at_least_82pct':all(
                           dev[name][selected]['by_kind'].get(kind,{}).get('recall',0)>=.82
                           for kind in REQUIRED_KINDS)},
                   'selected':selected,'selected_metrics':dev[name][selected],
                   'overflow_cases':dict(dev_overflow[name])},
            'historical_challenge':{'arms':challenge[name],
                   'oracle_ceilings':challenge_oracle[name],
                   'paired_vs_whole_linear':{
                       arm:_changes(challenge_records[name][CONTROL],challenge_records[name][arm])
                       for arm in ARM_NAMES if arm!=CONTROL},
                   'oracle_deficits':_oracle_deficits(challenge_oracle[name]),
                   'selected':selected,'selected_metrics':challenge[name][selected],
                   'overflow_cases':dict(challenge_overflow[name])},
            'certification_authorized':False}
    report={'schema':SCHEMA,
            'status':'FROZEN_ADMISSION_EXPERIMENT_NOT_CERTIFICATION',
            'design':{'control':CONTROL,'arms':[s.__dict__ for s in ARMS],
                'dev_cases':len(cohorts['dev']),
                'historical_challenge_cases':len(cohorts['challenge']),
                'selection':'DEV_ONLY_VS_WHOLE_LINEAR_STRICT_NONREGRESSION',
                'prior_challenge_previously_exposed':True,
                'oracle_ceiling_gold_assisted_not_inference':True,
                'no_threshold_or_parameter_fit_to_challenge':True,
                'same_frozen_qwen_capture_for_both_heads':True},
            'lineage':{'dataset_sha256':dataset_sha,
                'decoder_reference_sha256':prior_hash,
                'checkpoint_sha256':{n:e['manifest']['head_sha256'] for n,e in entries.items()},
                'source_adapter_sha256':ref['source_adapter_sha256']},
            'models':results,'weights_modified':False,'training_performed':False,
            'production_promotion':False,'g2_unlocked':False,
            'certification_authorized':False,'sealed_test_used':False}
    out=Path(output);out.mkdir(parents=True,exist_ok=False)
    (out/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf-8')
    with (out/'cases.jsonl').open('x',encoding='utf-8') as f:
        for item in dev_details+challenge_details:
            f.write(json.dumps(item,sort_keys=True,ensure_ascii=False)+'\n')
    print('G1_ADMISSION_RESULT '+json.dumps({name:{
        'selected':data['dev']['selected'],
        'dev_exact':data['dev']['selected_metrics']['exact_prompts'],
        'dev_f1':round(data['dev']['selected_metrics']['f1'],5),
        'dev_actor_recall':round(data['dev']['selected_metrics']['by_kind']['actor']['recall'],5),
        'challenge_exact':data['historical_challenge']['selected_metrics']['exact_prompts'],
        'challenge_f1':round(data['historical_challenge']['selected_metrics']['f1'],5),
        'control_dev_exact':data['dev']['arms'][CONTROL]['exact_prompts']}
        for name,data in results.items()},sort_keys=True),flush=True)
    return report


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dataset',required=True)
    ap.add_argument('--legacy',required=True)
    ap.add_argument('--cutover',required=True)
    ap.add_argument('--decoder-reference',required=True)
    ap.add_argument('--output',required=True)
    ap.add_argument('--device',default='cuda')
    ap.add_argument('--max-length',type=int,default=2048)
    run(**vars(ap.parse_args(argv)))


if __name__=='__main__':main()
