"""G1-0072: frozen action-span and complete-object reconstruction bake-off.

Both legacy and Oracle-Fabric G1 heads remain frozen. The 0071 report defines a
DIFFERENT preselected control for each head; both are reproduced exactly before
comparison. This script only changes inference-time decoding and reports
post-prediction oracle diagnostics. Development selects; the previously exposed
historical challenge is measured later and is not certification evidence.
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
from tools.nanojev_gamer_v1_g1_decoder_frob import _char_set
from tools.nanojev_gamer_v1_g1_oracle_decoder_cutover_frob import (
    _checked_preflight, _oracle_ceilings, _summarize, sha256,
)
from tools import nanojev_gamer_v1_g1_admission_bakeoff_0070 as admission
from tools import nanojev_gamer_v1_g1_actor_rescue_bakeoff_0071 as actor

SCHEMA = 'nanojev.gamer.graph.g1.complete-object-bakeoff-0072.v1'
CONTROL = 'g1_0071_selected_control'
ACTION = SPAN_KINDS.index('action') + 1
ACTOR = SPAN_KINDS.index('actor') + 1
REQUIRED_KINDS = ('actor', 'action', 'quantity', 'outcome')


@dataclass(frozen=True)
class Arm:
    name: str
    method: str = 'dp'  # dp | merge | control
    action_power: float = 1.0
    action_bonus: float = 0.0
    action_penalty: float = 0.0
    rescue_deficit: float = 0.0
    conditional_min: float = 0.0
    runnerup_gap: float = 0.0
    merge_ratio: float = 0.0


ARMS = (
    Arm(CONTROL, 'control'),
    Arm('action_width_1p10', action_power=1.10),
    Arm('action_width_1p25', action_power=1.25),
    Arm('action_width_1p40', action_power=1.40),
    Arm('action_bonus_0p25', action_bonus=.25),
    Arm('action_bonus_0p50', action_bonus=.50),
    Arm('action_count_penalty_0p50', action_penalty=.50),
    Arm('action_count_penalty_1p00', action_penalty=1.),
    Arm('action_width_1p25_penalty_0p50', action_power=1.25,action_penalty=.50),
    Arm('action_rescue_1p25_cond0p60',rescue_deficit=1.25,conditional_min=.60),
    Arm('action_rescue_1p50_cond0p55',rescue_deficit=1.50,conditional_min=.55),
    Arm('action_rescue_2p00_cond0p50',rescue_deficit=2.,conditional_min=.50),
    Arm('action_runnerup_gap0p25_bonus0p25',runnerup_gap=.25,action_bonus=.25),
    Arm('action_runnerup_gap0p50_bonus0p50',runnerup_gap=.50,action_bonus=.50),
    Arm('action_joint_width_1p25_rescue1p5',action_power=1.25,
        rescue_deficit=1.50,conditional_min=.55),
    Arm('action_joint_bonus0p25_rescue1p5',action_bonus=.25,
        rescue_deficit=1.50,conditional_min=.55),
    Arm('merge_adjacent_actions_0p65',method='merge',merge_ratio=.65,
        rescue_deficit=.50,conditional_min=.60),
    Arm('merge_adjacent_actions_0p85',method='merge',merge_ratio=.85,
        rescue_deficit=.50,conditional_min=.60),
)
ARM_NAMES = tuple(a.name for a in ARMS)


def _check_specs():
    if len(ARM_NAMES)!=len(set(ARM_NAMES)) or ARMS[0]!=Arm(CONTROL,'control'):
        raise ValueError('G1_0072_ARM_CATALOGUE')
    for a in ARMS:
        if (a.method not in ('control','dp','merge') or
            not all(math.isfinite(v) for v in (a.action_power,a.action_bonus,
                a.action_penalty,a.rescue_deficit,a.conditional_min,
                a.runnerup_gap,a.merge_ratio)) or
            not 1<=a.action_power<=1.5 or not 0<=a.action_bonus<=1 or
            not 0<=a.action_penalty<=2 or not 0<=a.rescue_deficit<=2 or
            not 0<=a.conditional_min<=1 or not 0<=a.runnerup_gap<=1 or
            not 0<=a.merge_ratio<=1):
            raise ValueError('G1_0072_BAD_ARM')


def _prior_scored_candidates(size,pool,previous_arm):
    """Recreate G1-0071's exact preselected per-model candidate scores."""
    spec=next((x for x in actor.ARMS if x.name==previous_arm),None)
    if spec is None or spec.method not in ('runnerup_joint','actor_prior','control'):
        raise ValueError('G1_0072_UNSUPPORTED_PREVIOUS_CONTROL')
    if spec.method=='runnerup_joint':
        return actor._runnerup_joint(size,pool,spec.bonus,spec.gap,spec.actor_cond_min)
    base=admission._score_candidates(size,pool,actor.JOINT)
    return actor._with_actor_bonus(base,spec.bonus) if spec.method=='actor_prior' else base


