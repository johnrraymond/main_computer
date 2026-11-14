#!/usr/bin/env python3
"""Gamer v1 per-case numerical error atlas; pure analysis, no model or GPU.

The axes are intentionally distinct:
  * target answer: signed distance of the oracle numerical target from zero;
  * zero-line residual: an optional, separately certified residual field;
  * error: signed prediction minus target, with magnitude and robust spread.
The table-target and residual coordinates are not interchangeable.
"""
from __future__ import annotations

import csv
import json
import math
import os
from pathlib import Path
import tempfile

SCHEMA='nanojev.gamer.v1.numerical-error-atlas.v1'
# Bins use (low, high], apart from zero and the unbounded outer bin.
EDGES=(0.1,1.0,2.0,10.0,100.0,1000.0)
ERROR_EDGES=(1e-6,0.01,0.1,0.5,1.0,2.0,10.0,100.0)


def _fmt(v):
    return f'{v:g}'


def _magnitude(v, *, edges=EDGES):
    a=abs(float(v))
    if a==0.0:
        return 'zero'
    low=0.0
    for high in edges:
        if a<=high:
            return f'({_fmt(low)},{_fmt(high)}]'
        low=high
    return f'>{_fmt(edges[-1])}'


def _signed_bin(v, *, edges=EDGES):
    s='negative' if v<0 else 'positive' if v>0 else 'zero'
    return 'zero' if s=='zero' else s+':'+_magnitude(v,edges=edges)


def _quantile(values,q):
    if not values: return None
    data=sorted(values)
    x=(len(data)-1)*q
    i=int(x)
    if i==len(data)-1:return float(data[-1])
    return float(data[i]+(data[i+1]-data[i])*(x-i))


def _summary(rows):
    if not rows:
        return {'cases':0, 'mae':None,'median_abs_error':None,'p90_abs_error':None,
                'p99_abs_error':None,'max_abs_error':None, 'rmse':None,'signed_bias':None,
                'median_signed_error':None,'median_absolute_deviation':None,
                'normalized_mae_floor_1':None,'correct_sign_rate':None,
                'median_signed_log_error':None,'p90_abs_signed_log_error':None,
                'robust_variance_signed_log_error':None,
                'within_0_1':None,'within_0_5':None,'within_1':None,'within_2':None,
                'within_10':None,'relative_within_10pct_nonzero':None,
                'relative_nonzero_count':0,'zero_target_count':0,
                'zero_target_within_0_1':None}
    n=len(rows)
    errs=[r['error'] for r in rows]
    ab=[r['absolute_error'] for r in rows]
    med=_quantile(errs,.5)
    rel=[r['relative_error_nonzero'] for r in rows if r['relative_error_nonzero'] is not None]
    zero=[r for r in rows if r['target']==0]
    log_errors=[r['signed_log_error'] for r in rows]
    log_med=_quantile(log_errors,.5)
    log_mad=_quantile([abs(v-log_med) for v in log_errors],.5)
    return {
        'cases':n,
        'mae':sum(ab)/n,
        'median_abs_error':_quantile(ab,.5),
        'p90_abs_error':_quantile(ab,.9),
        'p99_abs_error':_quantile(ab,.99),
        'max_abs_error':max(ab),
        'rmse':math.sqrt(sum(v*v for v in errs)/n),
        'signed_bias':sum(errs)/n,
        'median_signed_error':med,
        'median_absolute_deviation':_quantile([abs(e-med) for e in errs],.5),
        'normalized_mae_floor_1':sum(r['normalized_error_floor_1'] for r in rows)/n,
        'median_signed_log_error':log_med,
        'p90_abs_signed_log_error':_quantile([abs(v) for v in log_errors],.9),
        'robust_variance_signed_log_error':(1.4826*log_mad)**2,
        'correct_sign_rate':sum(r['correct_sign'] for r in rows)/n,
        'within_0_1':sum(a<=.1 for a in ab)/n,
        'within_0_5':sum(a<=.5 for a in ab)/n,
        'within_1':sum(a<=1 for a in ab)/n,
        'within_2':sum(a<=2 for a in ab)/n,
        'within_10':sum(a<=10 for a in ab)/n,
        'relative_within_10pct_nonzero':sum(a<=.1 for a in rel)/len(rel) if rel else None,
        'relative_nonzero_count':len(rel),
        'zero_target_count':len(zero),
        'zero_target_within_0_1':sum(r['absolute_error']<=.1 for r in zero)/len(zero) if zero else None,
    }


