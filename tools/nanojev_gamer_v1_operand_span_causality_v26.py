#!/usr/bin/env python3
"""V26: TRAIN-fit independent operand span/role locator + frozen V25 token decoding.

Read-only. The locator sees RAW prompts, not answer metadata or oracle spans at
inference. V25 archived ONLY the oracle-selected operand activations. Therefore
cached token reconstruction is only physically available when the independent
locator selects the *same token positions*. A wrong selection is counted as a
failure; no oracle feature is silently substituted. Text-only exact arithmetic
is a parser upper bound, NOT model arithmetic or proof of general competence.
"""
from __future__ import annotations
import argparse
import ast
from collections import Counter, defaultdict
import csv
from fractions import Fraction
import json
from math import log
from pathlib import Path
import re

import numpy as np
from tools import nanojev_gamer_v1_ordered_token_causality_v25 as v25
from tools import nanojev_gamer_v1_operand_token_causality_v24 as v24
from tools import nanojev_gamer_v1_operand_causality_v23 as v23
from tools import nanojev_gamer_v1_sign_information_probe as probe
from tools import nanojev_gamer_v1_head_only_lab as lab
from tools import nanojev_gamer_v1_precision_continuation_v19 as v19

SCHEMA='nanojev.gamer.v1.operand-span-causality.v26'
NUMBER=re.compile(r'(?<![A-Za-z0-9_.])(?:[+-]?\d+/\d+|[+-]?\d+(?:\.\d+)?)')
STAGES=v25.STAGES


def _require(condition,reason):
    if not condition:raise ValueError('V26_'+reason)


def _candidates(prompt):
    """Collect number spans without looking up an oracle or prompt answer."""
    result=[]
    for match in NUMBER.finditer(prompt):
        span=match.span()
        if v25._parse_decoded_number(match.group()) is None:continue
        preceding=prompt[max(0,span[0]-80):span[0]]
        payoff=re.search(r'\b(CC|CD|DC|DD)\s*=\s*$',preceding)
        # Cues are lexical features; arithmetic-role mappings are learned on TRAIN.
        marker=('payoff_'+payoff.group(1)) if payoff else (
            'coord_beacon' if 'navigation beacon' in preceding else
            'coord_spacecraft' if 'spacecraft' in preceding else
            'plain_numeric')
        result.append({'span':span,'value':match.group(),'marker':marker,
                       'index':len(result)})
    return result


def _features(prompt,candidate,total):
    """No numeric values or numeric digits as learned predictor features."""
    surface=('game' if 'CC=' in prompt and 'DC=' in prompt else
             'native' if 'signed coordinate' in prompt and 'navigation beacon' in prompt
             else 'unknown')
    return (surface,candidate['marker'],candidate['index'],total)


def _fit_locator(train_rows,prompts,fit_indices):
    """Supervised, template-local categorical ranker fitted on TRAIN ONLY."""
    _require(bool(fit_indices),'EMPTY_LOCATOR_FIT')
    counts=defaultdict(Counter);seen=set();training=[]
    for i in fit_indices:
        row=train_rows[i]; _require(row['split']=='train','LOCATOR_FIT_NOT_TRAIN')
        prompt=prompts[row['archive_index']]
        first,second,_,_=v24._spans(prompt,row['surface'])
        candidates=_candidates(prompt)
        _require(sum(x['span']==first for x in candidates)==1 and
                 sum(x['span']==second for x in candidates)==1,
                 'TRAIN_OPERAND_NOT_CANDIDATE')
        for candidate in candidates:
            label=('FIRST' if candidate['span']==first else
                   'SECOND' if candidate['span']==second else 'OTHER')
            key=_features(prompt,candidate,len(candidates))
            counts[key][label]+=1;seen.add(key)
            training.append((key,label))
    _require(len(seen)>=4,'DEGENERATE_TRAIN_LOCATOR')
    return counts,training


def _locate(model,prompt):
    """No ground-truth surface, span, target, sign or operand value is supplied."""
    candidates=_candidates(prompt)
    if len(candidates)<2:return None
    # If an unseen context occurs, refuse to hallucinate a role assignment.
    if any(_features(prompt,c,len(candidates)) not in model for c in candidates):
        return None
    def score(i,label):
        counts=model[_features(prompt,candidates[i],len(candidates))]
        # train-derived label likelihood with fixed additive smoothing
        return log((counts[label]+0.25)/(sum(counts.values())+0.75))
    ranked=sorted(((score(i,'FIRST')+score(j,'SECOND'),i,j)
                   for i in range(len(candidates))
                   for j in range(len(candidates)) if i!=j),reverse=True)
    best=ranked[0]
    return {'first':candidates[best[1]],'second':candidates[best[2]],
            'pair_score':best[0],'candidate_count':len(candidates)}