def _add_action_options(by_end,pool,arm):
    """Add only inference-visible ACTION candidates; never consult gold."""
    result=[list(group) for group in by_end]
    seen={(start,offset,k) for row in result for start,offset,k,_ in row}
    for start,offset,k,margin,_,p_cond,alt,alt_margin in pool:
        if arm.rescue_deficit and k==ACTION:
            option=(start,offset,ACTION)
            if (option not in seen and margin>=-arm.rescue_deficit and
                p_cond>=arm.conditional_min):
                score=margin+arm.rescue_deficit+.25
                if score>0:
                    result[start+offset+1].append((*option,float(score)))
                    seen.add(option)
        if arm.runnerup_gap and k==ACTOR and alt==ACTION:
            difference=margin-alt_margin
            option=(start,offset,ACTION)
            if (option not in seen and 0<=difference<=arm.runnerup_gap and
                alt_margin>=-arm.runnerup_gap):
                score=alt_margin+1.25
                if score>0:
                    result[start+offset+1].append((*option,float(score)))
                    seen.add(option)
    return result


def _decode_typed(by_end,arm):
    """Exact interval DP with action-specific geometry; other kinds unchanged."""
    n=len(by_end)-1
    dp=[0.0]*(n+1)
    back=[(0,None)]*(n+1)
    for end in range(1,n+1):
        best=dp[end-1]
        chosen=(end-1,None)
        for start,offset,k,score in by_end[end]:
            if (not isinstance(start,int) or not isinstance(offset,int) or
                not 0<=start<end or end!=start+offset+1 or
                not 1<=k<=len(SPAN_KINDS) or
                not math.isfinite(score) or score<=0):
                raise ValueError('G1_0072_INVALID_CANDIDATE')
            width=offset+1
            if k==ACTION:
                gain=(score+arm.action_bonus)*(width**arm.action_power)-arm.action_penalty
            else:
                gain=score*width
            value=dp[start]+gain
            if gain>0 and value>best+1e-8:
                best=value
                chosen=(start,(start,offset,k))
        dp[end]=best
        back[end]=chosen
    spans=[]
    at=n
    while at:
        first,found=back[at]
        if found is not None:
            start,offset,k=found
            spans.append({'kind':SPAN_KINDS[k-1],
                          'token_first':start,'token_last':start+offset})
        at=first
    if len(spans)>128:
        raise ValueError('GAMER_GRAPH_TOO_MANY_PREDICTED_OBJECTS')
    return sorted(spans,key=lambda x:x['token_first'])


def _merge_actions(baseline,pool,arm):
    """Replace adjacent ACTION fragments with a full scored ACTION candidate.

    Gold never influences the choice; each replacement must span precisely the
    union of two adjacent, already selected actions and pass a neural score test.
    """
    all_actions={}
    for first,offset,k,margin,_,p_cond,_,_ in pool:
        if k==ACTION and margin>=-arm.rescue_deficit and p_cond>=arm.conditional_min:
            all_actions[(first,first+offset)]=(margin,p_cond)
    current=[dict(span) for span in baseline]
    index=0
    while index+1<len(current):
        left,right=current[index:index+2]
        if (left['kind']==right['kind']=='action' and
            left['token_last']+1==right['token_first']):
            first,last=left['token_first'],right['token_last']
            scored=all_actions.get((first,last))
            if scored:
                full_margin=scored[0]
                s1=all_actions.get((left['token_first'],left['token_last']))
                s2=all_actions.get((right['token_first'],right['token_last']))
                # Compare whole-span weighted score with existing fragments.
                if s1 and s2 and full_margin>0 and (
                    full_margin*(last-first+1) >= arm.merge_ratio*(
                        max(s1[0],0)*(left['token_last']-first+1)+
                        max(s2[0],0)*(last-right['token_first']+1))):
                    current[index:index+2]=[{'kind':'action',
                        'token_first':first,'token_last':last}]
                    continue
        index+=1
    return current


