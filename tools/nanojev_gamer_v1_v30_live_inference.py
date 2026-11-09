#!/usr/bin/env python3
"""Read-only V29/V30 live GAMER inference bridge, not Wide-head promotion.

This is a separate, explicit Gamer CLI stage that actually forwards the
frozen BASE Qwen on raw prompts. The V29 selector ranks ALL numeric spans
without oracle labels, and V30 predicts numeric token IDs from layer-18
activations. Exact Fraction subtraction is a documented downstream calculator,
NOT evidence that the learned Wide/to_numerical neural head does arithmetic.

No saved training cases, oracle spans, input token identities or expected
answers may be used as prediction inputs. No weights or production dispatch
configuration are changed. Refuse unsupported prompt families and changed
source fingerprints. Synthetic unit tests do not certify GPU inference.
"""
from __future__ import annotations

import json
import math
from fractions import Fraction
from pathlib import Path

import numpy as np
from tools import nanojev_gamer_v1_candidate_span_causality_v27 as v27
from tools import nanojev_gamer_v1_role_reversal_retention_v29 as v29
from tools import nanojev_gamer_v1_selector_retention_training_v28 as v28
from tools import nanojev_gamer_v1_zero_line_decoder_v30 as v30
from tools import nanojev_gamer_v1_ordered_token_causality_v25 as v25
from tools import nanojev_gamer_v1_precision_continuation_v19 as v19

SCHEMA='nanojev.gamer.v1.v29-v30-live-inference.v31'
MAX_CASES=1024


def _require(condition,code):
    if not condition:
        raise ValueError('V31_'+code)


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _sha(path):
    return v19._sha(Path(path))


def _source_hash(report, path):
    """V30 stores a map whose paths can be Windows relative or absolute."""
    resolved=Path(path).resolve()
    matches=[digest for filename,digest in report['source_sha256'].items()
             if Path(filename).resolve()==resolved]
    _require(len(matches)==1,'SOURCE_SHA_PATH_NOT_ATTESTED_'+resolved.name)
    _require(_sha(resolved)==matches[0],'SOURCE_SHA_CHANGED_'+resolved.name)
    return matches[0]