def _locator_trials(model,rows,prompts,fit,eval_indices,scope):
    _require(not set(fit)&set(eval_indices),'LOCATOR_TRAIN_HOLDOUT_LEAKAGE')
    trials=[]
    for i in eval_indices:
        row=rows[i];prompt=prompts[row['archive_index']]
        guess=_locate(model,prompt)
        # Oracle locations appear ONLY in evaluation/scoring, after inference.
        f,s,_,_=v24._spans(prompt,row['surface'])
        spans=tuple(x['span'] for x in (guess['first'],guess['second'])) if guess else ()
        a,b=(v25._parse_decoded_number(x['value'])
             for x in (guess['first'],guess['second'])) if guess else (None,None)
        target=Fraction(row['target_fraction'])
        text_difference=a-b if a is not None and b is not None else None
        trials.append({'row_index':i,'id':row['id'],'group':row['group'],
            'surface':row['surface'],'evaluation':scope,
            'first_char_span':str(spans[0]) if guess else '',
            'second_char_span':str(spans[1]) if guess else '',
            'first_text':str(a) if a is not None else '',
            'second_text':str(b) if b is not None else '',
            'candidate_count':guess['candidate_count'] if guess else 0,
            'first_span_correct':int(bool(guess) and spans[0]==f),
            'second_span_correct':int(bool(guess) and spans[1]==s),
            'ordered_spans_correct':int(bool(guess) and spans==(f,s)),
            'text_only_exact_difference':int(text_difference==target),
            'text_only_sign_correct':int(text_difference is not None and
                  (text_difference>0)-(text_difference<0)==row['target_sign']),
            '_spans':spans,'_values':(a,b)})
    return trials


def _read_csv(path):
    with path.open(newline='',encoding='utf-8') as handle:return list(csv.DictReader(handle))


def _verify_source(args):
    info,_=probe._verify_archive(Path(args.input)/probe.CAPTURE_NAME,
                                 require_source=True)
    train=v23._operand_rows(info,'train');dev=v23._operand_rows(info,'dev')
    rows=train+dev;prompts=v24._indexed_prompts(info)
    sha24,_=v25._validated_v24(args,{'probe_info':info},train,dev)
    path=Path(args.v25_run)/'report.json'
    _require(path.is_file(),'V25_REPORT_MISSING')
    report=json.loads(path.read_text(encoding='utf-8'))
    flags={
      'schema':report.get('schema')==v25.SCHEMA,
      'status':report.get('status')=='READ_ONLY_ORDERED_TOKENS_COMPLETE_NO_PROMOTION',
      'v24_hash':report.get('v24_report_sha256')==sha24,
      'lineage':report.get('frozen_lineage')==info['lineage'],
      'dataset':report.get('source_dataset_sha256')==info['dataset_sha256'],
      'cases':report.get('capture_cases')==len(rows)==432,
      'stages':report.get('capture_stages')==list(STAGES),
      'splits':report.get('splits')==['train','dev'],
      'fit':report.get('fit_train_groups')==len(v24._group_fit_split(train)[0])//6,
      'archive_integrity':report.get('source_checkpoints_unchanged') is True and
          report.get('tokenizer_roundtrip_both_exact')=={'base':len(rows),'lora':len(rows)} and
          {x.get('variant') for x in report.get('archive_final_last_parity',[])}=={'base','lora'} and
          all(float(x.get('max_abs_difference',1))==0. for x in
              report.get('archive_final_last_parity',[])) and
          all(float(x)==0. for x in report.get('embedding_lookup_max_abs_difference',{}).values()) and
          set(report.get('embedding_lookup_max_abs_difference',{}))=={'base','lora'},
      'freeze':all((report.get('no_qwen_or_lora_training') is True,
                    report.get('no_specialist_or_original_head_training') is True,
                    report.get('checkpoint_saved') is False,
                    report.get('production_weights_modified') is False,
                    report.get('promotion_authorized') is False,
                    report.get('readout_fit_on_dev_or_test') is False,
                    report.get('token_identity_centroids_fit_on_train_only') is True,
                    report.get('activation_archives_contain_only_train_and_dev') is True,
                    report.get('test_cases_forwarded')==0)),
    }
    _require(all(flags.values()),'V25_PROVENANCE_MISMATCH: '+','.join(
        k for k,v in flags.items() if not v))
    spans_path=Path(args.v25_run)/'token_spans_and_input_roundtrip.csv'
    _require(spans_path.is_file(),'V25_SPANS_MISSING')
    span_csv=_read_csv(spans_path)
    _require(len(span_csv)==2*len(rows),'V25_SPAN_COUNT_MISMATCH')
    for variant in ('base','lora'):
        actual=[s for s in span_csv if s['variant']==variant]
        _require(len(actual)==len(rows) and all(s['id']==r['id'] and
            s['prompt_sha256']==r['prompt_sha256'] for s,r in zip(actual,rows)),
            'V25_SPAN_FINGERPRINT_OR_ORDER_MISMATCH_'+variant)
    return report,train,dev,rows,prompts,span_csv,info