def _group(rows,field,*, multi=False):
    values={}
    for row in rows:
        keys=row[field] if multi else [row[field]]
        for key in keys:
            values.setdefault(key,[]).append(row)
    return {key:_summary(values[key]) for key in sorted(values)}


def _signed_log(v):
    return math.copysign(math.log1p(abs(v)),v) if v else 0.


def _provenance(row):
    if row.get('bounded_transition_objective'):return 'transition'
    if row.get('bounded_catalog_objective'):return 'catalog'
    return 'native'


def build_error_atlas(cases,predictions,*,split,step,adapter_sha256,dataset_sha256,checkpoint_kind):
    """Construct the complete independently auditable error map for one split."""
    if len(cases)!=len(predictions):
        raise ValueError('GAMER_ATLAS_CASE_PREDICTION_LENGTH_MISMATCH')
    records=[]
    for i,(case,prediction) in enumerate(zip(cases,predictions)):
        actual=float(case['answer']); prediction=float(prediction)
        if not math.isfinite(actual) or not math.isfinite(prediction):
            raise ValueError('GAMER_ATLAS_NONFINITE_NUMERICAL_DATA')
        err=prediction-actual; ab=abs(err)
        residual=case.get('zero_line_residual')
        if residual is not None:
            residual=float(residual)
            if not math.isfinite(residual):raise ValueError('GAMER_ATLAS_NONFINITE_ZERO_LINE_RESIDUAL')
        records.append({
            'split':split, 'case_index':i,
            'case_sha256':case.get('case_sha256',''),
            'game_id':case['game_id'],
            'provenance':_provenance(case),
            'table_families':list(case.get('table_families',[])),
            'target':actual,'prediction':prediction,'error':err,'absolute_error':ab,
            'signed_log_error':_signed_log(prediction)-_signed_log(actual),
            'target_signed_bin':_signed_bin(actual),
            'target_abs_bin':_magnitude(actual),
            'error_bin':_magnitude(err,edges=ERROR_EDGES),
            'target_zone':('zero' if actual==0 else 'near_zero_abs_le_1' if abs(actual)<=1
                           else 'intermediate_1_to_10' if abs(actual)<=10 else 'far_gt_10'),
            'zero_line_residual':residual,
            'residual_signed_bin':_signed_bin(residual) if residual is not None else None,
            'residual_abs_bin':_magnitude(residual) if residual is not None else None,
            'residual_zone':('zero' if residual==0 else 'near_zero_abs_le_1' if abs(residual)<=1
                             else 'intermediate_1_to_10' if abs(residual)<=10 else 'far_gt_10')
                              if residual is not None else None,
            'correct_sign':int((actual==0 and abs(prediction)<=1e-6) or
                               (actual!=0 and prediction*actual>0)),
            'normalized_error_floor_1':ab/max(1.,abs(actual)),
            'relative_error_nonzero':ab/abs(actual) if actual!=0 else None,
        })
    residual_rows=[r for r in records if r['zero_line_residual'] is not None]
    # Expose each signed location, error band, and their intersection.
    intersections={}
    residual_target={}
    game_target={}
    group_target={}
    table_target={}
    for r in records:
        key=(r['target_signed_bin'],r['error_bin'])
        intersections.setdefault(key,[]).append(r)
        game_target.setdefault((r['game_id'],r['target_zone']),[]).append(r)
        group_target.setdefault((r['provenance'],r['target_zone']),[]).append(r)
        for table in r['table_families']:
            table_target.setdefault((table,r['target_zone']),[]).append(r)
        if r['zero_line_residual'] is not None:
            residual_target.setdefault((r['residual_signed_bin'],r['target_abs_bin']),[]).append(r)
    def cross(groups,a,b):
        return [{a:key[0],b:key[1],**_summary(subset)}
                for key,subset in sorted(groups.items())]
    return {
        'schema':SCHEMA,'split':split,'step':int(step),
        'checkpoint_kind':checkpoint_kind, 'adapter_sha256':adapter_sha256,
        'dataset_sha256':dataset_sha256,
        'coordinate_definitions':{
            'target':'Signed oracle answer. Absolute bands: zero, (0,0.1], (0.1,1], (1,2], (2,10], (10,100], (100,1000], >1000.',
            'zero_line_residual':'Independent optional oracle zero_line_residual, NOT assumed equal to numeric target; missing cases excluded from residual groupings.',
            'error':'Prediction minus oracle target; absolute-error bins use (low,high] except exact zero.',
            'relative_error':'abs(pred-target)/abs(target) only for nonzero targets.',
            'normalized_error_floor_1':'abs(pred-target)/max(1,abs(target)) for all targets.',
            'correct_sign':'For target=0, prediction magnitude <=1e-6; otherwise same nonzero sign.',
            'percentiles':'Linear interpolation over ordered absolute errors; p90 and p99 are tails, not exact rates.',
            'signed_log_error':'sign(pred)*log1p(abs(pred)) - sign(target)*log1p(abs(target)); robust variance is square of 1.4826 times its median absolute deviation.',
        },
        'summary':_summary(records),
        'by_target_abs_bin':_group(records,'target_abs_bin'),
        'by_target_signed_bin':_group(records,'target_signed_bin'),
        'by_target_zone':_group(records,'target_zone'),
        'by_error_band':_group(records,'error_bin'),
        'by_provenance':_group(records,'provenance'),
        'by_game':_group(records,'game_id'),
        'by_table_family':_group(records,'table_families',multi=True),
        'zero_line_residual':{
            'available':len(residual_rows), 'missing':len(records)-len(residual_rows),
            'by_abs_bin':_group(residual_rows,'residual_abs_bin'),
            'by_signed_bin':_group(residual_rows,'residual_signed_bin'),
            'by_zone':_group(residual_rows,'residual_zone'),
        },
        'target_bin_by_error_band':cross(intersections,'target_signed_bin','error_band'),
        'residual_bin_by_target_abs_bin':cross(residual_target,'residual_signed_bin','target_abs_bin'),
        'game_by_target_zone':cross(game_target,'game_id','target_zone'),
        'provenance_by_target_zone':cross(group_target,'provenance','target_zone'),
        'table_by_target_zone':cross(table_target,'table_family','target_zone'),
        'worst_cases':[dict(r) for r in sorted(records,key=lambda r:(-r['absolute_error'],r['case_index']))[:20]],
        'worst_zero_line_cases':[dict(r) for r in sorted((r for r in residual_rows if abs(r['zero_line_residual'])<=1.0),
                                                 key=lambda r:(-r['absolute_error'],r['case_index']))[:20]],
        'records':records,
    }