def _decode(logits,valid,selected_prior):
    """Inference-only interface: logits, valid span mask, and fixed arm ID."""
    previous,_=actor._decode(logits,valid)
    if selected_prior not in previous or previous[selected_prior] is None:
        raise ValueError('G1_0072_PRIOR_DECODER_UNAVAILABLE')
    baseline=previous[selected_prior]
    size,pool=admission._pool(logits,valid)
    candidates=_prior_scored_candidates(size,pool,selected_prior)
    # Mandatory exact bit-for-bit baseline prediction parity.
    reconstructed=_decode_typed(candidates,ARMS[0])
    if reconstructed!=baseline:
        raise ValueError('G1_0072_PRIOR_DECODER_PARITY')
    result={CONTROL:baseline}
    for arm in ARMS[1:]:
        if arm.method=='merge':
            result[arm.name]=_merge_actions(baseline,pool,arm)
        else:
            enriched=_add_action_options(candidates,pool,arm)
            try:
                result[arm.name]=_decode_typed(enriched,arm)
            except ValueError as exc:
                if str(exc)!='GAMER_GRAPH_TOO_MANY_PREDICTED_OBJECTS':
                    raise
                result[arm.name]=None
    if tuple(result)!=ARM_NAMES:
        raise ValueError('G1_0072_MISSING_ARM')
    return result,candidates


def _require_actor_reference(path,entries,dataset_hash,counts):
    folder=Path(path).resolve()
    report=json.loads((folder/'report.json').read_text(encoding='utf8'))
    if (report.get('schema')!=actor.SCHEMA or
        report.get('status')!='EXPLORATORY_FROZEN_ACTOR_RESCUE_NOT_CERTIFICATION' or
        report.get('lineage',{}).get('dataset_sha256')!=dataset_hash or
        report.get('lineage',{}).get('checkpoint_sha256')!={
            model:entry['manifest']['head_sha256'] for model,entry in entries.items()} or
        report.get('design',{}).get('dev_cases')!=counts['dev'] or
        report.get('design',{}).get('historical_challenge_cases')!=counts['challenge'] or
        any(report.get(flag) is not False for flag in (
            'weights_modified','training_performed','production_promotion',
            'g2_unlocked','sealed_test_used','certification_authorized'))):
        raise ValueError('G1_0072_ACTOR_REFERENCE_MISMATCH')
    selected={}
    for model in entries:
        rows=report['models'][model]
        name=rows['dev']['selected']
        if (name not in actor.ARM_NAMES or name not in rows['dev']['arms'] or
            name not in rows['historical_challenge']['arms'] or
            rows['historical_challenge']['selected']!=name):
            raise ValueError('G1_0072_PREVIOUS_SELECTION_MISMATCH')
        if name not in ('runnerup_joint_gap0p50_bonus0p50','actor_prior_0p25'):
            raise ValueError('G1_0072_UNEXPECTED_CONTROL')
        selected[model]=name
    return report,sha256(folder/'report.json'),selected


def _reference_parity(prior,split,metrics):
    group='dev' if split=='dev' else 'historical_challenge'
    for model in ('legacy','cutover'):
        expected=prior['models'][model][group]['selected_metrics']
        observed=metrics[model][CONTROL]
        for key in ('correct','predicted','expected','exact_prompts','false_positive_objects'):
            if observed[key]!=expected[key]:
                raise ValueError(f'G1_0072_PRIOR_METRIC_DRIFT:{model}:{key}')
        for kind in REQUIRED_KINDS:
            x=observed['by_kind'].get(kind)
            y=expected['by_kind'].get(kind)
            if (x is None)!=(y is None):
                raise ValueError(f'G1_0072_PRIOR_KIND_MISSING:{model}:{kind}')
            if x is not None:
                for key in ('correct','total','recall'):
                    if x[key]!=y[key]:
                        raise ValueError(f'G1_0072_PRIOR_KIND_DRIFT:{model}:{kind}:{key}')