def _verify_archive(npz,report,variant,rows,spans):
    path=npz
    _require(path.is_file() and v19._sha(path)==report['activation_archive_sha256'][variant],
             'V25_ACTIVATION_ARCHIVE_HASH_MISMATCH_'+variant)
    with np.load(path,allow_pickle=False) as z:
        tensors={stage:z[stage].copy() for stage in STAGES}
        ids=z['operand_token_ids'].copy();mask=z['token_mask'].copy()
        indices=z['archive_indices'].copy()
    _require(ids.shape==mask.shape and ids.shape[:2]==(len(rows),2) and
             np.array_equal(indices,np.asarray([r['archive_index'] for r in rows])) and
             set(np.unique(mask))<={0,1},'ARCHIVE_INDEX_MASK_OR_SHAPE_MISMATCH')
    _require(all(x.shape[:3]==ids.shape and np.isfinite(x).all()
                 for x in tensors.values()),'ARCHIVE_TENSOR_INVALID')
    meta=[s for s in spans if s['variant']==variant]
    for i,s in enumerate(meta):
        for role,name in enumerate(('first','second')):
            stored=json.loads(s[name+'_token_ids'])
            n=int(mask[i,role].sum())
            _require(n==len(stored) and
                     ids[i,role,:n].tolist()==stored and
                     np.all(ids[i,role,n:]==-1),
                     'ARCHIVE_TOKEN_ID_SPAN_MISMATCH_'+variant)
    return tensors,ids,mask,meta


def _role_token_match(tok,prompt,trial,meta):
    if not trial['_spans']:return False
    encoding=tok(prompt,return_offsets_mapping=True,add_special_tokens=True,
                truncation=False)
    offsets=encoding['offset_mapping']
    # This is an archive-coverage check, not a locator input. Incorrect
    # predictions have no cached activation and are marked failures.
    if tuple(trial['_spans']) != (tuple(ast.literal_eval(meta['first_char_span'])),
                                  tuple(ast.literal_eval(meta['second_char_span']))):
        return False
    try:
        selected=[v24._aligned_tokens(offsets,s) for s in trial['_spans']]
    except (ValueError,IndexError):return False
    oracle=[json.loads(meta[x+'_token_indices']) for x in ('first','second')]
    if selected!=oracle:return False
    # A new tokenizer vocabulary cannot be silently substituted for the one
    # used in V25. Exact identity is required before decoding archived vectors.
    fresh_ids=encoding['input_ids']
    for role,name in enumerate(('first','second')):
        expected_ids=json.loads(meta[name+'_token_ids'])
        _require([int(fresh_ids[j]) for j in selected[role]]==expected_ids,
                 'TOKENIZER_IDS_CHANGED_SINCE_V25')
    return True


