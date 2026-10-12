#!/usr/bin/env python3
"""Run all six AntiROM arithmetic compression levels from independent champions.

No hidden optimizer reuse, no synthetic results. Emits machine-readable summary.
Each level trains for EXACTLY the same number of AdamW optimizer steps.
Completed stages can be reused with --resume; partial training fails closed.
"""
from __future__ import annotations
import argparse
import csv
import json
from pathlib import Path
import subprocess
import sys

from nanojev_antirom_compression import (
    LEVELS, DESCRIPTIONS, OUTPUT_ROOT, render, representation, renderer_fingerprint,
)
from nanojev_arithmetic_runtime import DEFAULT_BANK, load_bank, TRAINING_POLICY
from nanojev_arithmetic_residual_micro_overfit import select_fixed_32


HERE = Path(__file__).resolve().parent
SUMMARY_SCHEMA = 'nanojev.antirom-arithmetic-compression-sweep.v1'


def emit(event, **fields):
    print(json.dumps({'event':event, **fields}, sort_keys=True), flush=True)


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def cli(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bank-dir',type=Path,default=DEFAULT_BANK)
    p.add_argument('--output-root',type=Path,default=OUTPUT_ROOT)
    p.add_argument('--max-steps',type=int,default=300)
    p.add_argument('--report-steps',type=int,default=25)
    p.add_argument('--target-loss',type=float,default=.05)
    p.add_argument('--seed',type=int,default=20261010)
    p.add_argument('--split',choices=('dev','predev'),default='dev')
    p.add_argument('--dry-run',action='store_true')
    p.add_argument('--train-only',action='store_true',help='skip independent held-out evaluation')
    p.add_argument('--resume',action='store_true',help='reuse verified completed stages')
    p.add_argument('--allow-model-download',action='store_true')
    args=p.parse_args(argv)
    if args.max_steps<=0 or args.report_steps<=0 or not 0<args.target_loss<1:
        p.error('invalid steps or target')
    return args


def prepared_plan(args, bank):
    examples=select_fixed_32(bank,args.seed)
    groups=dict(bank['groups'])
    gid,variant,_=examples[0]
    t=groups[gid]['target']
    a,b=(t['b'],t['a']) if variant=='reverse' else (t['a'],t['b'])
    return [{'level':level,'description':DESCRIPTIONS[level],
             'representation':representation(level),
             'renderer_sha256':renderer_fingerprint(level),
             'output_dir':str(args.output_root/level),
             'sample_prompt':render(level,a,b,t['threshold'])} for level in LEVELS]


def completed_training(output,level,bank_sha,steps,seed,target):
    """Validate that --resume never silently mixes experiments."""
    state=read_json(output/'state.json')
    experiment=read_json(output/'experiment.json')
    config=experiment['config']
    if (state.get('config')!=config or state.get('bankManifestSha256')!=bank_sha
        or experiment.get('bankManifestSha256')!=bank_sha
        or experiment.get('mode')!='fixed32-antirom-compression-sweep-v1'
        or config.get('representation')!=representation(level)
        or config.get('antirom_level')!=level
        or config.get('renderer_sha256')!=renderer_fingerprint(level)
        or config.get('seed')!=seed or config.get('target_loss')!=target
        or config.get('full_budget') is not True
        or config.get('head_lr')!=1e-4 or config.get('weight_decay')!=.01
        or config.get('grad_clip')!=1.0 or config.get('fixed_examples')!=32
        or state.get('cursor',{}).get('global_step')!=steps):
        raise RuntimeError('ANTIROM_RESUME_TRAINING_IDENTITY_MISMATCH: '+str(output))
    checkpoint=Path(state['checkpoint']).resolve(strict=True)
    if not (checkpoint/'head.safetensors').is_file():
        raise RuntimeError('ANTIROM_RESUME_CHECKPOINT_MISSING: '+str(checkpoint))
    events=[json.loads(line) for line in (output/'events.jsonl').read_text(encoding='utf-8').splitlines()
            if line.strip()]
    completions=[e for e in events if e.get('event')=='arithmetic_fixed32_complete'
                 and e.get('representation')==representation(level)]
    if len(completions)!=1 or completions[0]['steps']!=steps:
        raise RuntimeError('ANTIROM_RESUME_TRAINING_INCOMPLETE: '+str(output))
    return state,events


def summarize_level(level,state,events,bank_sha,baseline,trained):
    measurements=[e for e in events if e.get('event')=='arithmetic_fixed32_loss'
                  and e.get('representation')==representation(level)]
    initial=[e for e in events if e.get('event')=='arithmetic_fixed32_baseline_loss'
             and e.get('representation')==representation(level)]
    final=[e for e in events if e.get('event')=='arithmetic_fixed32_complete'
           and e.get('representation')==representation(level)]
    if len(initial)!=1 or len(final)!=1 or not measurements:
        raise RuntimeError('ANTIROM_MISSING_TRAINING_LOSS_METRICS: '+level)
    best=min([{'step':0,'fixed_loss':initial[0]['loss']}] +
             [{'step':e['step'],'fixed_loss':e['fixed_loss']} for e in measurements],
             key=lambda e:e['fixed_loss'])
    tokens=[e.get('backbones') for e in events if e.get('event')=='rom_arithmetic_visibility_verified']
    if len(tokens)!=1 or not tokens[0]:
        raise RuntimeError('ANTIROM_MISSING_TOKEN_VISIBILITY: '+level)
    row={'level':level,'description':DESCRIPTIONS[level],
         'representation':representation(level),'checkpoint':state['checkpoint'],
         'steps':final[0]['steps'],'initial_fixed_loss':initial[0]['loss'],
         'final_fixed_loss':final[0]['final_fixed_loss'],
         'best_fixed_loss':best['fixed_loss'],'best_step':best['step'],
         'fixed_target_reached':bool(best['fixed_loss']<=final[0]['target_loss']),
         'token_counts':tokens[0],
         'loss_curve':[{'step':0,'fixed_loss':initial[0]['loss']}] +
                      [{'step':e['step'],'fixed_loss':e['fixed_loss']} for e in measurements]}
    if baseline is not None and trained is not None:
        for file,label in ((baseline,'baseline'),(trained,'trained')):
            if (file.get('representation')!=representation(level) or
                file.get('bankManifestSha256')!=bank_sha or not file.get('predictions')):
                raise RuntimeError('ANTIROM_PROBE_IDENTITY_MISMATCH: '+level+' '+label)
        if baseline.get('checkpoint')!='pinned-cycle-136-champion' or trained.get('checkpoint')!=state['checkpoint']:
            raise RuntimeError('ANTIROM_PROBE_CHECKPOINT_MISMATCH: '+level)
        old=baseline['predictions']; new=trained['predictions']
        if old.keys()!=new.keys() or any(old[k]['gold']!=new[k]['gold'] for k in old):
            raise RuntimeError('ANTIROM_PROBE_CASES_DIFFER: '+level)
        row.update({'dev_accuracy_before':baseline['result']['overall']['accuracy'],
                    'dev_accuracy_after':trained['result']['overall']['accuracy'],
                    'dev_cross_entropy_before':baseline['result']['overall']['mean_cross_entropy'],
                    'dev_cross_entropy_after':trained['result']['overall']['mean_cross_entropy'],
                    'dev_examples':len(new)})
        row['dev_accuracy_gain']=row['dev_accuracy_after']-row['dev_accuracy_before']
        row['dev_cross_entropy_reduction']=row['dev_cross_entropy_before']-row['dev_cross_entropy_after']
    return row


def stage(cmd,stage_name,level):
    emit('antirom_sweep_stage_start',level=level,stage=stage_name,cmd=cmd)
    subprocess.run(cmd,check=True)
    emit('antirom_sweep_stage_complete',level=level,stage=stage_name)


def execute(args):
    bank=load_bank(args.bank_dir,split='train')
    output=Path(args.output_root).expanduser().resolve()
    args.output_root=output
    plan=prepared_plan(args,bank)
    if args.dry_run:
        emit('antirom_sweep_plan',ok=True,no_training=True,bankManifestSha256=bank['manifest_sha256'],
             training_policy=TRAINING_POLICY,levels=plan,max_steps=args.max_steps,
             seed=args.seed,split=args.split,train_only=args.train_only)
        return
    if output.exists() and any(output.iterdir()) and not args.resume:
        raise RuntimeError('ANTIROM_SWEEP_OUTPUT_NOT_EMPTY: '+str(output)+' (use --resume)')
    output.mkdir(parents=True,exist_ok=True)
    result_dir=output/'results'
    result_dir.mkdir(exist_ok=True)
    rows=[]
    # Every invocation of micro-overfit starts from the SAME pinned champion;
    # it does not accept an inherited checkpoint.
    for level in LEVELS:
        training_dir=output/level
        if training_dir.exists() and any(training_dir.iterdir()):
            if not args.resume:
                raise RuntimeError('ANTIROM_SWEEP_OUTPUT_COLLISION: '+str(training_dir))
            state,events=completed_training(training_dir,level,bank['manifest_sha256'],
                                             args.max_steps,args.seed,args.target_loss)
            emit('antirom_sweep_reused_training',level=level,step=state['cursor']['global_step'])
        else:
            cmd=[sys.executable,str(HERE/'nanojev_arithmetic_residual_micro_overfit.py'),
                 '--bank-dir',str(args.bank_dir),'--output-dir',str(training_dir),
                 '--antirom-level',level,'--full-budget','--max-steps',str(args.max_steps),
                 '--report-steps',str(args.report_steps),'--target-loss',str(args.target_loss),
                 '--seed',str(args.seed)]
            if args.allow_model_download: cmd+=['--allow-model-download']
            stage(cmd,'training',level)
            state,events=completed_training(training_dir,level,bank['manifest_sha256'],
                                             args.max_steps,args.seed,args.target_loss)
        baseline=trained=None
        if not args.train_only:
            baseline_path=result_dir/(level+'.champion.'+args.split+'.json')
            trained_path=result_dir/(level+'.trained.'+args.split+'.json')
            for which,path in (('champion',baseline_path),('trained',trained_path)):
                if path.exists() and not args.resume:
                    raise RuntimeError('ANTIROM_SWEEP_PROBE_FILE_EXISTS: '+str(path))
                if not path.exists():
                    cmd=[sys.executable,str(HERE/'nanojev_arithmetic_probe.py'),
                         '--bank-dir',str(args.bank_dir),'--antirom-level',level,
                         '--split',args.split,'--results-file',str(path)]
                    if which=='champion': cmd+=['--champion-only']
                    else: cmd+=['--training-dir',str(training_dir),'--compare-to',str(baseline_path)]
                    if args.allow_model_download: cmd+=['--allow-model-download']
                    stage(cmd,which+'-'+args.split+'-probe',level)
            baseline=read_json(baseline_path)
            trained=read_json(trained_path)
            if baseline.get('split')!=args.split or trained.get('split')!=args.split:
                raise RuntimeError('ANTIROM_SWEEP_PROBE_SPLIT_MISMATCH: '+level)
        row=summarize_level(level,state,events,bank['manifest_sha256'],baseline,trained)
        rows.append(row)
        emit('antirom_sweep_level_result',**{k:v for k,v in row.items() if k not in ('loss_curve','token_counts')})
    if len(rows)!=len(LEVELS):
        raise RuntimeError('ANTIROM_INCOMPLETE_LEVEL_SET')
    # The held-out split is identical for each presentation level, not independently sampled.
    summary={'schema':SUMMARY_SCHEMA,'bankManifestSha256':bank['manifest_sha256'],
             'training_policy':TRAINING_POLICY,'seed':args.seed,'max_steps':args.max_steps,
             'target_loss':args.target_loss,'eval_split':None if args.train_only else args.split,
             'levels':rows}
    summary_path=output/'summary.json'
    if summary_path.exists() and not args.resume:
        raise RuntimeError('ANTIROM_SWEEP_SUMMARY_EXISTS')
    summary_path.write_text(json.dumps(summary,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    csv_path=output/'summary.csv'
    columns=['level','steps','initial_fixed_loss','best_fixed_loss','best_step',
             'final_fixed_loss','fixed_target_reached','dev_accuracy_before','dev_accuracy_after',
             'dev_accuracy_gain','dev_cross_entropy_before','dev_cross_entropy_after',
             'dev_cross_entropy_reduction', 'dev_examples']
    with csv_path.open('w',encoding='utf-8',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=columns,extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    emit('antirom_sweep_complete',summary=str(summary_path),csv=str(csv_path),levels=len(rows),
         eval_split=summary['eval_split'])


if __name__=='__main__':
    try:
        execute(cli())
    except Exception as exc:
        emit('antirom_sweep_error',error=str(exc))
        raise SystemExit(1)