def _select_dev(metrics,overflow):
    """Nonregression first; exact-set improvement is required to select a new arm."""
    base=metrics[CONTROL]
    viable=[CONTROL]
    for name in ARM_NAMES[1:]:
        m=metrics[name]
        if overflow.get(name,0):continue
        if (m['exact_prompts']>base['exact_prompts'] and
            m['f1']>=base['f1']-1e-12 and
            m['precision']>=base['precision']-.005 and
            all(m['by_kind'].get(kind,{}).get('recall',
                0.0 if kind in base['by_kind'] else 1.0)+1e-12>=
                base['by_kind'].get(kind,{}).get('recall',1.0)
                for kind in REQUIRED_KINDS)):
            viable.append(name)
    return max(viable,key=lambda name:(metrics[name]['exact_prompts'],
        metrics[name]['f1'],metrics[name]['precision'],-ARM_NAMES.index(name)))


def _failure_map(gold,predicted,oracle):
    """Post-inference, oracle-assisted attribution; never used by decoders."""
    missing=gold-predicted
    extra=predicted-gold
    reasons=Counter()
    action_overlap=Counter()
    for span in missing:
        first,last,kind=span
        if span in oracle['oracle_eligible']:
            reason='eligible_but_not_selected'
        elif span in oracle['oracle_nonnull_type']:
            reason='correct_nonnull_type_not_eligible'
        elif span in oracle['oracle_forced_type']:
            reason='wrong_top_nonnull_type'
        else:
            reason='not_supported_or_aligned'
        reasons[(kind,reason)]+=1
        if kind=='action':
            overlapping=[p for p in predicted if p[2]=='action' and
                         p[0]<last and first<p[1]]
            if len(overlapping)>=2:action_overlap['split_multiple_actions']+=1
            elif overlapping:
                p=overlapping[0]
                if p[0]>=first and p[1]<=last:
                    action_overlap['shortened_action']+=1
                elif p[0]<=first and p[1]>=last:
                    action_overlap['extended_action']+=1
                else:action_overlap['shifted_action']+=1
            else:action_overlap['no_overlapping_action']+=1
    return {'missing_by_kind_reason':{k:{reason:n for (kind,reason),n in reasons.items()
                    if kind==k} for k in SPAN_KINDS if any(kind==k for kind,_ in reasons)},
            'action_span_shape':dict(action_overlap),
            'missing':len(missing),'false_objects':len(extra),
            'minimum_object_edits':len(missing)+len(extra),
            'one_edit_from_exact':len(missing)+len(extra)==1}


