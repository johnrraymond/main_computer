"""Read-only G1 error attribution for an EXISTING joint-span checkpoint.

Development split only.  Replays the frozen Qwen/LoRA and G1 head once per
prompt; never trains, changes model weights, calibrates production inference,
creates a gate certificate, or reads the sealed test/challenge splits.

Gold spans are used ONLY after inference for analysis and metrics.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import json

import torch
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS
from tools.nanojev_gamer_v1_graph_head import decode_neural_spans, token_span_char_bounds

SCHEMA = 'nanojev.gamer.graph.g1.span-attribution.v1'
DEFAULT_SWEEP = (0.05, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 0.95)


def _span_tuple(span):
    return (int(span['token_first']), int(span['token_last']), str(span['kind']))


def _char_set(spans, prepared):
    found = set()
    for span in spans:
        bounds = token_span_char_bounds(span, prepared)
        if bounds is not None:
            found.add((*bounds, span['kind']))
    return found


def attribute_case(output, prepared, row, aligned, *, confidence=0.50):
    """Return all missed-object reasons from existing logits, without edits.

    The full gold candidate is inspected even when the decoder rejects it.
    'wrong_type' and 'null_wins' mean the neural scorer itself failed;
    'below_threshold' and 'overlap_blocked' concern candidate selection.
    """
    logits = output['span_logits'].detach().cpu()
    valid = output['span_valid'].detach().cpu().bool()
    probabilities = logits.softmax(-1)
    decoded = decode_neural_spans(logits, valid, confidence=confidence)
    chosen_tokens = {_span_tuple(s) for s in decoded}
    chosen_char = _char_set(decoded, prepared)
    gold = {(s['start'], s['end'], s['kind']) for s in row['object_spans']}
    reasons = Counter()
    missed = []
    type_correct_candidates = 0
    supported_candidates = 0

    for labeled in aligned['spans']:
        a, b, kind = _span_tuple(labeled)
        bounds = token_span_char_bounds(labeled, prepared)
        char_id = (*bounds, kind) if bounds is not None else None
        label_id = SPAN_KINDS.index(kind) + 1
        record = {'kind': kind, 'gold_span':list(char_id[:2]) if char_id else None,
                  'token_span':[a,b],
                  'surface':prepared['prompt'][bounds[0]:bounds[1]] if bounds else None}
        if b-a >= logits.shape[1]:
            reason = 'span_exceeds_window'
        elif not bool(valid[a,b-a]):
            reason = 'invalid_token_span'
        else:
            supported_candidates += 1
            scores = probabilities[a,b-a]
            top = int(scores.argmax().item())
            gold_probability = float(scores[label_id].item())
            best_type_index = int(scores[1:].argmax().item()) + 1
            best_type_confidence = float(scores[best_type_index].item())
            null_probability = float(scores[0].item())
            record.update({'gold_probability':round(gold_probability,6),
                           'null_probability':round(null_probability,6),
                           'best_type':SPAN_KINDS[best_type_index-1],
                           'best_type_probability':round(best_type_confidence,6),
                           'top_class':'none' if top==0 else SPAN_KINDS[top-1],
                           'gold_logit_margin':round(float((logits[a,b-a,label_id]
                                                         -logits[a,b-a,0]).item()),6)})
            if top==label_id:
                type_correct_candidates += 1
            if char_id in chosen_char:
                continue
            if bounds is None or char_id not in gold:
                reason = 'token_to_character_alignment'
            elif top==0:
                reason = 'null_wins'
            elif top!=label_id:
                reason = 'wrong_type'
            elif best_type_confidence < confidence:
                reason = 'below_threshold'
            elif (a,b,kind) not in chosen_tokens:
                overlaps = [s for s in decoded if not (s['token_last']<a or s['token_first']>b)]
                record['blocking_proposals'] = [
                    {'kind':s['kind'],'token_span':[s['token_first'],s['token_last']],
                     'char_span':list(token_span_char_bounds(s,prepared))
                                 if token_span_char_bounds(s,prepared) else None}
                    for s in overlaps[:8]]
                reason = 'overlap_blocked' if overlaps else 'decoder_unexplained'
            else:
                reason = 'decoder_char_mismatch'
        record['reason']=reason
        reasons[reason]+=1
        missed.append(record)

    # A gold span missing solely due to offsets is a separate alignment issue.
    # The score of the full token candidate remains present in the record.
    extra = sorted(chosen_char-gold)
    return {
        'gold_count':len(gold),'predicted_count':len(chosen_char),
        'correct_count':len(chosen_char & gold),
        'exact':chosen_char==gold,
        'candidate_supported_count':supported_candidates,
        'candidate_correct_type_count':type_correct_candidates,
        'miss_reasons':dict(reasons),
        'misses':missed,
        'extra_objects':[{'span':[a,b], 'kind':k,
                          'surface':prepared['prompt'][a:b]} for a,b,k in extra],
    }


def _metrics(scores):
    correct=sum(v['correct'] for v in scores)
    predicted=sum(v['predicted'] for v in scores)
    expected=sum(v['expected'] for v in scores)
    exact=sum(v['exact'] for v in scores)
    p=correct/max(predicted,1);r=correct/max(expected,1)
    return {'exact_rate':exact/max(len(scores),1),
            'precision':p,'recall':r,'f1':2*p*r/max(p+r,1e-12),
            'correct':correct,'predicted':predicted,'expected':expected}


def diagnose(*, dataset, checkpoint, output, device='cuda', limit=0,
             max_length=2048, thresholds=DEFAULT_SWEEP):
    """Recompute missing span attribution from a fixed G1 checkpoint.

    Writes summary.json and misses.jsonl in a NEW output directory. Cannot
    promote G1 or mutate existing checkpoint, corpus or official reports.
    """
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    from tools import nanojev_gamer_v1_backbone_first as backbone

    dataset=Path(dataset);checkpoint=Path(checkpoint);output=Path(output)
    if output.exists():
        raise FileExistsError('GAMER_GRAPH_DIAGNOSTIC_IMMUTABLE')
    if limit < 0:
        raise ValueError('GAMER_GRAPH_DIAGNOSTIC_BAD_LIMIT')
    thresholds = tuple(sorted(set(float(x) for x in thresholds) | {0.50}))
    if not thresholds or not all(0<=x<=1 for x in thresholds):
        raise ValueError('GAMER_GRAPH_DIAGNOSTIC_INVALID_THRESHOLDS')
    head,manifest=cur._load_checkpoint(checkpoint,device=device)
    if manifest['stage']!='G1':
        raise PermissionError('GAMER_GRAPH_DIAGNOSTIC_G1_ONLY')
    if manifest['dataset_sha256']!=backbone.sha256(dataset):
        raise ValueError('GAMER_GRAPH_DIAGNOSTIC_DATASET_LINEAGE')
    if manifest['source_adapter_sha256']!=backbone.adapter_fingerprint(manifest['source_adapter']):
        raise ValueError('GAMER_GRAPH_DIAGNOSTIC_SOURCE_LINEAGE')
    rows=[r for r in cur.load_rows(dataset) if r['split']=='dev']
    if limit: rows=rows[:limit]
    model,tok=backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    head.eval()
    aggregates={x:[] for x in thresholds}
    threshold_errors=Counter()
    reasons=Counter();kind_reasons={k:Counter() for k in SPAN_KINDS}
    details=[];full_misses=[]
    supported=correct_types=0
    for index,row in enumerate(rows,1):
        prepared=backbone.prepare_prompt(tok,row['prompt'],max_input_tokens=max_length)
        with torch.inference_mode():
            h=backbone.frozen_context(model,prepared,device=device)
            prediction=head(h.to(device=device,dtype=torch.float32),
                            eligible=prepared['eligible'].to(device),active_stage='G1')
        labels=cur.aligned_labels(prepared,row)  # Analysis AFTER neural inference.
        assessment=attribute_case(prediction,prepared,row,labels)
        supported+=assessment['candidate_supported_count']
        correct_types+=assessment['candidate_correct_type_count']
        reasons.update(assessment['miss_reasons'])
        for miss in assessment['misses']:
            kind_reasons[miss['kind']][miss['reason']]+=1
            full_misses.append({'index':index,'family':row['family'],**miss})
        details.append({'index':index,'family':row['family'],
                        'gold_count':assessment['gold_count'],
                        'predicted_count':assessment['predicted_count'],
                        'correct_count':assessment['correct_count'],
                        'extra_objects':assessment['extra_objects'],
                        'miss_reasons':assessment['miss_reasons']})
        gold={(s['start'],s['end'],s['kind']) for s in row['object_spans']}
        with torch.inference_mode():
            for threshold in thresholds:
                try:
                    decoded=decode_neural_spans(prediction['span_logits'],
                                                prediction['span_valid'],confidence=threshold)
                except ValueError as exc:
                    if str(exc)!='GAMER_GRAPH_TOO_MANY_PREDICTED_OBJECTS':
                        raise
                    threshold_errors[threshold]+=1
                    # Treat candidate-overflow as a failed decoding, not an
                    # invented empty prediction or an excluded easy case.
                    aggregates[threshold].append({'correct':0,'predicted':0,
                        'expected':len(gold),'exact':0})
                    continue
                found=_char_set(decoded,prepared)
                aggregates[threshold].append({'correct':len(found&gold),
                    'predicted':len(found),'expected':len(gold),'exact':int(found==gold)})
        if index==1 or index%10==0 or index==len(rows):
            print(f'GAMER_GRAPH_G1_DIAG {index}/{len(rows)} '
                  f'correct={assessment["correct_count"]}/{assessment["gold_count"]} '
                  f'misses={len(assessment["misses"])}',flush=True)
    threshold_results={f'{t:.2f}':{**_metrics(aggregates[t]),
                                   'overflow_cases':threshold_errors[t]}
                       for t in thresholds}
    summary={'schema':SCHEMA,'status':'DIAGNOSTIC_ONLY_NOT_A_GATE',
        'stage':'G1','split':'dev','cases':len(rows),'limited':bool(limit),
        'checkpoint_head_sha256':manifest['head_sha256'],
        'dataset_sha256':manifest['dataset_sha256'],
        'source_adapter_sha256':manifest['source_adapter_sha256'],
        'threshold_active_in_production':0.50,'threshold_sweep':threshold_results,
        'miss_reasons':dict(reasons),
        'by_kind_miss_reasons':{k:dict(v) for k,v in kind_reasons.items()},
        'gold_token_candidates_supported':supported,
        'gold_token_candidates_correct_top_type':correct_types,
        'per_case':details,
        'classification_note':('wrong_type/null_wins imply a scorer error; '
          'below_threshold implies cutoff suppression; overlap_blocked implies '
          'greedy selection suppression; a changed sweep score is diagnostic only.'),
        'generated_tokens':0,'teacher_graph_at_inference':False,
        'checkpoint_mutated':False,'certification_authorized':False,
        'production_decoder_mutated':False,'test_or_challenge_split_used':False}
    output.mkdir(parents=True)
    (output/'summary.json').write_text(json.dumps(summary,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    with (output/'misses.jsonl').open('w',encoding='utf-8') as f:
        for miss in full_misses:
            f.write(json.dumps(miss,sort_keys=True)+'\n')
    return {'schema':SCHEMA,'status':summary['status'],
            'cases':len(rows),'checkpoint_head_sha256':manifest['head_sha256'],
            'miss_reasons':summary['miss_reasons'],
            'by_kind_miss_reasons':summary['by_kind_miss_reasons'],
            'gold_token_candidates_supported':supported,
            'gold_token_candidates_correct_top_type':correct_types,
            'threshold_sweep':threshold_results,
            'summary_path':str((output/'summary.json').resolve()),
            'misses_path':str((output/'misses.jsonl').resolve()),
            'checkpoint_mutated':False,'certification_authorized':False}