def load(args):
    """Verify source lineage and load disposable BASE-activation readouts."""
    import torch
    from tools.nanojev_two_game_spine_smoke import adapter_identity

    r27=Path(args.v27_run)/'report.json'
    r29=Path(args.v29_run)/'report.json'
    r30=Path(args.v30_run)/'report.json'
    p29=Path(args.v29_run)/'disposable_v29_hybrid_role_candidate.pt'
    p30=Path(args.v30_run)/'disposable_v30_decoder.pt'
    for path in (r27,r29,r30,p29,p30):
        _require(path.is_file(),'REQUIRED_ARTIFACT_MISSING_'+path.name)
    a,b,c=_read(r27),_read(r29),_read(r30)
    _require(a.get('schema')==v27.SCHEMA and
             a.get('status')=='READ_ONLY_ALL_CANDIDATES_NO_PROMOTION',
             'INVALID_V27_SOURCE')
    _require(b.get('schema')==v29.SCHEMA and
             b.get('status')=='ROLE_REVERSAL_TRAINING_COMPLETE_NO_PROMOTION' and
             b.get('promotion_authorized') is False and
             b.get('production_weights_modified') is False,
             'INVALID_V29_SOURCE')
    required_gates={'frozen_sources_unchanged','full_stress_zero_line',
         'non_reversal_stress_nonregression','numeric_decoder_available',
         'original_dev_exact_retained','original_dev_zero_exact',
         'previously_correct_train_numeric_answer_retention',
         'previously_correct_train_token_retention','train_selection_only'}
    _require(c.get('schema')==v30.SCHEMA and
             c.get('status')=='DECODER_TRAIN_ONLY_EXPERIMENT_COMPLETE_NO_PROMOTION' and
             c.get('all_gates_met') is True and
             set(c.get('acceptance_gates',{}))==required_gates and
             all(value is True for value in c['acceptance_gates'].values()) and
             c.get('promotion_authorized') is False and
             c.get('production_weights_modified') is False and
             c.get('no_dev_fitting_or_checkpoint_selection') is True and
             c.get('no_test_used') is True,
             'V30_ACCEPTANCE_OR_SOURCE_INVALID')
    _require(c['v27_report_sha256']==_sha(r27) and
             c['v29_report_sha256']==_sha(r29) and
             b.get('frozen_lineage')==a.get('frozen_lineage'),
             'V27_V29_V30_REPORT_LINEAGE_MISMATCH')
    for path,digest in c['source_sha256'].items():
        _require(Path(path).is_file() and _sha(path)==digest,
                 'FROZEN_EXPERIMENT_SOURCE_CHANGED_'+Path(path).name)
    _source_hash(c,p29)
    source=a['frozen_lineage']
    adapter=Path(source['adapter_dir'])
    checkpoint=Path(source['checkpoint_path'])
    _require(adapter.is_dir() and checkpoint.is_file(),'FROZEN_MODEL_MISSING')
    _require(_sha(checkpoint)==source['checkpoint_sha256'] and
             adapter_identity(adapter)==source['adapter_sha256'],
             'FROZEN_MODEL_IDENTITY_CHANGED')
    w29=torch.load(p29,map_location='cpu',weights_only=True)
    w30=torch.load(p30,map_location='cpu',weights_only=True)
    _require(w29.get('schema')==v29.SCHEMA and w29.get('production') is False and
             w30.get('schema')==v30.SCHEMA and w30.get('production') is False and
             w30.get('v29_checkpoint_sha256')==_sha(p29) and
             math.isclose(float(w30.get('alpha',-1)),float(c['selection_alpha']),abs_tol=1e-12) and
             w30.get('frozen_source_sha256')==c.get('source_sha256'),
             'DISPOSABLE_CHECKPOINT_LINEAGE_MISMATCH')
    base=np.asarray(w29['base_weight'].numpy(),dtype=np.float64)
    selector=np.asarray(w29['hybrid_v29_candidate'].numpy(),dtype=np.float32)
    tokens=np.asarray(w30['centroid_tokens'].numpy(),dtype=np.int64)
    centers=np.asarray(w30['centroids'].numpy(),dtype=np.float64)
    _require(base.ndim==2 and base.shape[0]==2 and
             selector.shape==(2,base.shape[1]+len(v28.MARKERS)+13) and
             centers.shape==(len(tokens),base.shape[1]) and len(tokens)>0 and
             len(set(int(x) for x in tokens))==len(tokens) and
             np.isfinite(base).all() and np.isfinite(selector).all() and
             np.isfinite(centers).all() and np.all(np.linalg.norm(centers,axis=1)>0),
             'DECODER_OR_SELECTOR_DIMENSIONS_INVALID')
    return {'base':base,'selector':selector,'decoder':(tokens.tolist(),centers),
            'lineage':source,'adapter':adapter,
            'source_files':{str(p):_sha(p) for p in
                            (r27,r29,r30,p29,p30,checkpoint)},
            'alpha':float(w30['alpha'])}


def _validate_prompt(prompt):
    _require(isinstance(prompt,str) and bool(prompt.strip()),'EMPTY_PROMPT')
    # These are the two signed-difference task families fitted by V27-V30.
    native=('spacecraft is at signed coordinate' in prompt and
            'navigation beacon is at signed coordinate' in prompt)
    game=('CC=' in prompt and 'DC=' in prompt and 'Compute T-R' in prompt)
    _require(native != game,'UNSUPPORTED_OR_AMBIGUOUS_PROMPT_FAMILY')
    candidates=v27._numeric_candidates(prompt)
    _require(2<=len(candidates)<=16,'CANDIDATE_LIMIT')
    return candidates


