#!/usr/bin/env python3
"""Continuous loss-only SAME/DIFFERENT CLEF trainer on original structured ROM.

No question generation, PREDEV, accuracy computation, auxiliary losses, or
champion selection. All three language models stay frozen. Stop at the first
completed optimizer step whose rolling plain cross-entropy meets --target-loss.
"""
from __future__ import annotations
import argparse
from collections import deque
import json
import math
import os
from pathlib import Path
import random
import shutil
import time
import traceback
import sys

from nanojev_binary_consensus_runtime import (
    deps, load_model, quiet_logger,
    frozen_evidence, evidence_on_cuda, pass_shard_order, shard_examples,
    validate_training_controls, TRAINING_POLICY, capture_residual_before, inspect_residual_step,
)
from nanojev_binary_raw_rom_bank import RAW_VARIANTS as VARIANTS, load_raw_bank as load_bank

SCHEMA = 'nanojev.binary-consensus-continuous.raw-rom-six-residual-v1'

DEFAULT_BANK = Path(r'C:\Users\subsi\NanoJev\runs\binary_consensus_raw_rom_bank_v1')
DEFAULT_OUTPUT = Path(r'C:\Users\subsi\NanoJev\runs\three_backbone_clef_binary_raw_rom_residual_v1')
DEFAULT_ROM333_OUTPUT = Path(r'C:\Users\subsi\NanoJev\runs\three_backbone_clef_rom333_residual_v1')
DEFAULT_ROM333_CONTROL_OUTPUT = Path(r'C:\Users\subsi\NanoJev\runs\three_backbone_clef_rom333_arith_control_v1')
DEFAULT_CONTROL_OUTPUT = Path(r'C:\Users\subsi\NanoJev\runs\three_backbone_clef_binary_raw_rom_arith_control_v1')


def atomic_json(path, payload):
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(payload,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    os.replace(tmp,path)


class Logger:
    def __init__(self, output):
        self.output=output
        self.file=output/'events.jsonl'
    def emit(self, event, **fields):
        row={'event':event,**fields}
        line=json.dumps(row,sort_keys=True)
        print(line,flush=True)
        with self.file.open('a',encoding='utf-8') as f:
            f.write(line+'\n')


def defaults(args):
    # The existing evidence extractor only needs these documented controls.
    args.path_batch=1 if args.path_batch is None else args.path_batch
    return args


def checkpoint(*, torch, output, head, optimizer, cursor, rolling, bank_sha, config, logger):
    from safetensors.torch import save_file
    step=int(cursor['global_step'])
    final=output/'checkpoints'/f'step-{step:09d}'
    tmp=final.with_name(final.name+'.tmp')
    if tmp.exists() or final.exists():
        raise RuntimeError('BINARY_CHECKPOINT_COLLISION: '+str(final))
    tmp.mkdir(parents=True)
    try:
        save_file({k:v.detach().cpu().contiguous() for k,v in head.state_dict().items()},str(tmp/'head.safetensors'))
        torch.save(optimizer.state_dict(),tmp/'optimizer.pt')
        torch.save({'python':random.getstate(),'cpu':torch.get_rng_state(),
                    'cuda':torch.cuda.get_rng_state_all()},tmp/'rng.pt')
        atomic_json(tmp/'meta.json',{'schema':SCHEMA,'cursor':cursor,'rolling':list(rolling),
                     'bankManifestSha256':bank_sha,'config':config})
        os.replace(tmp,final)
        state={'schema':SCHEMA,'checkpoint':str(final.resolve()),'bankManifestSha256':bank_sha,
               'config':config,'cursor':cursor,'rolling':list(rolling)}
        atomic_json(output/'state.json',state)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp)
    logger.emit('binary_checkpoint_saved',step=step,pass_index=cursor['pass_index'],
                checkpoint=str(final.resolve()))
    return final


