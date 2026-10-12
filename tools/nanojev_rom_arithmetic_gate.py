#!/usr/bin/env python3
"""Promotion gate for the isolated English-like ROM arithmetic experiment.

Does not train, generate examples, or declare success from training loss alone.
Requires fixed-32 convergence AND a better independent held-out DEV probe than
this same ROM representation's pinned champion baseline.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import traceback

from nanojev_rom_arithmetic_language import ROM_OUTPUT, REPRESENTATION, representation_fingerprint


def _read(path:Path):
    return json.loads(path.read_text(encoding='utf-8'))


def evaluate(*,output:Path,baseline_file:Path,trained_file:Path,
             min_accuracy_gain:float=0.05,require_lower_ce:bool=True):
    output=Path(output).expanduser().resolve(strict=True)
    exp=_read(output/'experiment.json')
    state=_read(output/'state.json')
    config=exp.get('config',{})
    if (exp.get('mode')!='fixed32-rom-arithmetic-language-control'
        or config.get('representation')!=REPRESENTATION
        or config.get('renderer_sha256')!=representation_fingerprint()
        or state.get('config')!=config or state.get('bankManifestSha256')!=exp.get('bankManifestSha256')):
        raise RuntimeError('ROM_ARITH_GATE_RUN_IDENTITY_MISMATCH')
    checkpoint=Path(state['checkpoint']).resolve(strict=True)
    if not (checkpoint/'head.safetensors').is_file():
        raise RuntimeError('ROM_ARITH_GATE_CHECKPOINT_MISSING')
    event_rows=[json.loads(line) for line in (output/'events.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    completed=[e for e in event_rows if e.get('event')=='arithmetic_fixed32_complete'
               and e.get('representation')==REPRESENTATION]
    if not completed:
        raise RuntimeError('ROM_ARITH_GATE_TRAINING_NOT_COMPLETE')
    completion=completed[-1]
    baseline=_read(baseline_file)
    trained=_read(trained_file)
    for item in (baseline,trained):
        if (item.get('representation')!=REPRESENTATION or item.get('split')!='dev'
                or item.get('bankManifestSha256')!=exp.get('bankManifestSha256')
                or item.get('result',{}).get('representation')!=REPRESENTATION):
            raise RuntimeError('ROM_ARITH_GATE_PROBE_IDENTITY_MISMATCH')
    if baseline.get('checkpoint')!='pinned-cycle-136-champion':
        raise RuntimeError('ROM_ARITH_GATE_BASELINE_MUST_BE_CHAMPION')
    if trained.get('checkpoint')!=str(checkpoint):
        raise RuntimeError('ROM_ARITH_GATE_PROBE_CHECKPOINT_MISMATCH')
    bp=baseline.get('predictions',{})
    tp=trained.get('predictions',{})
    if not bp or bp.keys()!=tp.keys():
        raise RuntimeError('ROM_ARITH_GATE_DEV_CASES_MISMATCH')
    for key in bp:
        if bp[key]['gold']!=tp[key]['gold']:
            raise RuntimeError('ROM_ARITH_GATE_DEV_GOLD_DRIFT')
    a=baseline['result']['overall']
    b=trained['result']['overall']
    acc_gain=float(b['accuracy'])-float(a['accuracy'])
    ce_gain=float(a['mean_cross_entropy'])-float(b['mean_cross_entropy'])
    fixed_loss=float(completion['final_fixed_loss'])
    target=float(completion['target_loss'])
    memorized=bool(completion['loss_target_reached']) and fixed_loss<=target
    generalized=acc_gain>=min_accuracy_gain and (not require_lower_ce or ce_gain>0)
    return {'event':'rom_arithmetic_promotion_gate','representation':REPRESENTATION,
            'passed':memorized and generalized,'fixed32_loss':fixed_loss,
            'fixed32_target':target,'fixed32_passed':memorized,
            'dev_accuracy_champion':a['accuracy'],'dev_accuracy_trained':b['accuracy'],
            'dev_accuracy_gain':acc_gain,'minimum_accuracy_gain':min_accuracy_gain,
            'dev_cross_entropy_champion':a['mean_cross_entropy'],
            'dev_cross_entropy_trained':b['mean_cross_entropy'],
            'dev_cross_entropy_reduction':ce_gain,'dev_generalization_passed':generalized,
            'dev_questions':len(tp),'checkpoint':str(checkpoint),
            'next_stage':'teach-one-ROM-primitive' if memorized and generalized else 'do-not-promote'}


def parse(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--training-dir',type=Path,default=ROM_OUTPUT)
    p.add_argument('--baseline-results',required=True,type=Path)
    p.add_argument('--trained-results',required=True,type=Path)
    p.add_argument('--min-accuracy-gain',type=float,default=.05,
                   help='minimum held-out absolute accuracy improvement (default: 5 percentage points)')
    args=p.parse_args(argv)
    if not 0<args.min_accuracy_gain<=1:
        p.error('--min-accuracy-gain must be in (0,1]')
    return args


def main(argv=None):
    args=parse(argv)
    row=evaluate(output=args.training_dir,baseline_file=args.baseline_results,
                 trained_file=args.trained_results,min_accuracy_gain=args.min_accuracy_gain)
    print(json.dumps(row,sort_keys=True),flush=True)
    return 0 if row['passed'] else 2


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({'event':'rom_arithmetic_gate_error','error':str(exc),
                          'traceback':traceback.format_exc()},sort_keys=True),file=sys.stderr)
        raise SystemExit(1)