def infer_from_capture(prompt,candidates,arrays,base,selector,decoder,tok):
    """Live inference kernel: no truth, no oracle, no input token ID scoring."""
    metadata=[];offset=0
    token_vectors=np.asarray(arrays['layer_18'],dtype=np.float32)
    lexical=v27._numeric_candidates(prompt)
    lookup={tuple(c['span']):c for c in lexical}
    raw=[];ctx=[]
    for c in candidates:
        count=len(c['indices'])
        _require(count>0 and count<=16,'BAD_CANDIDATE_TOKEN_COUNT')
        end=offset+count
        _require(end<=len(token_vectors),'CANDIDATE_CAPTURE_BOUNDS')
        span=tuple(c['span'])
        _require(span in lookup,'CAPTURE_LEXICAL_SPAN_MISMATCH')
        # Replicate V28's float32 mean, then float64 normalize.
        vec=v28._normalize(token_vectors[offset:end].mean(axis=0))
        raw.append(vec)
        ctx.append(v28._local_features(prompt,lookup[span],lexical))
        metadata.append((offset,end,span))
        offset=end
    _require(offset==len(token_vectors),'INCOMPLETE_CAPTURE')
    x=np.asarray(raw,dtype=np.float64)
    context=np.asarray(ctx,dtype=np.float64)
    case={'indices':list(range(len(metadata))),
          'base':x@base.T,'x':np.concatenate([x,context],axis=1)}
    _require(case['x'].shape[1]==selector.shape[1],'LIVE_SELECTOR_SHAPE_MISMATCH')
    first,second=v28._predict(case,selector)
    values=[];token_ids=[];selected=[]
    for j in (first,second):
        start,stop,span=metadata[j]
        features=v30._normal(token_vectors[start:stop])
        ids=v30._predict_tokens(features,decoder).tolist()
        value=v25._parse_decoded_number(v25._decode_ids(tok,ids))
        _require(value is not None,'TOKEN_DECODER_DID_NOT_PRODUCE_NUMBER')
        values.append(value)
        token_ids.append(ids)
        selected.append(list(span))
    # Explicit deterministic arithmetic: not learned Wide-head output.
    delta=values[0]-values[1]
    return {'first_operand':str(values[0]),'second_operand':str(values[1]),
            'difference':str(delta),'sign':int((delta>0)-(delta<0)),
            'zero_exact':delta==0,'predicted_token_ids':token_ids,
            'selected_spans':selected,'candidate_count':len(metadata),
            'calculation_authority':'exact_fraction_postprocess',
            'operand_authority':'frozen_v29_selector_and_v30_layer18_decoder',
            'oracle_used_for_prediction':False}


def _inputs(args):
    provided=sum(x is not None for x in (args.v30_prompt,args.v30_prompt_file,args.v30_cases_jsonl))
    _require(provided==1,'REQUIRES_ONE_PROMPT_OR_DECK')
    expected=getattr(args,'v30_expected',None)
    if args.v30_prompt is not None or args.v30_prompt_file is not None:
        prompt=(args.v30_prompt if args.v30_prompt is not None else
            Path(args.v30_prompt_file).read_text(encoding='utf-8').strip())
        record={'id':'single','prompt':prompt}
        if expected is not None:
            record['expected']=str(Fraction(expected))
        return [record]
    _require(expected is None,'EXPECTED_ARG_ONLY_WITH_SINGLE_PROMPT')
    result=[]
    for line in Path(args.v30_cases_jsonl).read_text(encoding='utf-8').splitlines():
        if not line.strip():continue
        record=json.loads(line)
        _require(isinstance(record,dict) and isinstance(record.get('prompt'),str) and
                 isinstance(record.get('id'),str),'BAD_DECK_ROW')
        if 'expected' in record:
            # Validation only: prediction sees only .prompt, NEVER expected.
            Fraction(str(record['expected']))
        result.append(record)
    _require(0<len(result)<=MAX_CASES and len({r['id'] for r in result})==len(result),
             'INVALID_DECK_SIZE_OR_DUPLICATES')
    return result


