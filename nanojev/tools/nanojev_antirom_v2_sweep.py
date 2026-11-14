#!/usr/bin/env python3
"""Full six-way AntiROM v2: sealed fresh bank, 72k unique-forward exposures total.

One 12k-problem sealed stream per level, 3000 independent optimizer steps,
independent DEV checkpoint probes at 0/1000/2000/3000 and final OOD control.
"""
from __future__ import annotations
import argparse
import csv
import json
from pathlib import Path
import subprocess
import sys

from nanojev_antirom_compression import LEVELS, DESCRIPTIONS,representation,renderer_fingerprint
from nanojev_antirom_v2_stream_bank import DEFAULT_OUTPUT as BANK_OUTPUT, DEFAULT_SEED, create,load
from nanojev_antirom_v2_fresh_train import DEFAULT_OUTPUT as SWEEP_OUTPUT, SCHEMA as TRAIN_SCHEMA
from nanojev_arithmetic_runtime import DEFAULT_BANK,load_bank

SCHEMA='nanojev.antirom-fresh-sweep.v2'
HERE=Path(__file__).resolve().parent


def parse(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bank-dir',type=Path,default=BANK_OUTPUT)
    p.add_argument('--legacy-bank-dir',type=Path,default=DEFAULT_BANK)
    p.add_argument('--output-root',type=Path,default=SWEEP_OUTPUT)
    p.add_argument('--max-steps',type=int,default=3000)
    p.add_argument('--grad-accumulation',type=int,default=4)
    p.add_argument('--checkpoint-steps',type=int,default=1000)
    p.add_argument('--report-steps',type=int,default=100)
    p.add_argument('--ood-count',type=int,default=600)
    p.add_argument('--seed',type=int,default=DEFAULT_SEED)
    p.add_argument('--resume',action='store_true')
    p.add_argument('--train-only',action='store_true')
    p.add_argument('--dry-run',action='store_true')
    p.add_argument('--allow-model-download',action='store_true')
    a=p.parse_args(argv)
    if (a.max_steps<1 or a.grad_accumulation<1 or a.checkpoint_steps<1
        or a.max_steps%a.checkpoint_steps!=0 or a.report_steps<1
        or a.max_steps*a.grad_accumulation%3!=0 or a.ood_count<3 or a.ood_count%3):
        p.error('ANTIROM_V2_INVALID_CONTROL: exact class balance and checkpoint divisibility required')
    return a


def emit(event,**fields):
    print(json.dumps({'event':event,**fields},sort_keys=True),flush=True)


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def stage(cmd,kind,level):
    emit('antirom_v2_stage_start',stage=kind,level=level,cmd=cmd)
    subprocess.run(cmd,check=True)
    emit('antirom_v2_stage_complete',stage=kind,level=level)


def completed_train(run,level,bank,steps,batch,seed):
    state=read_json(run/'state.json')
    exp=read_json(run/'experiment.json')
    config=exp.get('config',{})
    cp=Path(state['checkpoint']).resolve(strict=True)
    if (exp.get('schema')!=TRAIN_SCHEMA or exp.get('mode')!='fresh-unique-training-no-reexposure'
        or exp.get('bankManifestSha256')!=bank['bank_sha256'] or
        state.get('bankManifestSha256')!=bank['bank_sha256']
        or state.get('config')!=config or config.get('bank_sha256')!=bank['bank_sha256']
        or config.get('renderer_sha256')!=renderer_fingerprint(level)
        or config.get('representation')!=representation(level)
        or config.get('level')!=level or config.get('max_steps')!=steps
        or config.get('grad_accumulation')!=batch or config.get('seed')!=seed
        or config.get('training_policy')!='cycle136-frozen-core-six-tinystories-residual-adapters-adamw-v2'
        or config.get('head_lr')!=1e-4 or config.get('weight_decay')!=.01
        or state['cursor']['global_step']!=steps or
        state['cursor']['examples_seen']!=steps*batch
        or cp.parent!=(run/'checkpoints').resolve()
        or not (cp/'head.safetensors').is_file()):
        raise RuntimeError('ANTIROM_V2_TRAIN_RESULT_IDENTITY_MISMATCH: '+str(run))
    meta=read_json(cp/'meta.json')
    if (meta['config']!=config or meta['cursor']!=state['cursor']
        or meta['bankManifestSha256']!=bank['bank_sha256']):
        raise RuntimeError('ANTIROM_V2_LAST_CHECKPOINT_MISMATCH')
    lines=[json.loads(x) for x in (run/'events.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()]
    progress=[x for x in lines if x.get('event')=='antirom_v2_online_training_ce' and x.get('level')==level]
    visibility=[x for x in lines if x.get('event')=='antirom_v2_prompt_visibility' and x.get('level')==level]
    if not progress or not visibility or progress[-1]['step']!=steps:
        raise RuntimeError('ANTIROM_V2_MISSING_TRAINING_EVENTS: '+str(run))
    return {'level':level,'description':DESCRIPTIONS[level],
            'steps':steps,'seen':steps*batch,'checkpoint':str(cp),
            'training_ce_final':progress[-1]['rolling_ce'],
            'training_ce_curve':[{'step':p['step'],'seen':p['seen'],
                                  'rolling_ce':p['rolling_ce']} for p in progress],
            'token_counts':visibility[-1]['backbones']}


def probe_identity(path,level,split,step,bank,expected_count,run_root=None):
    data=read_json(path)
    if (data.get('schema')!='nanojev.antirom-fresh-independent-probe.v2'
        or data.get('level')!=level or data.get('split')!=split or data.get('step')!=step
        or data.get('representation')!=representation(level)
        or data.get('training_bank_sha256')!=bank['bank_sha256']
        or data.get('eval_sha256')!=(bank['legacy_bank_sha256'] if split=='dev' else bank['bank_sha256'])
        or data.get('overall',{}).get('examples')!=expected_count or
        len(data.get('predictions',{}))!=expected_count or data.get('weights_modified') is not False):
        raise RuntimeError('ANTIROM_V2_PROBE_RESULT_IDENTITY_MISMATCH: '+str(path))
    if (step==0)!=(data.get('checkpoint')=='pinned-cycle-136-champion'):
        raise RuntimeError('ANTIROM_V2_PROBE_CHECKPOINT_IDENTITY_MISMATCH')
    if run_root is not None and step!=0:
        expected=(Path(run_root)/level/'checkpoints'/f'step-{step:09d}').resolve()
        if Path(data['checkpoint']).resolve()!=expected:
            raise RuntimeError('ANTIROM_V2_PROBE_CHECKPOINT_PATH_MISMATCH')
    return data


# Keep the CSV schema aligned with both the DEV and OOD per-level metric rows.
SUMMARY_CSV_FIELDS=['level','steps','seen','training_ce_final',
    'dev_accuracy_before','dev_accuracy_after','dev_ce_before','dev_ce_after',
    'ood_accuracy_before','ood_accuracy_after','ood_ce_before','ood_ce_after']


def write_summary_csv(path, summary):
    """Render a complete summary atomically, including both split CE columns.

    Also repairs a partial CSV left by the previous post-training crash.
    """
    path=Path(path)
    temporary=path.with_name(path.name+'.tmp')
    with temporary.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=SUMMARY_CSV_FIELDS)
        w.writeheader()
        for row in summary:
            flat={key:row.get(key) for key in SUMMARY_CSV_FIELDS}
            for split in ('dev','ood'):
                if split in row:
                    flat[f'{split}_accuracy_before']=row[split]['baseline']['accuracy']
                    flat[f'{split}_accuracy_after']=row[split]['final']['accuracy']
                    flat[f'{split}_ce_before']=row[split]['baseline']['mean_cross_entropy']
                    flat[f'{split}_ce_after']=row[split]['final']['mean_cross_entropy']
            w.writerow(flat)
    temporary.replace(path)


def execute(args):
    output=Path(args.output_root).expanduser().resolve()
    bank_dir=Path(args.bank_dir).expanduser().resolve()
    total=args.max_steps*args.grad_accumulation
    if args.dry_run:
        emit('antirom_v2_sweep_plan',schema=SCHEMA,output=str(output),bank_dir=str(bank_dir),
          source_bank=str(args.legacy_bank_dir),levels=list(LEVELS),
          max_steps=args.max_steps,grad_accumulation=args.grad_accumulation,
          train_unique_cases_per_level=total,ood_count=args.ood_count,
          dev_source='original sealed v1 dev (216 presentations)',
          dev_probe_steps=[0]+list(range(args.checkpoint_steps,args.max_steps+1,args.checkpoint_steps)),
          ood_probe_steps=[0,args.max_steps],train_only=args.train_only,
          no_training=True)
        return
    if not (bank_dir/'manifest.json').is_file():
        emit('antirom_v2_bank_generation_start',train_count=total,ood_count=args.ood_count)
        create(output=bank_dir,seed=args.seed,count=total,ood_count=args.ood_count,
               source_bank=args.legacy_bank_dir)
    bank=load(bank_dir=bank_dir,legacy_bank_dir=args.legacy_bank_dir,split='train',expected_count=total)
    if (bank['manifest']['seed']!=args.seed or bank['manifest']['ood_count']!=args.ood_count):
        raise RuntimeError('ANTIROM_V2_SWEEP_BANK_CONFIGURATION_MISMATCH')
    if output.exists() and any(output.iterdir()) and not args.resume:
        raise RuntimeError('ANTIROM_V2_SWEEP_OUTPUT_NOT_EMPTY: '+str(output)+' (use --resume)')
    output.mkdir(parents=True,exist_ok=True)
    results=output/'results';results.mkdir(exist_ok=True)
    summary=[]
    dev_count=load_bank(args.legacy_bank_dir,split='dev')['count']*2 if not args.train_only else 0
    probe_steps=[0]+list(range(args.checkpoint_steps,args.max_steps+1,args.checkpoint_steps))
    for level in LEVELS:
        run=output/level
        complete=(run/'state.json').is_file() and read_json(run/'state.json')['cursor']['global_step']==args.max_steps
        if not complete:
            if run.exists() and any(run.iterdir()) and not args.resume:
                raise RuntimeError('ANTIROM_V2_LEVEL_OUTPUT_NOT_EMPTY: '+str(run))
            command=[sys.executable,str(HERE/'nanojev_antirom_v2_fresh_train.py'),
              '--bank-dir',str(bank_dir),'--legacy-bank-dir',str(args.legacy_bank_dir),
              '--output-root',str(output),'--level',level,'--seed',str(args.seed),
              '--max-steps',str(args.max_steps),'--grad-accumulation',str(args.grad_accumulation),
              '--report-steps',str(args.report_steps),'--checkpoint-steps',str(args.checkpoint_steps)]
            if run.exists() and any(run.iterdir()):
                if not (run/'state.json').is_file():
                    raise RuntimeError('ANTIROM_V2_UNCHECKPOINTED_PARTIAL_RUN: '+str(run))
                command+=['--resume']
            if args.allow_model_download:command+=['--allow-model-download']
            stage(command,'train',level)
        else:
            if not args.resume:raise RuntimeError('ANTIROM_V2_LEVEL_ALREADY_COMPLETE: '+level)
            emit('antirom_v2_reused_training',level=level)
        row=completed_train(run,level,bank,args.max_steps,args.grad_accumulation,args.seed)
        if not args.train_only:
            for split,steps,n in [('dev',probe_steps,dev_count),('ood',[0,args.max_steps],args.ood_count)]:
                snapshots={}
                for step in steps:
                    path=results/f'{level}.{split}.step-{step:07d}.json'
                    if not path.exists():
                        command=[sys.executable,str(HERE/'nanojev_antirom_v2_independent_probe.py'),
                            '--bank-dir',str(bank_dir),'--legacy-bank-dir',str(args.legacy_bank_dir),
                            '--output-root',str(output),'--level',level,'--seed',str(args.seed),
                            '--split',split,'--step',str(step),'--results-file',str(path)]
                        if args.allow_model_download:command+=['--allow-model-download']
                        stage(command,f'probe-{split}-step-{step}',level)
                    snapshots[step]=probe_identity(path,level,split,step,bank,n,output)
                first=snapshots[0]['overall'];last=snapshots[args.max_steps]['overall']
                row[split]={'baseline':first,'final':last,
                    'accuracy_gain':last['accuracy']-first['accuracy'],
                    'cross_entropy_reduction':first['mean_cross_entropy']-last['mean_cross_entropy'],
                    'learning_curve':[{'step':step,**snapshots[step]['overall']} for step in steps]}
        summary.append(row)
        emit('antirom_v2_level_complete',level=level,online_ce=row['training_ce_final'],
             dev=row.get('dev'),ood=row.get('ood'))
    if len(summary)!=6:
        raise RuntimeError('ANTIROM_V2_SWEEP_INCOMPLETE')
    payload={'schema':SCHEMA,'bank_sha256':bank['bank_sha256'],
             'prior_bank_sha256':bank['legacy_bank_sha256'],
             'max_steps':args.max_steps,'batch':args.grad_accumulation,'seed':args.seed,
             'train_cases_per_level':total,'levels':summary}
    from nanojev_arithmetic_continuous_train import atomic_json
    atomic_json(output/'summary.json',payload)
    write_summary_csv(output/'summary.csv',summary)
    emit('antirom_v2_sweep_complete',summary=str(output/'summary.json'),
          csv=str(output/'summary.csv'),levels=len(summary))


if __name__=='__main__':
    try:execute(parse())
    except Exception as e:
        emit('antirom_v2_sweep_error',error=str(e))
        raise SystemExit(1)
