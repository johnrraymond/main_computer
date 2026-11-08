#!/usr/bin/env python3
"""V30: offline, TRAIN-only numerical-token decoder continuation and zero-line audit.

V29 corrected operand selection does not train the V27 layer-18 token-ID decoder.
This lab tests that separate boundary. It uses only immutable V27/V28 caches,
never forwards Qwen/LoRA, and selects a decoder ONLY from TRAIN evidence.
DEV and stress are evaluated once after the decoder is locked. Even a passing
candidate is disposable: no production routing or numerical head is changed.

Numeric text and oracle spans are used only to SCORE the frozen predictions;
this module never replaces predicted token IDs with input token IDs at inference.
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from fractions import Fraction
import json
from pathlib import Path

import numpy as np
from tools import nanojev_gamer_v1_candidate_span_causality_v27 as v27
from tools import nanojev_gamer_v1_selector_retention_training_v28 as v28
from tools import nanojev_gamer_v1_role_reversal_retention_v29 as v29
from tools import nanojev_gamer_v1_operand_span_causality_v26 as v26
from tools import nanojev_gamer_v1_operand_token_causality_v24 as v24
from tools import nanojev_gamer_v1_head_only_lab as lab
from tools import nanojev_gamer_v1_precision_continuation_v19 as v19
from tools import nanojev_gamer_v1_conflict_projection_v10 as csvwriter

SCHEMA='nanojev.gamer.v1.zero-line-decoder-continuation.v30'
ALPHAS=(0.,.25,.5,.75,1.)


def _require(ok, reason):
    if not ok:
        raise ValueError('V30_'+reason)


def _normal(x):
    a=np.asarray(x,dtype=np.float64)
    return a/(np.linalg.norm(a,axis=-1,keepdims=True)+1e-12)


def _collect(meta,vectors,indices=None,selected=None):
    """Return token vectors, true token IDs for TRAIN-supervised readout.

    selected is a set of candidate metadata indices for retention calculations.
    None means all numeric candidates are included. Never consult DEV targets.
    """
    allowed=set(indices) if indices is not None else None
    selected=set(selected) if selected is not None else None
    feats=[];labels=[]
    for j,c in enumerate(meta):
        if allowed is not None and c['case_index'] not in allowed:continue
        if selected is not None and j not in selected:continue
        a,b=int(c['start']),int(c['stop'])
        ids=c['token_ids']
        _require(b>a and b-a==len(ids),'INVALID_METADATA_TOKEN_LENGTH')
        feats.append(np.asarray(vectors[a:b],dtype=np.float64))
        labels.extend(int(t) for t in ids)
    _require(bool(feats),'EMPTY_TOKEN_DATA')
    return _normal(np.concatenate(feats)),np.asarray(labels,dtype=np.int64)


def _centroids(meta,vectors,indices=None):
    """TRAIN-only enhanced representation of the same learned token-ID classes."""
    sums={};counts=defaultdict(int)
    allowed=set(indices) if indices is not None else None
    for c in meta:
        if allowed is not None and c['case_index'] not in allowed:continue
        a,b=int(c['start']),int(c['stop'])
        for token,vector in zip(c['token_ids'],vectors[a:b]):
            token=int(token)
            if token not in sums:sums[token]=np.zeros(len(vector),dtype=np.float64)
            sums[token]+=np.asarray(vector,dtype=np.float64)
            counts[token]+=1
    _require(bool(sums),'EMPTY_AUGMENTED_CENTROIDS')
    return {i:_normal(sums[i]/counts[i]) for i in sums}


def _blended(base,extended,alpha):
    """alpha=0 is byte-for-byte equivalent in labels/order to V27 baseline."""
    _require(0<=alpha<=1,'BAD_BLEND_ALPHA')
    vocab,centers=base
    start={int(k):np.asarray(c,dtype=np.float64) for k,c in zip(vocab,centers)}
    if not alpha:return list(vocab),np.asarray(centers,dtype=np.float64)
    merged={}
    for token in sorted(set(start)|set(extended)):
        if token in start and token in extended:
            merged[token]=_normal((1-alpha)*start[token]+alpha*extended[token])
        elif token in start:
            merged[token]=start[token]
        else:
            merged[token]=extended[token]
    ids=sorted(merged)
    return ids,np.asarray([merged[t] for t in ids],dtype=np.float64)


def _predict_tokens(x,fit,chunk_size=2048):
    vocab,centers=fit
    _require(len(vocab)>0 and len(vocab)==len(centers),'EMPTY_OR_MISMATCHED_DECODER')
    vocabulary=np.asarray(vocab,dtype=np.int64)
    answers=[]
    # Fixed chunking keeps large source archives bounded in RAM on Windows.
    for a in range(0,len(x),chunk_size):
        scores=np.asarray(x[a:a+chunk_size])@np.asarray(centers).T
        answers.append(vocabulary[scores.argmax(axis=1)])
    return np.concatenate(answers) if answers else np.empty((0,),dtype=np.int64)


def _choose(base,extra,fit_data,protected_data,alphas=ALPHAS,
            numeric_retention=None):
    """The acceptance decision sees TRAIN labels only. DEV is not an argument."""
    x,y=fit_data;px,py=protected_data
    original=_predict_tokens(px,base)
    baseline_mask=original==py
    _require(np.any(baseline_mask),'NO_ACQUIRED_TRAIN_TOKEN_COMPETENCE')
    best=base;score=int((_predict_tokens(x,best)==y).sum());best_alpha=0.
    history=[]
    for a in alphas:
        trial=_blended(base,extra,float(a))
        p=_predict_tokens(px,trial)
        losses=int(np.sum(baseline_mask&(p!=py)))
        gain=int(np.sum(_predict_tokens(x,trial)==y))
        numeric_losses=(numeric_retention(trial) if numeric_retention is not None
                        and losses==0 and gain>score else None)
        accepted=(losses==0 and gain>score and
                  (numeric_losses is None or numeric_losses==0))
        if accepted:best=trial;score=gain;best_alpha=float(a)
        history.append({'alpha':float(a),'train_token_correct':gain,
            'train_token_total':len(y),'previously_correct_protected_tokens':int(baseline_mask.sum()),
            'protected_token_losses':losses,
            'protected_numeric_answer_losses':numeric_losses,
            'accepted':accepted})
    _require(np.all(_predict_tokens(px,best)[baseline_mask]==py[baseline_mask]),
             'ACQUIRED_TRAIN_TOKEN_REGRESSION')
    return best,best_alpha,history


def _optional_tokenizer(model_id, path=None):
    """Load tokenizer from local cache only; NEVER load or forward model."""
    try:
        from transformers import AutoTokenizer
        t=AutoTokenizer.from_pretrained(str(path or model_id),use_fast=True,
                                         local_files_only=True)
        return t,None
    except (ImportError,OSError,ValueError) as error:
        return None,type(error).__name__+': '+str(error)[:200]


def _selected(meta,vectors,fit,tok=None):
    """Decode from model activations; true IDs are used for diagnostics only."""
    x=_normal(vectors[meta['start']:meta['stop']]);predicted=_predict_tokens(x,fit)
    true=[int(i) for i in meta['token_ids']]
    vocab=set(int(i) for i in fit[0]);known=all(t in vocab for t in true)
    value=None
    if tok is not None and known:
        from tools import nanojev_gamer_v1_ordered_token_causality_v25 as v25
        value=v25._parse_decoded_number(v25._decode_ids(tok,predicted.tolist()))
    return {'true_ids':true,'predicted_ids':predicted.tolist(),
        'token_exact':bool(known and true==predicted.tolist()),
        'oov':not known,'value':value}


def _evaluation(entries,meta,vec,by,gold,base_weights,theta,base_fit,new_fit,tok,
                indices,scope):
    cases=v28._case_features(entries,meta,vec,by,gold,indices,base_weights,use_context=True)
    all_rows=[]
    for case in cases:
        i=case['case_index'];e=entries[i]
        chosen=v28._predict(case,theta)
        selected=tuple(case['indices'][k] for k in chosen)
        located=selected==gold[i]
        for name,fit in (('v27_decoder',base_fit),('v30_decoder',new_fit)):
            a=_selected(meta[selected[0]],vec,fit,tok)
            b=_selected(meta[selected[1]],vec,fit,tok)
            va,vb=a['value'],b['value']
            pred=(va-vb) if va is not None and vb is not None else None
            target=e['target'];sgn=int((target>0)-(target<0))
            truth=(Fraction(e['prompt'][slice(*e['_oracle'][0])]),
                   Fraction(e['prompt'][slice(*e['_oracle'][1])]))
            all_rows.append({'scope':scope,'kind':e['kind'],'case_id':e['id'],
                'policy':name,'target_sign':sgn,'role_spans_correct':int(located),
                'both_operand_tokens_exact':int(located and a['token_exact'] and b['token_exact']),
                'first_token_exact':int(a['token_exact']),
                'second_token_exact':int(b['token_exact']),
                'oov':int(a['oov'] or b['oov']),
                'numeric_evaluated':int(tok is not None),
                'both_operands_exact':int(located and va==truth[0] and vb==truth[1]) if tok else 0,
                'exact_difference':int(located and pred==target) if tok and pred is not None else 0,
                'zero_exact':int(located and sgn==0 and pred==0) if tok and pred is not None else 0,
                'first_truth':str(truth[0]),'second_truth':str(truth[1]),
                'first_prediction':str(va) if va is not None else '',
                'second_prediction':str(vb) if vb is not None else '',
                'first_true_token_ids':json.dumps(a['true_ids']),
                'first_predicted_token_ids':json.dumps(a['predicted_ids']),
                'second_true_token_ids':json.dumps(b['true_ids']),
                'second_predicted_token_ids':json.dumps(b['predicted_ids'])})
    return all_rows


def _numerical_retention(entries,meta,vec,by,gold,base_weights,selector,
                         base_fit,tok,indices):
    """Lock previously exact TRAIN arithmetic, including V28 acquisitions.

    Used only on TRAIN in checkpoint selection; never called with DEV indices.
    """
    features=v28._case_features(entries,meta,vec,by,gold,indices,base_weights,
                                use_context=True)
    eligible=[]
    for case in features:
        i=case['case_index'];chosen=v28._predict(case,selector)
        pair=tuple(case['indices'][j] for j in chosen)
        if pair!=gold[i]:continue
        a=_selected(meta[pair[0]],vec,base_fit,tok)['value']
        b=_selected(meta[pair[1]],vec,base_fit,tok)['value']
        if a is not None and b is not None and a-b==entries[i]['target']:
            eligible.append((pair,entries[i]['target']))

    def losses(candidate):
        missing=0
        for pair,target in eligible:
            a=_selected(meta[pair[0]],vec,candidate,tok)['value']
            b=_selected(meta[pair[1]],vec,candidate,tok)['value']
            if a is None or b is None or a-b!=target:missing+=1
        return missing
    return losses,len(eligible)


def _verify_v29_case_parity(rows,run,require_numeric):
    """Fail rather than misattribute a V29 case if the replay is not identical."""
    path=Path(run)/'role_case_results.csv'
    _require(path.is_file(),'V29_CASE_CSV_MISSING')
    with path.open(encoding='utf-8',newline='') as stream:
        prior={(r['scope'],r['kind'],r['case_id']):r for r in csv.DictReader(stream)
               if r['policy']=='v29_hybrid' and
               r['scope'] in ('protected_train','original_dev','stress_dev')}
    replay=[r for r in rows if r['policy']=='v27_decoder' and
            r['scope'] in ('protected_train','original_dev','stress_dev')]
    _require(len(replay)==len(prior),'V29_CSV_REPLAY_CASE_COUNT')
    for r in replay:
        key=(r['scope'],r['kind'],r['case_id'])
        expected=prior.get(key)
        _require(expected is not None and
            int(expected['role_spans_correct'])==r['role_spans_correct'],
            'V29_ROLE_SELECTION_REPLAY_MISMATCH')
        if require_numeric:
            _require(expected['first_prediction']==r['first_prediction'] and
                expected['second_prediction']==r['second_prediction'] and
                int(expected['zero_exact'])==r['zero_exact'],
                'V29_FROZEN_NUMERIC_DECODER_REPLAY_MISMATCH')
    return len(replay)


def _summaries(records):
    groups=defaultdict(list)
    for r in records:groups[(r['scope'],r['kind'],r['policy'])].append(r)
    return [{'scope':s,'kind':k,'policy':p,'cases':len(rr),
        'role_spans_correct':sum(r['role_spans_correct'] for r in rr),
        'operand_token_pairs_exact':sum(r['both_operand_tokens_exact'] for r in rr),
        'numeric_evaluated':all(r['numeric_evaluated'] for r in rr),
        'difference_exact':sum(r['exact_difference'] for r in rr),
        'zero_total':sum(r['target_sign']==0 for r in rr),
        'zero_exact':sum(r['zero_exact'] for r in rr),
        'zero_token_pairs_exact':sum(r['both_operand_tokens_exact'] for r in rr
                                     if r['target_sign']==0)}
        for (s,k,p),rr in sorted(groups.items())]


def _run(args):
    import torch
    source=[Path(getattr(args,k)) for k in ('run','expanded_run','input','source_features',
        'prior','v11_run','v14_run','v19_run','v20_run','v21_run','v22_run',
        'v23_run','v24_run','v25_run','v26_run','v27_run','v28_run','v29_run')]
    out=lab._output(args.output,source)
    _require(not out.exists(),'OUTPUT_ALREADY_EXISTS')
    _,train,dev,rows,prompts,_,info=v26._verify_source(args)
    report27,entries,meta,vec,by,gold=v28._load_v27(args,rows,prompts,train,info)
    fit,hold,_=v24._group_fit_split(train)
    report28=json.loads((Path(args.v28_run)/'report.json').read_text(encoding='utf-8'))
    report29=json.loads((Path(args.v29_run)/'report.json').read_text(encoding='utf-8'))
    cp=Path(args.v29_run)/'disposable_v29_hybrid_role_candidate.pt'
    _require(report29.get('schema')==v29.SCHEMA and
        report29.get('status')=='ROLE_REVERSAL_TRAINING_COMPLETE_NO_PROMOTION' and
        report29.get('frozen_lineage')==info['lineage'] and
        report29.get('v27_report_sha256')==v19._sha(Path(args.v27_run)/'report.json') and
        report29.get('v28_report_sha256')==v19._sha(Path(args.v28_run)/'report.json') and
        report29.get('disposable_selector_checkpoint_sha256')==v19._sha(cp) and
        report29.get('promotion_authorized') is False and
        report29.get('production_weights_modified') is False and
        report29.get('train_cases')==len(train) and
        report29.get('dev_cases')==len(dev) and
        report28.get('schema')==v28.SCHEMA and
        report28.get('frozen_lineage')==info['lineage'],
        'INVALID_V29_LINEAGE_OR_REPORT')
    immutable=[Path(info['lineage']['checkpoint_path']),
        Path(args.v27_run)/'base_all_candidate_activations.npz',
        Path(args.v28_run)/'augmented_train_layer18.npz',cp]
    before={str(p):v19._sha(p) for p in immutable}
    checkpoint=torch.load(cp,map_location='cpu',weights_only=True)
    _require(checkpoint['schema']==v29.SCHEMA and checkpoint['production'] is False and
             checkpoint['v28_checkpoint_sha256']==report29['v28_disposable_checkpoint_sha256'],
             'INVALID_V29_CANDIDATE_CHECKPOINT')
    base_weights=np.asarray(checkpoint['base_weight'].numpy(),dtype=np.float64)
    selector=np.asarray(checkpoint['hybrid_v29_candidate'].numpy(),dtype=np.float32)
    _require(selector.shape==(2,base_weights.shape[1]+len(v28.MARKERS)+13),
             'SELECTOR_SHAPE_MISMATCH')
    aug,ma,va,ba,ga=v29._verify_augmented_archive(args.v28_run,report28,entries,fit)
    original_fit=v27._fit_token_centroids({v28.STAGE:vec},meta,gold,fit,v28.STAGE)
    _require(len(vec[0])==len(va[0]),'LAYER_WIDTH_MISMATCH')
    # Additional supervision: only FIT original candidates and V28 augmented
    # TRAIN candidates. No original heldout or DEV enters fitting or selection.
    counts=defaultdict(int);acc={}
    # Average both TRAIN sources token-wise with their natural sample counts.
    for mm,vv,ids in ((meta,vec,set(fit)),(ma,va,None)):
        for c in mm:
            if ids is not None and c['case_index'] not in ids:continue
            for token,v in zip(c['token_ids'],vv[c['start']:c['stop']]):
                token=int(token)
                if token not in acc:acc[token]=np.zeros(len(v),dtype=np.float64)
                acc[token]+=np.asarray(v,dtype=np.float64);counts[token]+=1
    enriched={k:_normal(v/counts[k]) for k,v in acc.items()}
    xf,yf=_collect(meta,vec,fit)
    xa,ya=_collect(ma,va)
    fit_data=(np.concatenate([xf,xa]),np.concatenate([yf,ya]))
    # Protect every previously correct numerical-token reconstruction, not just
    # TRAIN fit groups, for both original TRAIN and V28 acquired augmentations.
    p0={i for i in range(len(train)) for i in gold[i]}
    p1={i for i in range(len(aug)) for i in ga[i]}
    xp,yp=_collect(meta,vec,selected=p0)
    xpa,ypa=_collect(ma,va,selected=p1)
    protected=(np.concatenate([xp,xpa]),np.concatenate([yp,ypa]))
    tok,tokenizer_error=_optional_tokenizer(info['lineage']['model_id'],args.tokenizer_path)
    # Numerical retention can be certified only when the local tokenizer is
    # available. Otherwise inspect token IDs but keep the original baseline.
    numeric_retention=None;numeric_cases=0
    if tok is not None:
        original_check,n_original=_numerical_retention(entries,meta,vec,by,gold,
            base_weights,selector,original_fit,tok,list(range(len(train))))
        augmented_check,n_aug=_numerical_retention(aug,ma,va,ba,ga,
            base_weights,selector,original_fit,tok,list(range(len(aug))))
        numeric_cases=n_original+n_aug
        numeric_retention=lambda candidate:(original_check(candidate)+
                                            augmented_check(candidate))
    locked,alpha,hist=_choose(original_fit,enriched,fit_data,protected,
        alphas=ALPHAS if numeric_retention is not None else (0.,),
        numeric_retention=numeric_retention)
    cases=[]
    scopes={'protected_train':list(range(len(train))),
        'heldout_train':list(hold),'original_dev':list(range(len(train),len(rows))),
        'stress_dev':list(range(len(rows),len(entries)))}
    for name,indices in scopes.items():
        cases+=_evaluation(entries,meta,vec,by,gold,base_weights,selector,
            original_fit,locked,tok,indices,name)
    replay_cases=_verify_v29_case_parity(cases,args.v29_run,tok is not None)
    summary=_summaries(cases)
    s={(r['scope'],r['kind'],r['policy']):r for r in summary}
    dev0=s[('original_dev','original','v27_decoder')]
    dev1=s[('original_dev','original','v30_decoder')]
    stress=[r for r in summary if r['scope']=='stress_dev' and r['policy']=='v30_decoder']
    stress0=[r for r in summary if r['scope']=='stress_dev' and r['policy']=='v27_decoder']
    gates={'frozen_sources_unchanged':all(v19._sha(Path(p))==h for p,h in before.items()),
        'train_selection_only':True,
        'previously_correct_train_numeric_answer_retention':bool(
            numeric_retention is not None and numeric_retention(locked)==0),
        'previously_correct_train_token_retention':int(np.sum(
            (_predict_tokens(protected[0],original_fit)==protected[1]) &
            (_predict_tokens(protected[0],locked)!=protected[1])))==0,
        'numeric_decoder_available':tok is not None,
        'original_dev_exact_retained':bool(tok and dev1['difference_exact']>=dev0['difference_exact']),
        'original_dev_zero_exact':bool(tok and dev1['zero_total']==dev1['zero_exact']),
        'full_stress_zero_line':bool(tok and all(x['zero_total']==x['zero_exact'] for x in stress)),
        'non_reversal_stress_nonregression':bool(tok and all(
            s[(r['scope'],r['kind'],'v30_decoder')]['difference_exact']>=r['difference_exact']
            for r in stress0 if r['kind']!='role_reordered'))}
    _require(gates['frozen_sources_unchanged'] and
             gates['previously_correct_train_token_retention'],
             'SOURCE_OR_PROTECTED_TOKEN_MUTATION')
    index=[r for r in cases if r['scope']=='stress_dev' and r['kind']=='role_reordered'
           and r['case_id']==args.case_id]
    out.mkdir(parents=True,exist_ok=False)
    csvwriter._csv(out/'decoder_case_results.csv',cases)
    csvwriter._csv(out/'decoder_comparison.csv',summary)
    csvwriter._csv(out/'train_blend_history.csv',hist)
    torch.save({'schema':SCHEMA,'production':False,'v29_checkpoint_sha256':before[str(cp)],
        'centroid_tokens':torch.tensor(locked[0],dtype=torch.long),
        'centroids':torch.tensor(locked[1],dtype=torch.float32),
        'alpha':alpha,'frozen_source_sha256':before},out/'disposable_v30_decoder.pt')
    report={'schema':SCHEMA,'status':'DECODER_TRAIN_ONLY_EXPERIMENT_COMPLETE_NO_PROMOTION',
        'algorithm':'TRAIN_ONLY_FROZEN_LAYER18_TOKEN_CENTROID_BLEND_WITH_HARD_ACQUIRED_RETENTION',
        'source_sha256':before,'v27_report_sha256':v19._sha(Path(args.v27_run)/'report.json'),
        'v28_report_sha256':v19._sha(Path(args.v28_run)/'report.json'),
        'v29_report_sha256':v19._sha(Path(args.v29_run)/'report.json'),
        'selection_alpha':alpha,'train_history':hist,
        'protected_train_numerical_answers':numeric_cases,
        'v29_replayed_cases':replay_cases,
        'comparisons':summary,
        'case_id':args.case_id,'focus_case':index,'acceptance_gates':gates,
        'all_gates_met':all(gates.values()),
        'tokenizer_error':tokenizer_error,'no_dev_fitting_or_checkpoint_selection':True,
        'no_test_used':True,'no_qwen_or_lora_training':True,
        'no_original_head_training':True,'production_weights_modified':False,
        'promotion_authorized':False,
        'limits':['Selected spans held fixed at disposable V29 selector; this isolates decoder error',
            'TRAIN examples, augmented data, and all numerical tokens are distinguished from DEV',
            'True token IDs and source text never substitute for predicted IDs during scoring',
            'A centroid blend may fail to repair the context-dependent decoder; it is not guaranteed',
            'Only locally cached tokenizer is used; without it numeric zero-line certification is unavailable',
            'All source weights and production paths remain unchanged']}
    (out/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    print('V30_COMPLETE '+json.dumps({'case':args.case_id,'focus':index,
        'selected_alpha':alpha,'acceptance_gates':gates,'report':str(out/'report.json'),
        'production_modified':False},sort_keys=True),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('run','expanded-run','input','source-features','prior','v11-run',
         'v14-run','v19-run','v20-run','v21-run','v22-run','v23-run','v24-run',
         'v25-run','v26-run','v27-run','v28-run','v29-run','output'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--tokenizer-path',type=Path,default=None,
                   help='Optional LOCAL tokenizer directory; never downloads model')
    p.add_argument('--case-id',default='dev.offset.0003.zero.spine_difference::role_reordered')
    return p


def main(argv=None):
    _run(parser().parse_args(argv));return 0

if __name__=='__main__':raise SystemExit(main())