def next_cursor(cursor,n_shards,n_examples):
    """Cursor always denotes NEXT untrained sample, not last trained sample."""
    nxt=dict(cursor)
    nxt['offset']+=n_examples
    # shard transition happens after the current shard is exhausted, at run loop.
    return nxt


def make_shards(bank, count):
    items=bank['groups']
    return [items[i:i+count] for i in range(0,len(items),count)]


def read_cache(*,torch,cache_dir,bank_sha,source,champion,bundles,examples,args,logger,shard_index):
    """Lazily materialize immutable frozen evidence; later passes load from disk.

    A shard is fixed by its source group order; shuffled presentations are
    only selected after loading its data. No generation or evaluation occurs.
    """
    path=cache_dir/f'shard-{shard_index:05d}.pt'
    keys={(group_id,variant) for group_id,variant,_ in examples}
    if path.is_file():
        payload=torch.load(path,map_location='cpu',weights_only=True)
        if (payload['schema']!=SCHEMA or payload['bankManifestSha256']!=bank_sha or
            payload['source']!=source or set(map(tuple,payload['keys']))!=keys):
            raise RuntimeError('BINARY_FROZEN_CACHE_IDENTITY_MISMATCH')
        return {(k[0],k[1]):v for k,v in zip(payload['keys'],payload['rows'])}
    bank_objective=deps()[2]
    result={}
    started=time.perf_counter()
    for extraction_index,(group_id,variant,serialized) in enumerate(examples,1):
        question=champion.base.deserialize_question(serialized,bank_objective)
        result[(group_id,variant)]=frozen_evidence(torch=torch,champion=champion,bundles=bundles,
                                                   question=question,args=args)
        if extraction_index % 8 == 0 or extraction_index == len(examples):
            logger.emit('binary_evidence_cache_progress',shard=shard_index,
                        cached=extraction_index,total=len(examples))
    payload={'schema':SCHEMA,'bankManifestSha256':bank_sha,'source':source,
             'keys':[list(k) for k in sorted(result)],'rows':[result[k] for k in sorted(result)]}
    tmp=path.with_suffix('.pt.tmp')
    torch.save(payload,tmp)
    os.replace(tmp,path)
    logger.emit('binary_evidence_cache_created',shard=shard_index,examples=len(result),
                elapsed_seconds=round(time.perf_counter()-started,2))
    return result


