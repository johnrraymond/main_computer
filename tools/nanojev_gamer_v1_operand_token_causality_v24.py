#!/usr/bin/env python3
"""V24 read-only, span-aligned operand recovery audit over FROZEN Qwen and LoRA.

No training of Qwen, LoRA, production head or specialist. A new read-only
forward pass is required because V23 cached last/pooled activations, not number
positions. Every operand is parsed from an independently fingerprinted prompt.
Fixed kernel-ridge probes fit only on the synthetic TRAIN groups. Entire other
TRAIN groups are withheld from probe fitting for in-range testing. Original
synthetic DEV is an out-of-range diagnostic, never a fitting/selection set;
previously seen TEST is never forwarded or scored.

Crucial limitations: the probes may exploit contextual correlations rather than
literal operand semantics; arithmetic on decoded numerical estimates is not
an oracle; only subtraction T-R is varied in this capture. Token-pair swaps
are out-of-distribution sensitivity checks, not unique causal identification.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from fractions import Fraction
import hashlib
import json
from pathlib import Path

from tools import nanojev_gamer_v1_operand_causality_v23 as v23
from tools import nanojev_gamer_v1_joint_head_repair as joint
from tools import nanojev_gamer_v1_head_only_lab as lab
from tools import nanojev_gamer_v1_sign_information_probe as probe
from tools import nanojev_gamer_v1_zero_sign_frob as oracle
from tools import nanojev_gamer_v1_precision_continuation_v19 as v19
from tools import nanojev_two_game_spine_smoke as spine

SCHEMA='nanojev.gamer.v1.operand-token-causality.v24'
LAYERS=(9,18,27)
EPS=1e-10


def _require(test,reason):
    if not test:raise ValueError('V24_'+reason)


def _spans(prompt,surface):
    """Extract ordered semantic operands, not arbitrary first/second numbers."""
    if surface=='source_native':
        m=oracle.SOURCE_PATTERN.search(prompt)
        _require(m is not None,'NATIVE_SPAN_UNAVAILABLE')
        first,second=m.span(1),m.span(2)
    elif surface=='spine_difference':
        m=oracle.MATRIX_PATTERN.search(prompt)
        _require(m is not None and 'Compute T-R' in prompt,'GAME_SPAN_UNAVAILABLE')
        first,second=m.span(3),m.span(1)
    else:raise ValueError('V24_UNKNOWN_OPERAND_SURFACE')
    _require(first[1]<=second[0] or second[1]<=first[0],
             'OVERLAPPING_OPERAND_SPANS')
    a,b=Fraction(prompt[slice(*first)]),Fraction(prompt[slice(*second)])
    return first,second,a,b


def _aligned_tokens(offsets,span):
    """Offset alignment must cover every operand character without truncation."""
    start,end=span
    tokens=[i for i,(a,b) in enumerate(offsets) if b>a and a<end and b>start]
    _require(bool(tokens),'TOKEN_OPERAND_ALIGNMENT_EMPTY')
    _require(min(offsets[i][0] for i in tokens)<=start and
             max(offsets[i][1] for i in tokens)>=end,
             'TOKEN_OPERAND_SPAN_INCOMPLETE')
    return tokens


def _indexed_prompts(info):
    deck=probe.make_deck(
        sum(r['split']=='train' for r in info['dataset'])//6,
        sum(r['split']=='dev' for r in info['dataset'])//6,
        (sum(r['split']=='test' and r['family']=='signed_offset'
             for r in info['dataset']))//6,
        any(r['family']=='hawk_indifference' for r in info['dataset']))
    _require(len(deck)==len(info['dataset']) and
             probe.deck_hash(deck)==info['dataset_sha256'],
             'DECK_FINGERPRINT_MISMATCH')
    return {i:deck[i].prompt for i,r in enumerate(info['dataset'])
            if r['split'] in ('train','dev') and r['family']=='signed_offset'}


def _group_fit_split(train_rows):
    """Hold out interior groups, ensuring a group-disjoint in-range assessment."""
    groups=sorted({r['group'] for r in train_rows})
    _require(len(groups)>=10,'TOO_FEW_TRAIN_GROUPS_FOR_GROUP_HOLDOUT')
    hold={g for i,g in enumerate(groups) if 0<i<len(groups)-1 and i%5==2}
    _require(len(hold)>=2,'INSUFFICIENT_IN_RANGE_HELDOUT_GROUPS')
    fit=[i for i,r in enumerate(train_rows) if r['group'] not in hold]
    eval_idx=[i for i,r in enumerate(train_rows) if r['group'] in hold]
    _require(len(fit)%6==0 and len(eval_idx)%6==0,
             'INCOMPLETE_IN_RANGE_GROUPS')
    fit_a=[float(Fraction(train_rows[i]['second_operand'])) for i in fit]
    test_a=[float(Fraction(train_rows[i]['second_operand'])) for i in eval_idx]
    _require(min(fit_a)<min(test_a) and max(test_a)<max(fit_a),
             'IN_RANGE_OPERAND_CENTERS_NOT_INTERPOLATED')
    return fit,eval_idx,sorted(hold)


def _validated_v23(args,data,train_rows,dev_rows):
    p=Path(args.v23_run)/'report.json'
    _require(p.is_file(),'V23_REPORT_NOT_FOUND')
    r=json.loads(p.read_text(encoding='utf-8'))
    # Historical reports and the exact three source candidates are immutable
    # evidence, not optional advisory metadata. Check the files, not just the
    # strings stored in the previous diagnostic.
    report_checks={
        'v19_report':(Path(args.v19_run)/'report.json','v19_report_sha256'),
        'v20_report':(Path(args.v20_run)/'report.json','v20_report_sha256'),
        'v21_report':(Path(args.v21_run)/'report.json','v21_report_sha256')}
    candidate_files={
        'v14_post_consolidation':Path(args.v14_run)/'post_consolidation_candidate.pt',
        'v19_precision_joint':Path(args.v19_run)/'precision_joint_disposable_candidate.pt',
        'v21_shadow_precision':Path(args.v21_run)/'shadow_precision_disposable_candidate.pt'}
    expected={x.get('arm'):x.get('checkpoint_sha256')
              for x in r.get('starting_checkpoints_verified',[])}
    conditions={
        'historical_report_files':all(path.is_file() and r.get(key)==v19._sha(path)
                 for path,key in report_checks.values()),
        'historical_candidate_files':set(expected)==set(candidate_files) and
                 all(path.is_file() and v19._sha(path)==expected[arm]
                     for arm,path in candidate_files.items()),
        'schema':r.get('schema')==v23.SCHEMA,
        'status':r.get('status')=='READ_ONLY_VERIFIED_NO_PROMOTION',
        'lineage':r.get('frozen_lineage')==data['probe_info']['lineage'],
        'no_weights':r.get('no_trainable_parameters_modified') is True,
        'history_unchanged':r.get('history_checkpoints_unchanged') is True,
        'no_promotion':r.get('promotion_authorized') is False,
        'no_test':r.get('previously_seen_test_used_for_fitting_selection_or_metrics') is False,
        'groups':r.get('train_operand_groups')==len(train_rows)//6 and
                 r.get('dev_operand_groups')==len(dev_rows)//6,
        'v22_report_sha256':r.get('v22_report_sha256')==v19._sha(Path(args.v22_run)/'report.json'),
        'capture_lineage':r.get('frozen_lineage')==data['probe_info']['lineage']}
    _require(all(conditions.values()),'V23_VERIFICATION_FAILED: '+','.join(
             k for k,v in conditions.items() if not v))
    return v19._sha(p)


def _capture_token_batch(torch,model,tok,entries,*,device,max_length,layers=LAYERS):
    """Acquire only two operand-span means and last token, not full activations.

    The role ordering is arithmetic (first minus second), which is reversed in
    text for the game surface. Attention and tokenizer offsets are checked.
    """
    import numpy as np
    from tools.nanojev_gamer_v1_sign_information_probe import _backbone_parts
    core=_backbone_parts(model)
    _require(tok.padding_side=='right' and getattr(tok,'is_fast',False),
             'FAST_RIGHT_PAD_TOKENIZER_REQUIRED')
    _require(all(0<=i<len(core.layers) for i in layers),'QWEN_LAYER_NOT_AVAILABLE')
    prompts=[e['prompt'] for e in entries]
    positions=[];expected=[]
    for e in entries:
        first,second,a,b=_spans(e['prompt'],e['row']['surface'])
        _require((a,b)==(Fraction(e['row']['first_operand']),
                         Fraction(e['row']['second_operand'])),
                 'PROMPT_OPERAND_AND_LEDGER_MISMATCH')
        _require(a-b==Fraction(e['row']['target_fraction']),
                 'PROMPT_ORACLE_DISAGREEMENT')
        expected.append((first,second))
    _require(all(len(tok.encode(p,add_special_tokens=True))<=max_length for p in prompts),
             'PROMPT_TRUNCATION_REFUSED')
    enc=tok(prompts,padding=True,truncation=False,return_offsets_mapping=True,
            return_tensors='pt')
    _require('offset_mapping' in enc and 'attention_mask' in enc,
             'OFFSETS_OR_MASK_MISSING')
    offsets=enc.pop('offset_mapping').tolist()
    lengths=enc['attention_mask'].sum(dim=-1).tolist()
    for i,spans in enumerate(expected):
        first=_aligned_tokens(offsets[i][:int(lengths[i])],spans[0])
        second=_aligned_tokens(offsets[i][:int(lengths[i])],spans[1])
        _require(not set(first)&set(second),'NUMERIC_SPANS_SHARE_TOKENS')
        positions.append((first,second,int(lengths[i])-1))
    inputs={k:v.to(device) for k,v in enc.items()}
    names={'embedding':core.embed_tokens,
           **{f'layer_{i:02d}':core.layers[i] for i in layers},
           'final':core.norm}
    seen={};handles=[]
    def _hook(label):
        def capture(_module,_args,result):
            x=result[0] if isinstance(result,(tuple,list)) else result
            _require(isinstance(x,torch.Tensor) and x.ndim==3 and
                     x.shape[0]==len(entries),'QWEN_HOOK_OUTPUT_INVALID: '+label)
            values=[]
            for i,(a,b,last) in enumerate(positions):
                _require(last<x.shape[1],'HOOK_LENGTH_MISMATCH')
                values.append(torch.stack([x[i,a,:].float().mean(dim=0),
                         x[i,b,:].float().mean(dim=0),x[i,last,:].float()],dim=0)
                         .detach().cpu().numpy())
            _require(label not in seen,'REPEATED_QWEN_HOOK: '+label)
            seen[label]=np.asarray(values,dtype=np.float32)
        return capture
    for label,module in names.items():
        handles.append(module.register_forward_hook(_hook(label)))
    try:
        with torch.inference_mode():
            model(**inputs,output_hidden_states=False,return_dict=True,
                  logits_to_keep=1)
    finally:
        for handle in handles:handle.remove()
    _require(set(seen)==set(names),'INCOMPLETE_TOKEN_CAPTURE')
    _require(all(v.shape[0]==len(entries) and v.shape[1]==3 and
                 np.isfinite(v).all() for v in seen.values()),
             'INVALID_CAPTURE_SHAPE_OR_FINITE')
    span_records=[]
    for e,(a,b,last),spans in zip(entries,positions,expected):
        span_records.append({'id':e['row']['id'],'surface':e['row']['surface'],
         'group':e['row']['group'],'first_char_span':str(spans[0]),
         'second_char_span':str(spans[1]),'first_token_indices':str(a),
         'second_token_indices':str(b),'last_token_index':last,
         'first_token_count':len(a),'second_token_count':len(b),
         'first_before_second_in_text':int(min(a)<min(b)),
         'prompt_sha256':e['row']['prompt_sha256']})
    return seen,span_records


def _collect_variant(torch,tok,model,all_rows,prompts,*,variant,
                     device,batch_size,max_length,layers):
    import numpy as np
    names=['embedding',*[f'layer_{i:02d}' for i in layers],'final']
    gathered={name:[] for name in names};spans=[]
    for start in range(0,len(all_rows),batch_size):
        entries=[{'row':row,'prompt':prompts[row['archive_index']]}
                 for row in all_rows[start:start+batch_size]]
        batch,positions=_capture_token_batch(torch,model,tok,entries,
                       device=device,max_length=max_length,layers=layers)
        for name in names:gathered[name].append(batch[name])
        spans.extend([{'variant':variant,**r} for r in positions])
        if start==0 or start//batch_size%30==0:
            print('V24_CAPTURE '+probe.canonical({'variant':variant,
             'complete':min(start+batch_size,len(all_rows)),
             'total':len(all_rows)}),flush=True)
    return {k:np.concatenate(v,axis=0) for k,v in gathered.items()},spans


def _archive_parity(live,archive,indices,variant):
    """Check that V24 final-last positions reproduce V23's verified capture."""
    import numpy as np
    stored=np.asarray(archive[variant+'__final_last'][indices],dtype=np.float32)
    current=live['final'][:,2,:]
    _require(current.shape==stored.shape,'ARCHIVE_PARITY_SHAPE')
    delta=np.abs(current-stored)
    maxdelta=float(np.max(delta));rmse=float(np.sqrt(np.mean(delta**2)))
    _require(maxdelta<=0.35 and rmse<=0.05,
             f'ARCHIVE_FINAL_LAST_PARITY_FAILURE: {variant}: max={maxdelta} rmse={rmse}')
    return {'variant':variant,'max_abs_difference':maxdelta,'rmse':rmse,
            'note':'archived final-last vectors stored in float16'}


