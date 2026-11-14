#!/usr/bin/env python3
"""Read-only, stage-by-stage GAMER register recoverability diagnostic.

The registered *08 prompt is the ONLY model-side input. Oracle registers are
used solely as supervision/evaluation labels. Every probe is fitted on TRAIN;
DEV never selects features, regularization, layers, or a model. Neither Qwen,
LoRA, C0 nor ridge production weights are updated. The probes are diagnostic
and role-conditioned, NOT deployable register extraction or proof of loss of
information in a transformer representation.
"""
from __future__ import annotations

from collections import defaultdict, Counter
from datetime import datetime, timezone
from fractions import Fraction
import csv
import hashlib
import json
import math
from pathlib import Path
import re

import numpy as np

from tools.nanojev_gamer_v1_register_oracle import build_oracle
from tools.nanojev_gamer_v1_prompt_fidelity import NUM_RE
from tools.nanojev_gamer_v1_handoff import original_prompt
from tools.nanojev_gamer_v1_diagnostic_train import _source_inputs, _output_path

SCHEMA = 'nanojev.gamer.v1.representation-diagnostic.v1'
FEATURES = ('final_token','sequence_mean','number_context_mean','number_position_buckets',
            'number_ordered_32')
DECODERS = ('ridge_linear','ridge_rbf')


def _sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for x in iter(lambda:f.read(1024*1024),b''):
            h.update(x)
    return h.hexdigest()


