#!/usr/bin/env python3
"""Isolated, deliberately strong LoRA sign/zero intervention; frozen-C0 audit.

TRAIN changes only a new experimental adapter initialized from the *exact*
original fingerprinted GAMER adapter. It never updates the original adapter,
Qwen, production C0, original certified corpus, or oracle capture. Its exact-
Fraction completion objective deliberately targets negative, zero and positive
signed-offset examples, leaving dev/test and the hawk family unseen.

AUDIT feeds the unchanged oracle questions through snapshot LoRAs and the
*same* production C0, and uses the prior preservation probe policy frozen from
RAW DEV. Readout probes are diagnostic only, and full triples/zero/negative and
unseen-family performance are reported, never a self-certified promotion.

Example:
 python -m tools.nanojev_gamer_v1_lora_sign_stress train --source runtime/reports/gamer-v1/c0-matched-preservation-0001 --output runtime/reports/gamer-v1/lora-sign-stress-0001 --device cuda --steps 2000
 python -m tools.nanojev_gamer_v1_lora_sign_stress audit --source runtime/reports/gamer-v1/c0-matched-preservation-0001 --output runtime/reports/gamer-v1/lora-sign-stress-0001 --audit-output runtime/reports/gamer-v1/lora-sign-audit-0001 --device cuda --audit-steps 0,1000,2000
"""
from __future__ import annotations

import argparse
from fractions import Fraction
import gc
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import tempfile

import numpy as np

from tools import nanojev_gamer_v1_sign_information_probe as oracle
from tools import nanojev_gamer_v1_c0_information_preservation as preservation
from tools import nanojev_gamer_v1_c0_boundary as c0
from tools import nanojev_gamer_v1_encoder_bypass as bypass
from tools import nanojev_gamer_v1_sufficiency as suff
from tools import nanojev_gamer_v1_handoff as gamer

SCHEMA='nanojev.gamer.v1.isolated-lora-sign-stress.v1'
AUDIT_SCHEMA='nanojev.gamer.v1.isolated-lora-sign-stress-audit.v1'


def _json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _sha(path):
    return suff._digest(Path(path))


def _source(path):
    root=Path(path).resolve()
    report=_json(root/'final_report.json')
    if report.get('schema')!=preservation.SCHEMA or report.get('status')!='COMPLETE_READ_ONLY_BOUNDED_INFERENCE':
        raise ValueError('GAMER_LORA_STRESS_REQUIRES_COMPLETED_PRESERVATION')
    bypass_root=Path(report['source_encoder_bypass_report']).resolve().parent
    _,_,meta,arrays,capture,known=preservation._source(bypass_root)
    if (report['dataset_sha256']!=meta['dataset_sha256'] or
        report['fingerprints']!={**known,'encoder_bypass_report_sha256':_sha(bypass_root/'final_report.json')} or
        report.get('baseline_replay_parity',{}).get('max_abs_delta')!=0.0 or
        not report.get('no_production_training_or_weight_update')):
        raise ValueError('GAMER_LORA_STRESS_SOURCE_FINGERPRINT_MISMATCH')
    return root,report,meta,arrays,capture


def _dataset(meta,train_groups):
    # Only expand train contexts; dev/test prompts and their exact answers may
    # not change even by one character, and the entire hawk family stays TEST.
    base=meta['dataset']
    train_count=sum(x['split']=='train' for x in base)//6
    dev_count=sum(x['split']=='dev' for x in base)//6
    test_count=sum(x['split']=='test' and x['family']=='signed_offset' for x in base)//6
    if train_groups<train_count or train_groups>100:
        raise ValueError('GAMER_LORA_STRESS_TRAIN_GROUP_RANGE_24_TO_100')
    if train_groups+12>=151:
        raise ValueError('GAMER_LORA_STRESS_TRAIN_DEV_CONTEXT_COLLISION')
    deck=oracle.make_deck(train_groups,dev_count,test_count,include_ood=True)
    old={x['id']:x for x in base}
    new={x.id:x.public() for x in deck}
    if not all(new.get(k)==v for k,v in old.items()):
        raise ValueError('GAMER_LORA_STRESS_ORIGINAL_DECK_CHANGED')
    if sorted(k for k,v in new.items() if v['split']!='train')!=sorted(k for k,v in old.items() if v['split']!='train'):
        raise ValueError('GAMER_LORA_STRESS_HOLDOUT_SET_CHANGED')
    if any(x.family!='signed_offset' for x in deck if x.split=='train'):
        raise ValueError('GAMER_LORA_STRESS_UNSEEN_FAMILY_IN_TRAIN')
    return deck