def _features(captured,name,kind):
    import numpy as np
    x=np.asarray(captured[name],dtype=np.float64)
    if kind=='ordered_operand_pair':return np.concatenate([x[:,0,:],x[:,1,:]],axis=1)
    if kind=='unordered_operand_mean':return (x[:,0,:]+x[:,1,:])/2
    if kind=='prompt_last_control':return x[:,2,:]
    raise ValueError('V24_UNKNOWN_FEATURE_POLICY')


def _predict_operands(train_x,eval_x,train_rows,eval_rows,alpha=4.,*,seed=47):
    """Fixed TRAIN-only, dual-target kernel ridge. Neither DEV nor heldout fits.

    Predicted floats are snapped to a predeclared 0.01 lattice; exact Fraction
    subtraction then uses *those predictions*, never the prompt's oracle values.
    """
    import numpy as np
    _require(alpha>0 and np.isfinite(alpha),'INVALID_RIDGE_ALPHA')
    a=np.asarray(train_x,dtype=np.float64)
    b=np.asarray(eval_x,dtype=np.float64)
    _require(a.ndim==2 and b.ndim==2 and a.shape[1]==b.shape[1] and
             len(a)==len(train_rows) and len(b)==len(eval_rows),
             'READOUT_SHAPE_MISMATCH')
    _require(np.isfinite(a).all() and np.isfinite(b).all(),
             'READOUT_NONFINITE_FEATURE')
    mu=a.mean(0);sd=np.maximum(a.std(0),1e-3)
    a=np.clip((a-mu)/sd,-16,16);b=np.clip((b-mu)/sd,-16,16)
    k=a@a.T/a.shape[1];cross=b@a.T/a.shape[1]
    y=np.asarray([[float(Fraction(row['first_operand'])),
                   float(Fraction(row['second_operand']))] for row in train_rows])
    ym=y.mean(0);ys=np.maximum(y.std(0),1e-6)
    sol=np.linalg.solve(k+alpha*np.eye(len(k)),(y-ym)/ys)
    estimated=(cross@sol)*ys+ym
    _require(np.isfinite(estimated).all(),'READOUT_NONFINITE_PREDICTION')
    oracle_y=np.asarray([[float(Fraction(row['first_operand'])),
                 float(Fraction(row['second_operand']))] for row in eval_rows])
    pred_pairs=[(Fraction(round(float(p)*100),100),
                 Fraction(round(float(q)*100),100)) for p,q in estimated]
    exact_pairs=[(Fraction(r['first_operand']),Fraction(r['second_operand'])) for r in eval_rows]
    signs=np.asarray([int(p>q)-int(p<q) for p,q in pred_pairs],dtype=np.int8)
    pred_exact=[p-q for p,q in pred_pairs]
    true_exact=[p-q for p,q in exact_pairs]
    outcomes={'first_operand_mae':float(np.mean(np.abs(estimated[:,0]-oracle_y[:,0]))),
      'second_operand_mae':float(np.mean(np.abs(estimated[:,1]-oracle_y[:,1]))),
      'first_within_0_01':int(np.sum(np.abs(estimated[:,0]-oracle_y[:,0])<=.01)),
      'second_within_0_01':int(np.sum(np.abs(estimated[:,1]-oracle_y[:,1])<=.01)),
      'both_operands_exact_0_01':sum(pair==truth for pair,truth in zip(pred_pairs,exact_pairs)),
      'exact_difference_matches':sum(x==y for x,y in zip(pred_exact,true_exact)),
      'exact_zero_preserved':int(sum(x==0 and y==0 for x,y in zip(pred_exact,true_exact))),
      'sample_count':len(eval_rows),
      'sign_metrics':v23._scores(eval_rows,signs)}
    rng=np.random.default_rng(seed);null=[]
    for _ in range(3):
        shuffled=y[rng.permutation(len(y))]
        pred=(cross@np.linalg.solve(k+alpha*np.eye(len(k)),
                       (shuffled-ym)/ys))*ys+ym
        psg=np.sign(pred[:,0]-pred[:,1]).astype(np.int8)
        null.append(v23._scores(eval_rows,psg)['correct'])
    outcomes['shuffled_pair_null_sign_correct']=null
    rows=[]
    for row,pred,pair,truth in zip(eval_rows,estimated,pred_pairs,exact_pairs):
        actual=truth[0]-truth[1];computed=pair[0]-pair[1]
        rows.append({'id':row['id'],'group':row['group'],'surface':row['surface'],
           'true_first':str(truth[0]),'true_second':str(truth[1]),
           'pred_first_raw':float(pred[0]),'pred_second_raw':float(pred[1]),
           'decoded_first_0_01':str(pair[0]),'decoded_second_0_01':str(pair[1]),
           'true_difference':str(actual),'decoded_difference':str(computed),
           'both_operands_exact':int(pair==truth),
           'difference_exact':int(computed==actual),
           'sign_correct':int((computed>0)-(computed<0)==(actual>0)-(actual<0)),
           'true_sign':row['target_sign'],
           'pred_sign':int(computed>0)-int(computed<0)})
    return outcomes,rows