def train(args):
    validate_training_controls(args)
    if args.resume and args.output_dir is None:
        raise RuntimeError('BINARY_RESUME_REQUIRES_OUTPUT_DIR')
    if args.rom333:
        from nanojev_binary_rom333_bank import load_rom333_bank
        bank=load_rom333_bank(args.bank_dir,split='train')
        variants=('rom333','rom333_reverse')
        representation='rom333'
    else:
        bank=load_bank(args.bank_dir,split='train')
        variants=VARIANTS
        representation='raw_rom'
    control_plan = None
    control_runtime = None
    if args.arithmetic_control:
        import nanojev_binary_raw_rom_arithmetic_control as control_runtime
        if (args.arithmetic_control_every < 1 or args.arithmetic_control_report_every < 1
            or not math.isfinite(args.arithmetic_control_target_loss)
            or args.arithmetic_control_target_loss <= 0):
            raise RuntimeError('ROM_ARITH_CONTROL_INVALID_SCHEDULE_OR_TARGET')
        control_plan = control_runtime.load_control_plan(
            bank_dir=args.arithmetic_control_bank_dir, seed=args.seed,
            expected_source=bank['source'])
    config={'target_loss':args.target_loss,'rolling_questions':args.rolling_questions,
            'grad_accumulation':args.grad_accumulation,'seed':args.seed,
            'shard_groups':args.shard_groups,'head_lr':args.head_lr,
            'training_policy':TRAINING_POLICY,'weight_decay':args.weight_decay,
            'grad_clip':args.grad_clip,'evidence':{k:int(getattr(args,k)) for k in ('max_prompt_tokens','max_answer_tokens','prompt_evidence_tokens','answer_evidence_tokens','path_batch','tinystories_path_batch')},'variant':representation+'-both-orientations',
            'objective':'plain-cross-entropy-no-label-smoothing-no-auxiliary'}
    if control_plan is not None:
        config['arithmetic_control'] = control_runtime.plan_identity(control_plan, args)
    shards=make_shards(bank,args.shard_groups)
    output=Path(args.output_dir).expanduser().resolve()
    if args.dry_run:
        print(json.dumps({'ok':True,'event':'binary_continuous_plan','source':bank['source'],
          'train_groups':bank['count'],'examples_per_pass':bank['count']*2,
          'shards':len(shards),'bankManifestSha256':bank['manifest_sha256'],
          'config':config,'no_generation':True,'accuracy_computed':False,'arithmetic_control':bool(control_plan)},sort_keys=True))
        return
    state_path=output/'state.json'
    if args.resume:
        state=json.loads(state_path.read_text(encoding='utf-8'))
        if (state.get('schema')!=SCHEMA or state['bankManifestSha256']!=bank['manifest_sha256']
            or state['config']!=config):
            raise RuntimeError('BINARY_RESUME_CONTRACT_MISMATCH')
        cursor=dict(state['cursor'])
        rolling=deque(state['rolling'],maxlen=args.rolling_questions)
        checkpoint_dir=Path(state['checkpoint']).resolve(strict=True)
        meta=json.loads((checkpoint_dir/'meta.json').read_text(encoding='utf-8'))
        if (meta['cursor']!=cursor or meta['rolling']!=list(rolling)
            or meta['bankManifestSha256']!=bank['manifest_sha256']):
            raise RuntimeError('BINARY_RESUME_CHECKPOINT_STATE_MISMATCH')
    else:
        if output.exists() and any(output.iterdir()):
            raise RuntimeError('BINARY_OUTPUT_NOT_EMPTY: '+str(output))
        output.mkdir(parents=True,exist_ok=True)
        cursor={'pass_index':0,'shard_position':0,'offset':0,'global_step':0,'examples_seen':0}
        if control_plan is not None:
            cursor['arithmetic_control_steps'] = 0
        rolling=deque(maxlen=args.rolling_questions)
        checkpoint_dir=None
        atomic_json(output/'experiment.json',{'schema':SCHEMA,'bankManifestSha256':bank['manifest_sha256'],
                    'bankDir':bank['bank_path'],'cutover':{'repo':'johnrraymond/NanoJev-CLEF',
                     'revision':'222a8e361121a5aa35e0ecd0e073d5f645ef54f9','cycle':136,'reuse':2},
                    'config':config,'source':bank['source']})
    (output/'checkpoints').mkdir(exist_ok=True)
    cache_dir=output/'evidence_cache'
    cache_dir.mkdir(exist_ok=True)
    logger=Logger(output)
    logger.emit('binary_train_start',resume=args.resume,bank=bank['bank_path'],
                train_groups=bank['count'],train_examples_per_pass=bank['count']*2,
                target_loss=args.target_loss,rolling_questions=args.rolling_questions,
                source_champion='cycle-000136-reuse-002',representation=representation,accuracy_computed=False)
    import torch
    torch.manual_seed(args.seed)
    torch.cuda.manual_seed_all(args.seed)
    random.seed(args.seed)
    champion,_,objective=deps()
    head,bundles,optimizer,_=load_model(torch=torch,args=args,source=bank['source'],
                                      logger=logger,checkpoint=checkpoint_dir)
    if args.rom333:
        from nanojev_binary_rom333_bank import audit_token_visibility
        audit_token_visibility(bank=bank,bundles=bundles,champion=champion,
                               objective=objective,args=args,logger=logger)
    params=champion.joint_parameters(head,bundles[champion.TRAINABLE_LABEL].lm)
    control_prepared = None
    if control_plan is not None:
        if 'arithmetic_control_steps' not in cursor:
            raise RuntimeError('ROM_ARITH_CONTROL_CURSOR_MISSING')
        control_prepared = control_runtime.prepare(
            torch=torch, champion=champion, objective=objective, bundles=bundles,
            args=args, plan=control_plan, logger=logger)
        control_initial_loss = control_runtime.fixed_loss(
            torch=torch, champion=champion, head=head, prepared=control_prepared)
        logger.emit('rom_arithmetic_control_baseline_loss',
                    fixed_loss=round(control_initial_loss, 8),
                    control_steps=cursor['arithmetic_control_steps'],
                    resume=args.resume, accuracy_computed=False)
    head.train()
    bundles[champion.TRAINABLE_LABEL].lm.eval()
    since_report=time.perf_counter()
    since_report_examples=0
    most_recent_loss=None
    optimizer_step_in_flight=False
    if len(rolling)==args.rolling_questions and sum(rolling)/len(rolling)<=args.target_loss:
        logger.emit('binary_loss_target_already_reached',step=cursor['global_step'],
            rolling_loss=sum(rolling)/len(rolling),target_loss=args.target_loss)
        return
    try:
        while True:
            order=pass_shard_order(len(shards),args.seed,cursor['pass_index'])
            if cursor['shard_position']>=len(shards):
                cursor['pass_index']+=1
                cursor['shard_position']=0
                cursor['offset']=0
                logger.emit('binary_pass_complete',completed_passes=cursor['pass_index'],
                            global_step=cursor['global_step'],examples_seen=cursor['examples_seen'],
                            rolling_loss=(sum(rolling)/len(rolling) if rolling else None))
                continue
            shard_index=order[cursor['shard_position']]
            sample_rows=shard_examples(shards[shard_index],args.seed,cursor['pass_index'],shard_index,variants=variants)
            if cursor['offset']>len(sample_rows):
                raise RuntimeError('BINARY_RESUME_OFFSET_OUT_OF_RANGE')
            cache=read_cache(torch=torch,cache_dir=cache_dir,bank_sha=bank['manifest_sha256'],
                             source=bank['source'],champion=champion,bundles=bundles,
                             examples=sample_rows,args=args,logger=logger,shard_index=shard_index)
            for start in range(cursor['offset'],len(sample_rows),args.grad_accumulation):
                batch=sample_rows[start:start+args.grad_accumulation]
                optimizer.zero_grad(set_to_none=True)
                losses=[]
                for group_id,variant,serialized in batch:
                    question=champion.base.deserialize_question(serialized,objective)
                    evidence=evidence_on_cuda(champion,cache[(group_id,variant)])
                    logits=head(evidence)
                    loss=torch.nn.functional.cross_entropy(logits.float().unsqueeze(0),
                        torch.tensor([question.gold_index],dtype=torch.long,device=logits.device))
                    if not bool(torch.isfinite(loss).item()):
                        raise RuntimeError('BINARY_TRAIN_NONFINITE_LOSS')
                    (loss/len(batch)).backward()
                    losses.append(float(loss.detach().item()))
                    del evidence,logits,loss
                grad_norm=float(torch.nn.utils.clip_grad_norm_(params,args.grad_clip).item())
                if not math.isfinite(grad_norm):
                    raise RuntimeError('BINARY_TRAIN_NONFINITE_GRADIENT')
                optimizer_step_in_flight=True
                before = capture_residual_before(head) if (cursor['global_step']==0 or
                    (cursor['global_step']+1)%args.checkpoint_steps==0) else None
                optimizer.step()
                if before is not None:
                    inspect_residual_step(torch=torch,head=head,before=before,
                                          logger=logger,step=cursor['global_step']+1)
                optimizer_step_in_flight=False
                rolling.extend(losses)
                most_recent_loss=sum(losses)/len(losses)
                cursor=next_cursor(cursor,len(shards),len(batch))
                cursor['global_step']+=1
                cursor['examples_seen']+=len(batch)
                since_report_examples+=len(batch)
                if (control_prepared is not None and
                    cursor['global_step'] % args.arithmetic_control_every == 0):
                    # A second training step on the SAME head and optimizer.
                    # Binary rolling loss remains ROM-only, never mixed with
                    # three-class arithmetic loss.
                    optimizer_step_in_flight = True
                    control_result = control_runtime.train_step(
                        torch=torch, champion=champion, head=head,
                        optimizer=optimizer, params=params, prepared=control_prepared,
                        args=args, control_steps=cursor['arithmetic_control_steps'],
                        logger=logger)
                    optimizer_step_in_flight = False
                    cursor['arithmetic_control_steps'] += 1
                    control_n = cursor['arithmetic_control_steps']
                    if (control_n == 1 or
                        control_n % args.arithmetic_control_report_every == 0):
                        control_loss = control_runtime.fixed_loss(
                            torch=torch, champion=champion, head=head,
                            prepared=control_prepared)
                        logger.emit('rom_arithmetic_control_loss',
                            rom_step=cursor['global_step'], control_steps=control_n,
                            session_start_loss=round(control_initial_loss, 8),
                            fixed_loss=round(control_loss, 8),
                            batch_loss=round(control_result['batch_loss'], 8),
                            gradient_norm=round(control_result['gradient_norm'], 6),
                            target_loss=args.arithmetic_control_target_loss,
                            target_reached=control_loss<=args.arithmetic_control_target_loss,
                            accuracy_computed=False)
                sufficient=len(rolling)==args.rolling_questions
                mean_loss=sum(rolling)/len(rolling)
                achieved=sufficient and mean_loss<=args.target_loss
                due=cursor['global_step']%args.checkpoint_steps==0
                capped=args.max_steps and cursor['global_step']>=args.max_steps
                if (cursor['global_step']==1 or cursor['global_step']%args.report_steps==0 or achieved or capped):
                    elapsed=time.perf_counter()-since_report
                    logger.emit('binary_training_loss',step=cursor['global_step'],
                         pass_index=cursor['pass_index']+1,shard_position=cursor['shard_position']+1,
                         examples_seen=cursor['examples_seen'],batch_loss=round(most_recent_loss,7),
                         rolling_loss=round(mean_loss,7),rolling_samples=len(rolling),
                         window_ready=sufficient,gradient_norm=round(grad_norm,5),
                         examples_per_second=round(since_report_examples/max(elapsed,1e-9),2),
                         target_loss=args.target_loss,accuracy_computed=False)
                    since_report=time.perf_counter();since_report_examples=0
                if due or achieved or capped:
                    checkpoint(torch=torch,output=output,head=head,optimizer=optimizer,
                               cursor=cursor,rolling=rolling,bank_sha=bank['manifest_sha256'],
                               config=config,logger=logger)
                    # Preserve only recent safe checkpoints after committing state.
                    old=sorted((output/'checkpoints').glob('step-*'))
                    for stale in old[:-args.keep_checkpoints]:
                        shutil.rmtree(stale)
                if achieved:
                    logger.emit('binary_loss_target_reached',step=cursor['global_step'],
                        rolling_loss=round(mean_loss,7),window=args.rolling_questions,
                        target_loss=args.target_loss,checkpoint=str((output/'state.json').resolve()))
                    return
                if capped:
                    logger.emit('binary_run_step_limit_reached',step=cursor['global_step'],
                                rolling_loss=round(mean_loss,7),target_loss=args.target_loss)
                    return
            cursor['offset']=0
            cursor['shard_position']+=1
            del cache
    except KeyboardInterrupt:
        # Only save after a COMPLETED optimizer step. Ctrl-C mid-backward must
        # not resume from a state with partially updated gradients.
        optimizer.zero_grad(set_to_none=True)
        if optimizer_step_in_flight:
            logger.emit('binary_interrupt_during_optimizer_step',
                message='Cannot checkpoint a partly applied optimizer step; resume the previous committed checkpoint.')
        elif cursor['global_step'] and (not state_path.is_file() or
            json.loads(state_path.read_text(encoding='utf-8'))['cursor']['global_step'] != cursor['global_step']):
            checkpoint(torch=torch,output=output,head=head,optimizer=optimizer,
                       cursor=cursor,rolling=rolling,bank_sha=bank['manifest_sha256'],
                       config=config,logger=logger)
        logger.emit('binary_training_interrupted',global_step=cursor['global_step'])


