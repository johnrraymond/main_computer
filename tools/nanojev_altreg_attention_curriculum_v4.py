#!/usr/bin/env python3
"""Fork a frozen-Qwen/v3-attention head into a progressively harder X/Y prompt mix.

Does NOT touch source v3 checkpoints or tune RESULT/FINAL. New checkpoints are
self-contained and are compatible only with this curriculum identity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import sys
from types import SimpleNamespace

import numpy as np
import torch
from torch import nn

import nanojev_altreg_independent_math_qwen_precision_v2 as base
import nanojev_altreg_math_v3_decoder_experiment as v3

SCHEMA = 'nanojev.altreg.attention.curriculum.v4'
VERSION = 'attention-curriculum-v4.2'
STAGES = ('registered_logbar','one_raw_operand','one_logbar_removed',
          'one_raw_no_logbar','plain_expression','story_problem')
# All phases retain prior tasks; phase 5 has all six forms.
MIXES = (
    (1.,0.,0.,0.,0.,0.),
    (.60,.40,0.,0.,0.,0.),
    (.40,.20,.40,0.,0.,0.),
    (.25,.15,.20,.40,0.,0.),
    (.20,.10,.10,.20,.40,0.),
    (.15,.10,.10,.10,.25,.30),
)

# The operand stories deliberately use SIGNED readings, balances, and changes:
# the inherited stream includes negative numbers and zero. Every operation in
# the original train/validation set has an exact equivalent here.
STORIES = {
 'sym_add': 'A ledger has a signed balance of {a}. An adjustment of {b} is applied. What is the new balance?',
 'written_add': 'A ledger has a signed balance of {a}. An adjustment of {b} is applied. What is the new balance?',
 'algebra_difference': 'A ledger changes by {a} and then by {b}. What is the combined change?',
 'sym_sub': 'An elevator goes from floor {b} to floor {a}. What is its signed change in floors?',
 'written_sub': 'An elevator goes from floor {b} to floor {a}. What is its signed change in floors?',
 'algebra_left': 'An elevator goes from floor {b} to floor {a}. What is its signed change in floors?',
 'algebra_balance': 'An elevator goes from floor {b} to floor {a}. What is its signed change in floors?',
 'sym_reverse_sub': 'An elevator goes from floor {a} to floor {b}. What is its signed change in floors?',
 'written_difference': 'An elevator goes from floor {a} to floor {b}. What is its signed change in floors?',
 'algebra_right': 'An elevator goes from floor {a} to floor {b}. What is its signed change in floors?',
 'sym_multiply': 'A calibrated instrument starts with a signed reading of {a} and applies a multiplier of {b}. What reading results?',
 'written_product': 'A calibrated instrument starts with a signed reading of {a} and applies a multiplier of {b}. What reading results?',
 'sym_absdiff': 'Two sensors report {a} and {b}. How far apart are their readings?',
 'sym_max': 'Two signed readings are {a} and {b}. What is the larger reading?',
 'written_greater': 'A sensor reads {a} and another reads {b}. Is the first reading strictly greater than the second? Answer true or false.',
 'conditional_gt': 'A sensor reads {a} and another reads {b}. Is the first reading strictly greater than the second? Answer true or false.',
 'written_average': 'Two temperature changes are {a} and {b} degrees. What is their arithmetic mean?',
 'conditional_min': 'Two signed readings are {a} and {b}. What is the smaller reading?',
 'conditional_positive': 'Sensor A reads {a} and sensor B reads {b}. Report B if A is positive; otherwise report A. What do you report?',
 'conditional_abs': 'Sensor A reads {a} and sensor B reads {b}. Is the absolute value of A larger than that of B? Answer true or false.',
 'multistep_double': 'A signed balance is {a}. Double it, then subtract an adjustment of {b}. What remains?',
 'multistep_difference': 'A signed reading is {a}; subtract {b}, then add the original reading again. What is the result?',
 'multistep_combine': 'Two signed changes are {a} and {b}. Combine them, then double the combined change. What is the result?',
 'multistep_square': 'Two signed signals are {a} and {b}. Add them and square the total. What is the result?',
}

# The second story presentation is a genuine semantic paraphrase, not an
# appended field-label preamble. Each template mentions a and b exactly once,
# in the opposite order from the corresponding STORIES template. For directional
# operations, the operand roles (and numerical ground truth) are unchanged.
STORY_PARAPHRASES = {
 'sym_add': 'An adjustment of {b} is applied to a ledger balance of {a}. What signed balance remains?',
 'written_add': 'An adjustment of {b} is applied to a ledger balance of {a}. What signed balance remains?',
 'algebra_difference': 'After a signed change of {b}, another change of {a} occurs. What is their combined change?',
 'sym_sub': 'An elevator ends at floor {a} after starting at floor {b}. What signed change in floors did it make?',
 'written_sub': 'An elevator ends at floor {a} after starting at floor {b}. What signed change in floors did it make?',
 'algebra_left': 'An elevator ends at floor {a} after starting at floor {b}. What signed change in floors did it make?',
 'algebra_balance': 'An elevator ends at floor {a} after starting at floor {b}. What signed change in floors did it make?',
 'sym_reverse_sub': 'An elevator ends at floor {b} after starting at floor {a}. What signed change in floors did it make?',
 'written_difference': 'An elevator ends at floor {b} after starting at floor {a}. What signed change in floors did it make?',
 'algebra_right': 'An elevator ends at floor {b} after starting at floor {a}. What signed change in floors did it make?',
 'sym_multiply': 'A multiplier of {b} is applied to a signed instrument reading of {a}. What reading results?',
 'written_product': 'A multiplier of {b} is applied to a signed instrument reading of {a}. What reading results?',
 'sym_absdiff': 'One sensor reads {b} and another reads {a}. What is the absolute distance between their readings?',
 'sym_max': 'Of the two signed readings {b} and {a}, what is the larger value?',
 'written_greater': 'The second sensor reads {b} and the first reads {a}. Is the first reading strictly greater than the second? Answer true or false.',
 'conditional_gt': 'The second sensor reads {b} and the first reads {a}. Is the first reading strictly greater than the second? Answer true or false.',
 'written_average': 'A temperature change of {b} degrees and another of {a} degrees occur. What is their arithmetic mean?',
 'conditional_min': 'Of the two signed readings {b} and {a}, what is the smaller value?',
 'conditional_positive': 'Sensor B reads {b} and sensor A reads {a}. Report B if A is positive; otherwise report A. What do you report?',
 'conditional_abs': 'Sensor B reads {b} and sensor A reads {a}. Is the absolute value of A larger than that of B? Answer true or false.',
 'multistep_double': 'An adjustment of {b} is subtracted from twice an initial signed balance of {a}. What remains?',
 'multistep_difference': 'An adjustment of {b} is subtracted from a signed reading of {a}; then the original reading is added again. What is the result?',
 'multistep_combine': 'A signed change of {b} and another of {a} are combined. What is twice their total?',
 'multistep_square': 'Two signed signals measure {b} and {a}. What is the square of their sum?',
}
assert set(STORY_PARAPHRASES) == set(STORIES)

def prompt_revision():
    """Make resumed training reject incompatible prompt-template changes."""
    spec = {'stages': STAGES, 'mixes': MIXES, 'stories': STORIES,
            'story_paraphrases': STORY_PARAPHRASES,
            'operation_side_channel': 'constant-for-plain-and-story'}
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode('utf-8')).hexdigest()


def emit(name, **fields):
    print(json.dumps({'event':name, **fields},sort_keys=True,allow_nan=False),flush=True)


def instruction(a, b, operation):
    if operation == 'conditional_abs':
        return f'Is abs({a}) > abs({b})? Answer true or false.'
    return base.OPERATIONS[operation][2].replace(base.registers.FIRST,str(a)).replace(base.registers.SECOND,str(b))


def curriculum_prompt(a,b,operation,stage,reverse=False):
    """Every prompt has exact numeric ground truth (a,b) and no answer leakage."""
    if stage not in STAGES:raise ValueError('CURRICULUM_UNKNOWN_STAGE:'+str(stage))
    a=int(a);b=int(b)
    if stage == 'registered_logbar':
        return base.prompt(a,b,operation,reverse)
    if stage == 'story_problem':
        # Alternate two independently intelligible phrasings of the same
        # operation. No X/Y, "input" numbering, extra operand copies or LogBars.
        sentence=(STORY_PARAPHRASES if reverse else STORIES)[operation].format(a=a,b=b)
        return sentence+'\nAnswer:'
    if stage == 'plain_expression':
        prefix='Please solve: ' if reverse else 'operation: '
        return prefix+instruction(a,b,operation)+'\nAnswer:'
    first,second=base.registers.FIRST,base.registers.SECOND
    if stage == 'one_logbar_removed':
        # Both registers present; drop the LogBar for alternating operand side.
        x = f'{a}, {base.registers.logbar(a)}' if reverse else str(a)
        y = str(b) if reverse else f'{b}, {base.registers.logbar(b)}'
        lines=[f'{first} = {x}',f'{second} = {y}']
        if reverse:lines.reverse()
        return '\n'.join(lines)+'\noperation: '+base.OPERATIONS[operation][2]+'\nAnswer:'
    # Keep one operand registered; use the other literal directly in the task.
    # In one_raw_operand, the literal keeps its LogBar aid; in the next level
    # it does not. Alternate sides to prevent asymmetric shortcut learning.
    lose_first=bool(reverse)
    if lose_first:
        raw_a=str(a)
        if stage == 'one_raw_operand':raw_a+=f' [LogBar {base.registers.logbar(a)}]'
        line=f'{second} = {b}, {base.registers.logbar(b)}'
        question=instruction(raw_a,second,operation)
    else:
        raw_b=str(b)
        if stage == 'one_raw_operand':raw_b+=f' [LogBar {base.registers.logbar(b)}]'
        line=f'{first} = {a}, {base.registers.logbar(a)}'
        question=instruction(first,raw_b,operation)
    return line+'\noperation: '+question+'\nAnswer:'


def phase_mix(step, phase_steps):
    index=min(len(MIXES)-1, max(0,(step-1)//phase_steps))
    return index,MIXES[index]


def choose_stage(rng,weights):
    n=rng.random();total=0.
    for i,w in enumerate(weights):
        total+=w
        if n<total:return STAGES[i]
    return STAGES[-1]


def operation_ids(examples,stages,device):
    # The final plain and story levels provide no operation identity out of
    # band. Existing V3Decoder expects a valid ID, so use a SINGLE CONSTANT for
    # every problem in those levels; it contains no information about which
    # operation is requested. The natural-language prompt is the sole source.
    ids=[0 if stage in ('plain_expression','story_problem') else base.OP_IDS[e['operation']]
         for e,stage in zip(examples,stages)]
    return torch.tensor(ids,dtype=torch.long,device=device)


def evaluate_stage(decoder,parent,backbone,tokenizer,device,examples,stage,max_tokens,batch_size):
    decoder.eval()
    truth=np.asarray([[e['a'],e['b']] for e in examples],dtype=np.float64)
    preds={}
    with torch.no_grad():
        for label,rev in (('original',False),('reversed',True)):
            batches=[]
            for offset in range(0,len(examples),batch_size):
                sample=examples[offset:offset+batch_size]
                prompts=[curriculum_prompt(e['a'],e['b'],e['operation'],stage,rev) for e in sample]
                flat,seq,mask=v3.full_capture(backbone,tokenizer,device,prompts,max_tokens)
                h=parent.extract(flat.to(device));parent_xy=parent.xy_coord(h)
                op=operation_ids(sample,[stage]*len(sample),device)
                prediction=decoder(h,parent_xy,op,seq.to(device),mask.to(device))
                batches.append(v3.predict(prediction,base,'attention').cpu().numpy())
            preds[label]=np.concatenate(batches).astype(np.float64)
    preds['averaged']=(preds['original']+preds['reversed'])/2
    result={}
    for key,guess in preds.items():
        rel=np.abs(guess-truth)/np.maximum(1.,abs(truth))
        hit5=rel<=.05
        result[key]={'joint_5pct':float(hit5.all(axis=1).mean()),
                     'operand_5pct':float(hit5.mean()),
                     'operand_1pct':float((rel<=.01).mean()),
                     'operand_0p1pct':float((rel<=.001).mean()),
                     'mean_abs_relative_error':float(rel.mean())}
    decoder.train()
    return result


def make_config(args,source_sha,parent_sha,source_cfg):
    return {'schema':SCHEMA,'backbone':'qwen','source_sha256':source_sha,
        'parent_sha256':parent_sha,'source_best_step':int(args._source_step),
        'model_id':base.MODEL_ID,'source_root':str(args.source_root.resolve()),
        'parent_root':str(Path(source_cfg['parent_root']).resolve()),
        'parent_checkpoint':source_cfg['parent_checkpoint'],
        'seed':args.seed,'lr':args.lr,'batch_size':args.batch_size,
        'phase_steps':args.phase_steps,'hidden':source_cfg['hidden'],
        'max_prompt_tokens':source_cfg['max_prompt_tokens'],
        'validation_examples':args.validation_examples,'eval_every':args.eval_every,
        'report_every':args.report_every,'mixes':[list(x) for x in MIXES],
        'stages':list(STAGES),'prompt_revision':prompt_revision(),
        'side_channel':'no-operation-id-on-plain-and-story',
        'parent_feature_semantics':'frozen-precision-v2'}


def snapshot(decoder,optimizer,stream,rng,step,cfg,best):
    result={'schema':SCHEMA,'config':cfg,'step':step,'head':decoder.state_dict(),
            'optimizer':optimizer.state_dict(),'stream':stream.state_dict(),
            'mix_rng_state':rng.getstate(),'best':best,
            'rng_cpu':torch.get_rng_state()}
    if torch.cuda.is_available():result['rng_cuda']=torch.cuda.get_rng_state_all()
    return result


def restore(checkpoint,decoder,optimizer,stream,rng,cfg):
    saved=torch.load(checkpoint,map_location='cpu',weights_only=True)
    if saved.get('schema')!=SCHEMA or saved.get('config')!=cfg:
        raise RuntimeError('CURRICULUM_RESUME_CONFIG_MISMATCH')
    decoder.load_state_dict(saved['head'],strict=True)
    optimizer.load_state_dict(saved['optimizer'])
    stream.load_state_dict(saved['stream']);rng.setstate(saved['mix_rng_state'])
    torch.set_rng_state(saved['rng_cpu'])
    if torch.cuda.is_available() and saved.get('rng_cuda') is not None:
        torch.cuda.set_rng_state_all(saved['rng_cuda'])
    return saved['step'],saved['best']


def run(args):
    if args.preview:
        for stage in STAGES:
            for reverse in (False,True):
                emit('curriculum_prompt_preview',stage=stage,reverse=reverse,
                     text=curriculum_prompt(17,-3,'written_sub',stage,reverse))
        return 0
    if args.steps<0 or args.batch_size<1 or args.phase_steps<1 or args.eval_every<1 or args.report_every<1 or args.validation_examples<1:
        raise ValueError('CURRICULUM_INVALID_ARGUMENT')
    if args.device=='auto':args.device='cuda' if torch.cuda.is_available() else 'cpu'
    if args.device=='cuda' and not torch.cuda.is_available():raise RuntimeError('CURRICULUM_CUDA_UNAVAILABLE')
    output=args.output_root.expanduser().resolve()
    args.source_root=args.source_root.expanduser().resolve()
    if output==args.source_root or output in args.source_root.parents or args.source_root in output.parents:
        raise RuntimeError('CURRICULUM_SOURCE_OUTPUT_OVERLAP')
    if args.resume:
        source_path=output/'source-v3-attention.pt'
        if not source_path.is_file() or not (output/'latest.pt').is_file():
            raise RuntimeError('CURRICULUM_RESUME_FILES_MISSING')
    else:
        if output.exists() and any(output.iterdir()):
            raise RuntimeError('CURRICULUM_OUTPUT_NOT_EMPTY_USE_RESUME')
        source_path=args.source_root/(args.source_checkpoint+'.pt')
        if not source_path.is_file():raise FileNotFoundError('CURRICULUM_SOURCE_NOT_FOUND:'+str(source_path))
    source=torch.load(source_path,map_location='cpu',weights_only=True)
    source_cfg=source.get('config',{})
    if source.get('schema')!=v3.SCHEMA or source_cfg.get('arm')!='attention' or source_cfg.get('backbone')!='qwen':
        raise RuntimeError('CURRICULUM_SOURCE_IDENTITY_MISMATCH')
    args._source_step=int(source['step'])
    p_root=Path(source_cfg['parent_root']);p_name=source_cfg['parent_checkpoint']
    p_args=SimpleNamespace(parent_root=p_root,parent_checkpoint=p_name,backbone='qwen',device=args.device)
    parent_cp,cache,parent,parent_sha=v3.get_parent(base,p_args)
    if parent_sha!=source_cfg['parent_sha256']:
        raise RuntimeError('CURRICULUM_PARENT_CHANGED')
    source_sha=v3.sha256(source_path)
    cfg=make_config(args,source_sha,parent_sha,source_cfg)
    token_dim=(parent_cp['config']['input_dim']//8)*2
    torch.manual_seed(args.seed)
    if args.device=='cuda':torch.cuda.manual_seed_all(args.seed)
    decoder=v3.V3Decoder(parent_cp['config']['width'],len(base.OP_IDS),'attention',
                         math.floor(math.log10(base.registers.MAX_VALUE)),source_cfg['hidden'],token_dim).to(args.device)
    decoder.load_state_dict(source['head'],strict=True)
    optimizer=torch.optim.AdamW(decoder.parameters(),lr=args.lr)
    reserved_tasks=base.registers.build_plan(parent_cp['config']['seed'])[1]
    reserved={(min(int(t['a']),int(t['b'])),max(int(t['a']),int(t['b']))) for t in reserved_tasks if t['split']!='train'}
    stream=base.Stream(args.seed+303,reserved)
    mix_rng=random.Random(args.seed+919)
    val_examples=cache['validation']['examples'][:args.validation_examples]
    if len(val_examples)!=args.validation_examples:raise RuntimeError('CURRICULUM_INSUFFICIENT_VALIDATION')
    initial=0;best={'step':0,'joint_5pct':-1.}
    if not args.resume:
        output.mkdir(parents=True,exist_ok=True)
        # Pin the source champion once; subsequent v3 training can update best.pt
        # without changing this curriculum's origin.
        base.atomic_save(output/'source-v3-attention.pt',source)
        source_sha=v3.sha256(output/'source-v3-attention.pt')
        cfg=make_config(args,source_sha,parent_sha,source_cfg)
        base.write_json(output/'fork.json',{'config':cfg,'source_step':args._source_step,
            'notice':'Frozen Qwen and parent; forked attention head only. RESULT/FINAL diagnostic-only.'})
    else:
        # The forked copy, not the live source best.pt, defines resume identity.
        expected_cfg=torch.load(output/'latest.pt',map_location='cpu',weights_only=True).get('config',{})
        if expected_cfg!=cfg:raise RuntimeError('CURRICULUM_RESUME_CONFIG_MISMATCH')
    c=SimpleNamespace(device=args.device,dtype=args.dtype,allow_model_download=args.allow_model_download)
    _,backbone,tokenizer,model_device,_=base.cassette.load_source('qwen',c)
    if str(model_device)!=args.device or any(p.requires_grad for p in backbone.parameters()):
        raise RuntimeError('CURRICULUM_FROZEN_BACKBONE_INVALID')
    if args.resume:
        initial,best=restore(output/'latest.pt',decoder,optimizer,stream,mix_rng,cfg)
    else:
        base.atomic_save(output/'latest.pt',snapshot(decoder,optimizer,stream,mix_rng,0,cfg,best))
        emit('curriculum_fork_ready',source_step=args._source_step,output=str(output),version=VERSION)
    if initial>args.steps:raise RuntimeError('CURRICULUM_TARGET_BELOW_SAVED_STEP')
    for step in range(initial+1,args.steps+1):
        phase,weights=phase_mix(step,args.phase_steps)
        examples=[stream.get() for _ in range(args.batch_size)]
        stages=[choose_stage(mix_rng,weights) for _ in examples]
        prompts=[curriculum_prompt(e['a'],e['b'],e['operation'],stage,(step*args.batch_size+j)%2==1)
                 for j,(e,stage) in enumerate(zip(examples,stages))]
        flat,seq,mask=v3.full_capture(backbone,tokenizer,args.device,prompts,cfg['max_prompt_tokens'])
        ops=operation_ids(examples,stages,args.device)
        gold=torch.tensor([[e['a'],e['b']] for e in examples],device=args.device,dtype=torch.float32)
        with torch.no_grad():
            h=parent.extract(flat.to(args.device));old_xy=parent.xy_coord(h)
        out=decoder(h,old_xy,ops,seq.to(args.device),mask.to(args.device))
        loss,parts=v3.objective(out,gold,base,'attention')
        if not torch.isfinite(loss):raise RuntimeError('CURRICULUM_NONFINITE_LOSS')
        optimizer.zero_grad(set_to_none=True);loss.backward()
        if any(p.grad is not None and not bool(torch.isfinite(p.grad).all()) for p in decoder.parameters()):
            raise RuntimeError('CURRICULUM_NONFINITE_GRADIENT')
        nn.utils.clip_grad_norm_(decoder.parameters(),1.)
        optimizer.step()
        if step%args.report_every==0:
            emit('curriculum_progress',step=step,target_step=args.steps,phase=phase,
                 mix={name:weights[i] for i,name in enumerate(STAGES) if weights[i]},
                 loss=float(loss.detach()),loss_components=parts,examples_seen=stream.draws)
        if step%args.eval_every==0 or step==args.steps:
            # Validation is deliberately fixed, and never reads the test split.
            scores={}
            for name in STAGES[:min(phase+2,len(STAGES))]:
                scores[name]=evaluate_stage(decoder,parent,backbone,tokenizer,args.device,
                    val_examples,name,cfg['max_prompt_tokens'],args.batch_size)
            current=STAGES[phase]
            primary=scores[current]['averaged']['joint_5pct']
            improved=primary>best['joint_5pct'] if best.get('phase')==phase else True
            if improved:best={'step':step,'phase':phase,'joint_5pct':primary}
            base.write_json(output/('validation-step-%08d.json'%step),
                            {'step':step,'phase':phase,'validation':scores,'best':best})
            base.atomic_save(output/'latest.pt',snapshot(decoder,optimizer,stream,mix_rng,step,cfg,best))
            if improved:
                winning_snapshot=snapshot(decoder,optimizer,stream,mix_rng,step,cfg,best)
                base.atomic_save(output/('best-phase-%d.pt'%phase),winning_snapshot)
                # Means best in the current reached phase, never best across
                # incomparable curricula; phase-specific files remain intact.
                base.atomic_save(output/'best-curriculum.pt',winning_snapshot)
            emit('curriculum_validation',step=step,phase=phase,best=best,
                 metrics={name:v['averaged'] for name,v in scores.items()})
    emit('curriculum_complete',step=args.steps,source_step=args._source_step,output=str(output),best=best)
    return 0


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--version',action='version',version=VERSION)
    p.add_argument('--preview',action='store_true')
    p.add_argument('--source-root',type=Path,default=Path.home()/'NanoJev'/'runs'/'independent_altreg_math_qwen_v3_attention')
    p.add_argument('--source-checkpoint',choices=('best','latest'),default='best')
    p.add_argument('--output-root',type=Path,default=Path.home()/'NanoJev'/'runs'/'independent_altreg_math_qwen_curriculum_v4')
    p.add_argument('--steps',type=int,default=12000,help='Total NEW curriculum steps, not original v3 step count')
    p.add_argument('--phase-steps',type=int,default=2000)
    p.add_argument('--batch-size',type=int,default=16)
    p.add_argument('--lr',type=float,default=2e-5)
    p.add_argument('--eval-every',type=int,default=500)
    p.add_argument('--report-every',type=int,default=50)
    p.add_argument('--validation-examples',type=int,default=32)
    p.add_argument('--seed',type=int,default=20261015)
    p.add_argument('--device',choices=('cpu','cuda','auto'),default='cuda')
    p.add_argument('--dtype',choices=('float16','bfloat16','float32'),default='float16')
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--resume',action='store_true')
    return p

if __name__=='__main__':raise SystemExit(run(parser().parse_args()))