def _run(args):
    import gc
    import numpy as np
    import torch
    from tools import nanojev_gamer_v1_handoff as gamer
    _require(not str(args.device).startswith('cuda') or torch.cuda.is_available(),
             'CUDA_NOT_AVAILABLE')
    _require(args.capture_batch>=1 and args.capture_batch<=8 and
             args.ridge_alpha>0 and args.max_input_tokens>0,
             'INVALID_CAPTURE_OR_READOUT_CONFIG')
    sources=[Path(getattr(args,k)) for k in ('run','expanded_run','input','source_features',
       'prior','v11_run','v14_run','v19_run','v20_run','v21_run','v22_run','v23_run')]
    out=lab._output(args.output,sources)
    data,prepared=joint._prepare(args)  # exact source/cache/probe verification
    _require(bool(prepared),'VERIFIED_FEATURE_PREP_UNAVAILABLE')
    info=data['probe_info']
    train=v23._operand_rows(info,'train');dev=v23._operand_rows(info,'dev')
    sha23=_validated_v23(args,data,train,dev)
    prompts=_indexed_prompts(info)
    all_rows=train+dev
    for r in all_rows:
        prompt=prompts[r['archive_index']]
        _require(probe.sha_text(prompt)==r['prompt_sha256'],
                 'OPERAND_PROMPT_HASH_MISMATCH')
    fit,heldout,held_groups=_group_fit_split(train)
    _require(not set(fit)&set(heldout) and
             not set(r['group'] for r in (train[i] for i in fit))&set(held_groups),
             'READOUT_FIT_HELDOUT_GROUP_LEAK')
    checked_history=[Path(args.v14_run)/'post_consolidation_candidate.pt',
        Path(args.v19_run)/'precision_joint_disposable_candidate.pt',
        Path(args.v21_run)/'shadow_precision_disposable_candidate.pt',
        Path(args.v19_run)/'report.json',Path(args.v20_run)/'report.json',
        Path(args.v21_run)/'report.json']
    history_hashes={str(path):v19._sha(path) for path in checked_history}
    sha_before={'v23_report':sha23,'v22_report':v19._sha(Path(args.v22_run)/'report.json'),
      'original_head':spine.file_sha(Path(info['lineage']['checkpoint_path'])),
      'adapter':spine.adapter_identity(Path(info['lineage']['adapter_dir']))}
    _require(sha_before['original_head']==info['lineage']['checkpoint_sha256'] and
             sha_before['adapter']==info['lineage']['adapter_sha256'],
             'FROZEN_WEIGHTS_FINGERPRINT_MISMATCH')
    _require(args.max_input_tokens<=info['lineage']['configuration']['max_length'],
             'INPUT_LENGTH_EXCEEDS_FROZEN_POLICY')
    layers=LAYERS;results={};offset_rows=[];parities=[]
    spine.cache_policy(args.allow_model_download)
    for variant in ('base','lora'):
        adapter=Path(info['lineage']['adapter_dir']) if variant=='lora' else None
        _,tok,model=gamer.model_components(info['lineage']['model_id'],args.device,
                                           adapter_dir=adapter)
        model.eval()
        model.requires_grad_(False)
        data_variant,alignments=_collect_variant(torch,tok,model,all_rows,prompts,
           variant=variant,device=args.device,batch_size=args.capture_batch,
           max_length=args.max_input_tokens,layers=layers)
        offset_rows.extend(alignments)
        parities.append(_archive_parity(data_variant,data['probe_arrays'],
             np.asarray([r['archive_index'] for r in all_rows]),variant))
        results[variant]=data_variant
        del model,tok
        gc.collect()
        if torch.cuda.is_available():torch.cuda.empty_cache()
    readouts=[];cases=[]
    for variant in ('base','lora'):
        for stage in ('embedding',*[f'layer_{i:02d}' for i in layers],'final'):
            for kind in ('ordered_operand_pair','unordered_operand_mean','prompt_last_control'):
                features=_features(results[variant],stage,kind)
                fit_x=features[fit]
                fit_rows=[train[i] for i in fit]
                for scope,ix,eval_rows in (
                    ('heldout_in_range_train_groups',heldout,[train[i] for i in heldout]),
                    ('dev_out_of_range',list(range(len(train),len(all_rows))),dev)):
                    summary,individual=_predict_operands(fit_x,features[ix],fit_rows,
                         eval_rows,args.ridge_alpha)
                    rec={'variant':variant,'stage':stage,'policy':kind,
                         'evaluation':scope,'fit_groups':len(fit)//6,
                         'heldout_groups':len(held_groups),**{k:v for k,v in summary.items()
                          if k!='sign_metrics'},**{('sign_'+k):v for k,v in summary['sign_metrics'].items()}}
                    readouts.append(rec)
                    cases.extend({'variant':variant,'stage':stage,'policy':kind,
                                  'evaluation':scope,**row} for row in individual)
                print('V24_READOUT '+probe.canonical({'variant':variant,'stage':stage,
                      'policy':kind,'evaluations':2}),flush=True)
    # No selection by the DEV metrics. Report all fixed readouts, no champion.
    joint._verify_after(data)
    _require(v19._sha(Path(args.v23_run)/'report.json')==sha_before['v23_report'] and
             v19._sha(Path(args.v22_run)/'report.json')==sha_before['v22_report'] and
             spine.file_sha(Path(info['lineage']['checkpoint_path']))==sha_before['original_head'] and
             spine.adapter_identity(Path(info['lineage']['adapter_dir']))==sha_before['adapter'] and
             all(v19._sha(path)==history_hashes[str(path)] for path in checked_history),
             'SOURCE_HISTORY_MUTATED_DURING_CAPTURE')
    report={'schema':SCHEMA,'status':'READ_ONLY_CAPTURE_AND_AUDIT_COMPLETE_NO_PROMOTION',
       'algorithm':'VERIFIED_PROMPT_SPAN_TOKEN_CAUSAL_READOUT_FIXED_TRAIN_KERNEL_RIDGE',
       'v23_report_sha256':sha23,'v22_report_sha256':sha_before['v22_report'],
       'frozen_lineage':info['lineage'],
       'source_dataset_sha256':info['dataset_sha256'],
       'source_capture_sha256':v19._sha(Path(args.input)/probe.CAPTURE_NAME),
       'capture_cases':len(all_rows),'captured_splits':['train','dev'],
       'test_cases_forwarded':0,'train_fit_groups':len(fit)//6,
       'train_in_range_heldout_groups':held_groups,
       'dev_out_of_range_groups':len(dev)//6,
       'fixed_probe_alpha':args.ridge_alpha,
       'tokenizer_policy':'fast offsets, untruncated, right padded, actual numbered spans',
       'capture_stages':['embedding',*[f'layer_{i:02d}' for i in layers],'final'],
       'archive_final_last_parity':parities,
       'readouts':readouts,'readout_fit_on_dev_or_test':False,
       'no_qwen_or_lora_training':True,'no_existing_specialist_or_head_training':True,
       'production_weights_modified':False,'checkpoint_saved':False,
       'promotion_authorized':False,'historical_sources_unchanged':True,
       'limitations':[
           'TRAIN group holdouts are diagnostic; readout holdouts do not establish independent external generalization',
           'Synthetic DEV operand magnitudes exceed TRAIN; model numerical extrapolation is separately reported',
           'Source and game prompts both request subtraction; operation recognition beyond subtraction is not tested',
           'A contextual hidden-state readout can exploit task correlations without literal number extraction',
           'Exact Fraction arithmetic uses decoded 0.01 quantized readout estimates, not true oracle operands',
           'Exact textual operand parsing is independent oracle scaffolding, not Qwen operand extraction',
           'No LoRA update is justified solely by a negative linear/kernel operand readout',
           'No promotion, routing change, or replacement of zero-line authority']}
    out.mkdir(parents=True,exist_ok=False)
    from tools.nanojev_gamer_v1_conflict_projection_v10 import _csv
    _csv(out/'token_spans.csv',offset_rows)
    _csv(out/'operand_readouts.csv',readouts)
    _csv(out/'operand_predictions.csv',cases)
    _csv(out/'heldout_group_manifest.csv',[{'group':g,'policy':'within_train_group_heldout'}
                                             for g in held_groups])
    (out/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n',
                                   encoding='utf-8')
    print('V24_COMPLETE '+probe.canonical({'status':report['status'],
        'readouts':len(readouts),'cases':len(all_rows),
        'promotion':False,'checkpoint_saved':False,'report':str(out/'report.json')}),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',default='audit',choices=('audit',))
    for name in ('run','expanded-run','input','source-features','prior',
                 'v11-run','v14-run','v19-run','v20-run','v21-run','v22-run',
                 'v23-run','output'):
        p.add_argument('--'+name,required=True,type=Path)
    p.add_argument('--device',default='cuda')
    p.add_argument('--capture-batch',type=int,default=2)
    p.add_argument('--max-input-tokens',type=int,default=1024)
    p.add_argument('--ridge-alpha',type=float,default=4.)
    p.add_argument('--allow-model-download',action='store_true')
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    _run(args)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