def parse(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bank-dir',type=Path,default=None)
    p.add_argument('--rom333',action='store_true',
                   help='train English-like ROM3.3.3 instead of legacy JSON preROM')
    p.add_argument('--output-dir',type=Path,default=None)
    p.add_argument('--arithmetic-control',action='store_true',
                   help='interleave the proven fixed-32 arithmetic learning task with raw-ROM training')
    from nanojev_arithmetic_runtime import DEFAULT_BANK as ARITHMETIC_BANK
    p.add_argument('--arithmetic-control-bank-dir',type=Path,default=ARITHMETIC_BANK)
    p.add_argument('--arithmetic-control-every',type=int,default=4,
                   help='one arithmetic optimizer step after every N ROM optimizer steps')
    p.add_argument('--arithmetic-control-report-every',type=int,default=25,
                   help='measure fixed-32 arithmetic training loss every N arithmetic steps')
    p.add_argument('--arithmetic-control-target-loss',type=float,default=0.05)
    p.add_argument('--resume',action='store_true')
    p.add_argument('--dry-run',action='store_true',help='verify bank and print plan; no GPU/model')
    p.add_argument('--target-loss',type=float,default=0.20)
    p.add_argument('--rolling-questions',type=int,default=256)
    p.add_argument('--grad-accumulation',type=int,default=4)
    p.add_argument('--shard-groups',type=int,default=16)
    p.add_argument('--report-steps',type=int,default=20)
    p.add_argument('--checkpoint-steps',type=int,default=200)
    p.add_argument('--keep-checkpoints',type=int,default=2)
    p.add_argument('--max-steps',type=int,default=0,help='0 = unlimited; safe diagnostic cap')
    p.add_argument('--seed',type=int,default=20261010)
    p.add_argument('--head-lr',type=float,default=1e-4)
    p.add_argument('--weight-decay',type=float,default=.01)
    p.add_argument('--grad-clip',type=float,default=1.0)
    p.add_argument('--max-prompt-tokens',type=int,default=None)
    p.add_argument('--max-answer-tokens',type=int,default=64)
    p.add_argument('--prompt-evidence-tokens',type=int,default=6)
    p.add_argument('--answer-evidence-tokens',type=int,default=4)
    p.add_argument('--path-batch',type=int,default=4)
    p.add_argument('--tinystories-path-batch',type=int,default=4)
    p.add_argument('--local-files-only',action='store_true',default=True)
    p.add_argument('--allow-model-download',dest='local_files_only',action='store_false')
    args=p.parse_args(argv)
    if args.max_prompt_tokens is None:
        args.max_prompt_tokens=768 if args.rom333 else 512
    if args.bank_dir is None:
        if args.rom333:
            from nanojev_binary_rom333_bank import DEFAULT_OUTPUT as ROM333_BANK
            args.bank_dir = ROM333_BANK
        else:
            args.bank_dir = DEFAULT_BANK
    if args.output_dir is None:
        args.output_dir = (DEFAULT_ROM333_CONTROL_OUTPUT if args.arithmetic_control else DEFAULT_ROM333_OUTPUT) if args.rom333 else (DEFAULT_CONTROL_OUTPUT if args.arithmetic_control else DEFAULT_OUTPUT)
    if args.keep_checkpoints < 1:
        p.error('--keep-checkpoints must be >= 1')
    return args


if __name__=='__main__':
    try:
        train(parse())
    except Exception as exc:
        print(json.dumps({'event':'binary_train_error','error':str(exc),'traceback':traceback.format_exc()},sort_keys=True),file=sys.stderr)
        raise SystemExit(1)
