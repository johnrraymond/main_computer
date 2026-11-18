"""Frozen G1 Oracle-Fabric versus legacy decoder/candidate recoverability bake-off.

Read-only, immutable research report. Never updates model weights, decoders,
certificates, G2 gates, or production. Dev alone selects a deployment-blind
*research* decoder per checkpoint; previously inspected challenge is reported
only afterward, and is NOT fresh certification. Sealed test is never opened.

All proposed decisions depend exclusively on frozen logits and validity masks.
Oracle-only ceilings use labeled spans AFTER every inference prediction and are
strictly excluded from decoder choices and deployment metrics.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

import torch

from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS
from tools.nanojev_gamer_v1_graph_head import decode_neural_spans, token_span_char_bounds
from tools.nanojev_gamer_v1_g1_decoder_frob import (
    _candidate_scores, decode_whole_span_scores, _char_set,
)
from tools.nanojev_gamer_v1_graph_comparison import _stats

SCHEMA = 'nanojev.gamer.graph.g1.oracle-fabric-frozen-decoder-cutover.v1'
# Identical arm catalogue, including predeclared typed scores, for BOTH heads.
# None of these settings is fitted on challenge or fetched from gold labels.
ARMS = (
    'global', 'whole_linear', 'whole_sqrt', 'whole_linear_penalty_1p5',
    'type_actor_guard', 'type_action_whole', 'type_joint_whole',
)
REQUIRED_KINDS = ('actor', 'action', 'quantity', 'outcome')


def sha256(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):
            h.update(block)
    return h.hexdigest()


def _checked_preflight(dataset, legacy, cutover, output):
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    from tools import nanojev_gamer_v1_backbone_first as backbone
    if Path(output).exists():
        raise FileExistsError('G1_ORACLE_DECODER_IMMUTABLE_OUTPUT')
    dataset=Path(dataset)
    out=Path(output).resolve()
    for protected in (Path(legacy).resolve(),Path(cutover).resolve()):
        if out==protected or protected in out.parents:
            raise PermissionError('G1_ORACLE_DECODER_OUTPUT_INSIDE_PROTECTED_CHECKPOINT')
    ds_sha=sha256(dataset)
    entries={}
    for name,location in (('legacy',legacy),('cutover',cutover)):
        location=Path(location)
        manifest=json.loads((location/cur.MANIFEST).read_text(encoding='utf-8'))
        if (manifest.get('schema')!=cur.SCHEMA or
                manifest.get('status')!='TRAINED_NOT_CERTIFIED' or
                manifest.get('stage')!='G1' or
                manifest.get('dataset_sha256')!=ds_sha or
                manifest.get('production_promotion') is True or
                not isinstance(manifest.get('head_sha256'),str) or
                sha256(location/cur.HEAD_FILE)!=manifest['head_sha256']):
            raise ValueError('G1_ORACLE_DECODER_CHECKPOINT_LINEAGE:'+name)
        if bool(manifest.get('deterministic_fabric'))!=(name=='cutover'):
            raise ValueError('G1_ORACLE_DECODER_LEGACY_CUTOVER_CONFUSION:'+name)
        if manifest.get('source_adapter_sha256')!=backbone.adapter_fingerprint(manifest['source_adapter']):
            raise ValueError('G1_ORACLE_DECODER_SOURCE_ADAPTER_DRIFT:'+name)
        entries[name]={'path':str(location.resolve()),'manifest':manifest}
    a,b=(entries[n]['manifest'] for n in ('legacy','cutover'))
    if (a.get('source_adapter_sha256')!=b.get('source_adapter_sha256') or
            a.get('model_id')!=b.get('model_id')):
        raise ValueError('G1_ORACLE_DECODER_SOURCE_NOT_COMPARABLE')
    if a['head_sha256']==b['head_sha256']:
        raise ValueError('G1_ORACLE_DECODER_IDENTICAL_CHECKPOINTS')
    rows=cur.load_rows(dataset)
    dev=[r for r in rows if r['split']=='dev']
    challenge=[r for r in rows if r['split']=='challenge']
    if not dev or not challenge:
        raise ValueError('G1_ORACLE_DECODER_MISSING_COHORT')
    return entries,ds_sha,{'dev':dev,'challenge':challenge}


def _decode_type_scores(by_end, *, actor_weight, action_power,
                        action_weight, object_penalty=0.0):
    """Typed, label-blind global interval DP from unchanged neural candidates."""
    if (not math.isfinite(actor_weight) or actor_weight<=0 or
            not math.isfinite(action_power) or not 0<=action_power<=1.5 or
            not math.isfinite(action_weight) or action_weight<=0 or
            not math.isfinite(object_penalty) or object_penalty<0):
        raise ValueError('G1_ORACLE_DECODER_BAD_TYPED_PARAMETERS')
    size=len(by_end)-1
    dp=[0.]*(size+1);back=[None]*(size+1)
    actor=SPAN_KINDS.index('actor')+1
    action=SPAN_KINDS.index('action')+1
    for end in range(1,size+1):
        score=dp[end-1];chosen=(end-1,None)
        for first,offset,label,margin in by_end[end]:
            if not (0<=first<end and end-first==offset+1 and
                    1<=label<=len(SPAN_KINDS) and math.isfinite(margin) and margin>0):
                raise ValueError('G1_ORACLE_DECODER_BAD_CANDIDATE')
            width=offset+1
            if label==actor:
                value=margin*width*actor_weight-object_penalty
            elif label==action:
                value=margin*width**action_power*action_weight-object_penalty
            else:
                value=margin*width-object_penalty
            candidate=dp[first]+value
            if value>0 and candidate>score+1e-8:
                score=candidate;chosen=(first,(first,offset,label))
        dp[end]=score;back[end]=chosen
    result=[];at=size
    while at>0:
        first,item=back[at]
        if item is not None:
            start,offset,label=item
            result.append({'kind':SPAN_KINDS[label-1],
                           'token_first':start,'token_last':start+offset})
        at=first
    if len(result)>128:
        raise ValueError('GAMER_GRAPH_TOO_MANY_PREDICTED_OBJECTS')
    return sorted(result,key=lambda s:s['token_first'])


def _all_decoders(logits,valid):
    """Every option is computed without access to prompts, gold or labels."""
    by_end=_candidate_scores(logits,valid,confidence=.5)
    baseline=decode_neural_spans(logits,valid,confidence=.5)
    control=decode_whole_span_scores(by_end,length_power=0,
                                     per_object_penalty=0)
    if baseline!=control:
        raise ValueError('G1_ORACLE_DECODER_BASELINE_PARITY')
    return {
        'global':baseline,
        'whole_linear':decode_whole_span_scores(by_end,length_power=1,
                                                per_object_penalty=0),
        'whole_sqrt':decode_whole_span_scores(by_end,length_power=.5,
                                              per_object_penalty=0),
        'whole_linear_penalty_1p5':decode_whole_span_scores(
            by_end,length_power=1,per_object_penalty=1.5),
        'type_actor_guard':_decode_type_scores(by_end,actor_weight=1.15,
                                                 action_power=1,action_weight=1),
        'type_action_whole':_decode_type_scores(by_end,actor_weight=1,
                                                  action_power=1.25,action_weight=1),
        'type_joint_whole':_decode_type_scores(by_end,actor_weight=1.10,
                                                  action_power=1.2,action_weight=1.05,
                                                  object_penalty=.5),
    }


def _oracle_ceilings(logits,valid,prepared,aligned,eligible):
    """Three ORACLE-ASSISTED bounds; NEVER ordinary inference predictions.

    eligible: gold exists among unmodified eligible neural typed proposals.
    nonnull_type: valid full span's strongest non-null type matches gold,
        ignoring null dominance and confidence.
    forced_type: valid and correctly char-aligned gold token span even when
        logits prefer another class. This is an architectural ceiling only.
    """
    choices={'oracle_eligible':set(), 'oracle_nonnull_type':set(),
             'oracle_forced_type':set()}
    # Eligible proposals are computed from frozen logits independently of gold.
    scored={(a,a+offset,SPAN_KINDS[k-1])
            for candidates in eligible for a,offset,k,margin in candidates}
    valid=valid.detach().cpu().bool()
    logits=logits.detach().float().cpu()
    for span in aligned['spans']:
        a,b=span['token_first'],span['token_last']
        kind=span['kind']
        bounds=token_span_char_bounds(span,prepared)
        if bounds is None or a<0 or b<a or b-a>=valid.shape[1] or a>=valid.shape[0]:
            continue
        if not bool(valid[a,b-a]):
            continue
        item=(*bounds,kind)
        choices['oracle_forced_type'].add(item)
        class_index=int(logits[a,b-a,1:].argmax().item())+1
        if SPAN_KINDS[class_index-1]!=kind:
            continue
        choices['oracle_nonnull_type'].add(item)
        if (a,b,kind) in scored:
            choices['oracle_eligible'].add(item)
    return choices


def _summarize(records):
    result=_stats(records)
    result['incorrect_objects']=result['expected']-result['correct']
    result['false_positive_objects']=result['predicted']-result['correct']
    return result


def _case_metrics(pred,gold):
    return {'correct':len(pred&gold),'expected':len(gold),'predicted':len(pred),
            'exact':pred==gold}


def _safe_select(metrics):
    """A stage-agnostic development-only nonregression selector.

    Never selects from any oracle-assisted upper bound. The baseline stays
    selected unless a decoder improves exact sets or F1 AND preserves actor
    and action recall, total F1, and exact sets on development.
    """
    base=metrics['global']
    survivors=['global']
    for name in ARMS[1:]:
        m=metrics[name]
        if (m['exact_prompts']>=base['exact_prompts'] and m['f1']>=base['f1'] and
                all(m['by_kind'].get(k,{}).get('recall',0)>=
                    base['by_kind'].get(k,{}).get('recall',0) for k in REQUIRED_KINDS) and
                (m['exact_prompts']>base['exact_prompts'] or m['f1']>base['f1']+1e-9)):
            survivors.append(name)
    return max(survivors,key=lambda x:(metrics[x]['exact_prompts'],metrics[x]['f1'],
                                       -ARMS.index(x)))


def _per_case_row(*,name,split,index,row,pred,gold,baseline,oracle):
    def fmt(objects):return [{'char_span':[a,b],'kind':k} for a,b,k in sorted(objects)]
    return {'checkpoint':name,'split':split,'index':index,
            'family':row['family'],
            'prompt_sha256':hashlib.sha256(row['prompt'].encode('utf-8')).hexdigest(),
            'gold_count':len(gold),'baseline_correct':len(baseline&gold),
            'baseline_exact':baseline==gold,
            'oracle_eligible':len(oracle['oracle_eligible']),
            'oracle_nonnull_type':len(oracle['oracle_nonnull_type']),
            'oracle_forced_type':len(oracle['oracle_forced_type']),
            'baseline_missing_by_kind':dict(Counter(k for a,b,k in gold-baseline)),
            'eligible_oracle_missing':fmt(gold-oracle['oracle_eligible']),
            'nonnull_oracle_missing':fmt(gold-oracle['oracle_nonnull_type']),
            'arms':{arm:{'exact':proposed==gold,'correct':len(proposed&gold),
                         'predicted':len(proposed),
                         'recovered':fmt((gold-baseline)&proposed),
                         'lost':fmt((gold&baseline)-proposed),
                         'new_false':fmt((proposed-gold)-(baseline-gold))}
                    for arm,proposed in pred.items()}}


def _evaluate_split(*,split,rows,entries,heads,model,tok,device,max_length):
    """One frozen Qwen capture per prompt; two heads consume identical hidden."""
    from tools import nanojev_gamer_v1_backbone_first as backbone
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    records={name:{arm:[] for arm in ARMS} for name in heads}
    ceilings={name:{n:[] for n in ('oracle_eligible','oracle_nonnull_type',
                                  'oracle_forced_type')} for name in heads}
    details=[]
    for index,row in enumerate(rows,1):
        prepared=backbone.prepare_prompt(tok,row['prompt'],max_input_tokens=max_length)
        with torch.inference_mode():
            hidden=backbone.frozen_context(model,prepared,device=device)
            predictions={name:head(hidden.to(device=device,dtype=torch.float32),
                                   eligible=prepared['eligible'].to(device),active_stage='G1')
                         for name,head in heads.items()}
            decoded={}
            for name,prediction in predictions.items():
                try:
                    decoded[name]=_all_decoders(prediction['span_logits'],
                                                prediction['span_valid'])
                except ValueError as exc:
                    if str(exc)=='GAMER_GRAPH_TOO_MANY_PREDICTED_OBJECTS':
                        raise ValueError('G1_ORACLE_DECODER_TOO_MANY_CANDIDATES:'+name) from exc
                    raise
        # The gold/oracle read occurs strictly AFTER all neural predictions.
        aligned=cur.aligned_labels(prepared,row)
        gold={(x['start'],x['end'],x['kind']) for x in row['object_spans']}
        for name,prediction in predictions.items():
            found={arm:_char_set(spans,prepared) for arm,spans in decoded[name].items()}
            oracle=_oracle_ceilings(prediction['span_logits'],prediction['span_valid'],
                                    prepared,aligned,_candidate_scores(
                                        prediction['span_logits'],prediction['span_valid']))
            if any(not value<=gold for value in oracle.values()):
                raise ValueError('G1_ORACLE_DECODER_GOLD_ALIGNMENT_MISMATCH')
            if not (oracle['oracle_eligible']<=oracle['oracle_nonnull_type']<=
                    oracle['oracle_forced_type']):
                raise ValueError('G1_ORACLE_DECODER_CEILING_NOT_NESTED')
            for arm,pred in found.items():records[name][arm].append((pred,gold))
            for key,subset in oracle.items():ceilings[name][key].append((subset,gold))
            details.append(_per_case_row(name=name,split=split,index=index,row=row,
                       pred=found,gold=gold,baseline=found['global'],oracle=oracle))
        if index==1 or index%10==0 or index==len(rows):
            print(f'G1_ORACLE_DECODER_{split.upper()} {index}/{len(rows)} '
                  'legacy+cutover frozen',flush=True)
    return ({name:{key:_summarize(vals) for key,vals in result.items()}
             for name,result in records.items()},
            {name:{key:_summarize(vals) for key,vals in result.items()}
             for name,result in ceilings.items()}, details)


def run(*,dataset,legacy,cutover,output,device='cuda',max_length=2048):
    """Read-only replay. Challenge is opened only after dev choices are fixed."""
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    from tools import nanojev_gamer_v1_backbone_first as backbone
    if max_length<1:
        raise ValueError('G1_ORACLE_DECODER_BAD_MAX_LENGTH')
    entries,dataset_sha,cohorts=_checked_preflight(dataset,legacy,cutover,output)
    heads={name:cur._load_checkpoint(info['path'],device=device)[0].eval()
           for name,info in entries.items()}
    ref=entries['legacy']['manifest']
    model,tok=backbone.model_components(ref['model_id'],ref['source_adapter'],device)
    dev,dev_oracle,dev_details=_evaluate_split(split='dev',rows=cohorts['dev'],
                        entries=entries,heads=heads,model=model,tok=tok,
                        device=device,max_length=max_length)
    decisions={name:_safe_select(dev[name]) for name in entries}
    print('G1_ORACLE_DECODER_DEV_SELECTION '+json.dumps(decisions,sort_keys=True),flush=True)
    # No code may update decisions after this line.
    challenge,challenge_oracle,challenge_details=_evaluate_split(
                        split='challenge',rows=cohorts['challenge'],entries=entries,
                        heads=heads,model=model,tok=tok,device=device,
                        max_length=max_length)
    if sha256(dataset)!=dataset_sha:
        raise ValueError('G1_ORACLE_DECODER_DATASET_CHANGED_DURING_RUN')
    if backbone.adapter_fingerprint(ref['source_adapter'])!=ref['source_adapter_sha256']:
        raise ValueError('G1_ORACLE_DECODER_SOURCE_CHANGED_DURING_RUN')
    for name,info in entries.items():
        if sha256(Path(info['path'])/cur.HEAD_FILE)!=info['manifest']['head_sha256']:
            raise ValueError('G1_ORACLE_DECODER_CHECKPOINT_CHANGED_DURING_RUN')
    summary={}
    for name in entries:
        chosen=decisions[name]
        b=dev[name]['global'];s=dev[name][chosen]
        summary[name]={'checkpoint':entries[name]['path'],
               'head_sha256':entries[name]['manifest']['head_sha256'],
               'selected_decoder':chosen,
               'dev':{'models':dev[name],'oracle_assisted_ceilings':dev_oracle[name],
                      'selected':s,'baseline':b},
               'historical_challenge':{
                   'models':challenge[name],
                   'oracle_assisted_ceilings':challenge_oracle[name],
                   'selected':challenge[name][chosen],
                   'baseline':challenge[name]['global']},
               'dev_delta_exact_sets':s['exact_prompts']-b['exact_prompts'],
               'dev_delta_f1':s['f1']-b['f1'],
               'challenge_delta_exact_sets':challenge[name][chosen]['exact_prompts']-
                                            challenge[name]['global']['exact_prompts'],
               'challenge_delta_f1':challenge[name][chosen]['f1']-
                                    challenge[name]['global']['f1']}
    report={'schema':SCHEMA,
         'status':'FROZEN_DECODER_DEV_EXPERIMENT_NOT_CERTIFICATION',
         'design':{'dev_cases':len(cohorts['dev']),
                   'historical_challenge_cases':len(cohorts['challenge']),
                   'decoder_arms':list(ARMS),
                   'selection':'DEV_ONLY_NONREGRESSION; no challenge selection',
                   'same_frozen_qwen_capture_for_both_heads':True,
                   'oracle_ceiling_is_gold_assisted_not_deployable':True,
                   'challenge_is_historically_exposed_not_fresh_or_sealed':True},
         'lineage':{'dataset_sha256':dataset_sha,
                    'model_id':ref['model_id'],
                    'source_adapter_sha256':ref['source_adapter_sha256'],
                    'checkpoints':{name:entries[name]['manifest']['head_sha256']
                                   for name in entries}},
         'arms':summary,
         'training_performed':False,'weights_modified':False,
         'production_promotion':False,'g2_unlocked':False,
         'certification_authorized':False,'sealed_test_used':False}
    output=Path(output)
    output.mkdir(parents=True,exist_ok=False)
    (output/'report.json').write_text(json.dumps(report,sort_keys=True,indent=2,
                                            allow_nan=False)+'\n',encoding='utf-8')
    with (output/'cases.jsonl').open('x',encoding='utf-8') as f:
        for row in dev_details+challenge_details:
            f.write(json.dumps(row,sort_keys=True,ensure_ascii=False)+'\n')
    print('G1_ORACLE_DECODER_RESULT '+json.dumps({
        name:{'selected':x['selected_decoder'],
              'dev_exact':x['dev']['selected']['exact_prompts'],
              'dev_f1':round(x['dev']['selected']['f1'],5),
              'challenge_exact':x['historical_challenge']['selected']['exact_prompts'],
              'challenge_f1':round(x['historical_challenge']['selected']['f1'],5),
              'oracle_eligible_dev_exact':x['dev']['oracle_assisted_ceilings'][
                    'oracle_eligible']['exact_prompts']} for name,x in summary.items()
         },sort_keys=True),flush=True)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',required=True)
    parser.add_argument('--legacy',required=True)
    parser.add_argument('--cutover',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--device',default='cuda')
    parser.add_argument('--max-length',type=int,default=2048)
    args=parser.parse_args(argv)
    run(**vars(args))


if __name__=='__main__':main()