def write_atlas(atlas,path):
    """Atomic write; persist independently of training checkpoint and report."""
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',suffix='.tmp',dir=path.parent,delete=False) as file:
        tmp=Path(file.name)
        json.dump(atlas,file,indent=2,sort_keys=True,allow_nan=False)
        file.write('\n')
    try:os.replace(tmp,path)
    finally:
        if tmp.exists():tmp.unlink()


def export_csv(atlas,path):
    """Full per-location data, one row for each held-out case."""
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fields=['split','case_index','case_sha256','game_id','provenance','table_families',
            'target','prediction','error','absolute_error','signed_log_error','normalized_error_floor_1',
            'relative_error_nonzero','correct_sign','target_zone','target_abs_bin','target_signed_bin',
            'zero_line_residual','residual_zone','residual_abs_bin','residual_signed_bin','error_bin']
    with tempfile.NamedTemporaryFile('w',newline='',encoding='utf-8',suffix='.tmp',dir=path.parent,delete=False) as file:
        tmp=Path(file.name)
        writer=csv.DictWriter(file,fieldnames=fields)
        writer.writeheader()
        for row in atlas['records']:
            out={k:row[k] for k in fields}
            out['table_families']='|'.join(out['table_families'])
            writer.writerow(out)
    try:os.replace(tmp,path)
    finally:
        if tmp.exists():tmp.unlink()


