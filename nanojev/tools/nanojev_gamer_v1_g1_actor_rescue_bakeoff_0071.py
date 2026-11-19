"""G1-0071: frozen, actor-targeted rescue versus complete-object preservation.

Research-only follow-up to 0070's selected joint_actor_action_1p0.  The
checkpoint, dataset, Qwen adapter and previous admission report are immutable
inputs.  Every inference arm consumes frozen logits/masks ONLY; gold is read
strictly after decoding for evaluation and oracle-only attribution.  Dev
selects before historical challenge is evaluated.  No learning or promotion.
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
from tools.nanojev_gamer_v1_g1_decoder_frob import _char_set, decode_whole_span_scores
from tools.nanojev_gamer_v1_g1_oracle_decoder_cutover_frob import (
    _checked_preflight, _oracle_ceilings, _summarize, sha256,
)
from tools import nanojev_gamer_v1_g1_admission_bakeoff_0070 as admission

SCHEMA = 'nanojev.gamer.graph.g1.oracle-fabric-actor-rescue-0071.v1'
CONTROL = 'joint_actor_action_1p0'
PRIOR_ABLATION = 'joint_runnerup_actor_prior_0p75'
REQUIRED_KINDS = ('actor','action','quantity','outcome')
ACTOR = SPAN_KINDS.index('actor') + 1
ACTION = SPAN_KINDS.index('action') + 1
JOINT = next(s for s in admission.ARMS if s.name == CONTROL)


@dataclass(frozen=True)
class Arm:
    name: str
    method: str
    # Prespecified strength or uncertainty cut, never fitted to case labels.
    bonus: float = 0.0
    gap: float = 0.0
    actor_cond_min: float = 0.0
    maximum_added: int = 0


ARMS = (
    Arm(CONTROL,'control'),
    Arm(PRIOR_ABLATION,'prior'),
    Arm('actor_prior_0p10','actor_prior',bonus=.10),
    Arm('actor_prior_0p25','actor_prior',bonus=.25),
    Arm('actor_prior_0p50','actor_prior',bonus=.50),
    Arm('runnerup_joint_gap0p25_bonus0p25','runnerup_joint',bonus=.25,gap=.25,actor_cond_min=.22),
    Arm('runnerup_joint_gap0p50_bonus0p50','runnerup_joint',bonus=.50,gap=.50,actor_cond_min=.20),
    Arm('runnerup_joint_gap0p75_bonus0p75','runnerup_joint',bonus=.75,gap=.75,actor_cond_min=.18),
    Arm('guarded_swap_gap0p25','guarded_swap',gap=.25,actor_cond_min=.25,maximum_added=1),
    Arm('guarded_swap_gap0p50','guarded_swap',gap=.50,actor_cond_min=.20,maximum_added=1),
    Arm('guarded_swap_gap0p75','guarded_swap',gap=.75,actor_cond_min=.18,maximum_added=1),
    Arm('gap_insert_actor_0p50','gap_insert',gap=.50,actor_cond_min=.50,maximum_added=1),
    Arm('gap_insert_actor_1p00','gap_insert',gap=1.,actor_cond_min=.40,maximum_added=1),
    Arm('gap_insert_actor_two_1p00','gap_insert',gap=1.,actor_cond_min=.40,maximum_added=2),
    Arm('actor_admit_cond0p50','actor_admit',actor_cond_min=.50),
    Arm('actor_admit_cond0p40','actor_admit',actor_cond_min=.40),
)
ARM_NAMES = tuple(x.name for x in ARMS)


def _check_specs():
    if (len(ARM_NAMES) != len(set(ARM_NAMES)) or
            ARMS[0] != Arm(CONTROL,'control') or
            ARMS[1] != Arm(PRIOR_ABLATION,'prior')):
        raise ValueError('G1_ACTOR_ARM_CATALOGUE')
    for s in ARMS:
        if (s.method not in ('control','prior','actor_prior','runnerup_joint',
                             'guarded_swap','gap_insert','actor_admit') or
                not all(math.isfinite(v) for v in (s.bonus,s.gap,s.actor_cond_min)) or
                not 0 <= s.bonus <= 2 or not 0 <= s.gap <= 2 or
                not 0 <= s.actor_cond_min <= 1 or
                s.maximum_added not in (0,1,2)):
            raise ValueError('G1_ACTOR_ARM_PARAMETERS')


def _span(item):
    return (item['token_first'], item['token_last'], item['kind'])


def _nonoverlap(left,right):
    return left[1] < right[0] or right[1] < left[0]


def _actor_options(size,pool, *, gap, conditional_min, include_top=True):
    """Frozen potential actor spans, including runner-up type candidates."""
    choices=[]
    for start,offset,label,margin,p_raw,p_cond,alternate,alternate_margin in pool:
        end=start+offset
        if label==ACTOR and include_top:
            if margin < -gap or p_cond < conditional_min:
                continue
            choices.append((start,end,margin,p_cond,'top'))
        elif label==ACTION and alternate==ACTOR:
            difference=margin-alternate_margin
            actor_cond=p_cond*math.exp(-difference)
            if difference>gap or alternate_margin < -gap or actor_cond<conditional_min:
                continue
            choices.append((start,end,alternate_margin,actor_cond,'runnerup'))
    return choices


def _with_actor_bonus(candidates, bonus):
    return [[(a,o,k,score+(bonus if k==ACTOR else 0))
             for a,o,k,score in group] for group in candidates]


def _with_actor_nonnull_relaxation(size,pool,conditional_min):
    """Only relax actor null rescue, retaining all baseline joint candidates."""
    candidates=[list(x) for x in admission._score_candidates(size,pool,JOINT)]
    present={(a,o,k) for row in candidates for a,o,k,_ in row}
    for start,offset,label,margin,_,p_cond,_,_ in pool:
        if (label!=ACTOR or (start,offset,ACTOR) in present or
                margin < -1.0 or p_cond < conditional_min):
            continue
        score=margin+1.0+.25  # exactly JOINT's offset+rank score
        if score>0:
            candidates[start+offset+1].append((start,offset,ACTOR,score))
    return candidates


def _runnerup_joint(size,pool,bonus,gap,cond_min):
    by_end=[list(x) for x in admission._score_candidates(size,pool,JOINT)]
    existing={(a,o,k) for row in by_end for a,o,k,_ in row}
    for start,end,actor_margin,actor_cond,origin in _actor_options(
            size,pool,gap=gap,conditional_min=cond_min,include_top=False):
        option=(start,end-start,ACTOR)
        if option in existing: continue
        # JOINT's baseline rescue offset and typed rank, plus a measured prior.
        score=actor_margin+1.+.25+bonus
        if score>0:
            by_end[end+1].append((*option,score))
            existing.add(option)
    return by_end


def _guarded_swap(current,pool,spec):
    """Retype at most one selected action, leaving all span boundaries fixed."""
    proposals={}
    for start,end,margin,cond,origin in _actor_options(
            max((p[0] for p in pool),default=-1)+1,pool,
            gap=spec.gap,conditional_min=spec.actor_cond_min,include_top=False):
        if origin=='runnerup': proposals[(start,end)]=(margin,cond)
    matches=[]
    for i,span in enumerate(current):
        if span['kind']!='action':continue
        key=_span(span)[:2]
        if key in proposals:
            margin,cond=proposals[key]
            # Preference is the closest inferred non-null tie.  No gold used.
            matches.append((-cond,-margin,i))
    chosen={item[2] for item in sorted(matches)[:spec.maximum_added]}
    return [{**s,'kind':'actor'} if i in chosen else dict(s)
            for i,s in enumerate(current)]


def _gap_insert(current,size,pool,spec):
    """Add actor candidates only in completely unoccupied token intervals."""
    occupied=[_span(s) for s in current]
    choices=[]
    for start,end,margin,cond,origin in _actor_options(
            size,pool,gap=spec.gap,conditional_min=spec.actor_cond_min):
        candidate=(start,end,'actor')
        if candidate in occupied:continue
        if all(_nonoverlap(candidate,p) for p in occupied):
            choices.append((-(cond),-margin,start,end,origin))
    additions=[]
    for _,_,start,end,_ in sorted(choices):
        candidate=(start,end,'actor')
        if all(_nonoverlap(candidate,other) for other in additions):
            additions.append(candidate)
            if len(additions)==spec.maximum_added:break
    return sorted([dict(s) for s in current]+[
        {'kind':kind,'token_first':start,'token_last':end}
        for start,end,kind in additions],key=lambda s:s['token_first'])


def _decode(logits,valid):
    """No oracle, prompts, gold, targets, or trainable parameters are accepted."""
    prior,_=admission._decode_all(logits,valid)
    base=prior[CONTROL]
    rival=prior[PRIOR_ABLATION]
    if base is None or rival is None:
        raise ValueError('G1_ACTOR_PREVIOUS_BASELINE_OVERFLOW')
    size,pool=admission._pool(logits,valid)
    scores=admission._score_candidates(size,pool,JOINT)
    predictions={CONTROL:base,PRIOR_ABLATION:rival}
    for arm in ARMS[2:]:
        if arm.method=='actor_prior':
            candidates=_with_actor_bonus(scores,arm.bonus)
        elif arm.method=='runnerup_joint':
            candidates=_runnerup_joint(size,pool,arm.bonus,arm.gap,arm.actor_cond_min)
        elif arm.method=='actor_admit':
            candidates=_with_actor_nonnull_relaxation(size,pool,arm.actor_cond_min)
        elif arm.method=='guarded_swap':
            predictions[arm.name]=_guarded_swap(base,pool,arm)
            continue
        elif arm.method=='gap_insert':
            predictions[arm.name]=_gap_insert(base,size,pool,arm)
            continue
        else:raise ValueError('G1_ACTOR_UNKNOWN_METHOD')
        try:
            predictions[arm.name]=decode_whole_span_scores(
                candidates,length_power=1,per_object_penalty=0)
        except ValueError as exc:
            if str(exc)!='GAMER_GRAPH_TOO_MANY_PREDICTED_OBJECTS':raise
            predictions[arm.name]=None
    if tuple(predictions)!=ARM_NAMES:
        raise ValueError('G1_ACTOR_OUTPUT_CATALOGUE_MISMATCH')
    return predictions,scores


def _require_admission_reference(path,entries,dataset_hash,split_sizes):
    """Fail closed if the previous selected decoder or evidence changed."""
    loc=Path(path)
    doc=json.loads((loc/'report.json').read_text(encoding='utf-8'))
    if (doc.get('schema')!=admission.SCHEMA or
        doc.get('status')!='FROZEN_ADMISSION_EXPERIMENT_NOT_CERTIFICATION' or
        doc.get('lineage',{}).get('dataset_sha256')!=dataset_hash or
        doc.get('lineage',{}).get('checkpoint_sha256')!={
            key:e['manifest']['head_sha256'] for key,e in entries.items()} or
        doc.get('design',{}).get('dev_cases')!=split_sizes['dev'] or
        doc.get('design',{}).get('historical_challenge_cases')!=split_sizes['challenge'] or
        doc.get('design',{}).get('control')!=admission.CONTROL or
        any(doc.get('models',{}).get(key,{}).get('dev',{}).get('selected')!=CONTROL
            for key in entries) or
        any(doc.get(flag) is not False for flag in ('weights_modified','training_performed',
             'production_promotion','g2_unlocked','sealed_test_used','certification_authorized'))):
        raise ValueError('G1_ACTOR_ADMISSION_REFERENCE_MISMATCH')
    for model in entries:
        for split in ('dev','historical_challenge'):
            recorded=doc['models'][model][split]
            if not (CONTROL in recorded['arms'] and PRIOR_ABLATION in recorded['arms']):
                raise ValueError('G1_ACTOR_PREVIOUS_ARMS_MISSING')
    return doc,sha256(loc/'report.json')


def _reference_parity(reference,split,stats):
    source='dev' if split=='dev' else 'historical_challenge'
    for model in ('cutover','legacy'):
        for arm in (CONTROL,PRIOR_ABLATION):
            recorded=reference['models'][model][source]['arms'][arm]
            observed=stats[model][arm]
            for key in ('correct','predicted','expected','exact_prompts'):
                if observed[key]!=recorded[key]:
                    raise ValueError(f'G1_ACTOR_PRIOR_METRIC_DRIFT:{model}:{arm}:{key}')
            for kind in REQUIRED_KINDS:
                a=observed['by_kind'].get(kind)
                b=recorded['by_kind'].get(kind)
                if a is None and b is None:continue
                if a is None or b is None:
                    raise ValueError(f'G1_ACTOR_PRIOR_KIND_DRIFT:{model}:{arm}:{kind}:missing')
                for key in ('correct','total','recall'):
                    if a[key]!=b[key]:
                        raise ValueError(f'G1_ACTOR_PRIOR_KIND_DRIFT:{model}:{arm}:{kind}:{key}')


def _actor_attribution(logits,valid,prepared,aligned,predicted,gold):
    """Oracle-only attribution; never called until predictions are complete."""
    from tools.nanojev_gamer_v1_graph_head import token_span_char_bounds
    _,pool=admission._pool(logits,valid)
    by_key={(a,a+o):(label,margin,p_raw,p_cond,alt,alt_margin)
            for a,o,label,margin,p_raw,p_cond,alt,alt_margin in pool}
    result=Counter()
    for item in aligned['spans']:
        if item['kind']!='actor':continue
        bounds=token_span_char_bounds(item,prepared)
        if bounds is None or (*bounds,'actor') not in gold:
            continue
        if (*bounds,'actor') in predicted:continue
        start,last=item['token_first'],item['token_last']
        found=by_key.get((start,last))
        if found is None:
            result['missing_or_invalid_candidate']+=1
            continue
        label,margin,p_raw,p_cond,alt,alt_margin=found
        if label==ACTOR and margin>0 and p_raw>=.5:
            result['eligible_actor_lost_in_selection']+=1
        elif label==ACTOR:
            result['top_actor_but_rejected_by_admission']+=1
        elif label==ACTION and alt==ACTOR:
            result['actor_runnerup_to_action']+=1
        else:
            result['actor_not_top_or_runnerup']+=1
    return result


def _select_dev(metrics,overflows):
    """Predeclared selection, on dev only, constrained by every major metric."""
    base=metrics[CONTROL]
    passing=[]
    for name in ARM_NAMES[1:]:
        item=metrics[name]
        if overflows.get(name,0):continue
        if (item['exact_prompts']>=base['exact_prompts'] and
            item['f1']>=base['f1']-1e-12 and
            item['precision']>=base['precision']-.005 and
            all(item['by_kind'].get(kind,{}).get('recall',1.0)+1e-12>=
                base['by_kind'].get(kind,{}).get('recall',1.0)
                for kind in REQUIRED_KINDS) and
            item['by_kind']['actor']['recall']>=.82 and
            (item['by_kind']['actor']['recall']>base['by_kind']['actor']['recall']+1e-12 or
             item['exact_prompts']>base['exact_prompts'])):
            passing.append(name)
    if not passing:return CONTROL
    return max(passing,key=lambda name:(metrics[name]['exact_prompts'],
        metrics[name]['by_kind']['actor']['recall'],metrics[name]['f1'],
        metrics[name]['precision'],-ARM_NAMES.index(name)))


def _evaluate(*,split,rows,heads,model,tok,device,max_length):
    from tools import nanojev_gamer_v1_backbone_first as backbone
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    reports={name:{arm:[] for arm in ARM_NAMES} for name in heads}
    ceilings={name:{oracle:[] for oracle in ('oracle_eligible','oracle_nonnull_type',
                                                'oracle_forced_type')} for name in heads}
    diagnostics={name:Counter() for name in heads}
    overflow={name:Counter() for name in heads}
    details=[]
    for index,row in enumerate(rows,1):
        prepared=backbone.prepare_prompt(tok,row['prompt'],max_input_tokens=max_length)
        with torch.inference_mode():
            hidden=backbone.frozen_context(model,prepared,device=device)
            outputs={name:head(hidden.to(device=device,dtype=torch.float32),
                        eligible=prepared['eligible'].to(device),active_stage='G1')
                     for name,head in heads.items()}
            decoded={}
            eligible={}
            for name,output in outputs.items():
                decoded[name],eligible[name]=_decode(output['span_logits'],output['span_valid'])
        # No gold is materialized or passed to the decoding function.
        gold={(s['start'],s['end'],s['kind']) for s in row['object_spans']}
        aligned=cur.aligned_labels(prepared,row)
        for name,output in outputs.items():
            char={arm:(_char_set(v,prepared) if v is not None else set())
                  for arm,v in decoded[name].items()}
            for arm,value in decoded[name].items():
                if value is None:overflow[name][arm]+=1
                reports[name][arm].append((char[arm],gold))
            diagnostics[name].update(_actor_attribution(output['span_logits'],
                output['span_valid'],prepared,aligned,char[CONTROL],gold))
            oracle=_oracle_ceilings(output['span_logits'],output['span_valid'],prepared,
                                    aligned,eligible[name])
            if not (oracle['oracle_eligible']<=oracle['oracle_nonnull_type']<=
                    oracle['oracle_forced_type']<=gold):
                raise ValueError('G1_ACTOR_ORACLE_ALIGNMENT')
            for key,subset in oracle.items():ceilings[name][key].append((subset,gold))
            base=char[CONTROL]
            differences={}
            for arm in ARM_NAMES[1:]:
                val=char[arm]
                differences[arm]={
                    'recovered_correct':[list(x) for x in sorted((val&gold)-(base&gold))],
                    'lost_correct':[list(x) for x in sorted((base&gold)-(val&gold))],
                    'introduced_false':[list(x) for x in sorted((val-gold)-(base-gold))],
                    'removed_false':[list(x) for x in sorted((base-gold)-(val-gold))],
                    'exact':val==gold,'overflow':decoded[name][arm] is None}
            details.append({'split':split,'model':name,'index':index,
                'family':row['family'],
                'prompt_sha256':hashlib.sha256(row['prompt'].encode('utf8')).hexdigest(),
                'gold_count':len(gold),'control_exact':base==gold,
                'control_actor_correct':sum(kind=='actor' for *_,kind in base&gold),
                'changes':differences})
        if index==1 or index%10==0 or index==len(rows):
            print(f'G1_ACTOR_{split.upper()} {index}/{len(rows)} legacy+cutover frozen',
                  flush=True)
    return (reports,{name:{arm:_summarize(records) for arm,records in variants.items()}
                     for name,variants in reports.items()},
            {name:{key:_summarize(records) for key,records in variants.items()}
             for name,variants in ceilings.items()},details,overflow,diagnostics)


def run(*,dataset,legacy,cutover,admission_reference,output,device='cuda',max_length=2048):
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    from tools import nanojev_gamer_v1_backbone_first as backbone
    _check_specs()
    if max_length<1:raise ValueError('G1_ACTOR_BAD_MAX_LENGTH')
    entries,dataset_hash,cohorts=_checked_preflight(dataset,legacy,cutover,output)
    out=Path(output).resolve();ref=Path(admission_reference).resolve()
    if out==ref or ref in out.parents:
        raise PermissionError('G1_ACTOR_OUTPUT_INSIDE_ADMISSION_REFERENCE')
    prior,prior_hash=_require_admission_reference(ref,entries,dataset_hash,
                         {k:len(v) for k,v in cohorts.items()})
    heads={name:cur._load_checkpoint(info['path'],device=device)[0].eval()
           for name,info in entries.items()}
    manifest=entries['legacy']['manifest']
    model,tok=backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    dev_records,dev,dev_oracle,dev_cases,dev_overflow,dev_diag=_evaluate(
        split='dev',rows=cohorts['dev'],heads=heads,model=model,tok=tok,
        device=device,max_length=max_length)
    _reference_parity(prior,'dev',dev)
    selected={name:_select_dev(summary,dev_overflow[name]) for name,summary in dev.items()}
    print('G1_ACTOR_DEV_SELECTION '+json.dumps(selected,sort_keys=True),flush=True)
    # The previously exposed challenge must not affect selection.
    challenge_records,challenge,ch_oracle,ch_cases,ch_overflow,ch_diag=_evaluate(
        split='challenge',rows=cohorts['challenge'],heads=heads,model=model,tok=tok,
        device=device,max_length=max_length)
    _reference_parity(prior,'challenge',challenge)
    if sha256(dataset)!=dataset_hash or sha256(ref/'report.json')!=prior_hash:
        raise ValueError('G1_ACTOR_REFERENCE_DRIFT')
    if backbone.adapter_fingerprint(manifest['source_adapter'])!=manifest['source_adapter_sha256']:
        raise ValueError('G1_ACTOR_ADAPTER_DRIFT')
    for name,info in entries.items():
        if sha256(Path(info['path'])/cur.HEAD_FILE)!=info['manifest']['head_sha256']:
            raise ValueError('G1_ACTOR_HEAD_DRIFT')
    models={}
    for name in entries:
        winner=selected[name]
        models[name]={'head_sha256':entries[name]['manifest']['head_sha256'],
            'dev':{'arms':dev[name], 'selected':winner,
                'selected_metrics':dev[name][winner],
                'control_metrics':dev[name][CONTROL],
                'oracle_ceilings':dev_oracle[name],
                'remaining_actor_miss_attribution':dict(dev_diag[name]),
                'overflow_cases':dict(dev_overflow[name]),
                'changes_vs_joint_control':{
                    arm:admission._changes(dev_records[name][CONTROL],dev_records[name][arm])
                    for arm in ARM_NAMES[1:]},
                'development_targets_observed_only':{
                    'actor_recall_at_least_82pct':dev[name][winner]['by_kind']['actor']['recall']>=.82,
                    'exact_sets_at_least_80pct':dev[name][winner]['object_exact_rate']>=.80}},
            'historical_challenge':{'arms':challenge[name],
                'selected':winner,'selected_metrics':challenge[name][winner],
                'control_metrics':challenge[name][CONTROL],
                'oracle_ceilings':ch_oracle[name],
                'remaining_actor_miss_attribution':dict(ch_diag[name]),
                'overflow_cases':dict(ch_overflow[name]),
                'changes_vs_joint_control':{
                    arm:admission._changes(challenge_records[name][CONTROL],
                                           challenge_records[name][arm])
                    for arm in ARM_NAMES[1:]}}}
    report={'schema':SCHEMA,'status':'EXPLORATORY_FROZEN_ACTOR_RESCUE_NOT_CERTIFICATION',
        'design':{'control':CONTROL,'prior_actor_ablation':PRIOR_ABLATION,
            'arms':[x.__dict__ for x in ARMS],
            'dev_cases':len(cohorts['dev']),
            'historical_challenge_cases':len(cohorts['challenge']),
            'selected_using':'DEV_ONLY_ACTOR_RECALL_82PCT_AND_NONREGRESSION',
            'challenge_previously_exposed':True,
            'oracle_after_predictions_only':True},
        'lineage':{'dataset_sha256':dataset_hash,
            'admission_reference_sha256':prior_hash,
            'checkpoint_sha256':{name:e['manifest']['head_sha256']
                                 for name,e in entries.items()},
            'source_adapter_sha256':manifest['source_adapter_sha256']},
        'models':models,
        'weights_modified':False,'training_performed':False,
        'production_promotion':False,'g2_unlocked':False,
        'certification_authorized':False,'sealed_test_used':False}
    target=Path(output)
    target.mkdir(parents=True,exist_ok=False)
    (target/'report.json').write_text(json.dumps(report,sort_keys=True,indent=2,
                          allow_nan=False)+'\n',encoding='utf8')
    with (target/'cases.jsonl').open('x',encoding='utf8') as f:
        for row in dev_cases+ch_cases:
            f.write(json.dumps(row,sort_keys=True,ensure_ascii=False)+'\n')
    print('G1_ACTOR_RESULT '+json.dumps({name:{'selected':models[name]['dev']['selected'],
        'control_exact':models[name]['dev']['control_metrics']['exact_prompts'],
        'dev_exact':models[name]['dev']['selected_metrics']['exact_prompts'],
        'dev_actor_recall':round(models[name]['dev']['selected_metrics']['by_kind']['actor']['recall'],5),
        'dev_f1':round(models[name]['dev']['selected_metrics']['f1'],5),
        'challenge_exact':models[name]['historical_challenge']['selected_metrics']['exact_prompts']}
        for name in models},sort_keys=True),flush=True)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',required=True)
    parser.add_argument('--legacy',required=True)
    parser.add_argument('--cutover',required=True)
    parser.add_argument('--admission-reference',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--device',default='cuda')
    parser.add_argument('--max-length',type=int,default=2048)
    run(**vars(parser.parse_args(argv)))


if __name__=='__main__':main()
