#!/usr/bin/env python3
"""V27 fully captured candidate-number audit. Frozen Qwen/LoRA; NO promotion.

All numerical candidates, not oracle-cropped operands, are forwarded and saved.
TRAIN group labels fit two diagnostic selectors and token-ID centroids. Inference
receives candidate tokens/context ONLY; oracle arithmetic roles are used for
TRAIN supervision and, independently, to score held-out and DEV predictions.
A text-template locator is shown separately and never silently substituted for
the neural-only locator. Fraction arithmetic is a diagnostic tool, not a learned
numerical operation. The model is NEVER trained or patched.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from fractions import Fraction
import gc
import json
from pathlib import Path
import re

import numpy as np
from tools import nanojev_gamer_v1_operand_span_causality_v26 as v26
from tools import nanojev_gamer_v1_ordered_token_causality_v25 as v25
from tools import nanojev_gamer_v1_operand_token_causality_v24 as v24
from tools import nanojev_gamer_v1_sign_information_probe as probe
from tools import nanojev_gamer_v1_head_only_lab as lab
from tools import nanojev_gamer_v1_precision_continuation_v19 as v19
from tools import nanojev_two_game_spine_smoke as spine

SCHEMA='nanojev.gamer.v1.all-candidate-span-causality.v27'
STAGES=('embedding','layer_09','layer_18','layer_27','final')
ROLE_TAGS=('FIRST','SECOND')


def _require(ok,code):
    if not ok:raise ValueError('V27_'+code)


def _numeric_candidates(prompt):
    """Find candidates without using expected values or oracle locations."""
    candidates=v26._candidates(prompt)
    _require(len({tuple(c['span']) for c in candidates})==len(candidates),
             'DUPLICATE_CANDIDATES')
    return candidates


def _truth_spans(prompt,surface):
    """Independent scoring oracle; NEVER called by inference/selector."""
    if surface=='source_native':
        a=re.search(r'spacecraft is at signed coordinate\s+(-?(?:\d+/\d+|\d+(?:\.\d+)?))',prompt)
        b=re.search(r'navigation beacon is at signed coordinate\s+(-?(?:\d+/\d+|\d+(?:\.\d+)?))',prompt)
        _require(a is not None and b is not None,'NATIVE_SCORING_ORACLE')
        return a.span(1),b.span(1)
    if surface=='spine_difference':
        a=re.search(r'\bDC\s*=\s*(-?(?:\d+/\d+|\d+(?:\.\d+)?))',prompt)
        b=re.search(r'\bCC\s*=\s*(-?(?:\d+/\d+|\d+(?:\.\d+)?))',prompt)
        _require(a is not None and b is not None and 'Compute T-R' in prompt,
                 'GAME_SCORING_ORACLE')
        return a.span(1),b.span(1)
    raise ValueError('V27_UNKNOWN_SURFACE')


def _challenge_prompt(prompt,surface,kind):
    """Fixed, predeclared perturbations. They do not enter TRAIN fitting."""
    if kind=='original':return prompt
    if kind=='distractor_prefix':
        return ('Unrelated audit identifiers: 314159265 and 271828182. '
                'Ignore those identifiers for the requested calculation. '+prompt)
    if kind=='novel_operands':
        a,b=_truth_spans(prompt,surface)
        original=Fraction(prompt[slice(*a)])-Fraction(prompt[slice(*b)])
        sign=(original>0)-(original<0)
        left,right='314159265',str(314159265-sign)
        for span,value in sorted(((a,left),(b,right)),key=lambda x:x[0][0],reverse=True):
            prompt=prompt[:span[0]]+value+prompt[span[1]:]
        return prompt
    if kind=='altered_wording':
        return ('Irrelevant metadata for validation: batch 8675309. '
                'The following question is the only arithmetic task. '+prompt)
    if kind=='role_reordered':
        if surface=='source_native':
            match=re.search(r'A spacecraft is at signed coordinate ([-\d./]+), while a navigation beacon is at signed coordinate ([-\d./]+)',prompt)
            _require(match is not None,'NATIVE_REORDER_PARSE')
            return (prompt[:match.start()]+f'A navigation beacon is at signed coordinate {match.group(2)}, while a spacecraft is at signed coordinate {match.group(1)}'+prompt[match.end():])
        match=re.search(r'CC=([^,]+), CD=([^,]+), DC=([^,]+), DD=([^\.]+)\.',prompt)
        _require(match is not None,'GAME_REORDER_PARSE')
        reordered=f'DC={match.group(3)}, DD={match.group(4)}, CC={match.group(1)}, CD={match.group(2)}.'
        return prompt[:match.start()]+reordered+prompt[match.end():]
    raise ValueError('V27_UNKNOWN_CHALLENGE')


def _entries(rows,prompts,train_count,stress_limit):
    entries=[]
    for i,row in enumerate(rows):
        p=prompts[row['archive_index']]
        entries.append({'row_index':i,'id':row['id'],'scope':'train' if i<train_count else 'dev',
                        'kind':'original','prompt':p,'surface':row['surface'],
                        'target':Fraction(row['target_fraction'])})
    # Fixed challenge selection BEFORE any DEV readout performance is known.
    eligible=list(range(train_count,len(rows)))[:stress_limit]
    for kind in ('distractor_prefix','altered_wording','role_reordered','novel_operands'):
        for i in eligible:
            r=rows[i];p=_challenge_prompt(prompts[r['archive_index']],r['surface'],kind)
            a,b=_truth_spans(p,r['surface'])
            truth=Fraction(p[slice(*a)])-Fraction(p[slice(*b)])
            if kind!='novel_operands':
                _require(truth==Fraction(r['target_fraction']),'STRESS_TARGET_CHANGED')
            entries.append({'row_index':i,'id':r['id']+'::'+kind,'scope':'dev_stress',
                'kind':kind,'prompt':p,'surface':r['surface'],
                'target':truth})
    for e in entries:
        a,b=_truth_spans(e['prompt'],e['surface'])
        fa=Fraction(e['prompt'][slice(*a)]);fb=Fraction(e['prompt'][slice(*b)])
        _require(fa-fb==e['target'],'STRESS_ARITHMETIC_CHANGED')
        e['_oracle']=(a,b)  # scoring/training only; never an input to capture
    return entries


def _capture_batch(torch,model,tok,entries,*,device,max_input_tokens,max_candidates,max_candidate_tokens):
    """Capture ALL numeric candidate positions. No oracle-span access here."""
    from tools.nanojev_gamer_v1_sign_information_probe import _backbone_parts
    core=_backbone_parts(model)
    _require(tok.is_fast and tok.padding_side=='right','FAST_RIGHT_PAD_TOKENIZER')
    prompts=[e['prompt'] for e in entries]
    _require(all(len(tok.encode(p,add_special_tokens=True))<=max_input_tokens for p in prompts),
             'PROMPT_TOO_LONG')
    encoding=tok(prompts,padding=True,truncation=False,return_offsets_mapping=True,
                 return_tensors='pt')
    offsets=encoding.pop('offset_mapping').tolist()
    lengths=encoding['attention_mask'].sum(1).tolist()
    token_ids=encoding['input_ids'].tolist()
    candidates=[]
    for i,e in enumerate(entries):
        found=_numeric_candidates(e['prompt'])
        _require(2<=len(found)<=max_candidates,'CANDIDATE_COUNT_EXCEEDS_LIMIT')
        for c in found:
            ix=v24._aligned_tokens(offsets[i][:int(lengths[i])],c['span'])
            _require(len(ix)<=max_candidate_tokens,'CANDIDATE_TOKEN_LIMIT')
            candidates.append({'case_in_batch':i,'span':c['span'],'marker':c['marker'],
                  'text':c['value'],'indices':ix,'token_ids':[token_ids[i][j] for j in ix]})
    tensors={};handles=[]
    names={'embedding':core.embed_tokens,'layer_09':core.layers[9],
           'layer_18':core.layers[18],'layer_27':core.layers[27],
           'final':core.norm}
    def build_hook(name):
        def hook(_module,_args,result):
            x=result[0] if isinstance(result,(tuple,list)) else result
            _require(x.ndim==3 and x.shape[0]==len(entries),'BAD_HOOK_'+name)
            tensors[name]=np.concatenate([x[c['case_in_batch'],c['indices'],:].float()
                                          .detach().cpu().numpy() for c in candidates],axis=0)
        return hook
    for name,module in names.items():handles.append(module.register_forward_hook(build_hook(name)))
    try:
        with torch.inference_mode():
            model(**{k:v.to(device) for k,v in encoding.items()},
                  output_hidden_states=False,return_dict=True,logits_to_keep=1)
    finally:
        for handle in handles:handle.remove()
    _require(set(tensors)==set(STAGES),'MISSING_STAGES')
    return candidates,tensors


def _capture_variant(torch,tok,model,entries,*,variant,device,batch,max_input_tokens,
                     max_candidates,max_candidate_tokens):
    vectors={s:[] for s in STAGES};metadata=[];offset=0
    for start in range(0,len(entries),batch):
        picked=entries[start:start+batch]
        candidates,arrays=_capture_batch(torch,model,tok,picked,device=device,
                 max_input_tokens=max_input_tokens,max_candidates=max_candidates,
                 max_candidate_tokens=max_candidate_tokens)
        for c in candidates:
            n=len(c['token_ids'])
            metadata.append({'case_index':start+c['case_in_batch'],'span':list(c['span']),
                   'marker':c['marker'],'text':c['text'],'token_ids':c['token_ids'],
                   'start':offset,'stop':offset+n})
            offset+=n
        for s in STAGES:vectors[s].append(arrays[s])
        if start==0 or (start//batch)%30==0:
            print('V27_CAPTURE '+probe.canonical({'variant':variant,'complete':min(
                   start+batch,len(entries)),'total':len(entries)}),flush=True)
    return metadata,{s:np.concatenate(v,axis=0).astype(np.float32)
                     for s,v in vectors.items()}


def _gold_candidate_ids(entries,metadata):
    by_case=defaultdict(list)
    for i,c in enumerate(metadata):by_case[c['case_index']].append(i)
    result=[]
    for case_index,e in enumerate(entries):
        oracle=e['_oracle']
        found=[next((i for i in by_case[case_index] if
                     tuple(metadata[i]['span'])==tuple(span)),None) for span in oracle]
        _require(all(x is not None for x in found) and len(set(found))==2,
                 'ORACLE_SCORING_SPAN_ABSENT_FROM_FULL_CANDIDATE_CAPTURE')
        result.append(tuple(found))
    return by_case,result


def _v25_independent_capture_parity(path,metadata,vectors,gold,rows):
    """Compare freshly forwarded selected candidates to v25, never fetch missing inputs.

    Historical oracle-cropped features are used ONLY here as a post-capture
    fingerprint check, and cannot supply selector or decoded values.
    """
    _require(path.is_file(),'V25_ARCHIVE_MISSING_FOR_PARITY')
    max_difference=0.0
    with np.load(path,allow_pickle=False) as z:
        ids=z['operand_token_ids'];mask=z['token_mask']
        indices=z['archive_indices']
        reference={stage:z[stage] for stage in ('embedding','layer_18','final')}
        _require(ids.shape[:2]==(len(rows),2) and
                 np.array_equal(indices,[r['archive_index'] for r in rows]),
                 'V25_PARITY_INDEX')
        for i in range(len(rows)):
            for role,j in enumerate(gold[i]):
                c=metadata[j];start,stop=c['start'],c['stop']
                n=int(mask[i,role].sum())
                _require(n==stop-start and c['token_ids']==ids[i,role,:n].tolist(),
                         'V25_SELECTED_TOKEN_ID_PARITY')
                for stage in ('embedding','layer_18','final'):
                    actual=vectors[stage][start:stop]
                    historical=reference[stage][i,role,:n]
                    maximum=float(np.max(np.abs(actual-historical)))
                    _require(np.isfinite(maximum),'V25_ACTIVATION_NONFINITE')
                    max_difference=max(max_difference,maximum)
    # Using the same frozen model, tokenizer, model precision and prompt set,
    # the original selected features must agree to better than 0.01.
    _require(max_difference<=0.01,'V25_INDEPENDENT_FORWARD_PARITY_MISMATCH')
    return max_difference


def _fit_neural_selector(vectors,metadata,entries,gold,fit_cases,stage):
    """TRAIN-only normalized candidate centroids; model sees no text/template cues."""
    _require(all(entries[i]['scope']=='train' for i in fit_cases),'NEURAL_FIT_NOT_TRAIN')
    x=vectors[stage];positives=[[],[]];negatives=[[],[]]
    for i in fit_cases:
        selected=gold[i]
        for j,c in enumerate(metadata):
            if c['case_index']!=i:continue
            pooled=x[c['start']:c['stop']].mean(0).astype(np.float64)
            pooled/=np.linalg.norm(pooled)+1e-12
            for role in range(2):
                (positives[role] if j==selected[role] else negatives[role]).append(pooled)
    _require(all(positives) and all(negatives),'NEURAL_SELECTOR_EMPTY_ROLE')
    weights=[]
    for role in range(2):
        w=np.mean(positives[role],0)-np.mean(negatives[role],0)
        weights.append(w/(np.linalg.norm(w)+1e-12))
    return np.asarray(weights)


def _select_neural(weights,vectors,metadata,candidates):
    if len(candidates)<2:return None
    reps=[]
    for j in candidates:
        c=metadata[j];x=vectors[c['start']:c['stop']].astype(np.float64).mean(0)
        reps.append(x/(np.linalg.norm(x)+1e-12))
    scores=np.asarray(reps)@weights.T
    pairs=sorted(((float(scores[i,0]+scores[j,1]),candidates[i],candidates[j])
                  for i in range(len(candidates)) for j in range(len(candidates)) if i!=j),
                 reverse=True)
    return pairs[0][1:]


def _fit_token_centroids(vectors,metadata,gold,fit_cases,stage):
    """TRAIN selected operands ONLY. OOV must fail, even if guessed ID matches."""
    by_token=defaultdict(list)
    x=vectors[stage]
    for i in fit_cases:
        for j in gold[i]:
            c=metadata[j]
            for t,vec in zip(c['token_ids'],x[c['start']:c['stop']]):
                by_token[int(t)].append(np.asarray(vec,dtype=np.float64))
    vocab=sorted(by_token)
    _require(bool(vocab),'EMPTY_ID_FIT')
    center=np.asarray([np.mean(by_token[k],axis=0) for k in vocab],dtype=np.float64)
    center/=np.linalg.norm(center,axis=1,keepdims=True)+1e-12
    return vocab,center


def _decode_candidate(vectors,c,fit,tok):
    vocab,centers=fit
    seq=np.asarray(vectors[c['start']:c['stop']],dtype=np.float64)
    seq/=np.linalg.norm(seq,axis=1,keepdims=True)+1e-12
    guesses=[vocab[k] for k in (seq@centers.T).argmax(axis=1)]
    # OOV flag is diagnostic only; unknown lexical tokens cannot be credited.
    known=all(t in set(vocab) for t in c['token_ids'])
    decoded=(v25._parse_decoded_number(v25._decode_ids(tok,guesses))
             if known else None)
    return decoded,guesses,known


def _evaluate(entries,metadata,vectors,by_case,gold,fit_cases,eval_cases,tok,
              text_locator,*,variant,stage,scope):
    weights=_fit_neural_selector({stage:vectors},metadata,entries,gold,fit_cases,stage)
    tok_fit=_fit_token_centroids({stage:vectors},metadata,gold,fit_cases,stage)
    records=[]
    for i in eval_cases:
        e=entries[i];candidate_ids=by_case[i]
        neural=_select_neural(weights,vectors,metadata,candidate_ids)
        guess=v26._locate(text_locator,e['prompt'])
        text_pair=(tuple(next((j for j in candidate_ids if tuple(metadata[j]['span'])==
                         tuple(guess[role]['span'])),None) for role in ('first','second'))
                      if guess else None)
        # Intentionally no gold references in inference. Gold is consulted below.
        for policy,pair in [('neural_only',neural),('text_template_control',text_pair)]:
            valid=pair is not None and len(pair)==2 and all(j is not None for j in pair)
            decoded=[_decode_candidate(vectors,metadata[j],tok_fit,tok)
                     for j in pair] if valid else []
            a,b=(decoded[0][0],decoded[1][0]) if valid else (None,None)
            correct_spans=bool(valid and pair==gold[i])
            exact_pair=bool(correct_spans and a==Fraction(e['prompt'][slice(*e['_oracle'][0])])
                                   and b==Fraction(e['prompt'][slice(*e['_oracle'][1])]))
            answer=a-b if a is not None and b is not None else None
            truth=e['target']
            records.append({'variant':variant,'stage':stage,'scope':scope,'kind':e['kind'],
              'id':e['id'],'row_index':e['row_index'],'policy':policy,
              'candidate_count':len(candidate_ids),'role_spans_correct':int(correct_spans),
              'first_exact':int(correct_spans and a is not None and a==Fraction(e['prompt'][slice(*e['_oracle'][0])])),
              'second_exact':int(correct_spans and b is not None and b==Fraction(e['prompt'][slice(*e['_oracle'][1])])),
              'both_operands_exact':int(exact_pair),'exact_difference':int(correct_spans and answer==truth) if answer is not None else 0,
              'sign_correct':int(correct_spans and (answer>0)-(answer<0)==(truth>0)-(truth<0)) if answer is not None else 0,
              'exact_zero':int(correct_spans and answer==0 and truth==0) if answer is not None else 0,
              'out_of_vocabulary_operand':int(valid and not all(d[2] for d in decoded)),
              'reconstructed_first':str(a) if a is not None else '',
              'reconstructed_second':str(b) if b is not None else ''})
    summary=[]
    for policy in ('neural_only','text_template_control'):
        subset=[r for r in records if r['policy']==policy]
        truth_signs=[int((entries[i]['target']>0)-(entries[i]['target']<0)) for i in eval_cases]
        for r,sign in zip(subset,truth_signs):r['target_sign']=sign
        grouped=defaultdict(list)
        for i,r in zip(eval_cases,subset):
            if entries[i]['kind']=='original':
                grouped[entries[i]['id'].rsplit('.',2)[0]].append(r['exact_difference'])
        complete=sum(len(g)==6 and all(g) for g in grouped.values())
        summary.append({'variant':variant,'stage':stage,'scope':scope,'policy':policy,
          'complete_six_exact':int(complete),
          'zero_cases':sum(s==0 for s in truth_signs),
          'negative_cases':sum(s<0 for s in truth_signs),
          'positive_cases':sum(s>0 for s in truth_signs),
          'negative_sign_correct':sum(r['sign_correct'] for r in subset if r['target_sign']<0),
          'positive_sign_correct':sum(r['sign_correct'] for r in subset if r['target_sign']>0),
          'cases':len(subset),'role_spans_correct':sum(r['role_spans_correct'] for r in subset),
          'both_operands_exact':sum(r['both_operands_exact'] for r in subset),
          'exact_difference':sum(r['exact_difference'] for r in subset),
          'sign_correct':sum(r['sign_correct'] for r in subset),
          'zero_exact':sum(r['exact_zero'] for r in subset),
          'oov_cases':sum(r['out_of_vocabulary_operand'] for r in subset)})
    return summary,records


def _run(args):
    import torch
    from tools import nanojev_gamer_v1_handoff as gamer
    _require(args.mode=='audit' and args.capture_batch in (1,2,3,4,5,6,7,8)
             and args.max_candidates>=4 and 1<=args.max_candidate_tokens<=64
             and args.max_input_tokens>0 and args.stress_limit>=0
             and (not str(args.device).startswith('cuda') or torch.cuda.is_available()),
             'BAD_CONFIG_OR_DEVICE')
    sources=[Path(getattr(args,k)) for k in ('run','expanded_run','input','source_features',
        'prior','v11_run','v14_run','v19_run','v20_run','v21_run','v22_run',
        'v23_run','v24_run','v25_run','v26_run')]
    out=lab._output(args.output,sources)
    v25_report,train,dev,rows,prompts,_,info=v26._verify_source(args)
    report26=Path(args.v26_run)/'report.json'
    _require(report26.is_file(),'V26_REPORT_MISSING')
    r26=json.loads(report26.read_text(encoding='utf-8'))
    _require(r26.get('schema')==v26.SCHEMA and
             r26.get('status')=='READ_ONLY_INDEPENDENT_SPAN_AUDIT_NO_PROMOTION'
             and r26.get('v25_report_sha256')==v19._sha(Path(args.v25_run)/'report.json')
             and r26.get('v25_activation_archive_sha256')==v25_report.get('activation_archive_sha256')
             and r26.get('frozen_lineage')==info['lineage']
             and r26.get('source_dataset_sha256')==info['dataset_sha256']
             and r26.get('test_cases_used')==0
             and r26.get('original_zero_line_authority_unchanged') is True
             and r26.get('production_weights_modified') is False and
             r26.get('promotion_authorized') is False,'V26_PROVENANCE_MISMATCH')
    fit,hold,hold_groups=v24._group_fit_split(train)
    _require(args.max_input_tokens<=info['lineage']['configuration']['max_length'],
             'INPUT_LENGTH_POLICY_EXCEEDED')
    entries=_entries(rows,prompts,len(train),args.stress_limit)
    for e in entries:
        _require(len(_numeric_candidates(e['prompt']))<=args.max_candidates,
                 'CANDIDATE_BUDGET_TOO_SMALL')
    selector,_=v26._fit_locator(train,prompts,fit)
    scopes={'heldout_in_range_train_groups':hold,
            'dev_out_of_range':list(range(len(train),len(rows))),
            'dev_distractor_and_reordering':list(range(len(rows),len(entries)))}
    watched=[Path(info['lineage']['checkpoint_path']),
             Path(args.v25_run)/'report.json',report26,
             Path(args.v25_run)/'base_ordered_operand_tokens.npz',
             Path(args.v25_run)/'lora_ordered_operand_tokens.npz']
    hashes={str(p):v19._sha(p) for p in watched}
    _require(spine.adapter_identity(Path(info['lineage']['adapter_dir']))==
             info['lineage']['adapter_sha256'] and
             hashes[str(watched[0])]==info['lineage']['checkpoint_sha256'],
             'WEIGHTS_LINEAGE_MISMATCH')
    spine.cache_policy(args.allow_model_download)
    all_summary=[];all_cases=[];captures={};capture_metadata={}
    original_parity={}
    for variant in ('base','lora'):
        _,tok,model=gamer.model_components(info['lineage']['model_id'],args.device,
               adapter_dir=Path(info['lineage']['adapter_dir']) if variant=='lora' else None)
        model.eval();model.requires_grad_(False)
        metadata,vectors=_capture_variant(torch,tok,model,entries,variant=variant,
            device=args.device,batch=args.capture_batch,
            max_input_tokens=args.max_input_tokens,
            max_candidates=args.max_candidates,
            max_candidate_tokens=args.max_candidate_tokens)
        by_case,gold=_gold_candidate_ids(entries,metadata)
        original_parity[variant]=_v25_independent_capture_parity(
            Path(args.v25_run)/(variant+'_ordered_operand_tokens.npz'),
            metadata,vectors,gold,rows)
        for stage in STAGES:
            for scope,indices in scopes.items():
                summary,detail=_evaluate(entries,metadata,vectors[stage],by_case,gold,
                       fit,indices,tok,selector,variant=variant,stage=stage,scope=scope)
                all_summary.extend(summary);all_cases.extend(detail)
                print('V27_AUDIT '+probe.canonical({'variant':variant,'stage':stage,
                      'scope':scope,'neural_pair':summary[0]['both_operands_exact'],
                      'template_pair':summary[1]['both_operands_exact']}),flush=True)
        captures[variant]={s:vectors[s] for s in STAGES}
        captures[variant]['token_ids']=np.asarray([i for c in metadata for i in c['token_ids']],dtype=np.int64)
        capture_metadata[variant]=metadata
        del model,tok,vectors
        gc.collect()
        if torch.cuda.is_available():torch.cuda.empty_cache()
    _require(all(v19._sha(Path(p))==digest for p,digest in hashes.items()) and
             spine.adapter_identity(Path(info['lineage']['adapter_dir']))==info['lineage']['adapter_sha256'],
             'HISTORICAL_SOURCE_OR_ADAPTER_CHANGED_DURING_AUDIT')
    out.mkdir(parents=True,exist_ok=False)
    from tools.nanojev_gamer_v1_conflict_projection_v10 import _csv
    _csv(out/'all_candidate_end_to_end_comparison.csv',all_summary)
    _csv(out/'all_candidate_case_results.csv',all_cases)
    archive_hashes={}
    for variant in ('base','lora'):
        path=out/(variant+'_all_candidate_activations.npz')
        np.savez_compressed(path,**captures[variant])
        archive_hashes[variant]=v19._sha(path)
        (out/(variant+'_candidate_offsets.json')).write_text(json.dumps(capture_metadata[variant],
                              separators=(',',':'))+'\n',encoding='utf-8')
    report={'schema':SCHEMA,'status':'READ_ONLY_ALL_CANDIDATES_NO_PROMOTION',
       'algorithm':'TRAIN_SUPERVISED_INDEPENDENT_NEURAL_AND_TEXT_SPAN_SELECTORS_ALL_CANDIDATE_CAPTURE',
       'frozen_lineage':info['lineage'],'v25_report_sha256':hashes[str(Path(args.v25_run)/'report.json')],
       'v26_report_sha256':hashes[str(report26)],'activation_archive_sha256':archive_hashes,
       'capture_cases':len(entries),'original_capture_max_abs_difference':original_parity,
       'fit_train_groups':len(fit)//6,
       'heldout_in_range_groups':hold_groups,'stress_limit_per_condition':args.stress_limit,
       'test_cases_forwarded':0,'readout_fit_on_dev_or_test':False,
       'no_qwen_lora_or_head_training':True,'production_weights_modified':False,
       'checkpoint_saved':False,'promotion_authorized':False,
       'comparisons':all_summary,
       'limits':['TRAIN-fitted neural selector is a small centroid baseline, not a certified universal parser',
           'TRAIN-only token centroids cannot reconstruct never-seen token IDs',
           'DEV stress prompts are predeclared perturbations of known DEV cases',
           'Text-template locator is a distinct input-text control, not learned Qwen understanding',
           'Exact Fraction subtraction is a diagnostic calculator, not neural arithmetic',
           'No production router changed and no original zero-line competence weakened']}
    (out/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True,
                        allow_nan=False)+'\n',encoding='utf-8')
    print('V27_COMPLETE '+probe.canonical({'status':report['status'],
          'cases':len(entries),'comparisons':len(all_summary),'promotion':False,
          'report':str(out/'report.json')}),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('audit',),default='audit')
    for key in ('run','expanded-run','input','source-features','prior','v11-run',
         'v14-run','v19-run','v20-run','v21-run','v22-run','v23-run','v24-run',
         'v25-run','v26-run','output'):
        p.add_argument('--'+key,required=True,type=Path)
    p.add_argument('--device',default='cuda')
    p.add_argument('--capture-batch',type=int,default=2)
    p.add_argument('--max-input-tokens',type=int,default=1024)
    p.add_argument('--max-candidates',type=int,default=16)
    p.add_argument('--max-candidate-tokens',type=int,default=16)
    p.add_argument('--stress-limit',type=int,default=48)
    p.add_argument('--allow-model-download',action='store_true')
    return p


def main(argv=None):
    _run(parser().parse_args(argv));return 0


if __name__=='__main__':raise SystemExit(main())