def run(args):
    """Explicit Gamer v30-live stage; never trains, switches or promotes Wide."""
    artifacts=load(args)
    if args.v30_plan_only:
        result={'schema':SCHEMA,'status':'V31_PLAN_VERIFIED_NO_FORWARD',
                'sources':artifacts['source_files'],
                'frozen_adapter_sha256':artifacts['lineage']['adapter_sha256'],
                'selected_alpha':artifacts['alpha'],
                'production_modified':False,'promotion_authorized':False}
        print('GAMER_V30_LIVE_PLAN '+json.dumps(result,sort_keys=True),flush=True)
        return result
    rows=_inputs(args)
    for row in rows:_validate_prompt(row['prompt'])
    output=Path(args.v30_report)
    _require(not output.exists(),'REPORT_ALREADY_EXISTS')
    _require(args.max_length<=artifacts['lineage']['configuration']['max_length'],
             'PROMPT_MAX_LENGTH_EXCEEDS_CERTIFIED_CAPTURE')
    from tools.nanojev_gamer_v1_handoff import model_components
    from tools.nanojev_two_game_spine_smoke import adapter_identity,cache_policy
    import torch
    cache_policy(False)  # Source model and tokenizer MUST already be cached.
    # V27/V28/V29/V30 train and score exclusively on BASE layer-18
    # activations. Attest the LoRA lineage, but DO NOT apply that LoRA here.
    _,tok,model=model_components(artifacts['lineage']['model_id'],args.device)
    model.eval()
    reports=[]
    try:
        for index,row in enumerate(rows,1):
            if len(tok.encode(row['prompt'],add_special_tokens=True))>args.max_length:
                raise ValueError('V31_PROMPT_TRUNCATION_FORBIDDEN: '+row['id'])
            candidates,vectors=v27._capture_batch(torch,model,tok,[{'prompt':row['prompt']}],
                device=args.device,max_input_tokens=args.max_length,
                max_candidates=16,max_candidate_tokens=16)
            result=infer_from_capture(row['prompt'],candidates,vectors,
                artifacts['base'],artifacts['selector'],artifacts['decoder'],tok)
            result['id']=row['id']
            if 'expected' in row:
                expected=Fraction(str(row['expected']))
                result['expected']=str(expected)
                result['exact']=Fraction(result['difference'])==expected
            reports.append(result)
            print(f'GAMER_V30_LIVE {index}/{len(rows)} {row["id"]} '
                  f'{result["difference"]}',flush=True)
    finally:
        del model,tok
        if torch.cuda.is_available():torch.cuda.empty_cache()
    _require(all(_sha(Path(p))==digest for p,digest in artifacts['source_files'].items()) and
             adapter_identity(artifacts['adapter'])==artifacts['lineage']['adapter_sha256'],
             'FROZEN_SOURCE_CHANGED_DURING_INFERENCE')
    judged=[r for r in reports if 'exact' in r]
    zero=[r for r in judged if Fraction(r['expected'])==0]
    result={'schema':SCHEMA,'status':'V31_READ_ONLY_LIVE_INFERENCE_COMPLETE',
            'cases':reports,'total':len(reports),'scored_cases':len(judged),
            'exact_cases':sum(r['exact'] for r in judged),
            'zero_cases':len(zero),'zero_exact':sum(r['exact'] for r in zero),
            'all_scored_exact':bool(judged) and all(r['exact'] for r in judged),
            'sources':artifacts['source_files'],
            'frozen_adapter_sha256':artifacts['lineage']['adapter_sha256'],
            'selected_alpha':artifacts['alpha'],
            'activation_source':'frozen_base_qwen_layer18_no_lora_adapter',
            'wide_head_untouched':True,
            'production_modified':False,'promotion_authorized':False,
            'scope':'ONLY_CERTIFIED_DIFFERENCE_PROMPT_FAMILIES',
            'limits':['Exact subtraction is deterministic postprocessing, not a neural calculation',
                      'V30 base activations must not be replaced with LoRA activations',
                      'No assertion of Wide/to_numerical general competence or production cutover']}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    print('GAMER_V30_LIVE_COMPLETE '+json.dumps({'report':str(output),
        'exact':result['exact_cases'],'scored':result['scored_cases'],
        'zero_exact':result['zero_exact'],'zero_cases':result['zero_cases'],
        'promotion':False},sort_keys=True),flush=True)
    return result
