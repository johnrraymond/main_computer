#!/usr/bin/env python3
"""One-entrypoint sequential numeric-reconstruction experimental campaign.

Stages: preflight, diagnose, pilot, compare, all. "all" performs validation
work only; the test split requires the separate explicit final-test stage.
No checkpoint is overwritten, all three decoder arms have isolated roots.
Pilot safely resumes partially finished arms and skips completed ones up to --steps.
This behavior is unchanged by --resume; manual --additional-steps is explicit-only.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import sys

ARMS=('residual','factorized','attention')
BACKBONES=('qwen','tinystories','pythia')
LAUNCHER_VERSION='v3-campaign-restart-v2'

def command(script,*args):
    return [sys.executable,str(Path(__file__).resolve().parent/script),*map(str,args)]


def plan_pilot_restart(call, output, target):
    """Continue a partially finished arm toward a TOTAL target, without deleting data.

    This intentionally differs from the decoder CLI's --additional-steps: the
    campaign --steps is an absolute target for every selected arm.  The decoder
    still validates checkpoint/parent/config identities during restore.
    """
    if not output.exists() or (output.is_dir() and not any(output.iterdir())):
        return call, 'start', 0
    if not output.is_dir():
        raise RuntimeError('V3_CAMPAIGN_OUTPUT_NOT_DIRECTORY:'+str(output))
    checkpoint=output/'latest.pt'
    if not checkpoint.is_file():
        raise RuntimeError('V3_CAMPAIGN_NONEMPTY_WITHOUT_LATEST:'+str(output)
                           +' (preserve existing files and inspect before recovery)')
    # Do not use arbitrary pickle deserialization on checkpoint files.
    import torch
    try:
        saved=torch.load(checkpoint,map_location='cpu',weights_only=True)
        step=saved['step']
        if type(step) is not int or step<0:raise ValueError('invalid step')
        if not isinstance(saved.get('config'),dict):raise ValueError('missing config')
    except Exception as exc:
        raise RuntimeError('V3_CAMPAIGN_LATEST_UNREADABLE:'+str(checkpoint)) from exc
    if step>target:
        raise RuntimeError('V3_CAMPAIGN_TARGET_BELOW_EXISTING_STEP:'+str(step)+'>'+str(target))
    if step==target:
        if not (output/'best.pt').is_file():
            raise RuntimeError('V3_CAMPAIGN_COMPLETE_WITHOUT_BEST:'+str(output))
        return None,'already-complete',step
    return call+['--resume','--additional-steps',str(target-step)],'resume',step

def execute(args,runner=subprocess.run):
    runs=args.runs_root.expanduser().resolve()
    for backbone in args.backbones:
        precision=runs/f'independent_altreg_math_{backbone}_precision_v2'
        control=runs/f'independent_altreg_math_{backbone}_weight0_control'
        if args.stage=='preflight':
            print(json.dumps({'backbone':backbone,'precision_checkpoint':str(precision/'best-precision.pt'),
                'precision_ready':(precision/'best-precision.pt').is_file(),
                'control_ready':(control/'best-precision.pt').is_file(),
                'decoder_parent_ready':(precision/(args.parent_checkpoint+'.pt')).is_file(),
                'stage':'preflight'},sort_keys=True),flush=True)
            continue
        tasks=[]
        if args.stage in ('diagnose','all'):
            tasks.append(command('nanojev_altreg_math_reconstruction_compare_v3.py',
                '--backbone',backbone,'--precision-root',precision,'--control-root',control,
                '--output-root',runs/f'independent_altreg_math_{backbone}_reconstruction_comparison_v3',
                '--device',args.device,'--split','validation',
                '--checkpoints','best-precision','best-asinh'))
        if args.stage in ('pilot','all'):
            for arm in args.arms:
                output=runs/f'independent_altreg_math_{backbone}_v3_{"residual_identity" if arm=="residual" else arm}'
                call=command('nanojev_altreg_math_v3_decoder_experiment.py','--backbone',backbone,
                  '--arm',arm,'--parent-root',precision,'--parent-checkpoint',args.parent_checkpoint,
                  '--results-root',output,
                  '--steps',args.steps,'--batch-size',args.batch_size,'--device',args.device,
                  '--dtype',args.dtype,'--eval-every',args.eval_every,'--report-every',args.report_every)
                if args.allow_model_download:call.append('--allow-model-download')
                # --steps is always a TOTAL target. --resume is accepted for
                # compatibility, but never forces resume of a fresh arm or adds
                # 1000 steps beyond the requested target.
                if args.additional_steps is not None:
                    if not args.resume:
                        raise RuntimeError('V3_CAMPAIGN_ADDITIONAL_REQUIRES_RESUME')
                    # Explicit relative continuation of a single existing arm
                    # remains available, but prevents accidental mixed-arm runs.
                    if len(args.arms)!=1:
                        raise RuntimeError('V3_CAMPAIGN_RELATIVE_RESUME_SINGLE_ARM_ONLY')
                    if not output.is_dir() or not (output/'latest.pt').is_file():
                        raise RuntimeError('V3_CAMPAIGN_RELATIVE_RESUME_REQUIRES_CHECKPOINT:'+str(output))
                    call.extend(['--resume','--additional-steps',str(args.additional_steps)])
                    state='relative-resume';saved_step=None
                else:
                    call,state,saved_step=plan_pilot_restart(call,output,args.steps)
                print(json.dumps({'event':'altreg_campaign_pilot_plan','launcher_version':LAUNCHER_VERSION,
                    'backbone':backbone,'arm':arm,'state':state,'saved_step':saved_step,
                    'target_step':args.steps,'results_root':str(output)},sort_keys=True),flush=True)
                if call is None:continue
                tasks.append(call)
        if args.stage in ('compare','all','final-test'):
            call=command('nanojev_altreg_math_v3_pilot_compare.py',
                '--backbone',backbone,'--arms',*args.arms,'--runs-root',runs,
                '--output-root',runs/f'independent_altreg_math_{backbone}_v3_comparison_identity',
                '--split','test' if args.stage=='final-test' else 'validation','--device',args.device)
            if args.stage=='final-test':
                call.extend(['--confirm-final-test','--output-root',runs/f'independent_altreg_math_{backbone}_v3_final_test_identity'])
            tasks.append(call)
        for task in tasks:
            print(json.dumps({'event':'altreg_campaign_command','backbone':backbone,'command':task},sort_keys=True),flush=True)
            if not args.dry_run:
                if not all(isinstance(a,str) for a in task):
                    raise RuntimeError('V3_CAMPAIGN_NONSTRING_SUBPROCESS_ARGUMENT')
                result=runner(task,check=False)
                if result.returncode!=0:raise RuntimeError('V3_CAMPAIGN_STAGE_FAILED:'+str(result.returncode))
    return 0

def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--version',action='version',version=LAUNCHER_VERSION)
    p.add_argument('--stage',choices=('preflight','diagnose','pilot','compare','all','final-test'),default='all')
    p.add_argument('--backbones',nargs='+',choices=BACKBONES,default=['qwen'])
    p.add_argument('--arms',nargs='+',choices=ARMS,default=list(ARMS))
    p.add_argument('--runs-root',type=Path,default=Path.home()/'NanoJev'/'runs')
    p.add_argument('--parent-checkpoint',choices=('best-precision','best-asinh','latest'),default='best-precision')
    p.add_argument('--steps',type=int,default=2000)
    p.add_argument('--additional-steps',type=int,default=None,help='Explicit relative extension, requires --resume and a single existing arm')
    p.add_argument('--batch-size',type=int,default=16)
    p.add_argument('--eval-every',type=int,default=100)
    p.add_argument('--report-every',type=int,default=50)
    p.add_argument('--device',choices=('cuda','cpu','auto'),default='cuda')
    p.add_argument('--dtype',choices=('float16','float32','bfloat16'),default='float16')
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--dry-run',action='store_true')
    return p

if __name__=='__main__':
    a=parser().parse_args()
    if a.steps<0 or (a.additional_steps is not None and a.additional_steps<0) or a.batch_size<1 or a.eval_every<1 or a.report_every<1:
        raise ValueError('V3_CAMPAIGN_INVALID_PARAMETERS')
    raise SystemExit(execute(a))