def _settings(args,source,report,meta,deck):
    return {'source_preservation':str(source),'source_preservation_sha256':_sha(source/'final_report.json'),
            'source_checkpoint_sha256':meta['lineage']['checkpoint_sha256'],
            'source_adapter_sha256':meta['lineage']['adapter_sha256'],
            'baseline_oracle_sha256':meta['dataset_sha256'],
            'training_deck_sha256':oracle.deck_hash(deck),
            'train_groups':args.train_groups,'seed':args.seed,'lr':args.lr,
            'batch_size':args.batch_size,'accum':args.accum,
            'max_length':args.max_length,'checkpoint_every':args.checkpoint_every,
            'replay_fraction':args.replay_fraction,'near_zero_fraction':args.near_zero_fraction,
            'device_type':str(args.device).split(':')[0],
            'objective':'exact-Fraction signed-offset completion / protected zero-line / original certified train replay',
            'training_scope':'train-only signed_offset; hawk entire family unseen'}


def _destination(path,protected=(),*,must_exist=False):
    dest=Path(path).resolve()
    if len(str(dest/'final_report.json'))>=240 and os.name=='nt':
        raise ValueError('GAMER_LORA_STRESS_WINDOWS_OUTPUT_PATH_TOO_LONG')
    if len(dest.name)>120:
        raise ValueError('GAMER_LORA_STRESS_OUTPUT_COMPONENT_TOO_LONG')
    for p in protected:
        p=Path(p).resolve()
        if dest==p or p in dest.parents or dest in p.parents:
            raise ValueError('GAMER_LORA_STRESS_OUTPUT_OVERLAPS_PROTECTED_SOURCE')
    if dest.exists()!=must_exist:
        raise (FileNotFoundError if must_exist else FileExistsError)(str(dest))
    return dest


def _weights_hash(adapter):
    # The adapter directory hash is what the original GAMER audit uses.
    return gamer.file_tree_sha256(Path(adapter))


def _snapshot(model,dest,step):
    if dest.exists():raise FileExistsError('GAMER_LORA_STRESS_SNAPSHOT_ALREADY_EXISTS')
    temp=Path(tempfile.mkdtemp(prefix='writing-',dir=dest.parent))
    try:
        model.save_pretrained(str(temp),safe_serialization=True)
        sha=_weights_hash(temp)
        gamer.atomic_json(temp/'snapshot.json',{'step':step,'adapter_payload_sha256':sha})
        os.replace(temp,dest)
    finally:
        if temp.exists():shutil.rmtree(temp)
    return sha


def _verify_snapshot(folder,step):
    folder=Path(folder)
    manifest=_json(folder/'snapshot.json')
    if manifest.get('step')!=step:
        raise ValueError('GAMER_LORA_STRESS_WRONG_SNAPSHOT_STEP')
    # Reproduce the original GAMER file-tree hash exactly, excluding only our
    # own immutable sidecar. Never accept changed tensor bytes or config.
    digest=hashlib.sha256()
    for file in sorted(p for p in folder.rglob('*') if p.is_file() and p.name!='snapshot.json'):
        digest.update(file.relative_to(folder).as_posix().encode('utf-8')+b'\0')
        with file.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):
                digest.update(block)
    if manifest.get('adapter_payload_sha256')!=digest.hexdigest():
        raise ValueError('GAMER_LORA_STRESS_SNAPSHOT_PAYLOAD_CHANGED')
    return manifest['adapter_payload_sha256']


def _examples(deck):
    return [x for x in deck if x.split=='train']


def _draw(rng,groups,near_zero_fraction):
    """Balanced sign class, protected zero and tiny nonzero, both prompt surfaces."""
    sign=rng.choice((-1,0,1))
    small=rng.random()<near_zero_fraction
    candidates=groups[(sign,small)]
    if not candidates:
        raise ValueError('GAMER_LORA_STRESS_EMPTY_SIGN_MAGNITUDE_STRATUM')
    return rng.choice(candidates)