def _write_json(path,value):
    path.write_text(json.dumps(value,indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf-8')


def _event(path,data):
    with Path(path).open('a',encoding='utf-8') as f:
        f.write(json.dumps(data,sort_keys=True,allow_nan=False)+'\n')


def _csv(path,records,fields):
    with path.open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore')
        writer.writeheader();writer.writerows(records)


def _number_occurrences(prompt):
    out=[]
    for m in NUM_RE.finditer(prompt):
        a,b,value=m.start(),m.end(),m.group()
        if (a>0 and prompt[a-1].isdigit()) or (b<len(prompt) and prompt[b].isdigit()):
            continue
        # The final dot of a sentence is punctuation, not part of a numeric
        # register. Preserve the number without swallowing that punctuation.
        if value.endswith('.'):
            value=value[:-1];b-=1
        out.append((a,b,value))
    return out


def _tokenize(tok,prompt,max_length):
    """Require a lossless fast-tokenizer offset map; no truncation/padding."""
    if not getattr(tok,'is_fast',False):
        raise ValueError('GAMER_REPR_FAST_TOKENIZER_OFFSETS_REQUIRED')
    enc=tok(prompt,return_tensors='pt',return_offsets_mapping=True,
            truncation=False,add_special_tokens=True)
    offsets=enc.pop('offset_mapping')[0].tolist()
    ids=enc['input_ids'][0].tolist()
    if len(ids)>max_length:
        raise ValueError('GAMER_REPR_PROMPT_WOULD_BE_TRUNCATED')
    if len(offsets)!=len(ids):
        raise ValueError('GAMER_REPR_OFFSET_LENGTH_MISMATCH')
    if any(a<0 or b<a or b>len(prompt) for a,b in offsets):
        raise ValueError('GAMER_REPR_INVALID_TOKEN_OFFSETS')
    if 'attention_mask' not in enc:
        raise ValueError('GAMER_REPR_ATTENTION_MASK_MISSING')
    if enc['input_ids'].shape[0]!=1 or not bool(enc['attention_mask'][0].all()):
        raise ValueError('GAMER_REPR_SINGLE_PROMPT_UNPADDED_REQUIRED')
    numbers=_number_occurrences(prompt)
    if not numbers:raise ValueError('GAMER_REPR_NO_NUMERICAL_CANDIDATES')
    numeral_tokens=[]
    evidence=[]
    for a,b,value in numbers:
        covers=[i for i,(lo,hi) in enumerate(offsets) if lo< b and hi>a]
        if not covers or min(offsets[i][0] for i in covers)>a or max(offsets[i][1] for i in covers)<b:
            raise ValueError('GAMER_REPR_NUMERIC_LITERAL_NOT_TOKEN_COVERED')
        numeral_tokens.extend(covers)
        evidence.append({'literal':value,'start':a,'end':b,
                         'token_start':min(covers),'token_end':max(covers)+1,
                         'token_ids':[ids[i] for i in covers],
                         'token_offsets':[offsets[i] for i in covers]})
    # Optional direct decode check. A changed rendering is a stage-A finding,
    # not an excuse to silently normalize a certified prompt.
    decoded=None
    if hasattr(tok,'decode'):
        decoded=tok.decode(ids,skip_special_tokens=True,
                           clean_up_tokenization_spaces=False)
    numeric=sorted(set(numeral_tokens))
    context=sorted({j for i in numeric for j in range(max(0,i-2),min(len(ids),i+3))
                    if offsets[j][1]>offsets[j][0]})
    return enc, {'numbers':evidence,'numeric_positions':numeric,'context_positions':context,
                 'offsets':offsets,'token_count':len(ids),'candidate_count':len(numbers),
                 'tokenizer_roundtrip_exact':decoded==prompt if decoded is not None else None}


def _literal_alignment(prompt,doc,number_evidence):
    """Verify public-register lexical witnesses, never resolve roles by value."""
    out=[]
    for reg in doc['registers']:
        literal=reg['literal_witness']
        if literal is None:
            raise ValueError('GAMER_REPR_UNVERIFIED_ORACLE_REGISTER')
        if reg['kind']=='action_selector':
            status='SYMBOLIC_SELECTOR_IN_PROMPT' if literal in prompt else 'MISSING'
            if status=='MISSING':raise ValueError('GAMER_REPR_SYMBOLIC_SELECTOR_NOT_VISIBLE')
            positions=[]
        else:
            # A register may have multiple identical numerical occurrences.
            # Report ambiguity; do not use oracle values to choose model features.
            matches=[n for n in number_evidence if n['literal']==literal]
            if not matches:
                # The generator's lexical witness can have a mathematically
                # equivalent rendering (e.g. 2 vs 2.0). Verify equivalence.
                matches=[n for n in number_evidence if Fraction(n['literal'].replace('−','-'))==
                         Fraction(str(reg['value']['exact']))]
            if not matches:raise ValueError('GAMER_REPR_PUBLIC_REGISTER_MISSING_FROM_TOKENS')
            positions=[n['token_start'] for n in matches]
            status='MULTIPLE_MATCHING_SPANS_ROLE_UNRESOLVED' if len(matches)>1 else 'LITERAL_TOKEN_COVERED'
        out.append({'case_sha256':doc['case_sha256'],'game_id':doc['game_id'],
                    'register_id':reg['id'],'literal_witness':literal,
                    'status':status,'candidate_token_starts':';'.join(map(str,positions)),
                    'candidate_occurrences':len(positions)})
    return out


def _layers(n_hidden):
    if n_hidden<3:raise ValueError('GAMER_REPR_TOO_FEW_HIDDEN_STATES')
    final=n_hidden-1
    return sorted(set((0,max(1,final//4),max(1,final//2),max(1,3*final//4),final)))


def _representations(hidden,indices,meta):
    h=np.asarray(hidden,dtype=np.float32)
    if h.ndim!=2 or not np.isfinite(h).all() or len(h)!=meta['token_count']:
        raise ValueError('GAMER_REPR_NONFINITE_OR_MISALIGNED_ACTIVATIONS')
    numeric=meta['numeric_positions'];context=meta['context_positions']
    if not numeric or not context:raise ValueError('GAMER_REPR_EMPTY_NUMERIC_TOKENS')
    # Position buckets retain coarse numeric order without using oracle role
    # identity or any source numeric value to select a token.
    slices=np.array_split(np.asarray(numeric,dtype=np.int64),4)
    buckets=[h[b].mean(axis=0) if len(b) else np.zeros(h.shape[1],dtype=np.float32)
             for b in slices]
    # Ordered *numeric-span* features retain individual candidate geometry.
    # The sequence remains model-visible and the same ordering is applied to
    # every prompt; no oracle role/value determines any selection.
    spans=[list(range(n['token_start'],n['token_end'])) for n in meta['numbers']]
    ordered=[h[ids].mean(axis=0) for ids in spans[:32]]
    ordered.extend(np.zeros(h.shape[1],dtype=np.float32) for _ in range(32-len(ordered)))
    return {'final_token':h[-1].copy(),
            'sequence_mean':h.mean(axis=0),
            'number_context_mean':h[context].mean(axis=0),
            'number_position_buckets':np.concatenate(buckets).astype(np.float32),
            'number_ordered_32':np.concatenate(ordered).astype(np.float32)}


def _source_capture_batches(docs, batch_size):
    """Replay cached C0 batches, restarting at each certified train/dev split.

    The numerical-head cache was captured from complete, padded batches of
    prompts, not independent single-tokenizer/model forwards. In particular,
    do not co-batch the final TRAIN case with the first DEV case.
    """
    if batch_size < 1:
        raise ValueError('GAMER_REPR_INVALID_CAPTURE_BATCH')
    start = 0
    while start < len(docs):
        split = docs[start]['split']
        stop = min(start + batch_size, len(docs))
        while stop > start + 1 and docs[stop-1]['split'] != split:
            stop -= 1
        if docs[stop-1]['split'] != split:
            raise ValueError('GAMER_REPR_NONCONTIGUOUS_SPLITS')
        yield start, stop
        start = stop


def _capture_plan(rows, docs, batch_size, source_batch_context=None):
    """Map selected rows back to the actual original cached capture batches.

    A bounded game subset can omit the partner that was in the same padded
    source batch. Reconstruct that partner from the certified source *prompt*
    only, without using oracle labels as model input or training evidence.
    """
    if source_batch_context is None:
        for start,stop in _source_capture_batches(docs,batch_size):
            yield list(range(start,stop)), [original_prompt(r) for r in rows[start:stop]], list(range(stop-start))
        return
    full=source_batch_context['full_rows']; indices=source_batch_context['selected_indices']
    used=[]
    for split in ('train','dev'):
        selected_positions=[i for i,d in enumerate(docs) if d['split']==split]
        positions=indices[split]
        if len(selected_positions)!=len(positions):
            raise ValueError('GAMER_REPR_SOURCE_BATCH_INDEX_COUNT_MISMATCH')
        by_batch=defaultdict(list)
        previous=-1
        for p,source_ix in zip(selected_positions,positions):
            if source_ix<=previous or not 0<=source_ix<len(full[split]):
                raise ValueError('GAMER_REPR_SOURCE_BATCH_INDEX_ORDER_OR_RANGE_ERROR')
            previous=source_ix
            if original_prompt(rows[p])!=original_prompt(full[split][source_ix]):
                raise ValueError('GAMER_REPR_SOURCE_BATCH_PROMPT_MISMATCH')
            group=source_ix//batch_size
            by_batch[group].append((p,source_ix-group*batch_size))
        for group,selected in sorted(by_batch.items()):
            source_begin=group*batch_size
            prompts=[original_prompt(r) for r in full[split][source_begin:source_begin+batch_size]]
            selected.sort()
            chosen=[p for p,_ in selected]
            offsets=[ix for _,ix in selected]
            used.extend(chosen)
            yield chosen,prompts,offsets
    if used!=list(range(len(rows))):
        raise ValueError('GAMER_REPR_SOURCE_CAPTURE_PLAN_DOES_NOT_COVER_SELECTED_CASES')


def _parity_metrics(current, reference):
    a = np.asarray(current, dtype=np.float32)
    b = np.asarray(reference, dtype=np.float32)
    if a.shape != b.shape or a.ndim != 1 or not np.isfinite(a).all() or not np.isfinite(b).all():
        raise ValueError('GAMER_REPR_CACHED_FEATURE_SHAPE_OR_FINITE_MISMATCH')
    delta = a.astype(np.float64) - b.astype(np.float64)
    diff_norm = float(np.linalg.norm(delta))
    ref_norm = float(np.linalg.norm(b.astype(np.float64)))
    return {'max_abs_delta': float(np.abs(delta).max()),
            'mean_abs_delta': float(np.abs(delta).mean()),
            'relative_l2_delta': diff_norm / max(ref_norm, 1e-12),
            'cosine': float(np.dot(a.astype(np.float64), b.astype(np.float64)) /
                            max(float(np.linalg.norm(a.astype(np.float64)))*ref_norm, 1e-12)),
            'within_original_tolerance': bool(np.abs(delta).max() <= .1)}


def _write_capture_parity(output, rows, *, batch_size, mode):
    """Persist the original contract comparison, even on a fail-closed run."""
    if not rows:
        return None
    fields=('mode','split','case_sha256','game_id','capture_batch',
            'max_abs_delta','mean_abs_delta','relative_l2_delta','cosine',
            'within_original_tolerance')
    _csv(output/'capture_parity.csv', rows, fields)
    vals=np.asarray([r['max_abs_delta'] for r in rows],dtype=np.float64)
    failed=[r for r in rows if not r['within_original_tolerance']]
    summary={'mode':mode,'capture_contract':'C0_PADDED_BATCH_LAST_NONPADDING_TOKEN',
             'capture_batch':batch_size,'cases_compared':len(rows),
             'cases_exceeding_original_max_abs_tolerance':len(failed),
             'original_max_abs_tolerance':.1,
             'max_abs_delta':float(vals.max()),'median_case_max_abs_delta':float(np.median(vals)),
             'p90_case_max_abs_delta':float(np.percentile(vals,90)),
             'worst_cases':sorted(rows,key=lambda r:r['max_abs_delta'],reverse=True)[:12],
             'status':'MATCHED_SOURCE_FEATURE_CACHE' if not failed else 'SOURCE_FEATURE_CACHE_MISMATCH'}
    _write_json(output/'capture_parity_report.json',summary)
    return summary


def _capture(torch,model,tok,rows,docs,*,device,max_length,mode,output,
             expected_last=None,capture_batch=1,source_batch_context=None):
    """Capture the *original* padded-batch C0 forward contract, with token spans.

    Token offsets come from a separate tokenizer-only call. They never change
    the actual model inputs. The certified source used tok(list_of_prompts,
    padding=True,truncation=True,...) and model(**encoded,output_hidden_states=
    True,return_dict=True), with the last nonpadding-token hidden state.
    """
    if len(rows)!=len(docs):
        raise ValueError('GAMER_REPR_ROW_ORACLE_ALIGNMENT_FAILED')
    if expected_last is not None and len(expected_last)!=len(rows):
        raise ValueError('GAMER_REPR_SOURCE_FEATURE_ROW_COUNT_MISMATCH')
    model.eval()
    for p in model.parameters():p.requires_grad_(False)
    result=defaultdict(list);token_rows=[];alignment=[];parity=[]
    layer_ids=None;ordered_truncations=0
    for chosen,prompts,source_offsets in _capture_plan(rows,docs,capture_batch,source_batch_context):
        meta_batch=[]
        for k,off in zip(chosen,source_offsets):
            row=rows[k];doc=docs[k];prompt=original_prompt(row)
            try:
                enc_single,meta=_tokenize(tok,prompt,max_length)
            except ValueError as exc:
                raise ValueError('GAMER_REPR_TOKENIZATION_FAILED_CASE '+
                                 doc['case_sha256']+' GAME '+doc['game_id']+': '+str(exc)) from exc
            meta_batch.append((doc,meta,enc_single))
            ordered_truncations+=max(0,meta['candidate_count']-32)
            for n in meta['numbers']:
                token_rows.append({'mode':mode,'split':doc['split'],'case_sha256':doc['case_sha256'],
                                   'game_id':doc['game_id'],'literal':n['literal'],
                                   'char_start':n['start'],'char_end':n['end'],
                                   'token_start':n['token_start'],'token_end':n['token_end'],
                                   'token_ids':';'.join(map(str,n['token_ids'])),
                                   'token_offsets':';'.join(f'{a}:{b}' for a,b in n['token_offsets']),
                                   'tokenizer_roundtrip_exact':meta['tokenizer_roundtrip_exact']})
            for item in _literal_alignment(prompt,doc,meta['numbers']):
                alignment.append(dict(item,mode=mode,split=doc['split']))
        # These arguments are deliberately identical to C0 capture_features.
        batch=tok(prompts,padding=True,truncation=True,max_length=max_length,return_tensors='pt')
        if 'input_ids' not in batch or 'attention_mask' not in batch:
            raise ValueError('GAMER_REPR_C0_BATCH_MISSING_INPUTS')
        if batch['input_ids'].shape[0]!=len(prompts) or tok.padding_side!='right':
            raise ValueError('GAMER_REPR_C0_BATCH_SHAPE_OR_PADDING_MISMATCH')
        lengths=batch['attention_mask'].sum(dim=-1).tolist()
        for off,(_,meta,enc_single) in zip(source_offsets,meta_batch):
            length=int(lengths[off])
            if length!=meta['token_count'] or not bool(torch.equal(
                    batch['input_ids'][off,:length].cpu(),enc_single['input_ids'][0].cpu())):
                raise ValueError('GAMER_REPR_PRODUCTION_TOKEN_IDS_DIFFER_FROM_SPAN_IDS')
            if not bool(batch['attention_mask'][off,:length].all()) or \
                    bool(batch['attention_mask'][off,length:].any()):
                raise ValueError('GAMER_REPR_NON_RIGHT_PADDED_SOURCE_CAPTURE')
        encoded={key:value.to(device) for key,value in batch.items()}
        # Do NOT set use_cache=False: production C0 did not override use_cache.
        with torch.inference_mode():
            out=model(**encoded,output_hidden_states=True,return_dict=True)
        hstates=out.hidden_states
        if layer_ids is None:layer_ids=_layers(len(hstates))
        if max(layer_ids)>=len(hstates):raise ValueError('GAMER_REPR_HIDDEN_LAYER_STRUCTURE_CHANGED')
        for k,off,(doc,meta,_) in zip(chosen,source_offsets,meta_batch):
            length=int(lengths[off])
            for layer in layer_ids:
                h=hstates[layer][off,:length].detach().float().cpu().numpy()
                for f,vector in _representations(h,layer,meta).items():
                    if f=='number_ordered_32' and layer!=layer_ids[-1]:continue
                    result[(layer,f)].append(vector)
                if layer==layer_ids[-1] and expected_last is not None:
                    row=dict(mode=mode,split=doc['split'],case_sha256=doc['case_sha256'],
                             game_id=doc['game_id'],capture_batch=capture_batch,
                             **_parity_metrics(h[-1],expected_last[k]))
                    parity.append(row)
            _event(output/'events.jsonl',{'stage':'capture','mode':mode,'case_position':k,
                    'case_sha256':doc['case_sha256'],'game_id':doc['game_id'],
                    'split':doc['split'],'numeric_candidates':meta['candidate_count'],
                    'tokens':meta['token_count'], 'capture_batch':capture_batch,
                    'tokenizer_roundtrip_exact':meta['tokenizer_roundtrip_exact'],
                    'layer_indices':layer_ids,
                    'cached_last_token_max_abs_delta':parity[-1]['max_abs_delta'] if expected_last is not None else None})
            if (k+1)%50==0:
                print(f'  GAMER REPR {mode}: {k+1}/{len(rows)} captured',flush=True)
        del hstates,out,encoded
    par_summary=_write_capture_parity(output,parity,batch_size=capture_batch,mode=mode)
    if par_summary is not None:
        print('  GAMER REPR capture parity: max_abs_delta=',
              par_summary['max_abs_delta'],'cases_outside_tolerance=',
              par_summary['cases_exceeding_original_max_abs_tolerance'],flush=True)
        if par_summary['cases_exceeding_original_max_abs_tolerance']:
            raise ValueError('GAMER_REPR_FROZEN_LAST_TOKEN_CACHED_FEATURE_MISMATCH: '
                             +str(par_summary['max_abs_delta'])
                             +' (details: '+str(output/'capture_parity_report.json')+')')
    features={key:np.stack(v).astype(np.float32) for key,v in result.items()}
    return {'features':features,'layer_ids':layer_ids,'token_rows':token_rows,
            'alignment':alignment,'number_spans_beyond_ordered_32':ordered_truncations,
            'last_token_cache_max_abs_delta':par_summary['max_abs_delta'] if par_summary else None,
            'capture_parity':par_summary}


def _signed(x):
    a=np.asarray(x,dtype=np.float64)
    return np.sign(a)*np.log1p(np.abs(a))


def _inverse(x):
    a=np.asarray(x,dtype=np.float64)
    return np.sign(a)*np.expm1(np.clip(np.abs(a),0,25))


def _fit_kernel(x,y,*,decoder='ridge_linear',alpha=.1):
    """Two matched, fixed-hyperparameter readouts; train data only."""
    x=np.asarray(x,dtype=np.float64);y=_signed(y)
    if x.ndim!=2 or len(x)!=len(y) or len(x)<2 or not np.isfinite(x).all():
        raise ValueError('GAMER_REPR_BAD_PROBE_TRAIN_DATA')
    mu=x.mean(axis=0);center=x-mu
    norm=max(float(np.sqrt(np.mean(center*center))),1e-6)*math.sqrt(x.shape[1])
    z=center/norm
    if decoder=='ridge_linear':
        K=z@z.T;gamma=None
    elif decoder=='ridge_rbf':
        sq=np.maximum(np.sum(z*z,axis=1)[:,None]+np.sum(z*z,axis=1)[None,:]-2*z@z.T,0.)
        nonzero=sq[np.triu_indices(len(sq),1)]
        median=float(np.median(nonzero[nonzero>1e-12])) if (nonzero>1e-12).any() else 1.
        gamma=1./median
        K=np.exp(-np.minimum(sq*gamma,100.))
    else:raise ValueError('GAMER_REPR_UNKNOWN_DECODER')
    lam=max(alpha*float(np.trace(K))/len(x),1e-6)
    dual=np.linalg.solve(K+np.eye(len(K))*lam,y-y.mean())
    return {'center':mu,'norm':norm,'train':z,'weights':dual,
            'mean_target':float(y.mean()),'decoder':decoder,'gamma':gamma}


def _predict_probe(fitted,x):
    data=np.asarray(x,dtype=np.float64)
    z=(data-fitted['center'])/fitted['norm']
    ref=fitted['train']
    if fitted['decoder']=='ridge_linear':K=z@ref.T
    else:
        sq=np.maximum(np.sum(z*z,axis=1)[:,None]+np.sum(ref*ref,axis=1)[None,:]-2*z@ref.T,0.)
        K=np.exp(-np.minimum(sq*fitted['gamma'],100.))
    return _inverse(K@fitted['weights']+fitted['mean_target'])


def _zone(value):
    v=float(value)
    if v==0:return 'zero'
    if -1<=v<0:return 'small_negative'
    if 0<v<=1:return 'small_positive'
    return 'outer_negative' if v<0 else 'outer_positive'


def _groups(train_docs,dev_docs):
    groups=defaultdict(lambda:{'train':[],'dev':[]})
    for split,docs in (('train',train_docs),('dev',dev_docs)):
        for i,doc in enumerate(docs):
            for reg in doc['registers']:
                groups[(doc['game_id'],reg['id'])][split].append((i,reg))
    return groups


def _probe_all(features,train_docs,dev_docs):
    ntrain=len(train_docs);groups=_groups(train_docs,dev_docs)
    scores=[];cases=[]
    for (mode,layer,feature),x in sorted(features.items()):
        if len(x)!=ntrain+len(dev_docs):raise ValueError('GAMER_REPR_CASE_ALIGNMENT_FAILED')
        for (game,role),sections in sorted(groups.items()):
            tr=sections['train'];dv=sections['dev']
            if len(tr)<3 or not dv:continue
            ti=np.asarray([i for i,_ in tr],dtype=int)
            di=np.asarray([ntrain+i for i,_ in dv],dtype=int)
            yt=np.asarray([float(reg['value']['float']) for _,reg in tr])
            yd=np.asarray([float(reg['value']['float']) for _,reg in dv])
            for decoder in DECODERS:
                fitted=_fit_kernel(x[ti],yt,decoder=decoder)
                train_pred=_predict_probe(fitted,x[ti]); dev_pred=_predict_probe(fitted,x[di])
                null=float(_inverse(np.mean(_signed(yt))))
                score={'mode':mode,'layer':layer,'feature':feature,'decoder':decoder,
                       'game_id':game,'register_id':role,'train_support':len(ti),
                       'dev_support':len(di),
                       'train_mae':float(np.abs(train_pred-yt).mean()),
                       'dev_mae':float(np.abs(dev_pred-yd).mean()),
                       'dev_signed_log_mae':float(np.abs(_signed(dev_pred)-_signed(yd)).mean()),
                       'mean_baseline_mae':float(np.abs(yd-null).mean())}
                scores.append(score)
                for (idx,reg),target,pred in zip(dv,yd,dev_pred):
                    cases.append({'mode':mode,'layer':layer,'feature':feature,
                                  'decoder':decoder,'game_id':game,'register_id':role,
                                  'case_sha256':dev_docs[idx]['case_sha256'],
                                  'target_zone':dev_docs[idx]['target_zone'],
                                  'register_zone':_zone(target),
                                  'target':float(target),'predicted':float(pred),
                                  'absolute_error':float(abs(pred-target)),
                                  'baseline_absolute_error':float(abs(null-target))})
    return scores,cases


def _aggregate(scores,by):
    accum=defaultdict(lambda:{'weight':0,'train':0.,'dev':0.,'baseline':0.,'signed':0.})
    for r in scores:
        k=tuple(r[x] for x in by);w=r['dev_support'];a=accum[k]
        a['weight']+=w;a['train']+=r['train_mae']*w
        a['dev']+=r['dev_mae']*w;a['baseline']+=r['mean_baseline_mae']*w
        a['signed']+=r['dev_signed_log_mae']*w
    return [dict(zip(by,k),**{'dev_registers':a['weight'],'train_mae':a['train']/a['weight'],
                            'dev_mae':a['dev']/a['weight'],
                            'baseline_mae':a['baseline']/a['weight'],
                            'dev_signed_log_mae':a['signed']/a['weight']})
            for k,a in sorted(accum.items())]


def _first_degradations(scores,layer_ids):
    """Earliest *measured probe degradation*, NOT information destruction."""
    look={(r['mode'],r['layer'],r['feature'],r['decoder'],r['game_id'],r['register_id']):r
          for r in scores}
    out=[]
    for k in sorted({(r['mode'],r['game_id'],r['register_id']) for r in scores}):
        mode,game,role=k
        history=[]
        for layer in layer_ids:
            row=look.get((mode,layer,'final_token','ridge_linear',game,role))
            if row:history.append((layer,row['dev_mae'],row['mean_baseline_mae'],row['dev_support']))
        if not history:continue
        best=history[0][1];first=None
        for layer,mae,baseline,n in history[1:]:
            if first is None and best<baseline and mae>best*1.25+.05:
                first=layer
            best=min(best,mae)
        out.append({'mode':mode,'game_id':game,'register_id':role,
                    'first_measured_degradation_layer':first if first is not None else '',
                    'initial_mae':history[0][1],'final_mae':history[-1][1],
                    'development_cases':history[-1][3],
                    'verdict':('EXPLORATORY_PROBE_RECOVERABILITY_DEGRADATION' if first is not None
                               else 'NO_THRESHOLD_CROSSING_OR_INCONCLUSIVE')})
    return out


def _perturb_prompt(row,doc):
    """Change one unambiguous public literal; never use generator labels as input."""
    prompt=original_prompt(row)
    numbers=_number_occurrences(prompt)
    for reg in doc['registers']:
        if reg['kind']=='action_selector':continue
        literal=reg['literal_witness']
        hits=[(a,b) for a,b,value in numbers if value==literal]
        if len(hits)!=1:continue
        a,b=hits[0]
        value=Fraction(literal.replace('−','-'))
        step=Fraction(1) if value.denominator==1 else Fraction(1,10)
        updated=value+step
        replacement=(str(updated.numerator) if updated.denominator==1
                     else format(float(updated),'.12g'))
        return prompt[:a]+replacement+prompt[b:],reg['id'],literal,replacement
    return None


def _perturb_measure(torch,model,tok,rows,docs,*,device,max_length,layer_ids,feature_list,
                     mode,train_docs,train_features,limit=16):
    changes=[]; tested=0
    # Every perturbation probe is fitted once from certified TRAIN only.
    reference=defaultdict(list)
    for i,doc in enumerate(train_docs):
        for reg in doc['registers']:
            reference[(doc['game_id'],reg['id'])].append((i,float(reg['value']['float'])))
    fitted={}
    for row,doc in zip(rows,docs):
        if tested>=limit:break
        altered=_perturb_prompt(row,doc)
        if altered is None:continue
        tested+=1
        prompt,role,old,new=altered
        vectors=[]
        for text in (original_prompt(row),prompt):
            enc,meta=_tokenize(tok,text,max_length)
            encoded={key:value.to(device) for key,value in enc.items()}
            with torch.inference_mode():
                out=model(**encoded,output_hidden_states=True,return_dict=True,use_cache=False)
            h=out.hidden_states[-1][0].detach().float().cpu().numpy()
            vectors.append(_representations(h,layer_ids[-1],meta))
            del out,encoded
        for feature in feature_list:
            before=np.asarray(vectors[0][feature],dtype=np.float64)
            after=np.asarray(vectors[1][feature],dtype=np.float64)
            norm=float(np.linalg.norm(after-before))
            if (mode,feature) not in train_features:
                raise ValueError('GAMER_REPR_PERTURB_TRAIN_FEATURE_MISSING')
            train_x=train_features[(mode,feature)]
            if len(train_x)!=len(train_docs):
                raise ValueError('GAMER_REPR_PERTURB_TRAIN_ALIGNMENT_MISMATCH')
            predictions={}
            for reg in doc['registers']:
                key=(doc['game_id'],reg['id'])
                examples=reference[key]
                if len(examples)<3:continue
                key_model=(feature,key)
                if key_model not in fitted:
                    ix=np.asarray([i for i,_ in examples],dtype=np.int64)
                    targets=np.asarray([v for _,v in examples],dtype=np.float64)
                    fitted[key_model]=_fit_kernel(train_x[ix],targets,decoder='ridge_linear')
                predictor=fitted[key_model]
                predictions[reg['id']]=tuple(float(v) for v in
                    _predict_probe(predictor,np.stack((before,after))))
            if role not in predictions:
                raise ValueError('GAMER_REPR_PERTURBED_REGISTER_PROBE_MISSING')
            other_changes=[abs(p[1]-p[0]) for other,p in predictions.items() if other!=role]
            changes.append({'mode':mode,'case_sha256':doc['case_sha256'],
                            'game_id':doc['game_id'],'changed_register':role,
                            'old_literal':old,'new_literal':new,'feature':feature,
                            'feature_delta_l2':norm,
                            'relative_delta':norm/max(float(np.linalg.norm(before)),1e-9),
                            'cosine':float(np.dot(before,after)/max(float(np.linalg.norm(before))*float(np.linalg.norm(after)),1e-12)),
                            'changed_register_probe_before':predictions[role][0],
                            'changed_register_probe_after':predictions[role][1],
                            'changed_register_predicted_delta':predictions[role][1]-predictions[role][0],
                            'true_literal_delta':float(Fraction(new)-Fraction(old)),
                            'other_registers_evaluated':len(other_changes),
                            'other_register_mean_absolute_prediction_delta':
                                float(np.mean(other_changes)) if other_changes else None})
    return changes


def _preflight_selection(rows, docs, capture_batch, batch_context):
    """Sample source capture batches at both ends of each split *before* any full pass."""
    positions=[]
    offsets={}
    for split in ('train','dev'):
        choices=[i for i,d in enumerate(docs) if d['split']==split]
        n=min(len(choices),2*capture_batch)
        chosen=sorted(set(choices[:n]+choices[-n:]))
        positions.extend(chosen)
        if batch_context is not None:
            # Original index map is aligned with the source-selected split order.
            local_indices={position:j for j,position in enumerate(choices)}
            offsets[split]=[batch_context['selected_indices'][split][local_indices[p]] for p in chosen]
    selected_context=({'full_rows':batch_context['full_rows'],'selected_indices':offsets}
                      if batch_context is not None else None)
    return [rows[i] for i in positions],[docs[i] for i in positions],selected_context,positions


def _checkpoint_integrity(source,run):
    paths=[run/'lora'/'latest.pt',run/'certified_pairs.jsonl',
           source['cycle']/'to_numerical'/'latest.pt']
    adapter=source['source_adapter']
    paths.extend(p for p in adapter.rglob('*') if p.is_file())
    existing=[p for p in paths if p.exists()]
    if not existing:
        raise ValueError('GAMER_REPR_SOURCE_FINGERPRINT_TARGETS_MISSING')
    return {str(p):_sha(p) for p in existing}


def run_representation_diagnostic(run,*,device='cuda',output=None,max_games=0,
                                  train_per_game=0,dev_per_game=0,
                                  perturbations=16,include_base=True):
    if max_games<0 or (train_per_game and train_per_game<3) or dev_per_game<0 or perturbations<0:
        raise ValueError('GAMER_REPR_INVALID_CONTROLS')
    run=Path(run).resolve()
    dest=_output_path(run,output,suffix='representation-diagnostic')
    # Validate before claiming an output or initializing a large transformer.
    source=_source_inputs(run,max_games=max_games,
                          train_per_game=train_per_game,dev_per_game=dev_per_game)
    train_rows=source['selected']['train']['rows'];dev_rows=source['selected']['dev']['rows']
    rows=train_rows+dev_rows
    docs=[build_oracle(r) for r in rows]
    batch_context=({'full_rows':source['full_split_rows'],
                    'selected_indices':{p:source['selected'][p]['indices'] for p in ('train','dev')}}
                   if 'full_split_rows' in source else None)
    capture_batch=int(source['head_hp'].get('capture_batch',2))
    if capture_batch<1:
        raise ValueError('GAMER_REPR_INVALID_SOURCE_CAPTURE_BATCH')
    before=_checkpoint_integrity(source,run)
    dest.mkdir(parents=True,exist_ok=False)
    stage='setup'
    print(f'GAMER REPRESENTATION DIAGNOSTIC: {dest}',flush=True)
    print(f'  frozen LoRA step={source["lora_state"]["step"]}; '
          f'{len(train_rows)} train / {len(dev_rows)} dev; {len(source["games"])} games',flush=True)
    try:
        import torch
        from transformers import AutoTokenizer,AutoModelForCausalLM
        from peft import PeftModel
        if device.startswith('cuda') and not torch.cuda.is_available():
            raise ValueError('GAMER_REPR_CUDA_UNAVAILABLE')
        model_name=source['manifest']['model']
        tok=AutoTokenizer.from_pretrained(model_name,trust_remote_code=False,use_fast=True)
        if tok.pad_token_id is None:tok.pad_token=tok.eos_token
        tok.padding_side='right'
        max_length=int(source['head_hp'].get('max_length',
                                  source['lora_state']['hyperparameters'].get('max_length',1024)))
        token_rows=[];align_rows=[];features={};perturb=[]
        dtype=torch.float16 if device.startswith('cuda') else torch.float32
        stage='load_base'
        _event(dest/'events.jsonl',{'stage':stage,'model':model_name,'device':device})
        backbone=AutoModelForCausalLM.from_pretrained(model_name,dtype=dtype,
                                trust_remote_code=False).to(device).eval()
        for p in backbone.parameters():p.requires_grad_(False)
        source_model_revision=getattr(getattr(backbone,'config',None),'_commit_hash',None)
        source_tokenizer_revision=getattr(tok,'init_kwargs',{}).get('_commit_hash')
        # Parity preflight comes FIRST. The original diagnostic performed a
        # 2190-case base-Qwen pass before discovering the cache mismatch at
        # the end of the 2190-case LoRA pass. Verify capture identity on
        # source-aligned first/last batches before paying that cost.
        stage='load_frozen_lora_preflight'
        pre_model=PeftModel.from_pretrained(backbone,str(source['source_adapter']),
                                           is_trainable=False).eval()
        for p in pre_model.parameters():p.requires_grad_(False)
        stage='capture_lora_preflight'
        pre_rows,pre_docs,pre_context,pre_positions=_preflight_selection(
            rows,docs,capture_batch,batch_context)
        pre_expected=np.concatenate((
            np.asarray(source['selected']['train']['features']),
            np.asarray(source['selected']['dev']['features'])))[pre_positions]
        _event(dest/'events.jsonl',{'stage':stage,'cases':len(pre_rows),
                                   'capture_batch':capture_batch})
        preliminary=_capture(torch,pre_model,tok,pre_rows,pre_docs,device=device,
                             max_length=max_length,mode='frozen_lora_preflight',output=dest,
                             capture_batch=capture_batch,source_batch_context=pre_context,
                             expected_last=pre_expected)
        (dest/'capture_parity.csv').replace(dest/'capture_parity_preflight.csv')
        (dest/'capture_parity_report.json').replace(dest/'capture_parity_preflight.json')
        _event(dest/'events.jsonl',{'stage':'capture_lora_preflight_complete',
                                  'cases':len(pre_rows),
                                  'max_abs_delta':preliminary['last_token_cache_max_abs_delta']})
        # PEFT wraps/mutates the base model. Reload an unmodified base for its
        # independent control, then re-attach the exact frozen adapter.
        if include_base:
            del pre_model,backbone
            import gc
            gc.collect()
            if device.startswith('cuda'):
                torch.cuda.empty_cache()
            stage='load_base_for_control'
            backbone=AutoModelForCausalLM.from_pretrained(model_name,dtype=dtype,
                                    trust_remote_code=False).to(device).eval()
            for p in backbone.parameters():p.requires_grad_(False)
            stage='capture_base'
            base=_capture(torch,backbone,tok,rows,docs,device=device,
                          max_length=max_length,mode='base_qwen',output=dest,
                          capture_batch=capture_batch,source_batch_context=batch_context)
            layer_ids=base['layer_ids']
            features.update({('base_qwen',layer,f):value for (layer,f),value
                             in base['features'].items()})
            token_rows.extend(base['token_rows']);align_rows.extend(base['alignment'])
            if perturbations:
                perturb.extend(_perturb_measure(torch,backbone,tok,dev_rows,
                              docs[len(train_rows):],device=device,max_length=max_length,
                              layer_ids=layer_ids,feature_list=FEATURES,
                              mode='base_qwen',train_docs=docs[:len(train_rows)],
                              train_features={('base_qwen',f):base['features'][(layer_ids[-1],f)][:len(train_rows)]
                                              for f in FEATURES},limit=perturbations))
            stage='load_frozen_lora'
            model=PeftModel.from_pretrained(backbone,str(source['source_adapter']),
                                           is_trainable=False).eval()
            for p in model.parameters():p.requires_grad_(False)
        else:
            model=pre_model
        stage='capture_lora'
        _event(dest/'events.jsonl',{'stage':stage,'adapter_sha256':source['head_state']['adapter_sha256']})
        captured=_capture(torch,model,tok,rows,docs,device=device,
                          max_length=max_length,mode='frozen_lora',output=dest,
                          capture_batch=capture_batch,source_batch_context=batch_context,expected_last=np.concatenate((
                              np.asarray(source['selected']['train']['features']),
                              np.asarray(source['selected']['dev']['features']))))
        if include_base and captured['layer_ids']!=layer_ids:
            raise ValueError('GAMER_REPR_BASE_LORA_LAYER_LAYOUT_MISMATCH')
        layer_ids=captured['layer_ids']
        features.update({('frozen_lora',layer,f):value for (layer,f),value
                         in captured['features'].items()})
        token_rows.extend(captured['token_rows']);align_rows.extend(captured['alignment'])
        if perturbations:
            perturb.extend(_perturb_measure(torch,model,tok,dev_rows,
                          docs[len(train_rows):],device=device,max_length=max_length,
                          layer_ids=layer_ids,feature_list=FEATURES,
                          mode='frozen_lora',train_docs=docs[:len(train_rows)],
                          train_features={('frozen_lora',f):captured['features'][(layer_ids[-1],f)][:len(train_rows)]
                                          for f in FEATURES},limit=perturbations))
        del model,backbone
        stage='matched_train_only_probes'
        _event(dest/'events.jsonl',{'stage':stage,'train_cases':len(train_rows),
                               'dev_cases':len(dev_rows),'layer_indices':layer_ids})
        scores,cases=_probe_all(features,docs[:len(train_rows)],docs[len(train_rows):])
        if not scores:raise ValueError('GAMER_REPR_NO_EVALUABLE_REGISTER_GROUPS')
        stage='write_reports'
        summary=_aggregate(scores,('mode','layer','feature','decoder'))
        first=_first_degradations(scores,layer_ids)
        _csv(dest/'token_fidelity.csv',token_rows,
             ('mode','split','case_sha256','game_id','literal','char_start','char_end',
              'token_start','token_end','token_ids','token_offsets','tokenizer_roundtrip_exact'))
        _csv(dest/'register_alignment.csv',align_rows,
             ('mode','split','case_sha256','game_id','register_id','literal_witness','status',
              'candidate_token_starts','candidate_occurrences'))
        _csv(dest/'layer_recoverability.csv',summary,
             ('mode','layer','feature','decoder','dev_registers','train_mae','dev_mae',
              'baseline_mae','dev_signed_log_mae'))
        _csv(dest/'feature_comparison.csv',_aggregate(scores,('mode','feature','decoder')),
             ('mode','feature','decoder','dev_registers','train_mae','dev_mae',
              'baseline_mae','dev_signed_log_mae'))
        _csv(dest/'decoder_comparison.csv',_aggregate(scores,('mode','decoder')),
             ('mode','decoder','dev_registers','train_mae','dev_mae',
              'baseline_mae','dev_signed_log_mae'))
        _csv(dest/'register_probes.csv',scores,
             ('mode','layer','feature','decoder','game_id','register_id',
              'train_support','dev_support','train_mae','dev_mae',
              'dev_signed_log_mae','mean_baseline_mae'))
        _csv(dest/'register_predictions.csv',cases,
             ('mode','layer','feature','decoder','game_id','register_id',
              'case_sha256','target_zone','register_zone','target','predicted','absolute_error',
              'baseline_absolute_error'))
        _csv(dest/'first_degradations.csv',first,
             ('mode','game_id','register_id','first_measured_degradation_layer',
              'initial_mae','final_mae','development_cases','verdict'))
        _csv(dest/'register_perturbations.csv',perturb,
             ('mode','case_sha256','game_id','changed_register','old_literal',
              'new_literal','feature','feature_delta_l2','relative_delta','cosine',
              'changed_register_probe_before','changed_register_probe_after',
              'changed_register_predicted_delta','true_literal_delta',
              'other_registers_evaluated','other_register_mean_absolute_prediction_delta'))
        by_zone=_aggregate([dict(r,dev_support=1,train_mae=0.,dev_mae=r['absolute_error'],
                                 mean_baseline_mae=r['baseline_absolute_error'],
                                 dev_signed_log_mae=abs(float(_signed(r['predicted'])-_signed(r['target']))))
                            for r in cases],('mode','layer','feature','decoder','target_zone','register_zone'))
        _csv(dest/'zone_recoverability.csv',by_zone,
             ('mode','layer','feature','decoder','target_zone','register_zone','dev_registers',
              'train_mae','dev_mae','baseline_mae','dev_signed_log_mae'))
        provenance={'schema':SCHEMA,'source_run':str(run),
                    'source_dataset_sha256':source['manifest']['dataset_sha256'],
                    'lora_checkpoint_sha256':source['lora_sha256'],
                    'frozen_adapter_sha256':source['head_state']['adapter_sha256'],
                    'saved_head_checkpoint_sha256':source['head_sha256'],
                    'model':model_name,'model_revision':source_model_revision,
                    'tokenizer_revision':source_tokenizer_revision,
                    'selected_games':source['games'],
                    'selected_case_sha256':[r['case_sha256'] for r in rows],
                    'max_length':max_length,'capture_batch':capture_batch,'layers':layer_ids,
                    'capture_parity':captured['capture_parity'],
                    'capture_parity_preflight':preliminary['capture_parity'],
                    'source_file_sha256_before_and_after':before,
                    'feature_capture_contract':'ORIGINAL_PROMPT_08_ONLY',
                    'cached_final_token_max_abs_delta':captured['last_token_cache_max_abs_delta'],
                    'numeric_spans_beyond_ordered_32':captured['number_spans_beyond_ordered_32'],
                    'tokenizer_roundtrip_failures':sum(
                        r['tokenizer_roundtrip_exact'] is False for r in captured['token_rows']),
                    'training_contract':'TRAIN_ONLY_FROZEN_PROBES_DEV_HELD_OUT'}
        _write_json(dest/'provenance.json',provenance)
        final_summary=sorted([r for r in summary if r['mode']=='frozen_lora' and r['layer']==layer_ids[-1]],
                             key=lambda r:r['dev_mae'])
        report={'schema':SCHEMA,'status':'COMPLETED_DIAGNOSTIC_NOT_PROMOTED',
                'source_run':str(run),'output':str(dest),
                'train_cases':len(train_rows),'dev_cases':len(dev_rows),
                'games':len(source['games']),'evaluated_register_predictions':len(cases),
                'matched_probe_groups':len(scores),'numeric_candidate_spans':len(token_rows),
                'layers':layer_ids,'capture_batch':capture_batch,
                'capture_parity':captured['capture_parity'],
                'frozen_lora_final_layer_comparison':final_summary,
                'numeric_spans_beyond_ordered_32':captured['number_spans_beyond_ordered_32'],
                'tokenizer_roundtrip_failures':sum(
                   r['tokenizer_roundtrip_exact'] is False for r in captured['token_rows']),
                'first_measured_degradations':sum(bool(r['first_measured_degradation_layer']) for r in first),
                'perturbation_observations':len(perturb),
                'cached_final_token_max_abs_delta':captured['last_token_cache_max_abs_delta'],
                'limits':['Probe recoverability is not information-theoretic sufficiency',
                          'Oracle-conditioned per-register readouts are not deployable selectors',
                          'Only matched train samples fit probes; development labels never fit probes',
                          'Small per-game development groups make layer conclusions exploratory',
                          'Changed literals in perturbations are out-of-distribution controls',
                          'Ordered numeric-span feature uses at most 32 candidates per prompt',
                          'No LoRA or production head training, no automatic model change']}
        after=_checkpoint_integrity(source,run)
        if before!=after:raise ValueError('GAMER_REPR_SOURCE_MODEL_CHANGED_DURING_DIAGNOSTIC')
        _event(dest/'events.jsonl',{'stage':'complete','source_unchanged':True,
                                  'register_probe_groups':len(scores)})
        _write_json(dest/'final_report.json',report)
        print('GAMER REPRESENTATION DIAGNOSTIC COMPLETE:',dest/'final_report.json',flush=True)
        for r in final_summary:
            print(f'  frozen LoRA layer {layer_ids[-1]} {r["feature"]} '
                  f'{r["decoder"]}: dev register MAE {r["dev_mae"]:.4f} '
                  f'vs mean {r["baseline_mae"]:.4f}',flush=True)
        print('  SOURCE MODELS READ ONLY; no training or promotion',flush=True)
        return report
    except BaseException as exc:
        _write_json(dest/'failure_report.json',{'schema':SCHEMA,'status':'INCOMPLETE',
                    'stage':stage,'error_class':type(exc).__name__,
                    'error':str(exc),'model_bytes_unchanged':before==_checkpoint_integrity(source,run)})
        raise
