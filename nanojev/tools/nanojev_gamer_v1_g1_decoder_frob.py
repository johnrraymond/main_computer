"""Frozen-score G1 decoder experiment, not a production decoder or stage gate.

All alternatives are specified before reading the dev gold. A single Qwen/G1
inference supplies identical logits to the current global decoder and each
whole-span-aware weighted-interval variant. Gold is used only to assess outputs.
No trained weights, tokenizer settings, graph stages, or production rules change.
"""
from __future__ import annotations

from collections import Counter
import json
import math
from pathlib import Path

import torch

from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS
from tools.nanojev_gamer_v1_graph_head import (
    SPAN_CLASS_COUNT, decode_neural_spans, token_span_char_bounds,
)

SCHEMA = 'nanojev.gamer.graph.g1.frozen-whole-span-decoder-dev.v1'
EXPECTED_G1_0007_HEAD = '114150ae0ef351a2bd365b63c6a7fd70ac53d973d673577c5a910f6b8e1a7c90'
# Declared in code, never selected from labels during decoding.
VARIANTS = (
    ('per_object_1p5', 0.0, 1.5),
    ('per_object_3', 0.0, 3.0),
    ('length_sqrt', 0.5, 0.0),
    ('length_sqrt_penalty_1p5', 0.5, 1.5),
    ('length_sqrt_penalty_3', 0.5, 3.0),
    ('length_linear', 1.0, 0.0),
    ('length_linear_penalty_1p5', 1.0, 1.5),
    ('length_linear_penalty_3', 1.0, 3.0),
)


def _candidate_scores(span_logits, span_valid, *, confidence=0.50):
    """Build neural options once; labels and prompt strings are not inputs."""
    if (span_logits.ndim != 3 or span_logits.shape[-1] != SPAN_CLASS_COUNT or
            span_valid.shape != span_logits.shape[:2]):
        raise ValueError('GAMER_G1_FROB_LOGIT_SHAPE')
    if not 0 <= confidence <= 1:
        raise ValueError('GAMER_G1_FROB_CONFIDENCE')
    with torch.no_grad():
        logits = span_logits.detach().float().cpu()
        valid = span_valid.detach().bool().cpu().numpy()
        margins = (logits[...,1:] - logits[...,0:1]).numpy()
        probabilities = logits.softmax(-1)[...,1:].numpy()
        count, max_width = valid.shape
        by_end = [[] for _ in range(count+1)]
        for start in range(count):
            for offset in range(min(max_width,count-start)):
                if not valid[start,offset]:
                    continue
                k=int(margins[start,offset].argmax())
                margin=float(margins[start,offset,k])
                if margin <= 0 or probabilities[start,offset,k] < confidence:
                    continue
                end=start+offset+1
                by_end[end].append((start,offset,k+1,margin))
    return by_end


def decode_whole_span_scores(by_end, *, length_power, per_object_penalty):
    """Exact interval selection, using only frozen class-vs-null margins.

    Objective: sum[ margin(span) * span_width**length_power - penalty ].
    The existing per-span eligibility condition has already been applied.
    A length reward can favor complete multi-token objects over their fragments;
    an object-count penalty makes two fragments cost more than one whole object.
    Neither option is guaranteed to improve dev accuracy; this is a read-only test.
    """
    if (not isinstance(length_power,(int,float)) or not math.isfinite(length_power) or
            length_power < 0 or length_power > 1 or
            not isinstance(per_object_penalty,(int,float)) or
            not math.isfinite(per_object_penalty) or per_object_penalty < 0):
        raise ValueError('GAMER_G1_FROB_UNSAFE_SCORING_PARAMETERS')
    size=len(by_end)-1
    dp=[0.0]*(size+1)
    back=[None]*(size+1)
    for end in range(1,size+1):
        best=dp[end-1]
        selected=(end-1,None)
        for first,offset,klass,margin in by_end[end]:
            score=margin*(offset+1)**length_power - per_object_penalty
            candidate=dp[first]+score
            if score > 0 and candidate > best+1e-8:
                best=candidate
                selected=(first,(first,offset,klass))
        dp[end]=best
        back[end]=selected
    spans=[]
    end=size
    while end>0:
        first,chosen=back[end]
        if chosen is not None:
            start,offset,klass=chosen
            spans.append({'kind':SPAN_KINDS[klass-1],
                          'token_first':start,'token_last':start+offset})
        end=first
    if len(spans)>128:
        raise ValueError('GAMER_GRAPH_TOO_MANY_PREDICTED_OBJECTS')
    return sorted(spans,key=lambda item:item['token_first'])