def _group_examples(cases):
    groups={}
    for x in cases:
        sign=int((x.target>0)-(x.target<0))
        small=abs(x.target)<=Fraction(1,20)
        groups.setdefault((sign,small),[]).append(x)
    if set(groups)!={(s,z) for s in (-1,0,1) for z in (True,False)}:
        # Zeros are all small by definition: use zero examples in both lanes.
        groups[0,False]=list(groups.get((0,True),[]))
    if any(not v for v in groups.values()) or len(groups)!=6:
        raise ValueError('GAMER_LORA_STRESS_INCOMPLETE_TRAIN_STRATA')
    return groups


def _replay_rows(meta):
    run=Path(meta['lineage']['source_run'])
    dataset=run/'certified_pairs.jsonl'
    if not dataset.exists():
        raise FileNotFoundError('GAMER_LORA_STRESS_ORIGINAL_REPLAY_MISSING')
    if gamer.sha_bytes(dataset.read_bytes())!=meta['lineage']['source_dataset_sha256']:
        raise ValueError('GAMER_LORA_STRESS_ORIGINAL_REPLAY_CHANGED')
    rows=[json.loads(line) for line in dataset.read_text(encoding='utf-8').splitlines() if line.strip()]
    selected=[x for x in rows if x.get('split')=='train']
    if not selected:raise ValueError('GAMER_LORA_STRESS_NO_ORIGINAL_TRAIN_REPLAY')
    return selected


def _plan(source,meta,deck,settings):
    original=[r for r in deck if r.split=='train']
    by_class={str(i):sum(int((x.target>0)-(x.target<0))==i for x in original) for i in (-1,0,1)}
    return {'schema':SCHEMA,'status':'PLAN_ONLY','source':str(source),
            'oracle_train_cases':len(original),'oracle_train_class_counts':by_class,
            'oracle_dev_test_cases':sum(x.split!='train' for x in deck),
            'unseen_hawk_train_count':0,'settings':settings,
            'original_adapter_unchanged':True,'c0_never_trained':True}


