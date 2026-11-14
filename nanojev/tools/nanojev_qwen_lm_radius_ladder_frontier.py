#!/usr/bin/env python3
"""Independent mathematical frontier LoRA acquisition cutover.

No original ladder edits, no exact-memorization advancement, no glass dependency.
Continues from a frozen copy of the *current numeric-terminal* ladder working.pt.
Train a task until an independently measured reference loss stops improving;
keep sampling new tasks near the known magnitude frontier, replay older branches,
and append observed greedy failures DURING training.
"""
from __future__ import annotations

import argparse
import collections
from contextlib import contextmanager
import signal
import threading
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shutil
import time

from tools import nanojev_qwen_lm_radius_ladder as ladder
from tools import nanojev_qwen_lm_frontier_tasks as tasks

SCHEMA='nanojev.qwen.lm-mathematical-frontier.v1'


def _sha256(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def _event(name, value):
    print(name+'='+json.dumps(value,sort_keys=True),flush=True)


def _append(path,entry):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with open(path,'a',encoding='utf8') as stream:
        stream.write(json.dumps(entry,sort_keys=True,ensure_ascii=False)+'\n')
        stream.flush()
        os.fsync(stream.fileno())


def _atomic_copy(source,dest):
    dest=Path(dest);dest.parent.mkdir(parents=True,exist_ok=True)
    tmp=dest.with_name(dest.name+'.tmp')
    try:
        with open(source,'rb') as src,open(tmp,'wb') as dst:
            shutil.copyfileobj(src,dst,1024*1024)
            dst.flush();os.fsync(dst.fileno())
        os.replace(tmp,dest)
    finally:tmp.unlink(missing_ok=True)


def _legacy_seed_radius(seed_file):
    """Read only the immutable seed: its R was a signed-result bound, not an operand bound.

    Do not infer a numeric radius from radius_index alone. Older ladders could
    have any radii schedule, and the frontier working state stores only the index.
    """
    source=Path(seed_file)
    if not source.is_file():return None
    import torch
    ck=torch.load(source,map_location='cpu',weights_only=True)
    if ck.get('schema')!=ladder.SCHEMA:return None
    cfg=ck.get('state',{}).get('config',{})
    radii=cfg.get('radii',[])
    idx=ck.get('state',{}).get('radius_index')
    if not isinstance(idx,int) or idx<0 or idx>=len(radii):return None
    return int(radii[idx])


def _radius_info(state, *, phase, seed_radius=None):
    """Truthful coverage telemetry; does not alter or augment checkpoint policy.

    The old ladder radius bounds |sum(leaves)|. This frontier has *no* mutable
    radius variable; it uses level-scaled input anchors in two generators.
    """
    policy=state.get('policy')
    if not isinstance(policy,dict) or 'operand_boundary' not in policy:
        return {'phase':phase,'status':state.get('status'),'step':state.get('step'),
                'coverage_known':False, 'legacy_seed_signed_result_radius':seed_radius,
                'reason':'checkpoint_policy_unavailable'}
    levels=int(policy.get('levels',policy.get('source_levels',0)))
    boundary=int(policy['operand_boundary'])
    task_index=int(state.get('task_index',0))
    cells=tasks.task_order(levels)
    active=(cells[task_index] if phase=='systematic' and task_index<len(cells) else None)
    anchor=boundary*(2**active[1]) if active is not None else None
    return {
        'phase':phase,'status':state.get('status'),'step':state.get('step'),
        'coverage_known':True,
        'task_index':task_index if phase=='systematic' else None,
        'active_task':active[0] if active else None,
        'active_level':active[1] if active else None,
        'active_level_anchor':anchor,
        'configured_operand_boundary':boundary,
        'level_anchors':[boundary*(2**level) for level in range(levels)],
        'anchor_used_for': ['signed_arithmetic','arithmetic_operators'],
        'other_tasks_use_own_difficulty_scales':True,
        'legacy_seed_signed_result_radius':seed_radius,
        'legacy_seed_radius_index':state.get('seed_state',{}).get('radius_index'),
        'legacy_seed_rung':state.get('seed_state',{}).get('rung'),
        'active_signed_result_radius':None,
        'radius_increases_during_training':False,
        'level_expansion_only':True,
        'frontier_task_level_cells':len(cells),
        'mixed_phase_samples_all_configured_levels':phase=='samples',
        'meaning':'legacy R bounds signed result; frontier level anchors input operands. '
                  'Neither phase dynamically expands R or grows past configured levels.'}


def _policy(args):
    keys=('seed','operand_boundary','levels','batch_size','replay_per_batch',
          'lr','max_seq','max_tokens','max_new_tokens','start_rung_override',
          'check_every','loss_window','plateau_patience','plateau_delta',
          'plateau_relative_delta','min_steps_per_task',
          'reference_cases','greedy_cases')
    return {k:getattr(args,k) for k in keys}


def _validate(args):
    if (args.operand_boundary<8 or args.levels<1 or args.batch_size<2 or
        not 0<=args.replay_per_batch<args.batch_size or args.lr<=0 or
        args.max_seq<32 or args.max_tokens<args.max_seq or args.max_new_tokens<8 or
        args.check_every<1 or args.log_every<1 or args.save_every<1 or
        args.loss_window<1 or args.plateau_patience<1 or args.reference_cases<1 or
        args.greedy_cases<0 or args.plateau_delta<0 or args.plateau_relative_delta<0 or
        args.min_steps_per_task<1 or (args.max_steps_per_task and args.max_steps_per_task<args.min_steps_per_task) or
        args.start_rung_override < -1 or args.max_total_steps<0):
        raise ValueError('FRONTIER_INVALID_ARGUMENTS')
    if not args.stage1_checkpoint or not args.text_checkpoint:
        raise ValueError('FRONTIER_PARENT_CHECKPOINTS_REQUIRED')
    if not args.seed_working:
        raise ValueError('FRONTIER_SEED_WORKING_REQUIRED')
    if Path(args.seed_working).name!='working.pt':
        raise ValueError('FRONTIER_SEED_MUST_BE_WORKING_PT')
    output=Path(args.output)
    # Do not allow input run directories to be overwritten, even indirectly.
    ladder._outputs_safe(output,[Path(args.seed_working).parent,
                                  Path(args.stage1_checkpoint).parent,
                                  Path(args.text_checkpoint).parent])
    return _policy(args)


def _seed_meta(ck):
    if ck.get('schema')!=ladder.SCHEMA:
        raise RuntimeError('FRONTIER_SEED_NOT_RADIUS_LADDER_CHECKPOINT')
    state=ck.get('state');cfg=state.get('config') if isinstance(state,dict) else None
    if not isinstance(cfg,dict) or cfg.get('terminal_mode')!='numeric':
        raise RuntimeError('FRONTIER_SEED_NOT_NUMERIC_TERMINAL')
    if state.get('config_hash')!=ladder._hash(cfg):
        raise RuntimeError('FRONTIER_SEED_CONFIG_DRIFT')
    if not isinstance(ck.get('lora'),dict) or not ck['lora']:
        raise RuntimeError('FRONTIER_SEED_MISSING_LORA')
    return state


def _make_state(policy, meta, src_hash):
    return {'schema':SCHEMA,'status':'TRAINING','policy':policy,
            'seed_sha256':src_hash,'seed_state':{
                'rung':meta['rung'],'radius_index':meta['radius_index'],
                'config_hash':meta['config_hash'],
                'source_sha256':meta['source_sha256'],
                'frozen_sha256':meta['frozen_sha256']},
            'task_index':0,'task_step':0,'step':0,'train_next_index':0,
            'check_count':0,'best_reference_loss':None,'recent_loss':[],
            'patience':0,'task_results':[], 'errors_seen':0,
            'pending_check':False,'plateau_logic_version':2,
            'best_raw_reference_loss':None,'last_snapshot_step':0}


def _policies_compatible(old,new):
    # Old frontier checkpoints included these two logging/saving knobs in the
    # pinned training policy.  They are operational settings, not training data.
    ignored={'save_every','log_every','save_seconds'}
    return ({k:v for k,v in old.items() if k not in ignored}==
            {k:v for k,v in new.items() if k not in ignored})


def _load_working(out):
    import torch
    working=out/'working.pt';previous=out/'previous.pt'
    try:
        return torch.load(working,map_location='cpu',weights_only=True)
    except Exception as error:
        if not previous.is_file():
            raise RuntimeError('FRONTIER_WORKING_SNAPSHOT_UNREADABLE_NO_BACKUP') from error
        try:
            recovered=torch.load(previous,map_location='cpu',weights_only=True)
            if recovered.get('schema')!=SCHEMA or not isinstance(recovered.get('state'),dict):
                raise RuntimeError('FRONTIER_BACKUP_SCHEMA_INVALID')
        except Exception as backup_error:
            raise RuntimeError('FRONTIER_BOTH_SNAPSHOTS_UNREADABLE') from backup_error
        # Preserve the damaged file for diagnosis rather than overwriting it.
        damaged=out/'working.corrupt.pt'
        if damaged.exists():raise RuntimeError('FRONTIER_CORRUPT_FILE_ALREADY_PRESENT')
        os.replace(working,damaged)
        _atomic_copy(previous,working)
        _event('QWEN_FRONTIER_SNAPSHOT_RECOVERED',
               {'step':recovered['state'].get('step'),'from':'previous.pt'})
        return recovered


@contextmanager
def _stop_requests():
    """Defer Ctrl+C/termination until a safe optimizer-step checkpoint."""
    stop={'requested':False,'signal':None}
    originals={}
    if threading.current_thread() is threading.main_thread():
        def handler(signum,_frame):
            stop['requested']=True
            stop['signal']=signal.Signals(signum).name
        for sig in (signal.SIGINT,signal.SIGTERM,*([signal.SIGBREAK] if hasattr(signal,'SIGBREAK') else [])):
            originals[sig]=signal.getsignal(sig)
            signal.signal(sig,handler)
    try:
        yield stop
    finally:
        for sig,fn in originals.items():signal.signal(sig,fn)


def _logged_failure_keys(path,step):
    # Used only to reconcile a checkpoint interrupted DURING a greedy check.
    # The log is append-only; replaying its incomplete check must not duplicate
    # errors or inflate the cumulative counter.
    seen=set()
    if path.exists():
        with open(path,encoding='utf8') as stream:
            for line in stream:
                try:entry=json.loads(line)
                except json.JSONDecodeError:continue
                if entry.get('step')==step:
                    seen.add(entry.get('case_id'))
    return seen


def _prepare(args):
    """Seed snapshot is never recopied on restart. Load BEFORE expensive Qwen."""
    import torch
    out=Path(args.output)
    policy=_validate(args)
    work=out/'working.pt';seed_path=out/'seed_snapshot.pt'
    if work.exists():
        if not seed_path.exists():raise RuntimeError('FRONTIER_SEED_SNAPSHOT_MISSING')
        saved=_load_working(out)
        state=saved.get('state',{})
        if saved.get('schema')!=SCHEMA or not _policies_compatible(state.get('policy',{}),policy):
            raise RuntimeError('FRONTIER_RESUME_POLICY_DRIFT')
        if _sha256(seed_path)!=state['seed_sha256']:
            raise RuntimeError('FRONTIER_SEED_SNAPSHOT_DRIFT')
        seed=torch.load(seed_path,map_location='cpu',weights_only=True)
        meta=_seed_meta(seed)
        if state['seed_state']['config_hash']!=meta['config_hash']:
            raise RuntimeError('FRONTIER_SEED_LINEAGE_DRIFT')
        state['policy']=policy
        state.setdefault('pending_check',False)
        state.setdefault('best_raw_reference_loss',None)
        if state.get('plateau_logic_version',1)<2:
            # Old threshold could accumulate spurious patience at tiny losses.
            state['patience']=0
            state['plateau_logic_version']=2
        state.setdefault('last_snapshot_step',state.get('step',0))
        _event('QWEN_FRONTIER_RESUME',{'step':state['step'], 'task_index':state['task_index'],
            'pending_check':state['pending_check']})
        return state, saved, seed
    if out.exists() and any(out.iterdir()):
        # A crash after snapshotting but before writing the initial working.pt
        # must not strand the cutover or copy a newer live ladder checkpoint.
        if {p.name for p in out.iterdir()}=={'seed_snapshot.pt'}:
            checksum=_sha256(seed_path)
            seed=torch.load(seed_path,map_location='cpu',weights_only=True)
            meta=_seed_meta(seed)
            state=_make_state(policy,meta,checksum)
            _event('QWEN_FRONTIER_SEED_RECOVERY',{'sha256':checksum})
            return state,None,seed
        raise RuntimeError('FRONTIER_REFUSES_NONEMPTY_UNRECOGNIZED_OUTPUT')
    if not Path(args.seed_working).is_file():raise FileNotFoundError(args.seed_working)
    out.mkdir(parents=True,exist_ok=True)
    # The ladder writer uses os.replace: an opened source file provides a
    # complete previous-or-next checkpoint, never an in-progress torch.save.
    _atomic_copy(args.seed_working,seed_path)
    checksum=_sha256(seed_path)
    seed=torch.load(seed_path,map_location='cpu',weights_only=True)
    meta=_seed_meta(seed)
    state=_make_state(policy,meta,checksum)
    _event('QWEN_FRONTIER_SEED',{'sha256':checksum,
           'current_rung':meta['rung'],'radius_index':meta['radius_index'],
           'operand_boundary':args.operand_boundary})
    return state,None,seed


def _durable_torch(path,obj):
    import torch
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp')
    try:
        with open(tmp,'wb') as stream:
            torch.save(obj,stream)
            stream.flush();os.fsync(stream.fileno())
        os.replace(tmp,path)
    finally:tmp.unlink(missing_ok=True)


def _save(out, bb, zero, opt, state, *, final=False):
    import torch
    state['last_snapshot_step']=state['step']
    obj={'schema':SCHEMA,'state':state,'lora':zero.lora_state(bb),
         'optimizer':opt.state_dict(),
         'python_rng':random.getstate(),
         'torch_rng':torch.get_rng_state(),
         'cuda_rng':torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None}
    working=out/'working.pt'
    # Two generations survive abrupt interruption of a write, disk corruption,
    # or loss of the just-written checkpoint. Never mutate the live seed.
    if working.exists():_atomic_copy(working,out/'previous.pt')
    _durable_torch(working,obj)
    ladder._atomic_json(out/'frontier_progress.json',state)
    if final:_durable_torch(out/'final.pt',obj)


def _restore_rng(saved):
    import torch
    if saved.get('python_rng') is not None:random.setstate(saved['python_rng'])
    if saved.get('torch_rng') is not None:torch.set_rng_state(saved['torch_rng'])
    if saved.get('cuda_rng') is not None and torch.cuda.is_available():
        torch.cuda.set_rng_state_all(saved['cuda_rng'])


def _make(task,level,index,args,start_rung):
    return tasks.make_case(task,level,index,seed=args.seed,
        operand_boundary=args.operand_boundary,start_rung=start_rung)


def _training_batch(state,args,order,start_rung):
    task,level=order[state['task_index']]
    local=args.batch_size-args.replay_per_batch
    rows=[]
    for n in range(local):
        idx=state['train_next_index']+n
        rows.append(_make(task,level,idx+200000,args,start_rung))
    # Replay previously visited *different* competencies, not perfect-memory
    # gates. No DEV holdout or fixed finite acquisition corpus.
    for n in range(args.replay_per_batch):
        earlier=state['task_index']
        if earlier>0:
            prev=(state['step']+n)%earlier
            replay_task,replay_level=order[prev]
        else:replay_task,replay_level=task,level
        rows.append(_make(replay_task,replay_level,
                    100000+(state['step']*3+n)%100000,args,start_rung))
    state['train_next_index']+=local
    return rows


def _reference(task,level,args,start_rung):
    # Fixed diagnostic sample. It is NOT a test split nor a mastery certificate.
    return [_make(task,level,900000+i,args,start_rung)
            for i in range(args.reference_cases)]


def _reference_loss(bb,tok,rows,args):
    import torch
    bb.eval()
    losses=[]
    with torch.no_grad():
        for i in range(0,len(rows),args.batch_size):
            part=rows[i:i+args.batch_size]
            loss=ladder._batch_loss(bb,tok,part,args.device,args.max_seq)
            if not bool(torch.isfinite(loss)):raise RuntimeError('FRONTIER_NONFINITE_REFERENCE_LOSS')
            losses.append((float(loss),len(part)))
    return sum(v*n for v,n in losses)/len(rows)


def _greedy_one(bb,tok,row,args):
    """Unassisted LM-head greedy next packet, using decoder KV reuse."""
    import torch
    prefix=tok(row['source']+'\n',add_special_tokens=False)['input_ids']
    if not prefix or len(prefix)+args.max_new_tokens>args.max_tokens:
        raise RuntimeError('FRONTIER_GREEDY_CONTEXT_LIMIT:'+row['id'])
    decoder=bb.get_decoder();head=bb.get_output_embeddings()
    eos=tok.eos_token_id
    eos_set={int(eos)} if isinstance(eos,int) else set(eos or ())
    ids=list(prefix);generated=[];past=None
    with torch.inference_mode():
        for step in range(args.max_new_tokens):
            x=torch.tensor([ids[-1:] if past is not None else ids],device=args.device)
            mask=torch.ones((1,len(ids)),device=args.device,dtype=torch.long)
            result=decoder(input_ids=x,attention_mask=mask,
                           past_key_values=past,use_cache=True,return_dict=True)
            past=getattr(result,'past_key_values',None)
            hidden=result.last_hidden_state[:,-1,:]
            weight=getattr(head,'weight',None)
            logits=head(hidden.to(dtype=weight.dtype if weight is not None else hidden.dtype))
            token=int(logits.argmax(dim=-1).item())
            if token in eos_set:break
            generated.append(token);ids.append(token)
            if '\n' in tok.decode(generated,skip_special_tokens=True) or '\r' in tok.decode(generated,skip_special_tokens=True):break
    return tok.decode(generated,skip_special_tokens=True)


def _lora_hash(bb):
    import torch
    digest=hashlib.sha256()
    for name,param in sorted(bb.named_parameters()):
        if not name.endswith(('.lora_a','.lora_b')):continue
        digest.update(name.encode('utf8'))
        raw=param.detach().contiguous().cpu().reshape(-1).view(torch.uint8)
        digest.update(raw.numpy().tobytes())
    return digest.hexdigest()


def _greedy_observations(bb,tok,task,level,state,args,start_rung,out,*,reconcile=False):
    """Bounded, interleaved with training; no upfront defect census."""
    if args.greedy_cases==0:return {'tested':0,'errors':0}
    errors=0
    old_keys=(_logged_failure_keys(out/'discovered_errors.jsonl',state['step'])
              if reconcile else set())
    lora_digest=_lora_hash(bb)
    for i in range(args.greedy_cases):
        case=_make(task,level,700000+state['check_count']*args.greedy_cases+i,args,start_rung)
        got=_greedy_one(bb,tok,case,args)
        ok,parsed=ladder.correctness(case['source'],case['target'],got)
        if not ok:
            errors+=1
            if case['id'] in old_keys:
                state['errors_seen']+=1
                continue
            state['errors_seen']+=1
            event={'schema':SCHEMA,'task':task,'level':level,'step':state['step'],
                  'case_id':case['id'],'source':case['source'],'target':case['target'],
                  'greedy':got,'parsed':parsed,'failure_type':'incorrect_greedy_transformation',
                  'terminal_kind':case['terminal_kind'],'case_metadata':case['metadata'],
                  'seed_sha256':state['seed_sha256'],
                  'observed_lora_sha256':lora_digest,
                  'checkpoint_reference':'in_memory_training_step_'+str(state['step']),
                  'replay_seed':args.seed}
            _append(out/'discovered_errors.jsonl',event)
    return {'tested':args.greedy_cases,'errors':errors}


def _plateau_update(state,value,args):
    """Independent deterministic reference, smooth recent checks; no accuracy gate."""
    window=collections.deque(state['recent_loss'],maxlen=args.loss_window)
    window.append(value);state['recent_loss']=list(window)
    smoothed=sum(window)/len(window)
    prior=state['best_reference_loss']
    # Either significant *absolute* improvement OR proportional improvement
    # must reset patience. A fixed delta of 0.002 must not mask an 80% drop
    # from 0.001 to 0.0002.
    significance=min(args.plateau_delta,abs(prior or 0)*args.plateau_relative_delta)
    improved=prior is None or smoothed < prior-significance
    if improved:
        state['best_reference_loss']=smoothed;state['patience']=0
    else:state['patience']+=1
    saturated=(state['task_step']>=args.min_steps_per_task and
               len(window)>=args.loss_window and
               state['patience']>=args.plateau_patience)
    return {'smoothed':smoothed,'best':state['best_reference_loss'],
            'improved':improved,'plateau':saturated}


def _advance(state,order,reason,reference_loss,out):
    task,level=order[state['task_index']]
    record={'task':task,'level':level,'steps':state['task_step'],
            'reason':reason,'best_reference_loss':state['best_reference_loss'],
            'last_reference_loss':reference_loss,'global_step':state['step'],
            'no_exact_memorization_claim':True}
    state['task_results'].append(record)
    # On a crash after append but before the next working snapshot, retrying
    # the same check cannot create another event for the same advancement.
    hist=out/'frontier_history.jsonl'
    existing=False
    if hist.exists():
        for line in hist.read_text(encoding='utf8').splitlines():
            try:e=json.loads(line)
            except json.JSONDecodeError:continue
            if e.get('event')=='task_advanced' and e.get('global_step')==state['step']:
                existing=True;break
    if not existing:_append(hist,{'event':'task_advanced',**record})
    state['task_index']+=1;state['task_step']=0;state['train_next_index']=0
    state['patience']=0;state['best_reference_loss']=None
    state['best_raw_reference_loss']=None
    state['recent_loss']=[];state['check_count']=0;state['pending_check']=False
    state['status']='COMPLETE' if state['task_index']==len(order) else 'TRAINING'
    _event('QWEN_FRONTIER_ADVANCE',record)


def run(args):
    import torch
    from tools import nanojev_qwen_zero_line_backbone as zero
    policy=_validate(args)
    tasks.terminal_preflight()
    order=tasks.task_order(args.levels)
    out=Path(args.output)
    state,saved,seed=_prepare(args)
    if state['status'] in ('BUDGET_LIMIT','TASK_BUDGET_LIMIT','PAUSED'):state['status']='TRAINING'
    if state['status']=='COMPLETE':
        _event('QWEN_FRONTIER_DONE',{'step':state['step'],'status':state['status']});return state
    # One-time dependency checks AFTER local snapshot/seed validation.
    bb,tok,sources,frozen,dirs,zero=ladder._fresh_parent(args)
    ladder._outputs_safe(out,dirs)
    if sources!=state['seed_state']['source_sha256'] or frozen!=state['seed_state']['frozen_sha256']:
        raise RuntimeError('FRONTIER_PARENT_HASH_MISMATCH')
    zero.restore_lora(bb,saved['lora'] if saved is not None else seed['lora'])
    ps=[p for _,p in zero.lora_named_parameters(bb)]
    if not ps:raise RuntimeError('FRONTIER_NO_TRAINABLE_LORA')
    for p in ps:p.requires_grad_(True)
    opt=torch.optim.AdamW(ps,lr=args.lr,weight_decay=0.)
    if saved is not None:
        opt.load_state_dict(saved['optimizer'])
        # Optimizer state can be on CPU when restored; move to parameter device.
        for entry in opt.state.values():
            for name,val in entry.items():
                if torch.is_tensor(val):entry[name]=val.to(args.device)
        _restore_rng(saved)
    else:
        random.seed(args.seed);torch.manual_seed(args.seed)
        if torch.cuda.is_available():torch.cuda.manual_seed_all(args.seed)
        _save(out,bb,zero,opt,state)
    start_rung=(args.start_rung_override if args.start_rung_override>=0
                else max(0,int(state['seed_state']['rung'])))
    radius_info=_radius_info(state,phase='systematic',
            seed_radius=_legacy_seed_radius(out/'seed_snapshot.pt'))
    # Release loaded source optimizer/certificate state after the initial
    # LoRA/optimizer import: the new campaign no longer needs it in memory.
    del seed, saved
    _event('QWEN_FRONTIER_START',{'step':state['step'],'task_index':state['task_index'],
        'tasks':len(order),'seed_sha256':state['seed_sha256'],
        'first_operand_boundary':args.operand_boundary,'start_rung':start_rung,
        'exact_advancement':False,'glass_training':False})
    _event('QWEN_FRONTIER_RADIUS',radius_info)
    last_saved_at=time.monotonic()

    def save_now(*,final=False):
        nonlocal last_saved_at
        _save(out,bb,zero,opt,state,final=final)
        last_saved_at=time.monotonic()

    def finish_check(reconcile=False):
        """A due check is restartable from its *pre-evaluation* snapshot."""
        task,level=order[state['task_index']]
        ref=_reference(task,level,args,start_rung)
        value=_reference_loss(bb,tok,ref,args)
        # Do not advance the case generator cursor until the evaluation is
        # complete, so an interrupted check reruns exactly the same cases.
        observed=_greedy_observations(bb,tok,task,level,state,args,start_rung,out,
                                     reconcile=reconcile)
        from tools import nanojev_qwen_lm_competency_ledger as competence
        net=competence.observe(bb=bb,tok=tok,state=state,args=args,out=out,
            phase='systematic',focus=(task,level),per_cell=getattr(args,'competency_anchors',1),
            new_per_check=getattr(args,'competency_fresh',18),
            replay_per_check=getattr(args,'competency_replay',24))
        state['check_count']+=1
        plateau=_plateau_update(state,value,args)
        raw_best=state.get('best_raw_reference_loss')
        if raw_best is None or value<raw_best:
            state['best_raw_reference_loss']=value
            _durable_torch(out/'task_checkpoints'/f'{task}_L{level}_best.pt',
                {'schema':SCHEMA,'lora':zero.lora_state(bb),'task':task,'level':level,
                 'step':state['step'],'reference_loss':value})
        _event('QWEN_FRONTIER_CHECK',{'task':task,'level':level,
            'level_operand_anchor':args.operand_boundary*(2**level),
            'anchor_applies_to_task':task in ('signed_arithmetic','arithmetic_operators'),
            'step':state['step'],
            'task_step':state['task_step'],'reference_loss':round(value,7),
            'smoothed_loss':round(plateau['smoothed'],7),
            'best_reference_loss':round(plateau['best'],7),
            'patience':state['patience'],'greedy':observed,
            'net_competency':net,'no_exact_certificate':True})
        state['pending_check']=False
        if plateau['plateau'] and net['focus_recent']['flat_or_negative']:
            _advance(state,order,'loss_and_net_competency_plateau',value,out)
        elif plateau['plateau']:
            _event('QWEN_FRONTIER_COMPETENCY_CONTINUES',
                {'task':task,'level':level,'step':state['step'],
                 'net_recent':net['focus_recent']})
            _event('QWEN_FRONTIER_RADIUS',_radius_info(state,phase='systematic',
                seed_radius=radius_info['legacy_seed_signed_result_radius']))
        save_now(final=state['status']=='COMPLETE')

    with _stop_requests() as stop:
        if state.get('pending_check'):
            # The prior process saved after an optimizer step but stopped while
            # checking loss/greedy. Finish that step's evaluation before training.
            if not stop['requested']:
                _event('QWEN_FRONTIER_PENDING_CHECK',{'step':state['step']})
                finish_check(reconcile=True)
        while state['task_index']<len(order):
            if stop['requested']:
                state['status']='PAUSED'
                save_now()
                _event('QWEN_FRONTIER_STOP',{'reason':'graceful_signal',
                    'signal':stop['signal'],'step':state['step'],
                    'task_index':state['task_index'],'pending_check':state['pending_check']})
                return state
            if args.max_steps_per_task and state['task_step']>=args.max_steps_per_task:
                state['status']='TASK_BUDGET_LIMIT';save_now()
                _event('QWEN_FRONTIER_STOP',{'reason':'task_step_budget_not_plateau',
                        'step':state['step'],'task':order[state['task_index']],
                        'task_step':state['task_step']})
                return state
            if args.max_total_steps and state['step']>=args.max_total_steps:
                state['status']='BUDGET_LIMIT';save_now()
                _event('QWEN_FRONTIER_STOP',{'reason':'total_budget','step':state['step']})
                return state
            task,level=order[state['task_index']]
            rows=_training_batch(state,args,order,start_rung)
            bb.eval();opt.zero_grad(set_to_none=True)
            amp=zero.resolve_amp_dtype(args.device,'auto') if str(args.device).startswith('cuda') else 'cpu'
            with torch.autocast('cuda' if str(args.device).startswith('cuda') else 'cpu',
                                enabled=str(args.device).startswith('cuda'),
                                dtype=torch.bfloat16 if amp=='bfloat16' else torch.float16):
                loss=ladder._batch_loss(bb,tok,rows,args.device,args.max_seq)
            if not bool(torch.isfinite(loss)):raise RuntimeError('FRONTIER_NONFINITE_TRAIN_LOSS')
            loss.backward()
            if any(p.grad is None or not bool(torch.isfinite(p.grad).all()) for p in ps):
                raise RuntimeError('FRONTIER_INVALID_LORA_GRADIENT')
            torch.nn.utils.clip_grad_norm_(ps,1.)
            opt.step()
            state['task_step']+=1;state['step']+=1
            if state['step']%args.log_every==0 or state['task_step']==1:
                _event('QWEN_FRONTIER_TRAIN',{'task':task,'level':level,
                      'level_operand_anchor':args.operand_boundary*(2**level),
                      'anchor_applies_to_task':task in ('signed_arithmetic','arithmetic_operators'),
                      'step':state['step'],'task_step':state['task_step'],
                      'loss':round(float(loss.detach()),7)})
            due=(state['task_step']%args.check_every==0 or
                 (args.max_steps_per_task and state['task_step']>=args.max_steps_per_task))
            if due:
                # Save the very latest model and optimizer *before* expensive
                # greedy generation. The check may be replayed after a crash.
                state['pending_check']=True
                save_now()
                if stop['requested']:continue
                finish_check()
            elif (state['step']%args.save_every==0 or stop['requested'] or
                  (args.save_seconds and time.monotonic()-last_saved_at>=args.save_seconds)):
                save_now()
        if state['status']=='COMPLETE' and not (out/'final.pt').exists():
            save_now(final=True)
    _event('QWEN_FRONTIER_DONE',{'step':state['step'],'tasks_completed':len(order),
        'errors_seen':state['errors_seen'],'status':state['status']})
    return state


def _samples_output(args):
    """Keep both phase workspaces independent even with the old CLI defaults."""
    ordered=Path(args.output).resolve()
    mixed=Path(args.samples_output).resolve() if args.samples_output else ordered.with_name(
        (ordered.name[:-3] if ordered.name.endswith('_v1') else ordered.name)+'_mixed_v1')
    # Resolve here before subprocess/model loads. Do not overwrite the ordered run.
    if ordered==mixed or ordered in mixed.parents or mixed in ordered.parents:
        raise ValueError('FRONTIER_PHASE_OUTPUTS_MUST_BE_SEPARATE')
    return ordered,mixed


def _samples_argv(args):
    """Dispatch to the exact existing mixed-pass implementation and defaults.

    Phase 1 flags remain stable and do not silently become Phase 2 training
    policy (e.g. --seed=42257 vs --samples-seed=91733). Operational sampler
    settings have their own explicit --samples-* flags.
    """
    ordered,mixed=_samples_output(args)
    argv=['--stage',args.stage,'--device',args.device,
        '--frontier-output',str(ordered),'--output',str(mixed),
        '--seed',str(args.samples_seed),'--batch-size',str(args.samples_batch_size),
        '--lr',str(args.samples_lr),'--max-seq',str(args.max_seq),
        '--max-tokens',str(args.max_tokens),'--max-new-tokens',str(args.max_new_tokens),
        '--check-every',str(args.samples_check_every),
        '--reference-per-cell',str(args.samples_reference_per_cell),
        '--greedy-cases',str(args.samples_greedy_cases),
        '--min-steps',str(args.samples_min_steps),
        '--plateau-patience',str(args.samples_plateau_patience),
        '--loss-window',str(args.samples_loss_window),
        '--plateau-delta',str(args.samples_plateau_delta),
        '--plateau-relative-delta',str(args.samples_plateau_relative_delta),
        '--save-every',str(args.samples_save_every),
        '--save-seconds',str(args.samples_save_seconds),
        '--log-every',str(args.samples_log_every),
        '--max-steps',str(args.samples_max_steps),
        '--competency-anchors',str(args.competency_anchors),
        '--competency-fresh',str(args.competency_fresh),
        '--competency-replay',str(getattr(args,'competency_replay',24))]
    for flag,value in (('stage1-checkpoint',args.stage1_checkpoint),
                        ('text-checkpoint',args.text_checkpoint),
                        ('baseline',args.baseline),('backbone',args.backbone),
                        ('activation',args.activation),('format',args.format)):
        if value is not None: argv.extend(('--'+flag,str(value)))
    if args.allow_model_download:argv.append('--allow-model-download')
    return argv


def _dispatch_samples(args):
    if args.stage=='detect':
        raise ValueError('FRONTIER_DETECT_ONLY_AVAILABLE_IN_SYSTEMATIC_PHASE')
    from tools import nanojev_qwen_lm_frontier_mixed_pass as mixed
    _event('QWEN_FRONTIER_PHASE',{
        'phase':'samples','stage':args.stage,
        'systematic_output':str(Path(args.output).resolve()),
        'samples_output':str(_samples_output(args)[1]),'glass_training':False})
    return mixed.main(_samples_argv(args))


def _dispatch_balance(args):
    if args.stage in ('detect','audit','radius'):
        raise ValueError('FRONTIER_BALANCE_STAGE_RUN_STATUS_PREVIEW_ONLY')
    from tools import nanojev_qwen_lm_frontier_accuracy_balance as balance
    _,samples=_samples_output(args)
    out=(Path(args.balance_output).resolve() if args.balance_output else
         samples.with_name((samples.name[:-3] if samples.name.endswith('_v1') else samples.name)+'_balance_v1'))
    _event('QWEN_FRONTIER_PHASE',{'phase':'balance','stage':args.stage,
        'samples_output':str(samples),'balance_output':str(out),'glass_training':False})
    return balance.main(args,samples,out)


def _dispatch_glasses(args):
    """Rank-1 Easy -> Mixed -> conditional Hard under the unified CLI.

    This is a new frontier-compatible trainer, not the historical 240-case
    numeric-terminal experiment. Phase 1 and Phase 2 implementation unchanged.
    """
    if args.stage in ('detect','radius'):
        raise ValueError('FRONTIER_GLASSES_STAGE_RUN_STATUS_PREVIEW_AUDIT_ONLY')
    from tools import nanojev_qwen_lm_frontier_rank1_glasses as glass
    ordered,samples=_samples_output(args)
    balance=(Path(args.balance_output).resolve() if args.balance_output else
             samples.with_name((samples.name[:-3] if samples.name.endswith('_v1') else samples.name)+'_balance_v1'))
    balanced=(balance/'glass_readiness.json').is_file() and (balance/'glass_ready.pt').is_file()
    if not balanced and args.stage not in ('status','preview'):
        raise RuntimeError('FRONTIER_GLASSES_REQUIRE_COMPLETED_ACCURACY_BALANCE')
    parent=balance if balanced else samples
    args.samples_output=str(parent)
    if args.glasses_output is None:
        args.glasses_output=str(parent.with_name(
            (parent.name[:-3] if parent.name.endswith('_v1') else parent.name)+'_glasses_v1'))
    _event('QWEN_FRONTIER_PHASE',{
        'phase':'glasses','scheme':args.glass_scheme,'stage':args.stage,
        'systematic_output':str(ordered),'samples_output':str(samples),'balance_output':str(balance),
        'glasses_output':str(Path(args.glasses_output).resolve())})
    return glass.main(args)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--phase',choices=('systematic','samples','balance','glasses'),default='systematic',
                   help='systematic: resume current ordered frontier (default); samples: '
                        'resume mixed-frontier loss phase; balance: accuracy acquisition/forgetting phase; glasses: A/B')
    p.add_argument('--stage',choices=('run','status','radius','preview','detect','audit'),default='run')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--stage1-checkpoint');p.add_argument('--text-checkpoint')
    p.add_argument('--seed-working',help='Current numeric-terminal radius ladder working.pt (systematic phase)')
    p.add_argument('--output',default=str(Path.home()/'NanoJev'/'runs'/'qwen_lm_frontier_v1'),
                   help='SYSTEMATIC phase output, used as parent for --phase samples')
    p.add_argument('--operand-boundary',type=int,default=1024)
    p.add_argument('--levels',type=int,default=2)
    p.add_argument('--start-rung-override',type=int,default=-1)
    p.add_argument('--batch-size',type=int,default=4)
    p.add_argument('--replay-per-batch',type=int,default=1)
    p.add_argument('--lr',type=float,default=5e-5)
    p.add_argument('--max-seq',type=int,default=192)
    p.add_argument('--max-tokens',type=int,default=256)
    p.add_argument('--max-new-tokens',type=int,default=64)
    p.add_argument('--min-steps-per-task',type=int,default=100)
    p.add_argument('--max-steps-per-task',type=int,default=0,
                   help='0: run until actual loss plateau; otherwise stop at a budget, never advance without plateau')
    p.add_argument('--check-every',type=int,default=50)
    p.add_argument('--plateau-patience',type=int,default=4)
    p.add_argument('--loss-window',type=int,default=3)
    p.add_argument('--plateau-delta',type=float,default=0.002)
    p.add_argument('--plateau-relative-delta',type=float,default=0.001)
    p.add_argument('--reference-cases',type=int,default=16)
    p.add_argument('--greedy-cases',type=int,default=4)
    # Observational only: do not fold these into the immutable training policy.
    p.add_argument('--competency-anchors',type=int,default=1)
    p.add_argument('--competency-fresh',type=int,default=18)
    p.add_argument('--competency-replay',type=int,default=24)
    p.add_argument('--save-every',type=int,default=10,
                   help='SYSTEMATIC: save full resumable snapshot every N optimizer steps')
    p.add_argument('--save-seconds',type=float,default=60.0)
    p.add_argument('--log-every',type=int,default=10)
    p.add_argument('--max-total-steps',type=int,default=0)
    p.add_argument('--seed',type=int,default=42257)
    p.add_argument('--baseline');p.add_argument('--backbone');p.add_argument('--activation');p.add_argument('--format')
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--packet')
    # Deliberately separate sampling policy from Phase 1. Phase 1 defaults and
    # its existing checkpoint compatibility must never change just to run 2.
    p.add_argument('--samples-output',help='Optional separate sampling output directory')
    p.add_argument('--samples-seed',type=int,default=91733)
    p.add_argument('--samples-batch-size',type=int,default=4)
    p.add_argument('--samples-lr',type=float,default=5e-5)
    p.add_argument('--samples-check-every',type=int,default=250)
    p.add_argument('--samples-reference-per-cell',type=int,default=16)
    p.add_argument('--samples-greedy-cases',type=int,default=6)
    p.add_argument('--samples-min-steps',type=int,default=1000)
    p.add_argument('--samples-plateau-patience',type=int,default=6)
    p.add_argument('--samples-loss-window',type=int,default=3)
    p.add_argument('--samples-plateau-delta',type=float,default=0.0005)
    p.add_argument('--samples-plateau-relative-delta',type=float,default=0.002)
    p.add_argument('--samples-save-every',type=int,default=10)
    p.add_argument('--samples-save-seconds',type=float,default=60.0)
    p.add_argument('--samples-log-every',type=int,default=20)
    p.add_argument('--samples-max-steps',type=int,default=0,
                   help='Optional budget stop for sample pass, not a fabricated plateau')
    # Accuracy balance is a distinct post-loss convergence phase. Its knobs
    # do not modify the completed mixed-loss training policy.
    p.add_argument('--balance-output',help='Independent accuracy-balance workspace')
    p.add_argument('--balance-check-every',type=int,default=250)
    p.add_argument('--balance-min-steps',type=int,default=1500)
    p.add_argument('--balance-max-steps',type=int,default=0)
    p.add_argument('--balance-patience',type=int,default=4)
    p.add_argument('--balance-window',type=int,default=4)
    p.add_argument('--balance-lr',type=float,default=3e-5)
    p.add_argument('--balance-batch-size',type=int,default=4)
    p.add_argument('--balance-anchors-per-cell',type=int,default=4)
    p.add_argument('--balance-fresh-per-check',type=int,default=36)
    p.add_argument('--balance-replay-per-check',type=int,default=128)
    p.add_argument('--balance-expansion-every',type=int,default=4,
                   help='New frontier difficulty level every N accuracy checks')
    p.add_argument('--balance-max-extra-levels',type=int,default=3)
    # Glass training is only appropriate after accuracy balance.
    p.add_argument('--glass-scheme',choices=('easy','mixed','hard'),default='easy',
                   help='Rank-1 first-layer: easy -> mixed (inherits easy) -> hard only if mixed fails')
    p.add_argument('--glasses-output',help='Rank-1 workspace; defaults to sibling of samples output')
    p.add_argument('--glass-force-hard',action='store_true',
                   help='Run hard fallback even if mixed achieved its success gate')
    p.add_argument('--glass-seed',type=int,default=48157)
    p.add_argument('--glass-train-per-cell',type=int,default=12)
    p.add_argument('--glass-validation-per-cell',type=int,default=4)
    p.add_argument('--glass-easy-threshold',type=float,default=0.75)
    p.add_argument('--glass-retention-floor',type=float,default=0.95)
    p.add_argument('--glass-width',type=int,default=192)
    p.add_argument('--glass-feature-layers',type=int,default=4)
    p.add_argument('--glass-lr',type=float,default=0.0003)
    p.add_argument('--glass-preserve-weight',type=float,default=0.1)
    p.add_argument('--glass-delta-l2',type=float,default=0.001)
    p.add_argument('--glass-gate-loss',type=float,default=0.002)
    p.add_argument('--glass-steps-per-round',type=int,default=12)
    p.add_argument('--glass-rounds',type=int,default=6)
    p.add_argument('--glass-plateau-patience',type=int,default=3)
    args=p.parse_args(argv)
    if args.phase=='samples':
        if args.stage=='audit':raise ValueError('FRONTIER_SAMPLES_AUDIT_NOT_IMPLEMENTED')
        return _dispatch_samples(args)
    if args.phase=='balance':return _dispatch_balance(args)
    if args.phase=='glasses':return _dispatch_glasses(args)
    if args.stage=='audit':raise ValueError('FRONTIER_SYSTEMATIC_AUDIT_NOT_IMPLEMENTED')
    # Existing systematic phase is intentionally unchanged: identical command
    # without --phase retains exactly the same policy and checkpoint lineage.
    if args.stage=='detect':
        if args.packet is None:raise ValueError('FRONTIER_DETECT_REQUIRES_PACKET')
        r=ladder.numerical_completion(args.packet)
        _event('QWEN_FRONTIER_DETECT',{'packet':args.packet,**r});return r
    if args.stage=='preview':
        p=_policy(args)
        r={'schema':SCHEMA,'policy':p,'tasks':tasks.task_order(args.levels),
           'terminal_preflight':tasks.terminal_preflight(),
           'examples':[tasks.make_case(t,0,0,seed=args.seed,operand_boundary=args.operand_boundary)
                     for t in tasks.TASKS], 'no_model_loaded':True,'glass_training':False}
        _event('QWEN_FRONTIER_PREVIEW',r);return r
    if args.stage in ('status','radius'):
        path=Path(args.output)
        if not (path/'working.pt').exists():raise FileNotFoundError(path/'working.pt')
        state=_load_working(path)['state']
        radius=_radius_info(state,phase='systematic',
            seed_radius=_legacy_seed_radius(path/'seed_snapshot.pt'))
        _event('QWEN_FRONTIER_RADIUS',radius)
        if args.stage=='radius':return radius
        _event('QWEN_FRONTIER_STATUS',state);return state
    return run(args)

if __name__=='__main__':main()
