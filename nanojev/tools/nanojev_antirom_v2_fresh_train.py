#!/usr/bin/env python3
"""Train one AntiROM v2 level with a unique, sealed question every example.

Frozen Qwen/Pythia/TinyStories, frozen mature CLEF core, six residual taps,
AdamW, plain three-class CE. No generation, DEV accuracy or data reuse in trainer.
"""
from __future__ import annotations
import argparse
from collections import deque
import json
import math
from pathlib import Path
import random
import sys
import time
import traceback

from nanojev_antirom_compression import LEVELS, renderer_fingerprint, representation
from nanojev_antirom_v2_stream_bank import DEFAULT_OUTPUT as BANK_OUTPUT, load, build_sample
from nanojev_arithmetic_runtime import DEFAULT_BANK, deps, load_model, frozen_evidence, evidence_on_cuda, TRAINING_POLICY, capture_residual_before, inspect_residual_step
from nanojev_arithmetic_continuous_train import Logger, checkpoint, atomic_json
from nanojev_rom_arithmetic_language import verify_prompt_visibility

SCHEMA='nanojev.antirom-fresh-stream-training.v2'
DEFAULT_OUTPUT=Path(r'C:\Users\subsi\NanoJev\runs\antirom_fresh_arithmetic_sweep_v2')