def train(args):
    source,report,meta,arrays,capture=_source(args.source)
    deck=_dataset(meta,args.train_groups)
    settings=_settings(args,source,report,meta,deck)
    if args.mode=='plan':
        print(oracle.canonical(_plan(source,meta,deck,settings)))
        return
    import torch
    from peft import PeftModel
    from peft.utils.save_and_load import get_peft_model_state_dict,set_peft_model_state_dict
    original_adapter=Path(meta['lineage']['adapter_dir'])
    head_path=Path(meta['lineage']['checkpoint_path'])
    protected=[source,original_adapter,head_path.parent,capture,Path(meta['lineage']['source_run'])]
    dest=_destination(args.output,protected,must_exist=bool(args.resume))
    baseline=dest/'snapshots'/'step-0000000'
    if args.resume:
        if _json(dest/'experiment.json')!=settings:
            raise ValueError('GAMER_LORA_STRESS_RESUME_CONFIG_CHANGED')
        original_snapshot=_verify_snapshot(baseline,0)
        state=torch.load(dest/'latest.pt',map_location='cpu',weights_only=True)
        if state.get('schema')!=SCHEMA or state.get('config')!=settings:
            raise ValueError('GAMER_LORA_STRESS_CHECKPOINT_LINEAGE_MISMATCH')
        start=int(state['step'])
    else:
        if not args.output:raise ValueError('GAMER_LORA_STRESS_EXPLICIT_OUTPUT_REQUIRED')
        dest.mkdir(parents=True,exist_ok=False)
        (dest/'snapshots').mkdir()
        shutil.copytree(original_adapter,baseline)
        original_snapshot=_weights_hash(baseline)
        gamer.atomic_json(baseline/'snapshot.json',{'step':0,'adapter_payload_sha256':original_snapshot})
        gamer.atomic_json(dest/'experiment.json',settings)
        start=0
    if args.steps<=start:
        raise ValueError('GAMER_LORA_STRESS_STEPS_MUST_EXCEED_CHECKPOINT')
    cases=_examples(deck)
    group=_group_examples(cases)
    replay=_replay_rows(meta) if args.replay_fraction>0 else []
    gamer.set_seeds(args.seed)
    rng=random.Random(args.seed+171)
    torch,tokenizer,base_model=gamer.model_components(meta['lineage']['model_id'],args.device)
    adapter=PeftModel.from_pretrained(base_model,str(baseline),is_trainable=True)
    trainable=[name for name,p in adapter.named_parameters() if p.requires_grad]
    if not trainable or any(not ('lora_A' in name or 'lora_B' in name) for name in trainable):
        raise ValueError('GAMER_LORA_STRESS_ONLY_LORA_TENSORS_MAY_TRAIN')
    adapter.config.use_cache=False
    optimizer=torch.optim.AdamW((p for p in adapter.parameters() if p.requires_grad),
                                lr=args.lr,weight_decay=0.01)
    numeric_items={x.id:gamer.tokenize_completion(tokenizer,x.prompt,
                   oracle.spine.fstr(x.target),args.max_length) for x in cases}
    replay_items=[gamer.tokenize_completion(tokenizer,gamer.original_prompt(x),
                  x['math_prompt'],args.max_length) for x in replay]
    if args.resume:
        set_peft_model_state_dict(adapter,state['weights'])
        optimizer.load_state_dict(state['optimizer'])
        for item in optimizer.state.values():
            for key,val in item.items():
                if isinstance(val,torch.Tensor):item[key]=val.to(args.device)
        gamer.restore_rng(torch,state['rng'],python_rng=rng)
        if start%args.checkpoint_every==0 and not (dest/'snapshots'/f'step-{start:07d}').exists():
            _snapshot(adapter,dest/'snapshots'/f'step-{start:07d}',start)
    print(f'GAMER LoRA SIGN STRESS: train-only {len(cases)} cases, start={start} target={args.steps}; frozen C0',flush=True)
    print('  old adapter and original certified games untouched; OOD hawk test excluded from optimizer',flush=True)
    for step in range(start+1,args.steps+1):
        adapter.train()
        optimizer.zero_grad(set_to_none=True)
        losses=[]
        for _ in range(args.accum):
            batch=[]
            for _ in range(args.batch_size):
                if replay_items and rng.random()<args.replay_fraction:
                    item=rng.choice(replay_items)
                else:
                    item=numeric_items[_draw(rng,group,args.near_zero_fraction).id]
                batch.append(item)
            pack=gamer.collate(torch,tokenizer,batch,args.device)
            loss=adapter(**pack).loss/args.accum
            if not torch.isfinite(loss):raise ValueError('GAMER_LORA_STRESS_NONFINITE_LOSS')
            loss.backward()
            losses.append(float(loss.detach().cpu())*args.accum)
        torch.nn.utils.clip_grad_norm_(adapter.parameters(),1.0)
        optimizer.step()
        if step%25==0 or step==1 or step==args.steps:
            print(f'GAMER LoRA sign step {step}/{args.steps} loss={np.mean(losses):.6f}',flush=True)
        due=step%args.checkpoint_every==0 or step==args.steps
        if due:
            payload={'schema':SCHEMA,'step':step,'config':settings,
                     'weights':{k:v.detach().to('cpu').clone() for k,v in get_peft_model_state_dict(adapter).items()},
                     'optimizer':optimizer.state_dict(),
                     'rng':gamer.training_rng(torch,python_rng=rng)}
            gamer.save_training_state(torch,dest/'latest.pt',payload)
            snapshot=dest/'snapshots'/f'step-{step:07d}'
            if not snapshot.exists():_snapshot(adapter,snapshot,step)
            print(f'GAMER LoRA sign snapshot {step}: {snapshot}',flush=True)
    if _weights_hash(original_adapter)!=original_snapshot or _sha(head_path)!=settings['source_checkpoint_sha256']:
        raise ValueError('GAMER_LORA_STRESS_ORIGINAL_WEIGHTS_MUTATED')
    gamer.atomic_json(dest/'train_report.json',{'schema':SCHEMA,'status':'TRAINED_EXPERIMENTAL_NOT_PROMOTED',
         'steps':args.steps,'original_adapter_sha256':original_snapshot,
         'original_c0_sha256':settings['source_checkpoint_sha256'],
         'test_cases_accessed_during_optimizer':0,
         'source_preservation_sha256':settings['source_preservation_sha256'],
         'promotion':'BLOCKED_NO_C0_RETUNE'})
    print('GAMER LoRA SIGN STRESS COMPLETE: '+str(dest/'train_report.json'),flush=True)