def short_summary(atlas):
    a=atlas['summary']
    return (f'n={a["cases"]} MAE={a["mae"]:.5g} median={a["median_abs_error"]:.5g} '
            f'p90={a["p90_abs_error"]:.5g} p99={a["p99_abs_error"]:.5g} '
            f'bias={a["signed_bias"]:+.5g} logVar={a["robust_variance_signed_log_error"]:.4g} <=1={a["within_1"]:.1%} '
            f'sign={a["correct_sign_rate"]:.1%}')


def _fmt_n(x):
    return 'n/a' if x is None else f'{x:.4g}'


def describe(atlas, *, detail=True):
    """Text summary whose rows are exact disjoint regions of the test space."""
    lines=[f'NUMERICAL ERROR ATLAS — {atlas["split"]}, head step={atlas["step"]}, {atlas["checkpoint_kind"]}',
           'Overall: '+short_summary(atlas),
           'Target signed distance from zero (bins are disjoint):',
           '  band                         n      MAE   median     p90  bias      <=1    sign']
    for band,stat in atlas['by_target_signed_bin'].items():
        lines.append(f'  {band:27} {stat["cases"]:3d} {_fmt_n(stat["mae"]):>8} {_fmt_n(stat["median_abs_error"]):>8} '
                     f'{_fmt_n(stat["p90_abs_error"]):>8} {_fmt_n(stat["signed_bias"]):>8} '
                     f'{stat["within_1"]:>6.1%} {stat["correct_sign_rate"]:>6.1%}')
    res=atlas['zero_line_residual']
    lines.append('Absolute error distribution:')
    for band,stat in atlas['by_error_band'].items():
        lines.append(f'  {band:19} n={stat["cases"]:3d} ({stat["cases"]/atlas["summary"]["cases"]:.1%})')
    lines+= [f'Independent zero-line residual (available={res["available"]}, missing={res["missing"]}):']
    for band,stat in res['by_signed_bin'].items():
        lines.append(f'  {band:27} n={stat["cases"]:3d} MAE={_fmt_n(stat["mae"]):>8} '
                     f'p90={_fmt_n(stat["p90_abs_error"]):>8} sign={stat["correct_sign_rate"]:>6.1%}')
    if detail:
        lines+=['By absolute target-distance band (signs pooled):']
        for band,stat in atlas['by_target_abs_bin'].items():
            lines.append(f'  {band:19} n={stat["cases"]:3d} MAE={_fmt_n(stat["mae"]):>8} '
                         f'p90={_fmt_n(stat["p90_abs_error"]):>8} '
                         f'nMAE={_fmt_n(stat["normalized_mae_floor_1"]):>8}')
        lines+=['By curriculum:',]
        for group,stat in atlas['by_provenance'].items():
            lines.append(f'  {group:12} n={stat["cases"]:3d} MAE={_fmt_n(stat["mae"]):>8} '
                         f'p90={_fmt_n(stat["p90_abs_error"]):>8} sign={stat["correct_sign_rate"]:.1%}')
        lines+=['By table family (overlap is expected):']
        for family,stat in atlas['by_table_family'].items():
            lines.append(f'  {family:22} n={stat["cases"]:3d} MAE={_fmt_n(stat["mae"]):>8} '
                         f'p90={_fmt_n(stat["p90_abs_error"]):>8}')
        lines+=['Worst errors (absolute error):']
        for row in atlas['worst_cases'][:8]:
            lines.append(f'  {row["game_id"]:30} target={row["target"]:>9g} pred={row["prediction"]:>10g} '
                         f'error={row["error"]:+.5g} target_zone={row["target_zone"]}')
    lines.append('Cross-regime maps (residual × target, game × zone, table × zone) and all per-case errors: --json / --atlas-csv.')
    return '\n'.join(lines)