def _metrics(rows,trial_records,token_records,*,variant,stage,scope):
    _require(len(trial_records)==len(token_records),'MISSING_TOKEN_RECONSTRUCTION')
    correct_spans=exact_text=pair_exact=answer_exact=sign_correct=zero=zero_hit=0
    negative=positive=negative_hit=positive_hit=0
    groups=defaultdict(list);details=[]
    for trial,token in zip(trial_records,token_records):
        row=rows[trial['row_index']]
        _require(token['id']==row['id'] and token['variant']==variant and
                token['stage']==stage and token['evaluation']==scope,
                'TOKEN_RECONSTRUCTION_JOIN_MISMATCH')
        located=bool(trial['ordered_spans_correct'])
        # token positions MUST be independently predicted before considering
        # the archived oracle-cropped activation as available.
        usable=bool(trial.get('_archived_positions_match'))
        a=v25._parse_decoded_number(token['first_reconstructed']) if usable else None
        b=v25._parse_decoded_number(token['second_reconstructed']) if usable else None
        difference=a-b if a is not None and b is not None else None
        truth=Fraction(row['target_fraction']);sign=int((truth>0)-(truth<0))
        exact_pair=(a==Fraction(row['first_operand']) and
                    b==Fraction(row['second_operand'])) if usable else False
        good_answer=difference==truth if difference is not None else False
        good_sign=int((difference>0)-(difference<0))==sign if difference is not None else False
        correct_spans+=int(located);exact_text+=trial['text_only_exact_difference']
        pair_exact+=int(exact_pair);answer_exact+=int(good_answer)
        sign_correct+=int(good_sign)
        if sign==0:zero+=1;zero_hit+=int(good_answer)
        if sign<0:negative+=1;negative_hit+=int(good_sign)
        if sign>0:positive+=1;positive_hit+=int(good_sign)
        groups[row['group']].append((good_answer,good_sign,located))
        details.append({'variant':variant,'stage':stage,'evaluation':scope,
            **{k:v for k,v in trial.items() if not k.startswith('_')},
            'archived_activation_available':int(usable),
            'token_first_reconstructed':str(a) if a is not None else '',
            'token_second_reconstructed':str(b) if b is not None else '',
            'exact_pair_recovered':int(exact_pair),
            'exact_rational_difference':int(good_answer),
            'exact_sign':int(good_sign),
            'zero_line_exact':int(sign==0 and good_answer)})
    _require(all(len(g)==6 for g in groups.values()),'INCOMPLETE_SIX_CASE_GROUP')
    complete_exact=sum(all(x[0] for x in g) for g in groups.values())
    complete_sign=sum(all(x[1] for x in g) for g in groups.values())
    return {'variant':variant,'stage':stage,'evaluation':scope,
            'cases':len(trial_records),'independent_ordered_spans_correct':correct_spans,
            'independent_text_parser_exact_difference':exact_text,
            'cached_pair_exact_after_independent_span':pair_exact,
            'cached_difference_exact_after_independent_span':answer_exact,
            'cached_sign_correct_after_independent_span':sign_correct,
            'cached_zero_exact_after_independent_span':zero_hit,
            'zero_cases':zero,'negative_cases':negative,'positive_cases':positive,
            'negative_sign_correct':negative_hit,'positive_sign_correct':positive_hit,
            'complete_six_exact':complete_exact,
            'complete_six_sign':complete_sign},details