def _head_metrics(rows,pred):
    from fractions import Fraction
    values=np.asarray(pred,dtype=np.float64)
    cls=np.where(abs(values)<=0.05,0,np.sign(values)).astype(np.int8)
    split=oracle._test_partitions(rows);test=split['test']
    y=np.asarray([float(Fraction(r['target'])) for r in rows])
    families={}
    for f in sorted({rows[int(i)]['family'] for i in test}):
        loc=np.asarray([i for i in test if rows[int(i)]['family']==f],dtype=int)
        families[f]=oracle._metrics(y[loc],values[loc],cls[loc],rows,loc)
    return {'test':oracle._metrics(y[test],values[test],cls[test],rows,test),
            'test_by_family':families,'zero_threshold':0.05}


def _probe(rows,features,net,selection,*,device,batch_size=8):
    x=np.asarray(features['final_flat'],dtype=np.float16).astype(np.float32)
    pred,stages=bypass._features_at_every_stage(net,x,device=device,batch_size=batch_size)
    pools={'lora_layer09_mean100':np.asarray(features['layer_09_mean100'],dtype=np.float32),
           'lora_final_mean100':np.asarray(features['final_mean100'],dtype=np.float32),
           'c0_raw_mean100':x.reshape(len(x),100,1024).mean(axis=1),
           'c0_raw_flat':x,'c0_norm':stages['c0_norm'],
           'enc_linear_1':stages['enc_linear_1'],
           'enc_gelu_3':stages['enc_gelu_3'],'mid_gelu':stages['mid_gelu']}
    split=oracle._test_partitions(rows)
    projections={k:preservation._train_pca(v,split['train'],max_rank=64) for k,v in pools.items()}
    return {'production_c0':_head_metrics(rows,pred),
            'matched_readouts':preservation._matched(rows,projections,selection),
            'stage_input_magnitude':{k:float(np.sqrt(np.mean(v.astype(np.float64)**2))) for k,v in pools.items()}}


def _compare(baseline,candidate):
    stages=('lora_layer09_mean100','lora_final_mean100','c0_raw_mean100',
            'c0_raw_flat','c0_norm','enc_linear_1','enc_gelu_3','mid_gelu')
    pairs={}
    for s in stages:
        a=baseline['matched_readouts'][s]['test']
        b=candidate['matched_readouts'][s]['test']
        pairs[s]={'sign_before':a['balanced_sign_accuracy'],'sign_after':b['balanced_sign_accuracy'],
                  'sign_delta':b['balanced_sign_accuracy']-a['balanced_sign_accuracy'],
                  'triples_before':a['triple_accuracy'],'triples_after':b['triple_accuracy'],
                  'negative_recall_before':a['recall']['-1'],'negative_recall_after':b['recall']['-1'],
                  'zero_recall_before':a['recall']['0'],'zero_recall_after':b['recall']['0'],
                  'unseen_hawk_before':baseline['matched_readouts'][s]['test_by_family']['hawk_indifference']['balanced_sign_accuracy'],
                  'unseen_hawk_after':candidate['matched_readouts'][s]['test_by_family']['hawk_indifference']['balanced_sign_accuracy']}
    a=baseline['production_c0']['test'];b=candidate['production_c0']['test']
    return {'stages':pairs,'c0_output':{'sign_delta':b['balanced_sign_accuracy']-a['balanced_sign_accuracy'],
              'triple_delta':b['triple_accuracy']-a['triple_accuracy'],
              'negative_recall_before':a['recall']['-1'],'negative_recall_after':b['recall']['-1'],
              'zero_recall_before':a['recall']['0'],'zero_recall_after':b['recall']['0']},
            'no_model_promotion':True}


def _steps(arg,dest):
    names={int(p.name.split('-')[-1]):p for p in (dest/'snapshots').glob('step-*')
           if p.is_dir() and p.name.split('-')[-1].isdigit()}
    if arg=='all':return sorted(names)
    selected=sorted({int(k.strip()) for k in arg.split(',') if k.strip()})
    if not selected or 0 not in selected or any(s not in names for s in selected):
        raise ValueError('GAMER_LORA_STRESS_AUDIT_STEPS_MUST_INCLUDE_ZERO_AND_EXISTING_SNAPSHOTS')
    return selected