def parse(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bank-dir',type=Path,default=BANK_OUTPUT)
    p.add_argument('--legacy-bank-dir',type=Path,default=DEFAULT_BANK)
    p.add_argument('--output-root',type=Path,default=DEFAULT_OUTPUT)
    p.add_argument('--level',choices=LEVELS,required=True)
    p.add_argument('--max-steps',type=int,default=3000)
    p.add_argument('--grad-accumulation',type=int,default=4)
    p.add_argument('--report-steps',type=int,default=100)
    p.add_argument('--checkpoint-steps',type=int,default=1000)
    p.add_argument('--rolling-questions',type=int,default=256)
    p.add_argument('--head-lr',type=float,default=1e-4)
    p.add_argument('--weight-decay',type=float,default=.01)
    p.add_argument('--grad-clip',type=float,default=1.0)
    p.add_argument('--seed',type=int,default=20261012)
    p.add_argument('--max-prompt-tokens',type=int,default=512)
    p.add_argument('--max-answer-tokens',type=int,default=64)
    p.add_argument('--prompt-evidence-tokens',type=int,default=6)
    p.add_argument('--answer-evidence-tokens',type=int,default=4)
    p.add_argument('--path-batch',type=int,default=4)
    p.add_argument('--tinystories-path-batch',type=int,default=4)
    p.add_argument('--local-files-only',action='store_true',default=True)
    p.add_argument('--allow-model-download',action='store_false',dest='local_files_only')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--dry-run',action='store_true')
    a=p.parse_args(argv)
    if (a.max_steps<1 or a.grad_accumulation<1 or a.report_steps<1
        or a.checkpoint_steps<1 or a.rolling_questions<1
        or a.head_lr<=0 or not math.isfinite(a.head_lr)
        or a.grad_clip<=0 or not math.isfinite(a.grad_clip)
        or a.weight_decay<0 or not math.isfinite(a.weight_decay)):
        p.error('ANTIROM_V2_INVALID_TRAINING_CONTROLS')
    return a


def configuration(args,bank):
    return {'schema':SCHEMA,'level':args.level,'representation':representation(args.level),
            'renderer_sha256':renderer_fingerprint(args.level),'bank_sha256':bank['bank_sha256'],
            'legacy_bank_sha256':bank['legacy_bank_sha256'],'training_policy':TRAINING_POLICY,
            'objective':'plain-three-class-cross-entropy',
            'seed':args.seed,'max_steps':args.max_steps,'grad_accumulation':args.grad_accumulation,
            'report_steps':args.report_steps,'checkpoint_steps':args.checkpoint_steps,
            'rolling_questions':args.rolling_questions,'head_lr':args.head_lr,
            'weight_decay':args.weight_decay,'grad_clip':args.grad_clip,
            'evidence':{k:int(getattr(args,k)) for k in (
                'max_prompt_tokens','max_answer_tokens','prompt_evidence_tokens',
                'answer_evidence_tokens','path_batch','tinystories_path_batch')}}


def resume_state(output,config,identity):
    state=json.loads((output/'state.json').read_text(encoding='utf-8'))
    experiment=json.loads((output/'experiment.json').read_text(encoding='utf-8'))
    if (experiment.get('schema')!=SCHEMA or experiment.get('config')!=config
        or state.get('config')!=config or state.get('bankManifestSha256')!=identity
        or experiment.get('bankManifestSha256')!=identity):
        raise RuntimeError('ANTIROM_V2_RESUME_CONTRACT_MISMATCH')
    step=int(state['cursor']['global_step']); seen=int(state['cursor']['examples_seen'])
    if step<1 or step>config['max_steps'] or seen!=step*config['grad_accumulation']:
        raise RuntimeError('ANTIROM_V2_RESUME_STREAM_OFFSET_MISMATCH')
    ckpt=Path(state['checkpoint']).resolve(strict=True)
    if ckpt.parent!=((output/'checkpoints').resolve()):
        raise RuntimeError('ANTIROM_V2_CHECKPOINT_OUTSIDE_OUTPUT')
    meta=json.loads((ckpt/'meta.json').read_text(encoding='utf-8'))
    if (meta['cursor']!=state['cursor'] or meta['config']!=config
        or meta['bankManifestSha256']!=identity or meta['rolling']!=state['rolling']):
        raise RuntimeError('ANTIROM_V2_CHECKPOINT_METADATA_DRIFT')
    return state,ckpt


def train(args):
    bank=load(bank_dir=args.bank_dir,legacy_bank_dir=args.legacy_bank_dir,split='train')
    rows=bank['rows']; total=args.max_steps*args.grad_accumulation
    if len(rows)!=total:
        raise RuntimeError(f'ANTIROM_V2_NO_REPEAT_BUDGET_MISMATCH: sealed={len(rows)} planned={total}')
    config=configuration(args,bank)
    output=(Path(args.output_root).expanduser().resolve()/args.level)
    if args.dry_run:
        print(json.dumps({'event':'antirom_v2_train_plan','level':args.level,'config':config,
          'train_count':len(rows),'distinct_operand_pairs':len(rows),
          'sample_prompt':build_sample(rows[0],args.level)['candidates'][0]['paths'][0]['prompt'],
          'output':str(output),'no_training':True},sort_keys=True),flush=True)
        return
    if args.resume:
        state,previous=resume_state(output,config,bank['bank_sha256'])
        cursor=dict(state['cursor'])
        rolling=deque(state['rolling'],maxlen=args.rolling_questions)
    else:
        if output.exists() and any(output.iterdir()):
            raise RuntimeError('ANTIROM_V2_OUTPUT_NOT_EMPTY: '+str(output))
        output.mkdir(parents=True,exist_ok=True)
        cursor={'pass_index':0,'shard_position':0,'offset':0,'global_step':0,'examples_seen':0}
        rolling=deque(maxlen=args.rolling_questions)
        previous=None
        atomic_json(output/'experiment.json',{'schema':SCHEMA,'mode':'fresh-unique-training-no-reexposure',
          'bankManifestSha256':bank['bank_sha256'],'config':config,
          'cutover':'pinned-cycle-136-reuse-002','previous_antirom_sweep_unchanged':True})
    (output/'checkpoints').mkdir(exist_ok=True)
    logger=Logger(output)
    logger.emit('antirom_v2_train_start',level=args.level,step=cursor['global_step'],
      used=cursor['examples_seen'],total=total,no_reexposure=True,
      bank_sha256=bank['bank_sha256'])
    import torch
    torch.manual_seed(args.seed);torch.cuda.manual_seed_all(args.seed);random.seed(args.seed)
    champion,_,objective=deps()
    head,bundles,optimizer,_=load_model(torch=torch,args=args,source=bank['source'],
                                       logger=logger,checkpoint=previous)
    params=champion.joint_parameters(head,bundles[champion.TRAINABLE_LABEL].lm)
    # Fail before the first optimizer update if ANY streamed prompt would truncate.
    # Only one candidate prompt is needed for this check; answers remain unchanged.
    samples=[build_sample(r,args.level) for r in rows]
    vis=verify_prompt_visibility(samples=samples,bundles=bundles,args=args)
    logger.emit('antirom_v2_prompt_visibility',level=args.level,backbones=vis,presentations=len(rows))
    head.train();bundles[champion.TRAINABLE_LABEL].lm.eval()
    window_start=time.perf_counter()
    for step in range(cursor['global_step']+1,args.max_steps+1):
        offset=(step-1)*args.grad_accumulation
        batch=samples[offset:offset+args.grad_accumulation]
        if len(batch)!=args.grad_accumulation:
            raise RuntimeError('ANTIROM_V2_STREAM_EXHAUSTED')
        optimizer.zero_grad(set_to_none=True)
        losses=[]
        for sample in batch:
            question=champion.base.deserialize_question(sample,objective)
            evidence=frozen_evidence(torch=torch,champion=champion,bundles=bundles,
                                     question=question,args=args)
            logits=head(evidence_on_cuda(champion,evidence))
            gold=torch.tensor([question.gold_index],dtype=torch.long,device=logits.device)
            loss=torch.nn.functional.cross_entropy(logits.float().unsqueeze(0),gold)
            if not bool(torch.isfinite(loss).item()):
                raise RuntimeError('ANTIROM_V2_NONFINITE_TRAINING_CE')
            (loss/args.grad_accumulation).backward()
            losses.append(float(loss.detach().item()))
            del evidence,logits,loss,gold,question
        grad_norm=float(torch.nn.utils.clip_grad_norm_(params,args.grad_clip).item())
        if not math.isfinite(grad_norm):
            raise RuntimeError('ANTIROM_V2_NONFINITE_GRADIENT')
        before=capture_residual_before(head) if step==1 or step%args.checkpoint_steps==0 else None
        optimizer.step()
        if before is not None:
            inspect_residual_step(torch=torch,head=head,before=before,logger=logger,step=step)
        rolling.extend(losses)
        cursor['global_step']=step
        cursor['examples_seen']=step*args.grad_accumulation
        if step==1 or step%args.report_steps==0 or step==args.max_steps:
            logger.emit('antirom_v2_online_training_ce',level=args.level,step=step,
                seen=cursor['examples_seen'],batch_ce=round(sum(losses)/len(losses),8),
                rolling_ce=round(sum(rolling)/len(rolling),8),window_examples=len(rolling),
                grad_norm=round(grad_norm,6),
                seconds_since_report=round(time.perf_counter()-window_start,2),
                accuracy_computed=False)
            window_start=time.perf_counter()
        if step%args.checkpoint_steps==0 or step==args.max_steps:
            checkpoint(torch=torch,output=output,head=head,optimizer=optimizer,
              cursor=cursor,rolling=rolling,bank_sha=bank['bank_sha256'],config=config,logger=logger)
            logger.emit('antirom_v2_training_checkpoint',level=args.level,step=step,
                seen=cursor['examples_seen'],new_questions_since_previous=args.checkpoint_steps*args.grad_accumulation)
    logger.emit('antirom_v2_training_complete',level=args.level,
        steps=cursor['global_step'],unique_examples=cursor['examples_seen'],
        mean_recent_ce=sum(rolling)/len(rolling),no_reexposure=True)


if __name__=='__main__':
    try:train(parse())
    except Exception as exc:
        print(json.dumps({'event':'antirom_v2_train_error','error':str(exc),
             'traceback':traceback.format_exc()},sort_keys=True),file=sys.stderr)
        raise SystemExit(1)