def _evaluate(*,split,rows,heads,model,tok,device,max_length,prior_selected):
    from tools import nanojev_gamer_v1_backbone_first as backbone
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    records={name:{arm_name:[] for arm_name in ARM_NAMES} for name in heads}
    ceilings={name:{c:[] for c in ('oracle_eligible','oracle_nonnull_type','oracle_forced_type')}
              for name in heads}
    details=[]
    overflow={name:Counter() for name in heads}
    control_failures={name:Counter() for name in heads}
    control_shapes={name:Counter() for name in heads}
    for index,row in enumerate(rows,1):
        prepared=backbone.prepare_prompt(tok,row['prompt'],max_input_tokens=max_length)
        with torch.inference_mode():
            hidden=backbone.frozen_context(model,prepared,device=device)
            outputs={name:head(hidden.to(device=device,dtype=torch.float32),
                eligible=prepared['eligible'].to(device),active_stage='G1')
                for name,head in heads.items()}
            variants={name:_decode(output['span_logits'],output['span_valid'],
                prior_selected[name]) for name,output in outputs.items()}
        gold={(s['start'],s['end'],s['kind']) for s in row['object_spans']}
        aligned=cur.aligned_labels(prepared,row)
        for name,output in outputs.items():
            variants_for_head,raw_candidates=variants[name]
            chars={n:(_char_set(s,prepared) if s is not None else set())
                for n,s in variants_for_head.items()}
            for arm_name,pred in variants_for_head.items():
                if pred is None:overflow[name][arm_name]+=1
                records[name][arm_name].append((chars[arm_name],gold))
            oracle=_oracle_ceilings(output['span_logits'],output['span_valid'],
                prepared,aligned,raw_candidates)
            if not (oracle['oracle_eligible']<=oracle['oracle_nonnull_type']<=
                oracle['oracle_forced_type']<=gold):
                raise ValueError('G1_0072_ORACLE_CEILING_NESTING')
            for level,subset in oracle.items():
                ceilings[name][level].append((subset,gold))
            failures=_failure_map(gold,chars[CONTROL],oracle)
            for kind,counts in failures['missing_by_kind_reason'].items():
                control_failures[name].update({f'{kind}:{r}':n for r,n in counts.items()})
            control_shapes[name].update(failures['action_span_shape'])
            changes={}
            for arm_name in ARM_NAMES[1:]:
                prediction=chars[arm_name]
                before=chars[CONTROL]
                changes[arm_name]={
                    'recovered_correct':[list(s) for s in sorted((prediction&gold)-(before&gold))],
                    'lost_correct':[list(s) for s in sorted((before&gold)-(prediction&gold))],
                    'introduced_false':[list(s) for s in sorted((prediction-gold)-(before-gold))],
                    'removed_false':[list(s) for s in sorted((before-gold)-(prediction-gold))],
                    'exact':prediction==gold,
                    'overflow':variants_for_head[arm_name] is None}
            details.append({'split':split,'model':name,'index':index,
                'family':row['family'],
                'prompt_sha256':hashlib.sha256(row['prompt'].encode('utf8')).hexdigest(),
                'gold_count':len(gold),'control_exact':chars[CONTROL]==gold,
                'control_failure_attribution':failures,'oracle_ceilings_exact':{
                    k:subset==gold for k,subset in oracle.items()},
                'changes':changes})
        if index==1 or index%10==0 or index==len(rows):
            print(f'G1_COMPLETE_{split.upper()} {index}/{len(rows)} legacy+cutover frozen',
                  flush=True)
    metrics={m:{arm_name:_summarize(rec) for arm_name,rec in arms.items()}
             for m,arms in records.items()}
    ceilings_summaries={m:{level:_summarize(rec) for level,rec in arms.items()}
                        for m,arms in ceilings.items()}
    return records,metrics,ceilings_summaries,details,overflow,control_failures,control_shapes


