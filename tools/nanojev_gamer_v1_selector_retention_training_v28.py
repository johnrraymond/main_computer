#!/usr/bin/env python3
"""V28: retention-protected TRAIN-only robust operand selector acquisition.

Frozen base Qwen layer-18 features; original V27 archive is immutable. New
synthetic TRAIN prompt perturbations receive fresh *frozen* base-Qwen forwards.
Only isolated candidate selector weights receive gradient updates. Original
TRAIN correctness is an exact acceptance constraint (all already-correct pairs).
DEV and DEV stress are touched only in the final, post-training audit. Fraction
arithmetic remains an explicit non-neural diagnostic, never a production router.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from fractions import Fraction
import gc
import json
from pathlib import Path
import re
import math

import numpy as np
from tools import nanojev_gamer_v1_candidate_span_causality_v27 as v27
from tools import nanojev_gamer_v1_operand_span_causality_v26 as v26
from tools import nanojev_gamer_v1_operand_token_causality_v24 as v24
from tools import nanojev_gamer_v1_ordered_token_causality_v25 as v25
from tools import nanojev_gamer_v1_sign_information_probe as probe
from tools import nanojev_gamer_v1_head_only_lab as lab
from tools import nanojev_gamer_v1_precision_continuation_v19 as v19
from tools import nanojev_two_game_spine_smoke as spine

SCHEMA='nanojev.gamer.v1.selector-retention-training.v28'
STAGE='layer_18'
MARKERS=('payoff_CC','payoff_CD','payoff_DC','payoff_DD','coord_spacecraft',
         'coord_beacon','plain_numeric')
TRAIN_KINDS=('distractor_prefix','altered_wording','role_reordered',
             'novel_operands','distractor_suffix','false_role_example')


def _require(ok,reason):
    if not ok:raise ValueError('V28_'+reason)


def _new_train_prompt(prompt,surface,kind):
    if kind in ('distractor_prefix','altered_wording','role_reordered','novel_operands'):
        return v27._challenge_prompt(prompt,surface,kind)
    if kind=='distractor_suffix':
        return prompt+' Ignore the unrelated audit references 271828182 and 8675309.'
    if kind=='false_role_example':
        if surface=='spine_difference':
            return ('Irrelevant historical example: CC=11, CD=12, DC=13, DD=14. '
                    'Do not calculate from that example. Current question: '+prompt)
        return ('Unrelated historical record: a spacecraft is at signed coordinate 11, '
                'while a navigation beacon is at signed coordinate 14. '
                'Only answer this current question: '+prompt)
    raise ValueError('V28_UNKNOWN_TRAIN_PERTURBATION')


def _train_truth_spans(prompt,surface,kind):
    if kind!='false_role_example':return v27._truth_spans(prompt,surface)
    # The deliberately inserted historical example reuses role markers. The
    # supervision target is the LAST current-task occurrence, not the decoy.
    if surface=='spine_difference':
        first=list(re.finditer(r'\bDC\s*=\s*(-?(?:\d+/\d+|\d+(?:\.\d+)?))',prompt))
        second=list(re.finditer(r'\bCC\s*=\s*(-?(?:\d+/\d+|\d+(?:\.\d+)?))',prompt))
    else:
        first=list(re.finditer(r'spacecraft is at signed coordinate\s+(-?(?:\d+/\d+|\d+(?:\.\d+)?))',prompt))
        second=list(re.finditer(r'navigation beacon is at signed coordinate\s+(-?(?:\d+/\d+|\d+(?:\.\d+)?))',prompt))
    _require(len(first)>=2 and len(second)>=2,'FALSE_ROLE_EXAMPLE_FORMAT')
    return first[-1].span(1),second[-1].span(1)


def _train_entries(original,fit):
    """All additional examples are derived from fit TRAIN groups, never DEV."""
    entries=[]
    for kind in TRAIN_KINDS:
        for i in fit:
            src=original[i]
            _require(src['scope']=='train','TRAIN_AUGMENT_LEAK')
            prompt=_new_train_prompt(src['prompt'],src['surface'],kind)
            truth_spans=_train_truth_spans(prompt,src['surface'],kind)
            truth=(Fraction(prompt[slice(*truth_spans[0])])-
                   Fraction(prompt[slice(*truth_spans[1])]))
            if kind!='novel_operands':
                _require(truth==src['target'],'AUGMENT_TARGET_CHANGED')
            entries.append({'row_index':src['row_index'],
                'id':src['id']+'::train_'+kind,'scope':'train_aug',
                'kind':kind,'prompt':prompt,'surface':src['surface'],
                'target':truth,'_oracle':truth_spans})
    return entries


def _load_v27(args,rows,prompts,train,info):
    path=Path(args.v27_run);rp=path/'report.json'
    _require(rp.is_file(),'V27_REPORT_MISSING')
    report=json.loads(rp.read_text(encoding='utf-8'))
    _require(report.get('schema')==v27.SCHEMA and
             report.get('status')=='READ_ONLY_ALL_CANDIDATES_NO_PROMOTION' and
             report.get('frozen_lineage')==info['lineage'] and
             report.get('test_cases_forwarded')==0 and
             report.get('promotion_authorized') is False and
             report.get('production_weights_modified') is False and
             report.get('readout_fit_on_dev_or_test') is False and
             report.get('original_capture_max_abs_difference',{}).get('base') is not None,
             'V27_PROVENANCE_MISMATCH')
    limit=report.get('stress_limit_per_condition')
    _require(type(limit) is int and 0<=limit<=len(rows)-len(train),
             'V27_STRESS_LIMIT_INVALID')
    original=v27._entries(rows,prompts,len(train),limit)
    _require(report.get('capture_cases')==len(original) and
             report.get('fit_train_groups')==len(v24._group_fit_split(train)[0])//6,
             'V27_CAPTURE_COUNT_MISMATCH')
    archive=path/'base_all_candidate_activations.npz'
    offsets=path/'base_candidate_offsets.json'
    _require(archive.is_file() and offsets.is_file() and
             v19._sha(archive)==report.get('activation_archive_sha256',{}).get('base'),
             'V27_BASE_ARCHIVE_HASH')
    metadata=json.loads(offsets.read_text(encoding='utf-8'))
    with np.load(archive,allow_pickle=False) as z:
        _require(STAGE in z.files and 'token_ids' in z.files,
                 'V27_REQUIRED_ARRAYS_MISSING')
        vectors=np.asarray(z[STAGE],dtype=np.float32).copy()
        tokens=np.asarray(z['token_ids'],dtype=np.int64)
    _require(vectors.ndim==2 and len(tokens)==len(vectors) and
             np.isfinite(vectors).all() and bool(metadata),
             'V27_ARRAYS_INVALID')
    by_case=defaultdict(list)
    cursor=0
    for j,c in enumerate(metadata):
        case=int(c['case_index']);a=int(c['start']);b=int(c['stop'])
        _require(0<=case<len(original) and a==cursor and b>a and b<=len(tokens),
                 'V27_CANDIDATE_OFFSETS_INVALID')
        cursor=b
        expected=v27._numeric_candidates(original[case]['prompt'])
        _require(tuple(c['span']) in [tuple(x['span']) for x in expected] and
                 len(c['token_ids'])==b-a and
                 np.array_equal(tokens[a:b],c['token_ids']),
                 'V27_CANDIDATE_META_MISMATCH')
        by_case[case].append(j)
    _require(cursor==len(tokens),'V27_CANDIDATE_COVERAGE_MISMATCH')
    for i,e in enumerate(original):
        _require(len(by_case[i])==len(v27._numeric_candidates(e['prompt'])),
                 'V27_CANDIDATE_COUNT_INVALID')
    by_case,gold=v27._gold_candidate_ids(original,metadata)
    return report,original,metadata,vectors,by_case,gold


def _normalize(x):
    x=np.asarray(x,dtype=np.float64)
    return x/(np.linalg.norm(x,axis=-1,keepdims=True)+1e-12)


def _local_features(prompt,c,all_candidates):
    """Lexical context features are diagnostic, independent of truth/answer."""
    marker=c['marker']
    span=c['span'];start=span[0]
    left=prompt[max(0,start-96):start].lower()
    right=prompt[span[1]:min(len(prompt),span[1]+48)].lower()
    result=[float(marker==x) for x in MARKERS]
    # Prefix markers are not role labels: features are fitted on TRAIN only.
    result.extend((float('ignore' in left),float('unrelated' in left),
                   float('historical' in left),float('current' in left),
                   float('example' in left),float('question' in left),
                   float('while' in left),float('coordinate' in left),
                   float('compute' in left),float('reference' in right),
                   float(c['index'])/max(1,len(all_candidates)-1),
                   float(start)/max(1,len(prompt)),
                   float(len(all_candidates))/16.))
    return np.asarray(result,dtype=np.float32)


def _case_features(entries,metadata,vectors,by_case,gold,indices,base_weights,
                   *,use_context):
    """One immutable feature matrix per case. No truth enters feature generation."""
    result=[]
    for i in indices:
        ids=by_case[i];prompt=entries[i]['prompt']
        candidates=v27._numeric_candidates(prompt)
        lookup={tuple(c['span']):c for c in candidates}
        raw=[];ctx=[]
        for j in ids:
            m=metadata[j]
            _require(tuple(m['span']) in lookup,'CASE_CANDIDATE_SPAN_MISMATCH')
            # Match V27's float32 mean -> float64 normalization exactly.
            pooled=_normalize(vectors[m['start']:m['stop']].mean(axis=0))
            raw.append(pooled)
            ctx.append(_local_features(prompt,lookup[tuple(m['span'])],candidates))
        x=np.asarray(raw,dtype=np.float64)
        base=x@base_weights.T
        delta=np.concatenate([x,np.asarray(ctx,dtype=np.float64)],axis=1) if use_context else x
        truth=(ids.index(gold[i][0]),ids.index(gold[i][1]))
        result.append({'case_index':i,'indices':ids,'base':base,'x':delta,'truth':truth})
    return result


def _predict(case,theta):
    scores=case['base']+case['x']@theta.T
    # Independently choose TWO different candidate positions (as in v27).
    best=None
    candidate_ids=case.get('indices',list(range(len(scores))))
    for j in range(len(scores)):
        for k in range(len(scores)):
            if j==k:continue
            # Reproduce V27's descending sort tie-break (global candidate IDs).
            tup=(float(scores[j,0]+scores[k,1]),candidate_ids[j],candidate_ids[k])
            if best is None or tup>best[0]:best=(tup,(j,k))
    return best[1]


def _retention(cases,theta,reference=None):
    result=np.asarray([_predict(c,theta)==c['truth'] for c in cases],dtype=bool)
    if reference is None:return result
    _require(len(result)==len(reference),'RETENTION_LENGTH_MISMATCH')
    return int(np.sum(reference & ~result))


def _gain(cases,theta):
    return sum(_predict(c,theta)==c['truth'] for c in cases)


def _minibatch_loss(torch,cases,theta,indices):
    # Listwise supervised CE for BOTH ordered arithmetic roles. A candidate
    # can be neither role, so all other numeric spans act as hard negatives.
    loss=torch.zeros((),dtype=theta.dtype,device=theta.device)
    for i in indices:
        case=cases[i]
        x=torch.as_tensor(case['x'],dtype=theta.dtype,device=theta.device)
        base=torch.as_tensor(case['base'],dtype=theta.dtype,device=theta.device)
        logits=base+x@theta.T
        target=case['truth']
        for role in range(2):
            loss=loss+torch.logsumexp(logits[:,role],dim=0)-logits[target[role],role]
    return loss/(2*len(indices))


def _train_selector(torch,protected,augmented,baseline,*,steps,lr,batch_size,
                    eval_every,max_halvings,seed,use_context):
    """Commit only TRAIN improvements with zero loss of original TRAIN cases."""
    _require(bool(protected) and bool(augmented),'TRAINING_EMPTY')
    ref=_retention(protected,np.zeros((2,protected[0]['x'].shape[1]),np.float32))
    _require(bool(np.any(ref)),'NO_ORIGINALLY_CORRECT_TRAIN_CASES')
    d=protected[0]['x'].shape[1]
    best=np.zeros((2,d),dtype=np.float32);best_score=_gain(augmented,best)
    # Optimizer is independent of the acceptance state; all durable updates
    # are projections back toward the last TRAIN-certified candidate.
    theta=torch.nn.Parameter(torch.zeros((2,d),dtype=torch.float32))
    optimizer=torch.optim.AdamW([theta],lr=lr,weight_decay=0)
    rng=np.random.default_rng(seed)
    history=[];accepted=0;rejected=0
    original_count=min(batch_size//2,len(protected))
    synthetic_count=batch_size-original_count
    _require(original_count>=1 and synthetic_count>=1,'REPLAY_BATCH_INVALID')
    for step in range(1,steps+1):
        picked_orig=rng.choice(len(protected),size=original_count,replace=True)
        picked_aug=rng.choice(len(augmented),size=synthetic_count,replace=True)
        batch=[protected[int(i)] for i in picked_orig]+[augmented[int(i)] for i in picked_aug]
        optimizer.zero_grad(set_to_none=True)
        loss=_minibatch_loss(torch,batch,theta,list(range(len(batch))))
        loss.backward();torch.nn.utils.clip_grad_norm_([theta],2.0)
        optimizer.step()
        if step%eval_every and step!=steps:continue
        proposal=theta.detach().cpu().numpy().copy()
        current=None;good_gain=None;halves=None
        for halving in range(max_halvings+1):
            fraction=0.5**halving
            candidate=best+fraction*(proposal-best)
            if _retention(protected,candidate,ref):continue
            gain=_gain(augmented,candidate)
            if gain>best_score:
                current=candidate;good_gain=gain;halves=halving;break
        if current is None:
            rejected+=1
        else:
            accepted+=1;best=current.copy();best_score=good_gain
        # Training continues in an isolated, disposable proposal trajectory.
        # Only the TRAIN-certified `best` snapshot is ever saved as a candidate.
        history.append({'step':step,'loss':float(loss.item()),
            'accepted':current is not None,'halvings':halves,
            'train_aug_exact':int(best_score),'train_aug_total':len(augmented),
            'protected_original_correct':int(ref.sum()),
            'protected_original_losses':_retention(protected,best,ref)})
        print('V28_TRAIN '+probe.canonical({'policy':'hybrid' if use_context else 'neural_only',
             **history[-1]}),flush=True)
    _require(_retention(protected,best,ref)==0,'PROTECTED_TRAIN_LOST_AFTER_UPDATE')
    return best,history,{'accepted':accepted,'rejected':rejected,
                         'initial_aug_correct':int(_gain(augmented,np.zeros_like(best))),
                         'final_aug_correct':int(best_score)}


def _full_evaluation(entries,metadata,vectors,by_case,gold,eval_indices,
                     base_weights,theta,tok_fit,tok,*,scope,policy):
    cases=_case_features(entries,metadata,vectors,by_case,gold,eval_indices,
                         base_weights,use_context=theta.shape[1]>len(base_weights[0]))
    out=[]
    for case in cases:
        i=case['case_index'];e=entries[i]
        pred_local=_predict(case,theta)
        selected=tuple(case['indices'][j] for j in pred_local)
        located=selected==gold[i]
        predictions=[v27._decode_candidate(vectors,metadata[j],tok_fit,tok)
                     for j in selected]
        a,b=predictions[0][0],predictions[1][0]
        truth_a=Fraction(e['prompt'][slice(*e['_oracle'][0])]);truth_b=Fraction(e['prompt'][slice(*e['_oracle'][1])])
        exact=bool(located and a==truth_a and b==truth_b)
        value=a-b if a is not None and b is not None else None
        target=e['target'];sgn=int((target>0)-(target<0))
        out.append({'scope':scope,'kind':e['kind'],'case_id':e['id'],
                    'policy':policy,'candidate_count':len(case['indices']),
                    'role_spans_correct':int(located),
                    'both_operands_exact':int(exact),
                    'exact_difference':int(located and value==target) if value is not None else 0,
                    'sign_correct':int(located and (value>0)-(value<0)==sgn) if value is not None else 0,
                    'zero_exact':int(located and sgn==0 and value==0) if value is not None else 0,
                    'target_sign':sgn,'oov':int(not all(p[2] for p in predictions)),
                    'first_prediction':str(a) if a is not None else '',
                    'second_prediction':str(b) if b is not None else ''})
    return out


def _summarize(records):
    result=[]
    for (scope,kind,policy),rr in sorted(_by_groups(records).items()):
        groups=defaultdict(list)
        for r in rr:
            rid=r['case_id'].split('::',1)[0].rsplit('.',2)[0]
            groups[rid].append(r)
        complete=sum(len(g)==6 and all(x['exact_difference'] for x in g)
                     for g in groups.values())
        result.append({'scope':scope,'kind':kind,'policy':policy,'cases':len(rr),
          'role_spans_correct':sum(x['role_spans_correct'] for x in rr),
          'both_operands_exact':sum(x['both_operands_exact'] for x in rr),
          'exact_difference':sum(x['exact_difference'] for x in rr),
          'zero_total':sum(x['target_sign']==0 for x in rr),
          'zero_exact':sum(x['zero_exact'] for x in rr),
          'positive_total':sum(x['target_sign']>0 for x in rr),
          'positive_exact':sum(x['exact_difference'] for x in rr if x['target_sign']>0),
          'negative_total':sum(x['target_sign']<0 for x in rr),
          'negative_exact':sum(x['exact_difference'] for x in rr if x['target_sign']<0),
          'complete_six_exact':complete,'complete_six_groups':sum(len(g)==6 for g in groups.values()),
          'oov_cases':sum(x['oov'] for x in rr)})
    return result


def _by_groups(records):
    groups=defaultdict(list)
    for r in records:groups[(r['scope'],r['kind'],r['policy'])].append(r)
    return groups


def _new_token_challenge(tok,train_meta,fit,original,limit=6):
    """Extra heldout challenge with truly unseen (TRAIN) token identity.

    New numbers are selected from a FIXED candidate list using only the TRAIN
    decoder's lexical vocabulary, never using DEV outcomes. This is an explicit
    coverage audit: OOV identities must fail rather than receive guessed credit.
    """
    seen={int(t) for i in fit for c in train_meta if c['case_index']==i for t in c['token_ids']}
    choices=('987654321987','765432109876','423987615204','913570246813',
             '988877766655','654327198605','917364825019')
    selected=None
    for n in choices:
        ids=tok(n,add_special_tokens=False)['input_ids']
        if any(int(t) not in seen for t in ids):selected=n;break
    if selected is None:return [],{'true_oov_found':False,'reason':'NO_FIXED_VALUE_HAS_UNSEEN_TOKEN_IDS'}
    out=[]
    for src in original[:min(limit,len(original))]:
        _require(src['scope']=='train','OOV_SOURCE_NOT_TRAIN')
        prompt=src['prompt'];a,b=v27._truth_spans(prompt,src['surface'])
        for span,replacement in sorted(((a,selected),(b,selected)),key=lambda x:x[0][0],reverse=True):
            prompt=prompt[:span[0]]+replacement+prompt[span[1]:]
        spans=v27._truth_spans(prompt,src['surface'])
        out.append({'row_index':src['row_index'],'id':src['id']+'::heldout_true_oov',
            'scope':'oov_holdout','kind':'true_oov','prompt':prompt,
            'surface':src['surface'],'target':Fraction(0),'_oracle':spans})
    return out,{'true_oov_found':True,'novel_value':selected,
                'novel_token_ids':tok(selected,add_special_tokens=False)['input_ids']}


def _run(args):
    import torch
    from tools import nanojev_gamer_v1_handoff as gamer
    _require(args.mode=='train' and args.steps>0 and args.eval_every>0 and
             args.batch_size>=4 and args.lr>0 and args.max_halvings>=0 and
             args.capture_batch in (1,2,3,4,5,6,7,8) and
             args.max_candidates>=6 and 1<=args.max_candidate_tokens<=64 and
             (not str(args.device).startswith('cuda') or torch.cuda.is_available()),
             'BAD_CONFIG_OR_DEVICE')
    src=[Path(getattr(args,x)) for x in ('run','expanded_run','input',
         'source_features','prior','v11_run','v14_run','v19_run','v20_run','v21_run',
         'v22_run','v23_run','v24_run','v25_run','v26_run','v27_run')]
    out=lab._output(args.output,src)
    _require(not out.exists(),'OUTPUT_ALREADY_EXISTS')
    _,train,dev,rows,prompts,_,info=v26._verify_source(args)
    v27_report,original,original_meta,original_vectors,by_original,gold_original=\
        _load_v27(args,rows,prompts,train,info)
    fit,hold,hold_groups=v24._group_fit_split(train)
    _require(len(train)>len(hold)>0 and len(dev)>0 and len(fit)>0 and
             len(fit)+len(hold)==len(train),
             'UNEXPECTED_TRAIN_DEV_SPLITS')
    _require(args.max_input_tokens<=info['lineage']['configuration']['max_length'],
             'MAX_INPUT_POLICY')
    base_weight=v27._fit_neural_selector({STAGE:original_vectors},original_meta,
                         original,gold_original,fit,STAGE)
    base_weight=np.asarray(base_weight,dtype=np.float64)
    # Verify baseline parity on TRAIN only; DEV is reserved for the final audit.
    for i in range(len(train)):
        candidates=by_original[i]
        native=v27._select_neural(base_weight,original_vectors,original_meta,candidates)
        baseline_case=_case_features(original,original_meta,original_vectors,
                by_original,gold_original,[i],base_weight,use_context=False)[0]
        reproduced=tuple(candidates[j] for j in _predict(baseline_case,
                           np.zeros((2,base_weight.shape[1]),dtype=np.float32)))
        _require(native==reproduced,'V27_SELECTOR_BASELINE_PARITY')
    original_train=list(range(len(train)))
    replay_neural=_case_features(original,original_meta,original_vectors,by_original,
                      gold_original,original_train,base_weight,use_context=False)
    replay_hybrid=_case_features(original,original_meta,original_vectors,by_original,
                      gold_original,original_train,base_weight,use_context=True)
    baseline_train_correct=_gain(replay_neural,np.zeros((2,replay_neural[0]['x'].shape[1]),np.float32))
    _require(baseline_train_correct>0,'V27_NO_ORIGINALLY_CORRECT_TRAIN_CASES')
    augmented=_train_entries(original,fit)
    watch=[Path(info['lineage']['checkpoint_path']),Path(args.v27_run)/'report.json',
           Path(args.v27_run)/'base_all_candidate_activations.npz',
           Path(args.v27_run)/'base_candidate_offsets.json']
    watched={str(p):v19._sha(p) for p in watch}
    _require(watched[str(watch[0])]==info['lineage']['checkpoint_sha256'] and
             spine.adapter_identity(Path(info['lineage']['adapter_dir']))==
                 info['lineage']['adapter_sha256'],'MODEL_LINEAGE_MISMATCH')
    spine.cache_policy(args.allow_model_download)
    _,tok,model=gamer.model_components(info['lineage']['model_id'],args.device)
    model.eval();model.requires_grad_(False)
    _require(all(not p.requires_grad for p in model.parameters()),'QWEN_NOT_FROZEN')
    # All training perturbations are created from TRAIN examples only. DEV
    # archive is reused unmodified and is not loaded into training functions.
    meta_aug,vec_aug=v27._capture_variant(torch,tok,model,augmented,variant='base',
         device=args.device,batch=args.capture_batch,
         max_input_tokens=args.max_input_tokens,
         max_candidates=args.max_candidates,max_candidate_tokens=args.max_candidate_tokens)
    by_aug,gold_aug=v27._gold_candidate_ids(augmented,meta_aug)
    aug_neural=_case_features(augmented,meta_aug,vec_aug[STAGE],by_aug,gold_aug,
                   list(range(len(augmented))),base_weight,use_context=False)
    aug_hybrid=_case_features(augmented,meta_aug,vec_aug[STAGE],by_aug,gold_aug,
                   list(range(len(augmented))),base_weight,use_context=True)
    candidates={};histories={};training={}
    for policy,prot,aug in (('neural_only',replay_neural,aug_neural),
                            ('hybrid',replay_hybrid,aug_hybrid)):
        candidate,hist,train_summary=_train_selector(torch,prot,aug,base_weight,
           steps=args.steps,lr=args.lr,batch_size=args.batch_size,
           eval_every=args.eval_every,max_halvings=args.max_halvings,
           seed=args.seed,use_context=policy=='hybrid')
        candidates[policy]=candidate;histories[policy]=hist;training[policy]=train_summary
    # The first DEV read occurs ONLY AFTER the TRAIN-only optimization/selection.
    tok_fit=v27._fit_token_centroids({STAGE:original_vectors},original_meta,
                                     gold_original,fit,STAGE)
    scopes={'protected_train':list(range(len(train))),
            'heldout_train':hold,
            'original_dev':list(range(len(train),len(rows))),
            'stress_dev':list(range(len(rows),len(original)))}
    records=[]
    for policy,theta in candidates.items():
        for scope,indices in scopes.items():
            records+=_full_evaluation(original,original_meta,original_vectors,
                by_original,gold_original,indices,base_weight,
                np.zeros_like(theta),tok_fit,tok,
                scope=scope,policy=policy+'_v27_baseline')
            records+=_full_evaluation(original,original_meta,original_vectors,
                by_original,gold_original,indices,base_weight,theta,tok_fit,tok,
                scope=scope,policy=policy+'_v28_candidate')
    # Synthetic TRAIN acquisition is evaluated with identical frozen decoder.
    for policy,theta in candidates.items():
        records+=_full_evaluation(augmented,meta_aug,vec_aug[STAGE],by_aug,gold_aug,
            list(range(len(augmented))),base_weight,theta,tok_fit,tok,
            scope='augmented_train',policy=policy+'_v28_candidate')
    # A true never-before-seen tokenizer identity cannot be learned from the
    # frozen TRAIN token centroids. Report this limitation explicitly.
    oov_entries,oov_provenance=_new_token_challenge(tok,original_meta,fit,original)
    if oov_entries:
        meta_oov,vec_oov=v27._capture_variant(torch,tok,model,oov_entries,
             variant='base',device=args.device,batch=args.capture_batch,
             max_input_tokens=args.max_input_tokens,
             max_candidates=args.max_candidates,max_candidate_tokens=args.max_candidate_tokens)
        by_oov,gold_oov=v27._gold_candidate_ids(oov_entries,meta_oov)
        for policy,theta in candidates.items():
            records+=_full_evaluation(oov_entries,meta_oov,vec_oov[STAGE],by_oov,gold_oov,
                list(range(len(oov_entries))),base_weight,theta,tok_fit,tok,
                scope='heldout_unseen_token_identity',policy=policy+'_v28_candidate')
    del model;gc.collect()
    if torch.cuda.is_available():torch.cuda.empty_cache()
    _require(all(v19._sha(Path(p))==digest for p,digest in watched.items()) and
             spine.adapter_identity(Path(info['lineage']['adapter_dir']))==
             info['lineage']['adapter_sha256'],
             'SOURCE_OR_MODEL_CHANGED_DURING_TRAIN')
    metrics=_summarize(records)
    bymetric={(x['scope'],x['kind'],x['policy']):x for x in metrics}
    acceptance={}
    for policy,theta in candidates.items():
        prior=bymetric[('original_dev','original',policy+'_v27_baseline')]
        current=bymetric[('original_dev','original',policy+'_v28_candidate')]
        protected=bymetric[('protected_train','original',policy+'_v28_candidate')]
        _require(prior['exact_difference']==len(dev) and
                 prior['zero_exact']==prior['zero_total'] and
                 prior['complete_six_exact']==prior['complete_six_groups'],
                 'UNEXPECTED_V27_BASELINE_ORIGINAL_DEV')
        passed=(protected['role_spans_correct']>=baseline_train_correct and
                current['exact_difference']==prior['exact_difference'] and
                current['zero_exact']==prior['zero_exact'] and
                current['complete_six_exact']==prior['complete_six_exact'] and
                training[policy]['final_aug_correct']>training[policy]['initial_aug_correct'])
        acceptance[policy]={'train_retained':protected['role_spans_correct']>=baseline_train_correct,
               'original_train_baseline_correct':baseline_train_correct,
               'original_dev_unchanged':current['exact_difference']==prior['exact_difference'],
               'zero_line_unchanged':current['zero_exact']==prior['zero_exact'],
               'six_case_groups_unchanged':current['complete_six_exact']==prior['complete_six_exact'],
               'augmented_train_improved':training[policy]['final_aug_correct']>
                                           training[policy]['initial_aug_correct'],
               'all_gates_met':passed,
               'promotion_authorized':False}
    out.mkdir(parents=True,exist_ok=False)
    from tools.nanojev_gamer_v1_conflict_projection_v10 import _csv
    _csv(out/'selector_training_history.csv',[{'policy':p,**step}
          for p,hist in histories.items() for step in hist])
    _csv(out/'selector_comparison.csv',metrics)
    _csv(out/'selector_case_results.csv',records)
    candidate_name='disposable_selector_candidates.pt'
    torch.save({'schema':SCHEMA,'production':False,'base_stage':STAGE,
          'base_weight':torch.from_numpy(base_weight),
          'candidate_delta':{k:torch.from_numpy(v) for k,v in candidates.items()},
          'readout_fit_groups':len(fit)//6,'training_only_checkpoint_selection':True,
          'acceptance':acceptance},out/candidate_name)
    # New perturbed TRAIN activations allow exact replay and further diagnosis.
    np.savez_compressed(out/'augmented_train_layer18.npz',
         layer_18=vec_aug[STAGE],token_ids=np.asarray([t for c in meta_aug
                    for t in c['token_ids']],dtype=np.int64))
    (out/'augmented_train_candidates.json').write_text(json.dumps(meta_aug,
            separators=(',',':'))+'\n',encoding='utf-8')
    report={'schema':SCHEMA,'status':'SELECTOR_TRAINING_COMPLETE_NO_PROMOTION',
            'algorithm':'TRAIN_ONLY_RETENTION_PROJECTED_SELECTOR_WITH_ORIGINAL_REPLAY',
            'frozen_lineage':info['lineage'],'v27_report_sha256':watched[str(watch[1])],
            'v27_base_archive_sha256':watched[str(watch[2])],
            'source_dataset_sha256':info['dataset_sha256'],
            'train_cases':len(train),'dev_cases':len(dev),
            'augmented_train_cases':len(augmented),'fit_train_groups':len(fit)//6,
            'protected_original_train_cases':len(train),
            'original_train_baseline_correct':baseline_train_correct,
            'heldout_train_groups':hold_groups,
            'training':training,'acceptance':acceptance,'comparisons':metrics,
            'true_oov_challenge':oov_provenance,
            'no_qwen_lora_or_original_head_training':True,
            'selector_only_trainable_parameters':True,
            'original_task_bypass':'UNCHANGED_V6',
            'test_used_for_fitting':False,'dev_used_for_checkpoint_selection':False,
            'production_weights_modified':False,'production_checkpoint_saved':False,
            'disposable_selector_checkpoint_sha256':v19._sha(out/candidate_name),
            'augmented_archive_sha256':v19._sha(out/'augmented_train_layer18.npz'),
            'promotion_authorized':False,
            'limits':['The result is a diagnostic selector, not a production router',
                 'TRAIN perturbation families overlap with DEV stress families',
                 'Hybrid uses TRAIN-learned lexical context features, not purely neural semantics',
                 'Unseen token-ID failures require an independently expanded decoder vocabulary',
                 'Exact Fraction subtraction is not learned numerical computation',
                 'Original DEV is evaluated only once after TRAIN checkpoint selection',
                 'No held-out TEST data is used']}
    (out/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True,
               allow_nan=False)+'\n',encoding='utf-8')
    print('V28_COMPLETE '+probe.canonical({'status':report['status'],
          'train_cases':len(augmented),'acceptance':acceptance,
          'report':str(out/'report.json'),'promotion':False}),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('train',),default='train')
    for name in ('run','expanded-run','input','source-features','prior','v11-run',
         'v14-run','v19-run','v20-run','v21-run','v22-run','v23-run','v24-run',
         'v25-run','v26-run','v27-run','output'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--device',default='cuda')
    p.add_argument('--capture-batch',type=int,default=2)
    p.add_argument('--max-input-tokens',type=int,default=1024)
    p.add_argument('--max-candidates',type=int,default=16)
    p.add_argument('--max-candidate-tokens',type=int,default=16)
    p.add_argument('--steps',type=int,default=160)
    p.add_argument('--lr',type=float,default=0.002)
    p.add_argument('--batch-size',type=int,default=48)
    p.add_argument('--eval-every',type=int,default=20)
    p.add_argument('--max-halvings',type=int,default=8)
    p.add_argument('--seed',type=int,default=61)
    p.add_argument('--allow-model-download',action='store_true')
    return p


def main(argv=None):
    _run(parser().parse_args(argv));return 0

if __name__=='__main__':raise SystemExit(main())