def _char_set(spans, prepared):
    result=set()
    for span in spans:
        char_bounds=token_span_char_bounds(span,prepared)
        if char_bounds is not None:
            result.add((*char_bounds,span['kind']))
    return result


def _surface(row, triple):
    a,b,k=triple
    return {'span':[a,b],'kind':k,'surface':row['prompt'][a:b]}


def _reference_misses(diagnosis, *, head_sha, dataset_sha, case_count):
    path=Path(diagnosis)
    manifest=json.loads((path/'summary.json').read_text(encoding='utf-8'))
    if (manifest.get('checkpoint_head_sha256')!=head_sha or
        manifest.get('dataset_sha256')!=dataset_sha or
        manifest.get('split')!='dev' or
        manifest.get('cases')!=case_count or
        manifest.get('limited') or
        manifest.get('status')!='DIAGNOSTIC_ONLY_NOT_A_GATE'):
        raise ValueError('GAMER_G1_FROB_REFERENCE_LINEAGE')
    results={}
    with (path/'misses.jsonl').open(encoding='utf-8') as f:
        for line in f:
            if not line.strip():continue
            rec=json.loads(line)
            idx=rec['index']
            span=rec.get('gold_span')
            if (not isinstance(idx,int) or not 1<=idx<=case_count or
                    not isinstance(span,list) or len(span)!=2 or
                    rec.get('kind') not in SPAN_KINDS):
                raise ValueError('GAMER_G1_FROB_REFERENCE_MISSES')
            key=(idx,span[0],span[1],rec['kind'])
            if key in results:
                raise ValueError('GAMER_G1_FROB_DUPLICATE_REFERENCE_MISS')
            results[key]=rec['reason']
    if sum(manifest['miss_reasons'].values())!=len(results):
        raise ValueError('GAMER_G1_FROB_REFERENCE_COUNT')
    return results


def _summaries(records):
    from tools.nanojev_gamer_v1_graph_comparison import _stats
    return _stats(records)


