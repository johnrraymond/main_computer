#!/usr/bin/env python3
"""Matched information-rate test: A continued LoRA1 vs B frozen LoRA1 + additive LoRA2.

Never modifies the parent checkpoint. All arms train on the same deterministic
frontier deck and are evaluated by greedy answer correctness on a disjoint deck.
LoRA2 is a genuinely additive, initially-zero correction at the existing q/v
projections, NOT a replacement LM head, glass, or a merged LoRA1.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import torch
import math
from pathlib import Path
import time

from tools import nanojev_qwen_lm_radius_ladder as ladder
from tools import nanojev_qwen_lm_radius_ladder_frontier as fr
from tools import nanojev_qwen_lm_frontier_rate_diagnostic as rate
from tools import nanojev_qwen_lm_frontier_rank1_glasses as glass_train
from tools import nanojev_qwen_lm_competency_ledger as ledger
from tools import nanojev_qwen_lm_frontier_tasks as tasks

SCHEMA = 'nanojev.qwen.frontier.lora1-vs-additive-lora2-rate.ab.v2'
BRANCHES = ('A_continue', 'B_stack')


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage', choices=('preview','run','report'), default='run')
    p.add_argument('--device', choices=('cuda','cpu'), default='cuda')
    p.add_argument('--stage1-checkpoint', required=True)
    p.add_argument('--text-checkpoint', required=True)
    p.add_argument('--source-checkpoint', help='Default balance best.pt; may choose another completed raw checkpoint')
    p.add_argument('--balance-output', default=str(Path.home()/'NanoJev'/'runs'/'qwen_lm_frontier_mixed_balance_v1'))
    p.add_argument('--output', default=str(Path.home()/'NanoJev'/'runs'/'qwen_lm_lora_stack_rate_ab_v2'))
    p.add_argument('--budget-seconds', type=float, default=120.)
    p.add_argument('--check-seconds', type=float, default=40.)
    p.add_argument('--max-steps', type=int, default=0)
    p.add_argument('--seed', type=int, default=96031)
    p.add_argument('--lr', type=float, default=3e-5, help='Same configured LR for both arms')
    p.add_argument('--archive-train', type=int, default=64)
    p.add_argument('--generated-train-per-cell', type=int, default=3)
    p.add_argument('--eval-per-cell', type=int, default=5)
    p.add_argument('--eval-frontier-per-cell', type=int, default=5)
    p.add_argument('--eval-archive', type=int, default=32)
    p.add_argument('--extra-levels', type=int, default=2)
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


def _paths(args):
    if (args.budget_seconds<=0 or args.check_seconds<=0 or args.max_steps<0 or args.lr<=0 or
        args.archive_train<1 or args.generated_train_per_cell<1 or args.eval_per_cell<1 or
        args.eval_frontier_per_cell<1 or args.eval_archive<0 or args.extra_levels<1 or
        args.max_seq<16 or args.max_tokens<args.max_seq or args.max_new_tokens<1):
        raise ValueError('LORA_STACK_INVALID_POLICY')
    src=Path(args.source_checkpoint).resolve() if args.source_checkpoint else Path(args.balance_output).resolve()/'best.pt'
    out=Path(args.output).resolve()
    # Never allow output inside any live parent-run tree, even if the checkpoint
    # itself is a file in a subdirectory of that run.
    protected=(Path(args.balance_output).resolve(),src,src.parent,
               Path(args.stage1_checkpoint).resolve(),Path(args.text_checkpoint).resolve())
    for item in protected:
        if out==item or out in item.parents or item in out.parents:
            raise RuntimeError('LORA_STACK_OUTPUT_OVERLAPS_INPUT')
    if args.stage!='report':
        required=(Path(args.stage1_checkpoint),Path(args.text_checkpoint))
        if not (args.stage=='run' and (out/'manifest.json').is_file()):
            required=(src,*required)
        for item in required:
            if not item.is_file():raise FileNotFoundError(item)
    return src,out


class SecondLoRALinear(torch.nn.Module):
    """Frozen existing q/v LoRA plus an independently trainable additive delta.

    Maintains LoRA1 and original base *without merging*; the two low-rank
    corrections receive exactly the same projection input x.
    """
    def __init__(self, first):
        import torch
        from torch import nn
        super().__init__()
        if not hasattr(first,'lora_a') or not hasattr(first,'lora_b') or not hasattr(first,'scale'):
            raise TypeError('LORA_STACK_REQUIRES_EXISTING_LORA1')
        self.first=first
        self.first.requires_grad_(False)
        rank=int(first.lora_a.shape[0]);in_features=int(first.lora_a.shape[1]);out_features=int(first.lora_b.shape[0])
        self.lora2_a=nn.Parameter(torch.empty(rank,in_features,device=first.lora_a.device,dtype=torch.float32))
        self.lora2_b=nn.Parameter(torch.zeros(out_features,rank,device=first.lora_b.device,dtype=torch.float32))
        nn.init.kaiming_uniform_(self.lora2_a,a=math.sqrt(5))
        self.scale=float(first.scale)

    def forward(self,x):
        from torch.nn import functional as F
        original=self.first(x)
        correction=F.linear(F.linear(x.float(),self.lora2_a),self.lora2_b)*self.scale
        return original+correction.to(original.dtype)


def _install_second(bb):
    from tools import nanojev_qwen_zero_line_backbone as zero
    targets=[(name,layer) for name,layer in bb.named_modules()
             if name.endswith(('.q_proj','.v_proj')) and isinstance(layer,zero.LoRALinear)]
    if not targets:raise RuntimeError('LORA_STACK_NO_EXISTING_QV_ADAPTERS')
    for name,first in targets:
        parent,attr=name.rsplit('.',1)
        setattr(bb.get_submodule(parent),attr,SecondLoRALinear(first))
    names=[(name,p) for name,p in bb.named_parameters() if name.endswith(('.lora2_a','.lora2_b'))]
    if len(names)!=4*len(targets)//2:raise RuntimeError('LORA_STACK_SECOND_PARAMETER_COUNT_DRIFT')
    if any(p.requires_grad for name,p in bb.named_parameters() if not name.endswith(('.lora2_a','.lora2_b'))):
        raise RuntimeError('LORA_STACK_FIRST_OR_BASE_STILL_TRAINABLE')
    return names


def _first_state(bb):
    """Recover original unwrapped LoRA1 key names from installed LoRA2 model."""
    result={}
    for name,p in bb.named_parameters():
        if name.endswith(('.first.lora_a','.first.lora_b')):
            old=name.replace('.first.lora_a','.lora_a').replace('.first.lora_b','.lora_b')
            result[old]=p.detach().cpu().clone()
    return result


def _same_weights(a,b):
    import torch
    return set(a)==set(b) and all(torch.equal(a[k].cpu(),b[k].cpu()) for k in a)


def _snapshot_source(src):
    return rate._load_snapshot(src)


def _decks(state,args,archive):
    return rate._make_decks(state,args,archive)


def _prepare(args):
    src,out=_paths(args)
    if args.stage=='report':
        result=json.loads((out/'report.json').read_text(encoding='utf8'))
        fr._event('QWEN_LORA_RATE_REPORT',result)
        return None,None,None,None,result
    if (out/'manifest.json').is_file() and args.stage=='run':
        manifest=json.loads((out/'manifest.json').read_text(encoding='utf8'))
        snap=out/'source_snapshot.pt'
        if (manifest.get('schema')!=SCHEMA or manifest.get('settings')!=_options(args) or
            not snap.is_file() or manifest.get('source_sha256')!=fr._sha256(snap)):
            raise RuntimeError('LORA_STACK_RESUME_POLICY_DRIFT')
        source=_snapshot_source(snap)
        train=ledger._read(out/'train_deck.jsonl')
        evaluation=ledger._read(out/'evaluation_deck.jsonl')
        if (not train or not evaluation or manifest.get('train_fingerprint')!=rate._fingerprint(train) or
            manifest.get('evaluation_fingerprint')!=rate._fingerprint(evaluation)):
            raise RuntimeError('LORA_STACK_RESUME_DECK_DRIFT')
        return source,train,evaluation,manifest,None
    if args.stage=='run' and out.exists() and any(out.iterdir()):
        raise RuntimeError('LORA_STACK_UNRECOGNIZED_NONEMPTY_OUTPUT')
    source=_snapshot_source(src)
    archive=[]
    dirs=[Path(args.balance_output),src.parent,Path(args.balance_output).parent/'qwen_lm_frontier_mixed_v1']
    seen=set()
    for directory in dirs:
        for fname in ('glass_hard_candidates.jsonl','glass_replay_candidates.jsonl'):
            for row in ledger._read(directory/fname):
                if (row.get('task') not in tasks.TASKS or not isinstance(row.get('level'),int)
                    or not row.get('source') or not row.get('target')):continue
                key=(row['source'],row['target'])
                if key not in seen:archive.append(row);seen.add(key)
    if not archive:raise RuntimeError('LORA_STACK_ARCHIVE_REQUIRED')
    train,evaluation=_decks(source['state'],args,archive)
    info={'source_checkpoint':str(src),'source_step':source['state']['step'],
          'training_cases':len(train),'evaluation_cases':len(evaluation),
          'evaluation_strata':{t:sum(r['stratum']==t for r in evaluation) for t in sorted({r['stratum'] for r in evaluation})},
          'branches':BRANCHES,'read_only_parent':True,'starts_training':args.stage=='run'}
    if args.stage=='preview':
        fr._event('QWEN_LORA_RATE_PREVIEW',info)
        return None,None,None,None,info
    out.mkdir(parents=True,exist_ok=False)
    fr._atomic_copy(src,out/'source_snapshot.pt')
    # Validate and use the immutable local snapshot: the external raw trainer
    # may atomically publish a newer checkpoint during this copy.
    source=_snapshot_source(out/'source_snapshot.pt')
    manifest={'schema':SCHEMA,'settings':_options(args),'source_sha256':fr._sha256(out/'source_snapshot.pt'),
              'source_step':source['state']['step'],'train_fingerprint':rate._fingerprint(train),
              'evaluation_fingerprint':rate._fingerprint(evaluation),'read_only_parent':True,
              'evaluations_are_not_population_accuracy':True,'exclusive_gpu_for_timing':True}
    ladder._atomic_json(out/'manifest.json',manifest)
    for f,deck in (('train_deck.jsonl',train),('evaluation_deck.jsonl',evaluation)):
        for row in deck:fr._append(out/f,row)
    return source,train,evaluation,manifest,None


def _move_optimizer(opt,device):
    import torch
    for s in opt.state.values():
        for key,v in s.items():
            if torch.is_tensor(v):s[key]=v.to(device)


def _rng_save(device):
    import torch
    return {'rng_cpu':torch.get_rng_state(),
            'rng_cuda':torch.cuda.get_rng_state_all() if device=='cuda' else None}


def _rng_restore(saved,device):
    import torch
    if saved.get('rng_cpu') is not None:torch.set_rng_state(saved['rng_cpu'])
    if device=='cuda' and saved.get('rng_cuda') is not None:torch.cuda.set_rng_state_all(saved['rng_cuda'])


def _score(bb,tok,eval_rows,rollout,baseline,seconds,steps,name):
    # Common grading path: GREEDY final answer, not teacher-forced loss.
    started=time.perf_counter()
    current=rate._greedy(bb,tok,eval_rows,rollout,label=name)
    eval_s=time.perf_counter()-started
    return rate._with_rate(rate._score(baseline,current,eval_rows),seconds,steps),eval_s


def _branch(name,bb,tok,source,train,pair,baseline,eval_rows,args,out,rollout):
    import torch
    from tools import nanojev_qwen_zero_line_backbone as zero
    from tools import nanojev_qwen_lm_radius_ladder as ladder_mod
    if name not in BRANCHES:
        raise ValueError('LORA_STACK_UNKNOWN_BRANCH')
    source_lora=source['lora']
    zero.restore_lora(bb,source_lora)
    bb.requires_grad_(False)
    second=(name=='B_stack')
    if second:
        torch.manual_seed(args.seed+7121)
        params_named=_install_second(bb)
        if not _same_weights(_first_state(bb),source_lora):
            raise RuntimeError('LORA_STACK_FIRST_CHANGED_ON_INSTALL')
        # A and B have exactly the same number of trainable coefficients if rank2==rank1.
        expected=sum(p.numel() for _,p in zero.lora_named_parameters(bb) if '.first.' in _)
    else:
        params_named=zero.lora_named_parameters(bb)
        for _,p in params_named:p.requires_grad_(True)
        expected=sum(p.numel() for _,p in params_named)
    params=[p for _,p in params_named]
    if not params or sum(p.numel() for p in params)!=expected:
        raise RuntimeError('LORA_STACK_PARAMETER_BUDGET_MISMATCH')
    # All branches get the same deterministic dropout/optimizer trajectory
    # (except for optimizer moments inherited only by true continuation).
    torch.manual_seed(args.seed)
    opt=torch.optim.AdamW(params,lr=args.lr,weight_decay=0)
    if name=='A_continue':
        if not isinstance(source.get('optimizer'),dict):
            raise RuntimeError('LORA_STACK_CONTINUATION_REQUIRES_OPTIMIZER')
        opt.load_state_dict(source['optimizer'])
        for group in opt.param_groups:group['lr']=args.lr
        _move_optimizer(opt,args.device)
    saved_path=out/(name+'_working.pt')
    steps=0;seconds=0.;eval_total=0.;last_eval=0.
    if saved_path.exists():
        saved=torch.load(saved_path,map_location='cpu',weights_only=True)
        if (saved.get('schema')!=SCHEMA or saved.get('branch')!=name or
            saved.get('source_sha256')!=fr._sha256(out/'source_snapshot.pt')):
            raise RuntimeError('LORA_STACK_BRANCH_LINEAGE_DRIFT')
        if second:
            given=dict(params_named)
            if set(given)!=set(saved['second']):raise RuntimeError('LORA_STACK_SECOND_KEY_DRIFT')
            with torch.no_grad():
                for key,p in given.items():p.copy_(saved['second'][key].to(device=p.device,dtype=p.dtype))
        else:
            zero.restore_lora(bb,saved['first'])
        opt.load_state_dict(saved['optimizer']);_move_optimizer(opt,args.device)
        seconds=float(saved['seconds']);steps=int(saved['steps']);last_eval=seconds
        eval_total=float(saved.get('eval_seconds',0.))
        _rng_restore(saved,args.device)
    source_hash=fr._sha256(out/'source_snapshot.pt')
    history=ledger._read(out/(name+'_curve.jsonl'))
    if history and (int(history[-1]['steps'])!=steps or abs(history[-1]['seconds']-seconds)>1e-5):
        # A crash between durable working.pt and its append-only curve is
        # recoverable if the saved checkpoint includes the exact audit row.
        if (saved_path.is_file() and 'saved' in locals() and
            int(history[-1]['steps'])<steps and saved.get('last_point') and
            int(saved['last_point']['steps'])==steps):
            fr._append(out/(name+'_curve.jsonl'),saved['last_point'])
            history.append(saved['last_point'])
        else:raise RuntimeError('LORA_STACK_CURVE_CHECKPOINT_DRIFT')
    if not history and saved_path.is_file() and 'saved' in locals() and saved.get('last_point'):
        fr._append(out/(name+'_curve.jsonl'),saved['last_point'])
        history.append(saved['last_point'])
    # Zero-addition identity should hold for all source parameters; cheap
    # forward validation happens separately in the unit tests.
    while seconds<args.budget_seconds and (not args.max_steps or steps<args.max_steps):
        row,replay=pair(steps)
        rate._sync(args.device);start=time.perf_counter()
        bb.train();opt.zero_grad(set_to_none=True)
        amp=zero.resolve_amp_dtype(args.device,'auto') if args.device=='cuda' else 'cpu'
        with torch.autocast('cuda' if args.device=='cuda' else 'cpu',enabled=args.device=='cuda',
                            dtype=torch.bfloat16 if amp=='bfloat16' else torch.float16):
            loss=ladder_mod._batch_loss(bb,tok,[row,replay],args.device,args.max_seq)
        if not bool(torch.isfinite(loss)):raise RuntimeError('LORA_STACK_NONFINITE_LOSS')
        loss.backward()
        if any(p.grad is None or not bool(torch.isfinite(p.grad).all()) for p in params):
            raise RuntimeError('LORA_STACK_MISSING_OR_NONFINITE_GRAD')
        torch.nn.utils.clip_grad_norm_(params,1.)
        opt.step();rate._sync(args.device)
        seconds+=time.perf_counter()-start
        steps+=1
        if (seconds-last_eval>=args.check_seconds or seconds>=args.budget_seconds or
            (args.max_steps and steps>=args.max_steps)):
            metrics,esec=_score(bb,tok,eval_rows,rollout,baseline,seconds,steps,name)
            eval_total+=esec
            for entry in metrics.values():
                entry['net_per_hour_including_audits']=(3600*entry['net']/(seconds+eval_total)
                                                       if seconds+eval_total>0 else None)
            point={'branch':name,'steps':steps,'seconds':seconds,'evaluation_seconds':eval_total,
                   'loss':float(loss.detach()),'metrics':metrics}
            saved={'schema':SCHEMA,'branch':name,'source_sha256':source_hash,'steps':steps,
                   'seconds':seconds,'eval_seconds':eval_total,'optimizer':opt.state_dict(),
                   'last_point':point,**_rng_save(args.device)}
            if second:
                if not _same_weights(_first_state(bb),source_lora):
                    raise RuntimeError('LORA_STACK_FROZEN_FIRST_DRIFT')
                saved['second']={key:p.detach().cpu().clone() for key,p in params_named}
            else:saved['first']=zero.lora_state(bb)
            fr._durable_torch(saved_path,saved)
            fr._append(out/(name+'_curve.jsonl'),point)
            history.append(point)
            # A best candidate, not a holdout-tested estimator. Keep both the
            # final and best checkpoint in case a branch regresses later.
            key=lambda h:(h['metrics'].get('fresh_expanded',{}).get('net',0),
                          h['metrics']['fresh']['net'],h['metrics']['all']['net'])
            best=max(history,key=key)
            if best is point:
                fr._durable_torch(out/(name+'_best.pt'),saved)
            fr._event('QWEN_LORA_RATE_CHECK',{'branch':name,'steps':steps,'train_seconds':round(seconds,3),
                         'all':metrics['all'],'fresh':metrics['fresh'],
                         'fresh_expanded':metrics.get('fresh_expanded'),
                         'archive':metrics.get('archive_eval')})
            last_eval=seconds
    if not history:raise RuntimeError('LORA_STACK_NO_EVALUATION')
    if second and not _same_weights(_first_state(bb),source_lora):
        raise RuntimeError('LORA_STACK_FROZEN_FIRST_DRIFT_FINAL')
    best=max(history,key=lambda h:(h['metrics'].get('fresh_expanded',{}).get('net',0),
                                   h['metrics']['fresh']['net'],h['metrics']['all']['net']))
    result={'schema':SCHEMA,'branch':name,'steps':steps,'train_seconds':seconds,
            'evaluation_seconds':eval_total,'metrics':history[-1]['metrics'],
            'best_metrics':best['metrics'],'best_step':best['steps'],
            'trainable_parameters':sum(p.numel() for p in params),
            'fixed_first_lora':second,'inherited_optimizer':name=='A_continue',
            'full_time_budget_reached':seconds>=args.budget_seconds}
    ladder._atomic_json(out/(name+'_result.json'),result)
    return result


def run(args):
    source,train,eval_rows,manifest,done=_prepare(args)
    if done is not None:return done
    import torch
    from tools import nanojev_qwen_zero_line_backbone as zero
    out=Path(args.output).resolve()
    tasks.terminal_preflight()
    bb,tok,sources,frozen,dirs,_=ladder._fresh_parent(args)
    if sources!=source['state']['seed_state']['source_sha256'] or frozen!=source['state']['seed_state']['frozen_sha256']:
        raise RuntimeError('LORA_STACK_BACKBONE_LINEAGE_DRIFT')
    zero.restore_lora(bb,source['lora'])
    bb.requires_grad_(False)
    from types import SimpleNamespace
    rollout=SimpleNamespace(device=args.device,max_tokens=args.max_tokens,
           max_new_tokens=args.max_new_tokens,feature_layers=4,
           preserve_weight=.1,delta_l2=.001,gate_loss=.002,
           decode_cache=True,decode_progress_tokens=16)
    train_initial=rate._greedy(bb,tok,train,rollout,label='lora_train_baseline',cache=out/'train_baseline.jsonl')
    eval_initial=rate._greedy(bb,tok,eval_rows,rollout,label='lora_eval_baseline',cache=out/'evaluation_baseline.jsonl')
    pair,hard,correct=rate._training_pairs(train,train_initial,args)
    fr._event('QWEN_LORA_RATE_START',{'source_step':source['state']['step'],
           'branches':BRANCHES,'train_cases':len(train),'train_wrong':hard,
           'train_correct':correct,'eval_cases':len(eval_rows),
           'eval_initial_correct':sum(eval_initial.values()),
           'budget_seconds_per_branch':args.budget_seconds})
    results={}
    for name in BRANCHES:
        path=out/(name+'_result.json')
        if path.exists():
            result=json.loads(path.read_text(encoding='utf8'))
            if result.get('schema')!=SCHEMA or result.get('branch')!=name:
                raise RuntimeError('LORA_STACK_BAD_COMPLETED_BRANCH')
            results[name]=result;continue
        # LoRA2 is deliberately last: q/v projections are wrapped only
        # after A has finished training LoRA1.
        results[name]=_branch(name,bb,tok,source,train,pair,eval_initial,eval_rows,args,out,rollout)
    normal=results['A_continue']
    comparison={}
    for name in BRANCHES[1:]:
        other=results[name]
        comparison[name]={}
        for s in ('all','fresh','fresh_expanded','archive_eval'):
            a=normal['metrics'].get(s);b=other['metrics'].get(s)
            if a and b:
                comparison[name][s]={'net_difference':b['net']-a['net'],
                      'net_rate_difference_per_hour':b['net_per_gpu_hour_reserved']-a['net_per_gpu_hour_reserved'],
                      'continuation_net':a['net'],'candidate_net':b['net']}
    report={'schema':SCHEMA,'status':'COMPLETE','manifest':manifest,
            'results':results,'comparison':comparison,
            'evaluation_cases':len(eval_rows),'training_cases':len(train),
            'source_step':source['state']['step'],
            'decision':'CALIBRATION_ONLY_REPLICATE_AT_LATER_CHECKPOINTS' if
               all(v['full_time_budget_reached'] for v in results.values()) else
               'INCOMPLETE_TIME_BUDGET_DO_NOT_CLAIM_CROSSOVER',
            'limitations':['fixed small sample not a population estimate',
               'archive deliberately oversamples known hard cases',
               'training GPU-reserved time excludes evaluations and initial backbone load',
               'evaluation repeated at checkpoints; best-of-curve selection is exploratory',
               'same nominal lr and rank do not guarantee equal optimizer dynamics',
               'LoRA2 has additional available model capacity; not a pure freezing-only comparison']}
    ladder._atomic_json(out/'report.json',report)
    fr._event('QWEN_LORA_RATE_DONE',{'source_step':source['state']['step'],
              'results':{k:{'all':v['metrics']['all'],'fresh':v['metrics']['fresh'],
                       'fresh_expanded':v['metrics'].get('fresh_expanded'),
                       'steps':v['steps'],'train_seconds':v['train_seconds']} for k,v in results.items()},
              'comparison':comparison,'report':str(out/'report.json')})
    return report


def main(argv=None):
    return run(parser().parse_args(argv))


if __name__=='__main__':main()