def audit(args):
    import torch
    source,report,meta,arrays,capture=_source(args.source)
    dest=_destination(args.output,must_exist=True)
    cfg=_json(dest/'experiment.json')
    if cfg['source_preservation']!=str(source) or cfg['source_preservation_sha256']!=_sha(source/'final_report.json') or \
       cfg['source_checkpoint_sha256']!=meta['lineage']['checkpoint_sha256'] or \
       cfg['source_adapter_sha256']!=meta['lineage']['adapter_sha256']:
        raise ValueError('GAMER_LORA_STRESS_AUDIT_LINEAGE_CHANGED')
    steps=_steps(args.audit_steps,dest)
    output=_destination(args.audit_output,[source,dest,capture,Path(meta['lineage']['checkpoint_path']).parent])
    selection=report['matched_decoder_dev_selection']
    frozen_selection={k:selection[k] for k in ('feature_count','decoder','alpha')}
    rows=meta['dataset']
    original=np.asarray(arrays['lora__final_flat'],dtype=np.float32)
    net=c0._load_head(meta,args.device)
    baseline_features={'final_flat':original,
        'final_mean100':arrays['lora__final_mean100'],
        'layer_09_mean100':arrays['lora__layer_09_mean100']}
    original_pred,_=bypass._features_at_every_stage(net,original,device=args.device,batch_size=args.head_batch)
    if c0._verify_baseline(original_pred,arrays['production_head_predictions'])['max_abs_delta']!=0.0:
        raise ValueError('GAMER_LORA_STRESS_C0_BASELINE_REPLAY_CHANGED')
    baseline=_probe(rows,baseline_features,net,frozen_selection,device=args.device,batch_size=args.head_batch)
    baseline_head_sha=_sha(meta['lineage']['checkpoint_path'])
    baseline_adapter_sha=_weights_hash(meta['lineage']['adapter_dir'])
    del net
    gc.collect()
    if torch.cuda.is_available():torch.cuda.empty_cache()
    result={'schema':AUDIT_SCHEMA,'status':'AUDIT_IN_PROGRESS','source':str(source),
        'training_run':str(dest),'training_deck_sha256':cfg['training_deck_sha256'],
        'oracle_dataset_sha256':meta['dataset_sha256'],'frozen_c0_sha256':baseline_head_sha,
        'original_adapter_sha256':baseline_adapter_sha,'steps':{},'baseline':baseline,
        'matched_decoder_policy':frozen_selection,'audit_steps':steps,
        'no_c0_weight_update':True,'promotion':'BLOCKED_DIAGNOSTIC_ONLY'}
    output.mkdir(parents=True,exist_ok=False)
    # Save incrementally: no expensive recapture needs to repeat if a later
    # snapshot encounters a problem; final report is written only on success.
    gamer.atomic_json(output/'progress.json',{'status':'BASELINE_VERIFIED','steps':[]})
    for step in steps:
        snapshot=dest/'snapshots'/f'step-{step:07d}'
        sha=_verify_snapshot(snapshot,step)
        print(f'GAMER LoRA SIGN AUDIT: snapshot={step} sha={sha[:12]}',flush=True)
        _,tok,model=gamer.model_components(meta['lineage']['model_id'],args.device,adapter_dir=snapshot)
        deck=oracle.make_deck(
            sum(r['split']=='train' for r in rows)//6,
            sum(r['split']=='dev' for r in rows)//6,
            sum(r['split']=='test' and r['family']=='signed_offset' for r in rows)//6,
            include_ood=True)
        if [x.public() for x in deck]!=rows:raise ValueError('GAMER_LORA_STRESS_AUDIT_ORACLE_DECK_CHANGED')
        features,_=oracle._capture_profiles(torch,model,tok,deck,
            device=args.device,max_length=meta['lineage']['configuration']['max_length'],
            batch_size=args.capture_batch,curvature=meta['lineage']['configuration']['curvature'],
            verify_exact_final=True)
        if step==0:
            # An unchanged snapshot must regenerate the exact archived input.
            # Otherwise any apparent LoRA effect could be a capture mismatch.
            original_flat=np.asarray(arrays['lora__final_flat'],dtype=np.float16).astype(np.float32)
            recaptured=np.asarray(features['final_flat'],dtype=np.float16).astype(np.float32)
            if original_flat.shape!=recaptured.shape or float(np.max(np.abs(original_flat-recaptured)))>0.01:
                raise ValueError('GAMER_LORA_STRESS_ZERO_STEP_CAPTURE_PARITY_FAILED')
        del model,tok
        gc.collect()
        if torch.cuda.is_available():torch.cuda.empty_cache()
        net=c0._load_head(meta,args.device)
        measures=_probe(rows,features,net,frozen_selection,device=args.device,batch_size=args.head_batch)
        del net,features
        gc.collect()
        if torch.cuda.is_available():torch.cuda.empty_cache()
        result['steps'][str(step)]={'snapshot_sha256':sha,'metrics':measures,'delta_from_baseline':_compare(baseline,measures)}
        gamer.atomic_json(output/f'step-{step:07d}.json',result['steps'][str(step)])
        gamer.atomic_json(output/'progress.json',{'status':'IN_PROGRESS','steps':sorted(result['steps'])})
        m=measures['production_c0']['test']
        print(f'  C0 sign={m["balanced_sign_accuracy"]:.4f} triples={m["triple_accuracy"]:.4f} negative={m["recall"]["-1"]:.4f} zero={m["recall"]["0"]:.4f}',flush=True)
    if _sha(meta['lineage']['checkpoint_path'])!=baseline_head_sha or _weights_hash(meta['lineage']['adapter_dir'])!=baseline_adapter_sha:
        raise ValueError('GAMER_LORA_STRESS_ORIGINAL_WEIGHTS_MUTATED_DURING_AUDIT')
    result['status']='AUDIT_COMPLETE_NO_PRODUCTION_UPDATES'
    gamer.atomic_json(output/'final_report.json',result)
    print('GAMER LoRA SIGN AUDIT COMPLETE: '+str(output/'final_report.json'),flush=True)
    return result


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=('plan','train','audit'))
    p.add_argument('--source',required=True,help='Completed matched preservation diagnostic directory')
    p.add_argument('--output',help='Short, isolated experimental LoRA training directory')
    p.add_argument('--audit-output',help='Separate short directory for immutable audit reports')
    p.add_argument('--audit-steps',default='all',help='Snapshot steps, including 0, or all')
    p.add_argument('--device',default='cuda')
    p.add_argument('--steps',type=int,default=2000)
    p.add_argument('--checkpoint-every',type=int,default=500)
    p.add_argument('--train-groups',type=int,default=96)
    p.add_argument('--seed',type=int,default=47)
    p.add_argument('--lr',type=float,default=3e-5)
    p.add_argument('--batch-size',type=int,default=1)
    p.add_argument('--accum',type=int,default=8)
    p.add_argument('--max-length',type=int,default=1024)
    p.add_argument('--near-zero-fraction',type=float,default=0.75)
    p.add_argument('--replay-fraction',type=float,default=0.15)
    p.add_argument('--resume',action='store_true')
    p.add_argument('--capture-batch',type=int,default=2)
    p.add_argument('--head-batch',type=int,default=8)
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    if (args.steps<1 or args.checkpoint_every<1 or not 24<=args.train_groups<=100 or
        args.lr<=0 or args.lr>=0.01 or args.batch_size<1 or args.accum<1 or
        args.capture_batch<1 or args.head_batch<1 or args.max_length<1 or
        not 0<=args.replay_fraction<1 or not 0<=args.near_zero_fraction<=1):
        raise ValueError('GAMER_LORA_STRESS_INVALID_FLAGS')
    if args.mode!='audit' and not args.output and args.mode=='train':
        raise ValueError('GAMER_LORA_STRESS_REQUIRES_OUTPUT')
    if args.mode=='audit':
        if not args.output or not args.audit_output:
            raise ValueError('GAMER_LORA_STRESS_AUDIT_REQUIRES_OUTPUT_AND_AUDIT_OUTPUT')
        if args.resume:raise ValueError('GAMER_LORA_STRESS_AUDIT_CANNOT_RESUME_TRAINING')
        return audit(args)
    return train(args)


if __name__=='__main__':main()
