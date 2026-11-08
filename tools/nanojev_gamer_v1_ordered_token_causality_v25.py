#!/usr/bin/env python3
"""V25: intact ordered number-token causality diagnostic, frozen Qwen/LoRA.

The only fitted objects are TRAIN-only diagnostic ridge readouts and token-ID
centroids. DEV and whole held-out TRAIN groups never participate in fitting or
hyperparameter selection. Prior checkpoints, production routing, and zero-line
remain untouched. A literal-tokenizer roundtrip is an input-information control,
not a model prediction; recovered token IDs are evaluated separately.
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

from tools import nanojev_gamer_v1_operand_token_causality_v24 as v24
from tools import nanojev_gamer_v1_operand_causality_v23 as v23
from tools import nanojev_gamer_v1_sign_information_probe as probe
from tools import nanojev_gamer_v1_joint_head_repair as joint
from tools import nanojev_gamer_v1_head_only_lab as lab
from tools import nanojev_gamer_v1_precision_continuation_v19 as v19
from tools import nanojev_two_game_spine_smoke as spine

SCHEMA='nanojev.gamer.v1.ordered-token-causality.v25'
STAGES=('embedding','layer_09','layer_18','layer_27','final')
POLICIES=('ordered_token_sequence','span_mean_v24_control',
          'reversed_within_operand_control','swapped_operand_roles_control')
NUMERIC=re.compile(r'[+-]?[0-9]+(?:\.[0-9]+)?(?:/[0-9]+)?\Z')


def _require(condition,reason):
    if not condition:raise ValueError('V25_'+reason)


def _parse_decoded_number(value):
    """Never consult the oracle or prompt to repair a predicted token string."""
    clean=value.strip()
    if not NUMERIC.fullmatch(clean):return None
    try:return Fraction(clean)
    except (ValueError,ZeroDivisionError):return None


def _decode_ids(tok,ids):
    return tok.decode([int(x) for x in ids],skip_special_tokens=False,
                      clean_up_tokenization_spaces=False)


def _validated_v24(args,data,train,dev):
    sha23=v24._validated_v23(args,data,train,dev)
    path=Path(args.v24_run)/'report.json'
    _require(path.is_file(),'V24_REPORT_NOT_FOUND')
    r=json.loads(path.read_text(encoding='utf-8'))
    info=data['probe_info']
    tests={
      'schema':r.get('schema')==v24.SCHEMA,
      'status':r.get('status')=='READ_ONLY_CAPTURE_AND_AUDIT_COMPLETE_NO_PROMOTION',
      'lineage':r.get('frozen_lineage')==info['lineage'],
      'dataset':r.get('source_dataset_sha256')==info['dataset_sha256'],
      'capture':r.get('source_capture_sha256')==v19._sha(Path(args.input)/probe.CAPTURE_NAME),
      'v23':r.get('v23_report_sha256')==sha23,
      'v22':r.get('v22_report_sha256')==v19._sha(Path(args.v22_run)/'report.json'),
      'counts':r.get('capture_cases')==len(train)+len(dev) and
                 r.get('train_fit_groups')==len(v24._group_fit_split(train)[0])//6 and
                 r.get('dev_out_of_range_groups')==len(dev)//6,
      'no_test':r.get('test_cases_forwarded')==0 and
                 r.get('readout_fit_on_dev_or_test') is False,
      'frozen':r.get('no_qwen_or_lora_training') is True and
                 r.get('no_existing_specialist_or_head_training') is True and
                 r.get('production_weights_modified') is False and
                 r.get('checkpoint_saved') is False and
                 r.get('promotion_authorized') is False and
                 r.get('historical_sources_unchanged') is True,
      'stage_count':len(r.get('readouts',[]))==60,
      'parity':{x.get('variant') for x in r.get('archive_final_last_parity',[])}=={'base','lora'}
    }
    _require(all(tests.values()),'V24_PROVENANCE_MISMATCH: '+','.join(k for k,v in tests.items() if not v))
    return v19._sha(path),r


def _capture_batch(torch,model,tok,entries,*,device,max_length,max_operand_tokens,
                   layers=v24.LAYERS):
    """Gather each individual ordered operand-token vector, without averaging.

    `role` is arithmetic, so T precedes R even when DC=T follows CC=R in
    the game prompt. Padding masks prevent padding from being mistaken as data.
    """
    from tools.nanojev_gamer_v1_sign_information_probe import _backbone_parts
    core=_backbone_parts(model)
    _require(getattr(tok,'is_fast',False) and tok.padding_side=='right',
             'FAST_RIGHT_PAD_TOKENIZER_REQUIRED')
    _require(all(0<=i<len(core.layers) for i in layers),'MISSING_QWEN_LAYER')
    prompts=[e['prompt'] for e in entries]
    _require(all(len(tok.encode(p,add_special_tokens=True))<=max_length for p in prompts),
             'PROMPT_TRUNCATION_REFUSED')
    enc=tok(prompts,padding=True,truncation=False,return_offsets_mapping=True,
            return_tensors='pt')
    _require('offset_mapping' in enc and 'attention_mask' in enc,
             'MISSING_TOKEN_OFFSETS')
    offsets=enc.pop('offset_mapping').tolist()
    lengths=enc['attention_mask'].sum(dim=1).tolist()
    ids=enc['input_ids'].tolist()
    positions=[]
    mask=np.zeros((len(entries),2,max_operand_tokens),dtype=np.float32)
    label_ids=np.full((len(entries),2,max_operand_tokens),-1,dtype=np.int64)
    alignment=[]
    for i,e in enumerate(entries):
        row=e['row']; spans= v24._spans(e['prompt'],row['surface'])
        first,second,a,b=spans
        _require((a,b)==(Fraction(row['first_operand']),Fraction(row['second_operand'])) and
                 a-b==Fraction(row['target_fraction']),'PROMPT_ORACLE_LEDGER_MISMATCH')
        index_a=v24._aligned_tokens(offsets[i][:int(lengths[i])],first)
        index_b=v24._aligned_tokens(offsets[i][:int(lengths[i])],second)
        _require(not set(index_a)&set(index_b),'OPERAND_TOKEN_OVERLAP')
        _require(max(len(index_a),len(index_b))<=max_operand_tokens,
                 'OPERAND_TOKEN_CAP_EXCEEDED: '+row['id'])
        positions.append((index_a,index_b,int(lengths[i])-1))
        for j,ix in enumerate((index_a,index_b)):
            mask[i,j,:len(ix)]=1
            label_ids[i,j,:len(ix)]=[ids[i][k] for k in ix]
        baseline=tuple(_parse_decoded_number(_decode_ids(tok,[ids[i][k] for k in ix]))
                       for ix in (index_a,index_b))
        alignment.append({'id':row['id'],'group':row['group'],'surface':row['surface'],
            'prompt_sha256':row['prompt_sha256'],
            'first_token_ids':json.dumps([ids[i][j] for j in index_a]),
            'second_token_ids':json.dumps([ids[i][j] for j in index_b]),
            'first_token_indices':json.dumps(index_a),'second_token_indices':json.dumps(index_b),
            'first_char_span':str(first),'second_char_span':str(second),
            'first_token_count':len(index_a),'second_token_count':len(index_b),
            'first_before_second_in_text':int(index_a[0]<index_b[0]),
            'tokenizer_roundtrip_first_exact':int(baseline[0]==a),
            'tokenizer_roundtrip_second_exact':int(baseline[1]==b),
            'tokenizer_roundtrip_both_exact':int(baseline==(a,b))})
    inputs={key:value.to(device) for key,value in enc.items()}
    names={'embedding':core.embed_tokens,
           **{f'layer_{j:02d}':core.layers[j] for j in layers},
           'final':core.norm}
    outputs={};summaries={};handles=[]
    def hook(label):
        def capture(_module,_args,result):
            x=result[0] if isinstance(result,(tuple,list)) else result
            _require(isinstance(x,torch.Tensor) and x.ndim==3 and
                     x.shape[0]==len(entries),'INVALID_HOOK_OUTPUT_'+label)
            feature_width=x.shape[-1]
            seq=np.zeros((len(entries),2,max_operand_tokens,feature_width),dtype=np.float32)
            summary=np.zeros((len(entries),3,feature_width),dtype=np.float32)
            for k,(ia,ib,last) in enumerate(positions):
                _require(last<x.shape[1],'HOOK_LENGTH_MISMATCH')
                for role,ix in enumerate((ia,ib)):
                    t=x[k,ix,:].float().detach().cpu().numpy()
                    seq[k,role,:len(ix),:]=t
                    summary[k,role,:]=t.mean(0)
                summary[k,2,:]=x[k,last,:].float().detach().cpu().numpy()
            _require(label not in outputs,'DUPLICATE_HOOK_'+label)
            outputs[label]=seq;summaries[label]=summary
        return capture
    for label,module in names.items():
        handles.append(module.register_forward_hook(hook(label)))
    try:
        with torch.inference_mode():
            model(**inputs,output_hidden_states=False,return_dict=True,logits_to_keep=1)
    finally:
        for h in handles:h.remove()
    _require(set(outputs)==set(names),'INCOMPLETE_HOOK_CAPTURE')
    _require(all(np.isfinite(x).all() for x in outputs.values()),
             'NONFINITE_TOKEN_REPRESENTATION')
    # Embedding is an exact token table lookup. Check its identity locally:
    # this does NOT establish that any later layer retains token identities.
    with torch.inference_mode():
        lookup=core.embed_tokens(inputs['input_ids'])
    maximum=0.
    for k,(ia,ib,last) in enumerate(positions):
        for role,ix in enumerate((ia,ib)):
            original=lookup[k,ix,:].float().detach().cpu().numpy()
            maximum=max(maximum,float(np.max(np.abs(original-outputs['embedding'][k,role,:len(ix),:]))))
    _require(maximum<1e-5,'INPUT_EMBEDDING_LOOKUP_PARITY')
    return outputs,summaries,mask,label_ids,alignment,maximum


def _collect_variant(torch,tok,model,all_rows,prompts,*,variant,device,batch_size,
                     max_length,max_operand_tokens):
    outputs={s:[] for s in STAGES};summaries={s:[] for s in STAGES}
    masks=[];labels=[];spans=[];parity=0.
    for start in range(0,len(all_rows),batch_size):
        entries=[{'row':r,'prompt':prompts[r['archive_index']]}
                 for r in all_rows[start:start+batch_size]]
        seq,last,mask,ids,rows,diff=_capture_batch(torch,model,tok,entries,
             device=device,max_length=max_length,max_operand_tokens=max_operand_tokens)
        for s in STAGES:
            outputs[s].append(seq[s]);summaries[s].append(last[s])
        masks.append(mask);labels.append(ids)
        spans.extend({'variant':variant,**r} for r in rows)
        parity=max(parity,diff)
        if start==0 or (start//batch_size)%30==0:
            print('V25_CAPTURE '+probe.canonical({'variant':variant,'complete':min(
                  start+batch_size,len(all_rows)),'total':len(all_rows)}),flush=True)
    return ({k:np.concatenate(v,axis=0) for k,v in outputs.items()},
            {k:np.concatenate(v,axis=0) for k,v in summaries.items()},
            np.concatenate(masks,axis=0),np.concatenate(labels,axis=0),spans,parity)


def _feature_matrix(tensors,means,mask,stage,policy):
    seq=np.asarray(tensors[stage],dtype=np.float64)
    mask=np.asarray(mask,dtype=np.float64)
    n,roles,slots,width=seq.shape
    _require(roles==2 and mask.shape==(n,2,slots), 'SEQUENCE_MASK_SHAPE')
    if policy=='span_mean_v24_control':
        return np.asarray(means[stage][:,:2,:],dtype=np.float64).reshape(n,-1)
    if policy=='ordered_token_sequence':
        return np.concatenate((seq.reshape(n,-1),mask.reshape(n,-1)),axis=1)
    if policy=='swapped_operand_roles_control':
        return np.concatenate((seq[:,::-1,:,:].reshape(n,-1),
                               mask[:,::-1,:].reshape(n,-1)),axis=1)
    if policy=='reversed_within_operand_control':
        altered=seq.copy()
        for i in range(n):
            for role in range(2):
                count=int(mask[i,role].sum())
                altered[i,role,:count,:]=seq[i,role,:count,:][::-1,:]
        return np.concatenate((altered.reshape(n,-1),mask.reshape(n,-1)),axis=1)
    raise ValueError('V25_UNKNOWN_READOUT_POLICY')


def _token_identity_recovery(captured,ids,mask,fit_indices,eval_indices,rows,tok,
                             *,variant,stage,scope):
    """TRAIN-only token prototypes. Unseen token IDs remain OOV, not successes.

    Targets are token identities, never signs or mathematical oracle outputs.
    No DEV token label contributes to the reference centroids or classes.
    """
    vectors=np.asarray(captured[stage],dtype=np.float64)
    _require(vectors.ndim==4 and vectors.shape[:3]==ids.shape==mask.shape,
             'TOKEN_ID_FEATURE_SHAPE')
    by_token=defaultdict(list)
    for i in fit_indices:
        for role in range(2):
            for position in range(vectors.shape[2]):
                if mask[i,role,position]:
                    by_token[int(ids[i,role,position])].append(vectors[i,role,position])
    _require(not set(fit_indices)&set(eval_indices),'TOKEN_ID_TRAIN_EVALUATION_OVERLAP')
    _require(bool(by_token),'EMPTY_TRAIN_TOKEN_DICTIONARY')
    vocabulary=sorted(by_token)
    centers=np.asarray([np.mean(by_token[t],axis=0) for t in vocabulary])
    centers=centers/(np.linalg.norm(centers,axis=1,keepdims=True)+1e-12)
    known=correct=total=both_tokens=both_numeric=0
    records=[];novel_types=set()
    for i in eval_indices:
        row=rows[i]; actual=[Fraction(row['first_operand']),Fraction(row['second_operand'])]
        predicted=[];operand_exact=[];all_known=True;all_token_correct=True
        for role in range(2):
            n=int(mask[i,role].sum()); truth=[int(x) for x in ids[i,role,:n]]
            source=vectors[i,role,:n,:]
            source=source/(np.linalg.norm(source,axis=1,keepdims=True)+1e-12)
            similarity=source@centers.T
            guess=[vocabulary[j] for j in similarity.argmax(axis=1)]
            flags=[t in by_token for t in truth]
            known+=sum(flags);total+=n
            correct+=sum(int(p==t) for p,t,known_token in zip(guess,truth,flags)
                         if known_token)
            all_known=all_known and all(flags)
            novel_types.update(t for t,flag in zip(truth,flags) if not flag)
            all_token_correct=all_token_correct and guess==truth
            decoded=_parse_decoded_number(_decode_ids(tok,guess))
            predicted.append(decoded)
            operand_exact.append(decoded==actual[role])
        both_tokens+=int(all_token_correct)
        both_numeric+=int(all(operand_exact))
        records.append({'variant':variant,'stage':stage,'evaluation':scope,
                        'id':row['id'],'group':row['group'],'surface':row['surface'],
                        'both_token_ids_exact':int(all_token_correct),
                        'both_operands_exact_from_predicted_ids':int(all(operand_exact)),
                        'all_token_ids_seen_in_fit':int(all_known),
                        'first_reconstructed':str(predicted[0]) if predicted[0] is not None else '',
                        'second_reconstructed':str(predicted[1]) if predicted[1] is not None else '',
                        'true_first':str(actual[0]),'true_second':str(actual[1])})
    return ({'variant':variant,'stage':stage,'evaluation':scope,
             'train_token_vocabulary_size':len(vocabulary),
             'token_positions':total,'seen_token_positions':known,
             'unseen_token_positions':total-known,'unseen_token_types':len(novel_types),
             'known_token_identity_correct':correct,
             'known_token_identity_accuracy':correct/known if known else None,
             'both_token_sequences_exact':both_tokens,
             'both_operands_exact_from_token_ids':both_numeric,
             'cases':len(eval_indices)},records)


def _run(args):
    import torch
    from tools import nanojev_gamer_v1_handoff as gamer
    _require(args.capture_batch>=1 and args.capture_batch<=8 and
             args.max_operand_tokens>=1 and args.max_operand_tokens<=64 and
             args.max_input_tokens>0 and args.ridge_alpha>0 and
             (not str(args.device).startswith('cuda') or torch.cuda.is_available()),
             'INVALID_CAPTURE_CONFIG_OR_DEVICE')
    sources=[Path(getattr(args,name)) for name in ('run','expanded_run','input',
       'source_features','prior','v11_run','v14_run','v19_run','v20_run',
       'v21_run','v22_run','v23_run','v24_run')]
    out=lab._output(args.output,sources)
    data,prepared=joint._prepare(args)
    _require(bool(prepared),'SOURCE_VERIFICATION_UNAVAILABLE')
    info=data['probe_info']
    train=v23._operand_rows(info,'train');dev=v23._operand_rows(info,'dev')
    sha24,oldreport=_validated_v24(args,data,train,dev)
    fit,heldout,held_groups=v24._group_fit_split(train)
    prompts=v24._indexed_prompts(info)
    rows=train+dev
    _require(all(probe.sha_text(prompts[r['archive_index']])==r['prompt_sha256']
                 for r in rows),'PROMPT_SHA_MISMATCH')
    _require(args.max_input_tokens<=info['lineage']['configuration']['max_length'],
             'INPUT_LENGTH_POLICY_EXCEEDED')
    watched=[Path(args.v24_run)/'report.json',Path(args.v23_run)/'report.json',
             Path(args.v22_run)/'report.json',
             Path(args.v19_run)/'report.json',Path(args.v20_run)/'report.json',
             Path(args.v21_run)/'report.json',
             Path(args.v14_run)/'post_consolidation_candidate.pt',
             Path(args.v19_run)/'precision_joint_disposable_candidate.pt',
             Path(args.v21_run)/'shadow_precision_disposable_candidate.pt']
    hashes={str(p):v19._sha(p) for p in watched}
    head_path=Path(info['lineage']['checkpoint_path'])
    adapter_path=Path(info['lineage']['adapter_dir'])
    headhash=spine.file_sha(head_path);adapterhash=spine.adapter_identity(adapter_path)
    _require(headhash==info['lineage']['checkpoint_sha256'] and
             adapterhash==info['lineage']['adapter_sha256'],
             'FROZEN_WEIGHT_LINEAGE_MISMATCH')
    capture_archives={};roundtrips=[];archive_parity=[];embedding_integrity={};
    recons=[];readouts=[];predictions=[];tok_recovery=[];token_predictions=[]
    spine.cache_policy(args.allow_model_download)
    for variant in ('base','lora'):
        adapter=adapter_path if variant=='lora' else None
        _,tok,model=gamer.model_components(info['lineage']['model_id'],args.device,
                                          adapter_dir=adapter)
        model.eval();model.requires_grad_(False)
        tensors,means,mask,ids,spans,embedding_diff=_collect_variant(
          torch,tok,model,rows,prompts,variant=variant,device=args.device,
          batch_size=args.capture_batch,max_length=args.max_input_tokens,
          max_operand_tokens=args.max_operand_tokens)
        parity=v24._archive_parity({'final':means['final']},data['probe_arrays'],
               np.asarray([r['archive_index'] for r in rows]),variant)
        archive_parity.append(parity)
        embedding_integrity[variant]=embedding_diff
        # Retain the complete ordered activation sequences for independent
        # replay: CSV span means alone would repeat V24's information loss.
        capture_archives[variant]={
             **{stage:np.asarray(tensors[stage],dtype=np.float32) for stage in STAGES},
             'token_mask':np.asarray(mask,dtype=np.uint8),
             'operand_token_ids':np.asarray(ids,dtype=np.int64),
             'final_last':np.asarray(means['final'][:,2,:],dtype=np.float32),
             'archive_indices':np.asarray([r['archive_index'] for r in rows],dtype=np.int64)}
        roundtrips.extend(spans)
        for stage in STAGES:
            for scope,ix in (('heldout_in_range_train_groups',heldout),
                             ('dev_out_of_range',list(range(len(train),len(rows))))):
                identity,details=_token_identity_recovery(tensors,ids,mask,fit,ix,rows,tok,
                          variant=variant,stage=stage,scope=scope)
                tok_recovery.append(identity);token_predictions.extend(details)
                print('V25_TOKEN_ID '+probe.canonical({'variant':variant,'stage':stage,
                    'evaluation':scope,'known_correct':identity['known_token_identity_correct'],
                    'known_total':identity['seen_token_positions']}),flush=True)
            for policy in POLICIES:
                feature=_feature_matrix(tensors,means,mask,stage,policy)
                # Shuffling/swapping is an evaluation-only intervention. The
                # fitted readout stays identical to the intact ordered readout;
                # otherwise a refitted model could simply learn to undo the
                # deliberate corruption and confound this causal control.
                train_feature=(_feature_matrix(tensors,means,mask,stage,
                                  'ordered_token_sequence') if policy in (
                           'reversed_within_operand_control',
                           'swapped_operand_roles_control') else feature)
                for scope,ix,examined in (
                    ('heldout_in_range_train_groups',heldout,[train[i] for i in heldout]),
                    ('dev_out_of_range',list(range(len(train),len(rows))),dev)):
                    summary,individual=v24._predict_operands(train_feature[fit],feature[ix],
                              [train[i] for i in fit],examined,args.ridge_alpha)
                    rec={'variant':variant,'stage':stage,'policy':policy,
                         'evaluation':scope,'fit_groups':len(fit)//6,
                         'heldout_groups':len(held_groups),
                         **{k:v for k,v in summary.items() if k!='sign_metrics'},
                         **{'sign_'+k:v for k,v in summary['sign_metrics'].items()}}
                    readouts.append(rec)
                    predictions.extend({'variant':variant,'stage':stage,'policy':policy,
                        'evaluation':scope,**item} for item in individual)
                print('V25_READOUT '+probe.canonical({'variant':variant,'stage':stage,
                          'policy':policy,'evaluations':2}),flush=True)
        del model,tok,tensors,means,mask,ids
        gc.collect()
        if torch.cuda.is_available():torch.cuda.empty_cache()
    joint._verify_after(data)
    _require(all(v19._sha(p)==hashes[str(p)] for p in watched) and
             spine.file_sha(head_path)==headhash and
             spine.adapter_identity(adapter_path)==adapterhash,
             'HISTORICAL_SOURCES_MUTATED')
    report={'schema':SCHEMA,'status':'READ_ONLY_ORDERED_TOKENS_COMPLETE_NO_PROMOTION',
       'algorithm':'ROLE_ORDERED_TOKEN_SEQUENCE_VS_SPAN_MEAN_AND_SHUFFLE_FROZEN_READOUTS',
       'v24_report_sha256':sha24,
       'v23_report_sha256':oldreport['v23_report_sha256'],
       'frozen_lineage':info['lineage'],
       'source_dataset_sha256':info['dataset_sha256'],
       'capture_cases':len(rows),'splits':['train','dev'],'test_cases_forwarded':0,
       'fit_train_groups':len(fit)//6,'heldout_train_groups':held_groups,
       'readout_alpha':args.ridge_alpha,
       'max_operand_tokens':args.max_operand_tokens,'capture_stages':list(STAGES),
       'fixed_readout_policies':list(POLICIES),
       'tokenizer_roundtrip_both_exact':{
            variant:sum(r['tokenizer_roundtrip_both_exact'] for r in roundtrips
                        if r['variant']==variant) for variant in ('base','lora')},
       'embedding_lookup_max_abs_difference':embedding_integrity,
       'archive_final_last_parity':archive_parity,
       'readouts':readouts,'token_identity_readouts':tok_recovery,
       'readout_fit_on_dev_or_test':False,
       'token_identity_centroids_fit_on_train_only':True,
       'activation_archives_contain_only_train_and_dev':True,
       'activation_archive_precision':'float32',
       'source_checkpoints_unchanged':True,'no_qwen_or_lora_training':True,
       'no_specialist_or_original_head_training':True,
       'checkpoint_saved':False,'production_weights_modified':False,
       'promotion_authorized':False,'limits':[
         'Input tokenizer-ID roundtrip is an input-information control, not a model prediction',
         'Token-ID centroids cover only TRAIN-seen IDs; unseen identities are counted as OOV',
         'Kernel ridge operates on finite fixed-width role-ordered token sequences, not an exhaustive nonlinear decoder',
         'Order and role controls perturb evaluation features only; their readout is fitted on intact TRAIN sequences',
         'A failed readout does not prove the numerical information is absent upstream',
         'Contextual layer token IDs need not be linearly aligned with lexical identity',
         'DEV numerical magnitudes exceed the TRAIN range and are never used for fitting or choosing policies',
         'Only signed subtraction is evaluated; neither general operator recognition nor exact rational arithmetic is certified',
         'No change to zero-line authority, original protected game route, or promotion policy']}
    out.mkdir(parents=True,exist_ok=False)
    from tools.nanojev_gamer_v1_conflict_projection_v10 import _csv
    _csv(out/'token_spans_and_input_roundtrip.csv',roundtrips)
    _csv(out/'token_identity_readouts.csv',tok_recovery)
    _csv(out/'token_identity_predictions.csv',token_predictions)
    _csv(out/'sequence_operand_readouts.csv',readouts)
    _csv(out/'sequence_operand_predictions.csv',predictions)
    _csv(out/'heldout_group_manifest.csv',[{'group':g,'policy':'in_range_train_group_holdout'}
                                            for g in held_groups])
    report['activation_archive_sha256']={}
    for variant in ('base','lora'):
        archive_path=out/(variant+'_ordered_operand_tokens.npz')
        np.savez_compressed(archive_path,**capture_archives[variant])
        report['activation_archive_sha256'][variant]=v19._sha(archive_path)
    (out/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n',
                                    encoding='utf-8')
    print('V25_COMPLETE '+probe.canonical({'status':report['status'],
          'cases':len(rows),'readouts':len(readouts),
          'token_identity_readouts':len(tok_recovery),
          'checkpoint_saved':False,'promotion':False,
          'report':str(out/'report.json')}),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('audit',),default='audit')
    for name in ('run','expanded-run','input','source-features','prior',
        'v11-run','v14-run','v19-run','v20-run','v21-run','v22-run','v23-run',
        'v24-run','output'):
        p.add_argument('--'+name,required=True,type=Path)
    p.add_argument('--device',default='cuda')
    p.add_argument('--capture-batch',type=int,default=2)
    p.add_argument('--max-input-tokens',type=int,default=1024)
    p.add_argument('--max-operand-tokens',type=int,default=16)
    p.add_argument('--ridge-alpha',type=float,default=4.)
    p.add_argument('--allow-model-download',action='store_true')
    return p


def main(argv=None):
    _run(parser().parse_args(argv));return 0


if __name__=='__main__':
    raise SystemExit(main())
