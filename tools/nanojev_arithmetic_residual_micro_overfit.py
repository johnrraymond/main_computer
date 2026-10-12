#!/usr/bin/env python3
"""Isolated fixed-32-example loss-only test of the original CLEF residual-tap optimizer.

Never touches the normal continuous output directory or sealed SQLite database.
No accuracy measurements, no task generation, no changes to champion core/backbones.
The normal arithmetic_probe.py owns every accuracy calculation.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import random
import sys
import traceback
from collections import deque

from nanojev_arithmetic_runtime import (
    DEFAULT_BANK, SCHEMA, TRAINING_POLICY, deps, load_bank, load_model,
    frozen_evidence, evidence_on_cuda, capture_residual_before, inspect_residual_step,
)
from nanojev_arithmetic_continuous_train import Logger, checkpoint, atomic_json
from nanojev_antirom_compression import (
    LEVELS as ANTIROM_LEVELS, representation as antirom_representation,
    output_for as antirom_output_for, convert_presentation as antirom_convert_presentation,
    renderer_fingerprint as antirom_renderer_fingerprint,
    selection_fingerprint as antirom_selection_fingerprint,
)
from nanojev_rom_arithmetic_language import (
    REPRESENTATION as ROM_REPRESENTATION, ROM_OUTPUT,
    convert_presentation, selection_identity, representation_fingerprint, verify_prompt_visibility,
)

DEFAULT_OUTPUT=Path(r'C:\Users\subsi\NanoJev\runs\clef_arithmetic_residual_micro_overfit_v2')


def select_fixed_32(bank,seed):
    by_label={label:[] for label in ('LESS','EQUAL','MORE')}
    for group_id,g in bank['groups']:
        by_label[g['label']].append((group_id,g))
    chosen=[]
    for label,n in (('LESS',6),('EQUAL',5),('MORE',5)):
        rng=random.Random(f'{seed}:fixed32:{label}')
        rng.shuffle(by_label[label])
        if len(by_label[label])<n:
            raise RuntimeError(f'ARITH_MICRO_BANK_TOO_SMALL: {label}')
        chosen.extend(by_label[label][:n])
    rng=random.Random(f'{seed}:fixed32:order')
    rng.shuffle(chosen)
    return [(gid,orientation,g['variants'][orientation]) for gid,g in chosen for orientation in ('forward','reverse')]


def parse(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bank-dir',type=Path,default=DEFAULT_BANK)
    p.add_argument('--output-dir',type=Path,default=DEFAULT_OUTPUT)
    p.add_argument('--dry-run',action='store_true')
    representation_flags=p.add_mutually_exclusive_group()
    representation_flags.add_argument('--rom-language',action='store_true',
                   help='use English-like ROM instead of the proven English arithmetic wording')
    representation_flags.add_argument('--antirom-level',choices=ANTIROM_LEVELS,
                   help='run one prompt-only AntiROM compression level C0 through C5')
    p.add_argument('--full-budget',action='store_true',
                   help='complete all --max-steps even after reaching --target-loss (controlled sweep)')
    p.add_argument('--max-steps',type=int,default=300)
    p.add_argument('--report-steps',type=int,default=25)
    p.add_argument('--target-loss',type=float,default=0.05)
    p.add_argument('--grad-accumulation',type=int,default=4)
    p.add_argument('--head-lr',type=float,default=1e-4)
    p.add_argument('--weight-decay',type=float,default=.01)
    p.add_argument('--grad-clip',type=float,default=1.0)
    p.add_argument('--seed',type=int,default=20261010)
    p.add_argument('--max-prompt-tokens',type=int,default=512)
    p.add_argument('--max-answer-tokens',type=int,default=64)
    p.add_argument('--prompt-evidence-tokens',type=int,default=6)
    p.add_argument('--answer-evidence-tokens',type=int,default=4)
    p.add_argument('--path-batch',type=int,default=4)
    p.add_argument('--tinystories-path-batch',type=int,default=4)
    p.add_argument('--local-files-only',action='store_true',default=True)
    p.add_argument('--allow-model-download',action='store_false',dest='local_files_only')
    args=p.parse_args(argv)
    if args.rom_language and args.output_dir == DEFAULT_OUTPUT:
        args.output_dir = ROM_OUTPUT
    if args.antirom_level and args.output_dir == DEFAULT_OUTPUT:
        args.output_dir = antirom_output_for(args.antirom_level)
    if (args.max_steps<1 or args.report_steps<1 or args.grad_accumulation<1
        or 32%args.grad_accumulation or not math.isfinite(args.target_loss)
        or not 0<args.target_loss<math.log(3) or args.head_lr<=0 or args.grad_clip<=0):
        p.error('invalid fixed-batch diagnostic controls')
    return args


def run(args):
    bank=load_bank(args.bank_dir,split='train')
    examples=select_fixed_32(bank,args.seed)
    representation = (antirom_representation(args.antirom_level) if args.antirom_level
                      else ROM_REPRESENTATION if args.rom_language else 'english-arithmetic-original-v1')
    selection_sha = None
    if args.antirom_level:
        groups=dict(bank['groups'])
        selection_sha=antirom_selection_fingerprint(examples,groups,args.antirom_level)
        examples=[(gid,variant,antirom_convert_presentation(sample,groups[gid]['target'],variant,args.antirom_level))
                  for gid,variant,sample in examples]
    elif args.rom_language:
        groups=dict(bank['groups'])
        selection_sha=selection_identity(examples,groups)
        examples=[(gid,variant,convert_presentation(sample,groups[gid]['target'],variant))
                  for gid,variant,sample in examples]
    config={'representation':representation,
            'renderer_sha256':(antirom_renderer_fingerprint(args.antirom_level) if args.antirom_level
                               else representation_fingerprint() if args.rom_language else None),
            'antirom_level':args.antirom_level,
            'selection_sha256':selection_sha,
            'evidence':{k:int(getattr(args,k)) for k in (
                'max_prompt_tokens','max_answer_tokens','prompt_evidence_tokens',
                'answer_evidence_tokens','path_batch','tinystories_path_batch')},
            'training_policy':TRAINING_POLICY,'objective':'plain-three-class-cross-entropy',
            'head_lr':args.head_lr,'weight_decay':args.weight_decay,
            'grad_clip':args.grad_clip,'seed':args.seed,'fixed_examples':32,
            'target_loss':args.target_loss,'full_budget':args.full_budget}
    if args.dry_run:
        print(json.dumps({'ok':True,'event':'arithmetic_fixed32_plan','bankManifestSha256':bank['manifest_sha256'],
               'training_policy':TRAINING_POLICY,'examples':len(examples),'max_steps':args.max_steps,
               'representation':representation,'selection_sha256':selection_sha,
               'output_dir':str(args.output_dir),'full_budget':args.full_budget,
               'example_prompt':examples[0][2]['candidates'][0]['paths'][0]['prompt'],
               'no_generation':True,'accuracy_computed':False},sort_keys=True))
        return
    output=Path(args.output_dir).expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise RuntimeError('ARITH_MICRO_OUTPUT_NOT_EMPTY: '+str(output))
    output.mkdir(parents=True,exist_ok=True)
    (output/'checkpoints').mkdir(exist_ok=True)
    logger=Logger(output)
    atomic_json(output/'experiment.json',{'schema':SCHEMA,'bankManifestSha256':bank['manifest_sha256'],
        'source':bank['source'],'config':config,
        'mode':('fixed32-antirom-compression-sweep-v1' if args.antirom_level
                else 'fixed32-rom-arithmetic-language-control' if args.rom_language
                else 'fixed32-residual-memorization-diagnostic'),
        'cutover':'pinned-cycle-136-reuse-002'})
    import torch
    torch.manual_seed(args.seed)
    torch.cuda.manual_seed_all(args.seed)
    random.seed(args.seed)
    champion,_,objective=deps()
    head,bundles,optimizer,_=load_model(torch=torch,args=args,source=bank['source'],logger=logger)
    if args.rom_language or args.antirom_level:
        visibility=verify_prompt_visibility(samples=[q for _,_,q in examples],bundles=bundles,args=args)
        logger.emit('rom_arithmetic_visibility_verified',backbones=visibility,presentations=len(examples))
    params=champion.joint_parameters(head,bundles[champion.TRAINABLE_LABEL].lm)
    prepared=[]
    for group_id,variant,serialized in examples:
        question=champion.base.deserialize_question(serialized,objective)
        rows=frozen_evidence(torch=torch,champion=champion,bundles=bundles,
                             question=question,args=args)
        prepared.append((group_id,variant,question.gold_index,rows))
    logger.emit('arithmetic_fixed32_evidence_ready',examples=len(prepared),
                target_loss=args.target_loss,representation=representation,accuracy_computed=False)

    def fixed_loss():
        head.eval()
        with torch.inference_mode():
            losses=[]
            for _,_,gold,rows in prepared:
                logits=head(evidence_on_cuda(champion,rows))
                loss=torch.nn.functional.cross_entropy(logits.float().unsqueeze(0),
                    torch.tensor([gold],dtype=torch.long,device=logits.device))
                losses.append(float(loss.item()))
        head.train()
        return sum(losses)/len(losses)

    first_loss=fixed_loss()
    logger.emit('arithmetic_fixed32_baseline_loss',loss=round(first_loss,8),
                representation=representation,accuracy_computed=False)
    cursor={'pass_index':0,'shard_position':0,'offset':0,'global_step':0,'examples_seen':0}
    rolling=deque(maxlen=32)
    final_loss=first_loss
    success=False
    head.train()
    for step in range(1,args.max_steps+1):
        offset=((step-1)*args.grad_accumulation)%len(prepared)
        batch=[prepared[(offset+i)%len(prepared)] for i in range(args.grad_accumulation)]
        optimizer.zero_grad(set_to_none=True)
        losses=[]
        for _,_,gold,rows in batch:
            logits=head(evidence_on_cuda(champion,rows))
            loss=torch.nn.functional.cross_entropy(logits.float().unsqueeze(0),
                torch.tensor([gold],dtype=torch.long,device=logits.device))
            if not bool(torch.isfinite(loss).item()):
                raise RuntimeError('ARITH_MICRO_NONFINITE_LOSS')
            (loss/len(batch)).backward()
            losses.append(float(loss.detach().item()))
        grad_norm=float(torch.nn.utils.clip_grad_norm_(params,args.grad_clip).item())
        if not math.isfinite(grad_norm):
            raise RuntimeError('ARITH_MICRO_NONFINITE_GRADIENT')
        before=capture_residual_before(head) if (step==1 or step%args.report_steps==0) else None
        optimizer.step()
        if before is not None:
            inspect_residual_step(torch=torch,head=head,before=before,logger=logger,step=step)
        cursor['global_step']=step
        cursor['examples_seen']+=len(batch)
        rolling.extend(losses)
        if step==1 or step%args.report_steps==0 or step==args.max_steps:
            final_loss=fixed_loss()
            logger.emit('arithmetic_fixed32_loss',step=step,
                 baseline_loss=round(first_loss,8),fixed_loss=round(final_loss,8),
                 rolling_training_loss=round(sum(rolling)/len(rolling),8),
                 gradient_norm=round(grad_norm,6),target_loss=args.target_loss,
                 representation=representation,accuracy_computed=False)
            if final_loss<=args.target_loss:
                success=True
                if not args.full_budget:
                    break
    checkpoint(torch=torch,output=output,head=head,optimizer=optimizer,cursor=cursor,
               rolling=rolling,bank_sha=bank['manifest_sha256'],config=config,logger=logger)
    logger.emit('arithmetic_fixed32_complete',steps=cursor['global_step'],
                full_budget=args.full_budget,
                baseline_loss=round(first_loss,8),final_fixed_loss=round(final_loss,8),
                target_loss=args.target_loss,loss_target_reached=success,
                representation=representation,accuracy_computed=False)


if __name__=='__main__':
    try:
        run(parse())
    except Exception as exc:
        print(json.dumps({'event':'arithmetic_fixed32_error','error':str(exc),
                          'traceback':traceback.format_exc()},sort_keys=True),file=sys.stderr)
        raise SystemExit(1)
