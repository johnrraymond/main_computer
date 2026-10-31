"""Deterministic, checkpoint-compatible convergence for Qwen Phase 2.

No import hooks, monkeypatching, or changes to optimizer/model state. The mixed
trainer calls evaluate() *after* a complete fixed-deck evaluation and uses the
returned `converged` flag, instead of the legacy plateau Boolean, to finalize.

The original `mixed_history.jsonl` is the authoritative source of all prior
completed evaluations; existing v1 robust-sidecar entries may supply missing
older checks. A working.pt resumed from an earlier step never trusts future
sidecar entries. The current evaluation always wins over a stale sidecar row.
"""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
import statistics
import tempfile

SIDECAR='robust_plateau_history.json'
VERSION='qwen-mixed-robust-plateau.v2'
WINDOW=5
MIN_RELATIVE_GAIN=.02
STALE_CHECKS=4


def _write_json(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.'+path.name,suffix='.tmp')
    try:
        with os.fdopen(fd,'w',encoding='utf8') as f:
            json.dump(obj,f,sort_keys=True,indent=2,allow_nan=False)
            f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def _normalize(record):
    try:
        step=int(record['step'])
        loss=float(record['macro_reference_loss'])
    except (KeyError,TypeError,ValueError) as exc:
        raise RuntimeError('ROBUST_PLATEAU_INVALID_HISTORY_RECORD') from exc
    if step<0 or not math.isfinite(loss) or loss<0:
        raise RuntimeError('ROBUST_PLATEAU_INVALID_HISTORY_VALUE')
    return {'step':step,'macro_reference_loss':loss}


def _read_legacy_history(path,stop_before):
    rows={}
    if path.is_file():
        all_lines=path.read_text(encoding='utf8').splitlines()
        for line_num,line in enumerate(all_lines,1):
            if not line.strip():continue
            try:obj=json.loads(line)
            except json.JSONDecodeError as exc:
                # Log append can be interrupted; ignore only a final truncated line.
                if line_num==len(all_lines):break
                raise RuntimeError('ROBUST_PLATEAU_HISTORY_CORRUPT') from exc
            row=_normalize(obj)
            if row['step']<stop_before:
                earlier=rows.get(row['step'])
                if earlier and not math.isclose(earlier['macro_reference_loss'],row['macro_reference_loss'],rel_tol=1e-6,abs_tol=1e-7):
                    raise RuntimeError('ROBUST_PLATEAU_HISTORY_CONFLICT')
                rows[row['step']]=row
    return rows


def _read_sidecar(path,stop_before,parent_sha):
    if not path.exists():return {},False
    obj=json.loads(path.read_text(encoding='utf8'))
    if obj.get('schema') not in (VERSION,'qwen-mixed-robust-plateau.v1'):
        raise RuntimeError('ROBUST_PLATEAU_SIDECAR_SCHEMA_INVALID')
    if obj.get('parent_sha256') not in (None,parent_sha):
        raise RuntimeError('ROBUST_PLATEAU_PARENT_LINEAGE_DRIFT')
    if not isinstance(obj.get('checks'),list):
        raise RuntimeError('ROBUST_PLATEAU_SIDECAR_CHECKS_INVALID')
    rows={}
    for raw in obj['checks']:
        row=_normalize(raw)
        if row['step']<stop_before:
            earlier=rows.get(row['step'])
            if earlier and not math.isclose(earlier['macro_reference_loss'],row['macro_reference_loss'],rel_tol=1e-6,abs_tol=1e-7):
                raise RuntimeError('ROBUST_PLATEAU_SIDECAR_CONFLICT')
            rows[row['step']]=row
    return rows,True


def _recompute(rows,window=WINDOW,min_relative_gain=MIN_RELATIVE_GAIN,stale_checks=STALE_CHECKS):
    best=None;stale=0;rolling_mean=None;rolling_median=None
    for index in range(window-1,len(rows)):
        values=[row['macro_reference_loss'] for row in rows[index+1-window:index+1]]
        current=statistics.median(values)
        rolling_median=current
        rolling_mean=statistics.fmean(values)
        if best is None or current<best*(1-min_relative_gain):
            best=current;stale=0
        else:
            stale+=1
    return {'best_rolling_median':best,'rolling_median':rolling_median,
            'rolling_mean':rolling_mean,'stale':stale,
            'converged':bool(best is not None and stale>=stale_checks)}


def evaluate(*,out,step,phase_step,macro_reference_loss,per_cell,parent_sha256,
             check_every=250,min_steps=1000,expected_cells=18):
    """Compute and durably record the only authoritative Phase-2 stop gate.

    Exactly 18 task/level cell losses are checked against the macro mean, and
    existing historical checks are recovered *before* applying the new step.
    Never restores/changes weights, optimizers, best.pt, or working.pt.
    """
    out=Path(out)
    step=int(step);phase_step=int(phase_step)
    if not isinstance(per_cell,dict) or len(per_cell)!=expected_cells:
        raise RuntimeError('ROBUST_PLATEAU_UNEXPECTED_TASK_LEVEL_CELL_COUNT')
    vals=[]
    for value in per_cell.values():
        f=float(value)
        if f<0 or not math.isfinite(f):raise RuntimeError('ROBUST_PLATEAU_INVALID_CELL_LOSS')
        vals.append(f)
    raw=float(macro_reference_loss)
    if not math.isfinite(raw) or raw<0:raise RuntimeError('ROBUST_PLATEAU_INVALID_MACRO_LOSS')
    mean=statistics.fmean(vals)
    if not math.isclose(mean,raw,rel_tol=1e-5,abs_tol=1e-7):
        raise RuntimeError('ROBUST_PLATEAU_MACRO_LOSS_MISMATCH')
    if check_every<1 or min_steps<1:raise RuntimeError('ROBUST_PLATEAU_INVALID_SCHEDULE')
    if phase_step%check_every:
        raise RuntimeError('ROBUST_PLATEAU_NOT_AT_CHECK_BOUNDARY')
    path=out/SIDECAR
    legacy=_read_legacy_history(out/'mixed_history.jsonl',step)
    side,_=_read_sidecar(path,step,parent_sha256)
    merged=side.copy()
    for k,row in legacy.items():
        old=merged.get(k)
        if old and not math.isclose(old['macro_reference_loss'],row['macro_reference_loss'],rel_tol=1e-5,abs_tol=1e-7):
            raise RuntimeError('ROBUST_PLATEAU_SIDECAR_HISTORY_MISMATCH')
        merged[k]=row
    current={'step':step,'macro_reference_loss':raw}
    if step in merged:raise RuntimeError('ROBUST_PLATEAU_DUPLICATE_EVALUATION')
    merged[step]=current
    rows=[merged[k] for k in sorted(merged)]
    for a,b in zip(rows,rows[1:]):
        if b['step']-a['step']!=check_every:
            raise RuntimeError('ROBUST_PLATEAU_MISSING_OR_INCONSISTENT_CHECK_INTERVAL')
    result=_recompute(rows)
    result['converged']=bool(result['converged'] and phase_step>=min_steps)
    result.update({'schema':VERSION,'step':step,'phase_step':phase_step,
        'checks_seen':len(rows),'historical_checks_recovered':len(legacy),
        'window':WINDOW,'min_relative_improvement':MIN_RELATIVE_GAIN,
        'stale_required':STALE_CHECKS,'task_mean':mean,
        'task_median':statistics.median(vals),
        'top4_loss_share':sum(sorted(vals)[-4:])/sum(vals) if sum(vals) else 0.,
        'sidecar':str(path)})
    _write_json(path,{'schema':VERSION,'parent_sha256':parent_sha256,
        'checks':rows,'last_decision':result,'best_median':result['best_rolling_median'],
        'stale':result['stale'],'reached':result['converged'],
        'decision_step':step if result['converged'] else None})
    return result
