#!/usr/bin/env python3
"""Read-only neural V10 XY lattice probe; no training or weight changes.

Use the SAME held-out mathematical cases for all nine assistance nodes at each
selected tier. The combined stages are compared with X-first and Y-first paths.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools import nanojev_deterministic_brain_v10 as brain
from tools import nanojev_game_math_curriculum_v10_runtime as rt
from tools import nanojev_game_math_curriculum_v10_policy as policy


def make_parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--teacher-db',type=Path,default=rt.DEFAULT_TEACHER)
    p.add_argument('--source-checkpoint',type=Path,default=rt.DEFAULT_SOURCE)
    p.add_argument('--checkpoint',type=Path,default=rt.DEFAULT_PARENT,
                   help='V9 latest.pt, V2.1 latest.pt, or V10 latest.pt')
    p.add_argument('--output',type=Path,default=rt.DEFAULT_OUTPUT/'baseline-probe.json')
    p.add_argument('--split',choices=('dev','test'),default='dev')
    p.add_argument('--limit',type=int,default=48)
    p.add_argument('--tiers',choices=brain.TIERS,nargs='+',
                   help='Probe named tiers only; otherwise all bridge and lattice stages')
    p.add_argument('--nodes',choices=rt.ALL_NODES,nargs='+',
                   help='Select grammar bridge / assistance nodes; otherwise all available stages')
    p.add_argument('--batch-size',type=int,default=4)
    policy.add_gate_args(p)
    p.add_argument('--min-count',type=int,default=16)
    p.add_argument('--seed',type=int,default=20261017)
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--dtype',choices=('float16','bfloat16','float32'),default='float16')
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--teacher-info',action='store_true',
                   help='Read-only metadata check without loading any model')
    return p


def run(args: argparse.Namespace) -> dict:
    policy.resolve_gate(args)
    meta=rt.teacher_preflight(args.teacher_db)
    if args.teacher_info:
        return {'status':'PASS','teacher':meta,'model_loaded':False}
    stages=rt.stage_list(args.tiers,args.nodes)
    cases=rt.read_cases(args.teacher_db,args.split,limit=args.limit)
    if len(cases)<args.min_count:
        raise ValueError('V10_PROBE_NOT_ENOUGH_HELDOUT_CASES')
    model,cp,checkpoint_sha=rt.load_model(args.source_checkpoint,args.checkpoint,
                 device=args.device,dtype=args.dtype,seed=args.seed,
                 allow_model_download=args.allow_model_download,
                 expected_teacher_sha=meta['teacher_sha256'])
    report=rt.evaluate(cases,stages,lambda ps:rt.batch_predict(model,ps),
                       lambda p:rt.token_eligible(model,p),batch_size=args.batch_size,
                       gate_fraction=args.gate_fraction,min_count=args.min_count)
    policy.attach_diagnostics(report,args.min_count)
    report.update({'split':args.split,'step':int(cp.get('step',0)),
                   'checkpoint_sha256':checkpoint_sha,
                   'teacher_sha256':meta['teacher_sha256'],
                   'parent_kind':cp.get('schema','legacy'), 'device':args.device,
                   'Z_loss_weight':0})
    rt.save_json(args.output,report)
    return {'status':'PASS','report':str(args.output.resolve()),
            'advancement_percent':report['advancement_percent'],
            'diagnostic_gates':{k:{'passing':v['passing'],'total':v['total']}
                                for k,v in report['diagnostic_gates'].items()},
            'first_failing':report['first_failing'],
            'first_failing_in_selection':report['first_failing_in_selection'],
            'cases':len(cases),'stages_evaluated':len(stages),
            'all_stages_probed':report['all_stages_probed'],
            'unscorable_stages':{s:m['invalid'] for s,m in report['scores'].items() if m['invalid']},
            'scores':{s:{'x':round(m['x'],4),'y':round(m['y'],4),
                          'joint':round(m['joint_xy'],4),'coupled_Z':round(m['coupled_Z'],4),
                          'scorable':m['scorable'],
                          'gate':'PASS' if rt.passed(m,args.gate_fraction,args.min_count) else 'FAIL',
                          'n':m['n']} for s,m in report['scores'].items()}}


def format_console(summary:dict) -> str:
    if 'scores' not in summary:
        return json.dumps(summary,sort_keys=True)
    lines=[f"V10 probe: {summary['cases']} paired dev/test cases, "
           f"{summary['stages_evaluated']} stages",
           f"Joint X/Y advancement gate >={summary['advancement_percent']:g}% within 5%; coupled Z DIAGNOSTIC ONLY",
           f"{'Stage':21s} {'N':>4s} {'X':>8s} {'Y':>8s} {'Joint':>8s} {'Z diag':>8s} Gate",
           '-'*79]
    for stage,score in summary['scores'].items():
        lines.append(f"{stage:21s} {score['n']:4d} {score['x']:8.1%} "
                     f"{score['y']:8.1%} {score['joint']:8.1%} "
                     f"{score['coupled_Z']:8.1%} {score['gate']}")
    gates=summary['diagnostic_gates']
    lines.append('Diagnostic passing stages (70/80/90/95%): '+', '.join(
        f"{k}%={v['passing']}/{v['total']}" for k,v in gates.items()))
    lines.extend(['',f"First failing in selection: {summary['first_failing_in_selection']}",
                  f"Global first failing: {summary['first_failing'] if summary['all_stages_probed'] else 'NOT ASSESSED (partial probe)'}",
                  f"Full paired-edge report: {summary['report']}"])
    return '\n'.join(lines)


def main(argv:list[str]|None=None)->int:
    args=make_parser().parse_args(argv)
    result=run(args)
    print(format_console(result),flush=True)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
