#!/usr/bin/env python3
"""V10 X/Y lattice curriculum trainer: new optimizer and isolated lineage.

--steps is the number of *additional* V10 optimizer steps, not the V9 absolute
step number. Imports V9 or V2.1 trained decoder weights without overwriting
that parent. Frozen Qwen backbone; trained X/Y decoder; Z diagnostic only.

Every advancement report probes the same dev cases at all grammar-bridge and assistance stages. The
scheduler focuses the earliest unmastered dependency, including the X-only and
Y-only branches before a merged stage. No legacy T03 exercises are gate inputs.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools import nanojev_game_math_curriculum_v10_runtime as rt
from tools import nanojev_game_math_curriculum_v10_policy as policy
from tools import nanojev_game_transform_experiment as ex


def make_parser()->argparse.ArgumentParser:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--teacher-db',type=Path,default=rt.DEFAULT_TEACHER)
    p.add_argument('--source-checkpoint',type=Path,default=rt.DEFAULT_SOURCE)
    p.add_argument('--parent-checkpoint',type=Path,default=rt.DEFAULT_PARENT,
                   help='V9 latest.pt to initialize a fresh V10 optimizer')
    p.add_argument('--output',type=Path,default=rt.DEFAULT_OUTPUT)
    p.add_argument('--warm-start-checkpoint',type=Path,
                   help='Read old V10 latest.pt WEIGHTS only, from a separate immutable prior run; '
                        'start a fresh optimizer and new audited teacher lineage')
    p.add_argument('--steps',type=int,default=1000,
                   help='Total NEW V10 steps since parent import, not absolute checkpoint step')
    p.add_argument('--chunk-steps',type=int,default=250)
    p.add_argument('--batch-levels',type=int,default=4)
    p.add_argument('--probe-limit',type=int,default=48)
    p.add_argument('--probe-batch-size',type=int,default=4)
    policy.add_gate_args(p)
    p.add_argument('--min-count',type=int,default=16)
    p.add_argument('--lr',type=float,default=1e-5)
    p.add_argument('--seed',type=int,default=20261017)
    p.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    p.add_argument('--dtype',choices=('float16','bfloat16','float32'),default='float16')
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--preflight-only',action='store_true',
                   help='Inspect teacher, case counts and provenance without GPU or optimizer')
    return p


def config(args,meta,parent_sha, warm_start_sha=None, warm_start_step=None)->dict:
    return {'schema':rt.SCHEMA,'teacher_sha256':meta['teacher_sha256'],
            'source_sha256':rt.source_hash(args.source_checkpoint),
            'parent_sha256':parent_sha,
            'warm_start_sha256':warm_start_sha,
            'warm_start_step':warm_start_step,
            'trainer_sha256':rt.source_hash(Path(__file__)),
            'runtime_sha256':rt.source_hash(Path(rt.__file__)),
            'seed':args.seed,'lr':args.lr,'chunk_steps':args.chunk_steps,
            'batch_levels':args.batch_levels,'probe_limit':args.probe_limit,
            'probe_batch_size':args.probe_batch_size,
            'gate_fraction':args.gate_fraction,'min_count':args.min_count,
            'device':args.device,'dtype':args.dtype}


# This is the SHA-256 of the exact pre-patch trainer shipped in the
# 2026-10-17 repository snapshot. Only this known code revision is allowed
# to migrate its stored policy to the new resumable advancement mechanism.
LEGACY_TRAINER_SHA256 = 'd95174527a2c6a31d2407eabac68fd12cdb9e1745fc2e6efa952bda384503d20'


def _check_resume_configuration(previous: dict, new: dict) -> bool:
    """Allow an advancement change, never a mathematical/optimizer drift.

    Returns True when the threshold actually changed. The pre-existing trainer
    SHA can migrate exactly once; an unknown trainer implementation is rejected.
    """
    flexible = {'gate_fraction', 'trainer_sha256'}
    for key in set(previous) | set(new):
        if key not in flexible and previous.get(key) != new.get(key):
            raise RuntimeError('V10_CONFIG_DRIFT_CANNOT_RESUME:'+key)
    if previous.get('trainer_sha256') not in (new['trainer_sha256'], LEGACY_TRAINER_SHA256):
        raise RuntimeError('V10_UNRECOGNIZED_TRAINER_PROVENANCE')
    return abs(previous['gate_fraction'] - new['gate_fraction']) > 1e-10


def objective(model:dict,prompt:str,x:float,y:float):
    import torch
    output,_ = ex.forward(model,[prompt])
    truth=torch.tensor([[x,y]],device=model['device'],dtype=torch.float32)
    loss,_=model['v3'].objective(output,truth,model['base'],'attention')
    return loss


def draw_tasks(focus:str,seed:int,target:int,n:int=4)->list[str]:
    if focus not in rt.STAGE_ORDER or n<2:
        raise ValueError('INVALID_V10_FOCUS_OR_BATCH')
    index=rt.STAGE_ORDER.index(focus)
    # Directly train the failed stage, retain prior requirements and the
    # sibling X-first/Y-first branch. Do not mingle a different math tier as
    # a surrogate for the same paired transformation.
    ancestors=rt.STAGE_ORDER[:index]
    sibling=None
    tier,node=focus.split('/')
    paired={'logbar_x':'logbar_y','logbar_y':'logbar_x',
            'x_raw_y_full':'y_raw_x_full','y_raw_x_full':'x_raw_y_full',
            'register_x':'register_y','register_y':'register_x',
            'bridge_logbar_x':'bridge_logbar_y','bridge_logbar_y':'bridge_logbar_x',
            'bridge_register_x':'bridge_register_y','bridge_register_y':'bridge_register_x'}
    if node in paired:
        sibling=tier+'/'+paired[node]
    lower=ancestors[rt.stable_seed(seed,target,'retain')%len(ancestors)] if ancestors else focus
    family=[focus]*max(1,n-2)+[lower,sibling or lower]
    family=family[:n]
    import random
    random.Random(rt.stable_seed(seed,target,'shuffle')).shuffle(family)
    return family


def _get_pool(stage:str,train_rows:list[dict],model:dict,cache:dict)->list[dict]:
    if stage not in cache:
        eligible=[row for row in train_rows if rt.token_eligible(model,row['prompts'][stage])]
        cache[stage]=eligible
        print(json.dumps({'event':'v10_train_token_preflight','stage':stage,
                          'eligible':len(eligible),'total':len(train_rows)},sort_keys=True),flush=True)
    if not cache[stage]:
        raise RuntimeError('V10_NO_TOKEN_ELIGIBLE_TRAINING_CASES:'+stage)
    return cache[stage]


def _report(model,dev_rows,args,*,step:int,local_step:int,focus:str,passes:int,
            teacher_sha:str,parent_sha:str)->dict:
    model['decoder'].eval();model['gamehead'].eval()
    result=rt.evaluate(dev_rows,list(rt.STAGE_ORDER),
            lambda ps:rt.batch_predict(model,ps),
            lambda p:rt.token_eligible(model,p),
            batch_size=args.probe_batch_size,
            gate_fraction=args.gate_fraction,min_count=args.min_count)
    policy.attach_diagnostics(result,args.min_count)
    result.update({'step':step,'v10_steps':local_step,'focus_before_probe':focus,
                   'consecutive_passes_before_probe':passes,
                   'split':'dev','teacher_sha256':teacher_sha,
                   'parent_sha256':parent_sha,'Z_loss_weight':0})
    return result


def decide_focus(focus:str,passes:int,first:str|None)->tuple[str,int,bool]:
    """Regression is immediate; promotion requires two successive probes."""
    if focus not in rt.STAGE_ORDER:
        raise ValueError('INVALID_V10_FOCUS')
    current=rt.STAGE_ORDER.index(focus)
    if first is not None and rt.STAGE_ORDER.index(first)<current:
        return first,0,False
    if first == focus:
        return focus,0,False
    consecutive=passes+1
    if consecutive < 2:
        return focus,consecutive,False
    if first is None:
        return focus,0,True
    return first,0,False


def run(args):
    policy.resolve_gate(args)
    if (args.steps<1 or args.chunk_steps<1 or args.batch_levels<2 or args.probe_limit<args.min_count
        or args.probe_batch_size<1 or args.lr<=0 or not 0<args.gate_fraction<=1):
        raise ValueError('V10_INVALID_TRAINING_CONFIGURATION')
    meta=rt.teacher_preflight(args.teacher_db)
    if args.preflight_only:
        return {'status':'PASS','teacher':meta,'model_loaded':False,
                'note':'Teacher schema checked; use V10 build verify for complete oracle recheck.'}
    import torch
    source=args.source_checkpoint.resolve();parent_path=args.parent_checkpoint.resolve()
    parent_sha=rt.source_hash(parent_path)
    dest=args.output.resolve()
    warm_path = args.warm_start_checkpoint.resolve() if args.warm_start_checkpoint else None
    if (dest in (source.parent,parent_path.parent) or dest==args.teacher_db.resolve().parent
            or (warm_path is not None and dest == warm_path.parent)):
        raise ValueError('V10_OUTPUT_MUST_BE_ISOLATED_FROM_PARENT_AND_TEACHER_AND_WARM_START')
    dev_rows=rt.read_cases(args.teacher_db,'dev',limit=args.probe_limit)
    train_rows=rt.read_cases(args.teacher_db,'train')
    if len(dev_rows)<args.min_count or not train_rows:
        raise ValueError('V10_TRAIN_OR_DEV_SPLIT_INSUFFICIENT')
    model,parent,loaded_hash=rt.load_model(source,parent_path,device=args.device,
        dtype=args.dtype,seed=args.seed,allow_model_download=args.allow_model_download,
        expected_teacher_sha=meta['teacher_sha256'])
    if parent_sha != loaded_hash:
        raise AssertionError('PARENT_CHANGED_WHILE_LOADING')
    warm_weights = None
    warm_step = None
    warm_sha = None
    if warm_path is not None:
        warm_sha = rt.source_hash(warm_path)
        warm_weights = torch.load(warm_path,map_location='cpu',weights_only=True)
        if (warm_weights.get('schema') != rt.SCHEMA or
                warm_weights.get('config',{}).get('source_sha256') != rt.source_hash(source) or
                warm_weights.get('config',{}).get('parent_sha256') != parent_sha or
                'decoder' not in warm_weights or
                'game_operator_head' not in warm_weights):
            raise ValueError('V10_WARM_START_PROVENANCE_INCOMPATIBLE')
        warm_step = int(warm_weights['step'])
        if warm_step < int(parent.get('step',0)):
            raise ValueError('V10_WARM_START_STEP_REGRESSION')
    dest.mkdir(parents=True,exist_ok=True)
    cfg=config(args,meta,parent_sha,warm_sha,warm_step)
    cfgpath=dest/'v10-config.json'
    cp_path=dest/'latest.pt'
    previous_cfg=json.loads(cfgpath.read_text()) if cfgpath.exists() else None
    if cp_path.exists() and previous_cfg is None:
        raise RuntimeError('V10_RESUME_CONFIG_MISSING')
    if previous_cfg is not None:
        _check_resume_configuration(previous_cfg,cfg)
    decoder=model['decoder'];gamehead=model['gamehead']
    for p in gamehead.parameters():p.requires_grad_(False)
    optimizer=torch.optim.AdamW(decoder.parameters(),lr=args.lr)
    parent_step = warm_step if warm_step is not None else int(parent.get('step',0))
    if cp_path.exists():
        cp=torch.load(cp_path,map_location='cpu',weights_only=True)
        if cp.get('schema')!=rt.SCHEMA:
            raise RuntimeError('V10_RESUME_CHECKPOINT_PROVENANCE_DRIFT')
        threshold_changed=_check_resume_configuration(cp.get('config',{}),cfg)
        decoder.load_state_dict(cp['decoder'],strict=True)
        gamehead.load_state_dict(cp['game_operator_head'],strict=True)
        optimizer.load_state_dict(cp['optimizer'])
        torch.set_rng_state(cp['rng_cpu'])
        if args.device=='cuda' and cp.get('rng_cuda') is not None:
            torch.cuda.set_rng_state_all(cp['rng_cuda'])
        local_step=int(cp['v10_steps']);focus=cp['focus'];passes=int(cp['passes'])
        origin_step=int(cp['parent_step'])
        if origin_step != parent_step or cp['step']!=origin_step+local_step:
            raise RuntimeError('V10_RESUME_STEP_DRIFT')
        print(json.dumps({'event':'v10_resume','v10_steps':local_step,'focus':focus,
                          'parent_step':origin_step},sort_keys=True),flush=True)
        if threshold_changed:
            old_percent=100.0*cp['config']['gate_fraction']
            changed=_report(model,dev_rows,args,step=origin_step+local_step,
                 local_step=local_step,focus=focus,passes=passes,
                 teacher_sha=meta['teacher_sha256'],parent_sha=parent_sha)
            # Previous consecutive passes are invalid under a new threshold.
            focus=changed['first_failing']
            passes=0
            changed.update({'old_advancement_percent':old_percent,
                            'focus_after_gate_change':focus,
                            'consecutive_passes':0,
                            'reason':'advancement_threshold_changed'})
            change_path=dest/(f'gate-change-step-{origin_step+local_step:09d}-'
                              f'to-{args.gate_fraction*100:g}-percent.json')
            rt.save_json(change_path,changed)
            print(json.dumps({'event':'v10_advancement_threshold_changed',
                   'from_percent':old_percent,'to_percent':args.gate_fraction*100,
                   'v10_steps':local_step,'focus':focus,'passes':0,
                   'report':str(change_path)},sort_keys=True),flush=True)
            if focus is None:
                rt.save_json(cfgpath,cfg)
                return {'status':'ALL_STAGES_PASS_NEW_GATE', 'v10_steps':local_step,
                        'checkpoint':str(cp_path),'report':str(change_path)}
    else:
        if warm_weights is not None:
            decoder.load_state_dict(warm_weights['decoder'],strict=True)
            gamehead.load_state_dict(warm_weights['game_operator_head'],strict=True)
            print(json.dumps({'event':'v10_audited_warm_start',
                  'checkpoint':str(warm_path),'sha256':warm_sha,
                  'inherited_global_step':warm_step,
                  'optimizer':'new', 'teacher_sha256':meta['teacher_sha256']},
                  sort_keys=True),flush=True)
        local_step=0;origin_step=parent_step;passes=0
        initial=_report(model,dev_rows,args,step=origin_step,local_step=0,
              focus=rt.STAGE_ORDER[0],passes=0,teacher_sha=meta['teacher_sha256'],
              parent_sha=parent_sha)
        focus=initial['first_failing']
        rt.save_json(dest/'baseline-probe.json',initial)
        print(json.dumps({'event':'v10_baseline','first_failing':focus,
                          'report':str(dest/'baseline-probe.json'),'stages':len(rt.STAGE_ORDER)},sort_keys=True),flush=True)
        if focus is None:
            return {'status':'ALL_STAGES_PASS_BASELINE','v10_steps':0,
                    'report':str(dest/'baseline-probe.json')}
    if args.steps<=local_step:
        raise ValueError('V10_TARGET_STEPS_MUST_EXCEED_COMPLETED_V10_STEPS')
    rt.save_json(cfgpath,cfg)
    pool_cache={}
    while local_step<args.steps:
        cutoff=min(local_step+args.chunk_steps,args.steps)
        last_loss=0.
        for target in range(local_step+1,cutoff+1):
            decoder.train()
            optimizer.zero_grad(set_to_none=True)
            tasks=draw_tasks(focus,args.seed,target,args.batch_levels)
            total_loss=0.
            for slot,stage in enumerate(tasks):
                pool=_get_pool(stage,train_rows,model,pool_cache)
                row=pool[rt.stable_seed(args.seed,target,slot,stage)%len(pool)]
                loss=objective(model,row['prompts'][stage],row['x'],row['y'])
                if not torch.isfinite(loss):
                    raise FloatingPointError('V10_NONFINITE_TRAINING_LOSS:'+stage)
                (loss/len(tasks)).backward()
                total_loss+=float(loss.detach())/len(tasks)
            torch.nn.utils.clip_grad_norm_(decoder.parameters(),1.)
            optimizer.step()
            last_loss=total_loss
        local_step=cutoff
        report=_report(model,dev_rows,args,step=origin_step+local_step,
                   local_step=local_step,focus=focus,passes=passes,
                   teacher_sha=meta['teacher_sha256'],parent_sha=parent_sha)
        first=report['first_failing']
        focus,passes,complete=decide_focus(focus,passes,first)
        report.update({'focus':focus,'consecutive_passes':passes,'complete':complete,
                       'training_loss':last_loss})
        report_path=dest/f'probe-step-{origin_step+local_step:09d}.json'
        rt.save_json(report_path,report)
        saved={'schema':rt.SCHEMA,'config':cfg,'step':origin_step+local_step,
               'parent_step':origin_step,'v10_steps':local_step,'focus':focus,
               'passes':passes,'complete':complete,'decoder':decoder.state_dict(),
               'game_operator_head':gamehead.state_dict(),
               'optimizer':optimizer.state_dict(),'rng_cpu':torch.get_rng_state()}
        if args.device=='cuda':
            saved['rng_cuda']=torch.cuda.get_rng_state_all()
        temp=dest/'latest.tmp.pt'
        torch.save(saved,temp)
        temp.replace(cp_path)
        print(json.dumps({'event':'v10_xy_probe','advancement_percent':args.gate_fraction*100,
                          'step':origin_step+local_step,
                          'v10_steps':local_step,'first_failing':first,
                          'focus':focus,'passes':passes,'report':str(report_path),
                          'coupled_Z_diagnostic_only':True},sort_keys=True),flush=True)
        if complete:
            break
    return {'status':'PASS','step':origin_step+local_step,'v10_steps':local_step,
            'focus':focus,'checkpoint':str(cp_path),'Z_loss_weight':0,
            'parent_untouched':True}


def main(argv:list[str]|None=None)->int:
    a=make_parser().parse_args(argv)
    print(json.dumps(run(a),sort_keys=True),flush=True)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
