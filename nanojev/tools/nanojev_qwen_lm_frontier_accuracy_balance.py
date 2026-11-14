#!/usr/bin/env python3
"""Raw-Qwen post-loss Accuracy Balance, before any Rank-1 glass training.

Separate checkpoint/optimizer workspace and lineage. Measures gain vs loss on
exactly paired greedy problems, maintains a persistent archive of actual misses,
expands difficulty beyond the L0/L1 boundary, and promotes the best measured
raw-Qwen competency checkpoint, not the last or lowest-loss checkpoint.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import random
import time

from tools import nanojev_qwen_lm_radius_ladder as ladder
from tools import nanojev_qwen_lm_radius_ladder_frontier as fr
from tools import nanojev_qwen_lm_frontier_mixed_pass as mixed
from tools import nanojev_qwen_lm_frontier_tasks as tasks
from tools import nanojev_qwen_lm_competency_ledger as ledger

SCHEMA='nanojev.qwen.frontier.accuracy-balance.v1'


def _policy(args,parent):
    p=parent['state']['policy']
    return {'source_levels':p['source_levels'],'levels':p['source_levels'],
        'operand_boundary':p['operand_boundary'],
        'start_rung_override':p['start_rung_override'],
        'seed':p['seed'],'batch_size':args.balance_batch_size,
        'lr':args.balance_lr,'check_every':args.balance_check_every,
        'min_steps':args.balance_min_steps,'max_steps':args.balance_max_steps,
        'patience':args.balance_patience,'window':args.balance_window,
        'anchors_per_cell':args.balance_anchors_per_cell,
        'fresh_per_check':args.balance_fresh_per_check,
        'replay_per_check':args.balance_replay_per_check,
        'expansion_every':args.balance_expansion_every,
        'max_extra_levels':args.balance_max_extra_levels,
        'max_seq':args.max_seq,'max_tokens':args.max_tokens,
        'max_new_tokens':args.max_new_tokens}


def _validate(args,samples,out):
    if (args.balance_check_every<1 or args.balance_min_steps<0 or args.balance_max_steps<0
        or args.balance_batch_size<2 or args.balance_lr<=0 or
        args.balance_patience<2 or args.balance_window<2 or
        args.balance_anchors_per_cell<1 or args.balance_fresh_per_check<1 or
        args.balance_replay_per_check<0 or args.balance_expansion_every<1 or
        args.balance_max_extra_levels<1):
        raise ValueError('BALANCE_INVALID_POLICY')
    if not args.stage1_checkpoint or not args.text_checkpoint:
        raise ValueError('BALANCE_REQUIRES_PARENT_CHECKPOINT_PATHS')
    for path in (args.stage1_checkpoint,args.text_checkpoint):
        if not Path(path).is_file():raise FileNotFoundError(path)
    ladder._outputs_safe(out,[samples,Path(args.output),
                              Path(args.stage1_checkpoint).parent,
                              Path(args.text_checkpoint).parent])
    if samples == out or samples in out.parents or out in samples.parents:
        raise ValueError('BALANCE_SEPARATE_OUTPUT_REQUIRED')
    return samples,out


def _load(path):
    import torch
    return torch.load(path,map_location='cpu',weights_only=True)


def _recover_working(out):
    try:
        obj=_load(out/'working.pt')
        if obj.get('schema')!=SCHEMA:raise RuntimeError('BALANCE_BAD_WORKING_SCHEMA')
        return obj
    except Exception as exc:
        backup=out/'previous.pt'
        if not backup.is_file():
            raise RuntimeError('BALANCE_WORKING_CORRUPT_NO_PREVIOUS') from exc
        obj=_load(backup)
        if obj.get('schema')!=SCHEMA:
            raise RuntimeError('BALANCE_PREVIOUS_SCHEMA_INVALID')
        damaged=out/'working.corrupt.pt'
        if damaged.exists():raise RuntimeError('BALANCE_ALREADY_PRESERVED_CORRUPTION')
        (out/'working.pt').replace(damaged)
        fr._atomic_copy(backup,out/'working.pt')
        fr._event('QWEN_BALANCE_RECOVERED_PREVIOUS',{'step':obj['state']['step']})
        return obj


def _parent(path):
    parent=_load(path)
    state=parent.get('state',{})
    if (parent.get('schema')!=mixed.SCHEMA or
        not isinstance(state,dict) or state.get('best_step')!=state.get('step') or
        not isinstance(parent.get('optimizer'),dict) or
        not isinstance(parent.get('lora'),dict) or not parent['lora']):
        raise RuntimeError('BALANCE_PARENT_NOT_BEST_COMPLETED_LOSS_CHECKPOINT')
    return parent


def _new_state(parent,sha,policy):
    source=parent['state']
    return {'schema':SCHEMA,'status':'TRAINING','policy':policy,
        'parent_sha256':sha,'seed_sha256':sha,
        'seed_state':source['seed_state'],
        'parent_step':source['step'],'step':source['step'],'phase_step':0,
        'check_count':0,'pending_check':True,
        'best_step':source['step'],'best_phase_step':0,
        'best_cohort_correct':None,'best_cohort_total':None,
        'cohort_keys':[],'last_check':{},'last_snapshot_step':source['step']}


def _prepare(args,samples,out):
    _validate(args,samples,out)
    readiness=samples/'glass_readiness.json'
    source=samples/'glass_ready.pt'
    if not source.is_file() or not readiness.is_file():
        raise RuntimeError('BALANCE_REQUIRES_COMPLETED_LOSS_PLATEAU')
    ready=json.loads(readiness.read_text())
    if ready.get('status')!='MIXED_LOSS_PLATEAU':
        raise RuntimeError('BALANCE_LOSS_PARENT_NOT_PLATEAU')
    out.mkdir(parents=True,exist_ok=True)
    frozen=out/'parent_snapshot.pt'
    working=out/'working.pt'
    if working.exists():
        if not frozen.exists():raise RuntimeError('BALANCE_PARENT_SNAPSHOT_MISSING')
        ck=_recover_working(out)
        if ck.get('schema')!=SCHEMA:raise RuntimeError('BALANCE_WORKING_SCHEMA_INVALID')
        source_parent=_parent(frozen)
        state=ck['state']
        if state['parent_sha256']!=fr._sha256(frozen):
            raise RuntimeError('BALANCE_RESUME_PARENT_HASH_DRIFT')
        if state['policy']!=_policy(args,source_parent):
            raise RuntimeError('BALANCE_RESUME_POLICY_DRIFT')
        if not isinstance(ck.get('optimizer'),dict):
            raise RuntimeError('BALANCE_RESUME_OPTIMIZER_MISSING')
        return state,ck,source_parent
    if any(out.iterdir()) and {x.name for x in out.iterdir()} != {'parent_snapshot.pt'}:
        raise RuntimeError('BALANCE_UNKNOWN_NONEMPTY_OUTPUT')
    _parent(source)
    if frozen.exists():
        if fr._sha256(frozen)!=fr._sha256(source):
            raise RuntimeError('BALANCE_PARENT_SOURCE_DRIFT')
    else:fr._atomic_copy(source,frozen)
    source_parent=_parent(frozen)
    state=_new_state(source_parent,fr._sha256(frozen),_policy(args,source_parent))
    return state,None,source_parent


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
    ladder._atomic_json(out/'balance_progress.json',state)


def _case_from_record(record):
    return {'id':str(record.get('case_id') or record.get('case_key')),
            'source':record['source'],'target':record['target'],
            'task':record['task'],'level':record['level'],
            'terminal_kind':record.get('terminal_kind'),
            'metadata':record.get('case_metadata',{})}


def _pinned(out,state):
    path=out/'pinned_cohort.jsonl'
    if path.exists():
        rows=ledger._read(path)
        if not rows:raise RuntimeError('BALANCE_PINNED_COHORT_EMPTY')
        return rows
    records=ledger._read(out/'glass_hard_candidates.jsonl')
    fixed=ledger._fixed_cases(state,'balance',per_cell=state['policy']['anchors_per_cell'])
    cohort={}
    for item in records:
        row=_case_from_record(item)
        cohort.setdefault(ledger._key(row),row)
    for row in fixed:cohort.setdefault(ledger._key(row),row)
    if not cohort:raise RuntimeError('BALANCE_NO_BASELINE_CASES')
    rows=list(cohort.values())
    for row in rows:fr._append(path,row)
    return rows


def _score(cohort,observations):
    keys=[ledger._key(row) for row in cohort]
    missing=[k for k in keys if k not in observations]
    if missing:raise RuntimeError('BALANCE_MISSING_IMMUTABLE_COHORT_OBSERVATIONS')
    return sum(bool(observations[k]) for k in keys),len(keys)


def _balance_status(hist,window,patience,min_steps,phase_step,check_count,expansion_every):
    # All counts are wrong->right and right->wrong on the same problem.
    paired=[h['paired'] for h in hist if h['paired'].get('paired_tested',0)>0]
    newest=paired[-window:]
    gains=sum(x['gained'] for x in newest)
    losses=sum(x['lost'] for x in newest)
    # A zero-loss-plateau *alone* is not evidence of accuracy balance.
    # Require both exploration into genuinely higher levels and a sustained
    # absence of positive acquisition over the recently paired observations.
    sufficient=(phase_step>=min_steps and len(newest)==window and
                check_count>=2*expansion_every+window and
                len([x for x in paired[-patience:] if x['net']<=0])>=patience)
    return {'window':window,'checks':len(newest),'gained':gains,'lost':losses,
            'net':gains-losses,'paired_tested':sum(x['paired_tested'] for x in newest),
            'balanced':bool(sufficient and gains<=losses),
            'exploration_checks':check_count,'min_exploration_checks':2*expansion_every+window}


def _bank(state,out):
    """Refresh from durable greedy observations only at completed checks."""
    history=ledger._history(out/'competency_checks.jsonl',state['step'],state['parent_sha256'])
    observations=history[-1]['observations'] if history else {}
    cases={}
    for path in (out/'glass_hard_candidates.jsonl',out/'glass_replay_candidates.jsonl'):
        for row in ledger._read(path):cases.setdefault(row['case_key'],_case_from_record(row))
    return (cases,
            sorted(k for k in cases if observations.get(k) is False),
            sorted(k for k in cases if k not in observations),
            sorted(k for k in cases if observations.get(k) is True))


def _batch(state,bank,ordinal):
    """Two observed errors, one mastered replay, one expanding frontier case."""
    policy=state['policy']
    cases,wrong,uncertain,correct=bank
    selected=[]
    for j in range(policy['batch_size']):
        if j%4 in (0,1) and (wrong or uncertain):
            pool=wrong if wrong else uncertain
            selected.append(cases[pool[(ordinal*2+j)%len(pool)]])
        elif j%4==2 and correct:
            selected.append(cases[correct[(ordinal+j)%len(correct)]])
        else:
            cells=tasks.task_order(policy['source_levels'])
            task,level=cells[(ordinal*policy['batch_size']+j)%len(cells)]
            extra=min(policy['max_extra_levels'],state['check_count']//policy['expansion_every'])
            if extra and (ordinal+j)%2==0:level+=extra
            seed=policy['seed']+144013
            band=ledger.BANDS[(ordinal+j)%len(ledger.BANDS)][0] if task in ledger.RADIAL else 'native'
            row=ledger._make(state,task,level,92_000_000+97*(ordinal*policy['batch_size']+j)+13,
                             seed,band)
            selected.append(row)
    return selected


def _finalize(out,state):
    best=out/'best.pt'
    if not best.is_file():raise RuntimeError('BALANCE_MISSING_BEST')
    ck=_load(best)
    if (ck.get('schema')!=SCHEMA or ck['state']['best_step']!=ck['state']['step'] or
        ck['state']['parent_sha256']!=state['parent_sha256']):
        raise RuntimeError('BALANCE_BEST_CHECKPOINT_LINEAGE_INVALID')
    fr._atomic_copy(best,out/'glass_ready.pt')
    ladder._atomic_json(out/'glass_readiness.json',{
        'schema':SCHEMA,'status':'RAW_ACCURACY_BALANCED',
        'glass_ready_checkpoint':'glass_ready.pt',
        'parent_sha256':state['parent_sha256'],
        'best_step':state['best_step'],'best_cohort_correct':state['best_cohort_correct'],
        'best_cohort_total':state['best_cohort_total'],
        'last_accuracy_balance':state['last_check'],
        'hard_archive':'glass_hard_candidates.jsonl',
        'not_a_complete_accuracy_certificate':True})
    fr._event('QWEN_BALANCE_GLASS_READY',{'checkpoint':str(out/'glass_ready.pt'),
        'best_step':state['best_step'],'best_cohort_correct':state['best_cohort_correct'],
        'best_cohort_total':state['best_cohort_total']})


def run(args,samples,out):
    import torch
    from tools import nanojev_qwen_zero_line_backbone as zero
    tasks.terminal_preflight()
    state,saved,parent=_prepare(args,samples,out)
    if state['status']=='BALANCED':
        _finalize(out,state)
        fr._event('QWEN_BALANCE_DONE',state['last_check']);return state
    if state['status'] in ('PAUSED','BUDGET_LIMIT'):state['status']='TRAINING'
    bb,tok,sources,frozen,dirs,zero=ladder._fresh_parent(args)
    ladder._outputs_safe(out,dirs)
    if (sources!=state['seed_state']['source_sha256'] or
        frozen!=state['seed_state']['frozen_sha256']):
        raise RuntimeError('BALANCE_BACKBONE_LINEAGE_DRIFT')
    zero.restore_lora(bb,saved['lora'] if saved else parent['lora'])
    ps=[p for _,p in zero.lora_named_parameters(bb)]
    if not ps:raise RuntimeError('BALANCE_MISSING_TRAINABLE_LORA')
    for p in ps:p.requires_grad_(True)
    opt=torch.optim.AdamW(ps,lr=args.balance_lr,weight_decay=0.)
    old=saved if saved else parent
    opt.load_state_dict(old['optimizer'])
    for group in opt.param_groups:group['lr']=args.balance_lr
    for slot in opt.state.values():
        for k,v in slot.items():
            if torch.is_tensor(v):slot[k]=v.to(args.device)
    fr._restore_rng(old)
    del old,parent,saved
    if not (out/'working.pt').exists():_save(out,bb,zero,opt,state,best=True)
    ledger._archive_import(out,samples,'balance',state['parent_sha256'])
    cohort=_pinned(out,state)
    ids=[ledger._key(x) for x in cohort]
    if state['cohort_keys'] and state['cohort_keys']!=ids:
        raise RuntimeError('BALANCE_COHORT_CHANGED_ON_RESUME')
    state['cohort_keys']=ids
    fr._event('QWEN_BALANCE_START',{'step':state['step'],'phase_step':state['phase_step'],
        'pinned_cohort':len(cohort),'archived_cases':len(ledger._read(out/'glass_hard_candidates.jsonl')),
        'parent_sha256':state['parent_sha256']})
    last_save=time.monotonic()
    bank=_bank(state,out)

    def save_now(*,best=False):
        nonlocal last_save
        _save(out,bb,zero,opt,state,best=best)
        last_save=time.monotonic()

    def check():
        nonlocal bank
        bb.eval()
        extra=min(state['policy']['max_extra_levels'],
                  state['check_count']//state['policy']['expansion_every'])
        summary=ledger.observe(bb=bb,tok=tok,state=state,args=args,out=out,
            phase='balance',previous=samples,
            per_cell=state['policy']['anchors_per_cell'],
            new_per_check=state['policy']['fresh_per_check'],
            replay_per_check=state['policy']['replay_per_check'],
            pinned_cases=cohort,frontier_extra_levels=extra)
        observations=ledger._history(out/'competency_checks.jsonl',state['step'],
                                     state['parent_sha256'])[-1]['observations']
        scored,total=_score(cohort,observations)
        improved=(state['best_cohort_correct'] is None or scored>state['best_cohort_correct'])
        if improved:
            state['best_cohort_correct']=scored;state['best_cohort_total']=total
            state['best_step']=state['step'];state['best_phase_step']=state['phase_step']
        # For balance, use fixed-and-paired error transitions; don't mistake
        # a shrinking/expanding probe sample for actual newly learned skills.
        history=ledger._history(out/'competency_checks.jsonl',state['step'],
                                state['parent_sha256'])
        status=_balance_status(history,state['policy']['window'],state['policy']['patience'],
                  state['policy']['min_steps'],state['phase_step'],len(history),
                  state['policy']['expansion_every'])
        status.update({'step':state['step'],'phase_step':state['phase_step'],
            'best_step':state['best_step'],'cohort_correct':scored,
            'cohort_total':total,'cohort_gain_over_initial':scored-
            (state.get('initial_cohort_correct') if state.get('initial_cohort_correct') is not None else scored),
            'new_frontier_level':extra,'paired':summary['paired'],
            'new_candidates':summary['wrong_current'],
            'archive_size':summary['hard_archive_size']})
        if state.get('initial_cohort_correct') is None:
            state['initial_cohort_correct']=scored
        state['check_count']=len(history)
        state['last_check']=status
        state['pending_check']=False
        if status['balanced']:state['status']='BALANCED'
        save_now(best=improved)
        fr._event('QWEN_BALANCE_CHECK',status)
        if state['status']=='BALANCED':_finalize(out,state)
        bank=_bank(state,out)

    with fr._stop_requests() as stop:
        if state['pending_check']:
            if not stop['requested']:check()
        while state['status']!='BALANCED':
            if stop['requested']:
                state['status']='PAUSED';save_now()
                fr._event('QWEN_BALANCE_STOP',{'reason':'signal','step':state['step']})
                return state
            if (state['policy']['max_steps'] and
                state['phase_step']>=state['policy']['max_steps']):
                state['status']='BUDGET_LIMIT';save_now()
                fr._event('QWEN_BALANCE_STOP',{'reason':'budget_not_certified','step':state['step']})
                return state
            rows=_batch(state,bank,state['phase_step'])
            bb.train();opt.zero_grad(set_to_none=True)
            amp=zero.resolve_amp_dtype(args.device,'auto') if args.device=='cuda' else 'cpu'
            with torch.autocast('cuda' if args.device=='cuda' else 'cpu',
                enabled=args.device=='cuda',
                dtype=torch.bfloat16 if amp=='bfloat16' else torch.float16):
                loss=ladder._batch_loss(bb,tok,rows,args.device,args.max_seq)
            if not bool(torch.isfinite(loss)):
                raise RuntimeError('BALANCE_NONFINITE_LOSS')
            loss.backward()
            if any(p.grad is None or not bool(torch.isfinite(p.grad).all()) for p in ps):
                raise RuntimeError('BALANCE_NONFINITE_GRADIENT')
            torch.nn.utils.clip_grad_norm_(ps,1.)
            opt.step()
            state['phase_step']+=1;state['step']+=1
            if state['phase_step']%20==0 or state['phase_step']==1:
                fr._event('QWEN_BALANCE_TRAIN',{'step':state['step'],
                    'phase_step':state['phase_step'],
                    'sampled_cells':[f"{r['task']}:L{r['level']}" for r in rows],
                    'loss':round(float(loss.detach()),8)})
            if state['phase_step']%state['policy']['check_every']==0:
                state['pending_check']=True;save_now()
                if not stop['requested']:check()
            elif state['phase_step']%10==0 or (time.monotonic()-last_save)>=60:
                save_now()
    fr._event('QWEN_BALANCE_DONE',state['last_check'])
    return state


def main(args,samples,out):
    if args.stage=='run':return run(args,samples,out)
    if args.stage not in ('status','preview'):
        raise ValueError('BALANCE_STAGE_RUN_STATUS_PREVIEW_ONLY')
    working=out/'working.pt'
    if args.stage=='preview':
        result={'phase':'balance','parent':str(samples/'glass_ready.pt'),
                'output':str(out),'checkpoint':str(working)}
        fr._event('QWEN_BALANCE_PREVIEW',result);return result
    if not working.is_file():raise FileNotFoundError(working)
    state=_load(working)['state']
    fr._event('QWEN_BALANCE_STATUS',state)
    return state
