#!/usr/bin/env python3
"""Read-only NanoJev sign/zero information localization for the 100-vector Gamer head.

A three-phase diagnostic, NOT a training or checkpoint-promotion command:

  python -m tools.nanojev_gamer_v1_sign_information_probe --mode oracle
  python -m tools.nanojev_gamer_v1_sign_information_probe --mode capture \
    --run runtime/reports/gamer-v1/transition-005 \
    --expanded-run runtime/reports/gamer-v1/transition-005-expanded-head-100-0001 \
    --device cuda --capture-batch 2
  python -m tools.nanojev_gamer_v1_sign_information_probe --mode analyze \
    --input runtime/reports/gamer-v1/transition-005-sign-information-0001

Captures LoRA-on and optional LoRA-off activations from identical prompts, including
100-token final-norm features and selected earlier-layer samples. Evaluates the
frozen production head and fits DISPOSABLE ridge readouts ONLY on grouped train
features. Development selects ridge strength; held-out groups, numerical ranges,
prompt surfaces and an unseen game family test transfer. No production weights,
optimizer, model cache, dataset, or source checkpoint is modified.

Success of a simple readout means information is linearly accessible, not that it
is a faithful representation of arithmetic. Failure does not prove absence of
information: a nonlinear or different sampling/readout may succeed.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import re

from tools import nanojev_gamer_v1_zero_sign_frob as original
from tools import nanojev_two_game_spine_smoke as spine

SCHEMA='nanojev.gamer.v1.sign-information.v1'
CAPTURE_NAME='frozen_features.npz'


def canonical(x):
    return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)


def sha_text(x):
    return hashlib.sha256(x.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class Case:
    id:str
    split:str
    group:str
    surface:str
    prompt:str
    target:Fraction
    family:str
    offset:Fraction

    def public(self):
        return {'id':self.id,'split':self.split,'group':self.group,
                'surface':self.surface,'family':self.family,
                'target':spine.fstr(self.target),'offset':spine.fstr(self.offset),
                'prompt_sha256':sha_text(self.prompt)}


def _center(split,i):
    if split=='train':return Fraction(13+i)
    if split=='dev':return Fraction(151+3*i)
    if split=='test':return Fraction(1001+7*i)
    raise ValueError('BAD_SPLIT')


def make_deck(train_groups=48,dev_groups=24,test_groups=24,include_ood=True):
    """Strict group-disjoint triples, two identical-answer prompt surfaces.

    Entire {-epsilon,0,+epsilon} triples and both renderings live in one split.
    Train/dev/test baselines occupy disjoint and widely separated numeric ranges.
    The extra game-belief family is confined to the held-out test split.
    """
    if min(train_groups,dev_groups,test_groups)<2:
        raise ValueError('AT_LEAST_TWO_GROUPS_REQUIRED_PER_SPLIT')
    eps=(Fraction(1,100),Fraction(1,20),Fraction(1,4),Fraction(1),Fraction(2))
    deck=[]
    for split,count in (('train',train_groups),('dev',dev_groups),('test',test_groups)):
        for i in range(count):
            center=_center(split,i)
            e=eps[i%len(eps)]
            group=f'{split}.offset.{i:04d}'
            for tag,delta in (('negative',-e),('zero',Fraction(0)),('positive',e)):
                ship=center+delta
                prompts={
                    'source_native':original._native_prompt(ship,center),
                    'spine_difference':original._spine_difference_prompt(ship,center)}
                for surface,prompt in prompts.items():
                    deck.append(Case(f'{group}.{tag}.{surface}',split,group,surface,
                                     prompt,delta,'signed_offset',delta))
    if include_ood:
        stage=spine.ordinal_stage(spine.PAYOFF_ORDERS['chicken_hawk_dove'])
        threshold=spine.critical_q(stage)
        if threshold is None or stage.margin(threshold)!=0:
            raise AssertionError('INVALID_HAWK_INDIFFERENCE_ORACLE')
        for i,e in enumerate((Fraction(1,1000),Fraction(1,500),Fraction(1,200),
                              Fraction(1,100),Fraction(1,50),Fraction(1,25))):
            group=f'test.hawk.{i:04d}'
            for tag,delta in (('negative',-e),('zero',Fraction(0)),('positive',e)):
                q=threshold+delta
                if q<0 or q>1:
                    raise AssertionError('HAWK_BELIEF_OUT_OF_RANGE')
                target=stage.margin(q)
                for surface,render in (('spine_belief',original._spine_belief_prompt),
                                       ('game_prose_belief',original._game_prose_belief_prompt)):
                    deck.append(Case(f'{group}.{tag}.{surface}','test',group,surface,
                                     render(stage,q),target,'hawk_indifference',delta))
    validate_deck(deck)
    return deck


def validate_deck(deck):
    if not deck or len(set(x.id for x in deck))!=len(deck):
        raise ValueError('EMPTY_OR_DUPLICATED_DATASET')
    groups=defaultdict(list)
    for x in deck:
        if x.split not in ('train','dev','test'):
            raise ValueError('BAD_SPLIT')
        if original.independent_oracle(original.Probe(
                x.id,x.group,'controlled',x.surface,x.prompt,x.target))!=x.target:
            raise ValueError('INDEPENDENT_ORACLE_MISMATCH: '+x.id)
        groups[x.group].append(x)
    for group,items in groups.items():
        if len(items)!=6 or len(set(x.split for x in items))!=1:
            raise ValueError('LEAKY_OR_INCOMPLETE_MATHEMATICAL_GROUP: '+group)
        signs=Counter(-1 if x.target<0 else 1 if x.target>0 else 0 for x in items)
        if signs!=Counter({-1:2,0:2,1:2}):
            raise ValueError('MISSING_COMPLETE_SIGN_TRIPLE: '+group)
        surfaces=defaultdict(list)
        for x in items:surfaces[x.surface].append(x.target)
        if len(surfaces)!=2 or any(len(v)!=3 for v in surfaces.values()):
            raise ValueError('INVALID_SURFACE_PAIR: '+group)
    # Numeric group contexts are unique even when their epsilons repeat.
    return {'cases':len(deck),'groups':len(groups),'by_split':dict(Counter(x.split for x in deck)),
            'by_family':dict(Counter(x.family for x in deck)),
            'classes':dict(Counter('negative' if x.target<0 else 'positive' if x.target>0 else 'zero' for x in deck)),
            'independent_exact_fraction_oracle':True,
            'partition_policy':'group-disjoint triples and both surfaces; distinct numeric ranges',
            'test_only_family':'hawk_indifference'}


def deck_hash(deck):
    return sha_text(canonical([x.public() for x in deck]))


def _safe_report_directory(run,expanded,requested):
    run=Path(run).resolve();expanded=Path(expanded).resolve()
    if requested:
        output=Path(requested).resolve()
    else:
        output=next((run.parent/f'{run.name}-sign-information-{i:04d}'
              for i in range(1,10000) if not (run.parent/f'{run.name}-sign-information-{i:04d}').exists()),None)
        if output is None:raise RuntimeError('DIAGNOSTIC_OUTPUT_NAMES_EXHAUSTED')
        output=output.resolve()
    for protected in (run,expanded):
        if output==protected or protected in output.parents or output in protected.parents:
            raise ValueError('DIAGNOSTIC_OUTPUT_OVERLAPS_SOURCE: '+str(protected))
    if output.exists():raise FileExistsError('DIAGNOSTIC_OUTPUT_ALREADY_EXISTS: '+str(output))
    return output


def _verified_lineage(run,expanded,model_id):
    """Verify exact deployed adapter/head format without loading Qwen."""
    import torch
    from tools.nanojev_gamer_v1_expanded_head import SCHEMA as HEAD_SCHEMA
    run=Path(run).resolve();expanded=Path(expanded).resolve()
    other=argparse.Namespace(run=run,expanded_run=expanded,adapter_dir=None,mode='expanded')
    spine.resolve_model_paths(other)
    adapter=other.adapter_dir
    adapter_sha=spine.adapter_identity(adapter)
    meta=spine.expanded_metadata(expanded,adapter_sha,model_id)
    cp=expanded/'best_model.pt'
    state=torch.load(cp,map_location='cpu',weights_only=True)
    if (state.get('schema')!=HEAD_SCHEMA or state.get('identity')!=meta
        or not isinstance(state.get('step'),int) or not isinstance(state.get('model'),dict)):
        raise ValueError('SIGN_PROBE_BEST_MODEL_FINGERPRINT_OR_STEP_MISMATCH')
    info={'model_id':model_id,'adapter_dir':str(adapter.resolve()),
          'adapter_sha256':adapter_sha,'checkpoint_path':str(cp.resolve()),
          'checkpoint_sha256':spine.file_sha(cp),'checkpoint_step':int(state['step']),
          'expanded_run':str(expanded),'source_run':str(run),
          'head_schema':HEAD_SCHEMA,'configuration':meta['configuration'],
          'source_dataset_sha256':meta.get('dataset_sha256')}
    del state
    return info


def _backbone_parts(model):
    base=model.get_base_model() if hasattr(model,'get_base_model') else model
    core=getattr(base,'model',None)
    if core is None or getattr(core,'norm',None) is None or \
       getattr(core,'embed_tokens',None) is None or not hasattr(core,'layers'):
        raise ValueError('SIGN_PROBE_QWEN_LAYER_HOOKS_UNAVAILABLE')
    return core


def _capture_profiles(torch,model,tok,deck,*,device,max_length,batch_size,curvature,
                      verify_exact_final=True):
    """Capture final 100 vectors plus selected intermediate layer profiles.

    Final norm hook uses same right padding, mask-based real length, 100 raster
    positions, and logits_to_keep=1 as expanded_head._capture. Earlier layers
    contribute only last-token/mean100 profiles, not an unbounded hidden-state dump.
    """
    import numpy as np
    from tools.nanojev_gamer_v1_expanded_head import positions_for_length,_capture
    core=_backbone_parts(model)
    n_layers=len(core.layers)
    if n_layers<2:raise ValueError('SIGN_PROBE_REQUIRES_MULTI_LAYER_BACKBONE')
    selected=sorted(set((0,n_layers//3,(2*n_layers)//3,n_layers-1)))
    if tok.padding_side!='right':raise ValueError('SIGN_PROBE_REQUIRES_RIGHT_PADDING')
    names=['embedding']+[f'layer_{i:02d}' for i in selected]+['final']
    points={'embedding':core.embed_tokens,
            **{f'layer_{i:02d}':core.layers[i] for i in selected},
            'final':core.norm}
    buffers={name:[] for name in names}
    full=[]
    model.eval()
    for start in range(0,len(deck),batch_size):
        if start==0 or (start//batch_size)%20==0:
            print(f'SIGN_INFORMATION model capture {start}/{len(deck)}',flush=True)
        batch=deck[start:start+batch_size]
        prompts=[x.prompt for x in batch]
        if any(len(tok.encode(p,add_special_tokens=True))>max_length for p in prompts):
            raise ValueError('SIGN_PROBE_PROMPT_TRUNCATION_REFUSED')
        encoded=tok(prompts,padding=True,truncation=True,max_length=max_length,
                    return_tensors='pt')
        encoded={key:value.to(device) for key,value in encoded.items()}
        lengths=encoded['attention_mask'].sum(-1).tolist()
        seen=defaultdict(list);handles=[]
        for name,module in points.items():
            def hook(_module,_args,value,n=name):
                h=value[0] if isinstance(value,(list,tuple)) else value
                if not isinstance(h,torch.Tensor) or h.ndim!=3:
                    raise ValueError('SIGN_PROBE_BAD_HOOK_TENSOR: '+n)
                seen[n].append(h.detach())
            handles.append(module.register_forward_hook(hook))
        try:
            with torch.inference_mode():
                model(**encoded,output_hidden_states=False,return_dict=True,
                      logits_to_keep=1)
        finally:
            for handle in handles:handle.remove()
        if set(seen)!=set(names) or any(len(seen[k])!=1 for k in names):
            raise ValueError('SIGN_PROBE_INCOMPLETE_QWEN_HOOKS')
        for i,length in enumerate(lengths):
            length=int(length)
            chosen=list(positions_for_length(length,100,curvature))
            for name in names:
                h=seen[name][0]
                if h.shape[0]!=len(batch) or h.shape[1]<length or h.shape[2]!=1024:
                    raise ValueError('SIGN_PROBE_UNEXPECTED_QWEN_SHAPE: '+name)
                x=h[i,chosen,:].float().cpu().numpy()
                if not np.isfinite(x).all():
                    raise ValueError('SIGN_PROBE_NONFINITE_FEATURE: '+name)
                if name=='final':full.append(x.reshape(-1).copy())
                buffers[name].append((x[-1].copy(),x.mean(axis=0)))
    features={'final_flat':np.asarray(full,dtype=np.float32)}
    for name,pairs in buffers.items():
        features[name+'_last']=np.asarray([p[0] for p in pairs],dtype=np.float32)
        features[name+'_mean100']=np.asarray([p[1] for p in pairs],dtype=np.float32)
    if features['final_flat'].shape!=(len(deck),102400):
        raise ValueError('SIGN_PROBE_FINAL_FEATURE_WIDTH_NOT_102400')
    max_delta=0.
    if verify_exact_final:
        n=min(4,len(deck))
        exact,_=_capture(torch,model,tok,[{'prompt_08':x.prompt} for x in deck[:n]],
             device=device,max_length=max_length,batch_size=min(batch_size,n),
             samples=100,curvature=curvature)
        max_delta=float(np.max(np.abs(exact-features['final_flat'][:n])))
        if max_delta>1e-4:
            raise ValueError('SIGN_PROBE_CAPTURE_NOT_EXACT_EXPANDED_POLICY: '+str(max_delta))
    return features,{'case_count':len(deck),'selected_layer_indices':selected,
                     'available_profiles':sorted(features),'final_capture_parity_max_delta':max_delta,
                     'final_width':102400,'token_raster_samples':100,
                     'padding':'right, real length only','feature_finiteness':'PASS'}


def _head_outputs(info,features,*,device,head_batch):
    import gc
    import numpy as np
    import torch
    from tools.nanojev_gamer_v1_expanded_head import make_expanded_head
    from tools.nanojev_qwen_c0_compressive_train import inverse_signed_log
    cfg=info['configuration']
    state=torch.load(info['checkpoint_path'],map_location='cpu',weights_only=True)
    net=make_expanded_head(torch,102400,tuple(cfg['hidden']))
    net.load_state_dict(state['model'],strict=True)
    del state
    dtype=torch.float16 if device.startswith('cuda') else torch.float32
    net=net.to(device=device,dtype=dtype).eval()
    outputs=[]
    with torch.inference_mode():
        for start in range(0,len(features),head_batch):
            x=torch.as_tensor(features[start:start+head_batch],device=device,dtype=dtype)
            raw,_=net(x)
            outputs.extend(float(v) for v in inverse_signed_log(raw.float().cpu().numpy()))
    del net
    gc.collect()
    if torch.cuda.is_available():torch.cuda.empty_cache()
    if len(outputs)!=len(features) or not np.isfinite(outputs).all():
        raise ValueError('SIGN_PROBE_MISSING_OR_NONFINITE_HEAD_PREDICTIONS')
    return np.asarray(outputs,dtype=np.float32)


def capture(args):
    import gc
    import numpy as np
    import torch
    from tools import nanojev_gamer_v1_handoff as gamer
    if not args.run or not args.expanded_run:
        raise ValueError('CAPTURE_REQUIRES_RUN_AND_EXPANDED_RUN')
    if args.capture_batch<1 or args.head_batch<1 or args.max_input_tokens<1:
        raise ValueError('INVALID_CAPTURE_BATCH_OR_LENGTH')
    lineage=_verified_lineage(args.run,args.expanded_run,args.model_id)
    cfg=lineage['configuration']
    if args.max_input_tokens>cfg['max_length']:
        raise ValueError('CAPTURE_MAX_TOKENS_EXCEEDS_TRAINED_LIMIT')
    deck=make_deck(args.train_groups,args.dev_groups,args.test_groups,not args.skip_ood)
    out=_safe_report_directory(args.run,args.expanded_run,args.output)
    spine.cache_policy(args.allow_model_download)
    captures={};details={}
    modes=('lora','base') if not args.skip_base_ablation else ('lora',)
    for variant in modes:
        print(f'SIGN_INFORMATION capture {variant}: {len(deck)} questions',flush=True)
        adapter=Path(lineage['adapter_dir']) if variant=='lora' else None
        _,tok,model=gamer.model_components(args.model_id,args.device,adapter_dir=adapter)
        feat,summary=_capture_profiles(torch,model,tok,deck,device=args.device,
               max_length=args.max_input_tokens,batch_size=args.capture_batch,
               curvature=cfg['curvature'],verify_exact_final=True)
        details[variant]=summary
        del model,tok
        gc.collect()
        if torch.cuda.is_available():torch.cuda.empty_cache()
        if variant=='lora':
            # Release Qwen before loading the large Wide head onto the GPU.
            predictions=_head_outputs(lineage,feat['final_flat'],
                                      device=args.device,head_batch=args.head_batch)
            captures['production_head_predictions']=predictions
        # 16-bit archive is sufficient for diagnostic readouts; the frozen head
        # already received the original FP32 capture above, never a rounded copy.
        captures.update({variant+'__'+key:value.astype(np.float16)
                         for key,value in feat.items()})
        del feat
        gc.collect()
    if spine.file_sha(Path(lineage['checkpoint_path']))!=lineage['checkpoint_sha256'] or \
       spine.adapter_identity(Path(lineage['adapter_dir']))!=lineage['adapter_sha256']:
        raise ValueError('SOURCE_WEIGHTS_CHANGED_DURING_CAPTURE')
    dataset=[x.public() for x in deck]
    array_hashes={name:hashlib.sha256(np.ascontiguousarray(values).tobytes()).hexdigest()
                  for name,values in captures.items()}
    metadata={'schema':SCHEMA,'lineage':lineage,'dataset_sha256':deck_hash(deck),
              'dataset':dataset,'deck':validate_deck(deck),'captures':details,
              'array_sha256':array_hashes,
              'ablation':list(modes),'production_head_frozen':True,
              'production_weights_modified':False,'training_state_modified':False}
    out.mkdir(parents=True,exist_ok=False)
    with (out/CAPTURE_NAME).open('wb') as stream:
        np.savez_compressed(stream,metadata=canonical(metadata),**captures)
    (out/'capture_manifest.json').write_text(json.dumps({k:v for k,v in metadata.items() if k!='dataset'},
                                        indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf-8')
    print(canonical({'status':'CAPTURE_COMPLETE','directory':str(out),
                     'dataset_sha256':metadata['dataset_sha256'],
                     'cases':len(deck),'variants':list(modes),
                     'final_capture_parity':{v:x['final_capture_parity_max_delta'] for v,x in details.items()}}),flush=True)
    return out


def _verify_archive(path,require_source=True):
    import numpy as np
    if not path.is_file():raise FileNotFoundError(path)
    with np.load(path,allow_pickle=False) as file:
        metadata=json.loads(str(file['metadata'].item()))
        arrays={k:file[k].copy() for k in file.files if k!='metadata'}
    if metadata.get('schema')!=SCHEMA:
        raise ValueError('SIGN_PROBE_BAD_FEATURE_SCHEMA')
    deck=metadata['dataset']
    hash_actual=sha_text(canonical(deck))
    if hash_actual!=metadata['dataset_sha256']:
        raise ValueError('SIGN_PROBE_ARCHIVE_DATASET_HASH_MISMATCH')
    if len(set(x['id'] for x in deck))!=len(deck):
        raise ValueError('SIGN_PROBE_DUPLICATE_ARCHIVED_CASE_ID')
    n=len(deck)
    expected={'production_head_predictions'}
    for variant,details in metadata['captures'].items():
        expected.update(variant+'__'+key for key in details['available_profiles'])
    if set(arrays)!=expected:
        raise ValueError('SIGN_PROBE_ARCHIVE_FEATURE_KEY_MISMATCH')
    if set(metadata.get('array_sha256',{}))!=expected:
        raise ValueError('SIGN_PROBE_ARCHIVE_MISSING_ARRAY_FINGERPRINTS')
    for name,x in arrays.items():
        if hashlib.sha256(np.ascontiguousarray(x).tobytes()).hexdigest()!=metadata['array_sha256'][name]:
            raise ValueError('SIGN_PROBE_ARCHIVE_ARRAY_FINGERPRINT_MISMATCH: '+name)
        if x.shape[0]!=n or not np.isfinite(x).all():
            raise ValueError('SIGN_PROBE_ARCHIVE_FEATURE_SHAPE_OR_FINITE_ERROR: '+name)
    if arrays['lora__final_flat'].shape!=(n,102400):
        raise ValueError('SIGN_PROBE_ARCHIVE_WRONG_HEAD_INPUT_WIDTH')
    if require_source:
        info=metadata['lineage']
        if not Path(info['checkpoint_path']).exists() or not Path(info['adapter_dir']).exists():
            raise FileNotFoundError('SIGN_PROBE_ORIGINAL_WEIGHTS_NOT_FOUND')
        if spine.file_sha(Path(info['checkpoint_path']))!=info['checkpoint_sha256'] or \
           spine.adapter_identity(Path(info['adapter_dir']))!=info['adapter_sha256']:
            raise ValueError('SIGN_PROBE_SOURCE_FINGERPRINT_CHANGED')
    return metadata,arrays


def _sign(y):
    import numpy as np
    y=np.asarray(y,dtype=np.float64)
    return np.sign(y).astype(np.int8)


def _metrics(y,estimate,cls,rows,ids):
    import numpy as np
    y=np.asarray(y,dtype=np.float64)
    est=np.asarray(estimate,dtype=np.float64)
    cls=np.asarray(cls,dtype=np.int8)
    actual=_sign(y)
    if len(y)==0:
        return {'cases':0,'status':'INCOMPLETE_EMPTY_CLASS'}
    recalls={str(s):float((cls[actual==s]==s).mean()) if (actual==s).any() else None
             for s in (-1,0,1)}
    present=[x for x in recalls.values() if x is not None]
    macro=float(sum(present)/len(present))
    # A matched triple must contain the negative, zero, positive sides of one
    # baseline on the same prompt surface. Do NOT count a cherry-picked pair.
    buckets=defaultdict(list)
    for local,n in enumerate(ids):
        row=rows[int(n)]
        buckets[(row['group'],row['surface'])].append(local)
    triples=[v for v in buckets.values() if len(v)==3 and set(actual[list(v)])=={-1,0,1}]
    triple_all=sum(all(cls[i]==actual[i] for i in group) for group in triples)
    pair_both=sum(cls[i_neg]==-1 and cls[i_pos]==1
                  for group in triples for i_neg in group for i_pos in group
                  if actual[i_neg]==-1 and actual[i_pos]==1)
    zero=y==0;small=(abs(y)>0)&(abs(y)<=0.05)
    return {'cases':len(y),'correct_class':int((cls==actual).sum()),
            'balanced_sign_accuracy':macro,'recall':recalls,
            'signed_mae':float(abs(y-est).mean()),
            'zero_recall':float((cls[zero]==0).mean()) if zero.any() else None,
            'small_nonzero_correct_sign':float((cls[small]==actual[small]).mean()) if small.any() else None,
            'zero_within_0_05':float((abs(est[zero])<=0.05).mean()) if zero.any() else None,
            'triple_all_correct':triple_all,'triples':len(triples),
            'triple_accuracy':float(triple_all/len(triples)) if triples else None,
            'reversal_both_signs_correct':int(pair_both),
            'strict_prediction_sign_accuracy':float((_sign(est[~zero])==actual[~zero]).mean()) if (~zero).any() else None}


def _gram_features(xtrain,xval,xtest):
    """Scale using training features ONLY; linear kernel in original feature space.

    Kernel solves allow a 102400-d readout without a new neural architecture or
    storing hundreds of thousands of optimizer parameters. All operations are
    deterministic given the captured feature arrays.
    """
    import numpy as np
    a=np.asarray(xtrain,dtype=np.float32)
    b=np.asarray(xval,dtype=np.float32)
    c=np.asarray(xtest,dtype=np.float32)
    mean=a.mean(axis=0,keepdims=True)
    std=np.sqrt(np.maximum(np.mean((a-mean)**2,axis=0,keepdims=True),1e-6))
    a=(a-mean)/std
    b=(b-mean)/std
    c=(c-mean)/std
    scale=max(a.shape[1],1)
    K=np.asarray(a@a.T/scale,dtype=np.float64)
    Kdev=np.asarray(b@a.T/scale,dtype=np.float64)
    Ktest=np.asarray(c@a.T/scale,dtype=np.float64)
    K=(K+K.T)*0.5
    if not all(np.isfinite(v).all() for v in (K,Kdev,Ktest)):
        raise ValueError('SIGN_PROBE_KERNEL_NONFINITE')
    return K,Kdev,Ktest


def _solve_probe(K,Kdev,Ktest,ytrain,ydev,*,alphas=(0.01,0.1,1.,10.)):
    import numpy as np
    labels=(-1,0,1)
    ytrain=np.asarray(ytrain,dtype=np.float64)
    ydev=np.asarray(ydev,dtype=np.float64)
    train_sign=_sign(ytrain);dev_sign=_sign(ydev)
    Y=np.column_stack([(train_sign==lab).astype(float) for lab in labels])
    encoded=np.sign(ytrain)*np.log1p(np.abs(ytrain))
    rhs=np.column_stack([Y,encoded])
    def decode(scores):
        classes=np.asarray(labels,dtype=np.int8)[np.argmax(scores[:,:3],axis=1)]
        values=np.sign(scores[:,3])*np.expm1(np.minimum(np.abs(scores[:,3]),20.))
        return classes,values
    best=None
    for alpha in alphas:
        try:
            fitted=np.linalg.solve(K+alpha*np.eye(len(K)),rhs)
        except Exception as exc:
            raise ValueError('SIGN_PROBE_RIDGE_SOLVER_FAILED') from exc
        dclass,dvalue=decode(Kdev@fitted)
        actual=dev_sign
        recalls=[float((dclass[actual==s]==s).mean()) for s in labels if (actual==s).any()]
        bacc=float(np.mean(recalls))
        mae=float(np.mean(np.abs(ydev-dvalue)))
        rank=(bacc,-mae,-alpha)
        if best is None or rank>best[0]:
            best=(rank,alpha,fitted,bacc,mae)
    _,alpha,weights,bacc,mae=best
    testclass,testvalues=decode(Ktest@weights)
    return {'alpha':alpha,'dev_balanced_sign_accuracy':bacc,
            'dev_signed_mae':mae},testclass,testvalues


def _test_partitions(rows):
    import numpy as np
    out={}
    for split in ('train','dev','test'):
        out[split]=np.asarray([i for i,r in enumerate(rows) if r['split']==split],dtype=np.int64)
        if len(out[split])==0:raise ValueError('SIGN_PROBE_EMPTY_PARTITION: '+split)
    groups=defaultdict(set)
    for r in rows:groups[r['group']].add(r['split'])
    if any(len(x)>1 for x in groups.values()):
        raise ValueError('SIGN_PROBE_LEAKED_GROUP')
    return out


def _evaluate_policy(rows,arrays,variant,feature,*,train_surface=None,seed=37):
    import numpy as np
    X=arrays[variant+'__'+feature]
    y=np.asarray([float(Fraction(r['target'])) for r in rows],dtype=np.float64)
    splits=_test_partitions(rows)
    def choose(split):
        indices=splits[split]
        if train_surface is not None and split=='train':
            indices=np.asarray([i for i in indices if rows[int(i)]['surface']==train_surface],dtype=np.int64)
        if len(indices)<6:raise ValueError('SIGN_PROBE_TOO_FEW_PARTITION_SAMPLES')
        return indices
    train,dev,test=(choose(name) for name in ('train','dev','test'))
    K,Kdev,Ktest=_gram_features(X[train],X[dev],X[test])
    selection,classes,values=_solve_probe(K,Kdev,Ktest,y[train],y[dev])
    primary=_metrics(y[test],values,classes,rows,test)
    # Null control: randomize training labels; never modify the held-out oracle.
    shuffled=[]
    for s in range(3):
        rng=np.random.default_rng(seed+s)
        yy=y[train].copy();rng.shuffle(yy)
        _,null_classes,null_values=_solve_probe(K,Kdev,Ktest,yy,y[dev])
        shuffled.append(_metrics(y[test],null_values,null_classes,rows,test)['balanced_sign_accuracy'])
    surfaces=sorted(set(rows[int(i)]['surface'] for i in test))
    by_surface={surface:_metrics(y[test_sub],values[position],classes[position],rows,test_sub)
        for surface in surfaces
        for position,test_sub in [([k for k,i in enumerate(test) if rows[int(i)]['surface']==surface],
                         np.asarray([i for i in test if rows[int(i)]['surface']==surface],dtype=np.int64))]}
    families=sorted(set(rows[int(i)]['family'] for i in test))
    by_family={family:_metrics(y[indices],values[position],classes[position],rows,indices)
        for family in families
        for position,indices in [([k for k,i in enumerate(test) if rows[int(i)]['family']==family],
                         np.asarray([i for i in test if rows[int(i)]['family']==family],dtype=np.int64))]}
    return {'variant':variant,'feature':feature,'train_surface':train_surface or 'both',
            'train_count':len(train),'dev_count':len(dev),'test_count':len(test),
            'selection':selection,'test':primary,'test_by_surface':by_surface,
            'test_by_family':by_family,'shuffled_null_balanced_sign_accuracy':shuffled,
            'shuffled_null_max':max(shuffled),
            'passes_95pct_sign_and_triple_gate':(primary['balanced_sign_accuracy']>=0.95 and
                     primary['triple_accuracy'] is not None and primary['triple_accuracy']>=0.95 and
                     primary['zero_within_0_05'] is not None and primary['zero_within_0_05']>=0.95 and
                     primary['small_nonzero_correct_sign'] is not None and
                     primary['small_nonzero_correct_sign']>=0.95),
            'readout_interpretation':'LINEAR_ACCESSIBLE_ONLY_NOT_PROOF_OF_ARITHMETIC'}


def analyze(args):
    import csv
    import numpy as np
    path=Path(args.input)
    archive=path/CAPTURE_NAME if path.is_dir() else path
    info,arrays=_verify_archive(archive,require_source=not args.offline)
    rows=info['dataset'];indices=_test_partitions(rows)
    y=np.asarray([float(Fraction(x['target'])) for x in rows],dtype=np.float64)
    head=np.asarray(arrays['production_head_predictions'],dtype=np.float64)
    test=indices['test']
    # The production head is a continuous regressor. Its near-zero tolerance is
    # reported separately from three-class exact-zero classification.
    head_cls=np.where(head>0.05,1,np.where(head < -0.05,-1,0)).astype(np.int8)
    production=_metrics(y[test],head[test],head_cls[test],rows,test)
    scores=[]
    for variant in info['ablation']:
        features=[name[len(variant+'__'):] for name in arrays if name.startswith(variant+'__')]
        requested=['final_last','final_mean100','final_flat',
                   'embedding_last','embedding_mean100']
        requested.extend(sorted(name for name in features if name.startswith('layer_') and
                                (name.endswith('_last') or name.endswith('_mean100'))))
        for policy in requested:
            if policy not in features:continue
            if policy=='final_flat' and args.skip_full_vector:continue
            result=_evaluate_policy(rows,arrays,variant,policy,seed=args.seed)
            scores.append(result)
            print(f'SIGN_INFORMATION readout {variant}/{policy}: '+
                  f'dev_balanced_sign={result["selection"]["dev_balanced_sign_accuracy"]:.4f}',flush=True)
            if policy in ('final_flat','final_last','final_mean100'):
                scores.append(_evaluate_policy(rows,arrays,variant,policy,
                                               train_surface='source_native',seed=args.seed))
    if not scores:raise ValueError('SIGN_PROBE_NO_READOUT_POLICIES')
    # Select on DEVELOPMENT only. Do not use held-out scores to choose a winning
    # model for a success claim; all test metrics are exposed for inspection.
    by_variant={}
    for variant in info['ablation']:
        eligible=[r for r in scores if r['variant']==variant and r['train_surface']=='both']
        top=max(eligible,key=lambda r:(r['selection']['dev_balanced_sign_accuracy'],
                                       -r['selection']['dev_signed_mae'],r['feature']))
        by_variant[variant]=top
    lora=by_variant['lora'];base=by_variant.get('base')
    head_failure=production['balanced_sign_accuracy']<0.95 or \
       (production['triple_accuracy'] is not None and production['triple_accuracy']<0.95)
    supports_head=(lora['passes_95pct_sign_and_triple_gate'] and
                   lora['test']['balanced_sign_accuracy']>
                         lora['shuffled_null_max']+0.15 and head_failure)
    base_advantage=(base is not None and
                    base['test']['balanced_sign_accuracy']-
                    lora['test']['balanced_sign_accuracy']>=0.15 and
                    base['test']['balanced_sign_accuracy']>=0.9)
    conclusion=('HEAD_READOUT_REPAIR_SUPPORTED_BUT_NOT_CERTIFIED' if supports_head else
                'BASE_ADVANTAGE_UPSTREAM_LO_RA_SUSPECTED_NOT_CERTIFIED' if base_advantage else
                'INCONCLUSIVE_REPRESENTATION_OR_READOUT')
    if args.skip_full_vector:
        conclusion+='__FULL_VECTOR_UNTESTED'
    if base is None:
        conclusion+='__BASE_ABLATION_MISSING'
    if args.offline:
        conclusion+='__SOURCE_FINGERPRINT_UNVERIFIED'
    status=('DIAGNOSTIC_COMPLETE_NO_CAUSAL_CERTIFICATION' if
            not args.skip_full_vector and base is not None and not args.offline else
            'INCOMPLETE_OPTIONAL_ABLATION_OR_FULL_VECTOR')
    report={'schema':SCHEMA,'status':status,
            'conclusion':conclusion,'model_lineage':info['lineage'],
            'dataset_sha256':info['dataset_sha256'],'dataset':info['deck'],
            'capture':info['captures'],'production_head_test':production,
            'development_selected_readouts':by_variant,
            'readout_count':len(scores),'source_files_unmodified':True,
            'no_production_training':True,
            'limits':('Feature accessibility may reflect lexical regularities, not arithmetic. '
                      'Linear-readout failure does not establish information absence. '
                      'A base-vs-adapter difference is suggestive, not a proof of LoRA causation. '
                      'Shuffled labels are a negative control, not an inferential p-value. '
                      'Zero classification is distinct from numerical tolerance.' )}
    out=archive.parent
    if (out/'analysis.json').exists() or (out/'readout_scores.csv').exists():
        raise FileExistsError('SIGN_PROBE_ANALYSIS_ALREADY_EXISTS_REFUSE_OVERWRITE')
    (out/'analysis.json').write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf-8')
    fields=['variant','feature','train_surface','train_count','dev_count','test_count',
            'dev_balanced_sign_accuracy','dev_signed_mae','test_balanced_sign_accuracy',
            'test_signed_mae','test_zero_recall','test_triple_accuracy',
            'test_small_nonzero_correct_sign','max_shuffled_null_accuracy']
    with (out/'readout_scores.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for r in scores:
            writer.writerow({'variant':r['variant'],'feature':r['feature'],
               'train_surface':r['train_surface'],'train_count':r['train_count'],
               'dev_count':r['dev_count'],'test_count':r['test_count'],
               'dev_balanced_sign_accuracy':r['selection']['dev_balanced_sign_accuracy'],
               'dev_signed_mae':r['selection']['dev_signed_mae'],
               'test_balanced_sign_accuracy':r['test']['balanced_sign_accuracy'],
               'test_signed_mae':r['test']['signed_mae'],
               'test_zero_recall':r['test']['zero_recall'],
               'test_triple_accuracy':r['test']['triple_accuracy'],
               'test_small_nonzero_correct_sign':r['test']['small_nonzero_correct_sign'],
               'max_shuffled_null_accuracy':r['shuffled_null_max']})
    print(canonical({'status':report['status'],'conclusion':conclusion,
             'output':str(out/'analysis.json'),'scores':str(out/'readout_scores.csv'),
             'production_head_test':production,
             'development_selected_readouts':{
                 k:{'feature':v['feature'],'selection':v['selection'],
                    'test':v['test'],'null_max':v['shuffled_null_max']}
                 for k,v in by_variant.items()}}),flush=True)
    return report


def parser():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--mode',choices=('oracle','capture','analyze'),default='oracle')
    ap.add_argument('--run',type=Path)
    ap.add_argument('--expanded-run',type=Path)
    ap.add_argument('--input',type=Path,help='Directory containing the captured features')
    ap.add_argument('--output',type=Path,help='New sibling report directory; existing path refused')
    ap.add_argument('--device',default='cuda')
    ap.add_argument('--model-id',default='Qwen/Qwen3-0.6B')
    ap.add_argument('--capture-batch',type=int,default=2)
    ap.add_argument('--head-batch',type=int,default=4)
    ap.add_argument('--max-input-tokens',type=int,default=1024)
    ap.add_argument('--train-groups',type=int,default=48)
    ap.add_argument('--dev-groups',type=int,default=24)
    ap.add_argument('--test-groups',type=int,default=24)
    ap.add_argument('--skip-ood',action='store_true',help='Skip unseen Hawk-Dove belief family')
    ap.add_argument('--skip-base-ablation',action='store_true',help='Faster first pass; base comparison unverified')
    ap.add_argument('--skip-full-vector',action='store_true',help='Faster analysis, but attribution incomplete')
    ap.add_argument('--offline',action='store_true',help='Skip source fingerprint verification in analysis ONLY')
    ap.add_argument('--allow-model-download',action='store_true')
    ap.add_argument('--seed',type=int,default=37)
    return ap


def main(argv=None):
    args=parser().parse_args(argv)
    if args.mode=='oracle':
        deck=make_deck(args.train_groups,args.dev_groups,args.test_groups,not args.skip_ood)
        print(canonical({'schema':SCHEMA,'mode':'oracle','deck':validate_deck(deck),
                         'dataset_sha256':deck_hash(deck),'model_status':'NOT_EVALUATED'}))
    elif args.mode=='capture':
        capture(args)
    else:
        if not args.input:raise ValueError('ANALYZE_MODE_REQUIRES_INPUT')
        analyze(args)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