def run(*,dataset,checkpoint,baseline_diagnosis,output,device='cuda',
        max_length=2048,expected_head_sha256=EXPECTED_G1_0007_HEAD):
    """Compare predeclared decoders on dev only, with an immutable report."""
    from tools import nanojev_gamer_v1_backbone_first as backbone
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    from tools.nanojev_gamer_v1_graph_diagnostic import attribute_case

    dataset=Path(dataset);output=Path(output)
    if output.exists():
        raise FileExistsError('GAMER_G1_FROB_IMMUTABLE_OUTPUT')
    if max_length<1 or not isinstance(expected_head_sha256,str):
        raise ValueError('GAMER_G1_FROB_INVALID_SETTINGS')
    head,manifest=cur._load_checkpoint(checkpoint,device=device)
    if manifest.get('stage')!='G1':
        raise PermissionError('GAMER_G1_FROB_G1_ONLY')
    if manifest['head_sha256']!=expected_head_sha256:
        raise ValueError('GAMER_G1_FROB_UNEXPECTED_CHECKPOINT')
    dataset_sha=backbone.sha256(dataset)
    if manifest['dataset_sha256']!=dataset_sha:
        raise ValueError('GAMER_G1_FROB_DATASET_LINEAGE')
    if (manifest['source_adapter_sha256']!=
            backbone.adapter_fingerprint(manifest['source_adapter'])):
        raise ValueError('GAMER_G1_FROB_ADAPTER_LINEAGE')
    rows=[r for r in cur.load_rows(dataset) if r['split']=='dev']
    if not rows:
        raise ValueError('GAMER_G1_FROB_EMPTY_DEV')
    reference=_reference_misses(baseline_diagnosis,head_sha=manifest['head_sha256'],
                                dataset_sha=dataset_sha,case_count=len(rows))
    model,tok=backbone.model_components(manifest['model_id'],
                                        manifest['source_adapter'],device)
    head.eval()
    names=('baseline_global',)+(tuple(v[0] for v in VARIANTS))
    records={name:[] for name in names}
    changes={name:[] for name in names if name!='baseline_global'}
    overflow=Counter()
    baseline_misses={}
    for index,row in enumerate(rows,1):
        item=cur.capture(model,tok,row,device,max_length)
        prepared=item['prepared']
        with torch.inference_mode():
            prediction=head(item['hidden'].to(device=device,dtype=torch.float32),
                            eligible=prepared['eligible'].to(device),active_stage='G1')
        logits=prediction['span_logits']
        valid=prediction['span_valid']
        reference_spans=decode_neural_spans(logits,valid)
        proposals=_candidate_scores(logits,valid)
        # Exact identity check against the unmodified global decoder.
        if reference_spans != decode_whole_span_scores(
                proposals,length_power=0,per_object_penalty=0):
            raise ValueError('GAMER_G1_FROB_BASELINE_DECODER_DIVERGED')
        options={'baseline_global':reference_spans}
        for name,power,penalty in VARIANTS:
            try:
                options[name]=decode_whole_span_scores(
                    proposals,length_power=power,per_object_penalty=penalty)
            except ValueError as exc:
                if str(exc)!='GAMER_GRAPH_TOO_MANY_PREDICTED_OBJECTS':
                    raise
                overflow[name]+=1
                options[name]=None
        gold={(s['start'],s['end'],s['kind']) for s in row['object_spans']}
        baseline=_char_set(reference_spans,prepared)
        # Recompute the original diagnostic attribution from these same frozen
        # scores, rather than trusting an independently supplied reason label.
        aligned=cur.aligned_labels(prepared,row)
        attribution=attribute_case(prediction,prepared,row,aligned)
        for missed in attribution['misses']:
            start_end=missed['gold_span']
            if start_end is None:
                raise ValueError('GAMER_G1_FROB_REFERENCE_GOLD_ALIGNMENT')
            baseline_misses[(index,*start_end,missed['kind'])]=missed['reason']
        if len(attribution['misses'])!=len(gold-baseline):
            raise ValueError('GAMER_G1_FROB_DIAGNOSTIC_MISMATCH')
        for name,spans in options.items():
            predicted=(_char_set(spans,prepared) if spans is not None else set())
            records[name].append((predicted,gold))
            if name=='baseline_global':
                continue
            recovered=sorted((gold-baseline)&predicted)
            lost=sorted((gold&baseline)-predicted)
            new_false=sorted((predicted-gold)-(baseline-gold))
            removed_false=sorted((baseline-gold)-(predicted-gold))
            original_overlap_recovered=[span for span in recovered
                    if reference.get((index,*span))=='overlap_blocked']
            changes[name].append({
                'index':index,'family':row['family'],
                'recovered_gold':[ _surface(row,x) for x in recovered ],
                'lost_gold':[ _surface(row,x) for x in lost ],
                'introduced_false_positives':[ _surface(row,x) for x in new_false ],
                'removed_false_positives':[ _surface(row,x) for x in removed_false ],
                'original_overlap_recovered':[ _surface(row,x) for x in original_overlap_recovered ],
                'exact_gain':bool(predicted==gold and baseline!=gold),
                'exact_loss':bool(baseline==gold and predicted!=gold),
                'overflow':spans is None,
            })
        if index==1 or index%10==0 or index==len(rows):
            print(f'GAMER_G1_FROZEN_DECODER {index}/{len(rows)}',flush=True)
    if baseline_misses!=reference:
        raise ValueError('GAMER_G1_FROB_REFERENCE_MISMATCH_WITH_FROZEN_BASELINE')
    metrics={name:_summaries(recs) for name,recs in records.items()}
    base=metrics['baseline_global']
    results={}
    for name,power,penalty in VARIANTS:
        rows_changed=changes[name]
        recovery_by_reason=Counter()
        new_misses_by_kind=Counter()
        for row in rows_changed:
            for recovered in row['recovered_gold']:
                k=(row['index'],*recovered['span'],recovered['kind'])
                recovery_by_reason[reference[k]]+=1
            for lost in row['lost_gold']:
                new_misses_by_kind[lost['kind']]+=1
        m=metrics[name]
        gained=sum(len(r['recovered_gold']) for r in rows_changed)
        lost=sum(len(r['lost_gold']) for r in rows_changed)
        added_fp=sum(len(r['introduced_false_positives']) for r in rows_changed)
        removed_fp=sum(len(r['removed_false_positives']) for r in rows_changed)
        development_candidate=(
            not overflow[name] and m['exact_prompts']>base['exact_prompts'] and
            m['f1']>base['f1'] and m['precision']>=base['precision']-.02 and
            all(m['by_kind'][kind]['recall']>=base['by_kind'][kind]['recall']
                for kind in ('actor','action','quantity','outcome')))
        results[name]={
            'length_power':power,'per_object_penalty':penalty,
            'metrics':m,'overflow_cases':overflow[name],
            'recovered_gold':gained,'lost_previously_correct_gold':lost,
            'net_correct_objects':gained-lost,
            'recovered_original_misses_by_reason':dict(sorted(recovery_by_reason.items())),
            'recovered_original_overlap_misses':recovery_by_reason['overlap_blocked'],
            'newly_missed_by_kind':dict(sorted(new_misses_by_kind.items())),
            'introduced_false_positives':added_fp,
            'removed_false_positives':removed_fp,
            'exact_prompt_gains':sum(r['exact_gain'] for r in rows_changed),
            'exact_prompt_losses':sum(r['exact_loss'] for r in rows_changed),
            'dev_improvement_candidate':development_candidate,
        }
    output.mkdir(parents=True)
    report={'schema':SCHEMA,'status':'DEV_FROZEN_DECODER_ABLATION_ONLY_NOT_CERTIFICATION',
        'stage':'G1','split':'dev','cases':len(rows),
        'baseline_head_sha256':manifest['head_sha256'],
        'dataset_sha256':dataset_sha,
        'source_adapter_sha256':manifest['source_adapter_sha256'],
        'gold_reference_diagnosis':str(Path(baseline_diagnosis)),
        'baseline_misses':len(reference),
        'baseline_overlap_misses':sum(v=='overlap_blocked' for v in reference.values()),
        'baseline_global':base,
        'variants':results,
        'dev_improvement_candidates':[name for name in results
                                      if results[name]['dev_improvement_candidate']],
        'candidate_policy':'predeclared sweep on dev only; not selection/certification on blind test',
        'frozen_neural_scores':True,'no_training':True,
        'identical_logits_per_variant':True,'original_decoder_unchanged':True,
        'source_checkpoint_mutated':False,'production_promotion':False,
        'certification_authorized':False,'g2_unlocked':False,
        'challenge_or_test_split_used':False,
    }
    (output/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    with (output/'per_case_changes.jsonl').open('w',encoding='utf-8') as handle:
        for name in changes:
            for change in changes[name]:
                handle.write(json.dumps({'variant':name,**change},sort_keys=True)+'\n')
    return {'schema':SCHEMA,'status':report['status'],
            'baseline_head_sha256':manifest['head_sha256'],
            'baseline_global':base,
            'variants':results,
            'dev_improvement_candidates':report['dev_improvement_candidates'],
            'g2_unlocked':False,'production_promotion':False,
            'report_path':str((output/'report.json').resolve()),
            'per_case_path':str((output/'per_case_changes.jsonl').resolve())}
