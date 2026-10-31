#!/usr/bin/env python3
"""Second-pass stratified random mathematical frontier acquisition.

Starts ONLY from a completed ordered-frontier final.pt, in a separate output.
Samples all task-level cells evenly with reproducible randomized ordering and
fresh, effectively unbounded generated examples. Full, equally weighted
cross-cell reference loss determines plateau. No glass training or corpus scan.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import random
from types import SimpleNamespace
import time

from tools import nanojev_qwen_lm_radius_ladder as ladder
from tools import nanojev_qwen_lm_frontier_tasks as tasks
from tools import nanojev_qwen_lm_radius_ladder_frontier as fr
from tools import nanojev_qwen_mixed_robust_plateau as robust

SCHEMA='nanojev.qwen.frontier.mixed-acquisition.v1'


def _digest_int(*parts):
    data=json.dumps(parts,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()
    return int.from_bytes(hashlib.sha256(data).digest()[:8],'big')


def _policy(args,parent):
    foundation=parent['policy']
    return {
        'seed':args.seed,'source_levels':foundation['levels'],
        'source_seed':foundation['seed'],
        'operand_boundary':foundation['operand_boundary'],
        'start_rung_override':foundation['start_rung_override'],
        'batch_size':args.batch_size,'lr':args.lr,'max_seq':args.max_seq,
        'max_tokens':args.max_tokens,'max_new_tokens':args.max_new_tokens,
        'check_every':args.check_every,'reference_per_cell':args.reference_per_cell,
        'greedy_cases':args.greedy_cases,'min_steps':args.min_steps,
        'plateau_patience':args.plateau_patience,'loss_window':args.loss_window,
        'plateau_delta':args.plateau_delta,
        'plateau_relative_delta':args.plateau_relative_delta,
    }


def _validate(args):
    if (args.batch_size<1 or args.lr<=0 or args.max_seq<32 or
        args.max_tokens<args.max_seq or args.max_new_tokens<8 or
        args.check_every<1 or args.reference_per_cell<15 or
        args.greedy_cases<0 or args.min_steps<1 or args.plateau_patience<1 or
        args.loss_window<1 or args.plateau_delta<0 or args.plateau_relative_delta<0 or
        args.log_every<1 or args.save_every<1 or args.save_seconds<0 or
        args.max_steps<0 or args.seed<0):
        raise ValueError('MIXED_INVALID_ARGUMENTS')
    source=Path(args.frontier_output).resolve()
    output=Path(args.output).resolve()
    if not args.stage1_checkpoint or not args.text_checkpoint:
        raise ValueError('MIXED_PARENT_CHECKPOINTS_REQUIRED')
    for input_path in (args.stage1_checkpoint,args.text_checkpoint):
        if not Path(input_path).is_file():raise FileNotFoundError(input_path)
    # Also protects the completed ordered run and the original checkpoint inputs.
    ladder._outputs_safe(output,[source,Path(args.stage1_checkpoint).resolve().parent,
                                 Path(args.text_checkpoint).resolve().parent])
    if not source.is_dir():raise FileNotFoundError(source)
    return source,output


def _parent_final(path):
    import torch
    obj=torch.load(path,map_location='cpu',weights_only=True)
    state=obj.get('state',{})
    if (obj.get('schema')!=fr.SCHEMA or not isinstance(state,dict) or
        state.get('status')!='COMPLETE' or state.get('pending_check') or
        state.get('task_index')!=len(tasks.task_order(state['policy']['levels'])) or
        not isinstance(obj.get('lora'),dict) or not obj['lora'] or
        not isinstance(obj.get('optimizer'),dict)):
        raise RuntimeError('MIXED_PARENT_NOT_COMPLETED_ORDERED_FRONTIER')
    return obj


def _initial_state(parent,policy,sha):
    return {'schema':SCHEMA,'status':'TRAINING','policy':policy,
        'parent_sha256':sha,'parent_step':parent['step'],
        'seed_state':parent['seed_state'],
        'phase_step':0,'step':parent['step'],'sample_cursor':0,
        'check_count':0,'pending_check':False,
        'recent_loss':[],'best_reference_loss':None,'patience':0,
        'best_step':None,'best_phase_step':None,
        'last_reference_loss':None,'last_per_cell_loss':{},
        'errors_seen':0,'last_snapshot_step':parent['step']}


def _load(path):
    import torch
    try: return torch.load(path,map_location='cpu',weights_only=True)
    except Exception as exc:raise RuntimeError('MIXED_SNAPSHOT_UNREADABLE') from exc


def _load_working(out):
    working=out/'working.pt'
    try:
        ck=_load(working)
        if ck.get('schema')!=SCHEMA:raise RuntimeError('MIXED_WORKING_SCHEMA_INVALID')
        return ck
    except Exception as error:
        backup=out/'previous.pt'
        if not backup.exists():raise RuntimeError('MIXED_WORKING_UNREADABLE_NO_BACKUP') from error
        ck=_load(backup)
        if ck.get('schema')!=SCHEMA or not isinstance(ck.get('state'),dict):
            raise RuntimeError('MIXED_PREVIOUS_SCHEMA_INVALID')
        damaged=out/'working.corrupt.pt'
        if damaged.exists():raise RuntimeError('MIXED_CORRUPT_ALREADY_PRESERVED')
        os.replace(working,damaged)
        fr._atomic_copy(backup,working)
        fr._event('QWEN_MIXED_SNAPSHOT_RECOVERED',{'step':ck['state'].get('step')})
        return ck


def _prepare(args):
    source,output=_validate(args)
    parent_snapshot=output/'parent_snapshot.pt'
    working=output/'working.pt'
    if working.exists():
        if not parent_snapshot.is_file():raise RuntimeError('MIXED_PARENT_SNAPSHOT_MISSING')
        obj=_load_working(output)  # working / previous recovery
        state=obj.get('state',{})
        parent=_parent_final(parent_snapshot)
        if obj.get('schema')!=SCHEMA or state.get('parent_sha256')!=fr._sha256(parent_snapshot):
            raise RuntimeError('MIXED_RESUME_LINEAGE_DRIFT')
        if not isinstance(obj.get('optimizer'),dict) or not isinstance(obj.get('lora'),dict):
            raise RuntimeError('MIXED_RESUME_INVALID_CHECKPOINT')
        if state.get('policy')!=_policy(args,parent['state']):
            raise RuntimeError('MIXED_RESUME_POLICY_DRIFT')
        return output,state,obj,parent
    if output.exists() and any(output.iterdir()):
        if {p.name for p in output.iterdir()}!={'parent_snapshot.pt'}:
            raise RuntimeError('MIXED_REFUSES_NONEMPTY_UNRECOGNIZED_OUTPUT')
    else: output.mkdir(parents=True,exist_ok=True)
    if not parent_snapshot.exists():
        final=source/'final.pt'
        if not final.is_file():raise FileNotFoundError('MIXED_REQUIRES_ORDERED_FINAL:'+str(final))
        # Validate the source before copying, then use the *independent copy*
        # exclusively. Never read a live writer's partial contents.
        _parent_final(final)
        fr._atomic_copy(final,parent_snapshot)
    parent=_parent_final(parent_snapshot)
    # The completed source may be archived or moved after snapshotting.
    # Read the original fixed seed only if present, to check provenance.
    orig_seed=source/'seed_snapshot.pt'
    if orig_seed.is_file() and fr._sha256(orig_seed)!=parent['state']['seed_sha256']:
        raise RuntimeError('MIXED_SOURCE_SEED_LINEAGE_MISMATCH')
    state=_initial_state(parent['state'],_policy(args,parent['state']),fr._sha256(parent_snapshot))
    fr._event('QWEN_MIXED_PARENT',{'source_step':state['parent_step'],
             'parent_sha256':state['parent_sha256'],'cells':len(_cells(state))})
    return output,state,None,parent


def _cells(state):
    return tasks.task_order(state['policy']['source_levels'])


def _rung(state):
    p=state['policy']
    return (p['start_rung_override'] if p['start_rung_override']>=0
            else max(0,int(state['seed_state']['rung'])))


def _epoch_order(seed,epoch,cells):
    permutation=list(range(len(cells)))
    random.Random(_digest_int('mixed-order',seed,epoch)).shuffle(permutation)
    return permutation


def _sample_spec(state,ordinal):
    """Exactly one example from each cell per epoch, randomized order.

    Submode cycles modulo 60 to cover %2/%3/%4/%5 branches of generators;
    the large randomized index ensures that every example is fresh. This does
    NOT draw from reference/audit index ranges.
    """
    cells=_cells(state)
    epoch,position=divmod(ordinal,len(cells))
    cell_idx=_epoch_order(state['policy']['seed'],epoch,cells)[position]
    task,level=cells[cell_idx]
    offset=_digest_int('offset',state['policy']['seed'],task,level)%60
    submode=(epoch+offset)%60
    salt=_digest_int('sample',state['policy']['seed'],epoch,task,level)
    index=30_000_000+60*(salt%1_000_000_000)+submode
    return task,level,index


def _make(state,task,level,index,*,seed=None):
    p=state['policy']
    return tasks.make_case(task,level,index,
        seed=p['seed'] if seed is None else seed,
        operand_boundary=p['operand_boundary'],start_rung=_rung(state))


def _batch(state,args):
    rows=[]
    for n in range(args.batch_size):
        task,level,index=_sample_spec(state,state['sample_cursor']+n)
        rows.append(_make(state,task,level,index))
    return rows


def _reference(state,args):
    """Stable, macro-balanced reference deck; explicitly not a holdout test."""
    output={}
    for task,level in _cells(state):
        key=f'{task}:L{level}'
        # Keep every reference packet invariant across run/resume/checks.
        # Test branches by walking consecutive residues, not random luck.
        rows=[_make(state,task,level,60_000_000+i,seed=args.seed+71317)
              for i in range(args.reference_per_cell)]
        output[key]=rows
    return output


def _full_reference_loss(bb,tok,state,args,deck):
    per_cell={}
    fr._event('QWEN_MIXED_REFERENCE_START',{'step':state['step'],
        'cells':len(deck),'cases':len(deck)*args.reference_per_cell})
    for n,(cell,rows) in enumerate(deck.items(),1):
        per_cell[cell]=fr._reference_loss(bb,tok,rows,args)
        if n%3==0 or n==len(deck):
            fr._event('QWEN_MIXED_REFERENCE_PROGRESS',{'step':state['step'],
                'cells_completed':n,'cells_total':len(deck)})
    if not all(math.isfinite(value) for value in per_cell.values()):
        raise RuntimeError('MIXED_NONFINITE_FULL_REFERENCE')
    return sum(per_cell.values())/len(per_cell),per_cell


def _greedy_check(bb,tok,state,args,out,*,reconcile=False):
    if not args.greedy_cases:return {'tested':0,'errors':0}
    seen=(fr._logged_failure_keys(out/'mixed_discovered_errors.jsonl',state['step'])
          if reconcile else set())
    cells=_cells(state);errors=0
    for n in range(args.greedy_cases):
        ordinal=state['check_count']*args.greedy_cases+n
        task,level=cells[ordinal%len(cells)]
        index=70_000_000+ordinal*60+ordinal%60
        row=_make(state,task,level,index,seed=state['policy']['seed']+1447)
        got=fr._greedy_one(bb,tok,row,args)
        if (n+1)%2==0 or n+1==args.greedy_cases:
            fr._event('QWEN_MIXED_GREEDY_PROGRESS',{'step':state['step'],
                'tested':n+1,'total':args.greedy_cases})
        correct,parsed=ladder.correctness(row['source'],row['target'],got)
        if not correct:
            errors+=1;state['errors_seen']+=1
            if row['id'] not in seen:
                fr._append(out/'mixed_discovered_errors.jsonl',{
                    'schema':SCHEMA,'step':state['step'],'phase_step':state['phase_step'],
                    'task':task,'level':level,'case_id':row['id'],
                    'source':row['source'],'target':row['target'],
                    'greedy':got,'parsed':parsed,'parent_sha256':state['parent_sha256'],
                    'checkpoint_reference':'in_memory_mixed_step_'+str(state['step'])})
    return {'tested':args.greedy_cases,'errors':errors}


def _snapshot(bb,zero,opt,state):
    import torch
    return {'schema':SCHEMA,'state':state,'lora':zero.lora_state(bb),
        'optimizer':opt.state_dict(),'python_rng':random.getstate(),
        'torch_rng':torch.get_rng_state(),
        'cuda_rng':torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None}


def _save(out,bb,zero,opt,state,*,best=False):
    state['last_snapshot_step']=state['step']
    obj=_snapshot(bb,zero,opt,state)
    if best:fr._durable_torch(out/'best.pt',obj)
    if (out/'working.pt').exists():fr._atomic_copy(out/'working.pt',out/'previous.pt')
    fr._durable_torch(out/'working.pt',obj)
    ladder._atomic_json(out/'mixed_progress.json',state)


def _finalize(out,state):
    if not (out/'best.pt').is_file():raise RuntimeError('MIXED_MISSING_BEST_CHECKPOINT')
    best=_load(out/'best.pt')
    if (best.get('schema')!=SCHEMA or
        best.get('state',{}).get('parent_sha256')!=state['parent_sha256'] or
        best['state']['best_step']!=state['best_step']):
        raise RuntimeError('MIXED_BEST_LINEAGE_INVALID')
    fr._atomic_copy(out/'best.pt',out/'glass_ready.pt')
    ladder._atomic_json(out/'glass_readiness.json',{
        'schema':SCHEMA,'status':'MIXED_LOSS_PLATEAU','no_glass_training':True,
        'not_a_mathematical_accuracy_certificate':True,
        'parent_sha256':state['parent_sha256'],
        'glass_ready_checkpoint':'glass_ready.pt',
        'best_step':state['best_step'], 'best_phase_step':state['best_phase_step'],
        'best_macro_reference_loss':state['best_reference_loss'],
        'final_macro_reference_loss':state['last_reference_loss'],
        'latest_per_cell_loss':state['last_per_cell_loss'],
        'reference_per_cell':state['policy']['reference_per_cell'],
        'frontier_cells':len(_cells(state))})
    fr._event('QWEN_MIXED_GLASS_READY',{'best_step':state['best_step'],
        'best_macro_reference_loss':state['best_reference_loss'],
        'checkpoint':str(out/'glass_ready.pt')})


def run(args):
    import torch
    from tools import nanojev_qwen_zero_line_backbone as zero
    tasks.terminal_preflight()
    out,state,saved,parent=_prepare(args)
    if state['status']=='PLATEAU':
        _finalize(out,state)
        fr._event('QWEN_MIXED_DONE',{'status':'PLATEAU','step':state['step']})
        return state
    if state['status'] in ('PAUSED','BUDGET_LIMIT'):state['status']='TRAINING'
    bb,tok,sources,frozen,dirs,zero=ladder._fresh_parent(args)
    ladder._outputs_safe(out,dirs)
    if (sources!=state['seed_state']['source_sha256'] or
        frozen!=state['seed_state']['frozen_sha256']):
        raise RuntimeError('MIXED_BACKBONE_LINEAGE_MISMATCH')
    zero.restore_lora(bb,saved['lora'] if saved else parent['lora'])
    ps=[p for _,p in zero.lora_named_parameters(bb)]
    if not ps:raise RuntimeError('MIXED_MISSING_TRAINABLE_LORA')
    for p in ps:p.requires_grad_(True)
    opt=torch.optim.AdamW(ps,lr=args.lr,weight_decay=0.)
    old=saved if saved is not None else parent
    opt.load_state_dict(old['optimizer'])
    for group in opt.param_groups:group['lr']=args.lr
    for entry in opt.state.values():
        for k,v in entry.items():
            if torch.is_tensor(v):entry[k]=v.to(args.device)
    fr._restore_rng(old)
    del old,parent,saved
    deck=_reference(state,args)
    if state['phase_step']==0 and not (out/'working.pt').exists():_save(out,bb,zero,opt,state)
    fr._event('QWEN_MIXED_START',{'step':state['step'],'phase_step':state['phase_step'],
        'cells':len(deck),'reference_cases':len(deck)*args.reference_per_cell,
        'batch_size':args.batch_size,'glass_training':False,'parent_sha256':state['parent_sha256']})
    fr._event('QWEN_MIXED_RADIUS',fr._radius_info(state,phase='samples',
        seed_radius=fr._legacy_seed_radius(Path(args.frontier_output)/'seed_snapshot.pt')))
    last_save=time.monotonic()

    def save_now(*,best=False):
        nonlocal last_save
        _save(out,bb,zero,opt,state,best=best)
        last_save=time.monotonic()

    def finish_check(*,reconcile=False):
        loss,per_cell=_full_reference_loss(bb,tok,state,args,deck)
        greedy=_greedy_check(bb,tok,state,args,out,reconcile=reconcile)
        # Loss plateau is its own phase. Paired greedy accuracy is measured only
        # after this phase, by --phase balance.
        net={'deferred_to_accuracy_balance':True}
        # Shared plateau primitive reads task_step; here it is the mixed-pass
        # step counter, NOT the last sampled task's progress.
        state['task_step']=state['phase_step']
        plateau_args=SimpleNamespace(loss_window=args.loss_window,
            plateau_delta=args.plateau_delta,
            plateau_relative_delta=args.plateau_relative_delta,
            min_steps_per_task=args.min_steps,plateau_patience=args.plateau_patience)
        plateau=fr._plateau_update(state,loss,plateau_args)
        state.pop('task_step',None)
        state['last_reference_loss']=loss
        state['last_per_cell_loss']=per_cell
        state['check_count']+=1
        state['pending_check']=False
        robust_result=robust.evaluate(out=out,step=state['step'],phase_step=state['phase_step'],
            macro_reference_loss=loss,per_cell=per_cell,parent_sha256=state['parent_sha256'],
            check_every=args.check_every,min_steps=args.min_steps,expected_cells=len(deck))
        fr._event('QWEN_MIXED_ROBUST_PLATEAU',robust_result)
        if plateau['improved']:
            state['best_step']=state['step'];state['best_phase_step']=state['phase_step']
        fr._event('QWEN_MIXED_CHECK',{'step':state['step'],'phase_step':state['phase_step'],
            'level_anchors':[state['policy']['operand_boundary']*(2**n)
                             for n in range(state['policy']['source_levels'])],
            'dynamic_radius_expansion':False,
            'macro_reference_loss':round(loss,9),
            'smoothed_macro_loss':round(plateau['smoothed'],9),
            'best_smoothed_macro_loss':round(plateau['best'],9),
            'per_cell':{k:round(v,9) for k,v in per_cell.items()},
            'patience':robust_result['stale'],'legacy_patience':state['patience'],
            'robust_plateau':robust_result,'net_competency':net,
            'greedy':greedy,'full_cells':len(deck)})
        hist=out/'mixed_history.jsonl'
        seen=False
        if hist.is_file():
            for line in hist.read_text(encoding='utf8').splitlines():
                try:entry=json.loads(line)
                except json.JSONDecodeError:continue
                if entry.get('step')==state['step']:seen=True;break
        if not seen:fr._append(hist,{'step':state['step'],'phase_step':state['phase_step'],
            'macro_reference_loss':loss,'smoothed_macro_loss':plateau['smoothed'],
            'best_smoothed_macro_loss':plateau['best'],'patience':state['patience'],
            'per_cell':per_cell,'greedy':greedy})
        # Do not conflate loss convergence with competency convergence.
        if robust_result['converged']:
            state['status']='PLATEAU'
        save_now(best=plateau['improved'])
        if state['status']=='PLATEAU':_finalize(out,state)

    with fr._stop_requests() as stop:
        if state['pending_check'] and not stop['requested']:
            fr._event('QWEN_MIXED_PENDING_CHECK',{'step':state['step']})
            finish_check(reconcile=True)
        while state['status']!='PLATEAU':
            if stop['requested']:
                state['status']='PAUSED';save_now()
                fr._event('QWEN_MIXED_STOP',{'reason':'signal',
                    'signal':stop['signal'],'step':state['step'],
                    'pending_check':state['pending_check']})
                return state
            if args.max_steps and state['phase_step']>=args.max_steps:
                state['status']='BUDGET_LIMIT';save_now()
                fr._event('QWEN_MIXED_STOP',{'reason':'budget_without_plateau',
                    'step':state['step'],'phase_step':state['phase_step']})
                return state
            rows=_batch(state,args)
            bb.eval();opt.zero_grad(set_to_none=True)
            amp=zero.resolve_amp_dtype(args.device,'auto') if args.device=='cuda' else 'cpu'
            with torch.autocast('cuda' if args.device=='cuda' else 'cpu',
                    enabled=args.device=='cuda',
                    dtype=torch.bfloat16 if amp=='bfloat16' else torch.float16):
                loss=ladder._batch_loss(bb,tok,rows,args.device,args.max_seq)
            if not bool(torch.isfinite(loss)):raise RuntimeError('MIXED_NONFINITE_TRAIN_LOSS')
            loss.backward()
            if any(p.grad is None or not bool(torch.isfinite(p.grad).all()) for p in ps):
                raise RuntimeError('MIXED_INVALID_LORA_GRADIENT')
            torch.nn.utils.clip_grad_norm_(ps,1.)
            opt.step()
            state['sample_cursor']+=args.batch_size
            state['phase_step']+=1;state['step']+=1
            if (state['phase_step']%args.log_every==0 or state['phase_step']==1):
                fr._event('QWEN_MIXED_TRAIN',{'step':state['step'],
                    'phase_step':state['phase_step'],'loss':round(float(loss.detach()),8),
                    'sampled_cells':[f"{row['task']}:L{row['level']}" for row in rows],
                    'sampled_operand_anchors':[state['policy']['operand_boundary']*(2**row['level'])
                                               for row in rows]})
            if state['phase_step']%args.check_every==0:
                state['pending_check']=True;save_now()
                if stop['requested']:continue
                finish_check()
            elif (state['phase_step']%args.save_every==0 or stop['requested'] or
                  (args.save_seconds and time.monotonic()-last_save>=args.save_seconds)):
                save_now()
    fr._event('QWEN_MIXED_DONE',{'status':'PLATEAU',
        'step':state['step'],'phase_step':state['phase_step'],
        'best_step':state['best_step'],'errors_seen':state['errors_seen']})
    return state


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=('run','status','radius','preview'),default='run')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--frontier-output',required=True,
                   help='Completed ordered frontier directory; never written')
    p.add_argument('--stage1-checkpoint')
    p.add_argument('--text-checkpoint')
    p.add_argument('--output',help='Separate mixed pass output; default sibling ..._mixed_v1')
    p.add_argument('--seed',type=int,default=91733)
    p.add_argument('--batch-size',type=int,default=4)
    p.add_argument('--lr',type=float,default=5e-5)
    p.add_argument('--max-seq',type=int,default=192)
    p.add_argument('--max-tokens',type=int,default=256)
    p.add_argument('--max-new-tokens',type=int,default=64)
    p.add_argument('--check-every',type=int,default=250)
    p.add_argument('--reference-per-cell',type=int,default=16,
                   help='Stable deck per task-level (>=15 covers five place-value scales and other generator submodes)')
    p.add_argument('--greedy-cases',type=int,default=6)
    p.add_argument('--competency-anchors',type=int,default=1)
    p.add_argument('--competency-fresh',type=int,default=18)
    p.add_argument('--competency-replay',type=int,default=24)
    p.add_argument('--min-steps',type=int,default=1000)
    p.add_argument('--plateau-patience',type=int,default=6)
    p.add_argument('--loss-window',type=int,default=3)
    p.add_argument('--plateau-delta',type=float,default=0.0005)
    p.add_argument('--plateau-relative-delta',type=float,default=0.002)
    p.add_argument('--save-every',type=int,default=10)
    p.add_argument('--save-seconds',type=float,default=60.0)
    p.add_argument('--log-every',type=int,default=20)
    p.add_argument('--max-steps',type=int,default=0,
                   help='0 unlimited; if hit, STOP (never imply plateau)')
    p.add_argument('--baseline');p.add_argument('--backbone');p.add_argument('--activation');p.add_argument('--format')
    p.add_argument('--allow-model-download',action='store_true')
    args=p.parse_args(argv)
    if args.output is None:
        source=Path(args.frontier_output)
        args.output=str(source.with_name(source.name+'_mixed_v1'))
    if args.stage in ('status','radius'):
        out=Path(args.output)
        if not (out/'working.pt').is_file():raise FileNotFoundError(out/'working.pt')
        state=_load_working(out)['state']
        if state.get('schema')!=SCHEMA:raise RuntimeError('MIXED_STATUS_INVALID_SCHEMA')
        radius=fr._radius_info(state,phase='samples',
            seed_radius=fr._legacy_seed_radius(Path(args.frontier_output)/'seed_snapshot.pt'))
        fr._event('QWEN_MIXED_RADIUS',radius)
        if args.stage=='radius':return radius
        fr._event('QWEN_MIXED_STATUS',state)
        return state
    if args.stage=='preview':
        source=Path(args.frontier_output)
        if not (source/'final.pt').is_file():raise FileNotFoundError(source/'final.pt')
        parent=_parent_final(source/'final.pt')
        state=_initial_state(parent['state'],_policy(args,parent['state']),fr._sha256(source/'final.pt'))
        sample=[_sample_spec(state,i) for i in range(min(18,len(_cells(state))))]
        r={'schema':SCHEMA,'cells':_cells(state),'first_epoch':sample,
           'reference_total':len(_cells(state))*args.reference_per_cell,
           'glass_training':False,'model_loaded':False}
        fr._event('QWEN_MIXED_PREVIEW',r);return r
    return run(args)


if __name__=='__main__':main()
