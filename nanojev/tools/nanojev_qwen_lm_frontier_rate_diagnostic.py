#!/usr/bin/env python3
"""Independent, budget-matched raw-Qwen vs Glass A/B/C learning-rate diagnostic.

Runs from one copied raw checkpoint. All branches get the *same training rows*
(the archive plus fresh generated rows) and the *same fixed greedy probe deck*.
Reports paired gains, regressions, net correct, synchronized training seconds,
and the separately measured fresh vs archive cohorts. It does not alter or stop
any live Qwen training and does not certify a permanent learning-rate crossover.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import random
import time

from tools import nanojev_qwen_lm_radius_ladder_frontier as fr
from tools import nanojev_qwen_lm_frontier_accuracy_balance as balance
from tools import nanojev_qwen_lm_frontier_mixed_pass as mixed
from tools import nanojev_qwen_lm_frontier_tasks as tasks
from tools import nanojev_qwen_lm_competency_ledger as ledger
from tools import nanojev_qwen_lm_radius_ladder as ladder
from tools import nanojev_qwen_lm_frontier_rank1_glasses as glass_train
from tools import nanojev_qwen_lm_deformation_smoke as ab
from tools import nanojev_qwen_lm_frontier_local_glass as local_glass
from tools import nanojev_qwen_lm_frontier_cached_glass as cached_glass

SCHEMA = 'nanojev.qwen.frontier.rate-comparison.v2-abc'
BRANCHES = ('raw', 'glass_A', 'glass_B', 'glass_C')


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage', choices=('preview','run','report'), default='run')
    p.add_argument('--device', choices=('cuda','cpu'), default='cuda')
    p.add_argument('--stage1-checkpoint', required=True)
    p.add_argument('--text-checkpoint', required=True)
    p.add_argument('--source-checkpoint', help='Default: best.pt in the balance workspace')
    p.add_argument('--balance-output', default=str(Path.home()/'NanoJev'/'runs'/'qwen_lm_frontier_mixed_balance_v1'))
    p.add_argument('--output', default=str(Path.home()/'NanoJev'/'runs'/'qwen_lm_rate_comparison_abc_v2'))
    p.add_argument('--budget-seconds', type=float, default=120., help='Synchronized training wall seconds per branch')
    p.add_argument('--max-steps', type=int, default=0, help='Optional step ceiling, 0 = unlimited')
    p.add_argument('--check-seconds', type=float, default=40., help='Training-time interval between paired audits')
    p.add_argument('--archive-train', type=int, default=64)
    p.add_argument('--generated-train-per-cell', type=int, default=2)
    p.add_argument('--eval-per-cell', type=int, default=4)
    p.add_argument('--eval-frontier-per-cell', type=int, default=6)
    p.add_argument('--eval-archive', type=int, default=32)
    p.add_argument('--extra-levels', type=int, default=2)
    p.add_argument('--seed', type=int, default=96031)
    p.add_argument('--raw-lr', type=float, default=3e-5)
    p.add_argument('--glass-lr', type=float, default=3e-4)
    p.add_argument('--glass-width', type=int, default=192)
    p.add_argument('--glass-feature-layers', type=int, default=4)
    p.add_argument('--glass-preserve-weight', type=float, default=1.)
    p.add_argument('--glass-preserve-margin', type=float, default=1.)
    p.add_argument('--local-threshold', type=float, default=.99999)
    p.add_argument('--local-token-steps', type=int, default=32)
    p.add_argument('--local-lr', type=float, default=.1)
    p.add_argument('--local-logit-margin', type=float, default=1.)
    p.add_argument('--local-max-token-fixes', type=int, default=2)
    p.add_argument('--glass-delta-l2', type=float, default=.001)
    p.add_argument('--glass-gate-loss', type=float, default=.002)
    p.add_argument('--max-seq', type=int, default=192)
    p.add_argument('--max-tokens', type=int, default=256)
    p.add_argument('--max-new-tokens', type=int, default=64)
    p.add_argument('--allow-model-download', action='store_true')
    p.add_argument('--baseline')
    p.add_argument('--backbone')
    p.add_argument('--activation')
    p.add_argument('--format')
    return p


def _options(args):
    return {k:v for k,v in vars(args).items() if k!='stage'}


def _check_arguments(args):
    if (args.budget_seconds<=0 or args.check_seconds<=0 or args.max_steps<0 or
        args.archive_train<1 or args.generated_train_per_cell<1 or
        args.eval_per_cell<1 or args.eval_frontier_per_cell<1 or
        args.eval_archive<0 or args.extra_levels<1 or args.glass_width<1 or
        args.glass_feature_layers!=4 or args.glass_preserve_weight<0 or
        args.glass_preserve_margin<0 or not 0.<args.local_threshold<=1. or
        args.local_token_steps<1 or args.local_lr<=0 or args.local_logit_margin<0 or
        args.local_max_token_fixes<1 or args.max_seq<16 or args.max_tokens<32 or
        args.max_new_tokens<1 or args.raw_lr<=0 or args.glass_lr<=0):
        raise ValueError('RATE_INVALID_POLICY')
    source=Path(args.source_checkpoint).resolve() if args.source_checkpoint else Path(args.balance_output).resolve()/'best.pt'
    output=Path(args.output).resolve()
    protected=[source,Path(args.balance_output),Path(args.stage1_checkpoint),Path(args.text_checkpoint)]
    for path in protected:
        path=Path(path).resolve()
        if output==path or output in path.parents or path in output.parents:
            raise RuntimeError('RATE_OUTPUT_OVERLAPS_SOURCE')
    if args.stage!='report':
        for path in (source,Path(args.stage1_checkpoint),Path(args.text_checkpoint)):
            if not path.is_file():raise FileNotFoundError(path)
    return source,output


def _load_snapshot(path):
    import torch
    obj=torch.load(path,map_location='cpu',weights_only=True)
    if (obj.get('schema') not in (balance.SCHEMA,mixed.SCHEMA) or
        not isinstance(obj.get('lora'),dict) or not obj['lora'] or
        not isinstance(obj.get('state'),dict) or
        not isinstance(obj['state'].get('seed_state'),dict)):
        raise RuntimeError('RATE_SOURCE_NOT_RAW_QWEN_CHECKPOINT')
    return obj


def _fingerprint(rows):
    return hashlib.sha256(json.dumps([(r['source'],r['target']) for r in rows],sort_keys=True).encode()).hexdigest()


def _make_decks(state,args,archive):
    policy=state['policy']
    levels=policy.get('source_levels',policy.get('levels'))
    radius=policy['operand_boundary']
    rung=(policy['start_rung_override'] if policy['start_rung_override']>=0 else
          max(0,int(state['seed_state'].get('rung',0))))
    cells=tasks.task_order(levels)
    seen=set()
    train=[];evaluation=[]

    def add(target,row,stratum):
        key=row['source']+'\0'+row['target']
        if key in seen:return False
        seen.add(key)
        item={**row,'original_id':str(row.get('id') or row.get('case_id') or ''),
              'id':ledger._key(row),'stratum':stratum}
        target.append(item)
        return True

    shuffled=sorted(archive,key=lambda r:hashlib.sha256((str(args.seed)+r['source']+'\0'+r['target']).encode()).hexdigest())
    # Archived cases are intentionally nonrepresentative, so they are an
    # independent retention / remediation stratum in every report.
    for row in shuffled[:args.archive_train]:add(train,row,'archive_train')
    for row in shuffled[args.archive_train:]:
        if sum(r['stratum']=='archive_eval' for r in evaluation)>=args.eval_archive:break
        add(evaluation,row,'archive_eval')
    def generate(target,count,extra,stratum,start_index):
        for task,level in cells:
            for j in range(count):
                index=start_index+(cells.index((task,level))*count+j)*101+19
                row=tasks.make_case(task,level+extra,index,seed=args.seed+extra*173,
                                    operand_boundary=radius,start_rung=rung)
                add(target,row,stratum)
    generate(train,args.generated_train_per_cell,0,'generated_train',120_000_000)
    generate(train,args.generated_train_per_cell,args.extra_levels,'expanded_train',130_000_000)
    generate(evaluation,args.eval_per_cell,0,'fresh_original',220_000_000)
    generate(evaluation,args.eval_frontier_per_cell,args.extra_levels,'fresh_expanded',230_000_000)
    if not train or not evaluation:raise RuntimeError('RATE_EMPTY_TRAIN_OR_PROBE_DECK')
    if not any(r['stratum']=='fresh_expanded' for r in evaluation):
        raise RuntimeError('RATE_MISSING_FRESH_FRONTIER')
    return train,evaluation


def _sync(device):
    if device=='cuda':
        import torch
        torch.cuda.synchronize()


def _score(before,now,rows):
    results={}
    for tag in sorted({r['stratum'] for r in rows})+['all','fresh']:
        group=[r for r in rows if tag=='all' or
               (r['stratum'].startswith('fresh_') if tag=='fresh' else r['stratum']==tag)]
        gained=sum(not before[r['id']] and now[r['id']] for r in group)
        lost=sum(before[r['id']] and not now[r['id']] for r in group)
        results[tag]={'tested':len(group),'baseline_correct':sum(before[r['id']] for r in group),
                      'correct':sum(now[r['id']] for r in group),
                      'gained':gained,'lost':lost,'net':gained-lost}
    return results


def _with_rate(scores,seconds,steps):
    # The test cohort is fixed, and all branches are compared on its same
    # problem IDs; this is not an estimator of global mathematical entropy.
    for item in scores.values():
        item['net_per_gpu_hour_reserved']=3600*item['net']/seconds if seconds>0 else None
        item['net_per_100_steps']=100*item['net']/steps if steps>0 else None
    return scores


def _emit(name,data):
    fr._event(name,data)


def _greedy(bb,tok,rows,rollout_args,glass=None,label='',cache=None):
    out={}
    previous=ledger._read(cache) if cache is not None else []
    cached={r['id']:bool(r['correct']) for r in previous}
    allowed={r['id'] for r in rows}
    if set(cached)-allowed:
        raise RuntimeError('RATE_BASELINE_CACHE_DECK_DRIFT')
    bb.eval()
    for i,row in enumerate(rows,1):
        if row['id'] in cached:
            out[row['id']]=cached[row['id']]
        else:
            r=ab._rollout(bb,tok,row,rollout_args,glass=glass)
            out[row['id']]=bool(r['correct'])
            if cache is not None:
                fr._append(cache,{'id':row['id'],'correct':out[row['id']]})
        if i%12==0 or i==len(rows):
            _emit('QWEN_RATE_EVAL_PROGRESS',{'branch':label,'tested':i,
                  'total':len(rows),'cached':len(cached)})
    return out


def _load_data(args):
    source,out=_check_arguments(args)
    if args.stage=='report':
        path=out/'report.json'
        if not path.is_file():raise FileNotFoundError(path)
        result=json.loads(path.read_text(encoding='utf8'))
        _emit('QWEN_RATE_REPORT',result);return None,None,None,None,result
    if args.stage=='run' and (out/'manifest.json').is_file():
        manifest=json.loads((out/'manifest.json').read_text(encoding='utf8'))
        snap=out/'source_snapshot.pt'
        if (manifest.get('schema')!=SCHEMA or not snap.is_file() or
            manifest.get('source_sha256')!=fr._sha256(snap) or
            manifest.get('settings')!=_options(args)):
            raise RuntimeError('RATE_RESUME_POLICY_OR_SOURCE_DRIFT')
        ck=_load_snapshot(snap)
        train=ledger._read(out/'train_deck.jsonl')
        eval_rows=ledger._read(out/'evaluation_deck.jsonl')
        if (not train or not eval_rows or
            manifest.get('train_deck_sha256')!=_fingerprint(train) or
            manifest.get('eval_deck_sha256')!=_fingerprint(eval_rows)):
            raise RuntimeError('RATE_RESUME_DECK_DRIFT')
        return ck,train,eval_rows,manifest,None
    if args.stage=='run' and out.exists() and any(out.iterdir()):
        raise RuntimeError('RATE_UNRECOGNIZED_NONEMPTY_OUTPUT')
    ck=_load_snapshot(source)
    archive_dir=Path(args.balance_output).resolve()
    archive=[]
    for filename in ('glass_hard_candidates.jsonl','glass_replay_candidates.jsonl'):
        archive.extend(ledger._read(archive_dir/filename))
    if not archive:
        archive.extend(ledger._read(source.parent/'glass_hard_candidates.jsonl'))
    archive=[r for r in archive if r.get('task') in tasks.TASKS and
             isinstance(r.get('level'),int) and r.get('source') and r.get('target')]
    if not archive:raise RuntimeError('RATE_REQUIRES_ARCHIVED_CASES')
    train,eval_rows=_make_decks(ck['state'],args,archive)
    if args.stage=='preview':
        preview={'source_checkpoint':str(source),'output':str(out),'source_step':ck['state'].get('step'),
                 'train_cases':len(train),'evaluation_cases':len(eval_rows),
                 'eval_by_stratum':{s:sum(r['stratum']==s for r in eval_rows)
                                    for s in sorted({r['stratum'] for r in eval_rows})},
                 'time_budget_seconds_per_branch':args.budget_seconds,'branches':BRANCHES,
                 'training_starts':False,'parent_unchanged':True}
        _emit('QWEN_RATE_PREVIEW',preview)
        return None,None,None,None,preview
    out.mkdir(parents=True,exist_ok=False)
    # Copy before loading the model to make the experiment independent of a
    # concurrently advancing Accuracy Balance phase.
    fr._atomic_copy(source,out/'source_snapshot.pt')
    manifest={'schema':SCHEMA,'source_path':str(source),'source_sha256':fr._sha256(out/'source_snapshot.pt'),
              'source_step':ck['state'].get('step'),'settings':_options(args),
              'train_deck_sha256':_fingerprint(train),'eval_deck_sha256':_fingerprint(eval_rows),
              'not_a_population_accuracy_estimate':True,
              'read_only_source':True,'exclusive_gpu_required_for_meaningful_timing':True}
    ladder._atomic_json(out/'manifest.json',manifest)
    for name,items in [('train_deck.jsonl',train),('evaluation_deck.jsonl',eval_rows)]:
        for row in items:fr._append(out/name,row)
    return ck,train,eval_rows,manifest,None


def _training_pairs(train,baseline,args):
    # Exactly the same deterministic sequence is presented to every branch,
    # irrespective of architecture-specific errors after training begins.
    hard=[r for r in train if not baseline[r['id']]]
    correct=[r for r in train if baseline[r['id']]]
    if not hard:raise RuntimeError('RATE_NO_FAILURES_IN_TRAINING_DECK')
    def pair(step):
        # Rotation covers *every* member, including cohorts whose lengths
        # share factors with the old hard-coded prime strides.
        r=hard[(step+args.seed)%len(hard)]
        replay=(correct[(step+args.seed)%len(correct)] if correct else
                hard[(step+args.seed+1)%len(hard)])
        return r,replay
    return pair,len(hard),len(correct)


def _trial(name,bb,tok,baseline_train,baseline_eval,train,pair,eval_rows,args,zero,source_lora,out,raw_optimizer_state=None):
    import torch
    zero.restore_lora(bb,source_lora)
    is_raw=name=='raw'
    is_local=name=='glass_C'
    if not is_raw:torch.manual_seed(args.seed+{'glass_A':1,'glass_B':2,'glass_C':3}[name])
    if is_raw:
        params=[p for _,p in zero.lora_named_parameters(bb)]
        for p in params:p.requires_grad_(True)
        opt=torch.optim.AdamW(params,lr=args.raw_lr,weight_decay=0.)
        # A raw continuation must inherit the actual Adam moments from the
        # chosen checkpoint; resetting them would not measure its marginal
        # training rate under the existing training trajectory.
        if not isinstance(raw_optimizer_state,dict):
            raise RuntimeError('RATE_RAW_CONTINUATION_REQUIRES_OPTIMIZER_STATE')
        opt.load_state_dict(raw_optimizer_state)
        for group in opt.param_groups:group['lr']=args.raw_lr
        for slot in opt.state.values():
            for key,val in slot.items():
                if torch.is_tensor(val):slot[key]=val.to(args.device)
        adapter=None
    else:
        for _,p in zero.lora_named_parameters(bb):p.requires_grad_(False)
        for p in bb.parameters():p.grad=None
        hidden=int(bb.get_output_embeddings().weight.shape[1])
        if is_local:
            adapter=local_glass.FrontierLocalGlass(hidden,args.glass_feature_layers,
                        threshold=args.local_threshold,device=args.device)
            opt=None  # Original local-repair trainer uses a fresh optimizer per mode.
        else:
            adapter=ab.ContinuousGlass(hidden,args.glass_feature_layers,args.glass_width,
                                        gated=name=='glass_B')
            adapter.net.to(args.device)
            opt=torch.optim.AdamW(adapter.net.parameters(),lr=args.glass_lr,weight_decay=0.)
    rollout_args=glass_train._train_args(args)
    # Cache only frozen-Qwen teacher-forced features for A/B.  Capture time
    # is charged to the appropriate branch's training clock, not shared free.
    sites=(cached_glass.FrozenSites(bb,tok,rollout_args,baseline_train)
           if not is_raw and not is_local else None)
    elapsed=0.;steps=0;last_eval=0.
    local_token_steps=0;local_repairs_attempted=0
    branch_wall_start=time.perf_counter()
    prior_total_wall=0.
    history=[]
    working=out/f'{name}_working.pt'
    if working.is_file():
        saved=torch.load(working,map_location='cpu',weights_only=True)
        if (saved.get('schema')!=SCHEMA or saved.get('branch')!=name or
            saved.get('source_sha256')!=fr._sha256(out/'source_snapshot.pt')):
            raise RuntimeError('RATE_BRANCH_CHECKPOINT_LINEAGE_DRIFT')
        if is_raw:zero.restore_lora(bb,saved['lora'])
        elif is_local:
            adapter=local_glass.FrontierLocalGlass.restore(saved['local'],device=args.device)
        else:
            adapter.net.load_state_dict(saved['glass'])
            sites.restore(saved.get('frozen_sites',{}))
        if opt is not None:
            opt.load_state_dict(saved['optimizer'])
            for slot in opt.state.values():
                for key,val in slot.items():
                    if torch.is_tensor(val):slot[key]=val.to(args.device)
        elapsed=saved['elapsed'];steps=saved['steps'];last_eval=elapsed
        prior_total_wall=saved.get('total_branch_wall_seconds',0.)
        local_token_steps=saved.get('local_token_steps',0)
        local_repairs_attempted=saved.get('local_repairs_attempted',0)
        if saved.get('torch_rng') is not None:torch.set_rng_state(saved['torch_rng'])
        if args.device=='cuda' and saved.get('cuda_rng') is not None:
            torch.cuda.set_rng_state_all(saved['cuda_rng'])
    # Catch initialization/router defects before training; the untrained
    # adapter MUST be an exact identity on genuine greedy output.
    if not is_raw and steps==0:
        sentinels=eval_rows[:min(8,len(eval_rows))]
        initial=_greedy(bb,tok,sentinels,rollout_args,adapter,label=name+'_zero')
        if any(initial[r['id']]!=baseline_eval[r['id']] for r in sentinels):
            raise RuntimeError('RATE_GLASS_NONIDENTITY_AT_INITIALIZATION:'+name)
    while elapsed<args.budget_seconds and (args.max_steps==0 or steps<args.max_steps):
        row,replay=pair(steps)
        _sync(args.device);start=time.perf_counter()
        if is_raw:
            bb.train();opt.zero_grad(set_to_none=True)
            amp=zero.resolve_amp_dtype(args.device,'auto') if args.device=='cuda' else 'cpu'
            with torch.autocast('cuda' if args.device=='cuda' else 'cpu',
                enabled=args.device=='cuda',
                dtype=torch.bfloat16 if amp=='bfloat16' else torch.float16):
                loss=ladder._batch_loss(bb,tok,[row,replay],args.device,args.max_seq)
            if not bool(torch.isfinite(loss)):
                raise RuntimeError('RATE_RAW_NONFINITE_LOSS')
            loss.backward()
            torch.nn.utils.clip_grad_norm_(params,1.)
            opt.step()
        else:
            bb.eval()
            if is_local:
                step_result=local_glass.repair_step(adapter,bb,tok,row,rollout_args,
                      token_steps=args.local_token_steps,lr=args.local_lr,
                      margin=args.local_logit_margin,
                      max_token_fixes=args.local_max_token_fixes)
                loss_value=None  # Local optimizer uses token-margin objectives.
                local_token_steps+=step_result['token_optimizer_steps']
                local_repairs_attempted+=step_result['updated']
            else:
                step_result=cached_glass.train_step(adapter,opt,bb,row,replay,
                            sites,args,step=steps)
                loss_value=step_result['loss']
        _sync(args.device)
        elapsed+=time.perf_counter()-start
        steps+=1
        if elapsed-last_eval>=args.check_seconds or elapsed>=args.budget_seconds or (args.max_steps and steps>=args.max_steps):
            result=_greedy(bb,tok,eval_rows,rollout_args,adapter,label=name)
            scores=_with_rate(_score(baseline_eval,result,eval_rows),elapsed,steps)
            point={'branch':name,'steps':steps,'train_wall_seconds':elapsed,
                   'metrics':scores,'loss':float(loss.detach()) if is_raw else loss_value}
            history.append(point)
            fr._append(out/f'{name}_curve.jsonl',point)
            saved={'schema':SCHEMA,'branch':name,'steps':steps,'elapsed':elapsed,
                   'source_sha256':fr._sha256(out/'source_snapshot.pt'),
                   'optimizer':opt.state_dict() if opt is not None else None,
                   'total_branch_wall_seconds':prior_total_wall+time.perf_counter()-branch_wall_start,
                   'local_token_steps':local_token_steps,
                   'local_repairs_attempted':local_repairs_attempted,
                   'torch_rng':torch.get_rng_state(),
                   'cuda_rng':torch.cuda.get_rng_state_all() if args.device=='cuda' else None}
            if is_raw:saved['lora']=zero.lora_state(bb)
            elif is_local:saved['local']=adapter.state()
            else:
                saved['glass']={k:v.detach().cpu().clone() for k,v in adapter.net.state_dict().items()}
                saved['frozen_sites']=sites.state()
            fr._durable_torch(working,saved)
            _emit('QWEN_RATE_CHECK',{'branch':name,'steps':steps,
                'training_seconds':round(elapsed,3),
                'local_modes':adapter.n_modes if is_local else None,
                'local_token_steps':local_token_steps if is_local else None,
                'cached_sites':len(sites.sites) if sites is not None else None,
                'fresh':scores['fresh'],
                'archive':scores.get('archive_eval'),'all':scores['all']})
            last_eval=elapsed
    if not history:
        history=ledger._read(out/f'{name}_curve.jsonl')
    if not history:raise RuntimeError('RATE_NO_CHECKPOINT_EVALUATIONS')
    # The unmodified source is the denominator for each branch; save only
    # small adapters and LoRA tensors, never duplicate the frozen Qwen base.
    result={'schema':SCHEMA,'branch':name,'train_wall_seconds':elapsed,
            'steps':steps,'metrics':history[-1]['metrics'],'checkpoints':len(history),
            'full_time_budget_reached':elapsed>=args.budget_seconds,
            'total_branch_wall_seconds':prior_total_wall+time.perf_counter()-branch_wall_start,
            'local_token_steps':local_token_steps if is_local else None,
            'local_repairs_attempted':local_repairs_attempted if is_local else None,
            'local_modes':adapter.n_modes if is_local else None,
            'frozen_cached_sites':len(sites.sites) if sites is not None else None}
    if is_raw:
        fr._durable_torch(out/'raw_final_lora.pt',{'schema':SCHEMA,'lora':zero.lora_state(bb),
                                                   'source_sha256':fr._sha256(out/'source_snapshot.pt')})
    elif is_local:
        fr._durable_torch(out/f'{name}_final.pt',{'schema':SCHEMA,
                'local':adapter.state(),
                'source_sha256':fr._sha256(out/'source_snapshot.pt'),
                'activation_routing':'strict_four_layer_cosine'})
    else:
        fr._durable_torch(out/f'{name}_final.pt',{'schema':SCHEMA,
                'weights':{k:v.detach().cpu() for k,v in adapter.net.state_dict().items()},
                'source_sha256':fr._sha256(out/'source_snapshot.pt'),
                'gated':name=='glass_B'})
    ladder._atomic_json(out/f'{name}_result.json',result)
    for _,p in zero.lora_named_parameters(bb):p.requires_grad_(False)
    return result


def _comparison(results):
    raw=results['raw']
    comparison={}
    for name in ('glass_A','glass_B','glass_C'):
        candidate=results[name]
        sections={}
        for stratum in ('fresh','all','fresh_expanded','archive_eval'):
            r=raw['metrics'].get(stratum)
            g=candidate['metrics'].get(stratum)
            if r is None or g is None or not r['tested']:continue
            # Avoid claiming a statistically meaningful crossover from few
            # discrete acquired examples; this is a calibration diagnostic.
            delta=g['net_per_gpu_hour_reserved']-r['net_per_gpu_hour_reserved']
            sections[stratum]={'raw_net':r['net'],'glass_net':g['net'],
                  'raw_net_per_gpu_hour_reserved':r['net_per_gpu_hour_reserved'],
                  'glass_net_per_gpu_hour_reserved':g['net_per_gpu_hour_reserved'],
                  'difference_per_hour':delta,'ratio_if_raw_positive':(
                      g['net_per_gpu_hour_reserved']/r['net_per_gpu_hour_reserved']
                      if r['net_per_gpu_hour_reserved']>0 else None)}
        comparison[name]=sections
    return comparison


def run(args):
    ck,train,eval_rows,manifest,summary=_load_data(args)
    if summary is not None:return summary
    import torch
    from tools import nanojev_qwen_zero_line_backbone as zero
    source_lora=ck['lora'];src=ck['state']
    out=Path(args.output).resolve()
    tasks.terminal_preflight()
    bb,tok,sources,frozen,dirs,_=ladder._fresh_parent(args)
    if sources!=src['seed_state']['source_sha256'] or frozen!=src['seed_state']['frozen_sha256']:
        raise RuntimeError('RATE_BACKBONE_LINEAGE_DRIFT')
    zero.restore_lora(bb,source_lora)
    bb.requires_grad_(False)
    rollout_args=glass_train._train_args(args)
    # Training data must be pre-scored for the Glass loss function. The
    # evaluation deck is separate and NEVER used to select training pairs.
    baseline_train=_greedy(bb,tok,train,rollout_args,label='train_baseline',cache=out/'train_baseline.jsonl')
    baseline_eval=_greedy(bb,tok,eval_rows,rollout_args,label='evaluation_baseline',cache=out/'evaluation_baseline.jsonl')
    pair,hard,correct=_training_pairs(train,baseline_train,args)
    baseline={'train_correct':correct,'train_wrong':hard,
              'eval_correct':sum(baseline_eval.values()),'eval_total':len(baseline_eval),
              'source_step':src.get('step')}
    ladder._atomic_json(out/'baseline.json',baseline)
    _emit('QWEN_RATE_START',{'source_step':src.get('step'),'source_sha256':manifest['source_sha256'],
        'train_cases':len(train),'train_wrong':hard,'train_correct':correct,
        'evaluation_cases':len(eval_rows),'budget_seconds_per_branch':args.budget_seconds,
        'exclusive_gpu_required_for_meaningful_timing':True})
    results={}
    for name in BRANCHES:
        if (out/f'{name}_result.json').exists():
            prior=json.loads((out/f'{name}_result.json').read_text(encoding='utf8'))
            if prior.get('schema')!=SCHEMA or prior.get('branch')!=name:
                raise RuntimeError('RATE_COMPLETED_BRANCH_INVALID')
            results[name]=prior
            continue
        # For glass runs, the backbone stays frozen even during capture of
        # error-aligned features; only a freshly zero-initialized head trains.
        result=_trial(name,bb,tok,baseline_train,baseline_eval,
                      train,pair,eval_rows,args,zero,source_lora,out,ck.get('optimizer'))
        results[name]=result
    report={'schema':SCHEMA,'status':'COMPLETE','manifest':manifest,
            'baseline':baseline,'results':results,
            'evaluation_headroom_warning':(len(baseline_eval)-sum(baseline_eval.values())<max(10,len(baseline_eval)//10)),
            'comparison':_comparison(results),
            'decision':('CALIBRATION_ONLY_REPLICATE_AT_MULTIPLE_RAW_CHECKPOINTS' if
                all(r['full_time_budget_reached'] for r in results.values()) else
                'INCOMPLETE_TIME_BUDGET_DO_NOT_CLAIM_CROSSOVER'),
            'limitations':['small finite paired deck','archive cases deliberately enriched for prior failures',
              'timed training includes lazy frozen-feature capture and local repair rollouts',
              'greedy evaluation and checkpoint I/O are excluded from training seconds',
              'separate branch runs are sequential on the same device',
              'teacher-forced feature cache is valid for frozen states, not changing greedy paths',
              'initial candidate calibration does not certify sustained crossover']}
    ladder._atomic_json(out/'report.json',report)
    _emit('QWEN_RATE_DONE',{'source_step':src.get('step'),
        'raw_fresh':results['raw']['metrics']['fresh'],
        'glass_A_fresh':results['glass_A']['metrics']['fresh'],
        'glass_B_fresh':results['glass_B']['metrics']['fresh'],
        'glass_C_fresh':results['glass_C']['metrics']['fresh'],
        'comparison':report['comparison'],'report':str(out/'report.json')})
    return report


def main(argv=None):
    args=parser().parse_args(argv)
    return run(args)


if __name__=='__main__':main()