def _run(args):
    _require(args.mode=='audit','TRAINING_REFUSED')
    source_paths=[Path(getattr(args,k)) for k in ('run','expanded_run','input',
      'source_features','prior','v11_run','v14_run','v19_run','v20_run',
      'v21_run','v22_run','v23_run','v24_run','v25_run')]
    out=lab._output(args.output,source_paths)
    report25,train,dev,rows,prompts,span_rows,info=_verify_source(args)
    fit,hold,hold_groups=v24._group_fit_split(train)
    model,fit_rows=_fit_locator(train,prompts,fit)
    scopes=[('heldout_in_range_train_groups',hold),
            ('dev_out_of_range',list(range(len(train),len(rows))))]
    trials={s:_locator_trials(model,rows,prompts,fit,ix,s) for s,ix in scopes}
    # Only the tokenizer is loaded; no Qwen/LoRA model or forward pass occurs.
    from transformers import AutoTokenizer
    tokenizer=AutoTokenizer.from_pretrained(info['lineage']['model_id'],use_fast=True,
                        local_files_only=not args.allow_tokenizer_download,
                        trust_remote_code=False)
    _require(tokenizer.is_fast,'FAST_TOKENIZER_REQUIRED')
    summaries=[];all_details=[];token_summary=[];locator_details=[]
    archived_specialists=json.loads((Path(args.v23_run)/'report.json').read_text(encoding='utf-8'))
    archived_baselines=[x for x in archived_specialists['decoder_scores']
                        if x['model'] in ('v14_post_consolidation',
                            'v19_precision_joint','v21_shadow_precision')]
    for variant in ('base','lora'):
        path=Path(args.v25_run)/(variant+'_ordered_operand_tokens.npz')
        tensors,ids,mask,spans=_verify_archive(path,report25,variant,rows,span_rows)
        for scope,indexes in scopes:
            for trial in trials[scope]:
                trial['_archived_positions_match']=_role_token_match(
                    tokenizer,prompts[rows[trial['row_index']]['archive_index']],
                    trial,spans[trial['row_index']])
            for stage in STAGES:
                identity,token_items=v25._token_identity_recovery(tensors,ids,mask,
                  fit,indexes,rows,tokenizer,variant=variant,stage=stage,scope=scope)
                summary,case_items=_metrics(rows,trials[scope],token_items,
                               variant=variant,stage=stage,scope=scope)
                # Oracle-span outcomes are a separately labeled upper bound.
                summary['oracle_span_token_both_operands_exact']=identity[
                  'both_operands_exact_from_token_ids']
                summary['oracle_span_token_both_sequences_exact']=identity[
                  'both_token_sequences_exact']
                summary['oracle_span_known_token_id_accuracy']=identity[
                  'known_token_identity_accuracy']
                summary['oracle_span_oov_positions']=identity['unseen_token_positions']
                summaries.append(summary);all_details.extend(case_items)
                token_summary.append(identity)
                print('V26_AUDIT '+probe.canonical({'variant':variant,'stage':stage,
                  'scope':scope,'located':summary['independent_ordered_spans_correct'],
                  'exact':summary['cached_difference_exact_after_independent_span'],
                  'oracle_pair':identity['both_operands_exact_from_token_ids']}),flush=True)
        del tensors,ids,mask
    for scope in trials:
        locator_details.extend({k:v for k,v in r.items() if not k.startswith('_')}
                               for r in trials[scope])
    report={'schema':SCHEMA,'status':'READ_ONLY_INDEPENDENT_SPAN_AUDIT_NO_PROMOTION',
      'algorithm':'TRAIN_SUPERVISED_PROMPT_SPAN_AND_ROLE_LOCATION_PLUS_V25_CACHED_TOKEN_RECOVERY',
      'v25_report_sha256':v19._sha(Path(args.v25_run)/'report.json'),
      'v25_activation_archive_sha256':report25['activation_archive_sha256'],
      'source_dataset_sha256':info['dataset_sha256'],'frozen_lineage':info['lineage'],
      'train_fit_group_count':len(fit)//6,'heldout_in_range_groups':hold_groups,
      'evaluation_cases':{'heldout':len(hold),'dev':len(dev)},
      'test_cases_used':0,'predictor_fit_on_dev_or_test':False,
      'operator_fixed_subtraction_only':True,
      'frozen_specialist_reference_from_verified_v23':archived_baselines,
      'readout_checkpoint_saved':False,
      'no_qwen_lora_or_head_forward_or_training':True,'production_weights_modified':False,
      'original_zero_line_authority_unchanged':True,'promotion_authorized':False,
      'locator_cues':['numeric_candidate','preceding_lexical_marker',
                      'candidate_index','candidate_count','prompt_format'],
      'audits':summaries,'limits':[
       'Independent locator learns existing two prompt templates; it does not prove general operand finding',
       'The input prompt is used to identify spans, not the Qwen hidden states',
       'V25 archives store activations ONLY at oracle-cropped operands. On a wrong independent span the correct feature cannot be retrieved; it is counted as failure',
       'Recovered token IDs rely on TRAIN-seen centroids; unseen IDs are not recovered',
       'Exact Fraction subtraction is a deterministic diagnostic calculator, not a learned Qwen numerical computation',
       'Oracle-span results are separately labeled upper-bound controls, not independent inference',
       'No DEV or previously seen TEST fitting or hyperparameter selection',
       'No modification to production routing, zero-line authority, Qwen, LoRA or head weights']}
    # Do not output an experiment if historical archives changed during read.
    _require(all(v19._sha(Path(args.v25_run)/(s+'_ordered_operand_tokens.npz'))==
       report25['activation_archive_sha256'][s] for s in ('base','lora')),
       'ARCHIVE_CHANGED_DURING_AUDIT')
    out.mkdir(parents=True,exist_ok=False)
    from tools.nanojev_gamer_v1_conflict_projection_v10 import _csv
    _csv(out/'independent_locator_predictions.csv',locator_details)
    _csv(out/'end_to_end_case_results.csv',all_details)
    _csv(out/'end_to_end_comparison.csv',summaries)
    _csv(out/'oracle_span_token_controls.csv',token_summary)
    (out/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True,
      allow_nan=False)+'\n',encoding='utf-8')
    print('V26_COMPLETE '+probe.canonical({'status':report['status'],
      'audits':len(summaries),'read_only':True,'promotion':False,
      'report':str(out/'report.json')}),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('audit',),default='audit')
    for key in ('run','expanded-run','input','source-features','prior','v11-run',
       'v14-run','v19-run','v20-run','v21-run','v22-run','v23-run','v24-run',
       'v25-run','output'):
        p.add_argument('--'+key,required=True,type=Path)
    p.add_argument('--allow-tokenizer-download',action='store_true')
    return p


def main(argv=None):
    _run(parser().parse_args(argv));return 0


if __name__=='__main__':raise SystemExit(main())
