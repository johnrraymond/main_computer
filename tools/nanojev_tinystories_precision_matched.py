#!/usr/bin/env python3
"""Launch independent, matched TinyStories precision-v2 control/treatment arms.

No model training logic is modified. Each fresh arm forks the same v1 best.pt;
existing v2 or v4 checkpoints are not used as parents. Run arms separately to
avoid competing for a GPU. Checkpoint identity is verified before resume.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ARMS = {'control': 0.0, 'treatment': 0.01}
TRAINER = 'nanojev_altreg_independent_math_tinystories_precision_v2.py'

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def arm_dir(root, arm):
    return Path(root) / ('weight0_control' if arm == 'control' else 'weight001_treatment')

def lineage_marker(root, arm):
    # Sidecar identity must not occupy the trainer's fresh output directory.
    return Path(root) / '.matched-lineage' / (arm + '.json')

def default_root():
    return Path.home() / 'NanoJev' / 'runs' / 'independent_altreg_math_tinystories_matched_v2'

def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--arm', choices=tuple(ARMS), required=True)
    p.add_argument('--results-root', type=Path, default=default_root())
    p.add_argument('--fork-v1-root', type=Path, default=Path.home()/'NanoJev'/'runs'/'independent_altreg_math_tinystories_v1')
    p.add_argument('--steps', type=int, default=10000)
    p.add_argument('--resume', action='store_true')
    p.add_argument('--additional-steps', type=int, default=1000)
    p.add_argument('--seed', type=int, default=20261013)
    p.add_argument('--val-examples', type=int, default=128)
    p.add_argument('--test-examples', type=int, default=128)
    p.add_argument('--device', choices=('cuda','cpu','auto'), default='cuda')
    p.add_argument('--dtype', choices=('float16','bfloat16','float32','auto'), default='float16')
    p.add_argument('--eval-every', type=int, default=100)
    p.add_argument('--report-every', type=int, default=50)
    p.add_argument('--allow-model-download', action='store_true')
    p.add_argument('--dry-run', action='store_true')
    return p

def preflight(args, trainer_path):
    if not trainer_path.is_file():
        raise FileNotFoundError('TINY_MATCH_TRAINER_MISSING:'+str(trainer_path))
    if args.steps < 1 or args.additional_steps < 1 or min(args.val_examples,args.test_examples,args.eval_every,args.report_every)<1:
        raise ValueError('TINY_MATCH_INVALID_COUNT')
    source = args.fork_v1_root.expanduser().resolve()
    parent = source/'best.pt'
    cache = source/'validation-cache.pt'
    if not parent.is_file() or not cache.is_file():
        raise FileNotFoundError('TINY_MATCH_V1_SOURCE_MISSING:'+str(source))
    output = arm_dir(args.results_root.expanduser().resolve(), args.arm)
    lineage = {'arm':args.arm,'parent_best_sha256':digest(parent),'parent_cache_sha256':digest(cache),
               'seed':args.seed,'val_examples':args.val_examples,'test_examples':args.test_examples,
               'precision_weight':ARMS[args.arm], 'v1_root':str(source)}
    marker = lineage_marker(args.results_root.expanduser().resolve(), args.arm)
    if args.resume:
        if not (output/'latest.pt').is_file() or not marker.is_file():
            raise RuntimeError('TINY_MATCH_RESUME_STATE_MISSING:'+str(output))
        saved = json.loads(marker.read_text(encoding='utf-8'))
        if saved != lineage:
            raise RuntimeError('TINY_MATCH_RESUME_LINEAGE_DRIFT')
    elif output.exists() and any(output.iterdir()):
        raise RuntimeError('TINY_MATCH_OUTPUT_NOT_EMPTY:'+str(output))
    cmd=[sys.executable,str(trainer_path),'--results-root',str(output),'--fork-v1-root',str(source),
         '--precision-weight',str(ARMS[args.arm]),'--seed',str(args.seed),
         '--val-examples',str(args.val_examples),'--test-examples',str(args.test_examples),
         '--device',args.device,'--dtype',args.dtype,'--eval-every',str(args.eval_every),
         '--report-every',str(args.report_every)]
    if args.resume:
        cmd += ['--resume','--additional-steps',str(args.additional_steps)]
    else:
        cmd += ['--steps',str(args.steps)]
    if args.allow_model_download:cmd += ['--allow-model-download']
    return output,lineage,cmd

def main(argv=None):
    args=parser().parse_args(argv)
    trainer=Path(__file__).resolve().with_name(TRAINER)
    output,lineage,cmd=preflight(args,trainer)
    print(json.dumps({'event':'tinystories_matched_preflight','lineage':lineage,'output':str(output),
                      'command':cmd,'dry_run':args.dry_run},sort_keys=True),flush=True)
    if args.dry_run:return 0
    # Persist the lineage outside the output directory. The underlying trainer
    # rejects any nonempty results directory on a fresh fork.
    marker = lineage_marker(args.results_root.expanduser().resolve(), args.arm)
    if not args.resume:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps(lineage,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    return subprocess.call(cmd)

if __name__=='__main__':raise SystemExit(main())