def run(*,dataset,legacy,cutover,actor_reference,output,device='cuda',max_length=2048):
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    from tools import nanojev_gamer_v1_backbone_first as backbone
    _check_specs()
    if Path(output).exists():raise FileExistsError('G1_0072_IMMUTABLE_OUTPUT')
    if max_length<1:raise ValueError('G1_0072_BAD_MAX_LENGTH')
    entries,dataset_sha,cohorts=_checked_preflight(dataset,legacy,cutover,output)
    out=Path(output).resolve();ref=Path(actor_reference).resolve()
    if out==ref or ref in out.parents:
        raise PermissionError('G1_0072_OUTPUT_INSIDE_ACTOR_REFERENCE')
    previous,reference_sha,selected_prior=_require_actor_reference(ref,entries,
        dataset_sha,{split:len(rows) for split,rows in cohorts.items()})
    heads={name:cur._load_checkpoint(info['path'],device=device)[0].eval()
           for name,info in entries.items()}
    manifest=entries['legacy']['manifest']
    model,tok=backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    dev_records,dev,dev_ceilings,dev_cases,dev_overflow,dev_failures,dev_shapes=_evaluate(
        split='dev',rows=cohorts['dev'],heads=heads,model=model,tok=tok,
        device=device,max_length=max_length,prior_selected=selected_prior)
    _reference_parity(previous,'dev',dev)
    chosen={name:_select_dev(arms,dev_overflow[name]) for name,arms in dev.items()}
    print('G1_COMPLETE_DEV_SELECTION '+json.dumps(chosen,sort_keys=True),flush=True)
    historical_records,historical,historical_ceilings,historical_cases,historical_overflow,history_failures,history_shapes=_evaluate(
        split='challenge',rows=cohorts['challenge'],heads=heads,model=model,tok=tok,
        device=device,max_length=max_length,prior_selected=selected_prior)
    _reference_parity(previous,'challenge',historical)
    # Recheck immutable input artifacts before writing the research report.
    if sha256(dataset)!=dataset_sha or sha256(ref/'report.json')!=reference_sha:
        raise ValueError('G1_0072_SOURCE_DRIFT')
    if backbone.adapter_fingerprint(manifest['source_adapter'])!=manifest['source_adapter_sha256']:
        raise ValueError('G1_0072_ADAPTER_DRIFT')
    for name,entry in entries.items():
        if sha256(Path(entry['path'])/cur.HEAD_FILE)!=entry['manifest']['head_sha256']:
            raise ValueError('G1_0072_CHECKPOINT_DRIFT')
    models={}
    for name in entries:
        name_chosen=chosen[name]
        models[name]={'head_sha256':entries[name]['manifest']['head_sha256'],
            '0071_selected_control':selected_prior[name],
            'dev':{
                'selected':name_chosen,'control_metrics':dev[name][CONTROL],
                'selected_metrics':dev[name][name_chosen],'arms':dev[name],
                'oracle_ceilings':dev_ceilings[name],
                'control_miss_causes':dict(dev_failures[name]),
                'control_action_span_shapes':dict(dev_shapes[name]),
                'overflow_cases':dict(dev_overflow[name]),
                'changes_vs_control':{arm_name:admission._changes(
                    dev_records[name][CONTROL],dev_records[name][arm_name])
                    for arm_name in ARM_NAMES[1:]},
                'observed_targets_not_certification':{
                    'exact_sets_ge_80pct':dev[name][name_chosen]['object_exact_rate']>=.80,
                    'actor_recall_ge_82pct':dev[name][name_chosen]['by_kind']['actor']['recall']>=.82}},
            'historical_challenge':{
                'selected':name_chosen,'control_metrics':historical[name][CONTROL],
                'selected_metrics':historical[name][name_chosen],'arms':historical[name],
                'oracle_ceilings':historical_ceilings[name],
                'control_miss_causes':dict(history_failures[name]),
                'control_action_span_shapes':dict(history_shapes[name]),
                'overflow_cases':dict(historical_overflow[name]),
                'changes_vs_control':{arm_name:admission._changes(
                    historical_records[name][CONTROL],historical_records[name][arm_name])
                    for arm_name in ARM_NAMES[1:]}}}
    report={'schema':SCHEMA,'status':'FROZEN_COMPLETE_OBJECT_BAKEOFF_NOT_CERTIFIED',
        'design':{'arms':[a.__dict__ for a in ARMS],
            'control':CONTROL,'per_model_control':selected_prior,
            'dev_cases':len(cohorts['dev']),
            'historical_challenge_cases':len(cohorts['challenge']),
            'selected_using':'DEV_ONLY_EXACT_SETS_WITH_STRICT_NONREGRESSION',
            'challenge_previously_exposed':True,
            'oracle_only_after_inference':True,'independent_certification':False},
        'lineage':{'dataset_sha256':dataset_sha,
            'actor_reference_sha256':reference_sha,
            'checkpoint_sha256':{name:e['manifest']['head_sha256']
                for name,e in entries.items()},
            'source_adapter_sha256':manifest['source_adapter_sha256']},
        'models':models,'weights_modified':False,'training_performed':False,
        'production_promotion':False,'g2_unlocked':False,
        'sealed_test_used':False,'certification_authorized':False}
    target=Path(output)
    target.mkdir(parents=True,exist_ok=False)
    (target/'report.json').write_text(json.dumps(report,sort_keys=True,indent=2,
        allow_nan=False)+'\n',encoding='utf8')
    with (target/'cases.jsonl').open('x',encoding='utf8') as f:
        for row in dev_cases+historical_cases:
            f.write(json.dumps(row,sort_keys=True,ensure_ascii=False)+'\n')
    print('G1_COMPLETE_RESULT '+json.dumps({name:{
        'control_exact':models[name]['dev']['control_metrics']['exact_prompts'],
        'selected':models[name]['dev']['selected'],
        'dev_exact':models[name]['dev']['selected_metrics']['exact_prompts'],
        'dev_f1':round(models[name]['dev']['selected_metrics']['f1'],5),
        'actor_recall':round(models[name]['dev']['selected_metrics']['by_kind']['actor']['recall'],5),
        'historical_challenge_exact':models[name]['historical_challenge']['selected_metrics']['exact_prompts']}
        for name in models},sort_keys=True),flush=True)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',required=True)
    parser.add_argument('--legacy',required=True)
    parser.add_argument('--cutover',required=True)
    parser.add_argument('--actor-reference',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--device',default='cuda')
    parser.add_argument('--max-length',type=int,default=2048)
    run(**vars(parser.parse_args(argv)))


if __name__=='__main__':main()
